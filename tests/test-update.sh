#!/usr/bin/env bash
# Offline updater tests using local Git repositories and release tags.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
TMP="$(mktemp -d -t agyworker-update-tests.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
REAL_PYTHON_REAL="$(command -v python3)"
export REAL_PYTHON_REAL

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
expect_exit() {
    local name="$1" want="$2" got="$3"
    if [[ "$got" == "$want" ]]; then ok "$name (exit $got)"; else bad "$name (exit $got, wanted $want)"; fi
}
snapshot_repo() {
    local repo="$1" output="$2"
    python3 - "$repo" "$output" <<'PY'
import hashlib
import json
import os
import subprocess
import sys

repo, output = sys.argv[1:]

def git(*args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout

paths = {
    item
    for item in git(
        "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--"
    ).split(b"\0")
    if item
}
files = []
for encoded in sorted(paths):
    relative = os.fsdecode(encoded)
    path = os.path.join(repo, relative)
    if os.path.islink(path):
        kind = "symlink"
        digest = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    elif os.path.isfile(path):
        kind = "file"
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
    elif os.path.isdir(path):
        kind = "directory"
        digest = ""
    else:
        kind = "missing"
        digest = ""
    files.append([relative, kind, digest])

state = {
    "head": git("rev-parse", "HEAD").decode("ascii").strip(),
    "head_ref": git("symbolic-ref", "-q", "HEAD").decode("utf-8").strip(),
    "refs_hex": git("for-each-ref", "--format=%(refname)%00%(objectname)").hex(),
    "index_hex": git("ls-files", "--stage", "-z", "--").hex(),
    "status_hex": git(
        "status", "--porcelain=v1", "-z", "--untracked-files=all"
    ).hex(),
    "tracked_and_untracked_bytes": files,
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(state, handle, sort_keys=True, separators=(",", ":"))
PY
}
expect_same_snapshot() {
    local name="$1" before="$2" after="$3"
    if cmp -s "$before" "$after"; then ok "$name"; else bad "$name"; fi
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
cp "$ROOT/scripts/official_distribution.py" "$SOURCE/scripts/"

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
cat > "$TMP/bin/python3" <<'STUB'
#!/usr/bin/env bash
set -u
case "${1:-}" in
  */scripts/official_distribution.py)
    if [[ $# -ne 1 ]] || ! "$REAL_PYTHON_REAL" -B -c '
import importlib.util
import sys
expected = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/"
    "manifests/darwin_arm64.json"
)
spec = importlib.util.spec_from_file_location("manifest_fixture", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit(1)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
raise SystemExit(0 if module.MANIFEST_URL == expected else 1)
' "$1"; then
      printf '%s\n' 'fake manifest launcher rejected production argv or fixed URL' >&2
      exit 70
    fi
    case "${FAKE_MANIFEST_RESULT:-unchanged}" in
      unchanged)
        printf '%s\n' '  distribution manifest: unchanged (1.1.9)'
        exit 0 ;;
      drift)
        printf '%s\n' '  distribution manifest: drift-review (official distribution 1.1.10; verified 1.1.9)'
        exit 3 ;;
      unavailable)
        printf '%s\n' '  distribution manifest: evidence-unavailable (network evidence unavailable)'
        exit 2 ;;
      invalid)
        printf '%s\n' 'https://example.invalid/credential-secret'
        printf '%s\n' 'raw exception credential-secret' >&2
        exit 0 ;;
      *) exit 70 ;;
    esac
    ;;
esac
exec "$REAL_PYTHON_REAL" "$@"
STUB
chmod +x "$SOURCE/"*.sh "$SOURCE/tests/"*.sh "$TMP/bin/agy" "$TMP/bin/codex" \
    "$TMP/bin/python3" "$SOURCE/scripts/compatibility.py" "$SOURCE/scripts/official_distribution.py"

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

snapshot_repo "$CLIENT" "$TMP/check-zero.before"
PATH="$TMP/bin:$PATH" \
    "$CLIENT/update.sh" check > "$TMP/check.out" 2> "$TMP/check.err"
rc=$?
snapshot_repo "$CLIENT" "$TMP/check-zero.after"
expect_exit "check reports without changing files" 0 "$rc"
if grep -Fq 'tool update: available v1.0.0 -> v1.1.0' "$TMP/check.out"; then
    ok "check identifies the latest stable release"
else
    bad "check identifies the latest stable release"
fi
expect_same_snapshot "exit 0 preserves HEAD, refs, index, and all worktree bytes/status" \
    "$TMP/check-zero.before" "$TMP/check-zero.after"
if grep -Fq 'agy compatibility:' "$TMP/check.out" \
        && grep -Fq 'codex compatibility:' "$TMP/check.out" \
        && grep -Fq 'distribution manifest: unchanged' "$TMP/check.out" \
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
    OFFICIAL_AGY_DISTRIBUTION_MANIFEST=https://example.invalid/credential \
    COMPATIBILITY_REVIEW_DAYS=9999 \
    "$CLIENT/update.sh" check > "$TMP/fixed-policy.out" 2> "$TMP/fixed-policy.err"
rc=$?
expect_exit "environment cannot override fixed sources or cadence" 0 "$rc"
if ! grep -Fq 'example.invalid' "$TMP/fixed-policy.out" "$TMP/fixed-policy.err"; then
    ok "ignored source overrides are never disclosed"
else
    bad "ignored source overrides are never disclosed"
fi

PATH="$TMP/bin:$PATH" "$CLIENT/update.sh" check \
    --manifest-url https://example.invalid/credential-secret \
    > "$TMP/manifest-cli-override.out" 2> "$TMP/manifest-cli-override.err"
rc=$?
expect_exit "CLI cannot override the fixed distribution manifest" 64 "$rc"
if ! grep -Fq 'example.invalid' "$TMP/manifest-cli-override.out" \
        "$TMP/manifest-cli-override.err"; then
    ok "rejected manifest override is not disclosed"
else
    bad "rejected manifest override is not disclosed"
fi

snapshot_repo "$CLIENT" "$TMP/manifest-three.before"
PATH="$TMP/bin:$PATH" FAKE_MANIFEST_RESULT=drift \
    "$CLIENT/update.sh" check > "$TMP/manifest-drift.out" 2> "$TMP/manifest-drift.err"
rc=$?
snapshot_repo "$CLIENT" "$TMP/manifest-three.after"
expect_exit "official distribution drift requires review without advancing baseline" 3 "$rc"
expect_same_snapshot "distribution drift preserves HEAD, refs, index, and all worktree bytes/status" \
    "$TMP/manifest-three.before" "$TMP/manifest-three.after"

snapshot_repo "$CLIENT" "$TMP/manifest-two.before"
PATH="$TMP/bin:$PATH" FAKE_MANIFEST_RESULT=unavailable FAKE_CODEX_VERSION=9.9.9 \
    "$CLIENT/update.sh" check > "$TMP/manifest-unavailable.out" \
    2> "$TMP/manifest-unavailable.err"
rc=$?
snapshot_repo "$CLIENT" "$TMP/manifest-two.after"
expect_exit "distribution evidence-unavailable outranks established tool drift" 2 "$rc"
expect_same_snapshot "distribution failure preserves HEAD, refs, index, and all worktree bytes/status" \
    "$TMP/manifest-two.before" "$TMP/manifest-two.after"

PATH="$TMP/bin:$PATH" FAKE_MANIFEST_RESULT=invalid \
    "$CLIENT/update.sh" check > "$TMP/manifest-invalid-helper.out" \
    2> "$TMP/manifest-invalid-helper.err"
rc=$?
expect_exit "unexpected manifest-helper result fails closed" 2 "$rc"
if grep -Fq 'invalid helper result' "$TMP/manifest-invalid-helper.out" \
        && ! grep -Eq 'example\.invalid|credential-secret|raw exception' \
            "$TMP/manifest-invalid-helper.out" "$TMP/manifest-invalid-helper.err"; then
    ok "manifest helper output and errors are sanitized"
else
    bad "manifest helper output and errors are sanitized"
fi

snapshot_repo "$CLIENT" "$TMP/check-three.before"
PATH="$TMP/bin:$PATH" FAKE_CODEX_VERSION=9.9.9 \
    "$CLIENT/update.sh" check > "$TMP/codex-version-drift.out" 2> "$TMP/codex-version-drift.err"
rc=$?
snapshot_repo "$CLIENT" "$TMP/check-three.after"
expect_exit "installed Codex version drift requires review" 3 "$rc"
expect_same_snapshot "exit 3 preserves HEAD, refs, index, and all worktree bytes/status" \
    "$TMP/check-three.before" "$TMP/check-three.after"

snapshot_repo "$CLIENT" "$TMP/check-two.before"
PATH="$TMP/bin:$PATH" FAKE_AGY_MODE=usage \
    "$CLIENT/update.sh" check > "$TMP/agy-usage.out" 2> "$TMP/agy-usage.err"
rc=$?
snapshot_repo "$CLIENT" "$TMP/check-two.after"
expect_exit "agy usage text is inconclusive, not version evidence" 2 "$rc"
expect_same_snapshot "exit 2 preserves HEAD, refs, index, and all worktree bytes/status" \
    "$TMP/check-two.before" "$TMP/check-two.after"

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
for required_tool in bash git dirname; do
    ln -s "$(command -v "$required_tool")" "$NO_AGY_BIN/$required_tool"
done
ln -s "$TMP/bin/python3" "$NO_AGY_BIN/python3"
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
expect_matrix_validation() {
    local name="$1" want="$2" matrix="$3" schema="$4" stem="$5"
    local version_file="${6:-$TMP/matrix-version.txt}"
    local revision_file="${7:-$TMP/matrix-revision.txt}" got
    python3 "$MATRIX_TOOL" validate-matrix --matrix "$matrix" --schema "$schema" \
        --verified-version-file "$version_file" \
        --reviewed-revision-file "$revision_file" \
        > "$TMP/$stem.out" 2> "$TMP/$stem.err"
    got=$?
    if [[ "$got" == "$want" ]] && ! grep -Fq 'Traceback' "$TMP/$stem.err"; then
        ok "$name (exit $got, controlled)"
    else
        bad "$name (exit $got, wanted $want without traceback)"
    fi
}
expect_matrix_resolution() {
    local name="$1" model="$2" effort="$3" expected="$4" stem="$5" got
    python3 "$MATRIX_TOOL" resolve-matrix --matrix "$ACTIVE_MATRIX" \
        --schema "$MATRIX_SCHEMA" \
        --verified-version-file "$TMP/matrix-version.txt" \
        --reviewed-revision-file "$TMP/matrix-revision.txt" \
        --model "$model" --effort "$effort" \
        > "$TMP/$stem.out" 2> "$TMP/$stem.err"
    got=$?
    if [[ "$got" == 0 && "$(<"$TMP/$stem.out")" == "$expected" ]] \
            && ! grep -Fq 'Traceback' "$TMP/$stem.err"; then
        ok "$name"
    else
        bad "$name (exit $got, expected exact $expected)"
    fi
}
expect_matrix_reject() {
    local name="$1" want="$2" matrix="$3" model="$4" effort="$5" stem="$6"
    local version_file="${7:-$TMP/matrix-version.txt}"
    local revision_file="${8:-$TMP/matrix-revision.txt}" got
    python3 "$MATRIX_TOOL" resolve-matrix --matrix "$matrix" \
        --schema "$MATRIX_SCHEMA" \
        --verified-version-file "$version_file" \
        --reviewed-revision-file "$revision_file" \
        --model "$model" --effort "$effort" \
        > "$TMP/$stem.out" 2> "$TMP/$stem.err"
    got=$?
    if [[ "$got" == "$want" && ! -s "$TMP/$stem.out" ]] \
            && ! grep -Fq 'Traceback' "$TMP/$stem.err"; then
        ok "$name (exit $got, no fallback)"
    else
        bad "$name (exit $got, wanted $want without output/traceback)"
    fi
}
make_json_variant() {
    local source="$1" output="$2" mode="$3"
    python3 - "$source" "$output" "$mode" <<'PY'
import copy
import json
import sys

source, output, mode = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    data = json.load(handle)

if mode == "schema-malformed-type":
    data["properties"]["schema_version"] = "not-an-object"
elif mode == "schema-unknown-nested":
    data["$defs"]["inventory"]["properties"]["evidence"]["unknown"] = True
elif mode == "schema-missing-node":
    del data["$defs"]["fixedModel"]["properties"]["classification"]
elif mode == "schema-changed-policy":
    data["$defs"]["adjustableModel"]["additionalProperties"] = True
elif mode == "matrix-duplicate-adjustable":
    data["adjustable_models"][1] = copy.deepcopy(data["adjustable_models"][0])
elif mode == "matrix-duplicate-fixed":
    data["fixed_models"][1] = copy.deepcopy(data["fixed_models"][0])
elif mode == "matrix-duplicate-output":
    data["adjustable_models"][1]["resolutions"]["low"] = data["adjustable_models"][0]["resolutions"]["low"]
elif mode == "matrix-unknown-model":
    data["adjustable_models"][0]["model"] = "gemini-inferred-flash"
elif mode == "matrix-missing-coverage":
    del data["adjustable_models"][0]["resolutions"]["medium"]
elif mode == "matrix-missing-output":
    data["adjustable_models"][0]["resolutions"]["medium"] = ""
elif mode == "matrix-inferred-output":
    data["adjustable_models"][0]["resolutions"]["high"] = "gemini-3.6-flash-thinking"
elif mode == "matrix-malformed-nested-type":
    data["adjustable_models"][2]["unsupported_efforts"] = [{}]
else:
    raise SystemExit(f"unknown fixture mode: {mode}")

with open(output, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY
}
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
expect_matrix_validation "canonical schema and active matrix are exactly bound" 0 \
    "$ACTIVE_MATRIX" "$MATRIX_SCHEMA" active-matrix

expect_matrix_resolution "3.6 Flash low resolves exactly" \
    gemini-3.6-flash low gemini-3.6-flash-low pair-36-low
expect_matrix_resolution "3.6 Flash medium resolves exactly" \
    gemini-3.6-flash medium gemini-3.6-flash-medium pair-36-medium
expect_matrix_resolution "3.6 Flash high resolves exactly" \
    gemini-3.6-flash high gemini-3.6-flash-high pair-36-high
expect_matrix_resolution "3.5 Flash low resolves exactly" \
    gemini-3.5-flash low gemini-3.5-flash-low pair-35-low
expect_matrix_resolution "3.5 Flash medium resolves exactly" \
    gemini-3.5-flash medium gemini-3.5-flash-medium pair-35-medium
expect_matrix_resolution "3.5 Flash high resolves exactly" \
    gemini-3.5-flash high gemini-3.5-flash-high pair-35-high
expect_matrix_resolution "3.1 Pro low resolves exactly" \
    gemini-3.1-pro low gemini-3.1-pro-low pair-pro-low
expect_matrix_resolution "3.1 Pro high resolves exactly" \
    gemini-3.1-pro high gemini-3.1-pro-high pair-pro-high

expect_matrix_reject "Pro medium is explicitly unsupported" 64 "$ACTIVE_MATRIX" \
    gemini-3.1-pro medium pro-medium

for fixed_entry in \
    "claude-sonnet-4-6:no-level" \
    "claude-opus-4-6-thinking:thinking-labelled" \
    "gpt-oss-120b-medium:effort-labelled"
do
    fixed_slug="${fixed_entry%%:*}"
    fixed_classification="${fixed_entry#*:}"
    if python3 - "$ACTIVE_MATRIX" "$fixed_slug" "$fixed_classification" <<'PY'
import json
import sys
matrix, slug, classification = sys.argv[1:]
with open(matrix, encoding="utf-8") as handle:
    rows = json.load(handle)["fixed_models"]
matches = [row for row in rows if row == {
    "model_slug": slug,
    "classification": classification,
}]
raise SystemExit(0 if len(matches) == 1 else 1)
PY
    then
        fixed_exact=0
    else
        fixed_exact=1
    fi
    python3 "$MATRIX_TOOL" resolve-matrix --matrix "$ACTIVE_MATRIX" \
        --schema "$MATRIX_SCHEMA" \
        --verified-version-file "$TMP/matrix-version.txt" \
        --reviewed-revision-file "$TMP/matrix-revision.txt" \
        --model "$fixed_slug" --effort high \
        > "$TMP/fixed-$fixed_slug.out" 2> "$TMP/fixed-$fixed_slug.err"
    rc=$?
    if [[ "$fixed_exact" == 0 && "$rc" == 64 \
            && ! -s "$TMP/fixed-$fixed_slug.out" ]] \
            && ! grep -Fq 'Traceback' "$TMP/fixed-$fixed_slug.err"; then
        ok "$fixed_slug is exact and non-adjustable"
    else
        bad "$fixed_slug is exact and non-adjustable"
    fi
done

expect_matrix_reject "unknown model input has no inferred fallback" 64 \
    "$ACTIVE_MATRIX" gemini-unknown high unknown-model-input
expect_matrix_reject "uppercase effort is not normalized" 64 \
    "$ACTIVE_MATRIX" gemini-3.6-flash HIGH effort-uppercase
expect_matrix_reject "padded effort is not normalized" 64 \
    "$ACTIVE_MATRIX" gemini-3.6-flash ' high' effort-padded
expect_matrix_reject "thinking-like effort is not inferred" 64 \
    "$ACTIVE_MATRIX" gemini-3.6-flash thinking-high effort-inferred
expect_matrix_reject "unknown effort has no fallback" 64 \
    "$ACTIVE_MATRIX" gemini-3.6-flash extreme effort-unknown

expect_matrix_reject "disabled candidate matrix cannot resolve" 3 "$MATRIX" \
    gemini-3.6-flash high disabled-matrix

printf '9.9.9\n' > "$TMP/stale-version.txt"
expect_matrix_reject "version-stale matrix cannot resolve" 3 "$ACTIVE_MATRIX" \
    gemini-3.6-flash high stale-matrix "$TMP/stale-version.txt"

printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' > "$TMP/mismatched-revision.txt"
expect_matrix_reject "source-mismatched matrix cannot resolve" 3 "$ACTIVE_MATRIX" \
    gemini-3.6-flash high mismatch-matrix \
    "$TMP/matrix-version.txt" "$TMP/mismatched-revision.txt"

for matrix_variant in \
    duplicate-adjustable \
    duplicate-fixed \
    duplicate-output \
    unknown-model \
    missing-coverage \
    missing-output \
    inferred-output \
    malformed-nested-type
do
    make_json_variant "$ACTIVE_MATRIX" "$TMP/$matrix_variant.json" \
        "matrix-$matrix_variant"
    expect_matrix_validation "$matrix_variant matrix policy fails closed" 2 \
        "$TMP/$matrix_variant.json" "$MATRIX_SCHEMA" "$matrix_variant"
done

awk 'NR == 2 { print "  \"schema_version\": 1," } { print }' "$ACTIVE_MATRIX" > "$TMP/duplicate-matrix.json"
expect_matrix_validation "duplicate matrix keys fail closed" 2 \
    "$TMP/duplicate-matrix.json" "$MATRIX_SCHEMA" duplicate-matrix

awk 'NR == 2 { print "  \"unknown_policy\": true," } { print }' "$ACTIVE_MATRIX" > "$TMP/unknown-matrix.json"
expect_matrix_validation "unknown matrix keys fail closed" 2 \
    "$TMP/unknown-matrix.json" "$MATRIX_SCHEMA" unknown-matrix

printf '{ malformed\n' > "$TMP/malformed-matrix.json"
expect_matrix_validation "malformed matrix JSON fails closed" 2 \
    "$TMP/malformed-matrix.json" "$MATRIX_SCHEMA" malformed-matrix

for schema_variant in malformed-type unknown-nested missing-node changed-policy
do
    make_json_variant "$MATRIX_SCHEMA" "$TMP/schema-$schema_variant.json" \
        "schema-$schema_variant"
    expect_matrix_validation "$schema_variant schema fails closed" 2 \
        "$ACTIVE_MATRIX" "$TMP/schema-$schema_variant.json" \
        "schema-$schema_variant"
done

awk 'NR == 2 { print "  \"$schema\": \"duplicate\"," } { print }' \
    "$MATRIX_SCHEMA" > "$TMP/schema-duplicate-key.json"
expect_matrix_validation "duplicate schema key fails closed" 2 \
    "$ACTIVE_MATRIX" "$TMP/schema-duplicate-key.json" schema-duplicate-key

MANIFEST_TEST_OUTPUT="$($REAL_PYTHON_REAL "$ROOT/tests/test-official-distribution.py" 2>&1)"
rc=$?
printf '%s\n' "$MANIFEST_TEST_OUTPUT"
MANIFEST_RESULT="$(printf '%s\n' "$MANIFEST_TEST_OUTPUT" | tail -1)"
if [[ "$rc" == 0 && "$MANIFEST_RESULT" == "MANIFEST_TEST_RESULT passed=64 failed=0" ]]; then
    pass=$((pass+64))
else
    bad "official distribution policy tests (expected 64 controlled passes)"
fi

WORKFLOW="$ROOT/.github/workflows/compatibility-watch.yml"
if grep -Fq 'schedule:' "$WORKFLOW" && grep -Fq 'workflow_dispatch:' "$WORKFLOW" \
        && grep -Fq 'runs-on: macos-latest' "$WORKFLOW" \
        && grep -Fq 'contents: read' "$WORKFLOW" \
        && grep -Fq 'persist-credentials: false' "$WORKFLOW"; then
    ok "watch workflow has only the weekly/manual read-only platform contract"
else
    bad "watch workflow has only the weekly/manual read-only platform contract"
fi
if ! grep -Eq 'pull_request:|secrets\.|contents: write|issues: write|git (pull|commit|push)|gh |curl |wget |brew |npm |pip |update\.sh apply|agy-worker|model-recommendation|ground-truth|agy --|codex ' "$WORKFLOW"; then
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
