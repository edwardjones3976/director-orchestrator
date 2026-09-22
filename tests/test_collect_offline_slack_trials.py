# [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]]
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


def readback_fixture():
    """A serialized opt-in qualification_evidence in the typed API shape."""
    assignment = TASK_ID + "-A1"
    return {
        "schema_version": "director-qualification-readback/v1", "task_id": TASK_ID, "engine_owner": "langgraph",
        "projection_version": 3, "projection_digest": "fixture-digest", "source_event_id": SOURCE_EVENT,
        "execution_mode": "offline_simulation", "terminal_classification": "SUCCEEDED",
        "terminal_reason_code": "effect_readback_verified", "task_created_at": "2026-09-12T10:00:00+00:00",
        "task_updated_at": "2026-09-12T10:00:31+00:00",
        "captured_at": "2026-09-12T10:00:45+00:00",
        "task_scope_counters": {"thread_tasks": 1, "assignments": 1, "provider_attempts": 2, "attempt_ordinals": 1,
                                "started_attempts": 1, "status_receipts": 1, "effect_reservations": 1},
        "snapshot": {"isolation": "REPEATABLE READ", "read_only": True, "single_transaction": True,
                     "covers_task_record": True},
        "bounds": {"row_limit": 200, "byte_limit": 262144, "wall_seconds": 5.0, "elapsed_seconds": 0.01},
        "truncated": [], "absent": [], "complete": True,
        "assignments": [{"assignment_id": assignment, "sequence": 1, "provider_id": "claude",
                         "deployment_id": "dep-1", "status": "SUCCEEDED", "reservation_digest": "sha256:" + "a" * 64,
                         "result_digest": "sha256:" + "2" * 64, "reason_code": "completed",
                         "reserved_at": "2026-09-12T10:00:05+00:00", "completed_at": "2026-09-12T10:00:25+00:00"}],
        "provider_attempts": [
            {"assignment_id": assignment, "assignment_sequence": 1, "attempt_ordinal": 1, "event_sequence": 1,
             "provider_id": "claude", "format_id": "v1", "transport_id": "native-command",
             "idempotency_key_digest": "sha256:" + "0" * 64, "attempt_digest": "sha256:" + "1" * 64,
             "provider_run_id_digest": None, "status": "STARTED", "result_digest": None,
             "outcome_evidence_digest": None, "reason_code": "attempt_started",
             "budget_evidence_digest": "sha256:" + "9" * 64, "recorded_at": "2026-09-12T10:00:06+00:00"},
            {"assignment_id": assignment, "assignment_sequence": 1, "attempt_ordinal": 1, "event_sequence": 2,
             "provider_id": "claude", "format_id": "v1", "transport_id": "native-command",
             "idempotency_key_digest": "sha256:" + "0" * 64, "attempt_digest": "sha256:" + "1" * 64,
             "provider_run_id_digest": "sha256:" + "8" * 64, "status": "SUCCEEDED",
             "result_digest": "sha256:" + "2" * 64, "outcome_evidence_digest": "sha256:" + "7" * 64,
             "reason_code": "attempt_succeeded", "budget_evidence_digest": "sha256:" + "9" * 64,
             "recorded_at": "2026-09-12T10:00:25+00:00"}],
        "effects": [{"reservation_key": "sha256:" + "4" * 64, "claim_key": "sha256:" + "5" * 64,
                     "effect_key_digest": "sha256:" + "3" * 64, "effect_attempt_digest": "sha256:" + "6" * 64,
                     "writer_owner_id": "langgraph-primary", "fencing_generation": 1, "reserved_units": 1,
                     "reserved_at": "2026-09-12T10:00:26+00:00", "execution_started_at": "2026-09-12T10:00:27+00:00",
                     "remote_system": "slack", "remote_identifier": FINAL_TS, "bound_at": "2026-09-12T10:00:28+00:00",
                     "terminal_disposition": "COMPLETED", "terminal_evidence_digest": "sha256:" + "c" * 64,
                     "terminal_recorded_at": "2026-09-12T10:00:29+00:00"}],
        "reconciliation_events": [],
        "slack_deliveries": [{"notification_key": "terminal", "projection_digest": "fixture-digest",
                              "client_msg_id": FINAL_ID, "state": "DELIVERED", "created_at": "2026-09-12T10:00:19+00:00",
                              "claimed_at": "2026-09-12T10:00:20+00:00", "acknowledged_at": "2026-09-12T10:00:30+00:00",
                              "delivered_at": "2026-09-12T10:00:31+00:00", "slack_ts": FINAL_TS, "readback_attempts": 1,
                              "last_error": None, "owner_decision": None, "owner_decision_at": None,
                              "owner_command_id": None}],
        "status_receipts": [{"receipt_id": "receipt-1", "source_event_id": "status-event-1",
                             "created_at": "2026-09-12T10:00:40+00:00"}],
        "provider_usage": [{"attempt_digest": "sha256:" + "1" * 64, "provider_id": "claude",
                            "reserved_microusd": 50000, "token_limit": 4000, "actual_microusd": 12500,
                            "observed_tokens": 900}],
    }


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

    def summarize(self, *, complete=True, **options):
        return collector_module.summarize(THREAD, TASK_ID, self.task, self.deliveries,
            self.messages, complete, OWNER, BOT, self.headers, ENV["SLACK_DIRECTOR_CHANNEL_ID"], **options)

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

    def test_absent_readback_leaves_derived_facts_unknown_with_the_reason(self):
        evidence = self.summarize()["qualification_evidence"]
        chain, stamps = evidence["chain"], evidence["timestamps"]
        self.assertEqual(evidence["readback"]["state"], "absent")
        self.assertIsNone(evidence["readback"]["source"])
        self.assertEqual(chain["task_id"], TASK_ID)
        self.assertEqual(chain["source_event_id_sha256"], collector_module.digest(collector_module.encode(SOURCE_EVENT)))
        # Without the readback, delivery identities come from the deliveries API only.
        self.assertEqual(chain["delivery_ids"], [FINAL_ID])
        self.assertEqual((chain["assignment_ids"], chain["attempt_ids"], chain["effect_ids"],
                          chain["status_receipt_ids"]), ([], [], [], []))
        self.assertEqual(chain["slack_delivery_ts"], FINAL_TS)
        self.assertEqual(stamps["request_ts"], THREAD)
        self.assertEqual(stamps["delivery_at"], "2026-09-12T10:00:30+00:00")
        self.assertIsNone(stamps["first_byte_at"])
        self.assertIn("first_byte_at", stamps["unknown_reasons"])
        self.assertIsNone(evidence["effect_state"])
        self.assertIsNone(evidence["provider_effect_state"])
        self.assertIsNone(evidence["lineage"]["recorded_attempt_count"])
        self.assertIsNone(evidence["status_receipts"]["deduplicated_receipts"])
        self.assertEqual(evidence["status_receipts"]["dependency"], collector_module.STATUS_RECEIPT_DEPENDENCY)
        self.assertEqual(evidence["usage"], {"actual_usage": None, "actual_cost_usd": None, "cost_basis": "UNKNOWN"})
        self.assertIsNone(evidence["revision_binding"])
        self.assertEqual(evidence["terminal_message_ts"], FINAL_TS)

    def test_collected_readback_binds_real_assignment_attempt_effect_delivery_and_receipt_keys(self):
        self.task["qualification_evidence"] = readback_fixture()
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual(evidence["readback"]["state"], "collected")
        self.assertEqual(evidence["readback"]["source"], collector_module.API + "/v1/tasks/" + TASK_ID
                         + "?include_qualification_evidence=true")
        chain = evidence["chain"]
        self.assertEqual(chain["assignment_ids"], [TASK_ID + "-A1"])
        self.assertEqual(chain["assignment_sequences"], [1])
        self.assertEqual(chain["attempt_ids"], ["sha256:" + "1" * 64])
        self.assertEqual(chain["effect_ids"], ["sha256:" + "3" * 64])
        self.assertEqual(chain["delivery_ids"], [FINAL_ID])
        self.assertEqual(chain["status_receipt_ids"], ["receipt-1"])
        self.assertEqual(chain["provider_result_digests"], ["sha256:" + "2" * 64])
        self.assertEqual(evidence["effect_state"], "PROVIDER_EFFECT_RECORDED")
        self.assertEqual(evidence["provider_effect_state"], "PROVIDER_RESULT_RECORDED")
        self.assertEqual(evidence["external_effect_state"], "EXTERNAL_EFFECT_TERMINAL:COMPLETED")
        self.assertEqual(evidence["lineage"]["provider_routes"], ["claude"])
        self.assertEqual(evidence["lineage"]["transport"], "native-command")
        self.assertEqual(evidence["lineage"]["recorded_attempt_count"], 1)
        self.assertIsNone(evidence["lineage"]["fallback_observed"])  # no producer records fallback linkage
        self.assertEqual(evidence["timestamps"]["completion_at"], "2026-09-12T10:00:25+00:00")
        self.assertNotIn("completion_at", evidence["timestamps"]["unknown_reasons"])
        self.assertEqual(evidence["usage"]["cost_basis"], "authenticated_usage_table")
        self.assertEqual(evidence["usage"]["actual_cost_usd"], 0.0125)
        self.assertEqual(evidence["usage"]["actual_usage"]["reserved_budget_microusd"], 50000)
        self.assertEqual(evidence["status_receipts"]["durable_receipt_rows"], 1)
        self.assertIsNone(evidence["status_receipts"]["deduplicated_receipts"])  # a row count is not an observed replay
        self.assertIsNone(evidence["status_receipts"]["tasks_created"])  # needs a prospective baseline
        collector_module.validate_observations({**receipt_fixture(), "trials": [
            {**structural_trial(), "qualification_evidence": evidence}]})

    def test_readback_is_rejected_for_wrong_task_duplicate_or_unjoined_identities_and_marked_incomplete(self):
        for change, expected in (({"task_id": "DIR-LG-CD34"}, "rejected"), ({"schema_version": "other"}, "rejected"),
                                 ({"complete": False, "truncated": ["provider_attempts"]}, "incomplete")):
            with self.subTest(change=change):
                self.task["qualification_evidence"] = {**readback_fixture(), **change}
                evidence = self.summarize()["qualification_evidence"]
                self.assertEqual(evidence["readback"]["state"], expected)
                self.assertIsNone(evidence["effect_state"])
        duplicate = readback_fixture()
        duplicate["provider_attempts"].append(dict(duplicate["provider_attempts"][0]))
        unjoined = readback_fixture()
        unjoined["provider_attempts"][0]["assignment_id"] = "DIR-LG-CD34-A9"
        for readback in (duplicate, unjoined):
            self.task["qualification_evidence"] = readback
            evidence = self.summarize()["qualification_evidence"]
            self.assertEqual(evidence["readback"]["state"], "rejected")
            self.assertEqual(evidence["chain"]["attempt_ids"], [])
            self.assertIsNone(evidence["provider_effect_state"])

    def test_readback_distinguishes_pre_effect_ambiguous_and_zero_effect_outcomes(self):
        started_only = readback_fixture()
        started_only["provider_attempts"] = [started_only["provider_attempts"][0]]
        started_only["effects"] = [dict(started_only["effects"][0], execution_started_at=None,
                                        remote_identifier=None, remote_system=None, terminal_disposition=None,
                                        terminal_evidence_digest=None)]
        self.task["qualification_evidence"] = started_only
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual(evidence["provider_effect_state"], "PROVIDER_ATTEMPT_STARTED_UNSETTLED")
        self.assertEqual(evidence["external_effect_state"], "EXTERNAL_EFFECT_RESERVED_NO_EFFECT")
        self.assertIsNone(evidence["effect_state"])  # started but unsettled is neither zero nor recorded
        ambiguous = readback_fixture()
        ambiguous["effects"][0].update(remote_identifier=None, remote_system=None, terminal_disposition=None,
                                       terminal_evidence_digest=None)
        self.task["qualification_evidence"] = ambiguous
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual(evidence["external_effect_state"], "EXTERNAL_EFFECT_AMBIGUOUS")
        self.assertEqual(evidence["effect_state"], "AMBIGUOUS_DELIVERY_EFFECT")
        zero = readback_fixture()
        zero.update(provider_attempts=[], effects=[], provider_usage=[])
        self.task["qualification_evidence"] = zero
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual(evidence["effect_state"], "ZERO_PROVIDER_EFFECT")
        self.assertEqual(evidence["provider_effect_state"], "ZERO_PROVIDER_ATTEMPT")
        self.assertEqual(evidence["usage"]["cost_basis"], "UNKNOWN")

    def test_provider_reported_cost_is_recorded_only_when_finite_numeric_and_never_invented(self):
        # A top-level task cost is an unbound provider-reported figure: it is
        # recorded only alongside a complete validated readback without usage rows.
        collected = readback_fixture()
        collected["provider_usage"] = []
        self.task["qualification_evidence"] = collected
        for value, expected in ((0.25, 0.25), (0, 0), ("0.25", None), (-1, None), (None, None),
                                (float("inf"), None), (float("nan"), None), (True, None)):
            with self.subTest(value=value):
                self.task["actual_cost_usd"] = value
                usage = self.summarize()["qualification_evidence"]["usage"]
                self.assertEqual(usage["actual_cost_usd"], expected)
                self.assertEqual(usage["cost_basis"], "provider_reported" if expected is not None else "UNKNOWN")
        self.task["actual_cost_usd"] = 0.25
        self.task.pop("qualification_evidence")
        self.assertEqual("UNKNOWN", self.summarize()["qualification_evidence"]["usage"]["cost_basis"])  # absent readback
        self.assertIsNone(self.summarize()["actual_cost_usd"])  # the older scorecard field stays unmeasured

    def test_revision_binding_digests_safe_effective_values_and_excludes_secrets(self):
        runtime = {"container_id": "c", "image_id": "sha256:" + "d" * 64, "runtime_head": "e" * 40,
                   "started_at": None, "offline_configured": True, "paid_configured": False}
        safe = {**ENV, "ALLOW_PAID_RUNS": "false", "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED": "true"}
        binding = collector_module.revision_binding(runtime, safe)
        self.assertEqual(binding["runtime_head"], "e" * 40)
        self.assertNotIn("container_id", binding)
        self.assertEqual(sorted(collector_module.SAFE_CONFIGURATION_KEYS), binding["configuration_keys"])
        self.assertEqual(["ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"], binding["supplied_keys"])
        self.assertEqual(collector_module.CONFIGURATION_SCOPE, binding["configuration_scope"])
        self.assertEqual("UNKNOWN", binding["execution_time_binding"])
        serialized = json.dumps(binding)
        for value in ENV.values():
            self.assertNotIn(value, serialized)
        self.assertNotIn("director_primary_v1", serialized)  # resolved values are digested, never emitted
        # Settings semantics: declared defaults, bool normalization, profile lower-casing.
        resolved, supplied = collector_module.resolve_safe_configuration(safe)
        self.assertEqual({"ALLOW_PAID_RUNS": False, "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": False,
                          "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED": True,
                          "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED": False,
                          "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": "director_primary_v1",
                          "INTAKE_ALLOCATION_PROFILE": "engineering"}, resolved)
        omitted_default = collector_module.revision_binding(runtime, {**safe, "INTAKE_ALLOCATION_PROFILE": " Engineering "})
        self.assertEqual(binding["configuration_digest"], omitted_default["configuration_digest"])
        self.assertEqual(binding["configuration_digest"], collector_module.revision_binding(
            runtime, {**safe, "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED": "YES"})["configuration_digest"])
        # The real profile key drifts the binding; the unused alias does not.
        profile_drift = collector_module.revision_binding(runtime, {**safe, "INTAKE_ALLOCATION_PROFILE": "public_research"})
        self.assertNotEqual(binding["configuration_digest"], profile_drift["configuration_digest"])
        alias = collector_module.revision_binding(runtime, {**safe, "DIRECTOR_ALLOCATION_PROFILE": "public_research"})
        self.assertEqual(binding["configuration_digest"], alias["configuration_digest"])
        drifted = collector_module.revision_binding(runtime, {**safe, "ALLOW_PAID_RUNS": "true"})
        self.assertNotEqual(binding["configuration_digest"], drifted["configuration_digest"])
        schema_drift = collector_module.revision_binding(runtime, {**safe, "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": "other_schema"})
        self.assertNotEqual(binding["configuration_digest"], schema_drift["configuration_digest"])
        secret_changed = collector_module.revision_binding(runtime, {**safe, "SLACK_BOT_TOKEN": "rotated-secret"})
        self.assertEqual(binding["configuration_digest"], secret_changed["configuration_digest"])
        unknown_added = collector_module.revision_binding(runtime, {**safe, "NEW_UNLISTED_SWITCH": "x"})
        self.assertEqual(binding["configuration_digest"], unknown_added["configuration_digest"])
        for bad in ({"ALLOW_PAID_RUNS": "yes please\n"}, {"ALLOW_PAID_RUNS": True}, {"INTAKE_ALLOCATION_PROFILE": "quantum"},
                    {"LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": "drop table;"}, {"INTAKE_ALLOCATION_PROFILE": "x" * 300}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                collector_module.revision_binding(runtime, {**safe, **bad})
        schema = json.loads((COLLECTOR_PATH.parents[1] / "docs/schemas/director-slack-trial-observations-v1.schema.json").read_bytes())
        receipt = receipt_fixture()
        receipt["trials"][0]["qualification_evidence"]["revision_binding"] = binding
        jsonschema.validate(receipt, schema)
        receipt["trials"][0]["qualification_evidence"]["allocation"]["rolling_twenty"] = "PASS"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(receipt, schema)

    def test_confirmed_probe_defects_are_refused(self):
        def rejected(mutate, reason_fragment):
            readback = readback_fixture()
            mutate(readback)
            self.task["qualification_evidence"] = readback
            evidence = self.summarize()["qualification_evidence"]
            self.assertEqual("rejected", evidence["readback"]["state"], evidence["readback"]["reason"])
            self.assertIn(reason_fragment, evidence["readback"]["reason"])
            self.assertEqual(([], [], "UNKNOWN", None, None), (
                evidence["chain"]["attempt_ids"], evidence["chain"]["effect_ids"], evidence["usage"]["cost_basis"],
                evidence["provider_effect_state"], evidence["effect_state"]))
        # 1. attempt provider differs from its assignment
        rejected(lambda r: r["provider_attempts"][1].update(provider_id="openai"), "disagrees with its assignment")
        # 2. digest changes between STARTED and terminal events of one attempt
        rejected(lambda r: r["provider_attempts"][1].update(attempt_digest="sha256:" + "f" * 64), "identity changed")
        # 3a. usage naming an unjoined digest / another provider
        rejected(lambda r: r["provider_usage"][0].update(attempt_digest="sha256:" + "e" * 64), "usage is orphaned")
        rejected(lambda r: r["provider_usage"][0].update(provider_id="openai"), "usage is orphaned")
        # 3b. duplicate usage rows must not double the cost
        rejected(lambda r: r["provider_usage"].append(dict(r["provider_usage"][0])), "usage is orphaned")
        # bool-as-number and negative amounts
        rejected(lambda r: r["provider_usage"][0].update(actual_microusd=True), "non-integer")
        rejected(lambda r: r["provider_usage"][0].update(observed_tokens=-1), "non-integer")
        # malformed identities, sequences and statuses
        rejected(lambda r: r["provider_attempts"][1].update(event_sequence=7), "malformed attempt event")
        rejected(lambda r: r["provider_attempts"][1].update(status="DONE"), "malformed attempt event")
        rejected(lambda r: r["assignments"][0].update(assignment_id=""), "malformed or duplicate assignment")
        rejected(lambda r: r["provider_attempts"][1].update(assignment_sequence=2), "disagrees with its assignment")
        # 4. a digest reused by a second attempt ordinal
        def reuse(r):
            r["provider_attempts"].append({**r["provider_attempts"][0], "attempt_ordinal": 2})
        rejected(reuse, "reused by another attempt")

    def test_every_effect_and_latest_valid_event_drive_the_aggregate(self):
        # 4. a completed effect cannot hide a different unbound execution
        readback = readback_fixture()
        readback["effects"].append({**readback["effects"][0], "reservation_key": "sha256:" + "d" * 64,
                                    "effect_key_digest": "sha256:" + "b" * 64, "remote_system": None,
                                    "remote_identifier": None, "bound_at": None, "terminal_disposition": None,
                                    "terminal_evidence_digest": None, "terminal_recorded_at": None})
        self.task["qualification_evidence"] = readback
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual("EXTERNAL_EFFECT_AMBIGUOUS", evidence["external_effect_state"])
        self.assertEqual("AMBIGUOUS_DELIVERY_EFFECT", evidence["effect_state"])
        # 5. an AMBIGUOUS event 2 is superseded by an authenticated SUCCEEDED event 3
        readback = readback_fixture()
        readback["provider_attempts"][1].update(status="AMBIGUOUS", result_digest=None, outcome_evidence_digest=None,
                                                provider_run_id_digest=None, reason_code="callback_timeout",
                                                recorded_at="2026-09-12T10:00:20+00:00")
        self.task["qualification_evidence"] = readback
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual("PROVIDER_ATTEMPT_AMBIGUOUS", evidence["provider_effect_state"])
        self.assertEqual("AMBIGUOUS_DELIVERY_EFFECT", evidence["effect_state"])
        readback["provider_attempts"].append({**readback_fixture()["provider_attempts"][1], "event_sequence": 3,
                                              "reason_code": "authenticated_callback_settled",
                                              "recorded_at": "2026-09-12T10:00:26+00:00"})
        self.task["qualification_evidence"] = readback
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual("PROVIDER_RESULT_RECORDED", evidence["provider_effect_state"])
        self.assertEqual("PROVIDER_EFFECT_RECORDED", evidence["effect_state"])
        self.assertEqual("2026-09-12T10:00:26+00:00", evidence["timestamps"]["completion_at"])
        self.assertEqual(1, evidence["lineage"]["recorded_attempt_count"])
        # a mixed reserved + terminal set is reported as mixed, not as plainly terminal
        readback = readback_fixture()
        readback["effects"].append({**readback["effects"][0], "reservation_key": "sha256:" + "d" * 64,
                                    "effect_key_digest": "sha256:" + "b" * 64, "execution_started_at": None,
                                    "remote_system": None, "remote_identifier": None, "bound_at": None,
                                    "terminal_disposition": None, "terminal_evidence_digest": None,
                                    "terminal_recorded_at": None})
        self.task["qualification_evidence"] = readback
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual("EXTERNAL_EFFECT_MIXED_RESERVED_AND_TERMINAL:COMPLETED", evidence["external_effect_state"])
        # retry (second ordinal, same provider) is not reported as a fallback
        readback = readback_fixture()
        second = [{**e, "attempt_ordinal": 2, "attempt_digest": "sha256:" + "a" * 64,
                   "idempotency_key_digest": "sha256:" + "c" * 64} for e in readback["provider_attempts"]]
        readback["provider_attempts"].extend(second)
        readback["provider_usage"] = []
        self.task["qualification_evidence"] = readback
        evidence = self.summarize()["qualification_evidence"]
        self.assertTrue(evidence["lineage"]["retry_observed"])
        self.assertIsNone(evidence["lineage"]["fallback_observed"])  # retry is not fallback; fallback is UNKNOWN
        self.assertEqual(2, evidence["lineage"]["recorded_attempt_count"])

    def test_history_shape_defects_are_refused(self):
        def rejected(mutate, fragment):
            readback = readback_fixture()
            mutate(readback)
            self.task["qualification_evidence"] = readback
            evidence = self.summarize()["qualification_evidence"]
            self.assertEqual("rejected", evidence["readback"]["state"], evidence["readback"]["reason"])
            self.assertIn(fragment, evidence["readback"]["reason"])
            self.assertEqual("UNKNOWN", evidence["usage"]["cost_basis"])
        # terminal event without its STARTED event 1
        rejected(lambda r: r["provider_attempts"].pop(0), "lacks its STARTED event 1")
        # history whose first event is not STARTED
        rejected(lambda r: r["provider_attempts"][0].update(status="SUCCEEDED", result_digest="sha256:" + "2" * 64,
                                                            outcome_evidence_digest="sha256:" + "7" * 64),
                 "event 1 is not the STARTED record")
        rejected(lambda r: r["provider_attempts"][1].update(status="STARTED"), "a later event repeats STARTED")
        # bool assignment sequence and bool counters
        rejected(lambda r: r["provider_attempts"][1].update(assignment_sequence=True), "malformed attempt event")
        rejected(lambda r: r["task_scope_counters"].update(assignments=True), "shape/task binding mismatch")
        # missing terminal outcome evidence / result digest
        rejected(lambda r: r["provider_attempts"][1].update(outcome_evidence_digest=None), "lacks outcome evidence")
        rejected(lambda r: r["provider_attempts"][1].update(result_digest=None), "lacks a result digest")
        # events recorded out of sequence order
        rejected(lambda r: r["provider_attempts"][1].update(recorded_at="2026-09-12T09:00:00+00:00"),
                 "not recorded in sequence order")
        # missing/malformed external reservation identity and remote binding halves
        rejected(lambda r: r["effects"][0].update(reservation_key="not-a-digest"), "malformed external-effect reservation")
        rejected(lambda r: r["effects"][0].update(claim_key=None), "malformed external-effect reservation")
        rejected(lambda r: r["effects"][0].update(remote_system=None), "lacks its system or identifier")
        rejected(lambda r: r["effects"][0].update(terminal_evidence_digest=None), "terminal evidence lacks")
        rejected(lambda r: r["effects"].append(dict(r["effects"][0])), "duplicate external-effect reservation")
        # unknown top-level readback keys and malformed delivery/receipt rows
        rejected(lambda r: r.update(extra_claim="verified"), "shape/task binding mismatch")
        rejected(lambda r: r.pop("captured_at"), "shape/task binding mismatch")
        rejected(lambda r: r["slack_deliveries"][0].update(state="POSTED"), "malformed Slack delivery row")
        rejected(lambda r: r["status_receipts"][0].update(created_at="yesterday"), "malformed status receipt row")
        # a truncated readback with an unbound top-level cost still yields no measured cost
        readback = readback_fixture()
        readback.update(complete=False, truncated=["effects"])
        self.task["qualification_evidence"] = readback
        self.task["actual_cost_usd"] = 0.5
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual("incomplete", evidence["readback"]["state"])
        self.assertEqual({"actual_usage": None, "actual_cost_usd": None, "cost_basis": "UNKNOWN"}, evidence["usage"])
        # an authenticated callback event 3 directly after event 1 is legitimate
        readback = readback_fixture()
        readback["provider_attempts"][1]["event_sequence"] = 3
        self.task["qualification_evidence"] = readback
        self.task.pop("actual_cost_usd", None)
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual("collected", evidence["readback"]["state"], evidence["readback"]["reason"])
        self.assertEqual("PROVIDER_RESULT_RECORDED", evidence["provider_effect_state"])

    def test_incomplete_readback_emits_partial_diagnostics_but_no_derived_claims(self):
        readback = readback_fixture()
        readback.update(complete=False, truncated=["provider_attempts"])
        self.task["qualification_evidence"] = readback
        evidence = self.summarize()["qualification_evidence"]
        self.assertEqual("incomplete", evidence["readback"]["state"])
        self.assertEqual({"row_counts": {"assignments": 1, "provider_attempts": 2, "effects": 1,
                                         "reconciliation_events": 0, "slack_deliveries": 1, "status_receipts": 1,
                                         "provider_usage": 1}, "not_proof": True},
                         evidence["readback"]["partial_diagnostics"])
        self.assertEqual(([], [], [], []), (evidence["chain"]["attempt_ids"], evidence["chain"]["effect_ids"],
                                            evidence["chain"]["delivery_ids"], evidence["chain"]["status_receipt_ids"]))
        self.assertEqual("UNKNOWN", evidence["usage"]["cost_basis"])
        self.assertIsNone(evidence["usage"]["actual_cost_usd"])
        self.assertIsNone(evidence["provider_effect_state"])
        self.assertIsNone(evidence["status_receipts"]["deduplicated_receipts"])
        collector_module.validate_observations({**receipt_fixture(), "trials": [
            {**structural_trial(), "qualification_evidence": evidence}]})

    def test_prospective_status_delta_requires_ordered_compatible_complete_readbacks(self):
        runtime = {"container_id": "c", "image_id": "sha256:" + "d" * 64, "runtime_head": "e" * 40,
                   "started_at": None, "offline_configured": True, "paid_configured": False}
        binding = collector_module.revision_binding(runtime, {"ALLOW_PAID_RUNS": "false"})
        same = collector_module.SAME_PROCESS_AUTHORITY
        after = readback_fixture()
        before = {**readback_fixture(), "captured_at": "2026-09-12T10:00:35+00:00", "status_receipts": [],
                  "task_scope_counters": {**readback_fixture()["task_scope_counters"], "status_receipts": 0}}
        proof = collector_module.status_receipt_delta(before, after, "receipt-1", baseline_authority=same,
                                                      before_binding=binding, after_binding=binding)
        self.assertTrue(proof["proof"])
        self.assertEqual((0, 0, 0, 1), (proof["tasks_created"], proof["assignments_created"],
                                        proof["provider_attempts_created"], proof["deduplicated_receipts"]))
        self.assertEqual(0, proof["observed_delta"]["provider_attempt_events_added"])
        # A consistent caller-supplied file yields only an observed delta, never proof.
        filed = collector_module.status_receipt_delta(before, after, "receipt-1", baseline_authority="caller-supplied-file",
                                                      before_binding=binding, after_binding=binding)
        self.assertIsNone(filed["proof"])
        self.assertEqual(proof["observed_delta"], filed["observed_delta"])
        self.assertIn("not authoritative proof", filed["reason"])
        other_binding = collector_module.revision_binding({**runtime, "runtime_head": "f" * 40}, {"ALLOW_PAID_RUNS": "false"})
        config_drift = collector_module.revision_binding(runtime, {"ALLOW_PAID_RUNS": "true"})
        for label, mutate, kwargs in (
                ("baseline after receipt", lambda b, a: b.update(captured_at="2026-09-12T10:00:50+00:00"), {}),
                ("after captured before receipt", lambda b, a: a.update(captured_at="2026-09-12T10:00:39+00:00"), {}),
                ("foreign task", lambda b, a: b.update(task_id="DIR-LG-CD34"), {}),
                ("different source event", lambda b, a: b.update(source_event_id="another-event"), {}),
                ("truncated after", lambda b, a: a.update(complete=False), {}),
                ("negative delta", lambda b, a: b["task_scope_counters"].update(assignments=5), {}),
                ("receipt missing after", lambda b, a: a.update(status_receipts=[]), {}),
                ("receipt joins twice", lambda b, a: a["status_receipts"].append(dict(a["status_receipts"][0])), {}),
                ("receipt already in baseline", lambda b, a: b.update(status_receipts=a["status_receipts"]), {}),
                ("two receipts", lambda b, a: a["task_scope_counters"].update(status_receipts=2), {}),
                ("counters not integers", lambda b, a: a["task_scope_counters"].update(assignments=True), {}),
                ("counter scope mismatch", lambda b, a: a["task_scope_counters"].pop("attempt_ordinals"), {}),
                ("newer baseline", lambda b, a: b.update(task_updated_at="2026-09-12T11:00:00+00:00"), {}),
                ("naive timestamp", lambda b, a: b.update(captured_at="2026-09-12T10:00:35"), {}),
                ("runtime head differs", lambda b, a: None, {"before_binding": other_binding}),
                ("configuration drift", lambda b, a: None, {"after_binding": config_drift}),
                ("binding missing", lambda b, a: None, {"before_binding": None})):
            with self.subTest(label=label):
                b, a = {**before, "task_scope_counters": dict(before["task_scope_counters"]),
                        "status_receipts": list(before["status_receipts"])}, \
                       {**after, "task_scope_counters": dict(after["task_scope_counters"]),
                        "status_receipts": [dict(r) for r in after["status_receipts"]]}
                mutate(b, a)
                options = {"baseline_authority": same, "before_binding": binding, "after_binding": binding, **kwargs}
                verdict = collector_module.status_receipt_delta(b, a, "receipt-1", **options)
                self.assertIsNone(verdict["proof"], label)
                self.assertIsNone(verdict["observed_delta"], label)
        # Through the collector entry path, with the observation schema.
        self.task["qualification_evidence"] = after
        collected = self.summarize(baseline=before, receipt_id="receipt-1", baseline_authority=same,
                                   before_binding=binding, after_binding=binding)["qualification_evidence"]
        evidence = collected["status_receipts"]
        self.assertEqual((0, 0, 0, 1), (evidence["tasks_created"], evidence["assignments_created"],
                                        evidence["provider_attempts_created"], evidence["deduplicated_receipts"]))
        self.assertEqual("task-and-thread-local/v1", evidence["baseline"]["counter_scope"])
        collector_module.validate_observations({**receipt_fixture(), "trials": [
            {**structural_trial(), "qualification_evidence": collected}]})
        filed_evidence = self.summarize(baseline=before, receipt_id="receipt-1",
                                        before_binding=binding, after_binding=binding)["qualification_evidence"]
        self.assertIsNone(filed_evidence["status_receipts"]["tasks_created"])
        self.assertIsNone(filed_evidence["status_receipts"]["deduplicated_receipts"])
        self.assertEqual(0, filed_evidence["status_receipts"]["baseline"]["observed_delta"]["tasks_created"])
        collector_module.validate_observations({**receipt_fixture(), "trials": [
            {**structural_trial(), "qualification_evidence": filed_evidence}]})
        without = self.summarize()["qualification_evidence"]["status_receipts"]
        self.assertIsNone(without["tasks_created"])

    def test_baseline_from_receipt_requires_a_schema_valid_collected_prior_observation(self):
        prior = receipt_fixture()
        self.assertIsNone(collector_module.baseline_from_receipt(prior, TASK_ID))  # absent readback state
        self.assertIsNone(collector_module.baseline_from_receipt({"schema": "x"}, TASK_ID))
        self.task["qualification_evidence"] = readback_fixture()
        collected = self.summarize()["qualification_evidence"]
        collected["revision_binding"] = collector_module.revision_binding(prior["collection_runtime_before"], {})
        prior["trials"] = [{**structural_trial(), "qualification_evidence": collected}]
        baseline = collector_module.baseline_from_receipt(prior, TASK_ID)
        self.assertEqual(TASK_ID, baseline["task_id"])
        self.assertEqual(collected["readback"]["task_scope_counters"], baseline["task_scope_counters"])
        self.assertEqual(collected["revision_binding"], baseline["revision_binding"])
        self.assertIsNone(collector_module.baseline_from_receipt(prior, "DIR-LG-CD34"))
        prior["trials"][0]["qualification_evidence"]["readback"]["state"] = "forged"
        self.assertIsNone(collector_module.baseline_from_receipt(prior, TASK_ID))  # schema-invalid receipt


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
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | END]]
