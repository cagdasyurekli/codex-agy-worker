#!/usr/bin/env python3
"""Offline synthetic tests for the current-source initial bootstrap bridge."""

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


COMMAND = "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-version-evidence.py"


def runtime_ok() -> bool:
    return (
        sys.implementation.name == "cpython"
        and sys.version_info[:2] == (3, 9)
        and sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == sys.flags.ignore_environment == 1
    )


if not runtime_ok():
    sys.stderr.write("version initial bootstrap tests: unsupported interpreter or flags; run " + COMMAND + "\n")
    raise SystemExit(2)


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "version_manifest_version_evidence.py"
LEGACY_MODULE_PATH = ROOT / "scripts" / "models_capture_1_1_22_version_evidence.py"
SPEC = importlib.util.spec_from_file_location("models_capture_1_1_22_version_evidence_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
TMP = Path(tempfile.mkdtemp(prefix="agyworker-version-initial-bootstrap-tests.")).resolve()
TMP.chmod(0o700)
passed = failed = 0


def check(name: str, predicate: Callable[[], bool]) -> None:
    global passed, failed
    try:
        ok = bool(predicate())
    except BaseException as exc:
        ok = False
        print("FAIL version initial bootstrap: %s (%s: %s)" % (name, type(exc).__name__, exc))
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL version initial bootstrap: " + name)


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (MODULE.InitialBootstrapError, MODULE.version.AttestationError, OSError, ValueError):
        return True
    return False


def private(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


FAKE = b"#!/bin/sh\nprintf '1.1.22\\n'\n"


def fixture(label: str) -> tuple[Path, MODULE.InitialProfile]:
    root = private(TMP / label)
    source = root / "current-agy"
    source.write_bytes(FAKE)
    source.chmod(0o755)
    output_parent = private(root / "private-output")
    source_identity = MODULE.version.FileIdentity.from_stat(source.stat())
    profile = MODULE.InitialProfile(
        bootstrap_root=str(output_parent / "fresh-root"),
        expected_version=MODULE.EXPECTED_VERSION,
        source_identity=source_identity,
        source_path=str(source),
        source_sha256=MODULE.EXPECTED_SOURCE_SHA256,
    )
    return root, profile


def fake_constants() -> tuple[int, str]:
    old = MODULE.EXPECTED_SIZE, MODULE.EXPECTED_SOURCE_SHA256
    MODULE.EXPECTED_SIZE = len(FAKE)
    MODULE.EXPECTED_SOURCE_SHA256 = hashlib.sha256(FAKE).hexdigest()
    return old


def restore_constants(value: tuple[int, str]) -> None:
    MODULE.EXPECTED_SIZE, MODULE.EXPECTED_SOURCE_SHA256 = value


def bound_test_profile(profile: MODULE.InitialProfile) -> MODULE.InitialProfile:
    return dataclasses.replace(profile, expected_version=MODULE.EXPECTED_VERSION, source_sha256=MODULE.EXPECTED_SOURCE_SHA256)


check("exact CPython 3.9 isolation predicate accepts exact flags", lambda: MODULE._runtime_contract("cpython", 3, 9, 1, 1, 1, 1))
check("runtime predicate rejects wrong implementation", lambda: not MODULE._runtime_contract("pypy", 3, 9, 1, 1, 1, 1))
check("runtime predicate rejects wrong minor", lambda: not MODULE._runtime_contract("cpython", 3, 10, 1, 1, 1, 1))
check("runtime predicate rejects missing isolation", lambda: not MODULE._runtime_contract("cpython", 3, 9, 0, 1, 1, 1))
check("version evidence owns exact 1.1.22 stdout authority", lambda: MODULE.EXPECTED_VERSION == "1.1.22" and MODULE.EXPECTED_STDOUT == b"1.1.22\n")
check("installed source pin is exact", lambda: MODULE.EXPECTED_SOURCE_SHA256 == "7b1317779085913d338bde0e9b39b72323d9083a879525f944fd469c8ecca906" and MODULE.EXPECTED_SIZE == 179_586_688)
check("official distribution observation is exact", lambda: MODULE.EXPECTED_RELEASE_COMMIT == "556846a4bb94117222f53846896c7eb0d645307e" and MODULE.EXPECTED_DISTRIBUTION_URL == "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.1.22-5711547746615296/darwin-arm/cli_mac_arm64.tar.gz" and MODULE.EXPECTED_DISTRIBUTION_SHA512 == "a8121185bd1c3455410ad41e88e2030ea237d496b8e40ccde313bf611c0551840fddf450b45c8e1a2575d9863c990b3324f19eef0f479936df8bfc6e4e80d30b")
check("profile key set is closed", lambda: MODULE.INITIAL_KEYS == frozenset(("bootstrap_root", "expected_version", "source_identity", "source_path", "source_sha256")))


root, profile = fixture("profile")
value = {
    "bootstrap_root": profile.bootstrap_root,
    "expected_version": profile.expected_version,
    "source_identity": profile.source_identity.as_dict(),
    "source_path": profile.source_path,
    "source_sha256": profile.source_sha256,
}
encoded = MODULE._canonical_json(value)
check("canonical closed profile accepts current-source identity", lambda: MODULE.InitialProfile.from_bytes(encoded) == profile)
for label, altered in (
    ("profile rejects extra key", dict(value, account_home="/not/read")),
    ("profile rejects missing key", {key: item for key, item in value.items() if key != "source_sha256"}),
    ("profile rejects wrong expected version", dict(value, expected_version="1.1.10")),
    ("profile rejects wrong expected sha", dict(value, source_sha256="0" * 64)),
    ("profile rejects relative source", dict(value, source_path="relative")),
    ("profile rejects existing root", dict(value, bootstrap_root=str(root))),
):
    check(label, lambda altered=altered: rejects(lambda: MODULE.InitialProfile.from_bytes(MODULE._canonical_json(altered))))
check("profile rejects noncanonical JSON", lambda: rejects(lambda: MODULE.InitialProfile.from_bytes(json.dumps(value, indent=2).encode("ascii"))))
bad_identity = dict(value)
bad_identity["source_identity"] = dict(value["source_identity"], size=True)
check("profile rejects boolean identity component", lambda: rejects(lambda: MODULE.InitialProfile.from_bytes(MODULE._canonical_json(bad_identity))))
check("direct profile with unbound expected SHA rejects", lambda: rejects(lambda: MODULE._validate_profile(dataclasses.replace(profile, source_sha256="0" * 64), MODULE.SignalController(()))))
check("initial runner contains no account-home profile authority", lambda: b"account_home" not in MODULE_PATH.read_bytes())
check("own source AST pin accepts reviewed bytes", lambda: MODULE.validate_source_contract(MODULE._module_source())["status"] == "accepted")
mutated_source = MODULE._module_source().replace(b"start_new_session=True", b"start_new_session=False", 1)
check("own source AST pin rejects a child-session mutation", lambda: rejects(lambda: MODULE.validate_source_contract(mutated_source)))


def repin_module_ast(data: bytes) -> bytes:
    import ast

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


SOURCE = MODULE._module_source()
for label, altered in (
    ("removal", SOURCE.replace(b'"source_continuity_claimed": False, ', b"", 1)),
    ("rename", SOURCE.replace(b'"source_continuity_claimed": False', b'"continuity": False', 1)),
    ("true", SOURCE.replace(b'"source_continuity_claimed": False', b'"source_continuity_claimed": True', 1)),
):
    check(
        "repinned source contract rejects historical non-continuity " + label,
        lambda altered=altered: rejects(lambda: MODULE.validate_source_contract(repin_module_ast(altered))),
    )


for label, altered in (
    ("removal", SOURCE.replace(b'"provider_backend_proven": False, "routing_authority": False', b'"provider_backend_proven": False', 1)),
    ("rename", SOURCE.replace(b'"routing_authority": False', b'"route_authority": False', 1)),
    ("true", SOURCE.replace(b'"routing_authority": False', b'"routing_authority": True', 1)),
):
    check(
        "repinned source contract rejects recovery runner reconciliation limit " + label,
        lambda altered=altered: rejects(lambda: MODULE.validate_source_contract(repin_module_ast(altered))),
    )


imported_stdout = SOURCE.replace(b"stdout != EXPECTED_STDOUT", b"stdout != version.EXPECTED_STDOUT", 1)
check(
    "repinned source contract rejects imported historical stdout authority",
    lambda: rejects(lambda: MODULE.validate_source_contract(repin_module_ast(imported_stdout))),
)


def complete_bridge() -> bool:
    root, profile = fixture("complete")
    prior = None
    old = fake_constants()
    old_run_attestation = MODULE.version.run_attestation
    canonical_run_attempted = [False]
    try:
        profile = bound_test_profile(profile)
        def forbidden_canonical_recovery(*_args: object, **_kwargs: object) -> object:
            canonical_run_attempted[0] = True
            raise AssertionError("initial bridge must not execute canonical recovery")
        MODULE.version.run_attestation = forbidden_canonical_recovery
        result = MODULE.run_initial_bootstrap(profile)
        generated = MODULE.version.AttestationProfile.from_bytes(MODULE._canonical_json(result["profile"]))
        prior = Path(generated.prior_root)
        binding = json.loads((prior / "version.binding.json").read_text("ascii"))
        return (
            result["status"] == "accepted"
            and result["claim"] == "snapshot-version-capture-source"
            and result["call_count"] == 1
            and generated.source_path.endswith("agy.source")
            and generated.snapshot_path.endswith("agy.snapshot")
            and set(item.name for item in prior.iterdir()) == MODULE.EVIDENCE_FILES
            and binding["claim"] == "snapshot-version-only"
            and binding["version"]["logical_argv"] == [generated.source_path, "--version"]
            and binding["version"]["expected"] == "1.1.22"
            and binding["version"]["observed"] == "1.1.22"
            and (prior / "version.stdout").read_bytes() == MODULE.EXPECTED_STDOUT
            and binding["inventory"] == {"executable_version_bound": False}
            and binding["historical_recovery"]["bytes_used"] is False
            and binding["historical_recovery"]["revalidated"] is False
            and binding["historical_recovery"]["source_continuity_claimed"] is False
            and binding["limitations"]["network_absence_os_enforced"] is False
            and binding["limitations"]["models_called"] is False
            and binding["limitations"]["account_read"] is False
            and binding["limitations"]["routing_authority"] is False
            and binding["official_observation"]["version"] == "1.1.22"
            and binding["artifacts"]["runner.py"] == result["runner_sha256"]
            and MODULE.version.EXPECTED_VERSION == "1.1.11"
            and MODULE.version.EXPECTED_STDOUT == b"1.1.11\n"
            and not canonical_run_attempted[0]
            and (Path(profile.bootstrap_root) / "initial-bootstrap.profile.json").is_file()
            and (Path(profile.bootstrap_root) / "version.recovery.profile.json").is_file()
            and not (root / "current-agy").is_symlink()
        )
    finally:
        MODULE.version.run_attestation = old_run_attestation
        restore_constants(old)
        shutil.rmtree(root)


check("one held-source bridge emits only structural 1.1.22 capture-source evidence", complete_bridge)


def source_drift_rejects_before_root() -> bool:
    root, profile = fixture("source-drift")
    old = fake_constants()
    try:
        profile = bound_test_profile(profile)
        Path(profile.source_path).write_bytes(FAKE + b"# drift\n")
        return rejects(lambda: MODULE.run_initial_bootstrap(profile)) and not Path(profile.bootstrap_root).exists()
    finally:
        restore_constants(old)
        shutil.rmtree(root)


check("source identity/hash drift rejects before a new root", source_drift_rejects_before_root)


def root_overlap_rejects() -> bool:
    root, profile = fixture("overlap")
    old = fake_constants()
    try:
        profile = bound_test_profile(profile)
        inside_repo = dataclasses.replace(profile, bootstrap_root=str(ROOT / "forbidden-initial-root"))
        return rejects(lambda: MODULE.run_initial_bootstrap(inside_repo))
    finally:
        restore_constants(old)
        shutil.rmtree(root)


check("repository output overlap rejects", root_overlap_rejects)


def source_repo_overlap_rejects() -> bool:
    root, profile = fixture("source-repo")
    old = fake_constants()
    try:
        profile = bound_test_profile(profile)
        fake = ROOT / "scripts" / "version_manifest_version_evidence.py"
        drift = dataclasses.replace(profile, source_path=str(fake))
        return rejects(lambda: MODULE.run_initial_bootstrap(drift))
    finally:
        restore_constants(old)
        shutil.rmtree(root)


check("repository source overlap rejects", source_repo_overlap_rejects)


def parent_mode_rejects() -> bool:
    root, profile = fixture("parent-mode")
    old = fake_constants()
    try:
        profile = bound_test_profile(profile)
        Path(profile.bootstrap_root).parent.chmod(0o755)
        return rejects(lambda: MODULE.run_initial_bootstrap(profile)) and not Path(profile.bootstrap_root).exists()
    finally:
        restore_constants(old)
        shutil.rmtree(root)


check("nonprivate output parent rejects", parent_mode_rejects)


def stale_1_1_11_stdout_rejects() -> bool:
    root, profile = fixture("stale-1-1-11-output")
    old = fake_constants()
    try:
        profile = bound_test_profile(profile)
        Path(profile.source_path).write_bytes(b"#!/bin/sh\nprintf '1.1.11\\n'\n")
        Path(profile.source_path).chmod(0o755)
        identity = MODULE.version.FileIdentity.from_stat(Path(profile.source_path).stat())
        changed = dataclasses.replace(profile, source_identity=identity)
        MODULE.EXPECTED_SIZE = identity.size
        MODULE.EXPECTED_SOURCE_SHA256 = hashlib.sha256(Path(profile.source_path).read_bytes()).hexdigest()
        changed = bound_test_profile(changed)
        return rejects(lambda: MODULE.run_initial_bootstrap(changed)) and not Path(changed.bootstrap_root).exists()
    finally:
        restore_constants(old)
        shutil.rmtree(root)


check("stale 1.1.11 stdout rejects with safe rollback and no acceptance", stale_1_1_11_stdout_rejects)


def scratch_mutation_rejects() -> bool:
    root, profile = fixture("scratch-mutation")
    old = fake_constants()
    try:
        profile = bound_test_profile(profile)
        source = Path(profile.source_path)
        source.write_bytes(b"#!/bin/sh\n: > \"$TMPDIR/residual\"\nprintf '1.1.22\\n'\n")
        source.chmod(0o755)
        changed = dataclasses.replace(profile, source_identity=MODULE.version.FileIdentity.from_stat(source.stat()))
        MODULE.EXPECTED_SIZE = source.stat().st_size
        MODULE.EXPECTED_SOURCE_SHA256 = hashlib.sha256(source.read_bytes()).hexdigest()
        changed = bound_test_profile(changed)
        residual = Path(changed.bootstrap_root)
        return rejects(lambda: MODULE.run_initial_bootstrap(changed)) and residual.is_dir() and (residual / "agy-models-capture-1.1.22.version" / "tmp" / "residual").is_file()
    finally:
        restore_constants(old)
        shutil.rmtree(root)


check("scratch mutation after one version call rejects with a bounded private residual", scratch_mutation_rejects)


def copied_source_post_mutation_rejects() -> bool:
    root, profile = fixture("copied-source-post-mutation")
    old_constants = fake_constants()
    old_capture = MODULE.version._capture
    try:
        profile = bound_test_profile(profile)
        def mutate(process: object, deadline: float, controller: object) -> tuple[bytes, bytes]:
            output = old_capture(process, deadline, controller)
            copied = Path(profile.bootstrap_root) / "agy.source"
            before = copied.read_bytes()
            copied.write_bytes(before[:-1] + (b"x" if before[-1:] != b"x" else b"y"))
            copied.chmod(0o755)
            return output
        MODULE.version._capture = mutate
        residual = Path(profile.bootstrap_root)
        return rejects(lambda: MODULE.run_initial_bootstrap(profile)) and residual.is_dir() and (residual / "agy.source").is_file()
    finally:
        MODULE.version._capture = old_capture
        restore_constants(old_constants)
        shutil.rmtree(root)


check("post-child copied-source byte drift rejects without deleting the changed inode", copied_source_post_mutation_rejects)


def extra_root_entry_rejects() -> bool:
    root, profile = fixture("extra-root-entry")
    old_constants = fake_constants()
    old_capture = MODULE.version._capture
    try:
        profile = bound_test_profile(profile)
        def mutate(process: object, deadline: float, controller: object) -> tuple[bytes, bytes]:
            output = old_capture(process, deadline, controller)
            extra = Path(profile.bootstrap_root) / "foreign-extra"
            extra.write_bytes(b"foreign")
            extra.chmod(0o600)
            return output
        MODULE.version._capture = mutate
        residual = Path(profile.bootstrap_root)
        return rejects(lambda: MODULE.run_initial_bootstrap(profile)) and (residual / "foreign-extra").read_bytes() == b"foreign"
    finally:
        MODULE.version._capture = old_capture
        restore_constants(old_constants)
        shutil.rmtree(root)


check("foreign direct root entry rejects and remains a residual", extra_root_entry_rejects)


def current_source_path_replacement_rejects() -> bool:
    root, profile = fixture("current-source-path-replacement")
    old_constants = fake_constants()
    old_capture = MODULE.version._capture
    try:
        profile = bound_test_profile(profile)
        def mutate(process: object, deadline: float, controller: object) -> tuple[bytes, bytes]:
            output = old_capture(process, deadline, controller)
            current = Path(profile.source_path)
            replacement = current.with_name("replacement")
            replacement.write_bytes(current.read_bytes())
            replacement.chmod(0o755)
            os.replace(str(replacement), str(current))
            return output
        MODULE.version._capture = mutate
        return rejects(lambda: MODULE.run_initial_bootstrap(profile)) and not Path(profile.bootstrap_root).exists()
    finally:
        MODULE.version._capture = old_capture
        restore_constants(old_constants)
        shutil.rmtree(root)


check("post-child current source pathname replacement rejects through held-parent reopen", current_source_path_replacement_rejects)


def foreign_staging_replacement_survives() -> bool:
    root = private(TMP / "foreign-staging")
    ledger = MODULE.Ledger.create(str(root), "fresh")
    original_write = MODULE.os.write
    injected = [False]
    try:
        def replace_after_write(fd: int, data: object) -> int:
            written = original_write(fd, data)
            if not injected[0]:
                for candidate in (root / "fresh").iterdir():
                    if candidate.name.startswith("."):
                        os.unlink(str(candidate))
                        candidate.write_bytes(b"foreign")
                        candidate.chmod(0o600)
                        injected[0] = True
                        break
            return written
        MODULE.os.write = replace_after_write
        rejected = rejects(lambda: MODULE._publish(ledger, (), "final", b"owned", MODULE.SignalController(())))
        foreign = [item for item in (root / "fresh").iterdir() if item.name.startswith(".") and item.read_bytes() == b"foreign"]
        return rejected and injected[0] and len(foreign) == 1
    finally:
        MODULE.os.write = original_write
        ledger.close()
        shutil.rmtree(root)


check("foreign staging replacement is not unlinked by publication cleanup", foreign_staging_replacement_survives)


def termination_uncertainty_preserves_root() -> bool:
    root, profile = fixture("termination-uncertainty")
    old_constants = fake_constants()
    old_capture, old_terminate = MODULE.version._capture, MODULE.version._terminate_group
    try:
        profile = bound_test_profile(profile)
        def fail_capture(process: object, deadline: float, controller: object) -> tuple[bytes, bytes]:
            raise MODULE.InitialBootstrapError("synthetic capture failure")
        def fail_terminate(process: object, calls: object) -> None:
            raise MODULE.InitialBootstrapError("synthetic close uncertainty")
        MODULE.version._capture, MODULE.version._terminate_group = fail_capture, fail_terminate
        return rejects(lambda: MODULE.run_initial_bootstrap(profile)) and Path(profile.bootstrap_root).is_dir()
    finally:
        MODULE.version._capture, MODULE.version._terminate_group = old_capture, old_terminate
        restore_constants(old_constants)
        shutil.rmtree(root)


check("process-group closure uncertainty rejects and preserves the private root", termination_uncertainty_preserves_root)


def root_fsync_failure_leaves_bounded_residual() -> bool:
    root = private(TMP / "root-fsync-failure")
    original_fsync = MODULE.os.fsync
    try:
        def fail_once(fd: int) -> None:
            raise OSError("synthetic fsync failure")
        MODULE.os.fsync = fail_once
        rejected = rejects(lambda: MODULE.Ledger.create(str(root), "fresh"))
        return rejected and (root / "fresh").is_dir()
    finally:
        MODULE.os.fsync = original_fsync
        shutil.rmtree(root)


check("root creation fsync failure rejects with a bounded residual", root_fsync_failure_leaves_bounded_residual)


def ignored_signal_not_owned() -> bool:
    original = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        state = MODULE._acquire_lifecycle()
        try:
            return signal.SIGTERM not in state.controller.owned
        finally:
            MODULE._restore(state)
    finally:
        signal.signal(signal.SIGTERM, original)


check("inherited ignored lifecycle signal remains caller-owned", ignored_signal_not_owned)


def priority_is_not_chronology() -> bool:
    controller = MODULE.SignalController((signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
    controller.latch(signal.SIGTERM)
    controller.latch(signal.SIGHUP)
    return controller.choose() == signal.SIGHUP


check("latched signals choose fixed HUP priority", priority_is_not_chronology)


def invalid_cli_is_process_owned() -> bool:
    result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-B", str(MODULE_PATH), "--wrong"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return result.returncode == 64 and result.stdout == b"" and result.stderr == b"version initial bootstrap: invalid invocation\n"


check("invalid CLI emits one usage exit before stdin/filesystem authority", invalid_cli_is_process_owned)


def wrong_flags_reject_before_stdin() -> bool:
    result = subprocess.run(["/usr/bin/python3", str(LEGACY_MODULE_PATH), "--prepare-capture-version-evidence"], input=encoded, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return result.returncode == 2 and result.stdout == b"" and result.stderr == b"version initial bootstrap: rejected\n"


check("wrong interpreter flags reject before profile mutation", wrong_flags_reject_before_stdin)


shutil.rmtree(TMP)
print("version initial bootstrap runner offline tests: %d passed, %d failed" % (passed, failed))
raise SystemExit(0 if failed == 0 else 1)
