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
  and the authenticated orchestrator service.
- The bridge uses OpenAI Responses without tools and with response storage disabled.
- It cannot create Director work orders, write repositories, delegate providers,
  approve, review, merge, deploy, purchase, or perform other external actions.
- The Slack gateway does not receive `OPENAI_API_KEY`; only the orchestrator does.
- Prompt and output sizes, requests per minute, conservative estimated cost per
  request, and a UTC daily estimated-cost ceiling are configurable.
- A Slack source-event reservation is durable. A completed redelivery performs no
  provider call and posts no second Slack answer. An ambiguous failed request is
  not retried automatically.
- Raw prompts and answers are not persisted in the bridge ledger; it stores only
  routing metadata, a request digest, status, timestamps, and estimated cost.

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

The reply must appear in the originating thread under Aesop Director's bridge
label. Repeat delivery of the same Slack event must not call OpenAI or post again.

## Failure behavior

Non-owner, non-allowlisted, bot-authored, edited, and deleted events are ignored.
Invalid configuration, rate or daily-budget exhaustion, provider failure, and an
event-identity conflict fail closed. The Slack gateway posts a bounded `BLOCKED`
status without exposing the prompt, API response, or credential.

**[[Codex | GPT-5.6 | 2026-08-23 | 08:12 UTC | END]]**
