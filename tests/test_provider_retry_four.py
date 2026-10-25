"""Exercise the owner-authorized budget through real consumers, without providers."""
from contextlib import closing
import asyncio
from dataclasses import replace
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger, LedgerError
from director_orchestrator.approvals import reserve_task_effect, task_effect_key
from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.models import SlackIntake, TaskRecord, TaskStatus
from director_orchestrator.store import StaleTaskWrite, TaskStore
from director_orchestrator.primary_default import _execute_three_attempt_roster
from director_orchestrator.primary_parallel import (
    ParallelAssignmentStatus, ProviderAttemptReservation, ProviderTransientDispatchError,
)
from director_orchestrator.workload_policy import FailureKind, RetryRequest, evaluate_retry
from tests.test_engineering_workload_ledger import manifest, request, result


class FourRetryTests(unittest.TestCase):
    def test_five_attempts_share_resource_and_ambiguity_gates(self):
        for ordinal in range(2, 6):
            values = dict(attempt_number=ordinal, retry_limit=5,
                          previous_failure=FailureKind.TRANSIENT)
            self.assertTrue(evaluate_retry(RetryRequest(**values)).allowed)
            for gate in ("token_budget_remaining", "cost_budget_remaining", "timeout_budget_remaining"):
                with self.subTest(ordinal=ordinal, gate=gate):
                    self.assertFalse(evaluate_retry(RetryRequest(**values, **{gate: False})).allowed)
            self.assertFalse(evaluate_retry(RetryRequest(**values, cancellation_requested=True)).allowed)
            values["previous_failure"] = FailureKind.AMBIGUOUS_PAID_DISPATCH
            self.assertFalse(evaluate_retry(RetryRequest(**values, materially_new_information=True)).allowed)
        self.assertFalse(evaluate_retry(RetryRequest(
            attempt_number=6, retry_limit=5, previous_failure=FailureKind.TRANSIENT)).allowed)
        self.assertFalse(evaluate_retry(RetryRequest(
            attempt_number=4, retry_limit=3, previous_failure=FailureKind.TRANSIENT)).allowed)

    def test_roster_runs_fifth_attempt_and_stops_on_success(self):
        invoked, settled = [], []

        def reserve(ordinal):
            return ProviderAttemptReservation("task", "assignment", ordinal, "openai", "json", "api",
                                              "sha256:" + "a" * 64, "sha256:" + "b" * 64,
                                              "sha256:" + "c" * 64)

        def dispatch(attempt):
            invoked.append(attempt.attempt_ordinal)
            if attempt.attempt_ordinal < 5:
                raise ProviderTransientDispatchError("definite transient refusal")
            return {"status": "SUCCEEDED"}

        outcome = _execute_three_attempt_roster(
            retry_limit=5, format_roster=(("json", "api"),), reserve_attempt=reserve,
            record_attempt=lambda attempt, *_: settled.append(attempt.attempt_ordinal),
            dispatch_attempt=dispatch)
        self.assertIs(outcome[0], ParallelAssignmentStatus.SUCCEEDED)
        self.assertEqual([1, 2, 3, 4, 5], invoked)
        self.assertEqual(invoked, settled)

    def test_sqlite_upgrade_retains_history_and_append_only_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            ledger = EngineeringWorkloadLedger(path)
            with patch("director_orchestrator.engineering_workload_ledger.MAX_PROVIDER_ATTEMPTS", 3):
                ledger.bootstrap(manifest())
                ledger.reserve(request(1))
                ledger.begin_attempt("test-1", transport="local_cli")
                ledger.record_outcome("test-1", 1, result("transient"))
            with closing(sqlite3.connect(path)) as db:
                before = {table: db.execute(f"SELECT * FROM {table}").fetchall()
                          for table in ("metadata", "assignments", "attempts", "outcomes")}
            candidate = request(2)
            candidate["bounds"]["call_limit"] = 5
            ledger.reserve(candidate)  # First new write widens only the table constraint.
            with closing(sqlite3.connect(path)) as db:
                for table, rows in before.items():
                    self.assertEqual(rows, db.execute(f"SELECT * FROM {table} LIMIT ?", (len(rows),)).fetchall())
                self.assertEqual([], db.execute("PRAGMA foreign_key_check").fetchall())
                for table in ("attempts", "outcomes"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
                        db.execute(f"DELETE FROM {table}")
            for ordinal in range(1, 5):
                permit = ledger.begin_attempt("test-2", transport="local_cli")
                self.assertEqual(ordinal, permit["attempt_number"])
                ledger.record_outcome("test-2", ordinal, result("transient"))
            # The old failure still counts against the route: a new assignment
            # must not reset the global route's five recorded failures.
            with self.assertRaisesRegex(LedgerError, "route_unavailable"):
                ledger.begin_attempt("test-2", transport="provider_api")

    def test_ledger_allows_five_and_refuses_six_after_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            ledger = EngineeringWorkloadLedger(path)
            ledger.bootstrap(manifest())
            candidate = request(1)
            candidate["bounds"]["call_limit"] = 5
            ledger.reserve(candidate)
            for ordinal in range(1, 6):
                ledger = EngineeringWorkloadLedger(path)
                permit = ledger.begin_attempt("test-1", transport="local_cli")
                self.assertEqual(ordinal, permit["attempt_number"])
                ledger.record_outcome("test-1", ordinal, result("transient"))
            with self.assertRaisesRegex(LedgerError, "route_unavailable"):
                ledger.begin_attempt("test-1", transport="browser")
            with self.assertRaisesRegex(LedgerError, "route_unavailable"):
                ledger.reserve(request(2))


class FourMessageRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = replace(Settings(), app_env="retry-test", allow_paid_runs=False,
                                slack_bot_token="fixture-slack", sqlite_path=str(Path(directory.name) / "tasks.sqlite3"))
        self.task = TaskRecord(task_id="DIR-RETRY", status=TaskStatus.QUEUED,
            intake=SlackIntake(source_event_id="retry", channel_id="C1", thread_ts="1.1", user_id="U1", text="Status"))
        self.store = TaskStore(self.settings.sqlite_path)
        self.store.create(self.task)
        self.engine = OrchestrationEngine(self.settings, self.store)

    async def test_message_restart_preserves_five_total_and_refuses_counter_rewind(self):
        class InterruptedNotifier:
            calls = []

            async def post(self, record, message, **kwargs):
                self.calls.append(kwargs["client_msg_id"])
                if len(self.calls) == 2:
                    raise asyncio.CancelledError()
                raise RuntimeError("definite fixture refusal")

        notifier = InterruptedNotifier()
        self.engine.notifier = notifier
        with self.assertRaises(asyncio.CancelledError):
            await self.engine._deliver_collaboration_message(self.task, "bounded message")
        task = self.store.get(self.task.task_id)
        self.assertEqual([2], list(task.collaboration_delivery_attempts.values()))
        restarted = OrchestrationEngine(self.settings, self.store)
        restarted.notifier = notifier
        await restarted._deliver_collaboration_message(task, "bounded message")
        self.assertEqual(5, len(notifier.calls))
        self.assertEqual(1, len(set(notifier.calls)))
        task = self.store.get(self.task.task_id)
        self.assertEqual([5], list(task.collaboration_delivery_attempts.values()))
        await restarted._deliver_collaboration_message(task, "bounded message")
        self.assertEqual(5, len(notifier.calls))
        for key in task.collaboration_delivery_attempts:
            task.collaboration_delivery_attempts[key] = 1
        with self.assertRaisesRegex(StaleTaskWrite, "cannot be reset"):
            self.store.save(task)

    async def test_legacy_uncounted_intent_holds_without_another_post(self):
        client_id = "legacy-fixture"
        key = task_effect_key(self.task, phase="slack_delivery", identity={
            "client_msg_id": client_id, "plan_generation": self.task.plan_generation})
        reserve_task_effect(self.task, effect_key=key, kind="slack_intent", phase="slack_delivery", replay_safe=True)
        self.store.save(self.task)
        from unittest.mock import AsyncMock
        notifier = AsyncMock()
        self.engine.notifier = notifier
        await self.engine._deliver_collaboration_message(self.store.get(self.task.task_id),
                                                          "legacy message", client_msg_id=client_id)
        notifier.post.assert_not_awaited()
        task = self.store.get(self.task.task_id)
        self.assertTrue(task.collaboration_fallback_active)
        self.assertIn("reconciliation", task.collaboration_fallback_reason)
