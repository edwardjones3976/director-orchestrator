from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

import director_orchestrator.main as main
from director_orchestrator.config import Settings
from director_orchestrator.approvals import (
    apply_oracle_recovery,
    assignment_approval_digest,
    reset_for_owner_clarification,
    record_provider_effect_observation,
    reserve_task_effect,
    task_effect_key,
)
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
    DirectorPlan,
    OracleRecoveryApproval,
    ProviderName,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.oracle_merge import (
    OracleMergeInProgress,
    OracleMergeResult,
    OracleMergeStatus,
)
from director_orchestrator.providers import ProviderError
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
    async def test_late_planning_observer_does_not_mutate_live_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                openai_plan_estimated_cost_usd=0.1,
                sqlite_path=database,
            )
            task = TaskRecord(
                task_id="DIR-LATE-PLAN-OBSERVER",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-late-plan-observer",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Build one bounded plan",
                ),
            )
            owner_store = TaskStore(database)
            owner_store.create(task)
            owner_engine = OrchestrationEngine(settings, owner_store)
            identity = {
                "intake": hashlib.sha256(
                    task.intake.model_dump_json().encode("utf-8")
                ).hexdigest(),
                "target_generation": 1,
            }
            owner = owner_store.get(task.task_id)
            self.assertIsNotNone(
                owner_engine._reserve_director_effect(
                    owner,
                    phase="plan",
                    identity=identity,
                    estimated_cost_usd=0.1,
                )
            )

            observer_store = TaskStore(database)
            observer, version_before = observer_store.get_versioned(task.task_id)
            observer_engine = OrchestrationEngine(settings, observer_store)
            self.assertIsNone(
                observer_engine._reserve_director_effect(
                    observer,
                    phase="plan",
                    identity=identity,
                    estimated_cost_usd=0.1,
                )
            )
            current, version_after = observer_store.get_versioned(task.task_id)
            self.assertEqual(version_after, version_before)
            self.assertEqual(current.status, TaskStatus.PLANNING)
            self.assertEqual(
                [item.kind for item in current.task_effect_ledger],
                ["director_intent"],
            )

            lease_expiry = (
                current.task_effect_ledger[0].recorded_at.timestamp()
                + max(180, settings.provider_timeout_seconds)
                + 61
            )
            status_post = AsyncMock(return_value=None)
            with (
                patch("director_orchestrator.engine.time.time", return_value=lease_expiry),
                patch.object(main, "store", observer_store),
                patch.object(main, "engine", observer_engine),
                patch.object(observer_engine, "_status_post", new=status_post),
            ):
                await main._recoverable_tasks_once()
            expired = observer_store.get(task.task_id)
            self.assertEqual(expired.status, TaskStatus.FAILED)
            self.assertIn("ambiguous outcome", expired.error)
            status_post.assert_awaited_once()

    async def test_oracle_observer_does_not_invalidate_live_merge_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="Review one exact SHA",
                acceptance=["Return signed exact-SHA evidence"],
                repository="edwardjones3976/edge_engine",
            )
            run = ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=1,
                run_id="review-run",
                status="FINISHED",
                result="accepted artifact",
            )
            record = TaskRecord(
                task_id="DIR-ORACLE-OBSERVER",
                status=TaskStatus.REVIEWING,
                intake=SlackIntake(
                    source_event_id="evt-oracle-observer",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Review one exact SHA",
                ),
                plan=DirectorPlan(
                    project="edge-oracle",
                    normalized_outcome="Review one exact SHA",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[assignment],
                    final_acceptance=["Exact review recorded"],
                ),
                provider_runs=[run],
            )
            artifact = SimpleNamespace(
                artifact_id="artifact-live-owner",
                digest=lambda: "artifact-digest",
            )
            effect_key = task_effect_key(
                record,
                phase="oracle_merge",
                identity={
                    "artifact_id": artifact.artifact_id,
                    "artifact_digest": artifact.digest(),
                },
            )
            reserve_task_effect(
                record,
                effect_key=effect_key,
                kind="oracle_merge_intent",
                phase="oracle_merge",
                assignment=assignment,
                replay_safe=True,
            )
            task_store = TaskStore(database)
            task_store.create(record)
            observer, version_before = task_store.get_versioned(record.task_id)

            class ActiveService:
                policy = SimpleNamespace(
                    enabled=True,
                    kill_switch=False,
                    review_secret="test-secret",
                )

                @staticmethod
                def process(candidate):
                    raise OracleMergeInProgress("exact artifact has a live owner")

            engine = OrchestrationEngine(
                replace(Settings(), sqlite_path=database),
                task_store,
                ActiveService(),
            )
            status_post = AsyncMock(return_value=None)
            with (
                patch(
                    "director_orchestrator.engine.authenticate_provider_review_artifact",
                    return_value=artifact,
                ),
                patch.object(engine, "_status_post", new=status_post),
            ):
                self.assertFalse(
                    await engine._process_exact_sha_review(observer, run)
                )

            current, version_after = task_store.get_versioned(record.task_id)
            self.assertEqual(version_after, version_before)
            self.assertEqual(current.status, TaskStatus.REVIEWING)
            self.assertIsNone(current.oracle_recovery)
            status_post.assert_not_awaited()

    async def test_two_process_planners_have_one_intent_owner_and_keep_its_result(self):
        class PlanningDirector:
            def __init__(self):
                self.calls = 0
                self.lock = threading.Lock()

            async def plan(self, record: TaskRecord) -> DirectorPlan:
                with self.lock:
                    self.calls += 1
                return DirectorPlan(
                    project="demo",
                    normalized_outcome="Preserve the winning plan",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    owner_question="Confirm the winning plan",
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.OWNER,
                            task="Confirm the winning plan",
                            acceptance=["Owner confirms"],
                        )
                    ],
                    final_acceptance=["Plan persisted"],
                )

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                openai_plan_estimated_cost_usd=0.1,
                project_allowlist=("demo",),
                sqlite_path=database,
            )
            task = TaskRecord(
                task_id="DIR-PLAN-RACE",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-plan-race",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Build one bounded plan",
                ),
            )
            first_store = TaskStore(database)
            first_store.create(task)
            stores = (first_store, TaskStore(database))
            director = PlanningDirector()
            engines = tuple(OrchestrationEngine(settings, item) for item in stores)
            for engine in engines:
                engine.director = director
                engine._status_post = AsyncMock(return_value=None)
            barrier = threading.Barrier(2)

            def run_engine(engine: OrchestrationEngine) -> None:
                original = engine.store.mutate_if_version

                def synchronized(task_id: str, expected_version: int, mutator, **kwargs):
                    barrier.wait(timeout=5)
                    return original(task_id, expected_version, mutator, **kwargs)

                with patch.object(
                    engine.store, "mutate_if_version", side_effect=synchronized
                ):
                    asyncio.run(engine.run(task.task_id))

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(run_engine, engine) for engine in engines]
                for future in futures:
                    future.result(timeout=15)

            stored = TaskStore(database).get(task.task_id)
            self.assertEqual(director.calls, 1)
            self.assertEqual(stored.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stored.plan.normalized_outcome, "Preserve the winning plan")
            self.assertEqual(
                [item.kind for item in stored.task_effect_ledger],
                ["director_intent", "director_observation"],
            )
            self.assertEqual(
                sum(engine._status_post.await_count for engine in engines),
                1,
            )

    async def test_paid_director_plan_is_reserved_once_and_survives_replan_reset(self):
        class PlanningDirector:
            def __init__(self):
                self.calls = 0

            async def plan(self, record: TaskRecord) -> DirectorPlan:
                self.calls += 1
                return DirectorPlan(
                    project="demo",
                    normalized_outcome="Choose one bounded option",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    owner_question="Which bounded option should continue?",
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.OWNER,
                            task="Choose one bounded option",
                            acceptance=["Owner supplies the choice"],
                        )
                    ],
                    final_acceptance=["Choice recorded"],
                )

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                openai_plan_estimated_cost_usd=0.1,
                project_allowlist=("demo",),
                sqlite_path=database,
            )
            task = TaskRecord(
                task_id="DIR-DIRECTOR-LEDGER",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-director-ledger",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="PRIVATE_PROMPT_MARKER choose one bounded option",
                ),
            )
            store = TaskStore(database)
            store.create(task)
            director = PlanningDirector()
            engine = OrchestrationEngine(settings, store)
            engine.director = director

            await engine.run(task.task_id)
            stored = store.get(task.task_id)
            self.assertEqual(director.calls, 1)
            self.assertEqual(
                [item.kind for item in stored.task_effect_ledger],
                ["director_intent", "director_observation"],
            )
            self.assertEqual(stored.cumulative_estimated_cost_usd, 0.1)
            self.assertEqual(stored.cumulative_effect_hops, 1)
            self.assertNotIn(
                "PRIVATE_PROMPT_MARKER",
                json.dumps(
                    [item.model_dump(mode="json") for item in stored.task_effect_ledger]
                ),
            )

            reset_for_owner_clarification(stored)
            store.save(stored)
            restarted = TaskStore(database).get(task.task_id)
            self.assertEqual(len(restarted.task_effect_ledger), 2)
            self.assertEqual(restarted.cumulative_estimated_cost_usd, 0.1)
            self.assertEqual(restarted.cumulative_effect_hops, 1)

    async def test_ambiguous_director_plan_is_not_repeated_after_restart(self):
        class AmbiguousDirector:
            def __init__(self):
                self.calls = 0

            async def plan(self, record: TaskRecord) -> DirectorPlan:
                self.calls += 1
                raise httpx.TimeoutError("response outcome unknown")

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                openai_plan_estimated_cost_usd=0.2,
                sqlite_path=database,
            )
            task = TaskRecord(
                task_id="DIR-DIRECTOR-AMBIGUOUS",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-director-ambiguous",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Create one bounded plan",
                ),
            )
            store = TaskStore(database)
            store.create(task)
            director = AmbiguousDirector()
            engine = OrchestrationEngine(settings, store)
            engine.director = director

            await engine.run(task.task_id)
            await OrchestrationEngine(settings, TaskStore(database)).run(task.task_id)

            stored = TaskStore(database).get(task.task_id)
            self.assertEqual(director.calls, 1)
            self.assertEqual(stored.status, TaskStatus.FAILED)
            self.assertEqual(
                [item.outcome for item in stored.task_effect_ledger],
                ["reserved", "ambiguous"],
            )
            self.assertEqual(stored.cumulative_estimated_cost_usd, 0.2)
            self.assertEqual(stored.cumulative_effect_hops, 1)

    async def test_interactions_non_success_terminal_states_fail_without_polling(self):
        class NeverPollAdapter:
            async def poll(self, run: ProviderRun) -> ProviderRun:
                raise AssertionError("Terminal Interactions status must not be polled")

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            engine = OrchestrationEngine(
                replace(Settings(), sqlite_path=database),
                TaskStore(database),
            )
            for status in ("requires_action", "budget_exceeded"):
                with self.subTest(status=status):
                    run = ProviderRun(
                        provider=ProviderName.GEMINI,
                        run_id=f"interaction-{status}",
                        status=status,
                    )
                    with self.assertRaisesRegex(ProviderError, status):
                        await engine._wait(NeverPollAdapter(), run)

    async def test_cursor_exact_sha_review_reaches_deterministic_merge_after_acceptance(self):
        base_sha = "d" * 40
        head_sha = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                cursor_api_key="test-cursor",
                project_allowlist=("edge-oracle",),
                repository_allowlist=("edwardjones3976/edge_engine",),
                allowed_starting_refs=("main",),
                sqlite_path=database,
            )
            implementation = Assignment(
                sequence=1,
                agent=ProviderName.CLAUDE,
                task="Implement the bounded Oracle change.",
                acceptance=["Create the isolated implementation."],
                repository="edwardjones3976/edge_engine",
                starting_ref="main",
            )
            assignment = Assignment(
                sequence=2,
                agent=ProviderName.CURSOR,
                task=f"Independently review PR 23 at base {base_sha} and head {head_sha}",
                acceptance=[f"Confirm exact base {base_sha} and head {head_sha}"],
                repository="edwardjones3976/edge_engine",
                starting_ref="main",
                depends_on=[1],
            )
            payload = {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": "cursor-artifact",
                "assignment_id": "DIR-CURSOR-MERGE:2",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 23,
                "base_ref": "main",
                "base_sha": base_sha,
                "head_ref": "oracle/revival-phase-1",
                "head_sha": head_sha,
                "implementation_provider": "claude",
                "implementation_actor": "claude-agent",
                "reviewer_provider": "cursor",
                "review_run_id": "cursor-review-run",
                "verdict": "ACCEPT",
                "summary": "Independent exact-SHA review accepted.",
                "evidence": ["Focused tests passed."],
                "acceptance_checks": ["test"],
                "rollback_instructions": "Revert the merge commit and restore the previous main ref.",
                "reviewed_at": "2026-08-16T00:00:00Z",
            }
            run = ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=2,
                run_id="cursor-review-run",
                status="FINISHED",
                evidence_source="provider_authenticated",
                result=json.dumps(payload),
            )
            record = TaskRecord(
                task_id="DIR-CURSOR-MERGE",
                status=TaskStatus.RUNNING,
                intake=SlackIntake(
                    source_event_id="evt-cursor-merge",
                    channel_id="C123",
                    thread_ts="123.45",
                    user_id="U123",
                    text="DIRECTOR Project: edge-oracle Outcome: exact-SHA review",
                ),
                plan=DirectorPlan(
                    project="edge-oracle",
                    normalized_outcome="Review and conditionally merge one Oracle PR.",
                    risk_level=RiskLevel.MEDIUM,
                    approval_required=False,
                    max_hops=2,
                    max_revisions=0,
                    assignments=[implementation, assignment],
                    final_acceptance=["Exact-SHA review accepted."],
                ),
                provider_runs=[run],
                assignment_outcomes=[AssignmentOutcome(
                    assignment_sequence=1,
                    provider=ProviderName.CLAUDE,
                    run_id="claude-implementation-run",
                    result="Implementation completed on the isolated branch.",
                    review_summary="OpenAI accepted the implementation evidence.",
                )],
                next_assignment_index=1,
            )
            store = TaskStore(database)
            store.create(record)

            class RecordingMergeService:
                def __init__(self):
                    self.policy = SimpleNamespace(
                        enabled=True,
                        kill_switch=False,
                        review_secret="test-review-secret",
                    )
                    self.artifact = None
                    self.calls = 0
                    self.store = SimpleNamespace(
                        attempt_recovery_state=lambda artifact_id: (
                            "RECOVERABLE",
                            None,
                        )
                    )

                def process(self, artifact):
                    self.calls += 1
                    self.artifact = artifact
                    if self.calls == 1:
                        return OracleMergeResult(
                            artifact_id=artifact.artifact_id,
                            artifact_digest=artifact.digest(),
                            status=OracleMergeStatus.ERROR,
                            repository=artifact.repository,
                            pull_number=artifact.pull_number,
                            reviewed_sha=artifact.head_sha,
                            reasons=["Transient exact-SHA evidence read failed"],
                        )
                    return OracleMergeResult(
                        artifact_id=artifact.artifact_id,
                        artifact_digest=artifact.digest(),
                        status=OracleMergeStatus.MERGED,
                        repository=artifact.repository,
                        pull_number=artifact.pull_number,
                        reviewed_sha=artifact.head_sha,
                        merge_commit_sha="c" * 40,
                        actor="github-app-installation:test",
                    )

            merge_service = RecordingMergeService()
            engine = OrchestrationEngine(settings, store, merge_service)
            engine.director = AcceptingDirector()
            engine.providers[ProviderName.CURSOR] = NoRedispatchAdapter()

            await engine.run(record.task_id)

            stopped = store.get(record.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertIsNotNone(stopped.oracle_recovery)
            self.assertTrue(stopped.oracle_recovery.retryable)
            apply_oracle_recovery(
                stopped,
                OracleRecoveryApproval(
                    owner_user_id="U0B9UJ86D27",
                    decision_digest=stopped.oracle_recovery.decision_digest,
                    action="retry_recoverable",
                    note="Retry the unchanged recoverable exact-SHA artifact",
                ),
                oracle_attempt_status="RECOVERABLE",
            )
            store.save(stopped)

            class NoSecondReview:
                async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
                    raise AssertionError("Oracle recovery must reuse the accepted exact-run review")

            engine.director = NoSecondReview()
            await engine.run(record.task_id)

            completed = store.get(record.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE)
            self.assertEqual(merge_service.calls, 2)
            self.assertEqual(merge_service.artifact.reviewer_provider, "cursor")
            self.assertTrue(merge_service.artifact.signature_is_valid("test-review-secret"))
            self.assertEqual(
                [
                    (item.kind, item.outcome)
                    for item in completed.task_effect_ledger
                    if item.effect_key
                    == next(
                        effect.effect_key
                        for effect in completed.task_effect_ledger
                        if effect.kind == "oracle_merge_intent"
                    )
                ],
                [
                    ("oracle_merge_intent", "reserved"),
                    ("oracle_merge_observation", "stopped"),
                    ("oracle_merge_observation", "merged"),
                ],
            )

    async def test_manual_handoff_stays_eligible_for_the_next_assignment(self):
        """An owner-recovered ordinary handoff is not an exact-SHA review artifact.

        It must be classified as non-review at the merge hook so the dependent
        assignment still becomes eligible, instead of failing closed on the
        provider-status check that only claimed review artifacts must satisfy.
        """

        class DispatchingAdapter:
            def __init__(self):
                self.dispatch_count = 0

            async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
                self.dispatch_count += 1
                return ProviderRun(
                    provider=ProviderName.CURSOR,
                    assignment_sequence=assignment.sequence,
                    run_id=f"cursor-run-{assignment.sequence}",
                    status="FINISHED",
                    evidence_source="provider_authenticated",
                    result="Independent review evidence recorded.",
                )

            async def poll(self, run: ProviderRun) -> ProviderRun:
                raise AssertionError("A terminal provider run must not be polled")

        class RefusingMergeService:
            def __init__(self):
                self.policy = SimpleNamespace(
                    enabled=True,
                    kill_switch=False,
                    review_secret="test-review-secret",
                )

            def process(self, artifact):
                raise AssertionError("An ordinary handoff must never reach the merge service")

        for result in (
            "Bounded implementation-owner handoff. No merge authority claimed.",
            json.dumps({"status": "complete", "evidence": ["read-only"]}),
        ):
            with self.subTest(result=result):
                with tempfile.TemporaryDirectory() as directory:
                    database = str(Path(directory) / "tasks.sqlite3")
                    settings = replace(
                        Settings(),
                        allow_paid_runs=True,
                        openai_api_key="test-openai",
                        cursor_api_key="test-cursor",
                        claude_routine_url="https://example.invalid/routine",
                        claude_routine_token="test-claude-token",
                        claude_callback_secret="test-callback-secret",
                        project_allowlist=("edge-oracle",),
                        repository_allowlist=("edwardjones3976/edge_engine",),
                        allowed_starting_refs=("main",),
                        max_agent_hops=2,
                        provider_poll_seconds=0,
                        provider_timeout_seconds=1,
                        sqlite_path=database,
                    )
                    implementation = Assignment(
                        sequence=1,
                        agent=ProviderName.CLAUDE,
                        task="Implement the bounded Oracle change.",
                        acceptance=["Create the isolated implementation."],
                        repository="edwardjones3976/edge_engine",
                        starting_ref="main",
                    )
                    review = Assignment(
                        sequence=2,
                        agent=ProviderName.CURSOR,
                        task="Independently review the recorded implementation evidence.",
                        acceptance=["Report independent review evidence."],
                        repository="edwardjones3976/edge_engine",
                        starting_ref="main",
                        depends_on=[1],
                    )
                    record = TaskRecord(
                        task_id="DIR-MANUAL-HANDOFF",
                        status=TaskStatus.RUNNING,
                        intake=SlackIntake(
                            source_event_id="evt-manual-handoff",
                            channel_id="C123",
                            thread_ts="123.45",
                            user_id="U123",
                            text="DIRECTOR Project: edge-oracle Outcome: manual handoff recovery",
                        ),
                        plan=DirectorPlan(
                            project="edge-oracle",
                            normalized_outcome="Continue after an owner-recovered handoff.",
                            risk_level=RiskLevel.LOW,
                            approval_required=False,
                            max_hops=2,
                            max_revisions=0,
                            assignments=[implementation, review],
                            final_acceptance=["Both assignments are accepted."],
                        ),
                        provider_runs=[
                            ProviderRun(
                                provider=ProviderName.CLAUDE,
                                assignment_sequence=1,
                                run_id="claude-manual-run",
                                status="FINISHED",
                                evidence_source="owner_recovery",
                                result=result,
                            )
                        ],
                    )
                    store = TaskStore(database)
                    store.create(record)

                    adapter = DispatchingAdapter()
                    engine = OrchestrationEngine(settings, store, RefusingMergeService())
                    engine.director = AcceptingDirector()
                    engine.providers[ProviderName.CURSOR] = adapter
                    engine.providers[ProviderName.CLAUDE] = NoRedispatchAdapter()

                    await engine.run(record.task_id)

                    completed = store.get(record.task_id)
                    self.assertEqual(completed.status, TaskStatus.DONE)
                    self.assertNotIn("failed closed", completed.message or "")
                    self.assertEqual(
                        [outcome.assignment_sequence for outcome in completed.assignment_outcomes],
                        [1, 2],
                    )
                    self.assertEqual(adapter.dispatch_count, 1)

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
                approved_assignment_digests={1: assignment_approval_digest(assignment)},
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

    async def test_ambiguous_revision_dispatch_stops_for_reconciliation_without_counting_revision(self):
        class RevisionDirector:
            async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
                return ReviewDecision(
                    verdict=ReviewVerdict.REVISE,
                    summary="Revision needed.",
                    revision_prompt="Add evidence.",
                )

        class AmbiguousRevisionAdapter:
            async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
                raise AssertionError("Recorded original run must not be redispatched")

            async def poll(self, run: ProviderRun) -> ProviderRun:
                raise AssertionError("Terminal original run must not be polled")

            async def revise(
                self,
                run: ProviderRun,
                prompt: str,
                *,
                assignment: Assignment,
                context: str,
            ) -> ProviderRun:
                request = httpx.Request("POST", "https://cursor.invalid/v1/agents/run")
                raise httpx.HTTPStatusError(
                    "provider returned 503 after submission",
                    request=request,
                    response=httpx.Response(503, request=request),
                )

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            store = TaskStore(database)
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                cursor_api_key="test-cursor",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=database,
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="Implement the bounded change",
                acceptance=["Return evidence"],
                repository="example/demo",
            )
            record = TaskRecord(
                task_id="DIR-AMBIGUOUS-REVISION",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-ambiguous-revision",
                    channel_id="C123",
                    thread_ts="123.45",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: bounded change",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="bounded change",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=2,
                    max_revisions=1,
                    assignments=[assignment],
                    final_acceptance=["Evidence accepted"],
                ),
                provider_runs=[
                    ProviderRun(
                        provider=ProviderName.CURSOR,
                        assignment_sequence=1,
                        run_id="cursor-original",
                        session_id="cursor-session",
                        status="FINISHED",
                        result="Incomplete evidence",
                        evidence_source="provider_authenticated",
                    )
                ],
            )
            store.create(record)
            engine = OrchestrationEngine(settings, store)
            engine.providers[ProviderName.CURSOR] = AmbiguousRevisionAdapter()
            engine.director = RevisionDirector()

            await engine.run(record.task_id)

            stopped = store.get(record.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.revision_count, 0)
            self.assertEqual(len(stopped.provider_runs), 1)
            self.assertEqual(len(stopped.provider_contact_attempts), 1)
            self.assertEqual(stopped.provider_contact_attempts[0].phase, "revision")
            self.assertEqual(
                stopped.provider_contact_attempts[0].outcome, "ambiguous_dispatch"
            )
            self.assertIn("reconciliation is required", stopped.message or "")

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
                approved_assignment_digests={
                    2: assignment_approval_digest(plan.assignments[1])
                },
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

