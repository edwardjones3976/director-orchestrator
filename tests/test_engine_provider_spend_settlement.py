# [[OpenAI Codex | GPT-6 | 2026-09-28 | 01:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 01:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 22:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:21 UTC | BEGIN]]
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
    def test_settlement_does_not_charge_other_dispatch_identities(self):
        from director_orchestrator.approvals import (
            apply_ambiguous_dispatch_resolution, record_provider_effect_observation,
            reserve_provider_effect,
        )
        from director_orchestrator.models import AmbiguousDispatchResolution, ProviderContactAttempt

        for reused_id in (False, True):
            with self.subTest(reused_id=reused_id), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                record.plan_generation += 1
                engine = OrchestrationEngine(settings, store)
                stale_keys = []
                history = []
                for dimension in ("generation", "assignment", "phase", "provider"):
                    previous = record.model_copy(deep=True)
                    previous.task_effect_ledger = []
                    prior_assignment = assignment.model_copy(deep=True)
                    phase = "dispatch"
                    if dimension == "generation":
                        previous.plan_generation -= 1
                    elif dimension == "assignment":
                        prior_assignment.task = "A different approved assignment"
                    elif dimension == "phase":
                        phase = "revision"
                    else:
                        prior_assignment.agent = ProviderName.PERPLEXITY
                    key = reserve_provider_effect(previous, prior_assignment, phase=phase, max_task_budget_usd=10)
                    record_provider_effect_observation(
                        previous, prior_assignment, effect_key=key, phase=phase,
                        outcome="submitted" if reused_id else "ambiguous",
                        run_id="reused-run" if reused_id else None)
                    history.extend(previous.task_effect_ledger)
                    if dimension != "provider":
                        engine._reserve_provider_daily_spend(prior_assignment, effect_key=key)
                        engine._charge_spend(prior_assignment.agent, key, 0.01)
                        stale_keys.append(key)
                key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                record_provider_effect_observation(record, assignment, effect_key=key,
                                                   phase="dispatch", outcome="ambiguous")
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                record.task_effect_ledger = history + record.task_effect_ledger
                record.provider_contact_attempts.append(ProviderContactAttempt(
                    assignment_sequence=assignment.sequence, provider=assignment.agent,
                    ordinal=1, phase="dispatch", outcome="ambiguous_dispatch", error="Unknown submit outcome"))
                record.status = TaskStatus.OWNER_DECISION_REQUIRED
                apply_ambiguous_dispatch_resolution(record, AmbiguousDispatchResolution(
                    owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                    resolution="created", run_id="reused-run", note="Confirmed existing request"))
                store.save(record)
                # Resume from durable owner reconciliation, without another dispatch.
                record = store.get(record.task_id)
                run = record.provider_runs[-1]
                run.status, run.result, run.raw = "completed", "done", {"usage": {"cost": 0.3}}
                if reused_id:
                    with patch.object(engine.provider_spend, "record_charge",
                                      side_effect=sqlite3.OperationalError("interrupted settlement")):
                        self.assertFalse(engine._settle_provider_spend(record, run, assignment.agent))
                    engine = OrchestrationEngine(settings, store)
                    record = store.get(record.task_id)
                    self.assertTrue(engine._retry_spend_settlements(record))
                    run = record.provider_runs[-1]
                self.assertTrue(engine._settle_provider_spend(record, run, assignment.agent))
                self.assertEqual(engine.provider_spend.unreconciled(assignment.agent, stale_after=HOUR), [])
                self.assertAlmostEqual(engine.provider_spend.exposure(assignment.agent)[1],
                                       0.3 + 0.01 * len(stale_keys))
                self.assertEqual(set(engine.provider_spend.settlements(record.task_id)), {key})

    def test_settlement_missing_or_conflicting_identity_preserves_reservation(self):
        from director_orchestrator.approvals import record_provider_effect_observation, reserve_provider_effect

        for mismatch in ("generation", "provider", "interaction", "phase", "assignment"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                record_provider_effect_observation(record, assignment, effect_key=key,
                                                   phase="dispatch", outcome="submitted", run_id="same-run")
                run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                                  plan_generation=record.plan_generation, run_id="same-run",
                                  status="completed", raw={"usage": {"cost": 0.3}})
                phase = "dispatch"
                if mismatch == "generation":
                    run.plan_generation = None
                elif mismatch == "provider":
                    run.provider = ProviderName.GROK
                elif mismatch == "interaction":
                    run.interaction_id = "missing-engine-interaction"
                elif mismatch == "phase":
                    phase = "revision"
                else:
                    record.plan.assignments[0].task = "Replacement assignment"
                with patch.object(engine.provider_spend, "record_charge") as charge:
                    self.assertFalse(engine._settle_provider_spend(record, run, assignment.agent, phase=phase))
                charge.assert_not_called()
                self.assertEqual(record.status, TaskStatus.OWNER_DECISION_REQUIRED)
                self.assertEqual(engine.provider_spend.settlements(record.task_id), {})

    def test_resumed_revision_uses_its_interaction_or_owner_receipt_phase(self):
        from director_orchestrator.approvals import (
            apply_ambiguous_dispatch_resolution, record_provider_effect_observation, reserve_provider_effect,
        )
        from director_orchestrator.models import AmbiguousDispatchResolution, ProviderContactAttempt

        for owner_reconciled in (False, True):
            with self.subTest(owner_reconciled=owner_reconciled), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory, max_revisions=1)
                engine = OrchestrationEngine(settings, store)
                for index, phase in enumerate(("dispatch", "revision", "revision")):
                    key = reserve_provider_effect(record, assignment, phase=phase, max_task_budget_usd=10)
                    engine._reserve_provider_daily_spend(assignment, effect_key=key)
                    record_provider_effect_observation(
                        record, assignment, effect_key=key, phase=phase,
                        outcome="ambiguous" if owner_reconciled and index == 2 else "submitted",
                        run_id=None if owner_reconciled and index == 2 else "reused-run")
                    if index < 2:
                        engine._charge_spend(assignment.agent, key, 0.01)
                        record.revision_count = index
                if owner_reconciled:
                    record.provider_contact_attempts.append(ProviderContactAttempt(
                        assignment_sequence=assignment.sequence, provider=assignment.agent, ordinal=1,
                        phase="revision", outcome="ambiguous_dispatch", error="Unknown revision outcome"))
                    record.status = TaskStatus.OWNER_DECISION_REQUIRED
                    apply_ambiguous_dispatch_resolution(record, AmbiguousDispatchResolution(
                        owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                        resolution="created", run_id="reused-run", note="Confirmed revision request"))
                else:
                    interaction = engine._begin_provider_interaction(record, assignment,
                                                                     phase="revision", attempt=1, effect_key=key)
                    record.provider_runs.append(ProviderRun(
                        provider=assignment.agent, assignment_sequence=assignment.sequence,
                        plan_generation=record.plan_generation, interaction_id=interaction,
                        run_id="reused-run", status="completed"))
                store.save(record)
                record = store.get(record.task_id)
                run = record.provider_runs[-1]
                run.status, run.result, run.raw = "completed", "done", {"usage": {"cost": 0.3}}
                self.assertTrue(engine._settle_provider_spend(record, run, assignment.agent))
                self.assertEqual(set(engine.provider_spend.settlements(record.task_id)), {key})
                self.assertAlmostEqual(engine.provider_spend.exposure(assignment.agent)[1], 0.32)

    def test_packet_acknowledgement_read_failures_preserve_snapshot(self):
        from director_orchestrator.approvals import reserve_provider_effect
        from director_orchestrator.engine import AcknowledgementPersistencePending

        for boundary in ("task", "journal"):
            for exhausted in (False, True):
                with self.subTest(boundary=boundary, exhausted=exhausted), tempfile.TemporaryDirectory() as directory:
                    settings, store, record, assignment = self._world(directory)
                    engine = OrchestrationEngine(settings, store)
                    key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                    engine._reserve_provider_daily_spend(assignment, effect_key=key)
                    store.save(record)
                    record.provider_runs.append(ProviderRun(
                        provider=assignment.agent, assignment_sequence=assignment.sequence,
                        run_id="read-failure-run", status="completed", result="retained result",
                        raw={"usage": {"cost": 0.25}}, plan_generation=record.plan_generation))
                    outcome = SimpleNamespace(result_packet={"run_reference": "read-failure-run", "outcome": "retained result"})
                    target, method = (store, "get") if boundary == "task" else (engine.provider_spend, "settlements")
                    original = getattr(target, method)
                    failures = 0

                    def fail_read(*args, **kwargs):
                        nonlocal failures
                        if exhausted or failures == 0:
                            failures += 1
                            raise sqlite3.OperationalError("database is locked")
                        return original(*args, **kwargs)

                    with patch.object(target, method, side_effect=fail_read):
                        if exhausted:
                            with self.assertRaises(AcknowledgementPersistencePending):
                                engine._settle_packet_spend(record, assignment, key, outcome)
                        else:
                            self.assertTrue(engine._settle_packet_spend(record, assignment, key, outcome))
                    restarted = OrchestrationEngine(settings, TaskStore(store.path))
                    durable = restarted.store.get(record.task_id)
                    self.assertNotEqual(durable.status, TaskStatus.FAILED)
                    if exhausted:
                        self.assertEqual(failures, 2)
                        self.assertEqual(durable.provider_runs, [])
                        self.assertEqual(restarted.provider_spend.settlements(record.task_id), {})
                    else:
                        self.assertEqual(durable.provider_runs[-1].result, "retained result")
                        self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, 0.25))
                        self.assertTrue(restarted._retry_spend_settlements(durable))

    def test_intent_free_acknowledgement_uses_captured_generation(self):
        from director_orchestrator.approvals import reset_for_owner_clarification

        for source in ("run", "packet", "journal", "absent"):
            for reset in (False, True):
                if source == "absent" and reset:
                    continue  # Historical unbound runs cannot prove their dispatch epoch.
                with self.subTest(source=source, reset=reset), tempfile.TemporaryDirectory() as directory:
                    settings, store, record, assignment = self._world(directory)
                    engine = OrchestrationEngine(settings, store)
                    key = _key("intent-free")
                    generation = record.plan_generation
                    engine._reserve_provider_daily_spend(assignment, effect_key=key)
                    run = ProviderRun(provider=assignment.agent, run_id="intent-free-run",
                                      assignment_sequence=assignment.sequence, status="completed",
                                      result="bounded-result", raw={"usage": {"cost": 0.25}},
                                      plan_generation=generation if source == "run" else None)
                    self.assertFalse(any(item.kind == "provider_intent" for item in record.task_effect_ledger))
                    if source == "journal":
                        engine.provider_spend.save_settlement(record.task_id, key, {
                            "provider": run.provider.value, "run_id": run.run_id,
                            "cost": 0.25, "pending": True, "plan_generation": generation,
                        })
                    if reset:
                        reset_for_owner_clarification(record)
                        store.save(record)
                    if source == "packet":
                        outcome = SimpleNamespace(result_packet={
                            "run_reference": run.run_id, "outcome": run.result})
                        accepted = engine._settle_packet_spend(
                            record, assignment, key, outcome, plan_generation=generation)
                    else:
                        accepted = engine._charge_spend(
                            assignment.agent, key, 0.25, record=record, assignment=assignment, run=run)
                    self.assertEqual(accepted, not reset)
                    current = store.get(record.task_id)
                    self.assertEqual(bool(current.provider_runs), not reset)
                    if reset:
                        self.assertNotIn("bounded-result", current.model_dump_json())
                    held = engine.provider_spend.settlements(record.task_id)[key]
                    self.assertFalse(held["pending"])
                    self.assertEqual(held["plan_generation"], generation)
                    self.assertEqual(engine.provider_spend.exposure(assignment.agent)[0], 1)

    def test_intent_free_old_run_does_not_restore_replacement_plan_content(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = _key("old-generation")
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            run = ProviderRun(provider=assignment.agent, run_id="old-run", status="completed",
                              result="old-content", plan_generation=record.plan_generation)
            record.plan_generation += 1
            store.save(record)
            self.assertFalse(engine._charge_spend(
                assignment.agent, key, 0.25, record=record, assignment=assignment, run=run))
            self.assertEqual(store.get(record.task_id).provider_runs, [])
            self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 0.25))

    async def test_poll_preserves_dispatch_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(replace(settings, provider_poll_seconds=0), store)
            run = ProviderRun(provider=assignment.agent, run_id="poll-generation", status="RUNNING",
                              plan_generation=record.plan_generation)
            adapter = AsyncMock()
            adapter.poll.return_value = run.model_copy(update={"status": "completed", "plan_generation": None})
            observed = await engine._wait(adapter, run)
            self.assertEqual(observed.plan_generation, record.plan_generation)
            adapter.poll.assert_awaited_once()

    def test_reset_before_first_ack_write_settles_without_content(self):
        from director_orchestrator.approvals import reserve_provider_effect, reset_for_owner_clarification

        for phase in ("dispatch", "revision", "packet"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment,
                                              phase="revision" if phase == "revision" else "dispatch",
                                              max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                store.save(record)
                reset = store.get(record.task_id)
                reset_for_owner_clarification(reset)
                store.save(reset)
                run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                                  run_id="reset-run", status="completed", result="erased-content-marker",
                                  raw={"payload": "erased-content-marker"})
                if phase == "packet":
                    # Both packet exits reload the current reset snapshot before acknowledgement.
                    outcome = SimpleNamespace(result_packet={
                        "run_reference": run.run_id, "outcome": run.result})
                    self.assertFalse(engine._settle_packet_spend(record, assignment, key, outcome))
                else:
                    self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                         record=record, assignment=assignment, run=run))
                current = store.get(record.task_id)
                self.assertEqual(current.provider_runs, [])
                self.assertNotIn("erased-content-marker", current.model_dump_json())
                journal = engine.provider_spend.settlements(record.task_id)
                self.assertNotIn("erased-content-marker", json.dumps(journal))
                self.assertFalse(journal[key]["pending"])
                self.assertEqual(engine.provider_spend.exposure(assignment.agent)[0], 1)
                self.assertTrue(engine._retry_spend_settlements(current))
                self.assertEqual(store.get(record.task_id).provider_runs, [])

    def test_reset_between_ack_check_and_cas_keeps_only_accounting(self):
        from director_orchestrator.approvals import reserve_provider_effect, reset_for_owner_clarification

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            store.save(record)
            run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                              run_id="racing-reset", status="completed", result="erased-race-marker")
            save = store.save

            def reset_before_cas(candidate, **kwargs):
                if "spend_settlement" in kwargs:
                    reset = store.get(record.task_id)
                    reset_for_owner_clarification(reset)
                    save(reset)
                return save(candidate, **kwargs)

            with patch.object(store, "save", side_effect=reset_before_cas):
                self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                     record=record, assignment=assignment, run=run))
            self.assertNotIn("erased-race-marker", store.get(record.task_id).model_dump_json())
            self.assertNotIn("erased-race-marker", json.dumps(engine.provider_spend.settlements(record.task_id)))
            self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 0.25))

    def test_not_created_replay_cannot_release_authenticated_existence(self):
        from director_orchestrator.approvals import reserve_provider_effect, record_provider_effect_observation
        from director_orchestrator.models import ProviderContactAttempt

        for cost in (0.0, 0.25, None):
            with self.subTest(cost=cost), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                record_provider_effect_observation(record, assignment, effect_key=key,
                                                   phase="dispatch", outcome="ambiguous")
                for phase, outcome in (("dispatch", "ambiguous_dispatch"),
                                       ("reconciliation", "reconciled_not_created")):
                    record.provider_contact_attempts.append(ProviderContactAttempt(
                        assignment_sequence=assignment.sequence, provider=assignment.agent,
                        ordinal=1, phase=phase, outcome=outcome, error="test reconciliation"))
                store.save(record)
                self.assertTrue(engine._settle_not_created_dispatches(record))
                self.assertEqual(engine.provider_spend.reservations(assignment.agent, include_released=False), [])
                engine.provider_spend.record_charge(provider=assignment.agent, effect_key=key, actual_cost_usd=cost)
                restarted = OrchestrationEngine(settings, store)
                self.assertTrue(restarted._settle_not_created_dispatches(store.get(record.task_id)))
                self.assertEqual(len(restarted.provider_spend.reservations(assignment.agent, include_released=False)), 1)
                with self.assertRaises(ProviderDailyLimitExceeded):
                    restarted.provider_spend.reserve(provider=assignment.agent, effect_key=_key("ceiling"),
                                                     estimated_cost_usd=0, request_limit=1, budget_usd=10)

    async def test_atomic_ack_retries_and_restart_preserves_result_and_binding(self):
        from director_orchestrator.approvals import reserve_provider_effect

        for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
            for phase in ("dispatch", "revision"):
                with self.subTest(provider=provider, phase=phase), tempfile.TemporaryDirectory() as directory:
                    assignment = Assignment(sequence=1, agent=provider, task="Bounded question", acceptance=["Answer"])
                    settings, store, record, assignment = self._world(directory, assignment=assignment)
                    engine = OrchestrationEngine(settings, store)
                    key = reserve_provider_effect(record, assignment, phase=phase, max_task_budget_usd=10)
                    engine.provider_spend.reserve(provider=provider, effect_key=key,
                                                  estimated_cost_usd=0.5, request_limit=10, budget_usd=1)
                    binding = engine._begin_provider_interaction(record, assignment, phase=phase,
                                                                  attempt=1, effect_key=key)
                    store.save(record)
                    run = ProviderRun(provider=provider, assignment_sequence=1, run_id="atomic-run",
                                      interaction_id=binding, status="completed", result="preserved answer",
                                      evidence_source="provider_authenticated")
                    engine._record_dispatch_acknowledgement(record, run)
                    record.revision_count = int(phase == "revision")
                    real_save = store.save
                    calls = 0

                    def fail_once(candidate, **kwargs):
                        nonlocal calls
                        if "spend_settlement" in kwargs:
                            calls += 1
                            if calls == 1:
                                if phase == "revision":
                                    raise OSError(5, "injected temporary I/O failure")
                                raise sqlite3.OperationalError("database is locked")
                        return real_save(candidate, **kwargs)

                    with patch.object(store, "save", side_effect=fail_once), patch.object(
                        engine.provider_spend, "record_charge", wraps=engine.provider_spend.record_charge
                    ) as charge:
                        self.assertTrue(engine._charge_spend(provider, key, 0.25,
                                                            record=record, assignment=assignment, run=run))
                    self.assertEqual(calls, 2)
                    charge.assert_called_once()
                    # Reopen the SQLite store, not just the in-memory engine.
                    restarted = OrchestrationEngine(settings, TaskStore(settings.sqlite_path))
                    recovered = restarted.store.get(record.task_id)
                    with patch.object(restarted.provider_spend, "record_charge") as charge:
                        self.assertTrue(restarted._retry_spend_settlements(recovered))
                    charge.assert_not_called()
                    restored = recovered.provider_runs[-1]
                    self.assertEqual((restored.interaction_id, restored.result), (binding, run.result))
                    self.assertEqual(recovered.revision_count, int(phase == "revision"))
                    adapter = AsyncMock()
                    adapter.poll.side_effect = AssertionError("synchronous result must not be polled")
                    self.assertIs(await restarted._wait(adapter, restored, recovered, assignment), restored)
                    restarted._verify_provider_result(recovered, restored)
                    adapter.poll.assert_not_awaited()
                    self.assertEqual(restarted.provider_spend.exposure(provider), (1, 0.25))
                    self.assertNotIn("preserved answer", json.dumps(restarted.provider_spend.settlements(record.task_id)))

    def test_atomic_retry_deadline_prevents_another_write(self):
        from director_orchestrator.approvals import reserve_provider_effect
        from director_orchestrator.engine import AcknowledgementPersistencePending

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            store.save(record)
            run = ProviderRun(provider=assignment.agent, run_id="deadline-run", status="completed", result="answer",
                              plan_generation=record.plan_generation)
            with patch.object(store, "save", side_effect=sqlite3.OperationalError("database is locked")) as save, patch(
                "director_orchestrator.engine.time.monotonic", side_effect=[0, 0, 0, 3, 3]
            ):
                with self.assertRaises(AcknowledgementPersistencePending):
                    engine._journal_spend_acknowledgement(record, key, run, None)
            save.assert_called_once()
            self.assertEqual(store.get(record.task_id).provider_runs, [])

    def test_combined_transaction_rolls_back_task_when_journal_insert_fails(self):
        from director_orchestrator.approvals import reserve_provider_effect

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            store.save(record)
            version = record._store_version
            with store._connect() as connection:
                connection.execute("""CREATE TRIGGER reject_ack BEFORE INSERT ON engine_spend_settlements
                                      BEGIN SELECT RAISE(ABORT, 'injected journal failure'); END""")
            record.provider_runs.append(ProviderRun(provider=assignment.agent, run_id="rolled-back",
                                                    status="completed", result="must roll back"))
            with self.assertRaises(sqlite3.IntegrityError):
                store.save(record, spend_settlement=(key, {"provider": assignment.agent.value,
                                                           "run_id": "rolled-back", "pending": True, "cost": None}))
            self.assertEqual(record._store_version, version)
            self.assertEqual(store.get(record.task_id).provider_runs, [])
            self.assertEqual(engine.provider_spend.settlements(record.task_id), {})

    async def test_legacy_journal_only_requires_owner_or_bound_gemini_readback(self):
        from director_orchestrator.approvals import reserve_provider_effect

        for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
            for bound in (False, True):
                with self.subTest(provider=provider, bound=bound), tempfile.TemporaryDirectory() as directory:
                    assignment = Assignment(sequence=1, agent=provider, task="Bounded question", acceptance=["Answer"])
                    settings, store, record, assignment = self._world(directory, assignment=assignment)
                    settings = replace(settings, provider_poll_seconds=0)
                    engine = OrchestrationEngine(settings, store)
                    key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                    intent = record.task_effect_ledger[-1]
                    binding = engine._begin_provider_interaction(record, assignment, phase="dispatch",
                                                                 attempt=1, effect_key=key) if bound else None
                    store.save(record)
                    engine.provider_spend.reserve(provider=provider, effect_key=key,
                                                  estimated_cost_usd=0.5, request_limit=10, budget_usd=1)
                    engine.provider_spend.save_settlement(record.task_id, key, {
                        "provider": provider.value, "run_id": "legacy-run", "cost": 0.25, "pending": True,
                        **{name: getattr(intent, name) for name in
                           ("phase", "assignment_sequence", "assignment_digest", "plan_generation")},
                    })
                    restarted = OrchestrationEngine(settings, TaskStore(settings.sqlite_path))
                    recovered = restarted.store.get(record.task_id)
                    adapter = AsyncMock()
                    restarted.providers[provider] = adapter
                    recoverable = provider == ProviderName.GEMINI and bound
                    self.assertEqual(restarted._retry_spend_settlements(recovered), recoverable)
                    if recoverable:
                        run = recovered.provider_runs[-1]
                        self.assertEqual(run.interaction_id, binding)
                        adapter.poll.return_value = run.model_copy(update={"status": "completed", "result": "readback"})
                        observed = await restarted._wait(adapter, run, recovered, assignment)
                        restarted._verify_provider_result(recovered, observed)
                        adapter.poll.assert_awaited_once()
                    else:
                        self.assertEqual(recovered.status, TaskStatus.OWNER_DECISION_REQUIRED)
                        self.assertEqual(recovered.provider_runs, [])
                        self.assertIn("Owner reconciliation", recovered.message)
                        self.assertFalse(restarted._retry_spend_settlements(restarted.store.get(record.task_id)))
                        adapter.poll.assert_not_awaited()
                    adapter.dispatch.assert_not_awaited()

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
        from director_orchestrator.engine import AcknowledgementPersistencePending

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
                        record.plan.assignments = [assignment]
                        engine = OrchestrationEngine(settings, store)
                        run = ProviderRun(provider=provider, run_id="metered-run", status="completed", raw=raw,
                                          plan_generation=record.plan_generation)
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
                    elif failure == "terminal":
                        terminal = original if phase == "dispatch" else revised
                        submitted.return_value = terminal.model_copy(update={"status": "RUNNING", "result": None, "raw": {}})
                        adapter.poll.return_value = terminal
                        engine.settings = replace(engine.settings, provider_poll_seconds=0)
                    engine.providers[assignment.agent] = adapter
                    engine.director.review = AsyncMock(return_value=ReviewDecision(
                        verdict=ReviewVerdict.REVISE, summary="More evidence needed", revision_prompt="Add evidence"))
                    real_charge = engine.provider_spend.record_charge
                    calls = 0
                    fail_at = 2 * int(phase == "revision") + (2 if failure == "terminal" else 1)

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
                    # The atomic journal retains the known cost without prematurely
                    # reconciling the initial acknowledgement. Restart can settle it.
                    held = engine.provider_spend.settlements(record.task_id)[effects[0]]
                    self.assertTrue(held["pending"])
                    expected_cost = 0.1 if phase == "dispatch" else 0.2
                    self.assertEqual(held["cost"], expected_cost)
                    with engine.provider_spend._connect() as connection:
                        actual, settled = connection.execute(
                            "SELECT actual_cost_usd, settled_at FROM provider_daily_spend_reservations "
                            "WHERE provider = ? AND effect_key = ?", (assignment.agent.value, effects[0]),
                        ).fetchone()
                    self.assertIsNone(actual)
                    self.assertIsNotNone(settled)
                    restarted = OrchestrationEngine(settings, TaskStore(settings.sqlite_path))
                    self.assertEqual(restarted.provider_spend.unreconciled(assignment.agent, stale_after=HOUR), effects)
                    self.assertTrue(restarted._retry_spend_settlements(restarted.store.get(record.task_id)))
                    self.assertEqual(restarted.provider_spend.unreconciled(assignment.agent, stale_after=HOUR), [])
                    self.assertFalse(restarted.provider_spend.settlements(record.task_id)[effects[0]]["pending"])
                    self.assertAlmostEqual(restarted.provider_spend.exposure(assignment.agent)[1],
                                           expected_cost + (0.1 if phase == "revision" else 0))
                    adapter.dispatch.assert_awaited_once()
                    self.assertEqual(adapter.revise.await_count, int(phase == "revision"))
                    self.assertEqual(len(saved.provider_runs), 1 + int(phase == "revision"))

    def test_hold_cas_merges_current_record_and_binds_reconciliation(self):
        from director_orchestrator.approvals import reserve_provider_effect, StaleApproval
        from director_orchestrator.models import AmbiguousDispatchResolution

        for stale_writes in (1,):
            with self.subTest(stale_writes=stale_writes), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                binding = engine._begin_provider_interaction(record, assignment, phase="dispatch",
                                                             attempt=1, effect_key=key)
                store.save(record)
                run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                                  run_id="acknowledged-run", interaction_id=binding,
                                  status="completed", result="preserved evidence")
                engine._record_dispatch_acknowledgement(record, run)
                real_save = store.save
                conflicts = 0

                def race(candidate, **kwargs):
                    nonlocal conflicts
                    if conflicts < stale_writes:
                        conflicts += 1
                        current = store.get(record.task_id)
                        current.revision_count = conflicts
                        real_save(current)
                    return real_save(candidate, **kwargs)

                with patch.object(store, "save", side_effect=race), patch.object(
                    engine.provider_spend, "record_charge", side_effect=sqlite3.OperationalError("locked")
                ):
                    self.assertFalse(engine._charge_spend(assignment.agent, key, 0.25,
                                                         record=record, assignment=assignment, run=run))
                held = store.get(record.task_id)
                self.assertEqual(held.revision_count, stale_writes)
                self.assertEqual(held.provider_runs[-1].result, run.result)
                self.assertEqual(held.provider_runs[-1].interaction_id, binding)
                self.assertTrue(any(item.kind == "provider_observation" and item.effect_key == key
                                    and item.outcome == "submitted" for item in held.task_effect_ledger))
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

    async def test_persistent_atomic_failure_keeps_intent_and_never_fails_or_redispatches(self):
        from director_orchestrator.approvals import reserve_provider_effect, StaleApproval
        from director_orchestrator.engine import AcknowledgementPersistencePending

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            engine._status_post = AsyncMock()
            key = reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            store.save(record)
            run = ProviderRun(provider=assignment.agent, assignment_sequence=assignment.sequence,
                              run_id="in-memory-only", status="completed", result="response retained during retry")
            with patch.object(store, "save", side_effect=sqlite3.OperationalError("database is locked")) as save, patch.object(
                engine.provider_spend, "record_charge"
            ) as charge:
                with self.assertRaises(AcknowledgementPersistencePending):
                    engine._charge_spend(assignment.agent, key, 0.25,
                                         record=record, assignment=assignment, run=run)
            self.assertEqual(save.call_count, 2)
            charge.assert_not_called()
            # Exercise the actual outer handler with the same exhausted response.
            with patch.object(engine, "_retry_spend_settlements", side_effect=AcknowledgementPersistencePending()):
                await engine.run(record.task_id)
            recovered = store.get(record.task_id)
            self.assertNotEqual(recovered.status, TaskStatus.FAILED)
            self.assertEqual(recovered.provider_runs, [])
            self.assertEqual(engine.provider_spend.settlements(record.task_id), {})
            self.assertTrue(any(item.kind == "provider_intent" and item.effect_key == key
                                for item in recovered.task_effect_ledger))
            self.assertFalse(any(item.kind == "provider_observation" and item.effect_key == key
                                 for item in recovered.task_effect_ledger))
            # The durable unobserved intent remains ambiguous, first leased and
            # then requiring reconciliation. Neither state grants another send.
            self.assertIsNone(engine._reserve_effect_intent(recovered, assignment, phase="dispatch"))
            with patch.object(engine, "_effect_owner_is_live", return_value=False):
                with self.assertRaises(StaleApproval):
                    engine._reserve_effect_intent(recovered, assignment, phase="dispatch")
            self.assertNotEqual(store.get(record.task_id).status, TaskStatus.FAILED)

    async def test_orphaned_acknowledgement_reconciles_after_restart_through_api(self):
        import director_orchestrator.main as main
        from director_orchestrator.models import IntakeAuthentication

        for outcome in ("created", "not_created"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                settings = replace(settings, openai_api_key="fixture-only", shared_secret="fixture-auth",
                                   slack_owner_user_id=record.intake.user_id)
                record.intake_authentication = IntakeAuthentication.SLACK_GATEWAY_ATTESTED
                store.save(record)
                engine = OrchestrationEngine(settings, store)
                engine._status_post = AsyncMock()
                adapter = AsyncMock()
                adapter.dispatch.return_value = ProviderRun(
                    provider=assignment.agent, run_id="lost-response", status="completed",
                    result="must-not-resurrect", raw={"usage": {"cost": 0.25}})
                engine.providers[assignment.agent] = adapter
                save = store.save
                failures = 0

                def fail_acknowledgement(candidate, **kwargs):
                    nonlocal failures
                    if "spend_settlement" in kwargs:
                        failures += 1
                        raise sqlite3.OperationalError("database is locked")
                    return save(candidate, **kwargs)

                # Real run/dispatch/acknowledgement/outer catch. Only provider I/O,
                # optional publication and unrelated plan policy are stubbed.
                with patch.object(store, "save", side_effect=fail_acknowledgement), patch(
                    "director_orchestrator.engine.execution_policy_errors", return_value=[]
                ), patch.object(engine, "_publish_delegation", new=AsyncMock(return_value=True)):
                    await engine.run(record.task_id)
                adapter.dispatch.assert_awaited_once()
                self.assertEqual(failures, 2)
                durable = store.get(record.task_id)
                self.assertEqual(durable.status, TaskStatus.RUNNING)
                self.assertEqual(durable.provider_runs, [])
                self.assertEqual(durable.provider_contact_attempts, [])
                self.assertEqual(engine.provider_spend.settlements(record.task_id), {})
                intent = next(item for item in durable.task_effect_ledger if item.kind == "provider_intent")
                self.assertFalse(any(item.kind == "provider_observation" for item in durable.task_effect_ledger))
                restarted_store = TaskStore(store.path)
                restarted = OrchestrationEngine(settings, restarted_store)
                exposure = restarted.provider_spend.exposure(assignment.agent)
                resolution = dict(owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                                  resolution=outcome, note="Owner checked the provider request history")
                if outcome == "created":
                    resolution["run_id"] = "owner-confirmed-run"
                url = f"/v1/tasks/{record.task_id}/dispatch-reconciliation"
                with patch.object(main, "settings", settings), patch.object(main, "store", restarted_store), patch.object(
                    main, "engine", restarted
                ), patch.object(main, "primary_default_activation", None), patch.object(
                    restarted, "run", new=AsyncMock()
                ) as queued:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                                 base_url="http://fixture") as client:
                        denied = await client.post(url, json=resolution)
                        self.assertEqual(denied.status_code, 401)
                        headers = {"Authorization": "Bearer fixture-auth"}
                        denied = await client.post(url, json={**resolution, "owner_user_id": "wrong-owner"}, headers=headers)
                        self.assertEqual(denied.status_code, 403)
                        live = await client.post(url, json=resolution, headers=headers)
                        self.assertEqual(live.status_code, 409)
                        self.assertEqual(restarted_store.get(record.task_id).provider_contact_attempts, [])
                        # Advance only the lease clock; do not rewrite immutable evidence.
                        expired = intent.recorded_at.timestamp() + max(180, settings.provider_timeout_seconds) + 61
                        with patch("director_orchestrator.engine.time.time", return_value=expired):
                            accepted = await client.post(url, json=resolution, headers=headers)
                        self.assertEqual(accepted.status_code, 200, accepted.text)
                    queued.assert_awaited_once()
                recovered = restarted_store.get(record.task_id)
                self.assertNotIn("must-not-resurrect", recovered.model_dump_json())
                self.assertEqual(recovered.provider_contact_attempts[-1].outcome, "reconciled_" + outcome)
                if outcome == "created":
                    run = recovered.provider_runs[-1]
                    self.assertEqual(run.plan_generation, recovered.plan_generation)
                    self.assertEqual(run.evidence_source, "unverified")
                    self.assertEqual(restarted.provider_spend.exposure(assignment.agent), exposure)
                    self.assertTrue(restarted._settle_provider_spend(recovered, run, assignment.agent))
                    self.assertEqual(restarted.provider_spend.exposure(assignment.agent), exposure)
                    self.assertIn(intent.effect_key, restarted.provider_spend.unreconciled(
                        assignment.agent, stale_after=timedelta(0)))
                else:
                    self.assertEqual(recovered.provider_runs, [])
                    self.assertTrue(any(item.effect_key == intent.effect_key and item.outcome == "failed_pre_submit"
                                        for item in recovered.task_effect_ledger))
                    self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (0, 0.0))
                    self.assertTrue(restarted._settle_reconciled_spend(recovered))
                adapter.dispatch.assert_awaited_once()

    def test_exact_review_orphan_uses_the_dispatched_assignment(self):
        from director_orchestrator.approvals import assignment_approval_digest, reserve_provider_effect, StaleApproval
        from director_orchestrator.models import AmbiguousDispatchResolution
        from tests.test_review_handoff_effect_accounting import ReviewHandoffEffectAccountingTests

        for provider, outcome in ((ProviderName.CLAUDE, "created"),
                                  (ProviderName.CLAUDE, "not_created"),
                                  (ProviderName.GROK, "not_created")):
            with self.subTest(provider=provider, outcome=outcome), tempfile.TemporaryDirectory() as directory:
                settings, store, _, _ = self._world(directory)
                settings = replace(settings, oracle_automerge_cursor_actors=("cursor-agent",),
                                   oracle_automerge_claude_actors=("claude-agent",),
                                   oracle_automerge_grok_actors=("grok-agent",))
                record, planned = ReviewHandoffEffectAccountingTests().fixture()
                planned.agent = provider
                record.next_assignment_index = 1
                record.owner_approved = True
                store.create(record)
                engine = OrchestrationEngine(settings, store)
                effective = engine._effective_assignment(record, planned)
                self.assertNotEqual(assignment_approval_digest(planned), assignment_approval_digest(effective))
                key = reserve_provider_effect(record, effective, phase="dispatch", max_task_budget_usd=10)
                store.save(record)
                if provider == ProviderName.GROK:
                    engine.provider_spend.reserve(provider=provider, effect_key=key, estimated_cost_usd=0.25,
                                                  request_limit=50, budget_usd=1)
                restarted = OrchestrationEngine(settings, TaskStore(store.path))
                record = restarted.store.get(record.task_id)
                dispatched_snapshot = record.model_copy(deep=True)
                resolution = AmbiguousDispatchResolution(
                    owner_user_id=record.intake.user_id, assignment_sequence=planned.sequence,
                    resolution=outcome, run_id="owner-confirmed-run" if outcome == "created" else None,
                    note="Owner checked the provider request history")
                # Changed producer evidence must still refuse before adding a recovery contact.
                altered = record.model_copy(deep=True)
                altered.provider_runs[0].result = "changed candidate"
                before = altered.model_dump_json()
                with patch.object(restarted, "_effect_owner_is_live", return_value=False):
                    with self.assertRaises(StaleApproval):
                        restarted.apply_dispatch_reconciliation(altered, resolution)
                    self.assertEqual(altered.model_dump_json(), before)
                    restarted.apply_dispatch_reconciliation(record, resolution)
                restarted.store.save(record)
                observation = next(item for item in record.task_effect_ledger
                                   if item.kind == "provider_observation" and item.effect_key == key)
                self.assertEqual(observation.assignment_digest, assignment_approval_digest(effective))
                self.assertEqual(record.provider_contact_attempts[-1].outcome, "reconciled_" + outcome)
                if outcome == "not_created":
                    self.assertTrue(restarted._settle_reconciled_spend(record))
                    self.assertTrue(any(item.effect_key == key and item.outcome == "failed_pre_submit"
                                        for item in record.task_effect_ledger))
                    if provider == ProviderName.GROK:
                        self.assertEqual(restarted.provider_spend.exposure(provider), (0, 0))
                else:
                    self.assertEqual(record.provider_runs[-1].evidence_source, "unverified")
                self.assertEqual(record.plan.assignments[1], planned)

                if provider == ProviderName.GROK:
                    # The same overlay must bind atomic acknowledgement and legacy journal recovery.
                    ack_settings = replace(settings, sqlite_path=str(Path(directory) / "ack.sqlite3"))
                    ack_store = TaskStore(ack_settings.sqlite_path)
                    ack_record = dispatched_snapshot
                    ack_store.create(ack_record)
                    ack_engine = OrchestrationEngine(ack_settings, ack_store)
                    ack_engine.provider_spend.reserve(provider=provider, effect_key=key, estimated_cost_usd=0.25,
                                                      request_limit=50, budget_usd=1)
                    run = ProviderRun(provider=provider, assignment_sequence=planned.sequence,
                                      run_id="acknowledged-review", status="completed", result="review evidence")
                    self.assertTrue(ack_engine._journal_spend_acknowledgement(ack_record, key, run, 0.25))
                    submitted = [item for item in ack_record.task_effect_ledger
                                 if item.kind == "provider_observation" and item.effect_key == key]
                    self.assertEqual(len(submitted), 1)
                    self.assertEqual(submitted[0].assignment_digest, assignment_approval_digest(effective))
                    # A journal-only synchronous result requires owner reconciliation, not privacy-reset cleanup.
                    ack_record.provider_runs = [item for item in ack_record.provider_runs if item.run_id != run.run_id]
                    ack_store.save(ack_record)
                    self.assertFalse(ack_engine._retry_spend_settlements(ack_record))
                    self.assertEqual(ack_record.status, TaskStatus.OWNER_DECISION_REQUIRED)
                    self.assertEqual(ack_record.error, "legacy_acknowledgement_requires_readback")
                    self.assertEqual(ack_engine.provider_spend.exposure(provider), (1, 0.25))

    def test_orphan_reconciliation_preserves_the_attempt_ordinal(self):
        from director_orchestrator.approvals import reserve_provider_effect, record_provider_effect_observation
        from director_orchestrator.models import AmbiguousDispatchResolution

        for phase in ("dispatch", "revision"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                settings, store, record, assignment = self._world(directory)
                engine = OrchestrationEngine(settings, store)
                for attempt in range(1, 4):
                    effect = reserve_provider_effect(record, assignment, phase=phase, max_task_budget_usd=10)
                    if attempt < 3:
                        record_provider_effect_observation(record, assignment, effect_key=effect,
                                                           phase=phase, outcome="failed_pre_submit")
                resolution = AmbiguousDispatchResolution(
                    owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                    resolution="not_created", note="Owner checked the provider request history")
                with patch.object(engine, "_effect_owner_is_live", return_value=False):
                    engine.apply_dispatch_reconciliation(record, resolution)
                self.assertEqual([item.ordinal for item in record.provider_contact_attempts], [3, 3])
                self.assertEqual(record.provider_contact_attempts[0].phase, phase)
                self.assertEqual(record.task_effect_ledger[-1].effect_key, effect)

    async def test_orphan_reconciliation_refuses_wrong_binding_and_active_writer(self):
        from director_orchestrator.approvals import reserve_provider_effect, StaleApproval
        from director_orchestrator.models import AmbiguousDispatchResolution

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            reserve_provider_effect(record, assignment, phase="dispatch", max_task_budget_usd=10)
            resolution = AmbiguousDispatchResolution(
                owner_user_id=record.intake.user_id, assignment_sequence=assignment.sequence,
                resolution="not_created", note="Owner checked the provider request history")
            for mismatch in ("task", "generation", "assignment", "provider", "sequence", "effect"):
                with self.subTest(mismatch=mismatch):
                    candidate = record.model_copy(deep=True)
                    if mismatch == "task":
                        candidate.task_id = "DIR-OTHER"
                    elif mismatch == "generation":
                        candidate.plan_generation += 1
                    elif mismatch == "assignment":
                        candidate.plan.assignments[0].task = "Different authorized work"
                    else:
                        field, value = {"provider": ("provider", ProviderName.CLAUDE),
                                        "sequence": ("assignment_sequence", 2),
                                        "effect": ("effect_key", "0" * 64)}[mismatch]
                        candidate.task_effect_ledger[-1] = candidate.task_effect_ledger[-1].model_copy(update={field: value})
                    before = candidate.model_dump_json()
                    with patch.object(engine, "_effect_owner_is_live", return_value=False):
                        with self.assertRaises(StaleApproval):
                            engine.apply_dispatch_reconciliation(candidate, resolution)
                    self.assertEqual(candidate.model_dump_json(), before)
            async with engine._task_locks[record.task_id]:
                with patch.object(engine, "_effect_owner_is_live", return_value=False):
                    with self.assertRaisesRegex(StaleApproval, "active writer"):
                        engine.apply_dispatch_reconciliation(record, resolution)
            self.assertEqual(record.provider_contact_attempts, [])

    def test_non_sqlite_or_separate_spend_store_refuses_before_submission(self):
        from director_orchestrator.engine import AcknowledgementPersistencePending

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            for alternate in (SimpleNamespace(path=store.path), TaskStore(str(Path(directory) / "other.sqlite3"))):
                with self.subTest(store=type(alternate).__name__), patch.object(engine, "store", alternate):
                    with self.assertRaises(ProviderDailyLimitExceeded):
                        engine._reserve_provider_daily_spend(assignment, effect_key=_key("unsupported"))
                    with self.assertRaises(AcknowledgementPersistencePending):
                        engine._journal_spend_acknowledgement(record, _key("unsupported"), ProviderRun(
                            provider=assignment.agent, run_id="unsupported", status="completed"), None)

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
        """Unresolved requests survive UTC midnight; daily exposure stays on the reservation day."""
        for hour in (12, 0):
            now = datetime(2026, 9, 28, hour, 5, tzinfo=timezone.utc)
            before = now - timedelta(minutes=30)
            for provider in (ProviderName.GEMINI, ProviderName.GROK, ProviderName.PERPLEXITY):
                with self.subTest(hour=hour, provider=provider), tempfile.TemporaryDirectory() as directory:
                    settings, store, _, _ = self._world(directory)
                    settings = replace(settings, provider_timeout_seconds=600)
                    engine = OrchestrationEngine(settings, store)
                    ledger = engine.provider_spend
                    ledger.reserve(provider=provider, effect_key=_key("crashed"),
                                   estimated_cost_usd=0.01, request_limit=50, budget_usd=1.0, now=before)
                    self.assertEqual(ledger.unreconciled(provider, stale_after=HOUR, now=now), [])
                    self.assertEqual(ledger.unreconciled(
                        provider, stale_after=timedelta(minutes=10), now=now), [_key("crashed")])
                    # A new monitor opens the persisted ledger, then feeds the actual check-15 rule.
                    monitor = UsageMonitor(settings, store, adapters={})
                    accounting = monitor._daily_accounting(provider.value, now)
                    self.assertEqual(accounting["unreconciled_request_count"], 1)
                    self.assertEqual(self._check_15_state(accounting["unreconciled_request_count"]), "UNKNOWN")
                    self.assertEqual(ledger.exposure(provider, now=before), (1, 0.01))
                    self.assertEqual(ledger.exposure(provider, now=now), (1, 0.01) if hour else (0, 0))
                    ledger.reserve(provider=provider, effect_key=_key("fresh"),
                                   estimated_cost_usd=0.01, request_limit=50, budget_usd=1.0, now=now)
                    for cost in (None, 0.0, 0.2):
                        ledger.record_charge(provider=provider, effect_key=_key("crashed"),
                                             actual_cost_usd=cost, now=now)
                        expected = 1 if cost is None else 0
                        self.assertEqual(ledger.unreconciled(provider, stale_after=HOUR, now=now),
                                         [_key("crashed")] if expected else [])
                        count = monitor._daily_accounting(provider.value, now)["unreconciled_request_count"]
                        self.assertEqual(count, expected)
                        self.assertEqual(self._check_15_state(count), "UNKNOWN" if expected else "PASS")
                    ledger.record_charge(provider=provider, effect_key=_key("fresh"),
                                         actual_cost_usd=0, request_exists=False, now=now)
                    ledger.release_not_created(provider=provider, effect_key=_key("fresh"))
                    self.assertEqual(ledger.unreconciled(provider, stale_after=timedelta(0), now=now), [])

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
                              plan_generation=record.plan_generation,
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
            self.assertEqual([run.run_id for run in saved.provider_runs], ["known-run"])  # Missing intent is not a reset: an unchanged generation keeps the run.

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

    def test_packet_without_intent_unchanged_generation_restores_content(self) -> None:
        self._packet_without_intent(reset=False)

    def test_packet_without_intent_reset_generation_settles_without_content(self) -> None:
        self._packet_without_intent(reset=True)

    def _packet_without_intent(self, *, reset: bool) -> None:
        from director_orchestrator.approvals import reset_for_owner_clarification

        with tempfile.TemporaryDirectory() as directory:
            settings, store, record, assignment = self._world(directory)
            engine = OrchestrationEngine(settings, store)
            key = _key("packet")
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            record.provider_runs.append(ProviderRun(
                provider=assignment.agent, run_id="packet-run", status="completed",
                result="packet-content", raw={"usage": {"cost": 0.25}},
            ))
            generation = record.plan_generation
            if reset:
                current = store.get(record.task_id)
                reset_for_owner_clarification(current)
                # Simulate the replacement plan after the in-flight dispatch.
                current.plan_generation += 1
                current.plan = record.plan.model_copy(deep=True)
                seed_production_plan_materialization(current)
                self.assertNotEqual(current.plan_generation, generation)
                store.save(current)
            outcome = SimpleNamespace(result_packet={
                "run_reference": "packet-run", "outcome": "packet-content", "usage": {"cost_usd": 0}})
            self.assertEqual(engine._settle_packet_spend(record, assignment, key, outcome), not reset)
            saved = store.get(record.task_id)
            if reset:
                self.assertEqual(saved.provider_runs, [])
                self.assertNotIn("packet-content", saved.model_dump_json())
            else:
                self.assertEqual(saved.provider_runs[-1].result, "packet-content")
                self.assertEqual(saved.provider_runs[-1].plan_generation, generation)
            held = engine.provider_spend.settlements(record.task_id)[key]
            self.assertEqual((held["provider"], held["run_id"], held["cost"], held["pending"]),
                             (assignment.agent.value, "packet-run", 0.25, False))
            self.assertEqual(held["plan_generation"], generation)
            for field in ("phase", "assignment_sequence", "assignment_digest"):
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
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:21 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:49 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:39 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 22:07 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:24 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:35 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:45 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 01:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 01:28 UTC | END]]
