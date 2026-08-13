# agy 1.1.12 compatibility reconciliation

Reviewed: 2026-08-13

This record binds the agy `1.1.12` baseline and reviewed model/effort resolution
metadata to sanitized official, retained-version, and separately authorized capture
evidence. It retains the earlier eleven-slug capture as historical evidence and adds
the separately reviewed Gemini 3.7 Flash capture. It contains no account identifier,
credential, HOME contents, private path, raw model response, prompt, task, or provider
output.

## Official evidence

- Stable release/tag: `1.1.12`, published 2026-08-11.
- The exact stable tag is a commit ref resolving to
  `f7519c9084190ed421e89dd81c63970b5177c9ef`; the separately observed official
  `main` revision resolved to the same commit at review time.
- The official release notes add machine-readable `json` and `stream-json` output to
  `models` and `agents`, move their errors and progress output off stdout, and fix
  headless `--mode`. They do not advertise a change to the reviewed model slugs or
  the single compound-slug selection contract used by this project.
- The fixed `darwin_arm64` distribution canary returned version `1.1.12`, archive URL
  `https://storage.googleapis.com/antigravity-public/antigravity-cli/1.1.12-5877618327814144/darwin-arm/cli_mac_arm64.tar.gz`, and SHA-512
  `6dec2eab5d3188e68b6c2c1c011bb69a9506e0a20ae575c7500f058958042fd255d0969e65e76e7928cf41395e2155803824643df8069417886d2e2218af6d9d`.
- The official Google [Gemini 3.7 Flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash)
  and [thinking guide](https://ai.google.dev/gemini-api/docs/thinking)
  establish the `gemini-3.7-flash` base and its `low`, `medium`, and `high` levels;
  they do not establish a `minimal` level. Antigravity's `1.1.12` documentation and
  changelog are silent on exact agy compound slugs, so the exact slugs below come only
  from the separately authorized capture.

The distribution tuple remains an observational canary. It is not a signature,
archive-download permission, source proof, executable provenance, or independent
activation authority.

## Retained version and capture evidence

The retained bridge bound semantic version `1.1.12` to source and snapshot bytes
with SHA-256
`c8fd3c0016e101689f923f82da1c068b0e6dce3abcb0089e282742693ad4d344`.
The fresh recovery binding SHA-256 is
`b469298550a9d16921dc4f47ae72a5a00dfae414c11286097d2652e498f89da6`.

One newly authorized, no-retry account capture used the exact logical argv
`[agy.source, --output-format, json, models]` through the retained snapshot. The
child exited `0`, the runner observed one Popen, all capture-owned scratch
directories were empty after closure, and publication completed with:

- input profile SHA-256
  `1eabef4606b02c5312d52931b756f816cc8431cfa9dc08e05c855efba3ae2efe`;
- capture record/marker SHA-256
  `df1cc77947e5562976d51f295b4f023c2c24ef25db6d0afe30976004311996bd`;
- raw JSON stdout SHA-256
  `537985d72dc2284ed952931216af9fed8857d42e73511df4d959a45d54735115`
  (1,480 bytes);
- raw stderr SHA-256
  `53f588bc9a928f4a66908deacaca57dddc7e7ce177a0cc3586b5a501be26e1e8`
  (29 bytes); and
- capture-runner SHA-256
  `ca74f0ba46c4925f6b1a6fef99985d5184cb67739f38edc454117fd16c0fdc66`.

The strict JSON check required the fixed success/command shape, one `models` result,
and equality between its model IDs and the tab-separated response entries. The
existing offline semantic parser then accepted exactly these 11 slugs:

```text
gemini-3.6-flash-low
gemini-3.6-flash-medium
gemini-3.6-flash-high
gemini-3.5-flash-low
gemini-3.5-flash-medium
gemini-3.5-flash-high
gemini-3.1-pro-low
gemini-3.1-pro-high
claude-sonnet-4-6
claude-opus-4-6-thinking
gpt-oss-120b-medium
```

Their normalized SHA-256 remains
`8d46bcac6b8f27995635d91dc6f5a0e549d351e707efe11a82d8b6593fe12daf`.
No slug, adjustable pair, unsupported effort, or fixed classification changed from
the accepted `1.1.10` matrix. At that historical reconciliation, the matrix therefore
changed only its exact version and official source binding; its exact checked-in
matrix-byte SHA-256 was
`a36ead9a39715bb2380b3c36cbd8ae8e6e570e4147a4a4c7dc92f78e82e691a0`.

An earlier separately authorized attempt failed closed before publication when its
capture-owned TMPDIR was nonempty. It was not retried and contributes no inventory
authority. The successful evidence above is a distinct capture using a fresh
retained recovery chain and the reviewed JSON/cache contract.

### Supplemental Gemini 3.7 capture

One further separately authorized no-retry account capture used the same exact logical
argv `[agy.source, --output-format, json, models]` against the same attested `1.1.12`
source bytes (SHA-256 `c8fd3c0016e101689f923f82da1c068b0e6dce3abcb0089e282742693ad4d344`).
The separately observed official-source revision remains the distinct
`f7519c9084190ed421e89dd81c63970b5177c9ef` evidence. Its canonical profile was
prepared and validated before the one call. The
child exited `0`, the runner observed one Popen, and all capture-owned scratch was
empty after closure. Its sanitized bindings were:

- capture-runner SHA-256
  `c7a8542743f446501e902837ce2af540d396851c09fd50c012b9dbfa02b6ac3f`;
- canonical profile SHA-256
  `1eabef4606b02c5312d52931b756f816cc8431cfa9dc08e05c855efba3ae2efe`;
- capture JSON and completion-marker SHA-256
  `6ead226f93fea73669128a884e3d3affeb617cb39e5b38dd4348fc0c71b1f5af`;
- raw JSON stdout SHA-256
  `b75bd15381574af9ff1d9891dee36cc88a811c2abc86ef202c86c6b79077251c`
  (1,823 bytes); and
- sanitized response binding SHA-256
  `a7463eafad52e693c6d4890ed329f16aa60b1dfa9b058c051a13c0f0553efec1`
  (643 bytes).

Strict offline normalization accepted fourteen reviewed canonical slugs. It retained
all eleven historical slugs and added only:

```text
gemini-3.7-flash-low
gemini-3.7-flash-medium
gemini-3.7-flash-high
```

The normalized fourteen-slug SHA-256 is
`db2a3529568b1ce4bb112d4cb9a0c31a4f3d1b32bd787728d224894ec6db133c`.
The reviewed base has `low`, `medium`, and `high`; `minimal` remains unsupported.
The current exact checked-in matrix-byte SHA-256 is
`7aed92cc79154691407324f6d3bd75f335b67ab8ecc04cad89a60b5d15c03b3d`.

## Dispatch decision and limits

The prior eleven mappings and fixed classifications are unchanged. The only new
reviewed mappings are `gemini-3.7-flash` with `low`, `medium`, and `high`, each
resolving to the exact captured compound slug. Neither official Google model
documentation nor capture establishes agy's dual-selector composition: the project
continues to send one resolved downstream `--model`, never `--effort`. No new live
worker job was required for this bounded metadata reconciliation. The previously
bounded single-selector jobs remain representative behavior evidence, while the
complete offline dispatcher, updater, doctor, packaging, capture, and mutation suites
must still pass on the candidate bytes.

This reconciliation does not prove provider/backend identity, pricing, quota,
quality, authentication state, fallback behavior, dual-selector composition, or
effective billing. It does not forward agy's `--effort`, change tiers or
recommendations, authorize dispatch, accept a worker candidate, or replace
`qa-gate.sh` and human diff review. The capture runner published `captured` evidence;
the human reconciliation and atomic checked-in bindings make it baseline input.

P2-B and P2-C remain deferred under their separate evidence and approval gates.
Neither is activated by this baseline update or by a 30-, 60-, or 90-day date.
