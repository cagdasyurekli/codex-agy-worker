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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="${AGY_WORKER_SCHEMA:-$SCRIPT_DIR/schemas/worker-result.schema.json}"
LOG_DIR="${AGY_WORKER_LOG_DIR:-$SCRIPT_DIR/logs}"

# Model tiering (rec #10). Bulk work should NOT burn pro/opus quota; those groups are
# exhaustible and agy returns empty when they are — indistinguishable from any other
# silent-empty failure, which is why the cheap tier is the default.
tier="${AGY_WORKER_TIER:-bulk}"
case "$tier" in
    bulk)    model="gemini-3.6-flash-medium" ;;
    cheap)   model="gemini-3.6-flash-low" ;;
    hard)    model="gemini-3.1-pro-high" ;;
    hardest) model="claude-opus-4-6-thinking" ;;
    default) model="" ;;                      # use whatever agy's own /model UI picked
    *)       model="$tier" ;;                 # explicit model label passthrough
esac

mode="${AGY_WORKER_MODE:-plan}"               # plan | accept-edits  (rec: plan is the safe default)
print_timeout="${AGY_WORKER_TIMEOUT:-5m0s}"
max_attempts="${AGY_WORKER_MAX_ATTEMPTS:-2}"  # bounded retries, then fail closed (rec #11)
job_id="${AGY_WORKER_JOB_ID:-job-$$}"

usage() {
    cat >&2 <<'EOF'
usage: agy-worker.sh [--workdir DIR] [--persona NAME] [--mode plan|accept-edits] [--tier bulk|cheap|hard|hardest]
                     [--add-dir DIR]... [--allow-slash-commands]
       ... task prompt on stdin ...

Emits the schema-valid result envelope on stdout. Non-zero exit means the job failed;
stdout is then NOT a valid envelope. Artifacts land in $AGY_WORKER_LOG_DIR.

Exit codes: 0 ok · 2 no prompt · 3 empty output (agy silent-empty) · 4 schema invalid
            5 agy nonzero exit · 6 permission gate hit
EOF
    exit 64
}

workdir="$PWD"
persona=""
extra_dirs=()
# Injection control (rec #8): worker prompts routinely embed repo content, and a
# "/skill ..." string inside that content would otherwise expand as a real command.
# Default OFF; only a caller who controls the whole prompt should re-enable it.
disable_slash=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workdir) workdir="$2"; shift 2 ;;
        # Persona by PROMPT INJECTION, not by --agent. Measured 2026-08-01: passing
        # --agent silently disables --json-schema enforcement (result.structured_output
        # comes back null and the worker answers in prose), which breaks the entire
        # driver contract. agy also accepts any --agent name without error, so a typo
        # yields a default worker that believes it is a specialist. Inlining the
        # persona body keeps structured output working.
        --persona) persona="$2"; shift 2 ;;
        --mode) mode="$2"; shift 2 ;;
        --tier) tier="$2"; shift 2 ;;
        --add-dir) extra_dirs+=("$2"); shift 2 ;;
        --allow-slash-commands) disable_slash=0; shift ;;
        -h|--help) usage ;;
        *) echo "agy-worker.sh: unknown arg: $1" >&2; usage ;;
    esac
done

mkdir -p "$LOG_DIR"
# agy operates on its CWD as the workspace, so the dispatcher must actually move
# there. LOG_DIR is resolved to an absolute path first: it defaults to a path under
# SCRIPT_DIR, but a caller-supplied relative AGY_WORKER_LOG_DIR would otherwise
# silently follow us into the worktree and scatter artifacts.
LOG_DIR="$(cd "$LOG_DIR" && pwd)"
[[ -d "$workdir" ]] || { echo "agy-worker.sh: --workdir not a directory: $workdir" >&2; exit 64; }
cd "$workdir"

stdout_file="$LOG_DIR/$job_id.stream.ndjson"
stderr_file="$LOG_DIR/$job_id.stderr"
prompt_file="$LOG_DIR/$job_id.prompt.txt"

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
- Report tests honestly in tests_run. The driver re-runs them. A false "passed"
  is the single worst outcome here.
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
        cmd+=(--add-dir "$LOG_DIR")
        cmd+=(--print "$PREAMBLE
Read the file '$prompt_file' and execute the instructions in it as your task. You may
read ONLY that file. Do not summarize it; perform it. Return the JSON envelope inline.")
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
    python3 - "$stdout_file" <<'PY'
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
if str(result.get("status", "")).upper() not in ("SUCCESS", ""):
    print(f"agy-worker: agy reported status={result.get('status')}", file=sys.stderr)

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
    "${cmd[@]}" > "$stdout_file" 2> "$stderr_file" < /dev/null
    agy_rc=$?
    classify "$agy_rc"
    verdict=$?
    set -e

    if (( verdict == 6 )); then
        exit 6                       # permission gate: retrying reproduces it exactly
    fi
    if (( verdict == 0 )); then
        if extract_envelope; then
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
