import copy
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from director_orchestrator.approvals import reset_for_owner_clarification
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.models import ProviderName
from director_orchestrator.packet_results import DirectorPacketResultConsumer
from director_orchestrator.runtime_activation import RuntimeActivation
from tests import test_engine_packet_runtime_routing as routing
from tests import test_engine_provider_spend_settlement as fixtures


class PacketResultPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_runtime_recovery_payload_is_cleared_with_its_task(self):
        for consume in (False, True):
            with self.subTest(consume=consume):
                fixture = routing.PacketRuntimeRoutingTests()
                fixture.setUp()
                for packet in (fixture.work, fixture.result):
                    packet['task_id'] = 'DIR-PACKET-ROUTING'
                    packet['assignment_id'] = 'DIR-PACKET-ROUTING-1'
                try:
                    record, assignment = fixture._record(agent=ProviderName.GEMINI)
                    key = f'{record.task_id}:{record.plan_generation}:1:' + 'a'*64
                    fixture.work['idempotency_key'] = key
                    fixture.result['idempotency_key'] = key
                    fixture.result['outcome'] = 'RUNTIME-PRIVATE-MARKER'
                    result = copy.deepcopy(fixture.result)
                    result['provider'] = 'gemini'
                    transport = routing.RecordingTransport(result)
                    router = routing.StubPacketTaskRouter(fixture.work, PacketBoundaryAdapter('gemini', transport))
                    settings = fixture._settings(langgraph_runtime_enabled=True,
                        langgraph_runtime_live_dispatch_enabled=True, langgraph_runtime_task_writer_enabled=True,
                        gemini_daily_budget_usd=1.0)
                    consumer = DirectorPacketResultConsumer() if consume else routing.RecordingResultConsumer()
                    activation = RuntimeActivation(settings, packet_task_router=router, packet_result_consumer=consumer)
                    try:
                        engine, store = fixture._engine(settings, activation)
                        record, assignment = fixture._record(agent=ProviderName.GEMINI)
                        store.create(record)
                        await engine._route_live_dispatch(record, assignment)
                        self.assertEqual(transport.sends, 1, (store.get(record.task_id).error, store.get(record.task_id).message))
                        key = fixture.work['idempotency_key']
                        with closing(sqlite3.connect(settings.langgraph_runtime_ledger_path)) as connection:
                            raw, digest = connection.execute('SELECT result_packet_json, result_packet_digest FROM runtime_dispatch_ledger WHERE idempotency_key=?', (key,)).fetchone()
                        self.assertIsNone(raw)
                        self.assertIsNotNone(digest)
                        retained = store.read_runtime_result(key)
                        self.assertEqual(retained is None, consume)
                        if not consume:
                            self.assertEqual(json.loads(retained)['outcome'], result['outcome'])
                            # Simulate the previous runtime ledger layout, then reopen activation.
                            with closing(sqlite3.connect(settings.langgraph_runtime_ledger_path)) as connection:
                                connection.execute('UPDATE runtime_dispatch_ledger SET result_packet_json=? WHERE idempotency_key=?', (retained, key))
                                connection.commit()
                            with closing(sqlite3.connect(store.path)) as connection:
                                connection.execute('DELETE FROM runtime_result_payloads WHERE idempotency_key=?', (key,))
                                connection.commit()
                            activation.close()
                            activation = RuntimeActivation(settings, packet_task_router=router, packet_result_consumer=consumer)
                            self.assertEqual(store.read_runtime_result(key), retained)
                            self.assertNotIn(b'RUNTIME-PRIVATE-MARKER', Path(settings.langgraph_runtime_ledger_path).read_bytes())
                        current = store.get(record.task_id)
                        reset_for_owner_clarification(current)
                        store.save(current)
                        with closing(sqlite3.connect(settings.langgraph_runtime_ledger_path)) as connection:
                            connection.execute('UPDATE runtime_dispatch_ledger SET result_packet_json=? WHERE idempotency_key=?',
                                               (json.dumps(result), key))
                            connection.commit()
                        activation.close()
                        activation = RuntimeActivation(settings, packet_task_router=router, packet_result_consumer=consumer)
                        self.assertNotIn(b'RUNTIME-PRIVATE-MARKER', Path(settings.langgraph_runtime_ledger_path).read_bytes())
                        self.assertIsNone(store.read_runtime_result(key))
                        self.assertFalse(store.privacy_cleanup_pending())
                        with self.assertRaises(ValueError):
                            store.save_runtime_result(fixture.work, json.dumps(result))
                        with self.assertRaises(ValueError):
                            store.reserve_runtime_result(fixture.work)
                        self.assertEqual(transport.sends, 1, (store.get(record.task_id).error, store.get(record.task_id).message))
                    finally:
                        activation.close()
                finally:
                    fixture.directory.cleanup()

    def test_reset_and_consumption_keep_tombstones_against_late_payload_writes(self):
        for consumed in (False, True):
            with self.subTest(consumed=consumed), tempfile.TemporaryDirectory() as root:
                _, store, record, _ = fixtures.ProviderSpendSettlementTests()._world(root)
                work = {'task_id': record.task_id, 'idempotency_key': 'packet-key',
                        'correlation': {'correlation_id': 'correlation'}}
                store.reserve_runtime_result(work)
                store.save_runtime_result(work, 'private-provider-marker')
                with closing(sqlite3.connect(store.path)) as reader:
                    reader.execute('PRAGMA journal_mode=WAL')
                if consumed:
                    record.consumed_packet_results['correlation'] = 'a'*64
                else:
                    reset_for_owner_clarification(record)
                store.save(record)
                self.assertIsNone(store.read_runtime_result('packet-key'))
                with self.assertRaises(ValueError):
                    store.save_runtime_result(work, 'late-private-provider-marker')
                with self.assertRaises(ValueError):
                    store.reserve_runtime_result(work)
                self.assertFalse(store.privacy_cleanup_pending())
                self.assertNotIn(b'private-provider-marker', Path(store.path).read_bytes())

    def test_pinned_wal_reader_keeps_privacy_gate_closed_until_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            _, store, record, _ = fixtures.ProviderSpendSettlementTests()._world(root)
            with closing(sqlite3.connect(store.path)) as connection:
                connection.execute('PRAGMA journal_mode=WAL')
            work = {'task_id': record.task_id, 'idempotency_key': 'pinned-key',
                    'correlation': {'correlation_id': 'pinned-correlation'}}
            store.reserve_runtime_result(work)
            store.save_runtime_result(work, 'PINNED-PRIVATE-RESULT')
            with closing(sqlite3.connect(store.path)) as reader:
                reader.execute('BEGIN')
                reader.execute('SELECT payload_json FROM runtime_result_payloads').fetchall()
                reset_for_owner_clarification(record)
                store.save(record)
                self.assertIsNone(store.read_runtime_result('pinned-key'))
                self.assertTrue(store.privacy_cleanup_pending())
            self.assertFalse(store.privacy_cleanup_pending())
            self.assertNotIn(b'PINNED-PRIVATE-RESULT', Path(store.path).read_bytes())
            wal = Path(str(store.path) + '-wal')
            if wal.exists():
                self.assertNotIn(b'PINNED-PRIVATE-RESULT', wal.read_bytes())


    async def test_restart_recovers_split_commit_and_legacy_layouts(self):
        from director_orchestrator.langgraph_runtime import RuntimeExecutionMode, RuntimeExecutionStatus, RuntimeContractError
        for scenario in ('split_commit', 'legacy_pending', 'legacy_without_work', 'large_backlog', 'lost_guard'):
            with self.subTest(scenario=scenario):
                f = routing.PacketRuntimeRoutingTests()
                f.setUp()
                record, assignment = f._record(agent=ProviderName.GEMINI)
                for packet in (f.work, f.result):
                    packet['task_id'] = record.task_id
                    packet['assignment_id'] = record.task_id + '-1'
                    packet['idempotency_key'] = f'{record.task_id}:{record.plan_generation}:1:' + 'a'*64
                f.result['provider'] = 'gemini'
                transport = routing.RecordingTransport(f.result)
                adapter = PacketBoundaryAdapter('gemini', transport)
                router = routing.StubPacketTaskRouter(f.work, adapter)
                settings = f._settings(langgraph_runtime_enabled=True,
                    langgraph_runtime_live_dispatch_enabled=True, langgraph_runtime_task_writer_enabled=True)
                activation = RuntimeActivation(settings, packet_task_router=router,
                    packet_result_consumer=routing.RecordingResultConsumer())
                try:
                    engine, store = f._engine(settings, activation)
                    store.create(record)
                    await engine._route_live_dispatch(record, assignment)
                    self.assertEqual(transport.sends, 1)
                    key = f.work['idempotency_key']
                    encoded = store.read_runtime_result(key)
                    self.assertIsNotNone(encoded)
                    with closing(sqlite3.connect(settings.langgraph_runtime_ledger_path)) as db:
                        if scenario == 'split_commit':
                            db.execute("UPDATE runtime_dispatch_ledger SET status='DISPATCHING', result_packet_digest=NULL")
                        elif scenario == 'legacy_pending':
                            db.execute("UPDATE runtime_dispatch_ledger SET status='CLAIMED', result_packet_digest=NULL")
                        elif scenario == 'legacy_without_work':
                            db.execute('UPDATE runtime_dispatch_ledger SET work_packet_json=NULL, result_packet_json=?', (encoded,))
                        elif scenario == 'large_backlog':
                            row = db.execute('SELECT * FROM runtime_dispatch_ledger').fetchone()
                            columns = [v[1] for v in db.execute('PRAGMA table_info(runtime_dispatch_ledger)')]
                            copies = []
                            for i in range(1001):
                                values = list(row)
                                values[columns.index('idempotency_key')] = 'retired-' + str(i)
                                values[columns.index('task_id')] = 'retired-task'
                                values[columns.index('result_packet_json')] = encoded
                                copies.append(values)
                            db.executemany('INSERT INTO runtime_dispatch_ledger VALUES (' + ','.join('?' for _ in columns) + ')', copies)
                        else:
                            db.execute("UPDATE runtime_dispatch_ledger SET status='REJECTED'")
                        db.commit()
                    if scenario in ('legacy_pending', 'legacy_without_work', 'lost_guard'):
                        with closing(sqlite3.connect(store.path)) as db:
                            db.execute('DELETE FROM runtime_result_payloads')
                            db.commit()
                    activation.close()
                    activation = RuntimeActivation(settings, packet_task_router=router,
                        packet_result_consumer=routing.RecordingResultConsumer())
                    runtime = activation.runtime
                    self.assertIsNotNone(runtime)
                    if scenario == 'legacy_pending':
                        runtime.open_reconciliation(f.work, provider='gemini', writer_token=router.token)
                        outcome = runtime.reconcile_result(f.work, f.result, provider='gemini', writer_token=router.token)
                        self.assertEqual(outcome.status, RuntimeExecutionStatus.COMPLETED)
                    elif scenario == 'lost_guard':
                        with self.assertRaises(RuntimeContractError):
                            runtime._persist_result(f.work, f.result, provider='gemini', writer_token=router.token)
                        self.assertIsNone(store.read_runtime_result(key))
                    else:
                        outcome = runtime.run_packet(f.work, provider='gemini', execution_mode=RuntimeExecutionMode.LIVE_SAFE,
                            adapter=adapter, writer_token=router.token)
                        self.assertEqual(outcome.status, RuntimeExecutionStatus.REPLAYED)
                        self.assertEqual(outcome.result_packet, f.result)
                    with closing(sqlite3.connect(settings.langgraph_runtime_ledger_path)) as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM runtime_dispatch_ledger WHERE result_packet_json IS NOT NULL').fetchone()[0], 0)
                    self.assertEqual(transport.sends, 1)
                finally:
                    activation.close()
                    f.directory.cleanup()

    def test_persistent_journal_is_scrubbed_and_lifecycle_index_is_used(self):
        from unittest.mock import patch
        connect = sqlite3.connect
        def persistent(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.execute('PRAGMA journal_mode=PERSIST')
            return db
        with tempfile.TemporaryDirectory() as root, patch('sqlite3.connect', side_effect=persistent):
            _, store, record, _ = fixtures.ProviderSpendSettlementTests()._world(root)
            work = {'task_id': record.task_id, 'idempotency_key': 'persistent-key',
                    'correlation': {'correlation_id': 'persistent-correlation'}}
            store.reserve_runtime_result(work)
            store.save_runtime_result(work, 'PERSIST-PRIVATE-RESULT')
            reset_for_owner_clarification(record)
            store.save(record)
            self.assertFalse(store.privacy_cleanup_pending())
            for suffix in ('', '-journal'):
                path = Path(str(store.path) + suffix)
                if path.exists():
                    self.assertNotIn(b'PERSIST-PRIVATE-RESULT', path.read_bytes())
            with closing(connect(store.path)) as db:
                plan = db.execute('EXPLAIN QUERY PLAN UPDATE runtime_result_payloads SET payload_json=NULL, closed=1 WHERE task_id=? AND closed=0 AND correlation_id=?',
                                  (record.task_id, 'persistent-correlation')).fetchall()
                self.assertIn('runtime_result_lifecycle', str(plan))
