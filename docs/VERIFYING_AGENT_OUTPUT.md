---
layout: default
title: "How to verify AI coding-agent changes without trusting the worker report"
description: "A practical, source-backed workflow for checking Git scope and running driver-owned verification before accepting an AI coding-agent candidate."
canonical_url: "https://cagdasyurekli.github.io/codex-agy-worker/VERIFYING_AGENT_OUTPUT.html"
---

# How to verify AI coding-agent changes without trusting the worker report

An AI coding worker can say that it changed only the requested files and ran the
right tests. That report is useful context, but it is not acceptance evidence. The
repository—not the report—must answer what changed, and the driver must choose and
run the checks that decide whether the candidate is acceptable.

This guide shows the evidence boundary implemented by **codex-agy-worker**. It is a
bounded engineering workflow, not a security certification or a claim that every
semantic defect can be detected automatically.

## 1. Freeze the base before dispatch

Work in a branch-backed disposable Git worktree and record its immutable base commit
before the worker starts:

```bash
BASE="$(git -C "$WT" rev-parse HEAD)"
```

The base gives the driver an independent comparison point. A worker-provided list of
changed files cannot replace it.

## 2. Keep the worker report as untrusted input

Dispatch only after approving one exact transmission mode. Without
`--provider-scope`, the entire disposable worktree may be read and sent through `agy`
to Google/Gemini; `--add-dir`, prompt denylists, and gate path policies do not narrow
that default boundary. Optional provider-scope mode instead binds exact reviewed read
entries, their selected-content digest, and a write subset, then stages only selected
entries in a fresh owner-private mode-`0700` Gitless provider cwd. Exclude credentials,
secrets, denied paths, and unrelated private content from every approved entry.

```bash
echo "$TASK" | AGY_WORKER_MODE=accept-edits ./agy-worker.sh \
  --workdir "$WT" --add-dir "$WT" > envelope.json
```

The envelope may contain `files_changed`, `commands_run`, and `tests_run`. The gate
parses those fields as claims; it never executes worker-supplied shell text.

## 3. Derive scope from Git

Declare the paths that the task is allowed to change and compare the actual candidate
against both that policy and the envelope:

```bash
./qa-gate.sh \
  --envelope envelope.json \
  --repo "$WT" \
  --base "$BASE" \
  --only 'tests/**' \
  --expect-edits \
  --verify-argv '["python3","-m","pytest","-q","tests/test_parser.py"]'
```

The maintained gate rejects undeclared or missing paths, outside-policy edits,
malformed envelopes, an unexpected no-op, mutable base evidence, and verifier-created
mutations. It also accounts for nontracked paths, including ignored files, within its
documented trust boundary.

This is a write-acceptance boundary applied after dispatch. In default mode,
`--only`, `--allow`, prompt denylists, and `--add-dir` do not prevent reads elsewhere
in `--workdir`. Optional provider-scope staging is a distinct pre-dispatch content
boundary: it copies only selected entries, but the controller still locally enumerates
and validates worktree/scope paths. It is not filesystem, network, `PATH`, `HOME`, or
same-UID isolation, and scope approval grants no execution, Git, acceptance, or
publication authority. See [selected-content dispatch](USAGE.md#optional-selected-content-dispatch).

## 4. Let the driver own verification

At least one driver-owned verifier is mandatory. The normal repeatable path is a
strict canonical `--verify-argv` JSON string array chosen from the target repository's
source, configuration, and documented test surface—not from the worker report.
Multiple arrays run in order from the repository root without an implicit shell;
direct shell interpreters and every `env -S`/`--split-string` form are rejected.
Advanced `--verify-shell` and legacy `--verify` compatibility require both network
and credential-access acknowledgements. Ordinary exact-name environment opt-ins use
`--verify-env`; credential-like names instead require `--verify-credential-env` and
the credential-access acknowledgement. A passing check proves only the
exercised command against the exact candidate. Review the diff for semantic correctness
and run additional checks when the change warrants them.

The useful distinction is simple:

| Evidence | What it can establish |
|---|---|
| Worker report | What the worker claims happened |
| Git comparison | Which repository paths actually changed |
| Driver-owned check | Whether the selected check passes on the candidate |
| Human review | Whether the change makes sense beyond automated checks |

## 5. Preserve honest outcomes

A failed check is normally a bounded repair signal, not permission to erase a useful
candidate or start an unbounded retry loop. codex-agy-worker reports `verified` only
after its strict evidence policy passes; otherwise it preserves distinctions such as
`partially_verified`, `rejected`, or `blocked`.

## Try the boundary without a provider

From a reviewed clone:

```bash
./proof-demo.sh
```

The demo creates two private synthetic repositories, exercises one exact passing edit
and one rejected scope mismatch, and then removes both. It invokes no provider or
network and changes neither the checkout nor credentials. These two fixtures are a
fast introduction, not proof about a real coding task.

Continue with the [project README](https://github.com/cagdasyurekli/codex-agy-worker#readme),
the [bounded gate contract](CONFORMANCE.md), or the
[privacy disclosure](https://github.com/cagdasyurekli/codex-agy-worker/blob/main/PRIVACY.md).
