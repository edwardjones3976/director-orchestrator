from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import httpx
from pydantic import ValidationError

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
from director_orchestrator.policy import execution_policy_errors
from director_orchestrator.review_handoff import bind_review_handoff
from director_orchestrator.store import TaskStore


def review_candidate_payload(*, producer: str = "cursor", material_authors=None) -> dict:
    payload = {
        "artifact_type": "director_review_candidate/v1",
        "assignment_id": "DIR-HANDOFF:1",
        "logical_producer": producer,
        "material_authors": material_authors or [producer],
        "repository": "example/demo",
        "pull_number": 17,
        "base_ref": "main",
        "base_sha": "a" * 40,
        "head_ref": "codex/change",
        "head_sha": "b" * 40,
        "implementation_actor": "cursor-agent",
        "changed_files": ["src/example.py", "tests/test_example.py"],
        "tests": ["python -m unittest: 2 passed"],
        "known_limitations": ["No deployment performed"],
        "acceptance_checks": ["Behavior is covered"],
        "security_context": ["No credential or permission changes"],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    payload["artifact_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return payload


class ReviewHandoffTests(unittest.TestCase):
    def record(self, *, material_authors=None) -> tuple[TaskRecord, Assignment, ProviderRun]:
        producer = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Implement on an isolated branch and create one draft PR",
            acceptance=["Return a review candidate artifact"],
            repository="example/demo",
            starting_ref="main",
            requires_approval=True,
            allow_draft_pr=True,
            estimated_cost_usd=0.5,
        )
        reviewer = Assignment(
            sequence=2,
            agent=ProviderName.CLAUDE,
            task="Reserved independent review slot",
            acceptance=["Review exact candidate"],
            repository="example/demo",
            starting_ref="main",
            depends_on=[1],
            estimated_cost_usd=0.25,
        )
        record = TaskRecord(
            task_id="DIR-HANDOFF",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-handoff",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: implement and review",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="Implement and independently review",
                risk_level=RiskLevel.MEDIUM,
                approval_required=True,
                max_hops=2,
                max_revisions=1,
                assignments=[producer, reviewer],
                final_acceptance=["Exact review, security, and CI remain current"],
            ),
            approved_assignments=[1],
        )
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=1,
            run_id="cursor-run-1",
            status="FINISHED",
            result=json.dumps(
                review_candidate_payload(material_authors=material_authors),
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        return record, producer, run

    def test_exact_candidate_binds_the_prebudgeted_different_provider(self):
        record, producer, run = self.record()

        sequence, reviewer = bind_review_handoff(record, producer, run) or (None, None)

        self.assertEqual(sequence, 2)
        self.assertIsNotNone(reviewer)
        handoff = json.loads(reviewer.task)
        self.assertEqual(handoff["logical_producer"], "cursor")
        self.assertEqual(handoff["reviewer_provider"], "claude")
        self.assertEqual(handoff["head_sha"], "b" * 40)
        self.assertEqual(reviewer.estimated_cost_usd, 0.25)
        self.assertFalse(reviewer.allow_draft_pr)

    def test_material_author_cannot_receive_the_review_handoff(self):
        record, producer, run = self.record(material_authors=["cursor", "claude"])

        with self.assertRaisesRegex(ValueError, "material author"):
            bind_review_handoff(record, producer, run)

    def test_tampering_with_candidate_metadata_invalidates_its_digest(self):
        record, producer, run = self.record()
        payload = json.loads(run.result or "{}")
        payload["head_sha"] = "c" * 40
        run.result = json.dumps(payload)

        with self.assertRaisesRegex(ValidationError, "digest"):
            bind_review_handoff(record, producer, run)


class NonresponseContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_safe_poll_failures_defer_lane_and_continue_independent_work(self):
        class NonresponsiveAdapter:
            name = ProviderName.CLAUDE

            def __init__(self):
                self.dispatch_calls = 0
                self.poll_calls = 0

            async def dispatch(self, assignment, context):
                self.dispatch_calls += 1
                return ProviderRun(
                    provider=self.name,
                    run_id="claude-persisted-run",
                    status="RUNNING",
                    result="partial untrusted output",
                )

            async def poll(self, run):
                self.poll_calls += 1
                raise httpx.ReadTimeout("no response")

        class CompletingAdapter:
            name = ProviderName.CURSOR

            def __init__(self):
                self.dispatch_calls = 0

            async def dispatch(self, assignment, context):
                self.dispatch_calls += 1
                return ProviderRun(
                    provider=self.name,
                    run_id="cursor-independent-run",
                    status="FINISHED",
                    result="independent safe evidence",
                )

            async def poll(self, run):
                raise AssertionError("terminal run must not be polled")

        class AcceptingDirector:
            async def review(self, record, result):
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Independent evidence accepted.",
                    evidence=["bounded test evidence"],
                )

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                cursor_api_key="test-cursor",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=3,
                provider_poll_seconds=0,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            store = TaskStore(settings.sqlite_path)
            task = TaskRecord(
                task_id="DIR-NONRESPONSE",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-nonresponse",
                    channel_id="C1",
                    thread_ts="1.2",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: run independent lanes",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Run independent lanes",
                    risk_level=RiskLevel.MEDIUM,
                    approval_required=False,
                    max_hops=3,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Run unavailable lane",
                            acceptance=["Return evidence"],
                        ),
                        Assignment(
                            sequence=2,
                            agent=ProviderName.CURSOR,
                            task="Run independent safe lane",
                            acceptance=["Return independent evidence"],
                            repository="example/demo",
                        ),
                    ],
                    final_acceptance=["Report completed and deferred lanes"],
                ),
            )
            store.create(task)
            claude = NonresponsiveAdapter()
            cursor = CompletingAdapter()
            engine = OrchestrationEngine(settings, store)
            engine.providers = {
                ProviderName.CLAUDE: claude,
                ProviderName.CURSOR: cursor,
            }
            engine.director = AcceptingDirector()

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(
                completed.status,
                TaskStatus.OWNER_DECISION_REQUIRED,
                completed.error or completed.message,
            )
            self.assertEqual(claude.dispatch_calls, 1)
            self.assertEqual(claude.poll_calls, 3)
            self.assertEqual(cursor.dispatch_calls, 1)
            self.assertEqual(
                [attempt.ordinal for attempt in completed.provider_contact_attempts],
                [1, 2, 3],
            )
            self.assertTrue(
                all(attempt.partial_output == "partial untrusted output" for attempt in completed.provider_contact_attempts)
            )
            self.assertEqual(completed.unavailable_providers, [ProviderName.CLAUDE])
            self.assertEqual(completed.deferred_assignments, [1])
            self.assertEqual(completed.assignment_outcomes[0].provider, ProviderName.CURSOR)
            self.assertIn("no duplicate dispatch", completed.availability_impacts[0])

    async def test_ambiguous_dispatch_is_recorded_once_and_never_retried(self):
        class AmbiguousAdapter:
            name = ProviderName.CLAUDE

            def __init__(self):
                self.dispatch_calls = 0

            async def dispatch(self, assignment, context):
                self.dispatch_calls += 1
                raise httpx.ReadTimeout("request outcome unknown")

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            store = TaskStore(settings.sqlite_path)
            task = TaskRecord(
                task_id="DIR-AMBIGUOUS",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-ambiguous",
                    channel_id="C1",
                    thread_ts="1.3",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: bounded work",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Bounded work",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Perform bounded work",
                            acceptance=["Return evidence"],
                        )
                    ],
                    final_acceptance=["Evidence accepted"],
                ),
            )
            store.create(task)
            adapter = AmbiguousAdapter()
            engine = OrchestrationEngine(settings, store)
            engine.providers = {ProviderName.CLAUDE: adapter}

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(completed.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(adapter.dispatch_calls, 1)
            self.assertEqual(len(completed.provider_contact_attempts), 1)
            self.assertEqual(completed.provider_contact_attempts[0].outcome, "ambiguous_dispatch")


class AssignmentBoundaryTests(unittest.TestCase):
    def test_renamed_nested_and_malformed_assignment_fields_fail_closed(self):
        valid = {
            "sequence": 1,
            "agent": "claude",
            "task": "Analyze",
            "acceptance": ["Report evidence"],
        }
        cases = [
            {**valid, "provider": valid["agent"]},
            {**valid, "task": {"nested": "Analyze"}},
            {**valid, "acceptance": "Report evidence"},
            {**valid, "public_research_query": ["not", "a", "string"]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    Assignment.model_validate(payload)

    def test_perplexity_indirect_verdicts_and_private_queries_fail_closed(self):
        settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        cases = [
            (
                "Assess whether the attached patch should be accepted",
                "public evidence about calibration",
                "operational decision",
            ),
            (
                "Find public methodology",
                r"review C:\\Users\\owner\\private\\src\\policy.py",
                "repository path",
            ),
            (
                "Find public methodology",
                "analyze commit " + "a" * 40,
                "commit SHA",
            ),
            (
                "Find public methodology",
                "use secret sk-examplecredential123456",
                "secret-like value",
            ),
        ]
        for index, (task, query, reason) in enumerate(cases, start=1):
            plan = DirectorPlan(
                project="demo",
                normalized_outcome="Bounded public research",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.PERPLEXITY,
                        task=task,
                        acceptance=["Return public sources"],
                        public_research_query=query,
                    )
                ],
                final_acceptance=["Primary team evaluates evidence"],
            )
            with self.subTest(index=index):
                errors = execution_policy_errors(plan, settings)
                self.assertTrue(any(reason in error for error in errors), errors)
