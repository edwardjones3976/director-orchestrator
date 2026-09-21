<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:02 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 17:31 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 15:13 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:50 UTC | BEGIN]] -->
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
longer holds (a restart between reservation and dispatch) also fails closed,
**before either database call**: the lookup's absence raises immediately, so a
known refusal never reads the writer or persists a packet artifact on the way
out - and never runs those calls with the `None` deadline that absence implies.
It is never re-derived.

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

The writer generation is bound to the writer admission **actually resolves**,
not the one dispatch read when it began. The live coordinator resolves - and
renews or, after an expired lease, reacquires - the task writer at the moment
it enters admission, and a reacquired lease carries a new fencing generation.
An independent real-PostgreSQL probe expired the lease after the real
authority lookup and before that resolution: readiness had selected generation
1, dispatch had captured generation 1, so the comparison between those two
passed while admission committed under generation 2, leaving a STARTED
attempt, a spend row, a workload row and a constructed HTTP client behind.
The coordinator now receives the generation the selection was checked under
(`expected_writer_generation`) and refuses a resolved writer of any other
generation (`readiness_writer_changed_before_admission`) before the admission
transaction is entered, so nothing is written and no provider client is
constructed. The reacquired lease itself stands: a new generation is a
different selection, not a renewal.

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
instead of being classified. For a provider transport it arises before the
real request: before `adapter.dispatch` is entered at all, or from
`authority_recheck` inside the Responses path, after the input-token count and
strictly before the generation request. An earlier revision of this document
said it was never raised after a generation request; that was wrong for the
native team lane, whose callbacks run the same recheck *after* native
execution - at each local-command boundary and immediately before candidate
integration - so a generated result may already exist when the hold is
raised. The classification is unchanged because it is still correct: the
attempt's durable STARTED row remains, admission refuses to redispatch a
STARTED attempt until it is reconciled, and the hold is propagated as an owner
decision with the admitted rows, slot and evidence left as they are. Nothing is
replayed and nothing is recorded as an AMBIGUOUS paid outcome on a guess.

## The team lane

`NativeTeamPacketTransport` now consumes the same two things the Responses
lane does. `context.deadline` is the primary lane's remaining budget: it bounds
every SQL acquisition, statement and transaction this transport opens (through
`bounded_transaction`), refuses an already-spent bound before the worker is
prepared, and is handed to a worker that declares `accepts_primary_deadline`
so preparation and the remote exchange narrow their own grant timer instead of
restarting a full one each. `context.authority_recheck` runs inside
`before_execute` - after preparation has waited, immediately before the
execution entitlement and any native run - at each local-command boundary,
again immediately before the durable candidate is integrated, and once more
**inside the integrator's own transaction**, on its connection, after it holds
the writer row and before it writes any Git object. That last read is the one
that matters against a same-owner cancellation: `engage_kill_switch` locks the
same writer row before committing its switch, so a cancellation that completed
before the integrator's lock is seen there and refused
(`wave_cancelled_before_candidate`) with no candidate object, retention ref or
row created, while one still waiting behind the lock is serialized after the
candidate rather than racing it. The transport's read a moment earlier ran in
a separate transaction and could not see that ordering. Work after the
exchange spends this operation's one completion allowance rather than a fresh
budget, and a failure there is refused conservatively: the capture the
transport already stored and the STARTED attempt remain, and nothing is
retried.

A standalone caller carries neither value; every one of these checks is then a
no-op, and a worker that declares no support keeps its previous call signature
unchanged. When a primary bound *does* exist, a worker that cannot enforce it
is refused before it is invoked, rather than being held to nothing while the
wave believes it is bounded.

The decisive recheck is after the blocking work, not before it: claiming the
execution entitlement takes a pooled connection, a query and row locks, so the
authority is re-read once that returns, with nothing between it and the worker
being told to execute. The local-command boundary does the same.

Across hosts the bound travels through a **causal anchor handshake**, never as
this host's `time.monotonic()` instant, which means nothing on another
machine; never as a remaining *duration* re-anchored on arrival, which an
earlier revision used and which hands the transit back; and no longer as a UTC
instant, which the previous revision used and which only bounds the remote side
under a clock-agreement premise this protocol cannot establish - an independent
probe showed a receiver clock fifteen seconds behind, masked by ten seconds of
transit, passing the plausibility check and running five seconds past the
director's bound. The remote server now speaks first: it takes an instant on
its own monotonic clock, then sends an authenticated `anchor` frame carrying a
token it generated, bound to the challenge. The director samples what is left
of its fixed bound **only after that frame has arrived** and sends it in
`prepare` together with the token. The server's instant precedes the
director's sample in real time, so `anchor + remaining` can only be early -
by at least the transit in each direction and any pause on either side - and
never late; it is never `receipt + remaining`. No clock is compared with
another, no skew constant exists, and no synchronization service or
configuration was added. The `exchange` frame names the same anchor and
carries **no time value**, so there is nothing to renew: the server keeps the
bound it retained since preparation and refuses a frame naming a different
mode or anchor, claiming standalone use after a bound was prepared, or
carrying a bound after standalone preparation.

**Legacy frames are refused, not downgraded.** The `prepare` frame must carry
exactly `binding`, `grant_digest`, `attempt_digest` and `primary_bound`; the
`exchange` frame exactly `grant`, `work_packet`, `source` and `primary_bound`.
An explicit `primary_bound: null` is deliberate standalone use and keeps the
grant timer alone. An older client's frame - the revision that sent
`lifetime_seconds`, the one that sent `primary_cutoff`, or one with no bound
key at all - is refused before preparation instead of being treated as
standalone, as is a bound naming an anchor this server did not issue (which
is what a client that did not wait for the anchor would send), a non-finite,
non-positive or already spent value, or one above the task grant's own
`timeout_seconds` ceiling (2700 seconds, read from `TeamGrant` rather than
restated) - which no director holding a valid grant can send, so it is
refused as malformed rather than clamped. The server's own anchor, and the
resulting instant, must be finite or the frame is refused too.

**Support is acknowledged before private source.** The `anchor` frame *is* the
acknowledgment: a server that does not speak this protocol never sends it, so
the director - which waits for it before sending even the public preparation -
either refuses at the first frame of the wrong kind or shape, or is ended by
its own local timer at the bound having sent nothing but the challenge. In
either case no preparation and no source reaches such a server. The shipped
SSH client always carries a bound: the primary lane's remaining budget when one
exists, otherwise its own grant timer, so the remote worker is never bounded
later than the local SSH kill.

What this does **not** prove, stated rather than implied: killing the local SSH
process bounds this side only; a server that never anchors is ended by that
local timer only when the bound elapses, not sooner; `preexec_fn` runs between
fork and exec and cannot be interrupted once `Popen` is entered; and cleanup
spends its own five second process wait plus a further five second cgroup
drain, which the transported bound does not cover. Remote cgroup teardown is
not demonstrated here.

Inside the worker, the remaining budget is spent as it is: a fractional
remainder stays fractional, and a non-positive one is refused instead of
being rounded up into a fresh second. Preparation carries the same anchored
bound through qualification, the native state scan and the durable claim,
re-checked between them and **once more after the claim is durable**:
preparation that has run out writes no claim, and one that runs out while
writing it - during `fsync` - acknowledges nothing. That consumed claim is
deliberately retained rather than deleted as a rollback: removing it would
make the same qualification receipt spendable again, whereas an unacknowledged
preparation leaves the exchange with no preparation and a repeat on the same
receipt refused by the existing claim. `serve` re-checks the bound again
before sending `prepared`. The worker also retains the bound it was
acknowledged under, bound to the same attempt and challenge, and the exchange
can only narrow it - a caller carrying a later bound, or none, at exchange
still runs under the prepared one. `run_unprivileged` is unchanged from the
previous revision, which an independent probe confirmed: it takes the absolute
bound rather than a duration, the prompt is serialized before it, the bound is
checked after cgroup and file setup and again immediately before `Popen`, and
the value `communicate` receives is what is left *at that moment* rather than
the duration the caller computed earlier; a `Popen` that returns after the
budget has elapsed gets no wait at all, and the group is still killed and
drained. Candidate integration receives the same fixed completion bound
through its Git and database work rather than starting a sixty-second timer of
its own.

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

A reply is "complete" the moment the provider has delivered it, which is
earlier than the moment this transport can return it. HTTPX ends
`aiter_bytes` by closing the response, and closing marks the response closed
before its stream cleanup is awaited, so a cleanup that hangs or raises can
strand a body that already arrived in full - and was already charged for. An
independent real-`AsyncByteStream` probe proved exactly that: both a hanging
`aclose` and one raising `ReadError` refused the attempt and left no usage
row. The generation response's bytes and headers are therefore recorded as
they are *received*, and if the failure happens before parsing, those bytes
are parsed on the way out.

Completeness is proved there, never assumed: an over-bound body is refused
outright, a truncated or malformed one cannot parse as a single JSON object,
and every existing check - terminal status, model, tier, account binding,
token accounting, capture bounds - still applies afterwards. The same recovery
covers the outer client's cleanup, which previously raised ambiguity even with
a complete reply in hand. The operation is still refused (`... exhausted after
a complete provider response` or `... cleanup failed after a complete provider
response`), never retried, and never turned into output or funding.

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

Every figure below is a run that completed. An earlier revision's final
`--group full --warnings` and `--group full` attempts were cut off by the
author window and produced no result; those receipts stand as they are and
nothing here is claimed from them. The 17:31 and 18:02 UTC revisions changed
one production module, `antigravity_team_worker.py`, its test module, this
document and (at 18:02 UTC only) the extraction names in the root-only
`tests/test_team_process_lifetime.py`, which no selected group runs. The
18:52 UTC revision changed `primary_coordinator.py`, `primary_default.py`,
`langgraph_runtime.py`, `antigravity_team_transport.py` and
`antigravity_team_artifacts.py`, which the admission, full, startup and
readiness groups also import; only `--group composition` and `--group team`,
which carry the two new regressions, were rerun for it. The figures for the
other groups are therefore the earlier revisions' results on bytes that have
since changed - reused as prior evidence with that limit stated, not claimed
for this revision - until the Director's host QA covers the current bytes.

- `--group composition` (`tests/test_responses_team_readiness_composition.py`):
  **48 tests, all passing**, this revision: the previous 47 plus the
  writer-reacquisition regression, which reuses the independent witness
  unchanged - the real `_enrolled_wave`, the real coordinator and
  disposable PostgreSQL, the lease expired after the real
  `live_provider_admissions` lookup, and an `httpx.AsyncClient` constructor
  sentinel - and asserts the hold's reason code, a reacquired generation 2,
  zero attempt, spend and workload rows and zero client constructions.
- `--group admission` (`test_primary_parallel`, `test_antigravity_team`,
  `test_primary_coordinator`, `test_langgraph_postgres`): **182 tests, all
  passing**, at the 16:56 UTC revision on unchanged bytes, including every
  PostgreSQL deadline case below.
- `--group full` (all ten selected modules, including
  `tests/test_langgraph_primary.py`): **417 tests, all passing**, at the 16:56
  UTC revision on unchanged bytes, in 273s with zero resource warnings and a
  verified lane teardown. The team lane is not in this selection; `--group
  team` covers it.
- `--group startup`: 1 test, passing at an earlier revision on unchanged
  bytes.
- `--group transports` (not rerun this revision; its 54-test result stands on
  unchanged bytes) (`test_primary_transports`,
  `test_openai_responses_validation` - two existing modules outside the ten
  selected): **54 tests, all passing**. Two fixtures there had gone stale
  against the current transport: a finite `monotonic` `side_effect` list that
  additional legitimate cutoff checks exhausted, and a meter fake predating
  `verify_current`/`clock`/`sent_at`. Both were corrected in the fixture - a
  clock driven by request stages, and the explicit current protocol with
  named parameters - with no production byte changed and no assertion
  weakened.

- `--group compatibility` (the cancellation fixture and the skill suite-set
  check): **2 tests, passing**, at the 16:56 UTC revision on unchanged bytes.
- `--group team` (`test_antigravity_team_worker`): **37 tests, all passing**,
  this revision: the 35 below plus, at 18:52 UTC, a positive control that
  runs the live coordinator's own `_require_current_dispatch_authority` at
  every boundary of the real transport - including inside the real
  integrator's transaction - against the real disposable PostgreSQL and the
  local fixture Git repository and records the candidate, and the
  cancellation case: the switch is engaged exactly as `engage_kill_switch`
  does (writer row held, switch set, committed) after the transport's own
  recheck has passed and before the integrator takes the writer row, and the
  integrator refuses with `wave_cancelled_before_candidate` - no candidate
  row, no `refs/director/team/` retention ref, repository head untouched,
  the STARTED attempt and the stored capture retained, and the integrator's
  result boundary never claimed. Reverse ordering - a cancellation waiting
  behind the integrator's writer lock - is the unchanged row-lock
  serialization and is not newly tested here. The 16:56 UTC revision's 28
  included nine tests that were
  removed at 17:31 UTC rather than kept: five exercised only the cutoff
  helpers or compared dictionaries the implementation also built (the one
  named for `serve` never called `FrameChannel`, `serve` or
  `PinnedSshTeamWorker`), one entered preparation already expired and never
  reached qualification, and three process tests lived inside the PostgreSQL
  fixture, two of them sleeping on the real clock. Fourteen replacements in a
  class that acquires no database took their place, joined at 18:02 UTC by
  the two ceiling and anchor cases below.

What those fourteen actually exercise: the real `PinnedSshTeamWorker` and the
real `serve` talking over two OS pipes - real `FrameChannel` frames, real
authentication, sequence, challenge and kind checks - with a thread in place
of the SSH process, each side on its own monotonic clock with a distinct
epoch, advanced only by the stated transit of every frame it reads. On that
seam: an acknowledged success including both boundaries and the capture, with
the server bounding preparation and exchange alike at `anchor + remaining`
(5005 + 35 = 5040 on its clock, not 5010 + 35); a twenty-five second server
pause between anchor and `prepare` that does not extend the bound; a director
pause before its sample that shrinks what it sends; a bound spent in transit
that never prepares; a server of any earlier revision, which waits to be
spoken to first, receiving nothing but the challenge before the local timer
ends it; and a server answering with the wrong kind or shape refused before
preparation. On the real `serve` with prerecorded director frames: explicit
`null` as deliberate standalone use through to the capture; fourteen legacy,
unanchored, invalid and over-ceiling `prepare` shapes refused with only the
anchor frame ever emitted; exactly the grant ceiling bound as it is and a
thousandth of a second more refused rather than clamped, with `TeamGrant`
itself shown to accept 2700 and reject 2701; an infinite or undefined server
anchor refusing an otherwise admissible bound while still admitting
standalone use; and six `exchange` shapes refused after `prepared` and before
execution with the prepared bound unchanged. On the real `prepare_binding`
with the real constructor: the budget consumed by qualification writes no
claim; the budget consumed during `fsync` retains the claim, refuses
readiness, refuses the exchange and refuses a second preparation on the same
receipt; and a preparation inside the bound whose exchange is offered a later
bound still hands `run_unprivileged` the prepared one. On the real
`run_unprivileged` with a synthetic kernel, clock and `Popen`: exact and
fractional waits after setup and launch, an absolute bound and a timeout
narrowing each other, a `Popen` that consumes the budget getting no wait while
the group is still killed and drained, and setup or an absent bound launching
nothing. The eleven PostgreSQL-backed tests - the real transport, entitlement
boundary, integrator and Git repository, cancellation, shared-budget,
unsupported-worker and completion-bound cases - are unchanged.

What they do **not** exercise: a real SSH process (a thread and two pipes
stand in), host qualification (`qualify` is replaced), any native provider
execution, the `preexec_fn` callback, the Linux worker's cgroup/uid
containment, and remote cleanup after a bound expires. Those remain unproven
here and are stated as limits rather than coverage.

Two independent probes are preserved as *failed pre-repair* evidence, not as
current verdicts: the supplied restart probe reached the writer sentinel and
observed `artifact_rows_before=0, artifact_rows_after=1`, and the supplied
three-case team probe saw the cancellation hook never invoked and an expired
deadline reach the synthetic execution sentinel. Both were taken at the frozen
head before these changes. The independent full host QA at that head (3123
tests, 6 failures, 1 error, 2 platform skips) also stands as failed QA; two of
its failures are the fixtures corrected here, and the remaining five - four
native installer cases and Compose rendering exit 125 - are with the Director
for harness diagnosis and are untouched by this work. The sealed security scan
at that head reports two LOW findings and no HIGH/CRITICAL, with a canonical
metadata discrepancy (completed outcome, partial coverage field, a deferred
discovery entry) that its own review must reconcile; nothing here claims
complete coverage, exploitation or deployment.

`tests/test_langgraph_postgres.py::TransactionDeadlineBindingTests` proves the
timer by wall clock, durable rows and the reported SQLSTATE, never by `SHOW`
alone: a bound tightened from 1.0s to 0.25s ends two cumulative sleeps with
25P04 and commits nothing; a later 5.0s bound does not extend an earlier 0.25s
one; a deferred constraint trigger sleeping 0.5s during COMMIT is cut off by a
bound tightened to 0.3s, again 25P04; an already-expired bound is refused
before and after an earlier one; the same connection runs a later transaction
0.6s past the previous transaction's deadline and commits; and the real
projection and writer readers, under an `ACCESS EXCLUSIVE` table lock, end at
their own bound instead of silently losing it. Each witness names the cause it
accepts - the transaction timer (25P04), `statement_timeout` (57014),
`lock_timeout` (55P03), or the session termination `transaction_timeout`
performs - rather than accepting any exception.

`CheckpointOperationDeadlineTests` exercises the installed saver itself: a
second thread holds the saver's mutex while the cutoff expires, the caller is
refused in time with nothing written and the holder releases and joins
cleanly; and with `BEFORE INSERT` triggers delaying the blob and checkpoint
writes - each well inside any per-statement allowance, their sum outside the
cutoff - the operation aborts with the whole-transaction cutoff (25P04, or the
session termination it performs during pipeline synchronisation; a
per-statement 57014 is deliberately *not* accepted there) and neither
checkpoint nor blob rows survive.

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
- **Cleanup after a complete reply** - all three cases record exactly one
  usage row of 150 tokens against the admitted attempt digest, make two
  provider requests, and still refuse. The two inner-stream cases (an `aclose`
  that hangs past the deadline and one that raises `ReadError`) additionally
  assert the spend row, the body-exhaustion and cleanup witnesses, and that
  both response and client are closed; the outer-client case asserts the
  closed client only. A body truncated before the same failure is metered not
  at all and stays ambiguous.
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
| Complete replies survive response/client cleanup failure | Done, exercised |
| A lost selection is refused before any database call | Done, exercised |
| Team lane honours the primary recheck and shared budget | Done, exercised |
| Readiness generation bound to the writer admission resolves | Done, exercised |
| Candidate integration re-reads authority inside its writer-locked transaction | Done, exercised |
| One anchored bound; transit and pauses spent, never returned or renewed | Done, exercised |
| Remote support acknowledged before any private source | Done, exercised |
| Legacy bound-bearing frames refused; explicit standalone kept deliberate | Done, exercised |
| Preparation, its durable claim and process setup inside the same bound | Done, exercised |
| Cross-host bound without a clock premise | Done, exercised - causal anchor, no skew constant |
| A worker that cannot enforce the bound is refused | Done, exercised |
| Recheck after the blocking authorization, before execution | Done, exercised |
| Completion bound carried into candidate integration | Done, exercised |
| Remote cgroup teardown after a spent lifetime | Not proven - stated limit |
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
  client sees a closed connection rather than SQLSTATE 25P04; that witness
  accepts exactly those two shapes and nothing else.
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
- **2026-09-21 12:57 UTC**: an independent PostgreSQL 17.11
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
- **2026-09-21 14:07 UTC**: an independent probe reproduced the
  helper's lifetime defect - `SET LOCAL` outside a transaction on an autocommit
  connection is gone before the next statement - so every bounded read path now
  binds inside one explicit transaction. The saver's own mutex wait and its
  cumulative blob/checkpoint/synchronisation/commit work are bounded at the
  seams that actually wait. The writer lease is proved against
  `clock_timestamp()` at the final admission fence, because `now()` is
  transaction-start time and a lock wait can outlast a lease. Timeout
  witnesses now identify the SQLSTATE or the session termination, and this
  document's outer contribution records and evidence claims are corrected.
- **2026-09-21 14:50 UTC**: an independent real-HTTPX probe
  proved that a complete, already delivered generation reply was lost whenever
  the response's own cleanup hung or raised - the body was received, but
  parsing happened only after the stream context returned, so nothing was
  metered. The generation bytes and headers are now recorded as they arrive
  and parsed on the way out of a failed cleanup, with completeness proved by
  parsing and the byte bound rather than assumed; the same recovery covers an
  outer client cleanup error, which previously raised ambiguity with a valid
  reply in hand. Refusal, admission and account binding, byte bounds,
  deadlines and the no-retry rule are unchanged. The cumulative-checkpoint
  witness now demands the whole-transaction cutoff and asserts blob rollback,
  and `bounded_transaction`'s docstring no longer implies a cross-statement
  snapshot.
- **2026-09-21 15:13 UTC**: the host ran
  `tests/test_primary_transports.py`, which sits outside the ten selected
  modules, and found two stale fixtures - not production defects. Both were
  corrected in place without weakening a single assertion, and this document's
  cleanup-case bullet was qualified to match what each of those three tests
  directly asserts.
- **2026-09-21 16:04 UTC**: an independent restart probe showed
  a dispatch with no live selection still reading the writer and persisting one
  packet artifact before its intended refusal, so that refusal moved to the
  lookup itself, ahead of both database calls. The team lane, which had been
  given a primary recheck and absolute deadline it never consumed, now spends
  that budget across its SQL, preparation, execution authorization, worker
  waits and completion, and re-reads the primary authority immediately before
  native execution and before candidate integration. Two stale fixtures the
  host found - an unrestricted cancellation Mock and a missing skill suite
  entry - were corrected, and the pre-repair probe, host QA and security
  evidence is preserved as historical failure rather than restated as a pass.
- **2026-09-21 16:29 UTC**: independent review found five
  remaining team control gaps, two of them probed failing. The SSH lane now
  forwards an authenticated remote *lifetime* through its frames and `serve`,
  re-anchored to the receiving host's clock, with the clock-domain assumption
  and the unproven remote-teardown limit stated rather than implied. A primary
  bound is no longer silently dropped for a worker that cannot enforce it, and
  the misleading "standalone" case that supplied a deadline and expected
  execution was replaced by one that carries none. The authority recheck moved
  to *after* the blocking execution and command authorizations. Candidate
  integration receives the fixed completion bound through its Git and database
  work instead of restarting sixty seconds, and the Linux worker no longer
  rounds a spent remainder up into a new second.
- **2026-09-21 16:56 UTC**: an independent probe showed the
  previous revision's *duration* actually handing transit time back to the
  remote worker - 10 seconds sent plus 3 in flight became 10 again - so this
  document's "lost on the safe side" claim was wrong and is corrected here.
  One fixed authenticated UTC cutoff now travels from preparation through
  exchange, is converted once on the receiver's clock and only narrows; the
  accepted clock-skew premise is stated and enforced in both directions
  rather than assumed; and the remote server must acknowledge that exact
  cutoff before any private source is sent, because a local capability flag
  proves nothing about an older server. Preparation enforces the bound across
  qualification, the state scan and its durable claim, and `run_unprivileged`
  now takes the absolute bound, checks it after setup and before `Popen`, and
  recomputes what `communicate` waits for. The uninterruptible `preexec_fn`
  window and the two separate five-second cleanup waits are disclosed rather
  than covered.
- **2026-09-21 17:31 UTC**: an independent seven-case probe
  against the real authenticated `serve` failed four of the previous
  revision's claims: a five-second skew constant presented as a measured
  premise could be masked by transit (a receiver fifteen seconds behind ran
  five seconds past the bound); both sides sampled their two clocks in an
  order a pause could exploit; `prepare_binding` could acknowledge a budget
  that ran out during `fsync`; and a `prepare` frame from the revision that
  sent `lifetime_seconds` was silently treated as standalone. The UTC cutoff
  and its skew constant are gone. The server now anchors on its own monotonic
  clock and speaks first, the director samples only after that anchor
  arrives, the bound is `anchor + remaining` and never receipt-time, and the
  exchange names the anchor without carrying any time value. Prepare and
  exchange frames have exact shapes; legacy and unanchored bounds are refused
  while an explicit `null` stays deliberate standalone use. Preparation
  re-checks the bound after the claim is durable and before `serve`
  acknowledges, retaining the consumed claim rather than deleting it, and the
  worker keeps the prepared bound so the exchange can only narrow it. The
  independently confirmed `run_unprivileged` accounting is unchanged. The
  tests that only mirrored the implementation or never reached the seam they
  were named for were removed; their replacements drive the real
  `PinnedSshTeamWorker` and `serve` over OS pipes with distinct clocks and
  need no database.
- **2026-09-21 18:02 UTC**: two minimal corrections from the
  Director's review of the above. `anchored_primary_bound` accepted any finite
  positive remaining, including values no valid grant could produce, and did
  not validate its own anchor; it now refuses a remaining above `TeamGrant`'s
  `timeout_seconds` ceiling and a non-finite anchor or result as malformed
  input, clamping nothing, with the handshake and process accounting
  unchanged. `tests/test_team_process_lifetime.py`, the root-only Linux kernel
  regression, extracts `run_unprivileged` by AST and lacked the two helpers
  it has called since 16:56 UTC; only those two names were added, and that
  extraction is checked by the Director separately from any kernel run.
- **2026-09-21 18:52 UTC (this revision)**: two confirmed functional gaps and
  one inaccurate claim. An independent real-PostgreSQL probe expired the task
  writer's lease after the real authority lookup and before the coordinator
  resolved the writer for admission; the coordinator reacquired it under a
  new generation and admission committed against a selection checked under
  the old one, because the comparison used the writer dispatch had captured
  earlier. The coordinator now refuses a resolved writer whose generation is
  not the selection's, before the admission transaction. Two static reviews
  established that the candidate integrator, which locks the writer row,
  checked grant, proof and reservation but not the wave's current kill
  switch, assignment or latest attempt, so a same-owner cancellation
  completing after the transport's separate-transaction recheck could still
  gain a candidate; the integrator now runs the same durable recheck on its
  own connection after the lock and before any Git object is written. This
  document's claim that a hold is never raised after a generation request
  was wrong for the team lane and is corrected above, with the
  classification itself unchanged.
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:18 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:07 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 15:13 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:29 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 17:31 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:02 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | END]] -->
