<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:31 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:06 UTC | BEGIN]] -->
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

The primary effect executor adds an append-only execution marker between intent
reservation and the mutating call. Exactly one current writer can create that
marker. A restart that sees the marker but no remote binding records ambiguity
and never calls the mutation again; a restart with a binding performs bounded
readback only. The mutating and readback transports are explicit injected
dependencies with fixed timeouts. Live startup fails closed when either is
missing, and `/health.langgraph_primary.effect_executor_ready` must be true for
`primary_runtime_active` to become true.

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

## Windows Docker activation tooling

The following tools are operational mechanisms, not activation evidence by
their mere presence:

- `scripts/windows-primary-snapshot.ps1` creates a new snapshot only when its
  target does not exist, or verifies an existing clean snapshot against an exact
  repository, starting ref, commit, and tree. It never resets or replaces an
  existing checkout.
- `scripts/windows-host-provider-bridge.ps1 -Action Preflight` verifies the
  allowlisted Windows command map without invoking a provider. `-Action Run`
  starts the authenticated host bridge in the foreground for supervision.
- `scripts/windows-langgraph-primary-control.ps1` provides separate `Preflight`,
  `Accept`, `Migrate`, `Backup`, `RestoreDrill`, `Health`, `Activate`, and `Rollback`
  operations. Every invocation requires the exact environment-file path,
  Compose project, and host port; profile identity is never inferred.

The Windows host bridge is an optional narrow transport for supported native
CLIs. It uses a secret distinct from API, Slack, callback, reconciliation, and
writer secrets. Requests bind provider, task, assignment, canonical packet
digest, hashed idempotency key, and current writer generation. The bridge
commits a durable claim before one `shell=False` subprocess attempt. Successful
outcomes replay from the durable result; timeouts, process loss, nonzero exits,
or malformed output become ambiguous and are never resent.

The container client accepts only an exact `http` URL at
`/v1/provider/dispatch`, with an explicit port and the host
`host.docker.internal` or an exact loopback address. User information, query
strings, fragments, alternate paths, remote hosts, TLS-looking aliases, and
redirects are rejected. Immediately before any child-process I/O, the Windows
bridge reads the authoritative Postgres task-writer row and requires the
LangGraph owner, signed owner-token digest, exact fencing generation, and a live
lease. A missing, stale, inaccessible, or mismatched row fails closed. The child
receives only a minimal Windows process environment plus the selected lane's
explicit authentication variables; Director API, bridge, callback,
reconciliation, and shared secrets are never inherited. Each profile must set
`DIRECTOR_HOST_PROVIDER_POSTGRES_DSN` to its own reachable primary database;
that host-side DSN is a secret-store value and must never be copied into logs or
checked-in profile files.

The bridge currently permits only explicitly configured Claude, Grok,
Antigravity, and GitHub Copilot packet-wrapper commands. Cursor and Microsoft 365
Copilot remain direct and are never aliased through the Windows bridge.

The exact roster identity is `microsoft-365-copilot`, not Windows Copilot. It uses
the production Work IQ REST endpoint
`https://workiq.svc.cloud.microsoft/rest/conversations/{conversationId}/chat`.
The production contract requires a delegated work or school Entra identity with
admin-consented `WorkIQAgent.Ask`; personal and application permissions are
unsupported. The organization administrator must enable Work IQ. The lane is
limited to one synchronous, text-only research/review turn with web grounding
disabled and no external-effect authority.

The lane reports
`microsoft_365_copilot_work_iq_capability_evidence_missing` until Director
cryptographically verifies the delegated JWT against the configured tenant's
Microsoft OIDC discovery document and cached JWKS. The token must have the exact
tenant, issuer, Work IQ audience and `WorkIQAgent.Ask` scope, and must be within
its signed `nbf`/`exp` window. Director then creates a prompt-free production
Work IQ conversation. Only the successful authenticated response, bound to its
Microsoft request ID, body digest, conversation ID, signed token subject, and a
five-minute-or-shorter expiry, proves tenant enablement and the current Copilot
license/billing entitlement. Environment booleans, self-computed digests,
configuration, or a consumer-app installation are not evidence. The preflight
sends no model prompt and beta endpoint paths are never accepted.

Official contract references:

- <https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/rest/copilotroot-post-conversations>
- <https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/permissions>
- <https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/copilot-apis-security-authentication>

Endpoint and secret configuration alone never makes a host-bridge lane callable.
Before composing adapters, the container sends a fresh HMAC-authenticated
challenge and verifies the signed, digest-bound configured-provider command map.
Missing, stale, redirected, malformed, unsigned, or mismatched challenge evidence
leaves every affected bridge lane unavailable. This liveness challenge performs
no provider subprocess or paid action.

Independent exact-head review, trusted CI, and security results are stored as one
immutable completion-evidence bundle in the primary Postgres control plane. The
bundle is accepted only under the current unexpired LangGraph writer fence and is
bound to the task, candidate SHA, provider-result digest, gate-ledger digest, and
individual result digests. Restart readback revalidates canonical content;
missing or conflicting evidence remains `OWNER_DECISION_REQUIRED` and can never
be replaced with a synthetic pass.

For every profile, execute the control actions in this order while Slack intake
is closed: `Preflight`, `Accept`, `Migrate`, `Backup`, `RestoreDrill`, provider
activation evidence, then `Activate`. `Accept` reads the canonical GitHub pull
request, requires its head to equal the frozen candidate, requires every named
CI check to be completed successfully with no skipped/pending/failed check, and
requires distinct non-author security and independent-review ACCEPT artifacts
that both name the exact head. Those facts and their digests are recorded under
the same activation identity; operator-set booleans are not acceptance evidence.
Activation reopens Slack only after the observed health
projection proves Postgres, writer fencing, reconciliation, current provider
activation, graph ownership, and zero ambiguous effects. Rollback stops Slack and
the orchestrator, waits for the writer lease to expire, refuses when ambiguous
effects exist, performs the fenced generation change back to legacy, and preserves
the primary database.

The control tool persists `/data/langgraph-primary-activation-evidence.json` as
an atomic versioned state machine. Its identity binds the exact Director
candidate commit/tree, profile, Compose project, opaque Postgres storage
identity, schema, and checkpoint namespace. The only accepted sequence is
`PREFLIGHT -> ACCEPTED -> MIGRATED -> BACKED_UP -> RESTORE_PROVED -> ACTIVATED`; missing,
duplicate, out-of-order, corrupt, cross-candidate, cross-profile, or cross-store
evidence fails closed. Restore proof requires all named primary control-plane
tables, all four LangGraph checkpoint tables, and a nonzero control-plane schema
row from the restored disposable database. A successful command exit or a zero
table count is not restore evidence.

`RestoreDrill` copies the selected dump to its random container staging path,
hashes those exact staged bytes, and compares the lowercase `sha256:` digest to
the prior durable `BACKED_UP` record under the same activation identity before
database creation or `pg_restore`. Missing files, mutated dumps, and substituted
but otherwise valid dumps fail closed. `RESTORE_PROVED` records the same bound
backup digest, so a later activation cannot splice restore evidence from another
artifact.

Before rollback changes the intake generation, the Postgres quiescence query
must prove zero unresolved effect/provider reservations, zero pending/active/
ambiguous parallel assignments, zero live task-writer leases, zero unresolved
reconciliation claims, zero unsettled claims, and zero ambiguous effects. The
legacy engine never receives tasks merely because the intake lease expired.

Preflight first bootstraps only the profile's PostgreSQL service and persists a
database-instance UUID inside the control-plane singleton. Activation evidence
binds both the canonical connection target and that instance UUID, so replacing
a volume or server behind the same DSN invalidates the earlier evidence chain.

Immediately before cutover, the control tool stops and verifies all authenticated
intake surfaces: Slack, n8n, and the API orchestrator. It then hashes the complete
legacy SQLite task ID/status inventory and durably binds that immutable snapshot
to legacy ownership before `require-ready` may pass. Pre-cutover tasks remain
legacy-owned; only the fenced new-intake generation transfers to LangGraph.

If any post-start activation health check fails, the tool stops the orchestrator,
restores paid/live/new-intake switches to their closed values, and leaves Slack,
n8n, and API intake stopped. Rollback uses unconditional runtime exceptions for
ambiguous-effect and quiescence failures; Python optimization cannot remove the
safety checks.

The checked-in local Docker Compose topology uses `sslmode=disable` only for the
exact service hostname `langgraph-primary-db` and only under the explicit
`LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES=true` exception injected by
Compose. Loopback remains allowed for local tests. Lookalike names, arbitrary
container names, private-network IPs, mixed remote `hostaddr` values, and every
other non-loopback target fail closed. Managed or remote production PostgreSQL
must use `verify-ca` or `verify-full` with an explicit CA root; the local Compose
exception is not production TLS evidence.

## Default parallel assignment lifecycle

For live Default intake, the deterministic primary planner expands the normalized
plan into exactly one independent first-wave assignment for every configured
required provider lane. The first wave has no dependency edges. One bounded
review/synthesis assignment depends on the complete first wave, so it cannot run
until every provider result has settled durably. The exact provider roster is also
the concurrency bound: concurrency above three is valid only when it is backed by
that unique roster and every lane has exactly one active slot.

The Default primary application registers this complete assignment DAG in the
Postgres parallel ledger before an execution approval can cross the provider
boundary. After approval, the historical single-assignment graph edge is an
interrupt only: it never owns provider transport. The parallel ledger is the sole
dispatch owner and commits each assignment reservation before invoking its exact
packet adapter.

Each wave concurrently consumes every dependency-ready assignment allowed by
the configured task, provider, deployment, and write-surface caps. A dependent
packet is built only after all named upstream result packets are durably accepted.
Provider exceptions are ambiguous and are never resent. Restart recovery may
consume still-pending rows but does not redispatch a reserved or settled row.
For a definitive outcome, each assignment invokes and records exactly three
attempts. Every configured format/transport pair is used once before deterministic
reuse. An ambiguous paid dispatch consumes its current attempt and stops the
assignment immediately for reconciliation; the exact-three rule never authorizes
a blind resend of an unknown external outcome.
The graph task is successful only when the deterministic aggregate proves every
assignment succeeded; a pending, active, ambiguous, failed, or dependency-blocked
member cannot be reported as successful completion.

## Authenticated recovery and completion ingress

The periodic recovery worker heartbeats only task-writer leases touched by its
current process. A live database-clock lease proves normal `DISPATCHING` or
`EXECUTING` work is still owned and is never recovered by the sweep. Only a
nonterminal task with an absent or expired lease may be taken over through the
next fencing generation and reconciled; terminal tasks are ignored.

Cancel and recover commands require a fresh Slack-channel HMAC proof bound to
the exact route, task, command body, projection digest, writer generation,
actor, timestamp, and nonce. The nonce and command identity are claimed in an
append-only Postgres row under that live writer fence before state changes. An
exact redelivery is idempotent; nonce or command rebinding is rejected.

Completion evidence enters through the signed adapter-callback route only when
the transport actor equals the evidence actor. The route binds the exact task,
candidate SHA, provider-result digest, projection, and writer generation, then
uses the immutable writer-fenced completion store. Evidence without a valid
signature, exact gate/result digests, current writer lease, or exact replay
identity cannot authorize successful completion.
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:06 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:31 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | END]] -->
