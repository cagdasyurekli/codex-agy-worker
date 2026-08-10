#!/usr/bin/env python3
"""Offline paired and mutation-sensitive tests for Persona Evidence Registry v1."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "skills/agy-worker/runtime/scripts/persona_registry.py"
CLI = ROOT / "persona-evidence.sh"

passed = 0
failed = 0


def check(label: str, condition) -> None:
    global passed, failed
    try:
        result = bool(condition() if callable(condition) else condition)
    except Exception:
        result = False
    if result:
        passed += 1
        print(f"ok - {label}")
    else:
        failed += 1
        print(f"not ok - {label}")


def load_module():
    spec = importlib.util.spec_from_file_location("persona_registry_under_test", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def run_cli(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(root / "persona-evidence.sh"), *args],
        cwd=root,
        env={"PATH": "/usr/bin:/bin", "HOME": "/var/empty", "TMPDIR": "/tmp", "LANG": "C", "LC_ALL": "C"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )


def rejected(function) -> bool:
    try:
        function()
    except MODULE.RegistryError:
        return True
    return False


records = MODULE.validate_registry()
report = MODULE.markdown(records)
check("canonical registry validates exactly three records", len(records) == 3)
check("all shipped records remain conservatively offline-only", [x["status"] for x in records] == ["offline-only"] * 3)
check("registry order is the hardcoded runtime allowlist", [x["name"] for x in records] == list(MODULE.PERSONAS))
check("bulk writer modes match dispatcher semantics", records[0]["modes"] == ["plan", "accept-edits"])
check("read-only persona modes match dispatcher semantics", all(x["modes"] == ["plan"] for x in records[1:]))
check("offline evidence explicitly says persona was not executed", all(x["offline_evidence"]["persona_executed"] is False for x in records))
check("offline evidence explicitly excludes live execution", all(x["offline_evidence"]["live_execution"] is False for x in records))
check("offline evidence delegates verdict authority to qa-gate", all(x["offline_evidence"]["gate_authority"] == "qa-gate" for x in records))
check("limitations deny trust and acceptance authority", all(x["limitations"] == {"acceptance_authority": False, "general_reliability": False, "persona_enforcement": False, "prompt_guidance_only": True} for x in records))
check("report is deterministic", report == MODULE.markdown(MODULE.validate_registry()))
check("report contains no trusted label", "trusted" not in report.lower())
check("report contains no acceptance upgrade", "accepted candidate" not in report.lower())
check("report states persona was not executed", report.count("persona not executed") == 3)

valid = run_cli("validate")
check("validate CLI exits zero", valid.returncode == 0)
check("validate CLI has exact bounded stdout", valid.stdout == b"persona evidence registry valid: 3 records\n")
check("validate CLI has empty stderr", valid.stderr == b"")
rendered = run_cli("report")
check("report CLI exits zero", rendered.returncode == 0)
check("report CLI bytes equal pure renderer", rendered.stdout == report.encode("utf-8"))
check("report CLI has empty stderr", rendered.stderr == b"")
usage = run_cli("report", "target-registry.json")
check("CLI rejects target-repository registry injection", usage.returncode == 64 and usage.stdout == b"" and b"usage:" in usage.stderr)
check("CLI surface exposes only validate and report", run_cli("promote").returncode == 64)

manifest_raw = (ROOT / "compat/personas/manifest.json").read_bytes()
manifest = MODULE.strict_object(manifest_raw, "manifest")
schema = MODULE.load_schema(ROOT / "skills/agy-worker/runtime/compat/persona-evidence.schema.json")
manifest_schema = MODULE.load_schema(ROOT / "skills/agy-worker/runtime/compat/persona-registry.schema.json")


def schema_reject(value, target_schema) -> bool:
    try:
        MODULE.schema_validate(value, target_schema)
    except MODULE.RegistryError:
        return True
    return False


for field, bad_value in (
    ("schema_version", 2),
    ("kind", "persona-registry"),
    ("states", ["offline-only"]),
    ("records", manifest["records"][:2]),
):
    candidate = copy.deepcopy(manifest)
    candidate[field] = bad_value
    check(f"manifest schema rejects invalid {field}", lambda c=candidate: schema_reject(c, manifest_schema))

base = copy.deepcopy(records[0])
for field, bad_value in (
    ("schema_version", 2),
    ("kind", "persona-evidence"),
    ("name", "../bulk-test-writer"),
    ("status", "trusted"),
):
    candidate = copy.deepcopy(base)
    candidate[field] = bad_value
    check(f"record schema rejects invalid {field}", lambda c=candidate: schema_reject(c, schema))
candidate = copy.deepcopy(base); candidate["modes"] = ["accept-edits"]
check("runtime cross-field authority rejects wrong persona modes", rejected(lambda: MODULE.validate_record(candidate, MODULE.canonical_bytes(candidate))))

for field in ("benchmark_manifest_sha256", "selection_sha256", "receipt_schema_sha256", "qa_gate_sha256"):
    candidate = copy.deepcopy(base)
    candidate["offline_evidence"][field] = "0" * 64
    raw = MODULE.canonical_bytes(candidate)
    check(f"runtime rejects drifted {field}", lambda c=candidate, r=raw: rejected(lambda: MODULE.validate_record(c, r)))

candidate = copy.deepcopy(base)
candidate["persona"]["path"] = "agents/../bulk-test-writer.md"
check("runtime rejects persona path aliases", rejected(lambda: MODULE.validate_record(candidate, MODULE.canonical_bytes(candidate))))
candidate = copy.deepcopy(base)
candidate["modes"] = ["plan"]
check("runtime rejects mode restriction drift", rejected(lambda: MODULE.validate_record(candidate, MODULE.canonical_bytes(candidate))))
candidate = copy.deepcopy(base)
candidate["status"] = "accepted-real-candidate"
check("runtime rejects self-authored acceptance without evidence", rejected(lambda: MODULE.validate_record(candidate, MODULE.canonical_bytes(candidate))))
candidate = copy.deepcopy(base)
candidate["status"] = "real-escalation-observed"
check("runtime rejects escalation status without evidence", rejected(lambda: MODULE.validate_record(candidate, MODULE.canonical_bytes(candidate))))


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["/usr/bin/git", "-C", str(repo), *args], env={"PATH": "/usr/bin:/bin", "HOME": "/var/empty", "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return completed.stdout.decode("ascii", "strict").strip()


def write_json(path: Path, value: dict) -> bytes:
    raw = MODULE.canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw); path.chmod(0o644)
    return raw


def receipt(exit_code: int, selection: dict, base_commit: str) -> dict:
    outcomes = {0: ("gate-passed", "gate-passed"), 15: ("worker-escalation", "routed")}
    outcome, verdict = outcomes[exit_code]
    return {"schema_version": 1, "kind": "agy-worker-evidence-receipt", "gate_authority": "qa-gate", "resolved_base": base_commit, "envelope_sha256": "1" * 64, "path_policy_sha256": "2" * 64, "verifiers": [{"label": "verify-001", "command_sha256": "3" * 64}], "initial_candidate_state_sha256": "4" * 64, "final_candidate_state_sha256": "5" * 64, "gate_exit": exit_code, "gate_outcome": outcome, "verdict": verdict, "caller_selection": selection, "recommendations_participated_in_acceptance": False, "integrity": {"signed": False, "tamper_evident": False, "statement": "Unsigned local record; schema-valid content can be rewritten and is not self-authenticating."}}


def build_history(status: str, fault: str | None = None, persona_name: str = "bulk-test-writer") -> tuple[Path, dict, bytes, tempfile.TemporaryDirectory]:
    holder = tempfile.TemporaryDirectory(prefix="persona-evidence-history.")
    repo = Path(holder.name) / "worker"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.invalid")
    def install_benchmark_source() -> None:
        portable = json.loads((ROOT / "skills/agy-worker/runtime/benchmarks/v1/portable-source.json").read_text())
        for item in portable["files"]:
            source = ROOT / "skills/agy-worker/runtime" / item["path"]
            target = repo / "skills/agy-worker/runtime" / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target); target.chmod(0o755 if item["mode"] == "100755" else 0o644)
        target_tree = repo / "skills/agy-worker/runtime/benchmarks"
        shutil.copytree(ROOT / "skills/agy-worker/runtime/benchmarks", target_tree, dirs_exist_ok=True)

    for source in ("agy-worker.sh", "qa-gate.sh", "verify-job.sh"):
        target = repo / "skills/agy-worker/runtime" / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "skills/agy-worker/runtime" / source, target); target.chmod(0o755)
    runner_target = repo / "scripts/version_attestation_runner.py"
    runner_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "scripts/version_attestation_runner.py", runner_target); runner_target.chmod(0o755)
    persona_target = repo / "skills/agy-worker/runtime/agents" / f"{persona_name}.md"
    persona_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "skills/agy-worker/runtime/agents" / f"{persona_name}.md", persona_target); persona_target.chmod(0o644)
    install_benchmark_source()
    if fault == "benchmark-source-missing":
        (repo / "skills/agy-worker/runtime/scripts/benchmark.py").unlink()
    if fault == "source-persona-drift":
        persona_target.write_bytes(persona_target.read_bytes() + b"\n")
    if fault in ("source-runner-drift", "version-runner-recomputed"):
        runner_target.write_bytes(runner_target.read_bytes() + b"# drift\n")
    git(repo, "add", "."); git(repo, "commit", "-q", "-m", "public tool base")
    base_commit = git(repo, "rev-parse", "HEAD")

    def alternate_source(ref: str, orphan: bool) -> str:
        if orphan:
            git(repo, "checkout", "-q", "--orphan", ref)
            for child in repo.iterdir():
                if child.name == ".git":
                    continue
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            for source in ("agy-worker.sh", "qa-gate.sh", "verify-job.sh"):
                target = repo / "skills/agy-worker/runtime" / source
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / "skills/agy-worker/runtime" / source, target); target.chmod(0o755)
            target = repo / "scripts/version_attestation_runner.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "scripts/version_attestation_runner.py", target); target.chmod(0o755)
            target = repo / "skills/agy-worker/runtime/agents" / f"{persona_name}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "skills/agy-worker/runtime/agents" / f"{persona_name}.md", target); target.chmod(0o644)
            install_benchmark_source()
        else:
            git(repo, "checkout", "-q", "-b", ref)
        marker = repo / f".{ref}"
        marker.write_text("source\n"); marker.chmod(0o644)
        git(repo, "add", "."); git(repo, "commit", "-q", "-m", ref)
        value = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "main")
        return value

    unrelated_commit = alternate_source("unrelated-source", True) if fault == "source-unrelated" else None
    future_commit = alternate_source("future-source", False) if fault == "source-future" else None
    source_commit = unrelated_commit or future_commit or base_commit
    target_repo = Path(holder.name) / "target"
    target_repo.mkdir()
    git(target_repo, "init", "-q", "-b", "main")
    git(target_repo, "config", "user.name", "test")
    git(target_repo, "config", "user.email", "test@example.invalid")
    target_file = target_repo / "proof.txt"
    target_file.write_text("before\n"); target_file.chmod(0o644)
    git(target_repo, "add", "."); git(target_repo, "commit", "-q", "-m", "target base")
    target_base = source_commit if fault == "target-source-conflation" else git(target_repo, "rev-parse", "HEAD")

    record_base = next(item for item in records if item["name"] == persona_name)
    prefix = f"compat/personas/evidence/{persona_name}/"
    evidence = repo / prefix
    evidence.mkdir(parents=True)
    selection = {"schema_version": 1, "kind": "agy-worker-selection", "selection_mode": "tier", "selected_tier": "bulk", "selected_tier_source": "cli", "resolved_agy_model": "gemini-3.6-flash-medium"}
    selection_raw = write_json(evidence / "selection.json", selection)
    benchmark_receipt = receipt(0, selection, base_commit)
    benchmark_receipt["resolved_base"] = target_base
    benchmark_receipt_raw = write_json(evidence / "benchmark-receipt.json", benchmark_receipt)
    real_exit = 15 if status == "real-escalation-observed" else 0
    real_receipt = receipt(real_exit, selection, base_commit)
    real_receipt["resolved_base"] = target_base
    if fault == "receipt-selection":
        real_receipt["caller_selection"] = {**selection, "selected_tier": "cheap", "resolved_agy_model": "gemini-3.6-flash-low"}
    real_receipt_raw = write_json(evidence / "real-receipt.json", real_receipt)
    runtime = ROOT / "skills/agy-worker/runtime"
    manifest_source_raw = (runtime / "benchmarks/v1/manifest.json").read_bytes()
    manifest_source = json.loads(manifest_source_raw)
    portable_source_raw = (runtime / "benchmarks/v1/portable-source.json").read_bytes()
    variant_source_raw = (runtime / "benchmarks/v1/variants/bulk.json").read_bytes()
    variant_source = json.loads(variant_source_raw)
    source_selection = variant_source["selection"]
    source_selection_raw = MODULE.canonical_bytes(source_selection)
    source_task = manifest_source["tasks"][0]
    fixture_payload = b"".join((runtime / "benchmarks/v1" / source_task[key]).read_bytes() for key in ("initial_source", "candidate_source", "envelope_source"))
    benchmark_persona = None if fault != "benchmark-persona" else {"name": "bulk-test-writer", "source_sha256": record_base["persona"]["sha256"]}
    authority = {"source_kind": "git-clean-commit", "source_revision": source_commit, "source_manifest_sha256": hashlib.sha256(portable_source_raw).hexdigest(), "benchmark_runner_sha256": hashlib.sha256((runtime / "scripts/benchmark.py").read_bytes()).hexdigest(), "qa_gate_sha256": hashlib.sha256((runtime / "qa-gate.sh").read_bytes()).hexdigest(), "verify_job_sha256": hashlib.sha256((runtime / "verify-job.sh").read_bytes()).hexdigest(), "plan_schema_sha256": hashlib.sha256((runtime / "schemas/benchmark-plan.schema.json").read_bytes()).hexdigest(), "result_schema_sha256": hashlib.sha256((runtime / "schemas/benchmark-result.schema.json").read_bytes()).hexdigest(), "receipt_schema_sha256": hashlib.sha256((runtime / "schemas/evidence-receipt.schema.json").read_bytes()).hexdigest(), "manifest_sha256": hashlib.sha256(manifest_source_raw).hexdigest(), "fixture_set_sha256": hashlib.sha256(fixture_payload).hexdigest()}
    if fault == "benchmark-authority": authority["benchmark_runner_sha256"] = "0" * 64
    if fault == "benchmark-source-manifest": authority["source_manifest_sha256"] = "0" * 64
    if fault == "benchmark-fixture-set": authority["fixture_set_sha256"] = "0" * 64
    task_record = {key: source_task[key] for key in ("id", "initial_sha256", "candidate_sha256", "envelope_sha256", "only", "expect_edits", "verifiers")}
    variant_record = {"name": "bulk", "source_sha256": hashlib.sha256(variant_source_raw).hexdigest(), "selection": source_selection, "selection_sha256": hashlib.sha256(source_selection_raw).hexdigest(), "persona": benchmark_persona}
    if fault == "benchmark-variant": variant_record["source_sha256"] = "0" * 64
    if fault == "benchmark-selection":
        variant_record["selection"] = {**source_selection, "selected_tier": "cheap", "resolved_agy_model": "gemini-3.6-flash-low"}
        variant_record["selection_sha256"] = hashlib.sha256(MODULE.canonical_bytes(variant_record["selection"])).hexdigest()
    plan = {"schema_version": 1, "kind": "agy-worker-benchmark-plan", "manifest_sha256": hashlib.sha256(manifest_source_raw).hexdigest(), "tool_authority": authority, "policy": {"mode": "offline-synthetic", "attempts_per_variant_task": 1, "gate_authority": "qa-gate", "live_execution": False, "network": False, "provider": False, "ranking": False, "routing": False, "recommendation": False, "driver_duration": "diagnostic-only-not-recorded"}, "tasks": [task_record], "variants": [variant_record], "expected_runs": 1, "integrity": {"signed": False, "tamper_evident": False}}
    if fault == "benchmark-manifest": plan["manifest_sha256"] = "0" * 64
    plan_raw = write_json(evidence / "benchmark-plan.json", plan)
    run = {"sequence": 1, "variant": "bulk", "task": "exact-edit", "attempt": 1, "receipt_name": "receipt-001.json", "receipt_sha256": hashlib.sha256(benchmark_receipt_raw).hexdigest(), "gate_exit": 0, "gate_outcome": "gate-passed", "verdict": "gate-passed", "resolved_base": base_commit, "initial_candidate_state_sha256": "4" * 64, "final_candidate_state_sha256": "5" * 64, "selection_sha256": hashlib.sha256(selection_raw).hexdigest()}
    result = {"schema_version": 1, "kind": "agy-worker-benchmark-result", "plan_sha256": hashlib.sha256(plan_raw).hexdigest(), "manifest_sha256": "6" * 64, "policy": {"mode": "offline-synthetic", "attempts_per_variant_task": 1, "gate_authority": "qa-gate", "live_execution": False, "network": False, "provider": False, "ranking": False, "routing": False, "recommendation": False}, "complete": True, "runs": [run], "integrity": {"signed": False, "tamper_evident": False}}
    run["resolved_base"] = target_base
    result["manifest_sha256"] = hashlib.sha256(manifest_source_raw).hexdigest()
    if fault == "benchmark-result": result["complete"] = False
    result_raw = write_json(evidence / "benchmark-result.json", result)
    diff_raw = b"diff --git a/proof.txt b/proof.txt\n"; (evidence / "candidate.diff").write_bytes(diff_raw); (evidence / "candidate.diff").chmod(0o644)
    dispatch = {"schema_version": 1, "kind": "agy-worker-persona-dispatch", "driver_owned": True, "persona": persona_name, "persona_source_sha256": record_base["persona"]["sha256"], "mode": "plan" if status == "real-escalation-observed" or fault == "accepted-plan" or persona_name != "bulk-test-writer" else "accept-edits", "dispatcher_sha256": record_base["offline_evidence"]["dispatcher_sha256"], "selection_sha256": hashlib.sha256(selection_raw).hexdigest(), "resolved_base": base_commit}
    dispatch["resolved_base"] = target_base
    if fault == "dispatch-persona": dispatch["persona"] = "diff-reviewer"
    dispatch_raw = write_json(evidence / "dispatch-profile.json", dispatch)
    runner_sha = hashlib.sha256((repo / "scripts/version_attestation_runner.py").read_bytes()).hexdigest()
    version_facts = {**MODULE.ACCEPTED_VERSION_FACTS, "runner_sha256": runner_sha}
    if fault == "version-binding": version_facts["binding_sha256"] = "0" * 64
    if fault == "version-snapshot": version_facts["snapshot_sha256"] = "0" * 64
    version = {"schema_version": 1, "kind": "agy-worker-public-version-attestation", "claim": "maintainer-reviewed-private-version-reference", "status": "accepted", "version": version_facts, "limitations": MODULE.VERSION_LIMITATIONS}
    version_raw = write_json(evidence / "version-attestation.json", version)
    verifier = {"schema_version": 1, "kind": "agy-worker-public-verifier", "driver_owned": True, "label": "verify-001", "command_sha256": "3" * 64}
    if fault == "verifier-command": verifier["command_sha256"] = "4" * 64
    verifier_raw = write_json(evidence / "verifier-001.json", verifier)
    def source_binding(path: str, sha: str, commit: str = source_commit) -> dict:
        return {"path": path, "sha256": sha, "commit": commit, "mode": "100755"}
    tool = {"schema_version": 1, "kind": "agy-worker-persona-tool-attestation", "agy_version": "1.1.11", "agy_version_binding_sha256": version_facts["binding_sha256"], "version_attestation_sha256": hashlib.sha256(version_raw).hexdigest(), "selection_sha256": hashlib.sha256(selection_raw).hexdigest(), "verifier_sha256": hashlib.sha256(verifier_raw).hexdigest(), "dispatcher": source_binding("skills/agy-worker/runtime/agy-worker.sh", record_base["offline_evidence"]["dispatcher_sha256"]), "qa_gate": source_binding("skills/agy-worker/runtime/qa-gate.sh", record_base["offline_evidence"]["qa_gate_sha256"]), "verify_job": source_binding("skills/agy-worker/runtime/verify-job.sh", record_base["offline_evidence"]["verify_job_sha256"]), "version_runner": source_binding("scripts/version_attestation_runner.py", runner_sha)}
    if fault == "source-mixed": tool["qa_gate"]["commit"] = base_commit if source_commit != base_commit else "0" * 40
    if fault == "version-artifact": tool["version_attestation_sha256"] = "0" * 64
    if fault == "tool-version": tool["agy_version"] = "1.1.10"
    tool_raw = write_json(evidence / "tool-attestation.json", tool)
    real_outcome, real_verdict = ("worker-escalation", "routed") if real_exit == 15 else ("gate-passed", "gate-passed")
    run_evidence = {"schema_version": 1, "kind": "agy-worker-persona-real-run", "persona": "bulk-test-writer", "target_state": status, "dispatch_profile_sha256": hashlib.sha256(dispatch_raw).hexdigest(), "tool_attestation_sha256": hashlib.sha256(tool_raw).hexdigest(), "selection_sha256": hashlib.sha256(selection_raw).hexdigest(), "real_receipt_sha256": hashlib.sha256(real_receipt_raw).hexdigest(), "benchmark_plan_sha256": hashlib.sha256(plan_raw).hexdigest(), "benchmark_result_sha256": hashlib.sha256(result_raw).hexdigest(), "benchmark_receipt_sha256": hashlib.sha256(benchmark_receipt_raw).hexdigest(), "candidate_diff_sha256": hashlib.sha256(diff_raw).hexdigest(), "resolved_base": base_commit, "initial_candidate_state_sha256": "4" * 64, "final_candidate_state_sha256": "5" * 64, "gate_exit": real_exit, "gate_outcome": real_outcome, "verdict": real_verdict}
    run_evidence["persona"] = persona_name
    run_evidence["resolved_base"] = target_base
    if fault == "run-receipt": run_evidence["final_candidate_state_sha256"] = "6" * 64
    run_raw = write_json(evidence / "run-evidence.json", run_evidence)
    artifact_names = MODULE.EVIDENCE_FILES
    artifacts = {name.replace("-", "_").replace(".json", "").replace(".diff", ""): {"path": prefix + name, "sha256": hashlib.sha256((evidence / name).read_bytes()).hexdigest()} for name in artifact_names}
    manifest_value = {"schema_version": 1, "kind": "agy-worker-persona-run-manifest", "persona": "bulk-test-writer", "target_state": status, "artifacts": artifacts}
    manifest_value["persona"] = persona_name
    manifest_raw = write_json(evidence / "evidence-manifest.json", manifest_value)
    if fault in ("extra", "extra-evidence-deleted"): write_json(evidence / "unreviewed.json", {"unexpected": True})
    if fault == "wrong-mode": (evidence / "candidate.diff").chmod(0o755)
    git(repo, "add", "."); git(repo, "commit", "-q", "-m", "immutable public persona evidence")
    evidence_commit = git(repo, "rev-parse", "HEAD")
    if fault == "extra-evidence-deleted":
        (evidence / "unreviewed.json").unlink()
    approval = {"schema_version": 1, "kind": "agy-worker-persona-transition-approval", "persona": "bulk-test-writer", "from_status": "offline-only", "to_status": status, "decision": "approved", "reviewer_role": "maintainer", "evidence_commit": evidence_commit, "evidence_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(), "run_evidence_sha256": hashlib.sha256(run_raw).hexdigest(), "real_receipt_sha256": hashlib.sha256(real_receipt_raw).hexdigest(), "candidate_state_sha256": "5" * 64, "candidate_diff_sha256": hashlib.sha256(diff_raw).hexdigest()}
    approval["persona"] = persona_name
    if fault == "approval-decision": approval["decision"] = "rejected"
    if fault == "approval-candidate": approval["candidate_state_sha256"] = "6" * 64
    approval_raw = write_json(evidence / "transition-approval.json", approval)
    review_raw = None
    if status == "accepted-real-candidate":
        review = {"schema_version": 1, "kind": "agy-worker-persona-human-review", "persona": "bulk-test-writer", "target_state": status, "decision": "accepted", "reviewer_role": "human-maintainer", "evidence_commit": evidence_commit, "run_evidence_sha256": hashlib.sha256(run_raw).hexdigest(), "real_receipt_sha256": hashlib.sha256(real_receipt_raw).hexdigest(), "resolved_base": base_commit, "candidate_state_sha256": "5" * 64, "candidate_diff_sha256": hashlib.sha256(diff_raw).hexdigest()}
        review["persona"] = persona_name
        review["resolved_base"] = target_base
        if fault == "review-decision": review["decision"] = "rejected"
        if fault == "review-candidate": review["candidate_state_sha256"] = "6" * 64
        review_raw = write_json(evidence / "human-review.json", review)
    if fault == "extra-approval-deleted": write_json(evidence / "approval-private.json", {"unexpected": True})
    git(repo, "add", "."); git(repo, "commit", "-q", "-m", "separate maintainer approval and review")
    approval_commit = git(repo, "rev-parse", "HEAD")
    if fault == "extra-approval-deleted":
        (evidence / "approval-private.json").unlink()
    record = copy.deepcopy(record_base); record["status"] = status
    record["real_evidence"] = {"path": prefix + "evidence-manifest.json", "sha256": hashlib.sha256(manifest_raw).hexdigest(), "commit": evidence_commit}
    record["transition_approval"] = {"path": prefix + "transition-approval.json", "sha256": hashlib.sha256(approval_raw).hexdigest(), "commit": approval_commit}
    record["human_review"] = None if review_raw is None else {"path": prefix + "human-review.json", "sha256": hashlib.sha256(review_raw).hexdigest(), "commit": approval_commit}
    record_raw = write_json(repo / "compat/personas/bulk-test-writer.json", record)
    git(repo, "add", "."); git(repo, "commit", "-q", "-m", "promote persona evidence state")
    return repo, record, record_raw, holder


def validate_history(repo: Path, record: dict, raw: bytes) -> bool:
    previous_checkout, previous_mode = MODULE.CHECKOUT, MODULE.CHECKOUT_MODE
    MODULE.CHECKOUT, MODULE.CHECKOUT_MODE = repo, True
    try:
        return bool(MODULE.validate_record(record, raw))
    finally:
        MODULE.CHECKOUT, MODULE.CHECKOUT_MODE = previous_checkout, previous_mode


for status in ("real-escalation-observed", "accepted-real-candidate"):
    repo, candidate, raw, holder = build_history(status)
    try:
        check(f"strict immutable history accepts {status}", lambda r=repo, c=candidate, b=raw: validate_history(r, c, b))
        transition_report = MODULE.markdown([candidate])
        expected_label = "public routed Receipt + transition approval" if status == "real-escalation-observed" else "public gate-passed Receipt + human review + transition approval"
        check(f"report renders exact {status} evidence label", expected_label in transition_report)
        forged = copy.deepcopy(candidate); forged["transition_approval"]["sha256"] = "0" * 64
        check(f"immutable approval digest rejects {status} forgery", lambda r=repo, c=forged: rejected(lambda: validate_history(r, c, MODULE.canonical_bytes(c))))
    finally:
        holder.cleanup()

for fault, status in (
    ("benchmark-persona", "real-escalation-observed"),
    ("benchmark-authority", "real-escalation-observed"),
    ("benchmark-source-manifest", "real-escalation-observed"),
    ("benchmark-fixture-set", "real-escalation-observed"),
    ("benchmark-variant", "real-escalation-observed"),
    ("benchmark-selection", "real-escalation-observed"),
    ("benchmark-manifest", "real-escalation-observed"),
    ("benchmark-result", "real-escalation-observed"),
    ("benchmark-source-missing", "real-escalation-observed"),
    ("dispatch-persona", "real-escalation-observed"),
    ("tool-version", "real-escalation-observed"),
    ("receipt-selection", "real-escalation-observed"),
    ("verifier-command", "real-escalation-observed"),
    ("run-receipt", "accepted-real-candidate"),
    ("approval-decision", "real-escalation-observed"),
    ("approval-candidate", "real-escalation-observed"),
    ("review-decision", "accepted-real-candidate"),
    ("review-candidate", "accepted-real-candidate"),
    ("extra", "real-escalation-observed"),
    ("extra-evidence-deleted", "real-escalation-observed"),
    ("extra-approval-deleted", "real-escalation-observed"),
    ("wrong-mode", "real-escalation-observed"),
    ("source-unrelated", "real-escalation-observed"),
    ("source-future", "real-escalation-observed"),
    ("source-mixed", "real-escalation-observed"),
    ("source-persona-drift", "real-escalation-observed"),
    ("source-runner-drift", "real-escalation-observed"),
    ("version-binding", "real-escalation-observed"),
    ("version-snapshot", "real-escalation-observed"),
    ("version-artifact", "real-escalation-observed"),
    ("version-runner-recomputed", "real-escalation-observed"),
    ("target-source-conflation", "real-escalation-observed"),
    ("accepted-plan", "accepted-real-candidate"),
):
    repo, candidate, raw, holder = build_history(status, fault)
    try:
        check(f"immutable history rejects {fault} drift", lambda r=repo, c=candidate, b=raw: rejected(lambda: validate_history(r, c, b)))
    finally:
        holder.cleanup()

repo, candidate, raw, holder = build_history("accepted-real-candidate", persona_name="diff-reviewer")
try:
    check("plan-only persona cannot reach accepted-real-candidate", lambda: rejected(lambda: validate_history(repo, candidate, raw)))
finally:
    holder.cleanup()

for mutation, filename in (("rewrite", "real-receipt.json"), ("extra-after", "late.json"), ("symlink", "selection.json")):
    repo, candidate, raw, holder = build_history("accepted-real-candidate")
    try:
        target = repo / "compat/personas/evidence/bulk-test-writer" / filename
        if mutation == "rewrite":
            target.write_bytes(b"{}\n"); target.chmod(0o644)
        elif mutation == "extra-after":
            target.write_bytes(b"{}\n"); target.chmod(0o644)
        else:
            target.unlink(); target.symlink_to("/private/never-read.json")
        git(repo, "add", "."); git(repo, "commit", "-q", "-m", mutation)
        check(f"current immutable tree rejects {mutation}", lambda r=repo, c=candidate, b=raw: rejected(lambda: validate_history(r, c, b)))
    finally:
        holder.cleanup()

upper = copy.deepcopy(base); upper["status"] = "real-escalation-observed"; upper["real_evidence"] = {"path": "compat/personas/evidence/bulk-test-writer/evidence-manifest.json", "sha256": "0" * 64, "commit": "1" * 40}; upper["transition_approval"] = {"path": "compat/personas/evidence/bulk-test-writer/transition-approval.json", "sha256": "0" * 64, "commit": "2" * 40}
previous_mode = MODULE.CHECKOUT_MODE; MODULE.CHECKOUT_MODE = False
check("portable upper state fails closed before public path reads", lambda: rejected(lambda: MODULE.validate_record(upper, MODULE.canonical_bytes(upper))))
MODULE.CHECKOUT_MODE = previous_mode

source = RUNNER.read_text()


def mutant(marker: str, replacement: str):
    assert source.count(marker) == 1
    namespace = {"__file__": str(RUNNER), "__name__": "persona_registry_mutant"}
    exec(compile(source.replace(marker, replacement), "<persona-registry-mutant>", "exec"), namespace, namespace)
    return types.SimpleNamespace(**namespace)


def mutant_accepts(module, repo: Path, record: dict, raw: bytes) -> bool:
    module.CHECKOUT, module.CHECKOUT_MODE = repo, True
    return bool(module.validate_record(record, raw))


drifted = copy.deepcopy(base)
drifted["offline_evidence"]["qa_gate_sha256"] = "0" * 64
mutated = mutant('if value.get("offline_evidence") != expected_offline_evidence():', 'if False:')
check("offline evidence equality mutation is killed", lambda: bool(mutated.validate_record(drifted, mutated.canonical_bytes(drifted))))

offline_with_real = copy.deepcopy(upper); offline_with_real["status"] = "offline-only"
mutated = mutant("if any(item is not None for item in (manifest_binding, approval_binding, review_binding)):", "if False:")
check("offline upper-state exclusion mutation is killed", lambda: bool(mutated.validate_record(offline_with_real, mutated.canonical_bytes(offline_with_real))))

authority_markers = (
    'if variant_raw != canonical_bytes(variant_value) or set(variant_value) != {"schema_version", "kind", "name", "selection", "persona"} or variant_value.get("schema_version") != 1 or variant_value.get("kind") != "agy-worker-benchmark-variant-input" or variant_value.get("name") != "bulk" or variant_value.get("persona") is not None:',
    'if plan != expected_plan:',
    'if result != expected_result:',
    'if real_receipt.get("caller_selection") != selection:',
    'if tool.get("agy_version") != version_facts["observed"] or tool.get("agy_version_binding_sha256") != version_facts["binding_sha256"] or tool.get("version_attestation_sha256") != digest(version_raw):',
    'if run != expected_run:',
    'if approval != approval_expected:',
    'if review != review_expected:',
    'if store.paths_at(store.transition_commit) != approval_paths or store.paths_at("HEAD") != approval_paths:',
    'if store.paths_at(evidence_commit) != evidence_paths:',
    'if store.paths_at(approval_commit) != approval_paths:',
    'if actual_mode != mode:',
    'store.strict_ancestor(source_commit, evidence_commit, "source-to-evidence")',
    'store.strict_ancestor(evidence_commit, approval_commit, "evidence-to-approval")',
    'store.strict_ancestor(approval_commit, store.transition_commit, "approval-to-transition")',
    'if dispatcher_binding.get("commit") != source_commit:',
    'if item.get("commit") != source_commit or digest(store.read_at(source_commit, path, mode)) != item.get("sha256"):',
    'if digest(source_persona_raw) != record["persona"]["sha256"] or dispatch["persona_source_sha256"] != digest(source_persona_raw):',
    'version_facts = validate_version_attestation(version, runner_binding["sha256"])',
    'if runner_sha256 != ACCEPTED_VERSION_FACTS["runner_sha256"]:',
    'if real_receipt["resolved_base"] != benchmark_receipt["resolved_base"] or real_receipt["resolved_base"] == source_commit:',
    'if status == "accepted-real-candidate" and dispatch["mode"] != "accept-edits":',
)


def authority_contract(text: str) -> bool:
    return all(text.count(marker) == 1 for marker in authority_markers)


check("semantic and ancestry source authority is exact", authority_contract(source))
for index, marker in enumerate(authority_markers, 1):
    check(f"semantic authority source mutation {index} is killed", not authority_contract(source.replace(marker, "", 1)))

same_repo, same_record, same_raw, same_holder = build_history("real-escalation-observed")
try:
    MODULE.CHECKOUT, MODULE.CHECKOUT_MODE = same_repo, True
    store = MODULE.GitEvidenceStore("bulk-test-writer", same_raw)
    check("same-commit ancestry is rejected", lambda: rejected(lambda: store.strict_ancestor(store.transition_commit, store.transition_commit, "same")))
    weakened = mutant('if older == newer:\n            fail(f"{label} commits must be distinct")', "if False:\n            pass")
    weakened.GitEvidenceStore.__init__.__globals__["CHECKOUT"] = same_repo
    weakened.GitEvidenceStore.__init__.__globals__["CHECKOUT_MODE"] = True
    weak_store = weakened.GitEvidenceStore("bulk-test-writer", same_raw)
    check("same-commit ancestry weakening mutation is killed", lambda: weak_store.strict_ancestor(weak_store.transition_commit, weak_store.transition_commit, "same") is None)
finally:
    MODULE.CHECKOUT, MODULE.CHECKOUT_MODE = ROOT, True
    same_holder.cleanup()

dispatcher = (ROOT / "skills/agy-worker/runtime/agy-worker.sh").read_bytes()
check("dispatcher contract accepts canonical allowlist and restrictions", lambda: MODULE.validate_dispatcher_contract(dispatcher) is None)
check("dispatcher contract rejects added edit mode for read-only persona", rejected(lambda: MODULE.validate_dispatcher_contract(dispatcher.replace(b'"$persona" == "diff-reviewer"', b'"$persona" == "never"'))))
check("dispatcher contract rejects target-controlled persona path injection", rejected(lambda: MODULE.validate_dispatcher_contract(dispatcher.replace(b'persona_file="$SCRIPT_DIR/agents/$persona.md"', b'persona_file="$PWD/$persona.md"'))))

frontmatter = (ROOT / "skills/agy-worker/runtime/agents/diff-reviewer.md").read_bytes()
frontmatter_drift = frontmatter.replace(b"agyWorkerModes:\n    - plan\n", b"agyWorkerModes:\n    - plan\n    - accept-edits\n")
mutated = mutant("tuple(modes) != MODES[expected_name]", "False")
check("frontmatter mode authority mutation is killed", lambda: mutated.parse_frontmatter(frontmatter_drift, "diff-reviewer")[1] == ("plan", "accept-edits"))
check("frontmatter rejects missing tools", rejected(lambda: MODULE.parse_frontmatter(frontmatter.replace(b"tools:\n    - find_by_name\n    - grep_search\n    - view_file\n    - list_dir\n", b""), "diff-reviewer")))
check("frontmatter rejects false hidden policy", rejected(lambda: MODULE.parse_frontmatter(frontmatter.replace(b"hidden: true", b"hidden: false"), "diff-reviewer")))

with tempfile.TemporaryDirectory(prefix="persona-registry-mutation.") as temp_registry:
    registry_copy = Path(temp_registry)
    for item in (ROOT / "compat/personas").iterdir():
        shutil.copy2(item, registry_copy / item.name)
    (registry_copy / "target.json").write_text("{}\n")
    (registry_copy / "target.json").chmod(0o644)
    mutated = mutant("if actual_files != expected_files:", "if False:")
    mutated.REGISTRY = registry_copy
    check("no-extra registry mutation is killed", lambda: len(mutated.validate_manifest(copy.deepcopy(manifest), manifest_raw)) == 3)

with tempfile.TemporaryDirectory(prefix="persona-registry-test.") as temp:
    temp_root = Path(temp)
    skill = temp_root / "agy-worker"
    shutil.copytree(ROOT / "skills/agy-worker", skill)
    portable = subprocess.run(
        [str(skill / "runtime/persona-evidence.sh"), "report"],
        cwd=temp_root,
        env={"PATH": "/usr/bin:/bin", "HOME": "/var/empty", "TMPDIR": "/tmp", "LANG": "C", "LC_ALL": "C"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    check("folder-only skill validates and renders without checkout", portable.returncode == 0 and portable.stdout == report.encode())
    extra = skill / "runtime/compat/personas/target.json"
    extra.write_text("{}\n"); extra.chmod(0o644)
    rejected_dynamic = subprocess.run([str(skill / "runtime/persona-evidence.sh"), "validate"], cwd=temp_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
    check("folder-only registry rejects dynamic target records", rejected_dynamic.returncode == 2 and b"registry validation failed" in rejected_dynamic.stderr)
    extra.unlink()
    record = skill / "runtime/compat/personas/diff-reviewer.json"
    record.chmod(0o664)
    wrong_mode = subprocess.run([str(skill / "runtime/persona-evidence.sh"), "validate"], cwd=temp_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
    check("folder-only registry rejects writable record mode", wrong_mode.returncode == 2)
    record.chmod(0o644)
    moved = skill / "runtime/compat/personas/diff-reviewer.real"
    record.rename(moved); record.symlink_to(moved.name)
    symlinked = subprocess.run([str(skill / "runtime/persona-evidence.sh"), "validate"], cwd=temp_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
    check("folder-only registry rejects symlinked records", symlinked.returncode == 2)

check("runner Git subprocess is fixed and read-only", 'GIT = "/usr/bin/git"' in source and 'shell=True' not in source and 'stderr=subprocess.DEVNULL' in source)
check("runner imports no network module", all(token not in source for token in ("urllib", "socket", "requests", "http.client")))
check("runner exposes no registry path argument", "--registry" not in source and "AGY_WORKER_PERSONA" not in source)
check("runner exposes no promote or apply command", '"promote"' not in source and '"apply"' not in source)

print(f"PASSED: {passed} tests")
if failed:
    print(f"FAILED: {failed} tests")
    raise SystemExit(1)
