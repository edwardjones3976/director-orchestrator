# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | BEGIN]]
from __future__ import annotations

import json
import os
import subprocess
import tempfile
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
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            destination = base / "installed bin"
            capture = base / "wsl-arguments.json"
            harness = base / "invoke-installed-pplx.ps1"
            harness.write_text(
                "function global:wsl.exe {\n"
                "    param([Parameter(ValueFromRemainingArguments = $true)]\n"
                "          [string[]] $WslArguments)\n"
                "    $WslArguments | ConvertTo-Json -Compress | "
                "Set-Content -LiteralPath $env:PPLX_TEST_CAPTURE -Encoding utf8\n"
                "    Write-Output 'pplx 2026.09.02'\n"
                "    $global:LASTEXITCODE = 0\n"
                "}\n"
                "pplx --version\n"
                "exit $LASTEXITCODE\n",
                encoding="utf-8",
            )
            install = subprocess.run(
                (
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "scripts/windows/install-pplx-wrapper.ps1"),
                    "-Destination",
                    str(destination),
                    "-SkipPathUpdate",
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, install.returncode, install.stderr)
            environment = os.environ.copy()
            environment["PATH"] = str(destination) + os.pathsep + environment["PATH"]
            environment["PPLX_TEST_CAPTURE"] = str(capture)
            completed = subprocess.run(
                (
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                ),
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertRegex(completed.stdout, r"pplx \d{4}\.\d{2}\.\d{2}")
            self.assertEqual(
                [
                    "--distribution",
                    "Ubuntu",
                    "--exec",
                    "/home/edward/.local/bin/pplx",
                    "--version",
                ],
                json.loads(capture.read_text(encoding="utf-8-sig")),
            )


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:02 UTC | END]]
