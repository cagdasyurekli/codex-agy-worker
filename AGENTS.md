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

Probe documented commands and validate their expected semantic content. Do not treat
an unknown subcommand's exit code or generic usage text as compatibility evidence.

## Evidence boundaries

Keep these counts current when their suites change:

- `qa-gate.sh`: 41 offline cases.
- Evidence Receipt v1: 88 offline gate-protocol/publication/privacy cases.
- `agy-worker.sh` / `install.sh` / model selection and recommendation: 209 offline
  fake-agy/routing cases.
- `update.sh`: 175 offline local-remote/matrix/manifest/watch-policy cases.
- `bug-report.sh`: 21 offline privacy/fake-`gh` cases.
- Codex package/skill distribution: 135 offline
  manifest/runtime-copy/relocation/landing cases.
- `doctor.sh`: 180 offline fake-tool/read-only cases.
- `proof-demo.sh`: 21 offline synthetic-boundary cases.

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
./tests/test-evidence-receipt.sh # offline receipt/protocol/publication coverage
./tests/test-agy-worker.sh      # offline fake-agy dispatcher/installer coverage
./tests/test-update.sh          # offline local Git remotes; no public fetch
./tests/test-reporting.sh       # offline fake-gh privacy/submission coverage
./tests/test-packaging.sh       # offline Codex manifest, relocation, policy, landing
./tests/test-doctor.sh          # offline fake-tool/read-only readiness coverage
./tests/test-proof-demo.sh      # offline synthetic pass/reject proof coverage
bash -n ./*.sh tests/*.sh skills/*/scripts/*.sh skills/*/runtime/*.sh  # syntax
(
  AGY_WORKER_PYCACHE="$(mktemp -d -t agyworker-pycache.XXXXXX)" || exit 1
  trap 'rm -rf -- "$AGY_WORKER_PYCACHE"' EXIT
  PYTHONPYCACHEPREFIX="$AGY_WORKER_PYCACHE" \
    python3 -m py_compile scripts/*.py skills/*/runtime/scripts/*.py
)
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
- **`python -B -m py_compile` still writes bytecode.** Route explicit syntax-check
  output through an external `PYTHONPYCACHEPREFIX`; repository bytecode makes the
  public runtime incomplete by design.
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
- **A receipt records the gate; it is not another gate.** Keep `qa-gate.sh` as the
  sole outcome authority, publish only outside the audited repository without
  overwrite, keep its internal evidence FD out of verifier descendants, and strip
  shell/Python startup hooks on the wrapper-bound evidence path. Close that FD in the
  existing gate process before any verifier shell or interpreter starts. Cleanly abort
  the whole receipt operation on HUP/INT/TERM. Receipts remain unsigned and subject
  to human diff review.
- **`update.sh check` may need network, but must remain read-only.** Compatibility
  evidence for agy and Codex is reported as unchanged, drift-review, or
  evidence-unavailable; inconclusive evidence is never green. Metadata changes only
  after a human reconciles official docs, upstream source, command inventories, and a
  bounded real job when behavior changed. Production origins, release channels, and
  review cadence are not environment-overridable. The weekly watcher never advances
  metadata or takes an external action. `apply` is always an explicit action.
- **The agy distribution manifest is only a canary.** Its endpoint is fixed; reject
  proxies, redirects, oversized/malformed responses, and unexpected archive URLs.
  Never request the archive. The checked-in tuple detects drift but cannot advance a
  baseline, prove source/behavior, or activate model/effort resolution.
- **Bug reports are local drafts first.** Never gather prompts, source, envelopes,
  paths, credentials, or raw logs automatically. Submission must show the exact body
  and require the matching SHA-256 confirmation token; send those validated bytes,
  not a mutable path, to an explicitly bound github.com destination.
- **The public skill is self-contained.** Keep the core runtime canonical under
  `skills/agy-worker/runtime/`; repository-root commands are compatibility wrappers.
  Complete plugins and explicit standalone installs may resolve the checkout, while
  skill-folder-only copies use the bundled runtime. Never publish a local
  `.pipeline-root`, bake in a checkout path, or add an automatic fetch fallback.
- **The doctor observes; it never repairs.** Keep it offline and read-only, probe only
  exact semantic version/repository commands, expose no paths or raw output, and do
  not scan personal configuration. Green covers offline prerequisites only—not auth,
  provider, sandbox, task quality, or future dispatch. Portable agy metadata must
  remain byte-identical to the canonical `compat/` records, and the reviewed source
  revision must match the doctor's fixed expected revision without printing its bytes.
  Ignore caller temp paths; use a private external workspace and propagate HUP/INT/TERM
  to the active probe. Runtime parent directories are bundle-owned real directories,
  never symlinks.

## Boundaries

- Do not weaken `qa-gate.sh` checks to make something pass.
- Do not add `--dangerously-skip-permissions` or
  `--dangerously-bypass-approvals-and-sandbox` anywhere, or recommend them in docs.
  Narrow allow-rules, or restructure so the worker uses file tools.
- Do not modify the user's `~/.gemini/` or `~/.codex/` config as part of a code change.
  Document what the user should change; let them do it.
- Do not add a runtime dependency (Node, Bun, a package manager). Bash + Python 3 +
  git is the whole point; competing projects already occupy the MCP-server niche.
- Model recommendations are advisory only: never apply them automatically, change the
  caller-selected tier, invent a thinking-level flag, or escalate permission,
  authentication, scope-policy, or human-required outcomes.
- Direct model/effort selection is caller-owned. Reject repeated, empty, conflicting,
  inferred, unsupported, or unbound selectors before task read or dispatch. CLI and
  its matching environment source conflict even when equal; never add precedence.
  The checked-in matrix is validated metadata, not routing or gate authority; only
  exact SHA/schema/version/source-bound metadata matching the installed agy version
  may resolve a pair. Resolve once, freeze provenance and the exact slug across
  retries, send one downstream `--model`, and never send downstream `--effort` or a
  thinking-level flag.
  Keep reviewed pair-to-slug mappings and fixed classifications explicit in both the
  matrix and validator allowlists; update both in one reconciliation and never derive
  a slug by concatenating model and effort strings.
- Do not auto-pull during a worker job, auto-submit an issue, install `gh`, or make
  GitHub CLI a runtime dependency.
- Do not overstate the project in README. It is one differentiated idea among several
  existing tools, and the prior-art section stays.
- Ask before pushing to `main`, publishing a release, or enabling an external
  distribution/search service.
