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
from director_orchestrator.approvals import assignment_approval_digest
from director_orchestrator.engine import OrchestrationEngine, ProviderUnavailableError
from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
    DirectorPlan,
    ProviderName,
    ProviderContactAttempt,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.policy import execution_policy_errors
from director_orchestrator.oracle_merge import authenticate_provider_review_artifact
from director_orchestrator.review_handoff import (
    bind_review_handoff,
    transitive_dependency_sequences,
)
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
    ACTORS = {
        "cursor": frozenset({"cursor-agent"}),
        "claude": frozenset({"claude-agent"}),
        "gemini": frozenset({"gemini-agent"}),
    }

    def bind(self, record, producer, run):
        return bind_review_handoff(
            record,
            producer,
            run,
            trusted_provider_actors=self.ACTORS,
        )

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
            approved_assignment_digests={1: assignment_approval_digest(producer)},
        )
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=1,
            run_id="cursor-run-1",
            status="FINISHED",
            evidence_source="provider_authenticated",
            result=json.dumps(
                review_candidate_payload(material_authors=material_authors),
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        return record, producer, run

    def test_exact_candidate_binds_the_prebudgeted_different_provider(self):
        record, producer, run = self.record()

        sequence, reviewer = self.bind(record, producer, run) or (None, None)

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
            self.bind(record, producer, run)

    def test_producer_cannot_omit_recorded_material_author(self):
        record, producer, run = self.record()
        upstream = Assignment(
            sequence=3,
            agent=ProviderName.CLAUDE,
            task="Provide material implementation input",
            acceptance=["Return evidence"],
        )
        producer.depends_on = [3]
        record.plan.assignments.append(upstream)
        record.approved_assignment_digests[1] = assignment_approval_digest(producer)
        record.assignment_outcomes.append(
            AssignmentOutcome(
                assignment_sequence=3,
                provider=ProviderName.CLAUDE,
                run_id="claude-upstream",
                result="material implementation input",
                review_summary="accepted",
            )
        )

        with self.assertRaisesRegex(ValueError, "recorded orchestration provenance"):
            self.bind(record, producer, run)

    def test_review_artifact_identity_is_bound_to_the_stored_handoff(self):
        record, producer, run = self.record()
        record.assignment_outcomes.append(
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.CURSOR,
                run_id="cursor-run-1",
                result=run.result or "candidate",
                review_summary="accepted",
            )
        )
        sequence, reviewer = self.bind(record, producer, run) or (None, None)
        self.assertEqual(sequence, 2)
        handoff = json.loads(reviewer.task)
        record.plan.assignments = [producer, reviewer]
        record.review_handoff_digests[2] = handoff["artifact_digest"]
        payload = {
            "artifact_type": "edge_oracle_exact_sha_review/v1",
            "artifact_id": handoff["required_review_artifact_id"],
            "assignment_id": handoff["assignment_id"],
            "project": "demo",
            "repository": handoff["repository"],
            "pull_number": handoff["pull_number"],
            "base_ref": handoff["base_ref"],
            "base_sha": handoff["base_sha"],
            "head_ref": handoff["head_ref"],
            "head_sha": handoff["head_sha"],
            "implementation_provider": handoff["logical_producer"],
            "implementation_actor": handoff["implementation_actor"],
            "reviewer_provider": handoff["reviewer_provider"],
            "review_run_id": "claude-review-run",
            "verdict": "ACCEPT",
            "summary": "Accepted exact candidate.",
            "evidence": ["Reviewed the bound candidate."],
            "acceptance_checks": ["test"],
            "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
        }
        review_run = ProviderRun(
            provider=ProviderName.CLAUDE,
            assignment_sequence=2,
            run_id="claude-review-run",
            status="completed",
            evidence_source="provider_authenticated",
            result=json.dumps(payload),
        )
        record.provider_runs.append(review_run)
        authenticated = authenticate_provider_review_artifact(record, review_run, "secret")
        self.assertIsNotNone(authenticated)

        review_run.evidence_source = "owner_recovery"
        record.provider_runs[-1] = review_run
        with self.assertRaisesRegex(ValueError, "authenticated provider evidence"):
            authenticate_provider_review_artifact(record, review_run, "secret")
        review_run.evidence_source = "provider_authenticated"
        record.provider_runs[-1] = review_run

        for field, changed in (("pull_number", 18), ("implementation_actor", "other-actor")):
            with self.subTest(field=field):
                tampered = {**payload, field: changed}
                review_run.result = json.dumps(tampered)
                record.provider_runs[-1] = review_run
                with self.assertRaisesRegex(ValueError, "stored handoff"):
                    authenticate_provider_review_artifact(record, review_run, "secret")

    def test_tampering_with_candidate_metadata_invalidates_its_digest(self):
        record, producer, run = self.record()
        payload = json.loads(run.result or "{}")
        payload["head_sha"] = "c" * 40
        run.result = json.dumps(payload)

        with self.assertRaisesRegex(ValidationError, "digest"):
            self.bind(record, producer, run)

    def test_owner_recovery_result_cannot_become_provider_review_authority(self):
        record, producer, run = self.record()
        run.evidence_source = "owner_recovery"

        with self.assertRaisesRegex(ValueError, "provider-authenticated"):
            self.bind(record, producer, run)

    def test_nonwrite_and_unapproved_producers_cannot_arm_review(self):
        record, producer, run = self.record()
        nonwrite = producer.model_copy(update={"allow_draft_pr": False})
        record.plan.assignments[0] = nonwrite
        record.approved_assignment_digests[1] = assignment_approval_digest(nonwrite)
        with self.assertRaisesRegex(ValueError, "lacks draft-PR authority"):
            self.bind(record, nonwrite, run)

        record, producer, run = self.record()
        record.approved_assignment_digests = {}
        with self.assertRaisesRegex(ValueError, "exact-scope owner approval"):
            self.bind(record, producer, run)

    def test_candidate_actor_must_belong_to_producer_and_not_reviewer(self):
        record, producer, run = self.record()
        payload = json.loads(run.result or "{}")
        payload["implementation_actor"] = "claude-agent"
        payload.pop("artifact_digest")
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        payload["artifact_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        run.result = json.dumps(payload)

        with self.assertRaisesRegex(ValueError, "not bound to the logical producer"):
            self.bind(record, producer, run)

        with self.assertRaisesRegex(ValueError, "no configured trusted GitHub actor"):
            bind_review_handoff(
                record,
                producer,
                ProviderRun.model_validate({**run.model_dump(), "result": json.dumps(review_candidate_payload())}),
                trusted_provider_actors={},
            )

    def test_transitive_material_author_cannot_review_final_candidate(self):
        upstream = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Create material design input",
            acceptance=["Return design evidence"],
        )
        middle = Assignment(
            sequence=2,
            agent=ProviderName.GEMINI,
            task="Turn the design into a tested prototype",
            acceptance=["Return prototype evidence"],
            depends_on=[1],
        )
        producer = Assignment(
            sequence=3,
            agent=ProviderName.CURSOR,
            task="Integrate and create a draft PR",
            acceptance=["Return exact candidate"],
            repository="example/demo",
            starting_ref="main",
            depends_on=[2],
            requires_approval=True,
            allow_draft_pr=True,
        )
        reviewer = Assignment(
            sequence=4,
            agent=ProviderName.CLAUDE,
            task="Reserved review",
            acceptance=["Review exact candidate"],
            repository="example/demo",
            depends_on=[3],
        )
        record = TaskRecord(
            task_id="DIR-HANDOFF",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-transitive",
                channel_id="C1",
                thread_ts="1.5",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: staged implementation",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="Staged implementation",
                risk_level=RiskLevel.MEDIUM,
                approval_required=True,
                max_hops=4,
                max_revisions=0,
                assignments=[upstream, middle, producer, reviewer],
                final_acceptance=["Independent exact-SHA review"],
            ),
            assignment_outcomes=[
                AssignmentOutcome(
                    assignment_sequence=1,
                    provider=ProviderName.CLAUDE,
                    run_id="claude-upstream",
                    result="design",
                    review_summary="accepted",
                ),
                AssignmentOutcome(
                    assignment_sequence=2,
                    provider=ProviderName.GEMINI,
                    run_id="gemini-middle",
                    result="prototype",
                    review_summary="accepted",
                ),
            ],
            approved_assignments=[3],
            approved_assignment_digests={3: assignment_approval_digest(producer)},
        )
        payload = review_candidate_payload(
            material_authors=["claude", "gemini", "cursor"]
        )
        payload["assignment_id"] = "DIR-HANDOFF:3"
        payload.pop("artifact_digest")
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        payload["artifact_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=3,
            run_id="cursor-final",
            status="FINISHED",
            evidence_source="provider_authenticated",
            result=json.dumps(payload),
        )

        with self.assertRaisesRegex(ValueError, "review provider is listed as a material author"):
            self.bind(record, producer, run)

    def test_dependency_closure_excludes_unrelated_prior_assignments(self):
        unrelated = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Unrelated analysis",
            acceptance=["Evidence"],
        )
        upstream = Assignment(
            sequence=2,
            agent=ProviderName.GEMINI,
            task="Declared upstream input",
            acceptance=["Evidence"],
        )
        middle = Assignment(
            sequence=3,
            agent=ProviderName.CLAUDE,
            task="Declared middle input",
            acceptance=["Evidence"],
            depends_on=[2],
        )
        producer = Assignment(
            sequence=4,
            agent=ProviderName.CURSOR,
            task="Produce candidate",
            acceptance=["Evidence"],
            depends_on=[3],
        )

        self.assertEqual(
            transitive_dependency_sequences(
                [unrelated, upstream, middle, producer], producer
            ),
            {2, 3},
        )


class NonresponseContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_attempt_limit_survives_process_restart(self):
        class MustNotPoll:
            async def poll(self, run):
                raise AssertionError("persisted attempt cap must stop before another poll")

        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="bounded work",
            acceptance=["evidence"],
        )
        run = ProviderRun(
            provider=ProviderName.CLAUDE,
            assignment_sequence=1,
            run_id="persisted-run",
            status="RUNNING",
            result="partial",
        )
        record = TaskRecord(
            task_id="DIR-PERSISTED-ATTEMPTS",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-persisted",
                channel_id="C1",
                thread_ts="1.4",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: bounded work",
            ),
            provider_contact_attempts=[
                ProviderContactAttempt(
                    assignment_sequence=1,
                    provider=ProviderName.CLAUDE,
                    ordinal=ordinal,
                    phase="poll",
                    outcome="no_response",
                    run_id="persisted-run",
                    partial_output="partial",
                    error="ReadTimeout: unavailable",
                )
                for ordinal in (1, 2, 3)
            ],
        )
        engine = OrchestrationEngine(replace(Settings(), provider_poll_seconds=0), None)

        with self.assertRaises(ProviderUnavailableError):
            await engine._wait(MustNotPoll(), run, record, assignment)
        self.assertEqual(len(record.provider_contact_attempts), 3)

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

            completed.status = TaskStatus.QUEUED
            store.save(completed)
            await engine.run(task.task_id)
            self.assertEqual(adapter.dispatch_calls, 1)
            self.assertIn("remains ambiguous", (store.get(task.task_id).message or ""))


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
            (
                "Find public methodology",
                "Research records about jane.doe@example.com",
                "email address",
            ),
            (
                "Find public methodology",
                "Research records for 018f0f5e-7b3a-7c9d-8f12-123456789abc",
                "UUID",
            ),
            (
                "Find public methodology",
                "Research records for Slack user u0b9uj86d27",
                "Slack identifier",
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
