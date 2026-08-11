#!/usr/bin/env python3
"""Prepare one recovery input from one retained accepted version binding.

This repository-only command does not add a recovery mode. It validates one
reviewed retained recovery result, copies its held executable bytes into one new
private root, performs one bounded snapshot-backed ``--version`` observation, and
emits the unchanged recovery runner's input shape.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import pathlib
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn, Optional, Sequence


RUNTIME_MAJOR = 3
RUNTIME_MINOR = 9


def _runtime_contract(
    implementation: object,
    major: object,
    minor: object,
    isolated: object,
    no_site: object,
    dont_write_bytecode: object,
    ignore_environment: object,
) -> bool:
    """Pure predicate for the reviewed CPython AST and flag ABI."""

    return (
        type(implementation) is str
        and implementation == "cpython"
        and type(major) is int
        and major == RUNTIME_MAJOR
        and type(minor) is int
        and minor == RUNTIME_MINOR
        and type(isolated) is int
        and isolated == 1
        and type(no_site) is int
        and no_site == 1
        and type(dont_write_bytecode) is int
        and dont_write_bytecode == 1
        and type(ignore_environment) is int
        and ignore_environment == 1
    )


def _runtime_supported() -> bool:
    return _runtime_contract(
        sys.implementation.name,
        sys.version_info.major,
        sys.version_info.minor,
        sys.flags.isolated,
        sys.flags.no_site,
        sys.flags.dont_write_bytecode,
        sys.flags.ignore_environment,
    )


sys.dont_write_bytecode = True
SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))
import version_attestation_runner


version = version_attestation_runner
EXPECTED_VERSION = "1.1.11"
EXPECTED_SOURCE_SHA256 = "198ff7c3f6d173daa510b0814aa70c6ce14c94035bcd4707a3c0e79fa38a7bc3"
EXPECTED_BINDING_SHA256 = "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2"
EXPECTED_SIZE = 169_718_336
VERSION_RUNNER_BYTES = 62_988
VERSION_RUNNER_SHA256 = "e6bd55d2d0ab6c542745fd1bb1af4f6f4b7f163abb6f8c78597a24475d501d28"
PROFILE_LIMIT = 16_384
MODULE_LIMIT = 128 * 1024
MODULE_AST_SHA256 = "39cdf016cc215064d60a0bed61511b51c7f69ee294bdc8ef00d4c3b77ef75240"
PRODUCTION_AST_SHA256 = {
    "BootstrapProfile": "3944fa36927a9fe1ab57fbde0a5ddca5550d0461793d070d4b4a89ac42b426b2",
    "LifecycleState": "a20b236f7553b79b0d2aad14bf9e37e84c35e94fc53c61b873d9101743ebfa19",
    "OwnedIdentity": "effd36af34cefe23f6209bff085fdf138047de39546ce06d03b962ded600c1f8",
    "OwnershipLedger": "7ea923fc564d930a35fa68215bd68e86f7c4abe7017030c0d7ff338be95681db",
    "OwnedPublisher": "32fac9cf760586e3766515bcb21cc5e20eda3b6ef91dc6b71a858b0d056c1a0a",
    "SignalController": "e7d83d7bdefb7c6c3e45f750764915a6b3abb53548136e242b8bf4011c836365",
    "_acquire_lifecycle": "6b8a5786a3ef8654a660c931e4e953b580007598d2930d14246c8e669d94a32f",
    "_activate_lifecycle": "fd4b01fd2082d0d97714ba972dd2683a636361eeda4010a536f154b3dd4dedb3",
    "_atomic_exit": "4a057d05a8a83cb81806e4e40b9e74b54468e487b8d88d4a6d476b7854d18a1a",
    "_canonical_json": "fabcd67b48b36dd92128417c318ccecdd1afe85e1373ef80f1e51657032a3255",
    "_capture": "d169526a2c8ad817f3ea4bb881859208d3456d5acf6633f599f6d2057118f443",
    "_copy_held": "3c8eae74eb43aa111e1b35e6bf622633a2d85b3e1e10ae50418a152bb7949138",
    "_directory": "cba4c52452c71cca425f02babf435b799e58a4ef7e851a771075cf1a6ff0853d",
    "_exact_identity_at": "e8717e333e7a57305684fb0707c4d62e301a01a32302c3342ccd694a1cc4c078",
    "_execute_bootstrap": "ebfcc1fb3d77197a5017bf530656edb5f2132e9368f7720aab99c914678c045a",
    "_finish_process_group": "8f6077c1c23fd414e4be0bdf8b458f5865eeb301fa75acf0a330b9a80713ca2c",
    "_group_exists": "0cb0c553859c6d4d19f2761d181ba419be9ebfff95adfea68fce1bb635f9446f",
    "_hash_fd": "e9a3acc4fecf0dc5e39f2a8fa97b208e556b757125b0f05985b5fae37d28f440",
    "_inside": "d56461b1abf4f7808b86bb8052edd5c34ba80bc3df44e9e8efd97130dc3b0f95",
    "_logical_argv_sha256": "64714c630e01825fb684ef89f506549b0a57c17d5d7e67f0e7bb35fe6c9174ac",
    "_module_source": "a6211a3636961c5fd3be3705d5950b127e8fe8acb1a1f0841658b3b71adcb0bb",
    "_open_attested": "feace71fea2f22bc92615153700e1edf4bb642362cf80deead77f7877a40ced7",
    "_pending_signal": "e0b87ba8fd9872503b16eb5e36caa54786c8a58854032b2cf5e0f521c8a3683a",
    "_read_private": "196103bca7ab54da92feefe17572c90a497a2c915647acdbfc47118cdc72ad72",
    "_read_stdin": "74bbe7a749d9cc513e7e12a15cbd91fc418bb9d0a67ff2afee2ef692fe33446e",
    "_revalidate_scratch": "d774af3cf43eabd9f8a70a8368610480ad37ffee6cebb6e04f36c318a53fea42",
    "_runtime_contract": "a08ebd0b22a254eba0b6be693a0897a240fda9ef3be7fbfcdf240ed2bd7d2ca3",
    "_runtime_supported": "2d91df8c58018bb0c1ee3f5f8dbee05261a8d77415d1cacf6eb6717d24b4d317",
    "_validate_dependency": "fb928abf33e89a3c385a02c3f26c53302c0016f3a8dfa585069650632597be75",
    "_validate_process": "940826ab9b81446b6457f1807560ea0feb1c85c7f733018ad93cbeee7b88d052",
    "_validate_retained": "fd36d7ec6b23da09631b09cf9067a963f651bdc99ad3455b15664dd693c0d99b",
    "_verify_attested_path": "952690eddc1b464383857abd9a346c74f39c3de9942eb3a243767491723bde65",
    "_write_all": "905aa5580afea810b31c79197635758a11b195eca2244ee24f10050dac5fecdf",
    "main": "b6004bfa15287d32d6f25db70edf18aa6ab564c172de8b8ca3114f5157b626db",
    "run_bootstrap": "c661c016c79e3002b4f37f6cee7307aa2202ca011d0ebb54fa3d779aea024d98",
    "validate_source_contract": "f6756392ff3fe1ede822a7c7b39c906d0cc3e6f70252e7b26fd31fc99f73e0fc",
}
PRODUCTION_GRAPH = {
    "BootstrapProfile": ("_canonical_json",),
    "LifecycleState": (),
    "OwnedIdentity": (),
    "OwnershipLedger": ("_directory", "_exact_identity_at", "_hash_fd"),
    "OwnedPublisher": ("_exact_identity_at", "_write_all"),
    "SignalController": ("_pending_signal",),
    "_acquire_lifecycle": ("LifecycleState", "SignalController"),
    "_activate_lifecycle": (),
    "_atomic_exit": (),
    "_canonical_json": (),
    "_capture": (),
    "_copy_held": ("_exact_identity_at", "_write_all"),
    "_directory": (),
    "_exact_identity_at": (),
    "_execute_bootstrap": (
        "OwnedPublisher",
        "_atomic_exit",
        "_canonical_json",
        "_capture",
        "_copy_held",
        "_finish_process_group",
        "_logical_argv_sha256",
        "_module_source",
        "_revalidate_scratch",
        "_validate_dependency",
        "_validate_process",
        "_validate_retained",
        "_verify_attested_path",
        "_write_all",
        "validate_source_contract",
    ),
    "_finish_process_group": ("_group_exists", "_validate_process"),
    "_group_exists": (),
    "_hash_fd": (),
    "_inside": (),
    "_logical_argv_sha256": (),
    "_module_source": (),
    "_open_attested": ("_hash_fd",),
    "_pending_signal": (),
    "_read_private": (),
    "_read_stdin": (),
    "_revalidate_scratch": (),
    "_runtime_contract": (),
    "_runtime_supported": ("_runtime_contract",),
    "_validate_dependency": (),
    "_validate_process": (),
    "_validate_retained": (
        "_directory",
        "_inside",
        "_logical_argv_sha256",
        "_open_attested",
        "_read_private",
    ),
    "_verify_attested_path": ("_hash_fd",),
    "_write_all": (),
    "main": (
        "_acquire_lifecycle",
        "_activate_lifecycle",
        "_atomic_exit",
        "_execute_bootstrap",
        "_module_source",
        "_read_stdin",
        "_runtime_supported",
    ),
    "run_bootstrap": (
        "_acquire_lifecycle",
        "_activate_lifecycle",
        "_execute_bootstrap",
        "_runtime_supported",
    ),
    "validate_source_contract": (),
}
BOOTSTRAP_KEYS = frozenset(
    {
        "account_home",
        "bootstrap_root",
        "retained_binding_sha256",
        "retained_snapshot_path",
        "retained_source_path",
        "retained_version_root",
    }
)
SCRATCH_NAMES = ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state")
RECOVERY_FILES = frozenset(
    {
        "cwd",
        "home",
        "runner.py",
        "runner.py.sha256",
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
RETAINED_ARTIFACTS = frozenset(
    {"runner.py", "version.stderr", "version.stdout", "version.summary.json"}
)


class BootstrapError(ValueError):
    """The closed bootstrap contract rejected the supplied authority."""


class BootstrapInterrupted(SystemExit):
    def __init__(self, signum: int):
        super().__init__(128 + signum)
        self.signum = signum


class SignalController:
    """Latch signals and freeze one deterministic choice only at checkpoints."""

    def __init__(self) -> None:
        self.observed: set[int] = set()
        self.selected: Optional[int] = None

    def latch(self, signum: int, _frame: object = None) -> None:
        if signum in version.LIFECYCLE_SIGNALS:
            self.observed.add(signum)

    def choose(self) -> Optional[int]:
        if self.selected is None:
            for signum in version.LIFECYCLE_SIGNALS:
                if signum in self.observed:
                    self.selected = signum
                    break
        return self.selected

    def poll(self) -> None:
        selected = self.choose()
        if selected is not None:
            raise BootstrapInterrupted(selected)

    def merge_pending(self) -> None:
        while True:
            pending = _pending_signal()
            if pending is None:
                return
            self.latch(pending)


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
        raise BootstrapError("required signal primitives are unavailable")
    controller = SignalController()
    entry_mask = signal.pthread_sigmask(
        signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS
    )
    old_handlers: dict[signal.Signals, object] = {}
    installed_handlers: list[signal.Signals] = []
    try:
        old_handlers = {
            item: signal.getsignal(item) for item in version.LIFECYCLE_SIGNALS
        }
        for item in version.LIFECYCLE_SIGNALS:
            installed_handlers.append(item)
            signal.signal(item, controller.latch)
        controller.merge_pending()
        return LifecycleState(
            controller, entry_mask, old_handlers, installed_handlers
        )
    except BaseException:
        for item in reversed(installed_handlers):
            try:
                signal.signal(item, old_handlers[item])
            except BaseException:
                pass
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        raise


def _activate_lifecycle(state: LifecycleState) -> None:
    signal.pthread_sigmask(signal.SIG_SETMASK, state.entry_mask)
    state.controller.poll()


@dataclass(frozen=True)
class BootstrapProfile:
    account_home: str
    bootstrap_root: str
    retained_binding_sha256: str
    retained_snapshot_path: str
    retained_source_path: str
    retained_version_root: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "BootstrapProfile":
        value = version._strict_json(data)
        if (
            not isinstance(value, dict)
            or set(value) != BOOTSTRAP_KEYS
            or _canonical_json(value) != data
        ):
            raise BootstrapError("invalid bootstrap profile")
        for key in (
            "account_home",
            "retained_snapshot_path",
            "retained_source_path",
            "retained_version_root",
        ):
            version._require_canonical_absolute(value[key])
        root = value["bootstrap_root"]
        if (
            not isinstance(root, str)
            or not os.path.isabs(root)
            or os.path.normpath(root) != root
            or os.path.realpath(os.path.dirname(root)) != os.path.dirname(root)
            or os.path.lexists(root)
        ):
            raise BootstrapError("bootstrap root is not a new canonical path")
        if (
            not isinstance(value["retained_binding_sha256"], str)
            or not version._is_sha256(value["retained_binding_sha256"])
        ):
            raise BootstrapError("invalid bootstrap profile")
        return cls(**value)


@dataclass(frozen=True)
class OwnedIdentity:
    dev: int
    gid: int
    ino: int
    kind: str
    mode: int
    nlink: int
    uid: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "OwnedIdentity":
        if stat.S_ISDIR(value.st_mode):
            kind = "directory"
        elif stat.S_ISREG(value.st_mode):
            kind = "regular"
        else:
            kind = "other"
        return cls(
            dev=value.st_dev,
            gid=value.st_gid,
            ino=value.st_ino,
            kind=kind,
            mode=stat.S_IMODE(value.st_mode),
            nlink=value.st_nlink,
            uid=value.st_uid,
        )

    def with_nlink(self, nlink: int) -> "OwnedIdentity":
        return dataclasses.replace(self, nlink=nlink)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"


def _inside(path: str, parent: str) -> bool:
    return os.path.commonpath((path, parent)) == parent


def _directory(path: str, *, private: bool = True) -> int:
    descriptor = version._open_dir(path)
    value = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.getuid()
        or (private and stat.S_IMODE(value.st_mode) != 0o700)
    ):
        os.close(descriptor)
        raise BootstrapError("private directory authority changed")
    return descriptor


def _exact_identity_at(parent: int, name: str, expected: OwnedIdentity) -> bool:
    try:
        observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return OwnedIdentity.from_stat(observed) == expected


class OwnershipLedger:
    """Immutable creation identities for one bounded private output tree."""

    def __init__(
        self,
        parent_fd: int,
        root_name: str,
        root_fd: int,
        root_identity: OwnedIdentity,
    ) -> None:
        self.parent_fd = parent_fd
        self.root_name = root_name
        self.root_fd = root_fd
        self.directories: dict[tuple[str, ...], OwnedIdentity] = {(): root_identity}
        self.files: dict[tuple[str, ...], OwnedIdentity] = {}
        self.contents: dict[tuple[str, ...], tuple[int, str]] = {}
        self.reserved: set[tuple[str, ...]] = set()

    @classmethod
    def create(cls, parent_path: str, root_name: str) -> "OwnershipLedger":
        parent_fd = _directory(parent_path)
        root_fd = -1
        root_identity: Optional[OwnedIdentity] = None
        try:
            os.mkdir(root_name, 0o700, dir_fd=parent_fd)
            root_fd = os.open(
                root_name,
                os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                dir_fd=parent_fd,
            )
            identity = OwnedIdentity.from_stat(os.fstat(root_fd))
            root_identity = identity
            if identity.kind != "directory" or identity.uid != os.getuid() or identity.mode != 0o700:
                raise BootstrapError("bootstrap root identity changed")
            os.fsync(parent_fd)
            return cls(parent_fd, root_name, root_fd, identity)
        except BaseException:
            if root_fd >= 0:
                os.close(root_fd)
            try:
                if root_identity is not None:
                    child = os.open(
                        root_name,
                        os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                        dir_fd=parent_fd,
                    )
                    try:
                        if (
                            OwnedIdentity.from_stat(os.fstat(child)) == root_identity
                            and not os.listdir(child)
                        ):
                            os.rmdir(root_name, dir_fd=parent_fd)
                            os.fsync(parent_fd)
                    finally:
                        os.close(child)
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                pass
            os.close(parent_fd)
            raise

    def _expected_directory(self, relative: tuple[str, ...]) -> OwnedIdentity:
        identity = self.directories[relative]
        direct_entries = {
            child
            for child in (*self.directories, *self.files, *self.reserved)
            if len(child) == len(relative) + 1 and child[:-1] == relative
        }
        direct_children = len(direct_entries)
        return identity.with_nlink(identity.nlink + direct_children)

    def open_directory(self, relative: tuple[str, ...]) -> int:
        descriptor = os.dup(self.root_fd)
        try:
            current: tuple[str, ...] = ()
            if OwnedIdentity.from_stat(os.fstat(descriptor)) != self._expected_directory(current):
                raise BootstrapError("owned directory identity changed")
            for name in relative:
                next_descriptor = os.open(
                    name,
                    os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = next_descriptor
                current += (name,)
                if OwnedIdentity.from_stat(os.fstat(descriptor)) != self._expected_directory(current):
                    raise BootstrapError("owned directory identity changed")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def create_directory(self, parent: tuple[str, ...], name: str) -> OwnedIdentity:
        relative = parent + (name,)
        descriptor = self.open_directory(parent)
        child = -1
        try:
            os.mkdir(name, 0o700, dir_fd=descriptor)
            child = os.open(
                name,
                os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                dir_fd=descriptor,
            )
            identity = OwnedIdentity.from_stat(os.fstat(child))
            if (
                identity.kind != "directory"
                or identity.uid != os.getuid()
                or identity.mode != 0o700
                or identity.nlink != 2
            ):
                raise BootstrapError("created directory identity changed")
            self.directories[relative] = identity
            os.fsync(descriptor)
            return identity
        finally:
            if child >= 0:
                os.close(child)
            os.close(descriptor)

    def register_file(self, relative: tuple[str, ...], identity: OwnedIdentity) -> None:
        if relative in self.files or relative in self.directories:
            raise BootstrapError("owned artifact was registered twice")
        if identity.kind != "regular" or identity.uid != os.getuid() or identity.nlink != 1:
            raise BootstrapError("owned file identity is invalid")
        self.files[relative] = identity
        self.reserved.discard(relative)

    def register_linked_file(
        self,
        temporary: tuple[str, ...],
        final: tuple[str, ...],
        identity: OwnedIdentity,
    ) -> None:
        if (
            self.files.get(temporary) != identity
            or final in self.files
            or final in self.directories
            or final not in self.reserved
            or identity.nlink != 1
        ):
            raise BootstrapError("owned artifact link transition changed")
        linked = identity.with_nlink(2)
        self.files[temporary] = linked
        self.files[final] = linked
        self.reserved.remove(final)

    def normalize_linked_file(
        self,
        temporary: tuple[str, ...],
        final: tuple[str, ...],
        identity: OwnedIdentity,
    ) -> None:
        linked = identity.with_nlink(2)
        if self.files.get(temporary) != linked or self.files.get(final) != linked:
            raise BootstrapError("owned artifact link normalization changed")
        self.files.pop(temporary)
        self.contents.pop(temporary, None)
        self.files[final] = identity

    def cancel_linked_file(
        self,
        temporary: tuple[str, ...],
        final: tuple[str, ...],
        identity: OwnedIdentity,
    ) -> None:
        linked = identity.with_nlink(2)
        if self.files.get(temporary) not in {identity, linked}:
            return
        if self.files.get(final) not in {None, linked}:
            return
        self.files[temporary] = identity
        self.files.pop(final, None)
        self.contents.pop(final, None)
        self.reserved.add(final)

    def recover_link_transition(
        self,
        parent: int,
        temporary_path: tuple[str, ...],
        final_path: tuple[str, ...],
        temporary: str,
        final: str,
        identity: OwnedIdentity,
    ) -> None:
        """Normalize only an exact owned link pair after a transient failure."""

        linked = identity.with_nlink(2)
        try:
            if _exact_identity_at(parent, temporary, linked) and _exact_identity_at(
                parent, final, linked
            ):
                os.unlink(final, dir_fd=parent)
                self.cancel_linked_file(temporary_path, final_path, identity)
                os.fsync(parent)
                return
            if _exact_identity_at(parent, temporary, identity):
                self.cancel_linked_file(temporary_path, final_path, identity)
                return
            if _exact_identity_at(parent, final, identity):
                self.normalize_linked_file(temporary_path, final_path, identity)
        except (BootstrapError, OSError):
            return

    def reserve(self, relative: tuple[str, ...]) -> None:
        if relative in self.files or relative in self.directories or relative in self.reserved:
            raise BootstrapError("artifact target reservation changed")
        self.reserved.add(relative)

    def release_absent(self, relative: tuple[str, ...]) -> None:
        if relative not in self.reserved:
            return
        self.reserved.discard(relative)
        try:
            parent = self.open_directory(relative[:-1])
        except (BootstrapError, FileNotFoundError, NotADirectoryError):
            self.reserved.add(relative)
            return
        try:
            try:
                os.stat(relative[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            self.reserved.add(relative)
        finally:
            os.close(parent)

    def unregister_file(self, relative: tuple[str, ...]) -> None:
        self.files.pop(relative, None)
        self.contents.pop(relative, None)

    def bind_content(self, relative: tuple[str, ...], size: int, sha256: str) -> None:
        if relative not in self.files or size < 0 or not version._is_sha256(sha256):
            raise BootstrapError("owned artifact content binding is invalid")
        self.contents[relative] = (size, sha256)

    def validate(self, controller: SignalController) -> None:
        controller.poll()
        root = os.open(
            self.root_name,
            os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
            dir_fd=self.parent_fd,
        )
        try:
            if OwnedIdentity.from_stat(os.fstat(root)) != self._expected_directory(()):
                raise BootstrapError("bootstrap root path changed")
        finally:
            os.close(root)
        for relative, expected in self.directories.items():
            controller.poll()
            descriptor = self.open_directory(relative)
            os.close(descriptor)
        for relative, expected in self.files.items():
            controller.poll()
            parent = self.open_directory(relative[:-1])
            try:
                if not _exact_identity_at(parent, relative[-1], expected):
                    raise BootstrapError("owned artifact identity changed")
                content = self.contents.get(relative)
                if content is not None:
                    descriptor = os.open(
                        relative[-1],
                        os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW,
                        dir_fd=parent,
                    )
                    try:
                        size, sha256 = content
                        observed = os.fstat(descriptor)
                        if (
                            OwnedIdentity.from_stat(observed) != expected
                            or observed.st_size != size
                            or _hash_fd(descriptor, size, controller) != sha256
                            or OwnedIdentity.from_stat(os.fstat(descriptor)) != expected
                        ):
                            raise BootstrapError("owned artifact content changed")
                        controller.poll()
                    finally:
                        os.close(descriptor)
            finally:
                os.close(parent)

    def rollback(self) -> bool:
        """Delete only exact owned identities; return false when a residual remains."""

        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        try:
            try:
                root = os.open(
                    self.root_name,
                    os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                    dir_fd=self.parent_fd,
                )
            except (FileNotFoundError, NotADirectoryError):
                return False
            try:
                if OwnedIdentity.from_stat(os.fstat(root)) != self._expected_directory(()):
                    return False
                allowed = {path[0] for path in self.files if path} | {
                    path[0] for path in self.directories if path
                } | {path[0] for path in self.reserved if path}
                if not set(os.listdir(root)).issubset(allowed):
                    return False
            finally:
                os.close(root)
            for relative in sorted(tuple(self.files), key=lambda item: (len(item), item), reverse=True):
                expected = self.files[relative]
                try:
                    parent = self.open_directory(relative[:-1])
                except (BootstrapError, FileNotFoundError, NotADirectoryError):
                    continue
                try:
                    if _exact_identity_at(parent, relative[-1], expected):
                        os.unlink(relative[-1], dir_fd=parent)
                        removed = self.files.pop(relative, None)
                        self.contents.pop(relative, None)
                        if removed is not None and removed.nlink > 1:
                            for other, identity in tuple(self.files.items()):
                                if identity == removed:
                                    self.files[other] = identity.with_nlink(
                                        identity.nlink - 1
                                    )
                        os.fsync(parent)
                finally:
                    os.close(parent)
            for relative in sorted(
                (item for item in self.directories if item),
                key=lambda item: (len(item), item),
                reverse=True,
            ):
                try:
                    parent = self.open_directory(relative[:-1])
                except (BootstrapError, FileNotFoundError, NotADirectoryError):
                    continue
                try:
                    expected = self.directories[relative]
                    try:
                        child = os.open(
                            relative[-1],
                            os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                            dir_fd=parent,
                        )
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                    try:
                        if (
                            OwnedIdentity.from_stat(os.fstat(child)) == expected
                            and not os.listdir(child)
                        ):
                            os.rmdir(relative[-1], dir_fd=parent)
                            self.directories.pop(relative, None)
                            os.fsync(parent)
                    finally:
                        os.close(child)
                finally:
                    os.close(parent)
            expected_root = self.directories.get(())
            if expected_root is None:
                return False
            try:
                root = os.open(
                    self.root_name,
                    os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                    dir_fd=self.parent_fd,
                )
            except (FileNotFoundError, NotADirectoryError):
                return False
            try:
                removable = (
                    OwnedIdentity.from_stat(os.fstat(root)) == expected_root
                    and not os.listdir(root)
                )
            finally:
                os.close(root)
            if removable:
                os.rmdir(self.root_name, dir_fd=self.parent_fd)
                self.directories.pop((), None)
                os.fsync(self.parent_fd)
                return True
            return False
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)

    def close(self) -> None:
        """Make the final root and parent durable without releasing rollback authority."""

        os.fsync(self.root_fd)
        os.fsync(self.parent_fd)

    def release(self) -> None:
        """Release descriptors only after rollback or completion linearization."""

        root_fd, parent_fd = self.root_fd, self.parent_fd
        self.root_fd = self.parent_fd = -1
        if root_fd >= 0:
            os.close(root_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def _write_all(descriptor: int, data: bytes, controller: SignalController) -> None:
    view = memoryview(data)
    while view:
        controller.poll()
        written = os.write(descriptor, view)
        if written <= 0:
            raise BootstrapError("bootstrap artifact write failed")
        view = view[written:]
    controller.poll()


class OwnedPublisher:
    """Mode-0600, no-overwrite publication bound to an ownership ledger."""

    def __init__(
        self,
        ledger: OwnershipLedger,
        parent: tuple[str, ...],
        fsync: Callable[[int], None],
        controller: SignalController,
    ) -> None:
        self.ledger = ledger
        self.parent = parent
        self.fsync = fsync
        self.controller = controller

    def publish(self, name: str, data: bytes) -> str:
        if not name or "/" in name or name in {".", ".."}:
            raise BootstrapError("invalid artifact name")
        self.controller.poll()
        parent = self.ledger.open_directory(self.parent)
        temporary = "." + name + "." + secrets.token_hex(12) + ".tmp"
        temporary_path = self.parent + (temporary,)
        final_path = self.parent + (name,)
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | version.CLOEXEC | version.NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            os.fchmod(descriptor, 0o600)
            created = OwnedIdentity.from_stat(os.fstat(descriptor))
            if created.kind != "regular" or created.uid != os.getuid() or created.mode != 0o600 or created.nlink != 1:
                raise BootstrapError("staged artifact identity changed")
            self.ledger.register_file(temporary_path, created)
            self.controller.poll()
            _write_all(descriptor, data, self.controller)
            self.fsync(descriptor)
            self.controller.poll()
            staged = OwnedIdentity.from_stat(os.fstat(descriptor))
            if staged != created:
                raise BootstrapError("staged artifact identity changed")
            os.close(descriptor)
            descriptor = -1
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BootstrapError("artifact target already exists")
            self.ledger.reserve(final_path)
            os.link(
                temporary,
                name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
            linked = staged.with_nlink(2)
            try:
                self.ledger.register_linked_file(temporary_path, final_path, staged)
                if (
                    not _exact_identity_at(parent, temporary, linked)
                    or not _exact_identity_at(parent, name, linked)
                ):
                    raise BootstrapError("artifact link identity changed")
                os.unlink(temporary, dir_fd=parent)
                self.ledger.normalize_linked_file(temporary_path, final_path, staged)
            except BaseException:
                self.ledger.recover_link_transition(
                    parent,
                    temporary_path,
                    final_path,
                    temporary,
                    name,
                    staged,
                )
                raise
            self.ledger.bind_content(final_path, len(data), hashlib.sha256(data).hexdigest())
            self.fsync(parent)
            descriptor = os.open(
                name,
                os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW,
                dir_fd=parent,
            )
            try:
                if (
                    OwnedIdentity.from_stat(os.fstat(descriptor)) != staged
                    or not _exact_identity_at(parent, name, staged)
                ):
                    raise BootstrapError("published artifact identity changed")
            finally:
                os.close(descriptor)
                descriptor = -1
            self.controller.poll()
            return hashlib.sha256(data).hexdigest()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            self.ledger.release_absent(final_path)
            os.close(parent)


def _hash_fd(descriptor: int, size: int, controller: SignalController) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        controller.poll()
        block = os.read(descriptor, min(remaining, 1024 * 1024))
        controller.poll()
        if not block:
            raise BootstrapError("file ended before its attested size")
        digest.update(block)
        remaining -= len(block)
    controller.poll()
    if os.read(descriptor, 1) != b"":
        raise BootstrapError("file exceeded its attested size")
    controller.poll()
    return digest.hexdigest()


def _open_attested(
    path: str,
    identity: version.FileIdentity,
    sha256: str,
    expected_mode: int,
    controller: SignalController,
) -> tuple[int, int]:
    controller.poll()
    parent = version._open_dir(os.path.dirname(path))
    descriptor = -1
    try:
        leaf = os.path.basename(path)
        descriptor = os.open(
            leaf, os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW, dir_fd=parent
        )
        raw = os.fstat(descriptor)
        observed = version.FileIdentity.from_stat(raw)
        if (
            not stat.S_ISREG(raw.st_mode)
            or raw.st_uid != os.getuid()
            or stat.S_IMODE(raw.st_mode) != expected_mode
            or raw.st_nlink != 1
            or observed != identity
            or _hash_fd(descriptor, identity.size, controller) != sha256
            or version.FileIdentity.from_stat(os.fstat(descriptor)) != identity
        ):
            raise BootstrapError("attested executable identity changed")
        controller.poll()
        return parent, descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)
        raise


def _verify_attested_path(
    parent: int,
    path: str,
    held: int,
    identity: version.FileIdentity,
    sha256: str,
    controller: SignalController,
) -> version.FileIdentity:
    controller.poll()
    leaf = os.path.basename(path)
    held_identity = version.FileIdentity.from_stat(os.fstat(held))
    path_identity = version.FileIdentity.from_stat(
        os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    )
    reopened = os.open(
        leaf, os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW, dir_fd=parent
    )
    try:
        reopened_identity = version.FileIdentity.from_stat(os.fstat(reopened))
        reopened_sha = _hash_fd(reopened, identity.size, controller)
    finally:
        os.close(reopened)
    if (
        held_identity != identity
        or path_identity != identity
        or reopened_identity != identity
        or _hash_fd(held, identity.size, controller) != sha256
        or reopened_sha != sha256
    ):
        raise BootstrapError("attested executable path changed")
    controller.poll()
    return held_identity


def _read_private(
    root: int,
    name: str,
    controller: SignalController,
    cap: int = PROFILE_LIMIT,
) -> bytes:
    controller.poll()
    value = os.stat(name, dir_fd=root, follow_symlinks=False)
    identity = version.FileIdentity.from_stat(value)
    if (
        not stat.S_ISREG(value.st_mode)
        or identity.uid != os.getuid()
        or identity.mode != 0o600
        or identity.nlink != 1
        or identity.size > cap
    ):
        raise BootstrapError("private retained artifact is invalid")
    descriptor = os.open(name, os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW, dir_fd=root)
    try:
        data = os.read(descriptor, cap + 1)
        controller.poll()
        if (
            len(data) != identity.size
            or os.read(descriptor, 1) != b""
            or version.FileIdentity.from_stat(os.fstat(descriptor)) != identity
        ):
            raise BootstrapError("private retained artifact changed")
        controller.poll()
        return data
    finally:
        os.close(descriptor)


def _logical_argv_sha256(argv: list[str]) -> str:
    return hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("ascii")).hexdigest()


def _validate_retained(
    profile: BootstrapProfile,
    controller: SignalController,
) -> tuple[int, int, int, int, version.FileIdentity, version.FileIdentity]:
    controller.poll()
    repository = str(Path(__file__).resolve(strict=True).parents[1])
    authorities = (
        repository,
        profile.account_home,
        profile.retained_version_root,
        profile.bootstrap_root,
    )
    for index, first in enumerate(authorities):
        for second in authorities[index + 1 :]:
            if _inside(first, second) or _inside(second, first):
                raise BootstrapError("bootstrap authorities overlap")
    if (
        not _inside(profile.retained_source_path, profile.account_home)
        or _inside(profile.retained_snapshot_path, profile.account_home)
    ):
        raise BootstrapError("retained executable account policy changed")
    account = _directory(profile.account_home)
    os.close(account)
    root = _directory(profile.retained_version_root)
    try:
        controller.poll()
        if set(os.listdir(root)) != RECOVERY_FILES:
            raise BootstrapError("retained recovery evidence shape changed")
        for name in SCRATCH_NAMES:
            controller.poll()
            child = os.open(
                name,
                os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                dir_fd=root,
            )
            try:
                observed = os.fstat(child)
                if (
                    not stat.S_ISDIR(observed.st_mode)
                    or observed.st_uid != os.getuid()
                    or stat.S_IMODE(observed.st_mode) != 0o700
                    or os.listdir(child)
                ):
                    raise BootstrapError("retained scratch evidence changed")
            finally:
                os.close(child)
        binding = _read_private(root, "version.binding.json", controller)
        marker = _read_private(root, "version.binding.sha256", controller, 128)
        if (
            hashlib.sha256(binding).hexdigest() != profile.retained_binding_sha256
            or marker != (profile.retained_binding_sha256 + "\n").encode("ascii")
            or profile.retained_binding_sha256 != EXPECTED_BINDING_SHA256
        ):
            raise BootstrapError("retained binding changed")
        parsed = version._strict_json(binding)
        if not isinstance(parsed, dict) or set(parsed) != {
            "artifacts",
            "claim",
            "limitations",
            "prior",
            "runner",
            "schema_version",
            "snapshot",
            "source",
            "version",
        }:
            raise BootstrapError("retained binding structure changed")
        artifacts = parsed["artifacts"]
        limitations = parsed["limitations"]
        prior = parsed["prior"]
        runner = parsed["runner"]
        source = parsed["source"]
        snapshot = parsed["snapshot"]
        observed = parsed["version"]
        if (
            parsed["claim"] != "snapshot-version-recovery"
            or parsed["schema_version"] != 1
            or not isinstance(artifacts, dict)
            or set(artifacts) != RETAINED_ARTIFACTS
            or not isinstance(limitations, dict)
            or limitations
            != {
                "metadata_advance_authorized": False,
                "network_absence_os_enforced": False,
                "prior_inventory_executable_version_bound": False,
                "provider_backend_proven": False,
            }
            or not isinstance(prior, dict)
            or set(prior) != {"binding_sha256", "root_mutated"}
            or not version._is_sha256(prior.get("binding_sha256", ""))
            or prior.get("root_mutated") is not False
            or not isinstance(runner, dict)
            or set(runner) != {"byte_count", "sha256"}
            or type(runner.get("byte_count")) is not int
            or runner["byte_count"] <= 0
            or not version._is_sha256(runner.get("sha256", ""))
            or not isinstance(source, dict)
            or set(source) != {"post", "pre", "sha256"}
            or not isinstance(snapshot, dict)
            or set(snapshot) != {"post", "pre", "sha256"}
            or source["pre"] != source["post"]
            or snapshot["pre"] != snapshot["post"]
            or source["sha256"] != EXPECTED_SOURCE_SHA256
            or snapshot["sha256"] != EXPECTED_SOURCE_SHA256
            or not isinstance(observed, dict)
            or set(observed)
            != {
                "exit",
                "expected",
                "logical_argv",
                "logical_argv_sha256",
                "observed",
                "popen_count",
                "stderr_limit",
                "stdout_limit",
                "timeout_seconds",
            }
        ):
            raise BootstrapError("retained binding claim changed")
        logical_argv = [profile.retained_source_path, "--version"]
        if (
            observed["exit"] != 0
            or observed["expected"] != EXPECTED_VERSION
            or observed["logical_argv"] != logical_argv
            or observed["logical_argv_sha256"] != _logical_argv_sha256(logical_argv)
            or observed["observed"] != EXPECTED_VERSION
            or observed["popen_count"] != 1
            or observed["stderr_limit"] != version.STREAM_LIMIT
            or observed["stdout_limit"] != version.STREAM_LIMIT
            or observed["timeout_seconds"] != version.WALL_SECONDS
        ):
            raise BootstrapError("retained version observation changed")
        source_identity = version.FileIdentity.from_mapping(source["pre"])
        snapshot_identity = version.FileIdentity.from_mapping(snapshot["pre"])
        for identity, mode in ((source_identity, 0o755), (snapshot_identity, 0o500)):
            if (
                identity.uid != os.getuid()
                or identity.mode != mode
                or identity.nlink != 1
                or identity.size != EXPECTED_SIZE
            ):
                raise BootstrapError("retained executable policy changed")
        runner_bytes = _read_private(root, "runner.py", controller, MODULE_LIMIT)
        runner_marker = _read_private(root, "runner.py.sha256", controller, 128)
        stdout = _read_private(root, "version.stdout", controller, version.STREAM_LIMIT)
        stderr = _read_private(root, "version.stderr", controller, version.STREAM_LIMIT)
        summary_bytes = _read_private(root, "version.summary.json", controller)
        for name, data in (
            ("runner.py", runner_bytes),
            ("version.stdout", stdout),
            ("version.stderr", stderr),
            ("version.summary.json", summary_bytes),
        ):
            if artifacts[name] != hashlib.sha256(data).hexdigest():
                raise BootstrapError("retained artifact binding changed")
        if (
            len(runner_bytes) != runner["byte_count"]
            or hashlib.sha256(runner_bytes).hexdigest() != runner["sha256"]
            or runner_marker != (runner["sha256"] + "\n").encode("ascii")
            or stdout != version.EXPECTED_STDOUT
            or stderr != b""
        ):
            raise BootstrapError("retained observation artifact changed")
        summary = version._strict_json(summary_bytes)
        if (
            not isinstance(summary, dict)
            or set(summary)
            != {
                "call_count",
                "child_exit",
                "claim",
                "elapsed_ms",
                "logical_argv_sha256",
                "schema_version",
                "status",
                "stderr_bytes",
                "stdout_bytes",
                "timeout",
            }
            or summary["call_count"] != 1
            or summary["child_exit"] != 0
            or summary["claim"] != "snapshot-version-recovery"
            or type(summary["elapsed_ms"]) is not int
            or summary["elapsed_ms"] < 0
            or summary["logical_argv_sha256"] != observed["logical_argv_sha256"]
            or summary["schema_version"] != 1
            or summary["status"] != "accepted"
            or summary["stderr_bytes"] != 0
            or summary["stdout_bytes"] != len(version.EXPECTED_STDOUT)
            or summary["timeout"] is not False
        ):
            raise BootstrapError("retained summary changed")
        source_parent, source_fd = _open_attested(
            profile.retained_source_path,
            source_identity,
            EXPECTED_SOURCE_SHA256,
            0o755,
            controller,
        )
        try:
            snapshot_parent, snapshot_fd = _open_attested(
                profile.retained_snapshot_path,
                snapshot_identity,
                EXPECTED_SOURCE_SHA256,
                0o500,
                controller,
            )
        except BaseException:
            os.close(source_fd)
            os.close(source_parent)
            raise
        return source_parent, source_fd, snapshot_parent, snapshot_fd, source_identity, snapshot_identity
    finally:
        os.close(root)


def _copy_held(
    ledger: OwnershipLedger,
    parent: tuple[str, ...],
    name: str,
    held: int,
    size: int,
    mode: int,
    fsync: Callable[[int], None],
    controller: SignalController,
) -> version.FileIdentity:
    controller.poll()
    parent_fd = ledger.open_directory(parent)
    temporary = "." + name + "." + secrets.token_hex(12) + ".tmp"
    temporary_path = parent + (temporary,)
    final_path = parent + (name,)
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | version.CLOEXEC | version.NOFOLLOW,
            mode,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, mode)
        created = OwnedIdentity.from_stat(os.fstat(descriptor))
        if (
            created.kind != "regular"
            or created.uid != os.getuid()
            or created.mode != mode
            or created.nlink != 1
        ):
            raise BootstrapError("bootstrap executable staging changed")
        ledger.register_file(temporary_path, created)
        controller.poll()
        os.lseek(held, 0, os.SEEK_SET)
        remaining = size
        digest = hashlib.sha256()
        while remaining:
            controller.poll()
            block = os.read(held, min(1024 * 1024, remaining))
            if not block:
                raise BootstrapError("retained executable copy is short")
            digest.update(block)
            _write_all(descriptor, block, controller)
            remaining -= len(block)
        if os.read(held, 1) != b"" or digest.hexdigest() != EXPECTED_SOURCE_SHA256:
            raise BootstrapError("retained executable copy changed")
        fsync(descriptor)
        controller.poll()
        staged = OwnedIdentity.from_stat(os.fstat(descriptor))
        if staged != created:
            raise BootstrapError("bootstrap executable staging changed")
        os.close(descriptor)
        descriptor = -1
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BootstrapError("bootstrap executable target exists")
        ledger.reserve(final_path)
        os.link(
            temporary,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        linked = staged.with_nlink(2)
        try:
            ledger.register_linked_file(temporary_path, final_path, staged)
            if (
                not _exact_identity_at(parent_fd, temporary, linked)
                or not _exact_identity_at(parent_fd, name, linked)
            ):
                raise BootstrapError("bootstrap executable link changed")
            os.unlink(temporary, dir_fd=parent_fd)
            ledger.normalize_linked_file(temporary_path, final_path, staged)
        except BaseException:
            ledger.recover_link_transition(
                parent_fd,
                temporary_path,
                final_path,
                temporary,
                name,
                staged,
            )
            raise
        ledger.bind_content(final_path, size, EXPECTED_SOURCE_SHA256)
        fsync(parent_fd)
        descriptor = os.open(
            name,
            os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW,
            dir_fd=parent_fd,
        )
        try:
            observed = os.fstat(descriptor)
            if (
                OwnedIdentity.from_stat(observed) != staged
                or not _exact_identity_at(parent_fd, name, staged)
            ):
                raise BootstrapError("bootstrap executable publication changed")
        finally:
            os.close(descriptor)
            descriptor = -1
        controller.poll()
        return version.FileIdentity.from_stat(observed)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        ledger.release_absent(final_path)
        os.close(parent_fd)


def _revalidate_scratch(
    ledger: OwnershipLedger,
    parent: tuple[str, ...],
    expected: dict[str, OwnedIdentity],
) -> None:
    if tuple(expected) != SCRATCH_NAMES:
        raise BootstrapError("bootstrap scratch inventory changed")
    for name in SCRATCH_NAMES:
        relative = parent + (name,)
        descriptor = ledger.open_directory(relative)
        try:
            if OwnedIdentity.from_stat(os.fstat(descriptor)) != expected[name] or os.listdir(descriptor):
                raise BootstrapError("bootstrap scratch changed")
        finally:
            os.close(descriptor)


def _capture(
    process: subprocess.Popen[bytes],
    deadline: float,
    controller: SignalController,
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise BootstrapError("bootstrap process did not expose bounded streams")
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
            controller.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BootstrapError("bootstrap process timed out")
            for key, _mask in selector.select(min(remaining, 0.05)):
                controller.poll()
                stream, captured = buffers[key.fd]
                block = os.read(
                    key.fd, min(64, version.STREAM_LIMIT + 1 - len(captured))
                )
                controller.poll()
                if not block:
                    selector.unregister(key.fd)
                    stream.close()
                    continue
                captured.extend(block)
                if len(captured) > version.STREAM_LIMIT:
                    raise BootstrapError("bootstrap output exceeded its bound")
    controller.poll()
    return bytes(buffers[stdout_descriptor][1]), bytes(buffers[stderr_descriptor][1])


def _validate_process(process: subprocess.Popen[bytes]) -> int:
    pid = process.pid
    if type(pid) is not int or pid <= 1 or pid == os.getpgrp() or process.returncode is not None:
        raise BootstrapError("bootstrap process group is unsafe")
    return pid


def _group_exists(pgid: int, calls: object) -> bool:
    if type(pgid) is not int or pgid <= 1 or pgid == os.getpgrp():
        raise BootstrapError("bootstrap process group is unsafe")
    try:
        calls.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _finish_process_group(process: subprocess.Popen[bytes], calls: object) -> int:
    """Close the reserved group while its leader is unreaped, then reap once."""

    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
    try:
        pgid = _validate_process(process)
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
            return process.wait(timeout=0.75)
        except subprocess.TimeoutExpired as exc:
            raise BootstrapError("bootstrap process could not be reaped") from exc
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, blocked)


def _pending_signal() -> Optional[int]:
    pending = set(signal.sigpending()).intersection(version.LIFECYCLE_SIGNALS)
    for item in version.LIFECYCLE_SIGNALS:
        if item in pending:
            return signal.sigwait({item})
    return None


def _validate_dependency() -> bytes:
    path = SCRIPT_DIRECTORY / "version_attestation_runner.py"
    value = path.lstat()
    if (
        path.resolve(strict=True) != path
        or not stat.S_ISREG(value.st_mode)
        or value.st_size != VERSION_RUNNER_BYTES
        or stat.S_IMODE(value.st_mode) & 0o022
    ):
        raise BootstrapError("version dependency identity changed")
    descriptor = os.open(str(path), os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW)
    try:
        data = os.read(descriptor, VERSION_RUNNER_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(data) != VERSION_RUNNER_BYTES
        or hashlib.sha256(data).hexdigest() != VERSION_RUNNER_SHA256
        or version.FileIdentity.from_stat(value) != version.FileIdentity.from_stat(after)
        or Path(version.__file__).resolve(strict=True) != path
    ):
        raise BootstrapError("version dependency bytes changed")
    return data


def _module_source() -> bytes:
    path = Path(__file__).resolve(strict=True)
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or stat.S_IMODE(value.st_mode) & 0o022:
        raise BootstrapError("bootstrap source identity changed")
    descriptor = os.open(str(path), os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW)
    try:
        data = os.read(descriptor, MODULE_LIMIT + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not data
        or len(data) > MODULE_LIMIT
        or b"\x00" in data
        or version.FileIdentity.from_stat(value) != version.FileIdentity.from_stat(after)
    ):
        raise BootstrapError("bootstrap source changed while read")
    return data


def _atomic_exit(code: int, descriptor: int, message: bytes) -> NoReturn:
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


def validate_source_contract(data: bytes) -> dict[str, object]:
    """Validate reviewed production structure; this is not hostile-source proof."""

    try:
        text = data.decode("utf-8", "strict")
        tree = ast.parse(text, filename="<version-bootstrap-runner>", mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise BootstrapError("bootstrap source is invalid") from exc
    module_pin = None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "MODULE_AST_SHA256"
        ):
            module_pin = node
            break
    if module_pin is None:
        raise BootstrapError("bootstrap module authority is missing")
    module_pin.value = ast.Constant(value="PINNED-MODULE-AST")
    module_digest = hashlib.sha256(
        ast.dump(tree, include_attributes=False).encode("utf-8")
    ).hexdigest()
    if module_digest != MODULE_AST_SHA256:
        raise BootstrapError("bootstrap module structure changed")
    runtime_assignments = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"RUNTIME_MAJOR", "RUNTIME_MINOR"}
    }
    if (
        set(runtime_assignments) != {"RUNTIME_MAJOR", "RUNTIME_MINOR"}
        or ast.dump(runtime_assignments["RUNTIME_MAJOR"]) != "Constant(value=3)"
        or ast.dump(runtime_assignments["RUNTIME_MINOR"]) != "Constant(value=9)"
    ):
        raise BootstrapError("bootstrap runtime ABI changed")
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    production_nodes = {
        name: classes[name] if name in classes else functions[name]
        for name in PRODUCTION_AST_SHA256
        if name in classes or name in functions
    }
    if set(production_nodes) != set(PRODUCTION_AST_SHA256):
        raise BootstrapError("bootstrap production graph is incomplete")
    for name, expected in PRODUCTION_AST_SHA256.items():
        observed = hashlib.sha256(
            ast.dump(production_nodes[name], include_attributes=False).encode("utf-8")
        ).hexdigest()
        if observed != expected:
            raise BootstrapError("bootstrap production graph changed")
    imports = [
        (item.name, item.asname)
        for node in tree.body
        if isinstance(node, ast.Import)
        for item in node.names
    ]
    if sorted(item for item in imports if item[0] in {"os", "subprocess"}) != [
        ("os", None),
        ("subprocess", None),
    ] or any(name in {"asyncio", "builtins", "importlib"} for name, _alias in imports):
        raise BootstrapError("bootstrap process imports changed")
    launch_names = {"call", "check_call", "check_output", "getoutput", "getstatusoutput", "Popen", "run"}
    os_launch_names = {"fork", "forkpty", "popen", "posix_spawn", "posix_spawnp", "system"}

    def launch_reference(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and (
                (node.value.id == "calls" and node.attr == "popen")
                or (node.value.id == "subprocess" and node.attr in launch_names)
                or (
                    node.value.id == "os"
                    and (node.attr in os_launch_names or node.attr.startswith(("exec", "spawn")))
                )
            )
        )

    launch_calls = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and launch_reference(node.func)
    ]
    run_node = functions.get("_execute_bootstrap")
    if len(launch_calls) != 1 or run_node is None or launch_calls[0] not in tuple(ast.walk(run_node)):
        raise BootstrapError("bootstrap must contain one child launch")
    launch = launch_calls[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "subprocess"}:
            raise BootstrapError("bootstrap process import alias changed")
        if isinstance(node, ast.Attribute) and (
            node.attr in {"__dict__", "__getattribute__"} or launch_reference(node)
        ):
            if node is not launch.func and launch_reference(node):
                if not (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "subprocess"
                    and node.attr == "Popen"
                    and isinstance(node.ctx, ast.Load)
                    and any(node in tuple(ast.walk(annotation)) for annotation in (
                        functions["_capture"].args.args[0].annotation,
                        functions["_validate_process"].args.args[0].annotation,
                        functions["_finish_process_group"].args.args[0].annotation,
                        next(
                            item.annotation
                            for item in ast.walk(run_node)
                            if isinstance(item, ast.AnnAssign)
                            and isinstance(item.target, ast.Name)
                            and item.target.id == "process"
                        ),
                    ))
                ):
                    raise BootstrapError("bootstrap indirect process authority changed")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "__import__", "compile", "eval", "exec", "getattr", "globals", "locals", "vars"
        }:
            raise BootstrapError("bootstrap dynamic authority changed")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            if launch_reference(value):
                raise BootstrapError("bootstrap assigned process alias changed")
    keywords = {item.arg: item.value for item in launch.keywords if item.arg is not None}
    if not (
        len(launch.args) == 1
        and isinstance(launch.args[0], ast.Name)
        and launch.args[0].id == "argv"
        and len(launch.keywords) == 7
        and set(keywords)
        == {"cwd", "env", "executable", "start_new_session", "stderr", "stdin", "stdout"}
        and ast.dump(keywords["executable"])
        == "Attribute(value=Name(id='recovery_profile', ctx=Load()), attr='snapshot_path', ctx=Load())"
        and isinstance(keywords["env"], ast.Name)
        and keywords["env"].id == "environment"
        and ast.dump(keywords["stdin"])
        == "Attribute(value=Name(id='subprocess', ctx=Load()), attr='DEVNULL', ctx=Load())"
        and ast.dump(keywords["stdout"])
        == "Attribute(value=Name(id='subprocess', ctx=Load()), attr='PIPE', ctx=Load())"
        and ast.dump(keywords["stderr"])
        == "Attribute(value=Name(id='subprocess', ctx=Load()), attr='PIPE', ctx=Load())"
        and isinstance(keywords["start_new_session"], ast.Constant)
        and keywords["start_new_session"].value is True
    ):
        raise BootstrapError("bootstrap child contract changed")
    assignments = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"argv", "environment"}
    ]
    if [node.targets[0].id for node in assignments].count("argv") != 1 or [
        node.targets[0].id for node in assignments
    ].count("environment") != 1:
        raise BootstrapError("bootstrap launch inputs changed")
    argv_assignment = next(node for node in assignments if node.targets[0].id == "argv")
    environment_assignment = next(node for node in assignments if node.targets[0].id == "environment")
    if ast.dump(argv_assignment.value) != (
        "List(elts=[Attribute(value=Name(id='recovery_profile', ctx=Load()), "
        "attr='source_path', ctx=Load()), Constant(value='--version')], ctx=Load())"
    ) or not isinstance(environment_assignment.value, ast.Dict):
        raise BootstrapError("bootstrap launch inputs changed")
    expected_environment = (
        "HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
        "LANG", "LC_ALL", "NO_COLOR", "TERM", "PATH",
    )
    if tuple(
        key.value if isinstance(key, ast.Constant) else None
        for key in environment_assignment.value.keys
    ) != expected_environment:
        raise BootstrapError("bootstrap environment changed")
    if any(
        isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete))
        and any(
            isinstance(item, ast.Name)
            and item.id in {"argv", "environment"}
            and isinstance(item.ctx, (ast.Store, ast.Del))
            for item in ast.walk(node)
        )
        and node not in {argv_assignment, environment_assignment}
        for node in ast.walk(run_node)
    ):
        raise BootstrapError("bootstrap launch input mutation changed")
    main_node = functions.get("main")
    atomic_node = functions.get("_atomic_exit")
    if (
        main_node is None
        or atomic_node is None
        or any(isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) for node in ast.walk(main_node))
        or any(
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "SystemExit"
            for node in ast.walk(tree)
        )
    ):
        raise BootstrapError("bootstrap production exit authority changed")
    exit_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "_exit"
    ]
    if (
        len(exit_calls) != 2
        or not any(node in tuple(ast.walk(atomic_node)) for node in exit_calls)
        or not any(node in tuple(ast.walk(run_node)) for node in exit_calls)
    ):
        raise BootstrapError("bootstrap process-owned exit changed")
    internal_names = set(PRODUCTION_AST_SHA256)
    graph = {
        name: tuple(
            sorted(
                {
                    node.func.id
                    for node in ast.walk(owner)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in internal_names
                    and node.func.id != name
                }
            )
        )
        for name, owner in production_nodes.items()
    }
    if graph != PRODUCTION_GRAPH:
        raise BootstrapError("bootstrap production call graph changed")
    for start in graph:
        stack = [(start, ())]
        while stack:
            current, ancestors = stack.pop()
            if current in ancestors:
                raise BootstrapError("bootstrap production recursion changed")
            stack.extend((child, ancestors + (current,)) for child in graph[current])
    return {"byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest(), "status": "accepted"}


def _execute_bootstrap(
    profile: BootstrapProfile,
    lifecycle: LifecycleState,
    *,
    calls: object = version.REAL_CALLS,
    module_source: Optional[bytes] = None,
    process_owned: bool,
) -> dict[str, object]:
    """Execute one active lifecycle; production never returns through Python."""

    controller = lifecycle.controller
    ledger: Optional[OwnershipLedger] = None
    source_parent = source_fd = snapshot_parent = snapshot_fd = None
    process: Optional[subprocess.Popen[bytes]] = None
    process_active = False
    completion_linearized = False
    original_error: Optional[BaseException] = None
    result: Optional[dict[str, object]] = None

    try:
        controller.poll()
        source = _module_source() if module_source is None else module_source
        controller.poll()
        validate_source_contract(source)
        controller.poll()
        _validate_dependency()
        controller.poll()
        (
            source_parent,
            source_fd,
            snapshot_parent,
            snapshot_fd,
            retained_source_identity,
            retained_snapshot_identity,
        ) = _validate_retained(profile, controller)
        controller.poll()
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        try:
            parent_path, root_name = os.path.split(profile.bootstrap_root)
            ledger = OwnershipLedger.create(parent_path, root_name)
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        source_new = _copy_held(
            ledger,
            (),
            "agy.source",
            source_fd,
            EXPECTED_SIZE,
            0o755,
            calls.fsync,
            controller,
        )
        controller.poll()
        prior_name = "agy-version-attestation.bootstrap"
        ledger.create_directory((), prior_name)
        controller.poll()
        prior = (prior_name,)
        snapshot_new = _copy_held(
            ledger,
            prior,
            "agy.snapshot",
            snapshot_fd,
            EXPECTED_SIZE,
            0o500,
            calls.fsync,
            controller,
        )
        controller.poll()
        scratch = {}
        for name in SCRATCH_NAMES:
            scratch[name] = ledger.create_directory(prior, name)
            controller.poll()
        controller.poll()
        prior_path = str(Path(profile.bootstrap_root) / prior_name)
        recovery_profile = version.AttestationProfile(
            prior_binding_sha256="0" * 64,
            prior_root=prior_path,
            snapshot_identity=snapshot_new,
            snapshot_path=str(Path(prior_path) / "agy.snapshot"),
            source_identity=source_new,
            source_path=str(Path(profile.bootstrap_root) / "agy.source"),
            source_sha256=EXPECTED_SOURCE_SHA256,
            temp_parent=profile.bootstrap_root,
        )
        argv = [recovery_profile.source_path, "--version"]
        environment = {
            "HOME": str(Path(prior_path) / "home"),
            "TMPDIR": str(Path(prior_path) / "tmp"),
            "XDG_CONFIG_HOME": str(Path(prior_path) / "xdg-config"),
            "XDG_CACHE_HOME": str(Path(prior_path) / "xdg-cache"),
            "XDG_STATE_HOME": str(Path(prior_path) / "xdg-state"),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PATH": "/usr/bin:/bin",
        }
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        try:
            controller.merge_pending()
            controller.poll()
            _revalidate_scratch(ledger, prior, scratch)
            process = calls.popen(
                argv,
                executable=recovery_profile.snapshot_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(Path(prior_path) / "cwd"),
                env=environment,
                start_new_session=True,
            )
            _validate_process(process)
            process_active = True
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        started = time.monotonic()
        stdout, stderr = _capture(
            process, started + version.WALL_SECONDS, controller
        )
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        try:
            controller.merge_pending()
            exit_code = _finish_process_group(process, calls)
            process_active = False
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        if exit_code != 0 or stdout != version.EXPECTED_STDOUT or stderr != b"":
            raise BootstrapError("bootstrap version result changed")
        _revalidate_scratch(ledger, prior, scratch)
        controller.poll()
        retained_source_post = _verify_attested_path(
            source_parent,
            profile.retained_source_path,
            source_fd,
            retained_source_identity,
            EXPECTED_SOURCE_SHA256,
            controller,
        )
        controller.poll()
        retained_snapshot_post = _verify_attested_path(
            snapshot_parent,
            profile.retained_snapshot_path,
            snapshot_fd,
            retained_snapshot_identity,
            EXPECTED_SOURCE_SHA256,
            controller,
        )
        controller.poll()
        root_fd = ledger.open_directory(())
        prior_fd = ledger.open_directory(prior)
        try:
            held_source = os.open(
                "agy.source", os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW, dir_fd=root_fd
            )
            held_snapshot = os.open(
                "agy.snapshot", os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW, dir_fd=prior_fd
            )
            try:
                source_post = _verify_attested_path(
                    root_fd,
                    recovery_profile.source_path,
                    held_source,
                    source_new,
                    EXPECTED_SOURCE_SHA256,
                    controller,
                )
                controller.poll()
                snapshot_post = _verify_attested_path(
                    prior_fd,
                    recovery_profile.snapshot_path,
                    held_snapshot,
                    snapshot_new,
                    EXPECTED_SOURCE_SHA256,
                    controller,
                )
                controller.poll()
            finally:
                os.close(held_snapshot)
                os.close(held_source)
        finally:
            os.close(prior_fd)
            os.close(root_fd)
        prior_publisher = OwnedPublisher(ledger, prior, calls.fsync, controller)
        root_publisher = OwnedPublisher(ledger, (), calls.fsync, controller)
        stdout_sha = prior_publisher.publish("version.stdout", stdout)
        stderr_sha = prior_publisher.publish("version.stderr", stderr)
        logical_sha = _logical_argv_sha256(argv)
        summary = {
            "call_count": 1,
            "child_exit": exit_code,
            "claim": "snapshot-version-only",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "logical_argv_sha256": logical_sha,
            "schema_version": 1,
            "status": "accepted",
            "stderr_bytes": len(stderr),
            "stdout_bytes": len(stdout),
            "timeout": False,
        }
        summary_bytes = _canonical_json(summary)
        summary_sha = prior_publisher.publish("version.summary.json", summary_bytes)
        prior_publisher.publish("source.pre.json", _canonical_json(source_new.as_dict()))
        prior_publisher.publish("source.post.json", _canonical_json(source_post.as_dict()))
        prior_publisher.publish("snapshot.pre.json", _canonical_json(snapshot_new.as_dict()))
        prior_publisher.publish("snapshot.post.json", _canonical_json(snapshot_post.as_dict()))
        binding = {
            "artifacts": {
                "version.stderr": stderr_sha,
                "version.stdout": stdout_sha,
                "version.summary.json": summary_sha,
            },
            "bootstrap": {
                "claim": "snapshot-version-bootstrap",
                "network_absence_os_enforced": False,
                "new_snapshot_post": snapshot_post.as_dict(),
                "new_source_post": source_post.as_dict(),
                "retained_binding_sha256": EXPECTED_BINDING_SHA256,
                "retained_snapshot_post": retained_snapshot_post.as_dict(),
                "retained_source_post": retained_source_post.as_dict(),
            },
            "claim": "snapshot-version-only",
            "inventory": {"executable_version_bound": False},
            "snapshot": {
                "post": snapshot_post.as_dict(),
                "pre": snapshot_new.as_dict(),
                "sha256": EXPECTED_SOURCE_SHA256,
            },
            "source": {
                "post": source_post.as_dict(),
                "pre": source_new.as_dict(),
                "sha256": EXPECTED_SOURCE_SHA256,
            },
            "version": {
                "call_count": 1,
                "exit": exit_code,
                "expected": EXPECTED_VERSION,
                "logical_argv": argv,
                "logical_argv_sha256": logical_sha,
                "observed": EXPECTED_VERSION,
                "popen_count": 1,
            },
        }
        binding_bytes = _canonical_json(binding)
        binding_sha = prior_publisher.publish("version.binding.json", binding_bytes)
        prepared = dataclasses.asdict(
            dataclasses.replace(recovery_profile, prior_binding_sha256=binding_sha)
        )
        profile_bytes = _canonical_json(prepared)
        profile_sha = root_publisher.publish("version.recovery.profile.json", profile_bytes)
        controller.poll()
        prior_publisher.publish(
            "version.binding.sha256", (binding_sha + "\n").encode("ascii")
        )
        controller.merge_pending()
        controller.poll()
        _revalidate_scratch(ledger, prior, scratch)
        controller.merge_pending()
        controller.poll()
        ledger.validate(controller)
        controller.merge_pending()
        controller.poll()
        version._validate_prior(version.AttestationProfile.from_bytes(profile_bytes))
        controller.merge_pending()
        controller.poll()
        ledger.close()
        controller.merge_pending()
        controller.poll()
        result = {
            "artifact_root": profile.bootstrap_root,
            "binding_sha256": binding_sha,
            "call_count": 1,
            "claim": "snapshot-version-bootstrap",
            "profile_sha256": profile_sha,
            "status": "accepted",
        }
        if process_owned:
            sys.stdout.buffer.flush()
            _write_all(
                sys.stdout.buffer.fileno(), _canonical_json(result), controller
            )
            sys.stdout.buffer.flush()
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        controller.merge_pending()
        controller.poll()
        completion_linearized = True
    except BaseException as exc:
        original_error = exc
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        if process_owned and completion_linearized:
            os._exit(0)
        cleanup_error: Optional[BaseException] = None
        if not completion_linearized:
            controller.merge_pending()
            if process is not None and process_active:
                try:
                    _finish_process_group(process, calls)
                    process_active = False
                except BaseException as exc:
                    cleanup_error = exc
            if ledger is not None:
                try:
                    ledger.rollback()
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            controller.merge_pending()
        for descriptor in (snapshot_fd, snapshot_parent, source_fd, source_parent):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if ledger is not None:
            try:
                ledger.release()
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if process_owned:
            controller.merge_pending()
            selected = controller.choose()
            status = 128 + selected if selected is not None else 2
            message = (
                b"version bootstrap: interrupted\n"
                if selected is not None
                else b"version bootstrap: rejected\n"
            )
            _atomic_exit(status, sys.stderr.buffer.fileno(), message)
        for item in reversed(lifecycle.installed_handlers):
            try:
                signal.signal(item, lifecycle.old_handlers[item])
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if not completion_linearized:
            controller.merge_pending()
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, lifecycle.entry_mask)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if original_error is None and cleanup_error is not None:
            original_error = cleanup_error
    selected = controller.choose()
    if selected is not None and not completion_linearized:
        raise BootstrapInterrupted(selected)
    if original_error is not None:
        raise original_error
    if result is None:
        raise BootstrapError("bootstrap did not produce a result")
    return result


def run_bootstrap(
    profile: BootstrapProfile,
    *,
    calls: object = version.REAL_CALLS,
    module_source: Optional[bytes] = None,
) -> dict[str, object]:
    """Embedded test API; final-snapshot-absent signals become caller-owned."""

    if not _runtime_supported():
        raise BootstrapError("version bootstrap runtime is unsupported")
    lifecycle = _acquire_lifecycle()
    try:
        _activate_lifecycle(lifecycle)
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        lifecycle.controller.merge_pending()
        for item in reversed(lifecycle.installed_handlers):
            signal.signal(item, lifecycle.old_handlers[item])
        lifecycle.controller.merge_pending()
        signal.pthread_sigmask(signal.SIG_SETMASK, lifecycle.entry_mask)
        selected = lifecycle.controller.choose()
        if selected is not None:
            raise BootstrapInterrupted(selected)
        raise
    return _execute_bootstrap(
        profile,
        lifecycle,
        calls=calls,
        module_source=module_source,
        process_owned=False,
    )


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
    return bytes(data)


def main(argv: Sequence[str]) -> NoReturn:
    if not _runtime_supported():
        _atomic_exit(2, sys.stderr.buffer.fileno(), b"version bootstrap: rejected\n")
    try:
        lifecycle = _acquire_lifecycle()
    except BaseException:
        _atomic_exit(2, sys.stderr.buffer.fileno(), b"version bootstrap: rejected\n")
    usage = False
    diagnostic = b"version bootstrap: rejected\n"
    try:
        _activate_lifecycle(lifecycle)
        if list(argv) != ["--bootstrap-version"]:
            usage = True
            diagnostic = b"version bootstrap: invalid invocation\n"
            raise BootstrapError("invalid invocation")
        startup = version._production_startup_evaluation()
        lifecycle.controller.poll()
        if not startup.accepted:
            usage = True
            diagnostic = version._startup_diagnostic(startup)
            raise BootstrapError("production startup rejected")
        data = _read_stdin(lifecycle.controller)
        if len(data) > PROFILE_LIMIT:
            raise BootstrapError("bootstrap profile is oversized")
        profile = BootstrapProfile.from_bytes(data)
        lifecycle.controller.poll()
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        lifecycle.controller.merge_pending()
        selected = lifecycle.controller.choose()
        if selected is not None:
            _atomic_exit(
                128 + selected,
                sys.stderr.buffer.fileno(),
                b"version bootstrap: interrupted\n",
            )
        _atomic_exit(
            64 if usage else 2, sys.stderr.buffer.fileno(), diagnostic
        )
    _execute_bootstrap(
        profile,
        lifecycle,
        module_source=_module_source(),
        process_owned=True,
    )
    _atomic_exit(2, sys.stderr.buffer.fileno(), b"version bootstrap: rejected\n")


if __name__ == "__main__":
    main(sys.argv[1:])
