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

```yaml
project:
  id: "<short-stable-project-id>"
  name: "<working name>"
  proposition: "<what this project may achieve or clarify>"
  source_thread: "<optional link or identifier>"
  state: "exploring | framing | building | validating | operating | paused | closed"

outcomes:
  desired: ["<tentative or confirmed outcomes>"]
  non_goals: ["<things this project is not trying to do>"]
  stopping_point: "<decision, evidence, budget, deadline, or safety condition>"

surfaces:
  slack_channel: "<dedicated project channel, or none>"
  repositories: []                 # exact names only when approved
  starting_refs: []                # durable refs only when approved
  systems_and_data: ["<known scope or 'unknown'>"]
  browser_conversations: []        # continuity only, never execution authority

modules:
  discovery: enabled
  research_and_evidence: optional
  design_and_decision: optional
  implementation: optional
  quality_and_security: optional
  review_to_merge: optional
  operations: disabled
  learning_and_reuse: optional

team:
  director: "OpenAI"
  primary_lanes: ["Claude", "Cursor"]
  supplemental_lanes: ["Gemini/Antigravity", "Perplexity", "Firecrawl"]
  coordination_transport: "Slack"
  execution_transport: "local provider CLI in verified project worktree"
  canonical_review_and_merge: "GitHub"
  conversation_continuity: "existing browser conversations"

controls:
  budget: "<per-phase ceiling or owner decision required>"
  timeout: "<per-phase bound>"
  evidence_standard: "<what must be demonstrated>"
  external_effects: "analysis_only | explicitly scoped"
  owner_decisions: ["<known decisions that cannot be inferred>"]
  data_handling: "<public, private, sensitive, or unknown>"
  acceptance_checks: ["<observable checks>"]

working_assumptions:
  confirmed: []
  provisional: []
  unknown: []
  not_applicable: []
```

The profile may begin as a short list rather than complete YAML. The fields
exist to expose uncertainty and prevent accidental inheritance; they are not a
requirement to perform paperwork before useful exploration.

## Companion template kit

Copy only the artifacts the project needs from
[`docs/templates/project-bootstrap/`](templates/project-bootstrap/):

- [`intake.md`](templates/project-bootstrap/intake.md) molds a source thread
  into a concise `PROJECT_BOOTSTRAP` request.
- [`project-profile.yaml`](templates/project-bootstrap/project-profile.yaml)
  records the living project-local profile.
- [`workstream-plan.md`](templates/project-bootstrap/workstream-plan.md)
  composes bounded modules, provider assignments, workflow skills, and
  fallbacks.
- [`review-ready.md`](templates/project-bootstrap/review-ready.md) carries an
  exact candidate from its producer to a different eligible reviewer.
- [`project-state-report.md`](templates/project-bootstrap/project-state-report.md)
  reports bootstrap, milestone, reshaping, pause, or completion state.

The templates are scaffolding, not mandatory ceremony. Start with the intake
and add the other files only when they improve coordination, evidence, or
repeatability. Delete irrelevant fields and mark material uncertainty instead
of filling placeholders with guesses.

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
assignment.

| Provider or capability | Good project-local use | Never infer from this role |
| --- | --- | --- |
| OpenAI / Director | framing, orchestration, evidence synthesis, plan reshaping, acceptance synthesis, ordinary tie judgment | authority to remove deterministic policy or approve its own producer work |
| Claude | repository-scale reasoning, implementation, debugging, architecture, independent review | access to an unnamed repository, self-review, merge, deployment, or external representation |
| Cursor | full-spectrum implementation, reliability, QA, architecture, independent review, technical leadership | an overflow-only role, self-review, or a durable execution target other than the named one |
| Gemini / Antigravity | bounded subordinate scaffolding, mechanical work, sandboxed alternate ideas, explicitly limited experiments | primary authority, unrestricted project context, final review, acceptance, merge, or methodology truth |
| Perplexity | current public-source research, source discovery, fact and method cross-checking | repository context, programming, voting, review verdicts, Slack posts, merges, deployments, or permission changes |
| Firecrawl | collection, extraction, normalization, and provenance of approved web material | a decision, authority, or a substitute for evidence evaluation |
| Codex Security | independent security evidence for eligible repository changes | product direction, acceptance, merge, or deployment authority |

### Shared provider workflow skills

When the refreshed shared skill catalog exposes the following stable names, the
Director may select the matching workflow as a project-local execution overlay:

| Skill selector | Provider lane | Use it for | Preserved boundary |
| --- | --- | --- | --- |
| `cursor-agent-workflow` | Cursor | implementation, debugging, QA, and independent exact-SHA review | Verify the named repository, worktree, and branch; Cursor cannot review work it materially authored. |
| `antigravity-sandbox-workflow` | Gemini/Antigravity | disposable prototypes and bounded behavioral verification | The sandbox output is non-authoritative; the current Antigravity outbound contract and primary-provider verification still apply. |
| `perplexity-research-workflow` | Perplexity | bounded public research with citations and provenance | Send only the approved public query; no repository context, programming, voting, review verdict, or external-write authority. |

Use the catalog name in the assignment rather than a machine-specific absolute
path. Skill discovery is required before selection; installation or a reported
structural evaluation does not prove that the active provider session has
loaded the skill. If a selected skill is unavailable after refresh, continue
with the provider's baseline contract when that still satisfies acceptance, or
reshape the plan under the three-attempt availability rule.

A skill is a procedure, not a capability grant. It may narrow how an assignment
is performed and strengthen its evidence, but cannot widen the project profile,
provider role, data exposure, budget, repository access, review eligibility, or
external-action authority. Use no skill merely because it exists; select it
only when its workflow materially improves the active module.

Availability is not a reason to fabricate coverage. Each safe provider contact
is bounded and recorded. After three nonresponses or safe failed attempts, mark
the provider unavailable for that assignment cycle, preserve partial output as
untrusted evidence, and reshape the plan around the available team. Record the
change in scope, confidence, timing, and coverage. Do not repeat an ambiguous
paid dispatch merely to reach three attempts; reconcile the run first.

## Communication and project-local collaboration

Slack is the live coordination surface; GitHub is the canonical source for
review and merge evidence; local provider CLIs are the preferred execution
transport; browser conversations preserve continuity only.

For each live project, use its dedicated project channel and one
Director-owned, assignment-correlated thread. For the existing workspace:

- **Edge Engine:** `#edge-engine`
- **Director Orchestrator:** `#project-director`
- **Edge Oracle:** `#edge-oracle-ops`
- **Workspace-wide announcements:** `#all-edgeengine`, only when the message
  is explicitly relevant beyond its project

Before any local CLI assignment, verify the named repository, isolated
worktree, and branch. Never launch a provider from `System32` or a generic
directory. Keep repository context, credentials, provider outputs, budgets, and
Slack threads separated by project.

The minimum useful event sequence is:

1. `BOOTSTRAP` — project proposition, current bounds, selected modules.
2. `PLAN` — bounded workstreams, owners, dependencies, budget/time limits,
   acceptance checks, and stopping point.
3. `WORKING` / `EVIDENCE` — concise, correlated status or result with
   source and uncertainty.
4. `REVIEW_READY` — the completed artifact and a structured handoff for an
   eligible different-provider reviewer.
5. `DECISION` / `OWNER_DECISION_REQUIRED` — the recommendation, dissent,
   exact question, and decision needed.
6. `COMPLETE`, `PAUSED`, or `RESHAPED` — outcome, evidence, remaining
   risk, and next responsible party.

Providers communicate through the Director's correlated transport rather than
starting unbounded, cross-project conversations. They may debate when it helps:
collect independent positions first, allow one focused rebuttal round, preserve
evidence and dissent, and keep the discussion scoped to the active decision.

Producer votes count at their full configured advisory weight. Voting is still
not acceptance: the producing provider cannot be its own independent reviewer,
and no vote overrides deterministic policy, approval, budget, allowlist,
exact-SHA, CI, security, or serious-safety requirements. An ordinary tie is an
OpenAI evidence judgment; when the tied choices materially differ in cost,
authority, safety, privacy, repository scope, runtime behavior, or project
direction, OpenAI asks the owner one focused question.

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

- route live coordination to `#edge-engine`;
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
