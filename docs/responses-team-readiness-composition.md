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
waiting on its locks and before the STARTED insert, so an enrollment replaced
during that wait rolls the monetary, workload and STARTED writes back together.
`OpenAIResponsesPacketTransport.send` then compares the enrollment it actually
resolved against the carried `enrollment_digest`, so a registry substituted
after the caller's own comparison - or a meter lost entirely - cannot reach the
provider.

A `WorkloadAdmissionHold` raised at the pre-I/O checks propagates out of
`_execute_three_attempt_roster` instead of being classified: it is raised
strictly before `adapter.dispatch` is entered, so there is no provider request
to be ambiguous about. The admitted attempt keeps its rows, slot and evidence.

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
work; its assign hook arms once and does not re-arm, so later calls can observe
the bound but never extend it. When it fires PostgreSQL aborts the transaction
and terminates the session, and the pool discards that connection - the test
log shows exactly that discard. Every unbounded caller is unchanged: the
parameter is optional throughout.

Classification stays conservative. Only a bound already expired *before* any
I/O raises the narrow pre-I/O hold. Anything the server timer aborts
mid-transaction surfaces as an uncaught failure, which stays AMBIGUOUS, because
a client cannot distinguish an aborted commit from an unacknowledged one.

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

- `--group full` (all nine selected modules): **342 tests, all passing**, this
  revision. This supersedes the previous revision's 333/332 figure, which was
  recorded before the run-budget ran out.
- `--group composition` (`tests/test_responses_team_readiness_composition.py`):
  36 tests, all passing.

That module covers, with a real `PrimaryTaskCoordinator`, real
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
- **Cancellation** - engaged before admission (nothing admitted, no HTTP) and
  engaged between admission and I/O (no HTTP, admitted rows and slot intact).
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
| Cancellation honoured at admission and pre-I/O | Done, exercised |
| Enrollment pinned across every wait and at the transport | Done, exercised |
| Late authenticated usage recorded, eligibility unchanged | Done, exercised |
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
- Authority *uniqueness* is not re-resolved after admission's locks. The
  digest comparison is a pre-lock fail-fast on a value that cannot drift, and
  `reserve()` re-loads that signed budget under the project advisory lock and
  re-verifies its signature, bindings, UTC day and ceilings against actual
  spend. But a second valid signed row added *during* the lock wait - which
  readiness and `reserve_attempt` both refuse before the transaction - would
  not be noticed by the in-flight admission. Repeating that resolution there
  would mean opening a second pool connection while holding admission's locks,
  which was judged the larger risk.
- `transaction_timeout` requires PostgreSQL 17; on an older server the helper
  applies statement and lock timeouts only, which do not bound commit-time
  work. The sanctioned lane, Compose and CI all pin 17.
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
- **2026-09-21 12:05 UTC (this revision)**: review found four caller-path gaps
  the helper-level tests had missed. Closed them in the actual call path: the
  accounting allowance now reaches the transport's own `settle` with `sent_at`
  sampled at the generation request and completed replies retained through a
  timed-out unwinding; the bound now spans the coordinator's own writer work,
  the artifact write and the team adapter; cancellation is read inside atomic
  admission and again before I/O; the enrollment is re-proved after admission's
  locks and at the transport. A pre-I/O hold is no longer recorded as an
  ambiguous paid dispatch. Concurrency and overspend cases are now exercised,
  so the earlier "not exercised" limitation is removed.
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:18 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | END]] -->
