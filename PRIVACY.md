# Privacy disclosure

This document describes the data behavior of the open-source
`codex-agy-worker` project and its packaged Agent Skill. It is a project policy,
not a claim about every version or configuration of the third-party tools it calls.

## What the project itself does

The repository does not operate a hosted service, collect analytics, or send
telemetry on its own. Model-tier recommendations, Evidence Receipt v1 creation and
validation, and all offline test suites run locally without a network or provider.
Installing the skill copies its public workflow files and a local pointer to
the checkout; it does not contact a network service or change agy or Codex
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

When explicitly requested, `verify-job.sh` creates one local receipt at a new path the
user chose in an owner-private directory outside the audited repository. It records
the immutable base; SHA-256 hashes of the exact envelope snapshot, ordered path
policy, verifier commands, and candidate states; bounded gate outcome labels; and,
when supplied, the validated caller model/tier selection and canonical pre-dispatch
advisory (including its rationale, controlled evidence, and relative cost statement).
It does not store source or diff content, repository paths, prompts, worker prose,
raw logs, verifier commands or output, credentials, provider telemetry, or pricing.
Receipts are mode `0600`, unsigned, not self-authenticating, and never uploaded by
this command. The internal gate evidence descriptor is closed before any verifier
shell or interpreter starts. The wrapper removes executable shell/Python startup
controls only from the evidence-mode gate and verifier environment; it does not read
or modify the caller's configuration. Handled HUP, INT, and TERM interruptions remove
wrapper-owned snapshot, handoff, temporary, and partial receipt files. The user
controls durable receipt retention just like other local artifacts.

`evidence-report.sh` reads one explicitly named receipt and, when supplied, only the
explicitly named binding artifacts. It performs no dispatch, routing, gate, git, or
network action. Standard output is the default; an explicit new report file is mode
`0600` and never overwrites. The report contains only bounded verdict/outcome labels,
hashes, deterministic verifier labels, binding-presence flags, and fixed integrity
and human-review statements. It excludes source, diffs, prompts, worker prose, raw
commands or output, logs, credentials, and absolute repository paths. The report is
still unsigned and cannot authenticate a rewritten receipt.

`job.sh` stores one explicitly named mode-`0600` lifecycle state file in an
owner-private external directory. It contains canonical absolute repository,
worktree, Git-common-directory, and receipt paths; filesystem identities; the exact
branch, immutable base, and job ID; state-history, receipt, and candidate SHA-256
bindings; gate exit/verdict; and cleanup progress. `status` emits only bounded state,
match, and hash facts. `preserve-instructions` prints local Git commands and paths
only when explicitly requested; it executes none. The lifecycle performs no network,
provider, dispatch, commit, or publication action. Rejected-only cleanup removes the
exact registered disposable worktree and unchanged branch ref after fresh explicit
hash approvals, but deliberately retains the cleaned private state tombstone. Partial
or ambiguous states are retained for manual recovery rather than automatically
deleted.

Lifecycle-owned Git execution ignores system/global and caller Git configuration,
uses a private empty hooks directory, and disables prompts, pagers, fsmonitor,
external diff, protocols, and recursive submodules. Before worktree creation it
rejects local included hook/helper/filter configuration and any effective base-tree
or repository-info content-filter attribute. It therefore does not grant repository
hooks or filters execution authority during lifecycle initialization. Fatal or
ambiguous ref evidence is never treated as absence and retains the truthful recovery
state.

The project does not delete these artifacts automatically. The person running the
tool controls retention and should review and remove unneeded artifacts according to
their own policy. Do not commit or paste raw logs into public reports.

## Support and changes

Questions about this disclosure can be opened through the route in
[SUPPORT.md](SUPPORT.md) without including private content. Material changes to the
project's data flow should update this document before a public release.
