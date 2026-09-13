<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 14:05 UTC | BEGIN]] -->
<!-- [[Grok Bot | Grok 4.20 Expert | 2026-09-12 | 03:01 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:43 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-10 | 03:25 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:55 UTC | BEGIN]] -->
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
`N8N_DIRECTOR_WEBHOOK_URL` when that setting is nonempty. When it is empty, the
gateway posts directly to `ORCHESTRATOR_BASE_URL` plus `/v1/intake` and bypasses
n8n. When configured as the relay, n8n receives an already-normalized intake,
not a raw Slack event envelope.

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
resolves `orchestrator:8080` by Docker DNS. To use this relay, configure the
Slack bot's `N8N_DIRECTOR_WEBHOOK_URL` as
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

## Credentials

| n8n credential type | Name used in the workflows | Holds |
| --- | --- | --- |
| Slack API | `Director Slack bot` | The bot token for the status posts |

**Intake has no bound n8n credential.** The webhook is unauthenticated and the
relay forwards the caller's own `Authorization` header downstream, leaving the
orchestrator as the authenticator. That bearer contains
`ORCHESTRATOR_SHARED_SECRET`, so n8n processes the secret and may persist it
transiently in webhook or execution data. The absence of a bound credential does
not guarantee that n8n's database never contains the secret. The retention
settings below limit saved executions; they do not prove zero transient storage.

The trade is that any process able to reach `127.0.0.1:<n8n port>` can make the
relay forward a request. That process still needs a valid bearer to get past
`/v1/intake`, which returns 401 without one, so the relay grants no authority it
did not already have. Do not "harden" this by binding a Header Auth credential
unless you also name it exactly `Authorization`: `slack_bot._forward` sends that
header and no custom gateway header, so any other name rejects the orchestrator's
own caller.

## Director Intake

```
Slack Gateway Webhook → Create Director Task ─┬─ success → Exactly 202? ─┬─ yes → Accepted                    (202)
                                              │                          └─ no  → Unexpected Upstream Success (502)
                                              └─ error   → Definite refusal? ─┬─ yes → Intake Failed          (502)
                                                                              └─ no  → Ambiguous - Stop  (n8n default 200)
```

**Inbound.** The Webhook node carries no authentication and no credential; the
caller's `Authorization` header is forwarded downstream instead. **Ignore Bots is
deliberately off**: the caller is `python-httpx`, and user-agent bot filters can
classify HTTP-library agents as bots and drop the request before it is relayed.
n8n also offers an IP allowlist under the node's options; set it if the gateway
has a fixed address.

**Idempotency.** `source_event_id` is the stable Slack event ID and the
orchestrator deduplicates on it, so a re-delivered Slack event creates no second
task. Do not substitute a generated ID, a timestamp, or the n8n execution ID.

**The success response is the orchestrator's `TaskRecord`, verbatim.**
`slack_bot._intake_receipt_task_id` requires exactly one `task_id` matching
`DIR-(?:LG-)?[A-F0-9]+`, at the top level or under `data`, and `_forward`
additionally requires HTTP **202** exactly — not 200, not 201. A summarised or
renamed response body makes the bot post `DIRECTOR — BLOCKED` on an intake that
actually succeeded.

**Failure.** `Create Director Task` uses `onError: continueErrorOutput`, so an
error takes the second output. Only a numeric **401**, **403** or **422** reaches
`Intake Failed`, which responds **502**. A **409**, other non-401/403/422 **4xx**, a
**5xx**, or a transport error with no status goes to the ambiguity branch for
reconciliation instead.
Only the upstream status code is echoed; no response body, provider output or
secret-shaped value is relayed back through the webhook.

**Definite refusal and ambiguous delivery are not the same thing, and the whole
design turns on the difference.**

Only an upstream **401**, **403** or **422** is treated as a definite refusal before intake
is reserved, so the gateway can be told plainly and the error branch responds
**502**. Other **4xx** values are not that proof: a **409** from
`SlackIntakeReplayConflict` (`main.py`, `store.py`) means a conflicting task may
already exist, so the workflow sends those statuses down the ambiguity branch
instead of asserting a clean refusal.

**A 5xx is not proof of anything.** Primary intake is reserved before later
operations can fail (`primary_coordinator.py`, `primary_default.py`), and
`main.py` wraps such exceptions as **503** — so a task may already exist behind
one. 5xx is therefore classified as unproven and takes the same path as a
missing status code.

**No** status code — a timeout, a dropped socket, a response lost after the task
was written — means the outcome is genuinely unknown. The task may exist.
Answering 502 there would assert a failure that may not have happened, and 202
would assert a success that may not have happened. So that branch deliberately
reaches **no Respond node at all**, n8n answers with n8n's standard message with a 200 status, and the
gateway posts *"Acceptance could not be confirmed. The request state must be
reconciled before retrying."* — which is exactly right for an unknown delivery.

That is documented n8n behaviour: a workflow finishing without executing a
Respond to Webhook node returns a standard message with a 200 status, while a
workflow that *errors* before the first Respond node returns 500.

**Do not assert the body shape.** n8n does not document the standard message's
text, and the gateway rejects any non-202 before it parses a body, so nothing
depends on it. Historically, probes on 2026-09-09 against the *pre-fix* workflow
— which used `onError: stopWorkflow` — measured a zero-byte body at 200. That
observation stands as measured and is not the contract; do not generalise it to
the corrected design.

**The citation explains the mechanism, not the cause.** Scope it correctly: a
non-202 does **not** prove the execution ended without responding — `Intake
Failed` and `Unexpected Upstream Success` are Respond nodes and both answer 502.
Only n8n's **default 200** indicates the NoOp path, where no Respond node ran.
Even then the status alone does not identify which upstream condition produced
it. For that, read the n8n container logs, which carry the upstream status.

**Execution retention stays `none`, and that is a security setting, not a
diagnostic one.** The webhook item includes `$json.headers`, which carries the
caller's `Authorization: Bearer <shared secret>`. Retaining error executions
would retain that secret in n8n's database despite the absence of a bound
credential. Diagnose from the container logs instead. Even `none` controls
completion cleanup rather than proving zero transient storage.

**Strict 202.** The gateway requires 202 exactly, so the success output passes
through an `Exactly 202` gate; any other 2xx is a contract violation and answers
502 rather than being relayed as an acceptance.

**Redirects are refused.** Following one would resend the caller's
`Authorization` header to another host.

**The relay timeout is below the gateway's.** `slack_bot._forward` posts with
`httpx` `timeout=20`. A relay that waits longer than its caller cannot classify
anything for anyone — the gateway has already given up. 15 s leaves the decision
with the relay.

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
endpoint is unreachable, `ambiguous_effect_count` is above zero, or the parallel dispatch kill switch is engaged while parallel dispatch is enabled. An engaged switch with parallel dispatch disabled is the expected offline posture: it posts once as a readiness change and is then silent. The ambiguous-effects alarm matters most:
rollback is refused while that count is non-zero.

**Every watched key is verified against the `/health` builder in `main.py`.**
`paid_runs_enabled` is top level. Most `langgraph_primary` keys come from
`primary_default.health()` or the disabled-path literals at `main.py`;
`paid_off_primary_intake_active` is set only by `primary_default.health()` and is
absent from that disabled-path literal. The kill switch is
`parallel_dispatch.kill_switch_open`, from `parallel_dispatch_health_readiness()`
— **not** a `langgraph_primary` key, and its polarity is inverted: *open* means
the switch is not engaged, so the alarm fires on `false`. Its companion `parallel_dispatch.enabled` comes from the same builder and gates the alarm.

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
2. Import each workflow file.
3. `Director Intake` needs no credential. For `Director Status`, create the Slack
   API credential and bind it on `Post Status`, replacing the
   `REPLACE_WITH_CREDENTIAL_ID` placeholder.
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

**2. The orchestrator rejects an unauthenticated intake.** Post to
`/v1/intake` directly, with no `Authorization` header. Expect **401** and
`{"detail":"Unauthorized"}`. This creates nothing, so it is the safe first probe.

**3. The relay surfaces that rejection rather than swallowing it.** Post the same
unauthenticated request to `/webhook/director-intake`. A 401 is one of the three
definite-refusal statuses (with 403 and 422), so expect **502**. **Any 200 here is a
failure of this check** — it means the execution ended without reaching a
`Respond to Webhook` node, which is correct for 5xx, a transport error, or a
non-401/403/422 4xx such as 409, and wrong for a 401, 403 or 422 the orchestrator
actually returned. Assert the status code, not the body.

**Confirm you are on the Director instance first.** More than one n8n runs on
this host. Check `environment` in the orchestrator's `/health` before trusting
any relay measurement: the Director stack reports `local`, and a different
`environment` value means you are measuring another project's relay.

**4. A valid intake returns 202.** Post a body shaped exactly like `SlackIntake`
— a `source_event_id` you have not used before, the configured channel and owner
IDs, a `thread_ts`, a short `text`, `received_at` in ISO-8601 — with a real
bearer. Expect **202** and a body containing a `DIR-…` `task_id`.

**5. Idempotency.** Re-post the identical body. Expect 202 again and the **same**
`task_id`. A second, different ID means deduplication is not working and the
gateway will double-book every Slack retry.

**6. The extra-field guard.** Add one field the model does not define. Expect the
relay to return **502**, driven by a 422 from `/v1/intake`. This is the check
that catches a workflow that has drifted back into rebuilding the body.

**7. Failure surfaces correctly, never 202. Two fixtures, two outcomes.** These
are different paths and must be exercised separately.

*7a — HTTP error with a status.* Drive a fixture that answers **422** or **401**.
Expect the relay to respond **502** carrying that `upstream_status`.

*7b — transport error with no status.* Point the relay at a **closed port**. A
refused connection carries no status code, so it reaches `Ambiguous Delivery -
Stop` and n8n answers n8n's standard message with a 200 status — **not 502, and not
necessarily an empty body.** Assert the status only. The gateway rejects the
non-202 before parsing and posts the reconciliation message. Expecting 502 here
was wrong and was corrected on 2026-09-10.

**Do not stop the real orchestrator to produce either** — it is a shared service.

**8. End to end through Slack, last.** Post `DIRECTOR …` in the configured
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
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:55 UTC | END]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-10 | 03:25 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:43 UTC | END]] -->
<!-- [[Grok Bot | Grok 4.20 Expert | 2026-09-12 | 03:01 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 14:05 UTC | END]] -->
