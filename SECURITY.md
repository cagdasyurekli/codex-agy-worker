# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's
[private vulnerability reporting form](https://github.com/cagdasyurekli/codex-agy-worker/security/advisories/new)
so the report and follow-up remain private.

Include the affected commit or release, impact, reproduction steps, and the smallest
safe proof needed to understand the issue. Remove credentials, private repository
content, prompts, raw logs, and unrelated personal data. If the private form is not
available, open a minimal public issue asking the maintainer to enable a private
contact route; do not disclose vulnerability details there.

## Supported versions

Security fixes target the latest release and the current `main` branch. Older releases
may not receive fixes. A maintainer will validate the report, coordinate remediation,
and decide disclosure timing based on impact and available evidence; no response or
resolution deadline is guaranteed.

The evidence gate reduces specific acceptance risks but does not prove that generated
or accepted code is secure. Operators remain responsible for review, testing, access
control, and incident response in each target repository.

## Execution boundaries

`agy-worker` is a Codex Agent Skill, not a sandbox or an autonomous acceptance
service. It delegates approved repository work to the external `agy` provider while
Codex retains diff review and driver-owned verification. Default dispatch exposes the
entire disposable worktree as potentially provider-readable; `--add-dir`, prompt
instructions, and candidate-path gates do not narrow it. Optional `--provider-scope`
binds exact reviewed read/write entries and a selected-content digest, then stages only selected
entries in a fresh owner-private mode-`0700` Gitless cwd. The controller still locally
enumerates and validates worktree/scope paths, and scoped staging is not filesystem,
network, `PATH`, `HOME`, or same-UID isolation. Approve the exact transmission mode and
digest, and exclude credentials, private keys, denied paths, unrelated private files,
raw logs, and controller state from all approved content. Installation alone is not
provider-transmission consent; scope approval grants no execution, Git, acceptance, or
publication authority.

The disposable worktree limits ordinary workflow scope but is not a security sandbox.
Operators remain responsible for credentials, network access, review, testing, and
access control in each target repository. The skill supports the OpenAI Codex CLI; it
does not support Claude or Claude Code hosts.

Provider children, dispatch-time provider-interface probes, and driver verifiers
receive a closed baseline environment. Verification requires at least one driver-owned
verifier; repeatable `--verify-argv` is the normal no-shell path, while acknowledged
explicit and legacy shell modes remain advanced compatibility paths. Additional
variables require exact-name opt-in through `--provider-env` or ordinary verifier
`--verify-env`; credential-like verifier names instead require
`--verify-credential-env` plus the credential-access acknowledgement. Verifier-only values reach
the trusted gate process through a private pipe before the gate builds the verifier
child's environment. Unsafe interpreter, loader,
schema-selector, and Git-control hooks are rejected, and secret values are not written
to command or receipt artifacts. Driver ownership
of a verification command does not make candidate code imported by that command
trusted. Environment filtering does not isolate `HOME`, `PATH`, filesystem, network,
or same-user processes, so human diff review remains required after a green gate.

Transmission preview uses fixed, bounded local `/usr/bin/git worktree list` plumbing
to prove that its target is a registered branch-backed linked worktree. It launches no
`agy`, provider, credential probe, or network process, and its Git metadata check does
not turn the path-only preview digest into approval or launch authority.
