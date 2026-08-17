from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.models import (
    ProviderName,
    ProviderRun,
    UsageCheckRecord,
    UsageCheckRunStatus,
    UsageCheckStatus,
)
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
    async def test_unknown_provider_terminal_status_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            monitor = UsageMonitor(
                replace(Settings(), sqlite_path=str(Path(directory) / "tasks.sqlite3")),
                store,
                adapters={},
            )
            record = monitor._record_from_run(
                "USAGE-UNKNOWN", "cursor", ProviderRun(
                    provider=ProviderName.CURSOR,
                    run_id="unknown-run",
                    status="UNRECOGNIZED",
                )
            )
            self.assertEqual(record.status, UsageCheckStatus.ERROR)
            self.assertIn("UNRECOGNIZED", record.error)

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

    async def test_caller_supplied_cycle_id_binds_all_persisted_results(self):
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

            records = await monitor.run_once(force=True, cycle_id="USAGE-1234567890")

            self.assertEqual([record.cycle_id for record in records], ["USAGE-1234567890"])
            self.assertEqual(store.latest_usage_check("cursor").cycle_id, "USAGE-1234567890")

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

    async def test_legacy_force_accept_path_yields_no_records_while_cooling_down(self):
        """Reproduce the pre-fix defect: force=True still enforces 15 minutes.

        The old API accepted a cycle_id and scheduled run_once(force=True) even when
        every provider was cooling down, so callers waited for impossible records.
        """
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
            first = await monitor.run_once(force=True, cycle_id="USAGE-OLDACCEPT1")
            second = await monitor.run_once(force=True, cycle_id="USAGE-OLDACCEPT2")

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(adapter.calls, 1)
            latest = store.latest_usage_check("cursor")
            self.assertEqual(latest.cycle_id, "USAGE-OLDACCEPT1")
            self.assertNotEqual(latest.cycle_id, "USAGE-OLDACCEPT2")

    async def test_reserve_manual_cycle_due_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("cursor", "openai", "claude"),
                cursor_api_key="",
                openai_api_key="",
                claude_routine_url="",
                claude_routine_token="",
                slack_bot_token="",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            plan = await monitor.reserve_manual_cycle("USAGE-DUE0000001")

            self.assertEqual(plan.status, UsageCheckRunStatus.ACCEPTED)
            self.assertEqual(plan.cycle_id, "USAGE-DUE0000001")
            self.assertEqual(plan.scheduled_providers, ["cursor", "openai", "claude"])
            self.assertEqual(plan.cooling_down_providers, [])
            self.assertEqual(plan.minimum_interval_minutes, 15)

            records = await monitor.run_once(force=True, cycle_id="USAGE-DUE0000001")
            self.assertEqual({record.provider for record in records}, {"cursor", "openai", "claude"})
            self.assertTrue(all(record.status == UsageCheckStatus.SKIPPED for record in records))

    async def test_reserve_manual_cycle_all_providers_cooling_down(self):
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
            await monitor.run_once(force=True, cycle_id="USAGE-SEED000001")

            plan = await monitor.reserve_manual_cycle("USAGE-COOLDOWN01")
            self.assertEqual(plan.status, UsageCheckRunStatus.NOT_DUE)
            self.assertIsNone(plan.cycle_id)
            self.assertEqual(plan.scheduled_providers, [])
            self.assertEqual(plan.cooling_down_providers, ["cursor"])
            self.assertIn("15-minute", plan.reason or "")

            # No reservation means a follow-up forced run still creates no records.
            self.assertEqual(await monitor.run_once(force=True, cycle_id="USAGE-COOLDOWN01"), [])
            self.assertEqual(adapter.calls, 1)

    async def test_reserve_manual_cycle_partially_due_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = SuccessfulCursorProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("cursor", "openai"),
                cursor_api_key="test-cursor",
                openai_api_key="",
                slack_bot_token="",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={ProviderName.CURSOR: adapter})
            store.add_usage_check(
                UsageCheckRecord(
                    cycle_id="USAGE-PARTIAL001",
                    provider="cursor",
                    status=UsageCheckStatus.OK,
                    response_text="seeded cooldown",
                )
            )

            plan = await monitor.reserve_manual_cycle("USAGE-PARTIAL002")
            self.assertEqual(plan.status, UsageCheckRunStatus.ACCEPTED)
            self.assertEqual(plan.scheduled_providers, ["openai"])
            self.assertEqual(plan.cooling_down_providers, ["cursor"])

            records = await monitor.run_once(force=True, cycle_id="USAGE-PARTIAL002")
            self.assertEqual([record.provider for record in records], ["openai"])
            self.assertEqual(records[0].status, UsageCheckStatus.SKIPPED)
            self.assertEqual(adapter.calls, 0)

    async def test_reserve_manual_cycle_busy_when_lock_held(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_providers=("cursor",),
                cursor_api_key="test-cursor",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            await monitor._lock.acquire()
            try:
                plan = await monitor.reserve_manual_cycle("USAGE-BUSY000001")
            finally:
                monitor._lock.release()

            self.assertEqual(plan.status, UsageCheckRunStatus.BUSY)
            self.assertEqual(plan.scheduled_providers, [])
            self.assertIn("already running", plan.reason or "")

    async def test_reserve_manual_cycle_busy_when_already_reserved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_providers=("cursor",),
                cursor_api_key="test-cursor",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            first = await monitor.reserve_manual_cycle("USAGE-RESERVE001")
            second = await monitor.reserve_manual_cycle("USAGE-RESERVE002")

            self.assertEqual(first.status, UsageCheckRunStatus.ACCEPTED)
            self.assertEqual(second.status, UsageCheckRunStatus.BUSY)
            self.assertEqual(second.scheduled_providers, [])

            # The reserved owner can still run; a concurrent unreserved pass cannot.
            skipped = await monitor.run_once(force=True)
            self.assertEqual(skipped, [])
            records = await monitor.run_once(force=True, cycle_id="USAGE-RESERVE001")
            self.assertEqual([record.cycle_id for record in records], ["USAGE-RESERVE001"])

    async def test_minimum_interval_cannot_be_bypassed_by_reservation(self):
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
            await monitor.run_once(force=True)
            # Forge a stale reservation that claims a cooling-down provider.
            from director_orchestrator.usage import _ManualCycleReservation

            monitor._reserved = _ManualCycleReservation(
                cycle_id="USAGE-FORGE00001", providers=("cursor",)
            )
            records = await monitor.run_once(force=True, cycle_id="USAGE-FORGE00001")
            self.assertEqual(records, [])
            self.assertEqual(adapter.calls, 1)


if __name__ == "__main__":
    unittest.main()
