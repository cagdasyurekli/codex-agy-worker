#!/usr/bin/env bash
# Canonical offline CI body.  The workflow supplies committed-range hygiene separately.
set -eu

script_dir="${0%/*}"
[[ "$script_dir" != "$0" ]] || script_dir="."
root="$(cd "$script_dir/.." && pwd -P)"
cd "$root"

timing_report=""
timing_nonce=""
shard_nonce=""
target_shard=""
shard_receipt=""

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --timing-report)
            [[ "$#" -ge 2 && -n "$2" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            timing_report="$2"
            shift 2
            ;;
        --timing-report=*)
            timing_report="${1#--timing-report=}"
            [[ -n "$timing_report" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            shift 1
            ;;
        --timing-child)
            [[ "$#" -ge 2 && "${#2}" -eq 64 && "$2" != *[!0-9a-f]* ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            timing_nonce="$2"
            shift 2
            ;;
        --shard)
            [[ "$#" -ge 2 && -n "$2" && -z "$target_shard" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            target_shard="$2"
            shift 2
            ;;
        --shard=*)
            [[ -z "$target_shard" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            target_shard="${1#--shard=}"
            [[ -n "$target_shard" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            shift 1
            ;;
        --receipt|--receipt-file)
            [[ "$#" -ge 2 && -n "$2" && -z "$shard_receipt" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            shard_receipt="$2"
            shift 2
            ;;
        --receipt=*|--receipt-file=*)
            [[ -z "$shard_receipt" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            shard_receipt="${1#*=}"
            [[ -n "$shard_receipt" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            shift 1
            ;;
        --shard-child)
            [[ "$#" -ge 3 && "${#2}" -eq 64 && "$2" != *[!0-9a-f]* && -n "$3" \
                && -z "$shard_nonce" && -z "$target_shard" ]] || {
                printf '%s\n' 'ci offline: rejected arguments' >&2
                exit 2
            }
            shard_nonce="$2"
            target_shard="$3"
            shift 3
            ;;
        *)
            printf '%s\n' 'ci offline: rejected arguments' >&2
            exit 2
            ;;
    esac
done

if [[ -n "$target_shard" ]]; then
    case "$target_shard" in
        dispatcher|dispatcher-remediation|other-a|other-b) ;;
        *)
            printf '%s\n' 'ci offline: rejected arguments' >&2
            exit 2
            ;;
    esac
fi

if [[ -n "$timing_report" ]]; then
    [[ -z "$target_shard" && -z "$shard_receipt" && -z "$timing_nonce" && -z "$shard_nonce" ]] || {
        printf '%s\n' 'ci offline: rejected arguments' >&2
        exit 2
    }
    exec /usr/bin/python3 -I -S -B "$root/scripts/ci_timing.py" run --timing-report "$timing_report"
fi

if [[ -n "$shard_receipt" ]]; then
    [[ -n "$target_shard" && -z "$timing_report" && -z "$timing_nonce" && -z "$shard_nonce" ]] || {
        printf '%s\n' 'ci offline: rejected arguments' >&2
        exit 2
    }
    exec /usr/bin/python3 -I -S -B "$root/scripts/ci_sharding.py" run-shard --shard "$target_shard" --receipt "$shard_receipt"
fi

pycache="$(mktemp -d -t agyworker-ci-pycache.XXXXXX)" || exit 1
cleanup() {
    rm -rf -- "$pycache"
}
trap cleanup EXIT HUP INT TERM

stage_in_shard() {
    local shard="$1"
    local stage="$2"
    case "$shard" in
        dispatcher)
            case "$stage" in
                'dispatcher suite') return 0 ;;
                *) return 1 ;;
            esac
            ;;
        dispatcher-remediation)
            case "$stage" in
                'dispatcher remediation suite') return 0 ;;
                *) return 1 ;;
            esac
            ;;
        other-a)
            case "$stage" in
                'working-tree diff hygiene'|'shell syntax'|'Python syntax'|'local job lifecycle suite'|'updater suite'|'repository-only version bootstrap runner'|'repository-only version initial bootstrap runner'|'fixed 1.1.12 version recovery runner'|'canonical models inventory attestation runner'|'explicit-account models capture profile builder'|'fixed 1.1.12 models capture profile builder'|'fixed 1.1.16 models capture profile builder'|'fixed 1.1.22 models capture version evidence'|'fixed 1.1.22 models capture profile builder'|'fixed 1.1.22 models capture runner'|'1.1.16 activation binding'|'1.1.22 activation binding'|'reporting suite'|'feedback triage suite'|'Codex distribution suite'|'read-only doctor suite'|'starter proof suite') return 0 ;;
                *) return 1 ;;
            esac
            ;;
        other-b)
            case "$stage" in
                'qa-gate suite'|'Evidence Receipt v1 suite'|'Evidence Report suite'|'offline benchmark suite'|'persona evidence registry suite'|'data-only workload profiles suite'|'adoption measurement suite'|'local update notifier suite'|'canonical version attestation runner'|'repository-only version bootstrap runtime preflight'|'version attestation mutation harness'|'explicit-account models capture runner'|'fixed 1.1.12 models capture runner'|'fixed 1.1.16 models capture version evidence'|'fixed 1.1.16 models capture runner'|'fixed 1.1.22 models capture reprofile adapter'|'fixed 1.1.22 models capture failure classifier'|'Codex usage observation suite'|'public gate conformance suite'|'repository bytecode hygiene') return 0 ;;
                *) return 1 ;;
            esac
            ;;
        *)
            return 1
            ;;
    esac
}

announce() {
    local stage="$1"
    if [[ -n "$target_shard" ]]; then
        if ! stage_in_shard "$target_shard" "$stage"; then
            return 1
        fi
    fi
    printf '==> %s\n' "$stage"
    if [[ -n "$timing_nonce" ]]; then
        printf '@@agy-worker-ci-timing:%s:%s\n' "$timing_nonce" "$stage"
    fi
    if [[ -n "$shard_nonce" ]]; then
        printf '@@agy-worker-ci-shard:%s:%s\n' "$shard_nonce" "$stage"
    fi
    return 0
}

if announce 'working-tree diff hygiene'; then
git diff --check
fi

if announce 'shell syntax'; then
for file in ./*.sh conformance/*.sh scripts/*.sh tests/*.sh \
    skills/*/scripts/*.sh skills/*/runtime/*.sh; do
    bash -n "$file"
done
fi

if announce 'Python syntax'; then
PYTHONPYCACHEPREFIX="$pycache" \
    python3 -m py_compile conformance/v1/*.py scripts/*.py \
        skills/*/runtime/scripts/*.py
fi

if announce 'qa-gate suite'; then
./tests/test-qa-gate.sh
fi

if announce 'Evidence Receipt v1 suite'; then
./tests/test-evidence-receipt.sh
fi

if announce 'Evidence Report suite'; then
./tests/test-evidence-report.sh
fi

if announce 'offline benchmark suite'; then
/usr/bin/python3 -I -S -B tests/test-benchmark.py
fi

if announce 'persona evidence registry suite'; then
/usr/bin/python3 -I -S -B tests/test-persona-evidence.py
fi

if announce 'local job lifecycle suite'; then
/usr/bin/python3 -I -S -B tests/test-job-lifecycle.py
fi

if announce 'data-only workload profiles suite'; then
/usr/bin/python3 -I -S -B tests/test-workload-profiles.py
fi

if announce 'dispatcher suite'; then
./tests/test-agy-worker.sh
fi

if announce 'dispatcher remediation suite'; then
/usr/bin/python3 -I -S -B tests/test-agy-worker-remediation.py
fi

if announce 'updater suite'; then
./tests/test-update.sh
fi

if announce 'adoption measurement suite'; then
/usr/bin/python3 -I -S -B tests/test-adoption-measurement.py
fi

if announce 'local update notifier suite'; then
/usr/bin/python3 -I -S -B tests/test-update-notifier.py
fi

if announce 'canonical version attestation runner'; then
/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py
fi

if announce 'repository-only version bootstrap runtime preflight'; then
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
fi

if announce 'repository-only version bootstrap runner'; then
/usr/bin/python3 -I -S -B tests/test-version-bootstrap-runner.py
fi

if announce 'repository-only version initial bootstrap runner'; then
/usr/bin/python3 -I -S -B tests/test-version-initial-bootstrap-runner.py
fi

if announce 'fixed 1.1.12 version recovery runner'; then
/usr/bin/python3 -I -S -B tests/test-version-recovery-1-1-12-runner.py
fi

if announce 'version attestation mutation harness'; then
/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py
fi

if announce 'canonical models inventory attestation runner'; then
/usr/bin/python3 -I -S -B tests/test-models-attestation-runner.py
fi

if announce 'explicit-account models capture runner'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-runner.py
fi

if announce 'explicit-account models capture profile builder'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-profile.py
fi

if announce 'fixed 1.1.12 models capture profile builder'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-profile.py
fi

if announce 'fixed 1.1.12 models capture runner'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-runner.py
fi

if announce 'fixed 1.1.16 models capture version evidence'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-version-evidence.py
fi

if announce 'fixed 1.1.16 models capture profile builder'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-profile.py
fi

if announce 'fixed 1.1.16 models capture runner'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-runner.py
fi

if announce 'fixed 1.1.22 models capture version evidence'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-version-evidence.py
fi

if announce 'fixed 1.1.22 models capture profile builder'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-profile.py
fi

if announce 'fixed 1.1.22 models capture runner'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-runner.py
fi

if announce 'fixed 1.1.22 models capture reprofile adapter'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-reprofile.py
fi

if announce 'fixed 1.1.22 models capture failure classifier'; then
/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-classifier.py
fi

if announce '1.1.16 activation binding'; then
/usr/bin/python3 -I -S -B tests/test-agy-1-1-16-activation.py
fi

if announce '1.1.22 activation binding'; then
/usr/bin/python3 -I -S -B tests/test-agy-1-1-22-activation.py
fi

if announce 'reporting suite'; then
./tests/test-reporting.sh
fi

if announce 'feedback triage suite'; then
/usr/bin/python3 -I -S -B tests/test-feedback-triage.py
fi

if announce 'Codex usage observation suite'; then
/usr/bin/python3 -I -S -B tests/test-codex-usage-report.py
fi

if announce 'Codex distribution suite'; then
./tests/test-packaging.sh
fi

if announce 'read-only doctor suite'; then
./tests/test-doctor.sh
fi

if announce 'public gate conformance suite'; then
/usr/bin/python3 -I -S -B tests/test-conformance.py
fi

if announce 'starter proof suite'; then
./tests/test-proof-demo.sh
fi

if announce 'repository bytecode hygiene'; then
if find . -type d -name __pycache__ -print -quit | grep -q . \
        || find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -quit | grep -q .; then
    printf '%s\n' 'ci offline: repository bytecode detected' >&2
    exit 1
fi
fi
