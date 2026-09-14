<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:41 UTC | BEGIN]] -->
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

## Correction (2026-09-14 20:41 UTC, Claude Sonnet 5 high)

The 2026-09-14 18:52 candidate below (`3fb24bb`) had two defects, both fixed in
this correction, in `src/director_orchestrator/provider_validation.py`:

- `claim_execution` dropped `.fetchone()` on its locked-row read, so every call
  indexed the live cursor object instead of the fetched row. A focused
  regression (`test_claim_execution_reads_back_running_state_and_checkpoint`
  in `tests/test_provider_validation.py`) now reads back the attempt state and
  the checkpoint rows after a claim, asserting exactly one `running`
  checkpoint at sequence 2, distinct from the `admitted` checkpoint at
  sequence 1.
- The anchored failure-cleanup allowance (`CLEANUP_ALLOWANCE_SECONDS`) was a
  fixed **two** seconds, reapplied in full whenever the primary operation
  deadline was already exhausted, and the Responses route only tightened the
  shared deadline to the canary packet's own ceiling *after* claim and the
  first fence check. Together these meant a contended claim or fence could
  wait on the loose transport ceiling (tens of seconds) and the cleanup
  marker could add a further two seconds on top of that -- silently
  inflating the documented two-second packet budget plus one-second
  scheduler grace boundary well past three seconds. The fix moves the
  packet-ceiling tightening to immediately after admission (before claim,
  fence and reservation, not only before settlement) and reduces the cleanup
  allowance to one second, so it is the *same* scheduler grace already
  budgeted for rather than a second, additional window. `tests/test_provider_validation.py`'s
  `_run_service_contention_case` asserts completion strictly inside
  `2.0 + 1.0` seconds and strictly before a real-SQL blocker's own 3-second
  self-release (previously the test asserted `2.0 + 1.0 + 2.0` while claiming
  the boundary was preserved, which it was not); it also expects the correct
  durable state per phase (`admitted` or `ambiguous` after a claim-phase
  timeout, since claim's own locked read never reaches its `running` write;
  `running` or `ambiguous` after a fence or settle timeout), records the
  blocker's thread/connection handle before it starts or waits, propagates
  blocker setup failures instead of swallowing them, and releases/joins the
  blocker in a `finally`. A dead `(admission["reservation",]).fetchone() if
  False else` expression left over from an earlier edit was also removed from
  the deferred-COMMIT test's query (it never executed; the query it guarded
  was already correct). A new uncontended control-case test
  (`test_service_openai_responses_control_uncontended_success`) exercises the
  same Responses service fixture end-to-end with no contention, and
  `tests/test_response_meter.py`'s configured-factory fixture now explicitly
  creates its isolated primary schema and runs the team DDL (via
  `antigravity_team.team_ddl`) before the attempt-ledger table and
  `register()`, since `PrimaryProviderBudgetAuthority.__init__` only
  validates the schema identifier and performs no DDL itself.

None of this correction's changes were executed against real PostgreSQL in
this environment: Docker was stopped throughout and no native PostgreSQL was
available, so `tests.test_provider_validation.PostgresValidationTests` and
`tests.test_response_meter.ResponseMeterTests` were NOT RUN (their
`setUpClass` raises immediately when `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN` is
unset -- this is an intentional zero-skip fixture design, not a new gap).
What *was* run in this environment: an `ast.parse` syntax check of the four
scoped Python files, and the full non-Postgres unittest discovery across
`test_openai_responses_validation`, `test_provider_validation` and
`test_response_meter` -- 37 tests, all passing, 0 skips. The claim/fence
timing fix and the six-case Postgres coverage above are source-level
corrections plus committed, host-runnable tests; they are not independently
verified against a real server by this correction, and this correction does
not claim its own acceptance or constitute independent review.

Separately, and independently of Docker's availability: actual OpenAI
charges and any rolling twenty-something-day cost allocation are UNKNOWN from
this repository. There is no authoritative billing or admission history
available to inspect here, so no canonical admission record or live-activation
inference can be drawn one way or the other; this correction makes no claim
about them.

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
  a fixed **one**-second grace (see the 2026-09-14 20:41 UTC correction above;
  this was two seconds and reapplied in full after the primary deadline was
  already spent, which silently inflated the documented boundary). The
  anchored cleanup deadline then only decreases, so per-statement refreshes
  inside the cleanup, including the refresh immediately before the implicit
  COMMIT, can never restart or extend the grace after the primary deadline has
  raised or expired. Both service call sites are bounded: the native lane uses
  the fixed anchored grace, and the Responses lane tightens it to the shared
  deadline's remainder. If the bounded marker cannot be written, the attempt
  stays exactly as the aborted operation left it (admitted or running),
  remains ineligible for redispatch, and stays reconcilable; no second
  dispatch is funded and no rollback is treated as reversing provider work.
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
  ledger. As of the 2026-09-14 20:41 UTC correction the fixture actually
  creates that isolated primary schema and runs `team_ddl` against it before
  creating the attempt table and calling `register()`; previously the schema
  and its tables were never created, since `PrimaryProviderBudgetAuthority.__init__`
  only validates the schema identifier and performs no DDL of its own.
  Production inventory guards, funding checks and daily caps are unchanged;
  the bridge performs the single authoritative reservation (one spend row at
  the signed amount) and exactly one usage row is asserted.
- Timing boundary: the independent PostgreSQL deadline cases keep the original
  acceptance boundary -- a two-second packet budget plus one second of
  scheduler grace, with completion strictly before the blocker releases. The
  anchored cleanup allowance is that same one-second grace, not a second,
  additional window stacked on top of it (see the 2026-09-14 20:41 UTC
  correction above for the two defects that previously let a contended claim
  or fence, plus cleanup, exceed this boundary).
- Settling enters its deadline before capture serialization: an exhausted
  budget refuses as a deadline, and a malformed capture fails independently
  as a TypeError, so neither can masquerade as the other.

As of the 2026-09-14 20:41 UTC correction: the non-Postgres tests in
`tests.test_openai_responses_validation`, `tests.test_provider_validation` and
`tests.test_response_meter` (37 tests) were executed in this environment and
pass with 0 skips. The Postgres-backed suites in those same modules
(`PostgresDeadlineTests`-equivalent coverage, `PostgresValidationTests`,
`ResponseMeterTests`) require the documented disposable PostgreSQL lane and
were NOT RUN here because Docker was stopped and no native PostgreSQL was
available; trusted CI on an isolated PostgreSQL 17 lane and a distinct
non-author reviewer must confirm them.

## Remaining limitations

- Host/application builders must inject `openai_responses_route` with a real
  enrolled meter registry; this patch does not change production host
  configuration or enable paid runtime switches.
- Publication of already-captured evidence still uses the staged
  `PublishProviderEvidence` path from prior work; this patch only adds the
  capture/validation route that can produce qualifying Responses evidence.
- The anchored cleanup fix and the reordered claim/fence deadline tightening
  are source-level corrections with committed, host-runnable tests
  (`_run_service_contention_case`'s three phases plus the new uncontended
  control case); none of them has been runtime-reproduced against a real
  PostgreSQL server in this environment, since Docker was stopped throughout.
- Failed-meter retention is not established: if the meter transaction cannot
  finish, only provider-side facts remain for reconciliation.
- Actual OpenAI charges and any rolling-window cost allocation are unknown
  from this repository; no authoritative billing or admission history is
  available here to inspect, independently of Docker's availability.
- Full CI on the documented disposable PostgreSQL lane and a distinct
  non-author reviewer remain required; this correction does not accept
  itself and is not an independent review.

<!-- [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:41 UTC | END]] -->
