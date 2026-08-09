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
- [ ] `./tests/test-agy-worker.sh`
- [ ] `./tests/test-update.sh`
- [ ] `/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py`
- [ ] `/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py`
- [ ] `./tests/test-reporting.sh`
- [ ] `./tests/test-packaging.sh`
- [ ] `./tests/test-doctor.sh`
- [ ] `./tests/test-proof-demo.sh`
- [ ] Bash and Python syntax checks
- [ ] `git diff --check`
- [ ] Human diff review completed

## Safety and release

- [ ] No worker-reported command or test was treated as evidence.
- [ ] No permission, authentication, privacy, or path-policy boundary was weakened.
- [ ] No commit, push, merge, release, issue submission, or external setting change is
      implied by this pull request without separate authorization.
