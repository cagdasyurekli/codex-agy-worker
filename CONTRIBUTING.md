# Contributing

Thank you for helping improve `codex-agy-worker`. Start with [README.md](README.md),
[the repository map](docs/REPO_MAP.md), and
[the architectural lessons](docs/lessons_learned.md).

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

Run the canonical offline CI body before requesting review:

```bash
./scripts/ci-offline.sh
```

It is fail-fast, requires no network or provider call, does not intentionally inspect
account-HOME contents, externalizes temporary bytecode, and runs the static checks plus
all twenty-seven offline suites. Ambient local tools may still consult ordinary user
configuration. For a quota-unavailable
private fork this is evidence to attach to review, not a replacement for the protected
GitHub `test` check; manually dispatch the exact committed range after Actions becomes
available and before publication. The expanded command inventory is retained below for
targeted diagnosis:

```bash
./tests/test-qa-gate.sh
./tests/test-evidence-receipt.sh
./tests/test-evidence-report.sh
/usr/bin/python3 -I -S -B tests/test-benchmark.py
/usr/bin/python3 -I -S -B tests/test-persona-evidence.py
/usr/bin/python3 -I -S -B tests/test-job-lifecycle.py
/usr/bin/python3 -I -S -B tests/test-workload-profiles.py
./tests/test-agy-worker.sh
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
./tests/test-reporting.sh
/usr/bin/python3 -I -S -B tests/test-feedback-triage.py
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
