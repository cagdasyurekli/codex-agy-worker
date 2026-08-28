# agy-worker Campaign Quality Report — 2026-08-27

## Executive result

agy-worker's strongest quality is not that every delegated run succeeds; it is that
failure, acceptance, and publication are kept separate. During the v0.11.0 campaign,
the controller preserved a useful candidate, rejected unsupported activation, kept raw
account evidence private, and required Codex-owned verification. The final authorized
agy 1.1.22 models observation failed once, without retry, so 1.1.22 was not activated.
The active trust chain remains 1.1.16 and Codex 0.150.1 remains observation-only.

The campaign also exposed two concrete test/reporting bugs and two worthwhile product
improvements. The bugs are local-harness defects rather than confirmed runtime data-loss
or sandbox escapes. One additional existing feature request covers Codex orchestration
usage observation. The failed account capture is not classified as an agy runtime bug:
the sanitized evidence cannot establish whether the cause was authentication,
permission, quota, service, or another external condition.

## Sanitized campaign evidence snapshot

This report was finalized on 2026-08-27 against the published v0.11.0 release. The
release chain is bound to the following public evidence:

- PR #74 head: `871d7299987ff77ba382b39211371028a51bb4a9`;
- protected PR merge-ref test: run `33110439083`, successful;
- manual exact-head test: run `33111372507`, successful on the PR head;
- squash-merged `main`: `7fe59599ef26773f9ab6537e1ecf31ec8ddc00b9`;
- merged tree: `2636a911dc3839c75bb29642e4e1b5862399b1d0`;
- manual exact-main test: run `33115398617`, successful on the merged commit;
- annotated tag object: `c7b81a0d9659a7662eea14a42ef7b3dcb5fdc938`;
- tag dereference: `v0.11.0^{}` resolves to the exact merged `main` commit;
- published GitHub Release:
  `https://github.com/cagdasyurekli/codex-agy-worker/releases/tag/v0.11.0`.

The tag contains plugin version `0.11.0`, the agy 1.1.22 observation record and its
bounded capture tooling. It deliberately retains `compat/agy-verified-version.txt`,
the runtime copy, and the active model/effort matrix at agy 1.1.16. Publication is
therefore evidence of the approved observation-only scope, not 1.1.22 activation.

The final account event is identified as `agy-1.1.22-models-final-2026-08-27`:

- **Authority:** one account-backed `agy models` child, explicitly approved by the
  repository owner, with no retry.
- **Outcome:** child exit 1; `popen_count: 1`; no accepted or interpreted inventory;
  no metadata, model matrix, routing, or activation advance.
- **Sanitized failure record SHA-256:**
  `cab32a092e67b5199c1777e45f65623f703a94812b75a0732e7b3156302e9f77`.
- **Private stdout SHA-256:**
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
  (empty stream).
- **Private stderr SHA-256:**
  `00663fead5ee96eea3894a5397cc2f33ddad4c350322cd74ad91ddfa4dd64a9a`.
- **Limit:** These hashes prove equality to the retained private bytes; they do not
  disclose or classify the underlying provider/account failure and do not prove a
  functioning backend.

The candidate's review record is `compat/reviews/agy-1.1.22.md`. Raw streams remain
outside the repository in an owner-private evidence root. No raw stream content,
credential, account identity, or private filesystem path is reproduced here.

## Confirmed bugs

### 1. Updater aggregate can under-report nested test coverage

- **Status:** Open — [#70, “Bug: Updater suite can silently under-count nested manifest
  tests”](https://github.com/cagdasyurekli/codex-agy-worker/issues/70), read back from
  GitHub on 2026-08-27.
- **Observed:** The nested official-distribution suite grew from 64 to 65 cases. Its
  expected terminal line was updated, but the updater's separate aggregate increment
  remained 64. The suite exited successfully and reported 324 rather than 325 tests.
- **Impact:** Reporting integrity and documentation drift. The test still ran, so this
  did not hide a failing product behavior.
- **Local candidate:** The literal count and README/repository-map claims are corrected.
  The durable fix should remove the duplicated count or fail closed on inconsistency.

### 2. Provider-lease preflight regression test is scheduler-sensitive

- **Status:** Open — [#71, “Bug: Provider-lease preflight boundary test flakes under
  scheduler load”](https://github.com/cagdasyurekli/codex-agy-worker/issues/71), read
  back from GitHub on 2026-08-27.
- **Observed:** Two runtime-boundary fixtures used a 10 ms idle window while deliberately
  delaying local preflight. Under host load, they could fail before reaching the
  provider-lease assertion.
- **Impact:** Flaky canonical offline verification and slower release preparation; no
  production controller defect was established.
- **Local candidate:** The fixture idle window is 250 ms while the provider hard window
  remains 500 ms and the local preflight delay remains 750 ms. Production code and
  provider timeouts are unchanged.

### Previously reported and closed during the broader campaign

- [#64](https://github.com/cagdasyurekli/codex-agy-worker/issues/64) — partial Git clone
  failed before provider with a generic binding error and consumed a cycle.
- [#59](https://github.com/cagdasyurekli/codex-agy-worker/issues/59) — provider quota
  responses were classified as `agy_failed_unclassified`.
- [#57](https://github.com/cagdasyurekli/codex-agy-worker/issues/57) — repository-wide
  audits could claim completion without auditable coverage.

These closed issues are useful evidence that the product's failure semantics and
coverage claims have already improved. Their closure is not proof that every adjacent
provider or audit failure is classified.

## Improvement opportunities

### 1. Privacy-safe account capture failure categories

- **Status:** Open — [#72, “Feature: Add privacy-safe failure categories to account
  models capture”](https://github.com/cagdasyurekli/codex-agy-worker/issues/72), read
  back from GitHub on 2026-08-27.
- **Need:** The candidate's version-bound 1.1.22 capture wrapper recorded the one failed
  child without classifying its cause. This is campaign evidence, not a claim about the
  currently published 1.1.16 runner's exact failure-record surface.
- **Proposed boundary:** Add bounded `authentication`, `permission`, `quota`, `service`,
  `timeout`, and `unknown` categories. Keep raw streams private; never retry, switch
  models, advance metadata, alter routing, or infer inventory success from a category.
- **Value:** Maintainers can choose the right next action without publishing provider
  prose or weakening the no-retry activation gate.

### 2. Exact-head CI timing telemetry and safe sharding

- **Status:** Open — [#73, “Feature: Add exact-head CI timing telemetry and safe
  sharding”](https://github.com/cagdasyurekli/codex-agy-worker/issues/73), read back
  from GitHub on 2026-08-27.
- **Need:** The canonical offline gate is confidence-rich but long-running. Repeating it
  across many stacked PRs materially extends a release campaign.
- **Proposed boundary:** Emit bounded per-suite timings, then shard independent suites
  on the same exact PR-head SHA behind one required aggregate. Missing, duplicate,
  cancelled, or wrong-SHA results must fail closed. Do not shrink the suite inventory
  or reuse evidence across different commits.
- **Value:** Lower wall-clock release time and evidence-based optimization without
  claiming lower compute/provider cost or weakening acceptance.

### 3. Codex orchestration usage observation

- **Status:** Existing open request — [#69, “Feature: Observe Codex orchestration usage
  and benchmark delegation overhead”](https://github.com/cagdasyurekli/codex-agy-worker/issues/69),
  read back from GitHub on 2026-08-27. It is not duplicated by #72: #69 observes
  driver-side Codex usage, while #72 classifies a bounded agy account-capture failure.
- **Need:** Delegating to agy does not make Codex coordination, diff review, testing,
  waiting, and repair free. Raw/cache token totals do not prove billing or allowance
  consumption.
- **Boundary:** Report explicit thread/session counters separately, use account usage
  only when available and authorized, never infer money or remaining quota, and label
  token-only A/B results directional.

### 4. Release slicing is primarily a process decision

The initial many-PR stack provided narrow review boundaries but multiplied exact-head
CI waits. This is not, by itself, an agy-worker product bug. Prefer a small number of
cohesive release slices when changes share the same compatibility evidence, while
retaining separate authority for commit, push, PR, merge, tag, and release. CI sharding
can reduce wall time; it cannot make evidence from one SHA valid for another.

## What worked well

### Fail-closed compatibility and activation

- Installed/latest observations do not silently become active baselines.
- The failed 1.1.22 account capture did not advance the model matrix, inventory binding,
  routing authority, or release status.
- Active 1.1.16 and observed 1.1.22 can coexist without rewriting history.

### Privacy and authority boundaries

- Raw account stdout/stderr remained in an owner-private temporary evidence root.
- The public report binds hashes and limitations instead of provider prose,
  credentials, account identity, or local paths. An owner-private local capture result
  may name its private artifact root for the maintainer; that path is not public
  evidence and is intentionally omitted here.
- Auth, permission, quota, service, and human-decision failures do not trigger silent
  retries or model changes.

### Driver-owned acceptance

- Worker envelopes remain input, not proof. Codex inspects physical diffs and runs its
  own tests.
- `commands_run` and `tests_run` claims are treated as untrusted data.
- Project candidates can be preserved and repaired in the same conversation rather
  than discarded after one quality failure.

### Strong offline regression surface

- Focused suites cover lifecycle, worktree containment, symlink/Git boundaries,
  selection provenance, receipts, verification, packaging, updater, doctor, and
  compatibility capture.
- Root and portable runtime copies are byte-checked.
- `ground-truth.sh`, `update.sh check`, and the canonical offline gate keep live
  observation, drift, and acceptance distinct.

### Honest model routing

- Caller-selected model/effort is explicit and exact.
- Unsupported pairs fail closed; recommendations remain advisory.
- No general model ranking or "agy saves Codex quota" claim is inferred from a small
  dogfood campaign.

## Overall assessment

The product is unusually strong on trust boundaries, evidence provenance, and honest
failure handling. Its main cost is operational: the safety surface creates a large,
slow regression suite and compatibility activation depends on narrow account-backed
evidence. The right next step is not to weaken gates. It is to improve diagnostic
classification, remove manually duplicated reporting data, reduce flaky timing
assumptions, and parallelize exact-head verification safely.

The maintainer explicitly accepted the reduced non-activating scope, and v0.11.0 was
published from the green exact merged `main` commit described above. agy 1.1.22 remains
non-activating: another account/provider call, inventory acceptance, matrix change, or
activation still requires separate authority and evidence. This report records the
completed campaign; it grants no further publication or provider authority.

## Addendum — 2026-08-28: Sidecar failure classification of retained 1.1.22 evidence

On 2026-08-28, the separate sidecar maintenance tool (`scripts/models_capture_1_1_22_classifier.py`, Issue #72) was applied to the retained owner-private 1.1.22 models observation failure root (`agy-1.1.22-models-final-2026-08-27`).

The classified evidence outcome is:
- **Sanitized failure record SHA-256:** `cab32a092e67b5199c1777e45f65623f703a94812b75a0732e7b3156302e9f77` (unchanged).
- **Private stderr SHA-256:** `00663fead5ee96eea3894a5397cc2f33ddad4c350322cd74ad91ddfa4dd64a9a` (unchanged).
- **Private stdout SHA-256:** `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (unchanged).
- **Classified category:** `local_environment`.
- **Issue-contract refinement:** Issue #72's initial `permission` bucket was split into
  `provider_permission` and `local_environment` so a local pre-provider denial cannot be
  misreported as provider authorization evidence. The sidecar also preserves the
  original `authentication`, `quota`, `service`, `timeout`, and `unknown` outcomes.
- **Classification detail:** The single matching versioned ruleset (`agy-1.1.22-failure-rules-v1`) recognized local CLI log/crash output permission denial and loopback bind permission denial in the local sandbox/environment. Under that bounded ruleset the retained evidence is categorized as `local_environment`; this diagnostic label does not claim the provider was contacted or prove an underlying provider/account state.
- **Authority boundary:** This classification is diagnostic maintenance evidence only; it does not retry the child, alter the non-activating status of agy 1.1.22, advance the model matrix or inventory binding, or grant activation/routing authority.
