#!/usr/bin/env bash
# Offline privacy and explicit-confirmation tests for bug-report.sh.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
REPORT="$ROOT/bug-report.sh"
TMP="$(mktemp -d -t agyworker-report.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
expect_exit() {
    local name="$1" want="$2" got="$3"
    if [[ "$got" == "$want" ]]; then ok "$name (exit $got)"; else bad "$name (exit $got, wanted $want)"; fi
}

mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'FAKE'
#!/usr/bin/env bash
set -u
printf '%s\0' "$@" > "${FAKE_GH_ARGS:?}"
printf '%s' "${GH_HOST-unset}" > "${FAKE_GH_HOST:?}"
if [[ -n "${FAKE_ORIGINAL:-}" ]]; then
    printf 'changed after confirmation\n' > "$FAKE_ORIGINAL"
fi
cat > "${FAKE_GH_BODY:?}"
printf 'https://github.example.test/issues/1\n'
FAKE
chmod +x "$TMP/bin/gh"

echo "bug reporting offline test suite"
echo

DRAFT="$TMP/bug.md"
"$REPORT" draft --output "$DRAFT" \
    --title 'Gate token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456' \
    --component qa-gate \
    --summary 'The gate accepted a synthetic mismatch.' \
    --steps 'From /Users/example/private/repo run a synthetic case; inspect envelope.json and ```secret source```.' \
    --expected 'Reject without uploading logs.' \
    --actual 'Bearer abcdefghijklmnopqrstuvwxyz was present.' \
    > "$TMP/draft.out" 2> "$TMP/draft.err"
rc=$?
expect_exit "draft generation is local and successful" 0 "$rc"
if grep -Fq '<redacted-secret>' "$DRAFT" \
        && grep -Fq '<redacted-path>' "$DRAFT" \
        && grep -Fq '<redacted-artifact>' "$DRAFT" \
        && grep -Fq '<redacted-code-block>' "$DRAFT" \
        && ! grep -Fq 'ghp_' "$DRAFT" \
        && ! grep -Fq '/Users/example' "$DRAFT"; then
    ok "draft redacts secrets, paths, artifacts, and code blocks"
else
    bad "draft redacts secrets, paths, artifacts, and code blocks"
fi
if [[ ! -e "$TMP/gh.args" ]]; then ok "draft does not submit anything"; else bad "draft does not submit anything"; fi
if [[ "$(stat -f '%Lp' "$DRAFT" 2>/dev/null || stat -c '%a' "$DRAFT")" == "600" ]]; then
    ok "draft is atomically published with mode 0600"
else
    bad "draft is atomically published with mode 0600"
fi

MATRIX="$TMP/redaction-matrix.md"
"$REPORT" draft --output "$MATRIX" \
    --title 'Credential and path matrix' \
    --summary 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz' \
    --steps $'/root/company/private.py /Volumes/Company/private.txt \\\\server\\share\\private.txt stream.ndjson task.txt full-prompt.txt stderr.txt\n```\nprivate source without a closing fence' \
    --expected 'password="alpha beta gamma"' \
    --actual $'-----BEGIN PRIVATE KEY-----\nABCDEFSECRETBODY\n-----END PRIVATE KEY-----' \
    > "$TMP/matrix.out" 2> "$TMP/matrix.err"
rc=$?
expect_exit "privacy matrix draft is generated" 0 "$rc"
if ! grep -Eq 'abcdefghijklmnopqrstuvwxyz|beta gamma|ABCDEFSECRETBODY|/root/|/Volumes/|server\\share|private source|stream\.ndjson|task\.txt|full-prompt\.txt|stderr\.txt' "$MATRIX"; then
    ok "authorization, quoted secrets, PEM, paths, code, and artifacts are fully redacted"
else
    bad "authorization, quoted secrets, PEM, paths, code, and artifacts are fully redacted"
fi

SAFE_DRAFT="$TMP/safe-relative.md"
"$REPORT" draft --output "$SAFE_DRAFT" --title safe \
    --summary 'Synthetic fixture only.' --steps 'Run tests/test_gate.py with sample-input.json.' \
    --expected 'A synthetic pass.' --actual 'A synthetic failure.' \
    > "$TMP/safe.out" 2> "$TMP/safe.err"
rc=$?
expect_exit "safe relative synthetic text remains usable" 0 "$rc"
if grep -Fq 'tests/test_gate.py' "$SAFE_DRAFT"; then ok "safe relative path is preserved"; else bad "safe relative path is preserved"; fi

"$REPORT" preview "$DRAFT" > "$TMP/preview.out" 2> "$TMP/preview.err"
rc=$?
expect_exit "preview validates and prints exact body" 0 "$rc"
SHA="$(python3 - "$DRAFT" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
if grep -Fq "SHA256: $SHA" "$TMP/preview.out"; then ok "preview binds content to SHA256"; else bad "preview binds content to SHA256"; fi

PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$DRAFT" --confirm-sha "0000000000000000000000000000000000000000000000000000000000000000" \
    > "$TMP/wrong.out" 2> "$TMP/wrong.err"
rc=$?
expect_exit "wrong confirmation hash blocks submission" 65 "$rc"
if [[ ! -e "$TMP/gh.args" ]]; then ok "wrong hash never invokes gh"; else bad "wrong hash never invokes gh"; fi

cp "$DRAFT" "$TMP/reviewed-body.md"
PATH="$TMP/bin:$PATH" GH_HOST=attacker.example \
    FAKE_GH_ARGS="$TMP/gh.args" FAKE_GH_BODY="$TMP/gh.body" \
    FAKE_GH_HOST="$TMP/gh.host" FAKE_ORIGINAL="$DRAFT" \
    "$REPORT" submit "$DRAFT" --confirm-sha "$SHA" \
    > "$TMP/submit.out" 2> "$TMP/submit.err"
rc=$?
expect_exit "reviewed hash permits optional gh submission" 0 "$rc"
if python3 - "$TMP/gh.args" "$DRAFT" <<'PY'
import sys
args = [part.decode() for part in open(sys.argv[1], "rb").read().split(b"\0") if part]
expected = ["issue", "create", "--repo", "github.com/cagdasyurekli/codex-agy-worker"]
ok = args[:4] == expected and "--title" in args and "--body-file" in args
ok = ok and args[args.index("--body-file") + 1] == "-"
raise SystemExit(0 if ok else 1)
PY
then ok "gh is bound to github.com and reads confirmed stdin"; else bad "gh is bound to github.com and reads confirmed stdin"; fi
if cmp -s "$TMP/gh.body" "$TMP/reviewed-body.md" \
        && [[ "$(<"$TMP/gh.host")" == "unset" ]] \
        && [[ "$(<"$DRAFT")" == "changed after confirmation" ]]; then
    ok "submitted bytes are SHA-bound despite file mutation and hostile GH_HOST"
else
    bad "submitted bytes are SHA-bound despite file mutation and hostile GH_HOST"
fi
cp "$TMP/reviewed-body.md" "$DRAFT"

cp "$DRAFT" "$TMP/tampered.md"
printf '\npassword=supersecretvalue\n' >> "$TMP/tampered.md"
TAMPERED_SHA="$(python3 - "$TMP/tampered.md" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
rm -f "$TMP/gh.args"
PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$TMP/tampered.md" --confirm-sha "$TAMPERED_SHA" \
    > "$TMP/tampered.out" 2> "$TMP/tampered.err"
rc=$?
expect_exit "post-draft sensitive content fails validation" 65 "$rc"
if [[ ! -e "$TMP/gh.args" ]]; then ok "invalid draft never invokes gh"; else bad "invalid draft never invokes gh"; fi

"$REPORT" draft --output "$DRAFT" --title again --summary s --steps s --expected e --actual a \
    > "$TMP/overwrite.out" 2> "$TMP/overwrite.err"
rc=$?
expect_exit "draft generation refuses overwrite" 65 "$rc"

if grep -Fq 'I removed prompts, source code, envelopes, credentials, paths, and raw logs.' \
        "$ROOT/.github/ISSUE_TEMPLATE/bug_report.yml" \
        && grep -Fq 'Minimal synthetic reproduction' "$ROOT/.github/ISSUE_TEMPLATE/bug_report.yml"; then
    ok "bug issue form requires sanitized evidence"
else
    bad "bug issue form requires sanitized evidence"
fi
if grep -Fq 'Acceptance criteria' "$ROOT/.github/ISSUE_TEMPLATE/feature_request.yml" \
        && grep -Fq 'Security and privacy impact' "$ROOT/.github/ISSUE_TEMPLATE/feature_request.yml" \
        && grep -Fq 'I removed prompts, private source code, envelopes, credentials, paths, and raw logs.' "$ROOT/.github/ISSUE_TEMPLATE/feature_request.yml" \
        && grep -Fq 'blank_issues_enabled: false' "$ROOT/.github/ISSUE_TEMPLATE/config.yml"; then
    ok "feature form and chooser encode maintainer review boundaries"
else
    bad "feature form and chooser encode maintainer review boundaries"
fi

if python3 - "$ROOT/.github/ISSUE_TEMPLATE/bug_report.yml" \
        "$ROOT/.github/ISSUE_TEMPLATE/feature_request.yml" <<'PY'
import re
import sys

for filename in sys.argv[1:]:
    text = open(filename, encoding="utf-8").read()
    if "\t" in text:
        raise SystemExit(1)
    lines = text.splitlines()
    top = {line.split(":", 1)[0] for line in lines if line and not line.startswith(" ")}
    if not {"name", "description", "title", "body"}.issubset(top):
        raise SystemExit(1)
    identifiers = re.findall(r"^    id: ([A-Za-z0-9_-]+)$", text, re.MULTILINE)
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise SystemExit(1)
    types = re.findall(r"^  - type: ([A-Za-z0-9_-]+)$", text, re.MULTILINE)
    if not types or any(item not in {"markdown", "dropdown", "textarea", "input", "checkboxes"} for item in types):
        raise SystemExit(1)
    if text.count("required: true") < 2:
        raise SystemExit(1)
PY
then ok "issue forms pass dependency-free structural validation"; else bad "issue forms pass dependency-free structural validation"; fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
