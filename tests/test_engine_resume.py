from __future__ import annotations

import json
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
    async def test_execution_policy_blocks_before_provider_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                cursor_api_key="test-cursor",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            record = TaskRecord(
                task_id="DIR-BLOCKED",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-blocked",
                    channel_id="C123",
                    thread_ts="123.40",
                    user_id="U123",
                    text="DIRECTOR Project: other Outcome: do not dispatch",
                ),
                plan=DirectorPlan(
                    project="other",
                    normalized_outcome="do not dispatch",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CURSOR,
                            task="Must remain blocked",
                            acceptance=["No dispatch"],
                            repository="example/not-allowed",
                        )
                    ],
                    final_acceptance=["No provider call"],
                ),
            )
            store.create(record)

            engine = OrchestrationEngine(settings, store)
            adapter = NoRedispatchAdapter()
            engine.providers[ProviderName.CURSOR] = adapter
            await engine.run(record.task_id)

            blocked = store.get(record.task_id)
            self.assertIsNotNone(blocked)
            self.assertEqual(blocked.status, TaskStatus.BLOCKED)
            self.assertEqual(adapter.dispatch_count, 0)
            self.assertIn("project is not allowlisted", blocked.message)
            self.assertIn("repository is not allowlisted", blocked.message)

    async def test_terminal_recorded_run_is_reviewed_without_redispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                cursor_api_key="test-cursor",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
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
                        repository="example/demo",
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

    async def test_prior_accepted_result_is_handed_to_dependent_assignment(self):
        class RecordingAdapter:
            def __init__(self):
                self.contexts = []

            async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
                self.contexts.append(json.loads(context))
                return ProviderRun(
                    provider=ProviderName.CURSOR,
                    assignment_sequence=assignment.sequence,
                    run_id=f"run-{assignment.sequence}",
                    status="FINISHED",
                    result=f"accepted result {assignment.sequence}",
                )

            async def poll(self, run: ProviderRun) -> ProviderRun:
                raise AssertionError("Terminal runs must not be polled")

        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                cursor_api_key="test-cursor",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=2,
                provider_poll_seconds=0,
                provider_timeout_seconds=1,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            intake = SlackIntake(
                source_event_id="evt-handoff",
                channel_id="C123",
                thread_ts="123.46",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: implement then review",
            )
            plan = DirectorPlan(
                project="demo",
                normalized_outcome="implement then review",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=2,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CURSOR,
                        task="Implement",
                        acceptance=["Implementation evidence"],
                        repository="example/demo",
                    ),
                    Assignment(
                        sequence=2,
                        agent=ProviderName.CURSOR,
                        task="Review the implementation",
                        acceptance=["Independent review evidence"],
                        repository="example/demo",
                        depends_on=[1],
                    ),
                ],
                final_acceptance=["Implementation and review accepted"],
            )
            record = TaskRecord(
                task_id="DIR-HANDOFF",
                status=TaskStatus.QUEUED,
                intake=intake,
                plan=plan,
            )
            store.create(record)

            engine = OrchestrationEngine(settings, store)
            adapter = RecordingAdapter()
            engine.providers[ProviderName.CURSOR] = adapter
            engine.director = AcceptingDirector()
            await engine.run(record.task_id)

            completed = store.get(record.task_id)
            self.assertIsNotNone(completed)
            self.assertEqual(completed.status, TaskStatus.DONE)
            self.assertEqual(len(completed.assignment_outcomes), 2)
            second_context = adapter.contexts[1]
            self.assertIn("untrusted evidence", second_context["trust_boundary"])
            self.assertEqual(
                second_context["dependency_outcomes"][0]["result"],
                "accepted result 1",
            )


if __name__ == "__main__":
    unittest.main()

