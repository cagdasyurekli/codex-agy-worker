---
name: agy-worker
description: Use when Codex should delegate repository exploration or implementation to Google Antigravity CLI (agy), then review, verify, repair, and deliver the result.
license: MIT
compatibility: Requires OpenAI Codex CLI, Bash, Python 3, git, and agy with provider network access. Claude and Claude Code hosts are not supported.
metadata:
  author: cagdasyurekli
  version: "0.19.0"
---

# Delegate repository work and verify the result

Use this skill when `agy` can usefully explore a repository, implement bounded work,
or help with broader project changes. Codex retains responsibility for scope, diff
review, driver-owned checks, repair decisions, and the final assurance label.

Resolve the installed package instead of guessing a checkout path:

```bash
PIPELINE="$(bash "$SKILL_ROOT/scripts/resolve-pipeline.sh")" || exit $?
```

`"$PIPELINE/doctor.sh" --repo /absolute/path/to/target` can check offline
prerequisites before provider use. `ready` does not prove authentication, provider
availability, task quality, or future job success. For package orientation, read the
[Package README](README.md).

## Authorize provider work

Before a provider launch, obtain explicit human approval for the exact provider-readable
content, transmission and isolation mode, task, caller-selected model, and budget.
One concrete approval package may cover foreseeable provider work and bounded repair;
reuse it while those facts remain unchanged. Approval is a human decision. Preview,
transmission, state, candidate, and dispatch SHA values are mechanical bindings to
that decision; refreshing a still-applicable binding is not another approval request.
Ask again only when authority, content exposure, destination, or budget materially
changes. Ordinary jobs require neither hand-authored JSON nor a Goal.

Prefer `--provider-scope FILE --approve-transmission-sha SHA256` for bounded jobs. It binds exact reviewed read entries, their selected-content digest, and a write subset, then stages only selected entries in a fresh owner-private mode-`0700` Gitless provider cwd.
Whole-worktree dispatch remains an explicit exception. Treat the entire disposable worktree passed as `--workdir` as worker-readable and potentially transmissible to Google/Gemini, regardless of requested edit paths; `--add-dir`, prompt denylist instructions, `qa-gate --only`, and `--allow` do not narrow that read boundary.
Neither `workflow.sh run` nor the advanced `agy-worker.sh` initial dispatch has an implicit transmission mode: launch requires either `--approve-whole-worktree LAUNCH_APPROVAL_SHA256` or the scoped pair above. The deprecated facade-only `--approve-preview-sha` spelling cannot launch by itself and remains temporarily available only with `--legacy-preview-approval`.
New jobs default to `--provider-isolation session`, which uses the existing AGY session without AGY sandbox or native host containment. AGY has normal user filesystem/network authority; selected-file staging and reconciliation are not host isolation. Include this execution mode in the initial approval alongside task/content, then reuse that approval while its scope remains unchanged. Explicit `--provider-isolation native` retains supported macOS scoped containment with private HOME/TMP and reviewed network/Keychain access; it never falls back to session mode. The native `/usr/bin/security` exception allows broader same-user Keychain operations, and its listener rule permits wildcard binds. Read [Security and compatibility](references/SECURITY_AND_COMPATIBILITY.md) for those limits. Preserve the job's selected mode across continuation and repair.
Provider-scope approval grants neither provider execution, Git action, driver acceptance, nor publication.
Before each launch, ensure secrets, credentials, private keys, user-denied paths, and unrelated private files are absent from every entry approved for provider transmission; telling the worker not to read an approved entry is not a control.

Keep raw worker logs and local controller state outside the worktree and out of prompts.
Installation does not authorize provider transmission, Git actions, publication, or
acceptance.

For the required user-facing provider-launch notice, including defaults, preflight
failures, resumes, and continuation, read [Project lifecycle and verification](references/PROJECT_LIFECYCLE_AND_VERIFICATION.md#approval-bindings-and-launch-notices).
Direct model and effort selection remain caller-owned; recommendations are advisory.

## Choose and run the workflow

| User intent | Workflow | Default cycle budget | Codex responsibility |
|---|---|---:|---|
| Explore, understand, review, or plan | `explore` | 2 | Spot-check material claims and state coverage limits. |
| Implement a feature, refactor, tests, or bounded repair | `task` | 2 | Inspect the diff and run relevant project checks. |
| Build a project or perform broad audit-and-fix work | `project` | 5 | Review repo-wide changes and run applicable build, test, and lint checks. |

`explore` and `task` accept `1..2` cycles; `project` accepts `1..5`. Personas are
optional prompt specializations, not capability, approval, routing, verification, or
quality gates. The raw `--boost` profile is an advanced, separately acknowledged
one-cycle task path; read [Security and compatibility](references/SECURITY_AND_COMPATIBILITY.md#boost-authority-boundary)
before using it.

For material UX, lifecycle, trust-boundary, security, data-semantics, or other domain
plans, use the co-planning policy in [Project lifecycle and verification](references/PROJECT_LIFECYCLE_AND_VERIFICATION.md#material-planning-governance).
Purely mechanical changes are exempt.

Explicit delegation-first requires running the `delegation-policy.sh` evaluator before substantive repository work.
The controller records are local: the runtime cannot infer prior work or approval and
must never silently authorize direct-Codex fallback after a missing approval, hard
stop, preflight failure, provider failure, or exhausted budget. Direct-Codex and
second-eye work remain explicit policy choices.

Prefer `workflow.sh` for `run --preview`, approved `run`, read-only `status`, and
`verify-finalize`. Review the content-free preview, then run with its exact approved
whole-worktree or scoped binding. The facade does not choose a model, assurance label,
repair, retry, Git action, or external write. Read the lifecycle guide for facade
examples, low-level recovery, Verification v2, and required notices.

After a candidate arrives, inspect the actual Git diff; select and run checks yourself
in an isolated verification copy, never an envelope's `commands_run` or `tests_run`;
bind only sanitized driver findings to the current candidate; then finalize honestly or
request a bounded same-conversation repair. A scoped candidate can receive another
provider turn only when initial dispatch included
`--allow-scoped-repair`, which binds the same approved scope, selected model,
conversation, and budgets. Preserve useful work when a check fails or the cycle budget
ends; `restart` is an explicit user decision. See [Project lifecycle and verification](references/PROJECT_LIFECYCLE_AND_VERIFICATION.md).

## Hard stops and delivery

Do not dispatch or continue without exact provider approval; when approved
provider-readable content contains secrets, denied paths, or unrelated private files;
when writes can escape the disposable worktree, enter `.git`, or traverse a symlink
boundary; with dangerous permission or approval-bypass flags; or when a Git action,
publication, installation, account action, or other external write lacks its own
authorization.

Unknown architecture, an unknown first test command, lack of a persona, or a failed
first check are not hard stops. Discover what is needed, preserve the candidate, and
report evidence limits. Before changing agy-facing flags or claims, run
`"$PIPELINE/ground-truth.sh"` and inspect its current `agy --help`; consume
`result.structured_output` when ordinary agy output is empty. Its default phase is
version/help only; `--account` is a separate explicit action.

Before delivery, review the exact candidate bytes and run relevant driver-owned checks.
Report only what those checks establish: `verified`, `partially_verified`, `rejected`,
or `blocked`. Offline checks do not prove provider success, completeness, release state,
security, or general correctness. Use [Troubleshooting](references/TROUBLESHOOTING.md)
for actionable failures.
