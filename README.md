<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/logo-dark.svg">
  <img src="docs/assets/brand/logo-light.svg" alt="" width="132" height="132">
</picture>

# codex-agy-worker

Let **Codex CLI** delegate repository exploration, features, and project-scale coding
to **Antigravity CLI (`agy`)**. Codex owns the resulting diff review, build/test/lint
checks, bounded same-conversation repair loop, and honest delivery status. A worker
can discover ordinary project structure; it is not limited to mechanical edits or a
predeclared file list.

Bash + Python 3 + git. No Node and no MCP daemon. A deliberately started job may
have one private, per-job local controller; it is not a shared service.

```bash
# Codex dispatches a bounded job to agy...
BASE="$(git -C "$WT" rev-parse HEAD)"       # capture before dispatch
echo "$TASK" | AGY_WORKER_MODE=accept-edits ./agy-worker.sh \
    --workdir "$WT" --add-dir "$WT" > envelope.json

# ...then independently verifies it, rather than believing the report.
./qa-gate.sh --envelope envelope.json --repo "$WT" --base "$BASE" \
    --only 'tests/**' --expect-edits \
    --verify "cd $WT && pytest -q"
```

---

## Why another one of these?

There are already several Codex→agy delegators — [codex-agy-delegator](https://github.com/swjturay/codex-agy-delegator),
[codex-antigravity-bridge](https://github.com/Common-ka/codex-antigravity-bridge),
[agy-mcp](https://github.com/Boulea7/agy-mcp), [antigravity-cli-mcp](https://github.com/topics/agy),
and [antigravity-for-claude-code](https://github.com/VKirill/antigravity-for-claude-code).
Most are MCP servers; several are more featureful than this one. This project keeps
one agy backend and does not claim validated native-Windows support.

**Its differentiator is that Codex does not confuse a worker report with evidence.**
The worker's JSON report is a *claim*. The gate independently derives bounded facts
from the repository, while Codex uses those facts and driver-owned checks to decide
whether the result is verified or needs more work:

| The worker... | Gate | Exit |
|---|---|---|
| edits files it never declared | diffs the repo, compares to `files_changed` | `10` |
| declares files it never touched | same check, other direction | `10` |
| reports commands or tests on a completed candidate | treats them as untrusted data and executes none | `11` |
| returns a malformed envelope | validates the complete checked-in schema | `12` |
| claims completion, changed nothing under `--expect-edits` | diff is empty | `13` |
| makes a plausible but wrong fix | **runs the driver's own `--verify` command** | `14` |
| asks for a human or reports partial/failed/blocked | checks scope, then routes without accepting | `15` |
| changes files outside a driver-owned `--only` policy | rejects even if the worker declared them | `10` |

`--verify` is mandatory for acceptance. The gate never executes worker-supplied shell
text, and it hashes the Git diff plus every nontracked path—including ignored files—
before and after verification so a passing verifier cannot silently rewrite the
candidate.

The thirty-five offline suites need no agy provider call, network access, API key, or GitHub login.

### GitHub Actions cost and quota fallback

For a public repository, standard GitHub-hosted Actions runners do not consume the
owner's included minutes. A private fork or a future visibility change is different:
macOS runners cost substantially more than Linux runners, so the workflow deliberately
avoids a second full run after a normal merge.

The required `test` job runs the complete macOS suite on pull requests, uses strict
up-to-date branch protection, and cancels a superseded run for the same PR. Normal
squash merges preserve the tested tree; the post-merge commit has a new identity but
does not need a duplicate full-suite run. The job is also available by explicit manual
dispatch for an exact release comparison: supply committed `base_sha` and `head_sha`.

If a private fork's quota is unavailable, run the same fail-fast offline suite locally:

```bash
./scripts/ci-offline.sh
```

It runs the static checks and all thirty-five offline suites without requiring a
network or provider call and without intentionally inspecting account-HOME contents.
Ambient local tools may still consult their ordinary user configuration. Keep the
command's exact summaries together with the commit,
tree, and `git diff --check` evidence. This is local evidence only: it never satisfies
the protected GitHub `test` check. After availability returns, manually dispatch the
exact comparison before publishing or releasing unless the repository owner explicitly
changes the protection policy. The daily compatibility watch remains macOS-specific;
the weekly metadata-only feedback watch uses Linux because it has no macOS contract.
See GitHub's [Actions billing guidance](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
and [workflow concurrency documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

## See the evidence boundary in under a minute

```bash
./proof-demo.sh
```

This repository-only starter proof creates two independent private temporary Git
repositories, exercises the maintained gate once on an exact synthetic edit and once
on a plausible but incomplete synthetic envelope, then removes both repositories. It
does not invoke agy, access the network, inspect credentials, or change the current
checkout. The three-line result proves only that these two fixed cases produced the
expected gate exits. `gate-passed` is not a human review, accepted candidate, general
correctness claim, security certification, benchmark, or production validation.

[![Conformance v1: fixtures only](https://img.shields.io/badge/conformance-v1%20fixtures%20only-4c1.svg)](docs/CONFORMANCE.md)

Integrations and forks can run the full public gate contract against their own entry
point:

```bash
./conformance/run.sh --gate /path/to/their/qa-gate.sh
```

The versioned eleven-fixture kit requires exact exits for acceptance, scope,
ignored-file, untrusted-claim, malformed-envelope, no-op, verifier-failure,
verifier-mutation, human-required, mutable-base, and missing-verifier cases. Passing
means fixture compatibility only—not security certification, real-job quality,
Receipt v1 support, or human acceptance. The supplied gate executes with the current
user's privileges; review it first. Its execution TCB includes the supplied gate and
loaded code, the local owner and same-UID processes, and OS administrators. Cleanup
holds no-follow directory descriptors and deletes contents relative to them while the
original parent/root identities remain exact; final pathname removal trusts that TCB.
Identity drift fails closed with a possible residual, and the runner never scans for
or chases a moved directory. This is not same-user tamper resistance. See
[the bounded claim and fixture contract](docs/CONFORMANCE.md).

## Roadmap

[The product roadmap](docs/ROADMAP.md) records dependency-ordered feature slices and
their explicit implemented or deferred status. The published **v0.6.0 release scope**
added the reviewed Gemini 3.7 Flash low/medium/high mappings and hardened the capture
child mode and dispatch-state snapshot boundaries. The published **v0.7.0** scope adds
usability-first explore/task/project workflows and same-conversation repair. The
published **v0.8.0** scope adds explicit notifier
maintenance/rebind handling, bounded annotated-tag resolution, version-drift
observations, and the exact agy 1.1.13 quota-terminal classification. The published
**v0.9.0** scope activates the exact agy 1.1.16 version/source/distribution and
unchanged 14-slug inventory binding, and accepts Codex 0.148.0 as an observational
baseline. The published **v0.10.0** scope includes the lifecycle, recovery,
verification, and Codex-owned assurance scope, including the legacy notifier 18→21 file
refresh migration, bounded lifecycle recovery, and driver-owned verification. Immutable
historical v0.10.0 tag bytes cannot be rewritten; this source and package alignment
establishes repository truth without retroactively altering that published tag. The published v0.5.0
scope added sanitized bug/improvement drafts with exact double confirmation,
private-only security drafts, and the bounded metadata-only feedback aggregate and
weekly/manual watcher. The prior v0.4.0 scope includes daily compatibility observation,
private 30/60/90 measurement, the optional local notifier, version-drift-safe
default/literal routing, bounded updater, gate-envelope, lifecycle-Git-output,
Actions-checkout hardening, and the progress-aware per-job dispatch lifecycle. A
source checkout alone is not proof of publication; verify the exact reviewed tag and
release state separately.
The **v0.11.0 source candidate** records agy 1.1.22 as a non-activating observation,
Codex 0.150.1 as observation-only, and pins `actions/checkout` v6.0.2 by commit. The
required single no-retry 1.1.22 account inventory call failed without classifiable
inventory evidence, so the active agy baseline remains 1.1.16 and publication is
blocked pending an explicit scope decision or new separately authorized evidence. Its
bounded dogfood record includes accepted Flash-high task/explore and Pro-high task
candidates plus a rejected Pro-high project candidate; Codex-owned review, tests, and
exact-head CI remain the acceptance authority. This describes release scope, not proof
that a v0.11.0 tag or GitHub Release exists.
P2-B and P2-C remain deferred because their required live terminal-event and
recurring-accumulation evidence does not exist.
Source, tests, and this README remain
the authority for current CLI behavior; every new slice still requires its own
approval, tests, review, and pull request.

---

## Install from GitHub

```bash
git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker
./install.sh          # installs the Codex skill only; touches nothing else
for suite in tests/test-*.sh; do "$suite"; done
```

That loop is a shell-suite smoke check, not the complete offline gate. Run the full
shell and Python command list in [CONTRIBUTING.md](CONTRIBUTING.md) before publication.

The GitHub repository is the source of truth. Review the commit you cloned before
installation. For a released snapshot, check out the exact reviewed
`vMAJOR.MINOR.PATCH` tag from the
[GitHub Releases page](https://github.com/cagdasyurekli/codex-agy-worker/releases)
before running `./install.sh`; do not substitute an unverified tag.

Requires `agy` (Antigravity CLI) on `PATH`, `git`, Python 3, and Bash. The core CLI
has no Windows-specific denylist, but its maintained entrypoints require a
POSIX-compatible Bash/Python/Git environment and some canonical evidence commands use
fixed POSIX paths. Native Windows is untested; WSL or another compatible environment
may work on a best-effort basis. The optional daily notifier is specifically a macOS
LaunchAgent.
The active reviewed model/effort matrix remains bound to agy `1.1.16`; its exact
accepted evidence and pair-to-compound-slug mappings are reconciled in
[`compat/reviews/agy-1.1.16.md`](compat/reviews/agy-1.1.16.md). The separate
[`1.1.22` observation](compat/reviews/agy-1.1.22.md) records official evidence and the
bounded failed account capture without activating metadata. The complete
[`1.1.12`](compat/reviews/agy-1.1.12.md) reconciliation remains historical evidence,
and the earlier
[`1.1.16` interface observation](compat/reviews/agy-1.1.16-interface.md) records the
non-activating evidence that triggered the later capture and review. The agy-owned
default and explicit literal pass-through remain version-independent.
Codex `0.150.1` is the accepted observational Codex baseline; it does not grant agy
dispatch or model-selection authority. See
[`compat/reviews/codex-0.150.1.md`](compat/reviews/codex-0.150.1.md).

Before spending provider quota, run the offline doctor against the repository you
plan to delegate:

```bash
./doctor.sh --repo /absolute/path/to/target
./doctor.sh --repo /absolute/path/to/target --format json
```

The doctor is deterministic and read-only. It checks the bundled runtime, Bash 3.2,
Python 3, git and worktree support, target Git worktree, exact semantic
`agy --version`, and the checked-in agy version, reviewed-source, and review-date
records. Source-revision failures are reported only as match, mismatch, or unavailable;
the doctor never prints their bytes. It invokes no provider, network client, updater,
dispatch, authentication probe, or personal-config scan, and it repairs nothing.

| Exit | Overall | Meaning |
|---:|---|---|
| `0` | `ready` | All offline prerequisites match the checked-in evidence. |
| `3` | `review-required` | Prerequisites work, but the agy version drifted or review is due. |
| `3` | `not-ready` | A prerequisite, semantic probe, repository, bundle, or metadata check failed. |
| `64` | no report | Invocation or format is invalid. |

If the repository-root launcher cannot resolve a bounded symlink chain to the
canonical `doctor.sh`, it emits one sanitized diagnostic, no report, and exits `3`.
The wrapper itself may be reached through that bounded chain, but the package-owned
`skills/agy-worker/runtime` path and the runtime's `scripts`, `agents`, `schemas`,
and `compat` parents must be real directories contained in the bundle, not symlinks.
The doctor also ignores caller-provided temp paths, gives every child probe one
private external workspace, and removes it before returning. HUP, INT, or TERM is
forwarded to the active probe and its descendants; interruption emits only
`doctor: interrupted`, no report, and exits `3`.

Green covers only those offline prerequisites. It does **not** certify authentication,
provider availability, Codex/agy sandbox permission, task quality, or a future
dispatch. A due or drift result asks for human compatibility review; it never updates
metadata. `review-required` is not a blanket dispatch lock: no selector or
`--tier default` still delegates to agy's own default, and the explicitly approved
`--literal-model` surface remains an unreconciled caller-owned pass-through. Reviewed
`--model`/`--effort` resolution keeps its reviewed `1.1.16` matrix evidence. Every
direct selection uses a safe executable with bounded semantic `--version` and strict
critical `--help` structure. An exact matrix-version match proceeds mechanically after
that structural probe. Compatible version drift requires Codex's explicit
`--compatibility-disposition proceed --approve-help-sha SHA256` before dispatch; the
SHA must be the exact raw help digest just reviewed. The caller's exact resolved
slug is unchanged and the selection record reports
`model_availability: not_assessed`; it never claims that a drifted installation
offers a particular model. A structurally incompatible critical interface still
blocks reviewed direct selection. Structural acceptance, including an exact-version
match, is not semantic approval: before every reviewed direct dispatch, Codex inspects
the current bounded raw `agy --help` and stops if the exact caller-selected model or
effort cannot be honored. The controller never infers that decision from provider
prose. `not-ready` still blocks all dispatch. For a folder-only skill
copy, resolve `PIPELINE` as shown in
`skills/agy-worker/SKILL.md` and run `"$PIPELINE/doctor.sh"`—no checkout or fetch is
needed.

To approve compatible version drift without disclosing an executable pathname,
inspect the bounded local `agy --help` bytes, then calculate their raw SHA-256 with
`LC_ALL=C agy --help 2>&1 | /usr/bin/python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'` and compare its digest to the sanitized
`raw_help_sha256` review output. Retry the same caller-selected `--model`/`--effort`
request with `--compatibility-disposition proceed --approve-help-sha` set to that
matching digest. A mismatch, changed help, or unavailable probe needs a fresh review;
do not reuse an older digest.

`skills/agy-worker/` is the one canonical, open-standard Agent Skill and contains its
own Bash/Python/git runtime. A folder-only copy therefore works without the rest of
the checkout and never downloads code when invoked. The repository-root commands are
compatibility wrappers for clone users. `install.sh` copies the same bundle and writes
a local pointer so checkout-only maintenance commands remain available; it does not
rewrite the public `SKILL.md`.

The repository retains `.codex-plugin/plugin.json` so the Codex skills-only package
shape can be validated locally. It is not a marketplace listing: GitHub clone plus
`./install.sh` is the supported distribution path.

The portable Agent Skill can also be copied through the third-party skills CLI:

```bash
DO_NOT_TRACK=1 npx skills add cagdasyurekli/codex-agy-worker \
  --skill agy-worker --copy
```

`npx` is only an optional installer here; the installed skill has no Node runtime
dependency. Review the copied files before use.

The public landing page source lives at `docs/index.md`. GitHub Pages configuration,
repository About fields, topics, and search-engine verification are separate
repository-owner actions; checked-in files do not change those settings.

### Codex sandbox settings — required

agy starts a local language server and writes state under `~/.gemini`. Under Codex's
default `workspace-write` sandbox it fails with **exit 5 and empty stderr**. Add to
`~/.codex/config.toml`:

```toml
[sandbox_workspace_write]
network_access = true
```

and run Codex with `--add-dir ~/.gemini`. **Both** are needed — the writable dir alone
still fails, because the blocker is the socket bind, not the file write.

With the config saved, launch an interactive session with:

```bash
codex --add-dir "$HOME/.gemini"
```

For a one-off `codex exec` invocation, pass both settings explicitly:

```bash
codex exec --sandbox workspace-write --add-dir "$HOME/.gemini" \
  -c 'sandbox_workspace_write.network_access=true' "<your task>"
```

---

## Use it from Codex

After `./install.sh`, start a new Codex session and ask in normal language:

> Use the agy-worker skill to add error-path tests for
> the parser modules under `/absolute/path/to/project/src/`. Allow changes only
> under `tests/`, verify with `python3 -m pytest -q tests/test_parser.py`, and
> preserve accepted work on a branch.

For a larger request, the prompt can be equally direct:

> Use agy-worker to build this application in `/absolute/path/to/project/`. Discover
> the existing structure and test commands, implement the requested behavior across
> the project, run the relevant checks, and repair failures in the same conversation.

Codex creates an isolated worktree, selects a workflow, dispatches agy, inspects the
diff, runs driver-owned checks, and reports what is actually verified. You do not need
to supply a final file list, a persona, or every test command before starting.

| What you want | Workflow | Minimum input | What Codex delivers |
|---|---|---|---|
| Understand, plan, or review a repository | `explore` | Repository and question | A useful read-only report with stated coverage limits; not an exhaustive audit claim. |
| Implement a feature, refactor, or tests | `task` | Repository and desired behavior | A worktree diff plus Codex-run relevant checks; failed checks can trigger a bounded repair. |
| Build an app, complete a project, or broadly audit-and-fix | `project` | Repository and outcome | Repo-wide worktree changes, build/test/lint measurement, up to five total provider attempts (the initial attempt plus at most four same-conversation repairs), and an assurance label. |
| Follow a long job | async lifecycle | Job ID | Local `status`/bounded `wait`, controlled extension, or cancel state; not remote-provider truth. |

Assurance labels are intentionally practical: Codex uses `verified` only after its
strict review policy is met, `partially_verified` for useful work with unresolved
evidence, `rejected` for work it declines, and `blocked` for a genuine authority,
repository-boundary, or execution block. After it validates the exact current
candidate and Verification v2 binding, the controller persists Codex's declared
label; it does not reinterpret check counts into a different disposition. A failed
first check is a repair signal, not an automatic rejection or deletion.

Before the first dispatch for a repository, the skill identifies the paths in scope
and requires explicit approval for sending that task and any worker-read repository
content through agy to Google/Gemini, unless the user already approved that exact
transmission. Read [PRIVACY.md](PRIVACY.md) before use; support and project terms are
in [SUPPORT.md](SUPPORT.md) and [TERMS.md](TERMS.md).

## Manual end-to-end example

Keep the pipeline checkout and target repository explicit. The job branch matters:
it prevents accepted but uncommitted work from being destroyed during cleanup.
`bulk-test-writer` is still experimental: it has been exercised on a real task, but
has not yet produced an accepted real delivery.

```bash
PIPELINE=/absolute/path/to/codex-agy-worker
TARGET=/absolute/path/to/your-project
WT=/tmp/agy-job-12345
JOB_BRANCH=agy/tests-parser-errors-12345
ENVELOPE=/tmp/agy-job-12345-envelope.json
JOB_ID=parser-tests-12345
BASE="$(git -C "$TARGET" rev-parse HEAD)"

git -C "$TARGET" worktree add -b "$JOB_BRANCH" "$WT" "$BASE"

if ! echo "Add error-path tests for $WT/src/parser.py.
Edit ONLY files under $WT/tests/. Use file tools on absolute paths.
Do NOT run shell commands — they execute in a scratch directory, not this repo.
The driver runs every command. Return commands_run and tests_run as empty arrays." |
  AGY_WORKER_JOB_ID="$JOB_ID" "$PIPELINE/agy-worker.sh" \
    --workflow task --mode accept-edits --tier bulk --persona bulk-test-writer \
    --workdir "$WT" --add-dir "$WT" > "$ENVELOPE"; then
  echo "Dispatch failed; inspect the sanitized terminal state/result. Resume only a candidate-free failure; handle an ERROR candidate with Verification v2, and preserve/finalize or freshly restart a CANCELED candidate." >&2
  exit 1
fi

if "$PIPELINE/qa-gate.sh" --envelope "$ENVELOPE" --repo "$WT" --base "$BASE" \
  --only 'tests/**' --expect-edits \
  --verify "git -C '$WT' diff --check" \
  --verify "cd '$WT' && python3 -m pytest -q tests/test_parser.py"; then
  echo "Candidate passed the evidence gate; review the diff before preserving it."
else
  GATE_RC=$?
  echo "Gate rejected or routed the candidate (exit $GATE_RC)."
  exit "$GATE_RC"
fi
```

Exit 0 means the evidence gate accepted the current state; it does **not** merge it.
Review and preserve the work before removing the worktree:

```bash
git -C "$WT" diff
git -C "$WT" add tests/
git -C "$WT" commit -m "test: cover parser error paths"
git -C "$TARGET" worktree remove "$WT"

# Integrate JOB_BRANCH only after your normal review/PR process.
```

If the gate rejects the job and you intentionally want to discard it:

```bash
git -C "$TARGET" worktree remove --force "$WT"
git -C "$TARGET" branch -D "$JOB_BRANCH"
```

Gate failure handling is deliberately small: exits 10–14 reject the candidate, exit
15 routes its questions to a human, and exit 64 means the driver invocation is wrong.
A dispatch error starts no further provider call automatically; do not add a shell
retry loop. A candidate-free failed state may be eligible for SHA-approved exact
`resume` or explicit fresh `restart`. A terminal `ERROR` with a valid candidate goes
to `result`, driver Verification v2, then `continue` or `finalize`. A terminal
`CANCELED` candidate goes to `result` and preservation/finalization or an explicit
fresh `restart`; it is never resumed or continued.

### Read-only inventory example

Use `plan` for inventory and independently spot-check the report. Exit 0 can prove
that no files changed and that the driver command passed; it cannot prove the
worker's architecture prose is accurate.

An explore result is useful input for planning but does not prove that every semantic
path was inspected. Codex should spot-check material claims before relying on them;
that limitation does not make an otherwise useful broad exploration inadmissible.

```bash
if ! echo "Read repository-owned files under $WT using absolute paths. Report entry points,
test commands, and risky areas. Do not run commands. Return files_changed,
commands_run, and tests_run as empty arrays." |
  "$PIPELINE/agy-worker.sh" --workflow explore \
    --workdir "$WT" --add-dir "$WT" > /tmp/inventory-envelope.json; then
  echo "Inventory dispatch failed; do not pass its stdout to the gate." >&2
  exit 1
fi

"$PIPELINE/qa-gate.sh" --envelope /tmp/inventory-envelope.json \
  --repo "$WT" --base "$BASE" \
  --verify "git -C '$WT' diff --quiet '$BASE' --"

# Then open a sample of every claimed path and verify discovered commands against
# package/CI files yourself before using the inventory for planning.
```

### Two rules that make it work

1. **Keep the worker off the shell.** Under agy's sandbox, its *shell* tools run in
   `~/.gemini/antigravity-cli/scratch`, **not your repo** — a worker asked to survey a
   repo will run `ls` and truthfully report an empty directory. Its *file* tools do
   reach the real target. Let the worker edit files; the driver owns every command.
2. **Absolute paths + `--add-dir`.** agy has no reliable notion of "the current
   directory" in print mode. Name the absolute path in the task text *and* pass
   `--add-dir`. With only one, the worker works on the wrong thing.

### Personas

`--persona repo-inventory|diff-reviewer|bulk-test-writer` optionally inlines a role brief from
`skills/agy-worker/runtime/agents/`. `repo-inventory` measurably changed an
under-specified job from a false
survey into an honest escalation. `bulk-test-writer` has now been exercised on a real
Playbook-Gemini test task: the gate caught a bad first test and rejected the retry on
diff hygiene even though its focused tests passed. That proves the gate, not reliable
first-pass quality from the persona. The first dispatch consumed about 251k input
tokens and the corrective retry about 63k, reinforcing that this is for batched work,
not cheap one-line edits. `diff-reviewer` remains unexercised on a real job.

Personas are injected as prompt text, **not** via agy's `--agent` flag, because
`--agent` silently disables `--json-schema` enforcement (see below).

[`persona-evidence.sh`](docs/PERSONAS.md) validates the fixed shipped-persona
registry and reproduces its documentation table. All three shipped personas remain
`offline-only`: their records bind exact frontmatter/mode restrictions and the public
P1-C contract bytes, but that synthetic candidate does not execute a persona.
Historical real exercises lack the public Receipt/base/verifier/tool bindings needed
to promote a registry state. Future upper states require immutable public Git blobs
in three strict phases: evidence, separate approval/review, then registry transition.
This validates protected-main sequencing and exact bytes/modes, not cryptographic
reviewer identity or signatures; portable bundles reject upper states.

| Persona | Allowed modes | Evidence status | Public evidence |
|---|---|---|---|
| `bulk-test-writer` | `plan`, `accept-edits` | `offline-only` | P1-C public contract; persona not executed |
| `diff-reviewer` | `plan` | `offline-only` | P1-C public contract; persona not executed |
| `repo-inventory` | `plan` | `offline-only` | P1-C public contract; persona not executed |

Statuses are evidence levels, not trust labels or acceptance authority. The registry
cannot rank, route, select, execute, promote, or dynamically register a persona.

### Data-only workload profiles

[`profile.sh`](docs/PROFILES.md) lists three fixed maintained skeletons without
loading a repository or dispatching anything:

```bash
./profile.sh list
./profile.sh show bounded-test-backfill
```

`show` prints canonical JSON that may suggest one maintained mode, persona, and
repo-relative path-policy shape. It is deliberately non-executable and explicitly
requires the caller to provide approval, exact repository, exact path policy,
selected tier, and verification commands. Profiles contain no repository path,
model/tier/effort value, command, external root, authorization, routing, acceptance,
dispatch, or Git action. Only the fixed hash-bound bundle is read; target repositories,
environment variables, home directories, and caller-supplied profile paths are never
profile sources.

### Common options

| Worker option | Environment equivalent | Meaning |
|---|---|---|
| `--workflow explore|task|project` | — | Selects read-only exploration, ordinary implementation, or project-scale iterative work. Omitted input keeps the legacy raw-mode behavior. |
| `--max-cycles 1..2` | — | `explore` or `task` total provider-attempt budget; default `2`. |
| `--max-cycles 1..5` | — | `project` total provider-attempt budget; default `5`. Legacy raw mode is exactly one attempt; `--max-cycles` requires an explicit workflow. |
| `--mode plan|accept-edits` | `AGY_WORKER_MODE` | Raw agy mode for compatibility. `explore` fixes `plan`; `project` fixes `accept-edits`; `task` uses `accept-edits` unless an explicit raw mode is supplied. |
| `--tier cheap|bulk|hard|hardest|default` | `AGY_WORKER_TIER` | explicit legacy tier; a model label is also accepted |
| `--model EXACT_MODEL` | `AGY_WORKER_MODEL` | reviewed exact slug, or adjustable base used with effort |
| `--effort low|medium|high` | `AGY_WORKER_EFFORT` | requires an adjustable base and resolves to one exact slug |
| `--literal-model EXACT_SLUG` | — | CLI-only caller-owned pass-through; no matrix claim. A bounded non-gating version observation may support exact diagnostics without changing the selected model. |
| `--workdir DIR` | — | agy's workspace |
| `--add-dir DIR` | — | repeatable file-tool root; must resolve inside `--workdir` |
| `--persona NAME` | — | Optional bounded worker-role prompt; it does not authorize work or prove quality. |
| `--idle-timeout DURATION` | `AGY_WORKER_IDLE_TIMEOUT` | no valid progress for this long ends the attempt; default `10m` |
| `--hard-timeout DURATION` | `AGY_WORKER_HARD_TIMEOUT`; `AGY_WORKER_TIMEOUT` (deprecated alias) | initial per-attempt deadline; default `2h` |
| `--max-runtime DURATION` | `AGY_WORKER_MAX_RUNTIME` | caller-owned absolute cap across extensions; default `12h` |
| — | `AGY_WORKER_JOB_ID` | safe artifact directory name |

Worker exits: `0` ok · `2` no prompt · `3` empty output · `4` schema invalid ·
`5` unclassified agy failure · `6` permission gate · `7` compatibility review required ·
`8` compatibility evidence unavailable · `9` idle timeout · `16` hard deadline ·
`17` provider timeout (reserved) · `18` authentication failure (reserved) ·
`19` provider unavailable (reserved) ·
`20` local status, binding, or verification-copy runtime unavailable · `21` resume failure · `22` cancelled ·
`23` output oversized · `24` provider quota exhausted · `25` provider terminal error
with a preserved valid candidate · `26` direct-selection preflight failure · `64` invalid usage.

The reserved `17`–`19` exits require an exact, version-bound reviewed signature.
The accepted agy `1.1.16` baseline and observed `1.1.22` surface have no reviewed
signature allowlist, so an unproven
provider timeout, authentication error, or provider outage remains
`agy_failed_unclassified` with exit `5`; the supervisor does not infer a reason from
free-form stderr.

Exit `24` is narrower than a general rate-limit classifier. It currently recognizes
only the exact, structurally valid agy `1.1.13` terminal quota shape reviewed for
Issue #59. Status exposes only a bounded, decreasing `retry_after_seconds`; it never
prints the error text, conversation, prompt, model, path, envelope, or raw log. The
worker does not sleep, retry, restart, or change the caller's model automatically.
Wrong-version or altered quota terminals without a report remain `invalid_envelope`
with exit `4` and `failure_stage=missing_structured_output`. The exact recognized
`1.1.13` quota terminal remains `provider_quota_exhausted` with exit `24` when it has
no report, with `failure_stage=missing_structured_output`.

Only provider `init`, `step_update`, and terminal `result` events can update v9
`last_activity` to `provider_initialized`, `progress_signal`, or
`terminal_received`. They renew only an idle lease; they never prove success or
extend the hard deadline or the caller-owned maximum.
The supervisor forwards the maximum as agy's `--print-timeout`, owns the shorter
local clocks and process group, and records only sanitized elapsed/progress-age/count,
attempt origin, terminal reason, and resume availability. It never prints progress,
prompts, raw stderr, or a conversation ID.

There is no automatic fresh retry or continuation. A candidate-free failed state may
offer SHA-approved `resume` for the exact stored conversation or SHA-approved fresh
`restart`. A valid provider `ERROR` (exit `25`) candidate is `unreviewed`: obtain it
with `result`, supply driver Verification v2, then `continue` or `finalize`. A valid
provider `CANCELED`/`CANCELLED` (exit `22`) candidate is preserved for `result` and
finalization, or an explicit fresh `restart`; it is never resume- or continue-eligible.
None of those outcomes is provider success. `status`, `wait`, `result`, `extend`, and
`cancel` describe the local controller, not agy/provider status or proven remote
cancellation. A locally cancelled job therefore reports `remote_cancel_unverified`.

The dispatcher creates each job directory and its task, full prompt, stream, stderr,
staged prompt, and envelope under an owner-only mask, even when the caller's mask is
permissive. A missing `AGY_WORKER_LOG_DIR` is created owner-only. An existing final
log root must be a real directory owned by the current user with no group/other write
bits; a final symlink is rejected, and the accepted root is resolved physically. The
dispatcher does not rewrite that caller-owned root or change the caller's umask inside
agy, so this boundary does not silently change candidate-file permissions. A job ID
is exclusive: an existing directory, file, or symlink at that job path is rejected
before the prompt is read or agy is invoked. Oversized staged prompts return to
owner-only modes after the child, on an early exit, and before HUP, INT, or TERM is
re-raised with its normal status. This bounded final-root check does not validate the
full ancestor chain or claim to eliminate every filesystem time-of-check/time-of-use
race.

### Progress-aware local jobs

`run` remains synchronous. For a long explicitly approved job, `start` returns an
opaque job ID after the local controller handshake. `status`, `wait`, `result`,
`resume`, `restart`, `continue`, and `finalize` each default to machine-readable JSON and accept
`--format text`; text is exactly three sanitized, driver-owned lines and excludes
prompts, worker prose, conversation IDs, paths, and raw logs. Lines one and two carry
the sanitized reason/failure stage and current/maximum cycle count. For an unreviewed
current candidate, line three gives the exact bound `result` JSON command, then says
to review it and build Verification v2 before Codex chooses an eligible `continue` or
`finalize`; it does not present either as a controller recommendation. For a current
candidate whose `verified`, `partially_verified`, `rejected`, or `blocked` disposition
is already recorded, line three instead offers only an optional finalized-result JSON
readback and says not to construct Verification v2, `continue`, or `finalize`. If
the same current `available_actions` guard exposes `restart`, that line also prints
its exact fresh-restart command as an alternative; it makes no recommendation to use it.
Other states show their exact mechanically guarded command (or `none`). Every emitted action or
stale-approval rerun command uses the caller-resolved symbolic launcher
`"$PIPELINE/agy-worker.sh"`; export `PIPELINE` before copying and running it.
`result` returns a
bound schema-valid candidate only when `result_available` is true; it is not a success
or acceptance claim. `extend` and `cancel` require the current state SHA; eligible
`resume` uses the exact stored conversation and its current approval SHA, while
`restart` uses that SHA but labels a new attempt `fresh-restart`.

In this interface, **bound result** means a current candidate whose
`result_available` field is `true`. The text surface uses that term so a recognized
but unavailable candidate is not mistaken for a retrievable result.

Lifecycle state v9 uses `dispatching` for an active initial, resume, or restart
attempt; `attempt-failed` for a pre-candidate failure; `awaiting-verification` for a
recognized candidate; `repairing` for an active continuation; and `repair-failed` for
an actual failed continuation attempt. Controller terminal phases are `completed` or
`blocked`; exact Codex driver decisions/dispositions are `verified`,
`partially_verified`, `rejected`, or `blocked`.
Its additive public fields include candidate recognition/source/availability, driver
disposition, failure stage, `last_activity`, mechanically derived `available_actions`,
and the deprecated mechanical `next_action`/safe current-SHA-command aliases,
and privacy-bounded worktree reconciliation (`available`, `unavailable`, or
`not_applicable`). `has_prior_candidate` is a deprecated compatibility hint, not a
cleanliness signal. The controller captures a baseline before provider launch and a
terminal candidate only after the provider process group is closed and reaped. Queued
dispatch paths recompute the exact digest immediately before `Popen`; `continue` and
`finalize` recompute it against the bound candidate. Each observation uses a bounded,
no-follow double-manifest comparison: its topology pass binds directory names, kinds,
directory metadata, and empty directories without re-reading regular-file content,
while the primary observation/revalidation binds listed file bytes. It binds the Git,
index, root, and selected-Git target facts while provider activity is
controller-quiesced. Exact bound-root checks precede every Git enumeration. V9 also
rejects any resolve-undo (REUC) record, malformed or duplicate resolve-undo output,
and resolve-undo drift between its fixed listing passes. Detected drift or an
unavailable observation fails closed.

Reconciliation is only a physical-change signal. It is not a filesystem snapshot,
FSEvents watcher, hostile same-user tamper resistance, clean-worktree/review/acceptance
proof, or semantic recommendation. The local owner, same-UID processes, and OS
administrators remain in the TCB; a mutation after an entry's final read is a residual
outside the portable guarantee. V1 remains readable historical evidence only. A
V3/V4 *current* bound result may perform its first lifecycle transition only with
both its state SHA and the exact `migration_binding_sha256` emitted by `status`; the
controller recomputes that binding under the transition lock. V3/V4
`last_success_*`-only evidence remains read-only. Persisted V5/V6
state retains its exact legacy digest; V7 retains its exact semantic-v1 digest; and
V8 retains its explicit semantic-v1 algorithm. A V5/V6 transition proves the legacy
digest and then atomically captures a fresh semantic-v1 V9 baseline/candidate; V7/V8
reuse their exact proved semantic observation. New V9 state persists its snapshot
algorithm and private stable root/Git-administration identity, which deliberately
excludes mutable worktree, index, HEAD, ref, and object content.

Every explicit `explore`, `task`, or `project` workflow has the driver-owned quality
loop. `status` exposes the controller phase, current and maximum cycle count, check
summary, candidate availability, and only mechanically applicable actions. Public
`assurance` is `null` until a bound driver `finalize` records its exact disposition;
`phase` is deprecated raw compatibility storage, while `controller_phase` is the
current mechanical projection; neither is an assurance recommendation. `continue` and `finalize`
require Verification v2 JSON bound to the current candidate SHA. It records bounded
passed/failed/advisory/missing checks plus coverage, verified-findings,
unresolved-gaps, and whether the driver completed diff review. Codex may use any of
that review evidence—including advisory, gap, or review-driven findings—to request an
exact-conversation `continue`; the controller preserves the bound intent rather than
requiring a failed or missing check. Codex's `verified` policy is strict: `explore` needs complete coverage, zero unresolved gaps, zero
failed checks, and zero missing checks; `task`/`project` need at least one passed check,
zero failed/missing checks, and completed diff review. `finalize` accepts the exact
Codex declaration `verified`, `partially_verified`, `rejected`, or `blocked`; a worker
cannot self-assign any disposition. The controller never executes a command from this
JSON or starts a fresh conversation automatically.

V3/V4 `last_success_*`-only records retain an explicitly `unknown_bound_legacy`
historical result fact. They are never promoted to a provider-success candidate,
driver disposition, continuation, or finalization input. `result` may expose one only
after its exact command, linked-worktree boundary, file hash/inode, and schemas bind.
Its three-line text surface calls it historical evidence only. An unreviewed current
bound result instead directs the driver to retrieve JSON, run its checks, construct
Verification v2, and choose any eligible continuation or finalization itself. A
current result with a recorded final disposition offers only optional finalized-result
JSON readback; it does not invite another Verification v2, `continue`, or `finalize`.
When mechanically available, it also shows the exact fresh-restart alternative.

#### Reading lifecycle JSON and supplying Verification v2

For new integrations, use `controller_phase` for mechanical progress and
`driver_disposition` for the recorded Codex decision. Ignore the deprecated
compatibility fields `phase`, `next_action`, `next_action_command`, and
`has_prior_candidate` unless maintaining an existing integration.

Read public lifecycle JSON in this order: first `status` for `state_sha256`,
`controller_phase`, `cycle`/`max_cycles`, `failure_stage`, and
`available_actions`; then use `candidate_sha256` only when `result_available` is
`true`; then retrieve `result` only when its mechanically derived action is present.
Review that bound result and build driver evidence before choosing an eligible
`continue` or `finalize`; the controller does not choose either. If the candidate hash
is `null`, do not construct Verification v2 for it.

Driver checks that can write bytecode, caches, coverage output, generated files, or
other artifacts must run in an isolated verification copy. Do not delete or regenerate
artifacts in the candidate to make its snapshot match again: tracked, untracked,
deleted, and ignored paths are all bound candidate bytes. First inspect the candidate
read-only (including any Git-dependent diff); then create a new directory under a
private parent and run build/test commands in that copy. The copy deliberately omits
`.git`, so Git-dependent checks stay read-only against the original candidate and
build/test commands run in the copy:

```bash
VERIFY_PARENT="$(mktemp -d -t agyworker-verify.XXXXXX)" || exit $?
VERIFY_PARENT="$(CDPATH= cd -- "$VERIFY_PARENT" && pwd -P)" || exit $?
VERIFY_DIR="$VERIFY_PARENT/candidate"
"$PIPELINE/agy-worker.sh" verification-copy --job-id "$JOB_ID" \
  --destination "$VERIFY_DIR" --format text
( cd "$VERIFY_DIR" && /usr/bin/python3 -m pytest -q )
```

`verification-copy` rebinds the current result, command, schemas, root, and candidate
before copying, preserves regular-file bytes and executable bits, and rebases every
contained symlink to an equivalent relative target inside the copy. It rejects
broken/outward/Git-admin links and rebinds the
source afterward. Its destination must be new, private, canonical, and outside the
candidate. It records no acceptance or driver result; a source drift makes both the
copy action and `continue`/`finalize` unavailable.

The bounded pre/copy/post binding assumes an owner-controlled, quiescent candidate.
It detects ordinary source or destination-parent drift, but does not claim same-UID
tamper resistance: a local same-UID actor could replace a regular file with an outward
link only during a read and restore it before the later checks. A failed copy is never
reported as created and its destination is not a usable verification workspace.
After wrapper argument parsing, an unavailable candidate, binding failure, copy failure,
or invalid destination returns exit `20`; malformed wrapper arguments remain exit `64`.

`available_actions` is the current surface. `next_action` and
`next_action_command` remain deprecated mechanical compatibility aliases;
`has_prior_candidate` and raw `phase` are also retained compatibility fields. They
are not recommendations or acceptance facts, and no current public field is removed.

The canonical Verification v2 validator is the bounded
`_validate_verification` and `_require_current_candidate_verification` implementation
in [`skills/agy-worker/runtime/scripts/agy_dispatch.py`](skills/agy-worker/runtime/scripts/agy_dispatch.py).
There is intentionally no standalone Verification v2 schema: the validator is the
canonical source and accepts no unknown fields. This complete example takes the
candidate digest from the public status surface, not a path, worker prose, or a
locally rehashed candidate:

```bash
: "${PIPELINE:?set PIPELINE to the resolved skill runtime}"
: "${JOB_ID:?set JOB_ID to the controller job ID}"
: "${STATE_DIR:?set STATE_DIR to an existing private state directory}"
test -d "$STATE_DIR" || { echo "STATE_DIR is not a directory" >&2; exit 64; }

STATUS_JSON="$("$PIPELINE/agy-worker.sh" status --job-id "$JOB_ID" --format json)"
STATE_AND_CANDIDATE="$(printf '%s\n' "$STATUS_JSON" | python3 -c '
import json, re, sys
status = json.load(sys.stdin)
state_sha = status.get("state_sha256")
candidate = status.get("candidate_sha256")
if not isinstance(state_sha, str) or re.fullmatch(r"[0-9a-f]{64}", state_sha) is None:
    raise SystemExit("status state SHA is unavailable")
if status.get("result_available") is not True or not isinstance(candidate, str) or re.fullmatch(r"[0-9a-f]{64}", candidate) is None:
    raise SystemExit("status has no current bound candidate")
print(state_sha, candidate)
')" || exit $?
read -r STATE_SHA CANDIDATE_SHA <<EOF
$STATE_AND_CANDIDATE
EOF

python3 - "$CANDIDATE_SHA" > "$STATE_DIR/verification-v2.json" <<'PY'
import json, sys

json.dump({
    "schema_version": 2,
    "summary": "driver reviewed the bound candidate and found one repair",
    "passed_checks": ["unit"],
    "failed_checks": ["targeted-regression"],
    "advisory_checks": 0,
    "missing_checks": 0,
    "candidate_sha256": sys.argv[1],
    "coverage": "partial",
    "verified_findings": 1,
    "unresolved_gaps": 1,
    "diff_review_complete": True,
}, sys.stdout, sort_keys=True, separators=(",", ":"))
sys.stdout.write("\n")
PY

"$PIPELINE/agy-worker.sh" continue --job-id "$JOB_ID" \
  --approve-state-sha "$STATE_SHA" < "$STATE_DIR/verification-v2.json"
```

`resume`, `restart`, `continue`, `finalize`, `cancel`, and `extend` all use a current
`--approve-state-sha`; `wait` instead uses `--after-state-sha`, while read-only
`status`/`result` need neither. `resume` and `restart` both show the approval flag in
help; resume preserves the exact conversation, restart is a fresh attempt. Direct
reviewed model selection is a separate compatibility approval:
`--compatibility-disposition proceed --approve-help-sha SHA256`; it never changes the
caller-selected model or authorizes lifecycle writes. Common read commands accept
`--format json|text`, with JSON as the canonical input surface.

The provider-facing envelope permits omission of report-only `commands_run` and
`tests_run`; the controller restores omitted fields to empty arrays before requiring
the canonical envelope, where both fields are mandatory. The worker summary is capped
at 8,192 characters. These fields are provider claims only: non-empty command/test
arrays are rejected by the gate and never executed.

The Codex skill does not split a comprehensive task merely to fit a timer. While
progress is fresh, it may extend the initial deadline by `2h` at 80% utilization,
within the maximum cap; it provides only a sanitized progress summary every `30m` and
honors a user cancel request at any time. An extension is not another provider
attempt. A timeout never causes a new provider call without the user's explicit
resume or restart decision.

For `plan`, the complete user/repository prompt is owner-private staged input and agy
receives only a fixed driver prompt, with slash expansion enabled so its documented
plan transformation can operate. `accept-edits` keeps slash expansion disabled by
default. Plan mode is not a filesystem-security boundary: the disposable worktree and
post-run no-change gate remain the controls that make its read-only claim testable.

### Model selection is explicit; recommendations are advisory

The dispatcher does not infer difficulty or score the gap between worker output and
the expected result. With no selector it sends no model and lets agy own its default:

| Tier | Current configured model label |
|---|---|
| `cheap` | `gemini-3.6-flash-low` |
| `bulk` | `gemini-3.6-flash-medium` |
| `hard` | `gemini-3.1-pro-high` |
| `hardest` | `claude-opus-4-6-thinking` |
| `default` | no `--model`; let agy choose |

The named tier constants predate the current compatibility matrix. During version
drift they remain legacy best-effort labels, not verified claims that a tier is still
cheap, hard, provider-bound, or behaviorally equivalent. `--tier default` and the
no-selector path send no model. Direct reviewed selection is intentionally stricter:

```bash
./agy-worker.sh --model gemini-3.6-flash-high ...
./agy-worker.sh --model gemini-3.6-flash --effort high ...
./agy-worker.sh --literal-model future-model-1.2 ...
```

The second form resolves through the active, exact-SHA, agy-version/source-bound
matrix to `gemini-3.6-flash-high`. Flash 3.7, 3.6, and 3.5 accept low/medium/high;
Gemini 3.7 `minimal` is unsupported. Pro 3.1 accepts low/high and rejects medium.
Sonnet, the Opus thinking-labelled slug, the GPT
medium-labelled slug, and every already-compound slug are fixed exact choices and
reject an effort input. The wrapper sends exactly one downstream `--model` and never
sends agy's separate `--effort` or an invented thinking flag.

`--literal-model` is the deliberately narrow escape hatch approved for fast-moving
agy releases. It accepts one lowercase closed slug from CLI only, performs no version
probe or matrix lookup, sends that exact slug once, and records
`compatibility_status: unreconciled-pass-through`. It cannot be combined with tier,
model, effort, environment selection, inference, recommendation, fallback, or
thinking flags. The provider or agy may reject it; the record makes no compatibility,
cost, provider, or routing claim.

Selector sources have no silent precedence. A component may come from CLI or its
matching environment variable, never both—even when equal. Repeated selectors,
explicit empty values, tier plus any model/effort source, effort without a model,
unknown models, and unsupported pairs fail before the task is read or a worker is
dispatched. Model and effort may come from different sources when each has exactly
one source. Reviewed `--model` selectors use a bounded safe-target semantic
`agy --version` probe followed by strict critical `agy --help` interface probing.
Every direct reviewed selection preserves the caller's exact resolved slug. An exact
matrix-version match proceeds after the structural probe. Compatible version drift is
review-required until Codex supplies both `--compatibility-disposition proceed` and the
exact `--approve-help-sha` from a bounded local raw `agy --help` inspection. Codex must
independently compare the inspected raw-help SHA with the sanitized evidence; it must
not treat a copied digest as semantic approval. On drift review-required the selector
prints one relation-neutral sanitized JSON evidence object to stderr with
installed/matrix versions, relation/status, capability and raw-help digests, exact
caller model/effort plus their sources, resolved agy model, and a safe retry selector
fragment (argv/environment only, never prompt or path); it never prints a local
executable path or help prose. The resulting V3 selection record binds that drift disposition,
raw-help SHA, capabilities SHA, matrix facts, caller model/effort/provenance, and safe
executable object. The executable binding uses a bounded no-follow descriptor digest,
target ctime, and complete resolved path authority; only macOS's `/var` to
`/private/var` alias is normalized. It records no model availability assessment.
Tier/default and literal behavior perform no such probe.
HUP, INT, or TERM during that preflight closes its exact process group and returns
`129`, `130`, or `143` before the task is read or a selection record is published.

The resolved slug, input provenance, installed version, matrix version/source, and
matrix SHA-256 are frozen before attempt one in owner-private
`logs/<job>/selection.json`. A user-authorized resume/restart reuses the exact frozen
selection even if the matrix file changes later. This driver-owned record is
provenance, not worker evidence, a QA receipt, or an acceptance path. It never
silently escalates cost or changes model after failed tests, scope violations, or
malformed output.

If a final direct-selection reprobe fails, that frozen record is preserved only as
local evidence: no same-job `resume` or `restart` is safe, because it would repeat
the rejected executable/interface binding. Codex must first inspect current sanitized
`agy --help` evidence, then create a new job using the same caller-selected model and
effort; it must not silently rebind or replace either selection.

`model-recommendation.sh` is a separate, read-only policy layer. It prints a visible
JSON recommendation before dispatch or after a gate result, but never calls `agy`,
runs `qa-gate.sh`, changes job state, or applies its recommendation. Every successful
result includes the caller's tier or distinct user/resolved direct selection, a
recommended named tier or explicit `no-escalation`, rationale, controlled
driver-owned evidence, relative cost impact,
and both `recommendation_only: true` and `applied: false`.

For example, a driver that has classified a task as a mechanical batch can ask for a
pre-dispatch recommendation without changing `--tier cheap` (or any other caller
choice):

```bash
./model-recommendation.sh --stage pre-dispatch \
  --selected-tier cheap --evidence batched-mechanical
```

Pre-dispatch evidence is deliberately finite: `bounded-routine`,
`batched-mechanical`, `cross-file-bounded`, or `high-complexity-bounded`.
The policy maps those profiles to `cheap`, `bulk`, `hard`, or `hardest`; it recommends
a higher tier only when the selected named tier is below that profile. `default` and
custom model labels are non-rankable and therefore never escalated.

After independent verification, the driver can classify the observed outcome:

```bash
./model-recommendation.sh --stage post-gate \
  --selected-tier bulk --evidence driver-verification-failed
```

Only `driver-verification-failed`, `driver-quality-review-failed`, and
`expected-edits-missing` may recommend one higher named tier. `gate-accepted`,
`permission-failed`, `authentication-failed`, `scope-policy-failed`,
`human-required`, `noncompleted-worker-outcome`, `untrusted-worker-claim`, and
`invalid-envelope` always produce
`no-escalation`. Permission, authentication, scope policy, and human authorization
must be resolved at their own boundaries; extra model spend cannot fix them. The
evidence code must be selected from driver-observed state, never copied from a worker
claim. Unsupported, cross-stage, repeated, missing, or ambiguous inputs fail with
exit 64 and no JSON output.

Direct advisories use `--selected-model` and optional `--selected-effort`. They
resolve only against the checked-in reviewed matrix, never probe or dispatch agy,
and always remain unranked `no-escalation` decisions. Their JSON labels the user
model, optional user effort, exact resolved model, and matrix binding without
changing or redispatching the selection.

Gate controls:

- `--base FULL_COMMIT_ID` is required. Capture it before dispatch; mutable names such
  as `HEAD` and branch names are rejected so a worker cannot move the comparison base.
- `--verify COMMAND` is mandatory for acceptance and repeatable; only these
  driver-owned commands run. Empty/whitespace commands are rejected.
- `--only PATHGLOB` is repeatable and constrains every changed path.
- `--allow PATHGLOB` permits a known undeclared artifact but does not override `--only`.
- `--expect-edits` turns a completed no-op into exit 13.
- Exit 15 is escalation, never acceptance. Read `open_questions` and decide whether
  one corrective dispatch is justified.

### Manage one isolated local job

`job.sh` records one explicit branch-backed worktree in a private external state
file, delegates verification to `verify-job.sh`, and keeps every external action
outside the lifecycle. It never dispatches agy, commits, pushes, opens a PR, merges,
releases, or changes a model. Its Python entry is process-owning so signal authority
survives output flush; invoke `job.sh` as a command/subprocess and do not embed its
`main()` in a host process. Capture a full immutable base and create the state
directory and worktree outside the repository:

Lifecycle-owned Git commands use fixed `/usr/bin/git` with system/global and caller
Git environment removed, a fresh private empty hooks directory, and prompts, pagers,
fsmonitor, external diff, protocols, and recursive submodules disabled. `init` also
rejects local included config that could authorize hooks, fsmonitor, pagers, or
content filters, and rejects every effective `filter` attribute in the immutable base
tree or repository info attributes. Branch names must equal Git's canonical
`check-ref-format --branch` output byte-for-byte; reflog/revision shorthand is not a
branch authority. These controls prevent lifecycle initialization from executing
repository-provided checkout hooks or content filters; rejected preflight creates no
state, ref, or worktree.

```bash
BASE="$(git rev-parse HEAD)"
umask 077
STATE_DIR="$(mktemp -d -t agyworker-job-state.XXXXXX)"
WORKTREE_DIR="$(mktemp -d -t agyworker-job-worktree.XXXXXX)"
rmdir "$WORKTREE_DIR"

./job.sh init \
  --state "$STATE_DIR/job.json" \
  --repo "$(pwd -P)" \
  --worktree "$WORKTREE_DIR" \
  --branch codex/my-isolated-job \
  --base "$BASE" \
  --job-id my-isolated-job

./job.sh status --state "$STATE_DIR/job.json"
```

Run the separately approved worker against the printed worktree through the normal
dispatcher. Then bind the resulting envelope to the same gate and receipt protocol:

```bash
./job.sh verify \
  --state "$STATE_DIR/job.json" \
  --receipt "$STATE_DIR/receipt.json" \
  --envelope envelope.json \
  --only 'tests/**' --expect-edits \
  --verify "git -C '$WORKTREE_DIR' diff --check"
```

Pre-gate dispatch failure is separate from receipt cleanup. `job.sh abort` requires
the exact terminal dispatch-state binding, a closed supervisor process group, current
job/state/candidate SHA approvals, and an empty candidate unless the caller explicitly
adds `--discard-unverified`. It refuses active, receipt-bound, gate-passed, routed,
or otherwise unbound residuals.

For `verified-gate-passed`, `preserve-instructions` prints review/commit/integration
commands but runs none of them. Cleanup is deliberately narrower: only Receipt exits
`10`–`14` with verdict `rejected` qualify. First run `status` again and manually copy
its current `job_id`, `state_sha256`, and `receipt_candidate_state_sha256` into one
fresh command:

```bash
./job.sh cleanup \
  --state "$STATE_DIR/job.json" \
  --approve-job EXACT_JOB_ID \
  --approve-state-sha CURRENT_STATE_SHA256 \
  --approve-candidate-sha RECEIPT_CANDIDATE_STATE_SHA256
```

Cleanup revalidates the receipt, worktree registration, branch/ref/base, current
candidate digest, deletion domain, and all three approvals. It persists progress
before each destructive step, removes only the exact registered worktree, and deletes
only the exact unchanged branch ref with compare-and-delete. A reconciled interrupted
state returns without spending an old state approval; inspect the new status and issue
a fresh command. Gate-passed, routed, committed, moved, tampered, stale, foreign,
special-node, nested-repository, or digest-mismatched states are retained for manual
recovery. Candidate-bound symlinks are removed as link nodes only and are never
followed. The private mode-`0600` cleaned tombstone is retained; deleting it is a
separate manual retention decision.

### Preserve a local Evidence Receipt v1

`verify-job.sh` runs the same canonical gate and, only for gate outcomes `0` and
`10`–`15`, durably publishes a private JSON receipt. The receipt records hashes of
the exact envelope snapshot, ordered path policy, and driver-owned verifier commands;
the immutable base; the gate's initial and final candidate-state digests; and the
gate's exact outcome. It contains deterministic labels such as `verify-001`, never
the verifier command text or output.

Create a new owner-private directory outside the audited repository and choose a new
receipt path. The parent and path must already be canonical; the command rejects a
symlink, overwrite, repository-contained target, or group/other-accessible parent.

```bash
umask 077
RECEIPT_DIR="$(mktemp -d -t agyworker-receipts.XXXXXX)"

./verify-job.sh --receipt "$RECEIPT_DIR/job.json" \
  --envelope envelope.json --repo "$WT" --base "$BASE" \
  --only 'tests/**' --expect-edits \
  --verify "git -C '$WT' diff --check" \
  --verify "cd '$WT' && python3 -m pytest -q tests/test_parser.py"
```

`--selection FILE` may bind one current, validated G1 selection record.
`--pre-recommendation FILE` may independently bind one canonical `pre-dispatch`
advisory; a post-gate, cross-stage, mismatched, or `applied: true` advisory is
rejected. The command never creates or applies a recommendation. Both inputs are
optional and at most once. There is no implicit scan of `logs/`.

The receipt maps gate `0` to `gate-passed`, gate `10`–`14` to `rejected`, and gate
`15` to `routed`, then returns that exact exit only after file `fsync`, atomic
no-overwrite hard-link publication, and parent-directory `fsync`. Input or gate
preflight errors return `64`; missing, malformed, mismatched, unknown, or interrupted
gate evidence returns `70`; receipt validation or durable-publication failure returns
`74`. Those failures publish no receipt. The evidence descriptor and capability are
an internal `verify-job.sh` handoff, not a supported direct `qa-gate.sh` interface;
direct gate use without them retains the existing output, side effects, and exits.
The wrapper removes shell/Python startup controls before launching the evidence-mode
gate, gate-owned Python runs isolated without site startup, and the gate parent closes
the descriptor with a shell builtin before any verifier shell or interpreter starts.
Verifier descendants therefore cannot observe or write the handoff. Other verifier
environment values are preserved; the unsafe startup controls and internal handoff
variables are intentionally absent. HUP, INT, or TERM from private
snapshot creation through publication and cleanup returns `70`, closes and reaps an
active gate/verifier process group, and removes wrapper-owned partial artifacts.

Every receipt says explicitly that it is unsigned and not tamper-evident. Its
existence does not make a candidate accepted, signed, authentic, correct, or safe.
Only `qa-gate.sh` supplies the gate result, and human diff review remains required
after `gate-passed`. The dependency-free validator can check a receipt and optionally
bind the original envelope or a separately trusted candidate-state digest:

```bash
python3 -B skills/agy-worker/runtime/scripts/evidence_receipt.py validate \
  --receipt "$RECEIPT_DIR/job.json" --envelope envelope.json
```

Render the same validated receipt as compact text, canonical JSON, Markdown, or a
GitHub Step Summary without invoking agy, git, the gate, model routing, or the
network:

```bash
./evidence-report.sh --receipt "$RECEIPT_DIR/job.json" --format text
./evidence-report.sh --receipt "$RECEIPT_DIR/job.json" --format markdown \
  --output "$RECEIPT_DIR/job.md"
./evidence-report.sh --receipt "$RECEIPT_DIR/job.json" --format json
```

Standard output is the default. `--output` must be one new canonical absolute path;
the renderer publishes mode `0600` without overwrite. It reports only the validated
receipt's verdict, gate outcome/exit, immutable hashes, deterministic verification
labels, optional-binding presence, and fixed integrity/human-review limits. It never
prints source, prompts, raw commands, logs, absolute repository paths, or worker prose.
Malformed, inconsistent, unsupported, control-bearing, injection-shaped, oversized,
or externally mismatched input produces no report. Optional `--envelope`,
`--selection`, `--pre-recommendation`, `--initial-state-digest`, and
`--final-state-digest` bind separately trusted artifacts before rendering. A report is
still an unsigned rendering: it cannot authenticate a rewritten receipt, improve the
gate verdict, or turn `gate-passed` into acceptance without human diff review.

In GitHub Actions, redirect stdout explicitly; the reporter never discovers or writes
`GITHUB_STEP_SUMMARY` itself:

```yaml
- name: Render bounded evidence summary
  shell: bash
  run: ./evidence-report.sh --receipt "$RUNNER_TEMP/job.json" --format github-step-summary >> "${GITHUB_STEP_SUMMARY:?}"
```

Do not pass fork-controlled paths, repository content, tokens, or secrets to this
step. It validates one previously produced receipt, emits no workflow commands, and
does not comment, upload an artifact, or call a GitHub API.

The stdout-only path returns normally and can be used for in-process pure rendering.
The `--output` CLI path is deliberately process-owning: it retains signal rollback
authority through an atomic `os._exit(0)` boundary. Run file-output mode as the
documented command or a subprocess; do not call its `main(argv)` from a host process.

`--allow-slash-commands` exists for callers who fully control the entire prompt, but
is intentionally omitted from normal examples. It disables protection against
embedded `/skill` or slash-command text; leave slash expansion disabled for content
derived from a repository or another model. The plan dispatcher is the narrow
exception: it stages that content privately and enables slash expansion only for a
fixed driver prompt so upstream's documented plan transform can run.

---

## Explicit updates and tool compatibility checks

There is no automatic updater. Checking is read-only:

```bash
./update.sh check
```

It reports the latest stable project tag without fetching it, then reports installed,
verified, official stable-release/source drift, and 30-day documentation-review age
separately for agy and Codex CLI. The agy section also checks one fixed official
`darwin_arm64` distribution-manifest canary. It validates only that small JSON
document and never requests, downloads, hashes, or executes the referenced archive.
Exit `0` means all required evidence is available and unchanged. Exit `3` means
established drift, a due review, or a missing installed tool. Exit `2` means evidence
is unavailable or malformed, so the result is inconclusive—not green. Both tools are
reported before those results are aggregated, with `2` taking precedence over `3`.

Project, agy, and Codex release/source evidence comes from exact fixed
`https://api.github.com/repos/...` REST paths. The stdlib-only client disables ambient
HTTP proxies, refuses redirects, validates strict JSON and response metadata, and
captures responses under time and byte limits. Stable project tags resolve through
the compact exact `git/ref/tags/<tag>` document rather than the potentially large
commit document. Release documents have a separate 512-KiB ceiling; ref/source
documents retain the 256-KiB ceiling. The supervisor also bounds installed
`agy --version` and `codex --version` probes, discards their raw stderr, and terminates
the complete child process group on timeout, output overflow, or HUP/INT/TERM. Neither
`check` nor `check --watch` makes a Git network request, so repository or global
`url.*.insteadOf`, credential helpers, and Git proxy settings cannot redirect their
official release/source evidence.

The release origins, upstream sources, distribution-manifest endpoint, release
channels, and 30-day cadence are fixed in the updater rather than overridable through
arguments, environment variables, or configuration. The check never fetches into
Git, pulls, applies, or writes a baseline. A future per-tool review date is
inconclusive rather than silently postponing review.

Apply is always explicit:

```bash
./update.sh apply v1.2.3       # or omit the tag to select the latest stable release
```

`apply` refuses a dirty or detached checkout, accepts only stable release tags from
the expected GitHub origin, verifies that the fetched tag resolves to the exact
remote commit and is a fast-forward, and runs Bash syntax, a temporary skill-install
preflight, and every offline suite in a disposable candidate worktree. It also refuses
an update when the release would begin tracking and overwrite an ignored local file;
unrelated ignored caches are left alone. Candidate failure leaves the checkout
unchanged.

Only after those checks does it fast-forward the current branch and rerun `install.sh`
against the real Codex skill destination. A real-destination permission failure cannot
be rolled back safely after the Git fast-forward: `apply` exits 4, reports **PARTIAL
UPDATE**, and tells you to fix the destination and rerun this checkout's `install.sh`.
The fixed API commit must match the commit fetched during `apply`, but the explicit
apply path still uses `git fetch` and therefore honors the caller's Git transport
configuration, including URL rewrites and Git proxies. The read-only transport
isolation described above does not harden that mutating path. Release maintainers must
also protect the GitHub account and tag-publishing process.

The fixed primary sources and exact reviewed revisions are recorded in
[`compat/sources.md`](compat/sources.md). A separate daily/manual macOS compatibility
watch runs the official-evidence-only mode without installing agy or Codex. It writes
only a bounded Step Summary, preserves the same `0`/`3`/`2` meanings, is not a required
pull-request check, and cannot advance metadata or open an issue or pull request.

### Optional local daily notifier

macOS users may install an owner-private LaunchAgent that runs the same read-only
check once per day and displays a notification only when a drift fingerprint changes:

```bash
./update-notifier.sh install
./update-notifier.sh status
./update-notifier.sh run       # manual one-shot check
./update-notifier.sh refresh   # explicit rebind after a maintenance-required status
./update-notifier.sh uninstall
```

The notifier has no independent network or mutating Git authority. Its hash-bound
snapshot child invokes `update.sh check --watch`, whose existing contract performs
fixed bounded read-only HTTPS observations and read-only local Git inspection. It
never applies an update, edits a baseline, invokes agy/Codex/provider work, or reads
personal configuration. Installation binds the complete behavior-bearing source
manifest, canonical account HOME, exact launchd label, private state, and an
authenticated resumable uninstall ledger. Source drift enters an explicit maintenance
state rather than silently rebinding the installed snapshot.
Signals, overlapping operations, ambiguous launchctl outcomes, nested process groups,
and replacement files fail closed. A completed uninstall intentionally retains an
authenticated inert ledger/tombstone, prior result, and lock so recovery and
notification deduplication remain resumable across reinstall; additional private
residuals may remain on drift or failure. A valid loaded notifier now reports source
drift as `maintenance-required`, sends at most one sanitized maintenance notification,
and pauses ordinary monitoring until the owner explicitly runs `refresh`; refresh
rebinds only through the existing serialized uninstall/install lifecycle and never
updates code, metadata, or a tool. A notification attempt is the final irreversible
UI side effect and cannot be retracted.

`refresh` also has one deliberately narrow migration path for the immediately prior
v0.8.0 installation ledger. It recognizes only that exact 18-file manifest, validates
its account/source/Git binding and installed bytes under the historical contract,
then uses its authenticated resumable uninstall authority before creating a fresh
current 21-file installation. Unknown, partial, expanded, tampered, or unrelated
legacy shapes fail closed; the migration never rewrites private authority in place
and requires no manual private-state editing. Other notifier commands retain the
strict current manifest contract.

The separate optional [measurement ledger](docs/MEASUREMENT.md) records only explicit
sanitized public evidence and renders fixed 30/60/90-day reports. Neither watcher nor
notifier writes that ledger automatically.

Separately captured `agy models` output can be reconciled offline by the bounded
semantic parser in `scripts/agy_inventory.py`. It requires exactly one occurrence of
each of the 11 reviewed canonical slugs on 11 nonblank lines and rejects unknown,
missing, duplicate, or ambiguous slug-shaped tokens, including unknown entries in
the reviewed Gemini, Claude, and GPT namespaces. Ordinary display labels remain
non-authoritative. The `gpt-oss` display alias is
accepted only on the same line as `gpt-oss-120b-medium`; it never becomes a twelfth
model. Parsing inventory does not bind an installed version, advance a baseline, or
activate the matrix.

The accepted `1.1.11` version binding is narrower still: it binds one version-only
snapshot/source/argv observation, not inventory, authentication, provider behavior,
an official source revision, or a metadata update. The canonical models runner
deliberately launches its one child with a fresh private empty `HOME`, `TMPDIR`, and
XDG directories plus a closed fixed environment. It never inherits or copies caller
credentials or Python startup state. If `agy models` needs a logged-in account, this
runner rejects and publishes no accepted completion marker; that expected rejection
cannot advance the active matrix.

The separate repository-only `scripts/version_bootstrap_runner.py` consumes one
strict retained accepted recovery record, makes descriptor-held source and snapshot
copies in a new owner-private root, and performs one bounded snapshot-backed
`--version` observation. Every root, directory, temporary file, and final artifact is
bound to its creation-time identity. Rollback reopens without following symlinks and
compare-deletes only that owned inode; replacement or shape drift is left as a bounded
private residual. Hard-link publication records both names as the exact shared
`nlink=2` inode, unlinks staging, and verifies the final `nlink=1` identity before a
durability hook or signal checkpoint. After process-group closure, the unchanged mode-`0700` cwd, HOME,
TMP, and XDG directories must be empty before evidence publication. The production
CLI owns its process. Non-throwing handlers accumulate HUP/INT/TERM; each safe
checkpoint selects by fixed priority HUP, then INT, then TERM, not chronological send
order, and freezes that selection. Large retained hashes and copies poll between
userspace chunks of at most 1 MiB, while capture and publication loops also poll;
this does not bound the duration of one kernel syscall. Before completion the runner
closes the group, rolls back on a selected signal, and returns `129`, `130`, or `143`.
Signals remain unblocked through the copies, provisional marker, recovery and ledger
validation, durability, and the complete bounded JSON write and flush. Only then does
the runner block signals, merge one final pending snapshot, and call `os._exit(0)`
without restoring handlers, unblocking, or releasing rollback descriptors in Python.
Consumers require both exit `0` and the marker.
A signal after that snapshot is post-completion. The explicit embedded test API is
the only restoring path; a signal absent from its final snapshot is caller-owned and
may be delivered while the entry mask is restored.
Production and the test harness require the selected CPython 3.9 at
`/usr/bin/python3 -I -S -B`. They reject an implementation, major/minor, or exact
isolated/no-site/no-bytecode/environment flag mismatch before production source
parsing, lifecycle acquisition, or filesystem mutation; the harness prints one
canonical rerun command instead of importing the runner under a different AST ABI.

The bootstrap result has its own `snapshot-version-bootstrap` claim and cannot be
used directly as capture evidence. Its generated nested recovery input retains the
unchanged `snapshot-version-only` claim and is accepted by the existing recovery
validator. The source guard pins the exact reachable production graph and one fixed
Popen site as reviewed-source drift detection. The reviewed source and interpreter,
local owner and same-UID processes, and OS administrators remain the TCB; this is not
coordinated hostile-source or same-UID tamper resistance.

The separate repository-only `scripts/version_initial_bootstrap_runner.py` starts a
new source chain without reading a historical recovery record. Its canonical profile
names only a fresh owner-private output root, current source path/full identity, and
fixed `1.1.12` / source-SHA expectations plus its own exact local `1.1.12` stdout
authority; it contains no account-HOME authority. It holds the source twice, makes
independent mode-`0755` source and mode-`0500` snapshot
copies, and performs exactly one bounded snapshot-backed `--version` observation.
It emits a structurally accepted `snapshot-version-only` prior/profile with the
durable false `recovery_runner_version_reconciled` limitation; the unchanged canonical
`1.1.11` recovery runner must not execute it until a separately reviewed
reconciliation.
It does not read historical recovery evidence, run models or login, access network or
Git, route, retry, or advance compatibility/model metadata. Identity or scratch drift rejects with at
most a bounded owner-private residual. Any real call remains separately authorized.

The adjacent version, models, capture, and process-inert profile commands now use the
same process-owning production handoff. They preserve inherited ignored handlers and
caller-blocked signals outside their owned set, latch owned HUP/INT/TERM without
raising, and choose accumulated signals by fixed HUP/INT/TERM priority rather than
claiming chronological delivery. Bounded reads, hashes, stream capture, publication,
and cleanup poll while rollback authority remains live. A marker or profile is
provisional until validation, durability, and the bounded success bytes have been
written and flushed. Production then takes one blocked completion snapshot and calls
`os._exit` without a Python return, handler restore, or unblock race. Embedded APIs
alone restore with an explicit caller handoff; the mutation harness returns only via
its explicit test handoff. Exact byte pins propagate in order from the version runner
to the models runner and then the capture runner; the harness independently pins the
version runner, while the profile builder remains process-inert and independently
AST-pinned. These offline controls add no provider, live-account, routing, retry, or
metadata authority.

A separate `scripts/models_capture_runner.py` defines the future capture mechanism
without invoking it. Its strict canonical stdin profile names one explicit owner-
`0700` account HOME and its held directory identity. Production use is valid only
after a user separately authorizes that exact account path and the one snapshot-
backed `agy models` call. The child receives that HOME plus capture-owned private
TMP/XDG/cwd directories and a fixed closed environment. The runner revalidates every
HOME path component without following symlinks before and after the child, but it
neither reads nor validates account contents. The external CLI may read, write, or
mutate that authorized HOME and create account caches according to its own behavior;
the runner cannot inventory, prevent, or revert those HOME changes, and residuals may
remain even when capture fails. The selected account HOME, local owner
and same-UID processes, reviewed interpreter and source, and OS administrators remain
its trusted computing base; it makes no same-user tamper-resistance claim.

The profile itself is prepared or revalidated only by the separate process-inert
`scripts/models_capture_profile.py` command with exact `--prepare` or `--validate`
and bounded JSON on stdin. It accepts no ambient paths, configuration, or account
discovery; it reopens the explicit account, source, retained external snapshot, and
version evidence with no-follow descriptors before atomically creating a fixed-name
mode-`0600` canonical file. That preparation never launches agy, a shell, Git, a
network client, or a provider call, and it neither authorizes nor performs capture.

The independently reconciled `1.1.12` bridge uses
`scripts/models_capture_1_1_12_profile.py` and
`scripts/models_capture_1_1_12_runner.py`, separate from the historical capture
surfaces. The builder emits only a canonical owner-private profile from a closed
explicit request; the runner performs one separately authorized `models` call with
logical source argv and snapshot executable, then emits bounded `captured` evidence.
Its external capture parent is an owner-private container: stable directory identity
is bound while its changing link count is diagnostic only; exact source, snapshot,
recovery, profile, and newly owned result-root nodes are revalidated separately.
Unrelated siblings are neither enumerated as authority nor deleted. The owned root's
path-through-held-parent identity and exact internal inventory are rechecked before
the child starts. The bridge cannot enumerate HOME, accept inventory, update
metadata, route, retry, or authorize a call.

The post-v0.8.0 `1.1.16` reconciliation used a new, independent chain rather than
relabelling either historical runner. `models_capture_1_1_16_version_evidence.py`
retains an exact private source/snapshot binding from one empty-HOME `--version`
observation. The process-inert `models_capture_1_1_16_profile.py` can prepare one
explicit account profile, and `models_capture_1_1_16_runner.py` can make exactly one
separately authorized, no-retry `source --output-format json models` capture with a
25-second wall and independent 64-KiB streams. Its reserved process group must close
before the sole leader reap; uncertainty fails closed. These files and their synthetic
tests did not authorize an account call, interpret inventory, or activate the 1.1.16
baseline/matrix. One separately authorized no-retry capture succeeded; its raw marker
became input to strict offline normalization and human reconciliation, never activation
authority by itself.

The `1.1.22` observation repeats that fixed-contract design in
`models_capture_1_1_22_version_evidence.py`,
`models_capture_1_1_22_profile.py`, and
`models_capture_1_1_22_runner.py`. The production files differ from the reviewed
1.1.16 design only in exact version/source/distribution/binding constants and their
self-pins plus fail-closed nonzero-stream preservation. The separately authorized
no-retry account capture launched one child, exited `1`, and produced no classifiable
inventory. It was not retried; 1.1.22 therefore did not advance the active baseline.

After process-group closure, every capture-owned TMP/XDG/cwd directory must again be
the same empty directory or publication fails closed. Successful bounded exit-zero
execution publishes otherwise uninterpreted private mode-`0600` stdout/stderr, exact
source and profile bytes, a `status: captured` record, and the final detached
`models.capture.sha256` marker under a new owner-private evidence root. A capture is
not an accepted inventory or binding and cannot update compatibility metadata,
select a model, route work, or prove a provider backend. Output interpretation,
including authentication, license, permission, quota, rate-limit, interactive, and
inventory semantics, belongs to later offline reconciliation. Capture itself rejects
nonzero, overflow, timeout, identity/scratch drift, or publication failure with no
final marker. A completed bounded nonzero child preserves mode-`0600` raw streams and
a sanitized `child-failed` record in the owner-private root before returning failure;
the record explicitly leaves failure classification false. Earlier-stage failures do
not manufacture stream evidence. There is no login helper, prompt, retry, fallback,
task dispatch, or provider job. On success the CLI prints only sanitized JSON
containing the private artifact
root, capture SHA-256, and `captured` status; it never prints raw streams or the
account HOME. All checked-in coverage uses synthetic account roots; this runner's
presence neither performs nor authorizes a real-account call. Source-contract
mutations are selected drift checks under the reviewed-source/local-owner TCB, not
proof against coordinated hostile source changes. Stronger assurance would require
a separately trusted launcher.

The retained historical `1.1.12` capture established eleven slugs. A later separately
authorized no-retry capture completed with exit `0`, one Popen, empty capture scratch,
an exact completion marker, and a strict 14-model JSON shape. Offline normalization
retained the earlier eleven slugs and added Gemini 3.7 Flash low/medium/high; `minimal`
is unsupported. An earlier failed attempt remains non-authoritative; it was not
retried or reconstructed. Its historical hashes and claim limits remain recorded in
[`compat/reviews/agy-1.1.12.md`](compat/reviews/agy-1.1.12.md). The separately
authorized 1.1.16 capture normalized to the same fourteen slugs; its exact bindings
and activation decision are recorded in
[`compat/reviews/agy-1.1.16.md`](compat/reviews/agy-1.1.16.md). The separately
authorized 1.1.22 capture failed before inventory interpretation and is recorded as a
non-activating observation in
[`compat/reviews/agy-1.1.22.md`](compat/reviews/agy-1.1.22.md).

The human-reviewed active agy baseline remains `1.1.16` at source revision
`efa16f096dc02fb654b7e86958d268195284d014`. The checked-in 1.1.22 distribution tuple
is an observational drift detector rather than a trust root: a same-version
archive build, URL, or SHA-512 change requires review and cannot itself activate or
advance compatibility metadata.

The G1 direct-selection surface consumes the checked-in active model/effort matrix as
validated compatibility metadata, never as routing or gate authority. It maps eleven
explicit base/effort pairs to exact advertised compound slugs and records three exact fixed
choices; Pro medium is unsupported. The matrix resolves only while its agy version
and reviewed source revision match the canonical records and its exact bytes match
the checked-in SHA-256. The wrapper resolves one exact model slug and never sends
agy's separate effort flag. `qa-gate.sh` remains the sole
acceptance authority, and model recommendations remain visible, advisory-only, and
unable to escalate permission, authentication, scope-policy, or human-required
outcomes. The bounded jobs proved exact argv and candidate verification, not effective
provider backend identity, quality, or billing.

## Sanitized bug reports and improvement requests

Codex can create a local draft, but nothing is submitted automatically:

```bash
./bug-report.sh draft --output /tmp/agy-worker-bug.md \
  --title "QA gate rejects an accurate created-file claim" \
  --component qa-gate \
  --summary "A synthetic fixture is rejected." \
  --steps "Create a fresh fixture and run the offline gate case." \
  --expected "The accurate claim is accepted." \
  --actual "The gate exits 10."

./bug-report.sh preview /tmp/agy-worker-bug.md
```

For a bounded improvement request, choose that kind explicitly:

```bash
./bug-report.sh draft --kind improvement --output /tmp/agy-worker-improvement.md \
  --title "Show a clearer routed outcome" --component reporting \
  --summary "The current result needs manual interpretation." \
  --problem "Maintainers cannot quickly see why a job was routed." \
  --proposal "Add a bounded reason label to the local report." \
  --benefit "Faster review without exposing job data."
```

The generator reads no prompts, source files, envelopes, or logs. It conservatively
redacts credential-bearing lines, GitHub/Bearer/Basic tokens, complete private-key
blocks, absolute POSIX/Windows/UNC paths, current worker artifact names, and closed or
unclosed fenced/indented code. Safe relative synthetic paths remain usable. Drafts are
published atomically with mode `0600`, then a SHA-256 review token is printed.
Drafting and submitting are two separate user decisions. A public bug or improvement
submission requires the exact reviewed hash, a second explicit confirmation that the
same digest is public-safe, and an existing authenticated GitHub
CLI; `gh` is optional and not a runtime dependency:

```bash
./bug-report.sh submit /tmp/agy-worker-bug.md --confirm-sha <SHA256-FROM-PREVIEW> \
  --confirm-public-safe-sha <SAME-SHA256-FROM-PREVIEW>
```

Immediately before invoking `gh issue create`, submission validates and prints the
exact body again. The validated in-memory bytes are sent over stdin with
`--body-file -`, so a later file change cannot alter the confirmed body. The target is
explicitly `github.com/cagdasyurekli/codex-agy-worker`; an inherited `GH_HOST` cannot
redirect it. A changed draft invalidates the hash. Without `gh`, or when `gh` fails,
the local draft remains and nothing else is attempted.

Use `--kind security` for a minimal private-only security report. Security drafts are
deliberately ineligible for public submission. The conservative keyword check is an
extra barrier, never proof that a report is safe to publish. Do not add details to
make them public; use the [private vulnerability reporting
form](https://github.com/cagdasyurekli/codex-agy-worker/security/advisories/new).

The repository also includes GitHub Issue Forms for
[sanitized bugs](.github/ISSUE_TEMPLATE/bug_report.yml) and
[feature requests](.github/ISSUE_TEMPLATE/feature_request.yml). Feature requests ask
for the problem, concrete use case, acceptance criteria, alternatives, minimal scope,
security/privacy impact, and an explicit privacy acknowledgement. Blank issues are
disabled so maintainers can review proposals consistently; submission does not imply
acceptance or roadmap commitment.

Maintainers may run `./feedback-triage.sh fetch` deliberately, or inspect the weekly
read-only workflow summary. It requests at most one metadata-only page of open issues
from this repository and emits only canonical URLs/numbers, month counts, and
burst/overflow flags. Because fetch never reads titles or bodies, its type counts are
all `other` and it produces no duplicate groups; those fields are meaningful only for
an explicitly supplied, already-safe `summarize` input. It never fetches or emits
titles, bodies, comments, labels, usernames, or raw issue content, and it never writes
to GitHub or feeds issue text to an agent.

---

## agy behaviour worth knowing

Most facts below were measured on macOS with agy 1.1.9 on 2026-08-01. The active
1.1.16 model reconciliation, blocked 1.1.22 observation, and historical 1.1.12 record
are separate. Run
`./ground-truth.sh` against
your own install rather than treating historical observations as a current contract.
Its default interface phase calls only `agy --version` and `agy --help`; use
`./ground-truth.sh --account` only when you explicitly authorize inspection of
account-owned agy state such as models, agents, plugins, and local permissions.

- **`--print` must be built last.** The prompt is its argument value; agy ignores stdin
  in print mode and will read the next flag as the message. `agy --print --sandbox "x"`
  sends the literal string `--sandbox` as the prompt.
- **Exit 0 plus empty output does not mean success.** agy 1.1.18 fixed one dropped
  state-stream case and 1.1.20 stopped treating benign tool/permission errors as fatal
  print-mode failures. The worker still rejects any exit-zero empty stream and accepts
  a terminal result only through its bounded structured envelope.
- **`--agent` silently disables `--json-schema`.** `result.structured_output` comes back
  null and the worker answers in prose. agy also accepts *any* `--agent` name without
  error, so a typo yields a default worker that believes it is a specialist.
- **Auth is intermittent.** A run can fail into an interactive OAuth prompt and the
  identical next run can succeed. Classify only reviewed exact failure signatures;
  never turn that observation into an automatic retry.
- **`stream-json` shape:** `init` → repeated `step_update` → one `result`. The answer is
  at `result.structured_output`. `result.json_schema` is the *echoed schema* — a naive
  key-matching parser grabs that and hands you back your own schema.
- **~25k input tokens floor per invocation**, regardless of task size. Batch work; many
  tiny jobs are disproportionately expensive.
- **Unknown subcommands exit 0** and print usage. `agy run`, `agy exec`, `agy auth` do
  not exist. Never probe for a subcommand by exit code.
- **Headless permission gating:** `~/.gemini/antigravity-cli/settings.json` →
  `permissions.allow`, entries as `command(<name>)`. Interactive agy prompts for an
  unlisted command; `agy -p` cannot, so it hard-fails. Under `--sandbox`, shell commands
  additionally need `unsandboxed(<target>)`. Prefer narrow allow-rules over
  `--dangerously-skip-permissions`, which approves every tool for the whole run.
- **Agent files need YAML frontmatter** and live flat at
  `~/.gemini/config/agents/<name>.md`. A workspace `.agents/agents/<name>/agent.md` is
  not discovered.

## Reproducible offline benchmarks

[`benchmark.sh`](docs/BENCHMARKING.md) preregisters ordered caller-selected variants
against the frozen public synthetic manifest, then runs exactly one offline candidate
attempt per variant-task pair through `verify-job.sh`. It makes no agy, provider, or
network call. In a complete checkout the immutable plan binds the clean source
commit. In a folder-only bundle it instead binds the reviewed portable source
revision and exact no-extra source manifest; it never invents a Git commit. Both
layouts bind the exact runner, schemas, manifest, fixtures, gate, wrapper,
selections, and policy. The separate unsigned
result binds raw Receipt v1 hashes and validated gate facts.

The report is a pure completeness view in manifest order. It does not score, rank,
choose a winner, route, recommend, retry, fall back, or turn `gate-passed` into human
acceptance. Results live only in an explicit canonical owner-`0700` directory outside
the checkout. Live/provider benchmarking is not implemented and requires a separate
reviewed slice, accepted agy executable/version evidence, and explicit authorization.

### A note on where these came from

This project began as multi-model research into agy's own interface. The models that
ran *on agy* confidently invented `agy run`, `--headless`, `--slim`, and an
`agy auth status --json` OAuth introspection endpoint. None exist. The models that
shelled out to `agy --help` got it right.

That asymmetry is the reason this tool verifies instead of trusting, and the reason
`ground-truth.sh` exists: if you have an agent author agy skills, feed it live CLI
output, not its own recollection.

---

## Layout

```
agy-worker.sh                 dispatch a job, return a schema-valid envelope
job.sh                        manage one explicit branch-backed local job lifecycle
qa-gate.sh                    verify an envelope against the repo — the evidence
verify-job.sh                 run the gate and durably publish Evidence Receipt v1
evidence-report.sh            render a validated receipt as bounded text or Markdown
benchmark.sh                  prepare/run/report fixed provider-independent benchmarks
persona-evidence.sh           validate/report fixed persona evidence records
profile.sh                    list/show fixed non-executable workload profiles
proof-demo.sh                 offline starter proof of one gate pass and one rejection
conformance/run.sh            public v1 synthetic qa-gate fixture runner
conformance/v1/               pinned manifest, envelopes, and repository contents
model-recommendation.sh       repository compatibility wrapper for the advisory
model-selection.sh            repository compatibility wrapper for explicit resolution
doctor.sh                     repository wrapper for offline read-only diagnostics
ground-truth.sh               safe live agy version/help facts; --account explicitly adds account-state inventory
update.sh                     explicit release + agy/Codex compatibility check/apply
bug-report.sh                 sanitized local draft/preview/optional submission
feedback-triage.sh            bounded metadata-only feedback aggregate
compat/                       per-tool baselines, reviewed evidence, and active exact matrix
scripts/compatibility.py      stdlib metadata/matrix validation and exact resolution
scripts/compatibility_probe.py bounded process-group supervisor for fixed evidence/version probes
scripts/version_attestation_runner.py fixed-profile snapshot version runner with bounded startup diagnostics; real use separately authorized
scripts/version_bootstrap_runner.py repository-only retained-recovery bootstrap; never a recovery mode or live-account action
scripts/version_initial_bootstrap_runner.py repository-only current-source initial bridge; never reads HOME or historical recovery evidence
scripts/version_recovery_1_1_12_runner.py fixed 1.1.12 recovery from the exact phase-one prior; generated output is non-authorizing
scripts/version_attestation_harness.py persistent fake-child publication/process/signal mutation harness
scripts/models_attestation_runner.py auth-isolated snapshot models runner; not a live-account capture path
scripts/models_capture_runner.py explicit-account capture-only models runner; never auto-invoked
scripts/models_capture_profile.py process-inert canonical profile builder; never reads ambient account state
scripts/models_capture_1_1_12_profile.py fixed 1.1.12 process-inert capture profile bridge
scripts/models_capture_1_1_12_runner.py fixed 1.1.12 explicit-account capture-only bridge
scripts/models_capture_1_1_16_version_evidence.py fixed 1.1.16 source/snapshot version-only evidence bridge
scripts/models_capture_1_1_16_profile.py fixed 1.1.16 process-inert capture profile bridge
scripts/models_capture_1_1_16_runner.py fixed 1.1.16 explicit-account capture-only bridge
scripts/models_capture_1_1_22_version_evidence.py fixed 1.1.22 source/snapshot version-only evidence bridge
scripts/models_capture_1_1_22_profile.py fixed 1.1.22 process-inert capture profile bridge
scripts/models_capture_1_1_22_runner.py fixed 1.1.22 explicit-account capture-only bridge
scripts/ci-diff-check.sh      committed-range and changed-head-blob hygiene gate
scripts/ci_diff_check.py      bounded attribute-independent committed-blob scanner
scripts/agy_inventory.py      bounded exact-line semantic parser for private inventory evidence
scripts/official_github.py    fixed, proxyless, redirect-free GitHub REST evidence client
scripts/official_distribution.py  fixed, bounded agy distribution-manifest canary
scripts/bug-report.py         privacy filter and SHA-bound gh submission
scripts/feedback-triage.py    bounded metadata-only feedback parser/fetcher
skills/agy-worker/            canonical self-contained Agent Skill and runtime
skills/agy-worker/runtime/    dispatcher, gate, advisory, personas, schema, Python helpers
benchmarks/v1/                frozen public synthetic benchmark manifest and inputs
compat/personas/              fixed public persona evidence records and manifest
skills/agy-worker/runtime/compat/  byte-synced portable agy metadata and selection matrix
.codex-plugin/plugin.json     OpenAI skills-only plugin package metadata
docs/REPO_MAP.md              hand-maintained ownership, data flow, and trust map
docs/lessons_learned.md       durable architectural mistakes and prevention rules
docs/index.md                 GitHub Pages landing source
CONTRIBUTING.md               contributor workflow and evidence expectations
SECURITY.md                   private vulnerability reporting policy
CODE_OF_CONDUCT.md            enforceable participation standards
.github/pull_request_template.md  review and verification checklist
.github/workflows/feedback-watch.yml weekly/manual read-only feedback aggregate
tests/test-qa-gate.sh         offline adversarial suite
tests/test-evidence-receipt.sh  88-case offline receipt/publication/protocol suite
tests/test-evidence-report.sh  80-case offline pure renderer/privacy/CI-format/mutation suite
tests/test-benchmark.py       104-case offline plan/receipt/result/report suite
tests/test-persona-evidence.py 124-case offline semantic-chain/ancestry/portable/mutation suite
tests/test-workload-profiles.py 89-case offline data-only profile authority suite
tests/test-job-lifecycle.py   116-case offline state/receipt/Git-policy/cleanup/abort/signal suite
tests/test-agy-worker.sh      334-case offline dispatcher/installer/routing/lifecycle suite
tests/test-agy-worker-remediation.py 89-case offline controller-boundary remediation suite
tests/test-update.sh          325-case offline transport/process/inventory/local-remote/matrix/manifest updater suite
tests/test-agy-inventory.py   test-only exact-slug/display-alias adversary harness
tests/test-official-github.py 65-case fixed-endpoint transport adversary harness
tests/test-compatibility-probe.py test-only timeout/output/signal/version adversary harness
tests/test-version-attestation-runner.py  165-case offline canonical fixed-profile runner suite
tests/test-version-bootstrap-runner.py  139-case offline retained-recovery bootstrap suite
tests/test-version-initial-bootstrap-runner.py  43-case offline current-source initial bootstrap suite
tests/test-version-recovery-1-1-12-runner.py  75-case offline fixed 1.1.12 recovery suite
tests/test-version-attestation-harness.py  60-case offline version-attestation mutation suite
tests/test-models-attestation-runner.py  116-case offline fixed-profile inventory attestation suite
tests/test-models-capture-runner.py  84-case offline fake-account capture-only suite
tests/test-models-capture-profile.py 121-case offline canonical capture-profile builder suite
tests/test-models-capture-1-1-12-profile.py 30-case offline fixed 1.1.12 capture-profile suite
tests/test-models-capture-1-1-12-runner.py 56-case offline fixed 1.1.12 capture-runner suite (86 combined with profile)
tests/test-models-capture-1-1-16-version-evidence.py 45-case offline fixed 1.1.16 version-evidence suite
tests/test-models-capture-1-1-16-profile.py 30-case offline fixed 1.1.16 capture-profile suite
tests/test-models-capture-1-1-16-runner.py 58-case offline fixed 1.1.16 capture-runner suite (88 combined with profile)
tests/test-models-capture-1-1-22-version-evidence.py 45-case offline fixed 1.1.22 version-evidence suite
tests/test-models-capture-1-1-22-profile.py 30-case offline fixed 1.1.22 capture-profile suite
tests/test-models-capture-1-1-22-runner.py 58-case offline fixed 1.1.22 capture-runner suite (88 combined with profile)
tests/test-agy-1-1-16-activation.py 22-case offline active-baseline/inventory-binding suite
tests/test-adoption-measurement.py 41-case offline privacy-limited 30/60/90 measurement suite
tests/test-update-notifier.py 89-case offline local notifier lifecycle/signal/maintenance suite
tests/test-official-distribution.py  test-only stdlib manifest adversary harness
tests/test-reporting.sh       offline privacy/fake-gh reporting suite
tests/test-feedback-triage.py 26-case offline metadata-only triage suite
tests/test-packaging.sh       390-case offline Codex package/CI-policy/relocation/landing suite
tests/test-doctor.sh          257-case offline fake-tool/read-only doctor suite
tests/test-proof-demo.sh      21-case offline starter-proof adversarial suite
tests/test-conformance.py     81-case offline public gate-contract/adversary suite
.github/workflows/compatibility-watch.yml  observational daily/manual fixed-source watch
update-notifier.sh              optional local daily notifier lifecycle wrapper
```

Contributors should start with [the repository map](docs/REPO_MAP.md) and use
[the architectural lessons](docs/lessons_learned.md) for the rationale behind the
trust boundaries. Keep one-off run history and release notes out of `AGENTS.md`.

## Limitations

- No persistent daemon, MCP server, or shared polling service. `start` is a narrow
  explicit per-job local controller with owner-private state and bounded `wait`; it
  does not expose provider job status or remote cancellation truth.
- Native Windows is not tested or guaranteed. There is no Windows-specific denylist,
  but the maintained entrypoints require a POSIX-compatible environment; WSL may work
  on a best-effort basis. Fixed-POSIX-path evidence runners and the macOS-only
  LaunchAgent notifier are outside that claim.
- Single worker backend: agy; no alternative worker backend is implemented.
- Partial/promisor Git clones are unsupported. `start` rejects them synchronously
  with a sanitized diagnostic before it writes queued lifecycle state or launches a
  provider; use a full clone for the disposable worker worktree.
- One audited worktree per job. User-supplied `--add-dir` roots outside `--workdir`
  are rejected; multi-repository mutation is intentionally unsupported.
- `bulk-test-writer` has been exercised but has not yet produced an accepted real job;
  `diff-reviewer` remains untested against a real job.
- Persona registry status is currently `offline-only` for every shipped persona.
  Historical exercises are not promoted without public Receipt/base/verifier/tool and
  maintainer-approval bindings.
- The public v0.3.0 updater exercise failed closed when full commit/release evidence
  exceeded its original bound. Version 0.3.1 uses compact exact tag-ref evidence and
  a separate bounded release-document ceiling; successful public apply evidence is
  established only by post-release verification, not by offline tests alone.
- GitHub submission is tested with a fake `gh`; no live issue was created.
- Headless skill expansion (`agy -p "/skill-name ..."`) is untested.
- Direct model/effort selection has exhaustive offline fake-agy coverage but no new
  post-G0 live provider run. It proves argv, conflict, version/matrix, packaging, and
  frozen-selection continuation behavior—not backend identity, quality, relative performance, cost,
  quota efficiency, or dual-selector composition inside agy.
- A green doctor result covers offline prerequisites only. It does not predict live
  authentication, provider, sandbox, task, or dispatch behavior and never fixes them.
- Version attestation uses the fixed `/usr/bin/python3 -I -S -B` launch and exact
  reviewed Apple interpreter families. It trusts the selected interpreter, hosted
  image, local account owner, and OS administrators; it checks canonical components,
  alias/target identity, executable/no-setid mode, and rejects world-writable
  directories or resolved executables.
  UID/GID and owner/group writability remain bounded diagnostics, not binary
  provenance. This is not same-user or hostile-PR tamper resistance, code-signing
  verification, or OS attestation.

## License

MIT — see [LICENSE](LICENSE).
