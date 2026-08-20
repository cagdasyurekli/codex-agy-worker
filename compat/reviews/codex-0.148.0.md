# Codex CLI 0.148.0 compatibility reconciliation

Reviewed: 2026-08-20

This record binds the Codex CLI `0.148.0` observational compatibility baseline to
bounded official-release and installed-interface evidence. It is not a release
endorsement, an update instruction, or a change to agy selection/dispatch policy.

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

Codex `0.148.0` is accepted as the current observational compatibility baseline.
`compat/codex-verified-version.txt` and `compat/codex-upstream-head.txt` bind the
stable tag and exact release commit above, so a later stable tag or a changed exact
tag reference is reported as drift-review. This activation is limited to compatibility
metadata; it does not infer future-version behavior.

Codex compatibility is observation-only. This record does not gate agy dispatch,
resolve a model or effort, alter user-selected options, authenticate, send a request,
or authorize a Codex update.
