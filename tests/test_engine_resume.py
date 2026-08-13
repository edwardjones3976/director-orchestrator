from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
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


class AcceptingDirector:
    async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
        return ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary="Acceptance evidence is present.",
            evidence=[result],
        )


class NoRedispatchAdapter:
    def __init__(self):
        self.dispatch_count = 0

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        self.dispatch_count += 1
        raise AssertionError("A recorded provider run must not be redispatched")

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise AssertionError("A terminal provider run must not be polled")


class EngineRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_recorded_run_is_reviewed_without_redispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                cursor_api_key="test-cursor",
                max_agent_hops=1,
                provider_poll_seconds=0,
                provider_timeout_seconds=1,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            intake = SlackIntake(
                source_event_id="evt-resume",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: verify recovery",
            )
            plan = DirectorPlan(
                project="demo",
                normalized_outcome="verify recovery",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CURSOR,
                        task="Produce evidence",
                        acceptance=["Evidence exists"],
                    )
                ],
                final_acceptance=["Assignment accepted"],
            )
            record = TaskRecord(
                task_id="DIR-RESUME",
                status=TaskStatus.RUNNING,
                intake=intake,
                plan=plan,
                provider_runs=[
                    ProviderRun(
                        provider=ProviderName.CURSOR,
                        assignment_sequence=1,
                        run_id="run-1",
                        session_id="agent-1",
                        status="FINISHED",
                        result="Evidence exists",
                    )
                ],
            )
            store.create(record)

            engine = OrchestrationEngine(settings, store)
            adapter = NoRedispatchAdapter()
            engine.providers[ProviderName.CURSOR] = adapter
            engine.director = AcceptingDirector()
            await engine.run(record.task_id)

            completed = store.get(record.task_id)
            self.assertIsNotNone(completed)
            self.assertEqual(completed.status, TaskStatus.DONE)
            self.assertEqual(completed.next_assignment_index, 1)
            self.assertEqual(adapter.dispatch_count, 0)


if __name__ == "__main__":
    unittest.main()

