# AGY 1.2.6 candidate compatibility review

Reviewed: 2026-09-19

This record preserves the reviewed AGY `1.2.6` candidate interface evidence,
static analysis metadata, and bounded live observations before activation.
*Historical initial candidate stage note*: In this initial candidate stage,
the active baseline remained `1.2.2` and activation was deferred until accepted
capture, canary, and full driver-verification evidence were supplied. Subsequent
active compatibility promotion is recorded in [agy-1.2.6-activation.md](agy-1.2.6-activation.md).
This record contains no account identifier, credential, private path,
raw capture, prompt, task content, conversation identifier, or provider response.

## Bound candidate evidence

- Installed version: `1.2.6`
- Official release revision: `d39491f6f98a62aaf29af76964aa4fe75bc044d4`
- Executable SHA-256: `7e1a1036b68cd8e2ad66097ddc8f43dc2081079d51d340863647dc8de2a1bc84`
- Executable size: `184043424` bytes
- Distribution path component: `1.2.6-5912685477494784`
- Distribution URL: `https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.6-5912685477494784/darwin-arm/cli_mac_arm64.tar.gz`
- Distribution SHA-512: `a5c71e23bb2d32d056920f79c1c23be4693eec7d81525e93455d274031a2bff82c238b917e1198ca3697a35c715228c9bd23c067b40517bf511b6299af8520c1`
- Structural help digest: `5a03bf7dc9d3d7645f5853906cc117363fd8978a79c2a89521f0ff7590dbe454`
- Fixed model: `gemini-3.8-flash-high`

*Historical initial candidate stage note*: The candidate manifest row permitted `version-evidence` only prior to activation.

## Bounded live observations

- One canonical models capture succeeded (exit `0`, popen_count `1`, snapshot SHA matching
  source SHA, read-only snapshot). A complete 14-model inventory was independently accepted.
  Capture SHA: `c96a7875fee945c97edb61a44c83431b94ca1e97a841af718aa585b04d48bf4f`;
  version binding SHA: `9c6bd66e3d4fcd8c1c0ac2151a6cf72916dba68148822c53be852fea413992c3`.
- Actual native refusal canary: AGY 1.2.6 process exit `0`; stream terminal event
  `result.result.status` `SUCCESS`; `result.result.denied_actions` is a one-element array
  with exact keys `action` and `display_name`. No `structured_output` was present.
  Zero `AGY_ERROR` stderr lines were emitted. This is a live denial observation,
  NOT a live `AGY_ERROR` observation. The remediation controller extends closed refusal
  recognition to 1.2.6: before `missing_structured_output` handling, strict terminal framing,
  an exact provider returncode `0`, and the exact observed single-item refusal keys yield
  `permission_required` (exit `6`) without candidate/result, while non-zero provider exits or
  unknown/malformed shapes fail closed as `invalid_envelope`.
- Timeout canary and matcher: The approved timeout scenario uses controller hard `8s` with provider
  print-timeout `300s`. The approved 1.2.6 binary exposes the byte-exact timeout format
  `[agy] print timeout after %s with turn in progress; returning partial output`. The provider
  timeout matcher `_reviewed_provider_timeout_lines` is extended to 1.2.6 to recognize this
  exact line bound to the job's positive integer duration, preserving hard-deadline precedence.
  The static formatter is not live warning evidence.

## Static serializer metadata and operational dispatch

- Static analysis of the approved 1.2.6 binary's Go runtime struct metadata recovered
  `printmode.agentErrorPayload` serializer tags:
  - `short_error *string` (no omitempty)
  - `status *string` (omitempty)
  - `error_code *uint32` (omitempty)
  - `code_kind *string` (omitempty)
  - `retryable *bool` (no omitempty)
  - `error_id *string` (omitempty)
  - Fallback literal: `AGY_ERROR: {"short_error":%q}`
- Operational parsing: When returncode == `3`, stdout size is `0`, version == `"1.2.6"`, and version
  is observed, `_classify_stderr` validates column-zero `AGY_ERROR: ` lines `<= MAX_EVENT_BYTES` using
  `parse_agy_error` and maps to `provider_terminal_error` (exit `25`).
- Fail-closed marker rules: Raw JSON, leading whitespace, missing space after colon, duplicate markers,
  numeric overflow, or schema mismatch fail closed as `agy_failed_unclassified`. Any conflicting known
  stderr signatures (e.g. timeout or permission prompt) alongside an `AGY_ERROR` line fail closed as
  `agy_failed_unclassified`.
- Closed payload schemas:
  - Fallback literal: exact key set `{"short_error"}`, string value (empty string allowed).
  - Normal shape: requires both `short_error` (`str | None`) and `retryable` (`bool | None`).
    Optional fields: `status` (`str`), `error_code` (non-bool uint32 `0 <= code <= 0xFFFFFFFF`),
    `code_kind` (`str`), `error_id` (`str`). Unknown fields, duplicate keys, or constants are rejected.
- Guardrails: AGY_ERROR classification never overrides hard deadline, cancellation, binding failure, or
  non-empty stdout framing; produces no candidate or result artifact; never persists payload values or
  infers retry authority/timing; and does not change unknown `provider_terminal_status`.
- Retention of absence of live exit 3 evidence: No live AGY_ERROR/exit 3 sample was observed in any test
  or canary execution. Static metadata and operational dispatch provide verified defensive handling
  without asserting live occurrence.

## Activation boundary

*Historical initial candidate stage note*: During initial candidate evaluation, activation
remained deferred until the coordinator supplied accepted capture, canary, and full
driver-verification evidence in this conversation, with baseline `1.2.2` remaining active.
Active compatibility promotion is recorded in [agy-1.2.6-activation.md](agy-1.2.6-activation.md).
