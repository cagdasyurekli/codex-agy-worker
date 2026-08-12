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

The public conformance kit itself uses only checked-in synthetic content in private
disposable local repositories and invokes no agy, provider, or network client. Its
`--gate` argument is executable code selected by the user and runs with that user's
normal privileges; the kit does not sandbox it or prevent a hostile implementation
from reading files or using the network. Review a supplied gate before running it.
The supplied gate and loaded code, local owner and same-UID processes, and OS
administrators are trusted for cleanup pathname stability. Cleanup is descriptor-
relative while exact parent/root identities remain unchanged; drift produces a
sanitized failure and may leave a private residual. The runner never scans for or
chases a moved directory and makes no same-user tamper-resistance claim. The kit
discards bounded gate output and reports no fixture paths or captured bytes.

The offline benchmark harness likewise invokes no agy, provider, or network client.
It uses only hash-bound checked-in synthetic candidates and the canonical local gate.
Its explicit external owner-`0700` result root contains mode-`0600` plans, Evidence
Receipts, and results: source/tool/fixture/selection hashes, immutable synthetic Git
bases and candidate-state hashes, bounded gate facts, and unsigned integrity labels.
It stores no provider prompt, response, usage, pricing, credential, or raw worker log.
The report is a pure validated completeness view and uploads nothing. Live
benchmarking is not implemented.

The persona evidence registry is also local and read-only. It validates only
checked-in public persona, registry, schema, and P1-C contract hashes and renders a
bounded deterministic table. It does not read target repositories, prompts, worker
logs, private evidence, personal configuration, or environment-selected registries;
it performs no dispatch, benchmark, gate, provider, or network operation. Checkout
upper-state validation runs fixed read-only Git object queries with global/system
configuration disabled; it never follows an evidence pathname into private storage.
Future non-offline states require public canonical evidence, approval/review, and
transition blobs in strict ancestry. Private evidence cannot be described as public.

The workload-profile command is local, read-only, and data-only. It reads only the
fixed bundled v1 manifest, schema, and three public profile records and writes one
canonical JSON value to stdout. It never reads a target repository, caller path,
home-directory profile, environment-selected source, personal configuration, prompt,
log, or private evidence, and invokes no git, agy, provider, or network client. A
shown profile is not authorization and contains no repository/path, selected model or
tier, verifier command, dispatch, route, acceptance, or external action.

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
`0600` and never overwrites. Text, canonical JSON, Markdown, and GitHub Step Summary
formats contain only bounded verdict/outcome labels, hashes, deterministic verifier
labels, binding-presence flags, and fixed integrity and human-review statements. The
reporter never discovers or writes `GITHUB_STEP_SUMMARY`; a workflow must redirect
its stdout explicitly. It excludes source, diffs, prompts, worker prose, raw
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

The optional local update notifier stores a canonical status, result fingerprint,
source-manifest hashes, and resumable install/uninstall state under the account's
owner-private Application Support directory. It does not store raw compatibility
output, repository content, credentials, prompts, provider data, or personal paths in
notifications. The notifier itself has no independent network or mutating Git
authority; its hash-bound child invokes the existing read-only `update.sh check
--watch`, which performs fixed bounded HTTPS requests and local Git inspection with
global/system configuration disabled. It never applies an update or invokes agy,
Codex work, or a provider. Uninstall preserves replacement or ambiguous recovery
state rather than deleting it. A displayed macOS notification cannot be retracted.

The optional adoption ledger is explicit local input, not telemetry. It stores only
closed aggregate values, denominators/sample sizes, opaque observation IDs, UTC dates,
exact public repository revisions, and allowlisted public GitHub evidence URLs in an
owner `0600`, one-link file. It never discovers a ledger, reads HOME, calls a process
or network, or stores prompts, logs, accounts, tokens, provider usage, or user IDs.

The explicit-account models capture runner is a separate, never-automatic future
action. Its checked-in tests use only disposable synthetic account roots and make no
agy, provider, or network call. A production invocation would require separate user
authorization for the exact canonical owner-`0700` account HOME/profile and one
snapshot-backed `agy models` call. The external CLI may read account contents or use
credentials under its own behavior. It may also write or mutate normal HOME state and
create caches. The runner does not enumerate those contents and cannot detect,
prevent, or revert HOME changes; account residuals may remain even when capture
rejects. The account HOME, local owner and same-UID processes, reviewed source and
interpreter, and OS administrators are trusted.

After group closure, capture-owned TMP/XDG/cwd must be unchanged and empty. The fixed
1.1.12 JSON capture bridge has one narrower reviewed exception: it may hash and
compare-delete the exact owner-private bounded language-server schema cache leaf in
its own TMP, fsync, and then prove scratch is empty. Every other cache shape rejects
and remains a private residual. On a
bounded exit-zero observation the runner retains otherwise uninterpreted raw
stdout/stderr, exact profile and runner bytes, bounded summary, and capture record in
a new owner-private directory; files are mode `0600` and raw bytes are never printed.
The sanitized console JSON contains only the artifact root, capture SHA-256, and
`captured` status. The final marker is `models.capture.sha256`, not an accepted
binding. Output semantics such as authentication, license, permission, quota,
rate-limit, interactive, or inventory content are decided only by later offline
reconciliation. Nonzero, overflow, timeout, identity/scratch drift, or publication
failure publishes no final marker. The runner never logs in, prompts, retries, falls
back, dispatches a task, selects or routes a model, changes metadata, or uploads the
artifacts. The user controls retention and must not commit the private profile or raw
evidence.

`scripts/models_capture_profile.py` is the separate, process-inert preparation
step for that future action. It accepts only explicit stdin paths, does not inspect
HOME contents or ambient configuration, and validates no-follow external source,
snapshot, and version evidence before atomically creating one owner-private canonical
profile. It never invokes agy, a provider, a network client, a shell, or a Git
command; preparing a profile does not authorize a capture.

## Support and changes

Questions about this disclosure can be opened through the route in
[SUPPORT.md](SUPPORT.md) without including private content. Material changes to the
project's data flow should update this document before a public release.
