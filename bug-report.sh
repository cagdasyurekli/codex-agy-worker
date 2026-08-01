#!/usr/bin/env bash
# Create, preview, and explicitly submit sanitized GitHub bug drafts.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/scripts/bug-report.py" "$@"
