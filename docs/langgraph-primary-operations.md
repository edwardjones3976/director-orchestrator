<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:55 UTC | BEGIN]] -->
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:50 UTC | BEGIN]] -->
# LangGraph-primary operations authority

**Status:** implementation candidate — not active until merged, deployed, and activation evidence passes
**Owner:** Aesop / Ed
**Orchestration authority:** deterministic policy and code (`policy.py`), never model output
**Repository:** `edwardjones3976/director-orchestrator`
**Candidate branch:** `codex/langgraph-primary-runtime`

**Director documentation metadata (not Long Table canary authority):**
**Director docs base commit:** `cc791364e2f1ff1ac4c72e01c7122296e4ff7362`

This document is the intended canonical operations authority for LangGraph-primary
orchestration. It does not activate runtime behavior merely because it exists in a
worktree or branch. Until this candidate is merged, deployed, and its activation
evidence passes owner acceptance, legacy ownership and current production runtime
state remain unchanged.

Archived pre-primary sources (historical, non-authoritative):

- [langgraph-transition-plan-pre-primary.md](archive/langgraph-transition-plan-pre-primary.md)
- [langgraph-shadow-operations-pre-primary.md](archive/langgraph-shadow-operations-pre-primary.md)

## Current truth

Until activation evidence passes:

- The legacy Director orchestration engine remains the sole writer for provider
  dispatch, Slack/GitHub side effects, and task-state transitions on active
  deployments.
- LangGraph surfaces in the repository (shadow comparison, packet runtime,
  completion workers) are capability evidence only when their deployment switches
  and health output prove they are active — not when documentation or a merged PR
  exists.
- Gate 0 acceptance (2026-08-16, Project Uppercut Issue #48) closed a prerequisite;
  it did not start LangGraph, change the sole writer, or authorize deployment.
- Contract schemas in `docs/schemas/` and `docs/model-interoperability-contract.md`
  remain binding for packet work; this document does not replace them.

## Target topology

LangGraph becomes the primary coordinator for the full task lifecycle:

1. **Intake and deduplication** — authenticated Slack/API events with stable
   idempotency keys.
2. **Authority binding** — project/profile, allowlisted repository, immutable
   starting commit and authority-snapshot digest.
3. **Plan and decomposition** — OpenAI-directed planning; validated assignment DAG.
4. **Deterministic gates** — policy preflight, workload limits, budget reservation,
   hop/revision/timeout counters; model output may add gates, never remove them.
5. **Authenticated interrupts** — owner approval and provider callbacks bound to
   task, assignment, generation, scope, candidate digest/SHA, actor, and expiry.
6. **Dependency scheduling** — only dependency-ready assignments enter dispatch.
7. **Provider subgraphs** — v1 packet boundary adapters; provider-native payloads
   stay inside envelopes.
8. **Evidence aggregation** — normalized manifests, usage, interaction evidence.
9. **Bounded debate** — Gemini/Antigravity simulation and behavioral validation
   lanes where policy permits; not free-form routing.
10. **OpenAI synthesis and acceptance** — integrates peer evidence; cannot accept
    its own exact candidate.
11. **Revisions** — bounded revision loop with deterministic escalation.
12. **Exact-candidate review** — independent different-provider exact-head
    acceptance before merge authority.
13. **CI, security, and completion gates** — trusted CI, security review, and
    allowlisted completion workers. Exact-head evidence alone grants no merge or
    deployment authority; a deterministic effect executor may perform an explicitly
    owner-authorized scoped merge or deployment only after all current gates,
    ledger, idempotency, and writer-fence checks pass.
14. **Deterministic effects** — GitHub/Slack writes through policy effect keys.
15. **Reconciliation** — ambiguous provider or side-effect outcomes enter explicit
    reconciliation; never blind retry.
16. **Terminal reporting** — durable task record and owner-visible summary.

Failure routes are explicit: retry (when idempotent), quarantine, owner decision,
cancellation, or terminal failure. No model chat controls routing.

## Deterministic policy remains authority

`src/director_orchestrator/policy.py` and deterministic code own approval,
repository, branch, cost, timeout, hop, revision, exact-SHA, CI, security, budget,
safety, and owner gates. Model output is untrusted data.

- Models cannot widen permissions, bypass exact-SHA review, skip CI or security
  gates, reset budgets after restart, revive cancelled work, or approve their own
  implementation.
- Checkpoint rollback restores workflow state; it does not undo commits, provider
  runs, Slack messages, or GitHub writes.
- Provider identity never grants authority; all providers consume the same
  validated work contract.

## Persistence: Postgres, thread IDs, namespaces

**Production** requires a dedicated PostgreSQL LangGraph checkpointer, separate from
legacy task tables and effect ledgers.

| Requirement | Rule |
| --- | --- |
| Checkpointer | Dedicated Postgres store; not shared with unrelated services |
| Thread ID | Stable, task-derived; survives restarts and redeployments |
| Namespace | Versioned deployment/graph namespace; distinct from shadow and legacy |
| Schema migrations | Planned, tested, and reversible; recorded in activation evidence |
| Backup/restore | Mandatory before cutover; restore drill included in rollback evidence |
| Secret exclusion | No secrets, tokens, or raw credentials in checkpoints or traces |

**Local test only:** SQLite file checkpoints and in-memory checkpointers. They do
not satisfy production activation gates and must not be cited as cutover evidence.

Pre-primary shadow and packet-runtime SQLite paths remain historical local-dev
artifacts until Postgres cutover evidence replaces them.

Graph checkpoint stores and effect ledgers are **separate durable stores**. Do not
claim atomic cross-store commits unless a later implementation proves one shared
Postgres transaction covering both.

## External effects: intent ledger, idempotency, reconciliation

Every external effect (provider dispatch, Slack write, GitHub mutation, callback
mutation, merge/deployment action) requires:

1. **Durable intent** — commit durable effect intent to the ledger **before** the
   external call.
2. **Budget reservation** — dispatch and review cost reserved before paid work.
3. **Stable idempotency key** — same key replays durable outcome or stops.
4. **Ledger consult on graph replay** — when a node reruns after interrupt or
   restart, consult the effect ledger by the stable idempotency key; do not blindly
   re-execute if intent or terminal evidence already exists.
5. **Fail-closed on drift** — any crash window, checkpoint-ledger drift, or
   disagreement between graph checkpoint state and ledger rows enters fail-closed
   reconciliation and remote readback; never blind retry.
6. **Terminal evidence** — authenticated callback or remote readback bound to the
   original intent bindings.
7. **Reconciliation for ambiguity** — timeout, process loss, or conflicting callback
   moves to `RECONCILIATION_REQUIRED`; no blind resend.

Ambiguous effects never receive a second unreserved attempt. Owner or authenticated
readback must settle reconciliation before progression.

## Single-writer lease and fencing

Exactly one engine may own provider dispatch, Slack/GitHub writes, callback
mutation, merge/deployment actions, and task-state transitions for a task.

- A **durable single-writer lease** with **fencing generation** protects every
  writer edge. A stale generation fails closed.
- **Dual writer ownership is a no-go.** Legacy intake must be disabled before the
  graph writer lease is acquired for new work.
- Never run legacy and graph dispatchers against the same active task as a
  "comparison."
- `/health` must report checkpoint, writer, and reconciliation readiness before
  cutover; health never exposes secrets.

## Interrupts, approvals, and idempotent side effects

LangGraph may resume an interrupt by rerunning a node. Therefore every
pre-interrupt side effect must be idempotent under replay.

Approval and callback resumes bind:

- task ID and assignment ID;
- writer generation and scope;
- candidate digest/SHA;
- actor identity and expiry.

Stale approval, rebinding, or scope mismatch fails closed. Interrupt resume does
not transfer writer ownership to model output.

## Provider roles

| Provider | Role |
| --- | --- |
| **OpenAI / Codex** | Directs, plans, synthesizes, accepts, and integrates peer evidence |
| **Claude** | Full-spectrum implementation and review peer |
| **Cursor** | Full-spectrum implementation and review peer |
| **Grok** | Full-spectrum Cursor-family peer; adversarial and security value |
| **Gemini / Antigravity** | Isolated implementation, simulation, behavioral validation, bounded debate |
| **Perplexity** | Public sourced evidence only; no private data expansion |
| **Copilot Lite** | Optional non-gating hygiene |

**Acceptance rule:** a producer cannot independently accept its own exact candidate.
Exact-head acceptance requires a different provider than the implementation producer.

## Native Long Table canary evidence

The Native Long Table canary proves the full all-provider graph on an isolated lane.
It is evidence for orchestration graduation only. **Physical Quest validation** is a
separate gate and is not satisfied by this canary. Passing the canary does not
claim production readiness, deployment permission, or permission to merge product
`main`.

**Accepted Native Long Table source baseline (canary authority):**

| Field | Value |
| --- | --- |
| Commit | `4d26680fb99abacb20ecb18807b7c8833646cfa3` |
| Tree | `1e0db56f1e85d66c93ab64bc3dca8c237947c210` |
| Source worktree | `C:\Users\edwar\.codex\worktrees\long-table-claude-provenance-004` |

**Canary target:**

| Field | Value |
| --- | --- |
| Worktree | `C:\Users\edwar\.codex\worktrees\long-table-native-langgraph-canary` |
| Branch | `codex/native-langgraph-canary` |

**Prohibited canary authority:** do not use stale native-first-deal commit
`fb729579` or its branch as canary authority. Those identifiers are historical only.

Procedure:

1. Bind the accepted source baseline commit and tree above as the immutable authority
   for Native Long Table canary intake.
2. Execute in the isolated canary worktree and branch listed above.
3. Run the **full all-provider graph** with deterministic IDs through Postgres
   checkpointing and the production namespace.
4. **Freeze exact head** — record commit SHA and tree digest at canary completion.
5. Require **targeted and full tests**, Unreal build/automation evidence where
   applicable, trusted CI, security review, **different-provider exact-head
   acceptance**, restart/Docker/network recovery drills, **zero duplicate**
   provider/Slack/GitHub effects, and rollback evidence with preserved ledgers.
6. **Merge scope:** the canary may merge only an **isolated test-only candidate**
   when separately owner-authorized. It may **never** merge product `main` or
   deploy merely because the canary passes.

## Activation

LangGraph becomes primary only after **all** gates pass and owner accepts evidence:

1. Independent review and trusted CI/security on the exact candidate SHA.
2. Postgres checkpointer live with backup/restore drill recorded.
3. Distinct namespaces for deployment, graph, checkpoint, and effect ledger stores.
4. **Legacy intake disabled** before acquiring the graph writer lease for new tasks.
5. Pre-cutover tasks **bound or explicitly migrated**; no orphaned dual ownership.
6. `/health` shows checkpoint, writer, and reconciliation readiness.
7. Kill switch blocks new graph tasks and reconciles in-flight effects.
8. Long Table canary evidence bundle complete (see above).
9. Owner/operator acceptance recorded with exact commits, profile IDs, store digests,
   provider run IDs, costs, and rollback drill results.

Merged code or documentation alone satisfies none of these rows.

## Rollback

Rollback preserves evidence and never deletes ledgers to enable retry.

1. Stop new graph intake; engage parallel kill switch if active.
2. Allow in-flight effects to complete or move through authenticated reconciliation.
3. Do not hand unresolved packet assignments to the legacy engine.
4. Disable graph writer and live dispatch; confirm legacy sole-writer for new tasks.
5. Export checkpoint, task, and effect state; switch deployment flag to last accepted
   engine; restart isolated profile.
6. Reconcile provider runs and Slack/GitHub writes by idempotency key.
7. Resume only after owner review; preserve checkpoint and ledger databases as evidence.

Destructive rollback (ledger deletion, checkpoint wipe, secret-bearing export) is
prohibited.

## Explicit no-go conditions

Do not activate or widen the canary when any condition holds:

| No-go | Reason |
| --- | --- |
| Dual writers | Race on dispatch, Slack, GitHub, or task state |
| Missing durable Postgres checkpointing | Cannot survive restart or prove recovery |
| Unreserved external effects | Duplicate charge or write risk |
| Ambiguous paid retries without readback | Blind resend violates reconciliation policy |
| Stale evidence | Acceptance bound to wrong SHA or digest |
| Approval rebinding | Stale or widened approval scope |
| Secret-bearing checkpoints | Credential leakage and rollback contamination |
| Budget reset after restart | Cost escape |
| Revived cancelled work | Policy violation |
| Destructive rollback | Evidence loss and unsafe retry |
| Stale native-first-deal authority (`fb729579`) | Wrong Long Table baseline |

## Relationship to runtime switches

Pre-primary documents described SQLite-backed packet runtime switches
(`LANGGRAPH_RUNTIME_*`, shadow flags). Those paths are **historical local-dev
evidence**. Primary activation targets Postgres checkpointing, graph-coordinated
lifecycle, and the writer lease described here.

Until activation evidence passes, treat any `LANGGRAPH_*` switch in a deployment as
operator-local unless health and ledger inspection prove otherwise. Do not present
contract-only or default-off features as active production behavior.

## Work ownership

- **Owner / Director:** authority, exceptions, cutover, rollback, final acceptance.
- **Control-plane implementation:** graph, Postgres persistence, state mapping,
  compatibility, recovery.
- **Provider adapters:** translate neutral packets; do not own workflow state.
- **Independent review:** behavior, security, evidence, provider parity.
- **Project consumers:** validate orchestration cannot alter product canon or
  evidence meaning.

No model approves its own migration PR or accepts its own exact candidate.
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:50 UTC | END]] -->
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:55 UTC | END]] -->
