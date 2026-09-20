<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 16:59 UTC | BEGIN]] -->
# Controlled intake lease recovery

The health fields `intake_lease_last_renewal_at` and
`lease_renewal_failure_category` are process-local diagnostics, not durable
ownership evidence. Last renewal is null until an intake renewal succeeds in
this process. Task-writer renewal success cannot mint intake renewal evidence.
The existing `writer_lease_ready` remains the observed authority check.
Failures expose only a fixed stage category; warning logs are limited to once
per 60 seconds. Expired authority is never reacquired by the heartbeat loop.

Before any recovery, the Director must preserve image/configuration identity,
health and durable generation/lease evidence, then check competing writers,
paused as well as active work, unfinished reservations and reconciliation holds.
A healthy task writer can coexist with an expired intake lease. Do not restart
merely because intake health is false. Stop for unresolved work or uncertain
ownership; reconcile through authorized controls, never direct lease SQL.

Once preflight is clear and recovery authorized, restart only the existing
default orchestrator using the sanctioned same-image startup path. Keep volumes,
configuration, paid-off posture and sibling services unchanged. Verify observed
ownership and advancing lease expiry over three normal renewal intervals,
legacy intake disabled and zero ambiguous effects. Paid-off intake restoration
is distinct from full acceptance: an executor not ready leaves live-runtime
acceptance unmet and must be reported separately. Restarting is
not release acceptance or paid activation.

An unattended runtime requires an awake host and available database. Inspect
host sleep/resume records when diagnosing expiry; correlation alone does not
prove cause. Machine-wide power changes require a separate explicit scope.

The n8n status workflow reports state transitions including recovery. Repeated
unchanged faults stay quiet. Renewal timestamps are explanatory context, not
watched diff fields, so healthy renewals do not generate periodic messages.
Provider configured flags do not attest authorized live execution. Neither paid
nor parallel dispatch is enabled by these diagnostics. Existing Slack delivery
and readback contracts remain unchanged; this source change does not publish
any workflow.

<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 16:59 UTC | END]] -->
