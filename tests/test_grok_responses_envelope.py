# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 01:39 UTC | BEGIN]]
"""Genuine Responses captures cross the same boundaries as native Grok replies."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from director_orchestrator.packet_contracts import canonical_json_bytes, validate_packet
from director_orchestrator.primary_transports import (
    NativeCommandPacketTransport,
    grok_terminal_success,
    normalize_native_provider_envelope,
)
from director_orchestrator.provider_validation import ValidationError, normalize_capture
from tests.test_provider_validation import work_packet


def response():
    return {
        "object": "response", "id": "resp-xai-fixture", "model": "grok-4.6",
        "status": "completed", "error": None, "incomplete_details": None,
        "max_output_tokens": 40,
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": json.dumps(
                 {"status": "SUCCEEDED", "outcome": "Canary analyzed"})}]},
        ],
        "usage": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140,
                  "input_tokens_details": {"cached_tokens": 30},
                  "output_tokens_details": {"reasoning_tokens": 25},
                  "cost_in_usd_ticks": 5_000_000},
    }


class GrokResponsesEnvelopeTests(unittest.TestCase):
    def send(self, native, *, tokens=125_000, budget=0.55):
        work = work_packet()
        raw = canonical_json_bytes(native)
        observed = []
        with tempfile.TemporaryDirectory() as directory:
            transport = NativeCommandPacketTransport("grok", ["fixture-command"],
                cwd=directory, prompt_mode="grok-prompt-file")

            def run(argv, **kwargs):
                prompt = Path(argv[argv.index("--prompt-file") + 1]).read_bytes()
                self.assertTrue(prompt.endswith(canonical_json_bytes(work)))
                self.assertIsNone(kwargs["input_bytes"])
                return subprocess.CompletedProcess(argv, 0, stdout=raw, stderr=b"")

            with patch("director_orchestrator.primary_transports._run_bounded_command", side_effect=run):
                result = transport.send(canonical_json_bytes(work), context=SimpleNamespace(
                    fencing_generation=1, timeout_seconds=30, total_token_limit=tokens,
                    budget_usd=budget, execution_observer=lambda *args: observed.append(args)))
            self.assertEqual([], list(Path(directory).glob("director-packet-*.txt")))
        self.assertEqual([("grok", raw, result)], observed)
        return validate_packet("result", result)

    def test_completed_capture_and_actual_command_share_genuine_response_normalization(self):
        native = response()
        original = copy.deepcopy(native)
        # Only the authenticated API id is a run reference; provider text or
        # a stray CLI-style field cannot replace the original packet bindings.
        native["session_id"] = "untrusted-alias"
        native["output"][1]["content"][0]["text"] = json.dumps({
            "status": "SUCCEEDED", "outcome": "Canary analyzed", "task_id": "wrong-task"})
        result = self.send(native)
        self.assertEqual(result, normalize_capture("grok", work_packet(), canonical_json_bytes(native)))
        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual("resp-xai-fixture", result["run_reference"])
        self.assertEqual(work_packet()["task_id"], result["task_id"])
        self.assertEqual({"model": "grok-4.6", "input_tokens": 100,
            "output_tokens": 40, "total_tokens": 140, "cost_usd": 0.0005}, result["usage"])
        self.assertNotIn("stopReason", native)
        self.assertNotIn("num_turns", native)
        self.assertEqual(original["usage"], native["usage"])

    def test_incomplete_and_error_responses_preserve_partial_usage_but_never_qualify(self):
        for changes in ({"status": "incomplete"}, {"status": "failed"},
                        {"status": "cancelled"}, {"status": "in_progress"},
                        {"error": {"code": "server_error"}},
                        {"incomplete_details": {"reason": "max_output_tokens"}}):
            with self.subTest(changes=changes):
                native = response()
                native.update(changes)
                result = self.send(native)
                self.assertEqual("FAILED", result["status"])
                self.assertEqual(140, result["usage"]["total_tokens"])
                self.assertEqual("Canary analyzed", result["outcome"])
                self.assertFalse(grok_terminal_success(native))
                with self.assertRaises(ValidationError):
                    normalize_capture("grok", work_packet(), canonical_json_bytes(native))

    def test_completed_response_does_not_rehabilitate_model_reported_failure(self):
        native = response()
        native["output"][1]["content"][0]["text"] = json.dumps({
            "status": "FAILED", "outcome": "Missing required input", "blockers": ["No input"]})
        result = self.send(native)
        self.assertEqual("FAILED", result["status"])
        self.assertEqual(["No input"], result["blockers"])

    def test_no_text_malformed_identity_and_unexpected_output_are_rejected(self):
        cases = [
            {"id": ""}, {"model": ""}, {"output": []}, {"output": None},
            {"output": [{"type": "reasoning", "summary": []}]},
            {"output": [{"type": "function_call", "name": "unrequested_tool"}]},
            {"output": [{"type": "message", "role": "user", "status": "completed", "content": []}]},
            {"output": [{"type": "message", "role": "assistant", "status": "incomplete", "content": []}]},
            {"output": [{"type": "message", "role": "assistant", "status": "completed",
                         "content": [{"type": "refusal", "refusal": "No"}]}]},
            {"output": [{"type": "message", "role": "assistant", "status": "completed",
                         "content": [{"type": "output_text", "text": "   "}]}]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                native = response()
                native.update(changes)
                with self.assertRaises(RuntimeError):
                    normalize_native_provider_envelope("grok", work_packet(), native)
                with self.assertRaises((RuntimeError, ValidationError)):
                    normalize_capture("grok", work_packet(), canonical_json_bytes(native))

    def test_usage_requires_consistent_nonnegative_counts_and_does_not_double_count_reasoning(self):
        valid = response()["usage"]
        cases = [None, {}, {**valid, "input_tokens": True}, {**valid, "output_tokens": -1},
                 {**valid, "total_tokens": 165},  # Incorrectly adds reasoning a second time.
                 {**valid, "output_tokens_details": {"reasoning_tokens": 41}},
                 {**valid, "output_tokens_details": {"reasoning_tokens": False}},
                 {**valid, "input_tokens_details": {"cached_tokens": 101}},
                 {**valid, "cost_in_usd_ticks": -1}, {**valid, "cost_in_usd_ticks": True}]
        for usage in cases:
            with self.subTest(usage=usage):
                native = response()
                native["usage"] = usage
                with self.assertRaises(RuntimeError):
                    normalize_native_provider_envelope("grok", work_packet(), native)
        native = response()
        native["usage"].pop("cost_in_usd_ticks")
        self.assertIsNone(self.send(native)["usage"]["cost_usd"])

    def test_over_attempt_caps_fail_with_usage_and_raw_capture_retained(self):
        for limits in ({"tokens": 139}, {"budget": 0.00049}):
            with self.subTest(limits=limits):
                result = self.send(response(), **limits)
                self.assertEqual("FAILED", result["status"])
                self.assertEqual(140, result["usage"]["total_tokens"])
                self.assertEqual(0.0005, result["usage"]["cost_usd"])
                self.assertIn("admitted attempt limit", result["blockers"][0])
        self.assertEqual("SUCCEEDED", self.send(response(), tokens=140, budget=0.0005)["status"])

    def test_echoed_output_cap_includes_reasoning_and_cannot_rehabilitate_over_cap_reply(self):
        # 15 visible + 25 reasoning = 40 output tokens, not merely the 15 visible.
        for cap in (39, 15, 0, True, None, "40"):
            with self.subTest(cap=cap):
                native = response()
                native["max_output_tokens"] = cap
                result = self.send(native)
                self.assertEqual("FAILED", result["status"])
                self.assertEqual(40, result["usage"]["output_tokens"])
                self.assertEqual(0.0005, result["usage"]["cost_usd"])
                with self.assertRaises(ValidationError):
                    normalize_capture("grok", work_packet(), canonical_json_bytes(native))

    def test_legacy_cli_keeps_its_own_terminal_rules_and_other_lanes_cannot_adopt_responses(self):
        native = {"sessionId": "cli-session", "stopReason": "end_turn", "num_turns": 1,
                  "text": "CLI completed", "usage": {"output_tokens": 2}}
        self.assertEqual("SUCCEEDED", normalize_capture(
            "grok", work_packet(), canonical_json_bytes(native))["status"])
        for changes in ({"stopReason": "max_tokens"}, {"num_turns": 2}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                normalize_capture("grok", work_packet(), canonical_json_bytes({**native, **changes}))
        with self.assertRaises(ValidationError):
            normalize_capture("claude", work_packet(), canonical_json_bytes(response()))


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 01:39 UTC | END]]
