#!/usr/bin/env python3
"""Focused offline regression checks for the remediation controller contract."""
from __future__ import annotations

import copy
import contextlib
import fcntl
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills/agy-worker/runtime/scripts/agy_dispatch.py"
WORKTREE_SOURCE = SOURCE.with_name("agy_dispatch_worktree.py")
SCHEMA = ROOT / "skills/agy-worker/runtime/schemas/worker-result.schema.json"
PROVIDER_SCHEMA = ROOT / "skills/agy-worker/runtime/schemas/worker-result.provider.schema.json"
spec = importlib.util.spec_from_file_location("agy_dispatch_remediation", SOURCE)
MODULE = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(MODULE)

EXPECTED_CHECKS = 92
CHECKS_RUN = 0
FOCUSED_CHECK = os.environ.get("AGY_WORKER_REMEDIATION_FOCUSED_CHECK")


def check(label: str, action) -> None:
    global CHECKS_RUN
    if FOCUSED_CHECK is not None and label != FOCUSED_CHECK:
        return
    try:
        action()
    except Exception as exc:  # pragma: no cover - direct failure context
        raise AssertionError(f"{label}: {exc}") from exc
    CHECKS_RUN += 1
    print(f"ok: {label}")


def assert_symbolic_action_commands(actions: list[dict], expected: set[str]) -> None:
    by_action = {item["action"]: item for item in actions}
    assert expected <= set(by_action), (expected, by_action)
    for action in expected:
        command = by_action[action].get("command")
        assert isinstance(command, str)
        assert command.startswith(f"{MODULE.PUBLIC_LAUNCHER} {action} "), command


def provider_schema(path: Path) -> None:
    path.write_bytes(PROVIDER_SCHEMA.read_bytes())


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
    # Production invokes controller() in a dedicated child and deliberately
    # blocks lifecycle signals across its final snapshot until that process
    # exits.  Direct in-process fixtures must model that exit boundary so one
    # completed controller cannot leave the shared test process signal-masked.
    watched = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    prior_mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, watched)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{prior_path}"
    try:
        result = MODULE.controller(job, descriptor)
        descriptor = -1
        return result
    finally:
        os.environ["PATH"] = prior_path
        signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)
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

    def outer_status_requires_exact_provider_token() -> None:
        accepted = {
            "SUCCESS": "SUCCESS",
            "ERROR": "ERROR",
            "CANCELED": "CANCELLED",
            "CANCELLED": "CANCELLED",
        }
        for provided, expected in accepted.items():
            source = root / f"outer-status-{provided}.ndjson"
            envelope = root / f"outer-status-{provided}.json"
            stream(source, provided, report())
            binding, outer, stage = MODULE._validate_terminal_envelope(source, envelope, provider, SCHEMA)
            assert binding is not None and outer == expected and stage is None

        rejected = []
        for status in accepted:
            rejected.extend((
                (f"lowercase-{status}", status.lower()),
                (f"mixed-{status}", status.title()),
                (f"leading-whitespace-{status}", f" {status}"),
                (f"trailing-whitespace-{status}", f"{status} "),
            ))
        for label, provided in rejected:
            source = root / f"outer-status-rejected-{label}.ndjson"
            envelope = root / f"outer-status-rejected-{label}.json"
            stream(source, provided, report())
            binding, outer, stage = MODULE._validate_terminal_envelope(source, envelope, provider, SCHEMA)
            assert binding is None and outer is None and stage == "outer_status"
            assert not envelope.exists(), "an invalid status must not create a success candidate"

    check("outer status accepts only exact provider tokens", outer_status_requires_exact_provider_token)

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
        assert binding is None and outer == "SUCCESS" and stage == "missing_structured_output"

    check("framing and structured-output failures remain distinct", terminal_failure_stages_are_distinct)

    def only_two_optional_arrays_normalize() -> None:
        source = root / "critical-missing.ndjson"; envelope = root / "critical-missing.json"
        candidate = report(); candidate.pop("risks")
        stream(source, "SUCCESS", candidate)
        binding, outer, stage = MODULE._validate_terminal_envelope(source, envelope, provider, SCHEMA)
        assert binding is None and outer == "SUCCESS" and stage == "schema_rejection"

    check("only commands and tests arrays normalize", only_two_optional_arrays_normalize)

    def provider_and_canonical_schema_boundaries() -> None:
        provider_value = json.loads(PROVIDER_SCHEMA.read_text(encoding="utf-8"))
        canonical_value = json.loads(SCHEMA.read_text(encoding="utf-8"))
        assert provider_value["properties"]["summary"]["maxLength"] == 8192
        assert canonical_value["properties"]["summary"]["maxLength"] == 8192
        assert set(canonical_value["required"]) - set(provider_value["required"]) == {
            "commands_run", "tests_run",
        }
        assert set(provider_value["required"]) - set(canonical_value["required"]) == set()

        accepted = root / "summary-8192.ndjson"
        stream(accepted, "SUCCESS", report(summary="x" * 8192))
        binding, outer, stage = MODULE._validate_terminal_envelope(accepted, root / "summary-8192.json", provider, SCHEMA)
        assert binding is not None and outer == "SUCCESS" and stage is None
        rejected = root / "summary-8193.ndjson"
        stream(rejected, "SUCCESS", report(summary="x" * 8193))
        binding, outer, stage = MODULE._validate_terminal_envelope(rejected, root / "summary-8193.json", provider, SCHEMA)
        assert binding is None and outer == "SUCCESS" and stage == "schema_rejection"

    check("provider schema permits only bounded command/test ergonomics", provider_and_canonical_schema_boundaries)

    def invalid_report_encodings_types_and_size_fail_closed() -> None:
        wrong_type = root / "wrong-type.ndjson"
        stream(wrong_type, "SUCCESS", report(confidence="0.5"))
        binding, outer, stage = MODULE._validate_terminal_envelope(wrong_type, root / "wrong-type.json", provider, SCHEMA)
        assert binding is None and outer == "SUCCESS" and stage == "schema_rejection"

        nonfinite = root / "nonfinite.ndjson"
        nonfinite.write_bytes(
            b'{"event":"init","init":{},"conversation_id":"conversation-1"}\n'
            b'{"event":"result","result":{"conversation_id":"conversation-1","status":"SUCCESS","structured_output":'
            b'{"status":"completed","summary":"candidate","files_changed":[],"risks":[],"open_questions":[],"confidence":NaN,"requires_human":false}}}\n'
        )
        binding, outer, stage = MODULE._validate_terminal_envelope(nonfinite, root / "nonfinite.json", provider, SCHEMA)
        assert binding is None and outer is None and stage == "framing"

        invalid_utf8 = root / "invalid-utf8.ndjson"
        invalid_utf8.write_bytes(b'\xff\n')
        binding, outer, stage = MODULE._validate_terminal_envelope(invalid_utf8, root / "invalid-utf8.json", provider, SCHEMA)
        assert binding is None and outer is None and stage == "framing"

        oversized = root / "oversized.ndjson"
        stream(oversized, "SUCCESS", report(summary="x" * (1024 * 1024)))
        binding, outer, stage = MODULE._validate_terminal_envelope(oversized, root / "oversized.json", provider, SCHEMA)
        assert binding is None and stage in {"framing", "schema_rejection"}

    check("wrong type NaN invalid UTF-8 and oversized reports fail closed", invalid_report_encodings_types_and_size_fail_closed)

    def text_projection_is_three_line_private_and_driver_owned() -> None:
        command = {
            "workdir": str(root), "job_id": "text-job", "idle_seconds": 1,
            "hard_seconds": 2, "max_seconds": 3, "workflow": "task", "max_cycles": 2,
        }
        state = MODULE.initial_state(
            command, "initial", 1, command_sha="a" * 64,
            command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None,
            state_schema=8,
        )
        state.update({
            "status": "failed", "reason": "provider_terminal_error", "exit_code": 25,
            "workdir": "/private/repo-path", "conversation_id": "conversation-secret",
            "result_path": "/private/worker-prose.json", "check_summary": "raw worker prose secret",
            "result_sha256": "a" * 64, "result_identity": [1, 2, 3, 4, 5],
            "candidate_recognized": True, "candidate_source": "provider_error", "result_available": True,
            "driver_disposition": "unreviewed", "next_action": "driver_review",
            "worktree_reconciliation": "available", "worktree_changes_present": True,
            "worktree_changed_since_dispatch": False,
        })
        def render_text() -> str:
            captured = io.BytesIO()
            original_stdout = MODULE.sys.stdout
            wrapper = io.TextIOWrapper(captured, encoding="utf-8")
            MODULE.sys.stdout = wrapper
            try:
                MODULE.print_text_status(state, "b" * 64)
                wrapper.flush()
            finally:
                MODULE.sys.stdout = original_stdout
            return captured.getvalue().decode("utf-8")

        text = render_text()
        assert len(text.splitlines()) == 3
        assert text.splitlines() == [
            "Provider attempt: failed; reason: provider_terminal_error; failure stage: binding_failure; bound result available: no; driver disposition: unreviewed.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: fresh-attempt restart: "$PIPELINE/agy-worker.sh" restart --job-id text-job --approve-state-sha '
            + "b" * 64 + " --format text.",
        ]
        state["worktree_changes_present"] = False
        clean_text = render_text()
        assert clean_text == text
        for disposition in (
            "verified", "partially_verified", "rejected", "blocked",
        ):
            state["driver_disposition"] = disposition
            first_line = render_text().splitlines()[0]
            assert first_line == (
                "Provider attempt: failed; reason: provider_terminal_error; failure stage: binding_failure; "
                f"bound result available: no; driver disposition: {disposition}."
            )
        state["driver_disposition"] = "unreviewed"
        public = MODULE.public_status(state, "b" * 64)
        assert public["next_action_command"] == (
            '"$PIPELINE/agy-worker.sh" restart --job-id text-job --approve-state-sha ' + "b" * 64 + " --format text"
        )
        assert public["assurance"] is None and public["controller_phase"] == "awaiting-verification"
        assert public["available_actions"][0]["action"] == public["next_action"] == "restart"
        for action in ("driver_finalize", "blocked", "none"):
            state["next_action"] = action
            assert MODULE.public_status(state, "b" * 64)["next_action_command"] == (
                '"$PIPELINE/agy-worker.sh" restart --job-id text-job --approve-state-sha ' + "b" * 64 + " --format text"
            )
        for secret in ("/private/repo-path", "conversation-secret", "worker-prose.json", "raw worker prose secret"):
            assert secret not in text
        parsed = MODULE.parser().parse_args([
            "status", "--job-dir", str(root), "--format", "text",
        ])
        assert parsed.format == "text"

    check("text status is private and never promotes worker completion prose", text_projection_is_three_line_private_and_driver_owned)

    def control_formats_and_resume_approval_are_private_and_pre_dispatch() -> None:
        log_root = root / "control-logs"; log_root.mkdir(mode=0o700)
        repo = root / "control-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        job = log_root / "format-job"; job.mkdir(mode=0o700)
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "format-job",
            "workdir": str(repo), "argv": ["agy", "--json-schema", str(PROVIDER_SCHEMA), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 1, "hard_seconds": 2, "max_seconds": 3, "notice_seconds": 1,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
        _state, _initial_sha = MODULE.create_state(job, "initial", resume=False)
        worker = ROOT / "skills/agy-worker/runtime/agy-worker.sh"
        environment = {**os.environ, "AGY_WORKER_LOG_DIR": str(log_root)}
        def invoke(*arguments: str) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run([str(worker), *arguments], env=environment, input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        default = invoke("status", "--job-id", "format-job")
        explicit = invoke("status", "--job-id", "format-job", "--format", "json")
        assert default.returncode == explicit.returncode == 0 and default.stdout == explicit.stdout
        public = json.loads(default.stdout)
        sha = public["state_sha256"]
        assert public["status"] == "orphaned"
        assert public["next_action"] == "none" and public["next_action_command"] is None
        assert public["available_actions"] == [] and public["assurance"] is None
        text = invoke("status", "--job-id", "format-job", "--format", "text")
        assert text.returncode == 0 and len(text.stdout.decode("utf-8").splitlines()) == 3
        waited = invoke("wait", "--job-id", "format-job", "--after-state-sha", sha, "--timeout", "1s", "--format", "text")
        assert waited.returncode == 0 and len(waited.stdout.decode("utf-8").splitlines()) == 3
        missing = invoke("resume", "--job-id", "format-job")
        stale = invoke("resume", "--job-id", "format-job", "--approve-state-sha", "0" * 64)
        expected = f'"$PIPELINE/agy-worker.sh" resume --job-id format-job --approve-state-sha {sha}'.encode("ascii")
        for outcome in (missing, stale):
            assert outcome.returncode == 21 and outcome.stdout == b"" and expected in outcome.stderr, (outcome.returncode, outcome.stdout, outcome.stderr)
            assert b"usage:" not in outcome.stderr and str(root).encode() not in outcome.stderr

        # The diagnostic is a real shell command for both the canonical source
        # runtime and a folder-only skill copy. Each runtime creates and reads
        # its own schema-bound job; neither launcher needs to be on PATH.
        def execute_rerun(pipeline: Path, outcome: subprocess.CompletedProcess[bytes], expected_command: str) -> None:
            rerun = outcome.stderr.decode("utf-8").split("rerun: ", 1)[1].strip()
            assert rerun == expected_command
            executed = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", rerun],
                env={
                    **os.environ,
                    "PATH": "/usr/bin:/bin",
                    "PIPELINE": str(pipeline),
                    "AGY_WORKER_LOG_DIR": str(log_root),
                },
                input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert executed.returncode == 21 and executed.stdout == b"", (
                pipeline, executed.returncode, executed.stdout, executed.stderr,
            )
            assert executed.stderr == b"agy-dispatch: only a terminal unsuccessful dispatch can continue\n", (
                pipeline, executed.stderr,
            )

        execute_rerun(worker.parent, stale, expected.decode("ascii"))
        copied_skill = root / "folder-copy" / "agy-worker"
        shutil.copytree(ROOT / "skills/agy-worker", copied_skill)
        resolved = subprocess.run(
            ["bash", str(copied_skill / "scripts/resolve-pipeline.sh")],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.decode("utf-8").strip()
        assert resolved == str((copied_skill / "runtime").resolve())
        copied_pipeline = Path(resolved)
        copied_source = copied_pipeline / "scripts/agy_dispatch.py"
        copied_spec = importlib.util.spec_from_file_location("agy_dispatch_folder_copy", copied_source)
        copied_module = importlib.util.module_from_spec(copied_spec)
        assert copied_spec.loader is not None
        copied_spec.loader.exec_module(copied_module)
        copied_job = log_root / "folder-format"; copied_job.mkdir(mode=0o700)
        copied_command = dict(command, job_id="folder-format")
        copied_module.write_atomic(copied_job, copied_module.COMMAND_NAME, copied_command)
        copied_module.create_state(copied_job, "initial", resume=False)
        copied_environment = {**os.environ, "AGY_WORKER_LOG_DIR": str(log_root)}
        copied_worker = copied_pipeline / "agy-worker.sh"
        copied_status = subprocess.run(
            [str(copied_worker), "status", "--job-id", "folder-format"],
            env=copied_environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert copied_status.returncode == 0, copied_status.stderr
        copied_sha = json.loads(copied_status.stdout)["state_sha256"]
        copied_stale = subprocess.run(
            [str(copied_worker), "resume", "--job-id", "folder-format", "--approve-state-sha", "0" * 64],
            env=copied_environment, input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        copied_expected = (
            f'"$PIPELINE/agy-worker.sh" resume --job-id folder-format '
            f"--approve-state-sha {copied_sha}"
        )
        assert copied_stale.returncode == 21 and copied_stale.stdout == b""
        execute_rerun(copied_pipeline, copied_stale, copied_expected)
        assert not (job / "stream.ndjson").exists()
        assert not (copied_job / "stream.ndjson").exists()

    check("control formats and stale reruns are private copyable and pre-provider", control_formats_and_resume_approval_are_private_and_pre_dispatch)

    def stale_approval_guidance_keeps_required_caller_inputs() -> None:
        state = {"job_id": "stale-guidance"}
        sha = "a" * 64
        assert str(MODULE._state_approval_error(state, sha, "resume")).endswith(
            f'"$PIPELINE/agy-worker.sh" resume --job-id stale-guidance --approve-state-sha {sha}'
        )
        assert str(MODULE._state_approval_error(state, sha, "continue")).endswith(
            f'--approve-state-sha {sha} < DRIVER_VERIFICATION_JSON'
        )
        assert str(MODULE._state_approval_error(state, sha, "finalize")).endswith(
            f'--approve-state-sha {sha} --assurance ASSURANCE < DRIVER_VERIFICATION_JSON'
        )
        extend = str(MODULE._state_approval_error(state, sha, "extend"))
        assert extend.endswith(f"--approve-state-sha {sha} --by DURATION")
        assert not any(token in extend for token in ("1s", "10m", "\n"))

    check("stale approval guidance includes only required placeholders and invents no values", stale_approval_guidance_keeps_required_caller_inputs)

    def state_v4_migrates_additively() -> None:
        command = {
            "workdir": str(root), "workflow": "legacy", "max_cycles": 1, "job_id": "legacy",
            "hard_seconds": 2, "max_seconds": 4, "idle_seconds": 1,
        }
        state = MODULE.initial_state(command, "initial", 1, command_sha="0" * 64, command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None, state_schema=8)
        state.update({"phase": None, "assurance": None})
        state["schema_version"] = 4
        for key in {*MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS, *MODULE.STATE_V10_FIELDS}:
            state.pop(key, None)
        migrated = MODULE.validate_state(state)
        assert migrated["candidate_source"] == "none"
        assert migrated["driver_disposition"] == "not_applicable"
        assert migrated["worktree_changes_present"] is None

    check("v4 state reads as additive current state", state_v4_migrates_additively)

    def v1_v3_v4_remain_read_compatible() -> None:
        command = {
            "workdir": str(root), "workflow": "legacy", "max_cycles": 1, "job_id": "legacy-read",
            "hard_seconds": 2, "max_seconds": 4, "idle_seconds": 1,
        }
        original = MODULE.initial_state(
            command, "initial", 1, command_sha="0" * 64,
            command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None,
            state_schema=8,
        )
        original.update({"phase": None, "assurance": None})
        v3 = copy.deepcopy(original); v3["schema_version"] = 3
        for key in {"provider_retry_after_seconds", "provider_retry_observed_epoch", *MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS, *MODULE.STATE_V10_FIELDS}:
            v3.pop(key, None)
        validated_v3 = MODULE.validate_state(v3)
        assert validated_v3["schema_version"] == 3
        assert validated_v3["phase"] is None and validated_v3["assurance"] is None
        v1 = copy.deepcopy(v3); v1["schema_version"] = 1
        for key in MODULE.STATE_PROJECT_FIELDS:
            v1.pop(key)
        validated_v1 = MODULE.validate_state(v1)
        assert validated_v1["schema_version"] == 1
        assert validated_v1["phase"] is None and validated_v1["assurance"] is None

    check("v1 v3 and v4 state snapshots remain read compatible", v1_v3_v4_remain_read_compatible)

    def v3_v4_last_success_is_unknown_bound_result_only() -> None:
        """A historical result remains readable, but never becomes a candidate."""
        def fixture(label: str) -> tuple[Path, Path, Path]:
            source_repo = root / f"legacy-prior-{label}-source"; source_repo.mkdir()
            subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
            (source_repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source_repo), "add", "base.txt"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "commit", "-qm", "base"], check=True)
            worktree = root / f"legacy-prior-{label}-worktree"
            subprocess.run(["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", f"legacy-prior-{label}", str(worktree)], check=True)
            worktree = worktree.resolve()
            job = root / f"legacy-prior-{label}-job"; job.mkdir(mode=0o700); job = job.resolve()
            bound_provider = root / f"legacy-prior-{label}-provider.json"; provider_schema(bound_provider)
            command = {
                "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"legacy-prior-{label}",
                "workdir": str(worktree), "argv": ["agy", "--json-schema", str(bound_provider), "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "project",
                "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            state, _sha = MODULE.create_state(job, "initial", resume=False)
            artifact = job / "historical-result.json"
            payload = json.dumps(report(summary=f"legacy-{label}"), indent=2).encode("utf-8") + b"\n"
            artifact.write_bytes(payload); artifact.chmod(0o600)
            _raw, info = MODULE.read_regular(artifact, 1024 * 1024, "legacy result")
            state.update({
                "schema_version": 4, "status": "succeeded", "finished_epoch": 1.0, "exit_code": 0,
                "result_path": None, "result_sha256": None, "result_identity": None,
                "last_success_path": str(artifact), "last_success_sha256": MODULE.digest(payload),
                "last_success_identity": list(MODULE._identity(info)),
                "phase": "awaiting-verification", "assurance": "pending", "resume_available": False,
            })
            for key in {*MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS, *MODULE.STATE_V10_FIELDS}:
                state.pop(key)
            MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            return job, worktree, artifact

        job, _worktree, _artifact = fixture("valid")
        state, _raw, sha = MODULE.load_state(job)
        assert not state["candidate_recognized"] and state["candidate_source"] == "none"
        public = MODULE.public_status(state, sha, job=job)
        assert public["legacy_result_provenance"] == "unknown_bound_legacy"
        assert public["driver_disposition"] == "not_applicable" and public["next_action"] == "result"
        assert public["candidate_sha256"] is None and public["result_available"] is False
        # V3/V4 historical evidence remains readable, but its missing V9 root
        # identity cannot authorize a fresh provider attempt.
        assert {item["action"] for item in public["available_actions"]} == {"result"}

        captured = io.BytesIO()
        original_stdout = MODULE.sys.stdout

        class _Stdout:
            buffer = captured

        MODULE.sys.stdout = _Stdout()
        try:
            MODULE.print_text_status(state, sha, job=job)
        finally:
            MODULE.sys.stdout = original_stdout
        assert captured.getvalue().decode("utf-8").splitlines() == [
            "Provider attempt: succeeded; reason: none; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: retrieve historical result evidence only with "$PIPELINE/agy-worker.sh" result --job-id legacy-prior-valid --format json; do not use it for Verification v2, continue, or finalize.',
        ]
        historical_verification = {
            "schema_version": 2, "summary": "driver must not promote history", "passed_checks": [],
            "failed_checks": ["historical-only"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["last_success_sha256"], "coverage": "partial",
            "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        assert MODULE._validate_verification(historical_verification) == historical_verification
        try:
            MODULE._require_current_candidate_verification(historical_verification, state)
        except MODULE.DispatchError:
            pass
        else:
            raise AssertionError("a historical last-success digest became Verification v2 input")

        # An active V3/V4 attempt can still carry the historical pointer, but
        # status/wait/cancel/extend must never reopen it.  A slow or failing
        # legacy binder is terminal-result work and cannot delay active control.
        active = dict(state)
        active.update({
            "status": "running", "controller_pid": 123, "finished_epoch": None,
            "exit_code": None, "started_epoch": time.time(), "phase": "repairing",
            "elapsed_seconds": 1.0, "attempt_base_elapsed": 1.0,
        })
        binder_calls = 0
        original_legacy_binder = MODULE._legacy_result_action_is_bound
        def slow_failing_legacy_binder(_job, _state):
            nonlocal binder_calls
            binder_calls += 1
            time.sleep(0.3)
            raise AssertionError("active status invoked the legacy result binder")
        MODULE._legacy_result_action_is_bound = slow_failing_legacy_binder
        started = time.monotonic()
        try:
            active_public = MODULE.public_status(active, sha, job=job)
        finally:
            MODULE._legacy_result_action_is_bound = original_legacy_binder
        assert time.monotonic() - started < 0.15
        assert binder_calls == 0
        assert [item["action"] for item in active_public["available_actions"]] == ["wait", "cancel"]
        assert_symbolic_action_commands(active_public["available_actions"], {"wait", "cancel"})

        delivered = subprocess.run([sys.executable, str(SOURCE), "result", "--job-dir", str(job)], check=True, stdout=subprocess.PIPE)
        assert json.loads(delivered.stdout)["summary"] == "legacy-valid"
        for label, mutation in (("missing", "missing"), ("tampered", "tampered"), ("boundary", "boundary")):
            bad_job, bad_worktree, artifact = fixture(label)
            if mutation == "missing":
                artifact.unlink()
            elif mutation == "tampered":
                artifact.write_bytes(b"{}\n"); artifact.chmod(0o600)
            else:
                marker = bad_worktree / ".git"
                marker.write_text("gitdir: /nonexistent\n", encoding="utf-8")
            bad_state, _bad_raw, bad_sha = MODULE.load_state(bad_job)
            bad_public = MODULE.public_status(bad_state, bad_sha, job=bad_job)
            assert "result" not in {
                item["action"] for item in bad_public["available_actions"]
            }
            rejected = subprocess.run(
                [sys.executable, str(SOURCE), "result", "--job-dir", str(bad_job)],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert rejected.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not rejected.stdout

    check("v3/v4 last_success stays unknown, reads only with bindings, and fails closed on drift", v3_v4_last_success_is_unknown_bound_result_only)

    def new_current_legacy_attempts_have_nonrepair_lifecycle() -> None:
        command = {
            "workdir": str(root), "workflow": "legacy", "max_cycles": 1,
            "job_id": "legacy-lifecycle", "hard_seconds": 2,
            "max_seconds": 4, "idle_seconds": 1,
        }
        for origin in ("initial", "conversation-resume", "fresh-restart"):
            state = MODULE.initial_state(
                command, origin, 2, command_sha="0" * 64,
                command_identity=(1, 2, 3, 4, 5), stage_sha=None,
                stage_identity=None, state_schema=8,
            )
            validated = MODULE.validate_state(copy.deepcopy(state))
            assert validated["phase"] == "dispatching"
            assert validated["assurance"] == "pending"

        restarted = MODULE.initial_state(
            command, "fresh-restart", 2, command_sha="0" * 64,
            command_identity=(1, 2, 3, 4, 5), stage_sha=None,
            stage_identity=None, state_schema=8,
        )
        invalid = copy.deepcopy(restarted)
        invalid["phase"] = "repairing"
        try:
            MODULE.validate_state(invalid)
        except MODULE.DispatchError as exc:
            assert str(exc) == "legacy lifecycle cannot continue as repair" or str(exc) == "active legacy lifecycle is invalid"
        else:
            raise AssertionError("legacy restart accepted a repair phase")

        terminal = copy.deepcopy(restarted)
        terminal.update({
            "status": "succeeded", "finished_epoch": 1.0, "exit_code": 0,
            "result_path": "/private/result.json", "result_sha256": "1" * 64,
            "result_identity": [1, 2, 3, 4, 5], "candidate_recognized": True,
            "candidate_source": "provider_success", "result_available": True,
            "driver_disposition": "unreviewed", "phase": "awaiting-verification",
            "assurance": "pending", "next_action": "driver_review",
        })
        MODULE.validate_state(copy.deepcopy(terminal))
        terminal["phase"] = "dispatching"
        try:
            MODULE.validate_state(terminal)
        except MODULE.DispatchError as exc:
            assert str(exc) == "legacy candidate lifecycle is invalid"
        else:
            raise AssertionError("terminal legacy candidate retained dispatching phase")

    check("new current legacy attempts expose a strict nonrepair lifecycle", new_current_legacy_attempts_have_nonrepair_lifecycle)

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

        # A nested repository's administrative directory is an attempt to
        # introduce a second Git authority below the bound worktree.  It must
        # fail closed rather than being silently skipped by the manifest.
        nested_admin = repo / "nested" / ".git"; nested_admin.mkdir(parents=True)
        nested_secret = nested_admin / "secret"; nested_secret.write_text("one", encoding="utf-8")
        nested_before = MODULE._worktree_snapshot(str(repo))
        nested_secret.write_text("two", encoding="utf-8")
        nested_after = MODULE._worktree_snapshot(str(repo))
        assert nested_before is None and nested_after is None, "nested .git administration was silently skipped"

    check("worktree reconciliation hashes outward symlinks and rejects nested Git administration", reconciliation_never_follows_outward_symlink)

    def controller_symlink_preflight_blocks_provider_for_every_workflow() -> None:
        """A scope escape must fail before the provider for every workflow."""
        def make_job(
            label: str, workflow: str, *, internal_link: bool, outward_link: bool,
        ) -> tuple[Path, Path, Path, Path]:
            source = root / f"symlink-source-{label}"; source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Fixture"], check=True)
            (source / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "base.txt"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "base"], check=True)
            if workflow == "project":
                repo = root / f"symlink-worktree-{label}"
                subprocess.run([
                    "git", "-C", str(source), "worktree", "add", "-q", "-b",
                    f"symlink-{label}", str(repo),
                ], check=True)
                repo = repo.resolve()
            else:
                repo = source
            if internal_link:
                target = repo / "inside.txt"; target.write_text("inside\n", encoding="utf-8")
                (repo / "inside-link").symlink_to(target.name)
            if outward_link:
                outside = root / f"symlink-outside-{label}"; outside.write_text("outside\n", encoding="utf-8")
                (repo / "escape").symlink_to(outside)
            job = root / f"symlink-job-{label}"; job.mkdir(mode=0o700)
            bin_dir = root / f"symlink-bin-{label}"; bin_dir.mkdir(mode=0o700)
            marker = root / f"symlink-provider-{label}"
            events = [
                {"event": "init", "init": {}, "conversation_id": f"symlink-{label}"},
                {"event": "result", "result": {
                    "conversation_id": f"symlink-{label}", "status": "SUCCESS",
                    "structured_output": report(summary=f"symlink-{label}"),
                }},
            ]
            fake = bin_dir / "agy"
            fake.write_text(
                "#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nprintf '%s\\n' "
                + " ".join(shlex.quote(json.dumps(event)) for event in events) + "\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            command = {
                "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"symlink-{label}",
                "workdir": str(repo), "argv": ["agy", "--json-schema", str(provider), "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": workflow,
                "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            if not outward_link or workflow != "project":
                MODULE.create_state(job, "initial", resume=False)
            return job, bin_dir, repo, marker

        for workflow in ("explore", "task", "project"):
            job, bin_dir, _repo, marker = make_job(
                f"outward-{workflow}", workflow, internal_link=False, outward_link=True,
            )
            if workflow == "project":
                try:
                    MODULE.create_state(job, "initial", resume=False)
                except MODULE.DispatchError as exc:
                    assert str(exc) == "project worktree has an outward symlink"
                else:
                    raise AssertionError("project outward symlink created dispatch state")
            else:
                assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
                state, _raw, _sha = MODULE.load_state(job)
                assert state["failure_stage"] == "binding_failure"
            assert not marker.exists(), workflow

        for workflow in ("explore", "task", "project"):
            job, bin_dir, _repo, marker = make_job(
                f"internal-{workflow}", workflow, internal_link=True, outward_link=False,
            )
            assert run_controller(job, bin_dir) == 0
            assert marker.exists(), workflow

    check("controller rejects outward symlinks before provider launch for every workflow while allowing internal links", controller_symlink_preflight_blocks_provider_for_every_workflow)

    def worktree_snapshot_is_readonly_and_disarms_repo_programs() -> None:
        repo = root / "safe-snapshot-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        (repo / ".gitattributes").write_text("tracked.txt filter=hostile diff=hostile\n", encoding="utf-8")
        (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", ".gitattributes", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        linked = root / "safe-snapshot-linked"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "safe-snapshot-linked", str(linked)], check=True)

        marker = root / "repo-program-ran"
        program = root / "repo-program.sh"
        program.write_text("#!/bin/sh\nprintf ran > \"$1\"\ncat\n", encoding="utf-8")
        program.chmod(0o700)
        indexes: dict[Path, tuple[Path, tuple[object, ...]]] = {}
        for checkout in (repo, linked):
            # A post-commit write gives the old status route a real refresh
            # opportunity before the identity comparison begins.
            (checkout / "tracked.txt").write_text("base\n", encoding="utf-8")
            index = Path(subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "--git-path", "index"],
                check=True, stdout=subprocess.PIPE,
            ).stdout.decode("utf-8", "strict").strip())
            if not index.is_absolute():
                index = checkout / index
            before_info = index.lstat(); before_bytes = index.read_bytes()
            before = (
                before_bytes, before_info.st_dev, before_info.st_ino, before_info.st_mode,
                before_info.st_nlink, before_info.st_size, before_info.st_mtime_ns, before_info.st_ctime_ns,
            )
            indexes[checkout] = (index, before)
        for checkout in (repo, linked):
            command = f"{program} {marker}"
            subprocess.run(["git", "-C", str(checkout), "config", "core.fsmonitor", command], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "core.hooksPath", str(root / "hostile-hooks")], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "diff.external", command], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "diff.hostile.textconv", command], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "filter.hostile.clean", command], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "filter.hostile.process", command], check=True)
        poisoned = {
            "GIT_DIR": str(root / "poison-git-dir"), "GIT_WORK_TREE": str(root / "poison-worktree"),
            "GIT_COMMON_DIR": str(root / "poison-common"), "GIT_INDEX_FILE": str(root / "poison-index"),
            "GIT_OBJECT_DIRECTORY": str(root / "poison-objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(root / "poison-alternates"),
            "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=bad'", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": command,
            "GIT_CEILING_DIRECTORIES": str(root), "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
        }
        saved = {key: os.environ.get(key) for key in poisoned}
        os.environ.update(poisoned)
        try:
            for checkout in (repo, linked):
                first = MODULE._worktree_snapshot(str(checkout)); assert first is not None and first["entries"] == 0
                second = MODULE._worktree_snapshot(str(checkout)); assert second == first
                index, before = indexes[checkout]
                after_info = index.lstat(); after = (
                    index.read_bytes(), after_info.st_dev, after_info.st_ino, after_info.st_mode,
                    after_info.st_nlink, after_info.st_size, after_info.st_mtime_ns, after_info.st_ctime_ns,
                )
                assert after == before, "snapshot changed Git index bytes or identity"

        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        assert not marker.exists(), "snapshot executed a configured repository program"

        # Two clean linked worktrees can have identical content and HEAD, but
        # their roots and .git markers are distinct controller authorities and
        # therefore require distinct private digests.
        identity_source = root / "snapshot-linked-identity-source"; identity_source.mkdir()
        subprocess.run(["git", "init", "-q", str(identity_source)], check=True)
        subprocess.run(["git", "-C", str(identity_source), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(identity_source), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(identity_source), "commit", "--allow-empty", "-qm", "empty"], check=True)
        linked_one = root / "snapshot-linked-identity-one"
        linked_two = root / "snapshot-linked-identity-two"
        subprocess.run([
            "git", "-C", str(identity_source), "worktree", "add", "-q", "-b",
            "snapshot-linked-identity-one", str(linked_one),
        ], check=True)
        subprocess.run([
            "git", "-C", str(identity_source), "worktree", "add", "-q", "-b",
            "snapshot-linked-identity-two", str(linked_two),
        ], check=True)
        linked_one_snapshot = MODULE._worktree_snapshot(str(linked_one))
        linked_two_snapshot = MODULE._worktree_snapshot(str(linked_two))
        assert linked_one_snapshot is not None and linked_two_snapshot is not None
        assert linked_one_snapshot["entries"] == linked_two_snapshot["entries"] == 0
        assert (linked_one / ".git").read_bytes() != (linked_two / ".git").read_bytes()
        assert linked_one_snapshot["sha256"] != linked_two_snapshot["sha256"]

        # A marker moved aside and restored is the same no-follow authority:
        # stable identity and bytes are unchanged even though rename updates
        # ctime.  Mutable race metadata must not become cross-call state.
        linked_one_marker = linked_one / ".git"
        linked_one_displaced = root / "snapshot-linked-identity-one.marker"
        linked_one_marker.rename(linked_one_displaced)
        linked_one_displaced.rename(linked_one_marker)
        assert MODULE._worktree_snapshot(str(linked_one)) == linked_one_snapshot

        # A queued baseline is an authority binding, not just a content digest.
        # Replacing an empty repository at the exact pathname must fail before
        # provider launch even when both repositories have zero changed entries.
        replacement_repo = root / "snapshot-replacement-repo"
        replacement_repo.mkdir(); subprocess.run(["git", "init", "-q", str(replacement_repo)], check=True)
        replacement_baseline = MODULE._worktree_snapshot(str(replacement_repo))
        assert replacement_baseline is not None and replacement_baseline["entries"] == 0
        displaced_repo = root / "snapshot-replacement-displaced"
        replacement_repo.rename(displaced_repo)
        replacement_repo.mkdir(); subprocess.run(["git", "init", "-q", str(replacement_repo)], check=True)
        try:
            MODULE._bound_worktree_baseline(
                {"worktree_baseline": replacement_baseline}, {"workdir": str(replacement_repo)},
            )
        except MODULE.WorktreeBaselineError as exc:
            assert str(exc) == "queued worktree baseline changed"
        else:
            raise AssertionError("same-path different-repository replacement retained the baseline")
        replacement_repo.rename(root / "snapshot-replacement-new")
        replacement_repo.symlink_to(displaced_repo, target_is_directory=True)
        assert MODULE._worktree_snapshot(str(replacement_repo)) is None

        # A symlink inserted in a parent component changes the canonical root
        # even though it reaches the original inode and repository bytes.
        authority = root / "snapshot-authority"
        authority.mkdir(); authority_repo = authority / "repo"; authority_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(authority_repo)], check=True)
        authority_baseline = MODULE._worktree_snapshot(str(authority_repo))
        assert authority_baseline is not None
        moved_authority = root / "snapshot-authority-moved"
        authority.rename(moved_authority)
        authority.symlink_to(moved_authority, target_is_directory=True)
        try:
            MODULE._bound_worktree_baseline(
                {"worktree_baseline": authority_baseline}, {"workdir": str(authority / "repo")},
            )
        except MODULE.WorktreeBaselineError as exc:
            assert str(exc) in {
                "queued worktree baseline changed", "queued worktree baseline is unavailable",
            }
        else:
            raise AssertionError("outward canonical-root resolution retained the baseline")

        def closed_after_failure(action) -> None:
            processes = []
            original_popen = MODULE.subprocess.Popen
            def capture(*args, **kwargs):
                process = original_popen(*args, **kwargs); processes.append(process); return process
            MODULE.subprocess.Popen = capture
            try:
                assert action() is None
            finally:
                MODULE.subprocess.Popen = original_popen
            assert processes and all(
                process.poll() is not None
                and process.stdout is not None and process.stdout.closed
                and (process.stdin is None or process.stdin.closed)
                for process in processes
            )

        original_limit = MODULE.MAX_STREAM_BYTES
        MODULE.MAX_STREAM_BYTES = 1
        try:
            closed_after_failure(lambda: MODULE._worktree_snapshot(str(repo)))
        finally:
            MODULE.MAX_STREAM_BYTES = original_limit
        original_selector = MODULE.selectors.DefaultSelector
        class TimedOutSelector:
            def __init__(self): self.items = {}
            def register(self, item, _events): self.items[item] = True
            def unregister(self, item): self.items.pop(item, None)
            def get_map(self): return self.items
            def select(self, _timeout): return []
            def close(self): self.items.clear()
        MODULE.selectors.DefaultSelector = TimedOutSelector
        try:
            closed_after_failure(lambda: MODULE._worktree_snapshot(str(repo)))
        finally:
            MODULE.selectors.DefaultSelector = original_selector
        source = WORKTREE_SOURCE.read_bytes()
        start = source.index(b"def _worktree_snapshot")
        end = source.index(b"\n\n_IMPLEMENTATION_FUNCTIONS", start)
        body = source[start:end]
        runner_start = source.index(b"def _bounded_git_read")
        runner_end = source.index(b"\ndef _git_boundary_identity", runner_start)
        runner = source[runner_start:runner_end]
        assert b"_bounded_git_read(" in body
        assert b'"GIT_OPTIONAL_LOCKS": "0"' in runner
        assert b'"GIT_CONFIG_NOSYSTEM": "1"' in runner
        assert b'if key.startswith("GIT_")' in runner
        assert b'"core.fsmonitor=false"' in runner
        assert b'"core.hooksPath=/dev/null"' in runner
        assert b'"GIT_NO_LAZY_FETCH": "1"' in runner
        assert b'"GIT_NO_REPLACE_OBJECTS": "1"' in runner
        assert b'start_new_session=True' in runner
        assert b'os.killpg(process.pid, signal.SIGKILL)' in runner
        assert b"stderr=subprocess.PIPE" in runner
        assert b'"status", "--porcelain' not in body
        assert b'"diff"' not in body and b'"hash-object"' not in body
        assert b'"--filters"' not in body and b'"--textconv"' not in body
        assert b'git_read(["ls-files", "--stage", "-z"])' in body
        assert b'git_read(["ls-files", "-z", "--others", "--exclude-standard"])' in body
        assert b'git_read(["ls-files", "-z", "--others", "--ignored", "--exclude-standard"])' in body
        assert b'git_read(["cat-file", "--batch"]' in body
        assert b'second != first' in body and b'index_binding(index_path) != before_index' in body
        assert b"os.killpg(process.pid, signal.SIGKILL)" in runner and b"process.wait(" in runner
        assert b"payload_write" in runner and b"stream.close()" in runner

    check("worktree snapshot never runs repo programs or mutates normal and linked indexes", worktree_snapshot_is_readonly_and_disarms_repo_programs)

    def worktree_snapshot_uses_head_and_rejects_unsafe_git_states() -> None:
        """The snapshot is a HEAD/index/worktree observation, never index-only."""
        repo = root / "head-baseline-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, stdout=subprocess.PIPE).stdout.strip().decode()

        (repo / "added.txt").write_text("added\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "added.txt"], check=True)
        assert MODULE._worktree_snapshot(str(repo)) == MODULE._worktree_snapshot(str(repo))
        assert MODULE._worktree_snapshot(str(repo))["entries"] == 1  # type: ignore[index]
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", "HEAD"], check=True)

        tracked.write_text("staged modify\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        assert MODULE._worktree_snapshot(str(repo))["entries"] == 1  # type: ignore[index]
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", "HEAD"], check=True)

        # The worktree bytes and lstat stay fixed while index B becomes C.  The
        # private digest must still bind the staged object identity, not just count
        # the one dirty path.
        def write_blob(value: bytes) -> str:
            return subprocess.run(
                ["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=value,
                check=True, stdout=subprocess.PIPE,
            ).stdout.decode("ascii", "strict").strip()
        index_b = write_blob(b"index B\n")
        subprocess.run(["git", "-C", str(repo), "update-index", "--cacheinfo", f"100644,{index_b},tracked.txt"], check=True)
        snapshot_b = MODULE._worktree_snapshot(str(repo)); assert snapshot_b is not None and snapshot_b["entries"] == 1
        index_c = write_blob(b"index C\n")
        subprocess.run(["git", "-C", str(repo), "update-index", "--cacheinfo", f"100644,{index_c},tracked.txt"], check=True)
        snapshot_c = MODULE._worktree_snapshot(str(repo)); assert snapshot_c is not None and snapshot_c["entries"] == 1
        assert snapshot_b["sha256"] != snapshot_c["sha256"], "index object drift must alter the snapshot digest"
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", "HEAD"], check=True)

        subprocess.run(["git", "-C", str(repo), "rm", "-q", "tracked.txt"], check=True)
        assert MODULE._worktree_snapshot(str(repo))["entries"] == 1  # type: ignore[index]
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", "HEAD"], check=True)

        # A replace ref can make a staged change appear to be its replacement
        # commit unless the plumbing explicitly disables replacement objects.
        tracked.write_text("replacement\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "commit", "-am", "replacement", "-q"], check=True)
        replacement = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, stdout=subprocess.PIPE).stdout.strip().decode()
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", base], check=True)
        tracked.write_text("replacement\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "replace", base, replacement], check=True)
        try:
            snapshot = MODULE._worktree_snapshot(str(repo))
            assert snapshot is not None and snapshot["entries"] == 1
        finally:
            subprocess.run(["git", "-C", str(repo), "replace", "-d", base], check=True)
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", base], check=True)

        # Alternate object stores, sparse entries, and gitlinks are all outside
        # this small, self-contained observation contract.
        alternate_dir = repo / ".git" / "objects" / "info"; alternate_dir.mkdir(parents=True, exist_ok=True)
        for alternate_name in ("alternates", "http-alternates"):
            alternate = alternate_dir / alternate_name
            alternate.write_text("/missing/alternate\n", encoding="utf-8")
            try:
                assert MODULE._worktree_snapshot(str(repo)) is None
            finally:
                alternate.unlink()
        subprocess.run(["git", "-C", str(repo), "update-index", "--skip-worktree", "tracked.txt"], check=True)
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
        finally:
            subprocess.run(["git", "-C", str(repo), "update-index", "--no-skip-worktree", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "update-index", "--assume-unchanged", "tracked.txt"], check=True)
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
        finally:
            subprocess.run(["git", "-C", str(repo), "update-index", "--no-assume-unchanged", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", f"160000,{base},module"], check=True)
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
        finally:
            subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", "HEAD"], check=True)

        index = Path(subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "index"], check=True, stdout=subprocess.PIPE,
        ).stdout.decode("utf-8", "strict").strip())
        if not index.is_absolute():
            index = repo / index
        original_index = index.with_name("index-original")
        index.rename(original_index); index.symlink_to(original_index)
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
        finally:
            index.unlink(); original_index.rename(index)

        unborn = root / "head-baseline-unborn"; unborn.mkdir()
        subprocess.run(["git", "init", "-q", str(unborn)], check=True)
        (unborn / "first.txt").write_text("first\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(unborn), "add", "first.txt"], check=True)
        snapshot = MODULE._worktree_snapshot(str(unborn))
        assert snapshot is not None and snapshot["entries"] == 1

    check("worktree snapshot compares staged state to HEAD and rejects unsupported Git states", worktree_snapshot_uses_head_and_rejects_unsafe_git_states)

    def worktree_snapshot_never_uses_promisor_ext_helper() -> None:
        repo = root / "promisor-snapshot-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        blob = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", ":tracked.txt"], check=True, stdout=subprocess.PIPE,
        ).stdout.decode("ascii", "strict").strip()
        loose = repo / ".git" / "objects" / blob[:2] / blob[2:]
        assert loose.is_file()
        marker = root / "promisor-ext-ran"; helper = root / "promisor-ext-helper"
        helper.write_text("#!/bin/sh\nprintf ran > \"$1\"\nexit 1\n", encoding="utf-8")
        helper.chmod(0o700)
        subprocess.run(["git", "-C", str(repo), "config", "extensions.partialClone", "origin"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "remote.origin.promisor", "true"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "remote.origin.partialclonefilter", "blob:none"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "remote.origin.url", f"ext::{helper} {marker}"], check=True)
        loose.unlink()
        assert MODULE._worktree_snapshot(str(repo)) is None
        assert not marker.exists(), "a missing promisor blob invoked a repository ext helper"

    check("worktree snapshot declines promisor blobs without invoking repo ext helper", worktree_snapshot_never_uses_promisor_ext_helper)

    def worktree_snapshot_retries_listings_and_reaps_git_probe_descendants() -> None:
        repo = root / "snapshot-race-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        (repo / "race.txt").write_text("race\n", encoding="utf-8")
        real_git = shutil.which("git")
        assert real_git is not None
        bin_dir = root / "snapshot-race-bin"; bin_dir.mkdir()
        marker = root / "snapshot-race-marker"; helper = root / "snapshot-lazy-helper"; child_record = root / "snapshot-child"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\n"
            "real_git=" + shlex.quote(real_git) + "\n"
            "child_record=" + shlex.quote(str(child_record)) + "\n"
            "helper=" + shlex.quote(str(helper)) + "\n"
            "marker=" + shlex.quote(str(marker)) + "\n"
            "repo=" + shlex.quote(str(repo)) + "\n"
            "case \" $* \" in *\" cat-file --batch \"*)\n"
            "  (exec >/dev/null 2>&1; trap '' TERM; while :; do sleep 60; done) &\n"
            "  echo $! > \"$child_record\"\n"
            "  if [ \"${GIT_NO_LAZY_FETCH:-}\" != 1 ] || [ \"${GIT_NO_REPLACE_OBJECTS:-}\" != 1 ]; then : > \"$helper\"; fi\n"
            "  \"$real_git\" \"$@\"; code=$?\n"
            "  if [ ! -e \"$marker\" ]; then : > \"$marker\"; \"$real_git\" -C \"$repo\" add race.txt; fi\n"
            "  exit $code;;\n"
            "esac\n"
            "exec \"$real_git\" \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
        child = None
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None, "an index change between fixed listings is unavailable"
            assert not helper.exists(), "lazy fetching and replacement objects must be disabled for cat-file"
            child = int(child_record.read_text(encoding="ascii"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("snapshot left a Git probe descendant alive")
        finally:
            os.environ["PATH"] = previous_path
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def bounded_git_reader_caps_stdout_and_reaps_the_flooder() -> None:
        """The shared reader must reject incrementally, never buffer a flood."""
        repo = root / "bounded-git-flood-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        bin_dir = root / "bounded-git-flood-bin"; bin_dir.mkdir(mode=0o700)
        pid_record = root / "bounded-git-flood-pid"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$$\" > " + shlex.quote(str(pid_record)) + "\n"
            "while :; do printf '0123456789abcdef0123456789abcdef'; done\n",
            encoding="utf-8",
        )
        fake.chmod(0o700)
        prior_path = os.environ.get("PATH", "")
        prior_limit = MODULE.MAX_STREAM_BYTES
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{prior_path}"
        MODULE.MAX_STREAM_BYTES = 1024
        child = None
        try:
            safe_git = MODULE._safe_git_executable(); assert safe_git is not None
            started = time.monotonic()
            result = MODULE._bounded_git_read(
                safe_git[0], safe_git[1], str(repo),
                ["rev-parse", "--show-toplevel"],
                deadline=time.monotonic() + 2.0, stdout_limit=64,
            )
            elapsed = time.monotonic() - started
            assert result is None and elapsed < 3.0, elapsed
            child = int(pid_record.read_text(encoding="ascii"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("bounded Git stdout flooder remained alive")
        finally:
            MODULE.MAX_STREAM_BYTES = prior_limit
            os.environ["PATH"] = prior_path
            if child is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(child, signal.SIGKILL)

        source = WORKTREE_SOURCE.read_bytes()
        boundary_start = source.index(b"def _git_boundary_identity")
        boundary_end = source.index(b"\ndef _worktree_snapshot", boundary_start)
        boundary = source[boundary_start:boundary_end]
        assert b"_bounded_git_read(" in boundary
        assert b"subprocess.run(" not in boundary

    def bounded_git_reader_kills_stdout_holding_descendant_after_leader_exit() -> None:
        """A successful Git leader cannot strand a pipe-holding descendant."""
        repo = root / "bounded-git-descendant-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        bin_dir = root / "bounded-git-descendant-bin"; bin_dir.mkdir(mode=0o700)
        child_record = root / "bounded-git-descendant-pid"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\n"
            "(trap '' TERM; while :; do sleep 60; done) &\n"
            "printf '%s\\n' \"$!\" > " + shlex.quote(str(child_record)) + "\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake.chmod(0o700)
        prior_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{prior_path}"
        child = None
        try:
            safe_git = MODULE._safe_git_executable(); assert safe_git is not None
            started = time.monotonic()
            result = MODULE._bounded_git_read(
                safe_git[0], safe_git[1], str(repo),
                ["rev-parse", "--show-toplevel"],
                deadline=time.monotonic() + 2.0, stdout_limit=64,
            )
            elapsed = time.monotonic() - started
            assert result is None and elapsed < 3.0, elapsed
            child = int(child_record.read_text(encoding="ascii"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("stdout-holding Git descendant remained alive")
        finally:
            os.environ["PATH"] = prior_path
            if child is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(child, signal.SIGKILL)

    def bounded_git_snapshot_process_contracts() -> None:
        worktree_snapshot_retries_listings_and_reaps_git_probe_descendants()
        bounded_git_reader_caps_stdout_and_reaps_the_flooder()
        bounded_git_reader_kills_stdout_holding_descendant_after_leader_exit()

    check("snapshot and shared Git readers reject races floods and pipe-holding descendants", bounded_git_snapshot_process_contracts)

    def worktree_snapshot_rejects_target_and_binding_mutations() -> None:
        repo = root / "snapshot-binding-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        index = Path(subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "index"], check=True, stdout=subprocess.PIPE,
        ).stdout.decode("utf-8", "strict").strip())
        if not index.is_absolute():
            index = repo / index
        index = Path(os.path.realpath(index))

        original_open = MODULE.os.open; swapped = False
        def swap_bound_index(path, flags, *args, **kwargs):
            nonlocal swapped
            descriptor = original_open(path, flags, *args, **kwargs)
            if not swapped and os.fspath(path) == str(index):
                replacement = index.with_name("index-replacement")
                index.rename(replacement)
                index.write_bytes(replacement.read_bytes())
                swapped = True
            return descriptor
        MODULE.os.open = swap_bound_index
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
            assert swapped, "fixture did not swap the resolved index inode"
        finally:
            MODULE.os.open = original_open
            if index.exists(): index.unlink()
            index.with_name("index-replacement").rename(index)

        original_read = MODULE.os.read; mutated = False
        def mutate_after_read(descriptor, size):
            nonlocal mutated
            piece = original_read(descriptor, size)
            if not mutated and piece == b"base\n":
                tracked.write_text("mutated\n", encoding="utf-8")
                mutated = True
            return piece
        MODULE.os.read = mutate_after_read
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
            assert mutated, "fixture did not mutate the tracked file after its read"
        finally:
            MODULE.os.read = original_read

        # A stable Git/index listing does not prove that worktree bytes stayed
        # fixed after their per-file read.  Mutate the already-hashed file at the
        # first command in the final listings pass; the snapshot must fail closed
        # instead of publishing the stale clean observation.
        real_git = shutil.which("git"); assert real_git is not None
        post_read_bin = root / "snapshot-post-read-bin"; post_read_bin.mkdir()
        post_read_seen = root / "snapshot-post-read-seen"
        post_read_mutated = root / "snapshot-post-read-mutated"
        post_read_git = post_read_bin / "git"
        post_read_git.write_text(
            "#!/bin/sh\n"
            "seen=" + shlex.quote(str(post_read_seen)) + "\n"
            "mutated=" + shlex.quote(str(post_read_mutated)) + "\n"
            "tracked=" + shlex.quote(str(tracked)) + "\n"
            "case \" $* \" in *\" rev-parse --verify -q HEAD^{tree} \"*)\n"
            "  if [ -e \"$seen\" ]; then printf 'post-read mutation\\n' > \"$tracked\"; : > \"$mutated\"; else : > \"$seen\"; fi;;\n"
            "esac\n"
            "exec " + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        post_read_git.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{post_read_bin}{os.pathsep}{previous_path}"
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
            assert post_read_mutated.exists(), "fixture did not mutate the tracked file during final listings"
        finally:
            os.environ["PATH"] = previous_path
            tracked.write_text("base\n", encoding="utf-8")

        # The listed paths can remain unchanged while a new nested child appears
        # after the final Git listing.  The old per-path revalidation only reads
        # paths it had already observed, so this must fail closed instead of
        # publishing a stale snapshot.
        nested = repo / "nested"; nested.mkdir()
        (nested / "known.txt").write_text("known\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "nested/known.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "nested"], check=True)
        listing_bin = root / "snapshot-nested-listing-bin"; listing_bin.mkdir()
        listing_count = root / "snapshot-nested-listing-count"
        listing_mutated = root / "snapshot-nested-listing-mutated"
        listing_git = listing_bin / "git"
        listing_git.write_text(
            "#!/bin/sh\n"
            "count_file=" + shlex.quote(str(listing_count)) + "\n"
            "mutated=" + shlex.quote(str(listing_mutated)) + "\n"
            "late=" + shlex.quote(str(nested / "late.txt")) + "\n"
            "mutate=0\n"
            "case \" $* \" in *\" ls-files -z --others --ignored --exclude-standard \"*)\n"
            "  count=0; if [ -r \"$count_file\" ]; then count=$(cat \"$count_file\"); fi\n"
            "  count=$((count + 1)); printf '%s\\n' \"$count\" > \"$count_file\"\n"
            "  if [ \"$count\" -eq 3 ]; then mutate=1; fi;;\n"
            "esac\n"
            + shlex.quote(real_git) + " \"$@\"; code=$?\n"
            "if [ \"$mutate\" -eq 1 ] && [ \"$code\" -eq 0 ]; then printf 'late\\n' > \"$late\"; : > \"$mutated\"; fi\n"
            "exit \"$code\"\n",
            encoding="utf-8",
        )
        listing_git.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{listing_bin}{os.pathsep}{previous_path}"
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
            assert listing_count.read_text(encoding="ascii").strip() == "3"
            assert listing_mutated.exists(), "fixture did not add a nested path after the final listings output"
        finally:
            os.environ["PATH"] = previous_path

        real_git = shutil.which("git"); assert real_git is not None
        bin_dir = root / "snapshot-admin-drift-bin"; bin_dir.mkdir()
        marker = root / "snapshot-admin-drift-marker"; fake = bin_dir / "git"; replacement = bin_dir / "git-next"
        replacement.write_text("#!/bin/sh\nexec " + shlex.quote(real_git) + " \"$@\"\n", encoding="utf-8")
        replacement.chmod(0o755)
        fake.write_text(
            "#!/bin/sh\nmarker=" + shlex.quote(str(marker)) + "\nreplacement=" + shlex.quote(str(replacement)) + "\n"
            "if [ ! -e \"$marker\" ]; then : > \"$marker\"; mv \"$replacement\" \"$0\"; fi\n"
            "exec " + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        previous_path = os.environ.get("PATH", ""); os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
        try:
            assert MODULE._worktree_snapshot(str(repo)) is None
            assert marker.exists(), "fixture did not replace the selected Git target"
        finally:
            os.environ["PATH"] = previous_path

    check("worktree snapshot rejects index file post-read and Git target drift", worktree_snapshot_rejects_target_and_binding_mutations)

    def worktree_snapshot_has_a_bounded_preflight_and_two_manifest_linearization() -> None:
        """Marker and Git-alias preflights precede two manifests; late drift is residual."""
        repo = root / "snapshot-final-sweep-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        z_dir = repo / "z"; z_dir.mkdir()
        a_dir = repo / "a"; a_dir.mkdir()
        (z_dir / "known.txt").write_text("known\n", encoding="utf-8")
        (a_dir / "known.txt").write_text("known\n", encoding="utf-8")

        z_identity = (z_dir.stat().st_dev, z_dir.stat().st_ino)
        a_identity = (a_dir.stat().st_dev, a_dir.stat().st_ino)
        original_scandir = MODULE.os.scandir
        visits: list[str] = []
        mutated = False

        class OrderedScandir:
            def __init__(self, descriptor: int):
                nonlocal mutated
                info = os.fstat(descriptor)
                identity = (info.st_dev, info.st_ino)
                if identity == z_identity:
                    visits.append("z")
                elif identity == a_identity:
                    visits.append("a")
                    # The marker and Git-alias preflights are first no-follow
                    # walks. They must not replace either later full manifest,
                    # and a mutation after the final manifest's z read remains
                    # the documented finite-observation residual.
                    if visits.count("a") == 4:
                        (z_dir / "late.txt").write_text("late\n", encoding="utf-8")
                        mutated = True
                with original_scandir(descriptor) as scanned:
                    self.entries = sorted(list(scanned), key=lambda entry: entry.name, reverse=True)

            def __enter__(self):
                return iter(self.entries)

            def __exit__(self, _type, _value, _traceback):
                return False

        MODULE.os.scandir = OrderedScandir
        try:
            snapshot = MODULE._worktree_snapshot(str(repo))
        finally:
            MODULE.os.scandir = original_scandir
        assert mutated, "fixture did not add z/late while the final walk started a"
        assert visits == ["z", "a", "z", "a", "z", "a", "z", "a"], visits
        assert snapshot is not None, "the two preflights plus two-manifest linearization unexpectedly added a fifth sweep"

    check("worktree snapshot uses a marker-only preflight plus two bounded manifests and documents post-final-entry mutation as residual", worktree_snapshot_has_a_bounded_preflight_and_two_manifest_linearization)

    def worktree_snapshot_closes_manifest_scandir_descriptors() -> None:
        """Every manifest scan owns its duplicated directory descriptor."""
        repo = root / "snapshot-fd-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        nested = repo / "nested"; nested.mkdir()
        (nested / "child.txt").write_text("child\\n", encoding="utf-8")

        def fd_count() -> int:
            return len(os.listdir("/dev/fd"))

        before_success = fd_count()
        snapshots = [MODULE._worktree_snapshot(str(repo)) for _ in range(3)]
        after_success = fd_count()
        assert all(snapshot is not None for snapshot in snapshots)
        assert snapshots[1:] == snapshots[:-1]
        assert after_success == before_success, (before_success, after_success)

        # If scandir itself rejects the duplicate descriptor, the snapshot still
        # owns and closes it.  This exercises the failure path independently of
        # normal iterator/context-manager cleanup.
        before_failure = fd_count()
        original_scandir = MODULE.os.scandir
        def rejected_scandir(_descriptor):
            raise OSError("fixture scandir rejection")
        MODULE.os.scandir = rejected_scandir
        try:
            assert all(MODULE._worktree_snapshot(str(repo)) is None for _ in range(3))
        finally:
            MODULE.os.scandir = original_scandir
        after_failure = fd_count()
        assert after_failure == before_failure, (before_failure, after_failure)

    check("worktree snapshot closes manifest scandir descriptors on success and failure", worktree_snapshot_closes_manifest_scandir_descriptors)

    def worktree_snapshot_rejects_deep_manifest_recursion() -> None:
        """Depth beyond the interpreter limit is unavailable, never an exception."""
        repo = root / "snapshot-deep-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        current = repo
        for _ in range(100):
            current = current / "nested"; current.mkdir()
        (current / "leaf.txt").write_text("leaf\\n", encoding="utf-8")
        saved_limit = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(80)
            assert MODULE._worktree_snapshot(str(repo)) is None
        finally:
            sys.setrecursionlimit(saved_limit)

    check("worktree snapshot fails closed when the recursive manifest exceeds the interpreter depth", worktree_snapshot_rejects_deep_manifest_recursion)

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
        assert first["entries"] == 1
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
        assert untracked_one["entries"] == 2
        (repo / "untracked.txt").write_text("u2", encoding="utf-8")
        untracked_two = MODULE._worktree_snapshot(str(repo)); assert untracked_two is not None
        assert untracked_one["sha256"] != untracked_two["sha256"]
        untracked_mode = stat.S_IMODE((repo / "untracked.txt").stat().st_mode)
        (repo / "untracked.txt").chmod(untracked_mode | 0o111)
        executable = MODULE._worktree_snapshot(str(repo)); assert executable is not None
        assert executable["sha256"] != untracked_two["sha256"]
        (repo / "untracked.txt").chmod(untracked_mode)
        restored_mode = MODULE._worktree_snapshot(str(repo)); assert restored_mode is not None
        assert restored_mode == untracked_two
        (repo / "ignored.bin").write_text("i1", encoding="utf-8")
        ignored_one = MODULE._worktree_snapshot(str(repo)); assert ignored_one is not None
        assert ignored_one["entries"] == 3
        (repo / "ignored.bin").write_text("i2", encoding="utf-8")
        ignored_two = MODULE._worktree_snapshot(str(repo)); assert ignored_two is not None
        assert ignored_one["sha256"] != ignored_two["sha256"]
        (repo / "tracked.txt").unlink()
        deleted = MODULE._worktree_snapshot(str(repo)); assert deleted is not None
        assert deleted["entries"] == 3
        assert deleted["sha256"] != ignored_two["sha256"]
        (repo / "one-link").symlink_to("one-target")
        one_link = MODULE._worktree_snapshot(str(repo)); assert one_link is not None
        (repo / "one-link").unlink(); (repo / "one-link").symlink_to("other-target")
        other_link = MODULE._worktree_snapshot(str(repo)); assert other_link is not None
        assert other_link["sha256"] != one_link["sha256"]

    check("tracked untracked ignored deleted symlink and mode changes alter the bounded digest", reconciliation_hashes_content_under_same_git_status)

    def semantic_snapshot_ignores_ephemeral_driver_caches_and_index_refresh() -> None:
        """Driver checks may refresh Git/cache metadata without changing a candidate."""
        repo = root / "semantic-cache-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        tracked = repo / "tracked.py"
        tracked.write_text("value = 1\n", encoding="utf-8")
        (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.py", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        baseline = MODULE._worktree_snapshot(str(repo)); assert baseline is not None

        # A normal driver status check may rewrite only Git's index stat cache.
        os.utime(tracked, (1_893_456_789, 1_893_456_789))
        subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1"], check=True, stdout=subprocess.PIPE)
        refreshed = MODULE._worktree_snapshot(str(repo)); assert refreshed is not None
        assert refreshed["sha256"] == baseline["sha256"]
        subprocess.run(["git", "-C", str(repo), "update-index", "--refresh", "--", "tracked.py"], check=True)
        refreshed_again = MODULE._worktree_snapshot(str(repo)); assert refreshed_again is not None
        assert refreshed_again == baseline

        cache = repo / "__pycache__"; cache.mkdir()
        (cache / "tracked.cpython-313.pyc").write_bytes(b"ephemeral cache")
        pytest_cache = repo / ".pytest_cache"; pytest_cache.mkdir()
        (pytest_cache / "README.md").write_text("ephemeral", encoding="utf-8")
        (cache / "tracked.cpython-313.pyc").unlink(); cache.rmdir()
        (pytest_cache / "README.md").unlink(); pytest_cache.rmdir()
        restored = MODULE._worktree_snapshot(str(repo)); assert restored is not None
        assert restored == baseline

    check("semantic snapshots survive driver cache cleanup and ordinary Git index refresh", semantic_snapshot_ignores_ephemeral_driver_caches_and_index_refresh)

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
        for updates in (
            {"status": "queued", "attempt_origin": "initial", "controller_pid": None},
            {"status": "running", "attempt_origin": "conversation-continue", "controller_pid": 123},
        ):
            state.update(updates)
            try:
                MODULE._bound_candidate_worktree(state, command)
            except MODULE.DispatchError as exc:
                assert str(exc) == "candidate worktree is not quiescent"
            else:
                raise AssertionError("active or non-continuation candidate binding was accepted")
        state.update({"status": "succeeded", "controller_pid": None})
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

    def controller_binds_snapshot_before_provider_and_quiescent_candidate_after_termination() -> None:
        repo = root / "snapshot-order-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        job = root / "snapshot-order-job"; job.mkdir(mode=0o700); job = job.resolve()
        bin_dir = root / "snapshot-order-bin"; bin_dir.mkdir()
        calls = root / "snapshot-order-provider-calls"
        provider = root / "snapshot-order-provider.json"; provider_schema(provider)
        events = [
            {"event": "init", "init": {}, "conversation_id": "snapshot-order-conversation"},
            {"event": "result", "result": {
                "conversation_id": "snapshot-order-conversation", "status": "SUCCESS",
                "structured_output": report(summary="snapshot order candidate"),
            }},
        ]
        fake = bin_dir / "agy"
        fake.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' provider >> " + shlex.quote(str(calls)) + "\n"
            "printf '%s\\n' " + " ".join(shlex.quote(json.dumps(item)) for item in events) + "\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "snapshot-order",
            "workdir": str(repo), "argv": ["agy", "--json-schema", str(provider), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
        MODULE.create_state(job, "initial", resume=False)

        observed = {"provider": 0, "terminated": False, "snapshot_after_terminate": []}
        original_popen = MODULE.subprocess.Popen
        original_terminate = MODULE._terminate
        original_snapshot = MODULE._worktree_snapshot

        def inspect_popen(arguments, *args, **kwargs):
            if isinstance(arguments, list) and arguments and arguments[0] == "agy":
                running, _raw, _sha = MODULE.load_state(job)
                assert running["status"] == "running" and running["worktree_baseline"] is not None
                observed["provider"] += 1
            return original_popen(arguments, *args, **kwargs)

        def terminate_then_mutate(process):
            result = original_terminate(process)
            (repo / "after-terminate.txt").write_text("bound after termination\\n", encoding="utf-8")
            observed["terminated"] = True
            return result

        def observe_snapshot(workdir: str):
            observed["snapshot_after_terminate"].append(observed["terminated"])
            return original_snapshot(workdir)

        MODULE.subprocess.Popen = inspect_popen
        MODULE._terminate = terminate_then_mutate
        MODULE._worktree_snapshot = observe_snapshot
        try:
            assert run_controller(job, bin_dir) == 0
        finally:
            MODULE.subprocess.Popen = original_popen
            MODULE._terminate = original_terminate
            MODULE._worktree_snapshot = original_snapshot
        state, _raw, state_sha = MODULE.load_state(job)
        current = MODULE._worktree_snapshot(str(repo))
        assert observed["provider"] == 1 and observed["terminated"]
        # The queued baseline now rebinds immediately before provider Popen, so
        # pre-launch observations occur before termination; the terminal
        # candidate binding must still be the final, post-termination one.
        assert observed["snapshot_after_terminate"] and observed["snapshot_after_terminate"][-1]
        assert current is not None
        assert (state["candidate_worktree_sha256"], state["candidate_worktree_entries"]) == (
            current["sha256"], current["entries"],
        )

        verification = {
            "schema_version": 2, "summary": "driver found a defect", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        queued, _queued_sha = MODULE.create_state(
            job, "conversation-continue", resume=True, approve_sha=state_sha, verification=verification,
        )
        assert (
            queued["status"], queued["attempt_origin"], queued["controller_pid"],
        ) == ("queued", "conversation-continue", None)
        (repo / "queued-drift.txt").write_text("drift\\n", encoding="utf-8")
        assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
        assert calls.read_text(encoding="ascii").splitlines() == ["provider"]
        rejected, _raw, _sha = MODULE.load_state(job)
        assert rejected["status"] == "failed" and rejected["reason"] == "status_unavailable"

    def terminal_snapshot_unavailable_preserves_only_forensic_outer_candidates() -> None:
        """A valid report without its terminal worktree binding is not usable."""
        for label, outer, source in (
            ("success", "SUCCESS", "provider_success"),
            ("error", "ERROR", "provider_error"),
            ("cancelled", "CANCELLED", "provider_cancelled"),
        ):
            repo = root / f"terminal-missing-{label}-repo"; repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            job = root / f"terminal-missing-{label}-job"; job.mkdir(mode=0o700); job = job.resolve()
            bin_dir = root / f"terminal-missing-{label}-bin"; bin_dir.mkdir()
            bound_provider = root / f"terminal-missing-{label}-provider.json"; provider_schema(bound_provider)
            events = [
                {"event": "init", "init": {}, "conversation_id": f"terminal-missing-{label}"},
                {"event": "result", "result": {
                    "conversation_id": f"terminal-missing-{label}", "status": outer,
                    "structured_output": report(summary=f"terminal-missing-{label}"),
                }},
            ]
            fake = bin_dir / "agy"
            fake.write_text(
                "#!/bin/sh\nprintf '%s\\n' " + " ".join(shlex.quote(json.dumps(item)) for item in events) + "\n",
                encoding="utf-8",
            ); fake.chmod(0o755)
            command = {
                "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"terminal-missing-{label}",
                "workdir": str(repo), "argv": ["agy", "--json-schema", str(bound_provider), "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
                "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            MODULE.create_state(job, "initial", resume=False)
            original_snapshot = MODULE._worktree_snapshot
            snapshots = 0
            def drop_terminal_snapshot(workdir: str):
                nonlocal snapshots
                snapshots += 1
                return original_snapshot(workdir) if snapshots == 1 else None
            MODULE._worktree_snapshot = drop_terminal_snapshot
            try:
                assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
            finally:
                MODULE._worktree_snapshot = original_snapshot
            state, _raw, sha = MODULE.load_state(job)
            assert snapshots == 2
            assert (state["status"], state["reason"], state["exit_code"], state["failure_stage"]) == (
                "failed", "status_unavailable", 20, "binding_failure",
            )
            assert state["candidate_recognized"] and state["candidate_source"] == source
            assert state["result_path"] and state["result_sha256"] and state["result_identity"]
            assert not state["result_available"] and not state["resume_available"] and not state["continue_available"]
            assert state["phase"] == "blocked" and state["assurance"] == "blocked"
            assert state["driver_disposition"] == "unreviewed"
            assert state["worktree_reconciliation"] == "unavailable"
            assert state["worktree_changes_present"] is None and state["worktree_changed_since_dispatch"] is None
            public = MODULE.public_status(state, sha, job=job)
            assert public["available_actions"] == [] and public["next_action"] == "none"
            try:
                MODULE.command_result(job)
            except MODULE.DispatchError:
                pass
            else:
                raise AssertionError("unbound terminal candidate was delivered")
            assert "finalize" not in {item["action"] for item in public["available_actions"]}

    check(
        "baseline/candidate snapshots and unavailable SUCCESS ERROR CANCELLED terminal bindings fail closed",
        lambda: (
            controller_binds_snapshot_before_provider_and_quiescent_candidate_after_termination(),
            terminal_snapshot_unavailable_preserves_only_forensic_outer_candidates(),
        ),
    )

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
        assert state["provider_terminal_status"] == "error"

    check("controller maps ERROR plus valid report to failed unreviewed exit 25", controller_preserves_outer_error_candidate)

    def invalid_error_and_cancelled_candidate_are_separate() -> None:
        cases = [
            ("success-missing", "SUCCESS", None, 4, "failed", "invalid_envelope", "none", "missing_structured_output", "success"),
            ("error-missing", "ERROR", None, 4, "failed", "invalid_envelope", "none", "missing_structured_output", "error"),
            ("lowercase-success", "success", report(), 4, "failed", "invalid_envelope", "none", "outer_status", "unknown"),
            ("cancelled", "CANCELED", report(), 22, "cancelled", "provider_terminal_cancelled", "provider_cancelled", None, "cancelled"),
        ]
        for label, outer_status, candidate, expected_exit, status, reason, source_name, stage_name, terminal_status in cases:
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
            assert state["provider_terminal_status"] == terminal_status
            if source_name == "provider_cancelled":
                assert state["result_available"] and not state["resume_available"] and not state["continue_available"]
            else:
                assert not state["result_available"] and state["exit_code"] == 4

    check("invalid outer status cannot become success while CANCELED report is preserved", invalid_error_and_cancelled_candidate_are_separate)

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
            "failed", "attempt-failed", "not_applicable", "none",
        )
        assert pre["last_activity"] is None and not pre["resume_available"] and not pre["continue_available"]
        public_pre = MODULE.public_status(pre, pre_sha)
        assert all(public_pre[key] == pre[key] for key in (
            "status", "driver_disposition", "last_activity",
        ))
        assert public_pre["controller_phase"] == public_pre["phase"] == pre["phase"]
        assert public_pre["next_action"] == "restart"
        assert public_pre["available_actions"] == [{
            "action": "restart",
            "command": f'"$PIPELINE/agy-worker.sh" restart --job-id {pre["job_id"]} --approve-state-sha {pre_sha} --format text',
        }]

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
            "failed", "awaiting-verification", "unreviewed", "none",
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
            "failed", "interrupted", "awaiting-verification", "none",
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
            "cancelled", "cancelled", "awaiting-verification", "none",
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
            "failed", "blocked", "blocked", "binding_failure", "none",
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
            "driver_disposition", "failure_stage",
            "worktree_reconciliation", "worktree_changes_present", "worktree_changed_since_dispatch",
        ))
        assert public_bound["assurance"] is None
        assert public_bound["next_action"] == "restart"
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
            "orphaned", "awaiting-verification", "unreviewed", "none",
        )
        assert not orphan["resume_available"] and not orphan["continue_available"]
        assert orphan["result_sha256"] == before_orphan["result_sha256"]
        preserved = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(orphan_job)],
            check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(preserved.stdout)["summary"] == "candidate-orphan"
        public_orphan = MODULE.public_status(orphan, orphan_sha, job=orphan_job)
        assert captured == [public_orphan]
        assert all(public_orphan[key] == orphan[key] for key in (
            "status", "driver_disposition", "continue_available",
        ))
        assert public_orphan["controller_phase"] == public_orphan["phase"] == orphan["phase"]
        assert public_orphan["next_action"] == "result"

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
        for key in {*MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS, *MODULE.STATE_V10_FIELDS}:
            state.pop(key)
        old_raw, old_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        loaded, _raw, read_sha = MODULE.read_state_snapshot(job)
        assert read_sha == old_sha and loaded["candidate_recognized"]
        assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
        # A V4 historical read may establish no new mutation authority.  It
        # cannot persist an assurance claim or a V9 migration.
        public = MODULE.public_status(loaded, read_sha, job=job)
        assert public["assurance"] is None and public["driver_disposition"] == "unreviewed"
        assert public["controller_phase"] == "awaiting-verification"
        assert "result" in {item["action"] for item in public["available_actions"]}
        assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
        delivered = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
            check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(delivered.stdout)["summary"] == "candidate"
        assert (job / MODULE.STATE_NAME).read_bytes() == old_raw

        # The current V3/V4 candidate is eligible only through a second,
        # no-write migration approval.  Status and command use the same exact
        # digest; omitting it must leave the old bytes untouched.
        migration_sha = public["migration_binding_sha256"]
        assert isinstance(migration_sha, str) and len(migration_sha) == 64
        action_commands = {item["action"]: item.get("command", "") for item in public["available_actions"]}
        assert "finalize" in action_commands and migration_sha in action_commands["finalize"]
        verification = {
            "schema_version": 2, "summary": "driver verified", "passed_checks": ["fixture"],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": loaded["result_sha256"], "coverage": "complete",
            "verified_findings": 1, "unresolved_gaps": 0, "diff_review_complete": True,
        }
        missing = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", old_sha, "--assurance", "verified"],
            input=json.dumps(verification).encode(), check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert missing.returncode == 64 and missing.stdout == b""
        assert b"legacy migration approval" in missing.stderr
        assert (job / MODULE.STATE_NAME).read_bytes() == old_raw

        # A same-path symlink substitution cannot reuse a status-time approval.
        moved_repo = root / "legacy-upgrade-repo-moved"
        repo.rename(moved_repo)
        repo.symlink_to(moved_repo, target_is_directory=True)
        try:
            replaced = MODULE.public_status(loaded, read_sha, job=job)
            assert replaced["migration_binding_sha256"] is None
            assert "finalize" not in {item["action"] for item in replaced["available_actions"]}
            assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
        finally:
            repo.unlink()
            moved_repo.rename(repo)

        # A semantic snapshot change makes the previously displayed digest stale
        # and must roll back before a verification artifact or V9 state write.
        drift = repo / "migration-drift.txt"
        drift.write_text("drift\n", encoding="utf-8")
        stale_approval = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", old_sha, "--approve-migration-sha", migration_sha,
             "--assurance", "verified"],
            input=json.dumps(verification).encode(), check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert stale_approval.returncode == 64 and stale_approval.stdout == b""
        assert b"legacy migration approval is stale" in stale_approval.stderr
        assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
        drift.unlink()

        # Schema drift invalidates the advertised approval before state write.
        provider_raw = provider.read_bytes()
        provider.write_bytes(b"{")
        try:
            stale = MODULE.public_status(loaded, read_sha, job=job)
            assert "result" not in {item["action"] for item in stale["available_actions"]}
            assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
        finally:
            provider.write_bytes(provider_raw)
        approved = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", old_sha, "--approve-migration-sha", migration_sha,
             "--assurance", "verified"],
            input=json.dumps(verification).encode(), check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert approved.returncode == 0 and approved.stderr == b""
        current, _current_raw, _current_sha = MODULE.read_state_snapshot(job)
        assert current["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
        assert current["driver_disposition"] == "verified"

    check("v4 current candidate needs an exact migration approval then upgrades atomically", legacy_candidate_read_is_nonmutating_and_approved_finalize_upgrades_atomically)

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

    def deeply_nested_json_fails_closed_without_recursion() -> None:
        """Sub-megabyte hostile nesting must never escape the controller."""
        depth = 1100
        nested = b"[" * depth + b"0" + b"]" * depth
        event = b'{"event":"init","init":' + nested + b"}\n"
        assert len(event) < 1024 * 1024
        assert MODULE._event(event) == (False, None, None)

        stream_path = root / "deeply-nested.ndjson"
        stream_path.write_bytes(event)
        assert MODULE._terminal_result(stream_path) is None
        assert MODULE._terminal_result(stream_path, strict=True) is None

        try:
            MODULE.MODEL_SELECTION.decode_selection_record(nested, frozen=True)
        except MODULE.MODEL_SELECTION.CallerError:
            pass
        else:
            raise AssertionError("deep selection artifact escaped the bounded decoder")

        valid = {"command": ["agy"], "state": {"status": "queued"}, "verification": []}
        assert MODULE.parse_json(MODULE.canonical(valid), "fixture") == valid
        original_dumps = MODULE.json.dumps
        try:
            def recursive_dump(*_args: object, **_kwargs: object) -> str:
                raise RecursionError("injected encoder recursion")

            MODULE.json.dumps = recursive_dump
            try:
                MODULE.canonical(valid)
            except MODULE.DispatchError as exc:
                assert str(exc) == "JSON structure is invalid"
            else:
                raise AssertionError("deep decoded JSON escaped canonicalization containment")
        finally:
            MODULE.json.dumps = original_dumps

        # Exercise the real CLI boundary with a hostile owner-private command
        # that remains below MAX_COMMAND_BYTES. It must stop before state or
        # provider artifacts exist, without exposing an interpreter traceback.
        command_job = root / "deep-command-job"; command_job.mkdir(mode=0o700)
        command_job = command_job.resolve()
        command_path = command_job / MODULE.COMMAND_NAME
        command_path.write_bytes(b"[" * 150_000 + b"0" + b"]" * 150_000)
        command_path.chmod(0o600)
        completed = subprocess.run(
            [sys.executable, str(SOURCE), "run", "--job-dir", str(command_job)],
            input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )
        assert completed.returncode == 64, (completed.returncode, completed.stderr)
        assert completed.stdout == b""
        assert completed.stderr == b"agy-dispatch: dispatch command is invalid\n", completed.stderr
        assert {path.name for path in command_job.iterdir()} == {
            MODULE.COMMAND_NAME, MODULE.LOCK_NAME,
        }

    check("deeply nested event terminal and selection JSON fail closed", deeply_nested_json_fails_closed_without_recursion)

    def critical_help_is_structural_not_semantic() -> None:
        options = {
            "--add-dir": "Add a directory", "--conversation": "Resume a conversation",
            "--disable-slash-commands": "Disable slash commands", "--json-schema": "Schema path",
            "--mode": "Execution mode (accept-edits, plan)", "--model": "Select a model",
            "--output-format": "Format (text, json, stream-json)", "--print": "Run a prompt",
            "--print-timeout": "Print timeout", "--sandbox": "Sandboxed",
        }
        good = "\n".join(f"  {key}  {value}" for key, value in options.items()).encode() + b"\n"
        capability, help_digest = MODULE.MODEL_SELECTION.parse_critical_help(good)
        assert len(capability) == len(help_digest) == 64
        for missing in options:
            candidate = "\n".join(
                f"  {key}  {value}" for key, value in options.items() if key != missing
            ).encode() + b"\n"
            try:
                MODULE.MODEL_SELECTION.parse_critical_help(candidate)
            except MODULE.MODEL_SELECTION.EvidenceUnavailable:
                pass
            else:
                raise AssertionError(f"missing {missing} was accepted")
        adversarial = (
            good.replace(b"(accept-edits, plan)", b"(accept-edits, no-plan)"),
            good.replace(b"(accept-edits, plan)", b"(accept-edits, plan, unsupported)"),
            good.replace(b"(text, json, stream-json)", b"(text, json, stream-jsonish)"),
            good.replace(b"(text, json, stream-json)", b"(text, json, stream-json, unsupported)"),
            good.replace(b"Execution mode (accept-edits, plan)", b"Execution mode supports accept-edits and plan"),
            good.replace(b"  --model  Select a model", b"  --model  Duplicate\n  --model  Select a model"),
            good.replace(b"  --model  Select a model", b" --model  Select a model"),
            good.replace(b"  --model  Select a model", b"  --Model  Select a model"),
        )
        for candidate in adversarial:
            try:
                MODULE.MODEL_SELECTION.parse_critical_help(candidate)
            except MODULE.MODEL_SELECTION.EvidenceUnavailable:
                pass
            else:
                raise AssertionError("ambiguous or unsupported help grammar was accepted")

        # Provider prose is evidence for Codex review, never controller launch
        # authorization. A structurally complete interface remains compatible
        # despite wording that calls a model unavailable or unsupported.
        risks = (
            "Deprecated; retained for compatibility",
            "Removed and has no effect",
            "Ignored by this release",
            "Unsupported in print mode",
            "Not supported by this build",
            "Disabled for this build",
            "No longer supported by this build",
            "Select a model; currently unavailable",
        )
        for option, detail in options.items():
            for risk in risks:
                candidate_options = {**options, option: f"{detail}. {risk}"}
                candidate = "\n".join(
                    f"  {key}  {value}" for key, value in candidate_options.items()
                ).encode() + b"\n"
                MODULE.MODEL_SELECTION.parse_critical_help(candidate)

        for direct_unusability in (
            "This option cannot be used",
            "This option cannot currently be used",
            "This option may not be used",
            "This option is not available",
            "Select a model. This option is unavailable in this build",
            "This option can no longer be used",
            "This option is not supported",
            "The option is disabled",
            "--model must not be used",
            "Model selection is removed",
        ):
            direct_model_unusable = good.replace(
                b"  --model  Select a model",
                f"  --model  {direct_unusability}".encode(),
            )
            MODULE.MODEL_SELECTION.parse_critical_help(direct_model_unusable)

        for harmless in (
            "Supported compatibility examples",
            "Documents unsupportedness and ignoredness labels",
            "Notable behavior with removedly named examples",
            "Cannot be usedfully as a substring test case",
        ):
            candidate = good.replace(b"Select a model", f"Select a model. {harmless}".encode())
            MODULE.MODEL_SELECTION.parse_critical_help(candidate)
        # A qualifier about aliases is not an assertion that the critical option
        # itself is unavailable.  Every critical option must retain this same
        # option-local distinction, rather than inheriting a broad prose regex.
        for option, detail in options.items():
            candidate_options = {**options, option: f"{detail}; aliases cannot be used"}
            candidate = "\n".join(
                f"  {key}  {value}" for key, value in candidate_options.items()
            ).encode() + b"\n"
            MODULE.MODEL_SELECTION.parse_critical_help(candidate)
            for qualifier in (
                "Unavailable legacy alias --old-model",
                "Deprecated compatibility example legacy-value",
                "Removed legacy alias --old-model",
                "Disabled example value legacy-value",
                "No longer supported legacy alias --old-model",
                "Legacy alias --old-model cannot be used",
            ):
                candidate_options = {**options, option: f"{detail}. {qualifier}"}
                candidate = "\n".join(
                    f"  {key}  {value}" for key, value in candidate_options.items()
                ).encode() + b"\n"
                MODULE.MODEL_SELECTION.parse_critical_help(candidate)

    check("critical help uses only option and enum structure, never provider prose", critical_help_is_structural_not_semantic)

    def v3_selection_schema_matches_the_current_runtime_binding() -> None:
        """The public V3 schema may not accept records the strict decoder rejects."""
        schema = ROOT / "skills/agy-worker/runtime/schemas/model-selection.schema.json"
        record = {
            "schema_version": 3,
            "kind": "agy-worker-selection",
            "selection_mode": "exact-model",
            "user_model": "gemini-3.6-flash-high",
            "user_model_source": "cli",
            "resolved_agy_model": "gemini-3.6-flash-high",
            "installed_agy_version": "1.1.17",
            "matrix_sha256": "a" * 64,
            "matrix_agy_version": "1.1.22",
            "matrix_source_revision": "b" * 40,
            "version_relation": "drift",
            "compatibility_status": "critical-interface-compatible-version-drift",
            "critical_interface_probe_version": 1,
            "critical_interface_status": "compatible",
            "critical_capabilities_sha256": "c" * 64,
            "help_sha256": "d" * 64,
            "model_availability": "not_assessed",
            "probed_executable": {
                "path_sha256": "e" * 64,
                "content_sha256": "f" * 64,
                "target_lstat": {
                    "device": 1, "inode": 1, "mode": 0o100755,
                    "uid": os.geteuid(), "gid": os.getegid(), "size": 1,
                    "mtime_ns": 1, "ctime_ns": 1,
                },
                "symlink_chain": [],
                "components": [],
            },
            "compatibility_disposition": "proceed",
            "approved_help_sha256": "d" * 64,
        }
        record["compatibility_decision_sha256"] = (
            MODULE.MODEL_SELECTION.compatibility_decision_sha256(record)
        )

        document = json.loads(schema.read_text(encoding="utf-8"))
        for title in (
            "v3 approved drift exact-model selection",
            "v3 approved drift model and effort selection",
        ):
            v3_variant = next(
                item for item in document["oneOf"] if item.get("title") == title
            )
            v3_properties = v3_variant["properties"]
            assert v3_properties["resolved_agy_model"] == {
                "type": "string", "pattern": "^[a-z0-9]+(?:[.-][a-z0-9]+)+$", "maxLength": 128,
            }
            binding_rules = v3_properties["probed_executable"]["allOf"]
            assert binding_rules[0] == {"$ref": "#/properties/probed_executable"}
            current_binding = binding_rules[1]
            assert current_binding["required"] == ["content_sha256"]
            assert current_binding["properties"]["target_lstat"]["required"] == ["ctime_ns"]
            assert current_binding["properties"]["symlink_chain"]["items"]["properties"]["lstat"]["required"] == ["ctime_ns"]
            assert current_binding["properties"]["components"]["items"]["properties"]["lstat"]["required"] == ["ctime_ns"]
        MODULE.MODEL_SELECTION.validate_selection_record_shape(record)
        effort_record = copy.deepcopy(record)
        effort_record.update({
            "selection_mode": "model-effort",
            "user_effort": "high",
            "user_effort_source": "cli",
        })
        effort_record["compatibility_decision_sha256"] = (
            MODULE.MODEL_SELECTION.compatibility_decision_sha256(effort_record)
        )
        MODULE.MODEL_SELECTION.validate_selection_record_shape(effort_record)

        # V3 decoder validation must be no weaker than the public schema.  In
        # particular, recomputing the approval digest cannot make an invalid
        # model field a valid current selection artifact.
        for valid_record in (record, effort_record):
            for field in ("user_model", "resolved_agy_model"):
                for invalid_model in ("not a model slug", "a." * 64 + "a"):
                    malformed = copy.deepcopy(valid_record)
                    malformed[field] = invalid_model
                    malformed["compatibility_decision_sha256"] = (
                        MODULE.MODEL_SELECTION.compatibility_decision_sha256(malformed)
                    )
                    try:
                        MODULE.MODEL_SELECTION.validate_selection_record_shape(malformed)
                    except MODULE.MODEL_SELECTION.CallerError:
                        pass
                    else:
                        raise AssertionError("runtime decoder accepted an invalid V3 model slug")

        mutations = (
            lambda value: value.update({"resolved_agy_model": None}),
            lambda value: value.update({"resolved_agy_model": "not a model slug"}),
            lambda value: value["probed_executable"].pop("content_sha256"),
            lambda value: value["probed_executable"]["target_lstat"].pop("ctime_ns"),
            lambda value: value["probed_executable"].update({
                "symlink_chain": [{
                    "path_sha256": "1" * 64, "target_sha256": "2" * 64,
                    "lstat": {
                        "device": 1, "inode": 1, "mode": 0o120777,
                        "uid": os.geteuid(), "gid": os.getegid(), "size": 1,
                        "mtime_ns": 1,
                    },
                }],
            }),
            lambda value: value["probed_executable"].update({
                "components": [{
                    "path_sha256": "3" * 64,
                    "lstat": {
                        "device": 1, "inode": 1, "mode": 0o040755,
                        "uid": os.geteuid(), "gid": os.getegid(), "size": 1,
                        "mtime_ns": 1,
                    },
                }],
            }),
        )
        for mutate in mutations:
            malformed = copy.deepcopy(record)
            mutate(malformed)
            try:
                MODULE.MODEL_SELECTION.validate_selection_record_shape(malformed)
            except MODULE.MODEL_SELECTION.CallerError:
                pass
            else:
                raise AssertionError("runtime decoder accepted a malformed V3 schema shape")

    check("v3 selection schema rejects records missing the current runtime binding", v3_selection_schema_matches_the_current_runtime_binding)

    def completed_probe_descendants_are_always_reaped() -> None:
        help_text = b"""Usage of agy:\n  --add-dir  Add a directory\n  --conversation  Resume a conversation\n  --disable-slash-commands  Disable slash commands\n  --json-schema  Schema path\n  --mode  Execution mode (accept-edits, plan)\n  --model  Select a model\n  --output-format  Format (text, json, stream-json)\n  --print  Run a prompt\n  --print-timeout  Print timeout\n  --sandbox  Sandboxed\n"""

        def group_is_gone(child: int, group: int) -> bool:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.killpg(group, 0)
                except ProcessLookupError:
                    return True
                except PermissionError:
                    # The desktop sandbox can deny a negative-pid existence
                    # query after orphan reparenting; the known descendant PID
                    # remains an exact fallback observation for this fixture.
                    try:
                        os.kill(child, 0)
                    except ProcessLookupError:
                        return True
                    except PermissionError:
                        pass
                time.sleep(0.02)
            return False

        for argument in ("--version", "--help"):
            for returncode in (0, 23):
                label = f"{argument[2:]}-{returncode}"
                child_record = root / f"probe-child-{label}"
                provider_marker = root / f"probe-provider-{label}"
                probe = root / f"probe-{label}"
                probe.write_text(
                    "#!/usr/bin/env python3\n"
                    "import os, signal, sys, time\n"
                    f"child_record = {str(child_record)!r}\n"
                    f"provider_marker = {str(provider_marker)!r}\n"
                    f"help_text = {help_text!r}\n"
                    "child = os.fork()\n"
                    "if child == 0:\n"
                    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "    with open(child_record, 'w', encoding='ascii') as handle:\n"
                    "        handle.write(f'{os.getpid()} {os.getpgrp()}\\n')\n"
                    "    for descriptor in (0, 1, 2):\n"
                    "        try: os.close(descriptor)\n"
                    "        except OSError: pass\n"
                    "    while True: time.sleep(60)\n"
                    "deadline = time.monotonic() + 1.0\n"
                    "while (not os.path.exists(child_record) or os.path.getsize(child_record) == 0) and time.monotonic() < deadline:\n"
                    "    time.sleep(0.005)\n"
                    "if sys.argv[1:] == ['--version']:\n"
                    "    os.write(1, b'1.1.16\\n')\n"
                    "elif sys.argv[1:] == ['--help']:\n"
                    "    os.write(2, help_text)\n"
                    "else:\n"
                    "    open(provider_marker, 'w', encoding='ascii').write('provider\\n')\n"
                    f"os._exit({returncode})\n",
                    encoding="utf-8",
                )
                probe.chmod(0o755)
                if argument == "--version":
                    action = lambda: MODULE.MODEL_SELECTION.probe_installed_version(str(probe))
                else:
                    action = lambda: MODULE.MODEL_SELECTION.probe_critical_interface(str(probe))
                if returncode == 0:
                    result = action()
                    if argument == "--version":
                        assert result == "1.1.16"
                    else:
                        assert len(result) == 2
                else:
                    try:
                        action()
                    except MODULE.MODEL_SELECTION.EvidenceUnavailable:
                        pass
                    else:
                        raise AssertionError(f"nonzero {argument} leader was accepted")
                child, group = map(int, child_record.read_text(encoding="ascii").split())
                assert child != group and group_is_gone(child, group), (child, group)
                assert not provider_marker.exists()

    check("completed success and nonzero version/help probes reap pipe-closing live descendants", completed_probe_descendants_are_always_reaped)

    def completed_probe_leader_does_not_wait_for_descendant_held_pipe_eof() -> None:
        help_text = b"""Usage of agy:\n  --add-dir  Add a directory\n  --conversation  Resume a conversation\n  --disable-slash-commands  Disable slash commands\n  --json-schema  Schema path\n  --mode  Execution mode (accept-edits, plan)\n  --model  Select a model\n  --output-format  Format (text, json, stream-json)\n  --print  Run a prompt\n  --print-timeout  Print timeout\n  --sandbox  Sandboxed\n"""

        def group_is_gone(child: int, group: int) -> bool:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.killpg(group, 0)
                except ProcessLookupError:
                    return True
                except PermissionError:
                    try:
                        os.kill(child, 0)
                    except ProcessLookupError:
                        return True
                    except PermissionError:
                        pass
                time.sleep(0.02)
            return False

        for argument in ("--version", "--help"):
            label = argument[2:]
            child_record = root / f"probe-held-pipe-child-{label}"
            provider_marker = root / f"probe-held-pipe-provider-{label}"
            probe = root / f"probe-held-pipe-{label}"
            probe.write_text(
                "#!/usr/bin/env python3\n"
                "import os, signal, sys, time\n"
                f"child_record = {str(child_record)!r}\n"
                f"provider_marker = {str(provider_marker)!r}\n"
                f"help_text = {help_text!r}\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "    with open(child_record, 'w', encoding='ascii') as handle:\n"
                "        handle.write(f'{os.getpid()} {os.getpgrp()}\\n')\n"
                "    while True: time.sleep(60)\n"
                "deadline = time.monotonic() + 1.0\n"
                "while (not os.path.exists(child_record) or os.path.getsize(child_record) == 0) and time.monotonic() < deadline:\n"
                "    time.sleep(0.005)\n"
                "if sys.argv[1:] == ['--version']:\n"
                "    os.write(1, b'1.1.16\\n')\n"
                "elif sys.argv[1:] == ['--help']:\n"
                "    os.write(2, help_text)\n"
                "else:\n"
                "    open(provider_marker, 'w', encoding='ascii').write('provider\\n')\n"
                "os._exit(0)\n",
                encoding="utf-8",
            )
            probe.chmod(0o755)
            action = (
                (lambda: MODULE.MODEL_SELECTION.probe_installed_version(str(probe)))
                if argument == "--version"
                else (lambda: MODULE.MODEL_SELECTION.probe_critical_interface(str(probe)))
            )
            started = time.monotonic()
            result = action()
            elapsed = time.monotonic() - started
            assert elapsed < 2.00, elapsed
            if argument == "--version":
                assert result == "1.1.16"
            else:
                assert len(result) == 2
            child, group = map(int, child_record.read_text(encoding="ascii").split())
            assert child != group and group_is_gone(child, group), (child, group)
            assert not provider_marker.exists()

    check("successful version/help probe accepts leader output before descendant-held pipe EOF", completed_probe_leader_does_not_wait_for_descendant_held_pipe_eof)

    def selection_record_actions_are_mutually_exclusive() -> None:
        record = root / "mutual-selection.json"; record.write_text("{}\n", encoding="utf-8")
        selector = ROOT / "skills/agy-worker/runtime/scripts/model_selection.py"
        for args in (
            ("--validate-record", str(record), "--verify-record-executable", str(record)),
            ("--verify-record-executable", str(record), "--validate-record", str(record)),
        ):
            completed = subprocess.run([sys.executable, str(selector), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert completed.returncode == 64 and not completed.stdout

    check("selection validate and executable-verify actions reject each other in either order", selection_record_actions_are_mutually_exclusive)

    runtime_cases_path = ROOT / "tests/agy_worker_remediation_runtime_boundary_cases.py"
    runtime_cases_spec = importlib.util.spec_from_file_location(
        "agy_worker_remediation_runtime_boundary_cases", runtime_cases_path,
    )
    assert runtime_cases_spec is not None and runtime_cases_spec.loader is not None
    runtime_cases = importlib.util.module_from_spec(runtime_cases_spec)
    runtime_cases_spec.loader.exec_module(runtime_cases)
    runtime_cases.run(globals())

    def queued_baseline_drift_blocks_every_provider_launch_origin() -> None:
        def make_job(label: str) -> tuple[Path, Path, Path, dict]:
            repo = root / f"queued-drift-{label}-repo"; repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            job = (root / f"queued-drift-{label}-job"); job.mkdir(mode=0o700); job = job.resolve()
            bin_dir = root / f"queued-drift-{label}-bin"; bin_dir.mkdir()
            marker = root / f"queued-drift-{label}-provider"
            fake = bin_dir / "agy"
            fake.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nexit 99\n", encoding="utf-8")
            fake.chmod(0o755)
            schema = root / f"queued-drift-{label}-provider.json"; provider_schema(schema)
            command = {
                "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"queued-{label}",
                "workdir": str(repo), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
                "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            MODULE.create_state(job, "initial", resume=False)
            return repo, job, bin_dir, command

        for origin in ("initial", "conversation-resume", "fresh-restart", "conversation-continue"):
            repo, job, bin_dir, command = make_job(origin)
            state, _raw, sha = MODULE.load_state(job)
            if origin in {"conversation-resume", "fresh-restart"}:
                state.update({
                    "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
                    "finished_epoch": 1.0, "conversation_id": "queued-conversation",
                    "resume_available": True, "phase": "attempt-failed", "assurance": "pending",
                    "next_action": "resume", "driver_disposition": "not_applicable",
                })
                _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
                queued, _queued_sha = MODULE.create_state(job, origin, resume=True, approve_sha=sha)
                assert queued["attempt_origin"] == origin
            elif origin == "conversation-continue":
                envelope = job / "envelope.json"
                candidate_raw = json.dumps(report(), ensure_ascii=True, indent=2).encode("ascii") + b"\n"
                envelope.write_bytes(candidate_raw); envelope.chmod(0o600)
                _candidate, candidate_info = MODULE.read_regular(envelope, 1024 * 1024, "fixture candidate")
                snapshot = MODULE._worktree_snapshot(command["workdir"]); assert snapshot is not None
                state.update({
                    "status": "succeeded", "exit_code": 0, "finished_epoch": 1.0,
                    "conversation_id": "queued-conversation", "result_path": str(envelope),
                    "result_sha256": MODULE.digest(candidate_raw), "result_identity": list(MODULE._identity(candidate_info)),
                    "candidate_recognized": True, "candidate_source": "provider_success", "result_available": True,
                    "candidate_worktree_sha256": snapshot["sha256"], "candidate_worktree_entries": snapshot["entries"],
                    "driver_disposition": "unreviewed", "phase": "awaiting-verification", "assurance": "pending",
                    "continue_available": True, "next_action": "driver_review",
                })
                _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
                verification = {
                    "schema_version": 2, "summary": "driver found a defect", "passed_checks": [],
                    "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
                    "candidate_sha256": state["result_sha256"], "coverage": "partial",
                    "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
                }
                queued, _queued_sha = MODULE.create_state(
                    job, origin, resume=True, approve_sha=sha, verification=verification,
                )
                assert queued["candidate_recognized"]
            (repo / "empty-directory").mkdir()
            marker = root / f"queued-drift-{origin}-provider"
            assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
            assert not marker.exists(), f"{origin} reached provider Popen after queued drift"
            terminal, _raw, _sha = MODULE.load_state(job)
            assert (terminal["status"], terminal["reason"], terminal["failure_stage"]) == (
                "failed", "status_unavailable", "binding_failure",
            )

    check("initial resume restart and continue rebind queued worktree before provider Popen", queued_baseline_drift_blocks_every_provider_launch_origin)

    def empty_directory_topology_is_snapshot_bound() -> None:
        repo = root / "empty-topology-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        baseline = MODULE._worktree_snapshot(str(repo)); assert baseline is not None and baseline["entries"] == 0
        empty = repo / "empty"; empty.mkdir()
        created = MODULE._worktree_snapshot(str(repo)); assert created is not None
        assert created["sha256"] != baseline["sha256"] and created["entries"] == baseline["entries"] + 1
        os.utime(empty, ns=(empty.stat().st_atime_ns, empty.stat().st_mtime_ns + 1_000_000))
        metadata_changed = MODULE._worktree_snapshot(str(repo)); assert metadata_changed is not None
        assert metadata_changed == created
        empty.rmdir()
        removed = MODULE._worktree_snapshot(str(repo)); assert removed is not None
        assert removed["sha256"] == baseline["sha256"] and removed["entries"] == baseline["entries"]
        command = {
            "workdir": str(repo), "workflow": "task", "max_cycles": 2, "job_id": "empty-topology",
            "hard_seconds": 2, "max_seconds": 4, "idle_seconds": 1,
        }
        state = MODULE.initial_state(
            command, "initial", 1, command_sha="0" * 64,
            command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None,
        )
        empty.mkdir()
        try:
            MODULE._bound_worktree_baseline(state, command)
        except MODULE.WorktreeBaselineError:
            pass
        else:
            raise AssertionError("empty directory topology drift was accepted as a queued baseline")

    check("empty directory create remove and metadata changes bind snapshot digest and entries", empty_directory_topology_is_snapshot_bound)

    def snapshot_git_requires_safe_path_authority_before_execution() -> None:
        repo = root / "safe-git-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        assert MODULE._worktree_snapshot(str(repo)) is not None, "the installed Git positive control is unavailable"
        real_git = shutil.which("git"); assert real_git is not None
        bin_dir = root / "safe-git-bin"; bin_dir.mkdir(mode=0o700); bin_dir.chmod(0o700)
        marker = root / "safe-git-marker"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\nprintf ran > " + shlex.quote(str(marker)) + "\nexec "
            + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o700)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
        try:
            assert MODULE._worktree_snapshot(str(repo)) is not None
            assert marker.exists(), "an owner-private safe Git did not execute"
            marker.unlink()
            assert MODULE._dispatch_root_identity(str(repo)) is not None
            assert marker.exists(), "root binding rejected an owner-private external Git"
            marker.unlink()
            if sys.platform == "darwin":
                # The hosted Apple-Silicon image may put Homebrew's writable
                # launcher ahead of the root-owned platform Git.  A rejected
                # launcher must never be executed or trusted; the controller
                # may use only a separately bound system Git fallback.
                original_which = MODULE.shutil.which
                original_lstat = MODULE.os.lstat
                original_git_read = MODULE._bounded_git_read
                homebrew_marker = root / "rejected-homebrew-git-marker"
                homebrew_paths = {
                    "/opt/homebrew": bin_dir,
                    "/opt/homebrew/bin": bin_dir,
                    "/opt/homebrew/bin/git": fake,
                }

                def rejected_homebrew_lstat(path, *arguments, **keywords):
                    try:
                        mapped = homebrew_paths.get(os.fsdecode(path))
                    except (TypeError, UnicodeError):
                        mapped = None
                    return original_lstat(path if mapped is None else mapped, *arguments, **keywords)

                def observed_git_read(executable, *arguments, **keywords):
                    if executable == "/opt/homebrew/bin/git":
                        homebrew_marker.write_text("unexpected execution", encoding="utf-8")
                    return original_git_read(executable, *arguments, **keywords)

                MODULE.shutil.which = lambda name: "/opt/homebrew/bin/git" if name == "git" else original_which(name)
                MODULE.os.lstat = rejected_homebrew_lstat
                MODULE._bounded_git_read = observed_git_read
                fake.chmod(0o777)
                try:
                    fallback = MODULE._safe_git_executable()
                    assert fallback is not None and fallback[0] == "/usr/bin/git"
                    assert MODULE._worktree_snapshot(str(repo)) is not None
                    assert MODULE._dispatch_root_identity(str(repo)) is not None
                    assert not homebrew_marker.exists(), "a rejected Homebrew launcher executed"
                finally:
                    fake.chmod(0o700)
                    MODULE._bounded_git_read = original_git_read
                    MODULE.os.lstat = original_lstat
                    MODULE.shutil.which = original_which
            fake.chmod(0o777)
            assert MODULE._worktree_snapshot(str(repo)) is None
            assert not marker.exists(), "a group/world-writable Git target executed"
            fake.chmod(0o700)
            bin_dir.chmod(0o777)
            assert MODULE._worktree_snapshot(str(repo)) is None
            assert not marker.exists(), "a group/world-writable Git ancestor executed"
            bin_dir.chmod(0o700)
            repo_bin = repo / "bin"; repo_bin.mkdir(mode=0o700)
            inner_marker = root / "repo-controlled-git-marker"
            inner_git = repo_bin / "git"
            inner_git.write_text(
                "#!/bin/sh\nprintf ran > " + shlex.quote(str(inner_marker)) + "\nexec "
                + shlex.quote(real_git) + " \"$@\"\n",
                encoding="utf-8",
            )
            inner_git.chmod(0o700)
            os.environ["PATH"] = f"{repo_bin}{os.pathsep}{previous_path}"
            assert MODULE._git_boundary_identity(str(repo)) is None
            assert MODULE._dispatch_root_identity(str(repo)) is None
            assert not inner_marker.exists(), "a repository-controlled Git executed during root binding"
        finally:
            bin_dir.chmod(0o700)
            os.environ["PATH"] = previous_path

    check("snapshot and root binding reject unsafe or repository-controlled Git before execution", snapshot_git_requires_safe_path_authority_before_execution)

    def partial_finalize_returns_the_current_error_or_cancelled_candidate() -> None:
        for label, status, reason, source_name, origin, attempt in (
            ("first-error", "failed", "provider_terminal_error", "provider_error", "initial", 1),
            ("repair-cancelled", "cancelled", "provider_terminal_cancelled", "provider_cancelled", "conversation-continue", 2),
        ):
            source_repo = root / f"result-current-{label}-source"; source_repo.mkdir()
            subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
            (source_repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source_repo), "add", "base.txt"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "commit", "-qm", "base"], check=True)
            repo = root / f"result-current-{label}-repo"
            subprocess.run(
                ["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", f"result-{label}", str(repo)],
                check=True,
            )
            repo = repo.resolve()
            job = root / f"result-current-{label}-job"; job.mkdir(mode=0o700); job = job.resolve()
            schema = root / f"result-current-{label}-provider.json"; provider_schema(schema)
            command = {
                "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"result-{label}",
                "workdir": str(repo), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "project",
                "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            state, _state_sha = MODULE.create_state(job, "initial", resume=False)
            older = job / "older.json"; current = job / "current.json"
            older_raw = json.dumps(report(summary="candidate-A"), ensure_ascii=True, indent=2).encode("ascii") + b"\n"
            current_raw = json.dumps(report(summary=f"candidate-B-{label}"), ensure_ascii=True, indent=2).encode("ascii") + b"\n"
            for path, raw in ((older, older_raw), (current, current_raw)):
                path.write_bytes(raw); path.chmod(0o600)
            _raw, older_info = MODULE.read_regular(older, 1024 * 1024, "older candidate")
            _raw, current_info = MODULE.read_regular(current, 1024 * 1024, "current candidate")
            snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
            state.update({
                "status": status, "reason": reason, "exit_code": 25 if status == "failed" else 22,
                "finished_epoch": 1.0, "attempt_origin": origin, "attempt": attempt, "cycle": attempt,
                "conversation_id": "result-conversation", "result_path": str(current),
                "result_sha256": MODULE.digest(current_raw), "result_identity": list(MODULE._identity(current_info)),
                "candidate_recognized": True, "candidate_source": source_name, "result_available": True,
                "candidate_worktree_sha256": snapshot["sha256"], "candidate_worktree_entries": snapshot["entries"],
                "driver_disposition": "unreviewed", "phase": "awaiting-verification", "assurance": "pending",
                "continue_available": False, "resume_available": False, "next_action": "driver_review",
                "last_success_path": str(older), "last_success_sha256": MODULE.digest(older_raw),
                "last_success_identity": list(MODULE._identity(older_info)),
            })
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            verification = {
                "schema_version": 2, "summary": "driver observed unresolved behavior", "passed_checks": [],
                "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": state["result_sha256"], "coverage": "partial",
                "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
            }
            wrong = dict(verification); wrong["candidate_sha256"] = state["last_success_sha256"]
            wrong_result = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", sha, "--assurance", "partially_verified"],
                input=json.dumps(wrong).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert wrong_result.returncode == 64 and not wrong_result.stdout
            accepted = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", sha, "--assurance", "partially_verified"],
                input=json.dumps(verification).encode("utf-8"), check=True, stdout=subprocess.PIPE,
            )
            finalized = json.loads(accepted.stdout)
            assert finalized["phase"] == "completed" and finalized["assurance"] == "partially_verified"
            delivered = subprocess.run(
                [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
                check=True, stdout=subprocess.PIPE,
            )
            assert json.loads(delivered.stdout)["summary"] == f"candidate-B-{label}"
            current.write_bytes(b"{}\n"); current.chmod(0o600)
            tampered = subprocess.run(
                [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert tampered.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not tampered.stdout

    check("partial finalization returns current ERROR/CANCELED candidate and rejects A verification or B tamper", partial_finalize_returns_the_current_error_or_cancelled_candidate)

    def current_candidate_fixture(
        label: str, *, selection: bool = False, staged: bool = False,
        wrapper_addressable: bool = False, workflow: str = "task", linked: bool = False,
        inside_worktree: bool = False,
    ) -> tuple[Path, dict, str, Path]:
        """Create one schema-bound terminal candidate without any provider call."""
        repo = root / f"current-candidate-{label}-repo"
        if linked:
            origin = root / f"current-candidate-{label}-origin"; origin.mkdir()
            subprocess.run(["git", "init", "-q", str(origin)], check=True)
            subprocess.run(["git", "-C", str(origin), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(origin), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(origin), "commit", "--allow-empty", "-qm", "base"], check=True)
            subprocess.run([
                "git", "-C", str(origin), "worktree", "add", "-q", "-b",
                f"current-candidate-{label}", str(repo),
            ], check=True)
            repo = repo.resolve()
        else:
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
        job_id = f"current-{label}"
        if inside_worktree:
            logs_dir = repo / "logs"
            logs_dir.mkdir(mode=0o700, exist_ok=True)
            job = logs_dir / job_id
        else:
            job = root / (job_id if wrapper_addressable else f"current-candidate-{label}-job")
        job.mkdir(mode=0o700); job = job.resolve()
        schema = root / f"current-candidate-{label}-provider.json"; provider_schema(schema)
        selection_path = None
        selection_sha = None
        selection_identity = None
        if selection:
            selection_path = job / "selection.json"
            MODULE.MODEL_SELECTION.publish_record(
                selection_path, MODULE.MODEL_SELECTION.resolve_literal_selection("gemini-3.7-flash"),
            )
            selection_raw, selection_info = MODULE.read_regular(
                selection_path, MODULE.MAX_COMMAND_BYTES, "fixture selection",
            )
            selection_sha = MODULE.digest(selection_raw)
            selection_identity = list(MODULE._identity(selection_info))
        stage_dir = None
        stage_file = None
        if staged:
            stage_dir = job / "staged"; stage_dir.mkdir(mode=0o700)
            stage_file = stage_dir / "full-prompt.txt"
            stage_file.write_text("bounded fixture prompt", encoding="utf-8")
            stage_file.chmod(0o600)
        command = {
            "schema_version": 4 if selection else 3, "kind": "agy-worker-dispatch-command", "job_id": job_id,
            "workdir": str(repo), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 10, "max_seconds": 20, "notice_seconds": 3,
            "stage_dir": None if stage_dir is None else str(stage_dir),
            "stage_file": None if stage_file is None else str(stage_file), "child_umask": "022", "workflow": workflow,
            "max_cycles": 1 if workflow == "legacy" else 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        if selection:
            command.update({
                "selection_path": str(selection_path), "selection_sha256": selection_sha,
                "selection_identity": selection_identity,
            })
        MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
        state, _initial_sha = MODULE.create_state(job, "initial", resume=False)
        envelope = job / "envelope.json"
        payload = json.dumps(report(summary=f"candidate-{label}"), ensure_ascii=True, indent=2).encode("ascii") + b"\n"
        envelope.write_bytes(payload); envelope.chmod(0o600)
        _bound, info = MODULE.read_regular(envelope, 1024 * 1024, "fixture envelope")
        snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
        state.update({
            "status": "succeeded", "exit_code": 0, "finished_epoch": 1.0,
            "conversation_id": "candidate-conversation", "result_path": str(envelope),
            "result_sha256": MODULE.digest(payload), "result_identity": list(MODULE._identity(info)),
            "candidate_recognized": True, "candidate_source": "provider_success", "result_available": True,
            "candidate_worktree_sha256": snapshot["sha256"], "candidate_worktree_entries": snapshot["entries"],
            "driver_disposition": "unreviewed", "phase": "awaiting-verification", "assurance": "pending",
            "continue_available": False, "resume_available": False, "next_action": "driver_review",
        })
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        # The status command must consume the same current binding the mutation
        # commands consume, so make the fixture prove it is well-formed first.
        loaded, _raw, loaded_sha = MODULE.load_state(job)
        assert loaded_sha == sha
        return job, loaded, sha, envelope

    def v9_repository_controlled_git_fails_closed_before_guarded_commands() -> None:
        """A V9 root probe must never execute a worktree's ``bin/git`` wrapper."""
        job, state, sha, _envelope = current_candidate_fixture("v9-root-git-guard")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        real_git = shutil.which("git"); assert real_git is not None
        repo_bin = repo / "bin"; repo_bin.mkdir(mode=0o700); repo_bin.chmod(0o700)
        marker = root / "v9-root-git-executed"
        wrapper = repo_bin / "git"
        wrapper.write_text(
            "#!/bin/sh\nprintf ran > " + shlex.quote(str(marker)) + "\nexec "
            + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        assert not MODULE._safe_git_is_outside_worktree(str(wrapper), str(repo))
        assert MODULE._safe_git_is_outside_worktree(
            str(repo.with_name(repo.name + "-sibling") / "git"), str(repo),
        )
        verification = {
            "schema_version": 2, "summary": "driver found a bounded defect", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        before = (job / MODULE.STATE_NAME).read_bytes()
        verification_path = job / "verification-v2.json"
        environment = {**os.environ, "PATH": f"{repo_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        commands = (
            ([sys.executable, str(SOURCE), "status", "--job-dir", str(job)], None),
            ([sys.executable, str(SOURCE), "result", "--job-dir", str(job)], None),
            ([sys.executable, str(SOURCE), "continue", "--job-dir", str(job), "--approve-state-sha", sha], verification),
            ([sys.executable, str(SOURCE), "finalize", "--job-dir", str(job), "--approve-state-sha", sha,
              "--assurance", "partially_verified"], verification),
        )
        for arguments, payload in commands:
            completed = subprocess.run(
                arguments,
                input=None if payload is None else json.dumps(payload).encode("utf-8"),
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if arguments[2] == "status":
                assert completed.returncode == 0
            else:
                assert completed.returncode != 0 and not completed.stdout, arguments
            assert not marker.exists(), arguments
            assert (job / MODULE.STATE_NAME).read_bytes() == before, arguments
            assert not verification_path.exists(), arguments
            assert not list(job.glob("stream.ndjson")), arguments
            assert str(repo).encode("utf-8") not in completed.stderr, arguments
            assert str(wrapper).encode("utf-8") not in completed.stderr, arguments
        status = json.loads(subprocess.run(
            [sys.executable, str(SOURCE), "status", "--job-dir", str(job)],
            env=environment, check=True, stdout=subprocess.PIPE,
        ).stdout)
        assert not ({"result", "continue", "finalize"} & {
            item["action"] for item in status["available_actions"]
        })
        assert not marker.exists()

    check("V9 repository-controlled Git fails closed before status result continue or finalize", v9_repository_controlled_git_fails_closed_before_guarded_commands)

    def outward_symlink_removes_candidate_actions_and_rejects_their_commands() -> None:
        """Status must not advertise lifecycle actions that their guards reject."""
        job, state, _sha, _envelope = current_candidate_fixture("outward-lifecycle")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
        state.update({
            "candidate_worktree_sha256": snapshot["sha256"],
            "candidate_worktree_entries": snapshot["entries"],
            "continue_available": True,
        })
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        before_public = MODULE.public_status(state, sha, job=job)
        assert {"result", "continue", "finalize", "restart"} <= {
            item["action"] for item in before_public["available_actions"]
        }
        outside = root / "outward-lifecycle-outside"; outside.mkdir()
        (repo / "escape").symlink_to(outside, target_is_directory=True)
        before = (job / MODULE.STATE_NAME).read_bytes()
        verification = {
            "schema_version": 2, "summary": "bounded fixture", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        commands = (
            ([sys.executable, str(SOURCE), "status", "--job-dir", str(job)], None, True),
            ([sys.executable, str(SOURCE), "result", "--job-dir", str(job)], None, False),
            ([sys.executable, str(SOURCE), "continue", "--job-dir", str(job),
              "--approve-state-sha", sha], verification, False),
            ([sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
              "--approve-state-sha", sha, "--assurance", "partially_verified"], verification, False),
            ([sys.executable, str(SOURCE), "restart", "--job-dir", str(job),
              "--approve-state-sha", sha], None, False),
        )
        for arguments, payload, is_status in commands:
            completed = subprocess.run(
                arguments, input=None if payload is None else json.dumps(payload).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if is_status:
                assert completed.returncode == 0
                public = json.loads(completed.stdout)
                assert public["available_actions"] == []
                assert public["next_action"] == "none"
                assert public["next_action_command"] is None
            else:
                # `result` maps a binding failure to the worker-safe status
                # code, while lifecycle mutation argument handling reports a
                # local command error.  Neither may acquire an action that
                # status hid.
                assert completed.returncode in {64, MODULE.EXIT_BY_REASON["status_unavailable"]}, (
                    arguments, completed.returncode, completed.stderr,
                )
                assert not completed.stdout
            assert (job / MODULE.STATE_NAME).read_bytes() == before
            assert not list(job.glob("stream.ndjson"))
            assert str(repo).encode("utf-8") not in completed.stderr
            assert str(outside).encode("utf-8") not in completed.stderr

    check("outward symlink keeps candidate lifecycle status and commands in parity", outward_symlink_removes_candidate_actions_and_rejects_their_commands)

    def internal_git_alias_symlink_keeps_candidate_lifecycle_fail_closed() -> None:
        """A contained link may not alias the worktree's Git authority."""
        job, state, sha, _envelope = current_candidate_fixture("internal-git-alias")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        (repo / "admin").symlink_to(".git")
        assert not MODULE._worktree_symlink_boundary(str(repo))
        assert MODULE._worktree_snapshot(str(repo)) is None

        before = (job / MODULE.STATE_NAME).read_bytes()
        public = MODULE.public_status(state, sha, job=job)
        assert public["available_actions"] == []
        assert public["resume_available"] is False
        assert public["continue_available"] is False
        assert public["next_action"] == "none"
        assert public["next_action_command"] is None

        completed = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.returncode == MODULE.EXIT_BY_REASON["status_unavailable"]
        assert not completed.stdout
        assert (job / MODULE.STATE_NAME).read_bytes() == before
        assert not list(job.glob("stream.ndjson"))
        assert str(repo).encode("utf-8") not in completed.stderr

    check("internal Git-alias symlink is rejected by lifecycle candidate guards", internal_git_alias_symlink_keeps_candidate_lifecycle_fail_closed)

    def verification_copy_preserves_a_stale_ignored_candidate_during_driver_imports() -> None:
        """Driver checks run on a copy; ignored candidate bytes are never reconciled away."""
        verification = {
            "schema_version": 2, "summary": "driver found a bounded defect", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "coverage": "partial", "verified_findings": 1, "unresolved_gaps": 1,
            "diff_review_complete": True,
        }

        def fixture(label: str) -> tuple[Path, dict, str, Path, str]:
            job, state, _sha, _envelope = current_candidate_fixture(label)
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            repo = Path(command["workdir"])
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            package = repo / "rich"; package.mkdir()
            (repo / ".gitignore").write_text("__pycache__/\n__driver_pyc_prefix/\n", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")
            source = package / "_wrap.py"; source.write_text("VALUE = 'before'\n", encoding="utf-8")
            executable = repo / "driver-tool"; executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8"); executable.chmod(0o755)
            subprocess.run(["git", "-C", str(repo), "add", ".gitignore", "rich", "driver-tool"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            # Provider imports first, then edits the tracked source.  This is
            # the observed stale-pyc shape: the terminal candidate includes
            # both the revised source and the older ignored bytecode.
            import_environment = dict(os.environ)
            import_environment.pop("PYTHONDONTWRITEBYTECODE", None)
            import_environment["PYTHONPYCACHEPREFIX"] = str(repo / "__driver_pyc_prefix")
            subprocess.run(
                [sys.executable, "-c", "import rich._wrap, py_compile; py_compile.compile('rich/_wrap.py', doraise=True)"],
                cwd=repo, env=import_environment, check=True,
            )
            stale_pyc = next((repo / "__driver_pyc_prefix").rglob("*.pyc"))
            subprocess.run(
                ["git", "-C", str(repo), "check-ignore", "-q", str(stale_pyc.relative_to(repo))],
                check=True,
            )
            source.write_text("VALUE = 'provider revised'\n", encoding="utf-8")
            (repo / "provider-untracked.txt").write_text("candidate artifact\n", encoding="utf-8")
            snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
            assert (repo / "__driver_pyc_prefix").is_dir()
            state.update({
                "candidate_worktree_sha256": snapshot["sha256"],
                "candidate_worktree_entries": snapshot["entries"],
                "continue_available": True,
            })
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            return job, state, sha, repo, snapshot["sha256"]

        direct_job, direct_state, direct_sha, direct_repo, direct_snapshot = fixture("stale-pyc-direct")
        import_environment = dict(os.environ)
        import_environment.pop("PYTHONDONTWRITEBYTECODE", None)
        import_environment["PYTHONPYCACHEPREFIX"] = str(direct_repo / "__driver_pyc_prefix")
        subprocess.run([sys.executable, "-c", "import rich._wrap"], cwd=direct_repo, env=import_environment, check=True)
        assert MODULE._worktree_snapshot(str(direct_repo))["sha256"] != direct_snapshot
        direct_public = MODULE.public_status(direct_state, direct_sha, job=direct_job)
        assert not ({"result", "verification-copy", "continue", "finalize"} & {
            item["action"] for item in direct_public["available_actions"]
        })
        direct_parent = root / "stale-pyc-direct-parent"; direct_parent.mkdir(mode=0o700); direct_parent = direct_parent.resolve()
        direct_before = (direct_job / MODULE.STATE_NAME).read_bytes()
        direct_copy = subprocess.run(
            [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(direct_job),
             "--destination", str(direct_parent / "candidate")], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert direct_copy.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not direct_copy.stdout
        assert not (direct_parent / "candidate").exists()
        assert (direct_job / MODULE.STATE_NAME).read_bytes() == direct_before
        assert str(direct_repo).encode("utf-8") not in direct_copy.stderr

        for label, mutate in (("untracked", "untracked"), ("tracked", "tracked"), ("mixed-git", "mixed-git")):
            rejected_job, rejected_state, rejected_sha, rejected_repo, _snapshot = fixture(f"copy-reject-{label}")
            if mutate == "tracked":
                (rejected_repo / "rich" / "_wrap.py").write_text("driver drift\n", encoding="utf-8")
            elif mutate == "untracked":
                (rejected_repo / "provider-untracked.txt").write_text("driver drift\n", encoding="utf-8")
            else:
                nested = rejected_repo / "nested"; nested.mkdir()
                (nested / ".GiT").write_text("nested administration\n", encoding="utf-8")
            reject_parent = root / f"copy-reject-{label}-parent"; reject_parent.mkdir(mode=0o700); reject_parent = reject_parent.resolve()
            reject_destination = reject_parent / "candidate"
            before = (rejected_job / MODULE.STATE_NAME).read_bytes()
            rejected = subprocess.run(
                [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(rejected_job),
                 "--destination", str(reject_destination)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert rejected.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not rejected.stdout
            assert not reject_destination.exists()
            assert (rejected_job / MODULE.STATE_NAME).read_bytes() == before
            assert str(rejected_repo).encode("utf-8") not in rejected.stderr
            assert "verification-copy" not in {
                item["action"] for item in MODULE.public_status(rejected_state, rejected_sha, job=rejected_job)["available_actions"]
            }

        copy_job, copy_state, copy_sha, source_repo, source_snapshot = fixture("stale-pyc-copy")
        public = MODULE.public_status(copy_state, copy_sha, job=copy_job)
        actions = {item["action"] for item in public["available_actions"]}
        assert {"result", "verification-copy", "continue", "finalize", "restart"} <= actions
        assert_symbolic_action_commands(
            public["available_actions"], {"result", "verification-copy", "continue", "finalize", "restart"},
        )
        copy_action = next(item["command"] for item in public["available_actions"] if item["action"] == "verification-copy")
        assert str(source_repo) not in copy_action and str(copy_job) not in copy_action
        copy_parent = root / "stale-pyc-copy-parent"; copy_parent.mkdir(mode=0o700); copy_parent = copy_parent.resolve()
        destination = copy_parent / "candidate"
        copied = subprocess.run(
            [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(copy_job),
             "--destination", str(destination)],
            check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(copied.stdout) == {
            "candidate_sha256": copy_state["result_sha256"], "verification_copy": "created",
        }
        assert not (destination / ".git").exists()
        assert (destination / "provider-untracked.txt").read_text(encoding="utf-8") == "candidate artifact\n"
        assert stat.S_IMODE((destination / "driver-tool").stat().st_mode) & 0o111
        copy_import_environment = dict(import_environment)
        copy_import_environment["PYTHONPYCACHEPREFIX"] = str(destination / "__driver_pyc_prefix")
        subprocess.run([sys.executable, "-c", "import rich._wrap"], cwd=destination, env=copy_import_environment, check=True)
        assert MODULE._worktree_snapshot(str(source_repo))["sha256"] == source_snapshot
        still_bound = MODULE.public_status(copy_state, copy_sha, job=copy_job)
        assert {"result", "verification-copy", "continue", "finalize"} <= {
            item["action"] for item in still_bound["available_actions"]
        }

        # New private destination, source-root exclusion, and stale/dirtied
        # candidates all fail before a helper can promote a copied artifact.
        reused = subprocess.run(
            [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(copy_job),
             "--destination", str(destination)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert reused.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not reused.stdout
        inside = subprocess.run(
            [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(copy_job),
             "--destination", str(source_repo / "unsafe-copy")], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert inside.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not inside.stdout

        verification["candidate_sha256"] = copy_state["result_sha256"]
        finalized = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(copy_job),
             "--approve-state-sha", copy_sha, "--assurance", "partially_verified"],
            input=json.dumps(verification).encode("utf-8"), check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(finalized.stdout)["driver_disposition"] == "partially_verified"

    check("stale ignored pyc makes direct driver imports unusable while a bound verification copy preserves finalization", verification_copy_preserves_a_stale_ignored_candidate_during_driver_imports)

    def verification_copy_rebases_all_contained_symlinks() -> None:
        """Verifier writes through internal links must never reach the candidate."""
        job, state, _sha, _envelope = current_candidate_fixture("verification-copy-contained-links")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        target = repo / "nested" / "target.txt"; target.parent.mkdir()
        target.write_text("candidate bytes\n", encoding="utf-8")
        absolute_link = repo / "absolute-internal"
        absolute_link.symlink_to(target.resolve())
        relative_link = repo / "relative-internal"
        # This resolves within the source, but the same spelling in a sibling
        # verification directory would resolve back into the source candidate.
        relative_link.symlink_to(f"../{repo.name}/nested/target.txt")
        source_absolute_target = os.readlink(absolute_link)
        source_relative_target = os.readlink(relative_link)
        snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
        state.update({
            "candidate_worktree_sha256": snapshot["sha256"],
            "candidate_worktree_entries": snapshot["entries"],
        })
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        assert "verification-copy" in {
            item["action"] for item in MODULE.public_status(state, sha, job=job)["available_actions"]
        }
        parent = repo.parent.resolve()
        assert stat.S_IMODE(parent.lstat().st_mode) == 0o700
        destination = parent / "verification-copy-contained-links-copy"

        copied = subprocess.run(
            [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(job),
             "--destination", str(destination)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert copied.returncode == 0 and not copied.stderr, copied.stderr.decode("utf-8", "replace")
        assert json.loads(copied.stdout) == {
            "candidate_sha256": state["result_sha256"], "verification_copy": "created",
        }
        assert stat.S_IMODE(destination.lstat().st_mode) == 0o700
        assert stat.S_IMODE(parent.lstat().st_mode) == 0o700
        assert not (destination / ".git").exists()
        copied_absolute = destination / absolute_link.name
        copied_relative = destination / relative_link.name
        assert copied_absolute.is_symlink() and copied_relative.is_symlink()
        # The absolute source spelling must not occur in a completed verifier
        # workspace: it would make the candidate writable through the copy.
        assert os.readlink(copied_absolute) != source_absolute_target
        assert os.readlink(copied_relative) != source_relative_target
        assert copied_absolute.resolve() == destination / "nested" / "target.txt"
        assert copied_relative.resolve() == destination / "nested" / "target.txt"

        copied_absolute.write_text("absolute verifier write\n", encoding="utf-8")
        assert target.read_text(encoding="utf-8") == "candidate bytes\n"
        copied_relative.write_text("relative verifier write\n", encoding="utf-8")
        assert (destination / "nested" / "target.txt").read_text(encoding="utf-8") == "relative verifier write\n"
        assert target.read_text(encoding="utf-8") == "candidate bytes\n"
        assert MODULE._worktree_snapshot(str(repo))["sha256"] == snapshot["sha256"]

        verification = {
            "schema_version": 2, "summary": "isolated link driver checks", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        finalized = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "partially_verified"],
            input=json.dumps(verification).encode("utf-8"), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
        assert finalized.returncode == 0 and not finalized.stderr, finalized.stderr.decode("utf-8", "replace")
        assert json.loads(finalized.stdout)["driver_disposition"] == "partially_verified"

    check("verification copy rebases all contained symlinks before driver writes", verification_copy_rebases_all_contained_symlinks)

    def verification_copy_retains_readonly_nested_directory_mode() -> None:
        """A source directory may be read-only without making the copy unwritable mid-walk."""
        job, state, _sha, _envelope = current_candidate_fixture("verification-copy-readonly-directory")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        nested = repo / "readonly"; nested.mkdir()
        payload = nested / "payload.txt"; payload.write_text("candidate bytes\n", encoding="utf-8")
        nested.chmod(0o555)
        try:
            snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
            state.update({
                "candidate_worktree_sha256": snapshot["sha256"],
                "candidate_worktree_entries": snapshot["entries"],
            })
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            parent = root / "verification-copy-readonly-directory-parent"; parent.mkdir(mode=0o700)
            destination = parent.resolve() / "candidate"
            copied = subprocess.run(
                [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(job),
                 "--destination", str(destination)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert copied.returncode == 0 and not copied.stderr, copied.stderr.decode("utf-8", "replace")
            assert (destination / "readonly" / "payload.txt").read_text(encoding="utf-8") == "candidate bytes\n"
            assert stat.S_IMODE((destination / "readonly").lstat().st_mode) == 0o555
            assert MODULE._worktree_snapshot(str(repo))["sha256"] == snapshot["sha256"]
            verification = {
                "schema_version": 2, "summary": "readonly nested directory copy", "passed_checks": [],
                "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": state["result_sha256"], "coverage": "partial",
                "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
            }
            finalized = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", sha, "--assurance", "partially_verified"],
                input=json.dumps(verification).encode("utf-8"), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False,
            )
            assert finalized.returncode == 0 and not finalized.stderr, finalized.stderr.decode("utf-8", "replace")
            assert json.loads(finalized.stdout)["driver_disposition"] == "partially_verified"
        finally:
            nested.chmod(0o755)

    check("verification copy retains readonly nested directory mode and candidate binding", verification_copy_retains_readonly_nested_directory_mode)

    def verification_copy_rejects_unsafe_sources_and_discards_partial_output() -> None:
        """Copy-local checks reject broken/outward/Git links without a usable destination."""
        source = root / "verification-copy-unsafe-source"; source.mkdir()
        (source / "payload.txt").write_text("candidate\n", encoding="utf-8")
        parent = root / "verification-copy-unsafe-parent"; parent.mkdir(mode=0o700)
        parent = parent.resolve()

        original_copy2 = MODULE.shutil.copy2
        def fail_copy(*args, **kwargs):
            raise OSError("fixture copy failure")
        MODULE.shutil.copy2 = fail_copy
        try:
            try:
                MODULE._copy_bound_candidate(source, parent / "partial")
            except MODULE.DispatchError:
                pass
            else:
                raise AssertionError("a regular-file copy failure was accepted")
        finally:
            MODULE.shutil.copy2 = original_copy2
        assert not (parent / "partial").exists()

        readonly_source = root / "verification-copy-discard-source"; readonly_source.mkdir()
        readonly_nested = readonly_source / "readonly"; readonly_nested.mkdir()
        (readonly_nested / "payload.txt").write_text("candidate\n", encoding="utf-8")
        readonly_nested.chmod(0o555)
        discard_destination = parent / "readonly-discard"
        try:
            MODULE._copy_bound_candidate(readonly_source, discard_destination)
            assert stat.S_IMODE((discard_destination / "readonly").lstat().st_mode) == 0o555
            MODULE._discard_verification_copy(discard_destination)
            assert not discard_destination.exists()
        finally:
            readonly_nested.chmod(0o755)

        for label, prepare in (
            ("broken", lambda: (source / "link").symlink_to("missing")),
            ("outward", lambda: (source / "link").symlink_to(root / "outside-copy-link")),
            ("nested-git", lambda: (source / "nested" / ".git").parent.mkdir()),
        ):
            shutil.rmtree(source)
            source.mkdir(); (source / "payload.txt").write_text("candidate\n", encoding="utf-8")
            if label == "outward":
                (root / "outside-copy-link").write_text("outside\n", encoding="utf-8")
            prepare()
            if label == "nested-git":
                (source / "nested" / ".git").write_text("foreign administration\n", encoding="utf-8")
            destination = parent / label
            try:
                MODULE._copy_bound_candidate(source, destination)
            except MODULE.DispatchError:
                pass
            else:
                raise AssertionError(f"unsafe {label} source was copied")
            assert not destination.exists(), label

        job, state, _sha, _envelope = current_candidate_fixture("verification-copy-source-race")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        raced = repo / "raced.txt"; raced.write_text("before\n", encoding="utf-8")
        snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
        state.update({
            "candidate_worktree_sha256": snapshot["sha256"],
            "candidate_worktree_entries": snapshot["entries"],
        })
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        race_parent = root / "verification-copy-source-race-parent"; race_parent.mkdir(mode=0o700)
        race_destination = race_parent.resolve() / "candidate"
        original_copy = MODULE._copy_bound_candidate
        def copy_then_mutate(worktree: Path, destination: Path) -> None:
            original_copy(worktree, destination)
            raced.write_text("changed during copy\n", encoding="utf-8")
        MODULE._copy_bound_candidate = copy_then_mutate
        before = (job / MODULE.STATE_NAME).read_bytes()
        try:
            try:
                MODULE.command_verification_copy(job, race_destination, "json")
            except MODULE.DispatchError as exc:
                assert str(exc) == "candidate changed while creating verification copy"
            else:
                raise AssertionError("post-copy source mutation was accepted")
        finally:
            MODULE._copy_bound_candidate = original_copy
        assert not race_destination.exists()
        assert (job / MODULE.STATE_NAME).read_bytes() == before
        assert MODULE._worktree_snapshot(str(repo))["sha256"] != snapshot["sha256"]
        assert "verification-copy" not in {
            item["action"] for item in MODULE.public_status(state, sha, job=job)["available_actions"]
        }

    check("verification copy rejects unsafe links and source drift without a usable destination", verification_copy_rejects_unsafe_sources_and_discards_partial_output)

    def public_verification_copy_wrapper_has_exact_success_privacy_and_runtime_exits() -> None:
        """The supported wrapper keeps copy output private and distinguishes runtime failure."""
        def fixture(label: str) -> tuple[Path, dict, str, Path, Path, Path]:
            job, state, _sha, envelope = current_candidate_fixture(label, wrapper_addressable=True)
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            repo = Path(command["workdir"])
            log_root = root / f"{label}-logs"; log_root.mkdir(mode=0o700)
            moved = log_root / state["job_id"]
            job.rename(moved)
            state["result_path"] = str(moved / envelope.name)
            _raw, info = MODULE.read_regular(moved / envelope.name, 1024 * 1024, "fixture envelope")
            state["result_identity"] = list(MODULE._identity(info))
            _raw, sha = MODULE.write_atomic(moved, MODULE.STATE_NAME, state)
            return moved, state, sha, repo, log_root, moved / envelope.name

        def invoke(log_root: Path, job_id: str, destination: Path, output_format: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["bash", str(ROOT / "agy-worker.sh"), "verification-copy", "--job-id", job_id,
                 "--destination", str(destination), "--format", output_format],
                env={**os.environ, "AGY_WORKER_LOG_DIR": str(log_root)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )

        job, state, _sha, repo, log_root, _envelope = fixture("wrapper-copy-success")
        parent = root / "wrapper-copy-success-parent"; parent.mkdir(mode=0o700); parent = parent.resolve()
        copied = invoke(log_root, state["job_id"], parent / "json-copy", "json")
        assert copied.returncode == 0 and not copied.stderr, copied.stderr.decode("utf-8", "replace")
        assert json.loads(copied.stdout) == {
            "candidate_sha256": state["result_sha256"], "verification_copy": "created",
        }
        text = invoke(log_root, state["job_id"], parent / "text-copy", "text")
        assert text.returncode == 0 and not text.stderr
        assert text.stdout == b"Driver verification copy created; no candidate acceptance was recorded.\n"
        for output in (copied.stdout, copied.stderr, text.stdout, text.stderr):
            assert str(repo).encode("utf-8") not in output
            assert str(job).encode("utf-8") not in output
            assert str(log_root).encode("utf-8") not in output
        malformed = subprocess.run(
            ["bash", str(ROOT / "agy-worker.sh"), "verification-copy", "--job-id", state["job_id"]],
            env={**os.environ, "AGY_WORKER_LOG_DIR": str(log_root)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert malformed.returncode == 64 and not malformed.stdout
        assert str(repo).encode("utf-8") not in malformed.stderr

        invalid_job, invalid_state, _sha, invalid_repo, invalid_log, _envelope = fixture("wrapper-copy-invalid")
        invalid = invoke(invalid_log, invalid_state["job_id"], invalid_repo / "inside-candidate", "json")
        assert invalid.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not invalid.stdout
        assert not (invalid_repo / "inside-candidate").exists()
        assert str(invalid_repo).encode("utf-8") not in invalid.stderr

        unavailable_job, unavailable_state, unavailable_sha, unavailable_repo, unavailable_log, _envelope = fixture("wrapper-copy-unavailable")
        verification = {
            "schema_version": 2, "summary": "wrapper finalization", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": unavailable_state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        finalized = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(unavailable_job.resolve()),
             "--approve-state-sha", unavailable_sha, "--assurance", "partially_verified"],
            input=json.dumps(verification).encode("utf-8"), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
        assert finalized.returncode == 0 and not finalized.stderr, finalized.stderr.decode("utf-8", "replace")
        unavailable_parent = root / "wrapper-copy-unavailable-parent"; unavailable_parent.mkdir(mode=0o700)
        unavailable = invoke(
            unavailable_log, unavailable_state["job_id"],
            unavailable_parent.resolve() / "candidate", "json",
        )
        assert unavailable.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not unavailable.stdout
        assert not (unavailable_parent / "candidate").exists()
        assert str(unavailable_repo).encode("utf-8") not in unavailable.stderr

    check("public verification-copy wrapper preserves output privacy and maps runtime failures", public_verification_copy_wrapper_has_exact_success_privacy_and_runtime_exits)

    def v9_git_boundary_identity_is_stable_for_provider_content_and_rejects_replacement() -> None:
        """V9 separates stable Git authority from mutable candidate content."""
        def git_toplevel_alias_is_narrowly_bound_to_the_held_directory() -> None:
            """Only macOS's documented spelling alias survives a full binding."""
            fixture = root / "git-toplevel-alias"; fixture.mkdir()
            other = root / "git-toplevel-alias-other"; other.mkdir()
            arbitrary_link = root / "git-toplevel-arbitrary-link"
            arbitrary_link.symlink_to(fixture, target_is_directory=True)
            binding = MODULE._full_stat_binding(os.lstat(fixture))

            # Model a macOS Git response without depending on whether this host
            # exposes /var through /private/var.  The only mocked filesystem
            # lookups map that public spelling to this owned fixture directory.
            git_path = "/var/agy-worker-synthetic-root"
            canonical_root = "/private/var/agy-worker-synthetic-root"
            original_canonical = MODULE.MODEL_SELECTION._canonical_executable_path
            original_lstat = MODULE.os.lstat
            original_open = MODULE.os.open

            def mapped_path(value: str | bytes | os.PathLike[str] | os.PathLike[bytes]):
                try:
                    text = os.fsdecode(value)
                except (TypeError, UnicodeError):
                    return value
                return str(fixture) if text == git_path else value

            def canonical(value: str) -> str:
                if value in {git_path, canonical_root}:
                    return canonical_root
                return original_canonical(value)

            def lstat(value, *arguments, **keywords):
                return original_lstat(mapped_path(value), *arguments, **keywords)

            def open_path(value, *arguments, **keywords):
                return original_open(mapped_path(value), *arguments, **keywords)

            MODULE.MODEL_SELECTION._canonical_executable_path = canonical
            MODULE.os.lstat = lstat
            MODULE.os.open = open_path
            try:
                assert MODULE._bound_git_worktree_root(
                    git_path.encode("utf-8") + b"\n", canonical_root, binding,
                )
            finally:
                MODULE.MODEL_SELECTION._canonical_executable_path = original_canonical
                MODULE.os.lstat = original_lstat
                MODULE.os.open = original_open

            assert not MODULE._bound_git_worktree_root(
                os.fsencode(str(arbitrary_link)) + b"\n", str(fixture), binding,
            )
            assert not MODULE._bound_git_worktree_root(
                os.fsencode(str(other)) + b"\n", str(fixture), binding,
            )
            for malformed in (b"relative\n", b"/tmp/has\0nul\n", b"\xff\n", b"/tmp/one\nsecond\n"):
                assert not MODULE._bound_git_worktree_root(malformed, str(fixture), binding)

            helper_source = WORKTREE_SOURCE.read_text(encoding="utf-8")
            boundary = helper_source[
                helper_source.index("def _git_boundary_identity"):
                helper_source.index("\ndef _worktree_snapshot")
            ]
            snapshot = helper_source[
                helper_source.index("def _worktree_snapshot"):helper_source.index("\n\n_IMPLEMENTATION_FUNCTIONS")
            ]
            assert "_bound_git_worktree_root(top_level, root, root_binding)" in boundary
            assert "_bound_git_worktree_root(top_level[1], root, root_binding)" in snapshot

        git_toplevel_alias_is_narrowly_bound_to_the_held_directory()

        def verification(state: dict, label: str) -> dict:
            return {
                "schema_version": 2, "summary": f"{label} driver review", "passed_checks": [],
                "failed_checks": [label], "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": state["result_sha256"], "coverage": "partial",
                "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
            }

        def provider_changed_candidate(label: str, *, linked: bool = False) -> tuple[Path, dict, str, Path, Path]:
            job, state, _sha, envelope = current_candidate_fixture(label, linked=linked)
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            repo = Path(command["workdir"])
            tracked = repo / "provider-tracked.txt"
            tracked.write_text("provider content\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "provider-tracked.txt"], check=True)
            tracked.write_text("provider revised content\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "provider-tracked.txt"], check=True)
            (repo / "provider-untracked.txt").write_text("provider untracked\n", encoding="utf-8")
            snapshot = MODULE._worktree_snapshot(str(repo)); assert snapshot is not None
            state.update({
                "candidate_worktree_sha256": snapshot["sha256"],
                "candidate_worktree_entries": snapshot["entries"],
                "continue_available": True,
            })
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            assert MODULE._dispatch_root_identity(str(repo)) == state["worktree_root_identity"]
            public = MODULE.public_status(state, sha, job=job)
            assert {"result", "continue", "finalize"} <= {
                item["action"] for item in public["available_actions"]
            }
            return job, state, sha, envelope, repo

        # Ordinary provider-tracked/untracked changes remain candidate content;
        # result, continue, and finalize each retain their normal authority.
        result_job, result_state, _result_sha, _envelope, _repo = provider_changed_candidate("v9-standard-result")
        result = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(result_job)],
            check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(result.stdout)["summary"] == "candidate-v9-standard-result"

        continue_job, continue_state, continue_sha, _envelope, _repo = provider_changed_candidate("v9-standard-continue")
        queued, _queued_sha = MODULE.create_state(
            continue_job, "conversation-continue", resume=True,
            approve_sha=continue_sha, verification=verification(continue_state, "v9-standard-continue"),
        )
        assert queued["attempt_origin"] == "conversation-continue"

        finalize_job, finalize_state, finalize_sha, _envelope, linked_repo = provider_changed_candidate(
            "v9-linked-finalize", linked=True,
        )
        marker = finalize_state["worktree_root_identity"]["git_marker"]
        assert marker["kind"] == "file" and marker["content_sha256"] is not None
        finalized = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(finalize_job),
             "--approve-state-sha", finalize_sha, "--assurance", "partially_verified"],
            input=json.dumps(verification(finalize_state, "v9-linked-finalize")).encode("utf-8"),
            check=True, stdout=subprocess.PIPE,
        )
        assert json.loads(finalized.stdout)["driver_disposition"] == "partially_verified"

        # HEAD/ref/object and index-cache activity is mutable repository state,
        # not a V9 root boundary replacement, in both normal and linked roots.
        for label, repo in (("standard", _repo), ("linked", linked_repo)):
            before = MODULE._dispatch_root_identity(str(repo)); assert before is not None
            subprocess.run(["git", "-C", str(repo), "update-index", "--refresh"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "-c", "user.email=fixture@example.invalid",
                "-c", "user.name=Fixture", "commit", "-qm", f"{label} provider commit",
            ], check=True)
            subprocess.run(["git", "-C", str(repo), "update-ref", f"refs/heads/{label}-provider", "HEAD"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                input=b"unreferenced provider object\n", check=True, stdout=subprocess.PIPE,
            )
            assert MODULE._dispatch_root_identity(str(repo)) == before

        def reject_without_write(job: Path, state: dict, sha: str, label: str) -> None:
            before = (job / MODULE.STATE_NAME).read_bytes()
            no_provider = job.parent / f"{label.replace(' ', '-')}-provider-called"
            bin_dir = job.parent / f"{label.replace(' ', '-')}-bin"; bin_dir.mkdir()
            fake = bin_dir / "agy"
            fake.write_text(
                "#!/bin/sh\ntouch " + shlex.quote(str(no_provider)) + "\nexit 99\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            actions = {
                item["action"] for item in MODULE.public_status(state, sha, job=job)["available_actions"]
            }
            assert not ({"result", "continue", "finalize"} & actions), (label, actions)
            result = subprocess.run(
                [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert result.returncode != 0 and result.stdout == b"", label
            previous_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
            try:
                try:
                    MODULE.create_state(
                        job, "conversation-continue", resume=True, approve_sha=sha,
                        verification=verification(state, label),
                    )
                except MODULE.DispatchError as exc:
                    assert str(exc) == "dispatch worktree root binding changed", (label, exc)
                else:
                    raise AssertionError(f"{label} authorized a provider continuation")
                final = subprocess.run(
                    [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                     "--approve-state-sha", sha, "--assurance", "partially_verified"],
                    input=json.dumps(verification(state, label)).encode("utf-8"),
                    check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
            finally:
                os.environ["PATH"] = previous_path
            assert final.returncode == 64 and final.stdout == b"", label
            assert not no_provider.exists(), label
            assert (job / MODULE.STATE_NAME).read_bytes() == before, label

        # Same-path roots, direct .git directories, linked marker contents and
        # the Git-dir/common-dir/synthetic-worktree boundaries all fail closed
        # without a provider launch or controller-state rewrite.
        job, state, sha, _envelope, repo = provider_changed_candidate("v9-root-replacement")
        displaced = root / "v9-root-replacement-displaced"; repo.rename(displaced)
        repo.mkdir(); subprocess.run(["git", "init", "-q", str(repo)], check=True)
        reject_without_write(job, state, sha, "root replacement")

        job, state, sha, _envelope, repo = provider_changed_candidate("v9-git-dir-replacement")
        (repo / ".git").rename(repo / ".git-displaced")
        (repo / ".git").mkdir()
        reject_without_write(job, state, sha, "git dir replacement")

        job, state, sha, _envelope, repo = provider_changed_candidate("v9-linked-marker-replacement", linked=True)
        marker = repo / ".git"; original = marker.read_bytes(); replacement = repo / ".git.replacement"
        replacement.write_bytes(original); replacement.chmod(marker.stat().st_mode & 0o777)
        os.replace(replacement, marker)
        reject_without_write(job, state, sha, "linked marker replacement")

        job, state, sha, _envelope, repo = provider_changed_candidate("v9-linked-marker-retarget", linked=True)
        (repo / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
        reject_without_write(job, state, sha, "linked marker retarget")

        job, state, sha, _envelope, repo = provider_changed_candidate("v9-git-dir-symlink", linked=True)
        git_dir = Path(state["worktree_root_identity"]["git_dir"]["realpath"])
        displaced = git_dir.with_name(git_dir.name + "-displaced")
        git_dir.rename(displaced); git_dir.symlink_to(displaced, target_is_directory=True)
        reject_without_write(job, state, sha, "git dir symlink")

        job, state, sha, _envelope, repo = provider_changed_candidate("v9-common-dir-symlink", linked=True)
        common_dir = Path(state["worktree_root_identity"]["common_dir"]["realpath"])
        displaced = common_dir.with_name(common_dir.name + "-displaced")
        common_dir.rename(displaced); common_dir.symlink_to(displaced, target_is_directory=True)
        reject_without_write(job, state, sha, "common dir outward symlink")

        job, state, sha, _envelope, repo = provider_changed_candidate("v9-top-level-drift")
        outside = root / "v9-top-level-outside"; outside.mkdir()
        subprocess.run(["git", "-C", str(repo), "config", "core.worktree", str(outside)], check=True)
        reject_without_write(job, state, sha, "show-toplevel drift")

    check("v9 stable Git boundary permits provider content while rejecting root and Git authority replacement", v9_git_boundary_identity_is_stable_for_provider_content_and_rejects_replacement)

    def candidate_snapshot_actions_reject(
        job: Path, state: dict, label: str, *,
        continuation_error: str = "candidate worktree reconciliation is unavailable",
    ) -> None:
        """Public and mutating paths must share an unavailable snapshot result."""
        state = dict(state)
        state["continue_available"] = True
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        before = (job / MODULE.STATE_NAME).read_bytes()
        actions = {
            item["action"]
            for item in MODULE.public_status(state, sha, job=job)["available_actions"]
        }
        assert not ({"result", "continue", "finalize"} & actions), actions
        verification = {
            "schema_version": 2, "summary": f"{label} driver rejection", "passed_checks": [],
            "failed_checks": [label], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        result = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert result.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not result.stdout
        try:
            MODULE.create_state(
                job, "conversation-continue", resume=True,
                approve_sha=sha, verification=verification,
            )
        except MODULE.DispatchError as exc:
            assert str(exc) == continuation_error
        else:
            raise AssertionError("unavailable snapshot authorized a continuation")
        finalized = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "partially_verified"],
            input=json.dumps(verification).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert finalized.returncode == 64 and not finalized.stdout
        assert (job / MODULE.STATE_NAME).read_bytes() == before

    def redirected_core_worktree_cannot_hide_bound_root_content_or_authorize_actions() -> None:
        """Git plumbing must reject local core.worktree redirection before listings."""
        for variant in ("absolute", "relative", "symlink"):
            snapshot_repo = root / f"redirected-snapshot-{variant}-repo"; snapshot_repo.mkdir()
            subprocess.run(["git", "init", "-q", str(snapshot_repo)], check=True)
            bound = snapshot_repo / "bound-only.txt"; bound.write_text("one", encoding="utf-8")
            outside = root / f"redirected-snapshot-{variant}-outside"; outside.mkdir()
            configured = str(outside)
            if variant == "relative":
                configured = os.path.relpath(outside, snapshot_repo / ".git")
            elif variant == "symlink":
                outward_link = root / "redirected-snapshot-outside-link"
                outward_link.symlink_to(outside, target_is_directory=True)
                configured = str(outward_link)
            subprocess.run(["git", "-C", str(snapshot_repo), "config", "core.worktree", configured], check=True)
            redirected = MODULE._worktree_snapshot(str(snapshot_repo))
            assert redirected is None, f"{variant} core.worktree redirected bound-root Git enumeration"
            bound.write_text("two", encoding="utf-8")
            assert MODULE._worktree_snapshot(str(snapshot_repo)) is None

        job, state, _sha, _envelope = current_candidate_fixture("redirected-core-worktree")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        candidate_repo = Path(command["workdir"])
        candidate_outside = root / "redirected-candidate-outside"; candidate_outside.mkdir()
        subprocess.run(["git", "-C", str(candidate_repo), "config", "core.worktree", str(candidate_outside)], check=True)
        candidate_snapshot_actions_reject(
            job, state, "core-worktree",
            continuation_error="dispatch worktree root binding changed",
        )

    check("core.worktree redirection fails closed for snapshots and candidate action parity", redirected_core_worktree_cannot_hide_bound_root_content_or_authorize_actions)

    def bounded_git_reader_rejects_unbound_global_option_overrides() -> None:
        """A caller cannot repoint a bounded read to a different repository."""
        repo_a = root / "bounded-git-root-a"; repo_a.mkdir()
        repo_b = root / "bounded-git-root-b"; repo_b.mkdir()
        for repo, filename in ((repo_a, "a.txt"), (repo_b, "b.txt")):
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / filename).write_text(filename, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
        safe_git = MODULE._safe_git_executable(); assert safe_git is not None
        executable, executable_authority = safe_git
        git_dir_a = os.fspath(repo_a / ".git")
        git_dir_b = os.fspath(repo_b / ".git")
        attempts = {
            "different repository": [
                f"--git-dir={git_dir_b}", f"--work-tree={repo_b}",
                "ls-files", "--stage", "-z",
            ],
            "malformed override": [
                "--git-dir=", f"--work-tree={repo_a}", "ls-files", "--stage", "-z",
            ],
            "reordered override": [
                f"--work-tree={repo_a}", f"--git-dir={git_dir_a}",
                "ls-files", "--stage", "-z",
            ],
            "duplicate override": [
                f"--git-dir={git_dir_a}", f"--work-tree={repo_a}",
                f"--git-dir={git_dir_b}", f"--work-tree={repo_b}",
                "ls-files", "--stage", "-z",
            ],
        }
        for label, arguments in attempts.items():
            result = MODULE._bounded_git_read(
                executable, executable_authority, str(repo_a), arguments,
                deadline=time.monotonic() + 2.0,
            )
            assert result is None, (label, result)

    check("bounded Git reads reject caller-supplied context overrides", bounded_git_reader_rejects_unbound_global_option_overrides)

    def snapshot_git_context_is_pinned_and_avoids_redundant_root_probes() -> None:
        """One bound Git context replaces per-enumeration root subprocesses."""
        repo = root / "root-order-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        real_git = shutil.which("git"); assert real_git is not None
        bin_dir = root / "root-order-bin"; bin_dir.mkdir()
        log = root / "root-order.log"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> " + shlex.quote(str(log)) + "\n"
            "exec " + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
        original_reader = MODULE._bounded_git_read
        calls: list[tuple[tuple[str, ...], tuple[str, tuple[int, ...]] | None]] = []

        def count_reader(*args, **kwargs):
            arguments = args[3]
            calls.append((tuple(arguments), getattr(arguments, "git_directory", None)))
            return original_reader(*args, **kwargs)

        MODULE._bounded_git_read = count_reader
        try:
            snapshot = MODULE._worktree_snapshot(str(repo))
            assert snapshot is not None and snapshot["entries"] == 0
        finally:
            os.environ["PATH"] = previous_path
            MODULE._bounded_git_read = original_reader
        commands = log.read_text(encoding="utf-8").splitlines()
        # A committed empty repository used to make 83 bounded subprocesses:
        # 25 enumerations each repeated two root facts.  The pinned context
        # must remain well below that deterministic baseline without caching a
        # snapshot across lifecycle phases.
        assert len(calls) <= 40, len(calls)
        expected_root = os.path.realpath(repo)
        expected_git_dir = os.path.join(expected_root, ".git")
        enumerations = [
            call for call in calls
            if call[0][0] in {"config", "ls-files", "ls-tree", "cat-file"}
        ]
        assert enumerations, calls
        for arguments, git_directory in enumerations:
            assert git_directory is not None, arguments
            assert git_directory[0] == expected_git_dir, arguments

        def bare_root_fact(arguments: tuple[str, ...]) -> bool:
            return arguments in {
                ("rev-parse", "--is-inside-work-tree"),
                ("rev-parse", "--show-toplevel"),
            }

        bare_root_calls = [arguments for arguments, git_directory in calls if git_directory is None and bare_root_fact(arguments)]
        assert len(bare_root_calls) == 4, bare_root_calls
        assert sum(arguments == ("rev-parse", "--is-inside-work-tree") for arguments in bare_root_calls) == 2
        assert sum(arguments == ("rev-parse", "--show-toplevel") for arguments in bare_root_calls) == 2

        for command in commands:
            parts = shlex.split(command)
            if not ({"config", "ls-files", "ls-tree", "cat-file"} & set(parts)):
                continue
            assert f"--git-dir={expected_git_dir}" in parts, command
            assert f"--work-tree={expected_root}" in parts, command

    check("snapshot pins Git context while retaining initial/final root checks", snapshot_git_context_is_pinned_and_avoids_redundant_root_probes)

    recovery_path = ROOT / "tests/agy_worker_remediation_recovery_cases.py"
    recovery_spec = importlib.util.spec_from_file_location(
        "agy_worker_remediation_recovery_cases", recovery_path,
    )
    assert recovery_spec is not None and recovery_spec.loader is not None
    recovery_cases = importlib.util.module_from_spec(recovery_spec)
    recovery_spec.loader.exec_module(recovery_cases)
    recovery_cases.run(globals())

expected_checks = 1 if FOCUSED_CHECK is not None else EXPECTED_CHECKS
if CHECKS_RUN != expected_checks:
    raise AssertionError(
        f"remediation controller inventory drifted: expected {expected_checks}, ran {CHECKS_RUN}"
    )
print(f"PASS: remediation controller focused checks ({CHECKS_RUN} cases)")
