# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:41 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:54 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 17:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 17:14 UTC | BEGIN]]
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
import json
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
    async def test_acknowledgement_survives_first_task_save_failure(self):
        from director_orchestrator.approvals import StaleApproval
        from director_orchestrator.models import AmbiguousDispatchResolution, ReviewDecision, ReviewVerdict
        from director_orchestrator.store import StaleTaskWrite

        for phase in ("dispatch", "revision"):
            for failure in (StaleTaskWrite, sqlite3.OperationalError):
                with self.subTest(phase=phase, failure=failure.__name__), tempfile.TemporaryDirectory() as directory:
                    settings, store, record, assignment = self._world(directory, max_hops=4, max_revisions=1)
                    engine = OrchestrationEngine(replace(settings, openai_api_key=""), store)
                    engine._status_post = AsyncMock()
                    adapter = AsyncMock()
                    adapter.dispatch.return_value = ProviderRun(
                        provider=assignment.agent, run_id="initial", status="completed",
                        result="private result marker", raw={"payload": "private raw marker", "usage": {"cost": 0.25}})
                    adapter.revise.return_value = adapter.dispatch.return_value.model_copy(update={"run_id": "revision"})
                    engine.providers[assignment.agent] = adapter
                    engine.director.review = AsyncMock(return_value=ReviewDecision(
                        verdict=ReviewVerdict.REVISE, summary="More evidence", revision_prompt="Add evidence"))
                    target = "initial" if phase == "dispatch" else "revision"
                    real_save = store.save

                    def fail_ack(candidate):
                        if any(run.run_id == target for run in candidate.provider_runs):
                            raise failure("injected task save failure")
                        return real_save(candidate)

                    with patch.object(store, "save", side_effect=fail_ack):
                        with self.assertRaises(failure):
                            await engine._execute_assignment(record, assignment)
                    restarted = OrchestrationEngine(settings, store)
                    recovered = store.get(record.task_id)
                    held = next(value for value in restarted.provider_spend.settlements(record.task_id).values()
                                if value["run_id"] == target)
                    self.assertTrue(held["pending"])
                    self.assertEqual(held["phase"], phase)
                    self.assertEqual(held["cost"], 0.25)
                    self.assertNotIn("private", json.dumps(held))
                    with self.assertRaises(StaleApproval):
                        restarted.apply_dispatch_reconciliation(recovered, AmbiguousDispatchResolution(
                            owner_user_id=recovered.intake.user_id, assignment_sequence=assignment.sequence,
                            resolution="not_created", note="Contradictory reconciliation"))
                    self.assertTrue(restarted._retry_spend_settlements(recovered))
                    self.assertEqual(recovered.provider_runs[-1].run_id, target)
                    self.assertFalse(recovered.provider_runs[-1].result)
                    self.assertEqual(recovered.revision_count, int(phase == "revision"))
                    self.assertTrue(any(item.outcome == "submitted" and item.phase == phase
                                        for item in recovered.task_effect_ledger))
                    self.assertTrue(all(not item["pending"] for item in restarted.provider_spend.settlements(record.task_id).values()))
                    adapter.dispatch.assert_awaited_once()
                    self.assertEqual(adapter.revise.await_count, int(phase == "revision"))
                    adapter.poll.assert_not_awaited()

    def test_journal_content_is_compacted_on_recovery_and_owner_reset(self):
        from director_orchestrator.approvals import reserve_provider_effect, reset_for_owner_clarification

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                              run_id="privacy-run", status="completed", result="private result marker",
                              raw={"payload": "private raw marker"})
            with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")):
                self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                     record=record, assignment=assignment, run=run))
            held = engine.provider_spend.settlements(record.task_id)[key]
            self.assertNotIn("private", json.dumps(held))
            self.assertFalse({"run", "raw", "result", "packet_outcome"}.intersection(held))
            engine.provider_spend.save_settlement(record.task_id, key, {
                **held, "run": run.model_dump(mode="json"), "raw": run.raw, "result": run.result,
            })
            self.assertEqual(engine.provider_spend.settlements(record.task_id)[key], held)
            self.assertTrue(engine._retry_spend_settlements(record))
            self.assertFalse(engine.provider_spend.settlements(record.task_id)[key]["pending"])
            # Simulate the pre-upgrade format, including a pending charge that
            # must survive cleanup without restoring erased provider content.
            held.update(pending=True, run=run.model_dump(mode="json"), result=run.result,
                        packet_outcome={"result_packet": {"outcome": run.result}})
            with engine.provider_spend._connect() as connection:
                connection.execute("UPDATE engine_spend_settlements SET evidence = ? WHERE task_id = ?",
                                   (json.dumps(held), record.task_id))
            reset_for_owner_clarification(record)
            store.save(record)
            self.assertFalse(store.privacy_cleanup_pending())
            self.assertNotIn("private", json.dumps(engine.provider_spend.settlements(record.task_id)))
            restarted = OrchestrationEngine(settings, TaskStore(settings.sqlite_path))
            clean = restarted.store.get(record.task_id)
            self.assertTrue(restarted._retry_spend_settlements(clean))
            self.assertEqual(clean.provider_runs, [])
            self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, 0.25))
            self.assertNotIn(b"private result marker", Path(settings.sqlite_path).read_bytes())
            self.assertNotIn(b"private raw marker", Path(settings.sqlite_path).read_bytes())

    def test_stale_hold_does_not_restore_content_after_owner_reset(self):
        from director_orchestrator.approvals import reserve_provider_effect, reset_for_owner_clarification

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            store.save(record)
            reset = store.get(record.task_id)
            reset_for_owner_clarification(reset)
            store.save(reset)
            run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                              run_id="stale-run", status="completed", result="private stale content")
            with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")):
                self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                     record=record, assignment=assignment, run=run))
            current = store.get(record.task_id)
            self.assertIsNone(current.plan)
            self.assertEqual(current.provider_runs, [])
            self.assertTrue(engine._retry_spend_settlements(current))
            self.assertEqual(store.get(record.task_id).provider_runs, [])
            self.assertNotIn("private stale content", json.dumps(engine.provider_spend.settlements(record.task_id)))

    async def test_spend_pause_finalizes_real_workload_reservation(self):
        from director_orchestrator.store import WorkloadAttemptOutcome
        from director_orchestrator.workload_policy import EnforcementMode, Provider, WorkKind, WorkSize

        for ambiguous in (False, True):
            with self.subTest(ambiguous=ambiguous), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                engine = OrchestrationEngine(settings, store)
                engine._status_post = AsyncMock()
                engine._workload_policy_applies = lambda _: True
                reserved = []

                async def reserve(current, job, *, phase, effect_key):
                    result = store.reserve_workload_assignment(
                        reservation_key=f"{current.task_id}:{effect_key}", task_id=current.task_id,
                        assignment_id=f"{current.task_id}:0:{job.sequence}", phase=phase,
                        provider=Provider(job.agent.value), effort_units=1, kind=WorkKind.COORDINATION,
                        size=WorkSize.SIMPLE, model_label=None, available_primary_providers=frozenset({Provider.OPENAI, Provider.CLAUDE, Provider.CURSOR, Provider.GROK}),
                        mode=EnforcementMode.ENFORCED, shadow_assignment_limit=5,
                    )
                    self.assertTrue(result.decision.allowed, result.decision.violations)
                    reserved.append(result.reservation_key)
                    return result

                engine._reserve_workload_dispatch = reserve
                adapter = AsyncMock()
                adapter.dispatch.return_value = ProviderRun(
                    provider=assignment.agent, run_id="acknowledged", status="completed", result="evidence")
                if ambiguous:
                    adapter.dispatch.side_effect = RuntimeError("ambiguous submission")
                engine.providers[assignment.agent] = adapter
                with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")):
                    self.assertFalse(await engine._execute_assignment(record, assignment))
                self.assertEqual(len(reserved), 1)
                self.assertEqual(store.get_workload_reservation(reserved[0]).final_outcome, WorkloadAttemptOutcome.INVOKED)
                stopped = store.get(record.task_id)
                self.assertTrue(stopped.error.startswith("spend_settlement_failed:"))
                adapter.dispatch.assert_awaited_once()
                if not ambiguous:
                    restarted = OrchestrationEngine(settings, store)
                    self.assertTrue(restarted._retry_spend_settlements(stopped))
                    self.assertEqual(store.get_workload_reservation(reserved[0]).final_outcome, WorkloadAttemptOutcome.INVOKED)

    def test_hostile_raw_settlements_cannot_charge_or_replace_packet_recovery(self):
        from director_orchestrator.approvals import reserve_provider_effect

        for hostile in ({"pending": True, "cost": 999, "run_id": "real-run"},
                        {"pending": False, "cost": 0, "run_id": "different-run"}, "malformed"):
            with self.subTest(hostile=hostile), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                engine = OrchestrationEngine(settings, store)
                effect = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=effect)
                forged = _key("forged-effect")
                raw = {"spend_settlements": {forged: hostile, effect: hostile},
                       "settlement_result_packet": {"run_reference": "forged"}, "settlement_packet_only": True}
                run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                                  run_id="real-run", status="completed", result="real evidence", raw=raw)
                record.provider_runs.append(run)
                store.save(record)
                with patch.object(engine.provider_spend, "record_charge", wraps=engine.provider_spend.record_charge) as charge:
                    self.assertTrue(engine._retry_spend_settlements(record))
                    charge.assert_not_called()
                with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")):
                    self.assertFalse(engine._charge_spend(assignment.agent, effect, 0.25,
                                                         record=record, assignment=assignment, run=run))
                restarted = OrchestrationEngine(settings, store)
                held = store.get(record.task_id)
                with patch.object(restarted.provider_spend, "record_charge", wraps=restarted.provider_spend.record_charge) as charge:
                    self.assertTrue(restarted._retry_spend_settlements(held))
                    self.assertEqual(charge.call_count, 1)
                    self.assertEqual(charge.call_args.kwargs["effect_key"], effect)
                    self.assertEqual(charge.call_args.kwargs["actual_cost_usd"], 0.25)
                self.assertIsNone(restarted.provider_spend.settlements(held.task_id)[effect]["result_packet_digest"])
                restarted._prepare_packet_consumption(held, effect)
                self.assertEqual(len(held.provider_runs), 1)
                self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, 0.25))

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
                                self.assertTrue(engine._settle_packet_spend(record, assignment, key, outcome))
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
                    settings = replace(settings, openai_api_key="")
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
                    if failure != "ambiguous_submit":
                        expected = original if phase == "dispatch" else revised
                        self.assertEqual(stopped.provider_runs[-1].run_id, expected.run_id)
                        self.assertEqual(stopped.provider_runs[-1].result, expected.result)
                        self.assertTrue(engine._spend_settlements(stopped, stopped.provider_runs[-1])[key]["pending"])
                        self.assertFalse(any(item.outcome == "ambiguous_dispatch"
                                             for item in stopped.provider_contact_attempts))
                    else:
                        self.assertEqual(stopped.provider_contact_attempts[-1].outcome, "ambiguous_dispatch")
                    self.assertFalse(await engine._execute_assignment(stopped, assignment))
                    await engine.run(record.task_id)
                    recovered = store.get(record.task_id)
                    if failure != "ambiguous_submit":
                        self.assertFalse(engine._spend_settlements(recovered, recovered.provider_runs[-1])[key]["pending"])
                        # Exercise the preserved outcome through ordinary acceptance
                        # after run() has repaired accounting, with no new provider call.
                        engine.director.review = AsyncMock(side_effect=_ReviewOutOfScope())
                        with self.assertRaises(_ReviewOutOfScope):
                            await engine._execute_assignment(recovered, assignment)
                        self.assertEqual(engine.director.review.call_args.args[1], expected.result)
                    else:
                        self.assertTrue(recovered.error.startswith("spend_settlement_failed:"))
                    adapter.dispatch.assert_awaited_once()
                    self.assertEqual(adapter.revise.await_count, int(phase == "revision"))

    async def test_workload_finalization_failure_retains_submitted_run_and_spend(self):
        from director_orchestrator.models import ReviewDecision, ReviewVerdict

        for phase in ("dispatch", "revision"):
            for failure in (False, RuntimeError("workload storage unavailable")):
                with self.subTest(phase=phase, failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                    settings, store, record, assignment = self._world(directory, max_hops=4, max_revisions=1)
                    settings = replace(settings, openai_api_key="")
                    engine = OrchestrationEngine(settings, store)
                    engine._status_post = AsyncMock()
                    adapter = AsyncMock()
                    adapter.dispatch.return_value = ProviderRun(
                        provider=assignment.agent, run_id="initial", status="completed",
                        result="initial evidence", raw={"usage": {"cost": 0.1}})
                    adapter.revise.return_value = ProviderRun(
                        provider=assignment.agent, run_id="revised", status="completed",
                        result="revised evidence", raw={"usage": {"cost": 0.2}})
                    engine.providers[assignment.agent] = adapter
                    engine.director.review = AsyncMock(return_value=ReviewDecision(
                        verdict=ReviewVerdict.REVISE, summary="Need evidence",
                        revision_prompt="Add independent fixture evidence",
                        retry_basis={
                            "failed_acceptance_indexes": [0],
                            "prior_result_digest": _key("initial evidence"),
                            "corrected_approach": "Add independent fixture evidence",
                            "evidence_refs": [{"acceptance_index": 0, "evidence_index": 0,
                                               "evidence_digest": _key("new fixture")}],
                        }))
                    engine._workload_policy_applies = lambda _: True
                    engine._reserve_workload_dispatch = AsyncMock(return_value=object())
                    outcomes = [failure] if phase == "dispatch" else [True, failure]
                    engine._finalize_workload_dispatch = AsyncMock(side_effect=outcomes)
                    with patch("director_orchestrator.engine.evaluate_retry", return_value=SimpleNamespace(violations=())):
                        if isinstance(failure, Exception):
                            with self.assertRaises(RuntimeError):
                                await engine._execute_assignment(record, assignment)
                        else:
                            self.assertFalse(await engine._execute_assignment(record, assignment))
                    saved = store.get(record.task_id)
                    submitted = saved.provider_runs[-1]
                    self.assertEqual(submitted.run_id, "initial" if phase == "dispatch" else "revised")
                    self.assertEqual(submitted.evidence_source, "provider_authenticated")
                    effects = [item.effect_key for item in saved.task_effect_ledger
                               if item.kind == "provider_intent" and item.phase == phase]
                    self.assertEqual(len(effects), 1)
                    # Settled without a known charge remains unreconciled immediately,
                    # even before the stale-reservation timeout has elapsed.
                    self.assertEqual(engine.provider_spend.unreconciled(assignment.agent, stale_after=HOUR), effects)
                    adapter.dispatch.assert_awaited_once()
                    self.assertEqual(adapter.revise.await_count, int(phase == "revision"))
                    self.assertEqual(len(saved.provider_runs), 1 + int(phase == "revision"))

    def test_hold_cas_merges_current_record_and_binds_reconciliation(self):
        from director_orchestrator.approvals import reserve_provider_effect, StaleApproval
        from director_orchestrator.models import AmbiguousDispatchResolution

        for stale_writes in (1, 2):
            with self.subTest(stale_writes=stale_writes), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                store.save(record)
                run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                                  run_id="acknowledged-run", status="completed", result="preserved evidence")
                real_save = store.save
                conflicts = 0

                def race(candidate):
                    nonlocal conflicts
                    if conflicts < stale_writes:
                        conflicts += 1
                        current = store.get(record.task_id)
                        current.revision_count = conflicts
                        real_save(current)
                    return real_save(candidate)

                with patch.object(store, "save", side_effect=race), patch.object(
                    engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")
                ):
                    self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                         record=record, assignment=assignment, run=run))
                held = store.get(record.task_id)
                self.assertEqual(held.revision_count, stale_writes)
                self.assertEqual(held.provider_runs[-1].result, run.result)
                for resolution, run_id in (("created", "different-run"), ("not_created", None)):
                    with self.assertRaises(StaleApproval):
                        engine.apply_dispatch_reconciliation(held, AmbiguousDispatchResolution(
                            owner_user_id=held.intake.user_id, assignment_sequence=assignment.sequence,
                            resolution=resolution, run_id=run_id, note="Owner supplied reconciliation evidence"))
                engine.apply_dispatch_reconciliation(held, AmbiguousDispatchResolution(
                    owner_user_id=held.intake.user_id, assignment_sequence=assignment.sequence,
                    resolution="created", run_id=run.run_id, note="Owner confirmed the same request"))
                self.assertEqual(len(held.provider_runs), 1)
                self.assertTrue(engine._retry_spend_settlements(held))
                self.assertIsNone(held.error)
                self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 0.25))

    def test_charge_commit_before_hold_clear_is_idempotent(self):
        from director_orchestrator.approvals import reserve_provider_effect
        from director_orchestrator.store import StaleTaskWrite

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                              run_id="retained-run", status="completed", result="retained result")
            with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")):
                self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                     record=record, assignment=assignment, run=run))
            with patch.object(store, "save", side_effect=StaleTaskWrite("concurrent update")):
                self.assertFalse(engine._retry_spend_settlements(store.get(record.task_id)))
            restarted = OrchestrationEngine(settings, store)
            held = store.get(record.task_id)
            self.assertTrue(engine._spend_settlements(held, held.provider_runs[-1])[key]["pending"])
            self.assertTrue(restarted._retry_spend_settlements(held))
            self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, 0.25))
            self.assertEqual(held.provider_runs[-1].result, "retained result")

    def test_recovery_journal_survives_task_hold_write_failure(self):
        from director_orchestrator.approvals import reserve_provider_effect

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            store.save(record)
            run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                              run_id="journal-run", status="completed", result="durable evidence")
            with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")), patch.object(
                store, "save", side_effect=sqlite3.OperationalError("task write failed")
            ):
                self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                     record=record, assignment=assignment, run=run))
            restarted = OrchestrationEngine(settings, store)
            recovered = store.get(record.task_id)
            self.assertEqual(recovered.provider_runs, [])
            self.assertTrue(restarted._retry_spend_settlements(recovered))
            restored = store.get(record.task_id).provider_runs[-1]
            self.assertEqual(restored.run_id, "journal-run")
            self.assertFalse(restored.result)
            self.assertEqual(restored.status, "RECONCILIATION_REQUIRED")
            self.assertTrue(any(item.effect_key == key and item.outcome == "submitted"
                                for item in recovered.task_effect_ledger))
            self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, 0.25))

    def test_multiple_holds_survive_partial_recovery_and_restart(self):
        from director_orchestrator.approvals import reserve_provider_effect

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            for phase, cost in (("dispatch", None), ("revision", 0.25)):
                key = reserve_provider_effect(record, assignment, phase=phase, max_task_budget_usd=10)
                engine.provider_spend.reserve(provider=assignment.agent, effect_key=key,
                                              estimated_cost_usd=0.1, request_limit=2, budget_usd=1)
                run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                                  run_id=phase + "-run", status="completed", result=phase + " evidence")
                with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")):
                    self.assertFalse(engine._charge_spend(assignment.agent, key, cost,
                                                         record=record, assignment=assignment, run=run))
            real_charge = engine.provider_spend.record_charge

            def partial(**kwargs):
                if kwargs["actual_cost_usd"] is not None:
                    raise sqlite3.OperationalError("locked")
                return real_charge(**kwargs)

            with patch.object(engine.provider_spend, "record_charge", side_effect=partial):
                self.assertFalse(engine._retry_spend_settlements(record))
            restarted = OrchestrationEngine(settings, store)
            held = store.get(record.task_id)
            with patch.object(restarted.provider_spend, "record_charge", wraps=restarted.provider_spend.record_charge) as charge:
                self.assertTrue(restarted._retry_spend_settlements(held))
            self.assertEqual(charge.call_count, 1)
            self.assertEqual(charge.call_args.kwargs["actual_cost_usd"], 0.25)
            self.assertIsNone(held.error)
            self.assertEqual([run.result for run in held.provider_runs], ["dispatch evidence", "revision evidence"])

    def test_preexisting_acknowledged_hold_recovers_without_owner_reconciliation(self):
        from director_orchestrator.approvals import record_provider_effect_observation, reserve_provider_effect
        from director_orchestrator.models import ProviderContactAttempt
        from director_orchestrator.store import WorkloadAttemptOutcome
        from director_orchestrator.workload_policy import EnforcementMode, Provider, WorkKind, WorkSize

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            reservation = store.reserve_workload_assignment(
                reservation_key=f"{record.task_id}:{key}", task_id=record.task_id,
                assignment_id=f"{record.task_id}:0:{assignment.sequence}", phase="dispatch",
                provider=Provider(assignment.agent.value), effort_units=1, kind=WorkKind.COORDINATION,
                size=WorkSize.SIMPLE, model_label=None, available_primary_providers=frozenset({Provider.OPENAI, Provider.CLAUDE, Provider.CURSOR, Provider.GROK}),
                mode=EnforcementMode.ENFORCED, shadow_assignment_limit=5,
            )
            record_provider_effect_observation(record, assignment, effect_key=key,
                                               phase="dispatch", outcome="ambiguous")
            run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                              run_id="old-acknowledged-run", status="completed", result="old preserved evidence",
                              raw={"usage": {"cost": 0.25}})
            record.provider_runs.append(run)
            record.error = "spend_settlement_failed:" + key
            record.status = TaskStatus.OWNER_DECISION_REQUIRED
            record.provider_contact_attempts.append(ProviderContactAttempt(
                assignment_sequence=assignment.sequence, provider=assignment.agent, ordinal=1,
                phase="dispatch", outcome="ambiguous_dispatch", run_id=run.run_id, error=record.error))
            store.save(record)
            self.assertTrue(engine._retry_spend_settlements(record))
            self.assertEqual(record.provider_runs[-1].result, run.result)
            self.assertIsNone(record.error)
            self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 0.25))
            self.assertEqual(store.get_workload_reservation(reservation.reservation_key).final_outcome,
                             WorkloadAttemptOutcome.INVOKED)

    def test_priced_gemini_refuses_second_unsettled_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, store, _, assignment = self._world(
                directory, assignment=work_assignment(estimated_cost_usd=0.0))
            settings = replace(settings, gemini_input_usd_per_mtok=1.0,
                               gemini_output_usd_per_mtok=1.0)
            engine = OrchestrationEngine(settings, store)
            engine._reserve_provider_daily_spend(
                assignment, effect_key=_key("priced-gemini-first"), context="bounded test request")
            with self.assertRaises(ProviderDailyLimitExceeded) as caught:
                engine._reserve_provider_daily_spend(
                    assignment, effect_key=_key("priced-gemini-second"), context="bounded test request")
            self.assertEqual(caught.exception.code, "provider_spend_pending_retryable")

    def test_not_created_settles_only_exact_reservation_once(self) -> None:
        from director_orchestrator.approvals import (
            record_provider_effect_observation, reserve_provider_effect,
        )
        from director_orchestrator.models import AmbiguousDispatchResolution, ProviderContactAttempt

        for phase, crash in (("dispatch", False), ("revision", False), ("dispatch", True), ("revision", True)):
            with self.subTest(phase=phase, crash=crash), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(
                    directory, assignment=Assignment(
                        sequence=1, agent=ProviderName.GROK, task="Answer one bounded question",
                        acceptance=["An answer is returned"], estimated_cost_usd=0.0))
                settings = replace(settings, grok_input_usd_per_mtok=1.0,
                                   grok_output_usd_per_mtok=1.0, grok_daily_budget_usd=1.0,
                                   grok_daily_request_limit=50, grok_request_cost_floor_usd=0.01)
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
                            self.assertFalse(engine._settle_reconciled_spend(record))
                        # Returning normally lets dispatch-reconciliation schedule run().
                        self.assertEqual(store.get(record.task_id).status, TaskStatus.QUEUED)
                    self.assertTrue(engine._settle_reconciled_spend(store.get(record.task_id)))
                    self.assertTrue(engine._settle_not_created_dispatches(store.get(record.task_id)))
                self.assertEqual(charge.call_count, 2 if crash else 1)
                self.assertEqual(charge.call_args.kwargs["effect_key"], key)
                self.assertEqual(charge.call_args.kwargs["actual_cost_usd"], 0.0)
                self.assertEqual(engine.provider_spend.unreconciled(assignment.agent, stale_after=HOUR), [other])
                self.assertEqual(len(engine.provider_spend.reservations(assignment.agent, include_released=False)), 1)
                # Upgrade from a schema without released_at: the task already has
                # its no-effect receipt and the spend row already has zero cost.
                with engine.provider_spend._connect() as db:
                    db.execute("UPDATE provider_daily_spend_reservations SET released_at = NULL WHERE effect_key = ?", (key,))
                self.assertEqual(len(engine.provider_spend.reservations(assignment.agent, include_released=False)), 2)
                self.assertTrue(engine._settle_not_created_dispatches(store.get(record.task_id)))
                self.assertEqual(len(engine.provider_spend.reservations(assignment.agent, include_released=False)), 1)
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

    def test_missing_intent_charge_failure_preserves_hold_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = _key("missing-intent")
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            run = ProviderRun(provider=assignment.agent, run_id="known-run", status="completed")
            with patch.object(engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")):
                self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                     record=record, assignment=assignment, run=run))
            saved = store.get(record.task_id)
            self.assertEqual(saved.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(saved.error, "spend_settlement_failed:" + key)
            self.assertIn("intent is missing", saved.message)
            held = engine.provider_spend.settlements(record.task_id)[key]
            self.assertTrue(held["pending"])
            self.assertEqual((held["run_id"], held["cost"]), ("known-run", 0.25))
            restarted = OrchestrationEngine(settings, store)
            with patch.object(restarted.provider_spend, "record_charge") as charge:
                self.assertFalse(restarted._retry_spend_settlements(saved))
            charge.assert_not_called()
            self.assertEqual(saved.provider_runs, [])

    def test_sqlite_helpers_close_connections_on_success_and_failure(self):
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as directory:
            settings, store, _, _ = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            for owner in (store, engine.provider_spend):
                for failure in (None, "setup", "body"):
                    with self.subTest(owner=type(owner).__name__, failure=failure):
                        connection = MagicMock()
                        if failure == "setup":
                            connection.execute.side_effect = sqlite3.OperationalError("locked")
                        with patch("sqlite3.connect", return_value=connection):
                            if failure is None:
                                with owner._connect() as opened:
                                    self.assertIs(opened, connection)
                            else:
                                with self.assertRaises(sqlite3.OperationalError):
                                    with owner._connect():
                                        raise sqlite3.OperationalError("locked")
                        connection.close.assert_called_once_with()

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
            self.assertTrue(engine._settle_packet_spend(record, assignment, key, outcome))
            held = engine.provider_spend.settlements(record.task_id)[key]
            self.assertEqual((held["provider"], held["run_id"], held["cost"], held["pending"]),
                             (assignment.agent.value, "packet-run", 0.25, False))
            for field in ("phase", "assignment_sequence", "assignment_digest", "plan_generation"):
                self.assertIsNone(held[field])
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
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 17:14 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 17:35 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:54 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:16 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:41 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:01 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:05 UTC | END]]
