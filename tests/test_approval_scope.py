# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:51 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:38 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:59 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.approvals import (
    assignment_approval_digest,
    record_task_effect_observation,
)
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    IntakeAuthentication,
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
from director_orchestrator.policy import enforce_plan


class AcceptingDirector:
    async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
        return ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary="Acceptance evidence is present.",
            evidence=[result],
        )


class RecordingAdapter:
    def __init__(self):
        self.dispatched: list[int] = []

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        self.dispatched.append(assignment.sequence)
        return ProviderRun(
            provider=ProviderName.CURSOR,
            run_id=f"run-{assignment.sequence}",
            session_id=f"agent-{assignment.sequence}",
            status="FINISHED",
            result=f"Evidence for assignment {assignment.sequence}",
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        return run


def _gated_plan() -> DirectorPlan:
    """Two gated assignments, as enforce_plan produces when any gate fires."""
    return DirectorPlan(
        project="demo",
        normalized_outcome="ship two gated steps",
        risk_level=RiskLevel.HIGH,
        approval_required=True,
        approval_reasons=["merge or protected-branch change"],
        max_hops=4,
        max_revisions=0,
        assignments=[
            Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="First gated step",
                acceptance=["Evidence exists"],
                repository="example/demo",
                requires_approval=True,
            ),
            Assignment(
                sequence=2,
                agent=ProviderName.CURSOR,
                task="Second gated step",
                acceptance=["Evidence exists"],
                repository="example/demo",
                requires_approval=True,
            ),
        ],
        final_acceptance=["Both assignments accepted"],
    )


class ApprovalScopeTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self, directory: str, record: TaskRecord):
        store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
        settings = replace(
            Settings(),
            allow_paid_runs=True,
            openai_api_key="test-openai",
            cursor_api_key="test-cursor",
            max_agent_hops=4,
            provider_poll_seconds=0,
            provider_timeout_seconds=1,
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
            sqlite_path=str(Path(directory) / "tasks.sqlite3"),
        )
        raw_plan = record.plan
        self.assertIsNotNone(raw_plan)
        record.plan = None
        store.create(record)
        engine = OrchestrationEngine(settings, store)
        persisted = store.get(record.task_id)
        target_generation = persisted.plan_generation + 1
        reservation = engine._reserve_director_effect(
            persisted,
            phase="plan",
            identity={
                "intake": hashlib.sha256(
                    persisted.intake.model_dump_json().encode("utf-8")
                ).hexdigest(),
                "target_generation": target_generation,
            },
            estimated_cost_usd=settings.openai_plan_estimated_cost_usd,
        )
        self.assertIsNotNone(reservation)
        persisted, effect_key = reservation
        plan_interaction_id = engine._begin_director_interaction(
            persisted,
            phase="plan",
            effect_key=effect_key,
            plan_generation=target_generation,
        )
        verified_plan_event = engine._complete_director_interaction(
            persisted,
            interaction_id=plan_interaction_id,
            result=raw_plan,
        )
        persisted.plan = enforce_plan(raw_plan, persisted.intake.text, settings)
        persisted.plan_generation = target_generation
        persisted.plan_interaction_id = plan_interaction_id
        record_task_effect_observation(
            persisted,
            effect_key=effect_key,
            intent_kind="director_intent",
            observation_kind="director_observation",
            phase="plan",
            outcome="completed",
            run_id=engine._plan_materialization_binding(
                persisted,
                verified_event_digest=verified_plan_event.event_digest,
                verified_result_digest=(
                    verified_plan_event.transport_evidence.result_digest or ""
                ),
                effect_key=effect_key,
            ),
        )
        store.save(persisted)
        adapter = RecordingAdapter()
        engine.providers[ProviderName.CURSOR] = adapter
        engine.director = AcceptingDirector()
        return store, engine, adapter

    def _record(self, approved: list[int]) -> TaskRecord:
        record = TaskRecord(
            task_id="DIR-SCOPE",
            status=TaskStatus.QUEUED,
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
            intake=SlackIntake(
                source_event_id="evt-scope",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: ship two gated steps",
            ),
            plan=_gated_plan(),
            owner_approved=True,
            approved_assignments=list(approved),
        )
        record.approved_assignment_digests = {
            sequence: assignment_approval_digest(
                next(item for item in record.plan.assignments if item.sequence == sequence)
            )
            for sequence in approved
        }
        return record

    async def test_approving_one_assignment_does_not_clear_the_next(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store, engine, adapter = self._engine(directory, self._record([1]))
            await engine.run("DIR-SCOPE")

            record = store.get("DIR-SCOPE")
            # Assignment 1 was approved and ran; assignment 2 must stop and ask.
            self.assertEqual(adapter.dispatched, [1])
            self.assertEqual(record.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(record.next_assignment_index, 1)
            self.assertIn("2", record.message)

    async def test_approving_each_assignment_completes_the_plan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store, engine, adapter = self._engine(directory, self._record([1, 2]))
            await engine.run("DIR-SCOPE")

            record = store.get("DIR-SCOPE")
            self.assertEqual(adapter.dispatched, [1, 2])
            self.assertEqual(record.status, TaskStatus.DONE)

    async def test_unapproved_first_assignment_never_dispatches(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store, engine, adapter = self._engine(directory, self._record([]))
            await engine.run("DIR-SCOPE")

            record = store.get("DIR-SCOPE")
            self.assertEqual(adapter.dispatched, [])
            self.assertEqual(record.status, TaskStatus.OWNER_DECISION_REQUIRED)


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 09:59 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:51 UTC | END]]
