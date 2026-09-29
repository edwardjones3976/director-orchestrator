# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 04:18 UTC | BEGIN]]
"""Deterministic progression of validated packet results into task evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .interactions import (
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    canonical_identifier_digest,
    require_verified_result,
    start_interaction,
    transition_interaction,
)
from .langgraph_runtime import RuntimeExecutionOutcome, RuntimeExecutionStatus
from .models import Assignment, ProviderName, ProviderRun, TaskRecord, TaskStatus
from .packet_contracts import (
    PROHIBITED_EXTERNAL_EFFECT_KINDS,
    PacketContractError,
    build_replay_run,
    document_digest,
    validate_packet,
)


class PacketResultConsumptionError(ValueError):
    """Validated runtime evidence cannot safely progress the Director record."""


@dataclass(frozen=True, slots=True)
class PacketResultProgression:
    packet_status: str
    provider_run_recorded: bool
    requires_director_review: bool
    requires_revision: bool
    terminal_task_status: TaskStatus | None


class DirectorPacketResultConsumer:
    """Record provider evidence while withholding acceptance and side effects."""

    _RUN_STATUS = {
        "FAILED": "FAILED",
        "CANCELLED": "CANCELLED",
        "OWNER_DECISION_REQUIRED": "OWNER_DECISION_REQUIRED",
        "REVISION_REQUIRED": "REVISION_REQUIRED",
    }
    _TRANSPORT = {
        ProviderName.OPENAI: ProviderTransport.OPENAI_RESPONSES_API,
        ProviderName.CLAUDE: ProviderTransport.CLAUDE_ROUTINE,
        ProviderName.CURSOR: ProviderTransport.CURSOR_AGENTS_API,
        ProviderName.GEMINI: ProviderTransport.GEMINI_INTERACTIONS_API,
        ProviderName.GROK: ProviderTransport.XAI_RESPONSES_API,
        ProviderName.PERPLEXITY: ProviderTransport.PERPLEXITY_API,
        ProviderName.ZAI: ProviderTransport.ZAI_CODING_CLI,
    }

    @staticmethod
    def _validate_external_effects(
        work_packet: dict[str, object], result_packet: dict[str, object]
    ) -> None:
        allowed = set(work_packet["allowed_external_effects"])
        # There is no status bypass. A NOT_ATTEMPTED proposal is what drives the
        # typed effect gateway, so every reported kind - proposed or performed -
        # must already be granted by this exact work packet.
        for effect in result_packet["side_effects"]:
            kind = effect["kind"]
            if kind not in allowed:
                raise PacketResultConsumptionError(
                    "provider result reports an external effect outside packet authority"
                )
            if kind in PROHIBITED_EXTERNAL_EFFECT_KINDS:
                raise PacketResultConsumptionError(
                    "provider result reports a prohibited external effect"
                )

    def _record_verified_interaction(
        self,
        record: TaskRecord,
        assignment: Assignment,
        result: dict[str, object],
    ) -> None:
        interaction_id = str(result["correlation"]["correlation_id"])
        if any(
            item.interaction_id == interaction_id
            for item in record.provider_interaction_ledger
        ):
            require_verified_result(
                record.provider_interaction_ledger,
                interaction_id=interaction_id,
                result=str(result["outcome"]),
            )
            return
        effect_key = canonical_identifier_digest(str(result["idempotency_key"]))
        backend = build_transport_evidence(
            transport=self._TRANSPORT[assignment.agent],
            source=TransportEvidenceSource.BACKEND_OBSERVATION,
            idempotency_key=str(result["idempotency_key"]),
        )
        drafted = start_interaction(
            task_id=record.task_id,
            interaction_id=interaction_id,
            plan_generation=record.plan_generation,
            assignment_sequence=assignment.sequence,
            provider=assignment.agent.value,
            phase=ProviderInteractionPhase.DISPATCH,
            attempt=1,
            transport_evidence=backend,
            effect_key=effect_key,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, drafted
        )
        attempted = transition_interaction(
            record.provider_interaction_ledger,
            interaction_id=interaction_id,
            next_state=ProviderInteractionState.ATTEMPTED,
            transport_evidence=backend,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, attempted
        )
        authenticated = build_transport_evidence(
            transport=self._TRANSPORT[assignment.agent],
            source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
            idempotency_key=str(result["idempotency_key"]),
            provider_run_id=str(result["run_reference"]),
            correlation_id=interaction_id,
            native_status=str(result["status"]),
            result=str(result["outcome"]),
        )
        for state in (
            ProviderInteractionState.DISPATCHED,
            ProviderInteractionState.ACKNOWLEDGED,
            ProviderInteractionState.RESPONDED,
            ProviderInteractionState.COMPLETED,
            ProviderInteractionState.VERIFIED,
        ):
            event = transition_interaction(
                record.provider_interaction_ledger,
                interaction_id=interaction_id,
                next_state=state,
                transport_evidence=authenticated,
            )
            record.provider_interaction_ledger = append_interaction_event(
                record.provider_interaction_ledger, event
            )

    def consume(
        self,
        record: TaskRecord,
        assignment: Assignment,
        work_packet: dict[str, object],
        outcome: RuntimeExecutionOutcome,
    ) -> PacketResultProgression:
        if outcome.status not in {
            RuntimeExecutionStatus.COMPLETED,
            RuntimeExecutionStatus.REPLAYED,
        }:
            raise PacketResultConsumptionError(
                "only completed or replayed runtime evidence can be consumed"
            )
        if outcome.result_packet is None or outcome.result_packet_digest is None:
            raise PacketResultConsumptionError("runtime result packet evidence is missing")

        work = validate_packet("work", work_packet)
        result = validate_packet("result", outcome.result_packet)
        try:
            build_replay_run(work, result)
        except PacketContractError as exc:
            raise PacketResultConsumptionError(
                "work/result packet linkage is invalid"
            ) from exc
        digest = document_digest(result)
        if digest != outcome.result_packet_digest:
            raise PacketResultConsumptionError("runtime result digest is invalid")
        if result["provider"] != assignment.agent.value:
            raise PacketResultConsumptionError("result provider does not match assignment")
        self._validate_external_effects(work, result)

        existing = next(
            (item for item in record.provider_runs if item.run_id == result["run_reference"]),
            None,
        )
        if existing is not None:
            bound_digest = existing.raw.get("packet_result_digest")
            if bound_digest is not None and bound_digest != digest:
                raise PacketResultConsumptionError(
                    "provider run reference is already bound to different evidence"
                )
            if (
                existing.provider != assignment.agent
                or existing.assignment_sequence != assignment.sequence
                or existing.interaction_id
                != result["correlation"]["correlation_id"]
                or existing.result != result["outcome"]
            ):
                raise PacketResultConsumptionError(
                    "existing provider callback evidence conflicts with the result packet"
                )
            existing.raw = {
                **existing.raw,
                "packet_result_digest": digest,
                "packet_status": result["status"],
                "authority_snapshot_digest": result["authority_snapshot_digest"],
                "usage": result.get("usage") or {},
                "revision_recommended": result["revision_recommended"],
                "blockers": list(result["blockers"]),
                "requested_decisions": list(result["requested_decisions"]),
            }
            recorded = False
        else:
            try:
                provider = ProviderName(result["provider"])
            except ValueError as exc:
                raise PacketResultConsumptionError(
                    "result provider is not a Director provider"
                ) from exc
            usage = result.get("usage") or {}
            run = ProviderRun(
                provider=provider,
                assignment_sequence=assignment.sequence,
                interaction_id=result["correlation"]["correlation_id"],
                run_id=result["run_reference"],
                status=(
                    {
                        ProviderName.CURSOR: "FINISHED",
                        ProviderName.CLAUDE: "completed",
                        ProviderName.GROK: "completed",
                        ProviderName.GEMINI: "completed",
                        ProviderName.PERPLEXITY: "completed",
                        ProviderName.OPENAI: "completed",
                    }[provider]
                    if result["status"] == "SUCCEEDED"
                    else self._RUN_STATUS[result["status"]]
                ),
                result=result["outcome"],
                evidence_source="provider_authenticated",
                raw={
                    "packet_result_digest": digest,
                    "packet_status": result["status"],
                    "authority_snapshot_digest": result["authority_snapshot_digest"],
                    "usage": usage,
                    "revision_recommended": result["revision_recommended"],
                    "blockers": list(result["blockers"]),
                    "requested_decisions": list(result["requested_decisions"]),
                },
            )
            self._record_verified_interaction(record, assignment, result)
            record.provider_runs.append(run)
            recorded = True

        packet_status = result["status"]
        terminal: TaskStatus | None = None
        requires_review = packet_status == "SUCCEEDED"
        requires_revision = (
            packet_status == "REVISION_REQUIRED" or result["revision_recommended"]
        )
        if packet_status == "OWNER_DECISION_REQUIRED":
            terminal = TaskStatus.OWNER_DECISION_REQUIRED
        elif packet_status in {"FAILED", "CANCELLED"}:
            terminal = TaskStatus.BLOCKED

        if terminal is not None:
            record.status = terminal
        # A successful provider result deliberately remains RUNNING.  Only the
        # existing Director acceptance path may append AssignmentOutcome and
        # advance next_assignment_index or DONE.
        record.message = (
            f"Packet result for assignment {assignment.sequence} recorded as "
            f"{packet_status}; "
            + (
                "Director acceptance review is required."
                if requires_review
                else "the existing Director workflow must determine the next step."
            )
        )[:2_000]
        return PacketResultProgression(
            packet_status=packet_status,
            provider_run_recorded=recorded,
            requires_director_review=requires_review,
            requires_revision=requires_revision,
            terminal_task_status=terminal,
        )
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 04:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | END]]
