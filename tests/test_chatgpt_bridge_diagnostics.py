# [[OpenAI Codex | GPT-6 | 2026-09-13 | 23:00 UTC | BEGIN]]
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx

from director_orchestrator.chatgpt_bridge import ChatGPTBridgeService, _failure_diagnostics
from tests.test_chatgpt_slack_bridge import _approval, _settings, _signed_request, _store


class BridgeDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, response, *, break_diagnostics=False, break_store=False):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "bridge.sqlite3")
            settings = _settings(database)
            store = _store(database, settings)
            service = ChatGPTBridgeService(settings, store)
            pending = await service.reply(_signed_request(settings))
            approval = _approval(settings, pending.request_reference)
            calls = []

            class Client:
                def __init__(self, **kwargs):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def post(self, *args, **kwargs):
                    calls.append(True)
                    if isinstance(response, Exception):
                        raise response
                    return response

            with patch("director_orchestrator.chatgpt_bridge.httpx.AsyncClient", Client):
                with patch("director_orchestrator.chatgpt_bridge._failure_diagnostics",
                           side_effect=RuntimeError("diagnostic secret") if break_diagnostics
                           else _failure_diagnostics):
                    original_fail = store.fail_dispatch
                    with patch.object(store, "fail_dispatch", side_effect=
                                      RuntimeError("store secret") if break_store else original_fail):
                        with self.assertRaises(Exception) as caught:
                            await service.reply(approval)
                replay = await service.reply(approval)
            self.assertEqual(len(calls), 1)
            self.assertEqual(replay.state, "DISPATCHING" if break_store else "FAILED")
            self.assertFalse(replay.deliver)
            # Reopen to prove retention across service restarts and additive init.
            _store(database, settings)
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT failure_diagnostics_json, estimated_cost_usd, "
                    "approval_source_event_id, answer_ciphertext FROM chatgpt_bridge_requests"
                ).fetchone()
            self.assertEqual(row[1], 0.25)
            self.assertEqual(row[2], approval.source_event_id)
            self.assertIsNone(row[3])
            self.assertNotIn("failure_diagnostics_json", replay.model_dump())
            return json.loads(row[0]) if row[0] else None, caught.exception

    def response(self, status=429, **kwargs):
        return httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"),
                              **kwargs)

    async def test_http_failure_retains_safe_fields_and_reservation_without_retry(self):
        response = self.response(headers={"x-request-id": "req_" + "a" * 32,
                                          "authorization": "Bearer sk-secret"}, json={
            "error": {"code": "insufficient_quota", "type": "insufficient_quota",
                      "message": "private prompt and secret", "param": "private prompt"},
            "output_text": "private answer",
        })
        diagnostics, error = await self.exercise(response)
        self.assertIsInstance(error, httpx.HTTPStatusError)
        self.assertEqual(diagnostics, {
            "stage": "http_status", "category": "exception", "exception_class": "HTTPStatusError",
            "http_status": 429, "provider_request_id": "req_" + "a" * 32,
            "provider_error_code": "insufficient_quota", "provider_error_type": "insufficient_quota",
        })

    async def test_hostile_labels_headers_and_exception_messages_never_persist(self):
        for hostile in ("sk-private-key", "repeat my private prompt", "Bearer secret\nnext",
                        "req_" + "a" * 32 + "private", {"secret": "private"}, ["private"]):
            with self.subTest(hostile=hostile):
                response = self.response(headers={"x-request-id": str(hostile)}, json={
                    "error": {"code": hostile, "type": hostile, "message": hostile},
                    "output_text": "private answer",
                })
                diagnostics, _ = await self.exercise(response)
                self.assertEqual(set(diagnostics), {"stage", "category", "exception_class", "http_status"})
        hostile_class = type("private_prompt_sk_secret", (RuntimeError,), {})
        error = hostile_class("private prompt and sk-secret")
        diagnostics, original = await self.exercise(error)
        self.assertIs(original, error)
        self.assertEqual(diagnostics, {"stage": "transport", "category": "exception",
                                       "exception_class": "Exception"})

    async def test_transport_failure_is_not_misclassified_as_http_error(self):
        failure = httpx.ReadTimeout("sk-secret private prompt")
        diagnostics, original = await self.exercise(failure)
        self.assertIs(original, failure)
        self.assertEqual(diagnostics, {"stage": "transport", "category": "exception",
                                       "exception_class": "ReadTimeout"})

    async def test_empty_incomplete_and_malformed_success_are_distinct(self):
        for response, stage, category, exception_class in (
            (self.response(200, json={"output": []}), "answer_validation", "empty_response", "RuntimeError"),
            (self.response(200, json={"status": "incomplete", "output": []}),
             "answer_validation", "incomplete_response", "RuntimeError"),
            (self.response(200, content=b"private not json"), "response_decode", "exception", "JSONDecodeError"),
        ):
            with self.subTest(category=category, stage=stage):
                diagnostics, _ = await self.exercise(response)
                self.assertEqual(diagnostics, {"stage": stage, "category": category,
                                               "exception_class": exception_class, "http_status": 200})

    async def test_malformed_oversized_and_wrong_shape_errors_still_retain_http_status(self):
        for content in (b"private not json", b'{"error":' + b" " * 17_000,
                        b'[]', b'{"error": "private"}', b'{"error": null}'):
            with self.subTest(length=len(content)):
                diagnostics, error = await self.exercise(self.response(content=content))
                self.assertIsInstance(error, httpx.HTTPStatusError)
                self.assertEqual(diagnostics["http_status"], 429)
                self.assertEqual(len(diagnostics), 4)

    async def test_diagnostic_builder_failure_does_not_mask_error_or_prevent_terminal_state(self):
        failure = httpx.ReadTimeout("original secret")
        diagnostics, original = await self.exercise(failure, break_diagnostics=True)
        self.assertIs(original, failure)
        self.assertIsNone(diagnostics)

    async def test_persistence_failure_preserves_original_exception_and_no_retry(self):
        failure = httpx.ReadTimeout("original secret")
        diagnostics, original = await self.exercise(failure, break_store=True)
        self.assertIs(original, failure)
        self.assertIsNone(diagnostics)

    async def test_existing_schema_adds_nullable_diagnostics_without_rewriting_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "bridge.sqlite3")
            store = _store(database)
            settings = _settings(database)
            await ChatGPTBridgeService(settings, store).reply(_signed_request(settings))
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("ALTER TABLE chatgpt_bridge_requests DROP COLUMN failure_diagnostics_json")
                connection.row_factory = sqlite3.Row
                before = dict(connection.execute("SELECT * FROM chatgpt_bridge_requests").fetchone())
                connection.commit()
            _store(database)
            with closing(sqlite3.connect(database)) as connection:
                connection.row_factory = sqlite3.Row
                columns = {r[1] for r in connection.execute("PRAGMA table_info(chatgpt_bridge_requests)")}
                after = dict(connection.execute("SELECT * FROM chatgpt_bridge_requests").fetchone())
            self.assertIn("failure_diagnostics_json", columns)
            self.assertIsNone(after.pop("failure_diagnostics_json"))
            self.assertEqual(before, after)

# [[OpenAI Codex | GPT-6 | 2026-09-13 | 23:00 UTC | END]]
