#!/usr/bin/env python3
"""Fixed-profile, snapshot-backed agy version attestation.

The production interface accepts only ``--attest-version`` and a bounded strict
JSON evidence profile on stdin.  It always executes one logical
``[source_path, "--version"]`` call with ``executable=snapshot_path``.  The
offline interface accepts only ``--self-test`` and creates synthetic executables
inside its own private temporary root; it cannot read production evidence.
"""

from __future__ import annotations

import dataclasses
import ast
import hashlib
import json
import os
import pathlib
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence


sys.dont_write_bytecode = True

EXPECTED_VERSION = "1.1.11"
EXPECTED_STDOUT = b"1.1.11\n"
STREAM_LIMIT = 128
WALL_SECONDS = 3.0
PROFILE_LIMIT = 16_384
LIFECYCLE_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = getattr(os, "O_DIRECTORY", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)

PROFILE_KEYS = frozenset(
    {
        "prior_binding_sha256",
        "prior_root",
        "snapshot_identity",
        "snapshot_path",
        "source_identity",
        "source_path",
        "source_sha256",
        "temp_parent",
    }
)
IDENTITY_KEYS = frozenset(
    {"ctime_ns", "dev", "gid", "ino", "mode", "mtime_ns", "nlink", "size", "uid"}
)
PRIOR_FILES = frozenset(
    {
        "agy.snapshot",
        "cwd",
        "home",
        "snapshot.post.json",
        "snapshot.pre.json",
        "source.post.json",
        "source.pre.json",
        "tmp",
        "version.binding.json",
        "version.binding.sha256",
        "version.stderr",
        "version.stdout",
        "version.summary.json",
        "xdg-cache",
        "xdg-config",
        "xdg-state",
    }
)


class AttestationError(ValueError):
    """A fixed-profile attestation requirement failed closed."""


class AttestationInterrupted(BaseException):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


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
    def from_mapping(cls, value: object) -> "FileIdentity":
        if not isinstance(value, dict) or set(value) != IDENTITY_KEYS:
            raise AttestationError("invalid file identity")
        if any(type(value[key]) is not int or value[key] < 0 for key in IDENTITY_KEYS):
            raise AttestationError("invalid file identity")
        return cls(**value)

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

    def as_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class AttestationProfile:
    prior_binding_sha256: str
    prior_root: str
    snapshot_identity: FileIdentity
    snapshot_path: str
    source_identity: FileIdentity
    source_path: str
    source_sha256: str
    temp_parent: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "AttestationProfile":
        value = _strict_json(data)
        if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
            raise AttestationError("invalid evidence profile")
        for key in ("source_path", "snapshot_path", "prior_root", "temp_parent"):
            _require_canonical_absolute(value[key])
        for key in ("source_sha256", "prior_binding_sha256"):
            if not isinstance(value[key], str) or not _is_sha256(value[key]):
                raise AttestationError("invalid evidence profile")
        return cls(
            prior_binding_sha256=value["prior_binding_sha256"],
            prior_root=value["prior_root"],
            snapshot_identity=FileIdentity.from_mapping(value["snapshot_identity"]),
            snapshot_path=value["snapshot_path"],
            source_identity=FileIdentity.from_mapping(value["source_identity"]),
            source_path=value["source_path"],
            source_sha256=value["source_sha256"],
            temp_parent=value["temp_parent"],
        )


@dataclass(frozen=True)
class RunnerCalls:
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen
    fsync: Callable[[int], None] = os.fsync
    killpg: Callable[[int, int], None] = os.killpg


REAL_CALLS = RunnerCalls()


def validate_source_contract(data: bytes) -> dict[str, object]:
    """Validate fixed production-call structure without executing source bytes."""

    try:
        text = data.decode("utf-8", "strict")
        tree = ast.parse(text, filename="<version-attestation-runner>", mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise AttestationError("canonical runner source is invalid") from exc
    popen_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "calls"
        and node.func.attr == "popen"
    ]
    if len(popen_calls) != 1:
        raise AttestationError("canonical runner must own one Popen path")
    call = popen_calls[0]
    keywords = {item.arg: item.value for item in call.keywords if item.arg is not None}
    executable = keywords.get("executable")
    if not (
        len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "argv"
        and isinstance(executable, ast.Attribute)
        and isinstance(executable.value, ast.Name)
        and executable.value.id == "profile"
        and executable.attr == "snapshot_path"
    ):
        raise AttestationError("canonical runner lost its snapshot execution binding")
    argv_assignments = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "argv" for target in node.targets)
    ]
    expected = ast.List(
        elts=[
            ast.Attribute(
                value=ast.Name(id="profile", ctx=ast.Load()),
                attr="source_path",
                ctx=ast.Load(),
            ),
            ast.Constant(value="--version"),
        ],
        ctx=ast.Load(),
    )
    if (
        len(argv_assignments) != 1
        or ast.dump(argv_assignments[0], include_attributes=False)
        != ast.dump(expected, include_attributes=False)
    ):
        raise AttestationError("canonical runner lost its fixed logical argv")
    if (
        text.count("signal.signal(item, signal.SIG_IGN)") != 4
        or text.count("ignore_until_unblocked = True") != 4
    ):
        raise AttestationError("canonical runner lost its terminal signal disarm")
    functions = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    run_node = functions.get("run_attestation")
    publish_node = functions.get("publish")
    rollback_node = functions.get("rollback")
    main_node = functions.get("main")
    if not all((run_node, publish_node, rollback_node, main_node)):
        raise AttestationError("canonical runner lost a fixed lifecycle function")

    def named_calls(node: ast.AST, name: str) -> list[ast.Call]:
        return [
            item
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and (
                (isinstance(item.func, ast.Name) and item.func.id == name)
                or (isinstance(item.func, ast.Attribute) and item.func.attr == name)
            )
        ]

    def blocks_lifecycle(call: ast.Call) -> bool:
        return (
            len(call.args) == 2
            and isinstance(call.args[0], ast.Attribute)
            and call.args[0].attr == "SIG_BLOCK"
            and isinstance(call.args[1], ast.Name)
            and call.args[1].id == "LIFECYCLE_SIGNALS"
        )

    early_mask = named_calls(run_node, "pthread_sigmask")
    validations = named_calls(run_node, "_validate_prior")
    roots = named_calls(run_node, "mkdtemp")
    if (
        not any(blocks_lifecycle(call) for call in early_mask)
        or not validations
        or not roots
        or min(call.lineno for call in early_mask if blocks_lifecycle(call))
        > min(validations[0].lineno, roots[0].lineno)
    ):
        raise AttestationError("canonical runner lost full-lifecycle signal coverage")
    for node in (publish_node, rollback_node):
        if not any(
            blocks_lifecycle(call) for call in named_calls(node, "pthread_sigmask")
        ):
            raise AttestationError("canonical publication lost signal-masked cleanup")
    group_closes = named_calls(run_node, "_close_reserved_group")
    process_disarms = [
        item
        for item in ast.walk(run_node)
        if isinstance(item, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "process_active"
            for target in item.targets
        )
        and isinstance(item.value, ast.Constant)
        and item.value.value is False
    ]
    if (
        len(group_closes) != 1
        or not any(item.lineno > group_closes[0].lineno for item in process_disarms)
    ):
        raise AttestationError("canonical runner lost pre-reap group closure")
    for cleanup_name in ("_close_reserved_group", "_terminate_group"):
        cleanup = functions.get(cleanup_name)
        if cleanup is None:
            raise AttestationError("canonical runner lost reserved group cleanup")
        waits = named_calls(cleanup, "wait")
        group_calls = named_calls(cleanup, "killpg") + named_calls(cleanup, "_group_exists")
        if (
            len(waits) != 1
            or not group_calls
            or any(call.lineno > waits[0].lineno for call in group_calls)
        ):
            raise AttestationError("canonical runner uses group authority after reap")
    if len(named_calls(main_node, "_production_startup_isolated")) != 1:
        raise AttestationError("canonical runner lost isolated startup enforcement")
    return {
        "byte_count": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "status": "accepted",
    }


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _strict_json(data: bytes) -> object:
    if not data or len(data) > PROFILE_LIMIT or data.endswith(b"\n\n"):
        raise AttestationError("invalid JSON evidence")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise AttestationError("duplicate JSON key")
            result[key] = value
        return result

    try:
        text = data.decode("utf-8", "strict")
        if any(ord(char) < 0x20 and char not in "\n\t" for char in text):
            raise AttestationError("invalid JSON evidence")
        return json.loads(text, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttestationError("invalid JSON evidence") from exc


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"


def _require_canonical_absolute(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
        or os.path.realpath(value) != value
    ):
        raise AttestationError("path is not canonical and absolute")
    return value


def _open_dir(path: str) -> int:
    _require_canonical_absolute(path)
    descriptor = os.open("/", os.O_RDONLY | DIRECTORY | CLOEXEC)
    try:
        for part in pathlib.PurePosixPath(path).parts[1:]:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _hash_fd(descriptor: int, size: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = os.read(descriptor, min(remaining, 1024 * 1024))
        if not block:
            raise AttestationError("file ended before its attested size")
        digest.update(block)
        remaining -= len(block)
    if os.read(descriptor, 1) != b"":
        raise AttestationError("file exceeded its attested size")
    return digest.hexdigest()


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise AttestationError("artifact write failed")
        remaining = remaining[written:]


def _exact_unlink(parent_fd: int, name: str, inode: int) -> None:
    try:
        value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.getuid()
        and value.st_ino == inode
    ):
        os.unlink(name, dir_fd=parent_fd)


class Publisher:
    """Owner-private no-overwrite publisher with inode-pinned rollback."""

    def __init__(self, root: Path, calls: RunnerCalls = REAL_CALLS) -> None:
        self.root = root
        self.calls = calls
        self.root_fd = _open_dir(str(root))
        root_stat = os.fstat(self.root_fd)
        if root_stat.st_uid != os.getuid() or stat.S_IMODE(root_stat.st_mode) != 0o700:
            raise AttestationError("artifact root is not owner-private")
        self.owned: dict[str, int] = {}

    def close(self) -> None:
        os.close(self.root_fd)

    def rollback(self) -> None:
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        try:
            failure: Optional[BaseException] = None
            for name, inode in tuple(self.owned.items()):
                try:
                    _exact_unlink(self.root_fd, name, inode)
                    self.owned.pop(name, None)
                except BaseException as exc:
                    if failure is None:
                        failure = exc
            try:
                self.calls.fsync(self.root_fd)
            except BaseException as exc:
                if failure is None:
                    failure = exc
            if failure is not None:
                raise failure
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)

    def publish(self, name: str, data: bytes) -> str:
        if not name or "/" in name or name in (".", ".."):
            raise AttestationError("invalid artifact name")
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        temporary = f".{name}.{secrets.token_hex(12)}.tmp"
        descriptor = -1
        inode: Optional[int] = None
        linked = False
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | CLOEXEC | NOFOLLOW,
                0o600,
                dir_fd=self.root_fd,
            )
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, data)
            self.calls.fsync(descriptor)
            staged = os.fstat(descriptor)
            inode = staged.st_ino
            os.close(descriptor)
            descriptor = -1
            try:
                os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise AttestationError("artifact target exists")
            os.link(
                temporary,
                name,
                src_dir_fd=self.root_fd,
                dst_dir_fd=self.root_fd,
                follow_symlinks=False,
            )
            linked = True
            final = os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            if (
                final.st_ino != inode
                or final.st_uid != os.getuid()
                or stat.S_IMODE(final.st_mode) != 0o600
            ):
                raise AttestationError("artifact identity changed")
            self.owned[name] = inode
            self.calls.fsync(self.root_fd)
            os.unlink(temporary, dir_fd=self.root_fd)
            self.calls.fsync(self.root_fd)
            return hashlib.sha256(data).hexdigest()
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            if linked and inode is not None:
                _exact_unlink(self.root_fd, name, inode)
                self.owned.pop(name, None)
                self.calls.fsync(self.root_fd)
            raise
        finally:
            try:
                removed = False
                try:
                    os.unlink(temporary, dir_fd=self.root_fd)
                    removed = True
                except FileNotFoundError:
                    pass
                if removed:
                    self.calls.fsync(self.root_fd)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, blocked)


def _read_at(parent_fd: int, name: str, cap: int) -> bytes:
    value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_size > cap
    ):
        raise AttestationError("private evidence file is invalid")
    descriptor = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
    try:
        data = os.read(descriptor, cap + 1)
        if len(data) != value.st_size or os.read(descriptor, 1) != b"":
            raise AttestationError("private evidence file changed")
        return data
    finally:
        os.close(descriptor)


def _validate_profile_authority(profile: AttestationProfile) -> None:
    if os.path.dirname(profile.prior_root) != profile.temp_parent:
        raise AttestationError("prior evidence root is not bound to the private parent")
    if not os.path.basename(profile.prior_root).startswith("agy-version-attestation."):
        raise AttestationError("prior evidence root has an invalid name")
    expected_snapshot = os.path.join(profile.prior_root, "agy.snapshot")
    if profile.snapshot_path != expected_snapshot:
        raise AttestationError("snapshot is not bound to the prior evidence root")
    repository = str(Path(__file__).resolve(strict=True).parents[1])
    if os.path.commonpath((profile.temp_parent, repository)) == repository:
        raise AttestationError("private evidence parent is inside the repository")
    parent = _open_dir(profile.temp_parent)
    try:
        observed = os.fstat(parent)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or observed.st_uid != os.getuid()
            or stat.S_IMODE(observed.st_mode) != 0o700
        ):
            raise AttestationError("private evidence parent is not owner-private")
    finally:
        os.close(parent)
    for identity, expected_mode, label in (
        (profile.source_identity, 0o755, "source"),
        (profile.snapshot_identity, 0o500, "snapshot"),
    ):
        if (
            identity.uid != os.getuid()
            or identity.mode != expected_mode
            or identity.nlink != 1
            or identity.size <= 0
        ):
            raise AttestationError(f"{label} executable policy is invalid")


def _validate_prior(profile: AttestationProfile) -> None:
    _validate_profile_authority(profile)
    descriptor = _open_dir(profile.prior_root)
    try:
        value = os.fstat(descriptor)
        if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700:
            raise AttestationError("prior evidence root is not owner-private")
        if set(os.listdir(descriptor)) != PRIOR_FILES:
            raise AttestationError("prior evidence root has an unexpected shape")
        for name in ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"):
            child = os.open(
                name,
                os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW,
                dir_fd=descriptor,
            )
            try:
                child_stat = os.fstat(child)
                if (
                    not stat.S_ISDIR(child_stat.st_mode)
                    or child_stat.st_uid != os.getuid()
                    or stat.S_IMODE(child_stat.st_mode) != 0o700
                    or os.listdir(child)
                ):
                    raise AttestationError("prior evidence directory is invalid")
            finally:
                os.close(child)
        binding = _read_at(descriptor, "version.binding.json", PROFILE_LIMIT)
        digest = _read_at(descriptor, "version.binding.sha256", 128)
        if (
            hashlib.sha256(binding).hexdigest() != profile.prior_binding_sha256
            or digest != (profile.prior_binding_sha256 + "\n").encode("ascii")
        ):
            raise AttestationError("prior binding digest changed")
        parsed = _strict_json(binding)
        if (
            not isinstance(parsed, dict)
            or parsed.get("claim") != "snapshot-version-only"
            or not isinstance(parsed.get("inventory"), dict)
            or parsed["inventory"].get("executable_version_bound") is not False
            or not isinstance(parsed.get("snapshot"), dict)
            or parsed["snapshot"].get("pre") != profile.snapshot_identity.as_dict()
            or parsed["snapshot"].get("sha256") != profile.source_sha256
            or not isinstance(parsed.get("source"), dict)
            or parsed["source"].get("pre") != profile.source_identity.as_dict()
            or parsed["source"].get("sha256") != profile.source_sha256
            or not isinstance(parsed.get("version"), dict)
            or parsed["version"].get("logical_argv")
            != [profile.source_path, "--version"]
        ):
            raise AttestationError("prior binding claim is incompatible")
        for name in (
            "snapshot.post.json",
            "snapshot.pre.json",
            "source.post.json",
            "source.pre.json",
            "version.stderr",
            "version.stdout",
            "version.summary.json",
        ):
            _read_at(descriptor, name, PROFILE_LIMIT)
    finally:
        os.close(descriptor)


def _open_attested(
    path: str, identity: FileIdentity, sha256: str, expected_mode: int
) -> tuple[int, int]:
    parent = _open_dir(os.path.dirname(path))
    leaf = os.path.basename(path)
    descriptor = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
    raw = os.fstat(descriptor)
    observed = FileIdentity.from_stat(raw)
    if (
        not stat.S_ISREG(raw.st_mode)
        or raw.st_uid != os.getuid()
        or stat.S_IMODE(raw.st_mode) != expected_mode
        or raw.st_nlink != 1
        or observed != identity
        or _hash_fd(descriptor, identity.size) != sha256
        or FileIdentity.from_stat(os.fstat(descriptor)) != identity
    ):
        os.close(descriptor)
        os.close(parent)
        raise AttestationError("attested executable identity changed")
    return parent, descriptor


def _verify_attested_path(
    parent: int,
    path: str,
    held: int,
    identity: FileIdentity,
    sha256: str,
) -> FileIdentity:
    leaf = os.path.basename(path)
    held_identity = FileIdentity.from_stat(os.fstat(held))
    path_identity = FileIdentity.from_stat(
        os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    )
    reopened = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
    try:
        reopened_identity = FileIdentity.from_stat(os.fstat(reopened))
        reopened_sha = _hash_fd(reopened, identity.size)
    finally:
        os.close(reopened)
    if (
        held_identity != identity
        or path_identity != identity
        or reopened_identity != identity
        or _hash_fd(held, identity.size) != sha256
        or reopened_sha != sha256
    ):
        raise AttestationError("attested executable path changed")
    return held_identity


def _group_exists(pgid: int, calls: RunnerCalls) -> bool:
    try:
        calls.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_group(process: subprocess.Popen[bytes], calls: RunnerCalls) -> None:
    pgid = process.pid
    if process.returncode is not None:
        raise AttestationError("reaped process cannot authorize group cleanup")
    try:
        calls.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + 0.25
    while _group_exists(pgid, calls) and time.monotonic() < deadline:
        time.sleep(0.01)
    if _group_exists(pgid, calls):
        try:
            calls.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=0.75)
    except subprocess.TimeoutExpired as exc:
        raise AttestationError("version process could not be reaped") from exc


def _close_reserved_group(
    process: subprocess.Popen[bytes], calls: RunnerCalls
) -> int:
    """Close the exact reserved group before reaping its leader."""

    pgid = process.pid
    try:
        calls.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    grace = time.monotonic() + 0.25
    while time.monotonic() < grace:
        time.sleep(0.01)
    try:
        calls.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        return process.wait(timeout=0.75)
    except subprocess.TimeoutExpired as exc:
        raise AttestationError("version process could not be reaped") from exc


def _capture(
    process: subprocess.Popen[bytes], deadline: float
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise AttestationError("version process did not expose bounded streams")
    stdout_descriptor = process.stdout.fileno()
    stderr_descriptor = process.stderr.fileno()
    buffers = {
        stdout_descriptor: (process.stdout, bytearray()),
        stderr_descriptor: (process.stderr, bytearray()),
    }
    for descriptor in buffers:
        os.set_blocking(descriptor, False)
    with selectors.DefaultSelector() as selector:
        for descriptor in buffers:
            selector.register(descriptor, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AttestationError("version process timed out")
            for key, _mask in selector.select(min(remaining, 0.05)):
                stream, captured = buffers[key.fd]
                block = os.read(key.fd, min(64, STREAM_LIMIT + 1 - len(captured)))
                if not block:
                    selector.unregister(key.fd)
                    stream.close()
                    continue
                captured.extend(block)
                if len(captured) > STREAM_LIMIT:
                    raise AttestationError("version output exceeded its bound")
    return bytes(buffers[stdout_descriptor][1]), bytes(buffers[stderr_descriptor][1])


def _module_source() -> bytes:
    path = Path(__file__).resolve(strict=True)
    data = path.read_bytes()
    if not data or len(data) > 128 * 1024 or b"\x00" in data:
        raise AttestationError("canonical runner source is invalid")
    return data


def run_attestation(
    profile: AttestationProfile,
    *,
    calls: RunnerCalls = REAL_CALLS,
    module_source: Optional[bytes] = None,
) -> dict[str, object]:
    """Run one fixed version observation and publish its private binding."""

    if not all(hasattr(signal, name) for name in ("pthread_sigmask", "sigpending", "sigwait")):
        raise AttestationError("required signal primitives are unavailable")
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
    old_handlers = {item: signal.getsignal(item) for item in LIFECYCLE_SIGNALS}
    source_bytes: bytes
    source_contract: dict[str, object]
    root: Optional[Path] = None
    publisher: Optional[Publisher] = None
    source_parent = source_fd = snapshot_parent = snapshot_fd = None
    process: Optional[subprocess.Popen[bytes]] = None
    process_active = False
    disarmed = False
    ignore_until_unblocked = False

    def interrupted(signum: int, _frame: object) -> None:
        raise AttestationInterrupted(signum)

    for item in LIFECYCLE_SIGNALS:
        signal.signal(item, interrupted)
    signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
    try:
        source_bytes = _module_source() if module_source is None else module_source
        source_contract = validate_source_contract(source_bytes)
        _validate_prior(profile)
        temp_parent_fd = _open_dir(profile.temp_parent)
        os.close(temp_parent_fd)
        root = Path(tempfile.mkdtemp(prefix="agy-version-recovery.", dir=profile.temp_parent))
        os.chmod(root, 0o700)
        publisher = Publisher(root, calls)
        for name in ("cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"):
            (root / name).mkdir(mode=0o700)
        runner_sha = publisher.publish("runner.py", source_bytes)
        publisher.publish("runner.py.sha256", (runner_sha + "\n").encode("ascii"))
        if runner_sha != source_contract["sha256"]:
            raise AttestationError("persisted runner source digest changed")
        source_parent, source_fd = _open_attested(
            profile.source_path,
            profile.source_identity,
            profile.source_sha256,
            0o755,
        )
        snapshot_parent, snapshot_fd = _open_attested(
            profile.snapshot_path,
            profile.snapshot_identity,
            profile.source_sha256,
            0o500,
        )
        argv = [profile.source_path, "--version"]
        environment = {
            "HOME": str(root / "home"),
            "TMPDIR": str(root / "tmp"),
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "XDG_CACHE_HOME": str(root / "xdg-cache"),
            "XDG_STATE_HOME": str(root / "xdg-state"),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PATH": "/usr/bin:/bin",
        }
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        try:
            process = calls.popen(
                argv,
                executable=profile.snapshot_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(root / "cwd"),
                env=environment,
                start_new_session=True,
            )
            process_active = True
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        started = time.monotonic()
        deadline = started + WALL_SECONDS
        stdout, stderr = _capture(process, deadline)
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        try:
            exit_code = _close_reserved_group(process, calls)
            process_active = False
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        if exit_code != 0 or stdout != EXPECTED_STDOUT or stderr != b"":
            raise AttestationError("version result did not match the fixed contract")
        source_post = _verify_attested_path(
            source_parent,
            profile.source_path,
            source_fd,
            profile.source_identity,
            profile.source_sha256,
        )
        snapshot_post = _verify_attested_path(
            snapshot_parent,
            profile.snapshot_path,
            snapshot_fd,
            profile.snapshot_identity,
            profile.source_sha256,
        )
        stdout_sha = publisher.publish("version.stdout", stdout)
        stderr_sha = publisher.publish("version.stderr", stderr)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logical_sha = hashlib.sha256(
            json.dumps(argv, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        summary = {
            "call_count": 1,
            "child_exit": exit_code,
            "claim": "snapshot-version-recovery",
            "elapsed_ms": elapsed_ms,
            "logical_argv_sha256": logical_sha,
            "schema_version": 1,
            "status": "accepted",
            "stderr_bytes": 0,
            "stdout_bytes": len(stdout),
            "timeout": False,
        }
        summary_sha = publisher.publish("version.summary.json", _canonical_json(summary))
        binding = {
            "artifacts": {
                "runner.py": runner_sha,
                "version.stderr": stderr_sha,
                "version.stdout": stdout_sha,
                "version.summary.json": summary_sha,
            },
            "claim": "snapshot-version-recovery",
            "limitations": {
                "metadata_advance_authorized": False,
                "network_absence_os_enforced": False,
                "prior_inventory_executable_version_bound": False,
                "provider_backend_proven": False,
            },
            "prior": {"binding_sha256": profile.prior_binding_sha256, "root_mutated": False},
            "runner": {"byte_count": len(source_bytes), "sha256": runner_sha},
            "schema_version": 1,
            "snapshot": {
                "post": snapshot_post.as_dict(),
                "pre": profile.snapshot_identity.as_dict(),
                "sha256": profile.source_sha256,
            },
            "source": {
                "post": source_post.as_dict(),
                "pre": profile.source_identity.as_dict(),
                "sha256": profile.source_sha256,
            },
            "version": {
                "exit": exit_code,
                "expected": EXPECTED_VERSION,
                "logical_argv": argv,
                "logical_argv_sha256": logical_sha,
                "observed": EXPECTED_VERSION,
                "popen_count": 1,
                "stderr_limit": STREAM_LIMIT,
                "stdout_limit": STREAM_LIMIT,
                "timeout_seconds": WALL_SECONDS,
            },
        }
        binding_bytes = _canonical_json(binding)
        binding_sha = publisher.publish("version.binding.json", binding_bytes)
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        publisher.publish(
            "version.binding.sha256", (binding_sha + "\n").encode("ascii")
        )
        pending = set(signal.sigpending()).intersection(LIFECYCLE_SIGNALS)
        if pending:
            first = signal.sigwait(pending)
            publisher.rollback()
            raise AttestationInterrupted(first)
        for item in LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        pending = set(signal.sigpending()).intersection(LIFECYCLE_SIGNALS)
        if pending:
            first = signal.sigwait(pending)
            publisher.rollback()
            raise AttestationInterrupted(first)
        disarmed = True
        ignore_until_unblocked = True
        return {
            "artifact_root": str(root),
            "binding_sha256": binding_sha,
            "call_count": 1,
            "claim": "snapshot-version-recovery",
            "runner_sha256": runner_sha,
            "snapshot_sha256": profile.source_sha256,
            "status": "accepted",
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "stdout_sha256": hashlib.sha256(EXPECTED_STDOUT).hexdigest(),
        }
    except AttestationInterrupted as exc:
        signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        if process is not None and process_active:
            _terminate_group(process, calls)
        if publisher is not None:
            publisher.rollback()
        for item in LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        ignore_until_unblocked = True
        raise SystemExit(128 + exc.signum)
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        cleanup_failure: Optional[BaseException] = None
        if process is not None and process_active:
            try:
                _terminate_group(process, calls)
            except BaseException as exc:
                cleanup_failure = exc
        if publisher is not None:
            try:
                publisher.rollback()
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        for item in LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        ignore_until_unblocked = True
        if cleanup_failure is not None:
            raise cleanup_failure
        raise
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        for descriptor in (snapshot_fd, snapshot_parent, source_fd, source_parent):
            if descriptor is not None:
                os.close(descriptor)
        if publisher is not None:
            publisher.close()
        if ignore_until_unblocked:
            signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
            for item, handler in old_handlers.items():
                try:
                    signal.signal(item, handler)
                except BaseException:
                    pass
        elif not disarmed:
            for item, handler in old_handlers.items():
                try:
                    signal.signal(item, handler)
                except BaseException:
                    pass
            signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)


FAKE_EXECUTABLE = b"#!/usr/bin/python3\nimport sys\nsys.stdout.write('1.1.11\\n')\n"


def _identity(path: Path) -> FileIdentity:
    return FileIdentity.from_stat(path.stat())


def _offline_fixture(
    root: Path, executable_bytes: bytes = FAKE_EXECUTABLE
) -> AttestationProfile:
    source = root / "agy"
    source.write_bytes(executable_bytes)
    source.chmod(0o755)
    prior = root / "agy-version-attestation.synthetic"
    prior.mkdir(mode=0o700)
    snapshot = prior / "agy.snapshot"
    snapshot.write_bytes(executable_bytes)
    snapshot.chmod(0o500)
    for name in PRIOR_FILES:
        path = prior / name
        if name in {"cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"}:
            path.mkdir(mode=0o700)
        elif name != "agy.snapshot":
            path.write_bytes(b"")
            path.chmod(0o600)
    sha = hashlib.sha256(executable_bytes).hexdigest()
    binding_value = {
        "claim": "snapshot-version-only",
        "inventory": {"executable_version_bound": False},
        "snapshot": {"pre": _identity(snapshot).as_dict(), "sha256": sha},
        "source": {"pre": _identity(source).as_dict(), "sha256": sha},
        "version": {"logical_argv": [str(source), "--version"]},
    }
    (prior / "version.binding.json").write_bytes(_canonical_json(binding_value))
    (prior / "version.binding.json").chmod(0o600)
    binding = (prior / "version.binding.json").read_bytes()
    binding_sha = hashlib.sha256(binding).hexdigest()
    (prior / "version.binding.sha256").write_bytes((binding_sha + "\n").encode("ascii"))
    (prior / "version.binding.sha256").chmod(0o600)
    return AttestationProfile(
        prior_binding_sha256=binding_sha,
        prior_root=str(prior),
        snapshot_identity=_identity(snapshot),
        snapshot_path=str(snapshot),
        source_identity=_identity(source),
        source_path=str(source),
        source_sha256=sha,
        temp_parent=str(root),
    )


def run_offline_self_test() -> dict[str, object]:
    """Exercise the exact production function using synthetic local evidence."""

    root = Path(tempfile.mkdtemp(prefix="agy-version-runner-selftest.")).resolve()
    os.chmod(root, 0o700)
    artifact_root: Optional[Path] = None
    try:
        profile = _offline_fixture(root)
        result = run_attestation(profile, module_source=_module_source())
        artifact_root = Path(str(result["artifact_root"]))
        if result.get("status") != "accepted" or result.get("call_count") != 1:
            raise AttestationError("offline production-path self-test failed")
        return {
            "call_count": 1,
            "claim": "synthetic-version-attestation",
            "schema_version": 1,
            "status": "accepted",
        }
    finally:
        if artifact_root is not None and artifact_root.exists():
            shutil.rmtree(artifact_root)
        shutil.rmtree(root)


def _production_startup_isolated() -> bool:
    return (
        sys.executable
        in {
            "/usr/bin/python3",
            "/Library/Developer/CommandLineTools/usr/bin/python3",
        }
        and sys.flags.isolated == 1
        and sys.flags.no_site == 1
        and sys.flags.dont_write_bytecode == 1
    )


def main(argv: Sequence[str]) -> int:
    if list(argv) == ["--self-test"]:
        try:
            result = run_offline_self_test()
        except (AttestationError, OSError, subprocess.SubprocessError):
            print("version attestation runner: rejected", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if list(argv) != ["--attest-version"]:
        print("version attestation runner: invalid invocation", file=sys.stderr)
        return 64
    if not _production_startup_isolated():
        print("version attestation runner: isolated startup required", file=sys.stderr)
        return 64
    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)
    try:
        profile = AttestationProfile.from_bytes(data)
        result = run_attestation(profile)
    except (AttestationError, OSError, subprocess.SubprocessError):
        print("version attestation runner: rejected", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
