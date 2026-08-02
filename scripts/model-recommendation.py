#!/usr/bin/env python3
"""Render a driver-evidence-based, recommendation-only model-tier decision."""

import argparse
import json
import re
import sys


NAMED_TIERS = ("cheap", "bulk", "hard", "hardest")

PRE_DISPATCH_EVIDENCE = {
    "bounded-routine": (
        "cheap",
        "The driver classified the task as bounded and routine.",
    ),
    "batched-mechanical": (
        "bulk",
        "The driver identified a bounded batch of mechanical work.",
    ),
    "cross-file-bounded": (
        "hard",
        "The driver identified bounded cross-file reasoning with fixed acceptance criteria.",
    ),
    "high-complexity-bounded": (
        "hardest",
        "The driver identified a bounded high-complexity task with fixed acceptance criteria.",
    ),
}

POST_GATE_EVIDENCE = {
    "gate-accepted": (
        False,
        "The driver-owned gate accepted the independently observed repository state.",
        "Acceptance supplies no reason to recommend a more expensive tier.",
    ),
    "driver-verification-failed": (
        True,
        "A driver-authored verification command failed against the candidate.",
        "A bounded verification failure may benefit from one higher named tier.",
    ),
    "driver-quality-review-failed": (
        True,
        "Independent driver review found a bounded quality gap in the candidate.",
        "A bounded quality gap may benefit from one higher named tier.",
    ),
    "expected-edits-missing": (
        True,
        "The gate independently found no edits for a job that required edits.",
        "A bounded failure to produce the requested edit may benefit from one higher named tier.",
    ),
    "permission-failed": (
        False,
        "The driver classified the outcome as a permission failure.",
        "A model change cannot grant permission; resolve the permission boundary instead.",
    ),
    "authentication-failed": (
        False,
        "The driver classified the outcome as an authentication failure.",
        "A model change cannot authenticate the tool; resolve authentication instead.",
    ),
    "scope-policy-failed": (
        False,
        "The gate independently found a scope-policy violation.",
        "A stronger model must not be used to bypass the driver-owned scope policy.",
    ),
    "human-required": (
        False,
        "The gate routed the outcome to a human decision.",
        "A model change cannot supply authorization or make the required human decision.",
    ),
    "noncompleted-worker-outcome": (
        False,
        "The gate routed a partial, failed, or blocked worker outcome without accepting it.",
        "An untrusted noncompleted status is not driver-owned evidence that more model spend would help.",
    ),
    "untrusted-worker-claim": (
        False,
        "The gate rejected untrusted command or test claims from the worker envelope.",
        "A tier change does not turn worker claims into driver-owned evidence.",
    ),
    "invalid-envelope": (
        False,
        "The checked-in validator rejected the worker envelope contract.",
        "Repair the contract or prompt boundary without inferring that more model spend is justified.",
    ),
}

SAFE_TIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+/-]*\Z")


class UsageErrorParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(64, f"model-recommendation: {message}\n")


def one(parser, values, flag):
    if not values:
        parser.error(f"{flag} is required")
    if len(values) != 1:
        parser.error(f"{flag} must be provided exactly once")
    return values[0]


def no_change(reason):
    return {
        "decision": "no-escalation",
        "recommended_tier": None,
        "rationale": reason,
        "cost_impact": {
            "direction": "none",
            "relative_tier_steps": 0,
            "summary": "No tier change is recommended; no incremental model cost is proposed.",
        },
    }


def higher_tier(selected_tier, recommended_tier, reason):
    selected_index = NAMED_TIERS.index(selected_tier)
    recommended_index = NAMED_TIERS.index(recommended_tier)
    steps = recommended_index - selected_index
    return {
        "decision": "consider-higher-tier",
        "recommended_tier": recommended_tier,
        "rationale": reason,
        "cost_impact": {
            "direction": "increase",
            "relative_tier_steps": steps,
            "summary": (
                f"The recommendation is {steps} named tier step"
                f"{'s' if steps != 1 else ''} higher; exact provider cost is not inferred."
            ),
        },
    }


def pre_dispatch(selected_tier, evidence_code):
    target_tier, evidence_description = PRE_DISPATCH_EVIDENCE[evidence_code]
    if selected_tier not in NAMED_TIERS:
        decision = no_change(
            "The selected tier is default or a custom model label, so its relative position cannot be inferred safely."
        )
    elif NAMED_TIERS.index(selected_tier) >= NAMED_TIERS.index(target_tier):
        decision = no_change(
            f"The selected tier already meets or exceeds the driver-evidenced {target_tier} task profile."
        )
    else:
        decision = higher_tier(
            selected_tier,
            target_tier,
            f"The driver-evidenced task profile maps to the named {target_tier} tier.",
        )
    return evidence_description, decision


def post_gate(selected_tier, evidence_code):
    escalatable, evidence_description, rationale = POST_GATE_EVIDENCE[evidence_code]
    if not escalatable:
        return evidence_description, no_change(rationale)
    if selected_tier not in NAMED_TIERS:
        return evidence_description, no_change(
            "The selected tier is default or a custom model label, so a higher tier cannot be inferred safely."
        )
    selected_index = NAMED_TIERS.index(selected_tier)
    if selected_index == len(NAMED_TIERS) - 1:
        return evidence_description, no_change(
            "The caller already selected the highest named tier; no higher named tier exists."
        )
    return evidence_description, higher_tier(
        selected_tier,
        NAMED_TIERS[selected_index + 1],
        rationale,
    )


def main(argv=None):
    parser = UsageErrorParser(
        prog="model-recommendation.sh",
        description="Print a recommendation-only model-tier decision as JSON.",
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=("pre-dispatch", "post-gate"),
        help="recommendation point",
    )
    parser.add_argument(
        "--selected-tier",
        action="append",
        metavar="TIER",
        help="the caller-selected named tier, default, or explicit model label",
    )
    parser.add_argument(
        "--evidence",
        action="append",
        metavar="CODE",
        help="one controlled driver-owned evidence code",
    )
    args = parser.parse_args(argv)

    stage = one(parser, args.stage, "--stage")
    selected_tier = one(parser, args.selected_tier, "--selected-tier")
    evidence_code = one(parser, args.evidence, "--evidence")

    if not SAFE_TIER.fullmatch(selected_tier):
        parser.error("--selected-tier must be a non-empty tier or model label without whitespace")

    evidence_set = PRE_DISPATCH_EVIDENCE if stage == "pre-dispatch" else POST_GATE_EVIDENCE
    if evidence_code not in evidence_set:
        allowed = ", ".join(sorted(evidence_set))
        parser.error(f"--evidence is not valid for {stage}; choose one of: {allowed}")

    if stage == "pre-dispatch":
        evidence_description, decision = pre_dispatch(selected_tier, evidence_code)
    else:
        evidence_description, decision = post_gate(selected_tier, evidence_code)

    result = {
        "schema_version": 1,
        "kind": "model-tier-recommendation",
        "stage": stage,
        "recommendation_only": True,
        "applied": False,
        "selected_tier": selected_tier,
        **decision,
        "evidence": {
            "owner": "driver",
            "code": evidence_code,
            "description": evidence_description,
        },
    }
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
