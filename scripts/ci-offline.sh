#!/usr/bin/env bash
# Canonical offline CI body.  The workflow supplies committed-range hygiene separately.
set -eu

script_dir="${0%/*}"
[[ "$script_dir" != "$0" ]] || script_dir="."
root="$(cd "$script_dir/.." && pwd -P)"
cd "$root"

timing_report=""
timing_nonce=""
if [[ "$#" -eq 0 ]]; then
    :
elif [[ "$#" -eq 2 && "$1" == "--timing-report" ]]; then
    timing_report="$2"
    [[ -n "$timing_report" ]] || {
        printf '%s\n' 'ci offline: rejected arguments' >&2
        exit 2
    }
elif [[ "$#" -eq 1 && "$1" == --timing-report=* ]]; then
    timing_report="${1#--timing-report=}"
    [[ -n "$timing_report" ]] || {
        printf '%s\n' 'ci offline: rejected arguments' >&2
        exit 2
    }
elif [[ "$#" -eq 2 && "$1" == "--timing-child" \
        && "${#2}" -eq 64 && "$2" != *[!0-9a-f]* ]]; then
    timing_nonce="$2"
else
    printf '%s\n' 'ci offline: rejected arguments' >&2
    exit 2
fi

if [[ -n "$timing_report" ]]; then
    exec /usr/bin/python3 -I -S -B "$root/scripts/ci_timing.py" run --timing-report "$timing_report"
fi

pycache="$(mktemp -d -t agyworker-ci-pycache.XXXXXX)" || exit 1
cleanup() {
    rm -rf -- "$pycache"
}
trap cleanup EXIT HUP INT TERM

announce() {
    printf '==> %s\n' "$1"
    if [[ -n "$timing_nonce" ]]; then
        printf '@@agy-worker-ci-timing:%s:%s\n' "$timing_nonce" "$1"
    fi
}

announce 'working-tree diff hygiene'
git diff --check

announce 'shell syntax'
for file in ./*.sh conformance/*.sh scripts/*.sh tests/*.sh \
    skills/*/scripts/*.sh skills/*/runtime/*.sh; do
    bash -n "$file"
done

announce 'Python syntax'
PYTHONPYCACHEPREFIX="$pycache" \
    python3 -m py_compile conformance/v1/*.py scripts/*.py \
        skills/*/runtime/scripts/*.py

announce 'qa-gate suite'
./tests/test-qa-gate.sh
announce 'Evidence Receipt v1 suite'
./tests/test-evidence-receipt.sh
announce 'Evidence Report suite'
./tests/test-evidence-report.sh
announce 'offline benchmark suite'
/usr/bin/python3 -I -S -B tests/test-benchmark.py
announce 'persona evidence registry suite'
/usr/bin/python3 -I -S -B tests/test-persona-evidence.py
announce 'local job lifecycle suite'
/usr/bin/python3 -I -S -B tests/test-job-lifecycle.py
announce 'data-only workload profiles suite'
/usr/bin/python3 -I -S -B tests/test-workload-profiles.py
announce 'dispatcher suite'
./tests/test-agy-worker.sh
announce 'dispatcher remediation suite'
/usr/bin/python3 -I -S -B tests/test-agy-worker-remediation.py
announce 'updater suite'
./tests/test-update.sh
announce 'adoption measurement suite'
/usr/bin/python3 -I -S -B tests/test-adoption-measurement.py
announce 'local update notifier suite'
/usr/bin/python3 -I -S -B tests/test-update-notifier.py
announce 'canonical version attestation runner'
/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py

announce 'repository-only version bootstrap runtime preflight'
/usr/bin/python3 -I -S -B - <<'PY'
import sys

if not (
    sys.implementation.name == "cpython"
    and sys.version_info[:2] == (3, 9)
    and sys.flags.isolated == 1
    and sys.flags.no_site == 1
    and sys.flags.dont_write_bytecode == 1
    and sys.flags.ignore_environment == 1
):
    raise SystemExit("repository-only version bootstrap requires CPython 3.9 with -I -S -B")
PY

announce 'repository-only version bootstrap runner'
/usr/bin/python3 -I -S -B tests/test-version-bootstrap-runner.py
announce 'repository-only version initial bootstrap runner'
/usr/bin/python3 -I -S -B tests/test-version-initial-bootstrap-runner.py
announce 'fixed 1.1.12 version recovery runner'
/usr/bin/python3 -I -S -B tests/test-version-recovery-1-1-12-runner.py
announce 'version attestation mutation harness'
/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py
announce 'canonical models inventory attestation runner'
/usr/bin/python3 -I -S -B tests/test-models-attestation-runner.py
announce 'explicit-account models capture runner'
/usr/bin/python3 -I -S -B tests/test-models-capture-runner.py
announce 'explicit-account models capture profile builder'
/usr/bin/python3 -I -S -B tests/test-models-capture-profile.py
announce 'fixed 1.1.12 models capture profile builder'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-profile.py
announce 'fixed 1.1.12 models capture runner'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-runner.py
announce 'fixed 1.1.16 models capture version evidence'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-version-evidence.py
announce 'fixed 1.1.16 models capture profile builder'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-profile.py
announce 'fixed 1.1.16 models capture runner'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-runner.py
announce 'fixed 1.1.22 models capture version evidence'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-version-evidence.py
announce 'fixed 1.1.22 models capture profile builder'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-profile.py
announce 'fixed 1.1.22 models capture runner'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-runner.py
announce 'fixed 1.1.22 models capture failure classifier'
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-classifier.py
announce '1.1.16 activation binding'
/usr/bin/python3 -I -S -B tests/test-agy-1-1-16-activation.py
announce 'reporting suite'
./tests/test-reporting.sh
announce 'feedback triage suite'
/usr/bin/python3 -I -S -B tests/test-feedback-triage.py
announce 'Codex usage observation suite'
/usr/bin/python3 -I -S -B tests/test-codex-usage-report.py
announce 'Codex distribution suite'
./tests/test-packaging.sh
announce 'read-only doctor suite'
./tests/test-doctor.sh
announce 'public gate conformance suite'
/usr/bin/python3 -I -S -B tests/test-conformance.py
announce 'starter proof suite'
./tests/test-proof-demo.sh

announce 'repository bytecode hygiene'
if find . -type d -name __pycache__ -print -quit | grep -q . \
        || find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -quit | grep -q .; then
    printf '%s\n' 'ci offline: repository bytecode detected' >&2
    exit 1
fi
