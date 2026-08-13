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
#   * Auth can be intermittent and is not sandbox-dependent.  The dispatcher never
#     retries it automatically: a caller may explicitly resume or restart instead.
#   * Under --sandbox, SHELL commands need an `unsandboxed(<target>)` allow-rule; a
#     `command(<name>)` rule alone is NOT enough. But a worker editing files via its
#     FILE tools needs neither — a full accept-edits job was verified working with no
#     unsandboxed grant. Keep workers off the shell; the driver owns verification.
#   * Therefore: exit code 0 proves nothing. Empty stdout is a FAILURE. See classify().
set -euo pipefail

# Prompts, streams, stderr, and envelopes can contain private repository content.
# Create dispatcher-owned artifacts under a private mask regardless of the caller's
# umask. The local supervisor restores this exact mask only in the agy child, so
# target-file behavior remains caller-controlled without exposing controller state.
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
idle_timeout="${AGY_WORKER_IDLE_TIMEOUT:-10m}"
hard_timeout="${AGY_WORKER_HARD_TIMEOUT:-2h}"
max_runtime="${AGY_WORKER_MAX_RUNTIME:-12h}"
notice_interval="${AGY_WORKER_NOTICE_INTERVAL:-30m}"
legacy_timeout_seen=0
if [[ -n "${AGY_WORKER_TIMEOUT+x}" ]]; then
    legacy_timeout_seen=1
    hard_timeout="$AGY_WORKER_TIMEOUT"
fi
job_env_seen=0; job_id=""
if [[ -n "${AGY_WORKER_JOB_ID+x}" ]]; then job_env_seen=1; job_id="$AGY_WORKER_JOB_ID"; fi
dispatch_action="run"
case "${1:-}" in
    run|start|status|wait|result|extend|cancel|resume|restart)
        dispatch_action="$1"; shift ;;
esac

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
                     [--idle-timeout 10m] [--hard-timeout 2h]
                     [--max-runtime 12h]
                     [--add-dir DIR]... [--allow-slash-commands]
       ... task prompt on stdin ...

       agy-worker.sh start [run options] ... task prompt on stdin ...
       agy-worker.sh status|result|resume|restart --job-id JOB
       agy-worker.sh wait --job-id JOB --after-state-sha SHA [--timeout 60s]
       agy-worker.sh cancel --job-id JOB --approve-state-sha SHA
       agy-worker.sh extend --job-id JOB --approve-state-sha SHA --by 2h

Emits the schema-valid result envelope on stdout. Non-zero exit means the job failed;
stdout is then NOT a valid envelope. Artifacts land in $AGY_WORKER_LOG_DIR.

Exit codes: 0 ok · 2 no prompt · 3 empty output · 4 schema invalid · 5 unclassified agy failure
            6 permission gate · 7 compatibility review · 8 compatibility evidence unavailable
            9 idle timeout · 16 hard deadline · 17-19 reserved for version-bound
            provider/auth evidence · 20 status unavailable · 21 resume failed
            22 cancelled · 23 output oversized · 64 invalid usage
EOF
    exit 64
}

if [[ "$dispatch_action" != "run" && "$dispatch_action" != "start" ]]; then
    control_job="$job_id"
    control_job_seen=0
    control_state_sha=""
    control_after_sha=""
    control_timeout="60s"
    control_by=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --job-id)
                [[ $# -ge 2 && $control_job_seen -eq 0 ]] || usage
                control_job="$2"; control_job_seen=1; shift 2 ;;
            --approve-state-sha)
                [[ $# -ge 2 && -z "$control_state_sha" ]] || usage
                control_state_sha="$2"; shift 2 ;;
            --after-state-sha)
                [[ $# -ge 2 && -z "$control_after_sha" ]] || usage
                control_after_sha="$2"; shift 2 ;;
            --timeout)
                [[ $# -ge 2 && "$control_timeout" == "60s" ]] || usage
                control_timeout="$2"; shift 2 ;;
            --by)
                [[ $# -ge 2 && -z "$control_by" ]] || usage
                control_by="$2"; shift 2 ;;
            -h|--help) usage ;;
            *) echo "agy-worker.sh: invalid $dispatch_action argument: $1" >&2; usage ;;
        esac
    done
    case "$control_job" in
        ''|.|..|*[!A-Za-z0-9._-]*) echo "agy-worker.sh: invalid job ID" >&2; exit 64 ;;
    esac
    [[ -d "$LOG_DIR" ]] || { echo "agy-worker.sh: log root is unavailable" >&2; exit 64; }
    validate_log_root "$LOG_DIR" || {
        echo "agy-worker.sh: log root must be an owner-owned, non-writable real directory" >&2
        exit 64
    }
    LOG_DIR="$(CDPATH= cd -- "$LOG_DIR" 2>/dev/null && pwd -P)" || exit 64
    job_dir="$LOG_DIR/$control_job"
    supervisor=(python3 -I -S -B "$SCRIPT_DIR/scripts/agy_dispatch.py" "$dispatch_action" --job-dir "$job_dir")
    case "$dispatch_action" in
        wait)
            [[ -n "$control_after_sha" ]] || usage
            supervisor+=(--after-state-sha "$control_after_sha" --timeout "$control_timeout") ;;
        cancel)
            [[ -n "$control_state_sha" ]] || usage
            supervisor+=(--approve-state-sha "$control_state_sha") ;;
        extend)
            [[ -n "$control_state_sha" && -n "$control_by" ]] || usage
            supervisor+=(--approve-state-sha "$control_state_sha" --by "$control_by") ;;
        resume|restart)
            [[ -n "$control_state_sha" ]] || usage
            supervisor+=(--approve-state-sha "$control_state_sha") ;;
    esac
    exec "${supervisor[@]}"
fi

workdir="$PWD"
persona=""
extra_dirs=()
tier_cli_seen=0; tier_cli_value=""
model_cli_seen=0; model_cli_value=""
effort_cli_seen=0; effort_cli_value=""
literal_cli_seen=0; literal_cli_value=""
idle_cli_seen=0; hard_cli_seen=0; max_cli_seen=0
job_cli_seen=0
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
        --idle-timeout)
            [[ $# -ge 2 ]] || usage
            (( idle_cli_seen == 0 )) || { echo "agy-worker.sh: repeated --idle-timeout" >&2; exit 64; }
            idle_cli_seen=1; idle_timeout="$2"; shift 2 ;;
        --hard-timeout)
            [[ $# -ge 2 ]] || usage
            (( hard_cli_seen == 0 )) || { echo "agy-worker.sh: repeated --hard-timeout" >&2; exit 64; }
            hard_cli_seen=1; hard_timeout="$2"; shift 2 ;;
        --max-runtime)
            [[ $# -ge 2 ]] || usage
            (( max_cli_seen == 0 )) || { echo "agy-worker.sh: repeated --max-runtime" >&2; exit 64; }
            max_cli_seen=1; max_runtime="$2"; shift 2 ;;
        --job-id)
            [[ $# -ge 2 ]] || usage
            (( job_cli_seen == 0 )) || { echo "agy-worker.sh: repeated --job-id" >&2; exit 64; }
            job_cli_seen=1; job_id="$2"; shift 2 ;;
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

if (( job_cli_seen == 0 && job_env_seen == 0 )); then
    job_id="job-$(python3 -I -S -B - <<'PY'
import secrets
print(secrets.token_hex(16))
PY
)" || { echo "agy-worker.sh: could not generate an opaque job ID" >&2; exit 64; }
fi

if (( legacy_timeout_seen && hard_cli_seen )); then
    echo "agy-worker.sh: --hard-timeout conflicts with deprecated AGY_WORKER_TIMEOUT" >&2
    exit 64
fi
if (( legacy_timeout_seen )); then
    echo "agy-worker.sh: warning: AGY_WORKER_TIMEOUT is deprecated; use --hard-timeout" >&2
fi

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
if [[ -n "${AGY_WORKER_MAX_ATTEMPTS+x}" && "$AGY_WORKER_MAX_ATTEMPTS" != "1" ]]; then
    echo "agy-worker.sh: automatic retries were removed; AGY_WORKER_MAX_ATTEMPTS must be 1" >&2
    exit 64
fi
case "$job_id" in
    ''|.|..|*[!A-Za-z0-9._-]*) echo "agy-worker.sh: invalid AGY_WORKER_JOB_ID: $job_id" >&2; exit 64 ;;
esac
if [[ "$mode" != "plan" && ( "$persona" == "repo-inventory" || "$persona" == "diff-reviewer" ) ]]; then
    echo "agy-worker.sh: persona '$persona' is read-only and requires --mode plan" >&2
    exit 64
fi

duration_seconds() {
    python3 -I -S -B - "$1" <<'PY'
import re
import sys
match = re.fullmatch(r"([1-9][0-9]*)(s|m|h)", sys.argv[1])
if match is None:
    raise SystemExit(64)
factor = {"s": 1, "m": 60, "h": 3600}[match.group(2)]
value = int(match.group(1)) * factor
if value > 7 * 24 * 3600:
    raise SystemExit(64)
print(value)
PY
}
idle_seconds="$(duration_seconds "$idle_timeout")" || { echo "agy-worker.sh: invalid idle timeout" >&2; exit 64; }
hard_seconds="$(duration_seconds "$hard_timeout")" || { echo "agy-worker.sh: invalid hard timeout" >&2; exit 64; }
max_seconds="$(duration_seconds "$max_runtime")" || { echo "agy-worker.sh: invalid max runtime" >&2; exit 64; }
notice_seconds="$(duration_seconds "$notice_interval")" || { echo "agy-worker.sh: invalid notice interval" >&2; exit 64; }
if (( idle_seconds > hard_seconds || hard_seconds > max_seconds )); then
    echo "agy-worker.sh: require idle-timeout <= hard-timeout <= max-runtime" >&2
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
    cmd=(agy --sandbox --mode "$mode" --print-timeout "${max_seconds}s")
    cmd+=(--output-format stream-json --json-schema "$SCHEMA")
    [[ -n "$model" ]] && cmd+=(--model "$model")
    if (( disable_slash )) && [[ "$mode" != "plan" ]]; then
        cmd+=(--disable-slash-commands)
    fi
    for d in ${extra_dirs+"${extra_dirs[@]}"}; do cmd+=(--add-dir "$d"); done
    # argv ceiling: MAX_ARG_STRLEN is ~128KiB of BYTES. Oversized prompts get staged
    # to the cached file and agy is pointed at it instead of failing the job.
    local LC_ALL=C
    stage_used=0
    if [[ "$mode" == "plan" ]] || (( ${#full_prompt} > 100000 )); then
        # The automatic out-of-repo root contains only one read-only staged prompt.
        # Logs and envelopes remain outside agy's granted roots.
        restore_staged_permissions
        mkdir -p "$staged_dir"
        printf '%s' "$full_prompt" > "$staged_prompt_file"
        chmod 0444 "$staged_prompt_file"
        chmod 0555 "$staged_dir"
        stage_used=1
        cmd+=(--add-dir "$staged_dir")
        cmd+=(--print "Read '$staged_prompt_file' as the complete prompt, including its
output contract, persona, and task. Follow it exactly. The staged job directory is
read-only context; target files named in that prompt remain readable and editable
according to --mode and --add-dir. Return the JSON envelope inline.")
    else
        cmd+=(--print "$full_prompt")   # ALWAYS last, prompt as the value
    fi
}

# --- hand one frozen command to the common foreground/background supervisor ---
build_cmd
command_file="$job_dir/dispatch-command.json"
stage_dir_arg=""; stage_file_arg=""
if (( stage_used )); then
    stage_dir_arg="$staged_dir"; stage_file_arg="$staged_prompt_file"
fi
agy_version="$(<"$SCRIPT_DIR/compat/agy-verified-version.txt")"
python3 -I -S -B - "$command_file" "$job_id" "$workdir" "$agy_version" \
    "$idle_seconds" "$hard_seconds" "$max_seconds" "$notice_seconds" \
    "$stage_dir_arg" "$stage_file_arg" "$CALLER_UMASK" "${cmd[@]}" <<'PY'
import json
import os
from pathlib import Path
import sys

(
    output, job_id, workdir, agy_version, idle, hard, maximum, notice,
    stage_dir, stage_file, child_umask, *argv
) = sys.argv[1:]
if not isinstance(child_umask, str) or len(child_umask) not in (3, 4) or any(ch not in "01234567" for ch in child_umask):
    raise SystemExit(64)
value = {
    "schema_version": 1,
    "kind": "agy-worker-dispatch-command",
    "job_id": job_id,
    "workdir": workdir,
    "argv": argv,
    "agy_version": agy_version,
    "idle_seconds": int(idle),
    "hard_seconds": int(hard),
    "max_seconds": int(maximum),
    "notice_seconds": int(notice),
    "stage_dir": stage_dir or None,
    "stage_file": stage_file or None,
    "child_umask": child_umask,
    "resume_prompt": (
        "Continue the existing bounded task from its retained conversation. "
        "Do not repeat completed work. Return only the final schema-valid envelope."
    ),
}
raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
try:
    os.fchmod(descriptor, 0o600)
    os.write(descriptor, raw)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY

# From this point the controller owns staged-file modes and child cleanup.
restore_staged_permissions
trap - EXIT HUP INT TERM
exec python3 -I -S -B "$SCRIPT_DIR/scripts/agy_dispatch.py" \
    "$dispatch_action" --job-dir "$job_dir"
