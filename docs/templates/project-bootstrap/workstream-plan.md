# Project workstream plan

## Phase frame

- **Project:** <project ID and name>
- **Current state:** <starting state>
- **Phase outcome:** <target outcome>
- **Stopping point:** <observable stopping condition>
- **Total phase budget/time:** <bounded ceiling>
- **Project channel/thread:** <Slack destination>
- **Repository/ref/worktree:** <exact verified values or analysis-only>

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

Record `skill_discovered=true` only after the active session's refreshed
catalog exposes the selector. A missing skill does not widen the task or justify
an unbounded retry.

## Coordination sequence

1. Director posts the correlated assignment to the project Slack thread.
2. Provider executes through its bounded local CLI/API lane.
3. Director relays concise `WORKING` or `EVIDENCE` updates.
4. After three safe nonresponses, mark the provider unavailable and reshape
   scope, confidence, timing, and coverage.
5. A completed reviewable artifact emits `REVIEW_READY` to a different
   eligible provider.
6. Any candidate change invalidates prior review, security, CI, and merge
   evidence.
7. Record `COMPLETE`, `RESHAPED`, `PAUSED`, or
   `OWNER_DECISION_REQUIRED` with the next responsible party.

## Debate and decision

- **Decision question:** <question or none>
- **Independent positions:** <provider, position, evidence, confidence>
- **Rebuttal:** <one focused round or none>
- **Advisory votes:** <full configured weights, including producer votes>
- **Dissent/unknowns:** <material dissent>
- **Tie handling:** OpenAI evidence judgment, or one owner question when the
  tied options materially differ in cost, authority, safety, privacy, scope,
  runtime behavior, or direction.

## Owner and deterministic gates

- <owner decision required>
- <security/CI/exact-SHA gate>
- <merge/deployment/publication/access gate>
- <serious-safety stop>
