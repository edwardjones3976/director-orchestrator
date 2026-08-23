# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:05 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]]
from __future__ import annotations

import hmac
import logging
import re
import threading
import time
from datetime import datetime, timezone

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .approvals import (
    build_slack_gateway_proof,
    failed_retry_decision_digest,
    parse_approval_button_value,
)
from .config import Settings
from .contribution_tags import DIRECTOR_SYSTEM_LABEL, wrap_human_submission
from .models import OwnerApproval, TaskRecord, TaskStatus


settings = Settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("director.slack")

if (
    not settings.slack_bot_token
    or not settings.slack_app_token
    or not settings.slack_gateway_secret
    or len(settings.slack_gateway_secret) < 32
    or (
        settings.shared_secret
        and hmac.compare_digest(
            settings.slack_gateway_secret, settings.shared_secret
        )
    )
):
    raise SystemExit(
        "Slack tokens and a distinct 32+ character SLACK_GATEWAY_SECRET are required"
    )

app = App(token=settings.slack_bot_token)
_seen_events: dict[str, float] = {}
_seen_lock = threading.Lock()


def _log_boundary_failure(operation: str, exc: Exception) -> None:
    """Log only bounded diagnostics for failures involving Slack input.

    Validation exceptions can embed the rejected note or payload in their
    message and traceback. Slack is an untrusted boundary, so neither is
    logged; the exception class is enough to distinguish validation,
    transport, and service failures operationally.
    """

    log.warning("%s (%s)", operation, type(exc).__name__)


def _tag(text: str, *, at: datetime | None = None) -> str:
    return wrap_human_submission(
        text,
        "Aesop Director",
        DIRECTOR_SYSTEM_LABEL,
        at=at,
        slack_markup=True,
        neutralize_embedded_tags=True,
    )


def _claim_event(event_id: str) -> bool:
    now = time.monotonic()
    with _seen_lock:
        expired = [key for key, seen_at in _seen_events.items() if now - seen_at > 600]
        for key in expired:
            _seen_events.pop(key, None)
        if event_id in _seen_events:
            return False
        _seen_events[event_id] = now
        return True


def _clean_text(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


def _signed_owner_payload(
    task_id: str,
    record: TaskRecord,
    task_version: int,
    payload: dict,
) -> dict:
    approval = OwnerApproval.model_validate(payload)
    proof = build_slack_gateway_proof(
        settings.slack_gateway_secret,
        settings.shared_secret,
        record,
        task_version,
        approval,
        channel_id=settings.slack_channel_id,
        path=f"/v1/tasks/{task_id}/approve",
    )
    return {
        **payload,
        "slack_gateway_proof": proof.model_dump(mode="json"),
    }


def _task_and_version(response) -> tuple[TaskRecord, int]:
    response.raise_for_status()
    raw_version = response.headers.get("X-Task-Record-Version")
    if raw_version is None or not raw_version.isdigit():
        raise ValueError("Director task response omitted its record version")
    return TaskRecord.model_validate(response.json()), int(raw_version)


def _forward(event: dict, client, *, require_prefix: bool) -> None:
    if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_changed", "message_deleted"}:
        return
    if event.get("channel") != settings.slack_channel_id:
        return
    if event.get("user") != settings.slack_owner_user_id:
        return
    text = _clean_text(event.get("text", ""))
    if require_prefix and not text.upper().startswith("DIRECTOR"):
        return
    if not text:
        return

    thread_ts = event.get("thread_ts") or event.get("ts")
    source_event_id = event.get("client_msg_id") or event.get("event_ts") or event.get("ts")
    if not source_event_id or not thread_ts or not _claim_event(source_event_id):
        return
    payload = {
        "source_event_id": source_event_id,
        "channel_id": event["channel"],
        "thread_ts": thread_ts,
        "user_id": event["user"],
        "text": text,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    target = settings.n8n_webhook_url or f"{settings.orchestrator_base_url}/v1/intake"
    try:
        response = httpx.post(
            target,
            headers={"Authorization": f"Bearer {settings.shared_secret}"},
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, list) and body:
            body = body[0]
        task_id = body.get("task_id") or body.get("data", {}).get("task_id") or "queued"
        client.chat_postMessage(channel=event["channel"], thread_ts=thread_ts, text=_tag(f"*{task_id} — QUEUED*\nThe Director accepted this work order in dry-run or live mode according to the configured safety switch."))
    except Exception as exc:
        _log_boundary_failure("Failed to forward Director event", exc)
        client.chat_postMessage(channel=event["channel"], thread_ts=thread_ts, text=_tag(f"*DIRECTOR — BLOCKED*\nThe local orchestrator could not accept this request: `{type(exc).__name__}`."))


@app.event("app_mention")
def mention_handler(event, client):
    _forward(event, client, require_prefix=False)


@app.event("message")
def message_handler(event, client):
    _forward(event, client, require_prefix=True)


@app.command("/director")
def command_handler(ack, command, client, respond):
    ack()
    if command.get("channel_id") != settings.slack_channel_id or command.get("user_id") != settings.slack_owner_user_id:
        respond(_tag("Aesop Director only accepts owner commands in the configured private channel."), response_type="ephemeral")
        return
    text = command.get("text", "").strip()
    approval = re.match(r"(?is)^approve\s+(DIR-[A-F0-9]+)(?:\s+(.+))?$", text)
    if approval:
        task_id, note = approval.group(1).upper(), approval.group(2) or "Approved"
        try:
            payload = {
                "owner_user_id": command["user_id"],
                "note": note,
                "source": "slack_command",
            }
            current = httpx.get(
                f"{settings.orchestrator_base_url}/v1/tasks/{task_id}",
                headers={"Authorization": f"Bearer {settings.shared_secret}"},
                timeout=20,
            )
            record, task_version = _task_and_version(current)
            if record.status == TaskStatus.FAILED:
                assignments = sorted(
                    record.plan.assignments if record.plan else [],
                    key=lambda item: item.sequence,
                )
                if record.next_assignment_index >= len(assignments):
                    raise ValueError("Failed task has no pending assignment")
                payload["assignment_sequence"] = assignments[
                    record.next_assignment_index
                ].sequence
                payload["retry_decision_digest"] = failed_retry_decision_digest(
                    record
                )
            payload = _signed_owner_payload(task_id, record, task_version, payload)
            response = httpx.post(
                f"{settings.orchestrator_base_url}/v1/tasks/{task_id}/approve",
                headers={"Authorization": f"Bearer {settings.shared_secret}"},
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            respond(_tag(f"*{task_id} — RESUMING*\nYour approval or clarification was recorded."), response_type="ephemeral")
        except Exception as exc:
            _log_boundary_failure("Failed to approve Director task", exc)
            respond(_tag(f"*{task_id} — BLOCKED*\nApproval was not recorded: `{type(exc).__name__}`."), response_type="ephemeral")
        return

    if not text:
        respond(
            _tag("Usage: `/director Project: … Outcome: … Constraints: …` or `/director approve DIR-… <optional clarification>`."),
            response_type="ephemeral",
        )
        return

    parent = client.chat_postMessage(
        channel=command["channel_id"],
        text=_tag(f"*DIRECTOR work order*\n{text}"),
    )
    event = {
        "client_msg_id": command.get("trigger_id"),
        "channel": command.get("channel_id"),
        "user": command.get("user_id"),
        "text": f"DIRECTOR\n{text}",
        "ts": parent["ts"],
    }
    _forward(event, client, require_prefix=True)


@app.action("director_approve")
def approval_button_handler(ack, body, client):
    ack()
    user_id = body.get("user", {}).get("id")
    channel_id = body.get("channel", {}).get("id")
    if user_id != settings.slack_owner_user_id or channel_id != settings.slack_channel_id:
        return
    task_id = "UNKNOWN"
    try:
        raw_value = body.get("actions", [{}])[0].get("value", "")
        task_id, assignment_sequence, decision_digest = parse_approval_button_value(
            raw_value
        )
        payload = {
            "owner_user_id": user_id,
            "note": "Approved from Slack",
            "source": "slack_button",
            "decision_digest": decision_digest,
        }
        if assignment_sequence is not None:
            payload["assignment_sequence"] = assignment_sequence
        current = httpx.get(
            f"{settings.orchestrator_base_url}/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {settings.shared_secret}"},
            timeout=20,
        )
        record, task_version = _task_and_version(current)
        payload = _signed_owner_payload(task_id, record, task_version, payload)
        response = httpx.post(
            f"{settings.orchestrator_base_url}/v1/tasks/{task_id}/approve",
            headers={"Authorization": f"Bearer {settings.shared_secret}"},
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        message = body.get("message", {})
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=message.get("thread_ts") or message.get("ts"),
            text=_tag(f"*{task_id} — RESUMING*\nOwner approval was recorded from Slack."),
        )
    except Exception as exc:
        _log_boundary_failure("Failed to approve Director task from button", exc)
        message = body.get("message", {})
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=message.get("thread_ts") or message.get("ts"),
            text=_tag(f"*{task_id} — BLOCKED*\nApproval was not recorded: `{type(exc).__name__}`."),
        )


def main() -> None:
    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:05 UTC | END]]
