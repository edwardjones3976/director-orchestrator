# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:58 UTC | BEGIN]]
"""Cross-worker regressions for post-publication provider authority rebinding."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
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
from director_orchestrator.packet_contracts import load_json_document
from director_orchestrator.runtime_activation import RuntimeActivation
from director_orchestrator.store import TaskStore
from tests.plan_binding_helpers import seed_production_plan_materialization
from tests.test_engine_packet_runtime_routing import (
    RecordingResultConsumer,
    RecordingTransport,
    StubPacketTaskRouter,
)


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"


class PostPublicationPlanRevalidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        self.result = load_json_document(
            SCHEMAS / "model-result-packet-v1.example.json"
        )

    def _settings(self, **overrides) -> Settings:
        fields = {
            "sqlite_path": str(self.root / "director.sqlite3"),
            "langgraph_checkpoint_path": str(self.root / "shadow.sqlite3"),
            "langgraph_checkpoint_namespace": "post-publication-shadow-v1",
            "langgraph_runtime_checkpoint_path": str(self.root / "runtime.sqlite3"),
            "langgraph_runtime_ledger_path": str(self.root / "runtime-ledger.sqlite3"),
            "langgraph_runtime_checkpoint_namespace": "post-publication-runtime-v1",
            "langgraph_runtime_writer_owner_id": "langgraph-packet-runtime",
            "langgraph_runtime_writer_secret": "w" * 32,
            "repository_snapshot_root": str(self.root),
            "allow_paid_runs": True,
            "project_allowlist": ("demo",),
            "repository_allowlist": ("example/demo",),
            "allowed_starting_refs": ("main",),
            "claude_callback_secret": "callback-secret",
            "claude_routine_url": "https://claude.invalid/fire",
            "claude_routine_token": "test-token",
            "max_task_budget_usd": 1.0,
            "max_daily_provider_budget_usd": 5.0,
        }
        fields.update(overrides)
        return replace(Settings(), **fields)

    @staticmethod
    def _record(
        provider: ProviderName = ProviderName.CLAUDE,
    ) -> tuple[TaskRecord, Assignment]:
        assignment = Assignment(
            sequence=1,
            agent=provider,
            task="Execute only the authenticated plan",
            acceptance=["No stale plan reaches a provider"],
            repository="example/demo",
            estimated_cost_usd=0.1,
        )
        record = TaskRecord(
            task_id="DIR-POST-PUBLICATION-REBIND",
            status=TaskStatus.RUNNING,
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
            intake=SlackIntake(
                source_event_id="evt-post-publication-rebind",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: close the publication race",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="close the publication race",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[assignment],
                final_acceptance=["The provider sees only current authority"],
            ),
        )
        seed_production_plan_materialization(record)
        return record, assignment

    @staticmethod
    def _install_second_worker_mutation(
        engine: OrchestrationEngine, settings: Settings
    ) -> None:
        second_store = TaskStore(settings.sqlite_path)

        async def publish_then_mutate(record, assignment):
            current = second_store.get(record.task_id)
            current.plan.assignments[0] = current.plan.assignments[0].model_copy(
                update={"task": "second worker replaced the authenticated plan"}
            )
            second_store.save(current)
            return True

        engine._publish_delegation = AsyncMock(side_effect=publish_then_mutate)

    @staticmethod
    def _install_second_worker_version_bump(
        engine: OrchestrationEngine, settings: Settings
    ) -> None:
        second_store = TaskStore(settings.sqlite_path)

        async def publish_then_bump(record, assignment):
            current = second_store.get(record.task_id)
            current.message = "Second worker recorded a non-provider state update"
            second_store.save(current)
            return True

        engine._publish_delegation = AsyncMock(side_effect=publish_then_bump)

    @staticmethod
    def _assert_fail_closed(store: TaskStore) -> None:
        persisted = store.get("DIR-POST-PUBLICATION-REBIND")
        assert persisted is not None
        if persisted.status != TaskStatus.OWNER_DECISION_REQUIRED:
            raise AssertionError(persisted.status)
        if persisted.message != "COMMUNICATION_EVIDENCE_FAILURE":
            raise AssertionError(persisted.message)
        if "changed after collaboration publication" not in (persisted.error or ""):
            raise AssertionError(persisted.error)

    @staticmethod
    def _assert_failed_pre_submit(store: TaskStore) -> None:
        persisted = store.get("DIR-POST-PUBLICATION-REBIND")
        assert persisted is not None
        intents = [
            item
            for item in persisted.task_effect_ledger
            if item.kind == "provider_intent" and item.phase == "dispatch"
        ]
        if len(intents) != 1:
            raise AssertionError(intents)
        outcomes = [
            item.outcome
            for item in persisted.task_effect_ledger
            if item.kind == "provider_observation"
            and item.effect_key == intents[0].effect_key
        ]
        if outcomes != ["failed_pre_submit"]:
            raise AssertionError(outcomes)

    @staticmethod
    def _assert_preserved_ambiguity(store: TaskStore) -> None:
        persisted = store.get("DIR-POST-PUBLICATION-REBIND")
        assert persisted is not None
        intents = [
            item
            for item in persisted.task_effect_ledger
            if item.kind == "provider_intent" and item.phase == "dispatch"
        ]
        if len(intents) != 1:
            raise AssertionError(intents)
        outcomes = [
            item.outcome
            for item in persisted.task_effect_ledger
            if item.kind == "provider_observation"
            and item.effect_key == intents[0].effect_key
        ]
        if outcomes:
            raise AssertionError(outcomes)

    async def test_legacy_dispatch_reloads_after_slack_before_provider(self) -> None:
        settings = self._settings()
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store)
        adapter = AsyncMock()
        engine.providers[ProviderName.CLAUDE] = adapter
        record, assignment = self._record()
        store.create(record)
        self._install_second_worker_mutation(engine, settings)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        adapter.dispatch.assert_not_awaited()
        self._assert_fail_closed(store)
        self._assert_preserved_ambiguity(store)

    async def test_packet_dispatch_reloads_after_slack_before_provider(self) -> None:
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=RecordingResultConsumer(),
        )
        self.addCleanup(activation.close)
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store, runtime_activation=activation)
        record, assignment = self._record()
        store.create(record)
        self._install_second_worker_mutation(engine, settings)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        self.assertEqual(0, transport.sends)
        self._assert_fail_closed(store)
        self._assert_preserved_ambiguity(store)

    async def test_legacy_resume_reloads_after_slack_before_provider_poll(self) -> None:
        settings = self._settings()
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store)
        adapter = AsyncMock()
        engine.providers[ProviderName.CLAUDE] = adapter
        record, assignment = self._record()
        record.provider_runs.append(
            ProviderRun(
                provider=ProviderName.CLAUDE,
                run_id="existing-provider-run",
                status="running",
                assignment_sequence=assignment.sequence,
            )
        )
        store.create(record)
        self._install_second_worker_mutation(engine, settings)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        adapter.poll.assert_not_awaited()
        self._assert_fail_closed(store)

    async def test_parallel_dispatch_reloads_after_slack_before_provider(self) -> None:
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("cursor", transport)
        )
        settings = self._settings(
            cursor_api_key="test-cursor-key",
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            langgraph_parallel_external_dispatch_enabled=True,
            langgraph_parallel_kill_switch=False,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=RecordingResultConsumer(),
        )
        self.addCleanup(activation.close)
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store, runtime_activation=activation)
        record, _ = self._record(ProviderName.CURSOR)
        stored, _ = store.create(record)
        self._install_second_worker_mutation(engine, settings)

        self.assertFalse(await engine._dispatch_parallel_packet_batch(stored))

        self.assertEqual(1, engine._publish_delegation.await_count)
        self.assertEqual(0, transport.sends)
        self._assert_fail_closed(store)
        self._assert_preserved_ambiguity(store)

    async def test_legacy_rebind_failure_closes_proven_unsubmitted_effect(self) -> None:
        settings = self._settings()
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store)
        adapter = AsyncMock()
        engine.providers[ProviderName.CLAUDE] = adapter
        record, assignment = self._record()
        store.create(record)
        self._install_second_worker_version_bump(engine, settings)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        adapter.dispatch.assert_not_awaited()
        self._assert_fail_closed(store)
        self._assert_failed_pre_submit(store)

    async def test_packet_rebind_failure_closes_proven_unsubmitted_effect(self) -> None:
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=RecordingResultConsumer(),
        )
        self.addCleanup(activation.close)
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store, runtime_activation=activation)
        record, assignment = self._record()
        store.create(record)
        self._install_second_worker_version_bump(engine, settings)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        self.assertEqual(0, transport.sends)
        self._assert_fail_closed(store)
        self._assert_failed_pre_submit(store)

    async def test_parallel_rebind_failure_closes_proven_unsubmitted_effect(self) -> None:
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("cursor", transport)
        )
        settings = self._settings(
            cursor_api_key="test-cursor-key",
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            langgraph_parallel_external_dispatch_enabled=True,
            langgraph_parallel_kill_switch=False,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=RecordingResultConsumer(),
        )
        self.addCleanup(activation.close)
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store, runtime_activation=activation)
        record, _ = self._record(ProviderName.CURSOR)
        stored, _ = store.create(record)
        self._install_second_worker_version_bump(engine, settings)

        self.assertFalse(await engine._dispatch_parallel_packet_batch(stored))

        self.assertEqual(0, transport.sends)
        self._assert_fail_closed(store)
        self._assert_failed_pre_submit(store)

    async def test_accepted_recovery_reloads_before_collaboration_side_effect(self) -> None:
        settings = self._settings(cursor_api_key="test-cursor-key")
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store)
        record, assignment = self._record(ProviderName.CURSOR)
        record.assignment_outcomes.append(
            AssignmentOutcome(
                assignment_sequence=assignment.sequence,
                provider=assignment.agent,
                run_id="accepted-run",
                result="Accepted exact result",
                review_summary="Accepted exact review",
            )
        )
        record.provider_runs.append(
            ProviderRun(
                provider=assignment.agent,
                assignment_sequence=assignment.sequence,
                run_id="accepted-run",
                status="FINISHED",
                result="Accepted exact result",
            )
        )
        record.review = ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary="Accepted exact review",
            evidence=["Accepted exact result"],
        )
        store.create(record)
        self._install_second_worker_mutation(engine, settings)
        engine._record_and_publish_collaboration_turn = AsyncMock(return_value=True)
        engine._finalize_debate = AsyncMock(return_value=True)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        engine._record_and_publish_collaboration_turn.assert_not_awaited()
        engine._finalize_debate.assert_not_awaited()
        self._assert_fail_closed(store)


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:58 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:26 UTC | END]]
