# Provider Setup Matrix

The cross-provider responsibility, debate, advisory-voting, and strict
Perplexity evidence boundaries are defined in
[`team-operating-charter.md`](team-operating-charter.md).

## Shared periodic usage-check behavior

The orchestrator can periodically send every configured provider a short `DIRECTOR_USAGE_CHECK` prompt. The prompt instructs the provider not to browse, call tools, inspect repositories, edit files, create branches or pull requests, or perform project work. This is an instruction rather than a provider-side sandbox for agent products such as Cursor and Gemini, so provider-side spend limits remain required. The monitor records provider-returned tokens and cost when available, bounded attempts, and normalized quota failures; it never invents missing cost telemetry.

These probes consume provider usage and are opt-in through `USAGE_CHECK_ENABLED=true`. They are also disabled by the global paid-run gate and cannot execute while `ALLOW_PAID_RUNS=false`. The default interval is 360 minutes and the enforced minimum is 15 minutes, including manual runs. `POST /v1/usage-checks/run` returns a structured schedule (`accepted` / `not_due` / `busy`) so callers know which providers were actually scheduled before waiting for records. Account dashboards, not model prose, remain authoritative for plan balances and billing.

Configure one provider at a time and leave `ALLOW_PAID_RUNS=false` until every intended adapter has passed a credential/readiness check. Subscription access and API billing are separate unless the provider explicitly says otherwise.

Before enabling live execution, configure an exact deployment profile:

- `PROJECT_ALLOWLIST`: case-insensitive project names accepted by this deployment.
- `REPOSITORY_ALLOWLIST`: exact GitHub `owner/repository` targets; GitHub HTTPS URLs normalize to this form.
- `ALLOWED_STARTING_REFS`: case-sensitive starting branches or refs. Cursor auto-generated `cursor/...` working branches are not durable starting points; they are deleted after merge. A pre-creation "GitHub branch not found" response for one of those refs retries once against the first allowlisted ref. A follow-up against a deleted working branch starts a new agent from that same durable ref instead of resuming the dead session. Durable refs such as `main` still fail closed.
- `CLAUDE_CALLBACK_SECRET`: a dedicated callback bearer secret, stored only by the orchestrator and trusted callback sender.

An absent project allowlist blocks every live plan. Cursor cannot use an implicit dashboard repository, and model output or owner approval cannot widen these target lists.

## Owner account and routing context

- **The primary team is OpenAI, Claude, and Cursor.** These receive the routine periodic checks and should cover planning, implementation, review, and repository execution.
- **Claude Pro 20x is the primary workhorse.** Route coding implementation, repository-scale reasoning, code/document review, and Claude-strong analysis there first when its routine is configured and healthy.
- **ChatGPT is Plus.** OpenAI remains the Director planner and acceptance reviewer, but ChatGPT Plus does not itself fund OpenAI API calls.
- **Cursor is the third primary teammate.** Use it for repository automation, coding overflow, and independent implementation or review passes.
- **Gemini and Perplexity are supplemental specialists.** Their API or agent consumption must still be verified independently from the consumer subscription, and they are not included in the default periodic-check list.

Practical operating patterns:

1. **Claude-first coding lane:** Claude implements and tests; OpenAI reviews the acceptance evidence; the owner approves any gated action.
2. **Overflow lane:** if Claude approaches its limit or its Routine is unavailable, route isolated coding work to Cursor and keep Claude for the highest-value review or architectural decisions.
3. **Optional specialist lane:** when materially useful, Perplexity gathers current sources, Gemini validates a prototype or browser/sandbox behavior, and the primary team integrates the findings.
4. **Graceful degradation:** periodic usage checks report availability and returned token counts. If substitution could affect scope, cost, or quality, pause for an owner decision rather than silently changing providers.

| Role | Supported bridge in this package | Required values | Completion path |
| --- | --- | --- | --- |
| Primary — Director | OpenAI Responses API with Structured Outputs | `OPENAI_API_KEY`, `OPENAI_DIRECTOR_MODEL` | Immediate planning/review response |
| Primary — workhorse | Claude Code Routine API trigger | `CLAUDE_ROUTINE_URL`, `CLAUDE_ROUTINE_TOKEN` | Session URL plus callback/manual report |
| Primary — repository teammate | Cursor Cloud Agents API v1 | `CURSOR_API_KEY` | Poll durable agent run; draft PR allowed |
| Supplemental — sandbox/prototype | Gemini Antigravity Agent over the Interactions API | `GEMINI_API_KEY`, `GEMINI_AGENT`, `GEMINI_API_REVISION` | Poll stored background Interaction |
| Supplemental — current research | Perplexity Search API; Agent API only for the bounded availability probe | `PERPLEXITY_ENABLED`, `PERPLEXITY_API_KEY` and the research-only controls below | Synchronous ranked results |

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

Normal assignments use `POST /search`. The adapter sends only the assignment's
bounded research query and result limit; it does not send repository context,
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

1. Create and install the Slack app from `slack-app-manifest.yaml`.
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
- Perplexity Search API: https://docs.perplexity.ai/api-reference/search-post
- Perplexity Agent API: https://docs.perplexity.ai/docs/agent-api/quickstart
- Perplexity API privacy and security: https://docs.perplexity.ai/docs/resources/privacy-security
