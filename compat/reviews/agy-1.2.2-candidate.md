# AGY 1.2.2 candidate compatibility review

Reviewed: 2026-09-13

This record preserves the reviewed AGY `1.2.2` source/distribution observation and
the chronological live-workflow investigation before activation. At this stage the
active baseline remained 1.1.27; the subsequent
[activation record](agy-1.2.2-activation.md) owns the current decision. This record contains no account identifier, credential,
private path, raw capture, prompt, task content, conversation identifier, or provider
response.

## Bound candidate evidence

- Official release revision: `ba985e6b5de2ac8aa09860a154a102831eb7722b`.
- Executable SHA-256: `cabadc15a61944372bede1fdff186701c17467dd9d718e97dc79283055d3c101`.
- Distribution SHA-512:
  `8a3b5edea51e107a74413cea5eed1c5b02dede945ba21c7625b7c86f477c2c8ac423581de0ed84ff2f97c3bf6818c1fc24ca84cf9a76c2fb02a50e89d8a0d29a`.

The initial candidate manifest permitted version evidence only. One bounded account
inventory read selected fourteen reviewed slugs; that observation alone did not
activate an inventory, matrix, capture/classifier profile, or routing decision.

## Bounded live observations

- A one-time harmless-command canary observed a real `denied_actions` key. Its empty
  `SUCCESS` response lacked structured output, so the controller classified it as
  `invalid_envelope` with `failure_stage=missing_structured_output` and preserved no
  candidate. This confirms the controller's existing fail-closed response handling;
  it does not establish a general denied-action payload contract.
- A direct print canary returned exit `0` and terminal `SUCCESS` with a partial-output
  warning after eight seconds. It is a timeout/stream-boundary observation, not a
  successful worker result. Its relative filename caused an ambient account-home
  configuration search; that raw route will not be repeated. Remaining cases use
  wrapper-controlled absolute stage guidance.
- The approved session normal case completed with driver verification. It establishes
  only that exercised case and checked candidate.
- The same-conversation session repair completed after its bounded second start. The
  final candidate passed five driver checks. This establishes only that exercised
  session repair and its exact final candidate.
- The native normal case stopped at an authentication-required condition. Its public
  result is `status_unavailable` at `binding_failure` after the authentication wait;
  no provider stream or candidate was produced. Shutdown identity remains uncertain,
  so this record makes no native cleanup-success claim.

A subsequent offline macOS C probe isolated a local initialization failure: the
external executable's main bundle and SSL policy were null under the original
profile, while metadata/existence access to only its exact parent directory restored
both. The runtime candidate adds that permission only for the bound provider image.
Offline tests retain denied directory listing, sibling reads/stat, and exec-helper
access; self-verification receives no exception. A subsequent authorized native
normal attempt no longer emitted the SSL-policy error, but silent authentication
still failed before any stream or candidate. This is not native live qualification.

A separate offline experiment tested a minimal default-Keychain locator in private
HOME with a synthetic item explicitly accessible to the native security helper.
Metadata-only file access did not retrieve the item. Read-only access to that exact
file, limited to `/usr/bin/security`, enabled both default and explicit-path lookup;
the fake provider and a different executable remained unable to read it. This
supports the narrow helper-file mechanism for that fixture, not actual AGY token
presence, lock state, or item access controls. No real credentials or account
inventory were queried by this experiment.

After independent review and explicit authorization for actual Keychain access,
the next native attempt reached the selected model and received a response with
reported token usage. Authentication therefore progressed in that attempt. The
provider then denied a directory-listing read within the approved synthetic stage,
returned no structured result, and produced no candidate. The controller retained
`invalid_envelope` / `missing_structured_output`; this does not qualify native normal
work or authorize an automatic permission retry.

The reviewed runtime now prepares minimal AGY permission rules inside each native
job's private HOME. Rules cover only approved staged reads and exact write selectors
for the already-bounded attempts. Settings bytes remain identical across repair;
the host profile still permits only the current stage. Owner account and permission
settings are neither copied nor changed. Offline boundary tests and an independent
host preparation check accepted this mechanism before the next provider launch.

The ninth start completed the native normal case. Its sole file edit passed five
driver-owned checks and independent review. The tenth start completed the first
native repair turn, preserving the intentionally incomplete candidate: trimming
passed and lowercasing failed as expected. The eleventh start used the recorded
conversation identifier for the approved correction, but AGY reported `CLI crashed`
before producing any stream output. The controller stopped after approximately
sixty seconds of that attempt with `status_unavailable` / `binding_failure`.
The prior candidate remains on disk; result rebinding and process-group cleanup
were not established, so neither repaired success nor successful cleanup is claimed.
The crash message alone does not establish an authentication failure or root cause.

At that point, the approved eleven-start budget and single account inventory read
were exhausted. Native normal was accepted; native repair remained unqualified.
After renewed user authority, follow-up work was bounded to two additional native
repair starts, with the same model and selected synthetic file scope.

Targeted CLI diagnostics then identified a preceding persistence error: the initial
turn could not open its conversation database, and continuation panicked in the
trajectory database component. An offline upstream SQLite reproduction localized
`EPERM` to `lstat` on an ancestor of private HOME. A one-rule differential granted
only the exact provider image metadata access to that ancestor chain; database
creation, WAL/SHM use, commit, close/reopen, and sentinel recovery then passed.
No AGY start or credential access was used for this reproduction. This explains the
observed storage prerequisite. The twelfth and thirteenth starts then completed
the native repair in the same conversation: the first preserved the expected
trim-only candidate, and the second passed all five driver checks. Independent
review accepted the native normal and repaired candidates.
No unchanged retry, model substitution, or Boost run was performed.

## Decision and limits

The initial non-activating decision was superseded only after the accepted session
and native normal/repair results. The [activation record](agy-1.2.2-activation.md)
binds the resulting metadata promotion and its verification limits. Failed attempts
remain part of this investigation; their uncertain cleanup is not retroactively
reported as successful.

These bounded cases do not establish provider/backend identity, model quality,
exhaustive compatibility, authentication state for other runs, pricing, quota,
fallback, billing, effective routing, or live optional self-verification. No Boost
success or publication is established by this record.
