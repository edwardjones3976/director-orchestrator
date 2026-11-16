"""Regression tests for run_all Docker ownership and timeout/cancellation cleanup."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_all


class RunAllCleanupTest(unittest.TestCase):
    def test_docker_run_command_places_owned_name_and_labels_before_image(self):
        command, cname, labels = run_all.docker_run_command(
            "img:1", Path("/src"), Path("/out"), "unit-tests", "abcd1234", ["python3", "-V"],
        )
        self.assertEqual(cname, "doa-rea-lab-abcd1234-unit-tests")
        self.assertEqual(labels, {"doa.rea-lab.run": "abcd1234", "doa.rea-lab.step": "unit-tests"})
        img_index = command.index("img:1")
        self.assertLess(command.index("--name"), img_index)
        self.assertLess(command.index("--label"), img_index)
        self.assertEqual(command[img_index + 1:], ["python3", "-V"])

    def test_execute_mocked_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            receipts = []
            calls = []

            def fake_run(command, stdout=None, stderr=None, cwd=None, timeout=None, capture_output=False, text=False):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0)

            with patch.object(run_all.subprocess, "run", side_effect=fake_run):
                run_all.execute(
                    output, receipts, ["docker", "run", "img"], "step1",
                    container_name="doa-rea-lab-1-step1",
                    container_labels={"doa.rea-lab.run": "1", "doa.rea-lab.step": "step1"},
                )

            self.assertEqual(len(calls), 1)
            saved = json.loads((output / "run-receipts.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["status"], "passed")
            self.assertEqual(saved[0]["cleanup_status"], "not-needed")

    def test_execute_timeout_stops_only_verified_owned_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            receipts = []
            calls = []
            cname = "doa-rea-lab-1-step1"
            labels = {"doa.rea-lab.run": "1", "doa.rea-lab.step": "step1"}

            def fake_run(command, **kwargs):
                calls.append((command, kwargs.get("timeout")))
                if command[:2] == ["docker", "run"]:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                if command[:2] == ["docker", "inspect"]:
                    payload = [{"Id": "cid-owned", "Name": "/" + cname, "Config": {"Labels": labels}}]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if command[:2] == ["docker", "stop"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                raise AssertionError("Unexpected command: " + str(command))

            with patch.object(run_all.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_all.execute(
                        output, receipts, ["docker", "run", "img"], "step1",
                        timeout=45, container_name=cname, container_labels=labels, cleanup_timeout=15,
                    )

            self.assertEqual(calls[1][0], ["docker", "inspect", cname])
            self.assertEqual(calls[1][1], 15)
            self.assertEqual(calls[2][0], ["docker", "stop", "--time", "5", "cid-owned"])
            self.assertEqual(calls[2][1], 15)
            saved = json.loads((output / "run-receipts.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["status"], "timeout")
            self.assertEqual(saved[0]["cleanup_status"], "stopped")

    def test_execute_timeout_handles_container_already_removed_by_rm(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            receipts = []
            calls = []
            cname = "doa-rea-lab-1-step1"
            labels = {"doa.rea-lab.run": "1", "doa.rea-lab.step": "step1"}

            def fake_run(command, **kwargs):
                calls.append(command)
                if command[:2] == ["docker", "run"]:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                if command[:2] == ["docker", "inspect"]:
                    return subprocess.CompletedProcess(command, 1, "", "Error: No such object: " + cname)
                raise AssertionError("Unexpected command: " + str(command))

            with patch.object(run_all.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_all.execute(
                        output, receipts, ["docker", "run", "img"], "step1",
                        container_name=cname, container_labels=labels,
                    )

            self.assertEqual(len(calls), 2)
            saved = json.loads((output / "run-receipts.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["cleanup_status"], "already-removed")

    def test_execute_cancellation_stops_owned_container_and_preserves_interrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            receipts = []
            cname = "doa-rea-lab-1-step1"
            labels = {"doa.rea-lab.run": "1", "doa.rea-lab.step": "step1"}

            def fake_run(command, **kwargs):
                if command[:2] == ["docker", "run"]:
                    raise KeyboardInterrupt()
                if command[:2] == ["docker", "inspect"]:
                    payload = [{"Id": "cid-owned", "Name": "/" + cname, "Config": {"Labels": labels}}]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if command[:2] == ["docker", "stop"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                raise AssertionError("Unexpected command: " + str(command))

            with patch.object(run_all.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(KeyboardInterrupt):
                    run_all.execute(
                        output, receipts, ["docker", "run", "img"], "step1",
                        container_name=cname, container_labels=labels,
                    )

            saved = json.loads((output / "run-receipts.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["status"], "cancelled")
            self.assertEqual(saved[0]["cleanup_status"], "stopped")

    def test_execute_foreign_owner_refusal_never_stops_and_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            receipts = []
            calls = []
            cname = "doa-rea-lab-1-step1"
            labels = {"doa.rea-lab.run": "1", "doa.rea-lab.step": "step1"}

            def fake_run(command, **kwargs):
                calls.append(command)
                if command[:2] == ["docker", "run"]:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                if command[:2] == ["docker", "inspect"]:
                    foreign = {"doa.rea-lab.run": "foreign", "doa.rea-lab.step": "step1"}
                    payload = [{"Id": "cid-foreign", "Name": "/" + cname, "Config": {"Labels": foreign}}]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                raise AssertionError("docker stop must never run on foreign container: " + str(command))

            with patch.object(run_all.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(RuntimeError) as ctx:
                    run_all.execute(
                        output, receipts, ["docker", "run", "img"], "step1",
                        container_name=cname, container_labels=labels,
                    )

            self.assertIn("refused-foreign-owner", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, subprocess.TimeoutExpired)
            self.assertFalse(any(cmd[:2] == ["docker", "stop"] for cmd in calls))
            saved = json.loads((output / "run-receipts.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["cleanup_status"], "refused-foreign-owner")

    def test_execute_stop_refusal_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            receipts = []
            cname = "doa-rea-lab-1-step1"
            labels = {"doa.rea-lab.run": "1", "doa.rea-lab.step": "step1"}

            def fake_run(command, **kwargs):
                if command[:2] == ["docker", "run"]:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                if command[:2] == ["docker", "inspect"]:
                    payload = [{"Id": "cid-owned", "Name": "/" + cname, "Config": {"Labels": labels}}]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if command[:2] == ["docker", "stop"]:
                    return subprocess.CompletedProcess(command, 1, "", "permission denied")
                raise AssertionError("Unexpected command: " + str(command))

            with patch.object(run_all.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(RuntimeError) as ctx:
                    run_all.execute(
                        output, receipts, ["docker", "run", "img"], "step1",
                        container_name=cname, container_labels=labels,
                    )

            self.assertIn("stop-failed", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, subprocess.TimeoutExpired)
            saved = json.loads((output / "run-receipts.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["cleanup_status"], "stop-failed")


if __name__ == "__main__":
    unittest.main()
