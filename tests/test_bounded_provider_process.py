# [[OpenAI Codex | GPT-6 | 2026-09-08 | 03:32 UTC | BEGIN]]
"""Synthetic process tests; no providers or credentials."""
import ctypes
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from director_orchestrator.bounded_process import run_bounded_process
from director_orchestrator.process_isolation import ProcessContainmentError, ProcessTreeGuard


class BoundedProviderProcessTests(unittest.TestCase):
    def run_child(self, code, *, directory=None, **kwargs):
        return run_bounded_process(
            [sys.executable, '-c', code], cwd=Path(directory or tempfile.gettempdir()),
            environment=os.environ.copy(), timeout_seconds=kwargs.pop('timeout_seconds', 3),
            output_limit_bytes=kwargs.pop('output_limit_bytes', 4096), **kwargs,
        )

    def test_normal_stdin_and_separate_outputs(self):
        result = self.run_child('import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stderr.write("err")', input_bytes=b'hello')
        self.assertEqual((0, False, False, b'hello', b'err'), result)

    def test_combined_output_flood_is_bounded(self):
        result = self.run_child('import os\nwhile True:\n os.write(1,b"x"*4096); os.write(2,b"y"*4096)', output_limit_bytes=127)
        self.assertTrue(result[2])
        self.assertLessEqual(len(result[3])+len(result[4]), 127)
        self.assertFalse(result[1])

    def test_timeout(self):
        started = time.monotonic()
        result = self.run_child('import time; time.sleep(30)', timeout_seconds=.25)
        self.assertTrue(result[1])
        self.assertLess(time.monotonic()-started, 4)

    def test_expired_shared_deadline_does_not_launch(self):
        with patch('director_orchestrator.bounded_process.launch_contained_process') as launch:
            self.assertTrue(self.run_child('pass', deadline=time.monotonic()-1)[1])
        launch.assert_not_called()

    def test_shared_deadline_does_not_reset(self):
        started = time.monotonic()
        result = self.run_child('import time; time.sleep(30)', timeout_seconds=20, deadline=started+.25)
        self.assertTrue(result[1])
        self.assertLess(time.monotonic()-started, 4)

    def test_oversized_stdin_rejected_before_launch(self):
        with patch('director_orchestrator.bounded_process.launch_contained_process') as launch:
            with self.assertRaises(ValueError):
                self.run_child('pass', input_bytes=b'xx', input_limit_bytes=1)
        launch.assert_not_called()

    def assert_process_exited(self, pid):
        if os.name != 'nt':
            return
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                self.assertEqual(0, kernel.WaitForSingleObject(handle, 0), 'descendant still running')
            finally:
                kernel.CloseHandle(handle)
        else:
            self.assertEqual(87, ctypes.get_last_error())

    def test_descendant_pipe_and_blocked_stdin_are_contained(self):
        for parent_sleeps in (False, True):
            with self.subTest(parent_sleeps=parent_sleeps), tempfile.TemporaryDirectory() as directory:
                pid_file = Path(directory)/'child.pid'
                code = ('import subprocess,sys,time,pathlib; '
                        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                        f'pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); '
                        + ('time.sleep(30)' if parent_sleeps else 'pass'))
                started = time.monotonic()
                result = self.run_child(code, directory=directory, input_bytes=b'x'*1_048_576, timeout_seconds=1)
                self.assertEqual(parent_sleeps, result[1])
                self.assertLess(time.monotonic()-started, 5)
                self.assert_process_exited(int(pid_file.read_text()))

    def test_incomplete_containment_never_returns_success(self):
        with patch.object(ProcessTreeGuard, 'wait_empty', side_effect=ProcessContainmentError('synthetic uncertainty')):
            with self.assertRaises(ProcessContainmentError):
                self.run_child('pass')


if __name__ == '__main__':
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 03:32 UTC | END]]
