# Repository map

This is the human-maintained routing and intent map. Update it when entry points,
trust boundaries, ownership, or test-suite responsibility changes. Keep implementation
detail in source and durable rationale in `docs/lessons_learned.md`.

Use one relevant row to choose the canonical source and owning check; do not preload
this whole file. A fresh local Graphify index complements the map only for cross-file
relationships, paths, and impact analysis. Query it narrowly, then verify material
edges against the mapped source and tests. Generated `graphify-out/` data is ignored
local cache, not repository authority, and must not be copied into this map. The
ignore rule removes Git noise only; it does not exclude the cache from provider reads.
Keep the cache absent from the disposable worktree used for dispatch.

| Need | Start here | Add Graphify only when |
|---|---|---|
| Change one component | Its ownership row and owning suite below | Impact crosses unclear call/import boundaries |
| Understand lifecycle or trust | The relevant flow and trust-boundary bullets | A path between three or more components is unclear |
| Update public claims | The mapped source, then the exact README/docs section | Never; source and rendered copy are authoritative |
| Find historical rationale | One matching lesson heading via `rg` | Never; Graphify is structural, not decision history |

## Core delegation flow

```text
driver task
  -> model-recommendation.sh --stage pre-dispatch (visible advisory only)
  -> agy-worker.sh selector preflight
       -> legacy tier mapping; or portable exact-SHA/version/source-bound resolver
       -> every direct selection: bounded safe-target `agy --version` and strict
          critical `agy --help` probes; exact version match proceeds mechanically,
          while drift requires Codex disposition plus the exact raw-help SHA
       -> silent pre-task executable-record binding reprobe (no executable pathname
          is printed); HUP/INT/TERM closes the probe process group before task read
       -> owner-private selection.json; exact slug and matrix SHA frozen once
  -> agy-worker.sh / agy_dispatch.py (private staged prompt, one exact model,
       process-owning per-job controller, idle/hard/max clocks)
       -> every provider attempt: final version/help/executable binding reprobe
          immediately before provider launch
       -> direct-selection preflight failure: preserve local evidence only; no
          same-job resume/restart; Codex reviews sanitized interface evidence before
          creating a new job with the same caller-selected model and effort
  -> agy (untrusted worker; NDJSON is consumed incrementally)
  -> provider result (`SUCCESS`, `ERROR`, or `CANCELED`; a valid candidate may survive)
  -> provider schema (report-only `commands_run`/`tests_run` may be omitted)
  -> canonical envelope (both arrays restored and required; summary <= 8192)
  -> skills/agy-worker/runtime/scripts/validate-envelope.py (shape and contract only)
  -> one caller-chosen verification path:
       -> verify-job.sh (receipt-producing wrapper)
            -> sanitized, capability-bound launch with a pre-opened evidence FD
               owned only by the gate parent
            -> qa-gate.sh (Git scope, immutable base, policy, escalation)
                 -> isolated gate helpers
                 -> driver-owned argv/shell verifier children started only after the evidence
                    FD is closed in the existing gate process
            -> validated unsigned receipt, atomic no-overwrite local publication
            -> optional evidence-report.sh pure rendering of validated bounded fields
       -> or direct qa-gate.sh (same gate and verifiers, no receipt)
  -> model-recommendation.sh --stage post-gate (visible advisory only)
  -> human diff review and deliberate integration
```

The task, path policy, immutable base commit, verification commands, caller selector,
selection provenance, and routing evidence belong to the driver. A routing recommendation is display-only:
it does not alter the selected tier or participate in gate acceptance. The worker may
edit only the isolated worktree and may report claims, but its
commands never execute. Schema validation proves shape, not truth. The gate derives
Git-visible state independently, rejects undeclared, phantom, wrong-kind, outside-
policy, and verifier-created changes, and routes non-completed outcomes without
accepting them.

The optional local lifecycle wraps that same authority without adding autonomy:

```text
job.sh init (explicit repo/worktree/branch/full base/job ID -> private state v1)
  -> separately approved agy-worker.sh dispatch in the bound worktree
       -> `run` foreground or one explicit owner-private `start` controller
       -> local current-v10 `status|wait|result|extend|cancel|resume|restart` state only
  -> job.sh verify -> exact verify-job.sh / qa-gate.sh receipt path
  -> job.sh status (read-only facts and current approval hashes)
  -> gate-passed: preserve-instructions only, never execution
  -> receiptless terminal dispatch failure: exact dispatch binding + `job.sh abort`
       with fresh state/candidate approvals; empty or explicitly discarded candidate
  -> rejected exit 10-14 only: fresh triple-approved cleanup
       -> durable cleanup-in-progress
       -> exact registered worktree removal
       -> exact unchanged branch ref compare-and-delete
       -> private cleaned tombstone
```

The lifecycle cannot dispatch, accept, commit, publish, or clean routed/passed work.
Reconciliation never spends an approval for stale state bytes on a later destructive
step. Its deletion scan does not follow symlinks and rejects nested repositories,
initialized submodules, special nodes, device/mount changes, and unbound digest drift.
The dispatch controller is intentionally not a daemon: one explicitly started job
owns one local process group and private state. Its status and cancellation do not
assert remote/provider job state or remote cancellation; a local cancellation retains
`remote_cancel_unverified`. A candidate-free failure may be SHA-approved for exact
conversation `resume` or visibly fresh `restart`. A valid `ERROR` candidate (exit 25)
goes to `result`, driver Verification v2, then `continue`/`finalize`; it is never
resumed. A valid `CANCELED` candidate (exit 22) is preserved for `result` and
finalization or explicit fresh restart; it is never resumed or continued. No branch is
automatic. Only `init`, `step_update`, and terminal `result` update `last_activity` to
`provider_initialized`, `progress_signal`, or `terminal_received`; that activity is
nonsemantic and renews only the idle lease. Current v9 uses `dispatching` for an active
initial, resume, or restart attempt; `attempt-failed` for a pre-candidate failure;
`awaiting-verification` for a recognized candidate; `repairing` for an active
continuation; and `repair-failed` for an actual failed continuation attempt. Controller
terminal phases are `completed` or `blocked`; exact Codex driver decisions/dispositions
are `verified`, `partially_verified`, `rejected`, or `blocked`. Its additive candidate recognition/source/availability, driver
disposition, failure stage, `last_activity`, mechanically derived `available_actions`,
deprecated mechanical `next_action`/safe-current-SHA aliases,
and worktree-reconciliation fields are controller facts. `has_prior_candidate` is
deprecated and does not assert cleanliness. Reconciliation captures the pre-provider
baseline, the post-group-reap terminal candidate, and an exact recomputation before
queued `Popen`, `continue`, or `finalize`. Under controller-managed provider quiescence
it performs a bounded no-follow double-manifest comparison and binds the Git, index,
root, and selected-Git target facts; drift or unavailable evidence fails closed. This
is neither a filesystem snapshot/FSEvents monitor nor hostile same-user tamper
resistance; it proves no clean
worktree, review, or acceptance and makes no semantic recommendation. The local owner,
same-UID processes, and OS administrators remain the TCB; mutation after an entry's
final read is the portable residual. V1 is read-only. A V3/V4 current result requires
both state SHA and a status-time `migration_binding_sha256`, rebound under the first
transition lock; `last_success_*`-only evidence remains read-only. V5/V6 retains its exact legacy digest, V7 retains its
exact semantic-v1 digest, and V8 retains its explicit semantic-v1 algorithm. A V5/V6
transition proves its legacy state and atomically captures a fresh semantic V9
baseline/candidate; V7/V8 reuse an exact semantic proof. V9's separate no-follow
root/Git boundary identity excludes mutable worktree/index/HEAD/ref/object content.
`status`, `wait`, `result`, `resume`, `restart`,
`continue`, and `finalize` default to public JSON and accept three sanitized
driver-owned text lines. Every emitted action or stale-approval rerun command uses the
caller-resolved symbolic launcher `"$PIPELINE/agy-worker.sh"`. `result` returns only a
bound canonical candidate and remains separate from provider or driver acceptance.
Every explicit
`explore`/`task`/`project` workflow consumes candidate-SHA-bound Verification v2 for
`continue`/`finalize`, never a worker command. For `verified`, explore needs complete
coverage, zero unresolved gaps, zero failed checks, and zero missing checks;
task/project need at least one pass, zero failed/missing checks, and completed diff review. Plan mode stages the full prompt
privately and relies on upstream plan transformation plus the disposable
worktree/no-change gate, not mode as a filesystem isolation guarantee.

The provider-independent benchmark reuses the Receipt authority without joining the
delegation or routing path:

```text
benchmark.sh prepare (clean commit OR reviewed portable source manifest
                      + exact tool/schema/fixture/selection bindings)
  -> immutable external private plan.v1.json
  -> benchmark.sh run (one checked-in synthetic attempt per ordered pair)
       -> exact verify-job.sh / qa-gate.sh
       -> one Receipt v1 per pair
       -> immutable unsigned result.v1.json binding receipt bytes and gate facts
  -> benchmark.sh report (pure manifest-order completeness facts only)
```

It cannot call agy or a provider, synthesize a verdict, rank, score, retry, fall back,
route, recommend, or choose a winner. Live execution and persona evidence are outside
v1.

Direct persona selection stays deliberately narrow:

```text
direct --persona NAME
  -> fixed runtime/agents/<name>.md prompt text
  -> mode restriction enforced by agy-worker.sh
  -> prompt guidance only; no routing, verification, or acceptance authority
```

The repository-only starter proof has a deliberately smaller flow:

```text
canonical synthetic fixtures
  -> proof-demo.sh
  -> two independent private temporary Git repositories
  -> fixed repository qa-gate.sh
  -> exact honest exit 0 and mismatch exit 10
  -> cleanup, then bounded three-line summary
```

It demonstrates only those maintained gate outcomes. It does not dispatch a worker,
accept a candidate, replace human diff review, or certify correctness or security.

The public conformance kit expands that teaching subset without adding acceptance
authority:

```text
conformance/run.sh --gate PATH
  -> strict SHA-pinned conformance/v1 manifest and static sources
  -> eleven independent private disposable Git repositories
  -> supplied gate entry point under fixed time/output/process-group bounds
  -> exact expected gate exits 0, 10, 11, 12, 13, 14, 15, and 64
  -> cleanup, then one bounded fixture-compatibility result
```

The supplied gate is user-approved executable code, not sandboxed content. A passing
result means only that entry point matched the public fixtures; it is not security,
real-job, Receipt v1, report, lifecycle, or worker-quality certification. The gate,
loaded code, local owner/same-UID processes, and OS administrators form the cleanup
TCB. Cleanup is bounded, no-follow, and descriptor-relative only while exact original
parent/root identities remain; final pathname removal trusts that TCB. Drift fails
closed with a possible residual, never a parent scan or moved-directory chase, and
does not establish same-user tamper resistance.

## Opt-in maintenance flows

- `update.sh check` queries fixed official agy and Codex stable-release/source
  evidence, one bounded agy `darwin_arm64` distribution-manifest canary, per-tool
  review age, the active agy matrix binding, and (locally) installed versions without
  changing files. A disabled or stale matrix is drift-review; malformed or missing
  matrix evidence is inconclusive. The manifest helper requests no archive and its
  observational tuple cannot advance a baseline.
  GitHub release/source observations use exact fixed REST paths through a proxyless,
  redirect-rejecting strict JSON client and a bounded process-group supervisor.
  Installed version probes use the same supervisor with smaller limits. Check/watch
  makes no Git network request, so ambient Git URL rewrites and transport helpers are
  outside its evidence path.
  Its `--watch` mode needs no installed tools and preserves the same
  unchanged/drift-review/evidence-unavailable exit contract. `update.sh apply [TAG]`
  remains explicit: it verifies a
  stable tag and fast-forward, protects ignored-path collisions, runs the candidate
  suites and install preflight in a temporary worktree, then fast-forwards and
  reinstalls the skill. Candidate scripts still execute with user privileges; the
  temporary worktree is not a security sandbox.
- `bug-report.sh draft` creates a private sanitized bug or improvement draft.
  `preview` prints the exact body and SHA-256. `submit` requires that hash and sends
  the already validated bytes to the explicitly bound GitHub destination. Nothing
  submits automatically; security-sensitive drafts are private-route only.
- `feedback-triage.sh summarize` renders only supplied bounded metadata and `fetch`
  reads one fixed public GitHub issue-metadata page. Both reject or discard raw issue
  content and emit a canonical aggregate only; neither writes nor feeds GitHub text to
  an agent. Fetch cannot classify or deduplicate because it never reads those fields.
- `skills/agy-worker/` is the canonical Codex Agent Skill and owns the complete core runtime.
  A skill-folder-only copy resolves `runtime/` without the repository or a network
  fetch. Repository-root commands are compatibility wrappers; `install.sh` copies the
  same bundle and adds a local `.pipeline-root` marker so checkout maintenance remains
  available. `.codex-plugin/plugin.json` and `.agents/plugins/marketplace.json`
  describe that same root package: the marketplace source is exactly `.` and may not
  introduce a copied `plugins/` skill or runtime. `docs/MARKETPLACE.md` records the
  local contract; adding it to Codex remains separately approved. The tested public
  installation paths are the Git-backed Codex marketplace and GitHub clone plus
  explicit install; neither installation authorizes provider dispatch or repository
  transmission. `SKILL.md` declares Codex-only host compatibility and links its
  package-owned progressive-disclosure guides: `README.md` owns standalone package
  orientation, `PROJECT_LIFECYCLE_AND_VERIFICATION.md` owns lifecycle and Verification
  v2 procedures, `SECURITY_AND_COMPATIBILITY.md` owns provider/verifier boundaries,
  and `TROUBLESHOOTING.md` owns actionable failure recovery. These guides require no
  repository-root prose or decorative assets. Provider model names do not expand host
  support to Claude or Claude Code.
- `doctor.sh` delegates to the canonical bundled doctor. It reads only its fixed
  portable agy version/source/date metadata plus bounded semantic tool/repository
  probes, emits no paths or raw command output, and neither dispatches nor repairs.
  The source check exposes only match/mismatch/unavailable against its fixed reviewed
  revision. It ignores caller temp paths, isolates child probes in one private external
  workspace, and forwards HUP/INT/TERM to the active process group. Runtime parent
  directories are real, bundle-contained package components rather than followed
  symlinks. The root `compat/` records stay canonical; packaging tests require
  byte-identical copies.

## Ownership and test coverage

| Path | Responsibility | Owning offline suite |
|---|---|---|
| `agy-worker.sh`, `skills/agy-worker/runtime/agy-worker.sh`, `skills/agy-worker/runtime/scripts/agy_dispatch.py`, `skills/agy-worker/runtime/scripts/agy_dispatch_worktree.py`, `scripts/transmission_preview.py`, `skills/agy-worker/runtime/scripts/transmission_preview.py` | Root compatibility entry point plus canonical runtime entry point; explicitly mirrored helpers remain byte-synchronized. Caller-owned selection, optional personas, workflow resolution (`explore`/`task`: 1..2, default 2; `project`: 1..5, default 5), private prompt/log staging, a closed provider/probe environment baseline with exact-name `--provider-env` opt-ins bound into command state, deterministic external state root derivation under `XDG_STATE_HOME`/`HOME` when unset, prospective and post-resolution fail-closed rejection of project roots inside the target worktree before prompt staging or recovery dispatch, process-owning progress-aware dispatch, and a path-bound sibling engine for bounded no-follow Git/worktree observations. Provider-free `transmission-preview` runs before prompt, selection, provider, state, log, stdin, or network work and exposes a reusable schema-v1 path/kind manifest over two complete content-free scans of a canonical branch-backed linked worktree. Fixed bounded `/usr/bin/git worktree list` plumbing verifies real registration without hooks, prompts, provider, or network; streamed directory enumeration applies count/time bounds before sorting. It excludes the root `.git` marker, lists contained symlink aliases without targets, rejects drift, escapes, special nodes, and limits, and is review evidence rather than approval or provider-launch binding. Current V11/command V8 supports an explicit transmission choice and a closed read/write file-or-tree provider scope whose policy, readable manifest, selected-content manifest, and approval are bound by one transmission SHA. Its advanced one-cycle Boost task profile additionally binds a job-specific authority warning, requires `accept-edits`, no persona, slash protection, and provider init `agent=Boost` plus `permission_mode=request-review`, and disables resume/restart/continue without widening transmission or permissions. Each attempt uses a fresh external owner-private mode-0700 Git-less stage as provider cwd. Descriptor-relative no-follow copies reject aliases, hardlinks, special nodes, Git administration, and casefold/NFC/NFD collisions under count, byte, depth, and deadline bounds. Recognized success, error, and cancelled reports preserve and transactionally reconcile only authorized stage mutations after source rebind; durable backups, fsync, an atomic recovery ledger, prior/post identities, post-reconcile equality, and exact rollback fail closed on drift or uncertainty. Initial launch has no implicit transmission mode: provider scope is recommended, while whole-worktree dispatch remains a manifest-bound exception rechecked immediately before the initial provider process. V6/V7 jobs and already-queued states load compatibly, but unapproved legacy broad records cannot launch through dispatcher run/start; existing jobs may upgrade structurally but never acquire narrow scope or Boost authority. Current V11 candidate/lifecycle writes preserve all V9 explicit semantic-v1 candidate snapshots plus stable root/Git boundary identity and sanitized `provider_terminal_status` (`unknown`, `success`, `error`, `cancelled`) without exposing provider status in public driver disposition or altering action thresholds. Verification v2 rebinds result/schema/root/candidate before and after a no-follow isolated verification copy. It preserves regular bytes/executable bits, rebases every contained symlink inside the copy, and rejects broken/outward/Git-admin links (no `.git`) so writable driver checks do not reconcile ignored drift into the candidate. Partial/promisor clones fail synchronously with a fixed sanitized full-clone diagnostic before queued state or provider launch. A valid, non-empty REUC observation immediately before provider launch yields the bounded public reason `resolve_undo_present`, existing exit code 20, and `failure_stage=binding_failure` without provider launch or clearing index metadata; malformed, duplicate, or racing observations remain generic `status_unavailable`. Wrapper parse errors remain `64`; post-parse copy runtime/binding/destination failures map to `20`. The bounded owner-controlled copies, filtered environment, and optional stage are not filesystem, network, `PATH`, `HOME`, or same-UID isolation. V1 remains read-only; V3/V4 require first-transition SHA+rebound-migration approval and do not advertise the non-migrating copy helper. A preserved current V11 job already inside its worktree keeps exact command/schema/root/result readback and driver-only non-verified finalization, while verified finalization, verification-copy, and provider continuation/restart remain unavailable. Other current finalization retains artifact/schema/root/worktree rebinding; preserved provider-error/cancelled candidates, queued SHA+entry rebinding, and exact quota-terminal mapping remain unchanged. Provider-scope approval grants neither provider execution, Git action, driver acceptance, nor publication. | `tests/test-agy-worker.sh` (291 cases); `tests/test-agy-worker-remediation.py` (103 focused cases); its non-discoverable case modules are loaded by that canonical suite |
| `model-selection.sh`, `skills/agy-worker/runtime/model-selection.sh`, `skills/agy-worker/runtime/scripts/model_selection.py`, portable matrix/schema/SHA | Root compatibility entry plus exact matrix-bound model/effort resolution, CLI-only version-independent literal records, bounded safe-target semantic version plus structural critical-help preflight, automatic V2 mechanical launch authority for an exact version match, and explicit Codex disposition plus exact raw-help SHA for drift. V3 records bind that drift decision, capabilities/matrix/selection facts, and a bounded no-follow descriptor digest plus complete safe executable path authority (only the macOS `/var` alias is normalized); controller help prose is data, never availability inference. Codex inspects current bounded raw help before every reviewed direct dispatch and owns the semantic stop decision, including for an exact-version match. Literal version observation remains non-gating and selection provenance is driver-owned. | dispatcher, doctor, and packaging suites |
| `model-recommendation.sh`, `skills/agy-worker/runtime/model-recommendation.sh`, `skills/agy-worker/runtime/scripts/model-recommendation.py` | Root compatibility entry plus side-effect-free pre/post recommendations; direct selections are labelled but unranked and never applied | `tests/test-agy-worker.sh` (284 cases) |
| `model-intelligence.sh`, `skills/agy-worker/runtime/model-intelligence.sh`, `skills/agy-worker/runtime/scripts/model_intelligence.py`, `skills/agy-worker/runtime/compat/model-intelligence/dataset.v1.json`, schemas | Root compatibility entry plus offline Model Intelligence v1 validation, benchmark review tracking on supported model inventory/binding changes or dataset expiry with explicit maintainer disposition, and deterministic Pareto advisory calculation across quality, latency, token, and cost dimensions; distinct provenance types (vendor, independent, local), freshness/expiry, requested vs observed models, comparability boundaries (accounting, tokenizer, cost basis), and zero dispatch/git/model-change authority. | `tests/test-model-intelligence.py` plus doctor, resolver, packaging, and CI sharding/timing suites |
| `model-evidence-campaign.sh`, `skills/agy-worker/runtime/model-evidence-campaign.sh`, `skills/agy-worker/runtime/scripts/model_evidence_campaign.py`, schemas | Root compatibility entry plus a provider-independent offline incremental new-model evidence campaign contract: three mutually exclusive evidence lanes (`vendor_declared`, `measured`, `observational`); exact review/current inventory, matrix, dataset, and anchor binding; caller-owned record validation without provider or evaluator orchestration; pure deterministic evaluation with bounded reason codes; measured-only exact-recomputed owner-private materialization; privacy-bounded complete-cohort advisory preview and approved-SHA export that deterministically rebinds the evaluation; and opt-in local-only coarse aggregate status, preview, and export where only valid record-digest-bound evaluations count as reviewed. Advisory output omits raw identifiers/evidence and aggregate schema v2 adds workflow-category, requested-versus-observed, and identity-mismatch counts while preserving legacy v1 validation. It has no network, provider, dispatch, routing, or Git authority. | `tests/test-model-evidence-campaign.py` (27 cases) plus doctor, resolver, packaging, and CI sharding/timing suites |
| `delegation-policy.sh`, `skills/agy-worker/runtime/delegation-policy.sh`, `skills/agy-worker/runtime/scripts/delegation_policy.py`, `skills/agy-worker/runtime/schemas/delegation-policy.schema.json` | Root compatibility entry plus closed evaluator for explicit opt-in delegation-first coordinator policy; assigns AGY as first substantive repository actor after discovery/worktree/verification setup; fails closed on missing approvals, hard stops, preflight failures, or budget exhaustion without silent fallback to Codex. | `tests/test-delegation-policy.py` plus doctor, resolver, packaging, and CI sharding/timing suites |
| `workflow.sh`, `skills/agy-worker/runtime/workflow.sh`, `skills/agy-worker/runtime/scripts/workflow.py`, `skills/agy-worker/runtime/schemas/workflow-state.schema.json` | Root compatibility entry plus canonical thin workflow facade (`run`, `status`, `verify-finalize`) over existing job lifecycle, dispatch, and verification authorities. Ordinary run accepts an absolute repo/job ID, binds omitted base to `HEAD` once, derives deterministic owner-private state plus an isolated lifecycle-owned branch/worktree under safe XDG/HOME state, and retains preview resources for the approved second call. Launch requires either the exact whole-worktree manifest approval or an exact provider-scope policy plus selected-content transmission digest; the scoped path delegates to the canonical staging boundary without `--add-dir`. The all-explicit state/worktree/branch/base tuple remains advanced compatibility. Status projects facade, existing job-lifecycle, or dispatcher sources read-only without migration; verification remains driver-owned. Same-invocation pre-dispatch rollback delegates exact clean facade-created deletion to the lifecycle and refuses drift or dispatch evidence. | `tests/test-workflow.py` (20 cases) plus doctor, resolver, packaging, and CI sharding/timing suites |
| `doctor.sh`, `skills/agy-worker/runtime/doctor.sh`, `skills/agy-worker/runtime/scripts/doctor-metadata.py`, `skills/agy-worker/runtime/compat/` | Root compatibility entry plus deterministic offline prerequisite checks and byte-synchronized portable agy metadata | `tests/test-doctor.sh` (207 cases) plus packaging synchronization checks |
| `install.sh`, `skills/agy-worker/README.md`, `skills/agy-worker/SKILL.md`, `skills/agy-worker/agents/openai.yaml`, `skills/agy-worker/references/`, `skills/agy-worker/scripts/resolve-pipeline.sh` | Install and resolve complete-plugin, explicit-checkout, or folder-only skill layouts without fetching code. `SKILL.md` stays the concise progressive-disclosure router and preserves mandatory user-facing provider dispatch notices across initial, resume, continue, and restart launches, authority/privacy stops, workflow choice, and independent plan governance; the standalone README and references own package orientation, detailed lifecycle/Verification v2, security/compatibility, and actionable troubleshooting. Package metadata states its truthful use case. All internal package links resolve without repository-root files, and decorative assets are optional rather than a completeness requirement. | `tests/test-packaging.sh` (494 cases), including package-document presence, link, metadata, standalone-completeness, and no-required-asset guards |
| `skills/agy-worker/runtime/schemas/`, `skills/agy-worker/runtime/scripts/validate-envelope.py` | Dependency-free envelope contract validation | dispatcher and gate suites |
| `qa-gate.sh`, `skills/agy-worker/runtime/qa-gate.sh` | Root compatibility entry plus canonical immutable-base Git audit, bounded envelope intake, path policy, escalation, ordered no-shell canonical argv verification, explicitly acknowledged shell compatibility, a default verifier baseline without `HOME`, separately acknowledged credential-name opt-ins delivered only through a private descriptor, sanitized label/mode diagnostics, and internal pre-opened structured evidence handoff | `tests/test-qa-gate.sh` (62 cases) plus receipt suite no-FD compatibility checks |
| `verify-job.sh`, `skills/agy-worker/runtime/verify-job.sh`, `skills/agy-worker/runtime/scripts/evidence_receipt.py`, `skills/agy-worker/runtime/schemas/evidence-receipt.schema.json` | Root compatibility entry plus exact input hashing, strict selection/advisory binding, startup-isolated parent-exclusive gate evidence, domain-separated canonical verifier-spec hashes, mode/acknowledgement/ordinary-and-credential environment-name policy hashing, private value handoff without value persistence, interruption cleanup, structurally compatible unsigned Receipt v1 validation, and private durable no-overwrite publication | `tests/test-evidence-receipt.sh` (99 cases) |
| `evidence-report.sh`, `skills/agy-worker/runtime/evidence-report.sh`, `skills/agy-worker/runtime/scripts/evidence_report.py`, `skills/agy-worker/runtime/scripts/recommendation_record.py` | Root compatibility entry plus pure Receipt v1 validation, deterministic bounded text/canonical-JSON/Markdown/GitHub-Step-Summary rendering, final workflow-command and Markdown safety checks, separately trusted binding checks, privacy filtering, and optional mode-0600 no-overwrite publication; the renderer never discovers the GitHub summary environment path; stdout-only `main(argv)` returns, while file-output `main(argv)` is process-owning through `os._exit(0)` and must run as a command/subprocess; never dispatches, routes, gates, uploads, or changes a verdict | `tests/test-evidence-report.sh` (80 cases), receipt back-compat, and packaging checks |
| `job.sh`, `skills/agy-worker/runtime/job.sh`, `skills/agy-worker/runtime/scripts/job_lifecycle.py`, `skills/agy-worker/runtime/scripts/candidate_state.py`, `skills/agy-worker/runtime/schemas/job-state.schema.json` | Root compatibility entry plus process-owning lifecycle CLI, external private state, canonical branch-backed worktree init/status, fixed sanitized Git execution with incrementally bounded stdout and no checkout hook/filter authority, exact Receipt delegation/binding, read-only preserve instructions, interrupted-progress reconciliation, receipt-bound rejected-only cleanup, and separately bound pre-gate abort. Optional facade-created schema-v2 provenance leaves legacy v1 unchanged; lifecycle-owned `rollback-ready` deletes only an exact ready, empty, drift-free pre-dispatch facade resource set with current approvals and absent dispatch evidence. The shared candidate helper is also the gate's sole digest implementation. | `tests/test-job-lifecycle.py` (132 cases), gate parity, doctor, and packaging suites |
| `benchmark.sh`, `benchmarks/v1/`, `docs/BENCHMARKING.md`, `skills/agy-worker/runtime/benchmark.sh`, `skills/agy-worker/runtime/scripts/benchmark.py`, benchmark schemas and portable assets | Root compatibility entry plus explicit clean-commit or reviewed portable-source-manifest authority, structurally complete v1 schemas, immutable offline plan, exact one synthetic attempt per ordered caller variant/task, canonical Receipt v1 delegation, unsigned result binding, and pure manifest-order completeness report; no agy, provider, ranking, route, recommendation, retry, or live mode | `tests/test-benchmark.py` (104 cases), doctor, resolver, and packaging suites |
| `swebench-workflow-study.sh`, `skills/agy-worker/runtime/swebench-workflow-study.sh`, `skills/agy-worker/runtime/scripts/swebench_workflow_study.py`, `skills/agy-worker/runtime/schemas/swebench-workflow-study-*` | Root compatibility entry plus exact offline `prepare -> import -> report -> advise` study: checkout-or-bundle-root exclusion, caller-private owner-0600 inputs, flat 0600 no-overwrite artifacts, closed public nested plan/record/cell/telemetry schemas, closed budgets/records, stable rejection categories, all-cell acceptance and denominator derivation, separate provider telemetry bindings and explicit availability, full-chain hash revalidation, and strict Pareto advice that derives combined metrics only for structurally comparable token/cost bindings and keeps all authority fields false; no execution, provider, evaluator, retry, routing, or authority. | `tests/test-swebench-workflow-study.py` (58 cases) plus doctor, resolver, CI-sharding/timing, and packaging suites |
| `skills/agy-worker/runtime/agents/*.md` | Prompt-injected optional personas. Direct `--persona` selection and explicit mode restrictions remain enforced by `agy-worker.sh`; prompt text grants no routing, verification, or acceptance authority. | dispatcher suite and packaging checks |
| `proof-demo.sh`, `conformance/v1/envelopes/honest.json`, conformance content sources | Repository-only offline starter proof using the two-state teaching subset of the public versioned contract | `tests/test-proof-demo.sh` (21 cases) |
| `conformance/run.sh`, `conformance/v1/`, `docs/CONFORMANCE.md` | Repository-only public qa-gate v1 fixture contract, strict manifest/source binding, private normally disposable repositories, bounded supplied-gate execution, FD-relative no-follow cleanup under an explicit same-UID TCB, fail-closed residual policy, and non-certification claim | `tests/test-conformance.py` (83 cases) plus packaging policy checks |
| `update.sh`, `ground-truth.sh`, `scripts/compatibility.py`, `scripts/compatibility_probe.py`, `scripts/agy_inventory.py`, `scripts/official_github.py`, `scripts/official_distribution.py`, `compat/` | Explicit project releases; exact fixed-REST agy/Codex observation; compact exact stable-tag-ref commit binding including bounded annotated project tags; separately bounded release/ref documents; bounded process-group/version probes; safe agy version/help ground-truth phase plus an explicit account-state phase; exact-line allowlisted agy inventory interpretation; sanitized reconciliation records; bounded distribution-manifest canary; active agy `1.1.24` exact version/release/inventory/digest-bound 14-slug model matrix. The one authorized 1.1.24 capture and separate reconciliation activate the reviewed inventory, including the Gemini 3.8 Flash low/medium/high compound slugs; 3.5 Flash is not a current mapping. The activation path requires the digest-bound version manifest and rejects a missing or drifting manifest. Gemini 3.1 Pro medium remains outside this wrapper's reviewed compound-slug route because the accepted account inventory has no `gemini-3.1-pro-medium` slug. Codex `0.150.1` is an observational compatibility baseline with no agy dispatch, model, routing, or worker-backend authority. Explicit apply-time Git fetch remains ambient-configuration-aware. | `tests/test-version-manifest-engine.py` generic policy coverage, `tests/test-agy-1-1-22-activation.py` (25 active cases), the fixed 1.1.22 capture suites, `tests/test-update.sh` (325 cases, including fixed transport, supervisor, inventory, and daily-watch policy harnesses), `tests/test-official-github.py` (65 cases), plus packaging ground-truth phase coverage |
| `update-notifier.sh`, `scripts/update_notifier.py`, `scripts/update_notifier_child.py` | Optional macOS daily LaunchAgent over a hash-bound snapshot of the read-only watcher. Canonical account HOME, closed transitive source manifest, serialized lifecycle, launchctl reconciliation, parent-death acknowledgement, process-owned signals, drift-fingerprint deduplication, and resumable uninstall; no apply/provider/baseline authority. A valid installed record whose live source bytes changed is `maintenance-required`: one sanitized maintenance notification pauses ordinary watch results until the owner explicitly runs `refresh`, which performs the existing serialized uninstall/install rebind rather than silently adopting code. Refresh alone also recognizes the exact immediately-prior v0.8.0 18-file ledger, uses its historical bindings and authenticated uninstall authority, and creates a fresh current 21-file install; arbitrary legacy shapes and all other commands remain strict. | `tests/test-update-notifier.py` (89 offline fake-control cases) |
| `scripts/adoption_measurement.py`, `docs/MEASUREMENT.md` | Explicit owner-private canonical ledger and fixed 30/60/90 aggregate reports. Closed metrics, public evidence URL allowlist, opaque observations, bounded locked append, rolling age-out, no discovery/network/HOME/telemetry, and no activation authority. | `tests/test-adoption-measurement.py` (41 offline cases) |
| `scripts/version_attestation_runner.py` | Canonical fixed-profile snapshot-backed `--version` attestation; fixed `/usr/bin/python3 -I -S -B` launch under an explicit trusted Apple interpreter/host/local-owner/OS-admin boundary; exact family, component, family-specific alias kind, alias/target identity, executable/no-setid, and no-world-writable-directory/resolved-executable checks; bounded UID/GID/mode diagnostics; one exact Popen; bounded streams/pre-reap group cleanup; and private durable binding. Production owns nonthrowing signal observation through flushed output and a fixed-priority completion snapshot before `os._exit`; ignored/caller-blocked signals are excluded, and embedded restoration is an explicit caller handoff. Synthetic-only self-test. It does not prove binary provenance, code signing, host attestation, or same-user/hostile-PR tamper resistance. Production execution remains a separate explicit action. | `tests/test-version-attestation-runner.py` (165 cases) |
| `scripts/version_bootstrap_runner.py` | Separate closed-profile, repository-only bootstrap from one retained accepted recovery binding. It makes descriptor-held copies and one bounded snapshot-backed version observation, ledgers every created inode at creation, represents transient staging/final hard links as one exact `nlink=2` inode, normalizes to the final `nlink=1` identity before durability hooks or polls, compare-deletes only exact owned identities, and publishes only after exact empty mode-`0700` scratch revalidation and process-group closure. The production CLI is process-owning: non-throwing handlers accumulate signals, checkpoints choose fixed HUP/INT/TERM priority, large userspace chunks are at most 1 MiB, and signals remain unblocked through copies, provisional publication, validation, durability, and the flushed success line. Only then does one blocked pending snapshot linearize completion before `os._exit(0)` without a Python restore/unblock/release path; kernel syscalls themselves are not time-bounded by chunk polling. The separate embedded test API restores caller state and hands post-snapshot signals back to the caller. Production and tests require the selected CPython 3.9 `/usr/bin/python3 -I -S -B`; exact runtime and flag preflight precedes source parsing and mutation so the pinned AST stays interpreter-specific. The outer `snapshot-version-bootstrap` evidence is not capture input; its nested `snapshot-version-only` profile remains compatible with the unchanged recovery validator. Its exact reachable-call-graph/Popen guard detects reviewed-source drift under the reviewed-source/interpreter/local-owner/same-UID/OS-admin TCB, not coordinated hostile-source edits. It cannot enumerate account contents, run models/login/retry, use Git/network, route, or advance metadata; path or identity drift leaves a bounded private residual without scanning or chasing. | `tests/test-version-bootstrap-runner.py` (139 synthetic cases) |
| `scripts/version_initial_bootstrap_runner.py` | Separate repository-only current-source bridge. Its closed profile binds only a fresh owner-private root, source path/full identity, fixed current SHA, and its own exact `1.1.12` stdout authority; it has no account-HOME authority and never opens or enumerates HOME. It opens the source twice, copies each held descriptor independently into mode-`0755` source and mode-`0500` snapshot files, runs exactly one bounded snapshot-backed `--version` child in empty private scratch, and publishes a `snapshot-version-only` profile structurally accepted by the version-agnostic prior validator. The profile remains historical evidence: the manifest grants 1.1.12 no executable operation, and the retired fixed recovery/capture algorithms are absent. Its source AST and sole Popen site are pinned separately; inherited ignored/caller-blocked signals are not owned and choose fixed HUP/INT/TERM priority rather than chronology. Regular-file identities retain exact links and timestamps while owned directory identity tolerates only its child-count link change. It persists canonical initial/recovery profiles, and cannot read historical recovery evidence, run models/login, use provider/network/Git, route/retry, or advance metadata. Drift rejects with a bounded owner-private residual; any real call remains separately authorized. | `tests/test-version-initial-bootstrap-runner.py` (43 synthetic cases), plus manifest fail-closed coverage |
| `scripts/version_attestation_harness.py` | Persistent provider-independent mutation harness for owner-private no-overwrite publication, one bounded synthetic controller supervisor, exact process-group cleanup, lifecycle-signal linearization, fixed copy-based weakened controls, and exact byte/SHA binding to the canonical runner before import. Atomic completion is process-owning by default; only an explicit test handoff may restore/return, and its pending-signal choice is fixed priority rather than chronology. It never invokes agy or reads compatibility evidence. | `tests/test-version-attestation-harness.py` (60 cases) |
| `scripts/models_attestation_runner.py`, `scripts/agy_inventory.py` | Separate canonical fixed-profile snapshot-backed `models` inventory mechanism bound to an accepted version binding and the same attested executable; one exact Popen, private empty HOME/TMP/XDG and closed environment, 25-second/64-KiB bounds, exact 14-line allowlist semantics, private durable raw/source/binding publication, and synthetic-only self-test. Its version-runner bytes are pinned, and production owns the signal/output/completion boundary through immediate `os._exit`; embedded use restores explicitly. Auth-required output rejects without completion or metadata advance. The accepted version binding is version-only; this runner never inherits credentials, serves as a live-account capture path, advances metadata, proves a provider backend, applies a selector, or exposes model/effort flags. A future real-account capture needs a separate reviewed runner and authorization. Source-contract mutations are selected drift checks under the reviewed-source/local-owner TCB, not hostile-source or same-UID tamper resistance. | `tests/test-models-attestation-runner.py` (116 cases), plus updater parser controls |
| `scripts/models_capture_runner.py` | Separate capture-only fixed-profile mechanism for one future explicitly authorized real-account `models` observation, pinned to the reviewed models-runner bytes. Canonical stdin binds the exact owner-private account HOME identity, reviewed version binding and retained snapshot; the child receives that HOME plus capture-owned private TMP/XDG/cwd and a fixed environment. Nofollow component/identity revalidation, exact empty post-child scratch, 25-second/64-KiB bounds, pre-reap process-group closure, and process-owned mode-0600 no-overwrite publication make the final `models.capture.sha256` marker provisional until flushed bounded success output and the fixed-priority signal snapshot, followed by `os._exit`. Any bounded exit-zero stdout/stderr is captured without inventory or error interpretation. It independently rejects equality or either containment direction between account HOME and the source or snapshot, so a hand-authored canonical profile cannot bypass the process-inert builder. It does not inspect HOME contents, accept inventory, advance metadata, route, retry, log in, or prompt; tests make no real-account call. The external CLI may mutate/cache in HOME and the runner cannot detect or revert it; account residuals can remain after rejection. The account HOME/local owner/same-UID processes/reviewed source and interpreter/OS admins are trusted; no same-user tamper resistance is claimed. | `tests/test-models-capture-runner.py` (84 fake-account cases) |
| `scripts/models_capture_profile.py` | Process-inert preparation/validation of the runner's exact ten-field canonical profile. It accepts only `--prepare` or `--validate` bounded stdin, traverses explicit account/source/snapshot/version/output authorities with no-follow descriptors, binds existing version evidence and external snapshot identity without requiring snapshot co-location, and creates one mode-0600 no-overwrite profile through fsynced hard-link publication with bounded rollback. Its production CLI owns the signal/output boundary and keeps the profile provisional through the flushed result and fixed-priority completion snapshot before `os._exit`; the builder remains process-inert and has no Popen authority. It never imports process-capable runners, enumerates HOME, inherits environment, launches a child, uses Git/network/provider authority, or authorizes capture. The local owner/same-UID processes/OS/interpreter remain its TCB; source-contract tests are selected reviewed-source drift controls, not coordinated hostile-source resistance or provenance proof. | `tests/test-models-capture-profile.py` (121 synthetic cases) |
| `scripts/version_manifest_version_evidence.py`, `scripts/version_manifest_capture_profile.py`, `scripts/version_manifest_capture_runner.py`, `scripts/version_manifest_capture_classifier.py`, `scripts/version_manifest_reprofile.py`; thin `scripts/models_capture_1_1_22_*.py` adapters | Shared production implementations for version evidence, process-inert profile preparation, bounded capture, sidecar failure classification, and nlink-only reprofile. Runtime version and manifest path select closed digest-bound constants without changing the algorithms; the stable 1.1.22 filenames are thin CLI adapters. Their existing authority limits remain: version evidence owns one private `--version` child; profile and reprofile launch no child or enumerate HOME; capture owns one no-retry process group and private bounded artifacts; classifier emits only a sanitized mode-0600 record and grants no activation. A version row may require a macOS kernel-reported read-only mount for a disposable capture snapshot (for example, an owner-prepared UDRO image); digest and descriptor/path evidence plus the mount flag are rebound before and after the child. This blocks the observed in-place update route, but not a re-exec from a writable location; same-UID or administrator tampering remains outside the guarantee. | Fixed 1.1.22 suites: version evidence 45, profile 30, runner 63, classifier 24, reprofile 88 offline cases |
| `scripts/version_manifest_engine.py`, `scripts/version_copy_guard.py`, `compat/agy-version-manifest.json`, `compat/version-manifest.schema.json`; historical records under `compat/reviews/` | Digest-bound closed-schema current/legacy/previous/historical support tiers shared by the five production operations and active compatibility validation. Current 1.1.24 permits all five operations plus activation; legacy 1.1.22 preserves its fixed adapters without activation authority; previous 1.1.16 permits only generic version-evidence/profile/capture; historical 1.1.12 permits no executable operation. Capture policy is explicit per version (`stable` by default or macOS read-only mount) and does not authorize a provider call. The retired 1.1.12/1.1.16 version-stamped algorithms are absent while their evidence and review records remain unchanged. Activation requires the manifest and fails closed when it is absent; the copy guard permits only the five stable 1.1.22 adapters and rejects new version-stamped algorithm copies even if a manifest row exists. A manifest row is configuration, not live activation authority: separate reviewed evidence and canary are still required. | `tests/test-version-manifest-engine.py` (24 offline cases), `tests/test-agy-1-1-22-activation.py` (25 active cases), fixed 1.1.22 suites, and packaging synchronization checks |
| `codex-usage-report.sh`, `skills/agy-worker/runtime/codex-usage-report.sh`, `scripts/codex_usage_report.py`, `skills/agy-worker/runtime/scripts/codex_usage_report.py` | Root compatibility entry plus privacy-safe, version-pinned Codex CLI 0.150.1 usage observation; exact generated-schema digest preflight; live bounded JSONL stdio app-server protocol with independently drained streams and process-group cleanup; thread-bound response validation; separate cumulative and latest-phase reporting of input, cached input, net-new input, cache-write, output, and reasoning (subset); explicit owner-private session-file parsing; strict redaction of cwd, prompts, messages, raw logs, thread IDs, account IDs, and paths; estimated credits as provider estimates without inferring money/quota. | `tests/test-codex-usage-report.py` |
| `bug-report.sh`, `scripts/bug-report.py`, `.github/ISSUE_TEMPLATE/` | Local privacy filtering, exact double confirmation for public bug/improvement submission, explicit private-only security drafts, fixed-destination issue submission, and conservative non-proof keyword barrier | `tests/test-reporting.sh` (47 cases) |
| `feedback-triage.sh`, `scripts/feedback-triage.py`, `.github/workflows/feedback-watch.yml` | Explicit or weekly bounded read-only aggregate over fixed public issue metadata; raw issue content is not requested, surfaced, or agent input; no GitHub writes | `tests/test-feedback-triage.py` (26 cases) plus packaging workflow policy |
| `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`, `docs/MARKETPLACE.md` | Codex skills-only package identity plus a root-source (`.`) repo marketplace contract. The entry names the one canonical `skills/agy-worker/` bundle/runtime and is not installation or publication evidence. | `tests/test-packaging.sh` (494 cases) plus platform validators |
| `PRIVACY.md`, `TERMS.md`, `SUPPORT.md` | Public data disclosure, project policy, and support route | `tests/test-packaging.sh` (494 cases) plus review |
| `docs/index.md`, `docs/VERIFYING_AGENT_OUTPUT.md`, `docs/_layouts/`, `docs/_config.yml`, `docs/sitemap.xml` | Static GitHub Pages landing, source-grounded verification tutorial, canonical metadata, mobile table/inline-code overflow containment, and sitemap; enabling Pages and submitting the sitemap through Search Console remain external | `tests/test-packaging.sh` (494 cases) plus rendered desktop/mobile review |
| `docs/assets/brand/`, `scripts/validate-brand-assets.py` | Approved light/dark master marks, pixel-hinted micro variants, favicon PNGs, social preview, and dependency-free asset validation | `tests/test-packaging.sh` (494 cases) plus rendered review |
| `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.github/pull_request_template.md` | Contribution workflow, private vulnerability route, conduct enforcement, and review checklist | human review plus relevant offline suites |
| `.github/workflows/test.yml`, `scripts/ci-offline.sh`, `scripts/ci_stages.py`, `scripts/ci-worktree-check.sh`, `scripts/ci-diff-check.sh`, `scripts/ci_diff_check.py`, `scripts/ci_timing.py`, `scripts/ci_sharding.py` | Required `test` verifies exact full macOS offline coverage via four parallel fail-closed shards (`dispatcher`, `dispatcher-remediation`, `other-a`, `other-b`) on PRs (and explicit exact-SHA manual dispatch), cancels stale same-PR runs, and does not repeat the suite after a normal merge. Each shard checks out the exact immutable head SHA, runs committed-range diff hygiene and its registered stage subset from the 40-stage canonical manifest (`scripts/ci_stages.py`), and emits a mode-0600 no-overwrite privacy-safe v2 receipt with per-stage monotonic durations; standalone validation retains read-only v1 shape/digest compatibility, but same-run publication and aggregate acceptance require v2. GitHub retains the uploaded workflow artifact for one day under repository Actions access. The aggregate `test` job runs with `if: always()` and succeeds only when all four unique shard receipts exist, all producer jobs succeeded, all match the expected head and inventory, and every canonical stage appears exactly once. Lower CI wall time from sharding does not mean lower compute, token usage, cost, or weaker verification. Canonical local runner `./scripts/ci-offline.sh` checks tracked changes and non-ignored untracked candidate files for whitespace, then runs all 40 offline stages, including all 35 registered suite commands, by default without network or provider calls; explicit syntax bytecode is externalized without leaking the cache prefix into ordinary suites, and `--timing-report` observes monotonic wall time. | `tests/test-ci-sharding.py` (102 cases), `tests/test-ci-timing.py` (44 cases), `tests/test-ci-worktree-check.py`, plus packaging policy tests and GitHub Actions |
| `.github/workflows/compatibility-watch.yml` | Daily/manual macOS observation of fixed official evidence; bounded Step Summary only, never a required PR or metadata/action path | static policy tests in `tests/test-update.sh` plus GitHub Actions observation |
| `.github/workflows/feedback-watch.yml` | Weekly/manual Linux metadata-only feedback aggregate; read-only GitHub permissions, no raw issue content in logs or prompts, and no issue mutations | packaging policy tests plus GitHub Actions observation |
| `README.md`, `docs/INSTALLATION.md`, `docs/USAGE.md`, `docs/PROJECT_WORKFLOW.md`, `docs/OPERATIONS.md`, `docs/DOCUMENTATION_POLICY.md`, `docs/public-files.allowlist`, `scripts/validate-docs.py` | Compact first-visit onboarding plus task-owned installation, usage, project-lifecycle, and operations guides under a progressive-disclosure, single-owner, public-claim, inline-link/anchor, ordered-onboarding, Pages-mapping, complete public-docs inventory, and permanent 450-line README contract | `python3 scripts/validate-docs.py . --readme-max-lines 450`, `tests/test-packaging.sh`, and `agents-md-auditor` |
| `docs/ROADMAP.md` | Dependency-ordered product slices with explicit implemented, candidate, blocked, feature-request, or deferred status; published v0.5.0 through v0.15.0 history; the local v0.16.0 agy 1.1.24 and bounded Boost compatibility candidate; the separately governed pending SkillStore reassessment for v0.14.0; and release-external P2-D Codex-usage observation requirements. Historical release tree identity and separately verified rewritten tag identity remain distinct evidence. Source, tests, and the owning public task guide remain current-behavior authority, while tag, release, account-backed evidence, and live-provider state remain separately verifiable. | human review; publication claims remain prohibited until their gates complete |
| `AGENTS.md`, `docs/lessons_learned.md`, this file | Durable contributor rules, context routing, and architecture rationale | `agents-md-auditor` after material changes plus packaging policy checks |

## Trust boundaries

- No initial facade or raw dispatch has an implicit provider-read mode: whole-worktree launch
  requires its current manifest SHA acknowledgement, while scoped launch requires the
  reviewed scope plus transmission SHA. Whole-worktree mode exposes the entire
  disposable `--workdir` as potentially provider-readable; prompt denylists, gate
  paths, and `--add-dir` do not narrow it.
  Recommended provider-scope mode binds exact reviewed read entries, a write subset, the
  complete local path/kind enumeration, and selected-content bytes into one approved
  transmission SHA, then stages only selected entries in a fresh owner-private
  mode-`0700` Gitless cwd. The controller still locally enumerates and validates
  worktree/scope paths. Scoped staging is not filesystem, network, `PATH`, `HOME`, or
  same-UID isolation, and its approval grants no provider execution, Git action,
  driver acceptance, or publication. Secrets, denied paths, and unrelated private
  content must remain outside all entries approved for either mode.
- `agy` and every envelope field are untrusted. The driver's immutable base, path
  policy, and verification commands are trusted inputs and must be authored before
  dispatch.
- Model-routing evidence is a driver-owned classification, not worker prose. The
  recommender is outside the dispatch and acceptance paths, cannot execute either,
  and never applies its output. Default/custom tiers and the highest named tier fail
  safely to `no-escalation` when no ordered higher tier can be proved.
- An Evidence Receipt v1 is a private, unsigned serialization of one gate execution,
  not another acceptance authority. `verify-job.sh` can bind hashes and bounded
  optional G1/advisory data only after `qa-gate.sh` supplies the exact outcome through
  its internal wrapper-bound pre-opened descriptor. Direct `qa-gate.sh` is the
  no-receipt alternative; the evidence capability is not a public direct-call mode.
  Receipt validation never converts a rejected/routed
  result to `gate-passed`, and even `gate-passed` still needs human diff review.
- The model/effort matrix is compatibility metadata only. It cannot select a tier,
  dispatch a worker, recommend escalation, or accept a candidate. Direct caller input
  may resolve through it only while exact bytes, version, source, schema, and installed
  agy version match. The portable runtime owns the resolver; the root compatibility
  entry point delegates to it. `scripts/compatibility.py` carries the independently
  reviewed explicit pair and fixed-slug allowlists and rejects any matrix drift. This
  intentional duplicate representation is a fail-closed review boundary: a future
  reconciliation must update the matrix and validator policy together, never derive
  an output by concatenating model and effort strings.
- Scheduled compatibility evidence is observational. Missing or malformed official
  evidence is inconclusive, and neither the watcher nor a release/version name can
  advance a human-reviewed baseline.
- Read-only GitHub evidence never uses Git transport. `scripts/official_github.py`
  owns the exact API repository/path policy and response validation;
  `scripts/compatibility_probe.py` owns process, time, byte, environment, and signal
  bounds. These guarantees do not extend to the explicit `update.sh apply` Git fetch.
- `scripts/agy_inventory.py` interprets a separately owner-captured inventory only
  through 11 exact canonical line entries. Display aliases and generic regex matches
  are not inventory authority; unknown reviewed-provider tokens fail closed. Parsing
  cannot activate or advance compatibility metadata.
- `scripts/version_attestation_runner.py` owns the one fixed production `--version`
  path, but accepts only an isolated system-Python startup and a prior-bound private
  source/snapshot profile. `scripts/version_attestation_harness.py` is offline proof
  infrastructure, not compatibility evidence. Its fixed fake child and copy-based
  mutations exercise the canonical runner plus publication, process-group, and signal
  failure boundaries without touching agy, private evidence, provider state, metadata,
  or the network. A green result cannot authorize or substitute for a separately
  approved real observation.
- The fixed agy distribution manifest is a drift canary, not executable or source
  evidence. Its validated archive tuple is never requested, opened, hashed, or run;
  the observational snapshot detects same-version build/hash changes but cannot
  activate the model/effort matrix.
- `--workdir` is the single audited repository. User-supplied `--add-dir` roots must
  resolve inside it; multi-repository mutation is unsupported.
- Release tags are observed through fixed official REST endpoints and an apply
  candidate must match that observed commit. The apply-time Git fetch still honors
  ambient Git transport settings. Candidate validation executes release code and
  therefore relies on that transport plus the maintainer account and tag-publishing
  boundary.
- Sanitization reduces accidental disclosure but does not replace exact human review.
  The reviewed hash must bind the bytes actually sent.
- A plugin install is local enablement, not consent to send repository content.
  Dispatch through agy can expose the approved prompt and worker-read files to
  Google/Gemini; the skill must obtain explicit approval for that named scope first.
- The Codex package manifest and repo-scoped marketplace descriptor are not
  publication evidence. The root-source marketplace entry exposes no copied skill or
  runtime, and installation or external catalog enablement remains a separate owner
  action. This project is distributed from its public GitHub repository and does not
  maintain Claude catalogs.
- README and Pages copy may describe only the checks this repository actually runs:
  independent Git-scope inspection and driver-owned verification. Passing them is not
  proof of general correctness or security. GitHub About fields, topics, homepage,
  and social-preview settings are external repository-owner state.

## Generated and private artifacts

- `logs/<job>/` contains the task, full prompt, stream, stderr, staged oversized
  prompt, extracted envelope, and driver-owned selection record. The record freezes
  selector provenance and matrix binding but is not a gate receipt. The dispatcher
  creates this job tree owner-only
  even under a permissive caller umask. A missing log root is created privately; an
  existing final root must be current-user-owned, non-symlink, and not group/other
  writable before its physical path is used. The caller-owned root is not rewritten.
  This final-component check is not full ancestor-chain or TOCTOU protection. Job
  paths are created exclusively rather than reused, and staged prompt modes are
  restored on completion, early exit, and handled termination signals. Treat the
  tree as private evidence; do not commit or paste it into reports.
- Temporary worktrees, envelopes, updater candidates, and bug drafts normally live
  outside the repository. Preserve accepted work before cleanup; force removal is only
  for deliberately rejected disposable changes.
- Receipt files live only at a caller-selected new canonical path outside the audited
  repository, under an owner-private real parent. They are published mode `0600` by
  same-directory file `fsync`, atomic no-overwrite hard link, and parent `fsync`.
  They contain hashes and bounded labels rather than source, paths, commands, output,
  logs, or worker prose. They are unsigned and not self-authenticating; retain or
  delete them according to the caller's local evidence policy.
- `~/.gemini/` contains agy state. `~/.codex/skills/agy-worker/` is written only by an
  explicit `install.sh` or successful `update.sh apply`; its `.pipeline-root` is a
  local install artifact and must never enter the public skill bundle. Repository
  changes must not silently edit user configuration.
- Folder-only skill installs keep private job artifacts under their bundled
  `runtime/logs/`; repository-root and explicit-checkout installs retain the root
  `logs/` location.
- Ignored files are still part of the gate and updater collision checks. “Ignored”
  never means “outside the trust boundary.”
