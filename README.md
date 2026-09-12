<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 01:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:26 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:45 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:35 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 10:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 15:20 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 19:40 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | BEGIN]] -->
<!-- [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:34 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:19 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:18 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 14:02 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 12:37 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 12:18 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-23 | 21:49 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]] -->
# Director Orchestrator

<!-- DIRECTOR_OPERATING_DIRECTIVE_SHA256=cec0aca69091698bbaa02118ef5bddf7adaa9b8822dc2e6c370598d0e4a2bef4 -->

I am the Director.

One owner posts a work order in one private Slack channel. I turn it into a bounded plan,
route it to the provider lane that fits, hold each result against acceptance evidence, and
report an outcome — or stop and ask. This repository is the event-driven control plane
behind that, and it is built to stay up on an always-on Windows desktop under Docker
Desktop.

It is not built to run *unattended*, and it does not claim to. Operation is attended by
default: human-out-of-the-loop is off unless the owner configures it, paid dispatch is off,
and owner-gated work stops for a person rather than proceeding. What the design buys you is
that leaving it running is safe **because it fails closed**, not because it decides for you.

Read this before anything else: **I do not hold the authority. The deterministic policy
does.** Every plan I produce passes through the gates in
[`src/director_orchestrator/policy.py`](src/director_orchestrator/policy.py). My output may
*add* an approval requirement. It can never remove one. Paid execution remains disabled by
default and requires the existing activation gates. When a plan touches deployment,
publication, merge, deletion, purchases, credentials, or expanded access, I stop in
`OWNER_DECISION_REQUIRED` and hand it back.

That is the design, not a limitation I am working around. No model approves its own work,
and that includes me: I may write a change or I may accept one, never both.

**Claude is the acting project Director. Codex/ChatGPT provides bounded engineering
and eligible independent review.** See [current project leadership](docs/project-leadership.md)
and [Claude's project instructions](CLAUDE.md). The leadership handoff does not
attest that a deployed service has switched its executing provider.

## What I am built from

- A Slack Socket Mode gateway, so the desktop needs no public inbound port.
- A persistent Slack collaboration bus carrying delegation, building, research, evidence,
  review handoff, debate, synthesis, and completion reporting.
- A FastAPI service with idempotent intake and durable SQLite state.
- Deterministic approval gates that model output cannot remove.
- Adapters for Cursor Cloud Agents, Claude Code Routines, Gemini Antigravity Managed
  Agents, and the Perplexity Agent API.
- A structured Director interface retaining the legacy OpenAI runtime and an explicitly
  enabled Claude adapter that requires a real bound native transport.
- A Claude-led OpenAI–Claude–Cursor–Grok team, with Gemini/Antigravity eligible for its
  authorized team mode after enrollment and qualification, and Perplexity supplying bounded
  supplemental research. Membership and allocation attest nothing about whether a provider
  route is enrolled or live — that is a separate evidence chain, and it is not satisfied by
  configuration.
- Owner-approved [allocation profiles](docs/provider-allocation-profiles.md) across all eight
  provider lanes. Engineering defaults to OpenAI 40%, Claude 25%, Cursor 15%, Grok 10%, and
  Antigravity 10%; other profiles reserve real research and enterprise work. A fixed
  real-assignment twenty-slot window enforces target +5 hard caps and zero-target exclusion,
  with ±3-point normal preference. A zero-target lane is ineligible, not merely unlikely.
- Deterministic accepted-productive-work accounting, an immutable pre-stage packet, rolling
  twenty-assignment preferences, cheap-capable-model selection, and a five-action ordinary
  review-to-merge ceiling. Total effort is telemetry, and pure Director coordination creates
  no production credit — routing work is not authoring it.
- An optional GitHub Copilot hygiene review after required checks are green and the exact
  candidate is frozen. It has no assigned workload share, range, or ceiling; no provider
  adapter or automatic routing; and no production credit, gate, authority, or every-push
  trigger. Without an authenticated contact it fails closed as unavailable. A requested
  review uses at most three conditional attempt slots. Stop on success; recovery requires
  temporary failure; a third attempt requires materially new information or a substantially
  different transport. Never repeat an ambiguous paid dispatch.
- Evidence-first milestone reporting and explicit coordination-quality targets: 95%
  productive coordination, a 5% incident/replanning reserve, and a 33% reduction in
  avoidable micro/fixup commits.
- Format-aware contribution tags for human responses and safely commentable files, with
  structured and syntax-sensitive formats attributed through their submission envelope.
- A durable periodic provider-prompt monitor recording returned token metadata and posting
  bounded status summaries.
- A local n8n deployment and an importable intake workflow.
- A reusable Slack app manifest and a Claude routine prompt.
- A reusable project rollout packet recording isolated deployment, Slack, allowlist,
  activation-evidence, fallback, and rollback requirements.
- Dry-run previews that spend nothing.

Deeper operational detail lives in `docs/always-on-runbook.md`, `docs/provider-setup.md`,
`docs/slack-collaboration-bus.md`, `docs/project-bootstrap-protocol.md`,
`docs/contribution-attribution-policy.md`, `docs/provider-profiles/README.md`,
`docs/cloud-hosting-later.md`, `docs/project-uppercut-reiteration.md`, and
`docs/edge-oracle-operations.md`.

## How a work order reaches me

1. Aesop posts `DIRECTOR` in the private Slack channel.
2. The Slack gateway accepts only the configured owner and channel, ignores bot messages, and forwards a stable Slack event ID.
3. n8n passes the normalized event to the orchestrator.
4. The orchestrator deduplicates it, creates a task record, applies hard limits, and builds a preview or live plan.
5. I open one persisted collaboration thread, post each bounded delegation, and relay concise structured results — never raw provider output.
6. Specialist runs dispatch sequentially unless the plan explicitly marks work independent. Medium-to-critical plans may include independent positions and one rebuttal round.
7. Results are judged against acceptance checks. Revision loops are capped. Advisory votes use fixed weights, and synthesis preserves dissent without creating action authority.
8. Slack receives collected evidence, a review handoff, completion state, or one narrowly scoped owner decision. Approval-gated plans carry a confirm button; ambiguity requires a written `/director approve DIR-… <clarification>`.
9. In-progress tasks resume after a restart without redispatching an already-recorded provider run.
10. With live execution enabled, the usage monitor prompts each configured provider no more often than the configured interval and persists its latest status.


Claude keeps Moderator, Participant, and Director project functions separate. Moderation
uses neutral framing, gathers independent positions before cross-exposure when
practical, and performs a counter-bias check that holds the vote back so it cannot
pre-empt synthesis. Claude then contributes one participant position and casts its one
existing advisory vote at full weight or records an explicit abstention. Moderating
a debate never costs Claude its right to vote; only Claude's own abstention forgoes
exercising it. Director synthesis and tie handling create no extra vote and never fabricate
participation.
Milestone updates lead with evidence, exact candidate identity, blockers, and one
next action rather than activity summaries.

Repository oversight remains scoped. OpenAI production uses a separately assigned,
attributed lane under the frozen allocation profile; Director coordination creates
no production credit. Material authorship removes independent acceptance and merge
eligibility for that candidate. Different-provider exact-candidate review, security,
CI and action-specific approval remain mandatory. Profile selection grants no
external-action authority or subscription-to-API entitlement.
Prepared but undelivered content is labeled
`DRAFT â€” NOT SENT`; offline exercises use
`SIMULATION â€” NO PROVIDER INTERACTION`; hypothetical provider views use
`PREDICTED POSITION â€” PROVIDER HAS NOT RESPONDED` and never count as participation
or a vote. Alternatives are recorded only when credible and decision-relevant.

The 5% coordination reserve covers checks, drift, evidence gaps, recovery,
process failures, and infrastructure variance; it is not planned idle time. The
33% avoidable micro/fixup reduction is measured against a rolling comparable
baseline with the baseline, current result, delta, exclusions, and window recorded.
Provider selection records the eligible candidate set, exclusions, trusted comparable
costs, selected provider/model, and deterministic tie-break reason. Routing first
requires scope, capability, readiness, risk, and reviewer independence; applies
total-effort eligibility and corrective under-band preference before
direct-production rank; then uses trusted estimated cost and the deterministic
tie-breaker. Missing cost
telemetry is unknown, never zero. Paid routing therefore requires an operator-supplied
`TRUSTED_PROVIDER_COST_RANKS` JSON object with one unique positive rank for every
configured ready specialist; lower ranks are cheaper, and there is no default order.
Reports distinguish accepted productive units from prospectively guarded total
effort and categorize rejected, unusable, quota-blocked, duplicate,
recovery, and ambiguous work as waste. They report estimated ROI, provider-reported
cost ROI only when correlated cost exists, first-pass yield, correction rate, and
duplicate-validation waste without inventing telemetry.
Milestone records include project/assignment IDs; starting/current SHA or digest;
categorized status; authenticated participation; workload deviation; OpenAI
functions and dissent; tests, CI, security, and review; risks; cost/tokens/time;
commit-efficiency measurement; scope or authority changes; recovery/rollback;
next gate; owner decisions; and explicit not-done/unverified items.

External actions require exact stored owner authorization for the named action
and scope plus all current deterministic gates. Neither browser content nor
provider output creates authority.

## Run me on a Windows desktop

Prerequisites: Docker Desktop with Linux containers, and PowerShell 7 or Windows PowerShell 5.1.

```powershell
git clone https://github.com/edwardjones3976/director-orchestrator.git
cd director-orchestrator
powershell -ExecutionPolicy Bypass -File .\scripts\windows-bootstrap.ps1
```

The bootstrap script writes `.env` with random local secrets and starts the stack. It creates and stores no provider credentials.

Open `http://localhost:5678`, create the local n8n owner login, and import `n8n/workflows/director-intake.json`. Activate that workflow only after you have inspected it.

For always-on operation: enable Docker Desktop's start-on-sign-in, disable sleep and hibernation while plugged in, keep Windows and Docker auto-restart enabled, and put a UPS behind the machine if you intend to treat it as infrastructure. Containers run `restart: unless-stopped`, and in-progress provider polls resume from durable task state after a restart.

### Default LangGraph-primary database (inactive by default)

Compose provisions `langgraph-primary-db` and its
`langgraph-primary-db-data` volume separately from n8n's database and volume.
The orchestrator waits for this database's readiness check, but the database
does not make LangGraph primary: `LANGGRAPH_PRIMARY_ENABLED=false`,
`LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED=false`, and `ALLOW_PAID_RUNS=false` keep
new intake, provider calls, and external effects closed. Before any supervised
activation, replace both primary placeholders through the approved secret store,
keep `LANGGRAPH_PRIMARY_POSTGRES_SCHEMA=director_primary_v1`, and give every
deployment a distinct versioned `LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE`.

The bundled database intentionally uses unencrypted container-network transport
(`?sslmode=disable`). `.env.example` ships
`LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES=false`, and the checked-in
`docker-compose.yml` overrides it to `"true"` for the orchestrator service
alone. That override is the sole container-only exception: it is accepted only
for the exact Compose service hostname `langgraph-primary-db`, and every other
non-loopback target still fails closed. It remains a
fail-closed staging and backup surface, not an activation configuration,
because `LANGGRAPH_PRIMARY_ENABLED=false`,
`LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED=false`, and `ALLOW_PAID_RUNS=false` keep
the runtime closed. Do not carry the local
exception into any other deployment. A supervised activation must instead supply
a trusted Postgres endpoint with `sslmode=verify-full`, an explicit trusted root
certificate, and a matching versioned schema and checkpoint namespace.

`LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED` and
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED` are mutually exclusive. Live
dispatch additionally requires paid runs and an exact non-secret activation
binding for every required provider. Each binding is only an identity/version
tuple; authentication secrets remain in the provider's approved credential
store. Observed app and runtime-adapter evidence must both reach `LIVE_OK`
before that provider can cross the primary dispatch boundary.

Paid-off project canaries may set both
`LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA` and
`LANGGRAPH_PRIMARY_PINNED_TREE_SHA` to exact 40-hex Git object IDs. This narrow
mode still requires the configured origin repository identity, a clean
worktree, and exact HEAD, starting-ref, and computed-tree matches, but it does
not require a remote-tracking ref. Leave both values empty for the normal
origin-tracking branch check; a missing, malformed, or mismatched pin fails
startup.

Back up the Default primary database with the activation switches still off:

```powershell
$env:DIRECTOR_ENV_FILE = ".env"
$backupName = "director-primary-$(Get-Date -Format yyyyMMdd-HHmmss).dump"
New-Item -ItemType Directory -Force ".\backups" | Out-Null
docker compose --env-file .env exec -T langgraph-primary-db pg_dump --username=director_primary --dbname=director_primary --format=custom --file="/tmp/$backupName"
docker compose --env-file .env cp "langgraph-primary-db:/tmp/$backupName" ".\backups\$backupName"
docker compose --env-file .env exec -T langgraph-primary-db rm -f "/tmp/$backupName"
```

Restore only during a supervised maintenance window. Keep both primary switches
and paid execution off, stop every Director client of the database, and restore
one explicitly named backup. n8n is not stopped or restored because it uses a
different database and volume.

```powershell
$env:DIRECTOR_ENV_FILE = ".env"
$backupName = "director-primary-YYYYMMDD-HHMMSS.dump"
docker compose --env-file .env stop orchestrator slackbot
docker compose --env-file .env cp ".\backups\$backupName" "langgraph-primary-db:/tmp/$backupName"
docker compose --env-file .env exec -T langgraph-primary-db pg_restore --username=director_primary --dbname=director_primary --clean --if-exists --no-owner --no-privileges --exit-on-error --single-transaction "/tmp/$backupName"
docker compose --env-file .env exec -T langgraph-primary-db rm -f "/tmp/$backupName"
docker compose --env-file .env up -d orchestrator slackbot
docker compose --env-file .env ps
```

Treat backup files as sensitive operational data. Store them encrypted, restrict
access, verify a disposable restore before relying on them, and never commit them.

## Slack app setup

1. In Slack app management, create an app from `slack-app-manifest.yaml` in the `Edge_Engine` workspace.
2. Install it to the workspace.
3. Generate an app-level token with `connections:write` for Socket Mode.
4. Put the app token in `SLACK_APP_TOKEN` and the bot token in `SLACK_BOT_TOKEN` inside `.env`.
5. Invite the app to the private `#project-director` channel.
6. Generate a distinct `SLACK_GATEWAY_SECRET` for the local Slack gateway. Do
   not reuse the public orchestrator bearer secret.
7. Restart only the Slack gateway: `docker compose restart slackbot`.

Never paste tokens into Slack, chat, source control, screenshots, or issue bodies.

**[[Codex | GPT-5.6 | 2026-08-23 | 08:31 UTC | BEGIN]]**
The official ChatGPT Slack app remains a dedicated App/DM experience. To let the
owner use the visible `@ChatGPT` identity inside an allowlisted project channel,
enable Aesop Director's bounded alias bridge. Aesopâ€”not the OpenAI-managed botâ€”posts
the threaded answer with an honest bridge label only after a second signed owner
approval. Undelivered answers remain recoverable as bounded ciphertext until Slack
delivery is acknowledged. After an ambiguous Slack post, only an explicit owner
`RECOVER` command may make one budget-neutral delivery attempt; Slack may already
have accepted the first post, so recovery can duplicate it. The bridge never retries
that external side effect automatically, and each newly authorized retry requires a
new signed Slack event; replaying a consumed recovery event cannot release the answer
again. The bridge has no work-order,
tool, repository, delegation, merge, deployment, or purchasing authority. See
[the ChatGPT Slack alias bridge runbook](docs/chatgpt-slack-alias-bridge.md).
**[[Codex | GPT-5.6 | 2026-08-23 | 08:31 UTC | END]]**

## Prove I am safe before you pay me

Keep `ALLOW_PAID_RUNS=false`, then post:

```text
DIRECTOR
Project: director-orchestrator
Outcome: Produce a dry-run plan for adding a /version health field.
Constraints: Do not edit GitHub and do not call paid providers.
```

Expect a task ID, a preview plan, and `OWNER_DECISION_REQUIRED` because live execution is disabled. No provider should run. If one does, stop and report it — that is a defect in the gate, not a surprise to work around.

## Turning on spending

Add credentials one provider at a time, and verify each adapter independently before setting `ALLOW_PAID_RUNS=true`.

1. `OPENAI_API_KEY` and the configured Director model.
2. At least one specialist credential.
3. A repository URL and starting branch in the work order.
4. Provider-side spending limits.
5. Exact `PROJECT_ALLOWLIST`, `REPOSITORY_ALLOWLIST`, and `ALLOWED_STARTING_REFS` values for this deployment profile.
6. A separate `CLAUDE_CALLBACK_SECRET`, installed in both the orchestrator and the trusted Claude callback sender, when callbacks are enabled.
7. `ALLOW_PAID_RUNS=true`, then one low-risk test task.

OpenAI Pro 20x, Claude Max 5x, Cursor, Gemini, and Perplexity subscriptions do not necessarily include API usage. Claude Code Routine triggers are the exception represented here: they use the routine-specific URL and token supplied by Claude's routine UI.

## Watching the meter

Periodic prompts are opt-in. Once provider-side spend limits exist, set `USAGE_CHECK_ENABLED=true` to schedule a short availability prompt instructing the provider to use no tools and do no project work. The default list is the primary team — OpenAI, Claude, Cursor; add Gemini and Perplexity explicitly to monitor their supplemental lanes. Checks are additionally gated by `ALLOW_PAID_RUNS=true`, and either switch being false prevents every monitor-initiated model call. The default interval is six hours, providers prompted sequentially rather than simultaneously, with an unbypassable 15-minute per-provider minimum.

The monitor records provider-returned token metadata, provider-reported USD cost, bounded retry count, and normalized quota errors in SQLite, then posts a concise summary to `#project-director`. **It never invents a dollar estimate when a provider omits cost telemetry.** A model reply is an availability signal, not proof of an account or subscription balance — Cursor documents plan usage in its dashboard, and other providers may expose authoritative quota only in their own consoles.

Authenticated operator endpoints:

```text
GET  /v1/usage-checks/latest
POST /v1/usage-checks/run
```

The manual POST respects both safety switches and the 15-minute minimum, and returns a structured body:

- `202` `accepted` with `cycle_id` and `scheduled_providers` when work will run
- `200` `not_due` with `cooling_down_providers` when nothing is due under the minimum interval
- `409` while paid execution or periodic checks are disabled, or when a cycle is already `busy`

Wait only on the providers named in `scheduled_providers`. Treat `not_due` as an explicit no-new-work answer, and still prove every cooling primary provider has a recent `ok`/`prompted` record before claiming readiness.

## Execution profiles and handoffs

Live plans fail closed unless their project is listed in `PROJECT_ALLOWLIST`. Every explicit repository and starting ref must match `REPOSITORY_ALLOWLIST` and `ALLOWED_STARTING_REFS`, and Cursor assignments must always name a repository so they cannot fall back to an account-level default. Use separate deployments and databases when projects need different repositories, credentials, budgets, or Slack destinations. Every project presented to me receives a rollout packet; concurrently active deployments require distinct Slack apps, project channels, deployment profiles, and state namespaces. Pending identities permit analysis-only shaping but block Slack activation.

After an assignment is accepted, the orchestrator persists a bounded outcome carrying its result, review summary, and evidence. Later assignments receive prior accepted outcomes and their declared `depends_on` results inside a marked untrusted-data envelope. Missing, forward, or unknown dependencies block execution rather than being quietly ignored.

Claude completion callbacks authenticate with `CLAUDE_CALLBACK_SECRET`, not the general orchestrator bearer secret. A callback must identify the exact pending Claude `run_id`; duplicate identical callbacks are idempotent, and conflicting results are rejected.

## Cloud hosting later

The desktop design deliberately keeps Slack on Socket Mode and state local. To migrate:

- Move orchestrator state from SQLite to managed PostgreSQL.
- Put n8n and the orchestrator behind HTTPS with restricted ingress.
- Replace long-lived cloud credentials with OIDC or narrowly scoped service credentials where supported.
- Use a managed secret store, encrypted backups, centralized logs, uptime monitoring, and provider webhook verification.
- Pin container images by version or digest before production.
- Keep the Slack channel, task schema, approval policy, and provider adapters unchanged.

Do not expose the local n8n editor or the orchestrator port directly to the internet.

## Project Uppercut reiteration


Project Uppercut runs as a separate Compose project using `.env.uppercut`, dedicated ports, volumes, task state, Slack routing, budgets, and callback secrets. The supervised Windows script never edits the primary `.env`. See [the Uppercut runbook](docs/project-uppercut-reiteration.md). The broader LangGraph migration is planned separately in [PR #18](https://github.com/edwardjones3976/director-orchestrator/pull/18) so this remediation stays focused.

Edge Oracle uses the same isolated bootstrap through a separate `.env.edge-oracle`,
Compose namespace, ports, database, Slack route, and allowlists. Its optional exact-SHA
merge gate is disabled and kill-switched by default, accepts only Oracle-scoped paths,
and is reached only through an authenticated, exact-assignment Claude callback or the
local audit CLI rather than a public merge API. See
[Edge Oracle operations](docs/edge-oracle-operations.md).

## Local validation

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
.\scripts\windows-postgres-test-lane.ps1 -Action Start
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\scripts\windows-postgres-test-lane.ps1 -Action Stop
.\.venv\Scripts\python.exe -m json.tool n8n\workflows\director-intake.json > $null
.\.venv\Scripts\python.exe scripts\render_provider_profiles.py --check
$env:DIRECTOR_ENV_FILE = ".env.example"
docker compose --env-file .env.example config --quiet
```

The Postgres-backed suites (`tests/test_langgraph_postgres.py` and the
`tests/test_primary_*` modules) require `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN`
and fail rather than skip when it is absent. `-Action Start` runs the same
disposable container CI uses: `postgres:17-alpine` pinned by digest, published
only on `127.0.0.1:55433`, trust auth for the throwaway `director_ci` role, no
volume, and a bounded `pg_isready` wait. It sets
`LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN=postgresql://director_ci@127.0.0.1:55433/director_test?sslmode=disable`
and `LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER=director-primary-test-pg` in the
current shell, so `tests/test_primary_backup_restore.py` runs `pg_dump` and
`pg_restore` inside the container and no host PostgreSQL install is needed. Run
it from the shell that runs the tests. `windows-postgres-test-lane.ps1 -Action Test` starts the lane, runs the
suite (narrow it with `-TestNames tests.test_langgraph_postgres`), and removes
the container even when tests fail. `-Action Stop` removes the container;
`-Action Status` and `-Action Dsn` inspect it or re-export the variables in a
new shell.

The script labels every container it creates and refuses to stop, replace, or
reuse any container it did not create, including one already holding port
55433. It never reads `.env`, never starts Compose services, and never touches
the primary database service or its volume. Trust auth is loopback-only and the
database is disposable: never load real data into it and never point the lane at
any other database. The script runs under PowerShell 7 and Windows PowerShell
5.1.

## LangGraph transition planning

The gated incremental migration is documented in [docs/langgraph-transition-plan.md](docs/langgraph-transition-plan.md). The owner-accepted provider-neutral documentation/schema authority is in [docs/model-interoperability-contract.md](docs/model-interoperability-contract.md), with versioned work/result schemas and checked examples under [docs/schemas](docs/schemas/).

Gate 0 was accepted on 2026-08-16. Later phases remain individually gated and preserve the current Slack/API surface, provider adapters, deterministic approval policy, single-writer rule, and rollback path. No LangGraph runtime transition begins merely because the documentation/schema authority is merged.
<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-23 | 21:49 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 12:18 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 12:37 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 14:02 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:19 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:34 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:18 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:22 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]] -->
<!-- [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 19:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 15:20 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 10:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:35 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:26 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 01:40 UTC | END]] -->
