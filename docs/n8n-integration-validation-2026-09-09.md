<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 20:05 UTC | BEGIN]] -->
# Director Intake ↔ n8n — integration validation, 2026-09-09

Status: **the relay does not satisfy the Slack bot's contract.** The cause is
identified and the repository candidate is corrected. One decisive fact is still
unmeasured and needs a command only the owner can run.

## What was measured

All observations are from the running deployment, not from documentation.

| Probe | Result |
| --- | --- |
| `GET http://127.0.0.1:8080/health` | 200, full readiness payload |
| `POST http://127.0.0.1:8080/v1/intake`, no `Authorization` | **401** `{"detail":"Unauthorized"}`, 31 ms |
| `POST http://127.0.0.1:8080/v1/intake`, invalid bearer | **401**, 7 ms |
| `POST http://127.0.0.1:45678/webhook/director-intake`, no `Authorization` | **200, empty body**, 57 ms |
| `POST http://127.0.0.1:45678/webhook/director-intake`, invalid bearer | **200, empty body**, 85 ms |
| Same, with one field the model forbids | **200, empty body**, 79 ms |

The orchestrator behaves correctly. The relay does not: every request, valid or
not, returns 200 with no body.

## Why that is a failure and not a curiosity

`slack_bot._forward` calls `response.raise_for_status()`, then requires
`status_code == 202`, then requires `_intake_receipt_task_id` to find one
`task_id` matching `DIR-(?:LG-)?[A-F0-9]+`.

A 200 passes `raise_for_status`. It then fails the 202 check, so the bot posts
**`DIRECTOR — BLOCKED`** — and the empty body means neither the bot's log nor the
Slack thread carries any indication of why. The failure mode is the worst
available one: it looks like a reachable, responding endpoint from the outside.

## Cause

The deployed workflow has three nodes and no error branch:

```
Slack Gateway Webhook  →  Create Director Task  →  Accepted (202)
```

`Create Director Task` uses the default `onError`, so when `/v1/intake` answers
non-2xx the execution ends before reaching any `Respond to Webhook` node.
`Accepted` is only reached on success.

This is documented n8n behaviour, not an inference from the observation. The
Respond to Webhook node reference states that when "the workflow finishes without
executing the Respond to Webhook node", it "returns a standard message with a 200
status". The observed 200-empty is precisely that path.

Compounding it, the deployed workflow sets `saveDataErrorExecution: "none"`, so
the failing executions are not recorded. Neither end of the integration can say
what happened.

## What the deployed workflow gets right

Worth stating plainly, because the repository candidate had it backwards: the
deployed workflow is a **pass-through relay**. It forwards
`{{ JSON.stringify($json.body ?? $json) }}` unchanged, forwards the caller's
`Authorization` header and the three `X-Director-Intake-*` attestation headers,
and returns the orchestrator's body verbatim with a 202. That is the correct
architecture, and it holds no credential of its own — a secret n8n does not store
cannot leak from n8n.

The repository candidate on `claude/n8n-v2` rebuilt the body from a raw Slack
event envelope that never arrives. It has been corrected to the deployed design,
plus the error branch and the execution-recording setting the deployed one lacks.

## The unmeasured fact

**Is the n8n on port 45678 on the Director Compose network?** The relay resolves
`orchestrator:8080` by Docker DNS. A 401 from the orchestrator and a DNS failure
inside n8n produce the same 200-empty response from outside, so the probes above
cannot separate them.

Port 45678 appears nowhere in this repository: the primary namespace defaults to
5678, Uppercut uses 15678, Edge Oracle 25678. The instance also reports n8n
**2.34.5**, whereas `docker-compose.yml` pins an image by digest. Both point to
an instance created outside the documented bootstrap. It could still be the right
container on the right network — the host port is only how the editor is reached
— but that has not been established.

Settling it takes one command:

```
docker ps --format "{{.Names}}\t{{.Ports}}"
docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' <n8n container> <orchestrator container>
```

If the two containers do not share a network, the workflow is correct and the
deployment is wrong, and no amount of workflow editing will fix it.

## Readiness observed at the same time

From `/health`, recorded because it changes earlier findings:

- **All seven providers now report ready**, `grok` included. The provider audit
  earlier today recorded `grok` as NOT READY; that finding is superseded.
- `paid_runs_enabled: false`. The intake attestation headers are therefore not
  yet verified — `intake_authentication` falls back to `BEARER_DRY_RUN`. They
  become mandatory the moment paid runs are enabled.
- `new_intake_owner: "langgraph"`, `legacy_new_intake_disabled: true`,
  `intake_fencing_generation: 77`. New intake is routed to the LangGraph primary
  application; the legacy path is fenced.
- `primary_runtime_active: false`, `effect_executor_ready: false`,
  `reconciliation_worker_ready: false`, with
  `reconciliation_hold_reason: "historical_parallel_plan_requires_reconciliation"`
  and one held historical parallel plan. **If `create_intake` raises under those
  conditions, `/v1/intake` returns 503** — which the corrected relay would
  surface as 502 rather than swallow. This has not been observed, because
  observing it requires a valid bearer.
- `parallel_dispatch.kill_switch_open: false` — the kill switch is **engaged**.
  The corrected status workflow alarms on this; the previous revision watched a
  key `/health` does not return and would have stayed silent.
- `ambiguous_effect_count: 0`. Rollback is not blocked.
- `provider_activation.combined_live_ready: false`. Live activation remains shut.

## What is not claimed

No provider ran. No task was created: every probe was rejected at the
orchestrator's authentication boundary by design, so nothing was written. A
relay that returns 202 would prove the request was accepted for planning — not
that any work occurred.

Producer of the corrected candidate is Claude, which is therefore ineligible to
review or accept it. The probes above are testing, not review.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 20:05 UTC | END]] -->
