# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | BEGIN]]
import json
import sys
import runpy
import unittest
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.zai_trial import build_zai_trial_adapter, load_key, ENDPOINT

ROOT = Path(__file__).resolve().parents[1]


def assert_isolated_command(test, command):
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        test.assertIn(flag, command)
    for flag, value in (("--tools", ""), ("--setting-sources", ""),
                        ("--max-turns", "1"), ("--mcp-config", '{"mcpServers":{}}')):
        test.assertIn(flag, command)
        test.assertEqual(command[command.index(flag) + 1], value)


def fixture():
    work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text())
    work["allowed_tools"] = []
    work["limits"].update(budget_usd=0, timeout_seconds=60, retry_limit=1)
    context = ProviderAdapterContext(
        task_id=work["task_id"], assignment_id=work["assignment_id"],
        idempotency_key=work["idempotency_key"], work_packet_digest=document_digest(work),
        writer_token_digest="sha256:" + "b" * 64, timeout_seconds=60,
        budget_usd=0, max_hops=1, max_revisions=1, allowed_tools=(), allowed_external_effects=())
    return work, context


class ZaiTrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / ".env"
        self.env.write_text("OTHER_KEY=never-forward\nZAI_API_KEY=test.secret\n")
        self.kw = dict(executable=Path(sys.executable), env_file=self.env,
                       journal=self.root / "journal", model="glm-5.3-flash", lite_verified=True)
        self.work, self.context = fixture()

    def native(self):
        return (0, json.dumps({"session_id": "trial-1", "subtype": "success",
                              "result": '{"outcome":"def add(a, b): return a + b", "status":"SUCCEEDED"}'}).encode())

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_bound_identity_and_isolated_subscription_environment(self, run):
        run.return_value = self.native()
        result = build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        self.assertEqual(result["provider"], "zai")
        self.assertIsNone(result["usage"])
        sent = run.call_args.kwargs
        assert_isolated_command(self, run.call_args.args[0])
        self.assertEqual(sent["env"]["ANTHROPIC_BASE_URL"], ENDPOINT)
        self.assertNotIn("OTHER_KEY", sent["env"])
        self.assertNotIn("ANTHROPIC_API_KEY", sent["env"])
        self.assertIn(b"Welcome aboard, Z.ai", sent["stdin"])
        self.assertNotIn(b"test.secret", sent["stdin"])
        with self.assertRaises(FileExistsError):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        self.assertEqual(run.call_count, 1)

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_isolation_assertions_detect_removed_or_weakened_flags(self, run):
        run.return_value = self.native()
        build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        command = run.call_args.args[0]
        for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence",
                     "--tools", "--setting-sources", "--max-turns", "--mcp-config"):
            removed = list(command)
            removed.remove(flag)
            with self.assertRaises(AssertionError):
                assert_isolated_command(self, removed)
        for flag, value in (("--tools", "default"), ("--max-turns", "2"),
                            ("--setting-sources", "user"), ("--mcp-config", '{"mcpServers":{"unsafe":{}}}')):
            weakened = list(command)
            weakened[weakened.index(flag) + 1] = value
            with self.assertRaises(AssertionError):
                assert_isolated_command(self, weakened)

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_both_receipts_redact_plain_and_escaped_nested_credentials(self, run):
        key = 'test."secret\\tail'
        self.env.write_text("ZAI_API_KEY=" + key + "\n")
        text = "plain=" + key + "; embedded=" + json.dumps({"key": key})
        run.return_value = (0, json.dumps({"session_id": "trial-1", "subtype": "success",
            "result": json.dumps({"outcome": text, "checks": [{"nested": [text]}]})}).encode())
        with self.assertRaises(ValueError):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        diagnostic = json.loads(next((self.root / "journal").glob("*.result.json")).read_text())
        writer = runpy.run_path(str(ROOT / "scripts/zai-lite-smoke.py"))["write_report"]
        final = writer({"status": "PASS", "result": {"outcome": text, "nested": [text]}},
                       self.root / "result.json", key)
        for receipt in (diagnostic, final):
            serialized = json.dumps(receipt)
            self.assertNotIn("secret", serialized)
            self.assertIn("[REDACTED]", serialized)

    def test_missing_or_duplicate_credentials(self):
        for text in ("", "ZAI_API_KEY=\n", "ZAI_API_KEY=a\nZAI_API_KEY=b\n"):
            self.env.write_text(text)
            with self.assertRaises(ValueError):
                load_key(self.env)

    def test_entitlement_required(self):
        with self.assertRaises(ValueError):
            build_zai_trial_adapter(**dict(self.kw, lite_verified=False))

    def test_smoke_refuses_failed_or_blocked_results(self):
        check = runpy.run_path(str(ROOT / "scripts/zai-lite-smoke.py"))["require_success"]
        good = dict(status="SUCCEEDED", blockers=[], requested_decisions=[], revision_recommended=False)
        check(good)
        for change in (dict(status="FAILED"), dict(status="OWNER_DECISION_REQUIRED"),
                       dict(blockers=["blocked"]), dict(requested_decisions=["approve"]),
                       dict(revision_recommended=True)):
            with self.assertRaises(ValueError):
                check(dict(good, **change))

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_invalid_packet_never_dispatches(self, run):
        self.work.pop("objective")
        with self.assertRaises(ValueError):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        run.assert_not_called()

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_tools_budget_and_timeout_fail_closed(self, run):
        for context in (replace(self.context, budget_usd=1),
                        replace(self.context, allowed_tools=("Bash",)),
                        replace(self.context, timeout_seconds=61)):
            with self.assertRaises(ValueError):
                build_zai_trial_adapter(**self.kw).dispatch(self.work, context=context)
        run.assert_not_called()

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_malformed_result_retains_attempt(self, run):
        run.return_value = (0, b"not json")
        adapter = build_zai_trial_adapter(**self.kw)
        with self.assertRaises(ValueError):
            adapter.dispatch(self.work, context=self.context)
        with self.assertRaises(FileExistsError):
            adapter.dispatch(self.work, context=self.context)
        self.assertEqual(run.call_count, 1)

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_timeout_or_cancellation_retains_attempt(self, run):
        run.side_effect = TimeoutError("ambiguous")
        adapter = build_zai_trial_adapter(**self.kw)
        with self.assertRaises(TimeoutError):
            adapter.dispatch(self.work, context=self.context)
        with self.assertRaises(FileExistsError):
            adapter.dispatch(self.work, context=self.context)

    @patch("director_orchestrator.zai_trial.datetime")
    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_expired_trial_never_dispatches(self, run, clock):
        clock.now.return_value = datetime(2026, 9, 29, 7, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        run.assert_not_called()

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_invalid_result_retains_sanitized_diagnostic(self, run):
        run.return_value = (0, json.dumps({"session_id": "trial-1", "subtype": "success",
            "result": json.dumps({"outcome": "test.secret", "checks": [42]})}).encode())
        with self.assertRaises(ValueError):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        receipt = next((self.root / "journal").glob("*.result.json")).read_text()
        self.assertNotIn("test.secret", receipt)
        self.assertIn("[REDACTED]", receipt)

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_native_error_does_not_become_success(self, run):
        run.return_value = (0, b'{"subtype":"error_max_turns","session_id":"x","result":"text"}')
        with self.assertRaises(RuntimeError):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:18 UTC | END]]

