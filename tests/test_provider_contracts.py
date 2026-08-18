from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx

from director_orchestrator.config import Settings
from director_orchestrator.models import Assignment, ProviderName, ProviderRun
from director_orchestrator.providers import ClaudeAdapter, CursorAdapter, GeminiAdapter, PerplexityAdapter


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, headers: dict | None = None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://cursor.example")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "test response failed", request=request, response=response
            )

    def json(self) -> dict:
        return self.payload


class RecordingAsyncClient:
    calls: list[dict] = []
    responses: list[dict | FakeResponse] = []

    def __init__(self, **_: object):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        response = self.responses.pop(0)
        return response if isinstance(response, FakeResponse) else FakeResponse(response)

    async def get(self, url: str, *, headers: dict) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        response = self.responses.pop(0)
        return response if isinstance(response, FakeResponse) else FakeResponse(response)


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
        self.assertEqual(
            RecordingAsyncClient.calls[0]["json"]["repos"][0]["url"],
            "https://github.com/example/demo",
        )

    async def test_cursor_dispatch_retries_only_explicit_rate_limits(self):
        settings = replace(
            Settings(),
            cursor_api_key="test-cursor",
            cursor_dispatch_max_attempts=3,
            cursor_dispatch_max_retry_delay_seconds=60,
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Review exact SHA",
            acceptance=["Return evidence"],
            repository="example/demo",
        )
        rate_limit = {
            "code": "resource_exhausted",
            "details": [{
                "debug": {
                    "details": {
                        "isRetryable": True,
                        "additionalInfo": {"retryAfter": "60"},
                    }
                }
            }],
        }
        RecordingAsyncClient.responses = [
            FakeResponse(rate_limit, status_code=429),
            {"run": {"id": "run-2", "status": "RUNNING"}, "agent": {"id": "agent-2"}},
        ]

        with (
            patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient),
            patch("director_orchestrator.providers.asyncio.sleep") as sleep,
        ):
            run = await CursorAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(run.run_id, "run-2")
        self.assertEqual(len(RecordingAsyncClient.calls), 2)
        sleep.assert_awaited_once_with(60.0)

    async def test_cursor_dispatch_does_not_retry_ambiguous_server_failure(self):
        settings = replace(Settings(), cursor_api_key="test-cursor")
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Review exact SHA",
            acceptance=["Return evidence"],
            repository="example/demo",
        )
        RecordingAsyncClient.responses = [FakeResponse({}, status_code=503)]

        with (
            patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient),
            patch("director_orchestrator.providers.asyncio.sleep") as sleep,
        ):
            with self.assertRaises(httpx.HTTPStatusError):
                await CursorAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(len(RecordingAsyncClient.calls), 1)
        sleep.assert_not_awaited()

    async def test_cursor_dispatch_retries_deleted_working_branch_on_allowlisted_ref(self):
        settings = replace(
            Settings(),
            cursor_api_key="test-cursor",
            allowed_starting_refs=("main",),
            cursor_dispatch_max_attempts=1,
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Continue Edge Oracle research",
            acceptance=["Return evidence"],
            repository="edwardjones3976/edge_engine",
            starting_ref="cursor/director-task-1-f90c",
        )
        missing = FakeResponse(
            {
                "message": (
                    'GitHub branch not found: The starting branch or commit '
                    '"cursor/director-task-1-f90c" does not exist in '
                    "edwardjones3976/edge_engine. It may have been deleted after "
                    "its pull request merged. Choose an existing branch and try again."
                )
            },
            status_code=400,
        )
        RecordingAsyncClient.responses = [
            missing,
            {"run": {"id": "run-main", "status": "RUNNING"}, "agent": {"id": "agent-main"}},
        ]

        with (
            patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient),
            patch("director_orchestrator.providers.asyncio.sleep") as sleep,
        ):
            run = await CursorAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(run.run_id, "run-main")
        self.assertEqual(len(RecordingAsyncClient.calls), 2)
        self.assertEqual(
            RecordingAsyncClient.calls[0]["json"]["repos"][0]["startingRef"],
            "cursor/director-task-1-f90c",
        )
        self.assertEqual(
            RecordingAsyncClient.calls[1]["json"]["repos"][0]["startingRef"],
            "main",
        )
        sleep.assert_not_awaited()

    async def test_cursor_dispatch_retries_deleted_working_branch_on_later_durable_ref(self):
        settings = replace(
            Settings(),
            cursor_api_key="test-cursor",
            allowed_starting_refs=(
                "cursor/director-task-1-f90c",
                "cursor/other-ephemeral",
                "main",
            ),
            cursor_dispatch_max_attempts=1,
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Continue Edge Oracle research",
            acceptance=["Return evidence"],
            repository="edwardjones3976/edge_engine",
            starting_ref="cursor/director-task-1-f90c",
        )
        missing = FakeResponse(
            {
                "message": (
                    'GitHub branch not found: The starting branch or commit '
                    '"cursor/director-task-1-f90c" does not exist in '
                    "edwardjones3976/edge_engine. It may have been deleted after "
                    "its pull request merged. Choose an existing branch and try again."
                )
            },
            status_code=400,
        )
        RecordingAsyncClient.responses = [
            missing,
            {"run": {"id": "run-main", "status": "RUNNING"}, "agent": {"id": "agent-main"}},
        ]

        with (
            patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient),
            patch("director_orchestrator.providers.asyncio.sleep") as sleep,
        ):
            run = await CursorAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(run.run_id, "run-main")
        self.assertEqual(len(RecordingAsyncClient.calls), 2)
        self.assertEqual(
            RecordingAsyncClient.calls[1]["json"]["repos"][0]["startingRef"],
            "main",
        )
        sleep.assert_not_awaited()

    async def test_cursor_dispatch_does_not_rewrite_a_missing_durable_ref(self):
        settings = replace(Settings(), cursor_api_key="test-cursor", allowed_starting_refs=("main",))
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Review exact SHA",
            acceptance=["Return evidence"],
            repository="edwardjones3976/edge_engine",
            starting_ref="main",
        )
        RecordingAsyncClient.responses = [
            FakeResponse(
                {
                    "message": (
                        'GitHub branch not found: The starting branch or commit "main" '
                        "does not exist in edwardjones3976/edge_engine. Choose an "
                        "existing branch and try again."
                    )
                },
                status_code=400,
            )
        ]

        with (
            patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient),
            patch("director_orchestrator.providers.asyncio.sleep") as sleep,
        ):
            with self.assertRaises(httpx.HTTPStatusError):
                await CursorAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(len(RecordingAsyncClient.calls), 1)
        sleep.assert_not_awaited()

    async def test_cursor_revision_starts_a_new_agent_when_working_branch_is_gone(self):
        settings = replace(Settings(), cursor_api_key="test-cursor", allowed_starting_refs=("main",))
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Continue Edge Oracle research",
            acceptance=["Return evidence"],
            repository="edwardjones3976/edge_engine",
            starting_ref="main",
        )
        prior = ProviderRun(
            provider=ProviderName.CURSOR,
            run_id="run-old",
            session_id="agent-old",
            status="FINISHED",
            url="https://cursor.example/agent-old",
        )
        RecordingAsyncClient.responses = [
            FakeResponse(
                {
                    "message": (
                        'GitHub branch not found: The starting branch or commit '
                        '"cursor/director-task-1-f90c" does not exist in '
                        "edwardjones3976/edge_engine. It may have been deleted after "
                        "its pull request merged. Choose an existing branch and try again."
                    )
                },
                status_code=400,
            ),
            {
                "run": {"id": "run-fresh", "status": "RUNNING"},
                "agent": {"id": "agent-fresh", "url": "https://cursor.example/agent-fresh"},
            },
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await CursorAdapter(settings).revise(
                prior,
                "Address the failed acceptance checks",
                assignment=assignment,
                context="bounded-context",
            )

        self.assertEqual(run.run_id, "run-fresh")
        self.assertEqual(run.session_id, "agent-fresh")
        self.assertTrue(RecordingAsyncClient.calls[0]["url"].endswith("/v1/agents/agent-old/runs"))
        self.assertTrue(RecordingAsyncClient.calls[1]["url"].endswith("/v1/agents"))
        self.assertEqual(
            RecordingAsyncClient.calls[1]["json"]["repos"][0]["startingRef"],
            "main",
        )
        self.assertIn(
            "working branch was deleted",
            RecordingAsyncClient.calls[1]["json"]["prompt"]["text"],
        )

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
