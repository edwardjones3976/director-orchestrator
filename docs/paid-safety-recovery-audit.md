<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:29 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | BEGIN]] -->
# PR #326 recovery audit

Candidate starts at `8e2d8d250d10a1d6baeb96f9dbd2d286e3cb150f`.
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
| Engine settlement journal versus task CAS | Journal is keyed by task and effect. It retains the run/packet before the task hold save; restart restores a missing run and observation from engine-owned evidence bound to an existing intent. Charge and hold-clear replay are idempotent. |

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
| `settlement_result_packet` | Never read. Only the runtime outcome supplied to the engine is journaled. Old raw packets are not migrated as authority; runtime readback remains necessary. |
| `settlement_packet_only` | Never read. The engine explicitly records whether it created the temporary run in its own journal. |
| Old holds without a journal | Reconstruct only the exact effect's acknowledged run and ordinary authoritative usage. No raw settlement map or raw packet is promoted. |
| `usage` via `authoritative_reported_cost` | Provider-reported telemetry, intentionally allowed through existing provider-specific numeric validation. It cannot choose the effect key or pending/run binding. |
| `packet_result_digest` | Still read by packet-routing control flow and `packet_results.py`. The consumer validates packet identity/digest, but the earlier routing tests use raw marker presence. This is a separate unresolved trust-boundary finding; this change does not claim it safe. Suggested follow-up: persist consumer provenance outside raw and make routing use that receipt. |
| Grok `transport` in `_run_transport` | Still selects the communication evidence transport from raw. The CLI adapter adds its own marker, but other payloads are copied as raw. Separate unresolved finding: bind transport to the reserved interaction/adapter instead. |

## Checks prepared

- New adversarial cases: real enforced workload finalization on acknowledged and ambiguous spend failure; preexisting pending workload recovery; forged/malformed raw recovery entries and packet markers; journal commit before task-save failure.
- Released-row cases cover all three metered providers, unknown/zero/positive readback, restart, active telemetry, count/budget rejection, and old no-effect receipt repair.
- Existing dispatch/revision, stale-CAS, partial recovery, single-packet, reconciled-readback and parallel-member tests now inspect the engine journal.
- Python and Docker were not executed under the work-order restriction. Dispatcher must run the focused suites, syntax check, contribution-tag validation and independent exact-head review. The Python site-board check also remains dispatcher-owned.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:29 UTC | END]] -->
