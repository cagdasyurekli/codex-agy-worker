# codex-agy-worker — agent notes

**Public README is `README.md`; keep it in sync with any behavioural findings here.**

Codex (or any driver) dispatches bounded jobs to `agy` as a bulk-execution worker;
the driver independently verifies the result. Built 2026-08-01 from multi-model research into agy's real interface; the
findings are summarised in `README.md`.

## Design premise

The worker's self-report is a **claim**, never evidence. `qa-gate.sh` re-derives the
truth from the repo itself. This is not defensive pessimism — it is the measured
result of the research: when agy was asked to describe its own CLI it invented
`agy run`, `--headless`, `--slim` and an `agy auth status --json` endpoint, none of
which exist. A model's account of its own behaviour is not a source of truth.

## Parts

| File | Role |
|---|---|
| `agy-worker.sh` | Dispatch a job to agy, return a schema-valid envelope on stdout |
| `schemas/worker-result.schema.json` | The worker contract (shape only) |
| `qa-gate.sh` | Independently verify an envelope against the repo. The driver's evidence |
| `ground-truth.sh` | Emit live agy facts. Feed this to any agent authoring agy skills |
| `agents/<name>.md` | Worker personas, inlined via `--persona` (not installed globally) |

## Usage

```bash
WT=/path/to/worktree
echo "Fix X in $WT/file.py. Use file tools only; do not run shell commands." |
  AGY_WORKER_MODE=accept-edits ./agy-worker.sh \
    --persona bulk-test-writer --workdir "$WT" --add-dir "$WT" > envelope.json

./qa-gate.sh --envelope envelope.json --repo "$WT" --base HEAD \
    --verify "cd $WT && <your test command>"
```

`--verify` is what makes the gate trustworthy: without it, acceptance rests on the
worker's own `tests_run` claims, and a worker reporting `tests_run: []` runs nothing.

Env: `AGY_WORKER_TIER` (bulk|cheap|hard|hardest|default), `AGY_WORKER_MODE` (plan|accept-edits),
`AGY_WORKER_TIMEOUT`, `AGY_WORKER_MAX_ATTEMPTS`, `AGY_WORKER_JOB_ID`.

Exit codes — worker: 0 ok · 2 no prompt · 3 empty output · 4 schema invalid · 5 agy failed · 6 permission gate.
Gate: 0 accept · 10 scope violation · 11 false test claim · 12 malformed · 13 claimed work, changed nothing · 14 driver verification failed.

## Verified facts about agy 1.1.9 (empirical, 2026-08-01)

Re-run `./ground-truth.sh` rather than trusting this list if it has aged.

- **`--print` must be built LAST.** The prompt is its argument value; agy ignores
  stdin in print mode and will read the next flag as the message.
- **Exit 0 does not mean success.** agy exits 0 with empty stdout when a tool needed
  a permission headless mode cannot prompt for. Real reason goes to stderr only.
- **`command(<name>)` is not enough under `--sandbox`.** Shell commands additionally
  need an `unsandboxed(<target>)` allow-rule — but see "The two rules" below: the
  pipeline avoids needing this entirely.
- **Auth is intermittent.** A run can fail into an interactive OAuth prompt and the
  identical next run succeeds. This is why `AGY_WORKER_MAX_ATTEMPTS` defaults to 2 —
  it fired on the very first end-to-end test.
- **`stream-json` shape:** `init` → repeated `step_update` → one `result`. The answer
  is at `result.structured_output`. `result.json_schema` is the echoed schema — a
  naive key-matching parser grabs that instead and returns your own schema back.
- **Cost floor ~25k input tokens per invocation** regardless of task size. Batch work;
  many tiny jobs are disproportionately expensive.
- **Unknown subcommands exit 0** and print usage. Never probe by exit code.
- **Agent files need YAML frontmatter.** The three pre-existing files in
  `~/.gemini/config/agents/` are prose with none, which is why `agy agents` lists
  nothing. Global layout is flat (`agents/<name>.md`); the `<name>/agent.md` directory
  form appears in session/brain workspaces.

## The two rules that make this work

**1. Keep workers off the shell.** Under `--sandbox`, agy's shell tools execute in
`~/.gemini/antigravity-cli/scratch`, NOT your repo — a worker asked to survey a repo
ran `ls` and truthfully reported an empty directory. File tools (`view_file`,
`list_dir`, `write_to_file`) do reach the real target. So workers edit and read via
file tools; the driver owns every shell command. This also sidesteps `unsandboxed`
entirely — a full `accept-edits` job was verified with no such grant.

**2. Always give absolute paths + `--add-dir`.** agy has no reliable notion of "the
current repo" in print mode. `cd`-ing the wrapper is not enough. Name the absolute
path in the prompt AND pass `--add-dir <path>`. Without both, the worker either
surveys the scratch dir or correctly reports itself blocked.

## `--persona`, not `--agent`

`--agent` is a trap and the wrapper no longer uses it:
- Passing `--agent` **silently disables `--json-schema` enforcement** —
  `result.structured_output` returns null and the worker answers in prose, breaking
  the driver contract. Verified by running the identical job with and without it.
- agy accepts **any** `--agent` name with no error and exit 0, so a typo yields a
  default worker that believes it is a specialist.

`--persona NAME` instead inlines `agents/NAME.md` (frontmatter stripped) into the
prompt. Structured output keeps working, and the persona demonstrably changes
behaviour: with `repo-inventory` inlined, an under-specified job returned
`status: "blocked"` / `requires_human: true` — where the same job without a persona
confidently "surveyed" an empty scratch directory instead.

The `tools:` list in each persona's frontmatter is documentation only. Real tool
access is governed by agy's permissions; a prompt cannot restrict it.

## Agent-file discovery (measured, in case you ever do want `--agent`)

| Location | Listed by `agy agents`? |
|---|---|
| `~/.gemini/config/agents/<name>.md` + YAML frontmatter | ✅ yes |
| Workspace `.agents/agents/<name>/agent.md` | ❌ no |
| Prose file with no frontmatter | ❌ no |

The three files already in `~/.gemini/config/agents/` are prose with no frontmatter,
which is the whole reason `agy agents` reports nothing. Nothing was installed there;
that directory is exactly as it was found.

## Open decision (needs a human)

**`unsandboxed(<target>)` allow-rules — deliberately NOT granted.** They would let
agy run real shell commands, but per rule 1 above the pipeline does not need them,
and granting them removes the sandbox boundary. Only revisit if you find a job that
genuinely cannot be done with file tools plus driver-side verification.

## Not done

- `-p "/skill-name ..."` headless skill expansion is untested; the Codex-authors-agy-skills
  path (approach C in the research) depends on it.
- No cost/latency/success-rate measurements across job types beyond single runs.
- `bulk-test-writer` and `diff-reviewer` personas are written but have not been
  exercised on a real job — only `repo-inventory` has.
