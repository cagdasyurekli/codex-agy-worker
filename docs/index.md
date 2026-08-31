---
layout: default
title: "codex-agy-worker — delegate coding work, verify before trust"
description: "Delegate bounded coding work from Codex to Antigravity CLI while Codex independently checks Git scope and runs driver-owned verification before acceptance."
canonical_url: "https://cagdasyurekli.github.io/codex-agy-worker/"
---

<section class="hero" id="overview">
  <div class="hero-copy">
    <p class="eyebrow">Open-source Codex agent skill</p>
    <h1>Delegate coding work. Verify before you trust it.</h1>
    <p class="hero-lead">codex-agy-worker lets Codex hand bounded repository work to Antigravity CLI, then independently review the resulting Git scope and run driver-owned checks before accepting the candidate.</p>
    <div class="hero-actions">
      <a class="button button-primary" href="https://github.com/cagdasyurekli/codex-agy-worker">View on GitHub</a>
      <a class="button button-secondary" href="{{ '/VERIFYING_AGENT_OUTPUT.html' | relative_url }}">See how verification works</a>
    </div>
    <ul class="trust-list" aria-label="Core safeguards">
      <li>Bounded worktrees</li>
      <li>Git-derived scope</li>
      <li>Driver-owned checks</li>
      <li>No autonomous publishing</li>
    </ul>
  </div>
  <aside class="proof-card" aria-label="Offline proof overview">
    <div class="proof-card-head">
      <span class="status-dot" aria-hidden="true"></span>
      <span>Offline proof overview</span>
    </div>
    <pre><code>$ ./proof-demo.sh

checks one passing edit
rejects one scope mismatch
runs no provider</code></pre>
    <p>The repository includes a fixed synthetic <a href="https://github.com/cagdasyurekli/codex-agy-worker/blob/main/proof-demo.sh"><code>proof-demo.sh</code></a> so the verification boundary can be inspected without provider access. Its output is starter evidence for fixed cases—not human review, candidate acceptance, or general correctness.</p>
  </aside>
</section>

<section class="section" id="how-it-works">
  <div class="section-heading">
    <p class="eyebrow">A bounded workflow</p>
    <h2>One delegation loop, three clear responsibilities.</h2>
  </div>
  <div class="card-grid">
    <article class="card">
      <span class="card-number">01</span>
      <h3>Dispatch the right workflow</h3>
      <p>Use exploration for read-only analysis, task for bounded implementation, or project for broader build-and-repair work.</p>
    </article>
    <article class="card">
      <span class="card-number">02</span>
      <h3>Review the actual candidate</h3>
      <p>Codex inspects the Git diff and derives scope from repository state instead of trusting a worker summary.</p>
    </article>
    <article class="card">
      <span class="card-number">03</span>
      <h3>Verify and report honestly</h3>
      <p>Driver-owned checks determine whether delivery is verified, partially verified, or blocked—with the candidate preserved for review.</p>
    </article>
  </div>
</section>

<section class="section" id="use-cases">
  <div class="section-heading compact">
    <p class="eyebrow">Choose by intent</p>
    <h2>Useful from first inspection to project-scale work.</h2>
  </div>
  <div class="card-grid">
    <article class="card">
      <p class="card-number">Explore</p>
      <h3>Understand unfamiliar repositories</h3>
      <p>Request broad, read-only exploration while Codex spot-checks material claims and labels coverage limits.</p>
    </article>
    <article class="card">
      <p class="card-number">Task</p>
      <h3>Implement bounded changes</h3>
      <p>Delegate a feature, refactor, or test change inside a disposable worktree, then verify the exact diff.</p>
    </article>
    <article class="card">
      <p class="card-number">Project</p>
      <h3>Run broader quality loops</h3>
      <p>Allow repo-wide candidate changes, build and test them, and request bounded same-conversation repair when checks fail.</p>
    </article>
    <article class="card">
      <p class="card-number">Proof</p>
      <h3>Inspect the trust boundary offline</h3>
      <p>Use the synthetic proof demo to see a valid candidate pass and an out-of-scope candidate get rejected.</p>
    </article>
  </div>
</section>

<section class="section evidence-section" id="evidence">
  <div class="section-heading compact">
    <p class="eyebrow">Evidence before acceptance</p>
    <h2>Four signals keep provider output in its proper place.</h2>
  </div>
  <div class="evidence-grid">
    <article class="evidence-item">
      <h3>Worker output is input</h3>
      <p>A worker envelope is never treated as final acceptance evidence.</p>
    </article>
    <article class="evidence-item">
      <h3>Git is the scope authority</h3>
      <p>The actual repository diff—not a narrated file list—defines what changed.</p>
    </article>
    <article class="evidence-item">
      <h3>Checks belong to the driver</h3>
      <p>Codex runs the relevant commands itself and reports the assurance level reached.</p>
    </article>
    <article class="evidence-item">
      <h3>Publication stays human-owned</h3>
      <p>Commit, push, pull request, release, and other external writes remain separate approval gates.</p>
    </article>
  </div>
</section>

<section class="section" id="provider-scope">
  <div class="section-heading compact">
    <p class="eyebrow">Transmission boundary</p>
    <h2>Write scope and provider read scope are different controls.</h2>
  </div>
  <div class="evidence-grid">
    <article class="evidence-item">
      <h3>Default facade dispatch</h3>
      <p>With default facade dispatch, those paths constrain candidate acceptance, not provider reads: content in the disposable worktree is potentially readable and transmissible.</p>
    </article>
    <article class="evidence-item">
      <h3>Selected-content dispatch</h3>
      <p>Optional direct `--provider-scope` dispatch instead binds exact reviewed read/write entries. It stages only the selected content in a private, Gitless provider directory.</p>
    </article>
    <article class="evidence-item">
      <h3>Authority remains separate</h3>
      <p>This means scoped staging is not a sandbox, and its approval grants no provider execution, Git, acceptance, or publication authority.</p>
    </article>
    <article class="evidence-item">
      <h3>Offline comparison only</h3>
      <p><a href="https://github.com/cagdasyurekli/codex-agy-worker/blob/main/docs/BENCHMARKING.md">Benchmark v1</a> compares fixed synthetic tasks from canonical gate receipts. It has no live provider mode, score, ranking, winner, route, or recommendation.</p>
    </article>
  </div>
</section>

<section class="section get-started" id="get-started">
  <div>
    <p class="eyebrow">Get started</p>
    <h2>Install from the repository source of truth.</h2>
    <p>Use the GitHub repository as the source of truth. Clone it, follow the installation guide, and review the trust and privacy boundaries before your first provider-backed dispatch.</p>
    <div class="hero-actions">
      <a class="button button-primary" href="https://github.com/cagdasyurekli/codex-agy-worker/blob/main/docs/INSTALLATION.md">Installation</a>
      <a class="button button-secondary" href="https://github.com/cagdasyurekli/codex-agy-worker/releases">Releases</a>
    </div>
  </div>
  <pre class="install-command"><code>git clone https://github.com/cagdasyurekli/codex-agy-worker.git
cd codex-agy-worker</code></pre>
</section>

<aside class="callout" aria-labelledby="privacy-title">
  <div>
    <p class="eyebrow">Provider boundary</p>
    <h2 id="privacy-title">Know what leaves your machine.</h2>
  </div>
  <p>Provider-backed work can transmit repository content in the approved scope. Keep credentials and private paths out of that scope, and review the project’s privacy guidance before dispatch.</p>
  <a href="https://github.com/cagdasyurekli/codex-agy-worker/blob/main/PRIVACY.md">Read the privacy guidance <span aria-hidden="true">→</span></a>
</aside>

<section class="section" id="docs">
  <div class="section-heading compact">
    <p class="eyebrow">Documentation</p>
    <h2>Go deeper without losing the boundary.</h2>
  </div>
  <div class="docs-grid">
    <a class="doc-link" href="{{ '/VERIFYING_AGENT_OUTPUT.html' | relative_url }}">
      <span>Verification tutorial</span>
      <small>Understand why worker output is not acceptance evidence.</small>
    </a>
    <a class="doc-link" href="https://github.com/cagdasyurekli/codex-agy-worker/blob/main/docs/USAGE.md">
      <span>Usage guide</span>
      <small>Choose workflows and understand their delivery semantics.</small>
    </a>
    <a class="doc-link" href="https://github.com/cagdasyurekli/codex-agy-worker/blob/main/PRIVACY.md">
      <span>Privacy guide</span>
      <small>Review provider transmission and local boundary considerations.</small>
    </a>
    <a class="doc-link" href="https://github.com/cagdasyurekli/codex-agy-worker">
      <span>Source and tests</span>
      <small>Inspect the implementation, checks, release notes, and open issues.</small>
    </a>
  </div>
</section>
