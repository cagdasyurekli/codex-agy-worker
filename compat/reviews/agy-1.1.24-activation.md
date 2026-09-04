# agy 1.1.24 compatibility activation

Reviewed: 2026-09-03

This record activates the exact agy `1.1.24` release and its accepted fourteen-slug
model inventory. It supplements rather than rewrites the historical 1.1.22 records.
It contains no account identifier, credential, HOME contents, private path, raw model
response, prompt, task, or provider output.

## Bound evidence

- Stable release/tag: `1.1.24` at official release commit
  `bf27ce1134b4ead2f7bfa0a4fb3cb5fcbebcaa5a`.
- Snapshot bytes SHA-256:
  `4d1138b2dbde56127969fd307281494d4a7dcc22759ce9adb44d36247df86151`.
- Version binding SHA-256:
  `8d67b9e301c7fa117c44d0cc35ecb23602dcd940814e4569a5a4eb5e54dadb74`.
- One authorized capture record SHA-256:
  `03b97e0266acf0f162f06e9da3857f75078dc3e2506d5964d1a09e044ad3403a`.
- Raw capture stdout SHA-256:
  `d02970e6b6b4e0910461999afca8fb99d757e9094ab2874b557dad18fc75464a`.
- Sanitized response SHA-256:
  `b1cc011310435afa07b1e132a5b7f3e22297aa21427177461c858bcbd6a58794`.
- Normalized inventory SHA-256:
  `d5e58ab55e91ebd4a2cd23841c76cbe12b47d607c62cd8c834fc8f6b9f078ad7`.
- Checked-in inventory-binding JSON SHA-256:
  `0173be39149bfceac7dbbafae6335f2e95d60b2e482bcd25a822f0b29d34f7a5`.
- Checked-in model/effort matrix SHA-256:
  `e3768004b4685754ba5bfd72e75724a2c78b0b9ed78391b0363b5f3d3ff191f1`.

The capture completed with status `captured`, exit `0`, exactly one `Popen`, and the
logical argv `snapshot --output-format json models`. The sanitized structured result
has `command.name: models` and a `response` string; `command.data.models` contains
the model records. This record does not claim a `success` field because the observed
top-level shape did not contain one. Offline normalization accepted exactly fourteen
reviewed slugs. They include `gemini-3.8-flash-low`,
`gemini-3.8-flash-medium`, and `gemini-3.8-flash-high`; the absent 3.5 Flash slugs
are not retained as current mappings. Gemini 3.1 Pro medium remains unavailable
because no reviewed compound slug advertises it.

The exact 1.1.24 executable began from an owner-prepared macOS kernel-reported
read-only mount. The runner rebound its digest and mount state before and after the
child. This blocks the observed in-place update route, but does not prove that a
closed binary cannot re-exec from a writable location; same-UID and administrator
tampering remain outside this guarantee.

## Decision and limits

The checked-in version/release binding, inventory binding, and matrix are advanced
atomically to 1.1.24. The former 1.1.22 fixed adapters remain legacy compatibility
operations without activation authority. The capture result is evidence for this
decision, not authority to make another provider call.

This reconciliation does not prove provider/backend identity, model quality,
authentication state, pricing, quota, fallback, billing, or effective routing. It
does not select a model, forward agy's separate `--effort`, retry, accept worker
output, or authorize Git or publication actions. The controller does not
automatically relaunch, restart, or begin a fresh provider attempt. Any upstream
internal retry behavior remains outside this wrapper's control.
