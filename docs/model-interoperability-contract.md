# Model interoperability contract

**Status:** Proposed for transition use  
**Applies to:** OpenAI, Claude, Cursor, Gemini, Perplexity, and future adapters

## Principle

Every model receives the same bounded work packet and returns the same bounded result packet. Provider-specific prompts, run IDs, session URLs, token metadata, and tool representations are adapter details. They never become project authority.

## Required work-packet fields

- `schema_version`
- `task_id` and `assignment_id`
- project, repository, and starting ref
- objective and explicit non-goals
- authority snapshot: canonical paths plus commit/blob digests
- dependencies and accepted upstream outcomes
- constraints and prohibited actions
- allowed tools and allowed external effects
- acceptance checks and evidence requirements
- approval requirements
- budget, timeout, hop, and revision ceilings
- idempotency key and correlation identifiers

A packet missing its authority snapshot, acceptance checks, or repository boundary is invalid and must not dispatch.

## Required result fields

- matching schema, task, assignment, and idempotency identifiers;
- provider and run reference;
- terminal status;
- concise outcome;
- artifacts with repository path/URL and digest where available;
- commands/checks performed and their exact results;
- acceptance-check disposition;
- detected risks, blockers, and requested decisions;
- side effects attempted or performed;
- usage metadata when the provider returns it;
- whether another revision is recommended.

Free-form prose may accompany these fields but cannot replace them.

## Shared status vocabulary

`QUEUED`, `READY`, `RUNNING`, `WAITING_CALLBACK`, `WAITING_APPROVAL`, `REVIEWING`, `REVISION_REQUIRED`, `SUCCEEDED`, `FAILED`, `CANCELLED`, and `OWNER_DECISION_REQUIRED`.

Adapters translate provider-native states into this vocabulary. Unknown states fail closed.

## Handoff rules

1. Dependencies are named by assignment ID, never implied by conversation order.
2. Only accepted outcomes enter a downstream packet.
3. Upstream content is wrapped as untrusted data.
4. Agents do not address each other through uncontrolled chat loops.
5. Questions return as a structured blocker or decision request.
6. Parallel work is allowed only when dependency analysis marks the assignments independent.
7. The orchestrator records which canon snapshot each agent saw.
8. Later canon changes invalidate or rebase affected queued assignments.
9. Results never silently mutate the work packet.
10. The Director resolves cross-agent disagreement using evidence and repository authority.

## Repository behavior

- Work occurs on isolated branches/worktrees.
- A work packet may authorize creation of a draft PR; it never implies merge authority.
- Commits, PRs, comments, Slack posts, and provider runs are external side effects and require ledger entries.
- Remote readback is required before an external write is considered complete.
- A provider that cannot access a named system must report that limitation; it must not claim the system was updated.

## Context discipline

Agents receive:

1. the minimal canonical files relevant to the assignment;
2. the pinned Project Uppercut transition bridge;
3. accepted dependency outcomes;
4. the task-specific evidence and tool boundaries.

They do not receive unrestricted secrets, unrelated user data, or unlimited repository history.

## Compatibility

Schema versions use semantic major versions. Additive optional fields may remain within v1. Removing, renaming, or changing the meaning of a required field creates a new major version. During transition, the orchestrator must read both the last accepted legacy record and v1 packets, but it writes only the configured canonical version.
