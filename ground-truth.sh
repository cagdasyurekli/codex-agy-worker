#!/usr/bin/env bash
# ground-truth.sh — emit live, verified facts about this agy install.
#
# WHY THIS EXISTS (the core finding of the 2026-08-01 research):
# When agy was asked to research its own CLI, it confidently invented `agy run`,
# `--headless`, `--slim`, `--no-prompt`, `--workspace` and an `agy auth status --json`
# OAuth introspection endpoint. None exist. The Codex agents that shelled out to
# `agy --help` got it right. Conclusion: a model's memory of its own tooling is
# unreliable, so any agent AUTHORING agy skills must read this output first and
# treat it — not its own recollection — as ground truth.
#
# Feed the output of this script into the skill-authoring prompt.
set -euo pipefail

echo "# agy ground truth — generated $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
echo "## version"
agy --version 2>&1 || echo "(agy --version failed)"
echo
echo "## documented installed flags and subcommands"
agy --help 2>&1 || echo "(agy --help failed)"
echo
echo "## models available to --model"
agy models 2>&1 || echo "(agy models failed)"
echo
echo "## agents available to --agent"
agy agents 2>&1 || echo "(agy agents failed)"
echo
echo "## installed plugins"
agy plugin list 2>&1 || echo "(agy plugin list failed)"
echo
echo "## headless permission allowlist"
echo "Commands NOT in this list are auto-denied under 'agy -p' with no prompt."
python3 - <<'PY' 2>&1 || echo "(could not read settings.json)"
import json, os
p = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
d = json.load(open(p))
perms = d.get("permissions", {})
for key in ("allow", "ask", "deny"):
    print(f"{key}: {perms.get(key)}")
PY
echo
cat <<'EOF'
## verified behavioural facts (empirical, 2026-08-01, agy 1.1.9)

- The prompt is `--print`'s ARGUMENT VALUE. agy ignores stdin in print mode. With
  `--print` placed before other flags, agy reads the NEXT FLAG as the message.
  Therefore `--print` must always be built LAST.
- Exit code 0 does NOT mean success. agy exits 0 with EMPTY STDOUT when a tool it
  wanted required a permission headless mode cannot prompt for. The real reason
  appears only on stderr. Always check stripped stdout content, never just $?.
- Under `--sandbox`, running a shell command needs an `unsandboxed(<target>)`
  allow-rule. A `command(<name>)` rule alone is NOT sufficient.
- Authentication is INTERMITTENT: a run may fail with an interactive OAuth prompt
  and the identical next run succeeds. Bounded retry handles this; do not conclude
  from a single failure that agy is unauthenticated.
- `stream-json` event shape: {"event":"...","init":...}, then repeated
  {"event":"step_update",...}, then exactly one {"event":"result","result":{...}}.
  The schema-validated answer is at result.structured_output.
  result.json_schema is the ECHOED SCHEMA — do not mistake it for the answer.
- Baseline cost: a trivial no-tool job consumed ~24.8k input tokens. agy loads a
  large standing context per invocation regardless of task size, so many tiny jobs
  are far more expensive than one batched job.
- The observed unsupported examples `agy run`, `agy exec`, and `agy auth` print
  top-level usage and exit 0. Probe documented commands and their expected semantic
  content; never infer support from an exit code or generic usage text.
EOF
