#!/usr/bin/env bash
# Offline adversarial suite for qa-gate.sh: no agy, network, or API keys.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/../qa-gate.sh"
SCHEMA="$HERE/../skills/agy-worker/runtime/schemas/worker-result.schema.json"
TMP="$(mktemp -d -t agyworker-tests.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0

envelope() { printf '%s' "$2" > "$TMP/$1"; }

check() {
    local name="$1" want="$2" env_file="$3"; shift 3
    "$GATE" --envelope "$TMP/$env_file" --repo "$TMP/repo" --base "$BASE_COMMIT" "$@" >/dev/null 2>&1
    local got=$?
    if [[ "$got" == "$want" ]]; then
        printf '  ok   %-52s exit %s\n' "$name" "$got"; pass=$((pass+1))
    else
        printf '  FAIL %-52s exit %s (wanted %s)\n' "$name" "$got" "$want"; fail=$((fail+1))
    fi
}

argv_json() {
    python3 -I -S -B -c \
        'import json,sys;print(json.dumps(sys.argv[1:],ensure_ascii=True,separators=(",",":")))' \
        "$@"
}

mkdir -p "$TMP/repo"
cd "$TMP/repo"
git init -q .
git config user.email test@example.com
git config user.name test
printf 'original\n' > a.txt
printf 'dot env\n' > .env
printf 'plain env\n' > env
printf 'ignored.tmp\n' > .gitignore
git add -A && git commit -qm init
BASE_COMMIT="$(git rev-parse HEAD)"
printf 'modified by worker\n' > a.txt

HONEST='{"status":"completed","summary":"done","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":0.9,"requires_human":false}'
envelope honest.json "$HONEST"
envelope abs.json "{\"status\":\"completed\",\"summary\":\"done\",\"files_changed\":[{\"path\":\"$TMP/repo/a.txt\",\"change\":\"modified\"}],\"commands_run\":[],\"tests_run\":[],\"risks\":[],\"open_questions\":[],\"confidence\":1,\"requires_human\":false}"

echo "qa-gate.sh test suite"
echo
echo "acceptance requires independent evidence:"
check "completed, exact scope, driver verification" 0 honest.json --expect-edits --verify-argv '["true"]'
check "absolute path normalizes inside repo" 0 abs.json --verify-argv '["true"]'
check "exact --only policy accepts target" 0 honest.json --only a.txt --verify-argv '["true"]'
check "missing --verify never accepts" 64 honest.json
check "empty --verify is not evidence" 64 honest.json --verify ''
check "whitespace --verify is not evidence" 64 honest.json --verify '   '

echo
echo "worker escalation is routed, never accepted:"
envelope partial.json '{"status":"partial","summary":"partial","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":0.4,"requires_human":false}'
check "partial status" 15 partial.json
envelope failed.json '{"status":"failed","summary":"failed","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":0.1,"requires_human":false}'
check "failed status" 15 failed.json
envelope blocked_declared.json '{"status":"blocked","summary":"blocked","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":["decision"],"confidence":0.2,"requires_human":true}'
check "blocked status with declared diff" 15 blocked_declared.json
envelope human.json '{"status":"completed","summary":"needs review","files_changed":[{"path":"a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":["decision"],"confidence":0.5,"requires_human":true}'
check "completed but requires human" 15 human.json
envelope blocked_hidden.json '{"status":"blocked","summary":"blocked","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":["decision"],"confidence":0.2,"requires_human":true}'
check "blocked cannot hide undeclared edits" 10 blocked_hidden.json

echo
echo "scope and path policy:"
printf 'sneaky\n' > "$TMP/repo/b.txt"
check "undeclared file" 10 honest.json --verify-argv '["true"]'
rm -f "$TMP/repo/b.txt"
envelope phantom.json '{"status":"completed","summary":"done","files_changed":[{"path":"never.txt","change":"created"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
check "declared file did not change" 10 phantom.json --verify-argv '["true"]'
check "--only rejects paths outside policy" 10 honest.json --only 'tests/**' --verify-argv '["true"]'
envelope escape.json '{"status":"completed","summary":"done","files_changed":[{"path":"../a.txt","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
check "claimed path cannot escape repository" 10 escape.json --verify-argv '["true"]'
envelope wrong_kind.json '{"status":"completed","summary":"done","files_changed":[{"path":"a.txt","change":"created"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
check "declared change kind must match Git" 10 wrong_kind.json --verify-argv '["true"]'

git checkout -q -- a.txt
printf 'new file\n' > b.txt
envelope created.json '{"status":"completed","summary":"done","files_changed":[{"path":"b.txt","change":"created"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
check "created change kind is accepted when accurate" 0 created.json --verify-argv '["true"]'
rm -f b.txt
printf 'modified by worker\n' > a.txt

printf 'ignored worker output\n' > ignored.tmp
check "ignored untracked file is still in scope" 10 honest.json --verify-argv '["true"]'
check "explicitly allowlisted ignored file is auditable" 0 honest.json --allow ignored.tmp --verify-argv '["true"]'
check "verification cannot mutate an allowlisted ignored file" 14 honest.json --allow ignored.tmp \
    --verify-argv "$(argv_json /usr/bin/python3 -I -S -B -c 'from pathlib import Path;import sys;Path(sys.argv[1]).write_bytes(Path(sys.argv[1]).read_bytes()+b"changed\\n")' "$TMP/repo/ignored.tmp")"
rm -f ignored.tmp

git checkout -q -- a.txt
printf 'changed dot env\n' > .env
envelope env_collision.json '{"status":"completed","summary":"done","files_changed":[{"path":"env","change":"modified"}],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
check ".env is not collapsed into env" 10 env_collision.json --verify-argv '["true"]'
git checkout -q -- .env
printf 'modified by worker\n' > a.txt

echo
echo "untrusted commands never execute:"
envelope command_only.json "{\"status\":\"completed\",\"summary\":\"done\",\"files_changed\":[{\"path\":\"a.txt\",\"change\":\"modified\"}],\"commands_run\":[\"touch $TMP/command-sentinel\"],\"tests_run\":[],\"risks\":[],\"open_questions\":[],\"confidence\":1,\"requires_human\":false}"
check "worker command claim rejected as data" 11 command_only.json --verify-argv '["true"]'
envelope command_claim.json "{\"status\":\"completed\",\"summary\":\"done\",\"files_changed\":[{\"path\":\"a.txt\",\"change\":\"modified\"}],\"commands_run\":[],\"tests_run\":[{\"command\":\"touch $TMP/sentinel\",\"passed\":true}],\"risks\":[],\"open_questions\":[],\"confidence\":1,\"requires_human\":false}"
check "worker test command rejected as data" 11 command_claim.json --verify-argv '["true"]'
if [[ ! -e "$TMP/sentinel" && ! -e "$TMP/command-sentinel" ]]; then
    printf '  ok   %-52s\n' "worker commands were not executed"; pass=$((pass+1))
else
    printf '  FAIL %-52s\n' "worker commands were not executed"; fail=$((fail+1))
fi

echo
echo "driver verification is bounded and read-only:"
check "driver verification failure" 14 honest.json --verify-argv '["false"]'
SAFE_VERIFIER_ENV=keep check "ambient verifier variable is absent by default" 0 honest.json \
    --verify-argv "$(argv_json /usr/bin/python3 -I -S -B -c 'import os,sys;sys.exit(1 if "SAFE_VERIFIER_ENV" in os.environ else 0)')"
SAFE_VERIFIER_ENV=keep check "direct explicit verifier variable requires receipt wrapper" 64 honest.json \
    --verify-env SAFE_VERIFIER_ENV --verify-argv '["true"]'
SAFE_VERIFIER_ENV=keep check "unsafe verifier variable name is rejected" 64 honest.json \
    --verify-env PYTHONPATH --verify-argv '["true"]'
AGY_WORKER_SCHEMA="$SCHEMA" check "schema selector cannot be a verifier opt-in" 64 honest.json \
    --verify-env AGY_WORKER_SCHEMA --verify-argv '["true"]'
GIT_DIR=.git check "Git control cannot be a verifier opt-in" 64 honest.json \
    --verify-env GIT_DIR --verify-argv '["true"]'
check "driver verification cannot rewrite declared file" 14 honest.json \
    --verify-argv "$(argv_json /usr/bin/python3 -I -S -B -c 'from pathlib import Path;import sys;Path(sys.argv[1]).write_bytes(Path(sys.argv[1]).read_bytes()+b"verify mutation\\n")' "$TMP/repo/a.txt")"
printf 'modified by worker\n' > "$TMP/repo/a.txt"
check "driver verification cannot create undeclared file" 14 honest.json \
    --verify-argv "$(argv_json /usr/bin/python3 -I -S -B -c 'from pathlib import Path;import sys;Path(sys.argv[1]).write_text("artifact")' "$TMP/repo/verify.out")"
rm -f "$TMP/repo/verify.out"

echo
echo "verifier mode and argument boundaries:"
check "non-canonical argv JSON is rejected" 64 honest.json --verify-argv '[ "true" ]'
check "empty argv is rejected" 64 honest.json --verify-argv '[]'
check "non-string argv element is rejected" 64 honest.json --verify-argv '["true",1]'
check "NUL-bearing argv element is rejected" 64 honest.json --verify-argv '["true","\u0000"]'
check "lone-surrogate argv element is rejected" 64 honest.json \
    --verify-argv '["true","\ud800"]'
check "direct shell interpreter is rejected in argv mode" 64 honest.json \
    --verify-argv '["/bin/bash","-c","true"]'
check "env-mediated shell interpreter is rejected in argv mode" 64 honest.json \
    --verify-argv '["/usr/bin/env","SAFE=1","zsh","-c","true"]'
split_sentinel="$TMP/env-split-shell-sentinel"
split_quote_spec="$(argv_json /usr/bin/env -S \
    "ba\"\"sh -c \"touch $split_sentinel\"")"
split_expansion_spec="$(argv_json /usr/bin/env -S \
    "z\${UNSET_FOR_AGY_PREVIEW}sh -c \"touch $split_sentinel\"")"
split_ok=1
for split_case in quote expansion; do
    if [[ "$split_case" == quote ]]; then split_spec="$split_quote_spec"; else split_spec="$split_expansion_spec"; fi
    "$GATE" --envelope "$TMP/honest.json" --repo "$TMP/repo" --base "$BASE_COMMIT" \
        --verify-argv "$split_spec" >"$TMP/env-split-$split_case.out" \
        2>"$TMP/env-split-$split_case.err"
    split_rc=$?
    [[ "$split_rc" == 64 && ! -s "$TMP/env-split-$split_case.out" ]] || split_ok=0
    grep -Fq "$split_sentinel" "$TMP/env-split-$split_case.err" && split_ok=0
    grep -Fq 'ba""sh' "$TMP/env-split-$split_case.err" && split_ok=0
done
if [[ "$split_ok" == 1 && ! -e "$split_sentinel" ]]; then
    printf '  ok   %-52s\n' "env split-string shell transforms fail before execution"; pass=$((pass+1))
else
    printf '  FAIL %-52s\n' "env split-string shell transforms fail before execution"; fail=$((fail+1))
fi
ordinary_env_argv="$(argv_json /usr/bin/env 'SAFE_LITERAL=space value' \
    /usr/bin/python3 -I -S -B -c \
    'import os,sys;sys.exit(0 if os.environ.get("SAFE_LITERAL")=="space value" else 1)')"
check "ordinary non-shell env argv remains literal" 0 honest.json \
    --verify-argv "$ordinary_env_argv"
literal_argv="$(argv_json /usr/bin/python3 -I -S -B -c \
    'import sys;expected=["space value","quote\"value","$value","semi;colon","and&&and","*.txt","snowman ☃"];sys.exit(0 if sys.argv[1:]==expected else 1)' \
    'space value' 'quote"value' '$value' 'semi;colon' 'and&&and' '*.txt' 'snowman ☃')"
check "argv preserves spaces quotes metacharacters globs and Unicode" 0 honest.json \
    --verify-argv "$literal_argv"
legacy_sentinel="PRIVATE-LEGACY-SENTINEL-$RANDOM"
"$GATE" --envelope "$TMP/honest.json" --repo "$TMP/repo" --base "$BASE_COMMIT" \
    --verify "$legacy_sentinel" >"$TMP/legacy.out" 2>"$TMP/legacy.err"
legacy_rc=$?
if [[ "$legacy_rc" == 64 ]] && grep -Fq 'migrate to --verify-argv' "$TMP/legacy.err" \
        && ! grep -Fq "$legacy_sentinel" "$TMP/legacy.err"; then
    printf '  ok   %-52s exit %s\n' "legacy shell needs opt-in without raw diagnostic" "$legacy_rc"; pass=$((pass+1))
else
    printf '  FAIL %-52s exit %s (wanted sanitized 64)\n' "legacy shell needs opt-in without raw diagnostic" "$legacy_rc"; fail=$((fail+1))
fi
check "explicit shell needs network acknowledgement" 64 honest.json \
    --verify-shell true --acknowledge-verifier-credential-access
check "explicit shell needs credential acknowledgement" 64 honest.json \
    --verify-shell true --acknowledge-verifier-network
check "explicit shell runs only with both acknowledgements" 0 honest.json \
    --verify-shell 'test -z "${HOME+x}"' \
    --acknowledge-verifier-network --acknowledge-verifier-credential-access
order_marker="$TMP/verifier-order"
first_order="$(argv_json /usr/bin/python3 -I -S -B -c 'from pathlib import Path;import sys;Path(sys.argv[1]).write_text("one")' "$order_marker")"
second_order="$(argv_json /usr/bin/python3 -I -S -B -c 'from pathlib import Path;import sys;p=Path(sys.argv[1]);sys.exit(0 if p.read_text()=="one" else 1)' "$order_marker")"
check "multiple argv verifiers preserve caller order" 0 honest.json \
    --verify-argv "$first_order" --verify-argv "$second_order"
rm -f "$order_marker"

echo
echo "schema validation:"
envelope malformed.json '{not json'
check "malformed JSON" 12 malformed.json --verify-argv '["true"]'
envelope missing.json '{"status":"completed","summary":"done"}'
check "missing required fields" 12 missing.json --verify-argv '["true"]'
envelope extra.json '{"status":"completed","summary":"done","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false,"surprise":true}'
check "additional property rejected" 12 extra.json --verify-argv '["true"]'
envelope confidence.json '{"status":"completed","summary":"done","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":9,"requires_human":false}'
check "out-of-range confidence rejected" 12 confidence.json --verify-argv '["true"]'
envelope nested_type.json '{"status":"completed","summary":"done","files_changed":[],"commands_run":[],"tests_run":[{"command":"true","passed":"yes"}],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
check "nested field type rejected" 12 nested_type.json --verify-argv '["true"]'
python3 - "$SCHEMA" "$TMP/unsupported-schema.json" <<'PY'
import json
import sys
schema = json.load(open(sys.argv[1]))
schema["properties"]["files_changed"]["items"]["properties"]["why"]["pattern"] = "x"
with open(sys.argv[2], "w") as handle:
    json.dump(schema, handle)
PY
AGY_WORKER_SCHEMA="$TMP/unsupported-schema.json" "$GATE" \
    --envelope "$TMP/honest.json" --repo "$TMP/repo" --base "$BASE_COMMIT" \
    --verify-argv '["true"]' >/dev/null 2>&1
got=$?
if [[ "$got" == 12 ]]; then
    printf '  ok   %-52s exit %s\n' "unsupported optional schema keyword fails closed" "$got"; pass=$((pass+1))
else
    printf '  FAIL %-52s exit %s (wanted 12)\n' "unsupported optional schema keyword fails closed" "$got"; fail=$((fail+1))
fi

oversized_envelope="$TMP/oversized-valid.json"
python3 - "$TMP/honest.json" "$oversized_envelope" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_bytes()
Path(sys.argv[2]).write_bytes(source + b" " * (1024 * 1024))
PY
internal_token="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"$GATE" --envelope "$oversized_envelope" --repo "$TMP/repo" \
    --base "$BASE_COMMIT" --verify-argv '["true"]' >/dev/null 2>&1
direct_got=$?
exec 9> "$TMP/oversized-evidence.jsonl"
AGY_WORKER_INTERNAL_EVIDENCE_TOKEN="$internal_token" \
AGY_WORKER_INTERNAL_PYTHON="/usr/bin/python3" \
    "$GATE" --evidence-token "$internal_token" \
        --envelope "$oversized_envelope" --repo "$TMP/repo" \
        --base "$BASE_COMMIT" --verify-argv '["true"]' --evidence-fd 9 \
        >/dev/null 2>&1
got=$?
exec 9>&-
if [[ "$direct_got" == 12 && "$got" == 70 && ! -s "$TMP/oversized-evidence.jsonl" ]]; then
    printf '  ok   %-52s exit %s/%s\n' "oversized valid envelope rejects before evidence" "$direct_got" "$got"; pass=$((pass+1))
else
    printf '  FAIL %-52s exit %s/%s (wanted 12/70)\n' "oversized valid envelope rejects before evidence" "$direct_got" "$got"; fail=$((fail+1))
fi

echo
echo "edit expectation and artifact allowlist:"
git checkout -q -- a.txt
envelope no_edits.json '{"status":"completed","summary":"read only","files_changed":[],"commands_run":[],"tests_run":[],"risks":[],"open_questions":[],"confidence":1,"requires_human":false}'
check "read-only completion allowed with verification" 0 no_edits.json --verify-argv '["true"]'
check "--expect-edits rejects no-op completion" 13 no_edits.json --expect-edits --verify-argv '["true"]'
printf 'artifact only\n' > out.o
check "allowlisted artifact does not satisfy --expect-edits" 13 no_edits.json --allow '*.o' --expect-edits --verify-argv '["true"]'
rm -f out.o
printf 'modified by worker\n' > a.txt
printf 'artifact\n' > out.o
check "undeclared artifact rejected" 10 honest.json --verify-argv '["true"]'
check "explicit artifact allowlist accepted" 0 honest.json --allow '*.o' --verify-argv '["true"]'
rm -f out.o

echo
echo "input canonicalization and git failures:"
cd "$TMP"
"$GATE" --envelope honest.json --repo repo --base "$BASE_COMMIT" --verify-argv '["true"]' >/dev/null 2>&1
got=$?
if [[ "$got" == 0 ]]; then
    printf '  ok   %-52s exit %s\n' "relative envelope survives different --repo" "$got"; pass=$((pass+1))
else
    printf '  FAIL %-52s exit %s (wanted 0)\n' "relative envelope survives different --repo" "$got"; fail=$((fail+1))
fi
check "invalid base fails closed" 64 honest.json --base does-not-exist --verify-argv '["true"]'
check "symbolic HEAD is rejected as a mutable base" 64 honest.json --base HEAD --verify-argv '["true"]'

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
