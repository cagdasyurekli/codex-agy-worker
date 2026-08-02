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
CLIENT="$TMP/client"
DIRTY_CLIENT="$TMP/dirty-client"
NO_TAG_CLIENT="$TMP/no-tag-client"
IGNORED_CLIENT="$TMP/ignored-client"
INSTALL_FAIL_CLIENT="$TMP/install-fail-client"
SKILLS="$TMP/skills"
OFFICIAL_TOOL_URL="https://github.com/cagdasyurekli/codex-agy-worker.git"
OFFICIAL_UPSTREAM_URL="https://github.com/google-antigravity/antigravity-cli.git"
mkdir -p "$SOURCE/skills" "$SOURCE/tests" "$SOURCE/compat" "$TMP/bin" "$SKILLS"
cp "$ROOT/update.sh" "$ROOT/install.sh" "$SOURCE/"
cp -R "$ROOT/skills/agy-worker" "$SOURCE/skills/agy-worker"
cp "$ROOT/compat/"*.txt "$SOURCE/compat/"

mkdir -p "$UPSTREAM_SOURCE"
git -C "$UPSTREAM_SOURCE" init -q -b main
git -C "$UPSTREAM_SOURCE" config user.email test@example.com
git -C "$UPSTREAM_SOURCE" config user.name test
printf 'reviewed upstream\n' > "$UPSTREAM_SOURCE/README.md"
git -C "$UPSTREAM_SOURCE" add README.md
git -C "$UPSTREAM_SOURCE" commit -qm 'reviewed upstream fixture'
UPSTREAM_HEAD="$(git -C "$UPSTREAM_SOURCE" rev-parse HEAD)"
git init -q --bare "$UPSTREAM_REMOTE"
git -C "$UPSTREAM_SOURCE" remote add publish "$UPSTREAM_REMOTE"
git -C "$UPSTREAM_SOURCE" push -q publish main
git --git-dir="$UPSTREAM_REMOTE" symbolic-ref HEAD refs/heads/main
printf '%s\n' "$UPSTREAM_HEAD" > "$SOURCE/compat/agy-upstream-head.txt"
python3 -c 'from datetime import date; print(date.today().isoformat())' \
    > "$SOURCE/compat/last-reviewed.txt"
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
printf '%s\n' "${FAKE_AGY_VERSION:-1.1.9}"
STUB
chmod +x "$SOURCE/"*.sh "$SOURCE/tests/"*.sh "$TMP/bin/agy"

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

PATH="$TMP/bin:$PATH" FAKE_AGY_VERSION=9.9.9 \
    "$CLIENT/update.sh" check > "$TMP/version-drift.out" 2> "$TMP/version-drift.err"
rc=$?
expect_exit "installed agy version drift requires review" 3 "$rc"

printf '2000-01-01\n' > "$CLIENT/compat/last-reviewed.txt"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/review-due.out" 2> "$TMP/review-due.err"
rc=$?
expect_exit "periodic compatibility review becomes due" 3 "$rc"
if grep -Fq 'REVIEW DUE' "$TMP/review-due.out"; then ok "review age is fixed at 30 days"; else bad "review age is fixed at 30 days"; fi
git -C "$CLIENT" checkout -q -- compat/last-reviewed.txt

printf '2999-01-01\n' > "$CLIENT/compat/last-reviewed.txt"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/future-review.out" 2> "$TMP/future-review.err"
rc=$?
expect_exit "future compatibility review date fails closed" 3 "$rc"
if grep -Fq 'invalid compatibility review metadata' "$TMP/future-review.err"; then ok "future review date is identified"; else bad "future review date is identified"; fi
git -C "$CLIENT" checkout -q -- compat/last-reviewed.txt

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
if ! grep -Fq 'credential-value' "$TMP/unexpected-origin.err"; then ok "unexpected origin credentials are not printed"; else bad "unexpected origin credentials are not printed"; fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
