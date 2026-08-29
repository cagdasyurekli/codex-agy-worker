# Documentation policy

This policy keeps the public entry points concise, source-grounded, navigable, and
maintainable. It governs `README.md`, Markdown under `docs/`, and public documentation
claims elsewhere in the repository.

## Give each surface one job

- `README.md` is the first-visit product page. It explains the value, gets a new user
  to one safe successful task, states the most important trust boundaries, and routes
  deeper questions. It is not the exhaustive command, lifecycle, compatibility, test,
  or release-history reference.
- `docs/index.md` is the public Pages landing page. It introduces the product and
  routes readers to the tutorial and repository without copying the full README.
- Task guides under `docs/` own detailed installation, usage, project workflow,
  operations, verification, marketplace, benchmark, measurement, persona, profile,
  and policy material. README summarizes those surfaces and routes readers to the
  one task guide that owns the detail.
- `docs/REPO_MAP.md` owns maintainer-oriented paths, responsibilities, trust
  boundaries, and verification commands.
- `docs/lessons_learned.md` owns durable rationale and failure lessons, not current
  user instructions or release history.
- `docs/ROADMAP.md` and release notes own release status and history. Current behavior
  remains grounded in source, executable configuration, tests, and reviewed public
  documentation.

## Use progressive disclosure

Keep the README useful without requiring a visitor to read it end to end:

1. Preserve the first-120-line onboarding order: positioning, accurate badges,
   prerequisites, verified marketplace installation, GitHub fallback, the installation
   authorization boundary, offline proof, provider-transmission/privacy warning, one
   safe natural-language task, and the verification-tutorial link.
2. Below onboarding, keep only a compact evidence-pipeline explanation, intended and
   excluded use cases, a task-oriented documentation table, a compatibility summary,
   limitations, contributing/support entry points, and the license.
3. Put detailed commands, option catalogs, lifecycle recipes, compatibility evidence,
   CI operations, benchmarks, inventories, and release narratives in their owning
   task guide. Summarize and link from README instead of copying them.
4. Prefer a few coherent task guides over one file per small topic. A reader should
   choose a page by intent, not by knowing the source-tree layout.

The README has a permanent hard ceiling of **450 physical lines**. Never raise it
merely to avoid moving reference material to the task guide that owns it.

## Keep one authoritative owner

Every durable fact has one authoritative documentation owner. Other pages may provide
a short context sentence and a link, but must not maintain a second full copy.

| Topic | Documentation owner |
|---|---|
| First task and product overview | `README.md` |
| Installation, compatibility, doctor, sandbox, and agy troubleshooting | `docs/INSTALLATION.md` |
| Workflows, examples, options, personas/profiles entry points, and model selection | `docs/USAGE.md` |
| Project lifecycle, Verification v2, recovery, and Evidence Receipt v1 | `docs/PROJECT_WORKFLOW.md` |
| CI fallback, updates, notifier maintenance, and sanitized reporting | `docs/OPERATIONS.md` |
| Marketplace packaging and detailed install contract | `docs/MARKETPLACE.md`; README repeats only the minimal first-task commands and authorization boundary |
| Verification workflow tutorial | `docs/VERIFYING_AGENT_OUTPUT.md` |
| Public gate fixture contract | `docs/CONFORMANCE.md` |
| Repository architecture and owning checks | `docs/REPO_MAP.md` |
| Benchmarks and measurement | `docs/BENCHMARKING.md`, `docs/MEASUREMENT.md` |
| Personas and workload profiles | `docs/PERSONAS.md`, `docs/PROFILES.md` |
| Product/release state | `docs/ROADMAP.md` and release notes |
| Privacy, support, and terms | `PRIVACY.md`, `SUPPORT.md`, `TERMS.md` |

Before adding a new page, identify why an existing owner cannot hold the material. If
a topic moves, move the full detail, update inbound links and the repository map, and
remove the stale copy in the same change.

Packaging tests pin operational literals to their authoritative task guide, not to
README. Do not add an operational literal pin below README's first 120 lines. Exact
suite counts belong in the repository map or generated inventory rather than being
copied across README and task guides.

## Preserve claims and trust boundaries

- Derive commands, versions, supported behavior, counts, and path names from current
  source, executable configuration, or tests. Do not describe agy-facing flags from
  memory; use the repository's ground-truth workflow when those claims change.
- A worker report remains an untrusted claim. Git-derived scope and driver-owned
  verification are evidence only for the exact candidate and checks exercised.
- Installation never authorizes provider dispatch or repository transmission. Place
  the Google/Gemini transmission warning before the first agy-backed usage example.
- Do not claim official status, certification, general security, general correctness,
  guaranteed ranking, provider quality, or task quality.
- Keep offline-test, fixture-conformance, green-gate, and exit-code claims narrow. A
  check proves only its exercised contract.
- Model and effort choices remain caller-owned. Avoid static model badges and transient
  model recommendations in the README.

## Keep private and campaign artifacts out of public docs

`docs/public-files.allowlist` is the complete set of files allowed under `docs/`,
including Pages layouts, configuration, sitemap, and brand assets. A new public page
or supporting asset requires a deliberate allowlist and repository-map update in the
same change. High-signal private, report, draft, campaign, or dated paths are rejected
even if they are accidentally allowlisted. Do not commit private evidence receipts,
owner-specific readouts, raw provider/account material, generated audits, temporary
drafts, or local investigation notes under `docs/`.

Keep private artifacts outside the repository checkout. Stable product behavior belongs
in a task guide; durable engineering rationale belongs in `docs/lessons_learned.md`;
release outcomes belong in release notes or `docs/ROADMAP.md`; actionable defects belong
in issues. Do not publish a campaign report merely because its secrets were removed.

`.gitignore` excludes the owner-private root `evidence` path (file, symlink, or
directory) and common
`docs/private/`, `docs/reports/`, `docs/drafts/`, and dated campaign-report patterns
as an early guard. The complete inventory and forbidden-path checks remain authoritative
when a file is force-added or renamed around those patterns; broad `*report*` ignores
are avoided so legitimate public documentation does not disappear silently.
Repository-ignored operating-system or interpreter cache noise is not public content;
remove it before local validation rather than allowlisting it.

## Write links and commands for maintenance

- Use descriptive link text. Avoid "click here" and raw URLs when a normal Markdown
  link is clearer.
- Prefer repository-relative links for repository files and canonical absolute URLs
  only for public Pages metadata, external sources, or copy intended outside GitHub.
- Do not use root-relative local links. Their meaning differs between the GitHub
  repository view and a project Pages base path.
- If a commonly linked heading moves, keep a concise heading with the same anchor and
  link to the new owner where practical.
- Keep code blocks copyable and state their working directory, prerequisites, and
  side effects when those are not obvious.
- Do not publish realistic fake identifiers, local absolute paths, credentials,
  private evidence paths, TODO/TBD copy, or unverified installation commands.
  Unmistakable shell variables are allowed in examples only when a fail-closed
  preflight requires the reader to supply the real value.
- Pages source links must resolve after Markdown-to-HTML rendering; source `.md` and
  rendered `.html` destinations are different contracts.
- Public Pages must not create body-level horizontal overflow at a 390-pixel mobile
  viewport. Wide Markdown tables may scroll within their own container, while long
  inline-code tokens must wrap; do not hide overflow at the page root.

## Change checklist

For every README or public-doc change:

1. Identify the reader and authoritative topic owner before editing.
2. Verify changed commands and factual claims against source/tests.
3. Check the first 120 README lines when onboarding, installation, privacy, or the
   first example changes.
4. Check inline relative Markdown links, retained anchors, Pages canonical metadata,
   sitemap entries, and reciprocal navigation for the surfaces touched. Reference-style
   or multiline links require explicit review because the dependency-free validator
   intentionally covers the repository's current inline-link forms.
5. Update `docs/REPO_MAP.md` only when ownership, entry points, or verification change;
   update `docs/lessons_learned.md` only for durable rationale.
6. Confirm every file under `docs/` is intentional and present in
   `docs/public-files.allowlist`; private or campaign artifacts must stay outside
   the checkout.
7. Run `python3 scripts/validate-docs.py . --readme-max-lines 450`, then
   `bash tests/test-packaging.sh` for README/docs/marketplace/Pages changes. After
   material public claims or stable cross-cutting documentation bytes change, run
   `./scripts/ci-offline.sh` once and apply the repository's review requirements.
8. Render and inspect live Pages on desktop and mobile only after separately authorized
   publication. A successful render is not indexing or ranking evidence.

## Review failures

Reject a documentation change when it:

- makes README longer by adding reference detail that has a clear task-guide owner;
- duplicates an authoritative explanation instead of summarizing and linking;
- breaks the first-task path, a retained anchor, or a relative/rendered link;
- creates body-level horizontal overflow on the supported mobile QA viewport;
- changes a command or public claim without evidence;
- weakens a privacy, authorization, verification, or claim boundary;
- mixes release history, maintainer architecture, and first-visit onboarding on one
  surface without a concrete reader need.
