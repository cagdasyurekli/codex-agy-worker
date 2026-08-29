#!/usr/bin/env bash
# Compatibility entry point for repository users; the distributable skill owns the
# canonical runtime. Derive a deterministic external state path when unset.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -z "${AGY_WORKER_LOG_DIR:-}" ]]; then
    if ! derived_log_dir="$(python3 -I -S -B - "$SCRIPT_DIR" <<'PY'
import hashlib
import os
import sys

script_dir = sys.argv[1]
xdg_state = os.environ.get("XDG_STATE_HOME")
home = os.environ.get("HOME")

state_home = None
if xdg_state and os.path.isabs(xdg_state):
    state_home = os.path.abspath(xdg_state)
elif home and os.path.isabs(home):
    state_home = os.path.abspath(os.path.join(home, ".local", "state"))

if not state_home:
    sys.exit(1)

checkout_sha = hashlib.sha256(script_dir.encode("utf-8")).hexdigest()
print(os.path.join(state_home, "agy-worker", "checkouts", checkout_sha, "logs"))
PY
)"; then
        echo "agy-worker.sh: unable to derive a safe state root; set an explicit external AGY_WORKER_LOG_DIR" >&2
        exit 64
    fi
    export AGY_WORKER_LOG_DIR="$derived_log_dir"
fi
exec "$SCRIPT_DIR/skills/agy-worker/runtime/agy-worker.sh" "$@"
