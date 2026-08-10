# Persona Evidence Registry v1

`persona-evidence.sh` validates the fixed shipped-persona registry and renders its
deterministic Markdown table. For the shipped `offline-only` records it is a local,
read-only, no-Git command: it does not call agy, a provider, a network client, the
benchmark runner, or the gate. Checkout-only upper-state validation uses only the
fixed sanitized read-only Git object commands described below; portable copies fail
closed before any upper-state evidence read.

```bash
./persona-evidence.sh validate
./persona-evidence.sh report
```

The registry is canonical under `skills/agy-worker/runtime/compat/personas/` and has
an exact compatibility copy under `compat/personas/`. Its manifest permits only the
three hardcoded runtime personas. There is no registry-path option, environment
override, dynamic registration, promotion, or apply command.

## Evidence states

- `offline-only` binds the persona source/frontmatter/mode restriction and exact
  public P1-C manifest, fixture, selection, schemas, gate, and wrapper bytes. The
  synthetic candidate does not execute the persona, so this is contract coverage,
  not a behavior or quality result.
- `real-escalation-observed` additionally requires a public hash-bound real-job
  evidence manifest, canonical P1-C plan/result/Receipt, a separate real Receipt,
  driver-owned persona dispatch profile, exact base/selection/verifier/tool/version
  artifacts, routed `worker-escalation`, and a separate maintainer approval commit.
  All tool and persona source blobs come from one source commit that strictly
  precedes the evidence commit and equals the P1-C plan source revision. That source
  commit also supplies the exact P1-C portable-source inventory, runner, gate,
  wrapper, schemas, public manifest, fixtures, and variant from which the plan and
  result are derived. The Receipt base is a distinct target-repository commit.
- `accepted-real-candidate` additionally requires gate exit `0`, a validated
  `gate-passed` Receipt, exact `accept-edits` persona dispatch, exact candidate-diff
  binding, and a distinct human-review record committed with the maintainer approval.

Upper-state validation is checkout-only. It reads public evidence from immutable Git
objects, requires `evidence commit -> approval/review commit -> registry transition
commit` as strict ancestry after the source commit, checks exact blob modes and an
exact no-extra-file allowlist at every phase, and rejects portable upper states. This
proves protected-main sequencing, not authorship:
the trusted computing base is protected-branch policy, maintainers, OS administrators,
and the local Git object store. The records are unsigned and do not prove reviewer
identity or resist a maintainer who can rewrite protected history.

The public version attestation is a canonical, maintainer-reviewed reference to the
accepted private version binding. It binds the reviewed version, one-call result,
profile, executable/source/snapshot, the exact reviewed runner SHA, and exact
stdout/stderr hashes. It does
not publish or revalidate the private evidence, prove binary provenance, or add a
signature; the driver must separately bind the executable for a future real run.

All shipped records are currently `offline-only`. Historical exercises described in
the README lack the public Receipt/base/verifier/tool bindings required for promotion.
Changing a checked-in state is a reviewed source change; the validator never promotes
one automatically.

Statuses are evidence levels, not trust labels, routing inputs, quality scores, or
acceptance authority. Persona text remains prompt guidance. P1-C remains the evidence
producer and contains no persona trust label.

## Generated table

Run `./persona-evidence.sh report` to reproduce these exact rows:

| Persona | Allowed modes | Evidence status | Public evidence |
|---|---|---|---|
| `bulk-test-writer` | `plan`, `accept-edits` | `offline-only` | P1-C public contract; persona not executed |
| `diff-reviewer` | `plan` | `offline-only` | P1-C public contract; persona not executed |
| `repo-inventory` | `plan` | `offline-only` | P1-C public contract; persona not executed |

Statuses are evidence levels, not trust labels or acceptance authority.
