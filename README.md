# codex-agy-worker

Delegate bulk coding work from **Codex CLI** to **Antigravity CLI (`agy`)** — then
prove the work is correct before accepting it.

Two shell scripts and Python 3. No Node, no MCP daemon, no polling loop.

```bash
# Codex dispatches a bounded job to agy...
echo "$TASK" | AGY_WORKER_MODE=accept-edits ./agy-worker.sh \
    --workdir "$WT" --add-dir "$WT" > envelope.json

# ...then independently verifies it, rather than believing the report.
./qa-gate.sh --envelope envelope.json --repo "$WT" --base HEAD \
    --verify "cd $WT && pytest -q"
```

---

## Why another one of these?

There are already several Codex→agy delegators — [codex-agy-delegator](https://github.com/swjturay/codex-agy-delegator),
[codex-antigravity-bridge](https://github.com/Common-ka/codex-antigravity-bridge),
[agy-mcp](https://github.com/Boulea7/agy-mcp), [antigravity-cli-mcp](https://github.com/topics/agy),
and [antigravity-for-claude-code](https://github.com/VKirill/antigravity-for-claude-code).
Most are MCP servers; several are more featureful than this one. If you want async
job polling, Windows support, or multiple worker backends, use one of those.

**This one exists for a single reason: it assumes the worker may be wrong or dishonest,
and checks.** The worker's JSON report is treated as a *claim*, never as evidence. The
gate re-derives the truth from the repository itself:

| The worker... | Gate | Exit |
|---|---|---|
| edits files it never declared | diffs the repo, compares to `files_changed` | `10` |
| declares files it never touched | same check, other direction | `10` |
| reports a failing test as passing | **re-runs every claimed test** | `11` |
| returns a malformed envelope | schema/field check | `12` |
| claims completion, changed nothing | diff is empty | `13` |
| makes a plausible but wrong fix | **runs the driver's own `--verify` command** | `14` |

That last row is the important one. `--verify` runs a command *you* supply, regardless
of what the worker claimed — because a worker that reports `tests_run: []` would
otherwise be accepted having proven nothing.

All of the above are covered by an offline test suite (`./tests/test-qa-gate.sh`,
13 tests, no agy or network required) that asserts against deliberately dishonest
envelopes.

---

## Install

```bash
git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker
./install.sh          # installs the Codex skill only; touches nothing else
./tests/test-qa-gate.sh
```

Requires `agy` (Antigravity CLI) on `PATH`, `git`, `python3`, and bash.
Tested on macOS with agy 1.1.9 and codex-cli 0.146.0. Not tested on Windows.

### Codex sandbox settings — required

agy starts a local language server and writes state under `~/.gemini`. Under Codex's
default `workspace-write` sandbox it fails with **exit 5 and empty stderr**. Add to
`~/.codex/config.toml`:

```toml
[sandbox_workspace_write]
network_access = true
```

and run Codex with `--add-dir ~/.gemini`. **Both** are needed — the writable dir alone
still fails, because the blocker is the socket bind, not the file write.

---

## Usage

```bash
WT=/tmp/agy-job-$$
git worktree add "$WT" HEAD                 # never let the worker touch your tree

echo "Fix the trailing-hyphen bug in $WT/slugify.py.
Edit ONLY that file. Use your file tools on that absolute path.
Do NOT run shell commands — they execute in a scratch directory, not this repo.
The driver runs the tests; report tests_run as an empty array." |
  AGY_WORKER_MODE=accept-edits ./agy-worker.sh --workdir "$WT" --add-dir "$WT" > env.json

./qa-gate.sh --envelope env.json --repo "$WT" --base HEAD \
    --verify "cd $WT && python3 run_tests.py"

git worktree remove --force "$WT"
```

### Two rules that make it work

1. **Keep the worker off the shell.** Under agy's sandbox, its *shell* tools run in
   `~/.gemini/antigravity-cli/scratch`, **not your repo** — a worker asked to survey a
   repo will run `ls` and truthfully report an empty directory. Its *file* tools do
   reach the real target. Let the worker edit files; the driver owns every command.
2. **Absolute paths + `--add-dir`.** agy has no reliable notion of "the current
   directory" in print mode. Name the absolute path in the task text *and* pass
   `--add-dir`. With only one, the worker works on the wrong thing.

### Personas

`--persona repo-inventory|diff-reviewer|bulk-test-writer` inlines a role brief from
`agents/`. Personas measurably change behaviour: with `repo-inventory`, an
under-specified job returned `status: "blocked"`, `requires_human: true` — where the
same job without a persona confidently "surveyed" an empty scratch directory.

Personas are injected as prompt text, **not** via agy's `--agent` flag, because
`--agent` silently disables `--json-schema` enforcement (see below).

### Options

| Env | Default | |
|---|---|---|
| `AGY_WORKER_MODE` | `plan` | `plan` (read-only) or `accept-edits` |
| `AGY_WORKER_TIER` | `bulk` | `cheap`/`bulk`/`hard`/`hardest`/`default`, or a model label |
| `AGY_WORKER_TIMEOUT` | `5m0s` | passed to `--print-timeout` |
| `AGY_WORKER_MAX_ATTEMPTS` | `2` | bounded retries, then fail closed |

Worker exits: `0` ok · `2` no prompt · `3` empty output · `4` schema invalid ·
`5` agy failed · `6` permission gate.

---

## agy 1.1.9 behaviour worth knowing

Measured on macOS, 2026-08-01. Run `./ground-truth.sh` to regenerate against your own
install rather than trusting this list.

- **`--print` must be built last.** The prompt is its argument value; agy ignores stdin
  in print mode and will read the next flag as the message. `agy --print --sandbox "x"`
  sends the literal string `--sandbox` as the prompt.
- **Exit 0 does not mean success.** agy exits 0 with empty stdout when a tool needed a
  permission headless mode cannot prompt for. The reason goes to stderr only.
- **`--agent` silently disables `--json-schema`.** `result.structured_output` comes back
  null and the worker answers in prose. agy also accepts *any* `--agent` name without
  error, so a typo yields a default worker that believes it is a specialist.
- **Auth is intermittent.** A run can fail into an interactive OAuth prompt and the
  identical next run succeeds. Hence bounded retries.
- **`stream-json` shape:** `init` → repeated `step_update` → one `result`. The answer is
  at `result.structured_output`. `result.json_schema` is the *echoed schema* — a naive
  key-matching parser grabs that and hands you back your own schema.
- **~25k input tokens floor per invocation**, regardless of task size. Batch work; many
  tiny jobs are disproportionately expensive.
- **Unknown subcommands exit 0** and print usage. `agy run`, `agy exec`, `agy auth` do
  not exist. Never probe for a subcommand by exit code.
- **Headless permission gating:** `~/.gemini/antigravity-cli/settings.json` →
  `permissions.allow`, entries as `command(<name>)`. Interactive agy prompts for an
  unlisted command; `agy -p` cannot, so it hard-fails. Under `--sandbox`, shell commands
  additionally need `unsandboxed(<target>)`. Prefer narrow allow-rules over
  `--dangerously-skip-permissions`, which approves every tool for the whole run.
- **Agent files need YAML frontmatter** and live flat at
  `~/.gemini/config/agents/<name>.md`. A workspace `.agents/agents/<name>/agent.md` is
  not discovered.

### A note on where these came from

This project began as multi-model research into agy's own interface. The models that
ran *on agy* confidently invented `agy run`, `--headless`, `--slim`, and an
`agy auth status --json` OAuth introspection endpoint. None exist. The models that
shelled out to `agy --help` got it right.

That asymmetry is the reason this tool verifies instead of trusting, and the reason
`ground-truth.sh` exists: if you have an agent author agy skills, feed it live CLI
output, not its own recollection.

---

## Layout

```
agy-worker.sh                 dispatch a job, return a schema-valid envelope
qa-gate.sh                    verify an envelope against the repo — the evidence
ground-truth.sh               dump live agy facts for skill authoring
schemas/worker-result.*.json  the worker contract
agents/*.md                   personas, inlined via --persona
codex-skill/SKILL.md          the Codex skill installed by ./install.sh
tests/test-qa-gate.sh         offline adversarial suite
```

## Limitations

- No async/polling; jobs are synchronous and bounded by `--print-timeout`.
- Not tested on Windows.
- Single worker backend (agy). No Claude Code / Codex worker support.
- `bulk-test-writer` and `diff-reviewer` personas ship untested against real jobs;
  only `repo-inventory` has been exercised.
- Headless skill expansion (`agy -p "/skill-name ..."`) is untested.

## License

MIT — see [LICENSE](LICENSE).
