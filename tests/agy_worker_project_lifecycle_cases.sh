# Project lifecycle cases sourced by test-agy-worker.sh after shared fixtures and
# helper functions are initialized.

printf 'project workflow initial implementation\n' | FAKE_DISPATCH_MODE=heartbeat-success \
    FAKE_HEARTBEAT_COUNT=2 AGY_TEST_WORKDIR="$TMP/project-worktree" start_worker project-cycles --workflow project \
    --idle-timeout 1s --hard-timeout 2s --max-runtime 8s > "$TMP/project-cycles.start" 2> "$TMP/project-cycles.err"
project_start_rc=$?
wait_terminal project-cycles "$TMP/project-cycles.start"
project_wait_rc=$?
control_worker status project-cycles > "$TMP/project-cycles.status"
project_sha="$(status_sha "$TMP/project-cycles.status")"
if [[ "$project_start_rc" == 0 && "$project_wait_rc" == 0 ]] && python3 - "$TMP/project-cycles.status" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["workflow"] == "project"
assert value["status"] == "succeeded"
assert value["phase"] == "awaiting-verification"
assert value["cycle"] == 1 and value["max_cycles"] == 5
assert value["assurance"] is None
assert value["resume_available"] is False and value["continue_available"] is True
assert value["check_counts"] == {"passed": 0, "failed": 0, "advisory": 0, "missing": 0}
PY
then
    ok "project workflow exposes pending Codex-owned verification without changing resume semantics"
else
    bad "project workflow status contract"
fi

printf 'scoped project delete target\n' > "$TMP/project-worktree/scoped-delete.txt"
project_scoped_delete_scope="$TMP/project-scoped-delete.scope.json"
printf '%s\n' \
    '{"schema_version":1,"kind":"agy-worker-provider-scope","read":[{"path":"scoped-delete.txt","kind":"file"}],"write":[{"path":"scoped-delete.txt","kind":"file"}]}' \
    > "$project_scoped_delete_scope"
chmod 0600 "$project_scoped_delete_scope"
project_scoped_delete_workdir="$(cd "$TMP/project-worktree" && pwd -P)"
project_scoped_delete_transmission="$(
    "$WORKER" transmission-preview --workdir "$project_scoped_delete_workdir" \
        --provider-scope "$project_scoped_delete_scope" --format json \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["transmission_sha256"])'
)"
printf 'delete the scoped file\n' | FAKE_DELETE_FROM_BOUND_ROOT=scoped-delete.txt \
    AGY_TEST_WORKDIR="$project_scoped_delete_workdir" start_worker project-scoped-delete \
    --workflow project --max-cycles 2 --provider-scope "$project_scoped_delete_scope" \
    --approve-transmission-sha "$project_scoped_delete_transmission" \
    > "$TMP/project-scoped-delete.start" 2> "$TMP/project-scoped-delete.err"
project_scoped_delete_start_rc=$?
wait_terminal project-scoped-delete "$TMP/project-scoped-delete.start"
project_scoped_delete_wait_rc=$?
control_worker status project-scoped-delete > "$TMP/project-scoped-delete.status"
project_scoped_delete_sha="$(status_sha "$TMP/project-scoped-delete.status")"
project_feedback project-scoped-delete | control_worker continue project-scoped-delete \
    --approve-state-sha "$project_scoped_delete_sha" \
    > "$TMP/project-scoped-delete.continue" 2> "$TMP/project-scoped-delete.continue.err"
project_scoped_delete_continue_rc=$?
control_worker result project-scoped-delete > "$TMP/project-scoped-delete.result"
project_scoped_delete_result_rc=$?
project_scoped_delete_copy="$(cd "$TMP" && pwd -P)/project-scoped-delete-copy"
control_worker verification-copy project-scoped-delete \
    --destination "$project_scoped_delete_copy" \
    > "$TMP/project-scoped-delete.copy" 2> "$TMP/project-scoped-delete.copy.err"
project_scoped_delete_copy_rc=$?
project_verified_feedback project-scoped-delete | control_worker finalize project-scoped-delete \
    --approve-state-sha "$project_scoped_delete_sha" --assurance verified \
    > "$TMP/project-scoped-delete.finalize" 2> "$TMP/project-scoped-delete.finalize.err"
project_scoped_delete_finalize_rc=$?
project_scoped_delete_calls="$(wc -l < "$TMP/project-scoped-delete.worker-calls" | tr -d ' ')"
if [[ "$project_scoped_delete_start_rc" == 0 && "$project_scoped_delete_wait_rc" == 0 \
        && "$project_scoped_delete_continue_rc" == 64 \
        && "$project_scoped_delete_result_rc" == 0 \
        && "$project_scoped_delete_copy_rc" == 0 \
        && "$project_scoped_delete_finalize_rc" == 0 \
        && "$project_scoped_delete_calls" == 1 \
        && ! -e "$TMP/project-worktree/scoped-delete.txt" \
        && ! -e "$project_scoped_delete_copy/scoped-delete.txt" \
        && ! -e "$project_scoped_delete_copy/.git" ]] \
        && python3 -B - "$TMP/project-scoped-delete.status" \
            "$TMP/project-scoped-delete.result" "$TMP/project-scoped-delete.finalize" \
            "$TMP/project-scoped-delete.continue.err" <<'PY'
import json
import sys

status = json.load(open(sys.argv[1], encoding="utf-8"))
result = json.load(open(sys.argv[2], encoding="utf-8"))
finalized = json.load(open(sys.argv[3], encoding="utf-8"))
actions = [item["action"] for item in status["available_actions"]]
assert status["status"] == "succeeded" and status["result_available"] is True
assert status["continue_available"] is False and "continue" not in actions
assert "result" in actions and "finalize" in actions
assert result["files_changed"] == [{"path": "scoped-delete.txt", "change": "deleted"}]
assert finalized["driver_disposition"] == "verified"
assert "read file does not exist in worktree: scoped-delete.txt" in open(
    sys.argv[4], encoding="utf-8"
).read()
PY
then
    ok "scoped deletion remains result/finalize-bound and requires fresh approval before continuation"
else
    bad "scoped terminal deletion or continuation approval boundary"
fi
