#!/usr/bin/env bash
# Install the Codex skill that teaches Codex to use this pipeline.
#
# Installs ONLY the skill file. It does not touch your agy settings, your Codex
# config, or anything else — those changes are described in the README so you can
# make them yourself, deliberately.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CODEX_SKILLS_DIR:-$HOME/.codex/skills}/agy-worker"

command -v agy >/dev/null || {
    echo "warning: 'agy' not found on PATH. Install Antigravity CLI first." >&2
}

mkdir -p "$DEST"
# The skill hardcodes no paths except this repo's location, so bake in where the
# user actually cloned it rather than assuming ~/Documents/Projects.
sed "s|__REPO_ROOT__|$HERE|g" "$HERE/codex-skill/SKILL.md" > "$DEST/SKILL.md"

echo "installed: $DEST/SKILL.md"
echo "pipeline:  $HERE"
echo
echo "Next: add the sandbox settings from the README to ~/.codex/config.toml,"
echo "or agy will fail under Codex with exit 5 and an empty error."
