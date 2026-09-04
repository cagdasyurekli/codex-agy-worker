<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/logo-dark.svg">
  <img src="docs/assets/brand/logo-light.svg" alt="" width="132" height="132">
</picture>

# codex-agy-worker

**A Codex Agent Skill for bounded Antigravity CLI delegation with independent
Git-scope checks and driver-owned verification.**

[![Offline test workflow](https://github.com/cagdasyurekli/codex-agy-worker/actions/workflows/test.yml/badge.svg)](https://github.com/cagdasyurekli/codex-agy-worker/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

Use **Codex CLI** to delegate repository exploration, features, and project-scale
coding to **Antigravity CLI (`agy`)**. Codex—not the worker report—reviews the diff,
runs build/test/lint checks, and decides whether the result is verified, partial, or
blocked.

## Quick start

Requires a POSIX-compatible environment with Bash, Python 3, git, Codex CLI, and
`agy` on `PATH`. Native Windows is untested; WSL may work on a best-effort basis.

Install the Agent Skill from its Git-backed Codex marketplace:

```bash
codex plugin marketplace add cagdasyurekli/codex-agy-worker
codex plugin add codex-agy-worker@codex-agy-worker
```

Start a new Codex session after installation. The marketplace packages the same
canonical `skills/agy-worker/` bundle; it does not duplicate or download a second
runtime when the skill runs.

Or review and install directly from GitHub:

```bash
git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker
./install.sh
```

Review the selected source commit before either installation path. The marketplace
flow was tested from an immutable Git commit with isolated Codex state, including
add, discovery, install, removal, and exact installed-skill byte parity. Installation
does not authorize a provider dispatch or repository transmission.

### Try the evidence boundary offline

```bash
./proof-demo.sh
```

The starter proof uses two private synthetic Git repositories, invokes no provider or
network, and changes neither this checkout nor your credentials. It demonstrates two
fixed gate cases only; it is not a security certification or proof of general
correctness.

### First real task

Before an agy-backed request, choose and approve one transmission mode. Prefer
`--provider-scope` for bounded jobs: it binds exact reviewed
read entries, their selected-content digest, and a write subset, then stages only those
entries in a fresh owner-private mode-`0700` Gitless provider cwd. Whole-worktree
dispatch remains an explicit `--approve-whole-worktree MANIFEST_SHA256` exception and
may expose every file in the disposable worktree through `agy` to Google/Gemini;
`--add-dir`, prompt restrictions, and later `qa-gate --only` checks do not narrow that
read boundary. Scoped staging is
not a sandbox, and its approval grants no provider execution, Git action, acceptance,
or publication. Read [PRIVACY.md](PRIVACY.md) before use.

Before approval, inspect the content-free path boundary without starting `agy`, a
provider probe, or network activity:

```bash
./agy-worker.sh transmission-preview --workdir "$WT"

# Optional selected-content preview. Review its transmission_sha256, then bind the
# same scope and digest on direct dispatch as documented in docs/USAGE.md.
./agy-worker.sh transmission-preview --workdir "$WT" \
  --provider-scope "$SCOPE" --format json
```

The preview requires a canonical branch-backed linked worktree. It lists directories,
regular files, and contained symlink aliases (including ignored and untracked paths),
but excludes the root `.git` control marker. In default mode, it does not read file
contents and the digest binds path names and kinds only. Scoped preview reads selected
content to compute its digest and binds that digest plus the scope policy into
`transmission_sha256`. Both previews are review evidence, not approval or
provider-launch authority. Every initial raw or facade dispatch requires the exact
whole-worktree manifest approval or the scoped policy/transmission pair. The controller
still locally enumerates and validates
worktree paths; a fixed bounded Git worktree-list check proves registration, and no
`agy`, provider, credential probe, or network process is started.

After installation, start a new Codex session and ask:

> Use the agy-worker skill to add error-path tests for the parser modules under
> `/absolute/path/to/project/src/`. Allow changes only under `tests/`, verify with
> `python3 -m pytest -q tests/test_parser.py`, and preserve accepted work on a branch.

Codex creates an isolated worktree, asks for provider-transmission approval when it
has not already been granted for that exact scope, inspects the resulting diff, and
runs driver-owned checks. A worker can discover ordinary project structure; it is not
limited to mechanical edits or a predeclared file list.

[Learn how to verify an agent candidate without trusting its report](docs/VERIFYING_AGENT_OUTPUT.md).

## The evidence pipeline

Bash + Python 3 + git. No Node runtime and no MCP daemon. A deliberately started job
may have one private, per-job local controller; it is not a shared service.

The primary lifecycle is `workflow.sh run`, `workflow.sh status`, and
`workflow.sh verify-finalize`. For ordinary `run`, Codex supplies an absolute reviewed
repository and job ID. The façade binds the current `HEAD` once when `--base` is
omitted, derives owner-private state plus an isolated branch/worktree under
`XDG_STATE_HOME` (or `HOME/.local/state`), and delegates their creation to the existing
job lifecycle. It still creates no second state machine and never infers assurance.

```bash
# Review the canonical path/kind preview before provider approval.
./workflow.sh run --preview --repo "$TARGET" --job-id "$JOB_ID"

# The preview call retains its exact private lifecycle bindings. After approval,
# repeat the repo/job ID with --approve-whole-worktree SHA256, --workflow task,
# and --task "$TASK". For selected content, put --provider-scope on both calls
# and approve the scoped transmission SHA. An optional full --base can be supplied.
# Then use status, copy dispatch.state_sha256, and call verify-finalize with
# --approve-dispatch-sha plus driver-authored --verify-argv and, for controller
# finalization, --verification-json.
```

The former explicit `--state`, `--worktree`, `--branch`, and `--base` tuple remains
available together as an advanced compatibility surface. A failed invocation may
roll back only the clean façade-created resources that same invocation created before
any dispatch evidence; preview and stale approval retain resources for inspection.

`--verify-argv` accepts a canonical JSON array and runs it from the repository root
without an implicit shell. Explicit `--verify-shell SCRIPT` is an advanced surface
that requires both verifier network and credential-access acknowledgements. Historical
`--verify SCRIPT` additionally requires `--legacy-shell-verification`.
For a bound dispatch, `verify-finalize` requires the exact dispatch-state SHA reported
by facade `status`; it never substitutes a current state approval. The deprecated
`--approve-state-sha` spelling remains a strict mutually exclusive alias during the
compatibility window. Rejected or routed gate receipts are preserved without invoking
the lifecycle finalizer.

The worker envelope is input, not acceptance evidence:

1. Codex freezes an immutable Git base in a disposable worktree.
2. The facade requires an explicit choice. `--approve-whole-worktree` binds the
   current path/kind manifest and acknowledges that `agy` may read the whole disposable
   worktree; requested paths constrain the task, not provider read access. Facade
   `--provider-scope` dispatch instead binds exact reviewed read/write entries and a
   selected-content digest, then stages only selected entries in a fresh owner-private
   mode-`0700` Gitless provider cwd. See
   [selected-content dispatch](docs/USAGE.md#optional-selected-content-dispatch); the
   stage is not a sandbox, and scope approval grants no provider execution, Git,
   acceptance, or publication authority.
3. The gate derives changed paths from Git instead of trusting `files_changed`.
4. Codex runs driver-owned verification; worker-reported commands are never executed.
5. Codex reports `verified`, `partially_verified`, or `blocked` for that exact
   candidate and those exact checks.

| Observed condition | Gate outcome |
|---|---|
| declared paths disagree with Git | reject scope |
| worker supplies shell/test claims | treat as untrusted data |
| required verifier fails or mutates the candidate | reject verification |
| worker needs a human or returns partial work | preserve and escalate honestly |

See the [verification tutorial](docs/VERIFYING_AGENT_OUTPUT.md) for the full reasoning
flow and the [fixture-only conformance contract](docs/CONFORMANCE.md) for bounded exit
semantics. Passing the public fixtures means fixture compatibility only.

## What it is for

Choose the workflow that matches the result you want:

| Intent | Workflow | Result |
|---|---|---|
| Understand, review, or plan | `explore` | Read-only findings with coverage limits |
| Implement a feature, refactor, or tests | `task` | A bounded candidate plus driver checks |
| Build or repair across a project | `project` | Progress-aware work with bounded repair |

You do not need to know the final file list, architecture, or every test command before
dispatch. Codex still owns repository scope, review, verification, and the final
assurance statement.

Assurance describes the evidence for one candidate, not the worker's confidence:

| Status | Meaning |
|---|---|
| `verified` | The reviewed candidate passed the declared Git-scope and driver checks. |
| `partially_verified` | Useful work was preserved, but at least one required check or review remains. |
| `blocked` | The requested outcome needs new authority, input, or an external state change. |

## What it is not

- It is not an autonomous release, merge, or repository administration service.
- It does not bypass Codex approvals, agy permissions, or provider-transmission
  consent.
- It does not accept worker-reported tests, commands, or completion as evidence.
- It does not support one job mutating multiple repositories or escaping its
  disposable worktree.
- It does not infer a generally best model from offline fixtures or advisory records.

## Why another one of these?

Several Codex-to-agy bridges already exist, and some offer more integration features.
This project deliberately keeps one portable Agent Skill and one agy backend.

Its differentiator is narrower: **Codex does not confuse a worker report with
evidence.** Git-derived scope and driver-owned checks decide what was actually
verified. That does not establish general correctness, security, provider quality, or
task quality.

## Documentation

Choose a guide by what you need to do:

| Goal | Guide |
|---|---|
| Install, configure the sandbox, or diagnose prerequisites | [Installation and compatibility](docs/INSTALLATION.md) |
| Run explore, task, project, manual, or model-selected workflows | [Usage](docs/USAGE.md) |
| Manage progress-aware jobs, verification, recovery, and receipts | [Project workflow](docs/PROJECT_WORKFLOW.md) |
| Run CI, updates, notifier maintenance, or sanitized reporting | [Operations](docs/OPERATIONS.md) |
| Inspect the Codex marketplace package contract | [Marketplace](docs/MARKETPLACE.md) |
| Verify a candidate without trusting its report | [Verification tutorial](docs/VERIFYING_AGENT_OUTPUT.md) |
| Integrate against the bounded public gate fixtures | [Conformance](docs/CONFORMANCE.md) |
| Review offline benchmark and adoption evidence | [Benchmarking](docs/BENCHMARKING.md) · [Measurement](docs/MEASUREMENT.md) |
| Understand source ownership or product direction | [Repository map](docs/REPO_MAP.md) · [Roadmap](docs/ROADMAP.md) |

Public documentation follows a single-owner and progressive-disclosure policy. See
[the documentation policy](docs/DOCUMENTATION_POLICY.md) before changing README or
adding a guide.

## Installation details and compatibility

The quick-start installer copies only the canonical, self-contained
`skills/agy-worker/` bundle. Repository-root scripts are compatibility wrappers for
clone users. The Git marketplace and local installer resolve the same runtime; review
[the marketplace contract](docs/MARKETPLACE.md) for the verified package boundary.

Before spending provider quota, run the offline doctor against the target repository:

```bash
./doctor.sh --repo /absolute/path/to/target
```

A green doctor result covers offline prerequisites only. It does not predict live
authentication, provider availability, sandbox permission, task quality, or dispatch
success. Version drift asks for explicit compatibility review; it does not silently
change the caller's model or effort choice.

agy needs network access and writable state under `~/.gemini`. Codex's
`workspace-write` sandbox therefore needs both the reviewed network setting and an
explicit `--add-dir ~/.gemini`. Follow the exact recipes and troubleshooting boundary
in [Installation and compatibility](docs/INSTALLATION.md).

## Use it from Codex

Use normal language and state the repository, allowed scope, desired result, and
driver-owned checks. For broader work, Codex can discover ordinary structure and test
commands instead of requiring a predeclared file list.

Personas remain optional prompt templates. They cannot select a repository, command,
model, authorization, verification result, or Git action. Model and effort selection
remain caller-owned, while recommendations remain advisory.

The advanced raw dispatcher has an opt-in, one-cycle `--boost` task profile for an
explicitly approved higher-authority experiment. Its provider-free preflight prints a
job-bound risk digest; the acknowledgement warns that Boost may invoke provider-side
subagents and protected tools, but grants no permission and widens no transmission
scope. Provider init identity is verified, and Boost failures cannot resume, restart,
or continue.

See [Usage](docs/USAGE.md) for workflow examples, manual invocation, read-only
inventory, common options, and explicit model-selection behavior.

## Project workflow

Progress-aware `project` jobs preserve useful candidates across status, wait,
verification, repair, and finalization. Controller state is local coordination data,
not provider truth. Only Codex's strict verification input may continue or finalize a
candidate; a fresh restart remains an explicit user decision.

See [Project workflow](docs/PROJECT_WORKFLOW.md) for lifecycle commands, Verification
v2, quality gates, recovery, cleanup, and Evidence Receipt v1.

## Explicit updates and tool compatibility checks

Updates are deliberate and source-verified; the runtime does not self-update. CI and
the optional local notifier observe narrow compatibility signals without authorizing
an apply, release, dispatch, or provider call.

If hosted Actions quota is unavailable, run the same provider-independent suite
locally:

```bash
./scripts/ci-offline.sh
```

See [Operations](docs/OPERATIONS.md) for CI evidence, updates, notifier maintenance,
compatibility observation, and privacy-safe reporting.

## See the evidence boundary in under a minute

Run `./proof-demo.sh` from the repository root. The quick-start section above owns
the command and its claim boundary; [Conformance](docs/CONFORMANCE.md) documents the
larger fixture contract.

## Sanitized bug reports and improvement requests

Use [SUPPORT.md](SUPPORT.md) to choose a public bug, feature request, or private
security route. Public submission is always a separate, byte-reviewed action; local
drafting never implies permission to publish.

## agy behaviour worth knowing

Exit 0 with empty output is not success. The runtime accepts a terminal result only
through its bounded structured envelope. Use `./ground-truth.sh` to inspect the local
interface before changing agy-facing claims, and read the troubleshooting notes in
[Installation and compatibility](docs/INSTALLATION.md).

## Reproducible offline benchmarks

The provider-independent benchmark and SWE-bench workflow study do not rank models,
route work, or influence `qa-gate` acceptance. See [Benchmarking](docs/BENCHMARKING.md).

## Roadmap

Current and deferred product slices live in the [product roadmap](docs/ROADMAP.md).
Source and tests—not release narrative—remain authoritative for current behavior.

## Layout

The [repository map](docs/REPO_MAP.md) owns entry points, data flow, trust boundaries,
and the verification command for each maintained surface.

## Limitations

- No persistent daemon, MCP server, or shared polling service. A started job has one
  narrow local controller with owner-private state.
- Native Windows is untested. Maintained entrypoints require a POSIX-compatible
  Bash/Python/git environment; WSL may work on a best-effort basis.
- agy is the only worker backend.
- Partial/promisor Git clones are unsupported for disposable worker worktrees.
- Unresolved merge resolve-undo metadata (REUC) rejects dispatch with resolve_undo_present; the controller never clears index metadata.
- Each job audits one worktree. Mutation across additional repositories is rejected.
- Provider transmission requires explicit approval for the selected mode and content;
  installation is never that approval. Provider scope is recommended for bounded jobs
  and binds exact reviewed entries plus their selected-content digest without turning
  scoped staging into a sandbox. Whole-worktree mode is an explicit manifest-bound
  exception.
- Direct model/effort selection has strong offline mechanism coverage but does not
  prove backend identity, availability, quality, cost, or quota efficiency.
- A green gate proves only the exact candidate, immutable base, path policy, and
  driver-owned commands it exercised. Human review and broader validation remain
  separate decisions.
- Public conformance proves only fixed fixture behavior. It is not certification or
  hostile same-user tamper resistance.
- GitHub submission is exercised with fake infrastructure; no live submission is
  implied by offline tests.

Deeper platform, lifecycle, compatibility, and maintenance limits are kept with their
task guides rather than duplicated here.

## Contributing and support

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing source or public claims.
- Use [SUPPORT.md](SUPPORT.md) for help and reporting routes.
- Report vulnerabilities privately through [SECURITY.md](SECURITY.md).
- Review [PRIVACY.md](PRIVACY.md) before any provider-backed dispatch.
- See [TERMS.md](TERMS.md) for the project terms.

## License

MIT — see [LICENSE](LICENSE).
