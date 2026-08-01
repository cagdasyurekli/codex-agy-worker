#!/usr/bin/env bash
# Offline test suite for qa-gate.sh — no agy, no network, no API keys.
#
# The gate is the security-critical half of this project: if it accepts a lying
# worker, the whole design is worthless. So every case here is an adversarial one.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/../qa-gate.sh"
TMP="$(mktemp -d -t agyworker-tests.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0

envelope() {  # envelope <file> <json>
    printf '%s' "$2" > "$TMP/$1"
}

check() {     # check <name> <expected_exit> <envelope> [extra gate args...]
    local name="$1" want="$2" env_file="$3"; shift 3
    "$GATE" --envelope "$TMP/$env_file" --repo "$TMP/repo" --base HEAD "$@" >/dev/null 2>&1
    local got=$?
    if [[ "$got" == "$want" ]]; then
        printf '  ok   %-45s exit %s\n' "$name" "$got"; pass=$((pass+1))
    else
        printf '  FAIL %-45s exit %s (wanted %s)\n' "$name" "$got" "$want"; fail=$((fail+1))
    fi
}

# --- fixture repo ------------------------------------------------------------
mkdir -p "$TMP/repo"
cd "$TMP/repo"
git init -q .
git config user.email test@example.com
git config user.name  test
echo "original" > a.txt
git add -A && git commit -qm init
echo "modified by the worker" > a.txt      # the one legitimate change

HONEST='{"status":"completed","summary":"s","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[{"command":"true","passed":true}],"risks":[],"open_questions":[],"confidence":0.9,"requires_human":false}'

echo "qa-gate.sh test suite"
echo

echo "accepting good work:"
envelope honest.json "$HONEST"
check "honest envelope, true test claim" 0 honest.json

envelope honest_fail.json '{"status":"partial","summary":"s","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[{"command":"false","passed":false}],"risks":["known failure"],"open_questions":[],"confidence":0.4,"requires_human":false}'
check "honest about a FAILING test" 0 honest_fail.json

envelope blocked.json '{"status":"blocked","summary":"hit a gate","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":["needs a decision"],"confidence":0.2,"requires_human":true}'
check "worker correctly escalates" 0 blocked.json

envelope abs.json "{\"status\":\"completed\",\"summary\":\"s\",\"files_changed\":[{\"path\":\"$TMP/repo/a.txt\",\"change\":\"modified\"}],\"commands_run\":[],\"tests_run\":[],\"risks\":[],\"open_questions\":[],\"confidence\":1,\"requires_human\":false}"
check "absolute path normalises to relative" 0 abs.json

echo
echo "rejecting bad work:"

# undeclared file
echo "sneaky" > "$TMP/repo/b.txt"
check "touched an UNDECLARED file" 10 honest.json
rm -f "$TMP/repo/b.txt"

envelope phantom.json '{"status":"completed","summary":"s","files_changed":[{"path":"never_touched.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
check "claims a file it did NOT change" 10 phantom.json

envelope liar.json '{"status":"completed","summary":"s","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[{"command":"false","passed":true}],"risks":[],"open_questions":[],"confidence":0.99,"requires_human":false}'
check "claims a FAILING test PASSED" 11 liar.json

envelope malformed.json '{not valid json'
check "malformed envelope" 12 malformed.json

envelope missing.json '{"status":"completed","summary":"s"}'
check "envelope missing required fields" 12 missing.json

# driver-owned verification catches work that is simply wrong, even when the
# envelope is internally consistent and the scope is clean.
check "driver verification FAILS" 14 honest.json --verify 'false'
check "driver verification passes" 0 honest.json --verify 'true'

echo
echo "allowlist:"
echo "build artifact" > "$TMP/repo/out.o"
check "undeclared file, not allowlisted" 10 honest.json
check "undeclared file, allowlisted" 0 honest.json --allow '*.o'
rm -f "$TMP/repo/out.o"

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
