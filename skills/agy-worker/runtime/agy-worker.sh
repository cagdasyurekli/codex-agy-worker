#!/usr/bin/env bash
# agy-worker.sh — dispatch a bounded job to agy and return a schema-valid result envelope.
#
# Forked from octo's agy-exec.sh (the prompt-delivery, force-inline and silent-empty
# handling below are its hard-won lessons, not speculation) and extended with the
# structured-output contract Codex needs to act as QA.
#
# VERIFIED ON THIS BOX 2026-08-01 (agy 1.1.9):
#   * The prompt is --print's ARGUMENT VALUE. agy ignores stdin in print mode, and
#     with --print placed before other flags it reads the NEXT FLAG as the message.
#     So --print is built LAST, always.
#   * Auth is INTERMITTENT, not sandbox-dependent: a run can fail into an interactive
#     OAuth prompt and the identical next run succeeds. That is what max_attempts is
#     for — it fired on the very first end-to-end test.
#   * Under --sandbox, SHELL commands need an `unsandboxed(<target>)` allow-rule; a
#     `command(<name>)` rule alone is NOT enough. But a worker editing files via its
#     FILE tools needs neither — a full accept-edits job was verified working with no
#     unsandboxed grant. Keep workers off the shell; the driver owns verification.
#   * Therefore: exit code 0 proves nothing. Empty stdout is a FAILURE. See classify().
set -euo pipefail

# Prompts, streams, stderr, and envelopes can contain private repository content.
# Create dispatcher-owned artifacts under a private mask regardless of the caller's
# umask. The agy child gets the caller's mask back so target-file behavior is stable.
CALLER_UMASK="$(umask)"
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${AGY_WORKER_SCHEMA:-$SCRIPT_DIR/schemas/worker-result.schema.json}"
LOG_DIR="${AGY_WORKER_LOG_DIR:-$SCRIPT_DIR/logs}"

# Selector values are presence-sensitive. An explicitly empty environment variable
# is not the same as an unset one, and CLI never silently overrides environment.
tier_env_seen=0; tier_env_value=""
model_env_seen=0; model_env_value=""
effort_env_seen=0; effort_env_value=""
if [[ -n "${AGY_WORKER_TIER+x}" ]]; then tier_env_seen=1; tier_env_value="$AGY_WORKER_TIER"; fi
if [[ -n "${AGY_WORKER_MODEL+x}" ]]; then model_env_seen=1; model_env_value="$AGY_WORKER_MODEL"; fi
if [[ -n "${AGY_WORKER_EFFORT+x}" ]]; then effort_env_seen=1; effort_env_value="$AGY_WORKER_EFFORT"; fi
mode="${AGY_WORKER_MODE:-plan}"               # plan | accept-edits  (rec: plan is the safe default)
print_timeout="${AGY_WORKER_TIMEOUT:-5m0s}"
max_attempts="${AGY_WORKER_MAX_ATTEMPTS:-2}"  # bounded retries, then fail closed (rec #11)
job_id="${AGY_WORKER_JOB_ID:-job-$$}"

validate_log_root() {
    python3 -B - "$1" <<'PY'
import os
import stat
import sys

try:
    metadata = os.lstat(sys.argv[1])
except OSError:
    raise SystemExit(1)
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

usage() {
    cat >&2 <<'EOF'
usage: agy-worker.sh [--workdir DIR] [--persona NAME] [--mode plan|accept-edits]
                     [--tier bulk|cheap|hard|hardest|default|MODEL]
                     [--model REVIEWED_MODEL [--effort low|medium|high]]
                     [--literal-model EXACT_SLUG]
                     [--add-dir DIR]... [--allow-slash-commands]
       ... task prompt on stdin ...

Emits the schema-valid result envelope on stdout. Non-zero exit means the job failed;
stdout is then NOT a valid envelope. Artifacts land in $AGY_WORKER_LOG_DIR.

Exit codes: 0 ok · 2 no prompt · 3 empty output (agy silent-empty) · 4 schema invalid
            5 agy nonzero exit · 6 permission gate hit · 7 compatibility review
            8 compatibility evidence unavailable · 64 invalid usage
EOF
    exit 64
}

workdir="$PWD"
persona=""
extra_dirs=()
tier_cli_seen=0; tier_cli_value=""
model_cli_seen=0; model_cli_value=""
effort_cli_seen=0; effort_cli_value=""
literal_cli_seen=0; literal_cli_value=""
# Injection control (rec #8): worker prompts routinely embed repo content, and a
# "/skill ..." string inside that content would otherwise expand as a real command.
# Default OFF; only a caller who controls the whole prompt should re-enable it.
disable_slash=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workdir) [[ $# -ge 2 ]] || usage; workdir="$2"; shift 2 ;;
        # Persona by PROMPT INJECTION, not by --agent. Measured 2026-08-01: passing
        # --agent silently disables --json-schema enforcement (result.structured_output
        # comes back null and the worker answers in prose), which breaks the entire
        # driver contract. agy also accepts any --agent name without error, so a typo
        # yields a default worker that believes it is a specialist. Inlining the
        # persona body keeps structured output working.
        --persona) [[ $# -ge 2 ]] || usage; persona="$2"; shift 2 ;;
        --mode) [[ $# -ge 2 ]] || usage; mode="$2"; shift 2 ;;
        --tier)
            [[ $# -ge 2 ]] || usage
            (( tier_cli_seen == 0 )) || { echo "agy-worker.sh: repeated --tier" >&2; exit 64; }
            tier_cli_seen=1; tier_cli_value="$2"; shift 2 ;;
        --model)
            [[ $# -ge 2 ]] || usage
            (( model_cli_seen == 0 )) || { echo "agy-worker.sh: repeated --model" >&2; exit 64; }
            model_cli_seen=1; model_cli_value="$2"; shift 2 ;;
        --literal-model)
            [[ $# -ge 2 ]] || usage
            (( literal_cli_seen == 0 )) || { echo "agy-worker.sh: repeated --literal-model" >&2; exit 64; }
            literal_cli_seen=1; literal_cli_value="$2"; shift 2 ;;
        --effort)
            [[ $# -ge 2 ]] || usage
            (( effort_cli_seen == 0 )) || { echo "agy-worker.sh: repeated --effort" >&2; exit 64; }
            effort_cli_seen=1; effort_cli_value="$2"; shift 2 ;;
        --add-dir) [[ $# -ge 2 ]] || usage; extra_dirs+=("$2"); shift 2 ;;
        --allow-slash-commands) disable_slash=0; shift ;;
        -h|--help) usage ;;
        *) echo "agy-worker.sh: unknown arg: $1" >&2; usage ;;
    esac
done

(( tier_cli_seen == 0 || tier_env_seen == 0 )) || {
    echo "agy-worker.sh: --tier conflicts with AGY_WORKER_TIER" >&2; exit 64;
}
(( model_cli_seen == 0 || model_env_seen == 0 )) || {
    echo "agy-worker.sh: --model conflicts with AGY_WORKER_MODEL" >&2; exit 64;
}
(( effort_cli_seen == 0 || effort_env_seen == 0 )) || {
    echo "agy-worker.sh: --effort conflicts with AGY_WORKER_EFFORT" >&2; exit 64;
}
tier_seen=$((tier_cli_seen + tier_env_seen))
model_seen=$((model_cli_seen + model_env_seen))
effort_seen=$((effort_cli_seen + effort_env_seen))
if (( literal_cli_seen > 0 && (tier_seen > 0 || model_seen > 0 || effort_seen > 0) )); then
    echo "agy-worker.sh: --literal-model conflicts with tier/model/effort selectors" >&2
    exit 64
fi
if (( tier_seen > 0 && (model_seen > 0 || effort_seen > 0) )); then
    echo "agy-worker.sh: explicit tier and model/effort selectors are mutually exclusive" >&2
    exit 64
fi
if (( effort_seen > 0 && model_seen == 0 )); then
    echo "agy-worker.sh: effort requires an explicit base model" >&2
    exit 64
fi
if (( literal_cli_seen > 0 )); then
    [[ -n "$literal_cli_value" ]] || { echo "agy-worker.sh: literal model must not be empty" >&2; exit 64; }
    literal_model="$literal_cli_value"; selection_kind="literal"
elif (( tier_seen > 0 )); then
    if (( tier_cli_seen )); then tier="$tier_cli_value"; tier_source="cli"
    else tier="$tier_env_value"; tier_source="environment"; fi
    [[ -n "$tier" ]] || { echo "agy-worker.sh: explicit tier must not be empty" >&2; exit 64; }
    selection_kind="tier"
elif (( model_seen > 0 )); then
    if (( model_cli_seen )); then user_model="$model_cli_value"; model_source="cli"
    else user_model="$model_env_value"; model_source="environment"; fi
    [[ -n "$user_model" ]] || { echo "agy-worker.sh: explicit model must not be empty" >&2; exit 64; }
    user_effort=""; effort_source=""
    if (( effort_seen > 0 )); then
        if (( effort_cli_seen )); then user_effort="$effort_cli_value"; effort_source="cli"
        else user_effort="$effort_env_value"; effort_source="environment"; fi
        [[ -n "$user_effort" ]] || { echo "agy-worker.sh: explicit effort must not be empty" >&2; exit 64; }
    fi
    selection_kind="model"
else
    tier="default"; tier_source="implicit-default"; selection_kind="tier"
fi

case "$persona" in
    ''|bulk-test-writer|repo-inventory|diff-reviewer) ;;
    *) echo "agy-worker.sh: invalid persona: $persona" >&2; exit 64 ;;
esac
case "$mode" in
    plan|accept-edits) ;;
    *) echo "agy-worker.sh: invalid mode: $mode" >&2; exit 64 ;;
esac
case "$max_attempts" in
    ''|*[!0-9]*|0) echo "agy-worker.sh: AGY_WORKER_MAX_ATTEMPTS must be a positive integer" >&2; exit 64 ;;
esac
case "$job_id" in
    ''|.|..|*[!A-Za-z0-9._-]*) echo "agy-worker.sh: invalid AGY_WORKER_JOB_ID: $job_id" >&2; exit 64 ;;
esac
if [[ "$mode" != "plan" && ( "$persona" == "repo-inventory" || "$persona" == "diff-reviewer" ) ]]; then
    echo "agy-worker.sh: persona '$persona' is read-only and requires --mode plan" >&2
    exit 64
fi

mkdir -p "$LOG_DIR" 2>/dev/null || {
    echo "agy-worker.sh: log root cannot be created safely" >&2
    exit 64
}
if ! validate_log_root "$LOG_DIR"; then
    echo "agy-worker.sh: log root must be an owner-owned, non-writable real directory" >&2
    exit 64
fi
# agy operates on its CWD as the workspace, so the dispatcher must actually move
# there. LOG_DIR is resolved to an absolute path first: it defaults to a path under
# SCRIPT_DIR, but a caller-supplied relative AGY_WORKER_LOG_DIR would otherwise
# silently follow us into the worktree and scatter artifacts.
if ! LOG_DIR="$(CDPATH= cd -- "$LOG_DIR" 2>/dev/null && pwd -P)"; then
    echo "agy-worker.sh: log root cannot be resolved safely" >&2
    exit 64
fi
[[ -f "$SCHEMA" ]] || { echo "agy-worker.sh: schema not found: $SCHEMA" >&2; exit 64; }
SCHEMA="$(cd "$(dirname "$SCHEMA")" && pwd)/$(basename "$SCHEMA")"
[[ -d "$workdir" ]] || { echo "agy-worker.sh: --workdir not a directory: $workdir" >&2; exit 64; }
workdir="$(cd "$workdir" && pwd -P)"
normalized_dirs=()
for d in ${extra_dirs+"${extra_dirs[@]}"}; do
    [[ -d "$d" ]] || { echo "agy-worker.sh: --add-dir not a directory: $d" >&2; exit 64; }
    resolved_dir="$(cd "$d" && pwd -P)"
    case "$resolved_dir/" in
        "$workdir/"*) normalized_dirs+=("$resolved_dir") ;;
        *)
            echo "agy-worker.sh: --add-dir must resolve inside the audited --workdir: $d" >&2
            exit 64 ;;
    esac
done
extra_dirs=()
if (( ${#normalized_dirs[@]} > 0 )); then
    extra_dirs=("${normalized_dirs[@]}")
fi
cd "$workdir"

job_dir="$LOG_DIR/$job_id"
if ! mkdir "$job_dir" 2>/dev/null; then
    echo "agy-worker.sh: job artifact path already exists or cannot be created: $job_id" >&2
    exit 64
fi
stdout_file="$job_dir/stream.ndjson"
stderr_file="$job_dir/stderr.txt"
prompt_file="$job_dir/task.txt"
full_prompt_file="$job_dir/full-prompt.txt"
envelope_file="$job_dir/envelope.json"
staged_dir="$job_dir/staged"
staged_prompt_file="$staged_dir/full-prompt.txt"
selection_file="$job_dir/selection.json"

# Resolve once before consuming the task. New direct selectors validate the exact
# portable matrix and installed agy version; legacy tiers preserve their old mapping.
selection_args=(--output "$selection_file")
if [[ "$selection_kind" == "tier" ]]; then
    selection_args+=(--tier "$tier" --tier-source "$tier_source")
elif [[ "$selection_kind" == "literal" ]]; then
    selection_args+=(--literal-model "$literal_model")
else
    selection_args+=(--model "$user_model" --model-source "$model_source")
    if [[ -n "$user_effort" ]]; then
        selection_args+=(--effort "$user_effort" --effort-source "$effort_source")
    fi
fi
set +e
model="$(python3 -B "$SCRIPT_DIR/scripts/model_selection.py" "${selection_args[@]}")"
selection_rc=$?
set -e
if (( selection_rc != 0 )); then
    exit "$selection_rc"
fi

restore_staged_permissions() {
    if [[ -d "$staged_dir" ]]; then
        chmod 0700 "$staged_dir" 2>/dev/null || true
        [[ ! -f "$staged_prompt_file" ]] || chmod 0600 "$staged_prompt_file" 2>/dev/null || true
    fi
}

handle_signal() {
    local signal="$1" status="$2"
    restore_staged_permissions
    trap - "$signal"
    kill -s "$signal" "$$"
    exit "$status"
}

trap 'restore_staged_permissions' EXIT
trap 'handle_signal HUP 129' HUP
trap 'handle_signal INT 130' INT
trap 'handle_signal TERM 143' TERM

# --- read the task -----------------------------------------------------------
# stdin is consumed once; cache it so a retry can replay it verbatim.
if [[ -t 0 ]]; then
    echo "agy-worker.sh: no task on stdin" >&2
    exit 2
fi
cat > "$prompt_file"
task="$(<"$prompt_file")"
# $(<file) strips trailing whitespace, so a whitespace-only payload would pass a
# byte-size check yet still hand agy an empty --print value. Check stripped content.
if [[ -z "${task//[[:space:]]/}" ]]; then
    echo "agy-worker.sh: empty task; refusing to dispatch a promptless worker" >&2
    exit 2
fi

# --- the contract preamble ---------------------------------------------------
# agy is agentic: given an analysis prompt it will happily write its answer to an
# internal "brain" artifact and return a stub. The envelope requirement plus this
# directive pin the answer to stdout where the driver can actually read it.
read -r -d '' PREAMBLE <<'EOF' || true
You are a bounded worker. Another agent (the driver) will independently verify
everything you claim, so inaccurate self-reporting is worse than admitting failure.

OUTPUT CONTRACT — non-negotiable:
- Your FINAL response must be a single JSON object matching the enforced schema.
- Do NOT write your answer to a file, artifact, or brain document.
- Do NOT reply "see the artifact" or reference an external document.
- List EVERY file you touched in files_changed. The driver diffs the repo; an
  omission reads as a scope violation and fails the job.
- Do NOT run shell commands or tests. The driver's environment is the only trusted
  execution context. Leave commands_run and tests_run as empty arrays.
- If a permission gate, missing tool, or ambiguity blocks you: set
  status="blocked", requires_human=true, and explain in open_questions.
  Do not silently work around it.

TASK FOLLOWS:
EOF

# Persona is prepended as text (see --persona note above). Strip YAML frontmatter:
# the `tools:` list is meaningless here — tool access is governed by agy's own
# permissions, not by anything we can assert in a prompt.
persona_text=""
if [[ -n "$persona" ]]; then
    persona_file="$SCRIPT_DIR/agents/$persona.md"
    [[ -f "$persona_file" ]] || { echo "agy-worker.sh: no such persona: $persona_file" >&2; exit 64; }
    persona_text="$(awk 'BEGIN{fm=0} /^---$/{fm++; next} fm>=2' "$persona_file")
"
fi

full_prompt="$PREAMBLE
$persona_text
$task"
printf '%s' "$full_prompt" > "$full_prompt_file"

# --- build the command -------------------------------------------------------
# --sandbox is deliberately unconditional: see the auth note in the header.
build_cmd() {
    cmd=(agy --sandbox --mode "$mode" --print-timeout "$print_timeout")
    cmd+=(--output-format stream-json --json-schema "$SCHEMA")
    [[ -n "$model" ]] && cmd+=(--model "$model")
    (( disable_slash )) && cmd+=(--disable-slash-commands)
    for d in ${extra_dirs+"${extra_dirs[@]}"}; do cmd+=(--add-dir "$d"); done
    # argv ceiling: MAX_ARG_STRLEN is ~128KiB of BYTES. Oversized prompts get staged
    # to the cached file and agy is pointed at it instead of failing the job.
    local LC_ALL=C
    if (( ${#full_prompt} > 100000 )); then
        # The automatic out-of-repo root contains only one read-only staged prompt.
        # Logs and envelopes remain outside agy's granted roots.
        restore_staged_permissions
        mkdir -p "$staged_dir"
        printf '%s' "$full_prompt" > "$staged_prompt_file"
        chmod 0444 "$staged_prompt_file"
        chmod 0555 "$staged_dir"
        cmd+=(--add-dir "$staged_dir")
        cmd+=(--print "Read '$staged_prompt_file' as the complete prompt, including its
output contract, persona, and task. Follow it exactly. The staged job directory is
read-only context; target files named in that prompt remain readable and editable
according to --mode and --add-dir. Return the JSON envelope inline.")
    else
        cmd+=(--print "$full_prompt")   # ALWAYS last, prompt as the value
    fi
}

# --- classify the outcome ----------------------------------------------------
# Exit 0 is not success. This is the whole point of the wrapper.
classify() {
    local rc=$1
    if grep -qiE 'permission that headless mode cannot prompt for' "$stderr_file" 2>/dev/null; then
        local want
        want=$(grep -oiE '"[a-z]+" permission' "$stderr_file" | head -1)
        echo "agy-worker.sh: BLOCKED — agy needs the ${want:-unknown} permission and headless mode cannot prompt." >&2
        echo "agy-worker.sh: add a narrow allow-rule to ~/.gemini/antigravity-cli/settings.json." >&2
        echo "agy-worker.sh: do NOT use --dangerously-skip-permissions; it approves every tool for the run." >&2
        return 6
    fi
    (( rc != 0 )) && return 5
    [[ -s "$stdout_file" ]] || return 3
    return 0
}

# --- extract the envelope from the NDJSON stream -----------------------------
# VERIFIED event shape (agy 1.1.9, captured 2026-08-01):
#   {"event":"...","conversation_id":...,"init":{...}}
#   {"event":"step_update","step_update":{...}}          (repeated)
#   {"event":"result","result":{conversation_id,status,response,duration_seconds,
#                               num_turns,structured_output,json_schema,usage}}
# The schema-validated instance is result.structured_output.
# NOTE: result.json_schema is the ECHOED SCHEMA. A naive "find the object whose keys
# match required" walker matches the schema's own `properties` block instead of the
# answer — that bug cost a run; hence the exact path below rather than a search.
extract_envelope() {
    python3 -B - "$stdout_file" <<'PY'
import json, sys
stream = sys.argv[1]
result = None
for line in open(stream, encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(evt, dict) and evt.get("event") == "result":
        result = evt.get("result")          # keep the last one
if not isinstance(result, dict):
    print("agy-worker: no terminal result event in stream", file=sys.stderr)
    sys.exit(4)

# agy's own run status, distinct from our envelope's status field.
if str(result.get("status", "")).upper() != "SUCCESS":
    print(f"agy-worker: agy reported non-success status={result.get('status')}",
          file=sys.stderr)
    sys.exit(4)

envelope = result.get("structured_output")
if not isinstance(envelope, dict):
    print("agy-worker: result carried no structured_output "
          "(schema not enforced or worker answered in prose)", file=sys.stderr)
    sys.exit(4)

# Cost/latency accounting (rec #10) — to stderr so stdout stays a clean envelope.
usage = result.get("usage") or {}
print(f"agy-worker: turns={result.get('num_turns')} "
      f"duration={result.get('duration_seconds')}s usage={json.dumps(usage)}",
      file=sys.stderr)

json.dump(envelope, sys.stdout, indent=2)
print()
PY
}

# --- run, with bounded retries and fail-closed -------------------------------
attempt=1
rc_final=1
while (( attempt <= max_attempts )); do
    build_cmd
    : > "$stdout_file"; : > "$stderr_file"
    set +e
    (
        umask "$CALLER_UMASK"
        exec "${cmd[@]}"
    ) > "$stdout_file" 2> "$stderr_file" < /dev/null
    agy_rc=$?
    restore_staged_permissions
    classify "$agy_rc"
    verdict=$?
    set -e

    if (( verdict == 6 )); then
        exit 6                       # permission gate: retrying reproduces it exactly
    fi
    if (( verdict == 0 )); then
        if extract_envelope > "$envelope_file" \
                && "$SCRIPT_DIR/scripts/validate-envelope.py" "$SCHEMA" "$envelope_file"; then
            cat "$envelope_file"
            exit 0
        fi
        echo "agy-worker.sh: attempt $attempt produced no schema-valid envelope" >&2
        rc_final=4
    else
        echo "agy-worker.sh: attempt $attempt failed (verdict=$verdict, agy_rc=$agy_rc)" >&2
        [[ -s "$stderr_file" ]] && head -3 "$stderr_file" >&2
        rc_final=$verdict
    fi
    (( attempt++ ))
done

echo "agy-worker.sh: failing closed after $max_attempts attempts (artifacts in $LOG_DIR)" >&2
exit "$rc_final"
