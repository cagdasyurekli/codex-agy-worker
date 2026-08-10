#!/usr/bin/env python3
"""Offline tests for the canonical fixed-profile models observation runner."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "models_attestation_runner.py"
SPEC = importlib.util.spec_from_file_location("models_attestation_runner_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TMP = Path(tempfile.mkdtemp(prefix="agyworker-models-runner-tests.")).resolve()
TMP.chmod(0o700)
passed = 0
failed = 0


def check(name: str, predicate: Callable[[], bool]) -> None:
    global passed, failed
    try:
        result = bool(predicate())
    except BaseException as exc:
        result = False
        detail = f" ({type(exc).__name__}: {exc})"
    else:
        detail = ""
    if result:
        passed += 1
    else:
        failed += 1
        print(f"FAIL models attestation runner: {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (
        MODULE.ModelsAttestationError,
        MODULE.inventory.InventoryEvidenceError,
        MODULE.version.AttestationError,
        OSError,
        subprocess.SubprocessError,
    ):
        return True
    return False


def replace_last(data: bytes, old: bytes, new: bytes) -> bytes:
    position = data.rfind(old)
    if position < 0:
        raise AssertionError(f"mutation target missing: {old!r}")
    return data[:position] + new + data[position + len(old):]


def profile_bytes(profile: object) -> bytes:
    return MODULE._canonical_json(dataclasses.asdict(profile))


def cleanup_profile(root: Path, profile: object | None = None, result: object | None = None) -> None:
    if result and isinstance(result, dict):
        artifact = Path(str(result.get("artifact_root", "")))
        if artifact.is_dir() and artifact.parent == root:
            shutil.rmtree(artifact)
    if profile is not None:
        artifact = Path(str(getattr(profile, "version_root", "")))
        if artifact.is_dir() and artifact.parent == root:
            shutil.rmtree(artifact)
    if root.exists():
        shutil.rmtree(root)


source = MODULE_PATH.read_bytes()
version_source = (ROOT / "scripts" / "version_attestation_runner.py").read_bytes()
inventory_source = (ROOT / "scripts" / "agy_inventory.py").read_bytes()
contract = MODULE.validate_source_contract(source)

check("module imports with bytecode disabled", lambda: sys.dont_write_bytecode)
check("models wall is exactly 25 seconds", lambda: MODULE.WALL_SECONDS == 25.0)
check("streams are independently capped at 64 KiB", lambda: MODULE.STREAM_LIMIT == 64 * 1024)
check("reviewed normalized inventory hash is pinned", lambda: MODULE.EXPECTED_NORMALIZED_SHA256 == "8d46bcac6b8f27995635d91dc6f5a0e549d351e707efe11a82d8b6593fe12daf")
check("accepted version binding hash is pinned", lambda: MODULE.EXPECTED_VERSION_BINDING_SHA256 == "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2")
check("reviewed stderr byte count is pinned", lambda: MODULE.EXPECTED_STDERR_BYTES == 29)
check("reviewed stderr digest is pinned", lambda: MODULE.EXPECTED_STDERR_SHA256 == "53f588bc9a928f4a66908deacaca57dddc7e7ce177a0cc3586b5a501be26e1e8")
check("version runner byte count is pinned", lambda: len(version_source) == MODULE.VERSION_RUNNER_BYTES)
check("version runner digest is pinned", lambda: hashlib.sha256(version_source).hexdigest() == MODULE.VERSION_RUNNER_SHA256)
check("inventory parser byte count is pinned", lambda: len(inventory_source) == MODULE.INVENTORY_PARSER_BYTES)
check("inventory parser digest is pinned", lambda: hashlib.sha256(inventory_source).hexdigest() == MODULE.INVENTORY_PARSER_SHA256)
check("canonical source contract is accepted", lambda: contract["sha256"] == hashlib.sha256(source).hexdigest())
check("runner contains one Popen authority", lambda: source.count(b"calls.popen(") == 1)
check("actual executable is the snapshot", lambda: b"executable=profile.snapshot_path" in source)
check("logical argv is exact models only", lambda: b'argv = [profile.source_path, "models"]' in source)
check("runner does not expose model selector", lambda: b'argv = [profile.source_path, "--model"' not in source)
check("runner does not expose effort selector", lambda: b'argv = [profile.source_path, "--effort"' not in source)
check("production CLI is attest-models only", lambda: b'["--attest-models"]' in source and b'["--attest-version"]' not in source)
check("completion binding is detached and last", lambda: MODULE.OUTPUT_FILES[-1] == "models.binding.sha256")

mutated = source.replace(b"                executable=profile.snapshot_path,\n", b"", 1)
check("mutation removing executable override is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"                executable=profile.snapshot_path,", b"                executable=profile.source_path,", 1)
check("mutation changing the attested executable is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            process = calls.popen(\n", b"            calls.popen(argv)\n            process = calls.popen(\n", 1)
check("mutation adding a second Popen is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            process = calls.popen(\n", b"            subprocess.Popen(argv)\n            process = calls.popen(\n", 1)
check("mutation adding a direct fallback Popen is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"def hidden_popen(*args, **kwargs):\n    return subprocess.Popen(*args, **kwargs)\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
).replace(
    b"            process = calls.popen(\n",
    b"            hidden_popen(argv)\n            process = calls.popen(\n",
    1,
)
check("mutation hiding a second Popen in a helper is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"def hidden_run(*args, **kwargs):\n    return subprocess.run(*args, **kwargs)\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
).replace(
    b"            process = calls.popen(\n",
    b"            hidden_run(argv, check=False)\n            process = calls.popen(\n",
    1,
)
check("mutation hiding subprocess run in a helper is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"hidden_run = subprocess.run\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
).replace(
    b"            process = calls.popen(\n",
    b"            hidden_run(argv, check=False)\n            process = calls.popen(\n",
    1,
)
check("mutation aliasing subprocess run is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"hidden_subprocess = subprocess\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
).replace(
    b"            process = calls.popen(\n",
    b"            hidden_subprocess.run(argv, check=False)\n            process = calls.popen(\n",
    1,
)
check("mutation aliasing the subprocess module is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"            process = calls.popen(\n",
    b"            hidden_calls = calls\n            hidden_calls.popen(argv)\n            process = calls.popen(\n",
    1,
)
check("mutation aliasing injected call authority is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"def hidden_dynamic_run(*args, **kwargs):\n    return importlib.import_module('subprocess').run(*args, **kwargs)\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
).replace(
    b"            process = calls.popen(\n",
    b"            hidden_dynamic_run(argv, check=False)\n            process = calls.popen(\n",
    1,
)
check("mutation dynamically importing subprocess is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"hidden_dict_run = subprocess.__dict__['run']\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
)
check("mutation looking up subprocess through module dict is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"hidden_getattribute_run = subprocess.__getattribute__('run')\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
)
check("mutation looking up subprocess through getattribute is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"def hidden_dict_run(*args, **kwargs):\n    return subprocess.__dict__['run'](*args, **kwargs)\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
).replace(
    b"            process_active = True\n",
    b"            process_active = True\n            hidden_dict_run(argv, check=False)\n",
    1,
)
check("mutation launching through module dict after tracked Popen is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"import subprocess\n",
    b"import subprocess\nfrom subprocess import call as hidden_call\n",
    1,
).replace(
    b"            process = calls.popen(\n",
    b"            hidden_call(argv)\n            process = calls.popen(\n",
    1,
)
check("mutation importing a subprocess launch alias is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"def hidden_system(command):\n    return os.system(command)\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
).replace(
    b"            process = calls.popen(\n",
    b"            hidden_system(profile.source_path)\n            process = calls.popen(\n",
    1,
)
check("mutation hiding os system in a helper is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'        argv = [profile.source_path, "models"]', b'        argv = [profile.source_path, "/model"]', 1)
check("mutation changing logical argv is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"        deadline = started + WALL_SECONDS", b"        deadline = started + 999", 1)
check("mutation removing wall binding is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"min(8192, STREAM_LIMIT + 1 - len(captured))", b"999999", 1)
check("mutation removing stream cap binding is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"        evidence = inventory.parse_inventory_bytes(stdout)", b"        evidence = inventory.parse_inventory_bytes(b'')", 1)
check("mutation changing parser input is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            hashlib.sha256(binding_bytes).hexdigest() != profile.version_binding_sha256", b"            False", 1)
check("mutation removing version binding is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'        publisher.publish("models.binding.sha256",', b'        publisher.publish("models.complete",', 1)
check("mutation changing detached marker is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"    if profile.version_binding_sha256 != EXPECTED_VERSION_BINDING_SHA256:", b"    if False:", 1)
check("mutation removing accepted binding guard is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"        _validate_production_profile(profile)\n", b"", 1)
check("mutation bypassing production profile validation is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = replace_last(source, b'if list(argv) != ["--attest-models"]:', b"if False:")
check("mutation bypassing exact production CLI is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"    if not startup.accepted:", b"    if False:", 1)
check("mutation bypassing startup rejection is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"        return 64\n    data = sys.stdin.buffer.read", b"        pass\n    data = sys.stdin.buffer.read", 1)
check("mutation removing immediate startup exit is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)", b"    ignored = sys.stdin.buffer.read(1)\n    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)", 1)
check("mutation adding a prevalidation stdin read is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = replace_last(source, b"sys.stdin.buffer.read(PROFILE_LIMIT + 1)", b"sys.stdin.buffer.read(PROFILE_LIMIT + 2)")
check("mutation changing profile read cap is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = replace_last(source, b"result = run_attestation(profile, profile_source=data)", b"result = run_attestation(profile)")
check("mutation dropping exact profile source is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = replace_last(source, b'profile_sha = publisher.publish("models.profile.json", exact_profile)', b"profile_sha = hashlib.sha256(exact_profile).hexdigest()")
check("mutation dropping private profile publication is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"EXPECTED_STDERR_BYTES = 29", b"EXPECTED_STDERR_BYTES = 30", 1)
check("mutation changing reviewed stderr byte count is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"53f588bc9a928f4a66908deacaca57dddc7e7ce177a0cc3586b5a501be26e1e8", b"0" * 64, 1)
check("mutation changing reviewed stderr digest is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = replace_last(source, b"stderr_sha = _validate_stderr(stderr, stderr_contract)", b"stderr_sha = hashlib.sha256(stderr).hexdigest()")
check("mutation bypassing exact stderr validation is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"                    or os.listdir(child)\n", b"", 1)
check("mutation permitting nonempty version directories is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'            "HOME": str(root / "home"),', b'            "HOME": os.environ["HOME"],', 1)
check("mutation inheriting caller HOME is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)\n", b"        environment.update(os.environ)\n        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)\n", 1)
check("mutation merging caller environment is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'            "TMPDIR": str(root / "tmp"),\n', b"", 1)
check("mutation omitting private TMPDIR is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'            "XDG_CONFIG_HOME": str(root / "xdg-config"),\n', b"", 1)
check("mutation omitting private XDG config is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'            "PATH": "/usr/bin:/bin",\n', b'            "PATH": "/usr/bin:/bin",\n            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),\n', 1)
check("mutation inheriting Python startup path is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"                env=environment,", b"                env=os.environ.copy(),", 1)
check("mutation bypassing fixed environment is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'                cwd=str(root / "cwd"),', b"                cwd=profile.temp_parent,", 1)
check("mutation changing private cwd is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"                stdin=subprocess.DEVNULL,", b"                stdin=subprocess.PIPE,", 1)
check("mutation exposing child stdin is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"                stderr=subprocess.PIPE,", b"                stderr=subprocess.DEVNULL,", 1)
check("mutation removing bounded stderr capture is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"            _revalidate_private_directories(root, private_directory_identities)\n",
    b"",
    1,
)
check("mutation removing private directory revalidation is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"            process = calls.popen(\n",
    b'            shutil.rmtree(root / "home")\n            os.symlink(os.environ["HOME"], root / "home")\n            process = calls.popen(\n',
    1,
)
check("mutation swapping caller HOME after revalidation is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"            process_active = True\n",
    b'            process_active = True\n            shutil.rmtree(root / "home")\n            os.symlink(os.environ["HOME"], root / "home")\n',
    1,
)
check("mutation swapping caller HOME after Popen is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        started = time.monotonic()\n",
    b'        os.symlink(os.environ["HOME"], root / "home")\n        started = time.monotonic()\n',
    1,
)
check("mutation adding a post-launch filesystem symlink is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        started = time.monotonic()\n",
    b'        shutil.rmtree(root / "home")\n        started = time.monotonic()\n',
    1,
)
check("mutation adding post-launch recursive deletion is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"class ModelsAttestationError(ValueError):\n",
    b"def hidden_home_swap(root):\n    shutil.rmtree(root / 'home')\n    os.symlink(os.environ['HOME'], root / 'home')\n\n\nclass ModelsAttestationError(ValueError):\n",
    1,
).replace(
    b"        started = time.monotonic()\n",
    b"        hidden_home_swap(root)\n        started = time.monotonic()\n",
    1,
)
check("mutation invoking a hidden HOME swap helper post-launch is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        started = time.monotonic()\n",
    b"        run_offline_self_test()\n        started = time.monotonic()\n",
    1,
)
check("mutation recursively invoking offline self-test post-launch is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    if process.stdout is None or process.stderr is None:\n",
    b"    run_offline_self_test()\n    if process.stdout is None or process.stderr is None:\n",
    1,
)
check("mutation recursively launching through capture is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        result = run_attestation(profile, profile_source=data)\n",
    b"        result = run_attestation(profile, profile_source=data)\n        run_offline_self_test()\n",
    1,
)
check("mutation recursively launching through production main is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = replace_last(
    source,
    b"os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,\n",
    b"os.O_RDONLY | version.DIRECTORY | version.CLOEXEC,\n",
)
check("mutation removing private directory nofollow is killed", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))


def private_directories_are_revalidated() -> bool:
    root = TMP / "private-directory-revalidation"
    root.mkdir(mode=0o700)
    expected = {}
    for name in MODULE.PRIVATE_DIRECTORY_NAMES:
        child = root / name
        child.mkdir(mode=0o700)
        expected[name] = MODULE._private_directory_identity(child)
    MODULE._revalidate_private_directories(root, expected)
    return True


check("all six private directories accept exact empty identities", private_directories_are_revalidated)


def private_directory_symlink_swaps_reject() -> bool:
    root = TMP / "private-directory-symlink-swaps"
    root.mkdir(mode=0o700)
    target = root / "caller-home"
    target.mkdir(mode=0o700)
    (target / "session.marker").write_bytes(b"private-synthetic-marker\n")
    expected = {}
    for name in MODULE.PRIVATE_DIRECTORY_NAMES:
        child = root / name
        child.mkdir(mode=0o700)
        expected[name] = MODULE._private_directory_identity(child)
    for name in MODULE.PRIVATE_DIRECTORY_NAMES:
        child = root / name
        saved = root / (name + ".owned")
        child.rename(saved)
        os.symlink(target, child)
        rejected = rejects(lambda: MODULE._revalidate_private_directories(root, expected))
        child.unlink()
        saved.rename(child)
        if not rejected:
            return False
    return (target / "session.marker").read_bytes() == b"private-synthetic-marker\n"


check("symlink swaps of every private directory reject without target mutation", private_directory_symlink_swaps_reject)

minimal = {
    "inventory_normalized_sha256": MODULE.EXPECTED_NORMALIZED_SHA256,
    "snapshot_identity": {key: 0 for key in MODULE.version.IDENTITY_KEYS},
    "snapshot_path": "/private/tmp/snapshot",
    "source_identity": {key: 0 for key in MODULE.version.IDENTITY_KEYS},
    "source_path": "/private/tmp/agy",
    "source_sha256": "1" * 64,
    "temp_parent": "/private/tmp",
    "version_binding_sha256": "2" * 64,
    "version_root": "/private/tmp/agy-version-recovery.synthetic",
}
check("strict profile shape accepts exact keys", lambda: isinstance(MODULE.ModelsProfile.from_bytes(MODULE._canonical_json(minimal)), MODULE.ModelsProfile))
extra = dict(minimal, extra=True)
check("strict profile rejects extra keys", lambda: rejects(lambda: MODULE.ModelsProfile.from_bytes(MODULE._canonical_json(extra))))
missing = dict(minimal); missing.pop("version_binding_sha256")
check("strict profile rejects missing keys", lambda: rejects(lambda: MODULE.ModelsProfile.from_bytes(MODULE._canonical_json(missing))))
wrong = dict(minimal, inventory_normalized_sha256="0" * 64)
check("profile rejects unreviewed normalized hash", lambda: rejects(lambda: MODULE.ModelsProfile.from_bytes(MODULE._canonical_json(wrong))))
wrong = dict(minimal, source_sha256="not-a-digest")
check("profile rejects malformed digest", lambda: rejects(lambda: MODULE.ModelsProfile.from_bytes(MODULE._canonical_json(wrong))))
wrong = dict(minimal, source_path="relative/agy")
check("profile rejects relative source", lambda: rejects(lambda: MODULE.ModelsProfile.from_bytes(MODULE._canonical_json(wrong))))
wrong = dict(minimal, snapshot_path="/private/tmp/../tmp/snapshot")
check("profile rejects noncanonical snapshot", lambda: rejects(lambda: MODULE.ModelsProfile.from_bytes(MODULE._canonical_json(wrong))))
parsed_minimal = MODULE.ModelsProfile.from_bytes(MODULE._canonical_json(minimal))
check("production rejects a nonaccepted version binding", lambda: rejects(lambda: MODULE._validate_production_profile(parsed_minimal)))
accepted_minimal = dataclasses.replace(parsed_minimal, version_binding_sha256=MODULE.EXPECTED_VERSION_BINDING_SHA256)
check("production accepts only the pinned version binding", lambda: MODULE._validate_production_profile(accepted_minimal) is None)

check("reviewed inventory accepts same-line display alias", lambda: MODULE.inventory.parse_inventory_bytes(MODULE._inventory_bytes()).normalized_sha256 == MODULE.EXPECTED_NORMALIZED_SHA256)
bad_inventory = MODULE._inventory_bytes().replace(b"gpt-oss display gpt-oss-120b-medium", b"gpt-oss")
check("standalone display alias is rejected", lambda: rejects(lambda: MODULE.inventory.parse_inventory_bytes(bad_inventory)))
bad_inventory = MODULE._inventory_bytes().replace(b"available gemini-3.6-flash-low\n", b"")
check("missing reviewed slug is rejected", lambda: rejects(lambda: MODULE.inventory.parse_inventory_bytes(bad_inventory)))
bad_inventory = MODULE._inventory_bytes().replace(b"available gemini-3.6-flash-low", b"available gemini-unknown gemini-3.6-flash-low")
check("unknown provider token is rejected", lambda: rejects(lambda: MODULE.inventory.parse_inventory_bytes(bad_inventory)))
empty_stderr_contract = (0, hashlib.sha256(b"").hexdigest())
check("synthetic exact stderr contract is accepted", lambda: MODULE._validate_stderr(b"", empty_stderr_contract) == empty_stderr_contract[1])
check("arbitrary clean informational stderr is rejected", lambda: rejects(lambda: MODULE._validate_stderr(b"Using installed account context\n")))
check("authentication stderr is non-escalatable", lambda: rejects(lambda: MODULE._validate_stderr(b"Authentication required\n")))
check("permission stderr is non-escalatable", lambda: rejects(lambda: MODULE._validate_stderr(b"permission denied\n")))
check("malformed stderr UTF-8 is rejected", lambda: rejects(lambda: MODULE._validate_stderr(b"\xff")))
check("stderr control bytes are rejected", lambda: rejects(lambda: MODULE._validate_stderr(b"ok\x00")))


def positive_call_contract() -> bool:
    root = TMP / "positive"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root)
    seen = []

    def tracking(*args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.Popen(*args, **kwargs)

    result = None
    exact_profile = json.dumps(
        dataclasses.asdict(profile), sort_keys=False, indent=1
    ).encode("ascii") + b"\n"
    try:
        result = MODULE.run_attestation(
            profile,
            calls=MODULE.version.RunnerCalls(popen=tracking),
            module_source=source,
            profile_source=exact_profile,
            stderr_contract=empty_stderr_contract,
        )
        args, kwargs = seen[0]
        artifact = Path(str(result["artifact_root"]))
        binding = json.loads((artifact / "models.binding.json").read_text("ascii"))
        expected_environment = {
            "HOME": str(artifact / "home"),
            "TMPDIR": str(artifact / "tmp"),
            "XDG_CONFIG_HOME": str(artifact / "xdg-config"),
            "XDG_CACHE_HOME": str(artifact / "xdg-cache"),
            "XDG_STATE_HOME": str(artifact / "xdg-state"),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PATH": "/usr/bin:/bin",
        }
        return (
            len(seen) == 1
            and args == ([profile.source_path, "models"],)
            and kwargs["executable"] == profile.snapshot_path
            and kwargs["stdin"] is subprocess.DEVNULL
            and kwargs["stdout"] is subprocess.PIPE
            and kwargs["stderr"] is subprocess.PIPE
            and kwargs["cwd"] == str(artifact / "cwd")
            and kwargs["env"] == expected_environment
            and kwargs["start_new_session"] is True
            and set(kwargs) == {
                "cwd",
                "env",
                "executable",
                "start_new_session",
                "stderr",
                "stdin",
                "stdout",
            }
            and result["normalized_sha256"] == MODULE.EXPECTED_NORMALIZED_SHA256
            and binding["version"]["binding_sha256"] == profile.version_binding_sha256
            and binding["profile"]["byte_count"] == len(exact_profile)
            and binding["profile"]["sha256"] == hashlib.sha256(exact_profile).hexdigest()
            and (artifact / "models.profile.json").read_bytes() == exact_profile
            and binding["models"]["popen_count"] == 1
            and (artifact / "models.binding.sha256").read_text("ascii").strip() == result["binding_sha256"]
        )
    finally:
        cleanup_profile(root, profile, result)


check("one synthetic call binds argv snapshot parser version and marker", positive_call_contract)


def rejected_execution(label: str, **kwargs) -> bool:
    root = TMP / label
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root, **kwargs)
    try:
        rejected = rejects(lambda: MODULE.run_attestation(profile, module_source=source))
        return rejected and not any(root.glob("agy-models-attestation.*/models.binding.sha256"))
    finally:
        cleanup_profile(root, profile)


check("nonzero models exit is rejected", lambda: rejected_execution("nonzero", models_exit=7))
check("malformed inventory is rejected", lambda: rejected_execution("malformed", models_stdout=b"not inventory\n"))
check("auth failure text is rejected", lambda: rejected_execution("auth", models_stderr=b"authentication required\n"))
check("stdout overflow is rejected", lambda: rejected_execution("stdout-overflow", models_stdout=b"x" * (MODULE.STREAM_LIMIT + 1)))
check("stderr overflow is rejected", lambda: rejected_execution("stderr-overflow", models_stderr=b"x" * (MODULE.STREAM_LIMIT + 1)))


def auth_required_inventory_stays_rejected() -> bool:
    root = TMP / "auth-required-isolation"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root, models_require_session=True)
    metadata_paths = (
        ROOT / "compat" / "agy-verified-version.txt",
        ROOT / "compat" / "agy-last-reviewed.txt",
        ROOT / "compat" / "agy-upstream-head.txt",
        ROOT / "compat" / "agy-model-effort-matrix.json",
        ROOT / "compat" / "agy-model-effort-matrix.sha256",
        ROOT / "compat" / "agy-distribution-manifest.json",
        ROOT / "compat" / "sources.md",
    )
    metadata_before = {path: path.read_bytes() for path in metadata_paths}
    direct = subprocess.run(
        [profile.source_path, "models"],
        executable=profile.snapshot_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(root),
        env={
            "HOME": str(root / "caller-home"),
            "TMPDIR": str(root),
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        },
        check=False,
    )
    try:
        rejected = rejects(
            lambda: MODULE.run_attestation(
                profile,
                module_source=source,
                stderr_contract=empty_stderr_contract,
            )
        )
        metadata_after = {path: path.read_bytes() for path in metadata_paths}
        return (
            direct.returncode == 0
            and direct.stdout == MODULE._inventory_bytes()
            and rejected
            and metadata_before == metadata_after
            and not any(root.glob("agy-models-attestation.*/models.binding.sha256"))
        )
    finally:
        cleanup_profile(root, profile)


check(
    "auth-required inventory rejects without completion or metadata advance",
    auth_required_inventory_stays_rejected,
)


def version_binding_mismatch() -> bool:
    root = TMP / "binding-mismatch"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root)
    candidate = dataclasses.replace(profile, version_binding_sha256="0" * 64)
    try:
        return rejects(lambda: MODULE._validate_version_evidence(candidate))
    finally:
        cleanup_profile(root, profile)


check("version binding mismatch is rejected before Popen", version_binding_mismatch)


def snapshot_swap() -> bool:
    root = TMP / "snapshot-swap"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root)
    swapped = root / "other.snapshot"
    swapped.write_bytes(Path(profile.snapshot_path).read_bytes())
    swapped.chmod(0o500)
    candidate = dataclasses.replace(
        profile,
        snapshot_path=str(swapped),
        snapshot_identity=MODULE.version.FileIdentity.from_stat(swapped.stat()),
    )
    try:
        return rejects(lambda: MODULE._validate_version_evidence(candidate))
    finally:
        cleanup_profile(root, profile)


check("unrelated snapshot path is rejected by version binding", snapshot_swap)


def version_root_mode() -> bool:
    root = TMP / "version-mode"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root)
    path = Path(profile.version_root)
    path.chmod(0o755)
    try:
        return rejects(lambda: MODULE._validate_version_evidence(profile))
    finally:
        path.chmod(0o700)
        cleanup_profile(root, profile)


check("nonprivate version evidence root is rejected", version_root_mode)


def version_extra_file() -> bool:
    root = TMP / "version-extra"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root)
    extra = Path(profile.version_root) / "extra"
    extra.write_bytes(b"")
    extra.chmod(0o600)
    try:
        return rejects(lambda: MODULE._validate_version_evidence(profile))
    finally:
        cleanup_profile(root, profile)


check("extra version evidence file is rejected", version_extra_file)


def nonempty_version_directory() -> bool:
    root = TMP / "version-nonempty-dir"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root)
    marker = Path(profile.version_root) / "cwd" / "unexpected"
    marker.write_bytes(b"x")
    marker.chmod(0o600)
    try:
        return rejects(lambda: MODULE._validate_version_evidence(profile))
    finally:
        cleanup_profile(root, profile)


check("nonempty version evidence directory is rejected", nonempty_version_directory)


def timeout_is_bounded() -> bool:
    root = TMP / "timeout"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root, models_delay=0.25)
    original = MODULE.WALL_SECONDS
    MODULE.WALL_SECONDS = 0.05
    try:
        return (
            rejects(lambda: MODULE.run_attestation(profile, module_source=source))
            and not any(root.glob("agy-models-attestation.*/models.binding.sha256"))
        )
    finally:
        MODULE.WALL_SECONDS = original
        cleanup_profile(root, profile)


check("timeout terminates the synthetic group without a marker", timeout_is_bounded)


def interrupted_during_popen(signum: int) -> bool:
    root = TMP / f"signal-{signum}"
    root.mkdir(mode=0o700)
    profile = MODULE._synthetic_profile(root)

    def signaling(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        os.kill(os.getpid(), signum)
        return process

    try:
        try:
            MODULE.run_attestation(
                profile,
                calls=MODULE.version.RunnerCalls(popen=signaling),
                module_source=source,
                stderr_contract=empty_stderr_contract,
            )
        except SystemExit as exc:
            return (
                exc.code == 128 + signum
                and not any(root.glob("agy-models-attestation.*/models.binding.sha256"))
            )
        return False
    finally:
        cleanup_profile(root, profile)


for lifecycle_signal in MODULE.version.LIFECYCLE_SIGNALS:
    check(
        f"signal {lifecycle_signal} exits exactly and publishes no completion marker",
        lambda lifecycle_signal=lifecycle_signal: interrupted_during_popen(lifecycle_signal),
    )


def dependency_pin_mutation() -> bool:
    return rejects(
        lambda: MODULE._source_bytes(
            ROOT / "scripts" / "agy_inventory.py",
            MODULE.INVENTORY_PARSER_BYTES,
            "0" * 64,
        )
    )


check("dependency digest mutation is rejected", dependency_pin_mutation)
check("offline self-test uses only synthetic evidence", lambda: MODULE.run_offline_self_test()["accepted"] == 1)
check("invalid CLI invocation is rejected", lambda: MODULE.main(["models"]) == 64)
check("version mode is not exposed", lambda: MODULE.main(["--attest-version"]) == 64)

shutil.rmtree(TMP)
print(f"models attestation runner offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
