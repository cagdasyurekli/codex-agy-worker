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

Run the offline suites and static checks before requesting review:

```bash
./tests/test-qa-gate.sh
./tests/test-agy-worker.sh
./tests/test-update.sh
./tests/test-reporting.sh
./tests/test-packaging.sh
bash -n ./*.sh tests/*.sh skills/*/scripts/*.sh skills/*/runtime/*.sh
python3 -m py_compile scripts/*.py skills/*/runtime/scripts/*.py
git diff --check
```

Report exact summaries and any checks you could not run. Passing tests do not replace
human diff review or justify unrelated cleanup.

## Pull requests

Keep each pull request focused. Explain the trust boundary affected, list changed
paths, identify user-visible claims that changed, and include the exact verification
results. Maintainers may ask for a smaller slice when unrelated work obscures review.

By participating, you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
