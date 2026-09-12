<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:43 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:40 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:25 UTC | BEGIN]] -->
> **Historical record restored on 2026-09-12.** The observations and corrections below
> describe the author's 2026-09-09 session. They are not a current health check,
> route qualification, allocation decision, authorization, or activation receipt.
> Retained Microsoft 365 references describe the retired historical deployment;
> PR #161 retired that integration. Current authority is the
> [team operating charter](team-operating-charter.md),
> [provider allocation profiles](provider-allocation-profiles.md), and
> [LangGraph primary operations](langgraph-primary-operations.md).
> Verify current state through the [Witness Ledger](state-witness.md) rather
> than treating a historical readiness claim as evidence of today's deployment.

<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:35 UTC | BEGIN]] -->
# Grok, Cursor Grok, and Grok Bot are three different things

The owner asked for these to be kept distinct in the project. They already are
distinct in billing and transport; what is not distinct is the **naming**, and
one setting name actively implies the opposite of what the code does.

## The three entities

| Name | What it actually is | Whose quota it spends | Present in this repository |
| --- | --- | --- | --- |
| **Grok (xAI)** | Direct HTTPS to `https://api.x.ai/v1`, model `grok-4.6`, authenticated with an xAI key | **xAI account.** Nothing to do with Cursor | **Yes** — the `grok` lane |
| **Cursor Grok** | A model offered inside Cursor, counted in Cursor's "Cursor Models" allowance | Cursor subscription | **No** |
| **Grok Bot** | A separate Cursor product with its own weekly allowance, tracked separately from Cursor Models | Cursor subscription, **separate bucket** | **No** |

The 2026-09-09 author reported that a search of that checkout found no Grok Bot
references. This dated inventory is superseded: the current tree records Grok Bot
as a distinct contact in the Grok profile and charter.

## The naming trap

```
grok_mode: str = os.getenv("GROK_MODE", "cursor_family_primary")
```

`cursor_family_primary` reads like "route Grok through Cursor". **It does not.**
It is a role label only. It reaches the provider as

```
"role": "primary_cursor_family_teammate"
```

inside the request packet, and the request goes to `api.x.ai`. `providers.py`
verifies the model against `{grok_api_base}/models` and fails with "Configured
Grok model is unavailable to the authenticated **xAI** key". The single place
`grok_mode` is enforced is `grok_primary_readiness`, which merely requires the
string to equal `cursor_family_primary`.

**Consequence:** the `grok` lane is already fully independent of Cursor's
allowance. Exhausting Cursor does not exhaust Grok, and spending on Grok does not
touch Cursor. Only the setting's name suggests otherwise.

## Correction to the provider connection audit

That audit named the wrong environment variable. It reported `GROK_API_KEY` as
the absent credential. The code reads a different name:

```
grok_api_key: str = os.getenv("XAI_API_KEY", "")
```

`Settings.grok_api_key` reads **`XAI_API_KEY`** for xAI API readiness.
`GROK_API_KEY` is also a recognized child-environment transport alias; it must
not be described as unused throughout the code. The historical API-readiness
observation is not proof of repository-review transport qualification.

The audit's second named variable, `GROK_REPOSITORY_TRANSPORT`, is also not a
real setting. Repository transport readiness is computed from the `GROK_CLI_*`
group — `GROK_CLI_ENABLED`, `GROK_CLI_EXECUTABLE`, its pinned
`..._SHA256`, `..._REPOSITORY_MAP_JSON`, `..._TIMEOUT_SECONDS`, `..._MAX_TURNS`
— and is **required only when** `GROK_ALLOW_REPOSITORY_WRITE` or
`GROK_ALLOW_REVIEW_VERDICT` is true. Both default false, so with tools and
repository writes off, the lane is ready without any CLI at all.

## Routing consequence, 2026-09-09

The session reported provider availability constraints. Private account spend,
quota and reset details are deliberately omitted from this repository record.
Launch errors alone did not establish their cause, and they did not authorize a
billing change or another attempt.

**Review eligibility was not proved.** General `providers.grok` readiness and
membership in the reviewer roster do not qualify the repository review route.
That route separately requires `GROK_ALLOW_REVIEW_VERDICT`, an enabled pinned
CLI and an explicit repository mapping. The historical session did not provide
that complete qualification evidence, so its proposed reroute remains unproved.

## What using Grok Bot would require

It cannot simply be selected. There is no Grok Bot transport, and it is a Cursor
surface rather than an API this orchestrator dispatches to. Standing it up needs
three questions requiring scoped authority and verified capability:

1. **How the orchestrator reaches it.** If it is only usable from inside Cursor's
   own agent surface, it belongs in the same category as an owner-launched
   session: a real dispatch path, but not one this service drives.
2. **Whether it becomes a lane.** `LANES` in `allocation_profiles.py` is a fixed
   tuple, and every allocation profile carries one target per lane. Adding a lane
   changes every profile.
3. **Credentials.** Owner-reserved. This session neither creates, retrieves,
   handles nor stores a credential of any kind.

Until those are settled, "prioritize Grok Bot" is recorded as an owner
preference, and the nearest thing the project can act on today is the **xAI Grok
lane**. Its repository-review qualification must be established separately.

## What is not claimed

No credential was created, retrieved, modified or read. `XAI_API_KEY` is reported
as present and longer than twenty characters; its value was never read into any
output. No provider ran in that historical session. Private account figures
are not retained in this document.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:35 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:25 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:43 UTC | END]] -->
