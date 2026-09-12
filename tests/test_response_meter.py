# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | BEGIN]]
"""Synthetic tariffs, real PostgreSQL authority; no external calls or enrollment."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx

from director_orchestrator.antigravity_team import ProviderUsageReceipt, TeamAuthorizationError
from director_orchestrator.entitlement_contracts import canonical_json_bytes
from director_orchestrator.primary_transports import OpenAIResponsesPacketTransport
from director_orchestrator.response_meter import EnrolledResponseMeter, ResponseMeterEnrollment, ResponseMeterRegistry
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

# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | END]]
