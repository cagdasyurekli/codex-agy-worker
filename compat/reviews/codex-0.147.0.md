# Codex CLI 0.147.0 compatibility reconciliation

Reviewed: 2026-08-12

This record binds the Codex CLI `0.147.0` observational compatibility baseline to
sanitized public-source and installed-interface evidence. It contains no prompt,
source content, command stream, credential, account data, or private artifact path.

## Official evidence

- Stable tag: `rust-v0.147.0`.
- Stable-tag release commit:
  `be6e8eac029b183056b7e4402879f15d2c85f61b`.
- Separately observed official `main` revision:
  `379cb68444057c721b6c8fa0bd610b7c6ecb9824`.

The stable-tag commit binds the reviewed release. The `main` revision is a separate
drift observation and must not be represented as the release commit. Future movement
of either official surface is `drift-review`; it does not rewrite this record.

The refreshed `main` observation was 19 commits ahead of the prior reviewed source
and remained on the same `rust-v0.147.0` stable release. The reviewed changes covered
authentication, MCP/plugin internals, delegated-session policy, skills telemetry,
thread history, and tests. They did not remove or rename the maintained `codex exec`,
`--sandbox workspace-write`, or `--add-dir` CLI surfaces, and they do not add a Codex
runtime or dispatch dependency to this project.

## Installed interface

The reviewed macOS arm64 install reported semantic version `0.147.0`. Its executable
bytes had SHA-256
`19c4f144c5226a9f17c58e6f0fa854843b0f77a6eb420f40e2745a12f10f5d37`.
The maintained CLI inventory remained compatible with the project's use of
`codex exec`, sandbox selection, and `--add-dir`.

The executable digest proves equality to the locally reviewed bytes only. It is not
a signature, publisher identity, binary provenance, notarization result, or general
attestation of the host.

## Decision and claim limits

Codex `0.147.0` is accepted as the current observational compatibility baseline.
This baseline supports an early warning when the installed version, stable release,
official source revision, or review age changes. It does not auto-update Codex,
authorize a download, mutate user configuration, or prove future-version behavior.

Codex drift is never an agy dispatch gate. It cannot resolve or reject an agy model,
change the caller's tier/model/effort choice, alter retry policy, grant permissions,
or replace `qa-gate.sh` and human diff review. Inconclusive official evidence remains
`evidence-unavailable`, not compatibility success.
