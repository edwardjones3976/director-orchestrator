from __future__ import annotations

import hmac

from .models import ClaudeCallback, ProviderName, TaskRecord, TaskStatus


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
    record.status = TaskStatus.REVIEWING
    return True
