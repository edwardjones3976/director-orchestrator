**[[Codex | GPT-5.6 | 2026-08-23 | 08:12 UTC | BEGIN]]**

# ChatGPT Slack alias bridge

The official ChatGPT Slack app is used through its dedicated App/DM/sidebar. It
does not provide a documented shared-channel `@ChatGPT` bot invocation. This
bridge lets the owner mention the official ChatGPT Slack user ID in specifically
allowlisted channels while Aesop Director posts the resulting answer honestly as:

```text
Aesop Director — ChatGPT bridge
```

It does not impersonate the OpenAI-managed Slack identity.

## Boundary

- Exact owner ID and exact channel allowlist are checked by both the Slack gateway
  and the authenticated orchestrator service. The shared bearer is defense in
  depth: a distinct `SLACK_GATEWAY_SECRET` HMAC binds every request and delivery
  acknowledgement to its Slack message identity, action, prompt digest, and age.
- The bridge uses OpenAI Responses without tools and with response storage disabled.
- It cannot create Director work orders, write repositories, delegate providers,
  approve, review, merge, deploy, purchase, or perform other external actions.
- The Slack gateway does not receive `OPENAI_API_KEY`; only the orchestrator does.
- Prompt and output sizes, requests per minute, conservative estimated cost per
  request, and a UTC daily estimated-cost ceiling are configurable.
- The first signed mention always stops at `OWNER_DECISION_REQUIRED` and performs
  no provider call. The owner must send a second signed `APPROVE` message that
  references the opaque pending identity and repeats the exact prompt.
- The approval transition is atomic. Duplicate initial or approval events cannot
  dispatch the provider twice.
- Raw prompts are never persisted. An undelivered completed answer is retained only
  as size-bounded AES-GCM ciphertext with request-bound associated data. A signed
  delivery acknowledgement clears that ciphertext. Rotating either the gateway or
  internal bearer secret invalidates pending encrypted recovery material.

## Configuration

Set these values in the ignored deployment environment file:

```dotenv
CHATGPT_BRIDGE_ENABLED=true
CHATGPT_BRIDGE_SLACK_USER_ID=U0B9KFJPST1
CHATGPT_BRIDGE_CHANNEL_IDS=C0BQ6GSCNLR,C0BAV9CMC80
CHATGPT_BRIDGE_MAX_PROMPT_CHARS=4000
CHATGPT_BRIDGE_MAX_OUTPUT_TOKENS=600
CHATGPT_BRIDGE_REQUESTS_PER_MINUTE=4
CHATGPT_BRIDGE_ESTIMATED_COST_USD=0.10
CHATGPT_BRIDGE_DAILY_BUDGET_USD=2.00
```

`ALLOW_PAID_RUNS=true`, a valid `OPENAI_API_KEY`, the configured owner ID, and
membership of the Aesop Director app in every allowlisted private channel are also
required. Keep the bridge disabled until provider-side billing controls are set.

Recreate the orchestrator and Slack gateway after changing the environment. Check
`GET /health`: every `chatgpt_bridge` readiness field must be true. Then send one
bounded message from the configured owner in an allowlisted channel:

```text
@ChatGPT Reply with exactly: BRIDGE_OK
```

The first bridge response must be a deterministic approval instruction and must
not call OpenAI. Reply in that thread using the exact form it supplies:

```text
@ChatGPT APPROVE CBR-<opaque-reference> Reply with exactly: BRIDGE_OK
```

Only that matching approval may call OpenAI. The answer uses a deterministic Slack
`client_msg_id`; ambiguous Slack delivery is retried at most once with the same
identity. Until delivery is acknowledged, repeating the exact approval may recover
the encrypted answer without another provider call. A delivered replay is a no-op.

## Failure behavior

Non-owner, non-allowlisted, bot-authored, edited, and deleted events are ignored.
Invalid configuration, rate or daily-budget exhaustion, provider failure, a stale
or invalid gateway proof, a mismatched approval, and an event-identity conflict
fail closed. An ambiguous orchestrator transport result gets one same-identity
recovery request and then an honest `PENDING` status. An ambiguous answer post never
causes a contradictory `BLOCKED` post; encrypted recovery remains available until
acknowledged or its bounded recovery window expires.

**[[Codex | GPT-5.6 | 2026-08-23 | 08:12 UTC | END]]**
