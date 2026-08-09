#!/usr/bin/env bash
# Run the versioned public qa-gate conformance fixtures against one gate entry point.
set -eu

script_dir="$(CDPATH= cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)" || exit 64
exec /usr/bin/python3 -I -S -B "$script_dir/v1/run.py" "$@"
