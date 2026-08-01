#!/usr/bin/env bash
# qa-gate.sh — independently verify an agy worker envelope against reality.
#
# The envelope is a CLAIM. This script is the driver's evidence. It never asks agy
# anything; it looks at the repo and re-runs the tests itself. A worker that lies,
# omits a touched file, or reports a passing test that fails must not get through.
#
# usage:  qa-gate.sh --envelope FILE --repo DIR [--base GITREF] [--allow PATHGLOB]...
# exit:   0 accept · 10 scope violation · 11 test claim false · 12 malformed envelope
#         13 nothing changed but worker claimed completion · 14 driver verification failed
set -euo pipefail

envelope=""; repo="$PWD"; base="HEAD"; allow=(); verify=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --envelope) envelope="$2"; shift 2 ;;
        --repo)     repo="$2"; shift 2 ;;
        --base)     base="$2"; shift 2 ;;
        --allow)    allow+=("$2"); shift 2 ;;
        --verify)   verify+=("$2"); shift 2 ;;
        *) echo "qa-gate.sh: unknown arg: $1" >&2; exit 64 ;;
    esac
done
[[ -f "$envelope" ]] || { echo "qa-gate.sh: --envelope FILE required" >&2; exit 64; }

cd "$repo"

# --- 1. envelope must be well-formed -----------------------------------------
python3 - "$envelope" <<'PY' || exit 12
import json, sys
try:
    e = json.load(open(sys.argv[1]))
except Exception as ex:
    print(f"qa-gate: envelope is not valid JSON: {ex}", file=sys.stderr); sys.exit(1)
missing = {"status","summary","files_changed","commands_run","tests_run",
           "risks","open_questions","confidence","requires_human"} - set(e)
if missing:
    print(f"qa-gate: envelope missing fields: {sorted(missing)}", file=sys.stderr); sys.exit(1)
PY

status=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['status'])" "$envelope")
requires_human=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['requires_human'])" "$envelope")

# A worker that admits it is blocked is behaving correctly — escalate, don't fail it.
if [[ "$status" == "blocked" || "$requires_human" == "True" ]]; then
    echo "qa-gate: worker escalated (status=$status requires_human=$requires_human) — routing to human." >&2
    exit 0
fi

# --- 2. scope check: claimed files vs actual git diff ------------------------
actual=$(git diff --name-only "$base" 2>/dev/null || true)
untracked=$(git ls-files --others --exclude-standard 2>/dev/null || true)
actual=$(printf '%s\n%s\n' "$actual" "$untracked" | sed '/^$/d' | sort -u)

python3 - "$envelope" "${allow[@]+"${allow[@]}"}" <<PY || exit 10
import json, sys, fnmatch, os, posixpath
env = json.load(open("$envelope"))
repo = os.path.realpath("$repo")

def norm(p):
    """Workers legitimately report absolute paths (we instruct them to use absolute
    paths, since agy has no reliable cwd). git reports repo-relative. Normalise both
    to repo-relative or they never match and every job looks like a scope violation."""
    p = p.strip()
    if not p:
        return ""
    if os.path.isabs(p):
        try:
            p = os.path.relpath(os.path.realpath(p), repo)
        except ValueError:
            pass
    return posixpath.normpath(p).lstrip("./")

claimed = {norm(f["path"]) for f in env["files_changed"]}
actual  = {norm(l) for l in """$actual""".splitlines() if l.strip()}
claimed.discard(""); actual.discard("")
allow   = sys.argv[2:]

undeclared = {p for p in actual - claimed
              if not any(fnmatch.fnmatch(p, g) for g in allow)}
phantom    = claimed - actual

if undeclared:
    print("qa-gate: SCOPE VIOLATION — files changed but not declared:", file=sys.stderr)
    for p in sorted(undeclared): print(f"    {p}", file=sys.stderr)
    sys.exit(1)
if phantom:
    print("qa-gate: envelope claims files that did not change:", file=sys.stderr)
    for p in sorted(phantom): print(f"    {p}", file=sys.stderr)
    sys.exit(1)
print(f"qa-gate: scope OK ({len(actual)} file(s) changed, all declared)", file=sys.stderr)
PY

# --- 3. did anything actually happen? ----------------------------------------
if [[ "$status" == "completed" && -z "$actual" ]]; then
    # Only meaningful for edit-mode jobs; a plan-mode job legitimately changes nothing.
    if [[ "${QA_EXPECT_EDITS:-0}" == "1" ]]; then
        echo "qa-gate: worker claims 'completed' but no files changed." >&2
        exit 13
    fi
fi

# --- 4. re-run every claimed test ourselves ----------------------------------
# The worker's `passed` flag is hearsay. This is the evidence.
# NOTE: no `mapfile` here — macOS ships bash 3.2 and mapfile is bash 4+. A plain
# read loop keeps this working on the box it actually runs on.
test_cmds=()
while IFS= read -r line; do
    test_cmds+=("$line")
done < <(python3 -c "
import json,sys
for t in json.load(open(sys.argv[1]))['tests_run']: print(t['command'])
" "$envelope")

fail=0
for (( i=0; i<${#test_cmds[@]}; i++ )); do
    cmd="${test_cmds[$i]}"
    [[ -z "$cmd" ]] && continue
    claimed_pass=$(python3 -c "
import json,sys;print(json.load(open(sys.argv[1]))['tests_run'][int(sys.argv[2])]['passed'])" "$envelope" "$i")
    echo "qa-gate: re-running: $cmd" >&2
    if bash -c "$cmd" >/dev/null 2>&1; then actual_pass=True; else actual_pass=False; fi
    if [[ "$claimed_pass" != "$actual_pass" ]]; then
        echo "qa-gate: FALSE TEST CLAIM — '$cmd' claimed passed=$claimed_pass, actually $actual_pass" >&2
        fail=1
    fi
done
(( fail )) && exit 11

# --- 5. driver-owned verification --------------------------------------------
# Step 4 only re-runs what the worker CLAIMED. A worker that reports tests_run=[]
# would otherwise sail through having run nothing — the envelope cannot be allowed
# to determine how hard it is checked. These commands come from the driver and run
# no matter what the worker said.
if (( ${#verify[@]} == 0 )); then
    echo "qa-gate: WARNING — no --verify command given; acceptance rests on the" >&2
    echo "qa-gate:           worker's own claims. Pass --verify '<test cmd>' to fix." >&2
else
    for (( i=0; i<${#verify[@]}; i++ )); do
        vcmd="${verify[$i]}"
        echo "qa-gate: driver verification: $vcmd" >&2
        if ! bash -c "$vcmd"; then
            echo "qa-gate: DRIVER VERIFICATION FAILED — '$vcmd'" >&2
            exit 14
        fi
    done
fi

echo "qa-gate: ACCEPTED" >&2
exit 0
