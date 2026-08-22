# [[OpenAI Codex | GPT-5.6 | 2026-08-22 16:14 UTC | BEGIN]]
"""Bounded evidence that shadow checkpoints stay isolated and decision-equivalent."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from director_orchestrator.langgraph_shadow import (
    ShadowTaskSnapshot,
    checkpoint_config,
    run_shadow_comparison,
)
from director_orchestrator.models import TaskStatus


class LangGraphBoundedEvidenceTests(unittest.TestCase):
    def test_one_hundred_representative_shadow_workflows_agree_and_stay_isolated(self):
        """Exercise every status plus policy, approval, and clarification routes."""

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            legacy_path = root / "legacy.sqlite3"
            graph_path = root / "langgraph.sqlite3"

            with sqlite3.connect(legacy_path) as legacy:
                legacy.execute(
                    "CREATE TABLE legacy_sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                legacy.execute("INSERT INTO legacy_sentinel(value) VALUES ('unchanged')")

            graph_connection = sqlite3.connect(graph_path, check_same_thread=False)
            saver = SqliteSaver(graph_connection)

            statuses = tuple(TaskStatus)
            for index in range(100):
                route = index % 12
                kwargs: dict[str, object] = {}
                if route == 0:
                    status = TaskStatus.BLOCKED
                    kwargs["policy_errors"] = ("repository_not_allowlisted",)
                elif route == 1:
                    status = TaskStatus.OWNER_DECISION_REQUIRED
                    kwargs["plan_approval_required"] = True
                elif route == 2:
                    status = TaskStatus.OWNER_DECISION_REQUIRED
                    kwargs.update(
                        pending_assignment_sequence=2,
                        pending_assignment_requires_approval=True,
                    )
                elif route == 3:
                    # A clarification stop is not an approval gate.
                    status = TaskStatus.OWNER_DECISION_REQUIRED
                else:
                    status = statuses[(index - 4) % len(statuses)]
                    if status == TaskStatus.OWNER_DECISION_REQUIRED:
                        kwargs["plan_approval_required"] = True

                snapshot = ShadowTaskSnapshot(
                    task_id=f"DIR-EVIDENCE-{index:03d}",
                    status=status,
                    previous_status=TaskStatus.QUEUED,
                    transition_reason=f"bounded-fixture-{route}",
                    plan_shape_digest=f"{index:064x}",
                    **kwargs,
                )
                report = run_shadow_comparison(
                    snapshot,
                    checkpointer=saver,
                    config=checkpoint_config(
                        snapshot.task_id,
                        namespace="bounded-evidence-v1",
                    ),
                )

                with self.subTest(index=index, route=route):
                    self.assertTrue(report.statuses_agree)
                    self.assertTrue(report.classifications_agree)
                    self.assertTrue(report.approvals_agree)
                    self.assertEqual(report.differences, ())

            with sqlite3.connect(legacy_path) as legacy:
                self.assertEqual(
                    legacy.execute("SELECT value FROM legacy_sentinel").fetchone(),
                    ("unchanged",),
                )
                self.assertEqual(
                    {
                        row[0]
                        for row in legacy.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    },
                    {"legacy_sentinel"},
                )

            graph_tables = {
                row[0]
                for row in graph_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("checkpoints", graph_tables)
            self.assertNotIn("tasks", graph_tables)
            self.assertNotIn("legacy_sentinel", graph_tables)
            self.assertGreaterEqual(
                graph_connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0],
                100,
            )
            graph_connection.close()


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-08-22 16:14 UTC | END]]
