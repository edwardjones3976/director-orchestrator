<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 22:17 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:11 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:46 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:35 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 01:12 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:15 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:40 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:19 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:32 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:27 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 22:20 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 22:10 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | BEGIN]] -->
# Modular project bootstrap protocol

**Status:** Owner-authorized operating protocol. This document helps shape a
new project; the application's deterministic policy, project allowlists, and
owner-approved work order remain authoritative.

## Purpose

A productive provider should not have to rediscover the team's working style
every time it encounters a new objective. It may take a useful source thread,
the Director pattern, or an active project's working habits and **mold** them
into a fresh project profile.

This is deliberately liberal about the shape of a project. A project may be a
one-hour investigation, a repository migration, a long-lived product, a
research effort, an operations lane, or something that changes shape while it
is being understood. It may use only one provider at first or several; it may
have a repository, a Slack channel, both, or neither.

It is not liberal about authority. A source thread never grants the new project
its source project's repositories, credentials, private context, spending,
external-write permissions, merge authority, or deployment authority. Absent
an explicit project decision, the safe default is bounded analysis and a
project-profile draft.

## Core principle: adapt the pattern, not the permissions

Treat the Director pattern as a kit of modules, not a fixed ceremony.

1. Copy the useful *intent*, working assumptions, desired outcomes, evidence
   standards, and collaboration habits from the source thread.
2. Translate them into the new project's vocabulary, risks, stakeholders,
   surfaces, and definition of done.
3. Mark every element as **confirmed**, **provisional**, **not applicable**, or
   **unknown**. A useful project can begin with many provisional fields.
4. Do not infer execution targets or access from a prior project. Repository,
   starting ref, credentials, Slack destination, budget, and external effects
   must be named for this project before live work uses them.
5. Enable only the modules that add information or reduce risk. Modules can be
   added, removed, paused, or reshaped as the project becomes clearer.

A missing detail is an invitation to frame a question or make an explicitly
labelled assumption; it is not permission to improvise an external action.

## Bootstrap exchange

When a provider sees a potential new project, it opens a concise
`PROJECT_BOOTSTRAP` record in the designated project conversation. The
provider may start from an existing thread, including an Edge Engine thread,
but should produce a project-specific summary instead of forwarding a long,
unfiltered conversation.

The record should capture only what is presently useful:

- **Starting point:** the source thread or trigger, stated in a sentence or
  two; its useful ideas and its known limitations.
- **Project proposition:** what might be worth accomplishing, learning,
  deciding, building, or changing.
- **Possible outcome:** a tangible end state, even if it is tentative.
- **Current boundaries:** known repositories, systems, data classes,
  stakeholders, cost ceiling, time window, and forbidden actions.
- **Open shape:** what is deliberately undecided, what may vary, and the next
  small question or reversible experiment that would reduce uncertainty.
- **Suggested modules and team:** which modules are useful now and which
  providers have a credible, bounded contribution.
- **Evidence and stopping point:** what would make the next decision credible,
  and what result, budget, deadline, or safety condition ends the current
  phase.

The Director turns this record into a provisional project profile. It should
ask no more than the narrow questions that materially change scope, cost,
privacy, repository target, or risk. Otherwise it can proceed in
analysis-only mode and refine the profile from evidence.

## Project profile

The profile is a living project-local contract, not a duplicate of the global
charter. It gives the team a shared frame while leaving room for discovery.

The canonical machine-readable schema is
[`docs/templates/project-bootstrap/project-profile.yaml`](templates/project-bootstrap/project-profile.yaml)
with `schema_version: director-project-profile/v1`. Copy that template into the
project; do not maintain a divergent inline schema here.

| Section | Purpose |
| --- | --- |
| `project` | Identity, proposition, lifecycle state, owner, timestamps. |
| `source_context` | What to preserve, reshape, or discard from the source thread; `inherited_authority: false` by default. |
| `outcomes` | Desired results, non-goals, evidence standard, and stopping point. |
| `surfaces` | Slack channel/thread placeholders, repositories, worktrees, data classification, and external-effect mode. |
| `modules` | Which modular work kit entries are enabled, optional, disabled, or unknown. |
| `team` | Director lane, provider lanes, workflow-skill selectors, and forbidden authority per lane. |
| `coordination` | Live transport, execution transport, review/merge surface, and relay rules. |
| `controls` | Budgets, bounds, intent-only allowlist placeholders, approval gates, and acceptance checks. |
| `review_chain` | Producer, eligible reviewer, exact candidate SHAs/digest, and invalidation rule. |
| `assumptions` | Confirmed, provisional, unknown, and not-applicable working assumptions. |
| `fallbacks` | Safe degradation when providers, evidence, or scope are incomplete. |
| `next` | One bounded next action and expected evidence. |

The profile may begin as a short list rather than complete YAML. The fields
exist to expose uncertainty and prevent accidental inheritance; they are not a
requirement to perform paperwork before useful exploration. Do not place secrets,
credentials, tokens, or raw private source in the profile. Workflow skills are
procedure overlays, not permission grants.

## Companion template kit

Copy only the artifacts the project needs from
[`docs/templates/project-bootstrap/`](templates/project-bootstrap/):

- [`intake.md`](templates/project-bootstrap/intake.md) molds a source thread
  into a concise `PROJECT_BOOTSTRAP` request.
- [`project-profile.yaml`](templates/project-bootstrap/project-profile.yaml)
  records the living project-local profile.
- [`rollout-packet.md`](templates/project-bootstrap/rollout-packet.md) records
  deployment identity, allowlists, Slack isolation, activation evidence,
  communication fallback, and rollback for every project presented to the
  Director.
- [`workstream-plan.md`](templates/project-bootstrap/workstream-plan.md)
  composes bounded modules, provider assignments, workflow skills, and
  fallbacks.
- [`review-ready.md`](templates/project-bootstrap/review-ready.md) carries an
  exact candidate from its producer to a different eligible reviewer.
- [`project-state-report.md`](templates/project-bootstrap/project-state-report.md)
  reports bootstrap, milestone, reshaping, pause, or completion state.

The rollout packet is mandatory for every project presented to the Director.
Start with the intake and a project-local rollout packet created from the
template; add the remaining optional files only when they improve coordination,
evidence, or repeatability. Delete irrelevant fields and mark material
uncertainty instead of filling placeholders with guesses.

## Modular work kit

Select modules by the project's present needs. The modules below have loose
entry and exit criteria by design; a project may revisit them in any order.

| Module | Use it when | Typical output | Important boundary |
| --- | --- | --- | --- |
| Discovery | The problem, opportunity, or constraint is not yet clear. | Framing notes, options, assumptions, first questions. | May explore broadly but does not authorize access or spending. |
| Research and evidence | Current facts, methods, sources, market context, or external claims matter. | Provenanced evidence brief, contradictions, confidence, gaps. | Perplexity gets only a bounded public query; Firecrawl collects source material, not decisions. |
| Design and decision | There are credible alternatives or dependencies. | Options, trade-offs, recommendation, dissent, decision record. | Advisory debate or voting never creates execution authority. |
| Implementation | A bounded change, prototype, document, or artifact is ready to make. | Isolated branch/worktree artifact, tests, handoff. | Use only the exact named project, repository, and starting ref. |
| Quality and security | A claim, artifact, or change needs independent verification. | Reproduction, test results, review, security evidence. | The producer can vote, but cannot independently accept its own work. |
| Review to merge | A reviewable artifact is ready to progress. | Exact-SHA or artifact-digest review handoff, verdict, CI/security evidence. | A new commit invalidates prior review and merge evidence. |
| Operations | A validated project has a runtime or recurring responsibility. | Runbook, health signals, incident or maintenance plan. | Deployment, publication, destructive action, purchases, or access changes remain owner-gated. |
| Learning and reuse | A project has a durable lesson, reusable packet, or decision. | Sanitized template, retrospective, reusable module. | Do not copy private project context or credentials into a reusable asset. |

A small project may use only Discovery and Learning. A fast code correction may
use Implementation, Quality, and Review to merge. A new product may circulate
through all modules repeatedly. The Director should prefer the smallest useful
combination, then expand only where expected information gain justifies it.

## Provider-fit menu

Provider roles are selectors, not a rigid staffing chart. Fit, independence,
capacity, current availability, and the desired evidence determine the
assignment. Canonical provider roles, debate rules, committee weights, and
strict research contracts live in
[`docs/team-operating-charter.md`](team-operating-charter.md); restate only
project-local fit here and link back for authority boundaries.

| Provider or capability | Good project-local use | Never infer from this role |
| --- | --- | --- |
| Claude | framing, plan reshaping, implementation, debugging, and independent review of work it did not author; runtime Director only when its Director transport is explicitly activated | project coordination authority held by the ChatGPT/Codex Director, authority to remove deterministic policy, or approval of its own producer work |
| Codex / ChatGPT / OpenAI | Director (project coordination, routing, review orchestration, completion reporting); repository-scale reasoning, implementation, debugging, architecture, independent review | access to an unnamed repository, self-review, merge, deployment, or external representation |
| Cursor | full-spectrum implementation, reliability, QA, architecture, independent review, technical leadership | an overflow-only role, self-review, or a durable execution target other than the named one |
| Grok | capability-routed primary lane for research, evidence verification, programming, deep reasoning, innovation, development, QA, and assigned leadership, selected by verified transport capabilities, authority, independence, budget, availability, and expected information gain | shared capabilities or authority across the separate xAI API, Grok Build CLI, and Grok Bot contacts; assignment-scoped acceptance authority, self-review or self-approval, or bypass of scope, Codex Security, or deterministic gates |
| Gemini / Antigravity | restricted default fixture contact (bounded scaffolding, mechanical work, sandboxed ideas); explicitly granted team-contract engineering/review | primary authority, unrestricted project context, bypass of admission gates, independent acceptance, merge, or methodology truth |
| Perplexity | current public-source research, source discovery, fact and method cross-checking | repository context, programming, voting, review verdicts, Slack posts, merges, deployments, or permission changes |
| Firecrawl | collection, extraction, normalization, and provenance of approved web material | a decision, authority, or a substitute for evidence evaluation |
| Codex Security | independent security evidence for eligible repository changes | product direction, acceptance, merge, or deployment authority |
| GitHub Copilot Lite | post-green, exact-candidate-frozen unscored reporting-only hygiene observation with at most three conditional attempt slots | a provider role, production credit, gate, every-push trigger, independent acceptance, or merge authority |

Each row summarizes the canonical role definitions in
[`team-operating-charter.md`](team-operating-charter.md#roles).

Whichever lane directs a project, maintenance (review-finding fixes, record and
document inconsistencies, CI fixes, provider connection or plumbing problems)
goes to OpenAI Codex, through the Codex CLI or the ChatGPT/Codex Director
thread, under the charter's
[Director maintenance routing](team-operating-charter.md#director-maintenance-routing),
and Codex's edits are assistance that leaves the PR's sole author unchanged
(the sole-author rule, PR #284).

Select and freeze an [approved allocation profile](provider-allocation-profiles.md)
before approval; Engineering is the default. All nine lanes have explicit targets;
zero-target lanes are ineligible. Preserve actual assignment history and apply the
full twenty-slot startup denominator and ±3-point normal preference. The owner
retired the target+5 rolling-twenty hard ceiling on 2026-09-21 as outdated; it no
longer blocks admission. Report observed total effort separately; control-plane
work, retries, and failed output never become accepted production credit.
Subscription capacity signals (OpenAI Pro 20x, Claude Max 5x) do not grant API
budgets or capabilities.

Choose the cheapest capable trusted model, never simply the cheapest contact.
First establish exact scope and allowlists, usable transport, readiness,
capability, tools/context, risk, and independence; then apply direct-production
hard eligibility and under-target preference before total-effort rank,
compare trusted cost evidence, and use a deterministic tie-breaker. Unknown cost is not zero. An
exact trusted requirement may select Claude or a named model profile; workload
length alone does not. Record eligible candidates, exclusions, comparable costs,
the selected provider/model, and the tie-break reason.

For paid routing, only the operator-authored `TRUSTED_PROVIDER_COST_RANKS`
setting supplies the comparable total order. It must cover every configured
ready specialist with unique positive ranks and is never inferred, generated,
or rewritten by a provider. Zero-target lane ineligibility applies after capability, scope,
independence, safety, and resource eligibility; the normal band remains a soft preference,
and the retired rolling-twenty hard cap no longer blocks admission.

### Firecrawl privacy routing

Treat a rejected Zero Data Retention (ZDR) request as a terminal routing result,
not permission to weaken data handling:

1. Use Firecrawl with `zeroDataRetention: true` only after the authenticated
   account accepts that option.
2. For an approved public page, `lockdown: true` is an acceptable cache-only ZDR
   route. A cache miss is terminal for that route because lockdown never fetches
   the live page.
3. On a public lockdown cache miss, use an ordinary first-party public retrieval
   route outside Firecrawl.
4. Never send private, repository, account, credential, or project material
   through that public-only fallback or through non-ZDR Firecrawl.
5. Do not retry a rejected ZDR request unchanged. Record the rejection and keep
   Firecrawl unavailable for protected material until authenticated ZDR is
   proven.

### Shared provider workflow skills

When the refreshed shared skill catalog exposes the following stable names, the
Director may select the matching workflow as a project-local execution overlay:

| Skill selector | Provider lane | Use it for | Preserved boundary |
| --- | --- | --- | --- |
| `cursor-agent-workflow` | Cursor | implementation, debugging, QA, and independent exact-SHA review | Verify the named repository, worktree, and branch; Cursor cannot review work it materially authored. |
| `antigravity-sandbox-workflow` | Gemini/Antigravity | disposable prototypes and bounded behavioral verification | The sandbox output is non-authoritative; the current Antigravity outbound contract and primary-provider verification still apply. |
| `perplexity-research-workflow` | Perplexity | bounded public research with citations and provenance | Send only the approved public query; no repository context, programming, voting, review verdict, or external-write authority. |
| `pplx-cli` | Perplexity | terminal-based live web search and query-relevant public-page snippets | Use only within the Perplexity research-only lane; pass no repository context, credentials, private material, programming work, vote, review verdict, or external-write authority. |

On Windows hosts, run the Linux-only `pplx-cli` through the named project WSL
workspace. Browser availability is continuity only and does not replace CLI
authentication, the public-query boundary, or persisted research evidence.

Use the catalog name in the assignment rather than a machine-specific absolute
path. Skill discovery is required before selection; installation or a reported
structural evaluation does not prove that the active provider session has
loaded the skill. If a selected skill is unavailable after refresh, continue
with the provider's baseline contract when that still satisfies acceptance, or
reshape the plan under the assignment-specific retry budget.

A skill is a procedure, not a capability grant. It may narrow how an assignment
is performed and strengthen its evidence, but cannot widen the project profile,
provider role, data exposure, budget, repository access, review eligibility, or
external-action authority. Use no skill merely because it exists; select it
only when its workflow materially improves the active module.

Availability is not a reason to fabricate coverage. Each safe provider contact
is bounded and recorded under an immutable maximum three-attempt assignment
budget. Stop on success; recovery requires temporary failure; a third attempt requires materially new information or a substantially different transport. Never repeat ambiguous paid dispatch. Persist every child
attempt in the append-only Postgres ledger. Resource and cancellation bounds
remain mandatory. Do not repeat an ambiguous paid dispatch; reconcile the run
first, and never promote failed output into the final result artifact.

A v1 paid revision additionally requires authenticated, durable assignment token
accounting; primary attempts are unaffected. The current runtime has no generic
token authority, so it fails paid revisions closed rather than treating
pre-stage `budget_units` or optional provider telemetry as token accounting.

## Communication and project-local collaboration

Slack is the live coordination surface; GitHub is the canonical source for
review and merge evidence; local provider CLIs are the preferred execution
transport; browser conversations preserve continuity only.

For each live project, use its dedicated project channel and one
Director-owned, assignment-correlated thread. Name the channel only from the
project profile (`surfaces.slack.channel`) or runtime configuration—use the
configured channel ID or an explicit project-local placeholder such as
`<slack-channel-id-from-profile>`. Do not hardcode workspace channel names or
assert workspace facts in protocol or template artifacts.

Every presented project receives a rollout packet. When two or more Director
deployments will be active concurrently, each must have a distinct Slack app,
distinct project channel, distinct deployment profile, and distinct state
namespace. Record app and channel identities, installation/readiness status,
collision evidence, activation checks, and rollback without recording token
values. Pending, missing, reused, or unverified identities leave the project
analysis-only and block Slack gateway activation; they do not justify borrowing
another deployment's Slack surface.

Slack delivery obeys its immutable assignment-specific `retry_limit` of exactly
3. Stop on success. Recovery requires temporary failure; a third attempt requires materially new information or substantially different transport, with remaining resource budget. Never retry an ambiguous Slack side
effect; reconcile it first. When the bounded route is exhausted,
continue through the same project's prior durable SQLite/API state and
provider-native evidence. Do not route through another deployment's app,
channel, thread, credentials, profile, or state namespace.

Before any local CLI assignment, verify the named repository, isolated
worktree, and branch. Never launch a provider from `System32` or a generic
directory. Keep repository context, credentials, provider outputs, budgets, and
Slack threads separated by project.

Enclose human-readable project messages and safely taggable project artifacts
in the canonical contribution boundaries defined by
[`contribution-attribution-policy.md`](contribution-attribution-policy.md).
Preserve prior valid boundaries when revising an artifact. Do not insert tags
into structured protocol payloads, JSON, CSV, lock/checksum files, generated
artifacts, or any format whose behavior they could change; carry attribution in
the surrounding Slack, commit, pull-request, or handoff envelope instead. A tag
records authorship only and never grants authority or replaces correlation IDs,
run IDs, exact SHAs/digests, authenticated callbacks, review, CI, or security
evidence.

Use one event vocabulary across the protocol, intake template, workstream plan,
and state report:

| Marker | Meaning |
| --- | --- |
| `PROJECT_BOOTSTRAP` | Intake record: the shaping request copied from [`intake.md`](templates/project-bootstrap/intake.md). |
| `BOOTSTRAP` | Thread event after the Director accepts the intake: proposition, current bounds, and selected modules. |
| `PLAN` | Bounded workstreams, owners, dependencies, budget/time limits, acceptance checks, and stopping point. |
| `WORKING` | Concise in-progress status with correlation ID. |
| `EVIDENCE` | Accepted or reported result with source and uncertainty. |
| `REVIEW_READY` | Completed artifact and structured handoff for a different eligible reviewer. |
| `DECISION` | Recommendation, dissent, and rationale when no owner gate is required. |
| `OWNER_DECISION_REQUIRED` | Exact owner question when authority, scope, cost, privacy, or safety requires it. |
| `RESHAPED` | Module, provider, or scope change with updated bounds. |
| `PAUSED` | Bounded stop with next responsible party. |
| `COMPLETE` | Phase or project outcome with evidence and remaining risk. |

The minimum useful sequence is:
`PROJECT_BOOTSTRAP` → `BOOTSTRAP` → `PLAN` → (`WORKING` | `EVIDENCE`)\* →
(`REVIEW_READY` when reviewable) → (`DECISION` | `OWNER_DECISION_REQUIRED`) →
(`COMPLETE` | `PAUSED` | `RESHAPED`).

Providers communicate through the Director's correlated transport rather than
starting unbounded, cross-project conversations. They may debate when it helps:
collect independent `POSITION` statements from the seated members first, allow
only the configured bounded `REBUTTAL` rounds, preserve evidence and dissent,
and keep the discussion scoped to the active decision. The Director may invite
Antigravity through a persisted debate assignment. That separate typed contract
may relay the sanitized question, vote options, accepted declared-dependency
contributions, and a bounded Director revision request; the ordinary work
contract remains prose-free. Antigravity cannot initiate, self-route, widen, or
autonomously continue a conversation, and a seat grants no operational or
acceptance authority—see
[`team-operating-charter.md`](team-operating-charter.md).

Producer votes count at their full configured advisory weight for seated
members, including Antigravity when the Director invited it. Unavailable or
abstaining members cast zero without changing the immutable denominator or any
configured weight. Voting is still not acceptance: the producing provider
cannot be its own independent reviewer, and no vote overrides deterministic
policy, approval, budget, allowlist, exact-SHA, CI, security, or serious-safety
requirements. An ordinary tie is an evidence judgment attributed to the actual executing Director
(OpenAI by default, or Claude when its Director transport is activated); when the tied
choices materially differ in cost,
authority, safety, privacy, repository scope, runtime behavior, or project
direction, the executing Director asks the owner one focused question.

## Completion and review-to-merge relay

Every reviewable implementation assignment includes its next handoff before
work begins. When a provider finishes, it must produce a concise
`REVIEW_READY` handoff for a different eligible provider; the Director posts
or prompts that review assignment through the project thread and records the
correlation. This is a handoff request, not a self-approval.

The handoff includes:

- logical producer and all material authors;
- exact repository, base and head SHA, or artifact digest;
- changed files or artifact scope;
- acceptance checks and exact test evidence;
- known limitations, uncertainty, and security context;
- the requested review depth and the next decision.

The reviewer returns `ACCEPT`, `CHANGES_REQUIRED`, or
`SERIOUS_SAFETY_STOP` for that exact candidate. Eligible acceptance proceeds
through the project's configured independent security, CI, and deterministic
merge gates. A changed candidate restarts this evidence chain.

After required checks are green and the exact candidate is frozen, the Director
may request a GitHub Copilot Lite hygiene observation. It has no assigned
workload share, range, or ceiling, no provider adapter or automatic routing,
and no production credit, gate, or action authority. A request binds
at most three conditional attempt slots. Stop on success; recovery requires temporary failure; a third attempt requires materially new information or a substantially different transport. Never repeat ambiguous paid dispatch. Without
an authenticated contact it fails closed as unavailable; do not automate it on every push. A quota or
availability failure and every rejected finding count as waste. Validate each
finding independently and block only for a confirmed underlying defect. A new
commit invalidates the observation without an automatic rerun. If Copilot
substantively authors a fix, list it as a material author and exclude it from
independent review or acceptance of that candidate.

For ROI reporting, count only authenticated accepted direct-production effort as
productive units. Keep all attempts, failures, control-plane work, and recovery
in historical effort; categorize invoked effort without productive credit as
waste. Report estimated ROI, provider-reported cost ROI only when correlated
cost exists, first-pass yield, correction rate, duplicate validation, and quota
failures. Never invent or blend missing cost telemetry.

## Minimal integration fix lane

Before opening a new correction assignment, check whether the defect qualifies
for the up-to-three-attempt minimal-fix procedure in
[`team-operating-charter.md`](team-operating-charter.md#minimal-integration-fix-procedure).
Use it only for a tiny, unambiguous, reversible, mechanically correctable
surface with an already-established expected result and an existing focused
validation. Use the cheapest eligible method and emit the compact
[`MINIMAL_FIX` record](templates/project-bootstrap/minimal-fix.md).

The lane never covers architecture, behavior contracts, schemas, security,
privacy, credentials, dependencies, provider contracts, budgets, permissions,
deployment, publication, merge policy, unexplained failures, destructive work,
or serious-safety signals. A failed or expanding attempt immediately returns to
normal assignment. A changed candidate always receives a new exact identity
and fresh review, security, CI, and merge evidence.

## Project-shaping loop

Use this lightweight loop whenever the project is new or has changed enough to
need a new frame:

1. **Extract.** Summarize the source thread into intent, useful working
   practices, constraints, evidence, unresolved questions, and what must not
   be carried over.
2. **Re-contextualize.** Replace source-project names, goals, risks, channels,
   repositories, and definitions of done with project-local values. Keep
   unknowns explicit.
3. **Compose.** Select the smallest useful modules and providers. Give each
   active assignment one writer or one error surface; use independent research,
   testing, reproduction, or review lanes to keep other capable providers
   productively engaged without conflicting edits.
4. **Pilot.** Run the smallest reversible, bounded action that can validate the
   project frame. If access is not confirmed, pilot with analysis or public
   evidence rather than a repository write.
5. **Observe.** Compare the result with the expected evidence and stopping
   point. Correct inaccuracies before dependent work.
6. **Reshape.** Add, remove, split, merge, pause, or reorder modules; replace
   unavailable providers after the bounded attempt rule; ask the owner only
   where a choice materially changes authority, scope, risk, cost, or privacy.
7. **Promote.** When evidence is sufficient, turn the provisional profile into
   the project's durable work order, runbook, or implementation plan. Keep
   only project-local, sanitized lessons for future bootstraps.

The loop is intentionally permissive about sequence. It may move from research
to a prototype and back to discovery, or pause after one decision. What matters
is that each next action has a clear information or delivery purpose and stays
inside the project's current controls.

## Edge Engine illustration

For an Edge Engine effort, a provider can take a useful Director or Edge Oracle
thread and retain the collaboration pattern while creating an Edge Engine
profile:

- route live coordination to the Edge Engine project's configured Slack channel
  (`surfaces.slack.channel` in its profile);
- name only Edge Engine repositories, paths, refs, data, and acceptance checks;
- select research, design, implementation, QA, and review modules as needed;
- use Perplexity and Firecrawl only for bounded external evidence;
- use Claude, Cursor, and any approved supplemental lane within isolated Edge
  Engine worktrees;
- keep Edge Oracle's research-only and merge-policy specifics out unless they
  are deliberately adopted and independently approved for this project.

That is a model, not a constraint: another project may choose a different
channel, no repository, one provider, a different evidence mix, or a
research-only stopping point while still using the same protocol.

## Bootstrap checklist

Before a project moves from shaping to live execution, confirm:

- its rollout packet records deployment identity, activation evidence, and rollback;
- every concurrently active deployment has a verified distinct Slack app,
  project channel, deployment profile, and state namespace;
- a project-local proposition, outcome, and stopping point exist;
- the selected modules and provider roles are useful now;
- its channel, repositories, refs, budget, data handling, and external effects
  are either explicitly scoped or still safely analysis-only;
- each assignment has acceptance checks, a bound, and a safe fallback;
- private context and credentials from the source project are not inherited;
- reviewable work has a different-provider `REVIEW_READY` handoff;
- the plan explains how it will degrade after provider nonresponse;
- owner decisions are surfaced rather than implied.

This protocol is successful when a new project feels native to its own work
while retaining the team's strongest habits: bounded initiative, useful
provider collaboration, independent verification, clear evidence, and
project-specific control.
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 22:10 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 22:20 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:27 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:19 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:15 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 01:12 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:35 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:46 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:11 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 22:17 UTC | END]] -->
