# QA gate conformance v1

The public conformance kit lets an integration or fork test one executable gate
against the repository's reviewed synthetic contract:

```bash
./conformance/run.sh --gate /absolute/or/relative/path/to/qa-gate.sh
```

Success prints exactly:

```text
CONFORMANCE_RESULT version=v1 fixtures=11 status=passed
```

That result means only that the supplied gate entry point produced every exact exit
required by `conformance/v1/manifest.json` for these fixed fixtures. It is not a
security certification, correctness proof, real-job quality result, human diff
review, Receipt v1 compatibility claim, or statement about untested inputs.

## Versioned contract

The v1 manifest is canonical JSON and is SHA-256-pinned by the v1 runner. It binds
every static envelope and repository-content source by hash, fixes the fixture order,
and fixes time and output limits. Its eleven cases cover:

| Fixture | Required gate exit | Boundary exercised |
|---|---:|---|
| `honest-edit` | `0` | exact declared edit plus driver-owned verification |
| `scope-undeclared` | `10` | undeclared worktree content |
| `ignored-untracked` | `10` | ignored content remains in audited scope |
| `untrusted-worker-command` | `11` | worker command claims remain data |
| `malformed-envelope` | `12` | complete schema validation |
| `expected-edits-missing` | `13` | `--expect-edits` cannot accept a no-op |
| `verifier-failure` | `14` | a failing driver verifier rejects |
| `verifier-mutation` | `14` | a mutating verifier rejects |
| `human-required` | `15` | human-required work is routed, never accepted |
| `mutable-base` | `64` | symbolic `HEAD` is not immutable authority |
| `missing-verifier` | `64` | acceptance requires driver verification |

The two repository states used by `proof-demo.sh` are the small teaching subset of
the same versioned envelope and content sources. The full kit is the compatibility
contract; the starter proof remains only a short demonstration.

Changing a required fixture, exit, source byte, bound, or claim requires a new
reviewed manifest digest. An incompatible semantic change requires a new conformance
version rather than silently redefining v1.

## Execution and privacy boundary

The runner creates owner-private disposable Git repositories outside the checkout,
uses full commit IDs except in the deliberate mutable-base rejection, passes only
fixed verifier kinds, bounds each gate invocation to ten seconds and 8 KiB per
output stream, and removes its workspace before reporting success. It invokes no
agy process, provider, network client, Receipt renderer, lifecycle action, model
selector, or recommendation policy. Output never includes fixture paths or captured
gate output.

`--gate PATH` is still an explicit request to execute that program with the current
user's privileges. The kit is not a sandbox: a hostile gate can access user-visible
files, spawn detached processes, or use the network. Review the gate and its loaded
dependencies before running it. The process-group supervisor bounds ordinary child
output, timeout, and HUP/INT/TERM cleanup. It keeps the leader unreaped while sending
TERM and, when needed, SIGKILL, then performs one reap and makes no post-reap PGID
query. Runtime tests prove the known descendant PIDs and late markers are gone; that
is not OS isolation or proof against a program that deliberately escapes its process
group.

The cleanup trust computing base (TCB) includes the supplied gate and loaded code,
the local owner and same-UID processes, and OS administrators. The runner holds
close-on-exec, no-follow descriptors for the private parent and root and binds their
directory identities. While the original pathname identities remain exact, it
deletes contents through bounded descriptor-relative traversal: nested directories
are opened without following links, and symlinks are unlinked without touching their
targets. The final pathname removal is explicitly inside the same-UID TCB.

If the root or parent is renamed, removed, replaced, changed to a symlink, or moved
outside that parent, cleanup fails closed without disclosing a path and may leave a
private residual. It never scans for or chases a moved directory. This design does
not claim same-user tamper resistance; review the supplied gate and loaded code
before execution and inspect private residuals after a hostile-gate failure.

The kit directly tests `qa-gate.sh`. Evidence Receipt v1, evidence reports, the local
job lifecycle, worker dispatch, recommendation policy, and real provider behavior
are intentionally outside the v1 claim. Receipts and reports remain non-authoritative
views of the gate result, and `qa-gate.sh` remains the sole acceptance authority.

## Runner exits

| Exit | Meaning |
|---:|---|
| `0` | all eleven exact fixtures matched |
| `1` | the supplied gate returned a wrong fixture exit |
| `2` | manifest, fixture, process, bound, or cleanup validation failed closed |
| `64` | invocation was invalid |
| `129`, `130`, `143` | HUP, INT, or TERM interrupted the run |

Inspect and human-review the implementation even after exit `0`. Passing a finite
public fixture set is useful interoperability evidence, not a claim that a gate is
secure against fixtures it has not seen.
