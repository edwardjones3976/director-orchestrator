<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 22:36 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 18:04 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-10 | 03:25 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:55 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 20:05 UTC | BEGIN]] -->
# Director Intake ↔ n8n — integration validation, 2026-09-09

> ## Correction, 21:55 UTC — the measurements below were taken against the wrong instance
>
> Every probe row in "What was measured" that names port **45678** measured
> `edge-engine-director-n8n-1` on the `edge-engine-director_default` network,
> whose backend is the **Edge Engine** Director at host port 18082. Confirmed
> independently: `http://127.0.0.1:18082/health` reports `"environment":
> "edge-engine"` with every provider false, where the Director-Orchestrator
> stack on 8080 reports `"environment": "local"` with all seven true.
>
> **The Director's own n8n is port 5678**, on `director-orchestrator_default`,
> resolving the orchestrator at 8080. That is the instance the gateway uses and
> the editor to open.
>
> This was established by Codex under V-01 — the host measurement this document
> flagged as the one fact it could not establish. The process worked: the gap was
> named as unproven, assigned, and closed.
>
> **The defect itself reproduces on the correct instance.** Codex's V-02, run at
> 5678 with a valid bearer, recorded 422 `extra_forbidden` direct from the
> orchestrator and **200 with zero bytes** through the relay, with that same
> upstream 422 present in the n8n container logs. So the diagnosis holds and the
> backend is reachable; what does not hold is that this document's numbers were
> evidence for it. They were evidence about a different project's relay.
>
> Retained rather than deleted, because the mistake is the instructive part: an
> owner-supplied URL was treated as identifying the right service, and the port
> not matching any documented namespace was recorded as a curiosity instead of
> being resolved before measuring.

Historical status at the time of the probes: the relay did not satisfy the
Slack bot's contract. The instance identity question below was subsequently
resolved by V-01, and V-02 reproduced the contract failure on the Director
instance. The cause of the specific 200/empty response remains unresolved; this
record does not establish the current deployed revision or current health.

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
status".

**Corrected 2026-09-10 — the fit is not exact, and the original wording
overstated it.** That reference also states that a workflow which *errors*
before the first Respond node returns **500**. The pre-fix workflow used
`onError: stopWorkflow`, so the error path would predict 500, yet 200 was
measured. The measured status matches the completion path; the measured
zero-byte body matches neither path's documented output.

**This is an unresolved discrepancy and is recorded as one.** Why the deployed
instance answered 200 rather than 500 is *not* established by these
observations — whether n8n classified the stopped execution as finished, whether
the installed version behaves differently from the current documentation, or
something else, was never determined. Do not infer a cause from the status.
Both observations stand as measured. Neither is the contract, and the corrected design must not assert a
body shape.

The workflow sets `saveDataErrorExecution: "none"`. This requests cleanup of
completed error-execution data; it does not prove that execution data was never
persisted or that container logs contain no evidence. V-02 found the upstream
422 in the n8n logs. The corrected intake candidate retains completed-execution
cleanup rather than adding durable records of forwarded authorization headers.

## What the deployed workflow gets right

Worth stating plainly, because the repository candidate had it backwards: the
deployed workflow is a **pass-through relay**. It forwards
`{{ JSON.stringify($json.body ?? $json) }}` unchanged, forwards the caller's
`Authorization` header and the three `X-Director-Intake-*` attestation headers,
and returns the orchestrator's body verbatim with a 202. That is the correct
architecture. It does not configure an orchestrator credential of its own, but
forwarded bearer headers pass through n8n and may be stored initially. Completion
cleanup is not a guarantee of zero storage.

The repository candidate on `claude/n8n-v2` rebuilt the body from a raw Slack
event envelope that never arrives. It has been corrected to the deployed design,
plus the error branch. Completed execution retention remains disabled.

## Superseded question — resolved by V-01 and V-02

The following question and proposed probes are retained as historical context.
They are no longer pending operator instructions: port 45678 belongs to Edge
Engine; Director n8n is 5678, and its backend reachability was demonstrated.

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

The originally proposed host probes were:

```
docker ps --format "{{.Names}}\t{{.Ports}}"
docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' <n8n container> <orchestrator container>
```

The proposed inference above was too strong: a network mismatch alone would
not prove the workflow correct. V-02 instead established backend reachability
and a relay contract failure on the correct Director instance.

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
  conditions, `/v1/intake` returns 503.** The prediction first written here —
  that the corrected relay would surface that as 502 — was **wrong**, and is
  corrected on 2026-09-10: intake is reserved before later operations can fail,
  so a task may exist behind a 503. The corrected relay routes 5xx to the
  ambiguity branch for reconciliation, not to 502. This has not been observed,
  because observing it requires a valid bearer.
- `parallel_dispatch.kill_switch_open: false` — the kill switch is **engaged**.
  The corrected status workflow alarms on this; the previous revision watched a
  key `/health` does not return and would have stayed silent.
  *(Correction 2026-09-12: The rule changed in #197; it speaks when the kill switch is engaged while parallel dispatch is enabled.)*
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
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:55 UTC | END]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-10 | 03:25 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 18:04 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 22:36 UTC | END]] -->
