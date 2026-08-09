#!/usr/bin/env python3
"""Fixed-profile, snapshot-backed ``agy models`` inventory attestation.

The production interface accepts only ``--attest-models`` and a bounded strict
JSON profile on stdin.  It executes exactly one logical ``[source, "models"]``
call with the already version-attested snapshot as the actual executable.  The
offline interface uses synthetic evidence only.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib.util
import json
import os
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
from typing import Optional, Sequence


sys.dont_write_bytecode = True

VERSION_RUNNER_BYTES = 62_988
VERSION_RUNNER_SHA256 = "e6bd55d2d0ab6c542745fd1bb1af4f6f4b7f163abb6f8c78597a24475d501d28"
INVENTORY_PARSER_BYTES = 3_652
INVENTORY_PARSER_SHA256 = "824fc35b7c87df61a437b5c93e508b6caf5759626b004e0f82acd8f903eadd18"
EXPECTED_NORMALIZED_SHA256 = "8d46bcac6b8f27995635d91dc6f5a0e549d351e707efe11a82d8b6593fe12daf"
EXPECTED_VERSION_BINDING_SHA256 = "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2"
EXPECTED_STDERR_BYTES = 29
EXPECTED_STDERR_SHA256 = "53f588bc9a928f4a66908deacaca57dddc7e7ce177a0cc3586b5a501be26e1e8"
PROFILE_LIMIT = 16_384
STREAM_LIMIT = 64 * 1024
WALL_SECONDS = 25.0
PROFILE_KEYS = frozenset(
    {
        "inventory_normalized_sha256",
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
        "models-placeholder-never-present",
        "runner.py",
        "runner.py.sha256",
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
) - {"models-placeholder-never-present"}
OUTPUT_FILES = (
    "models_runner.py",
    "models_runner.py.sha256",
    "agy_inventory.py",
    "agy_inventory.py.sha256",
    "models.profile.json",
    "models.stdout",
    "models.stderr",
    "models.summary.json",
    "models.binding.json",
    "models.binding.sha256",
)


def _load_pinned_dependency(
    module_name: str, filename: str, expected_size: int, expected_sha256: str
) -> object:
    path = Path(__file__).resolve(strict=True).with_name(filename)
    value = path.lstat()
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if (
        path.parent != Path(__file__).resolve(strict=True).parent
        or not stat.S_ISREG(value.st_mode)
        or value.st_size != expected_size
        or stat.S_IMODE(value.st_mode) & 0o022
    ):
        raise RuntimeError("canonical dependency identity changed")
    descriptor = os.open(str(path), os.O_RDONLY | nofollow | cloexec)
    try:
        data = os.read(descriptor, expected_size + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(data) != expected_size
        or hashlib.sha256(data).hexdigest() != expected_sha256
        or (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise RuntimeError("canonical dependency bytes changed")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical dependency cannot be loaded")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


version = _load_pinned_dependency(
    "_agy_models_version_runner",
    "version_attestation_runner.py",
    VERSION_RUNNER_BYTES,
    VERSION_RUNNER_SHA256,
)
inventory = _load_pinned_dependency(
    "_agy_models_inventory_parser",
    "agy_inventory.py",
    INVENTORY_PARSER_BYTES,
    INVENTORY_PARSER_SHA256,
)


class ModelsAttestationError(ValueError):
    """A fixed-profile models observation failed closed."""


class ModelsAttestationInterrupted(BaseException):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class ModelsProfile:
    inventory_normalized_sha256: str
    snapshot_identity: version.FileIdentity
    snapshot_path: str
    source_identity: version.FileIdentity
    source_path: str
    source_sha256: str
    temp_parent: str
    version_binding_sha256: str
    version_root: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "ModelsProfile":
        value = version._strict_json(data)
        if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
            raise ModelsAttestationError("invalid models evidence profile")
        for key in ("snapshot_path", "source_path", "temp_parent", "version_root"):
            version._require_canonical_absolute(value[key])
        for key in (
            "inventory_normalized_sha256",
            "source_sha256",
            "version_binding_sha256",
        ):
            if not isinstance(value[key], str) or not version._is_sha256(value[key]):
                raise ModelsAttestationError("invalid models evidence profile")
        if value["inventory_normalized_sha256"] != EXPECTED_NORMALIZED_SHA256:
            raise ModelsAttestationError("inventory baseline is not reviewed")
        return cls(
            inventory_normalized_sha256=value["inventory_normalized_sha256"],
            snapshot_identity=version.FileIdentity.from_mapping(value["snapshot_identity"]),
            snapshot_path=value["snapshot_path"],
            source_identity=version.FileIdentity.from_mapping(value["source_identity"]),
            source_path=value["source_path"],
            source_sha256=value["source_sha256"],
            temp_parent=value["temp_parent"],
            version_binding_sha256=value["version_binding_sha256"],
            version_root=value["version_root"],
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"


def _source_bytes(path: Path, expected_size: int, expected_sha: str) -> bytes:
    canonical = path.resolve(strict=True)
    scripts = Path(__file__).resolve(strict=True).parent
    if canonical.parent != scripts or canonical != path:
        raise ModelsAttestationError("canonical dependency path changed")
    value = canonical.lstat()
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_size != expected_size
        or stat.S_IMODE(value.st_mode) & 0o022
    ):
        raise ModelsAttestationError("canonical dependency identity changed")
    descriptor = os.open(str(canonical), os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW)
    try:
        data = os.read(descriptor, expected_size + 1)
        if len(data) != expected_size or os.read(descriptor, 1) != b"":
            raise ModelsAttestationError("canonical dependency size changed")
        if version.FileIdentity.from_stat(os.fstat(descriptor)) != version.FileIdentity.from_stat(value):
            raise ModelsAttestationError("canonical dependency changed while read")
    finally:
        os.close(descriptor)
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise ModelsAttestationError("canonical dependency digest changed")
    return data


def _canonical_sources(module_source: Optional[bytes] = None) -> tuple[bytes, bytes, bytes]:
    scripts = Path(__file__).resolve(strict=True).parent
    runner = Path(__file__).resolve(strict=True).read_bytes() if module_source is None else module_source
    if not runner or len(runner) > 128 * 1024 or b"\x00" in runner:
        raise ModelsAttestationError("models runner source is invalid")
    version_source = _source_bytes(
        scripts / "version_attestation_runner.py",
        VERSION_RUNNER_BYTES,
        VERSION_RUNNER_SHA256,
    )
    inventory_source = _source_bytes(
        scripts / "agy_inventory.py",
        INVENTORY_PARSER_BYTES,
        INVENTORY_PARSER_SHA256,
    )
    if Path(version.__file__).resolve(strict=True) != scripts / "version_attestation_runner.py":
        raise ModelsAttestationError("loaded version runner path changed")
    if Path(inventory.__file__).resolve(strict=True) != scripts / "agy_inventory.py":
        raise ModelsAttestationError("loaded inventory parser path changed")
    return runner, version_source, inventory_source


def validate_source_contract(data: bytes) -> dict[str, object]:
    try:
        text = data.decode("utf-8", "strict")
        tree = ast.parse(text, filename="<models-attestation-runner>", mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ModelsAttestationError("models runner source is invalid") from exc
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    run_node = functions.get("run_attestation")
    capture_node = functions.get("_capture")
    version_node = functions.get("_validate_version_evidence")
    production_node = functions.get("_validate_production_profile")
    main_node = functions.get("main")
    if (
        run_node is None
        or capture_node is None
        or version_node is None
        or production_node is None
        or main_node is None
    ):
        raise ModelsAttestationError("models source authority is incomplete")
    popen = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "calls"
        and node.func.attr == "popen"
    ]
    if len(popen) != 1:
        raise ModelsAttestationError("models runner must contain one Popen authority")
    call = popen[0]
    keywords = {item.arg: item.value for item in call.keywords if item.arg is not None}
    if not (
        len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "argv"
        and isinstance(keywords.get("executable"), ast.Attribute)
        and keywords["executable"].attr == "snapshot_path"
        and isinstance(keywords.get("start_new_session"), ast.Constant)
        and keywords["start_new_session"].value is True
    ):
        raise ModelsAttestationError("models Popen contract changed")
    argv_assignments = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "argv"
    ]
    expected_argv = "List(elts=[Attribute(value=Name(id='profile', ctx=Load()), attr='source_path', ctx=Load()), Constant(value='models')], ctx=Load())"
    if len(argv_assignments) != 1 or ast.dump(argv_assignments[0].value) != expected_argv:
        raise ModelsAttestationError("models logical argv changed")
    run_text = ast.get_source_segment(text, run_node) or ""
    capture_text = ast.get_source_segment(text, capture_node) or ""
    version_text = ast.get_source_segment(text, version_node) or ""
    production_text = ast.get_source_segment(text, production_node) or ""
    main_text = ast.get_source_segment(text, main_node) or ""
    required_run = (
        "deadline = started + WALL_SECONDS",
        "evidence = inventory.parse_inventory_bytes(stdout)",
        'profile_sha = publisher.publish("models.profile.json", exact_profile)',
        "stderr_sha = _validate_stderr(stderr, stderr_contract)",
        'publisher.publish("models.binding.sha256",',
    )
    if (
        any(marker not in run_text for marker in required_run)
        or "STREAM_LIMIT + 1 - len(captured)" not in capture_text
        or "hashlib.sha256(binding_bytes).hexdigest() != profile.version_binding_sha256"
        not in version_text
        or "or os.listdir(child)" not in version_text
        or "profile.version_binding_sha256 != EXPECTED_VERSION_BINDING_SHA256"
        not in production_text
        or "_validate_production_profile(profile)\n        result = run_attestation(profile)"
        in main_text
    ):
        raise ModelsAttestationError("models source authority changed")
    main_reads = [
        node
        for node in ast.walk(main_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "buffer"
        and isinstance(node.func.value.value, ast.Attribute)
        and isinstance(node.func.value.value.value, ast.Name)
        and node.func.value.value.value.id == "sys"
        and node.func.value.value.attr == "stdin"
    ]
    if len(main_reads) != 1:
        raise ModelsAttestationError("models profile read authority changed")
    expected_read = "BinOp(left=Name(id='PROFILE_LIMIT', ctx=Load()), op=Add(), right=Constant(value=1))"
    if len(main_reads[0].args) != 1 or ast.dump(main_reads[0].args[0]) != expected_read:
        raise ModelsAttestationError("models profile read bound changed")
    required_main = (
        'if list(argv) != ["--attest-models"]:',
        "startup = version._production_startup_evaluation()",
        "if not startup.accepted:\n        sys.stderr.buffer.write(version._startup_diagnostic(startup))\n        return 64",
        "data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)",
        "profile = ModelsProfile.from_bytes(data)\n        _validate_production_profile(profile)\n        result = run_attestation(profile, profile_source=data)",
    )
    if any(marker not in main_text for marker in required_main):
        raise ModelsAttestationError("models production main authority changed")
    assignment_nodes = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    if (
        ast.dump(assignment_nodes.get("EXPECTED_STDERR_BYTES"))
        != "Constant(value=29)"
        or ast.dump(assignment_nodes.get("EXPECTED_STDERR_SHA256"))
        != "Constant(value='53f588bc9a928f4a66908deacaca57dddc7e7ce177a0cc3586b5a501be26e1e8')"
        or ast.dump(assignment_nodes.get("STREAM_LIMIT"))
        != "BinOp(left=Constant(value=64), op=Mult(), right=Constant(value=1024))"
        or ast.dump(assignment_nodes.get("WALL_SECONDS")) != "Constant(value=25.0)"
    ):
        raise ModelsAttestationError("models fixed evidence constants changed")
    if not (
        main_text.index("if not startup.accepted:")
        < main_text.index("data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)")
        < main_text.index("profile = ModelsProfile.from_bytes(data)")
        < main_text.index("result = run_attestation(profile, profile_source=data)")
    ):
        raise ModelsAttestationError("models production main ordering changed")
    return {"byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _validate_production_profile(profile: ModelsProfile) -> None:
    if profile.version_binding_sha256 != EXPECTED_VERSION_BINDING_SHA256:
        raise ModelsAttestationError("version binding is not the accepted production evidence")


def _validate_authority(profile: ModelsProfile) -> None:
    if os.path.dirname(profile.version_root) != profile.temp_parent:
        raise ModelsAttestationError("version evidence is outside its private parent")
    if not os.path.basename(profile.version_root).startswith("agy-version-recovery."):
        raise ModelsAttestationError("version evidence root name is invalid")
    repository = str(Path(__file__).resolve(strict=True).parents[1])
    if os.path.commonpath((profile.temp_parent, repository)) == repository:
        raise ModelsAttestationError("private evidence parent is inside the repository")
    parent = version._open_dir(profile.temp_parent)
    try:
        value = os.fstat(parent)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != 0o700
        ):
            raise ModelsAttestationError("private evidence parent is not owner-private")
    finally:
        os.close(parent)
    for identity, mode in (
        (profile.source_identity, 0o755),
        (profile.snapshot_identity, 0o500),
    ):
        if (
            identity.uid != os.getuid()
            or identity.mode != mode
            or identity.nlink != 1
            or identity.size <= 0
        ):
            raise ModelsAttestationError("executable identity policy is invalid")


def _validate_version_evidence(profile: ModelsProfile) -> None:
    _validate_authority(profile)
    descriptor = version._open_dir(profile.version_root)
    try:
        value = os.fstat(descriptor)
        if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700:
            raise ModelsAttestationError("version evidence root is not owner-private")
        if set(os.listdir(descriptor)) != VERSION_ROOT_FILES:
            raise ModelsAttestationError("version evidence root has an unexpected shape")
        for name in ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"):
            child = os.open(
                name,
                os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
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
                    raise ModelsAttestationError("version evidence directory is invalid")
            finally:
                os.close(child)
        binding_bytes = version._read_at(descriptor, "version.binding.json", PROFILE_LIMIT)
        detached = version._read_at(descriptor, "version.binding.sha256", 128)
        if (
            hashlib.sha256(binding_bytes).hexdigest() != profile.version_binding_sha256
            or detached != (profile.version_binding_sha256 + "\n").encode("ascii")
        ):
            raise ModelsAttestationError("version binding digest changed")
        binding = version._strict_json(binding_bytes)
        if not isinstance(binding, dict):
            raise ModelsAttestationError("version binding is invalid")
        runner = binding.get("runner")
        snapshot = binding.get("snapshot")
        source = binding.get("source")
        observed = binding.get("version")
        limitations = binding.get("limitations")
        if (
            binding.get("claim") != "snapshot-version-recovery"
            or not isinstance(runner, dict)
            or runner.get("byte_count") != VERSION_RUNNER_BYTES
            or runner.get("sha256") != VERSION_RUNNER_SHA256
            or not isinstance(snapshot, dict)
            or snapshot.get("pre") != profile.snapshot_identity.as_dict()
            or snapshot.get("post") != profile.snapshot_identity.as_dict()
            or snapshot.get("sha256") != profile.source_sha256
            or not isinstance(source, dict)
            or source.get("pre") != profile.source_identity.as_dict()
            or source.get("post") != profile.source_identity.as_dict()
            or source.get("sha256") != profile.source_sha256
            or not isinstance(observed, dict)
            or observed.get("exit") != 0
            or observed.get("logical_argv") != [profile.source_path, "--version"]
            or observed.get("observed") != "1.1.11"
            or observed.get("popen_count") != 1
            or not isinstance(limitations, dict)
            or limitations.get("prior_inventory_executable_version_bound") is not False
        ):
            raise ModelsAttestationError("version binding claim is incompatible")
        runner_bytes = version._read_at(descriptor, "runner.py", 128 * 1024)
        runner_digest = version._read_at(descriptor, "runner.py.sha256", 128)
        if (
            len(runner_bytes) != VERSION_RUNNER_BYTES
            or hashlib.sha256(runner_bytes).hexdigest() != VERSION_RUNNER_SHA256
            or runner_digest != (VERSION_RUNNER_SHA256 + "\n").encode("ascii")
        ):
            raise ModelsAttestationError("persisted version runner changed")
        if version._read_at(descriptor, "version.stdout", 128) != b"1.1.11\n":
            raise ModelsAttestationError("version observation changed")
        if version._read_at(descriptor, "version.stderr", 128) != b"":
            raise ModelsAttestationError("version stderr changed")
        version._read_at(descriptor, "version.summary.json", PROFILE_LIMIT)
    finally:
        os.close(descriptor)


def _capture(
    process: subprocess.Popen[bytes], deadline: float
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise ModelsAttestationError("models process did not expose bounded streams")
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
                raise ModelsAttestationError("models process timed out")
            for key, _mask in selector.select(min(remaining, 0.05)):
                stream, captured = buffers[key.fd]
                block = os.read(key.fd, min(8192, STREAM_LIMIT + 1 - len(captured)))
                if not block:
                    selector.unregister(key.fd)
                    stream.close()
                    continue
                captured.extend(block)
                if len(captured) > STREAM_LIMIT:
                    raise ModelsAttestationError("models output exceeded its bound")
    return bytes(buffers[stdout_descriptor][1]), bytes(buffers[stderr_descriptor][1])


def _validate_stderr(
    raw: bytes,
    expected: tuple[int, str] = (EXPECTED_STDERR_BYTES, EXPECTED_STDERR_SHA256),
) -> str:
    expected_bytes, expected_sha256 = expected
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if (
        type(expected_bytes) is not int
        or expected_bytes < 0
        or not isinstance(expected_sha256, str)
        or not version._is_sha256(expected_sha256)
        or len(raw) != expected_bytes
        or observed_sha256 != expected_sha256
    ):
        raise ModelsAttestationError("models stderr differs from reviewed evidence")
    return observed_sha256


def run_attestation(
    profile: ModelsProfile,
    *,
    calls: version.RunnerCalls = version.REAL_CALLS,
    module_source: Optional[bytes] = None,
    profile_source: Optional[bytes] = None,
    stderr_contract: tuple[int, str] = (EXPECTED_STDERR_BYTES, EXPECTED_STDERR_SHA256),
) -> dict[str, object]:
    """Run exactly one snapshot-backed models inventory observation."""

    if not all(hasattr(signal, name) for name in ("pthread_sigmask", "sigpending", "sigwait")):
        raise ModelsAttestationError("required signal primitives are unavailable")
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
    old_handlers = {item: signal.getsignal(item) for item in version.LIFECYCLE_SIGNALS}
    root: Optional[Path] = None
    publisher: Optional[version.Publisher] = None
    source_parent = source_fd = snapshot_parent = snapshot_fd = None
    process: Optional[subprocess.Popen[bytes]] = None
    process_active = False
    disarmed = False
    ignore_until_unblocked = False

    def interrupted(signum: int, _frame: object) -> None:
        raise ModelsAttestationInterrupted(signum)

    for item in version.LIFECYCLE_SIGNALS:
        signal.signal(item, interrupted)
    signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
    try:
        runner_source, _version_source, inventory_source = _canonical_sources(module_source)
        source_contract = validate_source_contract(runner_source)
        exact_profile = (
            _canonical_json(dataclasses.asdict(profile))
            if profile_source is None
            else profile_source
        )
        if ModelsProfile.from_bytes(exact_profile) != profile:
            raise ModelsAttestationError("exact profile bytes do not match the parsed profile")
        _validate_version_evidence(profile)
        root = Path(tempfile.mkdtemp(prefix="agy-models-attestation.", dir=profile.temp_parent))
        os.chmod(root, 0o700)
        publisher = version.Publisher(root, calls)
        for name in ("cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"):
            (root / name).mkdir(mode=0o700)
        runner_sha = publisher.publish("models_runner.py", runner_source)
        publisher.publish("models_runner.py.sha256", (runner_sha + "\n").encode("ascii"))
        parser_sha = publisher.publish("agy_inventory.py", inventory_source)
        publisher.publish("agy_inventory.py.sha256", (parser_sha + "\n").encode("ascii"))
        profile_sha = publisher.publish("models.profile.json", exact_profile)
        if runner_sha != source_contract["sha256"] or parser_sha != INVENTORY_PARSER_SHA256:
            raise ModelsAttestationError("persisted canonical source changed")
        source_parent, source_fd = version._open_attested(
            profile.source_path, profile.source_identity, profile.source_sha256, 0o755
        )
        snapshot_parent, snapshot_fd = version._open_attested(
            profile.snapshot_path, profile.snapshot_identity, profile.source_sha256, 0o500
        )
        argv = [profile.source_path, "models"]
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
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
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
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        try:
            exit_code = version._close_reserved_group(process, calls)
            process_active = False
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        if exit_code != 0:
            raise ModelsAttestationError("models process failed")
        evidence = inventory.parse_inventory_bytes(stdout)
        if (
            evidence.normalized_sha256 != profile.inventory_normalized_sha256
            or evidence.slugs != tuple(sorted(inventory.EXPECTED_SLUGS))
            or evidence.line_count != 11
        ):
            raise ModelsAttestationError("models inventory changed")
        stderr_sha = _validate_stderr(stderr, stderr_contract)
        source_post = version._verify_attested_path(
            source_parent, profile.source_path, source_fd, profile.source_identity, profile.source_sha256
        )
        snapshot_post = version._verify_attested_path(
            snapshot_parent, profile.snapshot_path, snapshot_fd, profile.snapshot_identity, profile.source_sha256
        )
        stdout_sha = publisher.publish("models.stdout", stdout)
        published_stderr_sha = publisher.publish("models.stderr", stderr)
        if published_stderr_sha != stderr_sha:
            raise ModelsAttestationError("persisted stderr digest changed")
        logical_sha = hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("ascii")).hexdigest()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        summary = {
            "call_count": 1,
            "child_exit": exit_code,
            "claim": "snapshot-models-inventory",
            "elapsed_ms": elapsed_ms,
            "line_count": evidence.line_count,
            "logical_argv_sha256": logical_sha,
            "normalized_sha256": evidence.normalized_sha256,
            "profile_bytes": len(exact_profile),
            "profile_sha256": profile_sha,
            "schema_version": 1,
            "status": "accepted",
            "stderr_bytes": len(stderr),
            "stdout_bytes": len(stdout),
            "timeout": False,
        }
        summary_sha = publisher.publish("models.summary.json", _canonical_json(summary))
        binding = {
            "artifacts": {
                "agy_inventory.py": parser_sha,
                "models.stderr": stderr_sha,
                "models.stdout": stdout_sha,
                "models.summary.json": summary_sha,
                "models_runner.py": runner_sha,
                "models.profile.json": profile_sha,
            },
            "claim": "snapshot-models-inventory",
            "inventory": {
                "line_count": evidence.line_count,
                "normalized_sha256": evidence.normalized_sha256,
                "parser": {"byte_count": len(inventory_source), "sha256": parser_sha},
                "slugs": list(evidence.slugs),
            },
            "limitations": {
                "cost_and_quota_unknown": True,
                "metadata_advance_authorized": False,
                "network_absence_os_enforced": False,
                "provider_backend_proven": False,
                "retry_behavior_proven": False,
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
            "runner": {"byte_count": len(runner_source), "sha256": runner_sha},
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
            "version": {"binding_sha256": profile.version_binding_sha256},
        }
        binding_sha = publisher.publish("models.binding.json", _canonical_json(binding))
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        publisher.publish("models.binding.sha256", (binding_sha + "\n").encode("ascii"))
        pending = set(signal.sigpending()).intersection(version.LIFECYCLE_SIGNALS)
        if pending:
            first = signal.sigwait(pending)
            publisher.rollback()
            raise ModelsAttestationInterrupted(first)
        for item in version.LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        pending = set(signal.sigpending()).intersection(version.LIFECYCLE_SIGNALS)
        if pending:
            first = signal.sigwait(pending)
            publisher.rollback()
            raise ModelsAttestationInterrupted(first)
        disarmed = True
        ignore_until_unblocked = True
        return {
            "artifact_root": str(root),
            "binding_sha256": binding_sha,
            "call_count": 1,
            "claim": "snapshot-models-inventory",
            "line_count": evidence.line_count,
            "normalized_sha256": evidence.normalized_sha256,
            "runner_sha256": runner_sha,
            "snapshot_sha256": profile.source_sha256,
            "status": "accepted",
            "stderr_sha256": published_stderr_sha,
            "stdout_sha256": stdout_sha,
        }
    except ModelsAttestationInterrupted as exc:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        if process is not None and process_active:
            version._terminate_group(process, calls)
        if publisher is not None:
            publisher.rollback()
        for item in version.LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        ignore_until_unblocked = True
        raise SystemExit(128 + exc.signum)
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
        cleanup_failure: Optional[BaseException] = None
        if process is not None and process_active:
            try:
                version._terminate_group(process, calls)
            except BaseException as exc:
                cleanup_failure = exc
        if publisher is not None:
            try:
                publisher.rollback()
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        for item in version.LIFECYCLE_SIGNALS:
            signal.signal(item, signal.SIG_IGN)
        ignore_until_unblocked = True
        if cleanup_failure is not None:
            raise cleanup_failure
        raise
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)
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


def _inventory_bytes() -> bytes:
    lines = []
    for slug in inventory.EXPECTED_SLUGS:
        if slug == "gpt-oss-120b-medium":
            lines.append("gpt-oss display gpt-oss-120b-medium")
        else:
            lines.append(f"available {slug}")
    return ("\n".join(lines) + "\n").encode("ascii")


def _fake_executable(stdout: bytes, stderr: bytes = b"", exit_code: int = 0) -> bytes:
    return (
        b"#!/usr/bin/python3\nimport os\n"
        + b"os.write(1," + repr(stdout).encode("ascii") + b")\n"
        + b"os.write(2," + repr(stderr).encode("ascii") + b")\n"
        + f"raise SystemExit({exit_code})\n".encode("ascii")
    )


def _synthetic_profile(
    root: Path,
    *,
    models_stdout: Optional[bytes] = None,
    models_stderr: bytes = b"",
    models_exit: int = 0,
    models_delay: float = 0.0,
) -> ModelsProfile:
    source = root / "agy"
    selected_stdout = _inventory_bytes() if models_stdout is None else models_stdout
    dual = (
        b"#!/usr/bin/python3\nimport os,sys,time\n"
        b"if sys.argv[1:] == ['--version']: os.write(1,b'1.1.11\\n')\n"
        b"elif sys.argv[1:] == ['models']:\n"
        + f" time.sleep({models_delay!r})\n".encode("ascii")
        + b" os.write(1," + repr(selected_stdout).encode("ascii") + b")\n"
        + b" os.write(2," + repr(models_stderr).encode("ascii") + b")\n"
        + f" raise SystemExit({models_exit})\n".encode("ascii")
        + b"else: raise SystemExit(2)\n"
    )
    source.write_bytes(dual)
    source.chmod(0o755)
    old = root / "agy-version-attestation.synthetic"
    old.mkdir(mode=0o700)
    snapshot = old / "agy.snapshot"
    snapshot.write_bytes(dual)
    snapshot.chmod(0o500)
    sha = hashlib.sha256(dual).hexdigest()
    prior_profile = version.AttestationProfile(
        prior_binding_sha256="0" * 64,
        prior_root=str(old),
        snapshot_identity=version.FileIdentity.from_stat(snapshot.stat()),
        snapshot_path=str(snapshot),
        source_identity=version.FileIdentity.from_stat(source.stat()),
        source_path=str(source),
        source_sha256=sha,
        temp_parent=str(root),
    )
    for name in version.PRIOR_FILES:
        path = old / name
        if name == "agy.snapshot":
            continue
        if name in {"cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"}:
            path.mkdir(mode=0o700)
        else:
            path.write_bytes(b"")
            path.chmod(0o600)
    prior_value = {
        "claim": "snapshot-version-only",
        "inventory": {"executable_version_bound": False},
        "snapshot": {"pre": prior_profile.snapshot_identity.as_dict(), "sha256": sha},
        "source": {"pre": prior_profile.source_identity.as_dict(), "sha256": sha},
        "version": {"logical_argv": [str(source), "--version"]},
    }
    prior_bytes = _canonical_json(prior_value)
    prior_sha = hashlib.sha256(prior_bytes).hexdigest()
    (old / "version.binding.json").write_bytes(prior_bytes)
    (old / "version.binding.sha256").write_bytes((prior_sha + "\n").encode("ascii"))
    (old / "version.binding.json").chmod(0o600)
    (old / "version.binding.sha256").chmod(0o600)
    prior_profile = dataclasses.replace(prior_profile, prior_binding_sha256=prior_sha)
    second = version.run_attestation(prior_profile)
    version_root = Path(str(second["artifact_root"]))
    for name in ("cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"):
        directory = version_root / name
        for child in tuple(directory.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    return ModelsProfile(
        inventory_normalized_sha256=EXPECTED_NORMALIZED_SHA256,
        snapshot_identity=prior_profile.snapshot_identity,
        snapshot_path=str(snapshot),
        source_identity=prior_profile.source_identity,
        source_path=str(source),
        source_sha256=sha,
        temp_parent=str(root),
        version_binding_sha256=str(second["binding_sha256"]),
        version_root=str(second["artifact_root"]),
    )


def run_offline_self_test() -> dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="agy-models-runner-selftest.")).resolve()
    root.chmod(0o700)
    try:
        profile = _synthetic_profile(root)
        profile_source = _canonical_json(dataclasses.asdict(profile))
        result = run_attestation(
            profile,
            profile_source=profile_source,
            stderr_contract=(0, hashlib.sha256(b"").hexdigest()),
        )
        output_root = Path(str(result["artifact_root"]))
        try:
            if set(path.name for path in output_root.iterdir()) != set(OUTPUT_FILES) | {
                "cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"
            }:
                raise ModelsAttestationError("self-test artifact shape changed")
            return {
                "accepted": 1,
                "claim": result["claim"],
                "mutations_killed": 1,
                "status": "accepted",
            }
        finally:
            shutil.rmtree(output_root)
    finally:
        shutil.rmtree(root)


def main(argv: Sequence[str]) -> int:
    if list(argv) == ["--self-test"]:
        try:
            result = run_offline_self_test()
        except (ModelsAttestationError, version.AttestationError, OSError, subprocess.SubprocessError):
            print("models attestation runner: rejected", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if list(argv) != ["--attest-models"]:
        print("models attestation runner: invalid invocation", file=sys.stderr)
        return 64
    startup = version._production_startup_evaluation()
    if not startup.accepted:
        sys.stderr.buffer.write(version._startup_diagnostic(startup))
        return 64
    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)
    try:
        profile = ModelsProfile.from_bytes(data)
        _validate_production_profile(profile)
        result = run_attestation(profile, profile_source=data)
    except (
        ModelsAttestationError,
        inventory.InventoryEvidenceError,
        version.AttestationError,
        OSError,
        subprocess.SubprocessError,
    ):
        print("models attestation runner: rejected", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
