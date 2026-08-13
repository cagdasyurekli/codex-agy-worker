#!/usr/bin/env python3
"""Offline adversarial tests for the branch-backed local job lifecycle."""

from __future__ import annotations

import hashlib
import importlib.util
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "skills" / "agy-worker" / "runtime"
MODULE_PATH = RUNTIME / "scripts" / "job_lifecycle.py"
CANDIDATE_PATH = RUNTIME / "scripts" / "candidate_state.py"
PYTHON = "/usr/bin/python3"


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load("job_lifecycle_tested", MODULE_PATH)
CANDIDATE = sys.modules["candidate_state"]
DISPATCH = sys.modules["agy_dispatch"]
TMP = Path(tempfile.mkdtemp(prefix="agyworker-job-lifecycle-tests.")).resolve()
TMP.chmod(0o700)
passed = 0
failed = 0


def check(name: str, predicate: Callable[[], bool]) -> None:
    global passed, failed
    try:
        result = bool(predicate())
    except BaseException as exc:
        result = False
        detail = f" ({type(exc).__name__}: {exc})"
    else:
        detail = ""
    if result:
        passed += 1
    else:
        failed += 1
        print(f"FAIL job lifecycle: {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (MODULE.JobError, CANDIDATE.CandidateStateError, OSError, ValueError):
        return True
    return False


def git(repo: Path, *args: str, check_result: bool = True) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if check_result and completed.returncode != 0:
        raise AssertionError(f"git failed: {args!r}")
    return completed.stdout


def make_repo(label: str) -> tuple[Path, str]:
    repo = TMP / label
    repo.mkdir(mode=0o700)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "fixture.txt").write_bytes(b"before\n")
    git(repo, "add", "fixture.txt")
    git(repo, "commit", "-qm", "base")
    return repo, git(repo, "rev-parse", "HEAD").decode("ascii").strip()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def run_cli(
    *args: str,
    timeout: float = 20.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [PYTHON, "-I", "-S", "-B", str(MODULE_PATH), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env=env,
    )


class Fixture:
    def __init__(self, label: str) -> None:
        self.root = TMP / label
        self.root.mkdir(mode=0o700)
        self.repo, self.base = make_repo(f"{label}-repo")
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(mode=0o700)
        self.state = self.state_dir / "job.json"
        self.worktree = self.root / "worktree"
        self.receipt = self.state_dir / "receipt.json"
        self.envelope = self.state_dir / "envelope.json"
        self.job_id = f"job-{label}"
        self.branch = f"codex/{label}"

    def init(self) -> subprocess.CompletedProcess[bytes]:
        return self.init_as(self.branch)

    def init_as(
        self,
        branch: str,
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return run_cli(
            "init", "--state", str(self.state), "--repo", str(self.repo),
            "--worktree", str(self.worktree), "--branch", branch,
            "--base", self.base, "--job-id", self.job_id,
            env=env,
        )

    def value(self) -> dict[str, Any]:
        return json.loads(self.state.read_bytes())

    def state_sha(self) -> str:
        return hashlib.sha256(self.state.read_bytes()).hexdigest()

    def write_envelope(self, *, changed: bool = True) -> None:
        value = {
            "status": "completed", "summary": "synthetic",
            "files_changed": ([{"path": "fixture.txt", "change": "modified"}] if changed else []),
            "commands_run": [], "tests_run": [], "risks": [], "open_questions": [],
            "confidence": 1, "requires_human": False,
        }
        self.envelope.write_bytes(canonical(value))
        self.envelope.chmod(0o600)

    def verify_reject(self) -> subprocess.CompletedProcess[bytes]:
        self.write_envelope()
        return run_cli(
            "verify", "--state", str(self.state), "--receipt", str(self.receipt),
            "--envelope", str(self.envelope), "--only", "tests/**", "--verify", "true",
        )

    def cleanup(self, *, state_sha: str | None = None, job: str | None = None, candidate: str | None = None) -> subprocess.CompletedProcess[bytes]:
        value = self.value()
        bound = value.get("receipt") or {}
        return run_cli(
            "cleanup", "--state", str(self.state),
            "--approve-job", job or self.job_id,
            "--approve-state-sha", state_sha or self.state_sha(),
            "--approve-candidate-sha", candidate or bound.get("final_candidate_state_sha256", "0" * 64),
        )

    def make_failed_dispatch(
        self, label: str = "dispatch", *, status: str = "failed",
        reason: str = "idle_timeout", exit_code: int = 9,
    ) -> Path:
        job = self.root / label
        job.mkdir(mode=0o700)
        lock = job / DISPATCH.LOCK_NAME
        lock.write_bytes(b""); lock.chmod(0o600)
        now = time.time()
        value = {
            "schema_version": 1, "kind": "agy-worker-dispatch-state",
            "sequence": 2, "previous_state_sha256": "1" * 64,
            "job_id": self.job_id, "status": status, "attempt": 1,
            "attempt_origin": "initial", "reason": reason, "exit_code": exit_code,
            "controller_pid": None, "workdir": str(self.worktree),
            "created_epoch": now - 2, "started_epoch": now - 2,
            "updated_epoch": now, "finished_epoch": now,
            "elapsed_seconds": 2.0, "progress_count": 1,
            "last_progress_epoch": now - 1, "notice_count": 0,
            "hard_seconds": 7200.0, "max_seconds": 43200.0,
            "idle_seconds": 600.0, "attempt_base_elapsed": 0.0,
            "cancel_requested": False, "conversation_id": None,
            "resume_available": False, "remote_cancel_unverified": False,
            "result_path": None, "stream_path": str(job / "stream.ndjson"),
            "stderr_path": str(job / "stderr.log"), "agy_returncode": -15,
            "limit_kind": "idle",
            "command_sha256": "2" * 64,
            "command_identity": [1, 2, os.getuid(), os.getgid(), 0o600],
            "stage_sha256": None, "stage_identity": None,
            "result_sha256": None, "result_identity": None,
        }
        DISPATCH.validate_state(value)
        state = job / DISPATCH.STATE_NAME
        state.write_bytes(DISPATCH.canonical(value)); state.chmod(0o600)
        return state

    def record_dispatch(
        self, dispatch_state: Path, *, state_sha: str | None = None,
        candidate: str | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return run_cli(
            "record-dispatch-failure", "--state", str(self.state),
            "--dispatch-state", str(dispatch_state),
            "--approve-job", self.job_id,
            "--approve-state-sha", state_sha or self.state_sha(),
            "--approve-candidate-sha", candidate or CANDIDATE.candidate_state_digest(self.worktree, self.base),
        )

    def abort(
        self, *, state_sha: str | None = None, candidate: str | None = None,
        discard: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        value = self.value(); bound = value.get("dispatch") or {}
        argv = [
            "abort", "--state", str(self.state), "--approve-job", self.job_id,
            "--approve-state-sha", state_sha or self.state_sha(),
            "--approve-candidate-sha", candidate or bound.get("candidate_state_sha256", "0" * 64),
        ]
        if discard:
            argv.append("--discard-unverified")
        return run_cli(*argv)


def clean_fixture(fixture: Fixture) -> None:
    git(fixture.repo, "worktree", "remove", "--force", str(fixture.worktree), check_result=False)
    git(fixture.repo, "update-ref", "-d", f"refs/heads/{fixture.branch}", fixture.base, check_result=False)


check("module imports with bytecode disabled", lambda: sys.dont_write_bytecode)
check("root wrapper is executable", lambda: os.access(ROOT / "job.sh", os.X_OK))
check("portable wrapper is executable", lambda: os.access(RUNTIME / "job.sh", os.X_OK))
check("candidate helper is executable", lambda: os.access(CANDIDATE_PATH, os.X_OK))
check("state schema is present", lambda: (RUNTIME / "schemas" / "job-state.schema.json").is_file())


def candidate_reference(repo: Path, base: str) -> str:
    digest = hashlib.sha256()
    tracked = git(repo, "diff", "--binary", "--no-ext-diff", "--no-textconv", "--submodule=short", base, "--")
    digest.update(len(tracked).to_bytes(8, "big")); digest.update(tracked)
    paths = set(part for part in git(repo, "ls-files", "--others", "--exclude-standard", "-z", "--").split(b"\0") if part)
    paths.update(part for part in git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--").split(b"\0") if part)
    for raw in sorted(paths):
        path = repo / raw.decode("utf-8", "surrogateescape")
        info = path.lstat()
        digest.update(len(raw).to_bytes(8, "big")); digest.update(raw)
        digest.update(str(stat.S_IFMT(info.st_mode)).encode("ascii")); digest.update(str(stat.S_IMODE(info.st_mode)).encode("ascii"))
        if stat.S_ISLNK(info.st_mode):
            digest.update(os.readlink(path).encode("utf-8", "surrogateescape"))
        elif stat.S_ISREG(info.st_mode):
            digest.update(path.read_bytes())
        else:
            digest.update(b"non-regular")
    return digest.hexdigest()


repo, base = make_repo("candidate-parity")
(repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
(repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")
(repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
(repo / "link").symlink_to("fixture.txt")
(repo / "fixture.txt").write_text("changed\n", encoding="utf-8")
check("shared candidate digest exactly matches legacy gate algorithm", lambda: CANDIDATE.candidate_state_digest(repo, base) == candidate_reference(repo, base))
before = CANDIDATE.candidate_state_digest(repo, base)
(repo / "ignored.txt").write_text("mutated\n", encoding="utf-8")
check("shared candidate digest binds ignored artifacts", lambda: CANDIDATE.candidate_state_digest(repo, base) != before)
check("candidate CLI rejects repeated repo authority", lambda: CANDIDATE.main(["--repo", str(repo), "--repo", str(repo), "--base", base]) == 64)
check("candidate CLI rejects symbolic base", lambda: CANDIDATE.main(["--repo", str(repo), "--base", "HEAD"]) == 1)


def private_parent_pair() -> bool:
    parent = TMP / "private-parent"
    parent.mkdir(mode=0o700)
    path, descriptor = MODULE.validate_private_parent(parent)
    os.close(descriptor)
    accepted = path == parent
    parent.chmod(0o755)
    return accepted and rejects(lambda: MODULE.validate_private_parent(parent))


check("state parent accepts owner 0700 and rejects 0755", private_parent_pair)
check("state path rejects canonical alias", lambda: rejects(lambda: MODULE.real_absolute(Path("/var/tmp"), "test")))


branch_fixture = Fixture("branch-alias")
original_branch = git(branch_fixture.repo, "symbolic-ref", "--short", "HEAD").decode().strip()
git(branch_fixture.repo, "checkout", "-qb", "old")
git(branch_fixture.repo, "checkout", "-q", original_branch)
alias_result = branch_fixture.init_as("@{-1}")
check(
    "checkout shorthand is rejected before any ref worktree or state is created",
    lambda: (
        alias_result.returncode == 64
        and not branch_fixture.state.exists()
        and not branch_fixture.worktree.exists()
        and git(
            branch_fixture.repo,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/@{-1}",
            check_result=False,
        ) == b""
        and git(branch_fixture.repo, "worktree", "list", "--porcelain").count(b"worktree ") == 1
    ),
)
original_branch_syntax = MODULE.canonical_branch_syntax
try:
    MODULE.canonical_branch_syntax = lambda _branch: True
    check(
        "canonical check-ref stdout independently rejects checkout shorthand",
        lambda: rejects(lambda: MODULE.validate_branch(branch_fixture.repo, "@{-1}")),
    )
finally:
    MODULE.canonical_branch_syntax = original_branch_syntax
for bad_branch in ("main@{1}", "main~1", "main^", "main:next", "main*", "main?", "main[0]", "main\\next"):
    check(
        f"revision or metachar branch is rejected: {bad_branch}",
        lambda bad_branch=bad_branch: rejects(
            lambda: MODULE.validate_branch(branch_fixture.repo, bad_branch)
        ),
    )


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o700)


hook_fixture = Fixture("hook-policy")
hook_marker = hook_fixture.root / "hook.marker"
late_marker = hook_fixture.root / "hook-late.marker"
hooks = hook_fixture.repo / ".git" / "hooks"
write_executable(
    hooks / "post-checkout",
    f"printf ran > {shlex.quote(str(hook_marker))}\n(sleep 1; printf late > {shlex.quote(str(late_marker))}) &\n",
)
write_executable(
    hooks / "pre-checkout",
    f"printf ran > {shlex.quote(str(hook_marker))}\n",
)
hook_init = hook_fixture.init()
time.sleep(1.1)
check(
    "private empty hooks policy suppresses checkout hooks and descendants",
    lambda: hook_init.returncode == 0 and not hook_marker.exists() and not late_marker.exists(),
)
clean_fixture(hook_fixture)


attribute_fixture = Fixture("attribute-filter")
(attribute_fixture.repo / ".gitattributes").write_text("fixture.txt filter=missing-driver\n", encoding="utf-8")
git(attribute_fixture.repo, "add", ".gitattributes")
git(attribute_fixture.repo, "commit", "-qm", "attributes")
attribute_fixture.base = git(attribute_fixture.repo, "rev-parse", "HEAD").decode().strip()
attribute_init = attribute_fixture.init()
check(
    "effective base-tree filter attributes reject before initialization",
    lambda: attribute_init.returncode == 64 and not attribute_fixture.state.exists() and not attribute_fixture.worktree.exists(),
)

info_attribute_fixture = Fixture("info-attribute-filter")
(info_attribute_fixture.repo / ".git" / "info" / "attributes").write_text(
    "fixture.txt filter=info-driver\n", encoding="utf-8"
)
info_attribute_init = info_attribute_fixture.init()
check(
    "repository info attributes cannot authorize a checkout filter",
    lambda: info_attribute_init.returncode == 64 and not info_attribute_fixture.state.exists(),
)


filter_fixture = Fixture("configured-filter")
filter_marker = filter_fixture.root / "filter.marker"
filter_program = filter_fixture.root / "filter.sh"
write_executable(filter_program, f"printf ran > {shlex.quote(str(filter_marker))}\ncat\n")
(filter_fixture.repo / ".gitattributes").write_text("fixture.txt filter=danger\n", encoding="utf-8")
git(filter_fixture.repo, "add", ".gitattributes")
git(filter_fixture.repo, "commit", "-qm", "filter attributes")
filter_fixture.base = git(filter_fixture.repo, "rev-parse", "HEAD").decode().strip()
git(filter_fixture.repo, "config", "filter.danger.smudge", str(filter_program))
git(filter_fixture.repo, "config", "filter.danger.process", str(filter_program))
filter_init = filter_fixture.init()
check(
    "smudge and process filters reject with zero external execution",
    lambda: filter_init.returncode == 64 and not filter_marker.exists() and not filter_fixture.state.exists(),
)


config_fixture = Fixture("external-config")
config_marker = config_fixture.root / "config.marker"
config_program = config_fixture.root / "config.sh"
write_executable(config_program, f"printf ran > {shlex.quote(str(config_marker))}\n")
git(config_fixture.repo, "config", "core.fsmonitor", str(config_program))
config_init = config_fixture.init()
check(
    "configured fsmonitor rejects with zero execution",
    lambda: config_init.returncode == 64 and not config_marker.exists(),
)


pager_fixture = Fixture("pager-config")
pager_marker = pager_fixture.root / "pager.marker"
pager_program = pager_fixture.root / "pager.sh"
write_executable(pager_program, f"printf ran > {shlex.quote(str(pager_marker))}\ncat\n")
git(pager_fixture.repo, "config", "core.pager", str(pager_program))
pager_init = pager_fixture.init()
check(
    "configured pager rejects with zero execution",
    lambda: pager_init.returncode == 64 and not pager_marker.exists(),
)


alias_fixture = Fixture("git-alias")
alias_marker = alias_fixture.root / "alias.marker"
git(alias_fixture.repo, "config", "alias.worktree", f"!printf ran > {alias_marker}")
alias_init = alias_fixture.init()
check(
    "exact built-in Git commands never execute a hostile alias",
    lambda: alias_init.returncode == 0 and not alias_marker.exists(),
)
clean_fixture(alias_fixture)


ambient_fixture = Fixture("ambient-config")
ambient_marker = ambient_fixture.root / "ambient.marker"
ambient_hooks = ambient_fixture.root / "ambient-hooks"
ambient_hooks.mkdir(mode=0o700)
write_executable(
    ambient_hooks / "post-checkout",
    f"printf ran > {shlex.quote(str(ambient_marker))}\n",
)
global_config = ambient_fixture.root / "global.gitconfig"
global_config.write_text(f"[core]\n\thooksPath = {ambient_hooks}\n", encoding="utf-8")
hostile_env = dict(os.environ)
hostile_env.update(
    {
        "GIT_CONFIG_GLOBAL": str(global_config),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(ambient_hooks),
        "GIT_PAGER": str(config_program),
        "GIT_EXTERNAL_DIFF": str(config_program),
    }
)
ambient_init = ambient_fixture.init_as(ambient_fixture.branch, env=hostile_env)
check(
    "ambient Git config environment and external helpers are stripped",
    lambda: ambient_init.returncode == 0 and not ambient_marker.exists() and not config_marker.exists(),
)
clean_fixture(ambient_fixture)

check(
    "fatal ref probe is never classified as absence",
    lambda: rejects(lambda: MODULE.ref_value(TMP / "not-a-repository", "refs/heads/missing")),
)


fixture = Fixture("happy")
init = fixture.init()
check("init creates exact branch-backed ready state", lambda: init.returncode == 0 and fixture.value()["phase"] == "ready")
check("state is canonical owner-private mode 0600", lambda: fixture.state.read_bytes() == canonical(fixture.value()) and stat.S_IMODE(fixture.state.stat().st_mode) == 0o600)
check("init uses explicit immutable base", lambda: git(fixture.worktree, "rev-parse", "HEAD").decode().strip() == fixture.base)
check("init binds exact branch ref", lambda: git(fixture.worktree, "symbolic-ref", "HEAD").decode().strip() == f"refs/heads/{fixture.branch}")
status = run_cli("status", "--state", str(fixture.state))
status_value = json.loads(status.stdout)
check("status is read-only and grants zero cleanup authority", lambda: status.returncode == 0 and status_value["cleanup_authorized"] is False and status_value["phase"] == "ready")
check("preserve instructions reject unverified state", lambda: run_cli("preserve-instructions", "--state", str(fixture.state)).returncode == 64)

original = fixture.value()
legacy = dict(original); legacy.pop("dispatch")
check("pre-feature schema-v1 lifecycle states remain readable", lambda: MODULE.validate_state(legacy) is legacy)
mutated = dict(original); mutated["previous_state_sha256"] = None
check("state history rejects a missing digest after sequence one", lambda: rejects(lambda: MODULE.validate_state(mutated)))
mutated = dict(original); mutated["branch_ref"] = "refs/heads/other"
check("state rejects branch and ref mismatch", lambda: rejects(lambda: MODULE.validate_state(mutated)))
mutated = dict(original); mutated["phase"] = "verified-rejected"
check("state rejects phase fields without a receipt", lambda: rejects(lambda: MODULE.validate_state(mutated)))
mutated = dict(original); mutated["repo_path"] += "/../x"
check("state rejects noncanonical bound paths", lambda: rejects(lambda: MODULE.validate_state(mutated)))


fixture.worktree.joinpath("fixture.txt").write_text("worker edit\n", encoding="utf-8")
outside_target = fixture.root / "outside-sentinel.txt"
outside_target.write_text("preserve\n", encoding="utf-8")
link = fixture.worktree / "bound-link"
link.symlink_to(outside_target)
verify = fixture.verify_reject()
check("verify delegates to qa-gate and records rejected receipt", lambda: verify.returncode == 10 and fixture.value()["phase"] == "verified-rejected")
rejected = fixture.value()
check("receipt path raw hash and candidate digest are bound", lambda: rejected["receipt"]["sha256"] == hashlib.sha256(fixture.receipt.read_bytes()).hexdigest() and rejected["receipt"]["final_candidate_state_sha256"] == CANDIDATE.candidate_state_digest(fixture.worktree, fixture.base))
rejected_status = json.loads(run_cli("status", "--state", str(fixture.state)).stdout)
check("status exposes exact approval hashes but grants no cleanup authority", lambda: rejected_status["state_sha256"] == fixture.state_sha() and rejected_status["receipt_candidate_state_sha256"] == rejected["receipt"]["final_candidate_state_sha256"] and rejected_status["cleanup_authorized"] is False)
check("cleanup rejects wrong job approval", lambda: fixture.cleanup(job="other-job").returncode == 64)
check("cleanup rejects stale state approval", lambda: fixture.cleanup(state_sha="0" * 64).returncode == 64)
check("cleanup rejects wrong candidate approval", lambda: fixture.cleanup(candidate="0" * 64).returncode == 64)

receipt_bytes = fixture.receipt.read_bytes()
fixture.receipt.write_bytes(receipt_bytes + b" ")
check("cleanup rejects mutated receipt bytes", lambda: fixture.cleanup().returncode == 64)
fixture.receipt.write_bytes(receipt_bytes); fixture.receipt.chmod(0o600)

fixture.worktree.joinpath("fixture.txt").write_text("post-gate drift\n", encoding="utf-8")
check("cleanup rejects candidate drift after receipt", lambda: fixture.cleanup().returncode == 64)
fixture.worktree.joinpath("fixture.txt").write_text("worker edit\n", encoding="utf-8")

nested = fixture.worktree / "nested"
nested.mkdir(); git(nested, "init", "-q")
check("cleanup rejects a nested repository", lambda: fixture.cleanup().returncode == 64)
shutil.rmtree(nested)

fifo = fixture.worktree / "unsafe-fifo"
os.mkfifo(fifo)
check("cleanup rejects special deletion nodes", lambda: fixture.cleanup().returncode == 64)
fifo.unlink()

cleanup = fixture.cleanup()
check("digest-bound symlink cleans without traversing its outside target", lambda: cleanup.returncode == 0 and outside_target.read_bytes() == b"preserve\n")
check("triple-approved rejected candidate cleans exact worktree and ref", lambda: not fixture.worktree.exists() and git(fixture.repo, "rev-parse", "--verify", f"refs/heads/{fixture.branch}", check_result=False) == b"")
check("cleanup retains private canonical cleaned tombstone", lambda: fixture.value()["phase"] == "cleaned" and fixture.value()["cleanup_step"] == "branch-removed" and stat.S_IMODE(fixture.state.stat().st_mode) == 0o600)


abort_clean = Fixture("abort-clean")
assert abort_clean.init().returncode == 0
clean_sha = CANDIDATE.candidate_state_digest(abort_clean.worktree, abort_clean.base)
check("canonical clean candidate digest matches explicit empty-domain digest", lambda: clean_sha == MODULE.EMPTY_CANDIDATE_STATE_SHA256)
clean_dispatch = abort_clean.make_failed_dispatch()
check("dispatch failure recording rejects stale lifecycle SHA", lambda: abort_clean.record_dispatch(clean_dispatch, state_sha="0" * 64).returncode == 64)
clean_record = abort_clean.record_dispatch(clean_dispatch)
check("receiptless terminal dispatch failure binds exact state and candidate", lambda: clean_record.returncode == 0 and abort_clean.value()["phase"] == "dispatch-failed" and abort_clean.value()["receipt"] is None)
check("abort rejects stale lifecycle SHA", lambda: abort_clean.abort(state_sha="0" * 64).returncode == 64)
clean_abort = abort_clean.abort()
check("clean terminal dispatch residual aborts without discard flag", lambda: clean_abort.returncode == 0 and abort_clean.value()["phase"] == "aborted" and not abort_clean.worktree.exists())


abort_changed = Fixture("abort-changed")
assert abort_changed.init().returncode == 0
abort_changed.worktree.joinpath("fixture.txt").write_text("unverified edit\n", encoding="utf-8")
changed_dispatch = abort_changed.make_failed_dispatch()
assert abort_changed.record_dispatch(changed_dispatch).returncode == 0
check("changed candidate abort requires explicit discard-unverified", lambda: abort_changed.abort().returncode == 64 and abort_changed.worktree.exists())
changed_abort = abort_changed.abort(discard=True)
check("explicit discard removes exact bound unverified candidate", lambda: changed_abort.returncode == 0 and abort_changed.value()["phase"] == "aborted" and not abort_changed.worktree.exists())


abort_active = Fixture("abort-active")
assert abort_active.init().returncode == 0
active_dispatch = abort_active.make_failed_dispatch()
active_lock_fd = os.open(active_dispatch.parent / DISPATCH.LOCK_NAME, os.O_RDWR)
fcntl.flock(active_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
try:
    check("active supervisor lock blocks dispatch-failure recording", lambda: abort_active.record_dispatch(active_dispatch).returncode == 64)
finally:
    fcntl.flock(active_lock_fd, fcntl.LOCK_UN); os.close(active_lock_fd)
clean_fixture(abort_active)


abort_orphaned = Fixture("abort-orphaned")
assert abort_orphaned.init().returncode == 0
orphaned_dispatch = abort_orphaned.make_failed_dispatch(
    status="orphaned", reason="status_unavailable", exit_code=20,
)
check(
    "orphaned supervisor state is preserve-only and cannot authorize abort",
    lambda: abort_orphaned.record_dispatch(orphaned_dispatch).returncode == 64
    and abort_orphaned.value()["phase"] == "ready"
    and abort_orphaned.worktree.exists(),
)
clean_fixture(abort_orphaned)


abort_symlink = Fixture("abort-symlink")
assert abort_symlink.init().returncode == 0
real_dispatch = abort_symlink.make_failed_dispatch()
alias_dispatch = abort_symlink.root / "dispatch-alias.json"
alias_dispatch.symlink_to(real_dispatch)
check("symlink dispatch state is never recordable", lambda: abort_symlink.record_dispatch(alias_dispatch).returncode == 64)
clean_fixture(abort_symlink)


abort_replaced = Fixture("abort-replaced")
assert abort_replaced.init().returncode == 0
replaced_dispatch = abort_replaced.make_failed_dispatch()
assert abort_replaced.record_dispatch(replaced_dispatch).returncode == 0
replaced_raw = replaced_dispatch.read_bytes()
replaced_dispatch.unlink(); replaced_dispatch.write_bytes(replaced_raw); replaced_dispatch.chmod(0o600)
check("dispatch state inode replacement blocks abort even with identical bytes", lambda: abort_replaced.abort().returncode == 64 and abort_replaced.worktree.exists())
clean_fixture(abort_replaced)


abort_race = Fixture("abort-race")
assert abort_race.init().returncode == 0
race_dispatch = abort_race.make_failed_dispatch()
assert abort_race.record_dispatch(race_dispatch).returncode == 0
race_lock_fd = os.open(race_dispatch.parent / DISPATCH.LOCK_NAME, os.O_RDWR)
fcntl.flock(race_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
try:
    check("active controller lock blocks abort after failure was recorded", lambda: abort_race.abort().returncode == 64 and abort_race.worktree.exists())
finally:
    fcntl.flock(race_lock_fd, fcntl.LOCK_UN); os.close(race_lock_fd)
clean_fixture(abort_race)


def abort_holds_lock_across_delete() -> bool:
    subject = Fixture("abort-held-lock")
    if subject.init().returncode != 0:
        return False
    dispatch_state = subject.make_failed_dispatch()
    if subject.record_dispatch(dispatch_state).returncode != 0:
        clean_fixture(subject); return False
    observed: list[bool] = []
    original_checkpoint = MODULE._cleanup_checkpoint
    probe = (
        "import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);"
        "\ntry:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        "\nexcept BlockingIOError:raise SystemExit(0)"
        "\nraise SystemExit(1)"
    )

    def checkpoint(name: str) -> None:
        if name == "before-abort-worktree-remove":
            completed = subprocess.run(
                [PYTHON, "-I", "-S", "-B", "-c", probe, str(dispatch_state.parent / DISPATCH.LOCK_NAME)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=3, check=False,
            )
            observed.append(completed.returncode == 0)

    value = subject.value()
    argv = [
        "abort", "--state", str(subject.state), "--approve-job", subject.job_id,
        "--approve-state-sha", subject.state_sha(), "--approve-candidate-sha",
        value["dispatch"]["candidate_state_sha256"],
    ]
    try:
        MODULE._cleanup_checkpoint = checkpoint
        prior_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            result = MODULE.main(argv)
        finally:
            sys.stdout = prior_stdout
    finally:
        MODULE._cleanup_checkpoint = original_checkpoint
        clean_fixture(subject)
    return result == 0 and observed == [True]


check("abort holds supervisor lock through destructive worktree removal", abort_holds_lock_across_delete)


abort_protected = Fixture("abort-protected")
assert abort_protected.init().returncode == 0
abort_protected.worktree.joinpath("fixture.txt").write_text("verified edit\n", encoding="utf-8")
assert abort_protected.verify_reject().returncode == 10
check("receipt-bound rejected work cannot enter dispatch abort", lambda: abort_protected.abort(discard=True).returncode == 64 and abort_protected.worktree.exists())
clean_fixture(abort_protected)


passed_fixture = Fixture("passed")
assert passed_fixture.init().returncode == 0
passed_fixture.worktree.joinpath("fixture.txt").write_text("worker edit\n", encoding="utf-8")
passed_fixture.write_envelope()
passed_verify = run_cli("verify", "--state", str(passed_fixture.state), "--receipt", str(passed_fixture.receipt), "--envelope", str(passed_fixture.envelope), "--only", "fixture.txt", "--expect-edits", "--verify", "true")
check("gate-passed state is retained and never cleanup-eligible", lambda: passed_verify.returncode == 0 and passed_fixture.value()["phase"] == "verified-gate-passed" and passed_fixture.cleanup().returncode == 64)
preserve = run_cli("preserve-instructions", "--state", str(passed_fixture.state))
check("passed state returns instructions without executing them", lambda: preserve.returncode == 0 and b"git -C" in preserve.stdout and passed_fixture.worktree.exists())
clean_fixture(passed_fixture)


def state_cas_pair() -> bool:
    parent = TMP / "state-cas"
    parent.mkdir(mode=0o700)
    path = parent / "state.json"
    value = dict(original)
    value["job_id"] = "cas-job"
    store = MODULE.StateStore(path, initial=True)
    try:
        first = store.create(value)
        raw = path.read_bytes()
        disk = json.loads(raw)
        disk["job_id"] = "attacker"
        path.write_bytes(canonical(disk)); path.chmod(0o600)
        rejected = rejects(lambda: store.update({"phase": "verifying"}))
        return first == hashlib.sha256(raw).hexdigest() and rejected
    finally:
        store.close()


check("state compare-and-swap rejects attacker byte replacement", state_cas_pair)


def state_parent_swap_pair() -> bool:
    parent = TMP / "state-parent-swap"
    parent.mkdir(mode=0o700)
    path = parent / "state.json"
    value = dict(original); value["job_id"] = "parent-swap-job"
    store = MODULE.StateStore(path, initial=True)
    moved = TMP / "state-parent-original"
    try:
        store.create(value)
        parent.rename(moved)
        parent.mkdir(mode=0o700)
        return rejects(lambda: store.update({"phase": "verifying"})) and (moved / "state.json").is_file()
    finally:
        store.close()


check("state operations reject parent path inode replacement", state_parent_swap_pair)


def state_history_and_fsync_pair() -> bool:
    parent = TMP / "state-history"
    parent.mkdir(mode=0o700)
    path = parent / "state.json"
    value = dict(original)
    value["job_id"] = "history-job"
    store = MODULE.StateStore(path, initial=True)
    original_fsync = MODULE.os.fsync
    original_replace = MODULE.os.replace
    try:
        first = store.create(value)
        store.update({"phase": "verifying", "last_result": None, "failure": None})
        second_value = store.value
        assert second_value is not None
        history_ok = second_value["sequence"] == value["sequence"] + 1 and second_value["previous_state_sha256"] == first
        installed = False
        failed_once = False

        def replacing(*args: Any, **kwargs: Any) -> None:
            nonlocal installed
            original_replace(*args, **kwargs)
            installed = True

        def failing_parent_fsync(descriptor: int) -> None:
            nonlocal failed_once
            if installed and descriptor == store.parent_fd and not failed_once:
                failed_once = True
                raise OSError("synthetic parent fsync failure")
            original_fsync(descriptor)

        MODULE.os.replace = replacing
        MODULE.os.fsync = failing_parent_fsync
        rejected = rejects(lambda: store.update({"phase": "verify-failed", "last_result": 1, "failure": "verify-failed"}))
        disk = json.loads(path.read_bytes())
        return rejected and failed_once and history_ok and disk["phase"] == "verify-failed" and store.value == disk and store.raw == path.read_bytes()
    finally:
        MODULE.os.fsync = original_fsync
        MODULE.os.replace = original_replace
        store.close()


check("installed state remains truthful when parent fsync reports failure", state_history_and_fsync_pair)


resume_fixture = Fixture("resume")
assert resume_fixture.init().returncode == 0
resume_fixture.worktree.joinpath("fixture.txt").write_text("worker edit\n", encoding="utf-8")
assert resume_fixture.verify_reject().returncode == 10
resume_store = MODULE.StateStore(resume_fixture.state)
resume_store.update({"phase": "cleanup-in-progress", "failure": None, "last_result": None})
resume_store.close()
git(resume_fixture.repo, "worktree", "remove", "--force", str(resume_fixture.worktree))
reconcile = resume_fixture.cleanup()
check("resume reconciles removed worktree without spending stale approval on ref", lambda: reconcile.returncode == 74 and resume_fixture.value()["cleanup_step"] == "worktree-removed" and git(resume_fixture.repo, "rev-parse", "--verify", f"refs/heads/{resume_fixture.branch}", check_result=False) != b"")
resume = resume_fixture.cleanup()
check("fresh approval resumes compare-delete after reconciled state", lambda: resume.returncode == 0 and resume_fixture.value()["phase"] == "cleaned")


fatal_ref_fixture = Fixture("fatal-ref")
assert fatal_ref_fixture.init().returncode == 0
fatal_ref_fixture.worktree.joinpath("fixture.txt").write_text("worker edit\n", encoding="utf-8")
assert fatal_ref_fixture.verify_reject().returncode == 10
fatal_state = fatal_ref_fixture.value()
fatal_argv = [
    "cleanup",
    "--state", str(fatal_ref_fixture.state),
    "--approve-job", fatal_ref_fixture.job_id,
    "--approve-state-sha", fatal_ref_fixture.state_sha(),
    "--approve-candidate-sha", fatal_state["receipt"]["final_candidate_state_sha256"],
]
original_ref_value = MODULE.ref_value
fatal_observation_raised = False


def fatal_after_ref_delete(repo: Path, reference: str) -> str | None:
    global fatal_observation_raised
    value = original_ref_value(repo, reference)
    if value is None and not fatal_observation_raised:
        fatal_observation_raised = True
        raise MODULE.GitError("synthetic fatal ref verification")
    return value


try:
    MODULE.ref_value = fatal_after_ref_delete
    fatal_result = MODULE.main(fatal_argv)
finally:
    MODULE.ref_value = original_ref_value
fatal_after = fatal_ref_fixture.value()
check(
    "fatal post-delete ref verification stays cleanup-in-progress",
    lambda: (
        fatal_result == 74
        and fatal_after["phase"] == "cleanup-in-progress"
        and fatal_after["cleanup_step"] == "worktree-removed"
        and original_ref_value(fatal_ref_fixture.repo, fatal_after["branch_ref"]) is None
    ),
)


def subprocess_signal(signum: int) -> bool:
    marker = TMP / f"signal-{signum}.marker"
    child_code = (
        "import pathlib,signal,time,sys;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "time.sleep(4);pathlib.Path(sys.argv[1]).write_text('late')"
    )
    script = (
        "import importlib.util,os,signal,sys,threading,time;"
        f"p={str(MODULE_PATH)!r};"
        "s=importlib.util.spec_from_file_location('life_signal',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);"
        "[signal.signal(n,m._interrupt) for n in m.SIGNALS];"
        f"threading.Timer(.2,lambda:os.kill(os.getpid(),{signum})).start();"
        f"\ntry:m.run_process([{PYTHON!r},'-I','-S','-B','-c',{child_code!r},{str(marker)!r}])\nexcept m.JobSignal as e:raise SystemExit(128+e.number)"
    )
    result = subprocess.run([PYTHON, "-I", "-S", "-B", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
    time.sleep(0.2)
    return result.returncode == 128 + signum and not marker.exists()


for lifecycle_signal in MODULE.SIGNALS:
    check(f"signal {lifecycle_signal} terminates child group without late side effect", lambda lifecycle_signal=lifecycle_signal: subprocess_signal(lifecycle_signal))


def completion_signal(signum: int) -> bool:
    script = (
        "import importlib.util,os,signal,sys;"
        f"p={str(MODULE_PATH)!r};"
        "s=importlib.util.spec_from_file_location('life_complete',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);"
        "\ndef fake():\n m.FIRST_SIGNAL=None\n [signal.signal(n,m._interrupt) for n in m.SIGNALS]\n return 0\n"
        "\nclass W:\n def __init__(self,x):self.x=x;self.sent=False\n def write(self,v):return self.x.write(v)\n def flush(self):\n  if not self.sent:self.sent=True;os.kill(os.getpid()," + str(int(signum)) + ")\n  return self.x.flush()\n"
        "\nm.main=fake;sys.stdout=W(sys.stdout);m.process_main()"
    )
    result = subprocess.run([PYTHON, "-I", "-S", "-B", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3, check=False)
    if result.returncode != 128 + signum:
        raise AssertionError(f"rc={result.returncode} stderr={result.stderr[:1000]!r}")
    return True


for lifecycle_signal in MODULE.SIGNALS:
    check(f"signal {lifecycle_signal} after command return wins before process exit", lambda lifecycle_signal=lifecycle_signal: completion_signal(lifecycle_signal))


def distinct_second_signal(module_path: Path, label: str) -> tuple[int, bool, bool, bool]:
    marker = TMP / f"double-signal-{label}.late"
    cleanup_marker = TMP / f"double-signal-{label}.cleanup"
    ready_marker = TMP / f"double-signal-{label}.ready"
    child_code = (
        "import os,pathlib,signal,time,sys;"
        "signal.signal(signal.SIGTERM,lambda *_:(pathlib.Path(sys.argv[2]).write_text('cleanup'),os.kill(os.getppid(),signal.SIGTERM)));"
        "ready=pathlib.Path(sys.argv[3]);ready.write_text('ready');ready.chmod(0o600);"
        "signal.pthread_sigmask(signal.SIG_UNBLOCK,(signal.SIGTERM,));"
        "time.sleep(4);pathlib.Path(sys.argv[1]).write_text('late')"
    )
    script = (
        "import importlib.util,os,signal,stat,sys,threading,time;"
        f"sys.path.insert(0,{str(MODULE_PATH.parent)!r});"
        f"p={str(module_path)!r};s=importlib.util.spec_from_file_location('life_double',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);"
        "[signal.signal(n,m._interrupt) for n in m.SIGNALS];"
        "\ndef arm():\n deadline=time.monotonic()+1\n while time.monotonic()<deadline:\n  try:\n   info=os.stat(sys.argv[1]);ready=open(sys.argv[1]).read()=='ready' and stat.S_IMODE(info.st_mode)==0o600\n  except OSError: ready=False\n  if ready: os.kill(os.getpid(),signal.SIGHUP);return\n  time.sleep(.005)\n os._exit(97)\n"
        "threading.Thread(target=arm,daemon=True).start();"
        f"\ntry:m.run_process([{PYTHON!r},'-I','-S','-B','-c',{child_code!r},{str(marker)!r},{str(cleanup_marker)!r},{str(ready_marker)!r}])\nexcept m.JobSignal as e:raise SystemExit(128+e.number)"
        f"\nif __name__=='__main__': pass\n"
    )
    result = subprocess.run([PYTHON, "-I", "-S", "-B", "-c", script, str(ready_marker)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
    time.sleep(0.2)
    try:
        ready = ready_marker.read_text() == "ready" and stat.S_IMODE(ready_marker.stat().st_mode) == 0o600
    except OSError:
        ready = False
    return result.returncode, ready, cleanup_marker.exists(), marker.exists()


secure_signal_result = distinct_second_signal(MODULE_PATH, "secure")
check(
    "distinct second signal cannot replace first exit or interrupt cleanup",
    lambda: secure_signal_result == (129, True, True, False),
)

signal_mutant = TMP / "job-lifecycle-signal-overwrite.py"
signal_source = MODULE_PATH.read_text(encoding="utf-8")
signal_guard = "    if FIRST_SIGNAL is not None:\n        return\n"
assert signal_source.count(signal_guard) == 1
signal_mutant.write_text(
    signal_source.replace(
        signal_guard,
        "    if FIRST_SIGNAL is not None:\n"
        "        FIRST_SIGNAL = number\n"
        "        raise JobSignal(number)\n",
        1,
    ),
    encoding="utf-8",
)
signal_mutant.chmod(0o600)
mutated_signal_result = distinct_second_signal(signal_mutant, "mutant")
check(
    "first-signal overwrite mutation is killed by cleanup-entry handshake",
    lambda: mutated_signal_result == (143, True, True, False),
)


def default_term_cleanup() -> bool:
    process = subprocess.Popen(
        [PYTHON, "-I", "-S", "-B", "-c", "import time; time.sleep(4)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    MODULE._terminate_process(process)
    return process.returncode is not None


check("default-TERM child is reaped by the sole lifecycle wait", default_term_cleanup)


def bounded_git_output_contract() -> bool:
    small = subprocess.Popen(
        [PYTHON, "-I", "-S", "-B", "-c", "import sys;sys.stdout.buffer.write(b'x'*64)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    accepted = MODULE._communicate_bounded(small, None, 64, 2.0) == b"x" * 64
    oversized = subprocess.Popen(
        [PYTHON, "-I", "-S", "-B", "-c", "import sys;sys.stdout.buffer.write(b'x'*4096)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        MODULE._communicate_bounded(oversized, None, 1024, 2.0)
    except MODULE.GitOutputLimitError:
        MODULE._terminate_process(oversized)
        rejected = oversized.returncode is not None
    else:
        MODULE._terminate_process(oversized)
        rejected = False
    repo, _base = make_repo("bounded-git-output")
    (repo / "large.txt").write_bytes(b"y" * 4096)
    git(repo, "add", "large.txt")
    git(repo, "commit", "-qm", "large")
    prior_maximum = MODULE.MAX_GIT_OUTPUT
    try:
        MODULE.MAX_GIT_OUTPUT = 1024
        try:
            MODULE.git_result(repo, "show", "HEAD:large.txt")
        except MODULE.GitOutputLimitError:
            integrated = MODULE.ACTIVE_PROCESS is None
        else:
            integrated = False
    finally:
        MODULE.MAX_GIT_OUTPUT = prior_maximum
    return accepted and small.returncode == 0 and rejected and integrated


check("Git stdout is bounded while the child is still running", bounded_git_output_contract)


def permission_error_cleanup(raise_on: int) -> bool:
    class FakeProcess:
        pid = 424242
        returncode: int | None = None

        def __init__(self) -> None:
            self.wait_calls = 0

        def wait(self, timeout: float) -> int:
            self.wait_calls += 1
            self.returncode = 0
            return 0

    process = FakeProcess()
    calls: list[tuple[int, int]] = []
    original_killpg = MODULE.os.killpg
    original_sleep = MODULE.time.sleep

    def injected_killpg(pgid: int, signum: int) -> None:
        calls.append((pgid, signum))
        if len(calls) == raise_on:
            raise PermissionError("synthetic denied group signal")

    try:
        MODULE.os.killpg = injected_killpg
        MODULE.time.sleep = lambda _seconds: None
        MODULE._terminate_process(process)
    finally:
        MODULE.os.killpg = original_killpg
        MODULE.time.sleep = original_sleep
    return (
        calls == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
        and process.wait_calls == 1
        and process.returncode == 0
    )


for permission_error_call in (1, 2):
    check(
        f"PermissionError on process-group signal {permission_error_call} still reaps once",
        lambda permission_error_call=permission_error_call: permission_error_cleanup(permission_error_call),
    )


test_source = Path(__file__).read_bytes()


def distinct_second_signal_contract(data: bytes) -> bool:
    start = data.index(b"def distinct_second_signal")
    end = data.index(b"\n\nsecure_signal_result", start)
    body = data[start:end]
    handler = b"signal.signal(signal.SIGTERM,lambda *_:(pathlib.Path(sys.argv[2]).write_text('cleanup'),os.kill(os.getppid(),signal.SIGTERM)));"
    unblock = b"signal.pthread_sigmask(signal.SIG_UNBLOCK,(signal.SIGTERM,));"
    return (
        handler in body
        and unblock in body
        and body.index(handler) < body.index(unblock)
        and b"ready.chmod(0o600);" in body
        and b"time.monotonic()+1" in body
        and b"while time.monotonic()<deadline:" in body
        and b"threading.Timer" not in body
    )


handshake_without_private_ready = test_source.replace(b"ready.chmod(0o600);", b"", 1)
handshake_without_bounded_wait = test_source.replace(
    b"time.monotonic()+1", b"time.monotonic()", 1
)
handshake_unblock_before_handler = test_source.replace(
    b'        "signal.signal(signal.SIGTERM,lambda *_:(pathlib.Path(sys.argv[2]).write_text(\'cleanup\'),os.kill(os.getppid(),signal.SIGTERM)));"\n'
    b'        "ready=pathlib.Path(sys.argv[3]);ready.write_text(\'ready\');ready.chmod(0o600);"\n'
    b'        "signal.pthread_sigmask(signal.SIG_UNBLOCK,(signal.SIGTERM,));"\n',
    b'        "signal.pthread_sigmask(signal.SIG_UNBLOCK,(signal.SIGTERM,));"\n'
    b'        "signal.signal(signal.SIGTERM,lambda *_:(pathlib.Path(sys.argv[2]).write_text(\'cleanup\'),os.kill(os.getppid(),signal.SIGTERM)));"\n'
    b'        "ready=pathlib.Path(sys.argv[3]);ready.write_text(\'ready\');ready.chmod(0o600);"\n',
    1,
)
check(
    "second-signal proof requires bounded private readiness and handler-before-unblock",
    lambda: (
        distinct_second_signal_contract(test_source)
        and handshake_without_private_ready != test_source
        and not distinct_second_signal_contract(handshake_without_private_ready)
        and handshake_without_bounded_wait != test_source
        and not distinct_second_signal_contract(handshake_without_bounded_wait)
        and handshake_unblock_before_handler != test_source
        and not distinct_second_signal_contract(handshake_unblock_before_handler)
    ),
)


source = MODULE_PATH.read_bytes()
check(
    "child stderr is suppressed and only bounded Git stdout may be captured",
    lambda: (
        source.count(b"stderr=subprocess.DEVNULL") >= 2
        and b"stdout=subprocess.PIPE if capture else subprocess.DEVNULL" in source
        and b"_communicate_bounded(" in source
        and b"if capture and len(stdout) > MAX_GIT_OUTPUT:" not in source
    ),
)
check("compare-delete uses exact ref and expected base", lambda: b'"update-ref", "-d", state["branch_ref"], state["base"]' in source)
check("cleanup never uses branch force delete", lambda: b"branch -D" not in source and b'"branch", "-D"' not in source)
check("status hard-codes zero cleanup authority", lambda: source.count(b'"cleanup_authorized": False') >= 2)
check("worker command claims are never executed by lifecycle", lambda: b"commands_run" not in source and b"tests_run" not in source)


def branch_contract(data: bytes) -> bool:
    start = data.index(b"def validate_branch")
    end = data.index(b"\ndef validate_base", start)
    body = data[start:end]
    return (
        b"canonical_branch_syntax(branch)" in body
        and b'"check-ref-format", "--branch", branch' in body
        and b'expected = branch.encode("utf-8", "strict") + b"\\n"' in body
        and b"if completed.stdout != expected:" in body
        and b'return f"refs/heads/{branch}"' in body
    )


check("branch authority binds exact canonical check-ref stdout", lambda: branch_contract(source))
mutated = source.replace(b"    if completed.stdout != expected:\n", b"    if False:\n", 1)
check("mutation accepting check-ref canonicalization is killed", lambda: not branch_contract(mutated))


def git_policy_contract(data: bytes) -> bool:
    start = data.index(b"def _git_environment")
    end = data.index(b"\ndef canonical_repo", start)
    body = data[start:end]
    initial = data[data.index(b"def initial_state"):data.index(b"\ndef command_init")]
    return (
        b'"GIT_CONFIG_NOSYSTEM": "1"' in body
        and b'"GIT_CONFIG_GLOBAL": "/dev/null"' in body
        and b'"GIT_TERMINAL_PROMPT": "0"' in body
        and b'"GIT_EXTERNAL_DIFF": ""' in body
        and b'"-c", f"core.hooksPath={hooks}"' in body
        and b'"-c", "core.fsmonitor=false"' in body
        and b'"-c", "protocol.allow=never"' in body
        and b"validate_safe_git_checkout(repo, args.base)" in initial
        and initial.index(b"validate_safe_git_checkout(repo, args.base)")
        < initial.index(b"if ref_value(repo, branch_ref) is not None:")
        and data.count(b"subprocess.run(") == 0
    )


check("all lifecycle Git runs share fixed no-external execution policy", lambda: git_policy_contract(source))
for old, new, label in (
    (b'"-c", f"core.hooksPath={hooks}"', b'"-c", "core.hooksPath=.git/hooks"', "hooks override"),
    (b'"GIT_CONFIG_GLOBAL": "/dev/null"', b'"GIT_CONFIG_GLOBAL": os.environ.get("GIT_CONFIG_GLOBAL", "/dev/null")', "global config isolation"),
    (b"    validate_safe_git_checkout(repo, args.base)\n", b"    pass\n", "checkout preflight"),
):
    mutated = source.replace(old, new, 1)
    check(f"mutation removing {label} is killed", lambda mutated=mutated: not git_policy_contract(mutated))


def checkout_preflight_contract(data: bytes) -> bool:
    start = data.index(b"def validate_safe_git_checkout")
    end = data.index(b"\ndef canonical_repo", start)
    body = data[start:end]
    return (
        b'"config",\n        "--local",\n        "--includes"' in body
        and b"filter\\..*\\.(clean|smudge|process|required)" in body
        and b'"check-attr",\n        f"--source={base}"' in body
        and b"if configured.returncode == 0:" in body
        and b'input_data=b"\\0".join(paths) + b"\\0"' in body
        and b'record[2] not in {b"unspecified", b"unset"}' in body
        and b"MAX_ATTRIBUTE_PATH_BYTES" in body
        and b"MAX_DELETE_NODES" in body
    )


check("checkout preflight binds config and effective filter attributes", lambda: checkout_preflight_contract(source))
for old, new, label in (
    (b'            or record[2] not in {b"unspecified", b"unset"}\n', b"            or False\n", "attribute rejection"),
    (b"    if configured.returncode == 0:\n", b"    if False:\n", "external config rejection"),
):
    mutated = source.replace(old, new, 1)
    check(f"mutation removing {label} is killed", lambda mutated=mutated: not checkout_preflight_contract(mutated))

def candidate_policy_contract(data: bytes) -> bool:
    return data.count(b"git_reader=git") == 8


check("lifecycle candidate digest uses the fixed Git policy reader", lambda: candidate_policy_contract(source))
mutated = source.replace(b", git_reader=git", b"", 1)
check(
    "mutation bypassing fixed Git reader is killed",
    lambda: not candidate_policy_contract(mutated),
)


def ref_contract(data: bytes) -> bool:
    start = data.index(b"def ref_value")
    end = data.index(b"\ndef _config_key", start)
    body = data[start:end]
    cleanup = data[data.index(b"def command_cleanup"):data.index(b"\ndef command_abort")]
    abort = data[data.index(b"def command_abort"):data.index(b"\ndef status_facts")]
    return (
        b'"show-ref", "--verify", "--quiet", reference' in body
        and b"if existence.returncode == 1:" in body
        and b"if existence.returncode != 0:" in body
        and b"returncode == 128" not in body
        and b'if ref_value(repo, state["branch_ref"]) is not None:' in cleanup
        and b"except GitError:" in cleanup
        and b"except GitError:" in abort
        and b"Do not retry/reconcile it into `cleaned`" in cleanup
        and b"except JobError:\n                pass" not in cleanup
        and b"except JobError:\n                pass" not in abort
    )


check("ref absence accepts only documented show-ref rc1", lambda: ref_contract(source))
mutated = source.replace(
    b"    if existence.returncode == 1:\n",
    b"    if existence.returncode in {1, 128}:\n",
    1,
)
check("mutation treating fatal rc128 as absence is killed", lambda: not ref_contract(mutated))
mutated = source.replace(b"    except GitError:\n", b"    except OSError:\n", 1)
check("mutation allowing same-invocation fatal ref reconciliation is killed", lambda: not ref_contract(mutated))


def process_exit_contract(data: bytes) -> bool:
    start = data.index(b"def process_main()")
    end = data.index(b'\n\nif __name__ == "__main__":', start)
    body = data[start:end]
    return (
        body.count(b"os._exit(result)") == 1
        and body.count(b"sys.stdout.flush()") == 2
        and body.count(b"sys.stderr.flush()") == 2
        and b"except JobSignal as exc:" in body
        and b"result = 128 + exc.number" in body
        and b"\n    return" not in body
        and b"signal.signal(number, handler)" not in data
    )


check("CLI owns handlers through flush and atomic process exit", lambda: process_exit_contract(source))
mutated = source.replace(b"    os._exit(result)\n", b"    return\n", 1)
check("mutation removing atomic process exit is killed", lambda: not process_exit_contract(mutated))


def process_group_contract(data: bytes) -> bool:
    start = data.index(b"def _terminate_process")
    end = data.index(b"\ndef run_process", start)
    body = data[start:end]
    wait = body.index(b"process.wait(")
    return (
        body.count(b"os.killpg(") == 2
        and body.index(b"os.killpg(process.pid, signal.SIGTERM)") < wait
        and body.index(b"os.killpg(process.pid, signal.SIGKILL)") < wait
        and b"os.killpg(" not in body[wait:]
        and b"process.poll(" not in body
    )


check("process-group authority is used only before leader reap", lambda: process_group_contract(source))
mutated = source.replace(b"        process.wait(timeout=0.75)\n", b"        process.wait(timeout=0.75)\n        os.killpg(process.pid, 0)\n", 1)
check("mutation adding post-reap process-group probe is killed", lambda: not process_group_contract(mutated))


def process_group_error_contract(data: bytes) -> bool:
    start = data.index(b"def _terminate_process")
    end = data.index(b"\ndef run_process", start)
    body = data[start:end]
    return body.count(b"except (ProcessLookupError, PermissionError):") == 2


permission_error_mutant = source.replace(
    b"except (ProcessLookupError, PermissionError):", b"except ProcessLookupError:", 1
)
check(
    "process-group cleanup keeps PermissionError non-authoritative before sole reap",
    lambda: (
        process_group_error_contract(source)
        and permission_error_mutant != source
        and not process_group_error_contract(permission_error_mutant)
    ),
)


def state_fsync_contract(data: bytes) -> bool:
    start = data.index(b"class StateStore")
    end = data.index(b"\ndef _write_all", start)
    body = data[start:end]
    create = body[body.index(b"    def create"):body.index(b"    def update")]
    update = body[body.index(b"    def update"):]
    return (
        body.count(b"os.fsync(descriptor)") == 2
        and body.count(b"os.fsync(self.parent_fd)") == 3
        and create.index(b"os.fsync(descriptor)") < create.index(b"self.raw, self.metadata") < create.index(b"os.fsync(self.parent_fd)")
        and update.index(b"os.fsync(descriptor)") < update.index(b"os.replace(") < update.index(b"self.raw, self.metadata") < update.index(b"os.fsync(self.parent_fd)")
        and update.index(b"os.unlink(temporary") < update.rindex(b"os.fsync(self.parent_fd)")
    )


check("state publication binds file and parent durability calls", lambda: state_fsync_contract(source))
mutated = source.replace(b"                os.fsync(descriptor)\n", b"                pass\n", 1)
check("mutation removing staged state fsync is killed", lambda: not state_fsync_contract(mutated))
mutated = source.replace(b"            os.fsync(self.parent_fd)\n", b"            pass\n", 1)
check("mutation removing initial parent fsync is killed", lambda: not state_fsync_contract(mutated))

clean_fixture(fixture)
shutil.rmtree(TMP)
print(f"job lifecycle offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
