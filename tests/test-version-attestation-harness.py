#!/usr/bin/env python3
"""Offline paired tests for the persistent version-attestation mutation harness."""

from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "version_attestation_harness.py"
SPEC = importlib.util.spec_from_file_location("version_attestation_harness_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TMP = Path(tempfile.mkdtemp(prefix="agyworker-version-harness-tests."))
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
        print(f"FAIL version attestation harness: {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except MODULE.HarnessError:
        return True
    return False


check("module imports without bytecode", lambda: sys.dont_write_bytecode)
check("secure policy enables every lifecycle control", lambda: all(dataclasses.astuple(MODULE.SECURE_POLICY)))


def frozen_policy() -> bool:
    try:
        MODULE.SECURE_POLICY.block_cleanup_signals = False
    except dataclasses.FrozenInstanceError:
        return True
    return False


check("mutation policy is immutable", frozen_policy)


def invalid_cli() -> bool:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = MODULE.main([])
    return code == 64 and stdout.getvalue() == "" and stderr.getvalue() == "version attestation harness: invalid invocation\n"


check("invalid CLI is sanitized and rejected", invalid_cli)

source = MODULE_PATH.read_text(encoding="utf-8")
check("CLI exposes only fixed self-test mode", lambda: 'argv != ["--self-test"]' in source)
check("no mutation is read from environment", lambda: "os.environ" not in source)
check("fake controller contains no agy invocation", lambda: "agy" not in MODULE.FAKE_CONTROLLER_SOURCE.lower())
check("fake controller imports no network module", lambda: not re.search(r"\b(?:socket|urllib|http|requests)\b", MODULE.FAKE_CONTROLLER_SOURCE))
check("fake controller explicitly unblocks lifecycle signals", lambda: "SIG_UNBLOCK" in MODULE.FAKE_CONTROLLER_SOURCE)
check("fake controller creates a descendant", lambda: "os.fork()" in MODULE.FAKE_CONTROLLER_SOURCE)
check("fake controller has a detectable late side effect", lambda: 'marker("late.marker"' in MODULE.FAKE_CONTROLLER_SOURCE)

runner_binding_cases = MODULE.run_canonical_runner_binding_cases()
check("canonical runner binding matrix has two paired cases", lambda: len(runner_binding_cases) == 2)
check("canonical runner source and production-path self-test are accepted", lambda: sum(item["status"] == "accepted" for item in runner_binding_cases) == 1)
check("canonical runner source drift mutation is killed", lambda: sum(item["status"] == "killed" for item in runner_binding_cases) == 1)

publisher_root = TMP / "publisher"
publisher_root.mkdir(mode=0o700)
publisher = MODULE.DurablePublisher(publisher_root)
check("publisher rejects path-bearing names", lambda: rejects(lambda: publisher.publish("../escape", b"x")))
digest = publisher.publish("evidence.json", b"evidence\n")
check("publisher returns the exact SHA-256", lambda: digest == "bdcf4c994585af6dd6cb1cfbff78bcc73ab27dc30a299db5bb83766ca05b5de4")
check("publisher creates mode 0600", lambda: stat.S_IMODE((publisher_root / "evidence.json").stat().st_mode) == 0o600)
check("publisher refuses overwrite", lambda: rejects(lambda: publisher.publish("evidence.json", b"other\n")))
publisher.rollback()
check("publisher rollback removes its exact inode", lambda: not (publisher_root / "evidence.json").exists())
check("publisher rollback removes temporary artifacts", lambda: not any(path.name.endswith(".tmp") for path in publisher_root.iterdir()))
publisher.close()

public_root = TMP / "not-private"
public_root.mkdir(mode=0o755)
check("publisher rejects a nonprivate parent", lambda: rejects(lambda: MODULE.DurablePublisher(public_root)))

supervisor_root = MODULE._private_case(TMP, "supervisor")
supervisor = MODULE.ControllerSupervisor()
supervisor.start(MODULE.fake_controller_argv(supervisor_root), supervisor_root)
pgid = supervisor.pgid
check("supervisor registers the exact PGID", lambda: pgid is not None and supervisor.registered and os.getpgid(pgid) == pgid)
check("controller handshake leaves no stderr", lambda: supervisor.process is not None and supervisor.process.stderr is not None and supervisor.process.stderr.closed)
supervisor.terminate()
check("supervisor clears active ownership after reap", lambda: not supervisor.registered and supervisor.pgid is None and supervisor.process is None)
check("supervisor proves the process group absent", lambda: pgid is not None and not MODULE._group_exists(pgid))
check("private case directories are mode 0700", lambda: stat.S_IMODE(supervisor_root.stat().st_mode) == 0o700)


def rejected_controller(label: str, code: str) -> bool:
    case = MODULE._private_case(TMP, f"rejected-controller-{label}")
    candidate = MODULE.ControllerSupervisor()
    rejected = False
    try:
        candidate.start([sys.executable, "-I", "-S", "-B", "-c", code], case)
    except MODULE.HarnessError:
        rejected = True
    return rejected and not candidate.registered and candidate.process is None and candidate.pgid is None


check("supervisor rejects a bounded handshake timeout", lambda: rejected_controller("timeout", "import time;time.sleep(2)"))
check("supervisor rejects malformed handshake semantics", lambda: rejected_controller("malformed", "import os,time;os.write(1,b'PGID 1\\nREADY\\n');time.sleep(2)"))
check("supervisor rejects handshake overflow", lambda: rejected_controller("overflow", "import os,time;os.write(1,b'x'*65);time.sleep(2)"))
check("supervisor rejects any handshake stderr", lambda: rejected_controller("stderr", "import os,time;os.write(2,b'error');time.sleep(2)"))

publication_root = MODULE._private_case(TMP, "publication-cases")
publication_cases = MODULE.run_publication_cases(publication_root)
check("publication matrix has twelve paired cases", lambda: len(publication_cases) == 12)
check("publication matrix accepts six secure cases", lambda: sum(item["status"] == "accepted" for item in publication_cases) == 6)
check("publication matrix kills six mutations", lambda: sum(item["status"] == "killed" for item in publication_cases) == 6)

fsync_root = MODULE._private_case(TMP, "fsync-authority-cases")
fsync_cases = MODULE.run_fsync_authority_cases(fsync_root)
check("actual fsync authority matrix has ten paired cases", lambda: len(fsync_cases) == 10)
check("actual fsync authority accepts five secure cases", lambda: sum(item["status"] == "accepted" for item in fsync_cases) == 5)
check("actual fsync authority kills five syscall omissions", lambda: sum(item["status"] == "killed" for item in fsync_cases) == 5)

signal_root = MODULE._private_case(TMP, "signal-cases")
signal_cases = MODULE.run_signal_cases(signal_root)
check("fork and group signal matrix has twelve cases", lambda: len(signal_cases) == 12)
check("fork and group signal matrix accepts six secure cases", lambda: sum(item["status"] == "accepted" for item in signal_cases) == 6)
check("fork and group signal matrix kills six mutations", lambda: sum(item["status"] == "killed" for item in signal_cases) == 6)
check("signal cleanup prevents every late marker", lambda: not any(signal_root.rglob("late.marker")))

cleanup_root = MODULE._private_case(TMP, "publisher-signal-cases")
cleanup_cases = MODULE.run_publisher_cleanup_signal_cases(cleanup_root)
check("unlink and fsync signal matrix has twelve cases", lambda: len(cleanup_cases) == 12)
check("unlink and fsync matrix accepts six secure cases", lambda: sum(item["status"] == "accepted" for item in cleanup_cases) == 6)
check("unlink and fsync matrix kills six mutations", lambda: sum(item["status"] == "killed" for item in cleanup_cases) == 6)

completion_root = MODULE._private_case(TMP, "completion-cases")
completion_cases = MODULE.run_completion_cases(completion_root)
check("completion matrix has six cases", lambda: len(completion_cases) == 6)
check("completion matrix accepts three secure cases", lambda: sum(item["status"] == "accepted" for item in completion_cases) == 3)
check("completion matrix kills three mutations", lambda: sum(item["status"] == "killed" for item in completion_cases) == 3)


def completion_failure_rolls_back_and_restores_mask(failure_point: str) -> bool:
    case = MODULE._private_case(TMP, f"direct-completion-failure-{failure_point}")
    publisher = MODULE.DurablePublisher(case)
    before = MODULE.signal.pthread_sigmask(MODULE.signal.SIG_BLOCK, [])

    def marker() -> None:
        publisher.publish("version.binding.sha256", b"digest\n")
        if failure_point == "marker":
            raise MODULE.HarnessError("synthetic marker failure")

    def disarm() -> None:
        if failure_point == "disarm":
            raise MODULE.HarnessError("synthetic disarm failure")

    try:
        MODULE.atomic_completion(
            marker,
            publisher.rollback,
            disarm,
        )
    except MODULE.HarnessError:
        after = MODULE.signal.pthread_sigmask(MODULE.signal.SIG_BLOCK, [])
        final_absent = not (case / "version.binding.sha256").exists()
        publisher.close()
        return after == before and final_absent
    publisher.close()
    return False


check("marker failure rolls back and restores the caller signal mask", lambda: completion_failure_rolls_back_and_restores_mask("marker"))
check("disarm failure rolls back and restores the caller signal mask", lambda: completion_failure_rolls_back_and_restores_mask("disarm"))

completion_failure_root = MODULE._private_case(TMP, "completion-failure-cases")
completion_failure_cases = MODULE.run_completion_failure_cases(completion_failure_root)
check("completion failure matrix has four paired cases", lambda: len(completion_failure_cases) == 4)
check("completion failure matrix accepts two secure cases", lambda: sum(item["status"] == "accepted" for item in completion_failure_cases) == 2)
check("completion failure matrix kills two mutations", lambda: sum(item["status"] == "killed" for item in completion_failure_cases) == 2)

result = MODULE.run_offline_harness()
check("full harness accepts exactly twenty-nine secure cases", lambda: result["secure"] == 29)
check("full harness kills exactly twenty-nine mutations", lambda: result["mutations_killed"] == 29)
check("full harness reports zero failures", lambda: result["failed"] == 0 and result["status"] == "accepted")
check("full result is path-free canonical JSON", lambda: "/" not in json.dumps(result, sort_keys=True, separators=(",", ":")))

shutil.rmtree(TMP)
print(f"version attestation harness offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
