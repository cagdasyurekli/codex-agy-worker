#!/usr/bin/env python3
"""Capture one reviewed ``agy models`` observation using an explicit account HOME.

This runner is intentionally separate from the auth-isolated accepting runner.  It
produces private capture evidence only; it never advances compatibility metadata or
turns the observation into routing, provider, or acceptance authority.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


sys.dont_write_bytecode = True

MODELS_RUNNER_BYTES = 60_173
MODELS_RUNNER_SHA256 = "70e93c1c32af1d2b65667b75ed240e81ea47548cbe301b7cc7f6e37a1b099b24"
EXPECTED_SOURCE_SHA256 = "198ff7c3f6d173daa510b0814aa70c6ce14c94035bcd4707a3c0e79fa38a7bc3"
EXPECTED_VERSION_BINDING_SHA256 = "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2"
EXPECTED_SNAPSHOT_INODE = 26_545_304
EXPECTED_SNAPSHOT_SIZE = 169_718_336
PROFILE_LIMIT = 16_384
STREAM_LIMIT = 64 * 1024
WALL_SECONDS = 25.0
MODULE_AST_SHA256 = "e571459fe697b048f676b9b6c99ac5ef5e770af180d4f1f5424e3d7ca8cb4427"
ACCOUNT_POLICY_AST_SHA256 = "c399d657d771773ccfe765be5d7fb8bf8040cae53cc2755e037b7a65faae613d"
PRIVATE_DIRECTORY_NAMES = ("cwd", "tmp", "xdg-config", "xdg-cache", "xdg-state")
ACCOUNT_IDENTITY_KEYS = frozenset({"dev", "gid", "ino", "mode", "nlink", "uid"})
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
OUTPUT_FILES = (
    "models_capture_runner.py",
    "models_capture_runner.py.sha256",
    "models.capture.profile.json",
    "models.stdout",
    "models.stderr",
    "models.capture.summary.json",
    "models.capture.json",
    "models.capture.sha256",
)


def _load_models_runner() -> object:
    path = Path(__file__).resolve(strict=True).with_name("models_attestation_runner.py")
    value = path.lstat()
    if (
        path.parent != Path(__file__).resolve(strict=True).parent
        or not stat.S_ISREG(value.st_mode)
        or value.st_size != MODELS_RUNNER_BYTES
        or stat.S_IMODE(value.st_mode) & 0o022
    ):
        raise RuntimeError("canonical models dependency identity changed")
    descriptor = os.open(
        str(path), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        data = os.read(descriptor, MODELS_RUNNER_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(data) != MODELS_RUNNER_BYTES
        or hashlib.sha256(data).hexdigest() != MODELS_RUNNER_SHA256
        or (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise RuntimeError("canonical models dependency bytes changed")
    spec = importlib.util.spec_from_file_location("_agy_models_capture_dependency", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical models dependency cannot be loaded")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


models = _load_models_runner()
version = models.version


class ModelsCaptureError(ValueError):
    """A capture-only models observation failed closed."""


class ModelsCaptureInterrupted(BaseException):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


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
        if not isinstance(value, dict) or set(value) != ACCOUNT_IDENTITY_KEYS:
            raise ModelsCaptureError("invalid account HOME identity")
        if any(type(value[key]) is not int or value[key] < 0 for key in ACCOUNT_IDENTITY_KEYS):
            raise ModelsCaptureError("invalid account HOME identity")
        return cls(**value)

    def as_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class CaptureProfile:
    account_home: str
    account_home_identity: DirectoryIdentity
    snapshot_identity: object
    snapshot_path: str
    source_identity: object
    source_path: str
    source_sha256: str
    temp_parent: str
    version_binding_sha256: str
    version_root: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "CaptureProfile":
        value = version._strict_json(data)
        if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
            raise ModelsCaptureError("invalid models capture profile")
        for key in (
            "account_home", "snapshot_path", "source_path", "temp_parent", "version_root"
        ):
            version._require_canonical_absolute(value[key])
        for key in ("source_sha256", "version_binding_sha256"):
            if not isinstance(value[key], str) or not version._is_sha256(value[key]):
                raise ModelsCaptureError("invalid models capture profile")
        profile = cls(
            account_home=value["account_home"],
            account_home_identity=DirectoryIdentity.from_mapping(value["account_home_identity"]),
            snapshot_identity=version.FileIdentity.from_mapping(value["snapshot_identity"]),
            snapshot_path=value["snapshot_path"],
            source_identity=version.FileIdentity.from_mapping(value["source_identity"]),
            source_path=value["source_path"],
            source_sha256=value["source_sha256"],
            temp_parent=value["temp_parent"],
            version_binding_sha256=value["version_binding_sha256"],
            version_root=value["version_root"],
        )
        if _canonical_json(profile.as_mapping()) != data:
            raise ModelsCaptureError("models capture profile is not canonical")
        return profile

    def as_mapping(self) -> dict[str, object]:
        return dataclasses.asdict(self)

    @property
    def models_profile(self) -> "CaptureProfile":
        """Compatibility view for the reviewed version-evidence validator only."""

        return self


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"


def _same_account_identity(
    observed: DirectoryIdentity, expected: DirectoryIdentity, *, require_nlink: bool
) -> bool:
    return (
        observed.dev == expected.dev
        and observed.gid == expected.gid
        and observed.ino == expected.ino
        and observed.mode == expected.mode
        and observed.uid == expected.uid
        and observed.nlink >= 1
        and (not require_nlink or observed.nlink == expected.nlink)
    )


def _account_descriptor(profile: CaptureProfile, *, require_profile_nlink: bool = True) -> int:
    version._require_canonical_absolute(profile.account_home)
    descriptor = os.open("/", os.O_RDONLY | version.DIRECTORY | version.CLOEXEC)
    try:
        root_value = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(root_value.st_mode)
            or root_value.st_uid != 0
            or stat.S_IMODE(root_value.st_mode) & 0o022
        ):
            raise ModelsCaptureError("account HOME component policy changed")
        for part in Path(profile.account_home).parts[1:]:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
            component = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(component.st_mode)
                or component.st_uid not in {0, os.getuid()}
                or stat.S_IMODE(component.st_mode) & 0o022
            ):
                raise ModelsCaptureError("account HOME component policy changed")
    except BaseException:
        os.close(descriptor)
        raise
    observed = os.fstat(descriptor)
    identity = DirectoryIdentity.from_stat(observed)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or not _same_account_identity(
            identity, profile.account_home_identity, require_nlink=require_profile_nlink
        )
        or identity.uid != os.getuid()
        or identity.mode != 0o700
        or identity.nlink < 1
    ):
        os.close(descriptor)
        raise ModelsCaptureError("account HOME identity changed")
    return descriptor


def _verify_account_descriptor(profile: CaptureProfile, held: int) -> DirectoryIdentity:
    held_identity = DirectoryIdentity.from_stat(os.fstat(held))
    reopened = _account_descriptor(profile, require_profile_nlink=False)
    try:
        reopened_identity = DirectoryIdentity.from_stat(os.fstat(reopened))
    finally:
        os.close(reopened)
    if (
        not _same_account_identity(
            held_identity, profile.account_home_identity, require_nlink=False
        )
        or reopened_identity != held_identity
    ):
        raise ModelsCaptureError("account HOME path changed")
    return held_identity


def _private_directory_identity(path: Path) -> object:
    descriptor = os.open(
        str(path), os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW
    )
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or observed.st_uid != os.getuid()
            or stat.S_IMODE(observed.st_mode) != 0o700
            or os.listdir(descriptor)
        ):
            raise ModelsCaptureError("private capture directory changed")
        return version.FileIdentity.from_stat(observed)
    finally:
        os.close(descriptor)


def _revalidate_private_directories(root: Path, expected: dict[str, object]) -> None:
    if tuple(expected) != PRIVATE_DIRECTORY_NAMES:
        raise ModelsCaptureError("private capture directory inventory changed")
    for name in PRIVATE_DIRECTORY_NAMES:
        observed = _private_directory_identity(root / name)
        prior = expected[name]
        if (
            observed.dev != prior.dev
            or observed.gid != prior.gid
            or observed.ino != prior.ino
            or observed.mode != prior.mode
            or observed.nlink != prior.nlink
            or observed.uid != prior.uid
        ):
            raise ModelsCaptureError("private capture directory identity changed")


def _validate_account_policy(profile: CaptureProfile) -> None:
    base = profile.models_profile
    repository = str(Path(__file__).resolve(strict=True).parents[1])
    protected = (repository, base.temp_parent, base.version_root)
    if (
        any(
            os.path.commonpath((profile.account_home, item)) in {profile.account_home, item}
            for item in protected
        )
    ):
        raise ModelsCaptureError("account HOME overlaps protected paths")
    # The profile builder already rejects this.  Keep the production consumer
    # independently fail-closed: a hand-authored canonical profile must not turn
    # an account-owned executable into capture authority.
    if any(
        os.path.commonpath((path, profile.account_home)) in {path, profile.account_home}
        for path in (base.source_path, base.snapshot_path)
    ):
        raise ModelsCaptureError("attested executable overlaps account HOME")
    if (
        profile.account_home_identity.uid != os.getuid()
        or profile.account_home_identity.mode != 0o700
        or profile.account_home_identity.nlink < 1
    ):
        raise ModelsCaptureError("account HOME policy is invalid")


def _validate_production_profile(profile: CaptureProfile) -> None:
    base = profile.models_profile
    _validate_account_policy(profile)
    if (
        base.version_binding_sha256 != EXPECTED_VERSION_BINDING_SHA256
        or base.source_sha256 != EXPECTED_SOURCE_SHA256
        or base.snapshot_identity.ino != EXPECTED_SNAPSHOT_INODE
        or base.snapshot_identity.size != EXPECTED_SNAPSHOT_SIZE
        or base.snapshot_identity.mode != 0o500
    ):
        raise ModelsCaptureError("retained snapshot profile is not reviewed")


def validate_source_contract(data: bytes) -> dict[str, object]:
    try:
        text = data.decode("utf-8", "strict")
        tree = ast.parse(text, filename="<models-capture-runner>", mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ModelsCaptureError("models capture source is invalid") from exc
    module_hash_assignment = None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "MODULE_AST_SHA256"
        ):
            module_hash_assignment = node
            break
    if module_hash_assignment is None:
        raise ModelsCaptureError("models capture module authority is missing")
    module_hash_assignment.value = ast.Constant(value="PINNED-MODULE-AST")
    if (
        hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()
        != MODULE_AST_SHA256
    ):
        raise ModelsCaptureError("models capture module structure changed")
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    run_node = functions.get("run_capture")
    main_node = functions.get("main")
    account_policy_node = functions.get("_validate_account_policy")
    if run_node is None or main_node is None or account_policy_node is None:
        raise ModelsCaptureError("models capture authority is incomplete")
    if (
        hashlib.sha256(
            ast.dump(account_policy_node, include_attributes=False).encode("utf-8")
        ).hexdigest()
        != ACCOUNT_POLICY_AST_SHA256
    ):
        raise ModelsCaptureError("models capture account policy changed")
    if any(
        isinstance(node, ast.Attribute) and node.attr in {"__dict__", "__getattribute__"}
        for node in ast.walk(tree)
    ) or any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"__import__", "compile", "eval", "exec"}
        for node in ast.walk(tree)
    ):
        raise ModelsCaptureError("models capture dynamic authority is forbidden")
    launch_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {
            "Popen", "call", "check_call", "check_output", "getoutput",
            "getstatusoutput", "popen", "run", "system", "fork", "forkpty",
            "posix_spawn", "posix_spawnp",
        }
    ]
    canonical = [
        node for node in launch_calls
        if isinstance(node.func.value, ast.Name)
        and node.func.value.id == "calls"
        and node.func.attr == "popen"
    ]
    if len(launch_calls) != 1 or len(canonical) != 1:
        raise ModelsCaptureError("models capture must contain one child launch")
    call = canonical[0]
    keywords = {item.arg: item.value for item in call.keywords if item.arg is not None}
    expected_environment = (
        '"HOME": profile.account_home',
        '"TMPDIR": str(root / "tmp")',
        '"XDG_CONFIG_HOME": str(root / "xdg-config")',
        '"XDG_CACHE_HOME": str(root / "xdg-cache")',
        '"XDG_STATE_HOME": str(root / "xdg-state")',
        '"LANG": "C"',
        '"LC_ALL": "C"',
        '"NO_COLOR": "1"',
        '"TERM": "dumb"',
        '"PATH": "/usr/bin:/bin"',
    )
    run_text = ast.get_source_segment(text, run_node) or ""
    main_text = ast.get_source_segment(text, main_node) or ""
    if not (
        len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "argv"
        and set(keywords) == {
            "cwd", "env", "executable", "start_new_session", "stderr", "stdin", "stdout"
        }
        and ast.dump(keywords["executable"])
        == "Attribute(value=Name(id='base', ctx=Load()), attr='snapshot_path', ctx=Load())"
        and isinstance(keywords["env"], ast.Name)
        and keywords["env"].id == "environment"
        and ast.dump(keywords["stdin"]).endswith("attr='DEVNULL', ctx=Load())")
        and ast.dump(keywords["stdout"]).endswith("attr='PIPE', ctx=Load())")
        and ast.dump(keywords["stderr"]).endswith("attr='PIPE', ctx=Load())")
        and isinstance(keywords["start_new_session"], ast.Constant)
        and keywords["start_new_session"].value is True
        and all(marker in run_text for marker in expected_environment)
        and 'argv = [base.source_path, "models"]' in run_text
        and "deadline = started + WALL_SECONDS" in run_text
        and "version._close_reserved_group(process, calls)" in run_text
        and 'publisher.publish("models.capture.sha256"' in run_text
        and 'list(argv) != ["--capture-models"]' in main_text
        and "data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)" in main_text
    ):
        raise ModelsCaptureError("models capture source authority changed")
    assignments = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    if (
        ast.dump(assignments.get("STREAM_LIMIT"))
        != "BinOp(left=Constant(value=64), op=Mult(), right=Constant(value=1024))"
        or ast.dump(assignments.get("WALL_SECONDS")) != "Constant(value=25.0)"
        or ast.dump(assignments.get("EXPECTED_SOURCE_SHA256"))
        != "Constant(value='198ff7c3f6d173daa510b0814aa70c6ce14c94035bcd4707a3c0e79fa38a7bc3')"
        or ast.dump(assignments.get("EXPECTED_VERSION_BINDING_SHA256"))
        != "Constant(value='72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2')"
    ):
        raise ModelsCaptureError("models capture constants changed")
    return {"byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def run_capture(
    profile: CaptureProfile,
    *,
    calls: object = version.REAL_CALLS,
    module_source: Optional[bytes] = None,
    profile_source: Optional[bytes] = None,
    profile_validator: Callable[[CaptureProfile], None] = _validate_production_profile,
) -> dict[str, object]:
    """Capture one exact snapshot-backed models observation without accepting it."""

    if not all(hasattr(signal, name) for name in ("pthread_sigmask", "sigpending", "sigwait")):
        raise ModelsCaptureError("required signal primitives are unavailable")
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
    old_handlers = {item: signal.getsignal(item) for item in version.LIFECYCLE_SIGNALS}
    root: Optional[Path] = None
    publisher = None
    account_fd = source_parent = source_fd = snapshot_parent = snapshot_fd = None
    process: Optional[subprocess.Popen[bytes]] = None
    process_active = False

    def interrupted(signum: int, _frame: object) -> None:
        raise ModelsCaptureInterrupted(signum)

    for item in version.LIFECYCLE_SIGNALS:
        signal.signal(item, interrupted)
    signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
    try:
        source = Path(__file__).resolve(strict=True).read_bytes() if module_source is None else module_source
        contract = validate_source_contract(source)
        exact_profile = _canonical_json(profile.as_mapping()) if profile_source is None else profile_source
        if CaptureProfile.from_bytes(exact_profile) != profile:
            raise ModelsCaptureError("exact capture profile changed")
        profile_validator(profile)
        base = profile.models_profile
        models._validate_version_evidence(base)
        account_fd = _account_descriptor(profile)
        root = Path(tempfile.mkdtemp(prefix="agy-models-account-capture.", dir=base.temp_parent))
        os.chmod(root, 0o700)
        publisher = version.Publisher(root, calls)
        private_identities = {}
        for name in PRIVATE_DIRECTORY_NAMES:
            child = root / name
            child.mkdir(mode=0o700)
            private_identities[name] = _private_directory_identity(child)
        runner_sha = publisher.publish("models_capture_runner.py", source)
        publisher.publish("models_capture_runner.py.sha256", (runner_sha + "\n").encode("ascii"))
        profile_sha = publisher.publish("models.capture.profile.json", exact_profile)
        if runner_sha != contract["sha256"]:
            raise ModelsCaptureError("persisted capture source changed")
        source_parent, source_fd = version._open_attested(
            base.source_path, base.source_identity, base.source_sha256, 0o755
        )
        snapshot_parent, snapshot_fd = version._open_attested(
            base.snapshot_path, base.snapshot_identity, base.source_sha256, 0o500
        )
        argv = [base.source_path, "models"]
        environment = {
            "HOME": profile.account_home,
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
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        try:
            _revalidate_private_directories(root, private_identities)
            _verify_account_descriptor(profile, account_fd)
            process = calls.popen(
                argv,
                executable=base.snapshot_path,
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
        stdout, stderr = models._capture(process, deadline)
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        try:
            exit_code = version._close_reserved_group(process, calls)
            process_active = False
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        if exit_code != 0:
            raise ModelsCaptureError("models capture process failed")
        _revalidate_private_directories(root, private_identities)
        account_post = _verify_account_descriptor(profile, account_fd)
        source_post = version._verify_attested_path(
            source_parent, base.source_path, source_fd, base.source_identity, base.source_sha256
        )
        snapshot_post = version._verify_attested_path(
            snapshot_parent, base.snapshot_path, snapshot_fd, base.snapshot_identity, base.source_sha256
        )
        stdout_sha = publisher.publish("models.stdout", stdout)
        stderr_sha = publisher.publish("models.stderr", stderr)
        logical_sha = hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("ascii")).hexdigest()
        summary = {
            "call_count": 1,
            "child_exit": exit_code,
            "claim": "snapshot-models-account-capture",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "logical_argv_sha256": logical_sha,
            "profile_bytes": len(exact_profile),
            "profile_sha256": profile_sha,
            "schema_version": 1,
            "status": "captured",
            "stderr_bytes": len(stderr),
            "stdout_bytes": len(stdout),
            "timeout": False,
        }
        summary_sha = publisher.publish("models.capture.summary.json", _canonical_json(summary))
        capture_record = {
            "account": {"home_identity": account_post.as_dict(), "tcb": "explicit-local-owner"},
            "artifacts": {
                "models.stderr": stderr_sha,
                "models.stdout": stdout_sha,
                "models.capture.summary.json": summary_sha,
                "models_capture_runner.py": runner_sha,
                "models.capture.profile.json": profile_sha,
            },
            "claim": "snapshot-models-account-capture",
            "limitations": {
                "accepted_inventory": False,
                "inventory_interpreted": False,
                "metadata_advance_authorized": False,
                "provider_backend_proven": False,
                "routing_authorized": False,
            },
            "models": {
                "exit": exit_code,
                "logical_argv": argv,
                "logical_argv_sha256": logical_sha,
                "popen_count": 1,
                "stderr_limit": STREAM_LIMIT,
                "stdout_limit": STREAM_LIMIT,
                "timeout_seconds": WALL_SECONDS,
            },
            "profile": {"byte_count": len(exact_profile), "sha256": profile_sha},
            "runner": {"byte_count": len(source), "sha256": runner_sha},
            "schema_version": 1,
            "snapshot": {
                "post": snapshot_post.as_dict(), "pre": base.snapshot_identity.as_dict(),
                "sha256": base.source_sha256,
            },
            "source": {
                "post": source_post.as_dict(), "pre": base.source_identity.as_dict(),
                "sha256": base.source_sha256,
            },
            "status": "captured",
            "version": {"binding_sha256": base.version_binding_sha256},
        }
        capture_sha = publisher.publish("models.capture.json", _canonical_json(capture_record))
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        publisher.publish("models.capture.sha256", (capture_sha + "\n").encode("ascii"))
        pending = set(signal.sigpending()).intersection(version.LIFECYCLE_SIGNALS)
        if pending:
            first = signal.sigwait(pending)
            publisher.rollback()
            raise ModelsCaptureInterrupted(first)
        for item in version.LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        pending = set(signal.sigpending()).intersection(version.LIFECYCLE_SIGNALS)
        if pending:
            first = signal.sigwait(pending)
            publisher.rollback()
            raise ModelsCaptureInterrupted(first)
        return {
            "artifact_root": str(root),
            "capture_sha256": capture_sha,
            "call_count": 1,
            "claim": "snapshot-models-account-capture",
            "status": "captured",
        }
    except ModelsCaptureInterrupted as exc:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        cleanup_failure: Optional[BaseException] = None
        if process is not None and process_active:
            try:
                version._terminate_group(process, calls)
            except BaseException as cleanup_error:
                cleanup_failure = cleanup_error
        if publisher is not None:
            try:
                publisher.rollback()
            except BaseException as cleanup_error:
                if cleanup_failure is None:
                    cleanup_failure = cleanup_error
        for item in version.LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        if cleanup_failure is not None:
            raise cleanup_failure
        raise SystemExit(128 + exc.signum)
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        cleanup_failure = None
        if process is not None and process_active:
            try:
                version._terminate_group(process, calls)
            except BaseException as cleanup_error:
                cleanup_failure = cleanup_error
        if publisher is not None:
            try:
                publisher.rollback()
            except BaseException as cleanup_error:
                if cleanup_failure is None:
                    cleanup_failure = cleanup_error
        for item in version.LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        if cleanup_failure is not None:
            raise cleanup_failure
        raise
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        for descriptor in (account_fd, snapshot_fd, snapshot_parent, source_fd, source_parent):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if publisher is not None:
            try:
                publisher.close()
            except OSError:
                pass
        for item, handler in old_handlers.items():
            signal.signal(item, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)


def main(argv: Sequence[str]) -> int:
    if list(argv) != ["--capture-models"]:
        print("models account capture: invalid invocation", file=sys.stderr)
        return 64
    startup = version._production_startup_evaluation()
    if not startup.accepted:
        sys.stderr.buffer.write(version._startup_diagnostic(startup))
        return 64
    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)
    try:
        if len(data) > PROFILE_LIMIT:
            raise ModelsCaptureError("models capture profile is oversized")
        profile = CaptureProfile.from_bytes(data)
        _validate_production_profile(profile)
        result = run_capture(profile, profile_source=data)
    except SystemExit:
        raise
    except BaseException:
        print("models account capture: rejected", file=sys.stderr)
        return 2
    print(json.dumps(
        {
            "artifact_root": result["artifact_root"],
            "capture_sha256": result["capture_sha256"],
            "status": "captured",
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
