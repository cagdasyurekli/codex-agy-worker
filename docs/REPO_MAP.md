# Repository map

This is a concise, hand-maintained map of the repository. Update it when entry points,
trust boundaries, ownership, or test-suite responsibility changes. It is not generated
from Graphify or another indexer.

## Core delegation flow

```text
driver task
  -> model-recommendation.sh --stage pre-dispatch (visible advisory only)
  -> agy-worker.sh selector preflight
       -> legacy tier mapping; or portable exact-SHA/version/source-bound resolver
       -> one bounded installed `agy --version` probe for direct selection only
          -> HUP/INT/TERM closes the probe process group before task read
       -> owner-private selection.json; exact slug and matrix SHA frozen once
  -> agy-worker.sh (bounded prompt, sandbox/mode/one exact model, private artifacts)
  -> agy (untrusted worker)
  -> structured envelope
  -> skills/agy-worker/runtime/scripts/validate-envelope.py (shape and contract only)
  -> one caller-chosen verification path:
       -> verify-job.sh (receipt-producing wrapper)
            -> sanitized, capability-bound launch with a pre-opened evidence FD
               owned only by the gate parent
            -> qa-gate.sh (Git scope, immutable base, policy, escalation)
                 -> isolated gate helpers
                 -> driver-owned --verify children started only after the evidence
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
  -> job.sh verify -> exact verify-job.sh / qa-gate.sh receipt path
  -> job.sh status (read-only facts and current approval hashes)
  -> gate-passed: preserve-instructions only, never execution
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

The persona registry consumes those public contract bytes without changing P1-C:

```text
fixed shipped persona source + declared runtime modes
  -> persona-evidence.sh validate
       -> exact allowlist manifest and canonical record/schema bytes
       -> exact P1-C plan/result/Receipt plus real Receipt and dispatch/tool artifacts
       -> immutable evidence commit < approval/review commit < transition commit
       -> exact Git blob/mode/allowlist checks (checkout-only for upper states)
  -> persona-evidence.sh report (pure deterministic Markdown rows)
```

It does not execute a persona, dispatch, run the benchmark or gate, promote a state,
rank, route, accept, or trust target-repository registration. P1-C remains only the
evidence producer and carries no persona trust label.

Strict ancestry evidences sequencing under the protected-main/maintainer/local-Git
TCB; it is not a signature, author-identity proof, or same-maintainer tamper defense.

The optional workload-profile view is deliberately outside execution and acceptance:

```text
fixed hash-bound profiles/v1 data
  -> profile.sh list (maintained names and summaries only)
  -> profile.sh show NAME (non-executable mode/persona/path-shape suggestion)
  -> caller still supplies approval, exact repo, path policy, tier, and verifiers
```

It never discovers target-repository, environment, home-directory, or caller-path
profiles and cannot dispatch, route, recommend, gate, accept, or perform a Git action.

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
- `bug-report.sh draft` creates a private sanitized local draft. `preview` prints the
  exact body and SHA-256. `submit` requires that hash and sends the already validated
  bytes to the explicitly bound GitHub destination. Nothing submits automatically.
- `skills/agy-worker/` is the canonical Agent Skill and owns the complete core runtime.
  A skill-folder-only copy resolves `runtime/` without the repository or a network
  fetch. Repository-root commands are compatibility wrappers; `install.sh` copies the
  same bundle and adds a local `.pipeline-root` marker so checkout maintenance remains
  available. `.codex-plugin/plugin.json` describes the same skill for local package
  validation; GitHub clone plus explicit install is the supported public path.
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
| `agy-worker.sh`, `skills/agy-worker/runtime/agy-worker.sh` | Root compatibility entry plus strict selector-source parsing, canonical dispatch, frozen model/mode selection, bounded retries, private selection/prompt/log staging, envelope extraction | `tests/test-agy-worker.sh` (209 cases) |
| `model-selection.sh`, `skills/agy-worker/runtime/model-selection.sh`, `skills/agy-worker/runtime/scripts/model_selection.py`, portable matrix/schema/SHA | Root compatibility entry plus canonical exact model/effort resolution, bounded installed-version preflight, and driver selection provenance | dispatcher, doctor, and packaging suites |
| `model-recommendation.sh`, `skills/agy-worker/runtime/model-recommendation.sh`, `skills/agy-worker/runtime/scripts/model-recommendation.py` | Root compatibility entry plus side-effect-free pre/post recommendations; direct selections are labelled but unranked and never applied | `tests/test-agy-worker.sh` (209 cases) |
| `doctor.sh`, `skills/agy-worker/runtime/doctor.sh`, `skills/agy-worker/runtime/scripts/doctor-metadata.py`, `skills/agy-worker/runtime/compat/` | Root compatibility entry plus deterministic offline prerequisite checks and byte-synchronized portable agy metadata | `tests/test-doctor.sh` (239 cases) plus packaging synchronization checks |
| `install.sh`, `skills/agy-worker/`, `skills/agy-worker/scripts/resolve-pipeline.sh` | Install and resolve complete-plugin, explicit-checkout, or folder-only skill layouts without fetching code | dispatcher and packaging suites |
| `skills/agy-worker/runtime/schemas/`, `skills/agy-worker/runtime/scripts/validate-envelope.py` | Dependency-free envelope contract validation | dispatcher and gate suites |
| `qa-gate.sh`, `skills/agy-worker/runtime/qa-gate.sh` | Root compatibility entry plus canonical immutable-base Git audit, path policy, escalation, driver verification, and internal pre-opened structured evidence handoff | `tests/test-qa-gate.sh` (41 cases) plus receipt suite no-FD compatibility checks |
| `verify-job.sh`, `skills/agy-worker/runtime/verify-job.sh`, `skills/agy-worker/runtime/scripts/evidence_receipt.py`, `skills/agy-worker/runtime/schemas/evidence-receipt.schema.json` | Root compatibility entry plus exact input hashing, strict selection/advisory binding, startup-isolated parent-exclusive gate evidence, interruption cleanup, unsigned receipt validation, and private durable no-overwrite publication | `tests/test-evidence-receipt.sh` (88 cases) |
| `evidence-report.sh`, `skills/agy-worker/runtime/evidence-report.sh`, `skills/agy-worker/runtime/scripts/evidence_report.py`, `skills/agy-worker/runtime/scripts/recommendation_record.py` | Root compatibility entry plus pure Receipt v1 validation, deterministic bounded text/canonical-JSON/Markdown/GitHub-Step-Summary rendering, final workflow-command and Markdown safety checks, separately trusted binding checks, privacy filtering, and optional mode-0600 no-overwrite publication; the renderer never discovers the GitHub summary environment path; stdout-only `main(argv)` returns, while file-output `main(argv)` is process-owning through `os._exit(0)` and must run as a command/subprocess; never dispatches, routes, gates, uploads, or changes a verdict | `tests/test-evidence-report.sh` (80 cases), receipt back-compat, and packaging checks |
| `job.sh`, `skills/agy-worker/runtime/job.sh`, `skills/agy-worker/runtime/scripts/job_lifecycle.py`, `skills/agy-worker/runtime/scripts/candidate_state.py`, `skills/agy-worker/runtime/schemas/job-state.schema.json` | Root compatibility entry plus process-owning lifecycle CLI, external private v1 state, canonical branch-backed worktree init/status, fixed sanitized Git execution with no checkout hook/filter authority, exact Receipt delegation/binding, read-only preserve instructions, interrupted-progress reconciliation, rc1-only ref-absence evidence, and triple-approved rejected-only compare-and-delete cleanup; the shared candidate helper is also the gate's sole digest implementation | `tests/test-job-lifecycle.py` (100 cases), gate parity, doctor, and packaging suites |
| `benchmark.sh`, `benchmarks/v1/`, `docs/BENCHMARKING.md`, `skills/agy-worker/runtime/benchmark.sh`, `skills/agy-worker/runtime/scripts/benchmark.py`, benchmark schemas and portable assets | Root compatibility entry plus explicit clean-commit or reviewed portable-source-manifest authority, structurally complete v1 schemas, immutable offline plan, exact one synthetic attempt per ordered caller variant/task, canonical Receipt v1 delegation, unsigned result binding, and pure manifest-order completeness report; no agy, provider, ranking, route, recommendation, retry, or live mode | `tests/test-benchmark.py` (104 cases), doctor, resolver, and packaging suites |
| `persona-evidence.sh`, `compat/personas/`, `docs/PERSONAS.md`, and canonical runtime registry/schema/validator assets | Fixed shipped-persona allowlist, coherent immutable tool/persona/version source chain, exact immutable P1-C source assets/plan/result, exact per-phase evidence inventories, exact `accept-edits` candidate-state authority, reviewed P1-C public-contract bindings, immutable semantic evidence/approval/review ancestry, and pure deterministic table; no persona execution, dynamic registration, promotion, trust, routing, ranking, or acceptance | `tests/test-persona-evidence.py` (124 cases), doctor, resolver, and packaging suites |
| `profile.sh`, `profiles/v1/`, `docs/PROFILES.md`, and canonical runtime profile/schema/renderer assets | Fixed hash-bound data-only workload skeletons plus pure canonical list/show; maintained mode/persona/path-policy-shape suggestions only, with caller repo/tier/path/verifier/approval still required; no discovery, command, dispatch, route, gate, acceptance, provider, network, or Git action | `tests/test-workload-profiles.py` (89 cases), doctor, resolver, and packaging suites |
| `proof-demo.sh`, `conformance/v1/envelopes/honest.json`, conformance content sources | Repository-only offline starter proof using the two-state teaching subset of the public versioned contract | `tests/test-proof-demo.sh` (21 cases) |
| `conformance/run.sh`, `conformance/v1/`, `docs/CONFORMANCE.md` | Repository-only public qa-gate v1 fixture contract, strict manifest/source binding, private normally disposable repositories, bounded supplied-gate execution, FD-relative no-follow cleanup under an explicit same-UID TCB, fail-closed residual policy, and non-certification claim | `tests/test-conformance.py` (81 cases) plus packaging policy checks |
| `skills/agy-worker/runtime/agents/*.md` | Prompt-injected bounded personas; prompt text is guidance, not enforcement | dispatcher suite plus bounded real exercises |
| `update.sh`, `scripts/compatibility.py`, `scripts/compatibility_probe.py`, `scripts/agy_inventory.py`, `scripts/official_github.py`, `scripts/official_distribution.py`, `compat/` | Explicit project releases; exact fixed-REST agy/Codex observation; bounded process-group/version probes; exact-line allowlisted agy inventory interpretation with reserved provider namespaces; sanitized reconciliation records; bounded distribution-manifest canary; strict per-tool metadata and active-only-when-bound model/effort matrix. Explicit apply-time Git fetch remains ambient-configuration-aware. | `tests/test-update.sh` (310 cases, including fixed transport, supervisor, inventory, and manifest adversary harnesses) |
| `scripts/version_attestation_runner.py` | Canonical fixed-profile snapshot-backed `--version` attestation; fixed `/usr/bin/python3 -I -S -B` launch under an explicit trusted Apple interpreter/host/local-owner/OS-admin boundary; exact family, component, family-specific alias kind, alias/target identity, executable/no-setid, and no-world-writable-directory/resolved-executable checks; bounded UID/GID/mode diagnostics; one exact Popen; bounded streams/pre-reap group cleanup; private durable binding; and synthetic-only self-test. It does not prove binary provenance, code signing, host attestation, or same-user/hostile-PR tamper resistance. Production execution remains a separate explicit action. | `tests/test-version-attestation-runner.py` (157 cases) |
| `scripts/version_attestation_harness.py` | Persistent provider-independent mutation harness for owner-private no-overwrite publication, one bounded synthetic controller supervisor, exact process-group cleanup, lifecycle-signal linearization, fixed copy-based weakened controls, and exact byte/SHA binding to the canonical runner before import. It never invokes agy or reads compatibility evidence. | `tests/test-version-attestation-harness.py` (55 cases) |
| `scripts/models_attestation_runner.py`, `scripts/agy_inventory.py` | Separate canonical fixed-profile snapshot-backed `models` inventory mechanism bound to an accepted version binding and the same attested executable; one exact Popen, private empty HOME/TMP/XDG and closed environment, 25-second/64-KiB bounds, exact 11-line allowlist semantics, private durable raw/source/binding publication, and synthetic-only self-test. Auth-required output rejects without completion or metadata advance. The accepted version binding is version-only; this runner never inherits credentials, serves as a live-account capture path, advances metadata, proves a provider backend, applies a selector, or exposes model/effort flags. A future real-account capture needs a separate reviewed runner and authorization. Source-contract mutations are selected drift checks under the reviewed-source/local-owner TCB, not hostile-source or same-UID tamper resistance. | `tests/test-models-attestation-runner.py` (113 cases), plus updater parser controls |
| `scripts/models_capture_runner.py` | Separate capture-only fixed-profile mechanism for one future explicitly authorized real-account `models` observation. Canonical stdin binds the exact owner-private account HOME identity, reviewed version binding and retained snapshot; the child receives that HOME plus capture-owned private TMP/XDG/cwd and a fixed environment. Nofollow component/identity revalidation, exact empty post-child scratch, 25-second/64-KiB bounds, pre-reap process-group closure, signal-safe mode-0600 no-overwrite publication, and a final `models.capture.sha256` marker produce private `captured` evidence only. Any bounded exit-zero stdout/stderr is captured without inventory or error interpretation. It does not inspect HOME contents, accept inventory, advance metadata, route, retry, log in, or prompt; tests make no real-account call. The external CLI may mutate/cache in HOME and the runner cannot detect or revert it; account residuals can remain after rejection. The account HOME/local owner/same-UID processes/reviewed source and interpreter/OS admins are trusted; no same-user tamper resistance is claimed. | `tests/test-models-capture-runner.py` (69 fake-account cases) |
| `bug-report.sh`, `scripts/bug-report.py`, `.github/ISSUE_TEMPLATE/` | Local privacy filtering, exact review binding, optional issue submission | `tests/test-reporting.sh` (21 cases) |
| `.codex-plugin/plugin.json` | Codex skills-only package identity retained for local validation; not a public listing | `tests/test-packaging.sh` (347 cases) plus platform validators |
| `PRIVACY.md`, `TERMS.md`, `SUPPORT.md` | Public data disclosure, project policy, and support route | `tests/test-packaging.sh` (347 cases) plus review |
| `docs/index.md`, `docs/_layouts/`, `docs/_config.yml`, `docs/sitemap.xml` | Static GitHub Pages landing, canonical metadata, and sitemap; enabling Pages and submitting the sitemap through Search Console remain external | `tests/test-packaging.sh` (347 cases) plus rendered review |
| `docs/assets/brand/`, `scripts/validate-brand-assets.py` | Approved light/dark master marks, pixel-hinted micro variants, favicon PNGs, social preview, and dependency-free asset validation | `tests/test-packaging.sh` (347 cases) plus rendered review |
| `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.github/pull_request_template.md` | Contribution workflow, private vulnerability route, conduct enforcement, and review checklist | human review plus relevant offline suites |
| `.github/workflows/test.yml`, `scripts/ci-diff-check.sh`, `scripts/ci_diff_check.py` | macOS CI for committed PR/push diff hygiene, syntax, and all eighteen offline suites. Checkout supplies the event range; the helper performs no fetch and linearly scans changed immutable head blobs from one bounded `git cat-file --batch` process, independently of attributes or diff drivers. | packaging policy tests plus GitHub Actions |
| `.github/workflows/compatibility-watch.yml` | Weekly/manual macOS observation of fixed official evidence; bounded Step Summary only, never a required PR or metadata/action path | static policy tests in `tests/test-update.sh` plus GitHub Actions observation |
| `README.md` | User setup, examples, current capabilities and limitations | review plus relevant offline suites |
| `docs/ROADMAP.md` | Dependency-ordered product slices with explicit implemented or deferred status, v0.2.0 scope, approval gates, and honest success measures; source, tests, and README remain current-behavior authority | human review; implementation claims remain prohibited until their slices land |
| `AGENTS.md`, `docs/lessons_learned.md`, this file | Durable contributor rules and architecture | `agents-md-auditor` after material changes |

## Trust boundaries

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
- The Codex package manifest is not publication evidence. This project is distributed
  from its public GitHub repository and does not maintain Claude or marketplace
  catalogs.
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
