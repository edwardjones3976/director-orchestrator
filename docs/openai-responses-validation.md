<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 04:42 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:57 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:02 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 21:26 UTC | BEGIN]] -->
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
  meter enrollment digest, token limit, and the transport's
  `maximum_timeout_seconds` ceiling (changing the ceiling changes the identity),
  plus `responses_runtime_binding`: the executing package directory (an
  installed bridge copy's content address), the runtime content address
  (every file Install copies, including `docs/schemas`, so a schema-only change
  counts), the proxies urllib resolves (registry fallback included), the
  content of the `SSL_CERT_FILE` bundle or of every file in `SSL_CERT_DIR`
  (absolute paths only), and the
  proxy/CA/key-log names httpx and `ssl` honour (`SSLKEYLOGFILE` too) (`HTTP_PROXY`, `HTTPS_PROXY`,
  `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`,
  `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, any case), so replacing the runtime
  or its proxy/CA environment refuses an older pin as drift
- signed admission and fence verification before any HTTP request
- provider-returned account headers and usage at meter settlement (missing headers
  refuse; they are never invented)
- fully pinned canary identity (task, assignment, attempt digest, packet digest)
  and authenticated monetary funding before dispatch; missing pins or funding
  refuse exactly like mismatched ones

Capture verification accepts Responses envelopes (`object=response`) while Codex
JSONL remains the native-host dialect. `CaptureVerifier` compares the signed
capture's candidate, generation **and expected runtime tree** against its pins
(the tree is read from `DIRECTOR_PROVIDER_VALIDATION_TREE`; a valid capture
minted for another tree never qualifies), and for the Responses lane it also
requires the capture's `configuration_digest` to equal the bound route's
*current* digest, so a rotated credential, endpoint, meter enrollment or
timeout ceiling refuses qualification even though the retained response
re-normalises identically.

## Corrections (2026-09-20 | 06:01 UTC, Claude Opus 5, Codex Director REQUEST_CHANGES at `2fbcb6a`)

- **Tree binding (High).** `CaptureVerifier` takes `tree=` and refuses a
  capture whose `tree` differs; `capture_verifier_from_environment` passes
  the environment pin through. Regression: a wrong-tree verifier refuses a
  capture the right-tree verifier accepts.
- **Current-configuration comparison (High).** The verifier compares the
  capture's `configuration_digest` with the route's current
  `configuration_digest` for every lane whose route it holds: the Responses
  route (`configuration_digest` property) and the Antigravity team route
  (`configuration_digest()` fingerprint). Regression: rotating the transport
  credential, API URL or timeout ceiling, or the team worker fingerprint,
  flips a passing verification to refusal. Native command lanes carry no
  recomputable identity in the verifier (the executor and account files
  exist only in the bridge service), so their configuration pin is enforced
  at execution time by `production_configuration_digest` before and after
  the run; a later native rotation is visible only through re-execution.
- **Connection acquisition deadline (Medium).** libpq honours only
  whole-second `connect_timeout` values of at least two seconds, so
  `_connect_bounded` no longer rounds a sub-second budget upward: when the
  remaining budget is smaller than what the driver would wait, the connect
  runs on a worker thread and is awaited for exactly the remaining budget
  (a late connection is closed on arrival), and every acquisition re-reads
  the shared deadline after the connection returns. Regression: a slow
  connect against a 0.4 s budget fails closed within the budget.
- **Timeout ceiling in the identity.** `maximum_timeout_seconds` is part of
  the configuration digest (see above); 180 and 180.0 are one identity.
- **Funding COMMIT refresh.** `reserve_monetary_admission` re-derives the
  transaction-local statement/lock timeouts from the time actually left
  immediately before the transaction block exits, so the funding COMMIT
  waits under the real remainder. Covered by an ordering test and by real
  funding-schema contention against the configured authority and pool (an
  advisory budget-lock holder aborts the reservation under `lock_timeout`
  with no spend row persisted).
- **Capture size reconciliation.** The native Responses body is read under
  `MAX_CAPTURE_BYTES - 4096` and the retained bundle
  (`{"response", "response_headers"}`, header values bounded at 512 bytes)
  is encoded and size-checked *before* the meter settles, so a metered
  response can never be uncapturable. Regression: the encoded boundary case
  (a body exactly at the native bound) is captured within the validation
  bound; one byte over is refused mid-stream with nothing settled.

## Corrections (2026-09-14, Claude Sonnet 5 high)

Three correction passes were made against the 2026-09-14 18:52 candidate
below (`3fb24bb`) and its descendants, in
`src/director_orchestrator/provider_validation.py`,
`tests/test_provider_validation.py`, `tests/test_response_meter.py` and this
file. The first two passes (20:41 and 20:52 UTC) were source-level and had no
PostgreSQL available to test against (Docker was stopped); their timing and
"six-case coverage" claims were **not** independently confirmed and one of
them turned out to be wrong, per the third pass below. The 21:26 UTC pass had
access to a real, host-controlled disposable PostgreSQL 17.11 instance and to
independent real Linux CI results, and is what the "Final status" section
below reports.

- **20:41 UTC**: fixed `claim_execution` dropping `.fetchone()` on its
  locked-row read (it indexed the live cursor instead of the fetched row);
  added `test_claim_execution_reads_back_running_state_and_checkpoint`.
  Reduced the failure-cleanup allowance (`CLEANUP_ALLOWANCE_SECONDS`) from a
  fixed two seconds -- reapplied in full even after the primary deadline was
  already spent -- to one second, and moved the Responses route's
  packet-ceiling deadline-tightening to immediately after admission instead
  of after the first claim and fence check, so claim/fence contention is
  bounded by the packet ceiling rather than the much looser transport
  ceiling. Fixed the configured-factory meter fixture to actually create its
  isolated primary schema and run `antigravity_team.team_ddl` against it
  before the attempt-ledger table and `register()` (the authority's
  constructor only validates the schema identifier; it performs no DDL).
  Removed a dead `(admission["reservation",]).fetchone() if False else`
  expression from the deferred-COMMIT test's query. Added an uncontended
  control-case test. **This pass believed, incorrectly, that `2.0+1.0`
  seconds was now a safe margin** -- see 21:26 UTC below.
- **20:52 UTC** (host findings F1-F6): qualified an ambiguous `state` column
  in a joined query; fixed a test assertion (`release.is_set()`) that could
  never actually observe a `threading.Event` timing out on its own, since
  `Event.wait` does not set the event on timeout -- replaced with an
  explicit `watchdog_fired` flag recorded by the blocker itself; added the
  two missing full-service C3 cases (admission-timeout and
  deferred-COMMIT-under-contention) to reach six total service-level cases
  (control, admission, claim, fence, settle, deferred COMMIT); corrected the
  rolling-control wording (see below) and distinguished two different test
  selections that both happened to total 37 passing tests; made the meter
  fixture's cleanup resilient to a raising `meter_pool.close()`.

The rolling control referenced elsewhere in this repository is a window over
the **last 20 assignments**, not a day count and not a cost-allocation
mechanism; actual OpenAI charges are UNKNOWN from this repository and no
canonical admission record or live-activation inference is available here to
inspect, independently of any of the above.

## Correction (2026-09-14 21:26 UTC, Claude Sonnet 5 high), real PostgreSQL evidence

Real independent Linux CI (2928 tests, 3 failures, 1 error, 15 skips) and a
real, host-controlled disposable PostgreSQL 17.11 instance in this
environment both showed the 20:52 UTC pass's claim/fence/settle timing fix
was insufficient, and found a genuine test bug and a real connection leak.
All three are root-cause fixed here, confirmed against the real PostgreSQL
17.11 instance (not simulated):

- **Cleanup margin (the CI timing failures)**: `claim`/`fence`/`settle`
  landed at 3.0003s/3.0009s/3.0004s on Linux CI and 3.000s/3.016s/3.016s
  locally -- consistently at or just past the strict-`<3s` boundary, not
  comfortably under it. The 20:52 UTC design computed the cleanup marker's
  SQL-level `lock_timeout` to fill the *entire* remaining
  deadline-plus-one-second-grace window, leaving no margin for the
  connection handshake before that timeout starts or the implicit `ROLLBACK`
  round trip after it fires -- neither of which `lock_timeout` itself
  bounds. Rather than further shrinking the one-second grace constant (a
  numeric tweak that would not address the missing margin itself and would
  only shift where the same problem resurfaces), a new
  `CLEANUP_OVERHEAD_MARGIN_SECONDS` (0.25s, comfortably above the observed
  0.3-16ms overshoot) is now reserved out of the grace *before* it is handed
  to `lock_timeout`, applied uniformly in `PostgresValidationStore._cleanup_remaining`
  for both the Responses lane's absolute-deadline-anchored allowance and the
  native lane's fresh-anchored one. No watchdog, completion, state or
  capture assertion was weakened; the 3-second test watchdogs are unchanged.
- **Meter canonicalization mismatch**: `test_responses_validation_bridge_runs_configured_factory_reservation_and_capture`
  raised `ValidationError('Responses validation canary work_packet_digest
  mismatch')`. `PostgresValidationStore.admit` builds `admission["packet"]`
  with `packet_contracts.canonical_json_bytes` (RFC 8785), but the test
  independently computed its own work-packet digest -- which seeds the
  enrollment's `work_packet_digest` and therefore the route's canary pin --
  with this module's `entitlement_contracts.canonical_json_bytes`, used
  correctly elsewhere in the same file for entitlement/receipt signing. The
  two canonicalizers disagree on number formatting (`budget_usd` as `1.0`
  vs `1`), so the digests never matched. Fixed by computing that one digest
  with `packet_contracts.canonical_json_bytes` instead, matching what
  admission actually produces; entitlement signing elsewhere in the file is
  untouched, and no canary digest check or production metering control was
  weakened.
- **Connection leak (ResourceWarning)**: `psycopg.Connection.__exit__`
  commits on a clean exit but does not guard that commit the way it guards a
  rollback on the exception path -- so when `settle()`'s implicit COMMIT
  itself blocks on the `qa_settle_commit_gate` deferred trigger and then
  times out, the resulting exception propagates out of `__exit__` *before*
  reaching its own `close()` call, leaking the connection until garbage
  collection warned about it. Confirmed by reading the installed psycopg
  source (`connection.py`), not by suppressing the warning. Fixed by making
  `PostgresValidationStore._connect_bounded` return a small guarded context
  manager that still closes the connection in a `finally` even when the
  wrapped `with` block's own exit (its commit or rollback) raises; this is
  the single call site every store method already uses, so the fix applies
  uniformly without changing any method body.

**Final status**: with all of the above applied, the host-controlled
disposable PostgreSQL 17.11 wrapper ran the full committed PostgreSQL suite
(26 tests in `PostgresValidationTests`, including all six full-service C3
cases) plus the previously-failing configured-factory meter test plus the 37
non-database tests -- **64 tests, 0 failures, 0 errors, 0 resource
warnings**, all strictly under the 2s-budget-plus-1s-grace boundary. This is
real evidence from an actual server, not a claim -- but it is still this
correction's own author-run evidence in a task-local, disposable cluster, not
the independent, unchanged C3 harness or the project's real CI, and it does
not constitute self-acceptance or independent review.

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
  reading -- the smaller of the shared operation deadline's remaining time
  and a fixed **one**-second grace, minus a reserved `CLEANUP_OVERHEAD_MARGIN_SECONDS`
  (0.25s) that is never handed to the SQL-level statement/lock timeout itself
  (see the 21:26 UTC correction above: without that margin, real PostgreSQL
  evidence showed completion landing at or just past the documented
  boundary, not strictly before it). The anchored cleanup deadline then only
  decreases, so per-statement refreshes inside the cleanup, including the
  refresh immediately before the implicit COMMIT, can never restart or
  extend the grace after the primary deadline has raised or expired. Both
  service call sites are bounded: the native lane uses the fixed anchored
  grace, and the Responses lane tightens it to the shared deadline's
  remainder. If the bounded marker cannot be written, the attempt stays
  exactly as the aborted operation left it (admitted or running), remains
  ineligible for redispatch, and stays reconcilable; no second dispatch is
  funded and no rollback is treated as reversing provider work.
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
  ledger. The fixture creates that isolated primary schema and runs
  `team_ddl` against it before creating the attempt table and calling
  `register()` (the authority's constructor only validates the schema
  identifier and performs no DDL of its own), and computes its work-packet
  digest with the same `packet_contracts.canonical_json_bytes` admission
  actually uses rather than this module's `entitlement_contracts` one (see
  the 21:26 UTC correction above). Production inventory guards, funding
  checks and daily caps are unchanged; the bridge performs the single
  authoritative reservation (one spend row at the signed amount) and exactly
  one usage row is asserted, now confirmed against a real PostgreSQL 17.11
  instance.
- Timing boundary: the six full-service PostgreSQL deadline cases keep the
  original acceptance boundary -- a two-second packet budget plus one second
  of scheduler grace, with completion strictly before each test's own
  3-second blocker watchdog. The anchored cleanup allowance is that same
  one-second grace minus the reserved overhead margin above, not a second,
  additional window stacked on top of it. Confirmed against a real
  PostgreSQL 17.11 instance as of the 21:26 UTC correction above; see that
  section for the exact counts.
- Settling enters its deadline before capture serialization: an exhausted
  budget refuses as a deadline, and a malformed capture fails independently
  as a TypeError, so neither can masquerade as the other.

See the "Final status" paragraph in the 21:26 UTC correction above for the
exact, real-PostgreSQL-confirmed test counts as of this revision, and the
"Remaining limitations" section below for what is still outstanding.

## Remaining limitations

- Host/application builders must inject `openai_responses_route` with a real
  enrolled meter registry; this patch does not change production host
  configuration or enable paid runtime switches.
- Publication of already-captured evidence still uses the staged
  `PublishProviderEvidence` path from prior work; this patch only adds the
  capture/validation route that can produce qualifying Responses evidence.
- The anchored cleanup fix and the reordered claim/fence deadline tightening
  are source-level corrections confirmed, in this correction's own
  host-controlled disposable PostgreSQL 17.11 run, against a real server:
  the six full-service C3 cases (control, admission, claim, fence, settle,
  deferred COMMIT) plus the configured-factory meter test plus the 37
  non-database tests all pass with 0 resource warnings. This is real
  evidence, not a claim -- but it remains this correction's own author-run
  evidence in a task-local, disposable cluster, not the project's real CI
  and not the independent, unchanged C3 harness.
- Failed-meter retention is not established: if the meter transaction cannot
  finish, only provider-side facts remain for reconciliation.
- Actual OpenAI charges are unknown from this repository; no authoritative
  billing or admission history is available here to inspect. The rolling
  control referenced elsewhere in this repository is over the last 20
  assignments, not a day count or a cost allocation mechanism, and this
  correction makes no claim about either.
- Full CI, the independent unchanged C3 harness on the project's own
  infrastructure, and a distinct non-author reviewer remain required; this
  correction does not accept itself and is not an independent review.

<!-- [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]] -->
<!-- [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:41 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:52 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 21:26 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:02 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:57 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 04:42 UTC | END]] -->
