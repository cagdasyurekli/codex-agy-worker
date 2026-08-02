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

mkdir -p "$TMP/bin" "$TMP/repo" "$TMP/logs"
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
    AGY_WORKER_LOG_DIR="$TMP/logs" \
    AGY_WORKER_JOB_ID="$job" \
    FAKE_MODEL_FILE="$TMP/$job.model" \
    FAKE_PROMPT_FILE="$TMP/$job.prompt" \
    FAKE_DIRS_FILE="$TMP/$job.dirs" \
    FAKE_ARGV_FILE="$TMP/$job.argv" \
    FAKE_STAGE_RESULT_FILE="$TMP/$job.stage-result" \
    FAKE_TRY_STAGE_WRITE="${FAKE_TRY_STAGE_WRITE:-0}" \
    "$WORKER" --workdir "$TMP/repo" "$@"
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
if grep -Fxq "$TMP/logs/oversized/staged" "$TMP/oversized.dirs" \
        && ! grep -Fxq "$TMP/logs" "$TMP/oversized.dirs"; then
    ok "oversized job grants only its staged prompt directory"
else
    bad "oversized job grants only its staged prompt directory"
fi
if grep -Fq "$TMP/logs/oversized/staged/full-prompt.txt" "$TMP/oversized.prompt"; then
    ok "oversized dispatch points agy at the complete staged prompt"
else
    bad "oversized dispatch points agy at the complete staged prompt"
fi
if [[ "$(<"$TMP/oversized.stage-result")" == "blocked" ]]; then
    ok "oversized staged prompt is read-only during agy execution"
else
    bad "oversized staged prompt is read-only during agy execution"
fi
expect_print_last "oversized prompt keeps --print and its value last" "$TMP/oversized.argv"

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
