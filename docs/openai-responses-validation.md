<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | BEGIN]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | BEGIN]] -->
<!-- [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | BEGIN]] -->
# OpenAI Responses validation route

Provider validation for `openai` previously always constructed
`WindowsHostProviderExecutor`, which routes OpenAI to `NativeCodexPacketTransport`.
That native path intentionally refuses before launch because the verified Codex CLI
cannot enforce a signed `total_token_limit`, so configuration could not mint
Responses LIVE_OK evidence.

## Explicit route

`ValidationBridgeService(..., openai_responses_route=OpenAIResponsesValidationRoute(...))`
selects the Responses API path for `provider=openai` only when that argument is
supplied. The default remains the native host bridge.

The route requires:

- an enrolled `response_meter` on `OpenAIResponsesPacketTransport` (unmetered
  results cannot become LIVE_OK)
- a positive signed `total_token_limit`
- a `configuration_digest` from `responses_validation_configuration_digest` that
  binds model, API URL, credential digest, enrollment organization/project ids,
  meter enrollment digest, and token limit
- signed admission and fence verification before any HTTP request
- provider-returned account headers and usage at meter settlement (missing headers
  refuse; they are never invented)
- fully pinned canary identity (task, assignment, attempt digest, packet digest)
  and authenticated monetary funding before dispatch; missing pins or funding
  refuse exactly like mismatched ones

Capture verification accepts Responses envelopes (`object=response`) while Codex
JSONL remains the native-host dialect.

## Repaired validation behavior (2026-09-14 proposal; host validation pending)

- Activation binding fixtures use the actual seven-field
  `ProviderActivationBinding` contract; the earlier `model`/`account_id` keys
  were never binding fields. Hexadecimal generation/tree fixtures and the
  native Codex hard-cap refusal regression are preserved.
- One whole-operation deadline, measured from a single start reading taken
  before admission, bounds store connection acquisition, admission, monetary
  reservation, claim, fence, transport, capture, settlement and COMMIT alike.
  The canary packet's own ceiling tightens that same deadline from the shared
  start; a post-admission reading never extends it. Store calls receive the
  shared deadline, so connection waits and transaction-local statement/lock
  timeouts -- including commit-time deferred-trigger lock waits -- are bounded:
  a blocked settlement or COMMIT aborts and rolls back instead of persisting
  eligible evidence after expiry, while real provider results, usage and
  charges are never treated as reversed and remain reconcilable facts.
- The bridge regression now exercises the configured factory
  (`responses_route_from_file`) end to end with the entitlement fixtures' real
  DSN and secret files, one authoritative reservation performed by the bridge
  itself through `reserve_monetary_admission`, real capture verification, and a
  durable no-replay refusal; a successful mocked admission alone proves
  nothing. Deterministic expiry regressions prove no dispatch and no
  settlement after expiry and that the packet ceiling is measured from the
  operation start; PostgreSQL-backed regressions cover bounded admission and
  settlement row locks, an exhausted deadline refusing before any store
  effect, and a blocked COMMIT that cannot persist late settlement.

Host suites `tests.test_openai_responses_validation`, `tests.test_provider_validation`,
`tests.test_response_meter` and `tests.test_primary_transports` (Postgres-backed
modules on the documented disposable Windows lane) were NOT executed by this
tools-disabled proposal; trusted CI and a non-author reviewer must confirm them.

## Remaining limitations

- Host/application builders must inject `openai_responses_route` with a real
  enrolled meter registry; this patch does not change production host
  configuration or enable paid runtime switches.
- Publication of already-captured evidence still uses the staged
  `PublishProviderEvidence` path from prior work; this patch only adds the
  capture/validation route that can produce qualifying Responses evidence.
- Full CI and independent non-author review remain required.

<!-- [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]] -->
