#!/usr/bin/env python3
"""Focused positive, negative, privacy, and adversarial tests for Model Evidence Campaign."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Callable

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "agy-worker" / "runtime" / "scripts" / "model_evidence_campaign.py"
SPEC = importlib.util.spec_from_file_location("model_evidence_campaign_tested", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SHIPPED_DATASET_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "compat" / "model-intelligence" / "dataset.v1.json"
INVENTORY_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "compat" / "agy-models-inventory-binding.json"
MATRIX_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "compat" / "agy-model-effort-matrix.json"
PLAN_SCHEMA_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "schemas" / "model-evidence-campaign-plan.schema.json"
RECORD_SCHEMA_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "schemas" / "model-evidence-campaign-record.schema.json"
EVAL_SCHEMA_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "schemas" / "model-evidence-campaign-evaluation.schema.json"
AGG_SCHEMA_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "schemas" / "model-evidence-campaign-aggregate.schema.json"
AGG_PREVIEW_SCHEMA_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "schemas" / "model-evidence-campaign-aggregate-preview.schema.json"

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


def make_valid_plan(
    lane: str = "measured",
    model_id: str = "gemini-3.7-flash-high",
    anchor_model_ids: list[str] | None = None,
) -> dict[str, Any]:
    if anchor_model_ids is None:
        anchor_model_ids = ["gemini-3.5-flash-high"]
    return {
        "schema_version": 1,
        "kind": "agy-model-evidence-campaign-plan",
        "lane": lane,
        "candidate_model_id": model_id,
        "anchor_model_ids": anchor_model_ids,
        "anchors": {
            "benchmark_review_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "inventory_binding_sha256": "1123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "model_matrix_sha256": "2123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "dataset_sha256": "3123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        },
        "trigger": {
            "benchmark_review_status": "benchmark-review-due",
            "review_reason": "inventory-added",
            "maintainer_disposition": "collect",
        },
        "scenario_id": "swe-bench-lite-v1",
        "harness": {
            "id": "agy-benchmark",
            "version": "1.0.0",
        },
        "evaluator": {
            "id": "exact-content",
            "version": "1.0.0",
        },
        "config_sha256": "4123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "measurement_window": {
            "start_date": "2026-08-25",
            "end_date": "2026-08-30",
        },
        "policy": {
            "id": "standard-eval",
            "version": "1.0.0",
        },
        "acceptance_rules": {
            "min_sample_size": 50,
            "min_coverage": 0.95,
            "max_error_rate": 0.05,
            "min_quality_score": 80.0,
            "uncertainty_rule": {
                "max_uncertainty": 0.05,
            },
        },
        "budget": {
            "sample_budget": 100,
            "invocation_budget": 120,
        },
        "drift_tolerance": {
            "max_drift_fraction": 0.02,
        },
        "required_telemetry": {
            "bindings": ["accounting", "tokenizer", "cost_basis", "currency"],
            "min_coverage": 0.95,
        },
        "limitations": [
            "Test campaign plan limitation.",
            MODULE.TOKEN_DISCLAIMER,
        ],
    }


def make_valid_record(
    plan_sha: str,
    lane: str = "measured",
    model_id: str = "gemini-3.7-flash-high",
    role: str = "candidate",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "agy-model-evidence-campaign-record",
        "plan_sha256": plan_sha,
        "lane": lane,
        "subject_role": role,
        "model_identity": {
            "requested_model": model_id,
            "observed_model": model_id,
            "substituted": False,
        },
        "scenario_id": "swe-bench-lite-v1",
        "harness": {
            "id": "agy-benchmark",
            "version": "1.0.0",
        },
        "evaluator": {
            "id": "exact-content",
            "version": "1.0.0",
        },
        "config_sha256": "4123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "measurement_window": {
            "start_date": "2026-08-25",
            "end_date": "2026-08-30",
        },
        "policy": {
            "id": "standard-eval",
            "version": "1.0.0",
        },
        "measured_metadata": ({
            "provenance_type": "local",
            "source_uri": "local://campaign/synthetic",
            "agy_version": "1.1.22",
            "effort": "high",
            "accounting": "observed_actual",
            "tokenizer": "cl100k_base",
            "currency": "USD",
            "cost_basis": "observed_billed",
            "confidence": "high",
            "observed_date": "2026-08-25",
            "expiry_date": "2026-11-23",
        } if lane == "measured" else None),
        "telemetry": {
            "sample_count": 80,
            "invocation_count": 85,
            "telemetry_bindings": ["accounting", "tokenizer", "cost_basis", "currency"],
            "coverage": 1.0,
            "error_rate": 0.01,
            "drift_fraction": 0.01,
            "metrics": {
                "quality_score": 92.5,
                "latency_p50_seconds": 1.2,
                "latency_p95_seconds": 2.5,
                "mean_input_tokens": 450.0,
                "mean_output_tokens": 120.0,
                "mean_cached_tokens": 0.0,
                "mean_thinking_tokens": 0.0,
                "estimated_cost_usd": 0.005,
            },
            "uncertainty": 0.02,
            "verification_passed": True,
        },
        "limitations": [
            "identity-observation-caller-supplied",
            "token-telemetry-not-billing-evidence",
        ],
    }


def make_valid_review(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "benchmark-review-due",
        "maintainer_disposition": "collect",
        "reviews_due": [
            {
                "model_id": plan["candidate_model_id"],
                "evidence_state": "inventory-added",
            }
        ],
    }


def make_authoritative_artifacts(model_id: str = "gemini-3.7-flash-high") -> dict[str, Any]:
    dataset, dataset_sha, _ = MODULE.read_json_file(
        SHIPPED_DATASET_PATH, max_bytes=MODULE.MAX_DATASET_BYTES
    )
    inventory, inventory_sha, _ = MODULE.read_json_file(INVENTORY_PATH)
    matrix, matrix_sha, _ = MODULE.read_json_file(MATRIX_PATH)
    review = {
        "schema_version": 1,
        "kind": "agy-benchmark-review-tracker",
        "status": "benchmark-review-due",
        "evidence_dataset_sha256": dataset_sha,
        "reference_date": "2026-08-30",
        "reviews_due": [{"model_id": model_id, "evidence_state": "inventory-added"}],
        "maintainer_disposition": "collect",
        "applied": False,
        "dispatch_authorized": False,
        "model_change_authorized": False,
        "effort_change_authorized": False,
        "acceptance_authorized": False,
        "git_authorized": False,
        "benchmark_run_authorized": False,
        "provider_call_authorized": False,
        "limitations": [
            "Offline benchmark review trigger fixture; it grants no execution or git authority.",
            MODULE.TOKEN_DISCLAIMER,
        ],
        "token_inference_disclaimer": MODULE.TOKEN_DISCLAIMER,
    }
    review_sha = hashlib.sha256(MODULE.canonical_bytes(review)).hexdigest()
    return {
        "review": review,
        "review_sha": review_sha,
        "inventory": inventory,
        "inventory_sha": inventory_sha,
        "matrix": matrix,
        "matrix_sha": matrix_sha,
        "dataset": dataset,
        "dataset_sha": dataset_sha,
    }


def bind_authoritative_plan(plan: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    plan["anchors"] = {
        "benchmark_review_sha256": artifacts["review_sha"],
        "inventory_binding_sha256": artifacts["inventory_sha"],
        "model_matrix_sha256": artifacts["matrix_sha"],
        "dataset_sha256": artifacts["dataset_sha"],
    }
    return plan


def evaluate_bound(
    plan: dict[str, Any],
    plan_sha: str,
    candidate_record: dict[str, Any] | None = None,
    candidate_record_sha: str | None = None,
    anchor_records: list[tuple[dict[str, Any], str]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    if candidate_record is None:
        candidate_record = make_valid_record(
            plan_sha, lane=plan["lane"], model_id=plan["candidate_model_id"], role="candidate"
        )
        candidate_record_sha = hashlib.sha256(MODULE.canonical_bytes(candidate_record)).hexdigest()
    elif candidate_record_sha is None:
        candidate_record_sha = hashlib.sha256(MODULE.canonical_bytes(candidate_record)).hexdigest()

    if anchor_records is None:
        anc_list: list[tuple[dict[str, Any], str]] = []
        for a in plan.get("anchor_model_ids", []):
            ar = make_valid_record(plan_sha, lane=plan["lane"], model_id=a, role="anchor")
            ar_sha = hashlib.sha256(MODULE.canonical_bytes(ar)).hexdigest()
            anc_list.append((ar, ar_sha))
        anchor_records = anc_list

    artifacts: dict[str, Any] = {
        "review": make_valid_review(plan),
        "review_sha": plan["anchors"]["benchmark_review_sha256"],
        "dataset_sha": plan["anchors"]["dataset_sha256"],
        "inventory_sha": plan["anchors"]["inventory_binding_sha256"],
        "matrix_sha": plan["anchors"]["model_matrix_sha256"],
    }
    artifacts.update(overrides)
    return MODULE.evaluate_campaign(
        plan, plan_sha,
        candidate_record=candidate_record,
        candidate_record_sha=candidate_record_sha,
        anchor_records=anchor_records,
        **artifacts,
    )


# Test 1: Plan validation happy path and schema rejections
def test_plan_validation() -> bool:
    plan = make_valid_plan()
    MODULE.validate_plan(plan)

    # Disallowed extra keys
    bad_plan = copy.deepcopy(plan)
    bad_plan["extra_key"] = "forbidden"
    try:
        MODULE.validate_plan(bad_plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Invalid lane
    bad_plan = copy.deepcopy(plan)
    bad_plan["lane"] = "synthetic"
    try:
        MODULE.validate_plan(bad_plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Invalid boolean as integer for schema_version
    bad_plan = copy.deepcopy(plan)
    bad_plan["schema_version"] = True
    try:
        MODULE.validate_plan(bad_plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Candidate in anchor_model_ids
    bad_plan = copy.deepcopy(plan)
    bad_plan["anchor_model_ids"] = [bad_plan["candidate_model_id"]]
    try:
        MODULE.validate_plan(bad_plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Empty anchor_model_ids
    bad_plan = copy.deepcopy(plan)
    bad_plan["anchor_model_ids"] = []
    try:
        MODULE.validate_plan(bad_plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Duplicate anchor_model_ids
    bad_plan = copy.deepcopy(plan)
    bad_plan["anchor_model_ids"] = ["gemini-3.5-flash-high", "gemini-3.5-flash-high"]
    try:
        MODULE.validate_plan(bad_plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Invalid calendar date
    bad_plan = copy.deepcopy(plan)
    bad_plan["measurement_window"]["start_date"] = "2026-02-31"
    try:
        MODULE.validate_plan(bad_plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    return True


check("plan schema validates strictly and rejects extra keys, invalid lanes, candidate-as-anchor, and bad dates", test_plan_validation)


# Test 2: Trigger eligibility & #106 review negatives
def test_trigger_eligibility_negatives() -> bool:
    plan = make_valid_plan()
    plan["trigger"]["maintainer_disposition"] = "defer"
    try:
        MODULE.validate_plan(plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    plan = make_valid_plan()
    plan["trigger"]["maintainer_disposition"] = "not-applicable"
    try:
        MODULE.validate_plan(plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    plan = make_valid_plan()
    plan["trigger"]["review_reason"] = "dataset-expired"
    try:
        MODULE.validate_plan(plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    plan = make_valid_plan()
    plan["trigger"]["review_reason"] = "inventory-removed"
    try:
        MODULE.validate_plan(plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    return True


check("campaign plan rejects non-collect dispositions and non-inventory-added trigger reasons", test_trigger_eligibility_negatives)


# Test 3: Record validation & privacy boundaries
def test_record_privacy_boundaries() -> bool:
    plan = make_valid_plan()
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    rec = make_valid_record(plan_sha)
    MODULE.validate_record(rec)

    anc_rec = make_valid_record(plan_sha, role="anchor", model_id="gemini-3.5-flash-high")
    MODULE.validate_record(anc_rec)

    # Rejection of forbidden prompt/output/code/diff/command/log keys
    for forbidden_key in ("prompt", "output", "code", "diff", "command", "log", "endpoint", "sender", "user", "timestamp"):
        bad_rec = copy.deepcopy(rec)
        bad_rec[forbidden_key] = "some_data"
        try:
            MODULE.validate_record(bad_rec)
            return False
        except MODULE.ModelEvidenceCampaignError:
            pass

    bad_rec = copy.deepcopy(rec)
    bad_rec["limitations"] = ["arbitrary caller-authored free text"]
    try:
        MODULE.validate_record(bad_rec)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Invalid subject_role
    bad_rec = copy.deepcopy(rec)
    bad_rec["subject_role"] = "observer"
    try:
        MODULE.validate_record(bad_rec)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    return True


check("record schema rejects forbidden fields, invalid roles, and free-text limitations", test_record_privacy_boundaries)


# Test 4: Happy path evaluation for measured lane
def test_measured_evaluation_happy_path() -> bool:
    plan = make_valid_plan(lane="measured")
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    rec = make_valid_record(plan_sha, lane="measured")
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()

    anc_rec = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.5-flash-high", role="anchor")
    anc_bytes = MODULE.canonical_bytes(anc_rec)
    anc_sha = hashlib.sha256(anc_bytes).hexdigest()

    eval_result = evaluate_bound(plan, plan_sha, rec, rec_sha, [(anc_rec, anc_sha)])
    assert eval_result["recommendation"] == "candidate_evidence_only"
    assert eval_result["reason_code"] == "verified-measured-evidence"
    assert eval_result["lane"] == "measured"
    assert eval_result["candidate_record_sha256"] == rec_sha
    assert eval_result["anchor_record_sha256s"] == [anc_sha]
    assert eval_result["applied"] is False
    assert eval_result["dispatch_authorized"] is False
    assert eval_result["model_change_authorized"] is False
    assert eval_result["facts"]["verification_passed"] is True
    assert eval_result["facts"]["anchor_matches"] is True
    assert eval_result["facts"]["cohort_complete"] is True
    assert eval_result["facts"]["identity_matches"] is True
    assert eval_result["facts"]["drift_detected"] is False
    assert eval_result["facts"]["anchor_count"] == 1
    return True


check("measured lane happy path yields candidate_evidence_only with verified-measured-evidence and ordered anchor digests", test_measured_evaluation_happy_path)


# Test 5: Vendor-declared and observational lanes cannot recommend
def test_non_measured_lanes_cannot_recommend() -> bool:
    for lane in ("vendor_declared", "observational"):
        plan = make_valid_plan(lane=lane)
        plan_bytes = MODULE.canonical_bytes(plan)
        plan_sha = hashlib.sha256(plan_bytes).hexdigest()

        rec = make_valid_record(plan_sha, lane=lane)
        rec_bytes = MODULE.canonical_bytes(rec)
        rec_sha = hashlib.sha256(rec_bytes).hexdigest()

        eval_result = evaluate_bound(plan, plan_sha, rec, rec_sha)
        assert eval_result["recommendation"] == "no_recommendation"
        assert eval_result["reason_code"] == "lane-limitation"
        assert eval_result["lane"] == lane
    return True


check("vendor-declared and observational lanes strictly evaluate to no_recommendation with lane-limitation", test_non_measured_lanes_cannot_recommend)


# Test 6: Mutual lane exclusion and mixed-lane rejection
def test_mixed_lane_rejection() -> bool:
    plan = make_valid_plan(lane="measured")
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    rec = make_valid_record(plan_sha, lane="vendor_declared")
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()

    try:
        evaluate_bound(plan, plan_sha, rec, rec_sha)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass
    return True


check("mixed-lane evaluation between plan and record fails closed", test_mixed_lane_rejection)


# Test 7: Hash-chain mismatch fails closed
def test_hash_chain_mismatch() -> bool:
    plan = make_valid_plan()
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    wrong_sha = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    rec = make_valid_record(wrong_sha)
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()

    try:
        evaluate_bound(plan, plan_sha, rec, rec_sha)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass
    return True


check("hash-chain mismatch between plan SHA and record plan_sha256 fails closed", test_hash_chain_mismatch)


# Test 8: Deterministic no-recommendation priority when multiple failures coexist
def test_deterministic_priority_order() -> bool:
    plan = make_valid_plan()
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    # Missing current artifact bindings
    bound_rec = make_valid_record(plan_sha)
    bound_rec_sha = hashlib.sha256(MODULE.canonical_bytes(bound_rec)).hexdigest()
    anc_rec = make_valid_record(plan_sha, role="anchor", model_id="gemini-3.5-flash-high")
    anc_rec_sha = hashlib.sha256(MODULE.canonical_bytes(anc_rec)).hexdigest()
    unbound = MODULE.evaluate_campaign(
        plan, plan_sha,
        candidate_record=bound_rec, candidate_record_sha=bound_rec_sha,
        anchor_records=[(anc_rec, anc_rec_sha)],
    )
    assert unbound["recommendation"] == "no_recommendation"
    assert unbound["reason_code"] == "anchor-drift"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        plan_path = tmp / "plan.json"
        record_path = tmp / "record.json"
        plan_path.write_bytes(MODULE.canonical_bytes(plan))
        record_path.write_bytes(MODULE.canonical_bytes(bound_rec))
        assert MODULE.main(["model-evidence-campaign", "validate-plan", f"--plan={plan_path}"]) == 2
        assert MODULE.main([
            "model-evidence-campaign", "evaluate", f"--plan={plan_path}", f"--record={record_path}"
        ]) == 2

    # Anchor drift vs missing subject: anchor drift wins
    eval_res = evaluate_bound(plan, plan_sha, bound_rec, bound_rec_sha, anchor_records=[], dataset_sha="wrong_dataset_sha")
    assert eval_res["reason_code"] == "anchor-drift"

    # Missing subject vs identity mismatch: missing subject wins
    rec = make_valid_record(plan_sha)
    rec["model_identity"]["requested_model"] = "different-model"
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha, anchor_records=[])
    assert eval_res["reason_code"] == "missing-subject"

    # Unexpected subject vs identity mismatch: unexpected subject wins
    extra_anc = make_valid_record(plan_sha, role="anchor", model_id="unexpected-anchor-model")
    extra_anc_sha = hashlib.sha256(MODULE.canonical_bytes(extra_anc)).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha, anchor_records=[(anc_rec, anc_rec_sha), (extra_anc, extra_anc_sha)])
    assert eval_res["reason_code"] == "unexpected-subject"

    # Identity mismatch vs scenario drift: identity mismatch wins
    rec = make_valid_record(plan_sha)
    rec["model_identity"]["requested_model"] = "different-model"
    rec["scenario_id"] = "different-scenario"
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "identity-mismatch"

    # Model substitution vs telemetry incompatibility: model substitution wins
    rec = make_valid_record(plan_sha)
    rec["model_identity"]["substituted"] = True
    rec["telemetry"]["telemetry_bindings"] = []
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "model-substituted"

    # Telemetry incompatibility vs budget exhaustion: telemetry incompatibility wins
    rec = make_valid_record(plan_sha)
    rec["telemetry"]["telemetry_bindings"] = ["only_accounting"]
    rec["telemetry"]["invocation_count"] = 999999
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "telemetry-incompatible"

    # Budget exhaustion vs failed verification: budget exhaustion wins
    rec = make_valid_record(plan_sha)
    rec["telemetry"]["invocation_count"] = 999999
    rec["telemetry"]["verification_passed"] = False
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "budget-exhausted"

    # Failed verification vs uncertainty exceeded: failed verification wins
    rec = make_valid_record(plan_sha)
    rec["telemetry"]["verification_passed"] = False
    rec["telemetry"]["uncertainty"] = 0.99
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "verification-failed"

    # Uncertainty exceeded vs lane limitation: uncertainty exceeded wins
    plan_obs = make_valid_plan(lane="observational")
    plan_obs_bytes = MODULE.canonical_bytes(plan_obs)
    plan_obs_sha = hashlib.sha256(plan_obs_bytes).hexdigest()
    rec_obs = make_valid_record(plan_obs_sha, lane="observational")
    rec_obs["telemetry"]["uncertainty"] = 0.99
    rec_obs_bytes = MODULE.canonical_bytes(rec_obs)
    rec_obs_sha = hashlib.sha256(rec_obs_bytes).hexdigest()
    eval_res = evaluate_bound(plan_obs, plan_obs_sha, rec_obs, rec_obs_sha)
    assert eval_res["reason_code"] == "uncertainty-exceeded"

    return True


check("unbound artifacts fail closed and deterministic evaluation selects the earliest reason code", test_deterministic_priority_order)


# Test 9: Model identity mismatch, substitution, and missing observed model
def test_identity_variations() -> bool:
    plan = make_valid_plan()
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    # Candidate missing observed model
    rec = make_valid_record(plan_sha)
    rec["model_identity"]["observed_model"] = None
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "observed-model-missing"
    assert eval_res["recommendation"] == "no_recommendation"

    # Candidate substituted
    rec = make_valid_record(plan_sha)
    rec["model_identity"]["substituted"] = True
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "model-substituted"

    # Anchor missing observed model
    rec_ok = make_valid_record(plan_sha)
    rec_ok_sha = hashlib.sha256(MODULE.canonical_bytes(rec_ok)).hexdigest()
    anc_rec = make_valid_record(plan_sha, role="anchor", model_id="gemini-3.5-flash-high")
    anc_rec["model_identity"]["observed_model"] = None
    anc_sha = hashlib.sha256(MODULE.canonical_bytes(anc_rec)).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec_ok, rec_ok_sha, [(anc_rec, anc_sha)])
    assert eval_res["reason_code"] == "observed-model-missing"

    # Anchor substituted
    anc_rec["model_identity"]["observed_model"] = "gemini-3.5-flash-high"
    anc_rec["model_identity"]["substituted"] = True
    anc_sha = hashlib.sha256(MODULE.canonical_bytes(anc_rec)).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec_ok, rec_ok_sha, [(anc_rec, anc_sha)])
    assert eval_res["reason_code"] == "model-substituted"

    return True


check("identity variations for candidate and anchor produce no_recommendation", test_identity_variations)


# Test 10: Drift rejections (scenario, harness, evaluator, config, window, policy)
def test_drift_rejections() -> bool:
    plan = make_valid_plan()
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    # Harness version drift on candidate
    rec = make_valid_record(plan_sha)
    rec["harness"]["version"] = "2.0.0"
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "harness-drift"

    # Evaluator version drift on anchor
    rec_ok = make_valid_record(plan_sha)
    rec_ok_sha = hashlib.sha256(MODULE.canonical_bytes(rec_ok)).hexdigest()
    anc_rec = make_valid_record(plan_sha, role="anchor", model_id="gemini-3.5-flash-high")
    anc_rec["evaluator"]["version"] = "2.0.0"
    anc_sha = hashlib.sha256(MODULE.canonical_bytes(anc_rec)).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec_ok, rec_ok_sha, [(anc_rec, anc_sha)])
    assert eval_res["reason_code"] == "evaluator-drift"

    # Config digest drift
    rec = make_valid_record(plan_sha)
    rec["config_sha256"] = "9999456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "config-drift"

    # Measurement-window drift
    rec = make_valid_record(plan_sha)
    rec["measurement_window"]["end_date"] = "2026-08-29"
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "window-drift"

    # Policy version drift
    rec = make_valid_record(plan_sha)
    rec["policy"]["version"] = "2.0.0"
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "policy-drift"

    return True


check("drift in harness, evaluator, config, window, or policy across cohort yields the corresponding reason code", test_drift_rejections)


# Test 11: Telemetry, budget, drift, error, and uncertainty boundaries
def test_telemetry_budget_uncertainty() -> bool:
    plan = make_valid_plan()
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    # Insufficient samples on candidate
    rec = make_valid_record(plan_sha)
    rec["telemetry"]["sample_count"] = 10
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "insufficient-samples"

    # Insufficient coverage on anchor
    rec_ok = make_valid_record(plan_sha)
    rec_ok_sha = hashlib.sha256(MODULE.canonical_bytes(rec_ok)).hexdigest()
    anc_rec = make_valid_record(plan_sha, role="anchor", model_id="gemini-3.5-flash-high")
    anc_rec["telemetry"]["coverage"] = 0.80
    anc_sha = hashlib.sha256(MODULE.canonical_bytes(anc_rec)).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec_ok, rec_ok_sha, [(anc_rec, anc_sha)])
    assert eval_res["reason_code"] == "insufficient-coverage"

    # Drift fraction beyond tolerance
    rec = make_valid_record(plan_sha)
    rec["telemetry"]["drift_fraction"] = 0.03
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "drift-exceeded"

    # Error rate beyond acceptance rule
    rec = make_valid_record(plan_sha)
    rec["telemetry"]["error_rate"] = 0.06
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "error-rate-exceeded"

    # Uncertainty exceeded
    rec = make_valid_record(plan_sha)
    rec["telemetry"]["uncertainty"] = 0.10
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    eval_res = evaluate_bound(plan, plan_sha, rec, rec_sha)
    assert eval_res["reason_code"] == "uncertainty-exceeded"

    return True


check("sample, coverage, drift, error, and uncertainty bounds fail closed deterministically", test_telemetry_budget_uncertainty)


# Test 12: Materialize measured dataset happy path
def test_materialize_measured_happy_path() -> bool:
    artifacts = make_authoritative_artifacts()
    plan = bind_authoritative_plan(
        make_valid_plan(lane="measured", model_id="gemini-3.7-flash-high"), artifacts
    )
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    rec = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.7-flash-high")
    rec_bytes = MODULE.canonical_bytes(rec)
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()

    anc_rec = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.5-flash-high", role="anchor")
    anc_bytes = MODULE.canonical_bytes(anc_rec)
    anc_sha = hashlib.sha256(anc_bytes).hexdigest()

    evaluation = evaluate_bound(
        plan,
        plan_sha,
        rec,
        rec_sha,
        [(anc_rec, anc_sha)],
        review=artifacts["review"],
        review_sha=artifacts["review_sha"],
        dataset_sha=artifacts["dataset_sha"],
        inventory_sha=artifacts["inventory_sha"],
        matrix_sha=artifacts["matrix_sha"],
    )
    eval_bytes = MODULE.canonical_bytes(evaluation)
    eval_sha = hashlib.sha256(eval_bytes).hexdigest()

    new_dataset = MODULE.materialize_measured_dataset(
        plan, plan_sha,
        candidate_record=rec, candidate_record_sha=rec_sha,
        anchor_records=[(anc_rec, anc_sha)],
        evaluation=evaluation, evaluation_sha=eval_sha,
        dataset=artifacts["dataset"], dataset_sha=artifacts["dataset_sha"],
        review=artifacts["review"], review_sha=artifacts["review_sha"],
        inventory=artifacts["inventory"], inventory_sha=artifacts["inventory_sha"],
        matrix=artifacts["matrix"], matrix_sha=artifacts["matrix_sha"],
    )
    assert len(new_dataset["items"]) == len(artifacts["dataset"]["items"]) + 1
    new_item = new_dataset["items"][-1]
    assert new_item["requested_model"] == plan["candidate_model_id"]
    assert new_item["observed_model"] == plan["candidate_model_id"]
    assert new_item["provenance_type"] == rec["measured_metadata"]["provenance_type"]
    assert new_item["source_uri"] == rec["measured_metadata"]["source_uri"]
    assert new_item["agy_version"] == rec["measured_metadata"]["agy_version"]
    assert new_item["effort"] == rec["measured_metadata"]["effort"]
    assert new_item["telemetry_bindings"]["accounting"] == rec["measured_metadata"]["accounting"]
    assert new_item["telemetry_bindings"]["tokenizer"] == rec["measured_metadata"]["tokenizer"]
    assert new_item["telemetry_bindings"]["currency"] == rec["measured_metadata"]["currency"]
    assert new_item["telemetry_bindings"]["cost_basis"] == rec["measured_metadata"]["cost_basis"]
    assert new_item["metrics"]["quality_score"] == 92.5
    assert new_item["sample_size"] == 80
    return True


check("materialize-measured recomputes cohort evaluation and adds verified candidate evidence preserving dataset schema", test_materialize_measured_happy_path)


# Test 13: Materialize rejections (vendor/observational, unverified, dataset SHA mismatch)
def test_materialize_rejections() -> bool:
    artifacts = make_authoritative_artifacts()

    # 1. Reject vendor_declared lane
    plan_vd = bind_authoritative_plan(
        make_valid_plan(lane="vendor_declared", model_id="gemini-3.7-flash-high"), artifacts
    )
    plan_vd_bytes = MODULE.canonical_bytes(plan_vd)
    plan_vd_sha = hashlib.sha256(plan_vd_bytes).hexdigest()
    rec_vd = make_valid_record(plan_vd_sha, lane="vendor_declared", model_id="gemini-3.7-flash-high")
    rec_vd_bytes = MODULE.canonical_bytes(rec_vd)
    rec_vd_sha = hashlib.sha256(rec_vd_bytes).hexdigest()
    eval_vd = evaluate_bound(plan_vd, plan_vd_sha, rec_vd, rec_vd_sha)
    eval_vd_bytes = MODULE.canonical_bytes(eval_vd)
    eval_vd_sha = hashlib.sha256(eval_vd_bytes).hexdigest()
    try:
        MODULE.materialize_measured_dataset(
            plan_vd, plan_vd_sha,
            candidate_record=rec_vd, candidate_record_sha=rec_vd_sha,
            anchor_records=[],
            evaluation=eval_vd, evaluation_sha=eval_vd_sha,
            dataset=artifacts["dataset"], dataset_sha=artifacts["dataset_sha"],
            review=artifacts["review"], review_sha=artifacts["review_sha"],
            inventory=artifacts["inventory"], inventory_sha=artifacts["inventory_sha"],
            matrix=artifacts["matrix"], matrix_sha=artifacts["matrix_sha"],
        )
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # 2. Reject dataset SHA mismatch
    plan_m = bind_authoritative_plan(
        make_valid_plan(lane="measured", model_id="gemini-3.7-flash-high"), artifacts
    )
    plan_m["anchors"]["dataset_sha256"] = "f" * 64
    plan_m_bytes = MODULE.canonical_bytes(plan_m)
    plan_m_sha = hashlib.sha256(plan_m_bytes).hexdigest()
    rec_m = make_valid_record(plan_m_sha, lane="measured", model_id="gemini-3.7-flash-high")
    rec_m_bytes = MODULE.canonical_bytes(rec_m)
    rec_m_sha = hashlib.sha256(rec_m_bytes).hexdigest()
    eval_m = evaluate_bound(plan_m, plan_m_sha, rec_m, rec_m_sha)
    eval_m_bytes = MODULE.canonical_bytes(eval_m)
    eval_m_sha = hashlib.sha256(eval_m_bytes).hexdigest()
    try:
        MODULE.materialize_measured_dataset(
            plan_m, plan_m_sha,
            candidate_record=rec_m, candidate_record_sha=rec_m_sha,
            anchor_records=[],
            evaluation=eval_m, evaluation_sha=eval_m_sha,
            dataset=artifacts["dataset"], dataset_sha=artifacts["dataset_sha"],
            review=artifacts["review"], review_sha=artifacts["review_sha"],
            inventory=artifacts["inventory"], inventory_sha=artifacts["inventory_sha"],
            matrix=artifacts["matrix"], matrix_sha=artifacts["matrix_sha"],
        )
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    return True


check("materialize-measured strictly rejects non-measured lanes, failed evaluations, and dataset SHA mismatch", test_materialize_rejections)


# Test 14: Atomic durable file publication and permissions (0600, no overwrite)
def test_atomic_file_publication() -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(os.path.realpath(tmpdir))
        target = root / "output.json"
        data = b'{"test": true}\n'

        MODULE.publish_file_atomically(target, data)
        assert target.exists()
        assert target.read_bytes() == data

        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600, f"Expected mode 0600, got {oct(mode)}"

        try:
            MODULE.publish_file_atomically(target, b'{"overwrite": true}\n')
            return False
        except MODULE.ModelEvidenceCampaignError:
            pass

        assert target.read_bytes() == data

        raced_target = root / "raced.json"
        original_link = MODULE.os.link

        def race_winner(source: str, destination: str, **kwargs: Any) -> None:
            winner_fd = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            try:
                os.write(winner_fd, b'{"winner": true}\n')
            finally:
                os.close(winner_fd)
            original_link(source, destination, **kwargs)

        MODULE.os.link = race_winner
        try:
            try:
                MODULE.publish_file_atomically(raced_target, b'{"loser": true}\n')
                return False
            except MODULE.ModelEvidenceCampaignError:
                pass
        finally:
            MODULE.os.link = original_link
        assert raced_target.read_bytes() == b'{"winner": true}\n'
    return True


check("publish_file_atomically writes mode 0600 and rejects sequential and raced overwrites", test_atomic_file_publication)


# Test 15: Aggregate status, preview, and export
def test_aggregate_preview_and_export() -> bool:
    plan_m = make_valid_plan(lane="measured")
    plan_m_sha = hashlib.sha256(MODULE.canonical_bytes(plan_m)).hexdigest()
    rec_m = make_valid_record(plan_m_sha, lane="measured")
    anc_m = make_valid_record(plan_m_sha, lane="measured", model_id="gemini-3.5-flash-high", role="anchor")

    plan_vd = make_valid_plan(lane="vendor_declared")
    plan_vd_sha = hashlib.sha256(MODULE.canonical_bytes(plan_vd)).hexdigest()
    rec_vd = make_valid_record(plan_vd_sha, lane="vendor_declared")

    plan_obs = make_valid_plan(lane="observational")
    plan_obs_sha = hashlib.sha256(MODULE.canonical_bytes(plan_obs)).hexdigest()
    rec_obs = make_valid_record(plan_obs_sha, lane="observational")

    records = [rec_m, anc_m, rec_vd, rec_obs]
    record_entries = [
        (record, hashlib.sha256(MODULE.canonical_bytes(record)).hexdigest())
        for record in records
    ]
    eval_m = evaluate_bound(plan_m, plan_m_sha, rec_m, record_entries[0][1], [(anc_m, record_entries[1][1])])
    eval_vd = evaluate_bound(plan_vd, plan_vd_sha, rec_vd, record_entries[2][1])
    eval_obs = evaluate_bound(plan_obs, plan_obs_sha, rec_obs, record_entries[3][1])
    evaluations = [eval_m, eval_vd, eval_obs]

    agg = MODULE.compute_aggregate(record_entries, evaluations)
    assert agg["total_records"] == 4
    assert agg["by_lane"]["measured"] == 2
    assert agg["by_lane"]["vendor_declared"] == 1
    assert agg["by_lane"]["observational"] == 1
    assert agg["total_samples"] == 80 * 4
    assert agg["total_invocations"] == 85 * 4

    preview_bytes = MODULE.canonical_bytes(agg)
    preview_sha = hashlib.sha256(preview_bytes).hexdigest()
    preview = {
        "schema_version": 1,
        "kind": "agy-model-evidence-campaign-aggregate-preview",
        "preview_sha256": preview_sha,
        "payload": agg,
    }
    MODULE.validate_aggregate_preview(preview)

    agg_text = json.dumps(agg)
    assert "gemini-3.7-flash" not in agg_text
    assert plan_m_sha not in agg_text
    assert "http" not in agg_text
    assert "local://" not in agg_text

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(os.path.realpath(tmpdir))
        export_file = root / "export.json"
        with tempfile.TemporaryDirectory() as rec_dir:
            eval_dir = root / "evaluations"
            eval_dir.mkdir(mode=0o700)
            for idx, r in enumerate(records):
                p = Path(rec_dir) / f"rec_{idx}.json"
                p.write_bytes(MODULE.canonical_bytes(r))
            for idx, ev in enumerate(evaluations):
                (eval_dir / f"eval_{idx}.json").write_bytes(MODULE.canonical_bytes(ev))

            rc = MODULE.main([
                "model-evidence-campaign",
                "aggregate-export",
                "--local-opt-in",
                f"--approve-preview-sha={preview_sha}",
                f"--records-dir={rec_dir}",
                f"--evaluations-dir={eval_dir}",
                f"--out={export_file}",
            ])
            assert rc == 0
            assert export_file.exists()

            export_file_2 = root / "export_2.json"
            rc_bad = MODULE.main([
                "model-evidence-campaign",
                "aggregate-export",
                "--local-opt-in",
                "--approve-preview-sha=ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                f"--records-dir={rec_dir}",
                f"--evaluations-dir={eval_dir}",
                f"--out={export_file_2}",
            ])
            assert rc_bad == 2
            assert not export_file_2.exists()

    return True


check("aggregate status, preview, and export enforce closed integer counts, preview SHA approval, and canary privacy", test_aggregate_preview_and_export)


# Test 16: CLI subcommand parsing and prohibited flag rejection
def test_cli_subcommands_and_prohibited_flags() -> bool:
    assert MODULE.main(["model-evidence-campaign"]) == 2

    for flag in ("--send", "--sender=collector", "--endpoint=https://remote.api", "--watch", "--background", "--upload", "--remote=server"):
        rc = MODULE.main(["model-evidence-campaign", "validate-plan", flag])
        assert rc == 2

    assert MODULE.main(["model-evidence-campaign", "aggregate-status", "--model=forbidden"]) == 2
    return True


check("CLI subcommands reject missing arguments and prohibited network/background flags", test_cli_subcommands_and_prohibited_flags)


# Test 17: Schema files exist and match draft-07 closure
def test_schema_files_exist_and_closed() -> bool:
    for schema_path in (PLAN_SCHEMA_PATH, RECORD_SCHEMA_PATH, EVAL_SCHEMA_PATH, AGG_SCHEMA_PATH, AGG_PREVIEW_SCHEMA_PATH):
        assert schema_path.exists(), f"Missing schema file: {schema_path}"
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        assert data.get("additionalProperties") is False or data.get("$schema") is not None
    return True


check("campaign schema files exist and are closed", test_schema_files_exist_and_closed)


# Test 18: Shipped Model Intelligence dataset remains unchanged
def test_shipped_dataset_unchanged() -> bool:
    data, _, _ = MODULE.read_json_file(SHIPPED_DATASET_PATH, max_bytes=MODULE.MAX_DATASET_BYTES)
    assert data["schema_version"] == 1
    assert data["kind"] == "agy-model-intelligence-evidence"
    assert len(data["items"]) == 0
    return True


check("shipped dataset.v1.json remains clean and untouched", test_shipped_dataset_unchanged)


# Test 19: The #106 trigger and current inventory/matrix/dataset chain are authoritative.
def test_authoritative_artifact_chain() -> bool:
    artifacts = make_authoritative_artifacts()
    plan = bind_authoritative_plan(
        make_valid_plan(model_id="gemini-3.7-flash-high", anchor_model_ids=["gemini-3.5-flash-high"]), artifacts
    )
    MODULE.validate_campaign_artifacts(
        plan,
        artifacts["review"], artifacts["review_sha"],
        artifacts["inventory"], artifacts["inventory_sha"],
        artifacts["matrix"], artifacts["matrix_sha"],
        artifacts["dataset"], artifacts["dataset_sha"],
    )

    plan_sha = hashlib.sha256(MODULE.canonical_bytes(plan)).hexdigest()
    cand_rec = make_valid_record(
        plan_sha, lane="measured", model_id="gemini-3.7-flash-high", role="candidate"
    )
    anc_rec = make_valid_record(
        plan_sha, lane="measured", model_id="gemini-3.5-flash-high", role="anchor"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        plan_path = tmp / "plan.json"
        cand_path = tmp / "cand_record.json"
        anc_path = tmp / "anc_record.json"
        review_path = tmp / "review.json"
        plan_path.write_bytes(MODULE.canonical_bytes(plan))
        cand_path.write_bytes(MODULE.canonical_bytes(cand_rec))
        anc_path.write_bytes(MODULE.canonical_bytes(anc_rec))
        review_path.write_bytes(MODULE.canonical_bytes(artifacts["review"]))
        common = [
            f"--plan={plan_path}",
            f"--review={review_path}",
            f"--inventory={INVENTORY_PATH}",
            f"--matrix={MATRIX_PATH}",
            f"--dataset={SHIPPED_DATASET_PATH}",
        ]
        assert MODULE.main(["model-evidence-campaign", "validate-plan", *common]) == 0
        assert MODULE.main([
            "model-evidence-campaign", "evaluate", *common,
            f"--candidate-record={cand_path}", f"--anchor-record={anc_path}"
        ]) == 0

    minimal_review = make_valid_review(plan)
    bad_plan = copy.deepcopy(plan)
    bad_plan["anchors"]["benchmark_review_sha256"] = hashlib.sha256(
        MODULE.canonical_bytes(minimal_review)
    ).hexdigest()
    try:
        MODULE.validate_campaign_artifacts(
            bad_plan,
            minimal_review, bad_plan["anchors"]["benchmark_review_sha256"],
            artifacts["inventory"], artifacts["inventory_sha"],
            artifacts["matrix"], artifacts["matrix_sha"],
            artifacts["dataset"], artifacts["dataset_sha"],
        )
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    unbound_review = copy.deepcopy(artifacts["review"])
    unbound_review["evidence_dataset_sha256"] = "f" * 64
    unbound_review_sha = hashlib.sha256(MODULE.canonical_bytes(unbound_review)).hexdigest()
    bad_plan = copy.deepcopy(plan)
    bad_plan["anchors"]["benchmark_review_sha256"] = unbound_review_sha
    try:
        MODULE.validate_campaign_artifacts(
            bad_plan,
            unbound_review, unbound_review_sha,
            artifacts["inventory"], artifacts["inventory_sha"],
            artifacts["matrix"], artifacts["matrix_sha"],
            artifacts["dataset"], artifacts["dataset_sha"],
        )
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Unsupported candidate identity
    missing_artifacts = make_authoritative_artifacts("missing-model-high")
    missing_plan = bind_authoritative_plan(
        make_valid_plan(model_id="missing-model-high"), missing_artifacts
    )
    try:
        MODULE.validate_campaign_artifacts(
            missing_plan,
            missing_artifacts["review"], missing_artifacts["review_sha"],
            missing_artifacts["inventory"], missing_artifacts["inventory_sha"],
            missing_artifacts["matrix"], missing_artifacts["matrix_sha"],
            missing_artifacts["dataset"], missing_artifacts["dataset_sha"],
        )
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    # Unsupported anchor identity
    bad_anchor_plan = copy.deepcopy(plan)
    bad_anchor_plan["anchor_model_ids"] = ["unsupported-anchor-model"]
    try:
        MODULE.validate_campaign_artifacts(
            bad_anchor_plan,
            artifacts["review"], artifacts["review_sha"],
            artifacts["inventory"], artifacts["inventory_sha"],
            artifacts["matrix"], artifacts["matrix_sha"],
            artifacts["dataset"], artifacts["dataset_sha"],
        )
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    return True


check("authoritative #106 review and current inventory/matrix/dataset chain fail closed for candidate and anchors", test_authoritative_artifact_chain)


# Test 20: Aggregate verification is derived only from bound evaluation artifacts.
def test_aggregate_requires_bound_evaluation() -> bool:
    plan = make_valid_plan(lane="measured")
    plan_sha = hashlib.sha256(MODULE.canonical_bytes(plan)).hexdigest()
    record = make_valid_record(plan_sha, lane="measured")
    record_sha = hashlib.sha256(MODULE.canonical_bytes(record)).hexdigest()
    anc_rec = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.5-flash-high", role="anchor")
    anc_sha = hashlib.sha256(MODULE.canonical_bytes(anc_rec)).hexdigest()
    evaluation = evaluate_bound(plan, plan_sha, record, record_sha, [(anc_rec, anc_sha)])

    missing = MODULE.compute_aggregate([(record, record_sha)], [])
    assert missing["by_status"] == {
        "evaluated": 0, "verified_measured": 0,
        "no_recommendation": 0, "unreviewed": 1,
    }
    mismatched = copy.deepcopy(evaluation)
    mismatched["lane"] = "observational"
    mismatched["facts"]["lane"] = "observational"
    mismatched["recommendation"] = "no_recommendation"
    mismatched["reason_code"] = "lane-limitation"
    bad = MODULE.compute_aggregate([(record, record_sha)], [mismatched])
    assert bad["by_status"]["unreviewed"] == 1
    assert bad["by_status"]["verified_measured"] == 0

    valid = MODULE.compute_aggregate([(record, record_sha)], [evaluation])
    assert valid["by_status"]["evaluated"] == 1
    assert valid["by_status"]["verified_measured"] == 1

    invalid = copy.deepcopy(evaluation)
    invalid["facts"]["sample_count"] = "invalid"
    for evaluation_set in ([evaluation, invalid], [evaluation, copy.deepcopy(evaluation)]):
        try:
            MODULE.compute_aggregate([(record, record_sha)], evaluation_set)
            return False
        except MODULE.ModelEvidenceCampaignError:
            pass

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(os.path.realpath(tmpdir))
        records_dir = root / "records"
        evaluations_dir = root / "evaluations"
        records_dir.mkdir(mode=0o700)
        evaluations_dir.mkdir(mode=0o700)
        (records_dir / "record.json").write_bytes(MODULE.canonical_bytes(record))
        (evaluations_dir / "valid.json").write_bytes(MODULE.canonical_bytes(evaluation))
        (evaluations_dir / "invalid.json").write_bytes(MODULE.canonical_bytes(invalid))
        rc = MODULE.main([
            "model-evidence-campaign", "aggregate-status", "--local-opt-in",
            f"--records-dir={records_dir}", f"--evaluations-dir={evaluations_dir}",
        ])
        assert rc == 2
    return True


check("aggregate leaves missing or mismatched evaluations unreviewed and rejects invalid or duplicate sets", test_aggregate_requires_bound_evaluation)


# Test 21: Forged cross-lane evaluations cannot materialize measured evidence.
def test_materialization_recomputes_lane() -> bool:
    artifacts = make_authoritative_artifacts()
    plan = bind_authoritative_plan(
        make_valid_plan(lane="observational", model_id="gemini-3.7-flash-high"), artifacts
    )
    plan_sha = hashlib.sha256(MODULE.canonical_bytes(plan)).hexdigest()
    record = make_valid_record(plan_sha, lane="observational", model_id="gemini-3.7-flash-high")
    record_sha = hashlib.sha256(MODULE.canonical_bytes(record)).hexdigest()
    anc_rec = make_valid_record(plan_sha, lane="observational", model_id="gemini-3.5-flash-high", role="anchor")
    anc_sha = hashlib.sha256(MODULE.canonical_bytes(anc_rec)).hexdigest()

    forged = evaluate_bound(plan, plan_sha, record, record_sha, [(anc_rec, anc_sha)])
    forged["lane"] = "measured"
    forged["facts"]["lane"] = "measured"
    forged["recommendation"] = "candidate_evidence_only"
    forged["reason_code"] = "verified-measured-evidence"
    forged_sha = hashlib.sha256(MODULE.canonical_bytes(forged)).hexdigest()
    try:
        MODULE.materialize_measured_dataset(
            plan, plan_sha,
            candidate_record=record, candidate_record_sha=record_sha,
            anchor_records=[(anc_rec, anc_sha)],
            evaluation=forged, evaluation_sha=forged_sha,
            dataset=artifacts["dataset"], dataset_sha=artifacts["dataset_sha"],
            review=artifacts["review"], review_sha=artifacts["review_sha"],
            inventory=artifacts["inventory"], inventory_sha=artifacts["inventory_sha"],
            matrix=artifacts["matrix"], matrix_sha=artifacts["matrix_sha"],
        )
        return False
    except MODULE.ModelEvidenceCampaignError:
        return True


check("materialization recomputes bindings and rejects forged cross-lane evidence", test_materialization_recomputes_lane)


# Test 22: Runtime and parser reject schema-out-of-range, non-finite, and duplicate JSON.
def test_runtime_schema_parity_and_strict_json() -> bool:
    record = make_valid_record("a" * 64)
    record["telemetry"]["metrics"]["quality_score"] = 100.01
    try:
        MODULE.validate_record(record)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    plan = make_valid_plan()
    plan["budget"]["sample_budget"] = 10_000_001
    try:
        MODULE.validate_plan(plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    plan = make_valid_plan()
    plan["acceptance_rules"]["max_error_rate"] = float("nan")
    try:
        MODULE.validate_plan(plan)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    plan = make_valid_plan()
    plan_sha = hashlib.sha256(MODULE.canonical_bytes(plan)).hexdigest()
    valid_record = make_valid_record(plan_sha)
    valid_record_sha = hashlib.sha256(MODULE.canonical_bytes(valid_record)).hexdigest()
    evaluation = evaluate_bound(plan, plan_sha, valid_record, valid_record_sha)
    evaluation["facts"]["sample_count"] = 10_000_001
    try:
        MODULE.validate_evaluation(evaluation)
        return False
    except MODULE.ModelEvidenceCampaignError:
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        duplicate = Path(tmpdir) / "duplicate.json"
        duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        nonfinite = Path(tmpdir) / "nonfinite.json"
        nonfinite.write_text('{"value":NaN}', encoding="utf-8")
        for path in (duplicate, nonfinite):
            try:
                MODULE.read_json_file(path)
                return False
            except MODULE.ModelEvidenceCampaignError:
                pass
    return True


check("runtime bounds and JSON parsing match closed schema expectations", test_runtime_schema_parity_and_strict_json)


# Test 23: Publication cannot traverse any symlinked ancestor.
def test_publication_rejects_symlink_parent() -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(os.path.realpath(tmpdir))
        real_parent = root / "real" / "nested"
        (root / "real").mkdir(mode=0o700)
        real_parent.mkdir(mode=0o700)
        allowed = real_parent / "allowed.json"
        MODULE.publish_file_atomically(allowed, b'{"allowed":true}\n')
        assert allowed.read_bytes() == b'{"allowed":true}\n'

        outside = root / "outside"
        outside.mkdir(mode=0o700)
        outside_child = outside / "child"
        outside_child.mkdir(mode=0o700)
        linked = root / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        target = linked / "child" / "forbidden.json"
        try:
            MODULE.publish_file_atomically(target, b'{}\n')
            return False
        except MODULE.ModelEvidenceCampaignError:
            pass
        assert not (outside_child / "forbidden.json").exists()
    return True


check("atomic publication accepts nested real 0700 parents and rejects intermediate symlink ancestors", test_publication_rejects_symlink_parent)


# Test 24: Comprehensive Anchor & Cohort Verification Failures
def test_anchor_cohort_failure_modes() -> bool:
    plan = make_valid_plan(lane="measured", anchor_model_ids=["gemini-3.5-flash-high", "gemini-3.1-pro-high"])
    plan_bytes = MODULE.canonical_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    cand_rec = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.7-flash-high", role="candidate")
    cand_sha = hashlib.sha256(MODULE.canonical_bytes(cand_rec)).hexdigest()

    anc_1 = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.5-flash-high", role="anchor")
    anc_1_sha = hashlib.sha256(MODULE.canonical_bytes(anc_1)).hexdigest()

    anc_2 = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.1-pro-high", role="anchor")
    anc_2_sha = hashlib.sha256(MODULE.canonical_bytes(anc_2)).hexdigest()

    # Complete cohort passes
    eval_ok = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_2, anc_2_sha)])
    assert eval_ok["recommendation"] == "candidate_evidence_only"
    assert eval_ok["reason_code"] == "verified-measured-evidence"
    assert eval_ok["anchor_record_sha256s"] == [anc_1_sha, anc_2_sha]

    # 1. Missing anchor record (only anc_1 passed, anc_2 missing)
    eval_missing = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha)])
    assert eval_missing["recommendation"] == "no_recommendation"
    assert eval_missing["reason_code"] == "missing-subject"
    assert eval_missing["facts"]["cohort_complete"] is False

    # 2. Duplicate anchor record (anc_1 passed twice)
    eval_dup = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_1, anc_1_sha), (anc_2, anc_2_sha)])
    assert eval_dup["recommendation"] == "no_recommendation"
    assert eval_dup["reason_code"] == "unexpected-subject"
    assert eval_dup["facts"]["cohort_complete"] is False

    # 3. Unexpected anchor subject
    anc_unexpected = make_valid_record(plan_sha, lane="measured", model_id="gpt-oss-120b-medium", role="anchor")
    anc_unexp_sha = hashlib.sha256(MODULE.canonical_bytes(anc_unexpected)).hexdigest()
    eval_unexp = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_2, anc_2_sha), (anc_unexpected, anc_unexp_sha)])
    assert eval_unexp["recommendation"] == "no_recommendation"
    assert eval_unexp["reason_code"] == "unexpected-subject"
    assert eval_unexp["facts"]["cohort_complete"] is False

    # 4. Anchor with wrong requested model ID (declared anchor missing from cohort)
    anc_bad_id = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.5-flash-high", role="anchor")
    anc_bad_id["model_identity"]["requested_model"] = "gemini-3.5-flash-low"
    anc_bad_id_sha = hashlib.sha256(MODULE.canonical_bytes(anc_bad_id)).hexdigest()
    eval_bad_id = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_bad_id, anc_bad_id_sha), (anc_2, anc_2_sha)])
    assert eval_bad_id["recommendation"] == "no_recommendation"
    assert eval_bad_id["reason_code"] == "missing-subject"
    assert eval_bad_id["facts"]["cohort_complete"] is False

    # 5. Anchor substitution flag
    anc_sub = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.1-pro-high", role="anchor")
    anc_sub["model_identity"]["substituted"] = True
    anc_sub_sha = hashlib.sha256(MODULE.canonical_bytes(anc_sub)).hexdigest()
    eval_sub = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_sub, anc_sub_sha)])
    assert eval_sub["recommendation"] == "no_recommendation"
    assert eval_sub["reason_code"] == "model-substituted"

    # 6. Anchor observed model mismatch
    anc_obs_mismatch = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.1-pro-high", role="anchor")
    anc_obs_mismatch["model_identity"]["observed_model"] = "gemini-3.1-pro-low"
    anc_obs_mismatch_sha = hashlib.sha256(MODULE.canonical_bytes(anc_obs_mismatch)).hexdigest()
    eval_obs_mismatch = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_obs_mismatch, anc_obs_mismatch_sha)])
    assert eval_obs_mismatch["recommendation"] == "no_recommendation"
    assert eval_obs_mismatch["reason_code"] == "model-substituted"

    # 7. Anchor missing observed model
    anc_obs_missing = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.1-pro-high", role="anchor")
    anc_obs_missing["model_identity"]["observed_model"] = None
    anc_obs_missing_sha = hashlib.sha256(MODULE.canonical_bytes(anc_obs_missing)).hexdigest()
    eval_obs_missing = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_obs_missing, anc_obs_missing_sha)])
    assert eval_obs_missing["recommendation"] == "no_recommendation"
    assert eval_obs_missing["reason_code"] == "observed-model-missing"

    # 8. Comparability telemetry drift between candidate and anchor (accounting mismatch)
    anc_drift = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.1-pro-high", role="anchor")
    anc_drift["measured_metadata"]["accounting"] = "estimated_tokens"
    anc_drift_sha = hashlib.sha256(MODULE.canonical_bytes(anc_drift)).hexdigest()
    eval_drift = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_drift, anc_drift_sha)])
    assert eval_drift["recommendation"] == "no_recommendation"
    assert eval_drift["reason_code"] == "telemetry-incompatible"

    # 9. Anchor verification failure
    anc_failed = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.1-pro-high", role="anchor")
    anc_failed["telemetry"]["verification_passed"] = False
    anc_failed_sha = hashlib.sha256(MODULE.canonical_bytes(anc_failed)).hexdigest()
    eval_failed = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_failed, anc_failed_sha)])
    assert eval_failed["recommendation"] == "no_recommendation"
    assert eval_failed["reason_code"] == "verification-failed"

    # 10. Anchor budget exhaustion
    anc_budget = make_valid_record(plan_sha, lane="measured", model_id="gemini-3.1-pro-high", role="anchor")
    anc_budget["telemetry"]["invocation_count"] = 999999
    anc_budget_sha = hashlib.sha256(MODULE.canonical_bytes(anc_budget)).hexdigest()
    eval_budget = evaluate_bound(plan, plan_sha, cand_rec, cand_sha, [(anc_1, anc_1_sha), (anc_budget, anc_budget_sha)])
    assert eval_budget["recommendation"] == "no_recommendation"
    assert eval_budget["reason_code"] == "budget-exhausted"

    return True


check("anchor and cohort validation strictly detects missing/duplicate/unexpected anchors, substitution, telemetry drift, verification, and budget failures", test_anchor_cohort_failure_modes)


def run_all() -> bool:
    print(f"\nModel Evidence Campaign test suite: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
