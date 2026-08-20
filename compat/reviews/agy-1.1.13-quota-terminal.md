# agy 1.1.13 quota terminal review

Reviewed on 2026-08-20 for the narrow Issue #59 controller classification.

This record does not activate a model matrix or authorize a provider call. An
owner-reviewed Codex session retained only a bounded projection of repeated agy 1.1.13
terminal events: one `result`, the exact observed result-key set, `status=ERROR`, an
empty response, string error type, numeric duration, integer turn count, object usage
and schema fields, and reset durations `4h51m54s`, `4h51m53s`, and `4h50m17s`.
Controller state retained the same conversation for explicit resume but reported exit
5 `agy_failed_unclassified`.

The public Antigravity project reports the canonical message family as
`Individual quota reached. Contact your administrator to enable overages. Resets in
<duration>.` The retained projection's exact error length and token set reconcile only
with the `rpc error: ` prefix plus that canonical text and the observed duration.
Production therefore accepts only the full byte pattern, exact agy version 1.1.13,
exact terminal structure, and `HhMMmSSs` duration grammar. Length, keyword, HTTP 429,
`RESOURCE_EXHAUSTED`, free-form stderr, and generic rate-limit prose are not classifier
inputs.

The public result is exit 24 `provider_quota_exhausted` and an optional bounded
`retry_after_seconds` countdown. It excludes error text, prompt, conversation, model,
path, source, envelope, and raw logs. It never authorizes automatic retry, restart,
sleep, or model selection. Other versions and shapes remain unclassified.
