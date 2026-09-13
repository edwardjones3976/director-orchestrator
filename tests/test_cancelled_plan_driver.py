# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:58 UTC | BEGIN]]
"""The owner driver emits safe diagnostics and never retries a submitted command."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location('cancelled_plan_driver',
    Path(__file__).resolve().parents[1] / 'scripts' / 'reconcile-cancelled-plan.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class CancelledPlanDriverTests(unittest.TestCase):
    def setUp(self):
        self.diagnostic = {
            **{key: 'sha256:' + 'a' * 64 for key in ('projection_digest', 'projection_plan_digest',
                'batch_plan_digest', 'diagnostic_digest')},
            'pending_assignment_count': 2, 'active_or_ambiguous_assignment_count': 0,
            'fencing_generation': 1, 'terminal_cancelled': True, 'kill_switch_engaged': True,
            'digests_differ': True, 'sequence_sets_differ': False, 'historical_digest_preimages_available': False,
            'adjudication_recorded': False, 'terminal_binding_consistent': False,
            'surviving_assignment_comparison': [{'sequence': 1, 'different_fields': ['allocation_profile'],
                                                'unavailable_fields': ['deployment_id']}],
        }
        self.client = MagicMock()
        self.client.get.return_value.json.return_value = self.diagnostic
        self.client.post.return_value.json.return_value = {
            'applied': True, 'closed_assignment_count': 2, 'evidence_digest': 'sha256:' + 'b' * 64}
        self.output = io.StringIO()
        self.environment = {'DIRECTOR_PRIMARY_URL': 'http://127.0.0.1:8080',
            'ORCHESTRATOR_SHARED_SECRET': 'synthetic-private-bearer', 'SLACK_GATEWAY_SECRET': 's' * 40,
            'SLACK_OWNER_USER_ID': 'UOWNER', 'SLACK_DIRECTOR_CHANNEL_ID': 'CTEST'}

    def run_driver(self, *, apply=False, confirm=None):
        with (patch.dict(os.environ, self.environment, clear=True),
              patch.object(driver.httpx, 'Client') as factory,
              patch('builtins.input', return_value=confirm or self.diagnostic['diagnostic_digest']),
              redirect_stdout(self.output), redirect_stderr(self.output)):
            factory.return_value.__enter__.return_value = self.client
            result = driver.main(['fixture-task'] + (['--apply'] if apply else []))
        for value in ('synthetic-private-bearer', 's' * 40, 'UOWNER', 'CTEST'):
            self.assertNotIn(value, self.output.getvalue())
        return result

    def test_diagnostic_only_sends_no_command(self):
        self.assertEqual(0, self.run_driver())
        self.client.get.assert_called_once()
        self.client.post.assert_not_called()

    def test_apply_requires_exact_owner_adjudication_before_signing(self):
        self.assertEqual(1, self.run_driver(apply=True, confirm='no'))
        self.client.post.assert_not_called()

    def test_apply_posts_one_signed_digest_bound_command(self):
        self.assertEqual(0, self.run_driver(apply=True))
        self.client.post.assert_called_once()
        request = driver.CancelledPlanReconciliationRequest.model_validate(self.client.post.call_args.kwargs['json'])
        self.assertEqual(self.diagnostic['diagnostic_digest'], request.expected_diagnostic_digest)
        from director_orchestrator.primary_commands import verify_primary_ingress_proof
        verify_primary_ingress_proof('s' * 40, 'synthetic-private-bearer', request,
            path='/v1/primary/tasks/fixture-task/cancelled-plan/reconcile', proof_field='gateway_proof')

    def test_unknown_submission_outcome_is_not_retried_or_printed(self):
        self.client.post.side_effect = RuntimeError('synthetic-private-bearer')
        self.assertEqual(1, self.run_driver(apply=True))
        self.client.post.assert_called_once()
        self.assertIn('outcome is unconfirmed', self.output.getvalue())

    def test_already_consistent_is_noop(self):
        self.diagnostic.update(adjudication_recorded=True, terminal_binding_consistent=True)
        self.assertEqual(0, self.run_driver(apply=True))
        self.client.post.assert_not_called()

    def test_untrusted_diagnostic_values_are_not_echoed(self):
        self.diagnostic['surviving_assignment_comparison'][0]['different_fields'] = ['synthetic-private-bearer']
        self.assertEqual(1, self.run_driver(apply=True))
        self.client.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:58 UTC | END]]
