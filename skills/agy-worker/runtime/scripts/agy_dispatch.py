#!/usr/bin/env python3
"""Progress-aware local supervisor for one agy worker conversation.

This is a local process lifecycle, not an agy/provider status API.  The controller
owns one agy process group, consumes bounded stream-json incrementally, and publishes
only sanitized control state.  Raw streams and prompts remain in the owner-private
job directory.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Iterator

sys.dont_write_bytecode = True

STATE_NAME = "dispatch-state.json"
COMMAND_NAME = "dispatch-command.json"
LOCK_NAME = ".dispatch.lock"
STATE_LOCK_NAME = ".dispatch-state.lock"
MAX_STATE_BYTES = 128 * 1024
MAX_COMMAND_BYTES = 512 * 1024
MAX_VERIFICATION_BYTES = 16 * 1024
MAX_CHECK_ITEMS = 32
MAX_CHECK_LABEL = 160
MAX_CHECK_SUMMARY = 512
MAX_BOUNDARY_ENTRIES = 100000
MAX_STREAM_BYTES = 32 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_STATUS_WAIT = 60.0
TERM_GRACE = 1.0
CONTROL_POLL = 0.20
CONVERSATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
JOB_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
COMMAND_V1_FIELDS = {
    "schema_version", "kind", "job_id", "workdir", "argv", "agy_version",
    "idle_seconds", "hard_seconds", "max_seconds", "notice_seconds",
    "stage_dir", "stage_file", "child_umask", "resume_prompt",
}
COMMAND_V2_FIELDS = COMMAND_V1_FIELDS | {"workflow", "max_cycles", "continue_prompt"}
COMMAND_V3_FIELDS = COMMAND_V2_FIELDS | {"agy_version_observed"}
STATE_PROJECT_FIELDS = {
    "workflow", "max_cycles", "cycle", "phase", "assurance",
    "check_summary", "check_counts", "verification_path", "verification_sha256",
    "verification_identity", "continue_available", "last_success_path",
    "last_success_sha256", "last_success_identity",
    "project_boundary",
}
STATE_V5_FIELDS = {
    "candidate_recognized", "candidate_source", "result_available",
    "worktree_reconciliation", "worktree_changes_present",
    "worktree_changed_since_dispatch", "driver_disposition", "failure_stage",
    "last_activity", "next_action", "next_action_command", "worktree_baseline",
    "provider_schema_sha256", "provider_schema_identity",
    "canonical_schema_sha256", "canonical_schema_identity",
    "candidate_worktree_sha256", "candidate_worktree_entries",
}
FAILURE_STAGES = {
    "framing", "outer_status", "structured_output", "schema_rejection",
    "binding_failure",
}
LIFECYCLE_PHASES = {
    "dispatching", "awaiting-verification", "repairing", "completed",
    "blocked", "attempt-failed", "repair-failed",
}
TERMINAL = {"succeeded", "failed", "cancelled", "orphaned"}
REASONS = {
    "provider_timeout", "idle_timeout", "hard_deadline_exceeded",
    "authentication_failed", "provider_unavailable", "status_unavailable",
    "resume_failed", "cancelled", "agy_failed_unclassified",
    "permission_required", "empty_output", "invalid_envelope",
    "output_oversized", "interrupted", "provider_quota_exhausted",
    "provider_terminal_error", "provider_terminal_cancelled",
}
EXIT_BY_REASON = {
    "empty_output": 3,
    "invalid_envelope": 4,
    "agy_failed_unclassified": 5,
    "permission_required": 6,
    "idle_timeout": 9,
    "hard_deadline_exceeded": 16,
    "provider_timeout": 17,
    "authentication_failed": 18,
    "provider_unavailable": 19,
    "status_unavailable": 20,
    "resume_failed": 21,
    "cancelled": 22,
    "output_oversized": 23,
    "provider_quota_exhausted": 24,
    "provider_terminal_error": 25,
    "provider_terminal_cancelled": 22,
    "interrupted": 143,
}

# These are exact, version-scoped observations.  Unknown text remains unclassified;
# no broad substring or prose inference may decide auth/provider state.
# Populated only from retained, version-bound observations.  There is currently no
# reviewed 1.1.12 stderr evidence in the repository, so every provider diagnostic
# remains deliberately unclassified.
EXACT_FAILURE_LINES: dict[str, dict[bytes, str]] = {"1.1.12": {}}

# agy 1.1.13 emitted this exact provider-owned terminal error shape in three
# retained same-conversation observations. The reset duration is the only
# variable part. Do not broaden this to free-form quota/message matching or to
# another agy version without separately reviewed evidence.
QUOTA_ERROR_1_1_13_RE = re.compile(
    r"rpc error: Individual quota reached\. Contact your administrator to enable "
    r"overages\. Resets in (?P<hours>[0-9]{1,3})h"
    r"(?P<minutes>[0-9]{2})m(?P<seconds>[0-9]{2})s\."
)
QUOTA_RESULT_FIELDS = {
    "conversation_id", "status", "response", "error", "duration_seconds",
    "num_turns", "json_schema", "usage",
}
MAX_PROVIDER_RETRY_SECONDS = 30 * 24 * 3600


class DispatchError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.print_usage(sys.stderr)
        self.exit(64, "agy-dispatch: invalid arguments\n")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii") + b"\n"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DispatchError("JSON contains duplicate fields")
        result[key] = value
    return result


def _invalid_json_constant(_value: str) -> None:
    raise DispatchError("JSON contains a non-finite number")


def parse_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=_duplicates,
            parse_constant=_invalid_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, DispatchError) as exc:
        raise DispatchError(f"{label} is invalid") from exc


def _valid_max_cycles(workflow: str, value: Any) -> bool:
    if type(value) is not int:
        return False
    if workflow == "legacy":
        return value == 1
    if workflow in {"explore", "task"}:
        return 1 <= value <= 2
    return workflow == "project" and 1 <= value <= 5


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev, info.st_ino, info.st_uid, info.st_gid,
        stat.S_IMODE(info.st_mode),
    )


def canonical_job(path: Path) -> Path:
    if not path.is_absolute() or Path(os.path.realpath(path)) != path:
        raise DispatchError("job directory must be one canonical absolute path")
    try:
        info = path.lstat()
    except OSError as exc:
        raise DispatchError("job directory is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise DispatchError("job directory must be owner-private")
    return path


def read_regular(
    path: Path, maximum: int, label: str, *, allowed_modes: tuple[int, ...] = (0o600,),
) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise DispatchError(f"{label} is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) not in allowed_modes
            or info.st_nlink != 1
        ):
            raise DispatchError(f"{label} must be one owner-private regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise DispatchError(f"{label} is oversized")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        named = path.lstat()
    except OSError as exc:
        raise DispatchError(f"{label} identity changed") from exc
    if _identity(info) != _identity(after) or _identity(after) != _identity(named):
        raise DispatchError(f"{label} identity changed")
    return b"".join(chunks), named


def write_atomic(job: Path, name: str, value: Any) -> tuple[bytes, str]:
    raw = canonical(value)
    temporary = job / f".{name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DispatchError("state write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, job / name)
    parent = os.open(job, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return raw, digest(raw)


@contextlib.contextmanager
def lifecycle_lock(job: Path, *, blocking: bool) -> Iterator[int]:
    path = job / LOCK_NAME
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as exc:
            raise DispatchError("dispatch controller is active") from exc
        yield descriptor
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def state_lock(job: Path) -> Iterator[int]:
    """Serialize short state replacements without sharing controller ownership."""

    path = job / STATE_LOCK_NAME
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def inherited_lifecycle_lock(job: Path, descriptor: int) -> Iterator[int]:
    """Adopt the spawner's already-held lock without an unlock/relock gap."""

    try:
        info = os.fstat(descriptor)
        named = (job / LOCK_NAME).lstat()
    except OSError as exc:
        raise DispatchError("inherited dispatch ownership is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or _identity(info) != _identity(named)
    ):
        raise DispatchError("inherited dispatch ownership is invalid")
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def load_state(job: Path) -> tuple[dict[str, Any], bytes, str]:
    raw, _info = read_regular(job / STATE_NAME, MAX_STATE_BYTES, "dispatch state")
    value = parse_json(raw, "dispatch state")
    value = validate_state(value)
    return value, raw, digest(raw)


def read_state_snapshot(job: Path) -> tuple[dict[str, Any], bytes, str]:
    """Read one strict state snapshot without racing an approved replacement."""

    with state_lock(job):
        return load_state(job)


def load_command(job: Path) -> tuple[dict[str, Any], bytes, tuple[int, int, int, int, int]]:
    raw, info = read_regular(job / COMMAND_NAME, MAX_COMMAND_BYTES, "dispatch command")
    value = parse_json(raw, "dispatch command")
    if not isinstance(value, dict):
        raise DispatchError("dispatch command fields are invalid")
    if set(value) == COMMAND_V1_FIELDS and value.get("schema_version") == 1:
        if raw != canonical(value):
            raise DispatchError("legacy dispatch command is not canonical")
        value = dict(value)
        value.update({
            "workflow": "legacy", "max_cycles": 1,
            "continue_prompt": "legacy commands cannot continue projects",
            "agy_version_observed": False,
        })
    elif set(value) == COMMAND_V2_FIELDS and value.get("schema_version") == 2:
        if raw != canonical(value):
            raise DispatchError("dispatch command is not canonical")
        value = dict(value)
        value["agy_version_observed"] = False
    elif set(value) != COMMAND_V3_FIELDS or value.get("schema_version") != 3:
        raise DispatchError("dispatch command fields are invalid")
    elif raw != canonical(value):
        raise DispatchError("dispatch command is not canonical")
    if value["kind"] != "agy-worker-dispatch-command":
        raise DispatchError("dispatch command version is invalid")
    if (
        not isinstance(value["agy_version"], str)
        or re.fullmatch(
            r"(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\."
            r"(?:0|[1-9][0-9]{0,4})",
            value["agy_version"],
        ) is None
    ):
        raise DispatchError("dispatch agy version is invalid")
    if not isinstance(value["job_id"], str) or JOB_RE.fullmatch(value["job_id"]) is None:
        raise DispatchError("dispatch command job ID is invalid")
    if not isinstance(value["argv"], list) or not value["argv"] or any(
        not isinstance(item, str) or "\x00" in item for item in value["argv"]
    ):
        raise DispatchError("dispatch argv is invalid")
    if value["argv"][0] != "agy" or value["argv"].count("--print") != 1:
        raise DispatchError("dispatch argv contract is invalid")
    if not isinstance(value["workdir"], str) or not Path(value["workdir"]).is_absolute():
        raise DispatchError("dispatch workdir is invalid")
    for key in ("idle_seconds", "hard_seconds", "max_seconds", "notice_seconds"):
        if type(value[key]) not in (int, float) or not (0 < value[key] <= 7 * 24 * 3600):
            raise DispatchError("dispatch duration is invalid")
    if not (value["idle_seconds"] <= value["hard_seconds"] <= value["max_seconds"]):
        raise DispatchError("dispatch duration order is invalid")
    for key in ("stage_dir", "stage_file"):
        if value[key] is not None and (
            not isinstance(value[key], str) or not Path(value[key]).is_absolute()
        ):
            raise DispatchError("dispatch stage path is invalid")
    if not isinstance(value["child_umask"], str) or re.fullmatch(r"[0-7]{3,4}", value["child_umask"]) is None:
        raise DispatchError("dispatch child umask is invalid")
    if not isinstance(value["resume_prompt"], str) or not value["resume_prompt"]:
        raise DispatchError("dispatch resume prompt is invalid")
    if not isinstance(value["continue_prompt"], str) or not value["continue_prompt"]:
        raise DispatchError("dispatch continue prompt is invalid")
    if value["workflow"] not in {"legacy", "explore", "task", "project"}:
        raise DispatchError("dispatch workflow is invalid")
    if not _valid_max_cycles(value["workflow"], value["max_cycles"]):
        raise DispatchError("dispatch max cycles is invalid for workflow")
    if type(value["agy_version_observed"]) is not bool:
        raise DispatchError("dispatch agy version evidence is invalid")
    return value, raw, _identity(info)


def validate_state(value: Any) -> dict[str, Any]:
    fields = {
        "schema_version", "kind", "sequence", "previous_state_sha256", "job_id",
        "status", "attempt", "attempt_origin", "reason", "exit_code",
        "controller_pid", "workdir", "created_epoch", "started_epoch",
        "updated_epoch", "finished_epoch", "elapsed_seconds", "progress_count",
        "last_progress_epoch", "notice_count", "hard_seconds", "max_seconds",
        "cancel_requested", "conversation_id", "resume_available",
        "remote_cancel_unverified", "result_path", "stream_path", "stderr_path",
        "agy_returncode", "limit_kind", "command_sha256", "command_identity",
        "stage_sha256", "stage_identity", "result_sha256", "result_identity",
        "idle_seconds", "attempt_base_elapsed",
        "workflow", "max_cycles", "cycle", "phase", "assurance",
        "check_summary", "check_counts", "verification_path", "verification_sha256",
        "verification_identity", "continue_available", "last_success_path",
        "last_success_sha256", "last_success_identity", "project_boundary",
        "provider_retry_after_seconds", "provider_retry_observed_epoch",
        "candidate_recognized", "candidate_source", "result_available",
        "worktree_reconciliation", "worktree_changes_present",
        "worktree_changed_since_dispatch", "driver_disposition", "failure_stage",
        "last_activity", "next_action", "next_action_command", "worktree_baseline",
        "provider_schema_sha256", "provider_schema_identity",
        "canonical_schema_sha256", "canonical_schema_identity",
        "candidate_worktree_sha256", "candidate_worktree_entries",
    }
    retry_fields = {"provider_retry_after_seconds", "provider_retry_observed_epoch"}
    legacy_fields = fields - STATE_PROJECT_FIELDS - retry_fields - STATE_V5_FIELDS
    version_three_fields = fields - retry_fields - STATE_V5_FIELDS
    version_four_fields = fields - STATE_V5_FIELDS
    if not isinstance(value, dict):
        raise DispatchError("dispatch state fields are invalid")
    if set(value) == legacy_fields and value.get("schema_version") == 1:
        value = dict(value)
        recognized = value["result_path"] is not None
        value.update({
            "workflow": "legacy", "max_cycles": 1, "cycle": value["attempt"],
            "phase": None, "assurance": None, "check_summary": None,
            "check_counts": {"passed": 0, "failed": 0, "advisory": 0, "missing": 0},
            "verification_path": None, "verification_sha256": None,
            "verification_identity": None, "continue_available": False,
            "last_success_path": None, "last_success_sha256": None,
            "last_success_identity": None, "project_boundary": None,
            "provider_retry_after_seconds": None,
            "provider_retry_observed_epoch": None,
            "candidate_recognized": recognized,
            "candidate_source": "provider_success" if recognized else "none",
            "result_available": recognized, "worktree_reconciliation": "not_applicable",
            "worktree_changes_present": None, "worktree_changed_since_dispatch": None,
            "driver_disposition": "unreviewed" if recognized else "not_applicable",
            "failure_stage": None, "last_activity": None,
            "next_action": "driver_review" if recognized else "none",
            "next_action_command": None,
            "worktree_baseline": None,
            "provider_schema_sha256": None, "provider_schema_identity": None,
            "canonical_schema_sha256": None, "canonical_schema_identity": None,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
        })
    elif set(value) == version_three_fields and value.get("schema_version") == 3:
        value = dict(value)
        if value["result_path"] is None and value["last_success_path"] is not None:
            value["result_path"] = value["last_success_path"]
            value["result_sha256"] = value["last_success_sha256"]
            value["result_identity"] = value["last_success_identity"]
        value.update({
            "provider_retry_after_seconds": None,
            "provider_retry_observed_epoch": None,
            "candidate_recognized": bool(value["result_path"]),
            "candidate_source": "provider_success" if value["result_path"] else "none",
            "result_available": bool(value["result_path"]),
            "worktree_reconciliation": "not_applicable",
            "worktree_changes_present": None, "worktree_changed_since_dispatch": None,
            "driver_disposition": "unreviewed" if value["result_path"] else "not_applicable",
            "failure_stage": None, "last_activity": None,
            "next_action": "driver_review" if value["result_path"] else "none",
            "next_action_command": None, "worktree_baseline": None,
            "provider_schema_sha256": None, "provider_schema_identity": None,
            "canonical_schema_sha256": None, "canonical_schema_identity": None,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
        })
    elif set(value) == version_four_fields and value.get("schema_version") == 4:
        value = dict(value)
        if value["result_path"] is None and value["last_success_path"] is not None:
            value["result_path"] = value["last_success_path"]
            value["result_sha256"] = value["last_success_sha256"]
            value["result_identity"] = value["last_success_identity"]
        value.update({
            "candidate_recognized": bool(value["result_path"]),
            "candidate_source": "provider_success" if value["result_path"] else "none",
            "result_available": bool(value["result_path"]),
            "worktree_reconciliation": "not_applicable",
            "worktree_changes_present": None, "worktree_changed_since_dispatch": None,
            "driver_disposition": "unreviewed" if value["result_path"] else "not_applicable",
            "failure_stage": None, "last_activity": None,
            "next_action": "driver_review" if value["result_path"] else "none",
            "next_action_command": None, "worktree_baseline": None,
            "provider_schema_sha256": None, "provider_schema_identity": None,
            "canonical_schema_sha256": None, "canonical_schema_identity": None,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
        })
    elif set(value) != fields or value.get("schema_version") != 5:
        raise DispatchError("dispatch state fields are invalid")
    if value["kind"] != "agy-worker-dispatch-state":
        raise DispatchError("dispatch state version is invalid")
    if type(value["sequence"]) is not int or value["sequence"] < 1:
        raise DispatchError("dispatch sequence is invalid")
    previous = value["previous_state_sha256"]
    if previous is not None and (not isinstance(previous, str) or SHA_RE.fullmatch(previous) is None):
        raise DispatchError("dispatch history is invalid")
    if (value["sequence"] == 1) != (previous is None):
        raise DispatchError("dispatch history is inconsistent")
    if value["status"] not in {"queued", "running", "cancel-requested", *TERMINAL}:
        raise DispatchError("dispatch status is invalid")
    if value["reason"] is not None and value["reason"] not in REASONS:
        raise DispatchError("dispatch reason is invalid")
    if type(value["candidate_recognized"]) is not bool or type(value["result_available"]) is not bool:
        raise DispatchError("dispatch candidate flags are invalid")
    if value["candidate_source"] not in {"none", "provider_success", "provider_error", "provider_cancelled"}:
        raise DispatchError("dispatch candidate source is invalid")
    if (
        value["candidate_recognized"] != (value["candidate_source"] != "none")
        or (value["result_available"] and not value["candidate_recognized"])
    ):
        raise DispatchError("dispatch candidate state is inconsistent")
    if value["worktree_reconciliation"] not in {"available", "unavailable", "not_applicable"}:
        raise DispatchError("dispatch worktree reconciliation is invalid")
    for key in ("worktree_changes_present", "worktree_changed_since_dispatch"):
        if value[key] is not None and type(value[key]) is not bool:
            raise DispatchError("dispatch worktree observation is invalid")
    if value["worktree_reconciliation"] == "available" and (
        value["worktree_changes_present"] is None or value["worktree_changed_since_dispatch"] is None
    ):
        raise DispatchError("dispatch worktree reconciliation is incomplete")
    if value["worktree_reconciliation"] != "available" and (
        value["worktree_changes_present"] is not None or value["worktree_changed_since_dispatch"] is not None
    ):
        raise DispatchError("dispatch unavailable worktree reconciliation has observations")
    if value["driver_disposition"] not in {"not_applicable", "unreviewed", "verified", "partially_verified", "rejected", "blocked"}:
        raise DispatchError("dispatch driver disposition is invalid")
    if value["failure_stage"] not in {None, *FAILURE_STAGES}:
        raise DispatchError("dispatch failure stage is invalid")
    if value["last_activity"] not in {None, "provider_initialized", "progress_signal", "terminal_received"}:
        raise DispatchError("dispatch activity is invalid")
    if value["next_action"] not in {"none", "wait", "resume", "driver_review", "driver_finalize", "blocked"}:
        raise DispatchError("dispatch next action is invalid")
    if value["next_action_command"] is not None and (not isinstance(value["next_action_command"], str) or not value["next_action_command"]):
        raise DispatchError("dispatch next action command is invalid")
    baseline = value["worktree_baseline"]
    if baseline is not None and (
        not isinstance(baseline, dict) or set(baseline) != {"sha256", "entries"}
        or not isinstance(baseline["sha256"], str) or SHA_RE.fullmatch(baseline["sha256"]) is None
        or type(baseline["entries"]) is not int or not (0 <= baseline["entries"] <= MAX_BOUNDARY_ENTRIES)
    ):
        raise DispatchError("dispatch worktree baseline is invalid")
    candidate_worktree_sha = value["candidate_worktree_sha256"]
    candidate_worktree_entries = value["candidate_worktree_entries"]
    if (candidate_worktree_sha is None) != (candidate_worktree_entries is None):
        raise DispatchError("dispatch candidate worktree binding is incomplete")
    if candidate_worktree_sha is not None and (
        not isinstance(candidate_worktree_sha, str)
        or SHA_RE.fullmatch(candidate_worktree_sha) is None
        or type(candidate_worktree_entries) is not int
        or not (0 <= candidate_worktree_entries <= MAX_BOUNDARY_ENTRIES)
    ):
        raise DispatchError("dispatch candidate worktree binding is invalid")
    for digest_key, identity_key in (
        ("provider_schema_sha256", "provider_schema_identity"),
        ("canonical_schema_sha256", "canonical_schema_identity"),
    ):
        bound_digest, bound_identity = value[digest_key], value[identity_key]
        if (bound_digest is None) != (bound_identity is None):
            raise DispatchError("dispatch schema binding is incomplete")
        if bound_digest is not None and (
            not isinstance(bound_digest, str) or SHA_RE.fullmatch(bound_digest) is None
            or not isinstance(bound_identity, list) or len(bound_identity) != 5
            or any(type(item) is not int or item < 0 for item in bound_identity)
        ):
            raise DispatchError("dispatch schema binding is invalid")
    if value["attempt_origin"] not in {"initial", "conversation-resume", "fresh-restart", "conversation-continue"}:
        raise DispatchError("dispatch attempt origin is invalid")
    if type(value["attempt"]) is not int or value["attempt"] < 1:
        raise DispatchError("dispatch attempt is invalid")
    if value["workflow"] not in {"legacy", "explore", "task", "project"}:
        raise DispatchError("dispatch workflow state is invalid")
    if not _valid_max_cycles(value["workflow"], value["max_cycles"]):
        raise DispatchError("dispatch max cycles state is invalid")
    if type(value["cycle"]) is not int or value["cycle"] != value["attempt"] or (
        value["workflow"] != "legacy" and value["cycle"] > value["max_cycles"]
    ):
        raise DispatchError("dispatch cycle state is invalid")
    if not isinstance(value["job_id"], str) or JOB_RE.fullmatch(value["job_id"]) is None:
        raise DispatchError("dispatch job ID is invalid")
    conversation = value["conversation_id"]
    if conversation is not None and (
        not isinstance(conversation, str) or CONVERSATION_RE.fullmatch(conversation) is None
    ):
        raise DispatchError("dispatch conversation ID is invalid")
    for key in ("cancel_requested", "resume_available", "continue_available", "remote_cancel_unverified"):
        if type(value[key]) is not bool:
            raise DispatchError("dispatch boolean is invalid")
    for key in ("progress_count", "notice_count"):
        if type(value[key]) is not int or value[key] < 0:
            raise DispatchError("dispatch counter is invalid")
    for key in (
        "created_epoch", "updated_epoch", "elapsed_seconds", "hard_seconds",
        "max_seconds", "idle_seconds", "attempt_base_elapsed",
    ):
        if type(value[key]) not in (int, float) or value[key] < 0:
            raise DispatchError("dispatch time is invalid")
    for key in ("started_epoch", "finished_epoch", "last_progress_epoch"):
        if value[key] is not None and (type(value[key]) not in (int, float) or value[key] < 0):
            raise DispatchError("dispatch optional time is invalid")
    retry_after = value["provider_retry_after_seconds"]
    retry_observed = value["provider_retry_observed_epoch"]
    if (retry_after is None) != (retry_observed is None):
        raise DispatchError("dispatch provider retry binding is incomplete")
    if retry_after is not None and (
        type(retry_after) is not int or not (1 <= retry_after <= MAX_PROVIDER_RETRY_SECONDS)
        or type(retry_observed) not in (int, float)
        or not math.isfinite(retry_observed) or retry_observed < 0
    ):
        raise DispatchError("dispatch provider retry binding is invalid")
    if value["reason"] != "provider_quota_exhausted" and retry_after is not None:
        raise DispatchError("dispatch provider retry reason is inconsistent")
    for key in ("exit_code", "controller_pid", "agy_returncode"):
        if value[key] is not None and type(value[key]) is not int:
            raise DispatchError("dispatch integer is invalid")
    for key in ("workdir", "result_path", "stream_path", "stderr_path", "verification_path", "last_success_path"):
        if value[key] is not None and not isinstance(value[key], str):
            raise DispatchError("dispatch path is invalid")
    if value["limit_kind"] not in {None, "idle", "hard", "max-runtime"}:
        raise DispatchError("dispatch limit kind is invalid")
    for key in ("command_sha256", "stage_sha256", "result_sha256", "verification_sha256", "last_success_sha256"):
        if value[key] is not None and (
            not isinstance(value[key], str) or SHA_RE.fullmatch(value[key]) is None
        ):
            raise DispatchError("dispatch digest is invalid")
    for key in ("command_identity", "stage_identity", "result_identity", "verification_identity", "last_success_identity"):
        identity = value[key]
        if identity is not None and (
            not isinstance(identity, list) or len(identity) != 5
            or any(type(item) is not int or item < 0 for item in identity)
        ):
            raise DispatchError("dispatch identity is invalid")
    current_result = [value["result_path"], value["result_sha256"], value["result_identity"]]
    if any(item is None for item in current_result) != all(item is None for item in current_result):
        raise DispatchError("dispatch result binding is incomplete")
    if value["candidate_recognized"] != all(item is not None for item in current_result):
        raise DispatchError("dispatch candidate result binding is inconsistent")
    inaccessible_candidate = bool(
        value["candidate_recognized"] and not value["result_available"]
    )
    if value["candidate_recognized"] and value["failure_stage"] == "binding_failure" and value["result_available"]:
        raise DispatchError("dispatch binding failure cannot advertise a result")
    if inaccessible_candidate and not (
        value["failure_stage"] == "binding_failure"
        and value["status"] == "failed"
        and value["reason"] == "status_unavailable"
        and not value["resume_available"]
        and not value["continue_available"]
        and value["driver_disposition"] == "unreviewed"
        and value["next_action"] == "blocked"
        and value["next_action_command"] is None
    ):
        raise DispatchError("dispatch inaccessible candidate state is inconsistent")
    if value["status"] in TERMINAL:
        if value["finished_epoch"] is None or value["exit_code"] is None:
            raise DispatchError("terminal dispatch state is incomplete")
    if value["schema_version"] == 5 and value["workflow"] != "legacy":
        if value["phase"] not in LIFECYCLE_PHASES:
            raise DispatchError("dispatch lifecycle phase is invalid")
        if value["assurance"] not in {"pending", "verified", "partially_verified", "rejected", "blocked"}:
            raise DispatchError("dispatch lifecycle assurance is invalid")
        if inaccessible_candidate and (
            value["phase"] != "blocked" or value["assurance"] != "blocked"
        ):
            raise DispatchError("dispatch inaccessible candidate lifecycle is invalid")
        if value["continue_available"] and not (
            value["assurance"] == "pending"
            and value["candidate_recognized"]
            and value["candidate_source"] != "provider_cancelled"
            and value["cycle"] < value["max_cycles"]
            and value["status"] in {"succeeded", "failed"}
            and value["phase"] in {"awaiting-verification", "repair-failed"}
        ):
            raise DispatchError("dispatch continuation availability is invalid")
        if value["assurance"] != "pending" and value["phase"] not in {"completed", "blocked"}:
            raise DispatchError("terminal dispatch assurance has an invalid phase")
        if value["status"] == "orphaned" and (
            value["assurance"] != "pending"
            or value["phase"] in {"completed", "blocked"}
            or value["resume_available"] or value["continue_available"]
        ):
            raise DispatchError("orphaned dispatch state must remain preserve-only")
    elif value["schema_version"] == 5 and (value["phase"] is not None or value["assurance"] is not None or value["continue_available"]):
        raise DispatchError("legacy state has lifecycle status")
    elif value["schema_version"] != 5 and value["workflow"] == "project":
        if value["phase"] not in {
            "dispatching", "awaiting-verification", "repairing", "completed",
            "blocked", "provider-failed", "repair-failed",
        } or value["assurance"] not in {"pending", "verified", "partially_verified", "blocked"}:
            raise DispatchError("legacy project lifecycle state is invalid")
    elif value["schema_version"] != 5 and (
        value["phase"] is not None or value["assurance"] is not None or value["continue_available"]
    ):
        raise DispatchError("legacy non-project state has lifecycle status")
    summary = value["check_summary"]
    if summary is not None and (
        not isinstance(summary, str) or not (1 <= len(summary) <= MAX_CHECK_SUMMARY)
        or any(ch in summary for ch in "\x00\r\n")
    ):
        raise DispatchError("dispatch check summary is invalid")
    counts = value["check_counts"]
    if not isinstance(counts, dict) or set(counts) != {"passed", "failed", "advisory", "missing"} or any(
        type(item) is not int or not (0 <= item <= MAX_CHECK_ITEMS) for item in counts.values()
    ):
        raise DispatchError("dispatch check counts are invalid")
    present = [value["verification_path"], value["verification_sha256"], value["verification_identity"]]
    if any(item is None for item in present) != all(item is None for item in present):
        raise DispatchError("dispatch verification binding is incomplete")
    prior = [value["last_success_path"], value["last_success_sha256"], value["last_success_identity"]]
    if any(item is None for item in prior) != all(item is None for item in prior):
        raise DispatchError("dispatch prior result binding is incomplete")
    boundary = value["project_boundary"]
    if value["workflow"] == "project":
        if not isinstance(boundary, dict) or set(boundary) != {"kind", "identity", "sha256"}:
            raise DispatchError("project boundary binding is invalid")
        if boundary["kind"] != "file" or not isinstance(boundary["identity"], list) or len(boundary["identity"]) != 5:
            raise DispatchError("project boundary identity is invalid")
        if not isinstance(boundary["sha256"], str) or SHA_RE.fullmatch(boundary["sha256"]) is None:
            raise DispatchError("project boundary marker digest is invalid")
    elif boundary is not None:
        raise DispatchError("non-project state has a boundary binding")
    return value


def initial_state(
    command: dict[str, Any], origin: str, attempt: int, *, command_sha: str,
    command_identity: tuple[int, int, int, int, int], stage_sha: str | None,
    stage_identity: tuple[int, int, int, int, int] | None,
    project_boundary: dict[str, Any] | None = None,
    schema_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    workflow = command.get("workflow", "legacy")
    max_cycles = command.get("max_cycles", 1)
    return {
        "schema_version": 5,
        "kind": "agy-worker-dispatch-state",
        "sequence": 1,
        "previous_state_sha256": None,
        "job_id": command["job_id"],
        "status": "queued",
        "attempt": attempt,
        "attempt_origin": origin,
        "reason": None,
        "exit_code": None,
        "controller_pid": None,
        "workdir": command["workdir"],
        "created_epoch": now,
        "started_epoch": None,
        "updated_epoch": now,
        "finished_epoch": None,
        "elapsed_seconds": 0.0,
        "progress_count": 0,
        "last_progress_epoch": None,
        "notice_count": 0,
        "hard_seconds": float(command["hard_seconds"]),
        "max_seconds": float(command["max_seconds"]),
        "idle_seconds": float(command["idle_seconds"]),
        "attempt_base_elapsed": 0.0,
        "cancel_requested": False,
        "conversation_id": None,
        "resume_available": False,
        "remote_cancel_unverified": False,
        "result_path": None,
        "stream_path": None,
        "stderr_path": None,
        "agy_returncode": None,
        "limit_kind": None,
        "command_sha256": command_sha,
        "command_identity": list(command_identity),
        "stage_sha256": stage_sha,
        "stage_identity": None if stage_identity is None else list(stage_identity),
        "result_sha256": None,
        "result_identity": None,
        "workflow": workflow,
        "max_cycles": max_cycles,
        "cycle": attempt,
        "phase": "dispatching" if workflow != "legacy" else None,
        "assurance": "pending" if workflow != "legacy" else None,
        "check_summary": None,
        "check_counts": {"passed": 0, "failed": 0, "advisory": 0, "missing": 0},
        "verification_path": None,
        "verification_sha256": None,
        "verification_identity": None,
        "continue_available": False,
        "last_success_path": None,
        "last_success_sha256": None,
        "last_success_identity": None,
        "project_boundary": (
            project_boundary if project_boundary is not None
            else _project_boundary(command["workdir"]) if workflow == "project"
            else None
        ),
        "provider_retry_after_seconds": None,
        "provider_retry_observed_epoch": None,
        "candidate_recognized": False,
        "candidate_source": "none",
        "result_available": False,
        "worktree_reconciliation": "not_applicable",
        "worktree_changes_present": None,
        "worktree_changed_since_dispatch": None,
        "driver_disposition": "not_applicable",
        "failure_stage": None,
        "last_activity": None,
        "next_action": "wait",
        "next_action_command": None,
        "worktree_baseline": _worktree_snapshot(command["workdir"]),
        "provider_schema_sha256": None if schema_bindings is None else schema_bindings["provider_schema_sha256"],
        "provider_schema_identity": None if schema_bindings is None else schema_bindings["provider_schema_identity"],
        "canonical_schema_sha256": None if schema_bindings is None else schema_bindings["canonical_schema_sha256"],
        "canonical_schema_identity": None if schema_bindings is None else schema_bindings["canonical_schema_identity"],
        "candidate_worktree_sha256": None,
        "candidate_worktree_entries": None,
    }


def _upgrade_legacy_state(
    state: dict[str, Any], command: dict[str, Any],
) -> dict[str, Any]:
    """Prepare an old readable snapshot for one atomic, approved v5 write."""
    if state["schema_version"] == 5:
        return state
    value = dict(state)
    if value["phase"] == "provider-failed":
        value["phase"] = "attempt-failed"
    if value["workflow"] != "legacy":
        if value["attempt_origin"] == "conversation-continue" and value["status"] == "failed":
            value["phase"] = "repair-failed"
        elif value["candidate_recognized"]:
            value["phase"] = "awaiting-verification"
        elif value["status"] == "failed":
            value["phase"] = "attempt-failed"
        elif value["phase"] is None:
            value["phase"] = "dispatching"
        if value["assurance"] is None:
            value["assurance"] = "pending"
    snapshot = _worktree_snapshot(command["workdir"])
    if snapshot is None:
        raise DispatchError("legacy worktree cannot be bound")
    value.update(_schema_bindings(command))
    value.update({
        "schema_version": 5,
        "worktree_baseline": snapshot,
        "worktree_reconciliation": "available",
        "worktree_changes_present": snapshot["entries"] > 0,
        "worktree_changed_since_dispatch": False,
        "resume_available": bool(
            value["conversation_id"] and not value["candidate_recognized"]
            and value["status"] == "failed"
        ),
    })
    if value["candidate_recognized"]:
        value["candidate_worktree_sha256"] = snapshot["sha256"]
        value["candidate_worktree_entries"] = snapshot["entries"]
        value["driver_disposition"] = "unreviewed"
        value["next_action"] = "driver_review"
    value["continue_available"] = bool(
        value["workflow"] != "legacy"
        and value["candidate_recognized"]
        and value["candidate_source"] != "provider_cancelled"
        and value["conversation_id"]
        and value["status"] in {"succeeded", "failed"}
        and value["phase"] in {"awaiting-verification", "repair-failed"}
        and value["attempt"] < value["max_cycles"]
        and float(value["elapsed_seconds"]) < float(value["max_seconds"])
    )
    validate_state(value)
    return value


def _transition_locked(job: Path, state: dict[str, Any], prior_raw: bytes, updates: dict[str, Any]) -> tuple[dict[str, Any], bytes, str]:
    current, _info = read_regular(job / STATE_NAME, MAX_STATE_BYTES, "dispatch state")
    if current != prior_raw:
        raise DispatchError("dispatch state changed before transition")
    value = dict(state)
    value.update(updates)
    if value["schema_version"] in {1, 3, 4}:
        command = _load_bound_command(job, state, stage_readonly=False)
        value = _upgrade_legacy_state(value, command)
    value["sequence"] = state["sequence"] + 1
    value["previous_state_sha256"] = digest(prior_raw)
    value["updated_epoch"] = time.time()
    validate_state(value)
    raw, sha = write_atomic(job, STATE_NAME, value)
    return value, raw, sha


def transition(job: Path, state: dict[str, Any], prior_raw: bytes, updates: dict[str, Any]) -> tuple[dict[str, Any], bytes, str]:
    with state_lock(job):
        return _transition_locked(job, state, prior_raw, updates)


def public_status(value: dict[str, Any], sha: str) -> dict[str, Any]:
    now = time.time()
    elapsed = float(value["elapsed_seconds"])
    if value["status"] in {"running", "cancel-requested"} and value["started_epoch"] is not None:
        elapsed = max(
            elapsed,
            float(value["attempt_base_elapsed"]) + max(0.0, now - float(value["started_epoch"])),
        )
    last_age = None
    if value["last_progress_epoch"] is not None:
        last_age = max(0.0, now - value["last_progress_epoch"])
    retry_remaining = None
    if value["provider_retry_after_seconds"] is not None:
        retry_remaining = max(
            0,
            int(math.ceil(
                value["provider_retry_after_seconds"]
                - max(0.0, now - value["provider_retry_observed_epoch"])
            )),
        )
    return {
        "attempt": value["attempt"],
        "attempt_origin": value["attempt_origin"],
        "assurance": value["assurance"],
        "check_counts": value["check_counts"],
        "check_summary": value["check_summary"],
        "cycle": value["cycle"],
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": value["exit_code"],
        "hard_seconds": value["hard_seconds"],
        # Kept as a deprecated compatibility hint.  It says nothing about a clean
        # worktree and must not be used as an acceptance decision.
        "has_prior_candidate": bool(value["result_path"] or value["last_success_path"]),
        "candidate_recognized": value["candidate_recognized"],
        "candidate_source": value["candidate_source"],
        "result_available": value["result_available"],
        "worktree_reconciliation": value["worktree_reconciliation"],
        "worktree_changes_present": value["worktree_changes_present"],
        "worktree_changed_since_dispatch": value["worktree_changed_since_dispatch"],
        "driver_disposition": value["driver_disposition"],
        "failure_stage": value["failure_stage"],
        "last_activity": value["last_activity"],
        "next_action": value["next_action"],
        "next_action_command": value["next_action_command"],
        "job_id": value["job_id"],
        "last_progress_age_seconds": None if last_age is None else round(last_age, 3),
        "limit_kind": value["limit_kind"],
        "max_seconds": value["max_seconds"],
        "max_cycles": value["max_cycles"],
        "notice_count": value["notice_count"],
        "progress_count": value["progress_count"],
        "phase": value["phase"],
        "reason": value["reason"],
        "retry_after_seconds": retry_remaining,
        "remote_cancel_unverified": value["remote_cancel_unverified"],
        "resume_available": value["resume_available"],
        "continue_available": value["continue_available"],
        "state_sha256": sha,
        "status": value["status"],
        "workflow": value["workflow"],
    }


def print_json(value: Any) -> None:
    sys.stdout.buffer.write(canonical(value))
    sys.stdout.buffer.flush()


def _attempt_paths(job: Path, attempt: int) -> tuple[Path, Path, Path]:
    if attempt == 1:
        return job / "stream.ndjson", job / "stderr.txt", job / "envelope.json"
    prefix = f"attempt-{attempt:03d}"
    return job / f"{prefix}.stream.ndjson", job / f"{prefix}.stderr.txt", job / f"{prefix}.envelope.json"


def _ensure_new_private(path: Path) -> int:
    return os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )


def _stage(command: dict[str, Any], readonly: bool) -> None:
    if command["stage_dir"] is None:
        return
    directory = Path(command["stage_dir"])
    source = Path(command["stage_file"])
    directory.chmod(0o555 if readonly else 0o700)
    source.chmod(0o444 if readonly else 0o600)


def _bound_stage(
    command: dict[str, Any], *, readonly: bool,
) -> tuple[str | None, tuple[int, int, int, int, int] | None]:
    if command["stage_file"] is None:
        return None, None
    path = Path(command["stage_file"])
    expected_mode = 0o444 if readonly else 0o600
    raw, info = read_regular(
        path, MAX_COMMAND_BYTES, "staged prompt", allowed_modes=(expected_mode,)
    )
    if stat.S_IMODE(info.st_mode) != expected_mode:
        raise DispatchError("staged prompt mode is invalid")
    directory = Path(command["stage_dir"])
    directory_info = directory.lstat()
    expected_directory_mode = 0o555 if readonly else 0o700
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or stat.S_ISLNK(directory_info.st_mode)
        or directory_info.st_uid != os.getuid()
        or stat.S_IMODE(directory_info.st_mode) != expected_directory_mode
    ):
        raise DispatchError("staged prompt directory is invalid")
    return digest(raw), _identity(info)


def _validate_verification(value: Any) -> dict[str, Any]:
    v1_fields = {"schema_version", "summary", "passed_checks", "failed_checks", "advisory_checks", "missing_checks"}
    v2_fields = v1_fields | {
        "candidate_sha256", "coverage", "verified_findings", "unresolved_gaps",
        "diff_review_complete",
    }
    if not isinstance(value, dict) or set(value) not in (v1_fields, v2_fields):
        raise DispatchError("verification feedback fields are invalid")
    if value["schema_version"] not in {1, 2} or (value["schema_version"] == 1) != (set(value) == v1_fields):
        raise DispatchError("verification feedback version is invalid")
    summary = value["summary"]
    if not isinstance(summary, str) or not (1 <= len(summary) <= MAX_CHECK_SUMMARY) or any(
        item in summary for item in ("\x00", "\r", "\n")
    ):
        raise DispatchError("verification feedback summary is invalid")
    for key in ("passed_checks", "failed_checks"):
        checks = value[key]
        if not isinstance(checks, list) or len(checks) > MAX_CHECK_ITEMS or any(
            not isinstance(item, str) or not (1 <= len(item) <= MAX_CHECK_LABEL)
            or any(control in item for control in ("\x00", "\r", "\n"))
            for item in checks
        ):
            raise DispatchError("verification feedback checks are invalid")
    for key in ("advisory_checks", "missing_checks"):
        if type(value[key]) is not int or not (0 <= value[key] <= MAX_CHECK_ITEMS):
            raise DispatchError("verification feedback counts are invalid")
    if value["schema_version"] == 2:
        if not isinstance(value["candidate_sha256"], str) or SHA_RE.fullmatch(value["candidate_sha256"]) is None:
            raise DispatchError("verification feedback candidate binding is invalid")
        if value["coverage"] not in {"complete", "partial", "not_assessed", "not_applicable"}:
            raise DispatchError("verification feedback coverage is invalid")
        for key in ("verified_findings", "unresolved_gaps"):
            if type(value[key]) is not int or not (0 <= value[key] <= MAX_CHECK_ITEMS):
                raise DispatchError("verification feedback evidence counts are invalid")
        if type(value["diff_review_complete"]) is not bool:
            raise DispatchError("verification feedback diff review is invalid")
    if len(canonical(value)) > MAX_VERIFICATION_BYTES:
        raise DispatchError("verification feedback canonical bytes are oversized")
    return value


def _verification_from_stdin() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_VERIFICATION_BYTES + 1)
    if len(raw) > MAX_VERIFICATION_BYTES:
        raise DispatchError("verification feedback is oversized")
    return _validate_verification(parse_json(raw, "verification feedback"))


def _verification_counts(value: dict[str, Any]) -> dict[str, int]:
    return {
        "passed": len(value["passed_checks"]),
        "failed": len(value["failed_checks"]),
        "advisory": value["advisory_checks"],
        "missing": value["missing_checks"],
    }


def _verification_is_verified(value: dict[str, Any], workflow: str) -> bool:
    counts = _verification_counts(value)
    if value["schema_version"] != 2 or counts["failed"] or counts["missing"]:
        return False
    if workflow == "explore":
        return value["coverage"] == "complete" and value["unresolved_gaps"] == 0
    return counts["passed"] >= 1 and value["diff_review_complete"]


def _require_current_candidate_verification(value: dict[str, Any], state: dict[str, Any]) -> None:
    """V1 is readable for compatibility, but never authorizes a lifecycle write."""
    if value["schema_version"] != 2:
        raise DispatchError("verification v2 is required for candidate disposition")
    if (
        not state["candidate_recognized"] or not state["result_available"]
        or state["result_sha256"] is None
    ):
        raise DispatchError("verification has no current recognized candidate")
    if value["candidate_sha256"] != state["result_sha256"]:
        raise DispatchError("verification candidate binding is stale")


def _write_verification(job: Path, label: str, value: dict[str, Any]) -> tuple[Path, str, tuple[int, int, int, int, int]]:
    directory = job / "continue-staged"
    if directory.exists() or directory.is_symlink():
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) not in {0o700, 0o555}
        ):
            raise DispatchError("verification staging directory is invalid")
        directory.chmod(0o700)
    else:
        directory.mkdir(mode=0o700)
    path = directory / f"{label}.json"
    raw = canonical(value)
    if len(raw) > MAX_VERIFICATION_BYTES:
        raise DispatchError("verification feedback canonical bytes are oversized")
    descriptor = -1
    created_identity: tuple[int, int, int, int, int] | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created_info = os.fstat(descriptor)
        if not stat.S_ISREG(created_info.st_mode) or created_info.st_nlink != 1:
            raise DispatchError("verification feedback staging identity is invalid")
        created_identity = _identity(created_info)
    except DispatchError:
        raise
    except OSError as exc:
        raise DispatchError("verification staging path is unavailable") from exc
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise DispatchError("verification feedback staging write failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        path.chmod(0o400)
        directory.chmod(0o700)
        bound, info = read_regular(path, MAX_VERIFICATION_BYTES, "verification feedback", allowed_modes=(0o400,))
        if bound != raw:
            raise DispatchError("verification feedback changed during staging")
        return path, digest(bound), _identity(info)
    except (OSError, DispatchError):
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if created_identity is None:
            with contextlib.suppress(OSError):
                fallback_info = path.lstat()
                if stat.S_ISREG(fallback_info.st_mode) and fallback_info.st_nlink == 1:
                    created_identity = _identity(fallback_info)
        _discard_new_verification(path, created_identity)
        raise


def _discard_new_verification(path: Path | None, identity: tuple[int, int, int, int, int] | None) -> None:
    if path is None or identity is None:
        return
    try:
        parent = path.parent
        parent_info = parent.lstat()
        if (
            not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
            or parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) not in {0o700, 0o555}
        ):
            return
        # A prior runtime used 0555 for this directory.  Recover that exact
        # owner-private directory, but publish new staging in cleanup-friendly
        # 0700 mode.
        parent.chmod(0o700)
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and _identity(info) == identity and info.st_nlink == 1:
            path.unlink()
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError:
        pass


def _bound_verification(job: Path, state: dict[str, Any]) -> Path | None:
    path_text = state["verification_path"]
    if path_text is None:
        return None
    path = Path(path_text)
    expected = job / "continue-staged" / f"cycle-{state['cycle']:03d}.json"
    if state["attempt_origin"] != "conversation-continue" or path != expected:
        raise DispatchError("verification feedback path is not bound to this continuation")
    raw, info = read_regular(path, MAX_VERIFICATION_BYTES, "verification feedback", allowed_modes=(0o400,))
    if digest(raw) != state["verification_sha256"] or list(_identity(info)) != state["verification_identity"]:
        raise DispatchError("verification feedback binding changed")
    _require_current_candidate_verification(
        _validate_verification(parse_json(raw, "verification feedback")), state,
    )
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise DispatchError("verification staging directory changed")
    return path


def _project_boundary(workdir: str) -> dict[str, Any]:
    root = os.path.realpath(workdir)
    if root != workdir:
        raise DispatchError("project worktree is no longer canonical")
    marker = Path(root) / ".git"
    descriptor = -1
    try:
        descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise DispatchError("project worktree has no Git marker") from exc
    try:
        marker_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(marker_info.st_mode)
            or marker_info.st_uid != os.getuid()
            or marker_info.st_nlink != 1
        ):
            raise DispatchError("project Git marker must be one owner-owned linked-worktree file")
        if marker_info.st_size > 4096:
            raise DispatchError("project Git marker is oversized")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4097 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 4096:
                raise DispatchError("project Git marker is oversized")
        after = os.fstat(descriptor)
        if after.st_size > 4096:
            raise DispatchError("project Git marker is oversized")
    except DispatchError:
        raise
    except OSError as exc:
        raise DispatchError("project Git marker is unavailable") from exc
    finally:
        os.close(descriptor)
    try:
        named = marker.lstat()
    except OSError as exc:
        raise DispatchError("project Git marker identity changed") from exc
    raw = b"".join(chunks)
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_uid != os.getuid()
        or after.st_nlink != 1
        or not stat.S_ISREG(named.st_mode)
        or named.st_uid != os.getuid()
        or named.st_nlink != 1
        or _identity(marker_info) != _identity(after)
        or _identity(after) != _identity(named)
        or marker_info.st_size != after.st_size
        or after.st_size != named.st_size
        or len(raw) != after.st_size
        or marker_info.st_mtime_ns != after.st_mtime_ns
        or marker_info.st_ctime_ns != after.st_ctime_ns
        or after.st_mtime_ns != named.st_mtime_ns
        or after.st_ctime_ns != named.st_ctime_ns
    ):
        raise DispatchError("project Git marker identity changed")
    marker_record: dict[str, Any] = {
        "kind": "file", "identity": list(_identity(after)), "sha256": digest(raw),
    }
    pending = [root]
    count = 0
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if current == root and entry.name == ".git":
                        continue
                    count += 1
                    if count > MAX_BOUNDARY_ENTRIES:
                        raise DispatchError("project worktree boundary scan is too large")
                    if entry.is_symlink():
                        # `mktemp` commonly returns /var/... on macOS while
                        # realpath canonicalizes the worktree to /private/var.
                        # Resolve before containment so an internal link is not
                        # falsely treated as an escape; chained escapes remain
                        # outside the canonical root.
                        resolved = os.path.realpath(entry.path)
                        try:
                            contained = os.path.commonpath([root, resolved]) == root
                        except ValueError:
                            contained = False
                        if not contained:
                            raise DispatchError("project worktree has an outward symlink")
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
        except OSError as exc:
            raise DispatchError("project worktree cannot be inspected") from exc
    return marker_record


def _worktree_snapshot(workdir: str) -> dict[str, Any] | None:
    """Hash bounded Git-visible artefacts without retaining or publishing names.

    Git supplies the tracked, deleted, untracked, and ignored path set.  Every
    component is then opened relative to a no-follow root descriptor, so a
    symlink contributes its own lstat and target bytes but is never traversed.
    """
    root_fd = -1
    try:
        root_fd = os.open(
            os.fsencode(workdir),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            return None
        commands = (
            ["git", "-C", workdir, "status", "--porcelain=v1", "-z", "--ignored", "--untracked-files=all"],
            ["git", "-C", workdir, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            ["git", "-C", workdir, "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
        )
        outputs: list[bytes] = []
        total = 0
        for argv in commands:
            completed = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=False, timeout=5.0,
            )
            if completed.returncode != 0:
                return None
            total += len(completed.stdout)
            if total > MAX_STREAM_BYTES:
                return None
            outputs.append(completed.stdout)
        status_raw, visible_raw, ignored_raw = outputs
        if any(raw and not raw.endswith(b"\0") for raw in outputs):
            return None
        dirty_entries = status_raw.count(b"\0")
        paths = set(visible_raw.split(b"\0")[:-1]) | set(ignored_raw.split(b"\0")[:-1])
        if dirty_entries > MAX_BOUNDARY_ENTRIES or len(paths) > MAX_BOUNDARY_ENTRIES:
            return None
        observation = hashlib.sha256()
        observation.update(b"agy-worker-worktree-v2\0")
        observation.update(len(status_raw).to_bytes(8, "big"))
        observation.update(status_raw)
        content_bytes = 0
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        for relative in sorted(paths):
            parts = relative.split(b"/")
            if (
                not relative or relative.startswith(b"/")
                or any(part in {b"", b".", b".."} for part in parts)
                or parts[0] == b".git"
            ):
                return None
            observation.update(len(relative).to_bytes(8, "big"))
            observation.update(relative)
            parent_fd = os.dup(root_fd)
            try:
                for component in parts[:-1]:
                    next_fd = os.open(
                        component, os.O_RDONLY | directory | nofollow,
                        dir_fd=parent_fd,
                    )
                    os.close(parent_fd)
                    parent_fd = next_fd
                name = parts[-1]
                try:
                    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    observation.update(b"missing\0")
                    continue
                metadata = (
                    before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
                    before.st_uid, before.st_gid, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns,
                )
                observation.update(canonical(list(metadata)))
                if stat.S_ISLNK(before.st_mode):
                    target = os.readlink(name, dir_fd=parent_fd)
                    target_raw = os.fsencode(target)
                    content_bytes += len(target_raw)
                    if content_bytes > MAX_STREAM_BYTES:
                        return None
                    after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if metadata != (
                        after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                        after.st_uid, after.st_gid, after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns,
                    ):
                        return None
                    observation.update(b"symlink\0")
                    observation.update(len(target_raw).to_bytes(8, "big"))
                    observation.update(target_raw)
                elif stat.S_ISREG(before.st_mode):
                    file_fd = os.open(name, os.O_RDONLY | nofollow, dir_fd=parent_fd)
                    try:
                        opened = os.fstat(file_fd)
                        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                            return None
                        content = hashlib.sha256()
                        while True:
                            piece = os.read(file_fd, 65536)
                            if not piece:
                                break
                            content_bytes += len(piece)
                            if content_bytes > MAX_STREAM_BYTES:
                                return None
                            content.update(piece)
                        after = os.fstat(file_fd)
                    finally:
                        os.close(file_fd)
                    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    after_metadata = (
                        after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                        after.st_uid, after.st_gid, after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns,
                    )
                    named_metadata = (
                        named.st_dev, named.st_ino, named.st_mode, named.st_nlink,
                        named.st_uid, named.st_gid, named.st_size,
                        named.st_mtime_ns, named.st_ctime_ns,
                    )
                    if metadata != after_metadata or after_metadata != named_metadata:
                        return None
                    observation.update(b"file\0")
                    observation.update(content.digest())
                else:
                    observation.update(b"special\0")
            finally:
                os.close(parent_fd)
        return {"sha256": observation.hexdigest(), "entries": dirty_entries}
    except (OSError, subprocess.TimeoutExpired, OverflowError):
        return None
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _reconcile_worktree(workdir: str, baseline: dict[str, Any] | None) -> dict[str, Any]:
    current = _worktree_snapshot(workdir)
    if current is None or baseline is None:
        return {
            "worktree_reconciliation": "unavailable",
            "worktree_changes_present": None,
            "worktree_changed_since_dispatch": None,
        }
    return {
        "worktree_reconciliation": "available",
        "worktree_changes_present": current["entries"] > 0,
        "worktree_changed_since_dispatch": current["sha256"] != baseline["sha256"],
    }


def _schema_binding(path: Path) -> tuple[str, tuple[int, int, int, int, int]]:
    """Bind a schema as dispatch input without accepting a symlink swap."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise DispatchError("dispatch schema is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_size > 1024 * 1024:
            raise DispatchError("dispatch schema is invalid")
        raw = b""
        while len(raw) <= 1024 * 1024:
            piece = os.read(descriptor, 65536)
            if not piece:
                break
            raw += piece
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        named = path.lstat()
    except OSError as exc:
        raise DispatchError("dispatch schema identity changed") from exc
    if len(raw) > 1024 * 1024 or _identity(before) != _identity(after) or _identity(after) != _identity(named):
        raise DispatchError("dispatch schema identity changed")
    return digest(raw), _identity(after)


def _schema_paths(command: dict[str, Any]) -> tuple[Path, Path] | None:
    argv = command["argv"]
    if "--json-schema" not in argv:
        return None
    if argv.count("--json-schema") != 1:
        raise DispatchError("dispatch schema argument is invalid")
    index = argv.index("--json-schema")
    if index + 1 >= len(argv):
        raise DispatchError("dispatch schema argument is invalid")
    return Path(argv[index + 1]), Path(__file__).parent.parent / "schemas" / "worker-result.schema.json"


def _schema_bindings(command: dict[str, Any]) -> dict[str, Any]:
    paths = _schema_paths(command)
    if paths is None:
        return {
            "provider_schema_sha256": None, "provider_schema_identity": None,
            "canonical_schema_sha256": None, "canonical_schema_identity": None,
        }
    provider_sha, provider_identity = _schema_binding(paths[0])
    canonical_sha, canonical_identity = _schema_binding(paths[1])
    return {
        "provider_schema_sha256": provider_sha, "provider_schema_identity": list(provider_identity),
        "canonical_schema_sha256": canonical_sha, "canonical_schema_identity": list(canonical_identity),
    }


def _bound_schemas(command: dict[str, Any], state: dict[str, Any]) -> tuple[Path, Path]:
    paths = _schema_paths(command)
    if paths is None:
        raise DispatchError("dispatch schema argument is unavailable")
    expected = _schema_bindings(command)
    for key, value in expected.items():
        if state[key] != value:
            raise DispatchError("dispatch schema binding changed")
    return paths


def _bound_candidate_worktree(state: dict[str, Any], command: dict[str, Any]) -> None:
    """Reject post-review worktree drift before a continuation or final disposition."""
    expected_sha = state["candidate_worktree_sha256"]
    expected_entries = state["candidate_worktree_entries"]
    current = _worktree_snapshot(command["workdir"])
    if current is None or expected_sha is None or expected_entries is None:
        raise DispatchError("candidate worktree reconciliation is unavailable")
    if current["sha256"] != expected_sha or current["entries"] != expected_entries:
        raise DispatchError("candidate worktree binding changed")


def _load_bound_command(
    job: Path, state: dict[str, Any], *, stage_readonly: bool,
) -> dict[str, Any]:
    command, raw, identity = load_command(job)
    if digest(raw) != state["command_sha256"] or list(identity) != state["command_identity"]:
        raise DispatchError("dispatch command binding changed")
    stage_sha, stage_identity = _bound_stage(command, readonly=stage_readonly)
    if stage_sha != state["stage_sha256"] or (
        stage_identity is not None and list(stage_identity[:4]) != state["stage_identity"][:4]
    ) or (stage_identity is None) != (state["stage_identity"] is None):
        raise DispatchError("staged prompt binding changed")
    return command


def _terminate(process: subprocess.Popen[bytes]) -> int:
    # The leader can exit while a descendant still owns the stream or performs a
    # late side effect.  Signal the recorded session even in that case.
    signalled = False
    try:
        os.killpg(process.pid, signal.SIGTERM)
        signalled = True
    except (ProcessLookupError, PermissionError):
        pass
    if signalled:
        deadline = time.monotonic() + TERM_GRACE
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                pass
            time.sleep(0.02)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return process.returncode


def _event(line: bytes) -> tuple[bool, str | None, str | None]:
    if not line or len(line) > MAX_EVENT_BYTES:
        return False, None, None
    try:
        value = json.loads(
            line.decode("utf-8", "strict"), object_pairs_hook=_duplicates,
            parse_constant=_invalid_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, DispatchError):
        return False, None, None
    if not isinstance(value, dict):
        return False, None, None
    event = value.get("event")
    if event not in {"init", "step_update", "result"}:
        return False, None, None
    if event == "init" and not isinstance(value.get("init"), dict):
        return False, None, None
    if event == "step_update" and not isinstance(value.get("step_update"), dict):
        return False, None, None
    if event == "result" and not isinstance(value.get("result"), dict):
        return False, None, None
    conversation = value.get("conversation_id")
    if conversation is None and event == "result":
        conversation = value["result"].get("conversation_id")
    if conversation is not None and (
        not isinstance(conversation, str) or CONVERSATION_RE.fullmatch(conversation) is None
    ):
        conversation = None
    return True, conversation, event


def _classify_stderr(path: Path, version: str, returncode: int) -> str:
    if returncode == 0:
        return "empty_output"
    try:
        raw = path.read_bytes()
    except OSError:
        return "agy_failed_unclassified"
    lines = {line.strip() for line in raw.splitlines() if line.strip()}
    if any(b"permission that headless mode cannot prompt for" in line for line in lines):
        return "permission_required"
    signatures = EXACT_FAILURE_LINES.get(version, {})
    matches = {reason for signature, reason in signatures.items() if signature in lines}
    return matches.pop() if len(matches) == 1 else "agy_failed_unclassified"


def _terminal_result(stream: Path, *, strict: bool = False) -> dict[str, Any] | None:
    result: dict[str, Any] | None = None
    init_conversation: str | None = None
    saw_init = False
    saw_terminal = False
    try:
        with stream.open("rb") as handle:
            for raw in handle:
                if len(raw) > MAX_EVENT_BYTES or not raw.endswith(b"\n"):
                    return None
                try:
                    event = json.loads(
                        raw.decode("utf-8", "strict"), object_pairs_hook=_duplicates,
                        parse_constant=_invalid_json_constant,
                    )
                except (UnicodeError, json.JSONDecodeError, DispatchError):
                    if strict:
                        return None
                    continue
                if not isinstance(event, dict):
                    if strict:
                        return None
                    continue
                kind = event.get("event")
                structurally_valid = (
                    (kind == "init" and isinstance(event.get("init"), dict))
                    or (kind == "step_update" and isinstance(event.get("step_update"), dict))
                    or (kind == "result" and isinstance(event.get("result"), dict))
                )
                if not structurally_valid:
                    if strict:
                        return None
                    continue
                if saw_terminal:
                    return None
                if kind == "init":
                    if saw_init:
                        return None
                    init_conversation = event.get("conversation_id")
                    if strict and init_conversation is not None and (
                        not isinstance(init_conversation, str)
                        or CONVERSATION_RE.fullmatch(init_conversation) is None
                    ):
                        return None
                    saw_init = True
                elif not saw_init:
                    return None
                if kind == "result":
                    result_conversation = event["result"].get("conversation_id")
                    if strict and result_conversation is not None and (
                        not isinstance(result_conversation, str)
                        or CONVERSATION_RE.fullmatch(result_conversation) is None
                    ):
                        return None
                    if strict and init_conversation is not None and result_conversation is not None and result_conversation != init_conversation:
                        return None
                    saw_terminal = True
                    result = event["result"]
    except OSError:
        return None
    if not saw_terminal or not isinstance(result, dict):
        return None
    return result


def _quota_terminal_failure(stream: Path, version: str) -> tuple[str, int | None] | None:
    if version != "1.1.13":
        return None
    result = _terminal_result(stream, strict=True)
    if result is None or set(result) != QUOTA_RESULT_FIELDS:
        return None
    if result.get("status") != "ERROR" or result.get("response") != "":
        return None
    conversation = result.get("conversation_id")
    if not isinstance(conversation, str) or CONVERSATION_RE.fullmatch(conversation) is None:
        return None
    if (
        type(result.get("duration_seconds")) not in (int, float)
        or not math.isfinite(result["duration_seconds"])
        or result["duration_seconds"] < 0
    ):
        return None
    if type(result.get("num_turns")) is not int or result["num_turns"] < 0:
        return None
    if not isinstance(result.get("usage"), dict) or not isinstance(result.get("json_schema"), dict):
        return None
    error = result.get("error")
    if not isinstance(error, str) or len(error) > 256:
        return None
    match = QUOTA_ERROR_1_1_13_RE.fullmatch(error)
    if match is None:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    retry = hours * 3600 + minutes * 60 + seconds
    if minutes >= 60 or seconds >= 60 or not (1 <= retry <= MAX_PROVIDER_RETRY_SECONDS):
        retry = None
    return "provider_quota_exhausted", retry


def _validate_terminal_envelope(
    stream: Path, envelope: Path, provider_schema: Path, canonical_schema: Path,
) -> tuple[tuple[str, tuple[int, int, int, int, int]] | None, str | None, str | None]:
    """Keep framing, provider status, extraction, and canonical validation distinct."""
    result = _terminal_result(stream, strict=True)
    if result is None:
        return None, None, "framing"
    outer_status = result.get("status")
    if not isinstance(outer_status, str) or outer_status.upper() not in {"SUCCESS", "ERROR", "CANCELED", "CANCELLED"}:
        return None, None, "outer_status"
    outer_status = "CANCELLED" if outer_status.upper() in {"CANCELED", "CANCELLED"} else outer_status.upper()
    value = result.get("structured_output")
    if not isinstance(value, dict):
        return None, outer_status, "structured_output"
    # The provider may omit exactly these ergonomic report-only arrays.  Every
    # other required field and every extra field remain schema failures.
    value = dict(value)
    for field in ("commands_run", "tests_run"):
        value.setdefault(field, [])
    raw = json.dumps(value, ensure_ascii=True, indent=2).encode("ascii") + b"\n"
    if len(raw) > 1024 * 1024:
        return None, outer_status, "schema_rejection"
    descriptor = _ensure_new_private(envelope)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    validator = Path(__file__).with_name("validate-envelope.py")
    provider_checked = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(validator), str(provider_schema), str(envelope)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False,
    )
    canonical_checked = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(validator), str(canonical_schema), str(envelope)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False,
    )
    if provider_checked.returncode != 0 or canonical_checked.returncode != 0:
        with contextlib.suppress(OSError):
            envelope.unlink()
        return None, outer_status, "schema_rejection"
    try:
        rebound, info = read_regular(envelope, 1024 * 1024, "dispatch result")
    except DispatchError:
        return None, outer_status, "binding_failure"
    if rebound != raw:
        return None, outer_status, "binding_failure"
    return (digest(raw), _identity(info)), outer_status, None


def controller(job: Path, ownership_fd: int) -> int:
    stop_signal: int | None = None

    def interrupted(number: int, _frame: Any) -> None:
        nonlocal stop_signal
        if stop_signal is None:
            stop_signal = number

    prior_handlers = {
        number: signal.getsignal(number)
        for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    }
    for number in prior_handlers:
        signal.signal(number, interrupted)
    try:
      with inherited_lifecycle_lock(job, ownership_fd):
        state, prior_raw, _sha = read_state_snapshot(job)
        if state["status"] == "cancel-requested" and state["cancel_requested"]:
            transition(job, state, prior_raw, _terminal_projection(
                state, status="cancelled", reason="cancelled",
                exit_code=EXIT_BY_REASON["cancelled"],
                # This SHA-approved local cancellation occurs before provider
                # startup; it is not an unverified remote cancellation.
                remote_cancel_unverified=False,
            ))
            return EXIT_BY_REASON["cancelled"]
        if state["status"] != "queued" or state["cancel_requested"]:
            raise DispatchError("dispatch is not queued")
        feedback: Path | None = None
        schema_paths: tuple[Path, Path] | None = None
        try:
            command = _load_bound_command(job, state, stage_readonly=False)
            schema_paths = _bound_schemas(command, state)
            if state["workflow"] == "project":
                if _project_boundary(command["workdir"]) != state["project_boundary"]:
                    raise DispatchError("project worktree boundary changed")
            if state["attempt_origin"] == "conversation-continue":
                feedback = _bound_verification(job, state)
                if feedback is None:
                    raise DispatchError("project continuation has no verification feedback")
                _bound_candidate_worktree(state, command)
        except (OSError, DispatchError):
            transition(job, state, prior_raw, _terminal_projection(
                state, status="failed", reason="status_unavailable",
                exit_code=EXIT_BY_REASON["status_unavailable"],
                failure_stage="binding_failure",
                allow_continue=False,
            ))
            return EXIT_BY_REASON["status_unavailable"]
        attempt = state["attempt"]
        stream_path, stderr_path, envelope_path = _attempt_paths(job, attempt)
        stdout_fd = -1; stderr_fd = -1
        try:
            stdout_fd = _ensure_new_private(stream_path)
            stderr_fd = _ensure_new_private(stderr_path)
            now = time.time()
            state, prior_raw, _sha = transition(job, state, prior_raw, {
                "status": "running", "controller_pid": os.getpid(),
                "started_epoch": now, "last_progress_epoch": None,
                "stream_path": str(stream_path), "stderr_path": str(stderr_path),
                "next_action": "wait",
            })
            _stage(command, True)
            _load_bound_command(job, state, stage_readonly=True)
            schema_paths = _bound_schemas(command, state)
        except (OSError, DispatchError):
            if stdout_fd >= 0: os.close(stdout_fd)
            if stderr_fd >= 0: os.close(stderr_fd)
            with contextlib.suppress(OSError): _stage(command, False)
            current, current_raw, _current_sha = read_state_snapshot(job)
            transition(job, current, current_raw, _terminal_projection(
                current, status="failed", reason="status_unavailable",
                exit_code=EXIT_BY_REASON["status_unavailable"],
                failure_stage="binding_failure",
                allow_continue=False,
            ))
            return EXIT_BY_REASON["status_unavailable"]
        argv = list(command["argv"])
        if state["attempt_origin"] in {"conversation-resume", "conversation-continue"}:
            conversation = state["conversation_id"]
            if not isinstance(conversation, str):
                raise DispatchError("resume has no conversation")
            print_index = argv.index("--print")
            prefix = ["--conversation", conversation]
            prompt = command["resume_prompt"]
            if state["attempt_origin"] == "conversation-continue":
                if feedback is None:
                    raise DispatchError("project continuation feedback was not prevalidated")
                prefix.extend(["--add-dir", str(feedback.parent)])
                prompt = command["continue_prompt"] + f" Feedback file: '{feedback}'."
            argv[print_index + 1] = prompt
            argv[print_index:print_index] = prefix
        selector = selectors.DefaultSelector()
        process: subprocess.Popen[bytes] | None = None
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        sizes = {"stdout": 0, "stderr": 0}
        heartbeat_mono = time.monotonic()
        started_mono = heartbeat_mono
        next_notice = started_mono + float(command["notice_seconds"])
        reason: str | None = None
        limit_kind: str | None = None
        failure_stage: str | None = None
        saw_init = False
        saw_terminal = False

        def controller_transition(updates: dict[str, Any]) -> bool:
            """Do not turn a concurrent approved control into a controller crash."""
            nonlocal state, prior_raw
            try:
                state, prior_raw, _sha = transition(job, state, prior_raw, updates)
                return True
            except DispatchError as exc:
                if str(exc) != "dispatch state changed before transition":
                    raise
                current, current_raw, _current_sha = read_state_snapshot(job)
                if current["attempt"] != state["attempt"]:
                    raise DispatchError("dispatch attempt changed during control")
                state, prior_raw = current, current_raw
                return False
        try:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=command["workdir"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    preexec_fn=lambda: os.umask(int(command["child_umask"], 8)),
                )
            except OSError:
                # A legacy tier deliberately reaches this point even when agy is
                # absent.  Publish a terminal, sanitized dispatch failure rather
                # than leaking an interpreter traceback or leaving a queued job.
                reason = "agy_failed_unclassified"
                returncode = 127
            else:
                assert process.stdout is not None and process.stderr is not None
                for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, name)
            # Pipe EOF is the completion observation.  Do not poll/reap the leader
            # before process-group closure; its PID reserves the group identifier.
            while process is not None and selector.get_map():
                now_mono = time.monotonic()
                elapsed = float(state["attempt_base_elapsed"]) + now_mono - started_mono
                try:
                    current, current_raw, _current_sha = read_state_snapshot(job)
                except DispatchError:
                    reason = "status_unavailable"
                    break
                if current_raw != prior_raw:
                    if (
                        current["previous_state_sha256"] != digest(prior_raw)
                        or current["sequence"] != state["sequence"] + 1
                        or current["attempt"] != state["attempt"]
                    ):
                        reason = "status_unavailable"
                        break
                    state, prior_raw = current, current_raw
                if state["cancel_requested"] or stop_signal is not None:
                    reason = "cancelled" if stop_signal is None else "interrupted"
                    break
                if elapsed >= state["max_seconds"]:
                    reason, limit_kind = "hard_deadline_exceeded", "max-runtime"
                    break
                if elapsed >= state["hard_seconds"]:
                    reason, limit_kind = "hard_deadline_exceeded", "hard"
                    break
                if now_mono - heartbeat_mono >= float(state["idle_seconds"]):
                    reason, limit_kind = "idle_timeout", "idle"
                    break
                if now_mono >= next_notice:
                    controller_transition({
                        "notice_count": state["notice_count"] + 1,
                        "elapsed_seconds": elapsed,
                    })
                    next_notice += float(command["notice_seconds"])
                    if sys.stderr.isatty():
                        print(
                            f"agy-worker: still running; elapsed={int(elapsed)}s "
                            f"progress={state['progress_count']}", file=sys.stderr, flush=True,
                        )
                events = selector.select(CONTROL_POLL)
                for key, _mask in events:
                    name = key.data
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    sizes[name] += len(chunk)
                    if sizes[name] > MAX_STREAM_BYTES:
                        reason = "output_oversized"
                        break
                    os.write(stdout_fd if name == "stdout" else stderr_fd, chunk)
                    if name == "stdout" and reason is None:
                        buffers[name].extend(chunk)
                        while b"\n" in buffers[name]:
                            line, _, remainder = buffers[name].partition(b"\n")
                            buffers[name] = bytearray(remainder)
                            if len(line) > MAX_EVENT_BYTES:
                                reason = "output_oversized"
                                break
                            valid, conversation, event_kind = _event(line)
                            if valid:
                                if saw_terminal or (event_kind == "init" and saw_init) or (
                                    event_kind != "init" and not saw_init
                                ):
                                    reason = "invalid_envelope"
                                    failure_stage = "framing"
                                    break
                                if event_kind == "init":
                                    saw_init = True
                                elif event_kind == "result":
                                    saw_terminal = True
                                heartbeat_mono = time.monotonic()
                                updates: dict[str, Any] = {
                                    "progress_count": state["progress_count"] + 1,
                                    "last_progress_epoch": time.time(),
                                    "elapsed_seconds": float(state["attempt_base_elapsed"]) + heartbeat_mono - started_mono,
                                    "last_activity": (
                                        "provider_initialized" if event_kind == "init"
                                        else "progress_signal" if event_kind == "step_update"
                                        else "terminal_received"
                                    ),
                                }
                                if conversation is not None:
                                    if state["conversation_id"] not in {None, conversation}:
                                        reason = "status_unavailable"
                                        break
                                    updates["conversation_id"] = conversation
                                    updates["resume_available"] = True
                                controller_transition(updates)
                        if len(buffers[name]) > MAX_EVENT_BYTES:
                            # A newline-free oversized frame cannot be safely
                            # resynchronized.  It is neither a heartbeat nor a
                            # candidate terminal result.
                            reason = "output_oversized"
                    if reason is not None:
                        break
                if reason is not None:
                    break
            if process is not None:
                returncode = _terminate(process)
                process = None
            os.fsync(stdout_fd)
            os.fsync(stderr_fd)
            elapsed = float(state["attempt_base_elapsed"]) + time.monotonic() - started_mono
            result_binding: tuple[str, tuple[int, int, int, int, int]] | None = None
            outer_status: str | None = None
            provider_retry_after: int | None = None
            provider_retry_observed: float | None = None
            if reason is None:
                terminal_failure = _quota_terminal_failure(
                    stream_path,
                    command["agy_version"] if command["agy_version_observed"] else "",
                )
                if terminal_failure is not None:
                    reason, provider_retry_after = terminal_failure
                    if provider_retry_after is not None:
                        provider_retry_observed = time.time()
                elif sizes["stdout"] == 0:
                    reason = (
                        _classify_stderr(stderr_path, command["agy_version"], returncode)
                        if returncode != 0 else "empty_output"
                    )
                else:
                    try:
                        if schema_paths is None:
                            raise DispatchError("dispatch schema binding is unavailable")
                        schema_paths = _bound_schemas(command, state)
                        result_binding, outer_status, failure_stage = _validate_terminal_envelope(
                            stream_path, envelope_path, schema_paths[0], schema_paths[1],
                        )
                        if result_binding is None:
                            reason = "invalid_envelope"
                        elif outer_status == "ERROR":
                            reason = "provider_terminal_error"
                        elif outer_status == "CANCELLED":
                            reason = "provider_terminal_cancelled"
                    except DispatchError:
                        reason = "status_unavailable"
                        result_binding = None
                        failure_stage = "binding_failure"
            boundary_failed = False
            if command["workflow"] == "project":
                try:
                    if _project_boundary(command["workdir"]) != state["project_boundary"]:
                        raise DispatchError("project worktree boundary changed")
                except DispatchError:
                    boundary_failed = True
                    reason = "status_unavailable"
                    result_binding = None
                    failure_stage = "binding_failure"
            # One blocked completion snapshot linearizes terminal state against
            # late HUP/INT/TERM just as the foreground result publisher does.
            watched = tuple(prior_handlers)
            signal.pthread_sigmask(signal.SIG_BLOCK, watched)
            pending = signal.sigpending()
            completion_signal = stop_signal
            for candidate in watched:
                if candidate in pending or candidate == stop_signal:
                    completion_signal = candidate
                    break
            if completion_signal is not None:
                stop_signal = completion_signal
                reason = "interrupted"
                result_binding = None
                failure_stage = None
            if reason is None:
                final_status, exit_code = "succeeded", 0
                result_path: str | None = str(envelope_path)
            else:
                final_status = "cancelled" if reason in {"cancelled", "interrupted"} else "failed"
                if reason == "provider_terminal_cancelled":
                    final_status = "cancelled"
                exit_code = 128 + stop_signal if stop_signal is not None else EXIT_BY_REASON[reason]
                result_path = str(envelope_path) if result_binding is not None else None
            cleanup_failed = False
            try:
                selector.close()
                os.close(stdout_fd); stdout_fd = -1
                os.close(stderr_fd); stderr_fd = -1
                _stage(command, False)
                _load_bound_command(job, state, stage_readonly=False)
                _bound_schemas(command, state)
                if state["attempt_origin"] == "conversation-continue":
                    _bound_verification(job, state)
            except (OSError, DispatchError):
                cleanup_failed = True
            if cleanup_failed:
                reason, final_status = "status_unavailable", "failed"
                exit_code = EXIT_BY_REASON["status_unavailable"]
                result_path = None
                result_binding = None
                failure_stage = "binding_failure"
            # An approved control may land after the last loop observation.  Bind
            # finalization to the current state under the same short transition lock.
            with state_lock(job):
                current, current_raw, _current_sha = load_state(job)
                if current["attempt"] != state["attempt"] or current["status"] in TERMINAL:
                    raise DispatchError("dispatch changed before terminalization")
                if current["cancel_requested"]:
                    reason, final_status, exit_code = "cancelled", "cancelled", EXIT_BY_REASON["cancelled"]
                    result_path = None
                    result_binding = None
                    failure_stage = None
                if reason != "provider_quota_exhausted":
                    provider_retry_after = None
                    provider_retry_observed = None
                preserve_candidate = bool(
                    result_binding is None
                    and current["attempt_origin"] == "conversation-continue"
                    and current["candidate_recognized"]
                )
                candidate_worktree = _worktree_snapshot(command["workdir"]) if result_binding is not None else None
                candidate_recognized = result_binding is not None or preserve_candidate
                candidate_unavailable = bool(
                    candidate_recognized and failure_stage == "binding_failure"
                )
                candidate_source = (
                    "provider_success" if result_binding is not None and outer_status == "SUCCESS"
                    else "provider_error" if result_binding is not None and outer_status == "ERROR"
                    else "provider_cancelled" if result_binding is not None and outer_status == "CANCELLED"
                    else current["candidate_source"] if preserve_candidate
                    else "none"
                )
                preserved_path = current["result_path"] if preserve_candidate else None
                preserved_sha = current["result_sha256"] if preserve_candidate else None
                preserved_identity = current["result_identity"] if preserve_candidate else None
                updates = {
                    "status": final_status,
                    "reason": reason,
                    "exit_code": exit_code,
                    "controller_pid": None,
                    "finished_epoch": time.time(),
                    "elapsed_seconds": elapsed,
                    "agy_returncode": returncode,
                    "result_path": str(envelope_path) if result_binding is not None else preserved_path,
                    "result_sha256": result_binding[0] if result_binding is not None else preserved_sha,
                    "result_identity": list(result_binding[1]) if result_binding is not None else preserved_identity,
                    "candidate_recognized": candidate_recognized,
                    "candidate_source": candidate_source,
                    # Preserve an exact old candidate after a binding failure for
                    # forensics, but never claim it can still be read or reviewed.
                    "result_available": candidate_recognized and not candidate_unavailable,
                    "candidate_worktree_sha256": (
                        candidate_worktree["sha256"] if candidate_worktree is not None
                        else current["candidate_worktree_sha256"] if preserve_candidate else None
                    ),
                    "candidate_worktree_entries": (
                        candidate_worktree["entries"] if candidate_worktree is not None
                        else current["candidate_worktree_entries"] if preserve_candidate else None
                    ),
                    "driver_disposition": "unreviewed" if candidate_recognized else "not_applicable",
                    "failure_stage": failure_stage,
                    "last_activity": "terminal_received" if saw_terminal else current["last_activity"],
                    "next_action": (
                        "blocked" if candidate_unavailable else "driver_review"
                    ) if candidate_recognized else ("resume" if current["conversation_id"] else "blocked"),
                    "next_action_command": None,
                    **_reconcile_worktree(command["workdir"], current["worktree_baseline"]),
                    "resume_available": bool(
                        current["conversation_id"] and not candidate_recognized
                        and final_status == "failed"
                    ),
                    "continue_available": False,
                    "remote_cancel_unverified": reason in {"cancelled", "interrupted"},
                    "limit_kind": limit_kind,
                    "provider_retry_after_seconds": provider_retry_after,
                    "provider_retry_observed_epoch": provider_retry_observed,
                }
                if current["workflow"] != "legacy":
                    if boundary_failed or candidate_unavailable:
                        updates.update({"phase": "blocked", "assurance": "blocked"})
                    elif candidate_recognized:
                        updates.update({
                            "phase": (
                                "repair-failed"
                                if final_status == "failed" and current["attempt_origin"] == "conversation-continue"
                                else "awaiting-verification"
                            ),
                            "assurance": "pending",
                            "continue_available": bool(
                                final_status in {"succeeded", "failed"}
                                and candidate_source != "provider_cancelled"
                                and current["conversation_id"]
                                and current["attempt"] < current["max_cycles"]
                                and elapsed < current["max_seconds"]
                            ),
                        })
                    else:
                        updates.update({
                            "phase": (
                                "repair-failed"
                                if final_status == "failed" and current["attempt_origin"] == "conversation-continue"
                                else "attempt-failed"
                            ),
                            "assurance": "pending",
                            "continue_available": False,
                        })
                state, prior_raw, _sha = _transition_locked(job, current, current_raw, updates)
            return exit_code
        finally:
            if process is not None:
                _terminate(process)
            with contextlib.suppress(Exception):
                selector.close()
            if stdout_fd >= 0:
                os.close(stdout_fd)
            if stderr_fd >= 0:
                os.close(stderr_fd)
            with contextlib.suppress(OSError):
                _stage(command, False)
    finally:
        for number, handler in prior_handlers.items():
            signal.signal(number, handler)


def create_state(
    job: Path, origin: str, *, resume: bool, approve_sha: str | None = None,
    verification: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    command, command_raw, command_info = load_command(job)
    stage_sha, stage_info = _bound_stage(command, readonly=False)
    schema_bindings = _schema_bindings(command)
    path = job / STATE_NAME
    with state_lock(job):
        if resume:
            state, raw, sha = load_state(job)
            if approve_sha != sha:
                raise DispatchError("continuation state approval is stale")
            _load_bound_command(job, state, stage_readonly=False)
            if state["schema_version"] != 5:
                state = _upgrade_legacy_state(state, command)
            _bound_schemas(command, state)
            if state["workflow"] == "project" and (
                _project_boundary(command["workdir"]) != state["project_boundary"]
            ):
                raise DispatchError("project worktree boundary changed")
            if origin == "conversation-continue":
                if (
                    state["workflow"] == "legacy" or not state["candidate_recognized"]
                    or state["status"] not in {"succeeded", "failed"}
                    or state["phase"] not in {"awaiting-verification", "repair-failed"}
                    or not state["continue_available"] or verification is None
                ):
                    raise DispatchError("dispatch continuation is unavailable")
                _require_current_candidate_verification(verification, state)
                _bound_candidate_worktree(state, command)
            else:
                if state["status"] not in TERMINAL or state["status"] == "orphaned":
                    raise DispatchError("only a terminal unsuccessful dispatch can continue")
                if origin == "conversation-resume" and state["candidate_recognized"]:
                    raise DispatchError("recognized candidates require driver review, finalize, or continue")
                if origin == "conversation-resume" and not state["resume_available"]:
                    raise DispatchError("dispatch is not resume-eligible")
            if float(state["elapsed_seconds"]) >= float(state["max_seconds"]):
                raise DispatchError("dispatch max runtime is exhausted")
            if state["workflow"] != "legacy" and state["attempt"] >= state["max_cycles"]:
                raise DispatchError("dispatch max cycles is exhausted")
            conversation = state["conversation_id"] if origin == "conversation-resume" else None
            if origin == "conversation-continue":
                conversation = state["conversation_id"]
            verification_path: Path | None = None
            verification_sha: str | None = None
            verification_identity: tuple[int, int, int, int, int] | None = None
            if verification is not None:
                verification_path, verification_sha, verification_identity = _write_verification(
                    job, f"cycle-{state['attempt'] + 1:03d}", verification,
                )
            try:
                next_state = initial_state(
                    command, origin, state["attempt"] + 1,
                    command_sha=digest(command_raw), command_identity=command_info,
                    stage_sha=stage_sha, stage_identity=stage_info,
                    project_boundary=state["project_boundary"],
                    schema_bindings=schema_bindings,
                )
                next_state["sequence"] = state["sequence"] + 1
                next_state["previous_state_sha256"] = sha
                next_state["conversation_id"] = conversation
                next_state["resume_available"] = conversation is not None
                next_state["attempt_base_elapsed"] = float(state["elapsed_seconds"])
                next_state["elapsed_seconds"] = float(state["elapsed_seconds"])
                next_state["hard_seconds"] = min(
                    float(state["max_seconds"]),
                    float(state["elapsed_seconds"]) + float(command["hard_seconds"]),
                )
                next_state["max_seconds"] = float(state["max_seconds"])
                if state["workflow"] != "legacy":
                    next_state["phase"] = "repairing" if origin == "conversation-continue" else "dispatching"
                    next_state["check_summary"] = state["check_summary"]
                    next_state["check_counts"] = state["check_counts"]
                    next_state["last_success_path"] = state["result_path"] or state["last_success_path"]
                    next_state["last_success_sha256"] = state["result_sha256"] or state["last_success_sha256"]
                    next_state["last_success_identity"] = state["result_identity"] or state["last_success_identity"]
                    if origin == "conversation-continue":
                        for key in (
                            "result_path", "result_sha256", "result_identity",
                            "candidate_recognized", "candidate_source", "result_available",
                            "candidate_worktree_sha256", "candidate_worktree_entries",
                            "driver_disposition",
                        ):
                            next_state[key] = state[key]
                if verification_path is not None:
                    next_state.update({
                        "verification_path": str(verification_path),
                        "verification_sha256": verification_sha,
                        "verification_identity": list(verification_identity),
                        "check_summary": verification["summary"],
                        "check_counts": _verification_counts(verification),
                    })
                validate_state(next_state)
                current, _info = read_regular(path, MAX_STATE_BYTES, "dispatch state")
                if current != raw:
                    raise DispatchError("dispatch state changed before continuation")
                _new_raw, new_sha = write_atomic(job, STATE_NAME, next_state)
                return next_state, new_sha
            except Exception:
                _discard_new_verification(verification_path, verification_identity)
                raise
        if path.exists() or path.is_symlink():
            raise DispatchError("dispatch state already exists")
        state = initial_state(
            command, origin, 1, command_sha=digest(command_raw),
            command_identity=command_info, stage_sha=stage_sha, stage_identity=stage_info,
            schema_bindings=schema_bindings,
        )
        validate_state(state)
        _raw, sha = write_atomic(job, STATE_NAME, state)
        return state, sha


def spawn(
    job: Path, origin: str, *, resume: bool, foreground: bool,
    approve_sha: str | None = None, verification: dict[str, Any] | None = None,
) -> int:
    parent_signal: int | None = None
    completion_blocked = False

    def latch(number: int, _frame: Any) -> None:
        nonlocal parent_signal
        if parent_signal is None:
            parent_signal = number

    watched = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous = {number: signal.getsignal(number) for number in watched}
    for number in watched:
        signal.signal(number, latch)
    controller_process: subprocess.Popen[bytes] | None = None
    try:
      with lifecycle_lock(job, blocking=False) as ownership_fd:
        state, _sha = create_state(
            job, origin, resume=resume, approve_sha=approve_sha, verification=verification,
        )
        if parent_signal is not None:
            _terminalize_queued_signal(job, parent_signal)
            return 128 + parent_signal
        command = [
            sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()),
            "controller", "--job-dir", str(job), "--ownership-fd", str(ownership_fd),
        ]
        controller_process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
            pass_fds=(ownership_fd,),
        )
      deadline = time.monotonic() + 5.0
      forwarded = False
      while time.monotonic() < deadline:
        current, _raw, sha = read_state_snapshot(job)
        if parent_signal is not None and not forwarded:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(controller_process.pid, parent_signal)
            forwarded = True
        if current["status"] != "queued":
            break
        if controller_process.poll() is not None:
            _terminalize_start_failure(job)
            raise DispatchError("controller exited before startup handshake")
        time.sleep(0.02)
      else:
        _terminate(controller_process)
        controller_process = None
        _terminalize_start_failure(job)
        raise DispatchError("controller startup handshake timed out")
      if parent_signal is not None and not foreground:
        try:
            controller_process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            _terminate(controller_process)
        return 128 + parent_signal
      if not foreground:
        print_json(public_status(current, sha))
        return 0
      while controller_process.poll() is None:
        if parent_signal is not None and not forwarded:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(controller_process.pid, parent_signal)
            forwarded = True
        time.sleep(0.02)
      result_code = controller_process.returncode
      # Linearize foreground completion: after this snapshot lifecycle signals stay
      # blocked until process exit, so success bytes cannot race a late cancellation.
      signal.pthread_sigmask(signal.SIG_BLOCK, watched)
      completion_blocked = True
      pending = signal.sigpending()
      completion_signal = parent_signal
      for candidate in watched:
        if candidate in pending or candidate == parent_signal:
            completion_signal = candidate
            break
      if completion_signal is not None:
        result_code = 128 + completion_signal
      final, _raw, sha = read_state_snapshot(job)
      if completion_signal is None and final["status"] == "succeeded" and final["result_path"] is not None:
        command_result(job)
      else:
        sys.stderr.buffer.write(canonical(public_status(final, sha)))
        sys.stderr.buffer.flush()
      return result_code
    finally:
      if not completion_blocked:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _terminal_projection(
    state: dict[str, Any], *, status: str, reason: str, exit_code: int,
    failure_stage: str | None = None, remote_cancel_unverified: bool = False,
    allow_continue: bool = True,
) -> dict[str, Any]:
    """Project a local terminal side path without dropping a bound candidate.

    These paths run before normal provider terminalization, so they must not leave
    the transient dispatching/repairing UI fields behind.  A queued continuation
    already contains an exact, driver-owned candidate binding; returning that
    candidate to review is safer than treating a local controller failure as a
    provider repair result.
    """
    candidate = bool(state["candidate_recognized"])
    candidate_unavailable = bool(candidate and failure_stage == "binding_failure")
    continuation = candidate and state["attempt_origin"] == "conversation-continue"
    can_continue = bool(
        continuation
        and state["candidate_source"] != "provider_cancelled"
        and state["conversation_id"]
        and state["attempt"] < state["max_cycles"]
        and float(state["elapsed_seconds"]) < float(state["max_seconds"])
        and status == "failed"
        and allow_continue
        and not candidate_unavailable
    )
    updates: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "exit_code": exit_code,
        "controller_pid": None,
        "finished_epoch": time.time(),
        "failure_stage": failure_stage,
        # A local process signal is not evidence that the provider observed a
        # cancellation.  Startup/binding failures make no remote-cancel claim.
        "remote_cancel_unverified": remote_cancel_unverified,
        "continue_available": can_continue,
        "result_available": candidate and not candidate_unavailable,
        **_reconcile_worktree(state["workdir"], state["worktree_baseline"]),
    }
    if candidate:
        updates.update({
            "resume_available": False,
            "driver_disposition": "unreviewed",
            "next_action": "blocked" if candidate_unavailable else "driver_review",
            "next_action_command": None,
        })
    else:
        resume_eligible = bool(
            status == "failed" and state["conversation_id"]
            and state["attempt_origin"] != "conversation-continue"
        )
        updates.update({
            "resume_available": resume_eligible,
            "driver_disposition": "not_applicable",
            "next_action": "resume" if resume_eligible else "blocked",
            "next_action_command": None,
        })
    if state["workflow"] != "legacy":
        updates.update({
            "phase": "blocked" if candidate_unavailable else ("awaiting-verification" if candidate else "attempt-failed"),
            "assurance": "blocked" if candidate_unavailable else "pending",
        })
    return updates


def _terminalize_start_failure(job: Path) -> None:
    with lifecycle_lock(job, blocking=True):
        with state_lock(job):
            state, raw, _sha = load_state(job)
            if state["status"] in TERMINAL:
                return
            _transition_locked(job, state, raw, _terminal_projection(
                state, status="failed", reason="status_unavailable",
                exit_code=EXIT_BY_REASON["status_unavailable"],
            ))


def _terminalize_queued_signal(job: Path, number: int) -> None:
    with state_lock(job):
        state, raw, _sha = load_state(job)
        if state["status"] != "queued":
            raise DispatchError("dispatch changed before queued cancellation")
        # A signal before a continuation controller starts must preserve the
        # prior candidate and send it back to driver review.  Its local terminal
        # state is failed (not provider-CANCELED), so a strict same-conversation
        # continue remains possible when its original budget still permits it.
        continuation = bool(
            state["candidate_recognized"]
            and state["attempt_origin"] == "conversation-continue"
        )
        _transition_locked(job, state, raw, _terminal_projection(
            state,
            status="failed" if continuation else "cancelled",
            reason="interrupted",
            exit_code=128 + number,
            remote_cancel_unverified=True,
        ))


def command_status(job: Path) -> int:
    state, _raw, sha = read_state_snapshot(job)
    if state["status"] in {"queued", "running", "cancel-requested"}:
        try:
            with lifecycle_lock(job, blocking=False):
                state, raw, sha = read_state_snapshot(job)
                if state["status"] in {"queued", "running", "cancel-requested"}:
                    state, raw, sha = transition(job, state, raw, _terminal_projection(
                        state, status="orphaned", reason="status_unavailable",
                        exit_code=EXIT_BY_REASON["status_unavailable"],
                    ))
        except DispatchError as exc:
            if str(exc) != "dispatch controller is active":
                raise
    print_json(public_status(state, sha))
    return 0


def command_wait(job: Path, after: str, timeout: float) -> int:
    if SHA_RE.fullmatch(after) is None or not (0 <= timeout <= MAX_STATUS_WAIT):
        raise DispatchError("wait arguments are invalid")
    deadline = time.monotonic() + timeout
    while True:
        state, _raw, sha = read_state_snapshot(job)
        if sha != after or state["status"] in TERMINAL:
            print_json(public_status(state, sha))
            return 0
        if time.monotonic() >= deadline:
            print_json(public_status(state, sha))
            return 0
        time.sleep(min(0.20, max(0.0, deadline - time.monotonic())))


def command_result(job: Path) -> int:
    state, _raw, _sha = read_state_snapshot(job)
    result_path = state["result_path"]
    result_sha = state["result_sha256"]
    result_identity = state["result_identity"]
    if (
        state["workflow"] == "project" and state["status"] in {"failed", "cancelled"}
        and state["phase"] == "completed" and state["assurance"] == "partially_verified"
    ):
        result_path = state["last_success_path"]
        result_sha = state["last_success_sha256"]
        result_identity = state["last_success_identity"]
    elif not state["result_available"]:
        raise DispatchError("dispatch result is unavailable")
    if result_path is None:
        raise DispatchError("dispatch has no preserved result")
    raw, info = read_regular(Path(result_path), 1024 * 1024, "dispatch result")
    if digest(raw) != result_sha or list(_identity(info)) != result_identity:
        raise DispatchError("dispatch result binding changed")
    command = _load_bound_command(job, state, stage_readonly=False)
    schema_paths = _schema_paths(command)
    if schema_paths is None:
        raise DispatchError("dispatch result schema is unavailable")
    if state["schema_version"] == 5:
        schema_paths = _bound_schemas(command, state)
    validator = Path(__file__).with_name("validate-envelope.py")
    checked = [
        subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(validator), str(schema), str(result_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        for schema in schema_paths
    ]
    if any(item.returncode != 0 for item in checked):
        raise DispatchError("dispatch result is no longer valid")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()
    return 0


def command_continue(job: Path, approve_sha: str) -> int:
    verification = _verification_from_stdin()
    if not verification["failed_checks"] and verification["missing_checks"] == 0:
        raise DispatchError("project continuation requires one failed or missing check")
    return spawn(
        job, "conversation-continue", resume=True, foreground=False,
        approve_sha=approve_sha, verification=verification,
    )


def command_finalize(job: Path, approve_sha: str, assurance: str) -> int:
    if assurance not in {"verified", "partially_verified", "rejected", "blocked"}:
        raise DispatchError("project assurance is invalid")
    verification = _verification_from_stdin()
    with state_lock(job):
        state, raw, sha = load_state(job)
        if sha != approve_sha:
            raise DispatchError("dispatch finalization is stale or unavailable")
        command = _load_bound_command(job, state, stage_readonly=False)
        if state["schema_version"] != 5:
            state = _upgrade_legacy_state(state, command)
        eligible_candidate = bool(
            state["candidate_recognized"] and state["result_available"] and state["result_path"]
            and state["workflow"] != "legacy"
        )
        if not eligible_candidate:
            raise DispatchError("dispatch finalization is stale or unavailable")
        _bound_schemas(command, state)
        _require_current_candidate_verification(verification, state)
        _bound_candidate_worktree(state, command)
        counts = _verification_counts(verification)
        if assurance == "verified" and (not eligible_candidate or not _verification_is_verified(verification, state["workflow"])):
            raise DispatchError("verified finalization requires passing complete checks")
        if assurance == "partially_verified" and not eligible_candidate:
            raise DispatchError("partial finalization has no candidate")
        if assurance == "rejected" and not (eligible_candidate and (counts["failed"] or counts["missing"])):
            raise DispatchError("rejected finalization requires a driver-observed failure")
        if assurance == "blocked" and (
            not (counts["failed"] or counts["missing"])
        ):
            raise DispatchError("blocked finalization requires a driver-observed blocker")
        path: Path | None = None
        identity: tuple[int, int, int, int, int] | None = None
        try:
            path, verification_sha, identity = _write_verification(
                job, f"final-{state['cycle']:03d}", verification,
            )
            phase = "blocked" if assurance == "blocked" else "completed"
            state, _raw, sha = _transition_locked(job, state, raw, {
                "phase": phase,
                "assurance": assurance,
                "continue_available": False,
                "check_summary": verification["summary"],
                "check_counts": counts,
                "verification_path": str(path),
                "verification_sha256": verification_sha,
                "verification_identity": list(identity),
                "driver_disposition": assurance,
                "next_action": "none",
                "next_action_command": None,
            })
        except Exception:
            _discard_new_verification(path, identity)
            raise
    print_json(public_status(state, sha))
    return 0


def command_control(job: Path, action: str, approve_sha: str, seconds: float | None) -> int:
    if SHA_RE.fullmatch(approve_sha) is None:
        raise DispatchError("state approval is invalid")
    with state_lock(job):
        state, raw, sha = load_state(job)
        if sha != approve_sha or state["status"] not in {"queued", "running", "cancel-requested"}:
            raise DispatchError("state approval is stale or dispatch is terminal")
        if action == "cancel":
            if state["cancel_requested"]:
                raise DispatchError("cancel is already requested")
            updates = {"cancel_requested": True, "status": "cancel-requested"}
        else:
            assert seconds is not None
            now = time.time()
            current_elapsed = float(state["elapsed_seconds"])
            if state["started_epoch"] is not None:
                current_elapsed = max(
                    current_elapsed,
                    float(state["attempt_base_elapsed"]) + now - float(state["started_epoch"]),
                )
            progress_fresh = (
                state["progress_count"] > 0
                and state["last_progress_epoch"] is not None
                and now - float(state["last_progress_epoch"]) < float(state["idle_seconds"])
            )
            if not progress_fresh or current_elapsed >= float(state["hard_seconds"]):
                raise DispatchError("deadline extension requires fresh progress before the hard deadline")
            if seconds <= 0 or state["hard_seconds"] + seconds > state["max_seconds"]:
                raise DispatchError("deadline extension exceeds max runtime")
            updates = {"hard_seconds": state["hard_seconds"] + seconds}
        state, _raw, sha = _transition_locked(job, state, raw, updates)
    print_json(public_status(state, sha))
    return 0


def duration(text: str) -> float:
    match = re.fullmatch(r"([1-9][0-9]*)(s|m|h)", text)
    if match is None:
        raise argparse.ArgumentTypeError("duration must be a positive integer plus s, m, or h")
    factor = {"s": 1, "m": 60, "h": 3600}[match.group(2)]
    value = int(match.group(1)) * factor
    if value > 7 * 24 * 3600:
        raise argparse.ArgumentTypeError("duration is too large")
    return float(value)


def parser() -> Parser:
    result = Parser(prog="agy-dispatch")
    commands = result.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in ("run", "start", "resume", "restart", "continue", "status", "result", "controller"):
        item = commands.add_parser(name)
        item.add_argument("--job-dir", required=True)
        if name == "controller":
            item.add_argument("--ownership-fd", required=True, type=int)
        if name in {"resume", "restart", "continue"}:
            item.add_argument("--approve-state-sha", required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--job-dir", required=True)
    finalize.add_argument("--approve-state-sha", required=True)
    finalize.add_argument("--assurance", required=True)
    wait = commands.add_parser("wait")
    wait.add_argument("--job-dir", required=True)
    wait.add_argument("--after-state-sha", required=True)
    wait.add_argument("--timeout", type=duration, default=60.0)
    for name in ("cancel", "extend"):
        item = commands.add_parser(name)
        item.add_argument("--job-dir", required=True)
        item.add_argument("--approve-state-sha", required=True)
        if name == "extend":
            item.add_argument("--by", type=duration, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    job = canonical_job(Path(args.job_dir))
    if args.command == "controller":
        if args.ownership_fd < 3:
            raise DispatchError("controller ownership descriptor is invalid")
        return controller(job, args.ownership_fd)
    if args.command == "run":
        return spawn(job, "initial", resume=False, foreground=True)
    if args.command == "start":
        return spawn(job, "initial", resume=False, foreground=False)
    if args.command == "resume":
        return spawn(
            job, "conversation-resume", resume=True, foreground=False,
            approve_sha=args.approve_state_sha,
        )
    if args.command == "restart":
        return spawn(
            job, "fresh-restart", resume=True, foreground=False,
            approve_sha=args.approve_state_sha,
        )
    if args.command == "continue":
        return command_continue(job, args.approve_state_sha)
    if args.command == "status":
        return command_status(job)
    if args.command == "wait":
        return command_wait(job, args.after_state_sha, args.timeout)
    if args.command == "result":
        return command_result(job)
    if args.command == "finalize":
        return command_finalize(job, args.approve_state_sha, args.assurance)
    if args.command in {"cancel", "extend"}:
        return command_control(
            job, args.command, args.approve_state_sha,
            getattr(args, "by", None),
        )
    raise DispatchError("unknown command")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DispatchError as exc:
        print(f"agy-dispatch: {exc}", file=sys.stderr)
        command_name = sys.argv[1] if len(sys.argv) > 1 else ""
        if command_name == "resume":
            raise SystemExit(EXIT_BY_REASON["resume_failed"])
        if command_name in {"status", "wait", "result"}:
            raise SystemExit(EXIT_BY_REASON["status_unavailable"])
        raise SystemExit(64)
