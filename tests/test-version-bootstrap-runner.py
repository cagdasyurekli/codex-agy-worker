#!/usr/bin/env python3
"""Synthetic, offline tests for the repository-only version bootstrap runner."""

from __future__ import annotations

import dataclasses
import ast
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
import threading
import time
from pathlib import Path
from typing import Callable


CANONICAL_TEST_COMMAND = "/usr/bin/python3 -I -S -B tests/test-version-bootstrap-runner.py"
RUNTIME_ERROR = (
    "version bootstrap tests: unsupported interpreter or flags; run "
    + CANONICAL_TEST_COMMAND
    + "\n"
)


def _test_runtime_contract(
    implementation: object,
    major: object,
    minor: object,
    isolated: object,
    no_site: object,
    dont_write_bytecode: object,
    ignore_environment: object,
) -> bool:
    return (
        type(implementation) is str
        and implementation == "cpython"
        and type(major) is int
        and major == 3
        and type(minor) is int
        and minor == 9
        and type(isolated) is int
        and isolated == 1
        and type(no_site) is int
        and no_site == 1
        and type(dont_write_bytecode) is int
        and dont_write_bytecode == 1
        and type(ignore_environment) is int
        and ignore_environment == 1
    )


if not _test_runtime_contract(
    sys.implementation.name,
    sys.version_info.major,
    sys.version_info.minor,
    sys.flags.isolated,
    sys.flags.no_site,
    sys.flags.dont_write_bytecode,
    sys.flags.ignore_environment,
):
    sys.stderr.write(RUNTIME_ERROR)
    raise SystemExit(2)


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "version_bootstrap_runner.py"
SPEC = importlib.util.spec_from_file_location("version_bootstrap_runner_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
TMP = Path(tempfile.mkdtemp(prefix="agyworker-version-bootstrap-tests.")).resolve()
TMP.chmod(0o700)
passed = failed = 0


def check(name: str, predicate: Callable[[], bool]) -> None:
    global passed, failed
    try:
        result = bool(predicate())
    except BaseException as exc:
        result = False
        print(f"FAIL version bootstrap: {name} ({type(exc).__name__}: {exc})")
    if result:
        passed += 1
    else:
        failed += 1
        print(f"FAIL version bootstrap: {name}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (MODULE.BootstrapError, MODULE.version.AttestationError, OSError, subprocess.SubprocessError):
        return True
    return False


def identity(path: Path) -> object:
    return MODULE.version.FileIdentity.from_stat(path.stat())


def private_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def fixture(label: str, payload_bytes: int = 0) -> tuple[Path, object]:
    root = TMP / label
    root.mkdir(mode=0o700)
    account = root / "account"
    account.mkdir(mode=0o700)
    source = account / "agy"
    executable = b"#!/bin/sh\nprintf '1.1.11\\n'\n"
    if payload_bytes:
        executable += b"#" + (b"x" * payload_bytes) + b"\n"
    source.write_bytes(executable); source.chmod(0o755)
    snapshot = root / "retained.snapshot"
    snapshot.write_bytes(executable); snapshot.chmod(0o500)
    retained = root / "retained-recovery"
    retained.mkdir(mode=0o700)
    for name in MODULE.RECOVERY_FILES:
        if name in {"cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"}:
            (retained / name).mkdir(mode=0o700)
        else:
            private_file(retained / name, b"")
    sha = hashlib.sha256(executable).hexdigest()
    logical_argv = [str(source), "--version"]
    logical_sha = MODULE._logical_argv_sha256(logical_argv)
    runner = b"synthetic retained runner\n"
    stdout = b"1.1.11\n"
    stderr = b""
    summary = {
        "call_count": 1,
        "child_exit": 0,
        "claim": "snapshot-version-recovery",
        "elapsed_ms": 1,
        "logical_argv_sha256": logical_sha,
        "schema_version": 1,
        "status": "accepted",
        "stderr_bytes": 0,
        "stdout_bytes": len(stdout),
        "timeout": False,
    }
    summary_bytes = MODULE._canonical_json(summary)
    private_file(retained / "runner.py", runner)
    private_file(retained / "runner.py.sha256", (hashlib.sha256(runner).hexdigest() + "\n").encode("ascii"))
    private_file(retained / "version.stdout", stdout)
    private_file(retained / "version.stderr", stderr)
    private_file(retained / "version.summary.json", summary_bytes)
    source_value = dataclasses.asdict(identity(source))
    snapshot_value = dataclasses.asdict(identity(snapshot))
    private_file(retained / "source.pre.json", MODULE._canonical_json(source_value))
    private_file(retained / "source.post.json", MODULE._canonical_json(source_value))
    private_file(retained / "snapshot.pre.json", MODULE._canonical_json(snapshot_value))
    private_file(retained / "snapshot.post.json", MODULE._canonical_json(snapshot_value))
    binding = {
        "artifacts": {
            "runner.py": hashlib.sha256(runner).hexdigest(),
            "version.stderr": hashlib.sha256(stderr).hexdigest(),
            "version.stdout": hashlib.sha256(stdout).hexdigest(),
            "version.summary.json": hashlib.sha256(summary_bytes).hexdigest(),
        },
        "claim": "snapshot-version-recovery",
        "limitations": {
            "metadata_advance_authorized": False,
            "network_absence_os_enforced": False,
            "prior_inventory_executable_version_bound": False,
            "provider_backend_proven": False,
        },
        "prior": {"binding_sha256": "0" * 64, "root_mutated": False},
        "runner": {"byte_count": len(runner), "sha256": hashlib.sha256(runner).hexdigest()},
        "schema_version": 1,
        "source": {"pre": source_value, "post": source_value, "sha256": sha},
        "snapshot": {"pre": snapshot_value, "post": snapshot_value, "sha256": sha},
        "version": {
            "exit": 0,
            "expected": "1.1.11",
            "logical_argv": logical_argv,
            "logical_argv_sha256": logical_sha,
            "observed": "1.1.11",
            "popen_count": 1,
            "stderr_limit": MODULE.version.STREAM_LIMIT,
            "stdout_limit": MODULE.version.STREAM_LIMIT,
            "timeout_seconds": MODULE.version.WALL_SECONDS,
        },
    }
    exact = MODULE.version._canonical_json(binding)
    digest = hashlib.sha256(exact).hexdigest()
    private_file(retained / "version.binding.json", exact)
    private_file(retained / "version.binding.sha256", (digest + "\n").encode("ascii"))
    MODULE.EXPECTED_SIZE = len(executable)
    MODULE.EXPECTED_SOURCE_SHA256 = sha
    MODULE.EXPECTED_BINDING_SHA256 = digest
    profile = MODULE.BootstrapProfile(
        account_home=str(account), bootstrap_root=str(root / "bootstrap"), retained_binding_sha256=digest,
        retained_snapshot_path=str(snapshot), retained_source_path=str(source), retained_version_root=str(retained),
    )
    return root, profile


def fake_popen(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
    return subprocess.Popen(["/bin/sh", "-c", "printf '1.1.11\\n'"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)


SOURCE = MODULE_PATH.read_bytes()
check("separate repository-only runner imports without bytecode", lambda: sys.dont_write_bytecode)
check("bootstrap surface is the one exact CLI flag", lambda: b'--bootstrap-version' in SOURCE and b'--attest-version' not in SOURCE)
check("bootstrap imports no network client and no shell launch", lambda: not any(item in SOURCE for item in (b"import socket", b"import urllib", b"shell=True")))
check("bootstrap pins the retained binding, version, source hash, and size", lambda: MODULE.EXPECTED_VERSION == "1.1.11" and len(MODULE.EXPECTED_BINDING_SHA256) == 64 and len(MODULE.EXPECTED_SOURCE_SHA256) == 64 and MODULE.EXPECTED_SIZE == 169_718_336)
check(
    "runtime predicates accept only the canonical CPython 3.9 flag ABI",
    lambda: all(
        predicate("cpython", 3, 9, 1, 1, 1, 1)
        for predicate in (_test_runtime_contract, MODULE._runtime_contract)
    ),
)
check(
    "runtime predicates reject missing ignore-environment evidence",
    lambda: all(
        not predicate("cpython", 3, 9, 1, 1, 1, None)
        for predicate in (_test_runtime_contract, MODULE._runtime_contract)
    ),
)
check(
    "runtime predicates reject boolean ignore-environment evidence",
    lambda: all(
        not predicate("cpython", 3, 9, 1, 1, 1, True)
        for predicate in (_test_runtime_contract, MODULE._runtime_contract)
    ),
)


def runtime_drift_rejects() -> bool:
    cases = (
        ("pypy", 3, 9, 1, 1, 1, 1),
        ("cpython", 2, 9, 1, 1, 1, 1),
        ("cpython", 3, 10, 1, 1, 1, 1),
        ("cpython", 3, 9, 0, 1, 1, 1),
        ("cpython", 3, 9, 1, 0, 1, 1),
        ("cpython", 3, 9, 1, 1, 0, 1),
        ("cpython", 3, 9, 1, 1, 1, 0),
    )
    return all(
        not predicate(*case)
        for predicate in (_test_runtime_contract, MODULE._runtime_contract)
        for case in cases
    )


check("runtime predicates reject implementation version and flag drift", runtime_drift_rejects)
check("current canonical interpreter satisfies production runtime preflight", MODULE._runtime_supported)
check("canonical bootstrap source contract is accepted", lambda: MODULE.validate_source_contract(SOURCE)["status"] == "accepted")


def wrong_flag_test_preflight() -> bool:
    result = subprocess.run(
        ["/usr/bin/python3", str(Path(__file__).resolve())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 2 and result.stdout == b"" and result.stderr == RUNTIME_ERROR.encode("ascii")


check("test harness wrong flags fail once with canonical command", wrong_flag_test_preflight)


def mutate(old: bytes, new: bytes) -> bytes:
    position = SOURCE.rfind(old)
    if position < 0:
        raise AssertionError(old)
    return SOURCE[:position] + new + SOURCE[position + len(old):]


def repin_module(data: bytes) -> bytes:
    tree = ast.parse(data.decode("utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "MODULE_AST_SHA256"
    )
    assignment.value = ast.Constant(value="PINNED-MODULE-AST")
    digest = hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()
    old = b'MODULE_AST_SHA256 = "' + MODULE.MODULE_AST_SHA256.encode("ascii") + b'"'
    new = b'MODULE_AST_SHA256 = "' + digest.encode("ascii") + b'"'
    if data.count(old) != 1:
        raise AssertionError("module pin occurrence changed")
    return data.replace(old, new, 1)


for label, old, new in (
    ("extra child", b"            process = calls.popen(\n", b"            calls.popen(argv)\n            process = calls.popen(\n"),
    ("logical argv", b'"--version"', b'"--help"'),
    ("snapshot executable", b"executable=recovery_profile.snapshot_path", b"executable=recovery_profile.source_path"),
    ("new session", b"start_new_session=True", b"start_new_session=False"),
    ("stream capture", b"stdout=subprocess.PIPE", b"stdout=None"),
    ("deadline", b"version.WALL_SECONDS", b"999.0"),
    ("group close", b"_finish_process_group(process, calls)", b"process.wait()"),
    ("marker ordering", b'"version.binding.sha256", (binding_sha', b'"wrong.marker", (binding_sha'),
    ("exclusive staging", b"os.O_EXCL", b"0"),
    ("nofollow staging", b"os.O_EXCL | version.CLOEXEC | version.NOFOLLOW", b"os.O_EXCL | version.CLOEXEC"),
):
    check("source contract rejects " + label + " removal", lambda old=old, new=new: rejects(lambda: MODULE.validate_source_contract(mutate(old, new))))


def inserted(before: bytes, payload: bytes) -> bytes:
    position = SOURCE.find(before)
    if position < 0:
        raise AssertionError(before)
    return SOURCE[:position] + payload + SOURCE[position:]


for label, changed in (
    ("repinned os import alias", SOURCE.replace(b"import os\n", b"import os as operating_system\n", 1)),
    ("repinned importlib authority", inserted(b"import json\n", b"import importlib\n")),
    ("repinned assigned Popen alias", inserted(b"            process = calls.popen(\n", b"            launch_alias = calls.popen\n")),
    ("repinned getattr lookup", inserted(b"        started = time.monotonic()\n", b"        getattr(calls, 'popen')\n")),
    ("repinned globals lookup", inserted(b"        started = time.monotonic()\n", b"        globals()\n")),
    ("repinned subprocess runner", inserted(b"        started = time.monotonic()\n", b"        subprocess.run(argv)\n")),
    ("repinned recursive launch owner", inserted(b"        started = time.monotonic()\n", b"        run_bootstrap(profile)\n")),
    ("repinned argv mutation", inserted(b"        started = time.monotonic()\n", b"        argv.append('--help')\n")),
    ("repinned environment mutation", inserted(b"        started = time.monotonic()\n", b"        environment['PATH'] = '/tmp'\n")),
    ("repinned post-close wait", inserted(b"        if exit_code != 0", b"        process.wait()\n")),
    (
        "repinned durability-close removal",
        SOURCE.replace(b"        ledger.close()\n", b"", 1),
    ),
    (
        "repinned early completion linearization",
        SOURCE.replace(
            b"        ledger.close()\n",
            b"        completion_linearized = True\n        ledger.close()\n",
            1,
        ),
    ),
    (
        "repinned raising lifecycle handler",
        SOURCE.replace(
            b"    def latch(self, signum: int, _frame: object = None) -> None:\n        if signum in version.LIFECYCLE_SIGNALS:\n",
            b"    def latch(self, signum: int, _frame: object = None) -> None:\n        raise BootstrapInterrupted(signum)\n        if signum in version.LIFECYCLE_SIGNALS:\n",
            1,
        ),
    ),
    (
        "repinned production return",
        inserted(b"            os._exit(0)\n", b"            return result\n"),
    ),
    (
        "repinned production SystemExit",
        SOURCE.replace(b"            os._exit(0)\n", b"            raise SystemExit(0)\n", 1),
    ),
    (
        "repinned production handler restore",
        inserted(
            b"            os._exit(0)\n",
            b"            signal.signal(signal.SIGTERM, lifecycle.old_handlers[signal.SIGTERM])\n",
        ),
    ),
    (
        "repinned production unblock",
        inserted(
            b"            os._exit(0)\n",
            b"            signal.pthread_sigmask(signal.SIG_SETMASK, lifecycle.entry_mask)\n",
        ),
    ),
    (
        "repinned output after completion",
        SOURCE.replace(
            b"        if process_owned:\n            sys.stdout.buffer.flush()\n",
            b"        completion_linearized = True\n        if process_owned:\n            sys.stdout.buffer.flush()\n",
            1,
        ),
    ),
    (
        "repinned early provisional-publication mask",
        SOURCE.replace(
            b"        controller.poll()\n        prior_publisher.publish(\n            \"version.binding.sha256\"",
            b"        signal.pthread_sigmask(signal.SIG_BLOCK, version.LIFECYCLE_SIGNALS)\n        controller.poll()\n        prior_publisher.publish(\n            \"version.binding.sha256\"",
            1,
        ),
    ),
    (
        "repinned large-hash checkpoint removal",
        SOURCE.replace(
            b"        block = os.read(descriptor, min(remaining, 1024 * 1024))\n        controller.poll()\n",
            b"        block = os.read(descriptor, min(remaining, 1024 * 1024))\n",
            1,
        ),
    ),
    (
        "repinned transient hard-link checkpoint",
        SOURCE.replace(
            b"            ledger.register_linked_file(temporary_path, final_path, staged)\n            if (\n",
            b"            ledger.register_linked_file(temporary_path, final_path, staged)\n            controller.poll()\n            if (\n",
            1,
        ),
    ),
    (
        "repinned runtime implementation weakening",
        SOURCE.replace(
            b'        and implementation == "cpython"\n',
            b'        and implementation in {"cpython", "pypy"}\n',
            1,
        ),
    ),
    (
        "repinned runtime minor weakening",
        SOURCE.replace(
            b"        and minor == RUNTIME_MINOR\n",
            b"        and minor >= RUNTIME_MINOR\n",
            1,
        ),
    ),
    (
        "repinned runtime flag weakening",
        SOURCE.replace(
            b"        and isolated == 1\n",
            b"        and isolated >= 0\n",
            1,
        ),
    ),
    (
        "repinned runtime ignore-environment None weakening",
        SOURCE.replace(
            b"        and type(ignore_environment) is int\n        and ignore_environment == 1\n",
            b"        and (ignore_environment is None or (type(ignore_environment) is int and ignore_environment == 1))\n",
            1,
        ),
    ),
    (
        "repinned runtime ABI constant drift",
        SOURCE.replace(b"RUNTIME_MINOR = 9\n", b"RUNTIME_MINOR = 10\n", 1),
    ),
    (
        "repinned production runtime preflight removal",
        SOURCE.replace(
            b"def main(argv: Sequence[str]) -> NoReturn:\n    if not _runtime_supported():\n        _atomic_exit(2, sys.stderr.buffer.fileno(), b\"version bootstrap: rejected\\n\")\n",
            b"def main(argv: Sequence[str]) -> NoReturn:\n",
            1,
        ),
    ),
):
    check(
        "source contract rejects validator-only " + label,
        lambda changed=changed: rejects(lambda: MODULE.validate_source_contract(repin_module(changed))),
    )


def positive() -> bool:
    root, profile = fixture("positive")
    try:
        result = MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        prior = Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap"
        prepared = json.loads((Path(profile.bootstrap_root) / "version.recovery.profile.json").read_text("ascii"))
        observed = (result["status"], result["claim"], result["call_count"], set(item.name for item in prior.iterdir()), prepared["prior_root"], (prior / "version.binding.sha256").read_text("ascii").strip(), result["binding_sha256"])
        expected = ("accepted", "snapshot-version-bootstrap", 1, MODULE.version.PRIOR_FILES, str(prior), result["binding_sha256"], result["binding_sha256"])
        return observed == expected
    finally:
        shutil.rmtree(root)


check("synthetic retained recovery evidence yields unchanged recovery input", positive)


def path_reject(field: str, value: str) -> bool:
    root, profile = fixture("path-" + field)
    try:
        return rejects(lambda: MODULE.BootstrapProfile.from_bytes(MODULE.version._canonical_json({**dataclasses.asdict(profile), field: value})))
    finally: shutil.rmtree(root)


check("strict profile rejects an extra key", lambda: rejects(lambda: MODULE.BootstrapProfile.from_bytes(MODULE.version._canonical_json({"account_home":"/tmp","bootstrap_root":"/tmp/new","retained_binding_sha256":"0" * 64,"retained_snapshot_path":"/tmp/a","retained_source_path":"/tmp/b","retained_version_root":"/tmp/c","extra":True}))))
check("strict profile rejects relative bootstrap roots", lambda: path_reject("bootstrap_root", "relative/root"))
for label, action in (
    ("duplicate JSON", lambda: MODULE.BootstrapProfile.from_bytes(b'{"account_home":"/tmp","account_home":"/tmp"}')),
    ("oversized JSON", lambda: MODULE.BootstrapProfile.from_bytes(b"x" * (MODULE.PROFILE_LIMIT + 1))),
    ("noncanonical JSON", lambda: MODULE.BootstrapProfile.from_bytes(b"{}\n")),
    ("missing JSON key", lambda: MODULE.BootstrapProfile.from_bytes(MODULE.version._canonical_json({"account_home":"/tmp"}))),
):
    check("strict profile rejects " + label, lambda action=action: rejects(action))


def retained_tamper() -> bool:
    root, profile = fixture("tamper")
    try:
        private_file(Path(profile.retained_version_root) / "version.binding.sha256", b"0" * 64 + b"\n")
        return rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))) and not Path(profile.bootstrap_root).exists()
    finally: shutil.rmtree(root)


check("retained binding marker drift rejects before bootstrap creation", retained_tamper)


def retained_claim_drift(label: str) -> bool:
    root, profile = fixture("retained-claim-" + label)
    retained = Path(profile.retained_version_root)
    old_expected = MODULE.EXPECTED_BINDING_SHA256
    try:
        binding = json.loads((retained / "version.binding.json").read_text("ascii"))
        if label == "claim": binding["claim"] = "snapshot-version-bootstrap"
        elif label == "logical-argv": binding["version"]["logical_argv"] = [profile.retained_source_path, "models"]
        elif label == "call-count": binding["version"]["popen_count"] = 2
        elif label == "source-post": binding["source"]["post"]["ino"] += 1
        elif label == "snapshot-post": binding["snapshot"]["post"]["ino"] += 1
        elif label == "source-hash": binding["source"]["sha256"] = "0" * 64
        elif label == "summary-call-count":
            summary = json.loads((retained / "version.summary.json").read_text("ascii"))
            summary["call_count"] = 2
            summary_bytes = MODULE._canonical_json(summary)
            private_file(retained / "version.summary.json", summary_bytes)
            binding["artifacts"]["version.summary.json"] = hashlib.sha256(summary_bytes).hexdigest()
        else: raise AssertionError(label)
        exact = MODULE._canonical_json(binding)
        digest = hashlib.sha256(exact).hexdigest()
        private_file(retained / "version.binding.json", exact)
        private_file(retained / "version.binding.sha256", (digest + "\n").encode("ascii"))
        MODULE.EXPECTED_BINDING_SHA256 = digest
        changed = dataclasses.replace(profile, retained_binding_sha256=digest)
        return rejects(lambda: MODULE.run_bootstrap(changed, calls=MODULE.version.RunnerCalls(popen=fake_popen))) and not Path(changed.bootstrap_root).exists()
    finally:
        MODULE.EXPECTED_BINDING_SHA256 = old_expected
        shutil.rmtree(root)


for label in ("claim", "logical-argv", "call-count", "source-post", "snapshot-post", "source-hash", "summary-call-count"):
    check("strict retained evidence rejects " + label + " drift after digest repin", lambda label=label: retained_claim_drift(label))


def retained_drift(label: str) -> bool:
    root, profile = fixture("drift-" + label)
    source = Path(profile.retained_source_path)
    snapshot = Path(profile.retained_snapshot_path)
    retained = Path(profile.retained_version_root)
    try:
        if label == "source-mode": source.chmod(0o700)
        elif label == "snapshot-mode": snapshot.chmod(0o700)
        elif label == "source-hash": source.write_bytes(b"changed\n")
        elif label == "source-nlink": os.link(source, root / "source.link")
        elif label == "snapshot-nlink": os.link(snapshot, root / "snapshot.link")
        elif label == "root-mode": retained.chmod(0o755)
        elif label == "root-extra": private_file(retained / "unexpected", b"x")
        elif label == "source-outside-home":
            external = root / "external-source"; external.write_bytes(source.read_bytes()); external.chmod(0o755)
            profile = dataclasses.replace(profile, retained_source_path=str(external))
        elif label == "snapshot-in-home":
            inside = Path(profile.account_home) / "inside.snapshot"; inside.write_bytes(snapshot.read_bytes()); inside.chmod(0o500)
            profile = dataclasses.replace(profile, retained_snapshot_path=str(inside))
        return rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))) and not Path(profile.bootstrap_root).exists()
    finally:
        if retained.exists(): retained.chmod(0o700)
        shutil.rmtree(root)


for label in ("source-mode", "snapshot-mode", "source-hash", "source-nlink", "snapshot-nlink", "root-mode", "root-extra", "source-outside-home", "snapshot-in-home"):
    check("retained authority rejects " + label, lambda label=label: retained_drift(label))


def overlap_rejects() -> bool:
    root, profile = fixture("overlap")
    try:
        bad = dataclasses.replace(profile, bootstrap_root=str(Path(profile.account_home) / "bootstrap"))
        return rejects(lambda: MODULE.run_bootstrap(bad, calls=MODULE.version.RunnerCalls(popen=fake_popen)))
    finally: shutil.rmtree(root)


check("bootstrap root cannot overlap account HOME", overlap_rejects)


def existing_root_rejects() -> bool:
    root, profile = fixture("existing")
    try:
        Path(profile.bootstrap_root).mkdir(mode=0o700)
        return rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen)))
    finally: shutil.rmtree(root)


check("bootstrap root must be a new path", existing_root_rejects)


def existing_root_preserves_foreign_contents() -> bool:
    root, profile = fixture("existing-foreign")
    try:
        existing = Path(profile.bootstrap_root); existing.mkdir(mode=0o700)
        foreign = existing / "foreign"; foreign.write_bytes(b"keep")
        return rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))) and foreign.read_bytes() == b"keep"
    finally: shutil.rmtree(root)


check("existing bootstrap root is rejected without deleting foreign contents", existing_root_preserves_foreign_contents)


def root_creation_replacement_survives() -> bool:
    parent = TMP / "root-creation-replacement"
    parent.mkdir(mode=0o700)
    replacement = parent / "bootstrap"
    moved = parent / "owned-root-moved"
    observed: list[object] = []
    original_fsync = MODULE.os.fsync
    def racing_fsync(descriptor: int) -> None:
        if not observed:
            replacement.rename(moved)
            replacement.mkdir(mode=0o700)
            observed.append(MODULE.OwnedIdentity.from_stat(replacement.stat()))
            raise OSError("synthetic root publication failure")
        original_fsync(descriptor)
    MODULE.os.fsync = racing_fsync
    try:
        rejected = rejects(lambda: MODULE.OwnershipLedger.create(str(parent), "bootstrap"))
    finally:
        MODULE.os.fsync = original_fsync
    try:
        return (
            rejected
            and len(observed) == 1
            and replacement.exists()
            and MODULE.OwnedIdentity.from_stat(replacement.stat()) == observed[0]
            and moved.exists()
        )
    finally:
        shutil.rmtree(parent)


check("root creation rollback preserves a same-UID replacement identity", root_creation_replacement_survives)


def post_link_replacement(target: str) -> bool:
    root, profile = fixture("post-link-" + target.replace(".", "-"))
    original_link = MODULE.os.link
    replaced: list[Path] = []
    modes = {"agy.source": 0o755, "agy.snapshot": 0o500}
    def racing_link(*args: object, **kwargs: object) -> None:
        original_link(*args, **kwargs)
        destination = str(args[1])
        if destination != target or replaced:
            return
        parent_fd = int(kwargs["dst_dir_fd"])
        MODULE.os.unlink(destination, dir_fd=parent_fd)
        descriptor = MODULE.os.open(
            destination,
            MODULE.os.O_WRONLY | MODULE.os.O_CREAT | MODULE.os.O_EXCL | MODULE.version.CLOEXEC | MODULE.version.NOFOLLOW,
            modes.get(target, 0o600),
            dir_fd=parent_fd,
        )
        try:
            MODULE.os.fchmod(descriptor, modes.get(target, 0o600))
            MODULE.os.write(descriptor, b"foreign-same-uid\n")
            MODULE.os.fsync(descriptor)
        finally:
            MODULE.os.close(descriptor)
        if target in {"agy.source", "version.recovery.profile.json"}:
            replaced.append(Path(profile.bootstrap_root) / target)
        else:
            replaced.append(Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / target)
    MODULE.os.link = racing_link
    try:
        rejected = rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen)))
    finally:
        MODULE.os.link = original_link
    try:
        marker = Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / "version.binding.sha256"
        valid_marker = marker.exists() and marker.read_bytes() != b"foreign-same-uid\n" and len(marker.read_bytes()) == 65
        temporaries = list(Path(profile.bootstrap_root).rglob(".*.tmp")) if Path(profile.bootstrap_root).exists() else []
        return (
            rejected
            and len(replaced) == 1
            and replaced[0].read_bytes() == b"foreign-same-uid\n"
            and not valid_marker
            and not temporaries
        )
    finally:
        shutil.rmtree(root)


for target in ("agy.source", "agy.snapshot", "version.binding.json", "version.recovery.profile.json", "version.binding.sha256"):
    check("post-link foreign same-UID replacement survives exact rollback for " + target, lambda target=target: post_link_replacement(target))


def same_inode_content_drift_rejects() -> bool:
    root, profile = fixture("same-inode-content-drift")
    changed = [False]
    def mutating_fsync(descriptor: int) -> None:
        os.fsync(descriptor)
        recovery = Path(profile.bootstrap_root) / "version.recovery.profile.json"
        marker = Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / "version.binding.sha256"
        if not changed[0] and recovery.exists() and marker.exists():
            with recovery.open("ab", buffering=0) as stream:
                stream.write(b"changed")
                os.fsync(stream.fileno())
            changed[0] = True
    try:
        rejected = rejects(
            lambda: MODULE.run_bootstrap(
                profile,
                calls=MODULE.version.RunnerCalls(popen=fake_popen, fsync=mutating_fsync),
            )
        )
        return rejected and changed == [True] and not Path(profile.bootstrap_root).exists()
    finally:
        shutil.rmtree(root)


check("same-inode post-publication content drift rejects before acceptance", same_inode_content_drift_rejects)


def moved_root_is_not_chased() -> bool:
    root, profile = fixture("moved-root")
    moved = root / "owned-root-moved"
    def moving(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
        Path(profile.bootstrap_root).rename(moved)
        foreign = Path(profile.bootstrap_root)
        foreign.mkdir(mode=0o700)
        (foreign / "foreign").write_bytes(b"keep")
        return fake_popen()
    try:
        rejected = rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=moving)))
        return rejected and (Path(profile.bootstrap_root) / "foreign").read_bytes() == b"keep" and (moved / "agy.source").exists()
    finally:
        shutil.rmtree(root)


check("root pathname drift leaves both private residuals without inode chasing", moved_root_is_not_chased)


def symlinked_retained_path_rejects() -> bool:
    root, profile = fixture("symlinked-retained")
    try:
        link = root / "source-link"; link.symlink_to(profile.retained_source_path)
        encoded = MODULE.version._canonical_json({**dataclasses.asdict(profile), "retained_source_path": str(link)})
        return rejects(lambda: MODULE.BootstrapProfile.from_bytes(encoded))
    finally: shutil.rmtree(root)


check("symlinked retained source aliases are rejected", symlinked_retained_path_rejects)


def bad_result_rejects() -> bool:
    root, profile = fixture("bad-result")
    def bad(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
        return subprocess.Popen(["/bin/sh", "-c", "printf bad"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rejected = rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=bad)))
        if Path(profile.bootstrap_root).exists():
            print("bootstrap residual", [str(item.relative_to(profile.bootstrap_root)) for item in Path(profile.bootstrap_root).rglob("*")])
        return rejected and not Path(profile.bootstrap_root).exists()
    finally: shutil.rmtree(root)


check("wrong version output rolls back owned bootstrap root", bad_result_rejects)


def result_rejects(label: str, command: str) -> bool:
    root, profile = fixture("result-" + label)
    def runner(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
        return subprocess.Popen(["/bin/sh", "-c", command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        return rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=runner))) and not Path(profile.bootstrap_root).exists()
    finally: shutil.rmtree(root)


check("nonzero child rolls back", lambda: result_rejects("nonzero", "printf '1.1.11\\n'; exit 7"))
check("stderr child output rolls back", lambda: result_rejects("stderr", "printf '1.1.11\\n'; printf x >&2"))
check("stdout overflow rolls back", lambda: result_rejects("overflow", "yes x | head -c 129"))


def scratch_drift(label: str) -> bool:
    root, profile = fixture("scratch-" + label)
    residuals: list[Path] = []
    def drifting(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        environment = kwargs["env"]
        if label == "file":
            residual = Path(environment["HOME"]) / "child.file"
            residual.write_bytes(b"keep"); residuals.append(residual)
        elif label == "symlink":
            residual = Path(environment["TMPDIR"]) / "child.link"
            residual.symlink_to("outside"); residuals.append(residual)
        elif label == "replacement":
            original = Path(environment["XDG_CACHE_HOME"])
            held = original.with_name("xdg-cache.owned")
            original.rename(held)
            original.mkdir(mode=0o700)
            (original / "foreign").write_bytes(b"keep")
            residuals.append(original / "foreign")
        elif label == "mode":
            directory = Path(environment["XDG_STATE_HOME"])
            directory.chmod(0o755)
            residuals.append(directory)
        else: raise AssertionError(label)
        return fake_popen(*args, **kwargs)
    try:
        rejected = rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=drifting)))
        marker = Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / "version.binding.sha256"
        residual = residuals[0]
        if label == "mode":
            survived = residual.exists() and stat.S_IMODE(residual.stat().st_mode) == 0o755
        elif label == "symlink":
            survived = residual.is_symlink() and os.readlink(residual) == "outside"
        else:
            survived = residual.read_bytes() == b"keep"
        return rejected and survived and not marker.exists()
    finally:
        state = Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / "xdg-state"
        if state.exists() and not state.is_symlink(): state.chmod(0o700)
        shutil.rmtree(root)


for label in ("file", "symlink", "replacement", "mode"):
    check("post-close scratch " + label + " drift rejects with bounded residual", lambda label=label: scratch_drift(label))


def timeout_rolls_back() -> bool:
    root, profile = fixture("timeout")
    old = MODULE.version.WALL_SECONDS
    MODULE.version.WALL_SECONDS = 0.05
    try:
        return result_rejects("timeout-child", "sleep 1")
    finally:
        MODULE.version.WALL_SECONDS = old
        if root.exists(): shutil.rmtree(root)


check("deadline timeout closes the group and rolls back", timeout_rolls_back)
check("bootstrap source never reaps the child directly after group closure", lambda: b"process.wait()" not in SOURCE[SOURCE.find(b"def run_bootstrap"):])


def launch_shape() -> bool:
    root, profile = fixture("launch-shape")
    seen: list[tuple[object, dict[str, object]]] = []
    def tracked(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        seen.append((args, kwargs)); return fake_popen()
    try:
        result = MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=tracked))
        kwargs = seen[0][1]
        return result["call_count"] == 1 and len(seen) == 1 and seen[0][0][0] == [str(Path(profile.bootstrap_root) / "agy.source"), "--version"] and kwargs["executable"] == str(Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / "agy.snapshot") and kwargs["stdin"] is subprocess.DEVNULL and kwargs["stdout"] is subprocess.PIPE and kwargs["stderr"] is subprocess.PIPE and kwargs["start_new_session"] is True and kwargs["env"]["PATH"] == "/usr/bin:/bin" and kwargs["env"]["LANG"] == "C" and kwargs["env"]["HOME"].endswith("/home")
    finally: shutil.rmtree(root)


check("one child has exact bounded closed launch shape", launch_shape)


def copy_properties() -> bool:
    root, profile = fixture("copy-properties")
    try:
        result = MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        source = Path(profile.bootstrap_root) / "agy.source"
        snapshot = Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / "agy.snapshot"
        return source.read_bytes() == snapshot.read_bytes() == Path(profile.retained_snapshot_path).read_bytes() and stat.S_IMODE(source.stat().st_mode) == 0o755 and stat.S_IMODE(snapshot.stat().st_mode) == 0o500 and source.stat().st_nlink == snapshot.stat().st_nlink == 1 and result["status"] == "accepted"
    finally: shutil.rmtree(root)


check("two descriptor copies publish exact independent mode-locked files", copy_properties)


def recovery_validator_integration() -> bool:
    root, profile = fixture("recovery-validator")
    try:
        MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        raw = (Path(profile.bootstrap_root) / "version.recovery.profile.json").read_bytes()
        generated = MODULE.version.AttestationProfile.from_bytes(raw)
        MODULE.version._validate_prior(generated)
        return generated.prior_root.endswith("agy-version-attestation.bootstrap") and generated.source_path.endswith("agy.source")
    finally: shutil.rmtree(root)


check("generated profile is accepted by the unchanged recovery validator", recovery_validator_integration)


def capture_rejects_bootstrap_input() -> bool:
    capture_path = ROOT / "scripts" / "models_capture_runner.py"
    capture_spec = importlib.util.spec_from_file_location("models_capture_rejects_bootstrap", capture_path)
    assert capture_spec is not None and capture_spec.loader is not None
    capture = importlib.util.module_from_spec(capture_spec)
    sys.modules[capture_spec.name] = capture
    capture_spec.loader.exec_module(capture)
    root, profile = fixture("capture-rejects-bootstrap")
    try:
        result = MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        generated = MODULE.version.AttestationProfile.from_bytes(
            (Path(profile.bootstrap_root) / "version.recovery.profile.json").read_bytes()
        )
        account_identity = capture.DirectoryIdentity.from_stat(Path(profile.account_home).stat())
        candidate = capture.CaptureProfile(
            account_home=profile.account_home,
            account_home_identity=account_identity,
            snapshot_identity=generated.snapshot_identity,
            snapshot_path=generated.snapshot_path,
            source_identity=generated.source_identity,
            source_path=generated.source_path,
            source_sha256=generated.source_sha256,
            temp_parent=generated.temp_parent,
            version_binding_sha256=result["binding_sha256"],
            version_root=generated.prior_root,
        )
        try:
            capture.models._validate_version_evidence(candidate.models_profile)
        except capture.models.ModelsAttestationError:
            binding = json.loads((Path(generated.prior_root) / "version.binding.json").read_text("ascii"))
            return result["claim"] == "snapshot-version-bootstrap" and binding["claim"] == "snapshot-version-only"
        return False
    finally:
        shutil.rmtree(root)


check("bootstrap result and nested version-only claim are never direct capture acceptance", capture_rejects_bootstrap_input)


def fsync_failure_rolls_back() -> bool:
    root, profile = fixture("fsync-failure")
    def fail(_descriptor: int) -> None: raise OSError("synthetic fsync")
    try:
        return rejects(lambda: MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen, fsync=fail))) and not Path(profile.bootstrap_root).exists()
    finally: shutil.rmtree(root)


check("publication fsync failure rolls back exact owned root", fsync_failure_rolls_back)


def outer_finally_entry_signal() -> bool:
    root, profile = fixture("signal-before-finally-block")
    original_dependency = MODULE._validate_dependency
    original_mask = MODULE.signal.pthread_sigmask
    armed = [False]
    sent = [False]
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    original_handlers = {
        item: signal.getsignal(item) for item in MODULE.version.LIFECYCLE_SIGNALS
    }
    def failing_dependency() -> bytes:
        armed[0] = True
        raise MODULE.BootstrapError("synthetic work failure")
    def signaling_mask(how: int, mask: object) -> object:
        if how == signal.SIG_BLOCK and armed[0] and not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGTERM)
        return original_mask(how, mask)
    MODULE._validate_dependency = failing_dependency
    MODULE.signal.pthread_sigmask = signaling_mask
    try:
        try:
            MODULE.run_bootstrap(
                profile, calls=MODULE.version.RunnerCalls(popen=fake_popen)
            )
        except SystemExit as exc:
            return (
                exc.code == 143
                and sent == [True]
                and signal.pthread_sigmask(signal.SIG_BLOCK, ()) == entry_mask
                and all(
                    signal.getsignal(item) == original_handlers[item]
                    for item in MODULE.version.LIFECYCLE_SIGNALS
                )
                and not Path(profile.bootstrap_root).exists()
            )
        return False
    finally:
        MODULE._validate_dependency = original_dependency
        MODULE.signal.pthread_sigmask = original_mask
        signal.pthread_sigmask(signal.SIG_BLOCK, MODULE.version.LIFECYCLE_SIGNALS)
        for item, handler in original_handlers.items(): signal.signal(item, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        shutil.rmtree(root)


check("latch-only handler survives signal immediately before outer-finally block", outer_finally_entry_signal)


def mid_copy_signal() -> bool:
    root, profile = fixture("signal-mid-copy")
    original_read = MODULE.os.read
    source_identity = profile.retained_source_path and Path(profile.retained_source_path).stat()
    sent = [False]
    def signaling_read(descriptor: int, size: int) -> bytes:
        if (
            not sent[0]
            and Path(profile.bootstrap_root).exists()
            and os.fstat(descriptor).st_ino == source_identity.st_ino
            and os.fstat(descriptor).st_dev == source_identity.st_dev
        ):
            sent[0] = True
            os.kill(os.getpid(), signal.SIGHUP)
        return original_read(descriptor, size)
    MODULE.os.read = signaling_read
    try:
        try:
            MODULE.run_bootstrap(
                profile, calls=MODULE.version.RunnerCalls(popen=fake_popen)
            )
        except SystemExit as exc:
            return exc.code == 129 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        MODULE.os.read = original_read
        shutil.rmtree(root)


check("mid-copy signal is polled and rolls back exact owned bytes", mid_copy_signal)


def mid_fsync_signal() -> bool:
    root, profile = fixture("signal-mid-fsync")
    sent = [False]
    def signaling_fsync(descriptor: int) -> None:
        MODULE.os.fsync(descriptor)
        if not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGHUP)
    try:
        try:
            MODULE.run_bootstrap(
                profile,
                calls=MODULE.version.RunnerCalls(
                    popen=fake_popen, fsync=signaling_fsync
                ),
            )
        except SystemExit as exc:
            return exc.code == 129 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        shutil.rmtree(root)


check("mid-fsync signal is polled after the registered temporary", mid_fsync_signal)


def blocking_capture_signal() -> bool:
    root, profile = fixture("signal-blocking-capture")
    original_selector = MODULE.selectors.DefaultSelector
    sent = [False]
    class SignalingSelector:
        def __init__(self) -> None:
            self.delegate = original_selector()
        def __enter__(self) -> object:
            self.delegate.__enter__()
            return self
        def __exit__(self, *args: object) -> object:
            return self.delegate.__exit__(*args)
        def register(self, *args: object, **kwargs: object) -> object:
            return self.delegate.register(*args, **kwargs)
        def unregister(self, *args: object, **kwargs: object) -> object:
            return self.delegate.unregister(*args, **kwargs)
        def get_map(self) -> object:
            return self.delegate.get_map()
        def select(self, timeout: float | None = None) -> object:
            if not sent[0]:
                sent[0] = True
                os.kill(os.getpid(), signal.SIGINT)
            return self.delegate.select(timeout)
    MODULE.selectors.DefaultSelector = SignalingSelector
    try:
        try:
            MODULE.run_bootstrap(
                profile, calls=MODULE.version.RunnerCalls(popen=fake_popen)
            )
        except SystemExit as exc:
            return exc.code == 130 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        MODULE.selectors.DefaultSelector = original_selector
        shutil.rmtree(root)


check("bounded capture wait polls its latched signal", blocking_capture_signal)


def first_signal(signum: int, second: int | None = None) -> bool:
    root, profile = fixture("signal-" + str(signum))
    def signaling(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = fake_popen(*args, **kwargs)
        os.kill(os.getpid(), signum)
        if second is not None: os.kill(os.getpid(), second)
        return process
    try:
        try:
            MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=signaling))
        except SystemExit as exc:
            return exc.code == 128 + signum and not Path(profile.bootstrap_root).exists()
        return False
    finally: shutil.rmtree(root)


for lifecycle_signal in MODULE.version.LIFECYCLE_SIGNALS:
    check("signal preserves conventional exit and rollback " + str(lifecycle_signal), lambda lifecycle_signal=lifecycle_signal: first_signal(lifecycle_signal))
check("first signal wins over a second signal", lambda: first_signal(signal.SIGHUP, signal.SIGTERM))


def after_launch_signal_has_no_orphan() -> bool:
    root, profile = fixture("signal-after-launch")
    late = root / "late.marker"
    timer: threading.Timer | None = None
    def launched(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
        nonlocal timer
        process = subprocess.Popen(
            ["/bin/sh", "-c", f"(sleep 0.8; touch '{late}') & sleep 5"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        timer = threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGINT))
        timer.start()
        return process
    try:
        try:
            MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=launched))
        except SystemExit as exc:
            exited = exc.code == 130
        else:
            exited = False
        if timer is not None: timer.join()
        time.sleep(1.0)
        marker = Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / "version.binding.sha256"
        return exited and not late.exists() and not marker.exists() and not Path(profile.bootstrap_root).exists()
    finally:
        if timer is not None: timer.cancel()
        shutil.rmtree(root)


check("after-launch SIGINT closes the exact group with no orphan or late marker", after_launch_signal_has_no_orphan)


def close_window_signal() -> bool:
    root, profile = fixture("signal-close-window")
    sent = [False]
    def signaling_killpg(pgid: int, signum: int) -> None:
        if signum == signal.SIGTERM and not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGHUP)
        os.killpg(pgid, signum)
    try:
        try:
            MODULE.run_bootstrap(
                profile,
                calls=MODULE.version.RunnerCalls(popen=fake_popen, killpg=signaling_killpg),
            )
        except SystemExit as exc:
            return exc.code == 129 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        shutil.rmtree(root)


check("signal during masked process-group close preserves first exit and rollback", close_window_signal)


def rollback_window_signal() -> bool:
    root, profile = fixture("signal-rollback-window")
    original_unlink = MODULE.os.unlink
    sent = [False]
    def signaling_unlink(path: object, *args: object, **kwargs: object) -> None:
        if path == "agy.snapshot" and not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGTERM)
        original_unlink(path, *args, **kwargs)
    def wrong(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            ["/bin/sh", "-c", "printf wrong"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    MODULE.os.unlink = signaling_unlink
    try:
        try:
            MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=wrong))
        except SystemExit as exc:
            return exc.code == 143 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        MODULE.os.unlink = original_unlink
        shutil.rmtree(root)


check("signal during ownership rollback cannot interrupt cleanup", rollback_window_signal)


def cleanup_release_signal() -> bool:
    root, profile = fixture("signal-cleanup-release")
    original_revalidate = MODULE._revalidate_scratch
    original_release = MODULE.OwnershipLedger.release
    calls = [0]
    sent = [False]
    def failing_revalidate(*args: object, **kwargs: object) -> None:
        calls[0] += 1
        if calls[0] == 2:
            raise MODULE.BootstrapError("synthetic post-child failure")
        original_revalidate(*args, **kwargs)
    def signaling_release(ledger: object) -> None:
        if not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGTERM)
        original_release(ledger)
    MODULE._revalidate_scratch = failing_revalidate
    MODULE.OwnershipLedger.release = signaling_release
    try:
        try:
            MODULE.run_bootstrap(
                profile, calls=MODULE.version.RunnerCalls(popen=fake_popen)
            )
        except SystemExit as exc:
            return exc.code == 143 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        MODULE._revalidate_scratch = original_revalidate
        MODULE.OwnershipLedger.release = original_release
        shutil.rmtree(root)


check("signal during final cleanup release is drained after rollback", cleanup_release_signal)


def no_group_call_after_reap() -> bool:
    root, profile = fixture("no-post-reap-group-call")
    state = {"reaped": False, "calls_after_reap": 0}
    class ProcessProxy:
        def __init__(self, child: subprocess.Popen[bytes]):
            self.child = child
            self.pid = child.pid
            self.stdout = child.stdout
            self.stderr = child.stderr
        @property
        def returncode(self) -> int | None:
            return self.child.returncode
        def wait(self, timeout: float) -> int:
            value = self.child.wait(timeout=timeout)
            state["reaped"] = True
            return value
    def proxied(*_args: object, **_kwargs: object) -> object:
        return ProcessProxy(fake_popen())
    def tracked_killpg(pgid: int, signum: int) -> None:
        if state["reaped"]: state["calls_after_reap"] += 1
        os.killpg(pgid, signum)
    try:
        result = MODULE.run_bootstrap(
            profile,
            calls=MODULE.version.RunnerCalls(popen=proxied, killpg=tracked_killpg),
        )
        return result["status"] == "accepted" and state == {"reaped": True, "calls_after_reap": 0}
    finally:
        shutil.rmtree(root)


check("sole controller performs zero group calls after its one reap", no_group_call_after_reap)


def handler_restore() -> bool:
    root, profile = fixture("handler-restore")
    original = {item: signal.getsignal(item) for item in MODULE.version.LIFECYCLE_SIGNALS}
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    def handler(_signum: int, _frame: object) -> None: return None
    for item in MODULE.version.LIFECYCLE_SIGNALS: signal.signal(item, handler)
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    expected_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    try:
        MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        return (
            signal.pthread_sigmask(signal.SIG_BLOCK, ()) == expected_mask
            and all(signal.getsignal(item) is handler for item in MODULE.version.LIFECYCLE_SIGNALS)
        )
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, MODULE.version.LIFECYCLE_SIGNALS)
        for item, prior in original.items(): signal.signal(item, prior)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        shutil.rmtree(root)


check("successful bootstrap restores caller signal handlers and exact entry mask", handler_restore)


def handler_setup_signal() -> bool:
    root, profile = fixture("handler-setup-signal")
    original_signal = MODULE.signal.signal
    original_handlers = {
        item: signal.getsignal(item) for item in MODULE.version.LIFECYCLE_SIGNALS
    }
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    caller_calls = [0]
    setup_calls = [0]
    sent = [False]
    def caller(_signum: int, _frame: object) -> None:
        caller_calls[0] += 1
    for item in MODULE.version.LIFECYCLE_SIGNALS:
        original_signal(item, caller)
    expected_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    def injecting(signum: int, handler: object) -> object:
        setup_calls[0] += 1
        observed = original_signal(signum, handler)
        if setup_calls[0] == 3 and not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGTERM)
        return observed
    MODULE.signal.signal = injecting
    try:
        try:
            MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        except SystemExit as exc:
            return (
                exc.code == 143
                and sent == [True]
                and caller_calls == [0]
                and signal.pthread_sigmask(signal.SIG_BLOCK, ()) == expected_mask
                and all(signal.getsignal(item) is caller for item in MODULE.version.LIFECYCLE_SIGNALS)
                and not Path(profile.bootstrap_root).exists()
            )
        return False
    finally:
        MODULE.signal.signal = original_signal
        signal.pthread_sigmask(signal.SIG_BLOCK, MODULE.version.LIFECYCLE_SIGNALS)
        for item, prior in original_handlers.items(): original_signal(item, prior)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        shutil.rmtree(root)


check("signal queued during third handler setup is controller-owned", handler_setup_signal)


def partial_handler_install_failure() -> bool:
    root, profile = fixture("partial-handler-install")
    original_signal = MODULE.signal.signal
    original_handlers = {
        item: signal.getsignal(item) for item in MODULE.version.LIFECYCLE_SIGNALS
    }
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    setup_calls = [0]
    def failing(signum: int, handler: object) -> object:
        setup_calls[0] += 1
        observed = original_signal(signum, handler)
        if setup_calls[0] == 3:
            raise RuntimeError("synthetic partial handler install")
        return observed
    MODULE.signal.signal = failing
    try:
        try:
            MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        except RuntimeError:
            return (
                signal.pthread_sigmask(signal.SIG_BLOCK, ()) == entry_mask
                and all(
                    signal.getsignal(item) == original_handlers[item]
                    for item in MODULE.version.LIFECYCLE_SIGNALS
                )
                and not Path(profile.bootstrap_root).exists()
            )
        return False
    finally:
        MODULE.signal.signal = original_signal
        signal.pthread_sigmask(signal.SIG_BLOCK, MODULE.version.LIFECYCLE_SIGNALS)
        for item, prior in original_handlers.items(): original_signal(item, prior)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        shutil.rmtree(root)


check("partial handler install failure restores every changed handler and mask", partial_handler_install_failure)


def close_durability_signal() -> bool:
    root, profile = fixture("close-durability-signal")
    original = MODULE.OwnershipLedger.close
    sent = [False]
    def signaling_close(ledger: object) -> None:
        if not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGHUP)
        original(ledger)
    MODULE.OwnershipLedger.close = signaling_close
    try:
        try:
            MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        except SystemExit as exc:
            return exc.code == 129 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        MODULE.OwnershipLedger.close = original
        shutil.rmtree(root)


check("signal during final ledger durability close rolls back before completion", close_durability_signal)


def marker_signal(phase: str) -> bool:
    root, profile = fixture("marker-signal-" + phase)
    original_publish = MODULE.OwnedPublisher.publish
    original_link = MODULE.os.link
    sent = [False]
    def publishing(publisher: object, name: str, data: bytes) -> str:
        if name == "version.binding.sha256" and phase == "before" and not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGINT)
        result = original_publish(publisher, name, data)
        if name == "version.binding.sha256" and phase == "after" and not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGINT)
        return result
    def linking(*args: object, **kwargs: object) -> None:
        original_link(*args, **kwargs)
        if str(args[1]) == "version.binding.sha256" and phase == "at" and not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGINT)
    MODULE.OwnedPublisher.publish = publishing
    MODULE.os.link = linking
    try:
        try:
            MODULE.run_bootstrap(profile, calls=MODULE.version.RunnerCalls(popen=fake_popen))
        except SystemExit as exc:
            return exc.code == 130 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        MODULE.OwnedPublisher.publish = original_publish
        MODULE.os.link = original_link
        shutil.rmtree(root)


for marker_phase in ("before", "at", "after"):
    check(
        "signal " + marker_phase + " final marker remains pre-completion rollback",
        lambda marker_phase=marker_phase: marker_signal(marker_phase),
    )


def post_completion_signal_is_consumed() -> bool:
    root, profile = fixture("post-completion-signal")
    original_release = MODULE.OwnershipLedger.release
    original_handlers = {
        item: signal.getsignal(item) for item in MODULE.version.LIFECYCLE_SIGNALS
    }
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    caller_calls = [0]
    sent = [False]
    def caller(_signum: int, _frame: object) -> None:
        caller_calls[0] += 1
    for item in MODULE.version.LIFECYCLE_SIGNALS: signal.signal(item, caller)
    expected_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    def signaling_release(ledger: object) -> None:
        if not sent[0]:
            sent[0] = True
            os.kill(os.getpid(), signal.SIGTERM)
        original_release(ledger)
    MODULE.OwnershipLedger.release = signaling_release
    try:
        result = MODULE.run_bootstrap(
            profile, calls=MODULE.version.RunnerCalls(popen=fake_popen)
        )
        marker = Path(profile.bootstrap_root) / "agy-version-attestation.bootstrap" / "version.binding.sha256"
        return (
            result["status"] == "accepted"
            and marker.exists()
            and sent == [True]
            and caller_calls == [1]
            and signal.pthread_sigmask(signal.SIG_BLOCK, ()) == expected_mask
            and all(signal.getsignal(item) is caller for item in MODULE.version.LIFECYCLE_SIGNALS)
        )
    finally:
        MODULE.OwnershipLedger.release = original_release
        signal.pthread_sigmask(signal.SIG_BLOCK, MODULE.version.LIFECYCLE_SIGNALS)
        for item, prior in original_handlers.items(): signal.signal(item, prior)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        shutil.rmtree(root)


check("post-snapshot embedded handoff delivers caller-owned signal", post_completion_signal_is_consumed)


def production_run(
    profile: object,
    setup: Callable[[], None] | None = None,
    *,
    broken_stdout: bool = False,
) -> tuple[int, bytes, bytes]:
    stdin_read, stdin_write = os.pipe()
    stdout_read, stdout_write = os.pipe()
    stderr_read, stderr_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.dup2(stdin_read, 0)
            os.dup2(stdout_write, 1)
            os.dup2(stderr_write, 2)
            for descriptor in (
                stdin_read,
                stdin_write,
                stdout_read,
                stdout_write,
                stderr_read,
                stderr_write,
            ):
                if descriptor > 2:
                    os.close(descriptor)
            if setup is not None:
                setup()
            MODULE.main(["--bootstrap-version"])
        except BaseException:
            os._exit(98)
        os._exit(99)
    os.close(stdin_read)
    os.close(stdout_write)
    os.close(stderr_write)
    if broken_stdout:
        os.close(stdout_read)
    profile_bytes = MODULE._canonical_json(dataclasses.asdict(profile))
    offset = 0
    while offset < len(profile_bytes):
        offset += os.write(stdin_write, profile_bytes[offset:])
    os.close(stdin_write)
    stdout = b"" if broken_stdout else os.read(stdout_read, 64 * 1024)
    if not broken_stdout:
        os.close(stdout_read)
    stderr = os.read(stderr_read, 64 * 1024)
    os.close(stderr_read)
    _pid, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), stdout, stderr


def production_positive() -> bool:
    root, profile = fixture("production-positive")
    try:
        code, stdout, stderr = production_run(profile)
        result = json.loads(stdout)
        marker = (
            Path(profile.bootstrap_root)
            / "agy-version-attestation.bootstrap"
            / "version.binding.sha256"
        )
        return (
            code == 0
            and stderr == b""
            and stdout.endswith(b"\n")
            and result["status"] == "accepted"
            and result["claim"] == "snapshot-version-bootstrap"
            and marker.exists()
        )
    finally:
        shutil.rmtree(root)


check("production success writes one line then commits by atomic exit 0", production_positive)


def production_pre_snapshot_signal() -> bool:
    root, profile = fixture("production-pre-snapshot")
    original_close = MODULE.OwnershipLedger.close
    def setup() -> None:
        def signaling_close(ledger: object) -> None:
            os.kill(os.getpid(), signal.SIGTERM)
            original_close(ledger)
        MODULE.OwnershipLedger.close = signaling_close
    try:
        code, stdout, stderr = production_run(profile, setup)
        return (
            code == 143
            and stdout == b""
            and stderr == b"version bootstrap: interrupted\n"
            and not Path(profile.bootstrap_root).exists()
        )
    finally:
        MODULE.OwnershipLedger.close = original_close
        shutil.rmtree(root)


check("production pre-snapshot signal rolls back with no marker", production_pre_snapshot_signal)


def production_post_snapshot_signal() -> bool:
    root, profile = fixture("production-post-snapshot")
    original_exit = MODULE.os._exit
    def setup() -> None:
        def signaling_exit(code: int) -> None:
            if code == 0:
                os.kill(os.getpid(), signal.SIGTERM)
            original_exit(code)
        MODULE.os._exit = signaling_exit
    try:
        code, stdout, stderr = production_run(profile, setup)
        marker = (
            Path(profile.bootstrap_root)
            / "agy-version-attestation.bootstrap"
            / "version.binding.sha256"
        )
        return code == 0 and json.loads(stdout)["status"] == "accepted" and stderr == b"" and marker.exists()
    finally:
        MODULE.os._exit = original_exit
        shutil.rmtree(root)


check("production post-snapshot signal remains committed exit 0", production_post_snapshot_signal)


def production_broken_stdout() -> bool:
    root, profile = fixture("production-broken-stdout")
    try:
        code, stdout, stderr = production_run(profile, broken_stdout=True)
        return (
            code == 2
            and stdout == b""
            and stderr == b"version bootstrap: rejected\n"
            and not Path(profile.bootstrap_root).exists()
        )
    finally:
        shutil.rmtree(root)


check("broken production stdout rolls back provisional evidence", production_broken_stdout)


def production_partial_stdout() -> bool:
    root, profile = fixture("production-partial-stdout")
    original_write = MODULE.os.write
    def setup() -> None:
        wrote = [False]
        def partial_write(descriptor: int, data: object) -> int:
            if descriptor == 1:
                if wrote[0]:
                    raise BrokenPipeError("synthetic partial stdout")
                wrote[0] = True
                view = memoryview(data)
                return original_write(descriptor, view[: max(1, len(view) // 2)])
            return original_write(descriptor, data)
        MODULE.os.write = partial_write
    try:
        code, stdout, stderr = production_run(profile, setup)
        return (
            code == 2
            and bool(stdout)
            and not stdout.endswith(b"\n")
            and stderr == b"version bootstrap: rejected\n"
            and not Path(profile.bootstrap_root).exists()
        )
    finally:
        MODULE.os.write = original_write
        shutil.rmtree(root)


check("partial production stdout cannot retain a completion marker", production_partial_stdout)


def production_cleanup_release_signal() -> bool:
    root, profile = fixture("production-cleanup-release-signal")
    original_revalidate = MODULE._revalidate_scratch
    original_release = MODULE.OwnershipLedger.release
    def setup() -> None:
        calls = [0]
        def failing_revalidate(*args: object, **kwargs: object) -> None:
            calls[0] += 1
            if calls[0] == 2:
                raise MODULE.BootstrapError("synthetic post-child failure")
            original_revalidate(*args, **kwargs)
        def signaling_release(ledger: object) -> None:
            os.kill(os.getpid(), signal.SIGTERM)
            original_release(ledger)
        MODULE._revalidate_scratch = failing_revalidate
        MODULE.OwnershipLedger.release = signaling_release
    try:
        code, stdout, stderr = production_run(profile, setup)
        return (
            code == 143
            and stdout == b""
            and stderr == b"version bootstrap: interrupted\n"
            and not Path(profile.bootstrap_root).exists()
        )
    finally:
        MODULE._revalidate_scratch = original_revalidate
        MODULE.OwnershipLedger.release = original_release
        shutil.rmtree(root)


check("production signal during cleanup release selects interrupted exit", production_cleanup_release_signal)


def production_copy_link_signal(phase: str, target: str) -> bool:
    root, profile = fixture("production-copy-link-signal-" + phase)
    mask_barrier = root / "link-mask.barrier"
    normalized_barrier = root / "link-normalized.barrier"
    early_poll = root / "link-early-poll.barrier"
    original_link = MODULE.os.link
    original_unlink = MODULE.os.unlink
    original_register = MODULE.OwnershipLedger.register_linked_file
    original_poll = MODULE.SignalController.poll
    def setup() -> None:
        state = {"sent": False, "transient": False}
        def record_signal() -> None:
            blocked = signal.pthread_sigmask(signal.SIG_BLOCK, ())
            mask_barrier.write_text(
                "blocked" if signal.SIGHUP in blocked else "unblocked",
                encoding="ascii",
            )
            state["sent"] = True
            os.kill(os.getpid(), signal.SIGHUP)
        def signaling_link(*args: object, **kwargs: object) -> None:
            original_link(*args, **kwargs)
            if str(args[1]) == target:
                state["transient"] = True
                if phase == "link":
                    record_signal()
        def signaling_register(
            ledger: object,
            temporary: tuple[str, ...],
            final: tuple[str, ...],
            identity: object,
        ) -> None:
            original_register(ledger, temporary, final, identity)
            if final[-1] == target and phase == "register":
                record_signal()
        def tracking_unlink(path: object, *args: object, **kwargs: object) -> None:
            original_unlink(path, *args, **kwargs)
            value = str(path)
            if (
                state["transient"]
                and value.startswith("." + target + ".")
                and value.endswith(".tmp")
            ):
                state["transient"] = False
                normalized_barrier.write_text("normalized", encoding="ascii")
        def guarded_poll(controller: object) -> None:
            if state["transient"]:
                early_poll.write_text("observed", encoding="ascii")
            original_poll(controller)
        MODULE.os.link = signaling_link
        MODULE.os.unlink = tracking_unlink
        MODULE.OwnershipLedger.register_linked_file = signaling_register
        MODULE.SignalController.poll = guarded_poll
    try:
        code, stdout, stderr = production_run(profile, setup)
        marker = (
            Path(profile.bootstrap_root)
            / "agy-version-attestation.bootstrap"
            / "version.binding.sha256"
        )
        return (
            code == 129
            and stdout == b""
            and stderr == b"version bootstrap: interrupted\n"
            and mask_barrier.read_text("ascii") == "unblocked"
            and normalized_barrier.read_text("ascii") == "normalized"
            and not early_poll.exists()
            and not Path(profile.bootstrap_root).exists()
            and not marker.exists()
        )
    finally:
        MODULE.os.link = original_link
        MODULE.os.unlink = original_unlink
        MODULE.OwnershipLedger.register_linked_file = original_register
        MODULE.SignalController.poll = original_poll
        shutil.rmtree(root)


check(
    "source-copy link signal is polled only after exact nlink1 normalization",
    lambda: production_copy_link_signal("link", "agy.source"),
)
check(
    "snapshot-copy registered-link signal is polled only after exact nlink1 normalization",
    lambda: production_copy_link_signal("register", "agy.snapshot"),
)


def production_second_snapshot_copy_signal() -> bool:
    root, profile = fixture(
        "production-second-snapshot-copy-signal", 2 * 1024 * 1024 + 37
    )
    barrier = root / "copy-mask.barrier"
    next_chunk = root / "copy-next-chunk.barrier"
    snapshot_identity = Path(profile.retained_snapshot_path).stat()
    original_read = MODULE.os.read
    def setup() -> None:
        sent = [False]
        def signaling_read(descriptor: int, size: int) -> bytes:
            try:
                offset = os.lseek(descriptor, 0, os.SEEK_CUR)
            except OSError:
                return original_read(descriptor, size)
            block = original_read(descriptor, size)
            observed = os.fstat(descriptor)
            if (
                observed.st_dev == snapshot_identity.st_dev
                and observed.st_ino == snapshot_identity.st_ino
                and Path(profile.bootstrap_root).exists()
            ):
                if not sent[0] and offset == 0:
                    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, ())
                    barrier.write_text(
                        "blocked" if signal.SIGHUP in blocked else "unblocked",
                        encoding="ascii",
                    )
                    sent[0] = True
                    os.kill(os.getpid(), signal.SIGHUP)
                elif sent[0]:
                    next_chunk.write_text("observed", encoding="ascii")
            return block
        MODULE.os.read = signaling_read
    try:
        code, stdout, stderr = production_run(profile, setup)
        barrier_value = barrier.read_text("ascii") if barrier.exists() else "missing"
        return (
            code == 129
            and stdout == b""
            and stderr == b"version bootstrap: interrupted\n"
            and barrier_value == "unblocked"
            and not next_chunk.exists()
            and not Path(profile.bootstrap_root).exists()
        )
    finally:
        MODULE.os.read = original_read
        shutil.rmtree(root)


check(
    "second snapshot copy observes unblocked HUP after one MiB with no next chunk",
    production_second_snapshot_copy_signal,
)


def production_final_ledger_hash_signal() -> bool:
    root, profile = fixture(
        "production-final-ledger-hash-signal", 2 * 1024 * 1024 + 37
    )
    barrier = root / "ledger-mask.barrier"
    next_chunk = root / "ledger-next-chunk.barrier"
    original_read = MODULE.os.read
    original_validate = MODULE.OwnershipLedger.validate
    def setup() -> None:
        validating = [False]
        sent = [False]
        def tagged_validate(ledger: object, controller: object) -> None:
            validating[0] = True
            try:
                original_validate(ledger, controller)
            finally:
                validating[0] = False
        def signaling_read(descriptor: int, size: int) -> bytes:
            try:
                offset = os.lseek(descriptor, 0, os.SEEK_CUR)
            except OSError:
                return original_read(descriptor, size)
            block = original_read(descriptor, size)
            observed = os.fstat(descriptor)
            if validating[0] and observed.st_size == MODULE.EXPECTED_SIZE:
                if not sent[0] and offset == 0:
                    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, ())
                    barrier.write_text(
                        "blocked" if signal.SIGHUP in blocked else "unblocked",
                        encoding="ascii",
                    )
                    sent[0] = True
                    os.kill(os.getpid(), signal.SIGHUP)
                elif sent[0]:
                    next_chunk.write_text("observed", encoding="ascii")
            return block
        MODULE.OwnershipLedger.validate = tagged_validate
        MODULE.os.read = signaling_read
    try:
        code, stdout, stderr = production_run(profile, setup)
        barrier_value = barrier.read_text("ascii") if barrier.exists() else "missing"
        return (
            code == 129
            and stdout == b""
            and stderr == b"version bootstrap: interrupted\n"
            and barrier_value == "unblocked"
            and not next_chunk.exists()
            and not Path(profile.bootstrap_root).exists()
        )
    finally:
        MODULE.os.read = original_read
        MODULE.OwnershipLedger.validate = original_validate
        shutil.rmtree(root)


check(
    "final ledger hash observes unblocked HUP after one MiB with no next chunk",
    production_final_ledger_hash_signal,
)


def production_priority(first: int, second: int, expected: int) -> bool:
    root, profile = fixture(f"production-priority-{first}-{second}")
    original_close = MODULE.OwnershipLedger.close
    def setup() -> None:
        def signaling_close(ledger: object) -> None:
            os.kill(os.getpid(), first)
            os.kill(os.getpid(), second)
            original_close(ledger)
        MODULE.OwnershipLedger.close = signaling_close
    try:
        code, stdout, _stderr = production_run(profile, setup)
        return code == 128 + expected and stdout == b"" and not Path(profile.bootstrap_root).exists()
    finally:
        MODULE.OwnershipLedger.close = original_close
        shutil.rmtree(root)


for priority_first, priority_second, priority_expected in (
    (signal.SIGTERM, signal.SIGHUP, signal.SIGHUP),
    (signal.SIGHUP, signal.SIGTERM, signal.SIGHUP),
    (signal.SIGINT, signal.SIGTERM, signal.SIGINT),
):
    check(
        "blocked-window priority is deterministic "
        + f"{priority_first}/{priority_second}->{priority_expected}",
        lambda priority_first=priority_first, priority_second=priority_second, priority_expected=priority_expected: production_priority(
            priority_first, priority_second, priority_expected
        ),
    )


def production_frozen_checkpoint() -> bool:
    root, profile = fixture("production-frozen-checkpoint")
    original_capture = MODULE._capture
    original_rollback = MODULE.OwnershipLedger.rollback
    def setup() -> None:
        def term_capture(*args: object, **kwargs: object) -> object:
            os.kill(os.getpid(), signal.SIGTERM)
            return original_capture(*args, **kwargs)
        def hup_rollback(ledger: object) -> bool:
            os.kill(os.getpid(), signal.SIGHUP)
            return original_rollback(ledger)
        MODULE._capture = term_capture
        MODULE.OwnershipLedger.rollback = hup_rollback
    try:
        code, stdout, _stderr = production_run(profile, setup)
        return code == 143 and stdout == b"" and not Path(profile.bootstrap_root).exists()
    finally:
        MODULE._capture = original_capture
        MODULE.OwnershipLedger.rollback = original_rollback
        shutil.rmtree(root)


check("earlier TERM checkpoint stays frozen after cleanup HUP", production_frozen_checkpoint)


def hash_chunk_signal(offset_target: int) -> bool:
    root, profile = fixture(
        "hash-signal-" + str(offset_target), 2 * 1024 * 1024 + 37
    )
    original_read = MODULE.os.read
    source_identity = Path(profile.retained_source_path).stat()
    sent = [False]
    def signaling_read(descriptor: int, size: int) -> bytes:
        offset = os.lseek(descriptor, 0, os.SEEK_CUR)
        block = original_read(descriptor, size)
        observed = os.fstat(descriptor)
        if (
            not sent[0]
            and observed.st_dev == source_identity.st_dev
            and observed.st_ino == source_identity.st_ino
            and offset == offset_target
        ):
            sent[0] = True
            os.kill(os.getpid(), signal.SIGHUP)
        return block
    MODULE.os.read = signaling_read
    try:
        try:
            MODULE.run_bootstrap(
                profile, calls=MODULE.version.RunnerCalls(popen=fake_popen)
            )
        except SystemExit as exc:
            return exc.code == 129 and sent == [True] and not Path(profile.bootstrap_root).exists()
        return False
    finally:
        MODULE.os.read = original_read
        shutil.rmtree(root)


for hash_offset, hash_label in (
    (0, "first"),
    (1024 * 1024, "middle"),
    (2 * 1024 * 1024, "final"),
):
    check(
        "attested hash polls after its " + hash_label + " <=1MiB chunk",
        lambda hash_offset=hash_offset: hash_chunk_signal(hash_offset),
    )


def invalid_cli_process_owned() -> bool:
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(MODULE_PATH), "bootstrap"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return (
        result.returncode == 64
        and result.stdout == b""
        and result.stderr == b"version bootstrap: invalid invocation\n"
    )


check("invalid production CLI is one process-owned usage exit", invalid_cli_process_owned)


def wrong_flag_production_preflight() -> bool:
    root, profile = fixture("wrong-flag-production-preflight")
    try:
        result = subprocess.run(
            ["/usr/bin/python3", str(MODULE_PATH), "--bootstrap-version"],
            input=MODULE._canonical_json(dataclasses.asdict(profile)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return (
            result.returncode == 2
            and result.stdout == b""
            and result.stderr == b"version bootstrap: rejected\n"
            and not Path(profile.bootstrap_root).exists()
        )
    finally:
        shutil.rmtree(root)


check("wrong production flags reject once before filesystem mutation", wrong_flag_production_preflight)

shutil.rmtree(TMP)
print(f"version bootstrap runner offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
