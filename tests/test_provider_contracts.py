from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.providers import GeminiAdapter, PerplexityAdapter


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
        self.calls.append({"url": url, "headers": headers, "json": json})
        return FakeResponse(self.responses.pop(0))


class ProviderContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        RecordingAsyncClient.calls = []
        RecordingAsyncClient.responses = []

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
