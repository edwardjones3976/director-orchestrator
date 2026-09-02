# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | BEGIN]]
"""Deterministic post-provider boundaries for the LangGraph primary engine.

The services in this module translate already-authenticated local evidence into
the compact result types consumed by ``PrimaryHandlers``.  They deliberately do
not execute providers, gates, repository commands, or external effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from .completion_gates import (
    VerifiedGateLedger,
    VerifiedGateResult,
    aggregate_completion_eligibility,
    domain_digest,
)
from .langgraph_primary import (
    CompletionGateResult,
    EffectDisposition,
    EffectExecutionResult,
    ExactShaReviewResult,
    HandlerContext,
)
from .langgraph_runtime import RuntimeExecutionOutcome
from .models import Assignment, TaskRecord
from .packet_contracts import document_digest
from .packet_results import DirectorPacketResultConsumer, PacketResultProgression


@dataclass(frozen=True, slots=True)
class PacketResultBinding:
    """Existing Director objects needed to consume one authenticated packet."""

    record: TaskRecord
    assignment: Assignment
    work_packet: dict[str, object]
    outcome: RuntimeExecutionOutcome


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    """Validated gate ledger and results selected for one primary task."""

    ledger: VerifiedGateLedger
    results: tuple[VerifiedGateResult, ...]
    ci_gate_id: str
    security_gate_id: str
    authority_proofs: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalEffectEvidence:
    """Authenticated execution result plus authoritative post-write readback."""

    effect_key_digest: str
    effect_attempt_digest: str
    writer_owner_id: str
    disposition: EffectDisposition
    result: Mapping[str, object] | None = None
    readback: Mapping[str, object] | None = None
    reason_code: str = "ok"


PacketResultResolver = Callable[[HandlerContext], PacketResultBinding | None]
CompletionEvidenceResolver = Callable[[HandlerContext], CompletionEvidence | None]
ExternalEffectResolver = Callable[[HandlerContext], ExternalEffectEvidence | None]


class PrimaryCompletionServices:
    """Synchronous adapters for graph-owned post-provider phases.

    Resolvers are expected to read authenticated, durable evidence.  Returning
    ``None`` or raising means the evidence is unavailable and therefore cannot
    authorize progress.
    """

    def __init__(
        self,
        *,
        packet_result_resolver: PacketResultResolver,
        completion_evidence_resolver: CompletionEvidenceResolver,
        external_effect_resolver: ExternalEffectResolver,
        packet_result_consumer: DirectorPacketResultConsumer | None = None,
    ) -> None:
        self._packet_result_resolver = packet_result_resolver
        self._completion_evidence_resolver = completion_evidence_resolver
        self._external_effect_resolver = external_effect_resolver
        self._packet_result_consumer = (
            packet_result_consumer or DirectorPacketResultConsumer()
        )

    def consume_provider_result(
        self, context: HandlerContext
    ) -> PacketResultProgression:
        """Consume one durable packet result without entering the legacy loop."""

        binding = self._packet_result_resolver(context)
        if binding is None:
            raise ValueError("packet result evidence is unavailable")
        if binding.record.task_id != context.task_id:
            raise ValueError("packet result task binding mismatch")
        if context.provider_result_digest is None:
            raise ValueError("primary context has no provider result digest")
        if binding.outcome.result_packet_digest != context.provider_result_digest:
            raise ValueError("packet result digest binding mismatch")
        return self._packet_result_consumer.consume(
            binding.record,
            binding.assignment,
            binding.work_packet,
            binding.outcome,
        )

    @staticmethod
    def _failure_digest(context: HandlerContext, phase: str, reason: str) -> str:
        return domain_digest(
            "director-primary-post-provider-failure/v1",
            {
                "task_id": context.task_id,
                "phase": phase,
                "candidate_sha": context.candidate_sha,
                "provider_result_digest": context.provider_result_digest,
                "reason": reason,
            },
        )

    def _completion_evidence(
        self, context: HandlerContext
    ) -> CompletionEvidence | None:
        try:
            evidence = self._completion_evidence_resolver(context)
        except Exception:
            return None
        if evidence is None:
            return None
        ledger = evidence.ledger.document
        if (
            ledger.get("task_id") != context.task_id
            or ledger.get("work_packet_digest") != context.work_packet_digest
            or ledger.get("expected_head_sha") != context.candidate_sha
            or ledger.get("candidate_result_digest") != context.provider_result_digest
            or ledger.get("ledger_state") != "HEAD_BOUND"
        ):
            return None
        return evidence

    def exact_sha_review(self, context: HandlerContext) -> ExactShaReviewResult:
        """Translate independent exact-SHA gate evidence into a review result."""

        producer_id = context.producer_id or "unavailable-producer"
        reviewer_id = context.reviewer_id or "unavailable-reviewer"
        evidence = self._completion_evidence(context)
        reason = "review_evidence_unavailable"
        if evidence is not None:
            producer = evidence.ledger.document.get("logical_producer", {})
            if isinstance(producer, Mapping) and producer.get("provider_id") == producer_id:
                for result in evidence.results:
                    document = result.document
                    actor = document.get("evidence_actor", {})
                    logical_reviewer = (
                        actor.get("logical_provider")
                        if isinstance(actor, Mapping)
                        else None
                    )
                    if (
                        document.get("ledger_digest") == evidence.ledger.digest
                        and document.get("executed_sha") == context.candidate_sha
                        and document.get("status") == "PASS"
                        and document.get("evidence_role") == "INDEPENDENT_REVIEWER"
                        and logical_reviewer
                        and logical_reviewer != producer_id
                        and (context.reviewer_id is None or logical_reviewer == context.reviewer_id)
                    ):
                        return ExactShaReviewResult(
                            review_digest=result.digest,
                            reviewed_sha=context.candidate_sha,
                            producer_id=producer_id,
                            reviewer_id=str(logical_reviewer),
                        )
            reason = "review_binding_invalid"
        return ExactShaReviewResult(
            review_digest=self._failure_digest(context, "exact_sha_review", reason),
            reviewed_sha=context.candidate_sha,
            producer_id=producer_id,
            reviewer_id=reviewer_id,
            accepted=False,
            reason_code=reason,
        )

    @staticmethod
    def _gate_result(
        evidence: CompletionEvidence, gate_id: str, candidate_sha: str
    ) -> VerifiedGateResult | None:
        matches = [
            result
            for result in evidence.results
            if result.document.get("gate_id") == gate_id
            and result.document.get("ledger_digest") == evidence.ledger.digest
            and result.document.get("executed_sha") == candidate_sha
            and result.document.get("status") == "PASS"
        ]
        return matches[0] if len(matches) == 1 else None

    def ci_security_completion(self, context: HandlerContext) -> CompletionGateResult:
        """Aggregate verified required gates, with explicit CI/security bindings."""

        evidence = self._completion_evidence(context)
        reason = "completion_evidence_unavailable"
        ci_result: VerifiedGateResult | None = None
        security_result: VerifiedGateResult | None = None
        if evidence is not None:
            ci_result = self._gate_result(evidence, evidence.ci_gate_id, context.candidate_sha)
            security_result = self._gate_result(
                evidence, evidence.security_gate_id, context.candidate_sha
            )
            status = aggregate_completion_eligibility(evidence.ledger, evidence.results)
            if (
                ci_result is not None
                and security_result is not None
                and status["assignment_acceptance_authorized"] is True
            ):
                completion_digest = domain_digest(
                    "director-primary-completion-evidence/v1",
                    {
                        "task_id": context.task_id,
                        "candidate_sha": context.candidate_sha,
                        "ledger_digest": evidence.ledger.digest,
                        "result_digests": sorted(result.digest for result in evidence.results),
                        "ci_digest": ci_result.digest,
                        "security_digest": security_result.digest,
                    },
                )
                return CompletionGateResult(
                    ci_digest=ci_result.digest,
                    security_digest=security_result.digest,
                    completion_digest=completion_digest,
                    subject_sha=context.candidate_sha,
                )
            reason = str(status["reason"])
            if ci_result is None:
                reason = "trusted_ci_evidence_missing"
            elif security_result is None:
                reason = "security_evidence_missing"
        failure = self._failure_digest(context, "ci_security_completion", reason)
        return CompletionGateResult(
            ci_digest=ci_result.digest if ci_result is not None else failure,
            security_digest=security_result.digest if security_result is not None else failure,
            completion_digest=failure,
            subject_sha=context.candidate_sha,
            passed=False,
            reason_code=reason,
        )

    def effect_execution_readback(self, context: HandlerContext) -> EffectExecutionResult:
        """Translate authenticated effect result/readback without performing a write."""

        def ambiguous(reason: str) -> EffectExecutionResult:
            return EffectExecutionResult(
                effect_key_digest=context.effect_key_digest
                or self._failure_digest(context, "effect_key", reason),
                effect_attempt_digest=context.effect_attempt_digest
                or self._failure_digest(context, "effect_attempt", reason),
                writer_owner_id=context.writer_owner_id,
                disposition=EffectDisposition.AMBIGUOUS,
                reason_code=reason,
            )

        if context.effect_key_digest is None or context.effect_attempt_digest is None:
            return ambiguous("effect_intent_unavailable")
        try:
            evidence = self._external_effect_resolver(context)
        except Exception:
            return ambiguous("effect_evidence_unavailable")
        if evidence is None:
            return ambiguous("effect_evidence_unavailable")
        if (
            evidence.effect_key_digest != context.effect_key_digest
            or evidence.effect_attempt_digest != context.effect_attempt_digest
            or evidence.writer_owner_id != context.writer_owner_id
        ):
            return ambiguous("effect_binding_invalid")
        if evidence.disposition == EffectDisposition.COMPLETED:
            if evidence.result is None or evidence.readback is None:
                return ambiguous("effect_readback_unavailable")
            return EffectExecutionResult(
                effect_key_digest=evidence.effect_key_digest,
                effect_attempt_digest=evidence.effect_attempt_digest,
                writer_owner_id=evidence.writer_owner_id,
                disposition=EffectDisposition.COMPLETED,
                result_digest=document_digest(evidence.result),
                readback_digest=document_digest(evidence.readback),
                reason_code=evidence.reason_code,
            )
        if evidence.readback is not None:
            return ambiguous("unexpected_effect_readback")
        return EffectExecutionResult(
            effect_key_digest=evidence.effect_key_digest,
            effect_attempt_digest=evidence.effect_attempt_digest,
            writer_owner_id=evidence.writer_owner_id,
            disposition=evidence.disposition,
            result_digest=(
                document_digest(evidence.result) if evidence.result is not None else None
            ),
            reason_code=evidence.reason_code,
        )


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]
