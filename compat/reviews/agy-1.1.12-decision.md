# agy 1.1.12 baseline reconciliation decision

Reviewed: 2026-08-12

Decision: do not advance the active agy baseline or model/effort matrix. Keep
`1.1.10` as the exact reviewed matrix baseline. This record is a sanitized decision
receipt, not accepted inventory evidence, provider evidence, or dispatch authority.

## Evidence available

- The official `1.1.12` release and the separately observed official `main` revision
  resolved to `f7519c9084190ed421e89dd81c63970b5177c9ef` at review time.
- The retained snapshot/version bridge binds semantic version `1.1.12` to reviewed
  source bytes with SHA-256
  `c8fd3c0016e101689f923f82da1c068b0e6dce3abcb0089e282742693ad4d344`.
- The fixed distribution endpoint exposed version `1.1.12` with archive build
  identifier `5877618327814144`. Only the abbreviated SHA-512 observation
  `6dec2eab...6d9d` is available to this decision record, so it is deliberately not
  represented as an exact tuple. Even a complete tuple would be observational canary
  evidence only: it is not a signature, source proof, executable provenance,
  archive-download permission, or matrix activation input.
- The repository's checked-in distribution-manifest snapshot remains the reviewed
  `1.1.10` tuple and is intentionally not advanced by this decision.

The retained source SHA-256 identifies exact reviewed bytes; it is not an upstream
Git revision and must not be substituted for the official commit. Likewise, equality
between the observed release and `main` revisions is only a point-in-time
observation.

## Authorized capture result

One exact, separately authorized `agy models` capture was attempted against the
explicit account HOME and fixed `1.1.12` profile, with no retry. The capture runner
started its child and then exited `1` because capture-owned TMPDIR was nonempty. A
private TMP residual remained. No capture record, summary, evidence artifact, or
completion marker was published.

Because publication did not complete, the child's return code, stdout, and stderr are
unknown. They must not be reconstructed from the residual or inferred from the
runner exit. No inventory bytes were interpreted, normalized, or accepted. This
attempt therefore supplies zero authority for the model inventory, pair-to-slug
mapping, provider behavior, or active matrix.

## Baseline decision

The official `1.1.12` and version/source observations are sufficient to report
version drift but insufficient to replace the human-reconciled `1.1.10` inventory
and matrix. The checked-in agy version, reviewed revision, review date, distribution
manifest, matrix, and matrix digest therefore remain unchanged.

This does not prohibit ordinary operation on a newer agy version. A caller-owned
literal model slug may pass through without matrix resolution, and an omitted model
remains agy-owned default selection. Only the reviewed model/effort pair resolver
requires an exact accepted version/source/matrix binding. Drift is visible and
reviewable; it is not a blanket agy or Codex version lock.

## P2-B and P2-C decision

P2-B remains deferred until a separately approved, executable/version-bound,
one-attempt public synthetic run yields owner-private raw NDJSON plus a sanitized
reviewed record that binds terminal-event order/cardinality, exact field names and
types, null/missing behavior, nested usage keys, duplicate/failure semantics, and
invocation/source/version hashes. Synthetic fixtures and the failed capture do not
meet that threshold.

P2-C remains deferred until opt-in sanitized measurement records from explicit
owner-private lifecycle roots demonstrate recurring retention or manual-cleanup
burden, and a separate managed-root/list/show/prune contract is reviewed. Age alone,
one failed capture residual, or an unvalidated local path cannot authorize deletion.

Neither feature is enabled merely by reaching a 30-, 60-, or 90-day reporting date.
The corresponding evidence class and explicit follow-on approval remain mandatory.
