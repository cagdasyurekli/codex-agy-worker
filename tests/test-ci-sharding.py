#!/usr/bin/env python3
"""Focused positive and adversarial checks for CI sharding and aggregate verification."""

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
SCRIPT = ROOT / "scripts" / "ci_sharding.py"
SPEC = importlib.util.spec_from_file_location("ci_sharding_tested", SCRIPT)
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


# 1. Shard Partition Invariants
check("inventory has 42 ordered unique stage IDs", len(MODULE.STAGES) == 42 and len({x[0] for x in MODULE.STAGES}) == 42)
check("exactly four frozen shard IDs exist", set(MODULE.SHARDS) == {"dispatcher", "dispatcher-remediation", "other-a", "other-b"})

all_shard_stages: list[str] = []
for shard_id, stages in MODULE.SHARDS.items():
    all_shard_stages.extend(stages)

check("total stage count across four shards is 42", len(all_shard_stages) == 42)
check("all stages across shards are disjoint and unique", len(set(all_shard_stages)) == 42)
check("union of shard stages equals canonical inventory stages", set(all_shard_stages) == {x[0] for x in MODULE.STAGES})

canonical_order_index = {stage_id: idx for idx, (stage_id, _) in enumerate(MODULE.STAGES)}
for shard_id, stages in MODULE.SHARDS.items():
    indexes = [canonical_order_index[s] for s in stages]
    check(f"shard {shard_id} stages strictly preserve canonical inventory order", indexes == sorted(indexes))

check("inventory digest is lowercase SHA-256", MODULE.SHA256_RE.fullmatch(MODULE.inventory_digest()) is not None)

TIMING_SCRIPT = ROOT / "scripts" / "ci_timing.py"
TIMING_SPEC = importlib.util.spec_from_file_location("ci_timing_inventory", TIMING_SCRIPT)
assert TIMING_SPEC is not None and TIMING_SPEC.loader is not None
TIMING_MODULE = importlib.util.module_from_spec(TIMING_SPEC)
TIMING_SPEC.loader.exec_module(TIMING_MODULE)
check(
    "timing and sharding share the exact canonical inventory",
    MODULE.STAGES == TIMING_MODULE.STAGES
    and MODULE.inventory_digest() == TIMING_MODULE.inventory_digest(),
)
check(
    "sharding module import does not chmod its own source",
    "os.chmod(Path(__file__).resolve()" not in SCRIPT.read_text(encoding="utf-8"),
)


def _shell_gate_announcements() -> list[str]:
    gate_script = ROOT / "scripts" / "ci-offline.sh"
    lines = gate_script.read_bytes().splitlines()
    observed = []
    pattern = MODULE.re.compile(rb"^(?:if\s+)?announce '([^']+)'(?:;\s*then)?$")
    for line in lines:
        m = pattern.fullmatch(line.strip())
        if m:
            observed.append(m.group(1).decode("utf-8"))
    return observed


check(
    "shell script announcements match canonical 42 stages exactly",
    _shell_gate_announcements() == [ann for _, ann in MODULE.STAGES],
)


# 2. Receipt Factory and Validation
def valid_receipt(shard_id: str = "other-a", outcome: str = "passed", failed_index: int = 2) -> dict[str, Any]:
    expected = list(MODULE.SHARDS[shard_id])
    if outcome == "passed":
        observed = list(expected)
    else:
        observed = list(expected[:failed_index])
    return {
        "schema_version": 1,
        "kind": "agy-worker-ci-shard-receipt",
        "head_sha": "a" * 40,
        "inventory_sha256": MODULE.inventory_digest(),
        "shard_id": shard_id,
        "expected_stage_ids": expected,
        "observed_stage_ids": observed,
        "outcome": outcome,
        "integrity": {
            "signed": False,
            "tamper_evident": False,
            "statement": MODULE.INTEGRITY_STATEMENT,
        },
    }


def receipt_rejected(receipt: dict[str, Any], expected_head: str = "a" * 40) -> bool:
    try:
        MODULE.validate_receipt(receipt, expected_head, MODULE.inventory_digest())
    except MODULE.ShardingError:
        return True
    return False


for shard in ("dispatcher", "dispatcher-remediation", "other-a", "other-b"):
    rec = valid_receipt(shard)
    check(f"valid passed receipt for {shard} validates", not receipt_rejected(rec))
    failed_rec = valid_receipt(shard, "failed", 1)
    check(f"valid failed receipt for {shard} validates", not receipt_rejected(failed_rec))

passed_receipt = valid_receipt("other-a")
failed_receipt = valid_receipt("other-a", "failed", 3)

for label, mutate in (
    ("extra root field is rejected", lambda r: r.__setitem__("path", "/private/example")),
    ("missing root field is rejected", lambda r: r.pop("shard_id")),
    ("wrong schema_version is rejected", lambda r: r.__setitem__("schema_version", 2)),
    ("wrong kind is rejected", lambda r: r.__setitem__("kind", "wrong-kind")),
    ("malformed head_sha is rejected", lambda r: r.__setitem__("head_sha", "A" * 40)),
    ("short head_sha is rejected", lambda r: r.__setitem__("head_sha", "a" * 39)),
    ("mismatched head_sha is rejected", lambda r: r.__setitem__("head_sha", "b" * 40)),
    ("malformed inventory_sha256 is rejected", lambda r: r.__setitem__("inventory_sha256", "Z" * 64)),
    ("mismatched inventory_sha256 is rejected", lambda r: r.__setitem__("inventory_sha256", "b" * 64)),
    ("unknown shard_id is rejected", lambda r: r.__setitem__("shard_id", "unknown-shard")),
    ("expected_stage_ids mismatch is rejected", lambda r: r.__setitem__("expected_stage_ids", ["diff-hygiene"])),
    ("reordered expected_stage_ids is rejected", lambda r: r["expected_stage_ids"].reverse()),
    ("passed outcome with missing observed stage is rejected", lambda r: r["observed_stage_ids"].pop()),
    ("passed outcome with reordered observed stages is rejected", lambda r: r["observed_stage_ids"].reverse()),
    ("failed outcome with empty observed stages is rejected", lambda r: r.__setitem__("observed_stage_ids", [])),
    ("failed outcome with non-prefix observed stages is rejected", lambda r: r.__setitem__("observed_stage_ids", ["qa-gate"])),
    ("invalid outcome value is rejected", lambda r: r.__setitem__("outcome", "cancelled")),
    ("tampered integrity block is rejected", lambda r: r["integrity"].update(signed=True)),
    ("tampered integrity statement is rejected", lambda r: r["integrity"].update(statement="changed")),
):
    candidate = copy.deepcopy(failed_receipt if "failed outcome with non-prefix" in label or "failed outcome with empty" in label else passed_receipt)
    mutate(candidate)
    check(label, receipt_rejected(candidate))

complete_failed_receipt = copy.deepcopy(passed_receipt)
complete_failed_receipt["outcome"] = "failed"
check(
    "failed outcome may observe every stage when the final stage fails",
    not receipt_rejected(complete_failed_receipt),
)

for forbidden in ("command", "environment", "logs", "credential", "provider", "account", "timestamp", "hostname", "cost", "path"):
    check(f"receipt schema excludes {forbidden}", forbidden not in json.dumps(passed_receipt, sort_keys=True).lower())


# 3. Publication Security
def _publication_cases() -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        private = root / "private"
        private.mkdir(mode=0o700)
        os.chmod(private, 0o700)
        target = private / "receipt.json"
        MODULE.publish_receipt(target, passed_receipt)
        if stat.S_IMODE(target.stat().st_mode) != 0o600:
            return False
        # Overwrite attempt must fail
        try:
            MODULE.publish_receipt(target, passed_receipt)
        except MODULE.ShardingError:
            pass
        else:
            return False
        # Non-0700 parent must fail
        open_parent = root / "open"
        open_parent.mkdir(mode=0o755)
        try:
            MODULE.validate_publication_target(open_parent / "receipt.json")
        except MODULE.ShardingError:
            pass
        else:
            return False
        # Symlink target parent must fail
        symlink = root / "linked"
        symlink.symlink_to(private)
        try:
            MODULE.validate_publication_target(symlink / "receipt.json")
        except MODULE.ShardingError:
            return True
    return False


check("publication is 0600, no-overwrite, 0700-parent, and no-follow", _publication_cases)


# 4. Clean HEAD Git Binding
def _clean_head_cases() -> bool:
    inherited_ci_head = os.environ.pop("AGY_WORKER_CI_HEAD_SHA", None)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "CI Shard Test"], cwd=repo, check=True)
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
            except MODULE.ShardingError:
                pass
            else:
                return False
            tracked.write_text("clean\n", encoding="utf-8")
            (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            try:
                MODULE.resolve_clean_head(repo)
            except MODULE.ShardingError:
                return True
        return False
    finally:
        if inherited_ci_head is not None:
            os.environ["AGY_WORKER_CI_HEAD_SHA"] = inherited_ci_head


check("HEAD binding accepts clean Git and rejects dirty tracked/untracked bytes", _clean_head_cases)


# 5. Stream Observation and Shard Runner
NONCE = "e" * 64


def observed_shard(
    shard_id: str, return_code: int, count: int, spoof_stdout: bool = False
) -> tuple[int, dict[str, Any]]:
    lines: list[bytes] = []
    stages = MODULE.SHARDS[shard_id]
    if spoof_stdout:
        lines.append(f"==> {MODULE.STAGE_MAP[stages[0]]}\n".encode())
    for s in stages[:count]:
        ann = MODULE.STAGE_MAP[s]
        lines.append(f"==> {ann}\n".encode())
        lines.append(f"@@agy-worker-ci-shard:{NONCE}:{ann}\n".encode())
    raw = b"".join(lines)
    return MODULE.observe_stream(
        io.BytesIO(raw), lambda: return_code, "c" * 40, shard_id, NONCE, io.BytesIO()
    )


def _successful_shard_observation() -> bool:
    for shard_id, stages in MODULE.SHARDS.items():
        rc, rec = observed_shard(shard_id, 0, len(stages))
        if rc != 0 or rec["outcome"] != "passed" or rec["observed_stage_ids"] != list(stages):
            return False
    return True


check("observer records all stages for successful shard run", _successful_shard_observation)


def _failed_shard_observation() -> bool:
    rc, rec = observed_shard("other-a", 1, 3)
    return rc == 1 and rec["outcome"] == "failed" and rec["observed_stage_ids"] == list(MODULE.SHARDS["other-a"][:3])


check("observer records prefix up to failed stage on shard failure", _failed_shard_observation)
check(
    "observer preserves a failed receipt when the final stage fails",
    lambda: observed_shard("other-a", 1, len(MODULE.SHARDS["other-a"]))[1]["outcome"]
    == "failed",
)
check(
    "ordinary stdout cannot spoof shard control marker",
    lambda: observed_shard("dispatcher", 0, 1, spoof_stdout=True)[1]["outcome"] == "passed",
)


def _duplicate_control_rejected() -> bool:
    ann = MODULE.STAGE_MAP[MODULE.SHARDS["dispatcher"][0]]
    raw = (
        f"@@agy-worker-ci-shard:{NONCE}:{ann}\n"
        f"@@agy-worker-ci-shard:{NONCE}:{ann}\n"
    ).encode()
    try:
        MODULE.observe_stream(
            io.BytesIO(raw), lambda: 1, "c" * 40, "dispatcher", NONCE, io.BytesIO()
        )
    except MODULE.ShardingError:
        return True
    return False


check("duplicate shard control marker is rejected", _duplicate_control_rejected)


# 6. Aggregate Verification
def all_four_passed_receipts(head_sha: str = "a" * 40) -> list[dict[str, Any]]:
    res = []
    for shard in ("dispatcher", "dispatcher-remediation", "other-a", "other-b"):
        rec = valid_receipt(shard)
        rec["head_sha"] = head_sha
        res.append(rec)
    return res


valid_aggregate = all_four_passed_receipts()
check("valid four-shard aggregate passes validation", lambda: (MODULE.validate_aggregate_receipts(valid_aggregate, "a" * 40) or True))


def aggregate_rejected(receipts: list[dict[str, Any]], expected_head: str = "a" * 40) -> bool:
    try:
        MODULE.validate_aggregate_receipts(receipts, expected_head, MODULE.inventory_digest())
    except MODULE.ShardingError:
        return True
    return False


check("missing shard receipt (3 receipts) is rejected", aggregate_rejected(valid_aggregate[:3]))

duplicate_shard_set = [valid_aggregate[0], valid_aggregate[1], valid_aggregate[2], copy.deepcopy(valid_aggregate[2])]
check("duplicate shard receipt in aggregate is rejected", aggregate_rejected(duplicate_shard_set))

failed_shard_set = copy.deepcopy(valid_aggregate)
failed_shard_set[0] = valid_receipt("dispatcher", "failed", 1)
check("aggregate with a failed shard receipt is rejected", aggregate_rejected(failed_shard_set))

wrong_sha_set = copy.deepcopy(valid_aggregate)
wrong_sha_set[1]["head_sha"] = "b" * 40
check("aggregate with mismatched head SHA is rejected", aggregate_rejected(wrong_sha_set))

wrong_inv_set = copy.deepcopy(valid_aggregate)
wrong_inv_set[0]["inventory_sha256"] = "c" * 64
check("aggregate with mismatched inventory SHA is rejected", aggregate_rejected(wrong_inv_set))


def _write_receipt_tree(root: Path) -> list[Path]:
    files: list[Path] = []
    for rec in valid_aggregate:
        sub = root / f"shard-receipt-{rec['shard_id']}"
        sub.mkdir(mode=0o700)
        path = sub / f"{rec['shard_id']}.json"
        path.write_text(json.dumps(rec), encoding="utf-8")
        files.append(path)
    return files


def _valid_directory_aggregate() -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        receipts_dir = Path(temp_dir) / "receipts"
        receipts_dir.mkdir(mode=0o700)
        _write_receipt_tree(receipts_dir)
        MODULE.verify_aggregate(receipts_dir, "a" * 40, "success")
        return True


check("verify_aggregate accepts exactly four successful bound artifacts", _valid_directory_aggregate)


def _producer_results_fail_closed() -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        receipts_dir = Path(temp_dir) / "receipts"
        receipts_dir.mkdir(mode=0o700)
        _write_receipt_tree(receipts_dir)
        for result in ("failure", "cancelled", "skipped", ""):
            try:
                MODULE.verify_aggregate(receipts_dir, "a" * 40, result)
            except MODULE.ShardingError:
                continue
            return False
    return True


check("failed, cancelled, skipped, and missing producer results fail closed", _producer_results_fail_closed)


def _artifact_tree_rejected(mutate: Callable[[Path, list[Path]], None]) -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        receipts_dir = Path(temp_dir) / "receipts"
        receipts_dir.mkdir(mode=0o700)
        files = _write_receipt_tree(receipts_dir)
        mutate(receipts_dir, files)
        try:
            MODULE.verify_aggregate(receipts_dir, "a" * 40, "success")
        except MODULE.ShardingError:
            return True
    return False


check(
    "duplicate extra receipt artifact is rejected",
    lambda: _artifact_tree_rejected(
        lambda root, files: (root / "duplicate.json").write_bytes(files[0].read_bytes())
    ),
)
check(
    "unexpected non-JSON artifact is rejected",
    lambda: _artifact_tree_rejected(
        lambda root, files: (root / "unexpected.txt").write_text("x", encoding="utf-8")
    ),
)
check(
    "malformed receipt JSON is rejected",
    lambda: _artifact_tree_rejected(lambda root, files: files[0].write_text("{", encoding="utf-8")),
)
check(
    "duplicate receipt JSON key is rejected",
    lambda: _artifact_tree_rejected(
        lambda root, files: files[0].write_text(
            '{"schema_version":1,' + json.dumps(valid_aggregate[0])[1:], encoding="utf-8"
        )
    ),
)
check(
    "oversized receipt artifact is rejected",
    lambda: _artifact_tree_rejected(
        lambda root, files: files[0].write_bytes(b"x" * (MODULE.MAX_RECEIPT_BYTES + 1))
    ),
)


def _replace_with_symlink(root: Path, files: list[Path]) -> None:
    target = root / "target.json"
    target.write_bytes(files[0].read_bytes())
    files[0].unlink()
    files[0].symlink_to(target)


check("symlink receipt artifact is rejected", lambda: _artifact_tree_rejected(_replace_with_symlink))


# 7. Shell Wrapper Argument Rejection
invalid_cli = subprocess.run(
    ["./scripts/ci-offline.sh", "--invalid"], cwd=ROOT,
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("ci-offline.sh rejects --invalid", invalid_cli.returncode == 2 and b"rejected arguments" in invalid_cli.stderr)

invalid_shard = subprocess.run(
    ["./scripts/ci-offline.sh", "--shard", "invalid-shard"], cwd=ROOT,
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("ci-offline.sh rejects unknown shard ID", invalid_shard.returncode == 2 and b"rejected arguments" in invalid_shard.stderr)

missing_shard_arg = subprocess.run(
    ["./scripts/ci-offline.sh", "--shard"], cwd=ROOT,
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("ci-offline.sh rejects missing shard argument", missing_shard_arg.returncode == 2 and b"rejected arguments" in missing_shard_arg.stderr)

duplicate_shell_shard = subprocess.run(
    ["./scripts/ci-offline.sh", "--shard", "dispatcher", "--shard", "other-a"],
    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("ci-offline.sh rejects duplicate shard selection", duplicate_shell_shard.returncode == 2)

duplicate_run_shard = subprocess.run(
    [
        "/usr/bin/python3", "-I", "-S", "-B", "scripts/ci_sharding.py", "run-shard",
        "--shard", "dispatcher", "--shard", "other-a", "--receipt", "/tmp/unused.json",
    ],
    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("run-shard CLI rejects duplicate shard selection", duplicate_run_shard.returncode != 0)

missing_producer = subprocess.run(
    [
        "/usr/bin/python3", "-I", "-S", "-B", "scripts/ci_sharding.py",
        "verify-aggregate", "--receipts-dir", "/tmp/none", "--expected-head", "a" * 40,
    ],
    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("aggregate CLI requires producer result", missing_producer.returncode == 2)

duplicate_producer = subprocess.run(
    [
        "/usr/bin/python3", "-I", "-S", "-B", "scripts/ci_sharding.py",
        "verify-aggregate", "--receipts-dir", "/tmp/none", "--expected-head", "a" * 40,
        "--producer-result", "success", "--producer-result", "success",
    ],
    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
check("aggregate CLI rejects duplicate producer result", duplicate_producer.returncode != 0)

print()
print(f"PASSED: {passed} tests")
if failed:
    print(f"FAILED: {failed} tests")
    raise SystemExit(1)
