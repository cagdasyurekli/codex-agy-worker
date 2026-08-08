# Compatibility sources

`update.sh check` uses fixed production repositories and a fixed 30-day review
interval. Environment variables cannot replace these sources or the cadence. The
check is read-only: it never fetches into the checkout, advances metadata, or takes
an external action.

Stable-release and source-revision observations use exact fixed GitHub REST endpoints
under `https://api.github.com/repos/`. The Python standard-library client ignores
ambient proxies, rejects redirects, requires strict bounded JSON response metadata,
and exposes only validated version/tag and revision fields through a bounded
process-group supervisor. Installed `agy --version` and `codex --version` probes use
the same supervisor with smaller time and byte limits. Read-only check/watch performs
no Git network operation, so Git URL rewrites, credential helpers, and Git proxy
configuration are not evidence inputs.

This transport boundary covers observation only. Explicit `update.sh apply` still
uses the caller's `git fetch` transport after resolving the expected release commit
through the fixed API, so ambient Git transport configuration remains part of that
separately authorized mutation path.

## agy (Antigravity CLI)

- Verified stable release: `agy-verified-version.txt`
- Reviewed source revision: `agy-upstream-head.txt`
- Documentation review date: `agy-last-reviewed.txt`
- Official releases/source: https://github.com/google-antigravity/antigravity-cli
- Official changelog: https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md
- Official CLI overview: https://antigravity.google/docs/cli-overview
- Official usage guidance: https://antigravity.google/docs/cli-using
- Official installer: https://antigravity.google/cli/install.sh
- Fixed `darwin_arm64` distribution manifest: https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/darwin_arm64.json
- Installed interface evidence: `./ground-truth.sh`

The verified baseline is agy `1.1.10` at reviewed source revision
`bfab12dac5bd090015a89cf82e65093d13b567d9`. The official release, source, CLI
documentation, one sandbox-correct 11-slug inventory, and two bounded
single-selector jobs were human-reconciled in
[`reviews/agy-1.1.10.md`](reviews/agy-1.1.10.md). The active matrix binds every
adjustable pair to one exact advertised compound slug and records fixed choices as
non-adjustable. It neither forwards `--effort` nor attests the effective provider
backend; silent fallback could not be independently excluded.

`agy-distribution-manifest.json` records the observed `1.1.10` version, exact Google
Storage archive URL, and lowercase SHA-512 tuple. It is an observational snapshot,
not an authoritative baseline, signature, or permission to download the archive.
The checker fetches only the fixed small manifest, rejects redirects and malformed
transport/schema/URL evidence, and never makes an archive request. A version change
or a same-version URL/build/hash change is `drift-review`; unavailable or invalid
evidence is inconclusive.

## Codex CLI

- Verified stable release: `codex-verified-version.txt`
- Reviewed source revision: `codex-upstream-head.txt`
- Documentation review date: `codex-last-reviewed.txt`
- Official releases/source: https://github.com/openai/codex/releases
- Official changelog: https://developers.openai.com/codex/changelog
- Official CLI reference: https://developers.openai.com/codex/cli/reference

The verified baseline is Codex CLI `0.146.0` at source revision
`bb5054fe47abe73ecbbd454751066a28c89f4bb9`.

## Advancing a baseline

Advance one tool only after a human reconciles its official docs, stable release,
source revision, and installed command inventory, then completes every offline suite
and the documented syntax/compile/diff checks. If dispatch behavior changed, a
bounded real job requires separate approval. The weekly watcher only reports
`unchanged`, `drift-review`, or `evidence-unavailable`; it cannot approve or write a
baseline. A future version or source change disables resolution until another human
reconciliation is accepted.

agy `1.1.11` remains unreconciled in the checked-in metadata. The transport and
process-boundary hardening needed to evaluate it does not itself advance version,
source, review date, manifest, or matrix records; `1.1.10` continues to fail closed
on observed drift until the separate inventory/provider evidence is authorized and
accepted.

Owner-captured inventory bytes are interpreted offline by `scripts/agy_inventory.py`,
which requires one exact reviewed canonical slug per line and complete one-time
coverage of all 11 slugs. Unknown tokens in reviewed provider namespaces fail closed.
Display text is non-authoritative: the `gpt-oss` alias is
valid only beside `gpt-oss-120b-medium` on that same line. This semantic parse is one
evidence input, not a version/executable binding or permission to advance metadata.
