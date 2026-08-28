#!/usr/bin/env bash
# Compatibility entry point; the distributable skill owns the canonical usage observer.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$SCRIPT_DIR/skills/agy-worker/runtime/codex-usage-report.sh" "$@"
