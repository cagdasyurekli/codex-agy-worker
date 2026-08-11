#!/usr/bin/env python3
"""Prepare or validate one canonical profile for explicit-account models capture.

This is deliberately process-inert maintenance tooling.  It never launches agy,
reads ambient configuration, or searches an account HOME.  The caller supplies all
five paths on stdin; the builder reopens every authority path with no-follow
descriptors before publishing the exact profile consumed by models_capture_runner.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import signal
import stat
import sys
from dataclasses import dataclass
from typing import Optional, Sequence


sys.dont_write_bytecode = True

PROFILE_LIMIT = 16_384
EXPECTED_SOURCE_SHA256 = "198ff7c3f6d173daa510b0814aa70c6ce14c94035bcd4707a3c0e79fa38a7bc3"
EXPECTED_VERSION_BINDING_SHA256 = "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2"
IDENTITY_KEYS = frozenset(
    {"ctime_ns", "dev", "gid", "ino", "mode", "mtime_ns", "nlink", "size", "uid"}
)
DIRECTORY_IDENTITY_KEYS = frozenset({"dev", "gid", "ino", "mode", "nlink", "uid"})
REQUEST_KEYS = frozenset(
    {"account_home", "output_path", "snapshot_path", "source_path", "version_root"}
)
VALIDATE_KEYS = frozenset({"profile_path"})
PROFILE_KEYS = frozenset(
    {
        "account_home",
        "account_home_identity",
        "snapshot_identity",
        "snapshot_path",
        "source_identity",
        "source_path",
        "source_sha256",
        "temp_parent",
        "version_binding_sha256",
        "version_root",
    }
)
VERSION_ROOT_FILES = frozenset(
    {
        "cwd",
        "home",
        "tmp",
        "xdg-cache",
        "xdg-config",
        "xdg-state",
        "runner.py",
        "runner.py.sha256",
        "version.binding.json",
        "version.binding.sha256",
        "version.stderr",
        "version.stdout",
        "version.summary.json",
    }
)
OUTPUT_BASENAME = "models.capture.profile.json"
MODULE_AST_SHA256 = "798fd1b42d4b45e0e0687f25e8fbaaa19f412e4975e50f4ae7ecfe22e9e58d1b"
LIFECYCLE_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
NOFOLLOW = os.O_NOFOLLOW
CLOEXEC = os.O_CLOEXEC
DIRECTORY = os.O_DIRECTORY
ALLOWED_MODULE_IMPORTS = (
    ("ast", None), ("dataclasses", None), ("hashlib", None), ("json", None),
    ("os", None), ("signal", None), ("stat", None), ("sys", None),
)
ALLOWED_FROM_IMPORTS = (
    ("__future__", "annotations", None), ("dataclasses", "dataclass", None),
    ("typing", "Optional", None), ("typing", "Sequence", None),
)
ALLOWED_IMPORTED_CALLS = frozenset(
    {
        ("ast", "Constant"), ("ast", "dump"), ("ast", "get_source_segment"),
        ("ast", "literal_eval"), ("ast", "parse"), ("ast", "walk"),
        ("dataclasses", "asdict"), ("hashlib", "sha256"),
        ("json", "dumps"), ("json", "loads"),
        ("os", "close"), ("os", "fstat"), ("os", "fsync"), ("os", "getuid"),
        ("os", "link"), ("os", "listdir"), ("os", "lseek"), ("os", "open"),
        ("os", "path", "abspath"), ("os", "path", "commonpath"),
        ("os", "path", "dirname"), ("os", "path", "normpath"),
        ("os", "path", "split"), ("os", "read"), ("os", "stat"),
        ("os", "unlink"), ("os", "urandom"), ("os", "write"), ("os", "_exit"),
        ("signal", "getsignal"), ("signal", "pthread_sigmask"),
        ("signal", "signal"), ("signal", "sigpending"), ("signal", "sigwait"),
        ("stat", "S_IMODE"), ("stat", "S_ISDIR"), ("stat", "S_ISREG"),
        ("sys", "stdin", "buffer", "fileno"),
        ("sys", "stdout", "buffer", "fileno"),
        ("sys", "stdout", "buffer", "flush"),
        ("sys", "stderr", "buffer", "fileno"),
    }
)
FORBIDDEN_SYMBOL_PREFIXES = (
    ("__builtins__", "__import__"), ("builtins", "__import__"),
    ("importlib", "import_module"), ("os", "environ"), ("os", "getenv"),
    ("os", "scandir"), ("os", "walk"), ("pathlib", "Path", "home"),
    ("pathlib", "Path", "expanduser"), ("pathlib", "Path", "glob"),
    ("pathlib", "Path", "rglob"), ("pathlib", "Path", "iterdir"),
    ("Path", "home"), ("Path", "expanduser"), ("Path", "glob"),
    ("Path", "rglob"), ("Path", "iterdir"), ("socket",), ("http",),
    ("urllib",), ("git",), ("subprocess",),
)
FORBIDDEN_DIRECT_CALLS = frozenset(
    {
        "__import__", "import_module", "getattr", "setattr", "delattr", "globals",
        "locals", "vars", "eval", "exec", "compile", "Popen", "run", "call",
        "system", "fork", "spawn", "popen",
    }
)
PRODUCTION_HELPER_NAMES = frozenset(
    {
        "prepare", "validate", "_account_identity", "_canonical_json", "_canonical_request",
        "_hash_descriptor", "_inside", "_open_directory", "_open_regular", "_owns_inode",
        "_profile_from_request", "_publish_profile", "_read_at", "_reject_overlap",
        "_remove_owned", "_require_absolute", "_strict_json", "_validate_version_evidence",
        "_verify_regular", "_poll",
    }
)
REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTIVE_OUTPUT_PATH: Optional[str] = None
ACTIVE_OUTPUT_IDENTITY: Optional[FileIdentity] = None
ACTIVE_FIRST_SIGNAL: Optional[int] = None
ACTIVE_SIGNAL_MODE = False
ACTIVE_CONTROLLER = None


class ModelsCaptureProfileError(ValueError):
    """The supplied maintenance authority is invalid or changed."""


class ModelsCaptureProfileInterrupted(SystemExit):
    def __init__(self, signum: int):
        super().__init__(128 + signum)
        self.signum = signum


class SignalController:
    def __init__(self, owned: Sequence[signal.Signals]) -> None:
        self.owned = tuple(owned)
        self.observed: set[int] = set()
        self.selected: Optional[int] = None

    def latch(self, signum: int, _frame: object = None) -> None:
        if signum in self.owned:
            self.observed.add(signum)

    def merge_pending(self) -> None:
        while True:
            pending = set(signal.sigpending()).intersection(self.owned)
            chosen = next((item for item in self.owned if item in pending), None)
            if chosen is None:
                return
            self.latch(signal.sigwait({chosen}))

    def choose(self) -> Optional[int]:
        if self.selected is None:
            self.selected = next(
                (item for item in self.owned if item in self.observed), None
            )
        return self.selected

    def poll(self) -> None:
        chosen = self.choose()
        if chosen is not None:
            raise ModelsCaptureProfileInterrupted(chosen)


@dataclass
class LifecycleState:
    controller: SignalController
    entry_mask: set[signal.Signals]
    old_handlers: dict[signal.Signals, object]
    installed_handlers: list[signal.Signals]


def _acquire_lifecycle() -> LifecycleState:
    if not all(
        hasattr(signal, name) for name in ("pthread_sigmask", "sigpending", "sigwait")
    ):
        raise ModelsCaptureProfileError("required signal primitives are unavailable")
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
    old_handlers = {item: signal.getsignal(item) for item in LIFECYCLE_SIGNALS}
    owned = tuple(
        item
        for item in LIFECYCLE_SIGNALS
        if item not in entry_mask and old_handlers[item] is not signal.SIG_IGN
    )
    controller = SignalController(owned)
    installed: list[signal.Signals] = []
    try:
        for item in owned:
            signal.signal(item, controller.latch)
            installed.append(item)
        controller.merge_pending()
        return LifecycleState(controller, entry_mask, old_handlers, installed)
    except BaseException:
        for item in reversed(installed):
            signal.signal(item, old_handlers[item])
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        raise


def _activate_lifecycle(state: LifecycleState) -> None:
    signal.pthread_sigmask(signal.SIG_SETMASK, state.entry_mask)
    state.controller.poll()


def _poll() -> None:
    if ACTIVE_CONTROLLER is not None:
        ACTIVE_CONTROLLER.poll()


def _write_all(descriptor: int, data: bytes, controller: SignalController) -> None:
    remaining = memoryview(data)
    while remaining:
        controller.poll()
        written = os.write(descriptor, remaining)
        controller.poll()
        if written <= 0:
            raise ModelsCaptureProfileError("profile result write failed")
        remaining = remaining[written:]


def _atomic_exit(code: int, descriptor: int, message: bytes) -> None:
    try:
        remaining = memoryview(message)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                break
            remaining = remaining[written:]
    except OSError:
        pass
    os._exit(code)


def _strict_json(data: bytes) -> object:
    def duplicate_key(pairs: object) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:  # type: ignore[union-attr]
            if not isinstance(key, str) or key in result:
                raise ModelsCaptureProfileError("invalid JSON object")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> object:
        raise ModelsCaptureProfileError("invalid JSON constant")

    try:
        value = json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=duplicate_key,
            parse_constant=invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ModelsCaptureProfileError("invalid JSON") from exc
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"


def _canonical_request(data: bytes, keys: frozenset[str], label: str) -> dict[str, object]:
    value = _strict_json(data)
    if not isinstance(value, dict) or set(value) != keys or _canonical_json(value) != data:
        raise ModelsCaptureProfileError("invalid " + label + " request")
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        item in "0123456789abcdef" for item in value
    )


def _require_absolute(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or "\x00" in value
        or value == "/"
        or os.path.normpath(value) != value
        or "//" in value
    ):
        raise ModelsCaptureProfileError("invalid path")
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
        return cls(
            ctime_ns=value.st_ctime_ns,
            dev=value.st_dev,
            gid=value.st_gid,
            ino=value.st_ino,
            mode=stat.S_IMODE(value.st_mode),
            mtime_ns=value.st_mtime_ns,
            nlink=value.st_nlink,
            size=value.st_size,
            uid=value.st_uid,
        )

    @classmethod
    def from_mapping(cls, value: object) -> "FileIdentity":
        if not isinstance(value, dict) or set(value) != IDENTITY_KEYS:
            raise ModelsCaptureProfileError("invalid file identity")
        if any(type(value[key]) is not int or value[key] < 0 for key in IDENTITY_KEYS):
            raise ModelsCaptureProfileError("invalid file identity")
        return cls(**value)

    def as_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


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
        return cls(
            dev=value.st_dev,
            gid=value.st_gid,
            ino=value.st_ino,
            mode=stat.S_IMODE(value.st_mode),
            nlink=value.st_nlink,
            uid=value.st_uid,
        )

    @classmethod
    def from_mapping(cls, value: object) -> "DirectoryIdentity":
        if not isinstance(value, dict) or set(value) != DIRECTORY_IDENTITY_KEYS:
            raise ModelsCaptureProfileError("invalid account identity")
        if any(
            type(value[key]) is not int or value[key] < 0
            for key in DIRECTORY_IDENTITY_KEYS
        ):
            raise ModelsCaptureProfileError("invalid account identity")
        return cls(**value)

    def as_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class CaptureProfile:
    account_home: str
    account_home_identity: DirectoryIdentity
    snapshot_identity: FileIdentity
    snapshot_path: str
    source_identity: FileIdentity
    source_path: str
    source_sha256: str
    temp_parent: str
    version_binding_sha256: str
    version_root: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "CaptureProfile":
        value = _strict_json(data)
        if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
            raise ModelsCaptureProfileError("invalid capture profile")
        for key in (
            "account_home",
            "snapshot_path",
            "source_path",
            "temp_parent",
            "version_root",
        ):
            _require_absolute(value[key])
        if (
            value.get("source_sha256") != EXPECTED_SOURCE_SHA256
            or value.get("version_binding_sha256") != EXPECTED_VERSION_BINDING_SHA256
        ):
            raise ModelsCaptureProfileError("unreviewed capture profile")
        profile = cls(
            account_home=value["account_home"],
            account_home_identity=DirectoryIdentity.from_mapping(value["account_home_identity"]),
            snapshot_identity=FileIdentity.from_mapping(value["snapshot_identity"]),
            snapshot_path=value["snapshot_path"],
            source_identity=FileIdentity.from_mapping(value["source_identity"]),
            source_path=value["source_path"],
            source_sha256=value["source_sha256"],
            temp_parent=value["temp_parent"],
            version_binding_sha256=value["version_binding_sha256"],
            version_root=value["version_root"],
        )
        if _canonical_json(profile.as_mapping()) != data:
            raise ModelsCaptureProfileError("capture profile is not canonical")
        return profile

    def as_mapping(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def _open_directory(path: str, *, leaf_private: bool = False) -> int:
    _require_absolute(path)
    descriptor = os.open("/", os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW)
    try:
        root = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(root.st_mode)
            or root.st_uid != 0
            or stat.S_IMODE(root.st_mode) & 0o022
        ):
            raise ModelsCaptureProfileError("directory root policy changed")
        for part in path.split("/")[1:]:
            next_descriptor = os.open(
                part, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
            value = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(value.st_mode)
                or value.st_uid not in {0, os.getuid()}
                or stat.S_IMODE(value.st_mode) & 0o022
            ):
                raise ModelsCaptureProfileError("directory component policy changed")
        leaf = os.fstat(descriptor)
        if leaf_private and (
            leaf.st_uid != os.getuid() or stat.S_IMODE(leaf.st_mode) != 0o700
        ):
            raise ModelsCaptureProfileError("directory is not owner-private")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_regular(path: str, expected_mode: int) -> tuple[int, int, FileIdentity, str]:
    path = _require_absolute(path)
    parent_path, leaf = os.path.split(path)
    parent = _open_directory(parent_path)
    try:
        descriptor = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
    except BaseException:
        os.close(parent)
        raise
    value = os.fstat(descriptor)
    identity = FileIdentity.from_stat(value)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) != expected_mode
        or value.st_nlink != 1
        or value.st_size <= 0
    ):
        os.close(descriptor)
        os.close(parent)
        raise ModelsCaptureProfileError("attested executable policy changed")
    return parent, descriptor, identity, leaf


def _hash_descriptor(descriptor: int, size: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        _poll()
        block = os.read(descriptor, min(65_536, remaining))
        _poll()
        if not block:
            raise ModelsCaptureProfileError("attested executable truncated")
        digest.update(block)
        remaining -= len(block)
    _poll()
    if os.read(descriptor, 1):
        raise ModelsCaptureProfileError("attested executable grew")
    _poll()
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _verify_regular(
    parent: int, descriptor: int, identity: FileIdentity, leaf: str, expected_sha: str
) -> None:
    held = FileIdentity.from_stat(os.fstat(descriptor))
    current = FileIdentity.from_stat(os.stat(leaf, dir_fd=parent, follow_symlinks=False))
    reopened = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
    try:
        reopened_identity = FileIdentity.from_stat(os.fstat(reopened))
        reopened_sha = _hash_descriptor(reopened, identity.size)
    finally:
        os.close(reopened)
    if (
        held != identity
        or current != identity
        or reopened_identity != identity
        or _hash_descriptor(descriptor, identity.size) != expected_sha
        or reopened_sha != expected_sha
    ):
        raise ModelsCaptureProfileError("attested executable identity changed")


def _read_at(parent: int, name: str, maximum: int) -> bytes:
    if not isinstance(name, str) or not name or "/" in name or maximum < 0:
        raise ModelsCaptureProfileError("invalid evidence member")
    descriptor = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
    try:
        value = os.fstat(descriptor)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_nlink != 1
            or value.st_size > maximum
        ):
            raise ModelsCaptureProfileError("version evidence member changed")
        data = os.read(descriptor, maximum + 1)
        if len(data) != value.st_size or len(data) > maximum:
            raise ModelsCaptureProfileError("version evidence member changed")
        return data
    finally:
        os.close(descriptor)


def _validate_version_evidence(
    version_root: str,
    source_path: str,
    source_identity: FileIdentity,
    snapshot_identity: FileIdentity,
) -> None:
    descriptor = _open_directory(version_root, leaf_private=True)
    try:
        if set(os.listdir(descriptor)) != VERSION_ROOT_FILES:
            raise ModelsCaptureProfileError("version evidence shape changed")
        binding_bytes = _read_at(descriptor, "version.binding.json", PROFILE_LIMIT)
        binding_digest = _read_at(descriptor, "version.binding.sha256", 128)
        if (
            hashlib.sha256(binding_bytes).hexdigest() != EXPECTED_VERSION_BINDING_SHA256
            or binding_digest != (EXPECTED_VERSION_BINDING_SHA256 + "\n").encode("ascii")
        ):
            raise ModelsCaptureProfileError("version binding changed")
        binding = _strict_json(binding_bytes)
        if not isinstance(binding, dict):
            raise ModelsCaptureProfileError("version binding is invalid")
        source = binding.get("source")
        snapshot = binding.get("snapshot")
        observed = binding.get("version")
        if (
            binding.get("claim") != "snapshot-version-recovery"
            or not isinstance(source, dict)
            or source.get("pre") != source_identity.as_dict()
            or source.get("post") != source_identity.as_dict()
            or source.get("sha256") != EXPECTED_SOURCE_SHA256
            or not isinstance(snapshot, dict)
            or snapshot.get("pre") != snapshot_identity.as_dict()
            or snapshot.get("post") != snapshot_identity.as_dict()
            or snapshot.get("sha256") != EXPECTED_SOURCE_SHA256
            or not isinstance(observed, dict)
            or observed.get("exit") != 0
            or observed.get("logical_argv") != [source_path, "--version"]
            or observed.get("observed") != "1.1.11"
            or observed.get("popen_count") != 1
            or _read_at(descriptor, "version.stdout", 128) != b"1.1.11\n"
            or _read_at(descriptor, "version.stderr", 128) != b""
        ):
            raise ModelsCaptureProfileError("version binding claim changed")
    finally:
        os.close(descriptor)


def _account_identity(path: str) -> DirectoryIdentity:
    descriptor = _open_directory(path, leaf_private=True)
    try:
        value = os.fstat(descriptor)
        if value.st_nlink < 1:
            raise ModelsCaptureProfileError("account HOME identity changed")
        return DirectoryIdentity.from_stat(value)
    finally:
        os.close(descriptor)


def _inside(path: str, parent: str) -> bool:
    return os.path.commonpath((path, parent)) == parent


def _reject_overlap(*paths: str) -> None:
    for index, first in enumerate(paths):
        for second in paths[index + 1:]:
            if _inside(first, second) or _inside(second, first):
                raise ModelsCaptureProfileError("capture authorities overlap")


def _profile_from_request(value: object) -> tuple[CaptureProfile, str]:
    if not isinstance(value, dict) or set(value) != REQUEST_KEYS:
        raise ModelsCaptureProfileError("invalid prepare request")
    account_home = _require_absolute(value["account_home"])
    output_path = _require_absolute(value["output_path"])
    snapshot_path = _require_absolute(value["snapshot_path"])
    source_path = _require_absolute(value["source_path"])
    version_root = _require_absolute(value["version_root"])
    output_parent, output_name = os.path.split(output_path)
    if output_name != OUTPUT_BASENAME:
        raise ModelsCaptureProfileError("capture profile basename is fixed")
    temp_parent = os.path.dirname(version_root)
    _require_absolute(temp_parent)
    _reject_overlap(
        REPOSITORY_ROOT,
        account_home,
        version_root,
        output_parent,
    )
    if _inside(source_path, account_home) or _inside(snapshot_path, account_home):
        raise ModelsCaptureProfileError("attested executable overlaps account HOME")
    output_descriptor = _open_directory(output_parent, leaf_private=True)
    os.close(output_descriptor)
    temp_descriptor = _open_directory(temp_parent, leaf_private=True)
    os.close(temp_descriptor)
    account_identity = _account_identity(account_home)
    source_parent = source_fd = snapshot_parent = snapshot_fd = None
    try:
        source_parent, source_fd, source_identity, source_leaf = _open_regular(source_path, 0o755)
        snapshot_parent, snapshot_fd, snapshot_identity, snapshot_leaf = _open_regular(snapshot_path, 0o500)
        if (
            _hash_descriptor(source_fd, source_identity.size) != EXPECTED_SOURCE_SHA256
            or _hash_descriptor(snapshot_fd, snapshot_identity.size) != EXPECTED_SOURCE_SHA256
        ):
            raise ModelsCaptureProfileError("attested executable hash changed")
        _validate_version_evidence(version_root, source_path, source_identity, snapshot_identity)
        _verify_regular(source_parent, source_fd, source_identity, source_leaf, EXPECTED_SOURCE_SHA256)
        _verify_regular(snapshot_parent, snapshot_fd, snapshot_identity, snapshot_leaf, EXPECTED_SOURCE_SHA256)
        return (
            CaptureProfile(
                account_home=account_home,
                account_home_identity=account_identity,
                snapshot_identity=snapshot_identity,
                snapshot_path=snapshot_path,
                source_identity=source_identity,
                source_path=source_path,
                source_sha256=EXPECTED_SOURCE_SHA256,
                temp_parent=temp_parent,
                version_binding_sha256=EXPECTED_VERSION_BINDING_SHA256,
                version_root=version_root,
            ),
            output_path,
        )
    finally:
        for descriptor in (snapshot_fd, snapshot_parent, source_fd, source_parent):
            if descriptor is not None:
                os.close(descriptor)


def _owns_inode(value: FileIdentity, identity: Optional[FileIdentity]) -> bool:
    return identity is not None and (
        value.dev == identity.dev
        and value.ino == identity.ino
        and value.uid == identity.uid
        and value.mode == identity.mode
    )


def _remove_owned(parent: int, name: str, identity: Optional[FileIdentity]) -> bool:
    if identity is None or not name:
        return False
    try:
        value = FileIdentity.from_stat(os.stat(name, dir_fd=parent, follow_symlinks=False))
    except FileNotFoundError:
        return False
    if not _owns_inode(value, identity):
        return False
    os.unlink(name, dir_fd=parent)
    return True


def _publish_profile(output_path: str, data: bytes) -> str:
    global ACTIVE_OUTPUT_IDENTITY
    parent_path, final_name = os.path.split(output_path)
    parent = _open_directory(parent_path, leaf_private=True)
    temporary_name: Optional[str] = None
    temporary_identity: Optional[FileIdentity] = None
    publisher_identity: Optional[FileIdentity] = None
    try:
        if os.stat(final_name, dir_fd=parent, follow_symlinks=False):
            raise ModelsCaptureProfileError("capture profile already exists")
    except FileNotFoundError:
        pass
    try:
        _poll()
        for _index in range(32):
            _poll()
            candidate = ".models.capture.profile." + os.urandom(16).hex() + ".tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | CLOEXEC | NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
                temporary_name = candidate
                temporary_identity = FileIdentity.from_stat(os.fstat(descriptor))
                if (
                    temporary_identity.uid != os.getuid()
                    or temporary_identity.mode != 0o600
                    or temporary_identity.nlink != 1
                    or temporary_identity.size != 0
                ):
                    raise ModelsCaptureProfileError("private publication identity changed")
                break
            except FileExistsError:
                continue
        else:
            raise ModelsCaptureProfileError("private publication allocation failed")
        try:
            written = 0
            while written < len(data):
                _poll()
                count = os.write(descriptor, data[written:])
                _poll()
                if count <= 0:
                    raise ModelsCaptureProfileError("private publication write failed")
                written += count
            os.fsync(descriptor)
            _poll()
            temporary_identity = FileIdentity.from_stat(os.fstat(descriptor))
            if (
                temporary_identity.uid != os.getuid()
                or temporary_identity.mode != 0o600
                or temporary_identity.nlink != 1
                or temporary_identity.size != len(data)
            ):
                raise ModelsCaptureProfileError("private publication identity changed")
        finally:
            os.close(descriptor)
        publisher_identity = temporary_identity
        if ACTIVE_SIGNAL_MODE:
            ACTIVE_OUTPUT_IDENTITY = publisher_identity
        os.link(
            temporary_name, final_name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
        )
        observed_final_identity = FileIdentity.from_stat(
            os.stat(final_name, dir_fd=parent, follow_symlinks=False)
        )
        if (
            not _owns_inode(observed_final_identity, publisher_identity)
            or observed_final_identity.uid != os.getuid()
            or observed_final_identity.mode != 0o600
            or observed_final_identity.nlink != 2
            or observed_final_identity.size != len(data)
            or observed_final_identity.dev != publisher_identity.dev
            or observed_final_identity.ino != publisher_identity.ino
        ):
            raise ModelsCaptureProfileError("private publication link changed")
        os.fsync(parent)
        _poll()
        if not _remove_owned(parent, temporary_name, temporary_identity):
            raise ModelsCaptureProfileError("private publication temporary changed")
        temporary_name = None
        os.fsync(parent)
        _poll()
        observed_final_identity = FileIdentity.from_stat(
            os.stat(final_name, dir_fd=parent, follow_symlinks=False)
        )
        if not _owns_inode(observed_final_identity, publisher_identity) or observed_final_identity.nlink != 1:
            raise ModelsCaptureProfileError("private publication unlink changed")
        final = os.open(final_name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
        try:
            if (
                not _owns_inode(FileIdentity.from_stat(os.fstat(final)), publisher_identity)
                or os.read(final, len(data) + 1) != data
            ):
                raise ModelsCaptureProfileError("private publication bytes changed")
        finally:
            os.close(final)
        return hashlib.sha256(data).hexdigest()
    except BaseException:
        if ACTIVE_SIGNAL_MODE:
            signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        _remove_owned(parent, final_name, publisher_identity)
        _remove_owned(parent, temporary_name or "", temporary_identity)
        os.fsync(parent)
        raise
    finally:
        os.close(parent)


def _rollback_profile_path(path: Optional[str], identity: Optional[FileIdentity]) -> None:
    if path is None or identity is None:
        return
    parent_path, name = os.path.split(path)
    if name != OUTPUT_BASENAME:
        raise ModelsCaptureProfileError("publication path changed")
    parent = _open_directory(parent_path, leaf_private=True)
    try:
        if _remove_owned(parent, name, identity):
            os.fsync(parent)
    finally:
        os.close(parent)


def prepare(data: bytes) -> dict[str, str]:
    global ACTIVE_OUTPUT_IDENTITY, ACTIVE_OUTPUT_PATH
    request = _canonical_request(data, REQUEST_KEYS, "prepare")
    profile, output_path = _profile_from_request(request)
    exact = _canonical_json(profile.as_mapping())
    if CaptureProfile.from_bytes(exact) != profile:
        raise ModelsCaptureProfileError("capture profile serialization changed")
    if ACTIVE_SIGNAL_MODE:
        ACTIVE_OUTPUT_PATH = output_path
        ACTIVE_OUTPUT_IDENTITY = None
    return {"profile_sha256": _publish_profile(output_path, exact), "status": "prepared"}


def validate(data: bytes) -> dict[str, str]:
    request = _canonical_request(data, VALIDATE_KEYS, "validate")
    profile_path = _require_absolute(request["profile_path"])
    parent_path, leaf = os.path.split(profile_path)
    if leaf != OUTPUT_BASENAME:
        raise ModelsCaptureProfileError("capture profile basename is fixed")
    parent = _open_directory(parent_path, leaf_private=True)
    try:
        descriptor = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
        try:
            value = os.fstat(descriptor)
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_uid != os.getuid()
                or stat.S_IMODE(value.st_mode) != 0o600
                or value.st_nlink != 1
                or value.st_size > PROFILE_LIMIT
            ):
                raise ModelsCaptureProfileError("capture profile file changed")
            raw = os.read(descriptor, PROFILE_LIMIT + 1)
            if len(raw) != value.st_size or len(raw) > PROFILE_LIMIT:
                raise ModelsCaptureProfileError("capture profile file changed")
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)
    profile = CaptureProfile.from_bytes(raw)
    current, expected_output_path = _profile_from_request(
        {
            "account_home": profile.account_home,
            "output_path": profile_path,
            "snapshot_path": profile.snapshot_path,
            "source_path": profile.source_path,
            "version_root": profile.version_root,
        }
    )
    if (
        expected_output_path != profile_path
        or current != profile
        or _canonical_json(profile.as_mapping()) != raw
    ):
        raise ModelsCaptureProfileError("capture profile bytes changed")
    return {"profile_sha256": hashlib.sha256(raw).hexdigest(), "status": "valid"}


def validate_source_contract(data: bytes) -> dict[str, object]:
    try:
        text = data.decode("utf-8", "strict")
        tree = ast.parse(text, filename="<models-capture-profile>", mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ModelsCaptureProfileError("profile builder source is invalid") from exc
    assignment = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "MODULE_AST_SHA256"
        ),
        None,
    )
    if assignment is None:
        raise ModelsCaptureProfileError("profile builder source authority missing")
    assignment.value = ast.Constant(value="PINNED-MODULE-AST")
    if hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest() != MODULE_AST_SHA256:
        raise ModelsCaptureProfileError("profile builder structure changed")
    module_imports = tuple(
        (alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    from_imports = tuple(
        (node.module, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )
    if module_imports != ALLOWED_MODULE_IMPORTS or from_imports != ALLOWED_FROM_IMPORTS:
        raise ModelsCaptureProfileError("profile builder import authority changed")

    def symbol(node: ast.AST) -> tuple[str, ...] | None:
        if isinstance(node, ast.Name):
            return (node.id,)
        if isinstance(node, ast.Attribute):
            parent = symbol(node.value)
            if parent is not None:
                return parent + (node.attr,)
        return None

    imported_roots = frozenset(name for name, _alias in ALLOWED_MODULE_IMPORTS)
    call_attributes = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    listdir_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {"__builtins__", "builtins", "importlib"}:
            raise ModelsCaptureProfileError("profile builder dynamic authority forbidden")
        if isinstance(node, ast.Call):
            target = symbol(node.func)
            if target is None:
                continue
            if len(target) == 1 and target[0] in FORBIDDEN_DIRECT_CALLS:
                raise ModelsCaptureProfileError("profile builder direct authority forbidden")
            if target[0] in imported_roots and target not in ALLOWED_IMPORTED_CALLS:
                raise ModelsCaptureProfileError("profile builder imported call authority changed")
            if target == ("os", "listdir"):
                listdir_calls += 1
                if ast.get_source_segment(text, node) != "os.listdir(descriptor)":
                    raise ModelsCaptureProfileError("profile builder directory enumeration changed")
        if isinstance(node, ast.Attribute):
            target = symbol(node)
            if target is None:
                continue
            if node.attr in {"__dict__", "__getattribute__"}:
                raise ModelsCaptureProfileError("profile builder dynamic authority forbidden")
            if any(target[:len(prefix)] == prefix for prefix in FORBIDDEN_SYMBOL_PREFIXES):
                raise ModelsCaptureProfileError("profile builder ambient authority forbidden")
            if target == ("os", "listdir") and id(node) not in call_attributes:
                raise ModelsCaptureProfileError("profile builder directory enumeration changed")
        if isinstance(node, ast.Subscript):
            target = symbol(node.value)
            if target is not None and target[0] in imported_roots:
                raise ModelsCaptureProfileError("profile builder imported lookup forbidden")
    if listdir_calls != 1:
        raise ModelsCaptureProfileError("profile builder directory enumeration changed")
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    if not {"prepare", "validate", "_publish_profile", "_profile_from_request"} <= set(functions):
        raise ModelsCaptureProfileError("profile builder surface changed")
    reachable = set()
    pending = ["prepare", "validate"]
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        for node in ast.walk(functions[name]):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in functions
            ):
                pending.append(node.func.id)
    if reachable != PRODUCTION_HELPER_NAMES:
        raise ModelsCaptureProfileError("profile builder production call graph changed")
    source = ast.get_source_segment(text, functions["_profile_from_request"]) or ""
    publish = ast.get_source_segment(text, functions["_publish_profile"]) or ""
    prepare_text = ast.get_source_segment(text, functions["prepare"]) or ""
    main_source = ast.get_source_segment(text, functions.get("main")) or ""
    validate_text = ast.get_source_segment(text, functions["validate"]) or ""
    account_source = ast.get_source_segment(text, functions.get("_open_directory")) or ""
    version_source = ast.get_source_segment(text, functions.get("_validate_version_evidence")) or ""
    evidence_read_source = ast.get_source_segment(text, functions.get("_read_at")) or ""
    profile_class = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CaptureProfile"), None
    )
    profile_bytes = ""
    if profile_class is not None:
        profile_bytes = ast.get_source_segment(
            text, next((node for node in profile_class.body if isinstance(node, ast.FunctionDef) and node.name == "from_bytes"), profile_class)
        ) or ""
    required_markers = (
        "_verify_regular(source_parent, source_fd, source_identity, source_leaf, EXPECTED_SOURCE_SHA256)",
        "_verify_regular(snapshot_parent, snapshot_fd, snapshot_identity, snapshot_leaf, EXPECTED_SOURCE_SHA256)",
    )
    if not (
        "_canonical_request(data, REQUEST_KEYS, \"prepare\")" in prepare_text
        and "_validate_version_evidence" in source
        and all(marker in source for marker in required_markers)
        and "if _canonical_json(profile.as_mapping()) != data:" in profile_bytes
        and "leaf.st_uid != os.getuid() or stat.S_IMODE(leaf.st_mode) != 0o700" in account_source
        and "or value.st_nlink != 1\n            or value.st_size > maximum" in evidence_read_source
        and "or stat.S_IMODE(value.st_mode) != 0o600\n            or value.st_nlink != 1" in evidence_read_source
        and "or value.st_uid != os.getuid()\n            or stat.S_IMODE(value.st_mode) != 0o600" in evidence_read_source
        and "if set(os.listdir(descriptor)) != VERSION_ROOT_FILES:" in version_source
        and "or observed.get(\"logical_argv\") != [source_path, \"--version\"]" in version_source
        and "or _read_at(descriptor, \"version.stdout\", 128) != b\"1.1.11\\n\"" in version_source
        and "or _read_at(descriptor, \"version.stderr\", 128) != b\"\"" in version_source
        and "current != profile" in validate_text
        and "os.O_EXCL" in publish
        and "os.link(" in publish
        and publish.count("os.fsync(parent)") == 3
        and "if list(argv) not in ([\"--prepare\"], [\"--validate\"]):" in main_source
    ):
        raise ModelsCaptureProfileError("profile builder authority changed")
    return {"byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _read_stdin(controller: SignalController) -> bytes:
    descriptor = sys.stdin.buffer.fileno()
    data = bytearray()
    while len(data) <= PROFILE_LIMIT:
        controller.poll()
        block = os.read(descriptor, min(64 * 1024, PROFILE_LIMIT + 1 - len(data)))
        controller.poll()
        if not block:
            break
        data.extend(block)
    if len(data) > PROFILE_LIMIT:
        raise ModelsCaptureProfileError("request is oversized")
    return bytes(data)


def _module_bytes() -> bytes:
    descriptor = os.open(__file__, os.O_RDONLY | CLOEXEC | NOFOLLOW)
    try:
        value = os.fstat(descriptor)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) & 0o022
            or value.st_nlink != 1
            or value.st_size > 256 * 1024
        ):
            raise ModelsCaptureProfileError("profile builder source changed")
        data = os.read(descriptor, value.st_size + 1)
        if len(data) != value.st_size:
            raise ModelsCaptureProfileError("profile builder source changed")
        return data
    finally:
        os.close(descriptor)


def main(argv: Sequence[str]) -> int:
    global ACTIVE_CONTROLLER, ACTIVE_FIRST_SIGNAL, ACTIVE_OUTPUT_IDENTITY, ACTIVE_OUTPUT_PATH, ACTIVE_SIGNAL_MODE
    if list(argv) not in (["--prepare"], ["--validate"]):
        print("models capture profile: invalid invocation", file=sys.stderr)
        return 64
    try:
        lifecycle = _acquire_lifecycle()
    except BaseException:
        _atomic_exit(2, sys.stderr.buffer.fileno(), b"models capture profile: rejected\n")
    ACTIVE_FIRST_SIGNAL = None
    ACTIVE_OUTPUT_IDENTITY = None
    ACTIVE_OUTPUT_PATH = None
    ACTIVE_SIGNAL_MODE = True
    ACTIVE_CONTROLLER = lifecycle.controller
    try:
        _activate_lifecycle(lifecycle)
        source = _module_bytes()
        lifecycle.controller.poll()
        validate_source_contract(source)
        lifecycle.controller.poll()
        request = _read_stdin(lifecycle.controller)
        result = prepare(request) if argv[0] == "--prepare" else validate(request)
        lifecycle.controller.poll()
        encoded = _canonical_json(result)
        sys.stdout.buffer.flush()
        _write_all(sys.stdout.buffer.fileno(), encoded, lifecycle.controller)
        sys.stdout.buffer.flush()
        signal.pthread_sigmask(signal.SIG_BLOCK, lifecycle.controller.owned)
        lifecycle.controller.merge_pending()
        lifecycle.controller.poll()
        os._exit(0)
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK, lifecycle.controller.owned)
        lifecycle.controller.merge_pending()
        cleanup_failed = False
        try:
            _rollback_profile_path(ACTIVE_OUTPUT_PATH, ACTIVE_OUTPUT_IDENTITY)
        except BaseException:
            cleanup_failed = True
        lifecycle.controller.merge_pending()
        selected = lifecycle.controller.choose()
        ACTIVE_SIGNAL_MODE = False
        ACTIVE_CONTROLLER = None
        ACTIVE_OUTPUT_IDENTITY = None
        ACTIVE_OUTPUT_PATH = None
        ACTIVE_FIRST_SIGNAL = None
        _atomic_exit(
            128 + selected if selected is not None else 2,
            sys.stderr.buffer.fileno(),
            b"models capture profile: interrupted\n"
            if selected is not None
            else b"models capture profile: rejected\n",
        )


if __name__ == "__main__":
    raise SystemExit(main(tuple(sys.argv)[1:]))
