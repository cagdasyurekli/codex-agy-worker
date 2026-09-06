#!/usr/bin/env python3
"""Offline dispatcher lifecycle coverage for advisory self-verification."""
from __future__ import annotations

import hashlib
import contextlib
import io
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills/agy-worker/runtime/scripts/agy_dispatch.py"
SPEC = importlib.util.spec_from_file_location("dispatch_self_verify_lifecycle", SOURCE)
assert SPEC is not None and SPEC.loader is not None
DISPATCH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DISPATCH
SPEC.loader.exec_module(DISPATCH)


def report() -> dict:
    return {"status": "completed", "summary": "candidate", "files_changed": [],
            "commands_run": [], "tests_run": [], "risks": [], "open_questions": [],
            "confidence": 0.5, "requires_human": False}


class SelfVerificationLifecycle(unittest.TestCase):
    def fixture(self, temporary: str, *, requested: list[str] | None = None,
                allow: bool = True, scoped: bool = False, optional_only: bool = False,
                manifest_seconds: int = 10, command_schema: int = 10) -> tuple[Path, str, Path]:
        root = Path(temporary).resolve()
        root.mkdir(mode=0o700, exist_ok=True)
        owner = root / "owner"; owner.mkdir(mode=0o700)
        subprocess.run(["git", "init", "-q", str(owner)], check=True)
        subprocess.run(["git", "-C", str(owner), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(owner), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(owner), "commit", "--allow-empty", "-qm", "fixture"], check=True)
        worktree = root / "candidate"
        subprocess.run(["git", "-C", str(owner), "worktree", "add", "-q", "-b", "fixture-work", str(worktree)], check=True)
        (worktree / "check.py").write_text("# approved fixture\n", encoding="utf-8")
        (worktree / "private.txt").write_text("must not be copied\n", encoding="utf-8")
        job = root / "job"; job.mkdir(mode=0o700)
        schema = root / "provider-schema.json"
        schema.write_bytes((ROOT / "skills/agy-worker/runtime/schemas/worker-result.provider.schema.json").read_bytes())
        schema.chmod(0o600)
        manifest_value = {"schema_version": 1, "kind": "agy-worker-self-verification",
                          "max_seconds": manifest_seconds, "checks": [{"id": "required", "argv": ["/usr/bin/python3", "-V"],
                          "required": not optional_only, "timeout_seconds": 5, "output_limit_bytes": 1024},
                          {"id": "optional", "argv": ["/usr/bin/python3", "-V"],
                          "required": False, "timeout_seconds": 5, "output_limit_bytes": 1024}]}
        manifest = job / "manifest.json"; raw_manifest = DISPATCH.canonical(manifest_value)
        manifest.write_bytes(raw_manifest); manifest.chmod(0o600); info = manifest.stat()
        readable = DISPATCH._scan_readable_worktree(str(worktree))
        readable_digest = DISPATCH._manifest_digest(readable)
        scope = None
        if scoped:
            scope = job / "scope.json"
            scope_raw = DISPATCH.canonical({"schema_version": 1, "kind": "agy-worker-provider-scope",
                "read": [{"path": "check.py", "kind": "file"}], "write": []})
            scope.write_bytes(scope_raw); scope.chmod(0o600)
            scope_info = scope.stat()
            selected = DISPATCH._build_selected_content_manifest(worktree, DISPATCH._parse_provider_scope(scope_raw))
            approved_transmission = DISPATCH._compute_transmission_sha256(
                DISPATCH._canonical_digest(DISPATCH._parse_provider_scope(scope_raw)),
                readable_digest, DISPATCH._selected_content_digest(selected))
        if command_schema not in {9, 10}:
            raise ValueError("fixture command schema is unsupported")
        command = {"schema_version": command_schema, "kind": "agy-worker-dispatch-command", "job_id": "fixture",
            "workdir": str(worktree), "argv": ["agy", "--json-schema", str(schema), "--print", "task"], "agy_version": "1.1.22", "agy_version_observed": True,
            "idle_seconds": 1, "hard_seconds": 2, "max_seconds": 30, "notice_seconds": 3, "stage_dir": None, "stage_file": None,
            "child_umask": "022", "resume_prompt": "resume", "continue_prompt": "continue", "selection_path": None,
            "selection_sha256": None, "selection_identity": None, "provider_env": [],
            "provider_scope_path": None if scope is None else str(scope),
            "provider_scope_sha256": None if scope is None else DISPATCH.digest(scope_raw),
            "provider_scope_identity": None if scope is None else list(DISPATCH._identity(scope_info)),
            "approved_transmission_sha256": None if scope is None else approved_transmission,
            "approved_whole_worktree_sha256": readable_digest if scope is None else None,
            "boost": False, "boost_policy_sha256": None, "approved_boost_risk_sha256": None,
            "allow_self_verification": allow, "self_verification_manifest_path": str(manifest) if allow else None,
            "self_verification_manifest_sha256": DISPATCH.digest(raw_manifest) if allow else None,
            "self_verification_manifest_identity": list(DISPATCH._identity(info)) if allow else None,
            "allow_scoped_repair": False, "workflow": "task", "max_cycles": 2, "repair_authority_sha256": None}
        if command_schema == 10:
            command["provider_isolation"] = "session"
            if scope is None:
                command["approved_whole_worktree_sha256"] = (
                    DISPATCH._compute_provider_launch_approval_sha256("session", readable_digest)
                )
            else:
                command["approved_transmission_sha256"] = DISPATCH._bound_transmission_sha256(
                    command, DISPATCH._canonical_digest(DISPATCH._parse_provider_scope(scope_raw)),
                    readable_digest, DISPATCH._selected_content_digest(selected),
                )
        DISPATCH.write_atomic(job, DISPATCH.COMMAND_NAME, command)
        state, _sha = DISPATCH.create_state(job, "initial", resume=False)
        envelope = job / "envelope.json"; payload = json.dumps({**report(), "requested_check_ids": requested or []}, sort_keys=True).encode() + b"\n"
        envelope.write_bytes(payload); envelope.chmod(0o600)
        snapshot = DISPATCH._worktree_snapshot(str(worktree)); assert snapshot is not None
        bound, envelope_info = DISPATCH.read_regular(envelope, 65536, "fixture envelope")
        state.update({"status": "succeeded", "exit_code": 0, "finished_epoch": 1.0, "conversation_id": "fixture",
            "result_path": str(envelope), "result_sha256": DISPATCH.digest(bound), "result_identity": list(DISPATCH._identity(envelope_info)),
            "candidate_recognized": True, "candidate_source": "provider_success", "result_available": True,
            "candidate_worktree_sha256": snapshot["sha256"], "candidate_worktree_entries": snapshot["entries"],
            "driver_disposition": "unreviewed", "phase": "awaiting-verification", "assurance": "pending",
            "continue_available": False, "resume_available": False, "next_action": "driver_review"})
        if scoped:
            state.update({"selected_content_sha256": DISPATCH._selected_content_digest(selected), "selected_file_count": 1,
                          "selected_tree_count": 0})
        _raw, sha = DISPATCH.write_atomic(job, DISPATCH.STATE_NAME, state)
        DISPATCH.load_state(job)
        return job, sha, worktree

    def invoke(self, job: Path, sha: str, results):
        with mock.patch.object(DISPATCH.SELF_VERIFICATION, "run_checks", return_value=results):
            with contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
                return DISPATCH.SELF_VERIFICATION.command_self_verify(
                    DISPATCH.LEGACY_API, job, sha, "text",
                    containment=SimpleNamespace(require_supported_host=lambda: None, ContainmentError=OSError))

    def test_candidate_outcomes_are_advisory_and_do_not_finalize(self):
        with tempfile.TemporaryDirectory() as temporary:
            for outcome in ("passed", "failed", "not-run"):
                with self.subTest(outcome=outcome):
                    job, sha, _worktree = self.fixture(str(Path(temporary) / outcome))
                    result = self.invoke(job, sha, [DISPATCH.SELF_VERIFICATION.CheckResult("required", outcome, 0, 0, 0, 0)])
                    state, _raw, _sha = DISPATCH.load_state(job)
                    command, _command_raw, _command_identity = DISPATCH.load_command(job)
                    self.assertEqual(result, 0)
                    self.assertEqual((state["schema_version"], command["schema_version"], command["provider_isolation"]),
                                     (13, 10, "session"))
                    self.assertEqual((state["status"], state["phase"], state["driver_disposition"]),
                                     ("succeeded", "awaiting-verification", "unreviewed"))
                    self.assertEqual(state["self_verification_run"], state["attempt"])

    def test_v9_command_remains_eligible_after_state_upgrade(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, sha, _worktree = self.fixture(temporary, command_schema=9)
            state, _raw, _state_sha = DISPATCH.load_state(job)
            command, command_raw, _command_identity = DISPATCH.load_command(job)
            self.assertEqual((state["schema_version"], command["schema_version"]), (13, 9))
            self.assertNotIn("provider_isolation", json.loads(command_raw))
            self.assertEqual(
                self.invoke(job, sha, [DISPATCH.SELF_VERIFICATION.CheckResult("required", "passed", 0, 0, 0, 0)]),
                0,
            )
            current, _raw, _state_sha = DISPATCH.load_state(job)
            self.assertEqual((current["schema_version"], current["phase"]), (13, "awaiting-verification"))

    def test_attempt_is_single_use_default_off_and_unknown_ids_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, sha, _worktree = self.fixture(temporary)
            self.invoke(job, sha, [DISPATCH.SELF_VERIFICATION.CheckResult("required", "passed", 0, 0, 0, 0)])
            _state, _raw, current_sha = DISPATCH.load_state(job)
            with self.assertRaisesRegex(DISPATCH.DispatchError, "unavailable"):
                self.invoke(job, current_sha, [])
            disabled, disabled_sha, _ = self.fixture(str(Path(temporary) / "disabled"), allow=False)
            with self.assertRaisesRegex(DISPATCH.DispatchError, "unavailable"):
                self.invoke(disabled, disabled_sha, [])
            unknown, unknown_sha, _ = self.fixture(str(Path(temporary) / "unknown"), requested=["unknown"])
            with self.assertRaises(DISPATCH.SELF_VERIFICATION.VerificationError):
                self.invoke(unknown, unknown_sha, [])

    def test_recovery_charges_shared_budget_and_scoped_copy_omits_private_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, sha, worktree = self.fixture(temporary, scoped=True)
            with mock.patch.object(DISPATCH.SELF_VERIFICATION, "run_checks", side_effect=RuntimeError("controlled")):
                self.assertEqual(DISPATCH.SELF_VERIFICATION.command_self_verify(DISPATCH.LEGACY_API, job, sha, "json", containment=mock.Mock()), 20)
            state, raw, _sha = DISPATCH.load_state(job)
            self.assertEqual(state["phase"], "awaiting-verification")
            self.assertGreaterEqual(state["self_verification_elapsed_seconds"], 0)
            copy = job / "manual-copy"
            DISPATCH.SELF_VERIFICATION._copy_candidate(DISPATCH.LEGACY_API, job, json.loads((job / DISPATCH.COMMAND_NAME).read_text()), state, copy)
            self.assertTrue((copy / "check.py").exists())
            self.assertFalse((copy / "private.txt").exists())

    def test_continue_reuses_only_current_advisory_feedback_without_stdin(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, sha, _worktree = self.fixture(temporary)
            self.invoke(job, sha, [DISPATCH.SELF_VERIFICATION.CheckResult("required", "passed", 0, 0, 0, 0)])
            _state, _raw, current_sha = DISPATCH.load_state(job)
            with mock.patch.object(DISPATCH, "spawn", return_value=17) as spawn, \
                    mock.patch.object(sys, "stdin") as stdin:
                self.assertEqual(DISPATCH.command_continue(job, current_sha, None, use_self_verification=True), 17)
            stdin.read.assert_not_called()
            self.assertEqual(spawn.call_args.kwargs["verification"]["coverage"], "partial")
            with self.assertRaises(DISPATCH.DispatchError):
                DISPATCH.command_continue(job, sha, None, use_self_verification=True)

    def test_empty_optional_action_requires_supported_host_before_transition(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, sha, _worktree = self.fixture(temporary, optional_only=True)
            containment = SimpleNamespace(
                require_supported_host=mock.Mock(side_effect=OSError("unsupported")), ContainmentError=OSError)
            with self.assertRaises(DISPATCH.DispatchError), mock.patch.object(DISPATCH.SELF_VERIFICATION, "run_checks") as checks:
                DISPATCH.SELF_VERIFICATION.command_self_verify(DISPATCH.LEGACY_API, job, sha, "json", containment=containment)
            checks.assert_not_called()
            state, _raw, _sha = DISPATCH.load_state(job)
            self.assertEqual(state["phase"], "awaiting-verification")

    def test_copy_time_uses_total_budget_not_manifest_batch_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, sha, _worktree = self.fixture(temporary, manifest_seconds=1)
            clock = [0.0]
            seen = []
            def copied(_api, _job, _command, _state, destination):
                destination.mkdir(mode=0o700)
                clock[0] += 2.0
            def checks(_manifest, _requested, **kwargs):
                seen.append(kwargs["remaining"])
                return [DISPATCH.SELF_VERIFICATION.CheckResult("required", "passed", 0, 0, 0, 0)]
            native = SimpleNamespace(require_supported_host=lambda: None, ContainmentError=OSError)
            with mock.patch.object(DISPATCH.SELF_VERIFICATION, "_copy_candidate", side_effect=copied), \
                    mock.patch.object(DISPATCH.SELF_VERIFICATION, "run_checks", side_effect=checks), \
                    mock.patch.object(DISPATCH.SELF_VERIFICATION.time, "monotonic", side_effect=lambda: clock[0]), \
                    contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
                self.assertEqual(DISPATCH.SELF_VERIFICATION.command_self_verify(DISPATCH.LEGACY_API, job, sha, "text", containment=native), 0)
            state, _raw, _sha = DISPATCH.load_state(job)
            self.assertGreater(seen[0], 1.0)
            self.assertGreaterEqual(state["self_verification_elapsed_seconds"], 2.0)

    def test_copy_parent_drift_suppresses_feedback(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, sha, _worktree = self.fixture(temporary)
            def drift(_manifest, _requested, **kwargs):
                kwargs["copy_directory"].chmod(0o755)
                return [DISPATCH.SELF_VERIFICATION.CheckResult("required", "passed", 0, 0, 0, 0)]
            native = SimpleNamespace(require_supported_host=lambda: None, ContainmentError=OSError)
            with mock.patch.object(DISPATCH.SELF_VERIFICATION, "run_checks", side_effect=drift), \
                    contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
                self.assertEqual(DISPATCH.SELF_VERIFICATION.command_self_verify(DISPATCH.LEGACY_API, job, sha, "text", containment=native), 20)
            state, _raw, _sha = DISPATCH.load_state(job)
            self.assertEqual(state["phase"], "awaiting-verification")
            self.assertIsNone(state["verification_path"])
            self.assertIsNone(state["verification_sha256"])
            self.assertIsNone(state["verification_identity"])

    def test_persisted_self_verifying_state_recovers_with_capped_charge(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, _sha, _worktree = self.fixture(temporary)
            state, raw, _sha = DISPATCH.load_state(job)
            state.update({"phase": "self-verifying", "continue_available": False,
                          "self_verification_run": state["attempt"],
                          "self_verification_started_epoch": 100.0,
                          "self_verification_return_phase": "awaiting-verification",
                          "verification_path": None, "verification_sha256": None,
                          "verification_identity": None, "self_verification_elapsed_seconds": 28.0})
            DISPATCH.write_atomic(job, DISPATCH.STATE_NAME, state)
            with mock.patch.object(DISPATCH.time, "time", return_value=110.0), \
                    contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
                self.assertEqual(DISPATCH.command_status(job, "text"), 0)
            recovered, _raw, _sha = DISPATCH.load_state(job)
            self.assertEqual(recovered["phase"], "awaiting-verification")
            self.assertEqual(recovered["self_verification_elapsed_seconds"], 30.0)
            self.assertEqual(recovered["check_summary"], "Self-verification was interrupted; wall-clock time was conservatively charged.")
            self.assertIsNone(recovered["verification_path"])

    def test_active_verification_rejects_continue_and_exposes_wait_only_under_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, sha, _worktree = self.fixture(temporary)
            state, _raw, _sha = DISPATCH.load_state(job)
            state.update({"phase": "self-verifying", "continue_available": True,
                          "self_verification_run": state["attempt"],
                          "self_verification_started_epoch": 100.0,
                          "self_verification_return_phase": "awaiting-verification"})
            with self.assertRaises(DISPATCH.DispatchError):
                DISPATCH.validate_state(state)
            state["continue_available"] = False
            DISPATCH.write_atomic(job, DISPATCH.STATE_NAME, state)
            with DISPATCH.lifecycle_lock(job, blocking=True):
                current, _raw, current_sha = DISPATCH.load_state(job)
                actions = DISPATCH._available_actions(current, current_sha, 110.0)
                self.assertEqual([item["action"] for item in actions], ["wait"])
                with self.assertRaises(DISPATCH.DispatchError):
                    DISPATCH.command_result(job, "json")


    def test_verification_time_reduces_provider_recovery_and_extension_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            job, _sha, _worktree = self.fixture(temporary)
            state, _raw, _sha = DISPATCH.load_state(job)
            state.update({"elapsed_seconds": 2.0, "self_verification_elapsed_seconds": 27.0,
                          "self_verification_run": state["attempt"], "continue_available": True})
            self.assertTrue(DISPATCH._continue_is_eligible(state, 100.0))
            self.assertTrue(DISPATCH._restart_guard_accepts(state))
            state["self_verification_elapsed_seconds"] = 28.0
            self.assertFalse(DISPATCH._continue_is_eligible(state, 100.0))
            self.assertFalse(DISPATCH._restart_guard_accepts(state))
            state.update({"status": "failed", "candidate_recognized": False,
                          "resume_available": True})
            self.assertFalse(DISPATCH._resume_is_eligible(state, 100.0))
            state["self_verification_elapsed_seconds"] = 27.0
            self.assertTrue(DISPATCH._resume_is_eligible(state, 100.0))
            state.update({"status": "running", "started_epoch": 100.0,
                          "attempt_base_elapsed": 2.0, "hard_seconds": 3.0,
                          "progress_count": 1, "last_progress_epoch": 100.0})
            self.assertFalse(DISPATCH._extend_is_eligible(state, 100.0))
            state["self_verification_elapsed_seconds"] = 0.0
            self.assertTrue(DISPATCH._extend_is_eligible(state, 100.0))


if __name__ == "__main__":
    unittest.main()
