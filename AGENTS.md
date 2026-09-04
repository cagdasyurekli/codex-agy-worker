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
  symlink. User denylist paths constrain requested writes. Gate `--only` constrains
  candidate changed paths after dispatch; `--allow` only exempts matching undeclared
  artifacts from rejection. None of these isolate provider reads.
- Prefer `--provider-scope` for bounded jobs. It binds exact reviewed read entries, a
  selected-content digest, and a
  write subset, then stages only those entries in a fresh owner-private mode-`0700`
  Gitless provider cwd. Whole-worktree dispatch remains an explicit exception and
  requires `--approve-whole-worktree` bound to the current path/kind manifest; without
  provider scope, treat every worktree entry as worker-readable and potentially
  transmissible to Google/Gemini, and remember that `--add-dir` does not narrow it.
  The controller still locally enumerates and validates
  worktree/scope paths, and the stage is not a filesystem, network, `PATH`, `HOME`, or
  same-UID sandbox. Keep secrets, denied paths, and unrelated private content outside
  every entry approved for either transmission mode.
- Provider, probe, and verifier children start with an operational allowlist. Do not
  pass `--provider-env` or `--verify-env` without approval for each variable name and
  its resulting provider/verifier exposure; values are not persisted, and filtering
  is not `HOME`, `PATH`, filesystem, network, or same-user isolation.
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

Before external agy dispatch, confirm approval for the exact mode and content. Default
mode requires whole-worktree approval. Scoped mode requires the exact reviewed policy
and `transmission_sha256`; that approval grants neither provider execution, Git action,
driver acceptance, nor publication.

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

Use `workflow.sh run`, `status`, and `verify-finalize` as the ordinary controller
facade. Treat direct dispatcher, gate, receipt, and lifecycle commands as advanced
recovery or compatibility surfaces; the facade delegates to them and does not own a
second lifecycle state machine or infer driver assurance.

`project` state is a local controller record, not provider truth. Use its status/wait
commands for progress, `continue` only with driver-owned strict verification JSON, and
`finalize` only after Codex has established the assurance result. Preserve the candidate
when the cycle or time budget ends; report what passed, what did not, and the next safe
action. Fresh `restart` remains an explicit user decision.
Because the current `continue` surface cannot collect a fresh transmission approval, a
scoped candidate whose approved content changed is result/finalize-only; never treat its
original transmission SHA as authority to send the changed bytes again.

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

After a green full run, classify later changes before rerunning it. Executable or
runtime bytes, trust boundaries, test runners or inventories, and upstream merges that
touch those surfaces require affected focused checks plus a new full run. A
documentation, public-claim, metadata, or existing-test-assertion correction that does
not change executable behavior or the canonical suite inventory requires its owning
focused check, diff hygiene, and incremental independent review; do not repeat the full
local suite solely to attach it to a new commit SHA. Any focused failure or uncertainty
about impact upgrades the change to a full run. Treat the required GitHub check as the
exact PR-head full gate. After integrating a pinned upstream base, do not chase later
remote drift unless it creates a conflict, fails a required check, or overlaps an
affected surface.

After material changes to commands, workflow behavior, trust boundaries, tests, or
product claims, run the `agents-md-auditor` skill before declaring completion. Keep
this file concise and repository-wide; put detailed lifecycle lessons in
`docs/lessons_learned.md`, release history in the roadmap/release notes, and mechanical
checks in tests or CI.

Treat SkillStore maintenance as a publication-close gate when a released change
materially alters the packaged skill's behavior, workflow, trust boundaries,
documentation, or specification metadata. Never report an unmerged local candidate as
published behavior. After the exact public commit or tag is final and any required
history remediation is complete, run a fresh assessment, obtain action-time approval
for the external update, submit an accurate change summary, and read the listing back.
If the release does not affect the SkillStore package or claims, record that no update
is needed instead of resubmitting it.

## Documentation governance

Follow `docs/DOCUMENTATION_POLICY.md` for README and public documentation changes.
Treat `README.md` as the first-visit product page, not the exhaustive reference:
preserve its first-120-line onboarding contract, summarize deeper material, and link
to the one authoritative task guide. Do not duplicate commands, lifecycle detail,
compatibility evidence, inventories, or release narratives across pages.

Only paths listed in `docs/public-files.allowlist` may live under `docs/`.
Keep owner-private evidence, dated campaign/readout reports, generated audits, and
temporary drafts outside the repository checkout; update the allowlist and repository
map deliberately when adding a real public guide.

README has a permanent 450-line ceiling; do not raise it to avoid moving detail to
the owning task guide. Verify changed commands and claims against source/tests,
preserve privacy and evidence boundaries, check affected relative/rendered links, and run
`python3 scripts/validate-docs.py . --readme-max-lines 450` followed by
`bash tests/test-packaging.sh` for README/docs/marketplace/Pages changes.

For material plans involving UX, lifecycle, trust boundaries, security, data semantics,
or other domain judgment, the coordinator and a suitable domain expert must co-plan
and freeze user journeys, acceptance tests, and authority/privacy constraints before
implementation. The planning expert and final reviewer must be different agents or
fresh contexts; neither may accept its own plan or implementation. The independent
reviewer remains the final acceptor. Purely mechanical changes are exempt.

Offline coverage is not a live-provider claim. Do not pin exact suite counts in this instruction file; `docs/REPO_MAP.md` owns focused-suite inventory and `scripts/ci_stages.py` owns the canonical CI stage registry.

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
material edge against source and tests. When relevant source changes make the graph
stale and a current relationship or impact query would materially help, follow the
Graphify skill's incremental update workflow before relying on it. If
`graphify-out/graph.json` already exists, an in-scope change to code structure,
workflow or trust boundaries, or graph-indexed documentation requires an incremental
Graphify refresh and graph-health readback before completion. Pure wording changes,
test-expectation corrections, and release or publication actions alone do not trigger
a refresh. Run it only over the reviewed repository corpus; if refresh fails, report
the graph as stale and do not cite it. Never add generated `graphify-out/` artifacts
to Git or an agy prompt, and keep owner-private evidence or untracked campaign
material outside the Graphify corpus.
