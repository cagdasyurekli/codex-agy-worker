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
if [[ "$(python3 -c 'import os, stat, sys; print(f"{stat.S_IMODE(os.stat(sys.argv[1]).st_mode):03o}")' "$DRAFT")" == "600" ]]; then
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

IMPROVEMENT="$TMP/improvement.md"
"$REPORT" draft --kind improvement --output "$IMPROVEMENT" --title 'Preview feedback workflow' \
    --component reporting --summary 'Submitting useful feedback is too cumbersome.' \
    --problem 'Users need a short, sanitized improvement path.' \
    --proposal 'Offer a local draft that captures the requested outcome.' \
    --benefit 'Maintainers receive bounded, reviewable requests.' \
    > "$TMP/improvement.out" 2> "$TMP/improvement.err"
rc=$?
expect_exit "improvement draft has type-specific required fields" 0 "$rc"
if grep -Fq '# Improvement: Preview feedback workflow' "$IMPROVEMENT" \
        && grep -Fq '## Problem to solve' "$IMPROVEMENT" \
        && grep -Fq '## Proposed improvement' "$IMPROVEMENT" \
        && grep -Fq '## Expected benefit' "$IMPROVEMENT"; then
    ok "improvement draft renders bounded type-specific fields"
else
    bad "improvement draft renders bounded type-specific fields"
fi

"$REPORT" draft --kind improvement --output "$TMP/incomplete-improvement.md" --title incomplete \
    --summary s --problem p --proposal q \
    > "$TMP/incomplete-improvement.out" 2> "$TMP/incomplete-improvement.err"
rc=$?
expect_exit "improvement rejects a missing type-specific field" 65 "$rc"

SECURITY_DRAFT="$TMP/security-sensitive.md"
"$REPORT" draft --output "$SECURITY_DRAFT" --title 'Authentication bypass in synthetic mode' \
    --component qa-gate --summary 'A security vulnerability may affect a synthetic path.' \
    --steps 'Use a minimal synthetic reproduction.' --expected 'Access is denied.' \
    --actual 'The request is accepted.' > "$TMP/security-draft.out" 2> "$TMP/security-draft.err"
rc=$?
expect_exit "security-sensitive feedback remains a local draft" 0 "$rc"
if grep -Fq '## Security-sensitive route' "$SECURITY_DRAFT" \
        && grep -Fq 'security/advisories/new' "$SECURITY_DRAFT"; then
    ok "security-sensitive draft names only the private route"
else
    bad "security-sensitive draft names only the private route"
fi

EXPLICIT_SECURITY_DRAFT="$TMP/explicit-security.md"
"$REPORT" draft --kind security --output "$EXPLICIT_SECURITY_DRAFT" \
    --title 'Archive boundary concern' --component updater \
    --summary 'A suspected vulnerability needs private maintainer review.' \
    > "$TMP/explicit-security.out" 2> "$TMP/explicit-security.err"
rc=$?
expect_exit "explicit security kind creates a private-only local draft" 0 "$rc"
if grep -Fq '# Security: Archive boundary concern' "$EXPLICIT_SECURITY_DRAFT" \
        && grep -Fq '## Security-sensitive route' "$EXPLICIT_SECURITY_DRAFT" \
        && grep -Fq 'security/advisories/new' "$EXPLICIT_SECURITY_DRAFT"; then
    ok "security kind contains only the minimal private route"
else
    bad "security kind contains only the minimal private route"
fi
"$REPORT" draft --kind security --output "$TMP/security-too-detailed.md" \
    --title concern --component updater --summary 'Private review is needed.' \
    --steps 'Exploit details must not be collected here.' \
    > "$TMP/security-too-detailed.out" 2> "$TMP/security-too-detailed.err"
rc=$?
expect_exit "security kind rejects public-report detail fields" 65 "$rc"

"$REPORT" preview "$DRAFT" > "$TMP/preview.out" 2> "$TMP/preview.err"
rc=$?
expect_exit "preview validates and prints exact body" 0 "$rc"
SHA="$(python3 - "$DRAFT" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
if grep -Fq "SHA256: $SHA" "$TMP/preview.out"; then ok "preview binds content to SHA256"; else bad "preview binds content to SHA256"; fi

rm -f "$TMP/gh.args"
PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$DRAFT" --confirm-sha "$SHA" \
    > "$TMP/missing-public-safe.out" 2> "$TMP/missing-public-safe.err"
rc=$?
expect_exit "public submission requires a separate exact public-safety acknowledgement" 65 "$rc"
if [[ ! -e "$TMP/gh.args" ]]; then ok "missing public-safety acknowledgement never invokes gh"; else bad "missing public-safety acknowledgement never invokes gh"; fi

PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$DRAFT" --confirm-sha "$SHA" \
    --confirm-public-safe-sha "0000000000000000000000000000000000000000000000000000000000000000" \
    > "$TMP/wrong-public-safe.out" 2> "$TMP/wrong-public-safe.err"
rc=$?
expect_exit "wrong public-safety acknowledgement blocks submission" 65 "$rc"
if [[ ! -e "$TMP/gh.args" ]]; then ok "wrong public-safety acknowledgement never invokes gh"; else bad "wrong public-safety acknowledgement never invokes gh"; fi

MUTATED_AFTER_REVIEW="$TMP/mutated-after-review.md"
cp "$DRAFT" "$MUTATED_AFTER_REVIEW"
printf '\nBenign but unreviewed edit.\n' >> "$MUTATED_AFTER_REVIEW"
PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$MUTATED_AFTER_REVIEW" --confirm-sha "$SHA" \
    --confirm-public-safe-sha "$SHA" \
    > "$TMP/mutated-after-review.out" 2> "$TMP/mutated-after-review.err"
rc=$?
expect_exit "file mutation invalidates both review acknowledgements" 65 "$rc"
if [[ ! -e "$TMP/gh.args" ]]; then ok "mutated reviewed file never invokes gh"; else bad "mutated reviewed file never invokes gh"; fi

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
    --confirm-public-safe-sha "$SHA" \
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

rm -f "$TMP/gh.args"
SECURITY_SHA="$(python3 - "$SECURITY_DRAFT" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$SECURITY_DRAFT" --confirm-sha "$SECURITY_SHA" \
    --confirm-public-safe-sha "$SECURITY_SHA" \
    > "$TMP/security-submit.out" 2> "$TMP/security-submit.err"
rc=$?
expect_exit "security-sensitive feedback fails closed before public submission" 70 "$rc"
if [[ ! -e "$TMP/gh.args" ]] && grep -Fq 'security/advisories/new' "$TMP/security-submit.err"; then
    ok "security-sensitive feedback never invokes gh and names private route"
else
    bad "security-sensitive feedback never invokes gh and names private route"
fi

EXPLICIT_SECURITY_SHA="$(python3 - "$EXPLICIT_SECURITY_DRAFT" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$EXPLICIT_SECURITY_DRAFT" \
    --confirm-sha "$EXPLICIT_SECURITY_SHA" \
    --confirm-public-safe-sha "$EXPLICIT_SECURITY_SHA" \
    > "$TMP/explicit-security-submit.out" 2> "$TMP/explicit-security-submit.err"
rc=$?
expect_exit "security kind cannot be publicly submitted even with both hashes" 70 "$rc"
if [[ ! -e "$TMP/gh.args" ]]; then ok "security kind never invokes gh"; else bad "security kind never invokes gh"; fi

UNKNOWN_SECURITY="$TMP/unknown-security-class.md"
"$REPORT" draft --output "$UNKNOWN_SECURITY" \
    --title 'Archive and fetch boundary' --component updater \
    --summary 'Zip Slip and SSRF phrasing may describe a suspected flaw.' \
    --steps 'Use only a minimal synthetic fixture.' \
    --expected 'The boundary rejects the request.' --actual 'Review is required.' \
    > "$TMP/unknown-security-draft.out" 2> "$TMP/unknown-security-draft.err"
rc=$?
expect_exit "unclassified security phrasing can remain a local draft" 0 "$rc"
UNKNOWN_SECURITY_SHA="$(python3 - "$UNKNOWN_SECURITY" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$UNKNOWN_SECURITY" --confirm-sha "$UNKNOWN_SECURITY_SHA" \
    > "$TMP/unknown-security-submit.out" 2> "$TMP/unknown-security-submit.err"
rc=$?
if [[ "$rc" != "0" ]]; then ok "unknown vulnerability phrasing cannot submit without public-safety acknowledgement"; else bad "unknown vulnerability phrasing cannot submit without public-safety acknowledgement"; fi
if [[ ! -e "$TMP/gh.args" ]]; then ok "classifier blind spots never bypass explicit public review"; else bad "classifier blind spots never bypass explicit public review"; fi

PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$DRAFT" --confirm-sha "$SHA" --repo attacker/example \
    > "$TMP/wrong-repo.out" 2> "$TMP/wrong-repo.err"
rc=$?
expect_exit "non-canonical repository is rejected before gh" 65 "$rc"
if [[ ! -e "$TMP/gh.args" ]]; then ok "non-canonical repository never invokes gh"; else bad "non-canonical repository never invokes gh"; fi

cp "$DRAFT" "$TMP/late-security.md"
printf '\nPossible exploit impact needs private review.\n' >> "$TMP/late-security.md"
LATE_SECURITY_SHA="$(python3 - "$TMP/late-security.md" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
PATH="$TMP/bin:$PATH" FAKE_GH_ARGS="$TMP/gh.args" \
    "$REPORT" submit "$TMP/late-security.md" --confirm-sha "$LATE_SECURITY_SHA" \
    --confirm-public-safe-sha "$LATE_SECURITY_SHA" \
    > "$TMP/late-security.out" 2> "$TMP/late-security.err"
rc=$?
expect_exit "late security-sensitive content also blocks public submission" 70 "$rc"
if [[ ! -e "$TMP/gh.args" ]]; then ok "late security-sensitive content never invokes gh"; else bad "late security-sensitive content never invokes gh"; fi

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

if "$REPORT" submit --help > "$TMP/submit-help.out" 2> "$TMP/submit-help.err" \
        && grep -Fq -- '--confirm-public-safe-sha' "$TMP/submit-help.out" \
        && "$REPORT" draft --help > "$TMP/draft-help.out" 2> "$TMP/draft-help.err" \
        && grep -Fq 'security' "$TMP/draft-help.out"; then
    ok "CLI help exposes security drafts and the second public acknowledgement"
else
    bad "CLI help exposes security drafts and the second public acknowledgement"
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
