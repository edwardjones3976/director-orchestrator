<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | BEGIN]] -->
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

## Repaired validation behavior (2026-09-14 18:52 correction proposal; host validation pending)

- Activation binding fixtures use the actual seven-field
  `ProviderActivationBinding` contract; the earlier `model`/`account_id` keys
  were never binding fields. Hexadecimal generation/tree fixtures and the
  native Codex hard-cap refusal regression are preserved.
- One whole-operation deadline, measured from a single start reading taken
  before admission, bounds store connection acquisition, admission, monetary
  reservation, claim, fence, transport, capture, settlement and COMMIT alike.
  The canary packet's own ceiling tightens that same deadline from the shared
  start; a post-admission reading never extends it. Store calls receive the
  shared deadline, and statement/lock timeouts are re-derived before later
  statements and again immediately before the implicit COMMIT, so
  commit-time deferred-trigger lock waits are bounded by the time actually
  left rather than an earlier, larger figure: a blocked settlement or COMMIT
  aborts and rolls back instead of persisting eligible evidence after expiry,
  while real provider results, usage and charges are never treated as reversed
  and remain reconcilable facts.
- Anchored failure cleanup: `PostgresValidationStore.fail` writes its
  best-effort ambiguity marker under one allowance anchored at the first
  reading -- the smaller of the shared operation deadline's remaining time and
  a fixed two-second grace. The anchored cleanup deadline then only
  decreases, so per-statement refreshes inside the cleanup, including the
  refresh immediately before the implicit COMMIT, can never restart or extend
  the grace after the primary deadline has raised or expired. Both service
  call sites are bounded: the native lane uses the fixed anchored grace, and
  the Responses lane tightens it to the shared deadline's remainder. If the
  bounded marker cannot be written, the attempt stays exactly as the aborted
  operation left it (admitted or running), remains ineligible for redispatch,
  and stays reconcilable; no second dispatch is funded and no rollback is
  treated as reversing provider work.
- Meter persistence versus capture retention: a successful meter commit
  durably stores the signed usage/charge summary and response/request
  identifiers in `director_primary_provider_usage`. The raw provider response
  bytes and the validation capture remain in process only and can be lost if
  validation settlement fails. When the meter transaction itself cannot
  complete, no retention is established by this path: the reservation stays
  held and actual usage remains a provider-side reconcilable fact.
  `record_usage` internals were not independently inspected by this proposal.
- Actual-factory fixture topology: the configured-factory regression builds
  its own entitlement-only control-plane schema, so the factory's strict
  inventory validation sees exactly the entitlement manifest tables, plus a
  separate primary schema holding the team spend/usage tables and the attempt
  ledger. Production inventory guards, funding checks and daily caps are
  unchanged; the bridge performs the single authoritative reservation (one
  spend row at the signed amount) and exactly one usage row is asserted.
- Timing boundary: the independent PostgreSQL deadline cases keep the original
  acceptance boundary -- a two-second packet budget plus one second of
  scheduler grace, with completion strictly before the blocker releases. This
  correction does not inflate any timing limit; the cleanup allowance is
  additional, anchored, and bounded.
- Settling enters its deadline before capture serialization: an exhausted
  budget refuses as a deadline, and a malformed capture fails independently
  as a TypeError, so neither can masquerade as the other.

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
- The anchored cleanup correction and the service-level contention regressions
  are source analysis plus proposed executable tests; neither has been
  runtime-reproduced by this tools-disabled proposal.
- Failed-meter retention is not established: if the meter transaction cannot
  finish, only provider-side facts remain for reconciliation.
- Full CI and independent non-author review remain required.

<!-- [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | END]] -->
