from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.models import ProviderName, ProviderRun, UsageCheckStatus
from director_orchestrator.store import TaskStore
from director_orchestrator.usage import UsageMonitor, normalize_usage


class SuccessfulCursorProbe:
    def __init__(self):
        self.calls = 0

    async def usage_check(self, prompt: str) -> ProviderRun:
        self.calls += 1
        self.last_prompt = prompt
        return ProviderRun(
            provider=ProviderName.CURSOR,
            run_id="usage-run-1",
            status="FINISHED",
            result="STATUS=OK; MODEL=test; QUOTA=UNAVAILABLE",
            raw={"usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}},
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise AssertionError("Terminal usage checks must not be polled")


class UsageNormalizationTests(unittest.TestCase):
    def test_normalizes_google_style_usage_metadata(self):
        raw, input_tokens, output_tokens, total_tokens = normalize_usage(
            {
                "response": {
                    "usageMetadata": {
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 5,
                        "totalTokenCount": 15,
                    }
                }
            }
        )
        self.assertEqual(raw["totalTokenCount"], 15)
        self.assertEqual((input_tokens, output_tokens, total_tokens), (10, 5, 15))


class UsageMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_paid_gate_prevents_all_provider_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = SuccessfulCursorProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=False,
                usage_check_enabled=True,
                usage_check_providers=("cursor",),
                cursor_api_key="test-cursor",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={ProviderName.CURSOR: adapter})

            self.assertEqual(await monitor.run_once(force=True), [])
            self.assertEqual(adapter.calls, 0)
            self.assertEqual(store.latest_usage_checks(), [])

    async def test_prompts_due_provider_and_persists_usage_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = SuccessfulCursorProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_interval_minutes=360,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("cursor",),
                cursor_api_key="test-cursor",
                slack_bot_token="",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={ProviderName.CURSOR: adapter})

            first = await monitor.run_once()
            second = await monitor.run_once()

            self.assertEqual(adapter.calls, 1)
            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(first[0].status, UsageCheckStatus.OK)
            self.assertEqual(first[0].total_tokens, 20)
            self.assertIn("Do not browse", adapter.last_prompt)
            latest = store.latest_usage_check("cursor")
            self.assertIsNotNone(latest)
            self.assertEqual(latest.total_tokens, 20)

    async def test_forced_run_still_enforces_fifteen_minute_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = SuccessfulCursorProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("cursor",),
                cursor_api_key="test-cursor",
                slack_bot_token="",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={ProviderName.CURSOR: adapter})

            first = await monitor.run_once(force=True)
            second = await monitor.run_once(force=True)

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(adapter.calls, 1)

    async def test_provider_list_is_deduplicated_and_unknown_names_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = SuccessfulCursorProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("cursor", "unknown", "cursor"),
                cursor_api_key="test-cursor",
                slack_bot_token="",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={ProviderName.CURSOR: adapter})

            records = await monitor.run_once()

            self.assertEqual([record.provider for record in records], ["cursor"])
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(monitor.health()["providers"], ["cursor", "unknown"])
            self.assertEqual(monitor.health()["unsupported_providers"], ["unknown"])

    async def test_unconfigured_provider_is_recorded_as_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("perplexity",),
                perplexity_api_key="",
                slack_bot_token="",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})

            records = await monitor.run_once(force=True)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, UsageCheckStatus.SKIPPED)
            self.assertIn("not configured", records[0].error)


if __name__ == "__main__":
    unittest.main()
