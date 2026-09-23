# [[Codex Director | GPT-6 Astra | 2026-09-23 | 01:43 UTC | BEGIN]]
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("xai_command", Path(__file__).resolve().parents[1] / "scripts/xai-responses-command.py")
command = importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)


class ResponsesCommandTests(unittest.TestCase):
    def test_real_request_cap_preflight_capture_and_no_replay(self):
        calls = []
        response = {"object": "response", "id": "test-response", "status": "incomplete",
                    "max_output_tokens": 8192, "usage": {"output_tokens": 8192}}
        def send(endpoint, body, key):
            calls.append((endpoint, body))
            return {"token_ids": [{"token_id": 1}] * 20} if endpoint == "tokenize-text" else response
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(command.execute("fixture packet", Path(directory), "fixture", send), response)
            self.assertEqual([item[0] for item in calls], ["tokenize-text", "responses"])
            body = calls[1][1]
            self.assertEqual(body["max_output_tokens"], 8192)
            self.assertEqual(body["tools"], [])
            self.assertFalse(body["store"])
            self.assertEqual(json.loads(calls[0][1]["text"]), body)
            self.assertEqual(json.loads(next(Path(directory).glob("*.response.json")).read_text()), response)
            with self.assertRaises(FileExistsError):
                command.execute("fixture packet", Path(directory), "fixture", send)
            self.assertEqual(len(calls), 2)

    def test_bad_or_over_budget_tokenizer_never_starts_inference(self):
        for result in ({}, {"token_ids": []}, {"token_ids": [{"token_id": True}]},
                       {"token_ids": [{"token_id": 1}] * 125000}):
            with self.subTest(result_size=len(result.get("token_ids", []))), tempfile.TemporaryDirectory() as directory:
                calls = []
                def send(endpoint, body, key):
                    calls.append(endpoint)
                    return result
                with self.assertRaises(ValueError):
                    command.execute("fixture packet", Path(directory), "fixture", send)
                self.assertEqual(calls, ["tokenize-text"])
                receipt = json.loads(next(Path(directory).glob("*.receipt.json")).read_text())
                self.assertFalse(receipt["inference_started"])

    def test_missing_echo_preserves_the_real_reply_and_refuses_success(self):
        with tempfile.TemporaryDirectory() as directory:
            response = {"object": "response", "id": "test-response", "status": "completed"}
            def send(endpoint, body, key):
                return {"token_ids": [{"token_id": 1}]} if endpoint == "tokenize-text" else response
            with self.assertRaises(ValueError):
                command.execute("fixture packet", Path(directory), "fixture", send)
            self.assertEqual(json.loads(next(Path(directory).glob("*.response.json")).read_text()), response)
            self.assertTrue(json.loads(next(Path(directory).glob("*.receipt.json")).read_text())["inference_started"])
# [[Codex Director | GPT-6 Astra | 2026-09-23 | 01:43 UTC | END]]
