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
Codex retains diff review and driver-owned verification. Before dispatch, the operator
must approve the repository and path scope that may be sent to the provider; do not
include credentials, private keys, unrelated local files, raw worker logs, or local
controller state in a prompt. Installation alone is not provider-transmission consent.

The disposable worktree limits ordinary workflow scope but is not a security sandbox.
Operators remain responsible for credentials, network access, review, testing, and
access control in each target repository. The skill supports the OpenAI Codex CLI; it
does not support Claude or Claude Code hosts.

Provider children, dispatch-time provider-interface probes, and driver verifiers
receive a closed baseline environment. Additional variables require exact-name opt-in
through `--provider-env` or `verify-job.sh --verify-env`; verifier-only values reach
only the verifier child through a private pipe. Unsafe interpreter, loader,
schema-selector, and Git-control hooks are rejected, and secret values are not written
to command or receipt artifacts. Driver ownership
of a verification command does not make candidate code imported by that command
trusted. Environment filtering does not isolate `HOME`, `PATH`, filesystem, network,
or same-user processes, so human diff review remains required after a green gate.
