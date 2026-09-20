# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 20:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 10:21 UTC | BEGIN]]
"""Offline evidence-boundary fixtures; never invoke the collector's live main."""
from __future__ import annotations

from copy import deepcopy
import base64
from contextlib import redirect_stdout
import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import jsonschema


COLLECTOR_PATH = Path(__file__).resolve().parents[1] / "scripts/collect-offline-slack-trials.py"
SPEC = importlib.util.spec_from_file_location("offline_slack_trial_collector", COLLECTOR_PATH)
collector_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector_module)

THREAD = "1789210000.100000"
FINAL_TS = "1789210030.100000"
TASK_ID = "DIR-LG-AB12"
OWNER = "UOWNER123"
BOT = "UBOT123"
SOURCE_EVENT = "fixture-owner-source-event"
FINAL_ID = "fixture-terminal-client-id"
EMPTY_ATTEMPT_FIELDS = (
    "provider_runs", "provider_run_history", "provider_contact_attempts",
    "provider_contact_attempt_history", "provider_interaction_ledger",
)
ENV = {
    "SLACK_DIRECTOR_CHANNEL_ID": "CFIXTURE",
    "SLACK_BOT_TOKEN": "fixture-slack-secret",
    "ORCHESTRATOR_SHARED_SECRET": "fixture-director-secret",
    "SLACK_OWNER_USER_ID": OWNER,
}


def sample():
    task = {
        "task_id": TASK_ID,
        "intake": {
            "channel_id": ENV["SLACK_DIRECTOR_CHANNEL_ID"], "thread_ts": THREAD,
            "user_id": OWNER, "source_event_id": SOURCE_EVENT, "text": "audit trial",
        },
        "intake_authentication": "slack_gateway_attested",
        "assignment_outcomes": [{
            "run_id": "offline-double:fixture", "result": collector_module.LABEL,
        }],
        **{field: [] for field in EMPTY_ATTEMPT_FIELDS},
    }
    deliveries = [{
        "task_id": TASK_ID, "notification_key": "terminal", "projection_digest": "fixture-digest",
        "client_msg_id": FINAL_ID, "state": "DELIVERED", "slack_ts": FINAL_TS,
        "claimed_at": "2026-09-12T10:00:20+00:00",
        "acknowledged_at": "2026-09-12T10:00:30+00:00",
        "delivered_at": "2026-09-12T10:00:31+00:00", "readback_attempts": 1,
    }]
    messages = [{
        "ts": THREAD, "user": OWNER, "client_msg_id": SOURCE_EVENT,
        "text": "<@UBOT123> audit trial",
    }, {
        "ts": FINAL_TS, "thread_ts": THREAD, "user": BOT,
        "client_msg_id": FINAL_ID,
        "text": TASK_ID + " — SUCCEEDED\n" + collector_module.LABEL,
    }]
    headers = {"x-task-engine-owner": "langgraph", "x-primary-projection-digest": "fixture-digest"}
    return task, deliveries, messages, headers


class ParseTrialsTests(unittest.TestCase):
    def test_accepts_one_and_ten_explicit_distinct_trials(self):
        values = [f"178921000{i}.100000" for i in range(10)]
        self.assertEqual(collector_module.parse_trials(values[:1]), ["1789210000.100000"])
        self.assertEqual(len(collector_module.parse_trials(values)), 10)

    def test_rejects_empty_and_more_than_ten(self):
        for values in ([], [f"17892100{i:02}.100000:DIR-LG-{i:X}" for i in range(11)]):
            with self.subTest(count=len(values)), self.assertRaises(ValueError):
                collector_module.parse_trials(values)

    def test_rejects_duplicate_thread_or_old_task_selector(self):
        for values in (
            [THREAD, THREAD],
            [THREAD + ":" + TASK_ID, FINAL_TS + ":" + TASK_ID],
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                collector_module.parse_trials(values)

    def test_rejects_invalid_selectors(self):
        for value in ("", "no-separator", "12.3:DIR-LG-AA", THREAD + ":DIR-LG-aa",
                      THREAD + ":../DIR-LG-AA", THREAD + ":DIR-LG-AA:extra", THREAD + ":DIR-LG-AA\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                collector_module.parse_trials([value])


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.task, self.deliveries, self.messages, self.headers = sample()

    def summarize(self, *, complete=True):
        return collector_module.summarize(THREAD, TASK_ID, self.task, self.deliveries,
            self.messages, complete, OWNER, BOT, self.headers, ENV["SLACK_DIRECTOR_CHANNEL_ID"])

    def test_binds_actual_owner_source_message_and_observed_final(self):
        report = self.summarize()
        self.assertTrue(report["task_record_bound"])
        self.assertTrue(report["owner_account_input_matched"])
        self.assertEqual(report["input_message_ts"], THREAD)
        self.assertEqual(report["input_client_msg_id"], SOURCE_EVENT)
        self.assertTrue(report["task_api_gateway_attested"])
        self.assertEqual(report["terminal_reply_count"], 1)
        self.assertTrue(report["final_receipt_matches_native_message"])
        self.assertEqual(report["wall_seconds"], 30.0)
        self.assertTrue(report["wall_under_60_seconds"])
        self.assertIsNone(report["human_typing_attested"])

    def test_two_distinct_terminal_timestamps_count_twice_even_with_same_client_id(self):
        duplicate = deepcopy(self.messages[-1])
        duplicate["ts"] = "1789210031.100000"
        self.messages.append(duplicate)
        report = self.summarize()
        self.assertEqual(report["observed_final_message_count"], 2)
        self.assertEqual(report["terminal_reply_count"], 2)
        self.assertFalse(report["final_receipt_matches_native_message"])
        self.assertIsNone(report["wall_seconds"])

    def test_wrong_sender_and_unrelated_task_final_are_not_counted(self):
        self.messages[-1]["user"] = "UOTHER123"
        unrelated = deepcopy(self.messages[-1])
        unrelated.update(user=BOT, client_msg_id="unrelated", text="DIR-LG-CD34 — SUCCEEDED")
        self.messages.append(unrelated)
        report = self.summarize()
        self.assertEqual(report["terminal_reply_count"], 0)
        self.assertFalse(report["final_receipt_matches_native_message"])

    def test_wrong_thread_messages_do_not_bind_inputs_or_finals(self):
        self.messages[0].update(ts="1789210001.100000", thread_ts="1789209999.100000")
        self.messages[-1]["thread_ts"] = "1789209999.100000"
        report = self.summarize()
        self.assertFalse(report["owner_account_input_matched"])
        self.assertEqual(report["terminal_reply_count"], 0)
        self.assertFalse(report["final_receipt_matches_native_message"])

    def test_missing_or_mismatched_source_event_does_not_match_owner_by_text(self):
        for source in (None, "another-source"):
            with self.subTest(source=source):
                self.task["intake"]["source_event_id"] = source
                self.messages[0].pop("client_msg_id", None)
                report = self.summarize()
                self.assertFalse(report["owner_account_input_matched"])
                self.assertIsNone(report["wall_seconds"])

    def test_bot_subtype_or_other_account_cannot_be_owner_input(self):
        original = deepcopy(self.messages[0])
        for mutation in ({"user": BOT}, {"bot_id": "BBOT123"}, {"subtype": "bot_message"}):
            with self.subTest(mutation=mutation):
                self.messages[0] = {**original, **mutation}
                self.assertFalse(self.summarize()["owner_account_input_matched"])

    def test_task_channel_thread_owner_or_engine_mismatch_prevents_correlated_claims(self):
        original_task, original_headers = deepcopy(self.task), deepcopy(self.headers)
        cases = ("task_id", "channel_id", "thread_ts", "user_id", "engine")
        for field in cases:
            with self.subTest(field=field):
                self.task, self.headers = deepcopy(original_task), deepcopy(original_headers)
                if field == "task_id":
                    self.task[field] = "DIR-LG-CD34"
                elif field == "engine":
                    self.headers["x-task-engine-owner"] = "legacy"
                else:
                    self.task["intake"][field] = "mismatch"
                report = self.summarize()
                self.assertFalse(report["task_record_bound"])
                self.assertFalse(report["owner_account_input_matched"])
                self.assertFalse(report["final_receipt_matches_native_message"])
                self.assertIsNone(report["simulation_charge_supported_usd"])

    def test_delivery_for_another_task_cannot_correlate(self):
        self.deliveries[0]["task_id"] = "DIR-LG-CD34"
        report = self.summarize()
        self.assertFalse(report["delivery_receipt_list_complete"])
        self.assertFalse(report["final_receipt_matches_native_message"])

    def test_incomplete_thread_has_observed_count_but_no_exact_count_or_duration(self):
        report = self.summarize(complete=False)
        self.assertEqual(report["observed_final_message_count"], 1)
        self.assertIsNone(report["terminal_reply_count"])
        self.assertFalse(report["final_receipt_matches_native_message"])
        self.assertIsNone(report["wall_seconds"])
        self.assertIsNone(report["wall_under_60_seconds"])

    def test_one_hundred_delivery_rows_do_not_prove_receipt_list_complete(self):
        self.deliveries.extend({"task_id": TASK_ID, "notification_key": f"hold:{index}"}
                               for index in range(99))
        self.assertFalse(self.summarize()["delivery_receipt_list_complete"])

    def test_simulation_zero_support_does_not_claim_actual_billing(self):
        report = self.summarize()
        self.assertTrue(report["all_recorded_outcomes_are_offline_doubles"])
        self.assertTrue(report["zero_calls_in_exposed_task_projection"])
        self.assertEqual(report["simulation_charge_supported_usd"], 0)
        self.assertTrue(report["terminal_messages"][0]["simulation_label_present"])
        self.assertIsNone(report["actual_cost_usd"])
        self.assertIsNone(report["operational_provider_attempts_verified"])

    def test_missing_or_invalid_attempt_lists_leave_zero_support_unknown(self):
        original = deepcopy(self.task)
        for field in EMPTY_ATTEMPT_FIELDS:
            for replacement in ("missing", None, "not-a-list"):
                with self.subTest(field=field, replacement=replacement):
                    self.task = deepcopy(original)
                    if replacement == "missing":
                        self.task.pop(field)
                    else:
                        self.task[field] = replacement
                    report = self.summarize()
                    self.assertIsNone(report["zero_calls_in_exposed_task_projection"])
                    self.assertIsNone(report["simulation_charge_supported_usd"])
                    self.assertIsNone(report["actual_cost_usd"])

    def test_explicit_provider_attempt_prevents_zero_support(self):
        self.task["provider_runs"] = [{"run_id": "recorded-attempt"}]
        report = self.summarize()
        self.assertFalse(report["zero_calls_in_exposed_task_projection"])
        self.assertIsNone(report["simulation_charge_supported_usd"])
        self.assertIsNone(report["actual_cost_usd"])

    def test_approval_timestamp_is_not_approval_or_owner_pause_proof(self):
        self.deliveries.append({
            "task_id": TASK_ID, "notification_key": "approval:request:projection", "state": "PENDING",
            "owner_decision_at": "2026-09-12T10:00:10+00:00", "owner_command_id": "fixture-command",
        })
        report = self.summarize()
        self.assertEqual(report["owner_decision_record_count"], 1)
        self.assertIsNone(report["authenticated_scoped_approval_verified"])
        self.assertIsNone(report["owner_pause_seconds"])
        self.assertIsNone(report["machine_seconds"])

    def test_negative_timestamp_duration_remains_unknown(self):
        self.messages[-1]["ts"] = "1789209999.100000"
        self.deliveries[0]["slack_ts"] = self.messages[-1]["ts"]
        report = self.summarize()
        self.assertIsNone(report["wall_seconds"])
        self.assertIsNone(report["wall_under_60_seconds"])

    def test_current_headers_cannot_prove_execution_image_or_scorecard_pass(self):
        self.headers["x-primary-execution-image"] = "sha256:" + "a" * 64
        self.task["candidate_sha"] = "b" * 40
        report = self.summarize()
        self.assertIsNone(report["execution_image_binding_verified"])
        self.assertIsNone(report["independent_correctness_verified"])
        self.assertIsNone(report["global_task_deduplication_verified"])
        self.assertEqual(report["verdict"], "EVIDENCE_ONLY_NOT_QUALIFIED")


class CollectorTransportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="offline-slack-collector-fixture-")
        self.addCleanup(folder.cleanup)
        self.fixture_root = Path(folder.name)
        self.collector_index = 0
        self.requests = []

    def build_collector(self, handler):
        self.collector_index += 1
        self.output = self.fixture_root / f"collector-{self.collector_index}"
        (self.output / "private").mkdir(parents=True)
        def recording_handler(request):
            self.requests.append(request)
            self.assertEqual(request.method, "GET")
            return handler(request)
        client = httpx.Client(transport=httpx.MockTransport(recording_handler),
                             trust_env=False, follow_redirects=False)
        self.addCleanup(client.close)
        return collector_module.Collector(client, self.output, dict(ENV), self.output / "private")

    def paged_collector(self, pages):
        def handle(request):
            self.assertEqual(str(request.url).split("?")[0], collector_module.SLACK + "conversations.replies")
            self.assertEqual(request.url.params["channel"], ENV["SLACK_DIRECTOR_CHANNEL_ID"])
            self.assertEqual(request.url.params["ts"], THREAD)
            self.assertEqual(request.url.params["limit"], "100")
            return httpx.Response(200, json=pages[len(self.requests) - 1])
        return self.build_collector(handle)

    def test_get_only_allowed_sources_use_correct_credentials(self):
        collector = self.build_collector(lambda request: httpx.Response(200, json={"ok": True}))
        urls = [collector_module.SLACK + "auth.test", collector_module.API + "/health",
                collector_module.API + "/v1/tasks/" + TASK_ID,
                collector_module.API + "/v1/primary/tasks/" + TASK_ID + "/deliveries"]
        for url in urls:
            collector.get(url)
        self.assertEqual(len(self.requests), 4)
        for request in self.requests:
            token = ENV["SLACK_BOT_TOKEN"] if request.url.host == "slack.com" else ENV["ORCHESTRATOR_SHARED_SECRET"]
            self.assertEqual(request.headers["Authorization"], "Bearer " + token)

    def test_wrong_urls_and_mutation_routes_are_rejected_before_transport(self):
        collector = self.build_collector(lambda request: self.fail("Rejected URL reached transport"))
        for url in (collector_module.SLACK + "chat.postMessage", "https://example.com/health",
                    "http://127.0.0.1:8080.evil/health", collector_module.API + "/v1/tasks/" + TASK_ID + "/approve",
                    collector_module.API + "/v1/primary/tasks/" + TASK_ID + "/approvals/resume",
                    collector_module.SLACK + "auth.test?extra=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                collector.get(url)
        self.assertEqual(self.requests, [])

    def test_pagination_passes_cursor_and_deduplicates_same_timestamp(self):
        _, _, messages, _ = sample()
        collector = self.paged_collector([
            {"ok": True, "messages": messages, "has_more": True, "response_metadata": {"next_cursor": "page-2"}},
            {"ok": True, "messages": [messages[-1]], "has_more": False, "response_metadata": {"next_cursor": ""}},
        ])
        observed, complete, error = collector.replies(THREAD)
        self.assertTrue(complete)
        self.assertIsNone(error)
        self.assertEqual(len(observed), 2)
        self.assertEqual(self.requests[1].url.params["cursor"], "page-2")

    def test_page_bound_retains_partial_messages_without_completeness(self):
        _, _, messages, _ = sample()
        collector = self.paged_collector([
            {"ok": True, "messages": [messages[0]], "has_more": True, "response_metadata": {"next_cursor": "page-2"}},
            {"ok": True, "messages": [messages[-1]], "has_more": True, "response_metadata": {"next_cursor": "page-3"}},
        ])
        with patch.object(collector_module, "MAX_PAGES", 2):
            observed, complete, error = collector.replies(THREAD)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(observed), 2)
        self.assertFalse(complete)
        self.assertEqual(error, "page_bound_reached")

    def test_repeated_cursor_stops_without_retrying(self):
        _, _, messages, _ = sample()
        page = {"ok": True, "messages": messages, "has_more": True, "response_metadata": {"next_cursor": "same"}}
        collector = self.paged_collector([page, page])
        observed, complete, error = collector.replies(THREAD)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(observed), 2)
        self.assertFalse(complete)
        self.assertIsNotNone(error)

    def test_changed_timestamp_payload_is_incomplete_and_preserves_first_observation(self):
        _, _, messages, _ = sample()
        changed = {**messages[-1], "text": "edited text"}
        collector = self.paged_collector([
            {"ok": True, "messages": messages, "has_more": True, "response_metadata": {"next_cursor": "page-2"}},
            {"ok": True, "messages": [changed], "has_more": False},
        ])
        observed, complete, error = collector.replies(THREAD)
        self.assertFalse(complete)
        self.assertIsNotNone(error)
        self.assertEqual(observed[-1]["text"], messages[-1]["text"])

    def test_missing_cursor_or_completeness_marker_cannot_claim_complete(self):
        _, _, messages, _ = sample()
        for marker in ({"has_more": True}, {}):
            with self.subTest(marker=marker):
                self.requests.clear()
                collector = self.paged_collector([{"ok": True, "messages": messages, **marker}])
                observed, complete, error = collector.replies(THREAD)
                self.assertEqual(len(observed), 2)
                self.assertFalse(complete)
                self.assertIsNotNone(error)
                self.assertEqual(len(self.requests), 1)

    def test_wrong_thread_reply_makes_snapshot_incomplete(self):
        _, _, messages, _ = sample()
        messages[-1]["thread_ts"] = "1789209999.100000"
        collector = self.paged_collector([{"ok": True, "messages": messages, "has_more": False}])
        observed, complete, error = collector.replies(THREAD)
        self.assertEqual(len(observed), 1)
        self.assertFalse(complete)
        self.assertIsNotNone(error)

    def test_deadline_stops_before_any_transport_request(self):
        collector = self.build_collector(lambda request: self.fail("Expired collector performed a request"))
        collector.deadline = 0
        with self.assertRaises(ValueError):
            collector.get(collector_module.API + "/health")
        self.assertEqual(self.requests, [])

    def test_http_failure_is_not_retried_and_source_receipt_is_retained(self):
        collector = self.build_collector(lambda request: httpx.Response(503, json={"unavailable": True}))
        with self.assertRaises(ValueError):
            collector.get(collector_module.API + "/health")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(collector.references), 1)
        self.assertEqual(collector.references[0]["http_status"], 503)

    def test_original_response_bytes_and_digest_are_retained_without_authorization_headers(self):
        raw_body = b'{ "ok": true, "fixture": "preserve exact bytes" }\n'
        collector = self.build_collector(lambda request: httpx.Response(200, content=raw_body, headers={
            "Authorization": "Bearer " + ENV["SLACK_BOT_TOKEN"],
            "x-task-engine-owner": "langgraph", "x-primary-projection-digest": "fixture-digest",
        }))
        payload, headers = collector.get(collector_module.SLACK + "auth.test")
        self.assertTrue(payload["ok"])
        self.assertEqual(headers["x-task-engine-owner"], "langgraph")
        self.assertNotIn("authorization", headers)
        reference = collector.references[0]
        self.assertEqual(reference["body_sha256"], collector_module.digest(raw_body))
        original = json.loads((self.output / "private" / reference["file"]).read_bytes())
        self.assertEqual(base64.b64decode(original["body_base64"], validate=True), raw_body)
        self.assertNotIn("headers", original)
        self.assertEqual(original["source"], collector_module.SLACK + "auth.test")
        persisted = b"\n".join(path.read_bytes() for path in (self.output / "private").iterdir())
        serialized_references = json.dumps(collector.references).encode()
        for secret in (ENV["SLACK_BOT_TOKEN"], ENV["ORCHESTRATOR_SHARED_SECRET"]):
            self.assertNotIn(secret.encode(), persisted)
            self.assertNotIn(secret.encode(), serialized_references)


class MainFailureTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="offline-slack-main-fixture-")
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name) / "output"
        self.values = [f"178921000{i}.100000" for i in range(3)]
        self.argv = ["--expected-head", "a" * 40,
                     "--rubric", str(COLLECTOR_PATH.parents[1] / "docs/examples/offline-slack-structural-rubric-v1.json"),
                     "--output-dir", str(self.output)]
        for value in self.values:
            self.argv.extend(["--trial", value])

    def test_runtime_failure_keeps_every_requested_trial_as_collection_unavailable(self):
        printed = io.StringIO()
        with patch.object(collector_module, "protect_directory", side_effect=lambda path: path.mkdir()) as protect, \
             patch.object(collector_module, "inspect_runtime", side_effect=RuntimeError("fixture-private-runtime-error")) as inspect, \
             patch.object(collector_module.httpx, "Client", side_effect=AssertionError("No HTTP client permitted")) as client, \
             patch.object(collector_module.subprocess, "run", side_effect=AssertionError("No subprocess permitted")) as run, \
             redirect_stdout(printed):
            result = collector_module.main(self.argv)
        self.assertEqual(result, 1)
        protect.assert_not_called()
        inspect.assert_called_once_with("a" * 40)
        client.assert_not_called()
        run.assert_not_called()
        receipt_bytes = (self.output / "receipt.json").read_bytes()
        receipt = json.loads(receipt_bytes)
        expected = [{"thread_ts": thread, "task_id": None, "verdict": "COLLECTION_UNAVAILABLE"}
                    for thread in collector_module.parse_trials(self.values)]
        self.assertEqual(receipt["trials"], expected)
        self.assertTrue(receipt["collection_failed"])
        self.assertTrue(receipt["read_only"])
        self.assertIsNone(receipt["qualified_run_count"])
        self.assertEqual(receipt["native_source_reads"], [])
        self.assertFalse((self.output / "private").exists())
        self.assertNotIn("fixture-private-runtime-error", printed.getvalue())
        self.assertNotIn(b"fixture-private-runtime-error", receipt_bytes)

    def test_existing_private_directory_without_receipt_is_not_mutated(self):
        private = self.output / "private"
        private.mkdir(parents=True)
        sentinel = private / "prior-original.json"
        sentinel.write_bytes(b'{"existing": "preserve exact bytes"}\n')
        before = {path.relative_to(self.output): (path.stat().st_mtime_ns,
                  path.read_bytes() if path.is_file() else None)
                  for path in [self.output, private, sentinel]}
        printed = io.StringIO()
        with patch.object(collector_module, "protect_directory", side_effect=lambda path: path.mkdir()) as protect, \
             patch.object(collector_module, "inspect_runtime", side_effect=RuntimeError("fixture-runtime-failure")) as inspect, \
             patch.object(collector_module.httpx, "Client", side_effect=AssertionError("No HTTP client permitted")) as client, \
             patch.object(collector_module.subprocess, "run", side_effect=AssertionError("No subprocess permitted")) as run, \
             redirect_stdout(printed):
            result = collector_module.main(self.argv)
        self.assertEqual(result, 1)
        protect.assert_not_called()
        inspect.assert_not_called()
        client.assert_not_called()
        run.assert_not_called()
        self.assertFalse((self.output / "receipt.json").exists())
        self.assertEqual(set(self.output.rglob("*")), {private, sentinel})
        after = {path.relative_to(self.output): (path.stat().st_mtime_ns,
                 path.read_bytes() if path.is_file() else None)
                 for path in [self.output, private, sentinel]}
        self.assertEqual(after, before)
        self.assertIsNone(json.loads(printed.getvalue())["receipt"])


def rubric_fixture():
    rubric = {"schema": "director-offline-structural-rubric/v1", "rubric_id": "fixture",
        "declared_at": "2020-01-01T00:00:00+00:00", "requirements": collector_module.RUBRIC_REQUIREMENTS}
    return rubric, collector_module.digest(collector_module.encode(rubric))


def structural_trial():
    rubric, sha = rubric_fixture()
    task, deliveries, messages, headers = sample()
    task["status"] = "DONE"
    task["assignment_outcomes"][0]["provider_result_digest"] = "a" * 64
    text = "audit trial rubric-sha256:" + sha
    task["intake"]["text"] = messages[0]["text"] = text
    messages[-1]["text"] += "\nActual USD cost: not recorded"
    row = collector_module.summarize(THREAD, TASK_ID, task, deliveries, messages, True,
        OWNER, BOT, headers, ENV["SLACK_DIRECTOR_CHANNEL_ID"])
    row.update(input_rubric_sha256=sha, source_errors=[], runtime_collection_binding_stable=True)
    row["rubric_evaluation"] = collector_module.evaluate_rubric(row, rubric, sha)
    return row


def receipt_fixture():
    _, sha = rubric_fixture()
    runtime = {"container_id": "synthetic-container", "image_id": "sha256:" + "a" * 64,
        "runtime_head": "b" * 40, "started_at": "2020-01-01T00:00:00+00:00",
        "offline_configured": True, "paid_configured": False}
    return {"schema": "director-slack-trial-observations/v1", "observed_at": collector_module.utc(),
        "expires_at": collector_module.utc(), "requested_trials": 1, "observed_trials": 1,
        "rubric_sha256": sha, "collection_runtime_before": runtime,
        "collection_runtime_after": deepcopy(runtime), "runtime_collection_binding_stable": True,
        "runtime_health_at_collection": {"paid_runs_enabled": False, "ambiguous_effect_count": 0,
            "reconciliation_hold_reason": None, "primary_runtime_active": True},
        "collector_sha256": "c" * 64, "read_only": True, "slack_writes": 0,
        "provider_dispatches": 0, "scorecard_mutations": 0, "qualified_run_count": None,
        "ten_consecutive_runs_verified": False, "native_source_reads": [],
        "trials": [structural_trial()], "limitations": []}


class TaskLookupAndRubricTests(unittest.TestCase):
    def test_main_reads_thread_once_then_binds_task_and_writes_valid_receipt(self):
        rubric, sha = rubric_fixture()
        task, deliveries, messages, headers = sample()
        task["status"] = "DONE"
        task["assignment_outcomes"][0]["provider_result_digest"] = "a" * 64
        task["intake"]["text"] = messages[0]["text"] = "audit trial rubric-sha256:" + sha
        messages[-1]["text"] += "\nActual USD cost: not recorded"
        messages.append({"ts": "1789210001.100000", "thread_ts": THREAD,
            "user": BOT, "text": f"*{TASK_ID} — ACCEPTED*"})
        requests = []
        def respond(request):
            self.assertEqual(request.method, "GET")
            requests.append(request.url.path)
            if request.url.path.endswith("auth.test"):
                payload = {"ok": True, "user_id": BOT, "bot_id": "BBOT123"}
            elif request.url.path.endswith("conversations.replies"):
                payload = {"ok": True, "messages": messages, "has_more": False}
            elif request.url.path.endswith("/health"):
                payload = {"environment": "local", "paid_runs_enabled": False,
                    "langgraph_primary": {"ambiguous_effect_count": 0, "reconciliation_hold_reason": None,
                        "primary_runtime_active": True}}
            elif request.url.path.endswith("/deliveries"):
                payload = deliveries
            else:
                self.assertTrue(request.url.path.endswith(TASK_ID))
                payload = task
            return httpx.Response(200, json=payload, headers=headers)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rubric_path = root / "rubric.json"
            rubric_path.write_bytes(collector_module.encode(rubric))
            output = root / "output"
            runtime = receipt_fixture()["collection_runtime_before"]
            client = httpx.Client(transport=httpx.MockTransport(respond))
            with patch.object(collector_module, "inspect_runtime", return_value=(runtime, dict(ENV))), \
                 patch.object(collector_module.httpx, "Client", return_value=client), redirect_stdout(io.StringIO()):
                result = collector_module.main(["--expected-head", "b" * 40, "--trial", THREAD,
                    "--rubric", str(rubric_path), "--output-dir", str(output)])
            self.assertEqual(0, result)
            receipt = json.loads((output / "receipt.json").read_bytes())
            collector_module.validate_observations(receipt)
            self.assertTrue(receipt["trials"][0]["rubric_evaluation"]["passed"])
            self.assertEqual(requests.count("/api/conversations.replies"), 1)
            self.assertLess(requests.index("/api/conversations.replies"), requests.index("/v1/tasks/" + TASK_ID))
            self.assertFalse((output / "private").exists())
            for value in ENV.values():
                self.assertNotIn(value.encode(), (output / "receipt.json").read_bytes())

    def test_main_binds_task_from_current_gateway_request_received_wording(self):
        rubric, sha = rubric_fixture()
        task, deliveries, messages, headers = sample()
        task["status"] = "DONE"
        task["assignment_outcomes"][0]["provider_result_digest"] = "a" * 64
        task["intake"]["text"] = messages[0]["text"] = "audit trial rubric-sha256:" + sha
        messages[-1]["text"] += "\nActual USD cost: not recorded"
        messages.append({"ts": "1789210001.100000", "thread_ts": THREAD, "user": BOT,
            "text": f"*{TASK_ID} — REQUEST RECEIVED*\nThe Director returned a valid "
                "intake receipt. Execution remains subject to the configured safety gates."})
        def respond(request):
            self.assertEqual(request.method, "GET")
            if request.url.path.endswith("auth.test"):
                payload = {"ok": True, "user_id": BOT, "bot_id": "BBOT123"}
            elif request.url.path.endswith("conversations.replies"):
                payload = {"ok": True, "messages": messages, "has_more": False}
            elif request.url.path.endswith("/health"):
                payload = {"environment": "local", "paid_runs_enabled": False,
                    "langgraph_primary": {"ambiguous_effect_count": 0, "reconciliation_hold_reason": None,
                        "primary_runtime_active": True}}
            elif request.url.path.endswith("/deliveries"):
                payload = deliveries
            else:
                self.assertTrue(request.url.path.endswith(TASK_ID))
                payload = task
            return httpx.Response(200, json=payload, headers=headers)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rubric_path = root / "rubric.json"
            rubric_path.write_bytes(collector_module.encode(rubric))
            output = root / "output"
            runtime = receipt_fixture()["collection_runtime_before"]
            client = httpx.Client(transport=httpx.MockTransport(respond))
            with patch.object(collector_module, "inspect_runtime", return_value=(runtime, dict(ENV))), \
                 patch.object(collector_module.httpx, "Client", return_value=client), redirect_stdout(io.StringIO()):
                result = collector_module.main(["--expected-head", "b" * 40, "--trial", THREAD,
                    "--rubric", str(rubric_path), "--output-dir", str(output)])
            self.assertEqual(0, result)
            receipt = json.loads((output / "receipt.json").read_bytes())
            collector_module.validate_observations(receipt)
            self.assertEqual(receipt["trials"][0]["task_id"], TASK_ID)
            self.assertTrue(receipt["trials"][0]["rubric_evaluation"]["passed"])

    def test_native_accepted_reply_derives_task(self):
        accepted = {"user": BOT, "thread_ts": THREAD, "text": f"*{TASK_ID} — ACCEPTED*"}
        self.assertEqual(TASK_ID, collector_module.task_from_accepted(THREAD, [accepted], True, BOT))

    def test_native_request_received_reply_derives_task(self):
        gateway_text = (f"*{TASK_ID} — REQUEST RECEIVED*\n"
            "The Director returned a valid intake receipt. Execution remains "
            "subject to the configured safety gates.")
        received = {"user": BOT, "thread_ts": THREAD, "text": gateway_text}
        self.assertEqual(TASK_ID, collector_module.task_from_accepted(THREAD, [received], True, BOT))

    def test_absent_duplicate_forged_wrong_thread_or_incomplete_acceptance_refused(self):
        accepted = {"user": BOT, "thread_ts": THREAD, "text": f"*{TASK_ID} — ACCEPTED*"}
        for messages, complete in (([], True), ([accepted, accepted], True), ([accepted], False),
                ([{**accepted, "user": OWNER}], True), ([{**accepted, "thread_ts": FINAL_TS}], True)):
            with self.subTest(messages=messages, complete=complete), self.assertRaises(ValueError):
                collector_module.task_from_accepted(THREAD, messages, complete, BOT)

    def test_request_received_wrong_sender_wrong_thread_or_incomplete_refused(self):
        gateway_text = (f"*{TASK_ID} — REQUEST RECEIVED*\n"
            "The Director returned a valid intake receipt. Execution remains "
            "subject to the configured safety gates.")
        received = {"user": BOT, "thread_ts": THREAD, "text": gateway_text}
        for messages, complete in (([], True), ([received, received], True), ([received], False),
                ([{**received, "user": OWNER}], True), ([{**received, "thread_ts": FINAL_TS}], True)):
            with self.subTest(messages=messages, complete=complete), self.assertRaises(ValueError):
                collector_module.task_from_accepted(THREAD, messages, complete, BOT)

    def test_mixed_accepted_and_request_received_wordings_are_ambiguous(self):
        accepted = {"user": BOT, "thread_ts": THREAD, "text": f"*{TASK_ID} — ACCEPTED*"}
        gateway_text = (f"*{TASK_ID} — REQUEST RECEIVED*\n"
            "The Director returned a valid intake receipt. Execution remains "
            "subject to the configured safety gates.")
        received = {"user": BOT, "thread_ts": THREAD, "text": gateway_text}
        with self.assertRaises(ValueError):
            collector_module.task_from_accepted(THREAD, [accepted, received], True, BOT)
        other_task_received = {**received, "text": received["text"].replace(TASK_ID, "DIR-LG-CD34")}
        with self.assertRaises(ValueError):
            collector_module.task_from_accepted(THREAD, [accepted, other_task_received], True, BOT)

    def test_structural_rubric_passes_without_claiming_full_qualification(self):
        row = structural_trial()
        self.assertTrue(row["rubric_evaluation"]["passed"])
        self.assertEqual(row["verdict"], "EVIDENCE_ONLY_NOT_QUALIFIED")
        self.assertIsNone(row["execution_image_binding_verified"])

    def test_each_rubric_condition_is_required(self):
        rubric, sha = rubric_fixture()
        for field, bad in (("task_status", "FAILED"), ("terminal_reply_count", 2),
                ("task_record_bound", False), ("all_recorded_outcomes_are_offline_doubles", False),
                ("all_provider_result_digests_present", False), ("input_rubric_sha256", "d" * 64),
                ("final_receipt_matches_native_message", False)):
            with self.subTest(field=field):
                row = structural_trial()
                row[field] = bad
                self.assertFalse(collector_module.evaluate_rubric(row, rubric, sha)["passed"])
        for field in ("simulation_label_present", "actual_cost_not_recorded_present"):
            row = structural_trial()
            row["terminal_messages"][0][field] = False
            self.assertFalse(collector_module.evaluate_rubric(row, rubric, sha)["passed"])

    def test_late_rubric_and_missing_original_digest_fail_predeclaration(self):
        rubric, sha = rubric_fixture()
        rubric["declared_at"] = "2099-01-01T00:00:00+00:00"
        self.assertFalse(collector_module.evaluate_rubric(structural_trial(), rubric, sha)["passed"])
        row = structural_trial()
        row.pop("input_rubric_sha256")
        self.assertFalse(collector_module.evaluate_rubric(row, rubric, sha)["passed"])

    def test_rejects_weakened_or_naive_rubric(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rubric.json"
            for change in ({"declared_at": "2020-01-01"}, {"requirements": {"task_status": "DONE"}}):
                rubric, _ = rubric_fixture()
                rubric.update(change)
                path.write_bytes(collector_module.encode(rubric))
                with self.assertRaises(ValueError):
                    collector_module.load_rubric(path)

    def test_digest_must_be_64_hex_for_every_outcome(self):
        for value in (None, "x" * 64, "a" * 63, "sha256:" + "a" * 64):
            task, deliveries, messages, headers = sample()
            task["assignment_outcomes"][0]["provider_result_digest"] = value
            row = collector_module.summarize(THREAD, TASK_ID, task, deliveries, messages, True,
                OWNER, BOT, headers, ENV["SLACK_DIRECTOR_CHANNEL_ID"])
            self.assertFalse(row["all_provider_result_digests_present"])

    def test_schema_accepts_synthetic_receipt_and_rejects_extra_identity(self):
        schema = json.loads((COLLECTOR_PATH.parents[1] / "docs/schemas/director-slack-trial-observations-v1.schema.json").read_bytes())
        receipt = receipt_fixture()
        jsonschema.validate(receipt, schema)
        receipt["channel_id"] = "synthetic-private-field"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(receipt, schema)

    def test_shipped_example_matches_schema_and_repository_rubric_bytes(self):
        root = COLLECTOR_PATH.parents[1]
        example = json.loads((root / "docs/examples/offline-slack-trial-observations-v1.json").read_bytes())
        collector_module.validate_observations(example)
        path = root / "docs/examples/offline-slack-structural-rubric-v1.json"
        rubric, local_sha = collector_module.load_rubric(path)
        self.assertEqual(local_sha, collector_module.digest(path.read_bytes()))
        # The static example identifies repository LF bytes. An autocrlf
        # checkout has different bytes and must declare its own digest live.
        sha = collector_module.digest(path.read_bytes().replace(b"\r\n", b"\n"))
        self.assertEqual(sha, example["rubric_sha256"])
        self.assertEqual(example["trials"][0]["rubric_evaluation"],
            collector_module.evaluate_rubric(example["trials"][0], rubric, sha))


class OwnerPauseTests(unittest.TestCase):
    def row(self, start=5, end=15):
        return {"notification_key": "approval:fixture:scope", "owner_decision": "approve",
            "state": "DELIVERED", "owner_command_id": "fixture-command",
            "slack_ts": f"{float(THREAD) + start:.6f}",
            "owner_decision_at": datetime.fromtimestamp(float(THREAD) + end, timezone.utc).isoformat()}

    def test_approved_pause_and_machine_arithmetic(self):
        pause, machine = collector_module.owner_pause([self.row()], 30, THREAD, FINAL_TS)
        self.assertAlmostEqual(pause, 10)
        self.assertAlmostEqual(machine, 20)

    def test_no_approval_rows_has_zero_pause(self):
        self.assertEqual((0, 30), collector_module.owner_pause([], 30, THREAD, FINAL_TS))

    def test_reject_unknown_missing_or_undelivered_decision_is_not_pause(self):
        for changes in ({"owner_decision": "reject"}, {"owner_decision": None}, {"owner_command_id": None},
                {"state": "ACKNOWLEDGED"}, {"owner_decision_at": None}, {"slack_ts": None},
                {"owner_decision_at": "2026-09-12T10:00:00"}):
            with self.subTest(changes=changes):
                row = {**self.row(), **changes}
                self.assertEqual((None, None), collector_module.owner_pause([row], 30, THREAD, FINAL_TS))

    def test_multiple_approvals_must_not_overlap_or_exceed_wall_interval(self):
        self.assertEqual((10, 20), collector_module.owner_pause([self.row(2, 7), self.row(10, 15)], 30, THREAD, FINAL_TS))
        for rows in ([self.row(2, 12), self.row(10, 15)], [self.row(-1, 2)], [self.row(2, 31)], [self.row(15, 5)]):
            self.assertEqual((None, None), collector_module.owner_pause(rows, 30, THREAD, FINAL_TS))


class PrivateCaptureTests(unittest.TestCase):
    def test_default_writes_no_originals_and_secrets_or_identities_are_withheld(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for private in (None, root):
                for raw in (ENV["SLACK_BOT_TOKEN"], ENV["ORCHESTRATOR_SHARED_SECRET"], OWNER):
                    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"echo": raw}))) as client:
                        instance = collector_module.Collector(client, root, dict(ENV), private)
                        instance.get(collector_module.SLACK + "auth.test")
                        self.assertIsNone(instance.references[0]["file"])
                        self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 10:21 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 20:45 UTC | END]]
