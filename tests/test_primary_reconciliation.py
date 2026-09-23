# [[Codex Director | GPT-6 Astra | 2026-09-23 | 00:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 17:27 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:33 UTC | BEGIN]]
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from director_orchestrator.lease_diagnostics import LeaseRenewalDiagnostics
from director_orchestrator.primary_default import DefaultPrimaryActivation


class PrimaryReconciliationLeaseTests(unittest.TestCase):
    @staticmethod
    def activation(*, new_intake: bool = True) -> DefaultPrimaryActivation:
        activation = object.__new__(DefaultPrimaryActivation)
        activation._lease_diagnostics = LeaseRenewalDiagnostics()
        activation.application = MagicMock()
        activation.settings = SimpleNamespace(
            langgraph_primary_new_intake_enabled=new_intake
        )
        activation.coordinator = MagicMock()
        activation.services = MagicMock()
        activation.services.occupied.return_value = False
        activation._reconciliation_worker_ready = False
        return activation

    def test_live_writer_heartbeat_prevents_inflight_reconciliation(self) -> None:
        activation = self.activation()
        live = SimpleNamespace(
            task_id="task-live", projection={"terminal_classification": None}
        )
        stale = SimpleNamespace(
            task_id="task-stale", projection={"terminal_classification": None}
        )
        terminal = SimpleNamespace(
            task_id="task-terminal", projection={"terminal_classification": "SUCCEEDED"}
        )
        activation.coordinator.list_projections.return_value = (live, stale, terminal)
        activation.coordinator.get_task_writer.side_effect = (
            SimpleNamespace(lease_live=True),
            SimpleNamespace(lease_live=False),
        )
        activation.coordinator.parallel_aggregate.return_value = SimpleNamespace(
            statuses={"assignment-live": "ACTIVE"}
        )
        activation.application.recover.return_value = stale

        self.assertEqual(1, activation.reconcile_once())
        activation.application.recover.assert_called_once_with("task-stale")
        activation.coordinator.parallel_aggregate.assert_called_once_with("task-live")
        activation.coordinator.recover.assert_not_called()
        activation.services.hydrate.assert_not_called()
        activation.services.discard.assert_not_called()

    def test_task_writer_heartbeats_continue_when_new_intake_is_disabled(self) -> None:
        activation = self.activation(new_intake=False)

        activation.renew()

        activation.coordinator.heartbeat_active_task_writers.assert_called_once_with(
            on_renewal_outcome=activation._lease_diagnostics.task_completed
        )
        activation.coordinator.renew_new_intake.assert_not_called()


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:33 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:00 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 17:27 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-23 | 00:50 UTC | END]]
