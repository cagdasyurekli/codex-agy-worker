#!/usr/bin/env bash
# Install the Codex skill that teaches Codex to use this pipeline.
#
# Installs only the canonical skill bundle and a local pipeline pointer. It does not
# touch agy settings, Codex config, or anything else — those changes are described in
# the README so you can make them yourself, deliberately.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CODEX_SKILLS_DIR:-$HOME/.codex/skills}/agy-worker"

command -v agy >/dev/null || {
    echo "warning: 'agy' not found on PATH. Install Antigravity CLI first." >&2
}

mkdir -p "$DEST"
# A standalone install copies the canonical skill bundle and records this checkout in
# a local marker that is not part of the public package.
python3 - "$HERE/skills/agy-worker" "$DEST" "$HERE" <<'PY'
import os
import pathlib
import sys
import tempfile

source_root = pathlib.Path(sys.argv[1])
destination_root = pathlib.Path(sys.argv[2])
repo_root = pathlib.Path(sys.argv[3]).resolve()


def publish_bytes(source: pathlib.Path, destination: pathlib.Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=destination.parent,
        prefix=f".{destination.name}.", delete=False,
    )
    try:
        with handle:
            handle.write(source.read_bytes())
        os.chmod(handle.name, source.stat().st_mode & 0o777)
        os.replace(handle.name, destination)
    except BaseException:
        try:
            os.unlink(handle.name)
        except FileNotFoundError:
            pass
        raise


for source in sorted(source_root.rglob("*")):
    if source.is_symlink():
        raise SystemExit(f"refusing symlink in skill bundle: {source}")
    if source.is_file():
        publish_bytes(source, destination_root / source.relative_to(source_root))

marker_source = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
try:
    with marker_source:
        marker_source.write(f"{repo_root}\n")
    marker_path = pathlib.Path(marker_source.name)
    os.chmod(marker_path, 0o644)
    publish_bytes(marker_path, destination_root / ".pipeline-root")
finally:
    try:
        os.unlink(marker_source.name)
    except FileNotFoundError:
        pass
PY

echo "installed: $DEST/SKILL.md"
echo "pipeline:  $HERE"
echo
echo "Next: add the sandbox settings from the README to ~/.codex/config.toml,"
echo "or agy will fail under Codex with exit 5 and an empty error."
echo "Then start a new Codex session and ask:"
echo '  "Use agy-worker for a batched test task in <absolute repo path>; verify with python3 -m pytest -q tests/<module>."'
