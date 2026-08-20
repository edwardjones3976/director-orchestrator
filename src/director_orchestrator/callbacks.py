from __future__ import annotations

import hmac

from .models import ClaudeCallback, ManualRunCompletion, ProviderName, TaskRecord, TaskStatus


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

    run = matching[-1]
    if run.status != "WAITING_CALLBACK":
        if run.status == callback.status and hmac.compare_digest(run.result or "", callback.result):
            return False
        raise CallbackConflict("Claude run already has a different terminal result")

    run.status = callback.status
    run.result = callback.result
    run.url = callback.session_url or run.url
    run.evidence_source = "provider_authenticated"
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
    record.status = TaskStatus.QUEUED
    record.error = None
    record.message = "Owner supplied exact-run manual completion evidence"
