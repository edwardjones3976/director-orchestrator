# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 18:30 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 23:04 UTC | BEGIN]]
# [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | BEGIN]]
"""Fixture regressions for OpenAI Responses validation route selection."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch
import json
import unittest

from director_orchestrator.packet_contracts import canonical_json_bytes
from director_orchestrator.openai_responses_validation import (
    OpenAIResponsesValidationRoute,
    normalize_openai_responses_capture,
    responses_route_from_file,
    responses_runtime_binding,
    responses_validation_configuration_digest,
)
from director_orchestrator.provider_validation import (
    CaptureVerifier,
    PostgresValidationStore,
    ValidationBridgeService,
    ValidationCapture,
    ValidationError,
    ValidationRequest,
    digest,
    make_capture,
    normalize_capture,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-fixture-responses"
ORG_ID = "org-fixture"
PROJ_ID = "proj-fixture"
REQ_ID = "req-fixture-123"


def work_packet():
    work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text())
    work.update(
        task_id="VALIDATION-canary",
        assignment_id="VALIDATION-canary",
        starting_ref="a" * 40,
        allowed_tools=[],
        allowed_external_effects=[],
        idempotency_key="sha256:" + "b" * 64,
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


def genuine_responses_bundle(*, model=MODEL, usage=None, response_id="resp_fixture_1",
                             org=ORG_ID, project=PROJ_ID, request_id=REQ_ID):
    return {
        "response": genuine_responses_envelope(model=model, usage=usage, response_id=response_id),
        "response_headers": {
            "openai-organization": org,
            "openai-project": project,
            "x-request-id": request_id,
        },
    }


class ResponsesNormalizeTests(unittest.TestCase):
    def test_genuine_bundle_normalizes_with_route(self):
        bundle = genuine_responses_bundle()
        raw = canonical_json_bytes(bundle)
        route = SimpleNamespace(
            expected_model=MODEL,
            expected_organization_id=ORG_ID,
            expected_api_project_id=PROJ_ID,
            total_token_limit=128,
            validate_canary=MagicMock(),
        )
        route.normalize = lambda work, b: normalize_openai_responses_capture(
            work, b, expected_model=route.expected_model,
            expected_organization_id=route.expected_organization_id,
            expected_api_project_id=route.expected_api_project_id,
            total_token_limit=route.total_token_limit,
        )
        result = normalize_capture("openai", work_packet(), raw, openai_responses_route=route)
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["run_reference"], "resp_fixture_1")
        self.assertEqual(result["usage"]["total_tokens"], 16)

    def test_generic_normalize_capture_without_route_rejects_responses_bundle(self):
        bundle = genuine_responses_bundle()
        raw = canonical_json_bytes(bundle)
        # Without openai_responses_route, normalize_capture falls back to normalize_codex_jsonl and fails.
        # It must NOT allow payload shape to select the Responses dialect.
        with self.assertRaises(Exception):
            normalize_capture("openai", work_packet(), raw, openai_responses_route=None)

    def test_arbitrary_success_json_is_not_live_ok(self):
        raw = canonical_json_bytes({"status": "SUCCESS", "session_id": "x", "outcome": "nope"})
        with self.assertRaises(Exception):
            normalize_openai_responses_capture(work_packet(), raw, expected_model=MODEL)

    def test_wrong_model_and_missing_usage_refuse(self):
        bad_model = genuine_responses_bundle(model="other-model")
        with self.assertRaisesRegex(ValidationError, "model"):
            normalize_openai_responses_capture(
                work_packet(), canonical_json_bytes(bad_model), expected_model=MODEL
            )
        bad_usage = genuine_responses_bundle()
        bad_usage["response"].pop("usage")
        with self.assertRaisesRegex(ValidationError, "usage"):
            normalize_openai_responses_capture(
                work_packet(), canonical_json_bytes(bad_usage), expected_model=MODEL
            )

    def test_missing_or_mismatched_response_headers_refuse(self):
        bad_org = genuine_responses_bundle(org="wrong-org")
        with self.assertRaisesRegex(ValidationError, "organization"):
            normalize_openai_responses_capture(
                work_packet(),
                canonical_json_bytes(bad_org),
                expected_model=MODEL,
                expected_organization_id=ORG_ID,
                expected_api_project_id=PROJ_ID,
            )

        bad_proj = genuine_responses_bundle(project="wrong-proj")
        with self.assertRaisesRegex(ValidationError, "project"):
            normalize_openai_responses_capture(
                work_packet(),
                canonical_json_bytes(bad_proj),
                expected_model=MODEL,
                expected_organization_id=ORG_ID,
                expected_api_project_id=PROJ_ID,
            )

        missing_req_id = genuine_responses_bundle(request_id="")
        with self.assertRaisesRegex(ValidationError, "request ID"):
            normalize_openai_responses_capture(
                work_packet(),
                canonical_json_bytes(missing_req_id),
                expected_model=MODEL,
                expected_organization_id=ORG_ID,
                expected_api_project_id=PROJ_ID,
            )

    def test_exceeding_total_token_limit_refuses(self):
        bundle = genuine_responses_bundle(usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        })
        with self.assertRaisesRegex(ValidationError, "exceeds total_token_limit"):
            normalize_openai_responses_capture(
                work_packet(),
                canonical_json_bytes(bundle),
                expected_model=MODEL,
                total_token_limit=100,
            )


class ResponsesRouteTests(unittest.TestCase):
    def test_route_requires_enrolled_meter(self):
        transport = SimpleNamespace(response_meter=None, model=MODEL)
        with self.assertRaisesRegex(ValueError, "enrolled response meter"):
            OpenAIResponsesValidationRoute(
                transport=transport,
                total_token_limit=128,
                expected_model=MODEL,
                meter_enrollment_digest="d" * 64,
            )

    def test_configuration_digest_binds_transport_and_meter(self):
        d1 = responses_validation_configuration_digest(
            model=MODEL,
            api_url="https://api.openai.com/v1/responses",
            credential_digest="e" * 64,
            organization_id="org_fixture",
            api_project_id="proj_fixture",
            meter_enrollment_digest="f" * 64,
            total_token_limit=128,
            maximum_timeout_seconds=180,
            runtime_binding="a" * 64,
        )
        self.assertEqual(len(d1), 64)
        d2 = responses_validation_configuration_digest(
            model=MODEL,
            api_url="https://api.openai.com/v1/responses",
            credential_digest="e" * 64,
            organization_id="org_fixture",
            api_project_id="proj_other",
            meter_enrollment_digest="f" * 64,
            total_token_limit=128,
            maximum_timeout_seconds=180,
            runtime_binding="a" * 64,
        )
        self.assertNotEqual(d1, d2)

    def test_configuration_digest_binds_the_executing_runtime(self):
        """A replaced runtime or a changed proxy/CA name changes the route digest."""
        import os
        import shutil
        from director_orchestrator import openai_responses_validation as module
        package = Path(module.__file__).resolve().parent

        def transport():
            return SimpleNamespace(response_meter=SimpleNamespace(enrollments={}), model=MODEL,
                                   api_url="https://api.openai.com/v1", _api_key="sk-test",
                                   maximum_timeout_seconds=60)

        route = OpenAIResponsesValidationRoute(transport=transport(), total_token_limit=128,
                                               expected_model=MODEL, meter_enrollment_digest="d" * 64,
                                               expected_organization_id="org-1", expected_api_project_id="proj-1")
        with patch.dict(os.environ, {}, clear=True):
            baseline, digest_before = responses_runtime_binding(), route.configuration_digest
            with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:8080"}):
                self.assertNotEqual(responses_runtime_binding(), baseline)
                self.assertNotEqual(route.configuration_digest, digest_before)
            with tempfile.TemporaryDirectory() as directory:
                copy = Path(directory) / ("a" * 64) / "director_orchestrator"
                copy.mkdir(parents=True)
                for name in module._RESPONSES_RUNTIME_MODULES:
                    shutil.copyfile(package / name, copy / name)
                with patch.object(module, "__file__", str(copy / "openai_responses_validation.py")):
                    relocated = responses_runtime_binding()
                    self.assertNotEqual(relocated, baseline)
                    self.assertNotEqual(route.configuration_digest, digest_before)
                    with (copy / "primary_transports.py").open("ab") as handle:
                        handle.write(b"\n# replaced\n")
                    self.assertNotEqual(responses_runtime_binding(), relocated)
            self.assertEqual(route.configuration_digest, digest_before)

    def test_configuration_digest_binds_the_timeout_ceiling(self):
        """Finding 4: the transport ceiling is part of the identity.

        The ceiling bounds the whole-operation deadline every store call
        obeys, so changing it changes the configuration digest; an integer
        and the equal float are one identity; a missing or non-positive
        ceiling is an incomplete pin.
        """
        base = dict(model=MODEL, api_url="https://api.openai.com/v1/responses",
                    credential_digest="e" * 64, organization_id="org_fixture",
                    api_project_id="proj_fixture", meter_enrollment_digest="f" * 64,
                    total_token_limit=128, runtime_binding="a" * 64)
        d180 = responses_validation_configuration_digest(**base, maximum_timeout_seconds=180)
        d60 = responses_validation_configuration_digest(**base, maximum_timeout_seconds=60)
        self.assertNotEqual(d180, d60)
        self.assertEqual(d180, responses_validation_configuration_digest(**base, maximum_timeout_seconds=180.0))
        for bad in (0, -1, True, "180"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValidationError, "configuration pin is incomplete"):
                responses_validation_configuration_digest(**base, maximum_timeout_seconds=bad)
        # The route reads the ceiling from its transport, so a transport
        # whose ceiling changed no longer matches a digest pinned before.
        def route(ceiling):
            transport = SimpleNamespace(response_meter=SimpleNamespace(enrollments={}), model=MODEL,
                                        api_url="https://api.openai.com/v1", _api_key="sk-test",
                                        maximum_timeout_seconds=ceiling)
            return OpenAIResponsesValidationRoute(transport=transport, total_token_limit=128,
                                                  expected_model=MODEL, meter_enrollment_digest="d" * 64,
                                                  expected_organization_id="org-1", expected_api_project_id="proj-1")
        self.assertNotEqual(route(60).configuration_digest, route(120).configuration_digest)
        transport = SimpleNamespace(response_meter=SimpleNamespace(enrollments={}), model=MODEL,
                                    api_url="https://api.openai.com/v1", _api_key="sk-test")
        with self.assertRaisesRegex(ValidationError, "transport configuration is incomplete"):
            OpenAIResponsesValidationRoute(transport=transport, total_token_limit=128, expected_model=MODEL,
                                           meter_enrollment_digest="d" * 64, expected_organization_id="org-1",
                                           expected_api_project_id="proj-1").compute_configuration_digest()

    def test_route_detects_configuration_drift_against_pin(self):
        transport = SimpleNamespace(
            response_meter=SimpleNamespace(enrollments={}),
            model=MODEL,
            api_url="https://api.openai.com/v1",
            _api_key="sk-test",
            maximum_timeout_seconds=60,
        )
        route = OpenAIResponsesValidationRoute(
            transport=transport,
            total_token_limit=128,
            expected_model=MODEL,
            meter_enrollment_digest="d" * 64,
            expected_organization_id="org-1",
            expected_api_project_id="proj-1",
            configuration_digest="c" * 64,
        )
        with self.assertRaisesRegex(ValidationError, "configuration changed during execution"):
            _ = route.configuration_digest

    def test_monetary_reservation_bounds_and_token_cap(self):
        authority = MagicMock()
        budget = SimpleNamespace(token_limit=50)
        authority.reserve.return_value = budget
        transport = SimpleNamespace(
            response_meter=SimpleNamespace(enrollments={}),
            model=MODEL,
            maximum_timeout_seconds=60,
        )
        route = OpenAIResponsesValidationRoute(
            transport=transport,
            total_token_limit=100,  # exceeds budget.token_limit of 50
            expected_model=MODEL,
            meter_enrollment_digest="d" * 64,
            budget_authority=authority,
            budget_authority_digest="a" * 64,
            canary_task_id="VALIDATION-task",
            canary_attempt_digest="sha256:" + "1" * 64,
        )
        connection = MagicMock()
        with self.assertRaisesRegex(ValidationError, "exceeds budget token limit"):
            route.reserve_monetary_admission(connection=connection)

    def test_monetary_reservation_refreshes_timeouts_immediately_before_commit(self):
        """Finding 5: the funding transaction's timeouts are re-derived before COMMIT.

        The pooled path binds statement/lock timeouts before the reservation
        statements run; those figures are stale by the time the transaction
        block exits and COMMIT waits on funding-schema locks. The last
        statement inside the transaction must be a fresh set_config from the
        time actually left, so a late COMMIT aborts under the real remainder.
        """
        events = []
        # Reads: pool acquire, first bind, post-reserve check, pre-COMMIT bind.
        remaining = iter([20.0, 20.0, 20.0, 0.75])

        class Conn:
            def execute(self, sql, params=None):
                events.append(("execute", sql, params))

            def transaction(self):
                conn = self

                class Tx:
                    def __enter__(self_):
                        events.append(("begin", None, None))
                        return conn

                    def __exit__(self_, *exc):
                        events.append(("commit", None, None))
                        return False
                return Tx()

        class Pool:
            def connection(self, timeout=None):
                pool_conn = Conn()

                class Ctx:
                    def __enter__(self_):
                        events.append(("acquire", timeout, None))
                        return pool_conn

                    def __exit__(self_, *exc):
                        return False
                return Ctx()

        authority = MagicMock()
        authority.entitlements = SimpleNamespace(pool=Pool())
        authority.reserve.side_effect = lambda conn, **kw: events.append(("reserve", None, None)) or SimpleNamespace(token_limit=500)
        transport = SimpleNamespace(response_meter=SimpleNamespace(enrollments={}), model=MODEL,
                                    maximum_timeout_seconds=60)
        route = OpenAIResponsesValidationRoute(
            transport=transport, total_token_limit=100, expected_model=MODEL,
            meter_enrollment_digest="d" * 64, budget_authority=authority,
            budget_authority_digest="a" * 64, canary_task_id="VALIDATION-task",
            canary_attempt_digest="sha256:" + "1" * 64)
        route.reserve_monetary_admission(remaining_seconds=lambda: next(remaining))
        kinds = [e[0] for e in events]
        self.assertEqual(["acquire", "begin", "execute", "reserve", "execute", "commit"], kinds)
        set_configs = [e for e in events if e[0] == "execute"]
        self.assertTrue(all("set_config('statement_timeout'" in e[1] for e in set_configs))
        # The refresh before COMMIT carries the smaller remainder, not the
        # figure bound before the reservation ran.
        self.assertEqual(("20000", "20000"), set_configs[0][2])
        self.assertEqual(("750", "750"), set_configs[1][2])
        self.assertEqual("commit", kinds[-1])

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


class ResponsesFactoryTests(unittest.TestCase):
    def test_factory_missing_required_keys_raises(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"model": "gpt-4o"}))
            path = f.name
        try:
            with self.assertRaisesRegex(ValidationError, "missing required keys"):
                responses_route_from_file(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_factory_relative_path_raises(self):
        with self.assertRaisesRegex(ValidationError, "absolute regular file"):
            responses_route_from_file("relative_config.json")


class ResponsesBridgeExecutionTests(unittest.TestCase):
    def test_bridge_service_passes_canary_identity_and_budget(self):
        work = work_packet()
        packet_bytes = canonical_json_bytes(work)

        transport = SimpleNamespace(
            model=MODEL,
            api_url="https://api.openai.com/v1",
            _api_key="sk-test",
            maximum_timeout_seconds=60,
            response_meter=SimpleNamespace(enrollments={}),
        )
        budget_authority = MagicMock()
        budget_authority.reserve.return_value = SimpleNamespace(token_limit=128)
        route = OpenAIResponsesValidationRoute(
            transport=transport,
            total_token_limit=128,
            expected_model=MODEL,
            meter_enrollment_digest="d" * 64,
            expected_organization_id=ORG_ID,
            expected_api_project_id=PROJ_ID,
            canary_task_id=work["task_id"],
            canary_assignment_id=work["assignment_id"],
            canary_attempt_digest=work["idempotency_key"],
            canary_work_packet_digest="sha256:" + digest(packet_bytes),
            budget_authority=budget_authority,
            budget_authority_digest="a" * 64,
        )
        config_digest = route.configuration_digest

        captured_context = []
        def mock_send(packet, context=None):
            captured_context.append(context)
            if context and context.execution_observer:
                bundle = genuine_responses_bundle()
                context.execution_observer("openai", canonical_json_bytes(bundle), None)

        transport.send = mock_send

        generation_hex = "c" * 64
        tree_hex = "e" * 40
        store = MagicMock()
        store.expected_configuration_digest.return_value = config_digest
        reservation = "b" * 64
        admission = {
            "reservation": reservation,
            "nonce": "n" * 64,
            "fence": 1,
            "generation": generation_hex,
            "provider": "openai",
            "candidate": "a" * 40,
            "tree": tree_hex,
            "expires_at": 1_800_000_000,
            # ProviderActivationBinding's actual seven-field contract; the
            # earlier model/account keys were never binding fields.
            "binding": {
                "provider_id": "openai",
                "workspace_id": "workspace",
                "provider_version": "v1",
                "app_version": "v1",
                "runtime_adapter_version": "v1",
                "workspace_version": "v1",
                "binding_version": "v1",
            },
            "configuration_digest": config_digest,
            "packet": packet_bytes,
            "owner_digest": "o" * 64,
        }
        store.admit.return_value = admission
        store.settle.return_value = "evidence_digest_xyz"

        service = ValidationBridgeService(
            store=store,
            production_executor=MagicMock(),
            capture_key="bridge-fixture-" + "k" * 40,
            owner_token="owner-fixture-" + "o" * 40,
            account_files={},
            openai_responses_route=route,
        )

        res = service._run_openai_responses(
            ValidationRequest(generation=generation_hex, provider="openai"),
            service.owner_token,
        )

        # Exactly one admission, one claim and one settlement; the fence calls
        # are read-only and share the operation deadline.
        store.admit.assert_called_once_with(
            ValidationRequest(generation=generation_hex, provider="openai"),
            service.owner_token,
            preserve_canary_identity=True,
            remaining_seconds=ANY,
        )
        self.assertEqual(1, store.claim_execution.call_count)
        self.assertEqual(1, store.settle.call_count)
        self.assertEqual(len(captured_context), 1)
        ctx = captured_context[0]
        self.assertEqual(ctx.task_id, work["task_id"])
        self.assertEqual(ctx.assignment_id, work["assignment_id"])
        self.assertEqual(ctx.idempotency_key, work["idempotency_key"])
        self.assertIsNotNone(ctx.deadline)
        self.assertEqual(ctx.total_token_limit, 128)
        self.assertEqual(res["transport"], "responses-api")
        self.assertEqual(res["evidence_digest"], "evidence_digest_xyz")
        self.assertEqual(res["expires_at"], 1_800_000_000)

    def _seven_field_binding(self):
        return {
            "provider_id": "openai",
            "workspace_id": "workspace",
            "provider_version": "v1",
            "app_version": "v1",
            "runtime_adapter_version": "v1",
            "workspace_version": "v1",
            "binding_version": "v1",
        }

    def _mocked_responses_bridge(self, *, admission_overrides=None):
        work = work_packet()
        packet_bytes = canonical_json_bytes(work)
        transport = MagicMock()
        transport.model = MODEL
        transport.api_url = "https://api.openai.com/v1"
        transport._api_key = "sk-test"
        transport.maximum_timeout_seconds = 60
        transport.response_meter = SimpleNamespace(enrollments={})
        budget_authority = MagicMock()
        budget_authority.reserve.return_value = SimpleNamespace(token_limit=128)
        route = OpenAIResponsesValidationRoute(
            transport=transport,
            total_token_limit=128,
            expected_model=MODEL,
            meter_enrollment_digest="d" * 64,
            expected_organization_id=ORG_ID,
            expected_api_project_id=PROJ_ID,
            canary_task_id=work["task_id"],
            canary_assignment_id=work["assignment_id"],
            canary_attempt_digest=work["idempotency_key"],
            canary_work_packet_digest="sha256:" + digest(packet_bytes),
            budget_authority=budget_authority,
            budget_authority_digest="a" * 64,
        )
        admission = {
            "reservation": "b" * 64,
            "nonce": "n" * 64,
            "fence": 1,
            "generation": "c" * 64,
            "provider": "openai",
            "candidate": "a" * 40,
            "tree": "e" * 40,
            "expires_at": 1_800_000_000,
            "binding": self._seven_field_binding(),
            "configuration_digest": route.configuration_digest,
            "packet": packet_bytes,
            "owner_digest": "o" * 64,
        }
        admission.update(admission_overrides or {})
        store = MagicMock()
        store.expected_configuration_digest.return_value = route.configuration_digest
        store.admit.return_value = admission
        store.settle.return_value = "evidence_digest_xyz"
        service = ValidationBridgeService(
            store=store,
            production_executor=MagicMock(),
            capture_key="bridge-fixture-" + "k" * 40,
            owner_token="owner-fixture-" + "o" * 40,
            account_files={},
            openai_responses_route=route,
        )
        return transport, route, admission, store, service

    def test_configuration_digest_drift_refuses_before_transport_http(self):
        transport, route, admission, store, service = self._mocked_responses_bridge(
            admission_overrides={"configuration_digest": "f" * 64})
        with self.assertRaisesRegex(ValidationError, "digest drift"):
            service._run_openai_responses(
                ValidationRequest(generation=admission["generation"], provider="openai"),
                service.owner_token)
        transport.send.assert_not_called()
        store.settle.assert_not_called()
        store.fail.assert_called_once_with(admission["reservation"], remaining_seconds=ANY)

    def test_registered_digest_drift_refuses_before_admission(self):
        transport, route, admission, store, service = self._mocked_responses_bridge()
        store.expected_configuration_digest.return_value = "f" * 64
        with self.assertRaisesRegex(ValidationError, "digest drift"):
            service._run_openai_responses(
                ValidationRequest(generation=admission["generation"], provider="openai"),
                service.owner_token)
        # Read-only comparison under the shared deadline; nothing is admitted or reserved.
        store.expected_configuration_digest.assert_called_once_with(
            admission["generation"], "openai", remaining_seconds=ANY)
        store.admit.assert_not_called()
        store.fail.assert_not_called()
        transport.send.assert_not_called()

    def test_expired_whole_operation_deadline_never_reaches_http_or_settlement(self):
        transport, route, admission, store, service = self._mocked_responses_bridge()
        # The single start reading budgets the whole operation; every later
        # clock reading observes an already-spent deadline. Admission may have
        # happened, but no HTTP, capture or settlement may follow it, and the
        # durable reconciliation marker is still written.
        readings = iter([100.0])
        def fake_monotonic():
            return next(readings, 1_000.0)
        with patch("director_orchestrator.provider_validation.time.monotonic", fake_monotonic):
            with self.assertRaisesRegex(ValidationError, "timeout"):
                service._run_openai_responses(
                    ValidationRequest(generation=admission["generation"], provider="openai"),
                    service.owner_token)
        self.assertEqual(1, store.admit.call_count)
        transport.send.assert_not_called()
        store.settle.assert_not_called()
        store.fail.assert_called_once_with(admission["reservation"], remaining_seconds=ANY)

    def test_packet_ceiling_is_measured_from_operation_start_not_admission(self):
        transport, route, admission, store, service = self._mocked_responses_bridge()
        seen = []
        def send(packet, context=None):
            seen.append(context)
            raise RuntimeError("stop-after-context")
        transport.send = send
        # Admission appears to consume ten seconds; the canary packet's own 30
        # second ceiling is still measured from the shared start (130), so a
        # post-admission reading can never extend the whole-operation budget.
        readings = iter([100.0])
        def fake_monotonic():
            return next(readings, 110.0)
        with patch("director_orchestrator.provider_validation.time.monotonic", fake_monotonic):
            with self.assertRaisesRegex(RuntimeError, "stop-after-context"):
                service._run_openai_responses(
                    ValidationRequest(generation=admission["generation"], provider="openai"),
                    service.owner_token)
        self.assertEqual(130.0, seen[0].deadline)
        self.assertEqual(1, store.admit.call_count)
        store.settle.assert_not_called()

    def test_transport_refuses_expired_deadline_before_any_http(self):
        from director_orchestrator.primary_transports import OpenAIResponsesPacketTransport
        import time as time_module
        transport = OpenAIResponsesPacketTransport(api_key="sk-test", model=MODEL)
        context = SimpleNamespace(fencing_generation=1, timeout_seconds=30,
                                  total_token_limit=1000,
                                  deadline=time_module.monotonic() - 1)
        with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                   side_effect=AssertionError("expired deadline must not perform HTTP")):
            with self.assertRaisesRegex(RuntimeError, "timeout is exhausted"):
                transport.send(canonical_json_bytes(work_packet()), context=context)

    def test_unpinned_canary_and_missing_funding_refuse_before_http(self):
        transport = SimpleNamespace(
            model=MODEL,
            api_url="https://api.openai.com/v1",
            _api_key="sk-test",
            maximum_timeout_seconds=60,
            response_meter=SimpleNamespace(enrollments={}),
        )
        unpinned = OpenAIResponsesValidationRoute(
            transport=transport,
            total_token_limit=128,
            expected_model=MODEL,
            meter_enrollment_digest="d" * 64,
        )
        with self.assertRaisesRegex(ValidationError, "canary identity is not fully pinned"):
            unpinned.validate_canary(work_packet(), canonical_json_bytes(work_packet()))
        _, route, _, _, _ = self._mocked_responses_bridge()
        route.budget_authority = None
        with self.assertRaisesRegex(ValidationError, "monetary funding"):
            route.reserve_monetary_admission()


if __name__ == "__main__":
    unittest.main()
# [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 18:30 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 23:04 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | END]]
