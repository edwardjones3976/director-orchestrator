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

Capture verification accepts Responses envelopes (`object=response`) while Codex
JSONL remains the native-host dialect.

## Remaining limitations

- Host/application builders must inject `openai_responses_route` with a real
  enrolled meter registry; this patch does not change production host
  configuration or enable paid runtime switches.
- Publication of already-captured evidence still uses the staged
  `PublishProviderEvidence` path from prior work; this patch only adds the
  capture/validation route that can produce qualifying Responses evidence.
- Full CI and independent non-author review remain required.

<!-- [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]] -->
