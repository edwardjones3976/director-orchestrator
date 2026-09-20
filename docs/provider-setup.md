<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 19:30 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-13 | 06:06 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 04:36 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:10 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:42 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:40 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 03:08 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:47 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 11:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:24 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:02 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 19:40 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:34 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:19 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:56 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:47 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:38 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:13 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | BEGIN]] -->
# Provider Setup Matrix

For editor-hosted agents, consult the [VS Code contact methods and Director
integration guide](providers/vscode-contact-methods.md). It separates GitHub
Copilot, provider-specific credentials, extension APIs, MCP, and workspace
transports, with the evidence required before a route can accept assignments.

The cross-provider responsibility, debate, advisory-voting, and strict
Perplexity evidence boundaries are defined in
[`team-operating-charter.md`](team-operating-charter.md).

## Shared periodic usage-check behavior

The orchestrator can periodically send every configured provider a short `DIRECTOR_USAGE_CHECK` prompt. The prompt instructs the provider not to browse, call tools, inspect repositories, edit files, create branches or pull requests, or perform project work. This is an instruction rather than a provider-side sandbox for agent products such as Cursor and Gemini, so provider-side spend limits remain required. The monitor records provider-returned tokens and cost when available, bounded attempts, and normalized quota failures; it never invents missing cost telemetry.

These probes consume provider usage and are opt-in through `USAGE_CHECK_ENABLED=true`. They are also disabled by the global paid-run gate and cannot execute while `ALLOW_PAID_RUNS=false`. The default interval is 360 minutes and the enforced minimum is 15 minutes, including manual runs. `POST /v1/usage-checks/run` returns a structured schedule (`accepted` / `not_due` / `busy`) so callers know which providers were actually scheduled before waiting for records. Account dashboards, not model prose, remain authoritative for plan balances and billing.

An OpenAI HTTP 429 is an unambiguously rejected availability request, so the monitor may make one recovery attempt only when a short, valid `Retry-After` window is available or absent. The accepted delay is 0–60 seconds, expressed as seconds or an HTTP date; absent headers use two seconds, and invalid, past, or longer delays stop recovery. It records both attempts and a sanitized error code for transport, HTTP, JSON, or response-validation failures; the retry never applies to another status or to a Claude callback-pending record.

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

## Live dispatch preflight

The prerequisites for paid, live provider dispatch are spread across several
validators that each raise on the first problem they reach, so configuring them
by starting the process reveals them one process death at a time. Read them all
at once instead:

```
PYTHONPATH=src python -B scripts/live-dispatch-preflight.py
```

It reports and does nothing else: it sets no variable, writes no file, opens no
socket, touches no database, and calls no provider. Every verdict it prints is
the deterministic validator's own EXCEPT the paid/live coupling line, which is
the preflight's own paraphrase of the startup constructor's refusal, because a
read-only preflight must not construct the activation (it opens a Postgres
control plane). It removes, relaxes, and defaults nothing. It prints variable
names and booleans only, never a value —
the same rule `/health` follows. The preflight reports bridge CONFIGURATION booleans only; the startup challenge result (captured once when the activation built its live dependencies; /health never re-challenges and opens no connection per request) is exposed as `/health.host_bridge.startup_challenge_ok`, named for what it is: startup evidence, not current liveness. Exit status is 0 when nothing is outstanding
and 1 when anything is. A clean exit is a configuration statement only: it is
not acceptance, not provider activation evidence, and not owner approval.

The script evaluates the rest of the configuration as though the four coupled
switches (`ALLOW_PAID_RUNS`, `LANGGRAPH_PRIMARY_ENABLED`,
`LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED`, and
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED`) were already set for the
first three validators, and as though two additional switches
(`LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED` and `LANGGRAPH_RUNTIME_ENABLED`)
were also set for the last two validators. This is what lets one pass name every
remaining prerequisite.

### The ordered prerequisite list

Reported following main.py's order, additionally running the runtime validator which startup reaches later through runtime activation.

1. **The coupled switches.** `ALLOW_PAID_RUNS`, `LANGGRAPH_PRIMARY_ENABLED`,
   `LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED`, and
   `LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED`. Any primary execution
   switch requires `LANGGRAPH_PRIMARY_ENABLED`; live dispatch additionally
   requires `ALLOW_PAID_RUNS`; primary enabled without new intake answers every
   intake with HTTP 503 rather than falling back to the legacy engine.
2. **Workload policy.** Always: `WORKLOAD_POLICY_MODE`,
   `WORKLOAD_POLICY_WINDOW_ASSIGNMENTS`, `WORKLOAD_POLICY_SHADOW_ASSIGNMENTS`.
   Then, while `WORKLOAD_POLICY_ENABLED` is on and paid runs are on:
   `PRIMARY_WORKHORSE`, then `TRUSTED_PROVIDER_COST_RANKS`, which must be
   configured, unique, a total order, and must cover every ready specialist.
   At least one specialist must be ready; an empty ready set fails this check.
   This validator raises one problem at a time, in that order, so the preflight
   can only surface the first outstanding one of them per pass.
3. **Shadow lane isolation**, when `LANGGRAPH_SHADOW_ENABLED` or
   `LANGGRAPH_APPROVAL_RESUME_ENABLED` is on: `LANGGRAPH_CHECKPOINT_PATH`,
   `LANGGRAPH_CHECKPOINT_NAMESPACE`, and isolation from `SQLITE_PATH`.
4. **Primary configuration.** `LANGGRAPH_PRIMARY_SNAPSHOT_MAX_FILES`;
   `LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS`;
   `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON`;
   `LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET`;
   the three distinct completion-authority evidence secrets
   (`LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET`,
   `LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET`,
   `LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET`);
   `LANGGRAPH_PRIMARY_COMPLETION_EVIDENCE_ACTOR_ALLOWLIST_JSON`;
   `LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET`;
   `LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT` with
   `LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET`, configured together or not at all;
   pairwise-distinct ingress secrets; `LANGGRAPH_PRIMARY_POSTGRES_DSN`;
   `LANGGRAPH_PRIMARY_POSTGRES_SCHEMA`;
   `LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE`, distinct from the shadow and
   runtime namespaces; `LANGGRAPH_PRIMARY_OWNER_TOKEN`;
   `REPOSITORY_SNAPSHOT_ROOT`; exactly one `REPOSITORY_ALLOWLIST` entry;
   exactly one `ALLOWED_STARTING_REFS` entry; and
   `LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA` with
   `LANGGRAPH_PRIMARY_PINNED_TREE_SHA`, supplied together or not at all.
   A binding for `microsoft-365-copilot` additionally requires
   `MICROSOFT_365_COPILOT_DELEGATED_TOKEN_FILE`,
   `MICROSOFT_365_COPILOT_TENANT_ID`,
   `MICROSOFT_365_COPILOT_EXPECTED_SUBJECT`, and
   `MICROSOFT_365_COPILOT_EXPECTED_OBJECT_ID`.
5. **The paid/live coupling**, checked next, at the point where startup builds
   the primary activation. See below.
6. **Parallel dispatch**, evaluated by the preflight with
   `LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED` forced on so its prerequisites
   are reported even before the operator enables it:
   `LANGGRAPH_PARALLEL_INITIAL_MAX_CONCURRENCY`,
   `LANGGRAPH_PARALLEL_AUTO_SCALE_MAX_CONCURRENCY`,
   `LANGGRAPH_PARALLEL_CLEAN_BATCHES_TO_SCALE`,
   `LANGGRAPH_PARALLEL_RECOVERY_CLEAN_BATCHES`, and the per-provider caps. The
   fixed values are not tunable and a different one is a startup refusal.
7. **Runtime lane**, evaluated by the preflight with
   `LANGGRAPH_RUNTIME_ENABLED` forced on so its prerequisites are reported even
   before the operator enables it:
   `LANGGRAPH_RUNTIME_CHECKPOINT_PATH`, `LANGGRAPH_RUNTIME_LEDGER_PATH`,
   `LANGGRAPH_RUNTIME_CHECKPOINT_NAMESPACE`,
   `LANGGRAPH_RUNTIME_WRITER_OWNER_ID`, `LANGGRAPH_RUNTIME_WRITER_SECRET`,
   `LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED`, and distinct store files.
8. **Execution profile**, which fails closed before any provider call:
   `PROJECT_ALLOWLIST`, `REPOSITORY_ALLOWLIST`, `ALLOWED_STARTING_REFS`, and
   `CLAUDE_CALLBACK_SECRET`, as listed above.
9. **Subsystem readiness**, evaluated last, mirroring what live startup
   requires without connecting to anything. The entitlement control plane:
   `ENTITLEMENT_CONTROL_PLANE_ENABLED` must be true, and its dependent checks
   run whether or not it is (one pass reports everything): the DSN file must
   exist at the literal configured path, be at most 65536 bytes and, stripped,
   be one non-empty line that the same `_parse_connection_target` startup
   uses accepts (explicit dbname, port, user, a verifying sslmode with
   sslrootcert, no delegated identity) and that names the same database and
   role as `LANGGRAPH_PRIMARY_POSTGRES_DSN` under the entitlement schema —
   the identical storage-identity digest startup compares; only the
   database-side accepted identity, which needs the live handle, is left to
   startup. The parser and digest are imported as pure functions; the file
   still opens no connection, which its tests pin by name; each of
   the three secret files must exist after `~` expansion and, stripped, hold
   at least 32 UTF-8 bytes with at least 16 distinct byte values — the same
   rule `_read_secret_file` applies; the schema must match `[a-z0-9_]+_v<N>`.
   File contents are read only to apply those rules and never reach the
   output; any filesystem or `~user` expansion failure reads as an unavailable
   file. The effect gateways: `SLACK_BOT_TOKEN` or a GitHub credential; when
   the registry would build the GitHub App gateway, its credential must also
   pass the constructor's local syntax checks (numeric ids, strict base64
   private key) — an incomplete App configuration is skipped by startup and
   therefore not reported. The host provider bridge: endpoint/secret
   configuration booleans only; the pairing rule is reported by the
   langgraph-primary validator above.

   `/health.providers.lanes.<lane>` reports two booleans. `configured` is a
   startup observation: an executable was observed for the lane, or the lane
   has a composed adapter (the Antigravity team route dispatches without a
   local executable; an installed lane outside
   `LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS` has no adapter). `live_ready` is the
   gate dispatch itself applies: the lane has a composed adapter and the
   activation service currently reports `provider_ready(lane, live=True)`, so
   a lapsed `LIVE_OK` capture flips it to false on the next read; without an
   activation service nothing is live. A binary or login that disappears after
   startup is not re-probed by `/health` (dispatch would fail the same way);
   that liveness belongs to a bounded monitor, not the health endpoint.

### Mutual exclusions and pairings

- `LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED` and
  `LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED` are **mutually
  exclusive**. Leaving offline simulation on while requesting live dispatch is
  a startup refusal, not a precedence rule.
- `LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT` and
  `LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET` must be present together or absent
  together.
- `LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA` and
  `LANGGRAPH_PRIMARY_PINNED_TREE_SHA` must be supplied together.
- `LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED` must be on whenever
  `LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED` is on.

### The paid/live coupling

`ALLOW_PAID_RUNS` and `LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED` are
coupled in **both** directions, and the two must move as one unit:

- Live dispatch without paid runs is rejected by the primary configuration
  validator.
- Paid runs without live dispatch requested, on a deployment where
  `LANGGRAPH_PRIMARY_ENABLED` is on, is rejected when startup builds the
  primary activation. **The process does not start.** Turning paid runs on by
  itself takes the deployment down rather than enabling anything.

The preflight reports that second state explicitly, against the configuration
as it stands rather than the target, because it is the trap: a deployment can
be one variable away from not booting while every other prerequisite reads as
satisfied.

## Owner account and routing context

- **The primary team is OpenAI, Claude, Cursor, and Grok.** These should cover planning, implementation, review, and repository execution; periodic-check membership remains independently configurable.
- **Implementation uses cheap-capable-model-first routing.** Establish scope, transport, readiness, capability, tools, context, risk, and reviewer independence; then prefer an under-target eligible provider and the lowest trusted comparable estimated cost. Missing cost is unknown, not zero. An exact trusted assignment requirement may select Claude, but workload length alone does not.
- **OpenAI Pro 20x and Claude Max 5x** are the owner-reported account context. These capacity signals do not fund separate API calls; verify route-specific remaining capacity and budget.
- **Cursor and Grok are primary Cursor-family teammates.** Both have full task-capability coverage across all eight responsibility areas and low through critical work, while preserving producer/reviewer separation and deterministic gates.
- **Gemini and Perplexity are supplemental specialists.** Their API or agent consumption must still be verified independently from consumer subscriptions, and they are not included in the default periodic-check list.

Use the [approved eight-lane allocation profiles](provider-allocation-profiles.md),
with Engineering as the default. Freeze profile and digest before approval,
preserve real history, exclude zero-target lanes, and enforce the prospective
fixed twenty-slot target+5 ceiling. The ±3-point normal band guides preference;
observed effort is separately reported and cannot create production credit.
OpenAI production and Antigravity specialist work require their actual scoped
adapters and non-author verification; a target alone proves no live capability.

Every assignment has at most three conditional attempt slots. The Director
derives a deterministic roster of the provider's configured packet formats and
transports and rejects rosters larger than three. Stop on success; recovery requires temporary failure; a third attempt requires materially new information or a substantially different transport. Never repeat ambiguous paid dispatch. Each child attempt is appended to Postgres before I/O
and settled with exact provider, format, transport, idempotency, budget,
result, and reason digests. An ambiguous paid attempt stops redispatch until
authenticated reconciliation; a failed attempt remains untrusted and cannot
overwrite the final assignment result artifact.

Practical operating patterns:

1. **Cheap-capable implementation lane:** select OpenAI production, Claude, Cursor, Grok, or an eligible bounded Antigravity lane only after capability, context, readiness, and independence; apply direct-production hard eligibility and under-target preference before total-effort rank, then trusted cost and deterministic tie-break; retain the assigned provider's transport and authority limits.
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

## Intake routing defaults

Two owner-set variables supply the allocation profile and the primary capability
request that every Slack intake carries. They are deployment configuration only.
The Slack gateway never derives either value from message text: a Slack message
is untrusted data and cannot steer provider selection.

- `INTAKE_ALLOCATION_PROFILE` selects one of the four approved
  [allocation profiles](provider-allocation-profiles.md): `engineering`,
  `mixed`, `public_research`, or `enterprise`. The default is `engineering`.
- `INTAKE_PRIMARY_REQUIRED_CAPABILITIES` is a comma-separated list drawn from
  `public_research`, `bounded_scaffolding`, and `repository_assistance`. The
  default is empty. A repeated entry is refused.

Leaving both unset reproduces the previous behavior exactly, so an existing
deployment changes nothing by upgrading. Startup validation refuses an unknown
profile name, an unknown capability, and a repeated capability before the
service accepts any intake; it does not wait for a per-task roster hold.

`enterprise_context` is a valid intake capability but is **not** configurable.
No scoped enterprise production adapter exists, so the roster selector holds
every plan that requests it. Configuring it could therefore only produce one
hold per task, and startup refuses it instead. This refusal is additive and
removes no existing gate.

These variables change which lanes can be selected, which creates one
operational prerequisite:

- `bounded_scaffolding` makes the Antigravity lane selectable, and
  `repository_assistance` makes the GitHub Copilot lane selectable. Under
  `engineering` the GitHub Copilot target is zero and the lane stays ineligible
  on allocation grounds; `mixed` and `enterprise` give it a non-zero target.
- **Every selectable lane must have a rank in `TRUSTED_PROVIDER_COST_RANKS`.**
  The selector holds the *entire* roster with `primary_trusted_cost_rank_required`
  when any candidate lane lacks a rank, not just that one lane. Configure a
  `github-copilot` rank before enabling `repository_assistance`. The Antigravity
  lane already draws its rank from the `gemini` entry — `antigravity` is
  accepted as a spelling and normalizes to `gemini` — so a deployment with a
  ready Gemini specialist already satisfies `bounded_scaffolding`.
- The `ready_provider_ranks_complete` readiness boolean covers only the ready
  specialists `claude`, `cursor`, `gemini`, `grok`, and `perplexity`. It does
  **not** cover `github-copilot`, so a missing GitHub Copilot rank passes
  startup validation and surfaces later as a plan-time hold. Verify the rank
  when enabling the capability.

The same setting must additionally cover every lane the primary roster can
select. A lane bound in `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON`
becomes a roster candidate as soon as an intake names a profile and capability
that admit it, and the roster refuses to select *anyone* while any candidate is
unranked. With `ALLOW_PAID_RUNS` and `WORKLOAD_POLICY_ENABLED` both true, the
service therefore refuses to start and names the unranked lane and
`TRUSTED_PROVIDER_COST_RANKS`. Two consequences are worth planning for:

- An `antigravity` binding is satisfied by a `gemini` rank; the parser
  normalizes the two names to one entry, so do not add a separate rank.
- The requirement follows the activation bindings, not credentials. Removing a
  lane from `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON` removes its
  rank requirement, because an unbound lane is never observed ready and never
  reaches a roster. Leaving a lane bound while omitting its rank is the
  configuration that previously failed only at dispatch.

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

GitHub Copilot Lite is not another provider bridge. It is an optional
hygiene observation requested only after required checks are green and the exact
candidate is frozen. It has no assigned workload share, range, or ceiling,
no provider adapter or automatic routing, and no production credit,
independent acceptance role, gate, merge authority, or every-push automation.
Without an authenticated contact it fails closed as unavailable. A requested
observation binds at most three conditional attempt slots. Stop on success; recovery requires temporary failure; a third attempt requires materially new information or a substantially different transport. Never repeat ambiguous paid dispatch.
Quota/unavailability and rejected findings are recorded as waste.
A confirmed defect follows the normal correction path, and any substantive
Copilot-authored fix makes Copilot a material author ineligible to independently
review that candidate.

Per-provider profile sheets in `docs/provider-profiles/` are rendered from YAML
sources and list each lane's variable names, readiness booleans, transports, and
routes. They mirror this document and the team operating charter, fail the drift
tests when they diverge, and grant no authority; no value, credential, allowlist
entry, endpoint, or path is ever recorded in a sheet.

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

<!-- [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | BEGIN]] -->
One xAI key serves every project (owner decision 2026-09-16); do not mint
per-project keys. `GROK_ENABLED=true` plus a configured `XAI_API_KEY` is not
enough for the key to be exercised: the lane must also appear in
`PRIMARY_TEAM` (assignments) and `USAGE_CHECK_PROVIDERS` (periodic usage
checks). A deployment `.env` that overrides those lists without `grok` leaves
the credential idle with no error; `scripts/live-dispatch-preflight.py`
reports this under "provider roster".
<!-- [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | END]] -->

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

## Z.ai trial-only contact lane

Z.ai (GLM Coding Plan) is staged as a trial-only, fail-closed contact lane under
owner directive #207 until the owner's separate activation decision on or after
2026-09-29. It is not a production provider: `zai` is absent from the provider
enums, the allocation lanes, the primary roster and the Postgres lane constraint,
so no code path can grant it production credit, a counted review verdict or merge
authority. Anything it reviews is advisory input to the author. The runtime
identity, ledger and dispatch path live in `src/director_orchestrator/zai_lane.py`;
the transport is unchanged in `zai_trial.py`.

Keep the key only in the ignored deployment `.env` and start with
`ZAI_TRIAL_ENABLED=false`:

```dotenv
ZAI_TRIAL_ENABLED=false
ZAI_API_KEY=
ZAI_API_BASE=https://api.z.ai/api/anthropic
ZAI_MODE=trial_only
ZAI_MODEL=glm-5.3-flash
ZAI_CLI_EXECUTABLE=
ZAI_TRIAL_JOURNAL_DIR=
ZAI_TRIAL_LEDGER_PATH=
ZAI_TIMEOUT_SECONDS=60
ZAI_LITE_ENTITLEMENT_VERIFIED=false
ZAI_PRODUCTION_ACTIVATION=
ZAI_ALLOW_REPOSITORY_WRITE=false
ZAI_ALLOW_REVIEW_VERDICT=false
```

`ZAI_CLI_EXECUTABLE` is the absolute path of the Claude Code CLI that hosts the
session on this host. The transport pins it to `ZAI_API_BASE`, which must stay the
official Anthropic-compatible endpoint, and disables tools, MCP servers, settings
sources and session persistence for every call. `ZAI_TRIAL_JOURNAL_DIR` holds the
per-attempt receipts (exclusive reservation, redacted native output, result) and
`ZAI_TRIAL_LEDGER_PATH` is the separate append-only SQLite trial ledger. Reuse both
across attempts; changing either to escape an attempt reservation is a policy
breach. The ledger must be its own file: startup and the CLI refuse a
`ZAI_TRIAL_LEDGER_PATH` that names the TaskStore, a LangGraph checkpoint, resume-claim,
acceptance, runtime-ledger or completion-gate store, and an existing file is reused
only when it is exactly this build's schema and version (a foreign, partial, weakened
or older file is refused, never migrated). The deployed container image does not carry the Claude Code CLI, so the
lane is closed there by construction and is operated from the Windows host.

Startup refuses any widening: `ZAI_MODE` other than `trial_only`, either
`ZAI_ALLOW_*` flag set true, a non-official `ZAI_API_BASE`, any value in
`ZAI_PRODUCTION_ACTIVATION`, or `ZAI_TRIAL_ENABLED=true` without
`ZAI_LITE_ENTITLEMENT_VERIFIED=true` (the owner has observed the Lite
entitlement). `GET /health` reports `providers.zai` (one boolean) and `zai_trial`
(the individual readiness booleans); neither carries a key, path, endpoint or
model value. The lane also refuses to dispatch at or after 2026-09-29 07:00 UTC,
the trial end the transport already enforces.

Run bounded Lite assignments with `scripts/zai-trial.py`, which reads only the
`ZAI_*` lines of the file named by `--env-file`:

```bash
PYTHONPATH=src python scripts/zai-trial.py --env-file .env status
PYTHONPATH=src python scripts/zai-trial.py --env-file .env dispatch --work-packet packet.json --purpose "bounded Lite assignment"
PYTHONPATH=src python scripts/zai-trial.py --env-file .env evaluate --attempt <id> --evaluator cursor --correctness correct --first-pass --privacy-fit fit --usefulness accepted
```

`dispatch` validates the v1 work packet and mirrors the transport's deterministic
refusals (digest, tools, budget, timeout, trial end) before it reserves one attempt
per assignment in the trial ledger, so a packet that could never reach Z.ai
consumes nothing; afterwards it records status, latency and the result digest.
Cost stays unknown, never zero, because the host CLI prices with Anthropic
tariffs. Outcome statuses: `SUCCEEDED`, `FAILED`, `AMBIGUOUS` (timeout or unknown
provider state), `REFUSED` (stopped before provider contact, attempt not consumed)
and `BLOCKED` (Z.ai answered `OWNER_DECISION_REQUIRED`; needs a new assignment).
Nothing retries automatically: one recovery is allowed only after a recorded
`FAILED` outcome and only with an explicit `--recovery-basis`; a `SUCCEEDED`,
`AMBIGUOUS` or `BLOCKED` attempt is never redispatched, and the route closes after
three recorded failures (`FAILED` plus `AMBIGUOUS`). `resolve --attempt <id>
--reason …` closes an attempt that a killed process left without an outcome, as
`AMBIGUOUS` only. `evaluate` records the non-Z.ai evaluator's correctness,
first-pass, correction-count, privacy-fit and usefulness verdicts (any evaluator
name naming Z.ai or GLM, or equal to the attempt's model, is refused), and
`status` derives the first-pass yield and correction rate the 2026-09-29 decision
needs. The ledger issues no production credit and is not the canonical rolling
allocation ledger.
The conditional eleven-contact allocation profile is described separately and
stays `PROPOSED` until that decision.

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
requires a later OpenAI, Claude, Cursor, or Grok assignment that directly depends on it and
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

## Oracle merge base ref is per project

`ORACLE_AUTOMERGE_BASE_REF` has no default. When it is unset the Oracle merge
gate refuses, and it also refuses the shared trunk names outright, naming tier 0
and the variable. Each project configures its own base ref rather than inheriting
one. GitHub branch protection and active repository/organization rules are read
for the exact base, with missing or inaccessible evidence refused. The gateway
rechecks before merging; concurrent administrator changes are not atomically locked. `/health` reports
`oracle_merge.base_ref_configured` as a boolean; it never reports the value.
`ORACLE_AUTOMERGE_ENABLED` and `ORACLE_AUTOMERGE_KILL_SWITCH` are unchanged and
stay closed.

The gate compares provider identities as canonical allocation lanes on both
sides, so a lane alias resolves to its lane before the reviewer allowlist, the
no-self-review comparison, and the `ORACLE_AUTOMERGE_*_ACTORS` lookups. An alias
therefore cannot reach a different verdict than its lane, and cannot review its
own lane's implementation. Admitting a lane to the reviewer allowlist remains a
separate decision.

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

## Primary security credential boundaries

Completion evidence uses three pairwise-distinct secrets:
`LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET`,
`LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET`, and
`LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET`. None may equal a provider,
shared, effect, bridge, or other ingress secret. Configure
`LANGGRAPH_PRIMARY_COMPLETION_EVIDENCE_ACTOR_ALLOWLIST_JSON` with exactly
`independent_review`, `ci`, and `security`; every entry has nonempty `issuers`
and `principals` arrays, and all three authorities must be pairwise disjoint.
Each role signs only its exact result binding; the aggregate has no bundle
credential capable of minting another role. Durable Postgres readback preserves
and revalidates the three canonical authority proofs.

Activation review authority is separately configured through
`LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON` and
`LANGGRAPH_PRIMARY_ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON`. These
GitHub-login arrays must be disjoint. Only an `APPROVED` review from an
`OWNER`, `MEMBER`, or `COLLABORATOR` is considered, and its body must equal one
canonical JSON object with exactly `actor`, `head_sha`, `kind`,
`schema_version`, and `verdict`. Comments, prose, quotes, code fences, extra
fields, and substring markers are never activation evidence.

The Windows bridge permits plaintext HTTP only on an exact loopback host.
Container-to-host use must use HTTPS. An all-interface bridge bind requires
explicit TLS certificate and private-key files; the certificate must be trusted
by the client runtime and cover `host.docker.internal`.

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
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:38 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:47 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:56 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:19 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:34 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | END]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 19:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:02 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:24 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 11:40 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:47 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 01:25 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 03:08 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:42 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:40 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:10 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 04:36 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-13 | 06:06 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 19:30 UTC | END]] -->
