# Provider Setup Matrix

## Shared periodic usage-check behavior

The orchestrator can periodically send every configured provider a short `DIRECTOR_USAGE_CHECK` prompt. The prompt instructs the provider not to browse, call tools, inspect repositories, edit files, create branches or pull requests, or perform project work. This is an instruction rather than a provider-side sandbox for agent products such as Cursor and Gemini, so provider-side spend limits remain required. The monitor records provider-returned token metadata when available and sends only a bounded status summary to Slack.

These probes consume provider usage and are opt-in through `USAGE_CHECK_ENABLED=true`. They are also disabled by the global paid-run gate and cannot execute while `ALLOW_PAID_RUNS=false`. The default interval is 360 minutes and the enforced minimum is 15 minutes, including manual runs. Account dashboards, not model prose, remain authoritative for plan balances and billing.

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
| Supplemental — sandbox/prototype | Gemini Antigravity Managed Agent | `GEMINI_API_KEY` | Poll background Interaction |
| Supplemental — current research | Perplexity Agent API | `PERPLEXITY_API_KEY` | Synchronous response |

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
- Gemini Managed Agents: https://ai.google.dev/gemini-api/docs/managed-agents-quickstart
- Perplexity Agent API: https://docs.perplexity.ai/docs/agent-api/quickstart
