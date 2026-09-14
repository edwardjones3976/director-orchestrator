<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:52 UTC | BEGIN]] -->
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
scoped Python files, and non-Postgres unittest discovery. Two distinct
selections were run, and they are not the same result even though both
happened to total 37 tests: the author's own broad command (`unittest
test_openai_responses_validation test_provider_validation test_response_meter
-v`) discovered all three modules and reported "Ran 37 tests" with 0
failures **plus 2 separate `setUpClass` errors** (the two Postgres-only test
classes above) -- this is not an overall PASS. The host then ran a narrower,
explicitly scoped command (`unittest test_openai_responses_validation
test_provider_validation.NativeCaptureTests -v`, i.e. excluding the
Postgres-only `PostgresValidationTests` class and the whole
`test_response_meter` module) and got **37 PASS, 0 skips, 1.722s**, with no
errors of any kind because nothing Postgres-dependent was in that selection.
At the time of the 20:41 UTC correction above, its claim of "six-case
Postgres coverage" was premature: only four full-service contention cases
(claim/fence/settle plus an uncontended control) existed, and the admission
and deferred-COMMIT cases were not yet implemented as full-service tests
(store-level-only admission and commit-time-lock tests already existed but
are not equivalent, since they never exercise the service's failure-cleanup
path). This 20:52 UTC correction adds the missing full-service admission and
deferred-COMMIT cases (see below), so the six-case claim is accurate as of
this correction; the claim/fence timing fix and this six-case Postgres
coverage remain source-level corrections plus committed, host-runnable
tests -- not independently verified against a real server, and this
correction does not claim its own acceptance or constitute independent
review.

Separately, and independently of Docker's availability: actual OpenAI
charges are UNKNOWN from this repository, and there is no authoritative
billing or admission history available here to inspect, so no canonical
admission record or live-activation inference can be drawn one way or the
other. The "rolling" control referenced elsewhere in this repository is a
rolling window over the **last 20 assignments**, not a twenty-something-day
window and not a cost-allocation mechanism; this correction makes no claim
about actual charges or about that assignment-count control.

## Correction (2026-09-14 20:52 UTC, Claude Sonnet 5 high)

Host review of the 20:41 UTC correction above found six further findings
(F1-F6), all fixed in this pass, in the same three files:

- **F1** (`tests/test_provider_validation.py`): the new control test's
  `SELECT state FROM {SCHEMA}.checkpoints c JOIN {SCHEMA}.attempts a ...`
  was ambiguous -- both tables define a `state` column. Qualified to
  `SELECT c.state`.
- **F2** (`tests/test_provider_validation.py`): `_run_service_contention_case`'s
  blocker self-release used `release.wait(3.0)` and the test asserted
  `release.is_set()` to prove the service finished before that safety-net
  watchdog fired. `threading.Event.wait` returns `False` on a timeout
  *without* setting the event, so that assertion could never actually
  observe an automatic watchdog release -- it only ever checked whether
  *this test* had called `release.set()` yet, which is a tautology at that
  point in the code. The blocker now records whether its own wait was ended
  by the watchdog or by an explicit release into `watchdog_fired`, asserts
  that flag is still unset immediately after the service call returns
  (proving the watchdog had not yet fired), asserts it is `False` after the
  explicit release and join (proving that release, not the watchdog, is what
  unblocked it), asserts the joined thread is no longer alive, and
  propagates (via a recorded, asserted-empty error list) any exception the
  blocker itself raised instead of silently dropping it.
- **F3** (`tests/test_provider_validation.py`): only four full-service C3
  cases existed (control, claim, fence, settle); the existing store-only
  admission and commit-time-lock tests exercise `PostgresValidationStore`
  directly and never touch `ValidationBridgeService`'s failure-cleanup path,
  so they are not equivalent to the original six full-service C3 phases.
  Added `test_service_openai_responses_admission_timeout_reserves_nothing`
  (a blocked `generations` row lock during `admit()`, which runs outside the
  failure-cleanup `try` block, so no ambiguity marker is ever written; the
  transport ceiling is set to 2s to match the packet ceiling, since
  admission happens before that packet is read back and so can only ever be
  bounded by the transport ceiling) and
  `test_service_openai_responses_deferred_commit_cannot_persist_late_settlement`
  (reuses this class's already-installed `qa_settle_commit_gate` deferred
  constraint trigger; a blocker holds the same advisory lock the trigger
  takes, so `settle()`'s own statements succeed but its implicit COMMIT
  blocks and rolls back instead of persisting a late `succeeded` state, with
  the correct lock witness, dispatch count and phase-correct final state).
- **F4** (`src/director_orchestrator/provider_validation.py`): reducing
  `CLEANUP_ALLOWANCE_SECONDS` from two seconds to one (20:41 UTC correction)
  was not sufficient on its own. `_cleanup_remaining`'s fallback still
  anchored a *fresh* one-second window at `time.monotonic()` -- i.e. at
  whatever moment cleanup happened to start -- rather than against the
  original absolute deadline. If the 2s primary operation timed out at
  `2s + epsilon`, cleanup could still finish just after `3s`, since the
  fresh window does not know how much of the shared boundary the primary
  phase already consumed. Fixed by anchoring the Responses lane's cleanup
  allowance to the *same absolute* `deadline + CLEANUP_ALLOWANCE_SECONDS`
  (an absolute point in time, computed once from the same shared `deadline`
  variable the primary operation already uses) instead of a relative,
  freshly-anchored window; if that absolute point has already passed,
  cleanup is refused outright rather than reset. The native lane (which has
  no shared deadline to anchor to) is unchanged and still gets a fresh,
  anchored one-second grace, as before. `store.fail(...)`'s call signature
  and keyword name are unchanged, so the existing
  `store.fail.assert_called_once_with(admission["reservation"],
  remaining_seconds=ANY)` expectations in `test_openai_responses_validation.py`
  still hold without modification.
- **F5** (this file): corrected above -- the rolling control elsewhere in
  this repository is over the last 20 *assignments*, not a day count or a
  cost-allocation mechanism, and the author's broad test command's result
  (37 passing plus 2 separate Postgres `setUpClass` errors) is now
  distinguished from the host's narrower, error-free 37/0-skip run.
- **F6** (`tests/test_response_meter.py`): the configured-factory fixture's
  cleanup was a flat sequence -- `if meter_pool is not None: meter_pool.close()`
  followed by the schema-drop-and-`handle.close()` block -- so an exception
  from `meter_pool.close()` would skip the schema drops and `handle.close()`
  entirely, leaking the control-plane handle and leaving the two temporary
  schemas behind. Restructured into nested `try`/`finally` so the schema
  drop and `handle.close()` are still attempted even if `meter_pool.close()`
  raises, and `handle.close()` is still attempted even if the schema drop
  raises.

As of this correction, the approved scoped command
(`unittest test_openai_responses_validation test_provider_validation.NativeCaptureTests`)
reports **37 PASS, 0 skips, 1.722s** in this environment, matching the host's
prior result exactly. The new full-service admission and deferred-COMMIT
tests, and every other Postgres-dependent test in this correction, remain
NOT RUN here -- Docker was stopped throughout -- and are sourced-level
corrections pending real-server verification, not a claim of passing
PostgreSQL CI.

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

As of the 2026-09-14 20:52 UTC correction: the approved scoped command
(`unittest test_openai_responses_validation test_provider_validation.NativeCaptureTests`,
deliberately excluding the Postgres-only `PostgresValidationTests` class and
the whole `test_response_meter` module) reports 37 PASS, 0 skips, 1.722s in
this environment -- see the 20:52 UTC correction above for why this is a
different, narrower selection than the author's earlier broad command, which
also totalled 37 passing tests but alongside 2 separate Postgres
`setUpClass` errors, not an overall PASS. The Postgres-backed suites in
these modules (`PostgresValidationTests`, `ResponseMeterTests`, and the new
full-service admission/deferred-COMMIT cases) require the documented
disposable PostgreSQL lane and were NOT RUN here because Docker was stopped
and no native PostgreSQL was available; trusted CI on an isolated
PostgreSQL 17 lane and a distinct non-author reviewer must confirm them.

## Remaining limitations

- Host/application builders must inject `openai_responses_route` with a real
  enrolled meter registry; this patch does not change production host
  configuration or enable paid runtime switches.
- Publication of already-captured evidence still uses the staged
  `PublishProviderEvidence` path from prior work; this patch only adds the
  capture/validation route that can produce qualifying Responses evidence.
- The anchored cleanup fix and the reordered claim/fence deadline tightening
  are source-level corrections with committed, host-runnable tests: the six
  full-service C3 cases (control, admission, claim, fence, settle, deferred
  COMMIT -- `_run_service_contention_case`'s three phases plus the dedicated
  admission and deferred-COMMIT tests, plus the uncontended control case);
  none of them has been runtime-reproduced against a real PostgreSQL server
  in this environment, since Docker was stopped throughout.
- Failed-meter retention is not established: if the meter transaction cannot
  finish, only provider-side facts remain for reconciliation.
- Actual OpenAI charges are unknown from this repository; no authoritative
  billing or admission history is available here to inspect, independently
  of Docker's availability. The rolling control referenced elsewhere in this
  repository is over the last 20 assignments, not a day count or a cost
  allocation mechanism, and this correction makes no claim about either.
- Full CI on the documented disposable PostgreSQL lane and a distinct
  non-author reviewer remain required; this correction does not accept
  itself and is not an independent review.

<!-- [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:41 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:52 UTC | END]] -->
