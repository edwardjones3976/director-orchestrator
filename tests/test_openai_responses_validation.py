# [[Grok Bot | Crock Bot | UNKNOWN model/session/charges | 2026-09-13 | BEGIN]]
"""Fixture regressions for OpenAI Responses validation route selection."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import json
import unittest

from director_orchestrator.packet_contracts import canonical_json_bytes
from director_orchestrator.openai_responses_validation import (
    OpenAIResponsesValidationRoute,
    normalize_openai_responses_capture,
    responses_validation_configuration_digest,
)
from director_orchestrator.provider_validation import (
    ValidationBridgeService,
    ValidationError,
    normalize_capture,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-fixture-responses"


def work_packet():
    work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text())
    work.update(
        task_id="VALIDATION-canary",
        assignment_id="VALIDATION-canary",
        starting_ref="a" * 40,
        allowed_tools=[],
        allowed_external_effects=[],
        idempotency_key="VALIDATION-canary",
    )
    work["limits"].update(max_hops=1, max_revisions=0, timeout_seconds=30)
    return work


def genuine_responses_envelope(*, model=MODEL, usage=None, response_id="resp_fixture_1"):
    usage = usage or {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    return {
        "object": "response",
        "id": response_id,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "model": model,
        "service_tier": "default",
        "created_at": 1_700_000_000,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "Canary analyzed"}],
            }
        ],
        "usage": usage,
    }


class ResponsesNormalizeTests(unittest.TestCase):
    def test_genuine_envelope_normalizes(self):
        raw = canonical_json_bytes(genuine_responses_envelope())
        result = normalize_openai_responses_capture(work_packet(), raw, expected_model=MODEL)
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["run_reference"], "resp_fixture_1")
        self.assertEqual(result["usage"]["total_tokens"], 16)

    def test_normalize_capture_selects_responses_object(self):
        raw = canonical_json_bytes(genuine_responses_envelope())
        result = normalize_capture("openai", work_packet(), raw)
        self.assertEqual(result["run_reference"], "resp_fixture_1")

    def test_arbitrary_success_json_is_not_live_ok(self):
        raw = canonical_json_bytes({"status": "SUCCESS", "session_id": "x", "outcome": "nope"})
        with self.assertRaises(Exception):
            normalize_capture("openai", work_packet(), raw)

    def test_wrong_model_and_missing_usage_refuse(self):
        bad_model = genuine_responses_envelope(model="other-model")
        with self.assertRaisesRegex(ValidationError, "model"):
            normalize_openai_responses_capture(
                work_packet(), canonical_json_bytes(bad_model), expected_model=MODEL
            )
        bad_usage = genuine_responses_envelope()
        bad_usage.pop("usage")
        with self.assertRaisesRegex(ValidationError, "usage"):
            normalize_openai_responses_capture(
                work_packet(), canonical_json_bytes(bad_usage), expected_model=MODEL
            )


class ResponsesRouteSelectionTests(unittest.TestCase):
    def test_route_requires_enrolled_meter(self):
        transport = SimpleNamespace(response_meter=None, model=MODEL)
        with self.assertRaisesRegex(ValueError, "enrolled response meter"):
            OpenAIResponsesValidationRoute(
                transport=transport,
                configuration_digest="c" * 64,
                total_token_limit=128,
                expected_model=MODEL,
                meter_enrollment_digest="d" * 64,
            )

    def test_bridge_selects_responses_path_when_configured(self):
        meter = object()
        transport = SimpleNamespace(response_meter=meter, model=MODEL, maximum_timeout_seconds=60)
        route = OpenAIResponsesValidationRoute(
            transport=transport,
            configuration_digest="c" * 64,
            total_token_limit=128,
            expected_model=MODEL,
            meter_enrollment_digest="d" * 64,
        )
        store = MagicMock()
        service = ValidationBridgeService(
            store=store,
            production_executor=MagicMock(),
            capture_key="bridge-fixture-" + "k" * 40,
            owner_token="owner-fixture-" + "o" * 40,
            account_files={},
            openai_responses_route=route,
        )
        with patch.object(service, "_run_openai_responses", return_value={"transport": "responses-api"}) as runner:
            from director_orchestrator.provider_validation import ValidationRequest
            out = service.run(ValidationRequest(generation="a" * 64, provider="openai"), service.owner_token)
        runner.assert_called_once()
        self.assertEqual(out["transport"], "responses-api")
        store.admit.assert_not_called()

    def test_native_codex_refusal_unchanged_without_responses_route(self):
        from director_orchestrator.native_codex_transport import NativeCodexPacketTransport
        import tempfile, os
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "codex"
            if os.name == "nt":
                exe = Path(directory) / "codex.exe"
            exe.write_bytes(b"MZ" if os.name == "nt" else b"#!/bin/sh\n")
            exe.chmod(0o755)
            transport = NativeCodexPacketTransport(str(exe.resolve()), "gpt-fixture")
            context = SimpleNamespace(
                fencing_generation=1,
                max_hops=1,
                timeout_seconds=30,
                allowed_external_effects=(),
                total_token_limit=64,
            )
            with self.assertRaisesRegex(RuntimeError, "total_token_limit cannot be enforced"):
                transport.send(canonical_json_bytes(work_packet()), context=context)

    def test_configuration_digest_binds_transport_and_meter(self):
        digest = responses_validation_configuration_digest(
            model=MODEL,
            api_url="https://api.openai.com/v1/responses",
            credential_digest="e" * 64,
            organization_id="org_fixture",
            api_project_id="proj_fixture",
            meter_enrollment_digest="f" * 64,
            total_token_limit=128,
        )
        self.assertEqual(len(digest), 64)
        other = responses_validation_configuration_digest(
            model=MODEL,
            api_url="https://api.openai.com/v1/responses",
            credential_digest="e" * 64,
            organization_id="org_fixture",
            api_project_id="proj_other",
            meter_enrollment_digest="f" * 64,
            total_token_limit=128,
        )
        self.assertNotEqual(digest, other)


if __name__ == "__main__":
    unittest.main()
# [[Grok Bot | Crock Bot | UNKNOWN model/session/charges | 2026-09-13 | END]]
