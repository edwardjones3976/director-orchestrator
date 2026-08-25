# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 11:58 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
"""LIVE_SAFE routing: the coordinator executes the task it takes ownership of.

Surrendering legacy dispatch ownership to a coordinator that cannot convert an
intake task into a work packet would leave no engine executing anything. These
tests pin the two halves of that contract: ownership is surrendered *into* a real
`run_packet` execution, and a missing packet, authority snapshot, or v1 boundary
adapter fails closed explicitly instead of falling back to the legacy writer.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import AsyncMock, MagicMock, patch

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.langgraph_runtime import (
    PacketBoundaryAdapter,
    WriterOwnershipToken,
)
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    IntakeAuthentication,
    ProviderName,
    ProviderRun,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.packet_results import PacketResultProgression
from director_orchestrator.packet_contracts import load_json_document
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.parallel_dispatch import MemberState
from director_orchestrator.providers import ProviderDailyLimitExceeded
from director_orchestrator.runtime_activation import RuntimeActivation
from director_orchestrator.store import TaskStore
from tests.plan_binding_helpers import seed_production_plan_materialization


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"


class RecordingTransport:
    """The untrusted side of the v1 boundary; returns one canned result packet."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        self.result = copy.deepcopy(dict(result))
        self.sends = 0

    def send(self, work_packet_json: bytes, *, context) -> Mapping[str, Any]:
        self.sends += 1
        return copy.deepcopy(self.result)


class LateReadbackTransport(RecordingTransport):
    readback_packet: dict[str, Any] | None = None

    def send(self, work_packet_json: bytes, *, context) -> Mapping[str, Any]:
        self.sends += 1
        raise TimeoutError("provider completion arrived after packet timeout")

    async def authenticated_readback(
        self, work_packet_json: bytes, provider_run: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.readback_packet = json.loads(work_packet_json.decode("utf-8"))
        return copy.deepcopy(self.result)


class BlockingSyncLateReadbackTransport(LateReadbackTransport):
    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__(result)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.readback_calls = 0

    def authenticated_readback(
        self, work_packet_json: bytes, provider_run: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.readback_calls += 1
        self.entered.set()
        self.release.wait(timeout=5)
        return copy.deepcopy(self.result)


class RecordingResultConsumer:
    def __init__(self) -> None:
        self.calls = 0

    def consume(self, record, assignment, work_packet, outcome):
        self.calls += 1
        return PacketResultProgression(
            packet_status="SUCCEEDED",
            provider_run_recorded=False,
            requires_director_review=True,
            requires_revision=False,
            terminal_task_status=None,
        )


class StubPacketTaskRouter:
    """Produces the three artifacts `run_packet` requires, or raises."""

    def __init__(
        self,
        work_packet: Mapping[str, Any],
        adapter: PacketBoundaryAdapter | None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.packet = copy.deepcopy(dict(work_packet))
        self.adapter = adapter
        self.failure = failure
        self.token = WriterOwnershipToken.issue(
            task_id=self.packet["task_id"], owner_id="langgraph-packet-runtime"
        )

    def work_packet(self, record, assignment) -> Mapping[str, Any]:
        if self.failure is not None:
            raise self.failure
        return self.packet

    def idempotency_key(self, record, assignment) -> str:
        return str(self.packet["idempotency_key"])

    def validate_bound_packet(self, record, assignment, packet) -> Mapping[str, Any]:
        return copy.deepcopy(dict(packet))

    def writer_token(self, record) -> WriterOwnershipToken:
        return self.token

    def boundary_adapter(self, provider: str) -> PacketBoundaryAdapter:
        assert self.adapter is not None
        return self.adapter


class MultiPacketTaskRouter:
    def __init__(self, packets, adapters) -> None:
        self.packets = packets
        self.adapters = adapters
        task_id = next(iter(packets.values()))["task_id"]
        self.token = WriterOwnershipToken.issue(
            task_id=task_id, owner_id="langgraph-packet-runtime"
        )

    def work_packet(self, record, assignment):
        return copy.deepcopy(self.packets[assignment.sequence])

    def idempotency_key(self, record, assignment):
        return str(self.packets[assignment.sequence]["idempotency_key"])

    def validate_bound_packet(self, record, assignment, packet):
        return copy.deepcopy(dict(packet))

    def boundary_adapter(self, provider):
        return self.adapters[provider]

    def writer_token(self, record):
        return self.token


class PacketRuntimeRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        self.result = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")
        self.ledger_path = self.root / "runtime-ledger.sqlite3"

    def _settings(self, **overrides) -> Settings:
        fields = {
            "sqlite_path": str(self.root / "director.sqlite3"),
            "langgraph_checkpoint_path": str(self.root / "shadow.sqlite3"),
            "langgraph_checkpoint_namespace": "director-test-shadow-v1",
            "langgraph_runtime_checkpoint_path": str(self.root / "runtime.sqlite3"),
            "langgraph_runtime_ledger_path": str(self.ledger_path),
            "langgraph_runtime_checkpoint_namespace": "director-test-runtime-v1",
            "langgraph_runtime_writer_owner_id": "langgraph-packet-runtime",
            "langgraph_runtime_writer_secret": "w" * 32,
            "repository_snapshot_root": str(self.root),
            "allow_paid_runs": True,
            "project_allowlist": ("demo",),
            "repository_allowlist": ("example/demo",),
            "allowed_starting_refs": ("main",),
            "claude_callback_secret": "callback-secret",
        }
        fields.update(overrides)
        return replace(Settings(), **fields)

    def _record(
        self, *, estimated_cost_usd: float = 0
    ) -> tuple[TaskRecord, Assignment]:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Route one assignment through the packet coordinator",
            acceptance=["The coordinator owns dispatch"],
            repository="example/demo",
            estimated_cost_usd=estimated_cost_usd,
        )
        record = TaskRecord(
            task_id="DIR-PACKET-ROUTING",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-packet-routing",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: route through the coordinator",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="route through the coordinator",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[assignment],
                final_acceptance=["The coordinator executed the assignment"],
            ),
        )
        seed_production_plan_materialization(record)
        return record, assignment

    def _engine(self, settings: Settings, activation: RuntimeActivation | None):
        store = TaskStore(settings.sqlite_path)
        engine = OrchestrationEngine(settings, store, runtime_activation=activation)
        engine.notifier.post = AsyncMock(return_value=None)
        return engine, store

    def _ledger_status(self) -> str | None:
        if not self.ledger_path.exists():
            return None
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            row = connection.execute(
                "SELECT status FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (self.work["idempotency_key"],),
            ).fetchone()
        return None if row is None else str(row[0])

    def test_task_record_authentication_default_fails_closed(self):
        record = TaskRecord(
            task_id="DIR-DEFAULT-AUTH",
            status=TaskStatus.QUEUED,
            intake=SlackIntake(
                source_event_id="evt-default-auth",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR verify fail-closed authentication",
            ),
        )

        self.assertEqual(
            IntakeAuthentication.BEARER_DRY_RUN,
            record.intake_authentication,
        )

    async def test_the_coordinator_executes_the_assignment_it_takes_ownership_of(
        self,
    ) -> None:
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(settings, packet_task_router=router)
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        legacy_adapter = AsyncMock()
        engine.providers[ProviderName.CLAUDE] = legacy_adapter
        record, assignment = self._record()
        store.create(record)

        continued = await engine._execute_assignment(record, assignment)

        persisted = store.get(record.task_id)
        self.assertFalse(continued)
        # The coordinator, not the legacy engine, contacted the provider.
        self.assertEqual(transport.sends, 1)
        legacy_adapter.dispatch.assert_not_awaited()
        self.assertEqual(self._ledger_status(), "COMPLETED")
        # It holds no acceptance authority, so the plan does not advance.
        self.assertEqual(persisted.status, TaskStatus.OWNER_DECISION_REQUIRED)
        self.assertIn("packet coordinator executed", persisted.message)
        self.assertNotIn("will not dispatch", persisted.message)

    async def test_missing_packet_authority_fails_closed_without_legacy_dispatch(
        self,
    ) -> None:
        router = StubPacketTaskRouter(
            self.work,
            None,
            failure=RuntimeError("authority snapshot is unavailable"),
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(settings, packet_task_router=router)
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        legacy_adapter = AsyncMock()
        engine.providers[ProviderName.CLAUDE] = legacy_adapter
        record, assignment = self._record()
        store.create(record)

        continued = await engine._execute_assignment(record, assignment)

        persisted = store.get(record.task_id)
        self.assertFalse(continued)
        legacy_adapter.dispatch.assert_not_awaited()
        # Nothing was reserved, so no idempotency key is stranded.
        self.assertIsNone(self._ledger_status())
        self.assertEqual(persisted.status, TaskStatus.OWNER_DECISION_REQUIRED)
        self.assertIn("boundary adapter", persisted.message)
        self.assertIn("authority snapshot is unavailable", persisted.error)

    async def test_packet_dispatch_reserves_canonical_task_budget_before_send(self):
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            max_task_budget_usd=0.5,
        )
        activation = RuntimeActivation(settings, packet_task_router=router)
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        record, assignment = self._record(estimated_cost_usd=0.7)
        store.create(record)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        persisted = store.get(record.task_id)
        self.assertEqual(0, transport.sends)
        self.assertIsNone(self._ledger_status())
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, persisted.status)
        self.assertIn("budget/effect ledger", persisted.message)

    async def test_packet_daily_budget_failure_precedes_slack_delegation(self):
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(settings, packet_task_router=router)
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        engine._reserve_provider_daily_spend = MagicMock(
            side_effect=ProviderDailyLimitExceeded("daily provider budget exhausted")
        )
        engine._publish_delegation = AsyncMock(return_value=True)
        record, assignment = self._record()
        store.create(record)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        engine._publish_delegation.assert_not_awaited()
        self.assertEqual(0, transport.sends)

    async def test_legacy_daily_budget_failure_precedes_slack_delegation(self):
        settings = self._settings(
            claude_routine_url="https://claude.invalid/fire",
            claude_routine_token="test-token",
        )
        engine, store = self._engine(settings, None)
        adapter = AsyncMock()
        engine.providers[ProviderName.CLAUDE] = adapter
        engine._reserve_provider_daily_spend = MagicMock(
            side_effect=ProviderDailyLimitExceeded("daily provider budget exhausted")
        )
        engine._publish_delegation = AsyncMock(return_value=True)
        record, assignment = self._record()
        store.create(record)

        self.assertFalse(await engine._execute_assignment(record, assignment))

        engine._publish_delegation.assert_not_awaited()
        adapter.dispatch.assert_not_awaited()

    async def test_parallel_daily_budget_failure_precedes_slack_delegation(self):
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            langgraph_parallel_external_dispatch_enabled=True,
            langgraph_parallel_kill_switch=False,
        )
        activation = RuntimeActivation(settings, packet_task_router=router)
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        engine._reserve_provider_daily_spend = MagicMock(
            side_effect=ProviderDailyLimitExceeded("daily provider budget exhausted")
        )
        engine._publish_delegation = AsyncMock(return_value=True)
        record, _ = self._record()
        stored, _ = store.create(record)

        await engine._dispatch_parallel_packet_batch(stored)

        engine._publish_delegation.assert_not_awaited()
        self.assertEqual(0, transport.sends)

    async def test_unknown_parallel_provider_cap_stops_before_any_reservation(self):
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            langgraph_parallel_external_dispatch_enabled=True,
            langgraph_parallel_kill_switch=False,
        )
        activation = RuntimeActivation(settings, packet_task_router=router)
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        engine._publish_delegation = AsyncMock(return_value=True)
        record, _ = self._record()
        stored, _ = store.create(record)

        with patch.object(
            Settings, "parallel_provider_caps", return_value={"cursor": 1}
        ):
            self.assertFalse(await engine._dispatch_parallel_packet_batch(stored))

        engine._publish_delegation.assert_not_awaited()
        self.assertEqual(0, transport.sends)
        persisted = store.get(record.task_id)
        self.assertFalse(
            [
                item
                for item in persisted.task_effect_ledger
                if item.kind == "provider_intent"
            ]
        )
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM parallel_dispatch_batches"
                ).fetchone()[0],
            )

    async def test_legacy_ownership_is_preserved_while_live_dispatch_is_off(
        self,
    ) -> None:
        settings = self._settings(langgraph_runtime_enabled=True)
        activation = RuntimeActivation(settings)
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        record, assignment = self._record()
        store.create(record)

        # None means this engine keeps dispatch ownership and runs its own lane.
        self.assertIsNone(await engine._route_live_dispatch(record, assignment))
        self.assertFalse(activation.coordinator_owns_dispatch())

    async def test_late_authenticated_readback_reconciles_without_redispatch(self):
        transport = LateReadbackTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        consumer = RecordingResultConsumer()
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=consumer,
        )
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        record, assignment = self._record()
        store.create(record)

        self.assertFalse(await engine._execute_assignment(record, assignment))
        self.assertEqual(1, transport.sends)
        self.assertEqual("RECONCILIATION_REQUIRED", self._ledger_status())

        late = store.get(record.task_id)
        late.status = TaskStatus.RUNNING
        late.provider_runs.append(
            ProviderRun(
                provider=ProviderName.CLAUDE,
                assignment_sequence=assignment.sequence,
                interaction_id=self.result["correlation"]["correlation_id"],
                run_id=self.result["run_reference"],
                status="completed",
                result=self.result["outcome"],
                evidence_source="provider_authenticated",
            )
        )
        store.save(late)

        advanced = copy.deepcopy(self.work)
        advanced["starting_ref"] = "b" * 40
        advanced["authority_snapshot"] = [
            {"path": "AGENTS.md", "commit_sha": "b" * 40, "blob_sha": "c" * 40}
        ]
        advanced["authority_snapshot_digest"] = document_digest(
            {"authority_snapshot": advanced["authority_snapshot"]}
        )
        router.packet = advanced

        resumed = store.get(record.task_id)
        self.assertIsNone(
            await engine._route_live_dispatch(resumed, assignment)
        )
        self.assertEqual(1, transport.sends)
        self.assertEqual("COMPLETED", self._ledger_status())
        self.assertEqual(1, consumer.calls)
        self.assertEqual(self.work["starting_ref"], transport.readback_packet["starting_ref"])

    async def test_blocking_sync_readback_times_out_without_freezing_or_redispatch(
        self,
    ) -> None:
        work = copy.deepcopy(self.work)
        work["limits"]["timeout_seconds"] = 1
        transport = BlockingSyncLateReadbackTransport(self.result)
        router = StubPacketTaskRouter(
            work, PacketBoundaryAdapter("claude", transport)
        )
        consumer = RecordingResultConsumer()
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=consumer,
        )
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        record, assignment = self._record()
        store.create(record)

        self.assertFalse(await engine._execute_assignment(record, assignment))
        late = store.get(record.task_id)
        late.status = TaskStatus.RUNNING
        late.provider_runs.append(
            ProviderRun(
                provider=ProviderName.CLAUDE,
                assignment_sequence=assignment.sequence,
                interaction_id=self.result["correlation"]["correlation_id"],
                run_id=self.result["run_reference"],
                status="completed",
                result=self.result["outcome"],
                evidence_source="provider_authenticated",
            )
        )
        store.save(late)

        route = asyncio.create_task(
            engine._route_live_dispatch(store.get(record.task_id), assignment)
        )
        try:
            self.assertTrue(
                await asyncio.wait_for(
                    asyncio.to_thread(transport.entered.wait, 2), timeout=2
                )
            )
            await asyncio.sleep(0.05)
            self.assertFalse(route.done(), "sync readback blocked the event loop")
            self.assertFalse(await asyncio.wait_for(route, timeout=2))
        finally:
            transport.release.set()

        persisted = store.get(record.task_id)
        self.assertEqual(1, transport.sends)
        self.assertEqual(1, transport.readback_calls)
        self.assertEqual(0, consumer.calls)
        self.assertEqual("RECONCILIATION_REQUIRED", self._ledger_status())
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, persisted.status)

    async def test_stale_generation_readback_cannot_reconcile_current_packet(self):
        transport = LateReadbackTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        consumer = RecordingResultConsumer()
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=consumer,
        )
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        record, assignment = self._record()
        store.create(record)

        self.assertFalse(await engine._execute_assignment(record, assignment))
        late = store.get(record.task_id)
        late.status = TaskStatus.RUNNING
        late.provider_runs.append(
            ProviderRun(
                provider=ProviderName.CLAUDE,
                assignment_sequence=assignment.sequence,
                interaction_id="stale-generation-correlation",
                run_id=self.result["run_reference"],
                status="completed",
                result=self.result["outcome"],
                evidence_source="provider_authenticated",
            )
        )
        store.save(late)

        self.assertFalse(
            await engine._route_live_dispatch(store.get(record.task_id), assignment)
        )
        self.assertEqual(1, transport.sends)
        self.assertIsNone(transport.readback_packet)
        self.assertEqual(0, consumer.calls)
        self.assertEqual("RECONCILIATION_REQUIRED", self._ledger_status())

    async def test_completed_packet_replays_original_snapshot_after_ref_advances(self):
        transport = RecordingTransport(self.result)
        router = StubPacketTaskRouter(
            self.work, PacketBoundaryAdapter("claude", transport)
        )
        consumer = RecordingResultConsumer()
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=consumer,
        )
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        record, assignment = self._record()
        store.create(record)

        self.assertIsNone(await engine._route_live_dispatch(record, assignment))
        self.assertEqual(1, transport.sends)
        original = activation.runtime.bound_work_packet(self.work["idempotency_key"])
        self.assertIsNotNone(original)

        advanced = copy.deepcopy(self.work)
        advanced["starting_ref"] = "b" * 40
        advanced["authority_snapshot"] = [
            {"path": "AGENTS.md", "commit_sha": "b" * 40, "blob_sha": "c" * 40}
        ]
        advanced["authority_snapshot_digest"] = document_digest(
            {"authority_snapshot": advanced["authority_snapshot"]}
        )
        router.packet = advanced

        resumed = store.get(record.task_id)
        self.assertIsNone(await engine._route_live_dispatch(resumed, assignment))
        self.assertEqual(1, transport.sends)
        replayed = activation.runtime.bound_work_packet(self.work["idempotency_key"])
        self.assertEqual(self.work["starting_ref"], replayed["starting_ref"])
        self.assertEqual(2, consumer.calls)

    async def test_paid_off_tasks_resumed_paid_on_share_the_daily_cap(self):
        settings = self._settings(
            openai_api_key="configured-test-key",
            max_task_budget_usd=1.0,
            max_daily_provider_budget_usd=1.0,
        )
        engine, store = self._engine(settings, None)
        first, _ = self._record()
        first.task_id = "DIR-PAID-OFF-FIRST"
        first.intake.source_event_id = "evt-paid-off-first"
        first.plan = None
        first.plan_generation = 0
        first.plan_interaction_id = None
        first.task_effect_ledger = []
        first.provider_interaction_ledger = []
        first.intake_authentication = IntakeAuthentication.SLACK_GATEWAY_ATTESTED
        second, _ = self._record()
        second.task_id = "DIR-PAID-OFF-SECOND"
        second.intake.source_event_id = "evt-paid-off-second"
        second.plan = None
        second.plan_generation = 0
        second.plan_interaction_id = None
        second.task_effect_ledger = []
        second.provider_interaction_ledger = []
        second.intake_authentication = IntakeAuthentication.SLACK_GATEWAY_ATTESTED
        store.create(first)
        store.create(second)
        engine.daily_task_budget.reserve(
            task_id=first.task_id,
            task_budget_usd=1.0,
            daily_budget_usd=1.0,
        )
        engine.director.plan = AsyncMock()

        await engine.run(second.task_id)

        persisted = store.get(second.task_id)
        self.assertEqual(TaskStatus.BLOCKED, persisted.status)
        self.assertIn("daily", persisted.message.lower())
        engine.director.plan.assert_not_awaited()

    async def test_two_parallel_members_reserve_send_and_consume_without_stale_write(self):
        assignments = [
            Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="Independently inspect fixture A",
                acceptance=["Fixture A inspected"],
                repository="example/demo",
                estimated_cost_usd=0.1,
            ),
            Assignment(
                sequence=2,
                agent=ProviderName.OPENAI,
                task="Independently inspect fixture B",
                acceptance=["Fixture B inspected"],
                repository="example/demo",
                estimated_cost_usd=0.1,
            ),
        ]
        record, _ = self._record()
        record.plan.assignments = assignments
        record.plan.max_hops = 3
        record.task_effect_ledger = []
        record.provider_interaction_ledger = []
        record.plan_interaction_id = None
        record.plan_generation = 0
        seed_production_plan_materialization(record)
        packets = {}
        adapters = {}
        transports = {}
        for assignment in assignments:
            work = copy.deepcopy(self.work)
            work["assignment_id"] = f"{record.task_id}-{assignment.sequence}"
            work["objective"] = assignment.task
            work["acceptance_checks"] = list(assignment.acceptance)
            work["idempotency_key"] = (
                f"{record.task_id}:{record.plan_generation}:{assignment.sequence}:"
                + ("a" * 64)
            )
            work["correlation"]["correlation_id"] = (
                f"{record.task_id}:generation-{record.plan_generation}:"
                f"assignment-{assignment.sequence}"
            )
            packets[assignment.sequence] = work
            result = copy.deepcopy(self.result)
            for field in (
                "task_id",
                "assignment_id",
                "idempotency_key",
                "authority_snapshot_digest",
                "correlation",
            ):
                result[field] = copy.deepcopy(work[field])
            result["provider"] = assignment.agent.value
            result["run_reference"] = f"run-{assignment.sequence}"
            result["acceptance"] = [
                {
                    "check": assignment.acceptance[0],
                    "status": "NOT_RUN",
                    "evidence": "Director review remains required.",
                }
            ]
            transport = RecordingTransport(result)
            transports[assignment.agent.value] = transport
            adapters[assignment.agent.value] = PacketBoundaryAdapter(
                assignment.agent.value, transport
            )
        router = MultiPacketTaskRouter(packets, adapters)
        consumer = RecordingResultConsumer()
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            langgraph_parallel_external_dispatch_enabled=True,
            langgraph_parallel_kill_switch=False,
            max_task_budget_usd=1.0,
            max_daily_provider_budget_usd=5.0,
        )
        activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=consumer,
        )
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        engine._publish_delegation = AsyncMock(return_value=True)
        stored, _ = store.create(record)

        self.assertTrue(await engine._dispatch_parallel_packet_batch(stored))

        self.assertEqual(1, transports["cursor"].sends)
        self.assertEqual(1, transports["openai"].sends)
        self.assertEqual(2, consumer.calls)
        persisted = store.get(record.task_id)
        intents = [
            item
            for item in persisted.task_effect_ledger
            if item.kind == "provider_intent" and item.phase == "dispatch"
        ]
        self.assertEqual(2, len(intents))
        self.assertAlmostEqual(0.2, persisted.cumulative_estimated_cost_usd)

    async def test_parallel_restart_republishes_delegation_before_provider_send(self):
        class SimulatedProcessCrash(BaseException):
            pass

        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Recover one interrupted delegation",
            acceptance=["Delegation precedes the recovered provider send"],
            repository="example/demo",
            estimated_cost_usd=0.1,
        )
        record, _ = self._record()
        record.plan.assignments = [assignment]
        record.plan.max_hops = 2
        record.task_effect_ledger = []
        record.provider_interaction_ledger = []
        record.plan_interaction_id = None
        record.plan_generation = 0
        seed_production_plan_materialization(record)

        work = copy.deepcopy(self.work)
        work["assignment_id"] = f"{record.task_id}-{assignment.sequence}"
        work["objective"] = assignment.task
        work["acceptance_checks"] = list(assignment.acceptance)
        work["idempotency_key"] = (
            f"{record.task_id}:{record.plan_generation}:{assignment.sequence}:"
            + ("b" * 64)
        )
        work["correlation"]["correlation_id"] = (
            f"{record.task_id}:generation-{record.plan_generation}:"
            f"assignment-{assignment.sequence}"
        )
        result = copy.deepcopy(self.result)
        for field in (
            "task_id",
            "assignment_id",
            "idempotency_key",
            "authority_snapshot_digest",
            "correlation",
        ):
            result[field] = copy.deepcopy(work[field])
        result["provider"] = assignment.agent.value
        result["run_reference"] = "run-recovered-delegation"
        result["acceptance"] = [
            {
                "check": assignment.acceptance[0],
                "status": "NOT_RUN",
                "evidence": "Director review remains required.",
            }
        ]
        transport = RecordingTransport(result)
        router = MultiPacketTaskRouter(
            {assignment.sequence: work},
            {
                assignment.agent.value: PacketBoundaryAdapter(
                    assignment.agent.value, transport
                )
            },
        )
        settings = self._settings(
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            langgraph_parallel_external_dispatch_enabled=True,
            langgraph_parallel_kill_switch=False,
            slack_bot_token="test-slack-token",
            max_task_budget_usd=1.0,
            max_daily_provider_budget_usd=5.0,
        )
        first_activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=RecordingResultConsumer(),
        )
        self.addCleanup(first_activation.close)
        first_engine, first_store = self._engine(settings, first_activation)
        first_engine.notifier.post = AsyncMock(return_value="1700000000.000001")
        publish_delegation = first_engine._publish_delegation

        async def publish_then_crash(current, current_assignment):
            self.assertTrue(
                await publish_delegation(current, current_assignment)
            )
            raise SimulatedProcessCrash("crash after durable Slack delivery")

        first_engine._publish_delegation = publish_then_crash
        stored, _ = first_store.create(record)

        with self.assertRaises(SimulatedProcessCrash):
            await first_engine._dispatch_parallel_packet_batch(stored)

        self.assertEqual(0, transport.sends)
        self.assertEqual(2, first_engine.notifier.post.await_count)
        recovered_batch = first_engine.parallel_dispatch.find_latest_batch(
            record.task_id,
            (hashlib.sha256(work["idempotency_key"].encode("utf-8")).hexdigest(),),
        )
        self.assertIsNotNone(recovered_batch)
        self.assertEqual(
            MemberState.DISPATCHING,
            recovered_batch.members[0].state,
        )

        restarted_consumer = RecordingResultConsumer()
        restarted_activation = RuntimeActivation(
            settings,
            packet_task_router=router,
            packet_result_consumer=restarted_consumer,
        )
        self.addCleanup(restarted_activation.close)
        restarted_engine, restarted_store = self._engine(
            settings, restarted_activation
        )
        restarted_engine.notifier = first_engine.notifier
        restarted_publish = restarted_engine._publish_delegation
        restarted_engine._publish_delegation = AsyncMock(
            wraps=restarted_publish
        )
        restarted_record = restarted_store.get(record.task_id)

        self.assertTrue(
            await restarted_engine._dispatch_parallel_packet_batch(restarted_record)
        )

        restarted_engine._publish_delegation.assert_awaited_once()
        self.assertEqual(2, restarted_engine.notifier.post.await_count)
        self.assertEqual(1, transport.sends)
        self.assertEqual(1, restarted_consumer.calls)
        completed_batch = restarted_engine.parallel_dispatch.aggregate(
            recovered_batch.batch_id
        )
        self.assertEqual(MemberState.SUCCEEDED, completed_batch.members[0].state)


if __name__ == "__main__":
    unittest.main()
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 11:58 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:16 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:26 UTC | END]]
