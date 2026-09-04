# Use agy-worker from Codex

`agy-worker` lets Codex delegate repository exploration and implementation to agy
while retaining responsibility for the actual diff, project-owned checks, repair
decisions, and final assurance label. A worker report is input, never acceptance
evidence.

Complete the [installation and sandbox setup](INSTALLATION.md) first. Installation
does not authorize provider dispatch or repository transmission.

## Before the first provider dispatch

agy is backed by Google/Gemini services. Before the first dispatch for a repository,
Codex must obtain explicit approval for the exact task, transmission mode, and bound
content unless that transmission was already approved. Prefer scoped dispatch for
bounded jobs: it binds
exact reviewed read entries, their selected-content digest, and a write subset, then
stages only selected entries in a fresh owner-private mode-`0700` Gitless provider cwd.
Whole-worktree dispatch remains an explicit manifest-bound exception and may expose
the entire disposable worktree; `--add-dir`, prompt instructions, and later gate paths
do not narrow it.
Before every initial `run`/`start`, `resume`, `continue`, or `restart`, exclude
credentials, secrets, private keys, unrelated private files, raw worker logs,
controller state, and denied paths from the entire default transmission or all scoped
entries. Read [PRIVACY.md](../PRIVACY.md) before use.

Scoped staging reduces provider-visible content but is not a sandbox. The controller
still locally enumerates and validates worktree paths and scope entries; filesystem,
network, `PATH`, `HOME`, same-UID, local-owner, and portable mutation-race residuals
remain. Approving the scope digest grants no provider execution, Git action, driver
acceptance, or publication.

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

## Primary run, status, verify-finalize path

After Codex prepares the branch-backed disposable worktree and an owner-private
state directory, the ordinary controller path is the portable `workflow.sh` facade:

1. Run `workflow.sh run ... --preview` and review its exact canonical transmission
   preview.
2. Repeat the same binding with exactly one explicit mode:
   `--approve-whole-worktree MANIFEST_SHA256`, or `--provider-scope FILE` plus
   `--approve-transmission-sha TRANSMISSION_SHA256`.
3. Use `workflow.sh status ...` for read-only, sanitized progress facts.
4. Use `workflow.sh verify-finalize ...` with repeatable structured
   `--verify-argv JSON_ARRAY` checks, `--approve-dispatch-sha` set to the exact
   `dispatch.state_sha256` from step 3, and the exact owner-private driver
   `--verification-json` when controller finalization is required.

The facade delegates to the existing dispatcher, canonical transmission preview,
verification receipt, and lifecycle finalizer. It does not create a second provider
state machine, infer assurance, choose retry or Git actions, or conceal a finalizer
failure. See [Operate and verify a local project](PROJECT_WORKFLOW.md) for the binding
details and advanced recovery surfaces.

The former `--approve-preview-sha` spelling is deprecated and cannot preserve an
implicit broad default: through at least v0.16.x it works only when paired with
`--legacy-preview-approval`, and it emits a migration warning. New callers must use
one of the two canonical modes above.

The deprecated `--approve-state-sha` facade spelling remains a strict alias for
`--approve-dispatch-sha` during the compatibility window; neither may be omitted for
a bound dispatch and the facade never synthesizes an approval. Gate exits 10–15 keep
their receipt but do not call the lifecycle finalizer. A local pre-dispatch rejection
removes only the exact unchanged facade state created by that invocation when the
dispatch artifact path never appeared, so a corrected invocation can retry safely.

## Two rules that make repository work reliable

1. **Keep the worker off the shell.** Under agy's sandbox, shell tools run in
   `~/.gemini/antigravity-cli/scratch`, not the target repository. Worker file tools
   can reach the approved target. Let the worker edit with file tools; Codex owns
   every repository command.
2. **Bind the intended provider surface.** Prefer the mutually exclusive
   `--provider-scope` mode for bounded jobs and
   bind its exact preview digest. The primary facade accepts this pair directly and
   omits the conflicting `--add-dir` grant. Use whole-worktree mode only as an explicit
   manifest-bound exception; `--add-dir` helps agy file tools reach that target but
   does not narrow provider reads.

## Optional selected-content dispatch

A provider-scope JSON object has closed `read` entries and a `write` subset; each
entry is a relative `file` or `tree` under the canonical worktree. Keep the descriptor
outside the worktree. For example:

```json
{"schema_version":1,"kind":"agy-worker-provider-scope","read":[{"path":"src/parser.py","kind":"file"},{"path":"tests","kind":"tree"}],"write":[{"path":"tests","kind":"tree"}]}
```

Preview and review the exact policy, complete path/kind enumeration, selected-content
digest, and unified transmission digest without starting a provider:

```bash
"$PIPELINE/workflow.sh" run --preview --repo "$TARGET" --job-id "$JOB_ID" \
  --provider-scope "$SCOPE"
"$PIPELINE/workflow.sh" run --repo "$TARGET" --job-id "$JOB_ID" \
  --provider-scope "$SCOPE" --approve-transmission-sha "$TRANSMISSION_SHA" \
  --workflow task --task "$TASK"

# Advanced direct-dispatch compatibility surface:
"$PIPELINE/agy-worker.sh" transmission-preview --workdir "$WT" \
  --provider-scope "$SCOPE" --format json > "$STATE_DIR/scoped-preview.json"

# After reviewing and obtaining approval for the exact transmission_sha256:
printf '%s\n' "$TASK" | "$PIPELINE/agy-worker.sh" \
  --workflow task --mode accept-edits --workdir "$WT" \
  --provider-scope "$SCOPE" --approve-transmission-sha "$TRANSMISSION_SHA" \
  > "$ENVELOPE"
```

`--provider-scope` conflicts with `--add-dir`. Each attempt gets a fresh
owner-private mode-`0700` Gitless cwd containing only selected entries; only authorized
write entries can reconcile back. The controller still enumerates and validates local
worktree paths and identities before staging. Scope approval grants neither the
provider launch itself, a Git action, driver acceptance, nor publication, and scoped
staging is not a security sandbox.

## Advanced: manual bounded task example

This lower-level example preserves accepted work on a branch. Before running it,
emit the mandatory provider notice and obtain explicit whole-worktree transmission approval.
Confirm that the worktree contains no secrets, user-denied paths, or unrelated private
files. Keep the pipeline checkout and target explicit.

```bash
PIPELINE=/absolute/path/to/codex-agy-worker
TARGET=/absolute/path/to/your-project
WT=/tmp/agy-job-12345
JOB_BRANCH=agy/tests-parser-errors-12345
ENVELOPE=/tmp/agy-job-12345-envelope.json
JOB_ID=parser-tests-12345
BASE="$(git -C "$TARGET" rev-parse HEAD)"

git -C "$TARGET" worktree add -b "$JOB_BRANCH" "$WT" "$BASE"
WHOLE_WORKTREE_SHA="$(
  "$PIPELINE/agy-worker.sh" transmission-preview --workdir "$WT" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["manifest_sha256"])'
)"

if ! echo "Add error-path tests for $WT/src/parser.py.
Edit ONLY files under $WT/tests/. Use file tools on absolute paths.
Do NOT run shell commands — they execute in a scratch directory, not this repo.
The driver runs every command. Return commands_run and tests_run as empty arrays." |
  AGY_WORKER_JOB_ID="$JOB_ID" "$PIPELINE/agy-worker.sh" \
    --workflow task --mode accept-edits --tier bulk \
    --workdir "$WT" --add-dir "$WT" \
    --approve-whole-worktree "$WHOLE_WORKTREE_SHA" > "$ENVELOPE"; then
  echo "Dispatch failed; inspect the sanitized terminal state/result. Resume only a candidate-free failure; handle an ERROR candidate with Verification v2, and preserve/finalize or freshly restart a CANCELED candidate." >&2
  exit 1
fi

if "$PIPELINE/qa-gate.sh" --envelope "$ENVELOPE" --repo "$WT" --base "$BASE" \
  --only 'tests/**' --expect-edits \
  --verify-argv '["/usr/bin/git","diff","--check"]' \
  --verify-argv '["python3","-m","pytest","-q","tests/test_parser.py"]'; then
  echo "Candidate passed the evidence gate; review the diff before preserving it."
else
  GATE_RC=$?
  echo "Gate rejected or routed the candidate (exit $GATE_RC)."
  exit "$GATE_RC"
fi
```

Provider children/probes and gate verifiers receive only the documented baseline
environment. If a selected tool genuinely needs another caller variable, opt in its
exact name with repeated `--provider-env NAME`, or use `verify-job.sh --verify-env
NAME` for an ordinary verifier child. Credential-like names, including `HOME`, use
`--verify-credential-env NAME` plus the credential acknowledgement. Verifier-only
values cross the gate through a private pipe, not its ambient environment. Values are not stored in dispatch or receipt
artifacts; unsafe startup, loader, schema-selector, and Git-control hooks are rejected.

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
  --verify-argv "[\"/usr/bin/git\",\"diff\",\"--quiet\",\"$BASE\",\"--\"]"

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
| `--workdir DIR` | — | Source worktree. Without `--provider-scope`, treat all content as worker-readable and potentially transmissible. |
| `--add-dir DIR` | — | Repeatable file-tool root for explicit whole-worktree dispatch; it does not narrow provider reads and conflicts with scoped mode. |
| `--provider-scope FILE` | — | Recommended closed read/write policy for bounded jobs; stages selected content only and requires `--approve-transmission-sha`. |
| `--approve-transmission-sha SHA256` | — | Exact scoped policy/path/content approval binding; grants no execution or downstream authority. |
| `--approve-whole-worktree SHA256` | — | Explicit broad-mode exception bound to the current path/kind manifest. |
| `--boost` | — | Advanced one-cycle `task` profile; may invoke provider-side subagents and protected tools and requires a job-bound risk acknowledgement. |
| `--approve-boost-risk-sha SHA256` | — | Exact warning/job acknowledgement printed by the provider-free Boost preflight; grants no permission or wider transmission. |
| `--provider-env NAME` | — | Repeatable exact-name opt-in for an additional caller variable passed to local `agy` probes and provider launches. |
| `--persona NAME` | — | Optional bounded prompt specialization; never authorization or quality evidence. |
| `--allow-slash-commands` | — | Expert-only opt-in for a fully caller-controlled prompt; disables the normal embedded slash-command protection. |
| `--idle-timeout DURATION` | `AGY_WORKER_IDLE_TIMEOUT` | No valid progress deadline; default `10m`. |
| `--hard-timeout DURATION` | `AGY_WORKER_HARD_TIMEOUT`; `AGY_WORKER_TIMEOUT` | Initial attempt deadline; default `2h`. |
| `--max-runtime DURATION` | `AGY_WORKER_MAX_RUNTIME` | Absolute caller-owned cap; default `12h`. |
| — | `AGY_WORKER_JOB_ID` | Safe artifact-directory name. |

The source-owned option contract is the bundled
[`SKILL.md`](../skills/agy-worker/SKILL.md). Do not infer compatibility, provider
availability, quality, cost, or routing from a label.

Boost is limited to `task`, `accept-edits`, `--max-cycles 1`, no persona, and
default slash protection. The controller also requires the provider init frame to
report `agent=Boost` and `permission_mode=request-review`. A Boost job cannot resume,
restart, or continue; any further attempt uses a new job plus fresh transmission and
risk approvals.

Leave slash expansion disabled when any prompt content comes from a repository or
another model. `--allow-slash-commands` exists only for callers who fully control the
entire prompt because it permits embedded `/skill` and slash-command text. The plan
dispatcher is the narrow built-in exception: it privately stages content and enables
expansion only for its fixed driver prompt.

## Optional personas

`--persona NAME` selects one shipped prompt template. Persona text is guidance only:
it never grants capability or approval, chooses routing, verifies a result, or changes
the driver’s acceptance decision. The direct selection and its read-only/edit-mode
restrictions remain part of the dispatcher contract.

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

- default whole-worktree approval is absent, or scoped mode lacks the exact reviewed
  policy and matching `transmission_sha256`;
- a credential, secret, private key, unrelated private file, or user-denied path is
  present in the default worktree transmission or any entry selected for scoped staging;
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
