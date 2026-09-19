# AGY 1.2.6 activation evidence

Reviewed: 2026-09-19

This record supports the provisional local active-compatibility promotion for
AGY **1.2.6**. Permanent full offline CI and independent acceptance requirements
govern qualification; unrun CI is not asserted to have passed. Release publication
and installation remain separately verified delivery states.

The accepted version and single account inventory capture bind AGY **1.2.6**,
reviewed upstream revision `d39491f6f98a62aaf29af76964aa4fe75bc044d4`, executable
SHA-256 `7e1a1036b68cd8e2ad66097ddc8f43dc2081079d51d340863647dc8de2a1bc84`, and
version binding `9c6bd66e3d4fcd8c1c0ac2151a6cf72916dba68148822c53be852fea413992c3`.
The capture record is `c96a7875fee945c97edb61a44c83431b94ca1e97a841af718aa585b04d48bf4f`.
Its fourteen model slugs have normalized SHA-256
`d5e58ab55e91ebd4a2cd23841c76cbe12b47d607c62cd8c834fc8f6b9f078ad7`.
Mappings are unchanged from 1.2.2. Root and portable compatibility records bind
this version, source, inventory, matrix, and capture together. The separate
observational distribution snapshot was refreshed to the accepted 1.2.6
URL/SHA-512 tuple. This prevents a stale snapshot from generating permanent drift
warnings; it grants no activation or download authority.

The official 1.2.6 changelog and bounded version/help inspection were reconciled with
observable behavior. Caller-selected model and effort continue to resolve to one
advertised compound slug, passed through `--model`.

## Failure handling

A real refused-command canary exposed `denied_actions` without structured output.
The strict no-envelope refusal recognition for 1.2.6 maps this pattern to
`permission_required` (exit 6) without candidate/result, sets `resume_available=false`,
and stored `next_action=none`, while `continue` remains false. Permission-refusal public
actions are budget and authority dependent: public restart may be offered only when
remaining budget and bound launch authority permit it; otherwise `next_action=none`
and `available_actions` is empty (preserving no candidate, result, resume, or continue).
The real native canary used `max_cycles=1` and correctly had no action. For valid envelopes on
exact 1.1.27 and 1.2.2, `denied_actions` presence produces `permission_required`,
preserves a valid candidate, and prevents automatic repair. For valid envelopes on
1.2.6, the candidate succeeds normally without widening the presence-only helper.

The timeout formatter (`[agy] print timeout after %s with turn in progress; returning partial output`)
is static binary plus offline evidence; the live timeout canary only proved local hard
deadline termination. Bound to the positive integer job duration, the matcher yields
`provider_timeout` even when the process exits zero with empty stdout for both 1.2.2
and 1.2.6.

Unknown or malformed `AGY_ERROR` lines stay unclassified (`agy_failed_unclassified`).
Invalid envelopes remain distinct (failing closed to `invalid_envelope` at exit 4).
`binding_failure` applies only to actual binding failures, keeping strict fail-closed
precedence.

`AGY_ERROR` handling is grounded in static serializer struct metadata from the binary
plus official exit-3 contract evidence; there is no live exit-3 sample or inferred
retry authority or timing.

## Session and native qualification

Recorded live calls occurred during capture and canary observations; this
evidence grants no authority for additional live calls. The earlier four session and
native normal/repair canaries correctly used the stable v0.19.0 controller against
AGY 1.2.6:
- `native-normal`: passed 2 checks, 0 failed, complete coverage, candidate SHA-256 `c327d3af7b8a051eead71a6a49f4c82b55f9c472c9a145ee2aeb67db29be6ac1`.
- `native-repair-recovery`: passed 5 checks, 0 failed, complete coverage, candidate SHA-256 `63e7fb6628d668a1dcad01d91ba6392c712aa38000d04d8f537cc4acea1d8a61`.
- `session-normal`: passed 2 checks, 0 failed, complete coverage, candidate SHA-256 `7475a43e25b019ddadf8b8eb6b7c0215b694096b27936ebf5eb2d148e865d613`.
- `session-repair`: passed 4 checks, 0 failed, complete coverage, candidate SHA-256 `9ed01ebdc539df8703b1b11b1c3e9f3d6490e2d255ac3a799e96291f03245a1c`.

Initial overlapping native-repair ended status_unavailable at binding_failure; serialized recovery passed.
Each test exhibited 0 advisory checks, 0 missing checks, 0 unresolved gaps, and 0 verified findings.

Separately, the driver completed additional pinned AGY 1.2.6 live qualification using the updated candidate runtime, all on `gemini-3.8-flash-high`:
- One session task on a synthetic `normalize_label` fixture returned an accurate no-change result; existing candidate-bound self-verification ran one required check successfully and independent driver verification passed four input cases; finalized verified.
- A separate native no-write permission-refusal canary returned `permission_required` (exit 6), with no candidate, result, resume, continue, or retry.

## Activation and limits

Activation promotes 1.2.6 to current, demotes 1.2.2 to previous, retains 1.1.27 and
1.1.26 as previous, 1.1.22 as legacy, and 1.1.24, 1.1.16, and 1.1.12 as historical.

These bounded cases do not establish exhaustive compatibility, provider backend
identity, model quality, authentication or quota for other accounts, models, or tasks,
billing, fallback, or effective routing. One bounded live synthetic result is now
established, but not general self-verification reliability or other accounts/models/tasks.
Permanent full offline CI and final independent acceptance remain requirements; their
pending results are not claimed to have passed. No release publication or installation
is claimed, no live exit-3 sample or inferred retry authority is established, and there
is no authority for additional calls.
