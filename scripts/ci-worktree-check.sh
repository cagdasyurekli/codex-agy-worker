#!/usr/bin/env bash
# Check whitespace in tracked changes and non-ignored untracked candidate files.
set -eu

[[ "$#" == 0 ]] || {
    printf '%s\n' 'ci worktree check: rejected arguments' >&2
    exit 2
}

paths_dir="$(mktemp -d -t agyworker-ci-worktree.XXXXXX)" || exit 1
cleanup() {
    rm -rf -- "$paths_dir"
}
trap cleanup EXIT HUP INT TERM

git diff --check
git ls-files --others --exclude-standard -z -- > "$paths_dir/untracked-paths"

while IFS= read -r -d '' path; do
    if git diff --no-index --check -- /dev/null "$path"; then
        continue
    else
        status=$?
    fi
    # --no-index returns 1 for an ordinary non-empty clean diff.  Any other
    # non-zero status is a whitespace or operational failure.
    [[ "$status" == 1 ]] || exit "$status"
done < "$paths_dir/untracked-paths"
