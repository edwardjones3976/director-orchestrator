# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | BEGIN]]
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime, timezone
from director_orchestrator.primary_paid_preflight import check_budget_setup
from director_orchestrator.primary_parallel import PaidSetupHold
from tests.test_antigravity_team import grant, budget, fixtures

class BudgetPreflightUnitTests(unittest.TestCase):
    def prepared(self):
        b = budget(grant())
        authority = SimpleNamespace(schema='synthetic', entitlements=SimpleNamespace(
            _owner_authority_secret=fixtures.OWNER_SECRET,
            _clock=lambda: datetime.now(timezone.utc)))
        row = dict(authority_digest=b.grant_digest, document=b.model_dump(mode='json'))
        connection = Mock()
        connection.execute.side_effect = [Mock(fetchall=lambda:[row]), Mock(fetchone=lambda:None),
            Mock(fetchone=lambda:None), Mock(fetchone=lambda:dict(task_spend=0, daily_spend=0))]
        args = dict(connection=connection, routing=SimpleNamespace(authority=authority),
            settings=SimpleNamespace(max_task_budget_usd=0.0001,max_daily_provider_budget_usd=0.0001),
            provider=b.provider_id, packet=dict(task_id=b.task_id,project=b.project_id), attempt_usd=0.00006)
        return b, row, args

    def test_signed_funded_readonly_path(self):
        b, row, args = self.prepared()
        self.assertEqual(check_budget_setup(**args), b)
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT')
                            for call in args['connection'].execute.call_args_list))

    def test_combined_wave_exhaustion_is_refused_before_reservation(self):
        wave = {}
        b, row, args = self.prepared()
        check_budget_setup(**args, wave_budget_usage=wave)
        b, row, args = self.prepared()
        with self.assertRaises(PaidSetupHold):
            check_budget_setup(**args, wave_budget_usage=wave)

    def test_missing_and_mismatched_funding_hold(self):
        b, row, args = self.prepared()
        args['connection'].execute.side_effect = [Mock(fetchall=lambda:[])]
        with self.assertRaises(PaidSetupHold): check_budget_setup(**args)
        b, row, args = self.prepared()
        row['authority_digest'] = '0'*64
        with self.assertRaises(PaidSetupHold): check_budget_setup(**args)

class PaidWavePreflightPostgresTests(unittest.TestCase):
    def setUp(self):
        from tests.test_primary_coordinator import PrimaryTaskCoordinatorIntegrationTests
        self.fixture = PrimaryTaskCoordinatorIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_entire_selected_wave_stays_pending_then_retries_without_attempts(self):
        from director_orchestrator.primary_parallel import (PostgresParallelDispatch,
            ParallelAssignment, ParallelDispatchPolicy)
        store = self.fixture.handle.claim_store
        writer = store.acquire_task_writer('synthetic-wave',engine_owner='langgraph',
            owner_token='synthetic',expected_generation=0)
        ledger = PostgresParallelDispatch(store)
        policy = ParallelDispatchPolicy(max_concurrency=2)
        ledger.register_plan(task_id='synthetic-wave',writer=writer,policy=policy,
            plan_digest='sha256:'+'a'*64,assignments=(
                ParallelAssignment('a',1,'claude','claude'),
                ParallelAssignment('b',2,'openai','openai')))
        seen=[]
        def preflight(candidate, **kwargs):
            seen.append(candidate.assignment_id)
            if candidate.assignment_id=='b': raise PaidSetupHold()
        result=ledger.reserve_ready(task_id='synthetic-wave',writer=writer,
            policy=policy,preflight=preflight)
        self.assertEqual(result, ())
        self.assertEqual(seen,['a','b'])
        state=ledger.aggregate('synthetic-wave')
        self.assertEqual(set(state.statuses.values()),{'PENDING'})
        self.assertEqual(state.setup_hold_reasons,('paid_setup_required',))
        self.assertFalse(state.terminal)
        with self.fixture.handle.pool.connection() as c:
            for table in ('director_primary_provider_attempt','director_primary_provider_spend'):
                self.assertEqual(c.execute(f'SELECT count(*) AS n FROM {self.fixture.schema}.{table}').fetchone()['n'],0)
        result=ledger.reserve_ready(task_id='synthetic-wave',writer=writer,
            policy=policy,preflight=lambda *args,**kwargs:None)
        self.assertEqual(len(result),2)
        self.assertEqual(ledger.aggregate('synthetic-wave').setup_hold_reasons,())
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | END]]
