<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:50 UTC | BEGIN]] -->
# Archived: LangGraph control-plane transition plan (pre-primary)

**HISTORICAL / NON-AUTHORITATIVE**

This file preserves the pre-primary transition plan exactly as it stood before
LangGraph-primary operations authority was introduced. Its phase gates, SQLite-first
storage assumptions, shadow-mode constraints, and runtime activation tables describe
historical evidence and design intent at that time — not current operating
instructions.

For intended operations authority after merge and activation, see
[langgraph-primary-operations.md](../../langgraph-primary-operations.md). The tombstone
at [langgraph-transition-plan.md](../../langgraph-transition-plan.md) links here.

---

<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 03:06 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:09 UTC | BEGIN]] -->
<!-- [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]] -->
# LangGraph control-plane transition plan

**Status:** Gate 0 accepted; guarded runtime foundations merged; production cutover not accepted
**Owner:** Aesop / Ed
**Orchestration authority:** Director policy and deterministic code, never a model
**Director baseline:** `director-orchestrator` main at `a299590e9999dd6eaf22e0181e025be396b58ed3`
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
7. Provider identity never grants authority. OpenAI, Claude, Cursor, Grok, Gemini, and Perplexity consume the same validated work contract.
8. Provider-specific data stays inside adapter envelopes; canonical state remains provider-neutral.
9. Existing task IDs, API endpoints, Slack commands, allowlists, and callback validation remain compatible until a separately approved change.
10. Project Uppercut combat canon, C++ authority, Decision 0040, and First Bell gates cannot be altered by an orchestration migration.

## Capability restrictions and graduation

The migration distinguishes a temporary lack of production authority from a
permanent unsafe design. The controlling policy is:

> Unrestricted host completion runners, production-affecting LangGraph
> execution, and LangGraph-owned parallel external dispatch remain disabled
> until their respective evidence-based graduation gates are independently
> accepted. Structured allowlisted completion gates and isolated shadow
> execution are permitted. Regex may supplement privacy enforcement but shall
> never constitute the sole privacy control.

- **Host completion runners — temporarily restricted in unrestricted form.**
  The isolated proof runtime may execute only fixed, registered commands with a
  sanitized environment, `shell=False`, timeouts, output ceilings, exact-SHA
  binding, process-tree termination, and independently verified results.
  Arbitrary host-shell execution remains prohibited.
- **Production-affecting LangGraph execution — temporarily restricted.** Tests,
  offline replay, and isolated shadow comparison are permitted. LangGraph may
  not become the production decision or side-effect owner until independent
  review, legacy-versus-graph comparison, restart recovery, approval durability,
  rollback, and explicit cutover acceptance are complete.
- **LangGraph-owned parallel external dispatch — temporarily restricted.**
  Parallel local analysis and read-only work are permitted. Parallel provider
  calls graduate only after correlation, idempotency, budgets, cancellation,
  recovery, bounded concurrency, deterministic aggregation, and a kill switch
  are proven without duplicate charges, callbacks, assignments, or writes.
- **Regex-only privacy enforcement — permanently prohibited as the sole
  boundary.** Typed allowlisted packets, explicit field selection,
  provider-specific policy, data-flow separation, and fail-closed validation are
  primary. Regex and secret scanners remain defense-in-depth only.

Each temporary restriction has its own graduation gate. Acceptance of one does
not authorize either of the others, and model agreement cannot substitute for
the required deterministic and independent evidence.

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
- Load and write offline replay-run fixtures through the same RFC 8785 canonicalization and SHA-256 verification path.
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

## Runtime activation switches

The packet coordinator (`langgraph_runtime.LangGraphPacketRuntime`) is now
constructible from configuration by `runtime_activation.RuntimeActivation`, but it
remains off by default and cannot reach a provider without every switch below.

| Setting | Default | Effect |
| --- | --- | --- |
| `LANGGRAPH_RUNTIME_ENABLED` | off | Activates the coordinator as an internal coordinator |
| `LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED` | off | Additionally permits live provider dispatch |
| `ALLOW_PAID_RUNS` | off | Required as well before any live dispatch |
| `LANGGRAPH_RUNTIME_CHECKPOINT_PATH` | sibling of `SQLITE_PATH` | Coordinator checkpoints |
| `LANGGRAPH_RUNTIME_LEDGER_PATH` | sibling of `SQLITE_PATH` | Dispatch ledger and writer ownership |
| `LANGGRAPH_RUNTIME_CHECKPOINT_NAMESPACE` | `director-<env>-runtime-v1` | Must differ from the shadow namespace |
| `LANGGRAPH_RUNTIME_WRITER_OWNER_ID` | `langgraph-packet-runtime` | Writer ownership identity |

Live dispatch additionally requires a configured execution profile
(`PROJECT_ALLOWLIST`, `REPOSITORY_ALLOWLIST`, `ALLOWED_STARTING_REFS`, and
`CLAUDE_CALLBACK_SECRET`). Startup fails closed when the coordinator is enabled and
the runtime checkpoint, runtime ledger, shadow checkpoint, shadow resume-claim, and
`SQLITE_PATH` databases are not all distinct files.

### Single writer

Exactly one engine may own provider dispatch and external side effects for a task.
When the coordinator holds live dispatch authority, the legacy engine surrenders it:
`OrchestrationEngine` stops at `OWNER_DECISION_REQUIRED` *before* reserving an
external effect key rather than racing for the same key. `/health` reflects this —
`langgraph.legacy_sole_writer` becomes false once live dispatch is active.

### Adapter boundary

An activated coordinator sets `require_packet_boundary_adapter`, so live dispatch
only accepts `langgraph_runtime.PacketBoundaryAdapter`. That adapter revalidates the
work packet against `model-work-packet-v1` and hands the transport canonical RFC 8785
bytes, then validates the raw reply against `model-result-packet-v1` and re-checks
work/result linkage before anything can reach the dispatch ledger. The v1 packet
contract is therefore the live boundary in both directions, not a convention.
<!-- [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]] -->

<!-- [[Claude | Claude Opus 5 | 2026-08-25 | 03:26 UTC | BEGIN]] -->
### Recovering a stranded dispatch

A live dispatch reserves its idempotency key as `DISPATCHING` in one committed
transaction, and `run_packet` only moves it to `RECONCILIATION_REQUIRED` from its
exception handler. A hard kill — SIGKILL, power loss, container eviction — can land
between the two and leave a durable `DISPATCHING` row that no ordinary edge accepts:
`run_packet` refuses to resend it, `open_reconciliation` accepts only a legacy
`CLAIMED` row, and `reconcile_result` accepts only `RECONCILIATION_REQUIRED`.

`LangGraphPacketRuntime.recover_stranded_dispatch` is the bounded recovery edge for
that one case. It performs exactly one transition, `DISPATCHING` to
`RECONCILIATION_REQUIRED`, and nothing else.

| Property | Behaviour |
| --- | --- |
| Explicit | Requires `dispatch_process_confirmed_stopped=True`; the default refuses |
| Bound | Requires the task writer token and the exact packet, provider, and idempotency-key bindings already recorded in the ledger |
| Fail-closed | `CLAIMED`, `REJECTED`, a wrong writer, or a mismatched binding raises; the row is left untouched |
| Idempotent | A repeat call reports that the dispatch is already awaiting reconciliation |
| Concurrency-safe | `BEGIN IMMEDIATE` plus a status-guarded update, so concurrent callers transition the row once |
| Never infers completion | Contacts no provider, resends nothing, and records no result |

Recovery does not decide what the provider did. The owner must still bind
authenticated readback evidence through `reconcile_result`, which remains the only
path that can write a result packet.

The attestation is required because a `DISPATCHING` row is indistinguishable from a
dispatch still in flight in another worker, and the ledger cannot settle that
question. A wrong attestation still fails closed rather than racing: the live
worker's result write is guarded on `CLAIMED`/`DISPATCHING`, so it loses its
reservation, its result is refused, and the row stays in reconciliation.
<!-- [[Claude | Claude Opus 5 | 2026-08-25 | 03:26 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-25 | 01:09 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 03:06 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]] -->

---

**End of archived pre-primary transition plan.**
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:50 UTC | END]] -->
