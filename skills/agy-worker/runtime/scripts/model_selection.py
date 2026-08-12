#!/usr/bin/env python3
"""Resolve one explicit, reviewed model selection and record driver provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import compatibility


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
COMPAT_ROOT = RUNTIME_ROOT / "compat"
MATRIX_PATH = COMPAT_ROOT / "agy-model-effort-matrix.json"
MATRIX_SCHEMA_PATH = COMPAT_ROOT / "model-effort-matrix.schema.json"
MATRIX_SHA_PATH = COMPAT_ROOT / "agy-model-effort-matrix.sha256"
VERSION_PATH = COMPAT_ROOT / "agy-verified-version.txt"
SOURCE_PATH = COMPAT_ROOT / "agy-upstream-head.txt"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SOURCE_NAMES = ("cli", "environment")
TIER_SOURCES = ("cli", "environment", "implicit-default")
VERSION_TIMEOUT_SECONDS = 3.0
VERSION_OUTPUT_LIMIT = 128
POLICY_FILE_LIMIT = 256 * 1024
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
REVISION_RE = re.compile(r"[0-9a-f]{40}")
LITERAL_MODEL_RE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)+")
TIER_MODEL_BY_NAME = {
    "bulk": "gemini-3.6-flash-medium",
    "cheap": "gemini-3.6-flash-low",
    "hard": "gemini-3.1-pro-high",
    "hardest": "claude-opus-4-6-thinking",
}
COMMON_RECORD_FIELDS = {"schema_version", "kind", "selection_mode"}
TIER_RECORD_FIELDS = COMMON_RECORD_FIELDS | {
    "selected_tier",
    "selected_tier_source",
    "resolved_agy_model",
}
DIRECT_RECORD_FIELDS = COMMON_RECORD_FIELDS | {
    "user_model",
    "user_model_source",
    "resolved_agy_model",
    "installed_agy_version",
    "matrix_sha256",
    "matrix_agy_version",
    "matrix_source_revision",
}
EFFORT_RECORD_FIELDS = DIRECT_RECORD_FIELDS | {"user_effort", "user_effort_source"}
LITERAL_RECORD_FIELDS = COMMON_RECORD_FIELDS | {
    "user_model",
    "user_model_source",
    "resolved_agy_model",
    "compatibility_status",
}


class CallerError(ValueError):
    """The caller supplied an invalid or ambiguous selector."""


class ReviewRequired(ValueError):
    """Reviewed compatibility evidence drifted and needs human reconciliation."""


class EvidenceUnavailable(ValueError):
    """Required local evidence is missing, malformed, or could not be observed."""


class ProbeInterrupted(BaseException):
    """A terminal signal interrupted the bounded local version probe."""

    def __init__(self, signal_number: int) -> None:
        super().__init__(signal_number)
        self.signal_number = signal_number


class UsageParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(64, f"model-selection: {message}\n")


def one(parser: UsageParser, values: list[str] | None, flag: str, required: bool) -> str | None:
    if not values:
        if required:
            parser.error(f"{flag} is required")
        return None
    if len(values) != 1:
        parser.error(f"{flag} must be provided exactly once")
    if values[0] == "":
        parser.error(f"{flag} must not be empty")
    return values[0]


def read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(POLICY_FILE_LIMIT + 1)
    except OSError as exc:
        raise EvidenceUnavailable(f"cannot read {path.name}") from exc
    if len(data) > POLICY_FILE_LIMIT:
        raise EvidenceUnavailable(f"{path.name} is oversized")
    return data


def read_policy_record(path: Path, kind: str) -> str:
    try:
        return compatibility.validate_record(kind, compatibility.read_record(path))
    except compatibility.CompatibilityError as exc:
        raise EvidenceUnavailable(str(exc)) from exc


def load_policy() -> tuple[dict[str, Any], str, str, str]:
    try:
        expected_sha = compatibility.read_record(MATRIX_SHA_PATH)
    except compatibility.CompatibilityError as exc:
        raise EvidenceUnavailable(str(exc)) from exc
    if SHA256_RE.fullmatch(expected_sha) is None:
        raise EvidenceUnavailable("malformed matrix SHA-256 record")

    matrix_bytes = read_bounded(MATRIX_PATH)
    actual_sha = hashlib.sha256(matrix_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise EvidenceUnavailable("matrix SHA-256 does not match the reviewed record")

    try:
        matrix = compatibility.validate_matrix_structure(MATRIX_PATH, MATRIX_SCHEMA_PATH)
    except compatibility.CompatibilityError as exc:
        raise EvidenceUnavailable(str(exc)) from exc
    if hashlib.sha256(read_bounded(MATRIX_PATH)).hexdigest() != actual_sha:
        raise EvidenceUnavailable("matrix changed while it was being validated")

    version = read_policy_record(VERSION_PATH, "version")
    revision = read_policy_record(SOURCE_PATH, "revision")
    active, reason = compatibility.matrix_binding_state(matrix, version, revision)
    if not active:
        raise ReviewRequired(reason)
    return matrix, actual_sha, version, revision


def resolve_model(matrix: dict[str, Any], model: str, effort: str | None) -> tuple[str, str]:
    if compatibility.MODEL_RE.fullmatch(model) is None:
        raise CallerError("--model must be one exact reviewed lowercase model name")
    if effort is not None and effort not in compatibility.EFFORTS:
        raise CallerError("--effort must be exactly low, medium, or high")

    adjustable = {row["model"]: row for row in matrix["adjustable_models"]}
    resolved_outputs = {
        slug
        for row in matrix["adjustable_models"]
        for slug in row["resolutions"].values()
    }
    fixed = {row["model_slug"] for row in matrix["fixed_models"]}

    if effort is None:
        if model in adjustable:
            raise CallerError("an adjustable base model requires one explicit effort")
        if model in resolved_outputs or model in fixed:
            return model, "exact-model"
        raise CallerError("--model is not an exact reviewed model choice")

    if model in resolved_outputs or model in fixed:
        raise CallerError("compound and fixed model slugs do not accept --effort")
    row = adjustable.get(model)
    if row is None:
        raise CallerError("--model is not a reviewed adjustable base model")
    if effort in row["unsupported_efforts"]:
        raise CallerError(f"{model} does not advertise {effort}")
    resolved = row["resolutions"].get(effort)
    if not isinstance(resolved, str) or not resolved:
        raise EvidenceUnavailable("matrix has no exact reviewed output")
    return resolved, "model-effort"


def stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the entire probe process group and reap its leader."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=0.20)
    except subprocess.TimeoutExpired:
        pass
    # Send KILL even if the leader already exited: a descendant may still hold the
    # stdout pipe and remain in the probe's process group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=0.50)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def capture_version_output(process: subprocess.Popen[bytes]) -> bytes:
    """Read a probe incrementally under hard byte and wall-clock bounds."""

    if process.stdout is None:
        stop_process_group(process)
        raise EvidenceUnavailable("agy version probe has no stdout pipe")
    stream = process.stdout
    descriptor = stream.fileno()
    os.set_blocking(descriptor, False)
    deadline = time.monotonic() + VERSION_TIMEOUT_SECONDS
    captured = bytearray()
    eof = False
    try:
        while not (eof and process.poll() is not None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EvidenceUnavailable("agy version probe timed out")
            readable: list[int] = []
            if not eof:
                try:
                    readable, _, _ = select.select(
                        [descriptor], [], [], min(remaining, 0.10)
                    )
                except InterruptedError:
                    continue
            if readable:
                try:
                    chunk = os.read(
                        descriptor,
                        VERSION_OUTPUT_LIMIT + 1 - len(captured),
                    )
                except BlockingIOError:
                    continue
                if chunk:
                    captured.extend(chunk)
                    if len(captured) > VERSION_OUTPUT_LIMIT:
                        raise EvidenceUnavailable(
                            "agy version probe failed or was oversized"
                        )
                else:
                    eof = True
            elif eof:
                try:
                    process.wait(timeout=min(remaining, 0.10))
                except subprocess.TimeoutExpired:
                    continue
        return bytes(captured)
    except BaseException:
        stop_process_group(process)
        raise
    finally:
        stream.close()


def probe_installed_version(expected: str) -> str:
    executable = shutil.which("agy")
    if not executable:
        raise EvidenceUnavailable("agy is unavailable on PATH")
    process: subprocess.Popen[bytes] | None = None
    watched_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous_handlers = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in watched_signals
    }

    def interrupt_probe(signal_number: int, _frame: Any) -> None:
        raise ProbeInterrupted(signal_number)

    try:
        for signal_number in watched_signals:
            signal.signal(signal_number, interrupt_probe)
        try:
            process = subprocess.Popen(
                [executable, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise EvidenceUnavailable("agy version probe could not start") from exc
        raw = capture_version_output(process)
    except ProbeInterrupted:
        if process is not None:
            stop_process_group(process)
        raise
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)
    assert process is not None
    if process.returncode != 0:
        raise EvidenceUnavailable("agy version probe failed or was oversized")

    if not raw.endswith(b"\n") or raw.count(b"\n") != 1 or b"\x00" in raw:
        raise EvidenceUnavailable("agy version output is empty or malformed")
    try:
        line = raw[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise EvidenceUnavailable("agy version output is not ASCII") from exc
    match = re.fullmatch(rf"(?:agy\s+)?({VERSION_RE.pattern})", line)
    if match is None:
        raise EvidenceUnavailable("agy version output lacks documented semantic content")
    installed = match.group(1)
    if installed != expected:
        raise ReviewRequired("installed agy version differs from the reviewed matrix")
    return installed


def resolve_selection(
    model: str,
    effort: str | None,
    model_source: str,
    effort_source: str | None,
    *,
    probe_version: bool,
) -> dict[str, Any]:
    if not model or model.strip() != model:
        raise CallerError("--model must be non-empty and unpadded")
    if effort is not None and (not effort or effort.strip() != effort):
        raise CallerError("--effort must be non-empty and unpadded")
    if model_source not in SOURCE_NAMES or (effort_source is not None and effort_source not in SOURCE_NAMES):
        raise CallerError("selector provenance must be cli or environment")
    if effort is None and effort_source is not None:
        raise CallerError("effort provenance was supplied without an effort")
    if effort is not None and effort_source is None:
        raise CallerError("effort provenance is required with an effort")

    matrix, matrix_sha, matrix_version, matrix_revision = load_policy()
    resolved, mode = resolve_model(matrix, model, effort)
    installed = probe_installed_version(matrix_version) if probe_version else None
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "agy-worker-selection",
        "selection_mode": mode,
        "user_model": model,
        "user_model_source": model_source,
        "resolved_agy_model": resolved,
        "matrix_sha256": matrix_sha,
        "matrix_agy_version": matrix_version,
        "matrix_source_revision": matrix_revision,
    }
    if installed is not None:
        result["installed_agy_version"] = installed
    if effort is not None:
        result["user_effort"] = effort
        result["user_effort_source"] = effort_source
    return result


def resolve_tier_selection(tier: str, source: str) -> dict[str, Any]:
    if not tier:
        raise CallerError("--tier must not be empty")
    if source not in TIER_SOURCES:
        raise CallerError("tier provenance must be cli, environment, or implicit-default")
    record: dict[str, Any] = {
        "schema_version": 1,
        "kind": "agy-worker-selection",
        "selection_mode": "tier",
        "selected_tier": tier,
        "selected_tier_source": source,
        "resolved_agy_model": None,
    }
    if tier != "default":
        record["resolved_agy_model"] = TIER_MODEL_BY_NAME.get(tier, tier)
    return record


def resolve_literal_selection(model: str) -> dict[str, Any]:
    """Record one caller-owned literal without consulting compatibility policy."""

    if not isinstance(model, str) or len(model) > 128 or LITERAL_MODEL_RE.fullmatch(model) is None:
        raise CallerError("--literal-model must be one exact lowercase model slug")
    return {
        "schema_version": 1,
        "kind": "agy-worker-selection",
        "selection_mode": "literal-model",
        "user_model": model,
        "user_model_source": "cli",
        "resolved_agy_model": model,
        "compatibility_status": "unreconciled-pass-through",
    }


def require_exact_fields(record: dict[str, Any], expected: set[str]) -> None:
    actual = set(record)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append(f"missing={','.join(missing)}")
        if extra:
            detail.append(f"extra={','.join(extra)}")
        raise CallerError(f"selection record has invalid fields ({'; '.join(detail)})")


def require_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise CallerError(f"selection record {key} must be a non-empty unpadded string")
    return value


def validate_selection_record_shape(record: dict[str, Any]) -> None:
    """Validate the side-effect-free v1 selection-record shape and provenance."""

    if not isinstance(record, dict):
        raise CallerError("selection record must be one JSON object")
    if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        raise CallerError("selection record schema_version must be 1")
    if record.get("kind") != "agy-worker-selection":
        raise CallerError("selection record kind is invalid")
    mode = record.get("selection_mode")
    if mode == "tier":
        require_exact_fields(record, TIER_RECORD_FIELDS)
        tier = require_string(record, "selected_tier")
        source = record.get("selected_tier_source")
        if source not in TIER_SOURCES:
            raise CallerError("selection record tier source is invalid")
        if source == "implicit-default" and tier != "default":
            raise CallerError("implicit-default provenance is reserved for the agy-owned default")
        expected = None if tier == "default" else TIER_MODEL_BY_NAME.get(tier, tier)
        if record.get("resolved_agy_model") != expected:
            raise CallerError("selection record tier resolution is inconsistent")
        return

    if mode == "literal-model":
        require_exact_fields(record, LITERAL_RECORD_FIELDS)
        user_model = require_string(record, "user_model")
        if len(user_model) > 128 or LITERAL_MODEL_RE.fullmatch(user_model) is None:
            raise CallerError("selection record literal model is invalid")
        if record.get("user_model_source") != "cli":
            raise CallerError("selection record literal source is invalid")
        if record.get("resolved_agy_model") != user_model:
            raise CallerError("selection record literal resolution is inconsistent")
        if record.get("compatibility_status") != "unreconciled-pass-through":
            raise CallerError("selection record literal compatibility status is invalid")
        return

    if mode not in ("exact-model", "model-effort"):
        raise CallerError("selection record mode is invalid")
    require_exact_fields(
        record,
        EFFORT_RECORD_FIELDS if mode == "model-effort" else DIRECT_RECORD_FIELDS,
    )
    user_model = require_string(record, "user_model")
    resolved_model = require_string(record, "resolved_agy_model")
    if record.get("user_model_source") not in SOURCE_NAMES:
        raise CallerError("selection record model source is invalid")
    installed = require_string(record, "installed_agy_version")
    matrix_version = require_string(record, "matrix_agy_version")
    if VERSION_RE.fullmatch(installed) is None or installed != matrix_version:
        raise CallerError("selection record installed version is invalid")
    if VERSION_RE.fullmatch(matrix_version) is None:
        raise CallerError("selection record matrix version is invalid")
    matrix_sha = require_string(record, "matrix_sha256")
    revision = require_string(record, "matrix_source_revision")
    if SHA256_RE.fullmatch(matrix_sha) is None:
        raise CallerError("selection record matrix SHA-256 is invalid")
    if REVISION_RE.fullmatch(revision) is None:
        raise CallerError("selection record source revision is invalid")
    effort = None
    if mode == "model-effort":
        effort = require_string(record, "user_effort")
        if effort not in compatibility.EFFORTS:
            raise CallerError("selection record effort is invalid")
        if record.get("user_effort_source") not in SOURCE_NAMES:
            raise CallerError("selection record effort source is invalid")
    elif resolved_model != user_model:
        raise CallerError("selection record exact model resolution is inconsistent")


def validate_selection_record(record: dict[str, Any]) -> None:
    """Validate the exact semantic artifact contract against reviewed policy."""

    validate_selection_record_shape(record)
    mode = record["selection_mode"]
    if mode in ("tier", "literal-model"):
        return
    user_model = record["user_model"]
    resolved_model = record["resolved_agy_model"]
    matrix_sha = record["matrix_sha256"]
    matrix_version = record["matrix_agy_version"]
    revision = record["matrix_source_revision"]
    effort = record.get("user_effort")
    matrix, current_sha, current_version, current_revision = load_policy()
    if (matrix_sha, matrix_version, revision) != (
        current_sha,
        current_version,
        current_revision,
    ):
        raise ReviewRequired("selection record compatibility provenance drifted")
    expected_model, expected_mode = resolve_model(matrix, user_model, effort)
    if mode != expected_mode or resolved_model != expected_model:
        raise CallerError("selection record direct resolution is inconsistent")


def read_selection_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CallerError("selection record input must be one real file")
    try:
        payload = read_bounded(path)
        record = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CallerError("selection record input is not bounded valid JSON") from exc
    validate_selection_record(record)
    return record


def publish_record(path: Path, record: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise CallerError("selection output must be a new file in an existing directory")
    payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.parent / f".{path.name}.{os.getpid()}"
    descriptor = -1
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Validate the exact bytes that will be published. The temporary remains
        # private and the destination does not exist until this check succeeds.
        candidate = read_selection_record(temporary)
        if candidate != record:
            raise CallerError("selection record changed before publication")
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_parser() -> UsageParser:
    parser = UsageParser(prog="model-selection.sh")
    parser.add_argument("--tier", action="append")
    parser.add_argument("--tier-source", action="append", choices=TIER_SOURCES)
    parser.add_argument("--model", action="append")
    parser.add_argument("--literal-model", action="append")
    parser.add_argument("--effort", action="append")
    parser.add_argument("--model-source", action="append", choices=SOURCE_NAMES)
    parser.add_argument("--effort-source", action="append", choices=SOURCE_NAMES)
    parser.add_argument("--output", action="append")
    parser.add_argument("--validate-record", action="append")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    tier = one(parser, args.tier, "--tier", False)
    tier_source = one(parser, args.tier_source, "--tier-source", False)
    model = one(parser, args.model, "--model", False)
    literal_model = one(parser, args.literal_model, "--literal-model", False)
    effort = one(parser, args.effort, "--effort", False)
    model_source = one(parser, args.model_source, "--model-source", False) or "cli"
    effort_source = one(parser, args.effort_source, "--effort-source", False)
    output = one(parser, args.output, "--output", False)
    validate_record = one(parser, args.validate_record, "--validate-record", False)
    if validate_record is not None:
        if any(
            value is not None
            for value in (
                args.tier,
                args.tier_source,
                args.model,
                args.literal_model,
                args.effort,
                args.model_source,
                args.effort_source,
                args.output,
            )
        ):
            parser.error("--validate-record is mutually exclusive with selection inputs")
        try:
            read_selection_record(Path(validate_record))
        except CallerError as exc:
            print(f"model-selection: {exc}", file=sys.stderr)
            return 64
        except ReviewRequired as exc:
            print(f"model-selection: review-required - {exc}", file=sys.stderr)
            return 7
        except EvidenceUnavailable as exc:
            print(f"model-selection: evidence-unavailable - {exc}", file=sys.stderr)
            return 8
        return 0
    if sum(value is not None for value in (tier, model, literal_model)) != 1:
        parser.error("exactly one of --tier, --model, or --literal-model is required")
    if tier is not None and (
        effort is not None or args.model_source is not None or effort_source is not None
    ):
        parser.error("--tier is mutually exclusive with model and effort inputs")
    if literal_model is not None and (
        effort is not None
        or args.model_source is not None
        or effort_source is not None
        or tier_source is not None
    ):
        parser.error("--literal-model is CLI-only and mutually exclusive with tier/model/effort provenance")
    if tier is None and tier_source is not None:
        parser.error("--tier-source requires --tier")
    if tier is not None and tier_source is None:
        tier_source = "cli"
    if effort is not None and effort_source is None:
        effort_source = "cli"
    if effort is None and effort_source is not None:
        parser.error("--effort-source requires --effort")
    try:
        if tier is not None:
            record = resolve_tier_selection(tier, tier_source or "cli")
        elif literal_model is not None:
            record = resolve_literal_selection(literal_model)
        else:
            record = resolve_selection(
                model or "", effort, model_source, effort_source, probe_version=True
            )
        if output is None:
            json.dump(record, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        else:
            publish_record(Path(output), record)
            print(record.get("resolved_agy_model") or "")
    except CallerError as exc:
        print(f"model-selection: {exc}", file=sys.stderr)
        return 64
    except ReviewRequired as exc:
        print(f"model-selection: review-required - {exc}", file=sys.stderr)
        return 7
    except EvidenceUnavailable as exc:
        print(f"model-selection: evidence-unavailable - {exc}", file=sys.stderr)
        return 8
    except ProbeInterrupted as exc:
        return 128 + exc.signal_number
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
