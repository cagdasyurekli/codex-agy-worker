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
python3 - "$HERE/codex-skill/SKILL.md" "$DEST/SKILL.md" "$HERE" <<'PY'
import os
import pathlib
import sys
import tempfile

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
repo_root = sys.argv[3]
rendered = source.read_text(encoding="utf-8").replace("__REPO_ROOT__", repo_root)
handle = tempfile.NamedTemporaryFile(
    mode="w", encoding="utf-8", dir=destination.parent,
    prefix=".SKILL.md.", delete=False,
)
try:
    with handle:
        handle.write(rendered)
    os.replace(handle.name, destination)
except BaseException:
    try:
        os.unlink(handle.name)
    except FileNotFoundError:
        pass
    raise
PY

echo "installed: $DEST/SKILL.md"
echo "pipeline:  $HERE"
echo
echo "Next: add the sandbox settings from the README to ~/.codex/config.toml,"
echo "or agy will fail under Codex with exit 5 and an empty error."
echo "Then start a new Codex session and ask:"
echo '  "Use agy-worker for a batched test task in <absolute repo path>; verify with python3 -m pytest -q tests/<module>."'
