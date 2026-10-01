# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:25 UTC | BEGIN]]
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
            try:
                # wait() is the parent-exit bound. communicate() also waits on
                # inherited pipes held by the detached grandchild, which is
                # cleanup, not spawn.
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.fail("contained parent did not exit within the spawn allowance")
            finally:
                process_isolation.stop_contained_process(process, guard, grace_seconds=5.0)
            time.sleep(1.25)
            self.assertFalse(marker.exists())

    def test_stop_ends_the_owned_tree_reaps_the_child_and_closes_its_pipes(self):
        """A real local child that leaves a descendant behind, stopped mid-run.

        The parent spawns a grandchild that would write a marker after a
        second and then keeps running itself. `stop_contained_process` must
        end both, reap the parent inside its own grace period, close the
        pipes and release the guard, and say so. On Windows the grandchild
        is detached into its own process group, which only the Job reaches;
        on POSIX it stays in the parent's session, which the group signal
        reaches - a grandchild that called `setsid()` would not be, and that
        is the documented limit, not a claim tested here.
        """
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary, "descendant-outlived-us.txt")
            grandchild = (
                "import pathlib,sys,time; time.sleep(1); "
                "pathlib.Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
            )
            detach = (
                "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS"
                if os.name == "nt" else "close_fds=True"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]], " + detach + "); "
                "sys.stdout.write('launched'); sys.stdout.flush(); time.sleep(60)"
            )
            process, guard = process_isolation.launch_contained_process(
                (sys.executable, "-c", parent, str(marker), grandchild),
                shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            # The parent has spawned its descendant once it reports so.
            self.assertEqual(b"launched", process.stdout.read(8))
            started = time.monotonic()
            observed = process_isolation.stop_contained_process(process, guard, grace_seconds=5.0)
            self.assertLess(time.monotonic() - started, 5.0, "stopping is bounded by the grace period")
            self.assertIsNotNone(process.poll(), "the immediate child is reaped, not merely signalled")
            self.assertTrue(observed["child_stopped"])
            self.assertTrue(observed["tree_terminated"])
            self.assertTrue(observed["pipes_closed"])
            self.assertTrue(all(stream.closed for stream in (process.stdin, process.stdout, process.stderr)))
            self.assertTrue(observed["guard_closed"])
            self.assertFalse(guard.active)
            time.sleep(1.5)
            self.assertFalse(marker.exists(), "the descendant was ended with the tree")

    def test_stop_refuses_to_report_a_child_that_did_not_stop(self):
        """Cleanup that cannot be verified is a raised refusal carrying what it saw."""

        process = Mock(pid=4321, returncode=None, stdin=None, stdout=None, stderr=None)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired(cmd="stuck", timeout=0.01)
        events: list[str] = []

        class Guard:
            active = True

            def terminate(self):
                events.append("terminate")

            def close(self):
                events.append("close")

        with self.assertRaises(process_isolation.ProcessContainmentError) as raised:
            process_isolation.stop_contained_process(process, Guard(), grace_seconds=0.01)
        self.assertIn("still running after 0.01 seconds", str(raised.exception))
        self.assertIn("'child_stopped': False", str(raised.exception))
        self.assertIn("'tree_terminated': True", str(raised.exception))
        self.assertEqual(["terminate", "close"], events, "the tree is still ended and the guard still released")
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=0.01)

    def _ordered_process(self, events, *, returncode):
        """A safe fake child with two open streams that record their closing."""

        class Stream:
            def __init__(self, name):
                self.name, self.closed = name, False

            def close(self):
                events.append("close:" + self.name)
                self.closed = True

        process = Mock(pid=77, returncode=returncode, stdin=None,
                       stdout=Stream("stdout"), stderr=Stream("stderr"))
        process.poll.side_effect = lambda: process.returncode

        def kill():
            events.append("kill")
            process.returncode = -9

        def wait(timeout=None):
            events.append(("wait", timeout))
            return process.returncode

        process.kill.side_effect = kill
        process.wait.side_effect = wait
        return process

    def test_stop_releases_ownership_before_streams_after_a_normal_exit(self):
        """Ordering with safe fakes: tree, child, guard release, then streams.

        The child has already exited, so it is not killed; the tree is ended,
        the child reaped within the grace period, the guard released, and only
        then are the streams closed. This asserts the ownership-release order
        and the reported observations only.
        """
        events: list[object] = []
        process = self._ordered_process(events, returncode=0)

        class Guard:
            active = True

            def terminate(self):
                events.append("terminate")

            def close(self):
                events.append("guard-close")

        observed = process_isolation.stop_contained_process(process, Guard(), grace_seconds=0.5)
        self.assertEqual(["terminate", ("wait", 0.5), "guard-close", "close:stdout", "close:stderr"], events)
        self.assertEqual({"platform": os.name, "tree_terminated": True, "child_stopped": True,
                          "guard_closed": True, "pipes_closed": True, "returncode": 0}, observed)

    def test_stop_still_releases_the_guard_and_keeps_every_failure_when_termination_fails(self):
        """A failed termination does not skip the guard release or hide what was seen.

        The termination step reports a failure; the child is still killed and
        reaped, the guard is still released before any stream is touched, the
        streams are still closed, and the refusal carries the failure together
        with every observation. No hang or stream-wait mechanism is reproduced
        or claimed here - only the order and the retained evidence.
        """
        events: list[object] = []
        process = self._ordered_process(events, returncode=None)

        class Guard:
            active = True

            def terminate(self):
                events.append("terminate")
                raise process_isolation.ProcessContainmentError("Windows Job Object termination failed")

            def close(self):
                events.append("guard-close")

        with self.assertRaises(process_isolation.ProcessContainmentError) as raised:
            process_isolation.stop_contained_process(process, Guard(), grace_seconds=0.5)
        self.assertEqual(["terminate", "kill", ("wait", 0.5), "guard-close", "close:stdout", "close:stderr"], events)
        message = str(raised.exception)
        self.assertIn("Windows Job Object termination failed", message)
        for observation in ("'tree_terminated': False", "'child_stopped': True",
                            "'guard_closed': True", "'pipes_closed': True", "'returncode': -9"):
            self.assertIn(observation, message)

    def test_signal_contained_tree_terminates_an_active_guard_then_kills_the_child(self):
        process = Mock()
        guard = Mock()
        guard.active = True
        process_isolation.signal_contained_tree(process, guard)
        guard.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()

    def test_signal_contained_tree_uses_the_posix_process_group_when_idle(self):
        process = Mock(pid=4242)
        guard = Mock()
        guard.active = False
        with (
            patch.object(process_isolation.os, "name", "posix"),
            patch.object(process_isolation.os, "killpg") as killpg,
        ):
            process_isolation.signal_contained_tree(process, guard)
        killpg.assert_called_once_with(4242, getattr(process_isolation.signal, "SIGKILL", 9))
        process.kill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:17 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:30 UTC | END]]
