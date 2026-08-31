#!/usr/bin/env python3
"""Focused offline positive, negative, and adversarial tests for workflow.sh facade."""

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
import time
from typing import Any, Callable

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "skills" / "agy-worker" / "runtime"
SCRIPT = RUNTIME / "scripts" / "workflow.py"
SCHEMA_PATH = RUNTIME / "schemas" / "workflow-state.schema.json"
SUBJECT_MODES = {
    ROOT / "workflow.sh": 0o755,
    RUNTIME / "workflow.sh": 0o755,
    SCRIPT: 0o755,
    SCHEMA_PATH: 0o644,
}
SUBJECT_MODES_BEFORE_IMPORT = {
    path: stat.S_IMODE(path.stat().st_mode) for path in SUBJECT_MODES
}

sys.path.insert(0, str(RUNTIME / "scripts"))
import candidate_state as CANDIDATE
import agy_dispatch_worktree as DISPATCH_WT
import workflow as WORKFLOW_MODULE

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


def run_cmd(
    *argv: str,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    env: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    full_env = dict(os.environ)
    if env:
        for name, value in env.items():
            if value is None:
                full_env.pop(name, None)
            else:
                full_env[name] = value
    return subprocess.run(
        argv,
        input=input_bytes,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=full_env,
    )


def run_workflow(
    *argv: str,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    env: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return run_cmd(
        sys.executable, "-I", "-S", "-B", str(SCRIPT), *argv,
        cwd=cwd, input_bytes=input_bytes, env=env
    )


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.decode("utf-8").strip()


def test_import_does_not_mutate_subject_modes() -> bool:
    after_import = {
        path: stat.S_IMODE(path.stat().st_mode) for path in SUBJECT_MODES
    }
    assert SUBJECT_MODES_BEFORE_IMPORT == after_import == SUBJECT_MODES
    return True


check("import does not mutate or self-heal workflow subject modes", test_import_does_not_mutate_subject_modes)


class RepoFixture:
    def __init__(self, name: str) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix=f"agy-wf-{name}-")).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir(mode=0o700)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Workflow Tester")
        git(self.repo, "config", "user.email", "workflow@example.com")

        initial_file = self.repo / "README.md"
        initial_file.write_text("# Initial Repo\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-q", "-m", "Initial commit")
        self.base = git(self.repo, "rev-parse", "HEAD")

        self.worktree = self.tmp / "wt"
        self.branch = f"agy/{name}-branch"
        git(self.repo, "worktree", "add", "-q", "-b", self.branch, str(self.worktree), self.base)

        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir(mode=0o700)
        self.state_file = self.state_dir / "workflow-state.json"
        self.receipt_file = self.state_dir / "receipt.json"
        self.envelope_file = self.state_dir / "envelope.json"
        self.job_id = f"job-{name}-001"

    def write_envelope(self, *, path: str = "README.md", content: str = "Updated README\n") -> None:
        (self.worktree / path).write_text(content, encoding="utf-8")
        envelope = {
            "status": "completed",
            "summary": "Updated content for testing",
            "files_changed": [{"path": path, "change": "modified"}],
            "commands_run": [],
            "tests_run": [],
            "risks": [],
            "open_questions": [],
            "confidence": 1.0,
            "requires_human": False,
        }
        self.envelope_file.write_bytes(json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        self.envelope_file.chmod(0o600)

    def clean(self) -> None:
        try:
            git(self.repo, "worktree", "remove", "--force", str(self.worktree))
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ============================================================================
# 1. run command positive, negative, and adversarial tests
# ============================================================================

def test_run_missing_args() -> bool:
    f = RepoFixture("missing-args")
    try:
        res = run_workflow("run")
        assert res.returncode != 0
        res = run_workflow("run", "--state", str(f.state_file))
        assert res.returncode != 0
        return True
    finally:
        f.clean()

check("run rejects missing required arguments", test_run_missing_args)


def test_run_path_boundary_enforcement() -> bool:
    f = RepoFixture("boundary")
    try:
        # State inside repo
        bad_state = f.repo / "state.json"
        res = run_workflow(
            "run", "--state", str(bad_state), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id, "--preview"
        )
        assert res.returncode != 0
        assert b"state must be outside" in res.stderr

        # State parent not 0700
        f.state_dir.chmod(0o755)
        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id, "--preview"
        )
        assert res.returncode != 0
        assert b"mode-0700" in res.stderr
        f.state_dir.chmod(0o700)

        # Invalid base commit
        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", "0" * 40, "--job-id", f.job_id, "--preview"
        )
        assert res.returncode != 0
        assert b"base commit does not exist" in res.stderr

        # Worktree not registered
        unregistered = f.tmp / "unreg"
        unregistered.mkdir(mode=0o700)
        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(unregistered), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id, "--preview"
        )
        assert res.returncode != 0
        assert b"not registered" in res.stderr

        return True
    finally:
        f.clean()

check("run enforces immutable base and worktree path boundaries", test_run_path_boundary_enforcement)


def test_run_preview_and_approval_enforcement() -> bool:
    f = RepoFixture("preview-approval")
    try:
        # 1. Preview flag outputs preview canonical JSON and exits 0
        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id, "--preview"
        )
        assert res.returncode == 0
        direct = run_cmd(
            str(RUNTIME / "agy-worker.sh"),
            "transmission-preview", "--workdir", str(f.worktree),
        )
        assert direct.returncode == 0
        assert res.stdout == direct.stdout
        preview = json.loads(res.stdout.decode("utf-8"))
        assert "kind" not in preview
        assert preview["manifest"]["kind"] == "agy-worker-readable-path-manifest"
        manifest_sha = preview["manifest_sha256"]
        assert len(manifest_sha) == 64

        # 2. Invoking run without --approve-preview-sha exits 20 with preview info
        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id
        )
        assert res.returncode == 20
        assert b"approval required" in res.stderr

        # 3. Invoking run with mismatched --approve-preview-sha fails closed
        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id,
            "--approve-preview-sha", "a" * 64
        )
        assert res.returncode != 0
        assert b"stale or mismatched" in res.stderr

        # 4. Invoking run with exact --approve-preview-sha creates valid Workflow State v1
        # Use fake mock for agy to avoid real provider dispatch
        fake_bin = f.tmp / "bin"
        fake_bin.mkdir(mode=0o700)
        fake_agy = fake_bin / "agy"
        fake_agy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_agy.chmod(0o755)

        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id,
            "--approve-preview-sha", manifest_sha,
            "--task", "Test prompt",
            env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
        )
        assert f.state_file.exists()
        state_data = json.loads(f.state_file.read_bytes())
        assert state_data["schema_version"] == 1
        assert state_data["kind"] == "agy-worker-workflow-state"
        assert state_data["job_id"] == f.job_id
        assert state_data["base"] == f.base
        assert state_data["branch"] == f.branch
        assert state_data["preview_manifest_sha256"] == manifest_sha
        assert state_data["dispatch_job_dir"] is not None
        assert "last_result" not in state_data
        assert "final_assurance" not in state_data
        assert "final_disposition" not in state_data
        assert stat.S_IMODE(f.state_file.stat().st_mode) == 0o600

        # Validate against schema
        schema = json.loads(SCHEMA_PATH.read_bytes())
        assert state_data["schema_version"] == schema["properties"]["schema_version"]["enum"][0]
        assert state_data["kind"] == schema["properties"]["kind"]["enum"][0]

        return True
    finally:
        f.clean()

check("run generates preview, rejects unapproved/stale preview, and binds approved preview", test_run_preview_and_approval_enforcement)


def test_run_drift_rejection() -> bool:
    f = RepoFixture("drift")
    try:
        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id, "--preview"
        )
        assert res.returncode == 0
        manifest_sha = json.loads(res.stdout.decode("utf-8"))["manifest_sha256"]

        # Modify worktree after preview
        (f.worktree / "untracked.txt").write_text("drift", encoding="utf-8")

        # Approval with old manifest sha now fails closed
        res = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id,
            "--approve-preview-sha", manifest_sha
        )
        assert res.returncode != 0
        assert b"stale or mismatched" in res.stderr
        return True
    finally:
        f.clean()

check("run rejects worktree drift after preview generation", test_run_drift_rejection)


def test_run_pre_dispatch_failure_rolls_back_exact_state() -> bool:
    f = RepoFixture("predispatch-rollback")
    try:
        preview = run_workflow(
            "run", "--state", str(f.state_file), "--repo", str(f.repo),
            "--worktree", str(f.worktree), "--branch", f.branch,
            "--base", f.base, "--job-id", f.job_id, "--preview"
        )
        assert preview.returncode == 0
        manifest_sha = json.loads(preview.stdout.decode("utf-8"))["manifest_sha256"]
        dispatch_dir = f.state_dir / "logs" / f.job_id

        def rejected_preflight() -> subprocess.CompletedProcess[bytes]:
            return run_workflow(
                "run", "--state", str(f.state_file), "--repo", str(f.repo),
                "--worktree", str(f.worktree), "--branch", f.branch,
                "--base", f.base, "--job-id", f.job_id,
                "--approve-preview-sha", manifest_sha,
                "--provider-env", "BASH_ENV", "--task", "bounded task",
            )

        first = rejected_preflight()
        assert first.returncode == 64
        assert b"unsafe --provider-env name" in first.stderr
        assert not f.state_file.exists()
        assert not dispatch_dir.exists()

        # The identical provider-free preflight remains retryable instead of
        # failing on a stranded facade state.
        retry = rejected_preflight()
        assert retry.returncode == 64
        assert b"unsafe --provider-env name" in retry.stderr
        assert not f.state_file.exists()
        assert not dispatch_dir.exists()

        protected_state = {
            "schema_version": 1,
            "kind": "agy-worker-workflow-state",
            "job_id": f.job_id,
            "repo_path": str(f.repo),
            "repo_identity": WORKFLOW_MODULE.identity(f.repo.lstat()),
            "worktree_path": str(f.worktree),
            "worktree_identity": WORKFLOW_MODULE.identity(f.worktree.lstat()),
            "branch": f.branch,
            "branch_ref": f"refs/heads/{f.branch}",
            "base": f.base,
            "preview_manifest_sha256": manifest_sha,
            "dispatch_job_dir": str(dispatch_dir),
            "job_state_path": None,
            "receipt_path": None,
        }
        original = WORKFLOW_MODULE.WorkflowStateStore(f.state_file, initial=True)
        original_sha = original.create(protected_state)
        assert original.metadata is not None
        original_identity = WORKFLOW_MODULE.identity(original.metadata)
        original.close()

        replacement = dict(protected_state)
        replacement["preview_manifest_sha256"] = "f" * 64
        replacement_raw = WORKFLOW_MODULE.canonical_json(replacement) + b"\n"
        f.state_file.write_bytes(replacement_raw)
        f.state_file.chmod(0o600)
        guarded = WORKFLOW_MODULE.WorkflowStateStore(f.state_file, initial=False)
        try:
            try:
                guarded.discard_exact(original_sha, original_identity)
            except WORKFLOW_MODULE.WorkflowError as exc:
                assert "changed before pre-dispatch rollback" in str(exc)
            else:
                raise AssertionError("replaced workflow state was removed")
        finally:
            guarded.close()
        assert f.state_file.read_bytes() == replacement_raw
        return True
    finally:
        f.clean()


check(
    "run rolls back only its exact state after provider-free pre-dispatch rejection",
    test_run_pre_dispatch_failure_rolls_back_exact_state,
)


def _derived_files(state_home: Path, job_id: str) -> tuple[Path, Path, Path]:
    matches = list(
        state_home.glob(f"agy-worker/workflows/*/{job_id}/workflow.json")
    )
    assert len(matches) == 1
    workflow_state = matches[0]
    return workflow_state, workflow_state.with_name("job.json"), workflow_state.with_name("worktree")


def test_ordinary_run_owns_private_initialization_and_reuses_preview() -> bool:
    f = RepoFixture("ordinary-owned")
    try:
        state_home = f.tmp / "xdg-state"
        state_home.mkdir(mode=0o700)
        job_id = "ordinary-owned-job"
        env = {"XDG_STATE_HOME": str(state_home)}
        # The caller checkout may be dirty; the derived worktree remains isolated
        # at the immutable HEAD selected once for lifecycle initialization.
        (f.repo / "README.md").write_text("dirty caller checkout\n", encoding="utf-8")
        preview = run_workflow(
            "run", "--repo", str(f.repo), "--job-id", job_id, "--preview", env=env
        )
        assert preview.returncode == 0, preview.stderr
        manifest_sha = json.loads(preview.stdout.decode("utf-8"))["manifest_sha256"]
        workflow_state, job_state, worktree = _derived_files(state_home, job_id)
        workflow_value = json.loads(workflow_state.read_bytes())
        job_value = json.loads(job_state.read_bytes())
        assert workflow_value["schema_version"] == 2
        assert workflow_value["origin"] == "workflow-facade"
        assert job_value["schema_version"] == 2
        assert job_value["origin"] == "workflow-facade"
        assert workflow_value["base"] == f.base == job_value["base"]
        assert worktree.joinpath("README.md").read_text(encoding="utf-8") == "# Initial Repo\n"
        assert git(f.repo, "status", "--short") == "M README.md"
        for directory in (
            state_home / "agy-worker",
            state_home / "agy-worker" / "workflows",
            workflow_state.parent.parent,
            workflow_state.parent,
            workflow_state.parent / "logs",
        ):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(workflow_state.stat().st_mode) == 0o600
        assert stat.S_IMODE(job_state.stat().st_mode) == 0o600

        before_workflow = workflow_state.read_bytes()
        before_job = job_state.read_bytes()
        # A later caller-checkout HEAD movement cannot silently rebind an
        # omitted --base on the approved second call.
        (f.repo / "README.md").write_text("# Initial Repo\n", encoding="utf-8")
        (f.repo / "NEXT.md").write_text("later commit\n", encoding="utf-8")
        git(f.repo, "add", "README.md", "NEXT.md")
        git(f.repo, "commit", "-q", "-m", "Later caller commit")
        assert git(f.repo, "rev-parse", "HEAD") != f.base
        approved = run_workflow(
            "run", "--repo", str(f.repo), "--job-id", job_id,
            "--approve-preview-sha", manifest_sha,
            "--provider-env", "BASH_ENV", "--task", "bounded task", env=env,
        )
        assert approved.returncode == 64
        assert b"unsafe --provider-env name" in approved.stderr
        # These resources came from the prior preview invocation, so this later
        # preflight failure must retain them for explicit recovery.
        assert workflow_state.read_bytes() == before_workflow
        assert job_state.read_bytes() == before_job
        assert worktree.is_dir()

        stale = run_workflow(
            "run", "--repo", str(f.repo), "--job-id", job_id,
            "--approve-preview-sha", "0" * 64, "--task", "bounded task", env=env,
        )
        assert stale.returncode == 20
        assert workflow_state.exists() and job_state.exists() and worktree.exists()
        return True
    finally:
        f.clean()


check(
    "ordinary run derives private lifecycle resources, isolates dirty checkout, and reuses preview bindings",
    test_ordinary_run_owns_private_initialization_and_reuses_preview,
)


def test_ordinary_run_home_fallback_and_partial_advanced_rejection() -> bool:
    f = RepoFixture("ordinary-home")
    try:
        fake_home = f.tmp / "home"
        fake_home.mkdir(mode=0o700)
        job_id = "ordinary-home-job"
        env = {"XDG_STATE_HOME": None, "HOME": str(fake_home)}
        preview = run_workflow(
            "run", "--repo", str(f.repo), "--job-id", job_id,
            "--base", f.base, "--preview", env=env,
        )
        assert preview.returncode == 0, preview.stderr
        workflow_state, job_state, worktree = _derived_files(
            fake_home / ".local" / "state", job_id
        )
        assert workflow_state.exists() and job_state.exists() and worktree.exists()
        partial = run_workflow(
            "run", "--repo", str(f.repo), "--job-id", "partial-job",
            "--state", str(f.state_file), "--preview", env=env,
        )
        assert partial.returncode == 20
        assert b"advanced mode requires" in partial.stderr
        return True
    finally:
        f.clean()


check(
    "ordinary run supports HOME state fallback and rejects mixed advanced authority",
    test_ordinary_run_home_fallback_and_partial_advanced_rejection,
)


def test_ordinary_same_invocation_predispatch_failure_rolls_back_lifecycle() -> bool:
    f = RepoFixture("ordinary-rollback")
    try:
        state_home = f.tmp / "xdg-state"
        state_home.mkdir(mode=0o700)
        job_id = "ordinary-rollback-job"
        env = {"XDG_STATE_HOME": str(state_home)}
        preview = run_workflow(
            "run", "--repo", str(f.repo), "--job-id", job_id, "--preview", env=env
        )
        assert preview.returncode == 0
        manifest_sha = json.loads(preview.stdout.decode("utf-8"))["manifest_sha256"]
        workflow_state, job_state, worktree = _derived_files(state_home, job_id)
        workflow_value = json.loads(workflow_state.read_bytes())
        job_value = json.loads(job_state.read_bytes())
        dispatch_dir = workflow_state.parent / "logs" / job_id
        lifecycle_rollback = run_cmd(
            str(RUNTIME / "job.sh"), "rollback-ready",
            "--state", str(job_state), "--approve-job", job_id,
            "--approve-state-sha", hashlib.sha256(job_state.read_bytes()).hexdigest(),
            "--repo", str(f.repo), "--worktree", str(worktree),
            "--branch", workflow_value["branch"], "--base", job_value["base"],
            "--dispatch-job-dir", str(dispatch_dir), env=env,
        )
        assert lifecycle_rollback.returncode == 0, lifecycle_rollback.stderr
        workflow_state.unlink()
        assert not job_state.exists() and not worktree.exists()

        failed = run_workflow(
            "run", "--repo", str(f.repo), "--job-id", job_id,
            "--approve-preview-sha", manifest_sha,
            "--provider-env", "BASH_ENV", "--task", "bounded task", env=env,
        )
        assert failed.returncode == 64, (failed.returncode, failed.stdout, failed.stderr)
        assert b"unsafe --provider-env name" in failed.stderr
        assert not workflow_state.exists()
        assert not job_state.exists()
        assert not worktree.exists()
        assert not dispatch_dir.exists()
        branch_probe = run_cmd(
            "/usr/bin/git", "-C", str(f.repo), "show-ref", "--verify", "--quiet",
            f"refs/heads/{workflow_value['branch']}", env=env,
        )
        assert branch_probe.returncode == 1
        return True
    finally:
        f.clean()


check(
    "ordinary run delegates same-invocation pre-dispatch rollback to lifecycle authority",
    test_ordinary_same_invocation_predispatch_failure_rolls_back_lifecycle,
)


# ============================================================================
# 2. status command positive, negative, and read-only tests
# ============================================================================

def test_status_read_only_and_sanitized() -> bool:
    f = RepoFixture("status")
    try:
        # Create initial state
        initial_state = {
            "schema_version": 1,
            "kind": "agy-worker-workflow-state",
            "job_id": f.job_id,
            "repo_path": str(f.repo),
            "repo_identity": WORKFLOW_MODULE.identity(f.repo.lstat()),
            "worktree_path": str(f.worktree),
            "worktree_identity": WORKFLOW_MODULE.identity(f.worktree.lstat()),
            "branch": f.branch,
            "branch_ref": f"refs/heads/{f.branch}",
            "base": f.base,
            "preview_manifest_sha256": "0" * 64,
            "dispatch_job_dir": None,
            "job_state_path": None,
            "receipt_path": None,
        }
        store = WORKFLOW_MODULE.WorkflowStateStore(f.state_file, initial=True)
        store.create(initial_state)
        store.close()

        state_sha_before = hashlib.sha256(f.state_file.read_bytes()).hexdigest()

        # Status in json format
        res = run_workflow("status", "--state", str(f.state_file), "--format", "json")
        assert res.returncode == 0
        status_data = json.loads(res.stdout.decode("utf-8"))
        assert status_data["kind"] == "agy-worker-workflow-status"
        assert status_data["job_id"] == f.job_id
        assert status_data["dispatch"] is None

        # Status in text format
        res_txt = run_workflow("status", "--state", str(f.state_file), "--format", "text")
        assert res_txt.returncode == 0
        lines = res_txt.stdout.decode("utf-8").strip().splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("workflow: job=")
        assert lines[1].startswith("dispatch: status=")
        assert lines[2].startswith("verification: verdict=")

        # Strictly read-only: state file SHA did not change
        state_sha_after = hashlib.sha256(f.state_file.read_bytes()).hexdigest()
        assert state_sha_before == state_sha_after

        return True
    finally:
        f.clean()

check("status is strictly read-only, emits sanitized facts, and does not infer assurance", test_status_read_only_and_sanitized)


def test_status_projects_existing_job_state_without_migration() -> bool:
    f = RepoFixture("status-job-state")
    try:
        state_home = f.tmp / "xdg-state"
        state_home.mkdir(mode=0o700)
        job_id = "status-existing-job"
        env = {"XDG_STATE_HOME": str(state_home)}
        preview = run_workflow(
            "run", "--repo", str(f.repo), "--job-id", job_id, "--preview", env=env
        )
        assert preview.returncode == 0
        workflow_state, job_state, _worktree = _derived_files(state_home, job_id)
        before = job_state.read_bytes()

        explicit = run_workflow(
            "status", "--job-state", str(job_state), "--format", "json", env=env
        )
        assert explicit.returncode == 0, explicit.stderr
        projection = json.loads(explicit.stdout.decode("utf-8"))
        assert projection["source_kind"] == "job_lifecycle"
        assert projection["phase"] == projection["controller_phase"] == "ready"
        assert projection["available_actions"] == ["status", "verify"]
        assert "Mutations remain with job.sh" in projection["advanced_recovery"]

        detected = run_workflow(
            "status", "--state", str(job_state), "--format", "json", env=env
        )
        assert detected.returncode == 0
        assert json.loads(detected.stdout.decode("utf-8"))["source_kind"] == "job_lifecycle"
        assert job_state.read_bytes() == before

        facade = run_workflow(
            "status", "--state", str(workflow_state), "--format", "json", env=env
        )
        assert facade.returncode == 0
        facade_projection = json.loads(facade.stdout.decode("utf-8"))
        assert facade_projection["source_kind"] == "workflow_facade"
        assert facade_projection["phase"] == "ready"
        return True
    finally:
        f.clean()


check(
    "status projects existing lifecycle state read-only without migrating legacy authority",
    test_status_projects_existing_job_state_without_migration,
)


def test_status_projects_dispatcher_state_and_job_id_read_only() -> bool:
    f = RepoFixture("status-dispatcher")
    try:
        copied_runtime = f.tmp / "runtime-status"
        shutil.copytree(RUNTIME, copied_runtime)
        fake_dispatch = copied_runtime / "scripts" / "agy_dispatch.py"
        fake_dispatch.write_text(
            "import hashlib,json,os,pathlib\n"
            "STATE_NAME='dispatch-state.json'\n"
            "class DispatchError(ValueError): pass\n"
            "def canonical_job(path):\n"
            " path=pathlib.Path(path)\n"
            " if not path.is_absolute() or pathlib.Path(os.path.realpath(path))!=path: raise DispatchError('bad job')\n"
            " return path\n"
            "def load_state(job):\n"
            " raw=(job/STATE_NAME).read_bytes(); value=json.loads(raw); return value,raw,hashlib.sha256(raw).hexdigest()\n"
            "def public_status(value,sha,job=None):\n"
            " return {'job_id':value['job_id'],'phase':'awaiting-verification',"
            "'controller_phase':'awaiting-verification','state_sha256':sha,"
            "'available_actions':[{'action':'result','command':'agy-worker.sh result'}]}\n",
            encoding="utf-8",
        )
        script = copied_runtime / "scripts" / "workflow.py"
        log_root = f.state_dir / "dispatcher-logs"
        log_root.mkdir(mode=0o700)
        job_id = "existing-dispatcher-job"
        job_dir = log_root / job_id
        job_dir.mkdir(mode=0o700)
        dispatch_state = job_dir / "dispatch-state.json"
        dispatch_state.write_bytes(
            json.dumps(
                {"job_id": job_id, "phase": "awaiting-verification"},
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8") + b"\n"
        )
        dispatch_state.chmod(0o600)
        before = dispatch_state.read_bytes()

        by_state = run_cmd(
            sys.executable, "-I", "-S", "-B", str(script),
            "status", "--dispatch-state", str(dispatch_state), "--format", "json",
        )
        assert by_state.returncode == 0, by_state.stderr
        projection = json.loads(by_state.stdout.decode("utf-8"))
        assert projection["source_kind"] == "dispatcher"
        assert projection["controller_phase"] == "awaiting-verification"
        assert projection["available_actions"][0]["action"] == "result"
        assert "Mutations remain with agy-worker.sh" in projection["advanced_recovery"]
        assert dispatch_state.read_bytes() == before

        by_id = run_cmd(
            sys.executable, "-I", "-S", "-B", str(script),
            "status", "--job-id", job_id, "--format", "json",
            env={"AGY_WORKER_LOG_DIR": str(log_root)},
        )
        assert by_id.returncode == 0, by_id.stderr
        assert json.loads(by_id.stdout.decode("utf-8"))["source_kind"] == "dispatcher"
        return True
    finally:
        f.clean()


check(
    "status projects dispatcher state or job ID through read-only controller authority",
    test_status_projects_dispatcher_state_and_job_id_read_only,
)


# ============================================================================
# 3. verify-finalize command positive, negative, and verifier boundary tests
# ============================================================================

def test_verify_finalize_structured_argv() -> bool:
    f = RepoFixture("verify-argv")
    try:
        # Create initial state
        initial_state = {
            "schema_version": 1,
            "kind": "agy-worker-workflow-state",
            "job_id": f.job_id,
            "repo_path": str(f.repo),
            "repo_identity": WORKFLOW_MODULE.identity(f.repo.lstat()),
            "worktree_path": str(f.worktree),
            "worktree_identity": WORKFLOW_MODULE.identity(f.worktree.lstat()),
            "branch": f.branch,
            "branch_ref": f"refs/heads/{f.branch}",
            "base": f.base,
            "preview_manifest_sha256": "0" * 64,
            "dispatch_job_dir": None,
            "job_state_path": None,
            "receipt_path": None,
        }
        store = WORKFLOW_MODULE.WorkflowStateStore(f.state_file, initial=True)
        store.create(initial_state)
        store.close()

        f.write_envelope(path="README.md", content="Verified content\n")

        # 1. Reject missing verifiers
        res = run_workflow(
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(f.receipt_file), "--envelope", str(f.envelope_file),
            "--assurance", "verified"
        )
        assert res.returncode != 0
        assert b"verifier is required" in res.stderr

        # 2. Structured argv verification succeeds
        res = run_workflow(
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(f.receipt_file), "--envelope", str(f.envelope_file),
            "--expect-edits", "--only", "README.md",
            "--verify-argv", '["/usr/bin/git","diff","--check"]',
            "--assurance", "verified"
        )
        assert res.returncode == 0
        assert f.receipt_file.exists()
        receipt = json.loads(f.receipt_file.read_bytes())
        assert receipt["verdict"] == "gate-passed"
        assert receipt["gate_exit"] == 0

        # State is updated with receipt handle only
        state_updated = json.loads(f.state_file.read_bytes())
        assert state_updated["receipt_path"] == str(f.receipt_file)
        assert "last_result" not in state_updated
        assert "final_assurance" not in state_updated
        assert "final_disposition" not in state_updated

        return True
    finally:
        f.clean()

check("verify-finalize runs structured argv verification and records receipt handle", test_verify_finalize_structured_argv)


def test_verify_finalize_shell_acknowledgements() -> bool:
    f = RepoFixture("verify-shell")
    try:
        initial_state = {
            "schema_version": 1,
            "kind": "agy-worker-workflow-state",
            "job_id": f.job_id,
            "repo_path": str(f.repo),
            "repo_identity": WORKFLOW_MODULE.identity(f.repo.lstat()),
            "worktree_path": str(f.worktree),
            "worktree_identity": WORKFLOW_MODULE.identity(f.worktree.lstat()),
            "branch": f.branch,
            "branch_ref": f"refs/heads/{f.branch}",
            "base": f.base,
            "preview_manifest_sha256": "0" * 64,
            "dispatch_job_dir": None,
            "job_state_path": None,
            "receipt_path": None,
        }
        store = WORKFLOW_MODULE.WorkflowStateStore(f.state_file, initial=True)
        store.create(initial_state)
        store.close()

        f.write_envelope(path="README.md", content="Shell verified\n")

        # 1. Unacknowledged --verify-shell fails closed
        res = run_workflow(
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(f.receipt_file), "--envelope", str(f.envelope_file),
            "--expect-edits", "--only", "README.md",
            "--verify-shell", "true",
            "--assurance", "verified"
        )
        assert res.returncode != 0
        assert b"requires network and credential access acknowledgements" in res.stderr

        # 2. Acknowledged --verify-shell succeeds
        res = run_workflow(
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(f.receipt_file), "--envelope", str(f.envelope_file),
            "--expect-edits", "--only", "README.md",
            "--verify-shell", "true",
            "--acknowledge-verifier-network",
            "--acknowledge-verifier-credential-access",
            "--assurance", "verified"
        )
        assert res.returncode == 0
        assert f.receipt_file.exists()

        return True
    finally:
        f.clean()

check("verify-finalize enforces explicit acknowledgements for shell verifiers", test_verify_finalize_shell_acknowledgements)


def test_verify_finalize_candidate_binding() -> bool:
    f = RepoFixture("cand-binding")
    try:
        initial_state = {
            "schema_version": 1,
            "kind": "agy-worker-workflow-state",
            "job_id": f.job_id,
            "repo_path": str(f.repo),
            "repo_identity": WORKFLOW_MODULE.identity(f.repo.lstat()),
            "worktree_path": str(f.worktree),
            "worktree_identity": WORKFLOW_MODULE.identity(f.worktree.lstat()),
            "branch": f.branch,
            "branch_ref": f"refs/heads/{f.branch}",
            "base": f.base,
            "preview_manifest_sha256": "0" * 64,
            "dispatch_job_dir": None,
            "job_state_path": None,
            "receipt_path": None,
        }
        store = WORKFLOW_MODULE.WorkflowStateStore(f.state_file, initial=True)
        store.create(initial_state)
        store.close()

        f.write_envelope(path="README.md", content="Candidate\n")

        # Wrong --candidate-sha fails closed
        res = run_workflow(
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(f.receipt_file), "--envelope", str(f.envelope_file),
            "--expect-edits", "--only", "README.md",
            "--candidate-sha", "f" * 64,
            "--verify-argv", '["true"]',
            "--assurance", "verified"
        )
        assert res.returncode != 0
        assert b"candidate state SHA mismatch" in res.stderr

        # A structurally valid but stale path identity fails before verification.
        current_cand = CANDIDATE.candidate_state_digest(f.worktree, f.base)
        stale_state = copy.deepcopy(initial_state)
        stale_state["worktree_identity"]["ino"] += 1
        f.state_file.write_bytes(WORKFLOW_MODULE.canonical_json(stale_state) + b"\n")
        f.state_file.chmod(0o600)
        res = run_workflow(
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(f.receipt_file), "--envelope", str(f.envelope_file),
            "--expect-edits", "--only", "README.md",
            "--candidate-sha", current_cand,
            "--verify-argv", '["true"]',
            "--assurance", "verified"
        )
        assert res.returncode != 0
        assert b"worktree identity changed" in res.stderr
        assert not f.receipt_file.exists()

        # Correct stored binding and --candidate-sha succeed.
        f.state_file.write_bytes(WORKFLOW_MODULE.canonical_json(initial_state) + b"\n")
        f.state_file.chmod(0o600)
        res = run_workflow(
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(f.receipt_file), "--envelope", str(f.envelope_file),
            "--expect-edits", "--only", "README.md",
            "--candidate-sha", current_cand,
            "--verify-argv", '["true"]',
            "--assurance", "partially_verified"
        )
        assert res.returncode == 0
        assert f.receipt_file.exists()
        receipt = json.loads(f.receipt_file.read_bytes())
        assert receipt["final_candidate_state_sha256"] == current_cand

        return True
    finally:
        f.clean()

check("verify-finalize binds exact candidate state and rejects mismatched candidate SHA", test_verify_finalize_candidate_binding)


def test_verify_finalize_propagates_finalize_failure() -> bool:
    f = RepoFixture("finalize-failure")
    try:
        f.write_envelope(path="README.md", content="Finalize candidate\n")
        candidate_sha = CANDIDATE.candidate_state_digest(f.worktree, f.base)
        dispatch_dir = f.state_dir / "dispatch"
        dispatch_dir.mkdir(mode=0o700)
        dispatch_state = dispatch_dir / "dispatch-state.json"
        dispatch_state.write_text('{"state":"awaiting-verification"}\n', encoding="utf-8")
        dispatch_state.chmod(0o600)
        dispatch_sha = hashlib.sha256(dispatch_state.read_bytes()).hexdigest()

        initial_state = {
            "schema_version": 1,
            "kind": "agy-worker-workflow-state",
            "job_id": f.job_id,
            "repo_path": str(f.repo),
            "repo_identity": WORKFLOW_MODULE.identity(f.repo.lstat()),
            "worktree_path": str(f.worktree),
            "worktree_identity": WORKFLOW_MODULE.identity(f.worktree.lstat()),
            "branch": f.branch,
            "branch_ref": f"refs/heads/{f.branch}",
            "base": f.base,
            "preview_manifest_sha256": "0" * 64,
            "dispatch_job_dir": str(dispatch_dir),
            "job_state_path": None,
            "receipt_path": None,
        }
        store = WORKFLOW_MODULE.WorkflowStateStore(f.state_file, initial=True)
        store.create(initial_state)
        store.close()

        copied_runtime = f.tmp / "runtime-copy"
        shutil.copytree(RUNTIME, copied_runtime)
        fake_verify = copied_runtime / "verify-job.sh"
        fake_verify.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "receipt = pathlib.Path(sys.argv[sys.argv.index('--receipt') + 1])\n"
            f"receipt.write_text(json.dumps({{'final_candidate_state_sha256': '{candidate_sha}'}}) + '\\n', encoding='utf-8')\n"
            "os.chmod(receipt, 0o600)\n",
            encoding="utf-8",
        )
        fake_verify.chmod(0o755)
        fake_dispatch = copied_runtime / "agy-worker.sh"
        fake_dispatch.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' 'finalize rejected exact approval' >&2\n"
            "exit 37\n",
            encoding="utf-8",
        )
        fake_dispatch.chmod(0o755)
        verification_file = f.state_dir / "verification.json"
        verification_file.write_bytes(
            json.dumps(
                {
                    "schema_version": 2,
                    "summary": "driver verification for finalizer failure test",
                    "passed_checks": ["fake bounded verifier"],
                    "failed_checks": [],
                    "advisory_checks": 0,
                    "missing_checks": 0,
                    "candidate_sha256": candidate_sha,
                    "coverage": "complete",
                    "verified_findings": 0,
                    "unresolved_gaps": 0,
                    "diff_review_complete": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        verification_file.chmod(0o600)

        missing_receipt = f.state_dir / "missing-verification-receipt.json"
        missing = run_cmd(
            sys.executable, "-I", "-S", "-B",
            str(copied_runtime / "scripts" / "workflow.py"),
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(missing_receipt),
            "--envelope", str(f.envelope_file),
            "--expect-edits", "--only", "README.md",
            "--candidate-sha", candidate_sha,
            "--verify-argv", '["true"]',
            "--approve-dispatch-sha", dispatch_sha,
            "--assurance", "verified",
        )
        assert missing.returncode == 20
        assert b"driver-authored --verification-json is required" in missing.stderr
        assert not missing_receipt.exists()

        res = run_cmd(
            sys.executable, "-I", "-S", "-B",
            str(copied_runtime / "scripts" / "workflow.py"),
            "verify-finalize", "--state", str(f.state_file),
            "--receipt", str(f.receipt_file),
            "--envelope", str(f.envelope_file),
            "--expect-edits", "--only", "README.md",
            "--candidate-sha", candidate_sha,
            "--verify-argv", '["true"]',
            "--approve-dispatch-sha", dispatch_sha,
            "--verification-json", str(verification_file),
            "--assurance", "verified",
        )
        assert res.returncode == 37
        assert b"finalize rejected exact approval" in res.stderr
        updated = json.loads(f.state_file.read_bytes())
        assert updated["receipt_path"] == str(f.receipt_file)
        return True
    finally:
        f.clean()


check("verify-finalize propagates controller finalization failure", test_verify_finalize_propagates_finalize_failure)


def test_verify_finalize_gate_and_dispatch_approval_boundaries() -> bool:
    f = RepoFixture("finalize-boundaries")
    try:
        f.write_envelope(path="README.md", content="Bound finalization candidate\n")
        candidate_sha = CANDIDATE.candidate_state_digest(f.worktree, f.base)
        dispatch_dir = f.state_dir / "dispatch"
        dispatch_dir.mkdir(mode=0o700)
        dispatch_state = dispatch_dir / "dispatch-state.json"
        dispatch_state.write_text(
            '{"state":"awaiting-verification"}\n', encoding="utf-8"
        )
        dispatch_state.chmod(0o600)
        dispatch_raw = dispatch_state.read_bytes()
        dispatch_sha = hashlib.sha256(dispatch_raw).hexdigest()

        initial_state = {
            "schema_version": 1,
            "kind": "agy-worker-workflow-state",
            "job_id": f.job_id,
            "repo_path": str(f.repo),
            "repo_identity": WORKFLOW_MODULE.identity(f.repo.lstat()),
            "worktree_path": str(f.worktree),
            "worktree_identity": WORKFLOW_MODULE.identity(f.worktree.lstat()),
            "branch": f.branch,
            "branch_ref": f"refs/heads/{f.branch}",
            "base": f.base,
            "preview_manifest_sha256": "0" * 64,
            "dispatch_job_dir": str(dispatch_dir),
            "job_state_path": None,
            "receipt_path": None,
        }
        store = WORKFLOW_MODULE.WorkflowStateStore(f.state_file, initial=True)
        store.create(initial_state)
        store.close()

        status = run_workflow("status", "--state", str(f.state_file))
        assert status.returncode == 0
        assert json.loads(status.stdout)["dispatch"]["state_sha256"] == dispatch_sha

        verification_file = f.state_dir / "verification.json"
        verification_file.write_bytes(
            json.dumps(
                {
                    "schema_version": 2,
                    "summary": "driver verification for facade boundary test",
                    "passed_checks": ["bounded verifier"],
                    "failed_checks": [],
                    "advisory_checks": 0,
                    "missing_checks": 0,
                    "candidate_sha256": candidate_sha,
                    "coverage": "complete",
                    "verified_findings": 0,
                    "unresolved_gaps": 0,
                    "diff_review_complete": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        verification_file.chmod(0o600)

        copied_runtime = f.tmp / "runtime-boundaries"
        shutil.copytree(RUNTIME, copied_runtime)
        fake_verify = copied_runtime / "verify-job.sh"
        fake_verify.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "receipt = pathlib.Path(sys.argv[sys.argv.index('--receipt') + 1])\n"
            "candidate = os.environ['FAKE_CANDIDATE_SHA']\n"
            "receipt.write_text(json.dumps({'final_candidate_state_sha256': candidate}) + '\\n', encoding='utf-8')\n"
            "os.chmod(receipt, 0o600)\n"
            "changed = os.environ.get('FAKE_CHANGED_DISPATCH')\n"
            "if changed:\n"
            "    path = pathlib.Path(changed)\n"
            "    path.write_text('{\"state\":\"changed\"}\\n', encoding='utf-8')\n"
            "    os.chmod(path, 0o600)\n"
            "raise SystemExit(int(os.environ.get('FAKE_GATE_RC', '0')))\n",
            encoding="utf-8",
        )
        fake_verify.chmod(0o755)
        sentinel = f.state_dir / "finalizer-called"
        fake_dispatch = copied_runtime / "agy-worker.sh"
        fake_dispatch.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib\n"
            "pathlib.Path(os.environ['FAKE_FINALIZER_SENTINEL']).write_text('called\\n', encoding='utf-8')\n"
            "print('{}')\n",
            encoding="utf-8",
        )
        fake_dispatch.chmod(0o755)

        def invoke(
            receipt: Path,
            *,
            approval: str | None,
            gate_rc: int = 0,
            change_dispatch: bool = False,
        ) -> subprocess.CompletedProcess[bytes]:
            argv = [
                sys.executable, "-I", "-S", "-B",
                str(copied_runtime / "scripts" / "workflow.py"),
                "verify-finalize", "--state", str(f.state_file),
                "--receipt", str(receipt), "--envelope", str(f.envelope_file),
                "--expect-edits", "--only", "README.md",
                "--candidate-sha", candidate_sha,
                "--verify-argv", '["true"]',
                "--verification-json", str(verification_file),
                "--assurance", "verified",
            ]
            if approval is not None:
                argv += ["--approve-dispatch-sha", approval]
            env = {
                "FAKE_CANDIDATE_SHA": candidate_sha,
                "FAKE_FINALIZER_SENTINEL": str(sentinel),
                "FAKE_GATE_RC": str(gate_rc),
            }
            if change_dispatch:
                env["FAKE_CHANGED_DISPATCH"] = str(dispatch_state)
            return run_cmd(*argv, env=env)

        missing = invoke(f.state_dir / "missing-approval.json", approval=None)
        assert missing.returncode == 20
        assert b"--approve-dispatch-sha" in missing.stderr
        assert not sentinel.exists()

        stale = invoke(f.state_dir / "stale-approval.json", approval="f" * 64)
        assert stale.returncode == 20
        assert b"stale or mismatched" in stale.stderr
        assert not sentinel.exists()

        for gate_rc in (10, 11, 12, 13, 14, 15):
            receipt = f.state_dir / f"gate-{gate_rc}.json"
            rejected = invoke(receipt, approval=dispatch_sha, gate_rc=gate_rc)
            assert rejected.returncode == gate_rc
            assert receipt.exists()
            assert not sentinel.exists()
            assert dispatch_state.read_bytes() == dispatch_raw

        exact = invoke(f.state_dir / "exact.json", approval=dispatch_sha)
        assert exact.returncode == 0
        assert sentinel.read_text(encoding="utf-8") == "called\n"
        sentinel.unlink()

        changed = invoke(
            f.state_dir / "changed.json",
            approval=dispatch_sha,
            change_dispatch=True,
        )
        assert changed.returncode == 20
        assert b"changed during verification" in changed.stderr
        assert not sentinel.exists()
        return True
    finally:
        f.clean()


check(
    "verify-finalize requires exact dispatch approval and never finalizes failed gates",
    test_verify_finalize_gate_and_dispatch_approval_boundaries,
)


# ============================================================================
# 4. Packaging and wrappers parity tests
# ============================================================================

def test_packaging_and_wrappers() -> bool:
    root_wrapper = ROOT / "workflow.sh"
    runtime_wrapper = RUNTIME / "workflow.sh"
    script_file = RUNTIME / "scripts" / "workflow.py"
    schema_file = RUNTIME / "schemas" / "workflow-state.schema.json"

    assert root_wrapper.exists() and os.access(root_wrapper, os.X_OK)
    assert runtime_wrapper.exists() and os.access(runtime_wrapper, os.X_OK)
    assert script_file.exists() and os.access(script_file, os.X_OK)
    assert schema_file.exists()

    # Verify root wrapper executes runtime wrapper
    res = run_cmd(str(root_wrapper), "--help")
    assert res.returncode == 0
    assert b"Canonical workflow facade" in res.stdout

    # Verify runtime wrapper executes workflow.py
    res = run_cmd(str(runtime_wrapper), "--help")
    assert res.returncode == 0
    assert b"Canonical workflow facade" in res.stdout

    return True

check("root and runtime workflow wrappers and permissions are intact", test_packaging_and_wrappers)


print()
print(f"PASSED: {passed} tests")
if failed:
    print(f"FAILED: {failed} tests")
    raise SystemExit(1)
