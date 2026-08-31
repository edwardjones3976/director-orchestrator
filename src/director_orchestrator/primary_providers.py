# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:04 UTC | BEGIN]]
"""Provider-neutral packet boundary for the LangGraph primary workflow.

The primary graph owns durable claims and budget/effect reservations.  This
module starts strictly after that boundary: it resolves the one immutable work
packet for the claimed attempt, revalidates every binding, invokes one existing
``PacketBoundaryAdapter``, and translates the authenticated v1 result into the
compact ``ProviderSubgraphResult`` consumed by ``PrimaryHandlers``.

There is deliberately no provider loop, retry, legacy-engine fallback, or
transport default here.  Once an adapter is entered, any exception is ambiguous
for that attempt and the same service instance will never call the adapter for
that attempt again.  Durable cross-process exclusion remains the responsibility
of the primary Postgres claim store.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .langgraph_primary import (
    HandlerContext,
    ProviderDisposition,
    ProviderSubgraphResult,
)
from .langgraph_runtime import PacketBoundaryAdapter, ProviderAdapterContext
from .packet_contracts import PacketContractError, document_digest, validate_packet


def identifier_digest(value: str) -> str:
    """Return the opaque binding used for packet idempotency identifiers."""

    if not isinstance(value, str) or not value:
        raise ValueError("identifier must be a non-empty string")
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PrimaryProviderBinding:
    """Immutable coordinator-owned material for one claimed provider attempt."""

    provider: str
    producer_id: str
    provider_attempt_digest: str
    writer_token_digest: str
    work_packet: Mapping[str, Any]


class PrimaryProviderBindingResolver(Protocol):
    """Resolve the packet already durably bound to a primary graph attempt."""

    def __call__(self, context: HandlerContext) -> PrimaryProviderBinding: ...


class CandidateShaResolver(Protocol):
    """Resolve an exact candidate SHA from an authenticated result packet."""

    def __call__(
        self, work_packet: Mapping[str, Any], result_packet: Mapping[str, Any]
    ) -> str: ...


class ProviderReadinessResolver(Protocol):
    """Return true only for combined observed app and adapter readiness."""

    def __call__(self, provider: str) -> bool: ...


_IN_FLIGHT = object()


class PrimaryProviderBoundary:
    """Synchronous ``PrimaryHandlers.provider_subgraph`` implementation.

    The caller supplies only existing enforced packet adapters plus two narrow
    resolvers.  The binding resolver retrieves coordinator-owned packet material;
    the candidate resolver performs repository-specific exact-SHA readback after
    a valid successful provider result.  Neither resolver gains dispatch authority.
    """

    def __init__(
        self,
        *,
        adapters: Mapping[str, PacketBoundaryAdapter],
        binding_resolver: PrimaryProviderBindingResolver,
        candidate_sha_resolver: CandidateShaResolver,
        provider_readiness_resolver: ProviderReadinessResolver,
    ) -> None:
        if not callable(binding_resolver):
            raise ValueError("binding_resolver must be callable")
        if not callable(candidate_sha_resolver):
            raise ValueError("candidate_sha_resolver must be callable")
        if not callable(provider_readiness_resolver):
            raise ValueError("provider_readiness_resolver must be callable")
        normalized: dict[str, PacketBoundaryAdapter] = {}
        for provider, adapter in adapters.items():
            if not isinstance(provider, str) or not provider:
                raise ValueError("provider adapter keys must be non-empty strings")
            if not isinstance(adapter, PacketBoundaryAdapter):
                raise ValueError("primary providers require PacketBoundaryAdapter")
            if adapter.provider != provider:
                raise ValueError("provider adapter key does not match adapter identity")
            normalized[provider] = adapter
        self._adapters = normalized
        self._binding_resolver = binding_resolver
        self._candidate_sha_resolver = candidate_sha_resolver
        self._provider_readiness_resolver = provider_readiness_resolver
        self._attempt_lock = threading.Lock()
        self._attempts: dict[str, object | ProviderSubgraphResult] = {}

    @staticmethod
    def _same(left: str, right: str) -> bool:
        return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))

    @staticmethod
    def _failed(context: HandlerContext, reason_code: str) -> ProviderSubgraphResult:
        return ProviderSubgraphResult(
            provider_attempt_digest=str(context.provider_attempt_digest),
            idempotency_key_digest=context.idempotency_key_digest,
            authority_snapshot_digest=context.authority_snapshot_digest,
            producer_id=str(context.scheduled_producer_id),
            disposition=ProviderDisposition.FAILED,
            reason_code=reason_code,
        )

    @staticmethod
    def _ambiguous(
        context: HandlerContext, reason_code: str = "provider_boundary_ambiguous"
    ) -> ProviderSubgraphResult:
        return ProviderSubgraphResult(
            provider_attempt_digest=str(context.provider_attempt_digest),
            idempotency_key_digest=context.idempotency_key_digest,
            authority_snapshot_digest=context.authority_snapshot_digest,
            producer_id=str(context.scheduled_producer_id),
            disposition=ProviderDisposition.AMBIGUOUS,
            reason_code=reason_code,
        )

    def _validate_binding(
        self, context: HandlerContext, binding: PrimaryProviderBinding
    ) -> dict[str, Any]:
        if context.provider_attempt_digest is None:
            raise PacketContractError("provider attempt is not durably bound")
        if context.scheduled_producer_id is None:
            raise PacketContractError("scheduled producer is not durably bound")
        if binding.provider not in self._adapters:
            raise PacketContractError("no packet adapter is configured for the provider")
        if binding.producer_id != context.scheduled_producer_id:
            raise PacketContractError("provider producer binding changed")
        if not self._same(
            binding.provider_attempt_digest, context.provider_attempt_digest
        ):
            raise PacketContractError("provider attempt binding changed")
        if not isinstance(binding.writer_token_digest, str) or not binding.writer_token_digest:
            raise PacketContractError("writer token digest is required")

        work = validate_packet("work", binding.work_packet)
        if work["task_id"] != context.task_id:
            raise PacketContractError("work packet task binding changed")
        if not self._same(document_digest(work), context.work_packet_digest):
            raise PacketContractError("work packet digest binding changed")
        if not self._same(
            str(work["authority_snapshot_digest"]),
            context.authority_snapshot_digest,
        ):
            raise PacketContractError("authority snapshot binding changed")
        if not self._same(
            identifier_digest(str(work["idempotency_key"])),
            context.idempotency_key_digest,
        ):
            raise PacketContractError("idempotency key binding changed")
        return work

    @staticmethod
    def _adapter_context(
        work: Mapping[str, Any], binding: PrimaryProviderBinding
    ) -> ProviderAdapterContext:
        limits = work["limits"]
        return ProviderAdapterContext(
            task_id=str(work["task_id"]),
            assignment_id=str(work["assignment_id"]),
            idempotency_key=str(work["idempotency_key"]),
            work_packet_digest=document_digest(work),
            writer_token_digest=binding.writer_token_digest,
            timeout_seconds=int(limits["timeout_seconds"]),
            budget_usd=float(limits["budget_usd"]),
            max_hops=int(limits["max_hops"]),
            max_revisions=int(limits["max_revisions"]),
            allowed_tools=tuple(str(item) for item in work["allowed_tools"]),
            allowed_external_effects=tuple(
                str(item) for item in work["allowed_external_effects"]
            ),
        )

    def provider_subgraph(self, context: HandlerContext) -> ProviderSubgraphResult:
        """Dispatch one claimed packet or replay the attempt's compact outcome."""

        if context.provider_attempt_digest is None or context.scheduled_producer_id is None:
            return self._failed(context, "provider_binding_missing")
        try:
            binding = self._binding_resolver(context)
            if not isinstance(binding, PrimaryProviderBinding):
                raise PacketContractError("binding resolver returned the wrong type")
            work = self._validate_binding(context, binding)
        except Exception:
            return self._failed(context, "provider_binding_invalid")

        try:
            provider_ready = self._provider_readiness_resolver(binding.provider)
        except Exception:
            provider_ready = False
        if provider_ready is not True:
            return self._failed(context, "provider_activation_not_ready")

        attempt = context.provider_attempt_digest
        with self._attempt_lock:
            prior = self._attempts.get(attempt)
            if isinstance(prior, ProviderSubgraphResult):
                return prior
            if prior is _IN_FLIGHT:
                return self._ambiguous(context, "provider_attempt_in_flight")
            self._attempts[attempt] = _IN_FLIGHT

        try:
            adapter = self._adapters[binding.provider]
            result = adapter.dispatch(
                work,
                context=self._adapter_context(work, binding),
            )
            result_digest = document_digest(result)
            if result["status"] == "SUCCEEDED":
                candidate_sha = self._candidate_sha_resolver(work, result)
                translated = ProviderSubgraphResult(
                    provider_attempt_digest=attempt,
                    idempotency_key_digest=context.idempotency_key_digest,
                    authority_snapshot_digest=context.authority_snapshot_digest,
                    producer_id=context.scheduled_producer_id,
                    disposition=ProviderDisposition.COMPLETED,
                    result_digest=result_digest,
                    candidate_sha=candidate_sha,
                    reason_code="provider_result_authenticated",
                )
            else:
                translated = ProviderSubgraphResult(
                    provider_attempt_digest=attempt,
                    idempotency_key_digest=context.idempotency_key_digest,
                    authority_snapshot_digest=context.authority_snapshot_digest,
                    producer_id=context.scheduled_producer_id,
                    disposition=ProviderDisposition.FAILED,
                    result_digest=result_digest,
                    reason_code="provider_reported_non_success",
                )
        except Exception:
            translated = self._ambiguous(context)

        with self._attempt_lock:
            self._attempts[attempt] = translated
        return translated


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:04 UTC | END]]
