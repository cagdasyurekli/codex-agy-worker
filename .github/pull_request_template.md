## What changed

<!-- Describe the bounded problem and solution. -->

## Trust boundary and scope

- Affected paths:
- Trust boundary affected:
- User-visible claims changed:
- Explicitly out of scope:

## Verification

<!-- Include exact summaries; mark unrun checks and explain why. -->

- [ ] Owning focused checks were used during iteration; commands and summaries are listed below.
- [ ] Stable candidate: `./scripts/ci-offline.sh` passed, including syntax/compile and tracked plus non-ignored untracked whitespace checks.
- [ ] Human diff review completed.

Focused checks and exact summaries:

## Safety and release

- [ ] No worker-reported command or test was treated as evidence.
- [ ] No permission, authentication, privacy, or path-policy boundary was weakened.
- [ ] No commit, push, merge, release, issue submission, or external setting change is
      implied by this pull request without separate authorization.
