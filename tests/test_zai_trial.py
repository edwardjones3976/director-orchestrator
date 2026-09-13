# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | BEGIN]]
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.zai_trial import build_zai_trial_adapter, load_key, ENDPOINT

ROOT = Path(__file__).resolve().parents[1]


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
        self.assertEqual(sent["env"]["ANTHROPIC_BASE_URL"], ENDPOINT)
        self.assertNotIn("OTHER_KEY", sent["env"])
        self.assertNotIn("ANTHROPIC_API_KEY", sent["env"])
        self.assertIn(b"Welcome aboard, Z.ai", sent["stdin"])
        self.assertNotIn(b"test.secret", sent["stdin"])
        with self.assertRaises(FileExistsError):
            build_zai_trial_adapter(**self.kw).dispatch(self.work, context=self.context)
        self.assertEqual(run.call_count, 1)

    def test_missing_or_duplicate_credentials(self):
        for text in ("", "ZAI_API_KEY=\n", "ZAI_API_KEY=a\nZAI_API_KEY=b\n"):
            self.env.write_text(text)
            with self.assertRaises(ValueError):
                load_key(self.env)

    def test_entitlement_required(self):
        with self.assertRaises(ValueError):
            build_zai_trial_adapter(**dict(self.kw, lite_verified=False))

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


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | END]]

