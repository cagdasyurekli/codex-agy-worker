<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/logo-dark.svg">
  <img src="docs/assets/brand/logo-light.svg" alt="" width="132" height="132">
</picture>

# codex-agy-worker

Delegate bounded coding work from **Codex CLI** to **Antigravity CLI (`agy`)**, then
independently check Git scope and run driver-owned verification commands before
accepting the candidate. The gate verifies those specific conditions; it does not
prove general correctness or security.

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

**This one exists for a single reason: it does not accept the worker's self-report as
evidence.** The worker's JSON report is treated as a *claim*. The gate independently
derives a bounded set of facts from the repository:

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

The ten offline suites need no agy process, network access, API key, or GitHub login.

## See the evidence boundary in under a minute

```bash
./proof-demo.sh
```

This repository-only starter proof creates two independent private temporary Git
repositories, exercises the maintained gate once on an exact synthetic edit and once
on a plausible but incomplete synthetic envelope, then removes both repositories. It
does not invoke agy, access the network, inspect credentials, or change the current
checkout. The three-line result proves only that these two fixed cases produced the
expected gate exits. `gate-passed` is not a human review, accepted candidate, general
correctness claim, security certification, benchmark, or production validation.

## Roadmap

[The product roadmap](docs/ROADMAP.md) lists proposed, dependency-ordered feature
slices. Those items are plans, not current CLI behavior or implementation claims;
each slice requires its own approval, tests, review, and pull request.

---

## Install from GitHub

```bash
git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker
./install.sh          # installs the Codex skill only; touches nothing else
for suite in tests/test-*.sh; do "$suite"; done
```

The GitHub repository is the source of truth. Review the commit you cloned before
installation. For a released snapshot, check out the exact reviewed
`vMAJOR.MINOR.PATCH` tag from the
[GitHub Releases page](https://github.com/cagdasyurekli/codex-agy-worker/releases)
before running `./install.sh`; do not substitute an unverified tag.

Requires `agy` (Antigravity CLI) on `PATH`, `git`, `python3`, and bash.
Tested on macOS with agy 1.1.10 and codex-cli 0.146.0. Not tested on Windows.

Before spending provider quota, run the offline doctor against the repository you
plan to delegate:

```bash
./doctor.sh --repo /absolute/path/to/target
./doctor.sh --repo /absolute/path/to/target --format json
```

The doctor is deterministic and read-only. It checks the bundled runtime, Bash 3.2,
Python 3, git and worktree support, target Git worktree, exact semantic
`agy --version`, and the checked-in agy version, reviewed-source, and review-date
records. Source-revision failures are reported only as match, mismatch, or unavailable;
the doctor never prints their bytes. It invokes no provider, network client, updater,
dispatch, authentication probe, or personal-config scan, and it repairs nothing.

| Exit | Overall | Meaning |
|---:|---|---|
| `0` | `ready` | All offline prerequisites match the checked-in evidence. |
| `3` | `review-required` | Prerequisites work, but the agy version drifted or review is due. |
| `3` | `not-ready` | A prerequisite, semantic probe, repository, bundle, or metadata check failed. |
| `64` | no report | Invocation or format is invalid. |

If the repository-root launcher cannot resolve a bounded symlink chain to the
canonical `doctor.sh`, it emits one sanitized diagnostic, no report, and exits `3`.
The wrapper itself may be reached through that bounded chain, but the package-owned
`skills/agy-worker/runtime` path and the runtime's `scripts`, `agents`, `schemas`,
and `compat` parents must be real directories contained in the bundle, not symlinks.
The doctor also ignores caller-provided temp paths, gives every child probe one
private external workspace, and removes it before returning. HUP, INT, or TERM is
forwarded to the active probe and its descendants; interruption emits only
`doctor: interrupted`, no report, and exits `3`.

Green covers only those offline prerequisites. It does **not** certify authentication,
provider availability, Codex/agy sandbox permission, task quality, or a future
dispatch. A due or drift result asks for human compatibility review; it never updates
metadata. For a folder-only skill copy, resolve `PIPELINE` as shown in
`skills/agy-worker/SKILL.md` and run `"$PIPELINE/doctor.sh"`—no checkout or fetch is
needed.

`skills/agy-worker/` is the one canonical, open-standard Agent Skill and contains its
own Bash/Python/git runtime. A folder-only copy therefore works without the rest of
the checkout and never downloads code when invoked. The repository-root commands are
compatibility wrappers for clone users. `install.sh` copies the same bundle and writes
a local pointer so checkout-only maintenance commands remain available; it does not
rewrite the public `SKILL.md`.

The repository retains `.codex-plugin/plugin.json` so the Codex skills-only package
shape can be validated locally. It is not a marketplace listing: GitHub clone plus
`./install.sh` is the supported distribution path.

The portable Agent Skill can also be copied through the third-party skills CLI:

```bash
DO_NOT_TRACK=1 npx skills add cagdasyurekli/codex-agy-worker \
  --skill agy-worker --copy
```

`npx` is only an optional installer here; the installed skill has no Node runtime
dependency. Review the copied files before use.

The public landing page source lives at `docs/index.md`. GitHub Pages configuration,
repository About fields, topics, and search-engine verification are separate
repository-owner actions; checked-in files do not change those settings.

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
`skills/agy-worker/runtime/agents/`. `repo-inventory` measurably changed an
under-specified job from a false
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
| `--model EXACT_MODEL` | `AGY_WORKER_MODEL` | reviewed exact slug, or adjustable base used with effort |
| `--effort low|medium|high` | `AGY_WORKER_EFFORT` | requires an adjustable base and resolves to one exact slug |
| `--workdir DIR` | — | agy's workspace |
| `--add-dir DIR` | — | repeatable file-tool root; must resolve inside `--workdir` |
| `--persona NAME` | — | inline a bounded worker role |
| — | `AGY_WORKER_TIMEOUT` | `--print-timeout`, default `5m0s` |
| — | `AGY_WORKER_MAX_ATTEMPTS` | bounded attempts, default `2` |
| — | `AGY_WORKER_JOB_ID` | safe artifact directory name |

Worker exits: `0` ok · `2` no prompt · `3` empty output · `4` schema invalid ·
`5` agy failed · `6` permission gate · `7` compatibility review required ·
`8` compatibility evidence unavailable · `64` invalid usage.

The dispatcher creates each job directory and its task, full prompt, stream, stderr,
staged prompt, and envelope under an owner-only mask, even when the caller's mask is
permissive. A missing `AGY_WORKER_LOG_DIR` is created owner-only. An existing final
log root must be a real directory owned by the current user with no group/other write
bits; a final symlink is rejected, and the accepted root is resolved physically. The
dispatcher does not rewrite that caller-owned root or change the caller's umask inside
agy, so this boundary does not silently change candidate-file permissions. A job ID
is exclusive: an existing directory, file, or symlink at that job path is rejected
before the prompt is read or agy is invoked. Oversized staged prompts return to
owner-only modes after the child, on an early exit, and before HUP, INT, or TERM is
re-raised with its normal status. This bounded final-root check does not validate the
full ancestor chain or claim to eliminate every filesystem time-of-check/time-of-use
race.

### Model selection is explicit; recommendations are advisory

The dispatcher does not infer difficulty or score the gap between worker output and
the expected result. The caller chooses a tier; the default is `bulk`:

| Tier | Current configured model label |
|---|---|
| `cheap` | `gemini-3.6-flash-low` |
| `bulk` | `gemini-3.6-flash-medium` |
| `hard` | `gemini-3.1-pro-high` |
| `hardest` | `claude-opus-4-6-thinking` |
| `default` | no `--model`; let agy choose |

These tier constants remain independent of the compatibility matrix. Any other
`--tier` value remains the legacy raw-label pass-through; custom labels are accepted
only on that legacy surface. Direct selection is intentionally stricter:

```bash
./agy-worker.sh --model gemini-3.6-flash-high ...
./agy-worker.sh --model gemini-3.6-flash --effort high ...
```

The second form resolves through the active, exact-SHA, agy-version/source-bound
matrix to `gemini-3.6-flash-high`. Flash 3.6 and 3.5 accept low/medium/high; Pro 3.1
accepts low/high and rejects medium. Sonnet, the Opus thinking-labelled slug, the GPT
medium-labelled slug, and every already-compound slug are fixed exact choices and
reject an effort input. The wrapper sends exactly one downstream `--model` and never
sends agy's separate `--effort` or an invented thinking flag.

Selector sources have no silent precedence. A component may come from CLI or its
matching environment variable, never both—even when equal. Repeated selectors,
explicit empty values, tier plus any model/effort source, effort without a model,
unknown models, and unsupported pairs fail before the task is read or a worker is
dispatched. Model and effort may come from different sources when each has exactly
one source. New direct selectors run one bounded local `agy --version` preflight and
require the reviewed `1.1.10`; legacy tier/default behavior performs no such probe.
HUP, INT, or TERM during that preflight closes its exact process group and returns
`129`, `130`, or `143` before the task is read or a selection record is published.

The resolved slug, input provenance, installed version, matrix version/source, and
matrix SHA-256 are frozen before attempt one in owner-private
`logs/<job>/selection.json`. Retries reuse that exact selection even if the matrix
file changes later. This driver-owned record is provenance, not worker evidence, a QA
receipt, or an acceptance path.
Its built-in retry handles dispatch failure with the same model. After a gate failure,
the Codex skill permits at most one targeted corrective dispatch, also at the
caller-selected tier. It never silently escalates cost or changes model in response
to failed tests, scope violations, or malformed output.

`model-recommendation.sh` is a separate, read-only policy layer. It prints a visible
JSON recommendation before dispatch or after a gate result, but never calls `agy`,
runs `qa-gate.sh`, changes job state, or applies its recommendation. Every successful
result includes the caller's tier or distinct user/resolved direct selection, a
recommended named tier or explicit `no-escalation`, rationale, controlled
driver-owned evidence, relative cost impact,
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

Direct advisories use `--selected-model` and optional `--selected-effort`. They
resolve only against the checked-in reviewed matrix, never probe or dispatch agy,
and always remain unranked `no-escalation` decisions. Their JSON labels the user
model, optional user effort, exact resolved model, and matrix binding without
changing or redispatching the selection.

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

### Preserve a local Evidence Receipt v1

`verify-job.sh` runs the same canonical gate and, only for gate outcomes `0` and
`10`–`15`, durably publishes a private JSON receipt. The receipt records hashes of
the exact envelope snapshot, ordered path policy, and driver-owned verifier commands;
the immutable base; the gate's initial and final candidate-state digests; and the
gate's exact outcome. It contains deterministic labels such as `verify-001`, never
the verifier command text or output.

Create a new owner-private directory outside the audited repository and choose a new
receipt path. The parent and path must already be canonical; the command rejects a
symlink, overwrite, repository-contained target, or group/other-accessible parent.

```bash
umask 077
RECEIPT_DIR="$(mktemp -d -t agyworker-receipts.XXXXXX)"

./verify-job.sh --receipt "$RECEIPT_DIR/job.json" \
  --envelope envelope.json --repo "$WT" --base "$BASE" \
  --only 'tests/**' --expect-edits \
  --verify "git -C '$WT' diff --check" \
  --verify "cd '$WT' && python3 -m pytest -q tests/test_parser.py"
```

`--selection FILE` may bind one current, validated G1 selection record.
`--pre-recommendation FILE` may independently bind one canonical `pre-dispatch`
advisory; a post-gate, cross-stage, mismatched, or `applied: true` advisory is
rejected. The command never creates or applies a recommendation. Both inputs are
optional and at most once. There is no implicit scan of `logs/`.

The receipt maps gate `0` to `gate-passed`, gate `10`–`14` to `rejected`, and gate
`15` to `routed`, then returns that exact exit only after file `fsync`, atomic
no-overwrite hard-link publication, and parent-directory `fsync`. Input or gate
preflight errors return `64`; missing, malformed, mismatched, unknown, or interrupted
gate evidence returns `70`; receipt validation or durable-publication failure returns
`74`. Those failures publish no receipt. The evidence descriptor and capability are
an internal `verify-job.sh` handoff, not a supported direct `qa-gate.sh` interface;
direct gate use without them retains the existing output, side effects, and exits.
The wrapper removes shell/Python startup controls before launching the evidence-mode
gate, gate-owned Python runs isolated without site startup, and the gate parent closes
the descriptor with a shell builtin before any verifier shell or interpreter starts.
Verifier descendants therefore cannot observe or write the handoff. Other verifier
environment values are preserved; the unsafe startup controls and internal handoff
variables are intentionally absent. HUP, INT, or TERM from private
snapshot creation through publication and cleanup returns `70`, closes and reaps an
active gate/verifier process group, and removes wrapper-owned partial artifacts.

Every receipt says explicitly that it is unsigned and not tamper-evident. Its
existence does not make a candidate accepted, signed, authentic, correct, or safe.
Only `qa-gate.sh` supplies the gate result, and human diff review remains required
after `gate-passed`. The dependency-free validator can check a receipt and optionally
bind the original envelope or a separately trusted candidate-state digest:

```bash
python3 -B skills/agy-worker/runtime/scripts/evidence_receipt.py validate \
  --receipt "$RECEIPT_DIR/job.json" --envelope envelope.json
```

`--allow-slash-commands` exists for callers who fully control the entire prompt, but
is intentionally omitted from normal examples. It disables protection against
embedded `/skill` or slash-command text; leave slash expansion disabled for content
derived from a repository or another model.

---

## Explicit updates and tool compatibility checks

There is no background updater. Checking is read-only:

```bash
./update.sh check
```

It reports the latest stable project tag without fetching it, then reports installed,
verified, official stable-release/source drift, and 30-day documentation-review age
separately for agy and Codex CLI. The agy section also checks one fixed official
`darwin_arm64` distribution-manifest canary. It validates only that small JSON
document and never requests, downloads, hashes, or executes the referenced archive.
Exit `0` means all required evidence is available and unchanged. Exit `3` means
established drift, a due review, or a missing installed tool. Exit `2` means evidence
is unavailable or malformed, so the result is inconclusive—not green. Both tools are
reported before those results are aggregated, with `2` taking precedence over `3`.

Project, agy, and Codex release/source evidence comes from exact fixed
`https://api.github.com/repos/...` REST paths. The stdlib-only client disables ambient
HTTP proxies, refuses redirects, validates strict JSON and response metadata, and
captures responses under time and byte limits. The supervisor also bounds installed
`agy --version` and `codex --version` probes, discards their raw stderr, and terminates
the complete child process group on timeout, output overflow, or HUP/INT/TERM. Neither
`check` nor `check --watch` makes a Git network request, so repository or global
`url.*.insteadOf`, credential helpers, and Git proxy settings cannot redirect their
official release/source evidence.

The release origins, upstream sources, distribution-manifest endpoint, release
channels, and 30-day cadence are fixed in the updater rather than overridable through
arguments, environment variables, or configuration. The check never fetches into
Git, pulls, applies, or writes a baseline. A future per-tool review date is
inconclusive rather than silently postponing review.

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
The fixed API commit must match the commit fetched during `apply`, but the explicit
apply path still uses `git fetch` and therefore honors the caller's Git transport
configuration, including URL rewrites and Git proxies. The read-only transport
isolation described above does not harden that mutating path. Release maintainers must
also protect the GitHub account and tag-publishing process.

The fixed primary sources and exact reviewed revisions are recorded in
[`compat/sources.md`](compat/sources.md). A separate weekly/manual macOS compatibility
watch runs the official-evidence-only mode without installing agy or Codex. It writes
only a bounded Step Summary, preserves the same `0`/`3`/`2` meanings, is not a required
pull-request check, and cannot advance metadata or open an issue or pull request.

Separately captured `agy models` output can be reconciled offline by the bounded
semantic parser in `scripts/agy_inventory.py`. It requires exactly one occurrence of
each of the 11 reviewed canonical slugs on 11 nonblank lines and rejects unknown,
missing, duplicate, or ambiguous slug-shaped tokens, including unknown entries in
the reviewed Gemini, Claude, and GPT namespaces. Ordinary display labels remain
non-authoritative. The `gpt-oss` display alias is
accepted only on the same line as `gpt-oss-120b-medium`; it never becomes a twelfth
model. Parsing inventory does not bind an installed version, advance a baseline, or
activate the matrix.

The human-reviewed agy baseline is `1.1.10` at source revision
`bfab12dac5bd090015a89cf82e65093d13b567d9`. The fixed official sources, one
sandbox-correct 11-slug inventory, and two bounded single-selector jobs are recorded
in [`compat/reviews/agy-1.1.10.md`](compat/reviews/agy-1.1.10.md). The checked-in
manifest tuple remains an observational change detector rather than a trust root: a
same-version archive build, URL, or SHA-512 change requires review and cannot itself
activate or advance compatibility metadata.

The G1 direct-selection surface consumes the checked-in active model/effort matrix as
validated compatibility metadata, never as routing or gate authority. It maps eight explicit
base/effort pairs to exact advertised compound slugs and records three exact fixed
choices; Pro medium is unsupported. The matrix resolves only while its agy version
and reviewed source revision match the canonical records and its exact bytes match
the checked-in SHA-256. The wrapper resolves one exact model slug and never sends
agy's separate effort flag. `qa-gate.sh` remains the sole
acceptance authority, and model recommendations remain visible, advisory-only, and
unable to escalate permission, authentication, scope-policy, or human-required
outcomes. The bounded jobs proved exact argv and candidate verification, not effective
provider backend identity, quality, or billing.

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

## agy behaviour worth knowing

Most facts below were measured on macOS with agy 1.1.9 on 2026-08-01. The narrower
1.1.10 model reconciliation is recorded separately. Run `./ground-truth.sh` against
your own install rather than treating historical observations as a current contract.

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
verify-job.sh                 run the gate and durably publish Evidence Receipt v1
proof-demo.sh                 offline starter proof of one gate pass and one rejection
model-recommendation.sh       repository compatibility wrapper for the advisory
model-selection.sh            repository compatibility wrapper for explicit resolution
doctor.sh                     repository wrapper for offline read-only diagnostics
ground-truth.sh               dump live agy facts for skill authoring
update.sh                     explicit release + agy/Codex compatibility check/apply
bug-report.sh                 sanitized local draft/preview/optional submission
compat/                       per-tool baselines, reviewed evidence, and active exact matrix
scripts/compatibility.py      stdlib metadata/matrix validation and exact resolution
scripts/compatibility_probe.py bounded process-group supervisor for fixed evidence/version probes
scripts/version_attestation_runner.py fixed-profile snapshot version runner with bounded startup diagnostics; real use separately authorized
scripts/version_attestation_harness.py persistent fake-child publication/process/signal mutation harness
scripts/agy_inventory.py      bounded exact-line semantic parser for private inventory evidence
scripts/official_github.py    fixed, proxyless, redirect-free GitHub REST evidence client
scripts/official_distribution.py  fixed, bounded agy distribution-manifest canary
scripts/bug-report.py         privacy filter and SHA-bound gh submission
skills/agy-worker/            canonical self-contained Agent Skill and runtime
skills/agy-worker/runtime/    dispatcher, gate, advisory, personas, schema, Python helpers
skills/agy-worker/runtime/compat/  byte-synced portable agy metadata and selection matrix
.codex-plugin/plugin.json     OpenAI skills-only plugin package metadata
docs/REPO_MAP.md              hand-maintained ownership, data flow, and trust map
docs/lessons_learned.md       durable architectural mistakes and prevention rules
docs/index.md                 GitHub Pages landing source
CONTRIBUTING.md               contributor workflow and evidence expectations
SECURITY.md                   private vulnerability reporting policy
CODE_OF_CONDUCT.md            enforceable participation standards
.github/pull_request_template.md  review and verification checklist
tests/test-qa-gate.sh         offline adversarial suite
tests/test-evidence-receipt.sh  88-case offline receipt/publication/protocol suite
tests/test-agy-worker.sh       offline dispatcher/installer/routing suite
tests/test-update.sh          310-case offline transport/process/inventory/local-remote/matrix/manifest updater suite
tests/test-agy-inventory.py   test-only exact-slug/display-alias adversary harness
tests/test-official-github.py test-only fixed-endpoint transport adversary harness
tests/test-compatibility-probe.py test-only timeout/output/signal/version adversary harness
tests/test-version-attestation-runner.py  136-case offline canonical fixed-profile runner suite
tests/test-version-attestation-harness.py  55-case offline version-attestation mutation suite
tests/test-official-distribution.py  test-only stdlib manifest adversary harness
tests/test-reporting.sh       offline privacy/fake-gh reporting suite
tests/test-packaging.sh       135-case offline Codex package/relocation/landing suite
tests/test-doctor.sh          180-case offline fake-tool/read-only doctor suite
tests/test-proof-demo.sh      21-case offline starter-proof adversarial suite
.github/workflows/compatibility-watch.yml  observational weekly/manual fixed-source watch
```

Contributors should start with [the repository map](docs/REPO_MAP.md) and use
[the architectural lessons](docs/lessons_learned.md) for the rationale behind the
trust boundaries. Keep one-off run history and release notes out of `AGENTS.md`.

## Limitations

- No async/polling; jobs are synchronous and bounded by `--print-timeout`.
- Not tested on Windows.
- Single worker backend: agy; no alternative worker backend is implemented.
- One audited worktree per job. User-supplied `--add-dir` roots outside `--workdir`
  are rejected; multi-repository mutation is intentionally unsupported.
- `bulk-test-writer` has been exercised but has not yet produced an accepted real job;
  `diff-reviewer` remains untested against a real job.
- No stable updater release tag has been exercised against the public repository yet;
  update behavior is proven with local offline release remotes.
- GitHub submission is tested with a fake `gh`; no live issue was created.
- Headless skill expansion (`agy -p "/skill-name ..."`) is untested.
- Direct model/effort selection has exhaustive offline fake-agy coverage but no new
  post-G0 live provider run. It proves argv, conflict, version/matrix, packaging, and
  retry-freeze behavior—not backend identity, quality, relative performance, cost,
  quota efficiency, or dual-selector composition inside agy.
- A green doctor result covers offline prerequisites only. It does not predict live
  authentication, provider, sandbox, task, or dispatch behavior and never fixes them.

## License

MIT — see [LICENSE](LICENSE).
