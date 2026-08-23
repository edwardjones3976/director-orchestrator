# [[Codex | GPT-5.6 | 2026-08-23 08:12 UTC | BEGIN]]
from __future__ import annotations

import importlib
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from director_orchestrator.chatgpt_bridge import (
    BridgeRejected,
    ChatGPTBridgeRequest,
    ChatGPTBridgeService,
    ChatGPTBridgeStore,
)
from director_orchestrator.config import Settings


class RecordingAsyncClient:
    calls: list[dict] = []
    responses: list[dict | Exception] = []

    def __init__(self, **_: object):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json=result)


def _request(*, event_id: str = "event-1", channel_id: str = "C123"):
    return ChatGPTBridgeRequest(
        source_event_id=event_id,
        channel_id=channel_id,
        thread_ts="1787450000.123456",
        user_id="UOWNER123",
        prompt="Summarize the current discussion.",
    )


def _settings(database: str, **changes) -> Settings:
    settings = replace(
        Settings(),
        sqlite_path=database,
        allow_paid_runs=True,
        openai_api_key="test-openai-key",
        slack_owner_user_id="UOWNER123",
        chatgpt_bridge_enabled=True,
        chatgpt_bridge_channel_ids=("C123", "C456"),
        chatgpt_bridge_estimated_cost_usd=0.25,
        chatgpt_bridge_daily_budget_usd=1.0,
        chatgpt_bridge_requests_per_minute=4,
        shared_secret="test-internal-bearer",
        slack_app_token="xapp-test",
        slack_bot_token="xoxb-test",
        slack_gateway_secret="distinct-gateway-secret-32-characters",
    )
    return replace(settings, **changes)


class ChatGPTBridgeServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        RecordingAsyncClient.calls = []
        RecordingAsyncClient.responses = []

    async def test_single_dispatch_is_bounded_and_replay_does_not_redeliver(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            service = ChatGPTBridgeService(
                _settings(database), ChatGPTBridgeStore(database)
            )
            RecordingAsyncClient.responses = [{"output_text": "Bounded answer"}]

            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                first = await service.reply(_request())
                replay = await service.reply(_request())

            self.assertEqual(first.answer, "Bounded answer")
            self.assertTrue(first.deliver)
            self.assertFalse(first.cached)
            self.assertEqual(replay.answer, "")
            self.assertFalse(replay.deliver)
            self.assertTrue(replay.cached)
            self.assertEqual(len(RecordingAsyncClient.calls), 1)
            provider_request = RecordingAsyncClient.calls[0]
            self.assertEqual(provider_request["url"], "https://api.openai.com/v1/responses")
            self.assertFalse(provider_request["json"]["store"])
            self.assertNotIn("tools", provider_request["json"])
            self.assertEqual(provider_request["json"]["max_output_tokens"], 600)
            self.assertNotIn("test-openai-key", str(provider_request["json"]))

            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT status, request_digest FROM chatgpt_bridge_requests"
                ).fetchone()
                columns = {
                    item[1]
                    for item in connection.execute(
                        "PRAGMA table_info(chatgpt_bridge_requests)"
                    ).fetchall()
                }
            finally:
                connection.close()
            self.assertEqual(row[0], "completed")
            self.assertEqual(len(row[1]), 64)
            self.assertNotIn("prompt", columns)
            self.assertNotIn("answer", columns)

    async def test_owner_channel_and_paid_gates_fail_before_provider_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            cases = (
                (_settings(database, allow_paid_runs=False), _request(), "paid"),
                (
                    _settings(database),
                    _request().model_copy(update={"user_id": "UOTHER123"}),
                    "owner",
                ),
                (_settings(database), _request(channel_id="C999"), "channel"),
            )
            for settings, request, reason in cases:
                with self.subTest(reason=reason):
                    service = ChatGPTBridgeService(
                        settings, ChatGPTBridgeStore(database)
                    )
                    with self.assertRaises(BridgeRejected):
                        await service.reply(request)
            self.assertEqual(RecordingAsyncClient.calls, [])

    def test_readiness_exposes_only_boolean_configuration_state(self):
        settings = _settings("unused.sqlite3")
        readiness = settings.chatgpt_bridge_readiness()

        self.assertTrue(all(readiness.values()))
        self.assertTrue(all(isinstance(value, bool) for value in readiness.values()))
        self.assertNotIn("test-openai-key", str(readiness))

    async def test_rate_limit_and_daily_budget_are_enforced_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(
                database,
                chatgpt_bridge_requests_per_minute=2,
                chatgpt_bridge_daily_budget_usd=0.5,
            )
            service = ChatGPTBridgeService(settings, ChatGPTBridgeStore(database))
            RecordingAsyncClient.responses = [
                {"output_text": "one"},
                {"output_text": "two"},
            ]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                await service.reply(_request(event_id="event-1"))
                await service.reply(_request(event_id="event-2"))
                with self.assertRaisesRegex(BridgeRejected, "rate limit|budget"):
                    await service.reply(_request(event_id="event-3"))
            self.assertEqual(len(RecordingAsyncClient.calls), 2)

    async def test_failed_or_identity_conflicting_events_never_retry_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            service = ChatGPTBridgeService(
                _settings(database), ChatGPTBridgeStore(database)
            )
            RecordingAsyncClient.responses = [httpx.TimeoutException("timeout")]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                with self.assertRaises(httpx.TimeoutException):
                    await service.reply(_request())
                replay = await service.reply(_request())
                changed = _request().model_copy(update={"prompt": "Different prompt"})
                with self.assertRaisesRegex(BridgeRejected, "identity conflict"):
                    await service.reply(changed)
            self.assertFalse(replay.deliver)
            self.assertTrue(replay.cached)
            self.assertEqual(len(RecordingAsyncClient.calls), 1)


class _FakeBoltApp:
    def __init__(self, **_: object):
        pass

    def event(self, _: str):
        return lambda function: function

    def command(self, _: str):
        return lambda function: function

    def action(self, _: str):
        return lambda function: function


class _SlackResponse:
    def __init__(self, body: dict):
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.body


class _SlackClient:
    def __init__(self):
        self.messages: list[dict] = []

    def chat_postMessage(self, **payload):
        self.messages.append(payload)
        return {"ts": "1787450000.654321"}


class ChatGPTSlackRoutingTests(unittest.TestCase):
    def test_exact_chatgpt_mention_routes_to_bridge_and_posts_in_thread(self):
        import director_orchestrator.config as config_module
        import slack_bolt

        settings = _settings("unused.sqlite3", shared_secret="bridge-bearer")
        settings = replace(
            settings,
            slack_bot_token="xoxb-test",
            slack_app_token="xapp-test",
            slack_gateway_secret="distinct-gateway-secret-32-characters",
            chatgpt_bridge_slack_user_id="U0B9KFJPST1",
            orchestrator_base_url="http://orchestrator:8080",
        )
        module_name = "director_orchestrator.slack_bot"
        sys.modules.pop(module_name, None)
        try:
            with (
                patch.object(config_module, "Settings", return_value=settings),
                patch.object(slack_bolt, "App", _FakeBoltApp),
            ):
                slack_bot = importlib.import_module(module_name)

            client = _SlackClient()
            with patch.object(
                slack_bot.httpx,
                "post",
                return_value=_SlackResponse(
                    {
                        "source_event_id": "event-1",
                        "answer": "Hello from the bounded bridge.",
                        "cached": False,
                        "deliver": True,
                    }
                ),
            ) as post:
                slack_bot.message_handler(
                    {
                        "client_msg_id": "event-1",
                        "channel": "C123",
                        "user": "UOWNER123",
                        "text": "<@U0B9KFJPST1> hello",
                        "ts": "1787450000.123456",
                    },
                    client,
                )

            request = post.call_args.kwargs
            self.assertEqual(request["json"]["prompt"], "hello")
            self.assertEqual(
                request["headers"]["Authorization"], "Bearer bridge-bearer"
            )
            self.assertTrue(post.call_args.args[0].endswith("/v1/chatgpt-bridge"))
            self.assertEqual(client.messages[0]["thread_ts"], "1787450000.123456")
            self.assertIn("Aesop Director — ChatGPT bridge", client.messages[0]["text"])

            with patch.object(
                slack_bot.httpx,
                "post",
                return_value=_SlackResponse(
                    {
                        "source_event_id": "event-unsafe-output",
                        "answer": (
                            "<!channel> <@UVICTIM123> "
                            "<https://malicious.example|click> & text"
                        ),
                        "cached": False,
                        "deliver": True,
                    }
                ),
            ):
                slack_bot.message_handler(
                    {
                        "client_msg_id": "event-unsafe-output",
                        "channel": "C123",
                        "user": "UOWNER123",
                        "text": "<@U0B9KFJPST1> render safely",
                        "ts": "1787450000.223456",
                    },
                    client,
                )
            safe_text = client.messages[-1]["text"]
            self.assertNotIn("<!channel>", safe_text)
            self.assertNotIn("<@UVICTIM123>", safe_text)
            self.assertNotIn("<https://malicious.example|click>", safe_text)
            self.assertIn("&lt;!channel&gt;", safe_text)
            self.assertIn("&lt;@UVICTIM123&gt;", safe_text)
            self.assertIn("&amp; text", safe_text)

            for event in (
                {
                    "client_msg_id": "event-2",
                    "channel": "C123",
                    "user": "UOTHER123",
                    "text": "<@U0B9KFJPST1> owner only",
                    "ts": "1787450001.123456",
                },
                {
                    "client_msg_id": "event-3",
                    "channel": "C999",
                    "user": "UOWNER123",
                    "text": "<@U0B9KFJPST1> wrong channel",
                    "ts": "1787450002.123456",
                },
                {
                    "client_msg_id": "event-4",
                    "channel": "C123",
                    "user": "UOWNER123",
                    "text": "<@UNRELATED> not the alias",
                    "ts": "1787450003.123456",
                },
            ):
                slack_bot.message_handler(event, client)
            self.assertEqual(post.call_count, 1)
        finally:
            sys.modules.pop(module_name, None)


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-23 08:12 UTC | END]]
