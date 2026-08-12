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
/usr/bin/python3 -I -S -B scripts/adoption_measurement.py append-watcher \
  --ledger /private/tmp/codex-agy-worker-measurement.jsonl \
  --result unchanged \
  --repo-sha 0123456789abcdef0123456789abcdef01234567 \
  --evidence-url https://github.com/cagdasyurekli/codex-agy-worker/actions/runs/123456789
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
/usr/bin/python3 -I -S -B scripts/adoption_measurement.py append \
  --ledger /private/tmp/codex-agy-worker-measurement.jsonl \
  --window 30 --metric doctor_pre_dispatch_blocker_ratio \
  --value 2 --denominator 10 --sample-size 8 \
  --repo-sha 0123456789abcdef0123456789abcdef01234567 \
  --evidence-url https://github.com/cagdasyurekli/codex-agy-worker/issues/42
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
