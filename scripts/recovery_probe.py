# [[Codex | GPT-5.6 | 2026-08-24 | 11:07 UTC | BEGIN]]
from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import (
    InteractionProvider,
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    start_interaction,
    transition_interaction,
)
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore


TASK_ID = "DIR-RECOVERY-PROBE"


def seed_verified_interaction(
    record: TaskRecord,
    *,
    provider: ProviderName,
    phase: ProviderInteractionPhase,
    result: str,
    interaction_id: str,
    assignment_sequence: int | None = None,
) -> None:
    """Install backend-owned VERIFIED evidence for a pre-seeded probe result."""

    transports = {
        ProviderName.OPENAI: ProviderTransport.OPENAI_RESPONSES_API,
        ProviderName.CURSOR: ProviderTransport.CURSOR_AGENTS_API,
    }
    effect_key = hashlib.sha256(interaction_id.encode("utf-8")).hexdigest()
    attempted_evidence = build_transport_evidence(
        transport=transports[provider],
        source=TransportEvidenceSource.BACKEND_OBSERVATION,
        execution_mode="simulation",
        idempotency_key=effect_key,
    )
    event = start_interaction(
        task_id=record.task_id,
        interaction_id=interaction_id,
        plan_generation=record.plan_generation,
        assignment_sequence=assignment_sequence,
        provider=InteractionProvider(provider.value),
        phase=phase,
        attempt=1,
        transport_evidence=attempted_evidence,
        effect_key=effect_key,
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, event
    )
    event = transition_interaction(
        record.provider_interaction_ledger,
        interaction_id=interaction_id,
        next_state=ProviderInteractionState.ATTEMPTED,
        transport_evidence=attempted_evidence,
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, event
    )
    verified_evidence = build_transport_evidence(
        transport=transports[provider],
        source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
        execution_mode="simulation",
        idempotency_key=interaction_id,
        result=result,
        native_status="completed",
    )
    for state in (
        ProviderInteractionState.DISPATCHED,
        ProviderInteractionState.ACKNOWLEDGED,
        ProviderInteractionState.RESPONDED,
        ProviderInteractionState.COMPLETED,
        ProviderInteractionState.VERIFIED,
    ):
        event = transition_interaction(
            record.provider_interaction_ledger,
            interaction_id=interaction_id,
            next_state=state,
            transport_evidence=verified_evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )


class AcceptingDirector:
    async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
        return ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary="Persisted recovery evidence accepted.",
            evidence=[result],
        )


class NoRedispatchAdapter:
    def __init__(self) -> None:
        self.dispatch_count = 0

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        self.dispatch_count += 1
        raise AssertionError("A persisted provider run must not be redispatched")

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise AssertionError("A persisted terminal provider run must not be polled")


def probe_settings(database: Path) -> Settings:
    return replace(
        Settings(),
        allow_paid_runs=True,
        openai_api_key="recovery-probe-openai",
        cursor_api_key="recovery-probe-cursor",
        project_allowlist=("recovery-probe",),
        repository_allowlist=("example/recovery-probe",),
        allowed_starting_refs=("main",),
        max_agent_hops=1,
        provider_poll_seconds=0,
        provider_timeout_seconds=1,
        sqlite_path=str(database),
    )


def seed(database: Path) -> None:
    store = TaskStore(str(database))
    record = TaskRecord(
        task_id=TASK_ID,
        status=TaskStatus.RUNNING,
        intake=SlackIntake(
            source_event_id="evt-recovery-probe",
            channel_id="C-PROBE",
            thread_ts="1.0",
            user_id="U-PROBE",
            text="DIRECTOR Project: recovery-probe Outcome: verify restart recovery",
        ),
        plan=DirectorPlan(
            project="recovery-probe",
            normalized_outcome="verify restart recovery",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=1,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CURSOR,
                    task="Recover the recorded result",
                    acceptance=["Persisted evidence is accepted without redispatch"],
                    repository="example/recovery-probe",
                )
            ],
            final_acceptance=["Task completes after restart"],
        ),
        provider_runs=[
            ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=1,
                run_id="run-recovery-probe",
                session_id="session-recovery-probe",
                status="FINISHED",
                result="Persisted provider evidence",
            )
        ],
    )
    if record.plan is None:
        raise AssertionError("Probe plan was not installed")
    plan_interaction_id = "recovery-probe:plan"
    seed_verified_interaction(
        record,
        provider=ProviderName.OPENAI,
        phase=ProviderInteractionPhase.PLAN,
        result=record.plan.model_dump_json(),
        interaction_id=plan_interaction_id,
    )
    record.plan_interaction_id = plan_interaction_id

    run = record.provider_runs[0]
    if not run.result:
        raise AssertionError("Probe provider result was not installed")
    run_interaction_id = "recovery-probe:cursor:run-recovery-probe"
    seed_verified_interaction(
        record,
        provider=run.provider,
        phase=ProviderInteractionPhase.DISPATCH,
        assignment_sequence=run.assignment_sequence,
        result=run.result,
        interaction_id=run_interaction_id,
    )
    run.interaction_id = run_interaction_id

    _, created = store.create(record)
    if not created:
        raise AssertionError("Probe database was not empty")


async def resume(database: Path) -> None:
    store = TaskStore(str(database))
    adapter = NoRedispatchAdapter()
    engine = OrchestrationEngine(probe_settings(database), store)
    engine.providers[ProviderName.CURSOR] = adapter
    engine.director = AcceptingDirector()

    await engine.run(TASK_ID)

    completed = store.get(TASK_ID)
    if completed is None:
        raise AssertionError("Persisted task disappeared during recovery")
    if completed.status != TaskStatus.DONE:
        raise AssertionError(f"Recovered task ended in {completed.status}, expected DONE")
    if completed.next_assignment_index != 1:
        raise AssertionError("Recovered task did not advance exactly once")
    if len(completed.provider_runs) != 1:
        raise AssertionError("Recovery created an unexpected provider run")
    if len(completed.assignment_outcomes) != 1:
        raise AssertionError("Recovery did not persist exactly one accepted outcome")
    if adapter.dispatch_count != 0:
        raise AssertionError("Recovery redispatched a recorded provider run")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic cross-process recovery probe")
    parser.add_argument("phase", choices=("seed", "resume"))
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()

    if args.phase == "seed":
        seed(args.database)
    else:
        asyncio.run(resume(args.database))


if __name__ == "__main__":
    main()
# [[Codex | GPT-5.6 | 2026-08-24 | 11:07 UTC | END]]
