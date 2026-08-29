#!/usr/bin/env bash
# Offline dispatcher and installer tests using a fake agy executable.
set -uo pipefail

# The suite owns every dispatcher input it exercises. Ambient caller values would
# otherwise conflict with explicit selector cases or silently change default
# timeout/mode behavior before the fake worker is reached.
unset AGY_WORKER_TIER AGY_WORKER_MODEL AGY_WORKER_EFFORT AGY_WORKER_MODE
unset AGY_WORKER_IDLE_TIMEOUT AGY_WORKER_HARD_TIMEOUT AGY_WORKER_MAX_RUNTIME
unset AGY_WORKER_NOTICE_INTERVAL AGY_WORKER_TIMEOUT AGY_WORKER_SCHEMA
unset AGY_WORKER_MAX_ATTEMPTS AGY_WORKER_JOB_ID AGY_WORKER_LOG_DIR

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
WORKER="$ROOT/agy-worker.sh"
RECOMMENDER="$ROOT/model-recommendation.sh"
SELECTOR="$ROOT/model-selection.sh"
TMP="$(mktemp -d -t agyworker-dispatch.XXXXXX)"
tmp_identity() {
    python3 - "$1" <<'PY'
import os
import stat
import sys

try:
    info = os.lstat(sys.argv[1])
except OSError as exc:
    print(f"unavailable errno={exc.errno}")
else:
    print(
        f"exists dev={info.st_dev} ino={info.st_ino} "
        f"mode={stat.S_IMODE(info.st_mode):o} uid={info.st_uid} gid={info.st_gid}"
    )
PY
}
if [[ "${KEEP_AGY_WORKER_TEST_TMP:-0}" == "1" ]]; then
    TMP_START_IDENTITY="$(tmp_identity "$TMP")"
    printf 'test tmp identity at startup: %s; %s\n' "$TMP" "$TMP_START_IDENTITY" >&2
    trap 'TMP_EXIT_IDENTITY="$(tmp_identity "$TMP")"; if [[ "$TMP_EXIT_IDENTITY" == "$TMP_START_IDENTITY" ]]; then printf "test tmp identity at exit: unchanged; %s\n" "$TMP_EXIT_IDENTITY" >&2; else printf "test tmp identity at exit: changed-or-unavailable; startup=%s exit=%s\n" "$TMP_START_IDENTITY" "$TMP_EXIT_IDENTITY" >&2; fi; printf "kept test tmp: %s\n" "$TMP" >&2' EXIT
else
    trap 'rm -rf "$TMP"' EXIT
fi
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
expect_exit() {
    local name="$1" want="$2" got="$3"
    if [[ "$got" == "$want" ]]; then ok "$name (exit $got)"; else bad "$name (exit $got, wanted $want)"; fi
}
expect_print_last() {
    local name="$1" argv_file="$2"
    if python3 - "$argv_file" <<'PY'
import sys
parts = [part for part in open(sys.argv[1], "rb").read().split(b"\0") if part]
raise SystemExit(0 if len(parts) >= 2 and parts[-2] == b"--print" else 1)
PY
    then ok "$name"; else bad "$name"; fi
}
expect_recommendation() {
    local name="$1" stage="$2" tier="$3" evidence="$4"
    local decision="$5" recommended="$6" direction="$7" steps="$8"
    local output="$TMP/recommendation-$pass.json" rc
    "$RECOMMENDER" --stage "$stage" --selected-tier "$tier" --evidence "$evidence" \
        > "$output" 2> "$output.err"
    rc=$?
    if [[ "$rc" == "0" ]] && python3 - "$output" "$stage" "$tier" "$evidence" \
        "$decision" "$recommended" "$direction" "$steps" <<'PY'
import json
import sys

path, stage, tier, evidence, decision, recommended, direction, steps = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    result = json.load(handle)
assert result["schema_version"] == 1
assert result["kind"] == "model-tier-recommendation"
assert result["stage"] == stage
assert result["selected_tier"] == tier
assert result["evidence"]["owner"] == "driver"
assert result["evidence"]["code"] == evidence
assert result["evidence"]["description"]
assert result["recommendation_only"] is True
assert result["applied"] is False
assert result["decision"] == decision
assert result["recommended_tier"] == (None if recommended == "null" else recommended)
assert result["rationale"]
assert result["cost_impact"]["direction"] == direction
assert result["cost_impact"]["relative_tier_steps"] == int(steps)
assert result["cost_impact"]["summary"]
PY
    then
        ok "$name"
    else
        bad "$name"
    fi
}
expect_recommendation_reject() {
    local name="$1"; shift
    local output="$TMP/recommendation-reject-$pass.out" rc
    "$RECOMMENDER" "$@" > "$output" 2> "$output.err"
    rc=$?
    if [[ "$rc" == "64" && ! -s "$output" ]]; then
        ok "$name (exit $rc)"
    else
        bad "$name (exit $rc, wanted 64 with empty stdout)"
    fi
}
expect_direct_recommendation() {
    local name="$1" stage="$2" model="$3" effort="$4" evidence="$5" resolved="$6"
    local output="$TMP/direct-recommendation-$pass.json" rc
    local args=(--stage "$stage" --selected-model "$model" --evidence "$evidence")
    [[ -z "$effort" ]] || args+=(--selected-effort "$effort")
    "$RECOMMENDER" "${args[@]}" > "$output" 2> "$output.err"
    rc=$?
    if [[ "$rc" == 0 ]] && python3 - "$output" "$model" "$effort" "$resolved" <<'PY'
import json
import sys

path, model, effort, resolved = sys.argv[1:]
value = json.load(open(path, encoding="utf-8"))
assert "selected_tier" not in value
assert value["user_model"] == model
assert value.get("user_effort", "") == effort
assert value["resolved_agy_model"] == resolved
assert len(value["matrix_sha256"]) == 64
assert value["matrix_agy_version"] == "1.1.22"
assert len(value["matrix_source_revision"]) == 40
assert value["recommendation_only"] is True
assert value["applied"] is False
assert value["decision"] == "no-escalation"
assert value["recommended_tier"] is None
assert value["cost_impact"]["direction"] == "none"
assert "caller-owned and unranked" in value["rationale"]
PY
    then ok "$name"; else bad "$name (exit $rc)"; fi
}
private_tree_is_private() {
    python3 - "$1" <<'PY'
import os
import stat
import sys

root = sys.argv[1]
paths = [root]
for current, directories, files in os.walk(root, followlinks=False):
    paths.extend(os.path.join(current, name) for name in directories + files)
for path in paths:
    mode = stat.S_IMODE(os.lstat(path).st_mode)
    if mode & 0o077:
        print(f"non-private artifact: {path} mode={mode:04o}", file=sys.stderr)
        raise SystemExit(1)
PY
}
mode_is() {
    python3 - "$1" "$2" <<'PY'
import os
import stat
import sys

actual = stat.S_IMODE(os.lstat(sys.argv[1]).st_mode)
expected = int(sys.argv[2], 8)
raise SystemExit(0 if actual == expected else 1)
PY
}
log_root_is_acceptable() {
    python3 - "$1" <<'PY'
import os
import stat
import sys

metadata = os.lstat(sys.argv[1])
mode = stat.S_IMODE(metadata.st_mode)
valid = (
    not stat.S_ISLNK(metadata.st_mode)
    and stat.S_ISDIR(metadata.st_mode)
    and metadata.st_uid == os.geteuid()
    and (mode & 0o022) == 0
)
raise SystemExit(0 if valid else 1)
PY
}

process_group_is_gone() {
    python3 -B - "$1" <<'PY'
import os
import sys

try:
    os.killpg(int(sys.argv[1]), 0)
except ProcessLookupError:
    raise SystemExit(0)
except (PermissionError, ValueError):
    pass
raise SystemExit(1)
PY
}

wait_probe_cleanup() {
    local child_pid="$1" probe_pgid="$2" wait_index
    [[ "$child_pid" != "$probe_pgid" ]] || return 1
    for (( wait_index=0; wait_index<100; wait_index++ )); do
        if process_group_is_gone "$probe_pgid"; then
            return 0
        fi
        sleep 0.02
    done
    process_group_is_gone "$probe_pgid"
}

mkdir -p "$TMP/bin" "$TMP/repo" "$TMP/logs"
git -C "$TMP/repo" init -q
git -C "$TMP/repo" -c user.name='agy-worker test' -c user.email='test@example.invalid' \
    commit --allow-empty -q -m initial
git -C "$TMP/repo" worktree add --detach -q "$TMP/project-worktree" HEAD
chmod 0755 "$TMP/logs"
LOGS_REAL="$(cd "$TMP/logs" && pwd -P)"

# Help is an informational CLI surface, never a result-envelope path.  It must
# succeed before log/state setup for the top level and every public subcommand;
# malformed use remains a normal 64 with no stdout JSON.
help_ok=1
for help_command in '' run start status wait result extend cancel resume restart continue finalize; do
    help_out="$TMP/help-${help_command:-top}.out"
    help_err="$TMP/help-${help_command:-top}.err"
    if [[ -n "$help_command" ]]; then
        "$WORKER" "$help_command" --help > "$help_out" 2> "$help_err"
    else
        "$WORKER" --help > "$help_out" 2> "$help_err"
    fi
    help_rc=$?
    if [[ "$help_rc" != 0 || -s "$help_out" ]] || ! grep -Fq 'usage: agy-worker.sh' "$help_err"; then
        help_ok=0
    fi
done
"$WORKER" status --not-a-real-option > "$TMP/help-invalid.out" 2> "$TMP/help-invalid.err"
invalid_help_rc=$?
private_unknown='--not-a-real-option=/private/unknown-argument'
"$WORKER" "$private_unknown" > "$TMP/help-private-run.out" 2> "$TMP/help-private-run.err"
private_run_rc=$?
"$WORKER" status "$private_unknown" > "$TMP/help-private-control.out" 2> "$TMP/help-private-control.err"
private_control_rc=$?
if [[ "$help_ok" == 1 && "$invalid_help_rc" == 64 && ! -s "$TMP/help-invalid.out" ]] \
    && grep -Fxq 'Stdout contracts: run emits a worker result envelope; start and lifecycle controls emit' "$TMP/help-top.err" \
    && grep -Fxq 'control JSON, with status/wait/resume/restart/continue/finalize accepting --format text;' "$TMP/help-top.err" \
    && grep -Fxq 'result emits its bound worker envelope unless --format text. A non-zero run exit means' "$TMP/help-top.err" \
    && [[ "$private_run_rc" == 64 && "$private_control_rc" == 64 ]] \
    && [[ ! -s "$TMP/help-private-run.out" && ! -s "$TMP/help-private-control.out" ]] \
    && grep -Fq 'agy-worker.sh: invalid usage; run --help for usage' "$TMP/help-private-run.err" \
    && grep -Fq 'agy-worker.sh: invalid usage; run --help for usage' "$TMP/help-private-control.err" \
    && ! grep -Fq -- "$private_unknown" "$TMP/help-private-run.err" \
    && ! grep -Fq -- "$private_unknown" "$TMP/help-private-control.err"; then
    ok "top-level help distinguishes worker and lifecycle stdout; invalid usage is sanitized"
else
    bad "help stdout contract or sanitized invalid usage"
fi
cat > "$TMP/bin/agy" <<'FAKE'
#!/usr/bin/env bash
set -u
FAKE_EXECUTABLE_CONTENT_SENTINEL=round-two-binding-original
FAKE_CALLS_FILE="${FAKE_CALLS_FILE:-/dev/null}"
FAKE_WORKER_CALLS_FILE="${FAKE_WORKER_CALLS_FILE:-/dev/null}"
if [[ "${1:-}" == "--version" && $# -eq 1 ]]; then
    printf 'version\n' >> "$FAKE_CALLS_FILE"
    case "${FAKE_VERSION_MODE:-ready}" in
        ready) printf '1.1.22\n' ;;
        quota113) printf '1.1.13\n' ;;
        prefixed) printf 'agy 1.1.22\n' ;;
        drift) printf '1.1.11\n' ;;
        drift117) printf '1.1.17\n' ;;
        drift999) printf '9.9.9\n' ;;
        empty) : ;;
        malformed) printf 'version 1.1.22\n' ;;
        oversize) i=0; while [[ $i -lt 140 ]]; do printf x; i=$((i+1)); done; printf '\n' ;;
        stream) while :; do printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'; done ;;
        child-stream)
            cleanup_probe_child() {
                trap - TERM
                kill -KILL "$child_pid" 2>/dev/null || true
                wait "$child_pid" 2>/dev/null || true
                exit 143
            }
            trap cleanup_probe_child TERM
            (
                trap '' TERM
                while [[ ! -e "${FAKE_PROBE_RELEASE_FILE:?}" ]]; do
                    sleep 0.01
                done
                while :; do
                    printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
                done
            ) &
            child_pid=$!
            [[ -z "${FAKE_CHILD_PID_FILE:-}" ]] || printf '%s\n' "$child_pid" > "$FAKE_CHILD_PID_FILE"
            [[ -z "${FAKE_PROBE_PGID_FILE:-}" ]] || printf '%s\n' "$$" > "$FAKE_PROBE_PGID_FILE"
            [[ -z "${FAKE_PROBE_PARENT_PID_FILE:-}" ]] || printf '%s\n' "$PPID" > "$FAKE_PROBE_PARENT_PID_FILE"
            [[ -z "${FAKE_PROBE_READY_FILE:-}" ]] || : > "$FAKE_PROBE_READY_FILE"
            wait "$child_pid"
            ;;
        signal-wait)
            cleanup_probe_child() {
                trap - TERM
                kill -KILL "$child_pid" 2>/dev/null || true
                wait "$child_pid" 2>/dev/null || true
                exit 143
            }
            trap cleanup_probe_child TERM
            (
                trap '' HUP INT TERM
                while :; do sleep 1; done
            ) &
            child_pid=$!
            [[ -z "${FAKE_CHILD_PID_FILE:-}" ]] || printf '%s\n' "$child_pid" > "$FAKE_CHILD_PID_FILE"
            [[ -z "${FAKE_PROBE_PGID_FILE:-}" ]] || printf '%s\n' "$$" > "$FAKE_PROBE_PGID_FILE"
            [[ -z "${FAKE_PROBE_PARENT_PID_FILE:-}" ]] || printf '%s\n' "$PPID" > "$FAKE_PROBE_PARENT_PID_FILE"
            [[ -z "${FAKE_PROBE_READY_FILE:-}" ]] || : > "$FAKE_PROBE_READY_FILE"
            wait "$child_pid"
            ;;
        fail) exit 23 ;;
        hang) sleep 10 ;;
        *) exit 24 ;;
    esac
    exit 0
fi
if [[ "${1:-}" == "--help" && $# -eq 1 ]]; then
    printf 'help\n' >> "$FAKE_CALLS_FILE"
    case "${FAKE_HELP_MODE:-ready}" in
        ready)
            cat >&2 <<'HELP'
Usage of agy:
  --add-dir                       Add a directory to the workspace
  --conversation                  Resume a previous conversation by ID
  --disable-slash-commands        Disable slash command expansion
  --json-schema                   Optional JSON schema path
  --mode                          Set execution mode (accept-edits, plan)
  --model                         Select a model
  --output-format                 Output format (text, json, stream-json)
  --print                         Run a prompt
  --print-timeout                 Timeout for print mode
  --sandbox                       Run sandboxed
HELP
            ;;
        locale-sensitive)
            locale_label="inherited-${LC_ALL:-unset}"
            [[ "${LC_ALL:-}" == "C" ]] && locale_label="canonical-C"
            cat >&2 <<HELP
Usage of agy:
  --add-dir                       Add a directory to the workspace ($locale_label)
  --conversation                  Resume a previous conversation by ID
  --disable-slash-commands        Disable slash command expansion
  --json-schema                   Optional JSON schema path
  --mode                          Set execution mode (accept-edits, plan)
  --model                         Select a model
  --output-format                 Output format (text, json, stream-json)
  --print                         Run a prompt
  --print-timeout                 Timeout for print mode
  --sandbox                       Run sandboxed
HELP
            ;;
        missing) printf '%s\n' 'Usage of agy:' >&2 ;;
        duplicate) printf '%s\n' '  --model                         Duplicate model' >&2; cat >&2 <<'HELP'
  --add-dir                       Add a directory to the workspace
  --conversation                  Resume a previous conversation by ID
  --disable-slash-commands        Disable slash command expansion
  --json-schema                   Optional JSON schema path
  --mode                          Set execution mode (accept-edits, plan)
  --model                         Select a model
  --output-format                 Output format (text, json, stream-json)
  --print                         Run a prompt
  --print-timeout                 Timeout for print mode
  --sandbox                       Run sandboxed
HELP
            ;;
        malformed) printf '%s\n' ' --model                          Select a model' >&2 ;;
        semantic) cat >&2 <<'HELP'
Usage of agy:
  --add-dir                       Add a directory to the workspace
  --conversation                  Resume a previous conversation by ID
  --disable-slash-commands        Disable slash command expansion
  --json-schema                   Optional JSON schema path
  --mode                          Set execution mode (accept-edits, plan)
  --model                         Select a model (not available in this build)
  --output-format                 Output format (text, json, stream-json)
  --print                         Run a prompt
  --print-timeout                 Timeout for print mode
  --sandbox                       Run sandboxed
HELP
            ;;
        utf8) printf '\377\n' >&2 ;;
        nul) printf 'x\000\n' >&2 ;;
        oversize) i=0; while [[ $i -lt 70000 ]]; do printf x >&2; i=$((i+1)); done ;;
        fail) exit 23 ;;
        hang) sleep 10 ;;
        *) exit 24 ;;
    esac
    if [[ -n "${FAKE_MUTATE_EXECUTABLE:-}" ]]; then
        printf '#!/usr/bin/env bash\nexit 97\n' > "$FAKE_MUTATE_EXECUTABLE"
        chmod 0755 "$FAKE_MUTATE_EXECUTABLE"
    fi
    if [[ -n "${FAKE_MUTATE_EXECUTABLE_SAME_LENGTH:-}" ]]; then
        python3 -B - "$FAKE_MUTATE_EXECUTABLE_SAME_LENGTH" <<'PY'
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
before = path.stat()
raw = path.read_bytes()
old = b"FAKE_EXECUTABLE_CONTENT_SENTINEL=round-two-binding-" + b"original"
new = b"FAKE_EXECUTABLE_CONTENT_SENTINEL=round-two-binding-" + b"replaced"
if len(old) != len(new) or raw.count(old) != 1:
    raise SystemExit(97)
path.write_bytes(raw.replace(old, new))
os.chmod(path, 0o755)
os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
marker = os.environ.get("FAKE_MUTATION_MARKER")
if marker:
    Path(marker).touch()
PY
    [[ $? == 0 ]] || exit 97
    fi
    if [[ -n "${FAKE_REPLACE_EXECUTABLE_SYMLINK:-}" ]]; then
        replacement="${FAKE_REPLACE_EXECUTABLE_SYMLINK}.round-two-replacement"
        rm -f "$replacement"
        ln -s "${FAKE_EXECUTABLE_SYMLINK_TARGET:?}" "$replacement"
        mv -f "$replacement" "$FAKE_REPLACE_EXECUTABLE_SYMLINK"
    fi
    if [[ -n "${FAKE_MUTATE_EXECUTABLE_MODE:-}" ]]; then
        chmod 0775 "$FAKE_MUTATE_EXECUTABLE_MODE"
    fi
    if [[ -n "${FAKE_MUTATE_EXECUTABLE_PARENT:-}" ]]; then
        chmod 0775 "$FAKE_MUTATE_EXECUTABLE_PARENT"
    fi
    exit 0
fi
printf 'worker\n' >> "$FAKE_CALLS_FILE"
printf 'worker\n' >> "$FAKE_WORKER_CALLS_FILE"
: "${FAKE_MODEL_FILE:?}"
: "${FAKE_PROMPT_FILE:?}"
: "${FAKE_DIRS_FILE:?}"
: "${FAKE_ARGV_FILE:?}"
: "${FAKE_STAGE_RESULT_FILE:?}"
: > "$FAKE_MODEL_FILE"
: > "$FAKE_PROMPT_FILE"
: > "$FAKE_DIRS_FILE"
: > "$FAKE_STAGE_RESULT_FILE"
printf '%s\0' "$@" > "$FAKE_ARGV_FILE"
stage_dir=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) printf '%s' "$2" > "$FAKE_MODEL_FILE"; shift 2 ;;
        --add-dir)
            printf '%s\n' "$2" >> "$FAKE_DIRS_FILE"
            case "$2" in */staged) stage_dir="$2" ;; esac
            shift 2 ;;
        --print) printf '%s' "$2" > "$FAKE_PROMPT_FILE"; shift 2 ;;
        *) shift ;;
    esac
done
if [[ "${FAKE_TRY_STAGE_WRITE:-0}" == "1" && -n "$stage_dir" ]]; then
    if printf 'tampered' 2>/dev/null > "$stage_dir/full-prompt.txt"; then
        printf 'wrote' > "$FAKE_STAGE_RESULT_FILE"
    else
        printf 'blocked' > "$FAKE_STAGE_RESULT_FILE"
    fi
fi
if [[ -n "${FAKE_CALLED_FILE:-}" ]]; then
    : > "$FAKE_CALLED_FILE"
fi
if [[ -n "${FAKE_MUTATE_MATRIX:-}" ]]; then
    printf '{"mutated":true}\n' > "$FAKE_MUTATE_MATRIX"
fi
if [[ -n "${FAKE_MUTATE_PROJECT_MARKER:-}" ]]; then
    printf 'gitdir: tampered\n' > "$FAKE_MUTATE_PROJECT_MARKER"
fi
if [[ -n "${FAKE_SPARSE_PROJECT_MARKER:-}" ]]; then
    python3 -B - "$FAKE_SPARSE_PROJECT_MARKER" <<'PY'
import os
import sys
os.truncate(sys.argv[1], 1 << 20)
PY
fi
dispatch_count=1
if [[ -n "${FAKE_DISPATCH_COUNT_FILE:-}" ]]; then
    if [[ -f "$FAKE_DISPATCH_COUNT_FILE" ]]; then
        IFS= read -r dispatch_count < "$FAKE_DISPATCH_COUNT_FILE"
        dispatch_count=$((dispatch_count+1))
    fi
    printf '%s\n' "$dispatch_count" > "$FAKE_DISPATCH_COUNT_FILE"
fi
if [[ "${FAKE_FAIL_FIRST:-0}" == "1" && "$dispatch_count" == "1" ]]; then
    exit 23
fi
if [[ -n "${FAKE_SIGNAL_PARENT:-}" ]]; then
    kill -s "$FAKE_SIGNAL_PARENT" "$PPID"
    exit "${FAKE_EXIT_CODE:-23}"
fi
case "${FAKE_DISPATCH_MODE:-result}" in
    idle)
        sleep 10
        exit 0
        ;;
    malformed-heartbeat)
        while :; do
            printf '{not-valid-json}\n'
            sleep 0.10
        done
        ;;
    oversized-heartbeat)
        python3 -c 'import json; print(json.dumps({"event":"step_update", "blob":"x" * 1100000}))'
        sleep 10
        exit 0
        ;;
    empty-success)
        exit 0
        ;;
    heartbeat-forever)
        printf '{"event":"init","conversation_id":"fake-conversation-01","init":{}}\n'
        if [[ -n "${FAKE_HEARTBEAT_BARRIER_READY:-}" \
                && -n "${FAKE_HEARTBEAT_BARRIER_RELEASE:-}" ]]; then
            : > "$FAKE_HEARTBEAT_BARRIER_READY"
            while [[ ! -e "$FAKE_HEARTBEAT_BARRIER_RELEASE" ]]; do
                sleep 0.01
            done
        fi
        if [[ -n "${FAKE_SIDE_EFFECT_FILE:-}" ]]; then
            ( sleep 3; : > "$FAKE_SIDE_EFFECT_FILE" ) &
        fi
        while :; do
            printf '{"event":"step_update","step_update":{}}\n'
            sleep "${FAKE_HEARTBEAT_DELAY:-0.10}"
        done
        ;;
    heartbeat-success)
        printf '{"event":"init","conversation_id":"fake-conversation-01","init":{}}\n'
        if [[ -n "${FAKE_HEARTBEAT_BARRIER_READY:-}" \
                && -n "${FAKE_HEARTBEAT_BARRIER_RELEASE:-}" ]]; then
            : > "$FAKE_HEARTBEAT_BARRIER_READY"
            while [[ ! -e "$FAKE_HEARTBEAT_BARRIER_RELEASE" ]]; do
                sleep 0.01
            done
        fi
        heartbeat_count="${FAKE_HEARTBEAT_COUNT:-8}"
        heartbeat_delay="${FAKE_HEARTBEAT_DELAY:-0.10}"
        heartbeat_index=0
        while [[ "$heartbeat_index" -lt "$heartbeat_count" ]]; do
            printf '{"event":"step_update","step_update":{}}\n'
            if [[ "$heartbeat_index" == 0 \
                    && -n "${FAKE_HEARTBEAT_AFTER_FIRST_READY:-}" \
                    && -n "${FAKE_HEARTBEAT_AFTER_FIRST_RELEASE:-}" ]]; then
                : > "$FAKE_HEARTBEAT_AFTER_FIRST_READY"
                while [[ ! -e "$FAKE_HEARTBEAT_AFTER_FIRST_RELEASE" ]]; do
                    sleep 0.01
                done
            fi
            sleep "$heartbeat_delay"
            heartbeat_index=$((heartbeat_index+1))
        done
        ;;
    conversation-fail)
        printf '{"event":"init","conversation_id":"fake-conversation-01","init":{}}\n'
        exit 23
        ;;
    quota-error)
        quota_text="${FAKE_QUOTA_ERROR:-rpc error: Individual quota reached. Contact your administrator to enable overages. Resets in 4h51m54s.}"
        printf '{"event":"init","conversation_id":"fake-conversation-01","init":{}}\n'
        python3 -B - "$quota_text" <<'PY'
import json
import sys

print(json.dumps({
    "event": "result",
    "result": {
        "conversation_id": "fake-conversation-01",
        "status": "ERROR",
        "response": "",
        "error": sys.argv[1],
        "duration_seconds": 1.0,
        "num_turns": 3,
        "json_schema": {},
        "usage": {},
    },
}, separators=(",", ":")))
PY
        exit "${FAKE_EXIT_CODE:-23}"
        ;;
esac
if [[ "${FAKE_EXIT_CODE:-0}" != "0" ]]; then
    [[ -z "${FAKE_ERROR_LINE:-}" ]] || printf '%s\n' "$FAKE_ERROR_LINE" >&2
    exit "$FAKE_EXIT_CODE"
fi
[[ -z "${FAKE_WARNING_LINE:-}" ]] || printf '%s\n' "$FAKE_WARNING_LINE" >&2
status="${FAKE_AGY_STATUS:-SUCCESS}"
if [[ "${FAKE_DISPATCH_MODE:-result}" == "result" ]]; then
    printf '{"event":"init","conversation_id":"fake-conversation-01","init":{}}\n'
fi
if [[ "${FAKE_BAD_ENVELOPE:-0}" == "1" ]]; then
    envelope='{"status":"completed","summary":"done","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":9,"requires_human":false}'
elif [[ "${FAKE_WORKER_VERIFIED:-0}" == "1" ]]; then
    envelope='{"status":"completed","summary":"Verified private-worker-prose-sentinel","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
elif [[ "${FAKE_UTF8_SUMMARY:-0}" == "1" ]]; then
    envelope='{"status":"completed","summary":"café 😀","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
else
    envelope='{"status":"completed","summary":"done","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
fi
printf '{"event":"result","result":{"status":"%s","duration_seconds":0,"num_turns":1,"usage":{},"structured_output":%s}}\n' "$status" "$envelope"
FAKE
chmod +x "$TMP/bin/agy"

run_worker() {
    local job="$1" workdir; shift
    workdir="${AGY_TEST_WORKDIR:-$TMP/repo}"
    PATH="$TMP/bin:$PATH" \
    AGY_WORKER_MODE="${AGY_WORKER_MODE:-accept-edits}" \
    AGY_WORKER_LOG_DIR="${AGY_TEST_LOG_DIR:-$TMP/logs}" \
    AGY_WORKER_JOB_ID="$job" \
    FAKE_MODEL_FILE="$TMP/$job.model" \
    FAKE_PROMPT_FILE="$TMP/$job.prompt" \
    FAKE_DIRS_FILE="$TMP/$job.dirs" \
    FAKE_ARGV_FILE="$TMP/$job.argv" \
    FAKE_STAGE_RESULT_FILE="$TMP/$job.stage-result" \
    FAKE_CALLS_FILE="$TMP/$job.calls" \
    FAKE_WORKER_CALLS_FILE="$TMP/$job.worker-calls" \
    FAKE_VERSION_MODE="${FAKE_VERSION_MODE:-ready}" \
    FAKE_HELP_MODE="${FAKE_HELP_MODE:-ready}" \
    FAKE_MUTATE_EXECUTABLE="${FAKE_MUTATE_EXECUTABLE:-}" \
    FAKE_MUTATE_EXECUTABLE_SAME_LENGTH="${FAKE_MUTATE_EXECUTABLE_SAME_LENGTH:-}" \
    FAKE_REPLACE_EXECUTABLE_SYMLINK="${FAKE_REPLACE_EXECUTABLE_SYMLINK:-}" \
    FAKE_EXECUTABLE_SYMLINK_TARGET="${FAKE_EXECUTABLE_SYMLINK_TARGET:-}" \
    FAKE_MUTATE_EXECUTABLE_MODE="${FAKE_MUTATE_EXECUTABLE_MODE:-}" \
    FAKE_MUTATE_EXECUTABLE_PARENT="${FAKE_MUTATE_EXECUTABLE_PARENT:-}" \
    FAKE_MUTATION_MARKER="${FAKE_MUTATION_MARKER:-}" \
    FAKE_CHILD_PID_FILE="${FAKE_CHILD_PID_FILE:-}" \
    FAKE_PROBE_PGID_FILE="${FAKE_PROBE_PGID_FILE:-}" \
    FAKE_PROBE_PARENT_PID_FILE="${FAKE_PROBE_PARENT_PID_FILE:-}" \
    FAKE_PROBE_READY_FILE="${FAKE_PROBE_READY_FILE:-}" \
    FAKE_PROBE_RELEASE_FILE="${FAKE_PROBE_RELEASE_FILE:-}" \
    FAKE_MUTATE_MATRIX="${FAKE_MUTATE_MATRIX:-}" \
    FAKE_MUTATE_PROJECT_MARKER="${FAKE_MUTATE_PROJECT_MARKER:-}" \
    FAKE_SPARSE_PROJECT_MARKER="${FAKE_SPARSE_PROJECT_MARKER:-}" \
    FAKE_DISPATCH_COUNT_FILE="${FAKE_DISPATCH_COUNT_FILE:-}" \
    FAKE_FAIL_FIRST="${FAKE_FAIL_FIRST:-0}" \
    FAKE_TRY_STAGE_WRITE="${FAKE_TRY_STAGE_WRITE:-0}" \
    FAKE_DISPATCH_MODE="${FAKE_DISPATCH_MODE:-result}" \
    FAKE_HEARTBEAT_COUNT="${FAKE_HEARTBEAT_COUNT:-8}" \
    FAKE_HEARTBEAT_DELAY="${FAKE_HEARTBEAT_DELAY:-0.10}" \
    FAKE_SIDE_EFFECT_FILE="${FAKE_SIDE_EFFECT_FILE:-}" \
    FAKE_ERROR_LINE="${FAKE_ERROR_LINE:-}" \
    FAKE_WARNING_LINE="${FAKE_WARNING_LINE:-}" \
    FAKE_QUOTA_ERROR="${FAKE_QUOTA_ERROR:-}" \
    FAKE_WORKER_VERIFIED="${FAKE_WORKER_VERIFIED:-0}" \
    FAKE_UTF8_SUMMARY="${FAKE_UTF8_SUMMARY:-0}" \
    FAKE_CALLED_FILE="$TMP/$job.called" \
    FAKE_SIGNAL_PARENT="${FAKE_SIGNAL_PARENT:-}" \
    FAKE_EXIT_CODE="${FAKE_EXIT_CODE:-0}" \
    "${AGY_TEST_WORKER:-$WORKER}" --workdir "$workdir" "$@"
}

echo "agy-worker.sh offline test suite"
echo

"$WORKER" --help > "$TMP/help.out" 2> "$TMP/help.err"
help_rc=$?
if [[ "$help_rc" == 0 && ! -s "$TMP/help.out" ]] \
        && grep -Fqx 'Workflow cycle limits: explore/task 1..2 (default 2); project 1..5 (default 5).' \
            "$TMP/help.err" \
        && grep -Fqx -- '--max-cycles requires an explicit workflow; legacy raw mode remains one attempt.' \
            "$TMP/help.err"; then
    ok "help states workflow-specific cycle limits"
else
    bad "help workflow-specific cycle limits"
fi

printf 'small task\n' | run_worker tier --tier cheap > "$TMP/tier.out" 2> "$TMP/tier.err"
rc=$?
if [[ "$rc" != "0" ]]; then
    # Keep the first synthetic dispatch actionable in remote CI.  This fixture
    # contains no provider prose or credentials; later cases intentionally keep
    # their captured diagnostics private.
    tail -n 5 "$TMP/tier.err" >&2
fi
expect_exit "--tier cheap produces an envelope" 0 "$rc"
if [[ "$(<"$TMP/tier.model")" == "gemini-3.6-flash-low" ]]; then
    ok "--tier is resolved after CLI parsing"
else
    bad "--tier is resolved after CLI parsing"
fi
expect_print_last "small prompt keeps --print and its value last" "$TMP/tier.argv"

printf 'raw custom model\n' | run_worker raw-flash-high \
    --tier gemini-3.6-flash-high > "$TMP/raw-flash-high.out" 2>/dev/null
rc=$?
if [[ "$rc" == "0" && "$(<"$TMP/raw-flash-high.model")" == "gemini-3.6-flash-high" ]] \
        && python3 - "$TMP/raw-flash-high.argv" "$TMP/raw-flash-high.calls" <<'PY'
import sys
parts = [part for part in open(sys.argv[1], "rb").read().split(b"\0") if part]
calls = open(sys.argv[2], encoding="ascii").read().splitlines()
raise SystemExit(0 if b"--effort" not in parts and calls == ["worker"] else 1)
PY
then
    ok "raw flash-high stays exact pass-through with no effort argument"
else
    bad "raw flash-high stays exact pass-through with no effort argument"
fi

assert_tier_selection() {
    local name="$1" job="$2" tier_value="$3" tier_source="$4" expected_model="$5"
    if python3 - "$TMP/$job.argv" "$TMP/logs/$job/selection.json" \
        "$TMP/$job.calls" "$tier_value" "$tier_source" "$expected_model" <<'PY'
import json
import sys

argv_path, selection_path, calls_path, tier, source, expected = sys.argv[1:]
parts = [part for part in open(argv_path, "rb").read().split(b"\0") if part]
record = json.load(open(selection_path, encoding="utf-8"))
assert open(calls_path, encoding="ascii").read().splitlines() == ["worker"]
assert record["selection_mode"] == "tier"
assert record["selected_tier"] == tier
assert record["selected_tier_source"] == source
assert record["resolved_agy_model"] == (expected or None)
if expected:
    assert parts.count(b"--model") == 1
    assert parts[parts.index(b"--model") + 1].decode() == expected
else:
    assert b"--model" not in parts
PY
    then ok "$name"; else bad "$name"; fi
}

legacy_index=0
while IFS='|' read -r legacy_tier legacy_model; do
    legacy_index=$((legacy_index+1))
    legacy_job="legacy-tier-$legacy_index"
    printf 'legacy tier %s\n' "$legacy_tier" | run_worker "$legacy_job" --tier "$legacy_tier" \
        > "$TMP/$legacy_job.out" 2> "$TMP/$legacy_job.err"
    rc=$?
    if [[ "$rc" == 0 ]]; then
        assert_tier_selection "legacy tier $legacy_tier preserves its exact mapping" \
            "$legacy_job" "$legacy_tier" cli "$legacy_model"
    else
        bad "legacy tier $legacy_tier preserves its exact mapping (exit $rc)"
    fi
done <<'EOF'
bulk|gemini-3.6-flash-medium
cheap|gemini-3.6-flash-low
hard|gemini-3.1-pro-high
hardest|claude-opus-4-6-thinking
default|
vendor/model-v1|vendor/model-v1
EOF

printf 'environment tier\n' | AGY_WORKER_TIER=hard run_worker legacy-tier-env \
    > "$TMP/legacy-tier-env.out" 2> "$TMP/legacy-tier-env.err"
rc=$?
if [[ "$rc" == 0 ]]; then
    assert_tier_selection "legacy environment tier records environment provenance" \
        legacy-tier-env hard environment gemini-3.1-pro-high
else
    bad "legacy environment tier records environment provenance (exit $rc)"
fi

printf 'implicit default tier\n' | run_worker legacy-tier-implicit \
    > "$TMP/legacy-tier-implicit.out" 2> "$TMP/legacy-tier-implicit.err"
rc=$?
if [[ "$rc" == 0 ]]; then
    assert_tier_selection "no selector uses the agy-owned default without a model" \
        legacy-tier-implicit default implicit-default ''
else
    bad "no selector uses the agy-owned default without a model (exit $rc)"
fi

printf 'literal model under malformed version output\n' | FAKE_VERSION_MODE=malformed \
    run_worker literal-version-independent --literal-model future-model-1.2 \
    > "$TMP/literal-version-independent.out" 2> "$TMP/literal-version-independent.err"
rc=$?
if [[ "$rc" == 0 && "$(<"$TMP/literal-version-independent.model")" == "future-model-1.2" ]] \
        && python3 - "$TMP/literal-version-independent.argv" \
            "$TMP/logs/literal-version-independent/selection.json" \
            "$TMP/literal-version-independent.calls" <<'PY'
import json
import sys

argv = [item for item in open(sys.argv[1], "rb").read().split(b"\0") if item]
record = json.load(open(sys.argv[2], encoding="utf-8"))
calls = open(sys.argv[3], encoding="ascii").read().splitlines()
assert calls == ["version", "worker"]
assert argv.count(b"--model") == 1
assert argv[argv.index(b"--model") + 1] == b"future-model-1.2"
assert b"--effort" not in argv and b"--thinking-level" not in argv
assert record == {
    "schema_version": 1,
    "kind": "agy-worker-selection",
    "selection_mode": "literal-model",
    "user_model": "future-model-1.2",
    "user_model_source": "cli",
    "resolved_agy_model": "future-model-1.2",
    "compatibility_status": "unreconciled-pass-through",
}
PY
then
    ok "literal model routing stays version-independent while version observation remains non-gating"
else
    bad "literal model version-independent contract (exit $rc)"
fi

assert_direct_result() {
    local name="$1" job="$2" expected="$3" user_model="$4" user_effort="$5"
    local model_source="${6:-cli}" effort_source="${7:-}"
    local expected_schema="${8:-2}"
    if [[ "$(<"$TMP/$job.model")" == "$expected" ]] \
            && [[ "$(wc -l < "$TMP/$job.worker-calls" | tr -d ' ')" == "1" ]] \
            && python3 - "$TMP/$job.argv" "$TMP/logs/$job/selection.json" \
                "$TMP/$job.calls" "$expected" "$user_model" "$user_effort" "$model_source" "$effort_source" "$expected_schema" <<'PY'
import json
import os
import stat
import sys

argv_path, selection_path, calls_path, expected, user_model, user_effort, model_source, effort_source, expected_schema = sys.argv[1:]
parts = [part for part in open(argv_path, "rb").read().split(b"\0") if part]
assert open(calls_path, encoding="ascii").read().splitlines() == ["version", "help", "version", "help", "version", "help", "worker"]
assert parts.count(b"--model") == 1
index = parts.index(b"--model")
assert parts[index + 1].decode() == expected
assert b"--effort" not in parts
assert b"--thinking-level" not in parts
record = json.load(open(selection_path, encoding="utf-8"))
assert record["schema_version"] == int(expected_schema)
assert record["kind"] == "agy-worker-selection"
assert record["user_model"] == user_model
assert record.get("user_effort", "") == user_effort
assert record["user_model_source"] == model_source
assert record.get("user_effort_source", "") == effort_source
assert record["resolved_agy_model"] == expected
assert record["installed_agy_version"] == "1.1.22"
assert record["matrix_agy_version"] == "1.1.22"
assert record["version_relation"] == "match"
assert record["critical_interface_probe_version"] == 1
assert record["critical_interface_status"] == "compatible"
if record["schema_version"] == 3:
    assert record["compatibility_status"] == "critical-interface-compatible-version-drift"
    assert record["version_relation"] == "drift"
    assert record["compatibility_disposition"] == "proceed"
    assert record["approved_help_sha256"] == record["help_sha256"]
    assert len(record["compatibility_decision_sha256"]) == 64
    assert "reviewed_help_sha256" not in record
else:
    assert record["schema_version"] == 2
    assert record["compatibility_status"] == "reviewed-version-match"
    assert "reviewed_help_sha256" not in record
    assert not ({"compatibility_disposition", "approved_help_sha256", "compatibility_decision_sha256"} & set(record))
assert record["model_availability"] == "not_assessed"
assert len(record["critical_capabilities_sha256"]) == 64
assert len(record["help_sha256"]) == 64
binding = record["probed_executable"]
assert set(binding) == {"path_sha256", "target_lstat", "content_sha256", "symlink_chain", "components"}
assert len(binding["path_sha256"]) == 64
assert len(binding["content_sha256"]) == 64
assert binding["target_lstat"]["inode"] > 0
assert binding["target_lstat"]["ctime_ns"] > 0
assert all("path" not in key or key == "path_sha256" for item in [binding, *binding["symlink_chain"], *binding["components"]] for key in item)
assert len(record["matrix_sha256"]) == 64
assert len(record["matrix_source_revision"]) == 40
assert stat.S_IMODE(os.stat(selection_path).st_mode) & 0o077 == 0
PY
    then ok "$name"; else bad "$name"; fi
}

# An exact matrix-version match proceeds after the bounded structural probe; it
# does not need a raw-help approval.  The standalone selector remains a public
# surface, so its private executable path must never appear in its JSON stdout.
printf 'exact-version structural help may dispatch\n' | \
    AGY_TEST_WORKER="$WORKER" run_worker exact-version-unseen-help --model gemini-3.6-flash --effort high \
    > "$TMP/exact-version-unseen-help.out" 2> "$TMP/exact-version-unseen-help.err"
rc=$?
if [[ "$rc" == 0 ]]; then
    assert_direct_result "exact-version structural probe proceeds without approval" \
        exact-version-unseen-help gemini-3.6-flash-high gemini-3.6-flash high cli cli 2
else
    bad "exact-version structural probe boundary (exit $rc)"
fi
# An exact-version help line may explicitly negate --model.  It must be an
# option-local structural match; the controller does not infer availability
# from provider prose.
printf 'help prose requires Codex, not controller, semantic interpretation\n' | \
    AGY_TEST_WORKER="$WORKER" FAKE_VERSION_MODE=ready FAKE_HELP_MODE=semantic \
    run_worker help-option-negation --model gemini-3.6-flash --effort high \
    > "$TMP/help-option-negation.out" 2> "$TMP/help-option-negation.err"
rc=$?
if [[ "$rc" == 0 ]]; then
    assert_direct_result "exact-version option prose does not override structural compatibility" \
        help-option-negation gemini-3.6-flash-high gemini-3.6-flash high cli cli 2
else
    bad "exact-version option prose structural boundary (exit $rc)"
fi
LOCALE_HELP_SHA="$(LC_ALL=C FAKE_HELP_MODE=locale-sensitive PATH="$TMP/bin:$PATH" \
    "$TMP/bin/agy" --help 2>&1 | /usr/bin/python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
LC_ALL=POSIX FAKE_VERSION_MODE=drift117 FAKE_HELP_MODE=locale-sensitive PATH="$TMP/bin:$PATH" \
    "$SELECTOR" --model gemini-3.6-flash --effort high \
    --compatibility-disposition proceed --approve-help-sha "$LOCALE_HELP_SHA" \
    > "$TMP/direct-selector-public.json" 2> "$TMP/direct-selector-public.err"
rc=$?
if [[ "$rc" == 0 ]] && python3 - "$TMP/direct-selector-public.json" "$TMP/bin/agy" \
        "$LOCALE_HELP_SHA" <<'PY'
import json, sys
payload = open(sys.argv[1], "rb").read()
record = json.loads(payload)
assert sys.argv[2].encode() not in payload
assert record["schema_version"] == 3
assert record["installed_agy_version"] == "1.1.17"
assert record["version_relation"] == "drift"
assert record["help_sha256"] == record["approved_help_sha256"] == sys.argv[3]
assert "path" not in record["probed_executable"]
assert set(record["probed_executable"]) == {"path_sha256", "target_lstat", "content_sha256", "symlink_chain", "components"}
PY
then
    ok "direct selector pins the documented C-locale help digest and exposes no executable path"
else
    bad "direct selector locale/hash parity or executable binding"
fi

if PYTHONDONTWRITEBYTECODE=1 python3 -B - "$ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "skills" / "agy-worker" / "runtime" / "scripts"))
import model_selection

original_platform = model_selection.sys.platform
try:
    model_selection.sys.platform = "darwin"
    assert model_selection._path_sha256("/var/run/agy") == model_selection._path_sha256("/private/var/run/agy")
    assert model_selection._path_sha256("/variety/run/agy") != model_selection._path_sha256("/private/variety/run/agy")
finally:
    model_selection.sys.platform = original_platform
PY
then
    ok "only the documented macOS /var alias has a normalized executable path digest"
else
    bad "macOS executable path alias normalization boundary"
fi

pair_index=0
direct_pairs=(
    'gemini-3.7-flash|low|gemini-3.7-flash-low'
    'gemini-3.7-flash|medium|gemini-3.7-flash-medium'
    'gemini-3.7-flash|high|gemini-3.7-flash-high'
    'gemini-3.6-flash|low|gemini-3.6-flash-low'
    'gemini-3.6-flash|medium|gemini-3.6-flash-medium'
    'gemini-3.6-flash|high|gemini-3.6-flash-high'
    'gemini-3.5-flash|low|gemini-3.5-flash-low'
    'gemini-3.5-flash|medium|gemini-3.5-flash-medium'
    'gemini-3.5-flash|high|gemini-3.5-flash-high'
    'gemini-3.1-pro|low|gemini-3.1-pro-low'
    'gemini-3.1-pro|high|gemini-3.1-pro-high'
)
for direct_pair in "${direct_pairs[@]}"; do
    IFS='|' read -r pair_model pair_effort pair_resolved <<< "$direct_pair"
    pair_index=$((pair_index+1))
    for source_mode in cli-cli cli-env env-cli env-env; do
        pair_job="direct-pair-$pair_index-$source_mode"
        case "$source_mode" in
            cli-cli)
                printf 'direct pair %s %s\n' "$pair_index" "$source_mode" | \
                    run_worker "$pair_job" --model "$pair_model" --effort "$pair_effort" \
                    > "$TMP/$pair_job.out" 2> "$TMP/$pair_job.err" ;;
            cli-env)
                printf 'direct pair %s %s\n' "$pair_index" "$source_mode" | \
                    AGY_WORKER_EFFORT="$pair_effort" run_worker "$pair_job" --model "$pair_model" \
                    > "$TMP/$pair_job.out" 2> "$TMP/$pair_job.err" ;;
            env-cli)
                printf 'direct pair %s %s\n' "$pair_index" "$source_mode" | \
                    AGY_WORKER_MODEL="$pair_model" run_worker "$pair_job" --effort "$pair_effort" \
                    > "$TMP/$pair_job.out" 2> "$TMP/$pair_job.err" ;;
            env-env)
                printf 'direct pair %s %s\n' "$pair_index" "$source_mode" | \
                    AGY_WORKER_MODEL="$pair_model" AGY_WORKER_EFFORT="$pair_effort" \
                    run_worker "$pair_job" > "$TMP/$pair_job.out" 2> "$TMP/$pair_job.err" ;;
        esac
        rc=$?
        case "$source_mode" in
            cli-cli) expected_model_source=cli; expected_effort_source=cli ;;
            cli-env) expected_model_source=cli; expected_effort_source=environment ;;
            env-cli) expected_model_source=environment; expected_effort_source=cli ;;
            env-env) expected_model_source=environment; expected_effort_source=environment ;;
        esac
        if [[ "$rc" == 0 ]]; then
            assert_direct_result "reviewed pair $pair_model/$pair_effort accepts $source_mode" \
                "$pair_job" "$pair_resolved" "$pair_model" "$pair_effort" \
                "$expected_model_source" "$expected_effort_source"
        else
            bad "reviewed pair $pair_model/$pair_effort accepts $source_mode (exit $rc)"
        fi
    done
done

exact_index=0
for exact_model in \
    gemini-3.7-flash-low gemini-3.7-flash-medium gemini-3.7-flash-high \
    gemini-3.6-flash-low gemini-3.6-flash-medium gemini-3.6-flash-high \
    gemini-3.5-flash-low gemini-3.5-flash-medium gemini-3.5-flash-high \
    gemini-3.1-pro-low gemini-3.1-pro-high \
    claude-sonnet-4-6 claude-opus-4-6-thinking gpt-oss-120b-medium
do
    exact_index=$((exact_index+1))
    for exact_source in cli env; do
        exact_job="direct-exact-$exact_index-$exact_source"
        if [[ "$exact_source" == cli ]]; then
            printf 'direct exact %s cli\n' "$exact_index" | run_worker "$exact_job" \
                --model "$exact_model" > "$TMP/$exact_job.out" 2> "$TMP/$exact_job.err"
        else
            printf 'direct exact %s env\n' "$exact_index" | \
                AGY_WORKER_MODEL="$exact_model" run_worker "$exact_job" \
                > "$TMP/$exact_job.out" 2> "$TMP/$exact_job.err"
        fi
        rc=$?
        if [[ "$exact_source" == cli ]]; then
            exact_model_source=cli
        else
            exact_model_source=environment
        fi
        if [[ "$rc" == 0 ]]; then
            assert_direct_result "reviewed exact model $exact_model accepts $exact_source" \
                "$exact_job" "$exact_model" "$exact_model" "" \
                "$exact_model_source" ""
        else
            bad "reviewed exact model $exact_model accepts $exact_source (exit $rc)"
        fi
    done
done

expect_selector_reject() {
    local name="$1" job="$2"; shift 2
    printf 'must reject before task read\n' | run_worker "$job" "$@" \
        > "$TMP/$job.out" 2> "$TMP/$job.err"
    local got=$?
    if [[ "$got" == 64 && ! -s "$TMP/$job.out" \
            && ! -s "$TMP/$job.calls" \
            && ! -e "$TMP/logs/$job/task.txt" ]]; then
        ok "$name (exit 64, zero agy calls)"
    else
        bad "$name (exit $got, wanted 64 before task read and agy)"
    fi
}

expect_selector_reject "repeated model is ambiguous" repeated-model \
    --model gemini-3.6-flash --model gemini-3.6-flash --effort high
expect_selector_reject "repeated effort is ambiguous" repeated-effort \
    --model gemini-3.6-flash --effort high --effort high
expect_selector_reject "repeated tier is ambiguous" repeated-tier --tier bulk --tier bulk
expect_selector_reject "repeated literal model is ambiguous" repeated-literal \
    --literal-model future-model-1.2 --literal-model future-model-1.2
expect_selector_reject "literal model conflicts with reviewed model" literal-model-conflict \
    --literal-model future-model-1.2 --model gemini-3.6-flash --effort high
expect_selector_reject "literal model conflicts with effort" literal-effort-conflict \
    --literal-model future-model-1.2 --effort high
expect_selector_reject "uppercase literal model is rejected" literal-uppercase \
    --literal-model Future-Model-1.2
expect_selector_reject "slash literal model is rejected" literal-slash \
    --literal-model vendor/model-v1
literal_too_long="$(python3 -c 'print("a-" + "b" * 127)')"
expect_selector_reject "overlong literal model is rejected" literal-too-long \
    --literal-model "$literal_too_long"
expect_selector_reject "empty literal model is rejected" empty-literal --literal-model ''
expect_selector_reject "empty CLI model is rejected" empty-cli-model --model ''
expect_selector_reject "empty CLI effort is rejected" empty-cli-effort \
    --model gemini-3.6-flash --effort ''
expect_selector_reject "effort without model is rejected" effort-without-model --effort high
expect_selector_reject "base model without effort is rejected" base-without-effort \
    --model gemini-3.6-flash
expect_selector_reject "Pro medium is unsupported" pro-medium \
    --model gemini-3.1-pro --effort medium
expect_selector_reject "fixed Sonnet rejects effort" sonnet-effort \
    --model claude-sonnet-4-6 --effort high
expect_selector_reject "fixed Opus rejects effort" opus-effort \
    --model claude-opus-4-6-thinking --effort high
expect_selector_reject "fixed GPT rejects effort" gpt-effort \
    --model gpt-oss-120b-medium --effort medium
expect_selector_reject "compound slug rejects effort" compound-effort \
    --model gemini-3.6-flash-high --effort high
expect_selector_reject "unknown direct model is rejected" unknown-direct \
    --model vendor/model-v1
expect_selector_reject "case-changing a direct model is rejected" upper-direct \
    --model Gemini-3.6-Flash --effort high
expect_selector_reject "padded direct model is rejected" padded-direct \
    --model ' gemini-3.6-flash' --effort high
expect_selector_reject "thinking-style effort is rejected" thinking-effort \
    --model gemini-3.6-flash --effort thinking-high
expect_selector_reject "invented thinking-level flag is rejected" thinking-flag \
    --model gemini-3.6-flash --thinking-level high

assert_env_reject() {
    local name="$1" job="$2" got="$3"
    if [[ "$got" == 64 && ! -s "$TMP/$job.out" && ! -s "$TMP/$job.calls" \
            && ! -e "$TMP/logs/$job/task.txt" ]]; then
        ok "$name (exit 64, zero agy calls)"
    else
        bad "$name (exit $got, wanted 64 before task read and agy)"
    fi
}

printf 'same model conflict\n' | AGY_WORKER_MODEL=gemini-3.6-flash \
    run_worker same-model-conflict --model gemini-3.6-flash --effort high \
    > "$TMP/same-model-conflict.out" 2> "$TMP/same-model-conflict.err"
assert_env_reject "same model in CLI and environment conflicts" same-model-conflict "$?"
printf 'same effort conflict\n' | AGY_WORKER_EFFORT=high \
    run_worker same-effort-conflict --model gemini-3.6-flash --effort high \
    > "$TMP/same-effort-conflict.out" 2> "$TMP/same-effort-conflict.err"
assert_env_reject "same effort in CLI and environment conflicts" same-effort-conflict "$?"
printf 'same tier conflict\n' | AGY_WORKER_TIER=bulk \
    run_worker same-tier-conflict --tier bulk \
    > "$TMP/same-tier-conflict.out" 2> "$TMP/same-tier-conflict.err"
assert_env_reject "same tier in CLI and environment conflicts" same-tier-conflict "$?"
printf 'empty env model\n' | AGY_WORKER_MODEL= run_worker empty-env-model \
    > "$TMP/empty-env-model.out" 2> "$TMP/empty-env-model.err"
assert_env_reject "explicit empty environment model is rejected" empty-env-model "$?"
printf 'empty env effort\n' | AGY_WORKER_MODEL=gemini-3.6-flash AGY_WORKER_EFFORT= \
    run_worker empty-env-effort > "$TMP/empty-env-effort.out" 2> "$TMP/empty-env-effort.err"
assert_env_reject "explicit empty environment effort is rejected" empty-env-effort "$?"
printf 'empty env tier\n' | AGY_WORKER_TIER= run_worker empty-env-tier \
    > "$TMP/empty-env-tier.out" 2> "$TMP/empty-env-tier.err"
assert_env_reject "explicit empty environment tier is rejected" empty-env-tier "$?"
printf 'tier cli model env\n' | AGY_WORKER_MODEL=gemini-3.6-flash-high \
    run_worker tier-cli-model-env --tier bulk \
    > "$TMP/tier-cli-model-env.out" 2> "$TMP/tier-cli-model-env.err"
assert_env_reject "CLI tier conflicts with environment model" tier-cli-model-env "$?"
printf 'tier env model cli\n' | AGY_WORKER_TIER=bulk \
    run_worker tier-env-model-cli --model gemini-3.6-flash-high \
    > "$TMP/tier-env-model-cli.out" 2> "$TMP/tier-env-model-cli.err"
assert_env_reject "environment tier conflicts with CLI model" tier-env-model-cli "$?"
printf 'tier cli effort env\n' | AGY_WORKER_EFFORT=high \
    run_worker tier-cli-effort-env --tier bulk \
    > "$TMP/tier-cli-effort-env.out" 2> "$TMP/tier-cli-effort-env.err"
assert_env_reject "CLI tier conflicts with environment effort" tier-cli-effort-env "$?"
printf 'tier env effort cli\n' | AGY_WORKER_TIER=bulk \
    run_worker tier-env-effort-cli --effort high \
    > "$TMP/tier-env-effort-cli.out" 2> "$TMP/tier-env-effort-cli.err"
assert_env_reject "environment tier conflicts with CLI effort" tier-env-effort-cli "$?"

selector_fixture_has_no_bytecode() {
    ! find "$1/runtime/scripts" -type f -name '*.pyc' -print -quit | grep -q . \
        && [[ ! -d "$1/runtime/scripts/__pycache__" ]]
}

make_selector_fixture() {
    local destination="$1" mode="$2" source="${3:-$ROOT/skills/agy-worker}"
    cp -R "$source" "$destination"
    # Test fixtures must start from source bytes, not an ambient interpreter cache.
    # This is deliberately fixture-local; it never deletes checkout/cache inputs.
    rm -rf "$destination/runtime/scripts/__pycache__"
    case "$mode" in
        clean) ;;
        disabled|missing-output)
            python3 - "$destination/runtime/compat/agy-model-effort-matrix.json" "$mode" <<'PY'
import hashlib
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
data = json.loads(path.read_text())
if mode == "disabled":
    data["resolution_status"] = "disabled-unverified-source"
    data["inventory"]["reviewed_source_revision"] = None
    data["inventory"]["evidence"] = ["installed-agy-models"]
else:
    data["adjustable_models"][0]["resolutions"]["high"] = ""
path.write_text(json.dumps(data, indent=2) + "\n")
digest = hashlib.sha256(path.read_bytes()).hexdigest()
(path.parent / "agy-model-effort-matrix.sha256").write_text(digest + "\n")
PY
            ;;
        source-drift) printf '%040d\n' 0 > "$destination/runtime/compat/agy-upstream-head.txt" ;;
        coordinated-source-drift)
            python3 - "$destination/runtime/compat" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
revision = "0" * 40
(root / "agy-upstream-head.txt").write_text(revision + "\n", encoding="ascii")
for name in ("agy-model-effort-matrix", "agy-models-inventory-binding"):
    path = root / f"{name}.json"
    value = json.loads(path.read_text())
    value["inventory" if name.endswith("matrix") else "reviewed_source_revision"] = (
        {**value["inventory"], "reviewed_source_revision": revision}
        if name.endswith("matrix")
        else revision
    )
    path.write_text(json.dumps(value, indent=2) + "\n")
    (root / f"{name}.sha256").write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + "\n",
        encoding="ascii",
    )
PY
            ;;
        version-drift) printf '9.9.9\n' > "$destination/runtime/compat/agy-verified-version.txt" ;;
        sha-mismatch) printf '%064d\n' 0 > "$destination/runtime/compat/agy-model-effort-matrix.sha256" ;;
        missing-matrix) rm -f "$destination/runtime/compat/agy-model-effort-matrix.json" ;;
        malformed-schema) printf '\n{}\n' >> "$destination/runtime/compat/model-effort-matrix.schema.json" ;;
    esac
}

fixture_cache_source="$TMP/selector-fixture-cache-source"
fixture_cache_destination="$TMP/selector-fixture-cache-destination"
cp -R "$ROOT/skills/agy-worker" "$fixture_cache_source"
mkdir -p "$fixture_cache_source/runtime/scripts/__pycache__"
python3 -m py_compile "$fixture_cache_source/runtime/scripts/model_selection.py"
make_selector_fixture "$fixture_cache_destination" clean "$fixture_cache_source"
fixture_cache_baseline=1
selector_fixture_has_no_bytecode "$fixture_cache_destination" || fixture_cache_baseline=0
python3 -m py_compile "$fixture_cache_destination/runtime/scripts/model_selection.py"
fixture_cache_generated=1
selector_fixture_has_no_bytecode "$fixture_cache_destination" && fixture_cache_generated=0
if [[ "$fixture_cache_baseline" == 1 && "$fixture_cache_generated" == 1 ]]; then
    ok "selector fixtures exclude ambient bytecode but detect fixture-generated bytecode"
else
    bad "selector fixture bytecode baseline or generated-bytecode detection"
fi

expect_compat_reject() {
    local name="$1" fixture="$2" job="$3" want="$4" version_mode="$5" calls_want="$6"
    printf 'compatibility rejection\n' | AGY_TEST_WORKER="$fixture/runtime/agy-worker.sh" \
        FAKE_VERSION_MODE="$version_mode" run_worker "$job" \
        --model gemini-3.6-flash --effort high \
        > "$TMP/$job.out" 2> "$TMP/$job.err"
    local got=$? calls=0
    [[ ! -f "$TMP/$job.calls" ]] || calls="$(wc -l < "$TMP/$job.calls" | tr -d ' ')"
    if [[ "$got" == "$want" && "$calls" == "$calls_want" \
            && ! -s "$TMP/$job.worker-calls" \
            && ! -e "$TMP/logs/$job/task.txt" ]]; then
        ok "$name (exit $got, calls $calls, zero worker dispatch)"
    else
        bad "$name (exit $got/calls $calls, wanted $want/$calls_want and zero worker)"
    fi
}

for compat_mode in disabled source-drift coordinated-source-drift version-drift sha-mismatch missing-matrix \
    malformed-schema missing-output; do
    compat_fixture="$TMP/selector-$compat_mode"
    make_selector_fixture "$compat_fixture" "$compat_mode"
    case "$compat_mode" in
        disabled|source-drift|version-drift) compat_exit=7 ;;
        *) compat_exit=8 ;;
    esac
    expect_compat_reject "$compat_mode matrix evidence fails closed" \
        "$compat_fixture" "compat-$compat_mode" "$compat_exit" ready 0
done

VERSION_FIXTURE="$TMP/selector-version-probes"
make_selector_fixture "$VERSION_FIXTURE" clean
printf 'version drift needs Codex review before task intake\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift117 \
    run_worker version-drift-installed --model gemini-3.6-flash --effort high \
    > "$TMP/version-drift-installed.out" 2> "$TMP/version-drift-installed.err"
rc=$?
if [[ "$rc" == 7 && "$(cat "$TMP/version-drift-installed.calls")" == $'version\nhelp' \
        && ! -s "$TMP/version-drift-installed.worker-calls" \
        && ! -e "$TMP/logs/version-drift-installed/task.txt" ]] \
        && ! grep -Eq 'Usage of agy:|Select a model|currently unavailable|/tmp|/Users' "$TMP/version-drift-installed.err"
then
    ok "installed version drift is review-required before task intake or provider dispatch"
else
    bad "installed version drift review-required boundary"
fi
# The approval digest is a public product fact, not an out-of-band shell hash.
# It must name only bounded compatibility evidence and never the executable,
# help prose, prompt, repository, or credentials.
HELP_SHA="$(python3 - "$TMP/version-drift-installed.err" <<'PY'
import json
import sys
raw = open(sys.argv[1], encoding="utf-8").read().splitlines()
prefix = "model-selection: review-required "
assert len(raw) == 1 and raw[0].startswith(prefix), raw
value = json.loads(raw[0][len(prefix):])
assert value == {
    "schema_version": 1,
    "kind": "agy-worker-compatibility-review-evidence",
    "installed_agy_version": "1.1.17",
    "matrix_agy_version": "1.1.22",
    "version_relation": "drift",
    "compatibility_status": "direct-selection-review-required",
    "critical_interface_status": "compatible",
    "critical_capabilities_sha256": value["critical_capabilities_sha256"],
    "raw_help_sha256": value["raw_help_sha256"],
    "user_model": "gemini-3.6-flash",
    "user_model_source": "cli",
    "user_effort": "high",
    "user_effort_source": "cli",
    "resolved_agy_model": "gemini-3.6-flash-high",
    "retry_selection_arguments": [
        "--model", "gemini-3.6-flash", "--effort", "high",
        "--compatibility-disposition", "proceed", "--approve-help-sha", value["raw_help_sha256"],
    ],
    "retry_selection_environment": {},
    "approval": {"compatibility_disposition": None, "approve_help_sha256": None},
}
assert all(isinstance(value[key], str) and len(value[key]) == 64 for key in (
    "critical_capabilities_sha256", "raw_help_sha256",
))
assert not any(secret in raw[0] for secret in ("/tmp", "/Users", "Usage of agy:", "version drift needs"))
assert "selected_model" not in value and "selected_effort" not in value
print(value["raw_help_sha256"])
PY
)"
if [[ -n "$HELP_SHA" ]]; then
    ok "version drift publishes bounded sanitized approval evidence from product output"
else
    bad "version drift approval evidence shape"
fi

# A drift review must preserve exactly where each caller selector came from.
# The retry fragment is deliberately limited to selector argv/environment plus
# the newly observed approval digest: task text, paths, and ambient secrets are
# not review evidence.
assert_drift_retry_selection() {
    local name="$1" error_file="$2" expected_model="$3" expected_model_source="$4"
    local expected_effort="$5" expected_effort_source="$6" expected_resolved="$7"
    if python3 - "$error_file" "$expected_model" "$expected_model_source" \
            "$expected_effort" "$expected_effort_source" "$expected_resolved" <<'PY'
import json
import sys

path, model, model_source, effort, effort_source, resolved = sys.argv[1:]
line = open(path, encoding="utf-8").read().strip()
prefix = "model-selection: review-required "
assert line.startswith(prefix)
value = json.loads(line[len(prefix):])
assert value["user_model"] == model
assert value["user_model_source"] == model_source
assert value["resolved_agy_model"] == resolved
if effort:
    assert value["user_effort"] == effort
    assert value["user_effort_source"] == effort_source
else:
    assert "user_effort" not in value and "user_effort_source" not in value
args = value["retry_selection_arguments"]
env = value["retry_selection_environment"]
assert isinstance(args, list) and all(isinstance(item, str) for item in args)
assert isinstance(env, dict) and set(env) <= {"AGY_WORKER_MODEL", "AGY_WORKER_EFFORT"}
expected_args = []
expected_env = {}
if model_source == "cli":
    expected_args += ["--model", model]
else:
    expected_env["AGY_WORKER_MODEL"] = model
if effort:
    if effort_source == "cli":
        expected_args += ["--effort", effort]
    else:
        expected_env["AGY_WORKER_EFFORT"] = effort
expected_args += ["--compatibility-disposition", "proceed", "--approve-help-sha", value["raw_help_sha256"]]
assert args == expected_args and env == expected_env
assert not ({"selected_model", "selected_effort"} & set(value))
assert not any(token in line for token in ("prompt-secret", "/private/", "ambient-secret"))
PY
    then
        ok "$name"
    else
        bad "$name"
    fi
}

printf 'prompt-secret env model and effort\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift117 \
    AGY_WORKER_MODEL=gemini-3.6-flash AGY_WORKER_EFFORT=high \
    AGY_WORKER_UNRELATED_SECRET=ambient-secret run_worker version-drift-env \
    > "$TMP/version-drift-env.out" 2> "$TMP/version-drift-env.err"
rc=$?
if [[ "$rc" == 7 ]]; then
    assert_drift_retry_selection "drift evidence preserves environment model and effort provenance" \
        "$TMP/version-drift-env.err" gemini-3.6-flash environment high environment gemini-3.6-flash-high
else
    bad "environment drift evidence preflight (exit $rc)"
fi

printf 'prompt-secret mixed selector provenance\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift117 \
    AGY_WORKER_EFFORT=high AGY_WORKER_UNRELATED_SECRET=ambient-secret \
    run_worker version-drift-mixed --model gemini-3.6-flash \
    > "$TMP/version-drift-mixed.out" 2> "$TMP/version-drift-mixed.err"
rc=$?
if [[ "$rc" == 7 ]]; then
    assert_drift_retry_selection "drift evidence preserves mixed CLI/environment provenance" \
        "$TMP/version-drift-mixed.err" gemini-3.6-flash cli high environment gemini-3.6-flash-high
else
    bad "mixed drift evidence preflight (exit $rc)"
fi

printf 'prompt-secret fixed selector\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift117 \
    AGY_WORKER_UNRELATED_SECRET=ambient-secret run_worker version-drift-fixed \
    --model gemini-3.6-flash-high \
    > "$TMP/version-drift-fixed.out" 2> "$TMP/version-drift-fixed.err"
rc=$?
if [[ "$rc" == 7 ]]; then
    assert_drift_retry_selection "drift evidence keeps exact compound as resolved rather than caller base" \
        "$TMP/version-drift-fixed.err" gemini-3.6-flash-high cli '' '' gemini-3.6-flash-high
else
    bad "fixed drift evidence preflight (exit $rc)"
fi

printf 'approved version drift preserves the caller selection\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift117 \
    run_worker version-drift-approved --model gemini-3.6-flash --effort high \
    --compatibility-disposition proceed --approve-help-sha "$HELP_SHA" \
    > "$TMP/version-drift-approved.out" 2> "$TMP/version-drift-approved.err"
rc=$?
if [[ "$rc" == 0 ]] && python3 - "$TMP/logs/version-drift-approved/selection.json" \
        "$TMP/version-drift-approved.argv" "$TMP/version-drift-approved.calls" "$HELP_SHA" <<'PY'
import json, sys
record = json.load(open(sys.argv[1], encoding="utf-8"))
argv = [item for item in open(sys.argv[2], "rb").read().split(b"\0") if item]
calls = open(sys.argv[3], encoding="ascii").read().splitlines()
assert calls == ["version", "help", "version", "help", "version", "help", "worker"]
assert record["schema_version"] == 3
assert record["compatibility_disposition"] == "proceed"
assert record["approved_help_sha256"] == record["help_sha256"] == sys.argv[4]
assert len(record["compatibility_decision_sha256"]) == 64
assert argv[argv.index(b"--model") + 1] == b"gemini-3.6-flash-high"
assert b"--effort" not in argv and b"--thinking-level" not in argv
PY
then
    ok "explicit Codex disposition binds exact help SHA and preserves model effort argv"
else
    bad "approved direct version drift binding"
fi
# A review-required selection consumes no task bytes and leaves no controller
# reservation, so the exact job id can be retried with its public digest.
retry_job="version-drift-retry"
printf 'review then exact same job retry\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift117 \
    run_worker "$retry_job" --model gemini-3.6-flash --effort high \
    > "$TMP/$retry_job.out" 2> "$TMP/$retry_job.err"
retry_rc=$?
RETRY_HELP_SHA="$(python3 - "$TMP/$retry_job.err" <<'PY'
import json
import sys
line = open(sys.argv[1], encoding="utf-8").read().strip()
prefix = "model-selection: review-required "
assert line.startswith(prefix)
print(json.loads(line[len(prefix):])["raw_help_sha256"])
PY
)"
retry_cleanup_ok=0
[[ ! -e "$TMP/logs/$retry_job" ]] && retry_cleanup_ok=1
rm -f "$TMP/$retry_job.calls"
printf 'approved exact same job retry\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift117 \
    run_worker "$retry_job" --model gemini-3.6-flash --effort high \
    --compatibility-disposition proceed --approve-help-sha "$RETRY_HELP_SHA" \
    > "$TMP/$retry_job-approved.out" 2> "$TMP/$retry_job-approved.err"
retry_approved_rc=$?
if [[ "$retry_rc" == 7 && "$retry_cleanup_ok" == 1 && "$retry_approved_rc" == 0 \
        && -f "$TMP/logs/$retry_job/selection.json" \
        && "$(cat "$TMP/$retry_job.calls")" == $'version\nhelp\nversion\nhelp\nversion\nhelp\nworker' ]]; then
    ok "review-required cleanup permits exact same job-id approval retry without provider before approval"
else
    bad "review-required job cleanup or same-id retry boundary"
fi
for approval_case in missing-help-sha stale-help-sha malformed-help-sha uppercase-disposition duplicate-disposition; do
    approval_args=(--model gemini-3.6-flash --effort high)
    approval_exit=64
    case "$approval_case" in
        missing-help-sha)
            approval_args+=(--compatibility-disposition proceed)
            approval_exit=7 ;;
        stale-help-sha)
            approval_args+=(--compatibility-disposition proceed --approve-help-sha "$(printf '0%.0s' {1..64})")
            approval_exit=7 ;;
        malformed-help-sha)
            approval_args+=(--compatibility-disposition proceed --approve-help-sha "NOT-A-SHA") ;;
        uppercase-disposition)
            approval_args+=(--compatibility-disposition PROCEED --approve-help-sha "$HELP_SHA") ;;
        duplicate-disposition)
            approval_args+=(--compatibility-disposition proceed --compatibility-disposition proceed --approve-help-sha "$HELP_SHA") ;;
    esac
    approval_job="approval-$approval_case"
    printf 'invalid compatibility approval must remain pre-task\n' | \
        AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift117 \
        run_worker "$approval_job" "${approval_args[@]}" \
        > "$TMP/$approval_job.out" 2> "$TMP/$approval_job.err"
    rc=$?
    if [[ "$rc" == "$approval_exit" && ! -s "$TMP/$approval_job.worker-calls" \
            && ! -e "$TMP/logs/$approval_job/task.txt" ]]; then
        ok "$approval_case is rejected before task intake and provider dispatch"
    else
        bad "$approval_case rejection boundary (exit $rc)"
    fi
done
printf 'far version drift keeps the exact requested model\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" FAKE_VERSION_MODE=drift999 \
    run_worker version-drift-999 --model gemini-3.7-flash --effort high \
    --compatibility-disposition proceed --approve-help-sha "$HELP_SHA" \
    > "$TMP/version-drift-999.out" 2> "$TMP/version-drift-999.err"
rc=$?
if [[ "$rc" == 0 ]] && python3 - "$TMP/logs/version-drift-999/selection.json" \
        "$TMP/version-drift-999.argv" "$TMP/version-drift-999.calls" <<'PY'
import json
import sys
record = json.load(open(sys.argv[1], encoding="utf-8"))
argv = [item for item in open(sys.argv[2], "rb").read().split(b"\0") if item]
calls = open(sys.argv[3], encoding="ascii").read().splitlines()
assert calls == ["version", "help", "version", "help", "version", "help", "worker"]
assert calls.count("worker") == 1
assert record["schema_version"] == 3
assert record["installed_agy_version"] == "9.9.9"
assert record["version_relation"] == "drift"
assert record["compatibility_disposition"] == "proceed"
assert record["resolved_agy_model"] == "gemini-3.7-flash-high"
assert argv[argv.index(b"--model") + 1] == b"gemini-3.7-flash-high"
assert b"--effort" not in argv and b"--thinking-level" not in argv
PY
then
    ok "9.9.9 drift preserves the caller's exact model and effort resolution"
else
    bad "9.9.9 drift exact model preservation"
fi
for version_mode in fail empty malformed oversize hang; do
    expect_compat_reject "$version_mode version evidence is unavailable" \
        "$VERSION_FIXTURE" "version-$version_mode" 8 "$version_mode" 1
done

for help_mode in missing duplicate malformed semantic utf8 nul oversize fail hang; do
    help_job="help-$help_mode"
    help_version_mode=ready
    [[ "$help_mode" != semantic ]] || help_version_mode=drift117
    printf 'help interface failure must not read this task\n' | \
        AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
        FAKE_VERSION_MODE="$help_version_mode" FAKE_HELP_MODE="$help_mode" \
        run_worker "$help_job" --model gemini-3.6-flash --effort high \
        > "$TMP/$help_job.out" 2> "$TMP/$help_job.err"
    rc=$?
    help_calls=0
    [[ ! -f "$TMP/$help_job.calls" ]] || help_calls="$(wc -l < "$TMP/$help_job.calls" | tr -d ' ')"
    help_expected=8
    if [[ "$help_mode" == semantic ]]; then
        # Option-local prose is evidence for Codex's review, not a controller
        # semantic decision.  Under version drift it reaches the same
        # review-required boundary as other structurally compatible drift.
        help_expected=7
    fi
    if [[ "$rc" == "$help_expected" && "$help_calls" == 2 && ! -s "$TMP/$help_job.worker-calls" \
            && ! -e "$TMP/logs/$help_job/task.txt" ]]; then
        ok "$help_mode help preflight is pre-provider and task-unread"
    else
        bad "$help_mode help preflight (exit $rc/calls $help_calls, wanted $help_expected/2)"
    fi
done

cp "$TMP/bin/agy" "$TMP/bin/agy.binding-original"
printf 'executable binding drift must not read this task\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
    FAKE_MUTATE_EXECUTABLE="$TMP/bin/agy" \
    run_worker executable-binding-drift --model gemini-3.6-flash --effort high \
    > "$TMP/executable-binding-drift.out" 2> "$TMP/executable-binding-drift.err"
rc=$?
if [[ "$rc" == 8 \
        && "$(cat "$TMP/executable-binding-drift.calls")" == $'version\nhelp' \
        && ! -s "$TMP/executable-binding-drift.worker-calls" \
        && ! -e "$TMP/logs/executable-binding-drift/task.txt" ]]; then
    ok "executable binding drift stops before task read or provider dispatch"
else
    bad "executable binding drift pre-provider boundary"
fi
mv "$TMP/bin/agy.binding-original" "$TMP/bin/agy"
chmod 0755 "$TMP/bin/agy"

# The first `--help` probe mutates the executable in-place without changing its
# inode, byte length, mode, uid/gid, or mtime.  Every lifecycle origin reaches
# the one controller launch routine below, so this initial dispatch proves the
# shared direct-selection launch binding fails before task intake or provider IO.
cp "$TMP/bin/agy" "$TMP/bin/agy.content-original"
CONTENT_MUTATION_MARKER="$TMP/executable-content-binding-drift.mutated"
printf 'same-length executable mutation must not read this task\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
    FAKE_MUTATE_EXECUTABLE_SAME_LENGTH="$TMP/bin/agy" \
    FAKE_MUTATION_MARKER="$CONTENT_MUTATION_MARKER" \
    run_worker executable-content-binding-drift --model gemini-3.6-flash --effort high \
    > "$TMP/executable-content-binding-drift.out" 2> "$TMP/executable-content-binding-drift.err"
rc=$?
if [[ "$rc" == 8 && -f "$CONTENT_MUTATION_MARKER" \
        && ! -s "$TMP/executable-content-binding-drift.worker-calls" \
        && ! -e "$TMP/logs/executable-content-binding-drift/task.txt" ]]; then
    ok "same-length restored-mtime executable mutation stops before task or provider"
else
    bad "same-length restored-mtime executable mutation boundary"
fi
mv "$TMP/bin/agy.content-original" "$TMP/bin/agy"
chmod 0755 "$TMP/bin/agy"

# A replaced final symlink can resolve to exactly the same executable object.
# Target-only comparison accepted that topology change; the frozen binding must
# retain the symlink entry and every checked component as well.
cp "$TMP/bin/agy" "$TMP/bin/agy.real"
cp "$TMP/bin/agy" "$TMP/bin/agy.other"
chmod 0755 "$TMP/bin/agy.real" "$TMP/bin/agy.other"
rm "$TMP/bin/agy"
ln -s "agy.real" "$TMP/bin/agy"
printf 'same-target symlink replacement must not read this task\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
    FAKE_REPLACE_EXECUTABLE_SYMLINK="$TMP/bin/agy" \
    FAKE_EXECUTABLE_SYMLINK_TARGET="$TMP/bin/agy.real" \
    run_worker executable-symlink-same-target-drift --model gemini-3.6-flash --effort high \
    > "$TMP/executable-symlink-same-target-drift.out" 2> "$TMP/executable-symlink-same-target-drift.err"
rc=$?
if [[ "$rc" == 8 \
        && "$(cat "$TMP/executable-symlink-same-target-drift.calls")" == $'version\nhelp' \
        && ! -s "$TMP/executable-symlink-same-target-drift.worker-calls" \
        && ! -e "$TMP/logs/executable-symlink-same-target-drift/task.txt" ]]; then
    ok "atomic same-target executable symlink replacement stops before task or provider"
else
    bad "atomic same-target executable symlink replacement boundary"
fi

printf 'different executable target must not read this task\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
    FAKE_REPLACE_EXECUTABLE_SYMLINK="$TMP/bin/agy" \
    FAKE_EXECUTABLE_SYMLINK_TARGET="$TMP/bin/agy.other" \
    run_worker executable-symlink-different-target --model gemini-3.6-flash --effort high \
    > "$TMP/executable-symlink-different-target.out" 2> "$TMP/executable-symlink-different-target.err"
rc=$?
if [[ "$rc" == 8 && ! -s "$TMP/executable-symlink-different-target.worker-calls" \
        && ! -e "$TMP/logs/executable-symlink-different-target/task.txt" ]]; then
    ok "different executable symlink target stops before task or provider"
else
    bad "different executable symlink target boundary"
fi

printf 'unsafe executable mode must not read this task\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
    FAKE_MUTATE_EXECUTABLE_MODE="$TMP/bin/agy.other" \
    run_worker executable-unsafe-mode --model gemini-3.6-flash --effort high \
    > "$TMP/executable-unsafe-mode.out" 2> "$TMP/executable-unsafe-mode.err"
rc=$?
if [[ "$rc" == 8 && ! -s "$TMP/executable-unsafe-mode.worker-calls" \
        && ! -e "$TMP/logs/executable-unsafe-mode/task.txt" ]]; then
    ok "unsafe executable mode stops before task or provider"
else
    bad "unsafe executable mode boundary"
fi
chmod 0755 "$TMP/bin/agy.other"

printf 'unsafe executable parent must not read this task\n' | \
    AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
    FAKE_MUTATE_EXECUTABLE_PARENT="$TMP/bin" \
    run_worker executable-parent-authority-drift --model gemini-3.6-flash --effort high \
    > "$TMP/executable-parent-authority-drift.out" 2> "$TMP/executable-parent-authority-drift.err"
rc=$?
if [[ "$rc" == 8 && ! -s "$TMP/executable-parent-authority-drift.worker-calls" \
        && ! -e "$TMP/logs/executable-parent-authority-drift/task.txt" ]]; then
    ok "unsafe executable parent authority stops before task or provider"
else
    bad "unsafe executable parent authority boundary"
fi
chmod 0755 "$TMP/bin"
# The path-chain negatives intentionally leave a symlink fixture behind.  The
# remaining legacy probe tests need the original regular fake executable, not
# that adversarial fixture's selected target.
rm "$TMP/bin/agy"
cp "$TMP/bin/agy.real" "$TMP/bin/agy"
chmod 0755 "$TMP/bin/agy"

SECONDS=0
expect_compat_reject "continuous-stream version evidence is bounded" \
    "$VERSION_FIXTURE" version-stream 8 stream 1
stream_elapsed=$SECONDS
if (( stream_elapsed < 3 )); then
    ok "continuous-stream probe fails promptly without reading the task"
else
    bad "continuous-stream probe exceeded the byte-bound deadline (${stream_elapsed}s)"
fi

run_child_stream_probe() {
    local job="$1" child_file="$TMP/$1.pid" pgid_file="$TMP/$1.pgid"
    local parent_file="$TMP/$1.parent" ready_file="$TMP/$1.ready"
    local release_file="$TMP/$1.release" task_file="$TMP/$1.task-input"
    local worker_pid ready=1 rc calls=0 cleanup=1 artifacts=0 elapsed
    local child_pid="" probe_pgid="" probe_parent=""
    printf 'stream probe must not read this task\n' > "$task_file"
    SECONDS=0
    FAKE_CHILD_PID_FILE="$child_file" \
        FAKE_PROBE_PGID_FILE="$pgid_file" \
        FAKE_PROBE_PARENT_PID_FILE="$parent_file" \
        FAKE_PROBE_READY_FILE="$ready_file" \
        FAKE_PROBE_RELEASE_FILE="$release_file" \
        AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
        FAKE_VERSION_MODE=child-stream run_worker "$job" \
        --model gemini-3.6-flash --effort high \
        < "$task_file" > "$TMP/$job.out" 2> "$TMP/$job.err" &
    worker_pid=$!
    for (( child_wait=0; child_wait<200; child_wait++ )); do
        if [[ -e "$ready_file" && -s "$parent_file" \
                && -s "$child_file" && -s "$pgid_file" ]]; then
            ready=0
            break
        fi
        if ! kill -0 "$worker_pid" 2>/dev/null; then
            break
        fi
        sleep 0.01
    done
    if (( ready == 0 )); then
        child_pid="$(<"$child_file")"
        probe_pgid="$(<"$pgid_file")"
        probe_parent="$(<"$parent_file")"
    fi
    : > "$release_file"
    wait "$worker_pid"
    rc=$?
    elapsed=$SECONDS
    [[ ! -f "$TMP/$job.calls" ]] \
        || calls="$(wc -l < "$TMP/$job.calls" | tr -d ' ')"
    if (( ready == 0 )); then
        wait_probe_cleanup "$child_pid" "$probe_pgid"
        cleanup=$?
    fi
    if [[ -d "$TMP/logs/$job" ]]; then
        artifacts="$(find "$TMP/logs/$job" -mindepth 1 -maxdepth 1 -print | wc -l | tr -d ' ')"
    fi
    case "$probe_parent:$probe_pgid:$child_pid" in
        *[!0-9:]*|:*|*::*) return 1 ;;
    esac
    [[ "$ready" == 0 && "$rc" == 8 && "$calls" == 1 \
        && "$elapsed" -lt 3 && "$probe_parent" != "$probe_pgid" \
        && "$probe_parent" != "$child_pid" && "$probe_pgid" != "$child_pid" \
        && "$cleanup" == 0 && "$artifacts" == 0 \
        && ! -s "$TMP/$job.out" && ! -s "$TMP/$job.worker-calls" \
        && "$(<"$TMP/$job.err")" == "model-selection: evidence-unavailable - agy version probe failed or was oversized" \
        && ! -e "$TMP/logs/$job/task.txt" \
        && ! -e "$TMP/logs/$job/selection.json" ]] \
        && selector_fixture_has_no_bytecode "$VERSION_FIXTURE"
}

if run_child_stream_probe version-child-stream; then
    ok "oversized child stream is handshake-bound, prompt, and group-clean"
else
    bad "oversized child stream handshake, bound, prompt, or group cleanup"
fi

child_stream_stress=0
for (( child_repeat=1; child_repeat<=20; child_repeat++ )); do
    if ! run_child_stream_probe "version-child-stream-stress-$child_repeat"; then
        child_stream_stress=1
        break
    fi
done
if (( child_stream_stress == 0 )); then
    ok "child-stream handshake and process-group cleanup are stable across 20 runs"
else
    bad "child-stream handshake or process-group cleanup flaked during 20 runs"
fi

for signal_case in HUP INT TERM; do
    case "$signal_case" in
        HUP) signal_exit=129; signal_slug=hup ;;
        INT) signal_exit=130; signal_slug=int ;;
        TERM) signal_exit=143; signal_slug=term ;;
    esac
    signal_job="version-signal-$signal_slug"
    signal_pid_file="$TMP/$signal_job.pid"
    signal_pgid_file="$TMP/$signal_job.pgid"
    signal_parent_file="$TMP/$signal_job.parent"
    signal_ready_file="$TMP/$signal_job.ready"
    signal_task_file="$TMP/$signal_job.task-input"
    printf 'probe signal must not read this task\n' > "$signal_task_file"
    FAKE_CHILD_PID_FILE="$signal_pid_file" \
        FAKE_PROBE_PGID_FILE="$signal_pgid_file" \
        FAKE_PROBE_PARENT_PID_FILE="$signal_parent_file" \
        FAKE_PROBE_READY_FILE="$signal_ready_file" \
        AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
        FAKE_VERSION_MODE=signal-wait run_worker "$signal_job" \
        --model gemini-3.6-flash --effort high \
        < "$signal_task_file" > "$TMP/$signal_job.out" 2> "$TMP/$signal_job.err" &
    signal_worker_pid=$!
    signal_ready=1
    for (( signal_wait=0; signal_wait<200; signal_wait++ )); do
        if [[ -e "$signal_ready_file" && -s "$signal_parent_file" \
                && -s "$signal_pid_file" && -s "$signal_pgid_file" ]]; then
            signal_ready=0
            break
        fi
        if ! kill -0 "$signal_worker_pid" 2>/dev/null; then
            break
        fi
        sleep 0.01
    done
    if (( signal_ready == 0 )); then
        signal_probe_parent="$(<"$signal_parent_file")"
        kill -s "$signal_case" "$signal_probe_parent" 2>/dev/null || true
    fi
    wait "$signal_worker_pid"
    rc=$?
    signal_calls=0
    [[ ! -f "$TMP/$signal_job.calls" ]] \
        || signal_calls="$(wc -l < "$TMP/$signal_job.calls" | tr -d ' ')"
    signal_cleanup=1
    if [[ -s "$signal_pid_file" && -s "$signal_pgid_file" ]]; then
        signal_child_pid="$(<"$signal_pid_file")"
        signal_probe_pgid="$(<"$signal_pgid_file")"
        wait_probe_cleanup "$signal_child_pid" "$signal_probe_pgid"
        signal_cleanup=$?
    fi
    signal_artifacts=0
    if [[ -d "$TMP/logs/$signal_job" ]]; then
        signal_artifacts="$(find "$TMP/logs/$signal_job" -mindepth 1 -maxdepth 1 -print | wc -l | tr -d ' ')"
    fi
    if [[ "$signal_ready" == 0 && "$rc" == "$signal_exit" && "$signal_calls" == 1 \
            && ! -s "$TMP/$signal_job.worker-calls" \
            && ! -s "$TMP/$signal_job.out" && ! -s "$TMP/$signal_job.err" \
            && ! -e "$TMP/logs/$signal_job/task.txt" \
            && ! -e "$TMP/logs/$signal_job/selection.json" \
            && "$signal_artifacts" == 0 \
            && "$signal_cleanup" == 0 ]] \
            && selector_fixture_has_no_bytecode "$VERSION_FIXTURE"; then
        ok "$signal_case interrupts the version probe with exit $signal_exit, no group, and no artifacts"
    else
        bad "$signal_case version-probe cleanup (ready $signal_ready/exit $rc/calls $signal_calls/artifacts $signal_artifacts/cleanup $signal_cleanup)"
    fi
done

NO_AGY_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
run_without_fake_agy() {
    local job="$1" path_value="$2"; shift 2
    PATH="$path_value" \
    AGY_WORKER_MODE=accept-edits \
    AGY_WORKER_LOG_DIR="$TMP/no-agy-logs" \
    AGY_WORKER_JOB_ID="$job" \
    AGY_WORKER_MAX_ATTEMPTS=1 \
    "$WORKER" --workdir "$TMP/repo" "$@"
}
mkdir -p "$TMP/no-agy-logs"
chmod 0755 "$TMP/no-agy-logs"

printf 'direct PATH missing must not be read\n' | run_without_fake_agy direct-path-missing \
    "$NO_AGY_PATH" --model gemini-3.6-flash --effort high \
    > "$TMP/direct-path-missing.out" 2> "$TMP/direct-path-missing.err"
rc=$?
if [[ "$rc" == 8 && ! -s "$TMP/direct-path-missing.out" \
        && ! -e "$TMP/no-agy-logs/direct-path-missing/task.txt" ]]; then
    ok "direct selector reports missing agy as unavailable before task read"
else
    bad "direct selector missing-agy boundary (exit $rc)"
fi

mkdir -p "$TMP/nonexec-agy-bin"
printf '#!/usr/bin/env bash\n: > %q\n' "$TMP/nonexec-agy-ran" \
    > "$TMP/nonexec-agy-bin/agy"
chmod 0644 "$TMP/nonexec-agy-bin/agy"
printf 'direct nonexec must not be read\n' | run_without_fake_agy direct-path-nonexec \
    "$TMP/nonexec-agy-bin:$NO_AGY_PATH" --model gemini-3.6-flash --effort high \
    > "$TMP/direct-path-nonexec.out" 2> "$TMP/direct-path-nonexec.err"
rc=$?
if [[ "$rc" == 8 && ! -e "$TMP/nonexec-agy-ran" \
        && ! -e "$TMP/no-agy-logs/direct-path-nonexec/task.txt" ]]; then
    ok "direct selector reports non-executable agy as unavailable before task read"
else
    bad "direct selector non-executable-agy boundary (exit $rc)"
fi

mkdir -p "$TMP/broken-agy-bin"
printf '#!/definitely/missing/agy-interpreter\n' > "$TMP/broken-agy-bin/agy"
chmod +x "$TMP/broken-agy-bin/agy"
printf 'direct broken launch must not be read\n' | run_without_fake_agy direct-start-fail \
    "$TMP/broken-agy-bin:$NO_AGY_PATH" --model gemini-3.6-flash --effort high \
    > "$TMP/direct-start-fail.out" 2> "$TMP/direct-start-fail.err"
rc=$?
if [[ "$rc" == 8 && ! -e "$TMP/no-agy-logs/direct-start-fail/task.txt" ]]; then
    ok "direct selector sanitizes an agy launch failure before task read"
else
    bad "direct selector launch-failure boundary (exit $rc)"
fi

printf 'legacy missing agy still consumes the task and reaches dispatch\n' | \
    run_without_fake_agy legacy-path-missing "$NO_AGY_PATH" --tier cheap \
    > "$TMP/legacy-path-missing.out" 2> "$TMP/legacy-path-missing.err"
rc=$?
if [[ "$rc" == 5 && -s "$TMP/no-agy-logs/legacy-path-missing/task.txt" \
        && -s "$TMP/no-agy-logs/legacy-path-missing/selection.json" ]]; then
    ok "legacy tier preserves historical missing-agy dispatch semantics"
else
    bad "legacy tier missing-agy semantics (exit $rc)"
fi

printf 'prefixed semantic version\n' | AGY_TEST_WORKER="$VERSION_FIXTURE/runtime/agy-worker.sh" \
    FAKE_VERSION_MODE=prefixed run_worker version-prefixed \
    --model gemini-3.6-flash --effort high \
    > "$TMP/version-prefixed.out" 2> "$TMP/version-prefixed.err"
rc=$?
if [[ "$rc" == 0 ]]; then
    assert_direct_result "documented prefixed agy version is accepted" version-prefixed \
        gemini-3.6-flash-high gemini-3.6-flash high cli cli 2
else
    bad "documented prefixed agy version is accepted (exit $rc)"
fi

VALID_DIRECT_RECORD="$TMP/logs/version-prefixed/selection.json"
VALID_TIER_RECORD="$TMP/logs/legacy-tier-1/selection.json"
ARTIFACT_CASES="$TMP/selection-artifacts"
mkdir -p "$ARTIFACT_CASES"
python3 -B - "$VALID_DIRECT_RECORD" "$VALID_TIER_RECORD" "$ARTIFACT_CASES" <<'PY'
import copy
import json
from pathlib import Path
import sys

direct = json.load(open(sys.argv[1], encoding="utf-8"))
tier = json.load(open(sys.argv[2], encoding="utf-8"))
root = Path(sys.argv[3])
assert direct["schema_version"] == 2
assert not ({"compatibility_disposition", "approved_help_sha256", "compatibility_decision_sha256"} & set(direct))

v2_only = {
    "version_relation", "compatibility_status", "critical_interface_probe_version",
    "critical_interface_status", "critical_capabilities_sha256", "help_sha256",
    "model_availability", "probed_executable",
}
v3_only = {
    "compatibility_disposition", "approved_help_sha256", "compatibility_decision_sha256",
}
legacy_direct = {key: value for key, value in direct.items() if key not in v2_only | v3_only}
legacy_direct["schema_version"] = 1

# Historical V2 records remain decodable, but a missing descriptor digest must
# make the current launch rebind fail closed rather than becoming trusted data.
legacy_v2_binding = copy.deepcopy(direct)
for key in v3_only:
    legacy_v2_binding.pop(key, None)
legacy_v2_binding["schema_version"] = 2
legacy_v2_binding["probed_executable"].pop("content_sha256")
legacy_v2_binding["probed_executable"]["target_lstat"].pop("ctime_ns")
for item in legacy_v2_binding["probed_executable"]["symlink_chain"]:
    item["lstat"].pop("ctime_ns")
for item in legacy_v2_binding["probed_executable"]["components"]:
    item["lstat"].pop("ctime_ns")

historical_v2_drift = copy.deepcopy(legacy_v2_binding)
historical_v2_drift.update({
    "installed_agy_version": "1.1.17",
    "version_relation": "drift",
    "compatibility_status": "critical-interface-compatible-version-drift",
})

v3_drift = copy.deepcopy(direct)
v3_drift.update({
    "schema_version": 3,
    "installed_agy_version": "1.1.17",
    "version_relation": "drift",
    "compatibility_status": "critical-interface-compatible-version-drift",
    "compatibility_disposition": "proceed",
    "approved_help_sha256": direct["help_sha256"],
})
decision_fields = (
    "compatibility_disposition", "approved_help_sha256", "help_sha256",
    "critical_capabilities_sha256", "installed_agy_version", "matrix_agy_version",
    "matrix_sha256", "matrix_source_revision", "selection_mode", "user_model",
    "user_model_source", "resolved_agy_model", "probed_executable",
)
decision = {key: v3_drift[key] for key in decision_fields}
if v3_drift["selection_mode"] == "model-effort":
    decision["user_effort"] = v3_drift["user_effort"]
    decision["user_effort_source"] = v3_drift["user_effort_source"]
v3_drift["compatibility_decision_sha256"] = __import__("hashlib").sha256(
    json.dumps(decision, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii"),
).hexdigest()

cases = {
    "three-key-direct": {
        "schema_version": 1,
        "kind": "agy-worker-selection",
        "selection_mode": "exact-model",
    },
    "tier-with-direct-fields": {**tier, "user_model": "gemini-3.6-flash-high"},
    "direct-missing-provenance": {key: value for key, value in direct.items() if key != "user_model_source"},
    "direct-invalid-source": {**direct, "user_model_source": "worker"},
    "direct-invalid-sha": {**direct, "matrix_sha256": "z" * 64},
    "direct-extra-field": {**direct, "applied": False},
    "v1-direct": legacy_direct,
    "v2-legacy-executable-binding": legacy_v2_binding,
    "v2-historical-drift": historical_v2_drift,
    "v2-historical-drift-with-v3-decision": {
        **historical_v2_drift, "compatibility_disposition": "proceed",
    },
    "v2-missing-help-digest": {key: value for key, value in direct.items() if key != "help_sha256"},
    "v2-contradictory-relation": {**direct, "version_relation": "drift"},
    "v2-extra-field": {**direct, "model_availability_detail": "not assessed"},
    "v2-tampered-binding": {**direct, "probed_executable": {
        **direct["probed_executable"], "target_lstat": {**direct["probed_executable"]["target_lstat"], "inode": 0},
    }},
    "v3-approved-drift": v3_drift,
    "v3-model-decision-transplant": {**v3_drift, "resolved_agy_model": "gemini-3.1-pro-high"},
    "v3-effort-decision-transplant": {**v3_drift, "user_effort": "medium"},
    "v3-source-decision-transplant": {**v3_drift, "user_model_source": "environment"},
}
for name, value in cases.items():
    (root / f"{name}.json").write_text(json.dumps(value) + "\n", encoding="utf-8")
PY

expect_valid_selection_record() {
    local name="$1" path="$2"
    "$SELECTOR" --validate-record "$path" > "$path.valid.out" 2> "$path.valid.err"
    local got=$?
    if [[ "$got" == 0 && ! -s "$path.valid.out" ]]; then ok "$name"; else bad "$name (exit $got)"; fi
}
expect_invalid_selection_record() {
    local name="$1" path="$2"
    "$SELECTOR" --validate-record "$path" > "$path.invalid.out" 2> "$path.invalid.err"
    local got=$?
    if [[ "$got" == 64 && ! -s "$path.invalid.out" ]]; then ok "$name"; else bad "$name (exit $got)"; fi
}
expect_valid_selection_record "runtime validator accepts a complete direct artifact" "$VALID_DIRECT_RECORD"
expect_valid_selection_record "runtime validator preserves v1 direct artifact compatibility" "$ARTIFACT_CASES/v1-direct.json"
expect_valid_selection_record "runtime validator reads a legacy v2 executable binding without trusting it for launch" "$ARTIFACT_CASES/v2-legacy-executable-binding.json"
expect_valid_selection_record "runtime validator preserves historical v2 drift as read-only evidence" "$ARTIFACT_CASES/v2-historical-drift.json"
expect_valid_selection_record "runtime validator accepts a complete tier artifact" "$VALID_TIER_RECORD"
expect_invalid_selection_record "runtime validator rejects a three-key direct artifact" "$ARTIFACT_CASES/three-key-direct.json"
expect_invalid_selection_record "runtime validator rejects tier records carrying direct fields" "$ARTIFACT_CASES/tier-with-direct-fields.json"
expect_invalid_selection_record "runtime validator rejects missing direct provenance" "$ARTIFACT_CASES/direct-missing-provenance.json"
expect_invalid_selection_record "runtime validator rejects an invalid source" "$ARTIFACT_CASES/direct-invalid-source.json"
expect_invalid_selection_record "runtime validator rejects an invalid matrix SHA" "$ARTIFACT_CASES/direct-invalid-sha.json"
expect_invalid_selection_record "runtime validator rejects extra artifact fields" "$ARTIFACT_CASES/direct-extra-field.json"
expect_invalid_selection_record "runtime validator rejects incomplete v2 interface evidence" "$ARTIFACT_CASES/v2-missing-help-digest.json"
expect_invalid_selection_record "runtime validator rejects contradictory v2 version relation" "$ARTIFACT_CASES/v2-contradictory-relation.json"
expect_invalid_selection_record "runtime validator rejects a historical v2 drift with v3 approval fields" "$ARTIFACT_CASES/v2-historical-drift-with-v3-decision.json"
expect_invalid_selection_record "runtime validator rejects extra v2 fields" "$ARTIFACT_CASES/v2-extra-field.json"
expect_invalid_selection_record "runtime validator rejects tampered v2 executable binding" "$ARTIFACT_CASES/v2-tampered-binding.json"
expect_valid_selection_record "runtime validator accepts an approved v3 drift decision" "$ARTIFACT_CASES/v3-approved-drift.json"
expect_invalid_selection_record "runtime validator rejects a v3 model decision transplant" "$ARTIFACT_CASES/v3-model-decision-transplant.json"
expect_invalid_selection_record "runtime validator rejects a v3 effort decision transplant" "$ARTIFACT_CASES/v3-effort-decision-transplant.json"
expect_invalid_selection_record "runtime validator rejects a v3 source decision transplant" "$ARTIFACT_CASES/v3-source-decision-transplant.json"

"$SELECTOR" --verify-record-executable "$ARTIFACT_CASES/v2-legacy-executable-binding.json" \
    > "$ARTIFACT_CASES/v2-legacy-executable-binding.verify.out" \
    2> "$ARTIFACT_CASES/v2-legacy-executable-binding.verify.err"
legacy_v2_verify_rc=$?
if [[ "$legacy_v2_verify_rc" == 64 && ! -s "$ARTIFACT_CASES/v2-legacy-executable-binding.verify.out" ]]; then
    ok "legacy v2 executable binding decodes but cannot authorize a current launch"
else
    bad "legacy v2 executable binding launch authority (exit $legacy_v2_verify_rc)"
fi

RETRY_FIXTURE="$TMP/selector-retry-freeze"
make_selector_fixture "$RETRY_FIXTURE" clean
RETRY_MATRIX="$RETRY_FIXTURE/runtime/compat/agy-model-effort-matrix.json"
printf 'automatic retry is forbidden\n' | \
    FAKE_DISPATCH_COUNT_FILE="$TMP/retry-freeze.dispatch-count" \
    FAKE_MUTATE_MATRIX="$RETRY_MATRIX" FAKE_FAIL_FIRST=1 \
    AGY_TEST_WORKER="$RETRY_FIXTURE/runtime/agy-worker.sh" \
    run_worker retry-freeze \
        --model gemini-3.6-flash --effort high \
        > "$TMP/retry-freeze.out" 2> "$TMP/retry-freeze.err"
rc=$?
if [[ "$rc" == 5 ]] && python3 - \
        "$TMP/logs/retry-freeze/selection.json" "$RETRY_MATRIX" \
        "$RETRY_FIXTURE/runtime/compat/agy-model-effort-matrix.sha256" \
        "$TMP/retry-freeze.calls" "$TMP/retry-freeze.worker-calls" <<'PY'
import hashlib
import json
import sys

selection_path, matrix_path, sha_path, calls_path, workers_path = sys.argv[1:]
selection = json.load(open(selection_path, encoding="utf-8"))
expected = open(sha_path, encoding="ascii").read().strip()
assert selection["resolved_agy_model"] == "gemini-3.6-flash-high"
assert selection["matrix_sha256"] == expected
assert hashlib.sha256(open(matrix_path, "rb").read()).hexdigest() != expected
calls = open(calls_path).read().splitlines()
assert calls == ["version", "help", "version", "help", "version", "help", "worker"]
assert calls.count("worker") == 1
assert open(workers_path).read().splitlines() == ["worker"]
PY
then
    ok "failure never starts an automatic fresh retry and preserves frozen selection"
else
    bad "failure must not start an automatic fresh retry"
fi

WRAPPER_FIXTURE="$TMP/root-wrapper"
mkdir -p "$WRAPPER_FIXTURE/skills"
cp "$ROOT/agy-worker.sh" "$WRAPPER_FIXTURE/agy-worker.sh"
cp -R "$ROOT/skills/agy-worker" "$WRAPPER_FIXTURE/skills/agy-worker"
chmod +x "$WRAPPER_FIXTURE/agy-worker.sh"
WRAPPER_REAL="$(cd "$WRAPPER_FIXTURE" && pwd -P)"
EXPECTED_CHECKOUT_SHA="$(python3 -I -S -B -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode("utf-8")).hexdigest())' "$WRAPPER_REAL")"

TEST_XDG="$TMP/isolated-xdg-state"
TEST_HOME="$TMP/isolated-home"
mkdir -p "$TEST_XDG" "$TEST_HOME"

wrapper_pass=1

# 1. Unset AGY_WORKER_LOG_DIR uses XDG_STATE_HOME
(
    unset AGY_WORKER_LOG_DIR
    printf 'unset log dir\n' | PATH="$TMP/bin:$PATH" \
        XDG_STATE_HOME="$TEST_XDG" HOME="$TEST_HOME" \
        AGY_WORKER_JOB_ID=unset-log AGY_WORKER_MODE=accept-edits \
        FAKE_MODEL_FILE="$TMP/unset-log.model" \
        FAKE_PROMPT_FILE="$TMP/unset-log.prompt" \
        FAKE_DIRS_FILE="$TMP/unset-log.dirs" \
        FAKE_ARGV_FILE="$TMP/unset-log.argv" \
        FAKE_STAGE_RESULT_FILE="$TMP/unset-log.stage-result" \
        "$WRAPPER_FIXTURE/agy-worker.sh" --workdir "$TMP/repo" \
        > "$TMP/unset-log.out" 2> "$TMP/unset-log.err"
)
rc=$?
if [[ "$rc" != "0" ]] \
        || [[ ! -f "$TEST_XDG/agy-worker/checkouts/$EXPECTED_CHECKOUT_SHA/logs/unset-log/task.txt" ]] \
        || [[ -e "$WRAPPER_FIXTURE/logs" ]] \
        || [[ -e "$WRAPPER_FIXTURE/skills/agy-worker/runtime/logs/unset-log" ]]; then
    wrapper_pass=0
fi

# 2. Empty AGY_WORKER_LOG_DIR with unset XDG_STATE_HOME falls back to HOME/.local/state
(
    unset XDG_STATE_HOME
    printf 'empty log override\n' | PATH="$TMP/bin:$PATH" \
        HOME="$TEST_HOME" AGY_WORKER_LOG_DIR= \
        AGY_WORKER_JOB_ID=empty-log-override AGY_WORKER_MODE=accept-edits \
        FAKE_MODEL_FILE="$TMP/empty-log.model" \
        FAKE_PROMPT_FILE="$TMP/empty-log.prompt" \
        FAKE_DIRS_FILE="$TMP/empty-log.dirs" \
        FAKE_ARGV_FILE="$TMP/empty-log.argv" \
        FAKE_STAGE_RESULT_FILE="$TMP/empty-log.stage-result" \
        "$WRAPPER_FIXTURE/agy-worker.sh" --workdir "$TMP/repo" \
        > "$TMP/empty-log.out" 2> "$TMP/empty-log.err"
)
rc=$?
if [[ "$rc" != "0" ]] \
        || [[ ! -f "$TEST_HOME/.local/state/agy-worker/checkouts/$EXPECTED_CHECKOUT_SHA/logs/empty-log-override/task.txt" ]]; then
    wrapper_pass=0
fi

# 3. Deterministic lifecycle reuse across commands (status uses same derived log root)
(
    unset XDG_STATE_HOME
    PATH="$TMP/bin:$PATH" HOME="$TEST_HOME" AGY_WORKER_LOG_DIR= \
        "$WRAPPER_FIXTURE/agy-worker.sh" status --job-id empty-log-override --format json \
        > "$TMP/empty-log-status.out" 2> "$TMP/empty-log-status.err"
)
rc=$?
if [[ "$rc" != "0" ]] || ! grep -Fq '"job_id":"empty-log-override"' "$TMP/empty-log-status.out"; then
    wrapper_pass=0
fi

# 4. Actionable error when neither XDG_STATE_HOME nor HOME is safe
(
    unset AGY_WORKER_LOG_DIR
    printf 'unsafe root\n' | PATH="$TMP/bin:$PATH" \
        XDG_STATE_HOME="relative/path" HOME="" \
        AGY_WORKER_JOB_ID=unsafe-root AGY_WORKER_MODE=accept-edits \
        "$WRAPPER_FIXTURE/agy-worker.sh" --workdir "$TMP/repo" \
        > "$TMP/unsafe-root.out" 2> "$TMP/unsafe-root.err"
)
rc=$?
if [[ "$rc" != "64" ]] \
        || ! grep -Fq 'unable to derive a safe state root; set an explicit external AGY_WORKER_LOG_DIR' "$TMP/unsafe-root.err" \
        || [[ -e "$WRAPPER_FIXTURE/logs/unsafe-root" ]]; then
    wrapper_pass=0
fi

# 5. Explicit external AGY_WORKER_LOG_DIR remains unchanged
EXPLICIT_EXTERNAL="$TMP/explicit-external-logs"
mkdir -p "$EXPLICIT_EXTERNAL"
(
    printf 'explicit log override\n' | PATH="$TMP/bin:$PATH" \
        AGY_WORKER_LOG_DIR="$EXPLICIT_EXTERNAL" AGY_WORKER_JOB_ID=explicit-log AGY_WORKER_MODE=accept-edits \
        FAKE_MODEL_FILE="$TMP/explicit-log.model" \
        FAKE_PROMPT_FILE="$TMP/explicit-log.prompt" \
        FAKE_DIRS_FILE="$TMP/explicit-log.dirs" \
        FAKE_ARGV_FILE="$TMP/explicit-log.argv" \
        FAKE_STAGE_RESULT_FILE="$TMP/explicit-log.stage-result" \
        "$WRAPPER_FIXTURE/agy-worker.sh" --workdir "$TMP/repo" \
        > "$TMP/explicit-log.out" 2> "$TMP/explicit-log.err"
)
rc=$?
if [[ "$rc" != "0" ]] || [[ ! -f "$EXPLICIT_EXTERNAL/explicit-log/task.txt" ]]; then
    wrapper_pass=0
fi

# 6. Project workflow rejects existing and prospective log roots equal to or inside workdir.
PROJECT_DIR="$TMP/project-test-worktree"
mkdir -p "$PROJECT_DIR/.git" "$PROJECT_DIR/logs"
touch "$PROJECT_DIR/.git/HEAD"

for test_case in equal child relative missing-child symlink-log symlink-worktree; do
    missing_root=""
    case "$test_case" in
        equal)
            test_log="$PROJECT_DIR"; test_work="$PROJECT_DIR"
            expected_job="$PROJECT_DIR/proj-equal" ;;
        child)
            test_log="$PROJECT_DIR/logs"; test_work="$PROJECT_DIR"
            expected_job="$PROJECT_DIR/logs/proj-child" ;;
        relative)
            test_log="./logs"; test_work="$PROJECT_DIR"
            expected_job="$PROJECT_DIR/logs/proj-relative" ;;
        missing-child)
            test_log="$PROJECT_DIR/new/logs"; test_work="$PROJECT_DIR"
            expected_job="$PROJECT_DIR/new/logs/proj-missing-child"
            missing_root="$PROJECT_DIR/new" ;;
        symlink-log)
            ln -s "$PROJECT_DIR/logs" "$TMP/symlink-proj-log"
            test_log="$TMP/symlink-proj-log"; test_work="$PROJECT_DIR"
            expected_job="$PROJECT_DIR/logs/proj-symlink-log" ;;
        symlink-worktree)
            ln -s "$PROJECT_DIR" "$TMP/symlink-proj-work"
            test_log="$PROJECT_DIR/logs"; test_work="$TMP/symlink-proj-work"
            expected_job="$PROJECT_DIR/logs/proj-symlink-worktree" ;;
    esac
    (
        cd "$PROJECT_DIR"
        printf 'project reject\n' | PATH="$TMP/bin:$PATH" \
            AGY_WORKER_LOG_DIR="$test_log" AGY_WORKER_JOB_ID="proj-$test_case" \
            FAKE_MODEL_FILE="$TMP/proj-$test_case.model" \
            FAKE_PROMPT_FILE="$TMP/proj-$test_case.prompt" \
            FAKE_DIRS_FILE="$TMP/proj-$test_case.dirs" \
            FAKE_ARGV_FILE="$TMP/proj-$test_case.argv" \
            FAKE_STAGE_RESULT_FILE="$TMP/proj-$test_case.stage-result" \
            FAKE_CALLED_FILE="$TMP/proj-$test_case.called" \
            "$WRAPPER_FIXTURE/agy-worker.sh" --workflow project --workdir "$test_work" \
            > "$TMP/proj-$test_case.out" 2> "$TMP/proj-$test_case.err"
    )
    rc=$?
    expected_msg="project log root cannot be inside the target workdir"
    if [[ "$rc" != "64" ]] \
            || ! grep -Fq "$expected_msg" "$TMP/proj-$test_case.err" \
            || [[ -e "$TMP/proj-$test_case.model" ]] \
            || [[ -e "$TMP/proj-$test_case.prompt" ]] \
            || [[ -e "$TMP/proj-$test_case.dirs" ]] \
            || [[ -e "$TMP/proj-$test_case.argv" ]] \
            || [[ -e "$TMP/proj-$test_case.stage-result" ]] \
            || [[ -e "$expected_job" ]] \
            || [[ -e "$expected_job/task.txt" ]] \
            || [[ -e "$expected_job/dispatch-state.json" ]] \
            || [[ -n "$missing_root" && -e "$missing_root" ]] \
            || [[ -e "$TMP/proj-$test_case.called" ]]; then
        wrapper_pass=0
    fi
done

if (( wrapper_pass )); then
    ok "root wrapper derives deterministic external state root and rejects project log root in worktree"
else
    bad "root wrapper derives deterministic external state root and rejects project log root in worktree"
fi

PRIVATE_LOG_022="$TMP/existing-log-022"
mkdir -p "$PRIVATE_LOG_022"
chmod 0755 "$PRIVATE_LOG_022"
(
    umask 022
    printf 'private artifacts under umask 022\n' | \
        AGY_TEST_LOG_DIR="$PRIVATE_LOG_022" run_worker private-022
) > "$TMP/private-022.out" 2>/dev/null
rc=$?
if [[ "$rc" == "0" ]] \
        && mode_is "$PRIVATE_LOG_022" 0755 \
        && log_root_is_acceptable "$PRIVATE_LOG_022" \
        && private_tree_is_private "$PRIVATE_LOG_022/private-022"; then
    ok "dispatcher keeps a new job private under umask 022 and a traversable custom log root"
else
    bad "dispatcher keeps a new job private under umask 022 and a traversable custom log root"
fi

PRIVATE_LOG_000="$TMP/existing-log-000"
mkdir -p "$PRIVATE_LOG_000"
chmod 0755 "$PRIVATE_LOG_000"
(
    umask 000
    printf 'private artifacts under umask 000\n' | \
        AGY_TEST_LOG_DIR="$PRIVATE_LOG_000" run_worker private-000
) > "$TMP/private-000.out" 2>/dev/null
rc=$?
if [[ "$rc" == "0" ]] \
        && mode_is "$PRIVATE_LOG_000" 0755 \
        && log_root_is_acceptable "$PRIVATE_LOG_000" \
        && mode_is "$TMP/private-000.model" 0666 \
        && private_tree_is_private "$PRIVATE_LOG_000/private-000"; then
    ok "dispatcher keeps artifacts private under umask 000 without changing the agy child umask"
else
    bad "dispatcher keeps artifacts private under umask 000 without changing the agy child umask"
fi

MISSING_LOG_ROOT="$TMP/missing-log-root"
(
    umask 000
    printf 'create a private log root\n' | \
        AGY_TEST_LOG_DIR="$MISSING_LOG_ROOT" run_worker missing-log-root
) > "$TMP/missing-log-root.out" 2>/dev/null
rc=$?
if [[ "$rc" == "0" ]] \
        && mode_is "$MISSING_LOG_ROOT" 0700 \
        && log_root_is_acceptable "$MISSING_LOG_ROOT" \
        && private_tree_is_private "$MISSING_LOG_ROOT/missing-log-root"; then
    ok "a missing custom log root is created owner-only under caller umask 000"
else
    bad "a missing custom log root is created owner-only under caller umask 000"
fi

invalid_root_index=0
for invalid_root_mode in 0777 0775; do
    invalid_root_index=$((invalid_root_index+1))
    invalid_root="$TMP/invalid-root-$invalid_root_index"
    invalid_job="invalid-root-$invalid_root_index"
    mkdir -p "$invalid_root"
    chmod "$invalid_root_mode" "$invalid_root"
    printf 'root sentinel %s\n' "$invalid_root_mode" > "$invalid_root/sentinel"
    printf 'must reject writable log root\n' | \
        AGY_TEST_LOG_DIR="$invalid_root" run_worker "$invalid_job" \
        > "$TMP/$invalid_job.out" 2>/dev/null
    rc=$?
    if [[ "$rc" == "64" ]] \
            && [[ "$(<"$invalid_root/sentinel")" == "root sentinel $invalid_root_mode" ]] \
            && [[ ! -e "$invalid_root/$invalid_job" ]] \
            && [[ ! -e "$TMP/$invalid_job.called" ]]; then
        ok "mode $invalid_root_mode log root is rejected before prompt staging or agy"
    else
        bad "mode $invalid_root_mode log root is rejected before prompt staging or agy"
    fi
done

SYMLINK_LOG_TARGET="$TMP/symlink-log-target"
SYMLINK_LOG_ROOT="$TMP/symlink-log-root"
mkdir -p "$SYMLINK_LOG_TARGET"
chmod 0755 "$SYMLINK_LOG_TARGET"
printf 'symlink root sentinel\n' > "$SYMLINK_LOG_TARGET/sentinel"
ln -s "$SYMLINK_LOG_TARGET" "$SYMLINK_LOG_ROOT"
printf 'must reject symlink log root\n' | \
    AGY_TEST_LOG_DIR="$SYMLINK_LOG_ROOT" run_worker symlink-log-root \
    > "$TMP/symlink-log-root.out" 2>/dev/null
rc=$?
if [[ "$rc" == "64" ]] \
        && [[ "$(<"$SYMLINK_LOG_TARGET/sentinel")" == "symlink root sentinel" ]] \
        && [[ ! -e "$SYMLINK_LOG_TARGET/symlink-log-root" ]] \
        && [[ ! -e "$TMP/symlink-log-root.called" ]]; then
    ok "symlink log root is rejected before prompt staging or agy"
else
    bad "symlink log root is rejected before prompt staging or agy"
fi

WEAK_ROOT_POLICY="$TMP/weak-root-policy"
mkdir -p "$WEAK_ROOT_POLICY"
cp -R "$ROOT/skills/agy-worker" "$WEAK_ROOT_POLICY/agy-worker"
python3 - "$WEAK_ROOT_POLICY/agy-worker/runtime/agy-worker.sh" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    source = handle.read()
old = 'if ! validate_log_root "$LOG_DIR"; then'
new = 'if ! true; then  # TEST MUTATION: final log-root policy bypassed'
if source.count(old) != 1:
    raise SystemExit("expected exactly one final log-root policy call")
with open(path, "w", encoding="utf-8") as handle:
    handle.write(source.replace(old, new, 1))
PY
WEAK_ROOT_POLICY_LOG="$TMP/weak-root-policy-log"
mkdir -p "$WEAK_ROOT_POLICY_LOG"
chmod 0777 "$WEAK_ROOT_POLICY_LOG"
printf 'mutation must be caught\n' | \
    AGY_TEST_WORKER="$WEAK_ROOT_POLICY/agy-worker/runtime/agy-worker.sh" \
    AGY_TEST_LOG_DIR="$WEAK_ROOT_POLICY_LOG" run_worker weak-root-policy \
    > "$TMP/weak-root-policy.out" 2>/dev/null
rc=$?
if [[ "$rc" == "0" ]] \
        && [[ -e "$TMP/weak-root-policy.called" ]] \
        && private_tree_is_private "$WEAK_ROOT_POLICY_LOG/weak-root-policy" \
        && ! log_root_is_acceptable "$WEAK_ROOT_POLICY_LOG" 2>/dev/null; then
    ok "log-root acceptance rejects a runtime with final-root validation bypassed"
else
    bad "log-root acceptance rejects a runtime with final-root validation bypassed"
fi

WEAK_UMASK_ROOT="$TMP/weak-umask"
mkdir -p "$WEAK_UMASK_ROOT"
cp -R "$ROOT/skills/agy-worker" "$WEAK_UMASK_ROOT/agy-worker"
python3 - "$WEAK_UMASK_ROOT/agy-worker/runtime/scripts/agy_dispatch.py" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    source = handle.read()
old = 'def _ensure_new_private(path: Path) -> int:\n    return os.open(\n'
new = 'def _ensure_new_private(path: Path) -> int:\n    os.umask(0)  # TEST MUTATION: private artifact creation weakened.\n    return os.open(\n'
if source.count(old) != 1:
    raise SystemExit("expected exactly one supervisor private-artifact block")
source = source.replace(old, new, 1)
old = '        0o600,\n    )\n\n\ndef _stage('
new = '        0o666,\n    )\n\n\ndef _stage('
if source.count(old) != 1:
    raise SystemExit("expected exactly one supervisor artifact mode")
with open(path, "w", encoding="utf-8") as handle:
    handle.write(source.replace(old, new, 1))
PY
WEAK_UMASK_LOG="$TMP/weak-umask-log"
mkdir -p "$WEAK_UMASK_LOG"
chmod 0755 "$WEAK_UMASK_LOG"
(
    umask 000
    printf 'mutation must be caught\n' | \
        AGY_TEST_WORKER="$WEAK_UMASK_ROOT/agy-worker/runtime/agy-worker.sh" \
        AGY_TEST_LOG_DIR="$WEAK_UMASK_LOG" run_worker weak-umask
) > "$TMP/weak-umask.out" 2>/dev/null
rc=$?
if [[ "$rc" != "0" && ! -s "$TMP/weak-umask.out" ]] \
        && ! private_tree_is_private "$WEAK_UMASK_LOG/weak-umask" 2>/dev/null; then
    ok "supervisor fails closed when a mutation weakens private creation"
else
    bad "privacy acceptance rejects a runtime with the private creation mask removed"
fi

COLLISION_LOG="$TMP/collision-log"
mkdir -p "$COLLISION_LOG/existing-dir" "$TMP/symlink-target"
chmod 0755 "$COLLISION_LOG"
printf 'directory sentinel\n' > "$COLLISION_LOG/existing-dir/sentinel"
printf 'file sentinel\n' > "$COLLISION_LOG/existing-file"
printf 'symlink sentinel\n' > "$TMP/symlink-target/sentinel"
ln -s "$TMP/symlink-target" "$COLLISION_LOG/existing-link"

printf 'must reject existing directory\n' | \
    AGY_TEST_LOG_DIR="$COLLISION_LOG" run_worker existing-dir \
    > "$TMP/existing-dir.out" 2>/dev/null
rc=$?
if [[ "$rc" == "64" ]] \
        && [[ "$(<"$COLLISION_LOG/existing-dir/sentinel")" == "directory sentinel" ]] \
        && [[ ! -e "$TMP/existing-dir.called" ]]; then
    ok "pre-existing job directory is rejected before invoking agy or touching its sentinel"
else
    bad "pre-existing job directory is rejected before invoking agy or touching its sentinel"
fi

printf 'must reject existing file\n' | \
    AGY_TEST_LOG_DIR="$COLLISION_LOG" run_worker existing-file \
    > "$TMP/existing-file.out" 2>/dev/null
rc=$?
if [[ "$rc" == "64" ]] \
        && [[ "$(<"$COLLISION_LOG/existing-file")" == "file sentinel" ]] \
        && [[ ! -e "$TMP/existing-file.called" ]]; then
    ok "pre-existing job file is rejected before invoking agy or touching its sentinel"
else
    bad "pre-existing job file is rejected before invoking agy or touching its sentinel"
fi

printf 'must reject existing symlink\n' | \
    AGY_TEST_LOG_DIR="$COLLISION_LOG" run_worker existing-link \
    > "$TMP/existing-link.out" 2>/dev/null
rc=$?
if [[ "$rc" == "64" ]] \
        && [[ "$(<"$TMP/symlink-target/sentinel")" == "symlink sentinel" ]] \
        && [[ ! -e "$TMP/existing-link.called" ]]; then
    ok "pre-existing job symlink is rejected before invoking agy or touching its target"
else
    bad "pre-existing job symlink is rejected before invoking agy or touching its target"
fi

printf 'must not edit\n' | run_worker readonly --mode accept-edits --persona diff-reviewer > "$TMP/readonly.out" 2>/dev/null
rc=$?
expect_exit "read-only persona rejects accept-edits" 64 "$rc"
printf 'alias must fail\n' | run_worker alias --mode accept-edits --persona ../agents/diff-reviewer > "$TMP/alias.out" 2>/dev/null
rc=$?
expect_exit "persona path alias is rejected" 64 "$rc"
printf 'unknown must fail\n' | run_worker unknown --persona unknown > "$TMP/unknown.out" 2>/dev/null
rc=$?
expect_exit "unknown persona is rejected" 64 "$rc"

printf 'broad audit is a usable default plan\n' | (
    unset AGY_WORKER_MODE
    PATH="$TMP/bin:$PATH" AGY_WORKER_LOG_DIR="$TMP/logs" \
        AGY_WORKER_JOB_ID=plan-without-persona \
        FAKE_MODEL_FILE="$TMP/plan-without-persona.model" \
        FAKE_PROMPT_FILE="$TMP/plan-without-persona.prompt" \
        FAKE_DIRS_FILE="$TMP/plan-without-persona.dirs" \
        FAKE_ARGV_FILE="$TMP/plan-without-persona.argv" \
        FAKE_STAGE_RESULT_FILE="$TMP/plan-without-persona.stage-result" \
        FAKE_CALLED_FILE="$TMP/plan-without-persona.called" \
        "$WORKER" --workdir "$TMP/repo"
) > "$TMP/plan-without-persona.out" 2> "$TMP/plan-without-persona.err"
rc=$?
if [[ "$rc" == "0" ]] \
        && [[ -s "$TMP/plan-without-persona.out" ]] \
        && [[ -e "$TMP/plan-without-persona.called" ]] \
        && [[ -d "$TMP/logs/plan-without-persona" ]]; then
    ok "generic plan dispatches without requiring a persona"
else
    bad "generic plan should remain usable without a persona"
fi

mkdir -p "$TMP/outside"
printf 'outside root\n' | run_worker outside --add-dir "$TMP/outside" > "$TMP/outside.out" 2>/dev/null
rc=$?
expect_exit "--add-dir outside audited workdir is rejected" 64 "$rc"

operand_index=0
for option in --workdir --persona --mode --tier --add-dir; do
    operand_index=$((operand_index+1))
    printf 'missing operand\n' | run_worker "missing-$operand_index" "$option" > "$TMP/missing-$operand_index.out" 2>/dev/null
    rc=$?
    expect_exit "$option missing operand is controlled" 64 "$rc"
done

python3 -c 'print("OVERSIZED_TASK_MARKER" + "x" * 100500)' > "$TMP/large-task.txt"
FAKE_TRY_STAGE_WRITE=1 run_worker oversized --mode accept-edits --persona bulk-test-writer \
    --add-dir "$TMP/repo" < "$TMP/large-task.txt" > "$TMP/oversized.out" 2>/dev/null
rc=$?
expect_exit "oversized persona job produces an envelope" 0 "$rc"
if grep -q 'OVERSIZED_TASK_MARKER' "$TMP/logs/oversized/full-prompt.txt" \
        && grep -q 'test author for a Codex-driven worker pipeline' "$TMP/logs/oversized/full-prompt.txt"; then
    ok "oversized staged prompt preserves task and persona"
else
    bad "oversized staged prompt preserves task and persona"
fi
if grep -Fxq "$LOGS_REAL/oversized/staged" "$TMP/oversized.dirs" \
        && ! grep -Fxq "$LOGS_REAL" "$TMP/oversized.dirs"; then
    ok "oversized job grants only its staged prompt directory"
else
    bad "oversized job grants only its staged prompt directory"
fi
if grep -Fq "$LOGS_REAL/oversized/staged/full-prompt.txt" "$TMP/oversized.prompt"; then
    ok "oversized dispatch points agy at the complete staged prompt"
else
    bad "oversized dispatch points agy at the complete staged prompt"
fi
if [[ "$(<"$TMP/oversized.stage-result")" == "blocked" ]]; then
    ok "oversized staged prompt is read-only during agy execution"
else
    bad "oversized staged prompt is read-only during agy execution"
fi
if private_tree_is_private "$TMP/logs/oversized"; then
    ok "oversized staged prompt is private again after agy returns"
else
    bad "oversized staged prompt is private again after agy returns"
fi
expect_print_last "oversized prompt keeps --print and its value last" "$TMP/oversized.argv"

WEAK_RESTORE_ROOT="$TMP/weak-restore"
mkdir -p "$WEAK_RESTORE_ROOT"
cp -R "$ROOT/skills/agy-worker" "$WEAK_RESTORE_ROOT/agy-worker"
python3 - "$WEAK_RESTORE_ROOT/agy-worker/runtime/scripts/agy_dispatch.py" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    source = handle.read()
replacements = (
    (
        'directory.chmod(0o555 if readonly else 0o700)',
        'directory.chmod(0o555 if readonly else 0o755)',
    ),
    (
        'source.chmod(0o444 if readonly else 0o600)',
        'source.chmod(0o444 if readonly else 0o644)',
    ),
)
for old, new in replacements:
    if source.count(old) != 1:
        raise SystemExit(f"expected exactly one restore statement: {old}")
    source = source.replace(old, new, 1)
with open(path, "w", encoding="utf-8") as handle:
    handle.write(source)
PY
WEAK_RESTORE_LOG="$TMP/weak-restore-log"
mkdir -p "$WEAK_RESTORE_LOG"
chmod 0755 "$WEAK_RESTORE_LOG"
AGY_TEST_WORKER="$WEAK_RESTORE_ROOT/agy-worker/runtime/agy-worker.sh" \
    AGY_TEST_LOG_DIR="$WEAK_RESTORE_LOG" \
    run_worker weak-restore < "$TMP/large-task.txt" \
    > "$TMP/weak-restore.out" 2>/dev/null
rc=$?
if [[ "$rc" == "20" && ! -s "$TMP/weak-restore.out" ]] \
        && ! private_tree_is_private "$WEAK_RESTORE_LOG/weak-restore" 2>/dev/null; then
    ok "supervisor fails closed when a mutation restores staged artifacts publicly"
else
    bad "privacy acceptance rejects a runtime that restores staged artifacts publicly"
fi

FAKE_EXIT_CODE=23 AGY_WORKER_MAX_ATTEMPTS=1 \
    run_worker staged-early-exit < "$TMP/large-task.txt" \
    > "$TMP/staged-early-exit.out" 2>/dev/null
rc=$?
if [[ "$rc" == "5" ]] && private_tree_is_private "$TMP/logs/staged-early-exit"; then
    ok "failed oversized child restores staged artifacts before the wrapper exits"
else
    bad "failed oversized child restores staged artifacts before the wrapper exits"
fi

signal_index=0
for signal_name in HUP INT TERM; do
    signal_index=$((signal_index+1))
    case "$signal_name" in
        HUP) expected_signal_rc=129 ;;
        INT) expected_signal_rc=130 ;;
        TERM) expected_signal_rc=143 ;;
    esac
    signal_job="staged-signal-$signal_index"
    FAKE_SIGNAL_PARENT="$signal_name" FAKE_EXIT_CODE=23 AGY_WORKER_MAX_ATTEMPTS=1 \
        run_worker "$signal_job" < "$TMP/large-task.txt" \
        > "$TMP/$signal_job.out" 2>/dev/null
    rc=$?
    if [[ "$rc" == "$expected_signal_rc" ]] \
            && private_tree_is_private "$TMP/logs/$signal_job"; then
        ok "$signal_name restores staged artifacts and preserves signal exit semantics"
    else
        bad "$signal_name restores staged artifacts and preserves signal exit semantics"
    fi
done

printf 'terminal failure\n' | PATH="$TMP/bin:$PATH" \
    AGY_WORKER_LOG_DIR="$TMP/logs" AGY_WORKER_JOB_ID=terminal \
    AGY_WORKER_MODE=accept-edits AGY_WORKER_MAX_ATTEMPTS=1 FAKE_AGY_STATUS=FAILED \
    FAKE_MODEL_FILE="$TMP/terminal.model" FAKE_PROMPT_FILE="$TMP/terminal.prompt" \
    FAKE_DIRS_FILE="$TMP/terminal.dirs" \
    FAKE_ARGV_FILE="$TMP/terminal.argv" FAKE_STAGE_RESULT_FILE="$TMP/terminal.stage-result" \
    "$WORKER" --workdir "$TMP/repo" > "$TMP/terminal.out" 2>/dev/null
rc=$?
expect_exit "non-success terminal status fails closed" 4 "$rc"

printf 'bad envelope\n' | PATH="$TMP/bin:$PATH" \
    AGY_WORKER_LOG_DIR="$TMP/logs" AGY_WORKER_JOB_ID=bad-envelope \
    AGY_WORKER_MODE=accept-edits AGY_WORKER_MAX_ATTEMPTS=1 FAKE_BAD_ENVELOPE=1 \
    FAKE_MODEL_FILE="$TMP/bad.model" FAKE_PROMPT_FILE="$TMP/bad.prompt" \
    FAKE_DIRS_FILE="$TMP/bad.dirs" \
    FAKE_ARGV_FILE="$TMP/bad.argv" FAKE_STAGE_RESULT_FILE="$TMP/bad.stage-result" \
    "$WORKER" --workdir "$TMP/repo" > "$TMP/bad.out" 2>/dev/null
rc=$?
expect_exit "dispatcher independently rejects schema-invalid output" 4 "$rc"

echo
echo "progress-aware local dispatch lifecycle tests:"

status_sha() {
    python3 - "$1" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["state_sha256"])
PY
}
status_field() {
    python3 - "$1" "$2" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])
PY
}
wait_terminal() {
    local job="$1" state_file="$2" next_file index sha status
    next_file="$state_file.next"
    for (( index=0; index<40; index++ )); do
        status="$(status_field "$state_file" status)"
        case "$status" in succeeded|failed|cancelled|orphaned) return 0 ;; esac
        sha="$(status_sha "$state_file")"
        control_worker wait "$job" --after-state-sha "$sha" --timeout 1s > "$next_file" || return 1
        mv "$next_file" "$state_file"
    done
    return 1
}
control_worker() {
    local action="$1" job="$2"; shift 2
    PATH="$TMP/bin:$PATH" AGY_WORKER_LOG_DIR="$TMP/logs" \
        FAKE_MODEL_FILE="$TMP/$job.model" FAKE_PROMPT_FILE="$TMP/$job.prompt" \
        FAKE_DIRS_FILE="$TMP/$job.dirs" FAKE_ARGV_FILE="$TMP/$job.argv" \
        FAKE_STAGE_RESULT_FILE="$TMP/$job.stage-result" \
        FAKE_CALLS_FILE="$TMP/$job.calls" FAKE_WORKER_CALLS_FILE="$TMP/$job.worker-calls" \
    FAKE_DISPATCH_MODE="${FAKE_DISPATCH_MODE:-result}" \
        FAKE_HEARTBEAT_COUNT="${FAKE_HEARTBEAT_COUNT:-8}" \
        FAKE_HEARTBEAT_DELAY="${FAKE_HEARTBEAT_DELAY:-0.10}" \
        FAKE_HEARTBEAT_BARRIER_READY="${FAKE_HEARTBEAT_BARRIER_READY:-}" \
        FAKE_HEARTBEAT_BARRIER_RELEASE="${FAKE_HEARTBEAT_BARRIER_RELEASE:-}" \
        FAKE_HEARTBEAT_AFTER_FIRST_READY="${FAKE_HEARTBEAT_AFTER_FIRST_READY:-}" \
        FAKE_HEARTBEAT_AFTER_FIRST_RELEASE="${FAKE_HEARTBEAT_AFTER_FIRST_RELEASE:-}" \
        FAKE_WORKER_VERIFIED="${FAKE_WORKER_VERIFIED:-0}" \
        "$WORKER" "$action" --job-id "$job" "$@"
}
start_worker() {
    local job="$1" workdir; shift
    workdir="${AGY_TEST_WORKDIR:-$TMP/repo}"
    PATH="$TMP/bin:$PATH" AGY_WORKER_LOG_DIR="$TMP/logs" AGY_WORKER_JOB_ID="$job" \
        AGY_WORKER_MODE="${AGY_WORKER_MODE:-accept-edits}" \
        FAKE_MODEL_FILE="$TMP/$job.model" FAKE_PROMPT_FILE="$TMP/$job.prompt" \
        FAKE_DIRS_FILE="$TMP/$job.dirs" FAKE_ARGV_FILE="$TMP/$job.argv" \
        FAKE_STAGE_RESULT_FILE="$TMP/$job.stage-result" \
        FAKE_CALLS_FILE="$TMP/$job.calls" FAKE_WORKER_CALLS_FILE="$TMP/$job.worker-calls" \
        FAKE_DISPATCH_MODE="${FAKE_DISPATCH_MODE:-result}" \
        FAKE_HEARTBEAT_COUNT="${FAKE_HEARTBEAT_COUNT:-8}" \
        FAKE_HEARTBEAT_DELAY="${FAKE_HEARTBEAT_DELAY:-0.10}" \
        FAKE_HEARTBEAT_BARRIER_READY="${FAKE_HEARTBEAT_BARRIER_READY:-}" \
        FAKE_HEARTBEAT_BARRIER_RELEASE="${FAKE_HEARTBEAT_BARRIER_RELEASE:-}" \
        FAKE_HEARTBEAT_AFTER_FIRST_READY="${FAKE_HEARTBEAT_AFTER_FIRST_READY:-}" \
        FAKE_HEARTBEAT_AFTER_FIRST_RELEASE="${FAKE_HEARTBEAT_AFTER_FIRST_RELEASE:-}" \
        FAKE_WORKER_VERIFIED="${FAKE_WORKER_VERIFIED:-0}" \
        "${AGY_TEST_WORKER:-$WORKER}" start --workdir "$workdir" "$@"
}

partial_clone_repo="$TMP/partial-clone-repo"
git init -q "$partial_clone_repo"
git -C "$partial_clone_repo" config extensions.partialclone origin
git -C "$partial_clone_repo" config remote.origin.promisor true
printf 'partial clone must fail before queueing\n' | \
    AGY_TEST_WORKDIR="$partial_clone_repo" start_worker partial-clone-preflight \
        --workflow task > "$TMP/partial-clone-preflight.out" \
        2> "$TMP/partial-clone-preflight.err"
partial_clone_rc=$?
if [[ "$partial_clone_rc" == 64 \
        && ! -e "$TMP/logs/partial-clone-preflight/dispatch-state.json" \
        && ! -s "$TMP/partial-clone-preflight.worker-calls" ]] \
        && grep -Fqx \
            'agy-dispatch: partial/promisor Git clones are unsupported; use a full clone' \
            "$TMP/partial-clone-preflight.err"; then
    ok "partial/promisor clones fail synchronously before queueing or provider launch"
else
    bad "partial/promisor clone synchronous preflight"
fi

printf 'plan staged prompt marker\n' | run_worker plan-staged --mode plan --persona repo-inventory \
    > "$TMP/plan-staged.out" 2> "$TMP/plan-staged.err"
rc=$?
if [[ "$rc" == 0 ]] && python3 - "$TMP/plan-staged.argv" \
        "$TMP/logs/plan-staged/staged/full-prompt.txt" <<'PY'
import sys
argv = [item for item in open(sys.argv[1], "rb").read().split(b"\0") if item]
staged = open(sys.argv[2], encoding="utf-8").read()
assert b"--mode" in argv and argv[argv.index(b"--mode") + 1] == b"plan"
assert b"--disable-slash-commands" not in argv
assert b"Read '" in argv[-1]
assert "plan staged prompt marker" in staged
assert "read-only repository surveyor for a Codex-driven worker pipeline" in staged
PY
then
    ok "maintained-persona plan stages the complete prompt and leaves slash expansion available only for its fixed driver prompt"
else
    bad "plan staging/slash contract"
fi

printf 'heartbeat completes\n' | FAKE_DISPATCH_MODE=heartbeat-success \
    FAKE_HEARTBEAT_COUNT=8 FAKE_HEARTBEAT_DELAY=0.10 \
    run_worker heartbeat-success --idle-timeout 1s --hard-timeout 3s --max-runtime 4s \
    > "$TMP/heartbeat-success.out" 2> "$TMP/heartbeat-success.err"
rc=$?
if [[ "$rc" == 0 ]] && python3 - "$TMP/logs/heartbeat-success/dispatch-state.json" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["status"] == "succeeded"
assert state["progress_count"] >= 2
assert state["conversation_id"] == "fake-conversation-01"
PY
then
    ok "valid init and step updates renew the idle lease without changing hard limits"
else
    bad "valid stream heartbeats must complete below the hard deadline"
fi

printf 'idle must fail\n' | FAKE_DISPATCH_MODE=idle \
    run_worker idle-timeout --idle-timeout 1s --hard-timeout 3s --max-runtime 4s \
    > "$TMP/idle-timeout.out" 2> "$TMP/idle-timeout.err"
rc=$?
if [[ "$rc" == 9 ]] && [[ "$(status_field "$TMP/idle-timeout.err" reason)" == idle_timeout ]]; then
    ok "silent worker terminates at the independent idle deadline"
else
    bad "silent worker idle timeout classification"
fi

printf 'malformed heartbeat must not count\n' | FAKE_DISPATCH_MODE=malformed-heartbeat \
    run_worker malformed-heartbeat --idle-timeout 1s --hard-timeout 3s --max-runtime 4s \
    > "$TMP/malformed-heartbeat.out" 2> "$TMP/malformed-heartbeat.err"
rc=$?
if [[ "$rc" == 9 ]] && python3 - "$TMP/malformed-heartbeat.err" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["reason"] == "idle_timeout"
assert state["progress_count"] == 0
PY
then
    ok "malformed events never renew the idle lease"
else
    bad "malformed event heartbeat boundary"
fi

printf 'oversized heartbeat must not count\n' | FAKE_DISPATCH_MODE=oversized-heartbeat \
    run_worker oversized-heartbeat --idle-timeout 1s --hard-timeout 2s --max-runtime 3s \
    > "$TMP/oversized-heartbeat.out" 2> "$TMP/oversized-heartbeat.err"
rc=$?
if [[ "$rc" == 23 ]] && python3 - "$TMP/oversized-heartbeat.err" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["reason"] == "output_oversized"
assert state["progress_count"] == 0
PY
then
    ok "oversized stream events fail closed and never renew the idle lease"
else
    bad "oversized event heartbeat boundary"
fi

printf 'exit-zero empty output\n' | FAKE_DISPATCH_MODE=empty-success \
    run_worker empty-success > "$TMP/empty-success.out" 2> "$TMP/empty-success.err"
rc=$?
if [[ "$rc" == 3 && ! -s "$TMP/empty-success.out" \
        && "$(status_field "$TMP/empty-success.err" reason)" == empty_output ]]; then
    ok "worker rejects exit-zero empty provider output"
else
    bad "worker exit-zero empty-output classification"
fi

printf 'benign print-mode diagnostics\n' | \
    FAKE_WARNING_LINE='permission that headless mode cannot prompt for; file write reported failure after content was already written' \
    FAKE_UTF8_SUMMARY=1 run_worker benign-print-diagnostics \
    > "$TMP/benign-print-diagnostics.out" 2> "$TMP/benign-print-diagnostics.err"
rc=$?
if [[ "$rc" == 0 ]] && python3 - "$TMP/benign-print-diagnostics.out" \
        "$TMP/logs/benign-print-diagnostics/dispatch-state.json" <<'PY'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
state = json.load(open(sys.argv[2], encoding="utf-8"))
assert result["status"] == "completed"
assert result["summary"] == "café 😀"
assert state["status"] == "succeeded"
assert state["reason"] is None
PY
then
    ok "successful structured output ignores stderr diagnostics and preserves UTF-8"
else
    bad "structured-output diagnostic and UTF-8 boundary"
fi

for classified_case in authentication_text provider_text unknown_text; do
    case "$classified_case" in
        authentication_text) classified_line='authentication failed' ;;
        provider_text) classified_line='provider unavailable' ;;
        *) classified_line='unrecognized provider diagnostic' ;;
    esac
    printf 'classification %s\n' "$classified_case" | FAKE_EXIT_CODE=23 \
        FAKE_ERROR_LINE="$classified_line" run_worker "classification-$classified_case" \
        --idle-timeout 1s --hard-timeout 2s --max-runtime 3s \
        > "$TMP/classification-$classified_case.out" \
        2> "$TMP/classification-$classified_case.err"
    rc=$?
    if [[ "$rc" == 5 ]] \
            && [[ "$(status_field "$TMP/classification-$classified_case.err" reason)" == agy_failed_unclassified ]]; then
        ok "unproven $classified_case remains unclassified without free-form guessing"
    else
        bad "closed error classification for $classified_case"
    fi
done

printf 'observed quota terminal\n' | FAKE_VERSION_MODE=quota113 \
    FAKE_DISPATCH_MODE=quota-error FAKE_EXIT_CODE=23 \
    run_worker quota-terminal --literal-model claude-opus-4-6-thinking \
    > "$TMP/quota-terminal.out" 2> "$TMP/quota-terminal.err"
quota_rc=$?
sleep 1
control_worker status quota-terminal > "$TMP/quota-terminal.status"
quota_status_rc=$?
if [[ "$quota_rc" == 24 && "$quota_status_rc" == 0 \
        && ! -s "$TMP/quota-terminal.out" ]] && python3 - \
        "$TMP/quota-terminal.err" "$TMP/quota-terminal.status" \
        "$TMP/quota-terminal.calls" "$TMP/quota-terminal.model" <<'PY'
import json
import sys

first = json.load(open(sys.argv[1], encoding="utf-8"))
later = json.load(open(sys.argv[2], encoding="utf-8"))
calls = open(sys.argv[3], encoding="ascii").read().splitlines()
model = open(sys.argv[4], encoding="utf-8").read()
for value in (first, later):
    assert value["status"] == "failed"
    assert value["exit_code"] == 24
    assert value["reason"] == "provider_quota_exhausted"
    assert value["failure_stage"] == "missing_structured_output"
    assert value["resume_available"] is True
    assert "conversation_id" not in value
    assert "provider_retry_after_seconds" not in value
    assert "provider_retry_observed_epoch" not in value
assert 17510 <= first["retry_after_seconds"] <= 17514
assert 17508 <= later["retry_after_seconds"] < first["retry_after_seconds"]
assert calls == ["version", "worker"]
assert model == "claude-opus-4-6-thinking"
PY
then
    ok "exact agy 1.1.13 quota terminal gets exit 24, bounded countdown, and no automatic retry"
else
    bad "exact agy 1.1.13 quota terminal classification"
fi

printf 'same text wrong version\n' | FAKE_DISPATCH_MODE=quota-error FAKE_EXIT_CODE=23 \
    run_worker quota-wrong-version > "$TMP/quota-wrong-version.out" \
    2> "$TMP/quota-wrong-version.err"
quota_wrong_version_rc=$?
if [[ "$quota_wrong_version_rc" == 4 \
        && "$(status_field "$TMP/quota-wrong-version.err" reason)" == invalid_envelope \
        && "$(status_field "$TMP/quota-wrong-version.err" failure_stage)" == missing_structured_output ]]; then
    ok "unreviewed quota terminal without a report is an invalid-envelope failure"
else
    bad "version-bound quota terminal classification"
fi

printf 'altered quota prose\n' | FAKE_VERSION_MODE=quota113 \
    FAKE_DISPATCH_MODE=quota-error FAKE_EXIT_CODE=23 \
    FAKE_QUOTA_ERROR='quota reached; retry in 4h51m54s' \
    run_worker quota-altered --literal-model claude-opus-4-6-thinking \
    > "$TMP/quota-altered.out" 2> "$TMP/quota-altered.err"
quota_altered_rc=$?
if [[ "$quota_altered_rc" == 4 \
        && "$(status_field "$TMP/quota-altered.err" reason)" == invalid_envelope \
        && "$(status_field "$TMP/quota-altered.err" failure_stage)" == missing_structured_output ]]; then
    ok "free-form quota prose without a report is an invalid-envelope failure"
else
    bad "free-form quota prose classification boundary"
fi

printf 'quota duration outside bound\n' | FAKE_VERSION_MODE=quota113 \
    FAKE_DISPATCH_MODE=quota-error FAKE_EXIT_CODE=23 \
    FAKE_QUOTA_ERROR='rpc error: Individual quota reached. Contact your administrator to enable overages. Resets in 999h00m00s.' \
    run_worker quota-unbounded --literal-model claude-opus-4-6-thinking \
    > "$TMP/quota-unbounded.out" 2> "$TMP/quota-unbounded.err"
quota_unbounded_rc=$?
if [[ "$quota_unbounded_rc" == 24 ]] && python3 - "$TMP/quota-unbounded.err" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["reason"] == "provider_quota_exhausted"
assert value["retry_after_seconds"] is None
PY
then
    ok "out-of-range quota reset stays classified without publishing a false duration"
else
    bad "quota retry duration bound"
fi

if PYTHONDONTWRITEBYTECODE=1 python3 - \
        "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" "$TMP" <<'PY'
import copy
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("agy_dispatch_quota_contract", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = Path(sys.argv[2])
canonical = (
    "rpc error: Individual quota reached. Contact your administrator to enable "
    "overages. Resets in 4h51m54s."
)
result = {
    "conversation_id": "fake-conversation-01",
    "status": "ERROR",
    "response": "",
    "error": canonical,
    "duration_seconds": 1.0,
    "num_turns": 3,
    "json_schema": {},
    "usage": {},
}

def stream(name, value=result, *, init=True, duplicate=False, malformed=False):
    path = root / name
    rows = []
    if init:
        rows.append(json.dumps({"event": "init", "conversation_id": "fake-conversation-01", "init": {}}))
    rows.append(json.dumps({"event": "result", "result": value}, separators=(",", ":")))
    if duplicate:
        rows.append(rows[-1])
    if malformed:
        rows.insert(0, "{not-json")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path

for index, duration in enumerate(("4h51m54s", "4h51m53s", "4h50m17s")):
    value = copy.deepcopy(result)
    value["error"] = canonical.replace("4h51m54s", duration)
    classified = module._quota_terminal_failure(stream(f"quota-observed-{index}", value), "1.1.13")
    assert classified == ("provider_quota_exhausted", {
        "4h51m54s": 17514, "4h51m53s": 17513, "4h50m17s": 17417,
    }[duration])

altered = (
    canonical + " ", canonical.replace("Individual", "individual"),
    "prefix " + canonical, canonical + " suffix",
    canonical.replace("quota", "limit"),
    canonical.replace("quota", "QUOTA"),
    "rpc error: RESOURCE_EXHAUSTED 429 retry quota",
)
for index, error in enumerate(altered):
    value = copy.deepcopy(result); value["error"] = error
    assert module._quota_terminal_failure(stream(f"quota-altered-{index}", value), "1.1.13") is None

for index, mutate in enumerate((
    lambda value: value.pop("usage"),
    lambda value: value.__setitem__("extra", 1),
    lambda value: value.__setitem__("response", "not empty"),
    lambda value: value.__setitem__("error", {"code": 429}),
    lambda value: value.__setitem__("duration_seconds", "1"),
    lambda value: value.__setitem__("duration_seconds", float("nan")),
    lambda value: value.__setitem__("num_turns", True),
    lambda value: value.__setitem__("json_schema", []),
    lambda value: value.__setitem__("usage", []),
)):
    value = copy.deepcopy(result); mutate(value)
    assert module._quota_terminal_failure(stream(f"quota-shape-{index}", value), "1.1.13") is None

assert module._quota_terminal_failure(stream("quota-no-init", init=False), "1.1.13") is None
assert module._quota_terminal_failure(stream("quota-duplicate", duplicate=True), "1.1.13") is None
assert module._quota_terminal_failure(stream("quota-malformed", malformed=True), "1.1.13") is None
mismatch = copy.deepcopy(result); mismatch["conversation_id"] = "different-conversation"
assert module._quota_terminal_failure(stream("quota-conversation-mismatch", mismatch), "1.1.13") is None
duplicate_key = root / "quota-duplicate-key"
duplicate_key.write_text(
    '{"event":"init","init":{},"conversation_id":"fake-conversation-01"}\n'
    '{"event":"result","result":{"conversation_id":"fake-conversation-01",'
    '"status":"ERROR","status":"ERROR","response":"","error":'
    + json.dumps(canonical) + ',"duration_seconds":1.0,"num_turns":3,'
    '"json_schema":{},"usage":{}}}\n',
    encoding="utf-8",
)
assert module._quota_terminal_failure(duplicate_key, "1.1.13") is None

# Older command schemas normalize conservatively: their version text was not a
# runtime observation and therefore cannot authorize this classifier.
job = root / "quota-command-v2"
job.mkdir(mode=0o700)
command = {
    "schema_version": 2, "kind": "agy-worker-dispatch-command", "job_id": "quota-v2",
    "workdir": str(root), "argv": ["agy", "--print", "task"], "agy_version": "1.1.13",
    "idle_seconds": 1, "hard_seconds": 2, "max_seconds": 3, "notice_seconds": 1,
    "stage_dir": None, "stage_file": None, "child_umask": "022",
    "resume_prompt": "resume", "continue_prompt": "continue",
    "workflow": "legacy", "max_cycles": 1,
}
(job / module.COMMAND_NAME).write_bytes(module.canonical(command))
(job / module.COMMAND_NAME).chmod(0o600)
loaded, _raw, _identity = module.load_command(job)
assert loaded["agy_version_observed"] is False
for bad_version in ([], {}, "01.1.13", "1.1", "1.1.123456"):
    bad = copy.deepcopy(command); bad["agy_version"] = bad_version
    (job / module.COMMAND_NAME).write_bytes(module.canonical(bad))
    try:
        module.load_command(job)
    except module.DispatchError as exc:
        assert str(exc) == "dispatch agy version is invalid"
    else:
        raise AssertionError("invalid command agy version accepted")
state = json.loads((root / "logs" / "quota-terminal" / module.STATE_NAME).read_text(encoding="utf-8"))
state.pop("provider_retry_after_seconds")
state.pop("provider_retry_observed_epoch")
for field in module.STATE_V5_FIELDS:
    state.pop(field)
for field in {*module.STATE_V6_FIELDS, *module.STATE_V8_FIELDS, *module.STATE_V9_FIELDS}:
    state.pop(field)
state["schema_version"] = 3
state["phase"] = None
state["assurance"] = None
migrated = module.validate_state(state)
assert migrated["provider_retry_after_seconds"] is None
assert migrated["provider_retry_observed_epoch"] is None
PY
then
    ok "agy 1.1.13 quota classifier is exact-shape, exact-version, and legacy-command conservative"
else
    bad "quota terminal exact contract matrix"
fi

HARD_SIDE_EFFECT="$TMP/hard-side-effect"
printf 'hard limit must win\n' | FAKE_DISPATCH_MODE=heartbeat-forever \
    FAKE_SIDE_EFFECT_FILE="$HARD_SIDE_EFFECT" \
    run_worker hard-timeout --idle-timeout 1s --hard-timeout 3s --max-runtime 5s \
    > "$TMP/hard-timeout.out" 2> "$TMP/hard-timeout.err"
rc=$?
sleep 1
if [[ "$rc" == 16 && ! -e "$HARD_SIDE_EFFECT" ]] && python3 - "$TMP/hard-timeout.err" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["reason"] == "hard_deadline_exceeded"
assert state["limit_kind"] == "hard"
assert state["progress_count"] >= 2
PY
then
    ok "fresh heartbeats cannot exceed hard deadline and process-group descendants are reaped"
else
    bad "hard deadline/process-group boundary"
fi

FOREGROUND_SIGNAL_SIDE_EFFECT="$TMP/foreground-signal-side-effect"
printf 'foreground signal ownership\n' > "$TMP/foreground-signal.task"
PATH="$TMP/bin:$PATH" AGY_WORKER_LOG_DIR="$TMP/logs" AGY_WORKER_JOB_ID=foreground-signal \
    AGY_WORKER_MODE=accept-edits \
    FAKE_MODEL_FILE="$TMP/foreground-signal.model" \
    FAKE_PROMPT_FILE="$TMP/foreground-signal.prompt" \
    FAKE_DIRS_FILE="$TMP/foreground-signal.dirs" \
    FAKE_ARGV_FILE="$TMP/foreground-signal.argv" \
    FAKE_STAGE_RESULT_FILE="$TMP/foreground-signal.stage-result" \
    FAKE_CALLS_FILE="$TMP/foreground-signal.calls" \
    FAKE_WORKER_CALLS_FILE="$TMP/foreground-signal.worker-calls" \
    FAKE_VERSION_MODE=ready FAKE_DISPATCH_MODE=heartbeat-forever \
    FAKE_SIDE_EFFECT_FILE="$FOREGROUND_SIGNAL_SIDE_EFFECT" \
    "$WORKER" --workdir "$TMP/repo" --idle-timeout 2s --hard-timeout 4s --max-runtime 4s \
    < "$TMP/foreground-signal.task" > "$TMP/foreground-signal.out" \
    2> "$TMP/foreground-signal.err" &
foreground_wrapper=$!
for (( foreground_wait=0; foreground_wait<200; foreground_wait++ )); do
    [[ -s "$TMP/foreground-signal.worker-calls" ]] && break
    kill -0 "$foreground_wrapper" 2>/dev/null || break
    sleep 0.01
done
kill -TERM "$foreground_wrapper" 2>/dev/null || true
wait "$foreground_wrapper"
foreground_rc=$?
sleep 1
if [[ "$foreground_rc" == 143 && ! -e "$FOREGROUND_SIGNAL_SIDE_EFFECT" ]] \
        && python3 - "$TMP/logs/foreground-signal/dispatch-state.json" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["status"] == "cancelled"
assert state["reason"] == "interrupted"
assert state["exit_code"] == 143
PY
then
    ok "foreground TERM is forwarded through the controller and leaves no late process-group side effect"
else
    bad "foreground signal ownership/process-group cleanup"
fi

printf 'private-task-prompt-sentinel\n' | FAKE_DISPATCH_MODE=heartbeat-success \
    FAKE_WORKER_VERIFIED=1 \
    FAKE_HEARTBEAT_COUNT=12 FAKE_HEARTBEAT_DELAY=0.10 \
    start_worker async-success --idle-timeout 1s --hard-timeout 3s --max-runtime 3s \
    > "$TMP/async-success.start" 2> "$TMP/async-success.start.err"
rc=$?
control_worker status async-success > "$TMP/async-success.status"
status_rc=$?
async_sha="$(status_sha "$TMP/async-success.status")"
control_worker wait async-success --after-state-sha "$async_sha" --timeout 1s \
    > "$TMP/async-success.wait"
wait_rc=$?
terminal_wait_rc=64
result_rc=64
result_json_rc=64
result_text_rc=64
status_text_rc=64
wait_text_rc=64
if [[ "$wait_rc" == 0 ]] && wait_terminal async-success "$TMP/async-success.wait"; then
    terminal_wait_rc=0
    control_worker result async-success > "$TMP/async-success.result"
    result_rc=$?
    control_worker result async-success --format json > "$TMP/async-success.result-json"
    result_json_rc=$?
    control_worker result async-success --format text > "$TMP/async-success.result-text"
    result_text_rc=$?
    control_worker status async-success --format text > "$TMP/async-success.status-text"
    status_text_rc=$?
    async_terminal_sha="$(status_sha "$TMP/async-success.wait")"
    control_worker wait async-success --after-state-sha "$async_terminal_sha" \
        --timeout 1s --format text > "$TMP/async-success.wait-text"
    wait_text_rc=$?
fi
if [[ "$rc" == 0 && "$status_rc" == 0 && "$wait_rc" == 0 \
        && "$terminal_wait_rc" == 0 && "$result_rc" == 0 \
        && "$result_json_rc" == 0 && "$result_text_rc" == 0 \
        && "$status_text_rc" == 0 && "$wait_text_rc" == 0 ]] \
        && cmp -s "$TMP/async-success.result" "$TMP/async-success.result-json" \
        && python3 - "$TMP/async-success.status" "$TMP/async-success.wait" \
            "$TMP/async-success.result" "$TMP/async-success.result-text" \
            "$TMP/async-success.status-text" "$TMP/async-success.wait-text" "$TMP" <<'PY'
import json
import sys
first = json.load(open(sys.argv[1], encoding="utf-8"))
changed = json.load(open(sys.argv[2], encoding="utf-8"))
result = json.load(open(sys.argv[3], encoding="utf-8"))
assert first["status"] in {"queued", "running"}
assert first["next_action"] == "wait"
assert first["next_action_command"] == (
    '"$PIPELINE/agy-worker.sh" wait --job-id async-success --after-state-sha '
    + first["state_sha256"] + " --format text"
)
assert changed["state_sha256"] != first["state_sha256"] or changed["status"] == "succeeded"
assert changed["next_action"] == "result"
assert changed["next_action_command"] == '"$PIPELINE/agy-worker.sh" result --job-id async-success --format json'
assert result["status"] == "completed"
for path in sys.argv[4:7]:
    lines = open(path, encoding="utf-8").read().splitlines()
    assert len(lines) == 3
    assert lines[0] == "Provider attempt: succeeded; reason: none; failure stage: none; bound result available: yes; driver disposition: unreviewed."
    assert lines[1] == f"Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: {changed['cycle']}/{changed['max_cycles']}."
    assert lines[2] == 'Next safe action: retrieve current bound result JSON with "$PIPELINE/agy-worker.sh" result --job-id async-success --format json; review it and run driver checks, construct Verification v2, then no further driver decision is currently listed.'
    text = "\n".join(lines)
    for sentinel in (
        "private-task-prompt-sentinel", "Verified private-worker-prose-sentinel",
        "fake-conversation-01", sys.argv[7],
    ):
        assert sentinel not in text
PY
then
    ok "status/wait/result formats preserve JSON and expose three sanitized driver-owned text lines"
else
    bad "async status/wait/result format and privacy lifecycle"
fi

startup_stress=0
for (( startup_index=1; startup_index<=12; startup_index++ )); do
    startup_job="startup-race-$startup_index"
    printf 'fast startup race\n' | FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=1 \
        FAKE_HEARTBEAT_DELAY=0.01 start_worker "$startup_job" --idle-timeout 1s \
        --hard-timeout 3s --max-runtime 4s > "$TMP/$startup_job.start" \
        2> "$TMP/$startup_job.err" || { startup_stress=1; break; }
    wait_terminal "$startup_job" "$TMP/$startup_job.start" || { startup_stress=1; break; }
    if ! python3 - "$TMP/$startup_job.start" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["status"] == "succeeded"
PY
    then startup_stress=1; break; fi
done
if [[ "$startup_stress" == 0 ]]; then
    ok "fast controller ownership handoff is stable without a competing parent PID transition"
else
    bad "fast controller ownership handoff race"
fi

extend_after_first_ready="$TMP/extend-active.after-first-ready"
extend_after_first_release="$TMP/extend-active.after-first-release"
printf 'extend active deadline\n' | FAKE_DISPATCH_MODE=heartbeat-success \
    FAKE_HEARTBEAT_COUNT=2 FAKE_HEARTBEAT_DELAY=1.00 \
    FAKE_HEARTBEAT_AFTER_FIRST_READY="$extend_after_first_ready" \
    FAKE_HEARTBEAT_AFTER_FIRST_RELEASE="$extend_after_first_release" \
    start_worker extend-active --idle-timeout 2s --hard-timeout 3s --max-runtime 5s \
    > "$TMP/extend-active.start" 2> "$TMP/extend-active.start.err"
extend_ready=0
for (( extend_index=0; extend_index<200; extend_index++ )); do
    control_worker status extend-active > "$TMP/extend-active.status"
    if [[ -e "$extend_after_first_ready" \
            && "$(status_field "$TMP/extend-active.status" status)" == "running" \
            && "$(status_field "$TMP/extend-active.status" progress_count)" -ge 1 ]]; then
        extend_ready=1
        break
    fi
    sleep 0.01
done
extend_sha="$(status_sha "$TMP/extend-active.status")"
control_worker extend extend-active --approve-state-sha "$(printf '0%.0s' {1..64})" --by 1s \
    > "$TMP/extend-active.stale" 2>&1
stale_extend_rc=$?
control_worker extend extend-active --approve-state-sha "$extend_sha" --by 3s \
    > "$TMP/extend-active.over-max" 2>&1
over_max_rc=$?
control_worker extend extend-active --approve-state-sha "$extend_sha" --by 1s \
    > "$TMP/extend-active.extend"
extend_rc=$?
: > "$extend_after_first_release"
wait_terminal extend-active "$TMP/extend-active.extend"
extend_terminal_rc=$?
control_worker result extend-active > "$TMP/extend-active.result"
result_rc=$?
if [[ "$extend_ready" == 1 && "$stale_extend_rc" == 64 \
        && "$over_max_rc" == 64 && "$extend_rc" == 0 \
        && "$extend_terminal_rc" == 0 && "$result_rc" == 0 ]] \
        && grep -Fqx 'agy-dispatch: deadline extension exceeds max runtime' "$TMP/extend-active.over-max" \
        && python3 - \
        "$TMP/extend-active.extend" "$TMP/extend-active.result" <<'PY'
import json
import sys
extended = json.load(open(sys.argv[1], encoding="utf-8"))
result = json.load(open(sys.argv[2], encoding="utf-8"))
assert extended["hard_seconds"] == 4.0
assert extended["max_seconds"] == 5.0
assert result["status"] == "completed"
PY
then
    ok "stale/over-max extend is rejected; fresh state-SHA extend changes only the local hard deadline"
else
    bad "extend control lifecycle"
fi

max_barrier_ready="$TMP/max-runtime.barrier-ready"
max_barrier_release="$TMP/max-runtime.barrier-release"
printf 'max runtime wins after extension\n' | FAKE_DISPATCH_MODE=heartbeat-forever \
    FAKE_HEARTBEAT_DELAY=0.60 \
    FAKE_HEARTBEAT_BARRIER_READY="$max_barrier_ready" \
    FAKE_HEARTBEAT_BARRIER_RELEASE="$max_barrier_release" \
    start_worker max-runtime --idle-timeout 3s --hard-timeout 3s --max-runtime 4s \
    > "$TMP/max-runtime.start" 2> "$TMP/max-runtime.start.err"
max_barrier_observed=0
for (( max_barrier_index=0; max_barrier_index<200; max_barrier_index++ )); do
    if [[ -e "$max_barrier_ready" ]]; then
        control_worker status max-runtime > "$TMP/max-runtime.status"
        if [[ "$(status_field "$TMP/max-runtime.status" progress_count)" -ge 1 ]]; then
            max_barrier_observed=1
            break
        fi
    fi
    sleep 0.01
done
max_extend_rc=64
max_wait_rc=64
if [[ "$max_barrier_observed" == 1 ]]; then
    max_sha="$(status_sha "$TMP/max-runtime.status")"
    control_worker extend max-runtime --approve-state-sha "$max_sha" --by 1s \
        > "$TMP/max-runtime.extend"
    max_extend_rc=$?
fi
: > "$max_barrier_release"
if [[ "$max_extend_rc" == 0 ]]; then
    max_after="$(status_sha "$TMP/max-runtime.extend")"
    control_worker wait max-runtime --after-state-sha "$max_after" --timeout 5s \
        > "$TMP/max-runtime.wait"
    max_wait_rc=$?
fi
for (( max_wait_index=0; max_wait_index<20; max_wait_index++ )); do
    [[ "$max_wait_rc" == 0 ]] || break
    max_status="$(status_field "$TMP/max-runtime.wait" status)"
    [[ "$max_status" == "running" || "$max_status" == "cancel-requested" ]] || break
    max_after="$(status_sha "$TMP/max-runtime.wait")"
    control_worker wait max-runtime --after-state-sha "$max_after" --timeout 1s \
        > "$TMP/max-runtime.wait.next"
    max_wait_rc=$?
    mv "$TMP/max-runtime.wait.next" "$TMP/max-runtime.wait"
done
if [[ "$max_extend_rc" == 0 && "$max_wait_rc" == 0 ]] && python3 - "$TMP/max-runtime.wait" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["status"] == "failed"
assert state["reason"] == "hard_deadline_exceeded"
assert state["limit_kind"] == "max-runtime"
assert state["hard_seconds"] == 4.0 == state["max_seconds"]
PY
then
    ok "max runtime remains an absolute cap even after an approved hard-deadline extension"
else
    bad "max runtime cap after extension"
fi

cancel_barrier_ready="$TMP/cancel-active.barrier-ready"
cancel_barrier_release="$TMP/cancel-active.barrier-release"
printf 'cancel active job\n' | FAKE_DISPATCH_MODE=heartbeat-forever \
    FAKE_HEARTBEAT_DELAY=0.40 \
    FAKE_HEARTBEAT_BARRIER_READY="$cancel_barrier_ready" \
    FAKE_HEARTBEAT_BARRIER_RELEASE="$cancel_barrier_release" \
    start_worker cancel-active --idle-timeout 1s --hard-timeout 3s --max-runtime 3s \
    > "$TMP/cancel-active.start" 2> "$TMP/cancel-active.start.err"
cancel_barrier_observed=0
for (( cancel_barrier_index=0; cancel_barrier_index<200; cancel_barrier_index++ )); do
    if [[ -e "$cancel_barrier_ready" ]]; then
        control_worker status cancel-active > "$TMP/cancel-active.status"
        if [[ "$(status_field "$TMP/cancel-active.status" progress_count)" -ge 1 ]]; then
            cancel_barrier_observed=1
            break
        fi
    fi
    sleep 0.01
done
cancel_a_rc=64
cancel_b_rc=64
if [[ "$cancel_barrier_observed" == 1 ]]; then
    cancel_sha="$(status_sha "$TMP/cancel-active.status")"
    control_worker cancel cancel-active --approve-state-sha "$cancel_sha" \
        > "$TMP/cancel-active.cancel-a" 2> "$TMP/cancel-active.cancel-a.err" &
    cancel_a_pid=$!
    control_worker cancel cancel-active --approve-state-sha "$cancel_sha" \
        > "$TMP/cancel-active.cancel-b" 2> "$TMP/cancel-active.cancel-b.err" &
    cancel_b_pid=$!
    wait "$cancel_a_pid"; cancel_a_rc=$?
    wait "$cancel_b_pid"; cancel_b_rc=$?
fi
: > "$cancel_barrier_release"
cancel_rc=64
cancel_file="$TMP/cancel-active.cancel-a"
if [[ "$cancel_a_rc" == 0 && "$cancel_b_rc" == 64 ]]; then
    cancel_rc=0
elif [[ "$cancel_b_rc" == 0 && "$cancel_a_rc" == 64 ]]; then
    cancel_rc=0
    cancel_file="$TMP/cancel-active.cancel-b"
fi
wait_rc=64
if [[ "$cancel_rc" == 0 ]]; then
    cancel_after="$(status_sha "$cancel_file")"
    control_worker wait cancel-active --after-state-sha "$cancel_after" --timeout 2s \
        > "$TMP/cancel-active.wait"
    wait_rc=$?
    # A successful wait may observe the approved cancel-requested transition
    # before the controller reaps its local process group and terminalizes.
    # Follow the current SHA through the existing fixed bound; do not accept a
    # non-terminal state if that terminal transition never arrives.
    if [[ "$wait_rc" == 0 ]]; then
        wait_terminal cancel-active "$TMP/cancel-active.wait"
        wait_rc=$?
    fi
fi
if [[ "$cancel_rc" == 0 && "$wait_rc" == 0 ]] && python3 - "$TMP/cancel-active.wait" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["status"] == "cancelled"
assert state["reason"] == "cancelled"
assert state["remote_cancel_unverified"] is True
assert state["candidate_recognized"] is False
assert state["result_available"] is False
assert state["driver_disposition"] == "not_applicable"
# A no-candidate local cancel must terminalize without the potentially slow
# repository reconciliation that follows a report-bearing completion.
assert state["worktree_reconciliation"] == "unavailable"
assert state["worktree_changes_present"] is None
assert state["worktree_changed_since_dispatch"] is None
PY
then
    ok "concurrent cancel is state-SHA-gated, reaps the local process group, and does not claim remote cancellation"
else
    bad "cancel control lifecycle"
fi

PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" \
        "$TMP/queued-cancel-job" "$TMP/repo" <<'PY'
import fcntl
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

source, job_text, workdir = sys.argv[1:]
spec = importlib.util.spec_from_file_location("agy_dispatch_queued_cancel", source)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
job = Path(job_text).resolve()
job.mkdir(mode=0o700)
command = {
    "schema_version": 1, "kind": "agy-worker-dispatch-command",
    "job_id": "queued-cancel", "workdir": workdir,
    "argv": ["agy", "--sandbox", "--mode", "plan", "--print-timeout", "4s",
             "--output-format", "stream-json", "--json-schema", str(Path(source).parent.parent / "schemas/worker-result.schema.json"),
             "--print", "must not dispatch"],
    "agy_version": "1.1.16", "idle_seconds": 1, "hard_seconds": 2,
    "max_seconds": 4, "notice_seconds": 3, "stage_dir": None,
    "stage_file": None, "child_umask": "022", "resume_prompt": "continue",
}
module.write_atomic(job, module.COMMAND_NAME, command)
lock_path = job / module.LOCK_NAME
lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
os.fchmod(lock_fd, 0o600)
fcntl.flock(lock_fd, fcntl.LOCK_EX)
state, sha = module.create_state(job, "initial", resume=False)
module.command_control(job, "cancel", sha, None)
child = subprocess.Popen(
    [sys.executable, "-I", "-S", "-B", source, "controller", "--job-dir", str(job),
     "--ownership-fd", str(lock_fd)], pass_fds=(lock_fd,), stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
os.close(lock_fd)
assert child.wait(timeout=5) == module.EXIT_BY_REASON["cancelled"]
terminal, _, _ = module.load_state(job)
assert terminal["status"] == "cancelled"
assert terminal["reason"] == "cancelled"
assert terminal["remote_cancel_unverified"] is False
assert not (job / "stream.ndjson").exists()
PY
queued_cancel_rc=$?
if [[ "$queued_cancel_rc" == 0 ]]; then
    ok "queued cancel is consumed by inherited controller ownership without starting agy"
else
    bad "queued cancel/start ownership handoff"
fi

PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" \
        "$TMP/state-snapshot-job" <<'PY'
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

source, job_text = sys.argv[1:]
spec = importlib.util.spec_from_file_location("agy_dispatch_state_snapshot", source)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
job = Path(job_text).resolve()
job.mkdir(mode=0o700)
workdir = job.parent / "state-snapshot-workdir"
workdir.mkdir(mode=0o700)
subprocess.run(["git", "init", "-q", str(workdir)], check=True)
command = {
    "job_id": "state-snapshot", "workdir": str(workdir),
    "idle_seconds": 1.0, "hard_seconds": 2.0, "max_seconds": 3.0,
    "workflow": "legacy", "max_cycles": 1,
}
state = module.initial_state(
    command, "initial", 1, command_sha="0" * 64,
    command_identity=(1, 1, os.getuid(), os.getgid(), 0o600),
    stage_sha=None, stage_identity=None,
)
assert state["schema_version"] == module.CURRENT_STATE_SCHEMA == 9
assert state["worktree_root_identity"] is not None
assert state["worktree_baseline"] is not None
assert state["worktree_snapshot_algorithm"] == module.WORKTREE_SNAPSHOT_SEMANTIC_V1
module.write_atomic(job, module.STATE_NAME, state)
done = job / "writer.done"
blocked = job / "writer.blocked"
acquired = job / "writer.acquired"
writer_source = r'''
import fcntl
import importlib.util
import os
from pathlib import Path
import sys

source, job_text, done_text, blocked_text, acquired_text = sys.argv[1:]
spec = importlib.util.spec_from_file_location("agy_dispatch_state_writer", source)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
job = Path(job_text)
lock_fd = os.open(
    job / module.STATE_LOCK_NAME,
    os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
os.fchmod(lock_fd, 0o600)
try:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        Path(blocked_text).touch(mode=0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    else:
        Path(acquired_text).touch(mode=0o600)
    state, raw, _sha = module.load_state(job)
    module._transition_locked(job, state, raw, {
        "notice_count": state["notice_count"] + 1,
    })
finally:
    os.close(lock_fd)
Path(done_text).touch(mode=0o600)
'''
original_lstat = Path.lstat
child = None
writer_was_blocked = False

def replace_during_identity_check(path: Path):
    global child, writer_was_blocked
    if path == job / module.STATE_NAME and child is None:
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", writer_source,
             source, str(job), str(done), str(blocked), str(acquired)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 2.0
        while not blocked.exists() and not acquired.exists() \
                and time.monotonic() < deadline:
            time.sleep(0.005)
        assert blocked.exists() != acquired.exists()
        writer_was_blocked = blocked.exists()
        if not writer_was_blocked:
            child.wait(timeout=3)
            assert done.exists()
    return original_lstat(path)

Path.lstat = replace_during_identity_check
try:
    snapshot, raw, sha = module.read_state_snapshot(job)
finally:
    Path.lstat = original_lstat
assert child is not None and child.wait(timeout=3) == 0
terminal, terminal_raw, terminal_sha = module.read_state_snapshot(job)
assert writer_was_blocked
assert snapshot["sequence"] == 1 and sha == module.digest(raw)
assert terminal["sequence"] == 2
assert terminal["previous_state_sha256"] == sha
assert terminal_sha == module.digest(terminal_raw)
PY
state_snapshot_rc=$?
if [[ "$state_snapshot_rc" == 0 ]]; then
    ok "state snapshot serializes an approved atomic replacement without weakening identity checks"
else
    bad "state snapshot atomic-replacement identity boundary"
fi

printf 'orphan fixture\n' | FAKE_EXIT_CODE=23 \
    run_worker orphan-no-resume --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
    > "$TMP/orphan-no-resume.out" 2> "$TMP/orphan-no-resume.err"
ORPHAN_JOB="$TMP/logs/orphan-no-resume"
rm -f "$ORPHAN_JOB/dispatch-state.json"
rm -f "$ORPHAN_JOB/stream.ndjson" "$ORPHAN_JOB/stderr.txt"
PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" \
        "$ORPHAN_JOB" <<'PY'
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("agy_dispatch_orphan", sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
job = Path(sys.argv[2])
command, raw, identity = module.load_command(job)
state = module.initial_state(
    command, "initial", 1, command_sha=module.digest(raw),
    command_identity=identity, stage_sha=None, stage_identity=None,
)
state.update({
    "status": "orphaned", "reason": "status_unavailable", "exit_code": 20,
    "finished_epoch": 1.0, "conversation_id": "fake-conversation-01",
    "resume_available": False,
})
module.write_atomic(job, module.STATE_NAME, state)
PY
orphan_sha="$(PYTHONDONTWRITEBYTECODE=1 python3 - "$ORPHAN_JOB/dispatch-state.json" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
orphan_calls_before="$(wc -l < "$TMP/orphan-no-resume.calls" | tr -d ' ')"
control_worker resume orphan-no-resume --approve-state-sha "$orphan_sha" >/dev/null 2>&1
orphan_resume_rc=$?
control_worker restart orphan-no-resume --approve-state-sha "$orphan_sha" >/dev/null 2>&1
orphan_restart_rc=$?
orphan_calls_after="$(wc -l < "$TMP/orphan-no-resume.calls" | tr -d ' ')"
if [[ "$orphan_resume_rc" == 21 && "$orphan_restart_rc" == 64 \
        && "$orphan_calls_before" == "$orphan_calls_after" ]]; then
    ok "orphaned dispatch is preserve-only and cannot resume or restart a provider call"
else
    bad "orphaned dispatch continuation boundary"
fi

printf 'private-resume-task-sentinel\n' | FAKE_DISPATCH_MODE=conversation-fail \
    run_worker resume-case --idle-timeout 1s --hard-timeout 4s --max-runtime 8s \
    > "$TMP/resume-case.out" 2> "$TMP/resume-case.err"
rc=$?
resume_sha="$(status_sha "$TMP/resume-case.err")"
FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
    control_worker resume resume-case --approve-state-sha "$resume_sha" --format text \
    > "$TMP/resume-case.resume"
resume_rc=$?
control_worker status resume-case > "$TMP/resume-case.resume-status"
wait_terminal resume-case "$TMP/resume-case.resume-status"
resume_wait_rc=$?
control_worker result resume-case > "$TMP/resume-case.result"
result_rc=$?
if [[ "$rc" == 4 && "$resume_rc" == 0 && "$resume_wait_rc" == 0 && "$result_rc" == 0 ]] && python3 - \
        "$TMP/resume-case.argv" "$TMP/resume-case.resume-status" \
        "$TMP/resume-case.resume" "$TMP/resume-case.err" "$TMP" <<'PY'
import json
import sys
argv = [item for item in open(sys.argv[1], "rb").read().split(b"\0") if item]
state = json.load(open(sys.argv[2], encoding="utf-8"))
assert argv.count(b"--conversation") == 1
assert argv[argv.index(b"--conversation") + 1] == b"fake-conversation-01"
assert b"Continue the existing bounded task" in argv[-1]
assert state["attempt_origin"] == "conversation-resume"
assert state["attempt"] == 2
assert state["next_action"] == "result"
assert state["next_action_command"] == '"$PIPELINE/agy-worker.sh" result --job-id resume-case --format json'
text = open(sys.argv[3], encoding="utf-8").read()
assert len(text.splitlines()) == 3
failed = json.load(open(sys.argv[4], encoding="utf-8"))
assert failed["next_action"] == "none"
assert failed["next_action_command"] is None
assert [item["action"] for item in failed["available_actions"]] == ["resume", "restart"]
assert failed["available_actions"][0]["command"] == (
    '"$PIPELINE/agy-worker.sh" resume --job-id resume-case --approve-state-sha '
    + failed["state_sha256"] + " --format text"
)
assert failed["available_actions"][1]["command"] == (
    '"$PIPELINE/agy-worker.sh" restart --job-id resume-case --approve-state-sha '
    + failed["state_sha256"] + " --format text"
)
for sentinel in ("private-resume-task-sentinel", "fake-conversation-01", sys.argv[5]):
    assert sentinel not in text
PY
then
    ok "resume text succeeds with the exact private conversation and no private output"
else
    bad "conversation resume format and privacy contract"
fi

printf 'restart without conversation\n' | FAKE_DISPATCH_MODE=conversation-fail \
    run_worker restart-case --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
    > "$TMP/restart-case.out" 2> "$TMP/restart-case.err"
restart_sha="$(status_sha "$TMP/restart-case.err")"
FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
    control_worker restart restart-case --approve-state-sha "$restart_sha" \
    > "$TMP/restart-case.restart"
restart_rc=$?
wait_terminal restart-case "$TMP/restart-case.restart"
restart_wait_rc=$?
control_worker result restart-case > "$TMP/restart-case.result"
result_rc=$?
if [[ "$restart_rc" == 0 && "$restart_wait_rc" == 0 && "$result_rc" == 0 ]] && python3 - \
        "$TMP/restart-case.argv" "$TMP/restart-case.restart" <<'PY'
import json
import sys
argv = [item for item in open(sys.argv[1], "rb").read().split(b"\0") if item]
state = json.load(open(sys.argv[2], encoding="utf-8"))
assert b"--conversation" not in argv
assert state["attempt_origin"] == "fresh-restart"
assert state["attempt"] == 2
PY
then
    ok "restart is explicit fresh origin and remains distinct from resume"
else
    bad "fresh restart contract"
fi

printf 'resume unavailable\n' | FAKE_EXIT_CODE=23 \
    run_worker resume-unavailable --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
    > "$TMP/resume-unavailable.out" 2> "$TMP/resume-unavailable.err"
unavailable_sha="$(status_sha "$TMP/resume-unavailable.err")"
FAKE_DISPATCH_MODE=heartbeat-success control_worker resume resume-unavailable \
    --approve-state-sha "$unavailable_sha" > "$TMP/resume-unavailable.resume" 2>&1
resume_rc=$?
resume_unavailable_calls="$(wc -l < "$TMP/resume-unavailable.calls" | tr -d ' ')"
if [[ "$resume_rc" == 21 && "$resume_unavailable_calls" == 1 ]]; then
    resume_unavailable_ok=1
else
    resume_unavailable_ok=0
fi

project_feedback() {
    python3 - "$TMP/logs/$1/dispatch-state.json" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"schema_version": 2, "summary": "driver checks found one repairable failure",
    "passed_checks": ["lint"], "failed_checks": ["unit-tests"], "advisory_checks": 0,
    "missing_checks": 0, "candidate_sha256": state["result_sha256"], "coverage": "partial",
    "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": True}, separators=(",", ":")))
PY
}
project_verified_feedback() {
    python3 - "$TMP/logs/$1/dispatch-state.json" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"schema_version": 2, "summary": "driver checks passed",
    "passed_checks": ["lint", "unit-tests"], "failed_checks": [], "advisory_checks": 0,
    "missing_checks": 0, "candidate_sha256": state["result_sha256"], "coverage": "complete",
    "verified_findings": 0, "unresolved_gaps": 0, "diff_review_complete": True}, separators=(",", ":")))
PY
}
project_missing_feedback() {
    python3 - "$TMP/logs/$1/dispatch-state.json" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"schema_version": 2, "summary": "one required driver check is missing",
    "passed_checks": ["lint"], "failed_checks": [], "advisory_checks": 0,
    "missing_checks": 1, "candidate_sha256": state["result_sha256"], "coverage": "partial",
    "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": True}, separators=(",", ":")))
PY
}

if PYTHONDONTWRITEBYTECODE=1 python3 -B - \
        "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" "$TMP" <<'PY'
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

source = Path(sys.argv[1]).resolve()
root = Path(sys.argv[2]).resolve()
spec = importlib.util.spec_from_file_location("agy_dispatch_v2_continue_parity", source)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

repo = root / "historical-v2-direct-repo"; repo.mkdir()
subprocess.run(["git", "init", "-q", str(repo)], check=True)
job = (root / "historical-v2-direct-job"); job.mkdir(mode=0o700); job = job.resolve()
provider_schema = source.parent.parent / "schemas" / "worker-result.provider.schema.json"
matrix, matrix_sha, matrix_version, matrix_revision = module.MODEL_SELECTION.load_policy()
resolved_model, selection_mode = module.MODEL_SELECTION.resolve_model(
    matrix, "gemini-3.7-flash", "high",
)
selection = {
    "schema_version": 2, "kind": "agy-worker-selection", "selection_mode": selection_mode,
    "user_model": "gemini-3.7-flash", "user_model_source": "cli",
    "user_effort": "high", "user_effort_source": "cli",
    "resolved_agy_model": resolved_model,
    "installed_agy_version": "9.9.9",
    "matrix_sha256": matrix_sha, "matrix_agy_version": matrix_version,
    "matrix_source_revision": matrix_revision, "version_relation": "drift",
    "compatibility_status": "critical-interface-compatible-version-drift",
    "critical_interface_probe_version": 1, "critical_interface_status": "compatible",
    "critical_capabilities_sha256": "a" * 64, "help_sha256": "b" * 64,
    "model_availability": "not_assessed",
    "probed_executable": {
        "path_sha256": "c" * 64,
        "target_lstat": {
            "device": 1, "inode": 1, "mode": stat.S_IFREG | 0o755,
            "uid": os.geteuid(), "gid": os.getegid(), "size": 1,
            "mtime_ns": 1,
        },
        "symlink_chain": [], "components": [],
    },
}
module.MODEL_SELECTION.validate_selection_record(selection)
selection_path = job / "selection.json"
module.MODEL_SELECTION.publish_record(selection_path, selection)
selection_raw, selection_info = module.read_regular(
    selection_path, module.MAX_COMMAND_BYTES, "fixture selection",
)
command = {
    "schema_version": 4, "kind": "agy-worker-dispatch-command",
    "job_id": "historical-v2-direct", "workdir": str(repo),
    "argv": [
        "agy", "--json-schema", str(provider_schema), "--model", resolved_model,
        "--print", "task",
    ],
    "agy_version": matrix_version, "agy_version_observed": True,
    "selection_path": str(selection_path),
    "selection_sha256": module.digest(selection_raw),
    "selection_identity": list(module._identity(selection_info)),
    "idle_seconds": 2, "hard_seconds": 10, "max_seconds": 20, "notice_seconds": 3,
    "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
    "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
}
module.write_atomic(job, module.COMMAND_NAME, command)
state, _initial_sha = module.create_state(job, "initial", resume=False)
candidate = {
    "status": "completed", "summary": "historical-v2-direct-candidate",
    "files_changed": [], "commands_run": [], "tests_run": [], "risks": [],
    "open_questions": [], "confidence": 0.9, "requires_human": False,
}
envelope = job / "envelope.json"
candidate_raw = json.dumps(candidate, ensure_ascii=True, indent=2).encode("ascii") + b"\n"
envelope.write_bytes(candidate_raw); envelope.chmod(0o600)
_bound, envelope_info = module.read_regular(envelope, 1024 * 1024, "fixture envelope")
snapshot = module._worktree_snapshot(str(repo)); assert snapshot is not None
state.update({
    "status": "succeeded", "exit_code": 0, "finished_epoch": 1.0,
    "conversation_id": "historical-v2-conversation", "result_path": str(envelope),
    "result_sha256": module.digest(candidate_raw),
    "result_identity": list(module._identity(envelope_info)),
    "candidate_recognized": True, "candidate_source": "provider_success",
    "result_available": True, "candidate_worktree_sha256": snapshot["sha256"],
    "candidate_worktree_entries": snapshot["entries"],
    "driver_disposition": "unreviewed", "phase": "awaiting-verification",
    "assurance": "pending", "continue_available": True, "resume_available": False,
    "next_action": "driver_review",
})
_raw, sha = module.write_atomic(job, module.STATE_NAME, state)
public = module.public_status(state, sha, job=job)
assert [item["action"] for item in public["available_actions"]] == [
    "result", "verification-copy", "finalize",
]
verification = {
    "schema_version": 2, "summary": "driver found a bounded defect",
    "passed_checks": [], "failed_checks": ["fixture"],
    "advisory_checks": 0, "missing_checks": 0,
    "candidate_sha256": state["result_sha256"], "coverage": "partial",
    "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
}
before = (job / module.STATE_NAME).read_bytes()
delivered = subprocess.run(
    [sys.executable, str(source), "result", "--job-dir", str(job)],
    check=True, stdout=subprocess.PIPE,
)
assert json.loads(delivered.stdout)["summary"] == candidate["summary"]
assert (job / module.STATE_NAME).read_bytes() == before

marker = root / "historical-v2-direct-provider-called"
bin_dir = root / "historical-v2-direct-bin"; bin_dir.mkdir()
fake = bin_dir / "agy"
fake.write_text("#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nexit 99\n", encoding="utf-8")
fake.chmod(0o755)
environment = dict(os.environ); environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
rejected = subprocess.run(
    [sys.executable, str(source), "continue", "--job-dir", str(job),
     "--approve-state-sha", sha],
    input=json.dumps(verification).encode("utf-8"), env=environment,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
assert rejected.returncode == 64 and not rejected.stdout
assert b"dispatch direct selection lacks approved compatibility disposition" in rejected.stderr
assert (job / module.STATE_NAME).read_bytes() == before
assert not (job / "continue-staged").exists() and not marker.exists()
try:
    module.create_state(
        job, "conversation-continue", resume=True,
        approve_sha=sha, verification=verification,
    )
except module.DispatchError as exc:
    assert str(exc) == "dispatch direct selection lacks approved compatibility disposition"
else:
    raise AssertionError("historical V2 drift selection authorized a continuation")
assert (job / module.STATE_NAME).read_bytes() == before
assert not (job / "continue-staged").exists() and not marker.exists()

finalized = subprocess.run(
    [sys.executable, str(source), "finalize", "--job-dir", str(job),
     "--approve-state-sha", sha, "--assurance", "partially_verified"],
    input=json.dumps(verification).encode("utf-8"), check=True, stdout=subprocess.PIPE,
)
assert json.loads(finalized.stdout)["driver_disposition"] == "partially_verified"
assert not marker.exists()
PY
then
    historical_v2_parity_ok=1
else
    historical_v2_parity_ok=0
fi
if [[ "$resume_unavailable_ok" == 1 && "$historical_v2_parity_ok" == 1 ]]; then
    ok "resume and historical V2 drift recovery reject provider launch while preserving result/finalize"
else
    bad "resume or historical V2 drift recovery action parity"
fi

printf 'project workflow initial implementation\n' | FAKE_DISPATCH_MODE=heartbeat-success \
    FAKE_HEARTBEAT_COUNT=2 AGY_TEST_WORKDIR="$TMP/project-worktree" start_worker project-cycles --workflow project \
    --idle-timeout 1s --hard-timeout 2s --max-runtime 8s > "$TMP/project-cycles.start" 2> "$TMP/project-cycles.err"
project_start_rc=$?
wait_terminal project-cycles "$TMP/project-cycles.start"
project_wait_rc=$?
control_worker status project-cycles > "$TMP/project-cycles.status"
project_sha="$(status_sha "$TMP/project-cycles.status")"
if [[ "$project_start_rc" == 0 && "$project_wait_rc" == 0 ]] && python3 - "$TMP/project-cycles.status" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["workflow"] == "project"
assert value["status"] == "succeeded"
assert value["phase"] == "awaiting-verification"
assert value["cycle"] == 1 and value["max_cycles"] == 5
assert value["assurance"] is None
assert value["resume_available"] is False and value["continue_available"] is True
assert value["check_counts"] == {"passed": 0, "failed": 0, "advisory": 0, "missing": 0}
PY
then
    ok "project workflow exposes pending Codex-owned verification without changing resume semantics"
else
    bad "project workflow status contract"
fi

printf 'project missing verification repair\n' | FAKE_DISPATCH_MODE=heartbeat-success \
    FAKE_HEARTBEAT_COUNT=2 AGY_TEST_WORKDIR="$TMP/project-worktree" \
    start_worker project-missing-check --workflow project \
    --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
    > "$TMP/project-missing-check.start" 2> "$TMP/project-missing-check.err"
wait_terminal project-missing-check "$TMP/project-missing-check.start"
control_worker status project-missing-check > "$TMP/project-missing-check.status"
project_missing_sha="$(status_sha "$TMP/project-missing-check.status")"
project_missing_feedback project-missing-check | FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
    control_worker continue project-missing-check --approve-state-sha "$project_missing_sha" --format text \
    > "$TMP/project-missing-check.continue" 2> "$TMP/project-missing-check.continue.err"
project_missing_rc=$?
control_worker status project-missing-check > "$TMP/project-missing-check.status"
wait_terminal project-missing-check "$TMP/project-missing-check.status"
project_missing_wait_rc=$?
project_missing_calls="$(wc -l < "$TMP/project-missing-check.worker-calls" | tr -d ' ')"
if [[ "$project_missing_rc" == 0 && "$project_missing_wait_rc" == 0 \
        && "$project_missing_calls" == 2 ]] \
        && python3 - "$TMP/project-missing-check.status" \
            "$TMP/project-missing-check.continue" "$TMP" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["cycle"] == 2
assert value["attempt_origin"] == "conversation-continue"
assert value["check_counts"] == {"passed": 0, "failed": 0, "advisory": 0, "missing": 0}
assert value["next_action"] == "result"
assert value["next_action_command"] == '"$PIPELINE/agy-worker.sh" result --job-id project-missing-check --format json'
text = open(sys.argv[2], encoding="utf-8").read()
assert len(text.splitlines()) == 3
assert text.splitlines()[1] == "Driver evidence: 1 passed, 0 failed, 0 advisory, 1 missing; cycle: 2/5."
for sentinel in ("one required driver check is missing", "lint", "fake-conversation-01", sys.argv[3]):
    assert sentinel not in text
PY
then
    ok "project continuation text keeps trigger evidence while the replacement candidate resets stored evidence"
else
    bad "project continuation format and privacy contract"
fi

project_missing_sha="$(status_sha "$TMP/project-missing-check.status")"
project_missing_calls_before_finalize="$(wc -l < "$TMP/project-missing-check.worker-calls" | tr -d ' ')"
project_missing_feedback project-missing-check | control_worker finalize project-missing-check \
    --approve-state-sha "$project_missing_sha" --assurance partially-verified \
    > /dev/null 2>&1
project_hyphen_assurance_rc=$?
project_missing_feedback project-missing-check | control_worker finalize project-missing-check \
    --approve-state-sha "$project_missing_sha" --assurance partially_verified --format text \
    > "$TMP/project-missing-check.finalize" 2> "$TMP/project-missing-check.finalize.err"
project_partial_assurance_rc=$?
control_worker status project-missing-check > "$TMP/project-missing-check.finalized-status"
project_missing_calls_after_finalize="$(wc -l < "$TMP/project-missing-check.worker-calls" | tr -d ' ')"
if [[ "$project_hyphen_assurance_rc" == 64 && "$project_partial_assurance_rc" == 0 \
        && "$project_missing_calls_after_finalize" == "$project_missing_calls_before_finalize" ]] \
        && python3 - "$TMP/project-missing-check.finalized-status" \
            "$TMP/project-missing-check.finalize" "$TMP" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["phase"] == "completed"
assert value["assurance"] == "partially_verified"
assert value["check_counts"] == {"passed": 1, "failed": 0, "advisory": 0, "missing": 1}
assert value["next_action"] == "result"
assert value["next_action_command"] == '"$PIPELINE/agy-worker.sh" result --job-id project-missing-check --format json'
lines = open(sys.argv[2], encoding="utf-8").read().splitlines()
assert len(lines) == 3
assert lines[0] == "Provider attempt: succeeded; reason: none; failure stage: none; bound result available: yes; driver disposition: partially_verified."
assert lines[1] == f"Driver evidence: 1 passed, 0 failed, 0 advisory, 1 missing; cycle: {value['cycle']}/{value['max_cycles']}."
assert lines[2] == (
    'Next safe action: optional finalized result JSON readback with "$PIPELINE/agy-worker.sh" result --job-id project-missing-check --format json; '
    'driver disposition is already recorded; do not construct Verification v2, continue, or finalize. '
    'Available fresh restart command: "$PIPELINE/agy-worker.sh" restart --job-id project-missing-check --approve-state-sha '
    + value["state_sha256"] + " --format text."
)
for sentinel in ("one required driver check is missing", "lint", "fake-conversation-01", sys.argv[3]):
    assert sentinel not in "\n".join(lines)
PY
then
    ok "project finalization text is three-line, driver-owned, and invokes no provider"
else
    bad "project finalization format and assurance contract"
fi

project_cycle_ok=1
for project_cycle in 2 3 4 5; do
    project_feedback project-cycles | FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
        control_worker continue project-cycles --approve-state-sha "$project_sha" \
        > "$TMP/project-cycles.$project_cycle" 2> "$TMP/project-cycles.$project_cycle.err"
    project_continue_rc=$?
    wait_terminal project-cycles "$TMP/project-cycles.$project_cycle" || project_cycle_ok=0
    control_worker status project-cycles > "$TMP/project-cycles.status"
    project_sha="$(status_sha "$TMP/project-cycles.status")"
    [[ "$project_continue_rc" == 0 ]] || project_cycle_ok=0
done
project_feedback project-cycles | control_worker continue project-cycles --approve-state-sha "$project_sha" \
    > "$TMP/project-cycles.exhausted" 2> "$TMP/project-cycles.exhausted.err"
project_exhausted_rc=$?
project_calls="$(wc -l < "$TMP/project-cycles.calls" | tr -d ' ')"
if [[ "$project_cycle_ok" == 1 && "$project_exhausted_rc" == 64 && "$project_calls" == 5 ]] \
        && python3 - "$TMP/project-cycles.status" "$TMP/project-cycles.argv" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
argv = [item.decode() for item in open(sys.argv[2], "rb").read().split(b"\0") if item]
assert state["cycle"] == 5 and state["continue_available"] is False
assert argv.count("--conversation") == 1
assert "unit-tests" not in " ".join(argv)
assert "driver checks found one repairable failure" not in " ".join(argv)
PY
then
    ok "project continuation is same-conversation, feedback-private, and bounded to initial plus four repairs"
else
    bad "project continuation cycle bound"
fi

printf 'project finalization\n' | FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
    AGY_TEST_WORKDIR="$TMP/project-worktree" start_worker project-final --workflow project --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
    > "$TMP/project-final.start" 2> "$TMP/project-final.err"
wait_terminal project-final "$TMP/project-final.start"
control_worker status project-final > "$TMP/project-final.status"
project_final_sha="$(status_sha "$TMP/project-final.status")"
project_final_calls_before="$(wc -l < "$TMP/project-final.calls" | tr -d ' ')"
project_verified_feedback project-final | control_worker finalize project-final --approve-state-sha "$project_final_sha" \
    --assurance verified > "$TMP/project-final.finalize" 2> "$TMP/project-final.finalize.err"
project_finalize_rc=$?
project_verified_feedback project-final | control_worker finalize project-final --approve-state-sha "$project_final_sha" \
    --assurance verified > /dev/null 2>&1
project_finalize_stale_rc=$?
project_final_calls_after="$(wc -l < "$TMP/project-final.calls" | tr -d ' ')"
if [[ "$project_finalize_rc" == 0 && "$project_finalize_stale_rc" == 64 \
        && "$project_final_calls_before" == "$project_final_calls_after" ]] \
        && python3 - "$TMP/project-final.finalize" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["phase"] == "completed"
assert value["assurance"] == "verified"
assert value["check_counts"] == {"passed": 2, "failed": 0, "advisory": 0, "missing": 0}
PY
then
    ok "project finalization is a SHA-bound no-provider Codex quality decision"
else
    bad "project finalization contract"
fi

mkdir -p "$TMP/project-outside"
ln -s "$TMP/project-outside" "$TMP/project-worktree/outward-link"
printf 'outward link\n' | AGY_TEST_WORKDIR="$TMP/project-worktree" run_worker project-outward --workflow project > "$TMP/project-outward.out" 2> "$TMP/project-outward.err"
project_outward_rc=$?
rm "$TMP/project-worktree/outward-link"
mkdir -p "$TMP/project-worktree/internal-link-target"
ln -s "$TMP/project-worktree/internal-link-target" "$TMP/project-worktree/internal-link"
printf 'internal link\n' | AGY_TEST_WORKDIR="$TMP/project-worktree" run_worker project-internal --workflow project > "$TMP/project-internal.out" 2> "$TMP/project-internal.err"
project_internal_rc=$?
ln -s .git "$TMP/project-worktree/admin"
printf 'Git admin alias\n' | AGY_TEST_WORKDIR="$TMP/project-worktree" run_worker project-internal-git-alias --workflow project > "$TMP/project-internal-git-alias.out" 2> "$TMP/project-internal-git-alias.err"
project_internal_git_alias_rc=$?
rm "$TMP/project-worktree/admin"
if [[ "$project_outward_rc" == 64 && "$project_internal_rc" == 0 \
        && "$project_internal_git_alias_rc" == 64 \
        && ! -e "$TMP/logs/project-internal-git-alias/task.txt" \
        && ! -e "$TMP/project-internal-git-alias.called" ]]; then
    ok "project workflow rejects Git-admin aliases while allowing ordinary contained links"
else
    bad "project worktree symlink boundary"
fi

printf 'main checkout is not an eligible project worktree\n' | run_worker project-main-reject --workflow project \
    > "$TMP/project-main-reject.out" 2> "$TMP/project-main-reject.err"
project_main_rc=$?
if [[ "$project_main_rc" == 64 && ! -e "$TMP/logs/project-main-reject/task.txt" ]]; then
    ok "project workflow requires a linked-worktree Git marker file"
else
    bad "project workflow main-checkout boundary"
fi

# The preflight binds only the linked-worktree root marker.  A nested Git
# marker is rejected as an authority boundary before worker/provider setup;
# its kind and contents must not be inspected as worktree input.
project_nested_git_ok=1
for project_nested_kind in file directory symlink special; do
    project_nested_dir="$TMP/project-worktree/nested-$project_nested_kind"
    project_nested_marker="$project_nested_dir/.git"
    mkdir -p "$project_nested_dir"
    case "$project_nested_kind" in
        file) printf 'nested marker\n' > "$project_nested_marker" ;;
        directory) mkdir "$project_nested_marker"; printf 'secret\n' > "$project_nested_marker/secret" ;;
        symlink) ln -s "$TMP/project-outside" "$project_nested_marker" ;;
        special) mkfifo "$project_nested_marker" ;;
    esac
    printf 'nested Git marker\n' | AGY_TEST_WORKDIR="$TMP/project-worktree" \
        run_worker "project-nested-git-$project_nested_kind" --workflow project \
        > "$TMP/project-nested-git-$project_nested_kind.out" \
        2> "$TMP/project-nested-git-$project_nested_kind.err"
    project_nested_rc=$?
    [[ "$project_nested_rc" == 64 && ! -e "$TMP/logs/project-nested-git-$project_nested_kind/task.txt" ]] \
        || project_nested_git_ok=0
    rm -rf "$project_nested_dir"
done
if [[ "$project_nested_git_ok" == 1 ]]; then
    ok "project shell preflight rejects every nested Git marker before provider setup"
else
    bad "project shell nested Git marker boundary"
fi

# On a case-insensitive volume `.GIT` aliases the canonical marker lookup.
# Directory-entry spelling, not that later lookup, must reject it before the
# launcher can create task/provider artifacts.
project_casefold_nested_dir="$TMP/project-worktree/nested-casefold"
mkdir -p "$project_casefold_nested_dir"
printf 'nested alias marker\n' > "$project_casefold_nested_dir/.GIT"
printf 'casefold nested Git marker\n' | AGY_TEST_WORKDIR="$TMP/project-worktree" \
    run_worker project-nested-git-casefold --workflow project \
    > "$TMP/project-nested-git-casefold.out" 2> "$TMP/project-nested-git-casefold.err"
project_casefold_nested_rc=$?
rm -rf "$project_casefold_nested_dir"
if [[ "$project_casefold_nested_rc" == 64 && ! -e "$TMP/logs/project-nested-git-casefold/task.txt" ]]; then
    ok "project shell preflight rejects casefold nested .GIT aliases before provider setup"
else
    bad "project shell casefold nested Git marker boundary"
fi

cp "$TMP/project-worktree/.git" "$TMP/project-marker.saved"
printf 'project marker drift\n' | AGY_TEST_WORKDIR="$TMP/project-worktree" \
    FAKE_MUTATE_PROJECT_MARKER="$TMP/project-worktree/.git" run_worker project-marker-drift --workflow project \
    > "$TMP/project-marker-drift.out" 2> "$TMP/project-marker-drift.err"
project_marker_rc=$?
cp "$TMP/project-marker.saved" "$TMP/project-worktree/.git"
control_worker status project-marker-drift > "$TMP/project-marker-drift.status"
if [[ "$project_marker_rc" == 20 ]] && python3 - "$TMP/project-marker-drift.status" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["status"] == "failed"
assert value["phase"] == "blocked"
assert value["assurance"] is None
assert value["continue_available"] is False
PY
then
    ok "project workflow binds the linked-worktree marker before and after provider execution"
else
    bad "project workflow marker drift boundary"
fi

cp "$TMP/project-worktree/.git" "$TMP/project-marker-sparse-post.saved"
printf 'project sparse marker drift after provider start\n' | \
    AGY_TEST_WORKDIR="$TMP/project-worktree" \
    FAKE_SPARSE_PROJECT_MARKER="$TMP/project-worktree/.git" \
    run_worker project-marker-sparse-post --workflow project \
    > "$TMP/project-marker-sparse-post.out" 2> "$TMP/project-marker-sparse-post.err"
project_marker_sparse_post_rc=$?
cp "$TMP/project-marker-sparse-post.saved" "$TMP/project-worktree/.git"
control_worker status project-marker-sparse-post > "$TMP/project-marker-sparse-post.status"
project_marker_sparse_post_calls="$(wc -l < "$TMP/project-marker-sparse-post.worker-calls" | tr -d ' ')"
if [[ "$project_marker_sparse_post_rc" == 20 && "$project_marker_sparse_post_calls" == 1 ]] \
        && python3 - "$TMP/project-marker-sparse-post.status" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["status"] == "failed"
assert value["phase"] == "blocked"
assert value["assurance"] is None
assert value["has_prior_candidate"] is False
PY
then
    ok "project post-provider boundary rejects a sparse oversized marker without accepting a result"
else
    bad "project post-provider sparse marker boundary"
fi

printf 'project boundary cannot rebaseline between cycles\n' | \
    FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
    AGY_TEST_WORKDIR="$TMP/project-worktree" start_worker project-between-cycle-drift \
    --workflow project --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
    > "$TMP/project-between-cycle-drift.start" 2> "$TMP/project-between-cycle-drift.err"
wait_terminal project-between-cycle-drift "$TMP/project-between-cycle-drift.start"
control_worker status project-between-cycle-drift > "$TMP/project-between-cycle-drift.status"
project_between_sha="$(status_sha "$TMP/project-between-cycle-drift.status")"
cp "$TMP/logs/project-between-cycle-drift/dispatch-state.json" \
    "$TMP/project-between-cycle-drift.state-before"
mv "$TMP/project-worktree/.git" "$TMP/project-between-cycle-drift.marker"
printf 'gitdir: between-cycle-tamper\n' > "$TMP/project-worktree/.git"
project_between_calls_before="$(wc -l < "$TMP/project-between-cycle-drift.worker-calls" | tr -d ' ')"
project_feedback project-between-cycle-drift | FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
    control_worker continue project-between-cycle-drift --approve-state-sha "$project_between_sha" \
    > "$TMP/project-between-cycle-drift.continue" 2> "$TMP/project-between-cycle-drift.continue.err"
project_between_rc=$?
project_between_calls_after="$(wc -l < "$TMP/project-between-cycle-drift.worker-calls" | tr -d ' ')"
rm "$TMP/project-worktree/.git"
mv "$TMP/project-between-cycle-drift.marker" "$TMP/project-worktree/.git"
if [[ "$project_between_rc" == 64 \
        && "$project_between_calls_before" == 1 \
        && "$project_between_calls_after" == "$project_between_calls_before" \
        && ! -e "$TMP/logs/project-between-cycle-drift/continue-staged/cycle-002.json" ]] \
        && cmp -s "$TMP/project-between-cycle-drift.state-before" \
            "$TMP/logs/project-between-cycle-drift/dispatch-state.json"; then
    ok "project continuation rejects between-cycle marker drift without provider call or state replacement"
else
    bad "project continuation must not rebaseline a changed worktree boundary"
fi

project_oversized_boundary_case() {
    local job="$1" fixture_kind="$2" description="$3"
    printf 'project oversized boundary fixture\n' | \
        FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
        AGY_TEST_WORKDIR="$TMP/project-worktree" start_worker "$job" \
        --workflow project --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
        > "$TMP/$job.start" 2> "$TMP/$job.err"
    wait_terminal "$job" "$TMP/$job.start"
    control_worker status "$job" > "$TMP/$job.status"
    local approved_sha calls_before calls_after continue_rc
    approved_sha="$(status_sha "$TMP/$job.status")"
    cp "$TMP/logs/$job/dispatch-state.json" "$TMP/$job.state-before"
    mv "$TMP/project-worktree/.git" "$TMP/$job.marker"
    if [[ "$fixture_kind" == "dense" ]]; then
        PYTHONDONTWRITEBYTECODE=1 python3 - "$TMP/project-worktree/.git" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_bytes(b"x" * 4097)
PY
    else
        PYTHONDONTWRITEBYTECODE=1 python3 - "$TMP/project-worktree/.git" <<'PY'
import os
import sys
descriptor = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    os.ftruncate(descriptor, 1 << 20)
finally:
    os.close(descriptor)
PY
    fi
    calls_before="$(wc -l < "$TMP/$job.worker-calls" | tr -d ' ')"
    project_feedback "$job" | control_worker continue "$job" --approve-state-sha "$approved_sha" \
        > "$TMP/$job.continue" 2> "$TMP/$job.continue.err"
    continue_rc=$?
    calls_after="$(wc -l < "$TMP/$job.worker-calls" | tr -d ' ')"
    rm "$TMP/project-worktree/.git"
    mv "$TMP/$job.marker" "$TMP/project-worktree/.git"
    if [[ "$continue_rc" == 64 && "$calls_before" == 1 && "$calls_after" == "$calls_before" \
            && ! -e "$TMP/logs/$job/continue-staged/cycle-002.json" ]] \
            && cmp -s "$TMP/$job.state-before" "$TMP/logs/$job/dispatch-state.json"; then
        ok "$description"
    else
        bad "$description"
    fi
}

project_oversized_boundary_case project-between-cycle-oversized dense \
    "project continuation rejects a 4097-byte marker before state replacement or provider call"
project_oversized_boundary_case project-between-cycle-sparse sparse \
    "project continuation rejects a sparse oversized marker before state replacement or provider call"

printf 'project orphan preserve-only fixture\n' | \
    FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
    AGY_TEST_WORKDIR="$TMP/project-worktree" start_worker project-orphan-preserve \
    --workflow project --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
    > "$TMP/project-orphan-preserve.start" 2> "$TMP/project-orphan-preserve.err"
wait_terminal project-orphan-preserve "$TMP/project-orphan-preserve.start"
PROJECT_ORPHAN_JOB="$TMP/logs/project-orphan-preserve"
PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" \
        "$PROJECT_ORPHAN_JOB" <<'PY'
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("agy_dispatch_project_orphan", sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
job = Path(sys.argv[2])
state, raw, _sha = module.load_state(job)
state.update({
    "sequence": state["sequence"] + 1,
    "previous_state_sha256": module.digest(raw),
    "status": "orphaned", "reason": "status_unavailable", "exit_code": 20,
    "attempt": 2, "cycle": 2, "attempt_origin": "conversation-continue",
    "controller_pid": None, "finished_epoch": 1.0,
    "resume_available": False, "continue_available": False,
    "result_path": None, "result_sha256": None, "result_identity": None,
    "last_success_path": state["result_path"],
    "last_success_sha256": state["result_sha256"],
    "last_success_identity": state["result_identity"],
    "phase": "repairing", "assurance": "pending",
})
module.write_atomic(job, module.STATE_NAME, state)
PY
project_orphan_sha="$(PYTHONDONTWRITEBYTECODE=1 python3 - "$PROJECT_ORPHAN_JOB/dispatch-state.json" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
cp "$PROJECT_ORPHAN_JOB/dispatch-state.json" "$TMP/project-orphan-preserve.state-before"
project_orphan_calls_before="$(wc -l < "$TMP/project-orphan-preserve.worker-calls" | tr -d ' ')"
project_feedback project-orphan-preserve | control_worker continue project-orphan-preserve \
    --approve-state-sha "$project_orphan_sha" > /dev/null 2>&1
project_orphan_continue_rc=$?
control_worker resume project-orphan-preserve \
    --approve-state-sha "$project_orphan_sha" > /dev/null 2>&1
project_orphan_resume_rc=$?
control_worker restart project-orphan-preserve \
    --approve-state-sha "$project_orphan_sha" > /dev/null 2>&1
project_orphan_restart_rc=$?
project_feedback project-orphan-preserve | control_worker finalize project-orphan-preserve \
    --approve-state-sha "$project_orphan_sha" --assurance partially_verified > /dev/null 2>&1
project_orphan_finalize_rc=$?
control_worker result project-orphan-preserve > /dev/null 2>&1
project_orphan_result_rc=$?
project_orphan_calls_after="$(wc -l < "$TMP/project-orphan-preserve.worker-calls" | tr -d ' ')"
if [[ "$project_orphan_continue_rc" == 64 && "$project_orphan_resume_rc" == 21 \
        && "$project_orphan_restart_rc" == 64 && "$project_orphan_finalize_rc" == 64 \
        && "$project_orphan_result_rc" == 20 \
        && "$project_orphan_calls_after" == "$project_orphan_calls_before" ]] \
        && cmp -s "$TMP/project-orphan-preserve.state-before" \
            "$PROJECT_ORPHAN_JOB/dispatch-state.json"; then
    ok "orphaned project state is preserve-only across continuation, finalization, and result surfaces"
else
    bad "orphaned project state must not progress or expose a trusted partial result"
fi

LEGACY_V1_JOB="$TMP/logs/resume-case"
PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" \
        "$LEGACY_V1_JOB" <<'PY'
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("agy_dispatch_legacy_v1", sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
job = Path(sys.argv[2])
state, _raw, _sha = module.load_state(job)
for field in module.STATE_PROJECT_FIELDS:
    state.pop(field)
for field in module.STATE_V5_FIELDS:
    state.pop(field)
for field in {*module.STATE_V6_FIELDS, *module.STATE_V8_FIELDS, *module.STATE_V9_FIELDS}:
    state.pop(field)
state.pop("provider_retry_after_seconds")
state.pop("provider_retry_observed_epoch")
state["schema_version"] = 1
module.write_atomic(job, module.STATE_NAME, state)
PY
legacy_v1_bytecode_manifest() {
    PYTHONDONTWRITEBYTECODE=1 python3 -B - "$ROOT/skills/agy-worker/runtime/scripts" <<'PY'
import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1])
for path in sorted(root.rglob("*")):
    if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
        if path.is_file():
            print(path.relative_to(root), hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            print(path.relative_to(root), "directory")
PY
}
legacy_v1_bytecode_manifest > "$TMP/legacy-v1.bytecode-before"
legacy_v1_calls_before="$(wc -l < "$TMP/resume-case.worker-calls" | tr -d ' ')"
control_worker status resume-case > "$TMP/legacy-v1.status"
legacy_v1_status_rc=$?
control_worker result resume-case > "$TMP/legacy-v1.result"
legacy_v1_result_rc=$?
legacy_v1_sha="$(status_sha "$TMP/legacy-v1.status")"
cp "$LEGACY_V1_JOB/dispatch-state.json" "$TMP/legacy-v1.state-before"
control_worker resume resume-case --approve-state-sha "$legacy_v1_sha" > /dev/null 2>&1
legacy_v1_resume_rc=$?
legacy_v1_resume_unchanged=1
cmp -s "$TMP/legacy-v1.state-before" "$LEGACY_V1_JOB/dispatch-state.json" \
    || legacy_v1_resume_unchanged=0
control_worker restart resume-case --approve-state-sha "$legacy_v1_sha" --format json \
    > "$TMP/legacy-v1.restart" 2> "$TMP/legacy-v1.restart.err"
legacy_v1_restart_rc=$?
legacy_v1_restart_unchanged=1
cmp -s "$TMP/legacy-v1.state-before" "$LEGACY_V1_JOB/dispatch-state.json" \
    || legacy_v1_restart_unchanged=0
PYTHONDONTWRITEBYTECODE=1 python3 -B - "$LEGACY_V1_JOB/selection.json" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.write_bytes(path.read_bytes() + b" ")
PY
control_worker result resume-case > /dev/null 2>&1
legacy_v1_tampered_result_rc=$?
legacy_v1_tamper_unchanged=1
cmp -s "$TMP/legacy-v1.state-before" "$LEGACY_V1_JOB/dispatch-state.json" \
    || legacy_v1_tamper_unchanged=0
legacy_v1_bytecode_manifest > "$TMP/legacy-v1.bytecode-after"
legacy_v1_bytecode_unchanged=1
cmp -s "$TMP/legacy-v1.bytecode-before" "$TMP/legacy-v1.bytecode-after" \
    || legacy_v1_bytecode_unchanged=0
legacy_v1_calls_after="$(wc -l < "$TMP/resume-case.worker-calls" | tr -d ' ')"
if [[ "$legacy_v1_status_rc" == 0 && "$legacy_v1_result_rc" == 0 \
        && "$legacy_v1_resume_rc" == 21 && "$legacy_v1_resume_unchanged" == 1 \
        && "$legacy_v1_restart_rc" == 64 && "$legacy_v1_restart_unchanged" == 1 \
        && "$legacy_v1_tampered_result_rc" == 20 && "$legacy_v1_tamper_unchanged" == 1 \
        && "$legacy_v1_bytecode_unchanged" == 1 \
        && "$legacy_v1_calls_after" == "$legacy_v1_calls_before" ]] \
        && PYTHONDONTWRITEBYTECODE=1 python3 -B - "$TMP/legacy-v1.status" \
            "$LEGACY_V1_JOB/dispatch-state.json" \
            "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" <<'PY'
import importlib.util
import json, sys
from pathlib import Path
old = json.load(open(sys.argv[1], encoding="utf-8"))
stored = json.load(open(sys.argv[2], encoding="utf-8"))
spec = importlib.util.spec_from_file_location("agy_dispatch_legacy_assert", sys.argv[3])
module = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(module)
assert old["next_action"] == "result"
assert old["next_action_command"] == '"$PIPELINE/agy-worker.sh" result --job-id resume-case --format json'
assert old["phase"] is None and old["assurance"] is None
assert [item["action"] for item in old["available_actions"]] == ["result"]
assert stored["schema_version"] == 1
assert stored["attempt"] == old["attempt"] == 2
assert stored["attempt_origin"] == old["attempt_origin"]
PY
then
    ok "legacy v1 reads safely, rejects selection drift, and remains result-only without recovery authority"
else
    bad "legacy v1 control-state compatibility"
fi

PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" \
        "$TMP/logs/project-between-cycle-drift" "$TMP/project-worktree" <<'PY'
import importlib.util
from pathlib import Path
import sys
source, job_text, workdir = sys.argv[1:]
spec = importlib.util.spec_from_file_location("agy_dispatch_rollback", source)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
job = Path(job_text)
before = (job / module.STATE_NAME).read_bytes()
state, _raw, sha = module.load_state(job)
verification = {"schema_version": 2, "summary": "retryable injected write failure",
    "passed_checks": ["lint"], "failed_checks": ["unit"],
    "advisory_checks": 0, "missing_checks": 0,
    "candidate_sha256": state["result_sha256"], "coverage": "partial",
    "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": True}
original = module.write_atomic
def fail_state_write(target, name, value):
    if name == module.STATE_NAME:
        raise module.DispatchError("injected state write failure")
    return original(target, name, value)
module.write_atomic = fail_state_write
try:
    module.create_state(job, "conversation-continue", resume=True,
        approve_sha=sha, verification=verification)
except module.DispatchError as exc:
    assert str(exc) == "injected state write failure"
else:
    raise AssertionError("injected write unexpectedly succeeded")
finally:
    module.write_atomic = original
assert (job / module.STATE_NAME).read_bytes() == before
assert not (job / "continue-staged" / "cycle-002.json").exists()
next_state, _next_sha = module.create_state(job, "conversation-continue", resume=True,
    approve_sha=sha, verification=verification)
assert next_state["cycle"] == 2 and next_state["phase"] == "repairing"
PY
verification_rollback_rc=$?
if [[ "$verification_rollback_rc" == 0 ]]; then
    ok "verification staging rolls back exactly on injected state-write failure and retries cleanly"
else
    bad "verification staging rollback and retry"
fi

PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" \
        "$TMP/boundary-cap" <<'PY'
import importlib.util
import os
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("agy_dispatch_boundary_cap", sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
root = Path(sys.argv[2]).resolve(); root.mkdir(mode=0o700)
(root / ".git").write_text("gitdir: bounded\n", encoding="ascii")
(root / "one").write_text("1", encoding="ascii")
(root / "two").write_text("2", encoding="ascii")
module.MAX_BOUNDARY_ENTRIES = 2
module._project_boundary(str(root))
(root / "three").write_text("3", encoding="ascii")
try:
    module._project_boundary(str(root))
except module.DispatchError as exc:
    assert str(exc) == "project worktree boundary scan is too large"
else:
    raise AssertionError("over-cap boundary scan unexpectedly succeeded")
PY
boundary_cap_rc=$?
if [[ "$boundary_cap_rc" == 0 ]]; then
    ok "project boundary accepts the exact entry cap and rejects one entry over it"
else
    bad "project boundary entry cap"
fi

printf 'project concurrent decision fixture\n' | FAKE_DISPATCH_MODE=heartbeat-success \
    FAKE_HEARTBEAT_COUNT=2 AGY_TEST_WORKDIR="$TMP/project-worktree" \
    start_worker project-concurrent-decision --workflow project \
    --idle-timeout 1s --hard-timeout 2s --max-runtime 8s \
    > "$TMP/project-concurrent-decision.start" 2> "$TMP/project-concurrent-decision.err"
wait_terminal project-concurrent-decision "$TMP/project-concurrent-decision.start"
control_worker status project-concurrent-decision > "$TMP/project-concurrent-decision.status"
project_concurrent_sha="$(status_sha "$TMP/project-concurrent-decision.status")"
( project_feedback project-concurrent-decision | FAKE_DISPATCH_MODE=heartbeat-success FAKE_HEARTBEAT_COUNT=2 \
    control_worker continue project-concurrent-decision --approve-state-sha "$project_concurrent_sha" \
    > "$TMP/project-concurrent-decision.continue" 2> /dev/null; \
    printf '%s\n' "$?" > "$TMP/project-concurrent-decision.continue.rc" ) &
project_concurrent_continue_pid=$!
( project_feedback project-concurrent-decision | control_worker finalize project-concurrent-decision \
    --approve-state-sha "$project_concurrent_sha" --assurance partially_verified \
    > "$TMP/project-concurrent-decision.finalize" 2> /dev/null; \
    printf '%s\n' "$?" > "$TMP/project-concurrent-decision.finalize.rc" ) &
project_concurrent_finalize_pid=$!
wait "$project_concurrent_continue_pid"
wait "$project_concurrent_finalize_pid"
project_concurrent_continue_rc="$(<"$TMP/project-concurrent-decision.continue.rc")"
project_concurrent_finalize_rc="$(<"$TMP/project-concurrent-decision.finalize.rc")"
if [[ "$project_concurrent_continue_rc" == 0 ]]; then
    wait_terminal project-concurrent-decision "$TMP/project-concurrent-decision.continue"
fi
project_concurrent_calls="$(wc -l < "$TMP/project-concurrent-decision.worker-calls" | tr -d ' ')"
if [[ $(( (project_concurrent_continue_rc == 0 ? 1 : 0) \
        + (project_concurrent_finalize_rc == 0 ? 1 : 0) )) == 1 \
        && "$project_concurrent_calls" -ge 1 && "$project_concurrent_calls" -le 2 ]]; then
    ok "concurrent continue and finalize on one approved SHA publish exactly one winner"
else
    bad "concurrent project continuation/finalization arbitration"
fi

printf 'project cancel repair fixture\n' | FAKE_DISPATCH_MODE=heartbeat-success \
    FAKE_HEARTBEAT_COUNT=2 AGY_TEST_WORKDIR="$TMP/project-worktree" \
    start_worker project-cancel-repair --workflow project --idle-timeout 1s --hard-timeout 3s --max-runtime 8s \
    > "$TMP/project-cancel-repair.start" 2> "$TMP/project-cancel-repair.err"
wait_terminal project-cancel-repair "$TMP/project-cancel-repair.start"
control_worker status project-cancel-repair > "$TMP/project-cancel-repair.status"
project_cancel_sha="$(status_sha "$TMP/project-cancel-repair.status")"
project_cancel_barrier_ready="$TMP/project-cancel-repair.barrier-ready"
project_cancel_barrier_release="$TMP/project-cancel-repair.barrier-release"
project_feedback project-cancel-repair | FAKE_DISPATCH_MODE=heartbeat-forever \
    FAKE_HEARTBEAT_BARRIER_READY="$project_cancel_barrier_ready" \
    FAKE_HEARTBEAT_BARRIER_RELEASE="$project_cancel_barrier_release" \
    control_worker continue project-cancel-repair --approve-state-sha "$project_cancel_sha" \
    > "$TMP/project-cancel-repair.continue" 2> "$TMP/project-cancel-repair.continue.err"
project_cancel_barrier_observed=0
# Controller ownership is acknowledged before its strict candidate/worktree
# preflight.  Under full-suite scheduling that bounded preflight can outlive a
# two-second fixture poll even though the public startup handshake remains
# healthy.  Observe the same five-second bound as spawn(); do not manufacture
# progress or weaken the provider hard deadline.
for (( project_cancel_index=0; project_cancel_index<500; project_cancel_index++ )); do
    if [[ -e "$project_cancel_barrier_ready" ]]; then
        control_worker status project-cancel-repair > "$TMP/project-cancel-repair.running"
        if [[ "$(status_field "$TMP/project-cancel-repair.running" progress_count)" -ge 1 ]]; then
            project_cancel_barrier_observed=1
            break
        fi
    fi
    sleep 0.01
done
project_cancel_rc=64
project_cancel_wait_rc=64
project_cancel_finalize_rc=64
project_cancel_result_rc=64
project_cancel_latency_ok=0
project_cancel_preserved_rc=64
if [[ "$project_cancel_barrier_observed" == 1 ]]; then
    project_cancel_running_sha="$(status_sha "$TMP/project-cancel-repair.running")"
    project_cancel_started_epoch="$(python3 -c 'import time; print(time.time())')"
    control_worker cancel project-cancel-repair --approve-state-sha "$project_cancel_running_sha" \
        > "$TMP/project-cancel-repair.cancel"
    project_cancel_rc=$?
fi
: > "$project_cancel_barrier_release"
if [[ "$project_cancel_rc" == 0 ]] \
        && wait_terminal project-cancel-repair "$TMP/project-cancel-repair.cancel"; then
    project_cancel_wait_rc=0
    if python3 - "$project_cancel_started_epoch" \
            "$TMP/logs/project-cancel-repair/dispatch-state.json" <<'PY'
import json
import sys
started = float(sys.argv[1])
state = json.load(open(sys.argv[2], encoding="utf-8"))
# Measure the terminal state transition itself. The subsequent public wait
# deliberately rebinds candidate actions and can include a bounded Git scan.
assert 0 <= state["finished_epoch"] - started < 2.0
PY
    then
        project_cancel_latency_ok=1
    fi
    if python3 - "$TMP/logs/project-cancel-repair/dispatch-state.json" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert (state["status"], state["reason"]) == ("cancelled", "cancelled")
assert state["candidate_recognized"] is True
assert state["candidate_source"] == "provider_success"
assert state["result_available"] is True
assert state["driver_disposition"] == "unreviewed"
# A local repair cancellation adds no new provider candidate/worktree fact.
# Preserve the old exact binding and let result/finalize rebind it on use.
assert state["worktree_reconciliation"] == "unavailable"
PY
    then
        project_cancel_preserved_rc=0
    fi
    project_cancel_terminal_sha="$(status_sha "$TMP/project-cancel-repair.cancel")"
    project_feedback project-cancel-repair | control_worker finalize project-cancel-repair \
        --approve-state-sha "$project_cancel_terminal_sha" --assurance partially_verified \
        > "$TMP/project-cancel-repair.finalize"
    project_cancel_finalize_rc=$?
    control_worker result project-cancel-repair > "$TMP/project-cancel-repair.result"
    project_cancel_result_rc=$?
fi
if [[ "$project_cancel_wait_rc" == 0 && "$project_cancel_latency_ok" == 1 \
        && "$project_cancel_preserved_rc" == 0 \
        && "$project_cancel_finalize_rc" == 0 \
        && "$project_cancel_result_rc" == 0 ]]; then
    ok "cancelled repair can partially finalize and return the prior bound success"
else
    bad "cancelled repair prior-candidate finalization"
fi

SYMLINK_STATE_DIR="$TMP/logs/resume-unavailable"
mv "$SYMLINK_STATE_DIR/dispatch-state.json" "$SYMLINK_STATE_DIR/dispatch-state.real"
ln -s dispatch-state.real "$SYMLINK_STATE_DIR/dispatch-state.json"
control_worker status resume-unavailable > "$TMP/symlink-state.out" 2> "$TMP/symlink-state.err"
symlink_state_rc=$?
if [[ "$symlink_state_rc" == 20 ]] && [[ ! -s "$TMP/symlink-state.out" ]]; then
    ok "control state refuses symlink replacement rather than following a swapped state file"
else
    bad "control state symlink replacement"
fi

echo
echo "model-recommendation.sh offline policy tests:"
expect_direct_recommendation "pre-dispatch explicit pair stays unranked and unapplied" \
    pre-dispatch gemini-3.6-flash high high-complexity-bounded gemini-3.6-flash-high
expect_direct_recommendation "post-gate explicit pair cannot be changed or redispatched" \
    post-gate gemini-3.1-pro low driver-verification-failed gemini-3.1-pro-low
expect_direct_recommendation "fixed exact model stays unranked and unapplied" \
    pre-dispatch claude-sonnet-4-6 '' high-complexity-bounded claude-sonnet-4-6
expect_recommendation "pre-dispatch routine work needs no escalation" \
    pre-dispatch cheap bounded-routine no-escalation null none 0
expect_recommendation "pre-dispatch mechanical batch recommends bulk" \
    pre-dispatch cheap batched-mechanical consider-higher-tier bulk increase 1
expect_recommendation "pre-dispatch bounded cross-file work recommends hard" \
    pre-dispatch bulk cross-file-bounded consider-higher-tier hard increase 1
expect_recommendation "pre-dispatch bounded high-complexity work recommends hardest" \
    pre-dispatch hard high-complexity-bounded consider-higher-tier hardest increase 1
expect_recommendation "pre-dispatch recommendation can span named tier steps" \
    pre-dispatch cheap high-complexity-bounded consider-higher-tier hardest increase 3
expect_recommendation "pre-dispatch never escalates the highest named tier" \
    pre-dispatch hardest high-complexity-bounded no-escalation null none 0
expect_recommendation "pre-dispatch default tier stays non-rankable" \
    pre-dispatch default high-complexity-bounded no-escalation null none 0
expect_recommendation "pre-dispatch custom model stays non-rankable" \
    pre-dispatch vendor/model-v1 high-complexity-bounded no-escalation null none 0
expect_recommendation "raw flash-high stays custom, unranked, and recommendation-only" \
    pre-dispatch gemini-3.6-flash-high high-complexity-bounded no-escalation null none 0

expect_recommendation "accepted gate result needs no escalation" \
    post-gate bulk gate-accepted no-escalation null none 0
expect_recommendation "driver verification failure recommends one higher tier" \
    post-gate bulk driver-verification-failed consider-higher-tier hard increase 1
expect_recommendation "driver quality review failure recommends one higher tier" \
    post-gate cheap driver-quality-review-failed consider-higher-tier bulk increase 1
expect_recommendation "missing expected edits recommends one higher tier" \
    post-gate hard expected-edits-missing consider-higher-tier hardest increase 1
expect_recommendation "permission failures are non-escalatable" \
    post-gate cheap permission-failed no-escalation null none 0
expect_recommendation "authentication failures are non-escalatable" \
    post-gate cheap authentication-failed no-escalation null none 0
expect_recommendation "scope-policy failures are non-escalatable" \
    post-gate cheap scope-policy-failed no-escalation null none 0
expect_recommendation "human-required outcomes are non-escalatable" \
    post-gate cheap human-required no-escalation null none 0
expect_recommendation "untrusted noncompleted outcomes are non-escalatable" \
    post-gate cheap noncompleted-worker-outcome no-escalation null none 0
expect_recommendation "untrusted worker claims are non-escalatable" \
    post-gate cheap untrusted-worker-claim no-escalation null none 0
expect_recommendation "invalid envelopes are non-escalatable" \
    post-gate cheap invalid-envelope no-escalation null none 0
expect_recommendation "post-gate never escalates the highest named tier" \
    post-gate hardest driver-verification-failed no-escalation null none 0
expect_recommendation "post-gate default tier stays non-rankable" \
    post-gate default driver-verification-failed no-escalation null none 0
expect_recommendation "post-gate custom model stays non-rankable" \
    post-gate vendor:model-v1 driver-verification-failed no-escalation null none 0

expect_recommendation_reject "pre-dispatch rejects post-gate evidence" \
    --stage pre-dispatch --selected-tier bulk --evidence permission-failed
expect_recommendation_reject "post-gate rejects pre-dispatch evidence" \
    --stage post-gate --selected-tier bulk --evidence batched-mechanical
expect_recommendation_reject "unknown evidence is rejected" \
    --stage post-gate --selected-tier bulk --evidence worker-says-hard
expect_recommendation_reject "invalid selected tier syntax is rejected" \
    --stage pre-dispatch --selected-tier 'hard tier' --evidence high-complexity-bounded
expect_recommendation_reject "duplicate stage is rejected as ambiguous" \
    --stage pre-dispatch --stage post-gate --selected-tier bulk --evidence gate-accepted
expect_recommendation_reject "duplicate selected tier is rejected as ambiguous" \
    --stage pre-dispatch --selected-tier cheap --selected-tier hard --evidence bounded-routine
expect_recommendation_reject "duplicate evidence is rejected as ambiguous" \
    --stage pre-dispatch --selected-tier bulk --evidence bounded-routine --evidence batched-mechanical
expect_recommendation_reject "missing stage is rejected" \
    --selected-tier bulk --evidence bounded-routine
expect_recommendation_reject "missing selected tier is rejected" \
    --stage pre-dispatch --evidence bounded-routine
expect_recommendation_reject "missing evidence is rejected" \
    --stage pre-dispatch --selected-tier bulk
expect_recommendation_reject "thinking-level flags are not an interface" \
    --stage pre-dispatch --selected-tier bulk --evidence bounded-routine --thinking-level high
expect_recommendation_reject "selected tier and selected model conflict" \
    --stage pre-dispatch --selected-tier bulk --selected-model gemini-3.6-flash-high \
    --evidence bounded-routine
expect_recommendation_reject "selected effort requires selected model" \
    --stage pre-dispatch --selected-tier bulk --selected-effort high --evidence bounded-routine
expect_recommendation_reject "duplicate selected model is ambiguous" \
    --stage pre-dispatch --selected-model gemini-3.6-flash-high \
    --selected-model gemini-3.6-flash-high --evidence bounded-routine
expect_recommendation_reject "duplicate selected effort is ambiguous" \
    --stage pre-dispatch --selected-model gemini-3.6-flash --selected-effort high \
    --selected-effort high --evidence bounded-routine
expect_recommendation_reject "unsupported direct recommendation pair is rejected" \
    --stage pre-dispatch --selected-model gemini-3.1-pro --selected-effort medium \
    --evidence bounded-routine
expect_recommendation_reject "unknown direct recommendation model is rejected" \
    --stage pre-dispatch --selected-model vendor/model-v1 --evidence bounded-routine
expect_recommendation_reject "positional arguments are rejected" \
    --stage pre-dispatch --selected-tier bulk --evidence bounded-routine hardest

mkdir -p "$TMP/route-bin"
cat > "$TMP/route-bin/agy" <<EOF
#!/usr/bin/env bash
touch "$TMP/recommender-called-agy"
EOF
cat > "$TMP/route-bin/qa-gate.sh" <<EOF
#!/usr/bin/env bash
touch "$TMP/recommender-called-gate"
EOF
chmod +x "$TMP/route-bin/agy" "$TMP/route-bin/qa-gate.sh"
PATH="$TMP/route-bin:$PATH" "$RECOMMENDER" --stage post-gate --selected-tier bulk \
    --evidence driver-verification-failed > "$TMP/side-effect.json" 2>/dev/null
rc=$?
if [[ "$rc" == "0" && ! -e "$TMP/recommender-called-agy" && ! -e "$TMP/recommender-called-gate" ]]; then
    ok "recommender invokes neither agy nor qa-gate"
else
    bad "recommender invokes neither agy nor qa-gate"
fi

echo
echo "installer path handling:"
SPECIAL="$TMP/repo&with|chars"
mkdir -p "$SPECIAL/skills" "$TMP/installed"
cp "$ROOT/install.sh" "$SPECIAL/install.sh"
cp -R "$ROOT/skills/agy-worker" "$SPECIAL/skills/agy-worker"
chmod +x "$SPECIAL/install.sh"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$TMP/installed" "$SPECIAL/install.sh" > "$TMP/install.out" 2>/dev/null
rc=$?
expect_exit "installer accepts replacement metacharacters in clone path" 0 "$rc"
SPECIAL_REAL="$(cd "$SPECIAL" && pwd -P)"
if [[ "$(<"$TMP/installed/agy-worker/.pipeline-root")" == "$SPECIAL_REAL" ]] \
        && [[ -f "$TMP/installed/agy-worker/agents/openai.yaml" ]] \
        && [[ -x "$TMP/installed/agy-worker/scripts/resolve-pipeline.sh" ]]; then
    ok "installer copies the canonical bundle and records the exact clone path"
else
    bad "installer copies the canonical bundle and records the exact clone path"
fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
