# AGY 1.2.7 activation evidence

Reviewed: 2026-09-19

This record supports the active-compatibility promotion for AGY **1.2.7**.
Candidate driver CI passed all 44 canonical offline stages exactly once across the
four canonical shards, including 114 remediation cases (manifest aggregate
`486d5999a6d4cd6cf60c50bbd8b9b988e9b4dc20326a5dcc2cb486a8ea1c2661`), with candidate
bytes and executable modes unchanged. Runtime and metadata review found no remaining
executable findings, and the mirrored `clientInfo` 0.20.0 correction passed 17 owning
usage tests. Final independent acceptance and release verification remain permanent release
requirements. Release publication, installation, and SkillStore publication remain
separately verified delivery states.

The accepted version and single account inventory capture bind AGY **1.2.7**,
reviewed upstream revision `7bb195acaec9e7788df5210d0dc3e15f3cefc6b3` (official release
at https://github.com/google-antigravity/antigravity-cli/releases/tag/1.2.7), executable
SHA-256 `8c01ef82307dc01455418eb2e6e82f2989a3fa8b815c4b192efaaa89a55bef8d`, and
version binding `b1942cfa7d7d12edcca7d5458f46bdaef8107fe762cfa3b551b20dc624a296d3`.
The capture record is `1975276f4bb8b030a21d83a6664eddb596c0ac68440da4556bde32de3d2d6fad`.
Its fourteen model slugs have normalized SHA-256
`d5e58ab55e91ebd4a2cd23841c76cbe12b47d607c62cd8c834fc8f6b9f078ad7`.
Mappings are unchanged from 1.2.6. Root and portable compatibility records bind
this version, source, inventory, matrix, and capture together. The separate
observational distribution snapshot was refreshed to the accepted 1.2.7
URL/SHA-512 tuple. This prevents a stale snapshot from generating permanent drift
warnings; it grants no activation or download authority.

The official 1.2.7 changelog and bounded version/help inspection were reconciled with
observable behavior. Caller-selected model and effort continue to resolve to one
advertised compound slug, passed through `--model`.

## Failure handling and runtime boundary

Static inspection of the binary packet proves `AGY_ERROR` fields and timeout literals only.
Live qualification on fixed `gemini-3.8-flash-high` established actual runtime and refusal
behavior:
- Session normal, native normal, session same-conversation repair, and native same-conversation
  repair each passed four independent driver cases (whitespace/case, empty, Unicode case,
  internal spaces). The initial repair candidate strip-only failed three expected checks, and
  AGY repaired its own candidate to strip+lower in the same conversation, model, isolation,
  and scope.
- These are point-in-time candidate-bound V2/finalization records and retained verification
  copies. Later synthetic fixture edits in their shared worktree make current candidate readback
  fail for three earlier jobs; their current source worktree is not implied to remain bound.
- Native permission refusal: stopped with `permission_required` (exit 6) with no candidate or
  continuation/resume authority. AGY process exit 0 and terminal `SUCCESS` with `denied_actions`
  established the actual 1.2.7 refusal shape.
- Session timeout: stopped with controller `hard_deadline_exceeded` (exit 16) at 8 seconds,
  with no candidate or resume authority; the AGY terminal `ERROR` exit 1 had no `AGY_ERROR`
  marker and no provider print-timeout warning, proving the controller hard deadline only.
- Live runs executed 10 of 12 starts including failures and repairs, consuming 240.977 of 1800
  seconds of provider wall time, with all provider turns complete and no extra model inventory calls.
- Bounded limits: no live 1.2.7 exit-3 sample, provider print-timeout warning sample, Boost,
  or API-key qualification was observed. Historical 1.2.6 evidence is not folded into these claims.
- Runtime and metadata were independently reviewed before documentation with no remaining executable
  findings; the mirrored `clientInfo` 0.20.0 correction passed 17 owning usage tests.

Live execution canaries, session qualification, and independent driver
verification established earlier under 1.2.6 (including resolution of `status_unavailable`
and `binding_failure` boundary cases) remain historical reference points.

Unknown or malformed `AGY_ERROR` lines stay unclassified (`agy_failed_unclassified`).
Invalid envelopes remain distinct (failing closed to `invalid_envelope` at exit 4).
`binding_failure` applies only to actual binding failures, keeping strict fail-closed
precedence.

## Activation and limits

Activation promotes 1.2.7 to current, demotes 1.2.6 to previous, retains 1.2.2, 1.1.27, and
1.1.26 as previous, 1.1.22 as legacy, and 1.1.24, 1.1.16, and 1.1.12 as historical.

These bounded cases do not establish exhaustive compatibility, provider backend
identity, model quality, authentication or quota for other accounts, models, or tasks,
billing, fallback, or effective routing. Candidate driver CI passed all 44 canonical offline
stages exactly once across the four canonical shards, including 114 remediation cases
(manifest aggregate `486d5999a6d4cd6cf60c50bbd8b9b988e9b4dc20326a5dcc2cb486a8ea1c2661`),
with candidate bytes and executable modes unchanged before and after. Owning
docs/package checks, independent review, and final independent acceptance remain permanent
release requirements; unverified release gates are not asserted to have passed. No GitHub
release publication, installation, or SkillStore publication is claimed, no live exit-3
sample, provider print-timeout warning, or inferred retry authority is established, and there
is no authority for additional calls.
