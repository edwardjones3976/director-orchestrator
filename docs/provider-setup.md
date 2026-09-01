<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:34 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:19 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:13 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | BEGIN]] -->
# Provider Setup Matrix

The cross-provider responsibility, debate, advisory-voting, and strict
Perplexity evidence boundaries are defined in
[`team-operating-charter.md`](team-operating-charter.md).

## Shared periodic usage-check behavior

The orchestrator can periodically send every configured provider a short `DIRECTOR_USAGE_CHECK` prompt. The prompt instructs the provider not to browse, call tools, inspect repositories, edit files, create branches or pull requests, or perform project work. This is an instruction rather than a provider-side sandbox for agent products such as Cursor and Gemini, so provider-side spend limits remain required. The monitor records provider-returned tokens and cost when available, bounded attempts, and normalized quota failures; it never invents missing cost telemetry.

These probes consume provider usage and are opt-in through `USAGE_CHECK_ENABLED=true`. They are also disabled by the global paid-run gate and cannot execute while `ALLOW_PAID_RUNS=false`. The default interval is 360 minutes and the enforced minimum is 15 minutes, including manual runs. `POST /v1/usage-checks/run` returns a structured schedule (`accepted` / `not_due` / `busy`) so callers know which providers were actually scheduled before waiting for records. Account dashboards, not model prose, remain authoritative for plan balances and billing.

Configure one provider at a time and leave `ALLOW_PAID_RUNS=false` until every intended adapter has passed a credential/readiness check. Subscription access and API billing are separate unless the provider explicitly says otherwise.

Before enabling live execution, configure an exact deployment profile:

- Create a project-local rollout packet using
  `docs/templates/project-bootstrap/rollout-packet.md` as the starting
  template. If another Director deployment will remain active, assign a
  distinct Slack app, project channel, deployment profile, and state namespace.
  Record app and channel IDs and readiness only; keep token values and exact
  owner-user allowlist IDs in the ignored deployment profile. Missing or reused
  identities block Slack activation while leaving analysis-only shaping
  available.
- `PROJECT_ALLOWLIST`: case-insensitive project names accepted by this deployment.
- `REPOSITORY_ALLOWLIST`: exact GitHub `owner/repository` targets; GitHub HTTPS URLs normalize to this form.
- `ALLOWED_STARTING_REFS`: case-sensitive starting branches or refs. Cursor auto-generated `cursor/...` working branches are not durable starting points; they are deleted after merge. A pre-creation "GitHub branch not found" response for one of those refs retries once against the first allowlisted ref. A follow-up against a deleted working branch starts a new agent from that same durable ref instead of resuming the dead session. Durable refs such as `main` still fail closed.
- `CLAUDE_CALLBACK_SECRET`: a dedicated callback bearer secret, stored only by the orchestrator and trusted callback sender.

An absent project allowlist blocks every live plan. Cursor cannot use an implicit dashboard repository, and model output or owner approval cannot widen these target lists.

## Owner account and routing context

- **The primary team is OpenAI, Claude, Cursor, and Grok.** These should cover planning, implementation, review, and repository execution; periodic-check membership remains independently configurable.
- **Implementation uses cheap-capable-model-first routing.** Establish scope, transport, readiness, capability, tools, context, risk, and reviewer independence; then prefer an under-target eligible provider and the lowest trusted comparable estimated cost. Missing cost is unknown, not zero. An exact trusted assignment requirement may select Claude, but workload length alone does not.
- **ChatGPT is Plus.** OpenAI remains the Director planner and acceptance reviewer, but ChatGPT Plus does not itself fund OpenAI API calls.
- **Cursor and Grok are primary Cursor-family teammates.** Both have full task-capability coverage across all eight responsibility areas and low through critical work, while preserving producer/reviewer separation and deterministic gates.
- **Gemini and Perplexity are supplemental specialists.** Their API or agent consumption must still be verified independently from consumer subscriptions, and they are not included in the default periodic-check list.

The accepted-productive-work allocation remains external OpenAI/Codex
25%, Claude 25%, Cursor 20%, Gemini/Antigravity 15%, Grok 11%, and Perplexity
4%. Total effort is also enforced prospectively: Claude 36%, Cursor 20%,
Gemini/Antigravity 20%, Grok 11%, OpenAI 8%, and Perplexity 5%, with normal
bands of 33–39%, 17–23%, 17–23%, 8–14%, 5–11%, and 1–7%, and hard ceilings
of 41%, 33%, 25%, 16%, 13%, and 9%, respectively. Cursor's 33% total-effort
ceiling is intentional; it is not the direct-production threshold.

Practical operating patterns:

1. **Cheap-capable implementation lane:** select Claude, Cursor, Grok, or an eligible bounded Antigravity lane only after capability, context, readiness, and independence; apply total-effort eligibility and corrective under-band preference before direct-production rank, then trusted cost and deterministic tie-break; retain the assigned provider's transport and authority limits.
2. **Exact-requirement lane:** when a trusted work order explicitly requires Claude or a named model profile, honor it; otherwise do not infer a provider requirement from task length.
3. **Optional specialist lane:** when materially useful, Perplexity gathers current public sources and Gemini Antigravity produces bounded subordinate artifacts. A primary model independently verifies and integrates or rejects their evidence.
4. **Graceful degradation:** periodic usage checks report availability and returned token counts. If substitution could affect scope, cost, or quality, pause for an owner decision rather than silently changing providers.

For each selection, record the eligible models, exclusions, trusted comparable
costs, selected provider/model, and tie-break reason. Track accepted productive
units, first-pass yield, correction rate, duplicate validation, and categorized
waste. Compute provider-reported cost ROI only from correlated returned cost;
never invent missing telemetry or treat it as zero.

Before enabling paid workload routing, set `PRIMARY_WORKHORSE=cheapest_capable`
and populate `TRUSTED_PROVIDER_COST_RANKS` in the ignored deployment profile as a
JSON object. Include every configured ready specialist among `claude`, `cursor`,
`gemini`, `grok`, and `perplexity`. Each value must be a unique positive integer;
lower means cheaper. The Director has no built-in provider order and fails closed
before the planning call when the setting is absent, malformed, or incomplete.
Use current operator-verified comparable cost evidence rather than copying a
sample ranking from documentation.

## Trusted OpenAI debate-turn accounting

Set `OPENAI_DEBATE_TURN_ESTIMATED_COST_USD` to one finite positive estimate for
each paid OpenAI counter-bias, synthesis, and ordinary-tie-judgment turn. The
default is `$0.05`. Provider-generated plan estimates are untrusted for budget
purposes: the Director persists and reserves the greater of the provider estimate
and this operator setting. A missing or zero plan estimate therefore cannot make a
paid debate turn free, while a higher conservative estimate is preserved. A
non-positive or non-finite configured value prevents the service from starting.

| Role | Supported bridge in this package | Required values | Completion path |
| --- | --- | --- | --- |
| Primary — Director | OpenAI Responses API with Structured Outputs | `OPENAI_API_KEY`, `OPENAI_DIRECTOR_MODEL` | Immediate planning/review response |
| Primary — full-spectrum implementation/review | Claude Code Routine API trigger | `CLAUDE_ROUTINE_URL`, `CLAUDE_ROUTINE_TOKEN` | Session URL plus callback/manual report |
| Primary — repository teammate | Cursor Cloud Agents API v1 | `CURSOR_API_KEY` | Poll durable agent run; draft PR allowed |
| Primary — Cursor-family teammate | xAI Responses API plus separately authorized Grok Build/Bot contacts | `GROK_ENABLED`, `XAI_API_KEY`, `GROK_MODEL` and the fail-closed controls below | Synchronous API response or named-worktree CLI/Bot evidence |
| Supplemental — subordinate boilerplate/ideation | Gemini Antigravity Agent over the Interactions API | `GEMINI_API_KEY`, `GEMINI_AGENT`, `GEMINI_API_REVISION` and the subordinate controls below | Poll stored background Interaction |
| Supplemental — current research | Perplexity Search API; Agent API only for the bounded availability probe | `PERPLEXITY_ENABLED`, `PERPLEXITY_API_KEY` and the research-only controls below | Synchronous ranked results |

GitHub Copilot Lite is not another provider bridge. It is an optional one-time
hygiene observation requested only after required checks are green and the exact
candidate is frozen: an unscored reporting-only 0–4% effort lane with a hard 6%
ceiling, no provider adapter or automatic routing, and no production credit,
independent acceptance role, gate, merge authority, or every-push automation.
Without an authenticated contact it fails closed as unavailable.
Quota/unavailability and rejected findings are recorded as waste.
A confirmed defect follows the normal correction path, and any substantive
Copilot-authored fix makes Copilot a material author ineligible to independently
review that candidate.

## Grok primary Cursor-family profile

The Grok adapter has two explicit transports. General analysis and debate use
the synchronous, tool-free xAI Responses API. Repository implementation and
exact-SHA review use the locally authenticated Grok Build CLI in an
operator-mapped clean named worktree. The Director never promotes API-only
output into repository or review authority. Keep both transports disabled until
their dedicated credentials, paths, and budgets are configured:

```dotenv
GROK_ENABLED=false
XAI_API_KEY=
GROK_API_BASE=https://api.x.ai/v1
GROK_MODEL=grok-4.6
GROK_MODE=cursor_family_primary
GROK_TIMEOUT_SECONDS=180
GROK_MAX_OUTPUT_TOKENS=4096
GROK_MAX_CONTEXT_CHARS=262144
GROK_DAILY_BUDGET_USD=2.00
GROK_DAILY_REQUEST_LIMIT=50
GROK_REQUEST_COST_FLOOR_USD=0.01
GROK_ALLOW_TOOLS=false
GROK_ALLOW_REPOSITORY_WRITE=false
GROK_ALLOW_REVIEW_VERDICT=false
GROK_CLI_ENABLED=false
GROK_CLI_EXECUTABLE=
GROK_CLI_EXECUTABLE_SHA256=
GROK_CLI_REPOSITORY_MAP_JSON={}
GROK_CLI_TIMEOUT_SECONDS=3600
GROK_CLI_MAX_TURNS=20
```

Readiness fails closed unless the endpoint is exactly `https://api.x.ai/v1`,
tools remain disabled for the API contact, every bound is valid, and any enabled
repository-write or review authority has a nonempty explicit CLI worktree map.
The CLI executable must be an absolute regular-file path and its exact SHA-256
must be recorded in `GROK_CLI_EXECUTABLE_SHA256`; the adapter revalidates that
digest immediately before every repository-capable dispatch.
The CLI transport is a native-host capability: a containerized Director cannot
use a Windows executable or host worktree path unless an authenticated host
runner is explicitly provided. The checked-in examples therefore keep CLI,
repository-write, and review authority disabled; operators enable them only in
an ignored native-host environment with an absolute named-worktree map.
Before the first
paid request in a process, the adapter authenticates `GET /models` and requires
the configured model ID to be present. Requests use `POST /responses` with
`tools=[]`, `store=false`, complete-or-rejected context, and no repository-write,
branch, pull-request, merge, or deployment lane. Grok Build CLI repository work
is dispatched only from an exact configured clean worktree. Exact-SHA review
verifies that both commit objects exist locally, that base is an ancestor of
head, runs in read-only plan mode, and binds that repository/base/head evidence
to the returned review artifact. Draft-PR work requires an isolated `grok/*`
worktree and may create one draft PR but may not merge it. These are transport
controls, not provider-rank limits. The synchronous API adapter never polls.
Assignment and usage-check requests share one atomic durable daily ledger, and
every Grok request reserves at least `GROK_REQUEST_COST_FLOOR_USD`.

## Perplexity research-only profile

Use a dedicated Perplexity API Platform key, not a browser session or consumer
subscription credential. Keep the key only in the ignored deployment `.env` and
start with `PERPLEXITY_ENABLED=false`:

```dotenv
PERPLEXITY_ENABLED=false
PERPLEXITY_API_KEY=
PERPLEXITY_API_BASE=https://api.perplexity.ai
PERPLEXITY_PRESET=fast-search
PERPLEXITY_MODE=research_only
PERPLEXITY_MAX_RESULTS=10
PERPLEXITY_SEARCH_CONTEXT_SIZE=high
PERPLEXITY_TIMEOUT_SECONDS=180
PERPLEXITY_DAILY_BUDGET_USD=2.00
PERPLEXITY_DAILY_REQUEST_LIMIT=100
PERPLEXITY_ALLOW_REPOSITORY_WRITE=false
PERPLEXITY_ALLOW_REVIEW_VERDICT=false
```

Normal assignments use `POST /search`. Every Perplexity assignment requires an
owner approval bound to the digest of the exact assignment; the Slack prompt
shows the complete outbound `public_research_query`, and a later query mutation
invalidates that approval. The adapter sends only the approved bounded research query and
result limit; it does not send repository context,
acceptance authority, credentials, or Slack state. Results are normalized with
the query, retrieval time, URL, page title, available publication/update dates,
supporting excerpt, source type, and explicit markers where confidence or
contradiction evidence is unavailable. OpenAI or another primary-team model
evaluates that evidence; Perplexity does not issue approval, review, or merge
verdicts.

`PERPLEXITY_SEARCH_CONTEXT_SIZE` is validated now for compatibility with an
optional future Agent API research lane, where the web-search tool supports
`low`, `medium`, or `high`. The raw Search API does not accept that field, so the
current adapter does not send it. The existing no-tools Agent API call is used
only for the bounded availability/usage prompt.

The adapter refuses to run unless every research-only control is valid. It also
rejects draft-PR authority and any assignment whose estimated cost exceeds
`PERPLEXITY_DAILY_BUDGET_USD`. It is a fail-closed per-assignment ceiling and the
usage monitor stops new probes after provider-reported daily cost reaches it.
`PERPLEXITY_DAILY_REQUEST_LIMIT` bounds daily monitor requests. Provider-reported
cost may be incomplete, so configure an API Platform budget/credit control as the
authoritative daily spend backstop. Only after billing and the dedicated
key are ready should an operator set `PERPLEXITY_ENABLED=true` and include
`perplexity` in `USAGE_CHECK_PROVIDERS` if periodic checks are desired.

## Antigravity subordinate profile

Antigravity is a subordinate Edge Oracle specialist, not a fourth primary
model. `GEMINI_MODE=antigravity_subordinate` is required for readiness, and
`GEMINI_ALLOW_REVIEW_VERDICT` and `GEMINI_ALLOW_PRIMARY_AUTHORITY` must stay
`false`; any other value fails the readiness profile closed and the adapter
refuses to dispatch before any paid call.

Deterministic policy admits an Antigravity work assignment only when its
`kind` is `boilerplate`, `scaffolding`, `low_level_implementation`, or
`abstract_ideation`, its task names that subordinate scope explicitly, it
requests no review, acceptance, merge, contract, or methodology authority, and
it does not create a draft pull request. Every admitted work assignment also
requires a later Claude, Cursor, or Grok assignment that directly depends on it and
carries the runtime-bound wording to independently reproduce the artifact and
integrate only the verified portions or reject it.

Ordinary Antigravity work dispatch sends no model-authored prose. The
assignment's task, acceptance checks, upstream handoff, owner objective, Slack
text, dependency results, and repository context remain in Director state and
never cross the Antigravity work boundary. Instead, the adapter selects one
repository-owned fixture specification using a closed lane enum plus bounded
enum and integer parameters; its closed-vocabulary validator rejects any other
outbound string.
A revision reuses that same fixed work contract and the prior sandbox
`environment_id`, failing closed rather than transmitting reviewer prose or
provisioning an unrelated sandbox. Preview routing selects Antigravity only for
an explicit boilerplate, scaffolding, low-level, mechanical, or
alternate-ideation request; other work follows cheap-capable-model-first routing.

Debate is the deliberate, narrow prose-bearing exception—not an ordinary work
packet. Antigravity may return an independent position and bounded rebuttal only
after the Director persists and dispatches a matching invitation. The separate
typed debate contract carries only the sanitized question, vote options,
accepted declared-dependency contributions, and any bounded Director revision
request. Antigravity cannot initiate, self-route, widen, or autonomously
continue a discussion. Its configured advisory weight remains advisory, and a
debate seat grants no review, acceptance, merge, deployment, publication,
external-write, access, or methodology authority.

## Gemini Interactions API contract

The Gemini adapter uses the REST Interactions API; it does not call the legacy
`generateContent` endpoint. `GEMINI_AGENT` names the managed Antigravity agent and
is sent as the request's `agent` field. New runs use `environment=remote`, while a
revision reuses the `environment_id` returned by the prior interaction together
with `previous_interaction_id`. Availability checks instead use the low-cost
`GEMINI_USAGE_MODEL=gemini-3.5-flash-lite` statelessly with `store=false`; they do
not provision an Antigravity sandbox. Explicit 429 responses receive at most
`GEMINI_USAGE_MAX_ATTEMPTS` bounded attempts, and the final normalized quota code
is persisted without provider response text or credentials.

Managed-agent Gemini calls are bounded background interactions with `store=true`.
Google requires stored state for background polling. Terminal output is read from the
`model_output` entries in the Interaction `steps` timeline, with `output_text`
accepted only as an SDK-compatible convenience field. `requires_action` and
`budget_exceeded` fail closed; the Director never loops indefinitely or treats
those statuses as successful completion.

`GEMINI_MAX_TOTAL_TOKENS` caps managed-agent runs. The monitor enforces
`GEMINI_DAILY_REQUEST_LIMIT` for its own probes and reports request, retry, token,
quota-failure, and provider-reported-cost totals. Google AI Studio billing and
quota settings remain the authoritative spend backstop when Gemini omits cost.

Gemini remains an optional supplemental specialist. Installing its developer
documentation tooling does not configure `GEMINI_API_KEY`, enable Gemini usage
checks, add Gemini to the primary team, or open the paid-run gate.

For coding agents that support MCP and portable skills, install Google's current
documentation tools outside the repository:

```powershell
codex mcp add gemini-docs --url https://gemini-api-docs-mcp.dev
npx skills add google-gemini/gemini-skills --skill gemini-interactions-api --global
```

Restart the coding-agent session after installation so it discovers the new MCP
server and skill. Confirm the MCP is enabled with `codex mcp get gemini-docs`.

## Label-triggered Codex security review

The optional `.github/workflows/codex-security-review.yml` lane runs only when
an authorized repository maintainer applies the `codex-security-review` label to
a same-repository pull request targeting `main`. Store a project-scoped OpenAI
key in the GitHub Actions secret `EDGE_ORACLE_OPENAI_API_KEY`; never reuse the
Windows Director `.env` file or expose the key to a pull-request checkout.

The workflow checks out the exact pull-request head SHA without persisted GitHub
credentials and runs the SHA-pinned Codex action with `drop-sudo` and a read-only
sandbox. Codex cannot edit, push, merge, deploy, or authorize the deterministic
Oracle merge gate. A separate publisher job receives only the GitHub permissions
needed to post the bounded advisory result. Remove and reapply the label to run a
fresh review after a new head commit; each run consumes OpenAI API usage.

## Important Claude Cowork boundary

Claude Cowork supports cloud and scheduled tasks, but the official Cowork documentation does not currently expose an on-demand inbound API trigger for a real-time Slack loop. The supported programmable bridge here is a **Claude Code Routine** with an API trigger. A Cowork project or scheduled task can be added later for recurring knowledge-work jobs, but it should report into Slack rather than being treated as a synchronous API adapter.

## Credential order

1. Create and install a project-specific Slack app from
   `slack-app-manifest.yaml`; do not reuse an app or project channel belonging
   to another concurrently active deployment.
2. Generate its Socket Mode app token and copy the bot token into the local `.env`.
3. Add the OpenAI API key and test Director planning with the specialist adapters still disabled.
4. Add one specialist credential, run a low-risk task, and verify its terminal state.
5. Repeat for each remaining specialist.
6. Set provider-side spend limits, then change `ALLOW_PAID_RUNS=true`.

Credential generation is an owner action. Do not paste any secret into Slack, ChatGPT, GitHub, documentation, screenshots, or issue bodies.

## Official references

- OpenAI Responses and Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Cursor Cloud Agents API: https://cursor.com/docs/cloud-agent/api/endpoints
- Claude Code Routines: https://docs.anthropic.com/en/docs/claude-code/routines
- Claude Cowork scheduled tasks: https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork
- Gemini Interactions API: https://ai.google.dev/gemini-api/docs/interactions-overview
- Gemini Antigravity Agent: https://ai.google.dev/gemini-api/docs/antigravity-agent
- Gemini coding-agent setup: https://ai.google.dev/gemini-api/docs/coding-agents
- Gemini `generateContent` migration: https://ai.google.dev/gemini-api/docs/migrate-to-interactions
- xAI API quickstart: https://docs.x.ai/developers/quickstart
- xAI model catalog: https://docs.x.ai/developers/rest-api-reference/inference/models
- Grok Build CLI: https://docs.x.ai/build/overview
- Perplexity Search API: https://docs.perplexity.ai/api-reference/search-post
- Perplexity Agent API: https://docs.perplexity.ai/docs/agent-api/quickstart
- Perplexity API privacy and security: https://docs.perplexity.ai/docs/resources/privacy-security
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:13 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:22 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:19 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:34 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | END]] -->
