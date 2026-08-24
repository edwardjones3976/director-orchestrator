# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | BEGIN]]
from __future__ import annotations

import hmac

from .models import ClaudeCallback, ManualRunCompletion, ProviderName, TaskRecord, TaskStatus
from .interactions import (
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    transition_interaction,
)


class CallbackConflict(ValueError):
    pass


def bearer_matches(secret: str, authorization: str | None) -> bool:
    expected = f"Bearer {secret}"
    return bool(
        secret
        and authorization
        and hmac.compare_digest(authorization, expected)
    )


def apply_claude_callback(record: TaskRecord, callback: ClaudeCallback) -> bool:
    matching = [
        run
        for run in record.provider_runs
        if run.provider == ProviderName.CLAUDE
        and hmac.compare_digest(run.run_id, callback.run_id)
    ]
    if not matching:
        raise CallbackConflict("Claude callback run does not match this task")
    if len(matching) != 1:
        raise CallbackConflict(
            "Claude callback run ID is ambiguous across task assignments"
        )

    run = matching[-1]
    if run.status != "WAITING_CALLBACK":
        if run.status == callback.status and hmac.compare_digest(run.result or "", callback.result):
            return False
        raise CallbackConflict("Claude run already has a different terminal result")
    if not run.interaction_id:
        raise CallbackConflict("Claude callback lacks its backend interaction binding")

    run.status = callback.status
    run.result = callback.result
    run.url = callback.session_url or run.url
    run.evidence_source = "provider_authenticated"
    evidence = build_transport_evidence(
        transport=ProviderTransport.CLAUDE_CALLBACK,
        source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
        idempotency_key=run.interaction_id,
        provider_run_id=run.run_id,
        provider_session_id=run.session_id,
        native_status=callback.status,
        result=callback.result,
    )
    for state in (
        ProviderInteractionState.RESPONDED,
        ProviderInteractionState.COMPLETED,
        ProviderInteractionState.VERIFIED,
    ):
        event = transition_interaction(
            record.provider_interaction_ledger,
            interaction_id=run.interaction_id,
            next_state=state,
            transport_evidence=evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )
    record.status = TaskStatus.REVIEWING
    return True


def apply_manual_run_completion(
    record: TaskRecord,
    run_id: str,
    completion: ManualRunCompletion,
) -> None:
    if record.status not in {TaskStatus.FAILED, TaskStatus.OWNER_DECISION_REQUIRED}:
        raise CallbackConflict("Task is not awaiting manual run recovery")
    matching = [
        run for run in record.provider_runs if hmac.compare_digest(run.run_id, run_id)
    ]
    if not matching:
        raise CallbackConflict("Provider run does not match this task")
    run = matching[-1]
    if run.result:
        raise CallbackConflict("Provider run already has a result")
    run.status = "FINISHED"
    run.result = completion.result
    run.evidence_source = "owner_recovery"
    if run.interaction_id:
        evidence = build_transport_evidence(
            transport=ProviderTransport.MANUAL_OWNER,
            source=TransportEvidenceSource.OWNER_ATTESTED,
            idempotency_key=run.interaction_id,
            provider_run_id=run.run_id,
            native_status="OWNER_RECOVERY",
            result=completion.result,
            ambiguity_reason="provider transport completion was not authenticated",
        )
        event = transition_interaction(
            record.provider_interaction_ledger,
            interaction_id=run.interaction_id,
            next_state=ProviderInteractionState.UNKNOWN,
            transport_evidence=evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )
    record.status = TaskStatus.QUEUED
    record.error = None
    record.message = "Owner supplied exact-run manual completion evidence"
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | END]]
