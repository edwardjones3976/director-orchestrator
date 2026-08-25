# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
"""LIVE_SAFE routing: the coordinator executes the task it takes ownership of.

Surrendering legacy dispatch ownership to a coordinator that cannot convert an
intake task into a work packet would leave no engine executing anything. These
tests pin the two halves of that contract: ownership is surrendered *into* a real
`run_packet` execution, and a missing packet, authority snapshot, or v1 boundary
adapter fails closed explicitly instead of falling back to the legacy writer.
"""

from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import AsyncMock

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.langgraph_runtime import (
    PacketBoundaryAdapter,
    WriterOwnershipToken,
)
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.packet_contracts import load_json_document
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

    def writer_token(self, record) -> WriterOwnershipToken:
        return self.token

    def boundary_adapter(self, provider: str) -> PacketBoundaryAdapter:
        assert self.adapter is not None
        return self.adapter


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
            "allow_paid_runs": True,
            "project_allowlist": ("demo",),
            "repository_allowlist": ("example/demo",),
            "allowed_starting_refs": ("main",),
            "claude_callback_secret": "callback-secret",
        }
        fields.update(overrides)
        return replace(Settings(), **fields)

    def _record(self) -> tuple[TaskRecord, Assignment]:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Route one assignment through the packet coordinator",
            acceptance=["The coordinator owns dispatch"],
            repository="example/demo",
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


if __name__ == "__main__":
    unittest.main()
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
