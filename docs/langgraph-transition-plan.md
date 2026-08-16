# LangGraph control-plane transition plan

**Status:** Gate 0 accepted; documentation/schema authority proposed for merge; runtime transition not started
**Owner:** Aesop / Ed
**Orchestration authority:** Director policy and deterministic code, never a model
**Director baseline:** `director-orchestrator` main at `3454e4369857cf9030cbb366d09f7c1b83959871`
**Uppercut checkpoint:** `Project-Uppercut` main at `af4cafcb727ce837f62f99fd16f07704b7122904`
**Project consumer:** `edwardjones3976/Project-Uppercut`

## Outcome

Introduce LangGraph beneath the existing Director Orchestrator as a durable state-and-transition engine without changing the public Slack/API surface, provider roles, owner approval authority, project allowlists, or GitHub safety rules. The migration is incremental, reversible, and single-writer at every stage.

LangGraph does not become a second Director. It implements the workflow that the Director and policy code authorize.

## Gate 0 — accepted 2026-08-16

The owner accepted Gate 0 in [Project Uppercut Issue #48](https://github.com/edwardjones3976/Project-Uppercut/issues/48#issuecomment-5306397247) after the [final evidence and rollback bundle](https://github.com/edwardjones3976/Project-Uppercut/issues/48#issuecomment-5306393066) was recorded. That acceptance closes the prerequisite; it does not itself start LangGraph, change the sole writer, or authorize a deployment.

### 0A. Isolated deployment — accepted

- Director PR #20 merged at `b54efdb749bda656adf6a8135d828da3165d34f7`.
- Director PR #22 merged at `3454e4369857cf9030cbb366d09f7c1b83959871` with the live-validation, approval-binding, callback, provider, and recovery corrections.
- The ignored `.env.uppercut`, Compose project `project-uppercut-director`, ports 18080/15678, three isolated volumes, task database, callback secret, allowlists, budgets, and Slack routing remain separate from the primary profile.
- Paid execution was staged closed, provider readiness was checked, and failure paths close the paid and Slack gates.

### 0B. Canonical Uppercut snapshot — accepted

- Uppercut PR #45 merged at `2f9900c2304403cda099a3629e609e132de7a2e8` with the first repaired snapshot.
- Corrective Uppercut PR #57 merged at `af4cafcb727ce837f62f99fd16f07704b7122904`, embedding Director merge `3454e4369857cf9030cbb366d09f7c1b83959871`.
- Post-merge comparison proved all 46 canonical blobs exact, with only `SOURCE_SNAPSHOT.md` and `requirements.lock.txt` as declared overlays.
- Exact-head Linux, Windows, Director, Compose, source, and provenance-relevant checks passed before merge.

The standalone `director-orchestrator` repository remains the upstream runtime source. The embedded copy is a pinned compatibility snapshot, not an independently editable fork.

### 0C. Known-good checkpoint — accepted

- OpenAI planning/acceptance, Claude work, and Cursor independent review completed one bounded analysis task and one isolated-branch/draft-PR task.
- Assignment-specific approval, stale approval rejection, callback replay/conflict handling, restart recovery without duplicate dispatch, SQLite backup/restore, and disposable n8n restore were proved.
- Four owner-authorized accelerated recovery observations passed, including a controlled isolated-orchestrator restart; the superseded Monday heartbeat was paused.
- The primary deployment remained separate and unchanged; rollback commands and volume identifiers are recorded in Issue #48.

Issue #58 remains outside orchestration Gate 0. It blocks First Bell and gameplay product-evidence claims on affected paths, not the read-only transition phases below.

## Invariants throughout migration

1. Owner approval remains binding and assignment-scoped.
2. Models cannot remove approval, repository, branch, cost, timeout, hop, or revision limits.
3. Exactly one component may execute external side effects for a task.
4. GitHub, Slack, provider callbacks, and documents are untrusted inputs.
5. Every external write uses an idempotency key and is recorded in a side-effect ledger.
6. A checkpoint restores workflow state; it does not pretend to undo a commit, comment, provider run, or message.
7. Provider identity never grants authority. OpenAI, Claude, Cursor, Gemini, and Perplexity consume the same validated work contract.
8. Provider-specific data stays inside adapter envelopes; canonical state remains provider-neutral.
9. Existing task IDs, API endpoints, Slack commands, allowlists, and callback validation remain compatible until a separately approved change.
10. Project Uppercut combat canon, C++ authority, Decision 0040, and First Bell gates cannot be altered by an orchestration migration.

## Target graph

1. Intake and deduplication
2. Project/profile resolution
3. Canon and repository snapshot
4. Deterministic policy preflight
5. Director planning
6. Plan validation
7. Owner approval interrupt when required
8. Dependency-ready assignment scheduling
9. Provider dispatch through an adapter
10. Callback/poll normalization
11. Evidence validation
12. Independent review
13. Revision loop or escalation
14. Side-effect authorization
15. GitHub/Slack write
16. Remote readback
17. Terminal record and summary

Failure routes are explicit: retry, quarantine, owner decision, cancellation, or terminal failure. No free-form group chat controls routing.

## Canonical state

The first graph state must remain intentionally small and typed:

- schema version and task ID;
- source event and idempotency key;
- project, repository, and starting ref;
- canonical document paths and commit SHAs;
- validated plan and assignment DAG;
- dependency outcomes;
- provider run references;
- evidence manifest and acceptance results;
- pending approval scope;
- side-effect ledger;
- budget, hop, revision, and timeout counters;
- current state, previous state, and transition reason;
- rollback/recovery reference.

Prompts and full provider transcripts are artifacts referenced by digest, not unlimited graph state.

## Phases

### Phase 1 — contract first

- Adopt the versioned provider-neutral work and result packet schemas. The v1 documentation/schema authority becomes accepted when this pull request merges; runtime adoption remains separately gated.
- Add contract tests against every adapter.
- Reject unknown schema versions and missing authority snapshots.
- Preserve current engine execution.

**Exit:** every configured provider can receive the same fixture and return a normalized result without a live call.

### Phase 2 — graph skeleton in shadow mode

- Add LangGraph behind a disabled feature flag.
- Map current task states to graph nodes without dispatching providers.
- Use a separate checkpoint namespace/database.
- Reproduce dry-run plans and approval requirements.
- Emit comparison reports only.

**Exit:** legacy and shadow paths agree on task state, plan shape, gates, and terminal classification across fixtures and recovery tests.

### Phase 3 — read-only dual run

- Feed identical intake events to legacy and graph planners.
- Permit model calls only through the existing budget gate.
- Disable all graph-owned external writes.
- Compare normalized plans, assignments, approvals, evidence expectations, and cost envelopes.
- Classify differences as expected, defect, or Director decision.

**Exit:** an approved comparison set shows no safety or authority regression. Exact prose equality is not required.

### Phase 4 — canary execution, legacy writer

- Let LangGraph schedule a bounded subset of reversible tasks.
- Keep the legacy engine as the only GitHub/Slack side-effect writer.
- Require exact correlation among graph checkpoint, provider run, task, branch, PR, and Slack thread.
- Exercise restart, duplicate callback, provider timeout, cancellation, and stale approval cases.

**Exit:** canaries complete without duplicate dispatch or write, state loss, authority widening, or budget escape.

### Phase 5 — LangGraph single-writer canary

- Move side-effect ownership for explicitly allowlisted canary tasks to LangGraph.
- Disable the matching legacy writer before enabling the graph writer.
- Start with documentation-only branches and draft PRs.
- Require owner approval and remote readback.

**Exit:** the owner accepts the canary evidence and rollback drill.

### Phase 6 — controlled cutover

- Make LangGraph the default for the Project Uppercut deployment only.
- Retain the legacy path behind an emergency rollback flag for a fixed observation window.
- Keep the shared deployment unchanged.
- Publish a checkpoint and operations report after the window.

**Exit:** no unresolved correctness, security, cost, recovery, or interoperability defect.

### Phase 7 — retirement

- Remove legacy orchestration only after state/export compatibility is proven.
- Archive migration-only flags and comparison tooling.
- Keep packet schemas, side-effect ledger, traces, and recovery tests.

## Storage and deployment

- Keep SQLite for the first shadow and canary phases; do not combine a LangGraph migration with a PostgreSQL migration.
- Store graph checkpoints separately from existing task tables, linked by task ID.
- Use an explicit namespace for Project Uppercut.
- Do not place secrets or raw provider credentials in checkpoints or traces.
- Make trace export opt-in and redact prompts/artifacts according to repository policy.
- Pin LangGraph and transitive dependencies before any live phase.

## Test matrix

Required before cutover:

- duplicate Slack intake;
- restart at every node;
- stale and matching approval buttons;
- callback replay and conflicting callback;
- provider accepted/running/failed/timeout/cancelled outcomes;
- dependency missing, forward, failed, and successful;
- budget/hop/revision/timeout exhaustion;
- repository/ref/allowlist rejection;
- schema-version rejection;
- malformed and adversarial provider output;
- side-effect idempotency;
- graph-to-legacy rollback;
- Project Uppercut dry run and one reversible live task;
- no secret in logs, checkpoints, artifacts, or Git diff.

## Rollback

At any phase:

1. close paid execution;
2. stop new intake;
3. allow or cancel the single recorded writer;
4. export graph/task/side-effect state;
5. switch the deployment flag to the last accepted engine;
6. restart against the same isolated Uppercut profile;
7. reconcile provider runs and GitHub/Slack writes by idempotency key;
8. resume only after owner review.

Rollback never deletes evidence or rewrites Git history.

## Work ownership

- **Owner/Director:** authority, exceptions, cutover, rollback, and final acceptance.
- **Control-plane implementation:** graph, persistence, state mapping, compatibility, and recovery.
- **Provider adapters:** translate the neutral packet; they do not own workflow state.
- **Independent review:** compares behavior, security, evidence, and provider parity.
- **Project Uppercut team:** validates that migration cannot change combat canon or evidence meaning.

No model may approve its own implementation or merge its own migration PR.
