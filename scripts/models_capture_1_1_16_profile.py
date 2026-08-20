#!/usr/bin/env python3
"""Process-inert profile preparation for the separately authorized 1.1.16 capture.

This file deliberately has no process-launch, network, Git, or account-discovery
authority.  It only turns one closed request into a durable, canonical profile.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import pathlib
import signal
import stat
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

sys.dont_write_bytecode = True

RUNTIME_MAJOR = 3
RUNTIME_MINOR = 9
PROFILE_LIMIT = 16_384
EXPECTED_SOURCE_SHA256 = "095705beb4e4591c8ee7f8b6261473e15228f0f4b1bec58c62c966a6d4bfab30"
EXPECTED_RECOVERY_BINDING_SHA256 = "facf6adc18afc85ed5c232e3e1f9ad0fbcac7d62f1f98866cabb615d43069a57"
EXPECTED_RECOVERY_STDOUT = b"1.1.16\n"
EXPECTED_RECOVERY_RUNNER_SHA256 = "9c1a9d35c0db9fe137ed4490b47d2b11443fe7cfaf1e552eca7adf575b048d4c"
EXPECTED_RECOVERY_RUNNER_BYTES = 46_027
EXPECTED_RECOVERY_SUMMARY_BYTES = 260
EXPECTED_RELEASE_COMMIT = "efa16f096dc02fb654b7e86958d268195284d014"
EXPECTED_DISTRIBUTION_URL = "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.1.16-6607970839166976/darwin-arm/cli_mac_arm64.tar.gz"
EXPECTED_DISTRIBUTION_SHA512 = "fa3a94a7d9d96cb367bf643ecf0da3b4d6b45f3e390ec6db1d699fdac4f7750894617152fc3c1695712a36eee926fff4f00ff4a44d372b3f604cfc9ec6fdbea6"
OUTPUT_NAME = "models.capture.1.1.16.profile.json"
LIFECYCLE_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = getattr(os, "O_DIRECTORY", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
MODULE_AST_SHA256 = "40ec002effaa05982a389493c0cd4493e61501be269fd8f79baf0c39b545e4b4"
ACTIVE_PROFILE_PATH: Optional[str] = None
ACTIVE_PROFILE_IDENTITY: Optional[FileIdentity] = None
ACTIVE_PROFILE_DIGEST: Optional[str] = None

REQUEST_KEYS = frozenset({"account_home", "capture_parent", "output_path", "snapshot_path", "source_path", "version_root"})
VALIDATE_KEYS = frozenset({"profile_path"})
IDENTITY_KEYS = frozenset({"ctime_ns", "dev", "gid", "ino", "mode", "mtime_ns", "nlink", "size", "uid"})
DIR_KEYS = frozenset({"dev", "gid", "ino", "mode", "nlink", "uid"})
PROFILE_KEYS = frozenset({"account_home", "account_home_identity", "capture_parent", "capture_parent_identity", "snapshot_identity", "snapshot_path", "source_identity", "source_path", "source_sha256", "version_binding_sha256", "version_root", "version_root_identity"})
RECOVERY_FILES = frozenset({"agy.snapshot", "cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state", "runner.py", "runner.py.sha256", "snapshot.post.json", "snapshot.pre.json", "source.post.json", "source.pre.json", "version.binding.json", "version.binding.sha256", "version.stderr", "version.stdout", "version.summary.json"})
RECOVERY_SCRATCH = ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state")


class ProfileError(ValueError):
    pass


class Interrupted(SystemExit):
    def __init__(self, signum: int):
        super().__init__(128 + signum)
        self.signum = signum


def _runtime_supported() -> bool:
    return (sys.implementation.name == "cpython" and sys.version_info[:2] == (RUNTIME_MAJOR, RUNTIME_MINOR)
            and sys.flags.isolated == 1 and sys.flags.no_site == 1 and sys.flags.dont_write_bytecode == 1 and sys.flags.ignore_environment == 1)


class Signals:
    def __init__(self, owned: Sequence[signal.Signals]):
        self.owned, self.seen, self.selected = tuple(owned), set(), None
    def latch(self, signum: int, _frame: object = None) -> None:
        if signum in self.owned:
            self.seen.add(signum)
    def poll(self) -> None:
        if self.selected is None:
            self.selected = next((item for item in self.owned if item in self.seen), None)
        if self.selected is not None:
            raise Interrupted(self.selected)


@dataclass
class Lifecycle:
    signals: Signals
    mask: set[signal.Signals]
    handlers: dict[signal.Signals, object]


def _acquire() -> Lifecycle:
    if not all(hasattr(signal, item) for item in ("pthread_sigmask", "sigpending", "sigwait")):
        raise ProfileError("required signal primitives are unavailable")
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
    handlers = {item: signal.getsignal(item) for item in LIFECYCLE_SIGNALS}
    owned = tuple(item for item in LIFECYCLE_SIGNALS if item not in mask and handlers[item] is not signal.SIG_IGN)
    state = Lifecycle(Signals(owned), mask, handlers)
    try:
        for item in owned:
            signal.signal(item, state.signals.latch)
        pending = set(signal.sigpending()).intersection(owned)
        for item in owned:
            if item in pending:
                state.signals.latch(signal.sigwait({item}))
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        state.signals.poll()
        return state
    except BaseException:
        for item in owned:
            signal.signal(item, handlers[item])
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        raise


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _json(data: bytes) -> object:
    if len(data) > PROFILE_LIMIT or not data:
        raise ProfileError("JSON input is invalid")
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ProfileError("JSON input has duplicate keys")
            result[key] = value
        return result
    try:
        value = json.loads(data.decode("utf-8", "strict"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError("JSON input is invalid") from exc
    return value


def _absolute(value: object) -> str:
    if not isinstance(value, str) or not value or not os.path.isabs(value) or os.path.normpath(value) != value or os.path.realpath(value) != value:
        raise ProfileError("path is not canonical and absolute")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        raise ProfileError("digest is invalid")
    return value


@dataclass(frozen=True)
class FileIdentity:
    ctime_ns: int
    dev: int
    gid: int
    ino: int
    mode: int
    mtime_ns: int
    nlink: int
    size: int
    uid: int
    @classmethod
    def from_stat(cls, value: os.stat_result) -> "FileIdentity":
        return cls(value.st_ctime_ns, value.st_dev, value.st_gid, value.st_ino, stat.S_IMODE(value.st_mode), value.st_mtime_ns, value.st_nlink, value.st_size, value.st_uid)
    @classmethod
    def from_mapping(cls, value: object) -> "FileIdentity":
        if not isinstance(value, dict) or set(value) != IDENTITY_KEYS or any(type(item) is not int or item < 0 for item in value.values()):
            raise ProfileError("file identity is invalid")
        return cls(**value)


@dataclass(frozen=True)
class DirectoryIdentity:
    dev: int
    gid: int
    ino: int
    mode: int
    nlink: int
    uid: int
    @classmethod
    def from_stat(cls, value: os.stat_result) -> "DirectoryIdentity":
        return cls(value.st_dev, value.st_gid, value.st_ino, stat.S_IMODE(value.st_mode), value.st_nlink, value.st_uid)
    @classmethod
    def from_mapping(cls, value: object) -> "DirectoryIdentity":
        if not isinstance(value, dict) or set(value) != DIR_KEYS or any(type(item) is not int or item < 0 for item in value.values()):
            raise ProfileError("directory identity is invalid")
        return cls(**value)


@dataclass(frozen=True)
class CaptureProfile:
    account_home: str
    account_home_identity: DirectoryIdentity
    capture_parent: str
    capture_parent_identity: DirectoryIdentity
    snapshot_identity: FileIdentity
    snapshot_path: str
    source_identity: FileIdentity
    source_path: str
    source_sha256: str
    version_binding_sha256: str
    version_root: str
    version_root_identity: DirectoryIdentity
    @classmethod
    def from_bytes(cls, data: bytes) -> "CaptureProfile":
        value = _json(data)
        if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
            raise ProfileError("capture profile is invalid")
        for key in ("account_home", "capture_parent", "snapshot_path", "source_path", "version_root"):
            _absolute(value[key])
        profile = cls(value["account_home"], DirectoryIdentity.from_mapping(value["account_home_identity"]), value["capture_parent"], DirectoryIdentity.from_mapping(value["capture_parent_identity"]), FileIdentity.from_mapping(value["snapshot_identity"]), value["snapshot_path"], FileIdentity.from_mapping(value["source_identity"]), value["source_path"], _sha(value["source_sha256"]), _sha(value["version_binding_sha256"]), value["version_root"], DirectoryIdentity.from_mapping(value["version_root_identity"]))
        if _canonical(dataclasses.asdict(profile)) != data:
            raise ProfileError("capture profile is not canonical")
        return profile


def _open_directory(path: str, private: bool) -> tuple[int, DirectoryIdentity]:
    _absolute(path)
    fd = os.open("/", os.O_RDONLY | DIRECTORY | CLOEXEC)
    try:
        for item in pathlib.PurePosixPath(path).parts[1:]:
            next_fd = os.open(item, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        observed = os.fstat(fd)
        identity = DirectoryIdentity.from_stat(observed)
        if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid() or (private and (identity.mode != 0o700 or identity.nlink < 1)):
            raise ProfileError("directory authority changed")
        return fd, identity
    except BaseException:
        os.close(fd)
        raise


def _open_file(path: str, expected_mode: int) -> tuple[int, FileIdentity]:
    parent, leaf = os.path.split(_absolute(path))
    parent_fd, _ = _open_directory(parent, False)
    try:
        fd = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    raw = os.fstat(fd); identity = FileIdentity.from_stat(raw)
    if not stat.S_ISREG(raw.st_mode) or identity.uid != os.getuid() or identity.mode != expected_mode or identity.nlink != 1:
        os.close(fd); raise ProfileError("file authority changed")
    return fd, identity


def _hash(fd: int, size: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET); digest = hashlib.sha256(); remain = size
    while remain:
        block = os.read(fd, min(remain, 1024 * 1024))
        if not block:
            raise ProfileError("file ended early")
        digest.update(block); remain -= len(block)
    if os.read(fd, 1):
        raise ProfileError("file is oversized")
    return digest.hexdigest()


def _read_at(parent: int, name: str, limit: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
    try:
        item = os.fstat(fd)
        if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1 or item.st_size > limit:
            raise ProfileError("recovery evidence changed")
        data = os.read(fd, limit + 1)
        if len(data) != item.st_size or len(data) > limit:
            raise ProfileError("recovery evidence changed")
        return data
    finally:
        os.close(fd)


def _validate_recovery(root: str, source_path: str, source: FileIdentity, snapshot: FileIdentity) -> None:
    fd, identity = _open_directory(root, True)
    try:
        if set(os.listdir(fd)) != RECOVERY_FILES:
            raise ProfileError("recovery evidence shape changed")
        for name in RECOVERY_SCRATCH:
            scratch = os.open(name, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=fd)
            try:
                observed = os.fstat(scratch)
                if (not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid()
                        or stat.S_IMODE(observed.st_mode) != 0o700 or observed.st_nlink != 2
                        or os.listdir(scratch)):
                    raise ProfileError("recovery scratch changed")
            finally:
                os.close(scratch)
        runner = _read_at(fd, "runner.py", EXPECTED_RECOVERY_RUNNER_BYTES + 1)
        if (len(runner) != EXPECTED_RECOVERY_RUNNER_BYTES
                or hashlib.sha256(runner).hexdigest() != EXPECTED_RECOVERY_RUNNER_SHA256
                or _read_at(fd, "runner.py.sha256", 128) != (EXPECTED_RECOVERY_RUNNER_SHA256 + "\n").encode("ascii")):
            raise ProfileError("recovery runner changed")
        binding_bytes = _read_at(fd, "version.binding.json", 3_205)
        if hashlib.sha256(binding_bytes).hexdigest() != EXPECTED_RECOVERY_BINDING_SHA256 or _read_at(fd, "version.binding.sha256", 128) != (EXPECTED_RECOVERY_BINDING_SHA256 + "\n").encode("ascii"):
            raise ProfileError("recovery binding changed")
        if len(binding_bytes) != 3_205:
            raise ProfileError("recovery binding changed")
        binding = _json(binding_bytes)
        if not isinstance(binding, dict):
            raise ProfileError("recovery binding changed")
        source_value, snapshot_value, version_value = binding.get("source"), binding.get("snapshot"), binding.get("version")
        official = binding.get("official_observation")
        limitations = binding.get("limitations")
        if (binding.get("claim") != "snapshot-version-only" or not isinstance(source_value, dict) or not isinstance(snapshot_value, dict) or not isinstance(version_value, dict)
                or source_value.get("pre") != dataclasses.asdict(source) or source_value.get("post") != dataclasses.asdict(source) or source_value.get("sha256") != EXPECTED_SOURCE_SHA256
                or snapshot_value.get("pre") != dataclasses.asdict(snapshot) or snapshot_value.get("post") != dataclasses.asdict(snapshot) or snapshot_value.get("sha256") != EXPECTED_SOURCE_SHA256
                or version_value.get("logical_argv") != [source_path, "--version"] or version_value.get("expected") != "1.1.16" or version_value.get("observed") != "1.1.16" or version_value.get("exit") != 0 or version_value.get("popen_count") != 1
                or official != {"distribution_sha512": EXPECTED_DISTRIBUTION_SHA512, "distribution_url": EXPECTED_DISTRIBUTION_URL, "release_commit": EXPECTED_RELEASE_COMMIT, "version": "1.1.16"}
                or limitations != {"account_read": False, "metadata_advance_authorized": False, "models_called": False, "network_absence_os_enforced": False, "provider_backend_proven": False, "routing_authority": False}
                or binding.get("inventory") != {"executable_version_bound": False}
                or _read_at(fd, "version.stdout", 7) != EXPECTED_RECOVERY_STDOUT or _read_at(fd, "version.stderr", 0) != b""):
            raise ProfileError("recovery binding claim changed")
        if (_read_at(fd, "source.pre.json", 1_024) != _canonical(dataclasses.asdict(source))
                or _read_at(fd, "source.post.json", 1_024) != _canonical(dataclasses.asdict(source))
                or _read_at(fd, "snapshot.pre.json", 1_024) != _canonical(dataclasses.asdict(snapshot))
                or _read_at(fd, "snapshot.post.json", 1_024) != _canonical(dataclasses.asdict(snapshot))):
            raise ProfileError("recovery identity evidence changed")
        summary = _read_at(fd, "version.summary.json", EXPECTED_RECOVERY_SUMMARY_BYTES)
        artifacts = binding.get("artifacts")
        if (len(summary) != EXPECTED_RECOVERY_SUMMARY_BYTES or not isinstance(_json(summary), dict)
                or not isinstance(artifacts, dict)
                or artifacts.get("runner.py") != EXPECTED_RECOVERY_RUNNER_SHA256
                or artifacts.get("version.summary.json") != hashlib.sha256(summary).hexdigest()
                or artifacts.get("version.stdout") != hashlib.sha256(EXPECTED_RECOVERY_STDOUT).hexdigest()
                or artifacts.get("version.stderr") != hashlib.sha256(b"").hexdigest()):
            raise ProfileError("recovery summary changed")
        path_identity = DirectoryIdentity.from_stat(os.stat(root, follow_symlinks=False))
        if path_identity != identity or DirectoryIdentity.from_stat(os.fstat(fd)) != identity:
            raise ProfileError("recovery root identity changed")
    finally:
        os.close(fd)


def _disjoint(first: str, second: str) -> bool:
    return os.path.commonpath((first, second)) not in (first, second)


def _from_request(value: object) -> tuple[CaptureProfile, str]:
    if not isinstance(value, dict) or set(value) != REQUEST_KEYS:
        raise ProfileError("prepare request is invalid")
    account, capture_parent, output, snapshot, source, version_root = (_absolute(value[key]) for key in ("account_home", "capture_parent", "output_path", "snapshot_path", "source_path", "version_root"))
    if (os.path.basename(output) != OUTPUT_NAME or os.path.dirname(output) != capture_parent
            or os.path.basename(source) != "agy.source" or os.path.dirname(source) != capture_parent
            or os.path.basename(snapshot) != "agy.snapshot" or os.path.dirname(snapshot) != version_root
            or os.path.commonpath((version_root, capture_parent)) != capture_parent
            or not _disjoint(account, capture_parent)):
        raise ProfileError("capture topology is invalid")
    account_fd, account_identity = _open_directory(account, True); os.close(account_fd)
    capture_fd, capture_identity = _open_directory(capture_parent, True); os.close(capture_fd)
    version_fd, version_identity = _open_directory(version_root, True); os.close(version_fd)
    source_fd, source_identity = _open_file(source, 0o755)
    snapshot_fd, snapshot_identity = _open_file(snapshot, 0o500)
    try:
        if _hash(source_fd, source_identity.size) != EXPECTED_SOURCE_SHA256 or _hash(snapshot_fd, snapshot_identity.size) != EXPECTED_SOURCE_SHA256:
            raise ProfileError("reviewed executable hash changed")
        _validate_recovery(version_root, source, source_identity, snapshot_identity)
        if FileIdentity.from_stat(os.fstat(source_fd)) != source_identity or FileIdentity.from_stat(os.fstat(snapshot_fd)) != snapshot_identity:
            raise ProfileError("reviewed executable identity changed")
    finally:
        os.close(source_fd); os.close(snapshot_fd)
    return CaptureProfile(account, account_identity, capture_parent, capture_identity, snapshot_identity, snapshot, source_identity, source, EXPECTED_SOURCE_SHA256, EXPECTED_RECOVERY_BINDING_SHA256, version_root, version_identity), output


def _same_capture_parent(observed: DirectoryIdentity, expected: DirectoryIdentity) -> bool:
    """Bind stable parent identity; nlink is diagnostic, not parent authority."""
    return (observed.dev == expected.dev and observed.gid == expected.gid
            and observed.ino == expected.ino and observed.mode == expected.mode
            and observed.uid == expected.uid and observed.nlink >= 1 and expected.nlink >= 1)


def _publish(path: str, data: bytes, signals: Optional[Signals]) -> str:
    global ACTIVE_PROFILE_PATH, ACTIVE_PROFILE_IDENTITY, ACTIVE_PROFILE_DIGEST
    parent_path, name = os.path.split(path); parent, _ = _open_directory(parent_path, True)
    temporary = ".models.capture.profile." + os.urandom(16).hex()
    temporary_identity: Optional[FileIdentity] = None
    final_identity: Optional[FileIdentity] = None
    try:
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False); raise ProfileError("capture profile exists")
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | CLOEXEC | NOFOLLOW, 0o600, dir_fd=parent)
        try:
            temporary_identity = FileIdentity.from_stat(os.fstat(fd))
            if signals: signals.poll()
            pending = memoryview(data)
            while pending:
                if signals: signals.poll()
                count = os.write(fd, pending)
                if count <= 0: raise ProfileError("profile publication write failed")
                pending = pending[count:]
            os.fsync(fd)
            item = os.fstat(fd)
            if stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1 or item.st_size != len(data):
                raise ProfileError("profile publication changed")
        finally:
            os.close(fd)
        if signals: signals.poll()
        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        final = os.stat(name, dir_fd=parent, follow_symlinks=False); final_identity = FileIdentity.from_stat(final)
        if final_identity.dev != temporary_identity.dev or final_identity.ino != temporary_identity.ino or final.st_nlink != 2 or stat.S_IMODE(final.st_mode) != 0o600:
            raise ProfileError("profile publication changed")
        temporary_after_link = FileIdentity.from_stat(os.stat(temporary, dir_fd=parent, follow_symlinks=False))
        if temporary_after_link != final_identity:
            raise ProfileError("profile publication changed")
        temporary_identity = temporary_after_link
        os.unlink(temporary, dir_fd=parent)
        final = os.stat(name, dir_fd=parent, follow_symlinks=False); final_identity = FileIdentity.from_stat(final)
        if final_identity.dev != temporary_identity.dev or final_identity.ino != temporary_identity.ino or final.st_nlink != 1:
            raise ProfileError("profile publication changed")
        if signals: signals.poll()
        os.fsync(parent)
        if signals: signals.poll()
        os.fsync(parent)
        final_fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
        try:
            if FileIdentity.from_stat(os.fstat(final_fd)) != final_identity or os.read(final_fd, len(data) + 1) != data:
                raise ProfileError("profile publication changed")
        finally:
            os.close(final_fd)
        digest = hashlib.sha256(data).hexdigest()
        ACTIVE_PROFILE_PATH, ACTIVE_PROFILE_IDENTITY, ACTIVE_PROFILE_DIGEST = path, final_identity, digest
        return digest
    except BaseException:
        if final_identity is not None and temporary_identity is not None:
            try:
                final_now = FileIdentity.from_stat(os.stat(name, dir_fd=parent, follow_symlinks=False))
                temporary_now = FileIdentity.from_stat(os.stat(temporary, dir_fd=parent, follow_symlinks=False))
                if final_now == final_identity and temporary_now == temporary_identity and final_now.nlink == 2 and temporary_now.nlink == 2:
                    os.unlink(name, dir_fd=parent)
                    derived_temporary = FileIdentity.from_stat(os.stat(temporary, dir_fd=parent, follow_symlinks=False))
                    if (derived_temporary.dev == temporary_identity.dev and derived_temporary.ino == temporary_identity.ino
                            and derived_temporary.uid == temporary_identity.uid and derived_temporary.nlink == 1):
                        if FileIdentity.from_stat(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) == derived_temporary:
                            os.unlink(temporary, dir_fd=parent)
                    temporary_identity = None
            except FileNotFoundError:
                pass
        for leaf, expected in ((name, final_identity), (temporary, temporary_identity)):
            try:
                item = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                observed = FileIdentity.from_stat(item)
                if expected is not None and observed == expected and stat.S_ISREG(item.st_mode): os.unlink(leaf, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.fsync(parent); raise
    finally:
        os.close(parent)


def prepare(data: bytes, signals: Optional[Signals] = None) -> dict[str, str]:
    profile, output = _from_request(_json(data)); raw = _canonical(dataclasses.asdict(profile)); CaptureProfile.from_bytes(raw)
    return {"profile_sha256": _publish(output, raw, signals), "status": "prepared"}


def validate(data: bytes) -> dict[str, str]:
    value = _json(data)
    if not isinstance(value, dict) or set(value) != VALIDATE_KEYS: raise ProfileError("validate request is invalid")
    path = _absolute(value["profile_path"]); parent, leaf = os.path.split(path)
    if leaf != OUTPUT_NAME: raise ProfileError("capture profile basename is fixed")
    parent_fd, parent_identity = _open_directory(parent, True)
    try:
        fd = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
        try:
            item = os.fstat(fd); profile_identity = FileIdentity.from_stat(item)
            if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1 or item.st_size > PROFILE_LIMIT:
                raise ProfileError("capture profile changed")
            raw = os.read(fd, PROFILE_LIMIT + 1)
            if len(raw) != item.st_size or len(raw) > PROFILE_LIMIT:
                raise ProfileError("capture profile changed")
        finally: os.close(fd)
    finally: os.close(parent_fd)
    profile = CaptureProfile.from_bytes(raw)
    current, output = _from_request({"account_home": profile.account_home, "capture_parent": profile.capture_parent, "output_path": path, "snapshot_path": profile.snapshot_path, "source_path": profile.source_path, "version_root": profile.version_root})
    if (output != path
            or not _same_capture_parent(current.capture_parent_identity, profile.capture_parent_identity)
            or dataclasses.replace(current, capture_parent_identity=profile.capture_parent_identity) != profile):
        raise ProfileError("capture profile changed")
    parent_check_fd, parent_check = _open_directory(parent, True)
    try:
        check_fd = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_check_fd)
        try:
            if (not _same_capture_parent(parent_check, parent_identity)
                    or FileIdentity.from_stat(os.fstat(check_fd)) != profile_identity
                    or os.read(check_fd, len(raw) + 1) != raw):
                raise ProfileError("capture profile changed")
        finally:
            os.close(check_fd)
    finally:
        os.close(parent_check_fd)
    return {"profile_sha256": hashlib.sha256(raw).hexdigest(), "status": "valid"}


def validate_source_contract(data: bytes) -> dict[str, str]:
    try: tree = ast.parse(data.decode("utf-8", "strict"))
    except (UnicodeDecodeError, SyntaxError) as exc: raise ProfileError("profile source invalid") from exc
    pin = next((node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "MODULE_AST_SHA256"), None)
    if pin is None: raise ProfileError("profile source pin is missing")
    pin.value = ast.Constant(value="PINNED-MODULE-AST")
    if hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest() != MODULE_AST_SHA256: raise ProfileError("profile source structure changed")
    imports = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
    if imports & {"subprocess", "socket", "urllib", "http", "importlib"}: raise ProfileError("profile source gained process or network authority")
    allowed_from = {("__future__", "annotations"), ("dataclasses", "dataclass"), ("typing", "Optional"), ("typing", "Sequence")}
    if any((node.module, alias.name) not in allowed_from for node in tree.body if isinstance(node, ast.ImportFrom) for alias in node.names):
        raise ProfileError("profile source gained imported authority")
    forbidden_direct = {"eval", "exec", "compile", "setattr", "delattr", "globals", "locals", "vars", "__import__"}
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_direct for node in ast.walk(tree)):
        raise ProfileError("profile source gained dynamic authority")
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"scandir", "Popen", "system", "popen"} for node in ast.walk(tree)):
        raise ProfileError("profile source gained discovery authority")
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    if not {"_from_request", "_open_directory", "prepare", "validate", "_finish_success"} <= set(functions):
        raise ProfileError("profile builder surface changed")
    reachable: set[str] = set(); pending = ["_from_request"]
    while pending:
        name = pending.pop()
        if name in reachable: continue
        reachable.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
                pending.append(node.func.id)
    if reachable != {"_absolute", "_canonical", "_disjoint", "_from_request", "_hash", "_json", "_open_directory", "_open_file", "_read_at", "_validate_recovery"}:
        raise ProfileError("profile authority call graph changed")
    def symbol(node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute):
            parent = symbol(node.value)
            return parent + "." + node.attr if parent is not None else None
        return None
    request_calls = {(symbol(node.func), node.lineno) for node in ast.walk(functions["_from_request"]) if isinstance(node, ast.Call)}
    allowed_request_calls = {
        ("isinstance", 334), ("set", 334), ("ProfileError", 335), ("_absolute", 336),
        ("os.path.basename", 337), ("os.path.dirname", 337), ("os.path.basename", 338), ("os.path.dirname", 338),
        ("os.path.basename", 339), ("os.path.dirname", 339), ("os.path.commonpath", 340), ("_disjoint", 341),
        ("ProfileError", 342), ("_open_directory", 343), ("os.close", 343),
        ("_open_directory", 344), ("os.close", 344), ("_open_directory", 345), ("os.close", 345),
        ("_open_file", 346), ("_open_file", 347), ("_hash", 349), ("_hash", 349),
        ("ProfileError", 350), ("_validate_recovery", 351), ("FileIdentity.from_stat", 352),
        ("os.fstat", 352), ("FileIdentity.from_stat", 352), ("os.fstat", 352),
        ("ProfileError", 353), ("os.close", 355), ("os.close", 355), ("CaptureProfile", 356),
    }
    if request_calls != allowed_request_calls:
        raise ProfileError("profile account authority changed")
    account_source = ast.get_source_segment(data.decode("utf-8", "strict"), functions["_open_directory"]) or ""
    if "listdir" in account_source or "scandir" in account_source or "walk" in account_source or "read(" in account_source:
        raise ProfileError("profile source enumerates account HOME")
    return {"status": "valid-source"}


def _held_source() -> bytes:
    path = _absolute(os.path.realpath(__file__))
    parent, name = os.path.split(path); parent_fd, _ = _open_directory(parent, False)
    try:
        fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        item = os.fstat(fd)
        if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) & 0o022 or item.st_size <= 0 or item.st_size > 128 * 1024:
            raise ProfileError("profile source authority changed")
        data = os.read(fd, item.st_size + 1)
        if len(data) != item.st_size or os.fstat(fd).st_ino != item.st_ino:
            raise ProfileError("profile source authority changed")
    finally:
        os.close(fd)
    validate_source_contract(data)
    return data


def _read_stdin(limit: int = PROFILE_LIMIT, signals: Optional[Signals] = None) -> bytes:
    chunks = bytearray()
    while len(chunks) <= limit:
        if signals: signals.poll()
        block = os.read(sys.stdin.buffer.fileno(), min(4096, limit + 1 - len(chunks)))
        if not block:
            return bytes(chunks)
        chunks.extend(block)
    raise ProfileError("stdin exceeds bound")


def _rollback_active_profile() -> None:
    if ACTIVE_PROFILE_PATH is None or ACTIVE_PROFILE_IDENTITY is None or ACTIVE_PROFILE_DIGEST is None:
        return
    parent, leaf = os.path.split(ACTIVE_PROFILE_PATH)
    try:
        fd, _ = _open_directory(parent, True)
    except (ProfileError, OSError):
        return
    try:
        try:
            held = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=fd)
            try:
                item = os.fstat(held); current = FileIdentity.from_stat(item)
                path_identity = FileIdentity.from_stat(os.stat(leaf, dir_fd=fd, follow_symlinks=False))
                data = os.read(held, item.st_size + 1)
                if (stat.S_ISREG(item.st_mode) and current == ACTIVE_PROFILE_IDENTITY and path_identity == current
                        and len(data) == item.st_size and hashlib.sha256(data).hexdigest() == ACTIVE_PROFILE_DIGEST):
                    os.unlink(leaf, dir_fd=fd); os.fsync(fd)
            finally:
                os.close(held)
        except FileNotFoundError:
            pass
    finally:
        os.close(fd)


def _finish_success(state: Lifecycle, result: dict[str, str]) -> None:
    try:
        payload = _canonical(result)
        if sys.stdout.buffer.write(payload) != len(payload): raise OSError("completion output write failed")
        sys.stdout.buffer.flush()
        signal.pthread_sigmask(signal.SIG_BLOCK, state.signals.owned)
        pending = set(signal.sigpending()).intersection(state.signals.owned)
        for item in state.signals.owned:
            if item in pending:
                state.signals.latch(signal.sigwait({item}))
        state.signals.poll()
    except Interrupted as exc:
        try:
            _rollback_active_profile()
        except BaseException:
            pass
        os._exit(exc.code)
    except BaseException:
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, state.signals.owned)
        except BaseException:
            pass
        try:
            pending = set(signal.sigpending()).intersection(state.signals.owned)
            for item in state.signals.owned:
                if item in pending: state.signals.latch(signal.sigwait({item}))
        except BaseException:
            pass
        try:
            _rollback_active_profile()
        except BaseException:
            pass
        try:
            pending = set(signal.sigpending()).intersection(state.signals.owned)
            for item in state.signals.owned:
                if item in pending: state.signals.latch(signal.sigwait({item}))
        except BaseException:
            pass
        try:
            state.signals.poll()
        except Interrupted as exc:
            os._exit(exc.code)
        os._exit(1)
    os._exit(0)


def main(argv: Sequence[str]) -> int:
    if not _runtime_supported():
        return 64
    if len(argv) != 1 or argv[0] not in ("--prepare", "--validate", "--validate-source-contract"):
        return 64
    state = _acquire()
    try:
        _held_source()
        raw = _read_stdin(128 * 1024 if argv[0] == "--validate-source-contract" else PROFILE_LIMIT, state.signals); result = validate_source_contract(raw) if argv[0] == "--validate-source-contract" else (prepare(raw, state.signals) if argv[0] == "--prepare" else validate(raw))
        _finish_success(state, result)
    except Interrupted as exc:
        return exc.code
    except (OSError, ProfileError, ValueError):
        return 1


if __name__ == "__main__":
    if not _runtime_supported():
        os._exit(64)
    os._exit(main(sys.argv[1:]))
