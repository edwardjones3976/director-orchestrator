<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:50 UTC | BEGIN]] -->
# Archived: LangGraph shadow, packet-runtime, and writer-cutover operations (pre-primary)

**HISTORICAL / NON-AUTHORITATIVE**

This file preserves the pre-primary shadow and packet-runtime operations document
exactly as it stood before LangGraph-primary operations authority was introduced.
Its default-off activation tables, SQLite ledger paths, and cutover graduation
steps describe historical capability evidence and operator procedure at that time —
not current operating instructions.

For intended operations authority after merge and activation, see
[langgraph-primary-operations.md](../langgraph-primary-operations.md). The tombstone
at [langgraph-shadow-operations.md](../langgraph-shadow-operations.md) links here.

---

<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:39 UTC | BEGIN]] -->
**[[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]**

# LangGraph shadow, packet-runtime, and writer-cutover operations

## Current status: implemented capability, default-off activation

The repository now contains two separate LangGraph surfaces. Do not use the
older shadow flags as evidence that the production packet runtime is active.

1. `LANGGRAPH_SHADOW_ENABLED` and `LANGGRAPH_APPROVAL_RESUME_ENABLED` retain the
   isolated comparison and approval-resume proof lanes. They use the shadow
   checkpoint database and never own live provider dispatch.
2. `LANGGRAPH_RUNTIME_ENABLED` constructs the production-capable provider-neutral
   packet coordinator with its own checkpoint and dispatch ledger. It still
   contacts no provider until the live-dispatch and task-writer cutover gates are
   also satisfied.

The packet runtime, exact task-writer token, v1 work/result packet adapters,
validated result-to-task progression, authenticated late-result reconciliation,
and dynamic bounded parallel-dispatch ledger are implemented. They remain
operator-gated and default off. A merged implementation is capability evidence;
only the active deployment's health, exact profile, and runtime ledgers establish
that a cutover actually occurred.

## Implemented production path

When the complete cutover configuration is valid, `main.py` constructs one
`DirectorPacketTaskRouter` and one `DirectorPacketResultConsumer` around the
`LangGraphPacketRuntime`:

- the router resolves exactly one allowlisted repository from a clean local Git
  snapshot, binds an immutable 40-character commit and authority-snapshot digest,
  builds a validated `model-work-packet-v1`, and selects the provider boundary;
- every live provider call uses `PacketBoundaryAdapter`; provider-native payloads
  do not become coordinator state;
- the runtime atomically reserves the packet idempotency key, validates task,
  assignment, provider, authority, acceptance, side-effect, usage, and writer
  bindings, and stores the validated `model-result-packet-v1` before reporting
  completion;
- the result consumer verifies the replay envelope and packet digest, binds or
  creates the provider run, records usage and authenticated interaction evidence,
  and returns control to the existing Director acceptance/revision/review path.

Runtime `COMPLETED` therefore means a validated provider result was durably
recorded and progressed into Director task evidence. It does not mean independent
exact-SHA acceptance, Codex Security, CI, merge, deployment, publication, or
project completion occurred.

## Exact activation gates

Live packet dispatch is active only when every row below is satisfied:

| Gate | Required state |
| --- | --- |
| `LANGGRAPH_RUNTIME_ENABLED` | `true` |
| `LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED` | `true` |
| `LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED` | `true` |
| `ALLOW_PAID_RUNS` | `true` |
| Runtime checkpoint and ledger | Non-empty, file-backed, and distinct from the task, shadow, and resume-claim stores |
| Runtime namespace | Non-empty and different from the shadow namespace |
| `LANGGRAPH_RUNTIME_WRITER_OWNER_ID` | Non-empty stable writer identity |
| `LANGGRAPH_RUNTIME_WRITER_SECRET` | At least 32 characters and never reported by health |
| `REPOSITORY_SNAPSHOT_ROOT` | Configured clean local snapshot for the one repository allowlist entry |
| Project/repository/ref allowlists | Fully configured |
| Packet router and result consumer | Constructed by startup |

An enabled-but-invalid combination is a startup error. In particular, live
dispatch without task-writer enablement, a writer secret, a repository snapshot,
or a packet router does not silently fall back to a provider call.

## No-dual-writer rule

Exactly one engine may own provider dispatch and task-state progression for an
assignment.

- Before cutover, the legacy engine owns the lane and the packet runtime cannot
  dispatch.
- After all live gates are active, the coordinator binds one authenticated writer
  token to the task. The legacy engine routes eligible assignments through that
  coordinator and does not independently call the same provider.
- Runtime and parallel ledgers reserve the same exact effect identity before a
  call. Redelivery replays a durable result or stops for reconciliation; it never
  creates a second dispatch.
- A writer-token, packet, provider, base/head, authority-snapshot, or idempotency
  mismatch fails closed. No model output can transfer writer ownership.
- Slack/GitHub actions remain subject to their existing deterministic effect and
  approval gates. Checkpoint rollback is never represented as reversal of an
  external effect.

Never run legacy and packet dispatchers against the same active task as a
"comparison." Shadow comparison uses fixtures or copied evidence and performs no
live side effect.

## Late results and restart recovery

A timeout, provider exception, or process loss after durable reservation is
ambiguous, not failed. The runtime records `RECONCILIATION_REQUIRED` and refuses
to resend the packet.

- `open_reconciliation` moves only a legacy `CLAIMED` row into reconciliation.
- `recover_stranded_dispatch` moves only a hard-killed `DISPATCHING` row after an
  explicit attestation that the dispatching process stopped.
- `reconcile_result` accepts only separately authenticated provider readback bound
  to the original work packet, provider, writer token, authority snapshot, and
  idempotency key.
- A late callback that already produced a canonical provider run may be bound to
  the packet result only when its identity and result agree; conflicting packet
  digests or callback evidence are rejected.
- `COMPLETED` results replay without another paid call. `REJECTED` is terminal.

For a parallel batch, restart recovery finds the latest batch containing the
exact effect keys. Reserved members may be claimed once, dispatching members are
replayed through the runtime ledger rather than blindly resent, and successful
members have their already-durable results consumed. Ambiguous members require
explicit reconciliation.

## Dynamic bounded parallel dispatch

Parallel external dispatch has its own switch and kill switch. It becomes active
only when the packet task writer is active and these fixed bounds validate:

- initial global cap: 2;
- scale cap: 3 after 10 consecutive clean batches;
- any failure, partial result, reconciliation requirement, or success only after
  reconciliation resets the clean streak and applies backpressure cap 1;
- three clean batches are required to leave backpressure;
- provider-specific caps remain positive and are enforced when claiming a member;
- dependency-incomplete, cooldown, overlapping write-surface, provider-cap, and
  budget-conflicting candidates remain out of the batch;
- the conservative batch reservation includes dispatch and independent-review
  cost, while the deployment-wide daily budget remains authoritative.

The engine currently parallelizes only eligible dependency-independent packet
assignments. Owner assignments and Claude's callback-owned lane remain sequential.
Results are consumed serially into canonical task state. Engaging
`LANGGRAPH_PARALLEL_KILL_SWITCH` cancels only unstarted reserved members; in-flight
members remain visible and must complete or reconcile.

## Completion worker boundary

Completion Gate evidence and execution are separate from provider dispatch.
`COMPLETION_GATES_ENABLED` records evidence;
`COMPLETION_GATE_ISOLATED_PROOF_ENABLED` permits the isolated-proof contract; and
`COMPLETION_GATE_WORKERS_ENABLED` constructs the selected audited-host and/or
disposable-container worker tiers.

The worker executor and append-only worker store are implemented and exposed to
the internal runtime. There is no unrestricted shell, public command endpoint,
automatic project-completion authority, or automatic merge/deployment authority.
The host tier remains fail-closed because the Director cannot yet independently
verify the broker's low-privilege identity, network policy, resource limits, and
read-only repository handle. The container tier requires an exact image digest, no network, non-root
identity, read-only root and repository, no capabilities, no-new-privileges, and
bounded resources. Do not mount a host Docker socket into the Director to make
this appear ready.

## Evidence-based graduation procedure

Use one isolated deployment profile and one harmless canary repository.

1. Merge an independently reviewed exact SHA after trusted CI and security review.
2. Keep live dispatch, parallel dispatch, and workers off. Start the profile and
   prove all task, shadow, runtime, ledger, Completion Gate, and worker database
   paths and namespaces are distinct.
3. Run offline packet replay and shadow comparison. Prove identical packet/result
   validation, restart recovery, and zero external calls.
4. Enable the packet runtime only. Confirm `/health` reports the coordinator
   constructed while live dispatch and task-writer activity remain false.
5. Configure the writer secret and clean repository snapshot, then enable live
   dispatch and task-writer cutover for one bounded canary task. Confirm exactly
   one provider dispatch and normal Director acceptance progression.
6. Restart during a second active canary. Prove durable replay or explicit
   reconciliation and no duplicate provider charge or callback consumption.
7. Exercise a deliberately delayed result through authenticated readback. Prove
   the exact reservation completes without resending.
8. Enable parallel dispatch with its kill switch initially engaged. Open it for
   two independent assignments, observe the 2 -> 3 graduation only after ten
   clean batches, inject one controlled failure to prove cap 1, and recover only
   after three clean batches.
9. Activate Completion Gate workers only from a dedicated low-privilege service
   environment with both executable fingerprints and the pinned image verified.
   Prove fixed argv, timeout/output/process-tree termination, repository
   immutability, restart replay, and append-only evidence.
10. Record exact commits, profile identifiers, store paths by digest or safe name,
    provider run IDs, costs, reconciliation evidence, health output, and rollback
    results. Obtain the separately required owner/operator acceptance before
    widening the canary.

## Rollback without duplicate effects

1. Stop new intake for the affected profile.
2. Engage `LANGGRAPH_PARALLEL_KILL_SWITCH=true`. This cancels reserved work only;
   record every dispatching or ambiguous member.
3. Allow known in-flight calls to finish or move them through the authenticated
   reconciliation path. Do not hand an unresolved packet assignment to the legacy
   engine.
4. Set `LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED=false`,
   `LANGGRAPH_RUNTIME_LIVE_DISPATCH_ENABLED=false`, and
   `LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED=false`; restart the profile.
5. Confirm health reports packet live dispatch/task writer/parallel activity false
   and the legacy sole-writer indication true for new tasks.
6. Resume only new or proven-undispatched legacy work. Keep every completed,
   rejected, dispatching, and reconciliation row; never delete a ledger to make a
   retry possible.
7. After all packet-owned tasks are terminal, optionally set
   `LANGGRAPH_RUNTIME_ENABLED=false`. Preserve checkpoint and ledger databases as
   evidence. Disabling runtime or deleting a checkpoint does not reverse an
   external effect.
8. Disable Completion Gate workers independently with
   `COMPLETION_GATE_WORKERS_ENABLED=false`; preserve their append-only store.

The shadow proof flags may remain enabled because they own no live side effects,
or they may be disabled separately for a complete LangGraph rollback.

**[[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]**
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:39 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | END]] -->

---

**End of archived pre-primary shadow operations document.**
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:50 UTC | END]] -->
