"""Protocol and paid-call refusal tests for dedicated provider servers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.provider_mcp.common import reserve
from scripts.provider_mcp.secret_store import import_key, load_key, store_path


def exchange(module: str, requests: list[dict], env: dict[str, str] | None = None) -> list[dict]:
    child_env = dict(os.environ)
    child_env.pop("DIRECTOR_MCP_PAID_CALLS_ENABLED", None)
    child_env.pop("XAI_MCP_ENABLED", None)
    child_env.pop("ZAI_MCP_ENABLED", None)
    if env:
        child_env.update(env)
    result = subprocess.run(
        [sys.executable, "-m", f"scripts.provider_mcp.{module}"],
        input="".join(json.dumps(item) + "\n" for item in requests), text=True,
        capture_output=True, env=child_env, timeout=5, check=True,
    )
    if result.stderr:
        raise AssertionError(result.stderr)
    return [json.loads(line) for line in result.stdout.splitlines()]


class ProviderMcpTests(unittest.TestCase):
    def test_each_server_exposes_only_its_provider(self):
        for module in ("xai", "zai"):
            with self.subTest(module=module):
                replies = exchange(module, [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                ])
                self.assertEqual(len(replies), 2)
                self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
                self.assertEqual({t["name"] for t in replies[1]["result"]["tools"]},
                                 {f"{module}_status", f"{module}_ask"})

    def test_paid_calls_refuse_by_default(self):
        for module in ("xai", "zai"):
            with self.subTest(module=module):
                reply = exchange(module, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": f"{module}_ask",
                                                      "arguments": {"text": "hello"}}}])[0]
                self.assertTrue(reply["result"]["isError"])
                self.assertIn("disabled", reply["result"]["content"][0]["text"])

    def test_zai_requires_activation_even_when_switches_are_on(self):
        reply = exchange("zai", [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "zai_ask", "arguments": {"text": "hello"}}}],
                         {"DIRECTOR_MCP_PAID_CALLS_ENABLED": "true", "ZAI_MCP_ENABLED": "true",
                          "ZAI_PRODUCTION_ACTIVATION": ""})[0]
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("activation", reply["result"]["content"][0]["text"])

    def test_zai_activation_format_matches_director(self):
        from scripts.provider_mcp.zai import activated, redact
        with patch.dict(os.environ, {"ZAI_PRODUCTION_ACTIVATION": "a" * 64}):
            self.assertFalse(activated())
        with patch.dict(os.environ, {"ZAI_PRODUCTION_ACTIVATION": "sha256:" + "a" * 64}):
            self.assertTrue(activated())
        self.assertEqual(redact({"synthetic-key": ["synthetic-key"]}, "synthetic-key"),
                         {"[REDACTED]": ["[REDACTED]"]})

    def test_status_does_not_expose_key_value(self):
        reply = exchange("xai", [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "xai_status", "arguments": {}}}],
                         {"XAI_API_KEY": "secret-example-only"})[0]
        self.assertNotIn("secret-example-only", json.dumps(reply))
        self.assertTrue(json.loads(reply["result"]["content"][0]["text"])["credential_configured"])

    def test_attempt_cap_and_replay_reservation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(Path, "home", return_value=Path(tmp)):
            reserve("xai", "one", 2)
            with self.assertRaises(FileExistsError):
                reserve("xai", "one", 2)
            reserve("xai", "two", 2)
            with self.assertRaisesRegex(ValueError, "daily call cap"):
                reserve("xai", "three", 2)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_zai_secret_store_encrypts_for_current_user(self):
        sample = b"synthetic-zai-test-key-12345"
        with tempfile.TemporaryDirectory() as tmp, patch.object(Path, "home", return_value=Path(tmp)):
            with patch.dict(os.environ, {"ZAI_API_KEY": ""}):
                import_key(sample)
                self.assertTrue(store_path().is_file())
                self.assertNotIn(sample, store_path().read_bytes())
                self.assertEqual(load_key(), sample.decode("ascii"))
                with self.assertRaisesRegex(ValueError, "already stored"):
                    import_key(sample)


if __name__ == "__main__":
    unittest.main()
