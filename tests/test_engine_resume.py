# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:51 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:38 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 11:58 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:37 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:55 UTC | BEGIN]]
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
    record_task_effect_observation,
    reset_for_owner_clarification,
    record_provider_effect_observation,
    reserve_task_effect,
    task_effect_key,
)
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import (
    CommunicationEvidenceFailure,
    InteractionProvider,
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    canonical_content_digest,
    require_verified_result,
    start_interaction,
    transition_interaction,
)
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


def _provider_transport(provider: ProviderName) -> ProviderTransport:
    return {
        ProviderName.OPENAI: ProviderTransport.OPENAI_RESPONSES_API,
        ProviderName.CLAUDE: ProviderTransport.CLAUDE_ROUTINE,
        ProviderName.CURSOR: ProviderTransport.CURSOR_AGENTS_API,
        ProviderName.GEMINI: ProviderTransport.GEMINI_INTERACTIONS_API,
        ProviderName.PERPLEXITY: ProviderTransport.PERPLEXITY_API,
    }[provider]


def _seed_verified_interaction(
    record: TaskRecord,
    *,
    provider: ProviderName,
    phase: ProviderInteractionPhase,
    result: str,
    assignment_sequence: int | None = None,
    interaction_id: str | None = None,
    effect_key: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
) -> tuple[str, str]:
    """Install immutable simulation evidence for a deliberately pre-seeded fixture."""

    interaction_id = interaction_id or (
        f"test:{record.task_id}:{provider.value}:{phase.value}:"
        f"{assignment_sequence or 0}:{len(record.provider_interaction_ledger)}"
    )
    effect_key = effect_key or hashlib.sha256(interaction_id.encode("utf-8")).hexdigest()
    drafted_evidence = build_transport_evidence(
        transport=_provider_transport(provider),
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
        transport_evidence=drafted_evidence,
        effect_key=effect_key,
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
    verified_evidence = build_transport_evidence(
        transport=_provider_transport(provider),
        source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
        execution_mode="simulation",
        idempotency_key=interaction_id,
        provider_run_id=run_id,
        provider_session_id=session_id,
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
    return interaction_id, event.event_digest


def _seed_verified_plan(record: TaskRecord) -> None:
    if record.plan is None:
        raise AssertionError("test fixture must install a plan before its evidence")
    plan_result = record.plan.model_dump_json()
    effect_key = task_effect_key(
        record,
        phase="plan",
        identity={"materialized_plan": json.loads(plan_result)},
    )
    reserve_task_effect(
        record,
        effect_key=effect_key,
        kind="director_intent",
        phase="plan",
        provider=ProviderName.OPENAI,
        estimated_cost_usd=0,
        hop_units=1,
    )
    interaction_id, verified_event_digest = _seed_verified_interaction(
        record,
        provider=ProviderName.OPENAI,
        phase=ProviderInteractionPhase.PLAN,
        result=plan_result,
        effect_key=effect_key,
    )
    record.plan_interaction_id = interaction_id
    materialization_binding = OrchestrationEngine._plan_materialization_binding(
        record,
        verified_event_digest=verified_event_digest,
        verified_result_digest=canonical_content_digest(plan_result),
        effect_key=effect_key,
    )
    record_task_effect_observation(
        record,
        effect_key=effect_key,
        intent_kind="director_intent",
        observation_kind="director_observation",
        phase="plan",
        outcome="completed",
        run_id=materialization_binding,
    )


def _seed_verified_run(record: TaskRecord, run: ProviderRun) -> None:
    if not run.result:
        raise AssertionError("test fixture run must contain a result")
    interaction_id, _ = _seed_verified_interaction(
        record,
        provider=run.provider,
        phase=ProviderInteractionPhase.DISPATCH,
        assignment_sequence=run.assignment_sequence,
        result=run.result,
        interaction_id=f"test:{record.task_id}:run:{run.run_id}",
        run_id=run.run_id,
        session_id=run.session_id,
    )
    run.interaction_id = interaction_id


def _seed_unknown_owner_run(record: TaskRecord, run: ProviderRun) -> None:
    """Bind an owner-recovered result to UNKNOWN, never VERIFIED, evidence."""

    interaction_id = f"test:{record.task_id}:owner-recovery:{run.run_id}"
    effect_key = hashlib.sha256(interaction_id.encode("utf-8")).hexdigest()
    drafted_evidence = build_transport_evidence(
        transport=_provider_transport(run.provider),
        source=TransportEvidenceSource.BACKEND_OBSERVATION,
        execution_mode="simulation",
        idempotency_key=effect_key,
    )
    event = start_interaction(
        task_id=record.task_id,
        interaction_id=interaction_id,
        plan_generation=record.plan_generation,
        assignment_sequence=run.assignment_sequence,
        provider=InteractionProvider(run.provider.value),
        phase=ProviderInteractionPhase.CALLBACK,
        attempt=1,
        transport_evidence=drafted_evidence,
        effect_key=effect_key,
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
    owner_evidence = build_transport_evidence(
        transport=ProviderTransport.MANUAL_OWNER,
        source=TransportEvidenceSource.OWNER_ATTESTED,
        execution_mode="simulation",
        idempotency_key=interaction_id,
        result=run.result,
        native_status=run.status,
        error_code="OWNER_RECOVERY_UNVERIFIED",
        ambiguity_reason="manual recovery cannot authenticate provider completion",
    )
    event = transition_interaction(
        record.provider_interaction_ledger,
        interaction_id=interaction_id,
        next_state=ProviderInteractionState.UNKNOWN,
        transport_evidence=owner_evidence,
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, event
    )
    run.interaction_id = interaction_id


def _seed_verified_outcome(record: TaskRecord, outcome: AssignmentOutcome) -> None:
    interaction_id, event_digest = _seed_verified_interaction(
        record,
        provider=outcome.provider,
        phase=ProviderInteractionPhase.DISPATCH,
        assignment_sequence=outcome.assignment_sequence,
        result=outcome.result,
        interaction_id=f"test:{record.task_id}:outcome:{outcome.run_id}",
    )
    outcome.interaction_id = interaction_id
    outcome.verified_interaction_event_digest = event_digest
    outcome.provider_result_digest = canonical_content_digest(outcome.result)
    review_result = ReviewDecision(
        verdict=ReviewVerdict.ACCEPT,
        summary=outcome.review_summary,
        evidence=outcome.evidence,
    ).model_dump_json()
    review_interaction_id, review_event_digest = _seed_verified_interaction(
        record,
        provider=ProviderName.OPENAI,
        phase=ProviderInteractionPhase.ACCEPTANCE_REVIEW,
        result=review_result,
        interaction_id=f"test:{record.task_id}:outcome-review:{outcome.run_id}",
    )
    outcome.director_review_interaction_id = review_interaction_id
    outcome.verified_director_review_event_digest = review_event_digest
    outcome.director_review_result_digest = canonical_content_digest(review_result)


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
            _seed_verified_plan(record)
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

    async def test_poll_rejects_provider_run_correlation_drift(self):
        class DriftingPollAdapter:
            def __init__(self, changes: dict[str, object]):
                self.changes = changes

            async def poll(self, run: ProviderRun) -> ProviderRun:
                return run.model_copy(
                    update={
                        "status": "completed",
                        "result": "authenticated terminal result",
                        **self.changes,
                    }
                )

        with tempfile.TemporaryDirectory() as directory:
            engine = OrchestrationEngine(
                replace(
                    Settings(),
                    sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                    provider_poll_seconds=0,
                    provider_timeout_seconds=1,
                ),
                TaskStore(str(Path(directory) / "tasks.sqlite3")),
            )
            original = ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=3,
                interaction_id="backend-owned-interaction",
                run_id="cursor-run-1",
                session_id="cursor-session-1",
                status="RUNNING",
            )
            drift_cases = {
                "run_id": {"run_id": "cursor-run-2"},
                "provider": {"provider": ProviderName.CLAUDE},
                "session_id": {"session_id": "cursor-session-2"},
            }

            for field, changes in drift_cases.items():
                with self.subTest(field=field):
                    with self.assertRaises(CommunicationEvidenceFailure):
                        await engine._wait(DriftingPollAdapter(changes), original)

    async def test_poll_restores_backend_owned_internal_bindings(self):
        class SparsePollAdapter:
            async def poll(self, run: ProviderRun) -> ProviderRun:
                return ProviderRun(
                    provider=run.provider,
                    run_id=run.run_id,
                    session_id=run.session_id,
                    status="completed",
                    result="authenticated terminal result",
                )

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            engine = OrchestrationEngine(
                replace(
                    Settings(),
                    sqlite_path=database,
                    provider_poll_seconds=0,
                    provider_timeout_seconds=1,
                ),
                TaskStore(database),
            )
            original = ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=3,
                interaction_id="backend-owned-interaction",
                run_id="cursor-run-1",
                session_id="cursor-session-1",
                status="RUNNING",
            )

            polled = await engine._wait(SparsePollAdapter(), original)

            self.assertEqual(polled.interaction_id, original.interaction_id)
            self.assertEqual(polled.assignment_sequence, original.assignment_sequence)

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
            _seed_verified_plan(record)
            _seed_verified_run(record, run)
            _seed_verified_outcome(record, record.assignment_outcomes[0])
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

    async def test_manual_handoff_recovery_cannot_authenticate_provider_result(self):
        """Owner recovery remains UNKNOWN and cannot unlock normal acceptance."""

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
                    _seed_verified_plan(record)
                    _seed_unknown_owner_run(record, record.provider_runs[0])
                    store.create(record)

                    adapter = DispatchingAdapter()
                    engine = OrchestrationEngine(settings, store, RefusingMergeService())
                    engine.director = AcceptingDirector()
                    engine.providers[ProviderName.CURSOR] = adapter
                    engine.providers[ProviderName.CLAUDE] = NoRedispatchAdapter()

                    await engine.run(record.task_id)

                    stopped = store.get(record.task_id)
                    self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
                    self.assertEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")
                    self.assertIn("VERIFIED", stopped.error or "")
                    self.assertEqual(stopped.assignment_outcomes, [])
                    self.assertEqual(adapter.dispatch_count, 0)

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
            _seed_verified_plan(record)
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
            _seed_verified_plan(record)
            _seed_verified_run(record, record.provider_runs[0])
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
            _seed_verified_plan(record)
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
            _seed_verified_plan(record)
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
            _seed_verified_plan(record)
            _seed_verified_run(record, record.provider_runs[0])
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

    async def test_accepted_outcome_persists_and_revalidates_exact_director_review(self):
        for changed_review in (False, True):
            with self.subTest(changed_review=changed_review), tempfile.TemporaryDirectory() as directory:
                database = str(Path(directory) / "tasks.sqlite3")
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
                    sqlite_path=database,
                )
                assignment = Assignment(
                    sequence=1,
                    agent=ProviderName.CURSOR,
                    task="Produce restart-safe acceptance evidence",
                    acceptance=["Evidence exists"],
                    repository="example/demo",
                )
                plan = DirectorPlan(
                    project="demo",
                    normalized_outcome="verify durable acceptance evidence",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[assignment],
                    final_acceptance=["Assignment accepted"],
                )
                run = ProviderRun(
                    provider=ProviderName.CURSOR,
                    assignment_sequence=1,
                    run_id="durable-review-run",
                    session_id="durable-review-session",
                    status="FINISHED",
                    result="Exact provider evidence",
                    evidence_source="provider_authenticated",
                )
                decision = ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Exact Director acceptance",
                    evidence=["Exact provider evidence"],
                )
                record = TaskRecord(
                    task_id=(
                        "DIR-DURABLE-REVIEW-CHANGED"
                        if changed_review
                        else "DIR-DURABLE-REVIEW-EXACT"
                    ),
                    status=TaskStatus.RUNNING,
                    intake=SlackIntake(
                        source_event_id=(
                            "evt-durable-review-changed"
                            if changed_review
                            else "evt-durable-review-exact"
                        ),
                        channel_id="C123",
                        thread_ts="123.48",
                        user_id="U123",
                        text="DIRECTOR Project: demo Outcome: durable review evidence",
                    ),
                    plan=plan,
                    provider_runs=[run],
                    review=decision,
                )
                _seed_verified_plan(record)
                _seed_verified_run(record, run)
                review_interaction_id, review_event_digest = _seed_verified_interaction(
                    record,
                    provider=ProviderName.OPENAI,
                    phase=ProviderInteractionPhase.ACCEPTANCE_REVIEW,
                    result=decision.model_dump_json(),
                    interaction_id=f"test:{record.task_id}:director-review",
                )
                record.review_interaction_id = review_interaction_id
                store = TaskStore(database)
                engine = OrchestrationEngine(settings, store)
                engine._record_outcome(record, assignment, run, decision)
                store.create(record)

                restarted = store.get(record.task_id)
                outcome = restarted.assignment_outcomes[0]
                self.assertEqual(
                    outcome.director_review_interaction_id, review_interaction_id
                )
                self.assertEqual(
                    outcome.verified_director_review_event_digest,
                    review_event_digest,
                )
                self.assertEqual(
                    outcome.director_review_result_digest,
                    canonical_content_digest(decision.model_dump_json()),
                )
                if changed_review:
                    restarted.review = decision.model_copy(
                        update={"summary": "Different accepted decision"}
                    )
                    store.save(restarted)

                resumed_engine = OrchestrationEngine(settings, TaskStore(database))
                adapter = NoRedispatchAdapter()
                resumed_engine.providers[ProviderName.CURSOR] = adapter
                await resumed_engine.run(record.task_id)

                completed = TaskStore(database).get(record.task_id)
                self.assertEqual(adapter.dispatch_count, 0)
                if changed_review:
                    self.assertEqual(
                        completed.status, TaskStatus.OWNER_DECISION_REQUIRED
                    )
                    self.assertEqual(
                        completed.message, CommunicationEvidenceFailure.code
                    )
                else:
                    self.assertEqual(completed.status, TaskStatus.DONE)

    def test_oversized_provider_result_cannot_create_inconsistent_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="Produce bounded evidence",
                acceptance=["Evidence is durably bound"],
                repository="example/demo",
            )
            oversized_result = "x" * 50_001
            run = ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=1,
                run_id="oversized-provider-result",
                status="FINISHED",
                result=oversized_result,
                evidence_source="provider_authenticated",
            )
            decision = ReviewDecision(
                verdict=ReviewVerdict.ACCEPT,
                summary="Director accepted the reported evidence",
                evidence=["Provider reported completion"],
            )
            record = TaskRecord(
                task_id="DIR-OVERSIZED-PROVIDER-RESULT",
                status=TaskStatus.REVIEWING,
                intake=SlackIntake(
                    source_event_id="evt-oversized-provider-result",
                    channel_id="C123",
                    thread_ts="123.49",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: bounded evidence",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="bound provider evidence",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[assignment],
                    final_acceptance=["Assignment accepted"],
                ),
                provider_runs=[run],
                review=decision,
            )
            _seed_verified_plan(record)
            _seed_verified_run(record, run)
            review_interaction_id, _ = _seed_verified_interaction(
                record,
                provider=ProviderName.OPENAI,
                phase=ProviderInteractionPhase.ACCEPTANCE_REVIEW,
                result=decision.model_dump_json(),
                interaction_id=f"test:{record.task_id}:director-review",
            )
            record.review_interaction_id = review_interaction_id
            engine = OrchestrationEngine(
                replace(Settings(), sqlite_path=database), TaskStore(database)
            )

            try:
                engine._record_outcome(record, assignment, run, decision)
            except (CommunicationEvidenceFailure, ValueError):
                self.assertEqual(record.assignment_outcomes, [])
            else:
                outcome = record.assignment_outcomes[0]
                self.assertLessEqual(len(outcome.result), 50_000)
                self.assertEqual(
                    outcome.provider_result_digest,
                    canonical_content_digest(outcome.result),
                )
                verified = require_verified_result(
                    record.provider_interaction_ledger,
                    interaction_id=outcome.interaction_id,
                    result=outcome.result,
                )
                self.assertEqual(
                    outcome.verified_interaction_event_digest,
                    verified.event_digest,
                )

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
            _seed_verified_plan(record)
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

    async def test_max_revisions_runs_every_bounded_revision_before_acceptance(self):
        class Director:
            def __init__(self):
                self.calls = 0

            async def review(self, record, result):
                self.calls += 1
                if self.calls <= 2:
                    return ReviewDecision(
                        verdict=ReviewVerdict.REVISE,
                        summary="More evidence required.",
                        revision_prompt=f"Add evidence round {self.calls}.",
                    )
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Both revisions passed.",
                    evidence=[result],
                )

        class Adapter:
            def __init__(self):
                self.revisions = 0

            async def dispatch(self, assignment, context):
                return ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id="original",
                    status="completed",
                    result="initial",
                )

            async def poll(self, run):
                raise AssertionError("terminal run must not be polled")

            async def revise(self, run, prompt, *, assignment, context):
                self.revisions += 1
                return ProviderRun(
                    provider=ProviderName.CLAUDE,
                    run_id=f"revision-{self.revisions}",
                    status="completed",
                    result=f"revised-{self.revisions}",
                )

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            store = TaskStore(database)
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.example/routine",
                claude_routine_token="test-claude",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=6,
                max_revisions=2,
                sqlite_path=database,
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CLAUDE,
                task="Iterate twice",
                acceptance=["Return final evidence"],
                repository="example/demo",
            )
            record = TaskRecord(
                task_id="DIR-TWO-REVISIONS",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-two-revisions",
                    channel_id="C123",
                    thread_ts="123.45",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: iterate twice",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="iterate twice",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=3,
                    max_revisions=2,
                    assignments=[assignment],
                    final_acceptance=["Accepted"],
                ),
            )
            _seed_verified_plan(record)
            store.create(record)
            engine = OrchestrationEngine(settings, store)
            adapter = Adapter()
            director = Director()
            engine.providers[ProviderName.CLAUDE] = adapter
            engine.director = director

            await engine.run(record.task_id)

            completed = store.get(record.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE)
            self.assertEqual(adapter.revisions, 2)
            self.assertEqual(director.calls, 3)
            self.assertEqual(
                [item.run_id for item in completed.provider_runs],
                ["original", "revision-1", "revision-2"],
            )

    async def test_perplexity_revision_requires_new_owner_approved_public_query(self):
        class Director:
            async def review(self, record, result):
                return ReviewDecision(
                    verdict=ReviewVerdict.REVISE,
                    summary="Research must be narrowed.",
                    revision_prompt="Private repository-derived feedback must not leave.",
                )

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            store = TaskStore(database)
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_mode="research_only",
                project_allowlist=("demo",),
                max_agent_hops=3,
                max_revisions=2,
                sqlite_path=database,
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.PERPLEXITY,
                task="Find public methodology sources",
                acceptance=["Return source URLs"],
                requires_approval=True,
                public_research_query="public calibration methodology evidence",
                estimated_cost_usd=0.05,
            )
            record = TaskRecord(
                task_id="DIR-PERPLEXITY-REFINEMENT",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-perplexity-refinement",
                    channel_id="C123",
                    thread_ts="123.45",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: research methodology",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="research methodology",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=2,
                    max_revisions=2,
                    assignments=[assignment],
                    final_acceptance=["Accepted"],
                ),
                approved_assignments=[1],
                approved_assignment_digests={1: assignment_approval_digest(assignment)},
                provider_runs=[
                    ProviderRun(
                        provider=ProviderName.PERPLEXITY,
                        assignment_sequence=1,
                        run_id="pplx-original",
                        status="completed",
                        result="public evidence packet",
                        evidence_source="provider_authenticated",
                    )
                ],
            )
            _seed_verified_plan(record)
            _seed_verified_run(record, record.provider_runs[0])
            store.create(record)
            engine = OrchestrationEngine(settings, store)
            engine.director = Director()
            engine.providers[ProviderName.PERPLEXITY] = NoRedispatchAdapter()

            await engine.run(record.task_id)

            stopped = store.get(record.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.revision_count, 0)
            self.assertEqual(len(stopped.provider_runs), 1)
            self.assertIn("new bounded public research query", stopped.message or "")
            self.assertNotIn("Private repository-derived", stopped.message or "")


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 09:55 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:37 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 11:58 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:38 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:51 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | END]]

