# Adoption measurement ledger

This optional local ledger provides a deliberately small, privacy-limited way to
measure public project operations. It is not telemetry, billing, provider-usage
evidence, routing input, an acceptance path, or permission for an external action.
It never invokes `agy`, `update.sh`, Git, a subprocess, or a network client; it never
discovers a ledger or reads a home directory. The owner supplies one explicit,
pre-existing external directory and retains the ledger.

```bash
/usr/bin/python3 -I -S -B scripts/adoption_measurement.py init \
  --ledger /private/tmp/codex-agy-worker-measurement.jsonl
```

The ledger is a canonical newline-delimited file. It must remain a regular,
current-owner, `0600`, one-link file. It records only an opaque random observation ID,
UTC date, repository revision SHA, one closed metric/value/denominator/sample-size
tuple, and one allowlisted public GitHub evidence URL. It rejects home-relative or
discovered paths, symlinks, links, future records, duplicate metric/window/date
records, non-canonical bytes, and URLs containing private logs, artifacts, query
strings, fragments, other hosts, or non-evidence paths. Generic records may bind a
public GitHub repository, issue, pull request, commit, or Actions run; watcher records
are further limited to this repository’s public Actions runs.
An advisory file lock spans each read/check/append/fsync transaction so concurrent
writers cannot both create the same metric/window/UTC-day observation.

## Explicit collection

After a compatibility-watch run, manually copy only its sanitized outcome and public
Actions run URL. This convenience command stores one 30-day watcher outcome count;
it does not read the workflow, action output, or any GitHub service.

```bash
: "${REPO_SHA:?set REPO_SHA to the measured 40-character repository commit}"
: "${WATCHER_EVIDENCE_URL:?set WATCHER_EVIDENCE_URL to the public Actions run URL}"
/usr/bin/python3 -I -S -B scripts/adoption_measurement.py append-watcher \
  --ledger /private/tmp/codex-agy-worker-measurement.jsonl \
  --result unchanged \
  --repo-sha "$REPO_SHA" \
  --evidence-url "$WATCHER_EVIDENCE_URL"
```

`unchanged`, `drift-review`, and `evidence-unavailable` map to the watcher’s `0`, `3`,
and `2` result classes. No raw watcher output, duration stream, user identity, host,
path, prompt, token, provider, account, or log data is retained.

For every other closed metric, append the aggregate explicitly. `value` is the
measured numerator/count/duration/snapshot. Ratios use `denominator` as the eligible
population and `sample-size` as the reviewed portion; a report labels an aggregate
partial when its sample size is smaller than its denominator. Sum, median, and latest
metrics require the denominator and sample size to be exactly `1`.

```bash
: "${REPO_SHA:?set REPO_SHA to the measured 40-character repository commit}"
: "${METRIC_EVIDENCE_URL:?set METRIC_EVIDENCE_URL to its public GitHub evidence URL}"
/usr/bin/python3 -I -S -B scripts/adoption_measurement.py append \
  --ledger /private/tmp/codex-agy-worker-measurement.jsonl \
  --window 30 --metric doctor_pre_dispatch_blocker_ratio \
  --value 2 --denominator 10 --sample-size 8 \
  --repo-sha "$REPO_SHA" \
  --evidence-url "$METRIC_EVIDENCE_URL"
```

## Closed report families

`report` emits fixed 30-, 60-, and 90-day sections. Each window includes today plus
the preceding 29, 59, or 89 UTC dates respectively. A metric with no fresh record is
shown as `missing`; stale valid records are ignored for that section and counted as
expired instead of invalidating the accumulating ledger. Future records always reject
the ledger. Each metric has exactly one aggregation method: sum, median, ratio with
denominator and sample size, or latest snapshot.

| Window | Closed metrics |
|---:|---|
| 30 days | Watcher outcome counts; fresh-clone-to-proof duration median; doctor pre-dispatch blocker ratio; published-example completeness ratio; GitHub interest snapshot. |
| 60 days | Compatibility lead-time median; verified external receipt/proof count; bug-report completeness ratio; referral/search trend snapshot; gate/external-action regression count. |
| 90 days | Baseline audit count and ratio; external conformance workflow count; conformance-result ratio; bound benchmark-claim count; accepted-real-persona count. |

```bash
/usr/bin/python3 -I -S -B scripts/adoption_measurement.py report \
  --ledger /private/tmp/codex-agy-worker-measurement.jsonl
```

To rotate, stop appending to the old ledger and explicitly initialize a new ledger at
a new owner-chosen path. The tool never overwrites, truncates, auto-prunes, or deletes
a ledger. Retention of old ledgers is the owner’s separate responsibility.

## Operational setup is a separate state transition

Publishing or checking out a release does not install the Codex skill, initialize a
ledger, or connect a daily observation to that ledger. Treat these as separate
post-release states and read each one back independently:

1. run `./install.sh`, then compare the installed bundle with
   `skills/agy-worker/` while excluding only `.pipeline-root`;
2. initialize one explicit persistent owner-private ledger and render an empty report;
3. install and read back the optional notifier separately;
4. configure a daily collector or reminder that invokes `append-watcher` with the
   exact sanitized result, repository revision, and public Actions run URL; and
5. confirm that the first real observation exists in the ledger.

The hosted watcher and local notifier intentionally do not discover or write a
measurement ledger. Seeing either one run successfully is therefore not evidence that
30/60/90 data is accumulating. A collector must fail closed rather than fabricate an
observation when the local result, exact repository revision, or public run URL cannot
be reconciled.

## Manual three-run A/B measurement protocol (Codex orchestration usage)

To measure the orchestration overhead and token consumption of direct Codex task execution
versus delegated `agy-worker` execution, use the privacy-safe `codex-usage-report.sh` tool
with this manual three-run protocol. This protocol is strictly manual; do not build an
automatic task executor.

### 1. Preparation and controlled conditions

Freeze all independent variables across both conditions:
- **Baseline repository commit:** identical base SHA in disposable worktrees.
- **Task prompt and scope:** identical user prompt and declared file scope.
- **Codex model & effort:** identical driver model and reasoning effort.
- **Verification commands & budget:** identical driver test suite and time budget.
- **No subagents or concurrent background tasks:** ensure single-agent execution.

### 2. Execution of three matched repetitions

Run three fresh, independent trials for each condition:
- **Condition A (Direct Codex):** Execute the task directly in Codex across 3 fresh runs (`A1`, `A2`, `A3`).
- **Condition B (Delegated agy-worker):** Dispatch the task via `agy-worker.sh` across 3 fresh runs (`B1`, `B2`, `B3`).

For each run, note its thread ID. Codex Desktop session files may be mode `0644`; never
change or pass that live file directly. If session counters are needed, copy the one
explicit file into a temporary owner-private (`0700`) directory, set the copy to mode
`0600`, pass only that absolute copied path, and delete the copy after observation.

### 3. Usage observation extraction

Run `codex-usage-report.sh` for each condition and run:

```bash
# Example for Condition A, Run 1
./codex-usage-report.sh \
  --task main=THREAD_A1 \
  --session main=/path/to/private/session_a1.jsonl \
  --account-usage \
  --format text

# Example for Condition B, Run 1
./codex-usage-report.sh \
  --task main=THREAD_B1 \
  --session main=/path/to/private/session_b1.jsonl \
  --account-usage \
  --format text
```

### 4. Analysis and limitations

Compare the resulting token breakdowns:
- **Input tokens:** total input, cached input, net-new input, and cache-write input.
- **Output tokens:** total output tokens and reasoning output tokens (reasoning is a subset of output and must not be double-counted).
- **Latest phase window:** the most recent validated `last_token_usage` counters, kept
  separate from cumulative session totals.
- **Structural tool activity:** allowlisted tool call counts and wait counts.
- **Per-thread usage estimate:** estimated credits in integer micros from exact
  `threadUsage` responses (explicitly labeled `provider_estimate`); account-level
  observation remains rate limits only.

**Strict boundaries:**
- Token-only comparisons are **directional only**.
- Never infer dollar/USD costs, pricing, or remaining billing quota from token numbers or estimates without official billing receipts.
