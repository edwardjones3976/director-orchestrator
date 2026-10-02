# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]]
"""Local receipt mapping and append safety; no real trial or external service."""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

import jsonschema

from director_orchestrator.completion_scorecard import (
    OFFLINE_RUN_RECORD_FIELDS, evaluate_run_record, evaluate_run_records,
)
from tests.test_collect_offline_slack_trials import THREAD, receipt_fixture, rubric_fixture

PATH = Path(__file__).resolve().parents[1] / "scripts/record-offline-trial.py"
SPEC = importlib.util.spec_from_file_location("record_offline_trial", PATH)
recorder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recorder)


def verified_fixture():
    # Additional authority is synthetic. The real GET collector intentionally
    # leaves these three assertions unknown pending the separate check-10 order.
    receipt = receipt_fixture()
    receipt["trials"][0].update(operational_provider_attempts_verified=True,
        global_task_deduplication_verified=True, execution_image_binding_verified=True)
    return receipt


def measured_row():
    rubric, sha = rubric_fixture()
    return recorder.row_from_receipt(verified_fixture(), rubric, sha)


class RecordOfflineTrialTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.log = self.root / "runs.jsonl"

    def test_exact_scorecard_fields_and_zero_call_cost_mapping(self):
        row = measured_row()
        self.assertEqual(set(row), OFFLINE_RUN_RECORD_FIELDS)
        self.assertEqual(row["actual_cost_usd"], 0)
        self.assertEqual(row["cost_basis"], "simulation_zero_calls")
        self.assertTrue(recorder.qualifies(row))

    def test_current_receipt_unknown_authorities_stay_unknown(self):
        rubric, sha = rubric_fixture()
        row = recorder.row_from_receipt(receipt_fixture(), rubric, sha)
        self.assertTrue(row["correctness_verified"])
        for field in ("task_count", "operational_provider_attempts", "actual_cost_usd", "cost_basis", "runtime_sha", "image_id"):
            self.assertIsNone(row[field])
        self.assertFalse(recorder.qualifies(row))

    def test_timing_summary_cannot_hide_a_slow_timestamp_interval(self):
        rubric, sha = rubric_fixture()
        receipt = verified_fixture()
        trial = receipt["trials"][0]
        slow = "%.6f" % (float(THREAD) + 65.1816)  # the owner's post plus 65.1816 seconds
        trial["terminal_messages"][0]["ts"] = slow
        trial["delivery_receipts"][0]["slack_ts"] = slow
        # The source interval is 65.1816 seconds, despite the 30-second summary.
        with self.assertRaisesRegex(ValueError, "timing"):
            recorder.row_from_receipt(receipt, rubric, sha)
        trial.update(wall_seconds=65.1816, owner_pause_seconds=0, machine_seconds=65.1816)
        row = recorder.row_from_receipt(receipt, rubric, sha)
        self.assertEqual(row["wall_seconds"], 65.1816)
        self.assertTrue(recorder.append_row(self.log, row))

    def test_pause_summary_requires_matching_approved_intervals(self):
        rubric, sha = rubric_fixture()
        receipt = verified_fixture()
        trial = receipt["trials"][0]
        trial.update(owner_pause_seconds=10, machine_seconds=20)
        with self.assertRaisesRegex(ValueError, "timing"):
            recorder.row_from_receipt(receipt, rubric, sha)

    def test_missing_timing_source_stays_unknown_despite_numeric_summary(self):
        rubric, sha = rubric_fixture()
        missing_timestamp = deepcopy(verified_fixture()["trials"][0]["terminal_messages"])
        missing_timestamp[0]["ts"] = None
        for changes in ({"input_message_ts": None}, {"terminal_messages": []},
                        {"terminal_messages": missing_timestamp},
                        {"slack_thread_complete_at_read": False}):
            with self.subTest(changes=changes):
                receipt = verified_fixture()
                receipt["trials"][0].update(changes)
                receipt["trials"][0]["rubric_evaluation"] = recorder.collector.evaluate_rubric(
                    receipt["trials"][0], rubric, sha)
                row = recorder.row_from_receipt(receipt, rubric, sha)
                for field in ("wall_seconds", "owner_pause_seconds", "machine_seconds"):
                    self.assertIsNone(row[field])
                self.assertFalse(recorder.qualifies(row))

    def test_incomplete_approval_rows_do_not_turn_unknown_pause_into_zero(self):
        rubric, sha = rubric_fixture()
        receipt = verified_fixture()
        receipt["trials"][0]["delivery_receipt_list_complete"] = False
        row = recorder.row_from_receipt(receipt, rubric, sha)
        self.assertEqual(row["wall_seconds"], 30)
        self.assertIsNone(row["owner_pause_seconds"])
        self.assertIsNone(row["machine_seconds"])
        self.assertFalse(recorder.qualifies(row))

    def test_approved_pause_is_recomputed_and_missing_decision_stays_unknown(self):
        rubric, sha = rubric_fixture()
        receipt = verified_fixture()
        trial = receipt["trials"][0]
        approval = {**trial["delivery_receipts"][0], "notification_key": "approval:fixture",
            "client_msg_id": "fixture-approval", "slack_ts": "%.6f" % (float(THREAD) + 5),
            "owner_decision": "approve", "owner_command_id": "fixture-command",
            "owner_decision_at": datetime.fromtimestamp(float(THREAD) + 15, timezone.utc).isoformat()}
        trial["delivery_receipts"].append(approval)
        trial.update(owner_decision_record_count=1, owner_pause_seconds=10, machine_seconds=20)
        row = recorder.row_from_receipt(receipt, rubric, sha)
        self.assertEqual((row["wall_seconds"], row["owner_pause_seconds"], row["machine_seconds"]),
                         (30, 10, 20))
        self.assertTrue(recorder.qualifies(row))
        approval["owner_decision"] = None
        row = recorder.row_from_receipt(receipt, rubric, sha)
        self.assertIsNone(row["owner_pause_seconds"])
        self.assertIsNone(row["machine_seconds"])
        self.assertFalse(recorder.qualifies(row))

    def test_missing_or_positive_attempt_evidence_cannot_be_converted_to_zero(self):
        rubric, sha = rubric_fixture()
        for changes in ({"operational_provider_attempts_verified": None},
                {"zero_calls_in_exposed_task_projection": False}):
            receipt = verified_fixture()
            receipt["trials"][0].update(changes)
            row = recorder.row_from_receipt(receipt, rubric, sha)
            self.assertIsNone(row["actual_cost_usd"])
            self.assertIsNone(row["operational_provider_attempts"])

    def test_unstable_or_paid_runtime_cannot_support_simulation_zero(self):
        rubric, sha = rubric_fixture()
        for key, value in (("offline_configured", False), ("paid_configured", True), ("image_id", "sha256:" + "c" * 64)):
            receipt = verified_fixture()
            receipt["collection_runtime_before"][key] = value
            self.assertIsNone(recorder.row_from_receipt(receipt, rubric, sha)["actual_cost_usd"])

    def test_rubric_hash_and_recomputed_checks_must_match(self):
        rubric, sha = rubric_fixture()
        receipt = verified_fixture()
        with self.assertRaises(ValueError):
            recorder.row_from_receipt(receipt, rubric, "e" * 64)
        receipt["trials"][0]["rubric_evaluation"]["passed"] = False
        with self.assertRaises(ValueError):
            recorder.row_from_receipt(receipt, rubric, sha)

    def test_records_first_failure_then_refuses_append_without_changing_bytes(self):
        row = measured_row()
        row["wall_seconds"] = 65.18
        row["machine_seconds"] = 0  # Inconsistent accounting remains a genuine failure.
        self.assertFalse(recorder.append_row(self.log, row))
        before = self.log.read_bytes()
        following = {**measured_row(), "run_id": "DIR-LG-CD34"}
        with self.assertRaisesRegex(ValueError, "stopped"):
            recorder.append_row(self.log, following)
        self.assertEqual(before, self.log.read_bytes())
        self.assertFalse(self.log.with_name("runs.jsonl.lock").exists())

    def test_ten_distinct_rows_use_same_validator_and_eleventh_is_refused(self):
        rows = [{**measured_row(), "run_id": f"DIR-LG-{i:04X}"} for i in range(10)]
        for row in rows:
            self.assertTrue(recorder.append_row(self.log, row))
        passed, _ = evaluate_run_records(rows, mode="offline", runtime_sha=rows[0]["runtime_sha"], image_id=rows[0]["image_id"])
        self.assertTrue(passed)
        with self.assertRaises(ValueError):
            recorder.append_row(self.log, {**rows[0], "run_id": "DIR-LG-EEEE"})
        self.assertEqual(10, len(self.log.read_bytes().splitlines()))

    def test_duplicate_and_changed_image_are_refused(self):
        row = measured_row()
        recorder.append_row(self.log, row)
        before = self.log.read_bytes()
        for next_row in (row, {**row, "run_id": "DIR-LG-EEEE", "image_id": "sha256:" + "d" * 64}):
            with self.assertRaises(ValueError):
                recorder.append_row(self.log, next_row)
            self.assertEqual(before, self.log.read_bytes())

    def test_lock_or_truncated_sequence_is_not_overwritten(self):
        lock = self.log.with_name("runs.jsonl.lock")
        lock.write_bytes(b"existing operator")
        with self.assertRaises(FileExistsError):
            recorder.append_row(self.log, measured_row())
        self.assertEqual(b"existing operator", lock.read_bytes())
        lock.unlink()
        self.log.write_bytes(b'{"run_id":')
        with self.assertRaises(ValueError):
            recorder.append_row(self.log, measured_row())
        self.assertEqual(b'{"run_id":', self.log.read_bytes())

    def test_schema_rejects_extra_keys_nonfinite_measurements_and_bool_counts(self):
        for changes in ({"secret": "synthetic"}, {"wall_seconds": float("nan")}, {"task_count": True}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, jsonschema.ValidationError)):
                recorder.append_row(self.log, {**measured_row(), **changes})
        self.assertFalse(self.log.exists())

    def test_shared_single_validator_matches_sequence_failures(self):
        row = measured_row()
        for field, value in (("correctness_verified", False), ("actual_cost_usd", None), ("wall_seconds", 60), ("operational_provider_attempts", 1)):
            candidate = {**row, field: value}
            one = evaluate_run_record(candidate, mode="offline", runtime_sha=row["runtime_sha"], image_id=row["image_id"])
            many = evaluate_run_records([{**candidate, "run_id": f"DIR-LG-{i:X}"} for i in range(10)],
                mode="offline", runtime_sha=row["runtime_sha"], image_id=row["image_id"])
            self.assertEqual(one, many)

    def test_cli_retains_unknown_row_and_returns_stop_exit_code(self):
        receipt_path, rubric_path = self.root / "receipt.json", self.root / "rubric.json"
        receipt_path.write_bytes(recorder.collector.encode(receipt_fixture()))
        rubric_path.write_bytes(recorder.collector.encode(rubric_fixture()[0]))
        with redirect_stdout(io.StringIO()) as output:
            result = recorder.main(["--receipt", str(receipt_path), "--rubric", str(rubric_path), "--output", str(self.log)])
        self.assertEqual(2, result)
        self.assertTrue(json.loads(output.getvalue())["sequence_stopped"])
        self.assertEqual(1, len(self.log.read_bytes().splitlines()))

    def test_collection_failure_becomes_an_unresolved_nonqualifying_row(self):
        rubric, sha = rubric_fixture()
        receipt = {"schema": "director-slack-trial-observations/v1", "collection_failed": True,
            "observed_at": recorder.collector.utc(), "error_type": "ValueError", "details_withheld": True,
            "read_only": True, "qualified_run_count": None, "native_source_reads": [],
            "trials": [{"thread_ts": "1789210000.100000", "task_id": None, "verdict": "COLLECTION_UNAVAILABLE"}]}
        row = recorder.row_from_receipt(receipt, rubric, sha)
        self.assertTrue(row["run_id"].startswith("unresolved-thread:"))
        self.assertFalse(recorder.append_row(self.log, row))

    def test_io_failure_after_write_reports_unknown_append_without_retry(self):
        receipt_path, rubric_path = self.root / "receipt.json", self.root / "rubric.json"
        receipt_path.write_bytes(recorder.collector.encode(verified_fixture()))
        rubric_path.write_bytes(recorder.collector.encode(rubric_fixture()[0]))
        with patch.object(recorder.os, "fsync", side_effect=OSError("synthetic storage failure")), \
                redirect_stdout(io.StringIO()) as output:
            result = recorder.main(["--receipt", str(receipt_path), "--rubric", str(rubric_path), "--output", str(self.log)])
        self.assertEqual(1, result)
        self.assertIsNone(json.loads(output.getvalue())["appended"])
        self.assertEqual(1, len(self.log.read_bytes().splitlines()))


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:22 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:17 UTC | END]]
