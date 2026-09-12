# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 11:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | BEGIN]]
"""Cancellation residue repairs use the sanctioned PostgreSQL test lane only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace, fields
from contextlib import ExitStack
import asyncio
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from director_orchestrator.primary_default import DefaultPrimaryActivation, _digest
from director_orchestrator.config import Settings
from director_orchestrator.primary_parallel import ParallelAssignment, ParallelDispatchError, ParallelDispatchPolicy
from tests import test_primary_cancellation as cancellation_fixtures
from tests import test_primary_coordinator as coordinator_fixtures


class CancelledPendingQuiescenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cancellation_fixtures.AuthenticatedCancellationTests()
        self.fixture.setUpClass()
        self.addCleanup(self.fixture.tearDownClass)
        request_factory = coordinator_fixtures.request
        # The reused cancellation fixture normally records graph CLAIMED/unit
        # bookkeeping before it pauses. This suite must instead start at the owner approval gate.
        with patch.object(coordinator_fixtures, "request", side_effect=lambda task_id, **overrides:
                request_factory(task_id, **{**overrides, "execution_approval_required": True})):
            self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.coordinator = self.fixture.coordinator
        self.parallel = self.coordinator.parallel
        self.schema = self.fixture.schema
        self.task = self.fixture.task
        self.pool = self.fixture.handle.pool

    def rows(self):
        with self.pool.connection() as connection:
            return connection.execute(f"SELECT * FROM {self.schema}.director_primary_parallel_assignment "
                "WHERE task_id=%s ORDER BY assignment_id", (self.task,)).fetchall()

    def receipts(self):
        with self.pool.connection() as connection:
            return connection.execute(f"SELECT * FROM {self.schema}.director_primary_command_receipt "
                "WHERE task_id=%s ORDER BY command_id", (self.task,)).fetchall()

    def audits(self):
        with self.pool.connection() as connection:
            return connection.execute(f"SELECT * FROM {self.schema}.director_primary_audit_event "
                "WHERE task_id=%s AND event_code='cancelled_pending_reconciled' ORDER BY event_id",
                (self.task,)).fetchall()

    def historical(self):
        # Execute the actual signed cancellation but emulate the former missing
        # cleanup call. The persisted cancellation/receipt is not manufactured.
        with patch.object(self.parallel, "reconcile_cancelled_pending", return_value=0):
            response = self.fixture.send(self.fixture.signed())
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("CANCELLED", response.json()["terminal_classification"])
        self.assertEqual({"PENDING"}, {row["status"] for row in self.rows()})

    def repair(self):
        return self.parallel.reconcile_cancelled_pending(task_id=self.task,
            writer=self.coordinator.get_task_writer(self.task))

    def copied_state_shape(self):
        """Sanitize the verified 2026-09-12 copied-state relationship, not its content.

        Two tasks/four PENDING unreserved rows: one CANCELLED+killed with unequal
        projection/batch bindings, one owner-PAUSED with equal bindings. The
        historical cause of the unequal digests is not present in the summary.
        """
        self.historical()
        cancelled=self.task
        with self.pool.connection() as c,c.transaction():
            row=c.execute(f"SELECT projection FROM {self.schema}.director_primary_task_projection WHERE task_id=%s",(cancelled,)).fetchone()
            payload=row['projection']
            payload['parallel_plan_digest']=coordinator_fixtures.digest('sanitized-surviving-projection-plan')
            c.execute(f"UPDATE {self.schema}.director_primary_task_projection SET projection=%s::jsonb,projection_digest=%s,projection_version=projection_version+1 WHERE task_id=%s",
                (json.dumps(payload),_digest(payload),cancelled))
        self.another_task()
        paused=self.task
        self.task=cancelled
        self.fixture.task=cancelled
        return paused

    def activation(self):
        activation=object.__new__(DefaultPrimaryActivation)
        activation.coordinator=self.coordinator
        activation.services=SimpleNamespace(available_provider_lanes=())
        activation.application=SimpleNamespace(recover=self.coordinator.recover)
        settings=Settings()
        cleared={field.name:'' for field in fields(settings)
            if isinstance(getattr(settings,field.name),str) and any(word in field.name for word in ('api_key','token','secret','password'))}
        activation.settings=replace(settings,**cleared,allow_paid_runs=False,
            langgraph_primary_live_provider_dispatch_enabled=False,langgraph_primary_new_intake_enabled=False)
        activation.provider_activation=None
        activation._live_dependencies_ready=False
        activation.handle=None  # Fixture owns its isolated database lifecycle.
        return activation

    def hold_audits(self):
        with self.pool.connection() as c:
            return c.execute(f"SELECT * FROM {self.schema}.director_primary_audit_event WHERE task_id=%s AND event_code='cancelled_plan_binding_hold'",(self.task,)).fetchall()

    def test_observed_copied_state_mismatch_is_durable_not_successful_cleanup(self):
        paused=self.copied_state_shape()
        cancelled_before=self.coordinator.get_projection(self.task).projection
        paused_before=self.coordinator.get_projection(paused).projection
        rows,receipts=self.rows(),self.receipts()
        activation=self.activation()
        activation.reconcile_once()
        self.assertFalse(activation._reconciliation_worker_ready)
        health=activation.health()
        self.assertEqual(1,health['cancelled_plan_binding_hold_count'])
        self.assertEqual('cancelled_parallel_plan_requires_owner_reconciliation',health['reconciliation_hold_reason'])
        self.assertFalse(health['primary_runtime_active'])
        self.assertFalse(health['paid_off_primary_intake_active'])
        self.assertEqual(cancelled_before,self.coordinator.get_projection(self.task).projection)
        self.assertEqual(paused_before,self.coordinator.get_projection(paused).projection)
        self.assertEqual('PAUSED',self.coordinator.get_projection(paused).projection['run_status'])
        self.assertEqual(rows,self.rows())
        self.assertEqual(receipts,self.receipts())
        audit=self.hold_audits()
        self.assertEqual(1,len(audit))
        activation.reconcile_once()
        self.assertEqual(audit,self.hold_audits())
        self.assertFalse(activation._reconciliation_worker_ready)
        self.fixture.provider.assert_not_called()
        self.fixture.effect.assert_not_called()

    def test_binding_hold_does_not_allow_live_or_paid_startup(self):
        self.copied_state_shape()
        for field in ('allow_paid_runs','langgraph_primary_live_provider_dispatch_enabled'):
            activation=self.activation()
            activation.settings=replace(activation.settings,**{field:True})
            with self.subTest(field=field),self.assertRaisesRegex(ParallelDispatchError,'owner reconciliation required'):
                activation.reconcile_once()
            self.assertFalse(activation._reconciliation_worker_ready)
        self.assertEqual(1,len(self.hold_audits()))
        self.assertEqual({'PENDING'},{row['status'] for row in self.rows()})
        self.fixture.provider.assert_not_called()
        self.fixture.effect.assert_not_called()

    def test_unexpected_reconciliation_errors_still_abort(self):
        self.historical()
        activation=self.activation()
        with patch.object(self.coordinator,'reconcile_cancelled_pending',side_effect=ParallelDispatchError('unexpected fence/storage refusal')):
            with self.assertRaisesRegex(ParallelDispatchError,'unexpected fence/storage refusal'):
                activation.reconcile_once()
        self.assertFalse(activation._reconciliation_worker_ready)

    def test_whole_fastapi_lifespan_serves_diagnostics_with_copied_shape_hold(self):
        from fastapi.testclient import TestClient
        import httpx
        self.copied_state_shape()
        activation=self.activation()
        main=self.fixture.main
        async def parked():
            await asyncio.Future()
        # Real app and lifespan, preinitialized isolated PostgreSQL coordinator.
        # Non-graph external/background services are inert; no host listener,
        # provider credentials, copied private records or external HTTP is used.
        with ExitStack() as stack:
            stack.enter_context(patch.object(main,'primary_default_activation',activation))
            stack.enter_context(patch.object(main,'settings',activation.settings))
            stack.enter_context(patch.object(main,'store',SimpleNamespace(
                claim_privacy_cleanup_resumes=lambda:(),list_recoverable=lambda:())))
            stack.enter_context(patch.object(main.chatgpt_bridge.store,'sweep_expired_answers',return_value=None))
            stack.enter_context(patch.object(main.usage_monitor,'run_forever',side_effect=parked))
            stack.enter_context(patch.object(main.runtime_activation,'close',return_value=None))
            for name in ('_privacy_cleanup_recovery_loop','_recoverable_task_loop','_chatgpt_bridge_expiry_loop',
                    '_primary_intake_lease_loop','_primary_reconciliation_loop','_primary_slack_delivery_loop'):
                stack.enter_context(patch.object(main,name,side_effect=parked))
            stack.enter_context(patch.object(httpx.HTTPTransport,'handle_request',side_effect=AssertionError('external HTTP forbidden')))
            stack.enter_context(patch.object(httpx.AsyncHTTPTransport,'handle_async_request',side_effect=AssertionError('external HTTP forbidden')))
            started=time.monotonic()
            with TestClient(main.app) as client:
                self.assertEqual(200,client.get('/openapi.json').status_code)
                self.assertFalse(activation.health()['reconciliation_worker_ready'])
                self.assertEqual(1,len(self.hold_audits()))
            self.assertLess(time.monotonic()-started,10)
        self.fixture.provider.assert_not_called()
        self.fixture.effect.assert_not_called()

    def another_task(self, *, claimed=False, dependent=False):
        self.task += '-other'
        self.fixture.task = self.task
        self.fixture.path = f'/v1/primary/tasks/{self.task}/cancel'
        self.coordinator.create_intake(coordinator_fixtures.request(self.task,
            execution_approval_required=not claimed))
        self.coordinator.register_parallel_plan(task_id=self.task,
            plan_digest=coordinator_fixtures.digest('another-plan'),
            assignments=(ParallelAssignment('one', 1, 'openai', 'openai-test'),
                ParallelAssignment('two', 2, 'claude', 'claude-test', dependencies=('one',) if dependent else ())),
            policy=ParallelDispatchPolicy())

    def test_signed_cancellation_closes_only_unstarted_rows_and_replay_is_stable(self):
        request = self.fixture.signed()
        response = self.fixture.send(request)
        self.assertEqual(200, response.status_code, response.text)
        rows = self.rows()
        self.assertEqual({"FAILED"}, {row["status"] for row in rows})
        self.assertEqual({"task_cancelled_before_dispatch"}, {row["reason_code"] for row in rows})
        self.assertTrue(all(row["completed_at"] and row["reservation_digest"] is None
                            and row["result_digest"] is None for row in rows))
        self.assertEqual(0, self.fixture.handle.claim_store.primary_quiescence_metrics().active_assignment_count)
        receipts, audits = self.receipts(), self.audits()
        self.assertEqual(1, len(audits))
        self.assertEqual(200, self.fixture.send(request).status_code)
        self.assertEqual(0, self.coordinator.reconcile_cancelled_pending(self.task))
        self.assertEqual(rows, self.rows())
        self.assertEqual(receipts, self.receipts())
        self.assertEqual(audits, self.audits())
        self.fixture.provider.assert_not_called()
        self.fixture.effect.assert_not_called()

    def test_existing_reconciliation_worker_repairs_historical_cancellation_without_graph_or_new_command(self):
        self.historical()
        before = self.coordinator.get_projection(self.task)
        receipts = self.receipts()
        activation = object.__new__(DefaultPrimaryActivation)
        activation.coordinator = self.coordinator
        activation.services = object()
        activation.application = Mock()
        with (patch.object(self.coordinator._runtime, "recover", side_effect=AssertionError("no graph recovery")),
              patch.object(self.coordinator._runtime, "cancel", side_effect=AssertionError("no new cancellation"))):
            self.assertEqual(0, activation.reconcile_once())
            rows, audits = self.rows(), self.audits()
            self.assertEqual({"FAILED"}, {row["status"] for row in rows})
            writer = self.coordinator.get_task_writer(self.task)
            self.assertEqual(0, activation.reconcile_once())
            self.assertEqual(writer, self.coordinator.get_task_writer(self.task))
        self.assertEqual(before, self.coordinator.get_projection(self.task))
        self.assertEqual(receipts, self.receipts())
        self.assertEqual(rows, self.rows())
        self.assertEqual(audits, self.audits())
        activation.application.recover.assert_not_called()
        self.fixture.provider.assert_not_called()
        self.fixture.effect.assert_not_called()

    def test_owner_paused_task_is_untouched_and_requires_no_writer_renewal(self):
        before, rows, writer = self.coordinator.get_projection(self.task), self.rows(), self.coordinator.get_task_writer(self.task)
        with patch.object(self.coordinator, "_task_writer", side_effect=AssertionError("no writer claim")):
            self.assertEqual(0, self.coordinator.reconcile_cancelled_pending(self.task))
        self.assertEqual(0, self.repair())
        self.assertEqual(before, self.coordinator.get_projection(self.task))
        self.assertEqual(rows, self.rows())
        self.assertEqual(writer, self.coordinator.get_task_writer(self.task))

    def test_stale_generation_and_foreign_writer_are_refused_without_mutation(self):
        self.historical()
        writer, rows = self.coordinator.get_task_writer(self.task), self.rows()
        for invalid in (replace(writer, fencing_generation=writer.fencing_generation + 1),
                        replace(writer, owner_token="foreign-owner")):
            with self.assertRaisesRegex(ParallelDispatchError, "current writer authority"):
                self.parallel.reconcile_cancelled_pending(task_id=self.task, writer=invalid)
        self.assertEqual(rows, self.rows())
        self.assertEqual([], self.audits())
        with self.assertRaisesRegex(ParallelDispatchError, 'does not authorize'):
            self.parallel.reconcile_cancelled_pending(task_id=self.task,
                writer=replace(writer, task_id='another-task'))

    def test_claimed_graph_bookkeeping_without_real_dispatch_evidence_is_repaired(self):
        self.another_task(claimed=True)
        self.historical()
        before = self.coordinator.get_projection(self.task)
        self.assertEqual('CLAIMED', before.projection['provider_dispatch_status'])
        self.assertGreater(before.projection['budget_used_units'], 0)
        receipts = self.receipts()
        self.assertEqual(2, self.coordinator.reconcile_cancelled_pending(self.task))
        self.assertEqual({'FAILED'}, {row['status'] for row in self.rows()})
        self.assertEqual(before, self.coordinator.get_projection(self.task))
        self.assertEqual(receipts, self.receipts())
        self.assertEqual(0, self.coordinator.reconcile_cancelled_pending(self.task))

    def test_dependency_blocked_presentation_does_not_hide_stored_pending_row(self):
        self.another_task(dependent=True)
        self.historical()
        with self.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment SET status='FAILED' "
                "WHERE task_id=%s AND assignment_id='one'", (self.task,))
        self.assertEqual('BLOCKED', self.parallel.aggregate(self.task).statuses['two'])
        self.assertEqual(1, self.coordinator.reconcile_cancelled_pending(self.task))
        self.assertEqual({'FAILED'}, {row['status'] for row in self.rows()})

    def test_expired_lease_during_projection_lock_wait_cannot_repair(self):
        self.historical()
        with self.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_task_writer "
                "SET lease_expires_at=clock_timestamp()+interval '2 seconds' WHERE task_id=%s", (self.task,))
        writer, rows = self.coordinator.get_task_writer(self.task), self.rows()
        entered = threading.Event()
        original = self.parallel._row
        def observe(connection, statement, parameters):
            if "SELECT p.projection" in statement:
                entered.set()
            return original(connection, statement, parameters)
        with patch.object(self.parallel, "_row", side_effect=observe), ThreadPoolExecutor(max_workers=1) as executor:
            with self.pool.connection() as connection, connection.transaction():
                connection.execute(f"SELECT task_id FROM {self.schema}.director_primary_task_projection "
                    "WHERE task_id=%s FOR UPDATE", (self.task,))
                future = executor.submit(self.parallel.reconcile_cancelled_pending, task_id=self.task, writer=writer)
                self.assertTrue(entered.wait(timeout=5))
                time.sleep(2.1)  # Deliberate local lock-contention test, not live polling.
            with self.assertRaisesRegex(ParallelDispatchError, "expired during reconciliation"):
                future.result(timeout=5)
        self.assertEqual(rows, self.rows())
        self.assertEqual([], self.audits())

    def test_active_and_ambiguous_rows_are_preserved(self):
        self.historical()
        with self.pool.connection() as connection:
            for identity, status in (("one", "ACTIVE"), ("two", "AMBIGUOUS")):
                connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment "
                    "SET status=%s,reservation_digest=%s,reserved_at=now() WHERE task_id=%s AND assignment_id=%s",
                    (status, coordinator_fixtures.digest(identity), self.task, identity))
        before = self.rows()
        self.assertEqual(0, self.repair())
        self.assertEqual(before, self.rows())
        self.assertEqual(2, self.fixture.handle.claim_store.primary_quiescence_metrics().active_assignment_count)

    def test_pending_reservation_and_result_evidence_are_preserved(self):
        self.historical()
        with self.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment "
                "SET reservation_digest=%s,reserved_at=now() WHERE task_id=%s AND assignment_id='one'",
                (coordinator_fixtures.digest("reserved"), self.task))
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment "
                "SET result_digest=%s,completed_at=now() WHERE task_id=%s AND assignment_id='two'",
                (coordinator_fixtures.digest("result"), self.task))
        before = self.rows()
        self.assertEqual(0, self.repair())
        self.assertEqual(before, self.rows())

    def test_pending_timestamps_and_unknown_reason_are_preserved(self):
        self.historical()
        with self.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment "
                "SET reserved_at=now() WHERE task_id=%s AND assignment_id='one'", (self.task,))
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment "
                "SET reason_code='provider_outcome_unknown' WHERE task_id=%s AND assignment_id='two'", (self.task,))
        before = self.rows()
        self.assertEqual(0, self.repair())
        self.assertEqual(before, self.rows())

    def test_provider_attempt_and_packet_evidence_preserve_pending_rows(self):
        self.historical()
        digest = coordinator_fixtures.digest("history")
        with self.pool.connection() as connection:
            connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_attempt "
                "(task_id,assignment_id,attempt_ordinal,event_sequence,provider_id,format_id,transport_id,"
                "idempotency_key_digest,attempt_digest,status,reason_code,budget_evidence_digest) "
                "VALUES (%s,'one',1,1,'openai','fixture','fixture',%s,%s,'STARTED','provider_attempt_started',%s)",
                (self.task, digest, digest, digest))
            connection.execute(f"INSERT INTO {self.schema}.director_primary_result_packet_artifact "
                "(task_id,assignment_id,authority_snapshot_digest,document_digest,canonical_document,writer_fencing_generation) "
                "VALUES (%s,'two',%s,%s,'{}',1)", (self.task, digest, digest))
        before = self.rows()
        self.assertEqual(0, self.repair())
        self.assertEqual(before, self.rows())

    def test_budget_charge_preserves_all_pending_rows(self):
        self.historical()
        self.fixture.handle.claim_store.register_task_budget(self.task, budget_limit_units=4)
        with self.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_budget "
                "SET budget_reserved_units=1 WHERE task_id=%s", (self.task,))
        before = self.rows()
        self.assertEqual(0, self.repair())
        self.assertEqual(before, self.rows())
        with self.pool.connection() as connection:
            self.assertEqual(1, connection.execute(f"SELECT budget_reserved_units FROM {self.schema}.director_primary_budget "
                "WHERE task_id=%s", (self.task,)).fetchone()["budget_reserved_units"])

    def test_monetary_spend_preserves_all_pending_rows(self):
        self.another_task(claimed=True)
        self.historical()
        digest = coordinator_fixtures.digest("money")
        with self.pool.connection() as connection:
            connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget VALUES (%s,'{{}}')", (digest,))
            connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_spend "
                "(attempt_digest,grant_digest,provider_id,project_id,task_id,budget_day,reserved_microusd,token_limit) "
                "VALUES (%s,%s,'openai','fixture',%s,CURRENT_DATE,1,1)", (digest, digest, self.task))
        before = self.rows()
        self.assertEqual(0, self.repair())
        self.assertEqual(before, self.rows())

    def test_effect_claim_preserves_all_pending_rows(self):
        self.another_task(claimed=True)
        self.historical()
        digest = coordinator_fixtures.digest("claim")
        writer = self.coordinator.get_task_writer(self.task)
        with self.pool.connection() as connection:
            connection.execute(f"INSERT INTO {self.schema}.director_primary_claim "
                "(claim_key,task_id,field,expected_state,target_state,attempt_digest,owner_token,fencing_generation) "
                "VALUES (%s,%s,'effect_status','NOT_STARTED','CLAIMED',%s,%s,%s)",
                (digest, self.task, digest, writer.owner_token, writer.fencing_generation))
        before = self.rows()
        self.assertEqual(0, self.repair())
        self.assertEqual(before, self.rows())

    def test_workload_admission_preserves_its_assignment(self):
        self.historical()
        digest = coordinator_fixtures.digest('workload')
        with self.pool.connection() as connection:
            connection.execute(f"INSERT INTO {self.schema}.director_primary_workload_assignment "
                "(task_id,assignment_id,provider_lane,reservation_digest,fencing_generation,policy_version,policy_digest) "
                "VALUES (%s,'one','openai',%s,1,'primary-assignment-count-v3',%s)", (self.task, digest, digest))
        before = self.rows()[0]
        self.assertEqual(1, self.repair())
        self.assertEqual(before, self.rows()[0])
        self.assertEqual('FAILED', self.rows()[1]['status'])

    def test_kill_switch_and_plan_binding_are_required(self):
        self.historical()
        before = self.rows()
        with self.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_batch SET kill_switch=false WHERE task_id=%s", (self.task,))
        self.assertEqual(0, self.repair())
        with self.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_batch SET kill_switch=true,plan_digest=%s WHERE task_id=%s",
                (coordinator_fixtures.digest("changed-plan"), self.task))
        with self.assertRaisesRegex(ParallelDispatchError, "plan binding changed"):
            self.repair()
        self.assertEqual(before, self.rows())

    def test_atomic_audit_failure_rolls_back_assignment_updates(self):
        self.historical()
        before = self.rows()
        with self.pool.connection() as connection:
            connection.execute(f"""CREATE FUNCTION {self.schema}.reject_repair_audit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'synthetic audit failure'; END; $$""")
            connection.execute(f"CREATE TRIGGER reject_repair BEFORE INSERT ON {self.schema}.director_primary_audit_event "
                f"FOR EACH ROW EXECUTE FUNCTION {self.schema}.reject_repair_audit()")
        with self.assertRaisesRegex(Exception, "synthetic audit failure"):
            self.repair()
        self.assertEqual(before, self.rows())
        self.assertEqual([], self.audits())

    def test_concurrent_repair_records_one_audit_and_cannot_dispatch(self):
        self.historical()
        writer = self.coordinator.get_task_writer(self.task)
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: self.parallel.reconcile_cancelled_pending(
                task_id=self.task, writer=writer), range(2)))
        self.assertEqual([0, 2], sorted(outcomes))
        self.assertEqual(1, len(self.audits()))
        self.assertEqual((), self.parallel.reserve_ready(task_id=self.task, writer=writer,
            policy=ParallelDispatchPolicy()))


if __name__ == '__main__':
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | END]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 11:23 UTC | END]]
