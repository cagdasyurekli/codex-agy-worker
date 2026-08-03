# Product roadmap

This document describes **planned work, not current behavior**. The current command
surface and verified limitations remain in [README.md](../README.md). A roadmap item
becomes current only after its own implementation, adversarial tests, documentation
review, and an accepted pull request.

The first recommended implementation is **P0-A Evidence Receipt v1 only**. Starting
that slice requires a fresh, explicit approval; this roadmap does not authorize code,
commit, push, pull-request, merge, release, live model use, or another external action.

## Product direction

`codex-agy-worker` should remain a small Codex-to-agy pipeline whose differentiator is
independent evidence, not feature-count parity. Codex owns planning, scope, acceptance
criteria, verification, and human-facing judgment. agy performs bounded worker tasks.
The worker envelope remains a claim.

Nearby projects demonstrate useful demand for asynchronous jobs, lifecycle tools,
diagnostics, multiple backends, MCP servers, and broad automation:

- [codex-agy-delegator](https://github.com/swjturay/codex-agy-delegator)
  exposes asynchronous multi-backend runs, report/apply/cleanup tools, and worktree
  isolation through an MCP server.
- [codex-antigravity-bridge](https://github.com/Common-ka/codex-antigravity-bridge)
  provides asynchronous status/result tools, capability and smoke probes, compact
  result modes, and retained local run artifacts.
- [agy-mcp](https://github.com/Boulea7/agy-mcp) combines typed MCP tools, a doctor,
  long-task supervision, skill bundles, worktrees, and safety policy.
- [antigravity-for-claude-code](https://github.com/VKirill/antigravity-for-claude-code)
  pursues detached jobs, multi-role orchestration, automatic commits, pushes, and
  deployment.

Those primary project sources motivate better diagnostics, lifecycle ergonomics, and
portable evidence here. They do **not** justify adopting their MCP, daemon,
multi-backend, or autonomous-shipping architecture. This project should make its
narrow evidence boundary easier to see and use.

## Evidence terminology

Use these terms consistently in code, tests, documentation, and reports:

- **Worker envelope:** schema-valid worker-authored claims. Shape is validated; truth
  is not implied.
- **Driver input:** immutable base, audited repository, path policy, verification
  commands, and controlled routing evidence selected before or after dispatch by the
  driver.
- **Gate observation:** facts independently derived by `qa-gate.sh` from Git and
  driver-owned verification.
- **Accepted candidate:** gate exit `0` followed by human diff review. It is not a
  commit, merge, release, or proof of general correctness or security.
- **Evidence receipt:** a local, versioned record binding a gate invocation to hashes
  of its inputs and observed outcome. It is not a signature or a new acceptance
  authority.
- **Receipt verdict:** exactly `gate-passed`, `rejected`, or `routed`. A receipt is
  never “accepted.” `gate-passed` records gate exit `0`; only the required later human
  diff review can turn that candidate into an accepted candidate.
- **Human report:** a sanitized rendering of a validated receipt. It cannot improve
  or reinterpret the underlying outcome.
- **Provider-reported usage:** token, duration, or turn telemetry supplied by agy. It
  is not independent billing or quota evidence.
- **Persona evidence status:** the documented level of offline and bounded real-job
  evidence for a prompt-injected persona. It is not a general quality guarantee.

## Immutable cross-slice rules

Every roadmap slice must preserve all of these rules:

1. `agy`, its stream, and every envelope field remain untrusted.
2. Acceptance continues to require an immutable base, complete Git-visible scope,
   driver-owned verification, unchanged candidate state during verification, and
   human diff review.
3. No receipt, report, persona, profile, benchmark, usage number, or CI rendering may
   become an alternative acceptance path.
4. The caller selects the model tier. Recommendations remain visible and advisory,
   with `recommendation_only: true` and `applied: false`.
5. Never add automatic tier/model changes or a wrapper-level thinking/effort control.
   Retries keep the caller-selected tier.
6. Permission, authentication, scope-policy, invalid-contract, untrusted-claim, and
   human-required outcomes are non-escalatable.
7. No feature may automatically commit, push, open a pull request, merge, release,
   submit an issue, enable a service, or publish an artifact.
8. No feature may silently edit `~/.codex`, `~/.gemini`, or another user
   configuration location.
9. The runtime remains Bash 3.2-compatible shell, Python 3 standard library, and git.
   Do not add Node, Bun, an MCP daemon, or a package-manager runtime dependency.
10. Offline suites remain independent of agy, network, provider credentials, and
    paid quota. Every new enforcement check needs both an accept and reject case.
11. The canonical runtime remains under `skills/agy-worker/runtime/`; root runtime
    commands are compatibility wrappers. Repository-only demonstrations and
    conformance fixtures may remain outside the public skill when documented as such.
12. External data transmission, live provider use, destructive cleanup, and GitHub
    or release actions keep separate explicit approval gates.

## Current agy inventory correction

The local `./ground-truth.sh` inventory on agy `1.1.9` currently lists `--effort`,
`models`, `agent`/`agents`, and `plugin`/`plugins` in addition to the already used
print, model, mode, sandbox, schema, timeout, conversation, and directory options.
However, the live `models` and `agents` probes failed in the current Codex sandbox
because agy could not write under `~/.gemini` or bind its local language-server
socket. Exact model and agent catalogs therefore remain unverified in this audit.

This corrects the inventory without expanding the wrapper contract:

- Do not add `--effort` or infer a thinking level. The roadmap deliberately preserves
  explicit tier selection and recommendation-only routing.
- Do not add dynamic model/persona discovery from help text alone.
- Do not infer authentication from a single failure or invent `agy auth`; unknown
  subcommands can print usage and exit `0`.
- Any future exposure of newly advertised agy behavior is a separate compatibility
  slice requiring official docs, official source, a sandbox-correct live inventory,
  a bounded real job, paired offline tests, and explicit approval.

## Release groups and slices

Each slice below is independently reviewable. A later slice must not be smuggled into
an earlier implementation because it shares a schema or helper.

### P0 — make the evidence boundary visible and usable

#### P0-A — Evidence Receipt v1

- **User job:** Preserve what the driver checked, against which immutable base and
  policy, and what the gate concluded without sharing source, prompts, raw logs, or
  worker prose.
- **Intended surface:** Add canonical
  `skills/agy-worker/runtime/verify-job.sh`, root `verify-job.sh`,
  `skills/agy-worker/runtime/schemas/evidence-receipt.schema.json`, and a
  dependency-free receipt validator. The wrapper invokes the existing gate, writes
  one atomic mode-`0600` receipt outside the audited repository, and returns the gate
  exit unchanged. Optional `--pre-recommendation FILE` and
  `--post-recommendation FILE` inputs may bind already-rendered advisory results; the
  receipt command never generates or applies them.
- **Receipt v1 minimum:** Version and kind; immutable base; envelope hash; candidate
  state hash; ordered path-policy hash; verifier labels and command hashes; exact gate
  exit/outcome; a verdict restricted to `gate-passed`, `rejected`, or `routed`;
  optional caller-selected tier; optional validated pre/post advisory objects
  retaining their rationale, controlled driver evidence, relative cost impact,
  `recommendation_only: true`, and `applied: false`; `gate_authority: qa-gate`; and an
  explicit statement that the receipt is unsigned and recommendations did not
  participate in acceptance.
- **Exclude:** Diff/source content, prompt, worker summary/confidence, raw verifier
  commands or output, credentials, absolute repository paths, provider pricing, and
  an applied recommendation.
- **Dependencies:** Existing `qa-gate.sh`, envelope validator, Python SHA-256, git.
- **Trust boundary:** A receipt records a gate execution. It must not reproduce gate
  acceptance logic, treat its own existence as acceptance, use `accepted` as a
  verdict, or map any nonzero gate result to `gate-passed`. A receipt path inside the
  audited repository is rejected.
- **Minimum accept tests:** An honest edit with passing driver verification yields a
  schema-valid receipt with verdict `gate-passed`; hashes bind the exact base,
  envelope, policy, verifier order, and candidate state; wrapper and gate exit codes
  match. The test does not call the candidate accepted before human review.
- **Minimum reject tests:** Scope failure, malformed envelope, untrusted command/test
  claim, missing edits, verifier failure/mutation, and human-required outcome remain
  rejected and are never rendered as acceptance; reject overwrite, symlink target,
  in-repository output, unknown schema version, receipt tampering, cross-stage or
  malformed advisory input, selected-tier mismatch, or an advisory that claims it was
  applied.
- **Docs and AGENTS impact:** Update README, SKILL, privacy disclosure, REPO_MAP, and
  architectural lessons only after implementation. AGENTS then gains the actual new
  suite/count and a concise durable rule that receipts do not replace gate or human
  review. Run `agents-md-auditor` before and after.
- **Size:** M.
- **Done/exit criteria:** One isolated PR; no behavior change when `verify-job.sh` is
  unused; all current suites plus receipt accept/reject matrix green; no raw private
  data in the receipt; independent verifier confirms no weaker gate path.

#### P0-B — Human Report renderer

- **User job:** Read or share a compact bounded result without pasting private job
  artifacts.
- **Intended surface:** Add canonical `evidence-report.sh --receipt FILE
  --format text|markdown` plus a root wrapper. Output defaults to stdout; writing a
  file requires an explicit path and refuses overwrite.
- **Dependencies:** Validated Receipt v1 schema and validator; no dispatch dependency.
- **Trust boundary:** Rendering only. It cannot invoke agy, git, gate, routing, or a
  network client. Rejected and routed receipts must be headed plainly as such. It may
  state only the receipt's bounded observations.
- **Minimum accept tests:** Receipts whose verdicts are `gate-passed`, `rejected`, and
  `routed` render stable, escaped text and Markdown with exact exit/outcome and
  verification labels.
- **Minimum reject tests:** Malformed, tampered, unsupported-version, control-character,
  Markdown-link-injection, or forbidden-private-field input produces no report.
- **Docs and AGENTS impact:** Add report examples to README/SKILL and ownership to
  REPO_MAP; update privacy language. Change AGENTS only for an actual suite/count.
- **Size:** S.
- **Done/exit criteria:** Deterministic output; no raw command, source, prompt, log, or
  absolute-path disclosure; renderer has no execution or submission capability.

#### P0-C — Read-only Doctor

- **User job:** Diagnose readiness before spending provider quota or changing personal
  configuration.
- **Intended surface:** Add canonical `doctor.sh [--repo DIR]
  [--format text|json]` plus root wrapper. Default execution is offline and read-only.
- **Checks:** Runtime resolution; Bash compatibility; Python 3 and git availability;
  Git worktree support; agy presence and version against checked-in compatibility
  metadata; target repository validity; and due/invalid compatibility review
  metadata.
- **Dependencies:** Existing resolver, compatibility metadata, and `ground-truth.sh`
  facts. It is independent of Receipt v1.
- **Trust boundary:** A green result means only that offline prerequisites passed. It
  does not certify authentication, provider availability, sandbox permission, task
  quality, or future dispatch success. It never repairs configuration. Optional
  config inspection requires an explicit path; it does not scan home files silently.
- **Minimum accept tests:** A fake compatible toolchain yields structured green output
  and a before/after filesystem snapshot is unchanged.
- **Minimum reject tests:** Missing agy/Python/git, version drift, incomplete bundle,
  invalid target repo, and invalid/due compatibility metadata fail or warn according
  to a documented matrix; no `agy auth` or unknown-subcommand probing; no network or
  configuration writes.
- **Docs and AGENTS impact:** Add onboarding/troubleshooting to README and SKILL,
  ownership to REPO_MAP, and a durable no-auto-fix lesson. AGENTS receives only the
  implemented suite/count and current doctor boundary.
- **Size:** M.
- **Done/exit criteria:** Stable text/JSON contract, no live prompt, no personal-config
  mutation, and offline fake-tool coverage for every reported state.

#### P0-D — 60-second offline proof demo

- **User job:** See the project's differentiator in under one minute without agy,
  credentials, network, or API credits.
- **Intended surface:** Add repository-only `proof-demo.sh` and a minimal
  `demo/fixtures/` pair. It creates a temporary Git repository, demonstrates one
  honest candidate that is `gate-passed` by driver verification and one plausible
  worker claim rejected because Git reality disagrees, prints a short explanation,
  and cleans only its own temporary directory. The demo performs no human review and
  therefore labels no candidate accepted.
- **Dependencies:** Current `qa-gate.sh`; use Receipt v1 when available without making
  the demo block P0-A.
- **Trust boundary:** This is a demonstration, not certification, a benchmark, or
  evidence of real agy quality. It must not edit the checkout or use the current
  repository as the fixture.
- **Minimum accept tests:** Runs offline on macOS Bash 3.2 in less than 60 seconds and
  shows the expected accept/reject exits.
- **Minimum reject tests:** A deliberately trusting substitute gate cannot make the
  demo pass; temporary-path collision and interrupted cleanup fail safely; no agy or
  network executable is invoked.
- **Docs and AGENTS impact:** README quick proof and Pages link; REPO_MAP ownership.
  AGENTS changes only if this becomes a named completion check.
- **Size:** S.
- **Done/exit criteria:** Two bounded cases, deterministic summary, verified offline,
  and explicit “starter proof, not conformance certification” wording.

### P1 — integrate the evidence boundary without broadening autonomy

#### P1-A — Safe local lifecycle

- **User job:** Create, inspect, verify, and deliberately clean an isolated job without
  manually reproducing the full worktree recipe.
- **Intended surface:** `job.sh init|status|verify|preserve-instructions|cleanup` with
  a private mode-`0600` state file binding exact target, worktree, branch, immutable
  base, and job ID. `verify` delegates to Receipt v1. `preserve-instructions` prints
  deliberate commands; it does not run them.
- **Dependencies:** Receipt v1; Doctor is recommended but not required.
- **Trust boundary:** No background process, polling, daemon, commit, push, PR, merge,
  release, auto-dispatch, or auto-model choice. Destructive cleanup is allowed only
  after explicit user approval for the exact job ID and exact hash-bound candidate
  state recorded with receipt verdict `rejected`. Immediately before deletion it
  re-derives that digest and refuses any mismatch. It refuses `gate-passed` or
  accepted candidates, routed outcomes, tampered/stale state, foreign or unbound
  artifacts, and every uncommitted state that does not exactly match the recorded
  rejected digest.
- **Minimum accept tests:** Init creates the exact branch-backed worktree; status
  detects current state; verify produces the expected receipt; after explicit user
  approval, cleanup removes only the exact uncommitted rejected candidate whose
  current digest matches the hash-bound rejected receipt.
- **Minimum reject tests:** Mutable base, path/branch collision, foreign or moved
  worktree, tampered/stale state, changed-since-verification digest, outside-repo root,
  symlink escape, missing explicit approval, `gate-passed`/accepted/routed outcome,
  foreign or unbound artifact, uncommitted state not exactly matching the recorded
  rejected digest, and any GitHub or commit command.
- **Docs and AGENTS impact:** Replace the README's manual path with a primary lifecycle
  example while retaining the manual reference; update SKILL, REPO_MAP, lessons, and
  current completion checks after implementation.
- **Size:** L.
- **Done/exit criteria:** Crash-safe state transitions; cleanup only for an explicitly
  approved, exact hash-bound rejected disposable state; no loss of gate-passed or
  accepted work; no external action; and independent destructive-target review.

#### P1-B — Full public conformance kit

- **User job:** Let integrations and forks test the published contract rather than
  claim compatibility from prose.
- **Intended surface:** `conformance/run.sh --gate PATH`, versioned
  `conformance/v1/manifest.json`, synthetic repositories/envelopes, and
  `docs/CONFORMANCE.md`. P0-D fixtures become the small starter subset.
- **Dependencies:** Stable gate behavior and Receipt v1 if receipts are part of the
  conformance claim.
- **Trust boundary:** Conformance means only that the implementation passes the
  published fixtures. It is not a security certification or real-job quality proof.
- **Minimum accept tests:** Current gate passes every required v1 fixture with exact
  documented exits.
- **Minimum reject tests:** Deliberately permissive gates that trust worker claims,
  ignore ignored files, accept mutable bases, skip verification, accept verifier
  mutation, or accept human-required outcomes must fail the kit.
- **Docs and AGENTS impact:** Add conformance specification and bounded README badge
  wording; update REPO_MAP and lessons. AGENTS receives actual suite/count only.
- **Size:** L.
- **Done/exit criteria:** Public versioned fixture contract, malicious reference
  implementations rejected, and no “certified secure” language.

#### P1-C — Reproducible benchmark harness

- **User job:** Compare releases, caller-selected tiers, or personas on fixed bounded
  tasks using gate observations rather than subjective worker summaries.
- **Intended surface:** `benchmark.sh prepare|run|report`, frozen
  `benchmarks/v1/manifest.json`, ignored local result directory, and
  `docs/BENCHMARKING.md`. Paid work requires an explicit `--live` boundary.
- **Dependencies:** Receipt v1; lifecycle is useful but optional.
- **Trust boundary:** Every result binds exact fixture/base, tool versions, selected
  tier, attempt count, policy, and verification. No hidden retries or model changes.
  Competitor comparisons require identical public tasks and rules. No leaderboard by
  default.
- **Minimum accept tests:** Frozen offline fixture produces a deterministic report and
  receipt; live-mode parser preserves selected tier and attempt count.
- **Minimum reject tests:** Changed fixture hash, missing verifier, unpublished input,
  partial task set described as complete, hidden retry/model change, or result without
  exact version binding.
- **Docs and AGENTS impact:** Add BENCHMARKING document and README evidence link;
  update REPO_MAP. AGENTS updates only verified real/offline evidence boundaries, not
  one-off results.
- **Size:** L.
- **Done/exit criteria:** Reproducible offline harness; live execution remains a
  separately approved action with explicit Google/Gemini data scope and cost.

#### P1-D — Persona evidence registry

- **User job:** Distinguish offline persona contract coverage from honest escalation
  and accepted real-candidate evidence.
- **Intended surface:** Validated `compat/personas/<name>.json` records and a generated
  documentation table. Runtime persona selection remains an explicit hardcoded
  allowlist; target repositories cannot register executable personas dynamically.
- **Dependencies:** Receipt v1 and public benchmark fixtures.
- **Trust boundary:** Persona text remains prompt guidance, never enforcement. One
  accepted real candidate does not make a persona generally reliable. Use evidence
  states such as `offline-only`, `real-escalation-observed`, and
  `accepted-real-candidate`; do not label a persona simply “trusted.”
- **Minimum accept tests:** Registered persona exists, has valid frontmatter and mode
  restriction, and references exact reproducible evidence for its stated level.
- **Minimum reject tests:** Unknown/path-alias persona, edit mode for a read-only
  persona, self-authored acceptance claim, missing base/verifier/tool version, private
  evidence described as public, or target-repository dynamic registration.
- **Docs and AGENTS impact:** Update README persona matrix, limitations, REPO_MAP, and
  current evidence boundaries. Keep one-off run history out of AGENTS.
- **Size:** M for registry; real exercises are separate approved operational work.
- **Done/exit criteria:** Every shipped persona has precise evidence status; no
  stronger claim than its reproducible records support.

#### P1-E — CI-safe JSON, Markdown, and GitHub Step Summary reporter

- **User job:** Render an already produced receipt in CI without custom parsing or a
  networked bot.
- **Intended surface:** Extend the report command with `--format json|markdown|github-step-summary`.
  Output goes to stdout or an explicit file. Documentation shows explicit redirection
  to `$GITHUB_STEP_SUMMARY`; the tool does not discover or write that environment file
  implicitly.
- **Dependencies:** Receipt v1 and Human Report renderer.
- **Trust boundary:** Reporter never dispatches agy, runs the gate, comments on a PR,
  calls GitHub APIs, or publishes artifacts. It escapes workflow-command and Markdown
  injection. JSON is the validated bounded report representation, not the raw
  envelope.
- **Minimum accept tests:** Receipts whose verdicts are `gate-passed`, `rejected`, and
  `routed` render exact status in all three formats and preserve stable
  machine-readable fields.
- **Minimum reject tests:** Workflow-command injection, Markdown links/HTML/control
  characters, malformed receipt, forbidden private fields, implicit environment-file
  writes, and external command/network invocation.
- **Docs and AGENTS impact:** Add a GitHub Actions snippet and fork/secret warning;
  update REPO_MAP. No GitHub integration claim beyond local rendering.
- **Size:** M.
- **Done/exit criteria:** Offline deterministic reporters, no network permissions, and
  exact rejected-state visibility.

Do not commit to SARIF: a gate run is not naturally a static-analysis result with
locations/rules. Do not add JUnit unless a concrete consumer first demonstrates a
semantically honest mapping; “job rejected” is not automatically a test-case failure.

### P2 — optional local ergonomics and telemetry

#### P2-A — Data-only workload profiles

- **User job:** Start common bounded jobs from a maintained skeleton without hiding
  policy choices.
- **Intended surface:** Bundled `profiles/*.json` plus `profile.sh list|show NAME`.
  Profiles may suggest mode, maintained persona, and path-policy shape. The driver
  supplies the selected tier, exact repository, verification commands, and approval.
- **Dependencies:** Stable lifecycle input contract.
- **Trust boundary:** No target-repository auto-loading. Profiles contain no model,
  tier, effort/thinking value, executable verifier, external add-dir, authorization,
  auto-dispatch, or Git action.
- **Minimum accept tests:** Maintained profile renders a non-executable plan that still
  requires caller tier and verifier.
- **Minimum reject tests:** Embedded model/tier/effort, shell command, authorization,
  outside-workdir root, dynamic repository profile, path alias, or implicit dispatch.
- **Docs and AGENTS impact:** README/SKILL/REPO_MAP and durable profile restrictions
  only after implementation.
- **Size:** M.
- **Done/exit criteria:** Profiles reduce typing without deciding cost, executing
  policy, or expanding read scope.

#### P2-B — Provider-reported usage and latency

- **User job:** Inspect one run's reported token, turn, and duration telemetry to make
  a manual batching decision.
- **Intended surface:** `usage-report.sh --stream FILE` for one explicitly selected
  NDJSON stream, optionally folded into a receipt/report as
  `provider_reported_usage`.
- **Dependencies:** Stable receipt/report schema and confirmed agy terminal-event
  shape.
- **Trust boundary:** No automatic log-directory scan. Provider telemetry is not bill,
  price, remaining quota, or independent evidence; it never changes acceptance,
  routing, selected tier, or retry count.
- **Minimum accept tests:** One valid terminal result yields labeled token/turn/duration
  fields and preserves missing values honestly.
- **Minimum reject tests:** Multiple ambiguous result events, malformed stream,
  inferred currency, inferred quota, auth/permission failure treated as cost evidence,
  implicit log scanning, and routing changes.
- **Docs and AGENTS impact:** README limitations, privacy disclosure, REPO_MAP, and
  model-routing lesson. AGENTS need not grow unless a new suite is added.
- **Size:** S–M.
- **Done/exit criteria:** No exact monetary/quota claims without a future official
  stable source and a separately approved contract.

#### P2-C — Optional local list/show/prune

- **User job:** Find and deliberately remove old locally managed job records after the
  lifecycle format is stable.
- **Intended surface:** `job.sh list|show|prune`, limited to a single explicit managed
  state root. `list`/`show` are read-only; `prune` requires exact job IDs and shows
  targets before deletion.
- **Dependencies:** Safe lifecycle plus a demonstrated accumulation problem. Do not
  implement speculatively.
- **Trust boundary:** Never scan arbitrary home/repository trees, infer ownership from
  names alone, remove `gate-passed`/accepted/routed or changed-since-verification
  worktrees, remove any uncommitted state not exactly matching its hash-bound rejected
  receipt, or run on a timer. Every prune remains an explicitly approved destructive
  action for exact job IDs.
- **Minimum accept tests:** Bound stale rejected records are listed and an explicitly
  approved prune removes only records whose current state still matches the recorded
  rejected digest.
- **Minimum reject tests:** Broad root, symlink escape, unknown/active/
  `gate-passed`/accepted/routed job, changed or unbound state, ambiguous ID, missing
  approval, implicit age-only deletion, and background scheduling.
- **Docs and AGENTS impact:** Lifecycle docs and destructive-action lesson only when a
  real need and implementation exist.
- **Size:** M.
- **Done/exit criteria:** Implement only after lifecycle usage supplies evidence that
  manual cleanup is a recurring problem.

### Deferred or rejected

- **Cryptographic signing — deferred.** Receipt v1 may expose deterministic SHA-256
  digests, which prove byte equality only. Signing needs a threat model, identity and
  key-discovery policy, revocation, CI key custody, and explicit approval of an
  external signer/tool. Never invent a home-grown signature or call a self-signed
  digest trusted provenance.
- **Async jobs, polling, background daemon, MCP server — rejected.** They add process
  lifecycle, retention, authentication, and cleanup boundaries while duplicating the
  main competitor niche.
- **Dashboard or cloud service — rejected.** It would create storage, hosting,
  authentication, privacy, and telemetry obligations unrelated to the local gate.
- **Windows parity race — deferred.** Do not claim parity until maintainers can run
  native adversarial suites and own the support burden. Portable code remains welcome,
  but macOS correctness takes priority.
- **Multiple worker backends — rejected for this product direction.** It would dilute
  agy-specific ground truth and compatibility review.
- **Automatic tier/model selection, thinking/effort controls, or quota routing —
  rejected.** Recommendations remain advisory and caller selection remains explicit.
- **Automatic commit, push, PR, merge, release, issue submission, or deployment —
  rejected.** These remain deliberate user-owned workflows with separate approvals.
- **Escalating permission, authentication, scope-policy, invalid-contract,
  untrusted-claim, or human-required outcomes — rejected.** More model spend cannot
  repair those boundaries.

## Approval gates

Roadmap priority is not authorization. Apply these gates independently:

1. **Feature implementation:** fresh explicit approval for one named slice.
2. **External data/live model:** name the repository and paths sent through agy and
   obtain explicit approval before a live dispatch or benchmark.
3. **Destructive local lifecycle:** allow cleanup only for the exact hash-bound state
   recorded as rejected and disposable, then re-derive its digest and obtain explicit
   user approval for those exact job/worktree/branch targets. Refuse every other
   state; cleanup is never authorized merely because a job is old or uncommitted.
4. **GitHub:** staging and local commits may occur only when requested; push, PR,
   merge, and release each follow the user's explicit authorization boundary.
5. **External distribution/search:** marketplace, directory, Search Console, or other
   service enablement remains out of scope unless newly approved. The current product
   direction is public GitHub distribution.
6. **Signing:** requires an approved threat model and signer dependency before code.

## Honest success measures

Establish a dated baseline before the first P0 release, then review at 30, 60, and 90
days. Do not add install beacons, hidden analytics, fake installs, fake stars, paid
reviews, or automated promotional submissions.

### 30 days — onboarding and proof

- Measure median fresh-clone-to-offline-proof time in small opt-in sessions; target
  under 10 minutes and keep P0-D itself under 60 seconds.
- Record whether Doctor identifies the real blocker before any paid dispatch; report
  misses as product defects, not user error.
- Require 100% of published P0 examples to identify exact suite, fixture, base, and
  bounded claim. No example may imply general correctness/security.
- Track GitHub unique visitors and unique cloners around the release as interest only;
  never call a clone an install or activation.

### 60 days — qualified external use

- Count distinct public external repositories or opt-in users that demonstrate a
  valid receipt or starter proof. Verify each signal manually instead of inferring it
  from stars.
- Measure the share of inbound bug reports that include sanitized Doctor output or a
  receipt identifier and sufficient reproduction data.
- Review GitHub Traffic referral domains and, if separately approved and configured,
  Search Console non-branded impressions/clicks for relevant delegation and evidence
  queries. Report trends, not guaranteed ranking.
- Keep zero regressions in the existing adversarial gate suite and zero external
  actions performed by a reporter or lifecycle command.

### 90 days — reusable evidence ecosystem

- Count verified external workflows that link to or run the conformance kit or local
  reporter. A public repository reference is stronger than a raw star.
- Require the full conformance kit to reject every deliberately trusting reference
  gate and accept the maintained implementation.
- Publish benchmark claims only when every result binds exact public fixture, base,
  selected tier, attempts, tool versions, policy, and driver verification.
- Track accepted-real-candidate evidence per persona without converting one success
  into a universal quality percentage.
- Reassess P2 from observed friction. Do not build profiles, pruning, quota, or
  signing merely because they appear on this roadmap.

## First implementation recommendation

Implement **P0-A Evidence Receipt v1 only** as the next isolated feature slice. Do
not include the renderer, Doctor, proof demo, lifecycle, CI formats, profiles, usage,
benchmarking, or signing in that change. Before any code begins, obtain a fresh user
approval naming P0-A and preserve the normal independent implementation and
verification-agent split.
