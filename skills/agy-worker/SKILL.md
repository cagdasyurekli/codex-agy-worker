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
accepted changes can be committed before cleanup.

```bash
PIPELINE="$(bash "$SKILL_ROOT/scripts/resolve-pipeline.sh")" || exit $?
TARGET=/absolute/path/to/target-repo
WT=/tmp/agy-job-12345
JOB_BRANCH=agy/job-12345
BASE="$(git -C "$TARGET" rev-parse HEAD)"
git -C "$TARGET" worktree add -b "$JOB_BRANCH" "$WT" "$BASE"
```

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

Personas: `bulk-test-writer` (tests only), `diff-reviewer` (review, no edits),
`repo-inventory` (read-only survey). Omit `--persona` for a plain worker.
The dispatcher rejects `accept-edits` for the read-only personas. Tiers may be passed
as `--tier cheap|bulk|hard|hardest|default` or through `AGY_WORKER_TIER`.
Tier selection is explicit: retries reuse the same model, and this skill does not
infer a thinking level or silently escalate models after a gate failure. The
recommendation helper has no thinking-level option and never invokes the dispatcher.
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
"$PIPELINE/qa-gate.sh" --envelope /tmp/envelope.json --repo "$WT" --base "$BASE" \
    --only 'tests/**' --expect-edits \
    --verify "git -C '$WT' diff --check" \
    --verify "cd '$WT' && <the command from step 2>"
```

**Always pass `--verify` for acceptance.** The gate never executes any command from
the worker envelope. Use `--only` whenever the task has a path policy, especially
for `bulk-test-writer`; its persona prompt is not enforcement. `--base` must be the
full commit ID captured before dispatch, never `HEAD` or another mutable ref.

| Exit | Meaning | Do |
|---|---|---|
| 0 | Evidence accepted | Review the diff; no merge or commit happened automatically |
| 10 | Scope mismatch, invalid path, or `--only` violation | Reject |
| 11 | Worker reported a command or test | Reject; it was not executed |
| 12 | Envelope failed the checked-in schema | Reject |
| 13 | `--expect-edits` job changed nothing | Reject |
| 14 | Verification failed or mutated the worktree | Reject |
| 15 | Partial/failed/blocked/human-required outcome | Escalate; never accept |
| 64 | Bad invocation, invalid Git base, or missing `--verify` | Fix the driver command |

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
was rejected and its disposable changes should be discarded, remove the worktree
with `--force` and then delete only `JOB_BRANCH`. Never force-remove accepted,
uncommitted work.

## Never

- Accept a job on the envelope alone. Run `qa-gate.sh` with `--verify`, every time.
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
  status. A folder-only install intentionally has no checkout updater: do not fetch or
  pull code automatically to manufacture one.
- Run `update.sh apply [TAG]` only on an explicit user request. It refuses dirty or
  detached checkouts and ignored-file collisions, validates tests plus a temporary
  skill install, fast-forwards, and reinstalls this skill. If the real install fails
  after merge, report the partial update and exact recovery command; do not claim an
  atomic rollback. Never invoke it during a worker job.
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
