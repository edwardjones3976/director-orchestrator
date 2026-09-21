# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:46 UTC | BEGIN]]
"""Ordinary cancellation must quiesce a never-admitted setup-held wave."""
import unittest
from unittest.mock import Mock, patch
from tests.test_primary_default import DefaultPrimaryApplicationIntegrationTests
from director_orchestrator.primary_parallel import PaidSetupHold
from director_orchestrator.models import IntakeAuthentication, TaskStatus

class PaidSetupCancellationTests(DefaultPrimaryApplicationIntegrationTests):
    def test_cancel_setup_hold_then_restart_never_dispatches(self):
        dispatcher=Mock(side_effect=AssertionError("cancelled work must never dispatch"))
        dispatcher.preflight=Mock(side_effect=PaidSetupHold())
        # This dispatcher publishes no readiness bound, which is the legacy
        # caller shape production still supports. An unrestricted Mock would
        # instead invent a callable `readiness_deadline` returning a Mock,
        # which the coordinator then subtracts from a float: a fixture
        # artefact, not a product defect. Marked absent explicitly, and
        # asserted, so a real protocol change fails here loudly.
        del dispatcher.readiness_deadline
        self.assertIsNone(getattr(dispatcher,'readiness_deadline',None))
        self.use_parallel_graph_completion_services(dispatcher)
        with patch('director_orchestrator.primary_default.preview_plan',return_value=self.parallel_plan()):
            pending=self.application.create_intake(self.intake(),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        held=self.approve(self.application,self.coordinator,pending.task_id)
        self.assertEqual(held.status,TaskStatus.BLOCKED)
        dispatcher.assert_not_called()
        self.application.cancel(pending.task_id)
        cancelled=self.coordinator.get_projection(pending.task_id)
        self.assertEqual(cancelled.projection['terminal_classification'],'CANCELLED')
        self.assertEqual(cancelled.projection['run_status'],'TERMINAL')
        # Construct a fresh coordinator/application against the disposable store.
        self.use_parallel_graph_completion_services(dispatcher)
        self.application.recover(pending.task_id)
        self.coordinator.reconcile_cancelled_pending(pending.task_id)
        after=self.coordinator.get_projection(pending.task_id)
        self.assertEqual(after.projection['terminal_classification'],'CANCELLED')
        self.assertEqual(after.projection['run_status'],'TERMINAL')
        aggregate=self.coordinator.parallel_aggregate(pending.task_id)
        self.assertEqual(aggregate.ambiguous_slot_count,0)
        self.assertNotIn('PENDING',aggregate.statuses.values())
        self.assertNotIn('ACTIVE',aggregate.statuses.values())
        dispatcher.assert_not_called()



def load_tests(loader, tests, pattern):
    # Reuse the fixture, not every inherited integration test.
    return unittest.TestSuite([PaidSetupCancellationTests(
        'test_cancel_setup_hold_then_restart_never_dispatches')])

# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:46 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:48 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | END]]
