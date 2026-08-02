# Architectural lessons

These are durable prevention rules for `codex-agy-worker`. This file is not a
release log, task diary, or list of completed work.

## A worker report is never evidence

The worker is outside the trust boundary. Its envelope is useful for routing and
scope comparison, but every claim must be re-derived by the driver.

- Validate the complete envelope shape before reading it.
- Compare every declared path and change kind with Git reality.
- Never execute `commands_run` or `tests_run`; they are untrusted text. Only
  driver-authored `--verify` commands may execute.
- Require a successful gate and human diff review before preserving or integrating
  a candidate. A confident summary or high confidence score changes nothing.

## Git scope must be immutable and complete

Capture the full commit ID before dispatch. Mutable names such as `HEAD` or branch
names let the comparison point move and invalidate the audit.

Constrain edit jobs with driver-owned `--only` policies, check declared-versus-
actual paths and change kinds, and include ignored as well as ordinary untracked
files. An allowlisted artifact remains auditable and must not satisfy
`--expect-edits`. Snapshot the complete Git-visible candidate before and after
verification so a passing verifier cannot rewrite it. Use a branch-backed disposable
worktree so rejected changes are isolated and accepted changes are not destroyed by
cleanup.

## Updates are explicit and trust official sources

`update.sh check` is read-only. `update.sh apply` is an explicit human-authorized
operation; never run it in the background or as part of a worker job. Production
release origin, agy upstream, and review cadence must not be environment-overridable.
Reconcile the official CLI documentation, official source repository, live
`ground-truth.sh` output, and a bounded real job before advancing compatibility
metadata.

A disposable candidate worktree isolates files, not execution. Candidate validation
runs release-owned scripts with the invoking user's privileges. Exact tag/ref and
fast-forward checks prove transport consistency, not that candidate code is harmless.
Keep the expected-origin boundary, protect the release account and tag process, and
do not describe candidate execution as a sandbox.

## Reporting must bind review to the uploaded bytes

Generate local drafts atomically with mode `0600` and refuse overwrite. Sanitize
caller-provided text before rendering, validate the complete rendered body before
hashing, and validate again when loading it for preview or submission. Redaction is a
conservative filter, not proof of privacy; the human must review the exact body.

Avoid the upload time-of-check/time-of-use bug: never validate one mutable path and
later ask another process to reread it. Submit the already validated in-memory bytes
over stdin, or use an equally immutable private snapshot. Bind the destination to the
intended public GitHub host, require the exact reviewed SHA-256, and never collect
prompts, source, envelopes, credentials, private paths, or raw logs automatically.

## Model routing is explicit

The caller selects the tier. Built-in retries reuse the same model; gate failures do
not silently increase cost or reasoning effort. Keep recommendation policy outside
both dispatch and gate acceptance: its output must be visible, state the current tier,
show controlled driver-owned evidence and relative cost impact, and say explicitly
that it was not applied. Do not add a separate thinking-level abstraction; where agy
exposes thinking, it is already part of the caller-selected model label.

Only an independently observed, bounded quality or verification gap can justify
recommending a higher named tier. Permission, authentication, scope-policy, contract,
untrusted-claim, and human-required failures need correction at their own boundary,
not more model spend. Do not infer ordering for agy's `default` choice or a custom
model label, and do not invent a tier above the highest named tier. Reject ambiguous
or cross-stage evidence rather than guessing.

## A rejected worker can prove the gate works

The real Playbook-Gemini exercise exposed the distinction between worker success and
gate success: focused tests passed on a corrective attempt, but `git diff --check`
still failed, so the candidate was correctly rejected. Passing tests do not override
scope or diff hygiene. Report such an outcome as successful enforcement by the gate,
not as a successful worker delivery, and never weaken independent checks to obtain a
green result.

## Distribution must preserve the trust boundary

A public skill cannot depend on a developer's absolute checkout path. Keep one
canonical Agent Skills bundle, resolve the runtime relative to a cached plugin, and
use a local install marker only for the explicit standalone installer. Test both
accepted layouts and reject relative or missing markers. Do not copy the core runtime
into each marketplace package or introduce a daemon merely to make installation look
uniform.

Plugin installation is not consent to transmit a repository. Before dispatch, name
the repository and allowed paths and obtain explicit approval for sending the prompt
and worker-read content through agy to Google/Gemini. Keep local logs private and make
privacy, support, and usage terms public before marketplace review.

Checked-in marketplace metadata proves package shape, not listing status. Submission,
review, publication, GitHub Pages enablement, and search-console ownership are external
state changes with their own approval and verification. Use accurate natural-language
landing copy, a canonical URL, and a sitemap that the owner explicitly submits through
Search Console; do not trade the project's evidence boundary for keyword stuffing or
unsupported product claims. Do not place `robots.txt` under a GitHub Pages project
subpath and call it crawler control: robots rules are host-root metadata owned by the
site owner, outside this repository's publication slice.
