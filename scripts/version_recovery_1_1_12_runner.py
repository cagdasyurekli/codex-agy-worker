#!/usr/bin/env python3
"""Fixed 1.1.12 recovery runner for one phase-one snapshot-backed version observation.

This independent runner consumes the unchanged AttestationProfile schema, requires the exact phase-one 1.1.12 prior, and owns one static snapshot-backed version Popen. It publishes non-authorizing snapshot-version-recovery evidence only.
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
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn, Optional, Sequence


sys.dont_write_bytecode = True

RUNTIME_MAJOR = 3
RUNTIME_MINOR = 9
MODULE_AST_SHA256 = "648b7a2f0fceeea3541c872bd9b129ae77d97ab3c03b922c67668ce2418bd1c2"
EXPECTED_VERSION = "1.1.12"
EXPECTED_STDOUT = b"1.1.12\n"
EXPECTED_SOURCE_SHA256 = "c8fd3c0016e101689f923f82da1c068b0e6dce3abcb0089e282742693ad4d344"
INITIAL_BOOTSTRAP_RUNNER_BYTES = 44_678
INITIAL_BOOTSTRAP_RUNNER_SHA256 = "b547c9a207c2ed67d761ac7067e9ad3f34499948722d0d1a27dccdd1b7508f50"
EXPECTED_PRIOR_BINDING_SHA256 = "61972e2a98b4540daceda1be1e8c24b3e4449a0979299e127f87496723afb08e"
EXPECTED_PROFILE_SHA256 = "fdd00475c45165229e53f5921f03c0f20e214a994a198c79c5e72f46af09e931"
EXPECTED_PROFILE_BYTES = 954
HISTORICAL_RECOVERY_BINDING_SHA256 = "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2"
HISTORICAL_RECOVERY_SOURCE_SHA256 = "198ff7c3f6d173daa510b0814aa70c6ce14c94035bcd4707a3c0e79fa38a7bc3"
PHASE_ONE_PRIOR_AST_SHA256 = "7c0b5518b8167b85c9222453b1a0fa8dd35a9a85ed62238e0f0d2fea829d2a3a"
PHASE_ONE_BINDING_AST_SHA256 = "0a13497579c63bb743866b4b5ab5498fa150b76d88eb4fc4c889ce44e7a92894"
STREAM_LIMIT = 128
WALL_SECONDS = 3.0
PROFILE_LIMIT = 16_384
STARTUP_DIAGNOSTIC_LIMIT = 8_192
STARTUP_FAILURE_LIMIT = 32
STARTUP_COLLECTION_ERRORS = frozenset(
    {"invalid-path", "missing", "permission", "os-error", "invalid-data"}
)
LIFECYCLE_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = getattr(os, "O_DIRECTORY", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)


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
PHASE_ONE_BINDING_KEYS = frozenset(
    {
        "artifacts", "claim", "copy", "historical_recovery", "inventory",
        "limitations", "runner", "schema_version", "snapshot", "source", "version",
    }
)


class AttestationError(ValueError):
    """A fixed-profile attestation requirement failed closed."""


class AttestationInterrupted(SystemExit):
    def __init__(self, signum: int):
        super().__init__(128 + signum)
        self.signum = signum


class SignalController:
    """Latch owned lifecycle signals and select by fixed priority at checkpoints."""

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
            raise AttestationInterrupted(chosen)


@dataclass
class LifecycleState:
    controller: SignalController
    entry_mask: set[signal.Signals]
    old_handlers: dict[signal.Signals, object]
    installed_handlers: list[signal.Signals]


def _acquire_lifecycle() -> LifecycleState:
    required = ("pthread_sigmask", "sigpending", "sigwait")
    if not all(hasattr(signal, name) for name in required):
        raise AttestationError("required signal primitives are unavailable")
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


@dataclass(frozen=True)
class InterpreterNode:
    dev: int
    gid: int
    ino: int
    kind: str
    mode: int
    uid: int


@dataclass(frozen=True)
class InterpreterTrustFacts:
    alias_path: str
    alias_nodes: tuple[InterpreterNode, ...]
    alias_target: InterpreterNode
    resolved_path: str
    resolved_nodes: tuple[InterpreterNode, ...]
    resolved_target: InterpreterNode


@dataclass(frozen=True)
class InterpreterTrustFailure:
    side: str
    predicate: str
    component_index: int
    basename: str
    kind: str
    uid: int
    gid: int
    mode: str


@dataclass(frozen=True)
class InterpreterTrustEvaluation:
    accepted: bool
    alias_family: str
    resolved_family: str
    resolved_filename: str
    isolated: bool
    no_site: bool
    dont_write_bytecode: bool
    collection_error: str
    failures: tuple[InterpreterTrustFailure, ...]
    truncated: bool


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
class EvidenceFile:
    identity: FileIdentity
    sha256: str


@dataclass(frozen=True)
class PriorEvidence:
    root: FileIdentity
    directories: tuple[tuple[str, FileIdentity], ...]
    files: tuple[tuple[str, EvidenceFile], ...]


@dataclass(frozen=True)
class PublisherEvidence:
    root: FileIdentity
    scratch: tuple[tuple[str, FileIdentity], ...]
    artifacts: tuple[tuple[str, EvidenceFile], ...]


OUTPUT_SCRATCH_NAMES = ("cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state")
OUTPUT_ARTIFACT_NAMES = frozenset(
    {
        "runner.py", "runner.py.sha256", "version.binding.json",
        "version.binding.sha256", "version.stderr", "version.stdout",
        "version.summary.json",
    }
)


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
        if (
            not isinstance(value, dict)
            or set(value) != PROFILE_KEYS
            or _canonical_json(value) != data
        ):
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


def _validate_exact_profile(data: bytes) -> AttestationProfile:
    """Bind the one retained canonical profile before acquiring mutation authority."""

    if (
        type(data) is not bytes
        or len(data) != EXPECTED_PROFILE_BYTES
        or hashlib.sha256(data).hexdigest() != EXPECTED_PROFILE_SHA256
    ):
        raise AttestationError("recovery profile is not the exact reviewed initial instance")
    profile = AttestationProfile.from_bytes(data)
    if profile.prior_binding_sha256 != EXPECTED_PRIOR_BINDING_SHA256:
        raise AttestationError("recovery profile does not bind the reviewed prior")
    _validate_profile_authority(profile)
    return profile


def validate_source_contract(data: bytes) -> dict[str, object]:
    """Validate fixed production-call structure without executing source bytes."""

    try:
        text = data.decode("utf-8", "strict")
        tree = ast.parse(text, filename="<version-recovery-1-1-12-runner>", mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise AttestationError("canonical runner source is invalid") from exc
    module_pin = next(
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
    if module_pin is None:
        raise AttestationError("recovery runner module pin is missing")
    module_pin.value = ast.Constant(value="PINNED-MODULE-AST")
    module_digest = hashlib.sha256(
        ast.dump(tree, include_attributes=False).encode("utf-8")
    ).hexdigest()
    if module_digest != MODULE_AST_SHA256:
        raise AttestationError("recovery runner module graph changed")
    assignments = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id
            in {
                "EXPECTED_VERSION", "EXPECTED_STDOUT", "EXPECTED_SOURCE_SHA256",
                "INITIAL_BOOTSTRAP_RUNNER_BYTES", "INITIAL_BOOTSTRAP_RUNNER_SHA256",
                "EXPECTED_PRIOR_BINDING_SHA256", "EXPECTED_PROFILE_SHA256", "EXPECTED_PROFILE_BYTES",
                "HISTORICAL_RECOVERY_BINDING_SHA256", "HISTORICAL_RECOVERY_SOURCE_SHA256",
                "PHASE_ONE_PRIOR_AST_SHA256", "PHASE_ONE_BINDING_AST_SHA256",
            }
        ):
            assignments.setdefault(node.targets[0].id, []).append(node.value)
    if (
        set(assignments) != {
            "EXPECTED_VERSION", "EXPECTED_STDOUT", "EXPECTED_SOURCE_SHA256",
            "INITIAL_BOOTSTRAP_RUNNER_BYTES", "INITIAL_BOOTSTRAP_RUNNER_SHA256",
            "EXPECTED_PRIOR_BINDING_SHA256", "EXPECTED_PROFILE_SHA256", "EXPECTED_PROFILE_BYTES",
            "HISTORICAL_RECOVERY_BINDING_SHA256", "HISTORICAL_RECOVERY_SOURCE_SHA256",
            "PHASE_ONE_PRIOR_AST_SHA256", "PHASE_ONE_BINDING_AST_SHA256",
        }
        or any(len(values) != 1 for values in assignments.values())
        or not isinstance(assignments["EXPECTED_VERSION"][0], ast.Constant)
        or assignments["EXPECTED_VERSION"][0].value != EXPECTED_VERSION
        or not isinstance(assignments["EXPECTED_STDOUT"][0], ast.Constant)
        or assignments["EXPECTED_STDOUT"][0].value != EXPECTED_STDOUT
        or not isinstance(assignments["EXPECTED_SOURCE_SHA256"][0], ast.Constant)
        or assignments["EXPECTED_SOURCE_SHA256"][0].value != EXPECTED_SOURCE_SHA256
        or not isinstance(assignments["INITIAL_BOOTSTRAP_RUNNER_BYTES"][0], ast.Constant)
        or assignments["INITIAL_BOOTSTRAP_RUNNER_BYTES"][0].value != INITIAL_BOOTSTRAP_RUNNER_BYTES
        or not isinstance(assignments["INITIAL_BOOTSTRAP_RUNNER_SHA256"][0], ast.Constant)
        or assignments["INITIAL_BOOTSTRAP_RUNNER_SHA256"][0].value != INITIAL_BOOTSTRAP_RUNNER_SHA256
        or not isinstance(assignments["EXPECTED_PRIOR_BINDING_SHA256"][0], ast.Constant)
        or assignments["EXPECTED_PRIOR_BINDING_SHA256"][0].value != EXPECTED_PRIOR_BINDING_SHA256
        or not isinstance(assignments["EXPECTED_PROFILE_SHA256"][0], ast.Constant)
        or assignments["EXPECTED_PROFILE_SHA256"][0].value != EXPECTED_PROFILE_SHA256
        or not isinstance(assignments["EXPECTED_PROFILE_BYTES"][0], ast.Constant)
        or assignments["EXPECTED_PROFILE_BYTES"][0].value != EXPECTED_PROFILE_BYTES
        or not isinstance(assignments["HISTORICAL_RECOVERY_BINDING_SHA256"][0], ast.Constant)
        or assignments["HISTORICAL_RECOVERY_BINDING_SHA256"][0].value != HISTORICAL_RECOVERY_BINDING_SHA256
        or not isinstance(assignments["HISTORICAL_RECOVERY_SOURCE_SHA256"][0], ast.Constant)
        or assignments["HISTORICAL_RECOVERY_SOURCE_SHA256"][0].value != HISTORICAL_RECOVERY_SOURCE_SHA256
        or not isinstance(assignments["PHASE_ONE_PRIOR_AST_SHA256"][0], ast.Constant)
        or assignments["PHASE_ONE_PRIOR_AST_SHA256"][0].value != PHASE_ONE_PRIOR_AST_SHA256
        or not isinstance(assignments["PHASE_ONE_BINDING_AST_SHA256"][0], ast.Constant)
        or assignments["PHASE_ONE_BINDING_AST_SHA256"][0].value != PHASE_ONE_BINDING_AST_SHA256
    ):
        raise AttestationError("recovery runner constants changed")
    dynamic_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"getattr", "exec", "eval", "__import__"}
        and not (
            node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "os"
        )
    ]
    forbidden_imports = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(
            name.name == "version_attestation_runner" or name.name == "importlib"
            for name in node.names
        )
    ]
    if dynamic_calls or forbidden_imports:
        raise AttestationError("recovery runner added dynamic canonical authority")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if any(
            isinstance(target, ast.Name)
            and target.id in {
                "EXPECTED_VERSION", "EXPECTED_STDOUT", "EXPECTED_SOURCE_SHA256",
                "INITIAL_BOOTSTRAP_RUNNER_BYTES", "INITIAL_BOOTSTRAP_RUNNER_SHA256",
                "EXPECTED_PRIOR_BINDING_SHA256", "EXPECTED_PROFILE_SHA256", "EXPECTED_PROFILE_BYTES",
                "HISTORICAL_RECOVERY_BINDING_SHA256", "HISTORICAL_RECOVERY_SOURCE_SHA256",
                "PHASE_ONE_PRIOR_AST_SHA256", "PHASE_ONE_BINDING_AST_SHA256",
            }
            and node not in tree.body
            for target in targets
        ):
            raise AttestationError("recovery runner reassigns fixed authority")
    process_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"popen", "Popen", "run", "call", "check_call", "check_output"}
    ]
    if (
        len(process_calls) != 1
        or not isinstance(process_calls[0].func.value, ast.Name)
        or process_calls[0].func.value.id != "calls"
        or process_calls[0].func.attr != "popen"
    ):
        raise AttestationError("canonical runner must own one Popen path")
    popen_calls = process_calls
    call = process_calls[0]
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
    functions = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    run_node = functions.get("run_attestation")
    publish_node = functions.get("publish")
    rollback_node = functions.get("rollback")
    main_node = functions.get("main")
    acquire_node = functions.get("_acquire_lifecycle")
    activate_node = functions.get("_activate_lifecycle")
    write_node = functions.get("_write_all")
    if not all(
        (run_node, publish_node, rollback_node, main_node, acquire_node, activate_node, write_node)
    ):
        raise AttestationError("canonical runner lost a fixed lifecycle function")
    authority_text = ast.get_source_segment(text, functions.get("_validate_profile_authority")) or ""
    prior_node = functions.get("_validate_prior")
    binding_node = functions.get("_validate_phase_one_binding")
    if prior_node is None or binding_node is None:
        raise AttestationError("recovery runner lost phase-one validation")
    prior_ast = hashlib.sha256(
        ast.dump(prior_node, include_attributes=False).encode("utf-8")
    ).hexdigest()
    binding_ast = hashlib.sha256(
        ast.dump(binding_node, include_attributes=False).encode("utf-8")
    ).hexdigest()
    if (
        "profile.source_sha256 != EXPECTED_SOURCE_SHA256" not in authority_text
        or prior_ast != PHASE_ONE_PRIOR_AST_SHA256
        or binding_ast != PHASE_ONE_BINDING_AST_SHA256
    ):
        raise AttestationError("recovery runner lost fixed phase-one prior binding")
    acquire_text = ast.get_source_segment(text, acquire_node) or ""
    run_text = ast.get_source_segment(text, run_node) or ""
    rollback_text = ast.get_source_segment(text, rollback_node) or ""
    read_text = ast.get_source_segment(text, functions.get("_read_profile_stdin")) or ""
    main_text = ast.get_source_segment(text, main_node) or ""

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

    def blocks_owned(call: ast.Call) -> bool:
        return (
            len(call.args) == 2
            and isinstance(call.args[0], ast.Attribute)
            and call.args[0].attr == "SIG_BLOCK"
            and isinstance(call.args[1], ast.Attribute)
            and isinstance(call.args[1].value, ast.Name)
            and call.args[1].value.id == "controller"
            and call.args[1].attr == "owned"
        )

    if (
        len(named_calls(main_node, "_acquire_lifecycle")) != 1
        or len(named_calls(main_node, "_activate_lifecycle")) != 1
        or len(named_calls(main_node, "_read_profile_stdin")) != 1
        or len(named_calls(main_node, "_validate_exact_profile")) != 1
        or len(named_calls(run_node, "_acquire_lifecycle")) != 1
        or len(named_calls(run_node, "_activate_lifecycle")) != 1
        or not any(blocks_owned(call) for call in named_calls(run_node, "pthread_sigmask"))
    ):
        raise AttestationError("canonical runner lost full-lifecycle signal coverage")
    prior_calls = named_calls(run_node, "_validate_prior")
    source_opens = named_calls(run_node, "_open_attested")
    if (
        len(prior_calls) != 1
        or len(source_opens) != 2
        or prior_calls[0].lineno >= min(item.lineno for item in source_opens)
        or prior_calls[0].lineno >= popen_calls[0].lineno
    ):
        raise AttestationError("recovery runner lost phase-one validation order")
    completion_exits = [
        item
        for item in ast.walk(run_node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and isinstance(item.func.value, ast.Name)
        and item.func.value.id == "os"
        and item.func.attr == "_exit"
        and len(item.args) == 1
        and isinstance(item.args[0], ast.Constant)
        and item.args[0].value == 0
    ]
    if len(completion_exits) != 1:
        raise AttestationError("canonical runner lost process-owned completion exit")
    if (
        "old_handlers[item] is not signal.SIG_IGN" not in acquire_text
        or "if item not in entry_mask" not in acquire_text
        or "entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)" not in acquire_text
        or run_text.count("controller.merge_pending()") < 8
        or run_text.count("controller.poll()") < 13
        or run_text.count("signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)") != 5
        or not named_calls(rollback_node, "pthread_sigmask")
        or "owned = self.controller.owned" not in rollback_text
    ):
        raise AttestationError("canonical lifecycle ownership or polling changed")
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
    if (
        len(named_calls(main_node, "_production_startup_evaluation")) != 1
        or len(named_calls(main_node, "_startup_diagnostic")) != 1
    ):
        raise AttestationError("canonical runner lost isolated startup enforcement")
    lifecycle_markers = (
        "process_owned and completion_linearized",
        "_write_all(sys.stdout.buffer.fileno(), _canonical_json(result), controller)",
        "signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)",
        "os._exit(0)",
        "for item in reversed(lifecycle.installed_handlers)",
        "if not startup.accepted:",
        "data = _read_profile_stdin()",
        "profile = _validate_exact_profile(data)",
    )
    if (
        any(text.count(marker) < 1 for marker in lifecycle_markers)
        or "descriptor = sys.stdin.buffer.fileno()" not in read_text
        or "min(64 * 1024, PROFILE_LIMIT + 1 - len(data))" not in read_text
        or "if not startup.accepted:" not in main_text
        or any(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "read"
            and isinstance(item.func.value, ast.Attribute)
            and item.func.value.attr == "buffer"
            and isinstance(item.func.value.value, ast.Attribute)
            and item.func.value.value.attr == "stdin"
            for item in ast.walk(tree)
        )
        or not (
            main_text.index("startup = _production_startup_evaluation()")
            < main_text.index("if not startup.accepted:")
            < main_text.index("data = _read_profile_stdin()")
            < main_text.index("profile = _validate_exact_profile(data)")
            < main_text.index("lifecycle = _acquire_lifecycle()")
        )
    ):
        raise AttestationError("canonical completion handoff changed")
    startup_requirements = {
        "isolated_ok = type(isolated)" + " is int and isolated == 1": 1,
        "no_site_ok = type(no_site)" + " is int and no_site == 1": 1,
        "no_bytecode_ok = type(dont_write_bytecode)" + " is int and dont_write_bytecode == 1": 1,
        "if node.kind" + ' != "directory"': 1,
        "if not os.path.isabs(path)" + " or os.path.normpath(path) != path": 1,
        'if family == "unreviewed"' + ":": 1,
        "if not nodes" + " or len(nodes) != len(parts)": 1,
        "if node.mode" + " & 0o002": 1,
        'if family == "usr-bin"' + ":": 1,
        "if leaf.kind" + ' != "symlink"': 1,
        "if leaf" + " != target": 2,
        "if leaf.kind" + ' != "regular"': 2,
        "if leaf.mode" + " & 0o002": 1,
        "if leaf.mode" + " & 0o6000": 1,
        "if not leaf.mode" + " & 0o111": 1,
        "if facts.alias_target" + " != facts.resolved_target": 1,
        "os.open(resolved," + " os.O_RDONLY | CLOEXEC | NOFOLLOW)": 1,
        "if parts" + " == (": 1,
        "if xcode_root and parts[5:]" + ' == ("usr", "bin", "python3")': 1,
        "len(tail)" + " != 3": 1,
        "not _numeric_python_version" + "(tail[0])": 1,
        "if tail[2]" + ' not in {"python3", "python" + tail[0]}': 1,
        'if family == "unreviewed"' + " or component_index < 0": 1,
        "if len(failures)" + " >= STARTUP_FAILURE_LIMIT": 1,
        "_atomic_exit(64, sys.stderr.buffer.fileno(), _startup_diagnostic(startup))": 2,
        '"failures": ' + "[dataclasses.asdict(item) for item in evaluation.failures]": 1,
        'return _collection_failure("invalid-path", ' + "**flags)": 1,
        'return _collection_failure("missing", ' + "**flags)": 1,
        'return _collection_failure("permission", ' + "**flags)": 1,
        'return _collection_failure("os-error", ' + "**flags)": 1,
        'return _collection_failure("invalid-data", ' + "**flags)": 1,
    }
    if any(text.count(marker) != count for marker, count in startup_requirements.items()):
        raise AttestationError("canonical runner lost interpreter trust enforcement")
    diagnostic_only_authority = (
        "if node.uid" + " != 0",
        "if node.gid" + " != 0",
        "if leaf.uid" + " != 0",
        "if leaf.gid" + " != 0",
        "mode & " + "0o022",
    )
    if any(marker in text for marker in diagnostic_only_authority):
        raise AttestationError("diagnostic interpreter facts became trust authority")
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


def _hash_fd(
    descriptor: int, size: int, controller: Optional[SignalController] = None
) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        if controller is not None:
            controller.poll()
        block = os.read(descriptor, min(remaining, 1024 * 1024))
        if controller is not None:
            controller.poll()
        if not block:
            raise AttestationError("file ended before its attested size")
        digest.update(block)
        remaining -= len(block)
    if controller is not None:
        controller.poll()
    if os.read(descriptor, 1) != b"":
        raise AttestationError("file exceeded its attested size")
    if controller is not None:
        controller.poll()
    return digest.hexdigest()


def _write_all(
    descriptor: int, data: bytes, controller: Optional[SignalController] = None
) -> None:
    remaining = memoryview(data)
    while remaining:
        if controller is not None:
            controller.poll()
        written = os.write(descriptor, remaining)
        if controller is not None:
            controller.poll()
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

    def __init__(
        self,
        root: Path,
        calls: RunnerCalls = REAL_CALLS,
        controller: Optional[SignalController] = None,
        scratch_names: Sequence[str] = (),
    ) -> None:
        self.root = root
        self.calls = calls
        self.controller = controller
        self.root_parent_fd = _open_dir(str(root.parent))
        self.root_leaf = root.name
        self.root_fd = _open_dir(str(root))
        root_stat = os.fstat(self.root_fd)
        path_stat = os.stat(self.root_leaf, dir_fd=self.root_parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != os.getuid()
            or stat.S_IMODE(root_stat.st_mode) != 0o700
            or FileIdentity.from_stat(root_stat) != FileIdentity.from_stat(path_stat)
        ):
            raise AttestationError("artifact root is not owner-private")
        self.root_node = (root_stat.st_dev, root_stat.st_ino, root_stat.st_uid, stat.S_IMODE(root_stat.st_mode))
        self.scratch: dict[str, FileIdentity] = {}
        for name in scratch_names:
            child = os.open(name, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=self.root_fd)
            try:
                value = os.fstat(child)
                if (
                    not stat.S_ISDIR(value.st_mode)
                    or value.st_uid != os.getuid()
                    or stat.S_IMODE(value.st_mode) != 0o700
                    or os.listdir(child)
                ):
                    raise AttestationError("artifact scratch is not empty and owner-private")
                self.scratch[name] = FileIdentity.from_stat(value)
            finally:
                os.close(child)
        if set(os.listdir(self.root_fd)) != set(scratch_names):
            raise AttestationError("artifact root initial inventory changed")
        self.calls.fsync(self.root_fd)
        self.calls.fsync(self.root_parent_fd)
        self.owned: dict[str, EvidenceFile] = {}

    def close(self) -> None:
        os.close(self.root_fd)
        os.close(self.root_parent_fd)

    def rollback(self) -> None:
        owned = self.controller.owned if self.controller is not None else LIFECYCLE_SIGNALS
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, owned)
        try:
            failure: Optional[BaseException] = None
            for name, evidence in tuple(self.owned.items()):
                if self.controller is not None:
                    self.controller.merge_pending()
                try:
                    _exact_unlink(self.root_fd, name, evidence.identity.ino)
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
            if self.controller is not None:
                self.controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)

    def publish(self, name: str, data: bytes) -> str:
        if not name or "/" in name or name in (".", ".."):
            raise AttestationError("invalid artifact name")
        blocked: Optional[set[signal.Signals]] = None
        if self.controller is None:
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
            _write_all(descriptor, data, self.controller)
            self.calls.fsync(descriptor)
            if self.controller is not None:
                self.controller.poll()
            staged_raw = os.fstat(descriptor)
            staged = FileIdentity.from_stat(staged_raw)
            if (
                not stat.S_ISREG(staged_raw.st_mode)
                or staged.uid != os.getuid()
                or staged.mode != 0o600
                or staged.nlink != 1
                or staged.size != len(data)
            ):
                raise AttestationError("artifact staging identity changed")
            inode = staged.ino
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
            temporary_link = FileIdentity.from_stat(
                os.stat(temporary, dir_fd=self.root_fd, follow_symlinks=False)
            )
            final_link = FileIdentity.from_stat(
                os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            )
            if temporary_link != final_link or final_link.nlink != 2:
                raise AttestationError("artifact identity changed")
            os.unlink(temporary, dir_fd=self.root_fd)
            finalized = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=self.root_fd)
            try:
                final = FileIdentity.from_stat(os.fstat(finalized))
                digest = _hash_fd(finalized, len(data), self.controller)
                final_after = FileIdentity.from_stat(os.fstat(finalized))
            finally:
                os.close(finalized)
            if (
                final != final_after
                or final != FileIdentity.from_stat(
                    os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
                )
                or final.ino != inode
                or final.uid != os.getuid()
                or final.mode != 0o600
                or final.nlink != 1
                or final.size != len(data)
                or digest != hashlib.sha256(data).hexdigest()
            ):
                raise AttestationError("artifact final identity changed")
            self.owned[name] = EvidenceFile(final, digest)
            self.calls.fsync(self.root_fd)
            if self.controller is not None:
                self.controller.poll()
            return digest
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
                if blocked is not None:
                    signal.pthread_sigmask(signal.SIG_SETMASK, blocked)

    def validate(self, expected_artifacts: frozenset[str]) -> None:
        """Require the held root, empty scratch, and every published byte to persist."""

        root_now = os.fstat(self.root_fd)
        path_now = os.stat(self.root_leaf, dir_fd=self.root_parent_fd, follow_symlinks=False)
        node = (root_now.st_dev, root_now.st_ino, root_now.st_uid, stat.S_IMODE(root_now.st_mode))
        if (
            node != self.root_node
            or (path_now.st_dev, path_now.st_ino, path_now.st_uid, stat.S_IMODE(path_now.st_mode)) != self.root_node
            or not stat.S_ISDIR(root_now.st_mode)
            or set(self.owned) != expected_artifacts
            or set(os.listdir(self.root_fd)) != set(self.scratch).union(expected_artifacts)
        ):
            raise AttestationError("artifact root identity or inventory changed")
        for name, identity in self.scratch.items():
            child = os.open(name, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=self.root_fd)
            try:
                current = FileIdentity.from_stat(os.fstat(child))
                entries = os.listdir(child)
                if (
                    (current.dev, current.gid, current.ino, current.mode, current.uid)
                    != (identity.dev, identity.gid, identity.ino, identity.mode, identity.uid)
                    or entries
                ):
                    raise AttestationError("artifact scratch changed: " + name)
            finally:
                os.close(child)
        for name, evidence in self.owned.items():
            descriptor = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=self.root_fd)
            try:
                before = FileIdentity.from_stat(os.fstat(descriptor))
                digest = _hash_fd(descriptor, evidence.identity.size, self.controller)
                after = FileIdentity.from_stat(os.fstat(descriptor))
            finally:
                os.close(descriptor)
            path_identity = FileIdentity.from_stat(
                os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            )
            if before != evidence.identity or after != evidence.identity or path_identity != evidence.identity or digest != evidence.sha256:
                raise AttestationError("published artifact changed")

    def snapshot(self) -> PublisherEvidence:
        self.validate(frozenset(self.owned))
        return PublisherEvidence(
            FileIdentity.from_stat(os.fstat(self.root_fd)),
            tuple(sorted(self.scratch.items())),
            tuple(sorted(self.owned.items())),
        )

    def require_unchanged(self, expected: PublisherEvidence) -> None:
        self.validate(frozenset(name for name, _evidence in expected.artifacts))
        observed = PublisherEvidence(
            FileIdentity.from_stat(os.fstat(self.root_fd)),
            tuple(sorted(self.scratch.items())),
            tuple(sorted(self.owned.items())),
        )
        if observed != expected:
            raise AttestationError("artifact root changed during child execution")


def _read_at(
    parent_fd: int,
    name: str,
    cap: int,
    observed: Optional[dict[str, EvidenceFile]] = None,
) -> bytes:
    value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_nlink != 1
        or value.st_size > cap
    ):
        raise AttestationError("private evidence file is invalid")
    identity = FileIdentity.from_stat(value)
    descriptor = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
    try:
        data = os.read(descriptor, cap + 1)
        if (
            len(data) != identity.size
            or os.read(descriptor, 1) != b""
            or FileIdentity.from_stat(os.fstat(descriptor)) != identity
            or FileIdentity.from_stat(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != identity
        ):
            raise AttestationError("private evidence file changed")
        if observed is not None:
            observed[name] = EvidenceFile(identity, hashlib.sha256(data).hexdigest())
        return data
    finally:
        os.close(descriptor)


def _validate_profile_authority(profile: AttestationProfile) -> None:
    if profile.prior_binding_sha256 != EXPECTED_PRIOR_BINDING_SHA256:
        raise AttestationError("prior binding is not the exact reviewed initial instance")
    if profile.source_sha256 != EXPECTED_SOURCE_SHA256:
        raise AttestationError("recovery source is not the reviewed 1.1.12 bytes")
    if profile.prior_root != os.path.join(profile.temp_parent, "agy-version-attestation.initial"):
        raise AttestationError("prior evidence root is not bound to the private parent")
    if profile.source_path != os.path.join(profile.temp_parent, "agy.source"):
        raise AttestationError("source is not bound to the initial bridge parent")
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
    if (
        profile.source_identity.dev == profile.snapshot_identity.dev
        and profile.source_identity.ino == profile.snapshot_identity.ino
    ):
        raise AttestationError("source and snapshot are not independent copies")


def _read_canonical_json_at(
    parent_fd: int,
    name: str,
    cap: int,
    observed: Optional[dict[str, EvidenceFile]] = None,
) -> object:
    data = _read_at(parent_fd, name, cap, observed)
    value = _strict_json(data)
    if _canonical_json(value) != data:
        raise AttestationError("phase-one JSON artifact is not canonical")
    return value


def _require_identity(value: object, expected: Optional[FileIdentity] = None) -> dict[str, int]:
    identity = FileIdentity.from_mapping(value)
    result = identity.as_dict()
    if expected is not None and result != expected.as_dict():
        raise AttestationError("phase-one identity changed")
    return result


def _exact_value(value: object, expected: object) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(value) == set(expected) and all(
            _exact_value(value[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            _exact_value(item, wanted) for item, wanted in zip(value, expected)
        )
    return value == expected


def _validate_phase_one_binding(
    profile: AttestationProfile,
    descriptor: int,
    binding: object,
    observed: Optional[dict[str, EvidenceFile]] = None,
) -> None:
    """Require the complete immutable record emitted by the initial bridge."""

    if not isinstance(binding, dict) or set(binding) != PHASE_ONE_BINDING_KEYS:
        raise AttestationError("phase-one binding shape changed")
    source_pre = _read_canonical_json_at(descriptor, "source.pre.json", PROFILE_LIMIT, observed)
    source_post = _read_canonical_json_at(descriptor, "source.post.json", PROFILE_LIMIT, observed)
    snapshot_pre = _read_canonical_json_at(descriptor, "snapshot.pre.json", PROFILE_LIMIT, observed)
    snapshot_post = _read_canonical_json_at(descriptor, "snapshot.post.json", PROFILE_LIMIT, observed)
    source_identity = _require_identity(source_pre, profile.source_identity)
    if _require_identity(source_post, profile.source_identity) != source_identity:
        raise AttestationError("phase-one source post identity changed")
    snapshot_identity = _require_identity(snapshot_pre, profile.snapshot_identity)
    if _require_identity(snapshot_post, profile.snapshot_identity) != snapshot_identity:
        raise AttestationError("phase-one snapshot post identity changed")
    stdout = _read_at(descriptor, "version.stdout", STREAM_LIMIT, observed)
    stderr = _read_at(descriptor, "version.stderr", STREAM_LIMIT, observed)
    if stdout != EXPECTED_STDOUT or stderr != b"":
        raise AttestationError("phase-one streams changed")
    logical_argv = [profile.source_path, "--version"]
    logical_sha = hashlib.sha256(
        json.dumps(logical_argv, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    summary = _read_canonical_json_at(descriptor, "version.summary.json", PROFILE_LIMIT, observed)
    if (
        not isinstance(summary, dict)
        or set(summary) != {
            "call_count", "child_exit", "claim", "elapsed_ms", "logical_argv_sha256",
            "schema_version", "status", "stderr_bytes", "stdout_bytes", "timeout",
        }
        or not _exact_value(summary.get("schema_version"), 1)
        or not _exact_value(summary.get("claim"), "snapshot-version-only")
        or not _exact_value(summary.get("status"), "accepted")
        or not _exact_value(summary.get("call_count"), 1)
        or not _exact_value(summary.get("child_exit"), 0)
        or type(summary.get("elapsed_ms")) is not int or summary["elapsed_ms"] < 0
        or not _exact_value(summary.get("logical_argv_sha256"), logical_sha)
        or not _exact_value(summary.get("stdout_bytes"), len(EXPECTED_STDOUT))
        or not _exact_value(summary.get("stderr_bytes"), 0)
        or summary.get("timeout") is not False
    ):
        raise AttestationError("phase-one summary changed")
    artifacts = binding["artifacts"]
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != {"version.stderr", "version.stdout", "version.summary.json"}
        or not _exact_value(artifacts.get("version.stdout"), hashlib.sha256(stdout).hexdigest())
        or not _exact_value(artifacts.get("version.stderr"), hashlib.sha256(stderr).hexdigest())
        or not _exact_value(artifacts.get("version.summary.json"), hashlib.sha256(_canonical_json(summary)).hexdigest())
    ):
        raise AttestationError("phase-one artifact hashes changed")
    if not _exact_value(binding["claim"], "snapshot-version-only") or not _exact_value(binding["schema_version"], 1):
        raise AttestationError("phase-one binding claim changed")
    copy = binding["copy"]
    if not isinstance(copy, dict) or not _exact_value(copy, {"snapshot_post": snapshot_identity, "source_post": source_identity}):
        raise AttestationError("phase-one copy binding changed")
    historical = binding["historical_recovery"]
    if not _exact_value(historical, {
        "binding_sha256": HISTORICAL_RECOVERY_BINDING_SHA256,
        "bytes_used": False,
        "revalidated": False,
        "source_continuity_claimed": False,
        "source_sha256": HISTORICAL_RECOVERY_SOURCE_SHA256,
    }):
        raise AttestationError("phase-one historical recovery binding changed")
    if not _exact_value(binding["inventory"], {"executable_version_bound": False}):
        raise AttestationError("phase-one inventory authority changed")
    if not _exact_value(binding["limitations"], {
        "metadata_advance_authorized": False,
        "network_absence_os_enforced": False,
        "provider_backend_proven": False,
        "recovery_runner_version_reconciled": False,
    }):
        raise AttestationError("phase-one limitations changed")
    if not _exact_value(binding["runner"], {
        "byte_count": INITIAL_BOOTSTRAP_RUNNER_BYTES,
        "sha256": INITIAL_BOOTSTRAP_RUNNER_SHA256,
    }):
        raise AttestationError("phase-one runner binding changed")
    snapshot = binding["snapshot"]
    if not _exact_value(snapshot, {"post": snapshot_identity, "pre": snapshot_identity, "sha256": EXPECTED_SOURCE_SHA256}):
        raise AttestationError("phase-one snapshot binding changed")
    source = binding["source"]
    if not isinstance(source, dict) or set(source) != {"current_post", "current_pre", "post", "pre", "sha256"}:
        raise AttestationError("phase-one source binding shape changed")
    current_pre = _require_identity(source["current_pre"])
    if (
        _require_identity(source["current_post"]) != current_pre
        or not _exact_value(source.get("pre"), source_identity)
        or not _exact_value(source.get("post"), source_identity)
        or not _exact_value(source.get("sha256"), EXPECTED_SOURCE_SHA256)
        or current_pre["dev"] == source_identity["dev"] and current_pre["ino"] == source_identity["ino"]
        or current_pre["uid"] != os.getuid()
        or current_pre["mode"] != 0o755
        or current_pre["nlink"] != 1
        or current_pre["size"] != source_identity["size"]
    ):
        raise AttestationError("phase-one source binding changed")
    version = binding["version"]
    if not _exact_value(version, {
        "exit": 0,
        "expected": EXPECTED_VERSION,
        "logical_argv": logical_argv,
        "logical_argv_sha256": logical_sha,
        "observed": EXPECTED_VERSION,
        "popen_count": 1,
        "stderr_limit": STREAM_LIMIT,
        "stdout_limit": STREAM_LIMIT,
        "timeout_seconds": WALL_SECONDS,
    }):
        raise AttestationError("phase-one version binding changed")


def _validate_prior(profile: AttestationProfile) -> PriorEvidence:
    _validate_profile_authority(profile)
    descriptor = _open_dir(profile.prior_root)
    parent = _open_dir(profile.temp_parent)
    try:
        value = os.fstat(descriptor)
        root_identity = FileIdentity.from_stat(value)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != 0o700
            or root_identity != FileIdentity.from_stat(
                os.stat(
                    os.path.basename(profile.prior_root),
                    dir_fd=parent,
                    follow_symlinks=False,
                )
            )
        ):
            raise AttestationError("prior evidence root is not owner-private")
        if set(os.listdir(descriptor)) != PRIOR_FILES:
            raise AttestationError("prior evidence root has an unexpected shape")
        directories: dict[str, FileIdentity] = {}
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
                    or FileIdentity.from_stat(child_stat)
                    != FileIdentity.from_stat(
                        os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    )
                ):
                    raise AttestationError("prior evidence directory is invalid")
                directories[name] = FileIdentity.from_stat(child_stat)
            finally:
                os.close(child)
        files: dict[str, EvidenceFile] = {}
        binding = _read_at(descriptor, "version.binding.json", PROFILE_LIMIT, files)
        digest = _read_at(descriptor, "version.binding.sha256", 128, files)
        if (
            hashlib.sha256(binding).hexdigest() != profile.prior_binding_sha256
            or digest != (profile.prior_binding_sha256 + "\n").encode("ascii")
        ):
            raise AttestationError("prior binding digest changed")
        parsed = _strict_json(binding)
        if _canonical_json(parsed) != binding:
            raise AttestationError("phase-one binding is not canonical")
        _validate_phase_one_binding(profile, descriptor, parsed, files)
        for name, identity in (
            ("agy.snapshot", profile.snapshot_identity),
        ):
            files[name] = EvidenceFile(identity, profile.source_sha256)
        if set(files).union(directories) != PRIOR_FILES:
            raise AttestationError("prior evidence snapshot is incomplete")
        return PriorEvidence(
            root_identity,
            tuple(sorted(directories.items())),
            tuple(sorted(files.items())),
        )
    finally:
        os.close(parent)
        os.close(descriptor)


def _revalidate_prior(profile: AttestationProfile, expected: PriorEvidence) -> None:
    observed = _validate_prior(profile)
    if observed != expected:
        raise AttestationError("phase-one prior changed during recovery")


def _open_attested(
    path: str,
    identity: FileIdentity,
    sha256: str,
    expected_mode: int,
    controller: Optional[SignalController] = None,
) -> tuple[int, int]:
    if controller is not None:
        controller.poll()
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
        or _hash_fd(descriptor, identity.size, controller) != sha256
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
    controller: Optional[SignalController] = None,
) -> FileIdentity:
    if controller is not None:
        controller.poll()
    leaf = os.path.basename(path)
    held_identity = FileIdentity.from_stat(os.fstat(held))
    path_identity = FileIdentity.from_stat(
        os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    )
    reopened = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent)
    try:
        reopened_identity = FileIdentity.from_stat(os.fstat(reopened))
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
    process: subprocess.Popen[bytes],
    deadline: float,
    controller: Optional[SignalController] = None,
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
            if controller is not None:
                controller.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AttestationError("version process timed out")
            for key, _mask in selector.select(min(remaining, 0.05)):
                if controller is not None:
                    controller.poll()
                stream, captured = buffers[key.fd]
                block = os.read(key.fd, min(64, STREAM_LIMIT + 1 - len(captured)))
                if controller is not None:
                    controller.poll()
                if not block:
                    selector.unregister(key.fd)
                    stream.close()
                    continue
                captured.extend(block)
                if len(captured) > STREAM_LIMIT:
                    raise AttestationError("version output exceeded its bound")
    if controller is not None:
        controller.poll()
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
    profile_bytes: bytes,
    calls: RunnerCalls = REAL_CALLS,
    module_source: Optional[bytes] = None,
    lifecycle: Optional[LifecycleState] = None,
    process_owned: bool = False,
    reviewed_prior: Optional[PriorEvidence] = None,
) -> dict[str, object]:
    """Run one observation; only production owns the final process boundary."""

    reviewed_profile = _validate_exact_profile(profile_bytes)
    if reviewed_profile != profile:
        raise AttestationError("parsed recovery profile changed before execution")
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    prior_evidence = _validate_prior(profile) if reviewed_prior is None else reviewed_prior
    _revalidate_prior(profile, prior_evidence)
    if lifecycle is None:
        lifecycle = _acquire_lifecycle()
        try:
            _activate_lifecycle(lifecycle)
        except BaseException:
            signal.pthread_sigmask(signal.SIG_BLOCK, lifecycle.controller.owned)
            lifecycle.controller.merge_pending()
            for item in reversed(lifecycle.installed_handlers):
                signal.signal(item, lifecycle.old_handlers[item])
            lifecycle.controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, lifecycle.entry_mask)
            raise
    controller = lifecycle.controller
    source_bytes: bytes
    source_contract: dict[str, object]
    root: Optional[Path] = None
    publisher: Optional[Publisher] = None
    source_parent = source_fd = snapshot_parent = snapshot_fd = None
    process: Optional[subprocess.Popen[bytes]] = None
    process_active = False
    completion_linearized = False
    original_error: Optional[BaseException] = None
    result: Optional[dict[str, object]] = None
    child_root_evidence: Optional[PublisherEvidence] = None
    try:
        controller.poll()
        source_bytes = _module_source() if module_source is None else module_source
        controller.poll()
        source_contract = validate_source_contract(source_bytes)
        controller.poll()
        temp_parent_fd = _open_dir(profile.temp_parent)
        os.close(temp_parent_fd)
        controller.poll()
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        try:
            controller.merge_pending()
            controller.poll()
            root = Path(tempfile.mkdtemp(prefix="agy-version-recovery.", dir=profile.temp_parent))
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        os.chmod(root, 0o700)
        for name in ("cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"):
            controller.poll()
            (root / name).mkdir(mode=0o700)
        publisher = Publisher(root, calls, controller, OUTPUT_SCRATCH_NAMES)
        controller.poll()
        runner_sha = publisher.publish("runner.py", source_bytes)
        publisher.publish("runner.py.sha256", (runner_sha + "\n").encode("ascii"))
        if runner_sha != source_contract["sha256"]:
            raise AttestationError("persisted runner source digest changed")
        child_root_evidence = publisher.snapshot()
        source_parent, source_fd = _open_attested(
            profile.source_path,
            profile.source_identity,
            profile.source_sha256,
            0o755,
            controller,
        )
        snapshot_parent, snapshot_fd = _open_attested(
            profile.snapshot_path,
            profile.snapshot_identity,
            profile.source_sha256,
            0o500,
            controller,
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
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        try:
            controller.merge_pending()
            controller.poll()
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
            if type(process.pid) is not int or process.pid <= 1 or process.pid == os.getpgrp():
                raise AttestationError("version process group is unsafe")
            process_active = True
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        started = time.monotonic()
        deadline = started + WALL_SECONDS
        stdout, stderr = _capture(process, deadline, controller)
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        try:
            controller.merge_pending()
            exit_code = _close_reserved_group(process, calls)
            process_active = False
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        if exit_code != 0 or stdout != EXPECTED_STDOUT or stderr != b"":
            raise AttestationError("version result did not match the fixed contract")
        if child_root_evidence is None:
            raise AttestationError("recovery evidence snapshot is incomplete")
        _revalidate_prior(profile, prior_evidence)
        publisher.require_unchanged(child_root_evidence)
        source_post = _verify_attested_path(
            source_parent,
            profile.source_path,
            source_fd,
            profile.source_identity,
            profile.source_sha256,
            controller,
        )
        snapshot_post = _verify_attested_path(
            snapshot_parent,
            profile.snapshot_path,
            snapshot_fd,
            profile.snapshot_identity,
            profile.source_sha256,
            controller,
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
            "input_profile": {
                "byte_count": len(profile_bytes),
                "sha256": profile_sha256,
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
        publisher.publish(
            "version.binding.sha256", (binding_sha + "\n").encode("ascii")
        )
        publisher.validate(OUTPUT_ARTIFACT_NAMES)
        controller.poll()
        result = {
            "artifact_root": str(root),
            "binding_sha256": binding_sha,
            "call_count": 1,
            "claim": "snapshot-version-recovery",
            "runner_sha256": runner_sha,
            "snapshot_sha256": profile.source_sha256,
            "input_profile_sha256": profile_sha256,
            "status": "accepted",
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "stdout_sha256": hashlib.sha256(EXPECTED_STDOUT).hexdigest(),
        }
        if process_owned:
            sys.stdout.buffer.flush()
            _write_all(sys.stdout.buffer.fileno(), _canonical_json(result), controller)
            sys.stdout.buffer.flush()
        signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        controller.merge_pending()
        controller.poll()
        completion_linearized = True
    except BaseException as exc:
        original_error = exc
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        if process_owned and completion_linearized:
            os._exit(0)
        cleanup_failure: Optional[BaseException] = None
        if not completion_linearized:
            controller.merge_pending()
            if process is not None and process_active:
                try:
                    _terminate_group(process, calls)
                    process_active = False
                except BaseException as exc:
                    cleanup_failure = exc
            if publisher is not None:
                try:
                    publisher.rollback()
                except BaseException as exc:
                    if cleanup_failure is None:
                        cleanup_failure = exc
            controller.merge_pending()
        for descriptor in (snapshot_fd, snapshot_parent, source_fd, source_parent):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if publisher is not None:
            try:
                publisher.close()
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        if process_owned:
            controller.merge_pending()
            selected = controller.choose()
            _atomic_exit(
                128 + selected if selected is not None else 2,
                sys.stderr.buffer.fileno(),
                b"version recovery 1.1.12 runner: interrupted\n"
                if selected is not None
                else b"version recovery 1.1.12 runner: rejected\n",
            )
        for item in reversed(lifecycle.installed_handlers):
            try:
                signal.signal(item, lifecycle.old_handlers[item])
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        if not completion_linearized:
            controller.merge_pending()
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, lifecycle.entry_mask)
        except BaseException as exc:
            if cleanup_failure is None:
                cleanup_failure = exc
        if original_error is None and cleanup_failure is not None:
            original_error = cleanup_failure
    selected = controller.choose()
    if selected is not None and not completion_linearized:
        raise AttestationInterrupted(selected)
    if original_error is not None:
        raise original_error
    if result is None:
        raise AttestationError("version attestation did not produce a result")
    return result


FAKE_EXECUTABLE = b"#!/bin/sh\nprintf '1.1.12\\n'\n"


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
    """Validate only reviewed runner structure; synthetic bytes never impersonate Phase 1."""

    contract = validate_source_contract(_module_source())
    if contract["status"] != "accepted":
        raise AttestationError("offline recovery source contract failed")
    return {
        "call_count": 0,
        "claim": "synthetic-version-recovery-contract",
        "schema_version": 1,
        "status": "accepted",
    }


def _interpreter_node(value: os.stat_result) -> InterpreterNode:
    if stat.S_ISDIR(value.st_mode):
        kind = "directory"
    elif stat.S_ISREG(value.st_mode):
        kind = "regular"
    elif stat.S_ISLNK(value.st_mode):
        kind = "symlink"
    else:
        kind = "other"
    return InterpreterNode(
        dev=value.st_dev,
        gid=value.st_gid,
        ino=value.st_ino,
        kind=kind,
        mode=stat.S_IMODE(value.st_mode),
        uid=value.st_uid,
    )


def _interpreter_path_nodes(path: str) -> tuple[InterpreterNode, ...]:
    current = "/"
    nodes = [_interpreter_node(os.lstat(current))]
    for part in pathlib.PurePosixPath(path).parts[1:]:
        current = os.path.join(current, part)
        nodes.append(_interpreter_node(os.lstat(current)))
    return tuple(nodes)


def _collect_interpreter_trust_facts(executable: str) -> InterpreterTrustFacts:
    if (
        not executable
        or not os.path.isabs(executable)
        or os.path.normpath(executable) != executable
    ):
        raise AttestationError("interpreter path is not absolute and normalized")
    resolved = os.path.realpath(executable)
    if (
        not os.path.isabs(resolved)
        or os.path.normpath(resolved) != resolved
        or os.path.realpath(resolved) != resolved
    ):
        raise AttestationError("resolved interpreter path is not canonical")
    alias_nodes = _interpreter_path_nodes(executable)
    resolved_nodes = _interpreter_path_nodes(resolved)
    descriptor = os.open(resolved, os.O_RDONLY | CLOEXEC | NOFOLLOW)
    try:
        resolved_target = _interpreter_node(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    return InterpreterTrustFacts(
        alias_path=executable,
        alias_nodes=alias_nodes,
        alias_target=_interpreter_node(os.stat(executable)),
        resolved_path=resolved,
        resolved_nodes=resolved_nodes,
        resolved_target=resolved_target,
    )


def _numeric_python_version(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 2 and all(item.isdigit() and item for item in parts)


def _apple_interpreter_family(path: str) -> str:
    parts = pathlib.PurePosixPath(path).parts
    if path == "/usr/bin/python3":
        return "usr-bin"
    if parts == (
        "/",
        "Library",
        "Developer",
        "CommandLineTools",
        "usr",
        "bin",
        "python3",
    ):
        return "clt-usr-bin"
    xcode_app = parts[2] if len(parts) > 2 else ""
    versioned_xcode = (
        xcode_app.startswith("Xcode_")
        and xcode_app.endswith(".app")
        and all(
            item.isdigit()
            for item in xcode_app[len("Xcode_") : -len(".app")].split(".")
        )
    )
    xcode_root = (
        parts[1:5]
        == ("Applications", xcode_app, "Contents", "Developer")
        and (xcode_app == "Xcode.app" or versioned_xcode)
    )
    if xcode_root and parts[5:] == ("usr", "bin", "python3"):
        return "xcode-usr-bin"
    clt_framework = (
        "/",
        "Library",
        "Developer",
        "CommandLineTools",
        "Library",
        "Frameworks",
        "Python3.framework",
        "Versions",
    )
    xcode_framework = (
        "/",
        "Applications",
        xcode_app,
        "Contents",
        "Developer",
        "Library",
        "Frameworks",
        "Python3.framework",
        "Versions",
    )
    prefix: tuple[str, ...]
    if parts[: len(clt_framework)] == clt_framework:
        prefix = clt_framework
        family = "clt-framework"
    elif xcode_root and parts[: len(xcode_framework)] == xcode_framework:
        prefix = xcode_framework
        family = "xcode-framework"
    else:
        return "unreviewed"
    tail = parts[len(prefix) :]
    if len(tail) != 3 or tail[1] != "bin" or not _numeric_python_version(tail[0]):
        return "unreviewed"
    if tail[2] not in {"python3", "python" + tail[0]}:
        return "unreviewed"
    return family


def _reviewed_apple_interpreter_path(path: str) -> bool:
    return _apple_interpreter_family(path) != "unreviewed"


def _resolved_filename_class(path: str) -> str:
    name = pathlib.PurePosixPath(path).name
    if name == "python3":
        return "python3"
    if name.startswith("python") and _numeric_python_version(name[6:]):
        return "python-major-minor"
    return "other"


def _safe_component(
    path: str, family: str, component_index: int
) -> str:
    if family == "unreviewed" or component_index < 0:
        return "redacted"
    parts = pathlib.PurePosixPath(path).parts
    if component_index >= len(parts):
        return "redacted"
    value = parts[component_index]
    if not value or len(value) > 64:
        return "redacted"
    if value == "/":
        return "root"
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in value):
        return "redacted"
    return value


def _diagnostic_node(node: Optional[InterpreterNode]) -> tuple[str, int, int, str]:
    if node is None:
        return ("not-applicable", -1, -1, "0000")
    kind = node.kind if node.kind in {"directory", "regular", "symlink", "other"} else "other"
    uid = node.uid if type(node.uid) is int and 0 <= node.uid <= 2_147_483_647 else -1
    gid = node.gid if type(node.gid) is int and 0 <= node.gid <= 2_147_483_647 else -1
    mode = node.mode if type(node.mode) is int else 0
    return (kind, uid, gid, format(mode & 0o7777, "04o"))


def _evaluate_interpreter_trust(
    facts: InterpreterTrustFacts,
    *,
    isolated: int,
    no_site: int,
    dont_write_bytecode: int,
) -> InterpreterTrustEvaluation:
    alias_family = _apple_interpreter_family(facts.alias_path)
    resolved_family = _apple_interpreter_family(facts.resolved_path)
    isolated_ok = type(isolated) is int and isolated == 1
    no_site_ok = type(no_site) is int and no_site == 1
    no_bytecode_ok = type(dont_write_bytecode) is int and dont_write_bytecode == 1
    failures: list[InterpreterTrustFailure] = []
    truncated = False

    def add(
        side: str,
        predicate: str,
        component_index: int = -1,
        node: Optional[InterpreterNode] = None,
    ) -> None:
        nonlocal truncated
        if len(failures) >= STARTUP_FAILURE_LIMIT:
            truncated = True
            return
        path = facts.alias_path if side == "alias" else facts.resolved_path
        family = alias_family if side == "alias" else resolved_family
        kind, uid, gid, mode = _diagnostic_node(node)
        failures.append(
            InterpreterTrustFailure(
                side=side,
                predicate=predicate,
                component_index=component_index,
                basename=_safe_component(path, family, component_index),
                kind=kind,
                uid=uid,
                gid=gid,
                mode=mode,
            )
        )

    if not isolated_ok:
        add("flags", "isolated")
    if not no_site_ok:
        add("flags", "no-site")
    if not no_bytecode_ok:
        add("flags", "no-bytecode")

    for side, path, nodes, target, family in (
        ("alias", facts.alias_path, facts.alias_nodes, facts.alias_target, alias_family),
        (
            "resolved",
            facts.resolved_path,
            facts.resolved_nodes,
            facts.resolved_target,
            resolved_family,
        ),
    ):
        parts = pathlib.PurePosixPath(path).parts
        if not os.path.isabs(path) or os.path.normpath(path) != path:
            add(side, "path-canonical")
        if family == "unreviewed":
            add(side, "family-reviewed")
        if not nodes or len(nodes) != len(parts):
            add(side, "components-complete")
        for index, node in enumerate(nodes[:-1]):
            if node.kind != "directory":
                add(side, "ancestor-directory", index, node)
            if node.mode & 0o002:
                add(side, "ancestor-not-world-writable", index, node)
        if not nodes:
            continue
        leaf_index = len(nodes) - 1
        leaf = nodes[-1]
        if side == "alias":
            if family == "usr-bin":
                if leaf != target:
                    add(side, "leaf-identity", leaf_index, leaf)
                if leaf.kind != "regular":
                    add(side, "leaf-regular", leaf_index, leaf)
            else:
                if leaf.kind != "symlink":
                    add(side, "leaf-symlink", leaf_index, leaf)
        else:
            if leaf != target:
                add(side, "leaf-identity", leaf_index, leaf)
            if leaf.kind != "regular":
                add(side, "leaf-regular", leaf_index, leaf)
            if leaf.mode & 0o002:
                add(side, "leaf-not-world-writable", leaf_index, leaf)
            if leaf.mode & 0o6000:
                add(side, "leaf-no-setid", leaf_index, leaf)
            if not leaf.mode & 0o111:
                add(side, "leaf-executable", leaf_index, leaf)
    if facts.alias_target != facts.resolved_target:
        add("resolved", "alias-target-identity", len(facts.resolved_nodes) - 1, facts.resolved_target)
    return InterpreterTrustEvaluation(
        accepted=not failures and not truncated,
        alias_family=alias_family,
        resolved_family=resolved_family,
        resolved_filename=_resolved_filename_class(facts.resolved_path),
        isolated=isolated_ok,
        no_site=no_site_ok,
        dont_write_bytecode=no_bytecode_ok,
        collection_error="none",
        failures=tuple(failures),
        truncated=truncated,
    )


def _trusted_interpreter_facts(
    facts: InterpreterTrustFacts,
    *,
    isolated: int,
    no_site: int,
    dont_write_bytecode: int,
) -> bool:
    return _evaluate_interpreter_trust(
        facts,
        isolated=isolated,
        no_site=no_site,
        dont_write_bytecode=dont_write_bytecode,
    ).accepted


def _collection_failure(
    collection_error: str,
    *,
    isolated: int,
    no_site: int,
    dont_write_bytecode: int,
) -> InterpreterTrustEvaluation:
    if collection_error not in STARTUP_COLLECTION_ERRORS:
        raise AttestationError("invalid startup collection classification")
    failure = InterpreterTrustFailure(
        side="collection",
        predicate="collection-error",
        component_index=-1,
        basename="redacted",
        kind="not-applicable",
        uid=-1,
        gid=-1,
        mode="0000",
    )
    return InterpreterTrustEvaluation(
        accepted=False,
        alias_family="unavailable",
        resolved_family="unavailable",
        resolved_filename="unavailable",
        isolated=type(isolated) is int and isolated == 1,
        no_site=type(no_site) is int and no_site == 1,
        dont_write_bytecode=type(dont_write_bytecode) is int and dont_write_bytecode == 1,
        collection_error=collection_error,
        failures=(failure,),
        truncated=False,
    )


def _production_startup_evaluation() -> InterpreterTrustEvaluation:
    flags = {
        "isolated": sys.flags.isolated,
        "no_site": sys.flags.no_site,
        "dont_write_bytecode": sys.flags.dont_write_bytecode,
    }
    try:
        facts = _collect_interpreter_trust_facts(sys.executable)
    except AttestationError:
        return _collection_failure("invalid-path", **flags)
    except FileNotFoundError:
        return _collection_failure("missing", **flags)
    except PermissionError:
        return _collection_failure("permission", **flags)
    except OSError:
        return _collection_failure("os-error", **flags)
    except ValueError:
        return _collection_failure("invalid-data", **flags)
    return _evaluate_interpreter_trust(facts, **flags)


def _startup_diagnostic(evaluation: InterpreterTrustEvaluation) -> bytes:
    payload = {
        "alias_family": evaluation.alias_family,
        "collection_error": evaluation.collection_error,
        "dont_write_bytecode": evaluation.dont_write_bytecode,
        "failures": [dataclasses.asdict(item) for item in evaluation.failures],
        "isolated": evaluation.isolated,
        "no_site": evaluation.no_site,
        "resolved_family": evaluation.resolved_family,
        "resolved_filename": evaluation.resolved_filename,
        "schema_version": 1,
        "status": "rejected",
        "truncated": evaluation.truncated,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    if len(encoded) <= STARTUP_DIAGNOSTIC_LIMIT:
        return encoded
    fallback = {
        "alias_family": "unavailable",
        "collection_error": "diagnostic-overflow",
        "dont_write_bytecode": evaluation.dont_write_bytecode,
        "failures": [],
        "isolated": evaluation.isolated,
        "no_site": evaluation.no_site,
        "resolved_family": "unavailable",
        "resolved_filename": "unavailable",
        "schema_version": 1,
        "status": "rejected",
        "truncated": True,
    }
    return json.dumps(fallback, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _read_profile_stdin() -> bytes:
    data = bytearray()
    descriptor = sys.stdin.buffer.fileno()
    while len(data) <= PROFILE_LIMIT:
        block = os.read(descriptor, min(64 * 1024, PROFILE_LIMIT + 1 - len(data)))
        if not block:
            break
        data.extend(block)
    return bytes(data)


def main(argv: Sequence[str]) -> int:
    if not _runtime_supported():
        _atomic_exit(2, sys.stderr.buffer.fileno(), b"version recovery 1.1.12 runner: requires /usr/bin/python3 -I -S -B\n")
    if list(argv) == ["--self-test"]:
        try:
            result = run_offline_self_test()
        except (AttestationError, OSError, subprocess.SubprocessError):
            print("version recovery 1.1.12 runner: rejected", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if list(argv) != ["--recover-version"]:
        print("version recovery 1.1.12 runner: invalid invocation", file=sys.stderr)
        return 64
    startup = _production_startup_evaluation()
    if not startup.accepted:
        _atomic_exit(64, sys.stderr.buffer.fileno(), _startup_diagnostic(startup))
    try:
        data = _read_profile_stdin()
        profile = _validate_exact_profile(data)
        prior_evidence = _validate_prior(profile)
    except BaseException:
        _atomic_exit(2, sys.stderr.buffer.fileno(), b"version recovery 1.1.12 runner: rejected\n")
    try:
        lifecycle = _acquire_lifecycle()
    except BaseException:
        _atomic_exit(2, sys.stderr.buffer.fileno(), b"version recovery 1.1.12 runner: rejected\n")
    try:
        _activate_lifecycle(lifecycle)
        lifecycle.controller.poll()
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK, lifecycle.controller.owned)
        lifecycle.controller.merge_pending()
        selected = lifecycle.controller.choose()
        _atomic_exit(
            128 + selected if selected is not None else 2,
            sys.stderr.buffer.fileno(),
            b"version recovery 1.1.12 runner: interrupted\n"
            if selected is not None
            else b"version recovery 1.1.12 runner: rejected\n",
        )
    run_attestation(
        profile,
        profile_bytes=data,
        lifecycle=lifecycle,
        module_source=_module_source(),
        process_owned=True,
        reviewed_prior=prior_evidence,
    )
    _atomic_exit(2, sys.stderr.buffer.fileno(), b"version recovery 1.1.12 runner: rejected\n")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
