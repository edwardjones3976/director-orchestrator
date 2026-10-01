<!-- [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | BEGIN]] -->
<!-- Administrative attribution only; material implementation remains Cursor-authored. -->
<!-- [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:43 UTC | BEGIN]] -->
<!-- [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:19 UTC | BEGIN]] -->
# Slack status receipts (Issue #230)

Director-owned status receipts are separate from work intake. Only explicit
`DIRECTOR` prose or `/director` starts planning, assignments, or provider
dispatch. A non-command owner app mention in a thread that is already bound to
exactly one LangGraph-primary task is recorded as a digest-only status receipt
through `POST /v1/status-receipts`.

## API surface

- `GET /v1/slack-thread-bindings?channel_id=&thread_ts=` resolves `task_id` from
  the persisted primary task projection intake binding. Unknown threads return
  **404**; ambiguous threads (more than one task share the binding) return
  **409**. Client-supplied task IDs do not authenticate a binding.
- `POST /v1/status-receipts` accepts `SlackStatusReceipt` (source event identity,
  channel, thread, owner user, task id, `text_digest`, `received_at`) and returns
  `StatusReceiptRecord`. The Slack gateway must supply the same signed
  attestation pattern as intake (`X-Director-Status-Receipt-*` headers). Raw
  status text is never persisted.
- Receipt persistence takes a per-`source_event_id` advisory lock and a
  per-thread lock before re-reading the intake binding inside the same
  transaction. That recheck linearizes receipt inserts against binding drift; it
  does not serialize primary task projection creation, which uses separate
  control-plane locks.

## Gateway routing

The Slack gateway resolves the binding with the GET endpoint, then posts the
signed receipt. Overlapping `message` and `app_mention` deliveries reconcile on
the canonical `channel:ts` source-event identity. A failed HTTP write releases
the in-memory claim so Slack redelivery can reach the durable receipt store.

## Provider lanes and passive status (September 14 investigation)

The table summarizes the retained
[provider status-mention investigation](file:///<report-path>)
plus its Grok addendum. Without a demonstrated route-level result, native
zero-new-work behavior is **unsupported/unverified**; use the controlled
Director status-receipt path above.

| Lane / contact route | Native Slack continuation / chat | Truly passive status receipt |
| --- | --- | --- |
| OpenAI Codex (native Slack app) | Documented cloud-task entry; new task confirmed in channel evidence | **Unsupported** — not reliable |
| OpenAI ChatGPT alias / Responses bridge | Opening alias only; inline mentions ignored | **Unsupported** for passive inbox |
| OpenAI ChatGPT app / cloud teammate | Sidebar chat; workspace agents may start work | **Unverified** for channel mentions |
| Claude | Code + Chat modes; session-linked replies observed | **Unsupported** for passive receipt |
| Cursor | Thread-associated Cloud Agent continuation documented | **Unsupported** for passive receipt |
| Direct Grok / xAI | Thread-aware replies documented | **Unverified** locally; not passive guarantee |
| Gemini / Antigravity | No demonstrated inbox in project channel | **Unsupported/unverified** |
| Perplexity | Ongoing Computer task continuation documented | **Unsupported** for zero-work receipt |
| Firecrawl | No verified inbound mention route | **Unsupported/unverified** |
| GitHub Copilot / GitHub Slack app | Existing Slack Code session updates documented | **Unverified** without session proof |
| Grok Bot (Cursor product) | Native agent conversation; inbound Slack unverified | **Unsupported/unverified** |
| Z.ai | API/SDK lane; no enrolled Slack identity | **Unsupported/unverified** |
| VS Code Coding Bot contact route | Editor host contact; no verified Slack listener | **Unsupported/unverified** |

Distinctions:

- **Native Slack continuation** — vendor app resumes chat or an existing agent
  session; may avoid a new task object but still performs inference or tool use.
- **Chat / inference** — model consumes the update and may reply; not zero work.
- **Truly passive status receipt** — records the update without new tasks,
  assignments, provider attempts, or inference; Director status receipts target
  this property for the owner gateway path only.

The separate ChatGPT bridge scope is unchanged: alias parsing, approval, and
Responses dispatch remain outside status receipt intake.
<!-- [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:19 UTC | END]] -->
<!-- [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:43 UTC | END]] -->

<!-- [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | END]] -->
