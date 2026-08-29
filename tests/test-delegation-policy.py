#!/usr/bin/env python3
"""Focused positive and adversarial tests for Delegation-First Coordinator Policy."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "agy-worker" / "runtime" / "scripts" / "delegation_policy.py"
SPEC = importlib.util.spec_from_file_location("delegation_policy_tested", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SCHEMA_PATH = ROOT / "skills" / "agy-worker" / "runtime" / "schemas" / "delegation-policy.schema.json"

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


def valid_input(policy: str = "delegation-first", intent: str = "task") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "agy-delegation-policy-input",
        "policy": policy,
        "intent": intent,
        "user_opt_in": True,
        "transmission_approved": True,
        "scope_path_approved": True,
        "preflight_passed": True,
        "provider_state": "available",
        "hard_stop_triggered": False,
        "hard_stop_reasons": [],
        "cycle_budget_exhausted": False,
        "attempt_count": 0,
        "max_cycles": 2,
        "prior_candidate_available": False,
        "caller_model": "gemini-3.7-flash",
        "caller_effort": "medium",
    }


# 1. Happy Path Delegation-First
def test_delegation_first_happy_path() -> bool:
    for intent in ("explore", "task", "project"):
        inp = valid_input("delegation-first", intent)
        res = MODULE.evaluate_policy(inp)
        assert res["decision"] == "delegate-agy"
        assert res["first_substantive_actor"] == "agy"
        assert res["reason_code"] == "delegation-first-active"
        assert res["caller_model"] == "gemini-3.7-flash"
        assert res["caller_effort"] == "medium"
        assert res["silent_fallback_authorized"] is False
        assert res["direct_codex_authorized"] is False
        assert res["git_authorized"] is False
        assert res["acceptance_authorized"] is False
        assert res["token_disclaimer"] == MODULE.TOKEN_DISCLAIMER
        assert res["fixed_overhead_warning"] == MODULE.FIXED_OVERHEAD_WARNING
        assert "instruction_discovery" in res["required_pre_steps"]
        assert "scope_privacy_approval" in res["required_pre_steps"]
        assert "disposable_worktree_setup" in res["required_pre_steps"]
        assert "verification_planning" in res["required_pre_steps"]
    skill_text = (ROOT / "skills" / "agy-worker" / "SKILL.md").read_text(encoding="utf-8")
    assert "requires running the `delegation-policy.sh` evaluator before substantive repository work" in skill_text
    assert "the runtime cannot infer prior work" in skill_text
    assert "must never silently authorize" in skill_text
    return True


check("delegation-first happy path selects AGY with pre-steps and locked authority", test_delegation_first_happy_path)


# 2. Explicit User Opt-In Requirement
def test_explicit_user_opt_in() -> bool:
    inp = valid_input("delegation-first", "task")
    inp["user_opt_in"] = False
    res = MODULE.evaluate_policy(inp)
    assert res["decision"] == "blocked"
    assert res["reason_code"] == "missing-user-opt-in"
    assert res["direct_codex_authorized"] is False

    inp2 = valid_input("delegation-first", "task")
    del inp2["user_opt_in"]
    res2 = MODULE.evaluate_policy(inp2)
    assert res2["decision"] == "blocked"
    assert res2["reason_code"] == "missing-user-opt-in"
    return True


check("delegation-first requires explicit user opt-in", test_explicit_user_opt_in)


# 3. Overrides: Direct-Codex and Second-Eye
def test_overrides() -> bool:
    # Direct-Codex needs no transmission approval, preflight, or provider availability
    inp_codex = valid_input("direct-codex", "task")
    inp_codex["transmission_approved"] = False
    inp_codex["preflight_passed"] = False
    inp_codex["provider_state"] = "unavailable"
    res_codex = MODULE.evaluate_policy(inp_codex)
    assert res_codex["decision"] == "direct-codex"
    assert res_codex["first_substantive_actor"] == "codex"
    assert res_codex["reason_code"] == "direct-codex-override"
    assert res_codex["direct_codex_authorized"] is True
    assert res_codex["silent_fallback_authorized"] is False

    # Second-Eye requires transmission approval, preflight, and provider availability
    inp_se = valid_input("second-eye", "project")
    inp_se["provider_state"] = "unavailable"
    res_se = MODULE.evaluate_policy(inp_se)
    assert res_se["decision"] == "blocked"
    assert res_se["reason_code"] == "provider-unavailable"
    assert res_se["direct_codex_authorized"] is False

    inp_se_good = valid_input("second-eye", "project")
    res_se_good = MODULE.evaluate_policy(inp_se_good)
    assert res_se_good["decision"] == "second-eye"
    assert res_se_good["first_substantive_actor"] == "codex"
    assert res_se_good["reason_code"] == "second-eye-override"
    assert res_se_good["direct_codex_authorized"] is True
    assert "post_implementation_review" in res_se_good["required_pre_steps"]
    return True


check("direct-codex needs no provider approval while second-eye respects provider/preflight", test_overrides)


# 4. Missing Approvals and Hard Stops
def test_missing_approvals_and_hard_stops() -> bool:
    # Hard stop triggered
    inp_hs = valid_input("delegation-first")
    inp_hs["hard_stop_triggered"] = True
    inp_hs["hard_stop_reasons"] = ["user cancelled"]
    res_hs = MODULE.evaluate_policy(inp_hs)
    assert res_hs["decision"] == "blocked"
    assert res_hs["reason_code"] == "hard-stop-active"
    assert res_hs["direct_codex_authorized"] is False

    # Missing transmission approval
    inp_trans = valid_input("delegation-first")
    inp_trans["transmission_approved"] = False
    res_trans = MODULE.evaluate_policy(inp_trans)
    assert res_trans["decision"] == "blocked"
    assert res_trans["reason_code"] == "missing-transmission-approval"
    assert res_trans["direct_codex_authorized"] is False

    # Missing scope approval
    inp_scope = valid_input("delegation-first")
    inp_scope["scope_path_approved"] = False
    res_scope = MODULE.evaluate_policy(inp_scope)
    assert res_scope["decision"] == "blocked"
    assert res_scope["reason_code"] == "missing-scope-approval"
    assert res_scope["direct_codex_authorized"] is False
    return True


check("missing transmission/scope approvals and hard stops fail closed with blocked", test_missing_approvals_and_hard_stops)


# 5. Preflight and Provider Failures Under Delegation-First
def test_preflight_and_provider_failures_never_silent_fallback() -> bool:
    # Preflight failed
    inp_pref = valid_input("delegation-first")
    inp_pref["preflight_passed"] = False
    res_pref = MODULE.evaluate_policy(inp_pref)
    assert res_pref["decision"] == "blocked"
    assert res_pref["reason_code"] == "preflight-failed"
    assert res_pref["direct_codex_authorized"] is False
    assert res_pref["silent_fallback_authorized"] is False

    # Provider quota exhausted
    inp_quota = valid_input("delegation-first")
    inp_quota["provider_state"] = "quota_exhausted"
    res_quota = MODULE.evaluate_policy(inp_quota)
    assert res_quota["decision"] == "blocked"
    assert res_quota["reason_code"] == "provider-quota-exhausted"
    assert res_quota["direct_codex_authorized"] is False

    # Provider unavailable
    for st in ("unavailable", "rate_limited", "unverified"):
        inp_unav = valid_input("delegation-first")
        inp_unav["provider_state"] = st
        res_unav = MODULE.evaluate_policy(inp_unav)
        assert res_unav["decision"] == "blocked"
        assert res_unav["reason_code"] == "provider-unavailable"
        assert res_unav["direct_codex_authorized"] is False
    return True


check("preflight and provider failures fail closed as blocked and never authorize silent fallback", test_preflight_and_provider_failures_never_silent_fallback)


# 6. Cycle Budget Exhaustion (With Candidate vs No Candidate)
def test_cycle_budget_exhaustion() -> bool:
    # Exhaustion with prior candidate -> partially_verified
    inp_cand = valid_input("delegation-first")
    inp_cand["cycle_budget_exhausted"] = True
    inp_cand["prior_candidate_available"] = True
    res_cand = MODULE.evaluate_policy(inp_cand)
    assert res_cand["decision"] == "partially_verified"
    assert res_cand["reason_code"] == "cycle-budget-exhausted"
    assert res_cand["direct_codex_authorized"] is False

    # Exhaustion without candidate -> blocked (no-candidate-exhausted)
    inp_no_cand = valid_input("delegation-first")
    inp_no_cand["cycle_budget_exhausted"] = True
    inp_no_cand["prior_candidate_available"] = False
    res_no_cand = MODULE.evaluate_policy(inp_no_cand)
    assert res_no_cand["decision"] == "blocked"
    assert res_no_cand["reason_code"] == "no-candidate-exhausted"
    assert res_no_cand["direct_codex_authorized"] is False
    return True


check("cycle budget exhaustion distinguishes candidate partial delivery from no-candidate blocked", test_cycle_budget_exhaustion)


# 7. Schema and Input Validation (Closed Keys and Bounded Selection)
def test_input_validation() -> bool:
    base = valid_input()

    for label, mutate in (
        ("unknown policy", lambda d: d.__setitem__("policy", "unsupported-policy")),
        ("unknown intent", lambda d: d.__setitem__("intent", "unsupported-intent")),
        ("non-bool transmission_approved", lambda d: d.__setitem__("transmission_approved", "yes")),
        ("invalid provider_state", lambda d: d.__setitem__("provider_state", "unknown-state")),
        ("negative attempt_count", lambda d: d.__setitem__("attempt_count", -1)),
        ("zero max_cycles", lambda d: d.__setitem__("max_cycles", 0)),
        ("unbounded caller_model", lambda d: d.__setitem__("caller_model", "m" * 150)),
        ("invalid caller_model chars", lambda d: d.__setitem__("caller_model", "model\nnewline")),
        ("invalid caller_effort enum", lambda d: d.__setitem__("caller_effort", "ultra")),
        ("extra key rejected", lambda d: d.__setitem__("unauthorized_extra_key", True)),
    ):
        candidate = copy.deepcopy(base)
        mutate(candidate)
        try:
            MODULE.evaluate_policy(candidate)
        except MODULE.DelegationPolicyError:
            continue
        print(f"FAILED to reject: {label}")
        return False
    return True


check("evaluator closes input keys and enforces bounded model and effort selection", test_input_validation)


# 8. CLI Subcommand and Argument Checks
def test_cli_interface() -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        in_file = Path(temp_dir) / "input.json"
        out_file = Path(temp_dir) / "out.json"
        in_file.write_text(json.dumps(valid_input()), encoding="utf-8")

        res = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPT), "eval", f"--input={in_file}", f"--out={out_file}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert res.returncode == 0, res.stderr
        out_data = json.loads(out_file.read_text(encoding="utf-8"))
        assert out_data["decision"] == "delegate-agy"
        assert out_data["caller_model"] == "gemini-3.7-flash"

        # Duplicate option rejected
        res_dup = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPT), "eval", "--input", str(in_file), "--input", str(in_file)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert res_dup.returncode == 2

    return True


check("CLI supports eval, --input, --out, and rejects duplicates", test_cli_interface)

print()
print(f"PASSED: {passed} tests")
if failed:
    print(f"FAILED: {failed} tests")
    raise SystemExit(1)
