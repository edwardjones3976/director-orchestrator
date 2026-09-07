# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:49 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | BEGIN]]
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
    ChatGPTBridgeDeliveryAmbiguity,
    ChatGPTBridgeGatewayProof,
    ChatGPTBridgeRequest,
    ChatGPTBridgeService,
    ChatGPTBridgeStore,
    build_chatgpt_bridge_delivery_ack_proof,
    build_chatgpt_bridge_delivery_ambiguity_proof,
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
        claude_routine_url="https://claude.example/routine",
        claude_routine_token="test-claude-token",
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
        trusted_provider_cost_ranks_json=(
            '{"claude":1,"cursor":2,"gemini":3,"grok":4,"perplexity":5}'
        ),
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


def _recovery(
    settings: Settings,
    request_reference: str,
    *,
    event_ts: str = "1787450004.123456",
    prompt: str = PROMPT,
    thread_ts: str = "1787450000.123456",
    issued_at: int | None = None,
) -> ChatGPTBridgeRequest:
    return _signed_request(
        settings,
        action="recover",
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


def _signed_ambiguity(
    settings: Settings,
    approval: ChatGPTBridgeRequest,
    response,
) -> ChatGPTBridgeDeliveryAmbiguity:
    unsigned = ChatGPTBridgeDeliveryAmbiguity(
        source_event_id=approval.source_event_id,
        request_reference=response.request_reference,
        channel_id=approval.channel_id,
        thread_ts=approval.thread_ts,
        user_id=approval.user_id,
        prompt_digest=response.prompt_digest,
        delivery_client_msg_id=response.delivery_client_msg_id,
        gateway_proof=ChatGPTBridgeGatewayProof(
            issued_at=1,
            nonce="N" * 32,
            signature="0" * 64,
        ),
    )
    proof = build_chatgpt_bridge_delivery_ambiguity_proof(
        settings.slack_gateway_secret,
        settings.shared_secret,
        unsigned,
        issued_at=int(time.time()),
        nonce="B" * 32,
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

    async def test_matching_approval_dispatches_once_and_duplicates_cannot_redeliver(self):
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
            self.assertEqual(same_event.state, "DELIVERY_PENDING_ACK")
            self.assertEqual(same_event.answer, "")
            self.assertFalse(same_event.deliver)
            self.assertTrue(same_event.cached)
            self.assertEqual(new_event.state, "DELIVERY_PENDING_ACK")
            self.assertEqual(new_event.answer, "")
            self.assertFalse(new_event.deliver)
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
            self.assertEqual(row[2], "DELIVERY_PENDING_ACK")
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
            self.assertEqual(recovered.state, "DELIVERY_PENDING_ACK")
            self.assertTrue(recovered.cached)
            self.assertFalse(recovered.deliver)
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

    async def test_ambiguous_delivery_requires_explicit_owner_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            store = _store(database, settings)
            service = ChatGPTBridgeService(settings, store)
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            RecordingAsyncClient.responses = [{"output_text": "recoverable answer"}]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                answer = await service.reply(approval)

            ambiguity = _signed_ambiguity(settings, approval, answer)
            first_mark = service.mark_delivery_ambiguous(ambiguity)
            replay_mark = service.mark_delivery_ambiguous(ambiguity)
            duplicate_approval = await service.reply(approval)
            recovery = _recovery(settings, pending.request_reference)
            recovery_results = await asyncio.gather(
                service.reply(recovery),
                service.reply(recovery),
            )
            released = [item for item in recovery_results if item.answer]
            self.assertEqual(len(released), 1)
            recovered = released[0]
            in_flight_replay = next(
                item for item in recovery_results if not item.answer
            )
            recovery_ambiguity = _signed_ambiguity(settings, recovery, recovered)
            service.mark_delivery_ambiguous(recovery_ambiguity)
            duplicate_recovery = await service.reply(recovery)
            new_recovery = _recovery(
                settings,
                pending.request_reference,
                event_ts="1787450005.123456",
            )
            recovered_again = await service.reply(new_recovery)

            self.assertFalse(first_mark.cached)
            self.assertTrue(replay_mark.cached)
            self.assertEqual(duplicate_approval.state, "DELIVERY_AMBIGUOUS")
            self.assertNotIn(
                "exactly-once", duplicate_approval.approval_instruction.lower()
            )
            self.assertIn("may already have accepted", duplicate_approval.approval_instruction)
            self.assertEqual(recovered.state, "ANSWER_READY")
            self.assertEqual(recovered.answer, "recoverable answer")
            self.assertTrue(recovered.deliver)
            self.assertEqual(
                in_flight_replay.state, "DELIVERY_RECOVERY_PENDING"
            )
            self.assertFalse(in_flight_replay.deliver)
            self.assertEqual(duplicate_recovery.state, "DELIVERY_AMBIGUOUS")
            self.assertEqual(duplicate_recovery.answer, "")
            self.assertTrue(duplicate_recovery.deliver)
            self.assertEqual(recovered_again.state, "ANSWER_READY")
            self.assertEqual(recovered_again.answer, "recoverable answer")
            self.assertTrue(recovered_again.deliver)
            self.assertEqual(len(RecordingAsyncClient.calls), 1)

    async def test_decrypt_failure_wipe_commits_before_error_is_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            store = _store(database, settings)
            service = ChatGPTBridgeService(settings, store)
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            RecordingAsyncClient.responses = [{"output_text": "sensitive answer"}]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                answer = await service.reply(approval)
            service.mark_delivery_ambiguous(_signed_ambiguity(settings, approval, answer))
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "UPDATE chatgpt_bridge_requests SET answer_ciphertext = ? "
                    "WHERE request_reference = ?",
                    (b"corrupted ciphertext", pending.request_reference),
                )

            with self.assertRaisesRegex(BridgeRejected, "recovery failed"):
                await service.reply(_recovery(settings, pending.request_reference))

            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT status, decision_state, answer_nonce, answer_ciphertext, "
                    "answer_expires_at FROM chatgpt_bridge_requests"
                ).fetchone()
            self.assertEqual(row, ("failed", "FAILED", None, None, None))

    async def test_expiry_sweeper_wipes_ciphertext_without_another_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            store = _store(database, settings)
            service = ChatGPTBridgeService(settings, store)
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            RecordingAsyncClient.responses = [{"output_text": "expiring answer"}]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                await service.reply(approval)
            with closing(sqlite3.connect(database)) as connection:
                expires_at = connection.execute(
                    "SELECT answer_expires_at FROM chatgpt_bridge_requests"
                ).fetchone()[0]

            self.assertEqual(store.sweep_expired_answers(now=expires_at + 1), 1)

            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT status, decision_state, answer_nonce, answer_ciphertext, "
                    "answer_expires_at FROM chatgpt_bridge_requests"
                ).fetchone()
            self.assertEqual(row, ("failed", "FAILED", None, None, None))

    async def test_pending_delivery_sweeper_requires_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            store = _store(database, settings)
            service = ChatGPTBridgeService(settings, store)
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            RecordingAsyncClient.responses = [{"output_text": "unacknowledged answer"}]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                await service.reply(approval)
            with closing(sqlite3.connect(database)) as connection:
                started_at = connection.execute(
                    "SELECT delivery_attempt_started_at FROM chatgpt_bridge_requests"
                ).fetchone()[0]

            self.assertEqual(
                store.sweep_expired_answers(
                    now=started_at + 61,
                    pending_timeout_seconds=60,
                ),
                0,
            )

            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT decision_state, answer_ciphertext, "
                    "delivery_attempt_source_event_id "
                    "FROM chatgpt_bridge_requests"
                ).fetchone()
            self.assertEqual(row[0], "DELIVERY_AMBIGUOUS")
            self.assertIsNotNone(row[1])
            self.assertIsNone(row[2])

    async def test_sweeper_preserves_consumed_recovery_event_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            store = _store(database, settings)
            service = ChatGPTBridgeService(settings, store)
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            RecordingAsyncClient.responses = [{"output_text": "one provider answer"}]
            with patch(
                "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                RecordingAsyncClient,
            ):
                answer = await service.reply(approval)
            service.mark_delivery_ambiguous(_signed_ambiguity(settings, approval, answer))
            recovery = _recovery(settings, pending.request_reference)
            recovered = await service.reply(recovery)
            with closing(sqlite3.connect(database)) as connection:
                started_at = connection.execute(
                    "SELECT delivery_attempt_started_at FROM chatgpt_bridge_requests"
                ).fetchone()[0]

            store.sweep_expired_answers(
                now=started_at + 61,
                pending_timeout_seconds=60,
            )
            replay = await service.reply(recovery)
            new_recovery = _recovery(
                settings,
                pending.request_reference,
                event_ts="1787450006.123456",
            )
            recovered_again = await service.reply(new_recovery)

            self.assertEqual(replay.state, "DELIVERY_AMBIGUOUS")
            self.assertEqual(replay.answer, "")
            self.assertEqual(recovered_again.state, "ANSWER_READY")
            self.assertEqual(recovered_again.answer, recovered.answer)
            self.assertEqual(len(RecordingAsyncClient.calls), 1)
            with closing(sqlite3.connect(database)) as connection:
                consumed = connection.execute(
                    "SELECT source_event_id FROM "
                    "chatgpt_bridge_consumed_recovery_events "
                    "ORDER BY consumed_at"
                ).fetchall()
            self.assertEqual(
                {row[0] for row in consumed},
                {recovery.source_event_id, new_recovery.source_event_id},
            )

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
                tables = {
                    item[0]
                    for item in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            self.assertEqual(row, ("failed", "FAILED", "", None))
            self.assertIn("approval_source_event_id", columns)
            self.assertIn("answer_ciphertext", columns)
            self.assertIn("delivery_attempt_source_event_id", columns)
            self.assertIn("delivery_attempt_started_at", columns)
            self.assertIn("chatgpt_bridge_consumed_recovery_events", tables)

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
            self.assertEqual(post.call_count, 1)
            payloads = [call.kwargs["json"] for call in post.call_args_list]
            self.assertEqual(
                {item["source_event_id"] for item in payloads},
                {"C123:1787450100.123456"},
            )
            self.assertEqual(
                {item["prompt"] for item in payloads},
                {"<@UAESOP123>  summarize <@UALICE123>'s note"},
            )
            self.assertTrue(
                all("client_msg_id" not in item for item in client.messages)
            )
            self.assertEqual(len(client.messages), 1)
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
                slack_bot.httpx,
                "post",
                side_effect=[
                    _SlackResponse(answer_body),
                    _SlackResponse(
                        {
                            "request_reference": answer_body["request_reference"],
                            "state": "DELIVERY_AMBIGUOUS",
                            "cached": False,
                        }
                    ),
                ],
            ) as post:
                slack_bot.message_handler(event, client)
            self.assertEqual(post.call_count, 2)
            self.assertTrue(
                post.call_args_list[1].args[0].endswith("/delivery-ambiguous")
            )
            self.assertEqual(len(client.messages), 1)
            self.assertNotIn("client_msg_id", client.messages[0])
            self.assertTrue(all("BLOCKED" not in item["text"] for item in client.messages))
        finally:
            sys.modules.pop(module_name, None)

    def test_alias_interception_requires_owner_and_allowlisted_channel(self):
        module_name, slack_bot = self._module()
        try:
            client = _SlackClient()
            non_owner = {
                "channel": "C123",
                "user": "UOTHER123",
                "text": "<@U0B9KFJPST1> hello",
                "ts": "1787450400.123456",
            }
            wrong_channel = {
                "channel": "C999",
                "user": "UOWNER123",
                "text": "<@U0B9KFJPST1> DIRECTOR status",
                "ts": "1787450401.123456",
            }
            with (
                patch.object(slack_bot, "_forward") as forward,
                patch.object(slack_bot.httpx, "post") as post,
            ):
                slack_bot.mention_handler(non_owner, client)
                slack_bot.message_handler(wrong_channel, client)
            self.assertEqual(forward.call_count, 2)
            self.assertFalse(post.called)
        finally:
            sys.modules.pop(module_name, None)

    def test_successful_answer_post_acks_and_lost_response_arms_recovery(self):
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

            lost_event = {
                **event,
                "ts": "1787450310.123456",
            }
            pending_body = {
                **answer_body,
                "source_event_id": "C123:1787450310.123456",
                "state": "DELIVERY_PENDING_ACK",
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
                    _SlackResponse(
                        {
                            "request_reference": pending_body["request_reference"],
                            "state": "DELIVERY_AMBIGUOUS",
                            "approval_instruction": (
                                "Slack delivery is ambiguous. Reply with "
                                "`@ChatGPT RECOVER CBR-BBBBBBBBBBBBBBBBBBBBBBBB "
                                "<repeat the original prompt exactly>`. Recovery may "
                                "duplicate a message Slack already accepted."
                            ),
                            "cached": False,
                        }
                    ),
                ],
            ) as recovery_post:
                slack_bot.message_handler(lost_event, client)
            self.assertEqual(recovery_post.call_count, 3)
            self.assertEqual(
                recovery_post.call_args_list[0].kwargs["json"],
                recovery_post.call_args_list[1].kwargs["json"],
            )
            self.assertTrue(
                recovery_post.call_args_list[2].args[0].endswith(
                    "/delivery-ambiguous"
                )
            )
            ambiguity_payload = recovery_post.call_args_list[2].kwargs["json"]
            self.assertEqual(
                ambiguity_payload["source_event_id"],
                "C123:1787450310.123456",
            )
            self.assertIn("gateway_proof", ambiguity_payload)
            self.assertIn("DELIVERY_AMBIGUOUS", client.messages[-1]["text"])
            self.assertIn("RECOVER", client.messages[-1]["text"])
            self.assertIn("duplicate", client.messages[-1]["text"])
            self.assertNotIn("BLOCKED", client.messages[-1]["text"])
        finally:
            sys.modules.pop(module_name, None)


class ChatGPTBridgeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_and_periodic_sweeps_are_wired(self):
        import director_orchestrator.config as config_module

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            settings = _settings(database)
            module_name = "director_orchestrator.main"
            sys.modules.pop(module_name, None)
            with patch.object(config_module, "Settings", return_value=settings):
                main = importlib.import_module(module_name)
            service = ChatGPTBridgeService(settings, _store(database, settings))
            try:
                with patch.object(main, "chatgpt_bridge", service), patch.object(
                    service.store, "sweep_expired_answers"
                ) as sweep:
                    async with main.lifespan(main.app):
                        self.assertEqual(sweep.call_count, 1)

                    sleep_calls = 0

                    async def one_periodic_cycle(_: float) -> None:
                        nonlocal sleep_calls
                        sleep_calls += 1
                        if sleep_calls > 1:
                            raise asyncio.CancelledError

                    with patch.object(main.asyncio, "sleep", new=one_periodic_cycle):
                        with self.assertRaises(asyncio.CancelledError):
                            await main._chatgpt_bridge_expiry_loop()
                    self.assertEqual(sweep.call_count, 2)
            finally:
                sys.modules.pop(module_name, None)


class ChatGPTBridgeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        RecordingAsyncClient.calls = []
        RecordingAsyncClient.responses = []
        RecordingAsyncClient.delay_seconds = 0

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

                    initial_3 = _signed_request(
                        settings,
                        event_ts="1787450100.123456",
                        thread_ts="1787450100.123456",
                    )
                    pending_3 = client.post(
                        "/v1/chatgpt-bridge",
                        headers={"Authorization": f"Bearer {settings.shared_secret}"},
                        json=initial_3.model_dump(mode="json"),
                    )
                    approval_3 = _approval(
                        settings,
                        pending_3.json()["request_reference"],
                        event_ts="1787450101.123456",
                        thread_ts="1787450100.123456",
                    )
                    RecordingAsyncClient.responses = [
                        {"output_text": "ambiguous route answer"}
                    ]
                    with patch(
                        "director_orchestrator.chatgpt_bridge.httpx.AsyncClient",
                        RecordingAsyncClient,
                    ):
                        answer_3 = client.post(
                            "/v1/chatgpt-bridge",
                            headers={
                                "Authorization": f"Bearer {settings.shared_secret}"
                            },
                            json=approval_3.model_dump(mode="json"),
                        )
                    ambiguity = _signed_ambiguity(
                        settings,
                        approval_3,
                        type("Response", (), answer_3.json())(),
                    )
                    self.assertEqual(
                        client.post(
                            "/v1/chatgpt-bridge/delivery-ambiguous",
                            json=ambiguity.model_dump(mode="json"),
                        ).status_code,
                        401,
                    )
                    tampered_ambiguity = ambiguity.model_copy(
                        update={"thread_ts": "1787450199.123456"}
                    )
                    self.assertEqual(
                        client.post(
                            "/v1/chatgpt-bridge/delivery-ambiguous",
                            headers={
                                "Authorization": f"Bearer {settings.shared_secret}"
                            },
                            json=tampered_ambiguity.model_dump(mode="json"),
                        ).status_code,
                        403,
                    )
                    accepted_ambiguity = client.post(
                        "/v1/chatgpt-bridge/delivery-ambiguous",
                        headers={"Authorization": f"Bearer {settings.shared_secret}"},
                        json=ambiguity.model_dump(mode="json"),
                    )
                    replayed_ambiguity = client.post(
                        "/v1/chatgpt-bridge/delivery-ambiguous",
                        headers={"Authorization": f"Bearer {settings.shared_secret}"},
                        json=ambiguity.model_dump(mode="json"),
                    )
                    self.assertEqual(accepted_ambiguity.status_code, 200)
                    self.assertFalse(accepted_ambiguity.json()["cached"])
                    self.assertTrue(replayed_ambiguity.json()["cached"])
            finally:
                sys.modules.pop(module_name, None)


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-23 08:12 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:49 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
