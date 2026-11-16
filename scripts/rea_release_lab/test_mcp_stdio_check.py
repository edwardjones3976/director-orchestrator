"""Regression tests for mcp_stdio_check cleanup error handling and MCP withholding."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch


def _ensure_mcp_importable():
    if importlib.util.find_spec("mcp") is not None:
        return
    mcp_mod = types.ModuleType("mcp")
    client_mod = types.ModuleType("mcp.client")
    stdio_mod = types.ModuleType("mcp.client.stdio")

    class StubClientSession:
        def __init__(self, read, write):
            pass

    class StubStdioServerParameters:
        def __init__(self, command, args):
            self.command = command
            self.args = args

    @asynccontextmanager
    async def stub_stdio_client(params, errlog=None):
        yield (None, None)

    mcp_mod.ClientSession = StubClientSession
    mcp_mod.StdioServerParameters = StubStdioServerParameters
    stdio_mod.stdio_client = stub_stdio_client
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.client"] = client_mod
    sys.modules["mcp.client.stdio"] = stdio_mod


_ensure_mcp_importable()
import mcp_stdio_check  # noqa: E402


class _FakeReply:
    def __init__(self, payload):
        self.isError = False
        self.structuredContent = payload
        self.content = []

    def model_dump_json(self, indent=2):
        return json.dumps(self.structuredContent, indent=indent)


class _FakeInitializeResult:
    def model_dump_json(self, indent=2):
        return json.dumps({"protocolVersion": "stub"}, indent=indent)


def _make_fake_session_factory(case_overrides=None, raise_on_case=None, raise_error=None):
    overrides = case_overrides or {}

    class FakeSession:
        def __init__(self, read, write):
            self.last_case = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            return _FakeInitializeResult()

        async def call_tool(self, name, arguments):
            if name == "inspect_web_network_capture":
                capture_path = arguments["capture_path"]
                case_name = capture_path.split("/")[2]
                self.last_case = case_name
                digest = (case_name.encode("utf-8").hex() * 8)[:64]
                return _FakeReply({
                    "ok": True,
                    "data": {
                        "evidence_id": "ev-" + digest,
                        "operation": "inspect_web_network_capture",
                    },
                })
            if name == "inspect_analysis_view":
                case_name = self.last_case
                if raise_on_case == case_name:
                    raise raise_error if raise_error is not None else RuntimeError("primary-tool-failure:" + case_name)
                digest = (case_name.encode("utf-8").hex() * 8)[:64]
                served = case_name not in ("synthetic-overlap", "synthetic-unusable-duration")
                limitations = (
                    [mcp_stdio_check.UNUSABLE_DURATION_LIMITATION]
                    if case_name == "synthetic-unusable-duration"
                    else []
                )
                write_clock = None if case_name == "synthetic-unusable-duration" else "2026-11-15T00:00:01.000Z"
                normalized = {
                    "kind": "environment-state",
                    "parent_evidence_id": "ev-" + digest,
                    "parent_operation": "inspect_web_network_capture",
                    "parent_digest": digest,
                    "timeline": "recorded-start-time",
                    "write_clock": write_clock,
                    "limitations": limitations,
                    "writes": [
                        {
                            "tool": "put_folders_by_id",
                            "served": served,
                            "reason": None if served else "unconfirmed",
                        }
                    ],
                }
                if case_name in overrides:
                    normalized.update(overrides[case_name])
                return _FakeReply({
                    "ok": True,
                    "data": {
                        "evidence_id": "ev-" + ("b" * 64),
                        "normalized_result": normalized,
                    },
                })
            raise AssertionError("Unexpected MCP tool: " + name)

    return FakeSession


@asynccontextmanager
async def _fake_stdio_client(params, errlog=None):
    yield (object(), object())


class McpStdioCheckTest(unittest.IsolatedAsyncioTestCase):
    async def test_primary_error_preserved_and_cleanup_error_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            ps_calls = 0
            stopped = []

            def fake_run(command, capture_output=True, text=True, check=True, timeout=20):
                nonlocal ps_calls
                if command[:2] == ["docker", "ps"]:
                    ps_calls += 1
                    stdout = "cid-one\ncid-two\n" if ps_calls == 1 else "cid-one\n"
                    return subprocess.CompletedProcess(command, 0, stdout, "")
                if command[:2] == ["docker", "stop"]:
                    cid = command[-1]
                    stopped.append(cid)
                    if cid == "cid-one":
                        raise subprocess.CalledProcessError(1, command, stderr="stop-refused-cid-one")
                    return subprocess.CompletedProcess(command, 0, "", "")
                raise AssertionError("Unexpected command: " + str(command))

            with patch.object(mcp_stdio_check, "stdio_client", _fake_stdio_client), \
                 patch.object(mcp_stdio_check, "ClientSession", _make_fake_session_factory(raise_on_case="synthetic-overlap")), \
                 patch.object(mcp_stdio_check.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(RuntimeError) as ctx:
                    await mcp_stdio_check.check("img", output)

            self.assertIn("primary-tool-failure:synthetic-overlap", str(ctx.exception))
            notes = "\n".join(getattr(ctx.exception, "__notes__", []))
            self.assertIn("stop-refused-cid-one", notes)
            self.assertIn("did not stop", notes)
            self.assertEqual(stopped, ["cid-one", "cid-two"])

            cleanup = json.loads((output / "mcp" / "cleanup.json").read_text(encoding="utf-8"))
            self.assertEqual(cleanup["status"], "FAIL")
            self.assertIn("primary-tool-failure:synthetic-overlap", cleanup["primary_error"])
            self.assertTrue(any("stop-refused-cid-one" in err for err in cleanup["errors"]))

    async def test_cleanup_only_failure_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            ps_calls = 0

            def fake_run(command, capture_output=True, text=True, check=True, timeout=20):
                nonlocal ps_calls
                if command[:2] == ["docker", "ps"]:
                    ps_calls += 1
                    return subprocess.CompletedProcess(command, 0, "cid-left\n" if ps_calls == 1 else "", "")
                if command[:2] == ["docker", "stop"]:
                    raise subprocess.CalledProcessError(1, command, stderr="stop-failed")
                raise AssertionError("Unexpected command: " + str(command))

            with patch.object(mcp_stdio_check, "stdio_client", _fake_stdio_client), \
                 patch.object(mcp_stdio_check, "ClientSession", _make_fake_session_factory()), \
                 patch.object(mcp_stdio_check.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(RuntimeError) as ctx:
                    await mcp_stdio_check.check("img", output)

            self.assertIn("stop-failed", str(ctx.exception))

    async def test_cleanup_json_write_failure_preserves_primary_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            primary_exc = RuntimeError("primary-tool-failure:synthetic-overlap")
            original_write_text = Path.write_text

            def fake_run(command, capture_output=True, text=True, check=True, timeout=20):
                if command[:2] == ["docker", "ps"]:
                    raise subprocess.CalledProcessError(1, command, stderr="ps-denied-stderr")
                raise AssertionError("Unexpected command: " + str(command))

            def fail_cleanup_write(path_self, *args, **kwargs):
                if path_self.name == "cleanup.json":
                    raise OSError("disk-full-cleanup-report")
                return original_write_text(path_self, *args, **kwargs)

            with patch.object(mcp_stdio_check, "stdio_client", _fake_stdio_client), \
                 patch.object(mcp_stdio_check, "ClientSession", _make_fake_session_factory(raise_on_case="synthetic-overlap", raise_error=primary_exc)), \
                 patch.object(mcp_stdio_check.subprocess, "run", side_effect=fake_run), \
                 patch.object(Path, "write_text", fail_cleanup_write):
                with self.assertRaises(RuntimeError) as ctx:
                    await mcp_stdio_check.check("img", output)

            self.assertIs(ctx.exception, primary_exc)
            notes = "\n".join(getattr(ctx.exception, "__notes__", []))
            self.assertIn("ps-denied-stderr", notes)
            self.assertIn("cleanup.json write failed", notes)
            self.assertIn("disk-full-cleanup-report", notes)
            self.assertFalse((output / "mcp" / "cleanup.json").exists())

    async def test_cleanup_json_write_failure_without_primary_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            original_write_text = Path.write_text

            def fake_run(command, capture_output=True, text=True, check=True, timeout=20):
                return subprocess.CompletedProcess(command, 0, "", "")

            def fail_cleanup_write(path_self, *args, **kwargs):
                if path_self.name == "cleanup.json":
                    raise OSError("disk-full-cleanup-report")
                return original_write_text(path_self, *args, **kwargs)

            with patch.object(mcp_stdio_check, "stdio_client", _fake_stdio_client), \
                 patch.object(mcp_stdio_check, "ClientSession", _make_fake_session_factory()), \
                 patch.object(mcp_stdio_check.subprocess, "run", side_effect=fake_run), \
                 patch.object(Path, "write_text", fail_cleanup_write):
                with self.assertRaises(RuntimeError) as ctx:
                    await mcp_stdio_check.check("img", output)

            self.assertIn("cleanup.json write failed", str(ctx.exception))
            self.assertIn("disk-full-cleanup-report", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, OSError)

    async def test_unusable_duration_withholding_diagnostic_and_correlation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)

            def fake_run(command, capture_output=True, text=True, check=True, timeout=20):
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(mcp_stdio_check, "stdio_client", _fake_stdio_client), \
                 patch.object(mcp_stdio_check, "ClientSession", _make_fake_session_factory()), \
                 patch.object(mcp_stdio_check.subprocess, "run", side_effect=fake_run):
                await mcp_stdio_check.check("img", output)

            summary = json.loads((output / "mcp" / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["passed"])
            by_case = {item["case"]: item for item in summary["results"]}
            self.assertFalse(by_case["synthetic-unusable-duration"]["update_served"])
            self.assertEqual(by_case["synthetic-unusable-duration"]["update_reason"], "unconfirmed")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)

            def fake_run(command, capture_output=True, text=True, check=True, timeout=20):
                return subprocess.CompletedProcess(command, 0, "", "")

            bad_overrides = {"synthetic-unusable-duration": {"limitations": []}}
            with patch.object(mcp_stdio_check, "stdio_client", _fake_stdio_client), \
                 patch.object(mcp_stdio_check, "ClientSession", _make_fake_session_factory(case_overrides=bad_overrides)), \
                 patch.object(mcp_stdio_check.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(AssertionError):
                    await mcp_stdio_check.check("img", output)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)

            def fake_run(command, capture_output=True, text=True, check=True, timeout=20):
                return subprocess.CompletedProcess(command, 0, "", "")

            bad_corr = {"live-1": {"parent_operation": "wrong_op"}}
            with patch.object(mcp_stdio_check, "stdio_client", _fake_stdio_client), \
                 patch.object(mcp_stdio_check, "ClientSession", _make_fake_session_factory(case_overrides=bad_corr)), \
                 patch.object(mcp_stdio_check.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(AssertionError):
                    await mcp_stdio_check.check("img", output)


if __name__ == "__main__":
    unittest.main()
