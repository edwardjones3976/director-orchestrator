# [[OpenAI Codex | GPT-6 | 2026-09-27 | 13:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 12:59 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 12:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 11:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 04:00 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:43 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | BEGIN]]
"""Review 4112533824: ordinary provider work settles its daily-spend reservation through record_charge."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
from director_orchestrator.usage import UsageMonitor, authoritative_reported_cost
from tests.plan_binding_helpers import seed_production_plan_materialization
from director_orchestrator.models import (
    AntigravityDirective as _AntigravityDirective,
    AntigravityFixtureLane as _AntigravityFixtureLane,
    Assignment as _Assignment,
    AssignmentKind as _AssignmentKind,
    ProviderName as _ProviderName,
)


def work_assignment(**overrides):
    # A valid typed Gemini/Antigravity work assignment (mirrors test_antigravity_contract.work_assignment,
    # which cannot be imported by module name because it relies on a sibling import).
    fields = {
        "sequence": 1,
        "agent": _ProviderName.GEMINI,
        "kind": _AssignmentKind.BOILERPLATE,
        "task": "Generate boilerplate test fixtures for the bounded adapter module",
        "acceptance": ["Report the isolated evidence"],
        "antigravity_directive": _AntigravityDirective(lane=_AntigravityFixtureLane.UNIT_TEST_FIXTURE_SET),
    }
    fields.update(overrides)
    return _Assignment(**fields)


HOUR = timedelta(hours=1)


class _ReviewOutOfScope(Exception):
    pass


def _key(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class ProviderSpendSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_charge_paths_use_only_authoritative_usage(self) -> None:
        from director_orchestrator.approvals import record_provider_effect_observation, reserve_provider_effect

        for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
            for raw, expected in (
                ({"result": {"usage": {"cost": 0}}}, None),
                ({"tool_data": {"usage": {"cost": 0}}}, None),
                ({"usage": {"cost": 0.25}}, 0.25),
                ({"tool_data": {"usage": {"cost": 0}}, "usage": {"cost": 0.25}}, 0.25),
                ({"usage": {"cost": 0}}, 0),
                ({"usage": [], "result": {"usage": {"cost": 0}}}, None),
            ):
                for path in ("ordinary", "packet", "probe"):
                    with self.subTest(provider=provider, raw=raw, path=path), tempfile.TemporaryDirectory() as directory:
                        settings, store, record, assignment = self._world(directory)
                        settings = replace(settings, usage_check_enabled=True,
                                           usage_check_providers=(provider.value,),
                                           perplexity_usage_probe_cost_ceiling_usd=0.01)
                        assignment = assignment.model_copy(update={"agent": provider})
                        engine = OrchestrationEngine(settings, store)
                        run = ProviderRun(provider=provider, run_id="metered-run", status="completed", raw=raw)
                        if path == "probe":
                            adapter = AsyncMock()
                            adapter.usage_check.return_value = run
                            monitor = UsageMonitor(settings, store, adapters={provider: adapter})
                            monitor._post_summary = AsyncMock()
                            with patch.object(Settings, "usage_check_readiness", return_value={provider.value: True}):
                                records = await monitor.run_once(force=True)
                            self.assertEqual(records[0].reported_cost_usd, expected)
                            self.assertEqual(records[0].status.value, "ok")
                        else:
                            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                            record_provider_effect_observation(record, assignment, effect_key=key, phase="dispatch",
                                                               outcome="ambiguous")
                            run.assignment_sequence = assignment.sequence
                            engine._reserve_provider_daily_spend(assignment, effect_key=key)
                            record.provider_runs.append(run)
                            if path == "ordinary":
                                engine._settle_provider_spend(record, run, provider)
                            else:
                                outcome = SimpleNamespace(result_packet={
                                    "run_reference": run.run_id, "usage": {"cost_usd": 0},
                                })
                                engine._settle_packet_spend(record, assignment, key, outcome)
                        ledger = engine.provider_spend
                        reservations = ledger.reservations(provider)
                        self.assertEqual(len(reservations), 1)
                        self.assertEqual(ledger.unreconciled(provider, stale_after=HOUR),
                                         [reservations[0][0]] if expected is None else [])
                        self.assertAlmostEqual(ledger.exposure(provider)[1],
                                               reservations[0][1] if expected is None else expected)

    async def test_settlement_failure_retains_submit_and_stops_legacy_and_revision(self) -> None:
        from director_orchestrator.models import ReviewDecision, ReviewVerdict

        for phase in ("dispatch", "revision"):
            for failure in ("acknowledgement", "terminal", "ambiguous_submit"):
                with self.subTest(phase=phase, failure=failure), tempfile.TemporaryDirectory() as directory:
                    settings, store, record, assignment = self._world(
                        directory, max_hops=4, max_revisions=1)
                    engine = OrchestrationEngine(settings, store)
                    engine._status_post = AsyncMock()
                    adapter = AsyncMock()
                    original = ProviderRun(provider=assignment.agent, run_id="original-run",
                                           status="completed", result="original evidence", raw={"usage": {"cost": 0.1}})
                    revised = original.model_copy(update={"run_id": "revised-run", "result": "revised evidence"})
                    adapter.dispatch.return_value = original
                    adapter.revise.return_value = revised
                    submitted = adapter.dispatch if phase == "dispatch" else adapter.revise
                    if failure == "ambiguous_submit":
                        submitted.side_effect = RuntimeError("submit outcome unknown")
                    engine.providers[assignment.agent] = adapter
                    engine.director.review = AsyncMock(return_value=ReviewDecision(
                        verdict=ReviewVerdict.REVISE, summary="More evidence needed", revision_prompt="Add evidence"))
                    real_charge = engine.provider_spend.record_charge
                    calls = 0
                    fail_at = (0 if phase == "dispatch" else 2) + (2 if failure == "terminal" else 1)

                    def charge(**kwargs):
                        nonlocal calls
                        calls += 1
                        if calls == fail_at:
                            raise sqlite3.OperationalError("ledger unavailable")
                        return real_charge(**kwargs)

                    with patch.object(engine.provider_spend, "record_charge", side_effect=charge) as charge_mock:
                        self.assertFalse(await engine._execute_assignment(record, assignment))
                    self.assertEqual(calls, fail_at)
                    self.assertEqual(charge_mock.call_args.kwargs["actual_cost_usd"],
                                     0.1 if failure == "terminal" else None)
                    self.assertEqual(engine.director.review.await_count, int(phase == "revision"))
                    stopped = store.get(record.task_id)
                    self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
                    self.assertTrue(stopped.error.startswith("spend_settlement_failed:"))
                    key = stopped.error.split(":", 1)[1]
                    self.assertIn(key, engine.provider_spend.unreconciled(assignment.agent, stale_after=timedelta(0)))
                    contact = stopped.provider_contact_attempts[-1]
                    self.assertEqual(contact.phase, phase)
                    self.assertEqual(contact.outcome, "ambiguous_dispatch")
                    if failure != "ambiguous_submit":
                        expected = original if phase == "dispatch" else revised
                        self.assertEqual(contact.run_id, expected.run_id)
                        self.assertEqual(stopped.provider_runs[-1].result, expected.result)
                    self.assertFalse(await engine._execute_assignment(stopped, assignment))
                    await engine.run(record.task_id)
                    self.assertEqual(store.get(record.task_id).status, TaskStatus.OWNER_DECISION_REQUIRED)
                    self.assertIn(key, engine.provider_spend.unreconciled(
                        assignment.agent, stale_after=timedelta(0)))
                    adapter.dispatch.assert_awaited_once()
                    self.assertEqual(adapter.revise.await_count, int(phase == "revision"))

    def test_not_created_settles_only_exact_reservation_once(self) -> None:
        from director_orchestrator.approvals import (
            record_provider_effect_observation, reserve_provider_effect,
        )
        from director_orchestrator.models import AmbiguousDispatchResolution, ProviderContactAttempt

        for phase, crash in (("dispatch", False), ("revision", False), ("dispatch", True), ("revision", True)):
            with self.subTest(phase=phase, crash=crash), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(
                    directory, assignment=work_assignment(estimated_cost_usd=0.0))
                settings = replace(settings, gemini_input_usd_per_mtok=1.0,
                                   gemini_output_usd_per_mtok=1.0)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment, phase=phase, max_task_budget_usd=10)
                record_provider_effect_observation(record, assignment, effect_key=key, phase=phase, outcome="ambiguous")
                engine._reserve_provider_daily_spend(assignment, effect_key=key, context="bounded test request")
                other = _key("unrelated-reservation")
                engine._reserve_provider_daily_spend(assignment, effect_key=other, context="bounded test request")
                engine._charge_spend(assignment.agent, key, None)
                engine._charge_spend(assignment.agent, other, None)
                record.provider_contact_attempts.append(ProviderContactAttempt(
                    assignment_sequence=assignment.sequence, provider=assignment.agent, ordinal=1,
                    phase=phase, outcome="ambiguous_dispatch", error="submit outcome unknown"))
                record.status = TaskStatus.OWNER_DECISION_REQUIRED
                engine.apply_dispatch_reconciliation(record, AmbiguousDispatchResolution(
                    owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                    resolution="not_created", note="Provider confirmed no request was created"))
                store.save(record)
                with patch.object(engine.provider_spend, "record_charge",
                                  wraps=engine.provider_spend.record_charge) as charge:
                    if crash:
                        # Spend commits, but the task receipt does not: replay must
                        # update the same reservation and append only one receipt.
                        with patch.object(store, "save", side_effect=sqlite3.OperationalError("locked")):
                            with self.assertRaises(sqlite3.OperationalError):
                                engine._settle_reconciled_spend(record)
                    self.assertTrue(engine._settle_reconciled_spend(store.get(record.task_id)))
                    self.assertTrue(engine._settle_not_created_dispatches(store.get(record.task_id)))
                self.assertEqual(charge.call_count, 2 if crash else 1)
                self.assertEqual(charge.call_args.kwargs["effect_key"], key)
                self.assertEqual(charge.call_args.kwargs["actual_cost_usd"], 0.0)
                self.assertEqual(engine.provider_spend.unreconciled(assignment.agent, stale_after=HOUR), [other])
                self.assertEqual(len(engine.provider_spend.reservations(assignment.agent)), 2)
                # Restart uses the durable receipt, and the authoritative no-effect
                # resolution allows a new, separately reserved attempt.
                replay = store.get(record.task_id)
                restarted = OrchestrationEngine(settings, store)
                self.assertTrue(restarted._settle_reconciled_spend(replay))
                self.assertEqual(sum(item.kind == "provider_observation"
                                     and item.effect_key == key and item.outcome == "failed_pre_submit"
                                     for item in replay.task_effect_ledger), 1)
                replacement = reserve_provider_effect(replay, assignment, phase=phase, max_task_budget_usd=10)
                self.assertNotEqual(replacement, key)

    async def test_not_created_settlement_failure_blocks_resume_until_repaired(self) -> None:
        from director_orchestrator.approvals import apply_ambiguous_dispatch_resolution
        from director_orchestrator.models import AmbiguousDispatchResolution

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            engine._status_post = AsyncMock()
            adapter = AsyncMock()
            adapter.dispatch.side_effect = RuntimeError("submit outcome unknown")
            engine.providers[assignment.agent] = adapter
            self.assertFalse(await engine._execute_assignment(record, assignment))
            record = store.get(record.task_id)
            apply_ambiguous_dispatch_resolution(record, AmbiguousDispatchResolution(
                owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                resolution="not_created", note="Provider confirmed no request was created"))
            store.save(record)
            with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")):
                await engine.run(record.task_id)
            stopped = store.get(record.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            adapter.dispatch.assert_awaited_once()
            self.assertEqual(len(engine.provider_spend.unreconciled(assignment.agent, stale_after=HOUR)), 1)
            self.assertTrue(engine._settle_not_created_dispatches(stopped))
            self.assertEqual(engine.provider_spend.unreconciled(assignment.agent, stale_after=HOUR), [])

    async def test_created_resolution_still_charges_the_original_effect(self) -> None:
        from director_orchestrator.approvals import apply_ambiguous_dispatch_resolution
        from director_orchestrator.models import AmbiguousDispatchResolution

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            engine._status_post = AsyncMock()
            adapter = AsyncMock()
            adapter.dispatch.side_effect = RuntimeError("submit outcome unknown")
            engine.providers[assignment.agent] = adapter
            with patch.object(engine.provider_spend, "record_charge",
                              side_effect=sqlite3.OperationalError("locked")):
                self.assertFalse(await engine._execute_assignment(record, assignment))
            record = store.get(record.task_id)
            key = engine.provider_spend.reservations(assignment.agent)[0][0]
            apply_ambiguous_dispatch_resolution(record, AmbiguousDispatchResolution(
                owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                resolution="created", run_id="confirmed-run", note="Provider confirmed the existing run"))
            store.save(record)
            self.assertTrue(engine._settle_not_created_dispatches(record))
            run = record.provider_runs[-1]
            run.status = "completed"
            run.result = "authenticated result"
            run.raw = {"usage": {"cost": 0.3}}
            with patch.object(engine.provider_spend, "record_charge",
                              wraps=engine.provider_spend.record_charge) as charge:
                self.assertTrue(engine._settle_provider_spend(record, run, assignment.agent))
            self.assertEqual(charge.call_args.kwargs["effect_key"], key)
            self.assertEqual(charge.call_args.kwargs["actual_cost_usd"], 0.3)
            self.assertEqual(engine.provider_spend.unreconciled(assignment.agent, stale_after=HOUR), [])
            adapter.dispatch.assert_awaited_once()

    def test_authoritative_cost_rejects_malformed_and_wrong_fields(self) -> None:
        for provider in ("gemini", "grok", "xai", "perplexity"):
            for cost in (None, True, -1, float("nan"), float("inf"), "0", 10 ** 1000,
                         {"currency": "EUR", "total_cost": 0}):
                with self.subTest(provider=provider, cost=cost):
                    self.assertIsNone(authoritative_reported_cost(provider, {"usage": {"cost": cost}}))
            self.assertIsNone(authoritative_reported_cost(provider, {"usageMetadata": {"cost": 0}}))
            self.assertIsNone(authoritative_reported_cost(provider, {"usage": {"cost_usd": 0}}))
            self.assertIsNone(authoritative_reported_cost(provider, None))
        self.assertIsNone(authoritative_reported_cost("unknown", {"usage": {"cost": 0}}))
        for provider in ("grok", "xai"):
            self.assertEqual(authoritative_reported_cost(provider, {
                "usage": {"cost_in_usd_ticks": 5_000_000}, "result": {"usage": {"cost": 0}},
            }), 0.0005)
            for ticks in (True, -1, "0", float("inf"), 10 ** 1000):
                self.assertIsNone(authoritative_reported_cost(provider, {
                    "usage": {"cost_in_usd_ticks": ticks, "cost": 0},
                }))

    def _world(self, directory: str, *, ceiling: float | None = 0.01,
               max_hops: int = 1, max_revisions: int = 0,
               assignment: Assignment | None = None):
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
        assignment = assignment or Assignment(
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
                max_hops=max_hops,
                max_revisions=max_revisions,
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
            engine.provider_spend.record_charge(provider=ProviderName.GEMINI, effect_key=_key("other"),
                                                actual_cost_usd=0.5)
            engine = await self._dispatch(settings, store, record, assignment, {"usage": {"cost": 0.60}})
            reservations = dict(engine.provider_spend.reservations(ProviderName.GEMINI))
            # Without prices, the request holds all remaining capacity.
            self.assertEqual(sorted(reservations.values()), [0.5, 0.5])
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
            self.assertAlmostEqual(exposure, 1.0)  # unknown cost retains all capacity
            # The producer check 15 reads reports it, so check 15 cannot pass on that lower bound.
            accounting = UsageMonitor(settings, store, adapters={})._daily_accounting(
                "gemini", datetime.now(timezone.utc))
            self.assertEqual(accounting["unreconciled_request_count"], 1)

    async def test_a_zero_estimate_with_no_configured_bound_serializes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory, ceiling=None)
            engine = OrchestrationEngine(settings, store)
            engine._reserve_provider_daily_spend(assignment, effect_key=_key("unbounded"))
            with self.assertRaises(ProviderDailyLimitExceeded):
                engine._reserve_provider_daily_spend(assignment, effect_key=_key("overlap"))
            self.assertEqual(engine.provider_spend.exposure(ProviderName.GEMINI), (1, 1.0))

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
        # The direct rule path: live collection holds 15 UNKNOWN until trusted collection lands (#313).
        return row(world.report(evaluate_rules=True), "15")["status"]

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

    def test_an_owner_reconciled_run_settles_its_ambiguous_dispatch(self) -> None:
        """Review 4112821088: a run the owner reconciled as created carries no observation of its own; its terminal
        settlement charges the ambiguous dispatch's reservation."""
        from director_orchestrator.approvals import record_provider_effect_observation, reserve_provider_effect
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            record_provider_effect_observation(record, assignment, effect_key=key, phase="dispatch",
                                               outcome="ambiguous")
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            run = ProviderRun(provider=ProviderName.GEMINI, assignment_sequence=assignment.sequence,
                              run_id="owner-reconciled", status="completed", result="done",
                              raw={"usage": {"cost": 0.3}})
            engine._settle_provider_spend(record, run, ProviderName.GEMINI)
            self.assertAlmostEqual(engine.provider_spend.exposure(ProviderName.GEMINI)[1], 0.3)
            self.assertEqual(engine.provider_spend.unreconciled(ProviderName.GEMINI, stale_after=HOUR), [])

    def test_a_completed_packet_settles_its_reported_cost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = _key("packet")
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            record.provider_runs.append(ProviderRun(
                provider=assignment.agent, run_id="packet-run", status="completed",
                raw={"usage": {"cost": 0.25}},
            ))
            outcome = SimpleNamespace(result_packet={"run_reference": "packet-run", "usage": {"cost_usd": 0}})
            engine._settle_packet_spend(record, assignment, key, outcome)
            self.assertAlmostEqual(engine.provider_spend.exposure(ProviderName.GEMINI)[1], 0.25)
            unpriced = _key("packet-unpriced")
            engine._reserve_provider_daily_spend(assignment, effect_key=unpriced)
            engine._settle_packet_spend(record, assignment, unpriced, SimpleNamespace(
                result_packet={"run_reference": "missing-run", "usage": {"cost_usd": 0}},
            ))
            self.assertEqual(engine.provider_spend.unreconciled(ProviderName.GEMINI, stale_after=HOUR), [unpriced])


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:43 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:21 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 04:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 11:15 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:49 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:42 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 12:31 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 12:59 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 13:02 UTC | END]]
