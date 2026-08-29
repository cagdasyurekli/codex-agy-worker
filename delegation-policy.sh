#!/usr/bin/env bash
# Compatibility entry point; the distributable skill owns the canonical delegation policy runtime.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/skills/agy-worker/runtime/delegation-policy.sh" "$@"
