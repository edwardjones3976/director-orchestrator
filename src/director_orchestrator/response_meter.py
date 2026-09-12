# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | BEGIN]]
"""Opt-in Responses metering; signed tariffs are not invoice-charge evidence.

Only protected admission may construct this adapter. No configuration discovery,
credential loading, provider call, or enrollment is performed here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .antigravity_team import (
    ClosedModel, Digest, Identifier, PrimaryProviderBudgetAuthority, ProviderBudget, ProviderUsageReceipt,
    TeamAuthorizationError,
)
from .entitlement_contracts import canonical_json_bytes, document_digest


class ResponseMeterEnrollment(ClosedModel):
    """Owner-signed, single-attempt tariff and credential/account binding."""

    schema_version: Literal["responses-meter-enrollment/v1"]
    authority_digest: Digest
    attempt_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    project_id: Identifier
    task_id: Identifier
    assignment_id: Identifier
    work_packet_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    credential_digest: Digest
    organization_id: Identifier
    api_project_id: Identifier
    model: Identifier
    service_tier: Literal["default", "priority", "flex"]
    origin: Literal["https://api.openai.com"]
    tariff_id: Identifier
    input_microusd_per_million: Annotated[int, Field(ge=0)]
    cached_input_microusd_per_million: Annotated[int, Field(ge=0)]
    output_microusd_per_million: Annotated[int, Field(ge=0)]
    valid_from: datetime
    expires_at: datetime
    owner_signature: Digest

    @model_validator(mode="after")
    def valid_interval(self):
        if (self.valid_from.tzinfo is None or self.expires_at.tzinfo is None
                or self.expires_at <= self.valid_from):
            raise ValueError("meter enrollment requires an aware finite validity interval")
        return self

    def signing_document(self):
        return self.model_dump(mode="json", exclude={"owner_signature"})


class EnrolledResponseMeter:
    """Meter only this enrolled attempt into the existing monetary authority.

    Explicit request account headers are routing intentions, not account proof.
    Settlement additionally requires matching provider-returned account headers.
    If the provider omits those headers, the reservation stays charged.
    """

    def __init__(self, *, enrollment: ResponseMeterEnrollment, authority, pool, clock=None):
        if not isinstance(authority, PrimaryProviderBudgetAuthority) or pool is not authority.entitlements._pool:
            raise TeamAuthorizationError("response meter requires the existing authority and pool")
        self.enrollment, self.authority, self.pool = enrollment, authority, pool
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _verify(self):
        e = self.enrollment
        expected = hmac.new(self.authority.entitlements._owner_authority_secret,
            canonical_json_bytes(e.signing_document()), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, e.owner_signature):
            raise TeamAuthorizationError("response meter enrollment signature invalid")
        now = self.clock()
        if now.tzinfo is None or not e.valid_from <= now < e.expires_at:
            raise TeamAuthorizationError("response meter enrollment is not current")

    def _reservation(self, connection):
        e = self.enrollment
        row = connection.execute(f"SELECT grant_digest,provider_id,project_id,task_id "
            f"FROM {self.authority.schema}.director_primary_provider_spend WHERE attempt_digest=%s",
            (e.attempt_digest,)).fetchone()
        if row is None or tuple(row[k] for k in (
                "grant_digest", "provider_id", "project_id", "task_id")) != (
                e.authority_digest, "openai", e.project_id, e.task_id):
            raise TeamAuthorizationError("response meter reservation binding mismatch")
        record = connection.execute(f"SELECT document FROM {self.authority.schema}.director_primary_provider_budget "
            "WHERE authority_digest=%s", (e.authority_digest,)).fetchone()
        if record is None:
            raise TeamAuthorizationError("response meter budget missing")
        budget = ProviderBudget.model_validate_json(json.dumps(record["document"]))
        budget.verify(self.authority.entitlements._owner_authority_secret)
        if (budget.grant_digest, budget.provider_id, budget.project_id, budget.task_id) != (
                e.authority_digest, "openai", e.project_id, e.task_id):
            raise TeamAuthorizationError("response meter budget binding mismatch")

    def prepare(self, *, context, packet: bytes, api_url: str, model: str, api_key: str):
        self._verify()
        e = self.enrollment
        if (api_url != e.origin + "/v1/responses" or model != e.model
                or hashlib.sha256(api_key.encode()).hexdigest() != e.credential_digest
                or context.task_id != e.task_id or context.assignment_id != e.assignment_id
                or context.idempotency_key != e.attempt_digest
                or context.work_packet_digest != e.work_packet_digest
                or "sha256:" + hashlib.sha256(packet).hexdigest() != e.work_packet_digest):
            raise TeamAuthorizationError("response meter request binding mismatch")
        with self.pool.connection() as connection, connection.transaction():
            self._reservation(connection)
        return {"OpenAI-Organization": e.organization_id, "OpenAI-Project": e.api_project_id}

    def settle(self, native, response_headers):
        self._verify()
        e = self.enrollment
        headers = {str(k).lower(): v for k, v in response_headers.items()}
        if (headers.get("openai-organization") != e.organization_id
                or headers.get("openai-project") != e.api_project_id
                or not isinstance(headers.get("x-request-id"), str)
                or not headers["x-request-id"].strip()):
            raise TeamAuthorizationError("provider-confirmed response account association required")
        if (not isinstance(native, dict) or native.get("object") != "response"
                or native.get("status") != "completed" or native.get("error") is not None
                or native.get("incomplete_details") is not None
                or native.get("model") != e.model or native.get("service_tier") != e.service_tier
                or not isinstance(native.get("id"), str) or not native["id"].strip()
                or type(native.get("created_at")) is not int
                or not e.valid_from.timestamp() <= native["created_at"] <= self.clock().timestamp()):
            raise TeamAuthorizationError("response meter terminal/model/tier/freshness binding invalid")
        usage = native.get("usage")
        if not isinstance(usage, dict):
            raise TeamAuthorizationError("response meter usage missing")
        def count(source, key):
            value = source.get(key) if isinstance(source, dict) else None
            if type(value) is not int or value < 0:
                raise TeamAuthorizationError("response meter token detail missing or malformed")
            return value
        inputs, outputs, total = (count(usage, k) for k in ("input_tokens", "output_tokens", "total_tokens"))
        cached = count(usage.get("input_tokens_details"), "cached_tokens")
        reasoning = count(usage.get("output_tokens_details"), "reasoning_tokens")
        if cached > inputs or reasoning > outputs or total != inputs + outputs:
            raise TeamAuthorizationError("response meter token accounting inconsistent")
        # Reasoning is already part of output; cached input replaces uncached input.
        numerator = ((inputs - cached) * e.input_microusd_per_million
                     + cached * e.cached_input_microusd_per_million
                     + outputs * e.output_microusd_per_million)
        amount = (numerator + 999_999) // 1_000_000
        # Receipt provenance is mandatory; no settlement without durable evidence.
        document = canonical_json_bytes(e.model_dump(mode="json")).decode()
        evidence = dict(cost_basis="signed-tariff-metered-not-invoice",
            enrollment_document=document, enrollment_digest=document_digest(e.model_dump(mode="json")),
            response_digest=document_digest(native),
            response_id=native["id"], request_id=headers["x-request-id"],
            cached_input_tokens=cached, reasoning_output_tokens=reasoning,
            input_tokens=inputs, output_tokens=outputs)
        receipt = ProviderUsageReceipt(schema_version="primary-provider-usage/v1",
            attempt_digest=e.attempt_digest, authority_digest=e.authority_digest,
            provider_id="openai", actual_microusd=amount, observed_tokens=total,
            complete=True, meter_signature="0" * 64, metering_evidence=evidence)
        signature = hmac.new(self.authority.entitlements._route_verifier_secret,
            canonical_json_bytes(receipt.signing_document()), hashlib.sha256).hexdigest()
        receipt = receipt.model_copy(update={"meter_signature": signature})
        with self.pool.connection() as connection, connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("primary-provider-budget:" + e.project_id,))
            self._verify()
            self._reservation(connection)
            self.authority.record_usage(connection, receipt)
        return receipt


class ResponseMeterRegistry:
    """Explicit protected configuration; no environment discovery or auto-enrollment.

    The owner-signed enrollment documents are supplied by the trusted application
    builder. Each call resolves a fresh instance using the admitted attempt, so
    parallel requests cannot exchange receipt bindings through shared state.
    """
    def __init__(self, *, enrollments, authority, pool):
        entries = {}
        for enrollment in enrollments:
            if not isinstance(enrollment, ResponseMeterEnrollment) or enrollment.attempt_digest in entries:
                raise TeamAuthorizationError("unique typed response meter enrollments required")
            entries[enrollment.attempt_digest] = enrollment
        self.enrollments = MappingProxyType(entries)
        self.authority, self.pool = authority, pool

    def for_context(self, context):
        enrollment = self.enrollments.get(context.idempotency_key)
        if enrollment is None:
            raise TeamAuthorizationError("admitted attempt lacks protected response meter enrollment")
        return EnrolledResponseMeter(enrollment=enrollment, authority=self.authority, pool=self.pool)
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | END]]
