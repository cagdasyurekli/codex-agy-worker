---
layout: default
title: "codex-agy-worker — bounded Codex to agy delegation"
description: "Delegate bounded coding work from Codex to Antigravity CLI, then independently check Git scope and driver-owned verification before acceptance."
canonical_url: "https://cagdasyurekli.github.io/codex-agy-worker/"
---

# Bounded Codex to agy delegation, independently checked

**codex-agy-worker** is an open-source Agent Skill and command-line pipeline for
delegating bounded coding tasks from Codex to Google's Antigravity CLI (`agy`). It
treats every worker report as an untrusted claim, independently checks Git scope, and
runs only driver-owned verification commands before accepting a candidate.

<div class="callout">
The worker can propose changes. Only the evidence gate can accept them, and exit 0
means only that the configured scope and verification checks passed. It does not
prove general correctness or security, and it does not commit, push, merge, or
release anything.
</div>

## What it is for

- Backfilling tests across many files.
- Repeating the same bounded edit mechanically.
- Read-only repository inventories that are independently spot-checked.
- Diff reviews used as a second opinion, never as acceptance evidence.

Each edit job runs in one branch-backed Git worktree. The driver captures an immutable
base commit, declares allowed paths, and supplies the verification commands before
dispatch. The gate rejects undeclared files, outside-policy changes, malformed worker
output, missing edits, failed verification, and verifier-created mutations.

## Small runtime, explicit boundaries

The project uses Bash, Python 3, and git. It has no Node runtime dependency, MCP
daemon, or background polling service. Model-tier recommendations are visible and
advisory: they include rationale, driver-owned evidence, and relative cost impact but
never change the selected model automatically.

Dispatches use the external Antigravity CLI and can send approved task text and
repository content to Google/Gemini. Read the [privacy disclosure](https://github.com/cagdasyurekli/codex-agy-worker/blob/main/PRIVACY.md)
before use.

## Install and explore

Use the GitHub repository as the source of truth. Review the cloned commit—or the
exact release tag selected from
[GitHub Releases](https://github.com/cagdasyurekli/codex-agy-worker/releases)—before
installing the standalone Codex skill:

```bash
git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker
./install.sh
```

Checked-in repository files do not alter GitHub About fields, topics, homepage
metadata, or social-preview settings. Those remain deliberate repository-owner
actions.

[Read the full documentation](https://github.com/cagdasyurekli/codex-agy-worker#readme)
or [inspect the source and offline tests](https://github.com/cagdasyurekli/codex-agy-worker).
