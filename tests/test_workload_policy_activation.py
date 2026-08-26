# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:59 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | BEGIN]]
from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.approvals import reserve_provider_effect
from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.langgraph_runtime import RuntimeExecutionStatus
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore, WorkloadAttemptOutcome
from director_orchestrator.workload_policy import (
    EnforcementMode,
    PRIMARY_PROVIDERS,
    Provider,
    WorkKind,
    WorkSize,
)


class WorkloadPolicyActivationTests(unittest.TestCase):
    def test_runtime_uses_the_same_trusted_work_kind_as_plan_enforcement(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Develop a bounded implementation without relying on its type label",
            acceptance=["Implementation evidence is recorded"],
        )

        self.assertIs(
            OrchestrationEngine._workload_kind(assignment),
            WorkKind.IMPLEMENTATION,
        )

    def test_default_configuration_is_enabled_and_enforced(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKLOAD_POLICY_ENABLED": "true",
                "WORKLOAD_POLICY_MODE": "enforced",
                "WORKLOAD_POLICY_WINDOW_ASSIGNMENTS": "20",
                "WORKLOAD_POLICY_SHADOW_ASSIGNMENTS": "5",
            },
            clear=False,
        ):
            settings = Settings()

        settings.validate_workload_policy_configuration()
        self.assertEqual(
            settings.workload_policy_readiness(),
            {
                "enabled": True,
                "enforced": True,
                "shadow": False,
                "mode_valid": True,
                "window_valid": True,
                "shadow_sample_valid": True,
            },
        )

    def test_shadow_mode_is_observe_only_but_remains_valid(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKLOAD_POLICY_ENABLED": "true",
                "WORKLOAD_POLICY_MODE": "shadow",
                "WORKLOAD_POLICY_WINDOW_ASSIGNMENTS": "20",
                "WORKLOAD_POLICY_SHADOW_ASSIGNMENTS": "5",
            },
            clear=False,
        ):
            settings = Settings()

        settings.validate_workload_policy_configuration()
        self.assertTrue(settings.workload_policy_readiness()["shadow"])
        self.assertFalse(settings.workload_policy_readiness()["enforced"])

    def test_health_readiness_uses_persisted_shadow_promotion(self) -> None:
        settings = replace(
            Settings(),
            workload_policy_enabled=True,
            workload_policy_mode="shadow",
            workload_policy_shadow_assignments=5,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "director.sqlite3")
            for sequence in range(1, 6):
                reservation = store.reserve_workload_assignment(
                    reservation_key=f"health-shadow-{sequence}",
                    task_id="DIR-HEALTH-SHADOW",
                    assignment_id=f"DIR-HEALTH-SHADOW:{sequence}",
                    phase="dispatch",
                    provider=Provider.CLAUDE,
                    effort_units=1,
                    kind=WorkKind.COORDINATION,
                    size=WorkSize.SIMPLE,
                    model_label=None,
                    available_primary_providers=PRIMARY_PROVIDERS,
                    mode=EnforcementMode.SHADOW,
                    shadow_assignment_limit=5,
                )
                store.finalize_workload_reservation(
                    reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
                )

            effective = store.effective_workload_mode(
                configured_mode=EnforcementMode.SHADOW,
                shadow_assignment_limit=5,
            )
            readiness = settings.workload_policy_readiness(
                effective_mode=effective.value
            )

        self.assertEqual(effective, EnforcementMode.ENFORCED)
        self.assertTrue(readiness["enforced"])
        self.assertFalse(readiness["shadow"])

    def test_disabled_policy_reports_no_active_enforced_or_shadow_mode(self) -> None:
        for mode in ("enforced", "shadow"):
            with self.subTest(mode=mode):
                settings = replace(
                    Settings(),
                    workload_policy_enabled=False,
                    workload_policy_mode=mode,
                )

                readiness = settings.workload_policy_readiness()

                self.assertFalse(readiness["enabled"])
                self.assertFalse(readiness["enforced"])
                self.assertFalse(readiness["shadow"])
                self.assertTrue(readiness["mode_valid"])

    def test_invalid_runtime_mode_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {"WORKLOAD_POLICY_MODE": "advisory"},
            clear=False,
        ):
            settings = Settings()

        with self.assertRaisesRegex(ValueError, "enforced.*shadow"):
            settings.validate_workload_policy_configuration()

    def test_window_and_shadow_sample_are_owner_fixed(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKLOAD_POLICY_WINDOW_ASSIGNMENTS": "19",
                "WORKLOAD_POLICY_SHADOW_ASSIGNMENTS": "4",
            },
            clear=False,
        ):
            settings = Settings()

        with self.assertRaisesRegex(ValueError, "exactly 20"):
            settings.validate_workload_policy_configuration()

    def test_runtime_gate_applies_only_to_new_v1_plans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = OrchestrationEngine(
                replace(
                    Settings(),
                    sqlite_path=str(Path(directory) / "director.sqlite3"),
                ),
                TaskStore(Path(directory) / "director.sqlite3"),
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CLAUDE,
                task="Perform bounded work",
                acceptance=["Evidence is recorded"],
            )
            plan = DirectorPlan(
                project="demo",
                normalized_outcome="Perform bounded work",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[assignment],
                final_acceptance=["Evidence is recorded"],
            )
            record = TaskRecord(
                task_id="DIR-WORKLOAD-ACTIVATION",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-workload-activation",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Perform bounded work",
                ),
                plan=plan,
            )

            self.assertFalse(engine._workload_policy_applies(record))
            record.plan = plan.model_copy(update={"workload_policy_version": "v1"})
            self.assertTrue(engine._workload_policy_applies(record))

    def test_packet_workload_stop_uses_canonical_task_effect_phase(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Perform bounded work",
            acceptance=["Evidence is recorded"],
        )
        record = TaskRecord(
            task_id="DIR-WORKLOAD-PACKET-STOP",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-workload-packet-stop",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="Perform bounded work",
            ),
        )

        effect_key = reserve_provider_effect(
            record,
            assignment,
            phase="dispatch",
            max_task_budget_usd=3.0,
        )
        OrchestrationEngine._record_workload_pre_submit_stop(
            record,
            assignment,
            effect_key=effect_key,
            phase="parallel_packet_dispatch",
        )

        self.assertEqual(record.task_effect_ledger[-1].phase, "dispatch")
        self.assertEqual(record.task_effect_ledger[-1].outcome, "failed_pre_submit")

    def test_packet_runtime_outcome_counts_unresolved_dispatch_conservatively(
        self,
    ) -> None:
        cases = (
            (
                RuntimeExecutionStatus.COMPLETED,
                WorkloadAttemptOutcome.INVOKED,
            ),
            (
                RuntimeExecutionStatus.REPLAYED,
                WorkloadAttemptOutcome.CACHED_REPLAY,
            ),
            (
                RuntimeExecutionStatus.REJECTED,
                WorkloadAttemptOutcome.FAILED_PRE_SUBMIT,
            ),
            (
                RuntimeExecutionStatus.OWNER_DECISION_REQUIRED,
                WorkloadAttemptOutcome.INVOKED,
            ),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                self.assertIs(
                    OrchestrationEngine._packet_workload_outcome(
                        status,
                        "dispatch state is unresolved",
                    ),
                    expected,
                )


# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:59 UTC | END]]
