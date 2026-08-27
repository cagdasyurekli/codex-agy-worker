# Codex CLI 0.150.1 compatibility reconciliation

Reviewed: 2026-08-27

This record binds the Codex CLI `0.150.1` observational compatibility baseline to
bounded official-release, source, changelog, and installed-interface evidence. It is
not a release endorsement, an update instruction, or a change to agy
selection/dispatch policy.

## Official and local evidence

- Stable tag: `rust-v0.150.1`.
- Annotated tag object: `0eb410ad0dd161ea323b05452f978de01cd63430`.
- Stable-tag release commit: `90854393966b21e9ebfd21b122334eb09a20c93d`.
- The installed CLI reported `codex-cli 0.150.1`.
- The locally inspected CLI help retained the project-used `codex exec`,
  `--sandbox workspace-write`, and `--add-dir` surfaces.
- The bounded stdout-only `codex --help` digest was
  `e8ecd554e6e3e870a55e540f1a21598c085cfee237f9c735ff9b5ba4ac4cf08a`.
- The bounded stdout-only `codex exec --help` digest was
  `e504bac5a6364566fbe408132dec7993639def9258ece34e8352f51f8d43687c`.

The official OpenAI changelog dated 2026-08-27 describes one narrow patch: remote
compaction counts retained images toward its token budget by default and trims older
images as needed. The full comparison is `rust-v0.150.0...rust-v0.150.1`. This patch
does not create an agy routing, model-selection, provider, or worker-backend surface.

The stable-tag commit is an exact source reference, not a binary signature,
installation provenance, account claim, or permission to update Codex. Help text and
its digests are interface evidence only; they are not proof of provider, sandbox, or
project behavior. The local sandbox emitted a PATH-alias warning on stderr; that
environment-specific warning is excluded from the stdout-only help digests above.

## Decision and limits

Codex `0.150.1` is accepted as the current observational compatibility baseline.
`compat/codex-verified-version.txt` and `compat/codex-upstream-head.txt` bind the
stable tag and exact release commit above, so a later stable tag or a changed peeled
release commit is reported as drift-review. This activation is limited to
compatibility metadata; it does not infer future-version behavior.

Codex compatibility is observation-only. This record does not gate agy dispatch,
resolve a model or effort, alter user-selected options, authenticate, send a request,
authorize a Codex update, or add Codex as a second worker backend.
