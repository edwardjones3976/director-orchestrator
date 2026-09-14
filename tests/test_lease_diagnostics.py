# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 16:59 UTC | BEGIN]]
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from director_orchestrator.lease_diagnostics import LeaseRenewalDiagnostics
from director_orchestrator.primary_default import DefaultPrimaryActivation

class LeaseDiagnosticsTests(unittest.TestCase):
    def activation(self):
        value = DefaultPrimaryActivation.__new__(DefaultPrimaryActivation)
        value.application = object()
        value.coordinator = Mock()
        value.settings = SimpleNamespace(langgraph_primary_new_intake_enabled=True)
        value._lease_diagnostics = LeaseRenewalDiagnostics()
        return value

    def test_no_observation_is_null_and_failure_does_not_create_success(self):
        value = self.activation()
        self.assertIsNone(value._lease_diagnostics.health()['intake_lease_last_renewal_at'])
        value.coordinator.renew_new_intake.side_effect = RuntimeError('secret DSN token')
        with self.assertLogs('director_orchestrator.lease_diagnostics', level='WARNING') as logs:
            with self.assertRaises(RuntimeError): value.renew()
        self.assertNotIn('secret', ''.join(logs.output))
        self.assertIsNone(value._lease_diagnostics.last_success_at)
        self.assertEqual(value._lease_diagnostics.failure_category, 'intake_renewal_failed')
        value.coordinator.adopt_or_activate_new_intake.assert_not_called()

    def test_success_failure_recovery_retains_last_success(self):
        value = self.activation()
        value.renew()
        first = value._lease_diagnostics.last_success_at
        self.assertIsNotNone(first)
        value.coordinator.renew_new_intake.side_effect = RuntimeError('stale fencing')
        with self.assertRaises(RuntimeError): value.renew()
        self.assertEqual(value._lease_diagnostics.last_success_at, first)
        value.coordinator.renew_new_intake.side_effect = None
        value.renew()
        self.assertIsNone(value._lease_diagnostics.failure_category)

    def test_task_failure_is_distinct_and_does_not_attempt_intake(self):
        value = self.activation()
        value.coordinator.heartbeat_active_task_writers.side_effect = RuntimeError('database')
        with self.assertRaises(RuntimeError): value.renew()
        value.coordinator.renew_new_intake.assert_not_called()
        self.assertEqual(value._lease_diagnostics.failure_category, 'task_writer_renewal_failed')

    def test_rate_limited_closed_vocabulary(self):
        diag = LeaseRenewalDiagnostics()
        with patch('director_orchestrator.lease_diagnostics.time.monotonic', side_effect=[0, 10, 59, 60]), patch('director_orchestrator.lease_diagnostics._LOG') as log:
            for _ in range(4): diag.failed('credential secret')
            self.assertEqual(log.warning.call_count, 2)
            self.assertNotIn('credential', str(log.mock_calls))
        self.assertEqual(diag.failure_category, 'renewal_failed')

    def test_actual_coordinator_caught_failure_then_disabled_intake_recovery(self):
        from director_orchestrator.primary_coordinator import PrimaryTaskCoordinator
        from director_orchestrator.langgraph_postgres import PostgresControlPlaneError
        coordinator = PrimaryTaskCoordinator.__new__(PrimaryTaskCoordinator)
        coordinator._store = Mock()
        coordinator._owner_token = 'local-test-owner'
        coordinator._active_task_ids = {'paused-task'}
        coordinator._store.current_task_writer.return_value = SimpleNamespace(
            lease_live=True, owner_token='local-test-owner', fencing_generation=1)
        coordinator._store.renew_task_writer.side_effect = PostgresControlPlaneError('secret')
        value = self.activation()
        value.coordinator = coordinator
        value.settings.langgraph_primary_new_intake_enabled = False
        value.renew()  # Actual coordinator catches the store failure.
        self.assertEqual(value._lease_diagnostics.failure_category, 'task_writer_renewal_failed')
        self.assertEqual(coordinator._active_task_ids, set())
        self.assertIsNone(value._lease_diagnostics.last_success_at)
        coordinator._active_task_ids.add('paused-task')
        coordinator._store.renew_task_writer.side_effect = None
        value.renew()
        self.assertIsNone(value._lease_diagnostics.failure_category)
        self.assertIsNone(value._lease_diagnostics.last_success_at)
        coordinator._store.acquire_task_writer.assert_not_called()

    def test_intake_success_does_not_clear_observed_task_failure(self):
        diag = LeaseRenewalDiagnostics()
        diag.task_completed(False)
        diag.succeeded()
        self.assertEqual(diag.failure_category, 'task_writer_renewal_failed')
        diag.task_completed(True)
        self.assertIsNone(diag.failure_category)

    def test_disabled_intake_does_not_mint_renewal_evidence(self):
        value = self.activation()
        value.settings.langgraph_primary_new_intake_enabled = False
        value.renew()
        value.coordinator.renew_new_intake.assert_not_called()
        self.assertIsNone(value._lease_diagnostics.last_success_at)

# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 16:59 UTC | END]]
