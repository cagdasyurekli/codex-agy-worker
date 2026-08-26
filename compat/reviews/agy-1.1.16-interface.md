# agy 1.1.16 interface observation (not model reconciliation)

Reviewed: 2026-08-20

This record preserves the bounded offline interface and official-release evidence
observed for agy `1.1.16`. It is intentionally **not** an accepted model/effort
reconciliation. No `agy models`, `agy agents`, plugin, prompt, authentication, or
provider operation was used to create this record.

## Official evidence

- Stable release/tag: `1.1.16`.
- Stable-tag release commit: `efa16f096dc02fb654b7e86958d268195284d014`.
- Fixed `darwin_arm64` distribution tuple:
  - version: `1.1.16`;
  - archive URL:
    `https://storage.googleapis.com/antigravity-public/antigravity-cli/1.1.16-6607970839166976/darwin-arm/cli_mac_arm64.tar.gz`;
  - SHA-512:
    `fa3a94a7d9d96cb367bf643ecf0da3b4d6b45f3e390ec6db1d699fdac4f7750894617152fc3c1695712a36eee926fff4f00ff4a44d372b3f604cfc9ec6fdbea6`.
- The official changelog's 1.1.13--1.1.16 entries include API-key/auth/quota fixes,
  `stream-json` input support, workspace access changes, MCP command additions, and
  Gemini 3.6/3.7 API-key effort handling. Those notes are a review trigger, not proof
  of this worker's dispatch, result, quota, or model-selection behavior.

The archive tuple is an observation canary. It is not a signature, a downloaded
artifact, executable provenance, a source proof, or permission to install anything.

## Safe local interface observation

The installed executable reported `1.1.16`. The safe phase of `ground-truth.sh`
ran only `agy --version` and `agy --help`; its help surface still documented the
worker-used output, print, mode, model, sandbox, and add-directory flags. This is
not evidence that a particular account can use a model, that a provider accepted a
request, or that a provider-side behavior remains unchanged.

This observation did not retain the exact raw C-locale help bytes or their SHA-256.
That does not block an exact matrix-version selection: the current direct-selection
contract proceeds after its bounded structural `--help` probe. A compatible version
drift still needs an explicit Codex disposition bound to its current raw-help digest.

The locally installed executable bytes had SHA-256
`095705beb4e4591c8ee7f8b6261473e15228f0f4b1bec58c62c966a6d4bfab30`. A bounded
offline inspection of those bytes established the concrete `steps.JSONOutput`
fields used by print-mode JSON: `conversation_id`, `status`, `response`, a string
`error`, `duration_seconds`, `num_turns`, `structured_output`, `json_schema`,
`usage`, and `command`. The `streamJSONEmitter.EmitError` implementation converts
the Go error to its `Error()` string before building that output. It does not expose
provider machine code/type or typed retry metadata as separate JSON fields.

This observation is a compatibility limit, not permission to classify raw error
prose. A terminal `status=ERROR` can be recognized as a structured agy failure, but
the bytes alone cannot prove that it is quota/rate-limit failure or derive a retry
duration without a separately reviewed exact upstream shape. Embedded generic
`google.rpc.RetryInfo` descriptors show only that the executable contains those
types; they do not prove that print-mode JSON emits them.

## Decision and limits

The active `agy-verified-version.txt`, source record, distribution snapshot, and
model/effort matrix remain the historical `1.1.12` reconciliation. Consequently,
the reviewed matrix must not resolve a `1.1.16` model/effort request. Agy-owned
default selection and caller-provided literal model pass-through remain subject to
their existing version-independent rules.

Promoting `1.1.16` requires a separate accepted reconciliation: official docs and
source review, an explicitly authorized and bounded account inventory capture, and
the normal offline and live-behavior gates where applicable. This record neither
authorizes that capture nor changes retry, authentication, permissions, or provider
selection policy.
