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
    for name in ("OPENAI_MCP_ENABLED", "CLAUDE_MCP_ENABLED", "CURSOR_MCP_ENABLED",
                 "COPILOT_MCP_ENABLED", "PERPLEXITY_MCP_ENABLED", "FIRECRAWL_MCP_ENABLED",
                 "ANTIGRAVITY_MCP_READ_ENABLED"):
        child_env.pop(name, None)
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

    def test_every_roster_lane_has_its_own_server(self):
        additional = {"openai": "openai_ask", "claude": "claude_ask",
                      "cursor": "cursor_ask", "copilot": "copilot_ask",
                      "antigravity": "antigravity_get_interaction",
                      "perplexity": "perplexity_search_public",
                      "firecrawl": "firecrawl_search_public"}
        for module, action in additional.items():
            with self.subTest(module=module):
                replies = exchange(module, [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": action, "arguments": {"text": "test"}}},
                ])
                self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
                self.assertEqual({tool["name"] for tool in replies[1]["result"]["tools"]},
                                 {f"{module}_status", action})
                self.assertTrue(replies[2]["result"]["isError"])
                self.assertIn("disabled", replies[2]["result"]["content"][0]["text"])

    def test_admin_contacts_are_distinct_and_do_not_grant_authority(self):
        for module, identity in (("admin_openai", "ChatGPT/Codex"),
                                 ("admin_claude", "Claude"),
                                 ("admin_cursor", "Cursor")):
            with self.subTest(module=module):
                replies = exchange(module, [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": f"{module}_status", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": f"{module}_consult", "arguments": {"text": "test"}}},
                ])
                self.assertEqual({tool["name"] for tool in replies[0]["result"]["tools"]},
                                 {f"{module}_status", f"{module}_consult"})
                state = json.loads(replies[1]["result"]["content"][0]["text"])
                self.assertEqual(state["administrator"], identity)
                self.assertFalse(state["authority_granted"])
                self.assertTrue(replies[2]["result"]["isError"])

    def test_admin_crew_wrapper_has_explicit_contacts(self):
        replies = exchange("admin_crew", [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "admin_crew_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "admin_crew_cursor_consult", "arguments": {"text": "test"}}},
        ])
        self.assertEqual({tool["name"] for tool in replies[0]["result"]["tools"]},
                         {"admin_crew_status", "admin_crew_openai_consult",
                          "admin_crew_claude_consult", "admin_crew_cursor_consult"})
        state = json.loads(replies[1]["result"]["content"][0]["text"])
        self.assertEqual(set(state["contacts"]), {"openai", "claude", "cursor"})
        self.assertFalse(state["authority_granted"])
        self.assertTrue(replies[2]["result"]["isError"])

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
