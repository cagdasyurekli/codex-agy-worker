#!/usr/bin/env python3
"""Focused regression tests for local tracked and untracked whitespace hygiene."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Callable

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "ci-worktree-check.sh"

passed = 0
failed = 0


def check(label: str, assertion: Callable[[], bool] | bool) -> None:
    global passed, failed
    try:
        result = assertion() if callable(assertion) else assertion
    except Exception as exc:
        print(f"  EXC  {label}: {exc}")
        result = False
    if result:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


def run(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def initialize(repo: Path) -> None:
    run(repo, "git", "init", "-q")
    run(repo, "git", "config", "user.name", "test")
    run(repo, "git", "config", "user.email", "test@example.invalid")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored-bad.txt\n", encoding="utf-8")
    assert run(repo, "git", "add", "--", "tracked.txt", ".gitignore").returncode == 0
    assert run(repo, "git", "commit", "-qm", "base").returncode == 0


def index_snapshot(repo: Path) -> bytes:
    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0
    return result.stdout


def run_check(repo: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return run(repo, "bash", str(CHECK), env=env)


with tempfile.TemporaryDirectory(prefix="agyworker-ci-worktree-test-") as directory:
    repo = Path(directory) / "repo"
    repo.mkdir()
    initialize(repo)

    safe_name = "-candidate with spaces\nand-newline.txt"
    candidate = repo / safe_name
    candidate.write_text("clean candidate\n", encoding="utf-8")
    (repo / "ignored-bad.txt").write_text("ignored trailing whitespace   \n", encoding="utf-8")
    before = index_snapshot(repo)
    clean = run_check(repo)
    check(
        "clean non-ignored untracked paths are accepted without index mutation",
        clean.returncode == 0 and index_snapshot(repo) == before and candidate.read_text(encoding="utf-8") == "clean candidate\n",
    )

    candidate.write_text("bad trailing whitespace   \n", encoding="utf-8")
    before = index_snapshot(repo)
    bad = run_check(repo)
    check(
        "NUL-safe leading-dash newline filename with trailing whitespace is rejected without staging or deletion",
        bad.returncode != 0
        and "trailing whitespace" in bad.stdout + bad.stderr
        and index_snapshot(repo) == before
        and candidate.read_text(encoding="utf-8") == "bad trailing whitespace   \n",
    )

    candidate.unlink()
    before = index_snapshot(repo)
    ignored_only = run_check(repo)
    check(
        "ignored untracked files remain out of scope",
        ignored_only.returncode == 0
        and index_snapshot(repo) == before
        and (repo / "ignored-bad.txt").read_text(encoding="utf-8") == "ignored trailing whitespace   \n",
    )

    fake_bin = repo / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == ls-files ]]; then exit 42; fi\n"
        "exec /usr/bin/git \"$@\"\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    failure_env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
    listing_failure = run_check(repo, failure_env)
    check("untracked enumeration failure is visible", listing_failure.returncode == 42)

check("helper is a regular shell script", CHECK.is_file())
print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
