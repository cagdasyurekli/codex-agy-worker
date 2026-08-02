#!/usr/bin/env bash
# Compatibility entry point for repository users; the distributable skill owns the
# canonical runtime. Preserve the historical repository-root log location.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -z "${AGY_WORKER_LOG_DIR:-}" ]]; then
    export AGY_WORKER_LOG_DIR="$SCRIPT_DIR/logs"
fi
exec "$SCRIPT_DIR/skills/agy-worker/runtime/agy-worker.sh" "$@"
