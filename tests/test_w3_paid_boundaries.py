import hashlib
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace

from director_orchestrator.approvals import reserve_provider_effect, record_provider_effect_observation
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import CommunicationEvidenceFailure, ProviderTransport
from director_orchestrator.models import Assignment, ProviderName, ProviderRun
from director_orchestrator.packet_contracts import canonical_json_bytes
from director_orchestrator.packet_transports import LegacyProviderPacketTransport
from tests import test_engine_provider_spend_settlement as fixtures
from tests import test_review_handoff_effect_accounting as reviews


class PaidBoundaryTests(unittest.TestCase):
    def test_raw_transport_cannot_change_the_engine_route(self):
        for cli in (False, True):
            with self.subTest(cli=cli), tempfile.TemporaryDirectory() as root:
                assignment = Assignment(sequence=1, agent=ProviderName.GROK, task='Bounded work',
                                        acceptance=['Return evidence'], allow_draft_pr=cli,
                                        repository='example/demo' if cli else None)
                settings, store, record, assignment = fixtures.ProviderSpendSettlementTests()._world(
                    root, assignment=assignment)
                engine = OrchestrationEngine(settings, store)
                identity = engine._begin_provider_interaction(record, assignment, phase='dispatch',
                                                               attempt=1, effect_key='a'*64)
                expected = ProviderTransport.GROK_CLI if cli else ProviderTransport.XAI_RESPONSES_API
                run = ProviderRun(provider=ProviderName.GROK, assignment_sequence=1, run_id='native',
                                  interaction_id=identity, status='completed',
                                  raw={'transport': 'xai_responses_api' if cli else 'grok_cli'})
                self.assertEqual(engine._run_transport(record, run), expected)
                run.interaction_id = 'unbound'
                with self.assertRaises(CommunicationEvidenceFailure):
                    engine._run_transport(record, run)

    def test_only_proven_unsubmitted_effect_releases_cost(self):
        for outcome in ('failed_pre_submit', 'ambiguous', 'submitted'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as root:
                settings, store, record, assignment = fixtures.ProviderSpendSettlementTests()._world(root)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment, phase='dispatch', max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                record_provider_effect_observation(record, assignment, effect_key=key,
                                                   phase='dispatch', outcome=outcome)
                store.save(record)
                self.assertEqual(engine._settle_unsubmitted_spend(record, assignment, key),
                                 outcome == 'failed_pre_submit')
                self.assertEqual(engine.provider_spend.exposure(assignment.agent)[1],
                                 0 if outcome == 'failed_pre_submit' else 1)

    def test_exact_review_native_metering_uses_the_effective_assignment(self):
        with tempfile.TemporaryDirectory() as root:
            settings, store, _, _ = fixtures.ProviderSpendSettlementTests()._world(root)
            settings = replace(settings, oracle_automerge_cursor_actors=('cursor-agent',),
                               oracle_automerge_claude_actors=('claude-agent',),
                               oracle_automerge_grok_actors=('grok-agent',))
            record, planned = reviews.ReviewHandoffEffectAccountingTests().fixture()
            planned.agent = ProviderName.GROK
            record.next_assignment_index = 1
            record.owner_approved = True
            store.create(record)
            engine = OrchestrationEngine(settings, store)
            effective = engine._effective_assignment(record, planned)
            key = reserve_provider_effect(record, effective, phase='dispatch', max_task_budget_usd=10)
            store.save(record)
            engine.provider_spend.reserve(provider=planned.agent, effect_key=key,
                                          estimated_cost_usd=0.25, request_limit=50, budget_usd=1)
            digest = hashlib.sha256(canonical_json_bytes(effective.model_dump(mode='json'))).hexdigest()
            packet = {'task_id': record.task_id, 'assignment_id': f'{record.task_id}-2',
                      'starting_ref': effective.starting_ref,
                      'idempotency_key': f'{record.task_id}:{record.plan_generation}:2:{digest}'}
            transport = LegacyProviderPacketTransport(SimpleNamespace(settings=settings, name=planned.agent),
                                                       task_store=store, poll_seconds=1)
            run = ProviderRun(provider=planned.agent, assignment_sequence=2, run_id='metered-review',
                              status='completed', raw={'usage': {'cost': 0.6}})
            transport._settle_native_metering(packet, run)
            transport._settle_native_metering(packet, run)
            self.assertEqual(engine.provider_spend.exposure(planned.agent), (1, 0.6))
            packet['idempotency_key'] = packet['idempotency_key'][:-64] + '0'*64
            with self.assertRaises(ValueError):
                transport._settle_native_metering(packet, run)

class PaidBoundaryAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepublication_stop_settles_daily_reservation(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as root:
            settings, store, record, assignment = fixtures.ProviderSpendSettlementTests()._world(root)
            engine = OrchestrationEngine(settings, store)
            engine._publish_delegation = AsyncMock(return_value=False)
            adapter = AsyncMock()
            engine.providers[assignment.agent] = adapter
            self.assertFalse(await engine._execute_assignment(record, assignment))
            adapter.dispatch.assert_not_awaited()
            self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 0))

    async def test_spend_hold_is_reported_even_when_run_returns_early(self):
        from unittest.mock import AsyncMock
        from director_orchestrator.models import TaskStatus
        with tempfile.TemporaryDirectory() as root:
            settings, store, record, _ = fixtures.ProviderSpendSettlementTests()._world(root)
            record.status = TaskStatus.OWNER_DECISION_REQUIRED
            record.error = 'spend_settlement_failed:' + 'a'*64
            record.message = 'Sensitive provider response must not be posted'
            store.save(record)
            engine = OrchestrationEngine(settings, store)
            engine._run_locked = AsyncMock()
            engine._status_post = AsyncMock()
            await engine.run(record.task_id)
            call = engine._status_post.call_args
            self.assertIn('reconciliation is pending', call.args[1])
            self.assertNotIn(record.message, call.args[1])
            first_id = call.kwargs['client_msg_id']
            await engine.run(record.task_id)
            self.assertEqual(first_id, engine._status_post.call_args.kwargs['client_msg_id'])

    def test_not_created_restores_only_the_exact_unused_revision_authority(self):
        from tests import test_engine_resume as recovery
        from director_orchestrator.models import TaskStatus, ProviderContactAttempt
        with tempfile.TemporaryDirectory() as root:
            _, store, engine, record = recovery.EngineRecoveryTests()._v1_revision_fixture(
                root, recovery.NoRedispatchAdapter(), recovery.AcceptingDirector())
            assignment = record.plan.assignments[0]
            record.status = TaskStatus.REVIEWING
            store.save(record)
            record, key = engine._reserve_effect_intent(record, assignment, phase='revision',
                retry_basis_digest='e'*64, revision_prompt_digest='f'*64)
            record.provider_contact_attempts.extend([
                ProviderContactAttempt(assignment_sequence=assignment.sequence, provider=assignment.agent,
                    ordinal=1, phase='revision', outcome='ambiguous_dispatch', error='unconfirmed'),
                ProviderContactAttempt(assignment_sequence=assignment.sequence, provider=assignment.agent,
                    ordinal=1, phase='reconciliation', outcome='reconciled_not_created', error='owner verified'),
            ])
            record.retry_basis_digests.append('a'*64)
            record.revision_prompt_digests.append('b'*64)
            store.save(record)
            self.assertTrue(engine._settle_not_created_dispatches(record))
            self.assertEqual(record.retry_basis_digests, ['a'*64])
            self.assertEqual(record.revision_prompt_digests, ['b'*64])
            self.assertEqual(record.revision_count, 0)
            # Replaying an old absence receipt must not free a newer use of that same authority.
            record.retry_basis_digests.append('e'*64)
            record.revision_prompt_digests.append('f'*64)
            store.save(record)
            self.assertTrue(engine._settle_not_created_dispatches(record))
            self.assertIn('e'*64, record.retry_basis_digests)
            self.assertIn('f'*64, record.revision_prompt_digests)

    def test_raw_packet_marker_cannot_skip_result_consumption(self):
        with tempfile.TemporaryDirectory() as root:
            settings, store, record, assignment = fixtures.ProviderSpendSettlementTests()._world(root)
            engine = OrchestrationEngine(settings, store)
            run = ProviderRun(provider=assignment.agent, assignment_sequence=1, run_id='fake',
                              status='completed', interaction_id='correlation',
                              raw={'packet_result_digest': 'a'*64})
            self.assertFalse(engine._packet_result_consumed(record, run))
            record.consumed_packet_results['correlation'] = 'b'*64
            self.assertTrue(engine._packet_result_consumed(record, run))
            from director_orchestrator.approvals import reset_for_owner_clarification
            record.revision_authority_by_effect = {str(i): ('a'*64, 'b'*64) for i in range(20)}
            reset_for_owner_clarification(record)
            self.assertEqual(record.revision_authority_by_effect, {})
            store.save(record)
            self.assertEqual(store.get(record.task_id).revision_authority_by_effect, {})
            self.assertFalse(engine._packet_result_consumed(record, run))


    async def test_zero_charge_storage_failure_is_retried_without_a_provider_run(self):
        import sqlite3
        from unittest.mock import patch
        from director_orchestrator.models import TaskStatus
        with tempfile.TemporaryDirectory() as root:
            settings, store, record, assignment = fixtures.ProviderSpendSettlementTests()._world(root)
            engine = OrchestrationEngine(settings, store)
            key = reserve_provider_effect(record, assignment, phase='dispatch', max_task_budget_usd=10)
            engine._reserve_provider_daily_spend(assignment, effect_key=key)
            record_provider_effect_observation(record, assignment, effect_key=key,
                                               phase='dispatch', outcome='failed_pre_submit')
            store.save(record)
            with patch.object(engine.provider_spend, 'record_charge', side_effect=sqlite3.OperationalError('locked')):
                self.assertFalse(engine._settle_unsubmitted_spend(record, assignment, key))
            saved = store.get(record.task_id)
            self.assertEqual(saved.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(saved.error, 'spend_settlement_failed:' + key)
            restarted = OrchestrationEngine(settings, store)
            self.assertTrue(restarted._retry_spend_settlements(saved))
            self.assertIsNone(store.get(record.task_id).error)
            self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, 0))
            self.assertEqual(saved.provider_runs, [])
            self.assertEqual(saved.provider_contact_attempts, [])

    async def test_zero_charge_recovery_without_a_durable_hold_marker(self):
        import sqlite3
        from itertools import product
        from unittest.mock import AsyncMock, patch
        from director_orchestrator.models import TaskStatus
        for outcome, entry in product(('failed_pre_submit', 'ambiguous'), ('helper', 'run')):
            with self.subTest(outcome=outcome, entry=entry), tempfile.TemporaryDirectory() as root:
                settings, store, record, assignment = fixtures.ProviderSpendSettlementTests()._world(root)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment, phase='dispatch', max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                record_provider_effect_observation(record, assignment, effect_key=key,
                                                   phase='dispatch', outcome=outcome)
                record.status = TaskStatus.DONE
                store.save(record)
                with patch.object(engine.provider_spend, 'record_charge', side_effect=sqlite3.OperationalError('locked')), \
                        patch.object(store, 'save', side_effect=sqlite3.OperationalError('locked')):
                    self.assertFalse(engine._settle_unsubmitted_spend(record, assignment, key))
                saved = store.get(record.task_id)
                self.assertIsNone(saved.error)
                self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 1.0))
                restarted = OrchestrationEngine(settings, store)
                adapter = AsyncMock()
                restarted.providers[assignment.agent] = adapter
                if entry == 'run':
                    await restarted.run(record.task_id)
                else:
                    self.assertTrue(restarted._retry_spend_settlements(saved))
                self.assertTrue(restarted._retry_spend_settlements(store.get(record.task_id)))
                expected = 0 if outcome == 'failed_pre_submit' else 1.0
                self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, expected))
                adapter.dispatch.assert_not_awaited()
                self.assertEqual(saved.provider_runs, [])
                self.assertEqual(saved.provider_contact_attempts, [])

    async def test_public_resume_preserves_storage_failure_recovery(self):
        import sqlite3
        from contextlib import ExitStack
        from unittest.mock import AsyncMock, patch
        from director_orchestrator.models import TaskStatus
        for failure in ('initial_read', 'discovery', 'closure_read', 'charge_and_hold', 'refresh_read', 'clear_hold'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                settings, store, record, assignment = fixtures.ProviderSpendSettlementTests()._world(root)
                engine = OrchestrationEngine(settings, store)
                key = reserve_provider_effect(record, assignment, phase='dispatch', max_task_budget_usd=10)
                engine._reserve_provider_daily_spend(assignment, effect_key=key)
                record_provider_effect_observation(record, assignment, effect_key=key,
                                                   phase='dispatch', outcome='failed_pre_submit')
                record.status = TaskStatus.DONE
                if failure == 'clear_hold':
                    record.error = 'spend_settlement_failed:' + key
                store.save(record)
                restarted = OrchestrationEngine(settings, store)
                adapter = AsyncMock()
                restarted.providers[assignment.agent] = adapter
                restarted._status_post = AsyncMock()
                original_get = store.get
                reads = 0
                def fail_read(task_id):
                    nonlocal reads
                    reads += 1
                    target = {'initial_read': 1, 'closure_read': 2, 'refresh_read': 3}.get(failure)
                    if reads == target:
                        raise sqlite3.OperationalError('locked')
                    return original_get(task_id)
                for _ in range(2):
                    reads = 0
                    with ExitStack() as patches:
                        patches.enter_context(patch.object(store, 'get', side_effect=fail_read))
                        if failure == 'discovery':
                            patches.enter_context(patch.object(restarted.provider_spend, 'unreconciled',
                                side_effect=sqlite3.OperationalError('locked')))
                        if failure == 'charge_and_hold':
                            patches.enter_context(patch.object(restarted.provider_spend, 'record_charge',
                                side_effect=sqlite3.OperationalError('locked')))
                        if failure in ('charge_and_hold', 'clear_hold'):
                            patches.enter_context(patch.object(store, 'save', side_effect=OSError('unavailable')))
                        await restarted.run(record.task_id)
                    self.assertEqual(store.get(record.task_id).status, TaskStatus.DONE)
                    expected = 0 if failure in ('refresh_read', 'clear_hold') else 1.0
                    self.assertEqual(restarted.provider_spend.exposure(assignment.agent), (1, expected))
                recovered = OrchestrationEngine(settings, store)
                self.assertTrue(recovered._retry_spend_settlements(store.get(record.task_id)))
                self.assertEqual(recovered.provider_spend.exposure(assignment.agent), (1, 0))
                self.assertIsNone(store.get(record.task_id).error)
                adapter.dispatch.assert_not_awaited()
                self.assertEqual(store.get(record.task_id).provider_runs, [])
                self.assertEqual(store.get(record.task_id).provider_contact_attempts, [])

    async def test_packet_pre_submit_exits_and_missing_replay_evidence(self):
        from unittest.mock import AsyncMock, patch
        from tests import test_engine_packet_runtime_routing as routing
        from director_orchestrator.models import TaskStatus
        for parallel in (False, True):
            for scenario in ('publication', 'revalidation', 'missing_packet', 'workload_failure'):
                with self.subTest(parallel=parallel, scenario=scenario):
                    f = routing.PacketRuntimeRoutingTests()
                    f.setUp()
                    try:
                        settings = f._settings(langgraph_runtime_enabled=True,
                            langgraph_runtime_live_dispatch_enabled=True,
                            langgraph_runtime_task_writer_enabled=True,
                            langgraph_parallel_external_dispatch_enabled=parallel,
                            langgraph_parallel_kill_switch=False)
                        transport = routing.RecordingTransport(f.result)
                        router = routing.StubPacketTaskRouter(f.work, routing.PacketBoundaryAdapter('gemini', transport))
                        activation = routing.RuntimeActivation(settings, packet_task_router=router,
                            packet_result_consumer=routing.RecordingResultConsumer())
                        f.addCleanup(activation.close)
                        engine, store = f._engine(settings, activation)
                        record, assignment = f._record(agent=ProviderName.GEMINI, workload_policy_version='v1' if scenario == 'workload_failure' else None)
                        if scenario == 'missing_packet':
                            record.provider_runs.append(ProviderRun(provider=assignment.agent,
                                assignment_sequence=1, run_id='existing', status='completed'))
                            engine._spend_settlements = lambda *args: {'held': {'result_packet_digest': 'a'*64}}
                        store.create(record)
                        engine._publish_delegation = AsyncMock(return_value=scenario not in ('publication', 'workload_failure'))
                        if scenario == 'workload_failure':
                            engine._reserve_workload_dispatch = AsyncMock(return_value=SimpleNamespace())
                            async def closed_but_unfinalized(current, assigned, reservation, **kwargs):
                                record_provider_effect_observation(current, assigned,
                                    effect_key=kwargs['effect_key'], phase='dispatch', outcome='failed_pre_submit')
                                store.save(current)
                                return False
                            engine._finalize_workload_pre_submit_stop = closed_but_unfinalized
                        if scenario == 'revalidation':
                            engine._revalidated_plan_after_publication = lambda *args: None
                        with patch('director_orchestrator.engine.assignment_request_cost_bound', return_value=0.1):
                            if parallel:
                                await engine._dispatch_parallel_packet_batch(record)
                            else:
                                await engine._route_live_dispatch(record, assignment)
                        self.assertEqual(transport.sends, 0)
                        saved = store.get(record.task_id)
                        if scenario == 'missing_packet':
                            self.assertEqual(saved.status, TaskStatus.OWNER_DECISION_REQUIRED)
                            self.assertIn('runtime packet', saved.message + (saved.error or ''))
                        elif parallel and scenario == 'revalidation':
                            # The claimed parallel member cannot prove pre-submit closure.
                            self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 0.1))
                        else:
                            self.assertTrue(any(e.outcome == 'failed_pre_submit' for e in saved.task_effect_ledger))
                            self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 0))
                    finally:
                        activation.close()
                        f.directory.cleanup()


    async def test_packet_exception_closure_survives_workload_finalization_failure(self):
        from unittest.mock import AsyncMock, patch
        from tests import test_engine_packet_runtime_routing as routing
        for closed in (False, True):
            with self.subTest(closed=closed):
                f = routing.PacketRuntimeRoutingTests()
                f.setUp()
                settings = f._settings(langgraph_runtime_enabled=True,
                    langgraph_runtime_live_dispatch_enabled=True, langgraph_runtime_task_writer_enabled=True)
                transport = routing.RecordingTransport(f.result)
                router = routing.StubPacketTaskRouter(f.work, routing.PacketBoundaryAdapter('gemini', transport))
                activation = routing.RuntimeActivation(settings, packet_task_router=router)
                try:
                    engine, store = f._engine(settings, activation)
                    record, assignment = f._record(agent=ProviderName.GEMINI, workload_policy_version='v1')
                    store.create(record)
                    engine._reserve_workload_dispatch = AsyncMock(return_value=SimpleNamespace())
                    engine._finalize_workload_dispatch = AsyncMock(return_value=False)
                    def close(current, assigned, **kwargs):
                        if closed:
                            record_provider_effect_observation(current, assigned, effect_key=kwargs['effect_key'],
                                                               phase='dispatch', outcome='failed_pre_submit')
                            store.save(current)
                        return closed
                    engine._close_failed_pre_submit_after_publication = close
                    with patch.object(activation.runtime, 'run_packet', side_effect=ValueError('runtime refused')), patch(
                        'director_orchestrator.engine.assignment_request_cost_bound', return_value=0.1):
                        self.assertFalse(await engine._route_live_dispatch(record, assignment))
                    saved = store.get(record.task_id)
                    outcomes = {e.outcome for e in saved.task_effect_ledger if e.kind == 'provider_observation'}
                    self.assertEqual(outcomes, {'failed_pre_submit'} if closed else {'ambiguous'})
                    self.assertEqual(engine.provider_spend.exposure(assignment.agent), (1, 0 if closed else 0.1))
                    self.assertEqual(transport.sends, 0)
                    engine._finalize_workload_dispatch.assert_awaited_once()
                finally:
                    activation.close()
                    f.directory.cleanup()
