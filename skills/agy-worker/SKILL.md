---
name: agy-worker
description: Delegate bulk, mechanical coding work to Google Antigravity CLI (agy) as a bounded worker, then independently verify Git scope and driver-owned acceptance checks before accepting it. Use for test backfills, repeated cross-file edits, mechanical refactors, repository inventories, or a verification-gated agy delegation. Do not use for architecture or judgment-heavy work, or when acceptance criteria cannot be stated before dispatch.
---

# Delegate to agy, then verify

You are the driver. `agy` is a worker whose self-report is a **claim, never evidence**.
You decide what "done" means, and you prove it yourself.

Resolve the pipeline from this skill's installed location. Set `SKILL_ROOT` to the
directory containing this `SKILL.md`, then run:

```bash
PIPELINE="$(bash "$SKILL_ROOT/scripts/resolve-pipeline.sh")" || exit $?
```

Do not guess a checkout path. The resolver supports a complete plugin checkout, the
explicit standalone install created by `install.sh`, and a portable skill-folder copy
using its bundled `runtime/`. It never downloads a missing runtime; an incomplete
bundle fails closed.

## Check readiness without dispatching

Before spending provider quota on a repository, run the resolved pipeline's offline
doctor:

```bash
"$PIPELINE/doctor.sh" --repo /absolute/path/to/target
# Machine-readable output, with the same outcome:
"$PIPELINE/doctor.sh" --repo /absolute/path/to/target --format json
```

Exit `0` is `ready`; exit `3` is either `review-required` (semantic agy-version drift
or a due compatibility review) or `not-ready` (a failed prerequisite, repository,
bundle, semantic probe, or metadata record); exit `64` is invalid usage. Inspect the
reported `overall` value when the exit is `3`.

The doctor is read-only and offline. It checks the resolved bundle, Bash 3.2,
Python 3, git/worktree support, the target Git worktree, exact `agy --version`
semantics, and portable reviewed metadata. It does not scan personal configuration,
repair anything, call an auth or unknown subcommand, invoke a provider, dispatch a
job, run the updater, or access the network. Green means only that offline
prerequisites passed; it says nothing about authentication, provider availability,
sandbox permission, task quality, or the next live dispatch. A folder-only copy runs
the same bundled doctor and metadata without a checkout or fetch. Caller temp paths
are ignored; HUP, INT, or TERM stops the active probe and descendants, removes the
private workspace, prints only `doctor: interrupted`, and exits `3` without a report.
The bundle's `scripts`, `agents`, `schemas`, and `compat` parents must be contained
real directories; parent-directory symlinks fail closed even when they point inward.

To inspect the fixed shipped-persona evidence levels without dispatching anything:

```bash
"$PIPELINE/persona-evidence.sh" validate
"$PIPELINE/persona-evidence.sh" report
```

All shipped records are currently `offline-only`. The registry binds public contract
bytes and mode restrictions; it does not execute, trust, rank, route, accept, or
promote a persona, and target repositories cannot register one dynamically.

To inspect one fixed non-executable workload skeleton without dispatching or reading
a target repository:

```bash
"$PIPELINE/profile.sh" list
"$PIPELINE/profile.sh" show bounded-test-backfill
```

A profile may suggest only one maintained mode, persona, and closed repo-relative
path-policy shape. It supplies no repository, path, tier/model/effort value, verifier
or shell command, external root, authorization, route, acceptance, dispatch, or Git
action. The caller must still provide approval, exact repository, exact path policy,
selected tier, and verification commands. Never load profiles from a target repo,
environment variable, home directory, or caller path.

## Data boundary and sandbox requirement (read first)

`agy` is an external CLI backed by Google/Gemini services. A dispatch can transmit
the task text and repository content that the worker reads from driver-approved
roots. Before the first dispatch for a repository, tell the user what repository and
paths will be in scope and obtain explicit approval unless that exact transmission
was already approved. Never include credentials, private keys, or unrelated files.
The pipeline stores job prompts, streams, stderr, and envelopes in local `logs/`;
treat them as private artifacts. See the
[public privacy disclosure](https://github.com/cagdasyurekli/codex-agy-worker/blob/main/PRIVACY.md)
for the complete project disclosure.

The following sandbox settings apply to Codex. In another Agent Skills client,
follow that client's own permission and network controls; do not copy Codex-specific
configuration into it.

agy starts a local language server and writes state under `~/.gemini`. Under Codex's
default `workspace-write` sandbox it fails with **exit 5 and empty stderr** — no useful
error. Verified working invocation:

```bash
codex exec --sandbox workspace-write \
    --add-dir ~/.gemini \
    -c 'sandbox_workspace_write.network_access=true' ...
```

Both parts are required: `--add-dir ~/.gemini` alone still fails (exit 5), because the
blocker is the socket bind, not the file write. To make it permanent, put the
equivalent in `~/.codex/config.toml` instead of passing flags each time.

If you are already running and hit exit 5, the alternative is to let Codex escalate the
single command via its approval prompt — that also works. Do **not** reach for
`--dangerously-bypass-approvals-and-sandbox`.

## When this is worth it

Delegate when ALL of these hold:
- The work is mechanical enough that you can write exact acceptance criteria now.
- You can name the files in scope and a command that proves success.
- The volume is high enough to justify tens or sometimes hundreds of thousands of
  worker tokens per job.

Otherwise just do it yourself. A single-file edit is cheaper direct than delegated.

## Procedure

### 1. Isolate

Never let the worker touch the user's working tree. Use a branch-backed worktree so
accepted changes can be committed before cleanup. Prefer the lifecycle command: it
binds the exact repository, worktree, branch, immutable base, and job ID in a private
external state file and performs no dispatch or external action. Invoke `job.sh` as a
command/subprocess; its Python `main()` is process-owning for signal-safe termination
and is not an embedding API.
It accepts only an exact canonical branch name. Every lifecycle-owned Git command
uses fixed sanitized Git execution with a private empty hooks directory; `init`
rejects configured hooks/helpers/filters and effective base-tree or info-attribute
filters before creating state, a ref, or a worktree. Fatal ref evidence is never
treated as absence.

```bash
PIPELINE="$(bash "$SKILL_ROOT/scripts/resolve-pipeline.sh")" || exit $?
TARGET=/absolute/path/to/target-repo
BASE="$(git -C "$TARGET" rev-parse HEAD)"
umask 077
STATE_DIR="$(mktemp -d -t agyworker-job-state.XXXXXX)"
WT="$(mktemp -d -t agyworker-job-worktree.XXXXXX)"
rmdir "$WT"
JOB_BRANCH=agy/job-12345
JOB_ID=job-12345
"$PIPELINE/job.sh" init --state "$STATE_DIR/job.json" \
    --repo "$TARGET" --worktree "$WT" --branch "$JOB_BRANCH" \
    --base "$BASE" --job-id "$JOB_ID"
```

The manual reference remains `git -C "$TARGET" worktree add -b "$JOB_BRANCH"
"$WT" "$BASE"`. Do not mix manual mutations into a lifecycle-managed state.

### 2. Write acceptance criteria BEFORE dispatching

You must be able to fill in both blanks:
- Files in scope: ...
- Command that proves success: ...

If you cannot, the task is not ready to delegate. Scope it further or do it yourself.

### 3. Show the pre-dispatch recommendation, then dispatch unchanged

Record the caller-selected tier and classify the task using driver-owned facts. Print
the advisory before dispatch; do not assign `TIER` from its output or otherwise apply
the recommendation automatically.

```bash
TIER=bulk  # caller-selected; keep this value unless the caller explicitly changes it
"$PIPELINE/model-recommendation.sh" --stage pre-dispatch \
    --selected-tier "$TIER" --evidence batched-mechanical
```

The other valid pre-dispatch evidence codes are `bounded-routine`,
`cross-file-bounded`, and `high-complexity-bounded`. Choose one from the task,
scope, and acceptance criteria you established—not from worker prose. Every result is
JSON with `recommendation_only: true` and `applied: false`.

Then dispatch using exactly `TIER`:

```bash
echo "<task>" | "$PIPELINE/agy-worker.sh" --mode accept-edits --tier "$TIER" \
    --persona bulk-test-writer \
    --workdir "$WT" --add-dir "$WT" > /tmp/envelope.json
```

For a caller-selected reviewed direct model, keep the user model and effort separate
in the advisory and dispatch them unchanged:

```bash
MODEL=gemini-3.6-flash
EFFORT=high
"$PIPELINE/model-recommendation.sh" --stage pre-dispatch \
    --selected-model "$MODEL" --selected-effort "$EFFORT" \
    --evidence batched-mechanical
echo "<task>" | "$PIPELINE/agy-worker.sh" --mode accept-edits \
    --model "$MODEL" --effort "$EFFORT" --workdir "$WT" --add-dir "$WT" \
    > /tmp/envelope.json
```

Personas: `bulk-test-writer` (tests only), `diff-reviewer` (review, no edits),
`repo-inventory` (read-only survey). Omit `--persona` for a plain worker.
The dispatcher rejects `accept-edits` for the read-only personas. Tiers may be passed
as `--tier cheap|bulk|hard|hardest|default` or through `AGY_WORKER_TIER`.
Direct selection uses `--model`/`AGY_WORKER_MODEL` and optional
`--effort`/`AGY_WORKER_EFFORT`. Each component has one source: CLI and its matching
environment variable conflict even when equal, repeated or empty components fail,
and any explicit tier conflicts with every model/effort source. Model and effort may
use different sources. Do not normalize or guess names.

Exact reviewed compound/fixed slugs are model-only. Adjustable Flash 3.6/3.5 bases
accept low/medium/high; Pro 3.1 accepts low/high and rejects medium. Fixed Sonnet,
Opus thinking-labelled, GPT medium-labelled, and compound slugs reject effort. Custom
labels remain available only through legacy `--tier CUSTOM`. Direct resolution needs
the active exact-SHA/version/source-bound portable matrix and exact installed agy
`1.1.10`; exit 7 needs human compatibility review and exit 8 means evidence is
unavailable. The dispatcher sends one downstream `--model`, never downstream
`--effort` or a thinking flag.
HUP, INT, or TERM during the direct-selection version preflight closes the exact
probe process group and returns `129`, `130`, or `143` before task read or selection
publication.

Every job has an owner-private `selection.json`. Direct selection records input
sources, exact resolved slug, installed version, matrix version/source, and matrix SHA
before attempt one; retries cannot re-resolve it. This is provenance, not gate
evidence or acceptance. Tier selection remains explicit: retries reuse the same
model, and this skill never infers a thinking level or silently escalates after a gate
failure. Direct advisories are unranked, recommendation-only, and never applied.
Every user-supplied `--add-dir` must resolve inside the audited `--workdir`; do not
delegate multi-repository mutation in one job.

**Two rules your task text MUST honour** — both are measured behaviour, not caution:

1. **Tell the worker not to run shell commands.** Under agy's sandbox, its shell tools
   execute in `~/.gemini/antigravity-cli/scratch`, NOT the repo. A worker that runs
   `ls` will truthfully describe an empty directory. Its *file* tools do reach the
   real target. You run every shell command.
2. **Use absolute paths and pass `--add-dir`.** agy has no reliable notion of "the
   current directory" in print mode. Name the absolute path in the task text AND pass
   `--add-dir "$WT"`. With only one of the two, the worker surveys the wrong place.

Good task text:

```
Add tests for /tmp/agy-job-123/src/parser.py covering the error paths.
Write only to /tmp/agy-job-123/tests/. Do not modify production source.
Use your file tools on those absolute paths. Do NOT run shell commands —
they execute in a scratch directory, not this repo, and will mislead you.
The driver runs every command; report commands_run and tests_run as empty arrays.
```

If the dispatcher exits nonzero, stop. Its stdout is not an envelope. Inspect the
job-scoped stderr after its built-in bounded attempts; never feed failed stdout into
the gate or wrap dispatch in an unbounded retry loop.

### 4. Read the worker exit code

| Exit | Meaning | Do |
|---|---|---|
| 0 | Envelope produced | Continue to step 5 — you have NOT verified anything yet |
| 2 | No/empty prompt | Your bug |
| 3 | agy returned empty stdout | Check `logs/<job>/stderr.txt`; usually a permission gate |
| 4 | No schema-valid envelope | Worker answered in prose; retighten the task |
| 5 | agy failed | Read stderr; often transient auth — retry once |
| 6 | Permission gate | Read stderr for the exact rule. **Do not** suggest `--dangerously-skip-permissions` |

### 5. Verify — this is the step that matters

```bash
RECEIPT_DIR="$(mktemp -d -t agyworker-receipts.XXXXXX)" || exit 1
RECEIPT_DIR="$(CDPATH= cd -- "$RECEIPT_DIR" && pwd -P)" || exit 1
"$PIPELINE/verify-job.sh" --receipt "$RECEIPT_DIR/job.json" \
    --envelope /tmp/envelope.json --repo "$WT" --base "$BASE" \
    --only 'tests/**' --expect-edits \
    --verify "git -C '$WT' diff --check" \
    --verify "cd '$WT' && <the command from step 2>"
```

**Always pass `--verify` for acceptance.** The gate never executes any command from
the worker envelope. Use `--only` whenever the task has a path policy, especially
for `bulk-test-writer`; its persona prompt is not enforcement. `--base` must be the
full commit ID captured before dispatch, never `HEAD` or another mutable ref.

`verify-job.sh` is the receipt-producing wrapper around the same canonical gate. Its
required `--receipt` must be a new canonical absolute path outside the audited
repository under an owner-private real parent. It hashes ordered policy and verifier
commands, publishes mode `0600` without overwrite only after file and parent `fsync`,
and never stores raw commands, output, source, paths, prompts, logs, or worker prose.
The lower-level `qa-gate.sh` remains available when no receipt is wanted. Its evidence
descriptor/capability is an internal `verify-job.sh` handoff, not a supported direct
interface; direct no-receipt behavior is unchanged.

One validated dispatcher `selection.json` may be supplied as `--selection FILE`.
One canonical advisory captured before dispatch may independently be supplied as
`--pre-recommendation FILE`. Neither is required; neither changes the gate result or
selected model. Never bind a post-gate advisory or an advisory claiming `applied`.
There is no implicit artifact discovery.

| Exit | Meaning | Do |
|---|---|---|
| 0 | Gate passed and receipt was durably published | Review the diff; no merge or commit happened automatically |
| 10 | Scope mismatch, invalid path, or `--only` violation | Reject |
| 11 | Worker reported a command or test | Reject; it was not executed |
| 12 | Envelope failed the checked-in schema | Reject |
| 13 | `--expect-edits` job changed nothing | Reject |
| 14 | Verification failed or mutated the worktree | Reject |
| 15 | Partial/failed/blocked/human-required outcome | Escalate; never accept |
| 64 | Bad invocation, invalid Git base, or missing `--verify` | Fix the driver command |
| 70 | Gate evidence missing, malformed, mismatched, unknown, or interrupted | Treat as internal failure; no receipt was published |
| 74 | Receipt validation or durable publication failed | Treat as publication failure; no receipt was published |

Gate exits `10`–`15` also publish the exact rejected/routed receipt before the wrapper
returns that exit. A receipt uses only `gate-passed`, `rejected`, or `routed`; it never
calls a candidate accepted. It is explicitly unsigned and not tamper-evident. Even
exit 0 still requires human diff review.

For a lifecycle-managed job, use the exact same verification inputs through its
wrapper instead of calling `verify-job.sh` directly:

```bash
"$PIPELINE/job.sh" verify --state "$STATE_DIR/job.json" \
    --receipt "$STATE_DIR/receipt.json" --envelope /tmp/envelope.json \
    --only 'tests/**' --expect-edits \
    --verify "git -C '$WT' diff --check" \
    --verify "cd '$WT' && <the command from step 2>"
```

To read or share the bounded receipt observations without pasting private job
artifacts, render the validated receipt locally:

```bash
"$PIPELINE/evidence-report.sh" --receipt "$RECEIPT_DIR/job.json" --format text
"$PIPELINE/evidence-report.sh" --receipt "$RECEIPT_DIR/job.json" \
    --format markdown --output "$RECEIPT_DIR/job.md"
"$PIPELINE/evidence-report.sh" --receipt "$RECEIPT_DIR/job.json" --format json
```

Standard output is the default; an explicit output path must be a new canonical
absolute path and is published mode `0600` without overwrite. The renderer invokes
neither agy, git, the gate, routing, nor the network. It reports only the validated
verdict, gate outcome/exit, hashes, verifier labels, binding presence, and fixed
unsigned/human-review limits. It does not make `gate-passed` accepted, and malformed,
inconsistent, injection-shaped, or separately mismatched evidence produces no report.
`--format github-step-summary` emits CI-safe Markdown only to stdout or the same
explicit private `--output` path. The renderer never reads `GITHUB_STEP_SUMMARY`;
workflow code must redirect stdout explicitly, and fork-controlled jobs must receive
no secrets or private receipt paths.

For provider-independent regression comparisons only, `benchmark.sh prepare|run|report`
uses the checked-in synthetic manifest and one attempt per ordered caller variant. It
calls no agy/provider/network path and cannot rank, route, recommend, retry, or change
a selector. Use a canonical owner-`0700` directory outside the checkout and follow
`docs/BENCHMARKING.md` in the repository. A complete checkout binds its clean commit;
a folder-only copy instead binds the reviewed portable source revision and exact
source manifest without inventing Git provenance. Nested schemas constrain the public
v1 structures; runtime checks retain cross-field and canonical-byte authority. Live
benchmarking is not implemented; do not infer authorization for it from an offline
plan.

The evidence descriptor belongs only to the gate parent. The wrapper strips executable
shell/Python startup controls from the evidence-mode gate, gate-owned Python runs in
isolated/no-site mode, and the already-running gate closes the FD with a shell builtin
before any driver verifier shell, interpreter, or descendant starts. Ordinary verifier
environment is preserved except for those unsafe startup controls and the internal
handoff variables. HUP, INT, or TERM anywhere from private input
snapshot through durable publication and wrapper cleanup returns `70`, terminates and
reaps an active gate/verifier group, and leaves no wrapper-owned partial receipt.

A `blocked` / `requires_human: true` envelope may be the worker behaving correctly,
but the gate still checks its diff before returning 15. Read `open_questions`, resolve
the ambiguity, and re-dispatch only when a concrete correction is available.

After recording the gate exit, print the post-gate advisory with the same selected
tier. Map independently observed outcomes to controlled evidence: exit 0 to
`gate-accepted`, 10 to `scope-policy-failed`, 11 to `untrusted-worker-claim`, 12 to
`invalid-envelope`, and 13 to `expected-edits-missing`. For exit 14, use
`driver-verification-failed` only when a driver-authored check exposed a bounded
candidate quality gap; fix a verifier that mutated the worktree instead. For exit 15,
use `human-required` when a human decision is actually required and otherwise use
`noncompleted-worker-outcome`. An independent review that finds a bounded quality
defect may use `driver-quality-review-failed`. Driver-
classified permission and authentication failures use `permission-failed` and
`authentication-failed`; neither is escalatable.

```bash
"$PIPELINE/model-recommendation.sh" --stage post-gate \
    --selected-tier "$TIER" --evidence driver-verification-failed
```

Do not pass worker-written rationale as evidence. Do not change `TIER` from this
output. Default/custom model labels and the highest named tier produce
`no-escalation` when no ordered higher tier can be proved.

### 6. Retry policy

At most one corrective re-dispatch, with the specific failure quoted back. If the
second attempt fails, take over the task yourself or escalate to the user. Do not
loop — vague "try again" cycles are the documented failure mode here.

### 7. Preserve or deliberately reject

After exit 0, inspect and commit on the job branch before removing the worktree:

```bash
git -C "$WT" diff
git -C "$WT" add <reviewed-paths>
git -C "$WT" commit -m "<intentional message>"
git -C "$TARGET" worktree remove "$WT"
```

Integrate `JOB_BRANCH` only through the user's normal review/merge flow. If the job
was rejected with gate exit `10`–`14`, run `job.sh status`, inspect its current facts,
and invoke `job.sh cleanup` only with the exact current job ID, state SHA, and Receipt
candidate SHA copied into the three approval flags. It rechecks the receipt, digest,
worktree, branch, deletion domain, and approvals; it never cleans gate-passed or
routed work. If reconciliation advances state after an interruption, stop and obtain
fresh approval for the new state SHA before continuing. The manual reference is to
force-remove only a known disposable rejected worktree and then delete only its exact
job branch. Never force-remove accepted, uncommitted work.

## Never

- Accept a job on the envelope or receipt alone. Run `verify-job.sh` (or the lower-level
  `qa-gate.sh`) with driver-owned `--verify`, then perform human diff review every time.
- Execute `commands_run` or `tests_run` from the envelope. Only driver-authored
  `--verify` commands are executable evidence.
- Suggest `--dangerously-skip-permissions` to clear a permission gate — it approves
  every tool for the whole run. Add a narrow allow-rule, or restructure so the worker
  uses file tools instead of the shell.
- Let the worker author or edit agy's own skills. agy misdescribes its own CLI
  (it invents `agy run`, `--headless`, `agy auth status`). If you author agy skills,
  run `./ground-truth.sh` first and treat its output as the only source of truth.
- Delegate work you cannot write an acceptance test for.

## Maintenance and GitHub reporting

- When `$PIPELINE/update.sh` exists, `update.sh check` is read-only and may be run
  when the user asks for an update/compatibility check. It reports tool releases plus
  verified agy version, official-upstream drift, and fixed 30-day documentation-review
  status. Its fixed GitHub REST evidence and installed-version probes are bounded and
  sanitized; check/watch makes no Git network request. A folder-only install
  intentionally has no checkout updater: do not fetch or pull code automatically to
  manufacture one.
- Run `update.sh apply [TAG]` only on an explicit user request. It refuses dirty or
  detached checkouts and ignored-file collisions, validates tests plus a temporary
  skill install, fast-forwards, and reinstalls this skill. If the real install fails
  after merge, report the partial update and exact recovery command; do not claim an
  atomic rollback. Its explicit Git fetch still honors the caller's Git transport
  configuration; the read-only check's transport isolation does not cover apply.
  Never invoke it during a worker job.
- A detected bug authorizes diagnosis, not external submission. When
  `$PIPELINE/bug-report.sh` exists and the user wants a report, create only a sanitized
  local draft with `bug-report.sh draft`, show it with `bug-report.sh preview`, and
  provide its SHA-256. A folder-only install may instead direct the user to the public
  support page; it must not fetch reporting tools. Run `bug-report.sh submit` only
  after the user explicitly approves that exact hash. Submission sends the confirmed
  in-memory body to an explicitly bound github.com repository, never a mutable file.
- Never attach or paste prompts, source code, envelopes, credentials, absolute paths,
  or raw logs into GitHub. `gh` is optional; if it is absent or fails, keep the draft
  local and stop.
