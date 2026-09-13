# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 06:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:43 UTC | BEGIN]]
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
from director_orchestrator.native_acceptance_capture import run_bounded, child_environment
from director_orchestrator.native_acceptance_issuer import IssuerError
from director_orchestrator.zai_trial import failure_stdout

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

    def test_failure_stdout_redacts_full_escaped_and_partial_key(self):
        key = 'test."secret\\tail'
        for form in (key.encode(), json.dumps(key)[1:-1].encode(),
                     json.dumps(json.dumps(key)[1:-1])[1:-1].encode()):
            for raw in (b"partial " + form, b"partial " + form[:-2]):
                safe = failure_stdout(raw, key)
                self.assertNotIn("secret", safe)
                self.assertIn("[REDACTED]", safe)
        self.assertEqual(failure_stdout(b"", key), "")

    def test_offline_failure_capture_preserves_bounds_and_default_behavior(self):
        cases = [('', 0.2, 100),
                 ('print("partial", flush=True);', 1, 100),
                 ('print("x"*10000, flush=True);', 1, 20)]
        for prefix, timeout, limit in cases:
            with self.subTest(prefix=prefix):
                captured = []
                with self.assertRaises(IssuerError):
                    run_bounded([sys.executable, "-c", prefix + "import time; time.sleep(5)"],
                        cwd=self.root, env=child_environment(), timeout=timeout, limit=limit,
                        failure_capture=lambda raw, truncated: captured.append((raw, truncated)))
                self.assertEqual(len(captured), 1)
                self.assertLessEqual(len(captured[0][0]), limit)
                if prefix.startswith('print("partial'):
                    self.assertEqual(captured[0], (b"partial\r\n" if sys.platform == "win32" else b"partial\n", False))
                if prefix.startswith('print("x'):
                    self.assertTrue(captured[0][1])
        self.assertEqual(run_bounded([sys.executable, "-c", "import sys; sys.stdout.write('bad'); sys.exit(7)"],
                         cwd=self.root, env=child_environment()), (7, b"bad"))
        captured = []
        self.assertEqual(run_bounded([sys.executable, "-c", "import sys; sys.stdout.write('bad'); sys.exit(7)"],
                         cwd=self.root, env=child_environment(),
                         failure_capture=lambda raw, truncated: captured.append((raw, truncated))), (7, b"bad"))
        self.assertEqual(captured, [(b"bad", False)])
        with self.assertRaises(IssuerError):
            run_bounded([sys.executable, "-c", "print('x'*100)"],
                        cwd=self.root, env=child_environment(), limit=10)

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_failed_trial_keeps_sanitized_exclusive_evidence_and_never_retries(self, run):
        def fail(*args, **kwargs):
            kwargs["failure_capture"](b"partial test.secret", True)
            raise IssuerError("native attempt ambiguous: timeout or capture limit")
        run.side_effect = fail
        adapter = build_zai_trial_adapter(**self.kw)
        with self.assertRaisesRegex(IssuerError, "native attempt ambiguous"):
            adapter.dispatch(self.work, context=self.context)
        path = next((self.root / "journal").glob("*.failure.json"))
        raw = path.read_bytes()
        receipt = json.loads(raw)
        self.assertEqual(receipt["stdout"], "partial [REDACTED]")
        self.assertEqual(receipt["status"], "FAILED_OR_AMBIGUOUS")
        self.assertIsNone(receipt["actual_cost_usd"])
        self.assertTrue(receipt["truncated"])
        with self.assertRaises(FileExistsError):
            adapter.dispatch(self.work, context=self.context)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(list((self.root / "journal").glob("*.result.json")))

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_nonzero_trial_keeps_empty_failure_capture(self, run):
        def fail(*args, **kwargs):
            kwargs["failure_capture"](b"", False)
            return 2, b""
        run.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "Z.ai trial failed"):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        receipt = json.loads(next((self.root / "journal").glob("*.failure.json")).read_text())
        self.assertEqual(receipt["stdout"], "")
        self.assertFalse(receipt["truncated"])

    def test_evidence_write_error_does_not_replace_timeout(self):
        def cannot_save(*args):
            raise OSError("private-path-and-key")
        with self.assertRaisesRegex(IssuerError, "native attempt ambiguous") as caught:
            run_bounded([sys.executable, "-c", "import time; time.sleep(5)"],
                        cwd=self.root, env=child_environment(), timeout=.2, failure_capture=cannot_save)
        self.assertNotIn("private-path", str(caught.exception))
        self.assertEqual(caught.exception.__notes__, ["Failure evidence could not be retained."])

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_bound_identity_and_isolated_subscription_environment(self, run):
        run.return_value = self.native()
        result = build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        self.assertEqual(result["provider"], "zai")
        self.assertIsNone(result["usage"])
        sent = run.call_args.kwargs
        assert_isolated_command(self, run.call_args.args[0])
        self.assertEqual(sent["env"]["ANTHROPIC_BASE_URL"], ENDPOINT)
        self.assertEqual(sent["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "2000")
        self.assertIn("--system-prompt", run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--effort") + 1], "low")
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
            "result": json.dumps({"outcome": text, "status": "SUCCEEDED", "checks": [{"nested": [text]}]})}).encode())
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
            "result": json.dumps({"outcome": "test.secret", "status": "SUCCEEDED", "checks": [42]})}).encode())
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

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_trial_response_contract_follows_input_and_rejects_invented_artifact(self, run):
        native = json.loads(self.native()[1])
        payload = json.loads(native["result"])
        payload["artifacts"] = [{"artifact": "corrected_assess.py", "sha256": "tbd-by-harness"}]
        native["result"] = json.dumps(payload)
        run.return_value = (0, json.dumps(native).encode())
        with self.assertRaises(ValueError):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        prompt = run.call_args.kwargs["stdin"].decode()
        self.assertGreater(prompt.index("TRIAL RESPONSE CONTRACT:"), prompt.index(self.work["objective"]))
        self.assertIn("These are the only two response fields", prompt)
        diagnostic = json.loads(next((self.root / "journal").glob("*.native.json")).read_text())
        self.assertEqual(json.loads(diagnostic["result"])["artifacts"], payload["artifacts"])
        self.assertEqual(run.call_count, 1)


    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_single_json_fence_preserves_failed_status(self, run):
        native = json.loads(self.native()[1])
        native["result"] = '```json\n{"outcome":"Unable to complete", "status":"FAILED"}\n```'
        run.return_value = (0, json.dumps(native).encode())
        result = build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        self.assertEqual(result["status"], "FAILED")

    @patch("director_orchestrator.zai_trial.run_bounded")
    def test_prose_and_missing_status_do_not_become_success(self, run):
        for index, payload in enumerate(('```json\n{"outcome":"x","status":"SUCCEEDED"}\n``` trailing',
                                          '{"outcome":"no status"}', 'plain code')):
            native = json.loads(self.native()[1])
            native["result"] = payload
            run.return_value = (0, json.dumps(native).encode())
            with self.assertRaises(ValueError):
                build_zai_trial_adapter(**dict(self.kw, journal=self.root / str(index))).dispatch(self.work, context=self.context)
            self.assertTrue(list((self.root / str(index)).glob('*.native.json')))

if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:18 UTC | END]]


# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:43 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:31 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 06:40 UTC | END]]
