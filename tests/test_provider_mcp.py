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

    def test_group_wrappers_have_exact_membership(self):
        expected_admin = {"openai", "claude", "cursor"}
        expected_workers = {"xai", "antigravity", "perplexity", "firecrawl",
                            "copilot", "zai"}
        for group, members in (("admin", expected_admin),
                               ("providers", expected_admin | expected_workers),
                               ("workers", expected_workers)):
            with self.subTest(group=group):
                replies = exchange(group, [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": f"{group}_status", "arguments": {}}},
                ], {"GROK_BOT_MCP_SCRIPT": ""})
                self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
                names = {tool["name"] for tool in replies[1]["result"]["tools"]}
                self.assertIn(f"{group}_status", names)
                for member in members:
                    self.assertTrue(any(name.startswith(f"{group}_{member}_")
                                        for name in names))
                self.assertEqual(any("_grok_bot_" in name for name in names),
                                 group != "admin")
                state = json.loads(replies[2]["result"]["content"][0]["text"])
                self.assertEqual(set(state["contacts"]),
                                 members | ({"grok_bot"} if group != "admin" else set()))
                self.assertFalse(state["authority_granted"])

    def test_group_actions_keep_original_gates_and_no_fallback(self):
        for group, action in (("admin", "admin_openai_consult"),
                              ("providers", "providers_openai_ask"),
                              ("workers", "workers_xai_ask")):
            with self.subTest(group=group):
                reply = exchange(group, [{"jsonrpc": "2.0", "id": 1,
                                          "method": "tools/call",
                                          "params": {"name": action,
                                                     "arguments": {"text": "test"}}}],
                                 {"GROK_BOT_MCP_SCRIPT": ""})[0]
                self.assertTrue(reply["result"]["isError"])
                self.assertIn("disabled", reply["result"]["content"][0]["text"])
        reply = exchange("workers", [{"jsonrpc": "2.0", "id": 1,
                                       "method": "tools/call",
                                       "params": {"name": "workers_openai_ask",
                                                  "arguments": {"text": "test"}}}],
                         {"GROK_BOT_MCP_SCRIPT": ""})[0]
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("unknown group tool", reply["result"]["content"][0]["text"])

    def test_researchers_exposes_only_two_public_searches(self):
        replies = exchange("researchers", [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "researchers_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "researchers_perplexity_search",
                        "arguments": {"query": "public example"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "researchers_firecrawl_search",
                        "arguments": {"query": "public example"}}},
        ])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual({tool["name"] for tool in replies[1]["result"]["tools"]},
                         {"researchers_status", "researchers_perplexity_search",
                          "researchers_firecrawl_search"})
        state = replies[2]["result"]["structuredContent"]
        self.assertEqual(set(state["contacts"]), {"perplexity", "firecrawl"})
        self.assertEqual(state["delivery"], "returned_to_caller")
        self.assertFalse(state["authority_granted"])
        for reply in replies[3:]:
            self.assertTrue(reply["result"]["isError"])
            self.assertIn("disabled", reply["result"]["content"][0]["text"])

    def test_researchers_returns_source_results_without_rewriting(self):
        from scripts.provider_mcp import researchers
        hits = [{"title": "Example", "url": "https://example.org/source",
                 "snippet": "Original source excerpt"}]
        for provider, tool in (("perplexity", "researchers_perplexity_search"),
                               ("firecrawl", "researchers_firecrawl_search")):
            with self.subTest(provider=provider):
                original = researchers.SEARCHERS[tool]
                try:
                    researchers.SEARCHERS[tool] = (provider, lambda query: {"results": hits})
                    result = researchers.call(tool, {"query": "public example"})
                finally:
                    researchers.SEARCHERS[tool] = original
                self.assertEqual(result["results"], hits)
                self.assertEqual(result["query"], "public example")
                self.assertEqual(result["delivery"], "returned_to_caller")

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

    def test_firecrawl_reports_provider_failure_as_failed_receipt(self):
        from scripts.provider_mcp import firecrawl

        with patch.dict(os.environ, {"DIRECTOR_MCP_PAID_CALLS_ENABLED": "true",
                                     "FIRECRAWL_MCP_ENABLED": "true",
                                     "FIRECRAWL_API_KEY": "synthetic-test-key"}), \
             patch.object(firecrawl, "reserve", return_value=Path("test-receipt")), \
             patch.object(firecrawl, "finish") as finish, \
             patch.object(firecrawl, "post_json", return_value={"success": False,
                                                                 "error": "provider rejected"}):
            with self.assertRaisesRegex(RuntimeError, "unsuccessful"):
                firecrawl.search("public example")
            finish.assert_called_once_with(Path("test-receipt"), "FAILED_OR_AMBIGUOUS")

    def test_antigravity_requires_exact_interaction_authorization(self):
        from scripts.provider_mcp import antigravity

        with patch.dict(os.environ, {"ANTIGRAVITY_MCP_READ_ENABLED": "true",
                                     "ANTIGRAVITY_MCP_ALLOWED_INTERACTION_IDS": "approved_1",
                                     "GEMINI_API_KEY": "synthetic-test-key"}), \
             patch.object(antigravity, "get_json", return_value={"id": "approved_1"}) as get_json:
            with self.assertRaisesRegex(ValueError, "exact authorization"):
                antigravity.get_interaction("other_1")
            get_json.assert_not_called()
            self.assertEqual(antigravity.get_interaction("approved_1")["interaction"],
                             {"id": "approved_1"})

    def test_grok_bot_bridge_does_not_pass_unrelated_credentials(self):
        from scripts.provider_mcp import groups

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "grok_bot_mcp.py"
            script.touch()
            reply = {"result": {"content": [{"text": "{}"}]}}
            completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(reply) + "\n")
            with patch.dict(os.environ, {"GROK_BOT_MCP_SCRIPT": str(script),
                                         "OPENAI_API_KEY": "synthetic-test-key",
                                         "GROK_BOT_DESKTOP_INPUT_ENABLED": "true"}), \
                 patch.object(groups.subprocess, "run", return_value=completed) as run:
                groups.grok_bot_call("grok_bot_status", {})
            child_env = run.call_args.kwargs["env"]
            self.assertNotIn("OPENAI_API_KEY", child_env)
            self.assertEqual(child_env["GROK_BOT_DESKTOP_INPUT_ENABLED"], "true")

    def test_cursor_requires_workspace_read_boundary(self):
        from scripts.provider_mcp import cursor

        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"CURSOR_CONFIG_DIR": tmp}):
            self.assertFalse(cursor.workspace_read_boundary())
            (Path(tmp) / "cli-config.json").write_text(
                json.dumps({"sandbox": {"readBoundary": "workspace"}}), encoding="utf-8")
            self.assertTrue(cursor.workspace_read_boundary())

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
