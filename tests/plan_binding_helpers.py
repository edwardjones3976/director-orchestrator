# [[Codex | GPT-5.6 | 2026-08-24 | 16:51 UTC | BEGIN]]
from __future__ import annotations

import hashlib

from director_orchestrator.approvals import (
    record_task_effect_observation,
    reserve_task_effect,
    task_effect_key,
)
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import (
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    require_verified_result,
    start_interaction,
    transition_interaction,
)
from director_orchestrator.models import ProviderName, TaskRecord


def seed_production_plan_materialization(record: TaskRecord) -> None:
    """Install the same generation/effect/result bindings as a live PLAN call."""

    if record.plan is None:
        raise AssertionError("a plan is required before seeding its authority")
    if record.plan_generation == 0:
        record.plan_generation = 1
    target_generation = record.plan_generation
    record.plan_generation = target_generation - 1
    effect_key = task_effect_key(
        record,
        phase="plan",
        identity={
            "intake": hashlib.sha256(
                record.intake.model_dump_json().encode("utf-8")
            ).hexdigest(),
            "target_generation": target_generation,
        },
    )
    reserve_task_effect(
        record,
        effect_key=effect_key,
        kind="director_intent",
        phase="plan",
        provider=ProviderName.OPENAI,
    )
    record.plan_generation = target_generation

    interaction_id = (
        f"{record.task_id}:generation-{target_generation}:openai-plan"
    )
    result_text = record.plan.model_dump_json()
    drafted_evidence = build_transport_evidence(
        transport=ProviderTransport.OPENAI_RESPONSES_API,
        source=TransportEvidenceSource.BACKEND_OBSERVATION,
        idempotency_key=effect_key,
    )
    drafted = start_interaction(
        task_id=record.task_id,
        interaction_id=interaction_id,
        plan_generation=target_generation,
        assignment_sequence=None,
        provider=ProviderName.OPENAI.value,
        phase=ProviderInteractionPhase.PLAN,
        attempt=1,
        transport_evidence=drafted_evidence,
        effect_key=effect_key,
    )
    attempted = transition_interaction(
        [drafted],
        interaction_id=interaction_id,
        next_state=ProviderInteractionState.ATTEMPTED,
        transport_evidence=drafted_evidence,
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, drafted
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, attempted
    )
    authenticated_evidence = build_transport_evidence(
        transport=ProviderTransport.OPENAI_RESPONSES_API,
        source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
        idempotency_key=interaction_id,
        native_status="completed",
        result=result_text,
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
            transport_evidence=authenticated_evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )
    verified_event = require_verified_result(
        record.provider_interaction_ledger,
        interaction_id=interaction_id,
        result=result_text,
    )
    record.plan_interaction_id = interaction_id
    binding = OrchestrationEngine._plan_materialization_binding(
        record,
        verified_event_digest=verified_event.event_digest,
        verified_result_digest=(
            verified_event.transport_evidence.result_digest or ""
        ),
        effect_key=effect_key,
    )
    record_task_effect_observation(
        record,
        effect_key=effect_key,
        intent_kind="director_intent",
        observation_kind="director_observation",
        phase="plan",
        outcome="completed",
        run_id=binding,
    )


# [[Codex | GPT-5.6 | 2026-08-24 | 16:51 UTC | END]]
