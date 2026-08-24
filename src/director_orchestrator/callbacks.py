# [[Codex | GPT-5.6 | 2026-08-24 | 12:22 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 11:58 UTC | BEGIN]]
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
    canonical_identifier_digest,
    project_interaction,
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
    states = (
        (
            ProviderInteractionState.RESPONDED,
            ProviderInteractionState.COMPLETED,
            ProviderInteractionState.VERIFIED,
        )
        if callback.status == "completed"
        else (ProviderInteractionState.FAILED,)
    )
    if callback.status != "completed":
        evidence = build_transport_evidence(
            transport=ProviderTransport.CLAUDE_CALLBACK,
            source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
            idempotency_key=run.interaction_id,
            provider_run_id=run.run_id,
            provider_session_id=run.session_id,
            native_status=callback.status,
            result=callback.result,
            error_code=f"PROVIDER_{callback.status.upper()}",
        )
    for state in states:
        event = transition_interaction(
            record.provider_interaction_ledger,
            interaction_id=run.interaction_id,
            next_state=state,
            transport_evidence=evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )
    if callback.status == "completed":
        record.status = TaskStatus.REVIEWING
    else:
        record.status = TaskStatus.FAILED
        record.error = f"ProviderError: claude ended with status {callback.status}"
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
    if len(matching) != 1:
        raise CallbackConflict("Provider run ID is ambiguous across task assignments")
    run = matching[0]
    if run.result:
        raise CallbackConflict("Provider run already has a result")

    next_ledger = record.provider_interaction_ledger
    if run.interaction_id:
        projection = project_interaction(
            record.provider_interaction_ledger, run.interaction_id
        )
        if (
            projection.task_id != record.task_id
            or projection.provider.value != run.provider.value
            or projection.assignment_sequence != run.assignment_sequence
        ):
            raise CallbackConflict(
                "Provider interaction does not match the exact task run"
            )
        bound_run_digests = {
            event.transport_evidence.provider_run_id_digest
            for event in record.provider_interaction_ledger
            if event.interaction_id == run.interaction_id
            and event.transport_evidence.provider_run_id_digest is not None
        }
        expected_run_digest = canonical_identifier_digest(run.run_id)
        if bound_run_digests and (
            len(bound_run_digests) != 1
            or not hmac.compare_digest(
                next(iter(bound_run_digests)), expected_run_digest
            )
        ):
            raise CallbackConflict(
                "Provider interaction is bound to a different provider run"
            )

        # FAILED and UNAVAILABLE are immutable historical observations. Owner
        # recovery authorizes the task projection to resume; it does not rewrite
        # that provider history or pretend the transport became authenticated.
        if projection.state not in {
            ProviderInteractionState.FAILED,
            ProviderInteractionState.UNAVAILABLE,
        }:
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
            next_ledger = append_interaction_event(
                record.provider_interaction_ledger, event
            )

    run.status = "FINISHED"
    run.result = completion.result
    run.evidence_source = "owner_recovery"
    record.provider_interaction_ledger = next_ledger
    record.status = TaskStatus.QUEUED
    record.error = None
    record.message = "Owner supplied exact-run manual completion evidence"
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 11:58 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 12:22 UTC | END]]
