## What changed

<!-- Describe the bounded problem and solution. -->

## Trust boundary and scope

- Affected paths:
- Trust boundary affected:
- User-visible claims changed:
- Explicitly out of scope:

## Verification

<!-- Include exact summaries; mark unrun checks and explain why. -->

- [ ] `./tests/test-qa-gate.sh`
- [ ] `./tests/test-evidence-receipt.sh`
- [ ] `./tests/test-evidence-report.sh`
- [ ] `/usr/bin/python3 -I -S -B tests/test-benchmark.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-persona-evidence.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-job-lifecycle.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-workload-profiles.py`
- [ ] `./tests/test-agy-worker.sh`
- [ ] `./tests/test-update.sh`
- [ ] `/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-version-bootstrap-runner.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-version-initial-bootstrap-runner.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-version-recovery-1-1-12-runner.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-models-attestation-runner.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-models-capture-runner.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-models-capture-profile.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-profile.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-runner.py`
- [ ] `./tests/test-reporting.sh`
- [ ] `./tests/test-packaging.sh`
- [ ] `./tests/test-doctor.sh`
- [ ] `/usr/bin/python3 -I -S -B tests/test-conformance.py`
- [ ] `./tests/test-proof-demo.sh`
- [ ] Bash and Python syntax checks
- [ ] `git diff --check`
- [ ] Human diff review completed

## Safety and release

- [ ] No worker-reported command or test was treated as evidence.
- [ ] No permission, authentication, privacy, or path-policy boundary was weakened.
- [ ] No commit, push, merge, release, issue submission, or external setting change is
      implied by this pull request without separate authorization.
