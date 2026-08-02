# codex-agy-worker

Delegate bulk coding work from **Codex CLI or another Agent Skills client** to
**Antigravity CLI (`agy`)** — then prove the driver-defined acceptance checks pass
before accepting it. The same verification-first workflow is packaged as an OpenAI
skills-only plugin and a Claude Code marketplace plugin.

Bash + Python 3 + git. No Node, no MCP daemon, no polling loop.

```bash
# Codex dispatches a bounded job to agy...
BASE="$(git -C "$WT" rev-parse HEAD)"       # capture before dispatch
echo "$TASK" | AGY_WORKER_MODE=accept-edits ./agy-worker.sh \
    --workdir "$WT" --add-dir "$WT" > envelope.json

# ...then independently verifies it, rather than believing the report.
./qa-gate.sh --envelope envelope.json --repo "$WT" --base "$BASE" \
    --only 'tests/**' --expect-edits \
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
| reports commands or tests on a completed candidate | treats them as untrusted data and executes none | `11` |
| returns a malformed envelope | validates the complete checked-in schema | `12` |
| claims completion, changed nothing under `--expect-edits` | diff is empty | `13` |
| makes a plausible but wrong fix | **runs the driver's own `--verify` command** | `14` |
| asks for a human or reports partial/failed/blocked | checks scope, then routes without accepting | `15` |
| changes files outside a driver-owned `--only` policy | rejects even if the worker declared them | `10` |

`--verify` is mandatory for acceptance. The gate never executes worker-supplied shell
text, and it hashes the Git diff plus every nontracked path—including ignored files—
before and after verification so a passing verifier cannot silently rewrite the
candidate.

The five offline suites need no agy process, network access, API key, or GitHub login.

---

## Install

```bash
git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker
./install.sh          # installs the Codex skill only; touches nothing else
for suite in tests/test-*.sh; do "$suite"; done
```

Requires `agy` (Antigravity CLI) on `PATH`, `git`, `python3`, and bash.
Tested on macOS with agy 1.1.9 and codex-cli 0.146.0. Not tested on Windows.

`skills/agy-worker/` is the one canonical, open-standard Agent Skill. `install.sh`
copies that bundle and writes a local runtime pointer; it does not rewrite the public
`SKILL.md`. Plugin-cache installs resolve the same Bash/Python/git runtime relative to
the package root.

The repository also contains marketplace metadata for direct testing and later public
submission. These commands install from the repository; they do **not** imply that a
public marketplace has approved the listing:

```text
# Codex: add the source, then inspect/install it in /plugins
codex plugin marketplace add cagdasyurekli/codex-agy-worker --ref main

# Claude Code
/plugin marketplace add cagdasyurekli/codex-agy-worker
/plugin install codex-agy-worker@codex-agy-worker
```

See [the marketplace runbook](docs/MARKETPLACE.md) for distribution status, platform
review prerequisites, and reviewer-ready positive/negative cases. The public landing
page source lives at `docs/index.md`; GitHub Pages and search-engine verification are
separate repository-owner actions. After Pages is live, submit the checked-in
`sitemap.xml` through Google Search Console; a repository file alone does not request
indexing.

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

With the config saved, launch an interactive session with:

```bash
codex --add-dir "$HOME/.gemini"
```

For a one-off `codex exec` invocation, pass both settings explicitly:

```bash
codex exec --sandbox workspace-write --add-dir "$HOME/.gemini" \
  -c 'sandbox_workspace_write.network_access=true' "<your task>"
```

---

## Use it from Codex

After `./install.sh`, start a new Codex session and ask in normal language:

> Use the agy-worker skill to add error-path tests for
> the parser modules under `/absolute/path/to/project/src/`. Allow changes only
> under `tests/`, verify with `python3 -m pytest -q tests/test_parser.py`, and
> preserve accepted work on a branch.

Codex should create an isolated worktree, dispatch agy, run the gate, inspect the
diff, and report evidence. You do not need to remember the shell interface.

Before the first dispatch for a repository, the skill identifies the paths in scope
and requires explicit approval for sending that task and any worker-read repository
content through agy to Google/Gemini, unless the user already approved that exact
transmission. Read [PRIVACY.md](PRIVACY.md) before use; support and project terms are
in [SUPPORT.md](SUPPORT.md) and [TERMS.md](TERMS.md).

## Manual end-to-end example

Keep the pipeline checkout and target repository explicit. The job branch matters:
it prevents accepted but uncommitted work from being destroyed during cleanup.
`bulk-test-writer` is still experimental: it has been exercised on a real task, but
has not yet produced an accepted real delivery.

```bash
PIPELINE=/absolute/path/to/codex-agy-worker
TARGET=/absolute/path/to/your-project
WT=/tmp/agy-job-12345
JOB_BRANCH=agy/tests-parser-errors-12345
ENVELOPE=/tmp/agy-job-12345-envelope.json
JOB_ID=parser-tests-12345
BASE="$(git -C "$TARGET" rev-parse HEAD)"

git -C "$TARGET" worktree add -b "$JOB_BRANCH" "$WT" "$BASE"

if ! echo "Add error-path tests for $WT/src/parser.py.
Edit ONLY files under $WT/tests/. Use file tools on absolute paths.
Do NOT run shell commands — they execute in a scratch directory, not this repo.
The driver runs every command. Return commands_run and tests_run as empty arrays." |
  AGY_WORKER_JOB_ID="$JOB_ID" "$PIPELINE/agy-worker.sh" \
    --mode accept-edits --tier bulk --persona bulk-test-writer \
    --workdir "$WT" --add-dir "$WT" > "$ENVELOPE"; then
  echo "Dispatch failed after its bounded attempts; inspect $PIPELINE/logs/$JOB_ID/stderr.txt" >&2
  exit 1
fi

if "$PIPELINE/qa-gate.sh" --envelope "$ENVELOPE" --repo "$WT" --base "$BASE" \
  --only 'tests/**' --expect-edits \
  --verify "git -C '$WT' diff --check" \
  --verify "cd '$WT' && python3 -m pytest -q tests/test_parser.py"; then
  echo "Candidate passed the evidence gate; review the diff before preserving it."
else
  GATE_RC=$?
  echo "Gate rejected or routed the candidate (exit $GATE_RC)."
  exit "$GATE_RC"
fi
```

Exit 0 means the evidence gate accepted the current state; it does **not** merge it.
Review and preserve the work before removing the worktree:

```bash
git -C "$WT" diff
git -C "$WT" add tests/
git -C "$WT" commit -m "test: cover parser error paths"
git -C "$TARGET" worktree remove "$WT"

# Integrate JOB_BRANCH only after your normal review/PR process.
```

If the gate rejects the job and you intentionally want to discard it:

```bash
git -C "$TARGET" worktree remove --force "$WT"
git -C "$TARGET" branch -D "$JOB_BRANCH"
```

Gate failure handling is deliberately small: exits 10–14 reject the candidate, exit
15 routes its questions to a human, and exit 64 means the driver invocation is wrong.
The wrapper already exhausted its bounded attempts before returning a dispatch error;
do not add an unbounded shell retry loop.

### Read-only inventory example

Use `plan` for inventory and independently spot-check the report. Exit 0 can prove
that no files changed and that the driver command passed; it cannot prove the
worker's architecture prose is accurate.

```bash
if ! echo "Read repository-owned files under $WT using absolute paths. Report entry points,
test commands, and risky areas. Do not run commands. Return files_changed,
commands_run, and tests_run as empty arrays." |
  "$PIPELINE/agy-worker.sh" --mode plan --persona repo-inventory \
    --workdir "$WT" --add-dir "$WT" > /tmp/inventory-envelope.json; then
  echo "Inventory dispatch failed; do not pass its stdout to the gate." >&2
  exit 1
fi

"$PIPELINE/qa-gate.sh" --envelope /tmp/inventory-envelope.json \
  --repo "$WT" --base "$BASE" \
  --verify "git -C '$WT' diff --quiet '$BASE' --"

# Then open a sample of every claimed path and verify discovered commands against
# package/CI files yourself before using the inventory for planning.
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
`agents/`. `repo-inventory` measurably changed an under-specified job from a false
survey into an honest escalation. `bulk-test-writer` has now been exercised on a real
Playbook-Gemini test task: the gate caught a bad first test and rejected the retry on
diff hygiene even though its focused tests passed. That proves the gate, not reliable
first-pass quality from the persona. The first dispatch consumed about 251k input
tokens and the corrective retry about 63k, reinforcing that this is for batched work,
not cheap one-line edits. `diff-reviewer` remains unexercised on a real job.

Personas are injected as prompt text, **not** via agy's `--agent` flag, because
`--agent` silently disables `--json-schema` enforcement (see below).

### Common options

| Worker option | Environment equivalent | Meaning |
|---|---|---|
| `--mode plan|accept-edits` | `AGY_WORKER_MODE` | defaults to read-only `plan` |
| `--tier cheap|bulk|hard|hardest|default` | `AGY_WORKER_TIER` | defaults to `bulk`; a model label is also accepted |
| `--workdir DIR` | — | agy's workspace |
| `--add-dir DIR` | — | repeatable file-tool root; must resolve inside `--workdir` |
| `--persona NAME` | — | inline a bounded worker role |
| — | `AGY_WORKER_TIMEOUT` | `--print-timeout`, default `5m0s` |
| — | `AGY_WORKER_MAX_ATTEMPTS` | bounded attempts, default `2` |
| — | `AGY_WORKER_JOB_ID` | safe artifact directory name |

Worker exits: `0` ok · `2` no prompt · `3` empty output · `4` schema invalid ·
`5` agy failed · `6` permission gate.

### Model selection is explicit; recommendations are advisory

The dispatcher does not infer difficulty or score the gap between worker output and
the expected result. The caller chooses a tier; the default is `bulk`:

| Tier | Current verified model label |
|---|---|
| `cheap` | `gemini-3.6-flash-low` |
| `bulk` | `gemini-3.6-flash-medium` |
| `hard` | `gemini-3.1-pro-high` |
| `hardest` | `claude-opus-4-6-thinking` |
| `default` | no `--model`; let agy choose |

Any other tier value is passed through as an explicit agy model label. Thinking is
therefore part of a selected model label where agy exposes one; this wrapper has no
separate thinking-level control. Its built-in retry handles dispatch failure with the
same model. After a gate failure, the Codex skill permits at most one targeted
corrective dispatch, also at the caller-selected tier. It never silently escalates
cost or changes model in response to failed tests, scope violations, or malformed
output.

`model-recommendation.sh` is a separate, read-only policy layer. It prints a visible
JSON recommendation before dispatch or after a gate result, but never calls `agy`,
runs `qa-gate.sh`, changes job state, or applies its recommendation. Every successful
result includes the caller's selected tier, a recommended named tier or explicit
`no-escalation`, rationale, controlled driver-owned evidence, relative cost impact,
and both `recommendation_only: true` and `applied: false`.

For example, a driver that has classified a task as a mechanical batch can ask for a
pre-dispatch recommendation without changing `--tier cheap` (or any other caller
choice):

```bash
./model-recommendation.sh --stage pre-dispatch \
  --selected-tier cheap --evidence batched-mechanical
```

Pre-dispatch evidence is deliberately finite: `bounded-routine`,
`batched-mechanical`, `cross-file-bounded`, or `high-complexity-bounded`.
The policy maps those profiles to `cheap`, `bulk`, `hard`, or `hardest`; it recommends
a higher tier only when the selected named tier is below that profile. `default` and
custom model labels are non-rankable and therefore never escalated.

After independent verification, the driver can classify the observed outcome:

```bash
./model-recommendation.sh --stage post-gate \
  --selected-tier bulk --evidence driver-verification-failed
```

Only `driver-verification-failed`, `driver-quality-review-failed`, and
`expected-edits-missing` may recommend one higher named tier. `gate-accepted`,
`permission-failed`, `authentication-failed`, `scope-policy-failed`,
`human-required`, `noncompleted-worker-outcome`, `untrusted-worker-claim`, and
`invalid-envelope` always produce
`no-escalation`. Permission, authentication, scope policy, and human authorization
must be resolved at their own boundaries; extra model spend cannot fix them. The
evidence code must be selected from driver-observed state, never copied from a worker
claim. Unsupported, cross-stage, repeated, missing, or ambiguous inputs fail with
exit 64 and no JSON output.

Gate controls:

- `--base FULL_COMMIT_ID` is required. Capture it before dispatch; mutable names such
  as `HEAD` and branch names are rejected so a worker cannot move the comparison base.
- `--verify COMMAND` is mandatory for acceptance and repeatable; only these
  driver-owned commands run. Empty/whitespace commands are rejected.
- `--only PATHGLOB` is repeatable and constrains every changed path.
- `--allow PATHGLOB` permits a known undeclared artifact but does not override `--only`.
- `--expect-edits` turns a completed no-op into exit 13.
- Exit 15 is escalation, never acceptance. Read `open_questions` and decide whether
  one corrective dispatch is justified.

`--allow-slash-commands` exists for callers who fully control the entire prompt, but
is intentionally omitted from normal examples. It disables protection against
embedded `/skill` or slash-command text; leave slash expansion disabled for content
derived from a repository or another model.

---

## Explicit updates and agy compatibility checks

There is no background updater. Checking is read-only:

```bash
./update.sh check
```

It reports the latest stable `vMAJOR.MINOR.PATCH` tag without fetching it, compares
the installed agy version to the verified version, checks whether the 30-day
compatibility review is due, and compares the official Antigravity CLI repository's
HEAD with the reviewed snapshot. Exit 3 means a human compatibility review is due;
the command still changed no files. The release origin, upstream source, and 30-day
window are fixed in the updater rather than overridable through environment variables;
a future `last-reviewed.txt` date is invalid rather than silently postponing review.

Apply is always explicit:

```bash
./update.sh apply v1.2.3       # or omit the tag to select the latest stable release
```

`apply` refuses a dirty or detached checkout, accepts only stable release tags from
the expected GitHub origin, verifies that the fetched tag resolves to the exact
remote commit and is a fast-forward, and runs Bash syntax, a temporary skill-install
preflight, and every offline suite in a disposable candidate worktree. It also refuses
an update when the release would begin tracking and overwrite an ignored local file;
unrelated ignored caches are left alone. Candidate failure leaves the checkout
unchanged.

Only after those checks does it fast-forward the current branch and rerun `install.sh`
against the real Codex skill destination. A real-destination permission failure cannot
be rolled back safely after the Git fast-forward: `apply` exits 4, reports **PARTIAL
UPDATE**, and tells you to fix the destination and rerun this checkout's `install.sh`.
This verifies GitHub transport/ref consistency; release maintainers must still protect
the GitHub account and tag-publishing process.

Compatibility review sources are recorded in `compat/sources.md`: Google's
[official CLI docs](https://antigravity.google/docs/cli-overview),
[usage guidance](https://antigravity.google/docs/cli-using), the
[official source repository](https://github.com/google-antigravity/antigravity-cli),
and live `./ground-truth.sh` output. When behavior changes, update the compatibility
snapshot only after reconciling all four and rerunning a bounded real-job check.

## Sanitized bug reports and feature requests

Codex can create a local draft, but nothing is submitted automatically:

```bash
./bug-report.sh draft --output /tmp/agy-worker-bug.md \
  --title "QA gate rejects an accurate created-file claim" \
  --component qa-gate \
  --summary "A synthetic fixture is rejected." \
  --steps "Create a fresh fixture and run the offline gate case." \
  --expected "The accurate claim is accepted." \
  --actual "The gate exits 10."

./bug-report.sh preview /tmp/agy-worker-bug.md
```

The generator reads no prompts, source files, envelopes, or logs. It conservatively
redacts credential-bearing lines, GitHub/Bearer/Basic tokens, complete private-key
blocks, absolute POSIX/Windows/UNC paths, current worker artifact names, and closed or
unclosed fenced/indented code. Safe relative synthetic paths remain usable. Drafts are
published atomically with mode `0600`, then a SHA-256 review token is printed.
Submission requires the exact reviewed hash and an existing, authenticated GitHub
CLI; `gh` is optional and not a runtime dependency:

```bash
./bug-report.sh submit /tmp/agy-worker-bug.md --confirm-sha <SHA256-FROM-PREVIEW>
```

Immediately before invoking `gh issue create`, submission validates and prints the
exact body again. The validated in-memory bytes are sent over stdin with
`--body-file -`, so a later file change cannot alter the confirmed body. The target is
explicitly `github.com/cagdasyurekli/codex-agy-worker`; an inherited `GH_HOST` cannot
redirect it. A changed draft invalidates the hash. Without `gh`, or when `gh` fails,
the local draft remains and nothing else is attempted.

The repository also includes GitHub Issue Forms for
[sanitized bugs](.github/ISSUE_TEMPLATE/bug_report.yml) and
[feature requests](.github/ISSUE_TEMPLATE/feature_request.yml). Feature requests ask
for the problem, concrete use case, acceptance criteria, alternatives, minimal scope,
security/privacy impact, and an explicit privacy acknowledgement. Blank issues are
disabled so maintainers can review proposals consistently; submission does not imply
acceptance or roadmap commitment.

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
model-recommendation.sh       print a read-only pre-dispatch/post-gate tier advisory
ground-truth.sh               dump live agy facts for skill authoring
update.sh                     explicit release + agy compatibility check/apply
bug-report.sh                 sanitized local draft/preview/optional submission
compat/                       reviewed agy version, upstream, date, and sources
scripts/bug-report.py         privacy filter and SHA-bound gh submission
scripts/model-recommendation.py  controlled recommendation policy and JSON rendering
scripts/validate-envelope.py  dependency-free full envelope validation
schemas/worker-result.*.json  the worker contract
agents/*.md                   personas, inlined via --persona
skills/agy-worker/            canonical Agent Skill, OpenAI metadata, runtime resolver
.codex-plugin/plugin.json     OpenAI skills-only plugin package metadata
.agents/plugins/marketplace.json  Codex repository marketplace source
.claude-plugin/               Claude plugin and repository marketplace metadata
docs/REPO_MAP.md              hand-maintained ownership, data flow, and trust map
docs/lessons_learned.md       durable architectural mistakes and prevention rules
docs/index.md                 SEO-friendly GitHub Pages landing source
docs/MARKETPLACE.md           marketplace status, submission gates, review cases
tests/test-qa-gate.sh         offline adversarial suite
tests/test-agy-worker.sh       offline dispatcher/installer/routing suite
tests/test-update.sh          offline local-remote updater suite
tests/test-reporting.sh       offline privacy/fake-gh reporting suite
tests/test-packaging.sh       offline plugin/marketplace/relocation/landing suite
```

Contributors should start with [the repository map](docs/REPO_MAP.md) and use
[the architectural lessons](docs/lessons_learned.md) for the rationale behind the
trust boundaries. Keep one-off run history and release notes out of `AGENTS.md`.

## Limitations

- No async/polling; jobs are synchronous and bounded by `--print-timeout`.
- Not tested on Windows.
- Single worker backend (agy). No Claude Code / Codex worker support.
- One audited worktree per job. User-supplied `--add-dir` roots outside `--workdir`
  are rejected; multi-repository mutation is intentionally unsupported.
- `bulk-test-writer` has been exercised but has not yet produced an accepted real job;
  `diff-reviewer` remains untested against a real job.
- No stable updater release tag has been exercised against the public repository yet;
  update behavior is proven with local offline release remotes.
- GitHub submission is tested with a fake `gh`; no live issue was created.
- Headless skill expansion (`agy -p "/skill-name ..."`) is untested.

## License

MIT — see [LICENSE](LICENSE).
