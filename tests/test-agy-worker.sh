#!/usr/bin/env bash
# Offline dispatcher and installer tests using a fake agy executable.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
WORKER="$ROOT/agy-worker.sh"
RECOMMENDER="$ROOT/model-recommendation.sh"
TMP="$(mktemp -d -t agyworker-dispatch.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
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

mkdir -p "$TMP/bin" "$TMP/repo" "$TMP/logs"
chmod 0755 "$TMP/logs"
LOGS_REAL="$(cd "$TMP/logs" && pwd -P)"
cat > "$TMP/bin/agy" <<'FAKE'
#!/usr/bin/env bash
set -u
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
if [[ -n "${FAKE_SIGNAL_PARENT:-}" ]]; then
    kill -s "$FAKE_SIGNAL_PARENT" "$PPID"
    exit "${FAKE_EXIT_CODE:-23}"
fi
if [[ "${FAKE_EXIT_CODE:-0}" != "0" ]]; then
    exit "$FAKE_EXIT_CODE"
fi
status="${FAKE_AGY_STATUS:-SUCCESS}"
if [[ "${FAKE_BAD_ENVELOPE:-0}" == "1" ]]; then
    envelope='{"status":"completed","summary":"done","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":9,"requires_human":false}'
else
    envelope='{"status":"completed","summary":"done","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
fi
printf '{"event":"result","result":{"status":"%s","duration_seconds":0,"num_turns":1,"usage":{},"structured_output":%s}}\n' "$status" "$envelope"
FAKE
chmod +x "$TMP/bin/agy"

run_worker() {
    local job="$1"; shift
    PATH="$TMP/bin:$PATH" \
    AGY_WORKER_LOG_DIR="${AGY_TEST_LOG_DIR:-$TMP/logs}" \
    AGY_WORKER_JOB_ID="$job" \
    FAKE_MODEL_FILE="$TMP/$job.model" \
    FAKE_PROMPT_FILE="$TMP/$job.prompt" \
    FAKE_DIRS_FILE="$TMP/$job.dirs" \
    FAKE_ARGV_FILE="$TMP/$job.argv" \
    FAKE_STAGE_RESULT_FILE="$TMP/$job.stage-result" \
    FAKE_TRY_STAGE_WRITE="${FAKE_TRY_STAGE_WRITE:-0}" \
    FAKE_CALLED_FILE="$TMP/$job.called" \
    FAKE_SIGNAL_PARENT="${FAKE_SIGNAL_PARENT:-}" \
    FAKE_EXIT_CODE="${FAKE_EXIT_CODE:-0}" \
    "${AGY_TEST_WORKER:-$WORKER}" --workdir "$TMP/repo" "$@"
}

echo "agy-worker.sh offline test suite"
echo

printf 'small task\n' | run_worker tier --tier cheap > "$TMP/tier.out" 2>/dev/null
rc=$?
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
        && python3 - "$TMP/raw-flash-high.argv" <<'PY'
import sys
parts = [part for part in open(sys.argv[1], "rb").read().split(b"\0") if part]
raise SystemExit(0 if b"--effort" not in parts else 1)
PY
then
    ok "raw flash-high stays exact pass-through with no effort argument"
else
    bad "raw flash-high stays exact pass-through with no effort argument"
fi

WRAPPER_FIXTURE="$TMP/root-wrapper"
mkdir -p "$WRAPPER_FIXTURE/skills"
cp "$ROOT/agy-worker.sh" "$WRAPPER_FIXTURE/agy-worker.sh"
cp -R "$ROOT/skills/agy-worker" "$WRAPPER_FIXTURE/skills/agy-worker"
chmod +x "$WRAPPER_FIXTURE/agy-worker.sh"
printf 'empty log override\n' | PATH="$TMP/bin:$PATH" \
    AGY_WORKER_LOG_DIR= AGY_WORKER_JOB_ID=empty-log-override \
    FAKE_MODEL_FILE="$TMP/empty-log.model" \
    FAKE_PROMPT_FILE="$TMP/empty-log.prompt" \
    FAKE_DIRS_FILE="$TMP/empty-log.dirs" \
    FAKE_ARGV_FILE="$TMP/empty-log.argv" \
    FAKE_STAGE_RESULT_FILE="$TMP/empty-log.stage-result" \
    "$WRAPPER_FIXTURE/agy-worker.sh" --workdir "$TMP/repo" \
    > "$TMP/empty-log.out" 2> "$TMP/empty-log.err"
rc=$?
if [[ "$rc" == "0" ]] \
        && [[ -f "$WRAPPER_FIXTURE/logs/empty-log-override/task.txt" ]] \
        && [[ ! -e "$WRAPPER_FIXTURE/skills/agy-worker/runtime/logs/empty-log-override" ]]; then
    ok "root wrapper treats an empty log override as the historical root logs default"
else
    bad "root wrapper treats an empty log override as the historical root logs default"
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
python3 - "$WEAK_UMASK_ROOT/agy-worker/runtime/agy-worker.sh" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    source = handle.read()
old = 'CALLER_UMASK="$(umask)"\numask 077\n'
new = 'CALLER_UMASK="$(umask)"\n# TEST MUTATION: private creation mask removed.\n'
if source.count(old) != 1:
    raise SystemExit("expected exactly one private-mask block")
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
if [[ "$rc" == "0" ]] \
        && ! private_tree_is_private "$WEAK_UMASK_LOG/weak-umask" 2>/dev/null; then
    ok "privacy acceptance rejects a runtime with the private creation mask removed"
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
python3 - "$WEAK_RESTORE_ROOT/agy-worker/runtime/agy-worker.sh" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    source = handle.read()
replacements = (
    (
        'chmod 0700 "$staged_dir" 2>/dev/null || true',
        'chmod 0755 "$staged_dir" 2>/dev/null || true',
    ),
    (
        'chmod 0600 "$staged_prompt_file" 2>/dev/null || true',
        'chmod 0644 "$staged_prompt_file" 2>/dev/null || true',
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
if [[ "$rc" == "0" ]] \
        && ! private_tree_is_private "$WEAK_RESTORE_LOG/weak-restore" 2>/dev/null; then
    ok "privacy acceptance rejects a runtime that restores staged artifacts publicly"
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
    AGY_WORKER_MAX_ATTEMPTS=1 FAKE_AGY_STATUS=FAILED \
    FAKE_MODEL_FILE="$TMP/terminal.model" FAKE_PROMPT_FILE="$TMP/terminal.prompt" \
    FAKE_DIRS_FILE="$TMP/terminal.dirs" \
    FAKE_ARGV_FILE="$TMP/terminal.argv" FAKE_STAGE_RESULT_FILE="$TMP/terminal.stage-result" \
    "$WORKER" --workdir "$TMP/repo" > "$TMP/terminal.out" 2>/dev/null
rc=$?
expect_exit "non-success terminal status fails closed" 4 "$rc"

printf 'bad envelope\n' | PATH="$TMP/bin:$PATH" \
    AGY_WORKER_LOG_DIR="$TMP/logs" AGY_WORKER_JOB_ID=bad-envelope \
    AGY_WORKER_MAX_ATTEMPTS=1 FAKE_BAD_ENVELOPE=1 \
    FAKE_MODEL_FILE="$TMP/bad.model" FAKE_PROMPT_FILE="$TMP/bad.prompt" \
    FAKE_DIRS_FILE="$TMP/bad.dirs" \
    FAKE_ARGV_FILE="$TMP/bad.argv" FAKE_STAGE_RESULT_FILE="$TMP/bad.stage-result" \
    "$WORKER" --workdir "$TMP/repo" > "$TMP/bad.out" 2>/dev/null
rc=$?
expect_exit "dispatcher independently rejects schema-invalid output" 4 "$rc"

echo
echo "model-recommendation.sh offline policy tests:"
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
