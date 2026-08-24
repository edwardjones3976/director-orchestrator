# [[Codex | GPT-5.6 | 2026-08-24 | 16:53 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 11:58 UTC | BEGIN]]
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import (
    CommunicationEvidenceFailure,
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
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore
from tests.plan_binding_helpers import seed_production_plan_materialization


class NoProviderCallAdapter:
    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        raise AssertionError("a persisted terminal run must not be redispatched")

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise AssertionError("a persisted terminal run must not be polled")


def _seed_verified_run_identity(record: TaskRecord, run: ProviderRun) -> None:
    interaction_id = f"test:{record.task_id}:provider-run"
    drafted_evidence = build_transport_evidence(
        transport=ProviderTransport.CURSOR_AGENTS_API,
        source=TransportEvidenceSource.BACKEND_OBSERVATION,
        execution_mode="simulation",
        idempotency_key=interaction_id,
    )
    event = start_interaction(
        task_id=record.task_id,
        interaction_id=interaction_id,
        plan_generation=record.plan_generation,
        assignment_sequence=run.assignment_sequence,
        provider=run.provider.value,
        phase=ProviderInteractionPhase.DISPATCH,
        attempt=1,
        transport_evidence=drafted_evidence,
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, event
    )
    event = transition_interaction(
        record.provider_interaction_ledger,
        interaction_id=interaction_id,
        next_state=ProviderInteractionState.ATTEMPTED,
        transport_evidence=drafted_evidence,
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, event
    )
    authenticated_evidence = build_transport_evidence(
        transport=ProviderTransport.CURSOR_AGENTS_API,
        source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
        execution_mode="simulation",
        idempotency_key=interaction_id,
        provider_run_id=run.run_id,
        provider_session_id=run.session_id,
        native_status=run.status,
        result=run.result,
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
            transport_evidence=authenticated_evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )
    run.interaction_id = interaction_id


class PersistedProviderRunIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_rejects_substituted_run_or_session_before_review(self) -> None:
        substitutions = {
            "run_id": {"run_id": "substituted-run"},
            "session_id": {"session_id": "substituted-session"},
            "missing_session_id": {"session_id": None},
        }
        for label, changes in substitutions.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                database = str(Path(directory) / "tasks.sqlite3")
                settings = replace(
                    Settings(),
                    allow_paid_runs=True,
                    openai_api_key="test-openai",
                    cursor_api_key="test-cursor",
                    project_allowlist=("demo",),
                    repository_allowlist=("example/demo",),
                    allowed_starting_refs=("main",),
                    provider_poll_seconds=0,
                    provider_timeout_seconds=1,
                    sqlite_path=database,
                )
                assignment = Assignment(
                    sequence=1,
                    agent=ProviderName.CURSOR,
                    task="Produce authenticated evidence",
                    acceptance=["Evidence exists"],
                    repository="example/demo",
                )
                run = ProviderRun(
                    provider=ProviderName.CURSOR,
                    assignment_sequence=1,
                    run_id="authenticated-run",
                    session_id="authenticated-session",
                    status="FINISHED",
                    result="Authenticated provider result",
                    evidence_source="provider_authenticated",
                )
                record = TaskRecord(
                    task_id=f"DIR-RESUME-IDENTITY-{label.upper()}",
                    status=TaskStatus.RUNNING,
                    intake=SlackIntake(
                        source_event_id=f"evt-resume-identity-{label}",
                        channel_id="C123",
                        thread_ts="123.45",
                        user_id="U123",
                        text="DIRECTOR Project: demo Outcome: verify resume identity",
                    ),
                    plan=DirectorPlan(
                        project="demo",
                        normalized_outcome="verify resume identity",
                        risk_level=RiskLevel.LOW,
                        approval_required=False,
                        max_hops=1,
                        max_revisions=0,
                        assignments=[assignment],
                        final_acceptance=["Identity remains bound"],
                    ),
                    provider_runs=[run],
                )
                seed_production_plan_materialization(record)
                _seed_verified_run_identity(record, run)
                record.provider_runs[0] = run.model_copy(update=changes)
                store = TaskStore(database)
                store.create(record)
                review = AsyncMock()
                engine = OrchestrationEngine(settings, store)
                engine.director = SimpleNamespace(review=review)
                engine.providers[ProviderName.CURSOR] = NoProviderCallAdapter()

                continued = await engine._execute_assignment(record, assignment)

                persisted = store.get(record.task_id)
                self.assertFalse(continued)
                self.assertEqual(persisted.status, TaskStatus.OWNER_DECISION_REQUIRED)
                self.assertEqual(persisted.message, CommunicationEvidenceFailure.code)
                self.assertIn("persisted provider", persisted.error)
                review.assert_not_awaited()
                self.assertFalse(
                    any(
                        item.phase == "acceptance_review"
                        for item in persisted.task_effect_ledger
                    )
                )


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 11:58 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:53 UTC | END]]
