---
layout: default
title: "Verified agy Worker — evidence-gated AI coding delegation"
description: "Delegate bounded mechanical coding work to Google Antigravity CLI, then verify repository scope and driver-owned tests before accepting the result."
canonical_url: "https://cagdasyurekli.github.io/codex-agy-worker/"
---

# Delegate to agy. Verify before you trust.

**Verified agy Worker** is an open-source Agent Skill and command-line pipeline for
delegating bounded, mechanical coding tasks from Codex or Claude Code to Google's
Antigravity CLI (`agy`). Its differentiator is not another bridge: it treats every
worker report as an untrusted claim and re-derives acceptance evidence from Git and
driver-owned verification commands.

<div class="callout">
The worker can propose changes. Only the evidence gate can accept them, and exit 0
still does not commit, push, merge, or release anything.
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

Clone and install the standalone Codex skill:

```bash
git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker
./install.sh
```

The same canonical Agent Skill is packaged for Codex/ChatGPT plugins and Claude Code
marketplaces. Public-directory listings require their platform reviews; until then,
use the repository-backed instructions in the
[marketplace runbook](https://github.com/cagdasyurekli/codex-agy-worker/blob/main/docs/MARKETPLACE.md).

[Read the full documentation](https://github.com/cagdasyurekli/codex-agy-worker#readme)
or [inspect the source and offline tests](https://github.com/cagdasyurekli/codex-agy-worker).
