---
name: agy-worker
description: Delegate bulk, mechanical code work to the Antigravity CLI (agy) as a bounded worker, then independently verify the result before accepting it. Use when a task is large but repetitive — backfilling tests, applying the same edit across many files, mechanical refactors, or repo surveys — and you want to conserve your own context. Do not use for tasks requiring judgment, architecture decisions, or anything where you cannot state acceptance criteria up front.
---

# Delegate to agy, then verify

You are the driver. `agy` is a worker whose self-report is a **claim, never evidence**.
You decide what "done" means, and you prove it yourself.

Pipeline lives at `__REPO_ROOT__`.

## Sandbox requirement (read first)

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
- The volume is high enough to justify ~25–50k tokens of worker overhead per job.

Otherwise just do it yourself. A single-file edit is cheaper direct than delegated.

## Procedure

### 1. Isolate

Never let the worker touch your working tree.

```bash
git worktree add /tmp/agy-job-$$ HEAD
```

### 2. Write acceptance criteria BEFORE dispatching

You must be able to fill in both blanks:
- Files in scope: ...
- Command that proves success: ...

If you cannot, the task is not ready to delegate. Scope it further or do it yourself.

### 3. Dispatch

```bash
WT=/tmp/agy-job-$$
cd __REPO_ROOT__

echo "<task>" | AGY_WORKER_MODE=accept-edits ./agy-worker.sh \
    --persona bulk-test-writer \
    --workdir "$WT" --add-dir "$WT" > /tmp/envelope.json
```

Personas: `bulk-test-writer` (tests only), `diff-reviewer` (review, no edits),
`repo-inventory` (read-only survey). Omit `--persona` for a plain worker.
Tiers: `AGY_WORKER_TIER=cheap|bulk|hard|hardest` (default `bulk`).

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
The driver runs the tests; report tests_run as an empty array.
```

### 4. Read the worker exit code

| Exit | Meaning | Do |
|---|---|---|
| 0 | Envelope produced | Continue to step 5 — you have NOT verified anything yet |
| 2 | No/empty prompt | Your bug |
| 3 | agy returned empty stdout | Check `logs/<job>.stderr`; usually a permission gate |
| 4 | No schema-valid envelope | Worker answered in prose; retighten the task |
| 5 | agy failed | Read stderr; often transient auth — retry once |
| 6 | Permission gate | Read stderr for the exact rule. **Do not** suggest `--dangerously-skip-permissions` |

### 5. Verify — this is the step that matters

```bash
./qa-gate.sh --envelope /tmp/envelope.json --repo "$WT" --base HEAD \
    --verify "cd $WT && <the command from step 2>"
```

**Always pass `--verify`.** Without it the gate only re-runs tests the worker
*claimed*, so a worker reporting `tests_run: []` is accepted having run nothing.

| Exit | Meaning | Do |
|---|---|---|
| 0 | Accepted | Review the diff yourself, then merge the worktree |
| 10 | Touched undeclared files, or claimed files it didn't touch | Reject |
| 11 | Reported a passing test that fails | Reject — the worker is unreliable for this task |
| 12 | Malformed envelope | Reject |
| 13 | Claimed completion, changed nothing | Reject |
| 14 | Your verification command failed | Reject — the work is simply wrong |

A `blocked` / `requires_human: true` envelope is the worker behaving **correctly**.
Read `open_questions`, resolve the ambiguity, re-dispatch. Do not punish it.

### 6. Retry policy

At most one corrective re-dispatch, with the specific failure quoted back. If the
second attempt fails, take over the task yourself or escalate to the user. Do not
loop — vague "try again" cycles are the documented failure mode here.

### 7. Clean up

```bash
git worktree remove --force "$WT"
```

## Never

- Accept a job on the envelope alone. Run `qa-gate.sh` with `--verify`, every time.
- Suggest `--dangerously-skip-permissions` to clear a permission gate — it approves
  every tool for the whole run. Add a narrow allow-rule, or restructure so the worker
  uses file tools instead of the shell.
- Let the worker author or edit agy's own skills. agy misdescribes its own CLI
  (it invents `agy run`, `--headless`, `agy auth status`). If you author agy skills,
  run `./ground-truth.sh` first and treat its output as the only source of truth.
- Delegate work you cannot write an acceptance test for.
