#!/usr/bin/env python3
"""One separately-authorized, capture-only ``agy models`` observation for 1.1.12.

The runner never interprets the captured stream.  A successful observation is
``captured`` evidence, not an accepted inventory, metadata update, route, or model
selection.  The authorized CLI may mutate its explicitly supplied account HOME;
this runner neither enumerates that HOME nor claims to undo those mutations.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import pathlib
import selectors
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional, Sequence

sys.dont_write_bytecode = True

RUNTIME_MAJOR = 3
RUNTIME_MINOR = 9
PROFILE_LIMIT = 16_384
STREAM_LIMIT = 64 * 1024
WALL_SECONDS = 25.0
EXPECTED_SOURCE_SHA256 = "c8fd3c0016e101689f923f82da1c068b0e6dce3abcb0089e282742693ad4d344"
EXPECTED_RECOVERY_BINDING_SHA256 = "30a81274557a55ed53109f140f9cf479ea3a1cfd09e6dcb3cb508abf3c50d22f"
EXPECTED_RECOVERY_STDOUT = b"1.1.12\n"
EXPECTED_RECOVERY_RUNNER_SHA256 = "d051c15536cca109101cfd101370038faa99274f1e44816e5551cee7a87da6e1"
EXPECTED_RECOVERY_RUNNER_BYTES = 96_663
EXPECTED_RECOVERY_SUMMARY_BYTES = 263
OUTPUT_PROFILE_NAME = "models.capture.1.1.12.profile.json"
SCRATCH_NAMES = ("cwd", "tmp", "xdg-cache", "xdg-config", "xdg-state")
RECOVERY_SCRATCH = ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state")
RECOVERY_FILES = frozenset(set(RECOVERY_SCRATCH) | {"runner.py", "runner.py.sha256", "version.binding.json", "version.binding.sha256", "version.stderr", "version.stdout", "version.summary.json"})
PROFILE_KEYS = frozenset({"account_home", "account_home_identity", "capture_parent", "capture_parent_identity", "snapshot_identity", "snapshot_path", "source_identity", "source_path", "source_sha256", "version_binding_sha256", "version_root", "version_root_identity"})
FILE_KEYS = frozenset({"ctime_ns", "dev", "gid", "ino", "mode", "mtime_ns", "nlink", "size", "uid"})
DIR_KEYS = frozenset({"dev", "gid", "ino", "mode", "nlink", "uid"})
LIFECYCLE_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = getattr(os, "O_DIRECTORY", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
MODULE_AST_SHA256 = "b6ca33f445ac81c0e4a6086a916b3558ba654d526ec0b87a5603b7930d883a54"
ACTIVE_MARKER_ROOT: Optional[str] = None
ACTIVE_MARKER_ROOT_IDENTITY: Optional[tuple[int, int]] = None
ACTIVE_MARKER_DIGEST: Optional[str] = None
ACTIVE_MARKER_IDENTITY: Optional[FileIdentity] = None


class CaptureError(ValueError):
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
        if signum in self.owned: self.seen.add(signum)
    def poll(self) -> None:
        if self.selected is None: self.selected = next((item for item in self.owned if item in self.seen), None)
        if self.selected is not None: raise Interrupted(self.selected)


@dataclass
class Lifecycle:
    signals: Signals
    mask: set[signal.Signals]
    handlers: dict[signal.Signals, object]


def _acquire() -> Lifecycle:
    if not all(hasattr(signal, item) for item in ("pthread_sigmask", "sigpending", "sigwait")): raise CaptureError("required signal primitives are unavailable")
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
    handlers = {item: signal.getsignal(item) for item in LIFECYCLE_SIGNALS}
    owned = tuple(item for item in LIFECYCLE_SIGNALS if item not in mask and handlers[item] is not signal.SIG_IGN)
    result = Lifecycle(Signals(owned), mask, handlers)
    try:
        for item in owned: signal.signal(item, result.signals.latch)
        pending = set(signal.sigpending()).intersection(owned)
        for item in owned:
            if item in pending: result.signals.latch(signal.sigwait({item}))
        signal.pthread_sigmask(signal.SIG_SETMASK, mask); result.signals.poll(); return result
    except BaseException:
        for item in owned: signal.signal(item, handlers[item])
        signal.pthread_sigmask(signal.SIG_SETMASK, mask); raise


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _json(data: bytes) -> object:
    if not data or len(data) > PROFILE_LIMIT: raise CaptureError("profile is invalid")
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result: raise CaptureError("profile has duplicate keys")
            result[key] = value
        return result
    try: return json.loads(data.decode("utf-8", "strict"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise CaptureError("profile is invalid") from exc


def _absolute(value: object) -> str:
    if not isinstance(value, str) or not value or not os.path.isabs(value) or os.path.normpath(value) != value or os.path.realpath(value) != value: raise CaptureError("path is invalid")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(item not in "0123456789abcdef" for item in value): raise CaptureError("digest is invalid")
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
    def from_stat(cls, value: os.stat_result) -> "FileIdentity": return cls(value.st_ctime_ns, value.st_dev, value.st_gid, value.st_ino, stat.S_IMODE(value.st_mode), value.st_mtime_ns, value.st_nlink, value.st_size, value.st_uid)
    @classmethod
    def from_mapping(cls, value: object) -> "FileIdentity":
        if not isinstance(value, dict) or set(value) != FILE_KEYS or any(type(item) is not int or item < 0 for item in value.values()): raise CaptureError("file identity is invalid")
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
    def from_stat(cls, value: os.stat_result) -> "DirectoryIdentity": return cls(value.st_dev, value.st_gid, value.st_ino, stat.S_IMODE(value.st_mode), value.st_nlink, value.st_uid)
    @classmethod
    def from_mapping(cls, value: object) -> "DirectoryIdentity":
        if not isinstance(value, dict) or set(value) != DIR_KEYS or any(type(item) is not int or item < 0 for item in value.values()): raise CaptureError("directory identity is invalid")
        return cls(**value)


@dataclass(frozen=True)
class Profile:
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
    def from_bytes(cls, data: bytes) -> "Profile":
        value = _json(data)
        if not isinstance(value, dict) or set(value) != PROFILE_KEYS: raise CaptureError("profile shape is invalid")
        for key in ("account_home", "capture_parent", "snapshot_path", "source_path", "version_root"): _absolute(value[key])
        profile = cls(value["account_home"], DirectoryIdentity.from_mapping(value["account_home_identity"]), value["capture_parent"], DirectoryIdentity.from_mapping(value["capture_parent_identity"]), FileIdentity.from_mapping(value["snapshot_identity"]), value["snapshot_path"], FileIdentity.from_mapping(value["source_identity"]), value["source_path"], _digest(value["source_sha256"]), _digest(value["version_binding_sha256"]), value["version_root"], DirectoryIdentity.from_mapping(value["version_root_identity"]))
        if _canonical(dataclasses.asdict(profile)) != data: raise CaptureError("profile is not canonical")
        return profile


def _open_dir(path: str, private: bool = False) -> tuple[int, DirectoryIdentity]:
    _absolute(path); fd = os.open("/", os.O_RDONLY | DIRECTORY | CLOEXEC)
    try:
        for part in pathlib.PurePosixPath(path).parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=fd); os.close(fd); fd = next_fd
        item = os.fstat(fd); identity = DirectoryIdentity.from_stat(item)
        if not stat.S_ISDIR(item.st_mode) or item.st_uid != os.getuid() or (private and (identity.mode != 0o700 or identity.nlink < 1)): raise CaptureError("directory authority changed")
        return fd, identity
    except BaseException:
        os.close(fd); raise


def _open_file(path: str, mode: int) -> tuple[int, FileIdentity]:
    parent, name = os.path.split(_absolute(path)); parent_fd, _ = _open_dir(parent)
    try: fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
    finally: os.close(parent_fd)
    raw = os.fstat(fd); identity = FileIdentity.from_stat(raw)
    if not stat.S_ISREG(raw.st_mode) or identity.uid != os.getuid() or identity.mode != mode or identity.nlink != 1: os.close(fd); raise CaptureError("file authority changed")
    return fd, identity


def _hash(fd: int, size: int, signals: Optional[Signals] = None) -> str:
    os.lseek(fd, 0, os.SEEK_SET); digest = hashlib.sha256(); remaining = size
    while remaining:
        if signals: signals.poll()
        block = os.read(fd, min(remaining, 1024 * 1024))
        if not block: raise CaptureError("file ended early")
        digest.update(block); remaining -= len(block)
    if os.read(fd, 1): raise CaptureError("file is oversized")
    return digest.hexdigest()


def _read_at(parent: int, name: str, limit: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
    try:
        item = os.fstat(fd)
        if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1 or item.st_size > limit: raise CaptureError("recovery evidence changed")
        data = os.read(fd, limit + 1)
        if len(data) != item.st_size or len(data) > limit: raise CaptureError("recovery evidence changed")
        return data
    finally: os.close(fd)


def _validate_recovery(profile: Profile) -> None:
    fd, identity = _open_dir(profile.version_root, True)
    try:
        if identity != profile.version_root_identity or set(os.listdir(fd)) != RECOVERY_FILES: raise CaptureError("recovery root changed")
        for name in RECOVERY_SCRATCH:
            scratch = os.open(name, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=fd)
            try:
                item = os.fstat(scratch)
                if not stat.S_ISDIR(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o700 or item.st_nlink != 2 or os.listdir(scratch): raise CaptureError("recovery scratch changed")
            finally: os.close(scratch)
        runner = _read_at(fd, "runner.py", EXPECTED_RECOVERY_RUNNER_BYTES + 1)
        if len(runner) != EXPECTED_RECOVERY_RUNNER_BYTES or hashlib.sha256(runner).hexdigest() != EXPECTED_RECOVERY_RUNNER_SHA256 or _read_at(fd, "runner.py.sha256", 128) != (EXPECTED_RECOVERY_RUNNER_SHA256 + "\n").encode("ascii"): raise CaptureError("recovery runner changed")
        binding = _read_at(fd, "version.binding.json", 2_051)
        if (profile.version_binding_sha256 != EXPECTED_RECOVERY_BINDING_SHA256 or hashlib.sha256(binding).hexdigest() != EXPECTED_RECOVERY_BINDING_SHA256 or _read_at(fd, "version.binding.sha256", 128) != (EXPECTED_RECOVERY_BINDING_SHA256 + "\n").encode("ascii")): raise CaptureError("recovery binding changed")
        if len(binding) != 2_051: raise CaptureError("recovery binding changed")
        value = _json(binding)
        if not isinstance(value, dict): raise CaptureError("recovery binding invalid")
        source, snapshot, version = value.get("source"), value.get("snapshot"), value.get("version")
        if (value.get("claim") != "snapshot-version-recovery" or not isinstance(source, dict) or not isinstance(snapshot, dict) or not isinstance(version, dict)
                or source.get("pre") != dataclasses.asdict(profile.source_identity) or source.get("post") != dataclasses.asdict(profile.source_identity) or source.get("sha256") != EXPECTED_SOURCE_SHA256
                or snapshot.get("pre") != dataclasses.asdict(profile.snapshot_identity) or snapshot.get("post") != dataclasses.asdict(profile.snapshot_identity) or snapshot.get("sha256") != EXPECTED_SOURCE_SHA256
                or version.get("logical_argv") != [profile.source_path, "--version"] or version.get("observed") != "1.1.12" or version.get("exit") != 0 or version.get("popen_count") != 1
                or _read_at(fd, "version.stdout", 7) != EXPECTED_RECOVERY_STDOUT or _read_at(fd, "version.stderr", 0) != b""):
            raise CaptureError("recovery binding claim changed")
        summary = _read_at(fd, "version.summary.json", EXPECTED_RECOVERY_SUMMARY_BYTES)
        summary_value = _json(summary)
        artifacts = value.get("artifacts")
        if (len(summary) != EXPECTED_RECOVERY_SUMMARY_BYTES or not isinstance(summary_value, dict)
                or not isinstance(artifacts, dict)
                or artifacts.get("version.summary.json") != hashlib.sha256(summary).hexdigest()
                or artifacts.get("version.stdout") != hashlib.sha256(EXPECTED_RECOVERY_STDOUT).hexdigest()
                or artifacts.get("version.stderr") != hashlib.sha256(b"").hexdigest()): raise CaptureError("recovery summary changed")
        if DirectoryIdentity.from_stat(os.stat(profile.version_root, follow_symlinks=False)) != identity or DirectoryIdentity.from_stat(os.fstat(fd)) != identity: raise CaptureError("recovery root changed")
    finally: os.close(fd)


def _disjoint(first: str, second: str) -> bool:
    return os.path.commonpath((first, second)) not in (first, second)


def _validate_profile(profile: Profile, signals: Signals) -> tuple[int, int, int, int]:
    if (profile.source_sha256 != EXPECTED_SOURCE_SHA256 or profile.version_binding_sha256 != EXPECTED_RECOVERY_BINDING_SHA256
            or os.path.basename(profile.source_path) != "agy.source" or os.path.dirname(profile.source_path) != profile.capture_parent
            or os.path.commonpath((profile.snapshot_path, profile.capture_parent)) != profile.capture_parent
            or os.path.commonpath((profile.version_root, profile.capture_parent)) != profile.capture_parent
            or not _disjoint(profile.account_home, profile.capture_parent)):
        raise CaptureError("profile topology changed")
    account_fd, account = _open_dir(profile.account_home, True)
    capture_fd, capture = _open_dir(profile.capture_parent, True)
    if account != profile.account_home_identity or capture.dev != profile.capture_parent_identity.dev or capture.ino != profile.capture_parent_identity.ino or capture.uid != profile.capture_parent_identity.uid or capture.gid != profile.capture_parent_identity.gid or capture.mode != profile.capture_parent_identity.mode:
        os.close(account_fd); os.close(capture_fd); raise CaptureError("profile directory identity changed")
    source_fd, source = _open_file(profile.source_path, 0o755); snapshot_fd, snapshot = _open_file(profile.snapshot_path, 0o500)
    if source != profile.source_identity or snapshot != profile.snapshot_identity:
        os.close(source_fd); os.close(snapshot_fd); os.close(account_fd); os.close(capture_fd); raise CaptureError("profile file identity changed")
    if _hash(source_fd, source.size, signals) != EXPECTED_SOURCE_SHA256 or _hash(snapshot_fd, snapshot.size, signals) != EXPECTED_SOURCE_SHA256:
        os.close(source_fd); os.close(snapshot_fd); os.close(account_fd); os.close(capture_fd); raise CaptureError("profile file hash changed")
    _validate_recovery(profile)
    return source_fd, snapshot_fd, account_fd, capture_fd


def _revalidate_authority(profile: Profile, source_fd: int, snapshot_fd: int, account_fd: int, capture_fd: int, signals: Signals) -> None:
    account = DirectoryIdentity.from_stat(os.fstat(account_fd)); capture = DirectoryIdentity.from_stat(os.fstat(capture_fd))
    source = FileIdentity.from_stat(os.fstat(source_fd)); snapshot = FileIdentity.from_stat(os.fstat(snapshot_fd))
    account_path_fd, account_path = _open_dir(profile.account_home, True); capture_path_fd, capture_path = _open_dir(profile.capture_parent, True)
    source_path_fd, source_path = _open_file(profile.source_path, 0o755); snapshot_path_fd, snapshot_path = _open_file(profile.snapshot_path, 0o500)
    try:
        if (account.dev != profile.account_home_identity.dev or account.ino != profile.account_home_identity.ino or account.uid != profile.account_home_identity.uid or account.gid != profile.account_home_identity.gid or account.mode != profile.account_home_identity.mode
            or account_path.dev != account.dev or account_path.ino != account.ino or account_path.uid != account.uid or account_path.gid != account.gid or account_path.mode != account.mode
            or source != profile.source_identity or snapshot != profile.snapshot_identity or source_path != source or snapshot_path != snapshot
            or capture.dev != profile.capture_parent_identity.dev or capture.ino != profile.capture_parent_identity.ino or capture.uid != profile.capture_parent_identity.uid or capture.gid != profile.capture_parent_identity.gid or capture.mode != profile.capture_parent_identity.mode
            or capture_path.dev != capture.dev or capture_path.ino != capture.ino or capture_path.uid != capture.uid or capture_path.mode != capture.mode
            or _hash(source_fd, source.size, signals) != EXPECTED_SOURCE_SHA256 or _hash(snapshot_fd, snapshot.size, signals) != EXPECTED_SOURCE_SHA256):
            raise CaptureError("capture authority changed")
        _validate_recovery(profile)
    finally:
        os.close(account_path_fd); os.close(capture_path_fd); os.close(source_path_fd); os.close(snapshot_path_fd)


def _new_root(parent: str) -> tuple[str, int]:
    parent_fd, _ = _open_dir(parent, True)
    try:
        for _ in range(32):
            name = "agy-models-capture-1-1-12." + os.urandom(12).hex()
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                fd = os.open(name, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
                if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700: os.close(fd); raise CaptureError("capture root mode changed")
                return os.path.join(parent, name), fd
            except FileExistsError: continue
        raise CaptureError("capture root allocation failed")
    finally: os.close(parent_fd)


def _write(root: int, name: str, data: bytes, signals: Optional[Signals] = None, ledger: Optional[dict[str, tuple[str, FileIdentity]]] = None) -> str:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | CLOEXEC | NOFOLLOW, 0o600, dir_fd=root)
    try:
        view = memoryview(data)
        while view:
            if signals: signals.poll()
            count = os.write(fd, view)
            if count <= 0: raise CaptureError("capture write failed")
            view = view[count:]
        os.fsync(fd)
        item = os.fstat(fd); identity = FileIdentity.from_stat(item)
        if stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1 or item.st_size != len(data): raise CaptureError("capture publication changed")
    finally: os.close(fd)
    verify = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=root)
    try:
        observed = FileIdentity.from_stat(os.fstat(verify))
        if observed != identity or os.read(verify, len(data) + 1) != data: raise CaptureError("capture publication changed")
    finally: os.close(verify)
    digest = hashlib.sha256(data).hexdigest()
    if ledger is not None: ledger[name] = (digest, identity)
    return digest


def _marker(root: int, digest: str, signals: Signals, ledger: dict[str, tuple[str, FileIdentity]]) -> FileIdentity:
    temporary = ".models.capture.marker." + os.urandom(12).hex()
    payload = (digest + "\n").encode("ascii"); temporary_identity: Optional[FileIdentity] = None; final_identity: Optional[FileIdentity] = None
    try:
        _write(root, temporary, payload, signals)
        temporary_identity = FileIdentity.from_stat(os.stat(temporary, dir_fd=root, follow_symlinks=False))
        signals.poll(); os.link(temporary, "models.capture.sha256", src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
        final_identity = FileIdentity.from_stat(os.stat("models.capture.sha256", dir_fd=root, follow_symlinks=False))
        if final_identity.dev != temporary_identity.dev or final_identity.ino != temporary_identity.ino or final_identity.nlink != 2 or final_identity.mode != 0o600: raise CaptureError("marker publication changed")
        temporary_after_link = FileIdentity.from_stat(os.stat(temporary, dir_fd=root, follow_symlinks=False))
        if temporary_after_link != final_identity: raise CaptureError("marker publication changed")
        temporary_identity = temporary_after_link
        os.unlink(temporary, dir_fd=root)
        final_identity = FileIdentity.from_stat(os.stat("models.capture.sha256", dir_fd=root, follow_symlinks=False))
        if final_identity.dev != temporary_identity.dev or final_identity.ino != temporary_identity.ino or final_identity.nlink != 1: raise CaptureError("marker publication changed")
        signals.poll(); os.fsync(root); signals.poll(); os.fsync(root)
        marker_fd = os.open("models.capture.sha256", os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=root)
        try:
            if FileIdentity.from_stat(os.fstat(marker_fd)) != final_identity or os.read(marker_fd, len(payload) + 1) != payload: raise CaptureError("marker publication changed")
        finally: os.close(marker_fd)
        ledger["models.capture.sha256"] = (hashlib.sha256(payload).hexdigest(), final_identity)
        return final_identity
    except BaseException:
        if final_identity is not None and temporary_identity is not None:
            try:
                final_now = FileIdentity.from_stat(os.stat("models.capture.sha256", dir_fd=root, follow_symlinks=False))
                temporary_now = FileIdentity.from_stat(os.stat(temporary, dir_fd=root, follow_symlinks=False))
                if final_now == final_identity and temporary_now == temporary_identity and final_now.nlink == 2 and temporary_now.nlink == 2:
                    os.unlink("models.capture.sha256", dir_fd=root)
                    derived_temporary = FileIdentity.from_stat(os.stat(temporary, dir_fd=root, follow_symlinks=False))
                    if (derived_temporary.dev == temporary_identity.dev and derived_temporary.ino == temporary_identity.ino
                            and derived_temporary.uid == temporary_identity.uid and derived_temporary.nlink == 1):
                        if FileIdentity.from_stat(os.stat(temporary, dir_fd=root, follow_symlinks=False)) == derived_temporary:
                            os.unlink(temporary, dir_fd=root)
                    temporary_identity = None
            except FileNotFoundError:
                pass
        for name, expected in (("models.capture.sha256", final_identity), (temporary, temporary_identity)):
            try:
                current = FileIdentity.from_stat(os.stat(name, dir_fd=root, follow_symlinks=False))
                if expected is not None and current == expected: os.unlink(name, dir_fd=root)
            except FileNotFoundError: pass
        os.fsync(root); raise


def _remove_owned_marker(root: int, digest: str, expected: FileIdentity) -> None:
    try:
        fd = os.open("models.capture.sha256", os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=root)
    except FileNotFoundError:
        return
    try:
        item = os.fstat(fd); data = os.read(fd, 128)
        observed = FileIdentity.from_stat(item)
        path_identity = FileIdentity.from_stat(os.stat("models.capture.sha256", dir_fd=root, follow_symlinks=False))
        if (stat.S_ISREG(item.st_mode) and item.st_uid == os.getuid() and stat.S_IMODE(item.st_mode) == 0o600 and item.st_nlink == 1 and data == (digest + "\n").encode("ascii")
                and observed == expected and path_identity == observed):
            os.unlink("models.capture.sha256", dir_fd=root); os.fsync(root)
    finally:
        os.close(fd)


def _rollback_active_marker() -> None:
    if ACTIVE_MARKER_ROOT is None or ACTIVE_MARKER_ROOT_IDENTITY is None or ACTIVE_MARKER_DIGEST is None or ACTIVE_MARKER_IDENTITY is None:
        return
    try:
        fd, _ = _open_dir(ACTIVE_MARKER_ROOT, True)
    except (CaptureError, OSError):
        return
    try:
        item = os.fstat(fd)
        if (item.st_dev, item.st_ino) == ACTIVE_MARKER_ROOT_IDENTITY:
            _remove_owned_marker(fd, ACTIVE_MARKER_DIGEST, ACTIVE_MARKER_IDENTITY)
    finally:
        os.close(fd)


def _capture(process: subprocess.Popen[bytes], signals: Signals) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None: raise CaptureError("capture streams unavailable")
    stdout_fd, stderr_fd = process.stdout.fileno(), process.stderr.fileno()
    buffers = {stdout_fd: (process.stdout, bytearray()), stderr_fd: (process.stderr, bytearray())}
    for fd in buffers: os.set_blocking(fd, False)
    deadline = time.monotonic() + WALL_SECONDS
    with selectors.DefaultSelector() as selector:
        for fd in buffers: selector.register(fd, selectors.EVENT_READ)
        while selector.get_map():
            signals.poll(); remaining = deadline - time.monotonic()
            if remaining <= 0: raise CaptureError("capture timed out")
            for key, _ in selector.select(min(remaining, 0.05)):
                stream, stored = buffers[key.fd]; block = os.read(key.fd, min(4096, STREAM_LIMIT + 1 - len(stored)))
                if not block: selector.unregister(key.fd); stream.close(); continue
                stored.extend(block)
                if len(stored) > STREAM_LIMIT: raise CaptureError("capture stream exceeded bound")
    return bytes(buffers[stdout_fd][1]), bytes(buffers[stderr_fd][1])


def _close_group(process: subprocess.Popen[bytes]) -> int:
    if process.returncode is not None or process.pid <= 1:
        raise CaptureError("capture group is not active")
    try:
        if os.getpgid(process.pid) != process.pid: raise CaptureError("capture group changed")
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        try:
            return process.wait(timeout=1.0)
        except subprocess.TimeoutExpired as exc:
            raise CaptureError("capture child could not be reaped") from exc
    except PermissionError as exc:
        raise CaptureError("capture group cannot be closed") from exc
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        result = process.poll()
        if result is not None:
            try:
                process.wait(timeout=0)
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return result
            except PermissionError as exc:
                raise CaptureError("capture group cannot be inspected") from exc
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        except PermissionError as exc:
            raise CaptureError("capture group cannot be inspected") from exc
        time.sleep(0.01)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        raise CaptureError("capture group cannot be closed") from exc
    try:
        result = process.wait(timeout=1.0)
    except subprocess.TimeoutExpired as exc:
        raise CaptureError("capture group could not be reaped") from exc
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return result
    except PermissionError as exc:
        raise CaptureError("capture group cannot be inspected") from exc
    raise CaptureError("capture group survives leader reap")


def _close_unvalidated_child(process: subprocess.Popen[bytes]) -> None:
    """Reap a direct child only when no process-group identity was observed."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        raise CaptureError("unvalidated capture child cannot be terminated") from exc
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired as exc:
        try:
            process.kill()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            raise CaptureError("unvalidated capture child could not be reaped") from exc


def _close_fast_exit_group(process: subprocess.Popen[bytes]) -> None:
    """Fail closed if the leader exited before PGID registration completed."""
    if process.pid <= 1:
        _close_unvalidated_child(process)
        raise CaptureError("capture process group registration changed")
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        _close_unvalidated_child(process)
        raise CaptureError("capture group cannot be closed") from exc
    _close_unvalidated_child(process)
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        raise CaptureError("capture process group registration changed")
    except PermissionError as exc:
        raise CaptureError("capture group cannot be inspected") from exc
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        raise CaptureError("capture process group registration changed")
    except PermissionError as exc:
        raise CaptureError("capture group cannot be closed") from exc
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            raise CaptureError("capture process group registration changed")
        except PermissionError as exc:
            raise CaptureError("capture group cannot be inspected") from exc
        time.sleep(0.01)
    raise CaptureError("capture process group survives leader exit")


def _start_capture(profile: Profile, root_path: str) -> subprocess.Popen[bytes]:
    """Launch and register the sole child before any unmasked lifecycle window."""
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
    try:
        environment = {"HOME": profile.account_home, "TMPDIR": os.path.join(root_path, "tmp"), "XDG_CACHE_HOME": os.path.join(root_path, "xdg-cache"), "XDG_CONFIG_HOME": os.path.join(root_path, "xdg-config"), "XDG_STATE_HOME": os.path.join(root_path, "xdg-state"), "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TERM": "dumb", "NO_COLOR": "1"}
        process = subprocess.Popen([profile.source_path, "models"], executable=profile.snapshot_path, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=os.path.join(root_path, "cwd"), env=environment, start_new_session=True, close_fds=True)
        try:
            pgid = os.getpgid(process.pid)
        except ProcessLookupError:
            _close_fast_exit_group(process)
            raise AssertionError("fast-exit close must raise")
        if process.pid <= 1 or pgid != process.pid:
            _close_unvalidated_child(process)
            raise CaptureError("capture process group registration changed")
        return process
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _empty_scratch(root: int) -> None:
    for name in SCRATCH_NAMES:
        fd = os.open(name, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=root)
        try:
            item = os.fstat(fd)
            if not stat.S_ISDIR(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o700 or item.st_nlink != 2 or os.listdir(fd): raise CaptureError("capture scratch changed")
        finally: os.close(fd)


def _verify_final_root(root_path: str, root_fd: int, profile: Profile, artifacts: dict[str, tuple[str, FileIdentity]], scratch_ledger: dict[str, FileIdentity], marker: bool = False) -> None:
    expected = set(SCRATCH_NAMES) | {"models_capture_1_1_12_runner.py", "models_capture_1_1_12_runner.py.sha256", OUTPUT_PROFILE_NAME, "models.stdout", "models.stderr", "models.capture.summary.json", "models.capture.json"}
    if marker: expected.add("models.capture.sha256")
    item = os.fstat(root_fd)
    path_item = os.stat(root_path, follow_symlinks=False)
    if (not stat.S_ISDIR(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o700
            or (item.st_dev, item.st_ino) != (path_item.st_dev, path_item.st_ino) or set(os.listdir(root_fd)) != expected):
        raise CaptureError("capture root inventory changed")
    _empty_scratch(root_fd)
    for name, identity in scratch_ledger.items():
        if FileIdentity.from_stat(os.stat(name, dir_fd=root_fd, follow_symlinks=False)) != identity:
            raise CaptureError("capture scratch identity changed")
    for name, expected_identity in artifacts.items():
        digest, identity = expected_identity
        fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=root_fd)
        try:
            item = os.fstat(fd)
            if not stat.S_ISREG(item.st_mode) or FileIdentity.from_stat(item) != identity or _hash(fd, item.st_size) != digest:
                raise CaptureError("capture artifact changed")
        finally:
            os.close(fd)
    parent_fd, parent = _open_dir(profile.capture_parent, True)
    try:
        if (parent.dev != profile.capture_parent_identity.dev or parent.ino != profile.capture_parent_identity.ino
                or parent.uid != profile.capture_parent_identity.uid or parent.gid != profile.capture_parent_identity.gid or parent.mode != profile.capture_parent_identity.mode):
            raise CaptureError("capture parent changed")
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def run_capture(profile: Profile, profile_bytes: bytes, signals: Signals, runner_source: bytes) -> dict[str, object]:
    global ACTIVE_MARKER_ROOT, ACTIVE_MARKER_ROOT_IDENTITY, ACTIVE_MARKER_DIGEST, ACTIVE_MARKER_IDENTITY
    source_fd, snapshot_fd, account_fd, capture_fd = _validate_profile(profile, signals)
    root_path = None; root_fd = None; process = None; record_sha: Optional[str] = None; marker_identity: Optional[FileIdentity] = None; completed = False; ledger: dict[str, tuple[str, FileIdentity]] = {}; scratch_ledger: dict[str, FileIdentity] = {}
    try:
        root_path, root_fd = _new_root(profile.capture_parent)
        for name in SCRATCH_NAMES:
            os.mkdir(name, 0o700, dir_fd=root_fd)
            scratch_ledger[name] = FileIdentity.from_stat(os.stat(name, dir_fd=root_fd, follow_symlinks=False))
        _empty_scratch(root_fd)
        process = _start_capture(profile, root_path)
        closed = False
        try:
            stdout, stderr = _capture(process, signals)
            rc = _close_group(process)
            closed = True
        except BaseException:
            if not closed: _close_group(process)
            raise
        if rc != 0: raise CaptureError("capture child failed")
        _revalidate_authority(profile, source_fd, snapshot_fd, account_fd, capture_fd, signals)
        _empty_scratch(root_fd)
        account_post = DirectoryIdentity.from_stat(os.fstat(account_fd))
        source_post = FileIdentity.from_stat(os.fstat(source_fd))
        snapshot_post = FileIdentity.from_stat(os.fstat(snapshot_fd))
        scratch = {}
        for name in SCRATCH_NAMES:
            child = os.open(name, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=root_fd)
            try: scratch[name] = dataclasses.asdict(DirectoryIdentity.from_stat(os.fstat(child)))
            finally: os.close(child)
        profile_sha = hashlib.sha256(profile_bytes).hexdigest()
        _write(root_fd, "models_capture_1_1_12_runner.py", runner_source, ledger=ledger)
        _write(root_fd, "models_capture_1_1_12_runner.py.sha256", (hashlib.sha256(runner_source).hexdigest() + "\n").encode("ascii"), ledger=ledger)
        _write(root_fd, OUTPUT_PROFILE_NAME, profile_bytes, ledger=ledger)
        stdout_sha, stderr_sha = _write(root_fd, "models.stdout", stdout, ledger=ledger), _write(root_fd, "models.stderr", stderr, ledger=ledger)
        summary = {"artifacts": {"models.stderr": stderr_sha, "models.stdout": stdout_sha}, "bounds": {"stream_bytes": STREAM_LIMIT, "wall_seconds": WALL_SECONDS}, "claim": "models-capture", "input_profile_sha256": profile_sha, "limitations": {"accepted_inventory": False, "account_home_may_mutate": True, "metadata_updated": False, "provider_backend_proven": False, "routing_authority": False}, "observation": {"argv": [profile.source_path, "models"], "exit": rc, "popen_count": 1}, "snapshot_sha256": EXPECTED_SOURCE_SHA256, "source_sha256": EXPECTED_SOURCE_SHA256, "status": "captured", "version_binding_sha256": profile.version_binding_sha256}
        summary_sha = _write(root_fd, "models.capture.summary.json", _canonical(summary), ledger=ledger)
        runner_sha = hashlib.sha256(runner_source).hexdigest()
        artifacts = {OUTPUT_PROFILE_NAME: profile_sha, "models.capture.summary.json": summary_sha, "models.stderr": stderr_sha, "models.stdout": stdout_sha, "models_capture_1_1_12_runner.py": runner_sha, "models_capture_1_1_12_runner.py.sha256": hashlib.sha256((runner_sha + "\n").encode("ascii")).hexdigest()}
        record = {"account": {"post": dataclasses.asdict(account_post), "pre": dataclasses.asdict(profile.account_home_identity)}, "artifacts": artifacts, "bounds": {"stream_bytes": STREAM_LIMIT, "wall_seconds": WALL_SECONDS}, "claim": "models-capture", "input_profile_sha256": profile_sha, "limitations": summary["limitations"], "observation": {"argv": [profile.source_path, "models"], "executable_sha256": EXPECTED_SOURCE_SHA256, "exit": rc, "popen_count": 1}, "runner_sha256": hashlib.sha256(runner_source).hexdigest(), "scratch": scratch, "snapshot": {"post": dataclasses.asdict(snapshot_post), "pre": dataclasses.asdict(profile.snapshot_identity), "sha256": EXPECTED_SOURCE_SHA256}, "source": {"post": dataclasses.asdict(source_post), "pre": dataclasses.asdict(profile.source_identity), "sha256": EXPECTED_SOURCE_SHA256}, "status": "captured", "version_binding_sha256": profile.version_binding_sha256}
        record_sha = _write(root_fd, "models.capture.json", _canonical(record), ledger=ledger)
        _verify_final_root(root_path, root_fd, profile, ledger, scratch_ledger)
        marker_identity = _marker(root_fd, record_sha, signals, ledger)
        _verify_final_root(root_path, root_fd, profile, ledger, scratch_ledger, True)
        root_item = os.fstat(root_fd)
        ACTIVE_MARKER_ROOT, ACTIVE_MARKER_ROOT_IDENTITY, ACTIVE_MARKER_DIGEST, ACTIVE_MARKER_IDENTITY = root_path, (root_item.st_dev, root_item.st_ino), record_sha, marker_identity
        signals.poll()
        completed = True
        return {"artifact_root": root_path, "capture_sha256": record_sha, "status": "captured"}
    finally:
        if root_fd is not None and not completed and record_sha is not None and marker_identity is not None:
            _remove_owned_marker(root_fd, record_sha, marker_identity)
        os.close(source_fd); os.close(snapshot_fd); os.close(account_fd); os.close(capture_fd)
        if root_fd is not None: os.close(root_fd)


def validate_source_contract(data: bytes) -> dict[str, str]:
    try: tree = ast.parse(data.decode("utf-8", "strict"))
    except (UnicodeDecodeError, SyntaxError) as exc: raise CaptureError("runner source invalid") from exc
    pin = next((node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "MODULE_AST_SHA256"), None)
    if pin is None: raise CaptureError("runner source pin is missing")
    pin.value = ast.Constant(value="PINNED-MODULE-AST")
    if hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest() != MODULE_AST_SHA256: raise CaptureError("runner source structure changed")
    imports = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
    if any(isinstance(node, ast.ImportFrom) and node.module not in {"__future__", "dataclasses", "typing"} for node in tree.body): raise CaptureError("runner source gained imported authority")
    if imports & {"importlib", "socket", "urllib", "http"}: raise CaptureError("runner source gained ambient authority")
    popens = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"]
    if len(popens) != 1: raise CaptureError("runner must own exactly one Popen")
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"system", "popen", "run", "call", "check_call", "check_output"} for node in ast.walk(tree)): raise CaptureError("runner source gained execution authority")
    popen_text = ast.get_source_segment(data.decode("utf-8", "strict"), popens[0]) or ""
    for required in ("[profile.source_path, \"models\"]", "executable=profile.snapshot_path", "env=environment", "cwd=os.path.join(root_path, \"cwd\")", "start_new_session=True", "close_fds=True"):
        if required not in popen_text: raise CaptureError("runner Popen contract changed")
    source = data.decode("utf-8", "strict")
    for required in ("_close_group(process)", "_marker(root_fd, record_sha, signals)", "\"routing_authority\": False", "\"metadata_updated\": False", "_finish_success(state, result)"):
        if required not in source: raise CaptureError("runner semantic contract changed")
    return {"status": "valid-source"}


def _held_source() -> bytes:
    path = _absolute(os.path.realpath(__file__))
    parent, name = os.path.split(path); parent_fd, _ = _open_dir(parent)
    try:
        fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        item = os.fstat(fd)
        if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) & 0o022 or item.st_size <= 0 or item.st_size > 128 * 1024:
            raise CaptureError("runner source authority changed")
        data = os.read(fd, item.st_size + 1)
        if len(data) != item.st_size or os.fstat(fd).st_ino != item.st_ino:
            raise CaptureError("runner source authority changed")
    finally:
        os.close(fd)
    validate_source_contract(data)
    return data


def _read_stdin(limit: int = PROFILE_LIMIT, signals: Optional[Signals] = None) -> bytes:
    chunks = bytearray()
    while len(chunks) <= limit:
        if signals: signals.poll()
        block = os.read(sys.stdin.buffer.fileno(), min(4096, limit + 1 - len(chunks)))
        if not block: return bytes(chunks)
        chunks.extend(block)
    raise CaptureError("stdin exceeds bound")


def _finish_success(state: Lifecycle, result: dict[str, object]) -> None:
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
            _rollback_active_marker()
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
            _rollback_active_marker()
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
    if not _runtime_supported() or len(argv) != 1 or argv[0] not in ("--capture-models", "--validate-source-contract"): return 64
    state = _acquire()
    try:
        held = _held_source()
        raw = _read_stdin(128 * 1024 if argv[0] == "--validate-source-contract" else PROFILE_LIMIT, state.signals)
        result = validate_source_contract(raw) if argv[0] == "--validate-source-contract" else run_capture(Profile.from_bytes(raw), raw, state.signals, held)
        _finish_success(state, result)
    except Interrupted as exc: return exc.code
    except (CaptureError, OSError, ValueError): return 1


if __name__ == "__main__":
    if not _runtime_supported(): os._exit(64)
    os._exit(main(sys.argv[1:]))
