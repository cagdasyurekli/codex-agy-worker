#!/usr/bin/env python3
"""Focused offline regression checks for the remediation controller contract."""
from __future__ import annotations

import copy
import fcntl
import io
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills/agy-worker/runtime/scripts/agy_dispatch.py"
SCHEMA = ROOT / "skills/agy-worker/runtime/schemas/worker-result.schema.json"
spec = importlib.util.spec_from_file_location("agy_dispatch_remediation", SOURCE)
MODULE = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(MODULE)


def check(label: str, action) -> None:
    try:
        action()
    except Exception as exc:  # pragma: no cover - direct failure context
        raise AssertionError(f"{label}: {exc}") from exc
    print(f"ok: {label}")


def provider_schema(path: Path) -> None:
    value = json.loads(SCHEMA.read_text(encoding="utf-8"))
    value["required"] = [item for item in value["required"] if item not in {"commands_run", "tests_run"}]
    path.write_text(json.dumps(value), encoding="utf-8")


def stream(path: Path, status: str, report: dict | None) -> None:
    events = [
        {"event": "init", "init": {}, "conversation_id": "conversation-1"},
        {"event": "result", "result": {"conversation_id": "conversation-1", "status": status, "structured_output": report}},
    ]
    path.write_bytes(b"".join(json.dumps(item).encode("utf-8") + b"\n" for item in events))


def report(**updates: object) -> dict:
    value = {
        "status": "completed", "summary": "candidate", "files_changed": [],
        "commands_run": [], "tests_run": [], "risks": [], "open_questions": [],
        "confidence": 0.5, "requires_human": False,
    }
    value.update(updates)
    return value


def run_controller(job: Path, bin_dir: Path) -> int:
    lock = job / MODULE.LOCK_NAME
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    prior_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{prior_path}"
    try:
        result = MODULE.controller(job, descriptor)
        descriptor = -1
        return result
    finally:
        os.environ["PATH"] = prior_path
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    provider = root / "provider.json"
    provider_schema(provider)

    def error_candidate_is_preserved() -> None:
        source = root / "error.ndjson"; envelope = root / "error.json"
        stream(source, "ERROR", report(commands_run=None) if False else {key: value for key, value in report().items() if key not in {"commands_run", "tests_run"}})
        binding, outer, stage = MODULE._validate_terminal_envelope(source, envelope, provider, SCHEMA)
        assert binding is not None and outer == "ERROR" and stage is None
        stored = json.loads(envelope.read_text(encoding="utf-8"))
        assert stored["commands_run"] == [] and stored["tests_run"] == []

    check("ERROR plus valid provider report is preserved and normalized", error_candidate_is_preserved)

    def critical_missing_is_canonical_failure() -> None:
        source = root / "missing.ndjson"; envelope = root / "missing.json"
        stream(source, "SUCCESS", report(summary=""))
        binding, outer, stage = MODULE._validate_terminal_envelope(source, envelope, provider, SCHEMA)
        assert binding is None and outer == "SUCCESS" and stage == "schema_rejection"

    check("SUCCESS missing a critical canonical value is not a candidate", critical_missing_is_canonical_failure)

    def extra_field_is_rejected() -> None:
        source = root / "extra.ndjson"; envelope = root / "extra.json"
        stream(source, "SUCCESS", report(untrusted_extra=True))
        binding, outer, stage = MODULE._validate_terminal_envelope(source, envelope, provider, SCHEMA)
        assert binding is None and outer == "SUCCESS" and stage == "schema_rejection"

    check("provider extra field is rejected", extra_field_is_rejected)

    def terminal_failure_stages_are_distinct() -> None:
        malformed = root / "malformed.ndjson"; malformed.write_bytes(b"not-json\n")
        binding, outer, stage = MODULE._validate_terminal_envelope(malformed, root / "malformed.json", provider, SCHEMA)
        assert binding is None and outer is None and stage == "framing"
        missing = root / "no-structured.ndjson"
        stream(missing, "SUCCESS", None)
        binding, outer, stage = MODULE._validate_terminal_envelope(missing, root / "no-structured.json", provider, SCHEMA)
        assert binding is None and outer == "SUCCESS" and stage == "structured_output"

    check("framing and structured-output failures remain distinct", terminal_failure_stages_are_distinct)

    def only_two_optional_arrays_normalize() -> None:
        source = root / "critical-missing.ndjson"; envelope = root / "critical-missing.json"
        candidate = report(); candidate.pop("risks")
        stream(source, "SUCCESS", candidate)
        binding, outer, stage = MODULE._validate_terminal_envelope(source, envelope, provider, SCHEMA)
        assert binding is None and outer == "SUCCESS" and stage == "schema_rejection"

    check("only commands and tests arrays normalize", only_two_optional_arrays_normalize)

    def state_v4_migrates_additively() -> None:
        command = {
            "workdir": str(root), "workflow": "legacy", "max_cycles": 1, "job_id": "legacy",
            "hard_seconds": 2, "max_seconds": 4, "idle_seconds": 1,
        }
        state = MODULE.initial_state(command, "initial", 1, command_sha="0" * 64, command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None)
        state["schema_version"] = 4
        for key in MODULE.STATE_V5_FIELDS:
            state.pop(key)
        migrated = MODULE.validate_state(state)
        assert migrated["candidate_source"] == "none"
        assert migrated["driver_disposition"] == "not_applicable"
        assert migrated["worktree_changes_present"] is None

    check("v4 state reads as additive v5 state", state_v4_migrates_additively)

    def v1_v3_v4_remain_read_compatible() -> None:
        command = {
            "workdir": str(root), "workflow": "legacy", "max_cycles": 1, "job_id": "legacy-read",
            "hard_seconds": 2, "max_seconds": 4, "idle_seconds": 1,
        }
        original = MODULE.initial_state(
            command, "initial", 1, command_sha="0" * 64,
            command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None,
        )
        v3 = copy.deepcopy(original); v3["schema_version"] = 3
        for key in {"provider_retry_after_seconds", "provider_retry_observed_epoch", *MODULE.STATE_V5_FIELDS}:
            v3.pop(key)
        assert MODULE.validate_state(v3)["schema_version"] == 3
        v1 = copy.deepcopy(v3); v1["schema_version"] = 1
        for key in MODULE.STATE_PROJECT_FIELDS:
            v1.pop(key)
        assert MODULE.validate_state(v1)["schema_version"] == 1

    check("v1 v3 and v4 state snapshots remain read compatible", v1_v3_v4_remain_read_compatible)

    def reconciliation_never_follows_outward_symlink() -> None:
        repo = root / "repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        baseline = MODULE._worktree_snapshot(str(repo))
        assert baseline is not None and baseline["entries"] == 0
        outside = root / "outside.txt"; outside.write_text("one", encoding="utf-8")
        (repo / "escape").symlink_to(outside)
        observation = MODULE._reconcile_worktree(str(repo), baseline)
        assert observation["worktree_reconciliation"] == "available"
        assert observation["worktree_changes_present"] is True
        before = MODULE._worktree_snapshot(str(repo)); assert before is not None
        outside.write_text("two", encoding="utf-8")
        after = MODULE._worktree_snapshot(str(repo)); assert after is not None
        assert before == after, "outward symlink target content was followed"

    check("worktree reconciliation hashes but never follows outward symlink", reconciliation_never_follows_outward_symlink)

    def reconciliation_hashes_content_under_same_git_status() -> None:
        repo = root / "content-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        (repo / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
        (repo / "tracked.txt").write_text("base", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", ".gitignore", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        (repo / "tracked.txt").write_text("aaaa", encoding="utf-8")
        first = MODULE._worktree_snapshot(str(repo)); assert first is not None
        status_one = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--ignored", "--untracked-files=all"],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        (repo / "tracked.txt").write_text("bbbb", encoding="utf-8")
        second = MODULE._worktree_snapshot(str(repo)); assert second is not None
        status_two = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--ignored", "--untracked-files=all"],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        assert status_one == status_two and first["sha256"] != second["sha256"]
        (repo / "untracked.txt").write_text("u1", encoding="utf-8")
        untracked_one = MODULE._worktree_snapshot(str(repo)); assert untracked_one is not None
        (repo / "untracked.txt").write_text("u2", encoding="utf-8")
        untracked_two = MODULE._worktree_snapshot(str(repo)); assert untracked_two is not None
        assert untracked_one["sha256"] != untracked_two["sha256"]
        (repo / "ignored.bin").write_text("i1", encoding="utf-8")
        ignored_one = MODULE._worktree_snapshot(str(repo)); assert ignored_one is not None
        (repo / "ignored.bin").write_text("i2", encoding="utf-8")
        ignored_two = MODULE._worktree_snapshot(str(repo)); assert ignored_two is not None
        assert ignored_one["sha256"] != ignored_two["sha256"]
        (repo / "tracked.txt").unlink()
        deleted = MODULE._worktree_snapshot(str(repo)); assert deleted is not None
        assert deleted["sha256"] != ignored_two["sha256"]

    check("tracked untracked ignored deleted content changes alter the bounded digest", reconciliation_hashes_content_under_same_git_status)

    def bound_schemas_and_candidate_worktree_fail_closed() -> None:
        repo = root / "bound-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        bound_provider = root / "bound-provider.json"; provider_schema(bound_provider)
        command = {
            "workdir": str(repo), "workflow": "task", "max_cycles": 1, "job_id": "bound",
            "hard_seconds": 2, "max_seconds": 4, "idle_seconds": 1,
            "argv": ["agy", "--json-schema", str(bound_provider), "--print", "task"],
        }
        bindings = MODULE._schema_bindings(command)
        state = MODULE.initial_state(
            command, "initial", 1, command_sha="0" * 64,
            command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None,
            schema_bindings=bindings,
        )
        assert MODULE._bound_schemas(command, state)[0] == bound_provider
        snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
        state.update({
            "candidate_recognized": True, "candidate_source": "provider_success",
            "result_available": True, "result_sha256": "b" * 64,
            "candidate_worktree_sha256": snapshot["sha256"],
            "candidate_worktree_entries": snapshot["entries"],
        })
        MODULE._bound_candidate_worktree(state, command)
        (repo / "drift.txt").write_text("drift", encoding="utf-8")
        try:
            MODULE._bound_candidate_worktree(state, command)
        except MODULE.DispatchError as exc:
            assert str(exc) == "candidate worktree binding changed"
        else:
            raise AssertionError("candidate worktree drift was accepted")
        bound_provider.write_text(
            bound_provider.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        try:
            MODULE._bound_schemas(command, state)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch schema binding changed"
        else:
            raise AssertionError("provider schema drift was accepted")

    check("schema and candidate worktree bindings reject drift", bound_schemas_and_candidate_worktree_fail_closed)

    def controller_preserves_outer_error_candidate() -> None:
        repo = root / "controller-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        job = root / "controller-job"; job.mkdir(mode=0o700)
        bin_dir = root / "controller-bin"; bin_dir.mkdir()
        events = [
            {"event": "init", "init": {}, "conversation_id": "conversation-1"},
            {"event": "result", "result": {
                "conversation_id": "conversation-1", "status": "ERROR",
                "structured_output": report(summary="Verified by worker prose"),
            }},
        ]
        fake = bin_dir / "agy"
        fake.write_text(
            "#!/bin/sh\nprintf '%s\\n' " + " ".join(
                shlex.quote(json.dumps(event)) for event in events
            ) + "\nexit 1\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "controller",
            "workdir": str(repo), "argv": ["agy", "--json-schema", str(provider), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 4, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
        MODULE.create_state(job, "initial", resume=False)
        lock = job / MODULE.LOCK_NAME
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600); fcntl.flock(descriptor, fcntl.LOCK_EX)
        prior_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{prior_path}"
        try:
            assert MODULE.controller(job, descriptor) == 25
            descriptor = -1
        finally:
            os.environ["PATH"] = prior_path
            if descriptor >= 0:
                os.close(descriptor)
        state, _raw, _sha = MODULE.load_state(job)
        assert state["status"] == "failed" and state["reason"] == "provider_terminal_error"
        assert state["exit_code"] == 25 and state["candidate_source"] == "provider_error"
        assert state["result_available"] and state["driver_disposition"] == "unreviewed"
        assert state["failure_stage"] is None and state["phase"] == "awaiting-verification"

    check("controller maps ERROR plus valid report to failed unreviewed exit 25", controller_preserves_outer_error_candidate)

    def invalid_error_and_cancelled_candidate_are_separate() -> None:
        cases = [
            ("error-missing", "ERROR", None, 4, "failed", "invalid_envelope", "none", "structured_output"),
            ("cancelled", "CANCELED", report(), 22, "cancelled", "provider_terminal_cancelled", "provider_cancelled", None),
        ]
        for label, outer_status, candidate, expected_exit, status, reason, source_name, stage_name in cases:
            repo = root / f"{label}-repo"; repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            job = root / f"{label}-job"; job.mkdir(mode=0o700)
            bin_dir = root / f"{label}-bin"; bin_dir.mkdir()
            events = [
                {"event": "init", "init": {}, "conversation_id": "conversation-1"},
                {"event": "result", "result": {
                    "conversation_id": "conversation-1", "status": outer_status,
                    "structured_output": candidate,
                }},
            ]
            fake = bin_dir / "agy"
            fake.write_text(
                "#!/bin/sh\nprintf '%s\\n' " + " ".join(
                    shlex.quote(json.dumps(event)) for event in events
                ) + "\nexit 1\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            command = {
                "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": label,
                "workdir": str(repo), "argv": ["agy", "--json-schema", str(provider), "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 4, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
                "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            MODULE.create_state(job, "initial", resume=False)
            assert run_controller(job, bin_dir) == expected_exit
            state, _raw, _sha = MODULE.load_state(job)
            assert (state["status"], state["reason"], state["candidate_source"], state["failure_stage"]) == (
                status, reason, source_name, stage_name,
            )
            if source_name == "provider_cancelled":
                assert state["result_available"] and not state["resume_available"] and not state["continue_available"]
            else:
                assert not state["result_available"] and state["exit_code"] == 4

    check("ERROR missing report is invalid while CANCELED report is preserved without continuation", invalid_error_and_cancelled_candidate_are_separate)

    def nonfinite_and_incomplete_framing_are_rejected() -> None:
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            try:
                MODULE.parse_json(b'{"value":' + constant + b"}", "fixture")
            except MODULE.DispatchError:
                pass
            else:
                raise AssertionError("non-finite JSON constant was accepted")
        incomplete = root / "incomplete.ndjson"
        incomplete.write_bytes(
            json.dumps({"event": "init", "init": {}, "conversation_id": "conversation-1"}).encode() + b"\n"
            + json.dumps({"event": "result", "result": {"status": "SUCCESS", "structured_output": report()}}).encode()
        )
        binding, outer, stage = MODULE._validate_terminal_envelope(
            incomplete, root / "incomplete.json", provider, SCHEMA,
        )
        assert binding is None and outer is None and stage == "framing"

    check("non-finite JSON and newline-incomplete terminal framing are rejected", nonfinite_and_incomplete_framing_are_rejected)

    def repair_failure_preserves_candidate_for_result_finalize_and_next_continue() -> None:
        def build(suffix: str) -> tuple[Path, Path, dict]:
            origin = root / f"repair-origin-{suffix}"; origin.mkdir()
            subprocess.run(["git", "init", "-q", str(origin)], check=True)
            subprocess.run(["git", "-C", str(origin), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(origin), "config", "user.name", "Fixture"], check=True)
            (origin / "base.txt").write_text("base", encoding="utf-8")
            subprocess.run(["git", "-C", str(origin), "add", "base.txt"], check=True)
            subprocess.run(["git", "-C", str(origin), "commit", "-qm", "base"], check=True)
            repo = root / f"repair-worktree-{suffix}"
            subprocess.run(["git", "-C", str(origin), "worktree", "add", "-q", "-b", f"fixture-{suffix}", str(repo)], check=True)
            repo = repo.resolve()
            job = root / f"repair-job-{suffix}"; job.mkdir(mode=0o700); job = job.resolve()
            bin_dir = root / f"repair-bin-{suffix}"; bin_dir.mkdir()
            counter = root / f"repair-count-{suffix}"
            success_events = [
                {"event": "init", "init": {}, "conversation_id": "repair-conversation"},
                {"event": "result", "result": {"conversation_id": "repair-conversation", "status": "SUCCESS", "structured_output": report()}},
            ]
            failed_events = [
                {"event": "init", "init": {}, "conversation_id": "repair-conversation"},
                {"event": "result", "result": {"conversation_id": "repair-conversation", "status": "ERROR", "structured_output": None}},
            ]
            fake = bin_dir / "agy"
            fake.write_text(
                "#!/bin/sh\nif [ -e " + shlex.quote(str(counter)) + " ]; then\n"
                + "printf '%s\\n' " + " ".join(shlex.quote(json.dumps(item)) for item in failed_events) + "\nexit 1\nfi\n"
                + "touch " + shlex.quote(str(counter)) + "\nprintf '%s\\n' "
                + " ".join(shlex.quote(json.dumps(item)) for item in success_events) + "\nexit 0\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            command = {
                "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"repair-{suffix}",
                "workdir": str(repo), "argv": ["agy", "--json-schema", str(provider), "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "project",
                "max_cycles": 3, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            MODULE.create_state(job, "initial", resume=False)
            assert run_controller(job, bin_dir) == 0
            first, _raw, first_sha = MODULE.load_state(job)
            original = {
                key: copy.deepcopy(first[key]) for key in (
                    "result_path", "result_sha256", "result_identity", "candidate_source",
                    "candidate_worktree_sha256", "candidate_worktree_entries",
                )
            }
            verification = {
                "schema_version": 2, "summary": "driver found a defect", "passed_checks": [],
                "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": first["result_sha256"], "coverage": "partial",
                "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
            }
            queued, _sha = MODULE.create_state(
                job, "conversation-continue", resume=True,
                approve_sha=first_sha, verification=verification,
            )
            assert all(queued[key] == original[key] for key in original)
            assert run_controller(job, bin_dir) == 4
            failed, _raw, failed_sha = MODULE.load_state(job)
            assert failed["phase"] == "repair-failed" and failed["continue_available"]
            assert all(failed[key] == original[key] for key in original)
            return job, bin_dir, {"state": failed, "sha": failed_sha, "verification": verification}

        continue_job, _bin, context = build("continue")
        preserved = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(continue_job)],
            check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(preserved.stdout)["summary"] == "candidate"
        next_state, _sha = MODULE.create_state(
            continue_job, "conversation-continue", resume=True,
            approve_sha=context["sha"], verification=context["verification"],
        )
        assert next_state["attempt"] == 3 and next_state["candidate_recognized"]

        finalize_job, _bin, context = build("finalize")
        finalized = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(finalize_job),
             "--approve-state-sha", context["sha"], "--assurance", "partially_verified"],
            input=json.dumps(context["verification"]).encode(), check=True, stdout=subprocess.PIPE,
        )
        public = json.loads(finalized.stdout)
        assert public["phase"] == "completed" and public["driver_disposition"] == "partially_verified"

    check("repair failure preserves exact candidate for result finalize and budgeted continue", repair_failure_preserves_candidate_for_result_finalize_and_next_continue)

    def local_terminal_side_paths_project_lifecycle_and_preserve_candidates() -> None:
        """All non-provider terminal paths use the same safe public projection."""
        def make_job(label: str) -> tuple[Path, Path, Path]:
            origin = root / f"projection-origin-{label}"; origin.mkdir()
            subprocess.run(["git", "init", "-q", str(origin)], check=True)
            subprocess.run(["git", "-C", str(origin), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(origin), "config", "user.name", "Fixture"], check=True)
            (origin / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(origin), "add", "base.txt"], check=True)
            subprocess.run(["git", "-C", str(origin), "commit", "-qm", "base"], check=True)
            repo = root / f"projection-repo-{label}"
            subprocess.run(["git", "-C", str(origin), "worktree", "add", "-q", "-b", f"projection-{label}", str(repo)], check=True)
            repo = repo.resolve()
            job = root / f"projection-job-{label}"; job.mkdir(mode=0o700); job = job.resolve()
            bin_dir = root / f"projection-bin-{label}"; bin_dir.mkdir()
            bound_provider = root / f"projection-provider-{label}.json"; provider_schema(bound_provider)
            events = [
                {"event": "init", "init": {}, "conversation_id": f"conversation-{label}"},
                {"event": "result", "result": {
                    "conversation_id": f"conversation-{label}", "status": "SUCCESS",
                    "structured_output": report(summary=f"candidate-{label}"),
                }},
            ]
            fake = bin_dir / "agy"
            fake.write_text(
                "#!/bin/sh\nprintf '%s\\n' " + " ".join(shlex.quote(json.dumps(item)) for item in events) + "\nexit 0\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            command = {
                "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"projection-{label}",
                "workdir": str(repo), "argv": ["agy", "--json-schema", str(bound_provider), "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "project",
                "max_cycles": 3, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            MODULE.create_state(job, "initial", resume=False)
            return job, bin_dir, bound_provider

        def candidate_continuation(label: str) -> tuple[Path, dict, str]:
            job, bin_dir, _provider = make_job(label)
            assert run_controller(job, bin_dir) == 0
            first, _raw, first_sha = MODULE.load_state(job)
            verification = {
                "schema_version": 2, "summary": "driver found a defect", "passed_checks": [],
                "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": first["result_sha256"], "coverage": "partial",
                "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": True,
            }
            queued, queued_sha = MODULE.create_state(
                job, "conversation-continue", resume=True,
                approve_sha=first_sha, verification=verification,
            )
            assert queued["status"] == "queued" and queued["candidate_recognized"]
            return job, copy.deepcopy(queued), queued_sha

        # No candidate: startup failure becomes a truthful attempt failure and
        # does not imply provider activity or an ineligible resume action.
        pre_job, _pre_bin, _pre_provider = make_job("pre")
        MODULE._terminalize_start_failure(pre_job)
        pre, _raw, pre_sha = MODULE.load_state(pre_job)
        assert (pre["status"], pre["phase"], pre["driver_disposition"], pre["next_action"]) == (
            "failed", "attempt-failed", "not_applicable", "blocked",
        )
        assert pre["last_activity"] is None and not pre["resume_available"] and not pre["continue_available"]
        public_pre = MODULE.public_status(pre, pre_sha)
        assert all(public_pre[key] == pre[key] for key in (
            "status", "phase", "driver_disposition", "next_action", "last_activity",
        ))

        # A startup failure after a queued continue restores the exact candidate
        # rather than mislabelling it as a repair failure.
        start_job, before_start, _sha = candidate_continuation("start")
        MODULE._terminalize_start_failure(start_job)
        started, _raw, started_sha = MODULE.load_state(start_job)
        for key in (
            "result_path", "result_sha256", "result_identity", "candidate_source",
            "candidate_worktree_sha256", "candidate_worktree_entries",
        ):
            assert started[key] == before_start[key]
        assert (started["status"], started["phase"], started["driver_disposition"], started["next_action"]) == (
            "failed", "awaiting-verification", "unreviewed", "driver_review",
        )
        assert not started["resume_available"] and started["continue_available"]
        public_started = MODULE.public_status(started, started_sha)
        assert public_started["candidate_source"] == before_start["candidate_source"]

        # A parent signal before the continuation controller starts is local,
        # not provider CANCELED; the exact candidate remains reviewable.
        signal_job, before_signal, _sha = candidate_continuation("signal")
        MODULE._terminalize_queued_signal(signal_job, 15)
        signalled, _raw, signal_sha = MODULE.load_state(signal_job)
        assert (signalled["status"], signalled["reason"], signalled["phase"], signalled["next_action"]) == (
            "failed", "interrupted", "awaiting-verification", "driver_review",
        )
        assert signalled["remote_cancel_unverified"] and not signalled["resume_available"]
        assert signalled["continue_available"]
        assert signalled["result_sha256"] == before_signal["result_sha256"]
        assert MODULE.public_status(signalled, signal_sha)["remote_cancel_unverified"]

        # A SHA-approved local cancellation takes the same candidate-preserving
        # projection, but makes no claim that the remote provider was cancelled.
        cancel_job, before_cancel, cancel_sha = candidate_continuation("approved-cancel")
        cancel_public: list[dict] = []
        original_print_json = MODULE.print_json
        MODULE.print_json = lambda value: cancel_public.append(value)
        try:
            assert MODULE.command_control(cancel_job, "cancel", cancel_sha, None) == 0
        finally:
            MODULE.print_json = original_print_json
        assert cancel_public[0]["status"] == "cancel-requested"
        assert run_controller(cancel_job, root) == 22
        cancelled, _raw, cancelled_sha = MODULE.load_state(cancel_job)
        assert (cancelled["status"], cancelled["reason"], cancelled["phase"], cancelled["next_action"]) == (
            "cancelled", "cancelled", "awaiting-verification", "driver_review",
        )
        assert not cancelled["resume_available"] and not cancelled["continue_available"]
        assert not cancelled["remote_cancel_unverified"]
        assert cancelled["result_sha256"] == before_cancel["result_sha256"]
        assert MODULE.public_status(cancelled, cancelled_sha)["candidate_recognized"]

        # Binding failure retains a forensic candidate, but it cannot advertise a
        # readable report or invite driver review after its schema binding drifts.
        binding_job, before_binding, _sha = candidate_continuation("binding")
        binding_command, _raw, _identity = MODULE.load_command(binding_job)
        binding_provider = Path(binding_command["argv"][binding_command["argv"].index("--json-schema") + 1])
        binding_provider.write_text(binding_provider.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        lock = binding_job / MODULE.LOCK_NAME
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600); fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            assert MODULE.controller(binding_job, descriptor) == 20
            descriptor = -1
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        bound, _raw, bound_sha = MODULE.load_state(binding_job)
        assert (bound["status"], bound["phase"], bound["assurance"], bound["failure_stage"], bound["next_action"]) == (
            "failed", "blocked", "blocked", "binding_failure", "blocked",
        )
        for key in (
            "result_path", "result_sha256", "result_identity", "candidate_source",
            "candidate_worktree_sha256", "candidate_worktree_entries",
        ):
            assert bound[key] == before_binding[key]
        assert bound["candidate_recognized"] and not bound["result_available"]
        assert bound["driver_disposition"] == "unreviewed"
        assert not bound["resume_available"] and not bound["continue_available"]
        assert (bound["worktree_reconciliation"], bound["worktree_changes_present"], bound["worktree_changed_since_dispatch"]) == (
            "available", False, False,
        )
        public_bound = MODULE.public_status(bound, bound_sha)
        assert all(public_bound[key] == bound[key] for key in (
            "candidate_recognized", "candidate_source", "result_available",
            "driver_disposition", "failure_stage", "next_action",
            "worktree_reconciliation", "worktree_changes_present", "worktree_changed_since_dispatch",
        ))
        unreadable = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(binding_job)],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert unreadable.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not unreadable.stdout
        for mutation in (
            {**bound, "result_available": True},
            {**bound, "failure_stage": None},
            {**bound, "next_action": "driver_review"},
            {**bound, "continue_available": True},
            {**bound, "worktree_reconciliation": "unavailable"},
        ):
            try:
                MODULE.validate_state(mutation)
            except MODULE.DispatchError:
                pass
            else:
                raise AssertionError("invalid inaccessible-candidate state was accepted")

        # An unavailable controller is preserve-only: no stale repairing phase,
        # no resume/continue claim, and the candidate remains visible to review.
        orphan_job, before_orphan, _sha = candidate_continuation("orphan")
        captured: list[dict] = []
        original_print_json = MODULE.print_json
        MODULE.print_json = lambda value: captured.append(value)
        try:
            assert MODULE.command_status(orphan_job) == 0
        finally:
            MODULE.print_json = original_print_json
        orphan, _raw, orphan_sha = MODULE.load_state(orphan_job)
        assert (orphan["status"], orphan["phase"], orphan["driver_disposition"], orphan["next_action"]) == (
            "orphaned", "awaiting-verification", "unreviewed", "driver_review",
        )
        assert not orphan["resume_available"] and not orphan["continue_available"]
        assert orphan["result_sha256"] == before_orphan["result_sha256"]
        preserved = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(orphan_job)],
            check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(preserved.stdout)["summary"] == "candidate-orphan"
        public_orphan = MODULE.public_status(orphan, orphan_sha)
        assert captured == [public_orphan]
        assert all(public_orphan[key] == orphan[key] for key in (
            "status", "phase", "driver_disposition", "next_action", "continue_available",
        ))

    check("local terminal side paths project coherent JSON and preserve queued candidates", local_terminal_side_paths_project_lifecycle_and_preserve_candidates)

    def lifecycle_cycle_ranges_match_workflow_contract() -> None:
        assert MODULE._valid_max_cycles("legacy", 1) and not MODULE._valid_max_cycles("legacy", 2)
        for workflow in ("explore", "task"):
            assert MODULE._valid_max_cycles(workflow, 1)
            assert MODULE._valid_max_cycles(workflow, 2)
            assert not MODULE._valid_max_cycles(workflow, 3)
        assert all(MODULE._valid_max_cycles("project", item) for item in range(1, 6))
        assert not MODULE._valid_max_cycles("project", 6)

    check("explore and task allow two cycles while project allows five and legacy one", lifecycle_cycle_ranges_match_workflow_contract)

    def legacy_candidate_read_is_nonmutating_and_approved_finalize_upgrades_atomically() -> None:
        repo = root / "legacy-upgrade-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        job = root / "legacy-upgrade-job"; job.mkdir(mode=0o700); job = job.resolve()
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "legacy-upgrade",
            "workdir": str(repo), "argv": ["agy", "--json-schema", str(provider), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 10, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        command_raw, _command_sha = MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
        _loaded, _raw, command_identity = MODULE.load_command(job)
        state = MODULE.initial_state(
            command, "initial", 1, command_sha=MODULE.digest(command_raw),
            command_identity=command_identity, stage_sha=None, stage_identity=None,
            schema_bindings=MODULE._schema_bindings(command),
        )
        result_path = job / "envelope.json"
        candidate_raw = json.dumps(report(), ensure_ascii=True, indent=2).encode("ascii") + b"\n"
        result_path.write_bytes(candidate_raw); result_path.chmod(0o600)
        _bound, result_info = MODULE.read_regular(result_path, 1024 * 1024, "fixture")
        state.update({
            "status": "succeeded", "exit_code": 0, "finished_epoch": 1.0,
            "conversation_id": "legacy-conversation", "result_path": str(result_path),
            "result_sha256": MODULE.digest(candidate_raw), "result_identity": list(MODULE._identity(result_info)),
            "phase": None, "assurance": None,
        })
        state["schema_version"] = 4
        for key in MODULE.STATE_V5_FIELDS:
            state.pop(key)
        old_raw, old_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        loaded, _raw, read_sha = MODULE.read_state_snapshot(job)
        assert read_sha == old_sha and loaded["candidate_recognized"]
        assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
        verification = {
            "schema_version": 2, "summary": "driver verified", "passed_checks": ["fixture"],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": loaded["result_sha256"], "coverage": "complete",
            "verified_findings": 1, "unresolved_gaps": 0, "diff_review_complete": True,
        }
        completed = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", old_sha, "--assurance", "verified"],
            input=json.dumps(verification).encode(), check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(completed.stdout)["driver_disposition"] == "verified"
        upgraded = json.loads((job / MODULE.STATE_NAME).read_text(encoding="utf-8"))
        assert upgraded["schema_version"] == 5 and upgraded["previous_state_sha256"] == old_sha
        assert upgraded["candidate_worktree_sha256"] and upgraded["provider_schema_sha256"]

    check("legacy candidate status read is nonmutating and approved finalize writes bound v5", legacy_candidate_read_is_nonmutating_and_approved_finalize_upgrades_atomically)

    def preflight_rejections_never_invoke_provider() -> None:
        job = root / "controller-job"
        state, _raw, state_sha = MODULE.load_state(job)
        command, _command_raw, _identity = MODULE.load_command(job)
        marker = root / "preflight-provider-called"
        no_call_bin = root / "preflight-bin"; no_call_bin.mkdir()
        fake = no_call_bin / "agy"
        fake.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nexit 99\n", encoding="utf-8")
        fake.chmod(0o755)
        verification = {
            "schema_version": 2, "summary": "driver defect", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        prior_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{no_call_bin}{os.pathsep}{prior_path}"
        try:
            for approved, candidate_sha in (("0" * 64, state["result_sha256"]), (state_sha, "f" * 64)):
                attempt = copy.deepcopy(verification); attempt["candidate_sha256"] = candidate_sha
                try:
                    MODULE.create_state(
                        job, "conversation-continue", resume=True,
                        approve_sha=approved, verification=attempt,
                    )
                except MODULE.DispatchError:
                    pass
                else:
                    raise AssertionError("stale or wrong-candidate preflight was accepted")
            (Path(command["workdir"]) / "drift.txt").write_text("drift", encoding="utf-8")
            try:
                MODULE.create_state(
                    job, "conversation-continue", resume=True,
                    approve_sha=state_sha, verification=verification,
                )
            except MODULE.DispatchError as exc:
                assert "worktree binding changed" in str(exc)
            else:
                raise AssertionError("worktree-drift preflight was accepted")
        finally:
            os.environ["PATH"] = prior_path
        assert not marker.exists()
        malformed = copy.deepcopy(verification); malformed.pop("coverage")
        try:
            MODULE._validate_verification(malformed)
        except MODULE.DispatchError:
            pass
        else:
            raise AssertionError("malformed v2 verification was accepted")

    check("stale SHA wrong candidate malformed v2 and worktree drift stop before provider", preflight_rejections_never_invoke_provider)

    def verification_v2_requires_driver_review_for_task() -> None:
        value = {
            "schema_version": 2, "summary": "driver evidence", "passed_checks": ["unit"],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": "a" * 64,
            "coverage": "complete", "verified_findings": 0, "unresolved_gaps": 0,
            "diff_review_complete": False,
        }
        assert not MODULE._verification_is_verified(value, "task")
        value["diff_review_complete"] = True
        assert MODULE._verification_is_verified(value, "task")

    check("verification v2 does not infer task driver review", verification_v2_requires_driver_review_for_task)

    def v1_verification_never_authorizes_and_explore_needs_coverage() -> None:
        v1 = {
            "schema_version": 1, "summary": "legacy", "passed_checks": ["unit"],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
        }
        assert not MODULE._verification_is_verified(v1, "task")
        explore = {
            "schema_version": 2, "summary": "coverage", "passed_checks": [],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": "c" * 64, "coverage": "complete",
            "verified_findings": 1, "unresolved_gaps": 0, "diff_review_complete": False,
        }
        assert MODULE._verification_is_verified(explore, "explore")
        explore["unresolved_gaps"] = 1
        assert not MODULE._verification_is_verified(explore, "explore")

    check("v1 cannot verify and explore requires complete gap-free coverage", v1_verification_never_authorizes_and_explore_needs_coverage)

print("PASS: remediation controller focused checks")
