#!/usr/bin/env bash
# Explicit bounded metadata-only feedback triage.  See scripts/feedback-triage.py.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3 -I -S -B "$SCRIPT_DIR/scripts/feedback-triage.py" "$@"
