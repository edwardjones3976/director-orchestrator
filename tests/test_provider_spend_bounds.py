# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 17:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 12:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 11:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 11:15 UTC | BEGIN]]
"""Atomic worst-case admission; no paid calls or external services."""
import copy
import hashlib
import json
import os
import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, AsyncMock

from director_orchestrator.antigravity import antigravity_wire_input
from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.models import (
    AmbiguousDispatchResolution, Assignment, ProviderContactAttempt, ProviderName, ProviderRun, TaskStatus,
)
from director_orchestrator.providers import (
    GrokAdapter, ProviderDailyLimitExceeded, ProviderDailySpendLedger,
    ProviderReservationIdentityConflict, assignment_request_cost_bound,
)
from tests import test_provider_contracts
from tests import test_engine_packet_runtime_routing as routing
from tests import test_engine_provider_spend_settlement as settlement
from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.runtime_activation import RuntimeActivation
from director_orchestrator.approvals import record_provider_effect_observation, reserve_provider_effect


def key(label):
    return hashlib.sha256(str(label).encode()).hexdigest()


def priced(provider, **overrides):
    return replace(Settings(), **{
        f"{provider}_input_usd_per_mtok": 2.0,
        f"{provider}_output_usd_per_mtok": 8.0,
        **overrides,
    })


class ProviderSpendBoundsTests(unittest.TestCase):
    def test_pre_settlement_upgrade_preserves_exposure_without_pending_hold(self):
        for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "spend.sqlite3")
                now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
                old_day = now - timedelta(days=1)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute("""CREATE TABLE provider_daily_spend_reservations (
                        provider TEXT NOT NULL, effect_key TEXT NOT NULL, utc_day TEXT NOT NULL,
                        estimated_cost_usd REAL NOT NULL, recorded_at TEXT NOT NULL,
                        PRIMARY KEY(provider, effect_key))""")
                    for label, at in (("legacy", now), ("older", old_day)):
                        db.execute("INSERT INTO provider_daily_spend_reservations VALUES (?, ?, ?, ?, ?)",
                                   (provider.value, key(label), at.date().isoformat(), 0.75, at.isoformat()))
                ledger = ProviderDailySpendLedger(path)
                ledger = ProviderDailySpendLedger(path)  # migration is idempotent
                with closing(sqlite3.connect(path)) as db, db:
                    self.assertEqual(db.execute(
                        "SELECT legacy, serialized, actual_cost_usd FROM provider_daily_spend_reservations"
                    ).fetchall(), [(1, 0, None), (1, 0, None)])
                self.assertEqual(ledger.exposure(provider, now=old_day), (1, 0.75))
                with self.assertRaises(ProviderDailyLimitExceeded) as caught:
                    ledger.reserve(provider=provider, effect_key=key("too-large"), estimated_cost_usd=0.3,
                                   request_limit=10, budget_usd=1, now=now)
                self.assertNotEqual(caught.exception.code, "provider_spend_pending_retryable")
                ledger.reserve(provider=provider, effect_key=key("new"), estimated_cost_usd=0,
                               request_limit=10, budget_usd=1, serialize=True, now=now)
                self.assertEqual(ledger.exposure(provider, now=now), (2, 1))
                ledger.record_charge(provider=provider, effect_key=key("legacy"), actual_cost_usd=0.5, now=now)
                self.assertEqual(ledger.exposure(provider, now=now), (2, 0.75))
                # Reopening must not relabel or release a new serialized request.
                reopened = ProviderDailySpendLedger(path)
                with self.assertRaises(ProviderDailyLimitExceeded) as caught:
                    reopened.reserve(provider=provider, effect_key=key("blocked"), estimated_cost_usd=0,
                                     request_limit=10, budget_usd=1, serialize=True, now=now)
                self.assertEqual(caught.exception.code, "provider_spend_pending_retryable")
                tomorrow = now + timedelta(days=1)
                reopened.reserve(provider=provider, effect_key=key("tomorrow"), estimated_cost_usd=0,
                                 request_limit=10, budget_usd=1, serialize=True, now=tomorrow)
                self.assertEqual(reopened.exposure(provider, now=now), (2, 0.75))
                self.assertEqual(reopened.exposure(provider, now=tomorrow), (1, 1))

    def test_payload_bytes_output_cap_and_prices(self):
        for provider in ("gemini", "grok", "perplexity"):
            with self.subTest(provider=provider):
                settings = priced(provider)
                payload = {"input": "\u00e9\U0001f600", "max_output_tokens": 4096}
                expected = (len(json.dumps(payload, ensure_ascii=True).encode()) * 2 + 4096 * 8) / 1_000_000
                bound = settings.token_request_cost_bound_usd(provider, payload, 4096)
                self.assertGreaterEqual(bound, expected)
                self.assertAlmostEqual(bound, expected)

    def test_grok_sizes_the_wire_body_including_revision_and_context(self):
        settings = priced("grok")
        assignment = Assignment(sequence=1, agent=ProviderName.GROK, task="Answer", acceptance=["Evidence"])
        adapter = GrokAdapter(settings)
        for revision in (None, "Correct the result " * 1000):
            body = adapter.request_body(adapter._input(assignment, "\U0001f600" * 100, revision_prompt=revision))
            self.assertEqual(
                assignment_request_cost_bound(settings, assignment, "\U0001f600" * 100, revision_prompt=revision),
                settings.token_request_cost_bound_usd("grok", body, settings.grok_max_output_tokens),
            )
        self.assertIsNone(assignment_request_cost_bound(settings, assignment, None))
        self.assertIsNone(assignment_request_cost_bound(
            settings, assignment.model_copy(update={"allow_draft_pr": True}), "context"))

    def test_gemini_uses_whole_agent_token_cap(self):
        settings = priced("gemini")
        assignment = test_provider_contracts.ProviderContractTests.gemini_assignment()
        prompt = antigravity_wire_input(assignment, context="context")
        expected = (len(prompt.encode()) * 2 + settings.gemini_max_total_tokens * 8) / 1_000_000
        self.assertAlmostEqual(assignment_request_cost_bound(settings, assignment, "context"), expected)
        # Search has request fees, not a bounded token-only response.
        search = Assignment(sequence=1, agent=ProviderName.PERPLEXITY, task="Research", acceptance=["Sources"])
        self.assertIsNone(assignment_request_cost_bound(priced("perplexity"), search, "context"))

    def test_invalid_prices_refused_without_echoing_values(self):
        for provider in ("gemini", "grok", "perplexity"):
            for kind in ("input", "output"):
                field = f"{provider}_{kind}_usd_per_mtok"
                variable = field.upper()
                for value in (0, -1, float("nan"), float("inf"), True):
                    with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                        replace(Settings(), **{field: value})
                for raw in ("0", "-1", "nan", "inf", "private-invalid-price"):
                    with patch.dict(os.environ, {variable: raw}), self.assertRaises(ValueError) as caught:
                        Settings()
                    self.assertNotIn(raw, str(caught.exception))
        settings = replace(Settings(), grok_input_usd_per_mtok=1, grok_output_usd_per_mtok=None)
        self.assertIsNone(settings.provider_token_prices("grok"))
        self.assertFalse(settings.provider_price_readiness()["grok"])
        self.assertTrue(all(type(value) is bool for value in settings.provider_price_readiness().values()))

    def test_engine_reserves_maximum_floor_or_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = object.__new__(OrchestrationEngine)
            engine.settings = priced("grok", grok_daily_budget_usd=10)
            engine.provider_spend = ProviderDailySpendLedger(str(Path(directory) / "spend.sqlite3"))
            assignment = Assignment(sequence=1, agent=ProviderName.GROK, task="Answer", acceptance=["Evidence"])
            bound = assignment_request_cost_bound(engine.settings, assignment, "context")
            for index, (floor, estimate) in enumerate(((0.01, 0), (0.5, 0), (0.01, 0.7))):
                engine.settings = replace(engine.settings, grok_request_cost_floor_usd=floor)
                current = assignment.model_copy(update={"estimated_cost_usd": estimate})
                engine._reserve_provider_daily_spend(current, effect_key=key(index), context="context")
                self.assertEqual(dict(engine.provider_spend.reservations(ProviderName.GROK))[key(index)],
                                 max(bound, floor, estimate))

    def test_overlapping_admissions_and_actual_maxima_stay_under_cap(self):
        for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                ledger = ProviderDailySpendLedger(str(Path(directory) / "spend.sqlite3"))
                maximum = priced(provider.value).token_request_cost_bound_usd(
                    provider.value, {"input": "bounded"}, 4096)
                budget = maximum * 3.5
                barrier = threading.Barrier(12)

                def admit(index):
                    barrier.wait(timeout=15)
                    try:
                        ledger.reserve(provider=provider, effect_key=key(index), estimated_cost_usd=maximum,
                                       request_limit=100, budget_usd=budget)
                        return index
                    except ProviderDailyLimitExceeded:
                        return None

                with ThreadPoolExecutor(max_workers=12) as pool:
                    admitted = [index for index in pool.map(admit, range(12)) if index is not None]
                self.assertEqual(len(admitted), 3)
                for index in admitted:
                    ledger.record_charge(provider=provider, effect_key=key(index), actual_cost_usd=maximum)
                self.assertLessEqual(ledger.exposure(provider)[1], budget)

    def test_serialization_is_atomic_and_known_settlement_releases_capacity(self):
        for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                ledger = ProviderDailySpendLedger(str(Path(directory) / "spend.sqlite3"))
                barrier = threading.Barrier(8)

                def admit(index):
                    barrier.wait(timeout=15)
                    try:
                        ledger.reserve(provider=provider, effect_key=key(index), estimated_cost_usd=0,
                                       request_limit=100, budget_usd=1, serialize=True)
                        return index
                    except ProviderDailyLimitExceeded as exc:
                        self.assertEqual(exc.code, "provider_spend_pending_retryable")
                        return None

                with ThreadPoolExecutor(max_workers=8) as pool:
                    admitted = [index for index in pool.map(admit, range(8)) if index is not None]
                self.assertEqual(len(admitted), 1)
                effect = key(admitted[0])
                self.assertEqual(ledger.exposure(provider), (1, 1))
                # Idempotent replay, with changed accounting still rejected.
                ledger.reserve(provider=provider, effect_key=effect, estimated_cost_usd=0,
                               request_limit=100, budget_usd=1, serialize=True)
                with self.assertRaises(ProviderReservationIdentityConflict):
                    ledger.reserve(provider=provider, effect_key=effect, estimated_cost_usd=0.1,
                                   request_limit=100, budget_usd=1, serialize=True)
                ledger.record_charge(provider=provider, effect_key=effect, actual_cost_usd=0.25)
                self.assertEqual(ledger.exposure(provider), (1, 0.25))
                ledger.reserve(provider=provider, effect_key=key("next"), estimated_cost_usd=0,
                               request_limit=100, budget_usd=1, serialize=True)
                self.assertEqual(dict(ledger.reservations(provider))[key("next")], 0.75)

    def test_priced_engine_admits_overlapping_requests_only_under_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = object.__new__(OrchestrationEngine)
            assignment = Assignment(sequence=1, agent=ProviderName.GROK, task="Answer", acceptance=["Evidence"])
            settings = priced("grok", grok_request_cost_floor_usd=0.0001)
            maximum = assignment_request_cost_bound(settings, assignment, "context")
            engine.settings = replace(settings, grok_daily_budget_usd=maximum * 2.5)
            engine.provider_spend = ProviderDailySpendLedger(str(Path(directory) / "spend.sqlite3"))
            barrier = threading.Barrier(4)

            def admit(index):
                barrier.wait(timeout=15)
                try:
                    engine._reserve_provider_daily_spend(assignment, effect_key=key(index), context="context")
                    return True
                except ProviderDailyLimitExceeded:
                    return False

            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(sum(pool.map(admit, range(4))), 2)
            self.assertAlmostEqual(engine.provider_spend.exposure(ProviderName.GROK)[1], maximum * 2)

    def test_unknown_or_stale_charge_never_frees_unpriced_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderDailySpendLedger(str(Path(directory) / "spend.sqlite3"))
            now = datetime.now(timezone.utc)
            ledger.reserve(provider=ProviderName.GROK, effect_key=key("first"), estimated_cost_usd=0,
                           request_limit=100, budget_usd=1, serialize=True, now=now)
            for serialized in (False, True):
                with self.assertRaisesRegex(ProviderDailyLimitExceeded, "retry after settlement or reconciliation"):
                    ledger.reserve(provider=ProviderName.GROK, effect_key=key("next"), estimated_cost_usd=0.1,
                                   request_limit=100, budget_usd=1, serialize=serialized, now=now)
            ledger.record_charge(provider=ProviderName.GROK, effect_key=key("first"), actual_cost_usd=None)
            self.assertEqual(ledger.exposure(ProviderName.GROK), (1, 1))
            self.assertEqual(ledger.unreconciled(ProviderName.GROK, stale_after=timedelta(hours=1)), [key("first")])

    def test_known_reservation_settles_lower_and_counts_during_unpriced_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderDailySpendLedger(str(Path(directory) / "spend.sqlite3"))
            ledger.reserve(provider=ProviderName.GROK, effect_key=key("priced"), estimated_cost_usd=0.8,
                           request_limit=10, budget_usd=1)
            ledger.reserve(provider=ProviderName.GROK, effect_key=key("unpriced"), estimated_cost_usd=0,
                           request_limit=10, budget_usd=1, serialize=True)
            self.assertAlmostEqual(dict(ledger.reservations(ProviderName.GROK))[key("unpriced")], 0.2)
            ledger.record_charge(provider=ProviderName.GROK, effect_key=key("unpriced"), actual_cost_usd=0)
            ledger.record_charge(provider=ProviderName.GROK, effect_key=key("priced"), actual_cost_usd=0.2)
            ledger.record_charge(provider=ProviderName.GROK, effect_key=key("priced"), actual_cost_usd=0.1)
            self.assertEqual(ledger.exposure(ProviderName.GROK), (2, 0.2))
            ledger.reserve(provider=ProviderName.GROK, effect_key=key("next"), estimated_cost_usd=0.8,
                           request_limit=10, budget_usd=1)
            self.assertEqual(ledger.exposure(ProviderName.GROK), (3, 1))

    def test_engine_missing_prices_uses_serialization_for_every_metered_provider(self):
        for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                engine = object.__new__(OrchestrationEngine)
                engine.settings = replace(Settings(), **{
                    f"{provider.value}_input_usd_per_mtok": None,
                    f"{provider.value}_output_usd_per_mtok": None,
                    f"{provider.value}_daily_budget_usd": 1,
                })
                engine.provider_spend = ProviderDailySpendLedger(str(Path(directory) / "spend.sqlite3"))
                assignment = Assignment(sequence=1, agent=provider, task="Answer", acceptance=["Evidence"],
                                        estimated_cost_usd=0)
                engine._reserve_provider_daily_spend(assignment, effect_key=key("first"), context="context")
                self.assertEqual(engine.provider_spend.exposure(provider), (1, 1))
                with self.assertRaises(ProviderDailyLimitExceeded):
                    engine._reserve_provider_daily_spend(assignment, effect_key=key("next"), context="context")


class UnpricedRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = routing.PacketRuntimeRoutingTests.setUp
    _settings = routing.PacketRuntimeRoutingTests._settings
    _record = routing.PacketRuntimeRoutingTests._record
    _engine = routing.PacketRuntimeRoutingTests._engine

    async def test_late_readback_settles_unpriced_request_after_day_and_price_change(self):
        result = copy.deepcopy(self.result)
        result["provider"] = "gemini"
        transport = routing.LateReadbackTransport(result)
        consumer = routing.RecordingResultConsumer()
        settings = self._settings(
            langgraph_runtime_enabled=True, langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            gemini_input_usd_per_mtok=None, gemini_output_usd_per_mtok=None,
            gemini_daily_budget_usd=1.0,
        )
        router = routing.StubPacketTaskRouter(self.work, PacketBoundaryAdapter("gemini", transport))
        activation = RuntimeActivation(settings, packet_task_router=router, packet_result_consumer=consumer)
        self.addCleanup(activation.close)
        engine, store = self._engine(settings, activation)
        engine._publish_delegation = AsyncMock(return_value=True)
        record, assignment = self._record(agent=ProviderName.GEMINI)
        store.create(record)
        self.assertFalse(await engine._route_live_dispatch(record, assignment))
        self.assertEqual(engine.provider_spend.exposure(ProviderName.GEMINI), (1, 1.0))
        with self.assertRaises(ProviderDailyLimitExceeded):
            engine._reserve_provider_daily_spend(assignment, effect_key=key("different"))
        late = store.get(record.task_id)
        late.status = TaskStatus.RUNNING
        late.provider_runs.append(ProviderRun(
            provider=ProviderName.GEMINI, assignment_sequence=assignment.sequence,
            interaction_id=result["correlation"]["correlation_id"],
            run_id=result["run_reference"], status="completed", result=result["outcome"],
            evidence_source="provider_authenticated", raw={"usage": {"cost": 0.25}},
        ))
        store.save(late)
        engine.settings = replace(settings, gemini_input_usd_per_mtok=1, gemini_output_usd_per_mtok=1)
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        with patch("director_orchestrator.engine.datetime", wraps=datetime) as clock:
            clock.now.return_value = tomorrow
            self.assertIsNone(await engine._route_live_dispatch(store.get(record.task_id), assignment))
        self.assertEqual(transport.sends, 1)
        self.assertEqual(consumer.calls, 1)
        self.assertEqual(engine.provider_spend.exposure(ProviderName.GEMINI), (1, 0.25))
        engine._reserve_provider_daily_spend(assignment, effect_key=key("different"))

    def test_owner_not_created_reconciliation_releases_only_its_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = settlement.ProviderSpendSettlementTests()._world(directory)
            engine = OrchestrationEngine(settings, store)
            effect = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            record_provider_effect_observation(record, assignment, effect_key=effect,
                                               phase="dispatch", outcome="ambiguous")
            record.provider_contact_attempts.append(ProviderContactAttempt(
                assignment_sequence=assignment.sequence, provider=assignment.agent,
                ordinal=1, phase="dispatch", outcome="ambiguous_dispatch",
                error="Transport outcome unknown",
            ))
            record.status = TaskStatus.OWNER_DECISION_REQUIRED
            engine._reserve_provider_daily_spend(assignment, effect_key=effect)
            engine._charge_spend(assignment.agent, effect, None)
            engine.provider_spend.reserve(provider=ProviderName.GROK, effect_key=key("unrelated"),
                                          estimated_cost_usd=0.5, request_limit=50, budget_usd=1)
            with self.assertRaises(ProviderDailyLimitExceeded):
                engine._reserve_provider_daily_spend(assignment, effect_key=key("next"))
            resolution = AmbiguousDispatchResolution(
                owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                resolution="not_created", note="Provider confirmed no request was created",
            )
            engine.apply_dispatch_reconciliation(record, resolution)
            store.save(record)
            # Reopen after the task commit, before settlement: recovery is durable.
            restarted = OrchestrationEngine(settings, store)
            restarted._settle_reconciled_spend(store.get(record.task_id))
            restarted._settle_reconciled_spend(store.get(record.task_id))
            self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, 0))
            self.assertEqual(restarted.provider_spend.exposure(ProviderName.GROK), (1, 0.5))
            record = store.get(record.task_id)
            next_effect = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            self.assertNotEqual(next_effect, effect)
            restarted._reserve_provider_daily_spend(assignment, effect_key=next_effect)
            self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (2, 1))

    def test_owner_created_resolution_keeps_hold_until_terminal_settlement(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = settlement.ProviderSpendSettlementTests()._world(directory)
            engine = OrchestrationEngine(settings, store)
            effect = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            record_provider_effect_observation(record, assignment, effect_key=effect,
                                               phase="dispatch", outcome="ambiguous")
            record.provider_contact_attempts.append(ProviderContactAttempt(
                assignment_sequence=assignment.sequence, provider=assignment.agent,
                ordinal=1, phase="dispatch", outcome="ambiguous_dispatch", error="Unknown result",
            ))
            record.status = TaskStatus.OWNER_DECISION_REQUIRED
            engine._reserve_provider_daily_spend(assignment, effect_key=effect)
            engine.apply_dispatch_reconciliation(record, AmbiguousDispatchResolution(
                owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                resolution="created", run_id="recovered-run", note="Provider confirmed creation",
            ))
            engine._settle_reconciled_spend(record)
            with self.assertRaises(ProviderDailyLimitExceeded):
                engine._reserve_provider_daily_spend(assignment, effect_key=key("next"))
            terminal = record.provider_runs[-1].model_copy(update={
                "status": "completed", "raw": {"usage": {"cost": 0.25}},
            })
            engine._settle_provider_spend(record, terminal, assignment.agent)
            engine._reserve_provider_daily_spend(assignment, effect_key=key("next"))
            self.assertEqual(dict(engine.provider_spend.reservations(assignment.agent))[key("next")], 0.75)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 11:15 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 11:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 12:31 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 17:13 UTC | END]]
