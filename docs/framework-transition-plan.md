# Project Uppercut Agent-Framework Transition Plan

## Decision

Use **LangGraph as the future orchestration kernel**, not as a wholesale product rewrite.
Retain the existing FastAPI and Slack boundaries, Pydantic models, deterministic policy
engine, SQLite task evidence, and provider adapters. OpenAI, Claude, and Cursor remain
independent providers; LangGraph owns state transitions, interruption, checkpointing,
and recovery, but it may not weaken an approval or repository boundary.

PydanticAI may be evaluated inside individual nodes where typed model calls reduce
boilerplate. CrewAI and conversational AutoGen group chats are not the Project Uppercut
control plane: their abstractions make policy traces and bounded recovery harder to audit.
Provider SDKs remain behind adapters rather than becoming the top-level orchestrator.

## Gate 0 — finish the original Director

No transition code may dispatch paid work until all of these are evidenced:

- OpenAI, Claude, and Cursor each pass a fresh bounded availability check.
- One owner-authored Slack analysis task completes OpenAI plan → Claude evidence →
  Cursor independent review → OpenAI acceptance.
- One isolated coding task creates a feature branch and draft PR only; no merge or deploy.
- Claude callbacks prove exact-run matching, replay idempotency, and conflict rejection.
- Assignment-scoped approvals prove that one approval cannot authorize a later assignment.
- Restart recovery resumes persisted work without duplicate provider dispatch.
- At least four scheduled recovery probes across a working day pass.
- SQLite and n8n backup/restore are exercised once on disposable copies.
- Operator evidence records provider run IDs, task state transitions, approvals, cost/token
  metadata when available, and terminal acceptance.

Gate 0 completion is a release criterion, not a statement that every future operational
feature is already implemented. Pause/cancel/resume, rate-limit backoff, hard cost
enforcement, metrics, and cloud hosting remain subsequent production-hardening work.

## Stable contracts before migration

Freeze and test these boundaries before adding LangGraph:

1. `SlackIntake`, `TaskRecord`, `Assignment`, `ProviderRun`, and `ReviewDecision` schemas.
2. `policy.py` as the final deterministic authorization boundary.
3. Provider adapter dispatch/poll/revise/usage-check semantics.
4. Persisted assignment outcomes and dependency evidence envelopes.
5. Owner decision, callback, and task-query HTTP contracts.

Every model must read `AGENTS.md`, this plan, and the linked work order before changing
transition code. Model output is evidence, never authority to alter these contracts.

## Migration phases

### Phase 1 — legacy trace fixtures

Capture sanitized state-transition traces from dry-run, approval, callback, failure,
restart, and completed-task scenarios. Remove prompts, secrets, and private payloads.
Turn the traces into deterministic replay fixtures.

### Phase 2 — shadow graph

Add `ORCHESTRATION_BACKEND=legacy|langgraph-shadow|langgraph`, defaulting to `legacy`.
The shadow graph consumes captured events and writes to a separate checkpoint database.
It must not call providers, post Slack messages, mutate the legacy task database, or
perform GitHub writes.

Required graph nodes:

- intake normalization
- plan request
- deterministic policy enforcement
- owner interrupt
- specialist dispatch intent
- poll/callback wait
- Director review
- bounded revision
- terminal report

### Phase 3 — parity evaluation

Run both engines over the same fixtures. A trace passes only when terminal status,
assignment order, approval stops, dependency behavior, provider-dispatch intent, and
recovery outcome agree. Timestamps and generated IDs may be normalized.

Required corpus: at least 20 fixtures, including every fail-closed and replay case.
Any graph-only dispatch intent, missed approval, or duplicate run is a blocking defect.

### Phase 4 — Project Uppercut pilot

Use the isolated `.env.uppercut` deployment. Start with analysis-only work, then one
reversible draft-PR task. Unreal remains Windows-authoritative; Linux/cloud agents may
run Python validation but cannot claim Unreal compilation or runtime proof.

### Phase 5 — controlled cutover

Enable `langgraph` for one task class at a time. Keep legacy state readable and keep a
one-command rollback to `legacy` for at least two release cycles. Do not dual-dispatch.

## Team responsibilities

| Role | Responsibility |
|---|---|
| OpenAI | Director planning, structured decomposition, final acceptance, migration parity adjudication |
| Claude | Primary coding workhorse, repository-scale implementation, test and documentation execution |
| Cursor | Independent repository review, adversarial validation, isolated secondary implementation |
| Owner | Approvals, ambiguity resolution, merges, deployments, access expansion, and authenticity judgments |

## Transition definition of done

- Gate 0 is evidenced and linked.
- The shadow graph passes the complete parity corpus with zero policy divergence.
- Crash/restart recovery and human interrupts pass on Windows and Linux CI.
- The Project Uppercut pilot produces one accepted analysis result and one reviewable
  draft PR without unauthorized actions.
- Usage and task evidence identify the provider, model/runtime where available, run ID,
  assignment, dependency inputs, review decision, and owner approval scope.
- Rollback to the legacy engine is tested, documented, and does not lose task evidence.
