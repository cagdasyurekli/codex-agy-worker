#!/usr/bin/env bash
# Explicit updater and compatibility checker for codex-agy-worker.
# No background work, no automatic pull, and no update from a dirty checkout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REMOTE="origin"
EXPECTED_HTTPS="https://github.com/cagdasyurekli/codex-agy-worker.git"
EXPECTED_HTTPS_NO_SUFFIX="https://github.com/cagdasyurekli/codex-agy-worker"
EXPECTED_SSH="git@github.com:cagdasyurekli/codex-agy-worker.git"
EXPECTED_SSH_URL="ssh://git@github.com/cagdasyurekli/codex-agy-worker.git"
OFFICIAL_AGY_UPSTREAM="https://github.com/google-antigravity/antigravity-cli.git"
OFFICIAL_CODEX_UPSTREAM="https://github.com/openai/codex.git"
COMPATIBILITY_REVIEW_DAYS=30

usage() {
    cat >&2 <<'EOF'
usage: update.sh check [--watch]
       update.sh apply [vMAJOR.MINOR.PATCH]

check: read-only remote release and agy/Codex compatibility check.
       Pass --watch for official-source evidence only (no installed tools needed).
apply: explicit fast-forward update from a verified release tag. Refuses a dirty
       checkout, validates the candidate in a disposable worktree, then reinstalls
       the Codex skill. It never runs during an agy worker job.
EOF
    exit 64
}

[[ $# -ge 1 ]] || usage
command_name="$1"; shift

cd "$SCRIPT_DIR"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "update: pipeline directory is not a Git worktree" >&2; exit 64;
}

remote="$DEFAULT_REMOTE"
origin_available=1
remote_url="$(git config --get "remote.$remote.url" 2>/dev/null)" || {
    echo "update: origin remote is unavailable" >&2
    origin_available=0
    remote_url=""
}
if [[ -n "$remote_url" ]]; then
    case "$remote_url" in
        "$EXPECTED_HTTPS"|"$EXPECTED_HTTPS_NO_SUFFIX"|"$EXPECTED_SSH"|"$EXPECTED_SSH_URL") ;;
        *)
            # Do not echo an unexpected URL: Git remotes sometimes contain credentials.
            echo "update: refusing unexpected origin URL" >&2
            echo "update: expected the official cagdasyurekli/codex-agy-worker repository" >&2
            origin_available=0 ;;
    esac
fi
if [[ "$command_name" == "apply" && "$origin_available" == 0 ]]; then
    exit 2
fi

latest_release() {
    local refs
    refs="$(git ls-remote --tags --refs "$remote" 'refs/tags/v*' 2>/dev/null)" || {
        echo "update: could not query release tags from official origin" >&2
        return 2
    }
    printf '%s\n' "$refs" | python3 -c '
import re, sys
versions = []
for line in sys.stdin:
    fields = line.split()
    if len(fields) != 2:
        continue
    tag = fields[1][len("refs/tags/"):]
    match = re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag)
    if match:
        versions.append((tuple(map(int, match.groups())), tag))
if versions:
    print(max(versions)[1])
'
}

remote_release_commit() {
    local tag="$1" refs
    refs="$(git ls-remote --tags "$remote" "refs/tags/$tag" "refs/tags/$tag^{}" 2>/dev/null)" || {
        echo "update: could not resolve release tag $tag" >&2; return 2;
    }
    printf '%s\n' "$refs" | python3 -c '
import sys
rows = [line.split() for line in sys.stdin if line.split()]
peeled = [oid for oid, ref in rows if ref.endswith("^{}")]
direct = [oid for oid, ref in rows if not ref.endswith("^{}")]
if peeled:
    print(peeled[0])
elif direct:
    print(direct[0])
else:
    raise SystemExit(1)
' || { echo "update: release tag not found: $tag" >&2; return 2; }
}

merge_status() {
    local current="$1" candidate="$2"
    if (( current == 2 || candidate == 2 )); then
        echo 2
    elif (( current == 3 || candidate == 3 )); then
        echo 3
    else
        echo 0
    fi
}

compat_metadata() {
    python3 "$SCRIPT_DIR/scripts/compatibility.py" metadata --kind "$1" --file "$2"
}

agy_distribution_manifest_check() {
    local output="" status=0
    output="$(python3 "$SCRIPT_DIR/scripts/official_distribution.py" 2>/dev/null)" || status=$?
    case "$status" in
        0)
            if [[ "$output" == "  distribution manifest: unchanged ("*')' ]]; then
                printf '%s\n' "$output"
                return 0
            fi
            ;;
        3)
            if [[ "$output" == "  distribution manifest: drift-review ("*')' ]]; then
                printf '%s\n' "$output"
                return 3
            fi
            ;;
        2)
            if [[ "$output" == "  distribution manifest: evidence-unavailable ("*')' ]]; then
                printf '%s\n' "$output"
                return 2
            fi
            ;;
    esac
    echo "  distribution manifest: evidence-unavailable (invalid helper result)"
    return 2
}

agy_model_matrix_check() {
    local output="" status=0
    output="$(python3 "$SCRIPT_DIR/scripts/compatibility.py" validate-matrix \
        --matrix "$SCRIPT_DIR/compat/agy-model-effort-matrix.json" \
        --schema "$SCRIPT_DIR/compat/model-effort-matrix.schema.json" \
        --verified-version-file "$SCRIPT_DIR/compat/agy-verified-version.txt" \
        --reviewed-revision-file "$SCRIPT_DIR/compat/agy-upstream-head.txt" \
        2>/dev/null)" || status=$?
    case "$status:$output" in
        "0:matrix: unchanged - active and version/source bound")
            echo "  model/effort matrix: unchanged (active and version/source bound)"
            return 0
            ;;
        "3:matrix: drift-or-review - resolution is disabled pending official source evidence"|\
        "3:matrix: drift-or-review - matrix agy version differs from the verified baseline"|\
        "3:matrix: drift-or-review - matrix source revision differs from the reviewed baseline")
            echo "  model/effort matrix: drift-review"
            return 3
            ;;
        *)
            echo "  model/effort matrix: evidence-unavailable"
            return 2
            ;;
    esac
}

tool_compatibility_check() {
    local tool="$1" mode="$2" upstream verified_file reviewed_file revision_file
    local verified_version="" review_date="" reviewed_head="" installed_output="" installed_version=""
    local latest_stable="" remote_head="" rc=0 step_rc=0
    case "$tool" in
        agy)
            upstream="$OFFICIAL_AGY_UPSTREAM"
            verified_file="$SCRIPT_DIR/compat/agy-verified-version.txt"
            reviewed_file="$SCRIPT_DIR/compat/agy-last-reviewed.txt"
            revision_file="$SCRIPT_DIR/compat/agy-upstream-head.txt"
            ;;
        codex)
            upstream="$OFFICIAL_CODEX_UPSTREAM"
            verified_file="$SCRIPT_DIR/compat/codex-verified-version.txt"
            reviewed_file="$SCRIPT_DIR/compat/codex-last-reviewed.txt"
            revision_file="$SCRIPT_DIR/compat/codex-upstream-head.txt"
            ;;
        *) return 2 ;;
    esac

    echo "$tool compatibility:"
    verified_version="$(compat_metadata version "$verified_file" 2>/dev/null)" || {
        echo "  baseline: evidence-unavailable (malformed verified-version metadata)"
        rc="$(merge_status "$rc" 2)"
    }
    review_date="$(compat_metadata date "$reviewed_file" 2>/dev/null)" || {
        echo "  review: evidence-unavailable (malformed last-reviewed metadata)"
        rc="$(merge_status "$rc" 2)"
    }
    reviewed_head="$(compat_metadata revision "$revision_file" 2>/dev/null)" || {
        echo "  source: evidence-unavailable (malformed reviewed revision metadata)"
        rc="$(merge_status "$rc" 2)"
    }
    if [[ -n "$verified_version" ]]; then
        echo "  verified baseline: $verified_version"
    fi

    if [[ "$mode" == "local" && "$origin_available" == 0 ]]; then
        echo "tool update: evidence-unavailable (official project origin is unavailable)"
        aggregate="$(merge_status "$aggregate" 2)"
    elif [[ "$mode" == "local" ]]; then
        if ! command -v "$tool" >/dev/null 2>&1; then
            echo "  installed: drift-review ($tool is not on PATH)"
            rc="$(merge_status "$rc" 3)"
        else
            step_rc=0
            installed_output="$("$tool" --version 2>/dev/null)" || step_rc=$?
            if (( step_rc != 0 )); then
                echo "  installed: evidence-unavailable (--version failed)"
                rc="$(merge_status "$rc" 2)"
            else
                installed_version="$(printf '%s\n' "$installed_output" | python3 "$SCRIPT_DIR/scripts/compatibility.py" version-output --tool "$tool" 2>/dev/null)" || {
                    echo "  installed: evidence-unavailable (version output lacks documented semantic content)"
                    rc="$(merge_status "$rc" 2)"
                }
                if [[ -n "$installed_version" && -n "$verified_version" ]]; then
                    if [[ "$installed_version" == "$verified_version" ]]; then
                        echo "  installed: unchanged ($installed_version)"
                    else
                        echo "  installed: drift-review ($installed_version; verified $verified_version)"
                        rc="$(merge_status "$rc" 3)"
                    fi
                fi
            fi
        fi
    else
        echo "  installed: not required in watch mode"
    fi

    if [[ "$tool" == "agy" ]]; then
        step_rc=0
        agy_model_matrix_check || step_rc=$?
        rc="$(merge_status "$rc" "$step_rc")"

        step_rc=0
        agy_distribution_manifest_check || step_rc=$?
        rc="$(merge_status "$rc" "$step_rc")"
    fi

    step_rc=0
    latest_stable="$(git ls-remote --tags --refs "$upstream" 2>/dev/null \
        | python3 "$SCRIPT_DIR/scripts/compatibility.py" latest-release --tool "$tool" 2>/dev/null)" || step_rc=$?
    if (( step_rc != 0 )) || [[ -z "$latest_stable" ]]; then
        echo "  stable release: evidence-unavailable"
        rc="$(merge_status "$rc" 2)"
    elif [[ -n "$verified_version" && "$latest_stable" == "$verified_version" ]]; then
        echo "  stable release: unchanged ($latest_stable)"
    else
        echo "  stable release: drift-review (official $latest_stable; verified ${verified_version:-invalid})"
        rc="$(merge_status "$rc" 3)"
    fi

    step_rc=0
    remote_head="$(git ls-remote "$upstream" HEAD 2>/dev/null \
        | python3 "$SCRIPT_DIR/scripts/compatibility.py" source-head 2>/dev/null)" || step_rc=$?
    if (( step_rc != 0 )) || [[ -z "$remote_head" ]]; then
        echo "  source revision: evidence-unavailable"
        rc="$(merge_status "$rc" 2)"
    elif [[ -n "$reviewed_head" && "$remote_head" == "$reviewed_head" ]]; then
        echo "  source revision: unchanged ($reviewed_head)"
    else
        echo "  source revision: drift-review"
        rc="$(merge_status "$rc" 3)"
    fi

    if [[ -n "$review_date" ]]; then
        step_rc=0
        python3 "$SCRIPT_DIR/scripts/compatibility.py" review-state \
            --reviewed "$review_date" --days "$COMPATIBILITY_REVIEW_DAYS" >/dev/null 2>&1 || step_rc=$?
        case "$step_rc" in
            0) echo "  documentation review: unchanged ($review_date)" ;;
            3)
                echo "  documentation review: drift-review (last reviewed $review_date)"
                rc="$(merge_status "$rc" 3)" ;;
            *)
                echo "  documentation review: evidence-unavailable"
                rc="$(merge_status "$rc" 2)" ;;
        esac
    fi
    return "$rc"
}

compatibility_check() {
    local mode="$1" aggregate=0 tool_rc=0
    tool_compatibility_check agy "$mode" || tool_rc=$?
    aggregate="$(merge_status "$aggregate" "$tool_rc")"
    tool_rc=0
    tool_compatibility_check codex "$mode" || tool_rc=$?
    aggregate="$(merge_status "$aggregate" "$tool_rc")"
    echo "compatibility result: $([[ "$aggregate" == 0 ]] && echo unchanged || { [[ "$aggregate" == 3 ]] && echo drift-review || echo evidence-unavailable; })"
    return "$aggregate"
}

check_updates() {
    local mode=local latest="" current_tag current_commit latest_commit release_rc=0 compat_rc=0 aggregate=0
    if [[ $# -eq 1 && "$1" == "--watch" ]]; then
        mode=watch
    elif [[ $# -ne 0 ]]; then
        usage
    fi
    if [[ "$mode" == "local" ]]; then
        latest="$(latest_release)" || release_rc=$?
        if (( release_rc != 0 )); then
            echo "tool update: evidence-unavailable (official release query failed)"
            aggregate="$(merge_status "$aggregate" 2)"
        else
            current_commit="$(git rev-parse HEAD)"
            current_tag="$(git describe --tags --exact-match --match 'v[0-9]*' HEAD 2>/dev/null || true)"
            if [[ -z "$latest" ]]; then
                echo "tool update: no stable release tags are published yet"
            else
                latest_commit="$(remote_release_commit "$latest")" || release_rc=$?
                if (( release_rc != 0 )); then
                    echo "tool update: evidence-unavailable (official release tag is inconclusive)"
                    aggregate="$(merge_status "$aggregate" 2)"
                elif [[ "$current_commit" == "$latest_commit" ]]; then
                    echo "tool update: up to date at $latest"
                elif [[ -n "$current_tag" ]]; then
                    echo "tool update: available $current_tag -> $latest"
                else
                    echo "tool update: current checkout is not release-tagged; latest is $latest"
                fi
            fi
        fi
    fi
    compatibility_check "$mode" || compat_rc=$?
    aggregate="$(merge_status "$aggregate" "$compat_rc")"
    if (( aggregate != 0 )); then
        echo "update: check is read-only; no files were changed" >&2
        exit "$aggregate"
    fi
}

apply_update() {
    [[ $# -le 1 ]] || usage
    local tag="${1:-}" remote_commit temp_ref candidate_commit candidate_wt="" current_branch
    if [[ -z "$tag" ]]; then
        tag="$(latest_release)" || exit $?
    fi
    [[ -n "$tag" ]] || { echo "update: no stable release tag is available" >&2; exit 2; }
    [[ "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || {
        echo "update: apply requires a stable vMAJOR.MINOR.PATCH tag" >&2; exit 64;
    }
    [[ -z "$(git status --porcelain --untracked-files=all)" ]] || {
        echo "update: refusing to update a dirty checkout" >&2; exit 2;
    }
    current_branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" || {
        echo "update: apply requires a checked-out branch, not detached HEAD" >&2; exit 2;
    }

    remote_commit="$(remote_release_commit "$tag")" || exit $?
    temp_ref="refs/agy-worker-update/candidate-$$"
    cleanup_update() {
        if [[ -n "$candidate_wt" && -d "$candidate_wt" ]]; then
            git worktree remove --force "$candidate_wt" >/dev/null 2>&1 || true
        fi
        git update-ref -d "$temp_ref" >/dev/null 2>&1 || true
    }
    trap cleanup_update EXIT INT TERM

    git fetch --quiet --no-tags "$remote" "refs/tags/$tag:$temp_ref" || {
        echo "update: failed to fetch $tag" >&2; exit 2;
    }
    candidate_commit="$(git rev-parse "$temp_ref^{commit}")" || {
        echo "update: fetched release does not resolve to a commit" >&2; exit 2;
    }
    [[ "$candidate_commit" == "$remote_commit" ]] || {
        echo "update: release verification failed; remote and fetched commits differ" >&2
        exit 2
    }
    git merge-base --is-ancestor HEAD "$candidate_commit" || {
        echo "update: release is not a fast-forward from $current_branch" >&2; exit 2;
    }

    protect_ignored_paths() {
        python3 - "$SCRIPT_DIR" "$candidate_commit" <<'PY'
import subprocess
import sys

repo, candidate = sys.argv[1:3]

def git_paths(*args):
    output = subprocess.run(
        ["git", "-C", repo, *args], check=True, stdout=subprocess.PIPE
    ).stdout
    return {part for part in output.split(b"\0") if part}

ignored = git_paths("ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--")
tracked = git_paths("ls-tree", "-r", "--name-only", "-z", candidate)
collisions = sorted(ignored & tracked)
if collisions:
    print("update: release would overwrite ignored local path(s):", file=sys.stderr)
    for path in collisions:
        display = path.decode("utf-8", "backslashreplace")
        print(f"    {display!r}", file=sys.stderr)
    raise SystemExit(1)
PY
    }

    protect_ignored_paths || {
        echo "update: preserve or remove those local files before retrying" >&2
        exit 2
    }

    candidate_wt="$(mktemp -d -t agy-worker-update.XXXXXX)"
    rmdir "$candidate_wt"
    git worktree add --quiet --detach "$candidate_wt" "$candidate_commit"
    echo "update: verified $tag -> $candidate_commit"
    echo "update: validating candidate in $candidate_wt"
    (
        cd "$candidate_wt"
        bash -n ./*.sh tests/*.sh skills/*/scripts/*.sh skills/*/runtime/*.sh || exit $?
        preflight_skills="$(mktemp -d -t agy-worker-skill-preflight.XXXXXX)"
        trap 'rm -rf -- "$preflight_skills"' EXIT
        CODEX_SKILLS_DIR="$preflight_skills" ./install.sh || exit $?
        for suite in tests/test-*.sh; do "$suite" || exit $?; done
        git diff --check || exit $?
    ) || { echo "update: candidate validation failed; checkout unchanged" >&2; exit 2; }
    git worktree remove "$candidate_wt"
    candidate_wt=""

    [[ -z "$(git status --porcelain --untracked-files=all)" ]] || {
        echo "update: checkout changed during validation; refusing apply" >&2; exit 2;
    }
    protect_ignored_paths || {
        echo "update: ignored local paths changed during validation; refusing apply" >&2
        exit 2
    }
    git update-ref "refs/tags/$tag" "$(git rev-parse "$temp_ref")"
    git update-ref -d "$temp_ref"
    trap - EXIT INT TERM
    git merge --ff-only "$candidate_commit"
    if ! "$SCRIPT_DIR/install.sh"; then
        echo "update: PARTIAL UPDATE - checkout is now at $candidate_commit, but Codex skill installation failed" >&2
        echo "update: recovery - fix the skill destination, then run: $SCRIPT_DIR/install.sh" >&2
        exit 4
    fi
    echo "update: applied $tag and reinstalled the Codex skill"
}

case "$command_name" in
    check) check_updates "$@" ;;
    apply) apply_update "$@" ;;
    *) usage ;;
esac
