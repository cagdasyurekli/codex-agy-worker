#!/usr/bin/env python3
"""Privacy-safe timing observer for the canonical offline gate."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, BinaryIO, Callable, Sequence

sys.dont_write_bytecode = True

MAX_REPORT_BYTES = 64 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
ANNOUNCE_RE = re.compile(rb"^(?:if\s+)?announce '([^']+)'(?:;\s*then)?$")
CONTROL_LABEL = b"@@agy-worker-ci-timing:"
INTEGRITY_STATEMENT = (
    "Unsigned local record; schema-valid content can be rewritten and is not "
    "self-authenticating."
)

STAGES: tuple[tuple[str, str], ...] = (
    ("diff-hygiene", "working-tree diff hygiene"),
    ("shell-syntax", "shell syntax"),
    ("python-syntax", "Python syntax"),
    ("qa-gate", "qa-gate suite"),
    ("evidence-receipt", "Evidence Receipt v1 suite"),
    ("evidence-report", "Evidence Report suite"),
    ("offline-benchmark", "offline benchmark suite"),
    ("persona-evidence", "persona evidence registry suite"),
    ("job-lifecycle", "local job lifecycle suite"),
    ("workload-profiles", "data-only workload profiles suite"),
    ("dispatcher", "dispatcher suite"),
    ("dispatcher-remediation", "dispatcher remediation suite"),
    ("updater", "updater suite"),
    ("adoption-measurement", "adoption measurement suite"),
    ("update-notifier", "local update notifier suite"),
    ("version-attestation-runner", "canonical version attestation runner"),
    ("version-bootstrap-preflight", "repository-only version bootstrap runtime preflight"),
    ("version-bootstrap-runner", "repository-only version bootstrap runner"),
    ("version-initial-bootstrap-runner", "repository-only version initial bootstrap runner"),
    ("version-recovery-1-1-12-runner", "fixed 1.1.12 version recovery runner"),
    ("version-attestation-harness", "version attestation mutation harness"),
    ("models-attestation-runner", "canonical models inventory attestation runner"),
    ("models-capture-runner", "explicit-account models capture runner"),
    ("models-capture-profile", "explicit-account models capture profile builder"),
    ("models-capture-1-1-12-profile", "fixed 1.1.12 models capture profile builder"),
    ("models-capture-1-1-12-runner", "fixed 1.1.12 models capture runner"),
    ("models-capture-1-1-16-version-evidence", "fixed 1.1.16 models capture version evidence"),
    ("models-capture-1-1-16-profile", "fixed 1.1.16 models capture profile builder"),
    ("models-capture-1-1-16-runner", "fixed 1.1.16 models capture runner"),
    ("models-capture-1-1-22-version-evidence", "fixed 1.1.22 models capture version evidence"),
    ("models-capture-1-1-22-profile", "fixed 1.1.22 models capture profile builder"),
    ("models-capture-1-1-22-runner", "fixed 1.1.22 models capture runner"),
    ("models-capture-1-1-22-classifier", "fixed 1.1.22 models capture failure classifier"),
    ("agy-1-1-16-activation", "1.1.16 activation binding"),
    ("reporting", "reporting suite"),
    ("feedback-triage", "feedback triage suite"),
    ("codex-usage-report", "Codex usage observation suite"),
    ("packaging", "Codex distribution suite"),
    ("doctor", "read-only doctor suite"),
    ("conformance", "public gate conformance suite"),
    ("proof-demo", "starter proof suite"),
    ("bytecode-hygiene", "repository bytecode hygiene"),
)


class TimingError(Exception):
    """A timing observation or publication boundary failed closed."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def inventory_digest() -> str:
    return hashlib.sha256(canonical_bytes(STAGES)).hexdigest()


def _finite_duration(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0.0
    )


def validate_report(data: dict[str, Any], expected_inventory_sha: str | None = None) -> None:
    root_keys = {
        "schema_version", "kind", "head_sha", "inventory_sha256",
        "gate_outcome", "total_duration_seconds", "suites", "integrity",
    }
    if not isinstance(data, dict) or set(data) != root_keys:
        raise TimingError("timing report root keys mismatch")
    if data["schema_version"] != 1 or data["kind"] != "agy-worker-ci-timing-report":
        raise TimingError("timing report identity mismatch")
    if not isinstance(data["head_sha"], str) or not COMMIT_RE.fullmatch(data["head_sha"]):
        raise TimingError("head_sha must be a lowercase 40-character Git object ID")
    digest = data["inventory_sha256"]
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise TimingError("inventory_sha256 must be lowercase SHA-256")
    if expected_inventory_sha is not None and digest != expected_inventory_sha:
        raise TimingError("inventory_sha256 does not match the canonical inventory")
    if data["gate_outcome"] not in ("gate-passed", "failed"):
        raise TimingError("invalid gate_outcome")
    if not _finite_duration(data["total_duration_seconds"]):
        raise TimingError("total_duration_seconds must be finite and non-negative")

    suites = data["suites"]
    if not isinstance(suites, list) or len(suites) != len(STAGES):
        raise TimingError("timing report must contain the complete canonical inventory")
    statuses: list[str] = []
    for index, ((expected_id, _), item) in enumerate(zip(STAGES, suites)):
        if not isinstance(item, dict) or set(item) != {"id", "status", "duration_seconds"}:
            raise TimingError(f"invalid suite entry at index {index}")
        if item["id"] != expected_id:
            raise TimingError(f"suite order mismatch at index {index}")
        status_value = item["status"]
        if status_value not in ("passed", "failed", "not-run"):
            raise TimingError(f"invalid suite status at index {index}")
        statuses.append(status_value)
        duration = item["duration_seconds"]
        if status_value == "not-run":
            if duration is not None:
                raise TimingError("not-run suite duration must be null")
        elif not _finite_duration(duration):
            raise TimingError("observed suite duration must be finite and non-negative")

    if data["gate_outcome"] == "gate-passed":
        if any(status_value != "passed" for status_value in statuses):
            raise TimingError("a passed gate requires every canonical stage to pass")
    else:
        failed_indexes = [i for i, status_value in enumerate(statuses) if status_value == "failed"]
        if len(failed_indexes) != 1:
            raise TimingError("a failed gate requires exactly one failed stage")
        failed_index = failed_indexes[0]
        if statuses[:failed_index] != ["passed"] * failed_index:
            raise TimingError("stages before the failure must have passed")
        if statuses[failed_index + 1 :] != ["not-run"] * (len(STAGES) - failed_index - 1):
            raise TimingError("stages after the failure must be not-run")

    if data["integrity"] != {
        "signed": False,
        "tamper_evident": False,
        "statement": INTEGRITY_STATEMENT,
    }:
        raise TimingError("integrity block mismatch")


def validate_gate_inventory(gate_script: Path) -> None:
    try:
        lines = gate_script.read_bytes().splitlines()
    except OSError as exc:
        raise TimingError("cannot read the canonical offline gate") from exc
    observed = [
        match.group(1).decode("utf-8")
        for line in lines
        if (match := ANNOUNCE_RE.fullmatch(line))
    ]
    expected = [announce for _, announce in STAGES]
    if observed != expected:
        raise TimingError("canonical offline gate announcement inventory drifted")


def resolve_clean_head(repo_root: Path) -> str:
    try:
        head = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=str(repo_root),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        status_result = subprocess.run(
            ["/usr/bin/git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(repo_root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TimingError("cannot bind timing observation to Git state") from exc
    sha = head.stdout.decode("ascii", errors="replace").strip()
    if head.returncode != 0 or not COMMIT_RE.fullmatch(sha):
        raise TimingError("cannot resolve an exact lowercase Git HEAD")
    if status_result.returncode != 0 or status_result.stdout:
        raise TimingError("timing observation requires a clean tracked and untracked worktree")
    return sha


def revalidate_clean_head(repo_root: Path, expected_head_sha: str) -> None:
    """Fail if the exact clean Git binding changed during gate execution."""
    if resolve_clean_head(repo_root) != expected_head_sha:
        raise TimingError("Git HEAD changed during timing observation")


def validate_publication_target(target_path: Path) -> Path:
    target = Path(os.path.abspath(os.fspath(target_path)))
    try:
        os.lstat(target)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise TimingError("cannot inspect timing report target") from exc
    else:
        raise TimingError("timing report target already exists")
    try:
        parent_stat = os.lstat(target.parent)
    except OSError as exc:
        raise TimingError("cannot inspect timing report parent") from exc
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise TimingError("timing report parent must be a real directory")
    if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) != 0o700:
        raise TimingError("timing report parent must be owner-private mode 0700")
    return target


def publish_report(target_path: Path, report: dict[str, Any]) -> None:
    validate_report(report, inventory_digest())
    target = validate_publication_target(target_path)
    raw = canonical_bytes(report) + b"\n"
    if len(raw) > MAX_REPORT_BYTES:
        raise TimingError("timing report exceeds the fixed size bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        parent_stat = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.getuid()
            or stat.S_IMODE(parent_stat.st_mode) != 0o700
        ):
            raise TimingError("timing report parent changed before publication")
        try:
            file_fd = os.open(target.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise TimingError("timing report target already exists") from exc
        try:
            view = memoryview(raw)
            while view:
                written = os.write(file_fd, view)
                if written <= 0:
                    raise TimingError("cannot publish complete timing report")
                view = view[written:]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def observe_stream(
    stream: BinaryIO,
    wait: Callable[[], int],
    head_sha: str,
    nonce: str,
    clock: Callable[[], float] = time.monotonic,
    output: BinaryIO | None = None,
) -> tuple[int, dict[str, Any]]:
    if output is None:
        output = sys.stdout.buffer
    expected_announces = [announce.encode("utf-8") for _, announce in STAGES]
    control_prefix = CONTROL_LABEL + nonce.encode("ascii") + b":"
    started: list[float] = []
    durations: list[float] = []
    control_error = False
    gate_start = clock()
    for line in iter(lambda: stream.readline(8192), b""):
        stripped = line.rstrip(b"\r\n")
        if stripped.startswith(CONTROL_LABEL):
            next_index = len(started)
            if (
                not stripped.startswith(control_prefix)
                or next_index >= len(STAGES)
                or stripped != control_prefix + expected_announces[next_index]
            ):
                control_error = True
                continue
            now = clock()
            if started:
                durations.append(round(now - started[-1], 6))
            started.append(now)
            continue
        output.write(line)
        output.flush()
    return_code = wait()
    end = clock()
    if control_error:
        raise TimingError("timing control marker is duplicated, reordered, or invalid")
    if started:
        durations.append(round(end - started[-1], 6))
    if return_code == 0 and len(started) != len(STAGES):
        raise TimingError("successful gate omitted a canonical stage announcement")
    if return_code != 0 and not started:
        raise TimingError("gate failed before the first canonical stage")

    suites: list[dict[str, Any]] = []
    observed_count = len(started)
    for index, (stage_id, _) in enumerate(STAGES):
        if index < observed_count - (1 if return_code != 0 else 0):
            status_value, duration = "passed", durations[index]
        elif return_code != 0 and index == observed_count - 1:
            status_value, duration = "failed", durations[index]
        elif return_code == 0:
            status_value, duration = "passed", durations[index]
        else:
            status_value, duration = "not-run", None
        suites.append({"id": stage_id, "status": status_value, "duration_seconds": duration})

    report = {
        "schema_version": 1,
        "kind": "agy-worker-ci-timing-report",
        "head_sha": head_sha,
        "inventory_sha256": inventory_digest(),
        "gate_outcome": "gate-passed" if return_code == 0 else "failed",
        "total_duration_seconds": round(end - gate_start, 6),
        "suites": suites,
        "integrity": {
            "signed": False,
            "tamper_evident": False,
            "statement": INTEGRITY_STATEMENT,
        },
    }
    validate_report(report, inventory_digest())
    return return_code, report


def run(repo_root: Path, report_path: Path) -> int:
    gate_script = repo_root / "scripts" / "ci-offline.sh"
    validate_publication_target(report_path)
    validate_gate_inventory(gate_script)
    head_sha = resolve_clean_head(repo_root)
    nonce = secrets.token_hex(32)
    try:
        process = subprocess.Popen(
            [os.fspath(gate_script), "--timing-child", nonce],
            cwd=str(repo_root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
    except OSError as exc:
        raise TimingError("cannot start the canonical offline gate") from exc
    assert process.stdout is not None
    return_code, report = observe_stream(process.stdout, process.wait, head_sha, nonce)
    revalidate_clean_head(repo_root, head_sha)
    publish_report(report_path, report)
    return return_code


def main(argv: Sequence[str]) -> int:
    if len(argv) == 4 and list(argv[1:3]) == ["run", "--timing-report"]:
        report_path = Path(argv[3])
    else:
        sys.stderr.write("ci timing: rejected arguments\n")
        return 2
    repo_root = Path(__file__).absolute().parent.parent
    try:
        return run(repo_root, report_path)
    except TimingError as exc:
        sys.stderr.write(f"ci timing error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
