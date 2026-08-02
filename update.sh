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

usage() {
    cat >&2 <<'EOF'
usage: update.sh check
       update.sh apply [vMAJOR.MINOR.PATCH]

check: read-only remote release and agy compatibility check.
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
remote_url="$(git config --get "remote.$remote.url" 2>/dev/null)" || {
    echo "update: origin remote is unavailable" >&2; exit 2;
}
case "$remote_url" in
    "$EXPECTED_HTTPS"|"$EXPECTED_HTTPS_NO_SUFFIX"|"$EXPECTED_SSH"|"$EXPECTED_SSH_URL") ;;
    *)
        # Do not echo an unexpected URL: Git remotes sometimes contain credentials.
        echo "update: refusing unexpected origin URL" >&2
        echo "update: expected the official cagdasyurekli/codex-agy-worker repository" >&2
        exit 2 ;;
esac

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

compatibility_check() {
    local attention=0 verified_version installed_version review_date due pinned_head remote_head
    verified_version="$(tr -d '[:space:]' < "$SCRIPT_DIR/compat/agy-verified-version.txt")"
    review_date="$(tr -d '[:space:]' < "$SCRIPT_DIR/compat/last-reviewed.txt")"
    if command -v agy >/dev/null 2>&1; then
        installed_version="$(agy --version 2>/dev/null | head -1 | tr -d '[:space:]')"
        if [[ "$installed_version" == "$verified_version" ]]; then
            echo "agy compatibility: installed $installed_version, verified"
        else
            echo "agy compatibility: REVIEW REQUIRED - installed $installed_version, verified $verified_version"
            attention=1
        fi
    else
        echo "agy compatibility: REVIEW REQUIRED - agy is not on PATH"
        attention=1
    fi

    due="$(python3 - "$review_date" <<'PY'
from datetime import date
import sys
reviewed = date.fromisoformat(sys.argv[1])
today = date.today()
if reviewed > today:
    raise SystemExit("compatibility review date is in the future")
print("yes" if (today - reviewed).days >= 30 else "no")
PY
)" || { echo "update: invalid compatibility review metadata" >&2; return 2; }
    if [[ "$due" == "yes" ]]; then
        echo "agy compatibility: REVIEW DUE - last official-doc review was $review_date"
        attention=1
    else
        echo "agy compatibility: official docs reviewed $review_date"
    fi

    pinned_head="$(tr -d '[:space:]' < "$SCRIPT_DIR/compat/agy-upstream-head.txt")"
    remote_head="$(git ls-remote "$OFFICIAL_AGY_UPSTREAM" HEAD 2>/dev/null | awk 'NR==1 {print $1}')" || true
    if [[ -z "$remote_head" ]]; then
        echo "agy compatibility: could not query official upstream HEAD" >&2
        attention=1
    elif [[ "$remote_head" != "$pinned_head" ]]; then
        echo "agy compatibility: REVIEW REQUIRED - official upstream changed"
        echo "  reviewed: $pinned_head"
        echo "  current:  $remote_head"
        attention=1
    else
        echo "agy compatibility: official upstream HEAD unchanged"
    fi
    echo "agy docs: https://antigravity.google/docs/cli-overview"
    echo "agy source: https://github.com/google-antigravity/antigravity-cli"
    return "$attention"
}

check_updates() {
    [[ $# -eq 0 ]] || usage
    local latest current_tag current_commit latest_commit compat_rc=0
    latest="$(latest_release)" || exit $?
    current_commit="$(git rev-parse HEAD)"
    current_tag="$(git describe --tags --exact-match --match 'v[0-9]*' HEAD 2>/dev/null || true)"
    if [[ -z "$latest" ]]; then
        echo "tool update: no stable release tags are published yet"
    else
        latest_commit="$(remote_release_commit "$latest")" || exit $?
        if [[ "$current_commit" == "$latest_commit" ]]; then
            echo "tool update: up to date at $latest"
        elif [[ -n "$current_tag" ]]; then
            echo "tool update: available $current_tag -> $latest"
        else
            echo "tool update: current checkout is not release-tagged; latest is $latest"
        fi
    fi
    compatibility_check || compat_rc=$?
    if (( compat_rc != 0 )); then
        echo "update: compatibility review is required; no files were changed" >&2
        exit 3
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
