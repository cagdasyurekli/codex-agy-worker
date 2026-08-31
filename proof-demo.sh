#!/usr/bin/env bash
# Synthetic, offline proof of qa-gate's independent Git-scope boundary.
set -uo pipefail

PROOF_WORK_DIR=''
PROOF_ACTIVE_PID=''
PROOF_ACTIVE_PGID=''
PROOF_RUN_SEQUENCE=0

proof_cleanup() {
    local work_dir="${PROOF_WORK_DIR:-}"
    [[ -n "$work_dir" ]] || return 0
    case "$work_dir" in
        /private/tmp/agy-worker-proof.*|/tmp/agy-worker-proof.*) ;;
        *) return 1 ;;
    esac
    [[ -d "$work_dir" && ! -L "$work_dir" && -O "$work_dir" ]] || return 1
    /bin/rm -rf -- "$work_dir" 2>/dev/null || return 1
    [[ ! -e "$work_dir" ]] || return 1
    PROOF_WORK_DIR=''
}

proof_stop_active() {
    local signal_name="$1" active_pid="${PROOF_ACTIVE_PID:-}"
    local active_pgid="${PROOF_ACTIVE_PGID:-}" poll
    case "$active_pgid" in
        ''|*[!0-9]*) ;;
        *) kill -s "$signal_name" -- "-$active_pgid" 2>/dev/null || true ;;
    esac
    case "$active_pid" in
        ''|*[!0-9]*) ;;
        *) kill -s "$signal_name" -- "$active_pid" 2>/dev/null || true ;;
    esac
    for (( poll=0; poll<5; poll++ )); do
        case "$active_pgid" in
            ''|*[!0-9]*) break ;;
            *) kill -0 -- "-$active_pgid" 2>/dev/null || break ;;
        esac
        /bin/sleep 0.01
    done
    case "$active_pgid" in
        ''|*[!0-9]*) ;;
        *) kill -KILL -- "-$active_pgid" 2>/dev/null || true ;;
    esac
    case "$active_pid" in
        ''|*[!0-9]*) ;;
        *)
            kill -KILL -- "$active_pid" 2>/dev/null || true
            wait "$active_pid" 2>/dev/null || true
            ;;
    esac
    case "$active_pgid" in
        ''|*[!0-9]*) ;;
        *) kill -KILL -- "-$active_pgid" 2>/dev/null || true ;;
    esac
    PROOF_ACTIVE_PID=''
    PROOF_ACTIVE_PGID=''
}

proof_on_signal() {
    local signal_name="$1"
    trap - HUP INT TERM
    proof_stop_active "$signal_name"
    proof_cleanup >/dev/null 2>&1 || true
    echo "proof-demo: interrupted" >&2
    exit 3
}

proof_run() {
    local ready_marker poll ready=0 rc
    PROOF_RUN_SEQUENCE=$((PROOF_RUN_SEQUENCE + 1))
    ready_marker="$PROOF_WORK_DIR/.proof-run-ready.$PROOF_RUN_SEQUENCE"
    [[ -n "$PROOF_WORK_DIR" && ! -e "$ready_marker" ]] || return 1
    python3 -B - "$ready_marker" "$@" <<'PY' >/dev/null 2>&1 &
import os
import sys

marker = sys.argv[1]
command = sys.argv[2:]
if not command:
    raise SystemExit(1)
os.setsid()
descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.close(descriptor)
os.execvp(command[0], command)
PY
    PROOF_ACTIVE_PID=$!
    PROOF_ACTIVE_PGID=$PROOF_ACTIVE_PID
    for (( poll=0; poll<500; poll++ )); do
        if [[ -f "$ready_marker" && ! -L "$ready_marker" ]]; then
            ready=1
            break
        fi
        kill -0 -- "$PROOF_ACTIVE_PID" 2>/dev/null || break
        /bin/sleep 0.01
    done
    /bin/rm -f -- "$ready_marker" 2>/dev/null || {
        proof_stop_active KILL
        return 1
    }
    if (( ! ready )); then
        proof_stop_active KILL
        return 1
    fi
    wait "$PROOF_ACTIVE_PID"
    rc=$?
    kill -KILL -- "-$PROOF_ACTIVE_PGID" 2>/dev/null || true
    PROOF_ACTIVE_PID=''
    PROOF_ACTIVE_PGID=''
    return "$rc"
}

proof_fail() {
    echo "proof-demo: starter proof failed" >&2
    exit 3
}

proof_prepare_workspace() {
    local base base_dir work_dir old_umask rc seen='|'
    for base in /private/tmp /tmp; do
        base_dir="$(CDPATH= cd -- "$base" 2>/dev/null && pwd -P)" || continue
        case "$seen" in *"|$base_dir|"*) continue ;; esac
        seen="$seen$base_dir|"
        [[ -d "$base_dir" && -w "$base_dir" && ! -L "$base" ]] || continue
        old_umask="$(umask)"
        umask 077
        work_dir="$(/usr/bin/mktemp -d \
            "$base_dir/agy-worker-proof.XXXXXX" 2>/dev/null)"
        rc=$?
        umask "$old_umask"
        [[ "$rc" == 0 && -n "$work_dir" && -d "$work_dir" \
            && ! -L "$work_dir" && -O "$work_dir" ]] || continue
        /bin/chmod 700 "$work_dir" 2>/dev/null || {
            /bin/rmdir "$work_dir" 2>/dev/null || true
            continue
        }
        PROOF_WORK_DIR="$(CDPATH= cd -- "$work_dir" 2>/dev/null && pwd -P)" \
            || return 1
        return 0
    done
    return 1
}

proof_validate_fixtures() {
    python3 -B - "$1" "$2" <<'PY' >/dev/null 2>&1
import json
import sys
from pathlib import Path

common = {
    "status": "completed",
    "summary": "synthetic worker claim",
    "files_changed": [{"path": "proof.txt", "change": "modified"}],
    "commands_run": [],
    "tests_run": [],
    "risks": [],
    "open_questions": [],
    "confidence": 0.9,
    "requires_human": False,
}
expected = [common, common]

def strict_object(raw: bytes):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value
    return json.loads(raw.decode("ascii"), object_pairs_hook=unique)

for name, wanted in zip(sys.argv[1:], expected):
    raw = Path(name).read_bytes()
    canonical = (json.dumps(
        wanted, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ) + "\n").encode("ascii")
    if raw != canonical or strict_object(raw) != wanted:
        raise SystemExit(1)
PY
}

proof_init_repo() {
    local repo="$1"
    /bin/mkdir -p "$repo" || return 1
    proof_run git -C "$repo" init -q || return 1
    printf 'original synthetic value\n' > "$repo/proof.txt" || return 1
    proof_run git -C "$repo" add proof.txt || return 1
    proof_run git -C "$repo" commit -qm 'synthetic base' || return 1
}

proof_main() {
    local script_dir gate fixture_root honest_fixture mismatch_fixture
    local honest_repo mismatch_repo honest_base mismatch_base gate_rc verify_argv

    [[ $# == 0 ]] || exit 64
    script_dir="$(CDPATH= cd -- "${BASH_SOURCE[0]%/*}" 2>/dev/null && pwd -P)" \
        || proof_fail
    gate="$script_dir/qa-gate.sh"
    fixture_root="$script_dir/conformance/v1/envelopes"
    honest_fixture="$fixture_root/honest.json"
    mismatch_fixture="$fixture_root/honest.json"

    [[ -f "$gate" && -x "$gate" && ! -L "$gate" ]] || proof_fail
    [[ -d "$script_dir/conformance" && ! -L "$script_dir/conformance" \
        && -d "$script_dir/conformance/v1" && ! -L "$script_dir/conformance/v1" \
        && -d "$fixture_root" && ! -L "$fixture_root" \
        && -f "$honest_fixture" && ! -L "$honest_fixture" \
        && -f "$mismatch_fixture" && ! -L "$mismatch_fixture" ]] || proof_fail
    command -v git >/dev/null 2>&1 || proof_fail
    command -v python3 >/dev/null 2>&1 || proof_fail

    export GIT_CONFIG_GLOBAL=/dev/null
    export GIT_CONFIG_NOSYSTEM=1
    export GIT_AUTHOR_NAME='agy-worker proof demo'
    export GIT_AUTHOR_EMAIL='proof@example.invalid'
    export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
    export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

    proof_validate_fixtures "$honest_fixture" "$mismatch_fixture" || proof_fail
    proof_prepare_workspace || proof_fail
    trap proof_cleanup EXIT
    trap 'proof_on_signal HUP' HUP
    trap 'proof_on_signal INT' INT
    trap 'proof_on_signal TERM' TERM

    honest_repo="$PROOF_WORK_DIR/honest"
    mismatch_repo="$PROOF_WORK_DIR/mismatch"
    proof_init_repo "$honest_repo" || proof_fail
    proof_init_repo "$mismatch_repo" || proof_fail
    honest_base="$(git -C "$honest_repo" rev-parse HEAD 2>/dev/null)" || proof_fail
    mismatch_base="$(git -C "$mismatch_repo" rev-parse HEAD 2>/dev/null)" || proof_fail

    printf 'verified synthetic change\n' > "$honest_repo/proof.txt" || proof_fail
    verify_argv='["/usr/bin/python3","-I","-S","-B","-c","from pathlib import Path;import sys;sys.exit(0 if Path(\"proof.txt\").read_text()==\"verified synthetic change\\n\" else 1)"]'
    proof_run "$gate" --envelope "$honest_fixture" --repo "$honest_repo" \
        --base "$honest_base" --only proof.txt --expect-edits \
        --verify-argv "$verify_argv"
    gate_rc=$?
    [[ "$gate_rc" == 0 ]] || proof_fail

    printf 'verified synthetic change\n' > "$mismatch_repo/proof.txt" || proof_fail
    printf 'undeclared synthetic change\n' > "$mismatch_repo/hidden.txt" || proof_fail
    proof_run "$gate" --envelope "$mismatch_fixture" --repo "$mismatch_repo" \
        --base "$mismatch_base" --expect-edits --verify-argv "$verify_argv"
    gate_rc=$?
    [[ "$gate_rc" == 10 ]] || proof_fail

    trap - HUP INT TERM EXIT
    proof_cleanup || proof_fail
    printf '%s\n' \
        'honest: gate-passed (exit 0)' \
        'mismatch: rejected (exit 10)' \
        'starter proof only; no candidate accepted because no human review occurred'
}

proof_main "$@"
