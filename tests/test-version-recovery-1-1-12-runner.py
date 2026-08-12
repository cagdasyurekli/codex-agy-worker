#!/usr/bin/env python3
"""Offline structural and lifecycle controls for fixed 1.1.12 recovery."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable


COMMAND = "/usr/bin/python3 -I -S -B tests/test-version-recovery-1-1-12-runner.py"


def runtime_ok() -> bool:
    return (
        sys.implementation.name == "cpython"
        and sys.version_info[:2] == (3, 9)
        and sys.flags.isolated == sys.flags.no_site == 1
        and sys.flags.dont_write_bytecode == sys.flags.ignore_environment == 1
    )


if not runtime_ok():
    sys.stderr.write("version recovery 1.1.12 tests: unsupported interpreter or flags; run " + COMMAND + "\n")
    raise SystemExit(2)


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "version_recovery_1_1_12_runner.py"
SPEC = importlib.util.spec_from_file_location("version_recovery_1_1_12_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
TMP = Path(tempfile.mkdtemp(prefix="agyworker-version-recovery-1-1-12-tests.")).resolve()
os.chmod(TMP, 0o700)
passed = 0
failed = 0


def check(name: str, predicate: Callable[[], bool]) -> None:
    global passed, failed
    try:
        ok = bool(predicate())
    except BaseException as exc:
        ok = False
        print("FAIL version recovery 1.1.12: %s (%s: %s)" % (name, type(exc).__name__, exc))
    if ok:
        passed += 1
    else:
        failed += 1
        if not "exc" in locals():
            print("FAIL version recovery 1.1.12: " + name)


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (ValueError, OSError, subprocess.SubprocessError):
        return True
    return False


SOURCE = MODULE_PATH.read_bytes()
CONTRACT = MODULE.validate_source_contract(SOURCE)
check("imports with bytecode disabled", lambda: sys.dont_write_bytecode)
check("exact CPython 3.9 runtime is selected", runtime_ok)
check("runtime contract rejects wrong implementation", lambda: not MODULE._runtime_contract("pypy", 3, 9, 1, 1, 1, 1))
check("runtime contract rejects wrong minor", lambda: not MODULE._runtime_contract("cpython", 3, 10, 1, 1, 1, 1))
check("runtime contract rejects missing isolation", lambda: not MODULE._runtime_contract("cpython", 3, 9, 0, 1, 1, 1))
check("fixed version is 1.1.12", lambda: MODULE.EXPECTED_VERSION == "1.1.12")
check("fixed stdout is exact", lambda: MODULE.EXPECTED_STDOUT == b"1.1.12\n")
check(
    "phase-one authority pins are exact",
    lambda: (
        MODULE.EXPECTED_SOURCE_SHA256 == "c8fd3c0016e101689f923f82da1c068b0e6dce3abcb0089e282742693ad4d344"
        and MODULE.EXPECTED_PRIOR_BINDING_SHA256 == "33825ec9f9c5b92384c504d3c814ac2af034ba3f1b87e7b3d2d5bfc15baf6702"
        and MODULE.EXPECTED_PROFILE_SHA256 == "aa1108f43aa29e0c6de5e67d2369154704d173665316752968a909c56aff263b"
        and MODULE.EXPECTED_PROFILE_BYTES == 990
    ),
)
check("source contract accepts reviewed bytes", lambda: CONTRACT["status"] == "accepted")
check("source digest is exact current bytes", lambda: CONTRACT["sha256"] == hashlib.sha256(SOURCE).hexdigest())
check("one production Popen exists", lambda: SOURCE.count(b"calls.popen(") == 1)
check("logical argv is fixed", lambda: b'argv = [profile.source_path, "--version"]' in SOURCE)
check("Popen executable is the snapshot", lambda: b"executable=profile.snapshot_path" in SOURCE)
check("recovery claim is fixed", lambda: SOURCE.count(b'"claim": "snapshot-version-recovery"') >= 3)
check("no model or effort path exists", lambda: b"--model" not in SOURCE and b"--effort" not in SOURCE and b"models" not in SOURCE)
check("no network client exists", lambda: all(item not in SOURCE for item in (b"socket", b"urllib", b"requests", b"httpx")))
check("no canonical runtime import exists", lambda: b"import version_attestation_runner" not in SOURCE)
check("no dynamic import exists", lambda: b"import importlib" not in SOURCE and b"from importlib" not in SOURCE)
check("only platform-constant getattr calls exist", lambda: SOURCE.count(b"getattr(") == 3)
check("no dynamic exec exists", lambda: b"exec(" not in SOURCE)
def no_production_global_assignment() -> bool:
    import ast
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    return not any(
        isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "MODULE"
            and target.attr in {
                "EXPECTED_SOURCE_SHA256", "EXPECTED_PRIOR_BINDING_SHA256",
                "EXPECTED_PROFILE_SHA256", "EXPECTED_PROFILE_BYTES",
            }
            for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        )
        for node in ast.walk(tree)
    )


check("production tests do not assign fixed source authority", no_production_global_assignment)


def mutated(old: bytes, new: bytes) -> bool:
    return rejects(lambda: MODULE.validate_source_contract(SOURCE.replace(old, new, 1)))


def mutated_last(old: bytes, new: bytes) -> bool:
    position = SOURCE.rfind(old)
    assert position >= 0
    return rejects(
        lambda: MODULE.validate_source_contract(
            SOURCE[:position] + new + SOURCE[position + len(old):]
        )
    )


check("rejects stale 1.1.11 constant", lambda: mutated(b'EXPECTED_VERSION = "1.1.12"', b'EXPECTED_VERSION = "1.1.11"'))
check("rejects stdout drift", lambda: mutated(b'EXPECTED_STDOUT = b"1.1.12\\n"', b'EXPECTED_STDOUT = b"1.1.11\\n"'))
check("rejects phase-one source drift", lambda: mutated(b"c8fd3c0016e101689f923f82da1c068b0e6dce3abcb0089e282742693ad4d344", b"0" * 64))
check("rejects extra Popen", lambda: mutated(b"            process = calls.popen(\n", b"            calls.popen(argv)\n            process = calls.popen(\n"))
check("rejects direct subprocess launch", lambda: mutated(b"            process = calls.popen(\n", b"            subprocess.Popen(argv)\n            process = calls.popen(\n"))
check("rejects snapshot executable removal", lambda: mutated(b"                executable=profile.snapshot_path,\n", b""))
check("rejects argv alias", lambda: mutated(b'argv = [profile.source_path, "--version"]', b'argv = [profile.snapshot_path, "--version"]'))
check("rejects help alias", lambda: mutated(b'argv = [profile.source_path, "--version"]', b'argv = [profile.source_path, "--help"]'))
check("rejects dynamic getattr", lambda: mutated(b"    process_calls = [", b"    getattr(calls, 'popen')\n    process_calls = ["))
check("rejects dynamic exec", lambda: mutated(b"    process_calls = [", b"    exec('pass')\n    process_calls = ["))
check("rejects canonical import", lambda: mutated(b"import dataclasses\n", b"import dataclasses\nimport version_attestation_runner\n"))
check("rejects global reassignment", lambda: mutated(b"    process_calls = [", b"    EXPECTED_VERSION = '1.1.12'\n    process_calls = ["))
check("rejects source-hash prior bypass", lambda: mutated(b"    if profile.source_sha256 != EXPECTED_SOURCE_SHA256:\n", b"    if False:\n"))
check("rejects phase-one validator immediate return", lambda: mutated(b"    \"\"\"Require the complete immutable record emitted by the initial bridge.\"\"\"\n", b"    \"\"\"Require the complete immutable record emitted by the initial bridge.\"\"\"\n    return\n"))
check("rejects prior validator immediate return", lambda: mutated(b"def _validate_prior(profile: AttestationProfile) -> PriorEvidence:\n", b"def _validate_prior(profile: AttestationProfile) -> PriorEvidence:\n    return\n"))
check("rejects phase-one validator call removal", lambda: mutated(b"        _validate_phase_one_binding(profile, descriptor, parsed, files)\n", b""))
check("rejects validation order bypass", lambda: mutated(b"        prior_evidence = _validate_prior(profile)\n", b"        prior_evidence = None\n"))
check("rejects unreconciled-limit source bypass", lambda: mutated(b'"recovery_runner_version_reconciled": False,', b'"recovery_runner_version_reconciled": True,'))
check("rejects expected-version source bypass", lambda: mutated(b'        "expected": EXPECTED_VERSION,\n', b'        "expected": "1.1.11",\n'))
check("rejects final completion exit loss", lambda: mutated_last(b"            os._exit(0)\n", b"            return\n"))
check("rejects process group close loss", lambda: mutated(b"            exit_code = _close_reserved_group(process, calls)\n", b"            exit_code = process.wait()\n"))
check("rejects activation fallback drift", lambda: mutated(b'            else b"version recovery 1.1.12 runner: rejected\\n",\n', b"            else missing_name,\n"))


def private(name: str) -> Path:
    path = TMP / name
    path.mkdir(mode=0o700)
    return path


def phase_one_profile(kind: str, mutate: Callable[[dict[str, object], Path], None] | None = None, source_sha: str | None = None) -> object:
    root = private(kind)
    current = root / "current-agy"
    current.write_bytes(MODULE.FAKE_EXECUTABLE)
    current.chmod(0o755)
    source = root / "agy.source"
    source.write_bytes(MODULE.FAKE_EXECUTABLE)
    source.chmod(0o755)
    prior = root / "agy-version-attestation.initial"
    prior.mkdir(mode=0o700)
    snapshot = prior / "agy.snapshot"
    snapshot.write_bytes(MODULE.FAKE_EXECUTABLE)
    snapshot.chmod(0o500)
    for name in ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"):
        (prior / name).mkdir(mode=0o700)
    current_id = MODULE.FileIdentity.from_stat(current.stat())
    source_id = MODULE.FileIdentity.from_stat(source.stat())
    snapshot_id = MODULE.FileIdentity.from_stat(snapshot.stat())
    def publish(name: str, data: bytes) -> str:
        path = prior / name
        path.write_bytes(data)
        path.chmod(0o600)
        return hashlib.sha256(data).hexdigest()
    source_json = MODULE._canonical_json(source_id.as_dict())
    snapshot_json = MODULE._canonical_json(snapshot_id.as_dict())
    publish("source.pre.json", source_json)
    publish("source.post.json", source_json)
    publish("snapshot.pre.json", snapshot_json)
    publish("snapshot.post.json", snapshot_json)
    stdout_sha = publish("version.stdout", MODULE.EXPECTED_STDOUT)
    stderr_sha = publish("version.stderr", b"")
    argv = [str(source), "--version"]
    logical_sha = hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("ascii")).hexdigest()
    summary = {
        "call_count": 1, "child_exit": 0, "claim": "snapshot-version-only",
        "elapsed_ms": 0, "logical_argv_sha256": logical_sha, "schema_version": 1,
        "status": "accepted", "stderr_bytes": 0, "stdout_bytes": len(MODULE.EXPECTED_STDOUT),
        "timeout": False,
    }
    summary_sha = publish("version.summary.json", MODULE._canonical_json(summary))
    binding = {
        "artifacts": {"version.stderr": stderr_sha, "version.stdout": stdout_sha, "version.summary.json": summary_sha},
        "claim": "snapshot-version-only",
        "copy": {"snapshot_post": snapshot_id.as_dict(), "source_post": source_id.as_dict()},
        "historical_recovery": {
            "binding_sha256": MODULE.HISTORICAL_RECOVERY_BINDING_SHA256, "bytes_used": False,
            "revalidated": False, "source_continuity_claimed": False,
            "source_sha256": MODULE.HISTORICAL_RECOVERY_SOURCE_SHA256,
        },
        "inventory": {"executable_version_bound": False},
        "limitations": {
            "metadata_advance_authorized": False, "network_absence_os_enforced": False,
            "provider_backend_proven": False, "recovery_runner_version_reconciled": False,
        },
        "runner": {"byte_count": MODULE.INITIAL_BOOTSTRAP_RUNNER_BYTES, "sha256": MODULE.INITIAL_BOOTSTRAP_RUNNER_SHA256},
        "schema_version": 1,
        "snapshot": {"post": snapshot_id.as_dict(), "pre": snapshot_id.as_dict(), "sha256": source_sha or MODULE.EXPECTED_SOURCE_SHA256},
        "source": {"current_post": current_id.as_dict(), "current_pre": current_id.as_dict(), "post": source_id.as_dict(), "pre": source_id.as_dict(), "sha256": source_sha or MODULE.EXPECTED_SOURCE_SHA256},
        "version": {
            "exit": 0, "expected": MODULE.EXPECTED_VERSION, "logical_argv": argv,
            "logical_argv_sha256": logical_sha, "observed": MODULE.EXPECTED_VERSION,
            "popen_count": 1, "stderr_limit": MODULE.STREAM_LIMIT,
            "stdout_limit": MODULE.STREAM_LIMIT, "timeout_seconds": MODULE.WALL_SECONDS,
        },
    }
    if mutate is not None:
        mutate(binding, prior)
    data = MODULE._canonical_json(binding)
    publish("version.binding.json", data)
    publish("version.binding.sha256", (hashlib.sha256(data).hexdigest() + "\n").encode("ascii"))
    return MODULE.AttestationProfile(hashlib.sha256(data).hexdigest(), str(prior), snapshot_id, str(snapshot), source_id, str(source), source_sha or MODULE.EXPECTED_SOURCE_SHA256, str(root))


def module_ast_digest(source: bytes) -> str:
    import ast
    tree = ast.parse(source.decode("utf-8", "strict"), filename="<synthetic-recovery>", mode="exec")
    pin = next(
        node for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "MODULE_AST_SHA256"
    )
    pin.value = ast.Constant(value="PINNED-MODULE-AST")
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()


def repin_source(source: bytes) -> bytes:
    marker = b'MODULE_AST_SHA256 = "'
    start = source.index(marker) + len(marker)
    end = source.index(b'"', start)
    digest = module_ast_digest(source)
    return source[:start] + digest.encode("ascii") + source[end:]


def synthetic_validator(profile: object) -> tuple[object, object, bytes, bytes]:
    profile_bytes = MODULE._canonical_json(dataclasses.asdict(profile))
    source = SOURCE.replace(
        MODULE.EXPECTED_PRIOR_BINDING_SHA256.encode("ascii"), profile.prior_binding_sha256.encode("ascii")
    ).replace(
        MODULE.EXPECTED_PROFILE_SHA256.encode("ascii"), hashlib.sha256(profile_bytes).hexdigest().encode("ascii")
    ).replace(
        b"EXPECTED_PROFILE_BYTES = 990", ("EXPECTED_PROFILE_BYTES = %d" % len(profile_bytes)).encode("ascii")
    ).replace(
        MODULE.EXPECTED_SOURCE_SHA256.encode("ascii"), profile.source_sha256.encode("ascii")
    )
    source = repin_source(source)
    clone_root = Path(tempfile.mkdtemp(prefix="recovery-validator-", dir=str(TMP)))
    clone_scripts = clone_root / "scripts"
    clone_scripts.mkdir()
    path = clone_scripts / "version_recovery_1_1_12_runner.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("version_recovery_synthetic_" + path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, module.AttestationProfile.from_bytes(profile_bytes), source, profile_bytes


def rejects_phase_one(name: str, mutate: Callable[[dict[str, object], Path], None]) -> bool:
    profile = phase_one_profile(name, mutate)
    module, copied, _source, _profile_bytes = synthetic_validator(profile)
    return rejects(lambda: module._validate_prior(copied))


def accepts_exact_synthetic_prior() -> bool:
    profile = phase_one_profile("complete")
    module, copied, _source, _profile_bytes = synthetic_validator(profile)
    module._validate_prior(copied)
    return True


check("complete initial bridge prior is accepted", accepts_exact_synthetic_prior)
check("rejects stale 1.1.11 prior", lambda: rejects_phase_one("stale", lambda b, _p: b["version"].update(expected="1.1.11", observed="1.1.11")))
check("rejects reconciled prior flag", lambda: rejects_phase_one("reconciled", lambda b, _p: b["limitations"].update(recovery_runner_version_reconciled=True)))
check("rejects missing initial binding field", lambda: rejects_phase_one("missing-field", lambda b, _p: b.pop("historical_recovery")))
check("rejects retry prior", lambda: rejects_phase_one("retry", lambda b, _p: b["version"].update(popen_count=2)))
check("rejects artifact digest drift", lambda: rejects_phase_one("artifacts", lambda b, _p: b["artifacts"].update({"version.stdout": "0" * 64})))
check("rejects copy identity drift", lambda: rejects_phase_one("copy", lambda b, _p: b["copy"].update(source_post={})))
check("rejects historical binding drift", lambda: rejects_phase_one("historical", lambda b, _p: b["historical_recovery"].update(bytes_used=True)))
check("rejects inventory authority drift", lambda: rejects_phase_one("inventory", lambda b, _p: b["inventory"].update(executable_version_bound=True)))
check("rejects full limitations drift", lambda: rejects_phase_one("limits", lambda b, _p: b["limitations"].update(metadata_advance_authorized=True)))
check("rejects initial runner pin drift", lambda: rejects_phase_one("runner", lambda b, _p: b["runner"].update(byte_count=0)))
check("rejects snapshot identity drift", lambda: rejects_phase_one("snapshot", lambda b, _p: b["snapshot"].update(sha256="0" * 64)))
check("rejects source current identity drift", lambda: rejects_phase_one("source", lambda b, _p: b["source"].update(current_post={})))
check("rejects summary binding drift", lambda: rejects_phase_one("summary", lambda _b, p: (p / "version.summary.json").write_bytes(b"{}\n")))
check("rejects stdout stream drift", lambda: rejects_phase_one("stdout", lambda _b, p: (p / "version.stdout").write_bytes(b"1.1.11\n")))
check("rejects boolean schema masquerade", lambda: rejects_phase_one("bool-schema", lambda b, _p: b.update(schema_version=True)))
check("rejects float integer masquerade", lambda: rejects_phase_one("float-count", lambda b, _p: b["version"].update(popen_count=1.0)))
check("rejects integer false masquerade", lambda: rejects_phase_one("int-false", lambda b, _p: b["limitations"].update(metadata_advance_authorized=0)))


def hardlinked_prior_rejects() -> bool:
    profile = phase_one_profile("hardlinked-prior")
    target = Path(profile.prior_root) / "version.stdout"
    os.link(target, Path(profile.temp_parent) / "outside-link")
    module, copied, _source, _profile_bytes = synthetic_validator(profile)
    return rejects(lambda: module._validate_prior(copied))


check("rejects hardlinked prior artifact", hardlinked_prior_rejects)


def malformed_profile_rejects() -> bool:
    profile = phase_one_profile("malformed-profile")
    _module, _copied, _source, profile_bytes = synthetic_validator(profile)
    return rejects(lambda: MODULE._validate_exact_profile(profile_bytes + b"\n"))


check("rejects non-exact raw profile bytes before execution", malformed_profile_rejects)


def accepts_synthetic_full_lifecycle() -> bool:
    root = private("accepted")
    executable = MODULE.FAKE_EXECUTABLE
    artifact = None
    try:
        profile = phase_one_profile("accepted-prior", source_sha=hashlib.sha256(executable).hexdigest())
        module, copied, synthetic_source, profile_bytes = synthetic_validator(profile)
        result = module.run_attestation(
            copied,
            profile_bytes=profile_bytes,
            module_source=synthetic_source,
        )
        artifact = Path(str(result["artifact_root"]))
        binding = json.loads((artifact / "version.binding.json").read_text(encoding="utf-8"))
        return (
            result["status"] == "accepted"
            and result["claim"] == "snapshot-version-recovery"
            and result["call_count"] == 1
            and result["input_profile_sha256"] == hashlib.sha256(profile_bytes).hexdigest()
            and binding["version"]["expected"] == "1.1.12"
            and binding["version"]["popen_count"] == 1
            and binding["limitations"]["metadata_advance_authorized"] is False
            and binding["limitations"]["provider_backend_proven"] is False
        )
    finally:
        if artifact is not None and artifact.exists():
            shutil.rmtree(artifact)
        shutil.rmtree(root, ignore_errors=True)


check("synthetic exact prior completes one non-authorizing recovery call", accepts_synthetic_full_lifecycle)


def prior_revalidation_detects_change() -> bool:
    profile = phase_one_profile("prior-post-change")
    module, copied, _source, _profile_bytes = synthetic_validator(profile)
    expected = module._validate_prior(copied)
    target = Path(copied.prior_root) / "version.stdout"
    target.write_bytes(b"1.1.11\n")
    return rejects(lambda: module._revalidate_prior(copied, expected))


check("post-child prior revalidation detects mutation", prior_revalidation_detects_change)


def publisher_rolls_back() -> bool:
    root = private("rollback")
    publisher = MODULE.Publisher(root)
    try:
        publisher.publish("owned", b"owned")
        publisher.rollback()
        return not (root / "owned").exists()
    finally:
        publisher.close()


def publisher_preserves_foreign() -> bool:
    root = private("foreign")
    publisher = MODULE.Publisher(root)
    try:
        publisher.publish("owned", b"owned")
        (root / "owned").unlink()
        (root / "owned").write_bytes(b"foreign")
        publisher.rollback()
        return (root / "owned").read_bytes() == b"foreign"
    finally:
        publisher.close()


check("publisher rollback removes exact owned artifact", publisher_rolls_back)
check("publisher rollback preserves foreign replacement", publisher_preserves_foreign)


def publisher_normalizes_hardlink() -> bool:
    root = private("normalized-hardlink")
    publisher = MODULE.Publisher(root)
    try:
        publisher.publish("owned", b"owned")
        return (root / "owned").stat().st_nlink == 1
    finally:
        publisher.rollback()
        publisher.close()


def publisher_rejects_artifact_mutation() -> bool:
    root = private("artifact-mutation")
    publisher = MODULE.Publisher(root)
    try:
        publisher.publish("owned", b"owned")
        (root / "owned").write_bytes(b"other")
        return rejects(lambda: publisher.validate(frozenset({"owned"})))
    finally:
        publisher.rollback()
        publisher.close()


def publisher_rejects_scratch_entry() -> bool:
    root = private("scratch-mutation")
    (root / "home").mkdir(mode=0o700)
    publisher = MODULE.Publisher(root, scratch_names=("home",))
    try:
        (root / "home" / "foreign").write_bytes(b"x")
        return rejects(lambda: publisher.validate(frozenset()))
    finally:
        publisher.close()


check("publisher normalizes final artifact to one link", publisher_normalizes_hardlink)
check("publisher detects persisted artifact mutation", publisher_rejects_artifact_mutation)
check("publisher detects scratch entries", publisher_rejects_scratch_entry)
check("fixed priority selects HUP", lambda: (lambda c: (c.latch(signal.SIGTERM), c.latch(signal.SIGHUP), c.choose() == signal.SIGHUP))(MODULE.SignalController((signal.SIGHUP, signal.SIGINT, signal.SIGTERM))))
check("invalid CLI is rejected", lambda: (lambda r: r.returncode == 64 and r.stdout == b"")(subprocess.run([sys.executable, "-I", "-S", "-B", str(MODULE_PATH), "--wrong"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)))
check("self-test is source-only and non-authorizing", lambda: (lambda r: r.returncode == 0 and b'"call_count":0' in r.stdout and b"synthetic-version-recovery-contract" in r.stdout)(subprocess.run([sys.executable, "-I", "-S", "-B", str(MODULE_PATH), "--self-test"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)))
check("wrong runtime flags reject before stdin", lambda: subprocess.run(["/usr/bin/python3", str(MODULE_PATH), "--recover-version"], input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False).returncode == 2)
check(
    "repeated malformed profiles reject consistently",
    lambda: all(
        subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-B", str(MODULE_PATH), "--recover-version"],
            input=b"{}\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).returncode == 2
        for _attempt in range(3)
    ),
)


shutil.rmtree(TMP)
print("version recovery 1.1.12 runner offline tests: %d passed, %d failed" % (passed, failed))
raise SystemExit(0 if failed == 0 else 1)
