# Operate and verify a local project

This guide covers the driver-owned workflow after a user has selected a repository,
approved its provider-transmission scope, and chosen an `explore`, `task`, or
`project` request. The worker envelope is input, never acceptance evidence. Codex
reviews the actual candidate, derives scope from Git, and runs its own checks.

For the shorter conceptual walkthrough, read
[How to verify AI coding-agent changes without trusting the worker report](VERIFYING_AGENT_OUTPUT.md).
For the public synthetic gate contract, read [QA gate conformance v1](CONFORMANCE.md).

## Lifecycle at a glance

1. Capture an immutable base commit and create an isolated worktree.
2. Choose the transmission mode. The primary facade uses default whole-worktree
   visibility, so its entire content must be approved as potentially provider-readable
   and transmissible. Optional direct
   `agy-worker.sh --provider-scope FILE --approve-transmission-sha SHA256` binds exact
   reviewed read/write entries and a selected-content digest, then stages only selected
   entries in a fresh owner-private mode-`0700` Gitless provider cwd.
3. Dispatch only the approved task. In default mode, requested paths constrain writes
   and candidate acceptance, not provider reads. Scoped staging narrows provider-visible
   content, but the controller still locally enumerates and validates worktree paths;
   it is not a sandbox, and scope approval grants no provider execution, Git,
   acceptance, or publication authority. See
   [optional selected-content dispatch](USAGE.md#optional-selected-content-dispatch).
4. Retrieve the bound candidate and inspect its Git diff without trusting the worker
   report or executing any worker-reported command.
5. Run writable build and test commands in a separate verification copy.
6. Bind the driver findings to Verification v2, then choose a bounded repair in the
   same conversation or finalize an honest disposition.
7. Preserve accepted work on its branch. Cleanup is available only for narrowly
   rejected or explicitly discarded candidates and requires current exact approvals.

Assurance labels are deliberately practical. `verified` is available only when the
strict workflow policy is satisfied; `partially_verified` records useful work with
unresolved evidence; `rejected` records work Codex declines; and `blocked` records a
genuine authority, repository-boundary, or execution block. A failed first check is
a repair signal, not permission to erase the candidate or start a fresh conversation.

## Primary facade and advanced recovery

For ordinary operation, use the portable `workflow.sh` facade: `run --preview`
produces the canonical transmission preview, approved `run` delegates dispatch,
`status` returns read-only sanitized facts, and `verify-finalize` delegates structured
driver checks and any explicitly supplied owner-private Verification v2 record. The
ordinary run needs only `--repo ABSOLUTE_PATH --job-id ID` plus optional `--base`.
When base is omitted, the first call resolves and binds `HEAD` once. The façade derives
deterministic state, worktree, and branch bindings under an owner-private
`XDG_STATE_HOME/agy-worker/workflows/` tree, or `HOME/.local/state/agy-worker/workflows/`,
and invokes `job.sh init` as the Git lifecycle authority. The preview call retains
those exact resources for the approved second call. The facade does not infer
assurance or duplicate the provider lifecycle state machine.

```bash
./workflow.sh run --preview --repo "$TARGET" --job-id "$JOB_ID"
./workflow.sh run --repo "$TARGET" --job-id "$JOB_ID" \
  --approve-preview-sha "$PREVIEW_SHA" --workflow task --task "$TASK"
```

The explicit `--state`, `--worktree`, `--branch`, and full `--base` tuple remains an
advanced compatibility mode. Partial mixing is rejected. A local pre-dispatch failure
can invoke lifecycle-owned `rollback-ready` only for clean façade-created resources
created by that same invocation. Exact job/state/repo/worktree/branch/base bindings,
an unchanged empty candidate, and absence of every dispatch artifact are mandatory.
Preview, stale approval, drift, or any dispatch evidence retains state for recovery;
the facade never performs Git deletion itself.

`workflow.sh status` also accepts an explicitly supplied existing low-level job state,
dispatcher state, or dispatcher job ID. The projection reports its source, phase or
controller phase, mechanically available actions, and advanced-recovery guidance.
It never migrates legacy bytes or moves low-level mutations into the façade.
For a bound dispatch, copy `dispatch.state_sha256` from facade `status` and pass it as
`--approve-dispatch-sha`; missing, stale, or changed state fails before finalization.
The deprecated `--approve-state-sha` spelling remains an exact mutually exclusive
alias. Gate rejection/routing preserves its receipt without finalizing assurance.

The lower-level dispatcher, gate, receipt, and lifecycle commands documented below
remain the advanced recovery and compatibility surfaces. They are authorities that
the facade composes, not parallel implementations to keep in sync.

## Quality and command boundary

The worker may claim that it changed files or ran tests. Those claims remain data.
Never execute `commands_run` or `tests_run` from an envelope. The driver chooses every
command it runs and applies the result only to the exact candidate exercised.

The canonical gate has these core controls:

- `--base FULL_COMMIT_ID` is required. Capture it before dispatch; mutable names such
  as `HEAD` and branch names are rejected.
- At least one driver-owned verifier is mandatory. The normal repeatable path is
  `--verify-argv CANONICAL_JSON_ARRAY`; it runs from the repository root without an
  implicit shell. `--verify-shell` and legacy `--verify` remain advanced compatibility
  paths and require their documented acknowledgements.
- Ordinary verifier variables use `--verify-env NAME`. Credential-like names require
  `--verify-credential-env NAME` plus the credential-access acknowledgement; neither
  flag grants a missing value.
- `--only PATHGLOB` is repeatable and constrains every changed path.
- `--allow PATHGLOB` permits a known undeclared artifact but does not override
  `--only`.
- `--expect-edits` turns a completed no-op into exit `13`.
- Exit `15` routes questions to a human; it is never acceptance.

For a direct gate invocation, capture the base before dispatch and review the diff
after the gate accepts the exact state:

```bash
BASE="$(git -C "$WT" rev-parse HEAD)"

"$PIPELINE/qa-gate.sh" --envelope "$ENVELOPE" --repo "$WT" --base "$BASE" \
  --only 'tests/**' --expect-edits \
  --verify-argv '["/usr/bin/git","diff","--check"]' \
  --verify-argv '["python3","-m","pytest","-q","tests/test_parser.py"]'
```

Exit `0` means only that the evidence gate accepted the exercised state and verifier
commands. It does not merge, commit, prove general correctness, or replace human diff
review. Gate exits `10`–`14` reject the candidate, exit `15` routes it, and exit `64`
means the driver invocation is invalid.

## Progress-aware local jobs

`run` remains synchronous. For a long explicitly approved job, `start` returns an
opaque job ID after the local controller handshake. `status`, `wait`, `result`,
`resume`, `restart`, `continue`, and `finalize` default to machine-readable JSON and
accept `--format text`.

The text form is exactly three sanitized, driver-owned lines. It excludes prompts,
worker prose, conversation IDs, paths, and raw logs. For an unreviewed current
candidate, it gives the exact bound `result` command and tells the driver to review it
and build Verification v2. It does not recommend `continue` or `finalize`. For a
candidate with a recorded disposition, it offers only optional finalized-result
readback and, when mechanically available, the exact fresh-restart alternative.

Every emitted action or stale-approval rerun command uses the caller-resolved
symbolic launcher `"$PIPELINE/agy-worker.sh"`; export `PIPELINE` before copying it.
`result` returns a bound schema-valid candidate only when `result_available` is true.
It is not a provider-success or acceptance claim. `extend` and `cancel` require the
current state SHA. Eligible `resume` preserves the exact stored conversation;
`restart` starts a fresh attempt. Neither happens automatically.

Lifecycle state v10 uses these controller phases:

| Phase | Meaning |
|---|---|
| `dispatching` | An initial, resume, or restart attempt is active. |
| `attempt-failed` | The attempt failed before producing a candidate. |
| `awaiting-verification` | A recognized candidate is available for driver review. |
| `repairing` | A driver-requested continuation is active. |
| `repair-failed` | A continuation attempt failed. |
| `completed` / `blocked` | The local controller is terminal. |

The separate driver dispositions are `verified`, `partially_verified`, `rejected`,
and `blocked`. `controller_phase` reports mechanical progress;
`driver_disposition` reports the recorded Codex decision. Deprecated compatibility
fields such as `phase`, `next_action`, `next_action_command`, and
`has_prior_candidate` are not recommendations or acceptance facts.

Only provider `init`, `step_update`, and terminal `result` events renew the idle
lease. They do not prove success, extend the hard deadline, or extend the
caller-owned maximum runtime. The controller exposes only sanitized elapsed and
progress facts; it does not print prompts, raw stderr, or conversation IDs.

There is no automatic fresh retry or continuation:

- A candidate-free failed state may offer SHA-approved `resume` for the exact stored
  conversation or a SHA-approved fresh `restart`.
- A valid provider `ERROR` candidate is `unreviewed`: retrieve it with `result`, build
  Verification v2 from driver-owned evidence, then choose `continue` or `finalize`.
- A valid provider `CANCELED` or `CANCELLED` candidate is preserved for `result` and
  finalization, or an explicit fresh `restart`; it is never resumed or continued.
- Local `status`, `wait`, `result`, `extend`, and `cancel` describe the controller,
  not provider truth. A locally cancelled job therefore reports
  `remote_cancel_unverified`.

### Reading lifecycle JSON and supplying Verification v2

For new integrations, use `controller_phase` for mechanical progress and
`driver_disposition` for the recorded Codex decision. Ignore the deprecated
compatibility fields `phase`, `next_action`, `next_action_command`, and
`has_prior_candidate` unless maintaining an existing integration.

Read public lifecycle JSON in this order: first `status` for `state_sha256`,
`controller_phase`, `cycle`/`max_cycles`, `failure_stage`, and
`available_actions`. `worktree_changes_present` describes current ambient dirtiness;
`worktree_changed_since_dispatch` is the attribution-relevant signal.

Controller-private V11 state also persists a sanitized
`provider_terminal_status`: `unknown`, `success`, `error`, or `cancelled`, derived
only from the exact attempt's structurally valid outer terminal event. The public
`status`, `wait`, and `result` JSON intentionally omit it. It is not candidate
acceptance, ambient provider/account health, quota, routing, model or task
acceptance, or billing evidence. A terminal without a recognized structured report
can retain that private enum while public `candidate_recognized` is false and
`failure_stage` is `missing_structured_output`.

Current V11 preserves every externally bound V10 transition and action decision
atomically while adding this private diagnostic field; migration does not create new
continuation, restart, or acceptance authority. Then use `candidate_sha256` only
when `result_available` is
`true`; then retrieve `result` only when its mechanically derived action is present.
Review that bound result and build driver evidence before choosing an eligible
`continue` or `finalize`; the controller does not choose either. If the candidate hash
is `null`, do not construct Verification v2 for it.

Driver checks that can write bytecode, caches, coverage output, generated files, or
other artifacts must run in an isolated verification copy. Do not delete or
regenerate artifacts in the candidate to make its snapshot match again: tracked,
untracked, deleted, and ignored paths are all bound candidate bytes. First inspect
the candidate read-only, then create a new directory under a private parent and run
build or test commands in that copy. The copy deliberately omits `.git`, so
Git-dependent checks stay read-only against the original candidate:

```bash
VERIFY_PARENT="$(mktemp -d -t agyworker-verify.XXXXXX)" || exit $?
VERIFY_PARENT="$(CDPATH= cd -- "$VERIFY_PARENT" && pwd -P)" || exit $?
VERIFY_DIR="$VERIFY_PARENT/candidate"
"$PIPELINE/agy-worker.sh" verification-copy --job-id "$JOB_ID" \
  --destination "$VERIFY_DIR" --format text
( cd "$VERIFY_DIR" && /usr/bin/python3 -m pytest -q )
```

`verification-copy` rebinds the current result, command, schemas, root, and candidate
before copying. It preserves regular-file bytes and executable bits, rebases every
contained symlink to an equivalent relative target inside the copy, rejects
broken/outward/Git-administration links, and rebinds the source afterward. The
destination must be new, private, canonical, and outside the candidate. A drifted
source makes copy, `continue`, and `finalize` unavailable.

The bounded pre/copy/post binding assumes an owner-controlled, quiescent candidate.
It detects ordinary source or destination-parent drift but does not claim same-UID
tamper resistance. A failed copy is never reported as created. After wrapper argument
parsing, candidate, binding, copy, or destination failures return `20`; malformed
wrapper arguments return `64`.

The canonical Verification v2 validator is the bounded `_validate_verification` and
`_require_current_candidate_verification` implementation in
[`skills/agy-worker/runtime/scripts/agy_dispatch.py`](../skills/agy-worker/runtime/scripts/agy_dispatch.py).
There is intentionally no standalone Verification v2 schema. The validator is the
canonical source and accepts no unknown fields. This complete example obtains the
candidate digest from public `status`, not from a path, worker prose, or a locally
rehashed candidate:

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

`resume`, `restart`, `continue`, `finalize`, `cancel`, and `extend` use a current
`--approve-state-sha`; `wait` instead uses `--after-state-sha`. Read-only `status`
and `result` need neither. Direct reviewed model selection has a separate
compatibility approval and never authorizes a lifecycle write.

Every explicit workflow has a driver-owned quality loop. `continue` and `finalize`
require Verification v2 bound to the current candidate SHA. The controller records
bounded passed, failed, advisory, and missing checks; coverage; verified findings;
unresolved gaps; and whether diff review completed. It executes no command from that
JSON and starts no fresh conversation automatically.

Codex may use any review evidence, including an advisory, an unresolved gap, or a
review-driven finding, to request same-conversation repair. The `verified` policy is
strict: `explore` needs complete coverage with no unresolved gaps or failed/missing
checks; `task` and `project` need at least one passed check, no failed or missing
checks, and completed diff review. `finalize` records the exact Codex declaration; a
worker cannot self-assign a disposition.

## Manage one isolated local job

`job.sh` records one explicit branch-backed worktree in a private external state
file, delegates verification to `verify-job.sh`, and keeps every external action
outside the lifecycle. It never dispatches agy, commits, pushes, opens a pull request,
merges, releases, or changes a model.

Create the state directory and worktree outside the repository and capture a full
immutable base:

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

Lifecycle Git commands use fixed `/usr/bin/git` with system/global and caller Git
environment removed, a private empty hooks directory, and prompts, pagers, fsmonitor,
external diff, protocols, and recursive submodules disabled. `init` rejects local
configuration that could authorize hooks, fsmonitor, pagers, or content filters, and
rejects effective filter attributes in the immutable base. Rejected preflight creates
no state, ref, or worktree.

Run the separately approved worker against the printed worktree through the normal
dispatcher. Then bind its envelope to the gate and Receipt protocol:

```bash
./job.sh verify \
  --state "$STATE_DIR/job.json" \
  --receipt "$STATE_DIR/receipt.json" \
  --envelope envelope.json \
  --only 'tests/**' --expect-edits \
  --verify-argv '["/usr/bin/git","diff","--check"]'
```

For `verified-gate-passed`, `preserve-instructions` prints review, commit, and
integration commands but runs none of them. A receiptless pre-gate dispatch failure
is a separate recovery path: `job.sh abort` requires the exact terminal dispatch
binding, a closed supervisor process group, current job/state/candidate SHA approvals,
and an empty candidate unless the caller explicitly supplies `--discard-unverified`.
It refuses active, receipt-bound, gate-passed, routed, or otherwise unbound residuals.

Cleanup is intentionally narrower. Only Receipt exits `10`–`14` with verdict
`rejected` qualify. First inspect `status`, then manually copy its current identifiers
and approvals into one fresh command:

```bash
./job.sh cleanup \
  --state "$STATE_DIR/job.json" \
  --approve-job EXACT_JOB_ID \
  --approve-state-sha CURRENT_STATE_SHA256 \
  --approve-candidate-sha RECEIPT_CANDIDATE_STATE_SHA256
```

Cleanup revalidates the receipt, worktree registration, branch/ref/base, candidate
digest, deletion domain, and all three approvals. It persists progress before each
destructive step, removes only the exact registered worktree, and compare-deletes
only the exact unchanged branch ref. Interrupted reconciliation consumes the old
approval: inspect the new state and issue a fresh command. Gate-passed, routed,
committed, moved, tampered, stale, foreign, special-node, nested-repository, or
digest-mismatched states are retained for manual recovery. Candidate symlinks are
removed as link nodes and never followed. The private cleaned tombstone remains until
a separate manual retention decision.

## Preserve a local Evidence Receipt v1

`verify-job.sh` runs the canonical gate and, only for gate outcomes `0` and
`10`–`15`, durably publishes a private JSON receipt. It records hashes of the exact
envelope snapshot, ordered path policy, and driver-owned verifier commands; the
immutable base; the gate's initial and final candidate-state digests; and the gate's
exact outcome. It stores deterministic labels rather than verifier command text or
output.

Create a new owner-private directory outside the audited repository. The parent and
receipt path must be canonical; the command rejects symlinks, overwrite,
repository-contained targets, and group/other-accessible parents:

```bash
umask 077
RECEIPT_DIR="$(mktemp -d -t agyworker-receipts.XXXXXX)"

./verify-job.sh --receipt "$RECEIPT_DIR/job.json" \
  --envelope envelope.json --repo "$WT" --base "$BASE" \
  --only 'tests/**' --expect-edits \
  --verify-argv '["/usr/bin/git","diff","--check"]' \
  --verify-argv '["python3","-m","pytest","-q","tests/test_parser.py"]'
```

`--selection FILE` may bind one validated current selection record.
`--pre-recommendation FILE` may bind one canonical pre-dispatch advisory. Both are
optional and accepted at most once. The command never discovers `logs/`, creates a
recommendation, or applies one.

The receipt maps gate `0` to `gate-passed`, exits `10`–`14` to `rejected`, and exit
`15` to `routed`, then returns that exact exit only after durable no-overwrite
publication. Input or preflight errors return `64`; missing or inconsistent gate
evidence returns `70`; validation or publication failure returns `74`. Those failures
publish no receipt.

Every receipt explicitly states that it is unsigned and not tamper-evident. A receipt
does not make a candidate accepted, signed, authentic, correct, or safe. Validate it
and optionally bind the original envelope with:

```bash
python3 -B skills/agy-worker/runtime/scripts/evidence_receipt.py validate \
  --receipt "$RECEIPT_DIR/job.json" --envelope envelope.json
```

Render the validated receipt without invoking agy, Git, the gate, model routing, or
the network:

```bash
./evidence-report.sh --receipt "$RECEIPT_DIR/job.json" --format text
./evidence-report.sh --receipt "$RECEIPT_DIR/job.json" --format markdown \
  --output "$RECEIPT_DIR/job.md"
./evidence-report.sh --receipt "$RECEIPT_DIR/job.json" --format json
```

Standard output is the default. `--output` must be a new canonical absolute path and
is published mode `0600` without overwrite. The renderer emits only bounded receipt
facts and fixed integrity/human-review limits. It never prints source, prompts, raw
commands, logs, absolute repository paths, or worker prose. A rendered report remains
unsigned and cannot upgrade `gate-passed` to human acceptance.

In GitHub Actions, redirect stdout explicitly:

```yaml
- name: Render bounded evidence summary
  shell: bash
  run: ./evidence-report.sh --receipt "$RUNNER_TEMP/job.json" --format github-step-summary >> "${GITHUB_STEP_SUMMARY:?}"
```

Do not pass fork-controlled paths, repository content, tokens, or secrets to that
step. The reporter never discovers or writes `GITHUB_STEP_SUMMARY` itself and does
not comment, upload an artifact, or call a GitHub API.

The stdout-only path returns normally and supports pure in-process rendering.
The `--output` CLI path is deliberately process-owning: it retains signal rollback
authority through an atomic `os._exit(0)` boundary. Run file-output mode as the
documented command or a subprocess; do not call its `main(argv)` from a host process.

## Recovery and preservation decisions

| Observed state | Driver action |
|---|---|
| Candidate-free failure with `resume` available | Review the current state and use the exact SHA-approved same-conversation command only if the user authorizes it. |
| Candidate-free failure with `restart` available | Treat restart as a fresh provider attempt requiring its own explicit decision. |
| Provider `ERROR` with a bound result | Retrieve it, inspect the diff, run driver checks in a verification copy, then construct Verification v2. |
| Provider cancellation with a bound result | Preserve and review it, finalize honestly, or explicitly choose a fresh restart; never resume or continue it. |
| Verification finds a repairable gap | Bind the exact current candidate and request a same-conversation `continue` within the remaining attempt budget. |
| Strict policy is met | Finalize the exact current candidate with the driver-owned `verified` disposition; preservation and Git integration remain separate actions. |
| Useful candidate has unresolved evidence | Finalize `partially_verified`, or preserve it for later review without overstating the result. |
| Gate-rejected candidate | Preserve for diagnosis or use the exact rejected-only cleanup flow; do not silently discard it. |

Git integration is outside every controller and Receipt action. Review the actual
diff, then commit, push, open a pull request, merge, or delete a branch only with the
separate authority appropriate to that action.
