#!/usr/bin/env python3
"""Create one fresh ``snapshot-version-only`` recovery input from current bytes.

This repository-only bridge is intentionally separate from
``version_bootstrap_runner.py``.  It never reads a retained recovery record: an
operator supplies one canonical current-source identity and one new
owner-private output root.  The command holds the supplied source twice, copies each
held descriptor independently, performs exactly one snapshot-backed ``--version``
observation, and writes only the unchanged initial profile consumed by
``version_attestation_runner.py``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Optional, Sequence


RUNTIME_MAJOR = 3
RUNTIME_MINOR = 9
EXPECTED_VERSION = "1.1.12"
EXPECTED_STDOUT = b"1.1.12\n"
EXPECTED_SOURCE_SHA256 = "c8fd3c0016e101689f923f82da1c068b0e6dce3abcb0089e282742693ad4d344"
EXPECTED_SIZE = 172_267_536
PROFILE_LIMIT = 16_384
MODULE_LIMIT = 128 * 1024
MODULE_AST_SHA256 = "9c95548054f71498d90e357aa9a30c164c79297f198df05be5046c6a4c649864"
VERSION_RUNNER_BYTES = 69_242
VERSION_RUNNER_SHA256 = "0e2632c2de1dc2651693dce942429b3219d551eb5a979aa2d8d273ee0aa95d6b"
HISTORICAL_RECOVERY_BINDING_SHA256 = "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2"
HISTORICAL_RECOVERY_SOURCE_SHA256 = "198ff7c3f6d173daa510b0814aa70c6ce14c94035bcd4707a3c0e79fa38a7bc3"
SCRATCH_NAMES = ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state")
INITIAL_KEYS = frozenset({"bootstrap_root", "expected_version", "source_identity", "source_path", "source_sha256"})


def _runtime_contract(
    implementation: object, major: object, minor: object, isolated: object,
    no_site: object, dont_write_bytecode: object, ignore_environment: object,
) -> bool:
    return (
        type(implementation) is str and implementation == "cpython"
        and type(major) is int and major == RUNTIME_MAJOR
        and type(minor) is int and minor == RUNTIME_MINOR
        and type(isolated) is int and isolated == 1
        and type(no_site) is int and no_site == 1
        and type(dont_write_bytecode) is int and dont_write_bytecode == 1
        and type(ignore_environment) is int and ignore_environment == 1
    )


def _runtime_supported() -> bool:
    return _runtime_contract(
        sys.implementation.name, sys.version_info.major, sys.version_info.minor,
        sys.flags.isolated, sys.flags.no_site, sys.flags.dont_write_bytecode,
        sys.flags.ignore_environment,
    )


sys.dont_write_bytecode = True
SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))
import version_attestation_runner as version


class InitialBootstrapError(ValueError):
    """The closed initial-bootstrap contract rejected supplied authority."""


class InitialBootstrapInterrupted(SystemExit):
    def __init__(self, signum: int) -> None:
        super().__init__(128 + signum)
        self.signum = signum


class SignalController:
    """Non-throwing owned-signal latch; checkpoints use HUP/INT/TERM priority."""

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
            self.selected = next((item for item in self.owned if item in self.observed), None)
        return self.selected

    def poll(self) -> None:
        selected = self.choose()
        if selected is not None:
            raise InitialBootstrapInterrupted(selected)


@dataclass
class LifecycleState:
    controller: SignalController
    entry_mask: set[signal.Signals]
    old_handlers: dict[signal.Signals, object]
    installed_handlers: list[signal.Signals]


def _acquire_lifecycle() -> LifecycleState:
    if not all(hasattr(signal, item) for item in ("pthread_sigmask", "sigpending", "sigwait")):
        raise InitialBootstrapError("required signal primitives are unavailable")
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
    old_handlers = {item: signal.getsignal(item) for item in version.LIFECYCLE_SIGNALS}
    owned = tuple(item for item in version.LIFECYCLE_SIGNALS if item not in entry_mask and old_handlers[item] is not signal.SIG_IGN)
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


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _inside(path: str, parent: str) -> bool:
    return os.path.commonpath((path, parent)) == parent


@dataclass(frozen=True)
class InitialProfile:
    bootstrap_root: str
    expected_version: str
    source_identity: version.FileIdentity
    source_path: str
    source_sha256: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "InitialProfile":
        value = version._strict_json(data)
        if not isinstance(value, dict) or set(value) != INITIAL_KEYS or _canonical_json(value) != data:
            raise InitialBootstrapError("invalid initial bootstrap profile")
        version._require_canonical_absolute(value["source_path"])
        root = value["bootstrap_root"]
        if not isinstance(root, str) or not os.path.isabs(root) or os.path.normpath(root) != root or os.path.lexists(root):
            raise InitialBootstrapError("bootstrap root must be a new canonical path")
        if os.path.realpath(os.path.dirname(root)) != os.path.dirname(root):
            raise InitialBootstrapError("bootstrap parent is not canonical")
        try:
            identity = version.FileIdentity.from_mapping(value["source_identity"])
        except version.AttestationError as exc:
            raise InitialBootstrapError("source identity is invalid") from exc
        if value["expected_version"] != EXPECTED_VERSION or value["source_sha256"] != EXPECTED_SOURCE_SHA256:
            raise InitialBootstrapError("initial source expectation changed")
        return cls(root, value["expected_version"], identity, value["source_path"], value["source_sha256"])


@dataclass(frozen=True)
class OwnedIdentity:
    ctime_ns: int
    dev: int
    gid: int
    ino: int
    mode: int
    mtime_ns: int
    nlink: int
    size: int
    uid: int
    kind: str

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "OwnedIdentity":
        kind = "directory" if stat.S_ISDIR(value.st_mode) else "regular" if stat.S_ISREG(value.st_mode) else "other"
        return cls(value.st_ctime_ns, value.st_dev, value.st_gid, value.st_ino, stat.S_IMODE(value.st_mode), value.st_mtime_ns, value.st_nlink, value.st_size, value.st_uid, kind)

    def with_nlink(self, nlink: int) -> "OwnedIdentity":
        return dataclasses.replace(self, nlink=nlink)

    def matches(self, observed: "OwnedIdentity") -> bool:
        """Directory link counts evolve with owned children; regular files may not."""
        if self.kind == "directory":
            return (
                observed.kind == "directory" and self.dev == observed.dev
                and self.gid == observed.gid and self.ino == observed.ino and self.mode == observed.mode
                and self.uid == observed.uid
            )
        return self == observed

    def same_object(self, observed: "OwnedIdentity") -> bool:
        return (self.dev, self.gid, self.ino, self.mode, self.nlink, self.uid, self.kind) == (observed.dev, observed.gid, observed.ino, observed.mode, observed.nlink, observed.uid, observed.kind)

    def same_node(self, observed: "OwnedIdentity") -> bool:
        return (self.dev, self.gid, self.ino, self.mode, self.uid, self.kind) == (observed.dev, observed.gid, observed.ino, observed.mode, observed.uid, observed.kind)


class Ledger:
    """A fixed-name, descriptor-relative ledger for this one newly-created root."""

    def __init__(self, parent_fd: int, root_fd: int, root_name: str, root: OwnedIdentity) -> None:
        self.parent_fd, self.root_fd, self.root_name = parent_fd, root_fd, root_name
        self.paths: dict[tuple[str, ...], OwnedIdentity] = {(): root}
        self.contents: dict[tuple[str, ...], tuple[int, str]] = {}

    @classmethod
    def create(cls, parent_path: str, root_name: str) -> "Ledger":
        parent_fd = version._open_dir(parent_path)
        root_fd = -1
        try:
            parent = os.fstat(parent_fd)
            if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
                raise InitialBootstrapError("bootstrap parent is not owner-private")
            os.mkdir(root_name, 0o700, dir_fd=parent_fd)
            root_fd = os.open(root_name, os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW, dir_fd=parent_fd)
            root = OwnedIdentity.from_stat(os.fstat(root_fd))
            if root.kind != "directory" or root.uid != os.getuid() or root.mode != 0o700 or root.nlink != 2:
                os.close(root_fd)
                raise InitialBootstrapError("bootstrap root identity changed")
            os.fsync(root_fd)
            os.fsync(parent_fd)
            return cls(parent_fd, root_fd, root_name, root)
        except BaseException:
            if root_fd >= 0:
                os.close(root_fd)
            os.close(parent_fd)
            raise

    def _fd(self, path: tuple[str, ...]) -> int:
        fd = os.dup(self.root_fd)
        try:
            for name in path:
                next_fd = os.open(name, os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            observed = OwnedIdentity.from_stat(os.fstat(fd))
            expected = self.paths.get(path)
            if expected is None or not expected.matches(observed):
                raise InitialBootstrapError("owned directory identity changed")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def mkdir(self, parent: tuple[str, ...], name: str) -> OwnedIdentity:
        if not name or "/" in name or (parent + (name,)) in self.paths:
            raise InitialBootstrapError("invalid owned directory")
        fd = self._fd(parent)
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
            identity = OwnedIdentity.from_stat(os.stat(name, dir_fd=fd, follow_symlinks=False))
            if identity.kind != "directory" or identity.uid != os.getuid() or identity.mode != 0o700:
                raise InitialBootstrapError("owned directory changed")
            self.paths[parent + (name,)] = identity
            return identity
        finally:
            os.close(fd)

    def record(self, path: tuple[str, ...], identity: OwnedIdentity, size: int, sha256: str) -> None:
        if identity.kind != "regular" or identity.uid != os.getuid() or identity.nlink != 1:
            raise InitialBootstrapError("owned file changed")
        self.paths[path] = identity
        self.contents[path] = (size, sha256)

    def verify_file(self, path: tuple[str, ...], expected: version.FileIdentity, mode: int, controller: SignalController) -> version.FileIdentity:
        parent_fd = self._fd(path[:-1])
        descriptor = -1
        try:
            name = path[-1]
            before = OwnedIdentity.from_stat(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
            if not self.paths[path].matches(before):
                raise InitialBootstrapError("owned executable path identity changed")
            descriptor = os.open(name, os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW, dir_fd=parent_fd)
            observed = version.FileIdentity.from_stat(os.fstat(descriptor))
            size, sha256 = self.contents[path]
            if observed != expected or observed.mode != mode or observed.nlink != 1 or observed.size != size or version._hash_fd(descriptor, size, controller) != sha256 or version.FileIdentity.from_stat(os.fstat(descriptor)) != expected:
                raise InitialBootstrapError("owned executable bytes changed")
            after = OwnedIdentity.from_stat(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
            if not self.paths[path].matches(after):
                raise InitialBootstrapError("owned executable path identity changed")
            return observed
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_fd)

    def inventory(self, path: tuple[str, ...], allowed: frozenset[str]) -> None:
        descriptor = self._fd(path)
        try:
            if set(os.listdir(descriptor)) != allowed:
                raise InitialBootstrapError("owned directory shape changed")
        finally:
            os.close(descriptor)

    def validate(self, controller: SignalController) -> None:
        if not self.paths[()].matches(OwnedIdentity.from_stat(os.fstat(self.root_fd))):
            raise InitialBootstrapError("bootstrap root identity changed")
        for path, expected in self.paths.items():
            if not path:
                continue
            parent_fd = self._fd(path[:-1])
            try:
                observed = OwnedIdentity.from_stat(os.stat(path[-1], dir_fd=parent_fd, follow_symlinks=False))
                if not expected.matches(observed):
                    raise InitialBootstrapError("owned artifact identity changed")
            finally:
                os.close(parent_fd)
        for path, (size, sha256) in self.contents.items():
            parent_fd = self._fd(path[:-1])
            descriptor = -1
            try:
                before = OwnedIdentity.from_stat(os.stat(path[-1], dir_fd=parent_fd, follow_symlinks=False))
                if not self.paths[path].matches(before):
                    raise InitialBootstrapError("owned content identity changed")
                descriptor = os.open(path[-1], os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW, dir_fd=parent_fd)
                if version._hash_fd(descriptor, size, controller) != sha256:
                    raise InitialBootstrapError("owned content hash changed")
                after = OwnedIdentity.from_stat(os.stat(path[-1], dir_fd=parent_fd, follow_symlinks=False))
                if not self.paths[path].matches(after):
                    raise InitialBootstrapError("owned content identity changed")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                os.close(parent_fd)

    def rollback(self) -> bool:
        ok = True
        for path in sorted((item for item in self.paths if item), key=len, reverse=True):
            parent_fd = -1
            try:
                parent_fd = self._fd(path[:-1])
                observed = OwnedIdentity.from_stat(os.stat(path[-1], dir_fd=parent_fd, follow_symlinks=False))
                if not self.paths[path].matches(observed):
                    ok = False
                    continue
                if observed.kind == "directory":
                    os.rmdir(path[-1], dir_fd=parent_fd)
                else:
                    os.unlink(path[-1], dir_fd=parent_fd)
            except (FileNotFoundError, OSError, InitialBootstrapError):
                ok = False
            finally:
                if parent_fd >= 0:
                    os.close(parent_fd)
        try:
            os.close(self.root_fd)
            self.root_fd = -1
            observed = OwnedIdentity.from_stat(os.stat(self.root_name, dir_fd=self.parent_fd, follow_symlinks=False))
            if not self.paths[()].matches(observed):
                return False
            os.rmdir(self.root_name, dir_fd=self.parent_fd)
        except (FileNotFoundError, OSError):
            ok = False
        finally:
            if self.parent_fd >= 0:
                os.close(self.parent_fd)
                self.parent_fd = -1
        return ok

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1


def _write_all(fd: int, data: bytes, controller: SignalController) -> None:
    view = memoryview(data)
    while view:
        controller.poll()
        written = os.write(fd, view)
        if written <= 0:
            raise InitialBootstrapError("short owned write")
        view = view[written:]


def _copy_held(ledger: Ledger, parent: tuple[str, ...], name: str, held: int, mode: int, controller: SignalController) -> version.FileIdentity:
    parent_fd = ledger._fd(parent)
    temporary = "." + name + "." + secrets.token_hex(12)
    path = parent + (name,)
    temporary_path = parent + (temporary,)
    descriptor = -1
    staging: Optional[OwnedIdentity] = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | version.CLOEXEC | version.NOFOLLOW, mode, dir_fd=parent_fd)
        os.fchmod(descriptor, mode)
        created = OwnedIdentity.from_stat(os.fstat(descriptor))
        staging = created
        if created.kind != "regular" or created.uid != os.getuid() or created.mode != mode or created.nlink != 1:
            raise InitialBootstrapError("copy staging identity changed")
        os.lseek(held, 0, os.SEEK_SET)
        remaining, digest = EXPECTED_SIZE, hashlib.sha256()
        while remaining:
            controller.poll()
            block = os.read(held, min(1024 * 1024, remaining))
            if not block:
                raise InitialBootstrapError("source copy is short")
            digest.update(block)
            _write_all(descriptor, block, controller)
            remaining -= len(block)
        if os.read(held, 1) != b"" or digest.hexdigest() != EXPECTED_SOURCE_SHA256:
            raise InitialBootstrapError("source copy hash changed")
        os.fsync(descriptor)
        staged = OwnedIdentity.from_stat(os.fstat(descriptor))
        if not created.same_object(staged):
            raise InitialBootstrapError("copy staging identity changed")
        staging = staged
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        linked = OwnedIdentity.from_stat(os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False))
        final_link = OwnedIdentity.from_stat(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
        if linked.nlink != 2 or not staged.same_node(linked) or final_link != linked:
            raise InitialBootstrapError("copy hard-link transition changed")
        os.unlink(temporary, dir_fd=parent_fd)
        final = OwnedIdentity.from_stat(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
        if final.nlink != 1 or not staged.same_node(final):
            raise InitialBootstrapError("copy final identity changed")
        ledger.record(path, final, EXPECTED_SIZE, EXPECTED_SOURCE_SHA256)
        os.fsync(parent_fd)
        return version.FileIdentity.from_stat(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if staging is not None:
            try:
                observed = OwnedIdentity.from_stat(os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False))
                if observed == staging:
                    os.unlink(temporary, dir_fd=parent_fd)
            except (FileNotFoundError, OSError):
                pass
        os.close(parent_fd)


def _publish(ledger: Ledger, parent: tuple[str, ...], name: str, data: bytes, controller: SignalController) -> str:
    parent_fd = ledger._fd(parent)
    temporary = "." + name + "." + secrets.token_hex(12)
    descriptor = -1
    staging: Optional[OwnedIdentity] = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | version.CLOEXEC | version.NOFOLLOW, 0o600, dir_fd=parent_fd)
        os.fchmod(descriptor, 0o600)
        created = OwnedIdentity.from_stat(os.fstat(descriptor))
        staging = created
        _write_all(descriptor, data, controller)
        os.fsync(descriptor)
        staged = OwnedIdentity.from_stat(os.fstat(descriptor))
        if not created.same_object(staged):
            raise InitialBootstrapError("publication staging identity changed")
        staging = staged
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        linked = OwnedIdentity.from_stat(os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False))
        final_link = OwnedIdentity.from_stat(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
        if linked.nlink != 2 or not staged.same_node(linked) or final_link != linked:
            raise InitialBootstrapError("publication hard-link transition changed")
        os.unlink(temporary, dir_fd=parent_fd)
        final = OwnedIdentity.from_stat(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
        if final.nlink != 1 or not staged.same_node(final):
            raise InitialBootstrapError("publication final identity changed")
        digest = hashlib.sha256(data).hexdigest()
        ledger.record(parent + (name,), final, len(data), digest)
        os.fsync(parent_fd)
        return digest
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if staging is not None:
            try:
                observed = OwnedIdentity.from_stat(os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False))
                if observed == staging:
                    os.unlink(temporary, dir_fd=parent_fd)
            except (FileNotFoundError, OSError):
                pass
        os.close(parent_fd)


def _validate_profile(profile: InitialProfile, controller: SignalController) -> tuple[int, int, int, int]:
    controller.poll()
    if profile.expected_version != EXPECTED_VERSION or profile.source_sha256 != EXPECTED_SOURCE_SHA256:
        raise InitialBootstrapError("initial source expectation changed")
    repository = str(Path(__file__).resolve(strict=True).parents[1])
    for first, second in ((repository, profile.source_path), (repository, profile.bootstrap_root), (profile.source_path, profile.bootstrap_root)):
        if _inside(first, second) or _inside(second, first):
            raise InitialBootstrapError("initial bootstrap authorities overlap")
    if profile.source_identity.uid != os.getuid() or profile.source_identity.mode != 0o755 or profile.source_identity.nlink != 1 or profile.source_identity.size != EXPECTED_SIZE:
        raise InitialBootstrapError("source identity policy changed")
    try:
        first_parent, first = version._open_attested(profile.source_path, profile.source_identity, EXPECTED_SOURCE_SHA256, 0o755, controller)
        try:
            second_parent, second = version._open_attested(profile.source_path, profile.source_identity, EXPECTED_SOURCE_SHA256, 0o755, controller)
        except BaseException:
            os.close(first)
            os.close(first_parent)
            raise
    except version.AttestationError as exc:
        raise InitialBootstrapError("current source is not attested") from exc
    if version.FileIdentity.from_stat(os.fstat(first)) != version.FileIdentity.from_stat(os.fstat(second)):
        os.close(second); os.close(second_parent); os.close(first); os.close(first_parent)
        raise InitialBootstrapError("independent source holds disagree")
    return first_parent, first, second_parent, second


def _verify_held(profile: InitialProfile, descriptor: int, controller: SignalController) -> version.FileIdentity:
    controller.poll()
    observed = version.FileIdentity.from_stat(os.fstat(descriptor))
    if observed != profile.source_identity or version._hash_fd(descriptor, EXPECTED_SIZE, controller) != EXPECTED_SOURCE_SHA256 or version.FileIdentity.from_stat(os.fstat(descriptor)) != profile.source_identity:
        raise InitialBootstrapError("held source changed")
    return observed


def _revalidate_scratch(ledger: Ledger, prior: tuple[str, ...]) -> None:
    for name in SCRATCH_NAMES:
        fd = ledger._fd(prior + (name,))
        try:
            if os.listdir(fd):
                raise InitialBootstrapError("initial scratch changed")
        finally:
            os.close(fd)


def _module_source() -> bytes:
    path = Path(__file__).resolve(strict=True)
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or stat.S_IMODE(value.st_mode) & 0o022:
        raise InitialBootstrapError("initial bootstrap source identity changed")
    descriptor = os.open(str(path), os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW)
    try:
        data = os.read(descriptor, MODULE_LIMIT + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not data or len(data) > MODULE_LIMIT or b"\x00" in data or version.FileIdentity.from_stat(value) != version.FileIdentity.from_stat(after):
        raise InitialBootstrapError("initial bootstrap source changed while read")
    return data


def _validate_dependency() -> bytes:
    path = SCRIPT_DIRECTORY / "version_attestation_runner.py"
    value = path.lstat()
    if path.resolve(strict=True) != path or not stat.S_ISREG(value.st_mode) or value.st_size != VERSION_RUNNER_BYTES or stat.S_IMODE(value.st_mode) & 0o022:
        raise InitialBootstrapError("version dependency identity changed")
    descriptor = os.open(str(path), os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW)
    try:
        data = os.read(descriptor, VERSION_RUNNER_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(data) != VERSION_RUNNER_BYTES or hashlib.sha256(data).hexdigest() != VERSION_RUNNER_SHA256 or version.FileIdentity.from_stat(value) != version.FileIdentity.from_stat(after) or Path(version.__file__).resolve(strict=True) != path:
        raise InitialBootstrapError("version dependency bytes changed")
    return data


def validate_source_contract(data: bytes) -> dict[str, object]:
    """Pin this bridge's reviewed AST and its sole bounded child authority."""
    import ast
    try:
        tree = ast.parse(data.decode("utf-8", "strict"), filename="<version-initial-bootstrap>", mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise InitialBootstrapError("initial bootstrap source is invalid") from exc
    pin = next((node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "MODULE_AST_SHA256"), None)
    if pin is None:
        raise InitialBootstrapError("initial bootstrap source pin is missing")
    pin.value = ast.Constant(value="PINNED-MODULE-AST")
    digest = hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()
    if digest != MODULE_AST_SHA256:
        raise InitialBootstrapError("initial bootstrap source structure changed")
    constants = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    if (
        not isinstance(constants.get("EXPECTED_VERSION"), ast.Constant)
        or constants["EXPECTED_VERSION"].value != "1.1.12"
        or not isinstance(constants.get("EXPECTED_STDOUT"), ast.Constant)
        or constants["EXPECTED_STDOUT"].value != b"1.1.12\n"
    ):
        raise InitialBootstrapError("initial bootstrap local version authority changed")
    launch = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess" and node.func.attr == "Popen"]
    if len(launch) != 1:
        raise InitialBootstrapError("initial bootstrap must have one child launch")
    keywords = {node.arg for node in launch[0].keywords}
    if keywords != {"executable", "stdin", "stdout", "stderr", "cwd", "env", "start_new_session"}:
        raise InitialBootstrapError("initial bootstrap child contract changed")
    stdout_checks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "stdout"
        and any(isinstance(item, ast.Name) and item.id == "EXPECTED_STDOUT" for item in node.comparators)
    ]
    imported_stdout = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "version"
        and node.attr == "EXPECTED_STDOUT"
    ]
    if len(stdout_checks) != 1 or imported_stdout:
        raise InitialBootstrapError("initial bootstrap local stdout authority changed")
    historical = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "historical_recovery":
                historical.append(value)
    if len(historical) != 1 or not isinstance(historical[0], ast.Dict):
        raise InitialBootstrapError("initial bootstrap historical non-continuity changed")
    fields = {
        key.value: value
        for key, value in zip(historical[0].keys, historical[0].values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    if set(fields) != {"binding_sha256", "bytes_used", "revalidated", "source_continuity_claimed", "source_sha256"}:
        raise InitialBootstrapError("initial bootstrap historical non-continuity changed")
    if any(not isinstance(fields[name], ast.Constant) or fields[name].value is not False for name in ("bytes_used", "revalidated", "source_continuity_claimed")):
        raise InitialBootstrapError("initial bootstrap historical non-continuity changed")
    limitations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "limitations":
                limitations.append(value)
    if len(limitations) != 1 or not isinstance(limitations[0], ast.Dict):
        raise InitialBootstrapError("initial bootstrap recovery reconciliation limit changed")
    limitation_fields = {
        key.value: value
        for key, value in zip(limitations[0].keys, limitations[0].values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    if set(limitation_fields) != {
        "metadata_advance_authorized",
        "network_absence_os_enforced",
        "provider_backend_proven",
        "recovery_runner_version_reconciled",
    } or any(
        not isinstance(item, ast.Constant) or item.value is not False
        for item in limitation_fields.values()
    ):
        raise InitialBootstrapError("initial bootstrap recovery reconciliation limit changed")
    return {"byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest(), "status": "accepted"}


def _atomic_exit(code: int, descriptor: int, message: bytes) -> NoReturn:
    try:
        os.write(descriptor, message)
    except OSError:
        pass
    os._exit(code)


def _restore(lifecycle: LifecycleState) -> None:
    for item in reversed(lifecycle.installed_handlers):
        signal.signal(item, lifecycle.old_handlers[item])
    lifecycle.controller.merge_pending()
    signal.pthread_sigmask(signal.SIG_SETMASK, lifecycle.entry_mask)


def run_initial_bootstrap(profile: InitialProfile, *, lifecycle: Optional[LifecycleState] = None, process_owned: bool = False) -> dict[str, object]:
    """Execute one initial bridge; production owns the final success boundary."""
    created_lifecycle = lifecycle is None
    if lifecycle is None:
        lifecycle = _acquire_lifecycle()
        try:
            _activate_lifecycle(lifecycle)
        except BaseException:
            _restore(lifecycle)
            raise
    controller = lifecycle.controller
    ledger: Optional[Ledger] = None
    first_parent = first = second_parent = second = -1
    process: Optional[subprocess.Popen[bytes]] = None
    complete = False
    try:
        runner_contract = validate_source_contract(_module_source())
        controller.poll()
        _validate_dependency()
        controller.poll()
        first_parent, first, second_parent, second = _validate_profile(profile, controller)
        parent, name = os.path.split(profile.bootstrap_root)
        ledger = Ledger.create(parent, name)
        source = _copy_held(ledger, (), "agy.source", first, 0o755, controller)
        prior_name = "agy-version-attestation.initial"
        ledger.mkdir((), prior_name)
        prior = (prior_name,)
        snapshot = _copy_held(ledger, prior, "agy.snapshot", second, 0o500, controller)
        for item in SCRATCH_NAMES:
            ledger.mkdir(prior, item)
        source_path = str(Path(profile.bootstrap_root) / "agy.source")
        prior_root = str(Path(profile.bootstrap_root) / prior_name)
        snapshot_path = str(Path(prior_root) / "agy.snapshot")
        argv = [source_path, "--version"]
        environment = {
            "HOME": str(Path(prior_root) / "home"), "TMPDIR": str(Path(prior_root) / "tmp"),
            "XDG_CONFIG_HOME": str(Path(prior_root) / "xdg-config"), "XDG_CACHE_HOME": str(Path(prior_root) / "xdg-cache"),
            "XDG_STATE_HOME": str(Path(prior_root) / "xdg-state"), "LANG": "C", "LC_ALL": "C",
            "NO_COLOR": "1", "TERM": "dumb", "PATH": "/usr/bin:/bin",
        }
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        try:
            controller.merge_pending()
            controller.poll()
            process = subprocess.Popen(argv, executable=snapshot_path, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(Path(prior_root) / "cwd"), env=environment, start_new_session=True)
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        if type(process.pid) is not int or process.pid <= 1 or process.pid == os.getpgrp():
            raise InitialBootstrapError("initial version process group is unsafe")
        started = time.monotonic()
        stdout, stderr = version._capture(process, started + version.WALL_SECONDS, controller)
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        try:
            controller.merge_pending()
            exit_code = version._close_reserved_group(process, version.REAL_CALLS)
            process = None
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        if exit_code != 0 or stdout != EXPECTED_STDOUT or stderr != b"":
            raise InitialBootstrapError("initial version result did not match fixed contract")
        current_post_first = version._verify_attested_path(first_parent, profile.source_path, first, profile.source_identity, profile.source_sha256, controller)
        current_post_second = version._verify_attested_path(second_parent, profile.source_path, second, profile.source_identity, profile.source_sha256, controller)
        if current_post_first != current_post_second:
            raise InitialBootstrapError("independent current source post identities disagree")
        source_post = ledger.verify_file(("agy.source",), source, 0o755, controller)
        snapshot_post = ledger.verify_file(prior + ("agy.snapshot",), snapshot, 0o500, controller)
        _revalidate_scratch(ledger, prior)
        source_identity_bytes = _canonical_json(source.as_dict())
        snapshot_identity_bytes = _canonical_json(snapshot.as_dict())
        _publish(ledger, prior, "source.pre.json", source_identity_bytes, controller)
        _publish(ledger, prior, "source.post.json", _canonical_json(source_post.as_dict()), controller)
        _publish(ledger, prior, "snapshot.pre.json", snapshot_identity_bytes, controller)
        _publish(ledger, prior, "snapshot.post.json", _canonical_json(snapshot_post.as_dict()), controller)
        stdout_sha = _publish(ledger, prior, "version.stdout", stdout, controller)
        stderr_sha = _publish(ledger, prior, "version.stderr", stderr, controller)
        logical_sha = hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("ascii")).hexdigest()
        summary = {"call_count": 1, "child_exit": exit_code, "claim": "snapshot-version-only", "elapsed_ms": int((time.monotonic() - started) * 1000), "logical_argv_sha256": logical_sha, "schema_version": 1, "status": "accepted", "stderr_bytes": 0, "stdout_bytes": len(stdout), "timeout": False}
        summary_sha = _publish(ledger, prior, "version.summary.json", _canonical_json(summary), controller)
        binding = {"artifacts": {"version.stderr": stderr_sha, "version.stdout": stdout_sha, "version.summary.json": summary_sha}, "claim": "snapshot-version-only", "copy": {"snapshot_post": snapshot_post.as_dict(), "source_post": source_post.as_dict()}, "historical_recovery": {"binding_sha256": HISTORICAL_RECOVERY_BINDING_SHA256, "bytes_used": False, "revalidated": False, "source_continuity_claimed": False, "source_sha256": HISTORICAL_RECOVERY_SOURCE_SHA256}, "inventory": {"executable_version_bound": False}, "limitations": {"metadata_advance_authorized": False, "network_absence_os_enforced": False, "provider_backend_proven": False, "recovery_runner_version_reconciled": False}, "runner": {"byte_count": runner_contract["byte_count"], "sha256": runner_contract["sha256"]}, "schema_version": 1, "snapshot": {"post": snapshot_post.as_dict(), "pre": snapshot.as_dict(), "sha256": EXPECTED_SOURCE_SHA256}, "source": {"current_post": current_post_first.as_dict(), "current_pre": profile.source_identity.as_dict(), "post": source_post.as_dict(), "pre": source.as_dict(), "sha256": EXPECTED_SOURCE_SHA256}, "version": {"exit": exit_code, "expected": EXPECTED_VERSION, "logical_argv": argv, "logical_argv_sha256": logical_sha, "observed": EXPECTED_VERSION, "popen_count": 1, "stderr_limit": version.STREAM_LIMIT, "stdout_limit": version.STREAM_LIMIT, "timeout_seconds": version.WALL_SECONDS}}
        binding_sha = _publish(ledger, prior, "version.binding.json", _canonical_json(binding), controller)
        _publish(ledger, prior, "version.binding.sha256", (binding_sha + "\n").encode("ascii"), controller)
        recovery_profile = version.AttestationProfile(prior_binding_sha256=binding_sha, prior_root=prior_root, snapshot_identity=snapshot, snapshot_path=snapshot_path, source_identity=source, source_path=source_path, source_sha256=EXPECTED_SOURCE_SHA256, temp_parent=profile.bootstrap_root)
        initial_profile_bytes = _canonical_json({"bootstrap_root": profile.bootstrap_root, "expected_version": profile.expected_version, "source_identity": profile.source_identity.as_dict(), "source_path": profile.source_path, "source_sha256": profile.source_sha256})
        initial_profile_sha = _publish(ledger, (), "initial-bootstrap.profile.json", initial_profile_bytes, controller)
        _publish(ledger, (), "initial-bootstrap.profile.sha256", (initial_profile_sha + "\n").encode("ascii"), controller)
        recovery_profile_bytes = _canonical_json(dataclasses.asdict(recovery_profile))
        recovery_profile_sha = _publish(ledger, (), "version.recovery.profile.json", recovery_profile_bytes, controller)
        _publish(ledger, (), "version.recovery.profile.sha256", (recovery_profile_sha + "\n").encode("ascii"), controller)
        ledger.inventory(prior, version.PRIOR_FILES)
        ledger.inventory((), frozenset({"agy.source", prior_name, "initial-bootstrap.profile.json", "initial-bootstrap.profile.sha256", "version.recovery.profile.json", "version.recovery.profile.sha256"}))
        version._validate_prior(recovery_profile)
        ledger.validate(controller)
        result = {"binding_sha256": binding_sha, "call_count": 1, "claim": "snapshot-version-initial-bootstrap", "initial_profile_sha256": initial_profile_sha, "profile": dataclasses.asdict(recovery_profile), "recovery_profile_sha256": recovery_profile_sha, "status": "accepted"}
        controller.poll()
        if process_owned:
            sys.stdout.buffer.write(_canonical_json(result)); sys.stdout.buffer.flush()
            blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
            controller.merge_pending()
            if controller.choose() is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
                raise InitialBootstrapInterrupted(controller.choose())
            complete = True
            os._exit(0)
        return result
    except BaseException:
        uncertain_process = False
        if process is not None:
            try:
                version._terminate_group(process, version.REAL_CALLS)
            except BaseException:
                uncertain_process = True
        if ledger is not None and not uncertain_process:
            ledger.rollback()
        raise
    finally:
        if first >= 0: os.close(first)
        if second >= 0: os.close(second)
        if first_parent >= 0: os.close(first_parent)
        if second_parent >= 0: os.close(second_parent)
        if ledger is not None and not complete: ledger.close()
        if created_lifecycle and not complete:
            _restore(lifecycle)


def _read_stdin(controller: SignalController) -> bytes:
    data = bytearray()
    while True:
        controller.poll()
        block = os.read(sys.stdin.buffer.fileno(), min(4096, PROFILE_LIMIT + 1 - len(data)))
        if not block: break
        data.extend(block)
        if len(data) > PROFILE_LIMIT: raise InitialBootstrapError("initial bootstrap profile exceeds bound")
    return bytes(data)


def main(argv: Sequence[str]) -> NoReturn:
    if not _runtime_supported():
        _atomic_exit(2, sys.stderr.buffer.fileno(), b"version initial bootstrap: rejected\n")
    lifecycle: Optional[LifecycleState] = None
    try:
        if list(argv) != ["--initial-bootstrap-version"]:
            _atomic_exit(64, sys.stderr.buffer.fileno(), b"version initial bootstrap: invalid invocation\n")
        validate_source_contract(_module_source())
        _validate_dependency()
        if not version._production_startup_evaluation().accepted:
            raise InitialBootstrapError("production interpreter trust rejected")
        lifecycle = _acquire_lifecycle()
        _activate_lifecycle(lifecycle)
        run_initial_bootstrap(InitialProfile.from_bytes(_read_stdin(lifecycle.controller)), lifecycle=lifecycle, process_owned=True)
    except InitialBootstrapInterrupted as exc:
        _atomic_exit(128 + exc.signum, sys.stderr.buffer.fileno(), b"version initial bootstrap: interrupted\n")
    except BaseException:
        if lifecycle is not None:
            lifecycle.controller.merge_pending()
            selected = lifecycle.controller.choose()
            if selected is not None:
                _atomic_exit(128 + selected, sys.stderr.buffer.fileno(), b"version initial bootstrap: interrupted\n")
        _atomic_exit(2, sys.stderr.buffer.fileno(), b"version initial bootstrap: rejected\n")
    _atomic_exit(2, sys.stderr.buffer.fileno(), b"version initial bootstrap: rejected\n")


if __name__ == "__main__":
    main(sys.argv[1:])
