#!/usr/bin/env python3
"""Sidecar maintenance tool for classifying agy 1.1.22 models-capture failure records.

This tool does not modify historical runner semantics, does not scan the filesystem,
and never grants routing, retry, activation, or inventory authority. It accepts one
explicit evidence-root directory, enforces strict fail-closed structural/ownership/hash
checks, and classifies sanitized failure records into exactly one of:
authentication, provider_permission, quota, service, timeout, local_environment, unknown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
from typing import Any, Optional, Sequence


EXPECTED_RUNNER_SHA256 = "c878d68c12017733878e463008eddb1d97213963675f567c47e1dd41e06586bc"
EXPECTED_SOURCE_SHA256 = "7b1317779085913d338bde0e9b39b72323d9083a879525f944fd469c8ecca906"
EXPECTED_RECOVERY_BINDING_SHA256 = "d9d830e65d3a5c76df6d9e07e6ea7e14e14f290ab4036bdbae8cb33502e29f2a"
OUTPUT_PROFILE_NAME = "models.capture.1.1.22.profile.json"
OUTPUT_CLASSIFICATION_NAME = "models.capture.classification.json"
SCRATCH_NAMES = frozenset({"cwd", "tmp", "xdg-cache", "xdg-config", "xdg-state"})
EXPECTED_FILES = frozenset({
    "models_capture_1_1_22_runner.py",
    "models_capture_1_1_22_runner.py.sha256",
    OUTPUT_PROFILE_NAME,
    "models.stdout",
    "models.stderr",
    "models.capture.failure.json",
})

STREAM_LIMIT = 65536
MAX_EVIDENCE_BYTES = 16384
MAX_RUNNER_BYTES = 131072
MAX_PROFILE_BYTES = 16384

RULESET_VERSION = "agy-1.1.22-failure-rules-v1"
CATEGORIES = (
    "authentication",
    "provider_permission",
    "quota",
    "service",
    "timeout",
    "local_environment",
    "unknown",
)

# Canonical versioned classification rules for agy 1.1.22 models capture failure stderr.
RULESET_PATTERNS: dict[str, list[str]] = {
    "authentication": [
        r"(?i)\b(?:unauthenticated|authentication\s+failed|invalid\s+(?:api\s+key|credentials)|api_key_invalid|oauth2:\s+cannot\s+fetch\s+token|credentials\s+expired|login\s+required|not\s+logged\s+in)\b",
        r"(?i)\bstatus(?:\s+code)?:\s*401\b",
        r"(?i)\bHTTP/?[0-9.]*\s+401\b",
    ],
    "provider_permission": [
        r"(?i)\b(?:permission_denied|permissiondenied)\b",
        r"(?i)\b(?:user\s+does\s+not\s+have\s+permission|iam\s+permission|access\s+denied\s+by\s+policy|forbidden\s+by\s+policy)\b",
        r"(?i)\bstatus(?:\s+code)?:\s*403\b",
        r"(?i)\bHTTP/?[0-9.]*\s+403\b",
    ],
    "quota": [
        r"(?i)\b(?:resource_exhausted|resourceexhausted)\b",
        r"(?i)\b(?:quota\s+exceeded|rate\s+limit\s+exceeded|out\s+of\s+quota|individual\s+quota\s+reached|too\s+many\s+requests)\b",
        r"(?i)\bstatus(?:\s+code)?:\s*429\b",
        r"(?i)\bHTTP/?[0-9.]*\s+429\b",
    ],
    "service": [
        r"(?i)\b(?:service_unavailable|bad_gateway|gateway_timeout|backend_error|internal_server_error)\b",
        r"(?i)\bstatus(?:\s+code)?:\s*(?:500|502|503|504)\b",
        r"(?i)\bHTTP/?[0-9.]*\s+(?:500|502|503|504)\b",
        r"(?i)\b(?:connection\s+reset\s+by\s+peer|connection\s+refused\s+by\s+server)\b",
    ],
    "timeout": [
        r"(?i)\b(?:context\s+deadline\s+exceeded|client\.timeout\s+exceeded|timed?\s*out\s+waiting\s+for\s+response|connection\s+timed?\s*out|request\s+timeout)\b",
        r"(?i)\bstatus(?:\s+code)?:\s*408\b",
    ],
    "local_environment": [
        r"(?i)\b(?:open|create|write|access|mkdir|stat|read)\s+.*?:\s*permission\s+denied\b",
        r"(?i)\b(?:open|create|write|access|mkdir|stat|read)\s+.*?:\s*operation\s+not\s+permitted\b",
        r"(?i)\bfailed\s+to\s+open\s+(?:log|crash|trace|config|state|cache|output).*?:\s*permission\s+denied\b",
        r"(?i)\bfailed\s+to\s+open\s+(?:log|crash|trace|config|state|cache|output).*?:\s*operation\s+not\s+permitted\b",
        r"(?i)\bpermission\s+denied\s+.*?(?:/\.gemini/|/logs?/|/tmp/|crash\.log|agy\.log)\b",
        r"(?i)\b(?:panic:\s+open|fatal:\s+open).*?:\s*permission\s+denied\b",
        r"(?i)\b(?:bind|listen(?:\s+tcp)?)\s+(?:127\.0\.0\.1|\[::1\]|localhost)?.*?:?\s*permission\s+denied\b",
        r"(?i)\b(?:bind|listen)\s+tcp\s+(?:127\.0\.0\.1|\[::1\]):[0-9]+:\s*permission\s+denied\b",
        r"(?i)\b(?:bind|listen(?:\s+tcp)?)\s+(?:127\.0\.0\.1|\[::1\]|localhost)?.*?:?\s*operation\s+not\s+permitted\b",
        r"(?i)\b(?:bind|listen)\s+tcp\s+(?:127\.0\.0\.1|\[::1\]):[0-9]+:\s*(?:bind:\s*)?operation\s+not\s+permitted\b",
        r"(?i)\bsocket:\s*permission\s+denied\b",
        r"(?i)\baddress\s+already\s+in\s+use\b",
        r"(?i)\bfailed\s+to\s+bind\s+loopback\b",
        r"(?i)\b(?:exec format error|read-only file system|no space left on device|too many open files)\b",
    ],
}


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        + b"\n"
    )


RULESET_CANONICAL_BYTES = _canonical({
    "categories": list(CATEGORIES[:-1]),
    "patterns": RULESET_PATTERNS,
    "version": RULESET_VERSION,
})
RULESET_SHA256 = hashlib.sha256(RULESET_CANONICAL_BYTES).hexdigest()


class ClassificationError(ValueError):
    """Raised when evidence verification fails closed or record is malformed."""
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ClassificationError("duplicate JSON object key")
        result[key] = value
    return result


def _json_loads_strict(data: bytes, max_bytes: int = MAX_EVIDENCE_BYTES) -> dict[str, Any]:
    if not data or len(data) > max_bytes:
        raise ClassificationError("JSON data exceeds size limit or is empty")
    try:
        val = json.loads(data.decode("utf-8", "strict"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClassificationError("invalid JSON encoding or syntax") from exc
    if not isinstance(val, dict):
        raise ClassificationError("JSON root must be an object")
    return val


def _normalize_abs_path(path_str: str) -> str:
    if not isinstance(path_str, str) or not path_str or not os.path.isabs(path_str):
        raise ClassificationError("path must be non-empty absolute")
    norm = os.path.normpath(path_str)
    if norm != path_str:
        raise ClassificationError("path is not normalized")
    try:
        real = os.path.realpath(path_str)
    except OSError as exc:
        raise ClassificationError("failed to resolve real path") from exc
    if real != path_str:
        raise ClassificationError("path contains symlinks")
    return path_str


def _read_file_at(
    dir_fd: int,
    name: str,
    max_size: int,
    expected_mode: Optional[int] = None,
) -> tuple[bytes, str]:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, os.O_RDONLY | no_follow | cloexec, dir_fd=dir_fd)
    except (FileNotFoundError, OSError) as exc:
        raise ClassificationError("failed to open required file") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ClassificationError("file is not a regular file")
        if st.st_uid != os.getuid():
            raise ClassificationError("file is not owned by current user")
        if expected_mode is not None and stat.S_IMODE(st.st_mode) != expected_mode:
            raise ClassificationError("file has unexpected mode")
        if st.st_nlink != 1:
            raise ClassificationError("file has hard links")
        if st.st_size > max_size:
            raise ClassificationError("file size exceeds bound")
        data = os.read(fd, max_size + 1)
        if len(data) != st.st_size or len(data) > max_size:
            raise ClassificationError("file size changed during read")
        digest = hashlib.sha256(data).hexdigest()
        return data, digest
    finally:
        os.close(fd)


def classify_stderr_bytes(stderr_bytes: bytes) -> tuple[str, list[str]]:
    """Evaluate sanitized rulesets against stderr bytes."""
    text = stderr_bytes.decode("utf-8", "replace")
    matched_categories: list[str] = []
    matched_rules: list[str] = []

    for category, patterns in RULESET_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                matched_categories.append(category)
                matched_rules.append(f"{category}:{pattern}")
                break

    unique_matched = sorted(set(matched_categories))
    if len(unique_matched) == 1:
        return unique_matched[0], matched_rules
    return "unknown", matched_rules


def _get_classifier_sha256() -> str:
    this_file = _normalize_abs_path(os.path.realpath(__file__))
    try:
        with open(this_file, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError as exc:
        raise ClassificationError("failed to read classifier source") from exc


def classify_evidence_root(
    evidence_root_path: str,
    output_path: Optional[str] = None,
) -> dict[str, Any]:
    """Verify evidence root strictly and classify sanitized failure record."""
    norm_root = _normalize_abs_path(evidence_root_path)

    # Check root directory permissions and ownership
    try:
        st_root = os.stat(norm_root, follow_symlinks=False)
    except OSError as exc:
        raise ClassificationError("cannot stat evidence root") from exc

    if not stat.S_ISDIR(st_root.st_mode):
        raise ClassificationError("evidence root is not a directory")
    if st_root.st_uid != os.getuid():
        raise ClassificationError("evidence root is not owned by current user")
    if stat.S_IMODE(st_root.st_mode) != 0o700:
        raise ClassificationError("evidence root mode is not 0700")

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)

    try:
        dir_fd = os.open(norm_root, os.O_RDONLY | no_follow | cloexec | directory_flag)
    except OSError as exc:
        raise ClassificationError("cannot open evidence root dir_fd") from exc

    try:
        entries = set(os.listdir(dir_fd))

        # Check for unexpected files/directories
        allowed_entries = set(EXPECTED_FILES) | set(SCRATCH_NAMES) | {OUTPUT_CLASSIFICATION_NAME}
        unexpected = entries - allowed_entries
        if unexpected:
            raise ClassificationError("evidence root contains unexpected entries")

        # Check required files exist
        missing_files = EXPECTED_FILES - entries
        if missing_files:
            raise ClassificationError("evidence root is missing required files")

        # Verify scratch directories
        for scratch_name in SCRATCH_NAMES:
            if scratch_name not in entries:
                raise ClassificationError("missing scratch directory")
            try:
                s_fd = os.open(scratch_name, os.O_RDONLY | no_follow | cloexec | directory_flag, dir_fd=dir_fd)
            except OSError as exc:
                raise ClassificationError("failed to open scratch dir") from exc
            try:
                s_st = os.fstat(s_fd)
                if not stat.S_ISDIR(s_st.st_mode):
                    raise ClassificationError("scratch entry is not a directory")
                if s_st.st_uid != os.getuid():
                    raise ClassificationError("scratch entry is not owned by current user")
                if stat.S_IMODE(s_st.st_mode) != 0o700:
                    raise ClassificationError("scratch entry mode is not 0700")
                scratch_contents = os.listdir(s_fd)
                if scratch_contents:
                    raise ClassificationError("scratch directory is not empty")
            finally:
                os.close(s_fd)

        # Read and verify runner file and its hash
        runner_bytes, runner_sha = _read_file_at(
            dir_fd,
            "models_capture_1_1_22_runner.py",
            MAX_RUNNER_BYTES,
            0o600,
        )
        if runner_sha != EXPECTED_RUNNER_SHA256:
            raise ClassificationError("runner sha256 mismatch")

        runner_sha_file_bytes, _ = _read_file_at(dir_fd, "models_capture_1_1_22_runner.py.sha256", 128, 0o600)
        expected_runner_sha_text = (EXPECTED_RUNNER_SHA256 + "\n").encode("ascii")
        if runner_sha_file_bytes != expected_runner_sha_text:
            raise ClassificationError("models_capture_1_1_22_runner.py.sha256 content mismatch")

        # Read profile
        profile_bytes, profile_sha = _read_file_at(dir_fd, OUTPUT_PROFILE_NAME, MAX_PROFILE_BYTES, 0o600)

        # Read stdout and stderr
        stdout_bytes, stdout_sha = _read_file_at(dir_fd, "models.stdout", STREAM_LIMIT, 0o600)
        stderr_bytes, stderr_sha = _read_file_at(dir_fd, "models.stderr", STREAM_LIMIT, 0o600)

        # Read failure record
        failure_bytes, failure_sha = _read_file_at(dir_fd, "models.capture.failure.json", MAX_EVIDENCE_BYTES, 0o600)
        failure_dict = _json_loads_strict(failure_bytes)

        # Verify failure record semantics
        if failure_dict.get("claim") != "models-capture-failure":
            raise ClassificationError("unexpected failure claim")
        if failure_dict.get("status") != "child-failed":
            raise ClassificationError("unexpected failure status")
        if failure_dict.get("source_sha256") != EXPECTED_SOURCE_SHA256:
            raise ClassificationError("failure record source_sha256 mismatch")
        if failure_dict.get("version_binding_sha256") != EXPECTED_RECOVERY_BINDING_SHA256:
            raise ClassificationError("failure record version_binding_sha256 mismatch")
        if failure_dict.get("runner_sha256") != EXPECTED_RUNNER_SHA256:
            raise ClassificationError("failure record runner_sha256 mismatch")
        if failure_dict.get("input_profile_sha256") != profile_sha:
            raise ClassificationError("failure record input_profile_sha256 mismatch")

        artifacts = failure_dict.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ClassificationError("failure record artifacts must be an object")
        if artifacts.get("models.stdout") != stdout_sha:
            raise ClassificationError("failure record artifacts models.stdout hash mismatch")
        if artifacts.get("models.stderr") != stderr_sha:
            raise ClassificationError("failure record artifacts models.stderr hash mismatch")

        observation = failure_dict.get("observation")
        if not isinstance(observation, dict):
            raise ClassificationError("failure record observation must be an object")
        exit_code = observation.get("exit")
        if not isinstance(exit_code, int) or exit_code == 0:
            raise ClassificationError("invalid failure observation exit code")
        if observation.get("popen_count") != 1:
            raise ClassificationError("failure observation popen_count must be 1")

        limitations = failure_dict.get("limitations")
        if not isinstance(limitations, dict):
            raise ClassificationError("failure record limitations must be an object")
        expected_limitations = {
            "accepted_inventory": False,
            "failure_classified": False,
            "inventory_interpreted": False,
            "metadata_advance_authorized": False,
            "metadata_updated": False,
            "provider_backend_proven": False,
            "routing_authority": False,
            "routing_authorized": False,
        }
        if limitations != expected_limitations:
            raise ClassificationError("failure record limitations mismatch")

        # Classify sanitized failure stderr
        category, matched_rules = classify_stderr_bytes(stderr_bytes)

        classifier_sha = _get_classifier_sha256()

        classification_record: dict[str, Any] = {
            "category": category,
            "classifier_sha256": classifier_sha,
            "enforced_limits": {
                "max_evidence_bytes": MAX_EVIDENCE_BYTES,
                "max_stream_bytes": STREAM_LIMIT,
                "single_ruleset_match_required": True,
            },
            "evidence_hashes": {
                "failure_record_sha256": failure_sha,
                "input_profile_sha256": profile_sha,
                "runner_sha256": runner_sha,
                "source_sha256": EXPECTED_SOURCE_SHA256,
                "stderr_sha256": stderr_sha,
                "stdout_sha256": stdout_sha,
                "version_binding_sha256": EXPECTED_RECOVERY_BINDING_SHA256,
            },
            "limitations": {
                "accepted_inventory": False,
                "activation_authorized": False,
                "inventory_interpreted": False,
                "metadata_advance_authorized": False,
                "retry_authorized": False,
                "routing_authority": False,
            },
            "origin": "agy-1.1.22-models-capture",
            "ruleset_sha256": RULESET_SHA256,
            "ruleset_version": RULESET_VERSION,
            "status": "classified",
        }

        canonical_output = _canonical(classification_record)

        # Write output file if requested or write to evidence root
        target_out_path = output_path
        if target_out_path is None:
            target_out_path = os.path.join(norm_root, OUTPUT_CLASSIFICATION_NAME)
        else:
            target_out_path = _normalize_abs_path(target_out_path)

        # Mode-0600 no-overwrite atomic file write
        out_parent = os.path.dirname(target_out_path)
        out_name = os.path.basename(target_out_path)
        parent_fd = os.open(out_parent, os.O_RDONLY | no_follow | cloexec | directory_flag)
        try:
            parent_stat = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(parent_stat.st_mode)
                or parent_stat.st_uid != os.getuid()
                or stat.S_IMODE(parent_stat.st_mode) != 0o700
            ):
                raise ClassificationError("output parent is not owner-private")
            out_fd = os.open(
                out_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow | cloexec,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                written = 0
                while written < len(canonical_output):
                    n = os.write(out_fd, canonical_output[written:])
                    if n <= 0:
                        raise ClassificationError("failed to write classification output")
                    written += n
                os.fsync(out_fd)
            finally:
                os.close(out_fd)
            os.fsync(parent_fd)
        except FileExistsError as exc:
            raise ClassificationError("output classification file already exists") from exc
        finally:
            os.close(parent_fd)

        return classification_record

    finally:
        os.close(dir_fd)


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="models_capture_1_1_22_classifier.py",
        description="Sidecar maintenance classifier for agy 1.1.22 models capture failure records.",
    )
    parser.add_argument(
        "--evidence-root",
        metavar="PATH",
        help="Explicit absolute path to owner-private 1.1.22 capture failure root.",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Optional explicit output path for mode-0600 classification JSON record.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Stdout output format (default: json).",
    )
    parser.add_argument(
        "--validate-ruleset",
        action="store_true",
        help="Validate static ruleset definition and print ruleset SHA-256.",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 64 if exc.code != 0 else 0

    if args.validate_ruleset:
        sys.stdout.write(f"ruleset_version={RULESET_VERSION}\nruleset_sha256={RULESET_SHA256}\n")
        return 0

    if not args.evidence_root:
        sys.stderr.write("error: --evidence-root PATH is required\n")
        return 64

    try:
        record = classify_evidence_root(args.evidence_root, args.output)
    except ClassificationError as exc:
        sys.stderr.write(f"classification error: {exc}\n")
        return 1
    except Exception:
        sys.stderr.write("unexpected classification error\n")
        return 1

    if args.format == "json":
        sys.stdout.buffer.write(_canonical(record))
    else:
        sys.stdout.write(
            f"Category: {record['category']}\n"
            f"Origin: {record['origin']}\n"
            f"Ruleset: {record['ruleset_version']} ({record['ruleset_sha256'][:16]}...)\n"
            f"Classifier SHA-256: {record['classifier_sha256'][:16]}...\n"
            f"Failure Record SHA-256: {record['evidence_hashes']['failure_record_sha256'][:16]}...\n"
            f"Stderr SHA-256: {record['evidence_hashes']['stderr_sha256'][:16]}...\n"
            f"Status: {record['status']}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
