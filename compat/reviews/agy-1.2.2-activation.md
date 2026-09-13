# AGY 1.2.2 activation evidence

Reviewed: 2026-09-13

This record supports the accepted v0.19.0 implementation. Its stable candidate
passed all 44 offline CI stages and independent review. Publication and installation
remain separately verified delivery states.

The accepted version and single account inventory capture bind AGY **1.2.2**,
reviewed upstream revision `ba985e6b5de2ac8aa09860a154a102831eb7722b`, executable
SHA-256 `cabadc15a61944372bede1fdff186701c17467dd9d718e97dc79283055d3c101`, and
version binding `cc3bd8bb44b31891e2bed4c4367ed90ae2334e0df29e9fba2e94bce7c3105bef`.
The capture record is `73d65488b618b721d70d85076cc1ca67c8ead62e0606588502aeb69e5230d032`.
Its fourteen model slugs have normalized SHA-256
`d5e58ab55e91ebd4a2cd23841c76cbe12b47d607c62cd8c834fc8f6b9f078ad7`.
Mappings are unchanged from 1.1.27. Root and portable compatibility records bind
this version, source, inventory, matrix, and capture together. The separate
observational distribution snapshot was refreshed to the same accepted 1.2.2
URL/SHA-512 tuple after a fresh fixed-manifest read. This prevents a stale snapshot
from generating permanent drift warnings; it grants no activation or download
authority.

The official [1.2.2 changelog](https://github.com/google-antigravity/antigravity-cli/blob/1.2.2/CHANGELOG.md)
and bounded version/help inspection were reconciled with observable behavior. The
upstream repository does not expose the CLI implementation. Caller-selected model
and effort continue to resolve to one advertised compound slug, passed through
`--model`; the presence of `--effort` in help does not establish safe composition.

## Failure handling

A real refused-command canary exposed `denied_actions` but lacked structured output.
It remained `invalid_envelope`; no candidate was invented. For a valid envelope on
exact 1.1.27 or 1.2.2, offline fixtures verify that key presence produces
`permission_required`, preserves a valid candidate, and prevents automatic repair.
This policy does not assume a stable field payload schema.

A direct eight-second canary returned exit zero and terminal success with a partial
output warning. Its relative file request also caused an ambient account-home
configuration search, so this route was not repeated. Subsequent worker cases used
wrapper-controlled absolute stage paths. Offline controller scenarios verify that
the exact observed warning, bound to the job duration, yields `provider_timeout`
and preserves a valid candidate for independent review. Permission denial, invalid
reports, hard deadlines, cancellation, and binding failures keep their precedence.
The canary did not itself produce a valid worker candidate.

## Session and native qualification

One bounded campaign used only `gemini-3.8-flash-high`. Its initial ten-start cap was
explicitly raised to eleven; renewed user authority after the failed continuation
was bounded to two further repair starts. Thirteen actual starts were recorded.
There were no model substitutions, additional inventory reads, or Boost starts.

Session normal work and same-conversation repair passed driver-owned checks. The
repair deliberately began with a trim-only candidate; the second turn added
lowercasing and passed five checks. These cases qualify only the exercised workflow
and candidates; session mode does not confine host reads.

Native qualification required narrow, separately reviewed preparation fixes:
exact provider executable-parent metadata for local initialization, a private
default-Keychain locator with read access restricted to the security helper, and
minimal staged-path permissions in private job settings. Owner configuration was
not copied or changed. The normal native candidate then passed five driver checks
and independent review.

An earlier native continuation failed with `status_unavailable` at `binding_failure`.
The prior candidate remained on disk, but cleanup and result rebinding were not
established. Targeted diagnostics identified a failed conversation database open
before the continuation panic. An offline upstream SQLite differential reproduced
an ancestor `lstat` denial. Provider-image-only metadata access to private HOME's
ancestor chain restored database creation, WAL/SHM use, and close/reopen recovery;
it grants neither ancestor listing/data nor the same access to other images or
self-verification. No credential access or AGY invocation was used in that proof.

The two newly authorized native turns then completed in the same recorded
conversation in approximately 32 seconds total, within the original job budget.
The expected trim-only candidate was preserved, and the repaired candidate passed
five driver checks. Independent review accepted both native normal and repair
results. Historical failed attempts and their uncertain cleanup remain documented
in the [investigation record](agy-1.2.2-candidate.md); their outcomes are not rewritten.

## Activation and limits

Activation promotes 1.2.2 to current, retains 1.1.27 and 1.1.26 as previous, 1.1.22
as legacy, and 1.1.24, 1.1.16, and 1.1.12 as historical. Stable offline CI,
package parity, instruction audit, and independent repository acceptance passed
for the implementation candidate. The skill simplification preserves automatic discovery,
same-scope repair, candidate preservation, and driver-owned assurance labels; it
makes no measured speed or quality claim.

These bounded cases do not establish exhaustive compatibility, provider backend
identity, model quality, authentication or quota for other runs, billing, fallback,
effective routing, or live optional self-verification. Native tests establish the
exercised filesystem boundaries, not complete same-user tamper resistance. No new
Boost qualification is claimed. The closed-binary provider backend cannot be
independently attested by this evidence.
