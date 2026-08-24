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
import time


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills/agy-worker/runtime/scripts/agy_dispatch.py"
SCHEMA = ROOT / "skills/agy-worker/runtime/schemas/worker-result.schema.json"
PROVIDER_SCHEMA = ROOT / "skills/agy-worker/runtime/schemas/worker-result.provider.schema.json"
spec = importlib.util.spec_from_file_location("agy_dispatch_remediation", SOURCE)
MODULE = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(MODULE)

EXPECTED_CHECKS = 87
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
        for key in {*MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS}:
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
        for key in {"provider_retry_after_seconds", "provider_retry_observed_epoch", *MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS}:
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
            for key in {*MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS}:
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
        source = SOURCE.read_bytes()
        start = source.index(b"def _worktree_snapshot")
        end = source.index(b"\ndef _reconcile_worktree", start)
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
                deadline=time.monotonic() + 0.5, stdout_limit=64,
            )
            elapsed = time.monotonic() - started
            assert result is None and elapsed < 2.0, elapsed
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

        source = SOURCE.read_bytes()
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
                deadline=time.monotonic() + 0.3, stdout_limit=64,
            )
            elapsed = time.monotonic() - started
            assert result is None and elapsed < 2.0, elapsed
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

    check("controller maps ERROR plus valid report to failed unreviewed exit 25", controller_preserves_outer_error_candidate)

    def invalid_error_and_cancelled_candidate_are_separate() -> None:
        cases = [
            ("error-missing", "ERROR", None, 4, "failed", "invalid_envelope", "none", "missing_structured_output"),
            ("lowercase-success", "success", report(), 4, "failed", "invalid_envelope", "none", "outer_status"),
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
        for key in {*MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS}:
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
            "matrix_agy_version": "1.1.16",
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
                    "while not os.path.exists(child_record) and time.monotonic() < deadline:\n"
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

    def direct_selection_reprobes_every_controller_attempt() -> None:
        """The controller, not PATH, owns the exact direct executable launch."""
        help_text = """Usage of agy:
  --add-dir  Add a directory
  --conversation  Resume a conversation
  --disable-slash-commands  Disable slash commands
  --json-schema  Schema path
  --mode  Execution mode (accept-edits, plan)
  --model  Select a model
  --output-format  Format (text, json, stream-json)
  --print  Run a prompt
  --print-timeout  Print timeout
  --sandbox  Sandboxed
"""

        def fixture(
            label: str, workflow: str = "task", *, idle_seconds: float = 2,
            hard_seconds: float = 3, max_seconds: float = 20,
            max_cycles: int = 2,
        ) -> tuple[Path, Path, Path, Path, dict]:
            repo = (root / f"direct-reprobe-repo-{label}").resolve()
            if workflow == "project":
                source_repo = (root / f"direct-reprobe-source-{label}").resolve()
                subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
                subprocess.run([
                    "git", "-C", str(source_repo), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit",
                    "--allow-empty", "-qm", "fixture",
                ], check=True)
                subprocess.run([
                    "git", "-C", str(source_repo), "worktree", "add", "-q",
                    "--detach", str(repo), "HEAD",
                ], check=True)
            else:
                repo.mkdir()
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
            job = root / f"direct-reprobe-job-{label}"; job.mkdir(mode=0o700); job = job.resolve()
            bin_dir = root / f"direct-reprobe-bin-{label}"; bin_dir.mkdir()
            calls = root / f"direct-reprobe-calls-{label}"
            args = root / f"direct-reprobe-args-{label}"
            cwd = root / f"direct-reprobe-cwd-{label}"
            schema = root / f"direct-reprobe-provider-{label}.json"; provider_schema(schema)
            events = [
                {"event": "init", "init": {}, "conversation_id": "conversation-1"},
                {"event": "step_update", "step_update": {}},
                {"event": "result", "result": {"conversation_id": "conversation-1", "status": "SUCCESS", "structured_output": report()}},
            ]
            fake = bin_dir / "agy"
            fake.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = --version ] && [ \"$#\" = 1 ]; then printf 'version\\n' >> " + shlex.quote(str(calls)) + "; printf '1.1.16\\n'; exit 0; fi\n"
                "if [ \"${1:-}\" = --help ] && [ \"$#\" = 1 ]; then printf 'help\\n' >> " + shlex.quote(str(calls)) + "; sleep \"${FAKE_DIRECT_HELP_DELAY:-0}\"; cat >&2 <<'HELP'\n" + help_text + "HELP\nexit 0\nfi\n"
                "printf 'provider\\n' >> " + shlex.quote(str(calls)) + "\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(args)) + "\npwd > " + shlex.quote(str(cwd)) + "\n"
                + "\n".join("printf '%s\\n' " + shlex.quote(json.dumps(item)) for item in events) + "\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            previous_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
            try:
                selection = MODULE.MODEL_SELECTION.resolve_selection(
                    "gemini-3.6-flash", "high", "cli", "cli", probe_version=True,
                )
            finally:
                os.environ["PATH"] = previous_path
            assert selection["schema_version"] == 2
            assert selection["version_relation"] == "match"
            assert selection["compatibility_status"] == "reviewed-version-match"
            assert not ({"compatibility_disposition", "approved_help_sha256", "compatibility_decision_sha256"} & set(selection))
            selection_path = job / "selection.json"
            MODULE.MODEL_SELECTION.publish_record(selection_path, selection)
            raw, info = MODULE.read_regular(selection_path, MODULE.MAX_COMMAND_BYTES, "fixture selection")
            command = {
                "schema_version": 4, "kind": "agy-worker-dispatch-command", "job_id": f"direct-{label}",
                "workdir": str(repo), "argv": ["agy", "--sandbox", "--mode", "accept-edits", "--add-dir", str(repo), "--json-schema", str(schema), "--model", "gemini-3.6-flash-high", "--print", "task"],
                "agy_version": "1.1.16", "agy_version_observed": True,
                "selection_path": str(selection_path), "selection_sha256": MODULE.digest(raw), "selection_identity": list(MODULE._identity(info)),
                "idle_seconds": idle_seconds, "hard_seconds": hard_seconds, "max_seconds": max_seconds, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": workflow,
                "max_cycles": max_cycles, "resume_prompt": "resume", "continue_prompt": "continue",
            }
            MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
            MODULE.create_state(job, "initial", resume=False)
            calls.unlink(missing_ok=True)
            return job, bin_dir, calls, fake, command

        def assert_attempt(job: Path, bin_dir: Path, calls: Path, command: dict, origin: str) -> None:
            observed_provider_executables: list[str | None] = []
            real_popen = MODULE.subprocess.Popen

            def inspect_popen(arguments, *popen_args, **popen_kwargs):
                if isinstance(arguments, list) and arguments and arguments[0] == "agy":
                    observed_provider_executables.append(popen_kwargs.get("executable"))
                return real_popen(arguments, *popen_args, **popen_kwargs)

            MODULE.subprocess.Popen = inspect_popen
            try:
                returncode = run_controller(job, bin_dir)
            finally:
                MODULE.subprocess.Popen = real_popen
            if returncode != 0:
                failed, _raw, _sha = MODULE.load_state(job)
                raise AssertionError((returncode, failed.get("reason"), calls.read_text(encoding="utf-8").splitlines()))
            assert observed_provider_executables == [str((bin_dir / "agy").resolve())]
            assert calls.read_text(encoding="utf-8").splitlines() == ["version", "help", "provider"]
            state, _raw, _sha = MODULE.load_state(job)
            assert state["status"] == "succeeded"
            assert state["selection_sha256"] == command["selection_sha256"]
            assert state["selection_identity"] == command["selection_identity"]
            assert state["attempt_origin"] == origin

        # Initial, resume, and explicit restart each enter the same controller
        # path and must re-probe directly before their provider Popen.
        initial_job, initial_bin, initial_calls, _initial_fake, initial_command = fixture("initial")
        assert_attempt(initial_job, initial_bin, initial_calls, initial_command, "initial")

        for origin in ("conversation-resume", "fresh-restart"):
            job, bin_dir, calls, _fake, command = fixture(origin)
            state, _raw, sha = MODULE.load_state(job)
            state.update({
                "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
                "finished_epoch": 1.0, "conversation_id": "conversation-1",
                "resume_available": True, "phase": "attempt-failed", "assurance": "pending",
                "next_action": "resume", "driver_disposition": "not_applicable",
            })
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            queued, _next_sha = MODULE.create_state(job, origin, resume=True, approve_sha=sha)
            assert queued["status"] == "queued"
            calls.unlink(missing_ok=True)
            assert_attempt(job, bin_dir, calls, command, origin)

        # A continuation preserves one accepted conversation and candidate, but
        # still gets a fresh executable/version/help proof before launch.
        job, bin_dir, calls, _fake, command = fixture("continue")
        state, _raw, sha = MODULE.load_state(job)
        envelope = job / "envelope.json"; payload = json.dumps(report()).encode() + b"\n"
        envelope.write_bytes(payload); envelope.chmod(0o600)
        _bound, info = MODULE.read_regular(envelope, 1024 * 1024, "fixture envelope")
        snapshot = MODULE._worktree_snapshot(command["workdir"])
        assert snapshot is not None
        state.update({
            "status": "succeeded", "exit_code": 0, "finished_epoch": 1.0,
            "conversation_id": "conversation-1", "result_path": str(envelope),
            "result_sha256": MODULE.digest(payload), "result_identity": list(MODULE._identity(info)),
            "candidate_recognized": True, "candidate_source": "provider_success", "result_available": True,
            "driver_disposition": "unreviewed", "phase": "awaiting-verification", "assurance": "pending",
            "continue_available": True, "next_action": "driver_review",
            "candidate_worktree_sha256": snapshot["sha256"], "candidate_worktree_entries": snapshot["entries"],
        })
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        verification = {
            "schema_version": 2, "summary": "driver found a repair", "passed_checks": [], "failed_checks": ["fixture"],
            "advisory_checks": 0, "missing_checks": 0, "candidate_sha256": state["result_sha256"],
            "coverage": "partial", "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        queued, _next_sha = MODULE.create_state(job, "conversation-continue", resume=True, approve_sha=sha, verification=verification)
        assert queued["conversation_id"] == "conversation-1"
        calls.unlink(missing_ok=True)
        assert_attempt(job, bin_dir, calls, command, "conversation-continue")
        repaired, _raw, repaired_sha = MODULE.load_state(job)
        # This repair input is bound audit evidence for the prior candidate;
        # it is not evidence about the replacement candidate which arrived
        # from the provider on this new attempt.
        assert repaired["verification_path"] is not None
        assert json.loads(Path(repaired["verification_path"]).read_text(encoding="utf-8")) == verification
        assert repaired["check_summary"] is None
        assert repaired["check_counts"] == {"passed": 0, "failed": 0, "advisory": 0, "missing": 0}
        repaired_public = MODULE.public_status(repaired, repaired_sha, job=job)
        assert repaired_public["driver_disposition"] == "unreviewed"
        assert repaired_public["check_summary"] is None
        assert repaired_public["check_counts"] == {"passed": 0, "failed": 0, "advisory": 0, "missing": 0}

        def queue_bound_origin(job: Path, command: dict, origin: str) -> None:
            state, _raw, sha = MODULE.load_state(job)
            if origin in {"conversation-resume", "fresh-restart"}:
                state.update({
                    "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
                    "finished_epoch": 1.0, "conversation_id": "conversation-1",
                    "resume_available": True, "phase": "attempt-failed", "assurance": "pending",
                    "next_action": "resume", "driver_disposition": "not_applicable",
                })
                _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
                MODULE.create_state(job, origin, resume=True, approve_sha=sha)
                return
            envelope = job / "matrix-mutation-envelope.json"
            payload = json.dumps(report()).encode() + b"\n"
            envelope.write_bytes(payload); envelope.chmod(0o600)
            _bound, info = MODULE.read_regular(envelope, 1024 * 1024, "fixture envelope")
            snapshot = MODULE._worktree_snapshot(command["workdir"])
            assert snapshot is not None
            state.update({
                "status": "succeeded", "exit_code": 0, "finished_epoch": 1.0,
                "conversation_id": "conversation-1", "result_path": str(envelope),
                "result_sha256": MODULE.digest(payload), "result_identity": list(MODULE._identity(info)),
                "candidate_recognized": True, "candidate_source": "provider_success", "result_available": True,
                "driver_disposition": "unreviewed", "phase": "awaiting-verification", "assurance": "pending",
                "continue_available": True, "next_action": "driver_review",
                "candidate_worktree_sha256": snapshot["sha256"], "candidate_worktree_entries": snapshot["entries"],
            })
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            continuation_feedback = {
                "schema_version": 2, "summary": "driver found a repair", "passed_checks": [], "failed_checks": ["fixture"],
                "advisory_checks": 0, "missing_checks": 0, "candidate_sha256": state["result_sha256"],
                "coverage": "partial", "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
            }
            MODULE.create_state(
                job, "conversation-continue", resume=True, approve_sha=sha,
                verification=continuation_feedback,
            )

        # The bound record is caller input, not a policy lookup on every later
        # attempt.  Mutating the live matrix location after job creation must
        # not alter resume/restart/continue, while malformed frozen evidence
        # remains rejected before any executable or provider action.
        bad_frozen = root / "bad-frozen-selection.json"
        malformed_frozen = json.loads(Path(initial_command["selection_path"]).read_text(encoding="utf-8"))
        malformed_frozen["matrix_sha256"] = "not-a-digest"
        bad_frozen.write_text(json.dumps(malformed_frozen), encoding="utf-8")
        try:
            MODULE.MODEL_SELECTION.read_selection_record(bad_frozen, frozen=True)
        except MODULE.MODEL_SELECTION.CallerError:
            pass
        else:
            raise AssertionError("malformed frozen selection evidence was accepted")

        # The bound read, not a later pathname reopen, owns frozen selection
        # semantics.  Swap a valid B record immediately after read_regular has
        # returned A: this call must still decode A, and the next controller
        # binding check must reject the replacement before any local probe or
        # provider launch.
        swap_job, swap_bin, swap_calls, _swap_fake, swap_command = fixture("record-toctou")
        swap_state, _raw, _sha = MODULE.load_state(swap_job)
        selection_path = Path(swap_command["selection_path"])
        record_a = json.loads(selection_path.read_text(encoding="utf-8"))
        record_b = copy.deepcopy(record_a)
        record_b["resolved_agy_model"] = "gemini-3.1-pro-high"
        replacement = selection_path.with_name("selection-b.json")
        replacement.write_text(json.dumps(record_b) + "\n", encoding="utf-8")
        replacement.chmod(0o600)
        original_read_regular = MODULE.read_regular
        swapped = False

        def swap_after_bound_read(path: Path, limit: int, label: str):
            nonlocal swapped
            payload, info = original_read_regular(path, limit, label)
            if label == "dispatch selection" and Path(path) == selection_path and not swapped:
                os.replace(replacement, selection_path)
                swapped = True
            return payload, info

        MODULE.read_regular = swap_after_bound_read
        try:
            bound_record = MODULE._load_bound_selection(swap_command, swap_state)
        finally:
            MODULE.read_regular = original_read_regular
        assert swapped and bound_record is not None
        assert bound_record["resolved_agy_model"] == record_a["resolved_agy_model"]
        assert bound_record["resolved_agy_model"] != record_b["resolved_agy_model"]
        assert run_controller(swap_job, swap_bin) == MODULE.EXIT_BY_REASON["status_unavailable"]
        assert not swap_calls.exists(), "selection replacement reached a local probe or provider"
        swap_terminal, _raw, _sha = MODULE.load_state(swap_job)
        assert (swap_terminal["reason"], swap_terminal["failure_stage"]) == (
            "status_unavailable", "binding_failure",
        )

        original_matrix_path = MODULE.MODEL_SELECTION.MATRIX_PATH
        mutated_matrix = root / "mutated-policy-matrix.json"
        mutated_matrix.write_bytes(original_matrix_path.read_bytes() + b"\n")
        for origin in ("conversation-resume", "fresh-restart", "conversation-continue"):
            job, bin_dir, calls, _fake, command = fixture(f"matrix-mutation-{origin}")
            queue_bound_origin(job, command, origin)
            MODULE.MODEL_SELECTION.MATRIX_PATH = mutated_matrix
            try:
                calls.unlink(missing_ok=True)
                assert_attempt(job, bin_dir, calls, command, origin)
            finally:
                MODULE.MODEL_SELECTION.MATRIX_PATH = original_matrix_path

        # A slow local preflight remains charged to hard/max, not the provider
        # idle lease.  It must suppress Popen before an independent hard
        # deadline while still allowing a generous absolute deadline.
        expired_job, expired_bin, expired_calls, _expired_fake, _expired_command = fixture(
            "deadline-expired", idle_seconds=0.01, hard_seconds=0.10, max_seconds=5,
        )
        previous_delay = os.environ.get("FAKE_DIRECT_HELP_DELAY")
        os.environ["FAKE_DIRECT_HELP_DELAY"] = "0.25"
        try:
            assert run_controller(expired_job, expired_bin) == MODULE.EXIT_BY_REASON["hard_deadline_exceeded"]
        finally:
            if previous_delay is None:
                os.environ.pop("FAKE_DIRECT_HELP_DELAY", None)
            else:
                os.environ["FAKE_DIRECT_HELP_DELAY"] = previous_delay
        expired, _raw, _sha = MODULE.load_state(expired_job)
        assert (expired["reason"], expired["limit_kind"]) == ("hard_deadline_exceeded", "hard")
        assert expired["elapsed_seconds"] >= expired["hard_seconds"]
        assert expired_calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]

        within_job, within_bin, within_calls, _within_fake, within_command = fixture(
            "deadline-within", idle_seconds=0.01, hard_seconds=5, max_seconds=5,
        )
        os.environ["FAKE_DIRECT_HELP_DELAY"] = "0.05"
        try:
            within_code = run_controller(within_job, within_bin)
        finally:
            if previous_delay is None:
                os.environ.pop("FAKE_DIRECT_HELP_DELAY", None)
            else:
                os.environ["FAKE_DIRECT_HELP_DELAY"] = previous_delay
        within, _raw, _sha = MODULE.load_state(within_job)
        assert within_code == 0, (within_code, within["reason"], within["limit_kind"], within["elapsed_seconds"])
        assert within["status"] == "succeeded" and within["elapsed_seconds"] < within_command["max_seconds"]
        assert within["progress_count"] >= 3
        assert within_calls.read_text(encoding="utf-8").splitlines() == ["version", "help", "provider"]

        # Binding or executable drift replaces the queued state without calling
        # the provider.  The reprobe target is never selected through fallback.
        for kind in ("selection", "executable"):
            job, bin_dir, calls, fake, command = fixture(f"reject-{kind}")
            if kind == "selection":
                Path(command["selection_path"]).write_text("{}\n", encoding="utf-8")
                Path(command["selection_path"]).chmod(0o600)
            else:
                fake.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8"); fake.chmod(0o755)
            calls.unlink(missing_ok=True)
            expected_reason = "status_unavailable" if kind == "selection" else "selection_preflight_failed"
            assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON[expected_reason]
            assert not calls.exists() or "provider" not in calls.read_text(encoding="utf-8")
            terminal, _raw, _sha = MODULE.load_state(job)
            assert terminal["status"] == "failed" and terminal["reason"] == expected_reason
            if kind == "executable":
                assert terminal["failure_stage"] == "selection_preflight"

        # A target that rewrites itself during the final help probe must not turn
        # the already-returned pathname into authority to launch new content.
        job, bin_dir, calls, fake, _command = fixture("reject-help-rewrite")
        replacement = bin_dir / "agy.replacement"
        replacement.write_text(
            "#!/bin/sh\n"
            "printf 'provider\\n' >> " + shlex.quote(str(calls)) + "\n"
            "exit 0\n",
            encoding="utf-8",
        )
        replacement.chmod(0o755)
        fake.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ] && [ \"$#\" = 1 ]; then "
            "printf 'version\\n' >> " + shlex.quote(str(calls)) + "; printf '1.1.16\\n'; exit 0; fi\n"
            "if [ \"${1:-}\" = --help ] && [ \"$#\" = 1 ]; then "
            "printf 'help\\n' >> " + shlex.quote(str(calls)) + "; cat >&2 <<'HELP'\n"
            + help_text
            + "HELP\n"
            "mv " + shlex.quote(str(replacement)) + " \"$0\"\n"
            "exit 0\n"
            "fi\n"
            "printf 'provider\\n' >> " + shlex.quote(str(calls)) + "\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        calls.unlink(missing_ok=True)
        assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["selection_preflight_failed"]
        assert calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]
        terminal, _raw, _sha = MODULE.load_state(job)
        assert (terminal["status"], terminal["reason"], terminal["failure_stage"]) == (
            "failed", "selection_preflight_failed", "selection_preflight",
        )

        # A final direct-selection probe failure is a deterministic local
        # preflight outcome, not an unclassified status failure.  It preserves
        # the caller-selected record as forensic input, but cannot safely
        # relaunch that frozen selection in the same job.
        def selection_preflight_failure(
            label: str, *, max_cycles: int, idle_seconds: float = 2,
            hard_seconds: float = 3, max_seconds: float = 20,
        ) -> tuple[Path, Path, Path, dict]:
            job, bin_dir, calls, fake, command = fixture(
                label, idle_seconds=idle_seconds, hard_seconds=hard_seconds,
                max_seconds=max_seconds,
            )
            fake.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = --version ] && [ \"$#\" = 1 ]; then printf 'version\\n' >> " + shlex.quote(str(calls)) + "; printf '1.1.16\\n'; exit 0; fi\n"
                "if [ \"${1:-}\" = --help ] && [ \"$#\" = 1 ]; then printf 'help\\n' >> " + shlex.quote(str(calls)) + "; sleep \"${FAKE_DIRECT_HELP_DELAY:-0}\"; printf 'incompatible interface\\n' >&2; exit 0; fi\n"
                "printf 'provider\\n' >> " + shlex.quote(str(calls)) + "\nexit 99\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            if max_cycles != command["max_cycles"]:
                state, _raw, _sha = MODULE.load_state(job)
                state["max_cycles"] = max_cycles
                MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            calls.unlink(missing_ok=True)
            return job, bin_dir, calls, command

        job, bin_dir, calls, command = selection_preflight_failure("selection-preflight", max_cycles=2)
        assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["selection_preflight_failed"]
        assert calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]
        terminal, _raw, terminal_sha = MODULE.load_state(job)
        assert (terminal["reason"], terminal["failure_stage"], terminal["next_action"]) == (
            "selection_preflight_failed", "selection_preflight", "none",
        )
        assert terminal["selection_sha256"] == command["selection_sha256"]
        public = MODULE.public_status(terminal, terminal_sha, job=job)
        assert {item["action"] for item in public["available_actions"]}.isdisjoint({"resume", "restart"})
        assert public["next_action"] == "none" and public["next_action_command"] is None
        captured_status = io.BytesIO()
        original_stdout = MODULE.sys.stdout

        class _StatusStdout:
            buffer = captured_status

        MODULE.sys.stdout = _StatusStdout()
        try:
            MODULE.print_text_status(terminal, terminal_sha, job=job)
        finally:
            MODULE.sys.stdout = original_stdout
        captured_lines = captured_status.getvalue().decode("utf-8").splitlines()
        assert captured_lines == [
            "Provider attempt: failed; reason: selection_preflight_failed; failure stage: selection_preflight; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            "Next safe action: create a fresh job using the unchanged caller selection after reviewing the current sanitized agy interface evidence. No same-job action is available.",
        ]
        captured_text = "\n".join(captured_lines)
        for private in (
            str(job), str(command["selection_path"]), command["selection_sha256"],
            "gemini-3.6-flash", "--job-id", "--approve-state-sha",
        ):
            assert private not in captured_text
        state_before_rejected_restart = (job / MODULE.STATE_NAME).read_bytes()
        rejected_restart = subprocess.run(
            [sys.executable, str(SOURCE), "restart", "--job-dir", str(job),
             "--approve-state-sha", terminal_sha],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert rejected_restart.returncode == 64 and not rejected_restart.stdout
        assert (job / MODULE.STATE_NAME).read_bytes() == state_before_rejected_restart
        assert calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]

        # A failed earlier attempt can already have a conversation before its
        # resumed direct selection later fails final preflight.  This is a real
        # resume route, not merely an in-memory status projection: both public
        # advice and the command guard must reject it without staging a new
        # attempt, modifying state, or reaching the provider.
        resume_job, resume_bin, resume_calls, resume_fake, _resume_command = fixture(
            "selection-preflight-resume", max_seconds=20,
        )
        resumable, _raw, resumable_sha = MODULE.load_state(resume_job)
        resumable.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "finished_epoch": 1.0, "conversation_id": "conversation-1",
            "resume_available": True, "phase": "attempt-failed", "assurance": "pending",
            "next_action": "resume", "driver_disposition": "not_applicable",
        })
        _raw, resumable_sha = MODULE.write_atomic(resume_job, MODULE.STATE_NAME, resumable)
        MODULE.create_state(
            resume_job, "conversation-resume", resume=True, approve_sha=resumable_sha,
        )
        resume_fake.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ] && [ \"$#\" = 1 ]; then printf 'version\\n' >> " + shlex.quote(str(resume_calls)) + "; printf '1.1.16\\n'; exit 0; fi\n"
            "if [ \"${1:-}\" = --help ] && [ \"$#\" = 1 ]; then printf 'help\\n' >> " + shlex.quote(str(resume_calls)) + "; printf 'incompatible interface\\n' >&2; exit 0; fi\n"
            "printf 'provider\\n' >> " + shlex.quote(str(resume_calls)) + "\nexit 99\n",
            encoding="utf-8",
        )
        resume_fake.chmod(0o755)
        resume_calls.unlink(missing_ok=True)
        assert run_controller(resume_job, resume_bin) == MODULE.EXIT_BY_REASON["selection_preflight_failed"]
        resume_terminal, _raw, resume_terminal_sha = MODULE.load_state(resume_job)
        assert resume_terminal["conversation_id"] == "conversation-1"
        assert not resume_terminal["resume_available"] and resume_terminal["next_action"] == "none"
        resume_public = MODULE.public_status(resume_terminal, resume_terminal_sha, job=resume_job)
        assert {item["action"] for item in resume_public["available_actions"]}.isdisjoint({"resume", "restart"})
        resume_before_rejected_command = (resume_job / MODULE.STATE_NAME).read_bytes()
        rejected_resume = subprocess.run(
            [sys.executable, str(SOURCE), "resume", "--job-dir", str(resume_job),
             "--approve-state-sha", resume_terminal_sha],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert rejected_resume.returncode == MODULE.EXIT_BY_REASON["resume_failed"] and not rejected_resume.stdout
        assert (resume_job / MODULE.STATE_NAME).read_bytes() == resume_before_rejected_command
        assert resume_calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]

        # A continuation can fail its final direct-selection probe after its
        # exact prior candidate and driver feedback were already staged.  The
        # candidate remains reviewable/finalizable, but this local preflight
        # failure must never become authority for another provider attempt in
        # the same job merely because one cycle remains.
        continue_job, continue_bin, continue_calls, continue_fake, continue_command = fixture(
            "selection-preflight-continue", workflow="project", max_cycles=3,
        )
        assert_attempt(
            continue_job, continue_bin, continue_calls, continue_command, "initial",
        )
        candidate, _raw, candidate_sha = MODULE.load_state(continue_job)
        continue_verification = {
            "schema_version": 2,
            "summary": "driver found a repair",
            "passed_checks": [],
            "failed_checks": ["fixture"],
            "advisory_checks": 0,
            "missing_checks": 0,
            "candidate_sha256": candidate["result_sha256"],
            "coverage": "partial",
            "verified_findings": 1,
            "unresolved_gaps": 1,
            "diff_review_complete": True,
        }
        MODULE.create_state(
            continue_job, "conversation-continue", resume=True,
            approve_sha=candidate_sha, verification=continue_verification,
        )
        continue_fake.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ] && [ \"$#\" = 1 ]; then printf 'version\\n' >> " + shlex.quote(str(continue_calls)) + "; printf '1.1.16\\n'; exit 0; fi\n"
            "if [ \"${1:-}\" = --help ] && [ \"$#\" = 1 ]; then printf 'help\\n' >> " + shlex.quote(str(continue_calls)) + "; printf 'incompatible interface\\n' >&2; exit 0; fi\n"
            "printf 'provider\\n' >> " + shlex.quote(str(continue_calls)) + "\nexit 99\n",
            encoding="utf-8",
        )
        continue_fake.chmod(0o755)
        continue_calls.unlink(missing_ok=True)
        assert run_controller(continue_job, continue_bin) == MODULE.EXIT_BY_REASON["selection_preflight_failed"]
        failed_continue, _raw, failed_continue_sha = MODULE.load_state(continue_job)
        assert failed_continue["attempt_origin"] == "conversation-continue"
        assert failed_continue["attempt"] < failed_continue["max_cycles"]
        assert failed_continue["candidate_recognized"] and failed_continue["result_available"]
        assert failed_continue["driver_disposition"] == "unreviewed"
        assert failed_continue["reason"] == "selection_preflight_failed"
        assert failed_continue["continue_available"] is False
        continue_public = MODULE.public_status(
            failed_continue, failed_continue_sha, job=continue_job,
        )
        continue_actions = {item["action"] for item in continue_public["available_actions"]}
        assert {"result", "finalize"} <= continue_actions
        assert not ({"continue", "resume", "restart"} & continue_actions)

        captured_continue_status = io.BytesIO()

        class _ContinueStatusStdout:
            buffer = captured_continue_status

        MODULE.sys.stdout = _ContinueStatusStdout()
        try:
            MODULE.print_text_status(
                failed_continue, failed_continue_sha, job=continue_job,
            )
        finally:
            MODULE.sys.stdout = original_stdout
        continue_status_lines = captured_continue_status.getvalue().decode("utf-8").splitlines()
        assert continue_status_lines[2] == (
            'Next safe action: retrieve current bound result JSON with "$PIPELINE/agy-worker.sh" '
            "result --job-id direct-selection-preflight-continue --format json; review it and run driver checks, "
            "then Codex—not the controller—may finalize after review. "
            "No provider-launching same-job recovery is available."
        )

        immutable_state = (continue_job / MODULE.STATE_NAME).read_bytes()
        immutable_calls = continue_calls.read_bytes()
        staged_before = sorted((continue_job / "continue-staged").iterdir())
        verification_input = MODULE.canonical(continue_verification)
        rejected_commands = (
            ("resume", ["--approve-state-sha", failed_continue_sha], None),
            ("restart", ["--approve-state-sha", failed_continue_sha], None),
            ("continue", ["--approve-state-sha", failed_continue_sha], verification_input),
        )
        for action, arguments, command_input in rejected_commands:
            rejected = subprocess.run(
                [sys.executable, str(SOURCE), action, "--job-dir", str(continue_job), *arguments],
                input=command_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            expected = MODULE.EXIT_BY_REASON["resume_failed"] if action == "resume" else 64
            assert rejected.returncode == expected and not rejected.stdout, (action, rejected.returncode)
            assert (continue_job / MODULE.STATE_NAME).read_bytes() == immutable_state
            assert continue_calls.read_bytes() == immutable_calls
            assert sorted((continue_job / "continue-staged").iterdir()) == staged_before

        result_output = io.BytesIO()

        class _ResultStdout:
            buffer = result_output

        MODULE.sys.stdout = _ResultStdout()
        try:
            assert MODULE.command_result(continue_job) == 0
        finally:
            MODULE.sys.stdout = original_stdout
        assert json.loads(result_output.getvalue()) == report()

        finalized_run = subprocess.run(
            [
                sys.executable, str(SOURCE), "finalize", "--job-dir", str(continue_job),
                "--approve-state-sha", failed_continue_sha,
                "--assurance", "partially_verified",
            ],
            input=verification_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert finalized_run.returncode == 0, finalized_run.stderr.decode("utf-8", "replace")
        finalized = json.loads(finalized_run.stdout)
        assert finalized["driver_disposition"] == "partially_verified"

        # Positive control: an unrelated provider repair failure with a bound
        # candidate still permits strict same-conversation continuation.
        repair_job, repair_bin, repair_calls, _repair_fake, repair_command = fixture(
            "repair-failed-continue-control", workflow="project", max_cycles=3,
        )
        assert_attempt(repair_job, repair_bin, repair_calls, repair_command, "initial")
        repair_state, _raw, repair_sha = MODULE.load_state(repair_job)
        repair_state.update({
            "status": "failed",
            "reason": "provider_terminal_error",
            "exit_code": MODULE.EXIT_BY_REASON["provider_terminal_error"],
            "attempt_origin": "conversation-continue",
            "phase": "repair-failed",
            "continue_available": True,
        })
        _raw, repair_sha = MODULE.write_atomic(repair_job, MODULE.STATE_NAME, repair_state)
        repair_actions = {
            item["action"] for item in MODULE.public_status(
                repair_state, repair_sha, job=repair_job,
            )["available_actions"]
        }
        assert "continue" in repair_actions
        queued_repair, _queued_repair_sha = MODULE.create_state(
            repair_job, "conversation-continue", resume=True,
            approve_sha=repair_sha,
            verification={**continue_verification, "candidate_sha256": repair_state["result_sha256"]},
        )
        assert queued_repair["status"] == "queued"
        assert queued_repair["attempt_origin"] == "conversation-continue"

        blocked_job, blocked_bin, blocked_calls, _blocked_command = selection_preflight_failure(
            "selection-preflight-exhausted", max_cycles=1,
        )
        assert run_controller(blocked_job, blocked_bin) == MODULE.EXIT_BY_REASON["selection_preflight_failed"]
        assert blocked_calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]
        blocked, _raw, blocked_sha = MODULE.load_state(blocked_job)
        assert blocked["next_action"] == "none"
        assert MODULE.public_status(blocked, blocked_sha)["next_action_command"] is None
        try:
            MODULE.create_state(blocked_job, "fresh-restart", resume=True, approve_sha=blocked_sha)
        except MODULE.DispatchError:
            pass
        else:
            raise AssertionError("exhausted selection-preflight state advertised an invalid restart")

        # A failed final probe can consume the last budget too.  The persisted
        # elapsed value drives both public recovery advice and the actual
        # restart command; neither may use the stale pre-probe state.
        deadline_job, deadline_bin, deadline_calls, _deadline_command = selection_preflight_failure(
            "selection-preflight-deadline", max_cycles=2,
            idle_seconds=0.01, hard_seconds=0.10, max_seconds=0.10,
        )
        previous_delay = os.environ.get("FAKE_DIRECT_HELP_DELAY")
        os.environ["FAKE_DIRECT_HELP_DELAY"] = "0.25"
        try:
            assert run_controller(deadline_job, deadline_bin) == MODULE.EXIT_BY_REASON["selection_preflight_failed"]
        finally:
            if previous_delay is None:
                os.environ.pop("FAKE_DIRECT_HELP_DELAY", None)
            else:
                os.environ["FAKE_DIRECT_HELP_DELAY"] = previous_delay
        deadline_state, _raw, deadline_sha = MODULE.load_state(deadline_job)
        assert deadline_state["elapsed_seconds"] >= deadline_state["max_seconds"]
        assert deadline_state["next_action"] == "none"
        deadline_public = MODULE.public_status(deadline_state, deadline_sha)
        assert deadline_public["next_action_command"] is None
        rejected_restart = subprocess.run(
            [sys.executable, str(SOURCE), "restart", "--job-dir", str(deadline_job),
             "--approve-state-sha", deadline_sha],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert rejected_restart.returncode == 64 and not rejected_restart.stdout
        assert deadline_calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]

        # ProbeInterrupted is deliberately a BaseException so a local probe
        # cannot mistake HUP/INT/TERM for an evidence failure.  The controller
        # must nevertheless own the terminal state and the probe's group must
        # be gone before the no-provider result is reported.
        for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal_name = signal.Signals(number).name.lower()
            signal_job, signal_bin, signal_calls, signal_fake, _signal_command = fixture(
                f"probe-{signal_name}", idle_seconds=1, hard_seconds=3, max_seconds=10,
            )
            ready = root / f"probe-{signal_name}-ready"
            probe_pid = root / f"probe-{signal_name}-pid"
            signal_fake.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = --version ] && [ \"$#\" = 1 ]; then printf 'version\\n' >> " + shlex.quote(str(signal_calls)) + "; printf '1.1.16\\n'; exit 0; fi\n"
                "if [ \"${1:-}\" = --help ] && [ \"$#\" = 1 ]; then "
                "printf 'help\\n' >> " + shlex.quote(str(signal_calls)) + "; "
                "printf '%s\\n' \"$$\" > " + shlex.quote(str(probe_pid)) + "; "
                "touch " + shlex.quote(str(ready)) + "; while :; do sleep 1; done; fi\n"
                "printf 'provider\\n' >> " + shlex.quote(str(signal_calls)) + "\nexit 0\n",
                encoding="utf-8",
            )
            signal_fake.chmod(0o755)
            lock = signal_job / MODULE.LOCK_NAME
            descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            child_env = dict(os.environ)
            child_env["PATH"] = f"{signal_bin}{os.pathsep}{child_env.get('PATH', '')}"
            controller_process = subprocess.Popen(
                [sys.executable, str(SOURCE), "controller", "--job-dir", str(signal_job),
                 "--ownership-fd", str(descriptor)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, pass_fds=(descriptor,), env=child_env,
            )
            os.close(descriptor)
            descriptor = -1
            try:
                deadline = time.monotonic() + 3.0
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert ready.exists(), f"{signal_name} never reached the blocked help probe"
                os.killpg(controller_process.pid, number)
                assert controller_process.wait(timeout=5.0) == 128 + number
            finally:
                if controller_process.poll() is None:
                    MODULE._terminate(controller_process)
                if descriptor >= 0:
                    os.close(descriptor)
            recorded_pid = int(probe_pid.read_text(encoding="ascii"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.killpg(recorded_pid, 0)
                except ProcessLookupError:
                    break
                except PermissionError:
                    try:
                        os.kill(recorded_pid, 0)
                    except ProcessLookupError:
                        break
                time.sleep(0.02)
            else:
                raise AssertionError(f"{signal_name} left a probe process group behind")
            interrupted, _raw, _sha = MODULE.load_state(signal_job)
            assert (interrupted["status"], interrupted["reason"], interrupted["exit_code"]) == (
                "cancelled", "interrupted", 128 + number,
            )
            assert interrupted["controller_pid"] is None and interrupted["status"] != "orphaned"
            assert interrupted["remote_cancel_unverified"] and not interrupted["resume_available"]
            assert signal_calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]

        # Once the direct version/help probe is complete, a signal can arrive
        # while the bounded worktree scan still owns the launch path.  The
        # controller must resample its local signal fact before provider Popen.
        baseline_job, baseline_bin, baseline_calls, _baseline_fake, _baseline_command = fixture(
            "baseline-signal", idle_seconds=1, hard_seconds=3, max_seconds=10,
        )
        original_baseline = MODULE._bound_worktree_baseline
        signal_delivered = False

        def signal_during_baseline(state: dict, command: dict) -> None:
            nonlocal signal_delivered
            original_baseline(state, command)
            os.kill(os.getpid(), signal.SIGTERM)
            signal_delivered = True
            # Keep this wrapper on the worktree-scan side of the launch
            # boundary while CPython dispatches the installed controller
            # handler.  No provider process exists to reap in this window.
            time.sleep(0.01)

        MODULE._bound_worktree_baseline = signal_during_baseline
        try:
            assert run_controller(baseline_job, baseline_bin) == 128 + signal.SIGTERM
        finally:
            MODULE._bound_worktree_baseline = original_baseline
        assert signal_delivered
        observed_baseline_calls = baseline_calls.read_text(encoding="utf-8").splitlines()
        assert observed_baseline_calls == ["version", "help"], observed_baseline_calls
        baseline_terminal, _raw, _sha = MODULE.load_state(baseline_job)
        assert (baseline_terminal["status"], baseline_terminal["reason"], baseline_terminal["exit_code"]) == (
            "cancelled", "interrupted", 128 + signal.SIGTERM,
        )
        assert baseline_terminal["controller_pid"] is None and baseline_terminal["status"] != "orphaned"
        assert baseline_terminal["remote_cancel_unverified"] and not baseline_terminal["resume_available"]

        # A direct executable binding is evidence for the target probed before
        # the scan, not a capability to launch a replacement after it.  Swap A
        # for B from the scan hook: B must never receive a provider invocation.
        swap_job, swap_bin, swap_calls, swap_fake, _swap_command = fixture(
            "scan-window-executable-swap", idle_seconds=1, hard_seconds=3, max_seconds=10,
        )
        swap_replacement = swap_bin / "agy-B"
        swap_replacement.write_text(
            "#!/bin/sh\nprintf 'B-provider\\n' >> " + shlex.quote(str(swap_calls)) + "\nexit 0\n",
            encoding="utf-8",
        )
        swap_replacement.chmod(0o755)
        original_baseline = MODULE._bound_worktree_baseline
        swapped = False

        def replace_after_scan(state: dict, command: dict) -> None:
            nonlocal swapped
            original_baseline(state, command)
            os.replace(swap_replacement, swap_fake)
            swapped = True

        MODULE._bound_worktree_baseline = replace_after_scan
        try:
            assert run_controller(swap_job, swap_bin) == MODULE.EXIT_BY_REASON["selection_preflight_failed"]
        finally:
            MODULE._bound_worktree_baseline = original_baseline
        assert swapped
        assert swap_calls.read_text(encoding="utf-8").splitlines() == ["version", "help"]
        swap_terminal, _raw, _sha = MODULE.load_state(swap_job)
        assert (swap_terminal["reason"], swap_terminal["failure_stage"]) == (
            "selection_preflight_failed", "selection_preflight",
        )

    check("direct selections re-probe every attempt and reject rewrite-during-help before provider launch", direct_selection_reprobes_every_controller_attempt)

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

    def every_git_enumeration_is_preceded_by_exact_root_binding() -> None:
        """No ls-files/list-tree/config read may precede the bound-root facts."""
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
        try:
            snapshot = MODULE._worktree_snapshot(str(repo))
            assert snapshot is not None and snapshot["entries"] == 0
        finally:
            os.environ["PATH"] = previous_path
        commands = log.read_text(encoding="utf-8").splitlines()
        enumerations = [
            index for index, command in enumerate(commands)
            if " ls-files " in f" {command} "
            or " ls-tree " in f" {command} "
            or " config " in f" {command} "
        ]
        assert enumerations, commands
        for index in enumerations:
            assert index >= 2, commands[:index + 1]
            assert commands[index - 2].endswith("rev-parse --is-inside-work-tree"), commands[max(0, index - 3):index + 1]
            assert commands[index - 1].endswith("rev-parse --show-toplevel"), commands[max(0, index - 3):index + 1]

    check("every Git listing and config enumeration follows exact bound-root checks", every_git_enumeration_is_preceded_by_exact_root_binding)

    def normal_standard_and_linked_worktrees_create_bound_v9_state() -> None:
        fixture = root / "bound-positive-controls"; fixture.mkdir()
        source_repo = fixture / "source"; source_repo.mkdir()
        linked = fixture / "linked-worktree"
        try:
            subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "commit", "--allow-empty", "-qm", "base"], check=True)
            subprocess.run(
                ["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", "bound-linked-worktree", str(linked)],
                check=True,
            )
            linked = linked.resolve()

            for label, worktree in (("standard", source_repo), ("linked", linked)):
                snapshot = MODULE._worktree_snapshot(str(worktree))
                assert snapshot is not None and snapshot["entries"] == 0, label
                job = fixture / f"{label}-job"; job.mkdir(mode=0o700)
                schema = fixture / f"{label}-provider.json"; provider_schema(schema)
                command = {
                    "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"bound-{label}",
                    "workdir": str(worktree), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
                    "agy_version": "1.1.16", "agy_version_observed": True,
                    "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                    "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
                    "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
                }
                MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
                state, _state_sha = MODULE.create_state(job, "initial", resume=False)
                persisted = json.loads((job / MODULE.STATE_NAME).read_text(encoding="utf-8"))
                assert state["schema_version"] == persisted["schema_version"] == MODULE.CURRENT_STATE_SCHEMA == 9, label
                assert state["worktree_snapshot_algorithm"] == MODULE.CURRENT_WORKTREE_SNAPSHOT_ALGORITHM, label
                assert state["worktree_baseline"] is not None, label
                assert state["worktree_root_identity"] is not None, label
                assert (job / MODULE.STATE_NAME).is_file(), label
        finally:
            if linked.exists():
                subprocess.run(["git", "-C", str(source_repo), "worktree", "remove", "--force", str(linked)], check=True)
            shutil.rmtree(fixture)

    check("normal standard and linked worktrees persist a bound V9 dispatch state", normal_standard_and_linked_worktrees_create_bound_v9_state)

    def nested_git_entries_fail_closed_without_opening_them() -> None:
        """Only the root marker may be bound; nested markers are never content."""
        source_repo = root / "nested-git-source"; source_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "commit", "--allow-empty", "-qm", "base"], check=True)
        linked = root / "nested-git-linked"
        subprocess.run(["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", "nested-git-linked", str(linked)], check=True)
        linked = linked.resolve()
        # A normal checkout binds its directory marker; the project-only
        # linked checkout separately binds its marker file.
        assert MODULE._worktree_snapshot(str(source_repo)) is not None
        assert MODULE._worktree_snapshot(str(linked)) is not None
        assert MODULE._project_boundary(str(linked))["kind"] == "file"

        for kind in ("file", "directory", "symlink", "special"):
            nested = linked / f"nested-{kind}"; nested.mkdir()
            marker = nested / ".git"
            if kind == "file":
                marker.write_text("not a root marker\n", encoding="utf-8")
            elif kind == "directory":
                marker.mkdir(); (marker / "secret").write_text("do not read\n", encoding="utf-8")
            elif kind == "symlink":
                marker.symlink_to(root / "outside-nested-git-marker")
            else:
                os.mkfifo(marker)

            parent_info = os.stat(nested)
            denied: list[str] = []
            original_open = MODULE.os.open
            def no_nested_marker_open(name, flags, mode=0o777, *, dir_fd=None):
                if (
                    name == ".git" and dir_fd is not None
                    and os.fstat(dir_fd).st_dev == parent_info.st_dev
                    and os.fstat(dir_fd).st_ino == parent_info.st_ino
                ):
                    denied.append(kind)
                    raise AssertionError("nested Git marker was opened")
                return original_open(name, flags, mode, dir_fd=dir_fd)
            MODULE.os.open = no_nested_marker_open
            try:
                assert MODULE._worktree_snapshot(str(linked)) is None, kind
            finally:
                MODULE.os.open = original_open
            assert not denied, kind
            try:
                MODULE._project_boundary(str(linked))
            except MODULE.DispatchError as exc:
                assert str(exc) == "project worktree has nested Git administration"
            else:
                raise AssertionError(f"nested {kind} marker passed the project boundary")
            if marker.is_dir() and not marker.is_symlink():
                shutil.rmtree(marker)
            else:
                marker.unlink()
            nested.rmdir()

        job, state, _sha, _envelope = current_candidate_fixture("nested-git-actions")
        candidate_repo = Path(json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))["workdir"])
        nested = candidate_repo / "nested"; nested.mkdir()
        (nested / ".git").write_text("nested authority\n", encoding="utf-8")
        candidate_snapshot_actions_reject(
            job, state, "nested-git",
            continuation_error="dispatch worktree root binding changed",
        )

        preflight_repo = root / "nested-git-preflight"; preflight_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(preflight_repo)], check=True)
        (preflight_repo / "nested").mkdir()
        (preflight_repo / "nested" / ".git").write_text("nested authority\n", encoding="utf-8")
        preflight_job = root / "nested-git-preflight-job"; preflight_job.mkdir(mode=0o700)
        schema = root / "nested-git-preflight-provider.json"; provider_schema(schema)
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "nested-git-preflight",
            "workdir": str(preflight_repo), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        MODULE.write_atomic(preflight_job, MODULE.COMMAND_NAME, command)
        try:
            MODULE.create_state(preflight_job, "initial", resume=False)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch worktree root cannot be bound"
        else:
            raise AssertionError("nested Git marker created a dispatch state")
        assert not (preflight_job / MODULE.STATE_NAME).exists()
        stream_path, stderr_path, envelope_path = MODULE._attempt_paths(preflight_job, 1)
        assert not stream_path.exists() and not stderr_path.exists() and not envelope_path.exists()
        popen_calls: list[object] = []
        original_popen = MODULE.subprocess.Popen
        def no_controller_or_provider_process(*arguments, **keywords):
            del keywords
            popen_calls.append(arguments[0] if arguments else None)
            raise AssertionError("nested Git preflight reached a controller/provider process")
        MODULE.subprocess.Popen = no_controller_or_provider_process
        try:
            try:
                run_controller(preflight_job, root)
            except MODULE.DispatchError as exc:
                assert str(exc) == "dispatch state is unavailable"
            else:
                raise AssertionError("nested Git preflight unexpectedly entered the controller")
        finally:
            MODULE.subprocess.Popen = original_popen
        assert not popen_calls
        assert not stream_path.exists() and not stderr_path.exists() and not envelope_path.exists()

    check("nested .git file directory symlink and special entries fail before snapshot, boundary, actions, or provider", nested_git_entries_fail_closed_without_opening_them)

    def casefold_nested_git_aliases_fail_before_git_or_project_actions() -> None:
        """A Darwin-style ``.GIT`` alias is an authority marker, never content."""
        source_repo = root / "casefold-nested-git-source"; source_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "commit", "--allow-empty", "-qm", "base"], check=True)
        linked = root / "casefold-nested-git-linked"
        subprocess.run(
            ["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", "casefold-nested-git-linked", str(linked)],
            check=True,
        )
        linked = linked.resolve()
        # Exact normal and linked root markers remain positive controls.
        assert MODULE._worktree_snapshot(str(source_repo)) is not None
        assert MODULE._worktree_snapshot(str(linked)) is not None
        assert MODULE._project_boundary(str(linked))["kind"] == "file"

        nested = linked / "nested"; nested.mkdir()
        alias = nested / ".GIT"; alias.write_text("nested authority\n", encoding="utf-8")
        calls: list[object] = []
        original_popen = MODULE.subprocess.Popen
        def no_git_process(*arguments, **keywords):
            del keywords
            calls.append(arguments[0] if arguments else None)
            raise AssertionError("casefold nested marker reached a Git subprocess")
        MODULE.subprocess.Popen = no_git_process
        try:
            assert MODULE._worktree_snapshot(str(linked)) is None
        finally:
            MODULE.subprocess.Popen = original_popen
        assert not calls
        try:
            MODULE._project_boundary(str(linked))
        except MODULE.DispatchError as exc:
            assert str(exc) == "project worktree has nested Git administration"
        else:
            raise AssertionError("casefold nested Git marker passed the project boundary")

        # Result, continue, and finalize share the snapshot guard.  Under the
        # vulnerable exact-name implementation this first observation succeeds,
        # so a forged candidate can authorize all three actions.
        job, state, _sha, _envelope = current_candidate_fixture("casefold-nested-git-actions")
        candidate_repo = Path(json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))["workdir"])
        candidate_nested = candidate_repo / "nested"; candidate_nested.mkdir()
        (candidate_nested / ".GIT").write_text("nested authority\n", encoding="utf-8")
        vulnerable_snapshot = MODULE._worktree_snapshot(str(candidate_repo))
        if vulnerable_snapshot is not None:
            state.update({
                "worktree_baseline": vulnerable_snapshot,
                "candidate_worktree_sha256": vulnerable_snapshot["sha256"],
                "candidate_worktree_entries": vulnerable_snapshot["entries"],
            })
        candidate_snapshot_actions_reject(
            job, state, "casefold-nested-git",
            continuation_error="dispatch worktree root binding changed",
        )

        # The initial controller path must reject before the fake provider can
        # run; this covers shell-created state as well as status/action reads.
        preflight_repo = root / "casefold-nested-git-preflight"; preflight_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(preflight_repo)], check=True)
        (preflight_repo / "nested").mkdir()
        (preflight_repo / "nested" / ".GIT").write_text("nested authority\n", encoding="utf-8")
        preflight_job = root / "casefold-nested-git-preflight-job"; preflight_job.mkdir(mode=0o700)
        schema = root / "casefold-nested-git-preflight-provider.json"; provider_schema(schema)
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "casefold-nested-git-preflight",
            "workdir": str(preflight_repo), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        MODULE.write_atomic(preflight_job, MODULE.COMMAND_NAME, command)
        try:
            MODULE.create_state(preflight_job, "initial", resume=False)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch worktree root cannot be bound"
        else:
            raise AssertionError("casefold nested Git marker created a dispatch state")
        assert not (preflight_job / MODULE.STATE_NAME).exists()
        stream_path, stderr_path, envelope_path = MODULE._attempt_paths(preflight_job, 1)
        assert not stream_path.exists() and not stderr_path.exists() and not envelope_path.exists()
        popen_calls: list[object] = []
        original_popen = MODULE.subprocess.Popen
        def no_controller_or_provider_process(*arguments, **keywords):
            del keywords
            popen_calls.append(arguments[0] if arguments else None)
            raise AssertionError("casefold nested Git preflight reached a controller/provider process")
        MODULE.subprocess.Popen = no_controller_or_provider_process
        try:
            try:
                run_controller(preflight_job, root)
            except MODULE.DispatchError as exc:
                assert str(exc) == "dispatch state is unavailable"
            else:
                raise AssertionError("casefold nested Git preflight unexpectedly entered the controller")
        finally:
            MODULE.subprocess.Popen = original_popen
        assert not popen_calls
        assert not stream_path.exists() and not stderr_path.exists() and not envelope_path.exists()

    check("casefold nested .GIT aliases fail before Git, project, result, continue, finalize, or provider actions", casefold_nested_git_aliases_fail_before_git_or_project_actions)

    def resolve_undo_semantics_fail_closed_and_block_candidate_actions() -> None:
        """REUC is semantic index state even after a conflict is resolved and added."""
        def leave_resolve_undo(repo: Path) -> bytes:
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            primary = subprocess.run(
                ["git", "-C", str(repo), "branch", "--show-current"], check=True, stdout=subprocess.PIPE,
            ).stdout.decode("utf-8", "strict").strip()
            subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "resolve-undo-side"], check=True)
            tracked.write_text("side\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-am", "side", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "checkout", "-q", primary], check=True)
            tracked.write_text("primary\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-am", "primary", "-q"], check=True)
            merged = subprocess.run(
                ["git", "-C", str(repo), "merge", "resolve-undo-side"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            assert merged.returncode == 1
            tracked.write_text("resolved\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            raw = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "--resolve-undo", "-z"],
                check=True, stdout=subprocess.PIPE,
            ).stdout
            assert len(raw.split(b"\0")[:-1]) == 3, raw
            return raw

        repo = root / "resolve-undo-repro"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        raw = leave_resolve_undo(repo)
        object_length = len(raw.split(b" ", 2)[1])
        parsed = MODULE._parse_resolve_undo(raw, object_length)
        assert parsed is not None and len(parsed) == 3
        legacy_before = MODULE._worktree_snapshot(str(repo), legacy=True)
        assert legacy_before is not None
        assert MODULE._worktree_snapshot(str(repo)) is None
        staged_before = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--stage", "-z"], check=True, stdout=subprocess.PIPE,
        ).stdout
        bytes_before = (repo / "tracked.txt").read_bytes()
        subprocess.run(["git", "-C", str(repo), "update-index", "--clear-resolve-undo"], check=True)
        assert subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--resolve-undo", "-z"], check=True, stdout=subprocess.PIPE,
        ).stdout == b""
        assert subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--stage", "-z"], check=True, stdout=subprocess.PIPE,
        ).stdout == staged_before
        assert (repo / "tracked.txt").read_bytes() == bytes_before
        legacy_after = MODULE._worktree_snapshot(str(repo), legacy=True)
        semantic_after = MODULE._worktree_snapshot(str(repo))
        assert legacy_after is not None and legacy_after["sha256"] != legacy_before["sha256"]
        assert semantic_after is not None

        job, state, _sha, _envelope = current_candidate_fixture("resolve-undo")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        candidate_repo = Path(command["workdir"])
        leave_resolve_undo(candidate_repo)
        candidate_snapshot_actions_reject(job, state, "resolve-undo")

    check("resolve-undo state fails closed for v7 and result continue finalize parity while v6 remains exact", resolve_undo_semantics_fail_closed_and_block_candidate_actions)

    def malformed_duplicate_and_racing_resolve_undo_fail_closed() -> None:
        repo = root / "resolve-undo-shape-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        oid = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", ":tracked.txt"], check=True, stdout=subprocess.PIPE,
        ).stdout.decode("ascii", "strict").strip()
        valid = f"100644 {oid} 1\ttracked.txt\0".encode("ascii")
        duplicate = valid + valid
        assert MODULE._parse_resolve_undo(b"", len(oid)) == {}
        assert MODULE._parse_resolve_undo(valid, len(oid)) == {(b"tracked.txt", 1): (0o100644, oid.encode("ascii"))}
        assert MODULE._parse_resolve_undo(b"malformed\0", len(oid)) is None
        assert MODULE._parse_resolve_undo(duplicate, len(oid)) is None

        real_git = shutil.which("git"); assert real_git is not None
        bin_dir = root / "resolve-undo-shape-bin"; bin_dir.mkdir()
        mode_file = root / "resolve-undo-mode"
        count_file = root / "resolve-undo-count"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\n"
            "mode=$(cat " + shlex.quote(str(mode_file)) + ")\n"
            "case \" $* \" in *\" ls-files --resolve-undo -z \"*)\n"
            "  case \"$mode\" in\n"
            "    malformed) printf 'malformed\\0'; exit 0;;\n"
            "    duplicate) printf '100644 " + oid + " 1\\ttracked.txt\\0100644 " + oid + " 1\\ttracked.txt\\0'; exit 0;;\n"
            "    race) count=0; if [ -r " + shlex.quote(str(count_file)) + " ]; then count=$(cat " + shlex.quote(str(count_file)) + "); fi; "
            "count=$((count + 1)); printf '%s\\n' \"$count\" > " + shlex.quote(str(count_file)) + "; "
            "if [ \"$count\" -gt 1 ]; then printf '100644 " + oid + " 1\\ttracked.txt\\0'; fi; exit 0;;\n"
            "  esac;;\n"
            "esac\n"
            "exec " + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
        try:
            for mode in ("malformed", "duplicate", "race"):
                mode_file.write_text(mode, encoding="ascii")
                if count_file.exists(): count_file.unlink()
                assert MODULE._worktree_snapshot(str(repo)) is None, mode
                if mode == "race":
                    assert int(count_file.read_text(encoding="ascii")) > 1
        finally:
            os.environ["PATH"] = previous_path

    check("malformed duplicate and racing resolve-undo output fails closed", malformed_duplicate_and_racing_resolve_undo_fail_closed)

    def malformed_debug_stat_cache_fails_closed_and_blocks_candidate_actions() -> None:
        """Only documented numeric ls-files --debug cache fields are eligible."""
        job, state, _sha, _envelope = current_candidate_fixture("debug-stat-cache")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        baseline = MODULE._worktree_snapshot(str(repo)); assert baseline is not None
        state.update({
            "worktree_baseline": baseline,
            "candidate_worktree_sha256": baseline["sha256"],
            "candidate_worktree_entries": baseline["entries"],
            "continue_available": True,
        })

        real_git = shutil.which("git"); assert real_git is not None
        bin_dir = root / "debug-stat-cache-bin"; bin_dir.mkdir()
        mode_file = root / "debug-stat-cache-mode"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\n"
            "mode=$(cat " + shlex.quote(str(mode_file)) + ")\n"
            "case \" $* \" in *\" ls-files --debug -z \"*)\n"
            "  case \"$mode\" in\n"
            "    garbage) printf 'tracked.txt\\0  ctime: garbage\\n  mtime: garbage\\n  dev: garbage\\n  uid: garbage\\n  size: 5\\tflags: 0\\n'; exit 0;;\n"
            "    missing) printf 'tracked.txt\\0  ctime: 1:\\n  mtime: 3:4\\n  dev: 5\\tino: 6\\n  uid: 7\\tgid: 8\\n  size: 5\\tflags: 0\\n'; exit 0;;\n"
            "    trailing) printf 'tracked.txt\\0  ctime: 1:2 junk\\n  mtime: 3:4\\n  dev: 5\\tino: 6\\n  uid: 7\\tgid: 8\\n  size: 5\\tflags: 0\\n'; exit 0;;\n"
            "    valid) printf 'tracked.txt\\0  ctime: 1:2\\n  mtime: 3:4\\n  dev: 5\\tino: 6\\n  uid: 7\\tgid: 8\\n  size: 5\\tflags: 0\\n'; exit 0;;\n"
            "  esac;;\n"
            "esac\n"
            "exec " + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
        try:
            # This exact safe-Git output was accepted by the vulnerable parser.
            mode_file.write_text("garbage", encoding="ascii")
            assert MODULE._worktree_snapshot(str(repo)) is None
            candidate_snapshot_actions_reject(job, state, "debug-stat-cache-garbage")
            for mode in ("missing", "trailing"):
                mode_file.write_text(mode, encoding="ascii")
                assert MODULE._worktree_snapshot(str(repo)) is None, mode
                candidate_snapshot_actions_reject(job, state, f"debug-stat-cache-{mode}")
            mode_file.write_text("valid", encoding="ascii")
            assert MODULE._worktree_snapshot(str(repo)) == baseline
            _raw, valid_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            actions = {item["action"] for item in MODULE.public_status(state, valid_sha, job=job)["available_actions"]}
            assert {"result", "continue", "finalize"} <= actions
        finally:
            os.environ["PATH"] = previous_path

    check("malformed debug stat cache fails closed while numeric zero-flag records preserve candidate parity", malformed_debug_stat_cache_fails_closed_and_blocks_candidate_actions)

    def intent_to_add_flags_fail_closed_and_cannot_authorize_candidate_actions() -> None:
        """CE_INTENT_TO_ADD is semantic even when --stage reports the empty OID."""
        job, state, _sha, _envelope = current_candidate_fixture("intent-to-add")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        empty = repo / "empty"; empty.write_bytes(b"")
        subprocess.run(["git", "-C", str(repo), "add", "-N", "empty"], check=True)
        intent_snapshot = MODULE._worktree_snapshot(str(repo))
        # The vulnerable v7 observation accepted the intent entry.  Keep this
        # branch so the red test also proves add-N -> add action parity instead
        # of merely testing a synthetic flag parser.
        if intent_snapshot is not None:
            state.update({
                "worktree_baseline": intent_snapshot,
                "candidate_worktree_sha256": intent_snapshot["sha256"],
                "candidate_worktree_entries": intent_snapshot["entries"],
            })
            subprocess.run(["git", "-C", str(repo), "add", "empty"], check=True)
            ordinary = MODULE._worktree_snapshot(str(repo))
            assert ordinary == intent_snapshot, "intent-to-add changed a semantic flag without changing v7"
        candidate_snapshot_actions_reject(job, state, "intent-to-add")

    check("intent-to-add fails closed for snapshots and result continue finalize parity", intent_to_add_flags_fail_closed_and_cannot_authorize_candidate_actions)

    def v6_snapshot_readback_uses_legacy_digest_and_rejects_semantic_substitution() -> None:
        """Persisted v6 candidates must never be silently reinterpreted as v7."""
        job, state, _sha, _envelope = current_candidate_fixture("v6-snapshot")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        legacy = MODULE._worktree_snapshot(command["workdir"], legacy=True)
        semantic = MODULE._worktree_snapshot(command["workdir"])
        assert legacy is not None and semantic is not None and legacy["sha256"] != semantic["sha256"]
        state.update({
            "schema_version": 6,
            "worktree_baseline": legacy,
            "candidate_worktree_sha256": legacy["sha256"],
            "candidate_worktree_entries": legacy["entries"],
            "continue_available": True,
        })
        state.pop("worktree_snapshot_algorithm")
        root_identity = state.pop("worktree_root_identity")
        legacy_raw, legacy_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        loaded, _raw, loaded_sha = MODULE.load_state(job)
        assert loaded_sha == legacy_sha
        assert "worktree_root_identity" not in loaded
        hybrid = dict(loaded)
        hybrid["worktree_root_identity"] = root_identity
        try:
            MODULE.validate_state(hybrid)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch state fields are invalid"
        else:
            raise AssertionError("v6 state accepted a v9 root identity")
        assert (job / MODULE.STATE_NAME).read_bytes() == legacy_raw
        MODULE._bound_candidate_worktree(loaded, command)
        actions = {item["action"] for item in MODULE.public_status(loaded, loaded_sha, job=job)["available_actions"]}
        assert {"result", "continue", "finalize"} <= actions
        substituted = dict(loaded)
        substituted["candidate_worktree_sha256"] = semantic["sha256"]
        substituted_raw, substituted_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, substituted)
        stale = MODULE.public_status(substituted, substituted_sha, job=job)
        assert {item["action"] for item in stale["available_actions"]} == {"result"}
        assert stale["result_available"] is True
        assert stale["candidate_sha256"] == substituted["result_sha256"]
        assert stale["continue_available"] is False
        try:
            MODULE._bound_candidate_worktree(substituted, command)
        except MODULE.DispatchError as exc:
            assert str(exc) == "candidate worktree binding changed"
        else:
            raise AssertionError("v6 candidate silently accepted a v7 semantic digest")
        delivered = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert delivered.returncode == 0
        assert delivered.stdout == _envelope.read_bytes() and delivered.stderr == b""
        assert (job / MODULE.STATE_NAME).read_bytes() == substituted_raw

        bin_dir = root / "v6-substituted-provider-bin"; bin_dir.mkdir()
        provider_called = root / "v6-substituted-provider-called"
        fake = bin_dir / "agy"
        fake.write_text("#!/bin/sh\nprintf called > " + shlex.quote(str(provider_called)) + "\n", encoding="utf-8")
        fake.chmod(0o755)
        verification = {
            "schema_version": 2, "summary": "legacy driver repair", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": substituted["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        for arguments in (
            ("restart", "--job-dir", str(job), "--approve-state-sha", substituted_sha),
            ("continue", "--job-dir", str(job), "--approve-state-sha", substituted_sha),
            ("finalize", "--job-dir", str(job), "--approve-state-sha", substituted_sha,
             "--assurance", "partially_verified"),
        ):
            rejected = subprocess.run(
                [sys.executable, str(SOURCE), *arguments], input=json.dumps(verification).encode("utf-8"),
                env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert rejected.returncode != 0 and rejected.stdout == b"", arguments
            assert (job / MODULE.STATE_NAME).read_bytes() == substituted_raw, arguments
            assert not provider_called.exists(), arguments
            assert not (job / "continue-staged").exists(), arguments
            assert not list(job.glob("*stream.ndjson")), arguments
        _raw, loaded_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, loaded)
        verification = {
            "schema_version": 2, "summary": "legacy driver repair", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": loaded["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        queued, _queued_sha = MODULE.create_state(
            job, "conversation-continue", resume=True,
            approve_sha=loaded_sha, verification=verification,
        )
        assert queued["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
        assert queued["worktree_baseline"] == semantic
        assert queued["candidate_worktree_sha256"] == semantic["sha256"]
        assert queued["candidate_worktree_entries"] == semantic["entries"]
        assert queued["worktree_root_identity"] == MODULE._dispatch_root_identity(command["workdir"])

    check("v6 candidate readback retains its legacy digest then atomically migrates to a fresh v9 semantic binding", v6_snapshot_readback_uses_legacy_digest_and_rejects_semantic_substitution)

    def v5_through_v8_status_parity_and_migration_are_exact() -> None:
        """Legacy status is read-only; eligible writes acquire one V9 binding."""
        for version in (5, 6, 7, 8):
            job, state, _sha, _envelope = current_candidate_fixture(f"v{version}-migration")
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            legacy = MODULE._worktree_snapshot(command["workdir"], legacy=True)
            semantic = MODULE._worktree_snapshot(command["workdir"])
            assert legacy is not None and semantic is not None
            state["schema_version"] = version
            if version in {5, 6}:
                expected = legacy
            else:
                expected = semantic
            state.update({
                "worktree_baseline": expected,
                "candidate_worktree_sha256": expected["sha256"],
                "candidate_worktree_entries": expected["entries"],
                "continue_available": True,
            })
            if version == 5:
                state.pop("selection_sha256"); state.pop("selection_identity")
            if version < 8:
                state.pop("worktree_snapshot_algorithm")
            if version < MODULE.CURRENT_STATE_SCHEMA:
                state.pop("worktree_root_identity")
            _raw, legacy_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            loaded, _raw, loaded_sha = MODULE.load_state(job)
            assert loaded_sha == legacy_sha and loaded["schema_version"] == version
            actions = {
                item["action"] for item in MODULE.public_status(loaded, loaded_sha, job=job)["available_actions"]
            }
            assert {"result", "continue", "finalize"} <= actions, version
            verification = {
                "schema_version": 2, "summary": f"v{version} migration", "passed_checks": [],
                "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": loaded["result_sha256"], "coverage": "partial",
                "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
            }
            queued, _queued_sha = MODULE.create_state(
                job, "conversation-continue", resume=True,
                approve_sha=loaded_sha, verification=verification,
            )
            assert queued["schema_version"] == MODULE.CURRENT_STATE_SCHEMA, version
            assert queued["worktree_snapshot_algorithm"] == MODULE.WORKTREE_SNAPSHOT_SEMANTIC_V1
            assert queued["worktree_baseline"] == semantic
            assert queued["candidate_worktree_sha256"] == semantic["sha256"]
            assert queued["candidate_worktree_entries"] == semantic["entries"]
            assert queued["worktree_root_identity"] == MODULE._dispatch_root_identity(command["workdir"])

    def v1_candidate_status_is_read_only_and_all_mutations_fail_without_writes() -> None:
        """V1 result evidence remains readable but has no lifecycle authority."""
        fake_bin = root / "legacy-mutation-fake-bin"; fake_bin.mkdir(mode=0o700)
        provider_marker = root / "legacy-mutation-provider-called"
        fake_agy = fake_bin / "agy"
        fake_agy.write_text(
            "#!/bin/sh\nprintf called > " + shlex.quote(str(provider_marker)) + "\nexit 99\n",
            encoding="utf-8",
        )
        fake_agy.chmod(0o700)
        environment = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}

        for version in (1,):
            for workflow in ("task", "project"):
                job, state, _sha, envelope = current_candidate_fixture(
                    f"v{version}-{workflow}-readonly",
                    workflow=workflow, linked=workflow == "project",
                )
                state["schema_version"] = version
                removed = {*MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS}
                if version == 1:
                    removed.update(MODULE.STATE_PROJECT_FIELDS)
                    removed.update({"provider_retry_after_seconds", "provider_retry_observed_epoch"})
                for key in removed:
                    state.pop(key, None)
                old_raw, old_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
                loaded, loaded_raw, loaded_sha = MODULE.load_state(job)
                assert loaded_raw == old_raw and loaded_sha == old_sha

                direct_public = MODULE.public_status(loaded, loaded_sha, job=job)
                assert {item["action"] for item in direct_public["available_actions"]} == {"result"}, (
                    version, workflow, direct_public["available_actions"],
                )
                commands = (
                    [sys.executable, str(SOURCE), "status", "--job-dir", str(job)],
                    [sys.executable, str(SOURCE), "wait", "--job-dir", str(job),
                     "--after-state-sha", loaded_sha, "--timeout", "1s"],
                )
                projected = []
                for command_line in commands:
                    outcome = subprocess.run(
                        command_line, env=environment, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, check=False,
                    )
                    assert outcome.returncode == 0 and not outcome.stderr
                    projected.append(json.loads(outcome.stdout))
                assert projected[0]["available_actions"] == projected[1]["available_actions"]
                assert {item["action"] for item in projected[0]["available_actions"]} == {"result"}
                delivered = subprocess.run(
                    [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False,
                )
                assert delivered.returncode == 0 and delivered.stdout == envelope.read_bytes()
                assert not delivered.stderr and (job / MODULE.STATE_NAME).read_bytes() == old_raw

                verification = {
                    "schema_version": 2, "summary": "historical candidate cannot mutate",
                    "passed_checks": [], "failed_checks": ["legacy-authority"],
                    "advisory_checks": 0, "missing_checks": 0,
                    "candidate_sha256": loaded["result_sha256"], "coverage": "partial",
                    "verified_findings": 1, "unresolved_gaps": 1,
                    "diff_review_complete": True,
                }
                attempts = (
                    ("resume", 21, []),
                    ("restart", 64, []),
                    ("continue", 64, []),
                    ("finalize", 64, ["--assurance", "partially_verified"]),
                )
                for action, expected_exit, suffix in attempts:
                    command_line = [
                        sys.executable, str(SOURCE), action, "--job-dir", str(job),
                        "--approve-state-sha", loaded_sha, *suffix,
                    ]
                    outcome = subprocess.run(
                        command_line, env=environment,
                        input=(json.dumps(verification).encode("utf-8")
                               if action in {"continue", "finalize"} else None),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                    )
                    assert outcome.returncode == expected_exit and not outcome.stdout, (
                        version, workflow, action, outcome.returncode, outcome.stderr,
                    )
                    assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
                    assert not list(job.glob("attempt-*.stream.ndjson"))
                copy_parent = root / f"v{version}-{workflow}-copy-parent"; copy_parent.mkdir(mode=0o700)
                copied = subprocess.run(
                    [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(job),
                     "--destination", str(copy_parent.resolve() / "candidate")],
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                assert copied.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not copied.stdout
                assert not (copy_parent / "candidate").exists()
                assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
                assert not provider_marker.exists()

    def v3_v4_migration_requires_state_command_agreement_before_any_write() -> None:
        """Only an internally coherent V3/V4 state may obtain a migration capability."""

        verification_template = {
            "schema_version": 2, "summary": "legacy migration binding",
            "passed_checks": [], "failed_checks": ["fixture"],
            "advisory_checks": 0, "missing_checks": 0,
            "coverage": "partial", "verified_findings": 1,
            "unresolved_gaps": 1, "diff_review_complete": True,
        }

        def downgrade(
            label: str, version: int, *, patch: dict | None = None, linked: bool = False,
        ) -> tuple[Path, dict, bytes, str]:
            job, state, _sha, _envelope = current_candidate_fixture(
                f"v{version}-{label}", workflow="task", linked=linked,
            )
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            state["schema_version"] = version
            for key in {
                *MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS,
                *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS,
            }:
                state.pop(key, None)
            if version == 3:
                state.pop("provider_retry_after_seconds", None)
                state.pop("provider_retry_observed_epoch", None)
            state.update({
                "phase": None, "assurance": None, "continue_available": False,
                "project_boundary": None,
            })
            if patch is not None:
                state.update(patch)
            if state["workflow"] == "project":
                state.update({
                    "phase": "awaiting-verification", "assurance": "pending",
                    "project_boundary": MODULE._project_boundary(command["workdir"]),
                })
            raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            loaded, loaded_raw, loaded_sha = MODULE.load_state(job)
            assert loaded_raw == raw and loaded_sha == sha
            return job, loaded, raw, sha

        # Both V3 and V4 retain a bounded positive path.  It needs the separate
        # status-time migration digest, and its first write upgrades atomically.
        for version in (3, 4):
            job, loaded, raw, sha = downgrade("positive", version)
            public = MODULE.public_status(loaded, sha, job=job)
            migration_sha = public["migration_binding_sha256"]
            assert isinstance(migration_sha, str) and len(migration_sha) == 64
            actions = {item["action"] for item in public["available_actions"]}
            assert {"result", "restart", "finalize"} <= actions, (version, actions)
            assert "verification-copy" not in actions, (version, actions)
            copy_parent = root / f"legacy-v{version}-copy-parent"; copy_parent.mkdir(mode=0o700)
            copied = subprocess.run(
                [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(job),
                 "--destination", str(copy_parent.resolve() / "candidate")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert copied.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not copied.stdout
            assert not (copy_parent / "candidate").exists()
            assert (job / MODULE.STATE_NAME).read_bytes() == raw
            verification = dict(verification_template, candidate_sha256=loaded["result_sha256"])
            missing = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", sha, "--assurance", "partially_verified"],
                input=json.dumps(verification).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert missing.returncode == 64 and not missing.stdout
            assert b"legacy migration approval" in missing.stderr
            assert (job / MODULE.STATE_NAME).read_bytes() == raw
            approved = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", sha, "--approve-migration-sha", migration_sha,
                 "--assurance", "partially_verified"],
                input=json.dumps(verification).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert approved.returncode == 0 and not approved.stderr
            current, _current_raw, _current_sha = MODULE.load_state(job)
            assert current["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
            assert current["driver_disposition"] == "partially_verified"

        # A V3/V4 record may be syntactically valid after a same-UID state
        # substitution.  It still must not advertise or use any migration
        # route unless it agrees with the frozen command's immutable lifecycle
        # contract.  ``hard_seconds`` is deliberately absent: extend may alter
        # it while every listed field remains immutable.
        mismatch_cases = (
            ("job-id", {"job_id": "different-legacy-job"}, False),
            ("workflow-explore", {"workflow": "explore"}, False),
            ("workflow-project", {"workflow": "project"}, True),
            ("max-cycles", {"max_cycles": 1}, False),
            ("idle-budget", {"idle_seconds": 1.0}, False),
            ("max-budget", {"max_seconds": 19.0}, False),
        )
        fake_bin = root / "legacy-migration-binding-bin"; fake_bin.mkdir(mode=0o700)
        provider_marker = root / "legacy-migration-binding-provider-called"
        fake_agy = fake_bin / "agy"
        fake_agy.write_text(
            "#!/bin/sh\nprintf called > " + shlex.quote(str(provider_marker)) + "\nexit 99\n",
            encoding="utf-8",
        )
        fake_agy.chmod(0o700)
        environment = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        for version in (3, 4):
            for label, patch, linked in mismatch_cases:
                job, loaded, raw, sha = downgrade(label, version, patch=patch, linked=linked)
                public = MODULE.public_status(loaded, sha, job=job)
                actions = {item["action"] for item in public["available_actions"]}
                assert public["migration_binding_sha256"] is None, (version, label, public)
                assert not ({"resume", "restart", "continue", "finalize"} & actions), (
                    version, label, public["available_actions"],
                )
                verification = dict(verification_template, candidate_sha256=loaded["result_sha256"])
                for action, suffix in (
                    ("continue", ()),
                    ("finalize", ("--assurance", "partially_verified")),
                ):
                    rejected = subprocess.run(
                        [sys.executable, str(SOURCE), action, "--job-dir", str(job),
                         "--approve-state-sha", sha, "--approve-migration-sha", "0" * 64,
                         *suffix],
                        env=environment, input=json.dumps(verification).encode("utf-8"),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                    )
                    assert rejected.returncode == 64 and not rejected.stdout, (
                        version, label, action, rejected.returncode, rejected.stderr,
                    )
                    assert b"immutable lifecycle binding changed" in rejected.stderr
                    assert (job / MODULE.STATE_NAME).read_bytes() == raw
                    assert not (job / "continue-staged").exists()
                    assert not list(job.glob("attempt-*.stream.ndjson"))
                assert not provider_marker.exists()

    def v5_through_v8_status_commands_project_only_proved_actions_and_finalize_to_v9() -> None:
        """Every migratable legacy generation can prove and use its actions."""
        for version in (5, 6, 7, 8):
            job, state, _sha, _envelope = current_candidate_fixture(f"v{version}-status-positive")
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            legacy = MODULE._worktree_snapshot(command["workdir"], legacy=True)
            semantic = MODULE._worktree_snapshot(command["workdir"])
            assert legacy is not None and semantic is not None
            expected = legacy if version in {5, 6} else semantic
            state.update({
                "schema_version": version,
                "worktree_baseline": expected,
                "candidate_worktree_sha256": expected["sha256"],
                "candidate_worktree_entries": expected["entries"],
                "continue_available": True,
            })
            if version == 5:
                state.pop("selection_sha256"); state.pop("selection_identity")
            if version < 8:
                state.pop("worktree_snapshot_algorithm")
            state.pop("worktree_root_identity")
            old_raw, old_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            loaded, _raw, loaded_sha = MODULE.load_state(job)
            assert loaded_sha == old_sha
            public = MODULE.public_status(loaded, loaded_sha, job=job)
            assert {item["action"] for item in public["available_actions"]} == {
                "result", "restart", "continue", "finalize",
            }, (version, public["available_actions"])
            status = subprocess.run(
                [sys.executable, str(SOURCE), "status", "--job-dir", str(job)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            wait = subprocess.run(
                [sys.executable, str(SOURCE), "wait", "--job-dir", str(job),
                 "--after-state-sha", loaded_sha, "--timeout", "1s"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert status.returncode == wait.returncode == 0
            assert json.loads(status.stdout)["available_actions"] == json.loads(wait.stdout)["available_actions"]
            assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
            verification = {
                "schema_version": 2, "summary": f"v{version} bounded finalization",
                "passed_checks": [], "failed_checks": ["fixture"],
                "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": loaded["result_sha256"], "coverage": "partial",
                "verified_findings": 1, "unresolved_gaps": 1,
                "diff_review_complete": True,
            }
            finalized = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", loaded_sha, "--assurance", "partially_verified"],
                input=json.dumps(verification).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert finalized.returncode == 0 and not finalized.stderr, (version, finalized.stderr)
            current, _raw, _sha = MODULE.load_state(job)
            assert current["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
            assert current["worktree_snapshot_algorithm"] == MODULE.WORKTREE_SNAPSHOT_SEMANTIC_V1
            assert current["worktree_baseline"] == semantic
            assert current["candidate_worktree_sha256"] == semantic["sha256"]
            assert current["worktree_root_identity"] == MODULE._dispatch_root_identity(command["workdir"])

    def legacy_read_and_mutation_authority_contracts() -> None:
        v5_through_v8_status_parity_and_migration_are_exact()
        v1_candidate_status_is_read_only_and_all_mutations_fail_without_writes()
        v3_v4_migration_requires_state_command_agreement_before_any_write()
        v5_through_v8_status_commands_project_only_proved_actions_and_finalize_to_v9()

    if FOCUSED_CHECK == "V3/V4 migration state-command binding rejects before any write":
        check(FOCUSED_CHECK, v3_v4_migration_requires_state_command_agreement_before_any_write)
    elif FOCUSED_CHECK == "V1 legacy evidence remains result-only":
        check(FOCUSED_CHECK, v1_candidate_status_is_read_only_and_all_mutations_fail_without_writes)
    else:
        check("legacy status separates readback from proved v5-v8 mutation authority", legacy_read_and_mutation_authority_contracts)

    def independent_nonempty_snapshot_reference_preserves_v6_v7_and_v8() -> None:
        """Freeze full per-path v6/v7 bytes without dispatcher's serializers."""
        repo = root / "snapshot-compatibility-reference"; repo.mkdir()
        subprocess.run(["git", "init", "-q", "--object-format=sha1", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)

        def run_git(*arguments: str) -> bytes:
            return subprocess.run(
                ["git", "-C", str(repo), *arguments], check=True, stdout=subprocess.PIPE,
            ).stdout

        def blob_oid(payload: bytes) -> bytes:
            return hashlib.sha1(
                b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload,
            ).hexdigest().encode("ascii")

        def write(relative: str, payload: bytes, *, executable: bool = False) -> None:
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            if executable:
                path.chmod(0o755)

        base = {
            ".gitignore": b"ignored.txt\n",
            "deleted.txt": b"delete me\n",
            "mode-change.sh": b"#!/bin/sh\necho mode\n",
            "staged.txt": b"base staged\n",
            "tracked.txt": b"tracked base\n",
        }
        for relative, payload in base.items():
            write(relative, payload)
        (repo / "tracked-symlink").symlink_to("tracked.txt")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)

        staged_payload = b"staged index\n"
        write("staged.txt", staged_payload)
        (repo / "mode-change.sh").chmod(0o755)
        subprocess.run(["git", "-C", str(repo), "add", "staged.txt", "mode-change.sh"], check=True)
        (repo / "deleted.txt").unlink()
        write("untracked.txt", b"untracked\n")
        (repo / "untracked-link").symlink_to("untracked.txt")
        write("ignored.txt", b"ignored\n")
        (repo / "empty-dir").mkdir()
        os.mkfifo(repo / "special")

        head = {
            b".gitignore": (0o100644, blob_oid(base[".gitignore"])),
            b"deleted.txt": (0o100644, blob_oid(base["deleted.txt"])),
            b"mode-change.sh": (0o100644, blob_oid(base["mode-change.sh"])),
            b"staged.txt": (0o100644, blob_oid(base["staged.txt"])),
            b"tracked-symlink": (0o120000, blob_oid(b"tracked.txt")),
            b"tracked.txt": (0o100644, blob_oid(base["tracked.txt"])),
        }
        index = dict(head)
        index[b"mode-change.sh"] = (0o100755, blob_oid(base["mode-change.sh"]))
        index[b"staged.txt"] = (0o100644, blob_oid(staged_payload))
        objects = {oid: payload for _mode, oid, payload in (
            (0o100644, blob_oid(base[".gitignore"]), base[".gitignore"]),
            (0o100644, blob_oid(base["deleted.txt"]), base["deleted.txt"]),
            (0o100644, blob_oid(base["mode-change.sh"]), base["mode-change.sh"]),
            (0o100644, blob_oid(base["staged.txt"]), base["staged.txt"]),
            (0o120000, blob_oid(b"tracked.txt"), b"tracked.txt"),
            (0o100644, blob_oid(base["tracked.txt"]), base["tracked.txt"]),
            (0o100644, blob_oid(staged_payload), staged_payload),
        )}
        # Git does not report this platform's FIFO through --others.  The
        # fixture therefore proves its special type through the independent
        # directory-manifest serialization, not by inventing a Git listing.
        other = {b"untracked-link", b"untracked.txt"}
        ignored = {b"ignored.txt"}

        def parse_stage(raw: bytes) -> dict[bytes, tuple[int, bytes]]:
            result: dict[bytes, tuple[int, bytes]] = {}
            for record in raw.split(b"\0")[:-1]:
                header, name = record.split(b"\t", 1)
                mode, oid, stage = header.split(b" ")
                assert stage == b"0"
                result[name] = (int(mode, 8), oid)
            return result

        def parse_tree(raw: bytes) -> dict[bytes, tuple[int, bytes]]:
            result: dict[bytes, tuple[int, bytes]] = {}
            for record in raw.split(b"\0")[:-1]:
                header, name = record.split(b"\t", 1)
                mode, kind, oid = header.split(b" ")
                assert kind == b"blob"
                result[name] = (int(mode, 8), oid)
            return result

        assert run_git("rev-parse", "--show-object-format") == b"sha1\n"
        assert parse_tree(run_git("ls-tree", "-r", "-z", "HEAD")) == head
        assert parse_stage(run_git("ls-files", "--stage", "-z")) == index
        listed_other = set(run_git("ls-files", "-z", "--others", "--exclude-standard").split(b"\0")[:-1])
        assert listed_other == other and b"special" not in listed_other
        assert set(run_git("ls-files", "-z", "--others", "--ignored", "--exclude-standard").split(b"\0")[:-1]) == ignored

        def binding(info: os.stat_result) -> tuple[int, ...]:
            return (
                info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            )

        def authority(info: os.stat_result) -> list[int]:
            value = binding(info)
            return [value[0], value[1], value[4], value[5], stat.S_IMODE(value[2])]

        def canonical_reference(value: object) -> bytes:
            return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"

        def git_path(*arguments: str) -> Path:
            raw = run_git(*arguments).decode("utf-8", "strict").strip()
            path = Path(raw)
            return (path if path.is_absolute() else repo / path).resolve()

        root_info = os.lstat(repo)
        marker_info = os.lstat(repo / ".git")
        git_dir = git_path("rev-parse", "--absolute-git-dir")
        common_dir = git_path("rev-parse", "--git-common-dir")
        index_path = git_path("rev-parse", "--git-path", "index")
        assert stat.S_ISDIR(marker_info.st_mode) and git_dir.is_dir() and common_dir.is_dir() and index_path.is_file()

        def persistent(info: os.stat_result, *, legacy: bool) -> tuple[int, ...]:
            value = binding(info)
            return value if legacy else (stat.S_IFMT(value[2]), stat.S_IMODE(value[2]))

        def directory_reference(path: Path, *, is_root: bool, legacy: bool) -> tuple[bytes, int]:
            records: list[tuple[bytes, bytes, tuple[int, ...], bytes]] = []
            empty_directories = 0
            for entry in os.scandir(path):
                raw_name = os.fsencode(entry.name)
                if is_root and raw_name == b".git":
                    continue
                info = os.lstat(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    payload, nested_empty = directory_reference(Path(entry.path), is_root=False, legacy=legacy)
                    metadata = persistent(info, legacy=legacy)
                    kind = b"directory"
                    empty_directories += nested_empty
                elif stat.S_ISLNK(info.st_mode):
                    payload = hashlib.sha256(os.fsencode(os.readlink(entry.path))).digest()
                    metadata = persistent(info, legacy=legacy)
                    kind = b"symlink"
                elif stat.S_ISREG(info.st_mode):
                    payload = b""; metadata = (); kind = b"file"
                else:
                    payload = b""; metadata = (); kind = b"special"
                records.append((raw_name, kind, metadata, payload))
            digest = hashlib.sha256(); digest.update(b"agy-worker-directory-manifest-v1\0")
            for raw_name, kind, metadata, payload in sorted(records):
                digest.update(len(raw_name).to_bytes(8, "big")); digest.update(raw_name)
                digest.update(len(kind).to_bytes(8, "big")); digest.update(kind)
                metadata_raw = canonical_reference(list(metadata))
                digest.update(len(metadata_raw).to_bytes(8, "big")); digest.update(metadata_raw)
                digest.update(len(payload).to_bytes(8, "big")); digest.update(payload)
            if not is_root and not records:
                empty_directories += 1
            return digest.digest(), empty_directories

        def reference(*, legacy: bool) -> dict[str, object]:
            digest = hashlib.sha256()
            digest.update(b"agy-worker-worktree-v5\0" if legacy else b"agy-worker-worktree-v7\0")
            root_bytes = os.fsencode(str(repo.resolve()))
            digest.update(len(root_bytes).to_bytes(8, "big")); digest.update(root_bytes)
            digest.update(canonical_reference([root_info.st_dev, root_info.st_ino]))
            digest.update(canonical_reference(["directory", authority(marker_info), hashlib.sha256(b"").hexdigest()]))
            index_raw = index_path.read_bytes()
            digest.update(canonical_reference([
                str(git_dir), authority(os.lstat(git_dir)), str(common_dir), authority(os.lstat(common_dir)),
                str(index_path.resolve()) if legacy else None,
                hashlib.sha256(index_raw).hexdigest() if legacy else None,
                authority(os.lstat(index_path)) if legacy else None,
            ]))
            changed = 0
            for relative in sorted(set(head) | set(index) | other | ignored):
                digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
                indexed = index.get(relative); head_entry = head.get(relative)
                for label, value in ((b"head", head_entry), (b"index", indexed)):
                    digest.update(label + b"\0")
                    if value is None:
                        digest.update(b"missing\0")
                    else:
                        digest.update(f"{value[0]:o}".encode("ascii") + b"\0"); digest.update(value[1])
                path = repo / os.fsdecode(relative)
                is_other = relative in other or relative in ignored
                try:
                    info = os.lstat(path)
                except FileNotFoundError:
                    digest.update(b"missing\0")
                    differs = indexed is not None
                else:
                    digest.update(canonical_reference(list(persistent(info, legacy=legacy))))
                    if stat.S_ISLNK(info.st_mode):
                        payload = os.fsencode(os.readlink(path))
                        digest.update(b"symlink\0"); digest.update(len(payload).to_bytes(8, "big")); digest.update(payload)
                        differs = indexed is None or indexed[0] != 0o120000 or payload != objects[indexed[1]]
                    elif stat.S_ISREG(info.st_mode):
                        payload = path.read_bytes(); payload_digest = hashlib.sha256(payload).digest()
                        digest.update(b"file\0"); digest.update(payload_digest)
                        differs = (
                            indexed is None or indexed[0] not in {0o100644, 0o100755}
                            or bool(info.st_mode & 0o111) != bool(indexed[0] & 0o111)
                            or info.st_size != len(objects[indexed[1]])
                            or payload_digest != hashlib.sha256(objects[indexed[1]]).digest()
                        )
                    else:
                        digest.update(b"special\0")
                        differs = True
                if is_other or indexed != head_entry or differs:
                    changed += 1
            manifest, empty_directories = directory_reference(repo, is_root=True, legacy=legacy)
            digest.update(b"directory-manifest-v1\0"); digest.update(manifest)
            digest.update(empty_directories.to_bytes(8, "big"))
            assert changed == 6 and empty_directories == 1
            return {"sha256": digest.hexdigest(), "entries": changed + empty_directories}

        expected_v6 = reference(legacy=True)
        expected_v7 = reference(legacy=False)
        expected_v8 = dict(expected_v7)
        assert expected_v6["sha256"] != expected_v7["sha256"] and expected_v7["entries"] == 7
        assert MODULE._worktree_snapshot(str(repo), legacy=True) == expected_v6
        assert MODULE._worktree_snapshot(str(repo)) == expected_v7
        assert MODULE._state_worktree_snapshot({"schema_version": 6}, str(repo)) == expected_v6
        assert MODULE._state_worktree_snapshot({"schema_version": 7}, str(repo)) == expected_v7
        assert MODULE._state_worktree_snapshot({
            "schema_version": 8, "worktree_snapshot_algorithm": "semantic-v1",
        }, str(repo)) == expected_v8

    check("independent non-empty per-path reference preserves frozen v6 v7 and named v8 snapshot digests", independent_nonempty_snapshot_reference_preserves_v6_v7_and_v8)

    def current_snapshot_algorithm_is_explicit_and_v7_remains_exact() -> None:
        job, state, _sha, _envelope = current_candidate_fixture("snapshot-algorithm")
        assert state["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
        assert state["worktree_snapshot_algorithm"] == MODULE.WORKTREE_SNAPSHOT_SEMANTIC_V1
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        assert MODULE._state_worktree_snapshot(state, command["workdir"]) == MODULE._worktree_snapshot(command["workdir"])

        invalid = dict(state)
        invalid["worktree_snapshot_algorithm"] = "semantic-v2"
        try:
            MODULE.validate_state(invalid)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch worktree snapshot algorithm is invalid"
        else:
            raise AssertionError("new state accepted an unrecognized snapshot algorithm")

        v7 = dict(state)
        v7["schema_version"] = 7
        v7.pop("worktree_snapshot_algorithm")
        v7.pop("worktree_root_identity")
        assert MODULE.validate_state(v7)["schema_version"] == 7
        assert MODULE._state_worktree_snapshot(v7, command["workdir"]) == MODULE._worktree_snapshot(command["workdir"])

    check("new states persist one snapshot algorithm while v7 readback remains semantic-v1", current_snapshot_algorithm_is_explicit_and_v7_remains_exact)

    def privileged_agy_executable_is_never_safe() -> None:
        executable = root / "privileged-agy"
        executable.write_text("#!/bin/sh\nprintf '1.1.16\\n'\n", encoding="utf-8")
        executable.chmod(0o755)
        original = MODULE.MODEL_SELECTION.shutil.which
        MODULE.MODEL_SELECTION.shutil.which = lambda _name: str(executable)
        try:
            resolved, _binding = MODULE.MODEL_SELECTION.resolve_safe_executable()
            assert Path(resolved).resolve() == executable.resolve()
            baseline = executable.lstat()
            for privileged in (0o4000, 0o2000):
                # Some test filesystems drop set-ID bits on chmod.  Exercise the
                # pre-probe metadata guard directly with the exact lstat shape;
                # no executable or provider fixture is involved.
                synthetic = list(baseline)
                synthetic[0] = baseline.st_mode | privileged
                assert not MODULE.MODEL_SELECTION._safe_owner_mode(
                    os.stat_result(synthetic), directory=False,
                ), f"privileged executable mode {privileged:o} was accepted"
        finally:
            MODULE.MODEL_SELECTION.shutil.which = original

    check("safe agy executable rejects setuid and setgid metadata before any probe", privileged_agy_executable_is_never_safe)

    def regular_to_fifo_race_fails_before_task_or_dispatch_artifacts() -> None:
        """A pathname replacement must not block the pre-task selector."""
        repo = root / "fifo-race-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        bin_dir = root / "fifo-race-bin"; bin_dir.mkdir()
        fake = bin_dir / "agy"
        provider_marker = root / "fifo-race-provider"
        fake.write_text(
            "#!/bin/sh\n"
            "touch " + shlex.quote(str(provider_marker)) + "\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

        # The injected hook runs only in the selector's Python process.  It
        # replaces the already-lstat'd regular target immediately before the
        # production descriptor open, making the historical blocking FIFO race
        # deterministic without changing the production source under test.
        hook_dir = root / "fifo-race-hook"; hook_dir.mkdir()
        (hook_dir / "sitecustomize.py").write_text(
            "import os\n"
            "_target = os.environ.get('AGY_FIFO_RACE_PATH')\n"
            "_open = os.open\n"
            "_fired = False\n"
            "def _race_open(path, flags, mode=0o777, *, dir_fd=None):\n"
            "    global _fired\n"
            "    if not _fired and _target and os.path.abspath(os.fspath(path)) == _target:\n"
            "        _fired = True\n"
            "        os.unlink(_target)\n"
            "        os.mkfifo(_target, 0o700)\n"
            "    if dir_fd is None:\n"
            "        return _open(path, flags, mode)\n"
            "    return _open(path, flags, mode, dir_fd=dir_fd)\n"
            "os.open = _race_open\n",
            encoding="utf-8",
        )
        log_dir = root / "fifo-race-logs"; log_dir.mkdir(mode=0o700)
        env = dict(os.environ)
        env.update({
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "PYTHONPATH": str(hook_dir),
            "AGY_FIFO_RACE_PATH": str(fake.resolve()),
            "AGY_WORKER_MODE": "accept-edits",
            "AGY_WORKER_LOG_DIR": str(log_dir),
            "AGY_WORKER_JOB_ID": "fifo-race",
            "AGY_WORKER_MAX_ATTEMPTS": "1",
        })
        worker = ROOT / "skills/agy-worker/runtime/agy-worker.sh"
        started = time.monotonic()
        process = subprocess.Popen(
            [str(worker), "--workdir", str(repo), "--model", "gemini-3.6-flash", "--effort", "high"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(
                input=b"this task must not be consumed\n", timeout=1.5,
            )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise AssertionError("regular-to-FIFO pre-task selection blocked") from exc
        assert time.monotonic() - started < 1.5
        assert process.returncode == 8 and not stdout
        assert stderr == (
            b"model-selection: evidence-unavailable - agy executable identity is unavailable\n"
        )
        job = log_dir / "fifo-race"
        assert stat.S_ISFIFO(fake.lstat().st_mode)
        assert not provider_marker.exists()
        for name in ("task.txt", "dispatch-command.json", "state.json", "selection.json"):
            assert not (job / name).exists(), name

    check("regular-to-FIFO race fails promptly before task provider state or selection", regular_to_fifo_race_fails_before_task_or_dispatch_artifacts)

    def bound_executable_reader_is_nonblocking_typed_and_size_bounded() -> None:
        import hashlib
        import socket

        selection = MODULE.MODEL_SELECTION
        unavailable = "agy executable identity is unavailable"

        def rejected(path: Path, expected: os.stat_result) -> None:
            try:
                selection._read_bound_executable_sha256(path, expected)
            except selection.EvidenceUnavailable as exc:
                assert str(exc) == unavailable
            else:
                raise AssertionError(f"unsafe executable was accepted: {path.name}")

        regular = root / "bound-executable-regular"
        regular.write_bytes(b"#!/bin/sh\nexit 0\n")
        regular.chmod(0o700)
        seen_flags: list[int] = []
        original_open = selection.os.open

        def record_open(path, flags, mode=0o777, *, dir_fd=None):
            seen_flags.append(flags)
            if dir_fd is None:
                return original_open(path, flags, mode)
            return original_open(path, flags, mode, dir_fd=dir_fd)

        selection.os.open = record_open
        try:
            assert selection._read_bound_executable_sha256(regular, regular.lstat()) == hashlib.sha256(
                regular.read_bytes(),
            ).hexdigest()
        finally:
            selection.os.open = original_open
        assert seen_flags and seen_flags[-1] & selection.os.O_NOFOLLOW
        assert seen_flags[-1] & selection.os.O_NONBLOCK

        # agy 1.1.17 is a 177,517,056-byte regular executable on the reproduced
        # host.  It must remain descriptor-bound and fully hashed before any
        # version/help probe; this fixture stays below the bounded policy cap.
        current_large_size = 177_517_056
        assert 16 * 1024 * 1024 < current_large_size <= selection.EXECUTABLE_CONTENT_LIMIT
        current_large = root / "bound-executable-current-large"
        with current_large.open("wb") as handle:
            handle.truncate(current_large_size)
        current_large.chmod(0o700)
        expected_digest = hashlib.sha256()
        zero_chunk = b"\0" * (64 * 1024)
        for _ in range(current_large_size // len(zero_chunk)):
            expected_digest.update(zero_chunk)
        expected_digest.update(b"\0" * (current_large_size % len(zero_chunk)))
        assert selection._read_bound_executable_sha256(
            current_large, current_large.lstat(),
        ) == expected_digest.hexdigest()

        oversized = root / "bound-executable-over-limit"
        with oversized.open("wb") as handle:
            handle.truncate(selection.EXECUTABLE_CONTENT_LIMIT + 1)
        oversized.chmod(0o700)
        original_read = selection.os.read
        selection.os.read = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("oversized executable content was read"),
        )
        try:
            rejected(oversized, oversized.lstat())
        finally:
            selection.os.read = original_read

        directory = root / "bound-executable-directory"; directory.mkdir(mode=0o700)
        rejected(directory, directory.lstat())

        fifo = root / "bound-executable-fifo"; os.mkfifo(fifo, 0o700)
        rejected(fifo, fifo.lstat())

        first_socket, second_socket = socket.socketpair()
        original_open = selection.os.open
        selection.os.open = lambda *_args, **_kwargs: os.dup(first_socket.fileno())
        try:
            rejected(regular, regular.lstat())
        finally:
            selection.os.open = original_open
            first_socket.close(); second_socket.close()

        device = Path("/dev/null")
        if device.exists():
            rejected(device, device.lstat())

        original_open = selection.os.open
        selection.os.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("fixture path must stay private"),
        )
        try:
            rejected(regular, regular.lstat())
        finally:
            selection.os.open = original_open

        original_nonblock = selection.os.O_NONBLOCK
        open_called = False

        def forbidden_open(*_args, **_kwargs):
            nonlocal open_called
            open_called = True
            raise AssertionError("blocking fallback was attempted")

        selection.os.O_NONBLOCK = 0
        selection.os.open = forbidden_open
        try:
            rejected(regular, regular.lstat())
        finally:
            selection.os.open = original_open
            selection.os.O_NONBLOCK = original_nonblock
        assert not open_called

    check("bound executable reader accepts regular limits and rejects nonregular or blocking fallbacks", bound_executable_reader_is_nonblocking_typed_and_size_bounded)

    def live_candidate_actions_share_the_mutation_binder() -> None:
        job, state, sha, envelope = current_candidate_fixture("actions")
        public = MODULE.public_status(state, sha, job=job)
        actions = {item["action"] for item in public["available_actions"]}
        assert {"result", "finalize"} <= actions

        # Candidate bytes are no longer current: no terminal candidate action may
        # be advertised even though the old state flag still says available.
        envelope.write_bytes(b"{}\n"); envelope.chmod(0o600)
        stale = MODULE.public_status(state, sha, job=job)
        assert not ({"result", "continue", "finalize"} & {
            item["action"] for item in stale["available_actions"]
        })

        # A repair attempt retains candidate references privately, but while it is
        # active its only public controls are active controls, never terminal ones.
        active = dict(state)
        active.update({
            "status": "running", "controller_pid": 123, "started_epoch": time.time(),
            "attempt_origin": "conversation-continue", "attempt": 2, "cycle": 2,
            "phase": "repairing", "elapsed_seconds": 1.0, "attempt_base_elapsed": 1.0,
        })
        active_actions = {item["action"] for item in MODULE.public_status(active, sha, job=job)["available_actions"]}
        assert {"result", "continue", "finalize"}.isdisjoint(active_actions)

    check("available candidate actions rebind live artifacts and disappear during an active repair", live_candidate_actions_share_the_mutation_binder)

    def stale_candidate_projection_is_live_private_and_nonmutating() -> None:
        """A stale candidate may remain recognized, but never publicly available."""
        job, state, sha, envelope = current_candidate_fixture("stale-projection")
        state["continue_available"] = True
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        before = (job / MODULE.STATE_NAME).read_bytes()
        envelope.write_bytes(b"{}\n"); envelope.chmod(0o600)

        public = MODULE.public_status(state, sha, job=job)
        assert public["candidate_recognized"] is True
        assert public["candidate_source"] == "provider_success"
        assert public["result_available"] is False
        assert public["candidate_sha256"] is None
        assert public["continue_available"] is False
        assert public["failure_stage"] == "binding_failure"
        assert not ({"result", "continue", "finalize"} & {
            item["action"] for item in public["available_actions"]
        })
        assert (job / MODULE.STATE_NAME).read_bytes() == before

        captured = io.BytesIO()
        original_stdout = MODULE.sys.stdout

        class _Stdout:
            buffer = captured

        MODULE.sys.stdout = _Stdout()
        try:
            MODULE.print_text_status(state, sha, job=job)
        finally:
            MODULE.sys.stdout = original_stdout
        lines = captured.getvalue().decode("utf-8").splitlines()
        expected_lines = [
            "Provider attempt: succeeded; reason: none; failure stage: binding_failure; bound result available: no; driver disposition: unreviewed.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: fresh-attempt restart: "$PIPELINE/agy-worker.sh" restart --job-id ' + state["job_id"] + " --approve-state-sha "
            + sha + " --format text.",
        ]
        assert lines == expected_lines

    check("stale recognized candidates project bound availability and private binding failure", stale_candidate_projection_is_live_private_and_nonmutating)

    def action_predicates_keep_runtime_and_headroom_in_sync() -> None:
        job, state, sha, _envelope = current_candidate_fixture("predicates")
        stale_resume = dict(state)
        stale_resume.update({
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "status": "failed", "conversation_id": None, "resume_available": True,
        })
        assert "resume" not in {
            item["action"] for item in MODULE.public_status(stale_resume, sha, job=job)["available_actions"]
        }
        near_deadline = dict(state)
        now = time.time()
        near_deadline.update({
            "status": "running", "controller_pid": 123, "started_epoch": now,
            "attempt_base_elapsed": 9.5, "elapsed_seconds": 9.5,
            "hard_seconds": 10.0, "max_seconds": 20.0,
            "progress_count": 1, "last_progress_epoch": now,
        })
        assert not MODULE._extend_is_eligible(near_deadline, now)
        near_deadline["hard_seconds"] = 10.0
        near_deadline["max_seconds"] = 10.5
        near_deadline["attempt_base_elapsed"] = 1.0
        near_deadline["elapsed_seconds"] = 1.0
        assert not MODULE._extend_is_eligible(near_deadline, now)

        eligible_extend = dict(near_deadline)
        eligible_extend.update({
            "hard_seconds": 10.0, "max_seconds": 20.0,
            "attempt_base_elapsed": 1.0, "elapsed_seconds": 1.0,
        })
        assert MODULE._extend_is_eligible(eligible_extend, now)
        extend = next(
            item for item in MODULE._available_actions(eligible_extend, sha, now)
            if item["action"] == "extend"
        )
        assert extend == {
            "action": "extend",
            "requires": ["--by caller-provided DURATION"],
            "guidance": "choose a positive duration that remains within the current maximum runtime",
        }

    check("resume and extend action predicates reject missing conversation and sub-second headroom", action_predicates_keep_runtime_and_headroom_in_sync)

    def codex_can_continue_on_advisory_repair_intent() -> None:
        verification = {
            "schema_version": 2, "summary": "driver requests an advisory repair", "passed_checks": [],
            "failed_checks": [], "advisory_checks": 1, "missing_checks": 0,
            "candidate_sha256": "a" * 64, "coverage": "partial",
            "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        called: list[tuple] = []
        original_read = MODULE._verification_from_stdin
        original_spawn = MODULE.spawn
        MODULE._verification_from_stdin = lambda: verification
        MODULE.spawn = lambda *args, **kwargs: (called.append((args, kwargs)) or 17)
        try:
            assert MODULE.command_continue(root / "unused-job", "b" * 64, None) == 17
        finally:
            MODULE._verification_from_stdin = original_read
            MODULE.spawn = original_spawn
        assert called and called[0][0][1] == "conversation-continue"

    check("continue accepts a bounded Codex advisory repair intent", codex_can_continue_on_advisory_repair_intent)

    def finalization_persists_the_exact_codex_declaration() -> None:
        job, state, sha, _envelope = current_candidate_fixture("declaration")
        verification = {
            "schema_version": 2, "summary": "driver explicitly rejects this candidate", "passed_checks": ["unit"],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "complete",
            "verified_findings": 0, "unresolved_gaps": 0, "diff_review_complete": True,
        }
        completed = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "rejected"],
            input=json.dumps(verification).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
        persisted = json.loads(completed.stdout)
        assert persisted["driver_disposition"] == persisted["assurance"] == "rejected"

    check("finalize records the bounded Codex declaration without reinterpreting check counts", finalization_persists_the_exact_codex_declaration)

    def verified_finalization_requires_declared_workflow_evidence() -> None:
        """`verified` is an exact Codex declaration, but has a hard evidence floor."""
        job, state, sha, _envelope = current_candidate_fixture("verified-floor")
        insufficient = {
            "schema_version": 2, "summary": "driver incorrectly called this verified", "passed_checks": [],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": False,
        }
        before = (job / MODULE.STATE_NAME).read_bytes()
        rejected = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "verified"],
            input=json.dumps(insufficient).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert rejected.returncode == 64 and not rejected.stdout
        assert (job / MODULE.STATE_NAME).read_bytes() == before
        assert not list((job / "continue-staged").glob("final-*.json"))

        sufficient = dict(insufficient)
        sufficient.update({
            "summary": "driver verified task candidate", "passed_checks": ["unit"],
            "coverage": "complete", "unresolved_gaps": 0, "diff_review_complete": True,
        })
        accepted = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "verified"],
            input=json.dumps(sufficient).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert accepted.returncode == 0, accepted.stderr.decode("utf-8", "replace")
        assert json.loads(accepted.stdout)["driver_disposition"] == "verified"

    check("verified finalization requires workflow evidence without rewriting other Codex dispositions", verified_finalization_requires_declared_workflow_evidence)

    def recovery_actions_share_their_strict_mutation_guard() -> None:
        """A command binding drift must hide recovery before it can mutate state."""
        job, state, sha, _envelope = current_candidate_fixture("action-parity")
        command = job / MODULE.COMMAND_NAME
        command.write_bytes(b"{}\n"); command.chmod(0o600)
        public = MODULE.public_status(state, sha, job=job)
        actions = {item["action"] for item in public["available_actions"]}
        assert not ({"result", "continue", "finalize", "restart"} & actions), actions
        before = (job / MODULE.STATE_NAME).read_bytes()
        try:
            MODULE.create_state(job, "fresh-restart", resume=True, approve_sha=sha)
        except MODULE.DispatchError:
            pass
        else:
            raise AssertionError("a status-hidden restart was accepted")
        assert (job / MODULE.STATE_NAME).read_bytes() == before

        # Resume has the same launch-input requirements but no candidate.  Its
        # public action and both recovery mutations must fail identically.
        resume_job, resume_state, _resume_sha, _resume_envelope = current_candidate_fixture("resume-parity")
        resume_state.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "finished_epoch": 1.0, "conversation_id": "resume-conversation", "resume_available": True,
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "driver_disposition": "not_applicable", "phase": "attempt-failed", "assurance": "pending",
            "continue_available": False,
        })
        _raw, resume_sha = MODULE.write_atomic(resume_job, MODULE.STATE_NAME, resume_state)
        (resume_job / MODULE.COMMAND_NAME).write_bytes(b"{}\n")
        (resume_job / MODULE.COMMAND_NAME).chmod(0o600)
        resume_actions = {item["action"] for item in MODULE.public_status(resume_state, resume_sha, job=resume_job)["available_actions"]}
        assert not ({"resume", "restart"} & resume_actions), resume_actions
        before_resume = (resume_job / MODULE.STATE_NAME).read_bytes()
        for origin in ("conversation-resume", "fresh-restart"):
            try:
                MODULE.create_state(resume_job, origin, resume=True, approve_sha=resume_sha)
            except MODULE.DispatchError:
                pass
            else:
                raise AssertionError(f"a status-hidden {origin} was accepted")
            assert (resume_job / MODULE.STATE_NAME).read_bytes() == before_resume

    check("available recovery actions use the same bound command/schema/root/selection guard as mutation", recovery_actions_share_their_strict_mutation_guard)

    def recovery_guard_rejects_stage_schema_and_root_drift_before_state_writes() -> None:
        for kind in ("stage", "provider-schema", "root"):
            job, state, _sha, _envelope = current_candidate_fixture(
                f"recovery-{kind}", staged=kind == "stage",
            )
            state.update({
                "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
                "finished_epoch": 1.0, "conversation_id": "recovery-conversation", "resume_available": True,
                "result_path": None, "result_sha256": None, "result_identity": None,
                "candidate_recognized": False, "candidate_source": "none", "result_available": False,
                "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
                "driver_disposition": "not_applicable", "phase": "attempt-failed", "assurance": "pending",
                "continue_available": False,
            })
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            if kind == "stage":
                Path(command["stage_file"]).write_text("tampered prompt", encoding="utf-8")
                Path(command["stage_file"]).chmod(0o600)
            elif kind == "provider-schema":
                provider_path = Path(command["argv"][command["argv"].index("--json-schema") + 1])
                provider_path.write_bytes(provider_path.read_bytes() + b"\n")
            else:
                original = Path(command["workdir"])
                moved = original.with_name(original.name + "-moved")
                original.rename(moved)
                original.symlink_to(moved, target_is_directory=True)
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            actions = {item["action"] for item in MODULE.public_status(state, sha, job=job)["available_actions"]}
            assert not ({"resume", "restart"} & actions), (kind, actions)
            before = (job / MODULE.STATE_NAME).read_bytes()
            for origin in ("conversation-resume", "fresh-restart"):
                try:
                    MODULE.create_state(job, origin, resume=True, approve_sha=sha)
                except MODULE.DispatchError:
                    pass
                else:
                    raise AssertionError(f"{kind} drift accepted {origin}")
                assert (job / MODULE.STATE_NAME).read_bytes() == before

    check("stage provider-schema and root drift have status-command recovery parity", recovery_guard_rejects_stage_schema_and_root_drift_before_state_writes)

    def replaced_same_path_root_hides_recovery_and_cannot_rebind() -> None:
        """Recovery must not turn a different repository at the same path into authority."""
        cases = (("task", ("conversation-resume", "fresh-restart")),
                 ("explore", ("conversation-resume", "fresh-restart")),
                 ("legacy", ("conversation-resume",)))
        for workflow, origins in cases:
            job, state, _sha, _envelope = current_candidate_fixture(
                f"same-path-{workflow}", workflow=workflow,
            )
            state.update({
                "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
                "finished_epoch": 1.0, "conversation_id": "same-path-conversation",
                "resume_available": True, "result_path": None, "result_sha256": None,
                "result_identity": None, "candidate_recognized": False,
                "candidate_source": "none", "result_available": False,
                "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
                "driver_disposition": "not_applicable", "phase": "attempt-failed",
                "assurance": "pending", "continue_available": False,
            })
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            original = Path(command["workdir"])
            moved = original.with_name(original.name + "-original")
            original.rename(moved)
            original.mkdir()
            subprocess.run(["git", "init", "-q", str(original)], check=True)
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            public = MODULE.public_status(state, sha, job=job)
            assert not ({"resume", "restart"} & {item["action"] for item in public["available_actions"]})
            before = (job / MODULE.STATE_NAME).read_bytes()
            for origin in origins:
                try:
                    MODULE.create_state(job, origin, resume=True, approve_sha=sha)
                except MODULE.DispatchError:
                    pass
                else:
                    raise AssertionError(f"{workflow} replacement authorized {origin}")
                assert (job / MODULE.STATE_NAME).read_bytes() == before
            assert not list(job.glob("stream.ndjson"))

    check("task explore and legacy same-path repository replacement hides recovery before mutation", replaced_same_path_root_hides_recovery_and_cannot_rebind)

    def selection_drift_hides_candidate_actions_before_any_mutation() -> None:
        job, state, sha, _envelope = current_candidate_fixture("selection-parity", selection=True)
        state["continue_available"] = True
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        selection_path = Path(json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))["selection_path"])
        original_selection = selection_path.read_text(encoding="utf-8")
        selection_path.write_bytes(b"{}\n"); selection_path.chmod(0o600)
        public = MODULE.public_status(state, sha, job=job)
        actions = {item["action"] for item in public["available_actions"]}
        assert not ({"result", "continue", "finalize", "restart"} & actions), actions
        assert public["resume_available"] is False
        captured = io.BytesIO()
        original_stdout = MODULE.sys.stdout

        class _Stdout:
            buffer = captured

        MODULE.sys.stdout = _Stdout()
        try:
            MODULE.print_text_status(state, sha, job=job)
        finally:
            MODULE.sys.stdout = original_stdout
        lines = captured.getvalue().decode("utf-8").splitlines()
        assert len(lines) == 3
        assert lines[2] == (
            "Next safe action: create a fresh job using the unchanged caller selection after reviewing "
            "the current sanitized agy interface evidence. No same-job action is available."
        )
        for private in (str(job), str(selection_path), original_selection, "gemini-3.6-flash"):
            assert private not in "\n".join(lines)

        resume_only = dict(state)
        resume_only.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "conversation_id": "selection-drift-conversation", "resume_available": True,
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "driver_disposition": "not_applicable", "phase": "attempt-failed",
            "continue_available": False,
        })
        resumed_public = MODULE.public_status(resume_only, sha, job=job)
        assert "resume" not in {item["action"] for item in resumed_public["available_actions"]}
        assert resumed_public["resume_available"] is False
        verification = {
            "schema_version": 2, "summary": "driver found a bounded defect", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        before = (job / MODULE.STATE_NAME).read_bytes()
        commands = (
            ([sys.executable, str(SOURCE), "result", "--job-dir", str(job)], MODULE.EXIT_BY_REASON["status_unavailable"]),
            ([sys.executable, str(SOURCE), "continue", "--job-dir", str(job), "--approve-state-sha", sha], 64),
            ([sys.executable, str(SOURCE), "finalize", "--job-dir", str(job), "--approve-state-sha", sha,
              "--assurance", "partially_verified"], 64),
        )
        for command, expected_exit in commands:
            completed = subprocess.run(
                command, input=json.dumps(verification).encode("utf-8") if command[2] != "result" else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert completed.returncode == expected_exit and not completed.stdout, (command, completed.stderr)
            assert (job / MODULE.STATE_NAME).read_bytes() == before
        assert not list(job.glob("stream.ndjson"))

    check("selection tamper hides result continue and finalize and leaves candidate state untouched", selection_drift_hides_candidate_actions_before_any_mutation)

    def text_status_has_three_sanitized_lines_and_lists_exact_actions() -> None:
        job, state, sha, _envelope = current_candidate_fixture("text")

        def render(value: dict, *, bound_job: Path | None = None) -> list[str]:
            captured = io.BytesIO()
            original_stdout = MODULE.sys.stdout

            class _Stdout:
                buffer = captured

            MODULE.sys.stdout = _Stdout()
            try:
                MODULE.print_text_status(value, sha, job=bound_job)
            finally:
                MODULE.sys.stdout = original_stdout
            lines = captured.getvalue().decode("utf-8").splitlines()
            assert len(lines) == 3
            for sentinel in ("conversation-secret", "worker prose secret", str(job), "candidate-text"):
                assert sentinel not in "\n".join(lines)
            return lines

        state.update({
            "conversation_id": "conversation-secret",
            "check_summary": "worker prose secret",
        })
        current = render(state, bound_job=job)
        assert current == [
            "Provider attempt: succeeded; reason: none; failure stage: none; bound result available: yes; driver disposition: unreviewed.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: retrieve current bound result JSON with "$PIPELINE/agy-worker.sh" result --job-id current-text --format json; review it and run driver checks, construct Verification v2, then Codex—not the controller—may choose the eligible finalize action.',
        ]

        cancelled = dict(state)
        cancelled.update({
            "status": "cancelled", "reason": "provider_terminal_cancelled", "exit_code": 22,
            "candidate_source": "provider_cancelled", "continue_available": False,
        })
        cancelled_public = MODULE.public_status(cancelled, sha, job=job)
        assert "continue" not in {item["action"] for item in cancelled_public["available_actions"]}
        assert {item["action"] for item in cancelled_public["available_actions"]} == {"result", "verification-copy", "restart", "finalize"}
        cancelled_lines = render(cancelled, bound_job=job)
        assert cancelled_lines[2] == (
            'Next safe action: retrieve current bound result JSON with "$PIPELINE/agy-worker.sh" result --job-id current-text --format json; '
            "review it and run driver checks, construct Verification v2, then Codex—not the controller—may choose the eligible finalize action. "
            'Available fresh restart command: "$PIPELINE/agy-worker.sh" restart --job-id current-text --approve-state-sha ' + sha + " --format text."
        )
        assert "continue" not in cancelled_lines[2]

        for disposition in ("verified", "partially_verified", "rejected", "blocked"):
            finalized = dict(state)
            finalized.update({
                "driver_disposition": disposition,
                "assurance": disposition,
                "phase": "blocked" if disposition == "blocked" else "completed",
                "continue_available": False,
            })
            finalized_public = MODULE.public_status(finalized, sha, job=job)
            assert {item["action"] for item in finalized_public["available_actions"]} == {"result", "restart"}
            assert render(finalized, bound_job=job) == [
                f"Provider attempt: succeeded; reason: none; failure stage: none; bound result available: yes; driver disposition: {disposition}.",
                "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
                'Next safe action: optional finalized result JSON readback with "$PIPELINE/agy-worker.sh" result --job-id current-text --format json; driver disposition is already recorded; do not construct Verification v2, continue, or finalize. Available fresh restart command: "$PIPELINE/agy-worker.sh" restart --job-id current-text --approve-state-sha ' + sha + " --format text.",
            ]

        finalized_without_restart = dict(finalized)
        finalized_without_restart["attempt"] = finalized_without_restart["max_cycles"]
        no_restart_public = MODULE.public_status(finalized_without_restart, sha, job=job)
        assert {item["action"] for item in no_restart_public["available_actions"]} == {"result"}
        no_restart_lines = render(finalized_without_restart, bound_job=job)
        assert no_restart_lines[2] == (
            'Next safe action: optional finalized result JSON readback with "$PIPELINE/agy-worker.sh" result --job-id current-text --format json; '
            "driver disposition is already recorded; do not construct Verification v2, continue, or finalize."
        )
        assert "restart" not in no_restart_lines[2]

        active = dict(state)
        active.update({
            "status": "running", "controller_pid": 123, "started_epoch": time.time(),
            "finished_epoch": None, "exit_code": None,
        })
        active_public = MODULE.public_status(active, sha, job=job)
        assert_symbolic_action_commands(active_public["available_actions"], {"wait", "cancel"})
        assert render(active, bound_job=job) == [
            "Provider attempt: running; reason: none; failure stage: none; bound result available: no; driver disposition: unreviewed.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: "$PIPELINE/agy-worker.sh" wait --job-id current-text --after-state-sha ' + sha + " --format text.",
        ]

        resumable = dict(state)
        resumable.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "driver_disposition": "not_applicable", "phase": "attempt-failed",
            "resume_available": True, "continue_available": False,
        })
        resumable_public = MODULE.public_status(resumable, sha)
        assert_symbolic_action_commands(resumable_public["available_actions"], {"resume", "restart"})
        assert resumable_public["next_action"] == "none"
        assert resumable_public["next_action_command"] is None
        assert render(resumable) == [
            "Provider attempt: failed; reason: agy_failed_unclassified; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe actions: exact-conversation resume: "$PIPELINE/agy-worker.sh" resume --job-id current-text --approve-state-sha ' + sha
            + ' --format text; fresh-attempt restart: "$PIPELINE/agy-worker.sh" restart --job-id current-text --approve-state-sha '
            + sha + " --format text.",
        ]

        restart_only = dict(resumable)
        restart_only.update({"conversation_id": None, "resume_available": False})
        restart_public = MODULE.public_status(restart_only, sha)
        assert_symbolic_action_commands(restart_public["available_actions"], {"restart"})
        assert render(restart_only) == [
            "Provider attempt: failed; reason: agy_failed_unclassified; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: fresh-attempt restart: "$PIPELINE/agy-worker.sh" restart --job-id current-text --approve-state-sha ' + sha + " --format text.",
        ]

        exhausted = dict(restart_only)
        exhausted.update({"attempt": exhausted["max_cycles"], "cycle": exhausted["max_cycles"]})
        exhausted_public = MODULE.public_status(exhausted, sha)
        assert exhausted_public["available_actions"] == []
        assert exhausted_public["next_action"] == "none"
        assert exhausted_public["next_action_command"] is None
        assert render(exhausted) == [
            "Provider attempt: failed; reason: agy_failed_unclassified; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 2/2.",
            "Next safe action: none; the current attempt budget is exhausted.",
        ]

        runtime_exhausted = dict(restart_only)
        runtime_exhausted.update({
            "reason": "hard_deadline_exceeded", "limit_kind": "max-runtime",
            "elapsed_seconds": runtime_exhausted["max_seconds"],
        })
        runtime_public = MODULE.public_status(runtime_exhausted, sha)
        assert runtime_public["available_actions"] == []
        assert runtime_public["next_action"] == "none"
        assert runtime_public["next_action_command"] is None
        assert render(runtime_exhausted) == [
            "Provider attempt: failed; reason: hard_deadline_exceeded; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            "Next safe action: none; the current runtime budget is exhausted.",
        ]

        no_action = dict(restart_only)
        no_action.update({"status": "orphaned", "reason": "status_unavailable", "exit_code": 23})
        assert render(no_action) == [
            "Provider attempt: orphaned; reason: status_unavailable; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            "Next safe action: none.",
        ]

    check("text status distinguishes unreviewed, finalized, and non-result sanitized snapshots", text_status_has_three_sanitized_lines_and_lists_exact_actions)

    def public_candidate_digest_and_result_text_are_bound_and_non_directive() -> None:
        job, state, sha, envelope = current_candidate_fixture("public-candidate")
        public = MODULE.public_status(state, sha, job=job)
        assert public["result_available"] is True
        assert public["candidate_sha256"] == state["result_sha256"]
        assert public["candidate_sha256"] == hashlib.sha256(envelope.read_bytes()).hexdigest()
        assert MODULE.SHA_RE.fullmatch(public["candidate_sha256"]) is not None
        assert set(("candidate_sha256", "result_available", "next_action", "next_action_command", "has_prior_candidate", "phase")) <= set(public)
        assert type(public["result_available"]) is bool
        assert type(public["has_prior_candidate"]) is bool
        assert isinstance(public["next_action"], str)
        assert public["next_action_command"] is None or isinstance(public["next_action_command"], str)
        assert isinstance(public["controller_phase"], str)
        assert isinstance(public["legacy_result_provenance"], str)
        assert public["phase"] is None or isinstance(public["phase"], str)

        # Current public candidate identity is only the bound envelope digest.
        # Path text, worker prose, and legacy historical data are distractors.
        path_digest = hashlib.sha256(str(envelope).encode("utf-8")).hexdigest()
        prose_digest = hashlib.sha256(b"candidate-public-candidate").hexdigest()
        legacy_digest = "f" * 64
        state["last_success_path"] = "/private/legacy-result.json"
        state["last_success_sha256"] = legacy_digest
        state["last_success_identity"] = [1, 2, 3, 4, 5]
        assert public["candidate_sha256"] not in {path_digest, prose_digest, legacy_digest}

        captured = io.BytesIO()
        original_stdout = MODULE.sys.stdout

        class _Stdout:
            buffer = captured

        MODULE.sys.stdout = _Stdout()
        try:
            MODULE.print_text_status(state, sha, job=job)
        finally:
            MODULE.sys.stdout = original_stdout
        lines = captured.getvalue().decode("utf-8").splitlines()
        assert len(lines) == 3
        assert lines[2] == (
            'Next safe action: retrieve current bound result JSON with "$PIPELINE/agy-worker.sh" result --job-id '
            + state["job_id"]
            + " --format json; review it and run driver checks, construct Verification v2, then Codex—not the controller—may choose the eligible finalize action."
        )
        for sentinel in (str(job), str(envelope), "/private/legacy-result.json", "candidate-public-candidate"):
            assert sentinel not in "\n".join(lines)

        before = (job / MODULE.STATE_NAME).read_bytes()

        envelope.write_bytes(b"{}\n"); envelope.chmod(0o600)
        stale = MODULE.public_status(state, sha, job=job)
        assert stale["result_available"] is False
        assert stale["candidate_sha256"] is None
        assert type(stale["result_available"]) is bool
        assert not ({"result", "continue", "finalize"} & {
            item["action"] for item in stale["available_actions"]
        })
        assert (job / MODULE.STATE_NAME).read_bytes() == before

    check("public candidate digest follows only the bound result and result text leaves disposition to Codex", public_candidate_digest_and_result_text_are_bound_and_non_directive)

    def verification_v2_example_and_lifecycle_help_are_copyable() -> None:
        job, state, sha, envelope = current_candidate_fixture(
            "verification-example", wrapper_addressable=True,
        )
        public = MODULE.public_status(state, sha, job=job)
        assert public["result_available"] is True

        help_text = subprocess.run(
            ["bash", str(ROOT / "skills/agy-worker/runtime/agy-worker.sh"), "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).stderr.decode("utf-8", "replace")
        assert "agy-worker.sh resume --job-id JOB --approve-state-sha SHA [--approve-migration-sha SHA] [--format json|text]" in help_text
        assert "agy-worker.sh restart --job-id JOB --approve-state-sha SHA [--approve-migration-sha SHA] [--format json|text]" in help_text
        assert "agy-worker.sh resume --job-id JOB --approve-state-sha STATE_SHA" in help_text
        assert "agy-worker.sh restart --job-id JOB --approve-state-sha STATE_SHA" in help_text
        assert "--compatibility-disposition proceed --approve-help-sha SHA256" in help_text
        assert (
            "agy-worker.sh finalize --job-id JOB --approve-state-sha SHA "
            "[--approve-migration-sha SHA] \\\n"
        ) in help_text
        assert "[--approve-migration-sha SHA] \\\\\\n" not in help_text

        def preparation_block(documentation: Path) -> str:
            text = documentation.read_text(encoding="utf-8")
            marker = ': "${PIPELINE:?set PIPELINE to the resolved skill runtime}"'
            marker_at = text.index(marker)
            start = text.rfind("```bash", 0, marker_at) + len("```bash\n")
            end = text.index("\n```", marker_at)
            block = text[start:end]
            final = '\n"$PIPELINE/agy-worker.sh" continue'
            assert final in block
            return block.split(final, 1)[0]

        def execute_preparation(documentation: Path, expected_state: dict) -> dict:
            state_dir = root / f"verification-example-{documentation.stem}"; state_dir.mkdir()
            completed = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", preparation_block(documentation)],
                env={
                    **os.environ, "PIPELINE": str(ROOT / "skills/agy-worker/runtime"),
                    "JOB_ID": expected_state["job_id"], "STATE_DIR": str(state_dir),
                    "AGY_WORKER_LOG_DIR": str(job.parent),
                },
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
            example = json.loads((state_dir / "verification-v2.json").read_text(encoding="utf-8"))
            assert MODULE._validate_verification(example) == example
            MODULE._require_current_candidate_verification(example, expected_state)
            assert example["candidate_sha256"] == hashlib.sha256(envelope.read_bytes()).hexdigest()
            return example

        for documentation in (ROOT / "README.md", ROOT / "skills/agy-worker/SKILL.md"):
            text = documentation.read_text(encoding="utf-8")
            assert "CANDIDATE_SHA" in text and "STATE_AND_CANDIDATE" in text
            assert 'candidate = status.get("candidate_sha256")' in text and "--approve-state-sha" in text
            assert "_validate_verification" in text
            example = execute_preparation(documentation, state)
            for invalid in ("A" * 64, "a" * 63, "a" * 65, "a" * 63 + "g"):
                mutated = dict(example); mutated["candidate_sha256"] = invalid
                try:
                    MODULE._validate_verification(mutated)
                except MODULE.DispatchError:
                    pass
                else:
                    raise AssertionError("Verification v2 accepted a non-lowercase-64hex candidate SHA")

        null_job, null_state, _null_sha, _null_envelope = current_candidate_fixture(
            "verification-null", wrapper_addressable=True,
        )
        null_state.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "driver_disposition": "not_applicable", "phase": "attempt-failed",
            "resume_available": False, "continue_available": False,
        })
        _raw, null_sha = MODULE.write_atomic(null_job, MODULE.STATE_NAME, null_state)
        null_public = MODULE.public_status(null_state, null_sha, job=null_job)
        assert null_public["result_available"] is False and null_public["candidate_sha256"] is None
        state_dir = root / "verification-example-null"; state_dir.mkdir()
        null_run = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", preparation_block(ROOT / "README.md")],
            env={
                **os.environ, "PIPELINE": str(ROOT / "skills/agy-worker/runtime"),
                "JOB_ID": null_state["job_id"], "STATE_DIR": str(state_dir),
                "AGY_WORKER_LOG_DIR": str(null_job.parent),
            },
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert null_run.returncode != 0
        assert b"status has no current bound candidate" in null_run.stderr
        assert not (state_dir / "verification-v2.json").exists()

    check("published Verification v2 examples execute from one public snapshot and bind the current candidate", verification_v2_example_and_lifecycle_help_are_copyable)

expected_checks = 1 if FOCUSED_CHECK is not None else EXPECTED_CHECKS
if CHECKS_RUN != expected_checks:
    raise AssertionError(
        f"remediation controller inventory drifted: expected {expected_checks}, ran {CHECKS_RUN}"
    )
print(f"PASS: remediation controller focused checks ({CHECKS_RUN} cases)")
