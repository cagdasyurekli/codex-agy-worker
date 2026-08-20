#!/usr/bin/env bash
# Offline, fake-tool tests for the read-only doctor contract.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
HOST_PYTHON="$(command -v python3)"
TMP="$(mktemp -d -t agyworker-doctor.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

expect_exit() {
    local name="$1" want="$2" got="$3"
    if [[ "$got" == "$want" ]]; then ok "$name (exit $got)"; else bad "$name (exit $got, wanted $want)"; fi
}

snapshot_tree() {
    "$HOST_PYTHON" -B - "$1" <<'PY'
import hashlib
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
digest = hashlib.sha256()
for current, dirs, files in os.walk(root):
    dirs[:] = sorted(name for name in dirs if name not in {".git", "__pycache__"})
    for name in sorted(files):
        if name.endswith((".pyc", ".pyo")):
            continue
        path = Path(current) / name
        relative = path.relative_to(root).as_posix().encode("utf-8", "surrogateescape")
        digest.update(relative + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
print(digest.hexdigest())
PY
}

make_fixture() {
    local destination="$1"
    mkdir -p "$destination/bin" "$destination/home/.codex" \
        "$destination/home/.gemini" "$destination/SECRET_REPO_PATH" \
        "$destination/tmp"
    cp -R "$ROOT/skills/agy-worker/runtime" "$destination/runtime"
    rm -rf "$destination/runtime/__pycache__" "$destination/runtime/scripts/__pycache__"
    printf '1.1.16\n' > "$destination/runtime/compat/agy-verified-version.txt"
    "$HOST_PYTHON" -B -c 'from datetime import date; print(date.today().isoformat())' \
        > "$destination/runtime/compat/agy-last-reviewed.txt"
    printf 'CONFIG_SECRET_DO_NOT_READ\n' > "$destination/home/.codex/config.toml"
    printf 'GEMINI_SECRET_DO_NOT_READ\n' > "$destination/home/.gemini/credentials"
    ln -s /bin/bash "$destination/bin/bash"
    ln -s /usr/bin/dirname "$destination/bin/dirname"
    ln -s "$(command -v mktemp)" "$destination/bin/mktemp"

    printf '%s\n' '#!/usr/bin/env bash' \
        'printf "python:%s\\n" "${TMPDIR-UNSET}" >> "${DOCTOR_TMP_OBSERVATIONS:-/dev/null}"' \
        '[[ "${FAKE_PYTHON_MODE:-ready}" == "fail" ]] && exit 7' \
        'if [[ "${2:-}" == *doctor-metadata.py && "${3:-}" == "capture-agy-version" ]]; then' \
        '  case "${FAKE_PYTHON_CAPTURE_MODE:-real}" in' \
        '    no-newline) printf "1.1.16"; exit 0 ;;' \
        '    multiline) printf "1.1.16\\nextra\\n"; exit 0 ;;' \
        '    short-write) exec 1>&-; exit 0 ;;' \
        '    disk-full) exit 74 ;;' \
        '  esac' \
        'fi' \
        'exec "$DOCTOR_TEST_PYTHON" "$@"' > "$destination/bin/python3"
    printf '%s\n' '#!/usr/bin/env bash' \
        'printf "git:%s\\n" "${TMPDIR-UNSET}" >> "${DOCTOR_TMP_OBSERVATIONS:-/dev/null}"' \
        ': > "$TMPDIR/xcrun_db"' \
        'printf "git" >> "$DOCTOR_GIT_CALLS"' \
        'printf " %s" "$@" >> "$DOCTOR_GIT_CALLS"' \
        'printf "\\n" >> "$DOCTOR_GIT_CALLS"' \
        'if [[ "$1" == "--version" ]]; then' \
        '  [[ "${FAKE_GIT_MODE:-ready}" == "version-fail" ]] && exit 1' \
        '  [[ "${FAKE_GIT_MODE:-ready}" == "version-usage" ]] && { printf "git version usage\\n"; exit 0; }' \
        '  printf "git version 2.45.0\\n"; exit 0' \
        'fi' \
        'if [[ "$1" == "-C" && "$3" == "rev-parse" && "$4" == "--is-inside-work-tree" ]]; then' \
        '  [[ "${FAKE_GIT_MODE:-ready}" == "invalid-repo" ]] && exit 1' \
        '  printf "true\\n"; exit 0' \
        'fi' \
        'if [[ "$1" == "-C" && "$3" == "worktree" && "$4" == "list" && "$5" == "--porcelain" ]]; then' \
        '  [[ "${FAKE_GIT_MODE:-ready}" == "worktree-fail" ]] && exit 1' \
        '  [[ "${FAKE_GIT_MODE:-ready}" == "worktree-empty" ]] && exit 0' \
        '  [[ "${FAKE_GIT_MODE:-ready}" == "worktree-usage" ]] && { printf "worktree usage\\n"; exit 0; }' \
        '  printf "worktree /redacted\\nHEAD 0000000000000000000000000000000000000000\\n"; exit 0' \
        'fi' \
        'exit 99' > "$destination/bin/git"
    printf '%s\n' '#!/usr/bin/env bash' \
        'printf "%s\\n" "$*" >> "$DOCTOR_AGY_CALLS"' \
        '"$DOCTOR_TEST_PYTHON" -B -c '"'"'import os,stat,sys; p=sys.argv[1]; c=os.path.join(p,"agy-version"); open(sys.argv[2],"a").write(f"agy:{p}:{stat.S_IMODE(os.stat(p).st_mode):03o}:{stat.S_IMODE(os.stat(c).st_mode):03o}\\n")'"'"' "$TMPDIR" "${DOCTOR_TMP_OBSERVATIONS:-/dev/null}"' \
        'case "${FAKE_AGY_MODE:-ready}" in' \
        '  ready) printf "agy 1.1.16\\n" ;;' \
        '  bare) printf "1.1.16\\n" ;;' \
        '  no-newline) printf "agy 1.1.16" ;;' \
        '  two-newlines) printf "agy 1.1.16\\n\\n" ;;' \
        '  carriage-return) printf "agy 1.1.16\\r\\n" ;;' \
        '  control) printf "agy 1.1.16\\t" ;;' \
        '  nul) printf "agy 1.1.16\\0" ;;' \
        '  prefix-junk) printf "version: agy 1.1.16\\n" ;;' \
        '  oversize) printf "agy "; i=0; while [[ $i -lt 140 ]]; do printf "1"; i=$((i+1)); done ;;' \
        '  huge) printf "agy "; i=0; while [[ $i -lt 4096 ]]; do printf "1"; i=$((i+1)); done ;;' \
        '  signal) kill -TERM "$PPID"; exit 7 ;;' \
        '  hang)' \
        '    (trap "" HUP INT TERM; while :; do /bin/sleep 1; done) &' \
        '    child=$!; printf "%s\\n" "$child" > "$DOCTOR_DESCENDANT_PID"' \
        '    printf "%s\\n" "$$" > "$DOCTOR_AGY_PID"' \
        '    : > "$DOCTOR_HANG_READY"' \
        '    trap "" HUP INT TERM' \
        '    while :; do /bin/sleep 1; done' \
        '    ;;' \
        '  drift) printf "agy 1.1.11\\n" ;;' \
        '  empty) : ;;' \
        '  usage) printf "usage: agy [options]\\n" ;;' \
        '  multiline) printf "agy 1.1.16\\nextra\\n" ;;' \
        '  fail) exit 7 ;;' \
        'esac' > "$destination/bin/agy"
    printf '%s\n' '#!/usr/bin/env bash' \
        ': > "$DOCTOR_NETWORK_MARKER"' \
        'exit 99' > "$destination/bin/curl"
    cp "$destination/bin/curl" "$destination/bin/wget"
    chmod +x "$destination/bin/python3" "$destination/bin/git" \
        "$destination/bin/agy" "$destination/bin/curl" "$destination/bin/wget"
}

run_doctor() {
    local fixture="$1" label="$2"; shift 2
    DOCTOR_TEST_PYTHON="$HOST_PYTHON" \
    DOCTOR_AGY_CALLS="$TMP/$label.agy-calls" \
    DOCTOR_GIT_CALLS="$TMP/$label.git-calls" \
    DOCTOR_NETWORK_MARKER="$TMP/$label.network" \
    DOCTOR_TMP_OBSERVATIONS="$TMP/$label.tmp-observations" \
    DOCTOR_HANG_READY="$TMP/$label.hang-ready" \
    DOCTOR_AGY_PID="$TMP/$label.agy-pid" \
    DOCTOR_DESCENDANT_PID="$TMP/$label.descendant-pid" \
    FAKE_AGY_MODE="${FAKE_AGY_MODE:-ready}" \
    FAKE_GIT_MODE="${FAKE_GIT_MODE:-ready}" \
    FAKE_PYTHON_MODE="${FAKE_PYTHON_MODE:-ready}" \
    FAKE_PYTHON_CAPTURE_MODE="${FAKE_PYTHON_CAPTURE_MODE:-real}" \
    TMPDIR="${DOCTOR_RUN_TMPDIR:-$fixture/tmp}" \
    HOME="$fixture/home" PATH="$fixture/bin" \
        /bin/bash "$fixture/runtime/doctor.sh" \
        --repo "$fixture/SECRET_REPO_PATH" "$@" \
        > "$TMP/$label.out" 2> "$TMP/$label.err"
}

assert_json_contract() {
    "$HOST_PYTHON" -B - "$1" "$2" "$3" <<'PY'
import json
import sys

path, overall, exit_code = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    value = json.load(handle)
assert list(value) == [
    "schema_version", "kind", "overall", "exit_code", "checks", "scope", "limitations"
]
assert value["schema_version"] == 1
assert value["kind"] == "agy-worker-doctor"
assert value["overall"] == overall
assert value["exit_code"] == int(exit_code)
assert [item["id"] for item in value["checks"]] == [
    "runtime_bundle", "private_workspace", "bash", "python", "git", "repository", "git_worktree",
    "agy", "agy_version", "agy_source", "compatibility_review",
]
assert all(list(item) == ["id", "status", "detail"] for item in value["checks"])
assert value["scope"] == "offline-prerequisites-only"
assert value["limitations"] == [
    "authentication", "provider-availability", "sandbox-permission", "task-quality",
    "future-dispatch",
]
PY
}

echo "doctor.sh offline test suite"
echo

BASE_FIXTURE="$TMP/base-fixture"
make_fixture "$BASE_FIXTURE"

if /bin/bash -c 'source "$1"; doctor_bash_compatible 3 2' _ \
        "$BASE_FIXTURE/runtime/doctor.sh"; then
    ok "Bash 3.2 is accepted by the runtime comparator"
else
    bad "Bash 3.2 is accepted by the runtime comparator"
fi
if /bin/bash -c 'source "$1"; ! doctor_bash_compatible 3 1' _ \
        "$BASE_FIXTURE/runtime/doctor.sh"; then
    ok "Bash 3.1 is rejected by the runtime comparator"
else
    bad "Bash 3.1 is rejected by the runtime comparator"
fi

run_doctor "$BASE_FIXTURE" ready-text
rc=$?
expect_exit "compatible fake toolchain is ready" 0 "$rc"
if grep -Fxq 'overall: ready' "$TMP/ready-text.out" \
        && grep -Fxq 'check agy_version: ready - verified-version-match' "$TMP/ready-text.out" \
        && grep -Fxq 'check agy_source: ready - reviewed-source-match' "$TMP/ready-text.out" \
        && grep -Fxq 'scope: offline-prerequisites-only' "$TMP/ready-text.out"; then
    ok "text output has the stable ready contract"
else
    bad "text output has the stable ready contract"
fi

run_doctor "$BASE_FIXTURE" ready-json --format json
rc=$?
if [[ "$rc" == 0 ]] && assert_json_contract "$TMP/ready-json.out" ready 0; then
    ok "JSON output has stable keys, order, and ready meanings"
else
    bad "JSON output has stable keys, order, and ready meanings"
fi
if "$HOST_PYTHON" -B - "$TMP/ready-text.out" "$TMP/ready-json.out" <<'PY'
import json
import sys
text = open(sys.argv[1], encoding="utf-8").read()
value = json.load(open(sys.argv[2], encoding="utf-8"))
assert f"overall: {value['overall']}\n" in text
for item in value["checks"]:
    assert f"check {item['id']}: {item['status']} - {item['detail']}\n" in text
PY
then ok "text and JSON report the same ordered observations"; else bad "text and JSON report the same ordered observations"; fi

run_doctor "$BASE_FIXTURE" stable-text-a
run_doctor "$BASE_FIXTURE" stable-text-b
if cmp -s "$TMP/stable-text-a.out" "$TMP/stable-text-b.out"; then
    ok "text output is deterministic"
else
    bad "text output is deterministic"
fi
run_doctor "$BASE_FIXTURE" stable-json-a --format json
run_doctor "$BASE_FIXTURE" stable-json-b --format json
if cmp -s "$TMP/stable-json-a.out" "$TMP/stable-json-b.out"; then
    ok "JSON output is deterministic"
else
    bad "JSON output is deterministic"
fi

ROOT_LAYOUT="$TMP/root-layout"
mkdir -p "$ROOT_LAYOUT/skills/agy-worker"
cp "$ROOT/doctor.sh" "$ROOT_LAYOUT/doctor.sh"
cp -R "$BASE_FIXTURE/runtime" "$ROOT_LAYOUT/skills/agy-worker/runtime"
chmod +x "$ROOT_LAYOUT/doctor.sh"
DOCTOR_TEST_PYTHON="$HOST_PYTHON" DOCTOR_AGY_CALLS="$TMP/root.agy-calls" \
DOCTOR_GIT_CALLS="$TMP/root.git-calls" DOCTOR_NETWORK_MARKER="$TMP/root.network" \
HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
    /bin/bash "$ROOT_LAYOUT/doctor.sh" --repo "$BASE_FIXTURE/SECRET_REPO_PATH" \
    > "$TMP/root.out" 2> "$TMP/root.err"
rc=$?
if [[ "$rc" == 0 ]] && cmp -s "$TMP/root.out" "$TMP/ready-text.out"; then
    ok "root wrapper preserves canonical runtime output"
else
    bad "root wrapper preserves canonical runtime output"
fi

run_root_launcher() {
    local launcher="$1" label="$2"; shift 2
    DOCTOR_TEST_PYTHON="$HOST_PYTHON" DOCTOR_AGY_CALLS="$TMP/$label.agy-calls" \
    DOCTOR_GIT_CALLS="$TMP/$label.git-calls" DOCTOR_NETWORK_MARKER="$TMP/$label.network" \
    TMPDIR="$BASE_FIXTURE/tmp" HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
        /bin/bash "$launcher" --repo "$BASE_FIXTURE/SECRET_REPO_PATH" "$@" \
        > "$TMP/$label.out" 2> "$TMP/$label.err"
}

for link_kind in absolute relative; do
    foreign_layout="$TMP/root-foreign-$link_kind"
    foreign_runtime="$TMP/root-foreign-$link_kind-runtime"
    marker="$TMP/root-foreign-$link_kind.marker"
    mkdir -p "$foreign_layout/skills/agy-worker"
    cp "$ROOT/doctor.sh" "$foreign_layout/doctor.sh"
    cp -R "$BASE_FIXTURE/runtime" "$foreign_runtime"
    printf '#!/usr/bin/env bash\n: > %q\n' "$marker" \
        > "$foreign_runtime/doctor.sh"
    chmod +x "$foreign_layout/doctor.sh" "$foreign_runtime/doctor.sh"
    if [[ "$link_kind" == absolute ]]; then
        ln -s "$foreign_runtime" "$foreign_layout/skills/agy-worker/runtime"
    else
        ln -s "../../../${foreign_runtime##*/}" \
            "$foreign_layout/skills/agy-worker/runtime"
    fi
    run_root_launcher "$foreign_layout/doctor.sh" "root-foreign-$link_kind"
    rc=$?
    if [[ "$rc" == 3 && ! -s "$TMP/root-foreign-$link_kind.out" ]] \
            && grep -Fxq 'doctor: launcher location is unavailable' \
                "$TMP/root-foreign-$link_kind.err" \
            && [[ ! -e "$marker" ]] \
            && ! grep -Fq "$TMP" "$TMP/root-foreign-$link_kind.err"; then
        ok "root wrapper rejects $link_kind foreign runtime without executing it"
    else
        bad "root wrapper rejects $link_kind foreign runtime without executing it"
    fi
done
(
    cd "$TMP" || exit 1
    DOCTOR_TEST_PYTHON="$HOST_PYTHON" DOCTOR_AGY_CALLS="$TMP/root-path.agy-calls" \
    DOCTOR_GIT_CALLS="$TMP/root-path.git-calls" DOCTOR_NETWORK_MARKER="$TMP/root-path.network" \
    HOME="$BASE_FIXTURE/home" PATH="$ROOT_LAYOUT:$BASE_FIXTURE/bin" \
        doctor.sh --repo "$BASE_FIXTURE/SECRET_REPO_PATH" \
        > "$TMP/root-path.out" 2> "$TMP/root-path.err"
)
rc=$?
if [[ "$rc" == 0 ]] && cmp -s "$TMP/root-path.out" "$TMP/ready-text.out"; then
    ok "root wrapper resolves its runtime when invoked through PATH"
else
    bad "root wrapper resolves its runtime when invoked through PATH"
fi

mkdir -p "$TMP/symlink launchers/absolute" "$TMP/symlink launchers/relative" \
    "$TMP/symlink launchers/path-bin"
ln -s "$ROOT_LAYOUT/doctor.sh" "$TMP/symlink launchers/absolute/doctor-absolute"
ln -s '../../root-layout/doctor.sh' "$TMP/symlink launchers/relative/doctor-relative"
ln -s "$ROOT_LAYOUT/doctor.sh" "$TMP/symlink launchers/path-bin/doctor.sh"

for entry in \
    "$TMP/symlink launchers/absolute/doctor-absolute:absolute-symlink" \
    "$TMP/symlink launchers/relative/doctor-relative:relative-symlink"; do
    launcher="${entry%:*}"
    label="${entry##*:}"
    run_root_launcher "$launcher" "$label"
    rc=$?
    if [[ "$rc" == 0 && ! -s "$TMP/$label.err" ]] \
            && cmp -s "$TMP/$label.out" "$TMP/ready-text.out"; then
        ok "root wrapper resolves $label chains with spaces"
    else
        bad "root wrapper resolves $label chains with spaces"
    fi
done

(
    cd "$TMP" || exit 1
    DOCTOR_TEST_PYTHON="$HOST_PYTHON" DOCTOR_AGY_CALLS="$TMP/path-symlink.agy-calls" \
    DOCTOR_GIT_CALLS="$TMP/path-symlink.git-calls" DOCTOR_NETWORK_MARKER="$TMP/path-symlink.network" \
    TMPDIR="$BASE_FIXTURE/tmp" HOME="$BASE_FIXTURE/home" \
    PATH="$TMP/symlink launchers/path-bin:$BASE_FIXTURE/bin" \
        doctor.sh --repo "$BASE_FIXTURE/SECRET_REPO_PATH" \
        > "$TMP/path-symlink.out" 2> "$TMP/path-symlink.err"
)
rc=$?
if [[ "$rc" == 0 && ! -s "$TMP/path-symlink.err" ]] \
        && cmp -s "$TMP/path-symlink.out" "$TMP/ready-text.out"; then
    ok "root wrapper resolves a PATH-discovered symlink"
else
    bad "root wrapper resolves a PATH-discovered symlink"
fi

ln -s "$TMP/does-not-exist" "$TMP/symlink launchers/broken"
ln -s 'loop-b' "$TMP/symlink launchers/loop-a"
ln -s 'loop-a' "$TMP/symlink launchers/loop-b"
ln -s "$BASE_FIXTURE/bin/agy" "$TMP/symlink launchers/foreign"
for label in broken loop-a foreign; do
    /bin/bash -c '
        source "$1"
        doctor_resolve_source "$2" >/dev/null || doctor_launcher_fail
    ' _ "$ROOT_LAYOUT/doctor.sh" "$TMP/symlink launchers/$label" \
        > "$TMP/reject-$label.out" 2> "$TMP/reject-$label.err"
    rc=$?
    if [[ "$rc" == 3 && ! -s "$TMP/reject-$label.out" ]] \
            && grep -Fxq 'doctor: launcher location is unavailable' "$TMP/reject-$label.err" \
            && ! grep -Fq "$TMP" "$TMP/reject-$label.err"; then
        ok "root wrapper rejects $label symlink with bounded sanitized exit 3"
    else
        bad "root wrapper rejects $label symlink with bounded sanitized exit 3"
    fi
done

run_root_launcher "$ROOT_LAYOUT/doctor.sh" root-usage --format yaml
rc=$?
if [[ "$rc" == 64 && ! -s "$TMP/root-usage.out" ]] \
        && grep -Fxq 'usage: doctor.sh [--repo DIR] [--format text|json]' "$TMP/root-usage.err"; then
    ok "root wrapper preserves canonical exit 64 usage contract"
else
    bad "root wrapper preserves canonical exit 64 usage contract"
fi

FOLDER_LAYOUT="$TMP/folder-layout/agy-worker"
mkdir -p "$FOLDER_LAYOUT/scripts"
cp "$ROOT/skills/agy-worker/scripts/resolve-pipeline.sh" "$FOLDER_LAYOUT/scripts/"
cp -R "$BASE_FIXTURE/runtime" "$FOLDER_LAYOUT/runtime"
resolved="$(PATH="$BASE_FIXTURE/bin" /bin/bash "$FOLDER_LAYOUT/scripts/resolve-pipeline.sh" 2>/dev/null)"
if [[ "$resolved" == "$(cd "$FOLDER_LAYOUT/runtime" && pwd -P)" ]]; then
    ok "folder-only resolver finds the bundled doctor runtime offline"
else
    bad "folder-only resolver finds the bundled doctor runtime offline"
fi
DOCTOR_TEST_PYTHON="$HOST_PYTHON" DOCTOR_AGY_CALLS="$TMP/folder.agy-calls" \
DOCTOR_GIT_CALLS="$TMP/folder.git-calls" DOCTOR_NETWORK_MARKER="$TMP/folder.network" \
HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
    /bin/bash "$resolved/doctor.sh" --repo "$BASE_FIXTURE/SECRET_REPO_PATH" \
    > "$TMP/folder.out" 2> "$TMP/folder.err"
rc=$?
expect_exit "folder-only bundled doctor runs without checkout or network" 0 "$rc"

before_root="$(snapshot_tree "$ROOT")"
before_fixture="$(snapshot_tree "$BASE_FIXTURE")"
before_home="$(snapshot_tree "$BASE_FIXTURE/home")"
run_doctor "$BASE_FIXTURE" immutable-ready
ready_rc=$?
after_root="$(snapshot_tree "$ROOT")"
after_fixture="$(snapshot_tree "$BASE_FIXTURE")"
after_home="$(snapshot_tree "$BASE_FIXTURE/home")"
if [[ "$ready_rc" == 0 && "$before_root" == "$after_root" \
        && "$before_fixture" == "$after_fixture" && "$before_home" == "$after_home" ]]; then
    ok "ready run leaves checkout, fixture, and HOME byte-identical"
else
    bad "ready run leaves checkout, fixture, and HOME byte-identical"
fi

NOT_READY_FIXTURE="$TMP/not-ready-fixture"
cp -R "$BASE_FIXTURE" "$NOT_READY_FIXTURE"
rm -f "$NOT_READY_FIXTURE/bin/agy"
before_root="$(snapshot_tree "$ROOT")"
before_fixture="$(snapshot_tree "$NOT_READY_FIXTURE")"
before_home="$(snapshot_tree "$NOT_READY_FIXTURE/home")"
run_doctor "$NOT_READY_FIXTURE" immutable-not-ready
not_ready_rc=$?
after_root="$(snapshot_tree "$ROOT")"
after_fixture="$(snapshot_tree "$NOT_READY_FIXTURE")"
after_home="$(snapshot_tree "$NOT_READY_FIXTURE/home")"
if [[ "$not_ready_rc" == 3 && "$before_root" == "$after_root" \
        && "$before_fixture" == "$after_fixture" && "$before_home" == "$after_home" ]]; then
    ok "not-ready run leaves checkout, fixture, and HOME byte-identical"
else
    bad "not-ready run leaves checkout, fixture, and HOME byte-identical"
fi

REVIEW_FIXTURE="$TMP/review-fixture"
cp -R "$BASE_FIXTURE" "$REVIEW_FIXTURE"
printf '1.1.10\n' > "$REVIEW_FIXTURE/bin/agy.version-unused"
before_root="$(snapshot_tree "$ROOT")"
before_fixture="$(snapshot_tree "$REVIEW_FIXTURE")"
before_home="$(snapshot_tree "$REVIEW_FIXTURE/home")"
FAKE_AGY_MODE=drift run_doctor "$REVIEW_FIXTURE" immutable-review
review_rc=$?
after_root="$(snapshot_tree "$ROOT")"
after_fixture="$(snapshot_tree "$REVIEW_FIXTURE")"
after_home="$(snapshot_tree "$REVIEW_FIXTURE/home")"
if [[ "$review_rc" == 3 && "$before_root" == "$after_root" \
        && "$before_fixture" == "$after_fixture" && "$before_home" == "$after_home" ]]; then
    ok "review-required run leaves checkout, fixture, and HOME byte-identical"
else
    bad "review-required run leaves checkout, fixture, and HOME byte-identical"
fi
unset FAKE_AGY_MODE

before_root="$(snapshot_tree "$ROOT")"
before_fixture="$(snapshot_tree "$BASE_FIXTURE")"
before_home="$(snapshot_tree "$BASE_FIXTURE/home")"
run_doctor "$BASE_FIXTURE" immutable-usage --format yaml
usage_rc=$?
after_root="$(snapshot_tree "$ROOT")"
after_fixture="$(snapshot_tree "$BASE_FIXTURE")"
after_home="$(snapshot_tree "$BASE_FIXTURE/home")"
if [[ "$usage_rc" == 64 && "$before_root" == "$after_root" \
        && "$before_fixture" == "$after_fixture" && "$before_home" == "$after_home" ]]; then
    ok "usage error leaves checkout, fixture, and HOME byte-identical"
else
    bad "usage error leaves checkout, fixture, and HOME byte-identical"
fi

run_doctor "$NOT_READY_FIXTURE" missing-agy --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "agy", "status": "not-ready", "detail": "missing"' "$TMP/missing-agy.out"; then
    ok "missing agy is not-ready"
else
    bad "missing agy is not-ready"
fi

MISSING_PYTHON="$TMP/missing-python"
cp -R "$BASE_FIXTURE" "$MISSING_PYTHON"
rm -f "$MISSING_PYTHON/bin/python3"
run_doctor "$MISSING_PYTHON" missing-python --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "python", "status": "not-ready"' "$TMP/missing-python.out"; then
    ok "missing Python is not-ready with valid JSON"
else
    bad "missing Python is not-ready with valid JSON"
fi
FAKE_PYTHON_MODE=fail run_doctor "$BASE_FIXTURE" failing-python --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "python", "status": "not-ready"' "$TMP/failing-python.out"; then
    ok "nonfunctional Python executable is not-ready"
else
    bad "nonfunctional Python executable is not-ready"
fi

MISSING_GIT="$TMP/missing-git"
cp -R "$BASE_FIXTURE" "$MISSING_GIT"
rm -f "$MISSING_GIT/bin/git"
run_doctor "$MISSING_GIT" missing-git --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "git", "status": "not-ready"' "$TMP/missing-git.out"; then
    ok "missing git is not-ready with valid JSON"
else
    bad "missing git is not-ready with valid JSON"
fi
FAKE_GIT_MODE=version-fail run_doctor "$BASE_FIXTURE" failing-git --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "git", "status": "not-ready"' "$TMP/failing-git.out"; then
    ok "nonfunctional git executable is not-ready"
else
    bad "nonfunctional git executable is not-ready"
fi
FAKE_GIT_MODE=version-usage run_doctor "$BASE_FIXTURE" usage-git --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "git", "status": "not-ready"' "$TMP/usage-git.out"; then
    ok "usage-like git output is not semantic availability evidence"
else
    bad "usage-like git output is not semantic availability evidence"
fi

for mode in empty usage multiline two-newlines carriage-return control nul oversize huge prefix-junk fail signal; do
    FAKE_AGY_MODE="$mode" run_doctor "$BASE_FIXTURE" "agy-$mode" --format json
    rc=$?
    if [[ "$rc" == 3 ]] \
            && grep -Fq '"id": "agy_version", "status": "not-ready", "detail": "invalid-version-output"' "$TMP/agy-$mode.out"; then
        ok "agy $mode output is rejected semantically"
    else
        bad "agy $mode output is rejected semantically"
    fi
done

for capture_mode in no-newline multiline short-write disk-full; do
    FAKE_PYTHON_CAPTURE_MODE="$capture_mode" run_doctor \
        "$BASE_FIXTURE" "capture-$capture_mode" --format json
    rc=$?
    if [[ "$rc" == 3 ]] \
            && grep -Fq '"id": "agy_version", "status": "not-ready", "detail": "invalid-version-output"' \
                "$TMP/capture-$capture_mode.out" \
            && ! grep -Fq "$TMP" "$TMP/capture-$capture_mode.out" \
                "$TMP/capture-$capture_mode.err"; then
        ok "normalized capture $capture_mode failure is bounded and sanitized"
    else
        bad "normalized capture $capture_mode failure is bounded and sanitized"
    fi
done

FAKE_AGY_MODE=bare run_doctor "$BASE_FIXTURE" agy-bare --format json
rc=$?
if [[ "$rc" == 0 ]] && grep -Fq 'verified-version-match' "$TMP/agy-bare.out"; then
    ok "documented bare semantic agy version is accepted"
else
    bad "documented bare semantic agy version is accepted"
fi

FAKE_AGY_MODE=no-newline run_doctor "$BASE_FIXTURE" agy-no-newline --format json
rc=$?
if [[ "$rc" == 0 ]] && grep -Fq 'verified-version-match' "$TMP/agy-no-newline.out"; then
    ok "documented agy version without a terminal newline is accepted"
else
    bad "documented agy version without a terminal newline is accepted"
fi

HOSTILE_TMPDIR_FILE="$BASE_FIXTURE/SECRET_TMPDIR_PATH"
printf 'not a directory\n' > "$HOSTILE_TMPDIR_FILE"
mkdir -p "$BASE_FIXTURE/unwritable-tmp"
chmod 500 "$BASE_FIXTURE/unwritable-tmp"
ln -s "$BASE_FIXTURE/SECRET_REPO_PATH" "$BASE_FIXTURE/repo-tmp-link"
ln -s "$BASE_FIXTURE/home" "$BASE_FIXTURE/home-tmp-link"
for tmp_specification in \
    "repo:$BASE_FIXTURE/SECRET_REPO_PATH" \
    "home:$BASE_FIXTURE/home" \
    "repo-symlink:$BASE_FIXTURE/repo-tmp-link" \
    "home-symlink:$BASE_FIXTURE/home-tmp-link" \
    'relative:relative-tmp' \
    "newline:line"$'\n'"break" \
    "missing:$BASE_FIXTURE/missing-tmp" \
    "file:$HOSTILE_TMPDIR_FILE" \
    "unwritable:$BASE_FIXTURE/unwritable-tmp"; do
    tmp_label="${tmp_specification%%:*}"
    tmp_value="${tmp_specification#*:}"
    DOCTOR_RUN_TMPDIR="$tmp_value" run_doctor "$BASE_FIXTURE" "caller-tmp-$tmp_label" --format json
    rc=$?
    unset DOCTOR_RUN_TMPDIR
    if [[ "$rc" == 0 ]] \
            && assert_json_contract "$TMP/caller-tmp-$tmp_label.out" ready 0 \
            && { "$HOST_PYTHON" -B - "$TMP/caller-tmp-$tmp_label.tmp-observations" \
                "$BASE_FIXTURE" <<'PY'
import os
import sys

observations, fixture = sys.argv[1:]
lines = open(observations, encoding="utf-8").read().replace(chr(92) + "n", chr(10)).splitlines()
assert lines
workspaces = []
for line in lines:
    fields = line.split(":")
    assert fields[0] in {"python", "git", "agy"}
    workspaces.append(fields[1])
    if fields[0] == "agy":
        assert fields[2:] == ["700", "600"], fields
assert len(set(workspaces)) == 1
workspace = workspaces[0]
assert os.path.isabs(workspace)
assert not os.path.exists(workspace)
fixture = os.path.realpath(fixture)
assert not os.path.realpath(os.path.dirname(workspace)).startswith(fixture + os.sep)
PY
            } && ! grep -Fq "$BASE_FIXTURE" "$TMP/caller-tmp-$tmp_label.out" \
                "$TMP/caller-tmp-$tmp_label.err"; then
        ok "caller TMPDIR $tmp_label is ignored for every child probe"
    else
        bad "caller TMPDIR $tmp_label is ignored for every child probe"
    fi
done

REAL_PARENT_FIXTURE="$TMP/real-parent-fixture"
cp -R "$BASE_FIXTURE" "$REAL_PARENT_FIXTURE"
run_doctor "$REAL_PARENT_FIXTURE" real-runtime-parents --format json
rc=$?
if [[ "$rc" == 0 ]] \
        && assert_json_contract "$TMP/real-runtime-parents.out" ready 0; then
    ok "doctor accepts bundle-owned real runtime parent directories"
else
    bad "doctor accepts bundle-owned real runtime parent directories"
fi

for parent in scripts agents schemas compat benchmarks profiles; do
    for link_kind in absolute relative in-root; do
        label="doctor-parent-$parent-$link_kind"
        fixture="$TMP/$label-fixture"
        foreign_parent="$TMP/$label-foreign"
        marker="$TMP/$label.marker"
        cp -R "$BASE_FIXTURE" "$fixture"
        if [[ "$link_kind" == in-root ]]; then
            mv "$fixture/runtime/$parent" "$fixture/runtime/owned-$parent"
            ln -s "owned-$parent" "$fixture/runtime/$parent"
            foreign_parent="$fixture/runtime/owned-$parent"
        else
            mv "$fixture/runtime/$parent" "$foreign_parent"
            if [[ "$link_kind" == absolute ]]; then
                ln -s "$foreign_parent" "$fixture/runtime/$parent"
            else
                ln -s "../../../${foreign_parent##*/}" "$fixture/runtime/$parent"
            fi
        fi
        if [[ "$parent" == scripts ]]; then
            printf '#!/usr/bin/env bash\n: > %q\nprintf "1.1.10\\n"\n' "$marker" \
                > "$foreign_parent/doctor-metadata.py"
            chmod +x "$foreign_parent/doctor-metadata.py"
        fi
        run_doctor "$fixture" "$label" --format json
        rc=$?
        if [[ "$rc" == 3 ]] \
                && assert_json_contract "$TMP/$label.out" not-ready 3 \
                && grep -Fq '"id": "runtime_bundle", "status": "not-ready", "detail": "incomplete"' \
                    "$TMP/$label.out" \
                && [[ ! -e "$marker" ]] \
                && ! grep -Fq "$TMP" "$TMP/$label.out" "$TMP/$label.err"; then
            ok "doctor rejects $link_kind $parent parent without foreign execution"
        else
            bad "doctor rejects $link_kind $parent parent without foreign execution"
        fi
    done
done

for specification in \
    'qa-gate.sh:executable' \
    'verify-job.sh:executable' \
    'scripts/validate-envelope.py:executable' \
    'scripts/evidence_receipt.py:executable' \
    'scripts/persona_registry.py:executable' \
    'scripts/workload_profiles.py:executable' \
    'scripts/model_selection.py:executable' \
    'schemas/worker-result.schema.json:data' \
    'schemas/worker-result.provider.schema.json:data' \
    'schemas/evidence-receipt.schema.json:data' \
    'schemas/persona-run-manifest.schema.json:data' \
    'schemas/persona-transition-approval.schema.json:data' \
    'schemas/workload-profile.schema.json:data' \
    'compat/persona-registry.schema.json:data' \
    'compat/personas/manifest.json:data' \
    'profiles/v1/manifest.json:data' \
    'agents/repo-inventory.md:data' \
    'compat/agy-verified-version.txt:data' \
    'compat/agy-model-effort-matrix.json:data' \
    'compat/agy-models-inventory-binding.json:data'; do
    dependency_path="${specification%:*}"
    dependency_class="${specification##*:}"
    for wrong_type in directory symlink-directory symlink-foreign fifo wrong-mode; do
        label="doctor-wrong-${dependency_path//\//-}-$wrong_type"
        fixture="$TMP/$label-fixture"
        cp -R "$BASE_FIXTURE" "$fixture"
        artifact="$fixture/runtime/$dependency_path"
        rm -f "$artifact"
        case "$wrong_type" in
            directory) mkdir "$artifact" ;;
            symlink-directory) ln -s "$TMP" "$artifact" ;;
            symlink-foreign) ln -s /dev/null "$artifact" ;;
            fifo) mkfifo "$artifact" ;;
            wrong-mode)
                cp "$BASE_FIXTURE/runtime/$dependency_path" "$artifact"
                if [[ "$dependency_class" == executable ]]; then
                    chmod -x "$artifact"
                else
                    chmod +x "$artifact"
                fi
                ;;
        esac
        run_doctor "$fixture" "$label" --format json
        rc=$?
        if [[ "$rc" == 3 ]] \
                && assert_json_contract "$TMP/$label.out" not-ready 3 \
                && grep -Fq '"id": "runtime_bundle", "status": "not-ready", "detail": "incomplete"' \
                    "$TMP/$label.out" \
                && ! grep -Fq "$TMP" "$TMP/$label.out" "$TMP/$label.err"; then
            ok "doctor rejects $wrong_type for $dependency_class $dependency_path"
        else
            bad "doctor rejects $wrong_type for $dependency_class $dependency_path"
        fi
    done
done

for helper_mode in malformed side-effect exit stale; do
    fixture="$TMP/doctor-helper-$helper_mode-fixture"
    cp -R "$BASE_FIXTURE" "$fixture"
    helper="$fixture/runtime/scripts/runtime-bundle.sh"
    case "$helper_mode" in
        malformed) printf 'if then impossible\n' > "$helper" ;;
        side-effect) printf ': > %q\n' "$TMP/doctor-helper-side-effect.marker" > "$helper" ;;
        exit) printf 'exit 91\n' > "$helper" ;;
        stale) printf 'doctor_runtime_complete() { return 1; }\n' > "$helper" ;;
    esac
    run_doctor "$fixture" "doctor-helper-$helper_mode" --format json
    rc=$?
    if [[ "$rc" == 0 ]] \
            && assert_json_contract "$TMP/doctor-helper-$helper_mode.out" ready 0 \
            && [[ ! -e "$TMP/doctor-helper-side-effect.marker" ]]; then
        ok "doctor ignores $helper_mode candidate runtime helper"
    else
        bad "doctor ignores $helper_mode candidate runtime helper"
    fi
done

FAKE_AGY_MODE=drift run_doctor "$BASE_FIXTURE" version-drift --format json
rc=$?
if [[ "$rc" == 3 ]] && assert_json_contract "$TMP/version-drift.out" review-required 3 \
        && grep -Fq '"id": "agy_version", "status": "review-required", "detail": "version-drift"' "$TMP/version-drift.out"; then
    ok "semantic agy version drift requires review"
else
    bad "semantic agy version drift requires review"
fi

INCOMPLETE="$TMP/incomplete-bundle"
cp -R "$BASE_FIXTURE" "$INCOMPLETE"
rm -f "$INCOMPLETE/runtime/qa-gate.sh"
run_doctor "$INCOMPLETE" incomplete-bundle --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "runtime_bundle", "status": "not-ready", "detail": "incomplete"' "$TMP/incomplete-bundle.out"; then
    ok "incomplete bundled runtime is not-ready"
else
    bad "incomplete bundled runtime is not-ready"
fi

for dependency in \
    'job.sh:lifecycle-entry' \
    'scripts/validate-envelope.py:python-helper' \
    'scripts/evidence_receipt.py:receipt-helper' \
    'scripts/persona_registry.py:persona-registry-helper' \
    'scripts/workload_profiles.py:profile-helper' \
    'scripts/candidate_state.py:candidate-state-helper' \
    'scripts/job_lifecycle.py:lifecycle-helper' \
    'scripts/model_selection.py:model-resolver' \
    'schemas/worker-result.schema.json:schema' \
    'schemas/evidence-receipt.schema.json:receipt-schema' \
    'schemas/persona-run-manifest.schema.json:persona-run-manifest-schema' \
    'schemas/persona-transition-approval.schema.json:persona-transition-schema' \
    'schemas/workload-profile.schema.json:profile-schema' \
    'compat/persona-evidence.schema.json:persona-evidence-schema' \
    'compat/personas/manifest.json:persona-registry-manifest' \
    'schemas/job-state.schema.json:lifecycle-schema' \
    'schemas/model-selection.schema.json:selection-schema' \
    'benchmarks/v1/portable-source.json:benchmark-source-manifest' \
    'profiles/v1/manifest.json:profile-manifest' \
    'agents/repo-inventory.md:persona' \
    'compat/agy-upstream-head.txt:source-record' \
    'compat/agy-verified-version.txt:compat-record' \
    'compat/agy-model-effort-matrix.json:model-matrix' \
    'compat/model-effort-matrix.schema.json:matrix-schema' \
    'compat/agy-model-effort-matrix.sha256:matrix-hash' \
    'compat/agy-models-inventory-binding.json:inventory-binding' \
    'compat/agy-models-inventory-binding.sha256:inventory-binding-hash'; do
    dependency_path="${dependency%:*}"
    dependency_class="${dependency##*:}"
    dependency_label="${dependency_path//\//-}"
    fixture="$TMP/incomplete-$dependency_label"
    cp -R "$BASE_FIXTURE" "$fixture"
    rm -f "$fixture/runtime/$dependency_path"
    run_doctor "$fixture" "incomplete-$dependency_label" --format json
    rc=$?
    if [[ "$rc" == 3 ]] \
            && grep -Fq '"id": "runtime_bundle", "status": "not-ready", "detail": "incomplete"' \
                "$TMP/incomplete-$dependency_label.out"; then
        ok "doctor rejects an incomplete $dependency_class runtime"
    else
        bad "doctor rejects an incomplete $dependency_class runtime"
    fi
done

NO_HELPER="$TMP/no-helper"
cp -R "$BASE_FIXTURE" "$NO_HELPER"
rm -f "$NO_HELPER/runtime/scripts/doctor-metadata.py"
run_doctor "$NO_HELPER" no-helper --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "compatibility_review", "status": "not-ready", "detail": "metadata-unavailable"' "$TMP/no-helper.out"; then
    ok "missing doctor metadata helper is not-ready"
else
    bad "missing doctor metadata helper is not-ready"
fi

FAKE_GIT_MODE=invalid-repo run_doctor "$BASE_FIXTURE" invalid-repo --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "repository", "status": "not-ready", "detail": "invalid-git-worktree"' "$TMP/invalid-repo.out"; then
    ok "invalid target repository is not-ready"
else
    bad "invalid target repository is not-ready"
fi

for mode in worktree-fail worktree-empty worktree-usage; do
    FAKE_GIT_MODE="$mode" run_doctor "$BASE_FIXTURE" "$mode" --format json
    rc=$?
    if [[ "$rc" == 3 ]] && grep -Fq '"id": "git_worktree", "status": "not-ready", "detail": "unsupported"' "$TMP/$mode.out"; then
        ok "git $mode does not prove worktree support"
    else
        bad "git $mode does not prove worktree support"
    fi
done

for kind in missing malformed symlink tamper; do
    fixture="$TMP/$kind-source"
    cp -R "$BASE_FIXTURE" "$fixture"
    source_record="$fixture/runtime/compat/agy-upstream-head.txt"
    case "$kind" in
        missing) rm -f "$source_record" ;;
        malformed) printf 'not-a-revision\n' > "$source_record" ;;
        symlink)
            rm -f "$source_record"
            ln -s /dev/null "$source_record"
            ;;
        tamper) printf '%040d\n' 0 > "$source_record" ;;
    esac
    run_doctor "$fixture" "$kind-source" --format json
    rc=$?
    if [[ "$kind" == tamper ]]; then
        expected_detail='reviewed-source-mismatch'
    else
        expected_detail='reviewed-source-metadata-unavailable'
    fi
    if [[ "$rc" == 3 ]] \
            && grep -Fq "\"id\": \"agy_source\", \"status\": \"not-ready\", \"detail\": \"$expected_detail\"" \
                "$TMP/$kind-source.out" \
            && ! grep -Eq 'not-a-revision|0000000000000000000000000000000000000000|/dev/null' \
                "$TMP/$kind-source.out" "$TMP/$kind-source.err"; then
        ok "$kind source revision is rejected without raw metadata"
    else
        bad "$kind source revision is rejected without raw metadata"
    fi
done

MISSING_REVIEW="$TMP/missing-review"
cp -R "$BASE_FIXTURE" "$MISSING_REVIEW"
rm -f "$MISSING_REVIEW/runtime/compat/agy-last-reviewed.txt"
run_doctor "$MISSING_REVIEW" missing-review --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "compatibility_review", "status": "not-ready"' "$TMP/missing-review.out"; then
    ok "missing review metadata is not-ready"
else
    bad "missing review metadata is not-ready"
fi

MALFORMED_REVIEW="$TMP/malformed-review"
cp -R "$BASE_FIXTURE" "$MALFORMED_REVIEW"
printf 'not-a-date\n' > "$MALFORMED_REVIEW/runtime/compat/agy-last-reviewed.txt"
run_doctor "$MALFORMED_REVIEW" malformed-review --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "compatibility_review", "status": "not-ready", "detail": "invalid"' "$TMP/malformed-review.out"; then
    ok "malformed review metadata is not-ready"
else
    bad "malformed review metadata is not-ready"
fi

FUTURE_REVIEW="$TMP/future-review"
cp -R "$BASE_FIXTURE" "$FUTURE_REVIEW"
printf '2999-01-01\n' > "$FUTURE_REVIEW/runtime/compat/agy-last-reviewed.txt"
run_doctor "$FUTURE_REVIEW" future-review --format json
rc=$?
if [[ "$rc" == 3 ]] && grep -Fq '"id": "compatibility_review", "status": "not-ready", "detail": "invalid"' "$TMP/future-review.out"; then
    ok "future review metadata is not-ready"
else
    bad "future review metadata is not-ready"
fi

DUE_REVIEW="$TMP/due-review"
cp -R "$BASE_FIXTURE" "$DUE_REVIEW"
printf '2000-01-01\n' > "$DUE_REVIEW/runtime/compat/agy-last-reviewed.txt"
run_doctor "$DUE_REVIEW" due-review --format json
rc=$?
if [[ "$rc" == 3 ]] && assert_json_contract "$TMP/due-review.out" review-required 3 \
        && grep -Fq '"id": "compatibility_review", "status": "review-required", "detail": "due"' "$TMP/due-review.out"; then
    ok "due review metadata requires review"
else
    bad "due review metadata requires review"
fi

for kind in malformed missing; do
    fixture="$TMP/$kind-version"
    cp -R "$BASE_FIXTURE" "$fixture"
    if [[ "$kind" == malformed ]]; then
        printf 'v1.1.10\n' > "$fixture/runtime/compat/agy-verified-version.txt"
    else
        rm -f "$fixture/runtime/compat/agy-verified-version.txt"
    fi
    run_doctor "$fixture" "$kind-version" --format json
    rc=$?
    if [[ "$rc" == 3 ]] && grep -Fq '"id": "agy_version", "status": "not-ready", "detail": "verified-metadata-unavailable"' "$TMP/$kind-version.out"; then
        ok "$kind version metadata is not-ready"
    else
        bad "$kind version metadata is not-ready"
    fi
done

run_doctor "$BASE_FIXTURE" duplicate-repo --repo "$BASE_FIXTURE/SECRET_REPO_PATH"
expect_exit "duplicate --repo is a usage error" 64 "$?"
run_doctor "$BASE_FIXTURE" duplicate-format --format text --format json
expect_exit "duplicate --format is a usage error" 64 "$?"
run_doctor "$BASE_FIXTURE" unknown --unknown
expect_exit "unknown option is a usage error" 64 "$?"
DOCTOR_TEST_PYTHON="$HOST_PYTHON" HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
    /bin/bash "$BASE_FIXTURE/runtime/doctor.sh" --repo \
    > "$TMP/missing-repo.out" 2> "$TMP/missing-repo.err"
expect_exit "missing --repo value is a usage error" 64 "$?"
DOCTOR_TEST_PYTHON="$HOST_PYTHON" HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
    /bin/bash "$BASE_FIXTURE/runtime/doctor.sh" --format \
    > "$TMP/missing-format.out" 2> "$TMP/missing-format.err"
expect_exit "missing --format value is a usage error" 64 "$?"
DOCTOR_TEST_PYTHON="$HOST_PYTHON" HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
    /bin/bash "$BASE_FIXTURE/runtime/doctor.sh" --repo --format json \
    > "$TMP/option-repo.out" 2> "$TMP/option-repo.err"
expect_exit "option-shaped --repo value is a usage error" 64 "$?"
DOCTOR_TEST_PYTHON="$HOST_PYTHON" HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
    /bin/bash "$BASE_FIXTURE/runtime/doctor.sh" --format --repo "$BASE_FIXTURE/SECRET_REPO_PATH" \
    > "$TMP/option-format.out" 2> "$TMP/option-format.err"
expect_exit "option-shaped --format value is a usage error" 64 "$?"
DOCTOR_TEST_PYTHON="$HOST_PYTHON" HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
    /bin/bash "$BASE_FIXTURE/runtime/doctor.sh" --repo '' \
    > "$TMP/empty-repo.out" 2> "$TMP/empty-repo.err"
expect_exit "empty --repo value is a usage error" 64 "$?"
run_doctor "$BASE_FIXTURE" invalid-format --format yaml
expect_exit "unsupported format is a usage error" 64 "$?"
run_doctor "$BASE_FIXTURE" duplicate-cross --format json --repo "$BASE_FIXTURE/SECRET_REPO_PATH"
expect_exit "options cannot silently override run_doctor defaults" 64 "$?"
DOCTOR_TEST_PYTHON="$HOST_PYTHON" HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
    /bin/bash "$BASE_FIXTURE/runtime/doctor.sh" --help \
    > "$TMP/help.out" 2> "$TMP/help.err"
rc=$?
if [[ "$rc" == 64 && ! -s "$TMP/help.out" ]] && grep -Fxq 'usage: doctor.sh [--repo DIR] [--format text|json]' "$TMP/help.err"; then
    ok "help is bounded usage output with exit 64"
else
    bad "help is bounded usage output with exit 64"
fi

run_doctor "$BASE_FIXTURE" exact-calls
if [[ "$(cat "$TMP/exact-calls.agy-calls")" == '--version' ]]; then
    ok "doctor invokes only exact agy --version"
else
    bad "doctor invokes only exact agy --version"
fi
if [[ "$(wc -l < "$TMP/exact-calls.git-calls" | tr -d ' ')" == 3 ]] \
        && grep -Fxq 'git --version' "$TMP/exact-calls.git-calls" \
        && grep -Fq ' rev-parse --is-inside-work-tree' "$TMP/exact-calls.git-calls" \
        && grep -Fq ' worktree list --porcelain' "$TMP/exact-calls.git-calls"; then
    ok "doctor uses only bounded semantic git probes"
else
    bad "doctor uses only bounded semantic git probes"
fi
if [[ ! -e "$TMP/exact-calls.network" ]]; then
    ok "doctor invokes neither curl nor wget nor a network client"
else
    bad "doctor invokes neither curl nor wget nor a network client"
fi
if ! grep -Fq 'CONFIG_SECRET_DO_NOT_READ' "$TMP/exact-calls.out" "$TMP/exact-calls.err" \
        && ! grep -Fq 'GEMINI_SECRET_DO_NOT_READ' "$TMP/exact-calls.out" "$TMP/exact-calls.err"; then
    ok "doctor does not expose or silently scan personal configuration"
else
    bad "doctor does not expose or silently scan personal configuration"
fi
if ! grep -Eq '\.codex|\.gemini|settings\.json|credentials' \
        "$ROOT/doctor.sh" "$ROOT/skills/agy-worker/runtime/doctor.sh" \
        "$ROOT/skills/agy-worker/runtime/scripts/doctor-metadata.py"; then
    ok "doctor implementation has no personal-config discovery surface"
else
    bad "doctor implementation has no personal-config discovery surface"
fi
if ! grep -Eiq 'auth|dispatch|apply|repair|commit|push|pull|issue|release' \
        "$TMP/exact-calls.agy-calls" "$TMP/exact-calls.git-calls"; then
    ok "doctor performs no auth, repair, dispatch, or external action probe"
else
    bad "doctor performs no auth, repair, dispatch, or external action probe"
fi

run_doctor "$BASE_FIXTURE" no-leak-text
run_doctor "$BASE_FIXTURE" no-leak-json --format json
if ! grep -Fq "$TMP" "$TMP/no-leak-text.out" "$TMP/no-leak-text.err" \
        && ! grep -Fq 'SECRET_REPO_PATH' "$TMP/no-leak-text.out" "$TMP/no-leak-text.err" \
        && ! grep -Fq 'CONFIG_SECRET' "$TMP/no-leak-text.out" "$TMP/no-leak-text.err"; then
    ok "text output exposes no repository path, raw output, or fixture secret"
else
    bad "text output exposes no repository path, raw output, or fixture secret"
fi
if ! grep -Fq "$TMP" "$TMP/no-leak-json.out" "$TMP/no-leak-json.err" \
        && ! grep -Fq 'SECRET_REPO_PATH' "$TMP/no-leak-json.out" "$TMP/no-leak-json.err" \
        && ! grep -Fq 'GEMINI_SECRET' "$TMP/no-leak-json.out" "$TMP/no-leak-json.err"; then
    ok "JSON output exposes no repository path, raw output, or fixture secret"
else
    bad "JSON output exposes no repository path, raw output, or fixture secret"
fi

FAKE_AGY_MODE=drift FAKE_GIT_MODE=invalid-repo run_doctor "$BASE_FIXTURE" precedence --format json
rc=$?
if [[ "$rc" == 3 ]] && assert_json_contract "$TMP/precedence.out" not-ready 3; then
    ok "not-ready takes precedence over review-required"
else
    bad "not-ready takes precedence over review-required"
fi

if DOCTOR_TEST_PYTHON="$HOST_PYTHON" HOME=/private/tmp PATH="$BASE_FIXTURE/bin" \
        /bin/bash -c '
            source "$1"
            DOCTOR_WORK_DIR=""
            if doctor_prepare_workspace /private/tmp; then
                case "$DOCTOR_WORK_DIR" in /private/var/tmp/agy-worker-doctor.*) ;; *) exit 2 ;; esac
                doctor_cleanup_workspace
            else
                [[ -z "$DOCTOR_WORK_DIR" ]]
            fi
        ' _ "$BASE_FIXTURE/runtime/doctor.sh"; then
    ok "canonical HOME and repository aliases are excluded or fail closed"
else
    bad "canonical HOME and repository aliases are excluded or fail closed"
fi

if DOCTOR_TEST_PYTHON="$HOST_PYTHON" DOCTOR_AGY_CALLS="$TMP/source.agy-calls" \
        DOCTOR_GIT_CALLS="$TMP/source.git-calls" DOCTOR_NETWORK_MARKER="$TMP/source.network" \
        HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
        /bin/bash -c '
            source "$1"
            TMPDIR="caller-value"
            export TMPDIR
            trap ":" HUP
            trap "true" INT
            trap "false" TERM
            before_hup="$(trap -p HUP)"
            before_int="$(trap -p INT)"
            before_term="$(trap -p TERM)"
            doctor_main --repo "$2" >/dev/null 2>&1 || exit 1
            [[ "$TMPDIR" == caller-value && "$(trap -p HUP)" == "$before_hup" \
                && "$(trap -p INT)" == "$before_int" \
                && "$(trap -p TERM)" == "$before_term" ]]
        ' _ "$BASE_FIXTURE/runtime/doctor.sh" "$BASE_FIXTURE/SECRET_REPO_PATH"; then
    ok "sourced doctor restores caller TMPDIR and signal traps exactly"
else
    bad "sourced doctor restores caller TMPDIR and signal traps exactly"
fi

for signal_name in HUP INT TERM; do
    label="interrupt-$signal_name"
    DOCTOR_TEST_PYTHON="$HOST_PYTHON" \
    DOCTOR_AGY_CALLS="$TMP/$label.agy-calls" \
    DOCTOR_GIT_CALLS="$TMP/$label.git-calls" \
    DOCTOR_NETWORK_MARKER="$TMP/$label.network" \
    DOCTOR_TMP_OBSERVATIONS="$TMP/$label.tmp-observations" \
    DOCTOR_HANG_READY="$TMP/$label.hang-ready" \
    DOCTOR_AGY_PID="$TMP/$label.agy-pid" \
    DOCTOR_DESCENDANT_PID="$TMP/$label.descendant-pid" \
    FAKE_AGY_MODE=hang HOME="$BASE_FIXTURE/home" PATH="$BASE_FIXTURE/bin" \
        "$HOST_PYTHON" -B -c '
import os
import signal
import sys
for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, signal.SIG_DFL)
os.execv("/bin/bash", ["/bin/bash", sys.argv[1], "--repo", sys.argv[2], "--format", "json"])
' "$BASE_FIXTURE/runtime/doctor.sh" "$BASE_FIXTURE/SECRET_REPO_PATH" \
        > "$TMP/$label.out" 2> "$TMP/$label.err" &
    doctor_pid=$!
    ready=0
    for (( poll=0; poll<100; poll++ )); do
        if [[ -f "$TMP/$label.hang-ready" \
                && -s "$TMP/$label.agy-pid" \
                && -s "$TMP/$label.descendant-pid" ]]; then
            ready=1
            break
        fi
        /bin/sleep 0.05
    done
    if (( ready )); then
        kill -s "$signal_name" "$doctor_pid" 2>/dev/null || true
    fi
    completed=0
    for (( poll=0; poll<80; poll++ )); do
        if ! kill -0 "$doctor_pid" 2>/dev/null; then
            completed=1
            break
        fi
        /bin/sleep 0.05
    done
    if (( completed )); then
        wait "$doctor_pid"
        rc=$?
    else
        rc=99
    fi
    agy_pid="$(cat "$TMP/$label.agy-pid" 2>/dev/null || true)"
    descendant_pid="$(cat "$TMP/$label.descendant-pid" 2>/dev/null || true)"
    descendants_gone=0
    for (( poll=0; poll<40; poll++ )); do
        if ! kill -0 "$agy_pid" 2>/dev/null \
                && ! kill -0 "$descendant_pid" 2>/dev/null; then
            descendants_gone=1
            break
        fi
        /bin/sleep 0.05
    done
    workspace="$(sed -n 's/^git://p' "$TMP/$label.tmp-observations" | head -n 1)"
    if (( ready && completed && descendants_gone )) \
            && [[ "$rc" == 3 && ! -s "$TMP/$label.out" ]] \
            && grep -Fxq 'doctor: interrupted' "$TMP/$label.err" \
            && [[ -n "$workspace" && ! -e "$workspace" ]]; then
        ok "$signal_name interrupts the active process group and removes its private workspace"
    else
        bad "$signal_name interrupts the active process group and removes its private workspace"
        kill -KILL "$doctor_pid" "$agy_pid" "$descendant_pid" 2>/dev/null || true
        wait "$doctor_pid" 2>/dev/null || true
    fi
done

if [[ ! -e "$BASE_FIXTURE/runtime/__pycache__" \
        && ! -e "$BASE_FIXTURE/runtime/scripts/__pycache__" \
        && -z "$(find "$BASE_FIXTURE/tmp" -mindepth 1 -print -quit)" ]]; then
    ok "doctor disables bytecode and removes private version captures"
else
    bad "doctor disables bytecode and removes private version captures"
fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
