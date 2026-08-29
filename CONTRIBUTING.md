# Contributing

Thank you for helping improve `codex-agy-worker`. Human contributors should open
only the section relevant to the change: [README.md](README.md) for user-facing
behavior, the matching [repository-map](docs/REPO_MAP.md) row for ownership and tests,
or the matching [architectural lesson](docs/lessons_learned.md) when prior rationale
is needed. README and public-documentation changes must also follow the
[documentation policy](docs/DOCUMENTATION_POLICY.md).

## Before opening a change

- Keep the worker outside the trust boundary. Do not weaken `qa-gate.sh` to make a
  candidate pass.
- Keep the runtime Bash + Python 3 + git. Do not add a package manager or daemon.
- Preserve explicit permission, privacy, and external-action boundaries.
- Open security reports through the private route in [SECURITY.md](SECURITY.md), not
  a public issue.

For a code or documentation change, describe the bounded problem, the paths in scope,
and the evidence that will show the change works. Add both an accept and a reject case
when introducing a new gate check.

## Verify locally

During implementation, run the owning focused suite from the relevant
[`REPO_MAP`](docs/REPO_MAP.md) row. Do not repeatedly run the full suite while the
same candidate bytes are unchanged.

Once the candidate is stable, run the canonical offline CI body once before
requesting review:

```bash
./scripts/ci-offline.sh
```

It is fail-fast, requires no network or provider call, does not intentionally inspect
account-HOME contents, externalizes temporary bytecode, and runs the static checks plus
all thirty-nine offline suites. Ambient local tools may still consult ordinary user
configuration. In GitHub Actions, the suite is partitioned across four fail-closed shards
(`dispatcher`, `dispatcher-remediation`, `other-a`, `other-b`) and validated by the required aggregate
`test` check; lower CI wall time from parallelization does not mean lower total compute, provider usage,
token usage, cost, or weaker verification. On a clean tracked/untracked worktree, an optional
`./scripts/ci-offline.sh --timing-report <PATH>` mode captures ordered per-stage
observational monotonic wall time in an owner-private mode-0600 JSON report without
recording commands, logs, environment variables, or timestamps. For a quota-unavailable
private fork this is evidence to attach to review, not a replacement for the protected
GitHub `test` check; manually dispatch the exact committed range after Actions becomes
available and before publication. The expanded command inventory is retained below
for targeted diagnosis. A focused pass accelerates iteration but does not replace the
stable-candidate full gate:

```bash
./tests/test-qa-gate.sh
./tests/test-evidence-receipt.sh
./tests/test-evidence-report.sh
/usr/bin/python3 -I -S -B tests/test-benchmark.py
/usr/bin/python3 -I -S -B tests/test-persona-evidence.py
/usr/bin/python3 -I -S -B tests/test-job-lifecycle.py
/usr/bin/python3 -I -S -B tests/test-workload-profiles.py
./tests/test-agy-worker.sh
/usr/bin/python3 -I -S -B tests/test-agy-worker-remediation.py
./tests/test-update.sh
/usr/bin/python3 -I -S -B tests/test-adoption-measurement.py
/usr/bin/python3 -I -S -B tests/test-update-notifier.py
/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py
/usr/bin/python3 -I -S -B tests/test-version-bootstrap-runner.py
/usr/bin/python3 -I -S -B tests/test-version-initial-bootstrap-runner.py
/usr/bin/python3 -I -S -B tests/test-version-recovery-1-1-12-runner.py
/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py
/usr/bin/python3 -I -S -B tests/test-models-attestation-runner.py
/usr/bin/python3 -I -S -B tests/test-models-capture-runner.py
/usr/bin/python3 -I -S -B tests/test-models-capture-profile.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-profile.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-runner.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-version-evidence.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-profile.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-runner.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-version-evidence.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-profile.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-runner.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-reprofile.py
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-classifier.py
/usr/bin/python3 -I -S -B tests/test-agy-1-1-16-activation.py
/usr/bin/python3 -I -S -B tests/test-agy-1-1-22-activation.py
./tests/test-reporting.sh
/usr/bin/python3 -I -S -B tests/test-feedback-triage.py
/usr/bin/python3 -I -S -B tests/test-codex-usage-report.py
./tests/test-packaging.sh
./tests/test-doctor.sh
/usr/bin/python3 -I -S -B tests/test-conformance.py
./tests/test-proof-demo.sh
bash -n ./*.sh conformance/*.sh scripts/*.sh tests/*.sh skills/*/scripts/*.sh skills/*/runtime/*.sh
(
  AGY_WORKER_PYCACHE="$(mktemp -d -t agyworker-pycache.XXXXXX)" || exit 1
  trap 'rm -rf -- "$AGY_WORKER_PYCACHE"' EXIT
  PYTHONPYCACHEPREFIX="$AGY_WORKER_PYCACHE" \
    python3 -m py_compile conformance/v1/*.py scripts/*.py skills/*/runtime/scripts/*.py
)
git diff --check
```

Report exact summaries and any checks you could not run. Passing tests do not replace
human diff review or justify unrelated cleanup.

When a release is also claimed to be installed or collecting local measurements,
verify those machine states separately after publication. A clean tagged checkout is
not proof that the global Codex skill was recopied, that the LaunchAgent snapshot was
rebound, or that an explicit measurement ledger and its daily append path exist. Read
back installed-bundle parity, notifier status, the ledger header/report, and at least
one real observation before making those claims.

## Pull requests

Keep each pull request focused. Explain the trust boundary affected, list changed
paths, identify user-visible claims that changed, and include the exact verification
results. Maintainers may ask for a smaller slice when unrelated work obscures review.

By participating, you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
