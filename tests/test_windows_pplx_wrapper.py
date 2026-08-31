# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | BEGIN]]
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsPplxWrapperTests(unittest.TestCase):
    def test_checked_in_wrappers_are_literal_and_fail_closed(self):
        powershell = (ROOT / "scripts/windows/pplx.ps1").read_text(encoding="utf-8")
        command = (ROOT / "scripts/windows/pplx.cmd").read_text(encoding="utf-8")

        self.assertIn("@PplxArguments", powershell)
        self.assertIn("exit $LASTEXITCODE", powershell)
        self.assertIn("%*", command)
        self.assertIn("exit /b %ERRORLEVEL%", command)
        combined = (powershell + command).lower()
        self.assertNotIn("invoke-expression", combined)
        self.assertNotIn("perplexity_api_key", combined)

    def test_installed_windows_path_route_executes_when_on_windows(self):
        if os.name != "nt":
            self.assertFalse((ROOT / "scripts/windows/pplx.ps1").is_symlink())
            return
        completed = subprocess.run(
            ("powershell.exe", "-NoProfile", "-Command", "pplx --version"),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertRegex(completed.stdout, r"pplx \d{4}\.\d{2}\.\d{2}")


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | END]]
