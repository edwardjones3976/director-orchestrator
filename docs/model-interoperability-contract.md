<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 03:06 UTC | BEGIN]] -->
# Model interoperability contract

**Status:** Owner-accepted v1 documentation/schema authority, effective when PR #18 merges; adapter conformance remains unaccepted
**Applies to:** OpenAI, Claude, Cursor, Grok, Gemini, Perplexity, and future adapters

## Acceptance boundary

The owner accepted this provider-neutral contract authority on 2026-08-16 after Gate 0 acceptance. Merging these documents and schemas authorizes fixture and adapter-conformance work only. It does not install LangGraph, enable a graph, dispatch a provider, migrate state, change the sole writer, or permit an external write.

OpenAI, Claude, and Cursor are the primary team. Gemini, Perplexity, and future providers may implement the same contract later without widening its authority.

## Principle

Every model receives the same bounded work packet and returns the same bounded result packet. Provider-specific prompts, run IDs, session URLs, token metadata, and tool representations are adapter details. They never become project authority.

## Required work-packet fields

- `schema_version`
- `task_id` and `assignment_id`
- project, repository, and starting ref
- objective and explicit non-goals
- authority snapshot: canonical paths plus commit/blob digests and one deterministic snapshot digest
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
- the authority-snapshot digest and matching correlation identifiers;
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
6. Parallel local analysis and read-only work are allowed only when dependency
   analysis marks the assignments independent. Parallel external provider
   dispatch additionally requires its separately accepted LangGraph graduation
   gate, bounded concurrency, idempotency, budgets, cancellation, recovery,
   deterministic aggregation, and an active kill switch.
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

## Formal v1 authority decision

**Decision:** ACCEPTED for documentation and JSON Schema authority, effective when PR #18 merges.

The accepted schemas fail closed on unknown fields and require:

- task, assignment, idempotency, correlation, and authority-snapshot identifiers;
- exact canonical file commit/blob references plus a deterministic snapshot digest;
- explicit dependencies, boundaries, approvals, acceptance checks, evidence, and resource ceilings;
- a terminal result with acceptance dispositions, risks, blockers, requested decisions, normalized usage, and a revision recommendation;
- an idempotency key for every reported external side effect.

The checked examples validate against JSON Schema Draft 2020-12, while negative validation cases prove that omitted dependencies, authority blob identity, correlation, and revision recommendation are rejected.

This decision does not accept an adapter implementation. Phase 1 still requires fixture-based conformance tests for each adapter before any runtime packet dispatch is enabled.

## Compatibility

Schema versions use semantic major versions. Additive optional fields may remain within v1. Removing, renaming, or changing the meaning of a required field creates a new major version. During transition, the orchestrator must read both the last accepted legacy record and v1 packets, but it writes only the configured canonical version.

## Replay-run documents

Offline replay fixtures bind one validated work packet to its validated result packet in a `director-replay-run` document. The v1 replay schema is `docs/schemas/model-replay-run-v1.schema.json`.

`director_orchestrator.packet_contracts` writes and loads these documents symmetrically. It canonicalizes JSON with RFC 8785, records SHA-256 digests for both packets and the replay envelope, rejects duplicate keys and documents over 1 MiB, verifies task/assignment/idempotency/authority/correlation linkage, and can compare the envelope against an independently trusted digest. The `replay_run_digest` is calculated over the canonical envelope with that field omitted, avoiding a self-referential hash.

A self-contained digest detects accidental or unsynchronized modification; it is not a signature and grants no authority. Acceptance, dispatch, external writes, and merge decisions remain governed by deterministic policy and separately trusted evidence.
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 03:06 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]] -->
