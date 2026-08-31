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

if [[ -n "$shard_nonce" ]]; then
    exec /usr/bin/python3 -I -S -B "$root/scripts/ci_stages.py" --shard-child "$shard_nonce" "$target_shard"
fi

if [[ -n "$timing_nonce" ]]; then
    exec /usr/bin/python3 -I -S -B "$root/scripts/ci_stages.py" --timing-child "$timing_nonce"
fi

if [[ -n "$target_shard" ]]; then
    exec /usr/bin/python3 -I -S -B "$root/scripts/ci_stages.py" --shard "$target_shard"
fi

exec /usr/bin/python3 -I -S -B "$root/scripts/ci_stages.py"
