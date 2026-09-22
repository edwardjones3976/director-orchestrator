# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 00:52 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-22 | 00:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]]
# [[Codex | GPT-6 Astra | 2026-09-14 | 02:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 11:53 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | BEGIN]]
from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from director_orchestrator.approvals import build_slack_intake_attestation
from director_orchestrator.langgraph_primary import ApprovalDecision, ApprovalKind, ApprovalResume
from director_orchestrator.langgraph_postgres import _base_control_plane_ddl
from director_orchestrator.models import IntakeAuthentication, TaskStatus
from director_orchestrator.primary_default import build_offline_parallel_dispatcher
from director_orchestrator.primary_slack_delivery import PrimarySlackDelivery
from director_orchestrator.primary_slack_delivery import parse_retry_after, render_notification
from tests import test_primary_default as primary_fixtures
from tests import test_slack_intake_receipts as slack_fixtures


class _AsyncByteStream(httpx.AsyncByteStream):
    """Synthetic response body driven by an async generator (slow/oversize/truncated)."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        async for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        await self._chunks.aclose()


class PrimarySlackDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = primary_fixtures.DefaultPrimaryApplicationIntegrationTests()
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.use_parallel_graph_completion_services(None)
        self.fixture.application._parallel_dispatcher = build_offline_parallel_dispatcher(
            self.fixture.services, lambda: self.fixture.coordinator)
        self.settings = replace(self.fixture.settings,
            langgraph_primary_postgres_schema=self.fixture.schema,
            langgraph_primary_new_intake_enabled=True,
            slack_channel_id="CDEFAULT", slack_owner_user_id="UOWNER",
            slack_bot_token="synthetic-slack-test-token",
            slack_gateway_secret="synthetic-gateway-signing-secret-32-characters",
            shared_secret="synthetic-shared-secret")
        self.activation = SimpleNamespace(handle=self.fixture.handle,
            coordinator=self.fixture.coordinator, application=self.fixture.application)
        self.posts = []
        self.messages = []
        self.readbacks = []
        self.mode = "success"
        self.delivery = PrimarySlackDelivery(self.activation, self.settings,
            transport=httpx.MockTransport(self.slack))

    @staticmethod
    def native(message):
        """What conversations.replies returns for a stored post: Slack flattens
        every newline in the fallback text to one space and adds block IDs and
        plain_text emoji defaults, while block text and actions stay verbatim."""
        blocks = []
        for index, block in enumerate(message["blocks"]):
            block = {**block, "block_id": f"synthetic{index}"}
            if "text" in block:
                block["text"] = {**block["text"], "emoji": True}
            if "elements" in block:
                block["elements"] = [{**item, "text": {**item["text"], "emoji": True}}
                                     for item in block["elements"]]
            blocks.append(block)
        return {**message, "text": message["text"].replace("\n", " "), "blocks": blocks}

    @staticmethod
    def rate_limited(retry_after="7", body=None, raw=None):
        """Slack's documented 429: ok:false envelope plus Retry-After seconds."""
        headers = {} if retry_after is None else {"Retry-After": retry_after}
        if raw is not None:
            return httpx.Response(429, content=raw, headers=headers)
        return httpx.Response(429, json={"ok": False, "error": "ratelimited"} if body is None else body,
                              headers=headers)

    def slack(self, request):
        if request.url.path.endswith("auth.test"):
            if self.mode == "auth_rate_limited":
                return self.rate_limited()
            return httpx.Response(200, json={"ok": True, "user_id": "UBOT", "bot_id": "BBOT"})
        if request.url.path.endswith("chat.postMessage"):
            payload = json.loads(request.content)
            self.posts.append(payload)
            ts = f"1720000001.{len(self.posts):06d}"
            message = {**payload, "ts": ts, "user": "UBOT"}
            if self.mode in {"success", "ack_lost_but_posted", "readback_rate_limited"}:
                self.messages.append(message)
            if self.mode in {"timeout", "ack_lost_but_posted"}:
                raise httpx.ReadTimeout("synthetic acknowledgement timeout")
            if self.mode == "refused":
                return httpx.Response(200, json={"ok": False, "error": "missing_scope"})
            if self.mode == "post_rate_limited":
                return self.rate_limited()
            if self.mode == "post_429_without_envelope":
                return self.rate_limited(body={"error": "unexplained"})
            if self.mode == "post_429_non_json":
                return self.rate_limited(retry_after="3601", raw=b"<html>Too Many Requests</html>")
            if self.mode == "post_429_malformed_json":
                return self.rate_limited(retry_after="45", raw=b'{"ok": false, "error": "ratelimited"')
            if self.mode == "post_429_long_delay":
                return self.rate_limited(retry_after="86400")
            if self.mode == "post_429_slow_body":
                # A body that never finishes: only pre-drain classification survives it.
                async def stalled():
                    yield b'{"ok": false'
                    await asyncio.sleep(3600)
                    yield b"}"
                return httpx.Response(429, headers={"Retry-After": "9"}, stream=_AsyncByteStream(stalled()))
            if self.mode == "post_429_oversize_body":
                async def oversize():
                    for _ in range(300):
                        yield b"x" * 1024
                return httpx.Response(429, headers={"Retry-After": "11"}, stream=_AsyncByteStream(oversize()))
            if self.mode == "post_429_truncated_body":
                async def truncated():
                    yield b'{"ok": fal'
                    raise httpx.ReadError("synthetic truncated 429 body")
                return httpx.Response(429, headers={"Retry-After": "13"}, stream=_AsyncByteStream(truncated()))
            return httpx.Response(200, json={"ok": True, "ts": ts, "channel": payload["channel"]})
        if request.url.path.endswith("conversations.replies"):
            self.readbacks.append(request)
            if self.mode == "readback_rate_limited":
                return self.rate_limited(retry_after="3")
            return httpx.Response(200, json={"ok": True, "messages": self.messages})
        raise AssertionError("unexpected network route: " + str(request.url))

    def intake(self, authenticated=True):
        return self.fixture.application.create_intake(self.fixture.intake(),
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED if authenticated
                else IntakeAuthentication.BEARER_DRY_RUN)

    def drain(self):
        self.fixture.coordinator.renew_new_intake()
        return asyncio.run(self.delivery.run_once())

    def approve(self, task):
        pending = self.fixture.coordinator.get_projection(task.task_id).projection["pending_approval"]
        return self.fixture.application.resume_approval(ApprovalResume(task_id=task.task_id,
            kind=ApprovalKind(pending["kind"]), request_digest=pending["request_digest"],
            approval_event_id="fixture-owner-approval", decision=ApprovalDecision.APPROVE))

    def test_actual_intake_signed_button_and_final_result_through_fastapi(self):
        # Load main safely, then route real requests to the actual test primary
        # application. Only Slack/network and startup deployment are doubled.
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SQLITE_PATH": directory + "/legacy.db"}, clear=True):
                main = importlib.import_module("director_orchestrator.main")
            with (patch.object(main, "settings", self.settings),
                  patch.object(main, "primary_default_activation", self.activation),
                  patch.object(main, "primary_slack_delivery", self.delivery)):
                api = TestClient(main.app)
                payload = self.fixture.intake()
                headers = {"Authorization": "Bearer " + self.settings.shared_secret,
                    **build_slack_intake_attestation(self.settings.slack_gateway_secret,
                                                    self.settings.shared_secret, payload)}
                partial = {"Authorization": headers["Authorization"],
                           "X-Director-Intake-Nonce": "partial-synthetic-attestation"}
                self.assertEqual(403, api.post("/v1/intake", headers=partial,
                    json=payload.model_dump(mode="json")).status_code)
                invalid = dict(headers)
                signature_name = next(name for name in headers if "signature" in name.lower())
                invalid[signature_name] = "0" * 64
                self.assertEqual(403, api.post("/v1/intake", headers=invalid,
                    json=payload.model_dump(mode="json")).status_code)
                first = api.post("/v1/intake", headers=headers, json=payload.model_dump(mode="json"))
                self.assertEqual(202, first.status_code, first.text)
                self.assertEqual("slack_gateway_attested", first.json()["intake_authentication"])
                task_id = first.json()["task_id"]
                replay = api.post("/v1/intake", headers=headers, json=payload.model_dump(mode="json"))
                self.assertEqual(task_id, replay.json()["task_id"])
                self.drain()
                self.assertEqual(1, len(self.posts))
                button = self.posts[0]["blocks"][1]["elements"][0]
                gateway_fixture = slack_fixtures.SlackIntakeReceiptTests()
                gateway_fixture.setUp()
                gateway = gateway_fixture.slack_bot
                gateway.settings = self.settings
                client = Mock()
                def submit(url, **kwargs):
                    response = api.post(url.removeprefix(self.settings.orchestrator_base_url), **kwargs)
                    self.assertEqual(200, response.status_code, response.text)
                    return response
                with (patch.object(gateway.httpx, "get", side_effect=lambda url, **kw:
                        api.get(url.removeprefix(self.settings.orchestrator_base_url), **kw)),
                      patch.object(gateway.httpx, "post", side_effect=submit)):
                    gateway.primary_approval_button_handler(Mock(), {
                        "user": {"id": "UOWNER"}, "channel": {"id": "CDEFAULT"},
                        "actions": [{**button, "action_ts": "1720000002.000001"}]}, client)
                current = api.get(f"/v1/tasks/{task_id}", headers=headers)
                self.assertEqual("DONE", current.json()["status"], current.text)
                self.drain()
                self.drain()
                self.assertEqual(2, len(self.posts))
                self.assertIn("SUCCEEDED", self.posts[-1]["text"])
                self.assertIn("SIMULATION", self.posts[-1]["text"])
                self.assertNotIn("$0", self.posts[-1]["text"])
                receipts = api.get(f"/v1/primary/tasks/{task_id}/deliveries", headers=headers)
                self.assertEqual(200, receipts.status_code)
                rows = receipts.json()
                self.assertEqual(["DELIVERED", "DELIVERED"], [r["state"] for r in rows])
                # Identify the approval-bound receipt by its actual displayed
                # button value and the exact production key, never by row
                # position: receipts() only orders by created_at.
                button_value = json.loads(button["value"])
                approval_key = ("approval:" + button_value["request_digest"] + ":"
                                 + button_value["projection_digest"])
                bound = [r for r in rows if r["notification_key"] == approval_key]
                identities = [(r["notification_key"], r["state"], r["created_at"]) for r in rows]
                self.assertEqual(1, len(bound),
                    f"expected exactly one receipt bound to {approval_key!r}; observed {identities!r}")
                approval_receipt = bound[0]
                self.assertEqual(task_id, approval_receipt["task_id"])
                self.assertEqual(button_value["projection_digest"], approval_receipt["projection_digest"])
                self.assertIsNotNone(approval_receipt["owner_decision_at"], identities)
                self.assertEqual("approve", approval_receipt["owner_decision"], identities)
                self.assertEqual("1720000002.000001", approval_receipt["owner_command_id"], identities)
                terminal = [r for r in rows if r["notification_key"] != approval_key]
                self.assertEqual(1, len(terminal), identities)
                self.assertIsNone(terminal[0]["owner_decision"], identities)
                self.assertIsNone(terminal[0]["owner_decision_at"], identities)
                self.assertIsNone(terminal[0]["owner_command_id"], identities)
                self.assertEqual(401, api.get(f"/v1/primary/tasks/{task_id}/deliveries").status_code)
                self.assertNotIn("payload", receipts.text)
                # A displayed old request cannot approve a new/current decision.
                with patch.object(gateway.httpx, "get", return_value=current), patch.object(gateway.httpx, "post") as post:
                    gateway.primary_approval_button_handler(Mock(), {
                        "user": {"id": "UOWNER"}, "channel": {"id": "CDEFAULT"},
                        "actions": [{**button, "action_ts": "1720000002.000002"}]}, client)
                    post.assert_not_called()

    def test_relay_redelivery_after_lost_response_preserves_the_delivered_effect(self):
        # Relay partial-failure replay: the approval post (an external effect)
        # completed, then the relay's accepted response was lost and n8n
        # redelivers the same signed Slack event through the API boundary.
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SQLITE_PATH": directory + "/legacy.db"}, clear=True):
                main = importlib.import_module("director_orchestrator.main")
            with (patch.object(main, "settings", self.settings),
                  patch.object(main, "primary_default_activation", self.activation),
                  patch.object(main, "primary_slack_delivery", self.delivery)):
                api = TestClient(main.app)
                payload = self.fixture.intake()
                headers = {"Authorization": "Bearer " + self.settings.shared_secret,
                    **build_slack_intake_attestation(self.settings.slack_gateway_secret,
                                                    self.settings.shared_secret, payload)}
                body = payload.model_dump(mode="json")
                first = api.post("/v1/intake", headers=headers, json=body)
                self.assertEqual(202, first.status_code, first.text)
                task_id = first.json()["task_id"]
                self.assertEqual(1, self.drain())
                before = self.delivery.receipts(task_id)
                self.assertEqual(["DELIVERED"], [row["state"] for row in before])
                self.assertEqual(1, len(self.posts))
                # Redelivery (twice, as Slack/n8n may retry) after the effect completed.
                for _ in range(2):
                    replay = api.post("/v1/intake", headers=headers, json=body)
                    self.assertEqual(202, replay.status_code, replay.text)
                    self.assertEqual(task_id, replay.json()["task_id"])
                self.assertEqual(0, self.drain())
                # One task, one delivery row, one post, one readback; identities correlate.
                self.assertEqual(before, self.delivery.receipts(task_id))
                self.assertEqual(1, len(self.posts))
                self.assertEqual(1, len(self.readbacks))
                self.assertEqual(before[0]["client_msg_id"], self.posts[0]["client_msg_id"])
                self.assertEqual(before[0]["slack_ts"], self.messages[0]["ts"])
                self.assertEqual(task_id, before[0]["task_id"])
                current = api.get(f"/v1/tasks/{task_id}", headers=headers)
                self.assertEqual(200, current.status_code, current.text)
                self.assertEqual(task_id, current.json()["task_id"])
                self.assertEqual("langgraph", current.headers.get("x-task-engine-owner"))

    def decision_receipt(self, record, decision="approve", command="fixture-command"):
        projection = self.fixture.coordinator.get_projection(record.task_id)
        pending = projection.projection["pending_approval"]
        request = SimpleNamespace(task_id=record.task_id, decision=decision, command_id=command,
            command_digest="sha256:" + "c" * 64, request_digest=pending["request_digest"],
            expected_projection_digest=projection.projection_digest)
        self.fixture.coordinator.record_command_receipt(task_id=record.task_id, operation="approval_resume",
            command_id=request.command_id, command_digest=request.command_digest, projection=projection)
        return request

    def test_owner_approve_and_deny_are_recorded_once_with_command_binding(self):
        for decision, stored in (("approve", "approve"), ("deny", "reject")):
            # A fresh event selects a fresh task for each decision.
            payload = self.fixture.intake().model_copy(update={"source_event_id": "fixture-" + decision})
            record = self.fixture.application.create_intake(payload,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED)
            self.drain()
            request = self.decision_receipt(record, decision, "command-" + decision)
            self.delivery.record_owner_decision(request)
            before = self.delivery.receipts(record.task_id)[0]
            self.assertEqual(stored, before["owner_decision"])
            self.assertIsNotNone(before["owner_decision_at"])
            self.delivery.record_owner_decision(request)
            self.assertEqual(before, self.delivery.receipts(record.task_id)[0])
            with self.assertRaisesRegex(Exception, "evidence is immutable"):
                with self.fixture.handle.pool.connection() as connection:
                    connection.execute(f"UPDATE {self.fixture.schema}.director_primary_slack_delivery "
                        "SET owner_decision=%s WHERE task_id=%s", ("reject" if stored == "approve" else "approve", record.task_id))

    def test_missing_or_mismatched_command_receipt_cannot_record_decision(self):
        record = self.intake()
        self.drain()
        request = self.decision_receipt(record)
        request.command_digest = "sha256:" + "e" * 64
        self.delivery.record_owner_decision(request)
        self.assertIsNone(self.delivery.receipts(record.task_id)[0]["owner_decision"])
        request.command_id = "absent-command"
        self.delivery.record_owner_decision(request)
        self.assertIsNone(self.delivery.receipts(record.task_id)[0]["owner_decision_at"])

    def test_owner_decision_migration_is_additive_idempotent_and_keeps_legacy_unknown(self):
        record = self.intake()
        self.drain()
        before = self.delivery.receipts(record.task_id)[0]
        with self.fixture.handle.pool.connection() as connection:
            # Disposable fixture only: reproduce the pre-column table shape.
            connection.execute(f"ALTER TABLE {self.fixture.schema}.director_primary_slack_delivery DROP COLUMN owner_decision")
            for _ in range(2):
                for statement in _base_control_plane_ddl(self.fixture.schema):
                    connection.execute(statement)
        self.assertEqual(before, self.delivery.receipts(record.task_id)[0])
        self.assertIsNone(self.delivery.receipts(record.task_id)[0]["owner_decision"])
        with self.assertRaises(Exception):
            with self.fixture.handle.pool.connection() as connection:
                connection.execute(f"UPDATE {self.fixture.schema}.director_primary_slack_delivery SET owner_decision='deny'")

    def test_unsigned_canary_and_wrong_channel_do_not_post(self):
        record = self.intake(authenticated=False)
        self.drain()
        self.assertEqual([], self.posts)
        self.assertEqual("SUPPRESSED", self.delivery.receipts(record.task_id)[0]["state"])

    def use_native_readback(self, alter=None):
        def native_response(request):
            response = self.slack(request)
            if request.url.path.endswith("conversations.replies"):
                messages = [self.native(message) for message in self.messages]
                if alter:
                    for message in messages:
                        alter(message)
                return httpx.Response(200, json={"ok": True, "messages": messages})
            return response
        self.delivery.transport = httpx.MockTransport(native_response)

    def test_native_newline_normalization_verifies_approval_and_terminal_once(self):
        self.use_native_readback()
        record = self.intake()
        self.drain()
        self.assertEqual("DELIVERED", self.delivery.receipts(record.task_id)[0]["state"])
        self.approve(record)
        self.drain()
        self.drain()
        receipts = self.delivery.receipts(record.task_id)
        self.assertEqual(["DELIVERED", "DELIVERED"], [row["state"] for row in receipts])
        self.assertEqual([1, 1], [row["readback_attempts"] for row in receipts])
        self.assertEqual(2, len(self.posts))
        self.assertEqual(2, len(self.readbacks))
        self.assertIn("SUCCEEDED", self.posts[-1]["text"])
        self.assertTrue(all("\n" in post["text"] for post in self.posts))
        self.assertTrue(all(self.native(post)["text"] != post["text"] for post in self.posts))

    def test_native_readback_resolves_lost_ack_without_reposting(self):
        self.use_native_readback()
        self.mode = "ack_lost_but_posted"
        record = self.intake()
        self.drain()
        self.drain()
        self.assertEqual("DELIVERED", self.delivery.receipts(record.task_id)[0]["state"])
        self.assertEqual(1, len(self.posts))

    def assert_native_readback_rejected(self, alter):
        self.use_native_readback(alter)
        record = self.intake()
        self.drain()
        self.drain()
        self.drain()
        receipt = self.delivery.receipts(record.task_id)[0]
        self.assertEqual("ACKNOWLEDGED", receipt["state"])
        self.assertEqual("readback_unverified", receipt["last_error"])
        self.assertEqual(2, receipt["readback_attempts"])
        self.assertEqual(2, len(self.readbacks))
        self.assertEqual(1, len(self.posts))

    def test_native_readback_rejects_added_fallback_words(self):
        self.assert_native_readback_rejected(lambda item: item.update(text=item["text"] + " unexpected"))

    def test_native_readback_rejects_missing_fallback_words(self):
        self.assert_native_readback_rejected(lambda item: item.update(text=item["text"].replace("OWNER DECISION REQUIRED", "OWNER DECISION")))

    def test_native_readback_does_not_normalize_other_whitespace(self):
        self.assert_native_readback_rejected(lambda item: item.update(text=item["text"].replace(" ", "\t", 1)))

    def test_native_readback_rejects_changed_block_text(self):
        self.assert_native_readback_rejected(lambda item: item["blocks"][0]["text"].update(text="different visible plan"))

    def test_native_readback_rejects_changed_block_type(self):
        self.assert_native_readback_rejected(lambda item: item["blocks"][0].update(type="context"))

    def test_native_readback_rejects_changed_button_label(self):
        self.assert_native_readback_rejected(lambda item: item["blocks"][-1]["elements"][0]["text"].update(text="Approve another request"))

    def test_native_readback_rejects_changed_button_value(self):
        self.assert_native_readback_rejected(lambda item: item["blocks"][-1]["elements"][0].update(value="different-scope"))

    def test_native_readback_rejects_changed_button_action(self):
        self.assert_native_readback_rejected(lambda item: item["blocks"][-1]["elements"][0].update(action_id="director_primary_reject"))

    def test_native_readback_rejects_other_bot(self):
        self.assert_native_readback_rejected(lambda item: item.update(user="UOTHER"))

    def test_native_readback_rejects_other_client_id(self):
        self.assert_native_readback_rejected(lambda item: item.update(client_msg_id="different-message"))

    def test_native_readback_rejects_other_thread(self):
        self.assert_native_readback_rejected(lambda item: item.update(thread_ts="1720000000.999999"))

    def test_native_readback_rejects_missing_thread(self):
        self.assert_native_readback_rejected(lambda item: item.pop("thread_ts"))

    def test_native_readback_rejects_other_timestamp(self):
        self.assert_native_readback_rejected(lambda item: item.update(ts="1720000999.000001"))

    def test_ambiguous_post_is_not_retried_after_restart(self):
        record = self.intake()
        self.mode = "timeout"
        self.drain()
        self.delivery = PrimarySlackDelivery(self.activation, self.settings,
            transport=httpx.MockTransport(self.slack))
        self.mode = "success"
        self.drain()
        self.assertEqual(1, len(self.posts))
        self.assertEqual("AMBIGUOUS", self.delivery.receipts(record.task_id)[0]["state"])

    def test_lost_ack_can_be_resolved_by_readback_without_reposting(self):
        record = self.intake()
        self.mode = "ack_lost_but_posted"
        self.drain()
        self.assertEqual(1, len(self.posts))
        self.assertEqual("DELIVERED", self.delivery.receipts(record.task_id)[0]["state"])
        self.drain()
        self.assertEqual(1, len(self.posts))

    def test_claim_survives_restart_and_cancel_produces_one_terminal_reply(self):
        record = self.intake()
        self.delivery.enqueue()
        self.delivery.bot_user_id = "UBOT"  # Synthetic auth boundary for crash injection.
        claimed = self.delivery.claim()
        self.assertIsNotNone(claimed)
        self.delivery = PrimarySlackDelivery(self.activation, self.settings,
            transport=httpx.MockTransport(self.slack))
        self.drain()
        self.assertEqual([], self.posts)
        self.fixture.application.cancel(record.task_id)
        self.drain()
        self.drain()
        self.assertEqual(1, len(self.posts))
        self.assertIn("CANCELLED", self.posts[0]["text"])

    def test_cancel_supersedes_unsent_approval(self):
        record = self.intake()
        self.delivery.enqueue()
        self.fixture.application.cancel(record.task_id)
        self.drain()
        self.assertEqual(1, len(self.posts))
        self.assertNotIn("actions", json.dumps(self.posts[0]["blocks"]))
        self.assertEqual(["SUPERSEDED", "DELIVERED"], [r["state"] for r in self.delivery.receipts(record.task_id)])

    def test_foreign_intake_owner_cannot_claim_a_post(self):
        self.intake()
        self.delivery.owner_token = "not-the-active-owner"
        self.drain()
        self.assertEqual([], self.posts)

    def test_known_slack_refusal_is_recorded_without_retry(self):
        record = self.intake()
        self.mode = "refused"
        self.drain()
        self.drain()
        self.assertEqual(1, len(self.posts))
        self.assertEqual("REFUSED", self.delivery.receipts(record.task_id)[0]["state"])

    def test_retry_after_header_is_honored_in_full_with_a_conservative_default(self):
        self.assertEqual(7, parse_retry_after("7"))
        self.assertEqual(7, parse_retry_after(" 7 "))
        self.assertEqual(1, parse_retry_after("0"))
        # Slack's full requested delay, never shortened to a worker-sized ceiling.
        self.assertEqual(601, parse_retry_after("601"))
        self.assertEqual(3601, parse_retry_after("3601"))
        self.assertEqual(86400, parse_retry_after("86400"))
        self.assertEqual(999999999, parse_retry_after("999999999"))
        for value in (None, "", "abc", "-5", "1.5", "1234567890", "Wed, 21 Sep 2026 20:00:00 GMT"):
            with self.subTest(value=value):
                self.assertEqual(30, parse_retry_after(value))

    def test_rate_limited_post_is_a_definite_refusal_honoring_retry_after_without_repost(self):
        first = self.intake()
        second = self.fixture.application.create_intake(
            self.fixture.intake().model_copy(update={"source_event_id": "fixture-second-event"}),
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED)
        self.mode = "post_rate_limited"
        self.assertEqual(0, self.drain())
        # Exactly one post was attempted; Slack said 429 and did not post it.
        self.assertEqual(1, len(self.posts))
        self.assertEqual([], self.readbacks)
        states = {task.task_id: self.delivery.receipts(task.task_id)[0] for task in (first, second)}
        self.assertEqual(["PENDING", "REFUSED"], sorted(row["state"] for row in states.values()))
        refused_id = next(task_id for task_id, row in states.items() if row["state"] == "REFUSED")
        withheld_id = next(task_id for task_id, row in states.items() if row["state"] == "PENDING")
        self.assertEqual("slack_rate_limited:retry_after=7", states[refused_id]["last_error"])
        self.assertIsNone(states[refused_id]["slack_ts"])
        # The other row was never claimed, so it cannot age into AMBIGUOUS.
        self.assertTrue(self.delivery.health()["rate_limited"])
        self.assertEqual(7, self.delivery.last_retry_after_seconds)
        # Inside the horizon: zero Slack calls, zero claims, even when Slack recovers.
        self.mode = "success"
        self.assertEqual(0, self.drain())
        self.assertEqual(1, len(self.posts))
        self.assertEqual("PENDING", self.delivery.receipts(withheld_id)[0]["state"])
        # After the horizon: the withheld row posts once; the refused row is never reposted.
        self.delivery.rate_limited_until = time.monotonic() - 1
        self.assertEqual(1, self.drain())
        self.assertEqual(2, len(self.posts))
        self.assertEqual("REFUSED", self.delivery.receipts(refused_id)[0]["state"])
        self.assertEqual("DELIVERED", self.delivery.receipts(withheld_id)[0]["state"])
        self.assertFalse(self.delivery.health()["rate_limited"])
        with self.assertRaisesRegex(Exception, "cannot be retried or rewound"):
            with self.fixture.handle.pool.connection() as connection:
                connection.execute(f"UPDATE {self.fixture.schema}.director_primary_slack_delivery "
                    "SET state='PENDING' WHERE task_id=%s", (refused_id,))

    def test_429_is_a_definite_refusal_independent_of_its_body_and_never_reposted(self):
        # Slack's documented rule keys on the status; envelope-less, non-JSON and
        # malformed-JSON bodies are all definite non-acceptance, never ambiguity.
        for mode, expected in (("post_429_without_envelope", "slack_rate_limited:retry_after=7"),
                               ("post_429_non_json", "slack_rate_limited:retry_after=3601"),
                               ("post_429_malformed_json", "slack_rate_limited:retry_after=45")):
            with self.subTest(mode=mode):
                self.posts.clear()
                record = self.fixture.application.create_intake(
                    self.fixture.intake().model_copy(update={"source_event_id": "fixture-" + mode}),
                    intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED)
                self.delivery.rate_limited_until = None
                self.mode = mode
                self.assertEqual(0, self.drain())
                self.assertEqual(1, len(self.posts))
                receipt = self.delivery.receipts(record.task_id)[0]
                self.assertEqual("REFUSED", receipt["state"])
                self.assertEqual(expected, receipt["last_error"])
                self.assertTrue(self.delivery.health()["rate_limited"])
                self.mode = "success"
                self.assertEqual(0, self.drain())  # inside the horizon: no call, no repost
                self.assertEqual(1, len(self.posts))
                self.assertEqual("REFUSED", self.delivery.receipts(record.task_id)[0]["state"])

    def test_429_is_classified_before_the_body_so_slow_oversize_or_truncated_bodies_cannot_stall_or_blur_it(self):
        for mode, expected in (("post_429_slow_body", "slack_rate_limited:retry_after=9"),
                               ("post_429_oversize_body", "slack_rate_limited:retry_after=11"),
                               ("post_429_truncated_body", "slack_rate_limited:retry_after=13")):
            with self.subTest(mode=mode):
                self.posts.clear()
                record = self.fixture.application.create_intake(
                    self.fixture.intake().model_copy(update={"source_event_id": "fixture-" + mode}),
                    intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED)
                self.delivery.rate_limited_until = None
                self.mode = mode
                started = time.monotonic()
                self.assertEqual(0, self.drain())
                self.assertLess(time.monotonic() - started, 5)  # the ten-second body deadline never ran
                self.assertEqual(1, len(self.posts))
                receipt = self.delivery.receipts(record.task_id)[0]
                self.assertEqual("REFUSED", receipt["state"])
                self.assertEqual(expected, receipt["last_error"])
                self.assertTrue(self.delivery.health()["rate_limited"])
                self.mode = "success"
                self.assertEqual(0, self.drain())
                self.assertEqual(1, len(self.posts))

    def test_retry_after_longer_than_the_worker_horizon_defers_without_truncation(self):
        record = self.intake()
        self.mode = "post_429_long_delay"
        self.assertEqual(0, self.drain())
        self.assertEqual(86400, self.delivery.last_retry_after_seconds)
        self.assertGreater(self.delivery.rate_limited_until, time.monotonic() + 86000)
        self.assertEqual("slack_rate_limited:retry_after=86400", self.delivery.receipts(record.task_id)[0]["last_error"])
        second = self.fixture.application.create_intake(
            self.fixture.intake().model_copy(update={"source_event_id": "fixture-deferred-event"}),
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED)
        self.mode = "success"
        for _ in range(3):
            self.assertEqual(0, self.drain())
        self.assertEqual(1, len(self.posts))
        self.assertEqual("PENDING", self.delivery.receipts(second.task_id)[0]["state"])
        self.assertTrue(self.delivery.health()["rate_limited"])

    def test_rate_limited_identity_check_keeps_bounded_attempts_and_claims_nothing(self):
        record = self.intake()
        self.mode = "auth_rate_limited"
        self.assertEqual(0, self.drain())
        self.assertEqual([], self.posts)
        self.assertEqual("PENDING", self.delivery.receipts(record.task_id)[0]["state"])
        self.assertIsNone(self.delivery.bot_user_id)
        self.assertEqual(0, self.delivery._auth_attempts)
        self.assertTrue(self.delivery.health()["rate_limited"])
        self.mode = "success"
        self.assertEqual(0, self.drain())
        self.assertEqual([], self.posts)
        self.delivery.rate_limited_until = time.monotonic() - 1
        self.assertEqual(1, self.drain())
        self.assertEqual("DELIVERED", self.delivery.receipts(record.task_id)[0]["state"])

    def test_rate_limited_readback_consumes_one_attempt_and_resolves_later_without_repost(self):
        record = self.intake()
        self.mode = "readback_rate_limited"
        self.assertEqual(1, self.drain())
        receipt = self.delivery.receipts(record.task_id)[0]
        self.assertEqual("ACKNOWLEDGED", receipt["state"])
        self.assertEqual(1, receipt["readback_attempts"])
        self.assertEqual("slack_rate_limited:retry_after=3", receipt["last_error"])
        self.assertEqual(1, len(self.readbacks))
        self.mode = "success"
        self.drain()
        self.assertEqual(1, len(self.readbacks))  # horizon: no second read yet
        self.delivery.rate_limited_until = time.monotonic() - 1
        self.drain()
        self.assertEqual(2, len(self.readbacks))
        self.assertEqual(1, len(self.posts))
        self.assertEqual("DELIVERED", self.delivery.receipts(record.task_id)[0]["state"])

    def test_concurrent_workers_claim_one_attempt_and_database_prevents_rewind(self):
        self.intake()
        self.delivery.enqueue()
        self.delivery.bot_user_id = "UBOT"  # Synthetic auth boundary for concurrent claims.
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: self.delivery.claim(), range(2)))
        self.assertEqual(1, sum(row is not None for row in claims))
        with self.assertRaisesRegex(Exception, "cannot be retried or rewound"):
            with self.fixture.handle.pool.connection() as connection:
                connection.execute(f"UPDATE {self.fixture.schema}.director_primary_slack_delivery SET state='PENDING'")
        with self.assertRaisesRegex(Exception, "identity is immutable"):
            with self.fixture.handle.pool.connection() as connection:
                connection.execute(f"UPDATE {self.fixture.schema}.director_primary_slack_delivery SET payload='{{}}'::jsonb")

    def test_partial_remote_readback_cannot_verify_the_displayed_plan(self):
        record = self.intake()
        def altered_readback(request):
            result = self.slack(request)
            if request.url.path.endswith("conversations.replies"):
                messages = [dict(message, blocks=[]) for message in self.messages]
                return httpx.Response(200, json={"ok": True, "messages": messages})
            return result
        self.delivery.transport = httpx.MockTransport(altered_readback)
        self.drain()
        self.drain()
        self.assertEqual(1, len(self.posts))
        receipt = self.delivery.receipts(record.task_id)[0]
        self.assertEqual("ACKNOWLEDGED", receipt["state"])
        self.assertEqual(2, receipt["readback_attempts"])

    def test_unsigned_http_canary_never_becomes_authenticated_slack(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SQLITE_PATH": directory + "/legacy.db"}, clear=True):
                main = importlib.import_module("director_orchestrator.main")
            with (patch.object(main, "settings", self.settings),
                  patch.object(main, "primary_default_activation", self.activation)):
                response = TestClient(main.app).post("/v1/intake",
                    headers={"Authorization": "Bearer " + self.settings.shared_secret},
                    json=self.fixture.intake().model_dump(mode="json"))
                self.assertEqual(202, response.status_code)
                self.assertEqual("bearer_dry_run", response.json()["intake_authentication"])
        self.drain()
        self.assertEqual([], self.posts)

    def test_full_external_grant_is_displayed_and_missing_grant_cannot_offer_approval(self):
        record = self.intake()
        projection = self.fixture.coordinator.get_projection(record.task_id)
        payload = dict(projection.projection)
        payload["pending_approval"] = {**payload["pending_approval"], "kind": "external_effect"}
        row = {"projection": payload, "projection_digest": projection.projection_digest,
               "created_at": record.created_at}
        missing = render_notification(row, self.settings)
        self.assertEqual(["director_primary_reject"],
            [item["action_id"] for item in missing["blocks"][-1]["elements"]])
        grant = {"action": "mark_ready", "repository": "owner/director", "pull_number": 7,
                 "base_sha": "a" * 40, "head_sha": "b" * 40}
        payload["application"] = {**payload["application"], "effect_grant": grant}
        rendered = render_notification(row, self.settings)
        self.assertIn("a" * 40, json.dumps(rendered))
        self.assertIn("b" * 40, json.dumps(rendered))
        self.assertEqual(2, len(rendered["blocks"][-1]["elements"]))

    def test_oversized_approval_does_not_hide_scope_behind_a_button(self):
        record = self.intake()
        projection = self.fixture.coordinator.get_projection(record.task_id)
        raw = projection.projection["application"]["task_record"]
        raw["plan"]["normalized_outcome"] = "Objective " * 1000
        for assignment in raw["plan"]["assignments"]:
            assignment["task"] = "Task " * 1900
        rendered = render_notification({"projection": projection.projection,
            "projection_digest": projection.projection_digest, "created_at": record.created_at}, self.settings)
        self.assertTrue(all(len(block.get("text", {}).get("text", "")) <= 2900
                            for block in rendered["blocks"]))
        self.assertEqual(["director_primary_reject"],
            [item["action_id"] for item in rendered["blocks"][-1]["elements"]])

    def test_recovery_preserves_execution_mode_and_rendering_ignores_new_flags(self):
        record = self.intake()
        projection = self.fixture.coordinator.get_projection(record.task_id)
        self.assertEqual("offline_simulation", projection.projection["application"]["execution_mode"])
        self.fixture.services.settings = replace(self.settings, langgraph_primary_offline_simulation_enabled=False)
        self.fixture.services.hydrate(projection)
        self.assertEqual("offline_simulation", self.fixture.services.application_projection(record.task_id)["execution_mode"])
        projection.projection["application"]["execution_mode"] = "live"
        rendered = render_notification({"projection": projection.projection,
            "projection_digest": projection.projection_digest, "created_at": record.created_at}, self.settings)
        self.assertNotIn("SIMULATION", rendered["text"])

    def test_other_slack_sender_cannot_supply_delivery_readback(self):
        record = self.intake()
        def forged_readback(request):
            response = self.slack(request)
            if request.url.path.endswith("conversations.replies"):
                return httpx.Response(200, json={"ok": True,
                    "messages": [dict(message, user="UOTHER") for message in self.messages]})
            return response
        self.delivery.transport = httpx.MockTransport(forged_readback)
        self.drain()
        self.assertEqual("ACKNOWLEDGED", self.delivery.receipts(record.task_id)[0]["state"])


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | END]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 11:53 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:01 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]]

# [[Codex | GPT-6 Astra | 2026-09-14 | 02:12 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-22 | 00:38 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-22 | 00:52 UTC | END]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | END]]
