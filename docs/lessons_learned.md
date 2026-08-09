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
release origins, agy/Codex upstreams, release channels, and review cadence must not be
environment-overridable. Report established drift separately from unavailable or
malformed evidence: the latter is inconclusive and must outrank a known drift result
rather than becoming green. Always report both tools before aggregating.

A literal GitHub URL passed to `git ls-remote` is not a fixed-source guarantee: Git
still honors repository and global `url.*.insteadOf`, proxy, credential, and transport
configuration. Read-only compatibility evidence therefore uses an exact
`api.github.com` REST repository/path allowlist, a proxyless redirect-rejecting strict
JSON client, and no Git network command. Keep the explicit apply-time fetch limitation
visible; observation hardening does not silently harden mutation.

Bounding a parent command is insufficient when stdout/stderr pipes or descendants can
outlive it. Capture both streams incrementally, cap them independently, impose a hard
deadline, create a fresh process group, and kill/reap that group on timeout, overflow,
or HUP/INT/TERM. Return only parsed canonical fields; never surface raw child output
as compatibility evidence or diagnostics.

Security-sensitive evidence runners cannot be validated by a happy-path subprocess
test. Exercise the exact publication and lifecycle primitives against fixed weakened
copies: post-link stat and parent-fsync failures, signals before process-group
registration, a second signal during TERM grace or inode rollback, and a signal
between the final marker and disarm. Use one supervisor for every synthetic
controller, keep Popen through validated PGID registration signal-masked, and never
signal an unvalidated group. A failed mutation must be observed before harness-owned
cleanup; cleanup must still prove no orphan, late side effect, final, or temporary
artifact remains. These tests are proof infrastructure, not authority to run a real
tool or advance compatibility metadata.

Reconcile each tool's official CLI documentation, stable release, exact source
revision, and installed semantic command inventory before advancing its separate
version/revision/review-date metadata. An unknown command that exits zero or prints
usage is not semantic evidence. The weekly watcher observes the same fixed sources;
it never updates a baseline, installs a tool, dispatches a model, or takes a GitHub
write action. A bounded real job remains separately approved when behavior changed.

Model-list display text can look like an additional slug. A generic slug regex turned
the `gpt-oss` label beside `gpt-oss-120b-medium` into a false twelfth entry. Parse a
bounded inventory one line at a time, recognize only whole exact reviewed slugs,
require every expected slug exactly once, and permit a display alias only beside its
bound canonical slug. Reserve the reviewed provider namespaces so even a one-hyphen
unknown such as `gemini-unknown` fails closed without treating every ordinary display
label as a model. Keep prefix/longest-match and namespace-removal mutations in paired
offline controls; inventory parsing alone is not a version binding.

An official installer channel can move before a public release/source repository.
Observe that difference through one fixed, bounded manifest canary, but do not turn a
distribution version or checksum into source or behavior evidence. Disable proxies,
reject redirects, cap and validate the JSON response, structurally bind its archive
URL to the expected host/path/version, and make no archive request. Keep the recorded
tuple explicitly observational: version or same-version build/hash drift asks for
human review and cannot advance a baseline or activate model routing.

## Diagnostics observe; they do not repair

A readiness command is safest when its success claim is narrower than the job it
precedes. Check only bounded offline prerequisites and semantic command output; do
not turn an exit code, usage page, or executable name into proof of compatibility.
Keep paths and raw command output out of reports, because even a diagnostic can leak
repository names, credentials, or personal configuration.

Never make a doctor scan home configuration, probe invented authentication commands,
call a provider, access the network, run an updater, or repair a failure. Report
version drift and due review as requiring human review; report missing or malformed
prerequisites as not ready. Green proves only the tested offline conditions, not
authentication, provider availability, sandbox permission, task quality, or a future
dispatch. Portable diagnostics must carry byte-synchronized reviewed metadata and
fail closed when their bundle or records are incomplete. Treat temp placement and
signal propagation as part of the trust boundary: ignore caller temp paths, keep
captures private and bounded, and terminate the exact active process group. A
non-symlink file is not contained when one of its parent directories is a symlink;
canonicalize the root and require package-owned parents to be real directories.

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

## Private evidence must be private when created

Prompts, model streams, stderr, and extracted envelopes can contain repository data.
Set an owner-only process mask before creating dispatcher-owned log directories or
files; fixing permissions after a write leaves an avoidable disclosure window. A
custom log root belongs to the caller, so contain each new job under its own private
directory instead of rewriting that root. Before the first job write, require the
final existing root itself to be a current-user-owned real directory without
group/other write bits, then use its physical path. Create a missing root under the
private mask. This is a bounded final-component invariant, not proof that every
ancestor is safe or that all filesystem TOCTOU races are eliminated. Create the job
directory atomically and fail closed when its path already names a directory, file,
or symlink; reusing an attacker-prepared path defeats creation-time permissions.

Do not let log hardening alter candidate-file behavior. Restore the caller's mask
only inside the untrusted worker child while keeping the shell-owned redirections
private. An oversized staged prompt may need its proven read-only access modes during
agy execution, but it must stay below a non-traversable job parent and return to
owner-only modes immediately afterward. Restore those modes from the normal child
return path and an EXIT trap; HUP, INT, and TERM handlers must restore first and then
re-raise the same signal so cleanup does not turn termination into success.

Treat a direct-selection version probe as a process-group boundary too. Read its
stdout incrementally under byte and wall-clock limits, close the whole group on
oversize, timeout, HUP, INT, or TERM, and do so before reading the task or publishing
selection provenance. Signal cleanup must preserve the conventional `128 + signal`
status instead of relabelling interruption as unavailable evidence.

## Model routing is explicit

The caller selects the tier or direct model/effort input. Built-in retries reuse the same model; gate failures do
not silently increase cost or reasoning effort. Keep recommendation policy outside
both dispatch and gate acceptance: its output must be visible, state the current tier,
show controlled driver-owned evidence and relative cost impact, and say explicitly
that it was not applied. A validated model/effort matrix remains metadata, never
routing or gate authority. Direct selectors resolve only from exact reviewed choices
after matrix SHA/schema/version/source and installed-version preflight. Preserve
presence as data: unset differs from explicit empty, CLI and matching environment
sources conflict even when equal, and repeated components never mean “last wins.”
Resolve once, publish private driver provenance, and freeze the exact slug and matrix
SHA across retries. Disabled, stale, unknown, duplicate, unsupported, and fixed/no-
level-plus-effort inputs fail closed. Do not add a separate thinking-level abstraction
or assume that agy's separate model and effort arguments compose safely; send one
resolved `--model` and no downstream `--effort`.

An exact matrix cannot validate itself. Keep its reviewed pair-to-slug mappings and
fixed-slug classifications mirrored in explicit validator allowlists, and require
exact equality between the two representations. That duplication is intentional:
changing only the data or only the code fails closed and forces the next compatibility
review to update both. Never reconstruct a supposedly reviewed slug with string
concatenation; a plausible model name is not evidence. Keep the sanitized review
record as the human evidence owner, and keep raw prompts, streams, envelopes, and
private artifact paths out of it.

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

## A starter proof is not an acceptance claim

A useful offline proof must exercise the maintained gate, not a reimplementation of
its decision logic. Give the passing and rejecting cases independent repositories,
require their exact exit contracts, and include a negative control showing that a
copied permissive gate cannot make the overall proof pass. Keep canonical fixtures
strict so silent edits cannot turn a teaching example into a different claim.

Buffer success output until every case and cleanup step succeeds. Describe the
result as evidence for the fixed synthetic cases only: a gate pass is still not a
human diff review, accepted candidate, correctness result, security certification,
benchmark, or production validation.

## Receipts bind observations; they do not create authority

A useful receipt records the gate's own bounded structured handoff rather than
parsing prose or reimplementing acceptance in an outer wrapper. Snapshot the exact
envelope bytes the gate validates, retain the resolved immutable base and the gate's
internal initial/final candidate-state digests, and cross-check the gate process exit
against one unique handoff. Missing, duplicate, malformed, mismatched, interrupted,
or unknown evidence is an internal protocol failure—not a result to reconstruct.

Keep private data out by hashing ordered policy and verifier commands and assigning
deterministic labels. An optional selection or pre-dispatch advisory must pass its
own canonical policy and agree when both are supplied; it still cannot participate
in acceptance or change the selected model. Never bind a later post-gate advisory by
rewriting a one-pass receipt.

Durability and non-overwrite are separate properties. Write and validate a same-dir
mode-`0600` temporary, `fsync` it, publish with an atomic hard link that refuses an
existing target, `fsync` the parent, remove the temporary, and `fsync` the parent
again. On validation, link, or durability failure, remove every publisher-owned
partial and never delete or overwrite a raced caller/attacker target. Revalidate the
private parent immediately before linking.

A pre-opened evidence descriptor is authority, not ordinary inherited process state.
Validate it in the gate parent, then close it before every verifier child and
descendant executes; otherwise a verifier can forge or corrupt the supposedly
gate-owned handoff. Signal ownership must span the full receipt transaction, not just
the gate wait: track private files and the pinned published inode before the atomic
link, terminate and reap the active process group, and remove only wrapper-owned
artifacts on HUP, INT, or TERM.

Closing a sensitive descriptor in a newly started helper is already too late:
Python `sitecustomize` or a shell `BASH_ENV` hook can execute before that helper's
first statement. Bind evidence mode to the receipt wrapper, sanitize executable
startup controls before launching the gate, run gate-owned Python with isolated/no-site
startup, and close the numeric validated FD with a Bash builtin in the already-running
gate process before starting the verifier shell. Preserve ordinary verifier environment
values, but do not forward the stripped startup controls or internal capability.

Schema validation detects malformed and internally inconsistent content, not an
authorized rewrite. An unsigned JSON document can be changed and rehashed by anyone
who can replace it. State that limitation in the document itself; require a separately
trusted envelope or candidate digest when later tampering matters. Receipt existence
does not replace `qa-gate.sh`, human diff review, signing, authenticity, correctness,
or security evidence.

## Distribution must preserve the trust boundary

A public skill cannot depend on a developer's absolute checkout path or assume that
an installer copied the surrounding repository. Keep the core runtime once, inside
the canonical Agent Skills bundle, and make repository-root commands compatibility
wrappers. A complete plugin may resolve those wrappers and an explicit standalone
install may use a local checkout marker, but a skill-folder-only copy must fall back
to its bundled runtime without fetching code. Test every accepted layout, reject
incomplete bundles and invalid markers, and preserve the root CLI's observable
defaults. Do not duplicate the runtime across packages or introduce a daemon merely
to make installation look uniform.

Skill installation is not consent to transmit a repository. Before dispatch, name
the repository and allowed paths and obtain explicit approval for sending the prompt
and worker-read content through agy to Google/Gemini. Keep local logs private and make
privacy, support, and usage terms public alongside the GitHub distribution.

Keep distribution surfaces no broader than the maintained product. A Codex package
manifest can validate local package shape without creating a listing; do not retain
Claude or marketplace catalogs after choosing a GitHub-first, Codex-only product.
GitHub Pages enablement and search-console ownership are external state changes with
their own approval and verification. Use accurate natural-language landing copy, a
canonical URL, and a sitemap that the owner explicitly submits through Search Console;
do not trade the project's evidence boundary for keyword stuffing or unsupported
product claims. Do not place `robots.txt` under a GitHub Pages project subpath and call
it crawler control: robots rules are host-root metadata owned by the site owner,
outside this repository's publication slice.

Treat Python syntax compilation as a package-boundary write. `-B` does not suppress
bytecode emitted by an explicit `py_compile` invocation, so CI and contributor checks
must direct `PYTHONPYCACHEPREFIX` to a private external temporary directory. A cache
inside the public skill is a distribution leak, not harmless ignored state; keep the
positive external-cache path and a plain-compile negative control paired offline.

## Public discovery claims need the same evidence discipline

The landing page and README are part of the trust boundary because users choose
whether to install before reading the implementation. Lead with the bounded mechanism:
Codex delegates to agy, then the driver independently checks Git scope and runs its own
verification commands. Do not turn those checks into claims that the project proves
general correctness, security, or official endorsement.

Keep GitHub repository files separate from GitHub repository settings. A checked-in
Pages source, sitemap, policy, or preview recommendation does not prove that Pages,
About metadata, topics, homepage, private reporting, search indexing, or a social
preview is enabled. Treat each external setting as a deliberate owner action and
verify live state after any separately approved change.

Treat brand assets as an interface with size-specific responsibilities. Use the
light/dark master SVGs for large surfaces, the pixel-hinted micro variants for
favicon-sized rendering, and an opaque, exact-size raster for social previews. Keep
all variants on the same geometry and palette, reject external SVG references and
vendor marks mechanically, allow only the SVG elements and attributes the masters
need, and compare light/dark path geometry in order. Verify every PNG chunk and CRC,
then boundedly decode the scanlines so valid framing cannot hide transparency or a
broken image stream. Do not imply that a checked-in preview is active in GitHub
repository settings.

Do not let an offline mutation harness prove only a reimplementation of a sensitive
runner. Keep the production one-call path in one canonical stdlib module, bind the
exact source byte count and SHA before importing it, and make synthetic self-test use
that same function with fixed test-only callables. A green generic lifecycle harness
is useful evidence for its primitives, but it cannot substitute for exact production
source provenance.

Do not use the literal value of `sys.executable` as Apple system-interpreter
provenance. `/usr/bin/python3` can report a versioned Xcode or Command Line Tools
path that changes with the macOS image. Keep `-I -S -B` mandatory, resolve the actual
executable fail-closed, restrict it to reviewed Apple system families, and verify the
regular root-owned executable plus every non-writable ancestor instead of pinning one
image-specific string.

When hosted-runner trust facts drift, emit only bounded, canonical categories from the
same evaluator that rejected them. Report every ordered violation, redact unreviewed
path components, cap the record, and treat it as diagnostic evidence rather than a
reason to relax the trust boundary.
