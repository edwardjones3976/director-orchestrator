<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:55 UTC | BEGIN]] -->
# Primary lane staging worksheet

**Purpose:** bring `langgraph_primary.configuration_valid` from false to true on
this deployment. Staging only. No activation switch is flipped, no provider is
dispatched, and the legacy Director remains sole writer throughout.

**Status of this document:** the owner fills in the marked values, then the
apply step in "Pass 2" runs. Nothing here is a secret except where noted, and no
value in this file should be committed once filled.

## What is actually missing

Measured against the running deployment. Every other term in the
`langgraph_primary` health block is already true.

| Term | Why false | Fix |
| --- | --- | --- |
| `authority_root_configured` | `REPOSITORY_SNAPSHOT_ROOT` absent from `.env` | Section 1 |
| `provider_activation_bindings_configured` | `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON` parses to `{}`, so all nine lanes are missing | Section 2 |

Confirmed already correct, so no action: `ALLOW_PAID_RUNS` is a clean `false`,
and `TRUSTED_PROVIDER_COST_RANKS` is genuinely populated. Neither startup trap
(`config.py:932`, `primary_default.py:1889`) is reachable while paid runs stay
off.

---

## Section 1 — the snapshot root

Two variables, not one. They are a pair and both are required.

```
REPOSITORY_SNAPSHOT_ROOT=/repository
REPOSITORY_SNAPSHOT_HOST_PATH=<FILL IN: absolute Windows path to the snapshot>
```

`REPOSITORY_SNAPSHOT_ROOT` is the path **inside the container** and must be
`/repository`, because `docker-compose.yml` binds the snapshot to that target
read-only. `REPOSITORY_SNAPSHOT_HOST_PATH` is the host directory bound to it.

Config validation only checks non-emptiness (`config.py:1341`), but
`primary_default.py:1000` calls `Path(...).resolve(strict=True)`, so the
directory must genuinely exist inside the container or primary work will fail at
use time rather than at startup. This is why the host path matters.

### Creating the snapshot

`scripts/windows-primary-snapshot.ps1 -Action Create` builds it, and refuses to
overwrite an existing target.

```
scripts/windows-primary-snapshot.ps1 -Action Create `
  -Source <a checkout whose origin is GitHub> `
  -Target <FILL IN: the same path as REPOSITORY_SNAPSHOT_HOST_PATH> `
  -ExpectedRepository edwardjones3976/director-orchestrator `
  -StartingRef main `
  -ExpectedCommit <FILL IN: 40-hex commit> `
  -ExpectedTree <FILL IN: 40-hex tree>
```

### Known blocker on this machine

The script reads `git remote get-url origin` and normalises it by stripping only
`git@github.com:`, `https://github.com/` and `.git`. On this machine every
checkout uses the Cursor mirror as `origin`:

```
origin  https://origin.cursor.com/edward-jones/director-orchestrator.git
github  https://github.com/edwardjones3976/director-orchestrator.git
```

So the normalised value stays `https://origin.cursor.com/edward-jones/director-orchestrator`,
which does not equal `edwardjones3976/director-orchestrator`, and the script
throws **"Snapshot source origin does not match the repository allowlist."**

Choose one:

- **Use a source clone whose `origin` is GitHub** — `git clone
  https://github.com/edwardjones3976/director-orchestrator.git <path>` and pass
  that as `-Source`. No code change, and it is arguably the more honest source
  for an exact snapshot anyway.
- **Teach the script the `github` remote** — a small reviewed change to accept a
  configurable remote name. Preferable if this recurs, but it edits an
  activation-path script and should be its own pull request.

---

## Section 2 — provider activation bindings

Nine lanes, six fields each, 54 values. The binding key set must **exactly
equal** the roster (`config.py:1360-1363`); a missing or extra lane fails the
whole check.

Current roster (`LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS`):

```
openai, claude, cursor, grok, antigravity, perplexity, firecrawl,
github-copilot, microsoft-365-copilot
```

These are attestations about really-installed software. They are deliberately
left blank rather than filled with plausible values, because a binding that
records a version nobody verified is worse than an empty one — it looks like
evidence.

Paste as **one line** into `.env`:

```json
{"openai":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""},"claude":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""},"cursor":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""},"grok":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""},"antigravity":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""},"perplexity":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""},"firecrawl":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""},"github-copilot":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""},"microsoft-365-copilot":{"workspace_id":"","provider_version":"","app_version":"","runtime_adapter_version":"","workspace_version":"","binding_version":""}}
```

### All 54 values are required — verified

`ProviderActivationBinding.__post_init__` validates every field against
`^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$` (`provider_activation.py:21,62`). Tested
directly against that class:

| Value | Result |
| --- | --- |
| `""` | **rejected** — "must be a bounded, non-secret identifier" |
| `" "` | **rejected** |
| `"1.2.3"` | accepted |
| `"a/b"` | accepted |

So the skeleton above will not load as-is, and there is **no partial-staging
path**: `configuration_valid` cannot flip until all 54 fields carry real values.
A field may contain letters, digits, and `. _ : / -`, must start alphanumeric,
and is capped at 128 characters. Dots, colons and slashes are allowed, so
ordinary version strings and namespaced workspace identifiers both fit.

Note also that this validator is what rejects secrets by construction — the
character class excludes most token punctuation. These fields are identities,
not credentials, and nothing here should be a secret.

---

## Section 3 — host provider bridge

Three variables are absent. The endpoint and secret are already present.

```
DIRECTOR_HOST_PROVIDER_COMMANDS_JSON=<FILL IN: see below>
DIRECTOR_HOST_PROVIDER_BRIDGE_STORE=<FILL IN: absolute path for the claim store>
DIRECTOR_HOST_PROVIDER_POSTGRES_DSN=<FILL IN: DSN for the writer-fence check>
```

The bridge allowlists only four providers and explicitly refuses two
(`scripts/windows-host-provider-bridge.ps1`):

- Allowed: `claude`, `grok`, `antigravity`, `github-copilot`
- Refused outright: `cursor`, `microsoft-365-copilot`

So the command map covers at most those four. For Grok specifically the entry
points at the host executable, which is what lets the Linux container reach a
Windows binary at all:

```json
{"grok": ["C:\\Users\\edwar\\.grok\\bin\\grok.exe", "..."]}
```

Preflight resolves each command with `Get-Command` and throws if any is
unavailable, so a wrong path fails loudly rather than at dispatch time.

### Lane authentication, and a gap preflight does not cover

`host_provider_bridge.py:52-57` defines which auth variables the bridge forwards
to each lane. Nothing outside this allowlist reaches the provider process:

| Lane | Variables forwarded |
| --- | --- |
| `claude` | `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` |
| `grok` | `XAI_API_KEY` |
| `antigravity` | `GEMINI_API_KEY`, `GOOGLE_API_KEY` |
| `github-copilot` | `GH_TOKEN`, `GITHUB_TOKEN` |

`_SAFE_OS_ENV` additionally forwards `USERPROFILE`, `APPDATA` and
`LOCALAPPDATA`, so a CLI that keeps its own credential store can still find it.

**Preflight does not check any of this.** It resolves the executable with
`Get-Command` and reports success. A lane whose CLI is installed but signed out
therefore passes preflight cleanly and fails later, at dispatch, when it is far
more expensive to diagnose. Verify each lane can actually authenticate before
standing the bridge up.

### The `claude` lane is unauthenticated on this host today

Measured 2026-09-04. `C:\Users\edwar\.claude\.credentials.json` holds a
`claudeAiOauth` record whose `accessToken` and `refreshToken` are both
zero-length and whose `expiresAt` is `0`, while the surrounding metadata
(`scopes`, `subscriptionType`, `refreshTokenExpiresAt` of 2026-09-15) is intact.
`cmdkey /list` shows no Claude or Anthropic entry, so nothing is held in Windows
Credential Manager either, and neither `ANTHROPIC_API_KEY` nor
`CLAUDE_CODE_OAUTH_TOKEN` is set. `claude.exe` itself is present and on PATH.

So the lane has no authentication path by either route. Fix before staging, by
one of:

- Sign the CLI back in from an interactive terminal (`claude`, then `/login`).
  The refresh-token expiry is still in the future, so this is an ordinary
  sign-in.
- Set `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` in the environment the
  bridge process runs under, which satisfies the table above without touching
  the CLI's own credential store.

The same file shows all 14 `mcpOAuth` entries with empty tokens. Those are a
separate concern, are not fixed by the sign-in above, and are not required for
staging.

`LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET` must be at least 32 characters
(`config.py:1470`) and **pairwise distinct** from every other primary ingress
secret (`config.py:1475-1487`) — the shared secret, Slack gateway secret,
adapter callback, independent review evidence, CI evidence, security evidence
and effect reconciliation secrets. The bridge script re-checks this itself and
refuses to start on a collision.

---

## Pass 2 — apply

1. Back up `.env` to a timestamped copy. This is the entire rollback plan.
2. Write Sections 1-3 into `.env`. Change nothing else. `LANGGRAPH_PRIMARY_ENABLED`,
   `LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED` and `ALLOW_PAID_RUNS` all
   stay `false`.
3. `docker compose up -d orchestrator`.
4. `scripts/windows-host-provider-bridge.ps1 -Action Preflight` — validates and
   reports "no provider was invoked". Remember this proves only that each
   configured executable resolves; it does not prove any lane can authenticate.
   Confirm lane authentication separately, per Section 3.

## Verification

- Diff the `langgraph_primary` block of `/health` before and after. Expect
  `authority_root_configured` and `provider_activation_bindings_configured` to
  become true, and `configuration_valid` to follow.
- Confirm what must **not** move: `paid_runs_enabled` false,
  `langgraph_primary.enabled` false, `primary_runtime_active` false, and
  `langgraph.legacy_sole_writer` still true. Staging changes readiness only,
  never who holds the pen.
- Container healthy, and `docker compose logs orchestrator` shows no startup
  raise since the restart.
- Rollback: restore the `.env` backup, `docker compose up -d orchestrator`,
  confirm `/health` matches the before-capture.

## What this does not do

Grok holds no review authority at the end of this. Staging makes the primary
lane configuration-valid. Granting a verdict additionally requires the primary
lane to be activated, which is gated behind the nine acceptance criteria in
`docs/langgraph-primary-operations.md`, of which only Gate 0 is satisfied.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:55 UTC | END]] -->
