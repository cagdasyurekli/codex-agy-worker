#!/usr/bin/env bash
# Offline Codex package, skill-bundle, and landing-page contract tests.
set -uo pipefail

# Contract checks use Python assertions throughout this legacy shell harness.
# Keep inherited optimizer settings from silently disabling those checks.
unset PYTHONOPTIMIZE
# Repository-facing Python probes must not leave import caches behind. The dedicated
# negative-control subshell below explicitly unsets this when testing leak detection.
export PYTHONDONTWRITEBYTECODE=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
CI_OFFLINE="$ROOT/scripts/ci-offline.sh"
TMP="$(mktemp -d -t agyworker-packaging.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

ground_truth_phase_contract() {
    local fixture="$TMP/ground-truth-phase" rc
    mkdir -p "$fixture/bin" "$fixture/home/.gemini/antigravity-cli"
    printf '%s\n' \
        '#!/usr/bin/env bash' \
        'printf "%s\\n" "$*" >> "$GROUND_TRUTH_LOG"' \
        'case "$*" in' \
        '  --version) printf "%s\\n" "1.1.16" ;;' \
        '  --help) printf "%s\\n" "usage: agy [--output-format] [--print]" ;;' \
        '  models) printf "%s\\n" "model-a" ;;' \
        '  agents) printf "%s\\n" "agent-a" ;;' \
        '  "plugin list") printf "%s\\n" "plugin-a" ;;' \
        '  *) exit 97 ;;' \
        'esac' > "$fixture/bin/agy"
    chmod +x "$fixture/bin/agy"
    printf '%s\n' '{"permissions":{"allow":["command(git)"],"ask":[],"deny":[]}}' \
        > "$fixture/home/.gemini/antigravity-cli/settings.json"

    HOME="$fixture/home" PATH="$fixture/bin:$PATH" \
    GROUND_TRUTH_LOG="$fixture/interface.log" "$ROOT/ground-truth.sh" \
        > "$fixture/interface.out" 2> "$fixture/interface.err" || return 1
    [[ ! -s "$fixture/interface.err" ]] \
        && grep -Fxq 'interface' "$fixture/interface.out" \
        && ! grep -Fq 'account phase' "$fixture/interface.out" \
        && [[ "$(cat "$fixture/interface.log")" == $'--version\n--help' ]] \
        || return 1

    HOME="$fixture/home" PATH="$fixture/bin:$PATH" \
    GROUND_TRUTH_LOG="$fixture/account.log" "$ROOT/ground-truth.sh" --account \
        > "$fixture/account.out" 2> "$fixture/account.err" || return 1
    [[ ! -s "$fixture/account.err" ]] \
        && grep -Fxq 'account' "$fixture/account.out" \
        && grep -Fq 'models available to --model (account phase)' "$fixture/account.out" \
        && grep -Fq 'allow: [' "$fixture/account.out" \
        && [[ "$(cat "$fixture/account.log")" == $'--version\n--help\nmodels\nagents\nplugin list' ]] \
        || return 1

    HOME="$fixture/home" PATH="$fixture/bin:$PATH" \
    GROUND_TRUTH_LOG="$fixture/invalid.log" "$ROOT/ground-truth.sh" --invalid \
        > "$fixture/invalid.out" 2> "$fixture/invalid.err"
    rc=$?
    [[ "$rc" == 64 ]] \
        && [[ ! -s "$fixture/invalid.out" ]] \
        && grep -Fxq 'usage: ground-truth.sh [--account]' "$fixture/invalid.err" \
        && [[ ! -e "$fixture/invalid.log" ]]
}

echo "Codex distribution offline test suite"
echo

if python3 - "$ROOT" <<'PY'
from pathlib import Path
import os
import stat
import subprocess
import sys

root = Path(sys.argv[1])
listed = subprocess.run(
    ["git", "-C", str(root), "ls-files", "-z"],
    check=True,
    stdout=subprocess.PIPE,
).stdout.split(b"\0")
paths = {item.decode("utf-8") for item in listed if item}
paths.update((
    "scripts/models_capture_runner.py",
    "tests/test-models-capture-runner.py",
    "scripts/version_bootstrap_runner.py",
    "tests/test-version-bootstrap-runner.py",
    "scripts/version_initial_bootstrap_runner.py",
    "tests/test-version-initial-bootstrap-runner.py",
    "scripts/version_recovery_1_1_12_runner.py",
    "tests/test-version-recovery-1-1-12-runner.py",
    "scripts/models_capture_profile.py",
    "tests/test-models-capture-profile.py",
    "scripts/models_capture_1_1_12_profile.py",
    "scripts/models_capture_1_1_12_runner.py",
    "tests/test-models-capture-1-1-12.py",
    "tests/test-models-capture-1-1-12-profile.py",
    "tests/test-models-capture-1-1-12-runner.py",
    "scripts/models_capture_1_1_16_version_evidence.py",
    "scripts/models_capture_1_1_16_profile.py",
    "scripts/models_capture_1_1_16_runner.py",
    "tests/test-models-capture-1-1-16.py",
    "tests/test-models-capture-1-1-16-version-evidence.py",
    "tests/test-models-capture-1-1-16-profile.py",
    "tests/test-models-capture-1-1-16-runner.py",
    "scripts/models_capture_1_1_22_version_evidence.py",
    "scripts/models_capture_1_1_22_profile.py",
    "scripts/models_capture_1_1_22_runner.py",
    "tests/test-models-capture-1-1-22.py",
    "tests/test-models-capture-1-1-22-version-evidence.py",
    "tests/test-models-capture-1-1-22-profile.py",
    "tests/test-models-capture-1-1-22-runner.py",
    "scripts/models_capture_1_1_22_reprofile.py",
    "tests/test-models-capture-1-1-22-reprofile.py",
    "tests/test-agy-1-1-16-activation.py",
))
for relative in sorted(paths):
    path = root / relative
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        continue
    if stat.S_ISREG(info.st_mode):
        content = path.read_bytes()
    elif stat.S_ISLNK(info.st_mode):
        content = os.readlink(path).encode("utf-8", "surrogateescape")
    else:
        continue
    forbidden = b"/Users/" + b"cagdasyurekli/"
    assert forbidden not in content, relative
PY
then
    ok "public repository sources contain no personal absolute path"
else
    bad "public repository sources contain no personal absolute path"
fi

ci_workflow_contract() {
    python3 - "$1" <<'PY'
from hashlib import sha256
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
info = path.lstat()
assert stat.S_ISREG(info.st_mode)
data = path.read_bytes()
assert sha256(data).hexdigest() == "b61b97c538bafa8eeea377f76647122ec04c21aca06f36a9746132854cdf005d"
text = data.decode("utf-8")
required = (
    "name: test\n",
    "  pull_request:\n",
    "  workflow_dispatch:\n",
    "  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}\n",
    "  cancel-in-progress: true\n",
    "permissions:\n  contents: read\n",
    "    timeout-minutes: 60\n",
    "    timeout-minutes: 10\n",
    "      base_sha:\n",
    "      head_sha:\n",
    "      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd\n        with:\n          fetch-depth: 0\n          persist-credentials: false\n          ref: ${{ github.event_name == 'workflow_dispatch' && inputs.head_sha || github.event.pull_request.head.sha }}\n",
    "          ref: ${{ github.event_name == 'workflow_dispatch' && inputs.head_sha || github.event.pull_request.head.sha }}\n",
    "      - name: committed diff hygiene\n",
    "          AGY_WORKER_CI_EVENT_NAME: ${{ github.event_name == 'workflow_dispatch' && 'push' || github.event_name }}\n",
    "          AGY_WORKER_CI_BASE_SHA: ${{ github.event_name == 'workflow_dispatch' && inputs.base_sha || github.event.pull_request.base.sha }}\n",
    "          AGY_WORKER_CI_HEAD_SHA: ${{ github.event_name == 'workflow_dispatch' && inputs.head_sha || github.event.pull_request.head.sha }}\n",
    "        run: ./scripts/ci-diff-check.sh\n",
    "      - name: shard offline suite\n",
    "          RECEIPT_DIR: ${{ runner.temp }}/agyworker-shard-receipt-${{ matrix.shard }}\n",
    "          (umask 077 && mkdir \"$RECEIPT_DIR\")\n",
    "          chmod 0700 \"$RECEIPT_DIR\"\n",
    "          ./scripts/ci-offline.sh --shard \"${{ matrix.shard }}\" --receipt \"$RECEIPT_DIR/${{ matrix.shard }}.json\"\n",
    "      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02\n",
    "          name: shard-receipt-${{ matrix.shard }}\n",
    "          path: ${{ runner.temp }}/agyworker-shard-receipt-${{ matrix.shard }}/${{ matrix.shard }}.json\n",
    "          retention-days: 1\n",
    "  test:\n",
    "    name: test\n",
    "    if: always()\n",
    "    needs: [shard]\n",
    "      - name: download shard receipts\n",
    "        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093\n",
    "          pattern: shard-receipt-*\n",
    "          path: ${{ runner.temp }}/downloaded-shard-receipts\n",
    "      - name: verify aggregate shard receipts\n",
    "          PRODUCER_RESULT: ${{ needs.shard.result }}\n",
    "          RECEIPTS_DIR: ${{ runner.temp }}/downloaded-shard-receipts\n",
    "          /usr/bin/python3 -I -S -B scripts/ci_sharding.py verify-aggregate \\\n            --receipts-dir \"${RECEIPTS_DIR}\" \\\n            --expected-head \"${AGY_WORKER_CI_HEAD_SHA}\" \\\n            --producer-result \"${PRODUCER_RESULT}\"\n",
)
assert all(text.count(item) >= 1 for item in required)
assert "  push:\n" not in text
assert "git fetch" not in text
assert "run: git diff --check\n" not in text
assert "actions/checkout@v4" not in text
assert "\r" not in text
assert not text.endswith(("# timeout-minutes: 1\n", "# byte binding\n"))
assert "? if\n" not in text
assert "!policy" not in text
assert "&policy" not in text
assert "*policy" not in text
assert "continue-on-error: true" not in text
assert "mktemp -d -t agyworker-shard-receipt" not in text
assert "/tmp/agyworker-shard-receipt" not in text
assert text.count("timeout-minutes:") == 2
PY
}

workflow_checkout_policy_contract() {
    python3 - "$1" <<'PY'
from pathlib import Path
import stat
import sys

target = Path(sys.argv[1])
if target.is_file():
    workflow_files = [target]
elif target.is_dir():
    workflow_files = sorted(set(target.glob("*.yml")) | set(target.glob("*.yaml")))
else:
    raise AssertionError(f"Invalid target: {target}")

assert len(workflow_files) > 0, "No workflows found"
PINNED = "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd"
EXPECTED_BLOCKS = {
    "compatibility-watch.yml": (
        f"      - uses: {PINNED}\n"
        "        with:\n"
        "          persist-credentials: false\n"
    ),
    "feedback-watch.yml": (
        f"      - uses: {PINNED}\n"
        "        with:\n"
        "          persist-credentials: false\n"
    ),
    "test.yml": (
        f"      - uses: {PINNED}\n"
        "        with:\n"
        "          fetch-depth: 0\n"
        "          persist-credentials: false\n"
        "          ref: ${{ github.event_name == 'workflow_dispatch' && inputs.head_sha || github.event.pull_request.head.sha }}\n"
    ),
}
EXPECTED_COUNTS = {
    "compatibility-watch.yml": 1,
    "feedback-watch.yml": 1,
    "test.yml": 2,
}

if target.is_dir():
    assert set(EXPECTED_BLOCKS).issubset({path.name for path in workflow_files})

for w in workflow_files:
    info = w.lstat()
    assert stat.S_ISREG(info.st_mode)
    text = w.read_text(encoding="utf-8")
    checkout_count = text.casefold().count("actions/checkout@")
    expected = EXPECTED_BLOCKS.get(w.name)
    count = EXPECTED_COUNTS.get(w.name, 1)
    if expected is None:
        assert checkout_count == 0, f"{w.name} has an ungoverned checkout reference"
        continue
    assert checkout_count == count, f"{w.name} expected {count} checkout reference(s), found {checkout_count}"
    assert text.count(expected) == count, f"{w.name} checkout block differs from policy"
    assert text.count("persist-credentials:") == count, f"{w.name} credential policy is ambiguous"
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

ci_offline_contract() {
    python3 - "$1" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
required = (
    "set -eu\n",
    "git diff --check\n",
    "bash -n \"$file\"\n",
    'PYTHONPYCACHEPREFIX="$pycache"',
    "python3 -m py_compile conformance/v1/*.py scripts/*.py",
    "./tests/test-qa-gate.sh",
    "./tests/test-evidence-receipt.sh",
    "./tests/test-evidence-report.sh",
    "/usr/bin/python3 -I -S -B tests/test-benchmark.py",
    "/usr/bin/python3 -I -S -B tests/test-persona-evidence.py",
    "/usr/bin/python3 -I -S -B tests/test-job-lifecycle.py",
    "/usr/bin/python3 -I -S -B tests/test-workload-profiles.py",
    "./tests/test-agy-worker.sh",
    "/usr/bin/python3 -I -S -B tests/test-agy-worker-remediation.py",
    "./tests/test-update.sh",
    "/usr/bin/python3 -I -S -B tests/test-adoption-measurement.py",
    "/usr/bin/python3 -I -S -B tests/test-update-notifier.py",
    "/usr/bin/python3 -I -S -B tests/test-version-attestation-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-version-bootstrap-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-version-initial-bootstrap-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-version-recovery-1-1-12-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-version-attestation-harness.py",
    "/usr/bin/python3 -I -S -B tests/test-models-attestation-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-profile.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-profile.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-12-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-version-evidence.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-profile.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-16-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-version-evidence.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-profile.py",
    "/usr/bin/python3 -I -S -B tests/test-models-capture-1-1-22-runner.py",
    "/usr/bin/python3 -I -S -B tests/test-agy-1-1-16-activation.py",
    "./tests/test-reporting.sh",
    "/usr/bin/python3 -I -S -B tests/test-feedback-triage.py",
    "./tests/test-packaging.sh",
    "./tests/test-doctor.sh",
    "/usr/bin/python3 -I -S -B tests/test-conformance.py",
    "./tests/test-proof-demo.sh",
    "if announce 'repository bytecode hygiene'; then",
    "find . -type d -name __pycache__ -print -quit",
)
assert all(text.count(item) == 1 for item in required)
assert "HOME" not in text
assert not any(token in text for token in ("curl ", "wget ", "git fetch", "agy "))
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
        && workflow_checkout_policy_contract "$ROOT/.github/workflows" \
        && ci_helper_contract "$ROOT/scripts/ci-diff-check.sh" \
            "$ROOT/scripts/ci_diff_check.py" \
        && ci_offline_contract "$CI_OFFLINE" \
        && [[ -x "$ROOT/scripts/ci-diff-check.sh" ]] \
        && [[ -x "$ROOT/scripts/ci_diff_check.py" ]] \
        && [[ -x "$ROOT/scripts/ci_timing.py" ]] \
        && [[ -x "$ROOT/scripts/ci_sharding.py" ]] \
        && [[ -x "$ROOT/scripts/ci-offline.sh" ]]; then
    ok "PR CI verifies the exact committed range, cancels stale runs, and uses the canonical offline runner"
else
    bad "PR CI verifies the exact committed range, cancels stale runs, and uses the canonical offline runner"
fi

if /usr/bin/python3 -I -S -B "$ROOT/tests/test-ci-diff-check.py"; then
    ok "CI batch reader rejects malformed, unbounded, and interrupted streams"
else
    bad "CI batch reader rejects malformed, unbounded, and interrupted streams"
fi

if /usr/bin/python3 -I -S -B "$ROOT/tests/test-ci-timing.py"; then
    ok "CI timing telemetry records monotonic wall time, validates schema, and publishes owner-private reports"
else
    bad "CI timing telemetry records monotonic wall time, validates schema, and publishes owner-private reports"
fi

if /usr/bin/python3 -I -S -B "$ROOT/tests/test-ci-sharding.py"; then
    ok "CI sharding partition invariants, receipt publication, and fail-closed aggregate validation"
else
    bad "CI sharding partition invariants, receipt publication, and fail-closed aggregate validation"
fi

workflow_mutations="$TMP/workflow-mutations"
mkdir "$workflow_mutations"
python3 - "$ROOT/.github/workflows/test.yml" "$workflow_mutations" <<'PY'
from pathlib import Path
import sys

canonical = Path(sys.argv[1]).read_bytes()
directory = Path(sys.argv[2])

def replace_once(old: bytes, new: bytes) -> bytes:
    assert canonical.count(old) == 1
    return canonical.replace(old, new)

mutations = {
    "job-if.yml": replace_once(b"  test:\n", b"  test:\n    if: false\n"),
    "step-if.yml": replace_once(b"      - name: committed diff hygiene\n        env:\n", b"      - name: committed diff hygiene\n        if: false\n        env:\n"),
    "job-continue-on-error.yml": replace_once(
        b"    timeout-minutes: 60\n",
        b"    timeout-minutes: 60\n    continue-on-error: true\n",
    ),
    "step-continue-on-error.yml": replace_once(
        b"      - name: committed diff hygiene\n        env:\n",
        b"      - name: committed diff hygiene\n        continue-on-error: true\n        env:\n",
    ),
    "step-timeout.yml": replace_once(
        b"      - name: committed diff hygiene\n        env:\n",
        b"      - name: committed diff hygiene\n        timeout-minutes: 1\n        env:\n",
    ),
    "commented-timeout.yml": canonical + b"\n# timeout-minutes: 1\n",
    "duplicate-timeout.yml": replace_once(
        b"    timeout-minutes: 60\n",
        b"    timeout-minutes: 60\n    timeout-minutes: 1\n",
    ),
    "alternate-timeout.yml": replace_once(
        b"    timeout-minutes: 60\n", b"    timeout-minutes: 1\n"
    ),
    "explicit-if.yml": canonical + b"\n? if\n: false\n",
    "tagged.yml": canonical + b"\npolicy_marker: !policy false\n",
    "anchored.yml": canonical + b"\npolicy_marker: &policy false\n",
    "alias.yml": canonical + b"\npolicy_anchor: &policy false\npolicy_marker: *policy\n",
    "single-byte.yml": b"m" + canonical[1:],
    "trailing-comment.yml": canonical + b"\n# byte binding\n",
    "crlf.yml": canonical.replace(b"\n", b"\r\n"),
}
for name, data in mutations.items():
    assert data != canonical
    (directory / name).write_bytes(data)
PY
workflow_mutations_rejected=true
for workflow in "$workflow_mutations"/*.yml; do
    if ci_workflow_contract "$workflow" 2>/dev/null; then
        workflow_mutations_rejected=false
        break
    fi
done
if [[ "$workflow_mutations_rejected" == true ]]; then
    ok "workflow policy rejects byte-level YAML and formatting mutations"
else
    bad "workflow policy rejects byte-level YAML and formatting mutations"
fi

if ! ci_workflow_contract "$TMP/missing-workflow.yml" 2>/dev/null; then
    ok "workflow policy fails closed for a missing workflow"
else
    bad "workflow policy fails closed for a missing workflow"
fi

ln -s "$ROOT/.github/workflows/test.yml" "$TMP/workflow-link.yml"
if ! ci_workflow_contract "$TMP/workflow-link.yml" 2>/dev/null; then
    ok "workflow policy fails closed for a symlinked workflow"
else
    bad "workflow policy fails closed for a symlinked workflow"
fi

mkdir "$TMP/workflow-directory.yml"
if ! ci_workflow_contract "$TMP/workflow-directory.yml" 2>/dev/null; then
    ok "workflow policy fails closed for a nonregular workflow"
else
    bad "workflow policy fails closed for a nonregular workflow"
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

cp "$ROOT/.github/workflows/test.yml" "$TMP/persisted-checkout-credentials.yml"
python3 - "$TMP/persisted-checkout-credentials.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "          persist-credentials: false\n"
assert text.count(old) == 2
path.write_text(text.replace(old, "", 1), encoding="utf-8")
PY
if ! ci_workflow_contract "$TMP/persisted-checkout-credentials.yml" 2>/dev/null; then
    ok "workflow policy rejects persisted checkout credentials"
else
    bad "workflow policy rejects persisted checkout credentials"
fi

cp "$ROOT/.github/workflows/test.yml" "$TMP/mutable-checkout-tag.yml"
python3 - "$TMP/mutable-checkout-tag.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd"
assert text.count(old) == 2
path.write_text(text.replace(old, "actions/checkout@v4", 1), encoding="utf-8")
PY
if ! ci_workflow_contract "$TMP/mutable-checkout-tag.yml" 2>/dev/null \
        && ! workflow_checkout_policy_contract "$TMP/mutable-checkout-tag.yml" 2>/dev/null; then
    ok "workflow policy rejects a mutable checkout tag"
else
    bad "workflow policy rejects a mutable checkout tag"
fi

cp "$ROOT/.github/workflows/test.yml" "$TMP/different-checkout-sha.yml"
python3 - "$TMP/different-checkout-sha.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd"
assert text.count(old) == 2
path.write_text(text.replace(old, "actions/checkout@1111111111111111111111111111111111111111", 1), encoding="utf-8")
PY
if ! ci_workflow_contract "$TMP/different-checkout-sha.yml" 2>/dev/null \
        && ! workflow_checkout_policy_contract "$TMP/different-checkout-sha.yml" 2>/dev/null; then
    ok "workflow policy rejects a different checkout SHA"
else
    bad "workflow policy rejects a different checkout SHA"
fi

cp "$ROOT/.github/workflows/test.yml" "$TMP/extra-unprotected-checkout.yml"
python3 - "$TMP/extra-unprotected-checkout.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "      - name: committed diff hygiene\n"
assert text.count(old) == 1
path.write_text(
    text.replace(old, "      - uses: \"Actions/Checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd\"\n" + old),
    encoding="utf-8",
)
PY
if ! ci_workflow_contract "$TMP/extra-unprotected-checkout.yml" 2>/dev/null \
        && ! workflow_checkout_policy_contract "$TMP/extra-unprotected-checkout.yml" 2>/dev/null; then
    ok "workflow policy rejects a quoted case-variant extra checkout step"
else
    bad "workflow policy rejects a quoted case-variant extra checkout step"
fi

mkdir "$TMP/workflow-policy-mutations"
for wf in "$ROOT/.github/workflows"/*.yml; do
    cp "$wf" "$TMP/workflow-policy-mutations/"
done

python3 - "$TMP/workflow-policy-mutations/compatibility-watch.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
p.write_text(t.replace("actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd", "actions/checkout@v4"), encoding="utf-8")
PY
if ! workflow_checkout_policy_contract "$TMP/workflow-policy-mutations" 2>/dev/null; then
    ok "workflow checkout policy rejects mutable checkout tag in any workflow"
else
    bad "workflow checkout policy rejects mutable checkout tag in any workflow"
fi
cp "$ROOT/.github/workflows/compatibility-watch.yml" "$TMP/workflow-policy-mutations/compatibility-watch.yml"

python3 - "$TMP/workflow-policy-mutations/feedback-watch.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
p.write_text(t.replace("actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd", "actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), encoding="utf-8")
PY
if ! workflow_checkout_policy_contract "$TMP/workflow-policy-mutations" 2>/dev/null; then
    ok "workflow checkout policy rejects different checkout SHA in any workflow"
else
    bad "workflow checkout policy rejects different checkout SHA in any workflow"
fi
cp "$ROOT/.github/workflows/feedback-watch.yml" "$TMP/workflow-policy-mutations/feedback-watch.yml"

python3 - "$TMP/workflow-policy-mutations/compatibility-watch.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
p.write_text(
    t.replace("          persist-credentials: false\n", "          fetch-depth: 1 # persist-credentials: false\n"),
    encoding="utf-8",
)
PY
if ! workflow_checkout_policy_contract "$TMP/workflow-policy-mutations" 2>/dev/null; then
    ok "workflow checkout policy rejects a credential marker hidden in a comment"
else
    bad "workflow checkout policy rejects a credential marker hidden in a comment"
fi
cp "$ROOT/.github/workflows/compatibility-watch.yml" "$TMP/workflow-policy-mutations/compatibility-watch.yml"

python3 - "$TMP/workflow-policy-mutations/feedback-watch.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
old = "        with:\n          persist-credentials: false\n"
new = (
    "        env:\n"
    "          CHECKOUT_PERSIST_CREDENTIALS: \"false\" # persist-credentials: false\n"
)
assert t.count(old) == 1
p.write_text(t.replace(old, new), encoding="utf-8")
PY
if ! workflow_checkout_policy_contract "$TMP/workflow-policy-mutations" 2>/dev/null; then
    ok "workflow checkout policy rejects credentials mis-scoped under env"
else
    bad "workflow checkout policy rejects credentials mis-scoped under env"
fi
cp "$ROOT/.github/workflows/feedback-watch.yml" "$TMP/workflow-policy-mutations/feedback-watch.yml"

python3 - "$TMP/workflow-policy-mutations/feedback-watch.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
p.write_text(t.replace("      - name: Summarize", "      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd\n      - name: Summarize"), encoding="utf-8")
PY
if ! workflow_checkout_policy_contract "$TMP/workflow-policy-mutations" 2>/dev/null; then
    ok "workflow checkout policy rejects extra unprotected checkout step in any workflow"
else
    bad "workflow checkout policy rejects extra unprotected checkout step in any workflow"
fi

python3 - "$TMP/workflow-policy-mutations/unexpected.yaml" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(
    'jobs:\n  unexpected:\n    steps:\n      - uses: "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd"\n',
    encoding="utf-8",
)
PY
if ! workflow_checkout_policy_contract "$TMP/workflow-policy-mutations" 2>/dev/null; then
    ok "workflow checkout policy rejects checkout in an unexpected YAML workflow"
else
    bad "workflow checkout policy rejects checkout in an unexpected YAML workflow"
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
assert manifest["version"] == "0.11.0"
assert manifest["skills"] == "./skills/"
assert manifest["license"] == "MIT"
assert manifest["interface"]["privacyPolicyURL"].startswith("https://")
assert manifest["interface"]["termsOfServiceURL"].startswith("https://")
assert not ({"apps", "mcpServers", "hooks"} & manifest.keys())
PY
then ok "Codex plugin is a skills-only package with public legal links"; else bad "Codex plugin is a skills-only package with public legal links"; fi

if python3 - "$ROOT" "$TMP/marketplace-contract" <<'PY'
import json
from pathlib import Path
import shutil
import stat
import sys

source_root = Path(sys.argv[1])
fixture = Path(sys.argv[2])
shutil.copytree(source_root / ".agents", fixture / ".agents")
shutil.copytree(source_root / ".codex-plugin", fixture / ".codex-plugin")
shutil.copytree(source_root / "skills", fixture / "skills")


def require_regular(path: Path) -> None:
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode), path


def require_directory(path: Path) -> None:
    info = path.lstat()
    assert stat.S_ISDIR(info.st_mode), path


def validate(root: Path) -> None:
    marketplace_path = root / ".agents/plugins/marketplace.json"
    manifest_path = root / ".codex-plugin/plugin.json"
    require_regular(marketplace_path)
    require_regular(manifest_path)
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert set(marketplace) == {"name", "interface", "plugins"}
    assert marketplace["name"] == "codex-agy-worker"
    assert marketplace["interface"] == {"displayName": "Codex agy Worker"}
    assert isinstance(marketplace["plugins"], list) and len(marketplace["plugins"]) == 1
    entry = marketplace["plugins"][0]
    assert set(entry) == {"name", "source", "policy", "category"}
    assert entry["name"] == manifest["name"] == "codex-agy-worker"
    assert entry["source"] == {"source": "local", "path": "."}
    assert entry["policy"] == {
        "installation": "AVAILABLE", "authentication": "ON_INSTALL"
    }
    assert entry["category"] == "Developer Tools"
    assert manifest["skills"] == "./skills/"

    skill_root = root / "skills/agy-worker"
    runtime_root = skill_root / "runtime"
    require_directory(root)
    require_directory(root / "skills")
    require_directory(skill_root)
    require_directory(runtime_root)
    require_regular(skill_root / "SKILL.md")
    assert [path.relative_to(root).as_posix() for path in root.rglob("SKILL.md")] == [
        "skills/agy-worker/SKILL.md"
    ]
    assert [path.relative_to(root).as_posix() for path in root.rglob("runtime") if path.is_dir()] == [
        "skills/agy-worker/runtime"
    ]
    assert not (root / "plugins").exists()


def rejected(root: Path) -> bool:
    try:
        validate(root)
    except (AssertionError, FileNotFoundError, IsADirectoryError, json.JSONDecodeError):
        return True
    return False


validate(fixture)
marketplace_path = fixture / ".agents/plugins/marketplace.json"
manifest_path = fixture / ".codex-plugin/plugin.json"
original_marketplace = marketplace_path.read_bytes()
original_manifest = manifest_path.read_bytes()

payload = json.loads(original_marketplace)
payload["plugins"][0]["name"] = "wrong-name"
marketplace_path.write_text(json.dumps(payload), encoding="utf-8")
assert rejected(fixture), "marketplace must reject a plugin-manifest name mismatch"

marketplace_path.write_bytes(original_marketplace)
manifest_path.unlink()
assert rejected(fixture), "marketplace must reject a missing plugin manifest"
manifest_path.symlink_to(source_root / ".codex-plugin/plugin.json")
assert rejected(fixture), "marketplace must reject a symlinked plugin manifest"
manifest_path.unlink()
manifest_path.write_bytes(original_manifest)

payload = json.loads(original_marketplace)
payload["plugins"][0]["source"]["path"] = "../escape"
marketplace_path.write_text(json.dumps(payload), encoding="utf-8")
assert rejected(fixture), "marketplace must reject a source-path escape"

linked_source = fixture / "linked-source"
linked_source.symlink_to(fixture / "skills", target_is_directory=True)
payload["plugins"][0]["source"]["path"] = "linked-source"
marketplace_path.write_text(json.dumps(payload), encoding="utf-8")
assert rejected(fixture), "marketplace must reject a symlinked source path"

marketplace_path.write_bytes(original_marketplace)
linked_source.unlink()
source_root_link = fixture.parent / "marketplace-root-symlink"
source_root_link.symlink_to(fixture, target_is_directory=True)
assert rejected(source_root_link), "marketplace must reject a symlinked root source"
source_root_link.unlink()
duplicate = fixture / "plugins/codex-agy-worker/skills"
duplicate.parent.mkdir(parents=True)
shutil.copytree(fixture / "skills", duplicate)
assert rejected(fixture), "marketplace must reject duplicate skill/runtime sources"
PY
then
    ok "root-source marketplace binds one canonical package and rejects source/name/layout mutants"
else
    bad "root-source marketplace contract"
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
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/doctor-metadata.py" ]] \
        && ground_truth_phase_contract; then
    ok "root/portable doctor and ground-truth phases preserve their read-only boundary"
else
    bad "root/portable doctor and ground-truth phases preserve their read-only boundary"
fi

if [[ -x "$ROOT/feedback-triage.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/feedback-triage.sh" ]] \
        && [[ -x "$ROOT/scripts/feedback-triage.py" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/feedback-triage.py" ]] \
        && cmp -s "$ROOT/scripts/feedback-triage.py" \
            "$ROOT/skills/agy-worker/runtime/scripts/feedback-triage.py"; then
    ok "root and portable packages include byte-identical bounded feedback triage"
else
    bad "root and portable packages include byte-identical bounded feedback triage"
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

if [[ -x "$ROOT/evidence-report.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/evidence-report.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/evidence_report.py" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/recommendation_record.py" ]]; then
    ok "root and portable packages include the pure Evidence Report renderer"
else
    bad "root and portable packages include the pure Evidence Report renderer"
fi

if [[ -x "$ROOT/job.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/job.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/job_lifecycle.py" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/candidate_state.py" ]] \
        && [[ -f "$ROOT/skills/agy-worker/runtime/schemas/job-state.schema.json" ]]; then
    ok "root and portable packages include the safe local job lifecycle"
else
    bad "root and portable packages include the safe local job lifecycle"
fi

if [[ -x "$ROOT/agy-worker.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/agy-worker.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch.py" ]] \
        && [[ -f "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch_worktree.py" ]] \
        && [[ ! -x "$ROOT/skills/agy-worker/runtime/scripts/agy_dispatch_worktree.py" ]]; then
    ok "root and portable packages include the progress-aware local dispatcher"
else
    bad "root and portable packages include the progress-aware local dispatcher"
fi

if [[ -x "$ROOT/benchmark.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/benchmark.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/benchmark.py" ]] \
        && [[ -f "$ROOT/benchmarks/v1/manifest.json" ]] \
        && cmp -s "$ROOT/benchmarks/v1/manifest.json" \
            "$ROOT/skills/agy-worker/runtime/benchmarks/v1/manifest.json" \
        && cmp -s "$ROOT/benchmarks/v1/portable-source.json" \
            "$ROOT/skills/agy-worker/runtime/benchmarks/v1/portable-source.json" \
        && [[ -f "$ROOT/skills/agy-worker/runtime/schemas/benchmark-plan.schema.json" ]] \
        && [[ -f "$ROOT/skills/agy-worker/runtime/schemas/benchmark-result.schema.json" ]]; then
    ok "root and portable packages include offline Benchmark v1"
else
    bad "root and portable packages include offline Benchmark v1"
fi

if [[ -x "$ROOT/swebench-workflow-study.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/swebench-workflow-study.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/swebench_workflow_study.py" ]] \
        && grep -Fq 'swebench-workflow-study.sh' "$ROOT/skills/agy-worker/scripts/resolve-pipeline.sh" \
        && grep -Fq 'scripts/swebench_workflow_study.py' "$ROOT/skills/agy-worker/runtime/doctor.sh" \
        && [[ -f "$ROOT/skills/agy-worker/runtime/schemas/swebench-workflow-study-plan.schema.json" ]] \
        && [[ -f "$ROOT/skills/agy-worker/runtime/schemas/swebench-workflow-study-report.schema.json" ]] \
        && [[ -f "$ROOT/skills/agy-worker/runtime/schemas/swebench-workflow-study-advisory.schema.json" ]] \
        && grep -Fq '"additionalProperties":false' "$ROOT/skills/agy-worker/runtime/schemas/swebench-workflow-study-plan.schema.json" \
        && grep -Fq '"exact_bindings_verified"' "$ROOT/skills/agy-worker/runtime/schemas/swebench-workflow-study-report.schema.json"; then
    ok "root and portable packages include SWE-bench Workflow Study v1"
else
    bad "root and portable packages include SWE-bench Workflow Study v1"
fi

if python3 - "$ROOT" "$TMP" <<'PY'
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys

root = Path(sys.argv[1])
temporary = Path(sys.argv[2])
runtime = root / "skills/agy-worker/runtime"
root_manifest = root / "benchmarks/v1/portable-source.json"
portable_manifest = runtime / "benchmarks/v1/portable-source.json"
raw = root_manifest.read_bytes()
assert raw == portable_manifest.read_bytes()
payload = json.loads(raw)
expected = {
    "benchmark.sh", "qa-gate.sh", "verify-job.sh",
    "scripts/benchmark.py", "scripts/candidate_state.py",
    "scripts/compatibility.py", "scripts/evidence_receipt.py",
    "scripts/model_selection.py", "scripts/recommendation_record.py",
    "scripts/validate-envelope.py", "schemas/benchmark-plan.schema.json",
    "schemas/benchmark-result.schema.json", "schemas/evidence-receipt.schema.json",
    "schemas/worker-result.schema.json", "schemas/worker-result.provider.schema.json",
}

def valid(value, source):
    try:
        if set(value) != {"files", "kind", "schema_version", "source_revision"}:
            return False
        if value["kind"] != "agy-worker-benchmark-portable-source" or value["schema_version"] != 1:
            return False
        files = value["files"]
        if not isinstance(files, list) or len(files) != len(expected):
            return False
        names = []
        for item in files:
            if set(item) != {"path", "mode", "sha256"}:
                return False
            name, mode, digest = item["path"], item["mode"], item["sha256"]
            if not isinstance(name, str) or "\\" in name:
                return False
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or str(pure) != name:
                return False
            names.append(name)
            if mode not in {"100644", "100755"} or not isinstance(digest, str) or len(digest) != 64:
                return False
            path = source.joinpath(*pure.parts)
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                return False
            if "100" + format(stat.S_IMODE(info.st_mode), "03o") != mode:
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return False
        return len(set(names)) == len(names) and set(names) == expected
    except (OSError, TypeError, ValueError):
        return False

assert valid(payload, runtime)
mutant = copy.deepcopy(payload); mutant["files"][0]["path"] = "../benchmark.sh"
assert not valid(mutant, runtime)
mutant = copy.deepcopy(payload); mutant["files"].pop()
assert not valid(mutant, runtime)
mutant = copy.deepcopy(payload); mutant["files"].append(copy.deepcopy(mutant["files"][0]))
assert not valid(mutant, runtime)
mutant = copy.deepcopy(payload); mutant["files"][0]["mode"] = "100600"
assert not valid(mutant, runtime)
mutant = copy.deepcopy(payload); mutant["files"][0]["sha256"] = "0" * 64
assert not valid(mutant, runtime)

fixture = temporary / "portable-source-fixture"
shutil.copytree(runtime, fixture)
missing = fixture / "scripts/model_selection.py"
missing.unlink()
assert not valid(payload, fixture)
shutil.copytree(runtime, fixture, dirs_exist_ok=True)
link = fixture / "scripts/model_selection.py"
link.unlink()
os.symlink("benchmark.py", link)
assert not valid(payload, fixture)
link.unlink()
shutil.copy2(runtime / "scripts/model_selection.py", link)
with link.open("ab") as handle:
    handle.write(b"# bounded packaging tamper\n")
assert not valid(payload, fixture)
PY
then
    ok "portable source manifest binds every exact runtime file and rejects bounded drift"
else
    bad "portable source manifest binds every exact runtime file and rejects bounded drift"
fi

if [[ -x "$ROOT/profile.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/profile.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/workload_profiles.py" ]] \
        && [[ -f "$ROOT/skills/agy-worker/runtime/schemas/workload-profile.schema.json" ]] \
        && cmp -s "$ROOT/profiles/v1/manifest.json" \
            "$ROOT/skills/agy-worker/runtime/profiles/v1/manifest.json" \
        && cmp -s "$ROOT/profiles/v1/bounded-test-backfill.json" \
            "$ROOT/skills/agy-worker/runtime/profiles/v1/bounded-test-backfill.json" \
        && cmp -s "$ROOT/profiles/v1/diff-review.json" \
            "$ROOT/skills/agy-worker/runtime/profiles/v1/diff-review.json" \
        && cmp -s "$ROOT/profiles/v1/repository-inventory.json" \
            "$ROOT/skills/agy-worker/runtime/profiles/v1/repository-inventory.json"; then
    ok "root and portable packages include data-only Workload Profiles v1"
else
    bad "root and portable packages include data-only Workload Profiles v1"
fi

if grep -Fq 'is process-owning: it keeps signal rollback authority' \
        "$ROOT/skills/agy-worker/runtime/scripts/evidence_report.py" \
        && grep -Fq 'The `--output` CLI path is deliberately process-owning' \
            "$ROOT/README.md" \
        && grep -Fq 'file-output `main(argv)` is process-owning through `os._exit(0)`' \
            "$ROOT/docs/REPO_MAP.md"; then
    ok "Evidence Report documents its process-owning file-output boundary"
else
    bad "Evidence Report documents its process-owning file-output boundary"
fi

if grep -Fq 'REPORT_FORMATS = ("text", "json", "markdown", "github-step-summary")' \
        "$ROOT/skills/agy-worker/runtime/scripts/evidence_report.py" \
        && ! grep -Eq 'GITHUB_STEP_SUMMARY|os\.environ' \
            "$ROOT/skills/agy-worker/runtime/scripts/evidence_report.py"; then
    ok "portable Evidence Report owns exact CI-safe formats without environment discovery"
else
    bad "portable Evidence Report owns exact CI-safe formats without environment discovery"
fi

if grep -Fq -- '--format github-step-summary >> "${GITHUB_STEP_SUMMARY:?}"' \
        "$ROOT/README.md" \
        && grep -Fq 'fork-controlled paths, repository content, tokens, or secrets' \
            "$ROOT/README.md"; then
    ok "README keeps GitHub Step Summary redirection explicit and fork-safe"
else
    bad "README keeps GitHub Step Summary redirection explicit and fork-safe"
fi

if grep -Fq 'or implicit environment-file write was added' "$ROOT/docs/ROADMAP.md" \
        && grep -Fq 'never discovers or writes `GITHUB_STEP_SUMMARY`' \
            "$ROOT/PRIVACY.md"; then
    ok "roadmap and privacy docs bound the local-only CI reporter surface"
else
    bad "roadmap and privacy docs bound the local-only CI reporter surface"
fi

required_runtime_dependencies=(
    agy-worker.sh
    job.sh
    qa-gate.sh
    verify-job.sh
    evidence-report.sh
    benchmark.sh
    swebench-workflow-study.sh
    persona-evidence.sh
    profile.sh
    model-recommendation.sh
    model-selection.sh
    doctor.sh
    feedback-triage.sh
    scripts/validate-envelope.py
    scripts/evidence_receipt.py
    scripts/evidence_report.py
    scripts/benchmark.py
    scripts/swebench_workflow_study.py
    scripts/persona_registry.py
    scripts/workload_profiles.py
    scripts/recommendation_record.py
    scripts/model-recommendation.py
    scripts/model_selection.py
    scripts/compatibility.py
    scripts/candidate_state.py
    scripts/agy_dispatch.py
    scripts/agy_dispatch_worktree.py
    scripts/job_lifecycle.py
    scripts/doctor-metadata.py
    scripts/feedback-triage.py
    schemas/worker-result.schema.json
    schemas/worker-result.provider.schema.json
    schemas/evidence-receipt.schema.json
    schemas/model-selection.schema.json
    schemas/model-recommendation.schema.json
    schemas/job-state.schema.json
    schemas/benchmark-plan.schema.json
    schemas/benchmark-result.schema.json
    schemas/swebench-workflow-study-plan.schema.json
    schemas/swebench-workflow-study-report.schema.json
    schemas/swebench-workflow-study-advisory.schema.json
    schemas/persona-dispatch.schema.json
    schemas/persona-human-review.schema.json
    schemas/persona-run-evidence.schema.json
    schemas/persona-run-manifest.schema.json
    schemas/persona-tool-attestation.schema.json
    schemas/persona-transition-approval.schema.json
    schemas/persona-verifier.schema.json
    schemas/persona-version-attestation.schema.json
    schemas/workload-profile.schema.json
    compat/persona-evidence.schema.json
    compat/persona-registry.schema.json
    compat/personas/manifest.json
    compat/personas/bulk-test-writer.json
    compat/personas/diff-reviewer.json
    compat/personas/repo-inventory.json
    benchmarks/v1/manifest.json
    benchmarks/v1/portable-source.json
    benchmarks/v1/tasks/exact-edit/initial.txt
    benchmarks/v1/tasks/exact-edit/candidate.txt
    benchmarks/v1/tasks/exact-edit/envelope.json
    benchmarks/v1/variants/bulk.json
    profiles/v1/manifest.json
    profiles/v1/bounded-test-backfill.json
    profiles/v1/diff-review.json
    profiles/v1/repository-inventory.json
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

for parent in scripts agents schemas compat benchmarks profiles; do
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
    'job.sh:executable' \
    'qa-gate.sh:executable' \
    'verify-job.sh:executable' \
    'evidence-report.sh:executable' \
    'benchmark.sh:executable' \
    'swebench-workflow-study.sh:executable' \
    'persona-evidence.sh:executable' \
    'profile.sh:executable' \
    'scripts/validate-envelope.py:executable' \
    'scripts/evidence_receipt.py:executable' \
    'scripts/evidence_report.py:executable' \
    'scripts/benchmark.py:executable' \
    'scripts/swebench_workflow_study.py:executable' \
    'scripts/persona_registry.py:executable' \
    'scripts/workload_profiles.py:executable' \
    'scripts/recommendation_record.py:executable' \
    'scripts/candidate_state.py:executable' \
    'scripts/agy_dispatch.py:executable' \
    'scripts/agy_dispatch_worktree.py:data' \
    'scripts/job_lifecycle.py:executable' \
    'scripts/model_selection.py:executable' \
    'schemas/worker-result.schema.json:data' \
    'schemas/evidence-receipt.schema.json:data' \
    'schemas/job-state.schema.json:data' \
    'schemas/benchmark-plan.schema.json:data' \
    'schemas/benchmark-result.schema.json:data' \
    'schemas/swebench-workflow-study-plan.schema.json:data' \
    'schemas/swebench-workflow-study-report.schema.json:data' \
    'schemas/swebench-workflow-study-advisory.schema.json:data' \
    'schemas/persona-run-manifest.schema.json:data' \
    'schemas/persona-transition-approval.schema.json:data' \
    'schemas/workload-profile.schema.json:data' \
    'compat/persona-evidence.schema.json:data' \
    'compat/persona-registry.schema.json:data' \
    'compat/personas/manifest.json:data' \
    'benchmarks/v1/manifest.json:data' \
    'benchmarks/v1/portable-source.json:data' \
    'profiles/v1/manifest.json:data' \
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

if grep -Fq './tests/test-doctor.sh' "$CI_OFFLINE" \
        && grep -Fq 'runs-on: macos-latest' "$ROOT/.github/workflows/test.yml"; then
    ok "macOS CI runs the dedicated offline doctor suite"
else
    bad "macOS CI runs the dedicated offline doctor suite"
fi

if python3 - "$ROOT/.github/workflows/feedback-watch.yml" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
required = (
    'name: feedback-watch\n',
    '    - cron: "23 8 * * 1"\n',
    '  workflow_dispatch:\n',
    '    runs-on: ubuntu-latest\n',
    '  contents: read\n',
    '  issues: read\n',
    '      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd\n',
    '          persist-credentials: false\n',
    '          GH_TOKEN: ${{ github.token }}\n',
    '          GH_PROMPT_DISABLED: "1"\n',
    'summary="$(./feedback-triage.sh fetch)"\n',
    'Read-only aggregate: no issue writes, comments, labels, closes, creates, dispatches, or agent input."\n',
)
forbidden = ('--paginate', 'issue create', 'issue comment', 'issue edit', 'issue close', 'gh api repos', 'actions/checkout@v4')
assert all(text.count(item) == 1 for item in required)
assert not any(item in text for item in forbidden)
PY
then
    ok "weekly feedback watch is fixed, read-only, and metadata-only"
else
    bad "weekly feedback watch is fixed, read-only, and metadata-only"
fi

if grep -Fq '/usr/bin/python3 -I -S -B tests/test-benchmark.py' \
        "$CI_OFFLINE" \
        && grep -Fq '[`benchmark.sh`](docs/BENCHMARKING.md)' "$ROOT/README.md" \
        && grep -Fq 'Live benchmarking is not implemented' "$ROOT/docs/BENCHMARKING.md" \
        && grep -Fq 'no live provider mode' "$ROOT/docs/index.md"; then
    ok "CI and public docs expose only provider-independent Benchmark v1"
else
    bad "CI and public docs expose only provider-independent Benchmark v1"
fi

if grep -Fq '/usr/bin/python3 -I -S -B tests/test-swebench-workflow-study.py' "$CI_OFFLINE" \
        && grep -Fq 'SWE-bench Workflow Study v1' "$ROOT/README.md" \
        && grep -Fq 'never influences `qa-gate` acceptance' "$ROOT/README.md" ; then
    ok "CI and public docs expose SWE-bench Workflow Study v1"
else
    bad "CI and public docs expose SWE-bench Workflow Study v1"
fi

if grep -Fq '/usr/bin/python3 -I -S -B tests/test-persona-evidence.py' \
        "$CI_OFFLINE" \
        && [[ -x "$ROOT/persona-evidence.sh" \
            && -x "$ROOT/skills/agy-worker/runtime/persona-evidence.sh" \
            && -x "$ROOT/skills/agy-worker/runtime/scripts/persona_registry.py" ]] \
        && grep -Fq 'Statuses are evidence levels, not trust labels' "$ROOT/README.md" \
        && grep -Fq 'For the shipped `offline-only` records it is a local,' \
            "$ROOT/docs/PERSONAS.md" \
        && grep -Fq 'fixed sanitized read-only Git object commands' \
            "$ROOT/docs/PERSONAS.md" \
        && grep -Fq 'not publish or revalidate the private evidence' \
            "$ROOT/docs/PERSONAS.md"; then
    ok "CI and public docs expose the non-authoritative persona registry"
else
    bad "CI and public docs expose the non-authoritative persona registry"
fi

if grep -Fq '/usr/bin/python3 -I -S -B tests/test-workload-profiles.py' \
        "$CI_OFFLINE" \
        && [[ -x "$ROOT/profile.sh" \
            && -x "$ROOT/skills/agy-worker/runtime/profile.sh" \
            && -x "$ROOT/skills/agy-worker/runtime/scripts/workload_profiles.py" ]] \
        && grep -Fq '[`profile.sh`](docs/PROFILES.md)' "$ROOT/README.md" \
        && grep -Fq 'Profiles never contain a repository or filesystem path' \
            "$ROOT/docs/PROFILES.md" \
        && grep -Fq 'profile is data, not a driver' "$ROOT/AGENTS.md"; then
    ok "CI and public docs expose only fixed data-only workload profiles"
else
    bad "CI and public docs expose only fixed data-only workload profiles"
fi

if grep -Fq './tests/test-evidence-report.sh' "$CI_OFFLINE" \
        && grep -Fq 'runs-on: macos-latest' "$ROOT/.github/workflows/test.yml"; then
    ok "macOS CI runs the dedicated offline Evidence Report suite"
else
    bad "macOS CI runs the dedicated offline Evidence Report suite"
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
mkdir -p "$workflow_compile_fixture/conformance/v1" \
    "$workflow_compile_fixture/scripts" \
    "$workflow_compile_fixture/skills/agy-worker/runtime/scripts"
cp "$ROOT/conformance/v1/run.py" "$workflow_compile_fixture/conformance/v1/run.py"
cp "$ROOT/scripts/compatibility.py" "$workflow_compile_fixture/scripts/compatibility.py"
cp "$ROOT/skills/agy-worker/runtime/scripts/model_selection.py" \
    "$workflow_compile_fixture/skills/agy-worker/runtime/scripts/model_selection.py"
(
    cd "$workflow_compile_fixture" || exit 1
    PYTHONPYCACHEPREFIX="$TMP/workflow-python-cache" \
        python3 -m py_compile conformance/v1/*.py scripts/*.py skills/*/runtime/scripts/*.py
)
workflow_compile_rc=$?
python3 -B - "$CI_OFFLINE" <<'PY'
from pathlib import Path
import re
import sys

script = Path(sys.argv[1]).read_text(encoding="utf-8")
assert script.count('PYTHONPYCACHEPREFIX="$pycache"') == 1
assert script.count('python3 -m py_compile conformance/v1/*.py scripts/*.py') == 1
assert 'runner.temp' not in script
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
        && [[ -f "$ROOT/conformance/v1/envelopes/honest.json" ]] \
        && [[ ! -L "$ROOT/conformance/v1/envelopes/honest.json" ]] \
        && grep -Fq 'conformance/v1/envelopes' "$ROOT/proof-demo.sh" \
        && [[ ! -e "$ROOT/demo/fixtures/honest-envelope.json" ]] \
        && [[ ! -e "$ROOT/demo/fixtures/scope-mismatch-envelope.json" ]] \
        && [[ ! -e "$ROOT/skills/agy-worker/runtime/proof-demo.sh" ]]; then
    ok "repository package binds starter proof to the public conformance subset"
else
    bad "repository package binds starter proof to the public conformance subset"
fi

if [[ -x "$ROOT/conformance/run.sh" ]] \
        && [[ -x "$ROOT/conformance/v1/run.py" ]] \
        && [[ -f "$ROOT/conformance/v1/manifest.json" ]] \
        && [[ -f "$ROOT/docs/CONFORMANCE.md" ]] \
        && grep -Fq 'MANIFEST_SHA256 = "9741584060f5391e5a79df1022c9cd574c28fdddefc75006b8b6e7ff0e5e36a0"' \
            "$ROOT/conformance/v1/run.py" \
        && grep -Fq 'fixture compatibility only' "$ROOT/README.md" \
        && grep -Fq 'security certification' "$ROOT/docs/CONFORMANCE.md" \
        && grep -Fq '/usr/bin/python3 -I -S -B tests/test-conformance.py' \
            "$CI_OFFLINE"; then
    ok "distribution includes the bounded non-certifying v1 conformance contract"
else
    bad "distribution includes the bounded non-certifying v1 conformance contract"
fi

if grep -Fq './tests/test-proof-demo.sh' "$CI_OFFLINE"; then
    ok "macOS CI runs the dedicated offline starter-proof suite"
else
    bad "macOS CI runs the dedicated offline starter-proof suite"
fi

if grep -Fq './tests/test-evidence-receipt.sh' "$CI_OFFLINE"; then
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
        && cmp -s "$ROOT/compat/agy-models-inventory-binding.json" \
            "$ROOT/skills/agy-worker/runtime/compat/agy-models-inventory-binding.json" \
        && cmp -s "$ROOT/compat/agy-models-inventory-binding.sha256" \
            "$ROOT/skills/agy-worker/runtime/compat/agy-models-inventory-binding.sha256" \
        && [[ "$(<"$ROOT/compat/agy-verified-version.txt")" == "1.1.22" ]] \
        && [[ "$(<"$ROOT/compat/agy-last-reviewed.txt")" == "2026-08-28" ]]; then
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

if python3 -B - "$ROOT/skills/agy-worker/runtime/schemas/model-selection.schema.json" \
        "$ROOT/skills/agy-worker/runtime/compat/agy-verified-version.txt" <<'PY'
import copy
import json
import sys

schema = json.load(open(sys.argv[1], encoding="utf-8"))
matrix_version = open(sys.argv[2], encoding="ascii").read().strip()

required = {
    "legacy tier selection": {
        "schema_version", "kind", "selection_mode", "selected_tier",
        "selected_tier_source", "resolved_agy_model",
    },
    "unreconciled literal model selection": {
        "schema_version", "kind", "selection_mode", "user_model",
        "user_model_source", "resolved_agy_model", "compatibility_status",
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
    "v2 exact reviewed model selection": {
        "schema_version", "kind", "selection_mode", "user_model", "user_model_source",
        "resolved_agy_model", "installed_agy_version", "matrix_sha256",
        "matrix_agy_version", "matrix_source_revision", "version_relation",
        "compatibility_status", "critical_interface_probe_version",
        "critical_interface_status", "critical_capabilities_sha256", "help_sha256",
        "model_availability", "probed_executable",
    },
    "v2 reviewed model and effort selection": {
        "schema_version", "kind", "selection_mode", "user_model", "user_model_source",
        "user_effort", "user_effort_source", "resolved_agy_model",
        "installed_agy_version", "matrix_sha256", "matrix_agy_version",
        "matrix_source_revision", "version_relation", "compatibility_status",
        "critical_interface_probe_version", "critical_interface_status",
        "critical_capabilities_sha256", "help_sha256", "model_availability",
        "probed_executable",
    },
    "v3 approved drift exact-model selection": {
        "schema_version", "kind", "selection_mode", "user_model", "user_model_source",
        "resolved_agy_model", "installed_agy_version", "matrix_sha256",
        "matrix_agy_version", "matrix_source_revision", "version_relation",
        "compatibility_status", "critical_interface_probe_version",
        "critical_interface_status", "critical_capabilities_sha256", "help_sha256",
        "model_availability", "probed_executable", "compatibility_disposition",
        "approved_help_sha256", "compatibility_decision_sha256",
    },
    "v3 approved drift model and effort selection": {
        "schema_version", "kind", "selection_mode", "user_model", "user_model_source",
        "user_effort", "user_effort_source", "resolved_agy_model",
        "installed_agy_version", "matrix_sha256", "matrix_agy_version",
        "matrix_source_revision", "version_relation", "compatibility_status",
        "critical_interface_probe_version", "critical_interface_status",
        "critical_capabilities_sha256", "help_sha256", "model_availability",
        "probed_executable", "compatibility_disposition", "approved_help_sha256",
        "compatibility_decision_sha256",
    },
}
forbidden = {
    "legacy tier selection": {
        "user_model", "user_model_source", "user_effort", "user_effort_source",
        "installed_agy_version", "matrix_sha256", "matrix_agy_version",
        "matrix_source_revision",
        "compatibility_status", "version_relation", "critical_interface_probe_version",
        "critical_interface_status", "critical_capabilities_sha256", "help_sha256",
        "model_availability", "probed_executable", "compatibility_disposition",
        "approved_help_sha256", "compatibility_decision_sha256",
    },
    "unreconciled literal model selection": {
        "selected_tier", "selected_tier_source", "user_effort", "user_effort_source",
        "installed_agy_version", "matrix_sha256", "matrix_agy_version",
        "matrix_source_revision",
        "version_relation", "critical_interface_probe_version", "critical_interface_status",
        "critical_capabilities_sha256", "help_sha256", "model_availability", "probed_executable",
        "compatibility_disposition", "approved_help_sha256", "compatibility_decision_sha256",
    },
    "exact reviewed model selection": {
        "selected_tier", "selected_tier_source", "user_effort", "user_effort_source",
        "compatibility_status", "version_relation", "critical_interface_probe_version",
        "critical_interface_status", "critical_capabilities_sha256", "help_sha256",
        "model_availability", "probed_executable", "compatibility_disposition",
        "approved_help_sha256", "compatibility_decision_sha256",
    },
    "reviewed model and effort selection": {
        "selected_tier", "selected_tier_source", "compatibility_status",
        "version_relation", "critical_interface_probe_version", "critical_interface_status",
        "critical_capabilities_sha256", "help_sha256", "model_availability", "probed_executable",
        "compatibility_disposition", "approved_help_sha256", "compatibility_decision_sha256",
    },
    "v2 exact reviewed model selection": {
        "selected_tier", "selected_tier_source", "user_effort", "user_effort_source",
        "compatibility_disposition", "approved_help_sha256",
        "compatibility_decision_sha256",
    },
    "v2 reviewed model and effort selection": {
        "selected_tier", "selected_tier_source",
        "compatibility_disposition", "approved_help_sha256",
        "compatibility_decision_sha256",
    },
    "v3 approved drift exact-model selection": {
        "selected_tier", "selected_tier_source", "user_effort", "user_effort_source",
    },
    "v3 approved drift model and effort selection": {
        "selected_tier", "selected_tier_source",
    },
}

def assert_strict(value):
    assert value["additionalProperties"] is False
    assert set(value["required"]) == {"schema_version", "kind", "selection_mode"}
    assert "reviewed_help_sha256" not in value["properties"]
    binding = value["properties"]["probed_executable"]
    assert binding["additionalProperties"] is False
    assert set(binding["required"]) == {
        "path_sha256", "target_lstat", "symlink_chain", "components",
    }
    assert binding["properties"]["content_sha256"] == {
        "type": "string", "pattern": "^[0-9a-f]{64}$",
    }
    lstat = binding["properties"]["target_lstat"]
    assert lstat["additionalProperties"] is False
    assert set(lstat["required"]) == {
        "device", "inode", "mode", "uid", "gid", "size", "mtime_ns",
    }
    assert lstat["properties"]["ctime_ns"] == {"type": "integer", "minimum": 0}
    variants = {variant["title"]: variant for variant in value["oneOf"]}
    assert set(variants) == set(required)
    assert value["properties"]["schema_version"]["enum"] == [1, 2, 3]
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
    assert '"const": "implicit-default"' in tier_conditions
    assert tier_conditions.count('"const": "default"') >= 2
    expected_relation = {
        "oneOf": [
            {"properties": {
                "installed_agy_version": {"const": matrix_version},
                "matrix_agy_version": {"const": matrix_version},
                "version_relation": {"const": "match"},
                "compatibility_status": {"const": "reviewed-version-match"},
            }},
            {"properties": {
                "installed_agy_version": {"not": {"const": matrix_version}},
                "matrix_agy_version": {"const": matrix_version},
                "version_relation": {"const": "drift"},
                "compatibility_status": {"const": "critical-interface-compatible-version-drift"},
            }},
        ],
    }
    expected_v3_relation = {
        "properties": {
            "installed_agy_version": {"not": {"const": matrix_version}},
            "matrix_agy_version": {"const": matrix_version},
            "version_relation": {"const": "drift"},
            "compatibility_status": {"const": "critical-interface-compatible-version-drift"},
        },
    }
    assert value.get("definitions") == {
        "v2_version_relation": expected_relation,
        "v3_approved_help": expected_v3_relation,
    }
    for title in ("v2 exact reviewed model selection", "v2 reviewed model and effort selection"):
        assert variants[title]["allOf"] == [{"$ref": "#/definitions/v2_version_relation"}]
    for title in ("v3 approved drift exact-model selection", "v3 approved drift model and effort selection"):
        assert variants[title]["allOf"] == [{"$ref": "#/definitions/v3_approved_help"}]
        props = variants[title]["properties"]
        assert props["schema_version"] == {"const": 3}
        assert props["compatibility_disposition"] == {"const": "proceed"}

def relation_accepts(record):
    relation = schema["definitions"]["v2_version_relation"]
    for variant in relation["oneOf"]:
        if any(key not in record for key in variant.get("required", ())):
            continue
        accepted = True
        for field, rule in variant["properties"].items():
            if "const" in rule and record.get(field) != rule["const"]:
                accepted = False
            if "not" in rule and record.get(field) == rule["not"]["const"]:
                accepted = False
        if accepted:
            return True
    return False

assert_strict(schema)
match = {
    "installed_agy_version": matrix_version, "matrix_agy_version": matrix_version,
    "version_relation": "match", "compatibility_status": "reviewed-version-match",
}
drift = {
    "installed_agy_version": "9.9.9", "matrix_agy_version": matrix_version,
    "version_relation": "drift",
    "compatibility_status": "critical-interface-compatible-version-drift",
}
assert relation_accepts(match) and relation_accepts(drift)
for invalid in (
    {**match, "version_relation": "drift"},
    {**match, "compatibility_status": "critical-interface-compatible-version-drift"},
    {**drift, "version_relation": "match"},
    {**drift, "compatibility_status": "reviewed-version-match"},
    {**match, "matrix_agy_version": "9.9.9", "installed_agy_version": "9.9.9"},
):
    assert not relation_accepts(invalid)
mutants = []
mutant = copy.deepcopy(schema)
mutant["additionalProperties"] = True
mutants.append(mutant)
mutant = copy.deepcopy(schema)
mutant["oneOf"][4]["required"].remove("help_sha256")
mutants.append(mutant)
mutant = copy.deepcopy(schema)
mutant["oneOf"][0]["not"]["anyOf"] = mutant["oneOf"][0]["not"]["anyOf"][1:]
mutants.append(mutant)
mutant = copy.deepcopy(schema)
mutant["oneOf"][0]["allOf"] = mutant["oneOf"][0]["allOf"][:1]
mutants.append(mutant)
mutant = copy.deepcopy(schema)
mutant["definitions"]["v2_version_relation"]["oneOf"][1]["properties"]["version_relation"]["const"] = "match"
mutants.append(mutant)
mutant = copy.deepcopy(schema)
mutant["definitions"]["v2_version_relation"]["oneOf"][0]["required"] = []
mutants.append(mutant)
for mutant in mutants:
    try:
        assert_strict(mutant)
    except (AssertionError, KeyError, TypeError):
        continue
    raise AssertionError("weakened selection schema mutant was accepted")
PY
then
    ok "selection schema derives v2 relation/status and rejects required, forbidden, conditional, and extra-field weakening"
else
    bad "selection schema derives v2 relation/status and rejects required, forbidden, conditional, and extra-field weakening"
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

PATH="$TMP/receipt-no-network-bin:$PATH" NETWORK_MARKER="$TMP/receipt-network-called" \
    "$copied_pipeline/evidence-report.sh" \
    --receipt "$portable_receipt_parent/receipt.json" --format text \
    > "$TMP/portable-report.out" 2> "$TMP/portable-report.err"
portable_report_rc=$?
if [[ "$portable_report_rc" == 0 && ! -s "$TMP/portable-report.err" ]] \
        && grep -Fq 'Verdict: gate-passed' "$TMP/portable-report.out" \
        && grep -Fq 'Human review: required' "$TMP/portable-report.out" \
        && [[ ! -e "$TMP/receipt-network-called" ]]; then
    ok "skill-folder-only copy renders a bounded receipt offline"
else
    bad "skill-folder-only copy renders a bounded receipt offline"
fi

mkdir -p "$TMP/selector-bin"
printf '%s\n' '#!/usr/bin/env bash' \
    'case "$*" in' \
    '  --version) printf "1.1.22\n" ;;' \
    '  --help) printf "%s\n" "Usage of agy:" "  --add-dir  Add a directory" "  --conversation  Resume a conversation" "  --disable-slash-commands  Disable slash commands" "  --json-schema  Schema path" "  --mode  Execution mode (accept-edits, plan)" "  --model  Select a model" "  --output-format  Format (text, json, stream-json)" "  --print  Run a prompt" "  --print-timeout  Print timeout" "  --sandbox  Sandboxed" >&2 ;;' \
    '  *) exit 97 ;;' \
    'esac' > "$TMP/selector-bin/agy"
chmod +x "$TMP/selector-bin/agy"
PATH="$TMP/selector-bin:$PATH" NETWORK_MARKER="$TMP/network-called" \
    "$copied_pipeline/model-selection.sh" --model gemini-3.6-flash --effort high \
    > "$TMP/copied-selection.json" 2> "$TMP/copied-selection.err"
rc=$?
copied_selection_v2=0
if python3 -B - "$TMP/copied-selection.json" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
assert record["schema_version"] == 2
assert not ({"compatibility_disposition", "approved_help_sha256", "compatibility_decision_sha256"} & set(record))
PY
then
    copied_selection_v2=1
fi
if [[ "$rc" == 0 ]] \
        && grep -Fq '"resolved_agy_model": "gemini-3.6-flash-high"' \
            "$TMP/copied-selection.json" \
        && grep -Fq '"matrix_sha256": "5a363dee8acb35e91b60405e705e8afaf155989dd755027cc5fa16741e42436c"' \
            "$TMP/copied-selection.json" \
        && [[ "$copied_selection_v2" == 1 ]] \
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

marketplace_installed_parity() {
    python3 - "$1" "$2" "$3" <<'PY'
from hashlib import sha256
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1]) / "skills/agy-worker"
installed = Path(sys.argv[2])
tampered = Path(sys.argv[3])

def snapshot(root: Path) -> dict[str, bytes]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == ".pipeline-root":
            continue
        assert not path.is_symlink(), path
        result[relative] = sha256(path.read_bytes()).digest()
    return result

source_snapshot = snapshot(source)
assert source_snapshot == snapshot(installed)
shutil.copytree(installed, tampered)
runtime = tampered / "runtime/agy-worker.sh"
runtime.write_bytes(runtime.read_bytes() + b"\n# marketplace tamper fixture\n")
assert source_snapshot != snapshot(tampered)
PY
}
if [[ "$installed_root" == "$(cd "$ROOT" && pwd -P)" ]]; then
    ok "standalone install resolves the checkout without rewriting SKILL.md"
else
    bad "standalone install resolves the checkout without rewriting SKILL.md"
fi
if [[ -x "$ROOT/agy-worker.sh" ]] \
        && grep -Fq 'skills/agy-worker/runtime/agy-worker.sh' "$ROOT/agy-worker.sh" \
        && [[ -x "$TMP/installed/agy-worker/runtime/doctor.sh" ]] \
        && [[ -x "$TMP/installed/agy-worker/runtime/scripts/agy_dispatch.py" ]] \
        && [[ -f "$TMP/installed/agy-worker/runtime/scripts/agy_dispatch_worktree.py" ]] \
        && [[ ! -x "$TMP/installed/agy-worker/runtime/scripts/agy_dispatch_worktree.py" ]] \
        && [[ -x "$TMP/installed/agy-worker/runtime/scripts/doctor-metadata.py" ]] \
        && grep -Fq '`"$PIPELINE/scripts/agy_dispatch.py"`' "$TMP/installed/agy-worker/SKILL.md" \
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
            "$TMP/installed/agy-worker/runtime/compat/agy-model-effort-matrix.sha256" \
        && marketplace_installed_parity "$ROOT" "$TMP/installed/agy-worker" \
            "$TMP/tampered-installed-marketplace-skill"; then
    ok "root wrapper and installed skill preserve runtime authority, complete parity, and tamper evidence"
else
    bad "root wrapper and installed skill preserve runtime dispatcher authority and compatibility bytes"
fi

governance_clauses=(
    'For material UX, lifecycle, trust-boundary, security, data-semantics, or other domain plans:'
    'A coordinator and suitable domain expert must co-plan.'
    'Freeze user journeys, acceptance tests, and authority/privacy constraints before implementation.'
    'The final acceptor must be a different agent or fresh context; no planner or implementer may self-accept.'
    'Purely mechanical changes are exempt.'
    'Verification v2 and the controller bind candidate evidence, not agent identity or governance.'
    'The final human-readable handoff must report the planner/reviewer separation.'
)

governance_skill_contract() {
    local skill_path="$1" clause
    for clause in "${governance_clauses[@]}"; do
        [[ "$(grep -Fxc "$clause" "$skill_path")" == "1" ]] || return 1
    done
}

if [[ "$installed_root" == "$(cd "$ROOT" && pwd -P)" ]] \
        && governance_skill_contract "$TMP/installed/agy-worker/SKILL.md"; then
    ok "installed skill preserves independent material-plan governance and handoff disclosure"
else
    bad "installed skill preserves independent material-plan governance and handoff disclosure"
fi

governance_mutants_rejected=1
governance_mutant_index=0
for clause in "${governance_clauses[@]}"; do
    governance_mutant_index=$((governance_mutant_index + 1))
    mutant="$TMP/governance-skill-mutant-$governance_mutant_index.md"
    if ! python3 -B - "$TMP/installed/agy-worker/SKILL.md" "$mutant" "$clause" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
clause = sys.argv[3]
text = source.read_text(encoding="utf-8")
if text.count(clause) != 1:
    raise SystemExit(1)
target.write_text(text.replace(clause, "", 1), encoding="utf-8")
PY
    then
        governance_mutants_rejected=0
        break
    fi
    if governance_skill_contract "$mutant"; then
        governance_mutants_rejected=0
        break
    fi
done
if [[ "$governance_mutants_rejected" == "1" ]]; then
    ok "installed governance contract rejects every independent clause deletion"
else
    bad "installed governance contract rejects every independent clause deletion"
fi

provider_notice_clauses=(
    'Before every provider-launch attempt (initial start/run, resume, continue, and restart), tell the user in one or two concise user-facing sentences what task is being sent to AGY.'
    'Include a short public-safe task label, caller-selected model information, caller-selected effort when separately selectable, and the exact resolved model slug.'
    'For default selection where no model is selected or the default tier is used, state truthfully that the provider default model is used and that model or effort is unresolved, without inventing a resolved slug or thinking level.'
    'For fixed/compound/literal models where effort is not separately selectable, state that accurately without inferring backend reasoning or inventing a thinking level.'
    'The notice must precede every dispatch attempt and remain accurate afterward.'
    'If preflight fails before provider launch, explicitly state that the task was not sent to AGY.'
    'If provider reach is genuinely uncertain, state that it is unverified rather than claiming success.'
    'Direct model and effort selection remain caller-owned; recommendations are advisory.'
)

provider_notice_skill_contract() {
    local skill_path="$1" clause
    for clause in "${provider_notice_clauses[@]}"; do
        [[ "$(grep -Fxc "$clause" "$skill_path")" == "1" ]] || return 1
    done
}

if [[ "$installed_root" == "$(cd "$ROOT" && pwd -P)" ]] \
        && provider_notice_skill_contract "$TMP/installed/agy-worker/SKILL.md" \
        && provider_notice_skill_contract "$ROOT/skills/agy-worker/SKILL.md"; then
    ok "installed skill preserves user-facing provider dispatch notice and boundary contract"
else
    bad "installed skill preserves user-facing provider dispatch notice and boundary contract"
fi

provider_notice_mutants_rejected=1
provider_notice_mutant_index=0
for clause in "${provider_notice_clauses[@]}"; do
    provider_notice_mutant_index=$((provider_notice_mutant_index + 1))
    mutant="$TMP/provider-notice-skill-mutant-$provider_notice_mutant_index.md"
    if ! python3 -B - "$TMP/installed/agy-worker/SKILL.md" "$mutant" "$clause" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
clause = sys.argv[3]
text = source.read_text(encoding="utf-8")
if text.count(clause) != 1:
    raise SystemExit(1)
target.write_text(text.replace(clause, "", 1), encoding="utf-8")
PY
    then
        provider_notice_mutants_rejected=0
        break
    fi
    if provider_notice_skill_contract "$mutant"; then
        provider_notice_mutants_rejected=0
        break
    fi
done
if [[ "$provider_notice_mutants_rejected" == "1" ]]; then
    ok "installed provider notice contract rejects every independent clause deletion"
else
    bad "installed provider notice contract rejects every independent clause deletion"
fi

provider_notice_weakening_mutants_rejected=1
provider_notice_weakening_mutant_index=0
weakening_replacements=(
    'tell the user in one or two concise user-facing sentences::tell the user if convenient'
    'caller-selected model information, caller-selected effort::default model information'
    'state truthfully that the provider default model is used and that model or effort is unresolved::invent a model slug'
    'without inferring backend reasoning or inventing a thinking level::inferring backend reasoning'
    'precede every dispatch attempt::follow completion of the job'
    'explicitly state that the task was not sent to AGY::state that the task was sent to AGY'
    'state that it is unverified rather than claiming success::claim success'
    'Direct model and effort selection remain caller-owned::Direct model selection is superseded'
)
for pair in "${weakening_replacements[@]}"; do
    provider_notice_weakening_mutant_index=$((provider_notice_weakening_mutant_index + 1))
    old_fragment="${pair%%::*}"
    new_fragment="${pair##*::}"
    mutant="$TMP/provider-notice-weakened-$provider_notice_weakening_mutant_index.md"
    if ! python3 -B - "$TMP/installed/agy-worker/SKILL.md" "$mutant" "$old_fragment" "$new_fragment" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
old_frag = sys.argv[3]
new_frag = sys.argv[4]
text = source.read_text(encoding="utf-8")
if text.count(old_frag) != 1:
    raise SystemExit(1)
target.write_text(text.replace(old_frag, new_frag, 1), encoding="utf-8")
PY
    then
        provider_notice_weakening_mutants_rejected=0
        break
    fi
    if provider_notice_skill_contract "$mutant"; then
        provider_notice_weakening_mutants_rejected=0
        break
    fi
done
if [[ "$provider_notice_weakening_mutants_rejected" == "1" ]]; then
    ok "installed provider notice contract rejects every clause weakening mutation"
else
    bad "installed provider notice contract rejects every clause weakening mutation"
fi

if grep -Fq 'Before every provider-launch attempt (initial `run`/`start`, `resume`, `continue`, and' \
        "$ROOT/README.md" \
        && grep -Fq 'mandatory user-facing provider dispatch notices across initial, resume, continue, and restart launches' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq '## Transparent provider dispatch notice and truthful boundaries' \
            "$ROOT/docs/lessons_learned.md"; then
    ok "package documentation describes mandatory user-facing provider dispatch notice contract"
else
    bad "package documentation describes mandatory user-facing provider dispatch notice contract"
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

governance_docs_contract() {
    /usr/bin/python3 -I -S -B - "$CI_OFFLINE" "$ROOT/CONTRIBUTING.md" \
        "$ROOT/.github/pull_request_template.md" <<'PY'
from pathlib import Path
import re
import sys

ci = Path(sys.argv[1]).read_text(encoding="utf-8")
contributing = Path(sys.argv[2]).read_text(encoding="utf-8")
template = Path(sys.argv[3]).read_text(encoding="utf-8")
suite_commands = re.findall(r"^\./tests/test-[^\s]+\.sh$", ci, re.MULTILINE)
suite_commands += re.findall(
    r"^/usr/bin/python3 -I -S -B tests/test-[^\s]+\.py$", ci, re.MULTILINE
)

valid = (
    len(suite_commands) == len(set(suite_commands)) == 40
    and all(command in contributing for command in suite_commands)
    and template.count("./scripts/ci-offline.sh") == 1
    and not any(command in template for command in suite_commands)
    and "Human diff review completed" in template
    and all(
        phrase in template
        for phrase in (
            "No worker-reported command or test was treated as evidence.",
            "No permission, authentication, privacy, or path-policy boundary was weakened.",
            "No commit, push, merge, release, issue submission, or external setting change",
        )
    )
)
if not valid:
    raise SystemExit(1)
PY
}

if governance_docs_contract \
        && grep -Fq 'The forty offline suites' "$ROOT/README.md" \
        && grep -Fq 'all forty offline suites' "$ROOT/CONTRIBUTING.md" \
        && grep -Fq 'Adoption measurement: 41 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'Local update notifier: 89 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-adoption-measurement.py 41-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-update-notifier.py 89-case' "$ROOT/README.md" \
        && [[ -f "$ROOT/docs/MEASUREMENT.md" ]] \
        && [[ -x "$ROOT/update-notifier.sh" ]] \
        && grep -Fq 'Google/Gemini' "$ROOT/PRIVACY.md" \
        && grep -Fq 'logs/' "$ROOT/PRIVACY.md" \
        && grep -Fq 'GitHub Issues' "$ROOT/SUPPORT.md" \
        && grep -Fq 'not legal advice' "$ROOT/TERMS.md"; then
    ok "governance keeps one canonical PR gate, targeted diagnostics, and public policy boundaries"
else
    bad "governance keeps one canonical PR gate, targeted diagnostics, and public policy boundaries"
fi

if grep -Fq '`--compatibility-disposition proceed --approve-help-sha SHA256`' \
        "$ROOT/README.md" \
        && grep -Fq 'An exact matrix-version match proceeds mechanically' \
            "$ROOT/README.md" \
        && grep -Fq 'that structural probe. Compatible version drift requires Codex' \
            "$ROOT/README.md" \
        && ! grep -Fq 'only when its raw C-locale help SHA-256 is retained' "$ROOT/README.md" \
        && ! grep -Fq 'An unseen exact-version digest, or compatible version drift' "$ROOT/README.md" \
        && grep -Fq "\`LC_ALL=C agy --help 2>&1 | /usr/bin/python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'\`" \
            "$ROOT/README.md" \
        && ! grep -Fq 'shasum -a 256' "$ROOT/README.md" \
            "$ROOT/README.md" \
        && grep -Fq 'controller help prose is data, never availability inference' "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq '`model_availability: not_assessed`' "$ROOT/README.md" \
        && grep -Fq 'V3/V4 *current* bound result may perform its first lifecycle transition' "$ROOT/README.md" \
        && grep -Fq 'migration_binding_sha256' "$ROOT/README.md" \
        && grep -Fq 'V5/V6 transition proves the legacy' "$ROOT/README.md" \
        && grep -Fq 'caller-resolved symbolic launcher' "$ROOT/README.md" \
        && grep -Fq 'caller-resolved symbolic launcher `"$PIPELINE/agy-worker.sh"`' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'deterministic external state path under' "$ROOT/README.md" \
        && grep -Fq 'deterministic external state root derivation' "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'prospective and post-resolution fail-closed rejection of project roots inside the target worktree' "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq '`status`, `wait`, `result`, `resume`, `restart`,' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Every emitted action or stale-approval rerun command uses' \
            "$ROOT/skills/agy-worker/SKILL.md" \
        && grep -Fq 'tests/test-agy-worker.sh      334-case' "$ROOT/README.md" \
        && [[ "$(grep -Fc '`tests/test-agy-worker.sh` (334 cases)' "$ROOT/docs/REPO_MAP.md")" == 2 ]] \
        && grep -Fq 'tests/test-agy-worker-remediation.py 90-case' "$ROOT/README.md" \
        && grep -Fq 'EXPECTED_CHECKS = 90' "$ROOT/tests/test-agy-worker-remediation.py" \
        && grep -Fq '`tests/test-agy-worker-remediation.py` (90 focused cases)' "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'tests/test-doctor.sh          257-case' "$ROOT/README.md" \
        && grep -Fq '`tests/test-doctor.sh` (257 cases)' "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Local update notifier: 89 offline; Doctor: 257 offline; Packaging: 429 offline.' "$ROOT/AGENTS.md" \
        && grep -Fq 'PYTHONDONTWRITEBYTECODE=1 python3 -B - "$TMP/legacy-v1.status"' \
            "$ROOT/tests/test-agy-worker.sh" \
        && ! grep -Fq '&& python3 - "$TMP/legacy-v1.status"' "$ROOT/tests/test-agy-worker.sh" \
        && ! grep -Fq 'tests/test-agy-worker.sh      300-case' "$ROOT/README.md" \
        && ! grep -Fq '`tests/test-agy-worker.sh` (300 cases)' "$ROOT/docs/REPO_MAP.md" \
        && ! grep -Fq 'resolution remains blocked until installed agy exactly matches' \
            "$ROOT/README.md"; then
    ok "dispatcher docs describe compatible direct selection, v9 migration, no-bytecode legacy import, and registered focused coverage"
else
    bad "dispatcher docs describe compatible direct selection, v9 migration, no-bytecode legacy import, and registered focused coverage"
fi

bootstrap_preflight_line="$(grep -nF "if announce 'repository-only version bootstrap runtime preflight'; then" \
    "$CI_OFFLINE" | cut -d: -f1)"
bootstrap_suite_line="$(grep -nF "if announce 'repository-only version bootstrap runner'; then" \
    "$CI_OFFLINE" | cut -d: -f1)"
if grep -Fq 'Canonical version-attestation runner: 165 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-version-attestation-runner.py  165-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-version-attestation-runner.py` (165 cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Version-attestation mutation harness: 60 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-version-attestation-harness.py  60-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-version-attestation-harness.py` (60 cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Canonical models-inventory attestation runner: 116 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-models-attestation-runner.py  116-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-attestation-runner.py` (116 cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Explicit-account models capture runner: 84 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-models-capture-runner.py  84-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-runner.py` (84 fake-account cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Repository-only version bootstrap runner: 139 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-version-bootstrap-runner.py  139-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-version-bootstrap-runner.py` (139 synthetic cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Repository-only version initial-bootstrap runner: 43 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-version-initial-bootstrap-runner.py  43-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-version-initial-bootstrap-runner.py` (43 synthetic cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Fixed 1.1.12 version recovery runner: 75 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-version-recovery-1-1-12-runner.py  75-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-version-recovery-1-1-12-runner.py` (75 synthetic cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Explicit-account models capture profile builder: 121 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-models-capture-profile.py 121-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-profile.py` (121 synthetic cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Fixed 1.1.12 models capture profile builder: 30 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-models-capture-1-1-12-profile.py 30-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-1-1-12-profile.py` (30 offline cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Fixed 1.1.12 models capture runner: 56 offline' "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-models-capture-1-1-12-runner.py 56-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-1-1-12-runner.py` (56 offline runner cases' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Fixed 1.1.16 version evidence: 45 offline; capture profile: 30 offline; capture runner: 58 offline; historical activation: 8 offline.' \
            "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-models-capture-1-1-16-version-evidence.py 45-case' \
            "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-1-1-16-version-evidence.py` (45 offline cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'tests/test-models-capture-1-1-16-profile.py 30-case' \
            "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-1-1-16-profile.py` (30 offline cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'tests/test-models-capture-1-1-16-runner.py 58-case' \
            "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-1-1-16-runner.py` (58 offline cases' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Fixed 1.1.22 version evidence: 45 offline; capture profile: 30 offline; capture runner: 58 offline; reprofile: 88 offline.' \
            "$ROOT/AGENTS.md" \
        && grep -Fq 'tests/test-models-capture-1-1-22-version-evidence.py 45-case' \
            "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-1-1-22-version-evidence.py` (45 offline cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'tests/test-models-capture-1-1-22-profile.py 30-case' \
            "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-1-1-22-profile.py` (30 offline cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'tests/test-models-capture-1-1-22-runner.py 58-case' \
            "$ROOT/README.md" \
        && grep -Fq 'tests/test-models-capture-1-1-22-runner.py` (58 offline cases' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'tests/test-agy-1-1-16-activation.py` (8 historical cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'tests/test-agy-1-1-16-activation.py 8-case' "$ROOT/README.md" \
        && grep -Fq 'tests/test-agy-1-1-22-activation.py` (24 active cases)' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'tests/test-agy-1-1-22-activation.py 24-case' "$ROOT/README.md" \
        && [[ -n "$bootstrap_preflight_line" ]] \
        && [[ -n "$bootstrap_suite_line" ]] \
        && (( bootstrap_preflight_line < bootstrap_suite_line )) \
        && grep -Fq '/usr/bin/python3 -I -S -B -' "$CI_OFFLINE" \
        && grep -Fq 'sys.implementation.name == "cpython"' "$CI_OFFLINE" \
        && grep -Fq 'sys.version_info[:2] == (3, 9)' "$CI_OFFLINE" \
        && grep -Fq 'sys.flags.isolated == 1' "$CI_OFFLINE" \
        && grep -Fq 'sys.flags.no_site == 1' "$CI_OFFLINE" \
        && grep -Fq 'sys.flags.dont_write_bytecode == 1' "$CI_OFFLINE" \
        && grep -Fq 'sys.flags.ignore_environment == 1' "$CI_OFFLINE" \
        && grep -Fq '/usr/bin/python3 -I -S -B tests/test-version-bootstrap-runner.py' \
            "$CI_OFFLINE"; then
    ok "bootstrap, recovery, and independent capture-bridge measured counts stay synchronized"
else
    bad "bootstrap, recovery, and independent capture-bridge measured counts stay synchronized"
fi

if [[ -x "$ROOT/scripts/models_capture_1_1_12_profile.py" ]] \
        && [[ -x "$ROOT/scripts/models_capture_1_1_12_runner.py" ]] \
        && [[ -x "$ROOT/scripts/models_capture_1_1_16_version_evidence.py" ]] \
        && [[ -x "$ROOT/scripts/models_capture_1_1_16_profile.py" ]] \
        && [[ -x "$ROOT/scripts/models_capture_1_1_16_runner.py" ]] \
        && [[ -x "$ROOT/scripts/models_capture_1_1_22_version_evidence.py" ]] \
        && [[ -x "$ROOT/scripts/models_capture_1_1_22_profile.py" ]] \
        && [[ -x "$ROOT/scripts/models_capture_1_1_22_runner.py" ]] \
        && [[ -x "$ROOT/scripts/models_capture_1_1_22_reprofile.py" ]] \
        && grep -Fq 'EXPECTED_VERSION = "1.1.16"' \
            "$ROOT/scripts/models_capture_1_1_16_version_evidence.py" \
        && grep -Fq 'private raw `captured` evidence' \
            "$ROOT/docs/REPO_MAP.md" \
        && [[ -f "$ROOT/compat/reviews/agy-1.1.12.md" ]] \
        && ! [[ -e "$ROOT/compat/reviews/agy-1.1.12-decision.md" ]] \
        && grep -Fq 'agy `1.1.12` baseline' "$ROOT/compat/reviews/agy-1.1.12.md" \
        && grep -Fq 'f7519c9084190ed421e89dd81c63970b5177c9ef' \
            "$ROOT/compat/reviews/agy-1.1.12.md" \
        && grep -Fq 'df1cc77947e5562976d51f295b4f023c2c24ef25db6d0afe30976004311996bd' \
            "$ROOT/compat/reviews/agy-1.1.12.md" \
        && grep -Fq '8d46bcac6b8f27995635d91dc6f5a0e549d351e707efe11a82d8b6593fe12daf' \
            "$ROOT/compat/reviews/agy-1.1.12.md" \
        && grep -Fq 'db2a3529568b1ce4bb112d4cb9a0c31a4f3d1b32bd787728d224894ec6db133c' \
            "$ROOT/compat/reviews/agy-1.1.12.md" \
        && grep -Fq 'a36ead9a39715bb2380b3c36cbd8ae8e6e570e4147a4a4c7dc92f78e82e691a0' \
            "$ROOT/compat/reviews/agy-1.1.12.md" \
        && grep -Fq '7aed92cc79154691407324f6d3bd75f335b67ab8ecc04cad89a60b5d15c03b3d' \
            "$ROOT/compat/reviews/agy-1.1.12.md" \
        && [[ -f "$ROOT/compat/reviews/agy-1.1.16-interface.md" ]] \
        && grep -Fq 'efa16f096dc02fb654b7e86958d268195284d014' \
            "$ROOT/compat/reviews/agy-1.1.16-interface.md" \
        && grep -Fq 'No `agy models`, `agy agents`, plugin, prompt, authentication, or' \
            "$ROOT/compat/reviews/agy-1.1.16-interface.md" \
        && [[ -f "$ROOT/compat/reviews/agy-1.1.16.md" ]] \
        && grep -Fq '04f9cf2d18c14635689630c7bb50437151f2b0eb1d414d0d943212fe12c7a20e' \
            "$ROOT/compat/reviews/agy-1.1.16.md" \
        && grep -Fq '3f34e6f6bfcf7b7e65951e02f92580c2858f32016f115866160f279d2d3a2747' \
            "$ROOT/compat/reviews/agy-1.1.16.md" \
        && grep -Fq 'a586927552d90295529f3059989a2a8c36c234d41b8f79d61c1c89edbf829e00' \
            "$ROOT/compat/reviews/agy-1.1.16.md" \
        && grep -Fq 'same fourteen exact slugs' "$ROOT/compat/reviews/agy-1.1.16.md" \
        && [[ -f "$ROOT/compat/reviews/agy-1.1.22-activation.md" ]] \
        && grep -Fq '626623c2c7b3b126efc2161c36554ecfa7fad3ce46e9dfcee8419c685ccaf2e3' \
            "$ROOT/compat/reviews/agy-1.1.22-activation.md" \
        && grep -Fq 'Active 1.1.22 compatibility binding: 24 offline.' \
            "$ROOT/AGENTS.md" \
        && [[ -f "$ROOT/compat/reviews/agy-1.1.22.md" ]] \
        && grep -Fq 'cab32a092e67b5199c1777e45f65623f703a94812b75a0732e7b3156302e9f77' \
            "$ROOT/compat/reviews/agy-1.1.22.md" \
        && grep -Fq 'failure_classified: false' \
            "$ROOT/compat/reviews/agy-1.1.22.md" \
        && grep -Fq 'does **not** activate 1.1.22' \
            "$ROOT/compat/reviews/agy-1.1.22.md" \
        && ! grep -Fq 'same fourteen' "$ROOT/compat/reviews/agy-1.1.22.md" \
        && [[ -f "$ROOT/compat/reviews/codex-0.148.0.md" ]] \
        && grep -Fq '3ba0f711642a888aec92a611a3f3b2211157ff89' \
            "$ROOT/compat/reviews/codex-0.148.0.md" \
        && grep -Fq 'At its 2026-08-20 review, Codex `0.148.0` was accepted as the observational' \
            "$ROOT/compat/reviews/codex-0.148.0.md" \
        && grep -Fq 'It is superseded by the separately reviewed `0.150.1` record.' \
            "$ROOT/compat/reviews/codex-0.148.0.md" \
        && [[ -f "$ROOT/compat/reviews/codex-0.150.1.md" ]] \
        && grep -Fq '90854393966b21e9ebfd21b122334eb09a20c93d' \
            "$ROOT/compat/reviews/codex-0.150.1.md" \
        && grep -Fq 'Codex `0.150.1` is accepted as the current observational compatibility baseline.' \
            "$ROOT/compat/reviews/codex-0.150.1.md" \
        && grep -Fq 'does not gate agy dispatch' \
            "$ROOT/compat/reviews/codex-0.150.1.md" \
        && grep -Fq 'add Codex as a second worker backend' \
            "$ROOT/compat/reviews/codex-0.150.1.md" \
        && ! grep -Fqr 'models_capture_1_1_12' "$ROOT/skills/agy-worker/runtime"; then
    ok "historical and active compatibility records preserve the activation boundary"
else
    bad "historical and active compatibility records preserve the activation boundary"
fi

if [[ -x "$ROOT/scripts/version_bootstrap_runner.py" ]] \
        && [[ -x "$ROOT/tests/test-version-bootstrap-runner.py" ]] \
        && ! grep -Fq 'skills/agy-worker/runtime/version_bootstrap_runner.py' "$ROOT/README.md"; then
    ok "bootstrap remains an executable repository-only surface"
else
    bad "bootstrap remains an executable repository-only surface"
fi

if [[ -x "$ROOT/scripts/version_initial_bootstrap_runner.py" ]] \
        && [[ -x "$ROOT/tests/test-version-initial-bootstrap-runner.py" ]] \
        && ! grep -Fq 'account_home' "$ROOT/scripts/version_initial_bootstrap_runner.py" \
        && ! grep -Fq 'version_initial_bootstrap_runner.py' "$ROOT/skills/agy-worker/runtime" -r; then
    ok "initial bootstrap remains a separate HOME-inert repository-only surface"
else
    bad "initial bootstrap remains a separate HOME-inert repository-only surface"
fi

if [[ -x "$ROOT/scripts/version_recovery_1_1_12_runner.py" ]] \
        && [[ -x "$ROOT/tests/test-version-recovery-1-1-12-runner.py" ]] \
        && ! grep -Fq 'version_recovery_1_1_12_runner.py' "$ROOT/skills/agy-worker/runtime" -r \
        && grep -Fq 'non-authorizing' "$ROOT/README.md"; then
    ok "fixed recovery remains a separate non-authorizing repository-only surface"
else
    bad "fixed recovery remains a separate non-authorizing repository-only surface"
fi

profile_builder_identity="$(/usr/bin/python3 -I -S -B - "$ROOT/scripts/models_capture_profile.py" <<'PY'
import ast
import hashlib
import os
import stat
import sys

path = sys.argv[1]
data = open(path, "rb").read()
tree = ast.parse(data.decode("utf-8"))
for node in tree.body:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "MODULE_AST_SHA256"
    ):
        node.value = ast.Constant(value="PINNED-MODULE-AST")
        break
print(
    "%o|%s|%s|%s" % (
        stat.S_IMODE(os.stat(path).st_mode),
        len(data),
        hashlib.sha256(data).hexdigest(),
        hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest(),
    )
)
PY
)"
if [[ "$profile_builder_identity" == "755|44660|f934c48857c286665a1cad91450a87419bdb3286fb66e1b0c4a6b5b87aa180cb|798fd1b42d4b45e0e0687f25e8fbaaa19f412e4975e50f4ae7ecfe22e9e58d1b" ]]; then
    ok "capture-profile builder reviewed identity is independently pinned"
else
    bad "capture-profile builder reviewed identity changed"
fi

if grep -Fq 'same-UID processes' "$ROOT/README.md" \
        && grep -Fq 'It never scans for or chases a moved directory.' \
            "$ROOT/docs/CONFORMANCE.md" \
        && grep -Fq 'may leave a private residual' "$ROOT/PRIVACY.md" \
        && grep -Fq 'does not establish same-user tamper resistance' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Final pathname removal still trusts that TCB.' \
            "$ROOT/docs/lessons_learned.md" \
        && grep -Fq 'same-user tamper-resistance or guaranteed' "$ROOT/AGENTS.md"; then
    ok "conformance docs bind the same-UID TCB and fail-closed residual boundary"
else
    bad "conformance docs bind the same-UID TCB and fail-closed residual boundary"
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
        && grep -Fq '<loc>https://cagdasyurekli.github.io/codex-agy-worker/VERIFYING_AGENT_OUTPUT.html</loc>' "$ROOT/docs/sitemap.xml" \
        && grep -Fq 'GitHub repository as the source of truth' "$ROOT/docs/index.md" \
        && grep -Fq 'VERIFYING_AGENT_OUTPUT.md' "$ROOT/docs/index.md" \
        && grep -Fq 'canonical_url: "https://cagdasyurekli.github.io/codex-agy-worker/VERIFYING_AGENT_OUTPUT.html"' "$ROOT/docs/VERIFYING_AGENT_OUTPUT.md" \
        && grep -Fq 'A Codex Agent Skill for bounded Antigravity CLI delegation' < <(sed -n '1,120p' "$ROOT/README.md") \
        && grep -Fq '## Quick start' < <(sed -n '1,120p' "$ROOT/README.md") \
        && grep -Fq 'codex plugin marketplace add cagdasyurekli/codex-agy-worker' < <(sed -n '1,120p' "$ROOT/README.md") \
        && grep -Fq 'codex plugin add codex-agy-worker@codex-agy-worker' < <(sed -n '1,120p' "$ROOT/README.md") \
        && grep -Fq 'git clone https://github.com/cagdasyurekli/codex-agy-worker.git' < <(sed -n '1,120p' "$ROOT/README.md") \
        && grep -Fq 'does not authorize a provider dispatch or repository transmission' < <(sed -n '1,120p' "$ROOT/README.md") \
        && grep -Fq './proof-demo.sh' < <(sed -n '1,120p' "$ROOT/README.md") \
        && grep -Fq 'read [PRIVACY.md](PRIVACY.md) before use' < <(sed -n '1,120p' "$ROOT/README.md") \
        && grep -Fq '<picture>' "$ROOT/README.md" \
        && grep -Fq 'srcset="docs/assets/brand/logo-dark.svg"' "$ROOT/README.md" \
        && grep -Fq 'src="docs/assets/brand/logo-light.svg" alt=""' "$ROOT/README.md" \
        && [[ ! -e "$ROOT/docs/robots.txt" ]]; then
    ok "approved brand assets and GitHub Pages wiring pass the production contract"
else
    bad "approved brand assets and GitHub Pages wiring pass the production contract"
fi

python3 "$ROOT/scripts/validate-docs.py" "$ROOT" --readme-max-lines 1905 \
    > "$TMP/docs-valid.out" 2> "$TMP/docs-valid.err"
docs_valid_rc=$?
python3 - "$ROOT/scripts/validate-docs.py" "$ROOT/README.md" <<'PY'
import importlib.util
from pathlib import Path
import sys
import tempfile

script = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("validate_docs", script)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

readme = Path(sys.argv[2]).read_text(encoding="utf-8")
assert module.validate_onboarding(readme, 1905) == []
max_lines = 1905
padding = ["<!-- budget mutation -->"] * (max_lines + 1 - len(readme.splitlines()))
over_budget = readme + "\n" + "\n".join(padding)
assert any("maximum is 1905" in error for error in module.validate_onboarding(over_budget, max_lines))

hidden_markers = "\n".join(
    f"<!-- {marker} -->" for _label, marker in module.ONBOARDING_MARKERS
)
assert module.validate_onboarding(hidden_markers, 1905)
unclosed_comment = "<!--\n" + "\n".join(marker for _label, marker in module.ONBOARDING_MARKERS)
assert module.validate_onboarding(unclosed_comment, 1905)
fenced_markers = "```text\n" + "\n".join(
    marker for _label, marker in module.ONBOARDING_MARKERS
) + "\n```"
assert module.validate_onboarding(fenced_markers, 1905)
full_tutorial = module.ONBOARDING_MARKERS[-1][1]
broken_tutorial = readme.replace(full_tutorial, full_tutorial.split("](", 1)[0] + "]")
assert any("verification tutorial" in error for error in module.validate_onboarding(broken_tutorial, 1905))
inline_code_tutorial = readme.replace(full_tutorial + ".", f"`{full_tutorial}`")
assert any("verification tutorial" in error for error in module.validate_onboarding(inline_code_tutorial, 1905))

lines = readme.splitlines()
positioning = next(index for index, line in enumerate(lines) if module.ONBOARDING_MARKERS[0][1] in line)
workflow_badge = next(index for index, line in enumerate(lines) if module.ONBOARDING_MARKERS[1][1] in line)
lines[positioning], lines[workflow_badge] = lines[workflow_badge], lines[positioning]
assert any("out of order" in error for error in module.validate_onboarding("\n".join(lines), 1905))

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory) / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text("[missing](docs/no.md)\n", encoding="utf-8")
    assert any("missing local link target" in error for error in module.validate_markdown_links(root))
    (root / "docs" / "guide.md").write_text("# Present\n", encoding="utf-8")
    (root / "README.md").write_text("[bad anchor](docs/guide.md#missing)\n", encoding="utf-8")
    assert any("missing Markdown anchor" in error for error in module.validate_markdown_links(root))
    (root / "README.md").write_text("[case](docs/GUIDE.md#present)\n", encoding="utf-8")
    assert any("non-exact path casing" in error for error in module.validate_markdown_links(root))
    (root.parent / "outside.md").write_text("# Outside\n", encoding="utf-8")
    (root / "README.md").write_text("[escape](../outside.md#outside)\n", encoding="utf-8")
    assert any("escapes the repository" in error for error in module.validate_markdown_links(root))
    (root / "README.md").write_text("[root](/../outside.md#outside)\n", encoding="utf-8")
    assert any("root-relative" in error for error in module.validate_markdown_links(root))
    (root / "README.md").write_text("[encoded](/%2e%2e/outside.md#outside)\n", encoding="utf-8")
    assert any("root-relative" in error for error in module.validate_markdown_links(root))
    (root / "docs" / "name_(guide).md").write_text("# Parentheses\n", encoding="utf-8")
    (root / "README.md").write_text("[balanced](docs/name_(guide).md#parentheses)\n", encoding="utf-8")
    assert module.validate_markdown_links(root) == []
    allowlist = root / "docs" / "public-files.allowlist"
    allowlist.write_text(
        "docs/guide.md\ndocs/name_(guide).md\ndocs/public-files.allowlist\n",
        encoding="utf-8",
    )
    assert module.validate_public_docs_inventory(root) == []
    (root / "docs" / ".DS_Store").write_bytes(b"ignored OS noise")
    assert any("not public-allowlisted" in error for error in module.validate_public_docs_inventory(root))
    (root / "docs" / ".DS_Store").unlink()
    (root / "docs" / ".DS_Store").mkdir()
    (root / "docs" / ".DS_Store" / "private.md").write_text("# Hidden\n", encoding="utf-8")
    assert any("not public-allowlisted" in error for error in module.validate_public_docs_inventory(root))
    (root / "docs" / ".DS_Store" / "private.md").unlink()
    (root / "docs" / ".DS_Store").rmdir()
    (root / "README.md").write_text("[escaped](docs/name_\\(guide\\).md#parentheses)\n", encoding="utf-8")
    assert module.validate_markdown_links(root) == []
    (root / "docs" / "private-report.MD").write_text("# Private readout\n", encoding="utf-8")
    assert any("not public-allowlisted" in error for error in module.validate_public_docs_inventory(root))
    (root / "docs" / "private-report.markdown").write_text("# Private readout\n", encoding="utf-8")
    assert any("not public-allowlisted" in error for error in module.validate_public_docs_inventory(root))
    (root / "docs" / "guide.markdown").write_text("[missing](nope.md)\n", encoding="utf-8")
    assert any("missing local link target" in error for error in module.validate_markdown_links(root))
    campaign = root / "docs" / "campaign-report-2026-08-29.md"
    campaign.write_text("# Private readout\n", encoding="utf-8")
    allowlist.write_text(
        "docs/campaign-report-2026-08-29.md\ndocs/guide.md\ndocs/name_(guide).md\n"
        "docs/private-report.MD\ndocs/private-report.markdown\ndocs/public-files.allowlist\n",
        encoding="utf-8",
    )
    assert any("forbidden private/report/draft/dated" in error for error in module.validate_public_docs_inventory(root))

    sitemap = root / "docs" / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n',
        encoding="utf-8",
    )
    assert any("at least one owned URL" in error for error in module.validate_pages_sitemap(root))
    sitemap.write_text("<urlset></urlset>\n", encoding="utf-8")
    assert any("sitemap urlset namespace" in error for error in module.validate_pages_sitemap(root))
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>'
        + module.PAGES_BASE
        + "GUIDE.html</loc></url></urlset>\n",
        encoding="utf-8",
    )
    assert any("non-exact path casing" in error for error in module.validate_pages_sitemap(root))
PY
docs_mutation_rc=$?
if tracked_evidence="$(git -C "$ROOT" ls-files -- 'evidence' 'evidence/**')"; then
    tracked_evidence_rc=0
else
    tracked_evidence_rc=$?
fi
mkdir "$TMP/evidence-pathspec-repo"
git -C "$TMP/evidence-pathspec-repo" init -q
ln -s private-target "$TMP/evidence-pathspec-repo/evidence"
git -C "$TMP/evidence-pathspec-repo" add -f evidence
if [[ "$(git -C "$TMP/evidence-pathspec-repo" ls-files -- 'evidence' 'evidence/**')" == "evidence" ]]; then
    evidence_pathspec_rc=0
else
    evidence_pathspec_rc=1
fi
if git -C "$ROOT" check-ignore -q --no-index \
        docs/agy-worker-campaign-quality-report-2026-08-27.md; then
    removed_report_ignore_rc=0
else
    removed_report_ignore_rc=$?
fi
if [[ "$docs_valid_rc" == "0" ]] \
        && [[ "$docs_mutation_rc" == "0" ]] \
        && [[ "$tracked_evidence_rc" == "0" ]] \
        && [[ "$evidence_pathspec_rc" == "0" ]] \
        && [[ "$removed_report_ignore_rc" == "0" ]] \
        && grep -Fq 'complete public docs inventory, README onboarding order and line budget' "$TMP/docs-valid.out" \
        && grep -Fq 'Follow `docs/DOCUMENTATION_POLICY.md`' "$ROOT/AGENTS.md" \
        && grep -Fq '[documentation policy](docs/DOCUMENTATION_POLICY.md)' "$ROOT/CONTRIBUTING.md" \
        && grep -Fq '`README.md` is the first-visit product page' "$ROOT/docs/DOCUMENTATION_POLICY.md" \
        && grep -Fq 'one authoritative documentation owner' "$ROOT/docs/DOCUMENTATION_POLICY.md" \
        && grep -Fq 'provisional hard ceiling is **1,905 lines**' "$ROOT/docs/DOCUMENTATION_POLICY.md" \
        && grep -Fq 'permanent 450-line budget' "$ROOT/docs/DOCUMENTATION_POLICY.md" \
        && grep -Fq 'Treat those pins as migration debt' "$ROOT/docs/DOCUMENTATION_POLICY.md" \
        && grep -Fq '`docs/public-files.allowlist` is the complete set' "$ROOT/docs/DOCUMENTATION_POLICY.md" \
        && grep -Fq 'Installation never authorizes provider dispatch or repository transmission' "$ROOT/docs/DOCUMENTATION_POLICY.md" \
        && grep -Fq '`bash tests/test-packaging.sh`' "$ROOT/docs/DOCUMENTATION_POLICY.md" \
        && grep -Fxq '/evidence' "$ROOT/.gitignore" \
        && grep -Fxq '/docs/private/' "$ROOT/.gitignore" \
        && grep -Fxq '/docs/reports/' "$ROOT/.gitignore" \
        && grep -Fxq '/docs/agy-worker-campaign-quality-report-*.md' "$ROOT/.gitignore" \
        && grep -Fxq '/docs/*campaign-report-20??-??-??*.md' "$ROOT/.gitignore" \
        && [[ -z "$tracked_evidence" ]] \
        && ! grep -Fq '0123456789abcdef0123456789abcdef01234567' "$ROOT/docs/MEASUREMENT.md" \
        && ! grep -Fq 'actions/runs/123456789' "$ROOT/docs/MEASUREMENT.md" \
        && grep -Fq ': "${REPO_SHA:?set REPO_SHA' "$ROOT/docs/MEASUREMENT.md" \
        && grep -Fq -- '--evidence-url "$WATCHER_EVIDENCE_URL"' "$ROOT/docs/MEASUREMENT.md" \
        && [[ ! -e "$ROOT/docs/agy-worker-campaign-quality-report-2026-08-27.md" ]]; then
    ok "documentation policy, public inventory, ordered onboarding, budget, links, anchors, and Pages mapping stay valid"
else
    bad "documentation policy, public inventory, ordered onboarding, budget, links, anchors, and Pages mapping stay valid"
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

if grep -Fq '24 quota exhausted' "$ROOT/skills/agy-worker/runtime/agy-worker.sh" \
        && grep -Fq '`24` provider quota exhausted' "$ROOT/README.md" \
        && grep -Fq 'Wrong-version or altered quota terminals without a report remain `invalid_envelope`' \
            "$ROOT/README.md" \
        && grep -Fq '`1.1.13` quota terminal remains `provider_quota_exhausted` with exit `24`' \
            "$ROOT/README.md" \
        && grep -Fq 'exact agy `1.1.13` terminal quota response' \
            "$ROOT/skills/agy-worker/SKILL.md" \
        && grep -Fq 'terminal phases are `completed` or `blocked`; exact Codex driver decisions/dispositions' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Controller terminal phases are `completed` or' \
            "$ROOT/docs/lessons_learned.md" \
        && grep -Fq 'bounded non-gating version observation' "$ROOT/README.md" \
        && grep -Fq '1.1.13 shape with no report, it records `provider_quota_exhausted`, exit `24`, and' \
            "$ROOT/compat/reviews/agy-1.1.13-quota-terminal.md" \
        && grep -Fq 'are `invalid_envelope`, exit `4`, and `failure_stage=missing_structured_output`.' \
            "$ROOT/compat/reviews/agy-1.1.13-quota-terminal.md" \
        && grep -Fq 'Wrong-version or altered quota terminals without a report remain `invalid_envelope`' \
            "$ROOT/README.md" \
        && grep -Fq 'Wrong-version or altered quota terminals without a' \
            "$ROOT/skills/agy-worker/SKILL.md" \
        && grep -Fq 'before every reviewed direct dispatch, Codex inspects' "$ROOT/README.md" \
        && grep -Fq 'Before every' "$ROOT/skills/agy-worker/SKILL.md" \
        && grep -Fq 'reviewed direct dispatch, including an exact-version match, Codex must inspect' \
            "$ROOT/skills/agy-worker/SKILL.md" \
        && grep -Fq 'Codex inspects current bounded raw help before every reviewed direct dispatch' \
            "$ROOT/docs/REPO_MAP.md" \
        && grep -Fq 'Exact-version structural acceptance is only mechanical' \
            "$ROOT/docs/lessons_learned.md"; then
    ok "package documents the narrow version-bound quota terminal contract"
else
    bad "package quota terminal documentation contract"
fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
