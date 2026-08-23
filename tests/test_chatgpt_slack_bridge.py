# [[Codex | GPT-5.6 | 2026-08-23 08:12 UTC | BEGIN]]
from __future__ import annotations

import asyncio
import importlib
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime as RealDateTime
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from director_orchestrator.chatgpt_bridge import (
    BridgeRejected,
    ChatGPTBridgeDeliveryAck,
    ChatGPTBridgeGatewayProof,
    ChatGPTBridgeRequest,
    ChatGPTBridgeService,
    ChatGPTBridgeStore,
    build_chatgpt_bridge_delivery_ack_proof,
    build_chatgpt_bridge_gateway_proof,
)
from director_orchestrator.config import Settings


GATEWAY_SECRET = "distinct-gateway-secret-32-characters"
SHARED_SECRET = "test-internal-bearer"
PROMPT = "Summarize the current discussion."


class RecordingAsyncClient:
    calls: list[dict] = []
    responses: list[dict | Exception] = []
    delay_seconds: float = 0

    def __init__(self, **_: object):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json=result)


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
        shared_secret=SHARED_SECRET,
        slack_app_token="xapp-test",
        slack_bot_token="xoxb-test",
        slack_gateway_secret=GATEWAY_SECRET,
    )
    return replace(settings, **changes)


def _store(database: str, settings: Settings | None = None) -> ChatGPTBridgeStore:
    configured = settings or _settings(database)
    return ChatGPTBridgeStore(
        database,
        gateway_secret=configured.slack_gateway_secret,
        shared_secret=configured.shared_secret,
    )


def _signed_request(
    settings: Settings,
    *,
    action: str = "request",
    event_ts: str = "1787450000.123456",
    thread_ts: str = "1787450000.123456",
    channel_id: str = "C123",
    user_id: str = "UOWNER123",
    prompt: str = PROMPT,
    approval_reference: str = "",
    issued_at: int | None = None,
) -> ChatGPTBridgeRequest:
    unsigned = ChatGPTBridgeRequest(
        action=action,
        source_event_id=f"{channel_id}:{event_ts}",
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_id=user_id,
        prompt=prompt,
        approval_reference=approval_reference,
        gateway_proof=ChatGPTBridgeGatewayProof(
            issued_at=1,
            nonce="N" * 32,
            signature="0" * 64,
        ),
    )
    proof = build_chatgpt_bridge_gateway_proof(
        settings.slack_gateway_secret,
        settings.shared_secret,
        unsigned,
        issued_at=int(time.time()) if issued_at is None else issued_at,
        nonce="R" * 32,
    )
    return unsigned.model_copy(update={"gateway_proof": proof})


def _approval(
    settings: Settings,
    request_reference: str,
    *,
    event_ts: str = "1787450001.123456",
    prompt: str = PROMPT,
    thread_ts: str = "1787450000.123456",
    issued_at: int | None = None,
) -> ChatGPTBridgeRequest:
    return _signed_request(
        settings,
        action="approve",
        event_ts=event_ts,
        thread_ts=thread_ts,
        prompt=prompt,
        approval_reference=request_reference,
        issued_at=issued_at,
    )


def _signed_ack(
    settings: Settings,
    approval: ChatGPTBridgeRequest,
    response,
    *,
    slack_message_ts: str = "1787450002.123456",
) -> ChatGPTBridgeDeliveryAck:
    unsigned = ChatGPTBridgeDeliveryAck(
        source_event_id=approval.source_event_id,
        request_reference=response.request_reference,
        channel_id=approval.channel_id,
        thread_ts=approval.thread_ts,
        user_id=approval.user_id,
        prompt_digest=response.prompt_digest,
        delivery_client_msg_id=response.delivery_client_msg_id,
        slack_message_ts=slack_message_ts,
        gateway_proof=ChatGPTBridgeGatewayProof(
            issued_at=1,
            nonce="N" * 32,
            signature="0" * 64,
        ),
    )
    proof = build_chatgpt_bridge_delivery_ack_proof(
        settings.slack_gateway_secret,
        settings.shared_secret,
        unsigned,
        issued_at=int(time.time()),
        nonce="A" * 32,
    )
    return unsigned.model_copy(update={"gateway_proof": proof})


class ChatGPTBridgeServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        RecordingAsyncClient.calls = []
        RecordingAsyncClient.responses = []
        RecordingAsyncClient.delay_seconds = 0

    async def test_initial_message_stops_at_owner_decision_without_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            service = ChatGPTBridgeService(settings, _store(database, settings))

            first = await service.reply(_signed_request(settings))
            replay = await service.reply(_signed_request(settings))

            self.assertEqual(first.state, "OWNER_DECISION_REQUIRED")
            self.assertTrue(first.deliver)
            self.assertIn(first.request_reference, first.approval_instruction)
            self.assertRegex(first.request_reference, r"^CBR-[A-F0-9]{24}$")
            self.assertTrue(replay.cached)
            self.assertEqual(replay.request_reference, first.request_reference)
            self.assertEqual(RecordingAsyncClient.calls, [])
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT status, decision_state, estimated_cost_usd "
                    "FROM chatgpt_bridge_requests"
                ).fetchone()
            self.assertEqual(row, ("pending", "OWNER_DECISION_REQUIRED", 0.0))
            self.assertNotIn(PROMPT.encode(), Path(database).read_bytes())

    async def test_matching_approval_dispatches_once_and_duplicate_recovers_ciphertext(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            service = ChatGPTBridgeService(settings, _store(database, settings))
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            RecordingAsyncClient.responses = [{"output_text": "Encrypted answer"}]

            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                first = await service.reply(approval)
                same_event = await service.reply(approval)
                new_event = await service.reply(
                    _approval(
                        settings,
                        pending.request_reference,
                        event_ts="1787450003.123456",
                    )
                )

            self.assertEqual(first.state, "ANSWER_READY")
            self.assertEqual(first.answer, "Encrypted answer")
            self.assertFalse(first.cached)
            self.assertEqual(same_event.answer, "Encrypted answer")
            self.assertTrue(same_event.cached)
            self.assertEqual(new_event.answer, "Encrypted answer")
            self.assertEqual(len(RecordingAsyncClient.calls), 1)
            self.assertEqual(first.delivery_client_msg_id, same_event.delivery_client_msg_id)
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT answer_nonce, answer_ciphertext, decision_state "
                    "FROM chatgpt_bridge_requests"
                ).fetchone()
                columns = {
                    item[1]
                    for item in connection.execute(
                        "PRAGMA table_info(chatgpt_bridge_requests)"
                    ).fetchall()
                }
            self.assertEqual(row[2], "ANSWER_READY")
            self.assertTrue(row[0])
            self.assertTrue(row[1])
            self.assertNotIn("prompt", columns)
            self.assertNotIn("answer", columns)
            database_bytes = Path(database).read_bytes()
            self.assertNotIn(PROMPT.encode(), database_bytes)
            self.assertNotIn(b"Encrypted answer", database_bytes)

    async def test_mismatched_tampered_and_stale_approvals_fail_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            service = ChatGPTBridgeService(settings, _store(database, settings))
            pending = await service.reply(_signed_request(settings))
            mismatch = _approval(
                settings, pending.request_reference, prompt="Different prompt"
            )
            valid = _approval(settings, pending.request_reference)
            tampered = valid.model_copy(update={"channel_id": "C456"})
            stale = _approval(
                settings,
                pending.request_reference,
                event_ts="1787450004.123456",
                issued_at=int(time.time()) - 301,
            )

            for candidate, reason in (
                (mismatch, "does not match"),
                (tampered, "invalid"),
                (stale, "stale"),
            ):
                with self.subTest(reason=reason):
                    with self.assertRaisesRegex(BridgeRejected, reason):
                        await service.reply(candidate)
            self.assertEqual(RecordingAsyncClient.calls, [])

    async def test_concurrent_double_approval_dispatches_provider_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            service = ChatGPTBridgeService(settings, _store(database, settings))
            pending = await service.reply(_signed_request(settings))
            RecordingAsyncClient.responses = [{"output_text": "one paid result"}]
            RecordingAsyncClient.delay_seconds = 0.05
            approvals = (
                _approval(settings, pending.request_reference),
                _approval(
                    settings,
                    pending.request_reference,
                    event_ts="1787450002.123456",
                ),
            )
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                results = await asyncio.gather(
                    *(service.reply(item) for item in approvals)
                )
            self.assertEqual(len(RecordingAsyncClient.calls), 1)
            self.assertEqual(
                {item.state for item in results}, {"ANSWER_READY", "DISPATCHING"}
            )
            self.assertEqual(sum(item.deliver for item in results), 1)
            recovered = await service.reply(approvals[1])
            self.assertEqual(recovered.state, "ANSWER_READY")
            self.assertTrue(recovered.cached)
            self.assertEqual(len(RecordingAsyncClient.calls), 1)

    async def test_ack_wipes_ciphertext_and_delivered_replay_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            service = ChatGPTBridgeService(settings, _store(database, settings))
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            RecordingAsyncClient.responses = [{"output_text": "wipe me"}]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                answer = await service.reply(approval)
            ack = _signed_ack(settings, approval, answer)

            first_ack = service.acknowledge_delivery(ack)
            replay_ack = service.acknowledge_delivery(ack)
            replay_approval = await service.reply(approval)

            self.assertFalse(first_ack.cached)
            self.assertTrue(replay_ack.cached)
            self.assertEqual(replay_approval.state, "DELIVERED")
            self.assertFalse(replay_approval.deliver)
            self.assertEqual(len(RecordingAsyncClient.calls), 1)
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT decision_state, answer_nonce, answer_ciphertext, "
                    "answer_expires_at FROM chatgpt_bridge_requests"
                ).fetchone()
            self.assertEqual(row, ("DELIVERED", None, None, None))

    async def test_provider_failure_is_terminal_and_never_redispatched(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            service = ChatGPTBridgeService(settings, _store(database, settings))
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            RecordingAsyncClient.responses = [httpx.TimeoutException("ambiguous")]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                with self.assertRaises(httpx.TimeoutException):
                    await service.reply(approval)
                replay = await service.reply(approval)
            self.assertEqual(replay.state, "FAILED")
            self.assertFalse(replay.deliver)
            self.assertEqual(len(RecordingAsyncClient.calls), 1)

    def test_af5013a_schema_migrates_additively_and_fails_legacy_rows_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE chatgpt_bridge_requests (
                        source_event_id TEXT PRIMARY KEY,
                        request_digest TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        thread_ts TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
                        estimated_cost_usd REAL NOT NULL,
                        created_at REAL NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO chatgpt_bridge_requests VALUES "
                    "('C123:1.1', ?, 'C123', 'UOWNER123', '1.1', 'pending', 0.25, 1, NULL)",
                    ("a" * 64,),
                )

            _store(database)

            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT status, decision_state, request_reference, answer_ciphertext "
                    "FROM chatgpt_bridge_requests"
                ).fetchone()
                columns = {
                    item[1]
                    for item in connection.execute(
                        "PRAGMA table_info(chatgpt_bridge_requests)"
                    ).fetchall()
                }
            self.assertEqual(row, ("failed", "FAILED", "", None))
            self.assertIn("approval_source_event_id", columns)
            self.assertIn("answer_ciphertext", columns)

    def test_daily_boundary_readiness_and_boolean_health_contracts(self):
        class ControlledDateTime:
            @classmethod
            def fromtimestamp(cls, value, zone):
                return RealDateTime.fromtimestamp(value, zone)

            @classmethod
            def now(cls, *_args, **_kwargs):
                return RealDateTime.now(*_args, **_kwargs)

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            store = _store(database, settings)
            pending = store.reserve_owner_decision(
                _signed_request(settings), requests_per_minute=4
            )
            approval = _approval(settings, pending.request_reference)
            with (
                patch(
                    "director_orchestrator.chatgpt_bridge.time.time",
                    return_value=1_787_529_599.9,
                ),
                patch(
                    "director_orchestrator.chatgpt_bridge.datetime",
                    ControlledDateTime,
                ),
            ):
                outcome = store.approve(
                    approval, estimated_cost_usd=0.25, daily_budget_usd=1.0
                )
            self.assertTrue(outcome.dispatch)

        readiness = settings.chatgpt_bridge_readiness()
        self.assertTrue(all(readiness.values()))
        self.assertTrue(all(isinstance(value, bool) for value in readiness.values()))
        self.assertFalse(
            _settings("unused", slack_owner_user_id="invalid")
            .chatgpt_bridge_readiness()["owner_configured"]
        )
        self.assertFalse(
            _settings(
                "unused",
                chatgpt_bridge_estimated_cost_usd=2.0,
                chatgpt_bridge_daily_budget_usd=1.0,
            ).chatgpt_bridge_readiness()["cost_estimate_valid"]
        )


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
    def __init__(self, *, fail_posts: int = 0, accept_before_failure: bool = False):
        self.messages: list[dict] = []
        self.fail_posts = fail_posts
        self.accept_before_failure = accept_before_failure

    def chat_postMessage(self, **payload):
        if self.accept_before_failure or self.fail_posts <= 0:
            self.messages.append(payload)
        if self.fail_posts > 0:
            self.fail_posts -= 1
            raise TimeoutError("ambiguous Slack delivery")
        return {"ts": "1787450005.654321"}


class ChatGPTSlackRoutingTests(unittest.TestCase):
    def _module(self):
        import director_orchestrator.config as config_module
        import slack_bolt

        settings = replace(
            _settings("unused.sqlite3"),
            chatgpt_bridge_slack_user_id="U0B9KFJPST1",
            orchestrator_base_url="http://orchestrator:8080",
        )
        module_name = "director_orchestrator.slack_bot"
        sys.modules.pop(module_name, None)
        with (
            patch.object(config_module, "Settings", return_value=settings),
            patch.object(slack_bolt, "App", _FakeBoltApp),
        ):
            module = importlib.import_module(module_name)
        return module_name, module

    def test_dual_handlers_canonicalize_identity_and_preserve_other_mentions(self):
        module_name, slack_bot = self._module()
        try:
            event = {
                "client_msg_id": "client-only-on-message",
                "channel": "C123",
                "user": "UOWNER123",
                "text": "<@UAESOP123> <@U0B9KFJPST1> summarize <@UALICE123>'s note",
                "ts": "1787450100.123456",
            }
            body = {
                "source_event_id": "C123:1787450100.123456",
                "state": "OWNER_DECISION_REQUIRED",
                "request_reference": "CBR-" + "A" * 24,
                "prompt_digest": "a" * 64,
                "approval_instruction": "approve exactly",
                "delivery_client_msg_id": str(
                    __import__("uuid").uuid4()
                ),
                "answer": "",
                "cached": False,
                "deliver": True,
            }
            client = _SlackClient()
            with patch.object(
                slack_bot.httpx, "post", return_value=_SlackResponse(body)
            ) as post:
                slack_bot.mention_handler(
                    {key: value for key, value in event.items() if key != "client_msg_id"},
                    client,
                )
                slack_bot.message_handler(event, client)
            self.assertEqual(post.call_count, 2)
            payloads = [call.kwargs["json"] for call in post.call_args_list]
            self.assertEqual(
                {item["source_event_id"] for item in payloads},
                {"C123:1787450100.123456"},
            )
            self.assertEqual(
                {item["prompt"] for item in payloads},
                {"<@UAESOP123>  summarize <@UALICE123>'s note"},
            )
            self.assertEqual(len({item["client_msg_id"] for item in client.messages}), 1)
        finally:
            sys.modules.pop(module_name, None)

    def test_failed_or_ambiguous_answer_post_keeps_recovery_and_never_posts_blocked(self):
        module_name, slack_bot = self._module()
        try:
            event = {
                "channel": "C123",
                "user": "UOWNER123",
                "text": "<@U0B9KFJPST1> APPROVE CBR-AAAAAAAAAAAAAAAAAAAAAAAA exact prompt",
                "ts": "1787450200.123456",
                "thread_ts": "1787450000.123456",
            }
            answer_body = {
                "source_event_id": "C123:1787450200.123456",
                "state": "ANSWER_READY",
                "request_reference": "CBR-" + "A" * 24,
                "prompt_digest": "b" * 64,
                "approval_instruction": "",
                "delivery_client_msg_id": "acdd41ee-0d0b-4e8b-b2fa-8c7465c60876",
                "answer": "paid answer",
                "cached": False,
                "deliver": True,
            }
            client = _SlackClient(fail_posts=2, accept_before_failure=True)
            with patch.object(
                slack_bot.httpx, "post", return_value=_SlackResponse(answer_body)
            ) as post:
                slack_bot.message_handler(event, client)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(len(client.messages), 2)
            self.assertEqual(
                {item["client_msg_id"] for item in client.messages},
                {answer_body["delivery_client_msg_id"]},
            )
            self.assertTrue(all("BLOCKED" not in item["text"] for item in client.messages))
        finally:
            sys.modules.pop(module_name, None)

    def test_successful_answer_post_acks_and_transport_timeout_has_one_recovery(self):
        module_name, slack_bot = self._module()
        try:
            event = {
                "channel": "C123",
                "user": "UOWNER123",
                "text": "<@U0B9KFJPST1> APPROVE CBR-BBBBBBBBBBBBBBBBBBBBBBBB exact prompt",
                "ts": "1787450300.123456",
                "thread_ts": "1787450000.123456",
            }
            answer_body = {
                "source_event_id": "C123:1787450300.123456",
                "state": "ANSWER_READY",
                "request_reference": "CBR-" + "B" * 24,
                "prompt_digest": "c" * 64,
                "approval_instruction": "",
                "delivery_client_msg_id": "bcdd41ee-0d0b-4e8b-b2fa-8c7465c60876",
                "answer": "paid answer",
                "cached": False,
                "deliver": True,
            }
            client = _SlackClient()
            with patch.object(
                slack_bot.httpx,
                "post",
                side_effect=[
                    _SlackResponse(answer_body),
                    _SlackResponse(
                        {
                            "request_reference": answer_body["request_reference"],
                            "state": "DELIVERED",
                            "cached": False,
                        }
                    ),
                ],
            ) as post:
                slack_bot.message_handler(event, client)
            self.assertEqual(post.call_count, 2)
            self.assertTrue(post.call_args_list[1].args[0].endswith("/delivery-ack"))
            self.assertIn("gateway_proof", post.call_args_list[1].kwargs["json"])

            pending_body = {
                **answer_body,
                "state": "DISPATCHING",
                "answer": "",
                "deliver": False,
            }
            client = _SlackClient()
            with patch.object(
                slack_bot.httpx,
                "post",
                side_effect=[
                    httpx.TimeoutException("first transport timeout"),
                    _SlackResponse(pending_body),
                ],
            ) as recovery_post:
                slack_bot.message_handler(event, client)
            self.assertEqual(recovery_post.call_count, 2)
            self.assertEqual(
                recovery_post.call_args_list[0].kwargs["json"],
                recovery_post.call_args_list[1].kwargs["json"],
            )
            self.assertIn("PENDING", client.messages[-1]["text"])
            self.assertNotIn("BLOCKED", client.messages[-1]["text"])
        finally:
            sys.modules.pop(module_name, None)


class ChatGPTBridgeRouteTests(unittest.TestCase):
    def test_route_auth_owner_decision_bad_proof_and_provider_failure(self):
        import director_orchestrator.config as config_module

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            module_name = "director_orchestrator.main"
            sys.modules.pop(module_name, None)
            with patch.object(config_module, "Settings", return_value=settings):
                main = importlib.import_module(module_name)
            service = ChatGPTBridgeService(settings, _store(database, settings))
            initial = _signed_request(settings)
            try:
                with patch.object(main, "chatgpt_bridge", service):
                    client = TestClient(main.app)
                    payload = initial.model_dump(mode="json")
                    self.assertEqual(
                        client.post("/v1/chatgpt-bridge", json=payload).status_code,
                        401,
                    )
                    self.assertEqual(
                        client.post(
                            "/v1/chatgpt-bridge",
                            headers={"Authorization": "Bearer wrong"},
                            json=payload,
                        ).status_code,
                        401,
                    )
                    owner_decision = client.post(
                        "/v1/chatgpt-bridge",
                        headers={"Authorization": f"Bearer {settings.shared_secret}"},
                        json=payload,
                    )
                    self.assertEqual(owner_decision.status_code, 200)
                    self.assertEqual(
                        owner_decision.json()["state"], "OWNER_DECISION_REQUIRED"
                    )
                    self.assertEqual(RecordingAsyncClient.calls, [])

                    tampered = initial.model_copy(update={"channel_id": "C456"})
                    bad_proof = client.post(
                        "/v1/chatgpt-bridge",
                        headers={"Authorization": f"Bearer {settings.shared_secret}"},
                        json=tampered.model_dump(mode="json"),
                    )
                    self.assertEqual(bad_proof.status_code, 403)

                    approval = _approval(
                        settings, owner_decision.json()["request_reference"]
                    )
                    RecordingAsyncClient.responses = [
                        httpx.TimeoutException("provider failure")
                    ]
                    with patch(
                        "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                        RecordingAsyncClient,
                    ):
                        provider_failure = client.post(
                            "/v1/chatgpt-bridge",
                            headers={
                                "Authorization": f"Bearer {settings.shared_secret}"
                            },
                            json=approval.model_dump(mode="json"),
                        )
                    self.assertEqual(provider_failure.status_code, 502)

                    initial_2 = _signed_request(
                        settings,
                        event_ts="1787450090.123456",
                        thread_ts="1787450090.123456",
                    )
                    pending_2 = client.post(
                        "/v1/chatgpt-bridge",
                        headers={"Authorization": f"Bearer {settings.shared_secret}"},
                        json=initial_2.model_dump(mode="json"),
                    )
                    approval_2 = _approval(
                        settings,
                        pending_2.json()["request_reference"],
                        event_ts="1787450091.123456",
                        thread_ts="1787450090.123456",
                    )
                    RecordingAsyncClient.responses = [
                        {"output_text": "route delivery answer"}
                    ]
                    with patch(
                        "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                        RecordingAsyncClient,
                    ):
                        answer_2 = client.post(
                            "/v1/chatgpt-bridge",
                            headers={
                                "Authorization": f"Bearer {settings.shared_secret}"
                            },
                            json=approval_2.model_dump(mode="json"),
                        )
                    self.assertEqual(answer_2.status_code, 200, answer_2.text)
                    ack = _signed_ack(
                        settings,
                        approval_2,
                        type("Response", (), answer_2.json())(),
                    )
                    self.assertEqual(
                        client.post(
                            "/v1/chatgpt-bridge/delivery-ack",
                            json=ack.model_dump(mode="json"),
                        ).status_code,
                        401,
                    )
                    tampered_ack = ack.model_copy(
                        update={"slack_message_ts": "1787450099.123456"}
                    )
                    self.assertEqual(
                        client.post(
                            "/v1/chatgpt-bridge/delivery-ack",
                            headers={
                                "Authorization": f"Bearer {settings.shared_secret}"
                            },
                            json=tampered_ack.model_dump(mode="json"),
                        ).status_code,
                        403,
                    )
                    accepted_ack = client.post(
                        "/v1/chatgpt-bridge/delivery-ack",
                        headers={"Authorization": f"Bearer {settings.shared_secret}"},
                        json=ack.model_dump(mode="json"),
                    )
                    replayed_ack = client.post(
                        "/v1/chatgpt-bridge/delivery-ack",
                        headers={"Authorization": f"Bearer {settings.shared_secret}"},
                        json=ack.model_dump(mode="json"),
                    )
                    self.assertEqual(accepted_ack.status_code, 200)
                    self.assertFalse(accepted_ack.json()["cached"])
                    self.assertTrue(replayed_ack.json()["cached"])
            finally:
                sys.modules.pop(module_name, None)


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-23 08:12 UTC | END]]
