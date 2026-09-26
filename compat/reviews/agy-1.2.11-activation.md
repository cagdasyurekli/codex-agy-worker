# AGY 1.2.11 Compatibility Activation

## Evidence

- **Reviewed Date**: 2026-09-26
- **Reviewed Source Revision**: `6dadd6227a49905f475d22b7f0afe59493229595`
- **Source SHA-256**: `42e76bedafb5896bc6a6eefb61902162f6ba08ddf59efd3357767102e3a59a0c`
- **Version Binding SHA-256**: `d130200bc0bac4ed65abca829d8e29cde91f82085c734e4b3aaead5ca9605d4c`
- **Capture Record SHA-256**: `2cb0f55205980c44d746ff4fda570e1383a3b950f9f4e8458b4d02b7dbcaaef6`
- **Capture Stdout SHA-256**: `d02970e6b6b4e0910461999afca8fb99d757e9094ab2874b557dad18fc75464a`
- **Capture Response SHA-256**: `b1cc011310435afa07b1e132a5b7f3e22297aa21427177461c858bcbd6a58794`
- **Inventory Normalized SHA-256**: `d5e58ab55e91ebd4a2cd23841c76cbe12b47d607c62cd8c834fc8f6b9f078ad7`

## Limitations

- Native and Boost/API-key compatibility are unqualified for 1.2.11.
- Both CLI argv requested accept-edits, but init reported request-review: successful edits are proven, effective accept-edits semantics are not.
- Existing bounded empty/permission/timeout observations must not be overstated.
- Offline checks establish only the exercised contracts; they do not qualify additional live provider modes.

## Bounded live session activation

- Two bounded synthetic cycles completed in the same AGY conversation. The planned synthetic refinement proceeded from the original synthetic value, to the candidate synthetic value, to the verified synthetic value. The driver-owned final receipt is retained privately (`gate-passed`, `gate_exit=0`); it is unsigned and not tamper-evident.
- No denied commands were observed in the bounded session evidence. The launch requested `accept-edits`, while initialization reported `request-review`; successful synthetic edits do not qualify effective `accept-edits` semantics.
- This evidence covers session mode only. Native isolation and Boost/API-key compatibility remain unqualified. The final executable candidate passed the full driver-owned offline CI.

## Interface and offline evidence

The [pinned official changelog](https://github.com/google-antigravity/antigravity-cli/blob/6dadd6227a49905f475d22b7f0afe59493229595/CHANGELOG.md) documents the 1.2.10 headless partial-response error correction (exit 3 with `AGY_ERROR`) and the 1.2.11 effort changes. Installed 1.2.11 help advertises `--effort` values `low|medium|high|max`. The version-bound parser and error classifier have offline regression coverage; no live exit-3 or provider timeout-warning sample is claimed.

A separate implementation attempt emitted a valid structured report together with a command denial. Valid, reconciled 1.2.11 reports with `denied_actions` now stop as `permission_required`; they grant no automatic retry. That attempt also created an undeclared file, so reconciliation correctly rejected it with the higher-priority binding failure. The rejected edits were not adopted.

## Local acceptance

On 2026-09-26, all 44 canonical offline CI stages passed across the four existing shards, with each stage run exactly once in the successful final run and every shard exiting zero. The driver verified unchanged source bytes and modes across that run. Focused checks and independent GPT-6 Sol review cover the final candidate; the later acceptance-note and repository-map corrections are documentation-only and receive focused documentation/packaging checks and incremental review. This is local candidate acceptance, not publication or a hosted GitHub check.
