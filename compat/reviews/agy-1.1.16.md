# agy 1.1.16 compatibility reconciliation

Reviewed: 2026-08-20

This record advances the active agy baseline and reviewed model/effort inventory to
`1.1.16`. It preserves the complete `1.1.12` reconciliation as historical evidence.
It contains no account identifier, credential, HOME contents, private path, raw model
response, prompt, task, or provider output.

## Bound evidence

- Stable release/tag: `1.1.16` at source revision
  `efa16f096dc02fb654b7e86958d268195284d014`.
- Fixed `darwin_arm64` distribution build: `6607970839166976`; SHA-512
  `fa3a94a7d9d96cb367bf643ecf0da3b4d6b45f3e390ec6db1d699fdac4f7750894617152fc3c1695712a36eee926fff4f00ff4a44d372b3f604cfc9ec6fdbea6`.
- Retained source/snapshot bytes SHA-256:
  `095705beb4e4591c8ee7f8b6261473e15228f0f4b1bec58c62c966a6d4bfab30`.
- Version binding SHA-256:
  `facf6adc18afc85ed5c232e3e1f9ad0fbcac7d62f1f98866cabb615d43069a57`.
- Separately authorized, single-call, no-retry capture record SHA-256:
  `04f9cf2d18c14635689630c7bb50437151f2b0eb1d414d0d943212fe12c7a20e`.
- Raw capture stdout SHA-256:
  `b75bd15381574af9ff1d9891dee36cc88a811c2abc86ef202c86c6b79077251c`.
- Sanitized response binding SHA-256:
  `a7463eafad52e693c6d4890ed329f16aa60b1dfa9b058c051a13c0f0553efec1`.
- Normalized inventory SHA-256:
  `db2a3529568b1ce4bb112d4cb9a0c31a4f3d1b32bd787728d224894ec6db133c`.
- Checked-in inventory-binding JSON SHA-256:
  `3f34e6f6bfcf7b7e65951e02f92580c2858f32016f115866160f279d2d3a2747`.
- Checked-in model/effort matrix SHA-256:
  `a586927552d90295529f3059989a2a8c36c234d41b8f79d61c1c89edbf829e00`.

Strict offline normalization accepted the same fourteen exact slugs as the accepted
`1.1.12` inventory:

```text
claude-opus-4-6-thinking
claude-sonnet-4-6
gemini-3.1-pro-high
gemini-3.1-pro-low
gemini-3.5-flash-high
gemini-3.5-flash-low
gemini-3.5-flash-medium
gemini-3.6-flash-high
gemini-3.6-flash-low
gemini-3.6-flash-medium
gemini-3.7-flash-high
gemini-3.7-flash-low
gemini-3.7-flash-medium
gpt-oss-120b-medium
```

No mapping, fixed classification, unsupported effort, or slug changed. The active
matrix therefore advances only its agy version and reviewed source binding. Gemini
3.7 `minimal` remains unsupported.

## Decision and limits

Human reconciliation accepted the exact inventory binding and atomically advanced
the checked-in version/source/distribution, inventory binding, and matrix metadata.
The capture runner's `captured` result was input to that decision, not activation
authority by itself. The earlier interface-only observation remains a historical
record of why a model inventory capture and review were required.

This reconciliation does not prove provider/backend identity, model quality,
authentication state, pricing, quota, fallback behavior, billing, or routing. It
does not authorize a provider call, choose a model, change caller selection, forward
agy's separate `--effort`, retry, or accept worker output. The wrapper continues to
resolve one reviewed base/effort pair to one exact compound slug and sends a single
downstream `--model`; Codex still owns diff review and driver-run verification.
