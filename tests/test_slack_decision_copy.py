# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:42 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:37 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:33 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:26 UTC | BEGIN]]
"""Offline render-only tests for Slack decision copy and terminal outcomes."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
    DirectorPlan,
    IntakeAuthentication,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.primary_slack_delivery import render_notification


class SlackDecisionCopyRenderTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            slack_channel_id="C_TEST",
            slack_owner_user_id="U_OWNER",
        )
        self.created_at = datetime(2026, 9, 13, 11, 26, tzinfo=timezone.utc)
        self.plan = DirectorPlan(
            project="test-project",
            normalized_outcome="Test outcome objective",
            risk_level=RiskLevel.LOW,
            approval_required=True,
            max_hops=3,
            max_revisions=2,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.OPENAI,
                    task="Implementation task",
                    repository="owner/repo",
                    starting_ref="main",
                    allow_draft_pr=True,
                    acceptance=["Tests green"],
                ),
            ],
            final_acceptance=["All tests pass", "Zero lint errors"],
        )

    def _make_task_record(self, outcomes=None):
        return TaskRecord(
            task_id="DIR-LG-TEST001",
            status=TaskStatus.RUNNING,
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
            intake=SlackIntake(
                source_event_id="evt_12345",
                channel_id="C_TEST",
                user_id="U_OWNER",
                text="Test prompt",
                thread_ts="1720000001.000001",
            ),
            plan=self.plan,
            assignment_outcomes=outcomes or [],
        )

    def test_pending_approval_has_concise_opening_and_complete_scope(self):
        record = self._make_task_record()
        row = {
            "projection": {
                "terminal_classification": "OWNER_DECISION_REQUIRED",
                "pending_approval": {
                    "kind": "plan_execution",
                    "request_digest": "sha256:" + "a" * 64,
                },
                "application": {
                    "task_record": record.model_dump(mode="json"),
                    "execution_mode": "offline_simulation",
                },
            },
            "projection_digest": "sha256:" + "b" * 64,
            "created_at": self.created_at,
        }
        rendered = render_notification(row, self.settings)
        self.assertIsNotNone(rendered)
        text = rendered["blocks"][0]["text"]["text"]
        self.assertIn("DIR-LG-TEST001 — OWNER DECISION REQUIRED", text)
        self.assertIn("SIMULATION — NO PROVIDER INTERACTION", text)
        self.assertIn(
            "Owner decision required for plan_execution. Review the complete proposed scope below:",
            text,
        )
        self.assertIn("Test outcome objective", text)
        self.assertIn("1. openai: Implementation task", text)
        self.assertIn("Acceptance: Tests green", text)
        self.assertIn("Final acceptance: All tests pass; Zero lint errors", text)
        self.assertIn("Decision scope: plan_execution", text)
        self.assertIn(
            "Approve or reject this exact request using the buttons below.",
            text,
        )
        self.assertEqual(len(rendered["blocks"][-1]["elements"]), 2)

    def test_terminal_completion_passed_exact_sentinel_compacts_to_simulated_result(self):
        outcomes = [
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.OPENAI,
                run_id="offline-double:123",
                result="SIMULATION — NO PROVIDER INTERACTION",
                review_summary="SIMULATION — NO PROVIDER INTERACTION - offline provider double",
            )
        ]
        record = self._make_task_record(outcomes=outcomes)
        row = {
            "projection": {
                "terminal_classification": "SUCCEEDED",
                "reason_code": "completion_passed",
                "application": {
                    "task_record": record.model_dump(mode="json"),
                    "execution_mode": "offline_simulation",
                },
            },
            "projection_digest": "sha256:" + "c" * 64,
            "created_at": self.created_at,
        }
        rendered = render_notification(row, self.settings)
        self.assertIsNotNone(rendered)
        text = rendered["blocks"][0]["text"]["text"]
        self.assertIn("DIR-LG-TEST001 — SUCCEEDED", text)
        # Yields exactly one global sentinel marker at the top
        self.assertEqual(text.count("SIMULATION — NO PROVIDER INTERACTION"), 1)
        self.assertIn("All planned assignments completed and passed verification.", text)
        self.assertNotIn("Reason: completion_passed", text)
        # Exact sentinel compacted underneath the existing global marker
        self.assertIn("openai: simulated result", text)
        self.assertNotIn("openai: SIMULATION", text)
        self.assertIn(
            "Actual USD cost: not recorded; budget units are not billing evidence.",
            text,
        )
        self.assertNotIn("$0", text)

    def test_offline_simulation_preserves_every_non_sentinel_value(self):
        outcomes = [
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.OPENAI,
                run_id="offline-double:101",
                result="-5 score penalty",
                review_summary="-5 score penalty",
            ),
            AssignmentOutcome(
                assignment_sequence=2,
                provider=ProviderName.CLAUDE,
                run_id="offline-double:102",
                result=":warning: failure detail",
                review_summary=":warning: failure detail",
            ),
            AssignmentOutcome(
                assignment_sequence=3,
                provider=ProviderName.CURSOR,
                run_id="offline-double:103",
                result="--dash-option",
                review_summary="--dash-option",
            ),
            AssignmentOutcome(
                assignment_sequence=4,
                provider=ProviderName.GROK,
                run_id="offline-double:104",
                result="SIMULATION — NO PROVIDER INTERACTION - offline provider double",
                review_summary="sentinel plus suffix prose",
            ),
            AssignmentOutcome(
                assignment_sequence=5,
                provider=ProviderName.GEMINI,
                run_id="offline-double:105",
                result="SIMULATION - NO PROVIDER INTERACTION",
                review_summary="hyphen variation",
            ),
        ]
        record = self._make_task_record(outcomes=outcomes)
        row = {
            "projection": {
                "terminal_classification": "SUCCEEDED",
                "reason_code": "completion_passed",
                "application": {
                    "task_record": record.model_dump(mode="json"),
                    "execution_mode": "offline_simulation",
                },
            },
            "projection_digest": "sha256:" + "e" * 64,
            "created_at": self.created_at,
        }
        rendered = render_notification(row, self.settings)
        self.assertIsNotNone(rendered)
        text = rendered["blocks"][0]["text"]["text"]
        # Negative, colon, dash content preserved byte-for-byte
        self.assertIn("openai: -5 score penalty", text)
        self.assertIn("claude: :warning: failure detail", text)
        self.assertIn("cursor: --dash-option", text)
        # Sentinel-plus-suffix prose preserved byte-for-byte (not stripped)
        self.assertIn(
            "grok: SIMULATION — NO PROVIDER INTERACTION - offline provider double",
            text,
        )
        # Any non-exact sentinel preserved byte-for-byte
        self.assertIn("gemini: SIMULATION - NO PROVIDER INTERACTION", text)

    def test_live_and_unknown_mode_preserves_exact_sentinel_and_outcomes(self):
        outcomes = [
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.OPENAI,
                run_id="live-run-101",
                result="SIMULATION — NO PROVIDER INTERACTION",
                review_summary="sentinel in live mode",
            ),
            AssignmentOutcome(
                assignment_sequence=2,
                provider=ProviderName.CLAUDE,
                run_id="live-run-102",
                result="-5 points penalty",
                review_summary="-5 points penalty",
            ),
        ]
        record = self._make_task_record(outcomes=outcomes)
        row_live = {
            "projection": {
                "terminal_classification": "SUCCEEDED",
                "reason_code": "completion_passed",
                "application": {
                    "task_record": record.model_dump(mode="json"),
                    "execution_mode": "live",
                },
            },
            "projection_digest": "sha256:" + "f" * 64,
            "created_at": self.created_at,
        }
        rendered_live = render_notification(row_live, self.settings)
        self.assertIsNotNone(rendered_live)
        text_live = rendered_live["blocks"][0]["text"]["text"]
        # In live mode, exact sentinel is preserved unchanged
        self.assertIn("openai: SIMULATION — NO PROVIDER INTERACTION", text_live)
        self.assertNotIn("simulated result", text_live)
        self.assertNotIn("SIMULATION — NO PROVIDER INTERACTION", text_live.splitlines())
        self.assertIn("claude: -5 points penalty", text_live)

        # In unknown mode, exact sentinel is preserved unchanged
        row_unknown = {
            "projection": {
                "terminal_classification": "SUCCEEDED",
                "reason_code": "completion_passed",
                "application": {
                    "task_record": record.model_dump(mode="json"),
                    "execution_mode": "unknown",
                },
            },
            "projection_digest": "sha256:" + "1" * 64,
            "created_at": self.created_at,
        }
        rendered_unknown = render_notification(row_unknown, self.settings)
        self.assertIsNotNone(rendered_unknown)
        text_unknown = rendered_unknown["blocks"][0]["text"]["text"]
        self.assertIn("Historical execution mode is not recorded.", text_unknown)
        self.assertIn("openai: SIMULATION — NO PROVIDER INTERACTION", text_unknown)
        self.assertNotIn("simulated result", text_unknown)
        self.assertIn("claude: -5 points penalty", text_unknown)

    def test_terminal_other_reason_preserves_reason_code(self):
        record = self._make_task_record()
        row = {
            "projection": {
                "terminal_classification": "FAILED",
                "reason_code": "timeout_budget_exhausted",
                "application": {
                    "task_record": record.model_dump(mode="json"),
                    "execution_mode": "live",
                },
            },
            "projection_digest": "sha256:" + "d" * 64,
            "created_at": self.created_at,
        }
        rendered = render_notification(row, self.settings)
        self.assertIsNotNone(rendered)
        text = rendered["blocks"][0]["text"]["text"]
        self.assertIn("Reason: timeout_budget_exhausted", text)
        self.assertIn("No completed provider result is recorded.", text)


if __name__ == "__main__":
    unittest.main()
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:26 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:33 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:37 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:42 UTC | END]]
