<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:07 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | BEGIN]] -->
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
every attempt of that roster. It is re-proved at four points, in this order:
before admission opens a transaction, **after** admission's own locks and
before its COMMIT (`revalidate_selection`), immediately before provider I/O,
and inside the transport that resolves the meter. A changed packet, a writer
takeover, a replaced enrollment registry, or an authority that no longer
resolves to the checked one is a different selection and fails closed as a
`WorkloadAdmissionHold` - which `execute_ready` maps to
`OWNER_DECISION_REQUIRED` - with nothing admitted. A selection this process no
longer holds (a restart between reservation and dispatch) also fails closed; it
is never re-derived.

Two of those points matter because waits happen between them. The ledger
repeats the authority comparison inside the authoritative locked admission via
`expected_authority_digest`, so a valid admission object for some other
authority is refused before any monetary, workload or STARTED row is written.
`revalidate_selection` runs after the admission transaction has finished
waiting on its locks and before the STARTED insert. It is handed that
transaction's own connection, so it re-runs the **authority selection itself** -
`select_budget_authority`/`select_team_authority`, the same uniqueness,
signature, revocation, grant/packet binding and UTC-day rules readiness applied
- without acquiring a second pooled connection while those locks are held. The
read-only spend ceilings are deliberately not repeated there: this attempt's
own reservation is already inserted, and counting it twice would refuse a
correctly funded attempt. The writer lease is re-proved at the same point, and
the enrollment document is compared again. A competing authority registered
while admission waits therefore rolls the monetary, workload and STARTED writes
back together.

`OpenAIResponsesPacketTransport.send` then compares the enrollment it actually
resolved against the carried `enrollment_digest`, so a registry substituted
after the caller's own comparison - or a meter lost entirely - cannot reach the
provider.

## Authority at the real generation boundary

The Responses transport counts input tokens before it generates anything. That
count is itself a provider request: it is bounded by the same carried deadline
as the rest of the attempt (`remaining_seconds()` is re-evaluated for it and
the whole call runs under `asyncio.timeout`), but within that bound the
provider can make the caller wait, and every check above ran before that wait. `ProviderAdapterContext.authority_recheck`
- created by the live coordinator, never by a transport - is therefore called
immediately before the generation request and re-reads the durable rows: the
wave's `kill_switch` (what `coordinator.cancel` engages), this task's
`langgraph` writer lease, owner token and fencing generation, this assignment's
ACTIVE reservation binding, and this attempt's own latest event. A cancellation,
a writer takeover, an expired lease, a changed plan binding or an already
reconciled attempt during the count stops the request. The refusal is a
`WorkloadAdmissionHold`, so the roster propagates it as an owner decision
instead of an ambiguous paid dispatch: no generation request is made, no
automatic repeat follows, and the admitted rows, slot and any recorded usage
are preserved. A transport with no such callback holds no authority of its own
and invents none.

A `WorkloadAdmissionHold` propagates out of `_execute_three_attempt_roster`
instead of being classified. It arises at two places: before `adapter.dispatch`
is entered at all, and - since this revision - from `authority_recheck` inside
the Responses path, after the input-token count and strictly before the
generation request. In both cases no generated result exists to be ambiguous
about, so the hold is an owner decision and the admitted attempt keeps its
rows, slot and evidence. It is never raised after a generation request is sent.

## Cancellation

`director_primary_parallel_batch.kill_switch` - the existing authority
`engage_kill_switch` sets and `reserve_ready` already honours - is now read at
both remaining authority points. Atomic admission re-reads it under the same
row lock it takes, so a wave cancelled while an admission is in flight gets no
attempt, spend or workload row (`parallel_wave_cancelled_before_admission`).
Dispatch re-reads it immediately before I/O, so an attempt already admitted
makes no provider call (`wave_cancelled_before_dispatch`) while keeping exactly
the rows, slot and evidence it was admitted with. No new table, flag or
approval path was added.

## The deadline

`execute_ready` reads the bound the dispatcher publishes (`readiness_deadline`)
*before* `reserve_ready` acquires a connection or takes a lock, so pool
acquisition, the writer row, the batch advisory lock and the per-assignment
`FOR UPDATE` waits are inside it. `execute_parallel_ready` reads the same bound
once and passes it to its own writer acquisition, so the coordinator's work
ahead of `execute_ready` is inside the bound too. Per-assignment readiness then
establishes the selection's own deadline, clamped by both the packet's timeout
and the configured transport ceiling, and every later step spends that same
value: `live_provider_admissions`, `PrimaryProviderBudgetRouting.admission`,
`PrimaryTeamRouting.grant_for`/`adapter`, `PrimaryTaskCoordinator._task_writer`
(through `current_`/`renew_`/`acquire_task_writer`),
`record_work_packet_artifact`, the pre-I/O cancellation read, and
`ProviderAdapterContext.deadline` itself. `send` narrows a carried deadline to
`min(carried, this request's own window)`, never widens it. The shared helpers
refuse an already-expired bound (`DeadlineExpired`) rather than converting it
into a one-millisecond timeout.

`bind_transaction_deadline` (`langgraph_postgres.py`) sets `statement_timeout`,
`lock_timeout` **and** PostgreSQL 17's `transaction_timeout`, all `LOCAL`.
Per-statement timeouts alone do not bound a transaction: PostgreSQL applies
`statement_timeout` separately to each statement and again to COMMIT, so work a
deferred constraint trigger performs during COMMIT gets a fresh window.
`transaction_timeout` bounds the whole transaction including that commit-time
work. When it fires PostgreSQL aborts the transaction and terminates the
session, and the pool discards that connection - the test log shows exactly
that discard. Every unbounded caller is unchanged: the parameter is optional
throughout.

Binding happens **inside an explicit transaction**, never beside one. These
pools are autocommit, so `SET LOCAL` issued outside a transaction block lasts
only for the statement that sets it: an independent probe on 17.11 read both
timeouts back as `0` immediately after binding, and a 0.35s sleep then
completed against a 0.20s bound. A pool-acquisition timeout does not bound the
SQL that follows either. `bounded_transaction` therefore acquires, opens one
transaction and binds within it; the read paths that spend a deadline -
`read_primary_task_projection`, `current_task_writer`, `aggregate`, the wave
cancellation read and the generation-boundary authority read - all use it.

Binding **tightens only, and tightening is enforced**. PostgreSQL 17's
`assign_transaction_timeout` hook enables the timer only while it is inactive,
so assigning a smaller value to a running transaction updates the setting and
leaves the original, longer timer in force: an independent probe on 17.11 saw
`SHOW` report 234ms while the transaction still committed a durable row at
0.312s against a 0.25s bound. The binder therefore records the earliest
absolute deadline in a transaction-local setting, takes the minimum of that and
any later bound, and - when the effective bound actually becomes earlier -
assigns zero to deactivate the timer before re-arming it with the tightened
window. A later, larger bound is observed and discarded, so no caller can
extend the earliest deadline in force, and a reused pooled connection starts
its next transaction clean because the recorded value reverts with the
transaction.

Classification stays conservative. Only a bound already expired *before* any
I/O raises the narrow pre-I/O hold. Anything the server timer aborts
mid-transaction surfaces as an uncaught failure, which stays AMBIGUOUS, because
a client cannot distinguish an aborted commit from an unacknowledged one.

## Completion

Execution ending does not make recording what already happened optional.
`COMPLETION_ALLOWANCE_SECONDS` (`primary_parallel.py`) defines one bounded
window for that recording, and `completion_deadline(deadline)` derives the
single absolute cutoff from the operation's own deadline. Every call in one
operation computes the identical instant, so no call can renew it, and it
authorises no execution, retry or eligibility.

Two things the checkpointer does are not the pool's to bound. `_cursor` takes
the saver's own mutex *before* it asks for a connection, so that wait is
bounded at the same object: the saver's `lock` is wrapped to refuse waiting
past the published cutoff, and a caller refused there has taken no connection
and written nothing. Once the connection is handed over it already carries one
open transaction with `transaction_timeout` armed, so the cumulative blob and
checkpoint statements, the pipeline synchronisation and the COMMIT share one
absolute deadline instead of a series of independent per-statement allowances -
and an aborted checkpoint leaves nothing durable to contradict the refusal.

That cutoff reaches the foreground writes the wave actually performs:
`record_attempt` through `PrimaryTaskCoordinator.record_provider_attempt` and
its writer resolution, the successful roster's `record_result_packet_artifact`
including its advisory-lock wait, `execute_ready`'s own `record_result` while
`executor.map` is still waiting, and the coordinator's `aggregate`,
`record_parallel_aggregate` and outcome projection write. The checkpointer
acquires its own connections and mutex, so `record_parallel_aggregate`
publishes the cutoff for its context and the saver and pool apply it to the
snapshot read, the state update and the outcome read alike. No thread, queue
or background writer is introduced: when the call returns, the bound is gone.

On failure the reservation and any recorded usage are retained, the attempt
stays STARTED for reconciliation, and unconfirmed persistence is reported as a
failure rather than a completed wave. Nothing is refunded or replayed.

## Late accounting

`PrimaryProviderBudgetAuthority.record_usage` verifies the receipt HMAC, the
admitted spend/authority/provider binding, project and response locks, and
duplicate/identical/conflicting replay. It does not require the enrollment to
be current; that refusal lived only in `EnrolledResponseMeter._verify`.

`settle` takes `sent_at`, the instant the request was actually sent. That
instant is sampled inside `send` immediately before the generation request -
after input-token counting, not before it - and `meter.verify_current()` is
re-run at the same point, so counting that outlives the signed window stops the
send instead of pricing it. The signed tariff must have covered that instant. A
request legitimately admitted and sent does not stop having consumed real
tokens because the window closed, or this attempt's execution budget ran out,
while the provider was answering.

Settlement is therefore bounded by one absolute `accounting_deadline`, derived
once from the operation's own deadline plus `ACCOUNTING_ALLOWANCE_SECONDS`, and
it replaces the execution callback inside `_connection` rather than widening
it: that callback raises the moment execution expires, which is exactly when
the fact most needs recording. The same value on every call means replay cannot
extend it, and it authorises no provider work and no eligibility - an expired
execution still fails. Anything the provider actually completed is retained as
soon as it is parsed, so a timeout while unwinding the async contexts after
parsing accounts for the reply and *then* refuses the attempt. Without
`sent_at` the previous current-validity gate applies unchanged, and `prepare` -
the gate that authorises new execution - is untouched. Signature,
provider-confirmed account association, model/tier/creation freshness, token
accounting and every replay check are unchanged.

## Evidence

All author-run, through the reviewed wrapper against a real disposable Docker
PostgreSQL 17 lane, over the *selected* modules in scope - not repository-wide
discovery. Paid execution stays false and every provider reply is synthetic
(`httpx.MockTransport`, no network). The Director runs independent QA and
arranges exact-head review; none of this is acceptance.

Every figure below is a run that completed. The previous revision's final
`--group full --warnings` and `--group full` attempts were cut off by the
author window and produced no result; those receipts stand as they are and
nothing here is claimed from them.

- `--group composition` (`tests/test_responses_team_readiness_composition.py`):
  42 tests, all passing, this revision.
- `--group admission` (`test_primary_parallel`, `test_antigravity_team`,
  `test_primary_coordinator`, `test_langgraph_postgres`): 182 tests, all
  passing, this revision, including every PostgreSQL deadline case below.
- `--group full` (all ten selected modules, including
  `tests/test_langgraph_primary.py`): **412 tests, all passing**, this
  revision, in 262s with zero resource warnings and a verified lane teardown.
- `--group startup`: 1 test, passing, this revision.

Only this document changed after that full run; no source or test byte did.

`tests/test_langgraph_postgres.py::TransactionDeadlineBindingTests` proves the
timer by wall clock, durable rows and the reported SQLSTATE, never by `SHOW`
alone: a bound tightened from 1.0s to 0.25s ends two cumulative sleeps with
25P04 and commits nothing; a later 5.0s bound does not extend an earlier 0.25s
one; a deferred constraint trigger sleeping 0.5s during COMMIT is cut off by a
bound tightened to 0.3s, again 25P04; an already-expired bound is refused
before and after an earlier one; the same connection runs a later transaction
0.6s past the previous transaction's deadline and commits; and the real
projection and writer readers, under an `ACCESS EXCLUSIVE` table lock, end at
their own bound instead of silently losing it.

`CheckpointOperationDeadlineTests` exercises the installed saver itself: a
second thread holds the saver's mutex while the cutoff expires, the caller is
refused in time with nothing written and the holder releases and joins
cleanly; and with `BEFORE INSERT` triggers delaying the blob and checkpoint
writes - each well inside any per-statement allowance, their sum outside the
cutoff - the operation aborts and no checkpoint row survives.

`tests/test_primary_default.py::DefaultPrimaryApplicationIntegrationTests`
covers the foreground completion path through the real coordinator and the real
graph: the contended row is locked from inside the dispatcher, so readiness and
admission are already done. Result settlement (`record_result` while
`executor.map` waits), the outcome projection write and the checkpointer's own
storage each end inside the completion allowance instead of blocking, with the
unsettled slot left ACTIVE, the settled ones intact, and no wave reported as
completed when its checkpoint could not be confirmed.

The composition module covers, with a real `PrimaryTaskCoordinator`, real
`PostgresParallelDispatch` admission, real signed budgets/grants/enrollments
and the real transport:

- **Deferred COMMIT** - a BEFORE INSERT trigger and a DEFERRABLE INITIALLY
  DEFERRED constraint trigger each sleeping 0.3s against a 0.5s bound
  (previously committing at 0.656s) is refused with zero attempt, spend and
  workload rows.
- **Real lock witnesses** - a second real connection holds the assignment row,
  the batch row or the writer row; readiness, admission and the coordinator's
  writer renewal each end inside their bound with nothing admitted.
- **Changed selection** - dependency-enriched packet, writer takeover, added
  signed budget row, an authority other than the checked one at the locked
  admission, a dispatch with no live selection, an enrollment replaced *during*
  the admission lock wait (rolled back), an enrollment replaced after admission
  (no provider call), and a registry substituted at the transport itself.
- **Cancellation** - engaged before admission (nothing admitted, no HTTP),
  engaged between admission and I/O (no HTTP, admitted rows and slot intact),
  and engaged *while the input-token count is in flight* (the generation
  request is never made).
- **Authority during the count** - a legitimate writer takeover and a plain
  lease expiry during the count response each stop the generation request,
  leaving one STARTED attempt, its reservation and slot, and no metering.
- **Competing authority** - a second valid signed budget registered while
  admission is blocked on the batch row rolls every write of that admission
  back.
- **Expiring writer lease** - a lease set to run out during a genuine
  admission lock wait is refused at the final fence, against the real clock
  rather than this transaction's start time, with nothing admitted and no
  provider call.
- **Bounded completion** - with the writer row held after the provider has
  answered, the attempt's outcome write ends inside the completion allowance;
  the attempt stays STARTED and the authenticated usage already recorded for it
  is retained.
- **Late accounting** - the real transport reaching the real meter for a
  complete reply whose unwinding timed out (usage recorded, attempt still
  refused); an enrollment expiring between counting and sending (generation
  request never made, nothing metered, reservation retained); an expired
  enrollment recording the usage it authorised while `prepare` and the
  unanchored gate still refuse; idempotent replay, refused conflicting replay,
  refused unconfirmed account association, refused forged signature, a spent
  accounting cutoff writing nothing, and project-lock contention bounding
  settlement with the reservation held.
- **Concurrency** - two admitted attempts sharing one registry cannot exchange
  enrollment, credential or account facts, and one provider response cannot be
  claimed by two attempts; two waves admitting simultaneously against the same
  signed project day leave exactly one reservation, within the signed ceiling,
  and no STARTED attempt without monetary accounting.
- **End to end** - real `reserve_ready`, admission, dispatch and settlement,
  with the predicted attempt identity equal to the admitted one.

## Acceptance map

| Work-order item | State |
| --- | --- |
| Readiness validates both branches before admission | Done, exercised |
| Checked selection carried into atomic admission | Done, exercised |
| One deadline spanning readiness, admission and I/O | Done, exercised |
| A tightened PostgreSQL timer actually tightens; none extends it | Done, exercised |
| Bounds survive into the statements they were taken for | Done, exercised |
| The saver's mutex and cumulative checkpoint work are bounded | Done, exercised |
| Writer lease proved against the real clock at the final fence | Done, exercised |
| Full authority re-selected after the admission waits | Done, exercised |
| Cancellation honoured at admission, pre-I/O and at generation | Done, exercised |
| Writer/plan binding re-read at the real generation boundary | Done, exercised |
| Enrollment pinned across every wait and at the transport | Done, exercised |
| Late authenticated usage recorded, eligibility unchanged | Done, exercised |
| Foreground completion, outcome and checkpoint writes bounded | Done, exercised |
| Concurrency: no cross-request facts, no overspend | Done, exercised |
| Host full-repository QA and exact-head source/security review | Not done - Director |

## Limitations

- A selection is in-process only; a restart between reservation and dispatch
  fails closed rather than resuming. No migration or schema change was made.
- The enrollment registry loader is **startup-only**. Replacing the file on
  disk changes nothing in a running process; no live file-change detection is
  implemented or claimed. The substitution cases above replace the in-memory
  registry, which is what an in-process change actually looks like.
- An authorization refusal raised *after* input-token counting (the expiring
  enrollment case) is reported by the transport as an ambiguous provider
  outcome, because it is raised inside the same guarded block as real transport
  failures. No generation request is made and nothing is metered, so this is
  conservative rather than wrong, but the reason code is less precise than the
  pre-I/O holds.
- `transaction_timeout` requires PostgreSQL 17; on an older server the helper
  applies statement and lock timeouts only, which do not bound commit-time
  work. The sanctioned lane, Compose and CI all pin 17.
- A checkpoint aborted by its own deadline is reported as a failure and
  reconciled by the owner, not retried here. When the transaction timer fires
  during pipeline synchronisation, PostgreSQL terminates the session, so the
  client sees a closed connection rather than SQLSTATE 25P04; the tests accept
  exactly those two shapes and nothing else.
- Provider-outcome-unknown classification is still asserted through existing
  tests rather than a dedicated case in this module.
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
- **2026-09-21 11:19 UTC**: an independent probe proved admission could still
  commit after its deadline; replaced per-statement refreshes with a
  transaction-level bound, moved the bound ahead of readiness acquisition and
  the authority/writer lookups, replaced the deadline map with a full immutable
  selection, and retained late authenticated usage through the existing receipt
  contract - a new table is not required, correcting an earlier claim here.
- **2026-09-21 12:05 UTC**: review found four caller-path gaps
  the helper-level tests had missed. Closed them in the actual call path: the
  accounting allowance now reaches the transport's own `settle` with `sent_at`
  sampled at the generation request and completed replies retained through a
  timed-out unwinding; the bound now spans the coordinator's own writer work,
  the artifact write and the team adapter; cancellation is read inside atomic
  admission and again before I/O; the enrollment is re-proved after admission's
  locks and at the transport. A pre-I/O hold is no longer recorded as an
  ambiguous paid dispatch. Concurrency and overspend cases are now exercised,
  so the earlier "not exercised" limitation is removed.
- **2026-09-21 12:57 UTC (this revision)**: an independent PostgreSQL 17.11
  probe showed that assigning a smaller `transaction_timeout` does not tighten
  a running timer, so the binder now deactivates and re-arms it and records the
  earliest absolute deadline per transaction. Admission re-selects the full
  authority - budget and team - on its own connection after its lock waits, so
  the documented uniqueness gap is closed rather than described. The live
  coordinator's `authority_recheck` re-reads cancellation, the writer lease and
  the plan binding at the real generation boundary, after input-token counting.
  One nonrenewable completion allowance now reaches the attempt outcome, result
  artifact, slot settlement, aggregate, outcome projection and checkpoint
  writes; the checkpointer's own pool applies it for the duration of that call.
- **2026-09-21 14:07 UTC (this revision)**: an independent probe reproduced the
  helper's lifetime defect - `SET LOCAL` outside a transaction on an autocommit
  connection is gone before the next statement - so every bounded read path now
  binds inside one explicit transaction. The saver's own mutex wait and its
  cumulative blob/checkpoint/synchronisation/commit work are bounded at the
  seams that actually wait. The writer lease is proved against
  `clock_timestamp()` at the final admission fence, because `now()` is
  transaction-start time and a lock wait can outlast a lease. Timeout
  witnesses now identify the SQLSTATE or the session termination, and this
  document's outer contribution records and evidence claims are corrected.
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:18 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:07 UTC | END]] -->
