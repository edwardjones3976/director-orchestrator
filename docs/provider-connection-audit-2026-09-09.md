<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 17:40 UTC | BEGIN]] -->
# Provider connection audit — 2026-09-09

Evaluated `Settings.readiness()` and each lane's readiness function against the
deployment environment file. Presence and length only; no credential value was
read into output, and nothing was changed — every remediation below is
owner-reserved.

## Result

`readiness()` is the repository's own definition of "connected". Six of seven
keys are true.

| Key | State | Basis |
| --- | --- | --- |
| `slack` | **READY** | app token, bot token, and a gateway secret ≥32 chars distinct from the shared secret |
| `openai_director` | **READY** | `OPENAI_API_KEY` present |
| `cursor` | **READY** | `CURSOR_API_KEY` present; repository and project allowlists set |
| `claude` | **READY** | `CLAUDE_ROUTINE_URL` and `CLAUDE_ROUTINE_TOKEN` present — the two fields `readiness()` actually reads |
| `gemini` | **READY** | all eleven `gemini_supplemental_readiness` checks pass |
| `perplexity` | **READY** | enabled, credential present, `research_only` mode by default |
| `grok` | **NOT READY** | see below |

**A near-miss worth recording.** `ANTHROPIC_API_KEY` is present but empty, which
looks like a broken Claude lane and is not one. `readiness()["claude"]` reads
`claude_routine_url` and `claude_routine_token`, because the Claude lane
dispatches through the Claude Code Routine rather than the raw Anthropic API.
The empty key is unused by that path. This audit initially recorded it as a
defect and corrected before reporting — the same restrictive-reading error
pattern recorded in the compliance audit.

## The one real defect: Grok is enabled without a credential

```
GROK_ENABLED           = true
GROK_DAILY_BUDGET_USD  = 5.00
GROK_API_KEY           = absent
GROK_REPOSITORY_TRANSPORT = absent
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
| **ChatGPT Slack alias bridge** | `CHATGPT_BRIDGE_ENABLED`, `..._ALIAS_USER_ID`, `..._CHANNEL_ALLOWLIST`, `..._INTERNAL_TOKEN` | The orchestrator cannot drive `@ChatGPT` in Slack. Part of why bus posts go unanswered |
| **GitHub credentials** | `GITHUB_APP_ID`, `GITHUB_TOKEN`, `ORACLE_REVIEW_SECRET` | **The orchestrator cannot read a pull request or post a review.** Every review must therefore be performed by an owner-launched agent; the automated review-to-merge path cannot operate at all |
| **Firecrawl** | `FIRECRAWL_API_KEY` | The repository research lane. The Director-side MCP surface is separate and works |
| **GitHub Copilot** | `COPILOT_GITHUB_TOKEN` | The Lite hygiene lane; fails closed as unavailable |
| **Microsoft 365 Copilot** | tenant and token file | Already known; blocked on tenant administration |

## Why bus posts go unanswered — the honest reading

Two Director posts on 2026-09-09 and an availability check on 2026-09-08 all went
unanswered. The channel history shows every provider exchange being initiated by
the owner from a desktop client. Combined with the absent ChatGPT bridge and the
absent GitHub credentials, the conclusion is that **the Slack identities are not
autonomous listeners on this deployment.** Posting to the bus records a
delegation durably and correctly; it does not by itself cause a provider to act.

That is a configuration fact, not a provider failure, and it means the practical
dispatch path today is owner-launched sessions with the bus as the durable
record. The delegation packets are written for exactly that.

## What was not done

No credential was created, modified or requested. No environment file was edited.
No value was read into output. Every remediation above requires the owner.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 17:40 UTC | END]] -->
