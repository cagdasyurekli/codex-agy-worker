#!/usr/bin/env python3
"""Installed-package, provider-free integration checks for the workflow facade.

The escape case is a characterization of the current Phase 0 gap.  A detected
outside write is evidence that this harness can observe the gap; it is not a
claim that the installed runtime contains the synthetic worker.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
MODEL = "gemini-3.8-flash"
EFFORT = "high"

AGY_HELP = """Usage of agy:
  --add-dir                       Add a directory to the workspace
  --conversation                  Resume a previous conversation by ID
  --disable-slash-commands        Disable slash command expansion
  --json-schema                   Optional JSON schema path
  --mode                          Set execution mode (accept-edits, plan)
  --model                         Select a model
  --output-format                 Output format (text, json, stream-json)
  --print                         Run a prompt
  --print-timeout                 Timeout for print mode
  --sandbox                       Run sandboxed
"""


@dataclass(frozen=True)
class RunObservation:
    returncode: int
    worker_calls: int
    candidate_content: str | None
    outside_content: str | None


def observation_failures(
    observation: RunObservation, *, expect_outside_write: bool
) -> list[str]:
    """Return exact integration-contract failures for mutation self-checks."""

    failures: list[str] = []
    if observation.returncode != 0:
        failures.append("workflow-returncode")
    if observation.worker_calls != 1:
        failures.append("worker-call-count")
    if observation.candidate_content != "synthetic candidate\n":
        failures.append("candidate-edit")
    outside_observed = observation.outside_content == "synthetic escape\n"
    if outside_observed != expect_outside_write:
        failures.append("outside-write-observation")
    return failures


class InstalledWorkflowFixture:
    def __init__(self, behavior: str) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=f"agy-installed-{behavior}-")).resolve()
        self.home = self.root / "home"
        self.state_home = self.root / "state"
        self.skills_dir = self.root / "skills"
        self.bin_dir = self.root / "bin"
        self.repo = self.root / "repo"
        for directory in (
            self.home,
            self.state_home,
            self.skills_dir,
            self.bin_dir,
            self.repo,
        ):
            directory.mkdir(mode=0o700)

        self.behavior = behavior
        self.job_id = f"installed-{behavior}"
        self.outside = self.root / "outside-sentinel.txt"
        self.calls = self.root / "agy-worker-calls.jsonl"
        self._write_fake_agy()
        self.env = dict(os.environ)
        self.env.update(
            {
                "HOME": str(self.home),
                "XDG_STATE_HOME": str(self.state_home),
                "CODEX_SKILLS_DIR": str(self.skills_dir),
                "PATH": f"{self.bin_dir}{os.pathsep}{self.env.get('PATH', '')}",
            }
        )
        self._install()
        self.workflow = self.skills_dir / "agy-worker" / "runtime" / "workflow.sh"
        if not self.workflow.is_file() or not os.access(self.workflow, os.X_OK):
            raise AssertionError("installed workflow CLI is unavailable")
        if self.workflow.samefile(ROOT / "skills" / "agy-worker" / "runtime" / "workflow.sh"):
            raise AssertionError("integration command did not come from the installed copy")
        self._init_repo()

    def _write_fake_agy(self) -> None:
        fake = self.bin_dir / "agy"
        source = f'''#!/usr/bin/env python3
import json
from pathlib import Path
import sys

args = sys.argv[1:]
calls = Path({str(self.calls)!r})
kind = "version" if args == ["--version"] else "help" if args == ["--help"] else "worker"
with calls.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"kind": kind, "argv": args}}, separators=(",", ":")) + "\\n")
if args == ["--version"]:
    print("1.1.24")
    raise SystemExit(0)
if args == ["--help"]:
    sys.stderr.write({AGY_HELP!r})
    raise SystemExit(0)

candidate = Path.cwd() / "candidate.txt"
candidate.write_text("synthetic candidate\\n", encoding="utf-8")
behavior = {self.behavior!r}
if behavior == "escape":
    Path({str(self.outside)!r}).write_text("synthetic escape\\n", encoding="utf-8")
elif behavior == "undeclared":
    (Path.cwd() / "undeclared.txt").write_text("undeclared\\n", encoding="utf-8")

envelope = {{
    "status": "completed",
    "summary": "provider-free synthetic candidate",
    "files_changed": [{{"path": "candidate.txt", "change": "created"}}],
    "commands_run": [],
    "tests_run": [],
    "risks": [],
    "open_questions": [],
    "confidence": 1,
    "requires_human": False,
}}
print(json.dumps(
    {{"event": "init", "conversation_id": "synthetic-1", "init": {{}}}},
    separators=(",", ":"),
))
print(json.dumps({{
    "event": "result",
    "result": {{
        "conversation_id": "synthetic-1",
        "status": "SUCCESS",
        "duration_seconds": 0,
        "num_turns": 1,
        "usage": {{}},
        "structured_output": envelope,
    }},
}}, separators=(",", ":")))
'''
        fake.write_text(source, encoding="utf-8")
        fake.chmod(0o755)

    def _install(self) -> None:
        result = subprocess.run(
            [str(INSTALLER)],
            cwd=str(ROOT),
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"standalone install failed: {result.stderr.decode('utf-8', 'replace')}"
            )

    def _init_repo(self) -> None:
        self.git("init", "-q")
        self.git("config", "user.name", "Synthetic Integration")
        self.git("config", "user.email", "synthetic@example.invalid")
        (self.repo / "README.md").write_text("# Synthetic repository\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "base")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(self.repo), *args],
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout.decode("utf-8", "strict").strip()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(self.workflow), *args],
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def launch(self) -> tuple[RunObservation, Path, Path]:
        preview = self.run_cli(
            "run", "--repo", str(self.repo), "--job-id", self.job_id, "--preview"
        )
        if preview.returncode != 0:
            raise AssertionError(preview.stderr.decode("utf-8", "replace"))
        manifest_sha = json.loads(preview.stdout)["manifest_sha256"]
        result = self.run_cli(
            "run",
            "--repo",
            str(self.repo),
            "--job-id",
            self.job_id,
            "--approve-whole-worktree",
            manifest_sha,
            "--model",
            MODEL,
            "--effort",
            EFFORT,
            "--task",
            "Create candidate.txt with the synthetic fixture content.",
        )
        workflow_states = list(
            self.state_home.glob(
                f"agy-worker/workflows/*/{self.job_id}/workflow.json"
            )
        )
        if len(workflow_states) != 1:
            raise AssertionError(f"expected one installed workflow state, got {workflow_states}")
        workflow_state = workflow_states[0]
        state = json.loads(workflow_state.read_bytes())
        worktree = Path(state["worktree_path"])
        calls = [] if not self.calls.exists() else [
            json.loads(raw)
            for raw in self.calls.read_text(encoding="utf-8").splitlines()
        ]
        kinds = [call["kind"] for call in calls]
        if "version" not in kinds or "help" not in kinds:
            raise AssertionError(f"installed dispatcher skipped compatibility probes: {kinds}")
        worker_calls = [call for call in calls if call["kind"] == "worker"]
        for call in worker_calls:
            argv = call["argv"]
            if "--print" not in argv or "--output-format" not in argv:
                raise AssertionError(f"synthetic provider saw unexpected argv: {argv}")
        observation = RunObservation(
            returncode=result.returncode,
            worker_calls=len(worker_calls),
            candidate_content=(
                (worktree / "candidate.txt").read_text(encoding="utf-8")
                if (worktree / "candidate.txt").is_file()
                else None
            ),
            outside_content=(
                self.outside.read_text(encoding="utf-8")
                if self.outside.is_file()
                else None
            ),
        )
        return observation, workflow_state, worktree

    def clean(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class InstalledWorkflowIntegrationTests(unittest.TestCase):
    def test_installed_cli_runs_one_benign_synthetic_worker(self) -> None:
        fixture = InstalledWorkflowFixture("benign")
        try:
            observation, _state, _worktree = fixture.launch()
            self.assertEqual(
                observation_failures(observation, expect_outside_write=False), []
            )
        finally:
            fixture.clean()

    def test_phase0_harness_detects_current_outside_write_gap(self) -> None:
        fixture = InstalledWorkflowFixture("escape")
        try:
            observation, _state, _worktree = fixture.launch()
            self.assertEqual(
                observation_failures(observation, expect_outside_write=True), []
            )
            print(
                "PHASE0_GAP_DETECTED: synthetic worker wrote outside its disposable "
                "worktree; this is characterization evidence, not containment acceptance"
            )
        finally:
            fixture.clean()

    def test_assertion_oracle_rejects_deliberately_broken_observations(self) -> None:
        valid = RunObservation(0, 1, "synthetic candidate\n", None)
        mutants = {
            "workflow-returncode": replace(valid, returncode=7),
            "worker-call-count": replace(valid, worker_calls=0),
            "candidate-edit": replace(valid, candidate_content="wrong\n"),
            "outside-write-observation": replace(
                valid, outside_content="synthetic escape\n"
            ),
        }
        for expected, mutant in mutants.items():
            with self.subTest(expected=expected):
                self.assertIn(
                    expected,
                    observation_failures(mutant, expect_outside_write=False),
                )

    def test_verification_rejects_undeclared_in_worktree_artifact(self) -> None:
        fixture = InstalledWorkflowFixture("undeclared")
        try:
            observation, workflow_state, worktree = fixture.launch()
            self.assertEqual(
                observation_failures(observation, expect_outside_write=False), []
            )
            state = json.loads(workflow_state.read_bytes())
            dispatch_state = Path(state["dispatch_job_dir"]) / "dispatch-state.json"
            dispatch_raw = dispatch_state.read_bytes()
            dispatch_value = json.loads(dispatch_raw)
            envelope = Path(dispatch_value["result_path"])
            receipt = workflow_state.with_name("undeclared-receipt.json")
            verification = workflow_state.with_name("undeclared-verification.json")
            verification.write_bytes(
                json.dumps(
                    {
                        "schema_version": 2,
                        "summary": "Phase 0 path-policy integration check",
                        "passed_checks": [],
                        "failed_checks": ["undeclared path expected to be rejected"],
                        "advisory_checks": 0,
                        "missing_checks": 0,
                        "candidate_sha256": dispatch_value["result_sha256"],
                        "coverage": "partial",
                        "verified_findings": 1,
                        "unresolved_gaps": 1,
                        "diff_review_complete": False,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
                + b"\n"
            )
            verification.chmod(0o600)
            rejected = fixture.run_cli(
                "verify-finalize",
                "--state",
                str(workflow_state),
                "--receipt",
                str(receipt),
                "--envelope",
                str(envelope),
                "--expect-edits",
                "--only",
                "candidate.txt",
                "--verify-argv",
                '["/usr/bin/git","diff","--check"]',
                "--assurance",
                "rejected",
                "--verification-json",
                str(verification),
                "--approve-dispatch-sha",
                hashlib.sha256(dispatch_raw).hexdigest(),
            )
            self.assertEqual(rejected.returncode, 10)
            self.assertTrue((worktree / "undeclared.txt").is_file())
            self.assertTrue(receipt.is_file())
            receipt_value = json.loads(receipt.read_bytes())
            self.assertEqual(receipt_value["verdict"], "rejected")
            self.assertEqual(receipt_value["gate_exit"], 10)
            self.assertEqual(receipt_value["gate_outcome"], "scope-violation")
        finally:
            fixture.clean()


if __name__ == "__main__":
    unittest.main(verbosity=2)
