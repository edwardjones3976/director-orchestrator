# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 10:34 UTC | BEGIN]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:16 UTC | BEGIN]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:07 UTC | BEGIN]]
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
import os
from pathlib import Path
import stat
from types import MappingProxyType
from contextlib import contextmanager
from time import monotonic
from math import ceil
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .antigravity_team import (
    ClosedModel, Digest, Identifier, PrimaryProviderBudgetAuthority, ProviderBudget, ProviderUsageReceipt,
    TeamAuthorizationError,
)
from .entitlement_contracts import canonical_json_bytes, document_digest

# One bounded window for writing a receipt whose request was already admitted
# and sent, used only when the execution budget is already spent. It buys no
# provider work and no eligibility: an expired execution stays ineligible.
ACCOUNTING_ALLOWANCE_SECONDS = 5.0


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

    def _verify_signature(self):
        """The owner signature over this enrollment. Never relaxed, anywhere."""

        e = self.enrollment
        expected = hmac.new(self.authority.entitlements._owner_authority_secret,
            canonical_json_bytes(e.signing_document()), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, e.owner_signature):
            raise TeamAuthorizationError("response meter enrollment signature invalid")

    def _verify_applicable(self, at):
        """The signed tariff covered `at`, the instant being accounted for."""

        e = self.enrollment
        if at is None or at.tzinfo is None or not e.valid_from <= at < e.expires_at:
            raise TeamAuthorizationError("response meter enrollment is not current")

    def _verify(self):
        """Full current-validity gate: signature plus a tariff current *now*."""

        self._verify_signature()
        self._verify_applicable(self.clock())

    def verify_current(self):
        """Public re-check for a caller about to send this exact request.

        The same gate `prepare` applied, repeated at the real send instant so
        work between them - input-token counting, queueing - cannot carry a
        lapsed signature or an expired tariff into the priced request.
        """

        self._verify()

    @contextmanager
    def _connection(self, remaining_seconds, *, accounting_deadline=None):
        from psycopg import Error
        if accounting_deadline is not None:
            # Recording an authenticated fact that already happened is not more
            # execution. This absolute cutoff comes from the original
            # operation and replaces the execution callback entirely here:
            # that callback raises the moment execution expires, which would
            # destroy the receipt before the pool is even reached. It is the
            # same value on every call, so settlement replay cannot extend it,
            # and it authorises no provider work.
            remaining_seconds = lambda: accounting_deadline - monotonic()
        elif remaining_seconds is None:
            # No caller bound: derive one from the enrollment's own remaining
            # validity, unchanged while that validity lasts. A window that has
            # already closed is a reason to record the authenticated fact
            # promptly - within the same fixed allowance - not to lose it.
            validity = (self.enrollment.expires_at - self.clock()).total_seconds()
            window = min(180, validity) if validity > 0 else ACCOUNTING_ALLOWANCE_SECONDS
            deadline = monotonic() + window
            remaining_seconds = lambda: deadline - monotonic()
        def remaining():
            seconds = remaining_seconds()
            if seconds <= 0:
                raise RuntimeError("response meter deadline exhausted; reservation remains held")
            return seconds
        try:
            with self.pool.connection(timeout=remaining()) as connection, connection.transaction():
                class BoundedConnection:
                    def execute(self, statement, parameters=None):
                        milliseconds = str(max(1, ceil(remaining()*1000)))
                        connection.execute("SELECT set_config('statement_timeout',%s,true), "
                            "set_config('lock_timeout',%s,true)", (milliseconds, milliseconds))
                        result = connection.execute(statement, parameters)
                        remaining()
                        return result
                yield BoundedConnection()
                # Refresh the commit-time timeouts from the time actually left:
                # per-statement settings bound the earlier statements only, and
                # the implicit COMMIT -- including deferred-trigger waits -- must
                # be bounded by remaining budget, not by an earlier larger
                # figure. Source-level staleness correction; no runtime
                # reproduction is claimed.
                milliseconds = str(max(1, ceil(remaining()*1000)))
                connection.execute("SELECT set_config('statement_timeout',%s,true), "
                    "set_config('lock_timeout',%s,true)", (milliseconds, milliseconds))
                # Roll back rather than commit a settlement after its deadline.
                remaining()
        except Error as exc:
            raise RuntimeError("response meter database unavailable or deadline exhausted; reservation remains held") from exc

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

    def _binding_check(self, *, context, packet: bytes, api_url: str, model: str, api_key: str) -> None:
        e = self.enrollment
        if (api_url != e.origin + "/v1/responses" or model != e.model
                or hashlib.sha256(api_key.encode()).hexdigest() != e.credential_digest
                or context.task_id != e.task_id or context.assignment_id != e.assignment_id
                or context.idempotency_key != e.attempt_digest
                or context.work_packet_digest != e.work_packet_digest
                or "sha256:" + hashlib.sha256(packet).hexdigest() != e.work_packet_digest):
            raise TeamAuthorizationError("response meter request binding mismatch")

    def preflight_binding(self, *, context, packet: bytes, api_url: str, model: str, api_key: str) -> None:
        """Pure eligibility check only: signature, validity interval and exact
        request/context binding. No database read, no HTTP, no reservation -
        the admitted `prepare()` path performs the authoritative DB reservation
        check at actual dispatch, immediately before the provider call.
        """
        self._verify()
        self._binding_check(context=context, packet=packet, api_url=api_url, model=model, api_key=api_key)

    def prepare(self, *, context, packet: bytes, api_url: str, model: str, api_key: str, remaining_seconds=None):
        self._verify()
        self._binding_check(context=context, packet=packet, api_url=api_url, model=model, api_key=api_key)
        with self._connection(remaining_seconds) as connection:
            self._reservation(connection)
        e = self.enrollment
        return {"OpenAI-Organization": e.organization_id, "OpenAI-Project": e.api_project_id}

    def settle(self, native, response_headers, *, remaining_seconds=None, sent_at=None,
               accounting_deadline=None):
        """Record the authenticated usage of one already-admitted request.

        `sent_at` is the instant this exact request was actually sent, checked
        against a current enrollment at that moment. The tariff must have
        covered *that* instant: the accounting fact for a request that was
        legitimately admitted and sent does not stop being true because the
        signed window closed, or this attempt's execution budget ran out,
        while the provider was answering.

        `accounting_deadline` is one absolute cutoff from the original
        operation bounding only this receipt write. It replaces the execution
        callback here rather than widening it: that callback raises as soon as
        execution expires, which is exactly the case the fact most needs
        recording. It buys no provider work and makes no output eligible.

        Everything else is unchanged and unrelaxed: the owner signature, the
        provider-confirmed account association, model/tier/creation freshness,
        token accounting, the receipt HMAC, and `record_usage`'s reservation
        binding, duplicate-response and identical/conflicting replay checks.
        Without `sent_at` the previous current-validity gate applies exactly.
        """

        self._verify_signature()
        self._verify_applicable(sent_at if sent_at is not None else self.clock())
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
        with self._connection(remaining_seconds, accounting_deadline=accounting_deadline) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("primary-provider-budget:" + e.project_id,))
            # Re-checked after the lock wait, against the same instant this
            # settlement is accounting for: a window that closed while waiting
            # does not unmake the request that was already admitted and sent.
            self._verify_signature()
            self._verify_applicable(sent_at if sent_at is not None else self.clock())
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


def load_response_meter_registry(path, *, authority, pool):
    """Load bounded owner-signed metadata; this does not attest host ACL custody.

    Runtime credentials and signing keys stay in their existing stores. A valid
    signature is required even for files supplied by trusted startup config.
    """
    source = Path(path)
    if not source.is_absolute():
        raise TeamAuthorizationError("response meter enrollment path must be absolute")
    for component in (source, *source.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise TeamAuthorizationError("response meter enrollment path cannot traverse links")
    with source.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise TeamAuthorizationError("response meter enrollment must be a regular file")
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576:
        raise TeamAuthorizationError("response meter enrollment exceeds its byte bound")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise TeamAuthorizationError("duplicate response meter enrollment key")
            result[key] = value
        return result
    documents = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(documents, list) or not 1 <= len(documents) <= 128:
        raise TeamAuthorizationError("response meter registry requires 1 to 128 signed enrollments")
    enrollments = tuple(ResponseMeterEnrollment.model_validate_json(json.dumps(item)) for item in documents)
    registry = ResponseMeterRegistry(enrollments=enrollments, authority=authority, pool=pool)
    for enrollment in enrollments:
        EnrolledResponseMeter(enrollment=enrollment, authority=authority, pool=pool)._verify()
    return registry
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | END]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:07 UTC | END]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:16 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 10:34 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | END]]
