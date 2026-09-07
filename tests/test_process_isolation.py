# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | BEGIN]]
from __future__ import annotations

import subprocess
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from director_orchestrator import process_isolation


class ProcessIsolationTests(unittest.TestCase):
    def test_windows_launch_attaches_job_before_resuming_suspended_child(self):
        events: list[str] = []
        process = Mock(pid=1234)

        def popen(*_args, **kwargs):
            events.append("popen")
            self.assertTrue(kwargs["creationflags"] & 0x00000004)
            return process

        class Guard:
            def __init__(self, candidate):
                self.assert_is(process, candidate)
                events.append("guard")

            @staticmethod
            def assert_is(expected, actual):
                if actual is not expected:
                    raise AssertionError("guard received the wrong process")

        with (
            patch.object(process_isolation.os, "name", "nt"),
            patch.object(process_isolation.subprocess, "Popen", side_effect=popen),
            patch.object(process_isolation, "ProcessTreeGuard", Guard),
            patch.object(
                process_isolation,
                "_resume_only_thread",
                side_effect=lambda _pid: events.append("resume"),
            ),
        ):
            returned, _guard = process_isolation.launch_contained_process(
                ("trusted.exe",), shell=False
            )

        self.assertIs(process, returned)
        self.assertEqual(["popen", "guard", "resume"], events)

    def test_windows_launch_kills_suspended_child_when_job_assignment_fails(self):
        process = Mock(pid=1234)
        process.wait.return_value = 1
        with (
            patch.object(process_isolation.os, "name", "nt"),
            patch.object(process_isolation.subprocess, "Popen", return_value=process),
            patch.object(
                process_isolation,
                "ProcessTreeGuard",
                side_effect=process_isolation.ProcessContainmentError("assignment failed"),
            ),
            patch.object(process_isolation, "_resume_only_thread") as resume,
        ):
            with self.assertRaises(process_isolation.ProcessContainmentError):
                process_isolation.launch_contained_process(("trusted.exe",), shell=False)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        resume.assert_not_called()

    def test_platform_process_containment_contract(self):
        if os.name != "nt":
            process = Mock()
            with patch.object(
                process_isolation.subprocess, "Popen", return_value=process
            ) as popen:
                returned, guard = process_isolation.launch_contained_process(
                    ("trusted",), shell=False
                )
            self.assertIs(process, returned)
            self.assertFalse(guard.active)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            return

        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary, "escaped.txt")
            child = (
                "import pathlib,sys,time; time.sleep(1); "
                "pathlib.Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]], "
                "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS)"
            )
            process, guard = process_isolation.launch_contained_process(
                (sys.executable, "-c", parent, str(marker), child),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            process.communicate(timeout=5)
            guard.terminate()
            guard.close()
            time.sleep(1.25)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | END]]
