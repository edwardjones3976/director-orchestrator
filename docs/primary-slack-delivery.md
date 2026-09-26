<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | BEGIN]] -->
# Primary Slack lifecycle delivery

The receipt API returns nullable `owner_decision` (`approve` or `reject`)
alongside its command identity and timestamp. Startup adds the column
idempotently. A matching durable command sets it once; `deny` maps to `reject`
and legacy rows remain unknown. See [offline trial tooling](collect-offline-slack-trials.md)
for approved-pause arithmetic and its evidence limits.

Primary tasks now send an approval request and a terminal result to the original
owner Slack thread. A small read-only task that a live standing grant
pre-authorizes (phase 4S in `human-out-of-the-loop-operation.md`) skips the
execution approval request. The FastAPI lifespan starts the delivery worker when primary
mode is constructed. It uses the existing bot token, owner, channel and primary
PostgreSQL configuration; it does not start providers or enable paid execution.

The gateway signs intake in both operating modes. `/v1/intake` verifies any
supplied attestation, including in offline mode. Partial or invalid attestations
are rejected. Paid intake still requires a valid attestation; unsigned offline
API canaries remain `bearer_dry_run` and never produce Slack lifecycle posts.

The approval shows the recorded plan, assignments, repository scope and
acceptance criteria. An external-action approval also shows its persisted exact
grant, including repository, PR and base/head commits. Historical requests without
that grant cannot offer a Slack approval button. Oversized approval displays
likewise withhold approval instead of hiding scope behind a truncated preview.
The owner can reject them or inspect the authenticated full task record before
using the existing explicit `/director approve DIR-LG-...` command.

The new buttons bind the displayed task, projection digest, approval kind and
request digest. The gateway compares all four with the current task before
signing the existing primary resume command. A changed projection requires its
new request; a stale button cannot approve it. Existing deterministic execution,
budget, owner, callback, exact-candidate and external-effect gates still run.

The control-plane migration adds `director_primary_slack_delivery` and its guard
trigger. Use the existing reviewed migration/startup mechanism; do not create a
second database or manually edit delivery rows. These records belong to the same
primary schema and backup as the task projections.

The worker first checks the bot identity using Slack's native
[`auth.test`](https://docs.slack.dev/reference/methods/auth.test/), which needs no
additional scope. This identity is recorded with the post and matched on
readback; another Slack sender cannot provide its delivery evidence. A transport
failure permits one identity-check recovery attempt; an explicit refusal does not.

Each notice has an immutable task/milestone identity, projection digest, payload
and `client_msg_id`. PostgreSQL admits a post once under the current intake-owner
lease. State can advance but cannot be reset to pending, and message/evidence
identity cannot be overwritten. Cancellation supersedes unsent approvals. A
terminal graph outcome no longer exposes a stale pending approval.

Delivery states distinguish an API acknowledgement from remote verification:

| State | Meaning |
| --- | --- |
| `PENDING` | Prepared, no post attempt claimed |
| `POSTING` | One attempt claimed; a crash has an unknown outcome |
| `ACKNOWLEDGED` | Slack returned its channel and timestamp; readback is unverified |
| `DELIVERED` | Remote message matches its identity, text and visible blocks |
| `AMBIGUOUS` | Post acknowledgement is missing/invalid; never automatically repost |
| `REFUSED` | Slack explicitly refused the request; no automatic retry |
| `SUPPRESSED` | Intake lacked owner Slack authority or its configured channel changed |
| `SUPERSEDED` | An unsent decision is no longer the current projection |

Slack rate limiting ([HTTP 429 with `Retry-After` seconds](https://docs.slack.dev/apis/web-api/rate-limits/#responding-to-rate-limiting-conditions))
is handled narrowly. HTTP 429 is classified from the status line and headers
before the body is drained at all, so a slow, truncated, oversize,
envelope-less, non-JSON or malformed 429 body can neither stall the worker
past `Retry-After` nor turn Slack's definite non-acceptance into an ambiguous
outcome. A 429
on `chat.postMessage` settles the claimed row as `REFUSED` with `last_error`
`slack_rate_limited:retry_after=N`: Slack did not post, so the row is never
reposted. The worker then records a process-local not-before horizon for the
**full** `Retry-After` delay (whole seconds, honored without any ceiling; a
missing or malformed header defaults to 30 seconds) and makes no Slack call of
any kind, and claims no further row, until it passes; withheld rows stay
`PENDING` instead of ageing into `AMBIGUOUS`, and a delay that outlives the
worker keeps it deferring for as long as it runs. A 429 on `auth.test` keeps
the bounded identity attempts, and a 429 on `conversations.replies` consumes
that one readback attempt without posting again. Other non-success statuses
keep the existing ambiguous classification.
`/health.primary_slack_delivery.rate_limited` reports the horizon. The horizon
is not durable across a worker restart, and because the guard trigger forbids
`POSTING → PENDING`, a durable requeue of the refused notice would need a
ledger-identity change in `langgraph_postgres.py`; neither is part of this
change.

A crashed `POSTING` attempt becomes ambiguous after sixty seconds. Up to two
read-only lookups may resolve a found message, including after a lost post
acknowledgement. An absent message is never evidence that reposting is safe.
Readback needs the existing Slack app's access to the thread history; this
change does not grant scopes. A missing scope remains unverified. The bounded
lookup reads at most 100 replies and does not prove absence beyond that window.

The worker discovers at most 100 notices and attempts at most four posts per
pass, plus four recovery readbacks and one initial identity check. Each network operation has a ten-second wall
deadline and a 256 KiB response limit. There are no POST retries. The two-second
interval starts after a pass finishes. The outbox anti-join excludes previously
observed milestones so old tasks do not consume the discovery batch forever.

`/health.primary_slack_delivery` exposes configuration, ledger readability, recent
worker activity and counts for pending, ambiguous and unverified deliveries.
Authenticated `GET /v1/primary/tasks/{id}/deliveries` returns safe receipt fields:
task/milestone/projection identity, message ID/timestamp, attempt/readback state,
preparation/claim/acknowledgement/readback timestamps and the bound owner command
timestamp. It returns neither the token nor stored message payload. Correlate
these with the task's intake event/thread and source timestamp for measured runs.

Offline results retain `SIMULATION — NO PROVIDER INTERACTION`. Actual recorded
execution mode is persisted with each projection and retained on recovery;
current deployment flags cannot relabel a historical task. An older task without
that field or explicit offline outcomes has an unknown execution mode. Actual
outcomes are shown; this feature does not turn digest-only doubles into factual
repository summaries. Long final results are explicitly marked excerpts, with
the complete outcomes retained in the authenticated task record. Dollar cost
remains unknown unless separate billing telemetry supplies it. A delivered
notification is not evidence of paid activation, useful result correctness,
sub-minute completion, or a dollar-cost ceiling.

Validation uses the sanctioned disposable PostgreSQL 17 lane, real primary
state transitions and real FastAPI/gateway signing with mocked Slack HTTP.
No live Slack messages, credentials, providers, production migrations or
deployment are part of those tests.

<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | END]] -->
