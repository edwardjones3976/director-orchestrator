from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx

from director_orchestrator.config import Settings
from director_orchestrator.models import Assignment, AssignmentKind, ProviderName, ProviderRun
from director_orchestrator.providers import (
    ClaudeAdapter,
    CursorAdapter,
    GeminiAdapter,
    PerplexityAdapter,
    ProviderError,
)


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

    @staticmethod
    def gemini_assignment() -> Assignment:
        return Assignment(
            sequence=1,
            agent=ProviderName.GEMINI,
            kind=AssignmentKind.LOW_LEVEL_IMPLEMENTATION,
            task="Generate low-level mechanical prototype boilerplate in an isolated sandbox",
            acceptance=["Report the isolated evidence"],
            repository="example/demo",
        )

    async def test_gemini_dispatch_uses_antigravity_interactions_contract(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_api_revision="2026-05-20",
            gemini_agent="antigravity-preview-05-2026",
        )
        RecordingAsyncClient.responses = [
            {
                "id": "interaction-1",
                "environment_id": "environment-1",
                "status": "in_progress",
            }
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await GeminiAdapter(settings).dispatch(self.gemini_assignment(), "context")

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(request["headers"]["Api-Revision"], "2026-05-20")
        self.assertEqual(request["json"]["agent"], "antigravity-preview-05-2026")
        self.assertEqual(request["json"]["environment"], "remote")
        self.assertTrue(request["json"]["background"])
        self.assertTrue(request["json"]["store"])
        self.assertNotIn("model", request["json"])
        self.assertIn("NON-AUTHORITATIVE ANTIGRAVITY ARTIFACT", request["json"]["input"])
        self.assertEqual(run.session_id, "environment-1")

    async def test_gemini_poll_extracts_text_from_interaction_steps(self):
        settings = replace(Settings(), gemini_api_key="test-gemini")
        prior = ProviderRun(
            provider=ProviderName.GEMINI,
            assignment_sequence=2,
            run_id="interaction-1",
            session_id="environment-1",
            status="in_progress",
        )
        RecordingAsyncClient.responses = [
            {
                "id": "interaction-1",
                "status": "completed",
                "steps": [
                    {"type": "thought", "content": [{"type": "text", "text": "hidden"}]},
                    {
                        "type": "model_output",
                        "content": [
                            {"type": "text", "text": "Evidence "},
                            {"type": "text", "text": "complete."},
                        ],
                    },
                ],
            }
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await GeminiAdapter(settings).poll(prior)

        self.assertIn("NON-AUTHORITATIVE ANTIGRAVITY ARTIFACT", run.result or "")
        self.assertTrue((run.result or "").endswith("Evidence complete."))
        self.assertEqual(run.session_id, "environment-1")
        self.assertEqual(run.assignment_sequence, 2)

    async def test_gemini_revision_reuses_interaction_environment(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_agent="antigravity-preview-05-2026",
        )
        prior = ProviderRun(
            provider=ProviderName.GEMINI,
            assignment_sequence=1,
            run_id="interaction-1",
            session_id="environment-1",
            status="completed",
        )
        RecordingAsyncClient.responses = [
            {"id": "interaction-2", "status": "in_progress"}
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await GeminiAdapter(settings).revise(
                prior,
                "Add exact evidence",
                assignment=self.gemini_assignment(),
                context="context",
            )

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(request["json"]["agent"], "antigravity-preview-05-2026")
        self.assertEqual(request["json"]["previous_interaction_id"], "interaction-1")
        self.assertEqual(request["json"]["environment"], "environment-1")
        self.assertTrue(request["json"]["store"])
        self.assertEqual(run.session_id, "environment-1")
        self.assertEqual(run.assignment_sequence, 1)

    async def test_gemini_dispatch_rejects_primary_authority_and_unbound_scope(self):
        settings = replace(Settings(), gemini_api_key="test-gemini")
        assignments = (
            self.gemini_assignment().model_copy(update={"kind": AssignmentKind.GENERAL}),
            self.gemini_assignment().model_copy(
                update={
                    "kind": AssignmentKind.ABSTRACT_IDEATION,
                    "task": "Perform the independent review and final acceptance",
                }
            ),
            self.gemini_assignment().model_copy(
                update={"allow_draft_pr": True, "requires_approval": True}
            ),
        )

        for assignment in assignments:
            with self.subTest(assignment=assignment):
                with self.assertRaises(ProviderError):
                    await GeminiAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(RecordingAsyncClient.calls, [])

    async def test_gemini_dispatch_fails_closed_for_unsafe_profile(self):
        unsafe = (
            replace(Settings(), gemini_api_key="test-gemini", gemini_mode="general"),
            replace(Settings(), gemini_api_key="test-gemini", gemini_allow_review_verdict=True),
            replace(Settings(), gemini_api_key="test-gemini", gemini_allow_primary_authority=True),
        )

        for settings in unsafe:
            with self.subTest(settings=settings):
                with self.assertRaises(ProviderError):
                    await GeminiAdapter(settings).dispatch(self.gemini_assignment(), "context")

        self.assertEqual(RecordingAsyncClient.calls, [])

    async def test_gemini_usage_check_is_stateless_and_never_launches_an_agent(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_api_revision="2026-05-20",
            gemini_usage_model="gemini-3.5-flash-lite",
        )
        RecordingAsyncClient.responses = [
            {"status": "completed", "output_text": "STATUS=OK"}
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await GeminiAdapter(settings).usage_check("probe")

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(request["json"]["model"], "gemini-3.5-flash-lite")
        self.assertFalse(request["json"]["store"])
        self.assertNotIn("agent", request["json"])
        self.assertNotIn("background", request["json"])
        self.assertNotIn("environment", request["json"])
        self.assertNotIn("tools", request["json"])
        self.assertEqual(run.run_id, "gemini-stateless-usage-check")
        self.assertEqual(run.status, "completed")

    async def test_perplexity_usage_check_uses_synchronous_agent_contract(self):
        settings = replace(
            Settings(),
            perplexity_enabled=True,
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

    async def test_perplexity_dispatch_uses_raw_search_without_repository_context(self):
        settings = replace(
            Settings(),
            perplexity_enabled=True,
            perplexity_api_key="test-perplexity",
            perplexity_max_results=7,
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.PERPLEXITY,
            task="Find current primary sources about calibration metrics",
            acceptance=["Report sources and contradictions"],
            repository="example/private-repository",
            estimated_cost_usd=0.25,
        )
        RecordingAsyncClient.responses = [
            {
                "id": "search-1",
                "results": [
                    {
                        "title": "Calibration reference",
                        "url": "https://example.test/reference",
                        "snippet": "A supporting excerpt.",
                        "date": "2026-08-01",
                        "last_updated": "2026-08-10",
                    }
                ],
            }
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await PerplexityAdapter(settings).dispatch(
                assignment,
                "PRIVATE REPOSITORY CONTEXT MUST NOT LEAVE THE ORCHESTRATOR",
            )

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(request["url"], "https://api.perplexity.ai/search")
        self.assertEqual(
            request["json"],
            {"query": assignment.task, "max_results": 7},
        )
        self.assertNotIn("PRIVATE", json.dumps(request))
        self.assertNotIn("example/private-repository", json.dumps(request))
        evidence = json.loads(run.result or "{}")
        self.assertEqual(evidence["query"], assignment.task)
        self.assertEqual(evidence["results"][0]["result_url"], "https://example.test/reference")
        self.assertEqual(evidence["results"][0]["page_title"], "Calibration reference")
        self.assertEqual(evidence["results"][0]["publication_date"], "2026-08-01")
        self.assertEqual(evidence["results"][0]["supporting_excerpt"], "A supporting excerpt.")
        self.assertEqual(evidence["results"][0]["source_type"], "web_result")
        self.assertEqual(evidence["results"][0]["confidence"], "not_provided_by_search_api")

    async def test_perplexity_revision_accepts_engine_contract_without_context_leak(self):
        settings = replace(
            Settings(),
            perplexity_enabled=True,
            perplexity_api_key="test-perplexity",
            perplexity_max_results=5,
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.PERPLEXITY,
            kind=AssignmentKind.RESEARCH,
            task="Find primary calibration sources",
            acceptance=["Report URLs and unavailable evidence"],
            estimated_cost_usd=0.25,
        )
        prior = ProviderRun(
            provider=ProviderName.PERPLEXITY,
            assignment_sequence=1,
            run_id="search-1",
            status="completed",
            result="{}",
        )
        RecordingAsyncClient.responses = [{"id": "search-2", "results": []}]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            revised = await PerplexityAdapter(settings).revise(
                prior,
                "Find one primary source for each named metric",
                assignment=assignment,
                context="PRIVATE REPOSITORY CONTEXT",
            )

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(
            request["json"],
            {
                "query": "Find one primary source for each named metric",
                "max_results": 5,
            },
        )
        self.assertNotIn("PRIVATE REPOSITORY CONTEXT", json.dumps(request))
        self.assertEqual(revised.run_id, "search-2")

    async def test_perplexity_dispatch_fails_closed_when_research_policy_is_not_ready(self):
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.PERPLEXITY,
            task="Find sources",
            acceptance=["Report URLs"],
        )
        unsafe_settings = (
            replace(Settings(), perplexity_api_key="test-perplexity"),
            replace(
                Settings(),
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_mode="general",
            ),
            replace(
                Settings(),
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_allow_repository_write=True,
            ),
            replace(
                Settings(),
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_allow_review_verdict=True,
            ),
            replace(
                Settings(),
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_max_results=21,
            ),
            replace(
                Settings(),
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_search_context_size="unbounded",
            ),
            replace(
                Settings(),
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_timeout_seconds=0,
            ),
            replace(
                Settings(),
                perplexity_enabled=True,
                perplexity_api_key="test-perplexity",
                perplexity_daily_budget_usd=0,
            ),
        )

        for settings in unsafe_settings:
            with self.subTest(settings=settings):
                with self.assertRaises(ProviderError):
                    await PerplexityAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(RecordingAsyncClient.calls, [])

    async def test_perplexity_dispatch_rejects_write_authority_and_budget_overrun(self):
        settings = replace(
            Settings(),
            perplexity_enabled=True,
            perplexity_api_key="test-perplexity",
            perplexity_daily_budget_usd=2.0,
        )
        base = Assignment(
            sequence=1,
            agent=ProviderName.PERPLEXITY,
            task="Find sources",
            acceptance=["Report URLs"],
            requires_approval=True,
        )

        for assignment in (
            base.model_copy(update={"allow_draft_pr": True}),
            base.model_copy(update={"estimated_cost_usd": 2.01}),
        ):
            with self.subTest(assignment=assignment):
                with self.assertRaises(ProviderError):
                    await PerplexityAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(RecordingAsyncClient.calls, [])


if __name__ == "__main__":
    unittest.main()
