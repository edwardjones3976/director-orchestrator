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
    async def test_revision_preserves_assignment_and_approval_context(self):
        class RevisingDirector:
            def __init__(self):
                self.reviews = 0

            async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
                self.reviews += 1
                if self.reviews == 1:
                    return ReviewDecision(
                        verdict=ReviewVerdict.REVISE,
                        summary="More evidence required.",
                        evidence=[],
                        revision_prompt="Add exact evidence.",
                    )
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Revision accepted.",
                    evidence=[result],
                )

        class RevisingAdapter:
            def __init__(self):
                self.revision_assignment = None
                self.revision_context = None

            async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
                return ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id=f"claude-original-{assignment.sequence}",
                    status="completed",
                    result=(
                        "Incomplete evidence"
                        if assignment.sequence == 1
                        else "Independent evidence"
                    ),
                )

            async def poll(self, run: ProviderRun) -> ProviderRun:
                raise AssertionError("Terminal runs must not be polled")

            async def revise(
                self,
                run: ProviderRun,
                prompt: str,
                *,
                assignment: Assignment,
                context: str,
            ) -> ProviderRun:
                self.revision_assignment = assignment
                self.revision_context = json.loads(context)
                return ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id="claude-revision",
                    status="completed",
                    result="Exact evidence added",
                )

        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.example/routine",
                claude_routine_token="test-claude",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=4,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CLAUDE,
                task="Analyze",
                acceptance=["Exact evidence"],
                repository="example/demo",
                requires_approval=True,
            )
            second_assignment = Assignment(
                sequence=2,
                agent=ProviderName.CLAUDE,
                task="Review independently",
                acceptance=["Independent evidence"],
                repository="example/demo",
                depends_on=[1],
            )
            record = TaskRecord(
                task_id="DIR-REVISION",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-revision",
                    channel_id="C123",
                    thread_ts="123.44",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: analyze",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="analyze",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=2,
                    max_revisions=1,
                    assignments=[assignment, second_assignment],
                    final_acceptance=["Accepted"],
                ),
                approved_assignments=[1],
            )
            store.create(record)

            engine = OrchestrationEngine(settings, store)
            adapter = RevisingAdapter()
            engine.providers[ProviderName.CLAUDE] = adapter
            engine.director = RevisingDirector()
            await engine.run(record.task_id)

            completed = store.get(record.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE)
            self.assertEqual(
                [run.run_id for run in completed.provider_runs],
                ["claude-original-1", "claude-revision", "claude-original-2"],
            )
            self.assertEqual(len(completed.assignment_outcomes), 2)
            self.assertEqual(adapter.revision_assignment.sequence, 1)
            self.assertTrue(adapter.revision_context["current_assignment"]["owner_approval_recorded"])

    async def test_each_assignment_gets_its_declared_revision_headroom(self):
        class RevisingEveryAssignmentDirector:
            def __init__(self):
                self.reviews_by_result = {}

            async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
                base_result = result.removesuffix(" revised")
                reviews = self.reviews_by_result.get(base_result, 0) + 1
                self.reviews_by_result[base_result] = reviews
                if reviews == 1:
                    return ReviewDecision(
                        verdict=ReviewVerdict.REVISE,
                        summary="One bounded revision required.",
                        evidence=[],
                        revision_prompt="Add exact evidence.",
                    )
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Revision accepted.",
                    evidence=[result],
                )

        class RevisingEveryAssignmentAdapter:
            async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
                return ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id=f"original-{assignment.sequence}",
                    status="completed",
                    result=f"assignment-{assignment.sequence}",
                )

            async def poll(self, run: ProviderRun) -> ProviderRun:
                raise AssertionError("Terminal runs must not be polled")

            async def revise(
                self,
                run: ProviderRun,
                prompt: str,
                *,
                assignment: Assignment,
                context: str,
            ) -> ProviderRun:
                return ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id=f"revision-{assignment.sequence}",
                    status="completed",
                    result=f"assignment-{assignment.sequence} revised",
                )

        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.example/routine",
                claude_routine_token="test-claude",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=4,
                max_revisions=1,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            assignments = [
                Assignment(
                    sequence=sequence,
                    agent=ProviderName.CLAUDE,
                    task=f"Analyze {sequence}",
                    acceptance=["Exact evidence"],
                    repository="example/demo",
                    depends_on=[] if sequence == 1 else [1],
                )
                for sequence in (1, 2)
            ]
            record = TaskRecord(
                task_id="DIR-REVISION-EACH",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-revision-each",
                    channel_id="C123",
                    thread_ts="123.45",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: analyze",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="analyze",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=2,
                    max_revisions=1,
                    assignments=assignments,
                    final_acceptance=["Accepted"],
                ),
            )
            store.create(record)

            engine = OrchestrationEngine(settings, store)
            engine.providers[ProviderName.CLAUDE] = RevisingEveryAssignmentAdapter()
            engine.director = RevisingEveryAssignmentDirector()
            await engine.run(record.task_id)

            completed = store.get(record.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE)
            self.assertEqual(
                [run.run_id for run in completed.provider_runs],
                ["original-1", "revision-1", "original-2", "revision-2"],
            )
            self.assertEqual(len(completed.assignment_outcomes), 2)

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
                        requires_approval=True,
                    ),
                ],
                final_acceptance=["Implementation and review accepted"],
            )
            record = TaskRecord(
                task_id="DIR-HANDOFF",
                status=TaskStatus.QUEUED,
                intake=intake,
                plan=plan,
                approved_assignments=[2],
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
                adapter.contexts[0]["current_assignment"],
                {
                    "sequence": 1,
                    "depends_on": [],
                    "requires_approval": False,
                    "owner_approval_recorded": False,
                },
            )
            self.assertEqual(
                second_context["current_assignment"],
                {
                    "sequence": 2,
                    "depends_on": [1],
                    "requires_approval": True,
                    "owner_approval_recorded": True,
                },
            )
            self.assertEqual(
                second_context["dependency_outcomes"][0]["result"],
                "accepted result 1",
            )


if __name__ == "__main__":
    unittest.main()

