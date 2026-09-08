# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:00 UTC | BEGIN]]
"""Offline adapter qualification; fixture authentication is not host enrollment."""
from __future__ import annotations

import copy
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from director_orchestrator.claude_director import (
    ClaudeDirector, ClaudeDirectorBinding, ClaudeDirectorPacketTransport,
)
from director_orchestrator.config import Settings
from director_orchestrator.director import OpenAIDirector, build_director
from director_orchestrator.interactions import ProviderTransport
from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.models import DirectorPlan, ProviderName, ReviewDecision, ReviewVerdict, SlackIntake, TaskRecord
from director_orchestrator.packet_contracts import PacketContractError, document_digest


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"
REVIEW = {"verdict": "accept", "summary": "Bounded evidence accepted.", "evidence": []}


class FixtureNativeTransport:
    def __init__(self):
        self.calls = []
        self.mutate = lambda result: None
        self.failure = None
        self.outcome = REVIEW

    def send(self, work_packet_json, *, context):
        work = json.loads(work_packet_json)
        self.calls.append((work, context))
        if self.failure:
            raise self.failure
        result = json.loads((SCHEMAS / "model-result-packet-v1.example.json").read_text())
        for field in ("task_id", "assignment_id", "idempotency_key", "authority_snapshot_digest", "correlation"):
            result[field] = copy.deepcopy(work[field])
        result["usage"]["model"] = "fixture-claude-model"
        result["outcome"] = json.dumps(self.outcome)
        result["artifacts"] = []
        self.mutate(result)
        return result


class AdapterFixture:
    def __init__(self):
        self.native = FixtureNativeTransport()
        self.auth_calls = []
        self.reject_stage = None
        self.mutate_binding = lambda binding: binding

    def bind(self, request):
        work = json.loads((SCHEMAS / "model-work-packet-v1.example.json").read_text())
        work["objective"] = request
        work["allowed_tools"] = []
        work["limits"].update(timeout_seconds=60, max_hops=1, max_revisions=0)
        context = ProviderAdapterContext(
            task_id=work["task_id"], assignment_id=work["assignment_id"],
            idempotency_key=work["idempotency_key"], work_packet_digest=document_digest(work),
            writer_token_digest="sha256:" + "f" * 64, timeout_seconds=60,
            budget_usd=1.0, max_hops=1, max_revisions=0, allowed_tools=(),
            allowed_external_effects=(), fencing_generation=1,
        )
        return self.mutate_binding(ClaudeDirectorBinding(work, context))

    def authenticate(self, binding, *, model_name, result):
        stage = "admission" if result is None else "result"
        self.auth_calls.append(stage)
        if self.reject_stage == stage:
            raise PermissionError("fixture rejects unauthenticated " + stage)

    def transport(self):
        return ClaudeDirectorPacketTransport(
            self.native, model_name="fixture-claude-model", bind_request=self.bind,
            authenticate=self.authenticate,
        )


class ClaudeDirectorConfigTests(unittest.TestCase):
    def test_flag_defaults_off_and_environment_is_read_at_construction(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(Settings().claude_director_enabled)
        with patch.dict(os.environ, {"CLAUDE_DIRECTOR_ENABLED": "true"}):
            self.assertTrue(Settings().claude_director_enabled)

    def test_disabled_retains_actual_openai_identity(self):
        director = build_director(Settings(claude_director_enabled=False, openai_model="fixture-openai"))
        self.assertIsInstance(director, OpenAIDirector)
        self.assertEqual(director.provider, ProviderName.OPENAI)
        self.assertEqual(director.transport, ProviderTransport.OPENAI_RESPONSES_API)
        self.assertEqual(director.model_name, "fixture-openai")
        self.assertIn("this invocation is openai, model fixture-openai", director.instructions)

    def test_enabled_cannot_fall_back_even_when_openai_or_routine_credentials_exist(self):
        settings = Settings(claude_director_enabled=True, openai_api_key="fixture-only", claude_routine_url="https://example.invalid", claude_routine_token="fixture-only")
        for transport in (None, object()):
            with self.subTest(transport=type(transport).__name__), self.assertRaisesRegex(RuntimeError, "enrolled"):
                build_director(settings, claude_transport=transport)

    def test_strategy_separates_leadership_from_runtime_selection_and_accounts(self):
        settings = Settings(claude_director_enabled=False)
        strategy = settings.provider_strategy()
        self.assertEqual(strategy["claude"]["project_role"], "acting_director")
        self.assertEqual(strategy["claude"]["runtime_selection"], "disabled_legacy_openai_runtime")
        self.assertEqual(strategy["openai"]["runtime_selection"], "legacy_responses_api_director")
        self.assertEqual(strategy["claude"]["account_context"], settings.claude_account_context)
        self.assertEqual(strategy["openai"]["account_context"], settings.openai_account_context)


class ClaudeDirectorTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = AdapterFixture()
        self.director = build_director(Settings(claude_director_enabled=True), claude_transport=self.fixture.transport())

    async def call(self):
        return await self.director._structured("fixture request", ReviewDecision, "review_decision")

    async def test_typed_native_review_preserves_identity_and_verifies_both_stages(self):
        with patch("httpx.AsyncClient", side_effect=AssertionError("no Responses API fallback")):
            result = await self.call()
        self.assertIsInstance(self.director, ClaudeDirector)
        self.assertEqual(self.director.provider, ProviderName.CLAUDE)
        self.assertEqual(self.director.transport, ProviderTransport.CLAUDE_NATIVE_HOST)
        self.assertEqual(self.director.model_name, "fixture-claude-model")
        self.assertEqual(result.verdict, ReviewVerdict.ACCEPT)
        self.assertEqual(self.fixture.auth_calls, ["admission", "result"])
        self.assertEqual(len(self.fixture.native.calls), 1)
        request = json.loads(self.fixture.native.calls[0][0]["objective"])
        self.assertEqual(request["executing_provider"], "claude")
        self.assertIn("this invocation is claude, model fixture-claude-model", request["instructions"])

    async def test_unauthenticated_admission_never_invokes_native(self):
        self.fixture.reject_stage = "admission"
        with self.assertRaises(PermissionError):
            await self.call()
        self.assertEqual(self.fixture.native.calls, [])

    async def test_native_plan_returns_the_existing_typed_contract(self):
        self.fixture.native.outcome = {
            "project": "fixture", "normalized_outcome": "Request one owner decision",
            "risk_level": "low", "approval_required": True, "max_hops": 1,
            "max_revisions": 0, "assignments": [{"sequence": 1, "agent": "owner",
                "task": "Confirm fixture scope", "acceptance": ["Scope recorded"]}],
            "final_acceptance": ["Scope recorded"],
        }
        result = await self.director._structured("fixture plan", DirectorPlan, "director_plan")
        self.assertIsInstance(result, DirectorPlan)
        self.assertEqual(result.assignments[0].agent, ProviderName.OWNER)

    async def test_authorized_packet_timeout_cannot_exceed_adapter_ceiling(self):
        def widen(binding):
            work = copy.deepcopy(binding.work_packet)
            work["limits"]["timeout_seconds"] = 181
            return ClaudeDirectorBinding(work, replace(binding.context,
                timeout_seconds=181, work_packet_digest=document_digest(work)))
        self.fixture.mutate_binding = widen
        with self.assertRaisesRegex(PacketContractError, "bounded call limit"):
            await self.call()
        self.assertEqual(self.fixture.auth_calls, [])
        self.assertEqual(self.fixture.native.calls, [])

    async def test_unauthenticated_result_never_reaches_typed_acceptance(self):
        self.fixture.reject_stage = "result"
        with self.assertRaises(PermissionError):
            await self.call()
        self.assertEqual(len(self.fixture.native.calls), 1)

    async def test_outbound_mismatch_and_widened_limits_fail_before_native(self):
        for updates in ({"task_id": "OTHER"}, {"work_packet_digest": "sha256:" + "0" * 64}, {"timeout_seconds": 181}, {"budget_usd": 2.0}, {"allowed_tools": ("RUN_TESTS",)}, {"fencing_generation": 0}):
            with self.subTest(updates=updates):
                self.fixture.mutate_binding = lambda binding: replace(binding, context=replace(binding.context, **updates))
                with self.assertRaises(PacketContractError):
                    await self.call()
                self.assertEqual(self.fixture.native.calls, [])

    async def test_result_provider_model_linkage_status_and_effects_fail_closed(self):
        mutations = [
            lambda r: r.update(provider="openai"),
            lambda r: r["usage"].update(model="different-model"),
            lambda r: r.update(task_id="OTHER"),
            lambda r: r.update(status="FAILED"),
            lambda r: r.update(side_effects=[{"kind": "WRITE", "target": "fixture", "status": "COMPLETED", "idempotency_key": "fixture", "external_id": None}]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                self.fixture.native.mutate = mutate
                with self.assertRaises(PacketContractError):
                    await self.call()

    async def test_malformed_and_oversized_typed_output_fail_closed(self):
        for outcome in ({"verdict": "accept"}, {**REVIEW, "unauthorized": True}):
            self.fixture.native.outcome = outcome
            with self.assertRaises(ValidationError):
                await self.call()
        self.fixture.native.mutate = lambda r: r.update(outcome="x" * 65_537)
        with self.assertRaisesRegex(PacketContractError, "output limit"):
            await self.call()

    async def test_ambiguous_native_failure_is_not_retried_or_fallen_back(self):
        self.fixture.native.failure = TimeoutError("fixture ambiguous native timeout")
        with patch("httpx.AsyncClient", side_effect=AssertionError("no fallback")), self.assertRaises(TimeoutError):
            await self.call()
        self.assertEqual(len(self.fixture.native.calls), 1)

    async def test_claude_plan_prompt_excludes_self_and_unregistered_openai_worker(self):
        captured = {}
        async def capture(input_text, model, name):
            captured.update(json.loads(input_text))
        self.director._structured = capture
        self.director.settings = replace(
            self.director.settings, claude_routine_url="https://example.invalid",
            claude_routine_token="fixture-only",
        )
        record = TaskRecord(task_id="DIR-FIXTURE", status="PLANNING", intake=SlackIntake(source_event_id="fixture", channel_id="C123", thread_ts="123.1", user_id="U123", text="Review fixture"))
        await self.director.plan(record)
        self.assertNotIn("claude", captured["available_agents"])
        self.assertNotIn("openai", captured["available_agents"])
        self.assertEqual(captured["execution_identity"]["provider"], "claude")
        self.assertIn("Claude acceptance", captured["routing_rules"][0])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
