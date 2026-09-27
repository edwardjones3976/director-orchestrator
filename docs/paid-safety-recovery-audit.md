<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:49 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:21 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:29 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | BEGIN]] -->
# PR #326 recovery audit

Round 7 starts at `8c11d64137ce57aefb966c9afc7797ca5b06a097`.
This is an implementation audit, not a test pass or independent review.

## A: durable dispatch records

| Site / record | Handling |
| --- | --- |
| Legacy dispatch and revision: provider run, interaction acknowledgement, task effect observation | Persisted before spend settlement; retained on a spend pause. |
| Legacy dispatch and revision: workload reservation | Finalized even when settlement returns false. Restart repairs existing pending rows from exact task effect observations without reserving or submitting again. |
| Ambiguous legacy dispatch/revision | Workload, interaction, effect and contact evidence are recorded before the spend pause; no retry authority is created. |
| Single packet dispatch: workload | Classified from the runtime outcome before returning a spend pause; normal resumed consumption retains existing finalization checks. |
| Packet task effects | Settlement hold records submitted evidence; resumed normal consumption records completed evidence and validates the result packet. |
| Parallel packet batch members and workload | The existing observed-results loop finalizes workload and member states before its spend-hold return. Recovery consumes the retained packet; it does not resend. |
| Delegation / collaboration ledger | Published before provider I/O. Resume uses the existing per-assignment publication receipt. Acceptance/collaboration outcomes remain after verified result consumption. |
| Daily task budget and provider spend reservation | Reserved before submission and retained, not reacquired for a bound packet. |
| Engine settlement journal versus task CAS | One SQLite transaction saves the task run, interaction/effect evidence, and bounded content-free journal. Restart reads the run from the task and settles accounting from the journal. Charge and hold-clear replay are idempotent. |

## B: every spend-table reader

There is no `usage_monitor.py` in this checkout; `UsageMonitor` lives in `usage.py`.

| Site | Released rows |
| --- | --- |
| `providers.py`: schema introspection / migration | Includes schema and preserves all audit rows; zero alone is not proof of not-created. |
| `ProviderDailySpendLedger._exposure`, `exposure`, reserve admission total | Excluded from count and reconciled monetary exposure. |
| `reserve`: existing-effect identity lookup | Included, to reject reuse of a released key. |
| `reserve`: serialized pending lookup | Excluded; a confirmed absent request does not block new work. |
| `release_not_created`: exact-row zero-charge check | Included, for idempotent release and contradiction detection. |
| `record_charge`: existing-row update | Included. Every authenticated charge readback reinstates the row, including zero. Unknown-cost readback also clears the released row's old no-request zero, restoring estimated exposure and unreconciled status. Known charges on active rows remain monotonic. |
| `unreconciled` | Excluded. Reinstated unknown-cost rows are included immediately. |
| `totals` | Excluded from count and estimated total. |
| `reservations` | Excluded; this is now explicitly the active-reservations view. Physical rows remain for audit. |
| `usage.py`: `_daily_accounting` | Uses active `totals` plus the following readers. |
| `usage.py`: `_provider_request_count` | Uses active `reservations`, excluding monitor probes as before. |
| `usage.py`: `_unreconciled_request_count` | Uses active `unreconciled`. |
| `usage.py`: `_exposure_usd` | Uses active `reservations` and `exposure`; unreserved probe charges retain their existing accounting. |
| `completion_scorecard.py`: monetary check 15 | No SQL reader; consumes monitor accounting, including active request count/exposure and unreconciled count. Health/completion projections inherit the same monitor view. |
| `engine.py`: `_settle_not_created_dispatches` | Exact task evidence repairs old zero-charge rows whose failed-pre-submit receipt predates `released_at`. Repeats release idempotently, without adding another observation or charging unrelated effects. Acknowledgement/assignment contradiction checks run first. |
| `engine.py`: reserve/charge/retry callers | No direct spend SQL reader; use the ledger methods above. The separate settlement journal is engine recovery evidence, not spend exposure. |

## C: raw payload trust audit

| Site / raw key | Handling |
| --- | --- |
| `spend_settlements` | Never read for recovery, reconciliation, CAS merge, packet routing or charge deduplication. New holds live in `engine_spend_settlements`. |
| `settlement_result_packet` | Never read. Only the runtime outcome digest is journaled; result content stays in the task or authenticated runtime. Old raw packets are not migrated as authority; runtime readback remains necessary. |
| `settlement_packet_only` | Never read. The engine explicitly records whether it created the temporary run in its own journal. |
| Old holds without a journal | Reconstruct only the exact effect's acknowledged run and ordinary authoritative usage. No raw settlement map or raw packet is promoted. |
| `usage` via `authoritative_reported_cost` | Provider-reported telemetry, intentionally allowed through existing provider-specific numeric validation. It cannot choose the effect key or pending/run binding. |
| `packet_result_digest` | Still read by packet-routing control flow and `packet_results.py`. The consumer validates packet identity/digest, but the earlier routing tests use raw marker presence. This is a separate unresolved trust-boundary finding; this change does not claim it safe. Suggested follow-up: persist consumer provenance outside raw and make routing use that receipt. |
| Grok `transport` in `_run_transport` | Still selects the communication evidence transport from raw. The CLI adapter adds its own marker, but other payloads are copied as raw. Separate unresolved finding: bind transport to the reserved interaction/adapter instead. |

## Round 7: atomic acknowledgement and limits

`TaskStore.save(..., spend_settlement=(effect_key, evidence))` writes the full
acknowledged run (including result and interaction ID), effect and interaction
ledgers, and compact settlement row under the same SQLite transaction and CAS.
It checks that the task contains the journal's matching run. The caller's CAS
version advances only after commit. Provider content never enters the journal.
Dispatch and revision use this transaction before charging spend. Initial
acknowledgement settles with an unknown charge; reported synchronous cost stays
pending in the journal until terminal settlement or restart recovery.

Persistence has one primary attempt and one recovery attempt, a two-second
monotonic retry-admission deadline, 250 ms SQLite and process-lock waits, and
100 ms backoff for temporary lock/I/O errors. Stale CAS reloads and merges the
acknowledged run, interaction/effect evidence and revision count into the current
record; newer fields survive. An owner plan/generation reset refuses restoration.
The deadline prevents starting another attempt; it cannot interrupt an operating
system I/O call already in progress. This is not a hard wall-clock I/O guarantee.

If persistence remains unavailable, `AcknowledgementPersistencePending` stops
the outer engine without saving FAILED. The durable pre-submit intent remains
AMBIGUOUS (an intent with no acknowledgement observation), and its spend
reservation remains exposed. No paid request is retried. The response is retained
in memory through the bounded attempts; after process loss its content cannot be
guaranteed. Existing intent reconciliation is required before further dispatch.
This is the explicit durability bound when storage cannot accept a write.

Restart uses the complete task run, so synchronous Grok and Perplexity results
never require polling. For pre-atomic journal-only records, Gemini can use its
existing provider readback only with a unique durable interaction binding for
the same effect, provider, assignment, phase and generation. Grok, Perplexity,
and unbound Gemini stop at OWNER_DECISION_REQUIRED without polling or sending.
Packet-only evidence continues through authenticated runtime readback.

Atomicity scope is the local SQLite task database only. `main.py` constructs
`TaskStore(settings.sqlite_path)` and the engine constructs its spend ledger
from that store's path. `langgraph_postgres.py` supplies a separate Postgres
control plane, not a Postgres implementation of TaskStore. This change claims no
transaction across those databases. A non-SQLite task store or different spend
database refuses metered admission, and acknowledgement persistence also fails
closed. Supporting another backend requires a backend-native atomic write first.

The adversarial corpus covers all three providers, dispatch/revision, recovery
with complete results and verified interaction bindings, transient failure,
transaction rollback, stale CAS merge, persistent exhaustion without FAILED or
redispatch, legacy journal-only owner holds, Gemini readback, and backend refusal.
Python and Docker were not run. The dispatcher owns test execution, site-board
verification, contribution checks after its commit, and exact-head review.

## Round 8: shared-store refusal and initial unknown settlement

The admission guard now obtains the store with `getattr`: a missing or mismatched
store raises `ProviderDailyLimitExceeded` before reservation. Four bounds-test
fixtures constructed with `object.__new__` now supply a real shared-path
`TaskStore`. A regression covers missing/mismatched stores for all three providers.
Production construction remains `main.py`'s `TaskStore(settings.sqlite_path)`
passed to the engine, which creates `ProviderDailySpendLedger(store.path)`.
This verifies the source wiring, not a live deployment.

Round 7 replaced the initial `None` charge with reported usage before workload
finalization. That populated `actual_cost_usd` and removed the request from
`unreconciled()` if finalization stopped execution. Dispatch and revision again
record `None` initially. The atomic journal still retains reported cost and stays
pending when it differs from that initial charge, so restart recovers the known
cost without resubmitting. The existing four failing subtests retain their
immediate-unreconciled assertion and now also check SQL NULL, settled timestamp,
restart visibility, and recovery of the retained charge.

The failure-injection corpus is retained. Its revision charge-failure counter now
accounts for initial unknown settlement followed by terminal known settlement;
acknowledgement failure expects `None`, while terminal failure expects known cost.
Atomic rollback, retry limits, stale CAS, privacy reset, persistent exhaustion and
no-redispatch assertions are unchanged. These checks were inspected but not run:
Python and Docker execution remain dispatcher-owned for this work order.

## Checks prepared

- New adversarial cases: real enforced workload finalization on acknowledged and ambiguous spend failure; preexisting pending workload recovery; forged/malformed raw recovery entries and packet markers; combined-write rollback, transient retry, persistent failure and legacy journal-only readback.
- Released-row cases cover all three metered providers, unknown/zero/positive readback, restart, active telemetry, count/budget rejection, and old no-effect receipt repair.
- Existing dispatch/revision, stale-CAS, partial recovery, single-packet, reconciled-readback and parallel-member tests now inspect the engine journal.
- Python and Docker were not executed under the work-order restriction. Dispatcher must run the focused suites, syntax check, contribution-tag validation and independent exact-head review. The Python site-board check also remains dispatcher-owned.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:29 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:21 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:49 UTC | END]] -->
