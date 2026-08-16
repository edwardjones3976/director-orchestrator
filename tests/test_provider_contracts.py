from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.models import Assignment, ProviderName, ProviderRun
from director_orchestrator.providers import ClaudeAdapter, CursorAdapter, GeminiAdapter, PerplexityAdapter


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class RecordingAsyncClient:
    calls: list[dict] = []
    responses: list[dict] = []

    def __init__(self, **_: object):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        return FakeResponse(self.responses.pop(0))

    async def get(self, url: str, *, headers: dict) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return FakeResponse(self.responses.pop(0))


class ProviderContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        RecordingAsyncClient.calls = []
        RecordingAsyncClient.responses = []

    async def test_cursor_usage_check_reads_models_instead_of_launching_an_agent(self):
        # A usage probe must never launch a paid cloud agent: POST /v1/agents
        # was observed hanging past its 60s timeout on every probe, and a
        # successful launch would burn an agent run per check. The credential
        # is proven by an authenticated read.
        settings = replace(Settings(), cursor_api_key="test-cursor")
        RecordingAsyncClient.responses = [
            {"items": [{"id": "default"}, {"id": "composer-2.5"}]}
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await CursorAdapter(settings).usage_check("probe")

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(request["method"], "GET")
        self.assertTrue(request["url"].endswith("/v1/models"))
        self.assertEqual(run.status, "FINISHED")
        self.assertIn("2 models", run.result)

    async def test_cursor_dispatch_denies_draft_pr_unless_assignment_explicitly_allows_it(self):
        settings = replace(Settings(), cursor_api_key="test-cursor")
        base = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Review the repository without changing it",
            acceptance=["Report evidence"],
            repository="example/demo",
            requires_approval=True,
        )
        response = {
            "run": {"id": "run-1", "status": "RUNNING"},
            "agent": {"id": "agent-1", "url": "https://cursor.example/agent-1"},
        }
        RecordingAsyncClient.responses = [response, response]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            await CursorAdapter(settings).dispatch(base, "context")
            await CursorAdapter(settings).dispatch(
                base.model_copy(update={"task": "Create only a draft PR", "allow_draft_pr": True}),
                "context",
            )

        self.assertFalse(RecordingAsyncClient.calls[0]["json"]["autoCreatePR"])
        self.assertTrue(RecordingAsyncClient.calls[1]["json"]["autoCreatePR"])

    async def test_claude_revision_fires_a_bounded_callback_run(self):
        settings = replace(
            Settings(),
            claude_routine_url="https://claude.example/routine",
            claude_routine_token="test-claude",
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Analyze",
            acceptance=["Evidence exists"],
            repository="example/demo",
            requires_approval=True,
        )
        prior = ProviderRun(
            provider=ProviderName.CLAUDE,
            run_id="claude-original",
            status="completed",
            result="Incomplete evidence",
        )
        context = '{"current_assignment":{"sequence":1,"owner_approval_recorded":true}}'
        RecordingAsyncClient.responses = [
            {
                "claude_code_session_id": "claude-revision",
                "claude_code_session_url": "https://claude.example/session/claude-revision",
            }
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await ClaudeAdapter(settings).revise(
                prior,
                "Add exact evidence",
                assignment=assignment,
                context=context,
            )

        request = RecordingAsyncClient.calls[0]
        payload = json.loads(request["json"]["text"])
        self.assertEqual(payload["revision"]["prior_run_id"], "claude-original")
        self.assertEqual(payload["revision"]["prompt"], "Add exact evidence")
        self.assertEqual(payload["assignment"]["sequence"], 1)
        self.assertEqual(payload["context"], context)
        self.assertIn("only Slack writer", payload["completion_requirement"])
        self.assertEqual(run.run_id, "claude-revision")
        self.assertEqual(run.status, "WAITING_CALLBACK")

    async def test_gemini_usage_check_uses_current_interactions_contract(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_api_revision="2026-05-20",
            gemini_agent="antigravity-preview-05-2026",
        )
        RecordingAsyncClient.responses = [
            {"id": "interaction-1", "status": "completed", "output_text": "STATUS=OK"}
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await GeminiAdapter(settings).usage_check("probe")

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(request["headers"]["Api-Revision"], "2026-05-20")
        self.assertEqual(request["json"]["model"], "antigravity-preview-05-2026")
        self.assertEqual(request["json"]["environment"], "remote")
        self.assertFalse(request["json"]["store"])
        self.assertNotIn("agent", request["json"])
        self.assertEqual(run.status, "completed")

    async def test_perplexity_usage_check_uses_synchronous_agent_contract(self):
        settings = replace(
            Settings(),
            perplexity_api_key="test-perplexity",
            perplexity_preset="fast-search",
        )
        RecordingAsyncClient.responses = [
            {
                "id": "response-1",
                "status": "completed",
                "output": [{"content": [{"text": "STATUS=OK"}]}],
                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            }
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await PerplexityAdapter(settings).usage_check("probe")

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(request["url"], "https://api.perplexity.ai/v1/agent")
        self.assertEqual(request["json"]["preset"], "fast-search")
        self.assertEqual(request["json"]["tools"], [])
        self.assertNotIn("background", request["json"])
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.result, "STATUS=OK")


if __name__ == "__main__":
    unittest.main()
