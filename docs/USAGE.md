# Use agy-worker from Codex

`agy-worker` lets Codex delegate repository exploration and implementation to agy
while retaining responsibility for the actual diff, project-owned checks, repair
decisions, and final assurance label. A worker report is input, never acceptance
evidence.

Complete the [installation and sandbox setup](INSTALLATION.md) first. Installation
does not authorize provider dispatch or repository transmission.

## Before the first provider dispatch

agy is backed by Google/Gemini services. Before the first dispatch for a repository,
Codex must obtain explicit approval to send the task and the entire disposable
worktree through agy to Google/Gemini unless that exact transmission was already
approved. A narrower approval is valid only when the worktree contains only approved
content. Before every initial `run`/`start`, `resume`, `continue`, or `restart`, ensure
credentials, secrets, private keys, unrelated private files, raw worker logs,
controller state, and user-denied paths are absent from the entire worktree. Prompt
instructions not to read a present file are not a privacy control. Read
[PRIVACY.md](../PRIVACY.md) before use.

Before every provider-launch attempt—initial `run`/`start`, `resume`, `continue`, and
`restart`—Codex must tell the user in one or two concise sentences:

- the public-safe task being sent;
- the caller-selected model;
- caller-selected effort when separately selectable; and
- the exact resolved model slug.

When no model is selected or `default` is used, state that agy's provider default is
used and model/effort is unresolved. For fixed, compound, or literal models where
effort is not separately selectable, say so without inventing backend reasoning or a
thinking level. If preflight fails before provider launch, state that the task was not
sent. If provider reach is uncertain, state that it is unverified.

## Choose a workflow

| What you want | Workflow | What Codex must deliver |
|---|---|---|
| Understand, plan, or review a repository | `explore` | A useful read-only report, independently spot-checked, with stated coverage limits. |
| Implement a feature, refactor, or tests | `task` | A worktree diff plus Codex-run relevant checks; failures can trigger bounded same-conversation repair. |
| Build an app or complete broad project work | `project` | Repo-wide worktree changes, build/test/lint measurement, bounded repair attempts, and an honest assurance label. |

`explore` and `task` accept `1..2` total provider attempts and default to `2`.
`project` accepts `1..5` and defaults to `5`. Legacy raw mode is exactly one attempt.
Unknown files, architecture, or initial test commands are ordinary discovery work;
they are not reasons to reject a useful task.

### Explicit delegation-first policy

Delegation-first is an opt-in coordinator policy, enabled only with literal
`user_opt_in: true`. After instruction discovery, scope/privacy confirmation,
disposable-worktree setup, and driver verification planning, the first substantive
repository action goes to agy: use `explore` for investigation and `task` or
`project` for implementation. Run `delegation-policy.sh` to evaluate that offline
policy decision before dispatch.

The evaluator cannot infer earlier work, launch agy, select or change a model or
effort, authorize Git operations, or accept a candidate. A missing transmission
approval, provider/preflight failure, hard stop, or exhausted cycle budget fails
closed without silently moving the substantive task to Codex. Direct-Codex work or
second-eye-only use must be an explicit override. Fixed agy overhead can make small
tasks inefficient, and token observations do not by themselves prove billing,
quota, cost, or savings.

After installation, start a new Codex session and ask in natural language:

> Use the agy-worker skill to add error-path tests for the parser modules under
> `/absolute/path/to/project/src/`. Allow changes only under `tests/`, verify with
> `python3 -m pytest -q tests/test_parser.py`, and preserve accepted work on a branch.

For a larger request:

> Use agy-worker to build this application in `/absolute/path/to/project/`. Discover
> the existing structure and test commands, implement the requested behavior across
> the project, run the relevant checks, and repair failures in the same conversation.

Codex creates an isolated worktree, dispatches the matching workflow, inspects the
diff, and runs driver-owned checks. You do not need to supply a final file list, a
persona, or every verification command before starting.

## Two rules that make repository work reliable

1. **Keep the worker off the shell.** Under agy's sandbox, shell tools run in
   `~/.gemini/antigravity-cli/scratch`, not the target repository. Worker file tools
   can reach the approved target. Let the worker edit with file tools; Codex owns
   every repository command.
2. **Use absolute paths and `--add-dir`.** Name the absolute target in the task and
   pass the same root with `--workdir` and `--add-dir`. Using only one can send the
   worker to the wrong place. `--add-dir` does not narrow the `--workdir` read
   boundary: all worktree content remains potentially readable and transmissible.

## Manual bounded task example

This lower-level example preserves accepted work on a branch. Before running it,
emit the mandatory provider notice and obtain whole-worktree transmission approval.
Confirm that the worktree contains no secrets, user-denied paths, or unrelated private
files. Keep the pipeline checkout and target explicit. The selected `bulk-test-writer` persona is
experimental: it has been exercised on a real task but has not produced an accepted
real delivery. Its inclusion here is not a quality claim.

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

Exit 0 means the evidence gate accepted this exact candidate under the exercised
scope and verifier commands. It does not merge, publish, or establish general
correctness. Review and preserve the work before removing the worktree:

```bash
git -C "$WT" diff
git -C "$WT" add tests/
git -C "$WT" commit -m "test: cover parser error paths"
git -C "$TARGET" worktree remove "$WT"

# Integrate JOB_BRANCH only after your normal review/PR process.
```

Commit and integration remain separate user-authorized actions. If the gate rejects
the candidate and you intentionally decide to discard it, follow your normal
worktree cleanup policy; do not make destructive cleanup an automatic consequence of
a failed check.

Gate exits 10–14 reject the candidate, exit 15 routes questions to a human, and exit
64 means the driver invocation is invalid. Never add an automatic shell retry loop.
Detailed result, Verification v2, continuation, finalization, preservation, and
recovery rules belong to the [project workflow guide](PROJECT_WORKFLOW.md).

## Read-only inventory example

An `explore` report is useful planning input, not proof that every semantic path was
inspected. Independently spot-check material paths and commands:

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

# Open a sample of every claimed path and verify discovered commands against
# package/CI files before using the inventory for planning.
```

Exit 0 proves only that no files changed and the driver command passed. It does not
prove the worker's architecture prose or completeness.

## Common options

| Worker option | Environment equivalent | Meaning |
|---|---|---|
| `--workflow explore|task|project` | — | Select read-only exploration, ordinary implementation, or project-scale iterative work. |
| `--max-cycles 1..2` | — | `explore` or `task` attempt budget; default `2`. |
| `--max-cycles 1..5` | — | `project` attempt budget; default `5`. |
| `--mode plan|accept-edits` | `AGY_WORKER_MODE` | Raw compatibility mode; explicit workflows constrain it. |
| `--tier cheap|bulk|hard|hardest|default` | `AGY_WORKER_TIER` | Legacy named tier or agy-owned default. |
| `--model EXACT_MODEL` | `AGY_WORKER_MODEL` | Reviewed exact slug or adjustable base used with effort. |
| `--effort low|medium|high` | `AGY_WORKER_EFFORT` | Requires an adjustable base and resolves to one exact slug. |
| `--literal-model EXACT_SLUG` | — | CLI-only unreconciled caller-owned pass-through. |
| `--workdir DIR` | — | agy's workspace; treat all content as worker-readable and potentially transmissible. |
| `--add-dir DIR` | — | Repeatable file-tool root inside `--workdir`; it does not narrow the worktree read boundary. |
| `--persona NAME` | — | Optional bounded prompt specialization; never authorization or quality evidence. |
| `--allow-slash-commands` | — | Expert-only opt-in for a fully caller-controlled prompt; disables the normal embedded slash-command protection. |
| `--idle-timeout DURATION` | `AGY_WORKER_IDLE_TIMEOUT` | No valid progress deadline; default `10m`. |
| `--hard-timeout DURATION` | `AGY_WORKER_HARD_TIMEOUT`; `AGY_WORKER_TIMEOUT` | Initial attempt deadline; default `2h`. |
| `--max-runtime DURATION` | `AGY_WORKER_MAX_RUNTIME` | Absolute caller-owned cap; default `12h`. |
| — | `AGY_WORKER_JOB_ID` | Safe artifact-directory name. |

The source-owned option contract is the bundled
[`SKILL.md`](../skills/agy-worker/SKILL.md). Do not infer compatibility, provider
availability, quality, cost, or routing from a label.

Leave slash expansion disabled when any prompt content comes from a repository or
another model. `--allow-slash-commands` exists only for callers who fully control the
entire prompt because it permits embedded `/skill` and slash-command text. The plan
dispatcher is the narrow built-in exception: it privately stages content and enables
expansion only for its fixed driver prompt.

## Personas and workload profiles

Personas are optional prompt specializations, not capability, approval, routing, or
quality gates. Their evidence status and generated registry are owned by
[Persona Evidence Registry v1](PERSONAS.md); do not copy its current table here.

Workload profiles are fixed data-only skeletons. They cannot contain a repository,
path, command, selection, authorization, dispatch, acceptance decision, or Git
action. See [data-only workload profiles](PROFILES.md) for their exact contract and
commands.

## Model and effort selection

Model and effort selection belongs to the caller. Recommendations are advisory and
never silently alter that selection. With no selector, the dispatcher sends no model
and leaves agy's default unchanged.

Legacy named tiers currently resolve as follows:

| Tier | Exact downstream selection |
|---|---|
| `cheap` | `gemini-3.6-flash-low` |
| `bulk` | `gemini-3.6-flash-medium` |
| `hard` | `gemini-3.1-pro-high` |
| `hardest` | `claude-opus-4-6-thinking` |
| `default` | no `--model`; let agy choose |

These constants predate the current compatibility matrix. During version drift they
remain best-effort labels, not verified claims about price, difficulty, provider,
availability, or behavioral equivalence.

Reviewed `--model` and `--effort` inputs resolve through the current checked-in
matrix to one exact slug. Fixed, compound, literal, and already-compound slugs do not
accept an invented effort or thinking flag. Selector sources have no silent
precedence: repeated selectors, CLI/environment duplicates, tier plus direct
selection, effort without an adjustable model, and unsupported pairs fail before
dispatch.

`--literal-model` is a narrow, CLI-only caller-owned pass-through for a closed slug.
It performs no matrix lookup and records
`compatibility_status: unreconciled-pass-through`. It makes no compatibility, cost,
provider, availability, or routing claim; agy or the provider may reject it.

Every provider attempt reuses the caller-owned frozen selection for that job. A
model recommendation can report advice but cannot dispatch, change state, apply
itself, or turn permission, authentication, path-policy, or human-required failures
into a reason for higher model spend. See
[installation and compatibility](INSTALLATION.md#version-drift-and-direct-model-selection)
for the drift preflight boundary.

Request advisory JSON before dispatch or after a driver-owned gate result:

```bash
./model-recommendation.sh --stage pre-dispatch \
  --selected-tier cheap --evidence batched-mechanical

./model-recommendation.sh --stage post-gate \
  --selected-tier bulk --evidence driver-verification-failed
```

The command never calls `agy`, runs the gate, changes job state, or applies its
recommendation. Its finite evidence vocabulary and escalation policy are enforced by
the bundled runtime; inspect `--help` before scripting additional cases.

## Verification and honest outcomes

Codex must inspect the Git-derived candidate scope and run its own project checks.
Never execute `commands_run` or `tests_run` from a worker envelope. A failed first
check is normally a bounded repair signal, not automatic deletion or a reason to
start a fresh conversation.

Use `verified` only when the exact candidate satisfies the applicable strict
driver-owned policy. Use `partially_verified` for useful work with unresolved,
failed, missing, or unavailable checks; use `rejected` or `blocked` only for Codex's
actual decision. A green gate proves the exercised candidate and checks, not general
correctness or permission to merge.

Continue with the [project workflow guide](PROJECT_WORKFLOW.md) for asynchronous
jobs, Verification v2, same-conversation repair, receipts, finalization, and recovery.
For the underlying acceptance model, read
[how to verify agent output](VERIFYING_AGENT_OUTPUT.md) and the
[public conformance contract](CONFORMANCE.md).

## Hard boundaries

Stop before dispatch, continuation, or external action when:

- whole-worktree provider-transmission approval is absent, or a narrower approval does
  not match the worktree's complete contents;
- a credential, secret, private key, unrelated private file, or user-denied path is
  present anywhere in the worktree before a provider launch;
- a requested write path leaves the approved worktree, enters `.git`, follows a
  symlink escape, or violates the task's write policy;
- a dangerous permission/approval bypass would be required; or
- commit, push, PR, publication, issue submission, installation, or an update lacks
  its separately required approval.

Ordinary uncertainty is not a hard stop. A broad codebase, unknown files, missing
initial test commands, lack of a persona, a partial worker answer, or a failed first
check should normally lead to discovery, bounded same-conversation repair, or an
honest partial result.

Support and project terms are in [SUPPORT.md](../SUPPORT.md) and
[TERMS.md](../TERMS.md).
