#!/usr/bin/env python3
"""Focused positive and adversarial checks for CI timing observation."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci_timing.py"
SPEC = importlib.util.spec_from_file_location("ci_timing_tested", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

passed = 0
failed = 0


def check(label: str, test: Callable[[], bool] | bool) -> None:
    global passed, failed
    try:
        result = test() if callable(test) else test
    except Exception as exc:
        print(f"  EXC  {label}: {exc}")
        result = False
    if result:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


def valid_report(outcome: str = "gate-passed", failed_index: int = 2) -> dict[str, Any]:
    suites = []
    for index, (stage_id, _) in enumerate(MODULE.STAGES):
        if outcome == "gate-passed" or index < failed_index:
            status_value, duration = "passed", 0.25
        elif index == failed_index:
            status_value, duration = "failed", 0.5
        else:
            status_value, duration = "not-run", None
        suites.append({"id": stage_id, "status": status_value, "duration_seconds": duration})
    return {
        "schema_version": 1,
        "kind": "agy-worker-ci-timing-report",
        "head_sha": "a" * 40,
        "inventory_sha256": MODULE.inventory_digest(),
        "gate_outcome": outcome,
        "total_duration_seconds": 12.5,
        "suites": suites,
        "integrity": {
            "signed": False,
            "tamper_evident": False,
            "statement": MODULE.INTEGRITY_STATEMENT,
        },
    }


def rejected(report: dict[str, Any]) -> bool:
    try:
        MODULE.validate_report(report, MODULE.inventory_digest())
    except MODULE.TimingError:
        return True
    return False


check("inventory has 43 ordered unique stage IDs", len(MODULE.STAGES) == 43 and len({x[0] for x in MODULE.STAGES}) == 43)
check("inventory digest is lowercase SHA-256", MODULE.SHA256_RE.fullmatch(MODULE.inventory_digest()) is not None)
check("canonical shell announcement inventory matches observer", lambda: (MODULE.validate_gate_inventory(ROOT / "scripts" / "ci-offline.sh") or True))

passed_report = valid_report()
failed_report = valid_report("failed", 3)
check("complete passed report validates", lambda: (MODULE.validate_report(passed_report, MODULE.inventory_digest()) or True))
check("single-failure fail-fast report validates", lambda: (MODULE.validate_report(failed_report, MODULE.inventory_digest()) or True))

for label, mutate in (
    ("extra root field is rejected", lambda r: r.__setitem__("path", "/private/example")),
    ("wrong schema is rejected", lambda r: r.__setitem__("schema_version", 2)),
    ("malformed HEAD is rejected", lambda r: r.__setitem__("head_sha", "A" * 40)),
    ("wrong inventory digest is rejected", lambda r: r.__setitem__("inventory_sha256", "b" * 64)),
    ("reordered stage is rejected", lambda r: r["suites"].__setitem__(slice(0, 2), list(reversed(r["suites"][:2])))),
    ("missing stage is rejected", lambda r: r["suites"].pop()),
    ("passed outcome with not-run stage is rejected", lambda r: r["suites"][-1].update(status="not-run", duration_seconds=None)),
    ("failed outcome without failure is rejected", lambda r: r.__setitem__("gate_outcome", "failed")),
    ("second failure is rejected", lambda r: r["suites"][4].update(status="failed", duration_seconds=0.1)),
    ("passed stage after failure is rejected", lambda r: r["suites"][4].update(status="passed", duration_seconds=0.1)),
    ("not-run duration is rejected", lambda r: r["suites"][4].update(duration_seconds=0.0)),
):
    use_failed = label in {
        "second failure is rejected",
        "passed stage after failure is rejected",
        "not-run duration is rejected",
    }
    candidate = copy.deepcopy(failed_report if use_failed else passed_report)
    mutate(candidate)
    check(label, rejected(candidate))

for value in (-1.0, True, float("nan"), float("inf"), "1"):
    candidate = copy.deepcopy(passed_report)
    candidate["suites"][0]["duration_seconds"] = value
    check(f"invalid suite duration {value!r} is rejected", rejected(candidate))

candidate = copy.deepcopy(passed_report)
candidate["total_duration_seconds"] = float("nan")
check("non-finite total duration is rejected", rejected(candidate))


def _inventory_mutation_rejected() -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        target = Path(temp_dir) / "gate.sh"
        text = (ROOT / "scripts" / "ci-offline.sh").read_text(encoding="utf-8")
        target.write_text(text.replace("announce 'shell syntax'", "announce 'changed syntax'"), encoding="utf-8")
        try:
            MODULE.validate_gate_inventory(target)
        except MODULE.TimingError:
            return True
    return False


def _canonical_nan_rejected() -> bool:
    try:
        MODULE.canonical_bytes({"duration": float("nan")})
    except ValueError:
        return True
    return False


check("canonical JSON refuses NaN", _canonical_nan_rejected)
check("changed shell announcement inventory is rejected", _inventory_mutation_rejected)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


NONCE = "d" * 64


def observed(return_code: int, count: int, spoof_stdout: bool = False) -> tuple[int, dict[str, Any]]:
    lines: list[bytes] = []
    if spoof_stdout:
        lines.append(f"==> {MODULE.STAGES[1][1]}\n".encode())
    for _, announce in MODULE.STAGES[:count]:
        lines.append(f"==> {announce}\n".encode())
        lines.append(f"@@agy-worker-ci-timing:{NONCE}:{announce}\n".encode())
    raw = b"".join(lines)
    return MODULE.observe_stream(
        io.BytesIO(raw), lambda: return_code, "c" * 40, NONCE, FakeClock(), io.BytesIO()
    )


def _successful_observation() -> bool:
    return_code, report = observed(0, len(MODULE.STAGES))
    return return_code == 0 and report["gate_outcome"] == "gate-passed" and all(item["status"] == "passed" for item in report["suites"])


def _failed_observation() -> bool:
    return_code, report = observed(7, 3)
    statuses = [item["status"] for item in report["suites"]]
    return return_code == 7 and statuses[:4] == ["passed", "passed", "failed", "not-run"]


check("observer records every successful stage in canonical order", _successful_observation)
check("observer records one failed stage and remaining stages as not-run", _failed_observation)
check(
    "ordinary stdout cannot spoof a timing control boundary",
    lambda: observed(0, len(MODULE.STAGES), spoof_stdout=True)[1]["gate_outcome"] == "gate-passed",
)


def _duplicate_control_rejected() -> bool:
    announce = MODULE.STAGES[0][1]
    raw = (
        f"@@agy-worker-ci-timing:{NONCE}:{announce}\n"
        f"@@agy-worker-ci-timing:{NONCE}:{announce}\n"
    ).encode()
    try:
        MODULE.observe_stream(
            io.BytesIO(raw), lambda: 1, "c" * 40, NONCE, FakeClock(), io.BytesIO()
        )
    except MODULE.TimingError:
        return True
    return False


check("duplicate private control marker is rejected", _duplicate_control_rejected)


def _omitted_success_rejected() -> bool:
    try:
        observed(0, len(MODULE.STAGES) - 1)
    except MODULE.TimingError:
        return True
    return False


check("successful child that omits a stage is rejected", _omitted_success_rejected)


def _target_cases() -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        private = root / "private"
        private.mkdir(mode=0o700)
        os.chmod(private, 0o700)
        target = private / "timing.json"
        MODULE.publish_report(target, passed_report)
        if stat.S_IMODE(target.stat().st_mode) != 0o600:
            return False
        try:
            MODULE.publish_report(target, passed_report)
        except MODULE.TimingError:
            pass
        else:
            return False
        open_parent = root / "open"
        open_parent.mkdir(mode=0o755)
        try:
            MODULE.validate_publication_target(open_parent / "timing.json")
        except MODULE.TimingError:
            pass
        else:
            return False
        symlink = root / "linked"
        symlink.symlink_to(private)
        try:
            MODULE.validate_publication_target(symlink / "other.json")
        except MODULE.TimingError:
            return True
    return False


check("publication is 0600, no-overwrite, 0700-parent, and no-follow", _target_cases)


def _clean_head_cases() -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "CI Timing Test"], cwd=repo, check=True)
        tracked = repo / "tracked.txt"
        tracked.write_text("clean\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
        sha = MODULE.resolve_clean_head(repo)
        if not MODULE.COMMIT_RE.fullmatch(sha):
            return False
        tracked.write_text("dirty\n", encoding="utf-8")
        try:
            MODULE.revalidate_clean_head(repo, sha)
        except MODULE.TimingError:
            pass
        else:
            return False
        tracked.write_text("clean\n", encoding="utf-8")
        (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        try:
            MODULE.resolve_clean_head(repo)
        except MODULE.TimingError:
            return True
    return False


check("HEAD binding accepts clean Git and rejects dirty tracked/untracked bytes", _clean_head_cases)

invalid = subprocess.run(
    ["./scripts/ci-offline.sh", "--invalid"], cwd=ROOT,
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("shell wrapper rejects unknown arguments", invalid.returncode == 2 and b"rejected arguments" in invalid.stderr)
missing = subprocess.run(
    ["./scripts/ci-offline.sh", "--timing-report"], cwd=ROOT,
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("shell wrapper rejects a missing timing path", missing.returncode == 2 and b"rejected arguments" in missing.stderr)
empty = subprocess.run(
    ["./scripts/ci-offline.sh", "--timing-report="], cwd=ROOT,
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("shell wrapper rejects an empty timing path", empty.returncode == 2 and b"rejected arguments" in empty.stderr)

for forbidden in ("command", "environment", "logs", "credential", "provider", "account", "timestamp", "hostname", "cost", "path"):
    check(f"report schema excludes {forbidden}", forbidden not in json.dumps(passed_report, sort_keys=True).lower())

print()
print(f"PASSED: {passed} tests")
if failed:
    print(f"FAILED: {failed} tests")
    raise SystemExit(1)
