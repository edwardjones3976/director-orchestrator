"""Tests for Cursor cli-config permission repair."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.cursor_cli_config_repair import (
    FOREMAN_REGISTER_READ_TOOLS,
    RETIRED_MCP_SERVERS,
    assert_retired_servers_absent,
    repair_cli_config,
    sha256_bytes,
)


class CursorCliConfigRepairTests(unittest.TestCase):
    def test_removes_retired_allows_and_adds_foreman_read_tools(self):
        before = {
            "version": 1,
            "permissions": {
                "allow": [
                    "Mcp(windows-mcp:*)",
                    "Mcp(admin:*)",
                    "Mcp(slack:slack_send_message)",
                ],
                "deny": ["Shell(rm)"],
            },
        }
        after, removed = repair_cli_config(before)
        self.assertEqual(
            removed,
            ["Mcp(windows-mcp:*)", "Mcp(admin:*)"],
        )
        allow = after["permissions"]["allow"]
        self.assertIn("Mcp(slack:slack_send_message)", allow)
        for tool in FOREMAN_REGISTER_READ_TOOLS:
            self.assertIn(tool, allow)
        self.assertEqual(after["permissions"]["deny"], ["Shell(rm)"])

    def test_refuses_when_retired_server_still_in_mcp_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_path = Path(tmp) / "mcp.json"
            mcp_path.write_text(
                json.dumps({"mcpServers": {"admin": {"command": "noop"}}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                assert_retired_servers_absent(mcp_path)

    def test_accepts_clean_mcp_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_path = Path(tmp) / "mcp.json"
            mcp_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "foreman-register": {"command": "python"},
                            "slack": {"url": "https://example.test"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            assert_retired_servers_absent(mcp_path)

    def test_rejects_foreman_register_wildcard_allow(self):
        before = {
            "permissions": {
                "allow": ["Mcp(foreman-register:*)"] + list(FOREMAN_REGISTER_READ_TOOLS),
            }
        }
        with self.assertRaises(ValueError):
            repair_cli_config(before)

    def test_stable_hash_helper(self):
        data = b'{"a":1}'
        self.assertEqual(
            sha256_bytes(data),
            "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862",
        )

    def test_all_retired_server_names_covered(self):
        self.assertEqual(len(RETIRED_MCP_SERVERS), 9)


if __name__ == "__main__":
    unittest.main()
