<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:31 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 22:10 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 21:35 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | BEGIN]] -->
# Project workstream plan

Do not place secrets, credentials, tokens, or raw private source in this plan.
Workflow skills are procedure overlays, not permission grants. Canonical provider
roles, debate rules, and committee weights:
[`docs/team-operating-charter.md`](../../team-operating-charter.md). Project-local
responsibility areas and provider boundaries:
[`provider-roles-contract.yaml`](provider-roles-contract.yaml). Assign each
workstream from one of its eight responsibility areas and respect the Cursor,
Antigravity, Perplexity, and OpenAI boundaries recorded there.

## Phase frame

- **Project:** <project ID and name>
- **Current state:** <starting state>
- **Phase outcome:** <target outcome>
- **Stopping point:** <observable stopping condition>
- **Total phase budget/time:** <bounded ceiling>
- **Project channel/thread:** <slack channel ID or configured placeholder and thread>
- **Repository/ref/worktree:** <exact verified values or analysis-only>
- **Rollout packet:** <path/status from `rollout-packet.md`>
- **Deployment isolation:** <dedicated Slack app, channel, profile, and state namespace status>

## Selected modules

| Module | State | Purpose | Exit evidence |
| --- | --- | --- | --- |
| <discovery/research/design/implementation/QA/review/operations/reuse> | enabled | <why now> | <observable evidence> |

## Workstreams

Use one active writer for a file or error surface. Other lanes may research,
reproduce, test, challenge, or review independently.

| ID | Module | Provider | Workflow skill | Role | Scope/surface | Depends on | Acceptance |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <A1> | <module> | <provider> | <catalog name or none> | <producer/researcher/verifier/reviewer> | <bounded scope> | <IDs or none> | <observable check> |

## Assignment packet

Repeat for each workstream.

### <assignment ID>: <short title>

- **Starting state:** <verified facts and evidence>
- **Target outcome:** <one bounded result>
- **Scope:** <repositories, paths, queries, systems, or analysis-only>
- **Provider:** <provider>
- **Contributor/system identity:** <full provider or tool name and model/system label>
- **Workflow skill:** <stable discovered catalog name or none>
- **Authority granted:** <exactly scoped actions>
- **Authority denied:** <writes, credentials, review, merge, deployment, etc.>
- **Context supplied:** <minimum necessary context>
- **Dependencies:** <accepted result IDs or none>
- **Acceptance checks:** <observable checks>
- **Evidence required:** <tests, sources, SHA, digest, reproduction, etc.>
- **Budget/timeout/attempts:** <bounds>
- **Fallback:** <replacement, reduced scope, pause, or owner question>
- **Handoff target:** <different eligible provider or non-review next lane>

## Shared workflow-skill selection

- `cursor-agent-workflow`: use for bounded Cursor implementation, debugging,
  QA, or eligible independent exact-SHA review.
- `antigravity-sandbox-workflow`: use for disposable prototypes or behavioral
  verification allowed by the active Antigravity contract; a primary provider
  independently verifies useful output.
- `perplexity-research-workflow`: use only for bounded public,
  citation-backed research with no repository context or operational verdict.
- `pplx-cli`: use only as the terminal transport for bounded public web search
  or public-page snippets inside the same Perplexity research-only boundary.

Record `skill_discovered=true` only after the active session's refreshed
catalog exposes the selector. A missing skill does not widen the task or justify
an unbounded retry. Skill selection cannot expand scope, data exposure,
repository access, spending, review eligibility, or external-action authority.

## Coordination sequence

Event markers follow
[`project-bootstrap-protocol.md`](../../project-bootstrap-protocol.md):
`BOOTSTRAP` → `PLAN` → (`WORKING` | `EVIDENCE`)\* → (`REVIEW_READY` when
reviewable) → (`DECISION` | `OWNER_DECISION_REQUIRED`) → (`COMPLETE` |
`PAUSED` | `RESHAPED`).

1. Director posts the correlated assignment to the project Slack thread.
2. Provider executes through its bounded local CLI/API lane.
3. Director relays concise `WORKING` or `EVIDENCE` updates.
4. After the assignment-specific retry budget is exhausted, mark the provider unavailable and reshape
   scope, confidence, timing, and coverage.
5. A completed reviewable artifact emits `REVIEW_READY` to a different
   eligible provider.
6. Any candidate change invalidates prior review, security, CI, and merge
   evidence.
7. Record `COMPLETE`, `RESHAPED`, `PAUSED`, or
   `OWNER_DECISION_REQUIRED` with the next responsible party.

## Minimal correction routing

- **Eligible defect/surface:** <tiny, unambiguous, reversible surface or none>
- **Established expected state:** <existing contract or evidence>
- **Method:** <deterministic_autofix | current_session_micro_fix | batched_minor_fix>
- **Focused validation:** <existing authoritative check>
- **Attempt:** <1 of 1>
- **Exit:** <continue current chain or normal reassignment>

Use [`minimal-fix.md`](minimal-fix.md) for the compact `MINIMAL_FIX` record. Do not use this
lane for architecture, behavior, schemas, security/privacy/credentials,
dependencies, provider contracts, budgets, permissions, deployment,
publication, merge policy, unexplained failures, destructive work, or a
serious-safety signal. Any changed candidate invalidates prior review evidence.

## Debate and decision

- **Decision question:** <question or none>
- **Debate participants (`POSITION` / `REBUTTAL`):** list the Director-seated
  members. Antigravity may be seated only through a matching persisted Director
  invitation; it cannot initiate, self-route, widen, or autonomously continue
  the discussion. Its separate typed debate contract carries only the bounded,
  sanitized debate subset, while ordinary Antigravity work remains prose-free.
- **Independent positions:** <provider, position, evidence, confidence>
- **Rebuttal:** <one focused round or none>
- **Advisory votes:** <full configured weights for seated members, including
  producer votes and an invited Antigravity turn; unavailable or abstaining
  members cast zero without changing the immutable denominator>
- **Dissent/unknowns:** <material dissent>
- **Tie handling:** OpenAI evidence judgment, or one owner question when the
  tied options materially differ in cost, authority, safety, privacy, scope,
  runtime behavior, or direction.

## Owner and deterministic gates

- <Slack activation blocked until concurrent-deployment isolation is verified>
- <owner decision required>
- <security/CI/exact-SHA gate>
- <merge/deployment/publication/access gate>
- <serious-safety stop>

Contribution tags record the author of the plan or artifact. They do not prove
the provider identity or satisfy any gate above. Preserve prior valid tags and
add a new outer pair when this plan is revised; for syntax-sensitive artifacts,
use the commit, pull-request, or `REVIEW_READY` envelope instead.
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | END]] -->
<!-- [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 21:35 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 22:10 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:31 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | END]] -->
