# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 02:01 UTC | BEGIN]]
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.providers import ProviderReservationIdentityConflict
from director_orchestrator.store import TaskStore
from tests.plan_binding_helpers import seed_production_plan_materialization


class ProviderReservationConflictTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_identity_conflict_is_a_hard_owner_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            settings = replace(
                Settings(),
                sqlite_path=database,
                allow_paid_runs=True,
                cursor_api_key="configured-for-readiness-only",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="Prove reservation conflicts stop",
                acceptance=["No paid dispatch occurs"],
                repository="example/demo",
            )
            record = TaskRecord(
                task_id="DIR-RESERVATION-CONFLICT",
                status=TaskStatus.RUNNING,
                intake=SlackIntake(
                    source_event_id="evt-reservation-conflict",
                    channel_id="C123",
                    thread_ts="123.45",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: stop on accounting conflict",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="stop on accounting conflict",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[assignment],
                    final_acceptance=["Conflict remains unresolved"],
                ),
            )
            seed_production_plan_materialization(record)
            store = TaskStore(database)
            store.create(record)
            engine = OrchestrationEngine(settings, store)
            adapter = AsyncMock()
            engine.providers[ProviderName.CURSOR] = adapter
            engine._reserve_provider_daily_spend = Mock(
                side_effect=ProviderReservationIdentityConflict(
                    "same provider/effect key has different accounting"
                )
            )

            continued = await engine._execute_assignment(record, assignment)

            persisted = store.get(record.task_id)
            self.assertFalse(continued)
            self.assertEqual(
                persisted.status, TaskStatus.OWNER_DECISION_REQUIRED
            )
            self.assertIn("identity conflict", persisted.message)
            self.assertIn("different accounting", persisted.error)
            adapter.dispatch.assert_not_awaited()
            self.assertFalse(
                any(
                    item.outcome == "failed_pre_submit"
                    for item in persisted.task_effect_ledger
                )
            )
            self.assertTrue(
                any(
                    item.kind == "provider_intent" and item.outcome == "reserved"
                    for item in persisted.task_effect_ledger
                )
            )


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-25 | 02:01 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
