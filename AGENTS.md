# AGENTS.md — working on codex-agy-worker

Read `README.md` first. Use `docs/REPO_MAP.md` for ownership and data flow, and
`docs/lessons_learned.md` for the architectural mistakes this project must not
repeat.

## The one principle

This project's entire value is that **it does not trust a worker's self-report**.
Any change that makes `qa-gate.sh` more trusting is a regression, even if it makes
tests pass or output prettier. If you find yourself weakening a check to get a green
run, stop — the failing check is probably right.

Corollary for your own work: do not report a task done because it looks done. Run
the completion checks below and report their exact summaries.

After material changes to commands, architecture, trust boundaries, tests, or
verified/untested claims, use the `agents-md-auditor` skill before declaring
completion. Re-read the effective instruction hierarchy, keep this root file
concise and repository-wide, and keep release notes or one-off run history out of
`AGENTS.md`.

## Ground truth about agy

Never describe agy's CLI from memory — including your own. Run `./ground-truth.sh`
and use its output. The project exists because models asked to describe agy's
interface invented `agy run`, `--headless`, `--slim`, and `agy auth status --json`.
None exist. Verify with `agy --help` before writing any flag into code.

Unknown agy subcommands exit 0 and print usage, so you cannot probe by exit code.

## Evidence boundaries

Keep these counts current when their suites change:

- `qa-gate.sh`: 41 offline cases.
- `agy-worker.sh` / `install.sh`: 22 offline fake-agy cases.
- `update.sh`: 26 offline local-remote cases.
- `bug-report.sh`: 21 offline privacy/fake-`gh` cases.

Real runs prove one bounded edit, the complete `codex exec` pipeline, the combined
Codex sandbox requirements, and an honest `repo-inventory` escalation. The
Playbook-Gemini exercise proved that the gate rejects a worker even after focused
tests pass when diff hygiene fails; it did not produce an accepted
`bulk-test-writer` delivery.

Do not claim real coverage for an accepted `bulk-test-writer` delivery,
`diff-reviewer`, non-`bulk` tiers, oversized live dispatch, Windows, headless skill
expansion, a public tagged update, or a live GitHub issue submission. Their current
coverage is offline, partial, or absent as described in `README.md`.

## Testing

```bash
./tests/test-qa-gate.sh        # offline, no agy, no network — must stay that way
./tests/test-agy-worker.sh      # offline fake-agy dispatcher/installer coverage
./tests/test-update.sh          # offline local Git remotes; no public fetch
./tests/test-reporting.sh       # offline fake-gh privacy/submission coverage
bash -n ./*.sh tests/*.sh      # syntax
python3 -m py_compile scripts/*.py
git diff --check
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
- **Never execute `commands_run` or `tests_run` from the envelope.** They are worker
  claims. The gate accepts executable input only through driver-owned `--verify`.
- **`update.sh check` may need network, but must remain read-only.** Compatibility
  metadata changes only after a human reconciles official docs, upstream source,
  `ground-truth.sh`, and a bounded real job. Production origins, upstream, and review
  cadence are not environment-overridable. `apply` is always an explicit action.
- **Bug reports are local drafts first.** Never gather prompts, source, envelopes,
  paths, credentials, or raw logs automatically. Submission must show the exact body
  and require the matching SHA-256 confirmation token; send those validated bytes,
  not a mutable path, to an explicitly bound github.com destination.

## Boundaries

- Do not weaken `qa-gate.sh` checks to make something pass.
- Do not add `--dangerously-skip-permissions` or
  `--dangerously-bypass-approvals-and-sandbox` anywhere, or recommend them in docs.
  Narrow allow-rules, or restructure so the worker uses file tools.
- Do not modify the user's `~/.gemini/` or `~/.codex/` config as part of a code change.
  Document what the user should change; let them do it.
- Do not add a runtime dependency (Node, Bun, a package manager). Bash + Python 3 +
  git is the whole point; competing projects already occupy the MCP-server niche.
- Do not auto-pull during a worker job, auto-submit an issue, install `gh`, or make
  GitHub CLI a runtime dependency.
- Do not overstate the project in README. It is one differentiated idea among several
  existing tools, and the prior-art section stays.
- Ask before pushing to `main` or publishing a release.
