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
MAX_STREAM_BYTES = 32 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_STATUS_WAIT = 60.0
TERM_GRACE = 1.0
CONTROL_POLL = 0.20
CONVERSATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
JOB_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
TERMINAL = {"succeeded", "failed", "cancelled", "orphaned"}
REASONS = {
    "provider_timeout", "idle_timeout", "hard_deadline_exceeded",
    "authentication_failed", "provider_unavailable", "status_unavailable",
    "resume_failed", "cancelled", "agy_failed_unclassified",
    "permission_required", "empty_output", "invalid_envelope",
    "output_oversized", "interrupted",
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
    "interrupted": 143,
}

# These are exact, version-scoped observations.  Unknown text remains unclassified;
# no broad substring or prose inference may decide auth/provider state.
# Populated only from retained, version-bound observations.  There is currently no
# reviewed 1.1.12 stderr evidence in the repository, so every provider diagnostic
# remains deliberately unclassified.
EXACT_FAILURE_LINES: dict[str, dict[bytes, str]] = {"1.1.12": {}}


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


def parse_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DispatchError(f"{label} is invalid") from exc


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
    validate_state(value)
    return value, raw, digest(raw)


def read_state_snapshot(job: Path) -> tuple[dict[str, Any], bytes, str]:
    """Read one strict state snapshot without racing an approved replacement."""

    with state_lock(job):
        return load_state(job)


def load_command(job: Path) -> tuple[dict[str, Any], bytes, tuple[int, int, int, int, int]]:
    raw, info = read_regular(job / COMMAND_NAME, MAX_COMMAND_BYTES, "dispatch command")
    value = parse_json(raw, "dispatch command")
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "kind", "job_id", "workdir", "argv", "agy_version",
        "idle_seconds", "hard_seconds", "max_seconds", "notice_seconds",
        "stage_dir", "stage_file", "child_umask", "resume_prompt",
    }:
        raise DispatchError("dispatch command fields are invalid")
    if value["schema_version"] != 1 or value["kind"] != "agy-worker-dispatch-command":
        raise DispatchError("dispatch command version is invalid")
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
    if raw != canonical(value):
        raise DispatchError("dispatch command is not canonical")
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
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise DispatchError("dispatch state fields are invalid")
    if value["schema_version"] != 1 or value["kind"] != "agy-worker-dispatch-state":
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
    if value["attempt_origin"] not in {"initial", "conversation-resume", "fresh-restart"}:
        raise DispatchError("dispatch attempt origin is invalid")
    if type(value["attempt"]) is not int or value["attempt"] < 1:
        raise DispatchError("dispatch attempt is invalid")
    if not isinstance(value["job_id"], str) or JOB_RE.fullmatch(value["job_id"]) is None:
        raise DispatchError("dispatch job ID is invalid")
    conversation = value["conversation_id"]
    if conversation is not None and (
        not isinstance(conversation, str) or CONVERSATION_RE.fullmatch(conversation) is None
    ):
        raise DispatchError("dispatch conversation ID is invalid")
    for key in ("cancel_requested", "resume_available", "remote_cancel_unverified"):
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
    for key in ("exit_code", "controller_pid", "agy_returncode"):
        if value[key] is not None and type(value[key]) is not int:
            raise DispatchError("dispatch integer is invalid")
    for key in ("workdir", "result_path", "stream_path", "stderr_path"):
        if value[key] is not None and not isinstance(value[key], str):
            raise DispatchError("dispatch path is invalid")
    if value["limit_kind"] not in {None, "idle", "hard", "max-runtime"}:
        raise DispatchError("dispatch limit kind is invalid")
    for key in ("command_sha256", "stage_sha256", "result_sha256"):
        if value[key] is not None and (
            not isinstance(value[key], str) or SHA_RE.fullmatch(value[key]) is None
        ):
            raise DispatchError("dispatch digest is invalid")
    for key in ("command_identity", "stage_identity", "result_identity"):
        identity = value[key]
        if identity is not None and (
            not isinstance(identity, list) or len(identity) != 5
            or any(type(item) is not int or item < 0 for item in identity)
        ):
            raise DispatchError("dispatch identity is invalid")
    if value["status"] in TERMINAL:
        if value["finished_epoch"] is None or value["exit_code"] is None:
            raise DispatchError("terminal dispatch state is incomplete")
    return value


def initial_state(
    command: dict[str, Any], origin: str, attempt: int, *, command_sha: str,
    command_identity: tuple[int, int, int, int, int], stage_sha: str | None,
    stage_identity: tuple[int, int, int, int, int] | None,
) -> dict[str, Any]:
    now = time.time()
    return {
        "schema_version": 1,
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
    }


def _transition_locked(job: Path, state: dict[str, Any], prior_raw: bytes, updates: dict[str, Any]) -> tuple[dict[str, Any], bytes, str]:
    current, _info = read_regular(job / STATE_NAME, MAX_STATE_BYTES, "dispatch state")
    if current != prior_raw:
        raise DispatchError("dispatch state changed before transition")
    value = dict(state)
    value.update(updates)
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
    return {
        "attempt": value["attempt"],
        "attempt_origin": value["attempt_origin"],
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": value["exit_code"],
        "hard_seconds": value["hard_seconds"],
        "job_id": value["job_id"],
        "last_progress_age_seconds": None if last_age is None else round(last_age, 3),
        "limit_kind": value["limit_kind"],
        "max_seconds": value["max_seconds"],
        "notice_count": value["notice_count"],
        "progress_count": value["progress_count"],
        "reason": value["reason"],
        "remote_cancel_unverified": value["remote_cancel_unverified"],
        "resume_available": value["resume_available"],
        "state_sha256": sha,
        "status": value["status"],
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
        value = json.loads(line.decode("utf-8", "strict"), object_pairs_hook=_duplicates)
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


def _validate_terminal_envelope(
    stream: Path, envelope: Path, schema: str,
) -> tuple[str, tuple[int, int, int, int, int]] | None:
    result: dict[str, Any] | None = None
    saw_init = False
    saw_terminal = False
    try:
        with stream.open("rb") as handle:
            for raw in handle:
                if len(raw) > MAX_EVENT_BYTES:
                    return None
                try:
                    event = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_duplicates)
                except (UnicodeError, json.JSONDecodeError, DispatchError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("event")
                structurally_valid = (
                    (kind == "init" and isinstance(event.get("init"), dict))
                    or (kind == "step_update" and isinstance(event.get("step_update"), dict))
                    or (kind == "result" and isinstance(event.get("result"), dict))
                )
                if not structurally_valid:
                    continue
                if saw_terminal:
                    return None
                if kind == "init":
                    if saw_init:
                        return None
                    saw_init = True
                elif not saw_init:
                    return None
                if kind == "result":
                    saw_terminal = True
                    result = event["result"]
    except OSError:
        return None
    if not saw_terminal or not isinstance(result, dict) or str(result.get("status", "")).upper() != "SUCCESS":
        return None
    value = result.get("structured_output")
    if not isinstance(value, dict):
        return None
    raw = json.dumps(value, ensure_ascii=True, indent=2).encode("ascii") + b"\n"
    if len(raw) > 1024 * 1024:
        return None
    descriptor = _ensure_new_private(envelope)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    validator = Path(__file__).with_name("validate-envelope.py")
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(validator), schema, str(envelope)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        rebound, info = read_regular(envelope, 1024 * 1024, "dispatch result")
    except DispatchError:
        return None
    if rebound != raw:
        return None
    return digest(raw), _identity(info)


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
            transition(job, state, prior_raw, {
                "status": "cancelled", "reason": "cancelled",
                "exit_code": EXIT_BY_REASON["cancelled"], "controller_pid": None,
                "finished_epoch": time.time(), "remote_cancel_unverified": False,
                "resume_available": bool(state["conversation_id"]),
            })
            return EXIT_BY_REASON["cancelled"]
        if state["status"] != "queued" or state["cancel_requested"]:
            raise DispatchError("dispatch is not queued")
        try:
            command = _load_bound_command(job, state, stage_readonly=False)
        except (OSError, DispatchError):
            transition(job, state, prior_raw, {
                "status": "failed", "reason": "status_unavailable",
                "exit_code": EXIT_BY_REASON["status_unavailable"],
                "controller_pid": None, "finished_epoch": time.time(),
            })
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
            })
            _stage(command, True)
            _load_bound_command(job, state, stage_readonly=True)
        except (OSError, DispatchError):
            if stdout_fd >= 0: os.close(stdout_fd)
            if stderr_fd >= 0: os.close(stderr_fd)
            with contextlib.suppress(OSError): _stage(command, False)
            current, current_raw, _current_sha = read_state_snapshot(job)
            transition(job, current, current_raw, {
                "status": "failed", "reason": "status_unavailable",
                "exit_code": EXIT_BY_REASON["status_unavailable"],
                "controller_pid": None, "finished_epoch": time.time(),
                "resume_available": bool(current["conversation_id"]),
            })
            return EXIT_BY_REASON["status_unavailable"]
        argv = list(command["argv"])
        if state["attempt_origin"] == "conversation-resume":
            conversation = state["conversation_id"]
            if not isinstance(conversation, str):
                raise DispatchError("resume has no conversation")
            print_index = argv.index("--print")
            argv[print_index:print_index] = ["--conversation", conversation]
            argv[print_index + 3] = command["resume_prompt"]
        selector = selectors.DefaultSelector()
        process: subprocess.Popen[bytes] | None = None
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        sizes = {"stdout": 0, "stderr": 0}
        heartbeat_mono = time.monotonic()
        started_mono = heartbeat_mono
        next_notice = started_mono + float(command["notice_seconds"])
        reason: str | None = None
        limit_kind: str | None = None
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
            if reason is None:
                if returncode != 0:
                    reason = _classify_stderr(stderr_path, command["agy_version"], returncode)
                elif sizes["stdout"] == 0:
                    reason = "empty_output"
                else:
                    result_binding = _validate_terminal_envelope(
                        stream_path, envelope_path, argv[argv.index("--json-schema") + 1]
                    )
                    if result_binding is None:
                        reason = "invalid_envelope"
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
            if reason is None:
                final_status, exit_code = "succeeded", 0
                result_path: str | None = str(envelope_path)
            else:
                final_status = "cancelled" if reason in {"cancelled", "interrupted"} else "failed"
                exit_code = 128 + stop_signal if stop_signal is not None else EXIT_BY_REASON[reason]
                result_path = None
            cleanup_failed = False
            try:
                selector.close()
                os.close(stdout_fd); stdout_fd = -1
                os.close(stderr_fd); stderr_fd = -1
                _stage(command, False)
                _load_bound_command(job, state, stage_readonly=False)
            except (OSError, DispatchError):
                cleanup_failed = True
            if cleanup_failed:
                reason, final_status = "status_unavailable", "failed"
                exit_code = EXIT_BY_REASON["status_unavailable"]
                result_path = None
                result_binding = None
            # An approved control may land after the last loop observation.  Bind
            # finalization to the current state under the same short transition lock.
            with state_lock(job):
                current, current_raw, _current_sha = load_state(job)
                if current["attempt"] != state["attempt"] or current["status"] in TERMINAL:
                    raise DispatchError("dispatch changed before terminalization")
                if current["cancel_requested"]:
                    reason, final_status, exit_code = "cancelled", "cancelled", EXIT_BY_REASON["cancelled"]
                    result_path = None
                state, prior_raw, _sha = _transition_locked(job, current, current_raw, {
                    "status": final_status,
                    "reason": reason,
                    "exit_code": exit_code,
                    "controller_pid": None,
                    "finished_epoch": time.time(),
                    "elapsed_seconds": elapsed,
                    "agy_returncode": returncode,
                    "result_path": result_path,
                    "result_sha256": None if result_binding is None else result_binding[0],
                    "result_identity": None if result_binding is None else list(result_binding[1]),
                    "resume_available": bool(current["conversation_id"]) and final_status != "succeeded",
                    "remote_cancel_unverified": reason in {"cancelled", "interrupted"},
                    "limit_kind": limit_kind,
                })
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
    job: Path, origin: str, *, resume: bool, approve_sha: str | None = None
) -> tuple[dict[str, Any], str]:
    command, command_raw, command_info = load_command(job)
    stage_sha, stage_info = _bound_stage(command, readonly=False)
    path = job / STATE_NAME
    with state_lock(job):
        if resume:
            state, raw, sha = load_state(job)
            if approve_sha != sha:
                raise DispatchError("continuation state approval is stale")
            if state["status"] not in TERMINAL or state["status"] in {"succeeded", "orphaned"}:
                raise DispatchError("only a terminal unsuccessful dispatch can continue")
            if origin == "conversation-resume" and not state["resume_available"]:
                raise DispatchError("dispatch is not resume-eligible")
            _load_bound_command(job, state, stage_readonly=False)
            if float(state["elapsed_seconds"]) >= float(state["max_seconds"]):
                raise DispatchError("dispatch max runtime is exhausted")
            conversation = state["conversation_id"] if origin == "conversation-resume" else None
            next_state = initial_state(
                command, origin, state["attempt"] + 1,
                command_sha=digest(command_raw), command_identity=command_info,
                stage_sha=stage_sha, stage_identity=stage_info,
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
            validate_state(next_state)
            current, _info = read_regular(path, MAX_STATE_BYTES, "dispatch state")
            if current != raw:
                raise DispatchError("dispatch state changed before continuation")
            _new_raw, new_sha = write_atomic(job, STATE_NAME, next_state)
            return next_state, new_sha
        if path.exists() or path.is_symlink():
            raise DispatchError("dispatch state already exists")
        state = initial_state(
            command, origin, 1, command_sha=digest(command_raw),
            command_identity=command_info, stage_sha=stage_sha, stage_identity=stage_info,
        )
        validate_state(state)
        _raw, sha = write_atomic(job, STATE_NAME, state)
        return state, sha


def spawn(
    job: Path, origin: str, *, resume: bool, foreground: bool,
    approve_sha: str | None = None,
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
        state, _sha = create_state(job, origin, resume=resume, approve_sha=approve_sha)
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


def _terminalize_start_failure(job: Path) -> None:
    with lifecycle_lock(job, blocking=True):
        with state_lock(job):
            state, raw, _sha = load_state(job)
            if state["status"] in TERMINAL:
                return
            _transition_locked(job, state, raw, {
                "status": "failed", "reason": "status_unavailable",
                "exit_code": EXIT_BY_REASON["status_unavailable"],
                "controller_pid": None, "finished_epoch": time.time(),
                "resume_available": bool(state["conversation_id"]),
            })


def _terminalize_queued_signal(job: Path, number: int) -> None:
    with state_lock(job):
        state, raw, _sha = load_state(job)
        if state["status"] != "queued":
            raise DispatchError("dispatch changed before queued cancellation")
        _transition_locked(job, state, raw, {
            "status": "cancelled", "reason": "interrupted",
            "exit_code": 128 + number, "controller_pid": None,
            "finished_epoch": time.time(), "remote_cancel_unverified": False,
            "resume_available": bool(state["conversation_id"]),
        })


def command_status(job: Path) -> int:
    state, _raw, sha = read_state_snapshot(job)
    if state["status"] in {"queued", "running", "cancel-requested"}:
        try:
            with lifecycle_lock(job, blocking=False):
                state, raw, sha = read_state_snapshot(job)
                if state["status"] in {"queued", "running", "cancel-requested"}:
                    state, raw, sha = transition(job, state, raw, {
                        "status": "orphaned", "reason": "status_unavailable",
                        "exit_code": EXIT_BY_REASON["status_unavailable"],
                        "controller_pid": None, "finished_epoch": time.time(),
                        "resume_available": False,
                    })
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
    if state["status"] != "succeeded" or state["result_path"] is None:
        raise DispatchError("dispatch has no successful result")
    raw, info = read_regular(Path(state["result_path"]), 1024 * 1024, "dispatch result")
    if digest(raw) != state["result_sha256"] or list(_identity(info)) != state["result_identity"]:
        raise DispatchError("dispatch result binding changed")
    command = _load_bound_command(job, state, stage_readonly=False)
    schema = command["argv"][command["argv"].index("--json-schema") + 1]
    validator = Path(__file__).with_name("validate-envelope.py")
    checked = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(validator), schema, str(state["result_path"])],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False,
    )
    if checked.returncode != 0:
        raise DispatchError("dispatch result is no longer valid")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()
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
    for name in ("run", "start", "resume", "restart", "status", "result", "controller"):
        item = commands.add_parser(name)
        item.add_argument("--job-dir", required=True)
        if name == "controller":
            item.add_argument("--ownership-fd", required=True, type=int)
        if name in {"resume", "restart"}:
            item.add_argument("--approve-state-sha", required=True)
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
    if args.command == "status":
        return command_status(job)
    if args.command == "wait":
        return command_wait(job, args.after_state_sha, args.timeout)
    if args.command == "result":
        return command_result(job)
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
