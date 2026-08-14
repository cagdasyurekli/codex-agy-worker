---
name: agy-worker
description: Let Codex use Google Antigravity CLI (agy) for repository exploration, feature work, and project-scale implementation. Codex owns the diff review, verification, repair loop, and transparent delivery assurance; agy is not limited to mechanical edits or predeclared files.
---

# Use agy to explore, build, and repair

`agy-worker` is for making progress on real repository work. Default to the workflow
that matches the user's request; do not decline merely because files, architecture, or
verification commands must be discovered during the work.

## Before dispatch

`agy` is an external CLI backed by Google/Gemini services. Before the first dispatch
for a repository, tell the user which repository/path scope may be read and obtain
explicit approval to send the task and readable repository content to the provider,
unless the user has already approved that exact transmission. Do not put credentials,
private keys, unrelated local files, raw worker logs, or local controller state in the
prompt.

Resolve the installed skill instead of guessing a checkout path:

```bash
PIPELINE="$(bash "$SKILL_ROOT/scripts/resolve-pipeline.sh")" || exit $?
```

Optionally inspect offline readiness before spending quota:

```bash
"$PIPELINE/doctor.sh" --repo /absolute/path/to/target
```

`ready` covers only offline prerequisites. `review-required` does not prevent the
agy-owned default or an explicitly approved literal model; it prevents only reviewed
matrix resolution. `not-ready` is a real dispatch blocker. The doctor never tests
auth, provider availability, task quality, or a future job.

## Choose a workflow

| User request | Pass | agy mode | Codex must do |
|---|---|---|---|
| Explore, inspect, review, understand, or make a plan | `--workflow explore` | `plan` | Report useful findings, spot-check material claims, and state coverage limits. |
| Add a feature, refactor, write tests, or make a bounded repair | `--workflow task` | `accept-edits` by default | Inspect the diff and run relevant checks; request a bounded repair when they fail. |
| Build an app, complete a project, make a broad implementation, or audit-and-fix | `--workflow project --max-cycles 5` | `accept-edits` | Let agy work across the disposable worktree; then run build/test/lint and continue the same conversation with driver-owned results. |

Personas are optional prompt specializations. Use them only when they help the task;
they never authorize dispatch, prove quality, or make a broad task inadmissible. A
plain `explore` job is valid. A broad report is informative, not proof that every
semantic path was inspected.

Do not choose `project` solely to avoid thinking through the request. Ask for product
intent when it is genuinely missing, but let the worker discover ordinary repository
structure and test entry points.

## Prepare an isolated target

Run every edit job in a branch-backed disposable worktree. The lifecycle helper binds
the repository, immutable base, worktree, branch, and job ID in private state:

```bash
TARGET=/absolute/path/to/target-repo
BASE="$(git -C "$TARGET" rev-parse HEAD)"
STATE_DIR="$(mktemp -d -t agyworker-job-state.XXXXXX)"
WT="$(mktemp -d -t agyworker-job-worktree.XXXXXX)"
rmdir "$WT"
"$PIPELINE/job.sh" init --state "$STATE_DIR/job.json" \
  --repo "$TARGET" --worktree "$WT" --branch "agy/job-12345" \
  --base "$BASE" --job-id job-12345
```

The worker may write anywhere in that worktree for a project workflow. Do not allow
writes into `.git`, outside the worktree, through a symlink escape, into local secret
files, or into user-denied paths. Never ask the worker to run shell commands: under
agy sandboxing they run in a scratch directory, not the repository. Use absolute
paths in the prompt and pass `--workdir "$WT" --add-dir "$WT"`.

## Dispatch and measure quality

For a normal task, dispatch once and keep stdout only when exit status is zero:

```bash
printf '%s\n' "$TASK" | "$PIPELINE/agy-worker.sh" \
  --workflow task --workdir "$WT" --add-dir "$WT" > "$STATE_DIR/envelope.json"
```

For a project, start the local controller so its state can safely track multiple
same-conversation repair cycles:

```bash
printf '%s\n' "$TASK" | "$PIPELINE/agy-worker.sh" start \
  --workflow project --max-cycles 5 --workdir "$WT" --add-dir "$WT"
```

Use `status` or bounded `wait` for a deliberately started job. Progress renews only
the idle lease; it does not prove correctness or grant unlimited runtime. The defaults
are `10m` idle, `2h` initial hard deadline, and `12h` maximum. `extend` can make a
bounded, state-SHA-approved extension while fresh progress exists. `cancel` describes
local process closure, not proven remote-provider cancellation.

Codex owns quality measurement:

1. Inspect the actual diff and identify project-owned build, test, lint, or type-check
   commands from the repository and its CI configuration.
2. Run those commands itself. Never execute `commands_run` or `tests_run` from an agy
   envelope.
3. Convert only the bounded, driver-owned result into the strict verification JSON
   required by the controller. Do not pass raw prompts, source, logs, secrets, or
   worker prose back through this channel.
4. If a usable candidate fails a check and the cycle budget remains, continue the
   exact conversation; do not silently start a new provider attempt:

```bash
"$PIPELINE/agy-worker.sh" continue --job-id "$JOB_ID" \
  --approve-state-sha "$STATE_SHA" < "$STATE_DIR/verification.json"
```

5. When Codex has completed its checks or the budget is exhausted, finalize with the
   same strict driver-owned verification JSON and one accurate assurance label:

```bash
"$PIPELINE/agy-worker.sh" finalize --job-id "$JOB_ID" \
  --approve-state-sha "$STATE_SHA" \
  --assurance verified < "$STATE_DIR/verification.json"
```

Use `verified` only when the required checks passed and Codex reviewed the diff. Use
`partially_verified` for a useful candidate with unresolved, failed, or unavailable
checks. Use `blocked` only for a genuine authority, repository-boundary, or execution
blocker. Keep partial work for review; do not delete it simply because a quality check
failed. `restart` starts a new conversation and needs explicit user direction.

For a bounded candidate that needs gate/receipt evidence, run `verify-job.sh` with the
immutable base, a suitable `--only` policy when one exists, and driver-authored
`--verify` commands. A passed gate is strong candidate evidence, not a merge, release,
or claim of general correctness.

## Hard stops

Do not dispatch or continue when the request requires any of these without the
necessary authorization or safe scope:

- provider transmission approval is absent;
- the requested path is outside the approved worktree, `.git`, a credential/secret,
  a symlink escape, or user denylist;
- the request would use dangerous permission/approval bypass flags;
- the request would commit, push, open a PR, submit feedback, publish, install tools,
  or apply an update without the separate approval required for that external action.

Do not confuse a hard stop with ordinary uncertainty. Unknown files, missing initial
test commands, broad architecture, lack of a persona, a partial worker answer, or a
failed first check should normally lead to discovery, same-conversation repair, or
transparent partial delivery instead.

## Selection, failure, and maintenance rules

Model and effort selection belong to the caller. Recommendations are advisory only:
never replace the caller's selection, invent a thinking-level flag, or escalate
permission, authentication, scope-policy, or human-required outcomes. With no
selector, leave the provider's default intact. Use `--literal-model` only when the
caller explicitly selects that unreconciled pass-through surface.

No automatic fresh retry exists. On a terminal worker failure, preserve the job and
offer exact-conversation `resume`, explicit fresh `restart`, or stop. If an agent or
implementation attempt makes a repeated semantic mistake, raise independent review
quality rather than lowering standards or silently changing caller-owned agy settings.

For bugs or improvement reports, use the local draft-first `bug-report.sh` flow. It
requires review of the exact sanitized body and its matching SHA-256 before any GitHub
submission; never submit feedback automatically.

Before changing agy-facing behavior, run `./ground-truth.sh` and inspect `agy --help`.
agy can exit zero with empty output; the answer is `result.structured_output`, not the
echoed schema. Before delivery, run the relevant offline suite and the project checks
you selected. Do not claim provider success, completeness, or release status from
offline evidence alone.
