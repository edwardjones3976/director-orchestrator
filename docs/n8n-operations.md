<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 17:00 UTC | BEGIN]] -->
# n8n operations

Two workflows. `Director Intake` carries owner requests from Slack into the
orchestrator; `Director Status` watches readiness and speaks only when something
changes. Both are imported and activated by hand after inspection.

Neither workflow contains a secret, a token, a channel ID or a DSN. Credentials
are bound in the n8n UI; the channel comes from an environment variable. The
`REPLACE_WITH_CREDENTIAL_ID` placeholders are meant to be replaced on import, and
a workflow that still carries one will fail closed rather than run unauthenticated.

## Credentials to create first

| n8n credential type | Name used in the workflows | Holds |
| --- | --- | --- |
| Header Auth | `Director gateway header auth` | The shared header the Slack gateway sends. Protects the inbound webhook |
| Bearer Auth | `Orchestrator shared secret` | `ORCHESTRATOR_SHARED_SECRET`. Produces `Authorization: Bearer …` on the call to `/v1/intake` |
| Slack API | `Director Slack bot` | The bot token for the status posts |

**Bearer, not header.** The outbound call to `/v1/intake` uses
`httpBearerAuth`, which takes a single token field and emits
`Authorization: Bearer <token>`. `httpHeaderAuth` is the wrong type here — it is
for custom headers such as `X-API-Key`, and using it means hand-writing both the
header name and the `Bearer ` prefix, which is exactly the error it invites.

## Director Intake

```
Slack Gateway Webhook  →  Create Director Task  →  Accepted        (202)
                                              └─→  Intake Failed   (502)
```

**Inbound.** The Webhook node authenticates with the Header Auth credential and
ignores bot requests. n8n also offers an IP allowlist under the node's options;
set it if the gateway has a fixed address.

**The request body** is built explicitly rather than forwarded wholesale, so the
orchestrator's `SlackIntake` contract is satisfied and nothing extra is passed:
`source_event_id`, `channel_id`, `thread_ts`, `user_id`, `text`,
`allocation_profile`, `permalink`, `received_at`. The model forbids extra fields,
so a wholesale forward would be rejected.

**Idempotency.** `source_event_id` is the stable Slack event ID. The orchestrator
deduplicates on it, so a re-delivered Slack event creates no second task. Do not
substitute a generated ID, a timestamp, or the n8n execution ID — any of those
would break deduplication silently.

**Failure.** `Create Director Task` uses `onError: continueErrorOutput`, so a
non-2xx from `/v1/intake` takes the second output into `Intake Failed`, which
responds **502**. A failed intake must never return 200. Only the upstream status
code is echoed; no response body, provider output or secret-shaped value is
relayed back through the webhook.

## Director Status

```
Every 5 minutes  →  Read Health  →  Diff Readiness  →  Post Status
```

**Quiet by default.** `Diff Readiness` compares a fixed whitelist of readiness
booleans against the previous run, held in workflow static data, and returns an
empty array when nothing changed — which ends the execution without posting.
**Silence means nothing changed, which is the normal state.** A channel that has
been quiet for a day is not a broken workflow.

**It speaks anyway** — regardless of change — on three conditions: the health
endpoint is unreachable, `ambiguous_effect_count` is above zero, or the parallel
kill switch is engaged. The ambiguous-effects alarm matters most: rollback is
refused while that count is non-zero, so it is the state you want to hear about
immediately rather than at the next change.

**Read Health** sets `neverError` and `fullResponse`, so an unreachable
orchestrator becomes a reportable state rather than a silently failed execution.

**What it reports** is a whitelist, not the whole payload: reachability,
`paid_runs_enabled`, `configuration_valid`, `new_intake_owner`,
`intake_fencing_generation`, `ambiguous_effect_count`,
`paid_off_primary_intake_active`, `primary_runtime_active`,
`legacy_new_intake_disabled`, and the kill switch. `/health` returns readiness
booleans only, but the whitelist means a future field cannot leak into Slack
without someone adding it deliberately.

## Repointing between profiles

The status workflow reads `DIRECTOR_HEALTH_URL` and falls back to the container
default. To watch a different profile, set that variable on the n8n service and
restart it — do not edit the URL into the node, which would put a
deployment-specific value into a repository file.

The intake workflow's URL is the container-internal orchestrator address. To
point intake at a different profile, the profile must be reachable on the n8n
container network; changing the port alone is not enough if the service is on a
different Compose project.

**One intake surface at a time.** Slack and n8n are single instances. Before a
second deployment is given new-intake authority, the first one's `slackbot` and
`n8n` must be stopped. Two deployments accepting the same Slack events is the
dual-writer no-go regardless of their databases.

## Importing

1. Open the n8n editor and create the three credentials above.
2. Import each workflow file, then open each node carrying a
   `REPLACE_WITH_CREDENTIAL_ID` placeholder and select the real credential.
3. Confirm `SLACK_DIRECTOR_CHANNEL_ID` and, if used, `DIRECTOR_HEALTH_URL` are
   set on the n8n service.
4. Activate `Director Status` first and watch one cycle; it is read-only.
5. Activate `Director Intake` only when the orchestrator is up.

## Testing against a simulation profile

With a Stage A profile running and provider doubles in place, send one intake
directly rather than through Slack — a simulation profile has no Slack surface.
Confirm a task record is created, that the provider result is labelled
`SIMULATION — NO PROVIDER INTERACTION`, that re-sending the same
`source_event_id` creates no second task, and that the status workflow posted the
readiness change. Then send one request with a deliberately wrong bearer
credential and confirm the response is 502 rather than 202.

## What these workflows are not

They carry requests and report readiness. They grant no authority: they cannot
approve an assignment, widen access, spend, merge, deploy, or override a stop.
A status post is coordination evidence, not a gate result, and a 202 from intake
means the request was accepted for planning — not that any work was done.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 17:00 UTC | END]] -->
