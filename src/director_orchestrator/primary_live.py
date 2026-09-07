# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:38 UTC | BEGIN]]
"""Production composition for the Default LangGraph-primary service.

This module only composes already-enforced boundaries.  It contains no
transport choice, retry loop, legacy-engine fallback, or evidence fabrication.
Every external or durable dependency is injected by the activation layer.
"""

from __future__ import annotations

import hmac
from typing import Any, Callable, Mapping, Protocol

from .langgraph_primary import (
    AcceptanceHandler,
    EvidenceHandler,
    HandlerContext,
    PlanResult,
    PrimaryHandlers,
)
from .langgraph_runtime import PacketBoundaryAdapter
from .packet_contracts import document_digest, validate_packet
from .primary_completion import (
    CompletionEvidenceResolver,
    ExternalEffectResolver,
    PacketResultResolver,
    PrimaryCompletionServices,
)
from .primary_default import DefaultDryRunServices
from .primary_providers import (
    CandidateShaResolver,
    PrimaryProviderBindingResolver,
    PrimaryProviderBoundary,
    ProviderResultArtifactWriter,
    ProviderWriterFenceResolver,
)


class WorkPacketArtifactWriter(Protocol):
    """Persist one validated packet and return its durable content digest."""

    def __call__(
        self,
        context: HandlerContext,
        work_packet: Mapping[str, Any],
        work_packet_digest: str,
    ) -> str: ...


LiveProviderActivation = Callable[[str], bool]


class DefaultLiveServices:
    """Build the complete live ``PrimaryHandlers`` set for Default Director.

    Planning remains the existing Default planning implementation, but its
    validated post-plan packet must be durably acknowledged before the graph is
    allowed to record ``PlanResult``.  Provider execution and all completion
    gates remain in their dedicated primary boundaries.
    """

    def __init__(
        self,
        *,
        planning_services: DefaultDryRunServices,
        adapters: Mapping[str, PacketBoundaryAdapter],
        work_packet_artifact_writer: WorkPacketArtifactWriter,
        work_packet_binding_resolver: PrimaryProviderBindingResolver,
        candidate_sha_resolver: CandidateShaResolver,
        live_provider_activation: LiveProviderActivation,
        writer_fence_resolver: ProviderWriterFenceResolver,
        result_artifact_writer: ProviderResultArtifactWriter,
        packet_result_resolver: PacketResultResolver,
        completion_evidence_resolver: CompletionEvidenceResolver,
        external_effect_resolver: ExternalEffectResolver,
        evidence_debate_handler: EvidenceHandler,
        acceptance_handler: AcceptanceHandler,
    ) -> None:
        required = {
            "work_packet_artifact_writer": work_packet_artifact_writer,
            "work_packet_binding_resolver": work_packet_binding_resolver,
            "candidate_sha_resolver": candidate_sha_resolver,
            "live_provider_activation": live_provider_activation,
            "writer_fence_resolver": writer_fence_resolver,
            "result_artifact_writer": result_artifact_writer,
            "packet_result_resolver": packet_result_resolver,
            "completion_evidence_resolver": completion_evidence_resolver,
            "external_effect_resolver": external_effect_resolver,
            "evidence_debate_handler": evidence_debate_handler,
            "acceptance_handler": acceptance_handler,
        }
        for name, dependency in required.items():
            if not callable(dependency):
                raise ValueError(f"{name} must be callable")
        if not adapters:
            raise ValueError("live Default services require at least one provider adapter")

        self._planning = planning_services
        self._artifact_writer = work_packet_artifact_writer
        self._evidence_debate_handler = evidence_debate_handler
        self._acceptance_handler = acceptance_handler
        self._provider = PrimaryProviderBoundary(
            adapters=adapters,
            binding_resolver=work_packet_binding_resolver,
            candidate_sha_resolver=candidate_sha_resolver,
            provider_readiness_resolver=live_provider_activation,
            writer_fence_resolver=writer_fence_resolver,
            result_artifact_writer=result_artifact_writer,
        )
        self._completion = PrimaryCompletionServices(
            packet_result_resolver=packet_result_resolver,
            completion_evidence_resolver=completion_evidence_resolver,
            external_effect_resolver=external_effect_resolver,
        )

    def plan(self, context: HandlerContext) -> PlanResult:
        """Plan once, then persist and verify the exact post-plan packet."""

        result = self._planning.plan(context)
        record = self._planning._record(context.task_id)
        if record.plan is None or not record.plan.assignments:
            raise RuntimeError("Default live plan has no packet assignment")
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        packet = validate_packet(
            "work",
            self._planning._packet_router.planned_work_packet(record, assignment),
        )
        packet_digest = document_digest(packet)
        if not hmac.compare_digest(packet_digest, result.work_packet_digest):
            raise RuntimeError("Default live post-plan packet changed before persistence")
        persisted_digest = self._artifact_writer(context, packet, packet_digest)
        if not isinstance(persisted_digest, str) or not hmac.compare_digest(
            persisted_digest, packet_digest
        ):
            raise RuntimeError("Default live work-packet persistence was not acknowledged")
        return result

    def evidence_debate(self, context: HandlerContext):
        """Consume authenticated durable result evidence before deliberation."""

        progression = self._completion.consume_provider_result(context)
        if progression.terminal_task_status is not None:
            raise RuntimeError("terminal packet result cannot enter evidence debate")
        if not progression.requires_director_review:
            raise RuntimeError("packet result is not eligible for Director review")
        return self._evidence_debate_handler(context)

    def handlers(self) -> PrimaryHandlers:
        """Return the one fully wired, transport-free live handler bundle."""

        planning = self._planning.handlers()
        return PrimaryHandlers(
            authenticate_scope=planning.authenticate_scope,
            canonicalize=planning.canonicalize,
            plan=self.plan,
            policy_budget=planning.policy_budget,
            schedule=planning.schedule,
            provider_subgraph=self._provider.provider_subgraph,
            evidence_debate=self.evidence_debate,
            openai_acceptance_revision=self._acceptance_handler,
            exact_sha_review=self._completion.exact_sha_review,
            ci_security_completion=self._completion.ci_security_completion,
            effect_execution_readback=self._completion.effect_execution_readback,
        )


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:38 UTC | BEGIN]]
# Live composition requires writer-fence revalidation and durable result artifacts.
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
