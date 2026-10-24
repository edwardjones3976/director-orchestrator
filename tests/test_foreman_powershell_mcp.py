"""End-to-end checks for the bounded Foreman read runner."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.foreman_mcp.powershell_readonly import call

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows PowerShell runner")
class ForemanPowerShellMcpTests(unittest.TestCase):
    def test_real_read_list_and_search_use_fixed_operations(self):
        run = subprocess.run

        def integration_run(*args, **kwargs):
            self.assertEqual(kwargs.pop("timeout"), 20)
            # Exercise real operations under CI load; test the production deadline separately.
            return run(*args, timeout=60, **kwargs)

        with patch.dict(os.environ, {"FOREMAN_READ_ROOTS": str(ROOT)}), \
             patch("scripts.foreman_mcp.powershell_readonly.subprocess.run", side_effect=integration_run):
            procedure = call("foreman_read_file", {
                "path": str(ROOT / "docs" / "foreman-repair-work-orders.html")})
            self.assertIn("Foreman-controlled repair work orders", procedure["text"])
            entries = call("foreman_list_directory", {
                "path": str(ROOT / "scripts" / "foreman_mcp")})["entries"]
            self.assertIn("read_only.ps1", {entry["name"] for entry in entries})
            matches = call("foreman_search_file", {
                "path": str(ROOT / "src" / "director_orchestrator" / "policy.py"),
                "needle": "powershell"})["matches"]
            self.assertTrue(matches)

    def test_production_timeout_refuses_without_retry(self):
        with patch.dict(os.environ, {"FOREMAN_READ_ROOTS": str(ROOT)}), \
             patch("scripts.foreman_mcp.powershell_readonly.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("powershell", 20)) as run:
            with self.assertRaisesRegex(ValueError, "PowerShell read operation timed out"):
                call("foreman_read_file", {"path": str(ROOT / "docs" / "foreman-repair-work-orders.html")})
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_outside_file_and_arbitrary_command_refuse(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"FOREMAN_READ_ROOTS": str(ROOT)}):
            outside = Path(tmp) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside configured"):
                call("foreman_read_file", {"path": str(outside)})
            with self.assertRaisesRegex(ValueError, "unknown tool"):
                call("foreman_run_command", {"command": "Get-Content anything"})
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, \
             patch.dict(os.environ, {"FOREMAN_READ_ROOTS": str(ROOT)}):
            secret = Path(tmp) / ".env"
            secret.write_text("synthetic=test", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "credential files"):
                call("foreman_read_file", {"path": str(secret)})

    def test_stdio_mcp_reports_read_only_tools(self):
        env = {**os.environ, "FOREMAN_READ_ROOTS": str(ROOT)}
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        result = subprocess.run(
            [sys.executable, "-m", "scripts.foreman_mcp.powershell_readonly"],
            input=json.dumps(request) + "\n", text=True, capture_output=True,
            check=True, timeout=10, env=env, cwd=ROOT,
        )
        tools = json.loads(result.stdout)["result"]["tools"]
        self.assertEqual({item["name"] for item in tools},
                         {"foreman_read_status", "foreman_read_file",
                          "foreman_list_directory", "foreman_search_file"})
        self.assertTrue(all(item["annotations"]["readOnlyHint"] for item in tools))


if __name__ == "__main__":
    unittest.main()
