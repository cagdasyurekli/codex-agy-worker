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
