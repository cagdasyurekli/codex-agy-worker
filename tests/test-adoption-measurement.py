#!/usr/bin/env python3
"""Offline acceptance and rejection tests for the local measurement ledger."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from unittest import mock
from typing import Callable

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "adoption_measurement.py"
SPEC = importlib.util.spec_from_file_location("adoption_measurement_tested", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SHA = "0123456789abcdef0123456789abcdef01234567"
passed = 0
failed = 0


def check(label: str, predicate: Callable[[], bool] | bool) -> None:
    global passed, failed
    try:
        result = predicate() if callable(predicate) else predicate
    except Exception:
        result = False
    if result:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


def bytecode_snapshot() -> tuple[tuple[str, str], ...]:
    cache = ROOT / "scripts" / "__pycache__"
    if not cache.exists():
        return ()
    return tuple(sorted((path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in cache.iterdir() if path.is_file()))


def run(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["/usr/bin/python3", "-I", "-S", "-B", str(SCRIPT), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)


def canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def url(kind: str = "actions/runs/123456789") -> str:
    return f"https://github.com/cagdasyurekli/codex-agy-worker/{kind}"


def rejected(result: subprocess.CompletedProcess[bytes]) -> bool:
    return result.returncode == 2 and result.stdout == b"" and result.stderr == b"adoption-measurement: invalid local measurement input\n"


def init(ledger: Path) -> subprocess.CompletedProcess[bytes]:
    return run("init", "--ledger", str(ledger))


def append(ledger: Path, metric: str, window: str, value: str, denominator: str, sample_size: str, *, on: str | None = None, evidence: str | None = None) -> subprocess.CompletedProcess[bytes]:
    command = ["append", "--ledger", str(ledger), "--window", window, "--metric", metric, "--value", value, "--denominator", denominator, "--sample-size", sample_size, "--repo-sha", SHA, "--evidence-url", evidence or url("issues/42")]
    if on is not None:
        command.extend(("--observed-on", on))
    return run(*command)


def append_watcher(ledger: Path, result: str = "unchanged", *, on: str | None = None) -> subprocess.CompletedProcess[bytes]:
    command = ["append-watcher", "--ledger", str(ledger), "--result", result, "--repo-sha", SHA, "--evidence-url", url()]
    if on is not None:
        command.extend(("--observed-on", on))
    return run(*command)


def record(metric: str, window: int, value: int, denominator: int, sample_size: int, day: str, identifier: str) -> dict[str, object]:
    return {"denominator": denominator, "evidence_url": url("commit/" + SHA), "metric": metric, "observation_id": identifier, "observed_on": day, "repo_sha": SHA, "sample_size": sample_size, "value": value, "window_days": window}


print("adoption measurement offline test suite")
print()

bytecode_before = bytecode_snapshot()
temporary_root: Path
with tempfile.TemporaryDirectory(prefix="agy-measurement-test.") as temporary:
    root = Path(temporary)
    temporary_root = root
    ledger = root / "ledger.jsonl"
    created = init(ledger)
    check("init writes the exact v2 ledger header", created.returncode == 0 and created.stdout == b"measurement ledger initialized\n" and created.stderr == b"" and ledger.read_bytes() == canonical({"kind": "adoption-measurement-ledger", "schema_version": 2}))
    info = os.stat(ledger)
    check("init creates a current-owner one-link 0600 regular ledger", stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
    check("init refuses to overwrite an existing ledger", rejected(init(ledger)))

    date = today()
    watcher = append_watcher(ledger)
    check("append-watcher stores a sanitized closed watcher outcome", watcher.returncode == 0 and watcher.stdout == b"measurement observation appended\n" and watcher.stderr == b"")
    values = [json.loads(line) for line in ledger.read_text(encoding="ascii").splitlines()][1:]
    check("watcher record maps result to a 30-day count with opaque identity", len(values) == 1 and values[0]["metric"] == "watcher_unchanged_count" and values[0]["window_days"] == 30 and values[0]["value"] == values[0]["denominator"] == values[0]["sample_size"] == 1 and re.fullmatch(r"[0-9a-f]{32}", values[0]["observation_id"]) is not None and set(values[0]) == {"denominator", "evidence_url", "metric", "observation_id", "observed_on", "repo_sha", "sample_size", "value", "window_days"})
    check("same metric window and UTC day duplicate is rejected", rejected(append_watcher(ledger, "unchanged", on=date)) and len(ledger.read_bytes().splitlines()) == 2)
    check("different watcher outcomes are separately counted", append_watcher(ledger, "drift-review", on=date).returncode == 0 and append_watcher(ledger, "evidence-unavailable", on=date).returncode == 0)

    concurrent = root / "concurrent.jsonl"
    init(concurrent)
    duplicate_command = ["/usr/bin/python3", "-I", "-S", "-B", str(SCRIPT), "append-watcher", "--ledger", str(concurrent), "--result", "unchanged", "--repo-sha", SHA, "--evidence-url", url(), "--observed-on", date]
    first = subprocess.Popen(duplicate_command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    second = subprocess.Popen(duplicate_command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first_output = first.communicate(timeout=4); second_output = second.communicate(timeout=4)
    check("concurrent duplicate appends serialize into one valid observation", sorted((first.returncode, second.returncode)) == [0, 2] and run("report", "--ledger", str(concurrent)).returncode == 0 and len(concurrent.read_bytes().splitlines()) == 2 and all(output in ((b"measurement observation appended\n", b""), (b"", b"adoption-measurement: invalid local measurement input\n")) for output in (first_output, second_output)))

    capacity = root / "capacity.jsonl"
    init(capacity)
    first_record = record("watcher_unchanged_count", 30, 1, 1, 1, date, "d" * 32)
    MODULE.append_record(capacity, first_record, dt.datetime.now(dt.timezone.utc).date())
    capacity_bytes = capacity.read_bytes()
    old_record_limit = MODULE.MAX_RECORDS
    MODULE.MAX_RECORDS = 1
    try:
        try:
            MODULE.append_record(capacity, record("watcher_drift_review_count", 30, 1, 1, 1, date, "e" * 32), dt.datetime.now(dt.timezone.utc).date())
        except MODULE.LedgerError:
            record_limited = True
        else:
            record_limited = False
    finally:
        MODULE.MAX_RECORDS = old_record_limit
    check("append rejects the next record at the exact record limit without mutation", record_limited and capacity.read_bytes() == capacity_bytes)

    second_record = record("watcher_drift_review_count", 30, 1, 1, 1, date, "f" * 32)
    old_byte_limit = MODULE.MAX_LEDGER_BYTES
    MODULE.MAX_LEDGER_BYTES = len(capacity_bytes) + len(MODULE.canonical(second_record)) - 1
    try:
        try:
            MODULE.append_record(capacity, second_record, dt.datetime.now(dt.timezone.utc).date())
        except MODULE.LedgerError:
            byte_limited = True
        else:
            byte_limited = False
    finally:
        MODULE.MAX_LEDGER_BYTES = old_byte_limit
    check("append rejects a record that would cross the byte limit without mutation", byte_limited and capacity.read_bytes() == capacity_bytes)

    with mock.patch.object(MODULE.os, "write", return_value=1):
        try:
            MODULE.append_record(capacity, second_record, dt.datetime.now(dt.timezone.utc).date())
        except MODULE.LedgerError:
            short_rejected = True
        else:
            short_rejected = False
    check("short append write restores the exact prior ledger bytes", short_rejected and capacity.read_bytes() == capacity_bytes)

    check("generic append accepts a median duration metric", append(ledger, "fresh_clone_to_proof_duration_seconds", "30", "18", "1", "1", on=date).returncode == 0)
    check("generic append accepts a partial ratio metric", append(ledger, "doctor_pre_dispatch_blocker_ratio", "30", "2", "10", "8", on=date).returncode == 0)
    check("generic append accepts a latest public-interest snapshot", append(ledger, "github_interest_snapshot", "30", "12", "1", "1", on=date, evidence=url("pull/7")).returncode == 0)
    check("generic records may bind an allowlisted public external GitHub evidence URL", append(ledger, "published_example_completeness_ratio", "30", "1", "1", "1", on=date, evidence="https://github.com/example/public-repo/actions/runs/987654321").returncode == 0)
    check("60-day count median ratio latest and zero-regression metrics are closed", all(result.returncode == 0 for result in (
        append(ledger, "compatibility_lead_time_seconds", "60", "120", "1", "1", on=date),
        append(ledger, "verified_external_receipt_proof_count", "60", "2", "1", "1", on=date),
        append(ledger, "bug_report_completeness_ratio", "60", "3", "4", "4", on=date),
        append(ledger, "referral_search_trend", "60", "5", "1", "1", on=date),
        append(ledger, "gate_external_action_regression_count", "60", "0", "1", "1", on=date),
    )))
    check("90-day audit conformance benchmark and persona metric family is closed", all(result.returncode == 0 for result in (
        append(ledger, "baseline_audit_count", "90", "1", "1", "1", on=date),
        append(ledger, "baseline_audit_ratio", "90", "1", "2", "2", on=date),
        append(ledger, "external_conformance_workflow_count", "90", "3", "1", "1", on=date),
        append(ledger, "conformance_result_ratio", "90", "4", "4", "4", on=date),
        append(ledger, "bound_benchmark_claim_count", "90", "1", "1", "1", on=date),
        append(ledger, "accepted_real_persona_count", "90", "0", "1", "1", on=date),
    )))

    report = run("report", "--ledger", str(ledger))
    text = report.stdout.decode("ascii")
    check("report has fixed 30 60 and 90 day templates", report.returncode == 0 and report.stderr == b"" and all(f"## {window}-day measurement report" in text for window in (30, 60, 90)))
    check("30-day template reports watcher counts median partial ratio and latest", "watcher_unchanged_count: sum 1" in text and "fresh_clone_to_proof_duration_seconds: median 18" in text and "doctor_pre_dispatch_blocker_ratio: 2/10; sample size 8; partial" in text and "github_interest_snapshot: latest 12" in text)
    check("60-day template reports only its closed operations metrics", "compatibility_lead_time_seconds: median 120" in text and "verified_external_receipt_proof_count: sum 2" in text and "bug_report_completeness_ratio: 3/4; sample size 4; complete" in text and "gate_external_action_regression_count: sum 0" in text)
    check("90-day template reports only its closed audit metrics", "baseline_audit_ratio: 1/2; sample size 2; complete" in text and "conformance_result_ratio: 4/4; sample size 4; complete" in text and "accepted_real_persona_count: sum 0" in text)
    empty = root / "empty.jsonl"
    init(empty)
    empty_report = run("report", "--ledger", str(empty)).stdout.decode("ascii")
    check("missing metrics and fixed privacy limitations remain explicit", "watcher_unchanged_count: missing" in empty_report and "provider usage, prompts, logs, accounts, host data, or user identifiers" in text)

    stale = record("watcher_unchanged_count", 30, 1, 1, 1, "2020-01-01", "a" * 32)
    stale["evidence_url"] = url()
    with ledger.open("ab") as handle:
        handle.write(canonical(stale))
    os.chmod(ledger, 0o600)
    stale_report = run("report", "--ledger", str(ledger)).stdout.decode("ascii")
    check("valid stale records age out instead of rejecting the accumulating ledger", "Expired observations ignored: 1" in stale_report and "watcher_unchanged_count: sum 1" in stale_report)
    boundary = root / "boundary.jsonl"
    init(boundary)
    today_date = dt.datetime.now(dt.timezone.utc).date()
    append_watcher(boundary, "unchanged", on=(today_date - dt.timedelta(days=29)).isoformat())
    append_watcher(boundary, "unchanged", on=(today_date - dt.timedelta(days=30)).isoformat())
    boundary_report = run("report", "--ledger", str(boundary)).stdout.decode("ascii")
    check("30-day report includes exactly today plus the prior 29 UTC dates", "Observations in window: 1" in boundary_report and "Expired observations ignored: 1" in boundary_report and "watcher_unchanged_count: sum 1" in boundary_report)
    check("mismatched metric horizon is rejected", rejected(append(ledger, "baseline_audit_count", "30", "1", "1", "1", on=date)))
    check("ratio numerator and sample-size violations are rejected", rejected(append(ledger, "doctor_pre_dispatch_blocker_ratio", "30", "9", "8", "8", on="2020-01-02")) and rejected(append(ledger, "doctor_pre_dispatch_blocker_ratio", "30", "2", "8", "9", on="2020-01-03")))
    check("scalar metric denominator sample-size drift is rejected", rejected(append(ledger, "github_interest_snapshot", "30", "1", "2", "1", on="2020-01-04")))
    check("invalid watcher result and nonallowlisted evidence URL are rejected", rejected(append_watcher(ledger, "accepted", on="2020-01-05")) and rejected(append(ledger, "github_interest_snapshot", "30", "1", "1", "1", on="2020-01-06", evidence="https://example.com/actions/runs/1")))
    oversized_evidence = "https://github.com/example/" + "r" * 600
    check("oversized public evidence URL is rejected", rejected(append(ledger, "github_interest_snapshot", "30", "1", "1", "1", on="2020-01-07", evidence=oversized_evidence)))
    check("future records are rejected at append time", rejected(append(ledger, "github_interest_snapshot", "30", "1", "1", "1", on="2099-01-01")))

    raw = ledger.read_bytes()
    os.chmod(ledger, 0o644)
    check("non-0600 ledger is rejected", rejected(run("report", "--ledger", str(ledger))))
    os.chmod(ledger, 0o600)
    hard_link = root / "ledger-link.jsonl"
    os.link(ledger, hard_link)
    check("multi-link ledger is rejected", rejected(run("report", "--ledger", str(ledger))))
    hard_link.unlink()
    ledger.write_bytes(raw)
    os.chmod(ledger, 0o600)
    linked = root / "ledger-symlink.jsonl"
    linked.symlink_to(ledger.name)
    check("symlinked ledger and relative ledger path are rejected", rejected(run("report", "--ledger", str(linked))) and rejected(run("report", "--ledger", "ledger.jsonl")))

    future = root / "future.jsonl"
    init(future)
    with future.open("ab") as handle:
        handle.write(canonical(record("github_interest_snapshot", 30, 1, 1, 1, "2099-01-01", "b" * 32)))
    os.chmod(future, 0o600)
    check("future records already in the ledger reject the ledger", rejected(run("report", "--ledger", str(future))))
    extra = root / "extra.jsonl"
    init(extra)
    prohibited = record("github_interest_snapshot", 30, 1, 1, 1, "2020-01-01", "c" * 32)
    prohibited["raw_log"] = "forbidden"
    with extra.open("ab") as handle:
        handle.write(canonical(prohibited))
    os.chmod(extra, 0o600)
    check("extra privacy-bearing fields are rejected", rejected(run("report", "--ledger", str(extra))))
    check("temporary artifacts stay inside the dedicated test root", all(path.parent == root for path in root.iterdir()))

check("temporary ledger root is removed after the test", not temporary_root.exists())
source = SCRIPT.read_text(encoding="utf-8")
check("runtime source has no network process or environment-discovery path", all(token not in source for token in ("import subprocess", "import socket", "import urllib", "import requests", "os.system", "Popen(", "os.environ", "getenv(")))
check("runtime source binds owner-mode-link future duplicate and metric gates", all(token in source for token in ("stat.S_IMODE(info.st_mode) != 0o600", "info.st_nlink != 1", "future observation", "duplicate observation", "METRICS", "URL_RE")))
check("script hash remains stable during offline testing", hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == hashlib.sha256(source.encode("utf-8")).hexdigest())
check("test writes no repository bytecode", bytecode_snapshot() == bytecode_before)

print()
print(f"PASSED: {passed} tests")
if failed:
    print(f"FAILED: {failed} tests")
    raise SystemExit(1)
