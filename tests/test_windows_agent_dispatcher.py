# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | BEGIN]]
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = ROOT / "scripts" / "windows" / "agent-provider.ps1"
INSTALLER = ROOT / "scripts" / "windows" / "install-agent-provider.ps1"
SHIM = ROOT / "scripts" / "windows" / "agent.cmd"


class WindowsAgentDispatcherStaticTests(unittest.TestCase):
    def test_dispatcher_is_fail_closed_and_does_not_evaluate_output(self) -> None:
        text = DISPATCHER.read_text(encoding="utf-8")
        self.assertIn("'cursor' { 'cursor-agent'", text)
        self.assertIn("'grok' { 'grok'", text)
        self.assertIn("default { Stop-WithError", text)
        self.assertIn("-CommandType Application", text)
        self.assertIn("@ProviderArguments", text)
        self.assertNotIn("Invoke-Expression", text)
        self.assertNotIn("iex ", text.casefold())

    def test_installer_is_idempotent_and_protects_unmanaged_files(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("if ($targetText -eq $sourceText)", text)
        self.assertIn("Refusing to overwrite existing unmanaged executable", text)
        self.assertIn("agent.ps1", text)
        self.assertIn("agent.cmd", text)

    def test_windows_assets_exist_without_platform_skip(self) -> None:
        self.assertTrue(DISPATCHER.is_file())
        self.assertTrue(INSTALLER.is_file())
        self.assertTrue(SHIM.is_file())


class WindowsAgentDispatcherExecutionTests(
    unittest.TestCase if sys.platform == "win32" else object
):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.worktree = self.base / "repo with spaces"
        self.bin_dir = self.base / "fake bin"
        self.worktree.mkdir()
        self.bin_dir.mkdir()
        subprocess.run(["git", "init", "-q", str(self.worktree)], check=True)
        self.capture = self.base / "arguments.json"
        self._write_fake_provider("cursor-agent.cmd", 23)
        self._write_fake_provider("grok.cmd", 0)
        self.environment = os.environ.copy()
        self.environment["PATH"] = str(self.bin_dir) + os.pathsep + self.environment["PATH"]
        self.environment["AGENT_CAPTURE"] = str(self.capture)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_fake_provider(self, name: str, exit_code: int) -> None:
        helper = self.bin_dir / "capture.py"
        helper.write_text(
            "import json, os, sys\n"
            "open(os.environ['AGENT_CAPTURE'], 'w', encoding='utf-8').write(json.dumps(sys.argv[1:], ensure_ascii=False))\n"
            "print('child stdout')\n"
            "print('child stderr', file=sys.stderr)\n"
            f"raise SystemExit({exit_code})\n",
            encoding="utf-8",
        )
        (self.bin_dir / name).write_text(
            f'@"{sys.executable}" "{helper}" %*\r\n'
            f"@exit /b {exit_code}\r\n",
            encoding="utf-8",
        )

    def _run(self, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(DISPATCHER),
                *arguments,
            ],
            cwd=cwd or self.worktree,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cursor_preserves_arguments_unicode_streams_and_exit_code(self) -> None:
        injection = "; echo SHOULD_NOT_RUN"
        result = self._run(
            "--provider",
            "cursor",
            "--literal",
            "two words",
            "snowman ☃",
            injection,
        )
        self.assertEqual(result.returncode, 23, (result.stdout, result.stderr))
        self.assertIn("child stdout", result.stdout)
        self.assertIn("child stderr", result.stderr)
        self.assertEqual(
            json.loads(self.capture.read_text(encoding="utf-8")),
            ["--literal", "two words", "snowman ☃", injection],
        )
        self.assertNotIn("SHOULD_NOT_RUN\r\n", result.stdout)

    def test_grok_dispatches_to_grok(self) -> None:
        result = self._run("--provider", "grok", "bounded")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.capture.read_text(encoding="utf-8")), ["bounded"])

    def test_unknown_and_missing_provider_fail_closed(self) -> None:
        unknown = self._run("--provider", "claude")
        missing = self._run()
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("unknown provider", unknown.stderr)
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_non_worktree_and_generic_user_home_are_rejected(self) -> None:
        non_repo = self.base / "not-a-repo"
        non_repo.mkdir()
        result = self._run("--provider", "grok", cwd=non_repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a Git worktree", result.stderr)

        user_home = Path.home()
        home_result = self._run("--provider", "grok", cwd=user_home)
        self.assertNotEqual(home_result.returncode, 0)
        self.assertIn("refusing generic working directory", home_result.stderr)

    def test_installer_is_idempotent_and_refuses_unmanaged_collision(self) -> None:
        destination = self.base / "managed user bin"
        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "-Destination",
            str(destination),
            "-SkipPathUpdate",
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        first = (destination / "agent.ps1").read_bytes()
        subprocess.run(command, check=True, capture_output=True, text=True)
        self.assertEqual((destination / "agent.ps1").read_bytes(), first)

        installed_environment = self.environment.copy()
        installed_environment["PATH"] = (
            str(destination) + os.pathsep + installed_environment["PATH"]
        )
        installed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "& agent.cmd --provider grok 'installed route'",
            ],
            cwd=self.worktree,
            env=installed_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, (installed.stdout, installed.stderr))
        self.assertEqual(
            json.loads(self.capture.read_text(encoding="utf-8")), ["installed route"]
        )

        (destination / "agent.cmd").write_text("unmanaged", encoding="utf-8")
        collision = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertNotEqual(collision.returncode, 0)
        self.assertEqual((destination / "agent.cmd").read_text(encoding="utf-8"), "unmanaged")


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:02 UTC | END]]
