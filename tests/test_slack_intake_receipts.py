# [[Claude Code | Claude Opus 5 | 2026-09-08 | 11:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 00:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 21:33 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 20:28 UTC | BEGIN]]
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import slack_bolt

import director_orchestrator.config as config_module


NORMAL_ID = "DIR-A1B2C3D4E5"
PRIMARY_ID = "DIR-LG-F0E1D2C3B4"
BODY_MARKER = "synthetic-private-response-marker"
SHARED_SECRET = "synthetic-intake-bearer"
GATEWAY_SECRET = "synthetic-intake-gateway-secret-32-characters"


class _FakeBoltApp:
    def __init__(self, **kwargs):
        pass

    def event(self, name):
        return lambda function: function

    def command(self, name):
        return lambda function: function

    def action(self, name):
        return lambda function: function


class SlackIntakeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            log_level="INFO",
            slack_bot_token="xoxb-synthetic-intake-fixture",
            slack_app_token="xapp-synthetic-intake-fixture",
            slack_gateway_secret=GATEWAY_SECRET,
            shared_secret=SHARED_SECRET,
            slack_channel_id="CFIXTURE",
            slack_owner_user_id="UFIXTURE",
            orchestrator_base_url="http://director.invalid",
            n8n_webhook_url="",
            intake_allocation_profile="engineering",
            intake_primary_required_capabilities=(),
        )
        # Load a separate module object: no live Bolt initialization, environment
        # settings, or shared slack_bot event ledger is used by these tests.
        source = (
            Path(__file__).resolve().parents[1]
            / "src/director_orchestrator/slack_bot.py"
        )
        spec = importlib.util.spec_from_file_location(
            "director_orchestrator._slack_intake_receipts_test", source
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        self.slack_bot = importlib.util.module_from_spec(spec)
        with (
            patch.object(config_module, "Settings", return_value=self.settings),
            patch.object(slack_bolt, "App", _FakeBoltApp),
        ):
            spec.loader.exec_module(self.slack_bot)
        self.event = {
            "client_msg_id": "synthetic-intake-event",
            "channel": "CFIXTURE",
            "user": "UFIXTURE",
            "text": "DIRECTOR synthetic fixture work order",
            "ts": "1788810000.123456",
            "thread_ts": "1788810000.000001",
        }
        self.client = Mock()

    def response(self, status, body):
        return httpx.Response(
            status,
            json=body,
            request=httpx.Request(
                "POST",
                "http://director.invalid/v1/intake",
                headers={"Authorization": f"Bearer {SHARED_SECRET}"},
            ),
        )

    def reset_case(self):
        self.slack_bot._seen_events.clear()
        self.client.reset_mock()

    def assert_blocked(self):
        self.client.chat_postMessage.assert_called_once()
        message = self.client.chat_postMessage.call_args.kwargs
        self.assertEqual(message["channel"], self.event["channel"])
        self.assertEqual(message["thread_ts"], self.event["thread_ts"])
        self.assertIn("DIRECTOR — BLOCKED", message["text"])
        self.assertIn("Acceptance could not be confirmed", message["text"])
        self.assertIn("request state must be reconciled before retrying", message["text"])
        self.assertNotIn("QUEUED", message["text"])
        self.assertNotIn("ACCEPTED", message["text"])
        self.assertNotIn("could not accept", message["text"])

    def forward_response(self, response):
        with patch.object(self.slack_bot.httpx, "post", return_value=response) as post:
            self.slack_bot._forward(self.event, self.client, require_prefix=True)
        post.assert_called_once()
        return post

    def test_valid_receipts_require_202_for_direct_and_n8n_routes(self):
        receipts = (
            (NORMAL_ID, {"task_id": NORMAL_ID}),
            (PRIMARY_ID, {"task_id": PRIMARY_ID}),
            (NORMAL_ID, {"data": {"task_id": NORMAL_ID}}),
            (PRIMARY_ID, {"data": {"task_id": PRIMARY_ID}}),
            (NORMAL_ID, [{"task_id": NORMAL_ID}]),
            (PRIMARY_ID, [{"data": {"task_id": PRIMARY_ID}}]),
            (NORMAL_ID, {"task_id": NORMAL_ID, "data": {"task_id": NORMAL_ID}}),
            ("DIR-A", {"task_id": "DIR-A"}),
        )
        for webhook in ("", "http://n8n.invalid/webhook/director-intake"):
            for task_id, body in receipts:
                with self.subTest(webhook=bool(webhook), body=body):
                    self.reset_case()
                    self.settings.n8n_webhook_url = webhook
                    with self.assertNoLogs(self.slack_bot.log, level="WARNING"):
                        post = self.forward_response(self.response(202, body))
                    self.assertEqual(
                        post.call_args.args[0],
                        webhook or "http://director.invalid/v1/intake",
                    )
                    self.client.chat_postMessage.assert_called_once()
                    message = self.client.chat_postMessage.call_args.kwargs
                    self.assertIn(f"{task_id} — ACCEPTED", message["text"])
                    self.assertNotIn("BLOCKED", message["text"])
                    self.assertEqual(message["thread_ts"], self.event["thread_ts"])

    def test_replayed_receipt_does_not_invent_a_queued_task_state(self):
        self.forward_response(self.response(202, {
            "task_id": PRIMARY_ID, "status": "OWNER_DECISION_REQUIRED"
        }))
        message = self.client.chat_postMessage.call_args.kwargs["text"]
        self.assertIn(f"{PRIMARY_ID} — ACCEPTED", message)
        self.assertNotIn("QUEUED", message)

    def test_http_200_never_confirms_acceptance_even_with_a_valid_id(self):
        bodies = (
            {},
            {"error": BODY_MARKER},
            {"data": {}},
            [{"error": BODY_MARKER}],
            {"task_id": NORMAL_ID},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.reset_case()
                with self.assertLogs(self.slack_bot.log, level="WARNING"):
                    self.forward_response(self.response(200, body))
                self.assert_blocked()

    def test_other_http_statuses_never_confirm_acceptance(self):
        for status in (201, 204, 301, 400, 500):
            with self.subTest(status=status):
                self.reset_case()
                with self.assertLogs(self.slack_bot.log, level="WARNING"):
                    self.forward_response(self.response(status, {"task_id": NORMAL_ID}))
                self.assert_blocked()

    def test_http_202_rejects_malformed_or_ambiguous_receipts(self):
        bodies = (
            None, True, 202, BODY_MARKER, {}, [], [None], [[{"task_id": NORMAL_ID}]],
            [{"task_id": NORMAL_ID}, {"task_id": NORMAL_ID}],
            [{"task_id": NORMAL_ID}, {"task_id": PRIMARY_ID}],
            {"data": {}}, {"data": None}, {"data": []},
            {"data": {"data": {"task_id": NORMAL_ID}}},
            {"task_id": NORMAL_ID, "data": BODY_MARKER},
            {"task_id": NORMAL_ID, "data": {"task_id": PRIMARY_ID}},
            {"task_id": NORMAL_ID, "data": {"task_id": ""}},
            {"task_id": "", "data": {"task_id": NORMAL_ID}},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.reset_case()
                with self.assertLogs(self.slack_bot.log, level="WARNING"):
                    self.forward_response(self.response(202, body))
                self.assert_blocked()

    def test_http_202_rejects_invalid_task_ids_without_coercion(self):
        invalid_ids = (
            None, False, 123, [], {}, "", "queued", "DIR-", "DIR-LG-",
            "dir-a1b2c3d4e5", "DIR-a1b2c3d4e5", "DIR-G123", " DIR-ABC",
            "DIR-ABC ", "DIR-ABC\n", "DIR-ABC<!channel>", BODY_MARKER,
        )
        for task_id in invalid_ids:
            for envelope in ("top", "nested"):
                with self.subTest(task_id=task_id, envelope=envelope):
                    self.reset_case()
                    body = {"task_id": task_id}
                    if envelope == "nested":
                        body = {"data": body}
                    with self.assertLogs(self.slack_bot.log, level="WARNING"):
                        self.forward_response(self.response(202, body))
                    self.assert_blocked()

    def test_malformed_json_and_response_values_are_not_logged_or_echoed(self):
        responses = (
            httpx.Response(
                202,
                content=f"not-json {BODY_MARKER} {SHARED_SECRET}",
                request=httpx.Request("POST", "http://director.invalid/v1/intake"),
            ),
            self.response(202, {"task_id": BODY_MARKER, "detail": GATEWAY_SECRET}),
        )
        for response in responses:
            with self.subTest(response=response):
                self.reset_case()
                with self.assertLogs(self.slack_bot.log, level="WARNING") as captured:
                    self.forward_response(response)
                self.assert_blocked()
                self.assert_sanitized(captured)

    def assert_sanitized(self, captured):
        rendered = "\n".join(captured.output)
        rendered += self.client.chat_postMessage.call_args.kwargs["text"]
        for marker in (BODY_MARKER, SHARED_SECRET, GATEWAY_SECRET, self.event["text"]):
            self.assertNotIn(marker, rendered)
        for record in captured.records:
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)
            self.assertIsNone(record.exc_text)

    def test_transport_errors_are_ambiguous_sanitized_and_not_retried(self):
        for error_type in (httpx.ConnectError, httpx.ReadTimeout):
            with self.subTest(error=error_type.__name__):
                self.reset_case()
                error = error_type(f"{BODY_MARKER} {SHARED_SECRET} {GATEWAY_SECRET}")
                with (
                    patch.object(self.slack_bot.httpx, "post", side_effect=error) as post,
                    self.assertLogs(self.slack_bot.log, level="WARNING") as captured,
                ):
                    self.slack_bot._forward(self.event, self.client, require_prefix=True)
                    self.slack_bot._forward(self.event, self.client, require_prefix=True)
                post.assert_called_once()
                self.assert_blocked()
                self.assert_sanitized(captured)
                self.assertIn(error_type.__name__, captured.output[0])

    def test_duplicate_events_are_suppressed_after_valid_or_unverified_receipts(self):
        for response in (self.response(202, {"task_id": NORMAL_ID}), self.response(200, {})):
            with self.subTest(status=response.status_code):
                self.reset_case()
                with (
                    patch.object(self.slack_bot.httpx, "post", return_value=response) as post,
                    patch.object(self.slack_bot.log, "warning"),
                ):
                    self.slack_bot._forward(self.event, self.client, require_prefix=True)
                    self.slack_bot._forward(self.event, self.client, require_prefix=False)
                post.assert_called_once()
                self.client.chat_postMessage.assert_called_once()

    def test_forward_preserves_intake_signature_headers_and_event_identity(self):
        attestation = {
            "X-Director-Intake-Issued-At": "synthetic-issued-at",
            "X-Director-Intake-Nonce": "synthetic-nonce",
            "X-Director-Intake-Signature": "synthetic-signature",
        }
        with patch.object(
            self.slack_bot, "build_slack_intake_attestation", return_value=attestation
        ) as sign:
            post = self.forward_response(self.response(202, {"task_id": NORMAL_ID}))
        sign.assert_called_once()
        self.assertEqual(sign.call_args.args[:2], (GATEWAY_SECRET, SHARED_SECRET))
        forwarded = post.call_args.kwargs
        self.assertEqual(
            forwarded["headers"],
            {"Authorization": f"Bearer {SHARED_SECRET}", **attestation},
        )
        self.assertEqual(forwarded["json"]["source_event_id"], self.event["client_msg_id"])
        self.assertEqual(forwarded["json"]["text"], self.event["text"])
        self.assertEqual(forwarded["timeout"], 20)

    def test_command_outside_configured_channel_only_acknowledges(self):
        channel_fields = ({"channel_id": "COTHER"}, {"channel_id": ""},
                          {"channel_id": None}, {})
        for fields in channel_fields:
            for user_id in ("UFIXTURE", "UOTHER"):
                for text in ("synthetic work order", f"approve {NORMAL_ID}"):
                    with self.subTest(fields=fields, user_id=user_id, text=text):
                        self.reset_case()
                        ack, respond = Mock(), Mock()
                        command = {**fields, "user_id": user_id, "text": text}
                        with (
                            patch.object(self.slack_bot.httpx, "get") as get,
                            patch.object(self.slack_bot.httpx, "post") as post,
                        ):
                            self.slack_bot.command_handler(
                                ack, command, self.client, respond
                            )
                        ack.assert_called_once_with()
                        respond.assert_not_called()
                        self.assertEqual(self.client.mock_calls, [])
                        get.assert_not_called()
                        post.assert_not_called()
                        self.assertFalse(self.slack_bot._seen_events)

    def test_command_in_configured_channel_retains_non_owner_rejection(self):
        ack, respond = Mock(), Mock()
        command = {
            "channel_id": "CFIXTURE",
            "user_id": "UOTHER",
            "text": f"approve {NORMAL_ID}",
        }
        with (
            patch.object(self.slack_bot.httpx, "get") as get,
            patch.object(self.slack_bot.httpx, "post") as post,
        ):
            self.slack_bot.command_handler(ack, command, self.client, respond)
        ack.assert_called_once_with()
        respond.assert_called_once()
        self.assertEqual(respond.call_args.kwargs, {"response_type": "ephemeral"})
        self.assertIn("only accepts owner commands", respond.call_args.args[0])
        self.assertEqual(self.client.mock_calls, [])
        get.assert_not_called()
        post.assert_not_called()
        self.assertFalse(self.slack_bot._seen_events)

    def test_filtered_events_never_submit_or_acknowledge(self):
        changes = (
            {"bot_id": "BSYNTHETIC"}, {"subtype": "bot_message"},
            {"subtype": "message_changed"}, {"subtype": "message_deleted"},
            {"channel": "COTHER"}, {"user": "UOTHER"},
            {"text": "no required prefix"}, {"text": ""},
        )
        for change in changes:
            with self.subTest(change=change):
                self.reset_case()
                with patch.object(self.slack_bot.httpx, "post") as post:
                    self.slack_bot._forward(
                        {**self.event, **change}, self.client, require_prefix=True
                    )
                post.assert_not_called()
                self.client.chat_postMessage.assert_not_called()
                self.assertFalse(self.slack_bot._seen_events)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 20:28 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 21:33 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 00:19 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 11:40 UTC | END]]
