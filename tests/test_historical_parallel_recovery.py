# [[Codex Director | GPT-6 Astra | 2026-09-23 | 00:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 17:27 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:30 UTC | BEGIN]]
"""Historical plan identity remains frozen across allocation schema upgrades."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from director_orchestrator.lease_diagnostics import LeaseRenewalDiagnostics
from director_orchestrator.primary_default import (
    DefaultPrimaryActivation, DefaultPrimaryApplication,
    HistoricalParallelPlanHold, _digest, _historical_parallel_binding,
)


def historical_projection():
    plan = {
        "project": "fixture", "normalized_outcome": "Review fixture",
        "risk_level": "low", "approval_required": True,
        "max_hops": 3, "max_revisions": 0,
        "assignments": [{"sequence": 1, "agent": "claude", "task": "Review",
                         "acceptance": ["Report evidence"]}],
        "final_acceptance": ["Independent review"],
    }
    # Frozen pre-5537e72 canonical payload, independent of current model defaults.
    binding = _digest({"task_id": "historical", "plan_digest": _digest(plan),
        "assignments": [{"assignment_id": "historical-1", "sequence": 1,
            "provider_id": "claude", "deployment_id": "claude",
            "dependencies": [], "write_surfaces": [], "retry_limit": 3}]})
    return SimpleNamespace(task_id="historical", projection={
        "parallel_plan_digest": binding, "terminal_classification": None,
        "application": {"task_record": {"task_id": "historical",
            "status": "OWNER_DECISION_REQUIRED", "plan": plan,
            "intake": {"source_event_id": "fixture", "channel_id": "fixture", "thread_ts": "1.0",
                       "user_id": "fixture", "text": "Review fixture"}}}})


class HistoricalParallelRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.projection = historical_projection()
        self.coordinator = Mock()
        self.coordinator.get_projection.return_value = self.projection
        self.coordinator._runtime.compact_state.return_value = {
            "parallel_plan_digest": self.projection.projection["parallel_plan_digest"]}
        self.application = object.__new__(DefaultPrimaryApplication)
        self.application.coordinator = self.coordinator
        self.application.services = Mock()

    def test_old_binding_recognized_without_inserting_new_defaults(self):
        before = copy.deepcopy(self.projection.projection)
        self.assertEqual(before["parallel_plan_digest"], _historical_parallel_binding(before))
        self.assertEqual(before, self.projection.projection)
        before["application"]["task_record"]["plan"]["assignments"][0]["allocation_profile"] = "engineering"
        self.assertIsNone(_historical_parallel_binding(before))

    def test_recovery_and_approval_hold_before_any_mutation(self):
        for operation in (lambda: self.application.recover("historical"),
                          lambda: self.application.resume_approval(SimpleNamespace(task_id="historical"))):
            with self.assertRaises(HistoricalParallelPlanHold):
                operation()
        self.application.services.hydrate.assert_not_called()
        self.coordinator.register_parallel_plan.assert_not_called()
        self.coordinator.recover.assert_not_called()
        self.coordinator.resume_approval.assert_not_called()

    def test_checkpoint_mismatch_is_not_downgraded_to_historical_hold(self):
        self.coordinator._runtime.compact_state.return_value = {"parallel_plan_digest": "sha256:" + "0" * 64}
        with self.assertRaisesRegex(ValueError, "parallel plan binding changed"):
            self.application.recover("historical")

    def test_unrecognized_plan_is_not_recognized(self):
        self.projection.projection["parallel_plan_digest"] = "sha256:" + "0" * 64
        self.assertIsNone(_historical_parallel_binding(self.projection.projection))

    def test_new_schema_does_not_enter_historical_hold(self):
        plan = self.projection.projection["application"]["task_record"]["plan"]
        plan["assignments"][0]["allocation_profile"] = "engineering"
        self.application._check_historical_parallel_hold("historical")
        self.coordinator._runtime.compact_state.assert_not_called()

    def test_unexpected_recovery_failure_still_aborts_reconciliation(self):
        self.projection.projection["parallel_plan_digest"] = "sha256:" + "0" * 64
        self.coordinator.list_projections.return_value = (self.projection,)
        self.coordinator.get_task_writer.return_value = None
        activation = object.__new__(DefaultPrimaryActivation)
        activation._lease_diagnostics = LeaseRenewalDiagnostics()
        activation.application = Mock()
        activation.application.recover.side_effect = ValueError("parallel plan binding changed")
        activation.services = SimpleNamespace(occupied=lambda _task_id: False)
        activation.coordinator = self.coordinator
        with self.assertRaisesRegex(ValueError, "parallel plan binding changed"):
            activation.reconcile_once()
        self.assertFalse(activation._reconciliation_worker_ready)

    def test_repeated_startup_stays_visibly_unready_and_does_not_rewrite(self):
        before = copy.deepcopy(self.projection.projection)
        self.coordinator.list_projections.return_value = (self.projection,)
        activation = object.__new__(DefaultPrimaryActivation)
        activation._lease_diagnostics = LeaseRenewalDiagnostics()
        activation.application = self.application
        activation.coordinator = self.coordinator
        activation.services = self.application.services
        for _ in range(2):
            self.assertEqual(0, activation.reconcile_once())
            self.assertFalse(activation._reconciliation_worker_ready)
            self.assertEqual(1, activation._historical_parallel_hold_count)
        self.assertEqual(before, self.projection.projection)
        self.coordinator.get_task_writer.assert_not_called()
        activation.settings = SimpleNamespace(
            langgraph_primary_configuration_readiness=lambda: {},
            langgraph_primary_live_provider_dispatch_enabled=False, allow_paid_runs=False)
        activation.provider_activation = None
        activation.services = None
        self.coordinator.readiness.return_value.as_dict.return_value = {
            "primary_router_wired": True, "checkpoint_backend_postgres": True,
            "claim_store_ready": True, "writer_lease_ready": True,
            "legacy_new_intake_disabled": True}
        health = activation.health()
        self.assertEqual("historical_parallel_plan_requires_reconciliation", health["reconciliation_hold_reason"])
        self.assertFalse(health["primary_runtime_active"])
        self.assertFalse(health["paid_off_primary_intake_active"])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:30 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 17:27 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-23 | 00:50 UTC | END]]
