#!/usr/bin/env bash
# Offline Codex package, skill-bundle, and landing-page contract tests.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
TMP="$(mktemp -d -t agyworker-packaging.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

echo "Codex distribution offline test suite"
echo

ci_workflow_contract() {
    python3 - "$1" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
required = (
    "      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n",
    "      - name: committed diff hygiene\n",
    "          AGY_WORKER_CI_EVENT_NAME: ${{ github.event_name }}\n",
    "          AGY_WORKER_CI_BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.before }}\n",
    "          AGY_WORKER_CI_HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}\n",
    "        run: ./scripts/ci-diff-check.sh\n",
)
assert all(text.count(item) == 1 for item in required)
assert "git fetch" not in text
assert "run: git diff --check\n" not in text
PY
}

ci_helper_contract() {
    python3 - "$1" "$2" <<'PY'
from pathlib import Path
import sys

shell = Path(sys.argv[1]).read_text(encoding="utf-8")
source = Path(sys.argv[2]).read_text(encoding="utf-8")
shell_required = (
    'exec /usr/bin/python3 -I -S -B "$script_dir/ci_diff_check.py"',
)
source_required = {
    'base + "..." + head, "--"': 1,
    '"merge-base", base, head, limit=128, overall_deadline=deadline': 1,
    'empty_tree + ".." + head, "--"': 1,
    'base + ".." + head, "--"': 1,
    '"--no-ext-diff", "--no-textconv"': 3,
    '"diff-tree",': 1,
    '"--raw",': 1,
    '"--full-index",': 1,
    '"--no-renames",': 1,
    '[GIT, "cat-file", "--batch"]': 1,
    '_check_head_blob(blob)': 1,
    'deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS': 1,
    'if output_seen > stdout_limit:': 1,
    'if len(stderr_buffer) > MAX_BATCH_STDERR_BYTES:': 1,
    'or stderr_buffer': 1,
}
assert all(shell.count(item) == 1 for item in shell_required)
assert all(source.count(item) == count for item, count in source_required.items())
assert "difflib" not in source
assert "SequenceMatcher" not in source
assert "git fetch" not in shell + source
assert "shell=True" not in source
assert source.count("subprocess.Popen(") == 2
assert '"cat-file", "-s"' not in source
assert '"cat-file", "blob"' not in source
PY
}

init_ci_repo() {
    mkdir "$1"
    git -C "$1" init -q
    git -C "$1" config user.name test
    git -C "$1" config user.email test@example.com
}

run_ci_check() {
    (
        cd "$1" || exit 1
        AGY_WORKER_CI_EVENT_NAME="$2" \
            AGY_WORKER_CI_BASE_SHA="$3" \
            AGY_WORKER_CI_HEAD_SHA="$4" \
            "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
    )
}

if ci_workflow_contract "$ROOT/.github/workflows/test.yml" \
        && ci_helper_contract "$ROOT/scripts/ci-diff-check.sh" \
            "$ROOT/scripts/ci_diff_check.py" \
        && [[ -x "$ROOT/scripts/ci-diff-check.sh" ]] \
        && [[ -x "$ROOT/scripts/ci_diff_check.py" ]]; then
    ok "CI verifies the exact committed event range with full checkout history"
else
    bad "CI verifies the exact committed event range with full checkout history"
fi

if /usr/bin/python3 -I -S -B "$ROOT/tests/test-ci-diff-check.py"; then
    ok "CI batch reader rejects malformed, unbounded, and interrupted streams"
else
    bad "CI batch reader rejects malformed, unbounded, and interrupted streams"
fi

cp "$ROOT/.github/workflows/test.yml" "$TMP/worktree-only.yml"
python3 - "$TMP/worktree-only.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "        run: ./scripts/ci-diff-check.sh\n"
assert text.count(old) == 1
path.write_text(text.replace(old, "        run: git diff --check\n"), encoding="utf-8")
PY
if ! ci_workflow_contract "$TMP/worktree-only.yml" 2>/dev/null; then
    ok "workflow policy rejects a worktree-only diff check"
else
    bad "workflow policy rejects a worktree-only diff check"
fi

cp "$ROOT/.github/workflows/test.yml" "$TMP/missing-diff-step.yml"
python3 - "$TMP/missing-diff-step.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "        run: ./scripts/ci-diff-check.sh\n"
assert text.count(old) == 1
path.write_text(text.replace(old, ""), encoding="utf-8")
PY
if ! ci_workflow_contract "$TMP/missing-diff-step.yml" 2>/dev/null; then
    ok "workflow policy rejects removal of the committed diff check"
else
    bad "workflow policy rejects removal of the committed diff check"
fi

mkdir "$TMP/ci-range-repo"
git -C "$TMP/ci-range-repo" init -q
git -C "$TMP/ci-range-repo" config user.name test
git -C "$TMP/ci-range-repo" config user.email test@example.com
printf 'base\n' > "$TMP/ci-range-repo/fixture.txt"
git -C "$TMP/ci-range-repo" add fixture.txt
git -C "$TMP/ci-range-repo" commit -qm base
ci_base="$(git -C "$TMP/ci-range-repo" rev-parse HEAD)"
printf 'good\n' > "$TMP/ci-range-repo/fixture.txt"
git -C "$TMP/ci-range-repo" add fixture.txt
git -C "$TMP/ci-range-repo" commit -qm good
ci_good="$(git -C "$TMP/ci-range-repo" rev-parse HEAD)"

(
    cd "$TMP/ci-range-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=pull_request \
        AGY_WORKER_CI_BASE_SHA="$ci_base" \
        AGY_WORKER_CI_HEAD_SHA="$ci_good" \
        "$ROOT/scripts/ci-diff-check.sh"
)
ci_good_rc=$?
if [[ "$ci_good_rc" == 0 ]]; then
    ok "committed PR range accepts a whitespace-clean patch"
else
    bad "committed PR range accepts a whitespace-clean patch"
fi

printf 'bad   \n' > "$TMP/ci-range-repo/fixture.txt"
git -C "$TMP/ci-range-repo" add fixture.txt
git -C "$TMP/ci-range-repo" commit -qm bad
ci_bad="$(git -C "$TMP/ci-range-repo" rev-parse HEAD)"
git -C "$TMP/ci-range-repo" diff --check >/dev/null 2>&1
plain_diff_rc=$?
(
    cd "$TMP/ci-range-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=pull_request \
        AGY_WORKER_CI_BASE_SHA="$ci_base" \
        AGY_WORKER_CI_HEAD_SHA="$ci_bad" \
        "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
)
ci_bad_pr_rc=$?
if [[ "$plain_diff_rc" == 0 && "$ci_bad_pr_rc" != 0 ]]; then
    ok "committed PR range catches whitespace hidden by a clean worktree"
else
    bad "committed PR range catches whitespace hidden by a clean worktree"
fi

git -C "$TMP/ci-range-repo" checkout -q -b attribute-clean "$ci_base"
printf 'fixture.txt -diff\n' > "$TMP/ci-range-repo/.gitattributes"
printf 'clean\n' > "$TMP/ci-range-repo/fixture.txt"
git -C "$TMP/ci-range-repo" add .gitattributes fixture.txt
git -C "$TMP/ci-range-repo" commit -qm attribute-clean
ci_attr_clean="$(git -C "$TMP/ci-range-repo" rev-parse HEAD)"
(
    cd "$TMP/ci-range-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=pull_request \
        AGY_WORKER_CI_BASE_SHA="$ci_base" \
        AGY_WORKER_CI_HEAD_SHA="$ci_attr_clean" \
        "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
)
ci_attr_clean_rc=$?
if [[ "$ci_attr_clean_rc" == 0 ]]; then
    ok "attribute-suppressed clean committed blobs are accepted"
else
    bad "attribute-suppressed clean committed blobs are accepted"
fi

printf 'bad   \n' > "$TMP/ci-range-repo/fixture.txt"
git -C "$TMP/ci-range-repo" add fixture.txt
git -C "$TMP/ci-range-repo" commit -qm attribute-bad
ci_attr_bad="$(git -C "$TMP/ci-range-repo" rev-parse HEAD)"
git -C "$TMP/ci-range-repo" diff --check "$ci_base...$ci_attr_bad" -- \
    >/dev/null 2>&1
attribute_git_rc=$?
(
    cd "$TMP/ci-range-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=pull_request \
        AGY_WORKER_CI_BASE_SHA="$ci_base" \
        AGY_WORKER_CI_HEAD_SHA="$ci_attr_bad" \
        "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
)
ci_attr_bad_rc=$?
if [[ "$attribute_git_rc" == 0 && "$ci_attr_bad_rc" != 0 ]]; then
    ok "raw committed-blob scan rejects a gitattributes diff suppression bypass"
else
    bad "raw committed-blob scan rejects a gitattributes diff suppression bypass"
fi

mkdir "$TMP/scanner-mutation"
cp "$ROOT/scripts/ci-diff-check.sh" "$TMP/scanner-mutation/ci-diff-check.sh"
cp "$ROOT/scripts/ci_diff_check.py" "$TMP/scanner-mutation/ci_diff_check.py"
python3 - "$TMP/scanner-mutation/ci_diff_check.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "        _check_head_blob(blob)\n"
assert text.count(old) == 1
path.write_text(text.replace(old, "        pass\n"), encoding="utf-8")
PY
chmod +x "$TMP/scanner-mutation/ci-diff-check.sh"
(
    cd "$TMP/ci-range-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=pull_request \
        AGY_WORKER_CI_BASE_SHA="$ci_base" \
        AGY_WORKER_CI_HEAD_SHA="$ci_attr_bad" \
        "$TMP/scanner-mutation/ci-diff-check.sh" >/dev/null 2>&1
)
scanner_mutation_rc=$?
if [[ "$scanner_mutation_rc" == 0 ]]; then
    ok "attribute bypass evidence kills raw committed-blob scanner removal"
else
    bad "attribute bypass evidence kills raw committed-blob scanner removal"
fi

cp "$ROOT/scripts/ci_diff_check.py" "$TMP/wrong-range.py"
python3 - "$TMP/wrong-range.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = 'base + "..." + head, "--"'
assert text.count(old) == 1
path.write_text(
    text.replace(old, 'head + "..." + base, "--"'),
    encoding="utf-8",
)
PY
if ! ci_helper_contract "$ROOT/scripts/ci-diff-check.sh" \
        "$TMP/wrong-range.py" 2>/dev/null; then
    ok "helper policy rejects a wrong-direction committed range"
else
    bad "helper policy rejects a wrong-direction committed range"
fi

cp "$ROOT/scripts/ci_diff_check.py" "$TMP/per-blob-process.py"
python3 - "$TMP/per-blob-process.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '[GIT, "cat-file", "--batch"]'
assert text.count(old) == 1
path.write_text(
    text.replace(old, '[GIT, "cat-file", "blob", requested[0]]'),
    encoding="utf-8",
)
PY
if ! ci_helper_contract "$ROOT/scripts/ci-diff-check.sh" \
        "$TMP/per-blob-process.py" 2>/dev/null; then
    ok "helper policy rejects restoration of per-blob Git subprocesses"
else
    bad "helper policy rejects restoration of per-blob Git subprocesses"
fi

cp "$ROOT/scripts/ci_diff_check.py" "$TMP/unbounded-batch.py"
python3 - "$TMP/unbounded-batch.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "if output_seen > stdout_limit:"
assert text.count(old) == 1
path.write_text(text.replace(old, "if False:"), encoding="utf-8")
PY
if ! ci_helper_contract "$ROOT/scripts/ci-diff-check.sh" \
        "$TMP/unbounded-batch.py" 2>/dev/null; then
    ok "helper policy rejects removal of the batch output bound"
else
    bad "helper policy rejects removal of the batch output bound"
fi

cp "$ROOT/scripts/ci_diff_check.py" "$TMP/ignored-batch-stderr.py"
python3 - "$TMP/ignored-batch-stderr.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "            or stderr_buffer\n"
assert text.count(old) == 1
path.write_text(text.replace(old, ""), encoding="utf-8")
PY
if ! ci_helper_contract "$ROOT/scripts/ci-diff-check.sh" \
        "$TMP/ignored-batch-stderr.py" 2>/dev/null; then
    ok "helper policy rejects ignoring bounded nonempty batch stderr"
else
    bad "helper policy rejects ignoring bounded nonempty batch stderr"
fi

(
    cd "$TMP/ci-range-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=push \
        AGY_WORKER_CI_BASE_SHA="$ci_good" \
        AGY_WORKER_CI_HEAD_SHA="$ci_bad" \
        "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
)
ci_bad_push_rc=$?
if [[ "$ci_bad_push_rc" != 0 ]]; then
    ok "committed push range rejects whitespace errors"
else
    bad "committed push range rejects whitespace errors"
fi

(
    cd "$TMP/ci-range-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=push \
        AGY_WORKER_CI_BASE_SHA="$ci_base" \
        AGY_WORKER_CI_HEAD_SHA="$ci_attr_clean" \
        "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
)
ci_clean_push_rc=$?
if [[ "$ci_clean_push_rc" == 0 ]]; then
    ok "committed noninitial push range accepts a clean patch"
else
    bad "committed noninitial push range accepts a clean patch"
fi

mkdir "$TMP/ci-initial-repo"
git -C "$TMP/ci-initial-repo" init -q
git -C "$TMP/ci-initial-repo" config user.name test
git -C "$TMP/ci-initial-repo" config user.email test@example.com
printf 'initial   \n' > "$TMP/ci-initial-repo/fixture.txt"
git -C "$TMP/ci-initial-repo" add fixture.txt
git -C "$TMP/ci-initial-repo" commit -qm initial
ci_initial="$(git -C "$TMP/ci-initial-repo" rev-parse HEAD)"
(
    cd "$TMP/ci-initial-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=push \
        AGY_WORKER_CI_BASE_SHA=0000000000000000000000000000000000000000 \
        AGY_WORKER_CI_HEAD_SHA="$ci_initial" \
        "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
)
ci_initial_rc=$?
if [[ "$ci_initial_rc" != 0 ]]; then
    ok "initial push checks the root commit against the empty tree"
else
    bad "initial push checks the root commit against the empty tree"
fi


mkdir "$TMP/ci-initial-clean-repo"
git -C "$TMP/ci-initial-clean-repo" init -q
git -C "$TMP/ci-initial-clean-repo" config user.name test
git -C "$TMP/ci-initial-clean-repo" config user.email test@example.com
printf 'initial\n' > "$TMP/ci-initial-clean-repo/fixture.txt"
git -C "$TMP/ci-initial-clean-repo" add fixture.txt
git -C "$TMP/ci-initial-clean-repo" commit -qm initial
ci_initial_clean="$(git -C "$TMP/ci-initial-clean-repo" rev-parse HEAD)"
(
    cd "$TMP/ci-initial-clean-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=push \
        AGY_WORKER_CI_BASE_SHA=0000000000000000000000000000000000000000 \
        AGY_WORKER_CI_HEAD_SHA="$ci_initial_clean" \
        "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
)
ci_initial_clean_rc=$?
if [[ "$ci_initial_clean_rc" == 0 ]]; then
    ok "initial push accepts a clean root commit"
else
    bad "initial push accepts a clean root commit"
fi

invalid_sha_rejected=1
for invalid_case in \
    "pull_request||$ci_good" \
    "pull_request|$ci_base|" \
    "pull_request|not-a-sha|$ci_good" \
    "pull_request|$ci_base|not-a-sha"; do
    IFS='|' read -r invalid_event invalid_base invalid_head <<EOF
$invalid_case
EOF
    (
        cd "$TMP/ci-range-repo" || exit 1
        AGY_WORKER_CI_EVENT_NAME="$invalid_event" \
            AGY_WORKER_CI_BASE_SHA="$invalid_base" \
            AGY_WORKER_CI_HEAD_SHA="$invalid_head" \
            "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
    )
    [[ "$?" != 0 ]] || invalid_sha_rejected=0
done
if [[ "$invalid_sha_rejected" == 1 ]]; then
    ok "missing and malformed event SHAs fail closed"
else
    bad "missing and malformed event SHAs fail closed"
fi

missing_object_rejected=1
for invalid_case in \
    "pull_request|1111111111111111111111111111111111111111|$ci_good" \
    "pull_request|$ci_base|2222222222222222222222222222222222222222"; do
    IFS='|' read -r invalid_event invalid_base invalid_head <<EOF
$invalid_case
EOF
    (
        cd "$TMP/ci-range-repo" || exit 1
        AGY_WORKER_CI_EVENT_NAME="$invalid_event" \
            AGY_WORKER_CI_BASE_SHA="$invalid_base" \
            AGY_WORKER_CI_HEAD_SHA="$invalid_head" \
            "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
    )
    [[ "$?" != 0 ]] || missing_object_rejected=0
done
if [[ "$missing_object_rejected" == 1 ]]; then
    ok "missing committed range objects fail closed"
else
    bad "missing committed range objects fail closed"
fi

(
    cd "$TMP/ci-range-repo" || exit 1
    AGY_WORKER_CI_EVENT_NAME=workflow_dispatch \
        AGY_WORKER_CI_BASE_SHA="$ci_base" \
        AGY_WORKER_CI_HEAD_SHA="$ci_good" \
        "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
)
unknown_event_rc=$?
if [[ "$unknown_event_rc" != 0 ]]; then
    ok "unknown CI event names fail closed"
else
    bad "unknown CI event names fail closed"
fi

zero_sha_rejected=1
for invalid_case in \
    "pull_request|0000000000000000000000000000000000000000|$ci_good" \
    "pull_request|$ci_base|0000000000000000000000000000000000000000" \
    "push|$ci_base|0000000000000000000000000000000000000000"; do
    IFS='|' read -r invalid_event invalid_base invalid_head <<EOF
$invalid_case
EOF
    (
        cd "$TMP/ci-range-repo" || exit 1
        AGY_WORKER_CI_EVENT_NAME="$invalid_event" \
            AGY_WORKER_CI_BASE_SHA="$invalid_base" \
            AGY_WORKER_CI_HEAD_SHA="$invalid_head" \
            "$ROOT/scripts/ci-diff-check.sh" >/dev/null 2>&1
    )
    [[ "$?" != 0 ]] || zero_sha_rejected=0
done
if [[ "$zero_sha_rejected" == 1 ]]; then
    ok "zero PR base and zero event heads fail closed"
else
    bad "zero PR base and zero event heads fail closed"
fi

init_ci_repo "$TMP/ci-binary-repo"
printf 'base\n' > "$TMP/ci-binary-repo/fixture.txt"
git -C "$TMP/ci-binary-repo" add fixture.txt
git -C "$TMP/ci-binary-repo" commit -qm base
ci_binary_base="$(git -C "$TMP/ci-binary-repo" rev-parse HEAD)"
python3 - "$TMP/ci-binary-repo/binary.dat" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_bytes(b"binary\x00payload\n")
PY
git -C "$TMP/ci-binary-repo" add binary.dat
git -C "$TMP/ci-binary-repo" commit -qm binary
ci_binary_head="$(git -C "$TMP/ci-binary-repo" rev-parse HEAD)"
if ! run_ci_check "$TMP/ci-binary-repo" pull_request \
        "$ci_binary_base" "$ci_binary_head"; then
    ok "binary committed additions fail closed"
else
    bad "binary committed additions fail closed"
fi

init_ci_repo "$TMP/ci-oversize-repo"
printf 'base\n' > "$TMP/ci-oversize-repo/fixture.txt"
git -C "$TMP/ci-oversize-repo" add fixture.txt
git -C "$TMP/ci-oversize-repo" commit -qm base
ci_oversize_base="$(git -C "$TMP/ci-oversize-repo" rev-parse HEAD)"
python3 - "$TMP/ci-oversize-repo/oversize.txt" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_bytes(b"x" * (2 * 1024 * 1024 + 1))
PY
git -C "$TMP/ci-oversize-repo" add oversize.txt
git -C "$TMP/ci-oversize-repo" commit -qm oversize
ci_oversize_head="$(git -C "$TMP/ci-oversize-repo" rev-parse HEAD)"
if ! run_ci_check "$TMP/ci-oversize-repo" pull_request \
        "$ci_oversize_base" "$ci_oversize_head"; then
    ok "oversized committed blobs fail closed"
else
    bad "oversized committed blobs fail closed"
fi

init_ci_repo "$TMP/ci-type-repo"
printf 'base\n' > "$TMP/ci-type-repo/fixture.txt"
git -C "$TMP/ci-type-repo" add fixture.txt
git -C "$TMP/ci-type-repo" commit -qm base
ci_type_base="$(git -C "$TMP/ci-type-repo" rev-parse HEAD)"
ln -s fixture.txt "$TMP/ci-type-repo/fixture-link"
git -C "$TMP/ci-type-repo" add fixture-link
git -C "$TMP/ci-type-repo" commit -qm symlink
ci_type_head="$(git -C "$TMP/ci-type-repo" rev-parse HEAD)"
if ! run_ci_check "$TMP/ci-type-repo" pull_request \
        "$ci_type_base" "$ci_type_head"; then
    ok "nonregular committed path types fail closed"
else
    bad "nonregular committed path types fail closed"
fi

git -C "$TMP/ci-type-repo" checkout -q -b gitlink "$ci_type_base"
git -C "$TMP/ci-type-repo" update-index --add \
    --cacheinfo "160000,$ci_type_base,nested"
git -C "$TMP/ci-type-repo" commit -qm gitlink
ci_gitlink_head="$(git -C "$TMP/ci-type-repo" rev-parse HEAD)"
if ! run_ci_check "$TMP/ci-type-repo" pull_request \
        "$ci_type_base" "$ci_gitlink_head"; then
    ok "committed gitlinks fail closed"
else
    bad "committed gitlinks fail closed"
fi

init_ci_repo "$TMP/ci-rename-repo"
printf 'clean\n' > "$TMP/ci-rename-repo/clean.txt"
printf 'bad   \n' > "$TMP/ci-rename-repo/bad.txt"
git -C "$TMP/ci-rename-repo" add clean.txt bad.txt
git -C "$TMP/ci-rename-repo" commit -qm base
ci_rename_base="$(git -C "$TMP/ci-rename-repo" rev-parse HEAD)"
git -C "$TMP/ci-rename-repo" mv clean.txt clean-renamed.txt
git -C "$TMP/ci-rename-repo" commit -qm clean-rename
ci_clean_rename="$(git -C "$TMP/ci-rename-repo" rev-parse HEAD)"
if run_ci_check "$TMP/ci-rename-repo" pull_request \
        "$ci_rename_base" "$ci_clean_rename"; then
    ok "clean committed renames are scanned and accepted"
else
    bad "clean committed renames are scanned and accepted"
fi
git -C "$TMP/ci-rename-repo" checkout -q -b bad-rename "$ci_rename_base"
git -C "$TMP/ci-rename-repo" mv bad.txt bad-renamed.txt
git -C "$TMP/ci-rename-repo" commit -qm bad-rename
ci_bad_rename="$(git -C "$TMP/ci-rename-repo" rev-parse HEAD)"
if ! run_ci_check "$TMP/ci-rename-repo" pull_request \
        "$ci_rename_base" "$ci_bad_rename"; then
    ok "renamed committed blobs are rescanned in full"
else
    bad "renamed committed blobs are rescanned in full"
fi

init_ci_repo "$TMP/ci-full-blob-repo"
printf 'legacy   \nbase\n' > "$TMP/ci-full-blob-repo/fixture.txt"
git -C "$TMP/ci-full-blob-repo" add fixture.txt
git -C "$TMP/ci-full-blob-repo" commit -qm base
ci_full_blob_base="$(git -C "$TMP/ci-full-blob-repo" rev-parse HEAD)"
printf 'legacy   \nchanged\n' > "$TMP/ci-full-blob-repo/fixture.txt"
git -C "$TMP/ci-full-blob-repo" add fixture.txt
git -C "$TMP/ci-full-blob-repo" commit -qm changed
ci_full_blob_head="$(git -C "$TMP/ci-full-blob-repo" rev-parse HEAD)"
if ! run_ci_check "$TMP/ci-full-blob-repo" pull_request \
        "$ci_full_blob_base" "$ci_full_blob_head"; then
    ok "changed files reject preexisting full-blob whitespace defects"
else
    bad "changed files reject preexisting full-blob whitespace defects"
fi

init_ci_repo "$TMP/ci-linear-lines-repo"
printf 'base\n' > "$TMP/ci-linear-lines-repo/base.txt"
git -C "$TMP/ci-linear-lines-repo" add base.txt
git -C "$TMP/ci-linear-lines-repo" commit -qm base
ci_linear_lines_base="$(git -C "$TMP/ci-linear-lines-repo" rev-parse HEAD)"
python3 - "$TMP/ci-linear-lines-repo/repeated.txt" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_bytes(b"same line\n" * 5_000)
PY
git -C "$TMP/ci-linear-lines-repo" add repeated.txt
git -C "$TMP/ci-linear-lines-repo" commit -qm repeated
ci_linear_lines_head="$(git -C "$TMP/ci-linear-lines-repo" rev-parse HEAD)"
if run_ci_check "$TMP/ci-linear-lines-repo" pull_request \
        "$ci_linear_lines_base" "$ci_linear_lines_head"; then
    ok "five thousand repeated lines complete under the linear scanner bound"
else
    bad "five thousand repeated lines complete under the linear scanner bound"
fi

init_ci_repo "$TMP/ci-max-paths-repo"
printf 'base\n' > "$TMP/ci-max-paths-repo/base.txt"
git -C "$TMP/ci-max-paths-repo" add base.txt
git -C "$TMP/ci-max-paths-repo" commit -qm base
ci_max_paths_base="$(git -C "$TMP/ci-max-paths-repo" rev-parse HEAD)"
path_index=0
while [[ "$path_index" -lt 1024 ]]; do
    printf 'clean\n' > "$TMP/ci-max-paths-repo/path-$path_index.txt"
    path_index=$((path_index + 1))
done
git -C "$TMP/ci-max-paths-repo" add .
git -C "$TMP/ci-max-paths-repo" commit -qm max-paths
ci_max_paths_head="$(git -C "$TMP/ci-max-paths-repo" rev-parse HEAD)"
ci_max_started=$SECONDS
if run_ci_check "$TMP/ci-max-paths-repo" pull_request \
        "$ci_max_paths_base" "$ci_max_paths_head" \
        && [[ "$((SECONDS - ci_max_started))" -lt 10 ]]; then
    ok "one thousand twenty-four small files complete under the path bound"
else
    bad "one thousand twenty-four small files complete under the path bound"
fi

if python3 - "$ROOT" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
manifest = json.loads((root / ".codex-plugin/plugin.json").read_text())
assert manifest["name"] == "codex-agy-worker"
assert manifest["version"] == "0.2.0"
assert manifest["skills"] == "./skills/"
assert manifest["license"] == "MIT"
assert manifest["interface"]["privacyPolicyURL"].startswith("https://")
assert manifest["interface"]["termsOfServiceURL"].startswith("https://")
assert not ({"apps", "mcpServers", "hooks"} & manifest.keys())
PY
then ok "Codex plugin is a skills-only package with public legal links"; else bad "Codex plugin is a skills-only package with public legal links"; fi

if [[ ! -e "$ROOT/.claude-plugin" ]] \
        && [[ ! -e "$ROOT/CLAUDE.md" ]] \
        && [[ ! -e "$ROOT/.agents/plugins/marketplace.json" ]] \
        && [[ ! -e "$ROOT/docs/MARKETPLACE.md" ]]; then
    ok "removed Claude and marketplace distribution surfaces stay absent"
else
    bad "removed Claude and marketplace distribution surfaces stay absent"
fi

if python3 - "$ROOT" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
skill = (root / "skills/agy-worker/SKILL.md").read_text()
metadata = (root / "skills/agy-worker/agents/openai.yaml").read_text()
assert skill.startswith("---\nname: agy-worker\ndescription:")
assert len(re.search(r"^description: (.+)$", skill, re.M).group(1)) <= 1024
assert 'display_name: "Verified agy Worker"' in metadata
assert "$agy-worker" in metadata
PY
then ok "canonical Agent Skill has matching OpenAI UI metadata"; else bad "canonical Agent Skill has matching OpenAI UI metadata"; fi

if ! grep -R -Fq '__REPO_ROOT__' "$ROOT/skills/agy-worker" \
        && ! grep -R -Fq '/Users/' "$ROOT/skills/agy-worker" \
        && [[ ! -e "$ROOT/skills/agy-worker/.pipeline-root" ]]; then
    ok "public skill bundle contains no checkout placeholder or local path marker"
else
    bad "public skill bundle contains no checkout placeholder or local path marker"
fi

if [[ -x "$ROOT/doctor.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/doctor.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/doctor-metadata.py" ]]; then
    ok "root and portable packages include the canonical read-only doctor"
else
    bad "root and portable packages include the canonical read-only doctor"
fi

if [[ -x "$ROOT/model-selection.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/model-selection.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/model_selection.py" ]]; then
    ok "root and portable packages include the canonical explicit selector"
else
    bad "root and portable packages include the canonical explicit selector"
fi

if [[ -x "$ROOT/verify-job.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/verify-job.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/evidence_receipt.py" ]] \
        && [[ -f "$ROOT/skills/agy-worker/runtime/schemas/evidence-receipt.schema.json" ]]; then
    ok "root and portable packages include Evidence Receipt v1"
else
    bad "root and portable packages include Evidence Receipt v1"
fi

required_runtime_dependencies=(
    agy-worker.sh
    qa-gate.sh
    verify-job.sh
    model-recommendation.sh
    model-selection.sh
    doctor.sh
    scripts/validate-envelope.py
    scripts/evidence_receipt.py
    scripts/model-recommendation.py
    scripts/model_selection.py
    scripts/compatibility.py
    scripts/doctor-metadata.py
    schemas/worker-result.schema.json
    schemas/evidence-receipt.schema.json
    schemas/model-selection.schema.json
    schemas/model-recommendation.schema.json
    agents/bulk-test-writer.md
    agents/repo-inventory.md
    agents/diff-reviewer.md
    compat/agy-verified-version.txt
    compat/agy-upstream-head.txt
    compat/agy-last-reviewed.txt
    compat/agy-model-effort-matrix.json
    compat/model-effort-matrix.schema.json
    compat/agy-model-effort-matrix.sha256
)
for dependency in "${required_runtime_dependencies[@]}"; do
    label="${dependency//\//-}"
    dependency_copy="$TMP/missing-$label"
    cp -R "$ROOT/skills/agy-worker" "$dependency_copy"
    rm -f "$dependency_copy/runtime/$dependency"
    PATH="$TMP/no-network-bin:$PATH" NETWORK_MARKER="$TMP/missing-$label.network" \
        bash "$dependency_copy/scripts/resolve-pipeline.sh" \
        > "$TMP/missing-$label.out" 2> "$TMP/missing-$label.err"
    rc=$?
    if [[ "$rc" == 2 && ! -s "$TMP/missing-$label.out" ]] \
            && grep -Fq 'complete agy-worker skill bundle' "$TMP/missing-$label.err" \
            && [[ ! -e "$TMP/missing-$label.network" ]]; then
        ok "resolver rejects a bundle missing $dependency"
    else
        bad "resolver rejects a bundle missing $dependency"
    fi
done

if python3 - "$ROOT" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
resolver = (root / "skills/agy-worker/scripts/resolve-pipeline.sh").read_text()
doctor = (root / "skills/agy-worker/runtime/doctor.sh").read_text()

def body(text: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}$", text, re.M | re.S)
    assert match is not None
    return match.group(1)

assert body(resolver, "pipeline_runtime_complete") == body(
    doctor, "doctor_runtime_complete"
)
assert "runtime-bundle.sh" not in resolver
assert "runtime-bundle.sh" not in doctor
assert not re.search(r"(?:^|[;&|()]\s*)(?:source|\.)\s+", resolver, re.M)
assert not re.search(r"(?:^|[;&|()]\s*)(?:source|\.)\s+", doctor, re.M)
PY
then ok "resolver and doctor use the same fixed non-sourced runtime predicate"; else bad "resolver and doctor use the same fixed non-sourced runtime predicate"; fi

real_parent_copy="$TMP/real-runtime-parents"
cp -R "$ROOT/skills/agy-worker" "$real_parent_copy"
real_parent_resolved="$(bash "$real_parent_copy/scripts/resolve-pipeline.sh" 2>/dev/null)"
if [[ "$real_parent_resolved" == "$(cd "$real_parent_copy/runtime" && pwd -P)" ]]; then
    ok "resolver accepts bundle-owned real runtime parent directories"
else
    bad "resolver accepts bundle-owned real runtime parent directories"
fi

for parent in scripts agents schemas compat; do
    for link_kind in absolute relative in-root; do
        parent_copy="$TMP/parent-$parent-$link_kind"
        foreign_parent="$TMP/foreign-$parent-$link_kind"
        cp -R "$ROOT/skills/agy-worker" "$parent_copy"
        if [[ "$link_kind" == in-root ]]; then
            mv "$parent_copy/runtime/$parent" \
                "$parent_copy/runtime/owned-$parent"
            ln -s "owned-$parent" "$parent_copy/runtime/$parent"
        else
            mv "$parent_copy/runtime/$parent" "$foreign_parent"
            if [[ "$link_kind" == absolute ]]; then
                ln -s "$foreign_parent" "$parent_copy/runtime/$parent"
            else
                ln -s "../../${foreign_parent##*/}" "$parent_copy/runtime/$parent"
            fi
        fi
        bash "$parent_copy/scripts/resolve-pipeline.sh" \
            > "$TMP/parent-$parent-$link_kind.out" \
            2> "$TMP/parent-$parent-$link_kind.err"
        rc=$?
        if [[ "$rc" == 2 \
                && ! -s "$TMP/parent-$parent-$link_kind.out" ]] \
                && grep -Fq 'complete agy-worker skill bundle' \
                    "$TMP/parent-$parent-$link_kind.err" \
                && ! grep -Fq "$TMP" "$TMP/parent-$parent-$link_kind.err"; then
            ok "resolver rejects $link_kind $parent parent symlink"
        else
            bad "resolver rejects $link_kind $parent parent symlink"
        fi
    done
done

for specification in \
    'qa-gate.sh:executable' \
    'verify-job.sh:executable' \
    'scripts/validate-envelope.py:executable' \
    'scripts/evidence_receipt.py:executable' \
    'scripts/model_selection.py:executable' \
    'schemas/worker-result.schema.json:data' \
    'schemas/evidence-receipt.schema.json:data' \
    'agents/repo-inventory.md:data' \
    'compat/agy-verified-version.txt:data' \
    'compat/agy-model-effort-matrix.json:data'; do
    dependency="${specification%:*}"
    dependency_class="${specification##*:}"
    for wrong_type in directory symlink-directory symlink-foreign fifo wrong-mode; do
        label="${dependency//\//-}-$wrong_type"
        dependency_copy="$TMP/wrong-$label"
        cp -R "$ROOT/skills/agy-worker" "$dependency_copy"
        dependency_path="$dependency_copy/runtime/$dependency"
        rm -f "$dependency_path"
        case "$wrong_type" in
            directory) mkdir "$dependency_path" ;;
            symlink-directory) ln -s "$TMP" "$dependency_path" ;;
            symlink-foreign) ln -s /dev/null "$dependency_path" ;;
            fifo) mkfifo "$dependency_path" ;;
            wrong-mode)
                cp "$ROOT/skills/agy-worker/runtime/$dependency" "$dependency_path"
                if [[ "$dependency_class" == executable ]]; then
                    chmod -x "$dependency_path"
                else
                    chmod +x "$dependency_path"
                fi
                ;;
        esac
        PATH="$TMP/no-network-bin:$PATH" NETWORK_MARKER="$TMP/wrong-$label.network" \
            bash "$dependency_copy/scripts/resolve-pipeline.sh" \
            > "$TMP/wrong-$label.out" 2> "$TMP/wrong-$label.err"
        rc=$?
        if [[ "$rc" == 2 && ! -s "$TMP/wrong-$label.out" ]] \
                && grep -Fq 'complete agy-worker skill bundle' "$TMP/wrong-$label.err" \
                && [[ ! -e "$TMP/wrong-$label.network" ]] \
                && ! grep -Fq "$TMP" "$TMP/wrong-$label.err"; then
            ok "resolver rejects $wrong_type for $dependency_class $dependency"
        else
            bad "resolver rejects $wrong_type for $dependency_class $dependency"
        fi
    done
done

for helper_mode in malformed side-effect exit stale; do
    helper_copy="$TMP/helper-$helper_mode"
    cp -R "$ROOT/skills/agy-worker" "$helper_copy"
    helper="$helper_copy/runtime/scripts/runtime-bundle.sh"
    case "$helper_mode" in
        malformed) printf 'if then impossible\n' > "$helper" ;;
        side-effect) printf ': > %q\n' "$TMP/helper-side-effect.marker" > "$helper" ;;
        exit) printf 'exit 91\n' > "$helper" ;;
        stale) printf 'pipeline_runtime_complete() { return 1; }\n' > "$helper" ;;
    esac
    resolved="$(bash "$helper_copy/scripts/resolve-pipeline.sh" 2> "$TMP/helper-$helper_mode.err")"
    rc=$?
    if [[ "$rc" == 0 \
            && "$resolved" == "$(cd "$helper_copy/runtime" && pwd -P)" \
            && ! -s "$TMP/helper-$helper_mode.err" \
            && ! -e "$TMP/helper-side-effect.marker" ]]; then
        ok "resolver ignores $helper_mode candidate runtime helper"
    else
        bad "resolver ignores $helper_mode candidate runtime helper"
    fi
done

if grep -Fq 'run: ./tests/test-doctor.sh' "$ROOT/.github/workflows/test.yml" \
        && grep -Fq 'runs-on: macos-latest' "$ROOT/.github/workflows/test.yml"; then
    ok "macOS CI runs the dedicated offline doctor suite"
else
    bad "macOS CI runs the dedicated offline doctor suite"
fi

python_cache_exists() {
    python3 -B - "$1" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
raise SystemExit(
    0 if any(
        path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}
        for path in root.rglob("*")
    ) else 1
)
PY
}

workflow_compile_fixture="$TMP/workflow-compile-fixture"
mkdir -p "$workflow_compile_fixture/scripts" \
    "$workflow_compile_fixture/skills/agy-worker/runtime/scripts"
cp "$ROOT/scripts/compatibility.py" "$workflow_compile_fixture/scripts/compatibility.py"
cp "$ROOT/skills/agy-worker/runtime/scripts/model_selection.py" \
    "$workflow_compile_fixture/skills/agy-worker/runtime/scripts/model_selection.py"
(
    cd "$workflow_compile_fixture" || exit 1
    PYTHONPYCACHEPREFIX="$TMP/workflow-python-cache" \
        python3 -m py_compile scripts/*.py skills/*/runtime/scripts/*.py
)
workflow_compile_rc=$?
python3 -B - "$ROOT/.github/workflows/test.yml" <<'PY'
from pathlib import Path
import re
import sys

workflow = Path(sys.argv[1]).read_text(encoding="utf-8")
step = re.compile(
    r"^      - name: Python syntax\n"
    r"        env:\n"
    r"          PYTHONPYCACHEPREFIX: \$\{\{ runner\.temp \}\}/codex-agy-worker-pycache\n"
    r"        run: python3 -m py_compile scripts/\*\.py skills/\*/runtime/scripts/\*\.py$",
    re.MULTILINE,
)
assert len(step.findall(workflow)) == 1
PY
workflow_contract_rc=$?
if [[ "$workflow_compile_rc" == 0 ]] \
        && [[ "$workflow_contract_rc" == 0 ]] \
        && ! python_cache_exists "$workflow_compile_fixture" \
        && python_cache_exists "$TMP/workflow-python-cache"; then
    ok "workflow Python syntax check keeps bytecode outside the public checkout"
else
    bad "workflow Python syntax check keeps bytecode outside the public checkout"
fi

plain_compile_fixture="$TMP/plain-compile-fixture"
mkdir -p "$plain_compile_fixture/scripts" \
    "$plain_compile_fixture/skills/agy-worker/runtime/scripts"
cp "$ROOT/scripts/compatibility.py" "$plain_compile_fixture/scripts/compatibility.py"
cp "$ROOT/skills/agy-worker/runtime/scripts/model_selection.py" \
    "$plain_compile_fixture/skills/agy-worker/runtime/scripts/model_selection.py"
(
    cd "$plain_compile_fixture" || exit 1
    unset PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX
    python3 -B -m py_compile scripts/*.py skills/*/runtime/scripts/*.py
)
plain_compile_rc=$?
if [[ "$plain_compile_rc" == 0 ]] \
        && python_cache_exists "$plain_compile_fixture/skills/agy-worker"; then
    ok "plain py_compile negative control is caught as a public bytecode leak"
else
    bad "plain py_compile negative control is caught as a public bytecode leak"
fi

if [[ -x "$ROOT/proof-demo.sh" ]] \
        && [[ -x "$ROOT/tests/test-proof-demo.sh" ]] \
        && [[ -f "$ROOT/demo/fixtures/honest-envelope.json" ]] \
        && [[ ! -L "$ROOT/demo/fixtures/honest-envelope.json" ]] \
        && [[ -f "$ROOT/demo/fixtures/scope-mismatch-envelope.json" ]] \
        && [[ ! -L "$ROOT/demo/fixtures/scope-mismatch-envelope.json" ]] \
        && [[ ! -e "$ROOT/skills/agy-worker/runtime/proof-demo.sh" ]]; then
    ok "repository package includes the repo-only starter proof and canonical fixtures"
else
    bad "repository package includes the repo-only starter proof and canonical fixtures"
fi

if grep -Fq 'run: ./tests/test-proof-demo.sh' "$ROOT/.github/workflows/test.yml"; then
    ok "macOS CI runs the dedicated offline starter-proof suite"
else
    bad "macOS CI runs the dedicated offline starter-proof suite"
fi

if grep -Fq 'run: ./tests/test-evidence-receipt.sh' "$ROOT/.github/workflows/test.yml"; then
    ok "macOS CI runs the dedicated Evidence Receipt v1 suite"
else
    bad "macOS CI runs the dedicated Evidence Receipt v1 suite"
fi

if grep -Fq './proof-demo.sh' "$ROOT/README.md" \
        && grep -Fq 'starter proof' "$ROOT/README.md" \
        && grep -Fq 'proof-demo.sh' "$ROOT/docs/index.md" \
        && grep -Fq 'not human review' "$ROOT/docs/index.md"; then
    ok "public documentation links the bounded starter proof without claiming acceptance"
else
    bad "public documentation links the bounded starter proof without claiming acceptance"
fi

if grep -Fq 'gate="$script_dir/qa-gate.sh"' "$ROOT/proof-demo.sh" \
        && ! grep -Eq 'PROOF_GATE|--gate([=[:space:]]|$)' "$ROOT/proof-demo.sh" \
        && grep -Fq 'honest: gate-passed (exit 0)' "$ROOT/proof-demo.sh" \
        && grep -Fq 'mismatch: rejected (exit 10)' "$ROOT/proof-demo.sh" \
        && grep -Fq 'starter proof only; no candidate accepted because no human review occurred' \
            "$ROOT/proof-demo.sh"; then
    ok "starter proof fixes the maintained gate and its bounded success contract"
else
    bad "starter proof fixes the maintained gate and its bounded success contract"
fi

if cmp -s "$ROOT/compat/agy-verified-version.txt" \
        "$ROOT/skills/agy-worker/runtime/compat/agy-verified-version.txt" \
        && cmp -s "$ROOT/compat/agy-upstream-head.txt" \
        "$ROOT/skills/agy-worker/runtime/compat/agy-upstream-head.txt" \
        && cmp -s "$ROOT/compat/agy-last-reviewed.txt" \
        "$ROOT/skills/agy-worker/runtime/compat/agy-last-reviewed.txt" \
        && [[ "$(<"$ROOT/compat/agy-verified-version.txt")" == "1.1.10" ]] \
        && [[ "$(<"$ROOT/compat/agy-last-reviewed.txt")" == "2026-08-05" ]]; then
    ok "portable doctor metadata is byte-synchronized with canonical compatibility records"
else
    bad "portable doctor metadata is byte-synchronized with canonical compatibility records"
fi

if cmp -s "$ROOT/compat/agy-model-effort-matrix.json" \
        "$ROOT/skills/agy-worker/runtime/compat/agy-model-effort-matrix.json" \
        && cmp -s "$ROOT/compat/model-effort-matrix.schema.json" \
            "$ROOT/skills/agy-worker/runtime/compat/model-effort-matrix.schema.json" \
        && cmp -s "$ROOT/compat/agy-model-effort-matrix.sha256" \
            "$ROOT/skills/agy-worker/runtime/compat/agy-model-effort-matrix.sha256" \
        && python3 - "$ROOT/compat/agy-model-effort-matrix.json" \
            "$ROOT/compat/agy-model-effort-matrix.sha256" <<'PY'
import hashlib
import sys

actual = hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()
expected = open(sys.argv[2], encoding="ascii").read().strip()
assert actual == expected
PY
then
    ok "portable resolver matrix, schema, and exact SHA are byte-synchronized"
else
    bad "portable resolver matrix, schema, and exact SHA are byte-synchronized"
fi

if python3 -B - "$ROOT/skills/agy-worker/runtime/schemas/model-selection.schema.json" <<'PY'
import copy
import json
import sys

schema = json.load(open(sys.argv[1], encoding="utf-8"))

required = {
    "legacy tier selection": {
        "schema_version", "kind", "selection_mode", "selected_tier",
        "selected_tier_source", "resolved_agy_model",
    },
    "exact reviewed model selection": {
        "schema_version", "kind", "selection_mode", "user_model",
        "user_model_source", "resolved_agy_model", "installed_agy_version",
        "matrix_sha256", "matrix_agy_version", "matrix_source_revision",
    },
    "reviewed model and effort selection": {
        "schema_version", "kind", "selection_mode", "user_model",
        "user_model_source", "user_effort", "user_effort_source",
        "resolved_agy_model", "installed_agy_version", "matrix_sha256",
        "matrix_agy_version", "matrix_source_revision",
    },
}
forbidden = {
    "legacy tier selection": {
        "user_model", "user_model_source", "user_effort", "user_effort_source",
        "installed_agy_version", "matrix_sha256", "matrix_agy_version",
        "matrix_source_revision",
    },
    "exact reviewed model selection": {
        "selected_tier", "selected_tier_source", "user_effort", "user_effort_source",
    },
    "reviewed model and effort selection": {"selected_tier", "selected_tier_source"},
}

def assert_strict(value):
    assert value["additionalProperties"] is False
    assert set(value["required"]) == {"schema_version", "kind", "selection_mode"}
    variants = {variant["title"]: variant for variant in value["oneOf"]}
    assert set(variants) == set(required)
    for title, expected in required.items():
        variant = variants[title]
        assert set(variant["required"]) == expected
        blocked = {
            tuple(rule["required"])[0]
            for rule in variant["not"]["anyOf"]
            if len(rule.get("required", [])) == 1
        }
        assert blocked == forbidden[title]
    tier_conditions = json.dumps(variants["legacy tier selection"]["allOf"], sort_keys=True)
    assert '"const": "default"' in tier_conditions and '"type": "null"' in tier_conditions
    assert '"const": "implicit-default"' in tier_conditions and '"const": "bulk"' in tier_conditions

assert_strict(schema)
mutants = []
mutant = copy.deepcopy(schema)
mutant["additionalProperties"] = True
mutants.append(mutant)
mutant = copy.deepcopy(schema)
mutant["oneOf"][1]["required"].remove("matrix_sha256")
mutants.append(mutant)
mutant = copy.deepcopy(schema)
mutant["oneOf"][0]["not"]["anyOf"] = mutant["oneOf"][0]["not"]["anyOf"][1:]
mutants.append(mutant)
mutant = copy.deepcopy(schema)
mutant["oneOf"][0]["allOf"] = mutant["oneOf"][0]["allOf"][:1]
mutants.append(mutant)
for mutant in mutants:
    try:
        assert_strict(mutant)
    except (AssertionError, KeyError, TypeError):
        continue
    raise AssertionError("weakened selection schema mutant was accepted")
PY
then
    ok "selection schema contract rejects required, forbidden, conditional, and extra-field weakening"
else
    bad "selection schema contract rejects required, forbidden, conditional, and extra-field weakening"
fi

TAMPERED_PORTABLE="$TMP/tampered-portable-metadata"
cp -R "$ROOT/skills/agy-worker" "$TAMPERED_PORTABLE"
printf '9.9.9\n' > "$TAMPERED_PORTABLE/runtime/compat/agy-verified-version.txt"
if ! cmp -s "$ROOT/compat/agy-verified-version.txt" \
        "$TAMPERED_PORTABLE/runtime/compat/agy-verified-version.txt"; then
    ok "portable metadata tampering breaks canonical byte identity"
else
    bad "portable metadata tampering breaks canonical byte identity"
fi

printf '%040d\n' 0 > "$TAMPERED_PORTABLE/runtime/compat/agy-upstream-head.txt"
if ! cmp -s "$ROOT/compat/agy-upstream-head.txt" \
        "$TAMPERED_PORTABLE/runtime/compat/agy-upstream-head.txt"; then
    ok "portable source-revision tampering breaks canonical byte identity"
else
    bad "portable source-revision tampering breaks canonical byte identity"
fi

resolved="$(bash "$ROOT/skills/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
if [[ "$resolved" == "$(cd "$ROOT" && pwd -P)" ]]; then
    ok "Codex package resolver finds the adjacent canonical runtime"
else
    bad "Codex package resolver finds the adjacent canonical runtime"
fi

mkdir -p "$TMP/legacy-claude-only/.claude-plugin" \
    "$TMP/legacy-claude-only/skills"
cp "$ROOT/agy-worker.sh" "$ROOT/qa-gate.sh" \
    "$ROOT/model-recommendation.sh" "$TMP/legacy-claude-only/"
cp -R "$ROOT/skills/agy-worker" "$TMP/legacy-claude-only/skills/agy-worker"
printf '{}\n' > "$TMP/legacy-claude-only/.claude-plugin/plugin.json"
legacy_resolved="$(bash "$TMP/legacy-claude-only/skills/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
if [[ "$legacy_resolved" == "$(cd "$TMP/legacy-claude-only/skills/agy-worker/runtime" && pwd -P)" ]]; then
    ok "resolver ignores a removed Claude-only package marker"
else
    bad "resolver ignores a removed Claude-only package marker"
fi

mkdir -p "$TMP/skill-folder-copy" "$TMP/no-network-bin"
cp -R "$ROOT/skills/agy-worker" "$TMP/skill-folder-copy/agy-worker"
for command_name in agy curl wget git npm npx; do
    printf '#!/usr/bin/env bash\n: > "$NETWORK_MARKER"\nexit 99\n' \
        > "$TMP/no-network-bin/$command_name"
    chmod +x "$TMP/no-network-bin/$command_name"
done
copied_pipeline="$(PATH="$TMP/no-network-bin:$PATH" \
    NETWORK_MARKER="$TMP/network-called" \
    bash "$TMP/skill-folder-copy/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
PATH="$TMP/no-network-bin:$PATH" NETWORK_MARKER="$TMP/network-called" \
    "$copied_pipeline/model-recommendation.sh" --stage pre-dispatch \
    --selected-tier cheap --evidence bounded-routine \
    > "$TMP/copied-recommendation.json" 2> "$TMP/copied-recommendation.err"
rc=$?
if [[ "$rc" == "0" ]] \
        && [[ "$copied_pipeline" == "$(cd "$TMP/skill-folder-copy/agy-worker/runtime" && pwd -P)" ]] \
        && grep -Fq '"recommendation_only": true' "$TMP/copied-recommendation.json" \
        && grep -Fq '"applied": false' "$TMP/copied-recommendation.json" \
        && [[ ! -e "$TMP/network-called" ]]; then
    ok "skill-folder-only copy resolves and runs a bounded offline advisory"
else
    bad "skill-folder-only copy resolves and runs a bounded offline advisory"
fi

mkdir -p "$TMP/portable-receipt-repo" "$TMP/portable-receipts" \
    "$TMP/receipt-no-network-bin"
chmod 700 "$TMP/portable-receipts"
for command_name in agy curl wget npm npx; do
    printf '#!/usr/bin/env bash\n: > "$NETWORK_MARKER"\nexit 99\n' \
        > "$TMP/receipt-no-network-bin/$command_name"
    chmod +x "$TMP/receipt-no-network-bin/$command_name"
done
git -C "$TMP/portable-receipt-repo" init -q
git -C "$TMP/portable-receipt-repo" config user.email test@example.com
git -C "$TMP/portable-receipt-repo" config user.name test
printf 'before\n' > "$TMP/portable-receipt-repo/a.txt"
git -C "$TMP/portable-receipt-repo" add a.txt
git -C "$TMP/portable-receipt-repo" commit -qm init
portable_receipt_base="$(git -C "$TMP/portable-receipt-repo" rev-parse HEAD)"
printf 'after\n' > "$TMP/portable-receipt-repo/a.txt"
printf '%s\n' '{"status":"completed","summary":"done","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}' \
    > "$TMP/portable-envelope.json"
portable_receipt_parent="$(cd "$TMP/portable-receipts" && pwd -P)"
PATH="$TMP/receipt-no-network-bin:$PATH" NETWORK_MARKER="$TMP/receipt-network-called" \
    "$copied_pipeline/verify-job.sh" \
    --receipt "$portable_receipt_parent/receipt.json" \
    --envelope "$TMP/portable-envelope.json" \
    --repo "$TMP/portable-receipt-repo" --base "$portable_receipt_base" \
    --only a.txt --expect-edits --verify true \
    > "$TMP/portable-receipt.out" 2> "$TMP/portable-receipt.err"
portable_receipt_rc=$?
if [[ "$portable_receipt_rc" == 0 && ! -e "$TMP/receipt-network-called" ]] \
        && python3 -B - "$portable_receipt_parent/receipt.json" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["gate_exit"] == 0
assert value["verdict"] == "gate-passed"
assert value["gate_authority"] == "qa-gate"
assert value["integrity"]["signed"] is False
PY
then
    ok "skill-folder-only copy publishes a bounded receipt offline"
else
    bad "skill-folder-only copy publishes a bounded receipt offline"
fi

mkdir -p "$TMP/selector-bin"
printf '%s\n' '#!/usr/bin/env bash' \
    '[[ "$*" == "--version" ]] || exit 97' \
    'printf "1.1.10\n"' > "$TMP/selector-bin/agy"
chmod +x "$TMP/selector-bin/agy"
PATH="$TMP/selector-bin:$PATH" NETWORK_MARKER="$TMP/network-called" \
    "$copied_pipeline/model-selection.sh" --model gemini-3.6-flash --effort high \
    > "$TMP/copied-selection.json" 2> "$TMP/copied-selection.err"
rc=$?
if [[ "$rc" == 0 ]] \
        && grep -Fq '"resolved_agy_model": "gemini-3.6-flash-high"' \
            "$TMP/copied-selection.json" \
        && grep -Fq '"matrix_sha256": "6ba67d662821b6c0888ea2a4665aadcb02b82084e2f607bc43d4f2ee334639d8"' \
            "$TMP/copied-selection.json" \
        && [[ ! -e "$TMP/network-called" ]]; then
    ok "skill-folder-only copy resolves an exact direct selector offline"
else
    bad "skill-folder-only copy resolves an exact direct selector offline"
fi

(
    unset PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX
    PATH="$TMP/selector-bin:$PATH" \
        "$copied_pipeline/model-selection.sh" \
        --model gemini-3.6-flash --effort high \
        > "$TMP/no-spill-selection.json" 2> "$TMP/no-spill-selection.err" \
    && "$copied_pipeline/model-recommendation.sh" \
        --stage pre-dispatch --selected-model gemini-3.6-flash \
        --selected-effort high --evidence bounded-routine \
        > "$TMP/no-spill-recommendation.json" 2> "$TMP/no-spill-recommendation.err"
)
no_spill_rc=$?
if [[ "$no_spill_rc" == 0 ]] \
        && python3 -B - "$copied_pipeline" "$ROOT/skills/agy-worker" <<'PY'
from pathlib import Path
import sys

for root_text in sys.argv[1:]:
    root = Path(root_text)
    leaked = [path for path in root.rglob("*") if path.name == "__pycache__" or path.suffix == ".pyc"]
    assert not leaked, leaked
PY
then
    ok "normal direct selector and recommendation runs leave no bytecode in public bundles"
else
    bad "normal direct selector and recommendation runs leave no bytecode in public bundles"
fi

mkdir -p "$TMP/incomplete-skill/agy-worker/agents" \
    "$TMP/incomplete-skill/agy-worker/scripts"
cp "$ROOT/skills/agy-worker/SKILL.md" "$TMP/incomplete-skill/agy-worker/SKILL.md"
cp "$ROOT/skills/agy-worker/agents/openai.yaml" \
    "$TMP/incomplete-skill/agy-worker/agents/openai.yaml"
cp "$ROOT/skills/agy-worker/scripts/resolve-pipeline.sh" \
    "$TMP/incomplete-skill/agy-worker/scripts/resolve-pipeline.sh"
bash "$TMP/incomplete-skill/agy-worker/scripts/resolve-pipeline.sh" \
    > "$TMP/incomplete.out" 2> "$TMP/incomplete.err"
rc=$?
if [[ "$rc" == "2" && ! -s "$TMP/incomplete.out" ]] \
        && grep -Fq 'complete agy-worker skill bundle' "$TMP/incomplete.err"; then
    ok "skill-folder-only resolver rejects an incomplete runtime bundle"
else
    bad "skill-folder-only resolver rejects an incomplete runtime bundle"
fi

cp -R "$ROOT/skills/agy-worker" "$TMP/missing-doctor-skill"
rm -f "$TMP/missing-doctor-skill/runtime/doctor.sh"
PATH="$TMP/no-network-bin:$PATH" NETWORK_MARKER="$TMP/missing-doctor-network" \
    bash "$TMP/missing-doctor-skill/scripts/resolve-pipeline.sh" \
    > "$TMP/missing-doctor.out" 2> "$TMP/missing-doctor.err"
rc=$?
if [[ "$rc" == "2" && ! -s "$TMP/missing-doctor.out" ]] \
        && grep -Fq 'complete agy-worker skill bundle' "$TMP/missing-doctor.err" \
        && [[ ! -e "$TMP/missing-doctor-network" ]]; then
    ok "resolver rejects a doctor-less bundle without fallback or network"
else
    bad "resolver rejects a doctor-less bundle without fallback or network"
fi

mkdir -p "$TMP/bin" "$TMP/installed"
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/bin/agy"
chmod +x "$TMP/bin/agy"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$TMP/installed" "$ROOT/install.sh" \
    > "$TMP/install.out" 2> "$TMP/install.err"
rc=$?
installed_root=""
if [[ "$rc" == "0" ]]; then
    installed_root="$(bash "$TMP/installed/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
fi
if [[ "$installed_root" == "$(cd "$ROOT" && pwd -P)" ]]; then
    ok "standalone install resolves the checkout without rewriting SKILL.md"
else
    bad "standalone install resolves the checkout without rewriting SKILL.md"
fi
if [[ -x "$TMP/installed/agy-worker/runtime/doctor.sh" ]] \
        && [[ -x "$TMP/installed/agy-worker/runtime/scripts/doctor-metadata.py" ]] \
        && cmp -s "$ROOT/compat/agy-verified-version.txt" \
            "$TMP/installed/agy-worker/runtime/compat/agy-verified-version.txt" \
        && cmp -s "$ROOT/compat/agy-upstream-head.txt" \
            "$TMP/installed/agy-worker/runtime/compat/agy-upstream-head.txt" \
        && cmp -s "$ROOT/compat/agy-last-reviewed.txt" \
            "$TMP/installed/agy-worker/runtime/compat/agy-last-reviewed.txt" \
        && cmp -s "$ROOT/compat/agy-model-effort-matrix.json" \
            "$TMP/installed/agy-worker/runtime/compat/agy-model-effort-matrix.json" \
        && cmp -s "$ROOT/compat/model-effort-matrix.schema.json" \
            "$TMP/installed/agy-worker/runtime/compat/model-effort-matrix.schema.json" \
        && cmp -s "$ROOT/compat/agy-model-effort-matrix.sha256" \
            "$TMP/installed/agy-worker/runtime/compat/agy-model-effort-matrix.sha256"; then
    ok "standalone install preserves doctor and explicit-selector compatibility bytes"
else
    bad "standalone install preserves doctor and explicit-selector compatibility bytes"
fi

mkdir -p "$TMP/reject-relative/agy-worker"
cp -R "$ROOT/skills/agy-worker/"* "$TMP/reject-relative/agy-worker/"
printf '../relative\n' > "$TMP/reject-relative/agy-worker/.pipeline-root"
bash "$TMP/reject-relative/agy-worker/scripts/resolve-pipeline.sh" \
    > "$TMP/relative.out" 2> "$TMP/relative.err"
rc=$?
if [[ "$rc" == "2" && ! -s "$TMP/relative.out" ]]; then
    ok "standalone resolver rejects a relative pipeline marker"
else
    bad "standalone resolver rejects a relative pipeline marker"
fi

printf '/definitely/missing/codex-agy-worker\n' > "$TMP/reject-relative/agy-worker/.pipeline-root"
bash "$TMP/reject-relative/agy-worker/scripts/resolve-pipeline.sh" \
    > "$TMP/missing.out" 2> "$TMP/missing.err"
rc=$?
if [[ "$rc" == "2" && ! -s "$TMP/missing.out" ]]; then
    ok "standalone resolver rejects a missing pipeline runtime"
else
    bad "standalone resolver rejects a missing pipeline runtime"
fi

required_suite_paths=(
    tests/test-qa-gate.sh
    tests/test-evidence-receipt.sh
    tests/test-agy-worker.sh
    tests/test-update.sh
    tests/test-reporting.sh
    tests/test-packaging.sh
    tests/test-doctor.sh
    tests/test-proof-demo.sh
)
governance_lists_all_suites=1
for suite in "${required_suite_paths[@]}"; do
    if ! grep -Fq "./$suite" "$ROOT/CONTRIBUTING.md" \
            || ! grep -Fq "./$suite" "$ROOT/.github/pull_request_template.md"; then
        governance_lists_all_suites=0
    fi
done
if ! grep -Fq '/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py' \
        "$ROOT/CONTRIBUTING.md" \
        || ! grep -Fq '/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py' \
            "$ROOT/.github/pull_request_template.md"; then
    governance_lists_all_suites=0
fi
if ! grep -Fq '/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py' \
        "$ROOT/CONTRIBUTING.md" \
        || ! grep -Fq '/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py' \
            "$ROOT/.github/pull_request_template.md"; then
    governance_lists_all_suites=0
fi

if [[ "$governance_lists_all_suites" == "1" ]] \
        && grep -Fq 'Google/Gemini' "$ROOT/PRIVACY.md" \
        && grep -Fq 'logs/' "$ROOT/PRIVACY.md" \
        && grep -Fq 'GitHub Issues' "$ROOT/SUPPORT.md" \
        && grep -Fq 'not legal advice' "$ROOT/TERMS.md"; then
    ok "governance docs require all ten suites and disclose public policy boundaries"
else
    bad "governance docs require all ten suites and disclose public policy boundaries"
fi

python3 "$ROOT/scripts/validate-brand-assets.py" "$ROOT/docs/assets/brand" \
    > "$TMP/brand-valid.out" 2> "$TMP/brand-valid.err"
brand_valid_rc=$?
if [[ "$brand_valid_rc" == "0" ]] \
        && grep -Fq '4 SVG, 7 PNG' "$TMP/brand-valid.out" \
        && grep -Fq 'https://cagdasyurekli.github.io/codex-agy-worker/' "$ROOT/docs/_config.yml" \
        && grep -Fq 'https://cagdasyurekli.github.io/codex-agy-worker/assets/brand/social-preview-1280x640.png' "$ROOT/docs/_config.yml" \
        && grep -Fq 'canonical' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq 'property="og:image"' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq 'name="twitter:card" content="summary_large_image"' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq 'sizes="16x16"' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq 'sizes="32x32"' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq '<picture aria-hidden="true">' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq '<loc>https://cagdasyurekli.github.io/codex-agy-worker/</loc>' "$ROOT/docs/sitemap.xml" \
        && grep -Fq 'GitHub repository as the source of truth' "$ROOT/docs/index.md" \
        && grep -Fq '<picture>' "$ROOT/README.md" \
        && grep -Fq 'srcset="docs/assets/brand/logo-dark.svg"' "$ROOT/README.md" \
        && grep -Fq 'src="docs/assets/brand/logo-light.svg" alt=""' "$ROOT/README.md" \
        && [[ ! -e "$ROOT/docs/robots.txt" ]]; then
    ok "approved brand assets and GitHub Pages wiring pass the production contract"
else
    bad "approved brand assets and GitHub Pages wiring pass the production contract"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-image"
python3 - "$TMP/reject-brand-image/logo-light.svg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(
    text.replace("</svg>", '<image href="https://invalid.example/logo.svg"/></svg>'),
    encoding="utf-8",
)
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-image" \
    > "$TMP/brand-image.out" 2> "$TMP/brand-image.err"
brand_image_rc=$?
if [[ "$brand_image_rc" == "1" ]] \
        && grep -Fq 'forbidden image element' "$TMP/brand-image.err"; then
    ok "brand validator rejects an external SVG image reference"
else
    bad "brand validator rejects an external SVG image reference"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-onload"
python3 - "$TMP/reject-brand-onload/logo-light.svg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(text.replace("<svg ", '<svg onload="alert(1)" ', 1), encoding="utf-8")
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-onload" \
    > "$TMP/brand-onload.out" 2> "$TMP/brand-onload.err"
brand_onload_rc=$?
if [[ "$brand_onload_rc" == "1" ]] \
        && grep -Fq 'event attributes are forbidden' "$TMP/brand-onload.err"; then
    ok "brand validator rejects an SVG root event attribute"
else
    bad "brand validator rejects an SVG root event attribute"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-style"
python3 - "$TMP/reject-brand-style/logo-light.svg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(
    text.replace(
        "</svg>",
        '<style>@import url("https://invalid.example/brand.css");</style></svg>',
    ),
    encoding="utf-8",
)
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-style" \
    > "$TMP/brand-style.out" 2> "$TMP/brand-style.err"
brand_style_rc=$?
if [[ "$brand_style_rc" == "1" ]] \
        && grep -Fq 'forbidden style element' "$TMP/brand-style.err"; then
    ok "brand validator rejects SVG style imports and external CSS"
else
    bad "brand validator rejects SVG style imports and external CSS"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-truncated"
python3 - "$TMP/reject-brand-truncated/social-preview-1280x640.png" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
data = path.read_bytes()
path.write_bytes(data[:-5])
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-truncated" \
    > "$TMP/brand-truncated.out" 2> "$TMP/brand-truncated.err"
brand_truncated_rc=$?
if [[ "$brand_truncated_rc" == "1" ]] \
        && grep -Fq 'truncated PNG chunk' "$TMP/brand-truncated.err"; then
    ok "brand validator rejects a truncated social-preview PNG"
else
    bad "brand validator rejects a truncated social-preview PNG"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-geometry"
python3 - "$TMP/reject-brand-geometry/logo-dark.svg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(text.replace("M160 224", "M161 224", 1), encoding="utf-8")
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-geometry" \
    > "$TMP/brand-geometry.out" 2> "$TMP/brand-geometry.err"
brand_geometry_rc=$?
if [[ "$brand_geometry_rc" == "1" ]] \
        && grep -Fq 'ordered geometry diverged' "$TMP/brand-geometry.err"; then
    ok "brand validator rejects light and dark SVG geometry divergence"
else
    bad "brand validator rejects light and dark SVG geometry divergence"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-trns"
python3 - "$TMP/reject-brand-trns/social-preview-1280x640.png" <<'PY'
from pathlib import Path
import struct
import sys
import zlib

path = Path(sys.argv[1])
data = path.read_bytes()
output = bytearray(data[:8])
cursor = 8
inserted = False
while cursor < len(data):
    length = struct.unpack(">I", data[cursor : cursor + 4])[0]
    chunk_end = cursor + 12 + length
    chunk_type = data[cursor + 4 : cursor + 8]
    if chunk_type == b"IDAT" and not inserted:
        transparent_color = b"\x00\x00\x00\x00\x00\x00"
        trns_type = b"tRNS"
        output.extend(struct.pack(">I", len(transparent_color)))
        output.extend(trns_type)
        output.extend(transparent_color)
        output.extend(
            struct.pack(">I", zlib.crc32(trns_type + transparent_color) & 0xFFFFFFFF)
        )
        inserted = True
    output.extend(data[cursor:chunk_end])
    cursor = chunk_end
assert inserted
path.write_bytes(output)
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-trns" \
    > "$TMP/brand-trns.out" 2> "$TMP/brand-trns.err"
brand_trns_rc=$?
if [[ "$brand_trns_rc" == "1" ]] \
        && grep -Fq 'tRNS is forbidden' "$TMP/brand-trns.err"; then
    ok "brand validator rejects a valid-CRC tRNS transparency chunk"
else
    bad "brand validator rejects a valid-CRC tRNS transparency chunk"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-idat"
python3 - "$TMP/reject-brand-idat/social-preview-1280x640.png" <<'PY'
from pathlib import Path
import struct
import sys
import zlib

path = Path(sys.argv[1])
data = path.read_bytes()
output = bytearray(data[:8])
cursor = 8
replaced = False
while cursor < len(data):
    length = struct.unpack(">I", data[cursor : cursor + 4])[0]
    chunk_end = cursor + 12 + length
    chunk_type = data[cursor + 4 : cursor + 8]
    if chunk_type == b"IDAT" and not replaced:
        payload = b"\x00" * length
        output.extend(struct.pack(">I", length))
        output.extend(chunk_type)
        output.extend(payload)
        output.extend(struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF))
        replaced = True
    else:
        output.extend(data[cursor:chunk_end])
    cursor = chunk_end
assert replaced
path.write_bytes(output)
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-idat" \
    > "$TMP/brand-idat.out" 2> "$TMP/brand-idat.err"
brand_idat_rc=$?
if [[ "$brand_idat_rc" == "1" ]] \
        && grep -Fq 'invalid IDAT zlib stream' "$TMP/brand-idat.err"; then
    ok "brand validator rejects a re-CRCed invalid IDAT zlib stream"
else
    bad "brand validator rejects a re-CRCed invalid IDAT zlib stream"
fi

if [[ ! -e "$ROOT/codex-skill/SKILL.md" ]] \
        && [[ -f "$ROOT/skills/agy-worker/SKILL.md" ]]; then
    ok "repository has one canonical skill source"
else
    bad "repository has one canonical skill source"
fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
