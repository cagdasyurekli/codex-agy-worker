# Offline Benchmark v1

`benchmark.sh` is a provider-independent proof harness for comparing the same
checked-in synthetic task across an ordered, caller-preregistered set of selection
records. It does not call agy, a provider, or the network. It does not rank, score,
route, recommend, retry, fall back, or change a selector.

## What is authoritative

In a complete checkout the runner accepts only a clean commit and binds its exact
commit. In a folder-only skill bundle it uses no Git authority: it binds the fixed
`offline-benchmark-v1` portable revision and a canonical source manifest that
rejects missing, extra, writable, wrong-mode, symlinked, or hash-drifted benchmark
authority files. It never fabricates a commit. Both layouts bind the canonical
benchmark runner, schemas, manifest, fixture set, `qa-gate.sh`, and
`verify-job.sh` into `plan.v1.json`. The public manifest fixes the task order,
scope, exact fixture hashes, and verifier policy. Every variant-task pair has
exactly one attempt. A driver wall duration is operational diagnostics only and is
not a result field.

The versioned JSON schemas constrain every nested v1 structure, field, enum,
integer bound, and digest/name grammar. The runtime validator additionally owns
cross-field equality, canonical bytes, current source identity, and Receipt facts.

`qa-gate.sh` is the sole verdict authority. Each run produces an unchanged Evidence
Receipt v1 through `verify-job.sh`; the separate unsigned benchmark result only
binds the raw receipt hash and schema-validated gate facts. A benchmark report is a
pure manifest-order completeness view. `gate-passed` is not human acceptance, and
the report never chooses a winner.

## Offline workflow

Create an external owner-only directory. It must be canonical, mode `0700`, and
outside the repository. The example variant is public synthetic input; callers may
provide multiple `--variant` flags in the exact order they want preregistered.

```bash
RESULT_ROOT="$(mktemp -d -t agy-benchmark-results.XXXXXX)"
RESULT_ROOT="$(cd "$RESULT_ROOT" && pwd -P)"
chmod 700 "$RESULT_ROOT"

./benchmark.sh prepare \
  --result-root "$RESULT_ROOT" \
  --variant "$PWD/benchmarks/v1/variants/bulk.json"

./benchmark.sh run --plan "$RESULT_ROOT/plan.v1.json"

./benchmark.sh report \
  --plan "$RESULT_ROOT/plan.v1.json" \
  --result "$RESULT_ROOT/result.v1.json"
```

Plan, receipt, and result files are private mode `0600`, canonical JSON, and never
overwritten. The plan and result are unsigned and not self-authenticating. Raw
worker/provider content is not part of this offline contract; the synthetic worker
envelope is checked in and hash-bound.

## Deliberate limits

Live benchmarking is not implemented.

- There is no `--live` mode and no provider/import/competitor adapter.
- Future live execution requires a separately reviewed and accepted agy executable
  and version attestation, a new implementation slice, and explicit authorization.
- Persona files may be hash-bound as caller input, but v1 does not define a persona
  registry, claim persona quality, or infer a persona. P1-D owns registry work.
- Evidence Receipt v1 is unchanged. Reports and results cannot upgrade or synthesize
  a verdict, acceptance decision, ranking, recommendation, or route.
- Interruptions and partial runs publish no benchmark result. Rerun in a fresh
  owner-only result root; there is no resume, retry, or overwrite path.

Run the dedicated offline suite with:

```bash
/usr/bin/python3 -I -S -B tests/test-benchmark.py
```

## SWE-bench Workflow Study v1

`swebench-workflow-study.sh` imports explicit, sanitized, matched experiment
results and derives token/cost efficiency per accepted solution without calling a
provider. Run its four stages against one external owner-only result root:

```bash
./swebench-workflow-study.sh prepare --root /path/to/results --plan /path/to/plan.json
./swebench-workflow-study.sh import --root /path/to/results --records /path/to/results.jsonl
./swebench-workflow-study.sh report --root /path/to/results
./swebench-workflow-study.sh advise --root /path/to/results
```

The result root must be outside the checkout or relocated skill bundle, owned by
the caller, mode `0700`, and empty at prepare time. Plan and record inputs must be
caller-owned mode-`0600` one-link regular files. Every stage consumes the exact
prior hash-linked artifact and publishes one flat canonical mode-`0600`
no-overwrite artifact.

Plans freeze sorted opaque task commitments, budgets, and separate Codex/agy
telemetry bindings. Imports require closed failure classifications, complete
driver/reviewer acceptance gates, and explicit unavailable states. The tool derives
`accepted_solution`; it does not trust that field as an independent claim.

Advice is deterministic and recommendation-only. It requires task-paired Pareto
non-regression over every planned cell, comparable accounting/tokenizer bindings
for combined token metrics, and matching currency/cost-basis bindings for combined
cost metrics. Incompatible or incomplete telemetry fails closed. The advisory keeps
`applied`, dispatch, model-change, and effort-change authority false and never
influences `qa-gate` acceptance.

## Model Intelligence v1

`model-intelligence.sh` provides offline evidence validation, Issue #78 SWE-bench study import, benchmark review tracking across supported model inventory changes and dataset expiry, and deterministic Pareto
frontier analysis across model candidates for bounded task taxonomies:

```bash
./model-intelligence.sh validate --dataset path/to/dataset.json
./model-intelligence.sh advise --dataset path/to/dataset.json --taxonomy swe-bench-lite --reference-date 2026-08-25
./model-intelligence.sh import-study --report path/to/report.json --plan path/to/plan.json --results path/to/imported_results.json --out path/to/dataset.json
./model-intelligence.sh benchmark-review --reference-date 2026-08-30
./model-intelligence.sh benchmark-review --reference-date 2026-08-30 \
  --baseline-inventory path/to/reviewed-baseline-inventory.json \
  --candidate-inventory path/to/reviewed-candidate-inventory.json \
  --out /absolute/private/path/benchmark-review.json
```

Evidence records require valid HTTPS or local provenances, distinct provenance types (`vendor`,
`independent`, `local`), non-expired observations relative to caller-provided `--reference-date`, and strict telemetry comparability
(harness, harness version, agy version, task taxonomy, provenance, confidence, accounting, tokenizer, cost basis, currency). Expired, calibration-only, substituted,
incomplete, or incomparable telemetry fails closed to `no_recommendation`. Comparable candidates
yield Pareto trade-off options with zero execution, dispatch, model-change, or git authority.
When supported model inventory bindings change or dataset evidence expires, `benchmark-review` emits bounded
`benchmark-review-due` facts naming affected public model identifiers and their evidence states without execution authority;
maintainer disposition (`collect`, `defer`, `not-applicable`) is recorded only when explicitly provided and is never chosen automatically.
The Issue #78 importer validates the canonical plan/import/report hash chain and emits
only a calibration provenance record. Because that chain does not attest an observed
model, agy version, substitution result, model-level quality, latency percentiles,
token means, cost, or confidence, those fields remain `null` and cannot participate
in ranking.

## Model Evidence Campaign

`model-evidence-campaign.sh` provides an offline incremental new-model evidence campaign workflow triggered after #106 benchmark-review:

```bash
./model-evidence-campaign.sh validate-plan --plan path/to/plan.json --review path/to/review.json --inventory path/to/inventory.json --matrix path/to/matrix.json --dataset path/to/dataset.json
./model-evidence-campaign.sh evaluate --plan path/to/plan.json --record path/to/record.json --review path/to/review.json --inventory path/to/inventory.json --matrix path/to/matrix.json --dataset path/to/dataset.json [--out path/to/eval.json]
./model-evidence-campaign.sh materialize-measured --plan path/to/plan.json --record path/to/record.json --evaluation path/to/eval.json --review path/to/review.json --inventory path/to/inventory.json --matrix path/to/matrix.json --dataset path/to/dataset.json --out /private/owner-state/new_dataset.json
./model-evidence-campaign.sh advisory-preview --local-opt-in --plan path/to/plan.json --records-dir path/to/records/ --evaluation path/to/eval.json --review path/to/review.json --inventory path/to/inventory.json --matrix path/to/matrix.json --dataset path/to/dataset.json
./model-evidence-campaign.sh advisory-export --local-opt-in --plan path/to/plan.json --records-dir path/to/records/ --evaluation path/to/eval.json --review path/to/review.json --inventory path/to/inventory.json --matrix path/to/matrix.json --dataset path/to/dataset.json --approve-preview-sha <SHA256> --out /private/owner-state/advisory.json
./model-evidence-campaign.sh aggregate-status --local-opt-in --records-dir path/to/records/ --evaluations-dir path/to/evaluations/
./model-evidence-campaign.sh aggregate-preview --local-opt-in --records-dir path/to/records/ --evaluations-dir path/to/evaluations/
./model-evidence-campaign.sh aggregate-export --local-opt-in --approve-preview-sha <SHA256> --records-dir path/to/records/ --evaluations-dir path/to/evaluations/ --out /private/owner-state/export.json
```

Campaigns separate four immutable artifact roles (plan, caller-owned record, deterministic evaluation, and optional local aggregate) across three mutually exclusive evidence lanes (`vendor_declared`, `measured`, `observational`). Plan validation and evaluation require the exact current #106 review, inventory binding, model matrix, and dataset artifacts. Records bind the frozen measurement window, expose bounded error and drift fractions, and use closed limitation codes rather than free text. Evaluation is pure and deterministic, failing closed on structural errors and yielding bounded `no_recommendation` reasons on drift, mismatch, incompatibility, insufficiency, verification failure, or uncertainty. Materialization revalidates that full chain, recomputes the evaluation exactly, accepts only a measured plan/record/evaluation, and copies only explicit measured metadata into a new no-overwrite 0600 dataset artifact.

The optional Phase 3 advisory requires the exact plan, complete candidate-plus-anchor cohort, full review/inventory/matrix/dataset chain, one bound evaluation, and explicit local opt-in. It deterministically recomputes that evaluation and rejects any caller-supplied result that differs. Its preview exposes only a coarse workflow category and terminal scenario version, version strings without component identifiers, per-subject coverage/identity/provenance/uncertainty/drift, and closed rationale codes. It never exposes prompts, paths, model or artifact identifiers, provenance URIs, or raw evidence; non-measured or failed evidence remains `no_recommendation`. Export requires the exact preview SHA and uses the same no-overwrite owner-private publication boundary. Aggregate commands also require explicit local opt-in and evaluation artifacts bound to record digests. A missing evaluation or a structurally valid evaluation that does not bind the record leaves that record `unreviewed`; any invalid or duplicate evaluation input makes the aggregate command fail closed. Newly computed aggregates use schema v2 for the coarse workflow-category, requested-versus-observed identity, and candidate/anchor identity-mismatch counts; legacy schema-v1 aggregate artifacts remain valid without those fields. Both versions expose integer counts only and carry zero network, provider, dispatch, or git authority. Output parents must be existing owner-owned mode-0700 directories reached through an entirely real, canonical, no-symlink ancestor chain; publication never creates or overwrites a target.
