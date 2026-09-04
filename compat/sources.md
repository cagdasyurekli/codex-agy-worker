# Compatibility sources

`update.sh check` uses fixed production repositories and a fixed 30-day review
interval. Environment variables cannot replace these sources or the cadence. The
check is read-only: it never fetches into the checkout, advances metadata, or takes
an external action.

Stable-release and source-revision observations use exact fixed GitHub REST endpoints
under `https://api.github.com/repos/`. The Python standard-library client ignores
ambient proxies, rejects redirects, requires strict bounded JSON response metadata,
and exposes only validated version/tag and revision fields through a bounded
process-group supervisor. Project release commits bind through the compact exact
`git/ref/tags/<tag>` document: a direct commit is accepted, or exactly one validated
annotated-tag object is resolved to a commit through the fixed `git/tags/<sha>`
endpoint. Nested, malformed, mismatched, or non-commit targets fail closed. Release
documents have a 512-KiB ceiling while ref/source and tag-object documents retain a
256-KiB ceiling. Installed
`agy --version` and `codex --version` probes use the same supervisor with smaller time
and byte limits. Read-only check/watch performs no Git network operation, so Git URL
rewrites, credential helpers, and Git proxy configuration are not evidence inputs.

This transport boundary covers observation only. Explicit `update.sh apply` still
uses the caller's `git fetch` transport after resolving the expected release commit
through the fixed API, so ambient Git transport configuration remains part of that
separately authorized mutation path.

## agy (Antigravity CLI)

- Verified stable release: `agy-verified-version.txt`
- Reviewed release revision: `agy-upstream-head.txt`
- Documentation review date: `agy-last-reviewed.txt`
- Official releases/source: https://github.com/google-antigravity/antigravity-cli
- Official changelog: https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md
- Official CLI overview: https://antigravity.google/docs/cli-overview
- Official usage guidance: https://antigravity.google/docs/cli-using
- Official installer: https://antigravity.google/cli/install.sh
- Official Google Gemini 3.7 Flash page: https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash
- Official Google thinking guide: https://ai.google.dev/gemini-api/docs/thinking
- Fixed `darwin_arm64` distribution manifest: https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/darwin_arm64.json
- Installed interface evidence: `./ground-truth.sh`

The verified active baseline is agy `1.1.24` at official release commit
`bf27ce1134b4ead2f7bfa0a4fb3cb5fcbebcaa5a`; its accepted inventory is reconciled in
[`reviews/agy-1.1.24-activation.md`](reviews/agy-1.1.24-activation.md). The earlier
1.1.22 observations remain historical records. The active matrix binds every
adjustable pair to one exact advertised compound slug and records fixed choices as
non-adjustable. It neither forwards `--effort` nor attests the effective provider
backend; silent fallback could not be independently excluded.

The complete historical `1.1.16` and `1.1.12` reconciliations remain in
[`reviews/agy-1.1.16.md`](reviews/agy-1.1.16.md) and
[`reviews/agy-1.1.12.md`](reviews/agy-1.1.12.md). The earlier `1.1.16` interface
observation is retained in
[`reviews/agy-1.1.16-interface.md`](reviews/agy-1.1.16-interface.md). It records
the official release/source/distribution tuple and a safe local version/help review,
plus the observed print-mode JSON boundary: agy emits terminal failure detail as a
string `error`, not separate machine-code or typed-retry fields. It contains no
account inventory, provider call, or model/effort decision. It remains a historical
drift-review record and did not activate the current baseline or matrix by itself.

The narrow agy `1.1.13` structured quota-terminal evidence for Issue #59 is retained
in [`reviews/agy-1.1.13-quota-terminal.md`](reviews/agy-1.1.13-quota-terminal.md).
It authorizes only the exact version/shape classifier and sanitized countdown; it is
not a general quota/rate-limit signature, baseline update, or retry authority.

`agy-distribution-manifest.json` records the observed `1.1.22` version, exact Google
Storage archive URL, and lowercase SHA-512 tuple. It is an observational snapshot,
not an authoritative baseline, signature, or permission to download the archive.
The checker fetches only the fixed small manifest, rejects redirects and malformed
transport/schema/URL evidence, and never makes an archive request. A version change
or a same-version URL/build/hash change is `drift-review`; unavailable or invalid
evidence is inconclusive.

## Codex CLI

- Verified stable release: `codex-verified-version.txt`
- Reviewed stable-tag source revision: `codex-upstream-head.txt`
- Documentation review date: `codex-last-reviewed.txt`
- Official releases/source: https://github.com/openai/codex/releases
- Official changelog: https://developers.openai.com/codex/changelog
- Official CLI reference: https://developers.openai.com/codex/cli/reference

The verified baseline is Codex CLI `0.150.1`. Its official annotated stable tag
`rust-v0.150.1` resolves to release commit
`90854393966b21e9ebfd21b122334eb09a20c93d`. The moving official `main` branch is
review context, not a daily compatibility fingerprint. The installed macOS arm64 CLI
and the maintained `exec`, sandbox, and `--add-dir` surfaces were reconciled in
[`reviews/codex-0.150.1.md`](reviews/codex-0.150.1.md). Codex compatibility
metadata is observation-only and never gates agy dispatch, resolves an agy model,
changes a caller selection, or creates a second worker backend.

## Advancing a baseline

Advance one tool only after a human reconciles its official docs, stable release,
available release evidence, and installed command inventory, then completes every offline suite
and the documented syntax/compile/diff checks. If dispatch behavior changed, a
bounded real job requires separate approval. The daily watcher only reports
`unchanged`, `drift-review`, or `evidence-unavailable`; it cannot approve or write a
baseline. For agy, a future version or source change disables reviewed pair
resolution until another human reconciliation is accepted. Codex drift remains
observation-only and never disables agy dispatch.

The original agy `1.1.22` failed capture is retained as non-activating historical
metadata. The separately authorized 1.1.24 capture and human reconciliation advance
the active version, release binding, inventory, and matrix only through
[`reviews/agy-1.1.24-activation.md`](reviews/agy-1.1.24-activation.md). Neither record
authorizes another account call.
Ordinary version-independent literal model pass-through and agy-owned default
selection remain independent of that matrix; reviewed model/effort resolution still
depends on an exact accepted version/release-revision/digest binding.

Owner-captured inventory bytes are interpreted offline by `scripts/agy_inventory.py`,
which requires one exact reviewed canonical slug per line and complete one-time
coverage of all 14 slugs. Unknown tokens in reviewed provider namespaces fail closed.
Display text is non-authoritative: the `gpt-oss` alias is
valid only beside `gpt-oss-120b-medium` on that same line. This semantic parse is one
evidence input, not a version/executable binding or permission to advance metadata.
