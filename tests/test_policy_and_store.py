from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.models import SlackIntake, TaskRecord, TaskStatus
from director_orchestrator.policy import deterministic_approval_reasons, preview_plan
from director_orchestrator.store import TaskStore


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(Settings(), max_agent_hops=4, max_revisions=1, max_task_budget_usd=1.0)

    def test_merge_and_deploy_require_approval(self):
        reasons = deterministic_approval_reasons("Deploy to production and merge directly to main")
        self.assertIn("production deployment or public publishing", reasons)
        self.assertIn("merge or protected-branch change", reasons)

    def test_dry_run_routes_research_and_clamps_limits(self):
        plan = preview_plan("Project: RootWitness\nResearch current retention requirements with sources", self.settings)
        self.assertEqual(plan.project, "RootWitness")
        self.assertEqual(plan.assignments[0].agent.value, "perplexity")
        self.assertLessEqual(plan.max_hops, 4)
        self.assertLessEqual(plan.max_revisions, 1)

    def test_dry_run_uses_claude_as_default_workhorse(self):
        plan = preview_plan("Project: demo\nImplement and test a bounded API change", self.settings)
        self.assertEqual(plan.assignments[0].agent.value, "claude")

    def test_dry_run_can_request_cursor_second_pass_explicitly(self):
        plan = preview_plan("Project: demo\nRun an independent coding pass in Cursor", self.settings)
        self.assertEqual(plan.assignments[0].agent.value, "cursor")

    def test_primary_team_is_openai_claude_cursor(self):
        self.assertEqual(self.settings.primary_team, ("openai", "claude", "cursor"))
        strategy = self.settings.provider_strategy()
        self.assertTrue(all(strategy[name]["team"] == "primary" for name in self.settings.primary_team))
        self.assertEqual(strategy["gemini"]["team"], "supplemental")
        self.assertEqual(strategy["perplexity"]["team"], "supplemental")


class StoreTests(unittest.TestCase):
    def test_source_event_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            intake = SlackIntake(
                source_event_id="evt-1",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: test",
            )
            first = TaskRecord(task_id="DIR-ONE", status=TaskStatus.QUEUED, intake=intake)
            second = TaskRecord(task_id="DIR-TWO", status=TaskStatus.QUEUED, intake=intake)
            stored_one, created_one = store.create(first)
            stored_two, created_two = store.create(second)
            self.assertTrue(created_one)
            self.assertFalse(created_two)
            self.assertEqual(stored_one.task_id, stored_two.task_id)

    def test_recoverable_tasks_exclude_owner_waits_and_terminal_states(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            for index, status in enumerate(
                (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.OWNER_DECISION_REQUIRED, TaskStatus.DONE)
            ):
                intake = SlackIntake(
                    source_event_id=f"evt-{index}",
                    channel_id="C123",
                    thread_ts=f"123.{index}",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: test",
                )
                store.create(TaskRecord(task_id=f"DIR-{index}", status=status, intake=intake))
            self.assertEqual(
                {record.task_id for record in store.list_recoverable()},
                {"DIR-0", "DIR-1"},
            )


if __name__ == "__main__":
    unittest.main()
