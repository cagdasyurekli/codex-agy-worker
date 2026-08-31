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
locally installed Antigravity CLI (`agy`), an external tool backed by Google/Gemini
services. Without `--provider-scope`, treat the entire disposable `--workdir` as
worker-readable and potentially transmissible to that service, even when the task
names only a few paths. Prompt denylist instructions, `qa-gate --only`, `--allow`, and
`--add-dir` do not narrow that default read boundary.

Optional `--provider-scope FILE --approve-transmission-sha SHA256` binds exact reviewed
read entries, their selected-content digest, and a write subset, then copies only
selected entries into a fresh owner-private mode-`0700` Gitless provider cwd. The
controller still locally enumerates and validates the complete worktree path/kind
surface and scope entries before staging. Scoped mode reduces provider-visible content
but is not filesystem, network, `PATH`, `HOME`, or same-UID isolation and retains the
documented local-owner and mutation-race residuals. Its approval grants no provider
execution, Git action, driver acceptance, or publication.

Before each initial, resumed, continued, or restarted provider attempt, approve the
exact mode and transmission digest. Credentials, secrets, private keys, regulated or
user-denied data, and unrelated private files must be absent from the entire default
worktree transmission or every entry selected for scoped staging. Telling the worker
not to read approved content is not a privacy control.

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
benchmark execution remains out of scope for this repository.

The SWE-bench Workflow Study v1 tool is fully offline and uses no network,
provider, telemetry collection, or analytics service. It only reads explicit, user-supplied
caller-owned mode-`0600`, one-link regular plan and result files through bounded no-follow
descriptors and writes deterministic sanitized,
hash-linked canonical artifacts to a user-selected external owner-0700 result root.
Every stored string is a bounded privacy-safe identifier. Artifacts omit source code, prompts,
diffs, logs, task bodies, absolute paths, timestamps, identities, and credentials.

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
bug-report and improvement drafts are local files with mode `0600` until a user
explicitly confirms the exact SHA-256 and separately confirms that same digest is
public-safe before submitting it. A `--kind security` draft is private-route only and
cannot be submitted publicly. Conservative keyword detection is an additional barrier,
not proof that another report is safe for public disclosure.

The optional feedback triage command and weekly workflow read only a fixed bounded
page of public issue metadata: issue number, canonical repository URL, and creation
and update timestamps. They discard titles, bodies, comments, labels, usernames, and
all other raw GitHub content before aggregation. The only output is a bounded
aggregate; neither sends issue content to an agent nor modifies an issue.

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

When explicitly requested with `--timing-report <PATH>`, `scripts/ci-offline.sh`
publishes one mode-`0600`, no-overwrite timing report to an owner-private mode-`0700`
directory. It first requires a clean tracked/untracked worktree, then binds only the
exact current HEAD commit SHA, deterministic canonical stage inventory digest, gate
outcome, and observational monotonic wall-clock
durations per suite. It strictly excludes file paths, commands, environment values,
logs, credentials, provider/account data, timestamps, host identity, and cost claims.

CI shard execution (`scripts/ci_sharding.py` and `scripts/ci-offline.sh --shard`) publishes
one mode-`0600`, no-overwrite shard receipt to a mode-`0700` local directory. It binds
only the schema version (currently v2), kind (`agy-worker-ci-shard-receipt`), exact lowercase Git HEAD commit SHA,
deterministic canonical stage inventory digest, shard ID, expected stage IDs, observed stage IDs,
per-stage monotonic durations, outcome, and fixed unsigned integrity statement. It strictly excludes repository paths, commands,
environment values, logs, credentials, provider/account data, timestamps, host identity, and cost claims.
GitHub Actions uploads these deliberately privacy-safe receipts as one-day workflow artifacts;
uploaded copies inherit repository Actions access rather than local filesystem ownership.
The aggregate `test` check verifies all four receipts within the same workflow run and fails closed
on missing, malformed, duplicate, version-stale, or mismatched evidence. Standalone validation retains
read-only compatibility with the smaller historical v1 receipt and its stage-identity digest.

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

The explicit-account models capture runner is a separate, never-automatic action.
Its checked-in tests use only disposable synthetic account roots and make no agy,
provider, or network call. Every production invocation requires separate user
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
step for that action. It accepts only explicit stdin paths, does not inspect
HOME contents or ambient configuration, and validates no-follow external source,
snapshot, and version evidence before atomically creating one owner-private canonical
profile. It never invokes agy, a provider, a network client, a shell, or a Git
command; preparing a profile does not authorize a capture.

`scripts/models_capture_1_1_22_classifier.py` is the separate sidecar maintenance
tool for diagnosing sanitized 1.1.22 models capture failure evidence. It requires an
explicit owner-private (`0700`) directory path, never scans for evidence, and enforces
strict fail-closed checks on permissions, topology, and artifact hashes. Its output is
a mode-`0600` canonical JSON record containing only the classified category, origin,
hashes, and enforced limitations. It excludes all raw stderr/stdout text, error prose,
absolute filesystem paths, and account identifiers, and grants no activation, retry,
or routing authority.

`scripts/models_capture_1_1_22_reprofile.py` is a separate, process-inert reprofile
preparation adapter that accepts an already-validated prior 1.1.22 capture profile
and produces a new profile reflecting exactly one permitted change:
`account_home_identity.nlink`. It opens the explicitly supplied account HOME only for
no-follow descriptor metadata; it never enumerates or reads HOME contents. Its output
is a mode-`0600` canonical profile in a distinct owner-private output root. It has no
subprocess, network, Git, retry, capture, inventory acceptance, routing, or activation
authority. Recovery validation is bounded to the explicitly supplied recovery root's
fixed artifact and scratch allowlists; it does not expand into HOME discovery.
Reprofiling does not authorize a capture or renew any prior one-call authorization.

`codex-usage-report.sh` observes Codex orchestration usage locally via the live,
bounded stdio JSONL protocol of the version-pinned Codex CLI 0.150.1 app-server. Before
observation it checks the exact CLI version and the SHA-256 of the CLI-generated
experimental combined schema; drift fails closed.
It requires explicit `--task LABEL=THREAD_ID` arguments and optional owner-private
(`0600`) `--session LABEL=ABS_FILE` inputs. The tool strictly redacts all sensitive
data: prompts, message text, raw logs, thread IDs, account emails/identities, cwd, and
file paths are never emitted. The tool never scans for sessions; it opens only the
explicit path, validates its private metadata, and inspects allowlisted structural
fields. Output reports categorize input, cached input,
net-new input, cache-write, output, and reasoning separately; reasoning is a subset of
output and is never double-counted. When the app-server supplies a per-thread estimated
credit value, it is emitted only as integer micros labeled `provider_estimate`; absent
values remain unavailable. Account-wide observation is limited to rate-limit fields and
does not invent an aggregate credit value. Rate-limit observations remain separate, and the tool never
infers USD/currency or remaining billing quota. All process groups are reaped on exit or
timeout. Token comparisons are directional only.

## Support and changes

Questions about this disclosure can be opened through the route in
[SUPPORT.md](SUPPORT.md) without including private content. Material changes to the
project's data flow should update this document before a public release.
