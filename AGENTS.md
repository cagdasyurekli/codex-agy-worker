# AGENTS.md — working on codex-agy-worker

You are continuing development of this repo. Read `README.md` first; it is accurate
and current. This file covers what the README deliberately leaves out: what is
actually proven, what is merely written, and what to do next.

## The one principle

This project's entire value is that **it does not trust a worker's self-report**.
Any change that makes `qa-gate.sh` more trusting is a regression, even if it makes
tests pass or output prettier. If you find yourself weakening a check to get a green
run, stop — the failing check is probably right.

Corollary for your own work: do not report a task done because it looks done. Run
`./tests/test-qa-gate.sh` and paste the output.

## Ground truth about agy

Never describe agy's CLI from memory — including your own. Run `./ground-truth.sh`
and use its output. The project exists because models asked to describe agy's
interface invented `agy run`, `--headless`, `--slim`, and `agy auth status --json`.
None exist. Verify with `agy --help` before writing any flag into code.

Unknown agy subcommands exit 0 and print usage, so you cannot probe by exit code.

## What is actually verified vs merely written

**Verified by running it (trust these):**
- `qa-gate.sh` — all 13 offline tests, including 4 adversarial rejection cases.
- `agy-worker.sh` — real `accept-edits` job fixed a real bug end-to-end; envelope
  parsed; bounded retry fired on a genuine transient auth failure.
- Full pipeline driven by `codex exec` under `workspace-write`, accepted by the gate.
- Codex sandbox config: `network_access = true` **and** `--add-dir ~/.gemini`. Each
  alone fails with exit 5 and empty stderr.
- `repo-inventory` persona: measurably changed behaviour (returned `blocked` /
  `requires_human` on an under-specified job instead of confabulating).
- `install.sh` against a fake `CODEX_SKILLS_DIR`.

**Written but NEVER exercised (do not claim these work):**
- `bulk-test-writer` and `diff-reviewer` personas — never run against a real job.
- `AGY_WORKER_TIER` values other than `bulk`.
- The oversized-prompt branch in `agy-worker.sh` (>100 KB → staged to file + `--add-dir`).
- `QA_EXPECT_EDITS=1` → exit 13 path.
- Windows anything.
- Headless skill expansion, `agy -p "/skill-name ..."`.

## Backlog, highest value first

1. **Exercise `bulk-test-writer` on a real repo.** Give it a module with untested
   error paths in a throwaway worktree. It must write tests ONLY under a test dir —
   confirm with `qa-gate.sh` that it touched no production file. If it edits
   production source, tighten the persona rather than loosening the gate.
2. **Exercise `diff-reviewer`.** Plant a real defect (a suppressed exception, an
   `assert True`, a hardcoded value that passes the current case) in a worktree diff
   and check it is found. It must report findings without editing anything.
3. **Test the oversized-prompt branch.** Generate a >100 KB task, confirm agy is
   pointed at the staged file and still returns a valid envelope.
4. **Test `QA_EXPECT_EDITS=1`** → a worker claiming `completed` with an empty diff
   must exit 13. Add it to `tests/test-qa-gate.sh`.
5. **Flip shellcheck to blocking** in `.github/workflows/test.yml` once it passes
   (it is `continue-on-error: true` only because it was never run locally).
6. **Test `agy -p "/skill-name ..."`.** If headless skill expansion works, a fourth
   integration approach opens up (agy's own skill system instead of prompt injection).
   If it does not, say so in the README's limitations.

## Testing

```bash
./tests/test-qa-gate.sh        # offline, no agy, no network — must stay that way
bash -n *.sh tests/*.sh        # syntax
./ground-truth.sh              # regenerate agy facts before touching agy behaviour
```

Keep the suite offline. It is what makes CI work on any box and what lets a
contributor verify the gate without an agy install or API credits.

When adding a gate check, add both an accept case and a reject case. A check with
only a passing test has not been shown to catch anything.

## Gotchas that will cost you an hour

- **This shell is zsh.** `PIPESTATUS` is `pipestatus[1]`. A test harness using
  `${PIPESTATUS[0]}` silently reports empty exit codes and every assertion "fails".
- **macOS bash is 3.2** — no `mapfile`, no associative-array conveniences. Use
  `while IFS= read -r` and C-style `for (( ))` loops.
- **agy exits 0 on failure** with empty stdout. Never treat `$? == 0` as success;
  check stripped stdout content.
- **`result.json_schema` is the echoed schema**, not the answer. The answer is
  `result.structured_output`. A parser that searches for "an object with the required
  keys" matches the schema's own `properties` block and hands back your own schema.
- **Workers report absolute paths; git reports relative.** `qa-gate.sh` normalises
  both. If you touch that code, keep the `tests/test-qa-gate.sh` absolute-path case.
- **Under `--sandbox`, agy's shell tools run in `~/.gemini/antigravity-cli/scratch`**,
  not the repo. File tools do reach the repo. Never ask a worker to run shell commands.

## Boundaries

- Do not weaken `qa-gate.sh` checks to make something pass.
- Do not add `--dangerously-skip-permissions` or
  `--dangerously-bypass-approvals-and-sandbox` anywhere, or recommend them in docs.
  Narrow allow-rules, or restructure so the worker uses file tools.
- Do not modify the user's `~/.gemini/` or `~/.codex/` config as part of a code change.
  Document what the user should change; let them do it.
- Do not add a runtime dependency (Node, Bun, a package manager). Bash + Python 3 +
  git is the whole point; competing projects already occupy the MCP-server niche.
- Do not overstate the project in README. It is one differentiated idea among several
  existing tools, and the prior-art section stays.
- Ask before pushing to `main` or publishing a release.
