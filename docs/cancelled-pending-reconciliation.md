<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:58 UTC | BEGIN]] -->
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

The automatic matching-digest repair uses the existing reconciliation lifecycle.
The signed mismatch adjudication below is a separate owner action. The authoritative
quiescence query remains unchanged. Preserved execution evidence continues to block
it; outstanding writers must also become quiescent under the existing shutdown procedure.

The cancellation follow-up depends on the frozen primary Slack delivery PR #188
and was tested with that source included. It does not alter #188's review head.
Validation runs use only the sanctioned disposable PostgreSQL lane, synthetic
authenticated requests and mocked provider boundaries. No live database edits,
Slack messages, provider calls, paid activation or deployment are performed.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | END]] -->

## Owner adjudication of a cancelled plan mismatch (#199)

The cancelled projection and batch digests remain immutable historical evidence.
`POST /v1/primary/tasks/{task_id}/cancelled-plan/reconcile` gives a mismatched,
killed `TERMINAL/CANCELLED` task a separate terminal binding, rather than rewriting
either plan, its projection, checkpoint, or original cancellation receipt.
Matching-digest cancelled tasks and every non-terminal task are refused unchanged.

The route requires the cancellation family's bearer, owner identity, channel,
and five-minute path-bound HMAC proof. The signed body also binds the current
writer generation, whole projection digest, both plan digests, diagnostic digest,
and the exact decision `retain_both_digests_close_unstarted`. It refuses expired,
foreign, or superseded writers, including on replay; it does not acquire or renew
a writer. The existing diagnostic process's reconciliation lifecycle must first
hold the task's current writer. A stale diagnostic or proof requires fresh owner
inspection and adjudication, never an automatic retry.

One transaction locks the writer, projection, batch and assignments. It reuses
all automatic-repair evidence exclusions, closes only unstarted `PENDING` rows,
and refuses the entire transaction if any `PENDING`, `ACTIVE`, or `AMBIGUOUS` rows
remain. It checks the real clock after lock waits and before completion. SQL lock
and statement waits are bounded. The command creates one append-only
`director_primary_cancelled_plan_adjudication` record containing both original
digests, the safe diagnostic, signed-command digest, closed assignment IDs, and
writer generation, plus one `cancelled_plan_reconciled` audit event and the
existing proof authorization record. No signature or secret is copied into the
adjudication. Failure of any write rolls back all changes. Exact signed redelivery
is a no-op with the same evidence digest and no additional audit row.

Running the command never sets readiness. On the next reconcile pass, including
startup with intake enabled, the worker checks the unchanged cancelled projection,
killed batch, both bindings, assignment field digest, terminal row dispositions,
and durable audit and authorization evidence. A removed pending row is not itself
proof of consistency. Corruption or drift holds readiness false even after a
previously successful pass. `paid_off_primary_intake_active` also still requires
its existing intake/writer/router gates. Paid activation and dispatch are unchanged.

## Safe diagnostic and one-command owner driver

`GET /v1/primary/tasks/{task_id}/cancelled-plan/diagnostic` requires the existing
bearer. It writes no rows and returns only digests, counts/sequences, booleans and
fixed field names. It never returns raw projection contents, assignment values,
writer tokens, or batch rows. The comparison covers surviving explicit provider,
deployment, dependency, write-surface, retry and allocation fields by sequence.
Missing historical inputs are marked unavailable; model defaults are not inserted.

The original batch plan preimage is not stored. Hashes cannot establish which
historical inputs changed, so this diagnostic cannot prove the origin of the
historical divergence. It compares surviving records and marks
`historical_digest_preimages_available=false`. The owner must adjudicate this
limitation along with the reported differences before signing; no reconstruction
or guessed historical equality is accepted.

After reviewed deployment and explicit owner authorization, use the selected
intake-off, paid-off, non-live diagnostic process to inspect/adjudicate the task.
Do not run a second writer alongside the live writer. Deployment, choosing the
live service/database, and returning intake ownership remain owner operations.
This implementation has not been executed against the live database.

The driver reads `ORCHESTRATOR_SHARED_SECRET`, `SLACK_GATEWAY_SECRET`,
`SLACK_OWNER_USER_ID`, and `SLACK_DIRECTOR_CHANNEL_ID` from its existing process
environment. `DIRECTOR_PRIMARY_URL` selects the service (default loopback port
8080; non-loopback requires HTTPS). No credentials belong on the command line.
From this worktree root, the owner runs:

```powershell
$env:PYTHONPATH = 'src'
& 'C:/Users/edwar/director-orchestrator/.worktrees/langgraph-primary-cutover/.venv/Scripts/python.exe' scripts/reconcile-cancelled-plan.py DIR-LG-EF9A9D4FF1 --apply
```

Omit `--apply` for diagnostic-only operation. With `--apply`, the same command
prints the sanitized diagnostic and asks the owner to type its diagnostic digest
before constructing a fresh signed request. It sends at most one mutation, prints
no request/proof or raw error response, and never connects directly to PostgreSQL.
If a previous adjudication is already consistent, it sends no mutation. On an
uncertain HTTP outcome, inspect the diagnostic before deciding the next action;
do not recreate a command blindly. After success, wait for the next reconcile
pass and read authenticated readiness. A command acknowledgement is not release
verification or evidence that the live stack is now on main.

## Validation for the author handoff

The regression modules are `tests.test_cancelled_plan_reconciliation` (PostgreSQL)
and `tests.test_cancelled_plan_driver` (mocked HTTP). Also run the existing
cancelled-pending, cancellation, primary command/authentication, coordinator,
default activation and PostgreSQL control-plane regressions. The assigned lane is
`director-lane-199` on host port `55441`, using the supplied venv and only
`scripts/windows-postgres-test-lane.ps1 -Action Test`.

In the author sandbox the sanctioned lane attempt stopped before unittest because
Docker configuration and the engine pipe were denied. The supplied venv's base
Python executable was also denied execution. No test pass or live readiness is
claimed; the Director must run the validation before exact-head review/merge.

Subsequent Windows author validation on 2026-09-12 executed the three focused
modules (cancelled-pending quiescence, cancelled-plan reconciliation and the
driver): **42 tests passed in 56.859 seconds**, with no skips. A separate pass
covered existing cancellation, live-startup boundaries, application integration
and PostgreSQL control-plane unit/integration regressions: **107 tests passed in
83.562 seconds**, with no skips. The remaining command, authenticated-ingress,
coordinator and parallel regressions passed **59 tests in 7.784 seconds**, also
without skips. Compilation and whitespace checks passed. All three passes used
the sanctioned disposable PostgreSQL lane and removed it afterwards.
These results supersede the sandbox's inability to execute those tests. They do
not establish live adjudication, historical cause, independent acceptance, or CI
at a future committed head.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:58 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:25 UTC | END]] -->
