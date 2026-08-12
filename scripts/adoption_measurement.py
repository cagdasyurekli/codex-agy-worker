#!/usr/bin/env python3
"""Explicit local-only adoption measurements for public compatibility evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Iterable


MAX_LEDGER_BYTES = 1024 * 1024
MAX_RECORDS = 4096
MAX_EVIDENCE_URL_LENGTH = 512
HEADER = {"kind": "adoption-measurement-ledger", "schema_version": 2}
WINDOWS = (30, 60, 90)
URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9][A-Za-z0-9_.-]{0,38}/[A-Za-z0-9_.-]+(?:/(?:actions/runs/[1-9][0-9]*|issues/[1-9][0-9]*|pull/[1-9][0-9]*|commit/[0-9a-f]{40}))?$")
WATCHER_URL_RE = re.compile(r"https://github\.com/cagdasyurekli/codex-agy-worker/actions/runs/[1-9][0-9]*$")
IDENTIFIER_RE = re.compile(r"[0-9a-f]{32}$")
SHA_RE = re.compile(r"[0-9a-f]{40}$")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}$")
RECORD_FIELDS = {"denominator", "evidence_url", "metric", "observation_id", "observed_on", "repo_sha", "sample_size", "value", "window_days"}

# The metric name fixes both its reporting horizon and aggregation semantics.
METRICS = {
    "watcher_unchanged_count": (30, "sum"),
    "watcher_drift_review_count": (30, "sum"),
    "watcher_evidence_unavailable_count": (30, "sum"),
    "fresh_clone_to_proof_duration_seconds": (30, "median"),
    "doctor_pre_dispatch_blocker_ratio": (30, "ratio"),
    "published_example_completeness_ratio": (30, "ratio"),
    "github_interest_snapshot": (30, "latest"),
    "compatibility_lead_time_seconds": (60, "median"),
    "verified_external_receipt_proof_count": (60, "sum"),
    "bug_report_completeness_ratio": (60, "ratio"),
    "referral_search_trend": (60, "latest"),
    "gate_external_action_regression_count": (60, "sum"),
    "baseline_audit_count": (90, "sum"),
    "baseline_audit_ratio": (90, "ratio"),
    "external_conformance_workflow_count": (90, "sum"),
    "conformance_result_ratio": (90, "ratio"),
    "bound_benchmark_claim_count": (90, "sum"),
    "accepted_real_persona_count": (90, "sum"),
}
WATCHER_METRICS = {
    "unchanged": "watcher_unchanged_count",
    "drift-review": "watcher_drift_review_count",
    "evidence-unavailable": "watcher_evidence_unavailable_count",
}


class LedgerError(ValueError):
    pass


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def observed_date(value: str) -> dt.date:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise LedgerError("invalid observation date")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise LedgerError("invalid observation date") from exc


def ledger_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path == Path("/") or path.name in ("", ".", ".."):
        raise LedgerError("ledger path must be an explicit absolute file path")
    try:
        parent = os.lstat(path.parent)
    except OSError as exc:
        raise LedgerError("ledger parent is unavailable") from exc
    if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode):
        raise LedgerError("ledger parent is not a real directory")
    return path


def strict_ledger_stat(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        raise LedgerError("ledger must be an owner-only regular file with one link")


def open_ledger(path: Path, flags: int) -> int:
    try:
        descriptor = os.open(str(path), flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise LedgerError("ledger is unavailable") from exc
    try:
        strict_ledger_stat(os.fstat(descriptor))
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def unsigned(value: Any, maximum: int = 10**9) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise LedgerError("invalid numeric measurement")
    return value


def validate_record(value: Any, today: dt.date) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != RECORD_FIELDS:
        raise LedgerError("invalid observation fields")
    metric = value.get("metric")
    window = value.get("window_days")
    if metric not in METRICS or window != METRICS[metric][0]:
        raise LedgerError("invalid metric horizon")
    if not isinstance(value.get("observation_id"), str) or not IDENTIFIER_RE.fullmatch(value["observation_id"]):
        raise LedgerError("invalid observation identifier")
    if not isinstance(value.get("repo_sha"), str) or not SHA_RE.fullmatch(value["repo_sha"]):
        raise LedgerError("invalid repository revision")
    evidence_url = value.get("evidence_url")
    if not isinstance(evidence_url, str) or len(evidence_url) > MAX_EVIDENCE_URL_LENGTH or not URL_RE.fullmatch(evidence_url):
        raise LedgerError("invalid public evidence URL")
    if metric in WATCHER_METRICS.values() and not WATCHER_URL_RE.fullmatch(evidence_url):
        raise LedgerError("invalid watcher evidence URL")
    day = observed_date(value.get("observed_on"))
    if day > today:
        raise LedgerError("future observation")
    numerator = unsigned(value.get("value"))
    denominator = unsigned(value.get("denominator"))
    sample_size = unsigned(value.get("sample_size"))
    kind = METRICS[metric][1]
    if kind == "ratio":
        if denominator < 1 or sample_size < 1 or sample_size > denominator or numerator > sample_size:
            raise LedgerError("invalid ratio measurement")
    elif denominator != 1 or sample_size != 1:
        raise LedgerError("invalid scalar measurement")
    return value


def read_records(descriptor: int, today: dt.date) -> list[dict[str, Any]]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_LEDGER_BYTES:
            raise LedgerError("ledger is too large")
        chunks.append(chunk)
    lines = b"".join(chunks).splitlines(keepends=True)
    if not lines or len(lines) > MAX_RECORDS + 1 or any(not line.endswith(b"\n") for line in lines):
        raise LedgerError("ledger is malformed")
    values: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerError("ledger is malformed") from exc
        if not isinstance(value, dict) or canonical(value) != line:
            raise LedgerError("ledger is not canonical")
        values.append(value)
    if values[0] != HEADER:
        raise LedgerError("ledger header is invalid")
    identifiers: set[str] = set()
    dates: set[tuple[str, int, dt.date]] = set()
    records: list[dict[str, Any]] = []
    for value in values[1:]:
        record = validate_record(value, today)
        key = (record["metric"], record["window_days"], observed_date(record["observed_on"]))
        if record["observation_id"] in identifiers or key in dates:
            raise LedgerError("duplicate observation")
        identifiers.add(record["observation_id"])
        dates.add(key)
        records.append(record)
    return records


def read_ledger(path: Path, today: dt.date) -> list[dict[str, Any]]:
    descriptor = open_ledger(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        return read_records(descriptor, today)
    finally:
        os.close(descriptor)


def init_ledger(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise LedgerError("ledger already exists or cannot be created") from exc
    try:
        os.fchmod(descriptor, 0o600)
        strict_ledger_stat(os.fstat(descriptor))
        os.write(descriptor, canonical(HEADER))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_record(path: Path, record: dict[str, Any], today: dt.date) -> None:
    descriptor = open_ledger(path, os.O_RDWR | os.O_APPEND)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        records = read_records(descriptor, today)
        if len(records) >= MAX_RECORDS:
            raise LedgerError("ledger record limit reached")
        key = (record["metric"], record["window_days"], observed_date(record["observed_on"]))
        if any((item["metric"], item["window_days"], observed_date(item["observed_on"])) == key for item in records):
            raise LedgerError("a measurement already exists for that metric and UTC day")
        payload = canonical(record)
        original_size = os.lseek(descriptor, 0, os.SEEK_END)
        if original_size + len(payload) > MAX_LEDGER_BYTES:
            raise LedgerError("ledger is too large")
        try:
            if os.write(descriptor, payload) != len(payload):
                raise LedgerError("ledger append was incomplete")
            os.fsync(descriptor)
        except BaseException:
            os.ftruncate(descriptor, original_size)
            os.fsync(descriptor)
            raise
    finally:
        os.close(descriptor)


def median(values: Iterable[int]) -> str:
    ordered = sorted(values)
    if not ordered:
        return "not available"
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return str(ordered[middle])
    return str((ordered[middle - 1] + ordered[middle]) / 2).rstrip("0").rstrip(".")


def metric_line(metric: str, records: list[dict[str, Any]]) -> str:
    if not records:
        return f"- {metric}: missing"
    kind = METRICS[metric][1]
    latest = max(records, key=lambda item: item["observed_on"])
    if kind == "sum":
        return f"- {metric}: sum {sum(item['value'] for item in records)}; latest {latest['value']} on {latest['observed_on']}"
    if kind == "median":
        return f"- {metric}: median {median(item['value'] for item in records)}; samples {len(records)}; latest {latest['value']} on {latest['observed_on']}"
    if kind == "latest":
        return f"- {metric}: latest {latest['value']} on {latest['observed_on']}; observations {len(records)}"
    numerator = sum(item["value"] for item in records)
    denominator = sum(item["denominator"] for item in records)
    sample_size = sum(item["sample_size"] for item in records)
    status = "complete" if sample_size == denominator else "partial"
    return f"- {metric}: {numerator}/{denominator}; sample size {sample_size}; {status}; latest {latest['value']}/{latest['denominator']} on {latest['observed_on']}"


def report_window(records: list[dict[str, Any]], today: dt.date, days: int) -> str:
    cutoff = today - dt.timedelta(days=days - 1)
    active = [record for record in records if record["window_days"] == days and observed_date(record["observed_on"]) >= cutoff]
    expired = sum(1 for record in records if record["window_days"] == days and observed_date(record["observed_on"]) < cutoff)
    metrics = [name for name, (window, _) in METRICS.items() if window == days]
    lines = [f"## {days}-day measurement report", "", f"Observations in window: {len(active)}", f"Expired observations ignored: {expired}"]
    lines.extend(metric_line(metric, [record for record in active if record["metric"] == metric]) for metric in metrics)
    lines.extend(("Limitations: only closed aggregate metrics and approved public evidence URLs are retained; no provider usage, prompts, logs, accounts, host data, or user identifiers are collected.", ""))
    return "\n".join(lines)


def render_report(path: Path, today: dt.date) -> str:
    records = read_ledger(path, today)
    return "# Compatibility-watch adoption measurement\n\n" + "\n".join(report_window(records, today, days) for days in WINDOWS)


def add_common(command: argparse.ArgumentParser) -> None:
    command.add_argument("--ledger", required=True)
    command.add_argument("--observed-on")
    command.add_argument("--repo-sha", required=True)
    command.add_argument("--evidence-url", required=True)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(prog="adoption_measurement.py", add_help=False)
    subcommands = argument_parser.add_subparsers(dest="command")
    init = subcommands.add_parser("init", add_help=False)
    init.add_argument("--ledger", required=True)
    append = subcommands.add_parser("append", add_help=False)
    add_common(append)
    append.add_argument("--window", required=True)
    append.add_argument("--metric", required=True)
    append.add_argument("--value", required=True)
    append.add_argument("--denominator", required=True)
    append.add_argument("--sample-size", required=True)
    watcher = subcommands.add_parser("append-watcher", add_help=False)
    add_common(watcher)
    watcher.add_argument("--result", required=True)
    report = subcommands.add_parser("report", add_help=False)
    report.add_argument("--ledger", required=True)
    return argument_parser


def number(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise LedgerError("invalid numeric measurement")
    return int(value)


def make_record(arguments: argparse.Namespace, metric: str, window: int, value: int, denominator: int, sample_size: int, today: dt.date) -> dict[str, Any]:
    day = today if arguments.observed_on is None else observed_date(arguments.observed_on)
    record = {
        "denominator": denominator,
        "evidence_url": arguments.evidence_url,
        "metric": metric,
        "observation_id": secrets.token_hex(16),
        "observed_on": day.isoformat(),
        "repo_sha": arguments.repo_sha,
        "sample_size": sample_size,
        "value": value,
        "window_days": window,
    }
    return validate_record(record, today)


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parser().parse_args(argv)
        if arguments.command not in ("init", "append", "append-watcher", "report"):
            raise LedgerError("invalid arguments")
        path = ledger_path(arguments.ledger)
        if arguments.command == "init":
            init_ledger(path)
            print("measurement ledger initialized")
            return 0
        today = utc_today()
        if arguments.command == "report":
            print(render_report(path, today), end="")
            return 0
        if arguments.command == "append-watcher":
            metric = WATCHER_METRICS.get(arguments.result)
            if metric is None:
                raise LedgerError("invalid watcher result")
            record = make_record(arguments, metric, 30, 1, 1, 1, today)
        else:
            if arguments.metric not in METRICS or arguments.window not in ("30", "60", "90"):
                raise LedgerError("invalid metric")
            record = make_record(arguments, arguments.metric, int(arguments.window), number(arguments.value), number(arguments.denominator), number(arguments.sample_size), today)
        append_record(path, record, today)
        print("measurement observation appended")
        return 0
    except (LedgerError, OSError, ValueError):
        print("adoption-measurement: invalid local measurement input", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
