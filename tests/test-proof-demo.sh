#!/usr/bin/env bash
# Offline adversarial tests for the synthetic starter proof.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
HOST_PYTHON="$(command -v python3)"
REAL_GIT="$(command -v git)"
TMP="$(mktemp -d -t agyworker-proof-tests.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

EXPECTED_OUTPUT="$(printf '%s\n' \
    'honest: gate-passed (exit 0)' \
    'mismatch: rejected (exit 10)' \
    'starter proof only; no candidate accepted because no human review occurred')"

snapshot_tree() {
    "$HOST_PYTHON" -B - "$1" <<'PY'
import hashlib
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
digest = hashlib.sha256()
root_info = root.lstat()
digest.update(b".\0" + oct(stat.S_IMODE(root_info.st_mode)).encode() + b"\0directory\0")
for current, dirs, files in os.walk(root):
    dirs[:] = sorted(dirs)
    for name in sorted(dirs + files):
        path = Path(current) / name
        relative = path.relative_to(root).as_posix().encode("utf-8", "surrogateescape")
        info = path.lstat()
        digest.update(relative + b"\0" + oct(stat.S_IMODE(info.st_mode)).encode() + b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8", "surrogateescape"))
        elif path.is_dir():
            digest.update(b"directory\0")
        elif path.is_file():
            digest.update(b"file\0")
            digest.update(path.read_bytes())
        else:
            digest.update(b"special\0" + str(stat.S_IFMT(info.st_mode)).encode())
        digest.update(b"\0")
print(digest.hexdigest())
PY
}

list_proof_roots() {
    "$HOST_PYTHON" -B - <<'PY'
from pathlib import Path

seen = set()
matches = []
for raw_base in ("/private/tmp", "/tmp"):
    try:
        base = Path(raw_base).resolve(strict=True)
    except OSError:
        continue
    if base in seen:
        continue
    seen.add(base)
    try:
        matches.extend(str(path) for path in base.glob("agy-worker-proof.*"))
    except OSError:
        pass
print("\n".join(sorted(matches)))
PY
}

process_alive() {
    case "${1:-}" in
        ''|*[!0-9]*) return 1 ;;
        *) kill -0 "$1" 2>/dev/null ;;
    esac
}

make_demo_tree() {
    local destination="$1"
    mkdir -p "$destination/demo/fixtures" \
        "$destination/skills/agy-worker/runtime/scripts" \
        "$destination/skills/agy-worker/runtime/schemas"
    cp "$ROOT/proof-demo.sh" "$ROOT/qa-gate.sh" "$destination/"
    cp "$ROOT/demo/fixtures/"*.json "$destination/demo/fixtures/"
    cp "$ROOT/skills/agy-worker/runtime/qa-gate.sh" \
        "$destination/skills/agy-worker/runtime/qa-gate.sh"
    cp "$ROOT/skills/agy-worker/runtime/scripts/validate-envelope.py" \
        "$destination/skills/agy-worker/runtime/scripts/validate-envelope.py"
    cp "$ROOT/skills/agy-worker/runtime/schemas/worker-result.schema.json" \
        "$destination/skills/agy-worker/runtime/schemas/worker-result.schema.json"
    chmod +x "$destination/proof-demo.sh" "$destination/qa-gate.sh" \
        "$destination/skills/agy-worker/runtime/qa-gate.sh" \
        "$destination/skills/agy-worker/runtime/scripts/validate-envelope.py"
}

mkdir -p "$TMP/bin" "$TMP/caller-temp/agy-worker-proof.COLLISION"
printf 'preserve me\n' > "$TMP/caller-temp/agy-worker-proof.COLLISION/sentinel"
for forbidden in agy gemini curl wget gh npm npx credential update.sh; do
    printf '#!/usr/bin/env bash\n: > "$DEMO_FORBIDDEN_MARKER"\nexit 97\n' \
        > "$TMP/bin/$forbidden"
    chmod +x "$TMP/bin/$forbidden"
done
printf '%s\n' '#!/usr/bin/env bash' \
    'if [[ "$1" == "-C" && "${3:-}" == "init" ]]; then' \
    '  "$DEMO_HOST_PYTHON" -B - "$2" "$DEMO_GIT_LOG" <<'"'"'PY'"'"'' \
    'import os, stat, sys' \
    'repo, output = sys.argv[1:]' \
    'root = os.path.dirname(repo)' \
    'mode = stat.S_IMODE(os.stat(root).st_mode)' \
    'with open(output, "a", encoding="utf-8") as handle:' \
    '    handle.write(f"{root}|{os.path.basename(repo)}|{mode:03o}|{os.stat(root).st_uid}\n")' \
    'PY' \
    'fi' \
    'exec "$DEMO_REAL_GIT" "$@"' > "$TMP/bin/git"
chmod +x "$TMP/bin/git"

run_demo() {
    local demo_root="$1" label="$2"
    TMPDIR="$TMP/caller-temp" \
    DEMO_REAL_GIT="$REAL_GIT" DEMO_HOST_PYTHON="$HOST_PYTHON" \
    DEMO_GIT_LOG="$TMP/$label.git-log" \
    DEMO_FORBIDDEN_MARKER="$TMP/$label.forbidden" \
    PATH="$TMP/bin:$PATH" \
        "$demo_root/proof-demo.sh" \
        > "$TMP/$label.out" 2> "$TMP/$label.err"
}

echo "proof-demo.sh offline test suite"
echo

initial_proof_roots="$(list_proof_roots)"
before_tree="$(snapshot_tree "$ROOT")"
start_seconds="$(date +%s)"
run_demo "$ROOT" normal
normal_rc=$?
elapsed=$(( $(date +%s) - start_seconds ))
after_tree="$(snapshot_tree "$ROOT")"
if [[ "$normal_rc" == 0 && "$(cat "$TMP/normal.out")" == "$EXPECTED_OUTPUT" \
        && ! -s "$TMP/normal.err" ]]; then
    ok "maintained gate proves honest exit 0 and mismatch exit 10"
else
    bad "maintained gate proves honest exit 0 and mismatch exit 10"
fi
if (( elapsed < 60 )); then
    ok "starter proof completes in under 60 seconds"
else
    bad "starter proof completes in under 60 seconds"
fi
if [[ "$before_tree" == "$after_tree" ]]; then
    ok "starter proof leaves the current checkout byte-identical"
else
    bad "starter proof leaves the current checkout byte-identical"
fi

snapshot_repo="$TMP/snapshot-negative"
mkdir -p "$snapshot_repo"
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    "$REAL_GIT" -C "$snapshot_repo" init -q
snapshot_before="$(snapshot_tree "$snapshot_repo")"
printf '\n[proof-negative]\n\tchanged = true\n' >> "$snapshot_repo/.git/config"
snapshot_after="$(snapshot_tree "$snapshot_repo")"
if [[ "$snapshot_before" != "$snapshot_after" ]]; then
    ok "checkout snapshot detects a synthetic Git metadata mutation"
else
    bad "checkout snapshot detects a synthetic Git metadata mutation"
fi
if [[ ! -e "$TMP/normal.forbidden" ]]; then
    ok "starter proof invokes no forbidden offline executable"
else
    bad "starter proof invokes no forbidden offline executable"
fi
if [[ -f "$TMP/caller-temp/agy-worker-proof.COLLISION/sentinel" ]] \
        && grep -Fxq 'preserve me' \
            "$TMP/caller-temp/agy-worker-proof.COLLISION/sentinel"; then
    ok "caller temp collision and sentinel are preserved"
else
    bad "caller temp collision and sentinel are preserved"
fi
if "$HOST_PYTHON" -B - "$TMP/normal.git-log" <<'PY'
import os
import sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
initial = [line.split("|") for line in lines if line.split("|")[1] in {"honest", "mismatch"}]
assert len(initial) == 2
assert {item[1] for item in initial} == {"honest", "mismatch"}
assert len({item[0] for item in initial}) == 1
assert all(item[2] == "700" and int(item[3]) == os.getuid() for item in initial)
assert not os.path.exists(initial[0][0])
PY
then
    ok "two independent repos use one private owned root and clean normally"
else
    bad "two independent repos use one private owned root and clean normally"
fi

run_demo "$ROOT" deterministic
if [[ "$?" == 0 ]] && cmp -s "$TMP/normal.out" "$TMP/deterministic.out" \
        && ! grep -Fq "$TMP" "$TMP/deterministic.out" "$TMP/deterministic.err" \
        && [[ "$(wc -l < "$TMP/deterministic.out" | tr -d ' ')" == 3 ]]; then
    ok "success output is deterministic, three-line, bounded, and nonleaking"
else
    bad "success output is deterministic, three-line, bounded, and nonleaking"
fi

"$ROOT/proof-demo.sh" unexpected > "$TMP/args.out" 2> "$TMP/args.err"
if [[ "$?" == 64 && ! -s "$TMP/args.out" && ! -s "$TMP/args.err" ]]; then
    ok "arguments are rejected without starting a proof"
else
    bad "arguments are rejected without starting a proof"
fi

VALID_TREE="$TMP/valid-tree"
make_demo_tree "$VALID_TREE"
run_demo "$VALID_TREE" valid-copy
if [[ "$?" == 0 && "$(cat "$TMP/valid-copy.out")" == "$EXPECTED_OUTPUT" ]]; then
    ok "canonical copied fixture pair remains valid"
else
    bad "canonical copied fixture pair remains valid"
fi

for fixture_mode in malformed extra altered missing; do
    fixture_tree="$TMP/fixture-$fixture_mode"
    make_demo_tree "$fixture_tree"
    case "$fixture_mode" in
        malformed)
            printf '{not json\n' > "$fixture_tree/demo/fixtures/honest-envelope.json"
            ;;
        extra)
            "$HOST_PYTHON" -B - "$fixture_tree/demo/fixtures/honest-envelope.json" <<'PY'
import json, sys
path = sys.argv[1]
value = json.load(open(path, encoding="utf-8"))
value["extra"] = True
open(path, "w", encoding="utf-8").write(json.dumps(value, indent=2) + "\n")
PY
            ;;
        altered)
            "$HOST_PYTHON" -B - "$fixture_tree/demo/fixtures/scope-mismatch-envelope.json" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.write_text(path.read_text().replace('"proof.txt"', '"hidden.txt"'), encoding="utf-8")
PY
            ;;
        missing)
            rm -f "$fixture_tree/demo/fixtures/honest-envelope.json"
            ;;
    esac
    run_demo "$fixture_tree" "fixture-$fixture_mode"
    rc=$?
    if [[ "$rc" == 3 && ! -s "$TMP/fixture-$fixture_mode.out" ]] \
            && grep -Fxq 'proof-demo: starter proof failed' \
                "$TMP/fixture-$fixture_mode.err"; then
        ok "$fixture_mode fixture fails closed"
    else
        bad "$fixture_mode fixture fails closed"
    fi
done

for gate_mode in missing nonexec trusting; do
    gate_tree="$TMP/gate-$gate_mode"
    make_demo_tree "$gate_tree"
    case "$gate_mode" in
        missing) rm -f "$gate_tree/qa-gate.sh" ;;
        nonexec) chmod -x "$gate_tree/qa-gate.sh" ;;
        trusting)
            printf '#!/usr/bin/env bash\nexit 0\n' > "$gate_tree/qa-gate.sh"
            chmod +x "$gate_tree/qa-gate.sh"
            ;;
    esac
    run_demo "$gate_tree" "gate-$gate_mode"
    rc=$?
    if [[ "$rc" == 3 && ! -s "$TMP/gate-$gate_mode.out" ]] \
            && grep -Fxq 'proof-demo: starter proof failed' \
                "$TMP/gate-$gate_mode.err"; then
        ok "$gate_mode repository-relative gate cannot pass the proof"
    else
        bad "$gate_mode repository-relative gate cannot pass the proof"
    fi
done

mkdir -p "$TMP/signal-bin"
printf '%s\n' '#!/usr/bin/env bash' \
    'if [[ "$1" == "-C" && "${3:-}" == "init" ]]; then' \
    '  printf "%s\n" "$$" > "$DEMO_SIGNAL_CHILD"' \
    '  "$DEMO_HOST_PYTHON" -B -c '\''import os; print(os.getpgrp())'\'' > "$DEMO_SIGNAL_PGID"' \
    '  printf "%s\n" "${2%/*}" > "$DEMO_SIGNAL_ROOT"' \
    '  /bin/bash -c '\''trap "" HUP INT TERM; while :; do /bin/sleep 1; done'\'' &' \
    '  signal_grandchild=$!' \
    '  printf "%s\n" "$signal_grandchild" > "$DEMO_SIGNAL_GRANDCHILD"' \
    '  : > "$DEMO_SIGNAL_READY"' \
    '  trap "" HUP INT TERM' \
    '  while :; do /bin/sleep 1; done' \
    'fi' \
    'exec "$DEMO_REAL_GIT" "$@"' > "$TMP/signal-bin/git"
chmod +x "$TMP/signal-bin/git"

for signal_name in HUP INT TERM; do
    label="signal-$signal_name"
    DEMO_REAL_GIT="$REAL_GIT" \
    DEMO_HOST_PYTHON="$HOST_PYTHON" \
    DEMO_SIGNAL_CHILD="$TMP/$label.child" \
    DEMO_SIGNAL_PGID="$TMP/$label.pgid" \
    DEMO_SIGNAL_GRANDCHILD="$TMP/$label.grandchild" \
    DEMO_SIGNAL_ROOT="$TMP/$label.root" \
    DEMO_SIGNAL_READY="$TMP/$label.ready" \
    PATH="$TMP/signal-bin:$PATH" \
        "$HOST_PYTHON" -B -c '
import os, signal, sys
for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, signal.SIG_DFL)
os.execv("/bin/bash", ["/bin/bash", sys.argv[1]])
' "$ROOT/proof-demo.sh" > "$TMP/$label.out" 2> "$TMP/$label.err" &
    demo_pid=$!
    ready=0
    for (( poll=0; poll<100; poll++ )); do
        if [[ -e "$TMP/$label.ready" && -s "$TMP/$label.child" \
                && -s "$TMP/$label.pgid" \
                && -s "$TMP/$label.grandchild" \
                && -s "$TMP/$label.root" ]]; then
            ready=1
            break
        fi
        /bin/sleep 0.05
    done
    if (( ready )); then kill -s "$signal_name" "$demo_pid" 2>/dev/null || true; fi
    completed=0
    for (( poll=0; poll<100; poll++ )); do
        if ! kill -0 "$demo_pid" 2>/dev/null; then completed=1; break; fi
        /bin/sleep 0.05
    done
    if (( completed )); then wait "$demo_pid"; rc=$?; else rc=99; fi
    child_pid="$(cat "$TMP/$label.child" 2>/dev/null || true)"
    child_pgid="$(cat "$TMP/$label.pgid" 2>/dev/null || true)"
    grandchild_pid="$(cat "$TMP/$label.grandchild" 2>/dev/null || true)"
    work_root="$(cat "$TMP/$label.root" 2>/dev/null || true)"
    descendants_gone=0
    for (( poll=0; poll<100; poll++ )); do
        if ! process_alive "$child_pid" && ! process_alive "$grandchild_pid"; then
            descendants_gone=1
            break
        fi
        /bin/sleep 0.05
    done
    if (( ready && completed )) && [[ "$rc" == 3 && ! -s "$TMP/$label.out" ]] \
            && grep -Fxq 'proof-demo: interrupted' "$TMP/$label.err" \
            && [[ "$child_pgid" == "$child_pid" && "$child_pgid" != "$demo_pid" ]] \
            && (( descendants_gone )) \
            && [[ -n "$work_root" && ! -e "$work_root" ]]; then
        ok "$signal_name cleans the private root, direct child, and grandchild"
    else
        bad "$signal_name cleans the private root, direct child, and grandchild"
        printf '       ready=%s completed=%s rc=%s child=%s pgid=%s demo=%s child_alive=%s grandchild_alive=%s root_exists=%s\n' \
            "$ready" "$completed" "$rc" \
            "$child_pid" "$child_pgid" "$demo_pid" \
            "$(process_alive "$child_pid" && echo yes || echo no)" \
            "$(process_alive "$grandchild_pid" && echo yes || echo no)" \
            "$([[ -e "$work_root" ]] && echo yes || echo no)"
        kill -KILL "$demo_pid" "$child_pid" "$grandchild_pid" 2>/dev/null || true
        wait "$demo_pid" 2>/dev/null || true
    fi
done

final_proof_roots="$(list_proof_roots)"
if [[ "$initial_proof_roots" == "$final_proof_roots" ]]; then
    ok "complete suite leaves no new proof workspace root"
else
    bad "complete suite leaves no new proof workspace root"
fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
