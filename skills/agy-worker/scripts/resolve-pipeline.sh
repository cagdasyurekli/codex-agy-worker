#!/usr/bin/env bash
# Resolve the canonical pipeline from either a plugin cache or install.sh output.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SKILL_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"

is_pipeline() {
    [[ -x "$1/agy-worker.sh" && -x "$1/qa-gate.sh" \
        && -x "$1/model-recommendation.sh" ]]
}

PLUGIN_ROOT="$(CDPATH= cd -- "$SKILL_DIR/../.." 2>/dev/null && pwd -P)" || PLUGIN_ROOT=""
if [[ -n "$PLUGIN_ROOT" ]] && is_pipeline "$PLUGIN_ROOT"; then
    printf '%s\n' "$PLUGIN_ROOT"
    exit 0
fi

MARKER="$SKILL_DIR/.pipeline-root"
if [[ -f "$MARKER" ]]; then
    IFS= read -r INSTALLED_ROOT < "$MARKER" || true
    case "$INSTALLED_ROOT" in
        /*) ;;
        *)
            echo "agy-worker: invalid standalone pipeline marker" >&2
            exit 2
            ;;
    esac
    if is_pipeline "$INSTALLED_ROOT"; then
        printf '%s\n' "$INSTALLED_ROOT"
        exit 0
    fi
fi

echo "agy-worker: pipeline not found beside the plugin or at the standalone install marker" >&2
echo "agy-worker: reinstall from a complete codex-agy-worker checkout" >&2
exit 2
