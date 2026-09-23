# [[Codex Director | GPT-6 Astra | 2026-09-23 | 04:05 UTC | BEGIN]]
"""The HTTP approval receipt must precede slow provider work."""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import BackgroundTasks
from director_orchestrator import main
from director_orchestrator.langgraph_primary import ApprovalDecision, ApprovalKind, ApprovalResume
from director_orchestrator.primary_commands import ApprovalResumeRequest, PrimaryIngressProof
from director_orchestrator.primary_default import DefaultPrimaryApplication


class ApprovalAcknowledgementTests(unittest.TestCase):
    def test_deferred_approval_persists_without_entering_provider_wave(self):
        app = object.__new__(DefaultPrimaryApplication)
        app.settings = SimpleNamespace(langgraph_primary_offline_simulation_enabled=False,
            langgraph_primary_live_provider_dispatch_enabled=True)
        app.services = MagicMock()
        app.coordinator = MagicMock()
        app._check_historical_parallel_hold = MagicMock()
        app._record_from_projection = MagicMock(return_value=SimpleNamespace(task_id='task-1'))
        app._drive_parallel_lifecycle = MagicMock()
        command = ApprovalResume(task_id='task-1', kind=ApprovalKind.EXECUTION,
            request_digest='sha256:'+'a'*64, approval_event_id='event-1', decision=ApprovalDecision.APPROVE)
        app.resume_approval(command, defer_provider_dispatch=True)
        app.coordinator.resume_approval.assert_called_once_with(command)
        app._drive_parallel_lifecycle.assert_not_called()
        app.services.discard.assert_called_once_with('task-1')
        app.resume_approval(command)
        app._drive_parallel_lifecycle.assert_called_once()

    def test_http_ack_precedes_recovery_and_denial_never_dispatches(self):
        for decision in (ApprovalDecision.APPROVE, ApprovalDecision.DENY):
            with self.subTest(decision=decision), ExitStack() as stack:
                order = []
                application = MagicMock()
                application.resume_approval.side_effect = lambda *a, **k: order.append('approval')
                application.recover.side_effect = lambda *a, **k: order.append('provider-wave')
                coordinator = MagicMock()
                request = ApprovalResumeRequest(task_id='task-1', command_id='event-1', actor_id='owner',
                    expected_projection_digest='sha256:'+'a'*64, expected_fencing_generation=1,
                    kind=ApprovalKind.EXECUTION, request_digest='sha256:'+'b'*64,
                    approval_event_id='event-1', decision=decision,
                    slack_gateway_proof=PrimaryIngressProof(issued_at=1, nonce='n'*32,
                        signature='0'*64, channel_id='C123'))
                stack.enter_context(patch.object(main, 'primary_default_activation',
                    SimpleNamespace(application=application)))
                stack.enter_context(patch.object(main, '_require_primary_owner_actor'))
                stack.enter_context(patch.object(main, 'verify_primary_ingress_proof', return_value='nonce'))
                stack.enter_context(patch.object(main, 'settings', replace(main.settings, slack_channel_id='C123')))
                stack.enter_context(patch.object(main, '_primary_command_projection',
                    return_value=(coordinator, None, SimpleNamespace(fencing_generation=1), None)))
                stack.enter_context(patch.object(main, '_record_primary_command',
                    side_effect=lambda *a, **k: order.append('receipt') or 'ack'))
                background = BackgroundTasks()
                self.assertEqual(main.primary_resume_approval('task-1', request, background), 'ack')
                self.assertEqual(order, ['approval', 'receipt'])
                self.assertTrue(application.resume_approval.call_args.kwargs['defer_provider_dispatch'])
                asyncio.run(background())
                self.assertEqual(order, ['approval', 'receipt'] +
                    (['provider-wave'] if decision == ApprovalDecision.APPROVE else []))


if __name__ == '__main__':
    unittest.main()
# [[Codex Director | GPT-6 Astra | 2026-09-23 | 04:05 UTC | END]]
