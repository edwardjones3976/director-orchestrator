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

`grep -ri 'grok[ _-]*bot'` over `src/` and `docs/` returns nothing. The project
has never had a Grok Bot concept, so there is nothing to disentangle — there is
something to *add*, if the owner wants that lane.

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

`GROK_API_KEY` is read nowhere. The lane is credentialed by **`XAI_API_KEY`**,
which is present and non-empty, which is why `/health` now reports
`providers.grok: true`. A remediation instruction naming `GROK_API_KEY` would
have set a variable the code never reads and left the lane closed while
appearing to fix it.

The audit's second named variable, `GROK_REPOSITORY_TRANSPORT`, is also not a
real setting. Repository transport readiness is computed from the `GROK_CLI_*`
group — `GROK_CLI_ENABLED`, `GROK_CLI_EXECUTABLE`, its pinned
`..._SHA256`, `..._REPOSITORY_MAP_JSON`, `..._TIMEOUT_SECONDS`, `..._MAX_TURNS`
— and is **required only when** `GROK_ALLOW_REPOSITORY_WRITE` or
`GROK_ALLOW_REVIEW_VERDICT` is true. Both default false, so with tools and
repository writes off, the lane is ready without any CLI at all.

## Routing consequence, 2026-09-09

Cursor's allowance is exhausted: Cursor Models 100%, Other Models 100%, and
on-demand at **$68.12 against a fixed $66 cap** — over the ceiling, not near it.
Cursor Models reset 17 September. Grok Bot's weekly allowance is at **0%** and
resets 15 September.

Two things follow.

**1. The Cursor agent-launch failures are explained.** Every Director mention
produced `The agent failed to start. Please try again.` within seconds, three
times out of three. A hard spend ceiling that has already been passed produces
exactly that: instant, deterministic, identical. **Pressing Try Again will not
help while the cap is exceeded** — an earlier recommendation to press it is
withdrawn. The fix is to raise or unset the monthly limit, or wait for the
reset.

**2. Cursor is not a usable reviewer this week; Grok is.** `primary_roster.py`
puts `grok` in `PRIMARY_TEAM`, `IMPLEMENTERS` **and** `REVIEWERS`, and its lane
is ready and billed to a different account entirely. Moving the pending
independent review to Grok is a routing decision the roster already permits, not
a workaround.

## What using Grok Bot would require

It cannot simply be selected. There is no Grok Bot transport, and it is a Cursor
surface rather than an API this orchestrator dispatches to. Standing it up needs
three owner decisions, none of them delegable:

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
lane**, which is ready, separately billed, and eligible to review.

## What is not claimed

No credential was created, retrieved, modified or read. `XAI_API_KEY` is reported
as present and longer than twenty characters; its value was never read into any
output. No provider ran. Grok Bot's own usage figures come from the owner's
screenshot of the Cursor spending page, not from any measurement this session
made.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 21:35 UTC | END]] -->
