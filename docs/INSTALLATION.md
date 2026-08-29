# Installation and compatibility

Use this guide when installing `agy-worker`, checking its local prerequisites, or
diagnosing a compatibility or Codex sandbox failure. The GitHub repository is the
source of truth: review the exact commit or reviewed release tag before installing.
Installation enables the local skill only. It does **not** authorize a provider call
or transmission of repository content through `agy` to Google/Gemini.

## Prerequisites

The maintained entrypoints require a POSIX-compatible environment with Bash, Python
3, git with worktree support, Codex CLI, and `agy` (Antigravity CLI) on `PATH`.
Native Windows is untested; WSL or another compatible environment may work on a
best-effort basis. Some evidence commands use fixed POSIX paths, and the optional
daily notifier is specifically a macOS LaunchAgent.

Before spending provider quota, run the offline doctor against the repository you
intend to delegate:

```bash
./doctor.sh --repo /absolute/path/to/target
./doctor.sh --repo /absolute/path/to/target --format json
```

The doctor is deterministic and read-only. It checks the bundled runtime, Bash 3.2,
Python 3, git and worktree support, the target Git worktree, exact semantic
`agy --version`, and checked-in compatibility records. It invokes no provider,
network client, updater, dispatch, authentication probe, or personal-config scan,
and it repairs nothing.

| Exit | Overall | Meaning |
|---:|---|---|
| `0` | `ready` | All offline prerequisites match the checked-in evidence. |
| `3` | `review-required` | Prerequisites work, but the agy version drifted or review is due. |
| `3` | `not-ready` | A prerequisite, repository, bundle, or metadata check failed. |
| `64` | no report | Invocation or format is invalid. |

`ready` does not certify authentication, provider availability, Codex/agy sandbox
permission, task quality, or a future dispatch. `review-required` never updates
metadata and is not a blanket dispatch lock: agy's own default and an explicitly
approved literal-model pass-through remain separate caller-owned surfaces.
`not-ready` blocks dispatch.

## Choose an installation path

### Codex Git marketplace

The primary first-visit commands are in the repository README. Read the
[marketplace contract](MARKETPLACE.md) for the root-source package layout, immutable
Git-ref verification, and installed-source byte-parity boundary. The marketplace and
clone paths resolve the same canonical `skills/agy-worker/` bundle; no second runtime
is created.

After installation, start a new Codex session so the skill is rediscovered.

### GitHub clone

Review the selected source commit, then install the canonical skill bundle:

```bash
git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker
./install.sh
```

`install.sh` installs the Codex skill only. It copies the canonical bundle and writes
a local pointer so checkout-only maintenance commands remain available; it does not
rewrite the public `SKILL.md` or install an additional runtime.

For a released snapshot, check out the exact reviewed `vMAJOR.MINOR.PATCH` tag from
the [GitHub Releases page](https://github.com/cagdasyurekli/codex-agy-worker/releases)
before running `./install.sh`; do not substitute an unverified tag.

### Folder-only or third-party copy

`skills/agy-worker/` is the one canonical, self-contained Agent Skill. A folder-only
copy contains its Bash/Python/git runtime and downloads no code when invoked. Resolve
the installed runtime as documented in
[`skills/agy-worker/SKILL.md`](../skills/agy-worker/SKILL.md), then run:

```bash
"$PIPELINE/doctor.sh" --repo /absolute/path/to/target
```

The bundle may also be copied with the third-party skills CLI:

```bash
DO_NOT_TRACK=1 npx skills add cagdasyurekli/codex-agy-worker \
  --skill agy-worker --copy
```

`npx` is only an optional installer. The installed skill has no Node runtime
dependency. Review the copied files before use.

## Required Codex sandbox settings

agy starts a local language server and writes state under `~/.gemini`. Under Codex's
default `workspace-write` sandbox it fails with **exit 5 and empty stderr** unless
both the socket and writable-directory requirements are satisfied.

Add this setting to `~/.codex/config.toml`:

```toml
[sandbox_workspace_write]
network_access = true
```

Then launch an interactive Codex session with:

```bash
codex --add-dir "$HOME/.gemini"
```

For a one-off `codex exec` invocation, pass both settings explicitly:

```bash
codex exec --sandbox workspace-write --add-dir "$HOME/.gemini" \
  -c 'sandbox_workspace_write.network_access=true' "<your task>"
```

The writable directory alone is insufficient because the language-server socket bind
also needs network access. Do not use dangerous permission or approval bypass flags.

## Version drift and direct model selection

The accepted model/effort mapping, exact agy version, and evidence digests live in the
current [activation record](../compat/reviews/agy-1.1.22-activation.md). Historical
observations remain history; they do not override the current source and checked-in
matrix. Codex compatibility evidence is observational and grants neither dispatch nor
model-selection authority.

Every reviewed direct selection first checks a safe executable with bounded semantic
`agy --version` and a strict critical `agy --help` structure probe. An exact
matrix-version match proceeds mechanically after that structural probe. Compatible
version drift requires Codex's explicit
`--compatibility-disposition proceed --approve-help-sha SHA256`; a structurally
incompatible interface blocks reviewed direct selection.

To review compatible drift without disclosing an executable pathname, inspect the
bounded local `agy --help` bytes and calculate their raw SHA-256:

```bash
LC_ALL=C agy --help 2>&1 | /usr/bin/python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
```

Compare that digest with the sanitized `raw_help_sha256` review output, then retry the
same caller-selected `--model`/`--effort` request with
`--compatibility-disposition proceed --approve-help-sha` set to the matching digest.
A mismatch, changed help, or unavailable probe needs a fresh review; never reuse an
older digest.

Structural acceptance is not semantic approval. Before every reviewed direct
dispatch, including an exact-version match, Codex must inspect current bounded raw
`agy --help` and stop if the exact caller-selected model or effort cannot be honored.
The caller's resolved slug remains unchanged, model availability is
`not_assessed`, and controller help prose is never availability evidence.

Model and effort choices belong to the caller. With no selector, leave agy's default
unchanged. The narrow `--literal-model` surface is an unreconciled caller-owned
pass-through; it makes no compatibility, cost, provider, availability, or quality
claim. See [the usage guide](USAGE.md#model-and-effort-selection) for the public
selection boundary.

## agy interface cautions

Run `./ground-truth.sh` against the installed agy before changing agy-facing flags or
claims. Its default interface phase calls only `agy --version` and `agy --help`; use
`./ground-truth.sh --account` only when you explicitly authorize inspection of
account-owned agy state such as models, agents, plugins, and local permissions.

- Build `--print` last: its next argument is the prompt, and print mode ignores stdin.
- Exit 0 plus empty output is not success. The worker accepts a terminal result only
  through its bounded structured envelope.
- agy's `--agent` disables `--json-schema`; personas are therefore injected as
  bounded prompt text instead of using that flag.
- In agy's sandbox, shell tools run in its scratch directory rather than the target
  repository. Worker prompts use file tools; Codex owns repository commands.
- Classify authentication, quota, timeout, or provider failures only from reviewed
  exact signatures. Never turn free-form error prose into an automatic retry.
- The terminal answer is in `result.structured_output`.
  `result.json_schema` is the echoed schema, not the answer.
- Unknown agy subcommands may print usage and exit 0; do not probe support by exit
  status alone.
- Prefer narrow permission allow-rules. Never use
  `--dangerously-skip-permissions` or an approval/sandbox bypass.

## Troubleshooting order

1. Confirm you reviewed the exact installed source commit or release tag.
2. Run `./doctor.sh --repo /absolute/path/to/target` and respect `not-ready`.
3. Confirm both Codex sandbox settings above are active.
4. Run `./ground-truth.sh` before interpreting an agy interface change.
5. For reviewed direct selection, inspect current raw help and handle drift without
   changing the caller's model or effort.
6. If local prerequisites pass but a provider call fails, preserve the sanitized
   result and follow the [project workflow](PROJECT_WORKFLOW.md); do not add a shell
   retry loop.

For privacy, support, and project terms, see [PRIVACY.md](../PRIVACY.md),
[SUPPORT.md](../SUPPORT.md), and [TERMS.md](../TERMS.md).
