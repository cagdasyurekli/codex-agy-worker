#!/usr/bin/env python3
"""Focused positive and adversarial tests for Model Intelligence v1."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "agy-worker" / "runtime" / "scripts" / "model_intelligence.py"
SPEC = importlib.util.spec_from_file_location("model_intelligence_tested", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SHIPPED_DATASET_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "compat" / "model-intelligence" / "dataset.v1.json"
EVIDENCE_SCHEMA_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "schemas" / "model-intelligence-evidence.schema.json"
ADVISORY_SCHEMA_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "schemas" / "model-intelligence-advisory.schema.json"

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


# 1. Shipped Dataset & Schema Validity
def test_shipped_dataset_validates() -> bool:
    data, digest_sha, _ = MODULE.read_json_file(SHIPPED_DATASET_PATH)
    MODULE.validate_dataset(data)
    adv = MODULE.compute_advisory(data, digest_sha, target_taxonomy="swe-bench-lite", reference_date="2026-08-25")
    # Empty baseline yields deterministic no_recommendation
    assert adv["reason_code"] == "no-comparable-models"
    assert adv["recommendation"] == "no_recommendation"
    assert adv["applied"] is False
    assert adv["dispatch_authorized"] is False
    assert adv["model_change_authorized"] is False
    assert adv["effort_change_authorized"] is False
    assert adv["acceptance_authorized"] is False
    assert adv["git_authorized"] is False
    assert adv["recommendation_only"] is True
    assert adv["token_inference_disclaimer"] == MODULE.TOKEN_DISCLAIMER
    return len(data["items"]) == 0 and digest_sha is not None


check("shipped dataset.v1.json validates cleanly and yields no_recommendation advisory", test_shipped_dataset_validates)


# 2. Strict Dataset Validation & Boundaries
def test_dataset_rejections() -> bool:
    sample_dataset = {
        "schema_version": 1,
        "kind": "agy-model-intelligence-evidence",
        "dataset_id": "test-dataset",
        "dataset_version": "1.0.0",
        "created_date": "2026-08-25",
        "freshness_window_days": 90,
        "expiry_date": "2026-11-23",
        "items": [
            {
                "id": "model-sample",
                "provenance_type": "local",
                "source_uri": "local://sample",
                "observed_date": "2026-08-20",
                "expiry_date": "2026-11-20",
                "harness": "test-harness",
                "harness_version": "1.0",
                "agy_version": "1.1.22",
                "requested_model": "gemini-3.7-flash",
                "observed_model": "gemini-3.7-flash",
                "substituted": False,
                "effort": "medium",
                "task_taxonomy": "repo-repair",
                "sample_size": 100,
                "calibration_only": True,
                "metrics": {
                    "quality_score": 90.0,
                    "latency_p50_seconds": 10.0,
                    "latency_p95_seconds": 20.0,
                    "mean_input_tokens": 1000.0,
                    "mean_output_tokens": 100.0,
                    "mean_cached_tokens": 500.0,
                    "mean_thinking_tokens": 50.0,
                },
                "telemetry_bindings": {
                    "accounting": "native-v1",
                    "tokenizer": "tok-v1",
                    "currency": "USD",
                    "cost_basis": "version_bound_list_price",
                    "estimated_cost_per_task": 0.01,
                },
                "confidence": "high",
                "limitations": [],
            }
        ],
    }

    cases: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("wrong schema_version", lambda d: d.__setitem__("schema_version", 2)),
        ("wrong kind", lambda d: d.__setitem__("kind", "invalid-kind")),
        ("missing items", lambda d: d.pop("items")),
        ("invalid created_date format", lambda d: d.__setitem__("created_date", "2026/08/25")),
        ("invalid calendar created_date", lambda d: d.__setitem__("created_date", "2026-02-31")),
        ("invalid calendar item observed_date", lambda d: d["items"][0].__setitem__("observed_date", "2026-13-45")),
        ("invalid provenance_type", lambda d: d["items"][0].__setitem__("provenance_type", "untrusted")),
        ("non-https/local source_uri", lambda d: d["items"][0].__setitem__("source_uri", "ftp://example.com")),
        ("source_uri with trailing data", lambda d: d["items"][0].__setitem__("source_uri", "local://sample\nunsafe")),
        ("negative quality_score", lambda d: d["items"][0]["metrics"].__setitem__("quality_score", -1.0)),
        ("quality_score > 100", lambda d: d["items"][0]["metrics"].__setitem__("quality_score", 101.0)),
        ("negative latency", lambda d: d["items"][0]["metrics"].__setitem__("latency_p50_seconds", -5.0)),
        ("latency exceeding bound", lambda d: d["items"][0]["metrics"].__setitem__("latency_p50_seconds", 2_000_000.0)),
        ("negative tokens", lambda d: d["items"][0]["metrics"].__setitem__("mean_input_tokens", -10.0)),
        ("tokens exceeding bound", lambda d: d["items"][0]["metrics"].__setitem__("mean_input_tokens", 2_000_000_000.0)),
        ("invalid cost_basis", lambda d: d["items"][0]["telemetry_bindings"].__setitem__("cost_basis", "inferred_savings")),
        ("duplicate item id", lambda d: d["items"].append(copy.deepcopy(d["items"][0]))),
    ]

    for label, mutator in cases:
        candidate = copy.deepcopy(sample_dataset)
        mutator(candidate)
        try:
            MODULE.validate_dataset(candidate)
        except MODULE.ModelIntelligenceError:
            continue
        print(f"FAILED to reject: {label}")
        return False

    evidence_schema = json.loads(EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8"))
    source_schema = evidence_schema["properties"]["items"]["items"]["properties"]["source_uri"]
    if re.search(source_schema["pattern"], "local://sample\n") is not None:
        print("FAILED to reject schema source_uri with terminal newline")
        return False
    return True


check("dataset validator rejects schema, bound, date, and type mutations without traceback", test_dataset_rejections)


# 3. Deterministic Fail-Closed Advisory Evaluation
def test_advisory_edge_cases() -> bool:
    base_data = {
        "schema_version": 1,
        "kind": "agy-model-intelligence-evidence",
        "dataset_id": "test-dataset",
        "dataset_version": "1.0.0",
        "created_date": "2026-08-25",
        "freshness_window_days": 90,
        "expiry_date": "2026-11-23",
        "items": [
            {
                "id": "model-1",
                "provenance_type": "local",
                "source_uri": "local://sample1",
                "observed_date": "2026-08-20",
                "expiry_date": "2026-11-20",
                "harness": "test-harness",
                "harness_version": "1.0",
                "agy_version": "1.1.22",
                "requested_model": "gemini-3.7-flash",
                "observed_model": "gemini-3.7-flash",
                "substituted": False,
                "effort": "medium",
                "task_taxonomy": "repo-repair",
                "sample_size": 100,
                "calibration_only": False,
                "metrics": {
                    "quality_score": 90.0,
                    "latency_p50_seconds": 10.0,
                    "latency_p95_seconds": 20.0,
                    "mean_input_tokens": 1000.0,
                    "mean_output_tokens": 100.0,
                    "mean_cached_tokens": 500.0,
                    "mean_thinking_tokens": 50.0,
                },
                "telemetry_bindings": {
                    "accounting": "native-v1",
                    "tokenizer": "tok-v1",
                    "currency": "USD",
                    "cost_basis": "version_bound_list_price",
                    "estimated_cost_per_task": 0.01,
                },
                "confidence": "high",
                "limitations": [],
            },
            {
                "id": "model-2",
                "provenance_type": "local",
                "source_uri": "local://sample2",
                "observed_date": "2026-08-20",
                "expiry_date": "2026-11-20",
                "harness": "test-harness",
                "harness_version": "1.0",
                "agy_version": "1.1.22",
                "requested_model": "gemini-3.1-pro",
                "observed_model": "gemini-3.1-pro",
                "substituted": False,
                "effort": "high",
                "task_taxonomy": "repo-repair",
                "sample_size": 100,
                "calibration_only": False,
                "metrics": {
                    "quality_score": 85.0,
                    "latency_p50_seconds": 15.0,
                    "latency_p95_seconds": 30.0,
                    "mean_input_tokens": 1200.0,
                    "mean_output_tokens": 150.0,
                    "mean_cached_tokens": 500.0,
                    "mean_thinking_tokens": 100.0,
                },
                "telemetry_bindings": {
                    "accounting": "native-v1",
                    "tokenizer": "tok-v1",
                    "currency": "USD",
                    "cost_basis": "version_bound_list_price",
                    "estimated_cost_per_task": 0.02,
                },
                "confidence": "high",
                "limitations": [],
            }
        ],
    }
    digest_sha = "0" * 64

    # A. Expired evidence via root expiry
    adv_root_exp = MODULE.compute_advisory(base_data, digest_sha, reference_date="2026-12-01")
    assert adv_root_exp["reason_code"] == "expired-evidence"
    assert adv_root_exp["recommendation"] == "no_recommendation"

    # B. Stale evidence via freshness window
    stale_data = copy.deepcopy(base_data)
    stale_data["created_date"] = "2026-01-01"
    stale_data["freshness_window_days"] = 30
    stale_data["expiry_date"] = "2026-12-31"
    for it in stale_data["items"]:
        it["observed_date"] = "2026-01-01"
        it["expiry_date"] = "2026-12-31"
    adv_stale = MODULE.compute_advisory(stale_data, digest_sha, reference_date="2026-08-25")
    assert adv_stale["reason_code"] == "stale-evidence"
    assert adv_stale["recommendation"] == "no_recommendation"

    # C. Incomparable harness
    incomp_harness = copy.deepcopy(base_data)
    incomp_harness["items"][0]["harness"] = "other-harness-v2"
    adv_harness = MODULE.compute_advisory(incomp_harness, digest_sha, reference_date="2026-08-25")
    assert adv_harness["reason_code"] == "incomparable-harness"
    assert adv_harness["recommendation"] == "no_recommendation"

    # D. Incomparable agy version
    incomp_agy = copy.deepcopy(base_data)
    incomp_agy["items"][0]["agy_version"] = "1.1.16"
    adv_agy = MODULE.compute_advisory(incomp_agy, digest_sha, reference_date="2026-08-25")
    assert adv_agy["reason_code"] == "incomparable-agy-version"
    assert adv_agy["recommendation"] == "no_recommendation"

    # E. Incomparable accounting telemetry
    incomp_acc = copy.deepcopy(base_data)
    incomp_acc["items"][0]["telemetry_bindings"]["accounting"] = "other-accounting-v2"
    adv_acc = MODULE.compute_advisory(incomp_acc, digest_sha, reference_date="2026-08-25")
    assert adv_acc["reason_code"] == "incomparable-accounting"
    assert adv_acc["recommendation"] == "no_recommendation"

    # F. Incomparable tokenizer telemetry
    incomp_tok = copy.deepcopy(base_data)
    incomp_tok["items"][0]["telemetry_bindings"]["tokenizer"] = "other-tokenizer-v2"
    adv_tok = MODULE.compute_advisory(incomp_tok, digest_sha, reference_date="2026-08-25")
    assert adv_tok["reason_code"] == "incomparable-tokenizer"
    assert adv_tok["recommendation"] == "no_recommendation"

    # G. Incomparable cost basis
    incomp_cost = copy.deepcopy(base_data)
    incomp_cost["items"][0]["telemetry_bindings"]["cost_basis"] = "observed_billed"
    incomp_cost["items"][1]["telemetry_bindings"]["cost_basis"] = "version_bound_list_price"
    adv_cost = MODULE.compute_advisory(incomp_cost, digest_sha, reference_date="2026-08-25")
    assert adv_cost["reason_code"] == "incomparable-cost-basis"
    assert adv_cost["recommendation"] == "no_recommendation"

    # H. Incomplete metrics
    incomp_metrics = copy.deepcopy(base_data)
    incomp_metrics["items"][0]["metrics"]["mean_input_tokens"] = None
    adv_metrics = MODULE.compute_advisory(incomp_metrics, digest_sha, reference_date="2026-08-25")
    assert adv_metrics["reason_code"] == "incomplete-evidence"
    assert adv_metrics["recommendation"] == "no_recommendation"

    # I. Unknown taxonomy
    adv_tax = MODULE.compute_advisory(base_data, digest_sha, target_taxonomy="unknown-task", reference_date="2026-08-25")
    assert adv_tax["reason_code"] == "incomparable-taxonomy"

    try:
        MODULE.compute_advisory(
            base_data,
            digest_sha,
            target_taxonomy="x" * 101,
            reference_date="2026-08-25",
        )
    except MODULE.ModelIntelligenceError:
        pass
    else:
        raise AssertionError("overlong target taxonomy must fail before advisory emission")
    assert adv_tax["recommendation"] == "no_recommendation"

    # J. Invalid calendar reference date
    try:
        MODULE.compute_advisory(base_data, digest_sha, reference_date="2026-02-31")
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    return True


check("advisory evaluator handles freshness, comparability, completeness, and invalid dates fail-closed", test_advisory_edge_cases)


# 4. Strictly Dominant Single-Winner vs Pareto Frontier
def test_strictly_dominant_pareto() -> bool:
    dominant_dataset = {
        "schema_version": 1,
        "kind": "agy-model-intelligence-evidence",
        "dataset_id": "test-dominant",
        "dataset_version": "1.0.0",
        "created_date": "2026-08-25",
        "freshness_window_days": 90,
        "expiry_date": "2026-11-23",
        "items": [
            {
                "id": "model-superior",
                "provenance_type": "independent",
                "source_uri": "https://example.com/superior",
                "observed_date": "2026-08-20",
                "expiry_date": "2026-11-20",
                "harness": "test-harness",
                "harness_version": "1.0",
                "agy_version": "1.1.22",
                "requested_model": "gemini-3.7-flash",
                "observed_model": "gemini-3.7-flash",
                "substituted": False,
                "effort": "medium",
                "task_taxonomy": "repo-repair",
                "sample_size": 100,
                "calibration_only": False,
                "metrics": {
                    "quality_score": 90.0,
                    "latency_p50_seconds": 10.0,
                    "latency_p95_seconds": 20.0,
                    "mean_input_tokens": 1000.0,
                    "mean_output_tokens": 100.0,
                    "mean_cached_tokens": 500.0,
                    "mean_thinking_tokens": 50.0,
                },
                "telemetry_bindings": {
                    "accounting": "native-v1",
                    "tokenizer": "tok-v1",
                    "currency": "USD",
                    "cost_basis": "version_bound_list_price",
                    "estimated_cost_per_task": 0.01,
                },
                "confidence": "high",
                "limitations": [],
            },
            {
                "id": "model-inferior",
                "provenance_type": "independent",
                "source_uri": "https://example.com/inferior",
                "observed_date": "2026-08-20",
                "expiry_date": "2026-11-20",
                "harness": "test-harness",
                "harness_version": "1.0",
                "agy_version": "1.1.22",
                "requested_model": "gemini-3.1-pro",
                "observed_model": "gemini-3.1-pro",
                "substituted": False,
                "effort": "high",
                "task_taxonomy": "repo-repair",
                "sample_size": 100,
                "calibration_only": False,
                "metrics": {
                    "quality_score": 60.0,
                    "latency_p50_seconds": 50.0,
                    "latency_p95_seconds": 100.0,
                    "mean_input_tokens": 5000.0,
                    "mean_output_tokens": 500.0,
                    "mean_cached_tokens": 500.0,
                    "mean_thinking_tokens": 200.0,
                },
                "telemetry_bindings": {
                    "accounting": "native-v1",
                    "tokenizer": "tok-v1",
                    "currency": "USD",
                    "cost_basis": "version_bound_list_price",
                    "estimated_cost_per_task": 0.05,
                },
                "confidence": "high",
                "limitations": [],
            },
        ],
    }

    adv = MODULE.compute_advisory(dominant_dataset, "0" * 64, target_taxonomy="repo-repair", reference_date="2026-08-25")
    assert adv["reason_code"] == "pareto-dominant"
    assert adv["recommendation"] == "gemini-3.7-flash:medium"
    assert len(adv["pareto_frontier"]) == 1
    assert adv["pareto_frontier"][0]["id"] == "model-superior"
    assert adv["applied"] is False
    assert adv["dispatch_authorized"] is False
    return True


check("strictly dominant candidate resolves to pareto-dominant recommendation with zero authority", test_strictly_dominant_pareto)


# 5. Issue #78 Canonical Five-Arm Full Hash-Chain Study Import & Negative Mutations
def test_issue_78_canonical_five_arm_study_import() -> bool:
    def make_5_arm_cells(task: str) -> list[dict[str, Any]]:
        return [
            {
                "arm": "codex-only",
                "failure_class": "none",
                "evaluator_resolved": True,
                "clean_driver_gate": True,
                "independent_diff_acceptance": True,
                "exact_bindings_verified": True,
                "accepted_solution": True,
                "repair_count": 0,
                "wall_time_seconds": 30.0,
                "codex_usage": {"input": 1000, "cached_input": 200, "fresh_input": 800, "cache_write": None, "output": 100, "reasoning_output": 50},
                "codex_cost": {"observed_billed": 0.002, "version_bound_list_price": 0.002},
                "agy_usage": {"input": 0, "cached_input": 0, "fresh_input": 0, "cache_write": None, "output": 0, "reasoning_output": 0},
                "agy_cost": {"observed_billed": 0.0, "version_bound_list_price": 0.0},
            },
            {
                "arm": "agy-explore-first",
                "failure_class": "none",
                "evaluator_resolved": True,
                "clean_driver_gate": True,
                "independent_diff_acceptance": True,
                "exact_bindings_verified": True,
                "accepted_solution": True,
                "repair_count": 0,
                "wall_time_seconds": 40.0,
                "codex_usage": {"input": 200, "cached_input": 0, "fresh_input": 200, "cache_write": None, "output": 50, "reasoning_output": 20},
                "codex_cost": {"observed_billed": 0.001, "version_bound_list_price": 0.001},
                "agy_usage": {"input": 10000, "cached_input": 2000, "fresh_input": 8000, "cache_write": None, "output": 500, "reasoning_output": 200},
                "agy_cost": {"observed_billed": 0.005, "version_bound_list_price": 0.005},
            },
            {
                "arm": "agy-task-first",
                "failure_class": "none",
                "evaluator_resolved": True,
                "clean_driver_gate": True,
                "independent_diff_acceptance": True,
                "exact_bindings_verified": True,
                "accepted_solution": True,
                "repair_count": 0,
                "wall_time_seconds": 45.0,
                "codex_usage": {"input": 200, "cached_input": 0, "fresh_input": 200, "cache_write": None, "output": 50, "reasoning_output": 20},
                "codex_cost": {"observed_billed": 0.001, "version_bound_list_price": 0.001},
                "agy_usage": {"input": 12000, "cached_input": 2000, "fresh_input": 10000, "cache_write": None, "output": 800, "reasoning_output": 300},
                "agy_cost": {"observed_billed": 0.006, "version_bound_list_price": 0.006},
            },
            {
                "arm": "agy-project-first",
                "failure_class": "none",
                "evaluator_resolved": True,
                "clean_driver_gate": True,
                "independent_diff_acceptance": True,
                "exact_bindings_verified": True,
                "accepted_solution": True,
                "repair_count": 1,
                "wall_time_seconds": 60.0,
                "codex_usage": {"input": 300, "cached_input": 0, "fresh_input": 300, "cache_write": None, "output": 80, "reasoning_output": 30},
                "codex_cost": {"observed_billed": 0.0015, "version_bound_list_price": 0.0015},
                "agy_usage": {"input": 15000, "cached_input": 3000, "fresh_input": 12000, "cache_write": None, "output": 1000, "reasoning_output": 400},
                "agy_cost": {"observed_billed": 0.008, "version_bound_list_price": 0.008},
            },
            {
                "arm": "second-eye",
                "failure_class": "none",
                "evaluator_resolved": True,
                "clean_driver_gate": True,
                "independent_diff_acceptance": True,
                "exact_bindings_verified": True,
                "accepted_solution": True,
                "repair_count": 0,
                "wall_time_seconds": 50.0,
                "codex_usage": {"input": 1000, "cached_input": 200, "fresh_input": 800, "cache_write": None, "output": 100, "reasoning_output": 50},
                "codex_cost": {"observed_billed": 0.002, "version_bound_list_price": 0.002},
                "agy_usage": {"input": 8000, "cached_input": 1000, "fresh_input": 7000, "cache_write": None, "output": 400, "reasoning_output": 150},
                "agy_cost": {"observed_billed": 0.004, "version_bound_list_price": 0.004},
            },
        ]

    plan_base = {
        "schema_version": 1,
        "kind": "agy-swebench-workflow-study-plan",
        "dataset_revision": "study-v1",
        "evaluator_revision": "eval-v1",
        "permissions_policy": "read-only",
        "network_policy": "denied",
        "codex_model": "gpt-5.6-terra",
        "codex_effort": "medium",
        "agy_model": "gemini-3.7-flash",
        "agy_effort": "medium",
        "tasks": ["django__django-11001", "django__django-11002"],
        "budgets": {
            "max_tasks": 2,
            "max_repairs_per_cell": 2,
            "max_wall_time_seconds_per_cell": 300.0,
            "max_codex_tokens_per_cell": 100000,
            "max_agy_tokens_per_cell": 100000,
            "max_observed_billed_cost_per_cell": 1.0,
            "max_version_bound_list_price_cost_per_cell": 1.0,
        },
        "telemetry_bindings": {
            "codex": {
                "accounting": "provider-native-v1",
                "tokenizer": "cl100k_base",
                "currency": "USD",
                "price_source": "version_bound_list_price",
            },
            "agy": {
                "accounting": "provider-native-v1",
                "tokenizer": "sentencepiece-v2",
                "currency": "USD",
                "price_source": "version_bound_list_price",
            },
        },
        "repository_base": "1" * 64,
        "repository_image": "2" * 64,
        "frozen_prompt_digest": "0" * 64,
        "arms": ["codex-only", "agy-explore-first", "agy-task-first", "agy-project-first", "second-eye"],
        "ordering": "task_then_arm",
        "aggregation": "input-plus-output-no-overlap-v1",
    }
    plan_content_sha = hashlib.sha256(MODULE.canonical_bytes(plan_base)).hexdigest()
    plan_data = dict(plan_base)
    plan_data["plan_content_sha256"] = plan_content_sha
    plan_raw = MODULE.canonical_bytes(plan_data)
    plan_sha = hashlib.sha256(plan_raw).hexdigest()

    records_data = [
        {"task_commitment": "django__django-11001", "cells": make_5_arm_cells("django__django-11001")},
        {"task_commitment": "django__django-11002", "cells": make_5_arm_cells("django__django-11002")},
    ]
    imported_results_raw = MODULE.canonical_bytes({
        "schema_version": 1,
        "kind": "agy-swebench-workflow-study-import",
        "plan_sha256": plan_sha,
        "exact_bindings_verified": True,
        "records": records_data,
    })
    imported_results_sha = hashlib.sha256(imported_results_raw).hexdigest()

    report_data = {
        "schema_version": 1,
        "kind": "agy-swebench-workflow-study-report",
        "plan_sha256": plan_sha,
        "imported_results_sha256": imported_results_sha,
        "exact_bindings_verified": True,
        "plan": plan_data,
        "records": records_data,
        "denominators": {
            "planned_tasks": 2,
            "planned_cells": 10,
            "accepted_solutions": 10,
        },
    }

    with tempfile.TemporaryDirectory() as td:
        plan_file = Path(td) / "plan.json"
        report_file = Path(td) / "report.json"
        results_file = Path(td) / "imported_results.json"

        plan_file.write_bytes(plan_raw)
        report_file.write_text(json.dumps(report_data), encoding="utf-8")
        results_file.write_bytes(imported_results_raw)

        # 1. Positive: valid 5-arm study import
        imported = MODULE.import_study_report(report_file, plan_file, results_file)
        assert imported["schema_version"] == 1
        assert imported["kind"] == "agy-model-intelligence-evidence"
        assert len(imported["items"]) == 1
        item = imported["items"][0]
        assert item["harness"] == "swebench-workflow-study-v1"
        assert item["requested_model"] == "gemini-3.7-flash"
        assert item["effort"] == "medium"
        assert item["sample_size"] == 2
        assert item["calibration_only"] is True
        assert item["agy_version"] is None
        assert item["observed_model"] is None
        assert item["substituted"] is None
        assert item["confidence"] is None
        assert all(value is None for value in item["metrics"].values())
        assert item["telemetry_bindings"]["cost_basis"] is None
        assert item["telemetry_bindings"]["estimated_cost_per_task"] is None
        advisory = MODULE.compute_advisory(
            imported,
            hashlib.sha256(MODULE.canonical_bytes(imported)).hexdigest(),
            target_taxonomy="swebench-study-v1",
            reference_date=imported["created_date"],
        )
        assert advisory["reason_code"] == "calibration-only"
        assert advisory["recommendation"] == "no_recommendation"
        assert advisory["pareto_frontier"] == []

        # 2. Negative: Tampered plan SHA in report
        bad_report = dict(report_data)
        bad_report["plan_sha256"] = "f" * 64
        bad_report_file = Path(td) / "bad_report.json"
        bad_report_file.write_text(json.dumps(bad_report), encoding="utf-8")
        try:
            MODULE.import_study_report(bad_report_file, plan_file, results_file)
        except MODULE.ModelIntelligenceError:
            pass
        else:
            return False

        # 3. Negative: Tampered imported results SHA in report
        bad_report2 = dict(report_data)
        bad_report2["imported_results_sha256"] = "f" * 64
        bad_report2_file = Path(td) / "bad_report2.json"
        bad_report2_file.write_text(json.dumps(bad_report2), encoding="utf-8")
        try:
            MODULE.import_study_report(bad_report2_file, plan_file, results_file)
        except MODULE.ModelIntelligenceError:
            pass
        else:
            return False

    return True


check("issue-78 study import validates canonical five-arm chain and rejects hash mutations fail-closed", test_issue_78_canonical_five_arm_study_import)


# 6. Benchmark Review Due on Inventory Additions, Removals, and Semantic Binding Changes
def test_benchmark_review_inventory_changes() -> bool:
    base_inv = {
        "schema_version": 1,
        "status": "accepted-current-inventory",
        "agy_version": "1.1.22",
        "reviewed_source_revision": "556846a4bb94117222f53846896c7eb0d645307e",
        "source_sha256": "7b1317779085913d338bde0e9b39b72323d9083a879525f944fd469c8ecca906",
        "version_binding_sha256": "d9d830e65d3a5c76df6d9e07e6ea7e14e14f290ab4036bdbae8cb33502e29f2a",
        "slugs": ["gemini-3.7-flash-medium", "claude-sonnet-4-6", "gpt-oss-120b-medium"],
    }

    # A. Model addition: unchanged common models must NOT be marked binding-changed
    cand_add = copy.deepcopy(base_inv)
    cand_add["slugs"].append("gemini-3.8-flash-high")
    rev_add = MODULE.track_benchmark_review(
        baseline_inventory=base_inv,
        candidate_inventory=cand_add,
        reference_date="2026-08-30",
    )
    assert rev_add["status"] == "benchmark-review-due"
    assert rev_add["kind"] == "agy-benchmark-review-tracker"
    assert rev_add["maintainer_disposition"] is None
    assert rev_add["applied"] is False
    assert rev_add["dispatch_authorized"] is False
    assert rev_add["model_change_authorized"] is False
    assert rev_add["git_authorized"] is False
    assert rev_add["benchmark_run_authorized"] is False
    assert len(rev_add["reviews_due"]) == 1
    assert rev_add["reviews_due"][0] == {"model_id": "gemini-3.8-flash-high", "evidence_state": "inventory-added"}

    # B. Model removal: unchanged common models must NOT be marked binding-changed
    cand_rem = copy.deepcopy(base_inv)
    cand_rem["slugs"].remove("gpt-oss-120b-medium")
    rev_rem = MODULE.track_benchmark_review(
        baseline_inventory=base_inv,
        candidate_inventory=cand_rem,
        reference_date="2026-08-30",
    )
    assert rev_rem["status"] == "benchmark-review-due"
    assert len(rev_rem["reviews_due"]) == 1
    assert rev_rem["reviews_due"][0] == {"model_id": "gpt-oss-120b-medium", "evidence_state": "inventory-removed"}

    # C. Global inventory digest changes must NOT mark common slugs binding-changed
    cand_digest = copy.deepcopy(base_inv)
    cand_digest["version_binding_sha256"] = "1" * 64
    cand_digest["source_sha256"] = "2" * 64
    rev_digest = MODULE.track_benchmark_review(
        baseline_inventory=base_inv,
        candidate_inventory=cand_digest,
        reference_date="2026-08-30",
    )
    assert rev_digest["status"] == "unchanged"
    assert rev_digest["reviews_due"] == []

    # D. Semantic per-model mapping changes in matrix (e.g. unsupported_efforts change)
    base_mat = {
        "schema_version": 1,
        "resolution_status": "active",
        "inventory": {"agy_version": "1.1.22", "reviewed_source_revision": "556846a4bb94117222f53846896c7eb0d645307e"},
        "adjustable_models": [
            {
                "model": "gemini-3.7-flash",
                "resolutions": {"low": "gemini-3.7-flash-low", "medium": "gemini-3.7-flash-medium"},
                "unsupported_efforts": ["high"],
            }
        ],
        "fixed_models": [{"model_slug": "claude-sonnet-4-6"}],
    }
    cand_mat_binding_change = copy.deepcopy(base_mat)
    cand_mat_binding_change["adjustable_models"][0]["unsupported_efforts"] = []
    rev_mat_binding = MODULE.track_benchmark_review(
        baseline_matrix=base_mat,
        candidate_matrix=cand_mat_binding_change,
        reference_date="2026-08-30",
    )
    assert rev_mat_binding["status"] == "benchmark-review-due"
    assert len(rev_mat_binding["reviews_due"]) == 2
    assert rev_mat_binding["reviews_due"] == [
        {"model_id": "gemini-3.7-flash-low", "evidence_state": "binding-changed"},
        {"model_id": "gemini-3.7-flash-medium", "evidence_state": "binding-changed"},
    ]

    # E. Matrix-based comparison with modified effort resolution addition
    cand_mat_add = copy.deepcopy(base_mat)
    cand_mat_add["adjustable_models"][0]["resolutions"]["high"] = "gemini-3.7-flash-high"
    rev_mat_add = MODULE.track_benchmark_review(
        baseline_matrix=base_mat,
        candidate_matrix=cand_mat_add,
        reference_date="2026-08-30",
    )
    assert rev_mat_add["status"] == "benchmark-review-due"
    assert len(rev_mat_add["reviews_due"]) == 1
    assert rev_mat_add["reviews_due"][0] == {"model_id": "gemini-3.7-flash-high", "evidence_state": "inventory-added"}
    return True


check("inventory additions, removals, and binding changes yield bounded benchmark-review-due facts", test_benchmark_review_inventory_changes)


# 7. Unchanged Inventory Yields No Review-Due Facts
def test_benchmark_review_unchanged_inventory() -> bool:
    inv = {
        "schema_version": 1,
        "status": "accepted-current-inventory",
        "agy_version": "1.1.22",
        "reviewed_source_revision": "556846a4bb94117222f53846896c7eb0d645307e",
        "source_sha256": "7b1317779085913d338bde0e9b39b72323d9083a879525f944fd469c8ecca906",
        "version_binding_sha256": "d9d830e65d3a5c76df6d9e07e6ea7e14e14f290ab4036bdbae8cb33502e29f2a",
        "slugs": ["gemini-3.7-flash-medium", "claude-sonnet-4-6"],
    }
    dataset = {
        "schema_version": 1,
        "kind": "agy-model-intelligence-evidence",
        "dataset_id": "test-clean",
        "dataset_version": "1.0.0",
        "created_date": "2026-08-25",
        "freshness_window_days": 90,
        "expiry_date": "2026-11-23",
        "items": [],
    }
    rev = MODULE.track_benchmark_review(
        dataset=dataset,
        baseline_inventory=inv,
        candidate_inventory=copy.deepcopy(inv),
        reference_date="2026-08-30",
    )
    assert rev["status"] == "unchanged"
    assert rev["reviews_due"] == []
    assert rev["maintainer_disposition"] is None
    assert rev["applied"] is False
    assert rev["dispatch_authorized"] is False
    assert rev["git_authorized"] is False

    # Setting a disposition when nothing is due must fail closed
    try:
        MODULE.track_benchmark_review(
            dataset=dataset,
            baseline_inventory=inv,
            candidate_inventory=copy.deepcopy(inv),
            reference_date="2026-08-30",
            maintainer_disposition="collect",
        )
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    return True


check("unchanged inventory yields no model-change review-due facts", test_benchmark_review_unchanged_inventory)


# 8. Dataset Expiry Yields Review-Due Without Execution Authority
def test_benchmark_review_dataset_expiry() -> bool:
    inv = {
        "schema_version": 1,
        "status": "accepted-current-inventory",
        "slugs": ["gemini-3.7-flash-medium"],
    }
    dataset = {
        "schema_version": 1,
        "kind": "agy-model-intelligence-evidence",
        "dataset_id": "test-exp",
        "dataset_version": "1.0.0",
        "created_date": "2026-08-01",
        "freshness_window_days": 90,
        "expiry_date": "2026-08-20",
        "items": [
            {
                "id": "sample-1",
                "provenance_type": "local",
                "source_uri": "local://sample1",
                "observed_date": "2026-08-01",
                "expiry_date": "2026-08-20",
                "harness": "test-harness",
                "harness_version": "1.0",
                "agy_version": "1.1.22",
                "requested_model": "gemini-3.7-flash-medium",
                "observed_model": "gemini-3.7-flash-medium",
                "substituted": False,
                "effort": "medium",
                "task_taxonomy": "repo-repair",
                "sample_size": 100,
                "calibration_only": True,
                "metrics": {
                    "quality_score": 90.0,
                    "latency_p50_seconds": 10.0,
                    "latency_p95_seconds": 20.0,
                    "mean_input_tokens": 1000.0,
                    "mean_output_tokens": 100.0,
                    "mean_cached_tokens": 500.0,
                    "mean_thinking_tokens": 50.0,
                },
                "telemetry_bindings": {
                    "accounting": "native-v1",
                    "tokenizer": "tok-v1",
                    "currency": "USD",
                    "cost_basis": None,
                    "estimated_cost_per_task": None,
                },
                "confidence": None,
                "limitations": [],
            }
        ],
    }

    # Root expiry exceeded relative to 2026-08-30
    rev_exp = MODULE.track_benchmark_review(
        dataset=dataset,
        baseline_inventory=inv,
        candidate_inventory=inv,
        reference_date="2026-08-30",
    )
    assert rev_exp["status"] == "benchmark-review-due"
    assert len(rev_exp["reviews_due"]) == 1
    assert rev_exp["reviews_due"][0] == {"model_id": "gemini-3.7-flash-medium", "evidence_state": "dataset-expired"}
    assert rev_exp["applied"] is False
    assert rev_exp["dispatch_authorized"] is False
    assert rev_exp["model_change_authorized"] is False
    assert rev_exp["git_authorized"] is False
    assert rev_exp["benchmark_run_authorized"] is False
    assert rev_exp["provider_call_authorized"] is False

    # Freshness window exceeded
    fresh_data = copy.deepcopy(dataset)
    fresh_data["created_date"] = "2026-01-01"
    fresh_data["freshness_window_days"] = 30
    fresh_data["expiry_date"] = "2026-12-31"
    fresh_data["items"][0]["observed_date"] = "2026-01-01"
    fresh_data["items"][0]["expiry_date"] = "2026-12-31"
    rev_stale = MODULE.track_benchmark_review(
        dataset=fresh_data,
        baseline_inventory=inv,
        candidate_inventory=inv,
        reference_date="2026-08-30",
    )
    assert rev_stale["status"] == "benchmark-review-due"
    assert rev_stale["reviews_due"][0] == {"model_id": "gemini-3.7-flash-medium", "evidence_state": "stale-evidence"}

    # Expired empty shipped dataset: target models resolved from current reviewed inventory without comparison fallback
    shipped_data, _, _ = MODULE.read_json_file(SHIPPED_DATASET_PATH)
    rev_shipped_exp = MODULE.track_benchmark_review(
        dataset=shipped_data,
        reference_date="2027-01-01",
    )
    assert rev_shipped_exp["status"] == "benchmark-review-due"
    assert len(rev_shipped_exp["reviews_due"]) == 14
    for it in rev_shipped_exp["reviews_due"]:
        assert it["evidence_state"] == "dataset-expired"
        assert set(it.keys()) == {"model_id", "evidence_state"}

    # Missing or malformed reviewed inventory cannot turn an expired empty
    # dataset into an unchanged result.
    original_module_file = MODULE.__file__
    with tempfile.TemporaryDirectory() as td:
        fake_script = Path(td) / "runtime" / "scripts" / "model_intelligence.py"
        fake_binding = fake_script.parent.parent / "compat" / "agy-models-inventory-binding.json"
        try:
            MODULE.__file__ = str(fake_script)
            for malformed in (None, "{not-json"):
                if malformed is None:
                    fake_binding.unlink(missing_ok=True)
                else:
                    fake_binding.parent.mkdir(parents=True, exist_ok=True)
                    fake_binding.write_text(malformed, encoding="utf-8")
                try:
                    MODULE.track_benchmark_review(
                        dataset=shipped_data,
                        reference_date="2027-01-01",
                    )
                except MODULE.ModelIntelligenceError as exc:
                    assert str(exc) == "reviewed inventory evidence is unavailable"
                else:
                    return False
        finally:
            MODULE.__file__ = original_module_file

    return True


check("dataset root and item expiry yield review-due without execution authority", test_benchmark_review_dataset_expiry)


# 9. Fail-Closed Validation and Adversarial Inputs
def test_benchmark_review_fail_closed() -> bool:
    inv = {"schema_version": 1, "status": "accepted", "slugs": ["model-a"]}
    mat = {
        "schema_version": 1,
        "resolution_status": "active",
        "adjustable_models": [{"model": "model-a", "resolutions": {"low": "model-a-low"}}],
        "fixed_models": [],
    }

    # Missing reference date
    try:
        MODULE.track_benchmark_review(reference_date=None)
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Invalid calendar date
    try:
        MODULE.track_benchmark_review(reference_date="2026-02-31")
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Mixed source pairs (inventory binding vs matrix)
    try:
        MODULE.track_benchmark_review(baseline_inventory=inv, candidate_inventory=mat, reference_date="2026-08-30")
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Complete inventory and matrix pairs are ambiguous, not ordered fallbacks.
    try:
        MODULE.track_benchmark_review(
            baseline_inventory=inv,
            candidate_inventory=inv,
            baseline_matrix=mat,
            candidate_matrix=mat,
            reference_date="2026-08-30",
        )
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # A single document cannot mix inventory representations either.
    mixed_inv = copy.deepcopy(inv)
    mixed_inv.update({"adjustable_models": [], "fixed_models": []})
    try:
        MODULE.track_benchmark_review(
            baseline_inventory=inv,
            candidate_inventory=mixed_inv,
            reference_date="2026-08-30",
        )
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Duplicate slugs in inventory binding
    dup_inv = {"schema_version": 1, "status": "accepted", "slugs": ["model-a", "model-a"]}
    try:
        MODULE.track_benchmark_review(baseline_inventory=inv, candidate_inventory=dup_inv, reference_date="2026-08-30")
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Hash-unfriendly rows are rejected as model-intelligence errors, never TypeError.
    for malformed_inv in (
        {"schema_version": 1, "slugs": [{}]},
        {"schema_version": 1, "models": [{}]},
    ):
        try:
            MODULE._extract_model_inventory(malformed_inv, "malformed_inventory")
        except MODULE.ModelIntelligenceError:
            pass
        else:
            return False

    # Duplicate models in matrix
    dup_mat = {
        "schema_version": 1,
        "resolution_status": "active",
        "adjustable_models": [
            {"model": "model-a", "resolutions": {"low": "model-a-low"}},
            {"model": "model-a", "resolutions": {"low": "model-a-low2"}},
        ],
        "fixed_models": [],
    }
    try:
        MODULE.track_benchmark_review(baseline_matrix=mat, candidate_matrix=dup_mat, reference_date="2026-08-30")
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Matrix rows and derived slugs share one aggregate public bound.
    oversized_matrix = {
        "schema_version": 1,
        "adjustable_models": [],
        "fixed_models": [
            {"model_slug": f"model-{index}"}
            for index in range(MODULE.MAX_TRACKED_MODELS + 1)
        ],
    }
    try:
        MODULE.track_benchmark_review(
            baseline_matrix=mat,
            candidate_matrix=oversized_matrix,
            reference_date="2026-08-30",
        )
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Unpaired inventory baseline/candidate
    try:
        MODULE.track_benchmark_review(candidate_inventory=inv, reference_date="2026-08-30")
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    try:
        MODULE.track_benchmark_review(baseline_inventory=inv, reference_date="2026-08-30")
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Unsafe / private model slug injection
    bad_inv = {
        "schema_version": 1,
        "status": "accepted",
        "slugs": ["model\nwith-newline", "secret_token_1234567890"],
    }
    try:
        MODULE.track_benchmark_review(baseline_inventory=inv, candidate_inventory=bad_inv, reference_date="2026-08-30")
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Non-dict inventory
    try:
        MODULE.track_benchmark_review(baseline_inventory=inv, candidate_inventory=["not-a-dict"], reference_date="2026-08-30")  # type: ignore[arg-type]
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    # Output validation enforces semantic invariants, ordering, uniqueness, and
    # bounded string-only limitations in addition to exact top-level keys.
    valid_output = MODULE.track_benchmark_review(
        baseline_inventory=inv,
        candidate_inventory={"schema_version": 1, "slugs": ["model-a", "model-b"]},
        reference_date="2026-08-30",
    )
    output_mutations = []

    wrong_status = copy.deepcopy(valid_output)
    wrong_status["status"] = "unchanged"
    output_mutations.append(wrong_status)

    duplicate_review = copy.deepcopy(valid_output)
    duplicate_review["reviews_due"].append(copy.deepcopy(duplicate_review["reviews_due"][0]))
    output_mutations.append(duplicate_review)

    extra_key = copy.deepcopy(valid_output)
    extra_key["unexpected"] = False
    output_mutations.append(extra_key)

    invalid_limitations = copy.deepcopy(valid_output)
    invalid_limitations["limitations"] = [{}]
    output_mutations.append(invalid_limitations)

    unordered_reviews = MODULE.track_benchmark_review(
        baseline_inventory=inv,
        candidate_inventory={"schema_version": 1, "slugs": ["model-a", "model-b", "model-c"]},
        reference_date="2026-08-30",
    )
    unordered_reviews["reviews_due"].reverse()
    output_mutations.append(unordered_reviews)

    unbounded_reviews = copy.deepcopy(valid_output)
    unbounded_reviews["reviews_due"] = [
        {"model_id": f"model-{index}", "evidence_state": "inventory-added"}
        for index in range(MODULE.MAX_TRACKED_MODELS + 1)
    ]
    output_mutations.append(unbounded_reviews)

    for mutated_output in output_mutations:
        try:
            MODULE.validate_benchmark_review_output(mutated_output)
        except MODULE.ModelIntelligenceError:
            pass
        else:
            return False

    return True


check("malformed, stale, private, and oversized inputs fail closed with sanitized error output", test_benchmark_review_fail_closed)


# 10. Explicit Maintainer Dispositions
def test_benchmark_review_maintainer_dispositions() -> bool:
    inv_add = {
        "schema_version": 1,
        "status": "accepted",
        "slugs": ["gemini-3.7-flash-medium", "new-model"],
    }
    inv_base = {
        "schema_version": 1,
        "status": "accepted",
        "slugs": ["gemini-3.7-flash-medium"],
    }

    # Default is null (unassigned)
    rev_def = MODULE.track_benchmark_review(
        baseline_inventory=inv_base, candidate_inventory=inv_add, reference_date="2026-08-30"
    )
    assert rev_def["maintainer_disposition"] is None

    # Explicit collect
    rev_collect = MODULE.track_benchmark_review(
        baseline_inventory=inv_base, candidate_inventory=inv_add, reference_date="2026-08-30",
        maintainer_disposition="collect",
    )
    assert rev_collect["maintainer_disposition"] == "collect"

    # Explicit defer
    rev_defer = MODULE.track_benchmark_review(
        baseline_inventory=inv_base, candidate_inventory=inv_add, reference_date="2026-08-30",
        maintainer_disposition="defer",
    )
    assert rev_defer["maintainer_disposition"] == "defer"

    # Explicit not-applicable
    rev_na = MODULE.track_benchmark_review(
        baseline_inventory=inv_base, candidate_inventory=inv_add, reference_date="2026-08-30",
        maintainer_disposition="not-applicable",
    )
    assert rev_na["maintainer_disposition"] == "not-applicable"

    # Unauthorized automatic or invalid disposition rejected
    for invalid in ("auto", "automatic", "accept", "proceed", "unreviewed", "collect; rm -rf"):
        try:
            MODULE.track_benchmark_review(
                baseline_inventory=inv_base, candidate_inventory=inv_add, reference_date="2026-08-30",
                maintainer_disposition=invalid,
            )
        except MODULE.ModelIntelligenceError:
            pass
        else:
            return False

    return True


check("maintainer dispositions are explicit only and never chosen automatically", test_benchmark_review_maintainer_dispositions)


# 11. Deterministic Reviewed-Inventory Workflow Integration Test
def test_reviewed_inventory_workflow_integration() -> bool:
    base_inv = {
        "schema_version": 1,
        "status": "accepted-current-inventory",
        "agy_version": "1.1.22",
        "reviewed_source_revision": "556846a4bb94117222f53846896c7eb0d645307e",
        "slugs": ["gemini-3.7-flash-medium", "claude-sonnet-4-6"],
    }
    cand_inv = {
        "schema_version": 1,
        "status": "accepted-current-inventory",
        "agy_version": "1.1.22",
        "reviewed_source_revision": "556846a4bb94117222f53846896c7eb0d645307e",
        "slugs": ["gemini-3.7-flash-medium", "claude-sonnet-4-6", "gemini-3.8-flash-high"],
    }

    # Step 1: Detect newly added model
    review_due_result = MODULE.track_benchmark_review(
        baseline_inventory=base_inv,
        candidate_inventory=cand_inv,
        reference_date="2026-08-30",
    )
    assert review_due_result["status"] == "benchmark-review-due"
    assert review_due_result["reviews_due"] == [
        {"model_id": "gemini-3.8-flash-high", "evidence_state": "inventory-added"}
    ]
    assert review_due_result["maintainer_disposition"] is None
    assert review_due_result["applied"] is False
    assert review_due_result["dispatch_authorized"] is False
    assert review_due_result["git_authorized"] is False

    # Step 2: Maintainer explicitly records collect disposition
    review_collect = MODULE.track_benchmark_review(
        baseline_inventory=base_inv,
        candidate_inventory=cand_inv,
        reference_date="2026-08-30",
        maintainer_disposition="collect",
    )
    assert review_collect["status"] == "benchmark-review-due"
    assert review_collect["maintainer_disposition"] == "collect"

    # Step 3: Once review is complete and candidate becomes new baseline
    post_review_result = MODULE.track_benchmark_review(
        baseline_inventory=cand_inv,
        candidate_inventory=copy.deepcopy(cand_inv),
        reference_date="2026-08-30",
    )
    assert post_review_result["status"] == "unchanged"
    assert post_review_result["reviews_due"] == []
    assert post_review_result["maintainer_disposition"] is None

    # Step 4: Verify disposition on unchanged state is rejected
    try:
        MODULE.track_benchmark_review(
            baseline_inventory=cand_inv,
            candidate_inventory=cand_inv,
            reference_date="2026-08-30",
            maintainer_disposition="collect",
        )
    except MODULE.ModelIntelligenceError:
        pass
    else:
        return False

    return True


check("deterministic reviewed-inventory workflow integration succeeds with zero execution authority", test_reviewed_inventory_workflow_integration)


# 12. CLI Subcommands for Benchmark Review
def test_cli_subcommands() -> bool:
    # validate subcommand
    res_val = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "validate", f"--dataset={SHIPPED_DATASET_PATH}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert res_val.returncode == 0, res_val.stderr
    out_val = json.loads(res_val.stdout)
    assert out_val["status"] == "valid"
    assert out_val["item_count"] == 0

    # advise subcommand with reference date
    res_adv = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "advise", f"--dataset={SHIPPED_DATASET_PATH}", "--reference-date=2026-08-25"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert res_adv.returncode == 0, res_adv.stderr
    out_adv = json.loads(res_adv.stdout)
    assert out_adv["kind"] == "agy-model-intelligence-advisory"
    assert out_adv["applied"] is False
    assert out_adv["dispatch_authorized"] is False

    # advise subcommand without reference date must fail closed with exit 2
    res_adv_no_date = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "advise", f"--dataset={SHIPPED_DATASET_PATH}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert res_adv_no_date.returncode == 2

    # benchmark-review subcommand with reference date
    res_rev = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "benchmark-review", f"--dataset={SHIPPED_DATASET_PATH}", "--reference-date=2026-08-25"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert res_rev.returncode == 0, res_rev.stderr
    out_rev = json.loads(res_rev.stdout)
    assert out_rev["kind"] == "agy-benchmark-review-tracker"
    assert out_rev["status"] == "unchanged"
    assert out_rev["maintainer_disposition"] is None
    assert out_rev["applied"] is False

    # benchmark-review without reference date must fail with exit 2
    res_rev_no_date = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "benchmark-review", f"--dataset={SHIPPED_DATASET_PATH}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert res_rev_no_date.returncode == 2

    # Malformed inventory rows fail at the sanitized CLI boundary without a
    # Python traceback, including the legacy models-list representation.
    with tempfile.TemporaryDirectory() as td:
        base_inventory = Path(td) / "base.json"
        candidate_inventory = Path(td) / "candidate.json"
        for baseline_data, candidate_data in (
            (
                {"schema_version": 1, "slugs": ["model-a"]},
                {"schema_version": 1, "slugs": [{}]},
            ),
            (
                {"models": ["model-a"]},
                {"models": [{}]},
            ),
        ):
            base_inventory.write_text(json.dumps(baseline_data), encoding="utf-8")
            candidate_inventory.write_text(json.dumps(candidate_data), encoding="utf-8")
            malformed_result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    str(SCRIPT),
                    "benchmark-review",
                    f"--baseline-inventory={base_inventory}",
                    f"--candidate-inventory={candidate_inventory}",
                    "--reference-date=2026-08-30",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            assert malformed_result.returncode == 1
            assert malformed_result.stdout == b""
            assert malformed_result.stderr == b"model-intelligence benchmark-review error: operation failed closed\n"
            assert b"Traceback" not in malformed_result.stderr

        private_output = Path(td) / "private-sensitive-parent" / "missing" / "review.json"
        output_failure = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(SCRIPT),
                "benchmark-review",
                f"--dataset={SHIPPED_DATASET_PATH}",
                "--reference-date=2026-08-25",
                f"--out={private_output}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert output_failure.returncode == 1
        assert output_failure.stdout == b""
        assert output_failure.stderr == b"model-intelligence benchmark-review error: operation failed closed\n"
        assert b"Traceback" not in output_failure.stderr
        assert os.fsencode(private_output) not in output_failure.stderr
        assert not private_output.exists()

    # track-review alias is removed and must be rejected as unknown subcommand (exit 2)
    res_alias = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "track-review", f"--dataset={SHIPPED_DATASET_PATH}", "--reference-date=2026-08-25"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert res_alias.returncode == 2

    # rejected arguments
    res_bad = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "--invalid-arg"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert res_bad.returncode == 2

    return True


check("CLI interface supports validate, advise, import-study, benchmark-review, and enforces reference date", test_cli_subcommands)

print()
print(f"PASSED: {passed} tests")
if failed:
    print(f"FAILED: {failed} tests")
    raise SystemExit(1)
