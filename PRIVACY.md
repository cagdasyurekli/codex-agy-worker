# Privacy disclosure

This document describes the data behavior of the open-source
`codex-agy-worker` project and its packaged Agent Skill. It is a project policy,
not a claim about every version or configuration of the third-party tools it calls.

## What the project itself does

The repository does not operate a hosted service, collect analytics, or send
telemetry on its own. Model-tier recommendations and all offline test suites run
locally. Installing the skill copies its public workflow files and a local pointer to
the checkout; it does not contact a network service or change agy, Codex, or Claude
configuration.

## When data can leave the machine

When a user explicitly dispatches a job, `agy-worker.sh` passes the task prompt to the
locally installed Antigravity CLI (`agy`). `agy` is an external tool backed by
Google/Gemini services. The task text and repository content that the worker reads
from driver-approved roots can therefore be transmitted to and processed by that
external service under its own terms and privacy policy.

The skill requires the driver to identify the repository and allowed paths and obtain
explicit approval for that transmission before the first dispatch unless the user
already approved that exact scope. Do not put credentials, private keys, regulated
data, or unrelated files in a prompt or an allowed root.

The project does not automatically submit GitHub issues, push code, merge branches,
or publish releases. Those are separate actions with separate approval boundaries.

## Local artifacts and retention

Each job can create local private artifacts under `logs/<job>/`, including the task,
full prompt, agy stream, stderr, staged oversized prompt, and extracted envelope.
Temporary worktrees and envelopes may also exist outside the repository. Sanitized
bug-report drafts are local files with mode `0600` until a user explicitly confirms
the exact SHA-256 and submits them.

The project does not delete these artifacts automatically. The person running the
tool controls retention and should review and remove unneeded artifacts according to
their own policy. Do not commit or paste raw logs into public reports.

## Support and changes

Questions about this disclosure can be opened through the route in
[SUPPORT.md](SUPPORT.md) without including private content. Material changes to the
project's data flow should update this document before a marketplace release.
