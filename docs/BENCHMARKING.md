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
