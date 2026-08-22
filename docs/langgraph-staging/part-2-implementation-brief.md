**[OpenAI Codex | GPT-5 | 2026-08-22 15:34 UTC | START]**

# LangGraph Migration Part 2: Durable Approval Interrupt and Resume

## Binding

- Producer: Claude
- Required independent reviewer: OpenAI Codex
- Repository: `edwardjones3976/director-orchestrator`
- Exact starting `main`: `69b1e08c511f14ce3fe6a0e196fdec8c82f3db4a`
- Reconciled Gate 0 PR #18 head: `ffd1a40492a374576af01a3f5e0d11780b7348b8`
- Reconciled Gate 0 merge commit: `422566de7278d85758f942ab7e9493112cf23bd4`
- Status: **STAGED / IMPLEMENTATION NOT STARTED**
- Writer authority: **legacy engine only**

PR #18's merge commit is an ancestor of the bound starting SHA. This assignment preserves its accepted Gate 0 evidence, provider-neutral v1 packet schemas, deterministic policy authority, assignment-scoped approvals, and legacy rollback path.

## Objective

Implement durable approval interrupts and deterministic resume behavior with assignment-scoped authority, isolated checkpoints, and recovery tests for stale approval, callback replay, restart, and idempotency.

## Required shape

- Represent approval waits as durable interrupts keyed to assignment, authority snapshot, and approval digest.
- Reject approvals created for another assignment, authority snapshot, graph version, or superseded state.
- Make callback handling and resume idempotent across duplicate delivery and process restart.
- Preserve deterministic policy as the only authority for whether approval is required.
- Add stale-approval, callback replay, restart, crash-boundary, and double-resume tests.
- No live callbacks or external writes; fixture/replay inputs only.

## Hard boundaries

- Enable graph-owned provider dispatch.
- Perform external writes from the graph.
- Deploy or enable the graph in any environment.
- Migrate existing state or checkpoints.
- Cut over writer authority from the legacy engine.
- Change deterministic policy or assignment-scoped approval authority.

The staging packet authorizes no live provider call or paid execution. The Director must issue a separately approved, budget-bound dispatch before Claude begins. If the exact starting SHA or authority blobs change, stop and rebind the packet.

## Change-surface guidance

Prefer new isolated checkpoint/interrupt modules plus narrow adapters around existing approvals and callbacks.

This guidance is intentionally modular: Claude may choose names and internal decomposition that fit the repository, while preserving the contract, authority, isolation, and test boundaries above.

## Required validation

- All pre-existing tests pass.
- All assignment-specific transition, recovery, and shadow-comparison tests pass.
- Python compilation passes.
- Docker Compose validation passes for every supported profile.
- Dependency and lock integrity checks pass.
- Provider-neutral packet schemas validate.
- Secret-hygiene checks pass.
- Independent reviewer accepts the exact implementation head.

CI evidence must include the repository's existing Linux and Windows tests, compilation, JSON/schema checks, Compose/build coverage, secret hygiene, and recovery probe, plus explicit dependency-lock integrity. No passing status may be inferred from this staging-only PR.

## Review and next gate

OpenAI Codex performs an independent exact-head review. Integration remains blocked until Part 1 has an accepted outcome digest. Claude's own vote may count in team deliberation but is not an independent acceptance review of Claude-authored migration work. Merge, deployment, enablement, state migration, and writer cutover remain separate owner-gated decisions.

**[OpenAI Codex | GPT-5 | 2026-08-22 15:34 UTC | END]**
