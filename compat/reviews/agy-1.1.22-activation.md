# agy 1.1.22 compatibility activation

Reviewed: 2026-08-28

This record activates the exact agy `1.1.22` version, reviewed release, and unchanged
fourteen-slug model inventory. It supplements rather than rewrites the historical
[`agy-1.1.22.md`](agy-1.1.22.md) observation and failed first capture. It contains no
account identifier, credential, HOME contents, private path, raw model response,
prompt, task, or provider output.

## Bound evidence

- Stable release/tag: `1.1.22` at the official release commit
  `556846a4bb94117222f53846896c7eb0d645307e`.
- Installed source/snapshot bytes SHA-256:
  `7b1317779085913d338bde0e9b39b72323d9083a879525f944fd469c8ecca906`.
- Version binding SHA-256:
  `d9d830e65d3a5c76df6d9e07e6ea7e14e14f290ab4036bdbae8cb33502e29f2a`.
- Raw C-locale combined root help SHA-256:
  `c26943c81bf16cf55fb35e6152eda42de30f6e09cd671e29dcbc22bc5517fde6`.
- Critical-capability SHA-256:
  `a08e143034f0cef4bd06b5de372b5e6b4a53e2e13db89ad26b0ea2c790bec293`.
- Capture-only runner source SHA-256:
  `c878d68c12017733878e463008eddb1d97213963675f567c47e1dd41e06586bc`.
- Reprofiled input profile SHA-256:
  `e9924c18789277f03bf998a9add60e43aa86cf843d3f6a2b47461e69a21f24e7`.
- Separately authorized, single-call capture record SHA-256:
  `626623c2c7b3b126efc2161c36554ecfa7fad3ce46e9dfcee8419c685ccaf2e3`.
- Raw capture stdout SHA-256:
  `b75bd15381574af9ff1d9891dee36cc88a811c2abc86ef202c86c6b79077251c`.
- Raw capture stderr SHA-256:
  `53f588bc9a928f4a66908deacaca57dddc7e7ce177a0cc3586b5a501be26e1e8`.
- Sanitized response binding SHA-256:
  `a7463eafad52e693c6d4890ed329f16aa60b1dfa9b058c051a13c0f0553efec1`.
- Normalized inventory SHA-256:
  `db2a3529568b1ce4bb112d4cb9a0c31a4f3d1b32bd787728d224894ec6db133c`.
- Checked-in inventory-binding JSON SHA-256:
  `e544ce0c8ac2fb11481b0590720ec3474122ec95238a02d6d3a13db833ed94e5`.
- Checked-in model/effort matrix SHA-256:
  `5a363dee8acb35e91b60405e705e8afaf155989dd755027cc5fa16741e42436c`.

The capture completed with status `captured`, exit `0`, exactly one `Popen`, and the
exact logical argv `source --output-format json models`; `--output-format` is a root
flag, while `agy help models` exposes only `-h`/`--help`. The sanitized top-level
object has `success: true`, `command.name: models`, and a `response` string;
`command.data.models` contains the model records, whose IDs exactly match the
tab-separated entries in that response. The raw stdout digest and sanitized response
digest respectively match the corresponding accepted 1.1.16 observation digests.
Offline normalization accepted exactly the fourteen slugs already listed in
`compat/agy-models-inventory-binding.json`. No wrapper mapping, fixed classification,
or captured slug changed, so the matrix advances only its agy version and reviewed
release binding. Official Gemini 3.1 Pro supports medium effort, but the accepted
account inventory lacks `gemini-3.1-pro-medium`; this wrapper's reviewed
single-compound-slug route therefore still does not expose that pair. Gemini 3.7
`minimal` remains outside the reviewed inventory. The matrix's legacy
`official-source` evidence label names the public
release-repository record; that repository contains no implementation source and the
label establishes no implementation-semantic claim.

## Decision and limits

Human reconciliation accepted the sanitized capture evidence and atomically advanced
the checked-in version/release-revision/review date, inventory binding, and matrix
metadata.
The capture runner's `captured` result was input to that decision, not activation
authority by itself. The prior failed observation remains historical evidence and is
not recharacterized as success.

This reconciliation does not prove provider/backend identity, model quality,
authentication state, pricing, quota, fallback, billing, or effective routing. It
does not authorize another provider call, choose a model, change caller selection,
forward agy's separate `--effort`, retry, or accept worker output. The controller
does not automatically relaunch, restart, or begin a fresh provider attempt. The
upstream agy `1.1.22` binary may internally retry endpoint HTTP 502 failures; its
retry count and backoff are unknown because the official release commit does not
publish implementation source. That closed-binary residual is outside the wrapper's
retry control and is not generalized from this one capture. The official release
commit and distribution observation are unsigned and do not prove backend identity.
