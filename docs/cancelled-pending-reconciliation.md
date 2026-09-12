<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | BEGIN]] -->
# Reconcile never-started assignments after cancellation

A primary task could be durably `TERMINAL/CANCELLED` with its batch kill switch
engaged while assignment rows remained `PENDING`. The kill switch prevented new
reservations, but the real quiescence query still counted those pending rows.

The existing cancellation path now attempts a fenced ledger repair after it
persists the terminal outcome. The existing periodic reconciliation worker also
checks historical cancelled tasks before its terminal-task skip. Historical
repair does not invoke graph recovery, reconstruct a plan, create a new owner
command or rewrite the original command receipt. Tasks paused for the owner
retain their existing state and workflow.

Only a stored `TERMINAL/CANCELLED` task with an engaged batch kill switch and the
same graph/batch plan digest qualifies. The repair locks the current task writer,
projection, batch and assignment rows in one transaction and checks the real
clock again after lock waits. An expired, foreign or superseded fence cannot
authorize a change.

For each qualifying assignment, `PENDING` becomes the existing terminal
non-success status `FAILED`, with reason `task_cancelled_before_dispatch` and a
completion timestamp. No reservation or result is invented. The task remains
`CANCELLED`; this assignment disposition records that its work was not performed,
not that a provider ran and failed. An immutable audit event binds the changed
assignment IDs to the original projection and plan. Audit failure rolls back the
row changes. Repeating a completed repair changes neither rows nor audit history.

The repair preserves an assignment when any reservation digest, reservation
timestamp, result digest, completion timestamp, existing reason, provider attempt,
result packet or workload admission exists. It also preserves `ACTIVE`,
`AMBIGUOUS` and every already-terminal assignment.

Task-scoped evidence cannot safely be attributed to one assignment. Consequently
any real claim, effect reservation, reserved budget units or monetary spend
preserves all of the task's remaining pending rows. Dispatched/ambiguous graph
states, effects beyond `NOT_STARTED`, recorded assignment outcomes and a nonzero
parallel ambiguity count also prevent repair. No attempt, charge, evidence,
counter, kill switch, approval or owner receipt is deleted or reset.

## Graph bookkeeping and actual dispatch evidence

`langgraph_primary.provider_dispatch_claim` sets graph status `CLAIMED`, derives
an attempt digest and increments `budget_used_units` before native/provider I/O.
Those checkpoint fields alone are not a durable dispatch claim or monetary
charge. `prepare_provider_dispatch` later creates the real claim and effect
reservation before transitioning to `DISPATCHING`. Parallel provider execution
also requires its own assignment reservation and durable attempt/admission.

The repair therefore permits either `NOT_STARTED` or `CLAIMED` when every real
evidence exclusion above passes. Graph bookkeeping is preserved exactly. Tests
include the historical shape of a cancelled task with `CLAIMED`, nonzero graph
units and no real dispatch evidence, plus cases where real claim/spend history
correctly prevents cleanup. This is not an assumption that a particular live
task is eligible: eligibility still needs read-only preflight against its exact
stored evidence after reviewed deployment.

## Operational boundary

There is no new repair endpoint or ad hoc SQL runbook. Use the existing reviewed
deployment and reconciliation lifecycle. The authoritative quiescence query is
unchanged. Preserved evidence continues to block it as appropriate; outstanding
writers must also become quiescent under the existing shutdown procedure.

The cancellation follow-up depends on the frozen primary Slack delivery PR #188
and was tested with that source included. It does not alter #188's review head.
Validation runs use only the sanctioned disposable PostgreSQL lane, synthetic
authenticated requests and mocked provider boundaries. No live database edits,
Slack messages, provider calls, paid activation or deployment are performed.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | END]] -->
