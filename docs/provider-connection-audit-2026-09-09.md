<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:00 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 17:40 UTC | BEGIN]] -->
# Provider connection audit — 2026-09-09

> **Three findings in this audit were corrected at 21:00 UTC. Read the corrections before relying on anything below:** Grok is now ready, the ChatGPT bridge row was wrong, and the nonresponse section was wrong. Each is marked in place.

Evaluated `Settings.readiness()` and each lane's readiness function against the
deployment environment file. Presence and length only; no credential value was
read into output, and nothing was changed — every remediation below is
owner-reserved.

## Result

`readiness()` is the repository's own definition of "connected". Six of seven
keys were true at 17:40 UTC.

| Key | State | Basis |
| --- | --- | --- |
| `slack` | **READY** | app token, bot token, and a gateway secret ≥32 chars distinct from the shared secret |
| `openai_director` | **READY** | `OPENAI_API_KEY` present |
| `cursor` | **READY** | `CURSOR_API_KEY` present; repository and project allowlists set |
| `claude` | **READY** | `CLAUDE_ROUTINE_URL` and `CLAUDE_ROUTINE_TOKEN` present — the two fields `readiness()` actually reads |
| `gemini` | **READY** | all eleven `gemini_supplemental_readiness` checks pass |
| `perplexity` | **READY** | enabled, credential present, `research_only` mode by default |
| ~~`grok`~~ | **SUPERSEDED — now READY** | See the correction below |

**Grok correction, 21:00 UTC.** `/health` on the running deployment reports
`providers.grok: true`. **All seven providers are ready.** The Grok defect
recorded below was real when measured and has since been remediated by the
owner; it is retained for the record, not as a live finding.

**A near-miss worth recording.** `ANTHROPIC_API_KEY` is present but empty, which
looks like a broken Claude lane and is not one. `readiness()["claude"]` reads
`claude_routine_url` and `claude_routine_token`, because the Claude lane
dispatches through the Claude Code Routine rather than the raw Anthropic API.
The empty key is unused by that path. This audit initially recorded it as a
defect and corrected before reporting — the same restrictive-reading error
pattern recorded in the compliance audit.

## The one real defect: Grok is enabled without a credential — RESOLVED

```
GROK_ENABLED           = true
GROK_DAILY_BUDGET_USD  = 5.00
GROK_API_KEY           = absent      <- resolved by 21:00 UTC
GROK_REPOSITORY_TRANSPORT = absent   <- resolved by 21:00 UTC
```

`grok_primary_readiness` therefore fails on `credential_configured` and
`repository_transport_ready`, so `readiness()["grok"]` is false. The lane is
switched on and budgeted while being unable to execute. Failing closed is
correct behaviour; the inconsistency is that the roster advertises a lane that
cannot act.

**Remediation, owner-only, pick one:**

1. Add `GROK_API_KEY` and `GROK_REPOSITORY_TRANSPORT`. The remaining defaults are
   already correct — `GROK_API_BASE` defaults to the official `api.x.ai`
   endpoint, `GROK_MODEL` to `grok-4.6`, and `GROK_MODE` to
   `cursor_family_primary`.
2. Set `GROK_ENABLED=false` so the roster states the truth until a credential
   exists.

Leaving it as-is is the one option to avoid: an enabled, budgeted, uncredentialed
lane will be counted in the roster and will fail at dispatch.

**Scope of this finding.** It governs *orchestrator-dispatched* Grok work. A Grok
session the owner launches directly is a different surface and is unaffected —
the same two-surface distinction that applies to Firecrawl.

## Transports that are entirely unconfigured

Not defects — nothing claims they are configured — but each one closes a path
that has been discussed as if it were available.

| Transport | Variables absent | What it closes |
| --- | --- | --- |
| **Host provider bridge** | `DIRECTOR_HOST_PROVIDER_BRIDGE_SECRET`, `..._URL`, `..._POSTGRES_DSN` | Grok's digest-verified CLI contact, the Antigravity team worker, and the Copilot packet wrapper. **D-11 and D-14 are blocked on infrastructure that does not exist yet, not merely on a decision** |
| ~~**ChatGPT Slack alias bridge**~~ | — | **Retracted 2026-09-09 21:00 UTC. This row was wrong.** `/health` reports `chatgpt_bridge.enabled: true` with `slack_transport_configured`, `internal_auth_configured`, `alias_user_configured` and `channels_allowlisted` all true, and every `CHATGPT_BRIDGE_*` key is present. The bridge is configured |
| **GitHub credentials** | `GITHUB_APP_ID`, `GITHUB_TOKEN`, `ORACLE_REVIEW_SECRET` | **The orchestrator cannot read a pull request or post a review.** Every review must therefore be performed by an owner-launched agent; the automated review-to-merge path cannot operate at all |
| **Firecrawl** | `FIRECRAWL_API_KEY` | The repository research lane. The Director-side MCP surface is separate and works |
| **GitHub Copilot** | `COPILOT_GITHUB_TOKEN` | The Lite hygiene lane; fails closed as unavailable |
| **Microsoft 365 Copilot** | tenant and token file | Already known; blocked on tenant administration |

## Why delegations do not get picked up — corrected 2026-09-09 21:00 UTC

**The earlier version of this section was wrong, and wrong in the direction that
makes a working component look broken.** It stated that two Director posts on
2026-09-09 and the availability check on 2026-09-08 "all went unanswered", and
concluded that the Slack identities are not autonomous listeners. That
conclusion was drawn from the channel's top-level view. **The threads were never
opened.**

They were not unanswered. Cursor replied to every one of them, in thread, within
seconds:

| Director post | Cursor reply | Latency |
| --- | --- | --- |
| 2026-09-09 02:58:15 PDT | `The agent failed to start. Please try again.` | 18 s |
| 2026-09-09 09:18:23 PDT | same | 7 s |
| 2026-09-09 09:23:38 PDT | same | 13 s |

Three for three, the same message, always within seconds. That is not silence
and it is not nonresponse. **Cursor is wired to this channel, is receiving the
mentions, and is being triggered by them; the agent process fails to start.**

This inverts the practical reading:

- The Slack bus **is** a live dispatch surface. The mention reaches Cursor.
- A-18 and A-19 were never declined and never ignored. They have never started.
- The failure is a Cursor agent-launch fault, and each reply carries a **Try
  Again** button, so the cheapest next action is an owner press, not a
  reassignment.
- The nonresponse counter in the operating charter must not be advanced for
  these three. A launch failure is a transport fault, not a provider decision,
  and counting it toward "three nonresponses ⇒ mark unavailable" would retire a
  provider that never received the work.

**Method note, recorded because the error is the reusable part.** The claim was
asserted from a channel listing that shows reply counts but not replies. Reply
counts were visible on all three posts. The rule that follows: a nonresponse
finding requires opening the thread, and a thread that shows any reply count is
not evidence of silence.

## What was not done

No credential was created, modified or requested. No environment file was edited.
No value was read into output. Every remediation above requires the owner.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 17:40 UTC | END]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:00 UTC | END]] -->
