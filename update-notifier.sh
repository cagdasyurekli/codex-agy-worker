#!/bin/bash
# Local macOS update-notifier lifecycle wrapper.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec /usr/bin/python3 -I -S -B \
    "$SCRIPT_DIR/scripts/update_notifier.py" "$@"
