<!-- [[Codex | GPT-6 Astra | 2026-09-14 | 03:39 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-13 | 23:03 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:38 UTC | BEGIN]] -->
**[[Codex | GPT-5.6 | 2026-08-23 | 21:49 UTC | BEGIN]]**
**[[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | BEGIN]]**
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
  depth: a distinct `SLACK_GATEWAY_SECRET` HMAC binds every request, delivery
  acknowledgement, and ambiguity record to its Slack message identity, action,
  prompt digest, and age.
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
  delivery acknowledgement clears that ciphertext. Startup and bounded periodic
  cleanup physically wipe ciphertext after its one-hour recovery TTL. Rotating
  either the gateway or internal bearer secret invalidates pending encrypted
  recovery material.

## Configuration

For bridge-only paid answers while the main provider runtime stays offline, use
the following settings in the ignored deployment environment file. See
[ChatGPT bridge activation](chatgpt-bridge-activation.md) for switch behavior,
disable instructions, and release verification.

```dotenv
ALLOW_PAID_RUNS=false
CHATGPT_BRIDGE_ENABLED=true
CHATGPT_BRIDGE_ALLOW_PAID_RUNS=true
CHATGPT_BRIDGE_SLACK_USER_ID=U0B9KFJPST1
CHATGPT_BRIDGE_CHANNEL_IDS=C0BQ6GSCNLR,C0BAV9CMC80
CHATGPT_BRIDGE_MAX_PROMPT_CHARS=4000
CHATGPT_BRIDGE_MAX_OUTPUT_TOKENS=600
CHATGPT_BRIDGE_REQUESTS_PER_MINUTE=4
CHATGPT_BRIDGE_ESTIMATED_COST_USD=0.10
CHATGPT_BRIDGE_DAILY_BUDGET_USD=2.00
```

`CHATGPT_BRIDGE_ALLOW_PAID_RUNS` defaults to `false`. Paid bridge execution
requires either this bridge-specific opt-in or the existing global
`ALLOW_PAID_RUNS=true`; the global switch is not required for bridge-only answers.
The bridge-specific flag leaves the main provider runtime gates unchanged. A valid
`OPENAI_API_KEY`, the configured owner ID, and membership of the Aesop Director app
in every allowlisted private channel remain required. Keep the bridge disabled
until provider-side billing controls are set. Neither paid switch replaces the
exact signed owner approval required for each request.

Recreate the orchestrator and Slack gateway after changing the environment. Check
`GET /health`: every `chatgpt_bridge` readiness field must be true. Then send one
bounded message from the configured owner in an allowlisted channel:

```text
@ChatGPT Reply with exactly: BRIDGE_OK
```

Address the alias at the start of the message, optionally after other Slack user
mentions in the opening address list. Mentions inside prose, quotes, code, or
connector attribution footers do not invoke this bridge. This applies to request,
APPROVE, and RECOVER commands. When paid execution is disabled, an intentional
request receives a static PAUSED notice and does not dispatch a provider.

The first bridge response must be a deterministic approval instruction and must
not call OpenAI. Reply in that thread using the exact form it supplies:

```text
@ChatGPT APPROVE CBR-<opaque-reference> Reply with exactly: BRIDGE_OK
```

Only that matching approval may call OpenAI. Aesop attempts the Slack answer post
once. Slack does not provide an exactly-once guarantee for a timed-out
`chat.postMessage`, so the bridge never performs an automatic second post after an
exception. It records the delivery as ambiguous and keeps only the encrypted answer.
The owner may then authorize a budget-neutral recovery in the same thread:

```text
@ChatGPT RECOVER CBR-<opaque-reference> Reply with exactly: BRIDGE_OK
```

Recovery repeats the original prompt for digest binding, does not call OpenAI again,
and attempts one new Slack post. Because Slack may have accepted the unacknowledged
first post, this explicit recovery can create a duplicate. Repeating `APPROVE` does
not release the answer, duplicate recovery events do not release the answer again,
and a delivered replay is a no-op. Every recovery Slack event is durably consumed: if its post is
ambiguous, another attempt requires a newly signed `RECOVER` message with a new Slack
timestamp. Redelivery of the already-consumed event never releases the answer again.

## Failure behavior

Failed dispatches retain a nullable `failure_diagnostics_json` value on the existing
`chatgpt_bridge_requests` row. The additive column contains only controlled stage,
category and exception-class labels, HTTP status, allowlisted provider error
code/type values, and a request ID matching `req_` plus 32 lowercase hexadecimal
characters. Unknown codes and IDs are omitted; provider error JSON is inspected
only for bodies of at most 16 KiB. Prompts, answers, exception messages, raw bodies,
URLs and header collections are never copied into diagnostics. Diagnostics stay
in the local store and do not change the HTTP or Slack response contracts.

Empty output and incomplete responses without text have separate failure categories;
this diagnostic change preserves existing handling of responses containing text.
Failure metadata is committed with the terminal transition and survives reopening
the store. It does not release the estimated reservation or authorize another
dispatch. If diagnostic extraction fails, the terminal transition still runs; if
the store itself fails, the original exception is preserved and the existing
`DISPATCHING` reservation still prevents redispatch. Older failures have no new
metadata: their original provider error cannot be reconstructed by this change.

Non-owner and non-allowlisted alias mentions are not intercepted by the bridge and
continue through the ordinary Director routing rules. Bot-authored, edited, and
deleted events are ignored.
Invalid configuration, rate or daily-budget exhaustion, provider failure, a stale
or invalid gateway proof, a mismatched approval, and an event-identity conflict
fail closed. An ambiguous orchestrator transport result gets one same-identity
recovery request. If that retry observes a completed answer awaiting delivery, the
gateway records ambiguity and posts the signed `RECOVER` instruction instead of
silently dropping the result. An unresolved dispatch gets an honest `PENDING` status.
An ambiguous answer post never
causes a contradictory `BLOCKED` post or an automatic Slack retry; encrypted recovery
remains available until acknowledged or its bounded recovery window expires. A
startup and 60-second periodic sweep moves abandoned acknowledgement waits to the
explicit recovery state and physically wipes expired ciphertext.

Slack may deliver one message through both the `app_mention` and generic `message`
handlers. The gateway claims the canonical channel-plus-message-timestamp identity
only after the owner/channel/alias checks, so that duplicate ingress produces one
backend request and one Slack response without suppressing unrelated messages.

**[[Codex | GPT-5.6 | 2026-08-23 | 08:12 UTC | END]]**
**[[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | END]]**
**[[Codex | GPT-5.6 | 2026-08-23 | 21:49 UTC | END]]**

<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:38 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-13 | 23:03 UTC | END]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-14 | 03:39 UTC | END]] -->
