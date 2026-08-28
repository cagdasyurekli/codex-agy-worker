# agy 1.1.22 version observation and blocked activation

Reviewed: 2026-08-27

This record preserves the reviewed `1.1.22` release, source, distribution, version,
help, and single failed account-capture observation. It does **not** activate 1.1.22.
The accepted `1.1.16` baseline and model/effort inventory remain current. This record
contains no account identifier, credential, HOME contents, private path, raw model
list, prompt, task, or provider response.

## Bound evidence

- Stable release/tag: `1.1.22` at direct release commit
  `556846a4bb94117222f53846896c7eb0d645307e`.
- Fixed `darwin_arm64` distribution build: `5711547746615296`; SHA-512
  `a8121185bd1c3455410ad41e88e2030ea237d496b8e40ccde313bf611c0551840fddf450b45c8e1a2575d9863c990b3324f19eef0f479936df8bfc6e4e80d30b`.
- Installed source/snapshot bytes SHA-256:
  `7b1317779085913d338bde0e9b39b72323d9083a879525f944fd469c8ecca906`.
- Raw C-locale combined help SHA-256:
  `c26943c81bf16cf55fb35e6152eda42de30f6e09cd671e29dcbc22bc5517fde6`.
- Fixed version-evidence runner SHA-256:
  `e2f6a50cad78ebf572719d81a5d1d5fee40b31808d960a0ac3f800db2bf9b9b7`.
- Version binding SHA-256:
  `d9d830e65d3a5c76df6d9e07e6ea7e14e14f290ab4036bdbae8cb33502e29f2a`.
- Process-inert capture profile source SHA-256:
  `045ae5617c90f47534ddb1f8fc7795fa0977826d74b331aa789a5a7e02df561d`.
- Exact private capture profile SHA-256:
  `e40e926c6e2baca75e10281d45462cf76e4bea7a80157acd8835b1e8a35bea20`.
- Capture-only runner source SHA-256:
  `c878d68c12017733878e463008eddb1d97213963675f567c47e1dd41e06586bc`.
- Sanitized single-call failure record SHA-256:
  `cab32a092e67b5199c1777e45f65623f703a94812b75a0732e7b3156302e9f77`.
- Raw capture stdout SHA-256:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- Raw capture stderr SHA-256:
  `00663fead5ee96eea3894a5397cc2f33ddad4c350322cd74ad91ddfa4dd64a9a`.

The separately authorized account-backed observation launched exactly one `models`
child and was not retried. The child exited `1`; stdout was empty and stderr was
retained privately under the 25-second and independent 64-KiB stream bounds. The
sanitized record is `child-failed` with `failure_classified: false`. No inventory was
accepted or interpreted, and no backend, routing, metadata, or activation authority
was established.

## Upstream behavior review

The official `1.1.17` through `1.1.22` changelog was reviewed at the exact release
commit. Relevant changes include nonzero reporting for exit-zero empty print-mode
failures in `1.1.18`, nonfatal print-mode tool and permission errors in `1.1.20`,
UTF-8 edit/output and post-write failure fixes in `1.1.21`, and Gemini 3.1 Pro effort
selection plus transient HTTP 502 retry changes in `1.1.22`. These notes are review
triggers, not authority for this wrapper to retry, reclassify arbitrary stderr, infer
provider behavior, or change caller-owned routing. Offline positive and negative
fixtures cover only the worker-owned empty-output, terminal-envelope, diagnostic, and
UTF-8 preservation boundaries.

## Decision and limits

The official 1.1.22 distribution remains an observational drift record, while the
checked-in active version, source, inventory binding, and matrix remain at accepted
1.1.16. Activation is blocked because the required account inventory evidence is
unavailable. The failed private capture is evidence of one bounded attempt, not an
inventory or activation decision.

This reconciliation does not prove provider/backend identity, model quality,
authentication state, pricing, quota, fallback, billing, or general retry behavior.
It does not authorize another account or provider call, retry, choose a model, change
caller selection, forward agy's separate `--effort`, or accept worker output. Reviewed
direct selection therefore continues to use the accepted 1.1.16 matrix; Codex owns
diff review and driver-run verification.

## Reprofile adapter

A separate process-inert reprofile adapter (`models_capture_1_1_22_reprofile.py`)
accepts an already-validated prior 1.1.22 capture profile and prepares a new profile
reflecting exactly one permitted change: `account_home_identity.nlink`. It reuses the
fixed 1.1.22 profile module's derivation and bounded validation of the explicitly
supplied recovery root's fixed artifact and scratch allowlists, and follows its
publication pattern. It never
enumerates or reads account HOME contents. It has no subprocess, network, Git, retry,
capture, inventory acceptance, routing, model selection, metadata update, or activation
authority. Reprofiling does not authorize a capture, contact a provider, call models,
or renew any prior one-call authorization. This adapter does not change the
non-activating status of agy 1.1.22.
