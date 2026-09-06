# AGY 1.1.26 activation evidence

This record supports the v0.17.0 candidate compatibility activation. Publication and final repository verification are separate delivery steps.

The accepted version and account inventory records bind AGY **1.1.26**, reviewed upstream revision `3bc5795ff561c9d71bf1ce272f185aec6013e5e4`, executable SHA-256 `1f6e0a36834022fbe459fae51a48f83ceaf790ecb06f1ee2dc64b708864db7d9`, and version binding `5507b8e35f64227035a21f46ba4eee73894750c28ebb312f2403c996904e7541`. The inventory has fourteen model slugs, normalized SHA-256 `d5e58ab55e91ebd4a2cd23841c76cbe12b47d607c62cd8c834fc8f6b9f078ad7`. Model mappings are unchanged from the accepted 1.1.24 inventory; the new version/source/capture bindings are recorded in the synchronized compatibility artifacts.

On 2026-09-06, four explicitly approved provider starts exercised the public worker with `gemini-3.8-flash-high` under the normal session mode. The existing account session was used. Automatic executable updates were disabled only in the child processes, and the pinned executable hash remained unchanged across the runs.

| Path | Driver-observed result |
| --- | --- |
| Normal task | Only the synthetic target changed; its unchanged check passed. |
| Same-conversation repair | The deliberately incomplete first result failed its check; driver feedback continued the same conversation, and the repaired result passed. |
| Boost task | Only the synthetic target changed; its unchanged check passed. |

The campaign used four starts within a single twenty-minute window, with at most 180 seconds per start. Both unused contingency starts were closed. Async continuation launch success was distinguished from actual dispatcher terminal success before accepting the repair. Every final diff and check result received independent review.

This evidence establishes the exercised normal-session paths. It does not establish provider backend identity, model quality, exhaustive compatibility, native isolation compatibility with a live account, or live optional self-verification. Optional self-verification retains separate native containment and owning offline checks.

Activation promotes 1.1.26 to current, retains 1.1.24 as previous and 1.1.22 as legacy, and keeps 1.1.16 and 1.1.12 historical. The current model matrix, version manifest, inventory binding, runtime constants, doctor metadata, and model-selection schema must agree. Validation must run against those actual updated runtime bytes without overriding validator constants. Final stable offline CI and independent release acceptance remain required.
