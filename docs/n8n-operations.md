<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 19:20 UTC | BEGIN]] -->
# n8n operations

Two workflows. `Director Intake` relays owner requests from the Slack gateway
into the orchestrator; `Director Status` watches readiness and speaks only when
something changes. Both are imported and activated by hand after inspection.

Neither workflow contains a secret, a token, a channel ID or a DSN. Credentials
are bound in the n8n UI. The `REPLACE_WITH_CREDENTIAL_ID` and
`REPLACE_WITH_CHANNEL_ID` placeholders are meant to be replaced on import, and a
workflow that still carries one fails closed rather than running unauthenticated
or posting to the wrong channel.

## n8n is a relay, not a normalizer

This is the single fact the intake workflow is built around, and getting it
backwards produces a workflow that looks correct and fails on every message.

`slack_bot._forward` does the normalization **before** n8n sees anything. It
builds the canonical `SlackIntake` — `source_event_id`, `channel_id`,
`thread_ts`, `user_id`, `text`, `allocation_profile`,
`primary_required_capabilities`, `permalink`, `received_at` — HMAC-signs it with
`build_slack_intake_attestation`, and POSTs that object to
`N8N_DIRECTOR_WEBHOOK_URL`. n8n receives an already-normalized intake, not a raw
Slack event envelope.

Three consequences bind the workflow:

- **The body is forwarded unchanged.** `SlackIntake` sets `extra="forbid"`, so an
  added field is a 422. `allocation_profile` and `primary_required_capabilities`
  are owner-set deployment configuration read from settings — injecting either in
  n8n overrides the owner and, once paid runs are on, breaks the signature.
- **`received_at` is not regenerated.** The attestation covers
  `intake.model_dump(mode="json")`, so rewriting any field invalidates it.
- **The three `X-Director-Intake-*` headers are forwarded.** They are unused
  while `ALLOW_PAID_RUNS=false` — the handler falls back to `BEARER_DRY_RUN` —
  and required the moment it is true. Dropping them is a defect that stays
  invisible until the exact moment paid execution is enabled.

## Which n8n

The relay only works from **inside the Director Compose project**. The HTTP node
resolves `orchestrator:8080` by Docker DNS, and the Slack bot posts to
`http://n8n:5678/webhook/director-intake` — the internal hostname, which resolves
to whichever n8n container shares that project's network. An n8n on another
project, or a standalone install, can neither reach the orchestrator nor receive
the bot's traffic.

| Namespace | env file | n8n host port | orchestrator host port |
| --- | --- | --- | --- |
| Primary Director | `.env` | 5678 (compose default; `N8N_HOST_PORT` unset) | 8080 |
| Project Uppercut | `.env.uppercut` | 15678 | 18080 |
| Edge Oracle | `.env.edge-oracle` | 25678 | 28080 |

The host port is only how *you* reach the editor. It has no bearing on the relay
path, which is entirely internal. Confirm the project before importing:

```
docker ps --format "{{.Names}}\t{{.Ports}}"
docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' <n8n container>
```

The n8n container and the orchestrator container must appear on the same network.

## Credentials to create first

| n8n credential type | Name used in the workflows | Holds |
| --- | --- | --- |
| Header Auth | `Director gateway header auth` | Name `Authorization`, Value `Bearer <ORCHESTRATOR_SHARED_SECRET>` |
| Bearer Auth | `Orchestrator shared secret` | `ORCHESTRATOR_SHARED_SECRET`. Produces `Authorization: Bearer …` on the call to `/v1/intake` |
| Slack API | `Director Slack bot` | The bot token for the status posts |

**The header credential name must be `Authorization`.** `slack_bot._forward`
sends exactly `Authorization: Bearer <shared secret>` plus the three attestation
headers, and no custom gateway header. A Header Auth credential configured with
any other name — `X-API-Key`, `X-Gateway-Token` — rejects the orchestrator's own
caller. The webhook gate and the outbound call therefore hold the same secret;
this adds no new secret to the deployment.

**Bearer, not header, outbound.** The call to `/v1/intake` uses `httpBearerAuth`,
which takes a single token field and emits `Authorization: Bearer <token>`.
`httpHeaderAuth` is the wrong type there — it is for custom headers, and using it
means hand-writing both the header name and the `Bearer ` prefix.

## Director Intake

```
Slack Gateway Webhook  →  Relay To Orchestrator  →  Accepted        (202)
                                               └─→  Intake Failed   (502)
```

**Inbound.** The Webhook node authenticates with the Header Auth credential.
**Ignore Bots is deliberately off**: the caller is `python-httpx`, and
user-agent bot filters can classify HTTP-library agents as bots and drop the
request before authentication ever runs. n8n also offers an IP allowlist under
the node's options; set it if the gateway has a fixed address.

**Idempotency.** `source_event_id` is the stable Slack event ID and the
orchestrator deduplicates on it, so a re-delivered Slack event creates no second
task. Do not substitute a generated ID, a timestamp, or the n8n execution ID.

**The success response is the orchestrator's `TaskRecord`, verbatim.**
`slack_bot._intake_receipt_task_id` requires exactly one `task_id` matching
`DIR-(?:LG-)?[A-F0-9]+`, at the top level or under `data`, and `_forward`
additionally requires HTTP **202** exactly — not 200, not 201. A summarised or
renamed response body makes the bot post `DIRECTOR — BLOCKED` on an intake that
actually succeeded.

**Failure.** `Relay To Orchestrator` uses `onError: continueErrorOutput`, so a
non-2xx takes the second output into `Intake Failed`, which responds **502**.
Only the upstream status code is echoed; no response body, provider output or
secret-shaped value is relayed back through the webhook.

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
dispatch kill switch is engaged. The ambiguous-effects alarm matters most:
rollback is refused while that count is non-zero.

**Every watched key is verified against the `/health` builder in `main.py`.**
`paid_runs_enabled` is top level. The `langgraph_primary` keys come from
`primary_default.health()` or the disabled-path literals. The kill switch is
`parallel_dispatch.kill_switch_open`, from `parallel_dispatch_health_readiness()`
— **not** a `langgraph_primary` key, and its polarity is inverted: *open* means
the switch is not engaged, so the alarm fires on `false`.

`/health` returns readiness booleans only, but the whitelist means a future field
cannot leak into Slack without someone adding it deliberately.

## No `$env` in expressions

`docker-compose.yml` sets `N8N_BLOCK_ENV_ACCESS_IN_NODE: "true"`. An expression
using `$env.ANYTHING` resolves to an access-denied error, not a value, and a node
built on one fails at run time rather than at import.

So the health URL is the literal compose-internal hostname — not a secret; it is
already in `docker-compose.yml` — and the Slack channel is a
`REPLACE_WITH_CHANNEL_ID` placeholder bound once in the editor, which keeps the
channel ID out of the repository. If you prefer environment indirection, remove
that compose setting deliberately and record the decision; do not work around it
by committing the value.

## Repointing between profiles

To watch a different profile, change the health URL in the editor on that
deployment's own n8n instance. Each profile has its own n8n container, so the
literal internal hostname is correct in each.

**One intake surface at a time.** Slack and n8n are single instances. Before a
second deployment is given new-intake authority, the first one's `slackbot` and
`n8n` must be stopped. Two deployments accepting the same Slack events is the
dual-writer no-go regardless of their databases.

## Importing

1. Confirm the n8n container shares a network with the orchestrator container.
2. Open the editor and create the three credentials above.
3. Import each workflow file, then open every node carrying a
   `REPLACE_WITH_CREDENTIAL_ID` placeholder and select the real credential.
4. Open `Post Status` and replace `REPLACE_WITH_CHANNEL_ID` with the Director
   channel.
5. Activate `Director Status` first and watch one cycle; it is read-only.
6. Activate `Director Intake` only when the orchestrator is up.

## Testing the connection

Test in this order. Each step isolates one hop, so a failure names its own cause.

**1. n8n can reach the orchestrator.** From the n8n container:

```
docker compose exec n8n sh -lc 'wget -qO- http://orchestrator:8080/health | head -c 200'
```

A DNS failure here means the containers are not on one network, and nothing
below can pass.

**2. The relay accepts an authenticated call and rejects an unauthenticated one.**
Use the editor's **Execute workflow** listener and the `/webhook-test/` path;
`/webhook/` only answers once the workflow is active. Post a body shaped exactly
like `SlackIntake` — a `source_event_id` you have not used before, the configured
channel and owner IDs, a `thread_ts`, a short `text`, `received_at` in ISO-8601.
Expect **202** and a body containing a `DIR-…` `task_id`. Repeat with no
`Authorization` header and expect **403** from the webhook itself.

**3. Idempotency.** Re-post the identical body. Expect 202 again and the **same**
`task_id`. A second, different ID means deduplication is not working and the
gateway will double-book every Slack retry.

**4. The extra-field guard.** Add one field the model does not define. Expect the
relay to return **502**, driven by a 422 from `/v1/intake`. This is the check
that catches a workflow that has drifted back into rebuilding the body.

**5. Downstream failure surfaces as 502, never 202.** Stop the orchestrator, post
a valid body, and confirm 502. Restart it before continuing.

**6. End to end through Slack, last.** Post `DIRECTOR …` in the configured
channel as the owner and confirm the bot replies `<task id> — ACCEPTED`. A
`DIRECTOR — BLOCKED` reply with a 202 in the n8n execution log means the response
shape is wrong, not the intake.

Record the n8n execution IDs and the resulting task IDs. An execution log is
evidence of relay behaviour only; it is not evidence that any provider ran.

## What these workflows are not

They carry requests and report readiness. They grant no authority: they cannot
approve an assignment, widen access, spend, merge, deploy, or override a stop.
A status post is coordination evidence, not a gate result, and a 202 from intake
means the request was accepted for planning — not that any work was done.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 19:20 UTC | END]] -->
