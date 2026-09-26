# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:43 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | BEGIN]]
"""Review 4112533824: ordinary provider work settles its daily-spend reservation through record_charge."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    ProviderRun,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.providers import ProviderDailyLimitExceeded
from director_orchestrator.store import TaskStore
from director_orchestrator.usage import UsageMonitor
from tests.plan_binding_helpers import seed_production_plan_materialization


HOUR = timedelta(hours=1)


class _ReviewOutOfScope(Exception):
    pass


def _key(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class ProviderSpendSettlementTests(unittest.IsolatedAsyncioTestCase):
    def _world(self, directory: str, *, ceiling: float | None = 0.01):
        database = str(Path(directory) / "tasks.sqlite3")
        settings = replace(
            Settings(),
            sqlite_path=database,
            allow_paid_runs=True,
            gemini_api_key="configured-for-readiness-only",
            gemini_daily_budget_usd=1.0,
            gemini_daily_request_limit=50,
            gemini_usage_probe_cost_ceiling_usd=ceiling,
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.GEMINI,
            task="Answer one bounded question",
            acceptance=["An answer is returned"],
            estimated_cost_usd=0.0,
        )
        record = TaskRecord(
            task_id="DIR-SPEND-SETTLEMENT",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-spend-settlement",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: settle provider spend",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="settle provider spend",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[assignment],
                final_acceptance=["Spend is reconciled"],
            ),
        )
        seed_production_plan_materialization(record)
        store = TaskStore(database)
        store.create(record)
        return settings, store, record, assignment

    async def _dispatch(self, settings, store, record, assignment, raw):
        engine = OrchestrationEngine(settings, store)
        adapter = AsyncMock()
        adapter.dispatch.return_value = ProviderRun(
            provider=ProviderName.GEMINI, run_id="gemini-run-1", status="completed", result="done", raw=raw
        )
        engine.providers[ProviderName.GEMINI] = adapter
        engine._status_post = AsyncMock()
        # The Director's acceptance review after the provider run is out of scope; settlement precedes it.
        engine.director.review = AsyncMock(side_effect=_ReviewOutOfScope())
        with self.assertRaises(_ReviewOutOfScope):
            await engine._execute_assignment(record, assignment)
        adapter.dispatch.assert_awaited_once()
        return engine

    async def test_a_zero_estimate_request_that_cost_more_refuses_the_next_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            # $0.50 of other spend today under the $1 cap.
            engine.provider_spend.reserve(provider=ProviderName.GEMINI, effect_key=_key("other"),
                                          estimated_cost_usd=0.5, request_limit=50, budget_usd=1.0)
            engine = await self._dispatch(settings, store, record, assignment, {"usage": {"cost": 0.60}})
            reservations = dict(engine.provider_spend.reservations(ProviderName.GEMINI))
            # The $0 estimate reserved the provider's nonzero bound, never $0.
            self.assertEqual(sorted(reservations.values()), [0.01, 0.5])
            count, exposure = engine.provider_spend.exposure(ProviderName.GEMINI)
            self.assertEqual(count, 2)
            self.assertAlmostEqual(exposure, 1.10)  # the actual $0.60 charge, not its $0.01 reservation
            with self.assertRaises(ProviderDailyLimitExceeded):
                engine.provider_spend.reserve(provider=ProviderName.GEMINI, effect_key=_key("next"),
                                              estimated_cost_usd=0.01, request_limit=50, budget_usd=1.0)
            self.assertEqual(engine.provider_spend.unreconciled(ProviderName.GEMINI, stale_after=HOUR), [])

    async def test_a_settled_request_without_a_reported_cost_is_unreconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = await self._dispatch(settings, store, record, assignment, {})
            self.assertEqual(len(engine.provider_spend.unreconciled(ProviderName.GEMINI, stale_after=HOUR)), 1)
            _, exposure = engine.provider_spend.exposure(ProviderName.GEMINI)
            self.assertAlmostEqual(exposure, 0.01)  # it keeps counting at its bound
            # The producer check 15 reads reports it, so check 15 cannot pass on that lower bound.
            accounting = UsageMonitor(settings, store, adapters={})._daily_accounting(
                "gemini", datetime.now(timezone.utc))
            self.assertEqual(accounting["unreconciled_request_count"], 1)

    async def test_a_zero_estimate_with_no_configured_bound_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory, ceiling=None)
            engine = OrchestrationEngine(settings, store)
            with self.assertRaises(ProviderDailyLimitExceeded):
                engine._reserve_provider_daily_spend(assignment, effect_key=_key("unbounded"))
            self.assertEqual(engine.provider_spend.reservations(ProviderName.GEMINI), [])

    async def _run_unfinished(self, settings, store, record, assignment, poll_error):
        engine = OrchestrationEngine(settings, store)
        adapter = AsyncMock()
        adapter.dispatch.return_value = ProviderRun(
            provider=ProviderName.GEMINI, run_id="gemini-run-2", status="RUNNING", raw={}
        )
        adapter.poll.side_effect = poll_error
        engine.providers[ProviderName.GEMINI] = adapter
        engine._status_post = AsyncMock()
        return engine, adapter

    def _check_15_state(self, unreconciled_count: int) -> str:
        """Check 15 over the monitor's accounting with the producer's unreconciled count."""
        from tests.test_completion_scorecard import World, row  # the scorecard's own fixture world
        world = World(self)
        world.payload["usage_monitor"]["daily_accounting"]["grok"]["unreconciled_request_count"] = unreconciled_count
        return row(world.report(), "15")["status"]

    async def test_an_unavailable_run_is_unreconciled_not_invisible(self) -> None:
        """Review 4112697677: the ProviderUnavailableError exit leaves the dispatched request unreconciled."""
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            settings = replace(settings, provider_poll_seconds=0)
            engine, adapter = await self._run_unfinished(
                settings, store, record, assignment, httpx.ConnectTimeout("no response"))
            self.assertTrue(await engine._execute_assignment(record, assignment))  # deferred as unavailable
            self.assertEqual(adapter.poll.await_count, 3)
            self.assertEqual(len(engine.provider_spend.unreconciled(ProviderName.GEMINI, stale_after=HOUR)), 1)
            count = UsageMonitor(settings, store, adapters={})._daily_accounting(
                "gemini", datetime.now(timezone.utc))["unreconciled_request_count"]
            self.assertEqual(count, 1)
            self.assertEqual(self._check_15_state(count), "UNKNOWN")

    async def test_a_timed_out_run_is_unreconciled_not_invisible(self) -> None:
        """Review 4112697677: a polling timeout that raises past the settlement still leaves it unreconciled."""
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            settings = replace(settings, provider_timeout_seconds=0)
            engine, _ = await self._run_unfinished(settings, store, record, assignment, AssertionError("never polled"))
            with self.assertRaises(TimeoutError):
                await engine._execute_assignment(record, assignment)
            self.assertEqual(len(engine.provider_spend.unreconciled(ProviderName.GEMINI, stale_after=HOUR)), 1)
            count = UsageMonitor(settings, store, adapters={})._daily_accounting(
                "gemini", datetime.now(timezone.utc))["unreconciled_request_count"]
            self.assertEqual(count, 1)
            self.assertEqual(self._check_15_state(count), "UNKNOWN")
            self.assertEqual(self._check_15_state(0), "PASS")

    def test_a_reservation_never_settled_within_the_bound_is_unreconciled(self) -> None:
        """A crash between dispatch and settlement cannot hide a request: unsettled past the bound counts."""
        with tempfile.TemporaryDirectory() as directory:
            settings, store, _, _ = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            now = datetime.now(timezone.utc)
            engine.provider_spend.reserve(provider=ProviderName.GEMINI, effect_key=_key("crashed"),
                                          estimated_cost_usd=0.01, request_limit=50, budget_usd=1.0,
                                          now=now - timedelta(minutes=30))
            self.assertEqual(engine.provider_spend.unreconciled(ProviderName.GEMINI, stale_after=HOUR, now=now), [])
            self.assertEqual(engine.provider_spend.unreconciled(
                ProviderName.GEMINI, stale_after=timedelta(minutes=10), now=now), [_key("crashed")])

    def test_a_completed_packet_settles_its_reported_cost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = _key("packet")
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            outcome = SimpleNamespace(result_packet={"run_reference": "packet-run", "usage": {"cost_usd": 0.25}})
            engine._settle_packet_spend(record, assignment, key, outcome)
            self.assertAlmostEqual(engine.provider_spend.exposure(ProviderName.GEMINI)[1], 0.25)
            unpriced = _key("packet-unpriced")
            engine._reserve_provider_daily_spend(assignment, effect_key=unpriced)
            engine._settle_packet_spend(record, assignment, unpriced, SimpleNamespace(result_packet=None))
            self.assertEqual(engine.provider_spend.unreconciled(ProviderName.GEMINI, stale_after=HOUR), [unpriced])


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:43 UTC | END]]
