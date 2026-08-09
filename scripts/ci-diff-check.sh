#!/usr/bin/env bash
# Verify both Git's committed-range policy and raw blobs independent of attributes.
set -u

[[ "$#" == 0 ]] || {
    printf '%s\n' 'ci diff check: rejected' >&2
    exit 2
}

script_dir="${0%/*}"
[[ "$script_dir" != "$0" ]] || script_dir="."
exec /usr/bin/python3 -I -S -B "$script_dir/ci_diff_check.py"
