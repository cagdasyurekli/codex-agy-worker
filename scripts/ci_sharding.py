#!/usr/bin/env python3
"""Exact-PR-head fail-closed CI sharding runner and aggregate verifier."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, BinaryIO, Callable, Sequence

sys.dont_write_bytecode = True

MAX_RECEIPT_BYTES = 64 * 1024
MAX_RECEIPT_TREE_ENTRIES = 32
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
CONTROL_LABEL = b"@@agy-worker-ci-shard:"
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
    ("models-capture-1-1-22-reprofile", "fixed 1.1.22 models capture reprofile adapter"),
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

STAGE_MAP: dict[str, str] = dict(STAGES)
ANNOUNCE_MAP: dict[str, str] = {ann: stage_id for stage_id, ann in STAGES}

SHARDS: dict[str, tuple[str, ...]] = {
    "dispatcher": (
        "dispatcher",
    ),
    "dispatcher-remediation": (
        "dispatcher-remediation",
    ),
    "other-a": (
        "diff-hygiene",
        "shell-syntax",
        "python-syntax",
        "job-lifecycle",
        "updater",
        "version-bootstrap-runner",
        "version-initial-bootstrap-runner",
        "version-recovery-1-1-12-runner",
        "models-attestation-runner",
        "models-capture-profile",
        "models-capture-1-1-12-profile",
        "models-capture-1-1-16-profile",
        "models-capture-1-1-22-version-evidence",
        "models-capture-1-1-22-profile",
        "models-capture-1-1-22-runner",
        "agy-1-1-16-activation",
        "reporting",
        "feedback-triage",
        "packaging",
        "doctor",
        "proof-demo",
    ),
    "other-b": (
        "qa-gate",
        "evidence-receipt",
        "evidence-report",
        "offline-benchmark",
        "persona-evidence",
        "workload-profiles",
        "adoption-measurement",
        "update-notifier",
        "version-attestation-runner",
        "version-bootstrap-preflight",
        "version-attestation-harness",
        "models-capture-runner",
        "models-capture-1-1-12-runner",
        "models-capture-1-1-16-version-evidence",
        "models-capture-1-1-16-runner",
        "models-capture-1-1-22-reprofile",
        "models-capture-1-1-22-classifier",
        "codex-usage-report",
        "conformance",
        "bytecode-hygiene",
    ),
}


class ShardingError(Exception):
    """A CI sharding invariant, receipt, or aggregate validation failed closed."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def inventory_digest() -> str:
    return hashlib.sha256(canonical_bytes(STAGES)).hexdigest()


def validate_receipt(
    data: dict[str, Any],
    expected_head_sha: str | None = None,
    expected_inventory_sha: str | None = None,
) -> None:
    root_keys = {
        "schema_version",
        "kind",
        "head_sha",
        "inventory_sha256",
        "shard_id",
        "expected_stage_ids",
        "observed_stage_ids",
        "outcome",
        "integrity",
    }
    if not isinstance(data, dict) or set(data) != root_keys:
        raise ShardingError("shard receipt root keys mismatch")
    if data["schema_version"] != 1 or data["kind"] != "agy-worker-ci-shard-receipt":
        raise ShardingError("shard receipt identity mismatch")
    if not isinstance(data["head_sha"], str) or not COMMIT_RE.fullmatch(data["head_sha"]):
        raise ShardingError("head_sha must be a lowercase 40-character Git object ID")
    if expected_head_sha is not None and data["head_sha"] != expected_head_sha:
        raise ShardingError("head_sha does not match expected head SHA")
    digest = data["inventory_sha256"]
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ShardingError("inventory_sha256 must be lowercase SHA-256")
    if expected_inventory_sha is not None and digest != expected_inventory_sha:
        raise ShardingError("inventory_sha256 does not match canonical inventory")
    shard_id = data["shard_id"]
    if not isinstance(shard_id, str) or shard_id not in SHARDS:
        raise ShardingError(f"unknown shard_id {shard_id!r}")

    expected_stages = SHARDS[shard_id]
    if data["expected_stage_ids"] != list(expected_stages):
        raise ShardingError("expected_stage_ids does not match canonical shard membership")

    observed_stages = data["observed_stage_ids"]
    if not isinstance(observed_stages, list) or not all(isinstance(s, str) for s in observed_stages):
        raise ShardingError("observed_stage_ids must be a list of strings")

    outcome = data["outcome"]
    if outcome not in ("passed", "failed"):
        raise ShardingError(f"invalid outcome {outcome!r}")

    if outcome == "passed":
        if observed_stages != list(expected_stages):
            raise ShardingError("passed shard receipt must have observed all expected stages")
    else:
        if not observed_stages:
            raise ShardingError("failed shard receipt must record at least the stage that failed")
        if len(observed_stages) > len(expected_stages):
            raise ShardingError("failed shard receipt observed more stages than expected")
        if observed_stages != list(expected_stages[: len(observed_stages)]):
            raise ShardingError("failed shard receipt observed stages must be a prefix of expected stages")
    if data["integrity"] != {
        "signed": False,
        "tamper_evident": False,
        "statement": INTEGRITY_STATEMENT,
    }:
        raise ShardingError("integrity block mismatch")


def validate_publication_target(target_path: Path) -> Path:
    target = Path(os.path.abspath(os.fspath(target_path)))
    try:
        os.lstat(target)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ShardingError("cannot inspect shard receipt target") from exc
    else:
        raise ShardingError("shard receipt target already exists")
    try:
        parent_stat = os.lstat(target.parent)
    except OSError as exc:
        raise ShardingError("cannot inspect shard receipt parent") from exc
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise ShardingError("shard receipt parent must be a real directory")
    if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) != 0o700:
        raise ShardingError("shard receipt parent must be owner-private mode 0700")
    return target


def publish_receipt(target_path: Path, receipt: dict[str, Any]) -> None:
    validate_receipt(receipt, expected_inventory_sha=inventory_digest())
    target = validate_publication_target(target_path)
    raw = canonical_bytes(receipt) + b"\n"
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ShardingError("shard receipt exceeds fixed size bound")
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
            raise ShardingError("shard receipt parent changed before publication")
        try:
            file_fd = os.open(target.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise ShardingError("shard receipt target already exists") from exc
        try:
            view = memoryview(raw)
            while view:
                written = os.write(file_fd, view)
                if written <= 0:
                    raise ShardingError("cannot publish complete shard receipt")
                view = view[written:]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


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
        raise ShardingError("cannot bind shard receipt to Git state") from exc
    sha = head.stdout.decode("ascii", errors="replace").strip()
    if head.returncode != 0 or not COMMIT_RE.fullmatch(sha):
        raise ShardingError("cannot resolve an exact lowercase Git HEAD")
    if status_result.returncode != 0 or status_result.stdout:
        raise ShardingError("shard execution requires a clean tracked and untracked worktree")
    env_head = os.environ.get("AGY_WORKER_CI_HEAD_SHA", "").strip()
    if env_head and env_head != sha:
        raise ShardingError("AGY_WORKER_CI_HEAD_SHA does not match Git HEAD")
    return sha


def revalidate_clean_head(repo_root: Path, expected_head_sha: str) -> None:
    if resolve_clean_head(repo_root) != expected_head_sha:
        raise ShardingError("Git HEAD changed during shard execution")


def observe_stream(
    stream: BinaryIO,
    wait: Callable[[], int],
    head_sha: str,
    shard_id: str,
    nonce: str,
    output: BinaryIO | None = None,
) -> tuple[int, dict[str, Any]]:
    if output is None:
        output = sys.stdout.buffer
    if shard_id not in SHARDS:
        raise ShardingError(f"unknown shard_id {shard_id!r}")
    expected_stage_ids = SHARDS[shard_id]
    expected_announces = [STAGE_MAP[s].encode("utf-8") for s in expected_stage_ids]
    control_prefix = CONTROL_LABEL + nonce.encode("ascii") + b":"
    observed_stage_ids: list[str] = []
    control_error = False

    for line in iter(lambda: stream.readline(8192), b""):
        stripped = line.rstrip(b"\r\n")
        if stripped.startswith(CONTROL_LABEL):
            next_index = len(observed_stage_ids)
            if (
                not stripped.startswith(control_prefix)
                or next_index >= len(expected_announces)
                or stripped != control_prefix + expected_announces[next_index]
            ):
                control_error = True
                continue
            observed_stage_ids.append(expected_stage_ids[next_index])
            continue
        output.write(line)
        output.flush()

    return_code = wait()
    if control_error:
        raise ShardingError("shard control marker is duplicated, reordered, or invalid")
    if return_code == 0 and len(observed_stage_ids) != len(expected_stage_ids):
        raise ShardingError("successful shard run omitted expected stage announcement(s)")
    if return_code != 0 and not observed_stage_ids:
        raise ShardingError("shard failed before the first stage announcement")

    receipt = {
        "schema_version": 1,
        "kind": "agy-worker-ci-shard-receipt",
        "head_sha": head_sha,
        "inventory_sha256": inventory_digest(),
        "shard_id": shard_id,
        "expected_stage_ids": list(expected_stage_ids),
        "observed_stage_ids": list(observed_stage_ids),
        "outcome": "passed" if return_code == 0 else "failed",
        "integrity": {
            "signed": False,
            "tamper_evident": False,
            "statement": INTEGRITY_STATEMENT,
        },
    }
    validate_receipt(receipt, head_sha, inventory_digest())
    return return_code, receipt


def run_shard(repo_root: Path, shard_id: str, receipt_path: Path) -> int:
    if shard_id not in SHARDS:
        raise ShardingError(f"unknown shard_id {shard_id!r}")
    gate_script = repo_root / "scripts" / "ci-offline.sh"
    validate_publication_target(receipt_path)
    head_sha = resolve_clean_head(repo_root)
    nonce = secrets.token_hex(32)
    try:
        process = subprocess.Popen(
            [os.fspath(gate_script), "--shard-child", nonce, shard_id],
            cwd=str(repo_root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
    except OSError as exc:
        raise ShardingError("cannot start offline gate shard child") from exc
    assert process.stdout is not None
    try:
        return_code, receipt = observe_stream(
            process.stdout, process.wait, head_sha, shard_id, nonce
        )
    except Exception:
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                pass
        raise
    revalidate_clean_head(repo_root, head_sha)
    publish_receipt(receipt_path, receipt)
    return return_code


def validate_aggregate_receipts(
    receipts: Sequence[dict[str, Any]],
    expected_head_sha: str,
    expected_inventory_sha: str | None = None,
) -> None:
    if expected_inventory_sha is None:
        expected_inventory_sha = inventory_digest()
    if not isinstance(receipts, (list, tuple)) or len(receipts) != len(SHARDS):
        raise ShardingError(f"aggregate requires exactly {len(SHARDS)} shard receipts, got {len(receipts)}")

    observed_shard_ids: set[str] = set()
    all_observed_stages: list[str] = []

    for receipt in receipts:
        validate_receipt(receipt, expected_head_sha, expected_inventory_sha)
        shard_id = receipt["shard_id"]
        if shard_id in observed_shard_ids:
            raise ShardingError(f"duplicate shard receipt for {shard_id!r}")
        observed_shard_ids.add(shard_id)

        if receipt["outcome"] != "passed":
            raise ShardingError(f"shard {shard_id!r} reported outcome {receipt['outcome']!r}")

        all_observed_stages.extend(receipt["observed_stage_ids"])

    if observed_shard_ids != set(SHARDS):
        missing = set(SHARDS) - observed_shard_ids
        raise ShardingError(f"missing shard receipts: {sorted(missing)}")

    expected_all_stage_ids = [s[0] for s in STAGES]
    if len(all_observed_stages) != len(expected_all_stage_ids):
        raise ShardingError(
            f"aggregate observed {len(all_observed_stages)} stages, expected {len(expected_all_stage_ids)}"
        )
    if set(all_observed_stages) != set(expected_all_stage_ids):
        missing_stages = set(expected_all_stage_ids) - set(all_observed_stages)
        extra_stages = set(all_observed_stages) - set(expected_all_stage_ids)
        raise ShardingError(
            f"canonical stage inventory mismatch: missing={sorted(missing_stages)}, extra={sorted(extra_stages)}"
        )
    if len(set(all_observed_stages)) != len(all_observed_stages):
        raise ShardingError("duplicate stage ID observed across shards")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ShardingError("receipt JSON contains a duplicate object key")
        value[key] = item
    return value


def _read_receipt(file_path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        before = os.lstat(file_path)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RECEIPT_BYTES:
            raise ShardingError("receipt artifact is not a bounded regular file")
        fd = os.open(file_path, flags)
        try:
            opened = os.fstat(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > MAX_RECEIPT_BYTES
            ):
                raise ShardingError("receipt artifact changed before reading")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(fd, min(8192, MAX_RECEIPT_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RECEIPT_BYTES:
                    raise ShardingError("receipt artifact exceeds the fixed size bound")
        finally:
            os.close(fd)
    except OSError as exc:
        raise ShardingError("cannot read receipt artifact safely") from exc

    try:
        value = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShardingError("receipt artifact is not canonical UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ShardingError("receipt artifact root must be an object")
    return value


def load_receipts_from_dir(receipts_dir: Path) -> list[dict[str, Any]]:
    try:
        root_info = os.lstat(receipts_dir)
    except OSError as exc:
        raise ShardingError("receipt artifact root is unavailable") from exc
    if not stat.S_ISDIR(root_info.st_mode):
        raise ShardingError("receipt artifact root must be a real directory")

    receipt_files: list[Path] = []
    pending = [receipts_dir]
    entry_count = 0
    try:
        while pending:
            current = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_RECEIPT_TREE_ENTRIES:
                        raise ShardingError("receipt artifact tree exceeds the entry bound")
                    info = entry.stat(follow_symlinks=False)
                    path = Path(entry.path)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode) and entry.name.endswith(".json"):
                        receipt_files.append(path)
                    else:
                        raise ShardingError("receipt artifact tree contains an unexpected entry")
    except OSError as exc:
        raise ShardingError("cannot inspect receipt artifact tree safely") from exc

    if len(receipt_files) != len(SHARDS):
        raise ShardingError(
            f"aggregate requires exactly {len(SHARDS)} receipt artifacts, got {len(receipt_files)}"
        )
    return [_read_receipt(path) for path in sorted(receipt_files)]


def verify_aggregate(
    receipts_dir: Path,
    expected_head_sha: str,
    producer_result: str,
) -> None:
    if not COMMIT_RE.fullmatch(expected_head_sha):
        raise ShardingError(f"expected head SHA {expected_head_sha!r} is invalid")

    if producer_result != "success":
        raise ShardingError(f"producer jobs did not succeed: producer_result={producer_result!r}")

    receipts = load_receipts_from_dir(receipts_dir)
    validate_aggregate_receipts(receipts, expected_head_sha, inventory_digest())
    print(f"ok: verified aggregate of {len(receipts)} shard receipts for {expected_head_sha}")


def main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("ci sharding: rejected arguments\n")
        return 2

    command = argv[1]
    repo_root = Path(__file__).absolute().parent.parent

    try:
        if command == "run-shard":
            shard_id = ""
            receipt_path_str = ""
            seen_options: set[str] = set()
            idx = 2
            while idx < len(argv):
                arg = argv[idx]
                if arg == "--shard" and idx + 1 < len(argv):
                    if "shard" in seen_options:
                        raise ShardingError("duplicate shard option")
                    seen_options.add("shard")
                    shard_id = argv[idx + 1]
                    idx += 2
                elif arg.startswith("--shard="):
                    if "shard" in seen_options:
                        raise ShardingError("duplicate shard option")
                    seen_options.add("shard")
                    shard_id = arg.partition("=")[2]
                    idx += 1
                elif arg in ("--receipt", "--receipt-file") and idx + 1 < len(argv):
                    if "receipt" in seen_options:
                        raise ShardingError("duplicate receipt option")
                    seen_options.add("receipt")
                    receipt_path_str = argv[idx + 1]
                    idx += 2
                elif arg.startswith("--receipt=") or arg.startswith("--receipt-file="):
                    if "receipt" in seen_options:
                        raise ShardingError("duplicate receipt option")
                    seen_options.add("receipt")
                    receipt_path_str = arg.partition("=")[2]
                    idx += 1
                else:
                    sys.stderr.write("ci sharding: rejected arguments\n")
                    return 2
            if not shard_id or not receipt_path_str:
                sys.stderr.write("ci sharding: rejected arguments\n")
                return 2
            return run_shard(repo_root, shard_id, Path(receipt_path_str))

        elif command == "verify-aggregate":
            receipts_dir_str = ""
            expected_head = ""
            producer_result = ""
            seen_options: set[str] = set()
            idx = 2
            while idx < len(argv):
                arg = argv[idx]
                if arg == "--receipts-dir" and idx + 1 < len(argv):
                    if "receipts-dir" in seen_options:
                        raise ShardingError("duplicate aggregate option")
                    seen_options.add("receipts-dir")
                    receipts_dir_str = argv[idx + 1]
                    idx += 2
                elif arg.startswith("--receipts-dir="):
                    if "receipts-dir" in seen_options:
                        raise ShardingError("duplicate aggregate option")
                    seen_options.add("receipts-dir")
                    receipts_dir_str = arg.partition("=")[2]
                    idx += 1
                elif arg == "--expected-head" and idx + 1 < len(argv):
                    if "expected-head" in seen_options:
                        raise ShardingError("duplicate aggregate option")
                    seen_options.add("expected-head")
                    expected_head = argv[idx + 1]
                    idx += 2
                elif arg.startswith("--expected-head="):
                    if "expected-head" in seen_options:
                        raise ShardingError("duplicate aggregate option")
                    seen_options.add("expected-head")
                    expected_head = arg.partition("=")[2]
                    idx += 1
                elif arg == "--producer-result" and idx + 1 < len(argv):
                    if "producer-result" in seen_options:
                        raise ShardingError("duplicate aggregate option")
                    seen_options.add("producer-result")
                    producer_result = argv[idx + 1]
                    idx += 2
                elif arg.startswith("--producer-result="):
                    if "producer-result" in seen_options:
                        raise ShardingError("duplicate aggregate option")
                    seen_options.add("producer-result")
                    producer_result = arg.partition("=")[2]
                    idx += 1
                else:
                    sys.stderr.write("ci sharding: rejected arguments\n")
                    return 2
            if not receipts_dir_str or not expected_head or not producer_result:
                sys.stderr.write("ci sharding: rejected arguments\n")
                return 2
            verify_aggregate(Path(receipts_dir_str), expected_head, producer_result)
            return 0

        elif command == "validate-receipt":
            if len(argv) not in (3, 5):
                sys.stderr.write("ci sharding: rejected arguments\n")
                return 2
            receipt_path = Path(argv[2])
            expected_head = None
            if len(argv) == 5 and argv[3] == "--expected-head":
                expected_head = argv[4]
            data = _read_receipt(receipt_path)
            validate_receipt(data, expected_head, inventory_digest())
            print("ok: receipt is valid")
            return 0

        else:
            sys.stderr.write("ci sharding: rejected arguments\n")
            return 2

    except ShardingError as exc:
        sys.stderr.write(f"ci sharding error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
