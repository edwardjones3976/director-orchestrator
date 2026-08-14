# Director Orchestrator

A bounded, event-driven control plane for coordinating specialist AI agents from a private Slack channel. The first deployment target is an always-on Windows desktop using Docker Desktop. Paid execution is disabled by default.

## What is included

- A Slack Socket Mode gateway, so the desktop does not need a public inbound port.
- A FastAPI orchestration service with idempotent task intake and durable SQLite state.
- Deterministic approval gates that model output cannot remove.
- Adapters for Cursor Cloud Agents, Claude Code Routines, Gemini Antigravity Managed Agents, and the Perplexity Agent API.
- An OpenAI Director planner/reviewer using Structured Outputs.
- A primary OpenAI–Claude–Cursor team, with Claude Pro 20x as the coding/general workhorse and Gemini/Perplexity retained as optional specialists.
- A durable periodic provider-prompt monitor that records returned token metadata and posts bounded status summaries.
- A local n8n deployment and an importable intake workflow.
- A reusable Slack app manifest and a Claude routine prompt.
- Dry-run previews for testing without provider spending.

Detailed operations notes live in `docs/always-on-runbook.md`, `docs/provider-setup.md`, `docs/cloud-hosting-later.md`, and `docs/project-uppercut-reiteration.md`.

## Architecture

1. Aesop posts `DIRECTOR` in the private Slack channel.
2. The Slack gateway accepts only the configured owner and channel, ignores bot messages, and forwards a stable Slack event ID.
3. n8n passes the normalized event to the orchestrator.
4. The orchestrator deduplicates it, creates a task record, applies hard limits, and builds a preview or live Director plan.
5. Specialist runs are dispatched sequentially unless the plan explicitly marks independent work.
6. Results are reviewed against acceptance checks. Revision loops are capped.
7. Slack receives the terminal state or a narrowly scoped owner decision request. Approval-gated plans include a confirm button; ambiguity requires a written `/director approve DIR-… <clarification>` response.
8. In-progress tasks resume after an orchestrator restart without redispatching an already-recorded provider run.
9. When live execution is enabled, the usage monitor prompts each configured provider no more often than the configured interval and persists its latest status.

## Windows desktop quick start

Prerequisites: Docker Desktop with Linux containers and PowerShell 7 or Windows PowerShell 5.1.

```powershell
git clone https://github.com/edwardjones3976/director-orchestrator.git
cd director-orchestrator
powershell -ExecutionPolicy Bypass -File .\scripts\windows-bootstrap.ps1
```

The bootstrap script creates `.env` with random local secrets and starts the stack. It does not create or store provider credentials.

Open `http://localhost:5678`, create the local n8n owner login, and import `n8n/workflows/director-intake.json`. Activate that workflow after inspection.

For always-on operation, enable Docker Desktop's start-on-sign-in option, disable sleep/hibernation for the host while plugged in, keep Windows and Docker auto-restart behavior enabled, and use a UPS if this desktop is expected to act as infrastructure. Containers use `restart: unless-stopped`; in-progress provider polls resume from durable task state after the orchestrator restarts.

## Slack app setup

1. In Slack app management, create an app from `slack-app-manifest.yaml` in the `Edge_Engine` workspace.
2. Install it to the workspace.
3. Generate an app-level token with `connections:write` for Socket Mode.
4. Put the app token in `SLACK_APP_TOKEN` and the bot token in `SLACK_BOT_TOKEN` inside `.env`.
5. Invite the app to the private `#project-director` channel.
6. Restart only the Slack gateway: `docker compose restart slackbot`.

Never paste tokens into Slack, chat, source control, screenshots, or issue bodies.

## Safe smoke test

Keep `ALLOW_PAID_RUNS=false`, then post:

```text
DIRECTOR
Project: director-orchestrator
Outcome: Produce a dry-run plan for adding a /version health field.
Constraints: Do not edit GitHub and do not call paid providers.
```

The expected result is a task ID, a preview plan, and `OWNER_DECISION_REQUIRED` because live execution is disabled. No provider should run.

## Enabling live execution

Add credentials one provider at a time. Verify each adapter independently before setting `ALLOW_PAID_RUNS=true`.

1. `OPENAI_API_KEY` and the configured Director model.
2. At least one specialist credential.
3. A repository URL and starting branch in the work order.
4. Provider-side spending limits.
5. `ALLOW_PAID_RUNS=true`, followed by a low-risk test task.

ChatGPT Plus, Claude Pro, Cursor Pro, Gemini subscriptions, and Perplexity Pro do not necessarily include API usage. Claude Code Routine triggers are the exception represented here: they use the routine-specific URL and token supplied by Claude's routine UI.

## Periodic provider usage checks

`USAGE_CHECK_ENABLED=true` schedules a short, tool-free availability prompt for the configured check list. The default list is the primary team: OpenAI, Claude, and Cursor. Gemini and Perplexity can be added explicitly when their supplemental lanes should also be monitored. Checks are additionally gated by `ALLOW_PAID_RUNS=true`; the dry-run default prevents every model call. The default interval is six hours, with providers prompted sequentially rather than simultaneously.

The monitor stores provider-returned token metadata in SQLite and posts a concise status summary to `#project-director`. A model reply is an availability signal, not authoritative proof of an account or subscription balance. Cursor documents plan usage in its dashboard, and other providers may likewise expose authoritative quota only in their account consoles.

Authenticated operator endpoints:

```text
GET  /v1/usage-checks/latest
POST /v1/usage-checks/run
```

The manual POST respects both safety switches and returns `409` while paid execution or periodic checks are disabled.

## Cloud hosting later

The desktop design intentionally keeps Slack on Socket Mode and stores state locally. For cloud migration:

- Move the orchestrator state from SQLite to managed PostgreSQL.
- Put n8n and the orchestrator behind HTTPS with restricted ingress.
- Replace long-lived cloud credentials with OIDC or narrowly scoped service credentials where supported.
- Use a managed secret store, encrypted backups, centralized logs, uptime monitoring, and provider webhook verification.
- Pin container images by version or digest before production.
- Keep the Slack channel, task schema, approval policy, and provider adapters unchanged.

Do not expose the local n8n editor or orchestrator port directly to the internet.

## Project Uppercut reiteration

After the shared Director system passes its smoke test, create a separate Project Uppercut deployment configuration. Reuse the engine, but give it its own repository allowlist, Slack thread/channel policy, provider budgets, acceptance checks, and secrets. Do not commingle Project Uppercut credentials or project data with the shared defaults.

## Local validation

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m json.tool n8n/workflows/director-intake.json > /dev/null
docker compose config
```
