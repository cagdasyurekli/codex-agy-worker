#!/usr/bin/env bash
# qa-gate.sh — independently verify an agy worker envelope against reality.
#
# The envelope is a CLAIM. Only driver-owned --verify commands execute here.
# Worker-supplied commands are untrusted data and are never evaluated.
#
# usage: qa-gate.sh --envelope FILE --repo DIR --base FULL_COMMIT_ID
#                   [--allow PATHGLOB]... [--only PATHGLOB]...
#                   [--expect-edits] --verify COMMAND [--verify COMMAND]...
# exit: 0 accepted · 10 scope violation · 11 untrusted command/test claim
#       12 malformed envelope · 13 expected edits missing
#       14 driver verification failed/mutated repo · 15 worker escalation
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${AGY_WORKER_SCHEMA:-$SCRIPT_DIR/schemas/worker-result.schema.json}"

envelope=""; repo="$PWD"; base=""; expect_edits=0
allow=(); only=(); verify=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --envelope) [[ $# -ge 2 ]] || exit 64; envelope="$2"; shift 2 ;;
        --repo) [[ $# -ge 2 ]] || exit 64; repo="$2"; shift 2 ;;
        --base) [[ $# -ge 2 ]] || exit 64; base="$2"; shift 2 ;;
        --allow) [[ $# -ge 2 ]] || exit 64; allow+=("$2"); shift 2 ;;
        --only) [[ $# -ge 2 ]] || exit 64; only+=("$2"); shift 2 ;;
        --verify)
            [[ $# -ge 2 && "$2" == *[![:space:]]* ]] || {
                echo "qa-gate.sh: --verify requires a non-empty command" >&2; exit 64;
            }
            verify+=("$2"); shift 2 ;;
        --expect-edits) expect_edits=1; shift ;;
        *) echo "qa-gate.sh: unknown arg: $1" >&2; exit 64 ;;
    esac
done

[[ -n "$envelope" && -f "$envelope" ]] || {
    echo "qa-gate.sh: --envelope FILE required" >&2; exit 64;
}
envelope="$(cd "$(dirname "$envelope")" && pwd)/$(basename "$envelope")"
[[ -d "$repo" ]] || { echo "qa-gate.sh: --repo DIR required" >&2; exit 64; }
repo="$(cd "$repo" && pwd -P)"
[[ -f "$SCHEMA" ]] || { echo "qa-gate.sh: schema not found: $SCHEMA" >&2; exit 64; }
SCHEMA="$(cd "$(dirname "$SCHEMA")" && pwd)/$(basename "$SCHEMA")"

git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "qa-gate.sh: not a git worktree: $repo" >&2; exit 64;
}
case "$base" in
    ''|*[!0-9a-f]*)
        echo "qa-gate.sh: --base must be the full immutable commit ID captured before dispatch" >&2
        exit 64 ;;
esac
if [[ ${#base} -ne 40 && ${#base} -ne 64 ]]; then
    echo "qa-gate.sh: --base must be a full 40- or 64-character commit ID" >&2
    exit 64
fi
resolved_base="$(git -C "$repo" rev-parse --verify "$base^{commit}" 2>/dev/null)" || {
    echo "qa-gate.sh: invalid base commit: $base" >&2; exit 64;
}
[[ "$resolved_base" == "$base" ]] || {
    echo "qa-gate.sh: base did not resolve to the exact supplied commit" >&2; exit 64;
}

"$SCRIPT_DIR/scripts/validate-envelope.py" "$SCHEMA" "$envelope" || exit 12

scope_check() {
    python3 - "$envelope" "$repo" "$base" "${#allow[@]}" "${#only[@]}" \
        ${allow[@]+"${allow[@]}"} ${only[@]+"${only[@]}"} <<'PY'
import fnmatch
import json
import os
import posixpath
import subprocess
import sys

envelope_path, repo, base = sys.argv[1:4]
allow_count, only_count = map(int, sys.argv[4:6])
allow = sys.argv[6:6 + allow_count]
only = sys.argv[6 + allow_count:6 + allow_count + only_count]

def git_output(*args):
    return subprocess.run(
        ["git", "-C", repo, *args], check=True,
        stdout=subprocess.PIPE).stdout

def git_paths(*args):
    return {
        part.decode("utf-8", "surrogateescape")
        for part in git_output(*args).split(b"\0") if part
    }

def normalize_claim(path):
    if not isinstance(path, str):
        raise ValueError("file path is not a string")
    path = path.strip()
    if not path:
        raise ValueError("file path is empty")
    if os.path.isabs(path):
        # Resolve directory aliases such as macOS /var -> /private/var, but do not
        # resolve the final path component: a worker must not smuggle a symlink
        # target outside the repository into the comparison.
        parent = os.path.realpath(os.path.dirname(path))
        path = os.path.relpath(os.path.join(parent, os.path.basename(path)), repo)
    elif path.startswith("./"):
        path = path[2:]
    path = posixpath.normpath(path)
    if path in ("", ".", "..") or path.startswith("../") or posixpath.isabs(path):
        raise ValueError(f"file path escapes repository: {path!r}")
    return path

try:
    with open(envelope_path, encoding="utf-8") as handle:
        envelope = json.load(handle)
    claimed_changes = {}
    for item in envelope["files_changed"]:
        path = normalize_claim(item["path"])
        if path in claimed_changes:
            raise ValueError(f"duplicate claimed path: {path!r}")
        claimed_changes[path] = item["change"]

    parts = [part for part in git_output(
        "diff", "--name-status", "--no-renames", "-z", base, "--"
    ).split(b"\0") if part]
    if len(parts) % 2:
        raise ValueError("unexpected git name-status output")
    actual_changes = {}
    for index in range(0, len(parts), 2):
        status = parts[index].decode("ascii", "strict")
        path = parts[index + 1].decode("utf-8", "surrogateescape")
        actual_changes[path] = (
            "created" if status == "A" else
            "deleted" if status == "D" else
            "modified"
        )
    untracked = git_paths("ls-files", "--others", "--exclude-standard", "-z", "--")
    ignored = git_paths(
        "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--")
    for path in untracked | ignored:
        actual_changes.setdefault(path, "created")
except (OSError, ValueError, subprocess.CalledProcessError) as exc:
    print(f"qa-gate: cannot establish scope: {exc}", file=sys.stderr)
    sys.exit(1)

claimed = set(claimed_changes)
actual = set(actual_changes)
undeclared = {
    path for path in actual - claimed
    if not any(fnmatch.fnmatchcase(path, pattern) for pattern in allow)
}
phantom = claimed - actual
outside_policy = {
    path for path in actual
    if only and not any(fnmatch.fnmatchcase(path, pattern) for pattern in only)
}
kind_mismatches = {
    path: (claimed_changes[path], actual_changes[path])
    for path in claimed & actual
    if claimed_changes[path] != actual_changes[path]
}

if undeclared:
    print("qa-gate: SCOPE VIOLATION - changed but undeclared:", file=sys.stderr)
    for path in sorted(undeclared):
        print(f"    {path}", file=sys.stderr)
if phantom:
    print("qa-gate: SCOPE VIOLATION - declared but unchanged:", file=sys.stderr)
    for path in sorted(phantom):
        print(f"    {path}", file=sys.stderr)
if outside_policy:
    print("qa-gate: ONLY POLICY VIOLATION:", file=sys.stderr)
    for path in sorted(outside_policy):
        print(f"    {path}", file=sys.stderr)
if kind_mismatches:
    print("qa-gate: CHANGE KIND MISMATCH:", file=sys.stderr)
    for path, (claimed_kind, actual_kind) in sorted(kind_mismatches.items()):
        print(f"    {path}: claimed {claimed_kind}, actual {actual_kind}",
              file=sys.stderr)
if undeclared or phantom or outside_policy or kind_mismatches:
    sys.exit(1)
print(f"qa-gate: scope OK ({len(actual)} file(s) changed)", file=sys.stderr)
print(len(claimed))
PY
}

snapshot_repo() {
    python3 - "$repo" "$base" <<'PY'
import hashlib
import os
import stat
import subprocess
import sys

repo, base = sys.argv[1:3]

def git_output(*args):
    return subprocess.run(
        ["git", "-C", repo, *args], check=True,
        stdout=subprocess.PIPE).stdout

def git_paths(*args):
    output = git_output(*args)
    return sorted(part for part in output.split(b"\0") if part)

digest = hashlib.sha256()
tracked_diff = git_output(
    "diff", "--binary", "--no-ext-diff", "--no-textconv",
    "--submodule=short", base, "--")
digest.update(len(tracked_diff).to_bytes(8, "big"))
digest.update(tracked_diff)
paths = set(git_paths("ls-files", "--others", "--exclude-standard", "-z", "--"))
paths.update(git_paths(
    "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--"))
for raw_path in sorted(paths):
    path = raw_path.decode("utf-8", "surrogateescape")
    full_path = os.path.join(repo, path)
    digest.update(len(raw_path).to_bytes(8, "big"))
    digest.update(raw_path)
    try:
        metadata = os.lstat(full_path)
    except FileNotFoundError:
        digest.update(b"deleted")
        continue
    digest.update(str(stat.S_IFMT(metadata.st_mode)).encode("ascii"))
    digest.update(str(stat.S_IMODE(metadata.st_mode)).encode("ascii"))
    if stat.S_ISLNK(metadata.st_mode):
        digest.update(os.readlink(full_path).encode("utf-8", "surrogateescape"))
    elif stat.S_ISREG(metadata.st_mode):
        with open(full_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    else:
        digest.update(b"non-regular")
print(digest.hexdigest())
PY
}

claimed_count="$(scope_check)" || exit 10

status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "$envelope")"
requires_human="$(python3 -c 'import json,sys; print("true" if json.load(open(sys.argv[1]))["requires_human"] else "false")' "$envelope")"

# Escalation is a valid worker outcome, but never accepted work. Scope has already
# been checked so status cannot hide edits.
if [[ "$status" != "completed" || "$requires_human" == "true" ]]; then
    echo "qa-gate: WORKER ESCALATION (status=$status requires_human=$requires_human)" >&2
    exit 15
fi

if (( expect_edits )) && [[ "$claimed_count" == "0" ]]; then
    echo "qa-gate: expected edits, but the completed worker changed nothing" >&2
    exit 13
fi

# Never execute commands supplied by the untrusted envelope. Workers are instructed
# to leave this list empty; a non-empty list is a contract violation, not evidence.
command_count="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["commands_run"]))' "$envelope")"
test_count="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["tests_run"]))' "$envelope")"
if [[ "$command_count" != "0" || "$test_count" != "0" ]]; then
    echo "qa-gate: UNTRUSTED COMMAND CLAIM - commands_run and tests_run must be empty; nothing from the envelope was executed" >&2
    exit 11
fi

if (( ${#verify[@]} == 0 )); then
    echo "qa-gate: at least one driver-owned --verify command is required" >&2
    exit 64
fi

before_snapshot="$(snapshot_repo)" || exit 14
for (( i=0; i<${#verify[@]}; i++ )); do
    vcmd="${verify[$i]}"
    echo "qa-gate: driver verification: $vcmd" >&2
    if ! bash -c "$vcmd"; then
        echo "qa-gate: DRIVER VERIFICATION FAILED - '$vcmd'" >&2
        exit 14
    fi
done
after_snapshot="$(snapshot_repo)" || exit 14
if [[ "$before_snapshot" != "$after_snapshot" ]]; then
    echo "qa-gate: DRIVER VERIFICATION MUTATED THE WORKTREE" >&2
    exit 14
fi

# Re-check path policy after verification as defense in depth.
scope_check >/dev/null || exit 10

echo "qa-gate: ACCEPTED" >&2
exit 0
