# Compatibility sources

`update.sh check` uses fixed production repositories and a fixed 30-day review
interval. Environment variables cannot replace these sources or the cadence. The
check is read-only: it never fetches into the checkout, advances metadata, or takes
an external action.

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

The verified baseline is agy `1.1.9` at source revision
`21f650e7bb852f58562425ddd0c7d203c80e3d0e`. The official distribution manifest and
the installed executable advertise `1.1.10`; the public stable release and reviewed
source still establish only `1.1.9`. This is drift, not permission to advance that
baseline. The `1.1.10` model list remains a disabled candidate inventory in
`agy-model-effort-matrix.json`, because distribution availability does not
substantiate its source or behavior.

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
baseline.
