# Product roadmap

This document describes dependency-ordered work. The current command surface and
verified limitations remain in [README.md](../README.md). An item explicitly marked
implemented has code, adversarial tests, and documentation in its isolated slice; it
is not a released/public capability until that slice is reviewed and merged.

Compatibility reconciliation remains a prerequisite for model/effort selection and
portable receipt work. The offline starter proof depends only on the maintained gate
and can remain an independent slice. Starting any slice requires a fresh, explicit
approval; this roadmap does not authorize code, commit, push, pull-request, merge,
release, live model use, or another external action.

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
4. The caller selects the tier or explicit model/effort input. Recommendations remain
   visible and advisory, with `recommendation_only: true` and `applied: false`.
5. Never add automatic tier/model/effort changes or invent a `--thinking-level`
   control. After G0 reconciles the exact agy contract, G1 may expose agy's real
   model and effort vocabulary as explicit wrapper choices only. The wrapper resolves
   a verified pair to one exact agy model slug; it does not assume agy's two CLI
   selectors compose. Retries keep the caller's resolved selection byte-for-byte.
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

The accepted agy `1.1.10` reconciliation combines documented `--effort` and `models`
surfaces with one sandbox-correct read-only model inventory. The exact 11-slug list,
its SHA-256, reviewed source revision, and bounded behavior limits live in
[`../compat/reviews/agy-1.1.10.md`](../compat/reviews/agy-1.1.10.md). Agent and plugin
catalogs were not part of that bounded review and remain outside this contract.

This corrects the inventory without turning advertised flags or historical failure
behavior into broader agy `1.1.10` promises:

- Do not expose `--effort` before G0 reconciles official releases/source/docs with a
  sandbox-correct inventory and bounded behavior tests. G1 may then expose the same
  vocabulary as a wrapper input; it must not infer effort, invent a thinking-level
  flag, or imply that both agy selectors are forwarded.
- Do not add dynamic model/persona discovery from help text alone.
- Do not infer authentication from a single failure or invent `agy auth`. Probe only
  documented commands and validate their expected semantic output; neither an unknown
  subcommand's exit code nor generic usage text is compatibility evidence.
- Do not assume agy's separate `--model` and `--effort` flags compose safely.
  Subsequent bounded `1.1.10` inventory evidence advertises compound slugs. Official
  `1.1.10` source and documentation are now available for human reconciliation, but
  this repository has not yet completed evidence that establishes dual-selector
  composition or precedence. G1 therefore resolves a verified base/effort pair to
  one exact advertised slug and sends one `--model`.
- Any exposure of newly advertised agy behavior remains a separate slice requiring
  official docs, official source, a sandbox-correct live inventory, a bounded real
  job, paired offline tests, and explicit approval.

## Release groups and slices

Each slice below is independently reviewable. A later slice must not be smuggled into
an earlier implementation because it shares a schema or helper.

### v0.2.0 release scope

Version 0.2.0 releases the completed provider-independent roadmap through P2-A:
G0/G1, P0-A through P0-D, P1-A through P1-E, and P2-A. P2-B and P2-C are not release
blockers and are deferred rather than left as active implementation goals. Their
sections retain the exact evidence and policy prerequisites required before either
may be reconsidered. Deferral does not weaken the active agy `1.1.10` baseline,
advance the unreconciled `1.1.11` evidence, or claim live provider or cleanup
behavior.

### G0 — Compatibility Reconciliation & Watch

**G0-F2 status:** Provider-independent transport hardening is implemented, merged, and
offline-verified. Read-only project/agy/Codex release and source observations now use
exact fixed GitHub REST paths with no ambient proxy or redirect path, and a bounded
process-group supervisor also contains installed version probes. Check/watch makes no
Git network request. The explicit `apply` fetch remains a separately authorized
ambient-Git transport path and is not claimed hardened by this slice. No agy `1.1.11`
version, revision, review date, manifest, or matrix record is advanced; `1.1.10`
remains the active fail-closed baseline until separately authorized inventory and
provider evidence is accepted.

The provider-independent inventory parser is also implemented offline. It treats
each line as one semantic inventory entry, requires complete one-time coverage of the
11 exact reviewed slugs, and rejects unknown reviewed-provider tokens,
generic-regex aliases, or prefix matches. In
particular, `gpt-oss` is accepted only as display text on the same line as
`gpt-oss-120b-medium`. Synthetic tests pin the corrected canonical-slug hash without
checking provider output into the repository. This does not close the missing
installed-version/executable evidence binding or advance any `1.1.11` record.

The provider-independent version-attestation prerequisite now has one canonical
fixed-profile runner plus its persistent mutation harness. The runner owns the exact
snapshot-backed version-only Popen path and a synthetic-only self-test; the harness
binds its exact source bytes and digest before import. Their 157- and 55-case suites
replace one-off inline runners with bounded process-group ownership, inode-bound
durable publication, atomic lifecycle completion, and paired weakened controls. They
invoke no agy, provider, network, or private production evidence. This closes the
offline version provenance prerequisite only.

A separate canonical models-inventory runner now binds one exact `models` Popen to
an accepted version binding and the same attested snapshot. Its 78-case offline suite
uses synthetic executables only, pins the exact 11-line semantic parser and corrected
normalized hash, enforces 25-second/64-KiB bounds and private detached publication,
and kills mutations of the executable override, logical argv, bounds, parser,
version-binding digest, and completion marker. It never exposes `/model`, `/effort`,
selector, retry, or provider-job surfaces. Running it against retained production
evidence remains a separate explicit authorization and independent review; the
checked-in `1.1.10` metadata and fail-closed matrix remain unchanged meanwhile.

Startup rejection now emits one capped canonical, path-redacted diagnostic line from
the same evaluator that owns the boolean decision. This is evidence for reconciling a
runner-image mismatch; it does not make that environment trusted or satisfy the gate.
The fixed `/usr/bin/python3 -I -S -B` boundary explicitly trusts the selected reviewed
Apple interpreter, hosted image, local owner, and OS administrators. Canonical
family/component shape, alias/target identity, executable/no-setid mode, and no
world-writable directory or resolved executable remain enforced; UID/GID and
owner/group writability are diagnostics only. This is not same-user or hostile-PR
tamper resistance, binary
provenance, code-signing verification, or OS attestation.

- **User job:** Learn that Codex or agy has drifted before a normal dispatch breaks,
  while keeping every check read-only and requiring a human to reconcile behavior.
- **Intended surface:** Extend the fixed-source compatibility contract in `update.sh`,
  `compat/sources.md`, and dependency-free metadata from agy to both agy and Codex.
  Use one explicit version, reviewed upstream revision, and last-reviewed date per
  tool; migrate the current shared `compat/last-reviewed.txt` to unambiguous per-tool
  records. Add a human-reviewed, dependency-free model/effort resolution matrix bound
  to the exact verified agy version and source revision. Each adjustable input pair
  maps to one exact advertised compound slug; fixed/no-level entries are recorded as
  non-adjustable. Add
  `.github/workflows/compatibility-watch.yml` as a separate weekly and
  `workflow_dispatch` macOS workflow. It is observational and is not a required pull
  request check.
- **Local check contract:** `./update.sh check` reports the installed version, the
  repository's human-verified baseline, official stable release/source drift, and
  official documentation-review age separately for agy and Codex. It exits `0` only
  when all required evidence is available and unchanged, `3` when evidence establishes
  drift or review is due, and `2` when network or source evidence is unavailable or
  malformed. Exit `2` is **inconclusive**, never green. The command changes no file,
  pulls nothing, applies nothing, and does not update its own baseline.
- **Read-only transport contract:** Project, agy, and Codex observations use only
  fixed `api.github.com` repository REST paths through a proxyless, redirect-rejecting,
  strict bounded JSON client. A fixed-profile supervisor incrementally caps both
  streams, applies a hard timeout, sanitizes output, and kills/reaps the entire child
  process group. Installed version probes use the same boundary. Mutation-sensitive
  offline controls prove that ambient Git URL rewrites, credentials, and proxies
  cannot redirect check/watch, which performs no Git network query. This does not
  make the explicit apply-time `git fetch` independent of ambient Git configuration.
- **Watch workflow contract:** The workflow runs only on `macos-latest`, declares
  `permissions: contents: read`, uses no secrets, installs no package or CLI, invokes
  no model, and performs no apply, pull, issue, PR, commit, or baseline write. A
  bounded GitHub Step Summary identifies each fixed source as unchanged, review-due,
  or evidence-unavailable without dumping fetched pages. Its command preserves the
  same `0`/`3`/`2` meanings; the workflow may surface nonzero status for maintainers
  but cannot open or modify anything. Scheduling it does not add it to the protected
  branch's required `test` check.
- **Fixed primary sources:** agy reconciliation binds the official
  [Antigravity source](https://github.com/google-antigravity/antigravity-cli),
  [releases](https://github.com/google-antigravity/antigravity-cli/releases),
  [changelog](https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md),
  and official CLI overview/usage pages already recorded in `compat/sources.md`.
  Codex reconciliation binds the official
  [Codex source and releases](https://github.com/openai/codex/releases),
  [Codex changelog](https://developers.openai.com/codex/changelog), and
  [Codex CLI reference](https://developers.openai.com/codex/cli/reference). Production
  URLs, review intervals, release channels, and upstream repositories remain fixed in
  the runtime and are not environment-overridable.
- **Official distribution canary:** The agy evidence set also observes the fixed
  official `darwin_arm64` updater manifest. The stdlib-only checker disables proxies,
  rejects redirects and oversized or malformed responses, validates the exact
  version/archive URL/SHA-512 tuple, and never requests the archive. Its checked-in
  tuple is an observational same-version change detector, not a verified release,
  source revision, signature, or baseline. Official release, source, documentation,
  and distribution evidence expose `1.1.10`. They were inputs to the separately
  accepted human reconciliation; the canary alone did not advance the now-reviewed
  `1.1.10` baseline or activate G1.
- **Baseline advancement:** A maintainer may advance either verified baseline only
  after reconciling official docs, release notes, and source; regenerating the local
  `./ground-truth.sh` evidence for agy and equivalent documented Codex CLI inventory;
  running every offline suite and syntax/compile/diff check; and recording the exact
  reviewed revisions. If behavior affecting dispatch changed, a bounded job against
  an explicit public fixture is a separate live-data approval, not part of the watch.
  The watch never performs this reconciliation. agy `1.1.10` was advanced only after
  its official evidence, installed inventory, bounded jobs, and offline gates were
  human-reviewed. Any later version or source movement returns the result to
  drift-review until another reconciliation is accepted.
- **Resolution-matrix rule:** G0 derives model-specific effort support and its single
  exact output slug from the verified `agy models` inventory, agy docs/source, and
  bounded CLI behavior—not from a provider API table or a model-name guess. The
  matrix records its agy version and source revision. Any agy version/source drift
  makes it stale and keeps effort resolution disabled until human reconciliation.
  The verified `1.1.10` inventory exposes compound slugs: Gemini 3.6 Flash and Gemini
  3.5 Flash have low/medium/high; Gemini 3.1 Pro has low/high but **not medium**.
  Sonnet is no-level; the advertised Opus thinking slug and GPT medium-labelled slug
  are fixed model choices, not adjustable effort pairs. G0 binds those exact entries
  as compatibility metadata; the wrapper does not consume the mappings until G1.
- **Current-behavior correction:** Implementation updates README and AGENTS guidance
  to say that probes must validate documented commands and expected semantic content,
  never an unknown subcommand's exit or usage output. It also records that agy has a
  real `--effort`, while this wrapper exposes no effort control until G1. Official
  `1.1.10` source was reviewed, but the reconciliation did not prove dual-selector
  composition: production code sends one resolved model slug and cannot combine an
  effort-bearing slug with agy's separate effort flag without a later, separately
  approved evidence gate.
- **Model option decision gate:** `gemini-3.6-flash-high` is already selectable as a
  raw custom `--tier` label. It remains unranked and non-escalating; no `bulk`/`hard`
  mapping changes and no effort flag are part of G0. Google's official
  [model catalog](https://ai.google.dev/gemini-api/docs/models) describes Gemini 3.6
  Flash as a speed/intelligence balance and Gemini 3.1 Pro as the advanced model for
  complex reasoning and coding. The official
  [thinking guide](https://ai.google.dev/gemini-api/docs/generate-content/thinking)
  shows real but model-specific effort levels, and
  [pricing](https://ai.google.dev/gemini-api/docs/pricing) distinguishes model and
  thinking usage. Those API facts must not be copied into the agy resolution matrix;
  they do not prove the agy CLI composition, relative quality, or effective
  subscription cost for this wrapper. A discoverable `flash-high` alias is a later
  isolated decision after G0/G1 compatibility tests; changing a default or
  recommendation order additionally requires the pre-registered benchmark below.
- **Dependencies:** Existing read-only `update.sh check`, fixed compatibility metadata,
  `ground-truth.sh`, Bash 3.2, Python 3 standard library, git, and GitHub-hosted macOS.
  No runtime dependency, provider credential, or paid quota is introduced.
- **Trust boundary:** Release names, help text, provider prose, and worker output are
  signals for review, not permission to update code or metadata. Missing network
  evidence cannot be collapsed into unchanged. The watcher cannot authorize model
  selection, acceptance, baseline advancement, dispatch, or an external action.
- **Minimum accept tests:** Fixed fake official sources unchanged return `0`; installed
  versus verified differences and stale review dates are reported separately and
  return `3`; unavailable network returns `2` with an inconclusive label; absent
  absent future-version evidence retains `1.1.10` and AMBER; version-bound resolution
  fixtures reproduce every documented pair-to-compound-slug mapping, preserve fixed
  no-level/thinking/medium-labelled entries, and mark drift stale; a raw
  `gemini-3.6-flash-high` selection remains pass-through, unranked, recommendation-only,
  and non-escalating; workflow fixtures prove weekly/manual triggers, macOS, read-only
  permission, bounded summary, and no mutation; fixed-manifest fixtures pair exact
  transport/schema/URL/hash acceptance with redirect, timeout, oversize,
  duplicate/extra/malformed field, archive-policy, and same-version build/hash
  rejection while proving that no archive request occurs.
- **Minimum reject tests:** Green on missing evidence; automatic baseline edits;
  environment-overridden source/review policy; malformed or future-dated metadata;
  treating unknown-command exit/usage as support; secret access, installation, model
  calls, `apply`, `pull`, GitHub writes, or required-PR-check coupling in the watcher;
  automatic issue/PR creation; tier remapping; alias creation; or an effort flag.
  Reject manifest redirects, missing or conflicting length/type metadata, invalid
  UTF-8/JSON/schema/SemVer/SHA-512, unexpected archive origins or paths, test/runtime
  source overrides, and any archive request.
  Reject a matrix with an unbound/mismatched version or revision, an unsupported pair,
  a provider-table-only claim, an inferred capability for an unknown model, a missing
  exact output slug, or an adjustable effort entry for a fixed/no-level model.
  Reject alternate GitHub repositories or endpoint shapes, redirects, proxies,
  duplicate/oversized/malformed REST evidence, unbounded version output, timeout,
  descendant pipe retention, or swallowed HUP/INT/TERM status in read-only probes.
- **Docs and AGENTS impact:** README compatibility semantics and limitations,
  `compat/sources.md`, REPO_MAP ownership/data flow, lessons learned on inconclusive
  evidence, and concise durable AGENTS probing rules. Run `agents-md-auditor` before
  and after those future guidance edits.
- **Size:** M.
- **Done/exit criteria:** Both tools have fixed, human-reviewed baselines; all three
  outcomes are adversarially tested; the macOS watcher is read-only and separately
  observable; all existing suites stay green; docs do not claim live compatibility
  beyond evidence; and an independent verifier confirms zero write/escalation path.
- **Success measures:** Zero false-green results when official evidence is unavailable;
  compatibility drift is classified by the next weekly run; each baseline advance
  links exact primary evidence and completed gates; and no watcher run changes a file,
  opens an item, invokes a model, or changes required branch checks.

### G1 — Explicit Model & Effort Selection

**Status:** Implemented, merged, and offline-verified; release remains separately
approval-gated.

- **User job:** Select an exact advertised agy model or a verified base-model/effort
  pair directly, without disguising the choice as a tier or allowing a recommendation
  to change it.
- **Sequence gate:** Start only after G0 has reconciled the exact agy `1.1.10` (or
  later explicitly verified) CLI/source behavior. G1 precedes any `flash-high` alias,
  performance ranking, or default/recommendation remap and must be its own pull request.
- **Intended surface:** Add wrapper CLI `--model MODEL` and
  `--effort low|medium|high`, using agy's real vocabulary but never inventing
  `--thinking-level`. These are wrapper inputs, not a promise to forward both agy
  flags. Preserve `--tier` named values, raw-label pass-through, and the no-option
  default exactly. Add
  `AGY_WORKER_MODEL` and `AGY_WORKER_EFFORT` only with the strict conflict contract
  below. Canonical runtime, root compatibility wrapper, public skill copy, validators,
  recommendation schemas/renderers, and later receipt/report schemas carry the same
  resolved selection contract.
- **Selection and precedence contract:** There is no silent precedence. Each option
  may be supplied by CLI or its matching environment variable, never both—even when
  the values match. Any explicit tier source (`--tier` or `AGY_WORKER_TIER`) is mutually
  exclusive with every explicit model/effort source. With no explicit selector, the
  existing implicit `bulk` default remains. Direct mode uses one exact model source;
  effort always requires a base-model input. Duplicate CLI occurrences, empty values,
  unknown effort values, and cross-source conflicts fail before dispatch with usage
  exit `64`.
- **Resolution and dispatch safety:** Exact advertised compound/no-level slugs supplied
  through `--model` alone remain allowed and unranked; agy receives one
  `--model EXACT_SLUG`. A base-model plus effort is allowed only when the G0 matrix for
  the installed verified agy version maps that exact pair to one exact advertised
  compound slug; agy again receives only `--model RESOLVED_SLUG`. The globally accepted
  effort spelling does not imply every model accepts all three values: verify Flash
  low/medium/high separately and Pro low/high separately while rejecting Pro medium.
  Sonnet no-level, Opus thinking-labelled, and GPT medium-labelled slugs are fixed
  exact-model choices and reject any effort input. Compound slug plus effort, unknown
  model/version, unsupported pair, stale matrix, missing mapping, or ambiguous form
  fails before dispatch with no fallback, slug surgery, normalization, or base-model
  guess.
- **Dual-selector evidence gate:** G1 never forwards agy's `--effort`. Passing both
  `--model` and `--effort` to agy requires a later isolated slice with official source
  or a separately approved bounded test proving exact composition, precedence, failure
  semantics, and retry behavior for the pinned version. That future evidence cannot
  silently replace the safe single-selector mapping.
- **Persistence and evidence:** Resolve selection once before attempt one and retain
  the exact tier or user model/effort provenance, matrix revision, and resolved agy
  slug across every retry. Pre-dispatch and post-gate recommendations, Evidence Receipt
  v1, and the Human Report represent selected tier, user model, user effort, and
  resolved agy model as distinct optional fields. A custom model or effort remains
  unranked; recommendation output stays `recommendation_only: true`, `applied: false`,
  and cannot alter or redispatch the selection.
- **Non-escalatable outcomes:** Permission, authentication, scope-policy,
  invalid-contract, untrusted-claim, and human-required failures remain
  non-escalatable regardless of selected model or effort. Higher effort is never
  proposed as a repair for those outcomes.
- **Dependencies:** Completed G0 baseline and resolution matrix, existing dispatcher
  parsing/model assembly, advisory recommender, and the schema/report surfaces present
  when G1 starts. No new dependency or provider lookup.
- **Minimum accept tests:** Legacy named tiers, raw `--tier` labels, and implicit bulk
  remain byte-compatible; each exact advertised slug reaches agy as one exact
  `--model`; every matrix-admitted base/effort pair has its own test and reaches agy as
  one exact resolved compound `--model`; fixed Sonnet/Opus/GPT choices remain exact and
  non-adjustable; retries preserve the same matrix revision and resolved slug;
  recommendations and receipts/reports label user and resolved selection without
  ranking or applying it.
- **Minimum reject tests:** CLI/env duplicates; tier plus model or effort across any
  source; repeated selector; empty/invalid model or effort; effort without a base
  model; compound/fixed/no-level slug plus effort; unsupported pair (including Pro
  medium); adjustable Sonnet, Opus, or GPT; unknown model or version; stale/unbound
  matrix; missing exact output; inferred capability; dual-selector forwarding;
  invented thinking flag; fallback to a nearby level/model; retry mutation;
  recommendation-driven change; or escalation of a non-escalatable failure. Assert
  that fake agy is never invoked for every preflight rejection.
- **Docs and AGENTS impact:** README option/precedence tables and examples, public
  skill SKILL.md, REPO_MAP data flow, lessons learned on single-slug resolution and
  unproven dual selectors, compatibility metadata, and a concise AGENTS rule forbidding
  inferred effort, silent override, and unverified two-argument composition.
  Run `agents-md-auditor` before and after those future guidance edits.
- **Size:** M.
- **Done/exit criteria:** Exact G0-backed single-slug mappings and conflict behavior are
  documented and adversarially tested on macOS Bash 3.2; all prior suites stay green;
  raw tier compatibility remains; agy receives no separate effort argument; no
  recommendation changes selection; and independent verification confirms no
  ambiguous, automatic, fallback, or dual-selector model/effort path.
- **Later alias/ranking gate:** A named `flash-high` alias can be proposed only after
  G0/G1 prove its exact agy composition. Mapping `bulk`/`hard`, recommending Flash-high
  over Pro-high, or changing a default additionally requires a pre-registered bounded
  benchmark using fixed public fixtures, identical scope and verifier, pinned tool
  versions, equal attempts, captured latency/provider telemetry, and explicit live-use
  approval. Official model descriptions or a one-off result are not a ranking.

### P0 — make the evidence boundary visible and usable

#### P0-A — Evidence Receipt v1

**Status:** Implemented, merged, and offline-verified; release remains separately
approval-gated.

- **User job:** Preserve what the driver checked, against which immutable base and
  policy, and what the gate concluded without sharing source, prompts, raw logs, or
  worker prose.
- **Intended surface:** Add canonical
  `skills/agy-worker/runtime/verify-job.sh`, root `verify-job.sh`,
  `skills/agy-worker/runtime/schemas/evidence-receipt.schema.json`, and a
  dependency-free receipt validator. The wrapper creates a private temporary receipt
  and pre-opens a driver-owned structured-evidence sink, then invokes the gate with an
  internal capability-bound `--evidence-fd FD` handoff. This evidence mode is owned by
  `verify-job.sh`, not supported as an arbitrary direct-call interface. When that
  option is absent, `qa-gate.sh` output, side effects, and exit behavior remain
  identical to today. With it, the gate writes
  exactly one bounded JSON handoff to the already-open descriptor; it never owns or
  chooses the destination path. Optional `--pre-recommendation FILE` may bind an
  already-rendered pre-dispatch advisory. The receipt command never generates or
  applies a recommendation.
- **Gate evidence handoff:** The structured handoff is the receipt's bounded source
  for driver-derived facts. It includes the hash of the exact envelope snapshot the
  gate validated, the resolved immutable base, the gate's internal initial and final
  candidate-state digests, and its exact outcome and exit. The wrapper validates the
  complete handoff, cross-checks it against its immutable inputs, and refuses missing,
  malformed, duplicate, or mismatched evidence. It never parses gate prose or
  reconstructs candidate truth from outer-wrapper observations alone.
- **Receipt v1 minimum:** Version and kind; gate-resolved immutable base; hash of the
  exact envelope snapshot validated by the gate; ordered path-policy hash; verifier
  labels and command hashes; the gate-supplied initial and final candidate-state
  digests; actual gate exit/outcome; a verdict restricted to `gate-passed`, `rejected`,
  or `routed`; optional caller-selection object with exactly one resolved mode,
  distinct tier/user-model/user-effort values, CLI/environment/default provenance,
  matrix revision when used, and exact resolved agy model slug under the accepted G1
  contract; optional validated pre-dispatch advisory retaining its
  rationale, controlled driver evidence, relative cost impact,
  `recommendation_only: true`, `applied: false`, and `stage: pre-dispatch`;
  `gate_authority: qa-gate`; and an explicit statement that the receipt is unsigned
  and recommendations did not participate in acceptance.
- **Exit and publication contract:** Wrapper preflight/input errors exit `64` before
  the gate and publish no receipt. Gate outcomes `0` and `10`–`15` may publish a
  receipt with the exact `gate_exit`; after successful receipt validation, file
  `fsync`, atomic publication, and parent-directory durability, the wrapper returns
  that gate exit. Gate exit `64` publishes no receipt and returns `64`. A missing or
  mismatched handoff, unknown gate exit, signal, or other internal protocol failure
  publishes no receipt and returns reserved exit `70`. Receipt validation, `fsync`,
  or atomic-publication failure removes the private temporary file, publishes no
  partial receipt, and returns reserved exit `74`. The wrapper never returns `0`
  unless the gate returned `0` **and** the receipt was durably published.
- **Exclude:** Diff/source content, prompt, worker summary/confidence, raw verifier
  commands or output, credentials, absolute repository paths, provider pricing, and
  an applied recommendation.
- **Dependencies:** Existing `qa-gate.sh`, envelope validator, accepted G1 selection
  contract, Python SHA-256, and git.
- **Trust boundary:** A receipt records a gate execution. It must not reproduce gate
  acceptance logic, treat its own existence as acceptance, use `accepted` as a
  verdict, or map any nonzero gate result to `gate-passed`. A receipt path inside the
  audited repository is rejected. Schema validation, canonical serialization, and
  internal-invariant checks can detect malformed or inconsistent receipts, but an
  unsigned receipt cannot detect arbitrary schema-valid tampering by an actor who can
  rewrite the document and recompute its embedded hashes. Validation can detect a
  mismatch when the caller separately supplies the bound envelope, candidate
  artifact, pre-dispatch advisory, or a trusted external digest. Receipt v1 makes no
  self-hash authenticity or signing claim; signing remains deferred to an approved
  threat model and external signer.
- **Minimum accept tests:** An honest edit with passing driver verification yields a
  durably published, schema-valid receipt with verdict `gate-passed`, actual
  `gate_exit: 0`, the gate's exact envelope snapshot hash, resolved base, and internal
  initial/final state digests; the wrapper returns `0`. Each normal gate result
  `10`–`14` durably publishes verdict `rejected`, result `15` publishes `routed`, and
  the wrapper returns that exact gate exit. A valid pre-dispatch advisory is bound
  without changing the selected input, matrix revision, resolved agy slug, or gate
  result. The tests never call a candidate accepted before human review. Direct
  `qa-gate.sh` calls without the internal evidence capability retain their
  current stdout/stderr and exit contract.
- **Minimum reject tests:** Scope failure, malformed envelope, untrusted command/test
  claim, missing edits, verifier failure/mutation, and human-required outcome retain
  their gate classification and are never rendered as acceptance. Reject overwrite,
  symlink target, in-repository output, unknown schema version, inconsistent receipt,
  separately bound artifact/digest mismatch, malformed or duplicate handoff, envelope
  snapshot/base/state/outcome/exit mismatch, post-gate or cross-stage advisory input,
  selected-input/matrix/resolved-slug mismatch, ambiguous selector provenance, or an
  advisory that claims it was applied. Wrapper/gate
  preflight `64`, unknown exit, signal, missing evidence, internal `70`, and durable
  publication `74` paths publish no receipt; injected validation, `fsync`, rename, and
  parent-directory durability failures leave no final or partial receipt. An unsigned
  receipt rewritten with recomputed hashes must not be falsely described as
  tamper-evident without a separately trusted binding.
- **Docs and AGENTS impact:** Update README, SKILL, privacy disclosure, REPO_MAP, and
  architectural lessons only after implementation. AGENTS then gains the actual new
  suite/count and a concise durable rule that receipts do not replace gate or human
  review. Run `agents-md-auditor` before and after.
- **Size:** M.
- **Done/exit criteria:** One isolated PR; no behavior change when `verify-job.sh` is
  unused and no behavior/output/exit change when `qa-gate.sh` is called without its
  optional evidence handoff; all current suites plus the `0`, `10`–`15`, `64`, `70`,
  and `74` receipt matrix green; no receipt on unknown/signal/missing-evidence or
  publication failure; no raw private data in the receipt; no post-gate recommendation
  binding in P0-A; and an independent verifier confirms no weaker gate path.

The existing post-gate recommender remains external and unchanged. P0-A does not
auto-run it or bind its output after a synchronous gate completes. A later, separate
contract may bind a post-gate advisory without rewriting the canonical one-pass
receipt or creating chronology ambiguity.

#### P0-B — Human Report renderer

**Status:** Implemented, merged, and offline-verified; release remains separately
approval-gated. Receipt-only selection and recommendation-record
validation is side-effect-free; canonical recommendation generation remains only an
explicit pre-gate publication-input check.

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
- **Minimum reject tests:** Malformed, internally inconsistent, unsupported-version,
  control-character, Markdown-link-injection, forbidden-private-field, or separately
  bound artifact/trusted-digest mismatch produces no report. Do not claim detection
  of arbitrary unsigned receipt rewriting with recomputed hashes.
- **Docs and AGENTS impact:** Add report examples to README/SKILL and ownership to
  REPO_MAP; update privacy language. Change AGENTS only for an actual suite/count.
- **Size:** S.
- **Done/exit criteria:** Deterministic output; no raw command, source, prompt, log, or
  absolute-path disclosure; renderer has no execution or submission capability.

#### P0-C — Read-only Doctor

**Status:** Implemented and offline-verified; live authentication/provider readiness
remains intentionally outside the Doctor contract.

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

**Status:** Implemented as the bounded starter subset retained by conformance v1.

- **User job:** See the project's differentiator in under one minute without agy,
  credentials, network, or API credits.
- **Intended surface:** Add repository-only `proof-demo.sh` and a minimal fixture
  pair, now retained as the `conformance/v1/` starter subset. It creates a temporary
  Git repository, demonstrates one
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

#### P1-A — Safe local lifecycle (implemented)

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
- **Implemented boundary:** The canonical portable runtime owns the v1 state machine,
  shared candidate-state digest, Receipt validation, progress reconciliation, and
  compare-and-delete cleanup. The root command is only a compatibility wrapper.
  Ninety-five offline cases cover accepted and rejected lifecycle paths, canonical
  branch authority, hook/filter-free fixed Git execution, durability, stale approval,
  ref-error separation, deletion-domain, signal, and weakened-authority mutations.
  Cleanup never follows symlinks, never deletes a commit, and retains the cleaned
  state file.

#### P1-B — Full public conformance kit (implemented)

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
- **Implemented boundary:** The repository-only v1 kit binds eleven exact synthetic
  gate cases, manifest/source hashes, fixed verifier kinds, private disposable Git
  repositories, and per-process time/output limits. Seventy-nine offline adversarial
  cases reject source drift and permissive gates while proving HUP/INT/TERM cleanup,
  FD-relative no-follow deletion, cleanup bounds, and fail-closed residual handling
  under an explicit gate/loaded-code/local-owner/same-UID/OS-admin TCB. The runner
  never scans for or chases a drifted root and claims no same-user tamper resistance.
  The claim is direct gate fixture compatibility only; it excludes Receipt/report,
  lifecycle, dispatch, provider, real-job quality, security, and human acceptance.

#### P1-C — Reproducible offline benchmark harness (implemented)

- **User job:** Compare releases, caller-selected model inputs, or personas on fixed
  bounded tasks using gate observations rather than subjective worker summaries.
- **Implemented surface:** `benchmark.sh prepare|run|report`, frozen
  `benchmarks/v1/manifest.json`, an explicit external owner-`0700` result root, and
  `docs/BENCHMARKING.md`. No live/provider path is implemented.
- **Dependencies:** Receipt v1; lifecycle is useful but optional.
- **Trust boundary:** Every result binds exact fixture/base, either a clean source
  commit or the reviewed portable source revision/manifest, runner/schema/manifest/
  gate/wrapper hashes, caller selection, one-attempt policy, and validated Receipt
  v1 facts. No hidden retries or selector changes. The report is
  completeness facts only; it has no leaderboard, score, winner, or route.
- **Minimum accept tests:** The frozen offline fixture produces a deterministic report
  and exact Receipt v1 through the canonical gate.
- **Minimum reject tests:** Changed fixture hash, missing verifier, unpublished input,
  partial task set described as complete, hidden retry/input/resolution change, or
  result without exact version binding.
- **Docs and AGENTS impact:** Add BENCHMARKING document and README evidence link;
  update REPO_MAP. AGENTS updates only verified real/offline evidence boundaries, not
  one-off results.
- **Size:** L.
- **Implemented evidence:** One hundred four provider-independent cases cover frozen assets,
  clean-source/tool bindings, private no-overwrite publication, one attempt,
  Receipt/result/report coherence, partial/tampered results, selectors, privacy,
  lifecycle interruption, complete nested schema constraints, folder-only execution,
  and source-policy mutations.
- **Done/exit criteria:** Reproducible offline harness. Live execution remains an
  unimplemented, separately reviewed and approved future slice requiring accepted agy
  executable/version evidence plus explicit Google/Gemini data scope and cost.

#### P1-D — Persona evidence registry (implemented)

- **User job:** Distinguish offline persona contract coverage from honest escalation
  and accepted real-candidate evidence.
- **Implemented surface:** Validated `compat/personas/<name>.json` records and a generated
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
- **Implemented evidence:** Provider-independent cases cover the fixed
  allowlist, canonical and folder-only records, public P1-C hash bindings, frontmatter
  and dispatcher mode restrictions, deterministic reporting, all three state
  contracts, semantic Receipt/dispatch/tool/version/verifier/diff coherence, strict
  Git ancestry and blob/mode/allowlist rules, maintainer/human-review records, privacy,
  schema structure, and weakening mutations. Protected-main ancestry proves ordering,
  not reviewer identity or a signature. All
  shipped records remain `offline-only`; no historical run has the public bindings
  required for promotion.
- **Done/exit criteria:** Every shipped persona has precise evidence status; no
  stronger claim than its reproducible records support.

#### P1-E — CI-safe JSON, Markdown, and GitHub Step Summary reporter

**Status:** Implemented as an offline-only extension of the pure P0-B renderer; no
GitHub API, comment, upload, or implicit environment-file write was added.

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
- **Implemented evidence:** Eighty offline cases bind canonical bounded JSON,
  Markdown and Step Summary bytes, all three receipt verdicts, explicit private
  no-overwrite publication, workflow-command/Markdown rejection, environment-file
  non-discovery, signals, and weakening mutations.

Do not commit to SARIF: a gate run is not naturally a static-analysis result with
locations/rules. Do not add JUnit unless a concrete consumer first demonstrates a
semantically honest mapping; “job rejected” is not automatically a test-case failure.

### P2 — optional local ergonomics and telemetry

#### P2-A — Data-only workload profiles

**Status:** Implemented provider-independently as a fixed v1 data bundle and pure
list/show renderer. It performs no dispatch, repository discovery, routing, or
acceptance operation.

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
- **Implemented evidence:** Eighty-nine offline cases bind the exact three-profile
  manifest, canonical list/show bytes, maintained mode/persona pairs, closed
  repo-relative path-policy shapes, all caller-required fields, portable parity,
  schema/inventory/hash/mode/symlink/bound enforcement, hidden-source non-discovery,
  and weakened-policy mutations. No profile contains or obtains a selected value,
  executable command, path, authorization, dispatch, route, acceptance, or Git action.

#### P2-B — Provider-reported usage and latency

**Status:** Deferred; no implementation path exists within the current evidence
boundary. The repository's
historical agy `1.1.9` observation and synthetic test streams do not establish the
current contract for usage, duration, or turn fields. Unblocking requires a separately
approved, executable/version-bound one-attempt public synthetic run; owner-private raw
NDJSON; and a sanitized reviewed record binding event order and cardinality, exact
field names and types, null/missing behavior, nested usage keys, duplicate/failure
semantics, and invocation/source/version hashes. Official docs/source must be
reconciled, and any undocumented behavior remains explicitly version-pinned empirical
evidence. Until then no usage parser or schema may be treated as current.

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

**Status:** Deferred; no implementation path exists until recurring accumulation is
demonstrated and a managed-root contract is reviewed. The stable per-job lifecycle
does not yet define a canonical inventory root
or prove that manual cleanup is recurring friction. Unblocking requires opt-in,
sanitized evidence from explicit owner-private lifecycle roots showing repeated
retention or cleanup burden without paths or raw state, followed by an explicit
contract for the managed-root inventory, list/show scope, exact prune deletion domain,
and approval binding. Existing current-state, rejected-Receipt, and candidate-digest
checks remain mandatory; age alone never authorizes deletion.

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
- **Automatic tier/model/effort selection, inferred thinking controls, or quota
  routing — rejected.** Recommendations remain advisory and caller selection remains
  explicit. G1 may expose agy's verified `--model` and `--effort` only as direct,
  non-inferred caller controls; no `--thinking-level` is planned.
- **Automatic commit, push, PR, merge, release, issue submission, or deployment —
  rejected.** These remain deliberate user-owned workflows with separate approvals.
- **Escalating permission, authentication, scope-policy, invalid-contract,
  untrusted-claim, or human-required outcomes — rejected.** More model spend cannot
  repair those boundaries.

## Approval gates

Roadmap priority is not authorization. Apply these gates independently:

1. **Feature implementation:** fresh explicit approval for one named slice.
2. **Compatibility watch enablement:** merging or scheduling the weekly external
   watcher and changing a verified baseline each require explicit approval. A baseline
   change also requires the G0 reconciliation record; the watcher cannot approve it.
3. **External data/live model:** name the repository and paths sent through agy and
   obtain explicit approval before a live dispatch or benchmark.
4. **Destructive local lifecycle:** allow cleanup only for the exact hash-bound state
   recorded as rejected and disposable, then re-derive its digest and obtain explicit
   user approval for those exact job/worktree/branch targets. Refuse every other
   state; cleanup is never authorized merely because a job is old or uncommitted.
5. **GitHub:** staging and local commits may occur only when requested; push, PR,
   merge, and release each follow the user's explicit authorization boundary.
6. **External distribution/search:** marketplace, directory, Search Console, or other
   service enablement remains out of scope unless newly approved. The current product
   direction is public GitHub distribution.
7. **Signing:** requires an approved threat model and signer dependency before code.

## Honest success measures

Establish a dated baseline before the first P0 release, then review at 30, 60, and 90
days. Do not add install beacons, hidden analytics, fake installs, fake stars, paid
reviews, or automated promotional submissions.

### 30 days — onboarding and proof

- Require every scheduled G0 result to distinguish unchanged, review-due, and
  evidence-unavailable; target zero false green on missing official evidence.
- Measure median fresh-clone-to-offline-proof time in small opt-in sessions; target
  under 10 minutes and keep P0-D itself under 60 seconds.
- Record whether Doctor identifies the real blocker before any paid dispatch; report
  misses as product defects, not user error.
- Require 100% of published P0 examples to identify exact suite, fixture, base, and
  bounded claim. No example may imply general correctness/security.
- Track GitHub unique visitors and unique cloners around the release as interest only;
  never call a clone an install or activation.

### 60 days — qualified external use

- Measure compatibility-review lead time from first weekly drift signal to a recorded
  human disposition; do not count an automatic metadata change as resolution.
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

- Audit every compatibility baseline advance for fixed primary sources, ground-truth
  evidence, full offline gates, and any separately approved behavior-changing live
  fixture; target zero unreviewed advances and zero watcher mutations.
- Count verified external workflows that link to or run the conformance kit or local
  reporter. A public repository reference is stronger than a raw star.
- Require the full conformance kit to reject every deliberately trusting reference
  gate and accept the maintained implementation.
- Publish benchmark claims only when every result binds exact public fixture, base,
  selected input, matrix revision and resolved agy slug when applicable, attempts,
  tool versions, policy, and driver verification.
- Track accepted-real-candidate evidence per persona without converting one success
  into a universal quality percentage.
- Reassess P2 from observed friction. Do not build profiles, pruning, quota, or
  signing merely because they appear on this roadmap.

## Sequencing reminder

Keep compatibility reconciliation separate from model/effort selection and receipt
work. The offline starter proof may evolve independently because it relies only on
the maintained gate. Do not mix otherwise independent slices merely because they
touch shared documentation, and require fresh approval and independent verification
for each implementation.
