#!/usr/bin/env python3
"""Offline test suite for the SWE-bench workflow study v1 tool."""

from __future__ import annotations

import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve(strict=True).parents[1]
SCRIPT_PATH = str(REPO_ROOT / "skills/agy-worker/runtime/scripts/swebench_workflow_study.py")
PYTHON = "/usr/bin/python3"


def make_valid_plan(tasks: list[str] | None = None) -> dict:
    if tasks is None:
        tasks = ["task_1", "task_2", "task_3"]
    return {
        "schema_version": 1,
        "kind": "agy-swebench-workflow-study-plan",
        "dataset_revision": "swe-bench-lite-v1",
        "evaluator_revision": "swe-bench-eval-v1",
        "repository_base": "b" * 64,
        "repository_image": "c" * 64,
        "frozen_prompt_digest": "a" * 64,
        "permissions_policy": "read-only-workspace",
        "network_policy": "offline-deny-all",
        "budgets": {
            "max_tasks": 100,
            "max_repairs_per_cell": 10,
            "max_wall_time_seconds_per_cell": 1000,
            "max_codex_tokens_per_cell": 10000,
            "max_agy_tokens_per_cell": 10000,
            "max_observed_billed_cost_per_cell": 100,
            "max_version_bound_list_price_cost_per_cell": 100,
        },
        "ordering": "task_then_arm",
        "codex_model": "gpt-5-codex-2026",
        "codex_effort": "high",
        "agy_model": "gemini-3.7-flash",
        "agy_effort": "medium",
        "arms": [
            "codex-only",
            "agy-explore-first",
            "agy-task-first",
            "agy-project-first",
            "second-eye",
        ],
        "tasks": tasks,
    }


def make_valid_cell(
    arm: str,
    accepted: bool = True,
    infra_fail: bool = False,
    repairs: int = 0,
    wall_time: float = 10.0,
    codex_input: int | None = 100,
    codex_cached: int | None = 0,
    codex_fresh: int | None = 100,
    codex_output: int | None = 50,
    codex_reasoning: int | None = 0,
    codex_cost_billed: float | None = 0.01,
) -> dict:
    return {
        "arm": arm,
        "failure_class": "pre_subject_infrastructure" if infra_fail else "none",
        "evaluator_resolved": accepted and not infra_fail,
        "clean_driver_gate": accepted and not infra_fail,
        "independent_diff_acceptance": accepted and not infra_fail,
        "exact_bindings_verified": accepted and not infra_fail,
        "accepted_solution": accepted and not infra_fail,
        "repair_count": repairs,
        "wall_time_seconds": wall_time,
        "codex_usage": {
            "input": codex_input,
            "cached_input": codex_cached,
            "fresh_input": codex_fresh,
            "cache_write": 0,
            "output": codex_output,
            "reasoning_output": codex_reasoning,
        },
        "codex_cost": {
            "observed_billed": codex_cost_billed,
            "version_bound_list_price": codex_cost_billed,
        },
        "agy_usage": {
            "input": 0,
            "cached_input": 0,
            "fresh_input": 0,
            "cache_write": 0,
            "output": 0,
            "reasoning_output": 0,
        },
        "agy_cost": {
            "observed_billed": 0,
            "version_bound_list_price": 0,
        },
    }


def make_valid_records(tasks: list[str] | None = None, dominant_arm: str = "agy-explore-first") -> list[dict]:
    if tasks is None:
        tasks = ["task_1", "task_2", "task_3"]
    arms = ["codex-only", "agy-explore-first", "agy-task-first", "agy-project-first", "second-eye"]
    records = []
    for t in tasks:
        cells = []
        for arm in arms:
            if arm == dominant_arm:
                cells.append(make_valid_cell(arm, codex_input=50, codex_fresh=50))
            else:
                cells.append(make_valid_cell(arm, codex_input=100, codex_fresh=100))
        records.append({"task_commitment": t, "cells": cells})
    return records


class TestSWEBenchWorkflowStudy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve(strict=True)
        self.root = base / "results"
        self.root.mkdir(mode=0o700)
        self.inputs = base / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.script = SCRIPT_PATH
        self.python = PYTHON

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, input_data=None, script=None):
        cmd = [self.python, "-I", "-S", "-B", script or self.script] + list(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=input_data,
        )

    def assert_rejected(self, result, category: str) -> None:
        output = result.stderr + result.stdout
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(f"error: {category}:", output)

    def write_plan(self, plan: dict) -> Path:
        plan_path = self.inputs / "input_plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        os.chmod(plan_path, 0o600)
        return plan_path

    def write_records(self, records: list[dict], name: str = "records.jsonl") -> Path:
        records_path = self.inputs / name
        with open(records_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        os.chmod(records_path, 0o600)
        return records_path

    def test_help_and_offline_source_contract(self):
        res = self.run_tool("--help")
        self.assertEqual(res.returncode, 0)
        self.assertIn("prepare", res.stdout)
        self.assertIn("import", res.stdout)
        self.assertIn("report", res.stdout)
        self.assertIn("advise", res.stdout)

        source = Path(self.script).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue(
            imported_roots.isdisjoint(
                {
                    "subprocess", "socket", "urllib", "http", "requests", "aiohttp", "ftplib",
                    "builtins", "importlib", "runpy", "code", "codeop", "ctypes",
                }
            )
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"eval", "exec", "compile", "__import__"})
            elif isinstance(node.func, ast.Attribute):
                self.assertNotIn(
                    node.func.attr,
                    {
                        "eval", "exec", "__import__",
                        "system", "popen", "Popen", "run", "call", "check_call", "check_output",
                        "fork", "forkpty", "posix_spawn", "posix_spawnp",
                        "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
                        "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
                    },
                )

    def test_full_positive_lifecycle(self):
        # 1. Prepare
        plan = make_valid_plan()
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.root / "plan.json").is_file())

        # Check published file mode is 0600
        st = (self.root / "plan.json").stat()
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)

        # 2. Import
        records = make_valid_records()
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.root / "imported_results.json").is_file())

        # 3. Report
        res = self.run_tool("report", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.root / "report.json").is_file())
        rep = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["kind"], "agy-swebench-workflow-study-report")
        self.assertEqual(len(rep["plan"]["tasks"]), 3)
        self.assertEqual(rep["denominators"], {"planned_tasks": 3, "planned_cells": 15, "accepted_solutions": 15})

        # 4. Advise
        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.root / "advisory.json").is_file())

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["kind"], "agy-swebench-workflow-study-advisory")
        self.assertEqual(adv["recommendation_only"], True)
        self.assertFalse(adv["applied"])
        self.assertFalse(adv["dispatch_authorized"])
        self.assertFalse(adv["model_change_authorized"])
        self.assertFalse(adv["effort_change_authorized"])

        for gate in (
            "failure_class",
            "evaluator_resolved",
            "clean_driver_gate",
            "independent_diff_acceptance",
            "exact_bindings_verified",
        ):
            with self.subTest(gate=gate):
                case = self.inputs / f"hard-stop-{gate}"
                case.mkdir(mode=0o700)
                result_root = case / "results"
                result_root.mkdir(mode=0o700)
                plan_file = case / "plan.json"
                plan_file.write_text(json.dumps(make_valid_plan()), encoding="utf-8")
                os.chmod(plan_file, 0o600)
                records = make_valid_records()
                records[0]["cells"][1][gate] = "subject_failure" if gate == "failure_class" else False
                records[0]["cells"][1]["accepted_solution"] = False
                records_file = case / "records.json"
                records_file.write_text(json.dumps(records), encoding="utf-8")
                os.chmod(records_file, 0o600)
                self.run_tool("prepare", "--root", str(result_root), "--plan", str(plan_file))
                self.run_tool("import", "--root", str(result_root), "--records", str(records_file))
                self.run_tool("report", "--root", str(result_root))
                result = self.run_tool("advise", "--root", str(result_root))
                self.assertEqual(result.returncode, 0, result.stderr)
                stopped = json.loads((result_root / "advisory.json").read_text(encoding="utf-8"))
                self.assertEqual(stopped["recommendation"], "no_recommendation")
                self.assertEqual(stopped["reason_code"], "hard-stop")
        self.assertEqual(adv["recommendation"], "agy-explore-first")
        self.assertEqual(adv["reason_code"], "pareto-dominant")
        self.assertAlmostEqual(adv["directional_total_reported_token_efficiency"], 0.3333333333333333, places=4)
        self.assertEqual(adv["denominators"]["planned_tasks"], 3)
        self.assertEqual(adv["denominators"]["planned_cells"], 15)
        self.assertEqual(adv["denominators"]["accepted_solutions"], 15)

    def test_prepare_rejects_missing_fields(self):
        plan = make_valid_plan()
        del plan["permissions_policy"]
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assertNotEqual(res.returncode, 0)

    def test_prepare_rejects_extra_fields(self):
        plan = make_valid_plan()
        plan["unknown_field"] = "bad"
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assertNotEqual(res.returncode, 0)

    def test_prepare_rejects_empty_tasks(self):
        plan = make_valid_plan(tasks=[])
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assert_rejected(res, "plan_tasks")

    def test_prepare_rejects_duplicate_tasks(self):
        plan = make_valid_plan(tasks=["task_1", "task_1"])
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assert_rejected(res, "plan_tasks")

    def test_prepare_rejects_placeholders(self):
        for placeholder in ["none", "unknown", "placeholder", "TODO", "null", "N/A"]:
            plan = make_valid_plan()
            plan["codex_model"] = placeholder
            plan_path = self.write_plan(plan)
            res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("privacy-safe", res.stderr + res.stdout)

    def test_prepare_rejects_absolute_paths(self):
        plan = make_valid_plan()
        plan["tasks"] = ["/etc/passwd", "task_2"]
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("privacy-safe", res.stderr + res.stdout)

    def test_prepare_rejects_task_bodies_with_newlines(self):
        plan = make_valid_plan()
        plan["tasks"] = ["task_1\nFix the bug in sympy", "task_2"]
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assert_rejected(res, "plan_privacy")

    def test_prepare_rejects_timestamps_and_privacy_keywords(self):
        for bad_date in ["task_2026-08-28_run", "task-2026-08-28-extra", "2026-08-28_task", "run_2026-08-28"]:
            plan = make_valid_plan()
            plan["tasks"] = [bad_date, "task_2"]
            plan_path = self.write_plan(plan)
            res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
            self.assert_rejected(res, "plan_privacy")

        plan = make_valid_plan()
        plan["tasks"] = ["task_secret_key", "task_2"]
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assert_rejected(res, "plan_privacy")

    def test_prepare_rejects_unordered_arms(self):
        plan = make_valid_plan()
        plan["arms"] = [
            "agy-explore-first",
            "codex-only",
            "agy-task-first",
            "agy-project-first",
            "second-eye",
        ]
        plan_path = self.write_plan(plan)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("arms", res.stderr + res.stdout)

    def test_import_rejects_unknown_arm(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"][0]["arm"] = "unknown-arm"
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("unknown arm", res.stderr + res.stdout)

    def test_import_rejects_missing_arm(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"].pop()
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assert_rejected(res, "arm_coverage")

    def test_import_rejects_duplicate_arm(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"][1]["arm"] = "codex-only"
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("duplicate arm", res.stderr + res.stdout)

    def test_import_rejects_extra_task(self):
        plan_path = self.write_plan(make_valid_plan(["task_1", "task_2", "task_3"]))
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records(["task_1", "task_2", "task_3", "task_extra"])
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assert_rejected(res, "task_coverage")

    def test_import_rejects_missing_task(self):
        plan_path = self.write_plan(make_valid_plan(["task_1", "task_2", "task_3"]))
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records(["task_1", "task_2"])
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assert_rejected(res, "task_coverage")

    def test_import_rejects_duplicate_task(self):
        plan_path = self.write_plan(make_valid_plan(["task_1", "task_2", "task_3"]))
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records(["task_1", "task_1", "task_2"])
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("duplicate", res.stderr + res.stdout)

    def test_import_rejects_malformed_json(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        bad_path = self.inputs / "bad.jsonl"
        bad_path.write_text("{not valid json\n", encoding="utf-8")
        os.chmod(bad_path, 0o600)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(bad_path))
        self.assertNotEqual(res.returncode, 0)

    def test_import_acceptance_derivation_infrastructure_failure(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"][0]["failure_class"] = "pre_subject_infrastructure"
        records[0]["cells"][0]["accepted_solution"] = True
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("accepted_solution", res.stderr + res.stdout)

    def test_import_acceptance_derivation_evaluator_unresolved(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"][0]["evaluator_resolved"] = False
        records[0]["cells"][0]["accepted_solution"] = True
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assert_rejected(res, "acceptance_derivation")

    def test_import_acceptance_derivation_driver_gate_and_diff(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"][0]["clean_driver_gate"] = False
        records[0]["cells"][0]["accepted_solution"] = True
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("accepted_solution", res.stderr + res.stdout)

    def test_import_token_arithmetic_input_mismatch(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"][0]["codex_usage"]["input"] = 101
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assert_rejected(res, "telemetry_arithmetic")

    def test_import_token_arithmetic_reasoning_output_exceeds_output(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"][0]["codex_usage"]["output"] = 50
        records[0]["cells"][0]["codex_usage"]["reasoning_output"] = 60
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("reasoning_output", res.stderr + res.stdout)

    def test_import_token_arithmetic_null_values_preserved(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        records[0]["cells"][0]["codex_usage"] = {
            "input": None,
            "cached_input": None,
            "fresh_input": None,
            "cache_write": None,
            "output": None,
            "reasoning_output": None,
        }
        records[0]["cells"][0]["codex_cost"] = {
            "observed_billed": None,
            "version_bound_list_price": None,
        }
        records_path = self.write_records(records)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertEqual(res.returncode, 0, res.stderr)

        res_rep = self.run_tool("report", "--root", str(self.root))
        self.assertEqual(res_rep.returncode, 0, res_rep.stderr)
        self.assertTrue((self.root / "report.json").is_file())

        res_adv = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res_adv.returncode, 0, res_adv.stderr)
        self.assertTrue((self.root / "advisory.json").is_file())
        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["reason_code"], "incomplete-telemetry")

    def test_report_preserves_plan_bindings_and_denominators(self):
        plan = make_valid_plan()
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        records_path = self.write_records(make_valid_records())
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))

        res = self.run_tool("report", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        rep = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        for k in ["dataset_revision", "evaluator_revision", "repository_base", "repository_image", "frozen_prompt_digest", "permissions_policy", "network_policy", "budgets", "ordering", "codex_model", "codex_effort", "agy_model", "agy_effort", "arms"]:
            self.assertEqual(rep["plan"][k], plan[k])

    def test_advise_calibration_policy_2_tasks(self):
        # 2-task x 5-arm calibration can never recommend
        plan = make_valid_plan(["task_1", "task_2"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        records = make_valid_records(["task_1", "task_2"])
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")
        self.assertEqual(adv["reason_code"], "calibration-only")
        self.assertIsNone(adv["directional_total_reported_token_efficiency"])

    def test_advise_calibration_policy_1_task(self):
        plan = make_valid_plan(["task_1"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        records = make_valid_records(["task_1"])
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")
        self.assertEqual(adv["reason_code"], "calibration-only")

    def test_advise_zero_accepted_solutions(self):
        plan = make_valid_plan(["task_1", "task_2", "task_3"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        for r in records:
            for cell in r["cells"]:
                cell["accepted_solution"] = False
                cell["evaluator_resolved"] = False
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")
        self.assertEqual(adv["reason_code"], "zero-accepted-solutions")
        self.assertEqual(adv["denominators"]["accepted_solutions"], 0)

    def test_advise_multiple_dominant_arms(self):
        plan = make_valid_plan(["task_1", "task_2", "task_3"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        for r in records:
            # Both agy-explore-first (idx 1) and second-eye (idx 4) improve tokens
            r["cells"][1]["codex_usage"]["input"] = 50
            r["cells"][1]["codex_usage"]["fresh_input"] = 50
            r["cells"][4]["codex_usage"]["input"] = 60
            r["cells"][4]["codex_usage"]["fresh_input"] = 60
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")
        self.assertEqual(adv["reason_code"], "multiple-dominant")

    def test_advise_no_dominant_arm(self):
        plan = make_valid_plan(["task_1", "task_2", "task_3"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        # No arm improves over codex-only
        records = make_valid_records(dominant_arm="none")
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")
        self.assertEqual(adv["reason_code"], "no-dominant-arm")

    def test_advise_matched_regression_repair_count(self):
        plan = make_valid_plan(["task_1", "task_2", "task_3"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        # agy-explore-first regresses on repair count for task 2
        records[1]["cells"][1]["repair_count"] = 5
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")

    def test_advise_matched_regression_wall_time(self):
        plan = make_valid_plan(["task_1", "task_2", "task_3"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        # agy-explore-first regresses on wall time for task 2
        records[1]["cells"][1]["wall_time_seconds"] = 100.0
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")

    def test_advise_matched_regression_token_usage(self):
        plan = make_valid_plan(["task_1", "task_2", "task_3"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        # agy-explore-first regresses on token usage on task 2
        records[1]["cells"][1]["codex_usage"]["input"] = 200
        records[1]["cells"][1]["codex_usage"]["fresh_input"] = 200
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")

    def test_advise_telemetry_incomparability(self):
        plan = make_valid_plan(["task_1", "task_2", "task_3"])
        plan_path = self.write_plan(plan)
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))

        records = make_valid_records()
        # agy-explore-first has missing telemetry for task 2
        records[1]["cells"][1]["codex_usage"] = {
            "input": None,
            "cached_input": None,
            "fresh_input": None,
            "cache_write": None,
            "output": None,
            "reasoning_output": None,
        }
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        res = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(adv["recommendation"], "no_recommendation")
        self.assertEqual(adv["reason_code"], "incomplete-telemetry")

    def test_advise_cost_incomparability(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        records = make_valid_records()
        records[1]["cells"][1]["codex_cost"] = {
            "observed_billed": None,
            "version_bound_list_price": None,
        }
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        result = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        advisory = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(advisory["recommendation"], "no_recommendation")
        self.assertEqual(advisory["reason_code"], "incomparable-cost")

    def test_advise_counts_agy_side_usage_in_pareto(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        records = make_valid_records()
        for record in records:
            candidate = record["cells"][1]
            candidate["agy_usage"]["input"] = 200
            candidate["agy_usage"]["fresh_input"] = 200
            candidate["agy_usage"]["output"] = 50
        records_path = self.write_records(records)
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))

        result = self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        advisory = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertEqual(advisory["recommendation"], "no_recommendation")
        self.assertEqual(advisory["reason_code"], "no-dominant-arm")

    def test_advise_authority_booleans_remain_false(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        records_path = self.write_records(make_valid_records())
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))
        self.run_tool("advise", "--root", str(self.root))

        adv = json.loads((self.root / "advisory.json").read_text(encoding="utf-8"))
        self.assertTrue(adv["recommendation_only"])
        self.assertFalse(adv["applied"])
        self.assertFalse(adv["dispatch_authorized"])
        self.assertFalse(adv["model_change_authorized"])
        self.assertFalse(adv["effort_change_authorized"])

    def test_no_overwrite_policy(self):
        plan_path = self.write_plan(make_valid_plan())
        res1 = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assertEqual(res1.returncode, 0)

        # Prepare again must fail
        res2 = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assertNotEqual(res2.returncode, 0)
        self.assertIn("lifecycle stage", res2.stderr + res2.stdout)

        records_path = self.write_records(make_valid_records())
        res3 = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertEqual(res3.returncode, 0)

        # Import again must fail
        res4 = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assertNotEqual(res4.returncode, 0)
        self.assertIn("lifecycle stage", res4.stderr + res4.stdout)

    def test_root_permissions_and_symlink_rejection(self):
        plan_path = self.write_plan(make_valid_plan())

        input_link = self.inputs / "plan-link.json"
        input_link.symlink_to(plan_path)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(input_link))
        self.assert_rejected(res, "input_unavailable")

        os.chmod(plan_path, 0o644)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assert_rejected(res, "input_permissions")
        os.chmod(plan_path, 0o600)

        spec = importlib.util.spec_from_file_location("swebench_workflow_study_uid_test", self.script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observed = plan_path.stat()
        foreign = SimpleNamespace(
            st_uid=os.getuid()+1,
            st_mode=observed.st_mode,
            st_nlink=observed.st_nlink,
            st_size=observed.st_size,
        )
        with mock.patch.object(module.os, "fstat", return_value=foreign):
            with self.assertRaises(SystemExit):
                module.read_private_json(plan_path, "plan input")

        # Non-0700 root
        os.chmod(self.root, 0o755)
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assert_rejected(res, "root_permissions")
        os.chmod(self.root, 0o700)

        prepared = self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        records_path = self.write_records(make_valid_records())
        records_link = self.inputs / "records-link.jsonl"
        records_link.symlink_to(records_path)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_link))
        self.assert_rejected(res, "input_unavailable")
        os.chmod(records_path, 0o644)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.assert_rejected(res, "input_permissions")
        os.chmod(records_path, 0o600)
        observed_records = records_path.stat()
        foreign_records = SimpleNamespace(
            st_uid=os.getuid()+1,
            st_mode=observed_records.st_mode,
            st_nlink=observed_records.st_nlink,
            st_size=observed_records.st_size,
        )
        with mock.patch.object(module.os, "fstat", return_value=foreign_records):
            with self.assertRaises(SystemExit):
                module.read_private_raw(records_path, "records input")

        # Symlink root
        symlink_root = self.root / "symlink_root"
        symlink_root.symlink_to(self.root)
        res = self.run_tool("prepare", "--root", str(symlink_root), "--plan", str(plan_path))
        self.assertNotEqual(res.returncode, 0)

    def test_deterministic_canonical_output(self):
        plan_path = self.write_plan(make_valid_plan())
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(plan_path))
        records_path = self.write_records(make_valid_records())
        self.run_tool("import", "--root", str(self.root), "--records", str(records_path))
        self.run_tool("report", "--root", str(self.root))
        self.run_tool("advise", "--root", str(self.root))

        for artifact in ["plan.json", "imported_results.json", "report.json", "advisory.json"]:
            path = self.root / artifact
            raw = path.read_bytes()
            parsed = json.loads(raw.decode("utf-8"))
            canonical = json.dumps(parsed, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.assertEqual(raw, canonical)

    def test_prepare_rejects_empty_or_open_budget_shapes(self):
        for budgets in ({}, {**make_valid_plan()["budgets"], "extra": 1}):
            plan = make_valid_plan()
            plan["budgets"] = budgets
            res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(plan)))
            self.assertNotEqual(res.returncode, 0)

    def test_prepare_rejects_nonfinite_budget(self):
        plan = make_valid_plan()
        plan["budgets"]["max_wall_time_seconds_per_cell"] = float("inf")
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(plan)))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("finite", res.stderr + res.stdout)

    def test_prepare_rejects_unsorted_tasks_and_non_digest(self):
        for mutation in ("tasks", "digest"):
            plan = make_valid_plan()
            if mutation == "tasks":
                plan["tasks"] = ["task_2", "task_1"]
            else:
                plan["frozen_prompt_digest"] = "not-a-digest"
            res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(plan)))
            self.assertNotEqual(res.returncode, 0)

    def test_prepare_rejects_seeded_result_root(self):
        (self.root / "unexpected").write_text("seed", encoding="utf-8")
        res = self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(make_valid_plan())))
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn(str(self.root), res.stderr + res.stdout)

    def test_import_requires_every_acceptance_gate(self):
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(make_valid_plan())))
        records = make_valid_records()
        del records[0]["cells"][0]["exact_bindings_verified"]
        res = self.run_tool("import", "--root", str(self.root), "--records", str(self.write_records(records)))
        self.assertNotEqual(res.returncode, 0)

    def test_import_rejects_false_accepted_derivation(self):
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(make_valid_plan())))
        records = make_valid_records()
        records[0]["cells"][0]["accepted_solution"] = False
        res = self.run_tool("import", "--root", str(self.root), "--records", str(self.write_records(records)))
        self.assertNotEqual(res.returncode, 0)

    def test_import_rejects_nonfinite_cell_and_cost(self):
        for field in ("wall", "cost", "combined-cost"):
            isolated = self.inputs / field
            isolated.mkdir(mode=0o700)
            root = isolated / "results"
            root.mkdir(mode=0o700)
            plan_path = isolated / "plan.json"
            plan_path.write_text(json.dumps(make_valid_plan()), encoding="utf-8")
            os.chmod(plan_path, 0o600)
            self.run_tool("prepare", "--root", str(root), "--plan", str(plan_path))
            records = make_valid_records()
            if field == "wall": records[0]["cells"][0]["wall_time_seconds"] = float("nan")
            elif field == "cost": records[0]["cells"][0]["codex_cost"]["observed_billed"] = float("inf")
            else:
                records[0]["cells"][1]["codex_cost"]["observed_billed"] = 80
                records[0]["cells"][1]["agy_cost"]["observed_billed"] = 80
            records_path = isolated / "records.json"
            records_path.write_text(json.dumps(records), encoding="utf-8")
            os.chmod(records_path, 0o600)
            res = self.run_tool("import", "--root", str(root), "--records", str(records_path))
            self.assertNotEqual(res.returncode, 0)
            if field == "combined-cost": self.assert_rejected(res, "cost_budget")

    def test_report_rejects_plan_drift_even_when_canonical(self):
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(make_valid_plan())))
        path = self.root / "plan.json"
        plan = json.loads(path.read_text(encoding="utf-8")); plan["codex_effort"] = "medium"
        path.write_text(json.dumps(plan, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.chmod(path, 0o600)
        res = self.run_tool("import", "--root", str(self.root), "--records", str(self.write_records(make_valid_records())))
        self.assertNotEqual(res.returncode, 0)

    def test_advise_rejects_report_chain_drift(self):
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(make_valid_plan())))
        self.run_tool("import", "--root", str(self.root), "--records", str(self.write_records(make_valid_records())))
        self.run_tool("report", "--root", str(self.root))
        path = self.root / "report.json"
        report = json.loads(path.read_text(encoding="utf-8")); report["plan_sha256"] = "0" * 64
        path.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.chmod(path, 0o600)
        res = self.run_tool("advise", "--root", str(self.root))
        self.assertNotEqual(res.returncode, 0)

    def test_publication_is_bounded_flat_owner_0600_and_relocatable(self):
        self.run_tool("prepare", "--root", str(self.root), "--plan", str(self.write_plan(make_valid_plan())))
        self.run_tool("import", "--root", str(self.root), "--records", str(self.write_records(make_valid_records())))
        self.run_tool("report", "--root", str(self.root)); self.run_tool("advise", "--root", str(self.root))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["advisory.json", "imported_results.json", "plan.json", "report.json"])
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in self.root.iterdir()))

        # The compact caller input can fit exactly while its derived canonical
        # wrapper does not. Publication must fail before creating any temporary
        # or final entry, leaving the prior plan stage retryable.
        overflow_case = self.inputs / "canonical-overflow"
        overflow_case.mkdir(mode=0o700)
        overflow_root = overflow_case / "results"
        overflow_root.mkdir(mode=0o700)
        overflow_plan = overflow_case / "plan.json"
        overflow_plan.write_text(json.dumps(make_valid_plan(["task_1"])), encoding="utf-8")
        os.chmod(overflow_plan, 0o600)
        prepared = self.run_tool(
            "prepare", "--root", str(overflow_root), "--plan", str(overflow_plan)
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        overflow_records = make_valid_records(["task_1"])
        overflow_raw = json.dumps(
            overflow_records, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        overflow_records_path = overflow_case / "records.json"
        overflow_records_path.write_bytes(overflow_raw)
        os.chmod(overflow_records_path, 0o600)

        spec = importlib.util.spec_from_file_location(
            "swebench_workflow_study_size_test", self.script
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plan_raw = (overflow_root / "plan.json").read_bytes()
        limit = len(overflow_raw)
        self.assertLessEqual(len(plan_raw), limit)
        self.assertLessEqual(module.PLAN_SCHEMA.stat().st_size, limit)
        derived = {
            "schema_version": 1,
            "kind": "agy-swebench-workflow-study-import",
            "plan_sha256": "0" * 64,
            "exact_bindings_verified": True,
            "records": overflow_records,
        }
        self.assertGreater(len(module.canonical_bytes(derived)), limit)
        diagnostic = io.StringIO()
        with mock.patch.object(module, "MAX_INPUT_BYTES", limit):
            with contextlib.redirect_stderr(diagnostic):
                with self.assertRaises(SystemExit) as raised:
                    module.do_import(
                        SimpleNamespace(root=overflow_root, records=overflow_records_path)
                    )
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("error: publication_size:", diagnostic.getvalue())
        self.assertEqual(sorted(path.name for path in overflow_root.iterdir()), ["plan.json"])

        relocated_skill = self.inputs / "relocated-skill"
        shutil.copytree(REPO_ROOT / "skills" / "agy-worker", relocated_skill)
        relocated_script = str(relocated_skill / "runtime" / "scripts" / "swebench_workflow_study.py")
        relocated_plan = self.inputs / "relocated-plan.json"
        relocated_plan.write_text(json.dumps(make_valid_plan()), encoding="utf-8")
        os.chmod(relocated_plan, 0o600)
        relocated_root = self.inputs / "relocated-results"
        relocated_root.mkdir(mode=0o700)
        result = self.run_tool(
            "prepare", "--root", str(relocated_root), "--plan", str(relocated_plan), script=relocated_script
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((relocated_root / "plan.json").is_file())

        bundle_internal_root = relocated_skill / "results"
        bundle_internal_root.mkdir(mode=0o700)
        result = self.run_tool(
            "prepare", "--root", str(bundle_internal_root), "--plan", str(relocated_plan), script=relocated_script
        )
        self.assert_rejected(result, "root_location")


if __name__ == "__main__":
    unittest.main()
