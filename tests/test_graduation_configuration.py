# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 07:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | BEGIN]]
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings


class GraduationConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.base = replace(
            Settings(),
            sqlite_path=str(root / "tasks.sqlite3"),
            langgraph_checkpoint_path=str(root / "shadow.sqlite3"),
            langgraph_checkpoint_namespace="graduation-shadow-v1",
            langgraph_runtime_checkpoint_path=str(root / "runtime.sqlite3"),
            langgraph_runtime_ledger_path=str(root / "ledger.sqlite3"),
            langgraph_runtime_checkpoint_namespace="graduation-runtime-v1",
            langgraph_runtime_writer_secret="w" * 32,
            repository_snapshot_root=str(root),
            completion_gate_store_path=str(root / "completion.sqlite3"),
            project_allowlist=("long-table-bid-whist",),
            repository_allowlist=("edwardjones3976/long-table-bid-whist",),
            allowed_starting_refs=("main",),
            claude_callback_secret="configured-for-test",
        )

    def test_both_completion_tiers_require_a_pinned_container(self):
        invalid = replace(
            self.base,
            completion_gates_enabled=True,
            completion_gate_isolated_proof_enabled=True,
            completion_gate_execution_tiers=("host", "container"),
            completion_gate_container_image="python:3.12-slim",
        )
        with self.assertRaisesRegex(ValueError, "exact sha256 digest"):
            invalid.validate_completion_gate_configuration()

        valid = replace(
            invalid,
            completion_gate_container_image="python:3.12-slim@sha256:" + "a" * 64,
        )
        valid.validate_completion_gate_configuration()
        readiness = valid.completion_gate_readiness()
        self.assertTrue(readiness["execution_active"])
        self.assertTrue(readiness["container_image_pinned"])

    def test_task_writer_requires_runtime_live_dispatch_and_paid_profile(self):
        settings = replace(
            self.base,
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            allow_paid_runs=True,
        )
        readiness = settings.langgraph_runtime_readiness()
        self.assertTrue(readiness["live_provider_dispatch"])
        self.assertTrue(readiness["task_writer_active"])

        self.assertFalse(
            replace(settings, allow_paid_runs=False).langgraph_runtime_readiness()[
                "task_writer_active"
            ]
        )

        without_writer = replace(
            settings, langgraph_runtime_task_writer_enabled=False
        )
        self.assertFalse(
            without_writer.langgraph_runtime_readiness()["live_provider_dispatch"]
        )
        with self.assertRaisesRegex(
            ValueError, "LANGGRAPH_RUNTIME_TASK_WRITER_ENABLED"
        ):
            without_writer.validate_langgraph_runtime_configuration()

    def test_parallel_dispatch_requires_open_kill_switch_and_task_writer(self):
        settings = replace(
            self.base,
            langgraph_runtime_enabled=True,
            langgraph_runtime_live_dispatch_enabled=True,
            langgraph_runtime_task_writer_enabled=True,
            allow_paid_runs=True,
            langgraph_parallel_external_dispatch_enabled=True,
            langgraph_parallel_kill_switch=False,
        )
        settings.validate_parallel_dispatch_configuration()
        readiness = settings.parallel_dispatch_readiness()
        self.assertTrue(readiness["active"])
        self.assertEqual(2, readiness["initial_max_concurrency"])
        self.assertEqual(3, readiness["auto_scale_max_concurrency"])
        health_readiness = settings.parallel_dispatch_health_readiness()
        self.assertTrue(health_readiness)
        self.assertTrue(all(isinstance(value, bool) for value in health_readiness.values()))
        self.assertNotIn("initial_max_concurrency", health_readiness)

        closed = replace(settings, langgraph_parallel_kill_switch=True)
        self.assertFalse(closed.parallel_dispatch_readiness()["active"])

    def test_parallel_limits_fail_closed_when_enabled(self):
        invalid = replace(
            self.base,
            langgraph_parallel_external_dispatch_enabled=True,
            langgraph_parallel_clean_batches_to_scale=9,
        )
        with self.assertRaisesRegex(ValueError, "10 clean batches"):
            invalid.validate_parallel_dispatch_configuration()

    def test_long_table_daily_limit_is_independent_of_task_limit(self):
        settings = replace(
            self.base,
            max_task_budget_usd=1.0,
            max_daily_provider_budget_usd=5.0,
        )
        self.assertEqual(1.0, settings.max_task_budget_usd)
        self.assertEqual(5.0, settings.max_daily_provider_budget_usd)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 07:37 UTC | END]]
