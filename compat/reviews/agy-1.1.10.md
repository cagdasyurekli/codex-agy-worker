# agy 1.1.10 compatibility reconciliation

Reviewed: 2026-08-05

This record binds the agy `1.1.10` baseline and model/effort resolution metadata
to sanitized evidence. It contains no prompt, source content, model stream, worker
envelope, credential, or private artifact path.

## Official evidence

- Stable release: `1.1.10`.
- Reviewed official source revision:
  `bfab12dac5bd090015a89cf82e65093d13b567d9`.
- The official release notes describe fixes for `--model` and `--effort` being
  ignored in interactive and print sessions, and for a bare effort selection not
  using the expected default model.
- The reviewed public source tree did not expose enough of the shipped model
  selection implementation to prove dual-selector composition or precedence.
  Runtime resolution therefore remains limited to one exact advertised model slug.

The fixed distribution-manifest snapshot is consistent with `1.1.10`, but remains
an observational canary rather than source, signature, or activation authority.

## Installed inventory

One read-only `agy models` probe on 2026-08-04 against the semantic `1.1.10`
install produced a canonical inventory artifact with SHA-256
`3ec89e109a0fcbeed902642a5a44b4d40df768b5048ab8a8b8b3642e7f70b419`.
Semantic normalization of that artifact yielded exactly these 11 slugs:

```text
gemini-3.6-flash-low
gemini-3.6-flash-medium
gemini-3.6-flash-high
gemini-3.5-flash-low
gemini-3.5-flash-medium
gemini-3.5-flash-high
gemini-3.1-pro-low
gemini-3.1-pro-high
claude-sonnet-4-6
claude-opus-4-6-thinking
gpt-oss-120b-medium
```

The exact inventory supports the eight explicit pair-to-slug mappings in
`agy-model-effort-matrix.json`. Gemini 3.1 Pro `medium` is rejected because no such
slug appears. The three remaining slugs are fixed choices: their labels do not
create adjustable effort inputs or prove provider capabilities.

## Bounded CLI behavior

Two separately approved synthetic jobs used the same one-file fixture and one
attempt each:

- `gemini-3.6-flash-high`
- `gemini-3.1-pro-high`

For each job, driver-owned argv capture observed exactly one `--model` with the
selected compound slug and no `--effort`. agy produced the exact synthetic edit,
and `qa-gate.sh` passed the independently verified candidate. These runs establish
representative single-selector CLI behavior; they do not prove which provider
backend served either request because silent fallback could not be independently
excluded.

## Claim limits

This reconciliation does not expose a wrapper effort option, forward agy's
`--effort`, prove dual-selector behavior, create an alias, change tier mappings or
recommendations, rank model quality, or establish provider billing. The matrix is
compatibility metadata only. It cannot dispatch, recommend, accept a candidate, or
replace `qa-gate.sh` and human diff review.
