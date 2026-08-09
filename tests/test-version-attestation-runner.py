#!/usr/bin/env python3
"""Offline tests for the canonical fixed-profile version attestation runner."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "version_attestation_runner.py"
SPEC = importlib.util.spec_from_file_location("version_attestation_runner_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TMP = Path(tempfile.mkdtemp(prefix="agyworker-version-runner-tests.")).resolve()
os.chmod(TMP, 0o700)
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
        print(f"FAIL version attestation runner: {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (MODULE.AttestationError, OSError, subprocess.SubprocessError):
        return True
    return False


def profile_bytes(profile: object) -> bytes:
    return MODULE._canonical_json(dataclasses.asdict(profile))


def fresh_profile(label: str, executable: bytes = MODULE.FAKE_EXECUTABLE):
    root = TMP / label
    root.mkdir(mode=0o700)
    return root, MODULE._offline_fixture(root, executable)


def cleanup_result(root: Path, result: dict[str, object] | None) -> None:
    if result is not None:
        artifact = Path(str(result.get("artifact_root", "")))
        if artifact.is_dir() and artifact.parent == root:
            shutil.rmtree(artifact)
    if root.exists():
        shutil.rmtree(root)


source = MODULE_PATH.read_bytes()
contract = MODULE.validate_source_contract(source)
check("module imports with bytecode disabled", lambda: sys.dont_write_bytecode)
check("fixed version contract is 1.1.11", lambda: MODULE.EXPECTED_VERSION == "1.1.11" and MODULE.EXPECTED_STDOUT == b"1.1.11\n")
check("fixed bounds are three seconds and 128 bytes", lambda: MODULE.WALL_SECONDS == 3.0 and MODULE.STREAM_LIMIT == 128)
check("canonical source contract is accepted", lambda: contract["status"] == "accepted")
check("canonical source digest is exact", lambda: contract["sha256"] == hashlib.sha256(source).hexdigest())
check("runner source has no model or effort command", lambda: not re.search(rb"--(?:model|effort)|\bmodels\b|/effort", source))
check("runner source imports no network client", lambda: not re.search(rb"\b(?:socket|urllib|requests|httpx)\b", source))
check("runner has exactly one production Popen call", lambda: source.count(b"calls.popen(") == 1)
check("runner binds executable to snapshot", lambda: b"executable=profile.snapshot_path" in source)
check("runner logical argv is version-only", lambda: b'argv = [profile.source_path, "--version"]' in source)

mutated = source.replace(b"                executable=profile.snapshot_path,\n", b"", 1)
check("source validator rejects executable override removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            process = calls.popen(\n", b"            calls.popen(argv)\n            process = calls.popen(\n", 1)
check("source validator rejects an extra Popen", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'argv = [profile.source_path, "--version"]', b'argv = [profile.source_path, "--help"]', 1)
check("source validator rejects logical argv drift", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            signal.signal(item, signal.SIG_IGN)\n", b"", 1)
check("source validator rejects terminal signal disarm removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)\n",
    b"    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])\n",
    1,
)
check("source validator rejects early lifecycle masking removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)\n",
    b"        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])\n",
    1,
)
check("source validator rejects publication signal masking removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            exit_code = _close_reserved_group(process, calls)\n", b"            exit_code = process.wait()\n", 1)
check("source validator rejects pre-reap group closure removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        return process.wait(timeout=0.75)\n",
    b"        result = process.wait(timeout=0.75)\n        calls.killpg(pgid, 0)\n        return result\n",
    1,
)
check("source validator rejects any post-reap group probe", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"    if not _production_startup_isolated():\n", b"    if False:\n", 1)
check("source validator rejects isolated startup enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
check("source hash changes under a one-byte drift", lambda: hashlib.sha256(source + b"\n").hexdigest() != contract["sha256"])

profile_root, profile = fresh_profile("profile")
encoded = profile_bytes(profile)
parsed = MODULE.AttestationProfile.from_bytes(encoded)
check("strict profile round-trips exact fields", lambda: parsed == profile)
value = json.loads(encoded)
value["extra"] = True
check("profile rejects extra fields", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
value = json.loads(encoded)
value.pop("snapshot_path")
check("profile rejects missing fields", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
duplicate = encoded[:-2] + b',"source_path":"/tmp/other"}\n'
check("profile rejects duplicate fields", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(duplicate)))
value = json.loads(encoded)
value["source_sha256"] = value["source_sha256"].upper()
check("profile rejects noncanonical SHA", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
value = json.loads(encoded)
value["source_path"] = "relative/agy"
check("profile rejects relative paths", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
symlink = profile_root / "source-link"
symlink.symlink_to(Path(profile.source_path))
value = json.loads(encoded)
value["source_path"] = str(symlink)
check("profile rejects symlink path aliases", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
value = json.loads(encoded)
value["source_identity"]["size"] = True
check("profile rejects boolean identity integers", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))

unrelated_snapshot = dataclasses.replace(profile, snapshot_path=str(profile_root / "unrelated.snapshot"))
check("authority rejects a snapshot outside the prior root", lambda: rejects(lambda: MODULE._validate_profile_authority(unrelated_snapshot)))
repo_root_profile = dataclasses.replace(
    profile,
    temp_parent=str(ROOT),
    prior_root=str(ROOT / "agy-version-attestation.synthetic"),
    snapshot_path=str(ROOT / "agy-version-attestation.synthetic" / "agy.snapshot"),
)
check("authority rejects an evidence parent inside the repository", lambda: rejects(lambda: MODULE._validate_profile_authority(repo_root_profile)))
unsafe_source = dataclasses.replace(profile.source_identity, mode=0o700)
check("authority rejects a caller-attested unsafe source mode", lambda: rejects(lambda: MODULE._validate_profile_authority(dataclasses.replace(profile, source_identity=unsafe_source))))
unsafe_snapshot = dataclasses.replace(profile.snapshot_identity, nlink=2)
check("authority rejects a caller-attested linked snapshot", lambda: rejects(lambda: MODULE._validate_profile_authority(dataclasses.replace(profile, snapshot_identity=unsafe_snapshot))))
swapped_source = profile_root / "agy-copy"
swapped_source.write_bytes(Path(profile.source_path).read_bytes())
swapped_source.chmod(0o755)
source_swap_profile = dataclasses.replace(
    profile,
    source_path=str(swapped_source),
    source_identity=MODULE._identity(swapped_source),
)
check("prior binding rejects a source path and identity swap", lambda: rejects(lambda: MODULE._validate_prior(source_swap_profile)))
snapshot_swap_identity = dataclasses.replace(profile.snapshot_identity, ino=profile.snapshot_identity.ino + 1)
check("prior binding rejects a snapshot identity swap", lambda: rejects(lambda: MODULE._validate_prior(dataclasses.replace(profile, snapshot_identity=snapshot_swap_identity))))
check("prior binding rejects a reviewed SHA swap", lambda: rejects(lambda: MODULE._validate_prior(dataclasses.replace(profile, source_sha256="0" * 64))))

directory_source = profile_root / "directory-agy"
directory_source.mkdir(mode=0o755)
check(
    "attested executable must be a regular file",
    lambda: rejects(
        lambda: MODULE._open_attested(
            str(directory_source), MODULE._identity(directory_source), "0" * 64, 0o755
        )
    ),
)
cleanup_result(profile_root, None)


def invalid_cli() -> bool:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = MODULE.main([])
    return code == 64 and stdout.getvalue() == "" and stderr.getvalue() == "version attestation runner: invalid invocation\n"


check("invalid CLI is sanitized", invalid_cli)
check("CLI exposes only self-test and attest-version", lambda: b'["--self-test"]' in source and b'["--attest-version"]' in source)


def nonisolated_production_rejected() -> bool:
    result = subprocess.run(
        ["/usr/bin/python3", str(MODULE_PATH), "--attest-version"],
        input=b"{}\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return (
        result.returncode == 64
        and result.stdout == b""
        and result.stderr == b"version attestation runner: isolated startup required\n"
    )


check("production CLI rejects a nonisolated interpreter", nonisolated_production_rejected)


def isolated_startup_ignores_python_hooks() -> bool:
    hooks = TMP / "startup-hooks"
    hooks.mkdir(mode=0o700)
    marker = TMP / "startup.marker"
    (hooks / "sitecustomize.py").write_text(
        f"open({str(marker)!r}, 'wb').write(b'hook\\n')\n", encoding="utf-8"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(hooks)
    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-S", "-B", str(MODULE_PATH), "--attest-version"],
        input=b"{}\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    return (
        result.returncode == 2
        and result.stdout == b""
        and result.stderr == b"version attestation runner: rejected\n"
        and not marker.exists()
    )


check("production CLI requires isolated no-site no-bytecode startup", isolated_startup_ignores_python_hooks)


def self_test_accepts() -> bool:
    result = MODULE.run_offline_self_test()
    return result == {
        "call_count": 1,
        "claim": "synthetic-version-attestation",
        "schema_version": 1,
        "status": "accepted",
    }


check("offline self-test uses the production function", self_test_accepts)


def exact_popen_contract() -> bool:
    root, candidate = fresh_profile("popen-contract")
    records: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def observed(*args, **kwargs):
        records.append((args, kwargs.copy()))
        return subprocess.Popen(*args, **kwargs)

    result = None
    try:
        result = MODULE.run_attestation(candidate, calls=MODULE.RunnerCalls(popen=observed), module_source=source)
        args, kwargs = records[0]
        return (
            len(records) == 1
            and args == ([candidate.source_path, "--version"],)
            and kwargs.get("executable") == candidate.snapshot_path
            and kwargs.get("stdin") is subprocess.DEVNULL
            and kwargs.get("stdout") is subprocess.PIPE
            and kwargs.get("stderr") is subprocess.PIPE
            and kwargs.get("start_new_session") is True
            and result["call_count"] == 1
        )
    finally:
        cleanup_result(root, result)


check("production function sends one exact snapshot-backed Popen", exact_popen_contract)


def result_rejected(label: str, executable: bytes) -> bool:
    root, candidate = fresh_profile(label, executable)
    try:
        return rejects(lambda: MODULE.run_attestation(candidate, module_source=source))
    finally:
        cleanup_result(root, None)


check("wrong stdout is rejected", lambda: result_rejected("wrong-stdout", b"#!/usr/bin/python3\nprint('wrong')\n"))
check("nonempty stderr is rejected", lambda: result_rejected("stderr", b"#!/usr/bin/python3\nimport sys\nsys.stderr.write('error')\nsys.stdout.write('1.1.11\\n')\n"))
check("stdout overflow is rejected", lambda: result_rejected("overflow", b"#!/usr/bin/python3\nprint('x'*129)\n"))


def timeout_closes_process_group() -> bool:
    script = b"#!/usr/bin/python3\nimport os,signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\npid=os.fork()\nif pid==0:\n time.sleep(3.4)\n open('../late.marker','wb').write(b'late\\n')\n time.sleep(5)\ntime.sleep(9)\n"
    root, candidate = fresh_profile("timeout", script)
    try:
        rejected = rejects(lambda: MODULE.run_attestation(candidate, module_source=source))
        time.sleep(0.5)
        return rejected and not any(root.glob("agy-version-recovery.*/late.marker"))
    finally:
        cleanup_result(root, None)


check("timeout kills the whole process group with no late side effect", timeout_closes_process_group)


def close_pipe_descendant_is_reaped() -> bool:
    script = b"#!/usr/bin/python3\nimport os,signal,sys,time\npid=os.fork()\nif pid==0:\n os.close(1);os.close(2);signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(.8);open('../late.marker','wb').write(b'late\\n');os._exit(0)\nsys.stdout.write('1.1.11\\n')\n"
    root, candidate = fresh_profile("close-pipe-descendant", script)
    result = None
    try:
        result = MODULE.run_attestation(candidate, module_source=source)
        time.sleep(0.9)
        return result["status"] == "accepted" and not any(
            root.glob("agy-version-recovery.*/late.marker")
        )
    finally:
        cleanup_result(root, result)


check("reserved group cleanup kills a pipe-closing TERM-ignoring descendant", close_pipe_descendant_is_reaped)


def signal_after_reap_never_reenters_group_cleanup(signum: int) -> bool:
    root, candidate = fresh_profile(f"post-reap-signal-{signum}")
    calls: list[tuple[int, int, int | None]] = []
    observed: list[subprocess.Popen[bytes]] = []

    def observed_popen(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        observed.append(process)
        return process

    def signaling_killpg(group: int, sent: int) -> None:
        calls.append((group, sent, observed[0].returncode))
        try:
            os.killpg(group, sent)
        finally:
            if sent == signal.SIGKILL:
                os.kill(os.getpid(), signum)

    try:
        try:
            MODULE.run_attestation(
                candidate,
                calls=MODULE.RunnerCalls(
                    popen=observed_popen, killpg=signaling_killpg
                ),
                module_source=source,
            )
        except SystemExit as exc:
            outcome = (
                exc.code == 128 + signum
                and [(sent, returncode) for _group, sent, returncode in calls]
                == [(signal.SIGTERM, None), (signal.SIGKILL, None)]
                and not any(root.glob("agy-version-recovery.*/version.binding.sha256"))
            )
            return outcome
        return False
    finally:
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"post-reap signal {lifecycle_signal} never reuses group cleanup authority",
        lambda lifecycle_signal=lifecycle_signal: signal_after_reap_never_reenters_group_cleanup(
            lifecycle_signal
        ),
    )


def changed_mode_rejected() -> bool:
    root, candidate = fresh_profile("mode-drift")
    Path(candidate.snapshot_path).chmod(0o700)
    try:
        return rejects(lambda: MODULE.run_attestation(candidate, module_source=source))
    finally:
        cleanup_result(root, None)


check("snapshot mode drift is rejected before Popen", changed_mode_rejected)


def source_drift_rejected() -> bool:
    root, candidate = fresh_profile("source-drift")
    Path(candidate.source_path).write_bytes(b"changed\n")
    try:
        return rejects(lambda: MODULE.run_attestation(candidate, module_source=source))
    finally:
        cleanup_result(root, None)


check("source byte drift is rejected before Popen", source_drift_rejected)


def prior_claim_rejected() -> bool:
    root, candidate = fresh_profile("prior-claim")
    binding = Path(candidate.prior_root) / "version.binding.json"
    binding.write_bytes(MODULE._canonical_json({"claim": "wrong", "inventory": {"executable_version_bound": False}}))
    digest = hashlib.sha256(binding.read_bytes()).hexdigest()
    (Path(candidate.prior_root) / "version.binding.sha256").write_bytes((digest + "\n").encode("ascii"))
    mutated = dataclasses.replace(candidate, prior_binding_sha256=digest)
    try:
        return rejects(lambda: MODULE.run_attestation(mutated, module_source=source))
    finally:
        cleanup_result(root, None)


check("incompatible prior claim is rejected", prior_claim_rejected)


def fsync_sequence() -> bool:
    root, candidate = fresh_profile("fsync-sequence")
    real = os.fsync
    completed: list[tuple[str, int]] = []

    def traced(descriptor: int) -> None:
        value = os.fstat(descriptor)
        real(descriptor)
        completed.append(("dir" if stat.S_ISDIR(value.st_mode) else "file", descriptor))

    result = None
    try:
        result = MODULE.run_attestation(candidate, calls=MODULE.RunnerCalls(fsync=traced), module_source=source)
        roles = [role for role, _descriptor in completed]
        return roles.count("file") >= 6 and roles.count("dir") >= 12 and roles[0] == "file"
    finally:
        cleanup_result(root, result)


check("runner completes actual staged-file and parent fsync calls", fsync_sequence)


def publisher_fsync_authority(category: str, suppress: bool) -> bool:
    root = TMP / f"publisher-fsync-{category}-{'weak' if suppress else 'secure'}"
    root.mkdir(mode=0o700)
    real = os.fsync
    completed: list[tuple[str, int]] = []
    ordinals = {"file": 0, "dir": 0}
    target = {
        "staged-file": ("file", 1),
        "post-link-parent": ("dir", 1),
        "post-temp-parent": ("dir", 2),
        "rollback-parent": ("dir", 1),
        "failure-cleanup-parent": ("dir", 1),
    }[category]

    def traced(descriptor: int) -> None:
        value = os.fstat(descriptor)
        role = "dir" if stat.S_ISDIR(value.st_mode) else "file"
        ordinals[role] += 1
        if suppress and (role, ordinals[role]) == target:
            return
        real(descriptor)
        completed.append((role, descriptor))

    publisher = MODULE.Publisher(root, MODULE.RunnerCalls(fsync=traced))
    try:
        if category in {"staged-file", "post-link-parent", "post-temp-parent"}:
            publisher.publish("binding.json", b"binding\n")
            expected = [("file", completed[0][1] if completed and completed[0][0] == "file" else -1), ("dir", publisher.root_fd), ("dir", publisher.root_fd)]
            accepted = completed == expected
            publisher.rollback()
        elif category == "rollback-parent":
            publisher.calls = MODULE.REAL_CALLS
            publisher.publish("binding.json", b"binding\n")
            publisher.calls = MODULE.RunnerCalls(fsync=traced)
            completed.clear()
            ordinals.update(file=0, dir=0)
            publisher.rollback()
            accepted = completed == [("dir", publisher.root_fd)]
            real(publisher.root_fd)
        else:
            sentinel = root / "binding.json"
            sentinel.write_bytes(b"existing\n")
            sentinel.chmod(0o600)
            rejected = rejects(lambda: publisher.publish("binding.json", b"replacement\n"))
            expected = [("file", completed[0][1] if completed and completed[0][0] == "file" else -1), ("dir", publisher.root_fd)]
            accepted = rejected and completed == expected
            sentinel.unlink()
            real(publisher.root_fd)
        return accepted if not suppress else not accepted
    finally:
        try:
            publisher.rollback()
        except BaseException:
            pass
        publisher.close()
        shutil.rmtree(root)


for fsync_category in (
    "staged-file",
    "post-link-parent",
    "post-temp-parent",
    "rollback-parent",
    "failure-cleanup-parent",
):
    check(
        f"{fsync_category} completes the real fsync syscall",
        lambda fsync_category=fsync_category: publisher_fsync_authority(fsync_category, False),
    )
    check(
        f"{fsync_category} syscall omission mutation is killed",
        lambda fsync_category=fsync_category: publisher_fsync_authority(fsync_category, True),
    )


def interrupted_during_preflight(signum: int) -> bool:
    root, candidate = fresh_profile(f"preflight-signal-{signum}")
    original = MODULE._validate_prior

    def signaling(profile) -> None:
        os.kill(os.getpid(), signum)
        original(profile)

    MODULE._validate_prior = signaling
    try:
        try:
            MODULE.run_attestation(candidate, module_source=source)
        except SystemExit as exc:
            return exc.code == 128 + signum and not any(
                root.glob("agy-version-recovery.*")
            )
        return False
    finally:
        MODULE._validate_prior = original
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"preflight signal {lifecycle_signal} exits exactly before root creation",
        lambda lifecycle_signal=lifecycle_signal: interrupted_during_preflight(
            lifecycle_signal
        ),
    )


def double_signal_during_intermediate_publication(first: int) -> bool:
    second = signal.SIGTERM if first != signal.SIGTERM else signal.SIGHUP
    root, candidate = fresh_profile(f"publication-signal-{first}-{second}")
    real = os.fsync
    file_calls = 0
    directory_calls = 0

    def signaling_fsync(descriptor: int) -> None:
        nonlocal file_calls, directory_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_calls += 1
            if directory_calls == 3:
                os.kill(os.getpid(), second)
        else:
            file_calls += 1
            if file_calls == 1:
                os.kill(os.getpid(), first)
        real(descriptor)

    try:
        try:
            MODULE.run_attestation(
                candidate,
                calls=MODULE.RunnerCalls(fsync=signaling_fsync),
                module_source=source,
            )
        except SystemExit as exc:
            artifact_roots = list(root.glob("agy-version-recovery.*"))
            expected_dirs = {"cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"}
            return (
                exc.code == 128 + first
                and file_calls >= 1
                and directory_calls >= 3
                and len(artifact_roots) == 1
                and {item.name for item in artifact_roots[0].iterdir()} == expected_dirs
                and all(not any((artifact_roots[0] / name).iterdir()) for name in expected_dirs)
            )
        return False
    finally:
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"distinct double signal beginning {lifecycle_signal} preserves the first during publication cleanup",
        lambda lifecycle_signal=lifecycle_signal: double_signal_during_intermediate_publication(
            lifecycle_signal
        ),
    )


def interrupted_during_popen(signum: int) -> bool:
    root, candidate = fresh_profile(f"signal-{signum}")

    def signaling(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        os.kill(os.getpid(), signum)
        return process

    try:
        try:
            MODULE.run_attestation(candidate, calls=MODULE.RunnerCalls(popen=signaling), module_source=source)
        except SystemExit as exc:
            return exc.code == 128 + signum and not any(root.glob("agy-version-recovery.*/version.binding.sha256"))
        return False
    finally:
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"signal {lifecycle_signal} exits exactly and publishes no completion marker",
        lambda lifecycle_signal=lifecycle_signal: interrupted_during_popen(lifecycle_signal),
    )


def double_signal_during_completion(signum: int) -> bool:
    root, candidate = fresh_profile(f"double-signal-{signum}")
    real = os.fsync
    directory_calls = 0

    def signaling_fsync(descriptor: int) -> None:
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_calls += 1
            if directory_calls in (13, 15):
                os.kill(os.getpid(), signum)
        real(descriptor)

    try:
        try:
            MODULE.run_attestation(
                candidate,
                calls=MODULE.RunnerCalls(fsync=signaling_fsync),
                module_source=source,
            )
        except SystemExit as exc:
            return (
                exc.code == 128 + signum
                and directory_calls >= 15
                and not any(root.glob("agy-version-recovery.*/version.binding.sha256"))
            )
        return False
    finally:
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"double signal {lifecycle_signal} during marker rollback preserves exact exit",
        lambda lifecycle_signal=lifecycle_signal: double_signal_during_completion(lifecycle_signal),
    )

shutil.rmtree(TMP)
print(f"version attestation runner offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
