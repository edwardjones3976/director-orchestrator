<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:18 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | BEGIN]] -->
# Responses and team readiness composition

`DefaultPrimaryActivation`'s constructor-created `preflight_parallel_assignment`
callback is the readiness gate `PostgresParallelDispatch.reserve_ready` calls,
under its own locked transaction, for every reservation-eligible assignment in
a wave before that wave's ACTIVE rows exist. It predicts eligibility only: it
performs no HTTP, provider launch, spend/usage insert, workload admission or
STARTED event. A hold records `paid_setup_required` and leaves the assignment
`PENDING`.

## Readiness

`check_budget_setup`/`check_team_setup` (`primary_paid_preflight.py`) validate
the signed budget or grant - signature, UTC day, ceilings - on the caller's own
connection. For Responses with an enrolled meter, the real
`OpenAIResponsesPacketTransport.preflight` additionally runs
`EnrolledResponseMeter.preflight_binding`: signature, validity interval and
exact enrollment/context binding, with no HTTP, no input-token counting and no
database reservation. `prepare()` keeps that reservation check for actual
dispatch. Neither branch probes `query_factory`, `worker_factory`,
`TeamAuthority.authorize` or `admissions`. Readiness canonicalizes the packet
with `packet_contracts.canonical_json_bytes` (RFC 8785) - the same bytes real
dispatch sends and a signed enrollment's `work_packet_digest` was computed
against.

## The selection

Readiness records one `ReadinessSelection` per assignment: provider, exact
packet digest, roster, signed authority digest, writer fencing generation,
enrolled document digest, authority day, and one absolute deadline shared by
every attempt of that roster. Each attempt re-resolves it and re-checks the
mutable parts before admission, and again immediately before I/O. A changed
packet, a writer takeover, a replaced enrollment registry, or an authority
that no longer resolves to the checked one is a different selection and fails
closed as a `WorkloadAdmissionHold` - which `execute_ready` already maps to
`OWNER_DECISION_REQUIRED` - with nothing admitted. A selection this process no
longer holds (a restart between reservation and dispatch) also fails closed;
it is never re-derived. The ledger repeats the comparison inside the
authoritative locked admission via `expected_authority_digest`, so even a
valid admission object for some other authority is refused before any
monetary, workload or STARTED row is written.

## The deadline

`execute_ready` reads the bound the dispatcher publishes (`readiness_deadline`)
*before* `reserve_ready` acquires a connection or takes a lock, so pool
acquisition, the writer row, the batch advisory lock and the per-assignment
`FOR UPDATE` waits are inside it. Per-assignment readiness then establishes the
selection's own deadline, clamped by both the packet's timeout and the
configured transport ceiling. `live_provider_admissions`,
`PrimaryProviderBudgetRouting.admission`, `PrimaryTeamRouting.grant_for` and
`PrimaryTaskCoordinator._task_writer` (through
`current_`/`renew_`/`acquire_task_writer`) all accept and apply that same
bound, so authority resolution and lease work happen inside it rather than
ahead of it. `OpenAIResponsesPacketTransport.send` narrows a carried deadline
to `min(carried, this request's own window)`, never widens it.

`bind_transaction_deadline` (`langgraph_postgres.py`) sets `statement_timeout`,
`lock_timeout` **and** PostgreSQL 17's `transaction_timeout`, all `LOCAL`.
Per-statement timeouts alone do not bound a transaction: PostgreSQL applies
`statement_timeout` separately to each statement and again to COMMIT, so work a
deferred constraint trigger performs during COMMIT gets a fresh window.
`transaction_timeout` bounds the whole transaction including that commit-time
work; its assign hook arms once and does not re-arm, so later calls can observe
the bound but never extend it. When it fires PostgreSQL aborts the transaction
and terminates the session, and the pool discards that connection - the test
log shows exactly that discard. Every unbounded caller is unchanged: the
parameter is optional throughout.

Classification is unchanged and deliberately conservative. Only a bound already
expired *before* any I/O raises the narrow pre-I/O hold. Anything the server
timer aborts mid-transaction surfaces as an uncaught failure, which stays
AMBIGUOUS, because a client cannot distinguish an aborted commit from an
unacknowledged one.

## Late accounting

`PrimaryProviderBudgetAuthority.record_usage` verifies the receipt HMAC, the
admitted spend/authority/provider binding, project and response locks, and
duplicate/identical/conflicting replay. It does not require the enrollment to
be current; that refusal lived only in `EnrolledResponseMeter._verify`.

`settle` now takes `sent_at`, the instant the request was actually sent - read
from the meter's own clock immediately after `prepare` had required a current
enrollment. The signed tariff must have covered *that* instant. A request that
was legitimately admitted and sent does not stop having consumed real tokens
because the window closed while the provider was answering, so its usage is
recorded through the existing receipt path, with one bounded
`ACCOUNTING_ALLOWANCE_SECONDS` window for writing the receipt when the
execution budget is already spent. That allowance buys no provider work and no
eligibility: an expired execution still fails. Without `sent_at` the previous
current-validity gate applies unchanged, and `prepare` - the gate that
authorises new execution - is untouched. Signature, provider-confirmed account
association, model/tier/creation freshness, token accounting and every replay
check are unchanged.

`send` no longer re-checks the deadline between receiving a complete provider
response and returning it; expiry is decided after that response has been
captured and its usage accounted, so a deadline can no longer discard an
authenticated fact that already cost tokens.

## Evidence

All author-run, against a real disposable Docker PostgreSQL 17 lane through the
reviewed wrapper on the *selected targeted modules*, not repository-wide
discovery. These are the author's own runs; the Director runs independent QA
and arranges exact-head review.

- `--group admission` (`test_primary_parallel`, `test_antigravity_team`,
  `test_primary_coordinator`, `test_langgraph_postgres`): 174 passed.
- `--group meter` (`test_response_meter`): 15 passed.
- `--group readiness`: 82 passed, including every case below.
- `--group full` (all nine selected modules): 333 tests, 332 passing, with one
  pre-existing test - `test_live_activation_composes_a_ready_typed_effect_executor`
  - erroring because it stubs `live_provider_admissions` to `{}`, so no
  monetary authority object exists to compare the selection against. That is
  fixed by comparing only when an attempt actually carries authority (an
  attempt carrying none is already refused by the ledger whenever it has
  budget limits), in both `primary_default.py` and `primary_parallel.py`.
  **The eight-run QA budget was exhausted before that fix could be re-run, so
  it is source-verified only.** The preceding run is the last executed
  evidence; the Director's independent QA should confirm 333/333.

`tests/test_responses_team_readiness_composition.py` covers:

- **Deferred COMMIT** - `test_insert_and_deferred_commit_share_one_total_deadline`
  reproduces the independently observed defect (a BEFORE INSERT trigger and a
  DEFERRABLE INITIALLY DEFERRED constraint trigger each sleeping 0.3s against a
  0.5s bound, previously committing at 0.656s) and asserts refusal with zero
  attempt, spend and workload rows.
- **Real lock witnesses** - a second real connection holds the assignment row,
  the batch row, or the writer row; admission, readiness and the coordinator's
  writer renewal each end inside their bound with nothing admitted.
- **Changed selection** - dependency-enriched packet, writer takeover, added
  signed budget row, an authority other than the checked one at the locked
  admission, and a dispatch with no live selection.
- **Late accounting** - an expired enrollment still records the usage it
  authorised while `prepare` and the unanchored gate still refuse; exact replay
  is idempotent, conflicting replay is refused with the original facts intact,
  an unconfirmed account association is refused before any write, and project
  lock contention bounds settlement while the reservation stays held.
- **End to end** - the real `PrimaryTaskCoordinator`, real transport and real
  signed registry through real `reserve_ready`, admission, dispatch and
  settlement with synthetic HTTP only.

## Limitations

- A selection is in-process only; a restart between reservation and dispatch
  fails closed rather than resuming. No migration or schema change was made.
- `transaction_timeout` requires PostgreSQL 17; on an older server the helper
  applies statement and lock timeouts only, which do not bound commit-time
  work. The sanctioned lane, Compose and CI all pin 17.
- Work order acceptance groups are not all complete. Concurrency and replay is
  covered for usage receipts and duplicate/ambiguous attempts, but two tasks or
  meters exchanging credentials, and competing wave budgets racing to overspend,
  are not exercised here. Provider-outcome-unknown classification is asserted
  through existing tests rather than a new dedicated case.
- Full CI, an independent non-author security and source review, and an
  exact-head reviewer verdict remain required. Nothing here is a source
  acceptance, merge or activation.

## History

- **2026-09-14 22:29 UTC**: initial composition (team branch's unconditional
  hold replaced with `check_team_setup`).
- **2026-09-21 09:43-10:18 UTC**: budget-day freshness, the transport
  `preflight`, first real-PostgreSQL composition tests, and an
  `entitlement_contracts`-vs-`packet_contracts` canonicalization fix a real
  signed-enrollment test found.
- **2026-09-21 10:45 UTC**: one per-assignment deadline carried across retries,
  the real `PrimaryTaskCoordinator` in the decisive test, and a real
  lock-contention test. An earlier attempt-identity finding was retracted by
  review; the existing admission-time override already unifies that digest.
- **2026-09-21 11:19 UTC (this revision)**: an independent probe proved
  admission could still commit after its deadline; replaced per-statement
  refreshes with a transaction-level bound, moved the bound ahead of readiness
  acquisition and the authority/writer lookups, replaced the deadline map with
  a full immutable selection compared at locked admission and before I/O, and
  retained late authenticated usage through the existing receipt contract - a
  new table is not required, correcting this document's earlier claim.
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:18 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]] -->
