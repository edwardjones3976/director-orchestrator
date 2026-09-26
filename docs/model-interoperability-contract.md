<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:42 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 03:06 UTC | BEGIN]] -->
# Model interoperability contract

**Status:** Owner-accepted v1 documentation/schema authority, effective when PR #18 merges; adapter conformance remains unaccepted
**Applies to:** OpenAI, Claude, Cursor, Grok, Gemini, Perplexity, and future adapters

## Acceptance boundary

The owner accepted this provider-neutral contract authority on 2026-08-16 after Gate 0 acceptance. Merging these documents and schemas authorizes fixture and adapter-conformance work only. It does not install LangGraph, enable a graph, dispatch a provider, migrate state, change the sole writer, or permit an external write.

OpenAI, Claude, Cursor, and Grok are primary-capable lanes. The owner-approved allocation profiles also account for Antigravity, Perplexity, Firecrawl, and GitHub Copilot. Each requires its actual bounded adapter capability; a nonzero target never expands this contract or authenticates readiness. See [Provider allocation profiles](provider-allocation-profiles.md).

## Principle

Every model receives the same bounded work packet and returns the same bounded result packet. Provider-specific prompts, run IDs, session URLs, token metadata, and tool representations are adapter details. They never become project authority.

**Adapter detail, accepted by the owner on 2026-09-23 as a Gate-0 amendment (recorded on the pull request that adds it):** the work packet above stays the Director-side authority, validated in full and bound by `work_packet_digest`. On the native CLI work-packet lanes, the OpenAI Responses lane, and the Claude and Grok host-bridge lanes, the model is shown a `director-compact-work-view/v1` document derived from it: the packet's fields under the view's own schema version, with the snapshot inventory replaced by its commit, digest, file count, and at most 64 entries under owned paths or named in the objective, never under prohibited paths, plus the packet's `work_packet_digest`. Every result is still validated and rebound against the full packet. The cursor, github-copilot, native Codex, and Antigravity host-bridge lanes still receive the full packet or the Antigravity closed directive.

**Compact view schema (2026-09-24).** The adapter detail above is unchanged. The `director-compact-work-view/v1` document is defined by `docs/schemas/director-compact-work-view-v1.schema.json`, whose fields reference this contract's work-packet schema, and every view is validated against it before a transport sends it. For a small read-only task only (`docs/human-out-of-the-loop-operation.md`), the view also carries a bounded `contents` array: the text of each path the request names, read from the git blob the packet's snapshot binds and bound by that blob hash. Results are still rebound to the full packet.

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
- budget, timeout, hop, and revision ceilings, including an immutable
  assignment retry limit of exactly 3
- idempotency key and correlation identifiers

A packet missing its authority snapshot, acceptance checks, or repository boundary is invalid and must not dispatch.
The retry limit is a ceiling of three total attempts, never a requirement to
perform three calls. Stop on success. Attempt two requires a classified temporary
failure. Attempt three requires materially new information or a substantially
different underlying transport; a corrected approach alone is insufficient.
Every attempt remains subject to resource and authority gates. After three
recorded failures the assignment route is unavailable. An ambiguous paid dispatch
is never repeated, including through a fallback; reconcile it first.
For v1 paid revisions, the additional attempt also requires authenticated,
durable assignment token accounting; primary attempts are unaffected. The
current runtime has no generic token authority and therefore fails paid
revisions closed rather than treating pre-stage `budget_units` or optional
provider telemetry as token accounting.

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
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:42 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | END]] -->
