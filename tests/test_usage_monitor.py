# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:38 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from director_orchestrator.config import Settings
from director_orchestrator.models import (
    ProviderName,
    ProviderRun,
    UsageCheckRecord,
    UsageCheckRunStatus,
    UsageCheckStatus,
)
from director_orchestrator.store import TaskStore
from director_orchestrator.providers import (
    GeminiAdapter,
    ProviderDailySpendLedger,
    ProviderError,
)
from director_orchestrator.usage import (
    UsageMonitor,
    normalize_reported_cost,
    normalize_usage,
)


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


class SequentialOpenAIUsageClient:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    async def __aenter__(self) -> "SequentialOpenAIUsageClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, *args: object, **kwargs: object) -> httpx.Response:
        self.calls += 1
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class QuotaFailingGeminiProbe:
    def __init__(self):
        self.calls = 0

    async def usage_check(self, prompt: str) -> ProviderRun:
        self.calls += 1
        raise ProviderError(
            "Gemini usage check failed with resource_exhausted after 3 bounded attempts",
            code="resource_exhausted",
            attempt_count=3,
        )


class SuccessfulPerplexityProbe:
    def __init__(self):
        self.calls = 0

    async def usage_check(self, prompt: str) -> ProviderRun:
        self.calls += 1
        return ProviderRun(
            provider=ProviderName.PERPLEXITY,
            run_id="perplexity-usage-1",
            status="completed",
            result="STATUS=OK",
            raw={
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 4,
                    "total_tokens": 24,
                    "cost": {"currency": "USD", "total_cost": 0.00019},
                }
            },
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        raise AssertionError("Terminal usage checks must not be polled")


class SuccessfulGrokProbe:
    def __init__(self):
        self.calls = 0

    async def usage_check(self, prompt: str) -> ProviderRun:
        self.calls += 1
        return ProviderRun(
            provider=ProviderName.GROK,
            run_id="grok-usage-1",
            status="completed",
            result="STATUS=OK; MODEL=grok-4.6; QUOTA=UNAVAILABLE",
            raw={"usage": {"input_tokens": 14, "output_tokens": 6}},
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

    def test_normalizes_only_provider_reported_usd_cost(self):
        self.assertEqual(
            normalize_reported_cost(
                {"cost": {"currency": "USD", "total_cost": 0.00019}}
            ),
            0.00019,
        )
        self.assertIsNone(
            normalize_reported_cost(
                {"cost": {"currency": "EUR", "total_cost": 0.00019}}
            )
        )
        self.assertIsNone(normalize_reported_cost({"total_tokens": 1_000}))


class UsageMonitorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _openai_response(
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, object] | None = None,
    ) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers=headers,
            json=payload,
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        )

    def test_openai_retry_delay_numeric_and_http_date_bounds(self):
        now = datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)
        cases = [
            (None, 2.0), ("0", 0.0), ("60", 60.0), ("61", None),
            ("-1", None), ("NaN", None), ("inf", None), ("broken", None),
            ("Mon, 07 Sep 2026 08:00:30 GMT", 30.0),
            ("Mon, 07 Sep 2026 08:01:00 GMT", 60.0),
            ("Mon, 07 Sep 2026 08:01:01 GMT", None),
            ("Mon, 07 Sep 2026 07:59:59 GMT", None),
        ]
        with patch("director_orchestrator.usage.datetime") as clock:
            clock.now.return_value = now
            for value, expected in cases:
                with self.subTest(value=value):
                    response = self._openai_response(
                        429, headers={} if value is None else {"Retry-After": value}
                    )
                    self.assertEqual(UsageMonitor._openai_usage_retry_delay(response), expected)

    async def test_openai_invalid_payload_after_retry_keeps_durable_accounting(self):
        for payload in ({"private": "must-not-leak"}, {"id": None},
                        {"id": "resp", "status": []}, []):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                settings = replace(
                    Settings(), allow_paid_runs=True, usage_check_enabled=True,
                    usage_check_provider_delay_seconds=0,
                    usage_check_providers=("openai",), openai_api_key="test-openai",
                    slack_bot_token="", sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                )
                monitor = UsageMonitor(settings, TaskStore(settings.sqlite_path), adapters={})
                client = SequentialOpenAIUsageClient([
                    self._openai_response(429, headers={"Retry-After": "0"}),
                    self._openai_response(200, payload=payload),
                ])
                with (
                    patch("director_orchestrator.usage.httpx.AsyncClient", return_value=client),
                    patch("director_orchestrator.usage.asyncio.sleep", new=AsyncMock()) as sleep,
                ):
                    records = await monitor.run_once(force=True)
                self.assertEqual(client.calls, 2)
                sleep.assert_awaited_once_with(0.0)
                self.assertEqual(records[0].status, UsageCheckStatus.ERROR)
                self.assertEqual(records[0].attempt_count, 2)
                self.assertEqual(records[0].provider_error_code, "invalid_response")
                self.assertNotIn("must-not-leak", str(records[0]))
                accounting = monitor.health()["daily_accounting"]["openai"]
                self.assertEqual(accounting["request_count"], 2)
                self.assertEqual(accounting["retry_count"], 1)

    async def test_openai_usage_check_recovers_once_from_short_rate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                openai_api_key="test-openai",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            client = SequentialOpenAIUsageClient(
                [
                    self._openai_response(429, headers={"Retry-After": "Mon, 07 Sep 2026 08:00:30 GMT"}),
                    self._openai_response(
                        200,
                        payload={
                            "id": "resp_recovered",
                            "status": "completed",
                            "output_text": "STATUS=OK",
                            "usage": {"input_tokens": 3, "output_tokens": 2},
                        },
                    ),
                ]
            )

            with (
                patch("director_orchestrator.usage.datetime") as clock,
                patch("director_orchestrator.usage.httpx.AsyncClient", return_value=client),
                patch(
                    "director_orchestrator.usage.asyncio.sleep",
                    new=AsyncMock(return_value=None),
                ) as sleep,
            ):
                clock.now.return_value = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
                run = await monitor._openai_usage_check()

            self.assertEqual(client.calls, 2)
            sleep.assert_awaited_once_with(30.0)
            self.assertEqual(run.attempt_count, 2)
            self.assertEqual(run.run_id, "resp_recovered")

    async def test_openai_rate_limit_is_recorded_after_one_bounded_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("openai",),
                openai_api_key="test-openai",
                slack_bot_token="",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            client = SequentialOpenAIUsageClient(
                [
                    self._openai_response(429, headers={"Retry-After": "0"}),
                    self._openai_response(429, headers={"Retry-After": "0"}),
                ]
            )

            with (
                patch("director_orchestrator.usage.httpx.AsyncClient", return_value=client),
                patch(
                    "director_orchestrator.usage.asyncio.sleep",
                    new=AsyncMock(return_value=None),
                ),
            ):
                records = await monitor.run_once(force=True)

            self.assertEqual(client.calls, 2)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, UsageCheckStatus.ERROR)
            self.assertEqual(records[0].attempt_count, 2)
            self.assertEqual(records[0].provider_error_code, "http_429")
            self.assertIsNone(records[0].response_text)
            accounting = monitor.health()["daily_accounting"]["openai"]
            self.assertEqual(accounting["request_count"], 2)
            self.assertEqual(accounting["retry_count"], 1)
            self.assertEqual(accounting["quota_failure_count"], 1)

    async def test_openai_does_not_retry_a_non_rate_limit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                openai_api_key="test-openai",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            client = SequentialOpenAIUsageClient([self._openai_response(503)])

            with patch("director_orchestrator.usage.httpx.AsyncClient", return_value=client):
                with self.assertRaises(ProviderError) as raised:
                    await monitor._openai_usage_check()

            self.assertEqual(client.calls, 1)
            self.assertEqual(raised.exception.code, "http_503")
            self.assertEqual(raised.exception.attempt_count, 1)

    async def test_openai_refuses_an_unbounded_rate_limit_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                openai_api_key="test-openai",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            client = SequentialOpenAIUsageClient(
                [self._openai_response(429, headers={"Retry-After": "61"})]
            )

            with patch("director_orchestrator.usage.httpx.AsyncClient", return_value=client):
                with self.assertRaises(ProviderError) as raised:
                    await monitor._openai_usage_check()

            self.assertEqual(client.calls, 1)
            self.assertEqual(raised.exception.code, "http_429")
            self.assertEqual(raised.exception.attempt_count, 1)

    async def test_openai_retry_transport_failure_preserves_second_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                openai_api_key="test-openai",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            client = SequentialOpenAIUsageClient(
                [
                    self._openai_response(429, headers={"Retry-After": "0"}),
                    httpx.ConnectTimeout("timed out"),
                ]
            )

            with (
                patch("director_orchestrator.usage.httpx.AsyncClient", return_value=client),
                patch(
                    "director_orchestrator.usage.asyncio.sleep",
                    new=AsyncMock(return_value=None),
                ),
            ):
                with self.assertRaises(ProviderError) as raised:
                    await monitor._openai_usage_check()

            self.assertEqual(client.calls, 2)
            self.assertEqual(raised.exception.code, "transport_error")
            self.assertEqual(raised.exception.attempt_count, 2)

    async def test_openai_retry_invalid_response_preserves_second_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                openai_api_key="test-openai",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            monitor = UsageMonitor(settings, store, adapters={})
            client = SequentialOpenAIUsageClient(
                [
                    self._openai_response(429, headers={"Retry-After": "0"}),
                    httpx.Response(
                        200,
                        content=b"not-json",
                        request=httpx.Request(
                            "POST", "https://api.openai.com/v1/responses"
                        ),
                    ),
                ]
            )

            with (
                patch("director_orchestrator.usage.httpx.AsyncClient", return_value=client),
                patch(
                    "director_orchestrator.usage.asyncio.sleep",
                    new=AsyncMock(return_value=None),
                ),
            ):
                with self.assertRaises(ProviderError) as raised:
                    await monitor._openai_usage_check()

            self.assertEqual(client.calls, 2)
            self.assertEqual(raised.exception.code, "invalid_response")
            self.assertEqual(raised.exception.attempt_count, 2)

    async def test_grok_is_a_bounded_supported_usage_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = SuccessfulGrokProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("grok",),
                grok_enabled=True,
                grok_api_key="test-xai",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                slack_bot_token="",
            )
            monitor = UsageMonitor(
                settings, store, adapters={ProviderName.GROK: adapter}
            )

            records = await monitor.run_once(force=True)

            self.assertEqual(adapter.calls, 1)
            self.assertEqual(records[0].provider, "grok")
            self.assertEqual(records[0].status, UsageCheckStatus.OK)
            self.assertEqual(records[0].total_tokens, 20)
            self.assertEqual(monitor.health()["unsupported_providers"], [])

    async def test_grok_probe_and_assignment_share_one_atomic_daily_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            store = TaskStore(database)
            ledger = ProviderDailySpendLedger(database)
            ledger.reserve(
                provider=ProviderName.GROK,
                effect_key="a" * 64,
                estimated_cost_usd=0.01,
                request_limit=1,
                budget_usd=1.0,
            )
            adapter = SuccessfulGrokProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("grok",),
                grok_enabled=True,
                grok_api_key="test-xai",
                grok_allow_review_verdict=False,
                grok_daily_request_limit=1,
                sqlite_path=database,
                slack_bot_token="",
            )
            monitor = UsageMonitor(
                settings, store, adapters={ProviderName.GROK: adapter}
            )

            records = await monitor.run_once(force=True)

            self.assertEqual(adapter.calls, 0)
            self.assertEqual(records[0].status, UsageCheckStatus.SKIPPED)
            self.assertIn("daily request limit", records[0].error or "")
            self.assertEqual(
                monitor.health()["daily_accounting"]["grok"]["request_count"],
                1,
            )

    async def test_quota_failure_attempts_and_freshness_are_visible_without_provider_text(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = QuotaFailingGeminiProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("gemini",),
                gemini_api_key="test-gemini",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                slack_bot_token="",
            )
            monitor = UsageMonitor(
                settings, store, adapters={ProviderName.GEMINI: adapter}
            )

            records = await monitor.run_once(force=True)
            health = monitor.health()

            self.assertEqual(records[0].status, UsageCheckStatus.ERROR)
            self.assertEqual(records[0].attempt_count, 3)
            self.assertEqual(records[0].provider_error_code, "resource_exhausted")
            self.assertNotIn("response_text", records[0].error or "")
            self.assertFalse(health["all_configured_have_recent_signal"])
            self.assertFalse(health["all_configured_completion_verified"])
            self.assertEqual(health["missing_or_stale_providers"], ["gemini"])
            self.assertEqual(health["daily_accounting"]["gemini"]["request_count"], 3)
            # The UTC day every daily_accounting entry covers, named by the monitor itself.
            self.assertRegex(health["accounting_day"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertLessEqual(
                abs((date.fromisoformat(health["accounting_day"]) - datetime.now(timezone.utc).date()).days), 1)
            self.assertEqual(health["daily_accounting"]["gemini"]["retry_count"], 2)
            self.assertEqual(
                health["daily_accounting"]["gemini"]["quota_failure_count"], 1
            )

    async def test_reported_cost_is_accounted_and_daily_request_limit_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            fixed_now = datetime.now(timezone.utc).replace(
                hour=12, minute=0, second=0, microsecond=0
            )
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            adapter = SuccessfulPerplexityProbe()
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("perplexity",),
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_daily_request_limit=1,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                slack_bot_token="",
            )
            monitor = UsageMonitor(
                settings, store, adapters={ProviderName.PERPLEXITY: adapter}
            )

            store.add_usage_check(
                UsageCheckRecord(
                    cycle_id="USAGE-STALE001",
                    provider="perplexity",
                    status=UsageCheckStatus.OK,
                    checked_at=fixed_now - timedelta(minutes=16),
                    input_tokens=20,
                    output_tokens=4,
                    total_tokens=24,
                    reported_cost_usd=0.00019,
                    raw_usage={
                        "cost": {"currency": "USD", "total_cost": 0.00019}
                    },
                )
            )
            with patch("director_orchestrator.usage.datetime") as usage_datetime:
                usage_datetime.now.return_value = fixed_now
                accounting = monitor.health()["daily_accounting"]["perplexity"]
                self.assertEqual(accounting["request_count"], 1)
                self.assertEqual(accounting["reported_cost_usd"], 0.00019)
                self.assertTrue(accounting["cost_visibility_complete"])
                limited = await monitor.run_once(force=True)
            self.assertEqual(limited[0].status, UsageCheckStatus.SKIPPED)
            self.assertEqual(limited[0].attempt_count, 0)
            self.assertIn("daily request limit", limited[0].error or "")
            self.assertEqual(adapter.calls, 0)

    async def test_gemini_retries_cannot_cross_remaining_daily_request_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            fixed_now = datetime.now(timezone.utc).replace(
                hour=12, minute=0, second=0, microsecond=0
            )
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("gemini",),
                gemini_api_key="test-gemini",
                gemini_usage_max_attempts=3,
                gemini_daily_request_limit=3,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                slack_bot_token="",
            )
            store.add_usage_check(
                UsageCheckRecord(
                    cycle_id="USAGE-EARLIER",
                    provider="gemini",
                    status=UsageCheckStatus.ERROR,
                    checked_at=fixed_now - timedelta(minutes=16),
                    attempt_count=2,
                    provider_error_code="http_429",
                    error="Earlier bounded quota response",
                )
            )
            outbound_calls = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal outbound_calls
                outbound_calls += 1
                return httpx.Response(
                    429,
                    request=request,
                    json={"error": {"status": "RESOURCE_EXHAUSTED"}},
                )

            transport = httpx.MockTransport(handler)
            monitor = UsageMonitor(
                settings,
                store,
                adapters={ProviderName.GEMINI: GeminiAdapter(settings)},
            )
            with (
                patch("director_orchestrator.usage.datetime") as usage_datetime,
                patch(
                    "director_orchestrator.providers.httpx.AsyncClient",
                    return_value=httpx.AsyncClient(transport=transport),
                ),
            ):
                usage_datetime.now.return_value = fixed_now
                records = await monitor.run_once(force=True)

            self.assertEqual(outbound_calls, 1)
            self.assertEqual(records[0].status, UsageCheckStatus.ERROR)
            self.assertEqual(records[0].attempt_count, 1)
            self.assertEqual(
                monitor.health()["daily_accounting"]["gemini"]["request_count"],
                settings.gemini_daily_request_limit,
            )

    async def test_gemini_transport_failure_preserves_all_attempted_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                usage_check_enabled=True,
                usage_check_provider_delay_seconds=0,
                usage_check_providers=("gemini",),
                gemini_api_key="test-gemini",
                gemini_usage_max_attempts=3,
                gemini_daily_request_limit=3,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                slack_bot_token="",
            )
            outbound_calls = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal outbound_calls
                outbound_calls += 1
                if outbound_calls == 1:
                    return httpx.Response(
                        429,
                        request=request,
                        json={"error": {"status": "RESOURCE_EXHAUSTED"}},
                    )
                raise httpx.ReadTimeout(
                    "second Gemini attempt lost its response", request=request
                )

            transport = httpx.MockTransport(handler)
            monitor = UsageMonitor(
                settings,
                store,
                adapters={ProviderName.GEMINI: GeminiAdapter(settings)},
            )
            with (
                patch(
                    "director_orchestrator.providers.httpx.AsyncClient",
                    return_value=httpx.AsyncClient(transport=transport),
                ),
                patch("director_orchestrator.providers.asyncio.sleep"),
            ):
                records = await monitor.run_once(force=True)

            self.assertEqual(outbound_calls, 2)
            self.assertEqual(records[0].status, UsageCheckStatus.ERROR)
            self.assertEqual(records[0].attempt_count, 2)
            self.assertEqual(records[0].provider_error_code, "transport_error")
            self.assertEqual(
                monitor.health()["daily_accounting"]["gemini"]["request_count"],
                2,
            )

    async def test_health_distinguishes_claude_prompt_from_completed_response(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            now = datetime.now(timezone.utc)
            store.add_usage_check(
                UsageCheckRecord(
                    cycle_id="USAGE-HEALTH01",
                    provider="claude",
                    status=UsageCheckStatus.PROMPTED,
                    checked_at=now,
                )
            )
            store.add_usage_check(
                UsageCheckRecord(
                    cycle_id="USAGE-HEALTH01",
                    provider="cursor",
                    status=UsageCheckStatus.OK,
                    checked_at=now,
                )
            )
            monitor = UsageMonitor(
                replace(
                    Settings(),
                    allow_paid_runs=True,
                    usage_check_enabled=True,
                    usage_check_providers=("claude", "cursor"),
                    sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                ),
                store,
                adapters={},
            )

            health = monitor.health()
            self.assertTrue(health["all_configured_have_recent_signal"])
            self.assertFalse(health["all_configured_completion_verified"])
            self.assertEqual(health["pending_completion_providers"], ["claude"])
            self.assertEqual(health["completion_verified_providers"], ["cursor"])
            latest = {item["provider"]: item for item in health["latest"]}
            self.assertFalse(latest["claude"]["completion_verified"])
            self.assertTrue(latest["cursor"]["completion_verified"])
    async def test_interactions_budget_exceeded_fails_closed_without_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            monitor = UsageMonitor(
                replace(Settings(), sqlite_path=str(Path(directory) / "tasks.sqlite3")),
                store,
                adapters={},
            )
            record = monitor._record_from_run(
                "USAGE-BUDGET",
                "gemini",
                ProviderRun(
                    provider=ProviderName.GEMINI,
                    run_id="interaction-budget",
                    status="budget_exceeded",
                ),
            )
            self.assertEqual(record.status, UsageCheckStatus.ERROR)
            self.assertIn("budget_exceeded", record.error)

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
            self.assertFalse(monitor.health()["all_configured_have_recent_signal"])
            self.assertFalse(monitor.health()["all_configured_completion_verified"])

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
            adapter = SuccessfulCursorProbe()
            monitor = UsageMonitor(settings, store, adapters={"cursor": adapter})
            first = await monitor.reserve_manual_cycle("USAGE-RESERVE001")
            second = await monitor.reserve_manual_cycle("USAGE-RESERVE002")

            self.assertEqual(first.status, UsageCheckRunStatus.ACCEPTED)
            self.assertEqual(second.status, UsageCheckRunStatus.BUSY)
            self.assertEqual(second.scheduled_providers, [])

            # The reserved owner can still run; a concurrent unreserved pass cannot.
            skipped = await monitor.run_once(force=True)
            self.assertEqual(skipped, [])
            with patch("httpx.AsyncClient.send", side_effect=AssertionError("unexpected network request")):
                records = await monitor.run_once(force=True, cycle_id="USAGE-RESERVE001")
            self.assertEqual([record.cycle_id for record in records], ["USAGE-RESERVE001"])
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(records[0].status, UsageCheckStatus.OK)
            self.assertEqual(records[0].provider, ProviderName.CURSOR)

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
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 08:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:14 UTC | END]]
