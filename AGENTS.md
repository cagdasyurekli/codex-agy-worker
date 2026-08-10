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
- Evidence Report v1: 80 offline pure-rendering/privacy/CI-format/binding/mutation cases.
- Offline Benchmark v1: 104 offline plan/Receipt/result/report/privacy/mutation cases.
- Persona Evidence Registry v1: 124 offline semantic-chain/Git-ancestry/portable/mutation cases.
- Safe local lifecycle: 95 offline state/receipt/Git-policy/cleanup/signal cases.
- Data-only Workload Profiles v1: 89 offline schema/allowlist/portable/mutation cases.
- `agy-worker.sh` / `install.sh` / model selection and recommendation: 209 offline
  fake-agy/routing cases.
- `update.sh`: 310 offline transport/process/inventory/local-remote/matrix/manifest/watch-policy cases.
- Canonical version-attestation runner: 157 offline fixed-profile/source-binding cases.
- Version-attestation mutation harness: 55 offline publication/process-group/signal cases.
- Canonical models-inventory attestation runner: 113 offline
  fixed-profile/version-binding/environment/parser/process cases.
- Explicit-account models capture runner: 69 offline
  profile/account-TCB/environment/capture/publication/process cases.
- `bug-report.sh`: 21 offline privacy/fake-`gh` cases.
- Codex package/skill distribution and CI policy: 347 offline
  manifest/runtime-copy/relocation/landing/range cases.
- `doctor.sh`: 239 offline fake-tool/read-only cases.
- Public gate conformance v1: 79 offline manifest/fixture/permissive-gate/signal/cleanup cases.
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
./tests/test-evidence-report.sh # offline pure receipt renderer/privacy coverage
/usr/bin/python3 -I -S -B tests/test-benchmark.py # offline synthetic benchmark coverage
/usr/bin/python3 -I -S -B tests/test-persona-evidence.py # offline persona evidence registry
/usr/bin/python3 -I -S -B tests/test-job-lifecycle.py # offline disposable Git lifecycle
/usr/bin/python3 -I -S -B tests/test-workload-profiles.py # offline data-only profiles
./tests/test-agy-worker.sh      # offline fake-agy dispatcher/installer coverage
./tests/test-update.sh          # offline local Git remotes; no public fetch
/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py # offline canonical runner path
/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py # offline fake-child mutation harness
/usr/bin/python3 -I -S -B tests/test-models-attestation-runner.py # offline fake inventory attestation
/usr/bin/python3 -I -S -B tests/test-models-capture-runner.py # offline fake-account capture only
./tests/test-reporting.sh       # offline fake-gh privacy/submission coverage
./tests/test-packaging.sh       # offline Codex manifest, relocation, policy, landing
./tests/test-doctor.sh          # offline fake-tool/read-only readiness coverage
/usr/bin/python3 -I -S -B tests/test-conformance.py # offline public gate contract
./tests/test-proof-demo.sh      # offline synthetic pass/reject proof coverage
bash -n ./*.sh conformance/*.sh scripts/*.sh tests/*.sh skills/*/scripts/*.sh skills/*/runtime/*.sh  # syntax
(
  AGY_WORKER_PYCACHE="$(mktemp -d -t agyworker-pycache.XXXXXX)" || exit 1
  trap 'rm -rf -- "$AGY_WORKER_PYCACHE"' EXIT
  PYTHONPYCACHEPREFIX="$AGY_WORKER_PYCACHE" \
    python3 -m py_compile conformance/v1/*.py scripts/*.py skills/*/runtime/scripts/*.py
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
- **A human report is only a validated receipt view.** Render fixed bounded fields;
  never include source, prompts, commands, paths, logs, or worker prose. Rendering
  cannot change rejected/routed outcomes or turn `gate-passed` into acceptance.
  Receipt-only selection and recommendation validation stays side-effect-free; only
  explicit pre-gate publication input may run canonical recommendation coherence.
  JSON is a canonical report view, not the raw receipt. GitHub Step Summary output
  goes only to stdout or an explicit new file; never discover or write the workflow
  environment path, emit workflow commands, call a GitHub API, comment, or upload.
- **Lifecycle cleanup spends fresh explicit authority.** State stays in an external
  owner-private file and binds the exact repo/worktree/ref/base/job/Receipt/candidate.
  Only rejected exits 10-14 may clean. Reconcile each completed Git step durably,
  stop when reconciliation changes the state SHA, and require fresh job/state/candidate
  approvals before the next destructive step. Remove the exact registered worktree,
  then compare-delete only the unchanged ref; never force-delete a branch, follow a
  symlink target, clean routed/passed work, or infer approval from old state.
  Accept only an exact canonical branch name, and run every lifecycle-owned Git
  command through the fixed sanitized Git policy. Checkout initialization disables
  hooks and ambient config/helpers and rejects configured fsmonitor, pager, include,
  or content-filter authority plus every effective base-tree/info filter attribute.
  Treat only documented `show-ref --verify --quiet` exit 1 as ref absence; fatal Git
  evidence must leave cleanup in progress for manual recovery.
- **A supplied conformance gate is trusted executable code.** Its cleanup TCB includes
  loaded code, the local owner and same-UID processes, and OS administrators. Keep
  parent/root descriptors close-on-exec and no-follow, identities exact, and content
  deletion bounded and descriptor-relative. Unlink symlinks without following them;
  final pathname removal trusts that TCB. On pathname or identity drift, fail closed
  with a possible residual and never scan for or chase a moved inode. Make no
  same-user tamper-resistance or guaranteed hostile-gate cleanup claim.
- **Benchmark v1 is not model evaluation or routing.** It preregisters caller
  selections and fixed public synthetic tasks, spends one attempt per pair, and binds
  the existing Receipt v1 verdict without changing it. Keep result roots external and
  owner-only. Never add ranking, winner, retry, fallback, route, recommendation, live
  provider execution, or persona-registry claims to the offline command.
  A complete checkout binds its clean full commit; a folder-only bundle binds the
  reviewed portable source revision, exact source manifest, modes, runner, schemas,
  gate, wrapper, and fixtures. Never invent a Git commit for the portable case or
  accept missing, extra, writable, symlinked, or hash-drifted authority. The JSON
  schemas own the nested v1 structure; runtime validation owns cross-field equality
  and canonical-byte checks.
- **A workload profile is data, not a driver.** It may suggest one maintained mode,
  persona, and closed repo-relative path-policy shape, but it cannot name a repository
  or path, select a tier/model/effort, carry a verifier or shell command, authorize
  work, dispatch, route, accept, or perform Git actions. The caller still supplies
  approval, exact repository, exact path policy, selected tier, and verification.
  Load only the fixed hash-bound bundle; never discover profiles from a target repo,
  environment variable, home directory, or caller path.
- **`update.sh check` may need network, but must remain read-only.** Compatibility
  evidence for agy and Codex is reported as unchanged, drift-review, or
  evidence-unavailable; inconclusive evidence is never green. Metadata changes only
  after a human reconciles official docs, upstream source, command inventories, and a
  bounded real job when behavior changed. Production origins, release channels, and
  review cadence are not environment-overridable. The weekly watcher never advances
  metadata or takes an external action. `apply` is always an explicit action.
- **A GitHub URL is not fixed evidence when Git transport is ambient.** Read-only
  check/watch must use the exact proxyless, redirect-rejecting, bounded GitHub REST
  helper and bounded process-group/version supervisor; never restore `git ls-remote`
  there. Preserve HUP/INT/TERM status and sanitized output. The explicit apply-time
  Git fetch remains outside that isolation boundary and must be documented as such.
- **The agy distribution manifest is only a canary.** Its endpoint is fixed; reject
  proxies, redirects, oversized/malformed responses, and unexpected archive URLs.
  Never request the archive. The checked-in tuple detects drift but cannot advance a
  baseline, prove source/behavior, or activate model/effort resolution.
- **Version attestation needs a proven supervisor, not an ad hoc probe.** Keep the
  canonical runner fixed to one snapshot-backed `--version` call and keep its
  persistent mutation harness offline and synthetic. The harness must bind the exact
  canonical source bytes before importing them. Production mode requires the fixed
  `/usr/bin/python3 -I -S -B` launch. Trust the selected reviewed Apple interpreter,
  hosted image, local owner, and OS administrators; require exact path/component and
  alias/target identity, regular executable/no-setid target, and no world-writable
  directory or resolved executable. UID/GID and owner/group writability are
  diagnostic facts, not provenance authority. Do not claim same-user or hostile-PR
  tamper resistance, code signing,
  binary provenance, or OS attestation. Bind snapshot, source, and external parent to
  the prior
  evidence record. Every controller must use its
  one bounded, signal-masked process-group owner; publication and completion must
  remain inode-pinned, no-overwrite, parent-fsynced, and paired with weakened controls.
  Test-only mutations are fixed Python callables/copies, never production CLI or
  environment overrides. Self-test mode may use only synthetic private fixtures and
  must have no path to production evidence. Green runner/harness tests authorize no
  agy, provider, or metadata call.
- **The current models runner is deliberately auth-isolated.** Its one child receives
  only a fresh private empty HOME, TMPDIR, and XDG roots plus the exact fixed locale,
  terminal, color, and PATH values. Never inherit or copy the caller's HOME,
  credentials, Python startup paths, or ambient environment into this runner. An
  auth-required `agy models` result must reject without a completion marker and cannot
  advance the `1.1.10` matrix. The accepted `1.1.11` version binding proves only the
  version snapshot/source/argv observation. Production use of the separate capture-
  only runner remains dormant until the user authorizes its exact account HOME,
  profile, and one call. It publishes `captured`, never accepted, evidence and cannot
  advance metadata. A bounded exit-zero stream is retained without inventory/error
  interpretation, while capture-owned scratch/cache/cwd must be empty after group
  closure. The authorized external CLI may read, write, mutate, or cache in HOME;
  the runner cannot detect or revert those changes, and residuals may remain after a
  rejected capture. Treat
  the reviewed runner sources, account HOME, local owner/same-UID processes,
  interpreter, and OS admins as TCB: AST mutations detect selected drift and do not
  prove hostile-source or same-UID tamper resistance.
- **CI diff hygiene audits committed bytes.** Keep checkout history sufficient for
  the GitHub event range and run `scripts/ci-diff-check.sh`; its stdlib scanner must
  inspect every changed regular head blob independently of Git attributes with a
  linear, globally bounded scan. Read reviewed object IDs through one bounded
  `git cat-file --batch` process; never restore one Git process per blob. This
  deliberately rejects pre-existing hygiene
  defects in a changed file. A plain
  worktree-only `git diff --check` is not equivalent. Pull requests use base...head, pushes use
  before..head, and an all-zero initial push compares the root commit to the empty
  tree. Never fetch an extra ref inside this check.
- **An inventory display label is not another model.** Interpret owner-captured
  `agy models` evidence line by line against the exact reviewed slug allowlist.
  `gpt-oss` is display text only when its line contains the one exact canonical
  `gpt-oss-120b-medium` slug. Unknown tokens in the reviewed provider namespaces
  fail closed; generic slug regex matches cannot advance metadata.
- **Bug reports are local drafts first.** Never gather prompts, source, envelopes,
  paths, credentials, or raw logs automatically. Submission must show the exact body
  and require the matching SHA-256 confirmation token; send those validated bytes,
  not a mutable path, to an explicitly bound github.com destination.
- **The public skill is self-contained.** Keep the core runtime canonical under
  `skills/agy-worker/runtime/`; repository-root commands are compatibility wrappers.
  Complete plugins and explicit standalone installs may resolve the checkout, while
  skill-folder-only copies use the bundled runtime. Never publish a local
  `.pipeline-root`, bake in a checkout path, or add an automatic fetch fallback.
- **Persona evidence is not persona trust.** Keep the runtime persona allowlist fixed
  and target-repository registration impossible. `offline-only` binds exact public
  persona/frontmatter/mode and P1-C contract bytes but does not execute the persona.
  Higher states require separate public Receipt/base/selection/verifier/tool and
  maintainer-approval bindings. Upper states require immutable evidence, separate
  approval/review, then transition commits; this proves protected-main sequencing,
  not reviewer identity or a signature. Never auto-promote, rank, route, accept, or add persona
  quality labels to the P1-C producer.
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
