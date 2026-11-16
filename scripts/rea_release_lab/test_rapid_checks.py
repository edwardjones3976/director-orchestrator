"""Regression tests for rapid_checks timestamp parsing and synthetic withholding."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rapid_checks


def _write_har(path, timestamps):
    entries = []
    for index, started in enumerate(timestamps):
        method = "PUT" if index == 1 else "GET"
        entries.append({
            "startedDateTime": started,
            "time": 0.01,
            "request": {"method": method, "url": "http://127.0.0.1:8080/api/folders/1"},
            "response": {"status": 200},
        })
    path.write_text(json.dumps({"log": {"entries": entries}}) + "\n", encoding="utf-8")


class RapidChecksTest(unittest.TestCase):
    def test_timing_handles_zero_microseconds_without_dot(self):
        with tempfile.TemporaryDirectory() as tmp:
            har = Path(tmp) / "capture.har"
            _write_har(har, [
                "2026-11-15T00:00:00Z",
                "2026-11-15T00:00:00.000400Z",
                "2026-11-15T00:00:01Z",
            ])
            info = rapid_checks.timing(har)
            self.assertEqual(info["requests"], 3)
            self.assertAlmostEqual(info["minimum_start_gap_microseconds"], 400.0)
            self.assertEqual(info["same_millisecond_adjacent_pairs"], 1)

    def test_timing_handles_short_fractions_and_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            har = Path(tmp) / "capture.har"
            _write_har(har, [
                "2026-11-15T01:00:00+01:00",
                "2026-11-15T00:00:00.000250Z",
                "2026-11-15T00:00:00.5Z",
                "2026-11-15T00:00:00.500900Z",
                "2026-11-15T00:00:00.501000Z",
            ])
            info = rapid_checks.timing(har)
            self.assertAlmostEqual(info["minimum_start_gap_microseconds"], 100.0)
            self.assertEqual(info["same_millisecond_adjacent_pairs"], 2)

    def test_main_exercises_schema_refusal_and_unusable_completion_withholding(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "rapid"
            seen_put_times = {}

            def fake_capture(path, names):
                _write_har(path, [
                    "2026-11-15T00:00:00Z",
                    "2026-11-15T00:00:00.000100Z",
                    "2026-11-15T00:00:00.001000Z",
                ])
                return 3

            def fake_run(command, capture_output=True, timeout=90):
                subcommand = command[1]
                if subcommand == "environment-build":
                    har_path = Path(command[2])
                    case_name = har_path.parent.name
                    entries = json.loads(har_path.read_text(encoding="utf-8"))["log"]["entries"]
                    put_entry = next(e for e in entries if e["request"]["method"] == "PUT")
                    seen_put_times[case_name] = put_entry.get("time", "<missing>")
                    if "time" not in put_entry:
                        payload = {"ok": False, "error": {"code": "invalid_request", "category": "invalid_input"}}
                        return subprocess.CompletedProcess(command, 1, json.dumps(payload).encode("utf-8"), b"")
                    if case_name in ("synthetic-overlap", "synthetic-unusable-duration"):
                        writes = {"served": [], "withheld": [{"tool": "put_folders_by_id", "reason": "unconfirmed"}]}
                    else:
                        writes = {"served": [{"tool": "put_folders_by_id"}], "withheld": []}
                    payload = {"ok": True, "data": {"coverage": {"writes": writes}}}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode("utf-8"), b"")
                if subcommand == "inspect-web-network-capture":
                    payload = {"ok": True, "data": {"evidence_id": "ev-" + ("a" * 64)}}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode("utf-8"), b"")
                if subcommand == "environment-compare":
                    payload = {"ok": True, "data": {"summary": {"equal": 21, "steps": 21, "unobserved": 0}}}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode("utf-8"), b"")
                raise AssertionError("Unexpected command: " + str(command))

            with patch.object(rapid_checks, "capture", side_effect=fake_capture), \
                 patch.object(rapid_checks.subprocess, "run", side_effect=fake_run):
                rapid_checks.main(output)

            self.assertEqual(seen_put_times["synthetic-unknown-duration"], "<missing>")
            self.assertEqual(seen_put_times["synthetic-unusable-duration"], 1e20)
            self.assertEqual(seen_put_times["synthetic-overlap"], 100)
            self.assertEqual(seen_put_times["synthetic-disjoint"], 0.01)

            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["passed"])
            by_case = {item["case"]: item for item in summary["results"]}
            self.assertEqual(by_case["synthetic-unknown-duration"]["kind"], "native-schema-regression")
            self.assertEqual(by_case["synthetic-unusable-duration"]["kind"], "synthetic-interval-regression")

    def test_main_rejects_served_write_on_unusable_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "rapid"

            def fake_capture(path, names):
                _write_har(path, [
                    "2026-11-15T00:00:00Z",
                    "2026-11-15T00:00:00.000100Z",
                    "2026-11-15T00:00:00.001000Z",
                ])
                return 3

            def fake_run(command, capture_output=True, timeout=90):
                subcommand = command[1]
                if subcommand == "environment-build":
                    har_path = Path(command[2])
                    case_name = har_path.parent.name
                    entries = json.loads(har_path.read_text(encoding="utf-8"))["log"]["entries"]
                    put_entry = next(e for e in entries if e["request"]["method"] == "PUT")
                    if "time" not in put_entry:
                        payload = {"ok": False, "error": {"code": "invalid_request", "category": "invalid_input"}}
                        return subprocess.CompletedProcess(command, 1, json.dumps(payload).encode("utf-8"), b"")
                    if case_name == "synthetic-overlap":
                        writes = {"served": [], "withheld": [{"tool": "put_folders_by_id", "reason": "unconfirmed"}]}
                    else:
                        writes = {"served": [{"tool": "put_folders_by_id"}], "withheld": []}
                    return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True, "data": {"coverage": {"writes": writes}}}).encode("utf-8"), b"")
                if subcommand == "inspect-web-network-capture":
                    return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True, "data": {"evidence_id": "ev-" + ("a" * 64)}}).encode("utf-8"), b"")
                return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True, "data": {"summary": {"equal": 21, "steps": 21, "unobserved": 0}}}).encode("utf-8"), b"")

            with patch.object(rapid_checks, "capture", side_effect=fake_capture), \
                 patch.object(rapid_checks.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(AssertionError):
                    rapid_checks.main(output)


if __name__ == "__main__":
    unittest.main()
