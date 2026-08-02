# Repository map

This is a concise, hand-maintained map of the repository. Update it when entry points,
trust boundaries, ownership, or test-suite responsibility changes. It is not generated
from Graphify or another indexer.

## Core delegation flow

```text
driver task
  -> model-recommendation.sh --stage pre-dispatch (visible advisory only)
  -> agy-worker.sh (bounded prompt, sandbox/mode/model, job artifacts)
  -> agy (untrusted worker)
  -> structured envelope
  -> scripts/validate-envelope.py (shape and contract only)
  -> qa-gate.sh (Git scope, immutable base, policy, escalation)
  -> driver-owned --verify commands
  -> model-recommendation.sh --stage post-gate (visible advisory only)
  -> human diff review and deliberate integration
```

The task, path policy, immutable base commit, verification commands, selected tier,
and routing evidence belong to the driver. A routing recommendation is display-only:
it does not alter the selected tier or participate in gate acceptance. The worker may
edit only the isolated worktree and may report claims, but its
commands never execute. Schema validation proves shape, not truth. The gate derives
Git-visible state independently, rejects undeclared, phantom, wrong-kind, outside-
policy, and verifier-created changes, and routes non-completed outcomes without
accepting them.

## Opt-in maintenance flows

- `update.sh check` queries the fixed official tool origin and agy compatibility
  sources without changing files. `update.sh apply [TAG]` is explicit: it verifies a
  stable tag and fast-forward, protects ignored-path collisions, runs the candidate
  suites and install preflight in a temporary worktree, then fast-forwards and
  reinstalls the skill. Candidate scripts still execute with user privileges; the
  temporary worktree is not a security sandbox.
- `bug-report.sh draft` creates a private sanitized local draft. `preview` prints the
  exact body and SHA-256. `submit` requires that hash and sends the already validated
  bytes to the explicitly bound GitHub destination. Nothing submits automatically.
- `skills/agy-worker/` is the canonical Agent Skill. Plugin-cache installs resolve the
  adjacent repository runtime; `install.sh` copies the same bundle and adds a local
  `.pipeline-root` marker. Codex and Claude manifests expose this one skill without
  duplicating the Bash/Python/git implementation or auto-publishing a listing.

## Ownership and test coverage

| Path | Responsibility | Owning offline suite |
|---|---|---|
| `agy-worker.sh` | Dispatch, model/mode selection, bounded retries, prompt staging, envelope extraction | `tests/test-agy-worker.sh` (57 cases) |
| `model-recommendation.sh`, `scripts/model-recommendation.py` | Side-effect-free pre-dispatch and post-gate tier recommendations from controlled driver evidence | `tests/test-agy-worker.sh` (57 cases) |
| `install.sh`, `skills/agy-worker/` | Install the canonical skill bundle and resolve either plugin-cache or standalone runtime | dispatcher and packaging suites |
| `schemas/worker-result.schema.json`, `scripts/validate-envelope.py` | Dependency-free envelope contract validation | dispatcher and gate suites |
| `qa-gate.sh` | Immutable-base Git audit, path policy, escalation, driver verification | `tests/test-qa-gate.sh` (41 cases) |
| `agents/*.md` | Prompt-injected bounded personas; prompt text is guidance, not enforcement | dispatcher suite plus bounded real exercises |
| `update.sh`, `compat/` | Explicit releases and fixed-source agy compatibility review | `tests/test-update.sh` (26 cases) |
| `bug-report.sh`, `scripts/bug-report.py`, `.github/ISSUE_TEMPLATE/` | Local privacy filtering, exact review binding, optional issue submission | `tests/test-reporting.sh` (21 cases) |
| `.codex-plugin/`, `.agents/plugins/`, `.claude-plugin/` | Skills-only plugin identity and opt-in repository marketplace catalogs | `tests/test-packaging.sh` (14 cases) plus platform validators |
| `PRIVACY.md`, `TERMS.md`, `SUPPORT.md`, `docs/MARKETPLACE.md` | Public data disclosure, project policy, support route, and external submission gates | `tests/test-packaging.sh` (14 cases) plus review |
| `docs/index.md`, `docs/_layouts/`, `docs/_config.yml`, `docs/sitemap.xml` | Static GitHub Pages landing, canonical metadata, and sitemap; enabling Pages and submitting the sitemap through Search Console remain external | `tests/test-packaging.sh` (14 cases) plus rendered review |
| `.github/workflows/test.yml` | Linux/macOS CI for syntax and all five offline suites | exercised by GitHub Actions |
| `README.md` | User setup, examples, current capabilities and limitations | review plus relevant offline suites |
| `AGENTS.md`, `docs/lessons_learned.md`, this file | Durable contributor rules and architecture | `agents-md-auditor` after material changes |

## Trust boundaries

- `agy` and every envelope field are untrusted. The driver's immutable base, path
  policy, and verification commands are trusted inputs and must be authored before
  dispatch.
- Model-routing evidence is a driver-owned classification, not worker prose. The
  recommender is outside the dispatch and acceptance paths, cannot execute either,
  and never applies its output. Default/custom tiers and the highest named tier fail
  safely to `no-escalation` when no ordered higher tier can be proved.
- `--workdir` is the single audited repository. User-supplied `--add-dir` roots must
  resolve inside it; multi-repository mutation is unsupported.
- Release tags are trusted only through the fixed official origin and exact ref/commit
  checks. Candidate validation executes release code and therefore relies on the
  maintainer account and tag-publishing boundary.
- Sanitization reduces accidental disclosure but does not replace exact human review.
  The reviewed hash must bind the bytes actually sent.
- A plugin install is local enablement, not consent to send repository content.
  Dispatch through agy can expose the approved prompt and worker-read files to
  Google/Gemini; the skill must obtain explicit approval for that named scope first.
- Marketplace metadata is not publication evidence. OpenAI and Anthropic submission,
  review, and publication remain separate human-approved external actions.

## Generated and private artifacts

- `logs/<job>/` contains the task, full prompt, stream, stderr, staged oversized
  prompt, and extracted envelope. Treat it as private evidence; do not commit or paste
  it into reports.
- Temporary worktrees, envelopes, updater candidates, and bug drafts normally live
  outside the repository. Preserve accepted work before cleanup; force removal is only
  for deliberately rejected disposable changes.
- `~/.gemini/` contains agy state. `~/.codex/skills/agy-worker/` is written only by an
  explicit `install.sh` or successful `update.sh apply`; its `.pipeline-root` is a
  local install artifact and must never enter the public skill bundle. Repository
  changes must not silently edit user configuration.
- Ignored files are still part of the gate and updater collision checks. “Ignored”
  never means “outside the trust boundary.”
