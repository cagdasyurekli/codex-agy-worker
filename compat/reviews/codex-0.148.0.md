# Codex CLI 0.148.0 interface observation (not baseline advancement)

Reviewed: 2026-08-20

This record preserves the bounded offline release and installed-interface observation
for Codex CLI `0.148.0`. It is not a release endorsement, an update instruction, or a
change to agy selection/dispatch policy.

## Official and local evidence

- Stable tag: `rust-v0.148.0`.
- Stable-tag release commit: `3ba0f711642a888aec92a611a3f3b2211157ff89`.
- The installed CLI reported `codex-cli 0.148.0`.
- The locally inspected CLI help retained the project-used `codex exec`,
  `--sandbox workspace-write`, and `--add-dir` surfaces.

The stable-tag commit is an exact source reference, not a binary signature,
installation provenance, account claim, or permission to update Codex. Help text is
interface evidence only; it is not proof of provider, sandbox, or project behavior.

## Decision and limits

`compat/codex-verified-version.txt` remains the accepted `0.147.0` observational
baseline until the ordinary human reconciliation is accepted. The update checker must
therefore continue to report `0.148.0` as drift-review rather than silently advancing
metadata.

Codex compatibility is observation-only. This record does not gate agy dispatch,
resolve a model or effort, alter user-selected options, authenticate, send a request,
or authorize a Codex update.
