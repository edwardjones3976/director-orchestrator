# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | BEGIN]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:16 UTC | BEGIN]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:07 UTC | BEGIN]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | BEGIN]]
"""Synthetic tariffs, real PostgreSQL authority; no external calls or enrollment."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from time import monotonic
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx

from director_orchestrator.antigravity_team import ProviderUsageReceipt, TeamAuthorizationError
from director_orchestrator.entitlement_contracts import canonical_json_bytes
from director_orchestrator.primary_transports import OpenAIResponsesPacketTransport
from director_orchestrator.config import Settings
from director_orchestrator.primary_default import build_automatic_primary_live_dependencies
from director_orchestrator.antigravity_team_routing import PrimaryProviderBudgetRouting
from director_orchestrator.response_meter import EnrolledResponseMeter, ResponseMeterEnrollment, ResponseMeterRegistry, load_response_meter_registry
from tests import test_antigravity_team as team


class ResponseMeterTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: team.TeamPostgresTests.setUpClass())
    tearDownClass = classmethod(lambda cls: team.TeamPostgresTests.tearDownClass())
    tearDown = team.TeamPostgresTests.tearDown

    def setUp(self):
        team.TeamPostgresTests.setUp(self)
        self.now = datetime.now(timezone.utc)
        self.attempt = "sha256:" + "3" * 64
        self.budget = team.budget(self.g, grant_digest="b" * 64, provider_id="openai")
        self.budget = team.signed(self.budget, team.fixtures.OWNER_SECRET)
        with self.store._pool.connection() as c, c.transaction():
            self.authority.budgets.register(c, self.budget)
            self.authority.budgets.reserve(c, authority_digest=self.budget.grant_digest,
                provider="openai", task_id=self.g.task_id, attempt_digest=self.attempt)
        work = json.loads((Path(__file__).resolve().parents[1] /
            "docs/schemas/model-work-packet-v1.example.json").read_text())
        work.update(task_id=self.g.task_id, assignment_id=self.g.assignment_id, project=self.g.project_id)
        self.packet = json.dumps(work).encode()
        digest = "sha256:" + hashlib.sha256(self.packet).hexdigest()
        self.context = SimpleNamespace(task_id=self.g.task_id, assignment_id=self.g.assignment_id,
            idempotency_key=self.attempt,
            work_packet_digest=digest, fencing_generation=1, timeout_seconds=5, total_token_limit=1000)
        self.enrollment = team.signed(ResponseMeterEnrollment(
            schema_version="responses-meter-enrollment/v1", authority_digest=self.budget.grant_digest,
            attempt_digest=self.attempt, task_id=self.g.task_id, assignment_id=self.g.assignment_id,
            project_id=self.g.project_id, work_packet_digest=digest,
            credential_digest=hashlib.sha256(b"test-key").hexdigest(), organization_id="org-test",
            api_project_id="proj-test", model="synthetic-model-snapshot", service_tier="default",
            origin="https://api.openai.com", tariff_id="synthetic-test-only",
            input_microusd_per_million=4_000_000, cached_input_microusd_per_million=1_000_000,
            output_microusd_per_million=8_000_000, valid_from=self.now-timedelta(seconds=10),
            expires_at=self.now+timedelta(minutes=1), owner_signature="0"*64), team.fixtures.OWNER_SECRET)
        self.meter = self.meter_for(self.enrollment)
        self.headers = {"openai-organization":"org-test", "openai-project":"proj-test", "x-request-id":"req-one"}

    def meter_for(self, enrollment):
        return EnrolledResponseMeter(enrollment=enrollment, authority=self.authority.budgets,
            pool=self.store._pool, clock=lambda: self.now)

    def native(self):
        return dict(object="response", id="resp-one", created_at=int(self.now.timestamp()),
            status="completed", model=self.enrollment.model, service_tier="default",
            output=[dict(type="message", role="assistant", status="completed",
                content=[dict(type="output_text", text="A synthetic result.")])],
            usage=dict(input_tokens=8, output_tokens=4, total_tokens=12,
                input_tokens_details=dict(cached_tokens=2), output_tokens_details=dict(reasoning_tokens=3)))

    def count_rows(self):
        with self.store._pool.connection() as c:
            return c.execute(f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_usage").fetchone()["n"]

    def test_tariff_semantics_durable_provenance_and_exact_replay(self):
        receipt = self.meter.settle(self.native(), self.headers)
        self.assertEqual(58, receipt.actual_microusd)  # 6*4 + 2*1 + 4*8; reasoning not added again.
        self.assertEqual(12, receipt.observed_tokens)
        self.assertEqual("signed-tariff-metered-not-invoice", receipt.metering_evidence.cost_basis)
        with self.store._pool.connection() as c:
            document = c.execute(f"SELECT document FROM {self.schema}.director_primary_provider_usage").fetchone()["document"]
        self.assertEqual("resp-one", document["metering_evidence"]["response_id"])
        self.assertNotIn("A synthetic result", json.dumps(document))
        self.assertNotIn("test-key", json.dumps(document))
        self.assertEqual(self.meter.settle(self.native(), self.headers), receipt)
        self.assertEqual(1, self.count_rows())
        different = self.native(); different["id"] = "resp-different-same-totals"
        with self.assertRaisesRegex(TeamAuthorizationError, "conflicting"):
            self.meter.settle(different, self.headers)

    def test_concurrent_identical_receipts_insert_once(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(lambda _: self.meter.settle(self.native(), self.headers), range(2)))
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(1, self.count_rows())

    def test_same_provider_response_cannot_charge_two_attempts(self):
        self.meter.settle(self.native(),self.headers)
        second="sha256:"+"5"*64
        # A second small reservation under the same unchanged 100 micro-USD ceiling.
        second_budget=team.signed(self.budget.model_copy(update={"grant_digest":"c"*64,
            "attempt_reservation_microusd":1}),team.fixtures.OWNER_SECRET)
        with self.store._pool.connection() as c,c.transaction():
            self.authority.budgets.register(c,second_budget)
            self.authority.budgets.reserve(c,authority_digest=second_budget.grant_digest,
                provider="openai",task_id=self.g.task_id,attempt_digest=second)
        enrollment=team.signed(self.enrollment.model_copy(update={"attempt_digest":second,
            "authority_digest":second_budget.grant_digest}),team.fixtures.OWNER_SECRET)
        with self.assertRaisesRegex(TeamAuthorizationError,"another attempt"):
            self.meter_for(enrollment).settle(self.native(),self.headers)
        self.assertEqual(1,self.count_rows())

    def test_account_model_tier_freshness_and_unknown_token_details_hold(self):
        for field, value in (("model","unapproved"), ("service_tier","priority"),
                ("status","incomplete"), ("created_at",0)):
            native=self.native(); native[field]=value
            with self.subTest(field=field), self.assertRaises(TeamAuthorizationError):
                self.meter.settle(native, self.headers)
        for detail, value in (("input_tokens_details", {}), ("input_tokens_details", {"cached_tokens":9}),
                ("output_tokens_details", {"reasoning_tokens":5}), ("output_tokens_details", {"reasoning_tokens":True})):
            native=self.native(); native["usage"][detail]=value
            with self.subTest(detail=detail,value=value), self.assertRaises(TeamAuthorizationError):
                self.meter.settle(native, self.headers)
        for headers in ({}, dict(self.headers, **{"openai-project":"wrong"})):
            with self.assertRaises(TeamAuthorizationError): self.meter.settle(self.native(), headers)
        self.assertEqual(0,self.count_rows())

    def test_invalid_enrollment_and_wrong_reservation_never_settle(self):
        for update in ({"owner_signature":"0"*64}, {"expires_at":self.now-timedelta(seconds=1)},
                {"attempt_digest":"sha256:"+"f"*64}, {"authority_digest":"f"*64}):
            enrollment=self.enrollment.model_copy(update=update)
            if "owner_signature" not in update: enrollment=team.signed(enrollment,team.fixtures.OWNER_SECRET)
            with self.subTest(update=update),self.assertRaises(TeamAuthorizationError):
                self.meter_for(enrollment).settle(self.native(),self.headers)
        self.assertEqual(0,self.count_rows())

    def test_real_transport_hook_uses_only_authenticated_envelope(self):
        requests=[]
        def respond(request):
            requests.append(request)
            body={"object":"response.input_tokens","input_tokens":8} if request.url.path.endswith("input_tokens") else self.native()
            return httpx.Response(200,json=body,headers=self.headers)
        original=httpx.AsyncClient
        registry=ResponseMeterRegistry(enrollments=(self.enrollment,),authority=self.authority.budgets,pool=self.store._pool)
        with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                side_effect=lambda **kw: original(transport=httpx.MockTransport(respond),**kw)):
            result=OpenAIResponsesPacketTransport(api_key="test-key",model=self.enrollment.model,
                response_meter=registry).send(self.packet,context=self.context)
        self.assertEqual(2,len(requests))
        self.assertTrue(all(r.headers["OpenAI-Project"]=="proj-test" for r in requests))
        self.assertEqual("default",json.loads(requests[1].content)["service_tier"])
        self.assertIsNone(result["usage"]["cost_usd"])
        self.assertEqual(1,self.count_rows())

    def test_registry_has_no_fallback_and_resolves_distinct_attempt_instances(self):
        registry=ResponseMeterRegistry(enrollments=(self.enrollment,),authority=self.authority.budgets,pool=self.store._pool)
        self.assertIsNot(registry.for_context(self.context),registry.for_context(self.context))
        with self.assertRaises(TeamAuthorizationError):
            registry.for_context(SimpleNamespace(idempotency_key="sha256:"+"f"*64))
        with self.assertRaises(TeamAuthorizationError):
            ResponseMeterRegistry(enrollments=(self.enrollment,self.enrollment),authority=self.authority.budgets,pool=self.store._pool)
        with self.assertRaises(TypeError):registry.enrollments[self.attempt]=self.enrollment

    def test_startup_registry_loader_requires_bounded_signed_current_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"enrollments.json"
            path.write_text(json.dumps([self.enrollment.model_dump(mode="json")]))
            registry=load_response_meter_registry(path,authority=self.authority.budgets,pool=self.store._pool)
            self.assertEqual(self.enrollment,registry.for_context(self.context).enrollment)
            for contents in ('[]', '[{"model":"one","model":"two"}]', 'x'*1_048_577,
                    json.dumps([self.enrollment.model_copy(update={"owner_signature":"0"*64}).model_dump(mode="json")])):
                path.write_text(contents)
                with self.subTest(size=len(contents)),self.assertRaises(ValueError):
                    load_response_meter_registry(path,authority=self.authority.budgets,pool=self.store._pool)

    def test_actual_runtime_factory_loads_explicit_registry_and_default_is_off(self):
        self.assertEqual("",Settings().langgraph_primary_openai_response_meter_enrollment_file)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"enrollments.json"
            path.write_text(json.dumps([self.enrollment.model_dump(mode="json")]))
            settings=replace(Settings(),langgraph_primary_antigravity_team_enabled=False,
                langgraph_primary_openai_response_meter_enrollment_file=str(path))
            transport=OpenAIResponsesPacketTransport(api_key="test-key",model=self.enrollment.model)
            self.assertIsNone(transport.response_meter)
            routing=PrimaryProviderBudgetRouting(authority=self.authority.budgets,pool=self.store._pool)
            providers=("openai","claude","cursor")
            adapters={provider:object() for provider in providers}
            capabilities={provider:SimpleNamespace(transport_kind="responses-api" if provider=="openai" else "test") for provider in providers}
            with patch("director_orchestrator.primary_default.discover_local_primary_command_transports",return_value=({"openai":transport},capabilities)), \
                 patch("director_orchestrator.primary_default.build_graph_native_packet_adapters",return_value=adapters), \
                 patch("director_orchestrator.primary_default.build_primary_packet_result_resolver",return_value=lambda _:None), \
                 patch("director_orchestrator.antigravity_team_routing.configured_provider_budget_routing",return_value=routing):
                dependencies=build_automatic_primary_live_dependencies(settings,None,None)
            self.assertIs(dependencies.budget_routing,routing)
            self.assertEqual(self.enrollment,transport.response_meter.for_context(self.context).enrollment)

    def test_wrong_origin_or_credential_rejected_before_http(self):
        for changes in ({"api_url":"http://api.openai.com/v1/responses"}, {"api_key":"wrong"}):
            args=dict(api_key="test-key",model=self.enrollment.model,response_meter=self.meter);args.update(changes)
            with patch("director_orchestrator.primary_transports.httpx.AsyncClient") as client:
                with self.assertRaises(TeamAuthorizationError):
                    OpenAIResponsesPacketTransport(**args).send(self.packet,context=self.context)
                client.assert_not_called()

    def test_complete_overrun_is_recorded_before_output_rejection(self):
        native=self.native()
        native["usage"].update(input_tokens=8,output_tokens=1001,total_tokens=1009)
        def respond(request):
            body={"object":"response.input_tokens","input_tokens":8} if request.url.path.endswith("input_tokens") else native
            return httpx.Response(200,json=body,headers=self.headers)
        original=httpx.AsyncClient
        with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                side_effect=lambda **kw: original(transport=httpx.MockTransport(respond),**kw)):
            with self.assertRaisesRegex(RuntimeError,"usage.*violates"):
                OpenAIResponsesPacketTransport(api_key="test-key",model=self.enrollment.model,
                    response_meter=self.meter).send(self.packet,context=self.context)
        self.assertEqual(1,self.count_rows())
        with self.assertRaisesRegex(TeamAuthorizationError,"token limit violation"):
            with self.store._pool.connection() as c,c.transaction():
                self.authority.budgets.reserve(c,authority_digest=self.budget.grant_digest,
                    provider="openai",task_id=self.g.task_id,attempt_digest="sha256:"+"4"*64)

    def test_unknown_tariff_and_model_authored_usage_cannot_settle(self):
        values=self.enrollment.model_dump()
        values.pop("input_microusd_per_million")
        with self.assertRaises(ValueError):ResponseMeterEnrollment(**values)
        native=self.native()
        native["output"][0]["content"][0]["text"]=json.dumps({"usage":native.pop("usage")})
        with self.assertRaisesRegex(TeamAuthorizationError,"usage missing"):
            self.meter.settle(native,self.headers)
        self.assertEqual(0,self.count_rows())

    def test_old_receipt_signing_bytes_remain_unchanged(self):
        fields=dict(schema_version="primary-provider-usage/v1",attempt_digest=self.attempt,
            authority_digest=self.budget.grant_digest,provider_id="openai",actual_microusd=0,
            observed_tokens=0,complete=True)
        signature=hmac.new(team.fixtures.ROUTE_SECRET,canonical_json_bytes(fields),hashlib.sha256).hexdigest()
        receipt=ProviderUsageReceipt(**fields,meter_signature=signature)
        self.assertEqual(canonical_json_bytes(fields),canonical_json_bytes(receipt.signing_document()))
        with self.store._pool.connection() as c,c.transaction():self.authority.budgets.record_usage(c,receipt)
        self.assertEqual(1,self.count_rows())

    def test_database_contention_obeys_attempt_deadline_and_retains_reservation(self):
        for phase in ("prepare", "settle"):
            with self.subTest(phase=phase):
                requests=[]
                def respond(request):
                    requests.append(request)
                    body={"object":"response.input_tokens","input_tokens":8} if request.url.path.endswith("input_tokens") else self.native()
                    return httpx.Response(200,json=body,headers=self.headers)
                context=SimpleNamespace(**vars(self.context));context.timeout_seconds=1
                original=httpx.AsyncClient
                with self.store._pool.connection() as blocker,blocker.transaction():
                    if phase=="prepare":
                        blocker.execute(f"LOCK TABLE {self.schema}.director_primary_provider_spend IN ACCESS EXCLUSIVE MODE")
                    else:
                        blocker.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            ("primary-provider-budget:"+self.g.project_id,))
                    with patch.object(self.store._pool,"connection",wraps=self.store._pool.connection) as acquisition, \
                         patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                            side_effect=lambda **kw:original(transport=httpx.MockTransport(respond),**kw)):
                        started=monotonic()
                        with self.assertRaisesRegex(RuntimeError,"deadline|timeout"):
                            OpenAIResponsesPacketTransport(api_key="test-key",model=self.enrollment.model,
                                response_meter=self.meter).send(self.packet,context=context)
                        elapsed=monotonic()-started
                        self.assertLess(elapsed,2.5)
                        self.assertTrue(all(0 < call.kwargs["timeout"] <= 1 for call in acquisition.call_args_list))
                self.assertEqual(0 if phase=="prepare" else 2,len(requests))
                self.assertEqual(0,self.count_rows())
                with self.store._pool.connection() as c:
                    reservation=c.execute(f"SELECT reserved_microusd FROM {self.schema}.director_primary_provider_spend WHERE attempt_digest=%s",(self.attempt,)).fetchone()
                self.assertEqual(self.budget.attempt_reservation_microusd,reservation["reserved_microusd"])

    def test_responses_validation_bridge_runs_configured_factory_reservation_and_capture(self):
        """Real configured factory, store, meter and budget; only HTTP is mocked.

        Requires the documented disposable Windows PostgreSQL lane through the
        inherited team fixtures.  The entitlement fixtures supply the DSN and
        secret files, so responses_route_from_file builds its own real budget
        authority and pool from on-disk configuration, and the route derives its
        canary pins from the single enrolled admission.  setUp for this test
        only registers the owner-signed canary budget; the bridge performs the
        single authoritative reservation through
        route.reserve_monetary_admission.  Proposed by this tools-disabled
        repair; NOT executed by it.
        """
        import secrets
        import time
        from dataclasses import asdict
        from director_orchestrator.openai_responses_validation import responses_route_from_file
        from director_orchestrator.provider_activation import ProviderActivationBinding
        from director_orchestrator.provider_validation import (
            CaptureVerifier,
            PostgresValidationStore,
            ValidationBridgeService,
            ValidationError,
            ValidationRequest,
        )

        attempt = "sha256:" + "4" * 64
        task_id = "VALIDATION-meter-canary"
        budget = team.signed(team.budget(self.g, grant_digest="d" * 64, provider_id="openai",
            task_id=task_id, attempt_reservation_microusd=30), team.fixtures.OWNER_SECRET)
        with self.store._pool.connection() as c, c.transaction():
            self.authority.budgets.register(c, budget)
        work = json.loads((Path(__file__).resolve().parents[1] /
            "docs/schemas/model-work-packet-v1.example.json").read_text())
        work.update(task_id=task_id, assignment_id=task_id, starting_ref="a" * 40,
            allowed_tools=[], allowed_external_effects=[], idempotency_key=attempt)
        work["limits"].update(max_hops=1, max_revisions=0, timeout_seconds=30)
        packet = canonical_json_bytes(work)
        packet_digest = "sha256:" + hashlib.sha256(packet).hexdigest()
        enrollment = team.signed(ResponseMeterEnrollment(
            schema_version="responses-meter-enrollment/v1", authority_digest=budget.grant_digest,
            attempt_digest=attempt, task_id=task_id, assignment_id=task_id,
            project_id=self.g.project_id, work_packet_digest=packet_digest,
            credential_digest=hashlib.sha256(b"test-key").hexdigest(), organization_id="org-test",
            api_project_id="proj-test", model="synthetic-model-snapshot", service_tier="default",
            origin="https://api.openai.com", tariff_id="synthetic-test-only",
            input_microusd_per_million=4_000_000, cached_input_microusd_per_million=1_000_000,
            output_microusd_per_million=8_000_000, valid_from=self.now - timedelta(seconds=10),
            expires_at=self.now + timedelta(minutes=5), owner_signature="0" * 64),
            team.fixtures.OWNER_SECRET)
        native = dict(object="response", id="resp-validation-canary",
            created_at=int(self.now.timestamp()), status="completed", model=enrollment.model,
            service_tier="default",
            output=[dict(type="message", role="assistant", status="completed",
                content=[dict(type="output_text", text="Validation canary captured.")])],
            usage=dict(input_tokens=8, output_tokens=4, total_tokens=12,
                input_tokens_details=dict(cached_tokens=2),
                output_tokens_details=dict(reasoning_tokens=3)))
        headers = {"openai-organization": "org-test", "openai-project": "proj-test",
                   "x-request-id": "req-validation-canary"}
        requests = []
        def respond(request):
            requests.append(request)
            body = ({"object": "response.input_tokens", "input_tokens": 8}
                    if request.url.path.endswith("input_tokens") else native)
            return httpx.Response(200, json=body, headers=headers)
        original = httpx.AsyncClient
        binding = asdict(ProviderActivationBinding(
            "openai", "workspace", "v1", "v1", "v1", "v1", "v1"))
        owner = "owner-fixture-" + "o" * 40
        capture_key = "bridge-fixture-" + "k" * 40
        with tempfile.TemporaryDirectory() as directory:
            enrollment_path = Path(directory) / "enrollments.json"
            enrollment_path.write_text(json.dumps([enrollment.model_dump(mode="json")]))
            config_path = Path(directory) / "responses-validation.json"
            config_path.write_text(json.dumps({
                "model": enrollment.model,
                "api_url": "https://api.openai.com/v1/responses",
                "total_token_limit": 1000,
                "api_key": "test-key",
                "enrollment_file": str(enrollment_path.resolve()),
                "entitlements_postgres_dsn": team.fixtures.TEST_DSN,
                "entitlements_schema": self.schema,
                "primary_postgres_schema": self.schema,
                "trusted_verifier_secret_file": team.fixtures.VERIFIER_SECRET_FILE,
                "route_verifier_secret_file": team.fixtures.ROUTE_SECRET_FILE,
                "owner_authority_secret_file": team.fixtures.OWNER_SECRET_FILE,
                "allow_insecure_local_postgres": True,
                "maximum_timeout_seconds": 60,
            }))
            route = responses_route_from_file(config_path)
            meter_pool = route.transport.response_meter.pool
            try:
                store = PostgresValidationStore(team.fixtures.TEST_DSN)
                store.bootstrap()
                generation = secrets.token_hex(32)
                store.create_generation(generation=generation, candidate="a" * 40, tree="b" * 40,
                    owner_token=owner, expires_at=int(time.time()) + 600, budget_units=1,
                    lanes={"openai": {"binding": binding, "configuration_digest": route.configuration_digest,
                                      "cost_units": 1, "work_packet": work}})
                service = ValidationBridgeService(store=store, production_executor=None,
                    capture_key=capture_key, owner_token=owner, account_files={},
                    openai_responses_route=route)
                request = ValidationRequest(generation=generation, provider="openai")
                verifier = CaptureVerifier(store, capture_key=capture_key, candidate="a" * 40,
                    generation=generation, openai_responses_route=route)
                with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                        side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
                    result = service.run(request, owner)
                self.assertEqual(2, len(requests))
                self.assertTrue(all(r.headers["OpenAI-Project"] == "proj-test" for r in requests))
                self.assertEqual("default", json.loads(requests[1].content)["service_tier"])
                self.assertEqual("responses-api", result["transport"])
                self.assertTrue(verifier.verify(
                    ProviderActivationBinding(**binding), result["evidence_digest"]))
                # Exactly one authoritative reservation, made by the bridge.
                with self.store._pool.connection() as c:
                    spend = c.execute(f"SELECT count(*) AS n, reserved_microusd "
                        f"FROM {self.schema}.director_primary_provider_spend "
                        "WHERE attempt_digest=%s GROUP BY reserved_microusd", (attempt,)).fetchone()
                self.assertIsNotNone(spend)
                self.assertEqual((1, 30), (spend["n"], spend["reserved_microusd"]))
                self.assertEqual(1, self.count_rows())
                # Durable no-replay refusal for the same admitted attempt.
                with self.assertRaisesRegex(ValidationError, "already admitted"):
                    service.run(request, owner)
            finally:
                meter_pool.close()

# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | END]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:07 UTC | END]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:16 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 05:12 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]]
