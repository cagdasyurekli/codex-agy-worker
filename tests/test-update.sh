#!/usr/bin/env bash
# Offline updater tests using local Git repositories and release tags.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
TMP="$(mktemp -d -t agyworker-update-tests.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
expect_exit() {
    local name="$1" want="$2" got="$3"
    if [[ "$got" == "$want" ]]; then ok "$name (exit $got)"; else bad "$name (exit $got, wanted $want)"; fi
}

SOURCE="$TMP/source"
REMOTE="$TMP/remote.git"
NO_TAG_REMOTE="$TMP/no-tag-remote.git"
UPSTREAM_SOURCE="$TMP/agy-upstream-source"
UPSTREAM_REMOTE="$TMP/agy-upstream.git"
CODEX_UPSTREAM_SOURCE="$TMP/codex-upstream-source"
CODEX_UPSTREAM_REMOTE="$TMP/codex-upstream.git"
CLIENT="$TMP/client"
DIRTY_CLIENT="$TMP/dirty-client"
NO_TAG_CLIENT="$TMP/no-tag-client"
IGNORED_CLIENT="$TMP/ignored-client"
INSTALL_FAIL_CLIENT="$TMP/install-fail-client"
SKILLS="$TMP/skills"
OFFICIAL_TOOL_URL="https://github.com/cagdasyurekli/codex-agy-worker.git"
OFFICIAL_UPSTREAM_URL="https://github.com/google-antigravity/antigravity-cli.git"
OFFICIAL_CODEX_UPSTREAM_URL="https://github.com/openai/codex.git"
mkdir -p "$SOURCE/skills" "$SOURCE/tests" "$SOURCE/compat" "$SOURCE/scripts" "$TMP/bin" "$SKILLS"
cp "$ROOT/update.sh" "$ROOT/install.sh" "$SOURCE/"
cp -R "$ROOT/skills/agy-worker" "$SOURCE/skills/agy-worker"
cp "$ROOT/compat/"* "$SOURCE/compat/"
cp "$ROOT/scripts/compatibility.py" "$SOURCE/scripts/"

mkdir -p "$UPSTREAM_SOURCE"
git -C "$UPSTREAM_SOURCE" init -q -b main
git -C "$UPSTREAM_SOURCE" config user.email test@example.com
git -C "$UPSTREAM_SOURCE" config user.name test
printf 'reviewed upstream\n' > "$UPSTREAM_SOURCE/README.md"
git -C "$UPSTREAM_SOURCE" add README.md
git -C "$UPSTREAM_SOURCE" commit -qm 'reviewed upstream fixture'
git -C "$UPSTREAM_SOURCE" tag v1.1.9
UPSTREAM_HEAD="$(git -C "$UPSTREAM_SOURCE" rev-parse HEAD)"
git init -q --bare "$UPSTREAM_REMOTE"
git -C "$UPSTREAM_SOURCE" remote add publish "$UPSTREAM_REMOTE"
git -C "$UPSTREAM_SOURCE" push -q publish main --tags
git --git-dir="$UPSTREAM_REMOTE" symbolic-ref HEAD refs/heads/main
printf '%s\n' "$UPSTREAM_HEAD" > "$SOURCE/compat/agy-upstream-head.txt"
python3 -c 'from datetime import date; print(date.today().isoformat())' > "$SOURCE/compat/agy-last-reviewed.txt"

mkdir -p "$CODEX_UPSTREAM_SOURCE"
git -C "$CODEX_UPSTREAM_SOURCE" init -q -b main
git -C "$CODEX_UPSTREAM_SOURCE" config user.email test@example.com
git -C "$CODEX_UPSTREAM_SOURCE" config user.name test
printf 'reviewed Codex upstream\n' > "$CODEX_UPSTREAM_SOURCE/README.md"
git -C "$CODEX_UPSTREAM_SOURCE" add README.md
git -C "$CODEX_UPSTREAM_SOURCE" commit -qm 'reviewed Codex upstream fixture'
git -C "$CODEX_UPSTREAM_SOURCE" tag rust-v0.146.0
CODEX_UPSTREAM_HEAD="$(git -C "$CODEX_UPSTREAM_SOURCE" rev-parse HEAD)"
git init -q --bare "$CODEX_UPSTREAM_REMOTE"
git -C "$CODEX_UPSTREAM_SOURCE" remote add publish "$CODEX_UPSTREAM_REMOTE"
git -C "$CODEX_UPSTREAM_SOURCE" push -q publish main --tags
git --git-dir="$CODEX_UPSTREAM_REMOTE" symbolic-ref HEAD refs/heads/main
printf '%s\n' "$CODEX_UPSTREAM_HEAD" > "$SOURCE/compat/codex-upstream-head.txt"
python3 -c 'from datetime import date; print(date.today().isoformat())' > "$SOURCE/compat/codex-last-reviewed.txt"
cat > "$SOURCE/tests/test-qa-gate.sh" <<'STUB'
#!/usr/bin/env bash
echo "fixture qa suite passed"
STUB
cat > "$SOURCE/tests/test-agy-worker.sh" <<'STUB'
#!/usr/bin/env bash
echo "fixture dispatcher suite passed"
STUB
cat > "$TMP/bin/agy" <<'STUB'
#!/usr/bin/env bash
case "${FAKE_AGY_MODE:-version}" in
  empty) exit 0 ;;
  usage) printf 'Usage: agy [options] [command]\n'; exit 0 ;;
  fail) exit 7 ;;
esac
printf '%s\n' "${FAKE_AGY_OUTPUT:-${FAKE_AGY_VERSION:-1.1.9}}"
STUB
cat > "$TMP/bin/codex" <<'STUB'
#!/usr/bin/env bash
case "${FAKE_CODEX_MODE:-version}" in
  empty) exit 0 ;;
  usage) printf 'Usage: codex [OPTIONS]\n'; exit 0 ;;
  fail) exit 7 ;;
esac
printf '%s\n' "${FAKE_CODEX_OUTPUT:-codex-cli ${FAKE_CODEX_VERSION:-0.146.0}}"
STUB
chmod +x "$SOURCE/"*.sh "$SOURCE/tests/"*.sh "$TMP/bin/agy" "$TMP/bin/codex" "$SOURCE/scripts/compatibility.py"

git -C "$SOURCE" init -q -b main
git -C "$SOURCE" config user.email test@example.com
git -C "$SOURCE" config user.name test
printf 'v1\n' > "$SOURCE/release-marker.txt"
printf '*.cache\n' > "$SOURCE/.gitignore"
git -C "$SOURCE" add -A
git -C "$SOURCE" commit -qm 'v1 fixture'
git -C "$SOURCE" tag v1.0.0
printf 'v2\n' > "$SOURCE/release-marker.txt"
printf 'release-owned cache\n' > "$SOURCE/private.cache"
git -C "$SOURCE" add release-marker.txt
git -C "$SOURCE" add -f private.cache
git -C "$SOURCE" commit -qm 'v2 fixture'
V2_COMMIT="$(git -C "$SOURCE" rev-parse HEAD)"
git -C "$SOURCE" tag v1.1.0

git init -q --bare "$REMOTE"
git -C "$SOURCE" remote add publish "$REMOTE"
git -C "$SOURCE" push -q publish main --tags
git --git-dir="$REMOTE" symbolic-ref HEAD refs/heads/main
git init -q --bare "$NO_TAG_REMOTE"
git -C "$SOURCE" push -q "$NO_TAG_REMOTE" main
git --git-dir="$NO_TAG_REMOTE" symbolic-ref HEAD refs/heads/main
git clone -q "$REMOTE" "$CLIENT"
git clone -q "$REMOTE" "$DIRTY_CLIENT"
git clone -q "$REMOTE" "$IGNORED_CLIENT"
git clone -q "$REMOTE" "$INSTALL_FAIL_CLIENT"
git clone -q "$NO_TAG_REMOTE" "$NO_TAG_CLIENT"

configure_official_urls() {
    local checkout="$1" release_remote="$2"
    git -C "$checkout" remote set-url origin "$OFFICIAL_TOOL_URL"
    git -C "$checkout" config "url.$release_remote.insteadOf" "$OFFICIAL_TOOL_URL"
    git -C "$checkout" config "url.$UPSTREAM_REMOTE.insteadOf" "$OFFICIAL_UPSTREAM_URL"
    git -C "$checkout" config "url.$CODEX_UPSTREAM_REMOTE.insteadOf" "$OFFICIAL_CODEX_UPSTREAM_URL"
}

for checkout in "$CLIENT" "$DIRTY_CLIENT" "$IGNORED_CLIENT" "$INSTALL_FAIL_CLIENT"; do
    git -C "$checkout" checkout -qb local-v1 v1.0.0
    git -C "$checkout" tag -d v1.1.0 >/dev/null
    configure_official_urls "$checkout" "$REMOTE"
done
configure_official_urls "$NO_TAG_CLIENT" "$NO_TAG_REMOTE"

echo "update.sh offline test suite"
echo

PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/check.out" 2> "$TMP/check.err"
rc=$?
expect_exit "check reports without changing files" 0 "$rc"
if grep -Fq 'tool update: available v1.0.0 -> v1.1.0' "$TMP/check.out"; then
    ok "check identifies the latest stable release"
else
    bad "check identifies the latest stable release"
fi
if ! git -C "$CLIENT" show-ref --verify --quiet refs/tags/v1.1.0 \
        && [[ -z "$(git -C "$CLIENT" status --porcelain --untracked-files=all)" ]]; then
    ok "check does not fetch tags or mutate the checkout"
else
    bad "check does not fetch tags or mutate the checkout"
fi
if grep -Fq 'agy compatibility:' "$TMP/check.out" \
        && grep -Fq 'codex compatibility:' "$TMP/check.out" \
        && grep -Fq 'stable release: unchanged' "$TMP/check.out" \
        && grep -Fq 'source revision: unchanged' "$TMP/check.out"; then
    ok "local check always reports both tools and official evidence"
else
    bad "local check always reports both tools and official evidence"
fi

WATCH_BEFORE="$(git -C "$CLIENT" status --porcelain --untracked-files=all)"
PATH="$TMP/bin:$PATH" FAKE_AGY_MODE=fail FAKE_CODEX_MODE=fail \
    "$CLIENT/update.sh" check --watch > "$TMP/watch.out" 2> "$TMP/watch.err"
rc=$?
expect_exit "watch mode needs no installed-tool evidence" 0 "$rc"
WATCH_AFTER="$(git -C "$CLIENT" status --porcelain --untracked-files=all)"
if [[ "$WATCH_BEFORE" == "$WATCH_AFTER" ]] \
        && grep -Fq 'installed: not required in watch mode' "$TMP/watch.out"; then
    ok "watch mode is read-only and bounded to official evidence"
else
    bad "watch mode is read-only and bounded to official evidence"
fi

PATH="$TMP/bin:$PATH" OFFICIAL_AGY_UPSTREAM=https://example.invalid/secret \
    OFFICIAL_CODEX_UPSTREAM=https://example.invalid/secret \
    COMPATIBILITY_REVIEW_DAYS=9999 \
    "$CLIENT/update.sh" check > "$TMP/fixed-policy.out" 2> "$TMP/fixed-policy.err"
rc=$?
expect_exit "environment cannot override fixed sources or cadence" 0 "$rc"
if ! grep -Fq 'example.invalid' "$TMP/fixed-policy.out" "$TMP/fixed-policy.err"; then
    ok "ignored source overrides are never disclosed"
else
    bad "ignored source overrides are never disclosed"
fi

PATH="$TMP/bin:$PATH" FAKE_CODEX_VERSION=9.9.9 \
    "$CLIENT/update.sh" check > "$TMP/codex-version-drift.out" 2> "$TMP/codex-version-drift.err"
rc=$?
expect_exit "installed Codex version drift requires review" 3 "$rc"

PATH="$TMP/bin:$PATH" FAKE_AGY_MODE=usage \
    "$CLIENT/update.sh" check > "$TMP/agy-usage.out" 2> "$TMP/agy-usage.err"
rc=$?
expect_exit "agy usage text is inconclusive, not version evidence" 2 "$rc"

PATH="$TMP/bin:$PATH" FAKE_CODEX_MODE=empty \
    "$CLIENT/update.sh" check > "$TMP/codex-empty.out" 2> "$TMP/codex-empty.err"
rc=$?
expect_exit "empty Codex output is inconclusive" 2 "$rc"

PATH="$TMP/bin:$PATH" FAKE_AGY_MODE=fail \
    "$CLIENT/update.sh" check > "$TMP/agy-fail.out" 2> "$TMP/agy-fail.err"
rc=$?
expect_exit "failed documented version command is inconclusive" 2 "$rc"

NO_AGY_BIN="$TMP/no-agy-bin"
mkdir -p "$NO_AGY_BIN"
for required_tool in bash git python3 dirname; do
    ln -s "$(command -v "$required_tool")" "$NO_AGY_BIN/$required_tool"
done
cp "$TMP/bin/codex" "$NO_AGY_BIN/codex"
PATH="$NO_AGY_BIN" "$CLIENT/update.sh" check > "$TMP/missing-agy.out" 2> "$TMP/missing-agy.err"
rc=$?
expect_exit "missing installed tool is established drift" 3 "$rc"

PATH="$TMP/bin:$PATH" FAKE_AGY_MODE=usage FAKE_CODEX_VERSION=9.9.9 \
    "$CLIENT/update.sh" check > "$TMP/aggregate.out" 2> "$TMP/aggregate.err"
rc=$?
expect_exit "inconclusive evidence outranks established drift" 2 "$rc"
if grep -Fq 'agy compatibility:' "$TMP/aggregate.out" \
        && grep -Fq 'codex compatibility:' "$TMP/aggregate.out"; then
    ok "aggregation still reports both tools"
else
    bad "aggregation still reports both tools"
fi
if [[ -z "$(git -C "$CLIENT" status --porcelain --untracked-files=all)" ]]; then
    ok "check exits 0, 3, and 2 without changing repository state"
else
    bad "check exits 0, 3, and 2 without changing repository state"
fi

PATH="$TMP/bin:$PATH" FAKE_AGY_VERSION=9.9.9 \
    "$CLIENT/update.sh" check > "$TMP/version-drift.out" 2> "$TMP/version-drift.err"
rc=$?
expect_exit "installed agy version drift requires review" 3 "$rc"

printf '2000-01-01\n' > "$CLIENT/compat/agy-last-reviewed.txt"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/review-due.out" 2> "$TMP/review-due.err"
rc=$?
expect_exit "periodic compatibility review becomes due" 3 "$rc"
if grep -Fq 'documentation review: drift-review' "$TMP/review-due.out"; then ok "review age is fixed at 30 days"; else bad "review age is fixed at 30 days"; fi
git -C "$CLIENT" checkout -q -- compat/agy-last-reviewed.txt

printf '2000-01-01\n' > "$CLIENT/compat/codex-last-reviewed.txt"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/codex-review-due.out" 2> "$TMP/codex-review-due.err"
rc=$?
expect_exit "Codex documentation review age is enforced" 3 "$rc"
git -C "$CLIENT" checkout -q -- compat/codex-last-reviewed.txt

printf '2999-01-01\n' > "$CLIENT/compat/agy-last-reviewed.txt"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/future-review.out" 2> "$TMP/future-review.err"
rc=$?
expect_exit "future compatibility review date is inconclusive" 2 "$rc"
if grep -Fq 'evidence-unavailable' "$TMP/future-review.out"; then ok "future review date is identified"; else bad "future review date is identified"; fi
git -C "$CLIENT" checkout -q -- compat/agy-last-reviewed.txt

git -C "$CODEX_UPSTREAM_SOURCE" tag rust-v0.147.0
git -C "$CODEX_UPSTREAM_SOURCE" push -q publish rust-v0.147.0
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/codex-stable-drift.out" 2> "$TMP/codex-stable-drift.err"
rc=$?
expect_exit "Codex stable release drift requires review" 3 "$rc"
if grep -Fq 'official 0.147.0; verified 0.146.0' "$TMP/codex-stable-drift.out"; then
    ok "Codex stable release drift is reported separately"
else
    bad "Codex stable release drift is reported separately"
fi
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check --watch > "$TMP/watch-drift.out" 2> "$TMP/watch-drift.err"
rc=$?
expect_exit "watch mode preserves drift-review exit semantics" 3 "$rc"

git -C "$UPSTREAM_SOURCE" tag v1.1.10
git -C "$UPSTREAM_SOURCE" push -q publish v1.1.10
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/agy-stable-drift.out" 2> "$TMP/agy-stable-drift.err"
rc=$?
expect_exit "agy stable release drift requires review without baseline advance" 3 "$rc"

printf 'Codex upstream moved\n' >> "$CODEX_UPSTREAM_SOURCE/README.md"
git -C "$CODEX_UPSTREAM_SOURCE" add README.md
git -C "$CODEX_UPSTREAM_SOURCE" commit -qm 'Codex upstream drift fixture'
git -C "$CODEX_UPSTREAM_SOURCE" push -q publish main
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/codex-source-drift.out" 2> "$TMP/codex-source-drift.err"
rc=$?
expect_exit "Codex source revision drift requires review" 3 "$rc"

printf 'not-a-version\n' > "$CLIENT/compat/agy-verified-version.txt"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/malformed-version.out" 2> "$TMP/malformed-version.err"
rc=$?
expect_exit "malformed baseline metadata is inconclusive" 2 "$rc"
git -C "$CLIENT" checkout -q -- compat/agy-verified-version.txt

printf 'credential-bearing-not-a-revision\n' > "$CLIENT/compat/codex-upstream-head.txt"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/malformed-revision.out" 2> "$TMP/malformed-revision.err"
rc=$?
expect_exit "malformed source metadata is inconclusive" 2 "$rc"
if ! grep -Fq 'credential-bearing' "$TMP/malformed-revision.out" "$TMP/malformed-revision.err"; then
    ok "malformed metadata bytes are not disclosed"
else
    bad "malformed metadata bytes are not disclosed"
fi
git -C "$CLIENT" checkout -q -- compat/codex-upstream-head.txt

MISSING_UPSTREAM="$TMP/missing-upstream.git"
git -C "$CLIENT" config --unset-all "url.$UPSTREAM_REMOTE.insteadOf"
git -C "$CLIENT" config "url.$MISSING_UPSTREAM.insteadOf" "$OFFICIAL_UPSTREAM_URL"
PATH="$TMP/bin:$PATH" FAKE_CODEX_VERSION=9.9.9 \
    "$CLIENT/update.sh" check > "$TMP/unavailable-source.out" 2> "$TMP/unavailable-source.err"
rc=$?
expect_exit "unavailable official evidence outranks drift" 2 "$rc"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check --watch > "$TMP/watch-unavailable.out" 2> "$TMP/watch-unavailable.err"
rc=$?
expect_exit "watch mode preserves evidence-unavailable exit semantics" 2 "$rc"
git -C "$CLIENT" config --unset-all "url.$MISSING_UPSTREAM.insteadOf"
git -C "$CLIENT" config "url.$UPSTREAM_REMOTE.insteadOf" "$OFFICIAL_UPSTREAM_URL"

printf 'upstream moved\n' >> "$UPSTREAM_SOURCE/README.md"
git -C "$UPSTREAM_SOURCE" add README.md
git -C "$UPSTREAM_SOURCE" commit -qm 'upstream drift fixture'
git -C "$UPSTREAM_SOURCE" push -q publish main
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/upstream-drift.out" 2> "$TMP/upstream-drift.err"
rc=$?
expect_exit "official upstream HEAD drift requires review" 3 "$rc"

PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$SKILLS" "$NO_TAG_CLIENT/update.sh" apply \
    > "$TMP/no-tag.out" 2> "$TMP/no-tag.err"
rc=$?
expect_exit "implicit apply reports that no stable release exists" 2 "$rc"

printf 'dirty\n' > "$DIRTY_CLIENT/dirty.txt"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$SKILLS" \
    "$DIRTY_CLIENT/update.sh" apply v1.1.0 > "$TMP/dirty.out" 2> "$TMP/dirty.err"
rc=$?
expect_exit "apply refuses a dirty checkout" 2 "$rc"
if [[ "$(git -C "$DIRTY_CLIENT" rev-parse HEAD)" != "$V2_COMMIT" ]]; then ok "dirty refusal leaves HEAD unchanged"; else bad "dirty refusal leaves HEAD unchanged"; fi

printf 'private local bytes\n' > "$IGNORED_CLIENT/private.cache"
IGNORED_BEFORE="$(<"$IGNORED_CLIENT/private.cache")"
IGNORED_HEAD="$(git -C "$IGNORED_CLIENT" rev-parse HEAD)"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$SKILLS" \
    "$IGNORED_CLIENT/update.sh" apply v1.1.0 > "$TMP/ignored.out" 2> "$TMP/ignored.err"
rc=$?
expect_exit "apply refuses an ignored path the release would track" 2 "$rc"
if [[ "$(<"$IGNORED_CLIENT/private.cache")" == "$IGNORED_BEFORE" ]] \
        && [[ "$(git -C "$IGNORED_CLIENT" rev-parse HEAD)" == "$IGNORED_HEAD" ]]; then
    ok "ignored collision refusal preserves bytes and HEAD"
else
    bad "ignored collision refusal preserves bytes and HEAD"
fi

printf 'harmless local cache\n' > "$CLIENT/harmless.cache"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$SKILLS" \
    "$CLIENT/update.sh" apply v1.1.0 > "$TMP/apply.out" 2> "$TMP/apply.err"
rc=$?
expect_exit "explicit apply validates and fast-forwards" 0 "$rc"
if [[ "$(git -C "$CLIENT" rev-parse HEAD)" == "$V2_COMMIT" ]] \
        && [[ "$(<"$CLIENT/release-marker.txt")" == "v2" ]]; then
    ok "apply lands the verified release commit"
else
    bad "apply lands the verified release commit"
fi
CLIENT_REAL="$(cd "$CLIENT" && pwd -P)"
if [[ "$(<"$SKILLS/agy-worker/.pipeline-root")" == "$CLIENT_REAL" ]] \
        && [[ -f "$SKILLS/agy-worker/agents/openai.yaml" ]]; then
    ok "apply reinstalls the canonical Codex skill bundle"
else
    bad "apply reinstalls the canonical Codex skill bundle"
fi
if [[ "$(<"$CLIENT/harmless.cache")" == "harmless local cache" ]]; then ok "harmless ignored cache is preserved"; else bad "harmless ignored cache is preserved"; fi

BLOCKED_SKILLS="$TMP/not-a-directory"
printf 'not a directory\n' > "$BLOCKED_SKILLS"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$BLOCKED_SKILLS" \
    "$INSTALL_FAIL_CLIENT/update.sh" apply v1.1.0 \
    > "$TMP/install-fail.out" 2> "$TMP/install-fail.err"
rc=$?
expect_exit "post-merge skill installation failure is explicit" 4 "$rc"
if [[ "$(git -C "$INSTALL_FAIL_CLIENT" rev-parse HEAD)" == "$V2_COMMIT" ]] \
        && grep -Fq 'PARTIAL UPDATE' "$TMP/install-fail.err" \
        && grep -Fq 'recovery' "$TMP/install-fail.err"; then
    ok "partial update reports exact recovery state"
else
    bad "partial update reports exact recovery state"
fi

printf '#!/usr/bin/env bash\nexit 1\n' > "$SOURCE/tests/test-qa-gate.sh"
chmod +x "$SOURCE/tests/test-qa-gate.sh"
printf 'v3-bad\n' > "$SOURCE/release-marker.txt"
git -C "$SOURCE" add tests/test-qa-gate.sh release-marker.txt
git -C "$SOURCE" commit -qm 'failing candidate fixture'
git -C "$SOURCE" tag v1.2.0
git -C "$SOURCE" push -q publish main --tags
BEFORE_FAILED_APPLY="$(git -C "$CLIENT" rev-parse HEAD)"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$SKILLS" \
    "$CLIENT/update.sh" apply v1.2.0 > "$TMP/failing.out" 2> "$TMP/failing.err"
rc=$?
expect_exit "candidate test failure blocks apply" 2 "$rc"
if [[ "$(git -C "$CLIENT" rev-parse HEAD)" == "$BEFORE_FAILED_APPLY" ]]; then ok "failed candidate leaves checkout unchanged"; else bad "failed candidate leaves checkout unchanged"; fi

cat > "$SOURCE/tests/test-qa-gate.sh" <<'STUB'
#!/usr/bin/env bash
echo "fixture qa suite passed"
STUB
printf '#!/usr/bin/env bash\nexit 1\n' > "$SOURCE/install.sh"
chmod +x "$SOURCE/install.sh" "$SOURCE/tests/test-qa-gate.sh"
git -C "$SOURCE" add install.sh tests/test-qa-gate.sh
git -C "$SOURCE" commit -qm 'failing install preflight fixture'
git -C "$SOURCE" tag v1.3.0
git -C "$SOURCE" push -q publish main --tags
BEFORE_INSTALL_PREFLIGHT="$(git -C "$CLIENT" rev-parse HEAD)"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$SKILLS" \
    "$CLIENT/update.sh" apply v1.3.0 > "$TMP/preflight.out" 2> "$TMP/preflight.err"
rc=$?
expect_exit "candidate install preflight blocks a broken installer" 2 "$rc"
if [[ "$(git -C "$CLIENT" rev-parse HEAD)" == "$BEFORE_INSTALL_PREFLIGHT" ]]; then ok "install preflight failure leaves checkout unchanged"; else bad "install preflight failure leaves checkout unchanged"; fi

git -C "$CLIENT" remote set-url origin 'https://user:credential-value@example.invalid/repo.git'
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/unexpected-origin.out" 2> "$TMP/unexpected-origin.err"
rc=$?
expect_exit "default updater refuses an unexpected origin" 2 "$rc"
if ! grep -Fq 'credential-value' "$TMP/unexpected-origin.err" \
        && grep -Fq 'agy compatibility:' "$TMP/unexpected-origin.out" \
        && grep -Fq 'codex compatibility:' "$TMP/unexpected-origin.out"; then
    ok "unexpected origin credentials are redacted while both tools are reported"
else
    bad "unexpected origin credentials are redacted while both tools are reported"
fi

MATRIX_TOOL="$ROOT/scripts/compatibility.py"
MATRIX="$ROOT/compat/agy-model-effort-matrix.json"
MATRIX_SCHEMA="$ROOT/compat/model-effort-matrix.schema.json"
python3 "$MATRIX_TOOL" validate-matrix --matrix "$MATRIX" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$ROOT/compat/agy-verified-version.txt" \
    --reviewed-revision-file "$ROOT/compat/agy-upstream-head.txt" \
    > "$TMP/candidate-matrix.out" 2> "$TMP/candidate-matrix.err"
rc=$?
expect_exit "candidate inventory validates but remains disabled" 3 "$rc"

ACTIVE_MATRIX="$TMP/active-matrix.json"
sed -e 's/"resolution_status": "disabled-unverified-source"/"resolution_status": "active"/' \
    -e "s/\"reviewed_source_revision\": null/\"reviewed_source_revision\": \"$UPSTREAM_HEAD\"/" \
    -e 's/"evidence": \["installed-agy-models"\]/"evidence": ["agy-models", "official-release", "official-source"]/' \
    "$MATRIX" > "$ACTIVE_MATRIX"
printf '1.1.10\n' > "$TMP/matrix-version.txt"
printf '%s\n' "$UPSTREAM_HEAD" > "$TMP/matrix-revision.txt"
python3 "$MATRIX_TOOL" validate-matrix --matrix "$ACTIVE_MATRIX" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/matrix-version.txt" \
    --reviewed-revision-file "$TMP/matrix-revision.txt" > "$TMP/active-matrix.out" 2> "$TMP/active-matrix.err"
rc=$?
expect_exit "active matrix is exactly version/source bound" 0 "$rc"

python3 "$MATRIX_TOOL" resolve-matrix --matrix "$ACTIVE_MATRIX" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/matrix-version.txt" \
    --reviewed-revision-file "$TMP/matrix-revision.txt" \
    --model gemini-3.6-flash --effort high > "$TMP/resolved.out" 2> "$TMP/resolved.err"
rc=$?
expect_exit "adjustable pair resolves to one exact compound slug" 0 "$rc"
if [[ "$(<"$TMP/resolved.out")" == "gemini-3.6-flash-high" ]]; then
    ok "matrix resolution preserves the advertised exact slug"
else
    bad "matrix resolution preserves the advertised exact slug"
fi

python3 "$MATRIX_TOOL" resolve-matrix --matrix "$ACTIVE_MATRIX" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/matrix-version.txt" \
    --reviewed-revision-file "$TMP/matrix-revision.txt" \
    --model gemini-3.1-pro --effort medium > "$TMP/pro-medium.out" 2> "$TMP/pro-medium.err"
rc=$?
expect_exit "Pro medium is explicitly unsupported" 64 "$rc"

python3 "$MATRIX_TOOL" resolve-matrix --matrix "$ACTIVE_MATRIX" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/matrix-version.txt" \
    --reviewed-revision-file "$TMP/matrix-revision.txt" \
    --model claude-sonnet-4-6 --effort high > "$TMP/fixed-model.out" 2> "$TMP/fixed-model.err"
rc=$?
expect_exit "fixed model rejects adjustable effort" 64 "$rc"

printf '9.9.9\n' > "$TMP/stale-version.txt"
python3 "$MATRIX_TOOL" resolve-matrix --matrix "$ACTIVE_MATRIX" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/stale-version.txt" \
    --reviewed-revision-file "$TMP/matrix-revision.txt" \
    --model gemini-3.6-flash --effort high > "$TMP/stale-matrix.out" 2> "$TMP/stale-matrix.err"
rc=$?
expect_exit "version-stale matrix cannot resolve" 3 "$rc"

printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' > "$TMP/mismatched-revision.txt"
python3 "$MATRIX_TOOL" resolve-matrix --matrix "$ACTIVE_MATRIX" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/matrix-version.txt" \
    --reviewed-revision-file "$TMP/mismatched-revision.txt" \
    --model gemini-3.6-flash --effort high > "$TMP/mismatch-matrix.out" 2> "$TMP/mismatch-matrix.err"
rc=$?
expect_exit "source-mismatched matrix cannot resolve" 3 "$rc"

awk 'NR == 2 { print "  \"schema_version\": 1," } { print }' "$ACTIVE_MATRIX" > "$TMP/duplicate-matrix.json"
python3 "$MATRIX_TOOL" validate-matrix --matrix "$TMP/duplicate-matrix.json" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/matrix-version.txt" \
    --reviewed-revision-file "$TMP/matrix-revision.txt" > "$TMP/duplicate-matrix.out" 2> "$TMP/duplicate-matrix.err"
rc=$?
expect_exit "duplicate matrix keys fail closed" 2 "$rc"

awk 'NR == 2 { print "  \"unknown_policy\": true," } { print }' "$ACTIVE_MATRIX" > "$TMP/unknown-matrix.json"
python3 "$MATRIX_TOOL" validate-matrix --matrix "$TMP/unknown-matrix.json" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/matrix-version.txt" \
    --reviewed-revision-file "$TMP/matrix-revision.txt" > "$TMP/unknown-matrix.out" 2> "$TMP/unknown-matrix.err"
rc=$?
expect_exit "unknown matrix keys fail closed" 2 "$rc"

printf '{ malformed\n' > "$TMP/malformed-matrix.json"
python3 "$MATRIX_TOOL" validate-matrix --matrix "$TMP/malformed-matrix.json" --schema "$MATRIX_SCHEMA" \
    --verified-version-file "$TMP/matrix-version.txt" \
    --reviewed-revision-file "$TMP/matrix-revision.txt" > "$TMP/malformed-matrix.out" 2> "$TMP/malformed-matrix.err"
rc=$?
expect_exit "malformed matrix JSON fails closed" 2 "$rc"

WORKFLOW="$ROOT/.github/workflows/compatibility-watch.yml"
if grep -Fq 'schedule:' "$WORKFLOW" && grep -Fq 'workflow_dispatch:' "$WORKFLOW" \
        && grep -Fq 'runs-on: macos-latest' "$WORKFLOW" \
        && grep -Fq 'contents: read' "$WORKFLOW" \
        && grep -Fq 'persist-credentials: false' "$WORKFLOW"; then
    ok "watch workflow has only the weekly/manual read-only platform contract"
else
    bad "watch workflow has only the weekly/manual read-only platform contract"
fi
if ! grep -Eq 'pull_request:|secrets\.|contents: write|issues: write|git (pull|commit|push)|gh |curl |wget |brew |npm |pip |update\.sh apply' "$WORKFLOW"; then
    ok "watch workflow has no install, secret, mutation, or GitHub-write path"
else
    bad "watch workflow has no install, secret, mutation, or GitHub-write path"
fi
if grep -Fq 'GITHUB_STEP_SUMMARY' "$WORKFLOW" && grep -Fq 'head -80' "$WORKFLOW" \
        && grep -Fq 'exit "$status"' "$WORKFLOW"; then
    ok "watch summary is bounded and preserves 0/3/2 status"
else
    bad "watch summary is bounded and preserves 0/3/2 status"
fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
