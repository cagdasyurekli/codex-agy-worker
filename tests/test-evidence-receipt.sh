#!/usr/bin/env bash
# Offline adversarial suite for Evidence Receipt v1: no agy, network, or provider.
set -uo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(CDPATH= cd -- "$HERE/.." && pwd -P)"
VERIFY="$ROOT/verify-job.sh"
GATE="$ROOT/qa-gate.sh"
VALIDATOR="$ROOT/skills/agy-worker/runtime/scripts/evidence_receipt.py"
INTERNAL_EVIDENCE_TOKEN="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INTERNAL_PYTHON="$(python3 -I -S -B -c 'import pathlib,sys; print(pathlib.Path(sys.executable).resolve())')"
TMP="$(mktemp -d -t agyworker-receipt.XXXXXX)"
trap 'rm -rf -- "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass + 1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail + 1)); }

REPO="$TMP/repo"
RECEIPTS="$TMP/receipts"
mkdir -m 700 "$REPO" "$RECEIPTS"
RECEIPTS="$(CDPATH= cd -- "$RECEIPTS" && pwd -P)"
git -C "$REPO" init -q
git -C "$REPO" config user.email test@example.com
git -C "$REPO" config user.name test
printf 'original\n' > "$REPO/a.txt"
printf 'ignored.tmp\n' > "$REPO/.gitignore"
git -C "$REPO" add -A
git -C "$REPO" commit -qm init
BASE="$(git -C "$REPO" rev-parse HEAD)"

reset_repo() {
    git -C "$REPO" checkout -q -- .
    git -C "$REPO" clean -qfdx
}

write_envelope() { printf '%s\n' "$2" > "$TMP/$1"; }

run_internal_gate() {
    AGY_WORKER_INTERNAL_EVIDENCE_TOKEN="$INTERNAL_EVIDENCE_TOKEN" \
    AGY_WORKER_INTERNAL_PYTHON="$INTERNAL_PYTHON" \
        "$GATE" --evidence-token "$INTERNAL_EVIDENCE_TOKEN" "$@"
}

HONEST='{"status":"completed","summary":"PRIVATE-WORKER-PROSE","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
COMMAND_CLAIM='{"status":"completed","summary":"done","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":["PRIVATE-UNTRUSTED-COMMAND"],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
NO_EDITS='{"status":"completed","summary":"done","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
PARTIAL='{"status":"partial","summary":"PRIVATE-PARTIAL-PROSE","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":["PRIVATE-QUESTION"],"confidence":0.4,"requires_human":true}'
write_envelope honest.json "$HONEST"
write_envelope command.json "$COMMAND_CLAIM"
write_envelope no-edits.json "$NO_EDITS"
write_envelope partial.json "$PARTIAL"
printf '{not json\n' > "$TMP/malformed.json"

assert_receipt() {
    local name="$1" target="$2" expected_exit="$3" expected_outcome="$4" expected_verdict="$5"
    if python3 -B - "$target" "$expected_exit" "$expected_outcome" "$expected_verdict" "$BASE" <<'PY'
import json
import os
import stat
import sys

path, expected_exit, expected_outcome, expected_verdict, base = sys.argv[1:]
raw = open(path, "rb").read()
value = json.loads(raw)
assert raw.endswith(b"\n") and raw.count(b"\n") == 1
assert value["schema_version"] == 1
assert value["kind"] == "agy-worker-evidence-receipt"
assert value["gate_authority"] == "qa-gate"
assert value["resolved_base"] == base
assert value["gate_exit"] == int(expected_exit)
assert value["gate_outcome"] == expected_outcome
assert value["verdict"] == expected_verdict
assert value["recommendations_participated_in_acceptance"] is False
assert value["integrity"]["signed"] is False
assert value["integrity"]["tamper_evident"] is False
assert value["verifiers"][0]["label"] == "verify-001"
assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
for key in (
    "envelope_sha256", "path_policy_sha256",
    "initial_candidate_state_sha256", "final_candidate_state_sha256",
    "command_sha256",
):
    digest = value["verifiers"][0][key] if key == "command_sha256" else value[key]
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
for forbidden in (
    "PRIVATE-WORKER-PROSE", "PRIVATE-UNTRUSTED-COMMAND",
    "PRIVATE-PARTIAL-PROSE", "PRIVATE-QUESTION", base,
):
    if forbidden == base:
        continue
    assert forbidden not in raw.decode("utf-8")
PY
    then ok "$name"; else bad "$name"; fi
}

run_receipt_case() {
    local name="$1" label="$2" expected_exit="$3" outcome="$4" verdict="$5" envelope="$6"
    shift 6
    local target="$RECEIPTS/$label.json" rc
    rm -f -- "$target"
    "$VERIFY" --receipt "$target" --envelope "$TMP/$envelope" \
        --repo "$REPO" --base "$BASE" "$@" \
        > "$TMP/$label.out" 2> "$TMP/$label.err"
    rc=$?
    if [[ "$rc" == "$expected_exit" && -f "$target" ]]; then
        assert_receipt "$name" "$target" "$expected_exit" "$outcome" "$verdict"
    else
        bad "$name (exit $rc, receipt=$([[ -f "$target" ]] && echo yes || echo no))"
    fi
}

echo "Evidence Receipt v1 offline test suite"
echo
echo "durable gate outcome matrix:"
reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
run_receipt_case "exit 0 publishes gate-passed" pass 0 gate-passed gate-passed honest.json \
    --only a.txt --expect-edits --verify true

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
run_receipt_case "exit 10 publishes rejected scope result" scope 10 scope-violation rejected honest.json \
    --only 'tests/**' --verify true

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
run_receipt_case "exit 11 publishes rejected claim result" claim 11 untrusted-worker-claim rejected command.json \
    --verify true

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
run_receipt_case "exit 12 publishes rejected envelope result" malformed 12 invalid-envelope rejected malformed.json \
    --verify true

reset_repo
run_receipt_case "exit 13 publishes rejected missing-edit result" missing 13 expected-edits-missing rejected no-edits.json \
    --expect-edits --verify true

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
run_receipt_case "exit 14 publishes rejected verifier result" verifier 14 driver-verification-failed rejected honest.json \
    --verify false

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
run_receipt_case "exit 15 publishes routed worker escalation" routed 15 worker-escalation routed partial.json \
    --verify true

echo
echo "legacy gate behavior and narrow FD handoff:"
reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
"$GATE" --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --only a.txt --expect-edits --verify true \
    > "$TMP/direct.out" 2> "$TMP/direct.err"
direct_rc=$?
cat > "$TMP/direct.expected" <<'EOF'
qa-gate: scope OK (1 file(s) changed)
qa-gate: driver verification: true
qa-gate: scope OK (1 file(s) changed)
qa-gate: ACCEPTED
EOF
if [[ "$direct_rc" == 0 && ! -s "$TMP/direct.out" ]] \
        && cmp -s "$TMP/direct.expected" "$TMP/direct.err"; then
    ok "direct gate without evidence FD retains exact output and exit"
else
    bad "direct gate without evidence FD retains exact output and exit"
fi

cat > "$TMP/unsupported-worker-schema.json" <<'EOF'
{"type":"object","pattern":"worker-controlled schema must not govern receipts"}
EOF
AGY_WORKER_SCHEMA="$TMP/unsupported-worker-schema.json" \
    "$GATE" --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
        --only a.txt --verify true >/dev/null 2>&1
override_gate_rc=$?
schema_target="$RECEIPTS/canonical-schema.json"
AGY_WORKER_SCHEMA="$TMP/unsupported-worker-schema.json" \
    "$VERIFY" --receipt "$schema_target" --envelope "$TMP/honest.json" \
        --repo "$REPO" --base "$BASE" --only a.txt --verify true \
        >/dev/null 2>&1
override_wrapper_rc=$?
if [[ "$override_gate_rc" == 12 && "$override_wrapper_rc" == 0 \
        && -f "$schema_target" ]]; then
    ok "receipt wrapper isolates the gate from a caller schema override"
else
    bad "receipt wrapper isolates the gate from a caller schema override"
fi

exec 9> "$TMP/direct-handoff.jsonl"
run_internal_gate --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --only a.txt --verify true --evidence-fd 9 \
    > "$TMP/fd.out" 2> "$TMP/fd.err"
fd_rc=$?
exec 9>&-
if [[ "$fd_rc" == 0 ]] && python3 -B - "$TMP/direct-handoff.jsonl" "$BASE" <<'PY'
import json, sys
raw=open(sys.argv[1], 'rb').read()
value=json.loads(raw)
assert raw.endswith(b'\n') and raw.count(b'\n') == 1
assert value['kind']=='agy-worker-gate-evidence'
assert value['resolved_base']==sys.argv[2]
assert value['gate_exit']==0 and value['gate_outcome']=='gate-passed'
assert set(value)=={
 'schema_version','kind','resolved_base','envelope_sha256',
 'initial_candidate_state_sha256','final_candidate_state_sha256',
 'gate_exit','gate_outcome'}
PY
then ok "optional evidence FD receives exactly one bounded gate JSON line"; else bad "optional evidence FD receives exactly one bounded gate JSON line"; fi

exec 9> "$TMP/unbound-handoff.jsonl"
"$GATE" --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --only a.txt --verify true --evidence-fd 9 >/dev/null 2>&1
unbound_fd_rc=$?
exec 9>&-
if [[ "$unbound_fd_rc" == 64 && ! -s "$TMP/unbound-handoff.jsonl" ]]; then
    ok "direct evidence FD without the wrapper-bound capability fails before gate work"
else
    bad "direct evidence FD without the wrapper-bound capability fails before gate work"
fi

fd_probe='printf child >&8; if { printf forged >&9; } 2>/dev/null; then exit 41; fi; /bin/bash -c '\''printf descendant >&8; if { printf forged >&9; } 2>/dev/null; then exit 42; fi'\'''
exec 8> "$TMP/fd-control.txt"
exec 9> "$TMP/exclusive-handoff.jsonl"
run_internal_gate --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --only a.txt --verify "$fd_probe" --evidence-fd 9 \
    > "$TMP/exclusive-fd.out" 2> "$TMP/exclusive-fd.err"
exclusive_fd_rc=$?
exec 9>&-
exec 8>&-
if [[ "$exclusive_fd_rc" == 0 \
        && "$(<"$TMP/fd-control.txt")" == childdescendant ]] \
        && python3 -B - "$TMP/exclusive-handoff.jsonl" <<'PY'
import json, sys
raw=open(sys.argv[1], 'rb').read()
assert raw.count(b'\n') == 1 and b'forged' not in raw
assert json.loads(raw)['kind'] == 'agy-worker-gate-evidence'
PY
then
    ok "verifier child and descendant cannot observe or write the evidence FD"
else
    bad "verifier child and descendant cannot observe or write the evidence FD"
fi

LEAK_RUNTIME="$TMP/leaky-runtime"
mkdir "$LEAK_RUNTIME"
cp -R "$ROOT/skills/agy-worker/runtime/scripts" "$LEAK_RUNTIME/scripts"
cp -R "$ROOT/skills/agy-worker/runtime/schemas" "$LEAK_RUNTIME/schemas"
cp -R "$ROOT/skills/agy-worker/runtime/compat" "$LEAK_RUNTIME/compat"
cp "$ROOT/skills/agy-worker/runtime/verify-job.sh" "$LEAK_RUNTIME/verify-job.sh"
sed 's/builtin eval "exec ${evidence_fd}>&-"/:/' \
    "$ROOT/skills/agy-worker/runtime/qa-gate.sh" > "$LEAK_RUNTIME/qa-gate.sh"
chmod +x "$LEAK_RUNTIME/qa-gate.sh"
exec 9> "$TMP/leaky-handoff.jsonl"
AGY_WORKER_INTERNAL_EVIDENCE_TOKEN="$INTERNAL_EVIDENCE_TOKEN" \
AGY_WORKER_INTERNAL_PYTHON="$INTERNAL_PYTHON" \
"$LEAK_RUNTIME/qa-gate.sh" --evidence-token "$INTERNAL_EVIDENCE_TOKEN" \
    --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --only a.txt --verify 'printf forged >&9' --evidence-fd 9 \
    >"$TMP/leaky.out" 2>"$TMP/leaky.err"
leaky_fd_rc=$?
exec 9>&-
if [[ "$leaky_fd_rc" == 0 ]] \
        && grep -Fq forged "$TMP/leaky-handoff.jsonl" \
        && ! python3 -B -c 'import json,sys; json.load(open(sys.argv[1]))' \
            "$TMP/leaky-handoff.jsonl" >/dev/null 2>&1; then
    ok "removing the child close makes the inheritance probe detect forged evidence"
else
    bad "removing the child close makes the inheritance probe detect forged evidence (exit $leaky_fd_rc, bytes=$(wc -c < "$TMP/leaky-handoff.jsonl"))"
fi

leaky_receipt="$RECEIPTS/leaky-wrapper.json"
leak_scan="python3 -B -c 'import os
for descriptor in range(3, 256):
    try: os.write(descriptor, b\"forged\")
    except OSError: pass'"
"$LEAK_RUNTIME/verify-job.sh" --receipt "$leaky_receipt" \
    --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --only a.txt --verify "$leak_scan" >/dev/null 2>&1
leaky_wrapper_rc=$?
if [[ "$leaky_wrapper_rc" == 70 && ! -e "$leaky_receipt" ]]; then
    ok "wrapper rejects a close mutation that lets a verifier scan and forge the FD"
else
    bad "wrapper rejects a close mutation that lets a verifier scan and forge the FD"
fi

HOSTILE_PYTHON="$TMP/hostile-python"
mkdir "$HOSTILE_PYTHON"
cat > "$HOSTILE_PYTHON/sitecustomize.py" <<'PY'
import os
from pathlib import Path

sentinel = os.environ.get("HOSTILE_STARTUP_SENTINEL")
if sentinel:
    Path(sentinel).open("ab").write(b"python-startup\n")
for descriptor in range(3, 256):
    try:
        os.write(descriptor, b"python-forged")
    except OSError:
        pass
PY
cat > "$TMP/hostile-bash-env.sh" <<'SH'
if [[ -n "${HOSTILE_STARTUP_SENTINEL:-}" ]]; then
    printf 'bash-startup\n' >> "$HOSTILE_STARTUP_SENTINEL"
fi
for hostile_fd in 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
    builtin eval "printf bash-forged >&${hostile_fd}" 2>/dev/null || true
done
SH
hostile_sentinel="$TMP/hostile-startup-sentinel.txt"
hostile_target="$RECEIPTS/hostile-startup.json"
startup_verifier='test "$SAFE_VERIFIER_ENV" = keep; test -z "${BASH_ENV+x}"; test -z "${PYTHONPATH+x}"; test -z "${AGY_WORKER_INTERNAL_EVIDENCE_TOKEN+x}"; test -z "${AGY_WORKER_INTERNAL_PYTHON+x}"; python3 -c '\''print(1)'\'' >/dev/null; /bin/bash -c true'
BASH_ENV="$TMP/hostile-bash-env.sh" \
ENV="$TMP/hostile-bash-env.sh" \
PYTHONPATH="$HOSTILE_PYTHON" \
PYTHONHOME="$HOSTILE_PYTHON/missing-home" \
PYTHONSTARTUP="$HOSTILE_PYTHON/sitecustomize.py" \
PYTHONINSPECT=1 \
PYTHONWARNINGS=default \
HOSTILE_STARTUP_SENTINEL="$hostile_sentinel" \
SAFE_VERIFIER_ENV=keep \
    "$VERIFY" --receipt "$hostile_target" --envelope "$TMP/honest.json" \
        --repo "$REPO" --base "$BASE" --only a.txt \
        --verify "$startup_verifier" \
        >"$TMP/hostile-startup.out" 2>"$TMP/hostile-startup.err"
hostile_startup_rc=$?
if [[ "$hostile_startup_rc" == 0 && -f "$hostile_target" ]] \
        && ! grep -Eq 'python-forged|bash-forged' "$hostile_target" \
        && ! grep -Fq python-startup "$hostile_sentinel" \
        && python3 -I -S -B "$VALIDATOR" validate --receipt "$hostile_target" \
            >/dev/null 2>&1; then
    ok "wrapper isolates evidence from hostile Python and Bash startup controls"
else
    bad "wrapper isolates evidence from hostile Python and Bash startup controls"
fi

UNSANITIZED_RUNTIME="$TMP/unsanitized-runtime"
cp -R "$ROOT/skills/agy-worker/runtime" "$UNSANITIZED_RUNTIME"
sed 's/gate_environment = sanitized_gate_environment()/gate_environment = os.environ.copy()/' \
    "$UNSANITIZED_RUNTIME/scripts/evidence_receipt.py" \
    > "$TMP/unsanitized-helper.py"
mv "$TMP/unsanitized-helper.py" \
    "$UNSANITIZED_RUNTIME/scripts/evidence_receipt.py"
chmod +x "$UNSANITIZED_RUNTIME/scripts/evidence_receipt.py"
unsanitized_target="$RECEIPTS/unsanitized-startup.json"
BASH_ENV="$TMP/hostile-bash-env.sh" \
ENV="$TMP/hostile-bash-env.sh" \
PYTHONPATH="$HOSTILE_PYTHON" \
HOSTILE_STARTUP_SENTINEL="$hostile_sentinel" \
    "$UNSANITIZED_RUNTIME/verify-job.sh" --receipt "$unsanitized_target" \
        --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
        --only a.txt --verify true >/dev/null 2>&1
unsanitized_rc=$?
if [[ "$unsanitized_rc" == 70 && ! -e "$unsanitized_target" ]]; then
    ok "removing gate environment sanitization contaminates evidence and returns 70"
else
    bad "removing gate environment sanitization contaminates evidence and returns 70"
fi

exec 9> "$TMP/duplicate-fd.jsonl"
run_internal_gate --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --verify true --evidence-fd 9 --evidence-fd 9 >/dev/null 2>&1
duplicate_fd_rc=$?
exec 9>&-
if [[ "$duplicate_fd_rc" == 64 && ! -s "$TMP/duplicate-fd.jsonl" ]]; then
    ok "duplicate evidence FD is usage failure with no handoff"
else
    bad "duplicate evidence FD is usage failure with no handoff"
fi

run_internal_gate --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --verify true --evidence-fd 999999999999999999999 >/dev/null 2>&1
if [[ $? == 64 ]]; then ok "closed or overflowing evidence FD fails before gate work"; else bad "closed or overflowing evidence FD fails before gate work"; fi

exec 8< "$TMP/direct-handoff.jsonl"
run_internal_gate --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" \
    --verify true --evidence-fd 8 >/dev/null 2>&1
readonly_fd_rc=$?
exec 8>&-
if [[ "$readonly_fd_rc" == 64 ]]; then ok "read-only evidence FD fails before gate work"; else bad "read-only evidence FD fails before gate work"; fi

echo
echo "wrapper preflight and safe target policy:"
no_receipt="$RECEIPTS/no-verify.json"
"$VERIFY" --receipt "$no_receipt" --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" >/dev/null 2>&1
if [[ $? == 64 && ! -e "$no_receipt" ]]; then ok "missing verifier exits 64 with no receipt"; else bad "missing verifier exits 64 with no receipt"; fi

existing="$RECEIPTS/existing.json"
printf 'keep-me\n' > "$existing"
"$VERIFY" --receipt "$existing" --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" --verify true >/dev/null 2>&1
if [[ $? == 64 && "$(<"$existing")" == keep-me ]]; then ok "existing target is never overwritten"; else bad "existing target is never overwritten"; fi

symlink_target="$RECEIPTS/symlink.json"
ln -s "$TMP/nowhere" "$symlink_target"
"$VERIFY" --receipt "$symlink_target" --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" --verify true >/dev/null 2>&1
if [[ $? == 64 && -L "$symlink_target" ]]; then ok "symlink receipt target is rejected without following"; else bad "symlink receipt target is rejected without following"; fi

mkdir -m 700 "$TMP/real-receipt-parent"
ln -s "$TMP/real-receipt-parent" "$TMP/linked-receipt-parent"
linked_parent_target="$TMP/linked-receipt-parent/receipt.json"
"$VERIFY" --receipt "$linked_parent_target" --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" --verify true >/dev/null 2>&1
if [[ $? == 64 && ! -e "$linked_parent_target" ]]; then ok "symlinked receipt parent is rejected"; else bad "symlinked receipt parent is rejected"; fi

ln -s "$TMP/honest.json" "$TMP/envelope-link.json"
linked_envelope_target="$RECEIPTS/linked-envelope.json"
"$VERIFY" --receipt "$linked_envelope_target" --envelope "$TMP/envelope-link.json" \
    --repo "$REPO" --base "$BASE" --verify true >/dev/null 2>&1
if [[ $? == 64 && ! -e "$linked_envelope_target" ]]; then ok "symlinked envelope input is rejected before gate"; else bad "symlinked envelope input is rejected before gate"; fi

in_repo="$REPO/receipt.json"
"$VERIFY" --receipt "$in_repo" --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" --verify true >/dev/null 2>&1
if [[ $? == 64 && ! -e "$in_repo" ]]; then ok "receipt target inside audited repository is rejected"; else bad "receipt target inside audited repository is rejected"; fi

mkdir -m 755 "$TMP/public-parent"
public_target="$(CDPATH= cd -- "$TMP/public-parent" && pwd -P)/receipt.json"
"$VERIFY" --receipt "$public_target" --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" --verify true >/dev/null 2>&1
if [[ $? == 64 && ! -e "$public_target" ]]; then ok "non-private receipt parent is rejected"; else bad "non-private receipt parent is rejected"; fi

mkdir -m 300 "$TMP/owner-incomplete"
owner_incomplete_parent="$(CDPATH= cd -- "$TMP/owner-incomplete" && pwd -P)"
owner_incomplete_target="$owner_incomplete_parent/receipt.json"
"$VERIFY" --receipt "$owner_incomplete_target" --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" --verify true >/dev/null 2>&1
owner_incomplete_rc=$?
chmod 700 "$owner_incomplete_parent"
if [[ "$owner_incomplete_rc" == 64 && ! -e "$owner_incomplete_target" ]]; then
    ok "receipt parent without full owner access is rejected before gate"
else
    bad "receipt parent without full owner access is rejected before gate"
fi

"$VERIFY" --receipt relative-receipt.json --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" --verify true >/dev/null 2>&1
if [[ $? == 64 && ! -e relative-receipt.json ]]; then ok "relative receipt target is rejected"; else bad "relative receipt target is rejected"; fi

duplicate_target="$RECEIPTS/duplicate-option.json"
"$VERIFY" --receipt "$duplicate_target" --receipt "$duplicate_target" \
    --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" --verify true \
    >/dev/null 2>&1
if [[ $? == 64 && ! -e "$duplicate_target" ]]; then ok "singleton wrapper options reject repetition"; else bad "singleton wrapper options reject repetition"; fi

echo
echo "selection and recommendation bindings:"
"$ROOT/model-selection.sh" --tier bulk --tier-source cli > "$TMP/selection.json"
"$ROOT/model-recommendation.sh" --stage pre-dispatch --selected-tier bulk \
    --evidence batched-mechanical > "$TMP/recommendation.json"
reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
bound="$RECEIPTS/bound.json"
"$VERIFY" --receipt "$bound" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --selection "$TMP/selection.json" \
    --pre-recommendation "$TMP/recommendation.json" --verify true \
    > "$TMP/bound.out" 2> "$TMP/bound.err"
if [[ $? == 0 ]] && python3 -B - "$bound" "$TMP/selection.json" "$TMP/recommendation.json" <<'PY'
import json,sys
receipt=json.load(open(sys.argv[1]))
assert receipt['caller_selection']==json.load(open(sys.argv[2]))
assert receipt['pre_dispatch_recommendation']==json.load(open(sys.argv[3]))
assert receipt['recommendations_participated_in_acceptance'] is False
PY
then ok "valid selection and pre-dispatch advisory bind without gate authority"; else bad "valid selection and pre-dispatch advisory bind without gate authority"; fi

for optional_mode in selection recommendation; do
    reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
    optional_target="$RECEIPTS/$optional_mode-only.json"
    optional_args=()
    if [[ "$optional_mode" == selection ]]; then
        optional_args=(--selection "$TMP/selection.json")
    else
        optional_args=(--pre-recommendation "$TMP/recommendation.json")
    fi
    "$VERIFY" --receipt "$optional_target" --envelope "$TMP/honest.json" \
        --repo "$REPO" --base "$BASE" "${optional_args[@]}" --verify true \
        >/dev/null 2>&1
    if [[ $? == 0 && -f "$optional_target" ]]; then ok "$optional_mode input is independently optional"; else bad "$optional_mode input is independently optional"; fi
done

"$ROOT/model-recommendation.sh" --stage post-gate --selected-tier bulk \
    --evidence gate-accepted > "$TMP/post.json"
post_target="$RECEIPTS/post.json"
"$VERIFY" --receipt "$post_target" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --pre-recommendation "$TMP/post.json" --verify true >/dev/null 2>&1
if [[ $? == 64 && ! -e "$post_target" ]]; then ok "post-gate advisory cannot bind in P0-A"; else bad "post-gate advisory cannot bind in P0-A"; fi

python3 -B - "$TMP/recommendation.json" "$TMP/applied.json" <<'PY'
import json,sys
value=json.load(open(sys.argv[1])); value['applied']=True
json.dump(value,open(sys.argv[2],'w'))
PY
applied_target="$RECEIPTS/applied.json"
"$VERIFY" --receipt "$applied_target" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --pre-recommendation "$TMP/applied.json" --verify true >/dev/null 2>&1
if [[ $? == 64 && ! -e "$applied_target" ]]; then ok "advisory claiming application is rejected before gate"; else bad "advisory claiming application is rejected before gate"; fi

"$ROOT/model-recommendation.sh" --stage pre-dispatch --selected-tier cheap \
    --evidence bounded-routine > "$TMP/mismatch-recommendation.json"
mismatch_target="$RECEIPTS/mismatch-selection.json"
"$VERIFY" --receipt "$mismatch_target" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --selection "$TMP/selection.json" \
    --pre-recommendation "$TMP/mismatch-recommendation.json" --verify true >/dev/null 2>&1
if [[ $? == 64 && ! -e "$mismatch_target" ]]; then ok "selection and advisory mismatch is rejected before gate"; else bad "selection and advisory mismatch is rejected before gate"; fi

mkdir -p "$TMP/selection-bin"
cat > "$TMP/selection-bin/agy" <<'SH'
#!/usr/bin/env bash
[[ "$*" == "--version" ]] || exit 97
printf '1.1.16\n'
SH
chmod +x "$TMP/selection-bin/agy"
PATH="$TMP/selection-bin:$PATH" "$ROOT/model-selection.sh" \
    --model gemini-3.6-flash --effort high > "$TMP/direct-selection.json"
"$ROOT/model-recommendation.sh" --stage pre-dispatch \
    --selected-model gemini-3.6-flash --selected-effort high \
    --evidence batched-mechanical > "$TMP/direct-recommendation.json"
reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
direct_target="$RECEIPTS/direct-selection.json"
"$VERIFY" --receipt "$direct_target" --envelope "$TMP/honest.json" \
    --repo "$REPO" --base "$BASE" --selection "$TMP/direct-selection.json" \
    --pre-recommendation "$TMP/direct-recommendation.json" --verify true \
    >/dev/null 2>&1
if [[ $? == 0 ]] && python3 -B - "$direct_target" <<'PY'
import json,sys
value=json.load(open(sys.argv[1]))
selection=value['caller_selection']
assert selection['selection_mode']=='model-effort'
assert selection['user_model']=='gemini-3.6-flash'
assert selection['user_effort']=='high'
assert selection['resolved_agy_model']=='gemini-3.6-flash-high'
assert value['pre_dispatch_recommendation']['recommendation_only'] is True
assert value['pre_dispatch_recommendation']['applied'] is False
PY
then ok "direct model/effort provenance binds without changing selection"; else bad "direct model/effort provenance binds without changing selection"; fi

for direct_mutation in resolved provenance matrix; do
    python3 -B - "$TMP/direct-selection.json" "$TMP/direct-$direct_mutation.json" \
        "$direct_mutation" <<'PY'
import json,sys
source,target,mode=sys.argv[1:]
value=json.load(open(source))
if mode=='resolved': value['resolved_agy_model']='gemini-3.6-flash-low'
elif mode=='provenance': del value['user_effort_source']
elif mode=='matrix': value['matrix_sha256']='0'*64
json.dump(value,open(target,'w'),sort_keys=True); open(target,'a').write('\n')
PY
    invalid_direct_target="$RECEIPTS/direct-$direct_mutation.json"
    "$VERIFY" --receipt "$invalid_direct_target" --envelope "$TMP/honest.json" \
        --repo "$REPO" --base "$BASE" --selection "$TMP/direct-$direct_mutation.json" \
        --verify true >/dev/null 2>&1
    if [[ $? == 64 && ! -e "$invalid_direct_target" ]]; then ok "direct $direct_mutation mutation is rejected before gate"; else bad "direct $direct_mutation mutation is rejected before gate"; fi
done

echo
echo "privacy, canonical hashing, and mutation evidence:"
reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
private_target="$RECEIPTS/privacy.json"
private_command="printf PRIVATE-VERIFY-SECRET >/dev/null"
"$VERIFY" --receipt "$private_target" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --allow first --allow second --only a.txt \
    --verify "$private_command" --verify true >/dev/null 2>&1
if [[ $? == 0 ]] && python3 -B - "$private_target" "$REPO" "$TMP" "$private_command" <<'PY'
import hashlib,json,sys
raw=open(sys.argv[1],'rb').read(); value=json.loads(raw)
for forbidden in sys.argv[2:]: assert forbidden.encode() not in raw
assert value['verifiers']==[
 {'label':'verify-001','command_sha256':hashlib.sha256(sys.argv[4].encode()).hexdigest()},
 {'label':'verify-002','command_sha256':hashlib.sha256(b'true').hexdigest()}]
assert not any(k in value for k in ('repo','command','diff','prompt','log','provider'))
PY
then ok "receipt exposes hashes and labels but no paths, commands, prose, or logs"; else bad "receipt exposes hashes and labels but no paths, commands, prose, or logs"; fi

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
mutation_target="$RECEIPTS/mutation.json"
"$VERIFY" --receipt "$mutation_target" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --verify "printf mutation >> '$REPO/a.txt'" >/dev/null 2>&1
if [[ $? == 14 ]] && python3 -B - "$mutation_target" <<'PY'
import json,sys
value=json.load(open(sys.argv[1]))
assert value['verdict']=='rejected'
assert value['initial_candidate_state_sha256'] != value['final_candidate_state_sha256']
PY
then ok "verifier mutation is rejected and gate digests preserve the change"; else bad "verifier mutation is rejected and gate digests preserve the change"; fi

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
first_order="$RECEIPTS/order-one.json"; second_order="$RECEIPTS/order-two.json"
"$VERIFY" --receipt "$first_order" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --allow one --allow two --verify true >/dev/null 2>&1
"$VERIFY" --receipt "$second_order" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --allow two --allow one --verify true >/dev/null 2>&1
if python3 -B - "$first_order" "$second_order" <<'PY'
import json,sys
one=json.load(open(sys.argv[1])); two=json.load(open(sys.argv[2]))
assert one['path_policy_sha256'] != two['path_policy_sha256']
PY
then ok "ordered path policy changes its canonical binding hash"; else bad "ordered path policy changes its canonical binding hash"; fi

echo
echo "receipt validation and trusted external bindings:"
python3 -B "$VALIDATOR" validate --receipt "$RECEIPTS/pass.json" \
    --envelope "$TMP/honest.json" >/dev/null 2>&1
if [[ $? == 0 ]]; then ok "validator accepts receipt with matching envelope binding"; else bad "validator accepts receipt with matching envelope binding"; fi

write_envelope other.json "$NO_EDITS"
python3 -B "$VALIDATOR" validate --receipt "$RECEIPTS/pass.json" \
    --envelope "$TMP/other.json" >/dev/null 2>&1
if [[ $? == 1 ]]; then ok "validator rejects separately bound envelope mismatch"; else bad "validator rejects separately bound envelope mismatch"; fi

pass_initial="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["initial_candidate_state_sha256"])' "$RECEIPTS/pass.json")"
pass_final="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["final_candidate_state_sha256"])' "$RECEIPTS/pass.json")"
python3 -B "$VALIDATOR" validate --receipt "$RECEIPTS/pass.json" \
    --initial-state-digest "$pass_initial" >/dev/null 2>&1
if [[ $? == 0 ]]; then ok "validator accepts separately trusted candidate digest"; else bad "validator accepts separately trusted candidate digest"; fi
python3 -B "$VALIDATOR" validate --receipt "$RECEIPTS/pass.json" \
    --initial-state-digest "$(printf 'f%.0s' {1..64})" >/dev/null 2>&1
if [[ $? == 1 ]]; then ok "validator rejects separately trusted candidate digest mismatch"; else bad "validator rejects separately trusted candidate digest mismatch"; fi

python3 -B "$VALIDATOR" validate --receipt "$RECEIPTS/pass.json" \
    --final-state-digest "$pass_final" >/dev/null 2>&1
if [[ $? == 0 ]]; then ok "validator accepts separately trusted final candidate digest"; else bad "validator accepts separately trusted final candidate digest"; fi
python3 -B "$VALIDATOR" validate --receipt "$RECEIPTS/pass.json" \
    --final-state-digest "$(printf 'e%.0s' {1..64})" >/dev/null 2>&1
if [[ $? == 1 ]]; then ok "validator rejects final candidate digest mismatch"; else bad "validator rejects final candidate digest mismatch"; fi

python3 -B "$VALIDATOR" validate --receipt "$bound" \
    --selection "$TMP/selection.json" \
    --pre-recommendation "$TMP/recommendation.json" >/dev/null 2>&1
if [[ $? == 0 ]]; then ok "validator accepts matching selection and advisory bindings"; else bad "validator accepts matching selection and advisory bindings"; fi
"$ROOT/model-selection.sh" --tier cheap --tier-source cli > "$TMP/cheap-selection.json"
python3 -B "$VALIDATOR" validate --receipt "$bound" \
    --selection "$TMP/cheap-selection.json" >/dev/null 2>&1
if [[ $? == 1 ]]; then ok "validator rejects separately bound selection mismatch"; else bad "validator rejects separately bound selection mismatch"; fi
python3 -B "$VALIDATOR" validate --receipt "$bound" \
    --pre-recommendation "$TMP/mismatch-recommendation.json" >/dev/null 2>&1
if [[ $? == 1 ]]; then ok "validator rejects separately bound advisory mismatch"; else bad "validator rejects separately bound advisory mismatch"; fi

for mutation in schema extra outcome duplicate; do
    mutated="$TMP/receipt-$mutation.json"
    python3 -B - "$RECEIPTS/pass.json" "$mutated" "$mutation" <<'PY'
import json,sys
source,target,mode=sys.argv[1:]
raw=open(source).read()
value=json.loads(raw)
if mode=='schema': value['schema_version']=2
elif mode=='extra': value['private_path']='/PRIVATE/PATH'
elif mode=='outcome': value['gate_outcome']='human-required'
if mode=='duplicate':
    open(target,'w').write('{"schema_version":1,'+raw[1:])
else:
    json.dump(value,open(target,'w'),sort_keys=True,separators=(',',':'))
    open(target,'a').write('\n')
PY
    python3 -B "$VALIDATOR" validate --receipt "$mutated" >/dev/null 2>&1
    if [[ $? == 1 ]]; then ok "validator rejects $mutation receipt mutation"; else bad "validator rejects $mutation receipt mutation"; fi
done

python3 -B - "$RECEIPTS/pass.json" "$TMP/unsigned-rewrite.json" <<'PY'
import json,sys
value=json.load(open(sys.argv[1])); value['envelope_sha256']='f'*64
json.dump(value,open(sys.argv[2],'w'),sort_keys=True,separators=(',',':')); open(sys.argv[2],'a').write('\n')
PY
python3 -B "$VALIDATOR" validate --receipt "$TMP/unsigned-rewrite.json" >/dev/null 2>&1
unsigned_rc=$?
python3 -B "$VALIDATOR" validate --receipt "$TMP/unsigned-rewrite.json" \
    --envelope "$TMP/honest.json" >/dev/null 2>&1
bound_unsigned_rc=$?
if [[ "$unsigned_rc" == 0 && "$bound_unsigned_rc" == 1 ]] \
        && grep -Fq '"tamper_evident":false' "$TMP/unsigned-rewrite.json"; then
    ok "unsigned rewrite needs a separately trusted binding and is never called tamper-evident"
else
    bad "unsigned rewrite needs a separately trusted binding and is never called tamper-evident"
fi

echo
echo "gate protocol failures publish nothing:"
FAKE_RUNTIME="$TMP/fake-runtime"
cp -R "$ROOT/skills/agy-worker/runtime" "$FAKE_RUNTIME"
cat > "$FAKE_RUNTIME/qa-gate.sh" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
fd=''; base=''; envelope=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --evidence-fd) fd="$2"; shift 2 ;;
        --evidence-token) token="$2"; shift 2 ;;
        --base) base="$2"; shift 2 ;;
        --envelope) envelope="$2"; shift 2 ;;
        *) if [[ $# -ge 2 && "$1" != --expect-edits ]]; then shift 2; else shift; fi ;;
    esac
done
case "${FAKE_GATE_MODE:-missing}" in
    exit64) exit 64 ;;
    unknown) exit 99 ;;
    signal) kill -TERM "$$" ;;
    missing) exit 0 ;;
    malformed) printf '{bad\n' >&"$fd"; exit 0 ;;
esac
payload="$(python3 -B - "$base" "$envelope" "${FAKE_GATE_MODE:-}" <<'PY'
import hashlib,json,sys
base,envelope,mode=sys.argv[1:]
envelope_hash=hashlib.sha256(open(envelope,'rb').read()).hexdigest()
gate_exit=10 if mode=='wrong-exit' else 0
value={'schema_version':1,'kind':'agy-worker-gate-evidence','resolved_base':base,
 'envelope_sha256':'0'*64 if mode=='mismatch' else envelope_hash,
 'initial_candidate_state_sha256':'1'*64,'final_candidate_state_sha256':'1'*64,
 'gate_exit':gate_exit,'gate_outcome':'scope-violation' if gate_exit==10 else 'gate-passed'}
print(json.dumps(value,sort_keys=True,separators=(',',':')))
PY
)"
printf '%s\n' "$payload" >&"$fd"
if [[ "${FAKE_GATE_MODE:-}" == duplicate ]]; then printf '%s\n' "$payload" >&"$fd"; fi
exit 0
SH
chmod +x "$FAKE_RUNTIME/qa-gate.sh"
for protocol_mode in missing malformed duplicate mismatch wrong-exit unknown signal; do
    protocol_target="$RECEIPTS/protocol-$protocol_mode.json"
    FAKE_GATE_MODE="$protocol_mode" \
        "$FAKE_RUNTIME/verify-job.sh" --receipt "$protocol_target" \
        --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" --verify true \
        >/dev/null 2>&1
    if [[ $? == 70 && ! -e "$protocol_target" ]]; then ok "$protocol_mode gate protocol failure exits 70 with no receipt"; else bad "$protocol_mode gate protocol failure exits 70 with no receipt"; fi
done
protocol64_target="$RECEIPTS/protocol-64.json"
FAKE_GATE_MODE=exit64 "$FAKE_RUNTIME/verify-job.sh" --receipt "$protocol64_target" \
    --envelope "$TMP/honest.json" --repo "$REPO" --base "$BASE" --verify true \
    >/dev/null 2>&1
if [[ $? == 64 && ! -e "$protocol64_target" ]]; then ok "gate preflight 64 passes through with no receipt"; else bad "gate preflight 64 passes through with no receipt"; fi

echo
echo "real wrapper interruption publishes nothing and closes the gate group:"
for signal_name in HUP INT TERM; do
    reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
    signal_target="$RECEIPTS/signal-$signal_name.json"
    signal_ready="$TMP/signal-$signal_name.ready"
    signal_child="$TMP/signal-$signal_name.child"
    signal_command="printf '%s\\n' \"\$\$\" > '$signal_child'; : > '$signal_ready'; while :; do /bin/sleep 1; done"
    "$VERIFY" --receipt "$signal_target" --envelope "$TMP/honest.json" \
        --repo "$REPO" --base "$BASE" --verify "$signal_command" \
        > "$TMP/signal-$signal_name.out" 2> "$TMP/signal-$signal_name.err" &
    wrapper_pid=$!
    ready=0
    for (( poll=0; poll<100; poll++ )); do
        if [[ -s "$signal_child" && -e "$signal_ready" ]]; then ready=1; break; fi
        /bin/sleep 0.05
    done
    if (( ready )); then kill -s "$signal_name" "$wrapper_pid" 2>/dev/null || true; fi
    completed=0
    for (( poll=0; poll<100; poll++ )); do
        if ! kill -0 "$wrapper_pid" 2>/dev/null; then completed=1; break; fi
        /bin/sleep 0.05
    done
    if (( completed )); then wait "$wrapper_pid"; signal_rc=$?; else signal_rc=99; fi
    verifier_pid="$(cat "$signal_child" 2>/dev/null || true)"
    child_gone=0
    for (( poll=0; poll<40; poll++ )); do
        if [[ -n "$verifier_pid" ]] && ! kill -0 "$verifier_pid" 2>/dev/null; then child_gone=1; break; fi
        /bin/sleep 0.05
    done
    if (( ready && completed && child_gone )) \
            && [[ "$signal_rc" == 70 && ! -e "$signal_target" ]]; then
        ok "$signal_name interruption exits 70, publishes nothing, and closes verifier"
    else
        bad "$signal_name interruption exits 70, publishes nothing, and closes verifier"
        kill -KILL "$wrapper_pid" "$verifier_pid" 2>/dev/null || true
        wait "$wrapper_pid" 2>/dev/null || true
    fi
done

echo
echo "deterministic wrapper-lifetime interruption cleanup:"
SIGNAL_PARENT="$TMP/checkpoint-signals"
mkdir -m 700 "$SIGNAL_PARENT"
SIGNAL_PARENT="$(CDPATH= cd -- "$SIGNAL_PARENT" && pwd -P)"
checkpoint_stages=(
    snapshot-created
    snapshot-before-fsync
    handoff-created
    publication-temp-created
    publication-before-file-fsync
    publication-before-link
    publication-after-link
    publication-before-first-parent-fsync
    publication-before-second-parent-fsync
    wrapper-cleanup-start
    wrapper-cleanup-parent-fsync
)
reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
for checkpoint_stage in "${checkpoint_stages[@]}"; do
    if python3 -B - "$ROOT/skills/agy-worker/runtime/scripts" \
            "$checkpoint_stage" "$SIGNAL_PARENT" "$TMP/honest.json" \
            "$REPO" "$BASE" >"$TMP/checkpoint-$checkpoint_stage.out" \
            2>"$TMP/checkpoint-$checkpoint_stage.err" <<'PY'
import importlib.util
import os
from pathlib import Path
import signal
import sys

scripts,stage,parent_text,envelope,repo,base=sys.argv[1:]
sys.path.insert(0,scripts)
spec=importlib.util.spec_from_file_location(
    'receipt_module',Path(scripts)/'evidence_receipt.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
parent=Path(parent_text)
for signal_name in ('HUP','INT','TERM'):
    target=parent/f'{stage}-{signal_name}.json'
    triggered={'value':False}
    def checkpoint(name):
        if name == stage and not triggered['value']:
            triggered['value']=True
            os.kill(os.getpid(), getattr(signal, f'SIG{signal_name}'))
    module.interruption_checkpoint=checkpoint
    rc=module.main([
        'verify','--receipt',str(target),'--envelope',envelope,
        '--repo',repo,'--base',base,'--only','a.txt','--verify','true'])
    assert triggered['value'] and rc == 70
    assert not target.exists() and not target.is_symlink()
    assert not tuple(parent.iterdir())
PY
    then
        ok "$checkpoint_stage handles HUP/INT/TERM with exit 70 and no artifact"
    else
        bad "$checkpoint_stage handles HUP/INT/TERM with exit 70 and no artifact"
    fi
done

if python3 -B - "$ROOT/skills/agy-worker/runtime/scripts" \
        "$SIGNAL_PARENT" "$TMP/honest.json" "$REPO" "$BASE" \
        >"$TMP/signal-replacement.out" 2>"$TMP/signal-replacement.err" <<'PY'
import importlib.util
import os
from pathlib import Path
import signal
import sys

scripts,parent_text,envelope,repo,base=sys.argv[1:]
sys.path.insert(0,scripts)
spec=importlib.util.spec_from_file_location(
    'receipt_module',Path(scripts)/'evidence_receipt.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
parent=Path(parent_text); target=parent/'attacker-replacement.json'
triggered={'value':False}
def checkpoint(name):
    if name == 'wrapper-cleanup-start' and not triggered['value']:
        triggered['value']=True
        target.unlink()
        target.write_bytes(b'attacker replacement\n')
        os.kill(os.getpid(), signal.SIGTERM)
module.interruption_checkpoint=checkpoint
rc=module.main([
    'verify','--receipt',str(target),'--envelope',envelope,
    '--repo',repo,'--base',base,'--only','a.txt','--verify','true'])
assert triggered['value'] and rc == 70
assert target.read_bytes() == b'attacker replacement\n'
assert tuple(parent.iterdir()) == (target,)
target.unlink()
PY
then
    ok "signal cleanup preserves a raced attacker replacement by pinned inode"
else
    bad "signal cleanup preserves a raced attacker replacement by pinned inode"
fi

echo
echo "atomic no-overwrite publication failure cleanup:"
INJECT_PARENT="$TMP/injection"
mkdir -m 700 "$INJECT_PARENT"
INJECT_PARENT="$(CDPATH= cd -- "$INJECT_PARENT" && pwd -P)"
if python3 -B - "$ROOT/skills/agy-worker/runtime/scripts" \
        "$ROOT/skills/agy-worker/runtime/schemas/evidence-receipt.schema.json" \
        "$ROOT/skills/agy-worker/runtime/scripts/model-recommendation.py" \
        "$RECEIPTS/pass.json" "$INJECT_PARENT" <<'PY'
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

scripts,schema_path,recommender,receipt_path,parent_text=sys.argv[1:]
sys.path.insert(0,scripts)
spec=importlib.util.spec_from_file_location('receipt_module',Path(scripts)/'evidence_receipt.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
parent=Path(parent_text); target=parent/'restrictive-umask.json'
value=json.load(open(receipt_path)); schema=module.load_schema(Path(schema_path))
old_umask=os.umask(0o777)
try:
    module.publish_receipt(target,parent,value,schema,Path(recommender))
finally:
    os.umask(old_umask)
assert stat.S_IMODE(target.lstat().st_mode)==0o600
target.unlink()
PY
then ok "publication forces exact mode 0600 under a restrictive umask"; else bad "publication forces exact mode 0600 under a restrictive umask"; fi
for injection in validation file-fsync link parent-fsync; do
    if python3 -B - "$ROOT/skills/agy-worker/runtime/scripts" \
            "$ROOT/skills/agy-worker/runtime/schemas/evidence-receipt.schema.json" \
            "$ROOT/skills/agy-worker/runtime/scripts/model-recommendation.py" \
            "$RECEIPTS/pass.json" "$INJECT_PARENT" "$injection" <<'PY'
import importlib.util
import json
import os
from pathlib import Path
import sys

scripts,schema_path,recommender,receipt_path,parent_text,mode=sys.argv[1:]
sys.path.insert(0,scripts)
spec=importlib.util.spec_from_file_location('receipt_module',Path(scripts)/'evidence_receipt.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
parent=Path(parent_text); target=parent/f'{mode}.json'
value=json.load(open(receipt_path)); schema=module.load_schema(Path(schema_path))
original_validate=module.validate_receipt
original_fsync=module.os.fsync
original_link=module.os.link
calls={'fsync':0}
if mode=='validation':
    def reject(*_args,**_kwargs): raise module.ValidationFailure('injected')
    module.validate_receipt=reject
elif mode in ('file-fsync','parent-fsync'):
    def fail_fsync(fd):
        calls['fsync']+=1
        if (mode=='file-fsync' and calls['fsync']==1) or (mode=='parent-fsync' and calls['fsync']==2):
            raise OSError('injected')
        return original_fsync(fd)
    module.os.fsync=fail_fsync
elif mode=='link':
    def fail_link(*_args,**_kwargs): raise OSError('injected')
    module.os.link=fail_link
try:
    try:
        module.publish_receipt(target,parent,value,schema,Path(recommender))
    except module.PublicationFailure:
        pass
    else:
        raise AssertionError('injection unexpectedly published')
finally:
    module.validate_receipt=original_validate
    module.os.fsync=original_fsync
    module.os.link=original_link
assert not target.exists() and not target.is_symlink()
assert not list(parent.glob(f'.{target.name}.receipt.*'))
PY
    then ok "$injection failure leaves no final or partial receipt"; else bad "$injection failure leaves no final or partial receipt"; fi
done

if python3 -B - "$ROOT/skills/agy-worker/runtime/scripts" \
        "$ROOT/skills/agy-worker/runtime/schemas/evidence-receipt.schema.json" \
        "$ROOT/skills/agy-worker/runtime/scripts/model-recommendation.py" \
        "$RECEIPTS/pass.json" "$INJECT_PARENT" <<'PY'
import importlib.util
import json
import os
from pathlib import Path
import sys

scripts,schema_path,recommender,receipt_path,parent_text=sys.argv[1:]
sys.path.insert(0,scripts)
spec=importlib.util.spec_from_file_location('receipt_module',Path(scripts)/'evidence_receipt.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
parent=Path(parent_text); target=parent/'replacement-during-fsync.json'
value=json.load(open(receipt_path)); schema=module.load_schema(Path(schema_path))
original_fsync=module.os.fsync
calls={'fsync':0}
def replace_then_fail(fd):
    calls['fsync']+=1
    if calls['fsync']==2:
        target.unlink()
        target.write_bytes(b'attacker replacement\n')
        raise OSError('injected')
    return original_fsync(fd)
module.os.fsync=replace_then_fail
try:
    try:
        module.publish_receipt(target,parent,value,schema,Path(recommender))
    except module.PublicationFailure:
        pass
    else:
        raise AssertionError('injection unexpectedly published')
finally:
    module.os.fsync=original_fsync
assert target.read_bytes()==b'attacker replacement\n'
assert not list(parent.glob(f'.{target.name}.receipt.*'))
target.unlink()
PY
then ok "cleanup preserves a raced replacement after publication identity changes"; else bad "cleanup preserves a raced replacement after publication identity changes"; fi

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
race_target="$RECEIPTS/race.json"
"$VERIFY" --receipt "$race_target" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --verify "printf attacker > '$race_target'" >/dev/null 2>&1
race_rc=$?
if [[ "$race_rc" == 74 && "$(<"$race_target")" == attacker ]]; then
    ok "atomic hard-link publication refuses a raced target without overwriting it"
else
    bad "atomic hard-link publication refuses a raced target without overwriting it"
fi
rm -f -- "$race_target"

reset_repo; printf 'worker edit\n' > "$REPO/a.txt"
mode_target="$RECEIPTS/parent-mode.json"
"$VERIFY" --receipt "$mode_target" --envelope "$TMP/honest.json" --repo "$REPO" \
    --base "$BASE" --verify "chmod 755 '$RECEIPTS'" >/dev/null 2>&1
mode_rc=$?
chmod 700 "$RECEIPTS"
if [[ "$mode_rc" == 74 && ! -e "$mode_target" ]]; then ok "parent privacy drift before publication exits 74 with no receipt"; else bad "parent privacy drift before publication exits 74 with no receipt"; fi

if [[ -z "$(find "$RECEIPTS" -maxdepth 1 -name '.*receipt*' -print -quit)" ]]; then
    ok "successful and failed runs leave no private publication temporary"
else
    bad "successful and failed runs leave no private publication temporary"
fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
