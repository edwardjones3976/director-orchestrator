# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:58 UTC | BEGIN]]
"""Owner-signed cancelled-plan adjudication on the sanctioned PostgreSQL lane."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import Depends

from director_orchestrator.primary_commands import build_primary_ingress_proof, verify_primary_ingress_proof
from director_orchestrator.primary_parallel import (
    CancelledPlanReconciliationRequest, CancelledPlanReconciliationResponse,
)
from tests import test_cancelled_pending_quiescence as fixtures
from tests.test_primary_coordinator import digest


class CancelledPlanReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CancelledPendingQuiescenceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.coordinator = self.f.coordinator
        self.parallel = self.f.parallel
        self.pool, self.schema = self.f.pool, self.f.schema
        self.client, self.main = self.f.fixture.client, self.f.fixture.main
        self.client.app.add_api_route('/v1/primary/tasks/{task_id}/cancelled-plan/reconcile',
            self.main.primary_reconcile_cancelled_plan, methods=['POST'],
            response_model=CancelledPlanReconciliationResponse, dependencies=[Depends(self.main.require_auth)])
        self.client.app.add_api_route('/v1/primary/tasks/{task_id}/cancelled-plan/diagnostic',
            self.main.primary_cancelled_plan_diagnostic, methods=['GET'], dependencies=[Depends(self.main.require_auth)])

    def copied(self):
        return self.f.copied_state_shape()

    def path(self, task=None):
        return f'/v1/primary/tasks/{task or self.f.task}/cancelled-plan/reconcile'

    def signed(self, task=None, *, nonce='r' * 32, issued_at=None, signing_path=None, **changes):
        task = task or self.f.task
        diagnostic = self.parallel.cancelled_plan_diagnostic(task)
        values = dict(task_id=task, command_id='reconcile-command', actor_id='UOWNER',
            expected_projection_digest=diagnostic['projection_digest'],
            expected_fencing_generation=diagnostic['fencing_generation'],
            expected_projection_plan_digest=diagnostic['projection_plan_digest'],
            expected_batch_plan_digest=diagnostic['batch_plan_digest'],
            expected_diagnostic_digest=diagnostic['diagnostic_digest'],
            adjudication='retain_both_digests_close_unstarted')
        values.update(changes)
        request = CancelledPlanReconciliationRequest(**values)
        proof = build_primary_ingress_proof('g' * 40, 'test-bearer', request,
            path=signing_path or self.path(task), proof_field='gateway_proof', channel_id='CTEST',
            nonce=nonce, issued_at=issued_at or int(time.time()))
        return request.model_copy(update={'gateway_proof': proof})

    def send(self, request, *, path=None, headers=None):
        return self.client.post(path or self.path(request.task_id), json=request.model_dump(mode='json'),
            headers=self.f.fixture.headers if headers is None else headers)

    def evidence(self):
        with self.pool.connection() as c:
            return c.execute(f'SELECT evidence,evidence_digest FROM {self.schema}.director_primary_cancelled_plan_adjudication '
                             'WHERE task_id=%s', (self.f.task,)).fetchall()

    def audit_count(self):
        with self.pool.connection() as c:
            return c.execute(f"SELECT count(*) AS n FROM {self.schema}.director_primary_audit_event "
                "WHERE task_id=%s AND event_code='cancelled_plan_reconciled'", (self.f.task,)).fetchone()['n']

    def snapshot(self, task=None):
        task = task or self.f.task
        with self.pool.connection() as c:
            # Hash safe batch columns internally; never return or print batch rows.
            batch = c.execute(f'SELECT plan_digest,kill_switch FROM {self.schema}.director_primary_parallel_batch '
                              'WHERE task_id=%s', (task,)).fetchone()
            assignments = c.execute(f'SELECT assignment_id,status,reservation_digest,result_digest,reason_code,completed_at '
                f'FROM {self.schema}.director_primary_parallel_assignment WHERE task_id=%s ORDER BY assignment_id',
                (task,)).fetchall()
            authorizations = c.execute(f'SELECT count(*) AS n FROM {self.schema}.director_primary_command_authorization '
                                      'WHERE task_id=%s', (task,)).fetchone()['n']
        return (self.coordinator.get_projection(task), fixtures._digest(batch), assignments, authorizations)

    def update_projection(self, **changes):
        projection = self.coordinator.get_projection(self.f.task).projection
        projection.update(changes)
        with self.pool.connection() as c:
            c.execute(f'UPDATE {self.schema}.director_primary_task_projection SET projection=%s::jsonb, '
                'projection_digest=%s,projection_version=projection_version+1 WHERE task_id=%s',
                (json.dumps(projection), fixtures._digest(projection), self.f.task))

    def test_copied_shape_is_atomic_replayable_and_readiness_waits_for_next_pass(self):
        paused = self.copied()
        paused_before = self.snapshot(paused)
        activation = self.f.activation()
        activation.reconcile_once()
        self.assertFalse(activation.health()['reconciliation_worker_ready'])
        before = self.coordinator.get_projection(self.f.task)
        receipts = self.f.receipts()
        request = self.signed()
        with patch.object(self.coordinator._runtime, 'cancel', side_effect=AssertionError('no graph cancellation')):
            response = self.send(request)
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.json()['closed_assignment_count'])
        self.assertTrue(response.json()['applied'])
        self.assertFalse(activation.health()['reconciliation_worker_ready'])
        self.assertFalse(activation.health()['paid_off_primary_intake_active'])
        after = self.snapshot()
        self.assertEqual(1, self.audit_count())
        evidence = self.evidence()
        self.assertEqual(1, len(evidence))
        self.assertEqual(request.expected_projection_plan_digest, evidence[0]['evidence']['projection_plan_digest'])
        self.assertEqual(request.expected_batch_plan_digest, evidence[0]['evidence']['batch_plan_digest'])
        replay = self.send(request)
        self.assertEqual(200, replay.status_code)
        self.assertEqual({'applied': False, 'closed_assignment_count': 0,
                          'evidence_digest': response.json()['evidence_digest']}, replay.json())
        self.assertEqual(after, self.snapshot())
        self.assertEqual(evidence, self.evidence())
        self.assertEqual(1, self.audit_count())
        self.assertEqual(before, self.coordinator.get_projection(self.f.task))
        self.assertEqual(receipts, self.f.receipts())
        activation.settings = replace(activation.settings, langgraph_primary_new_intake_enabled=True)
        activation.reconcile_once()
        self.assertTrue(activation.health()['reconciliation_worker_ready'])
        self.assertTrue(activation.health()['paid_off_primary_intake_active'])
        self.assertEqual(paused_before, self.snapshot(paused))
        self.f.fixture.provider.assert_not_called()
        self.f.fixture.effect.assert_not_called()
        self.parallel.verify_cancelled_terminal_binding(self.f.task)
        # Fresh process readiness must also verify the ledger, with no cached result.
        restarted = self.f.activation()
        restarted.reconcile_once()
        self.assertTrue(restarted.health()['reconciliation_worker_ready'])

    def test_matching_cancelled_and_owner_paused_commands_leave_all_rows_untouched(self):
        before = self.snapshot()
        self.assertEqual(409, self.send(self.signed()).status_code)
        self.assertEqual(before, self.snapshot())
        self.f.historical()
        before = self.snapshot()
        self.assertEqual(409, self.send(self.signed()).status_code)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], self.evidence())
        self.assertEqual(0, self.audit_count())

    def test_stale_foreign_and_expired_writers_refused(self):
        self.copied()
        before = self.snapshot()
        stale = self.signed(expected_fencing_generation=self.coordinator.get_task_writer(self.f.task).fencing_generation + 1)
        self.assertEqual(409, self.send(stale).status_code)
        with patch.object(self.coordinator, '_owner_token', 'foreign-process'):
            self.assertEqual(409, self.send(self.signed()).status_code)
        request = self.signed()
        self.f.fixture.expire(self.f.task)
        self.assertEqual(409, self.send(request).status_code)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], self.evidence())

    def test_signatures_owner_path_and_nonce_reuse_are_refused(self):
        self.copied()
        before = self.snapshot()
        request = self.signed()
        for invalid in (request.model_copy(update={'gateway_proof': None}),
                        request.model_copy(update={'actor_id': 'UOTHER'}),
                        self.signed(actor_id='UOTHER'),
                        self.signed(signing_path=f'/v1/primary/tasks/{self.f.task}/cancel'),
                        self.signed(issued_at=int(time.time()) - 400)):
            with self.subTest(case='invalid owner proof'):
                self.assertEqual(403, self.send(invalid).status_code)
        self.assertEqual(401, self.send(request, headers={}).status_code)
        self.assertEqual(403, self.send(request, path=self.path('different-task')).status_code)
        self.assertEqual(before, self.snapshot())
        # A nonce already claimed for this exact route cannot authorize a new command.
        nonce = verify_primary_ingress_proof('g' * 40, 'test-bearer', request,
            path=self.path(), proof_field='gateway_proof')
        with self.pool.connection() as c:
            c.execute(f"""INSERT INTO {self.schema}.director_primary_command_authorization
                (nonce_digest,task_id,operation,command_id,command_digest,proof_version,actor_id,
                 writer_fencing_generation,proof_expires_at)
                VALUES (%s,%s,'cancelled_plan_reconciliation','prior-command',%s,'v1','UOWNER',%s,now()+interval '5 minutes')""",
                (nonce,self.f.task,digest('prior-command'),request.expected_fencing_generation))
        before = self.snapshot()
        self.assertEqual(409, self.send(request).status_code)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], self.evidence())

    def test_changed_signed_bindings_and_rebound_command_are_refused(self):
        self.copied()
        before = self.snapshot()
        for field in ('expected_projection_digest', 'expected_projection_plan_digest',
                      'expected_batch_plan_digest', 'expected_diagnostic_digest'):
            with self.subTest(field=field):
                self.assertEqual(409, self.send(self.signed(**{field: digest('different')})).status_code)
                self.assertEqual(before, self.snapshot())
        request = self.signed()
        self.assertEqual(200, self.send(request).status_code)
        self.assertEqual(409, self.send(self.signed(nonce='s' * 32)).status_code)
        self.assertEqual(1, self.audit_count())

    def test_execution_evidence_causes_full_rollback_including_other_pending_rows(self):
        self.copied()
        with self.pool.connection() as c:
            c.execute(f'UPDATE {self.schema}.director_primary_parallel_assignment SET reservation_digest=%s '
                      "WHERE task_id=%s AND assignment_id='one'", (digest('reserved'), self.f.task))
        before = self.snapshot()
        self.assertEqual(409, self.send(self.signed()).status_code)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], self.evidence())
        self.assertEqual(0, self.audit_count())

    def test_real_task_budget_preserves_rows(self):
        self.copied()
        self.f.fixture.handle.claim_store.register_task_budget(self.f.task, budget_limit_units=4)
        with self.pool.connection() as c:
            c.execute(f'UPDATE {self.schema}.director_primary_budget SET budget_reserved_units=1 WHERE task_id=%s',
                      (self.f.task,))
        before = self.snapshot()
        self.assertEqual(409, self.send(self.signed()).status_code)
        self.assertEqual(before, self.snapshot())

    def test_claimed_graph_bookkeeping_can_be_adjudicated_without_rewriting_it(self):
        self.f.another_task(claimed=True)
        self.f.historical()
        self.update_projection(parallel_plan_digest=digest('mismatched'))
        before = self.coordinator.get_projection(self.f.task)
        self.assertEqual('CLAIMED', before.projection['provider_dispatch_status'])
        self.assertGreater(before.projection['budget_used_units'], 0)
        self.assertEqual(200, self.send(self.signed()).status_code)
        self.assertEqual(before, self.coordinator.get_projection(self.f.task))

    def test_audit_failure_rolls_back_cleanup_authorization_and_adjudication(self):
        self.copied()
        before = self.snapshot()
        with self.pool.connection() as c:
            c.execute(f"""CREATE FUNCTION {self.schema}.reject_adjudication() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'synthetic sensitive text'; END; $$""")
            c.execute(f'CREATE TRIGGER reject_adjudication BEFORE INSERT ON {self.schema}.director_primary_audit_event '
                      f'FOR EACH ROW EXECUTE FUNCTION {self.schema}.reject_adjudication()')
        response = self.send(self.signed())
        self.assertEqual(409, response.status_code)
        self.assertNotIn('synthetic sensitive text', response.text)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], self.evidence())

    def test_concurrent_replay_has_one_audit_row(self):
        self.copied()
        request = self.signed()
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: self.send(request), range(2)))
        self.assertEqual([200, 200], [response.status_code for response in responses])
        self.assertEqual([False, True], sorted(response.json()['applied'] for response in responses))
        self.assertEqual(1, self.audit_count())
        self.assertEqual(1, len(self.evidence()))

    def test_expiry_during_projection_lock_wait_refuses_without_mutation(self):
        self.copied()
        request = self.signed()
        before = self.snapshot()
        with self.pool.connection() as c:
            c.execute(f"UPDATE {self.schema}.director_primary_task_writer SET lease_expires_at=clock_timestamp()+interval '1 second' "
                      'WHERE task_id=%s', (self.f.task,))
        started = threading.Event()
        def send():
            started.set()
            return self.send(request)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.pool.connection() as c, c.transaction():
                c.execute(f'SELECT task_id FROM {self.schema}.director_primary_task_projection WHERE task_id=%s FOR UPDATE',
                          (self.f.task,)).fetchone()
                future = executor.submit(send)
                self.assertTrue(started.wait(2))
                time.sleep(1.2)
            self.assertEqual(409, future.result(timeout=10).status_code)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], self.evidence())

    def test_changed_ledger_invalidates_readiness_even_with_no_pending_rows(self):
        self.copied()
        self.assertEqual(200, self.send(self.signed()).status_code)
        activation = self.f.activation()
        activation.reconcile_once()
        self.assertTrue(activation.health()['reconciliation_worker_ready'])
        with self.pool.connection() as c:
            c.execute(f'UPDATE {self.schema}.director_primary_parallel_assignment SET allocation_profile=%s '
                      "WHERE task_id=%s AND assignment_id='one'", ('mixed', self.f.task))
        activation.reconcile_once()
        self.assertFalse(activation.health()['reconciliation_worker_ready'])
        self.assertFalse(activation.health()['paid_off_primary_intake_active'])

    def test_adjudication_is_append_only(self):
        self.copied()
        self.assertEqual(200, self.send(self.signed()).status_code)
        for operation in ('UPDATE', 'DELETE'):
            with self.subTest(operation=operation), self.assertRaisesRegex(Exception, 'append-only'):
                with self.pool.connection() as c:
                    sql = (f'UPDATE {self.schema}.director_primary_cancelled_plan_adjudication SET evidence=evidence'
                           if operation == 'UPDATE' else f'DELETE FROM {self.schema}.director_primary_cancelled_plan_adjudication')
                    c.execute(sql + ' WHERE task_id=%s', (self.f.task,))

    def test_diagnostic_compares_safe_field_names_without_historical_claims_or_raw_values(self):
        self.copied()
        plan = {'assignments': [dict(sequence=1, primary_provider_lane='private-provider-value',
            model_label='private-deployment-value', allocation_profile='mixed', retry_limit=3,
            depends_on=[], allow_draft_pr=False)]}
        self.update_projection(application={'task_record': {'plan': plan}})
        before = self.snapshot()
        path = self.path().replace('/reconcile', '/diagnostic')
        self.assertEqual(401, self.client.get(path).status_code)
        response = self.client.get(path, headers=self.f.fixture.headers)
        self.assertEqual(200, response.status_code)
        report = response.json()
        self.assertFalse(report['historical_digest_preimages_available'])
        self.assertEqual(['allocation_profile', 'deployment_id', 'provider_id'],
                         report['surviving_assignment_comparison'][0]['different_fields'])
        self.assertNotIn('private-provider-value', response.text)
        self.assertNotIn('private-deployment-value', response.text)
        for private_key in ('owner_token', 'SLACK', 'channel_id', 'actor_id', 'projection"', 'task_record'):
            self.assertNotIn(private_key, response.text)
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:58 UTC | END]]
