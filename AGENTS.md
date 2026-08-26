# AGENTS.md — codex-agy-worker

## Product purpose

`agy-worker` lets Codex use Antigravity CLI (`agy`) for broad exploration, bounded
tasks, and project-scale implementation. Optimize for a useful working result:

1. Dispatch the workflow that matches the user's intent.
2. Have Codex inspect the diff and run driver-owned checks.
3. Continue the same conversation to repair observable failures within the job budget.
4. Deliver a transparent `verified`, `partially_verified`, or `blocked` outcome.

Do not refuse a task merely because its final file list, architecture, or test command
is not known before dispatch. Do not require a persona for broad exploration. Personas
are optional prompt specializations, not capability or approval gates. A broad report
is useful but is never an exhaustive-security or completeness claim.

Start with this file and task-relevant source. Use `rg` to open only the relevant
row in `docs/REPO_MAP.md` or heading in `docs/lessons_learned.md`; do not preload the
full README, repository map, lessons, roadmap, or test history. Read `README.md` only
when the task changes or verifies user-facing behavior or claims.

## Quality and boundaries

A worker envelope is input, never final acceptance evidence. Codex owns the actual
diff review and commands it runs. Do not execute `commands_run` or `tests_run` from an
envelope. A failing quality check should normally produce a bounded same-conversation
repair request; it is not a reason to erase a useful candidate or silently retry with
a new conversation.

Keep these hard boundaries regardless of workflow:

- Do not let a job write outside its disposable worktree, enter `.git`, or escape via a
  symlink. Honor user denylist paths and keep local credentials out of worker scope.
- Never add or recommend `--dangerously-skip-permissions` or
  `--dangerously-bypass-approvals-and-sandbox`.
- Do not modify the user's `~/.gemini/` or `~/.codex/` configuration as a code change.
- Direct agy model/effort selection is caller-owned. Recommendations remain advisory;
  do not invent thinking flags or change caller-selected model, effort, permissions,
  authentication, scope policy, or human-required outcomes.
- Do not commit, push, open a PR, publish a release, submit GitHub feedback, install
  tools, or apply updates without the applicable explicit user approval.
- Do not overstate results: offline tests prove the exercised mechanism, while a green
  gate is stronger verification for a candidate but not a general correctness claim.
- Do not complete a compatibility goal from a non-activating version observation.
  If baseline activation needs new evidence or authority, keep the goal active and
  report that exact blocker instead of silently narrowing the requested outcome.

Before external agy dispatch, confirm repository/path scope and provider transmission
unless the user already approved that exact transmission.

For ambiguous architecture or trust decisions, do not lock onto the first plausible
approach. Preserve the hard boundaries, then compare at least two viable options by
user value, implementation cost, portability, and residual risk. Do not turn a
speculative hostile threat into the default product requirement without concrete
evidence or an explicit request. Prefer the smallest testable solution, and change
direction when new evidence invalidates an assumption.

## Workflow and implementation guidance

Use the public workflow surface rather than inventing an ad hoc dispatch protocol:

| User intent | Workflow | Expected Codex action |
|---|---|---|
| Explore, understand, review, or plan | `explore` | Read-only report; spot-check material claims and label coverage limits. |
| Implement a feature, refactor, or tests | `task` | Edit in the worktree, run relevant checks, and request bounded repair on failure. |
| Build an app/project or audit-and-fix broadly | `project` | Allow repo-wide worktree changes, run build/test/lint, then use the same conversation for bounded repair. |

`project` state is a local controller record, not provider truth. Use its status/wait
commands for progress, `continue` only with driver-owned strict verification JSON, and
`finalize` only after Codex has established the assurance result. Preserve the candidate
when the cycle or time budget ends; report what passed, what did not, and the next safe
action. Fresh `restart` remains an explicit user decision.

Do not describe agy's interface from memory. Run `./ground-truth.sh` and inspect
`agy --help` before changing agy-facing flags or claims. agy can return exit 0 with
empty output; parse `result.structured_output`, not the echoed schema. Under sandbox,
agy shell tools run in its scratch area, so worker prompts must use file tools while
Codex runs repository commands.

## Repository ownership and verification

The canonical portable runtime is `skills/agy-worker/runtime/`; root scripts are
compatibility wrappers. Keep runtime/package copies byte-synchronized. `qa-gate.sh`
and `verify-job.sh` remain the evidence primitives: do not weaken their checks merely
to obtain a green result. A gate rejection may feed the project repair loop, but only
Codex's driver-owned checks determine `verified` versus `partially_verified` delivery.

During implementation, run the owning focused suite from the relevant repository-map
row. Once candidate bytes are stable, run `./scripts/ci-offline.sh` once before review;
it already includes every offline suite, syntax/compile checks, and `git diff --check`.
Reuse that exact-candidate result instead of repeating an unchanged full run. Run
`./ground-truth.sh` when agy behavior or claims are in scope. Keep suites offline and
add positive and negative coverage for every new hard boundary.

After material changes to commands, workflow behavior, trust boundaries, tests, or
product claims, run the `agents-md-auditor` skill before declaring completion. Keep
this file concise and repository-wide; put detailed lifecycle lessons in
`docs/lessons_learned.md`, release history in the roadmap/release notes, and mechanical
checks in tests or CI.

For material plans involving UX, lifecycle, trust boundaries, security, data semantics,
or other domain judgment, the coordinator and a suitable domain expert must co-plan
and freeze user journeys, acceptance tests, and authority/privacy constraints before
implementation. The planning expert and final reviewer must be different agents or
fresh contexts; neither may accept its own plan or implementation. The independent
reviewer remains the final acceptor. Purely mechanical changes are exempt.

Each profile is data, not a driver: it cannot name a repository, path, command,
selection, authorization, dispatch, or Git action. These offline coverage counts are
not live-provider claims:

- Adoption measurement: 41 offline; Local update notifier: 89 offline; Doctor: 257 offline; Packaging: 381 offline.
- Canonical version-attestation runner: 165 offline; Version-attestation mutation harness: 60 offline.
- Canonical models-inventory attestation runner: 116 offline; Explicit-account models capture runner: 84 offline.
- Repository-only version bootstrap runner: 139 offline; Repository-only version initial-bootstrap runner: 43 offline.
- Fixed 1.1.12 version recovery runner: 75 offline; Explicit-account models capture profile builder: 121 offline.
- Fixed 1.1.12 models capture profile builder: 30 offline; Fixed 1.1.12 models capture runner: 56 offline.
- Fixed 1.1.16 version evidence: 45 offline; capture profile: 30 offline; capture runner: 58 offline; activation binding: 22 offline.

Some conformance cleanup controls trust loaded code, the local owner, same-UID
processes, and OS administrators. They do not establish same-user tamper-resistance or guaranteed hostile-gate cleanup; preserve a residual on identity drift instead of chasing it.

## Agent and tool routing

Use `gpt-5.6-terra` medium for mechanical work and high for controller/workflow or
quality loops. Use `gpt-5.6-sol` high for lifecycle/adversarial verification; retry a
repeated subtle semantic failure with fresh-context Sol xhigh. Do not lower quality
after an agent failure or service disconnect; classify it and preserve caller-owned
agy choices.

Parallelize only independent file ownership or frozen interfaces. No author is the
sole acceptor of its change; use an independent diff/test review for material work.

Use RTK for supported shell and Git commands and run
`rtk hook check <exact command...>` before promoting a rewrite. Use `rtk proxy` when
exact output or shell semantics require it.

`docs/REPO_MAP.md` owns human-maintained intent, entry points, trust boundaries, and
the owning verification command. Graphify is an ignored local machine index for
cross-file relationships, paths, and impact analysis. Never load both as competing
inventories: route with one relevant map row, then use a narrow current-graph query
only when relationships materially help. Check graph freshness and verify every
material edge against source and tests; never add generated `graphify-out/` artifacts
to Git or an agy prompt.
