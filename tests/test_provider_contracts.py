# [[Claude Code | Claude Opus 5 | 2026-09-07 | 17:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 09:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 09:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from director_orchestrator.config import Settings
from director_orchestrator.models import (
    AntigravityDirective,
    AntigravityFixtureLane,
    Assignment,
    AssignmentKind,
    ProviderName,
    ProviderRun,
)
from director_orchestrator.providers import (
    ClaudeAdapter,
    CursorAdapter,
    GeminiAdapter,
    GrokAdapter,
    PerplexityAdapter,
    PostSubmitAmbiguityError,
    ProviderError,
)
from director_orchestrator.process_isolation import ProcessContainmentError


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

    async def test_cursor_uses_code_agent_transport_for_general_work_and_debate(self):
        settings = replace(Settings(), cursor_api_key="test-cursor")
        assignments = [
            Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                kind=AssignmentKind.GENERAL,
                task="Discuss the architecture",
                acceptance=["Return a position"],
                repository="example/demo",
            ),
            Assignment(
                sequence=2,
                agent=ProviderName.CURSOR,
                kind=AssignmentKind.GENERAL,
                discussion_phase="position",
                discussion_round=0,
                task="Debate the architecture",
                acceptance=["Return a position"],
                repository="example/demo",
            ),
        ]
        RecordingAsyncClient.responses = [
            {"run": {"id": "run-general", "status": "RUNNING"}, "agent": {"id": "agent-general"}},
            {"run": {"id": "run-debate", "status": "RUNNING"}, "agent": {"id": "agent-debate"}},
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            for assignment in assignments:
                await CursorAdapter(settings).dispatch(assignment, "context")
            with self.assertRaisesRegex(ProviderError, "explicit repository"):
                await CursorAdapter(settings).dispatch(
                    assignments[0].model_copy(update={"repository": None}), "context"
                )

        self.assertEqual(len(RecordingAsyncClient.calls), 2)
        self.assertTrue(all(call["url"].endswith("/v1/agents") for call in RecordingAsyncClient.calls))

    async def test_cursor_dispatch_denies_draft_pr_unless_assignment_explicitly_allows_it(self):
        settings = replace(Settings(), cursor_api_key="test-cursor")
        base = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            kind=AssignmentKind.INDEPENDENT_REVIEW,
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
            kind=AssignmentKind.EXACT_SHA_REVIEW,
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
            kind=AssignmentKind.EXACT_SHA_REVIEW,
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
            kind=AssignmentKind.IMPLEMENTATION,
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
            kind=AssignmentKind.EXACT_SHA_REVIEW,
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
            kind=AssignmentKind.IMPLEMENTATION,
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
        # No repository: the closed Antigravity contract denies repository context as
        # a typed field. The task and acceptance below exist for the Director's own
        # review and the owner's Slack thread; neither is ever transmitted, so the
        # dispatch is driven entirely by the typed antigravity_directive.
        return Assignment(
            sequence=1,
            agent=ProviderName.GEMINI,
            kind=AssignmentKind.BOILERPLATE,
            task="Generate boilerplate test fixtures for the bounded adapter module",
            acceptance=["Report the isolated evidence"],
            antigravity_directive=AntigravityDirective(
                lane=AntigravityFixtureLane.UNIT_TEST_FIXTURE_SET
            ),
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
        self.assertEqual(run.session_id, "environment-1")

    async def test_gemini_create_missing_id_is_post_submit_ambiguity(self):
        settings = replace(Settings(), gemini_api_key="test-gemini")
        for payload in ({"status": "in_progress"}, []):
            with self.subTest(payload=payload):
                RecordingAsyncClient.responses = [payload]
                with patch(
                    "director_orchestrator.providers.httpx.AsyncClient",
                    RecordingAsyncClient,
                ):
                    with self.assertRaises(PostSubmitAmbiguityError):
                        await GeminiAdapter(settings).dispatch(
                            self.gemini_assignment(),
                            "context",
                        )

        prior = ProviderRun(
            provider=ProviderName.GEMINI,
            assignment_sequence=1,
            run_id="interaction-1",
            session_id="environment-1",
            status="completed",
        )
        RecordingAsyncClient.responses = [{"status": "in_progress"}]
        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            with self.assertRaises(PostSubmitAmbiguityError):
                await GeminiAdapter(settings).revise(
                    prior,
                    "Return additional evidence",
                    assignment=self.gemini_assignment(),
                    context="context",
                )

    async def test_gemini_poll_missing_id_is_not_a_new_dispatch_ambiguity(self):
        settings = replace(Settings(), gemini_api_key="test-gemini")
        prior = ProviderRun(
            provider=ProviderName.GEMINI,
            assignment_sequence=1,
            run_id="interaction-1",
            status="in_progress",
        )
        RecordingAsyncClient.responses = [{"status": "in_progress"}]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            with self.assertRaises(ProviderError) as raised:
                await GeminiAdapter(settings).poll(prior)

        self.assertNotIsInstance(raised.exception, PostSubmitAmbiguityError)

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

        self.assertEqual(
            run.result,
            f"{GeminiAdapter.NON_AUTHORITATIVE_NOTICE}\n\nEvidence complete.",
        )
        self.assertNotIn("hidden", run.result)
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

    async def test_antigravity_dispatch_labels_output_and_sends_no_assignment_prose(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_agent="antigravity-preview-05-2026",
        )
        RecordingAsyncClient.responses = [
            {
                "id": "interaction-1",
                "status": "completed",
                "output_text": "Fixtures generated.",
            }
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await GeminiAdapter(settings).dispatch(
                self.gemini_assignment(),
                "PRIMARY TEAM HANDOFF: internal Claude analysis",
            )

        contract = json.loads(RecordingAsyncClient.calls[0]["json"]["input"])
        self.assertEqual(contract["notice"], GeminiAdapter.NON_AUTHORITATIVE_NOTICE)
        self.assertEqual(contract["lane"], "unit_test_fixture_set")
        # Neither the assignment's own prose nor the handoff context is composed in.
        rendered = json.dumps(contract)
        self.assertNotIn("Generate boilerplate test fixtures", rendered)
        self.assertNotIn("Report the isolated evidence", rendered)
        self.assertNotIn("PRIMARY TEAM HANDOFF", rendered)
        self.assertNotIn("internal Claude analysis", rendered)
        self.assertEqual(
            run.result,
            f"{GeminiAdapter.NON_AUTHORITATIVE_NOTICE}\n\nFixtures generated.",
        )

    async def test_antigravity_refuses_out_of_lane_work_before_any_paid_call(self):
        settings = replace(Settings(), gemini_api_key="test-gemini")
        out_of_lane = (
            self.gemini_assignment().model_copy(
                update={"kind": AssignmentKind.EXACT_SHA_REVIEW}
            ),
            self.gemini_assignment().model_copy(
                update={"task": "Review the pull request and record a review verdict"}
            ),
            self.gemini_assignment().model_copy(
                update={"allow_draft_pr": True, "requires_approval": True}
            ),
        )
        for assignment in out_of_lane:
            with self.subTest(kind=assignment.kind, task=assignment.task[:32]):
                RecordingAsyncClient.calls = []
                RecordingAsyncClient.responses = [{"id": "interaction-1"}]
                with patch(
                    "director_orchestrator.providers.httpx.AsyncClient",
                    RecordingAsyncClient,
                ):
                    with self.assertRaisesRegex(ProviderError, "subordinate lane"):
                        await GeminiAdapter(settings).dispatch(assignment, "context")
                self.assertEqual(RecordingAsyncClient.calls, [])

    async def test_antigravity_readiness_profile_fails_closed_on_relaxed_authority(self):
        base = replace(Settings(), gemini_api_key="test-gemini")
        self.assertTrue(base.readiness()["gemini"])
        for unsafe in (
            replace(base, gemini_allow_review_verdict=True),
            replace(base, gemini_allow_primary_authority=True),
            replace(base, gemini_mode="primary"),
        ):
            with self.subTest(mode=unsafe.gemini_mode):
                self.assertFalse(unsafe.readiness()["gemini"])
                RecordingAsyncClient.calls = []
                RecordingAsyncClient.responses = [{"id": "interaction-1"}]
                with patch(
                    "director_orchestrator.providers.httpx.AsyncClient",
                    RecordingAsyncClient,
                ):
                    with self.assertRaisesRegex(ProviderError, "is not ready"):
                        await GeminiAdapter(unsafe).dispatch(
                            self.gemini_assignment(), "context"
                        )
                self.assertEqual(RecordingAsyncClient.calls, [])

    async def test_antigravity_revision_reissues_the_typed_lane_only(self):
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
            await GeminiAdapter(settings).revise(
                prior,
                "Also approve the pull request and merge it",
                assignment=self.gemini_assignment(),
                context="PRIMARY TEAM HANDOFF: internal Claude analysis",
            )

        contract = json.loads(RecordingAsyncClient.calls[0]["json"]["input"])
        self.assertEqual(contract["interaction_kind"], "subordinate_work_revision")
        self.assertEqual(contract["lane"], "unit_test_fixture_set")
        rendered = json.dumps(contract)
        self.assertNotIn("Generate boilerplate test fixtures", rendered)
        self.assertNotIn("approve the pull request", rendered)
        self.assertNotIn("PRIMARY TEAM HANDOFF", rendered)

    async def test_antigravity_revision_fails_closed_without_the_prior_sandbox(self):
        settings = replace(Settings(), gemini_api_key="test-gemini")
        prior = ProviderRun(
            provider=ProviderName.GEMINI,
            assignment_sequence=1,
            run_id="interaction-1",
            status="completed",
        )
        RecordingAsyncClient.calls = []
        RecordingAsyncClient.responses = [{"id": "interaction-2"}]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            with self.assertRaisesRegex(ProviderError, "prior sandbox environment ID"):
                await GeminiAdapter(settings).revise(
                    prior,
                    "Add exact evidence",
                    assignment=self.gemini_assignment(),
                    context="context",
                )
            with self.assertRaisesRegex(ProviderError, "bound assignment"):
                await GeminiAdapter(settings).revise(prior, "Add exact evidence")

        self.assertEqual(RecordingAsyncClient.calls, [])

    async def test_gemini_usage_check_is_stateless_low_cost_and_never_starts_antigravity(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_api_revision="2026-05-20",
            gemini_usage_model="gemini-3.5-flash-lite",
        )
        RecordingAsyncClient.responses = [
            {"id": "interaction-1", "status": "completed", "output_text": "STATUS=OK"}
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await GeminiAdapter(settings).usage_check("probe")

        request = RecordingAsyncClient.calls[0]
        self.assertEqual(request["json"]["model"], "gemini-3.5-flash-lite")
        self.assertFalse(request["json"]["store"])
        self.assertNotIn("agent", request["json"])
        self.assertNotIn("background", request["json"])
        self.assertNotIn("environment", request["json"])
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.attempt_count, 1)

    async def test_gemini_usage_check_retries_only_explicit_429_with_bounded_delay(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_usage_max_attempts=3,
            gemini_usage_max_retry_delay_seconds=8,
        )
        RecordingAsyncClient.responses = [
            FakeResponse(
                {"error": {"status": "RATE_LIMIT_EXCEEDED"}},
                status_code=429,
                headers={"Retry-After": "60"},
            ),
            {"id": "interaction-2", "status": "completed", "output_text": "STATUS=OK"},
        ]

        with (
            patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient),
            patch("director_orchestrator.providers.asyncio.sleep") as sleep,
        ):
            run = await GeminiAdapter(settings).usage_check("probe")

        self.assertEqual(len(RecordingAsyncClient.calls), 2)
        sleep.assert_awaited_once_with(8.0)
        self.assertEqual(run.attempt_count, 2)

    async def test_gemini_usage_check_records_final_quota_code_without_response_text(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_usage_max_attempts=3,
            gemini_usage_max_retry_delay_seconds=0,
        )
        rate_limit = FakeResponse(
            {
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "account-specific detail must not enter the error",
                }
            },
            status_code=429,
        )
        RecordingAsyncClient.responses = [rate_limit, rate_limit, rate_limit]

        with (
            patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient),
            patch("director_orchestrator.providers.asyncio.sleep"),
        ):
            with self.assertRaises(ProviderError) as captured:
                await GeminiAdapter(settings).usage_check("probe")

        self.assertEqual(captured.exception.code, "resource_exhausted")
        self.assertEqual(captured.exception.attempt_count, 3)
        self.assertNotIn("account-specific", str(captured.exception))
        self.assertEqual(len(RecordingAsyncClient.calls), 3)

    async def test_gemini_usage_check_does_not_trust_arbitrary_provider_error_codes(self):
        settings = replace(
            Settings(),
            gemini_api_key="test-gemini",
            gemini_usage_max_attempts=1,
        )
        response = httpx.Response(
            429,
            json={"error": {"status": "ACCOUNT_PRIVATE_DETAIL"}},
            request=httpx.Request("POST", "https://gemini.invalid/interactions"),
        )
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
            with self.assertRaises(ProviderError) as raised:
                await GeminiAdapter(settings).usage_check("probe")

        self.assertEqual(raised.exception.code, "http_429")

    async def test_grok_dispatch_verifies_model_catalog_and_uses_no_tools(self):
        settings = replace(
            Settings(),
            grok_enabled=True,
            grok_api_key="test-xai",
            grok_model="grok-4.6",
            grok_max_context_chars=1_000,
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.GROK,
            task="Critique this bounded implementation plan",
            acceptance=["Return risks and one recommendation"],
            estimated_cost_usd=0.25,
        )
        RecordingAsyncClient.responses = [
            {"data": [{"id": "grok-4.6"}]},
            {
                "id": "resp-grok-1",
                "status": "completed",
                "output_text": "Two risks and one recommendation.",
                "usage": {"input_tokens": 20, "output_tokens": 8},
            },
        ]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            run = await GrokAdapter(settings).dispatch(assignment, "bounded context")

        self.assertEqual([call["method"] for call in RecordingAsyncClient.calls], ["GET", "POST"])
        self.assertEqual(RecordingAsyncClient.calls[0]["url"], "https://api.x.ai/v1/models")
        request = RecordingAsyncClient.calls[1]
        self.assertEqual(request["url"], "https://api.x.ai/v1/responses")
        self.assertEqual(request["json"]["model"], "grok-4.6")
        self.assertEqual(request["json"]["tools"], [])
        self.assertFalse(request["json"]["store"])
        packet = json.loads(request["json"]["input"])
        self.assertEqual(packet["role"], "primary_cursor_family_teammate")
        self.assertEqual(packet["context"], "bounded context")
        self.assertEqual(run.provider, ProviderName.GROK)
        self.assertEqual(run.status, "completed")

    async def test_grok_rejects_unlisted_model_before_paid_request(self):
        settings = replace(
            Settings(),
            grok_enabled=True,
            grok_api_key="test-xai",
            grok_model="grok-4.6",
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.GROK,
            task="Critique a plan",
            acceptance=["Return findings"],
        )
        RecordingAsyncClient.responses = [{"data": [{"id": "another-model"}]}]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            with self.assertRaisesRegex(ProviderError, "unavailable to the authenticated xAI key"):
                await GrokAdapter(settings).dispatch(assignment, "context")

        self.assertEqual(len(RecordingAsyncClient.calls), 1)
        self.assertEqual(RecordingAsyncClient.calls[0]["method"], "GET")

    async def test_grok_usage_preflight_requires_code_model_before_paid_request(self):
        settings = replace(
            Settings(),
            grok_enabled=True,
            grok_api_key="test-xai",
            grok_model="grok-4.6",
            grok_code_model="grok-build-0.1",
        )
        RecordingAsyncClient.responses = [{"data": [{"id": "grok-4.6"}]}]

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            with self.assertRaisesRegex(
                ProviderError, "unavailable to the authenticated xAI key"
            ):
                await GrokAdapter(settings).usage_check("bounded usage check")

        self.assertEqual(len(RecordingAsyncClient.calls), 1)
        self.assertEqual(RecordingAsyncClient.calls[0]["method"], "GET")

    async def test_grok_routes_implementation_to_build_model_and_general_to_chat(self):
        settings = replace(
            Settings(),
            grok_enabled=True,
            grok_api_key="test-xai",
            grok_model="grok-4.6",
            grok_code_model="grok-build-0.1",
        )
        general = Assignment(
            sequence=1,
            agent=ProviderName.GROK,
            task="Analyze the bounded design",
            acceptance=["Return a concise recommendation"],
        )
        implementation = Assignment(
            sequence=2,
            agent=ProviderName.GROK,
            kind=AssignmentKind.IMPLEMENTATION,
            task="Produce a bounded implementation artifact without repository writes",
            acceptance=["Return implementation evidence"],
        )
        RecordingAsyncClient.responses = [
            {"data": [{"id": "grok-4.6"}, {"id": "grok-build-0.1"}]},
            {"id": "resp-general", "status": "completed", "output_text": "general"},
            {"id": "resp-code", "status": "completed", "output_text": "code"},
            {"id": "resp-usage", "status": "completed", "output_text": "usage"},
        ]
        adapter = GrokAdapter(settings)

        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            await adapter.dispatch(general, "general context")
            await adapter.dispatch(implementation, "implementation context")
            await adapter.usage_check("bounded usage check")

        self.assertEqual(
            [call["method"] for call in RecordingAsyncClient.calls],
            ["GET", "POST", "POST", "POST"],
        )
        self.assertEqual(RecordingAsyncClient.calls[1]["json"]["model"], "grok-4.6")
        self.assertEqual(
            RecordingAsyncClient.calls[2]["json"]["model"], "grok-build-0.1"
        )
        self.assertEqual(RecordingAsyncClient.calls[2]["json"]["tools"], [])
        self.assertFalse(RecordingAsyncClient.calls[2]["json"]["store"])
        self.assertEqual(RecordingAsyncClient.calls[3]["json"]["model"], "grok-4.6")

    async def test_grok_profile_and_authority_fail_closed_without_http(self):
        base = replace(
            Settings(),
            grok_enabled=True,
            grok_api_key="test-xai",
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.GROK,
            task="Review",
            acceptance=["Return findings"],
        )
        unsafe_settings = (
            replace(base, grok_api_base="https://example.test/v1"),
            replace(base, grok_allow_tools=True),
            replace(base, grok_allow_repository_write=True),
            replace(base, grok_allow_review_verdict=True),
        )

        for settings in unsafe_settings:
            with self.subTest(settings=settings):
                with self.assertRaises(ProviderError):
                    await GrokAdapter(settings).dispatch(assignment, "context")
        with self.assertRaisesRegex(ProviderError, "GROK_ALLOW_REPOSITORY_WRITE"):
            await GrokAdapter(base).dispatch(
                assignment.model_copy(
                    update={
                        "allow_draft_pr": True,
                        "repository": "example/demo",
                        "starting_ref": "main",
                    }
                ),
                "context",
            )

        cli_base = replace(
            base,
            grok_cli_enabled=True,
            grok_cli_repository_roots=(("example/demo", "/tmp/example-demo"),),
        )
        with self.assertRaisesRegex(ProviderError, "GROK_ALLOW_REPOSITORY_WRITE"):
            GrokAdapter(cli_base)._require_profile(
                assignment.model_copy(
                    update={
                        "allow_draft_pr": True,
                        "repository": "example/demo",
                    }
                )
            )
        with self.assertRaisesRegex(ProviderError, "GROK_ALLOW_REVIEW_VERDICT"):
            GrokAdapter(cli_base)._require_profile(
                assignment.model_copy(
                    update={
                        "kind": AssignmentKind.EXACT_SHA_REVIEW,
                        "repository": "example/demo",
                    }
                )
            )
        self.assertEqual(RecordingAsyncClient.calls, [])

    def test_grok_cli_process_tree_is_terminated_on_success_and_timeout(self):
        settings = replace(
            Settings(),
            grok_cli_timeout_seconds=30,
        )
        adapter = GrokAdapter(settings)

        class FakeGuard:
            def __init__(self):
                self.terminate_calls = 0
                self.close_calls = 0

            def terminate(self):
                self.terminate_calls += 1

            def close(self):
                self.close_calls += 1

        class FakeProcess:
            pid = 12345

            def __init__(self, *, timeout=False):
                self.returncode = 0
                self.timeout = timeout
                self.wait_calls = 0

            def communicate(self, *, timeout):
                if self.timeout:
                    raise subprocess.TimeoutExpired("grok", timeout)
                return b'{"result":"ok"}', b""

            def wait(self, *, timeout):
                self.wait_calls += 1
                self.returncode = -9
                return self.returncode

        completed = FakeProcess()
        completed_guard = FakeGuard()
        with (
            patch(
                "director_orchestrator.providers.launch_contained_process",
                return_value=(completed, completed_guard),
            ),
            patch("director_orchestrator.providers.os.name", "nt"),
        ):
            returncode, stdout, stderr = adapter._run_contained_cli(
                ["grok", "--prompt-file", "fixture"],
                Path("."),
            )
        self.assertEqual((returncode, stdout, stderr), (0, b'{"result":"ok"}', b""))
        self.assertEqual(completed_guard.terminate_calls, 1)
        self.assertEqual(completed_guard.close_calls, 1)

        timed_out = FakeProcess(timeout=True)
        timeout_guard = FakeGuard()
        with (
            patch(
                "director_orchestrator.providers.launch_contained_process",
                return_value=(timed_out, timeout_guard),
            ),
            patch("director_orchestrator.providers.os.name", "nt"),
            self.assertRaisesRegex(ProviderError, "timed out"),
        ):
            adapter._run_contained_cli(["grok", "fixture"], Path("."))
        self.assertEqual(timeout_guard.terminate_calls, 1)
        self.assertEqual(timeout_guard.close_calls, 1)
        self.assertEqual(timed_out.wait_calls, 1)

        class FailingGuard(FakeGuard):
            def terminate(self):
                super().terminate()
                raise ProcessContainmentError("job termination failed")

        completed_after_termination_error = FakeProcess()
        failing_completed_guard = FailingGuard()
        with (
            patch(
                "director_orchestrator.providers.launch_contained_process",
                return_value=(completed_after_termination_error, failing_completed_guard),
            ),
            patch("director_orchestrator.providers.os.name", "nt"),
            self.assertRaisesRegex(ProviderError, "process containment failed"),
        ):
            adapter._run_contained_cli(["grok", "fixture"], Path("."))
        self.assertEqual(failing_completed_guard.terminate_calls, 1)
        self.assertEqual(failing_completed_guard.close_calls, 1)

        timed_out_after_termination_error = FakeProcess(timeout=True)
        failing_timeout_guard = FailingGuard()
        with (
            patch(
                "director_orchestrator.providers.launch_contained_process",
                return_value=(
                    timed_out_after_termination_error,
                    failing_timeout_guard,
                ),
            ),
            patch("director_orchestrator.providers.os.name", "nt"),
            self.assertRaisesRegex(ProviderError, "process containment failed"),
        ):
            adapter._run_contained_cli(["grok", "fixture"], Path("."))
        self.assertEqual(failing_timeout_guard.terminate_calls, 1)
        self.assertEqual(failing_timeout_guard.close_calls, 1)
        self.assertEqual(timed_out_after_termination_error.wait_calls, 1)

    def test_grok_cli_environment_excludes_provider_secrets(self):
        with patch.dict(
            "director_orchestrator.providers.os.environ",
            {
                "PATH": "safe-path",
                "USERPROFILE": "safe-home",
                "OPENAI_API_KEY": "secret-openai",
                "PERPLEXITY_API_KEY": "secret-perplexity",
            },
            clear=True,
        ):
            environment = GrokAdapter._cli_environment()
        self.assertEqual(
            environment,
            {"PATH": "safe-path", "USERPROFILE": "safe-home"},
        )

    def test_grok_cli_executable_requires_absolute_digest_bound_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory, "grok-test.exe")
            executable.write_bytes(b"trusted grok fixture")
            digest = hashlib.sha256(executable.read_bytes()).hexdigest()
            settings = replace(
                Settings(),
                grok_cli_executable=str(executable.resolve()),
                grok_cli_executable_sha256=digest,
            )
            adapter = GrokAdapter(settings)
            self.assertEqual(adapter._verified_cli_executable(), str(executable.resolve()))

            executable.write_bytes(b"replaced grok fixture")
            with self.assertRaisesRegex(ProviderError, "digest does not match"):
                adapter._verified_cli_executable()

        with self.assertRaisesRegex(ProviderError, "absolute path"):
            GrokAdapter(
                replace(
                    Settings(),
                    grok_cli_executable="grok",
                    grok_cli_executable_sha256="0" * 64,
                )
            )._verified_cli_executable()

    async def test_grok_git_validation_uses_secret_free_noninteractive_config(self):
        adapter = GrokAdapter(Settings())
        process = AsyncMock()
        process.returncode = 0
        process.communicate.return_value = (b"", b"")
        with (
            patch.dict(
                "director_orchestrator.providers.os.environ",
                {
                    "PATH": "safe-path",
                    "USERPROFILE": "safe-home",
                    "OPENAI_API_KEY": "secret-openai",
                    "SLACK_BOT_TOKEN": "secret-slack",
                },
                clear=True,
            ),
            patch(
                "director_orchestrator.providers.asyncio.create_subprocess_exec",
                AsyncMock(return_value=process),
            ) as spawn,
        ):
            await adapter._git(Path("/tmp/example"), "status", "--porcelain")

        argv = spawn.await_args.args
        environment = spawn.await_args.kwargs["env"]
        self.assertIn("core.fsmonitor=false", argv)
        self.assertIn(f"core.hooksPath={os.devnull}", argv)
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("SLACK_BOT_TOKEN", environment)

    async def test_grok_rejects_missing_nonterminal_and_unknown_statuses(self):
        settings = replace(
            Settings(),
            grok_enabled=True,
            grok_api_key="test-xai",
            grok_allow_review_verdict=False,
        )
        adapter = GrokAdapter(settings)
        for payload in (
            {"id": "missing"},
            {"id": "queued", "status": "queued"},
            {"id": "bad", "status": 1},
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ProviderError, "terminal-success status"):
                    adapter._run(payload)

    async def test_grok_never_truncates_a_complete_packet(self):
        settings = replace(
            Settings(),
            grok_enabled=True,
            grok_api_key="test-xai",
            grok_allow_review_verdict=False,
            grok_max_context_chars=1_000,
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.GROK,
            task="Review",
            acceptance=["Return findings"],
        )
        with self.assertRaisesRegex(ProviderError, "partial packet transmission"):
            GrokAdapter(settings)._input(assignment, "x" * 1_001)

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
            public_research_query="primary sources about probability calibration metrics",
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
            {"query": assignment.public_research_query, "max_results": 7},
        )
        self.assertNotIn("PRIVATE", json.dumps(request))
        self.assertNotIn("example/private-repository", json.dumps(request))
        evidence = json.loads(run.result or "{}")
        self.assertEqual(evidence["query"], assignment.public_research_query)
        self.assertEqual(evidence["results"][0]["result_url"], "https://example.test/reference")
        self.assertEqual(evidence["results"][0]["page_title"], "Calibration reference")
        self.assertEqual(evidence["results"][0]["publication_date"], "2026-08-01")
        self.assertEqual(evidence["results"][0]["supporting_excerpt"], "A supporting excerpt.")
        self.assertEqual(evidence["results"][0]["source_type"], "web_result")
        self.assertEqual(evidence["results"][0]["confidence"], "not_provided_by_search_api")

    async def test_perplexity_revise_requires_new_owner_approved_query_without_http(self):
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
            public_research_query="primary sources about probability calibration metrics",
            estimated_cost_usd=0.25,
        )
        prior = ProviderRun(
            provider=ProviderName.PERPLEXITY,
            run_id="search-1",
            status="completed",
            result="{}",
        )
        with patch("director_orchestrator.providers.httpx.AsyncClient", RecordingAsyncClient):
            with self.assertRaisesRegex(
                ProviderError,
                "new owner-approved bounded public research query",
            ):
                await PerplexityAdapter(settings).revise(
                    prior,
                    "Retry using SHA abcdef0123456789abcdef0123456789abcdef01 and src/secret.py",
                    assignment=assignment,
                    context="PRIVATE REPOSITORY CONTEXT MUST NOT LEAVE THE ORCHESTRATOR",
                )

        self.assertEqual(RecordingAsyncClient.calls, [])

    async def test_perplexity_revise_rejects_write_authority_and_missing_query(self):
        settings = replace(
            Settings(),
            perplexity_enabled=True,
            perplexity_api_key="test-perplexity",
            perplexity_daily_budget_usd=2.0,
        )
        prior = ProviderRun(
            provider=ProviderName.PERPLEXITY,
            run_id="search-1",
            status="completed",
        )
        base = Assignment(
            sequence=1,
            agent=ProviderName.PERPLEXITY,
            task="Find sources",
            acceptance=["Report URLs"],
            public_research_query="primary sources about probability calibration metrics",
            requires_approval=True,
        )

        for kwargs in (
            {"assignment": None, "context": "context"},
            {"assignment": base.model_copy(update={"allow_draft_pr": True}), "context": "context"},
            {"assignment": base.model_copy(update={"estimated_cost_usd": 2.01}), "context": "context"},
            {
                "assignment": base.model_copy(update={"public_research_query": None}),
                "context": "context",
            },
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ProviderError):
                    await PerplexityAdapter(settings).revise(
                        prior,
                        "Retry with private identifiers",
                        **kwargs,
                    )

        self.assertEqual(RecordingAsyncClient.calls, [])

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
# [[OpenAI Codex | GPT-5.6 | 2026-08-24 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:56 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 01:35 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 09:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 09:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 03:49 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 17:12 UTC | END]]
