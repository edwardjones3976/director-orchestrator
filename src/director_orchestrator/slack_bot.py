# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-23 | 04:05 UTC | BEGIN]]
# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | BEGIN]]
# Administrative attribution only; material implementation remains Cursor-authored.
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:43 UTC | BEGIN]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:23 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 06:33 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 11:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 00:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 21:33 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 20:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:53 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:06 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:49 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | BEGIN]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:05 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]]
from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .approvals import (
    build_slack_intake_attestation,
    build_slack_status_receipt_attestation,
    build_slack_gateway_proof,
    failed_retry_decision_digest,
    parse_approval_button_value,
)
from .config import Settings
# [[Codex | GPT-5.6 | 2026-08-23 19:17 UTC | BEGIN]]
from .chatgpt_bridge import (
    ChatGPTBridgeDeliveryAck,
    ChatGPTBridgeDeliveryAmbiguity,
    ChatGPTBridgeGatewayProof,
    ChatGPTBridgeRequest,
    build_chatgpt_bridge_delivery_ack_proof,
    build_chatgpt_bridge_delivery_ambiguity_proof,
    build_chatgpt_bridge_gateway_proof,
)
# [[Codex | GPT-5.6 | 2026-08-23 19:17 UTC | END]]
from .contribution_tags import DIRECTOR_SYSTEM_LABEL, wrap_human_submission
from .models import (
    OwnerApproval,
    SlackIntake,
    SlackStatusReceipt,
    TaskRecord,
    TaskStatus,
    canonical_slack_source_event_id,
    slack_status_text_digest,
)
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
from .state_witness import stamp_unwitnessed
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]
from .primary_commands import (
    ApprovalResumeRequest,
    PrimaryIngressProof,
    build_primary_ingress_proof,
)


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
    # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
    # No receipt, no state: outbound prose that asserts merged/green/reviewed/
    # authorized/the-doc-says/provider-down without citing a witness row is
    # stamped UNWITNESSED. Cosmetic; nothing transitions on prose.
    text = stamp_unwitnessed(text)
    # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]
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


def _release_event(event_id: str) -> None:
    with _seen_lock:
        _seen_events.pop(event_id, None)


def _is_work_intake_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if re.match(r"(?i)^/director\b", stripped):
        return True
    return re.match(r"(?i)^DIRECTOR\b", stripped) is not None


def _configured_slack_owner_channel(event: dict) -> bool:
    """Fail closed before any Director HTTP side effect for Slack events."""

    return (
        event.get("channel") == settings.slack_channel_id
        and event.get("user") == settings.slack_owner_user_id
    )


def _clean_text(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
def _escape_slack_text(text: str) -> str:
    """Render untrusted provider output without activating Slack control syntax."""

    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_chatgpt_alias_message(event: dict) -> bool:
    if not settings.chatgpt_bridge_enabled:
        return False
    mention = f"<@{settings.chatgpt_bridge_slack_user_id}>"
    # Only the opening address list is a command; quoted mentions and connector
    # attribution footers are content, even when Slack reports the owner as author.
    addresses = re.match(r"^\s*(?:<@[A-Z0-9]+>(?=\s|$)\s*)+", event.get("text", ""))
    return (
        addresses is not None and mention in addresses.group(0)
        and event.get("user") == settings.slack_owner_user_id
        and event.get("channel") in settings.chatgpt_bridge_channel_ids
    )


def _clean_chatgpt_alias_text(text: str) -> str:
    """Remove only the configured alias while preserving other Slack mentions."""

    return text.replace(f"<@{settings.chatgpt_bridge_slack_user_id}>", "").strip()


# Shared with the offline collector's input matching (models.canonical_slack_source_event_id).
_canonical_slack_source_event_id = canonical_slack_source_event_id


def _placeholder_bridge_proof() -> ChatGPTBridgeGatewayProof:
    return ChatGPTBridgeGatewayProof(
        issued_at=1,
        nonce="N" * 32,
        signature="0" * 64,
    )


def _post_bridge_message(client, **payload) -> str | None:
    """Attempt one Slack side effect; any exception leaves delivery ambiguous."""

    try:
        response = client.chat_postMessage(**payload)
    except Exception as exc:
        _log_boundary_failure("ChatGPT bridge Slack delivery was ambiguous", exc)
        return None
    if isinstance(response, dict):
        return response.get("ts")
    try:
        return response.get("ts")
    except Exception:
        return None


def _bridge_http_post(path: str, payload: dict, *, timeout: int) -> dict:
    response = httpx.post(
        f"{settings.orchestrator_base_url}{path}",
        headers={"Authorization": f"Bearer {settings.shared_secret}"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("bridge response was not an object")
    return body


def _signed_bridge_request(
    event: dict,
    *,
    source_event_id: str,
    thread_ts: str,
    action: str,
    prompt: str,
    approval_reference: str,
) -> ChatGPTBridgeRequest:
    unsigned = ChatGPTBridgeRequest(
        action=action,
        source_event_id=source_event_id,
        channel_id=event["channel"],
        thread_ts=thread_ts,
        user_id=event["user"],
        prompt=prompt,
        approval_reference=approval_reference,
        gateway_proof=_placeholder_bridge_proof(),
    )
    proof = build_chatgpt_bridge_gateway_proof(
        settings.slack_gateway_secret,
        settings.shared_secret,
        unsigned,
        nonce=secrets.token_urlsafe(32),
    )
    return unsigned.model_copy(update={"gateway_proof": proof})


def _signed_delivery_ack(
    request: ChatGPTBridgeRequest,
    body: dict,
    slack_message_ts: str,
) -> ChatGPTBridgeDeliveryAck:
    unsigned = ChatGPTBridgeDeliveryAck(
        source_event_id=request.source_event_id,
        request_reference=body["request_reference"],
        channel_id=request.channel_id,
        thread_ts=request.thread_ts,
        user_id=request.user_id,
        prompt_digest=body["prompt_digest"],
        delivery_client_msg_id=body["delivery_client_msg_id"],
        slack_message_ts=slack_message_ts,
        gateway_proof=_placeholder_bridge_proof(),
    )
    proof = build_chatgpt_bridge_delivery_ack_proof(
        settings.slack_gateway_secret,
        settings.shared_secret,
        unsigned,
        nonce=secrets.token_urlsafe(32),
    )
    return unsigned.model_copy(update={"gateway_proof": proof})


def _signed_delivery_ambiguity(
    request: ChatGPTBridgeRequest,
    body: dict,
) -> ChatGPTBridgeDeliveryAmbiguity:
    unsigned = ChatGPTBridgeDeliveryAmbiguity(
        source_event_id=request.source_event_id,
        request_reference=body["request_reference"],
        channel_id=request.channel_id,
        thread_ts=request.thread_ts,
        user_id=request.user_id,
        prompt_digest=body["prompt_digest"],
        delivery_client_msg_id=body["delivery_client_msg_id"],
        gateway_proof=_placeholder_bridge_proof(),
    )
    proof = build_chatgpt_bridge_delivery_ambiguity_proof(
        settings.slack_gateway_secret,
        settings.shared_secret,
        unsigned,
        nonce=secrets.token_urlsafe(32),
    )
    return unsigned.model_copy(update={"gateway_proof": proof})


def _forward_chatgpt_alias(event: dict, client) -> None:
    if not _is_chatgpt_alias_message(event):
        return
    if event.get("bot_id") or event.get("subtype") in {
        "bot_message",
        "message_changed",
        "message_deleted",
    }:
        return
    if event.get("channel") not in settings.chatgpt_bridge_channel_ids:
        return
    if event.get("user") != settings.slack_owner_user_id:
        return
    cleaned = _clean_chatgpt_alias_text(event.get("text", ""))
    approval_match = re.fullmatch(
        r"APPROVE\s+(CBR-[A-F0-9]{24})\s+(.+)",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    recovery_match = re.fullmatch(
        r"RECOVER\s+(CBR-[A-F0-9]{24})\s+(.+)",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if approval_match:
        action = "approve"
        approval_reference = approval_match.group(1).upper()
        prompt = approval_match.group(2).strip()
    elif recovery_match:
        action = "recover"
        approval_reference = recovery_match.group(1).upper()
        prompt = recovery_match.group(2).strip()
    else:
        if cleaned.upper().startswith(("APPROVE ", "RECOVER ")):
            return
        action = "request"
        approval_reference = ""
        prompt = cleaned
    if not prompt or len(prompt) > settings.chatgpt_bridge_max_prompt_chars:
        return
    thread_ts = event.get("thread_ts") or event.get("ts")
    source_event_id = _canonical_slack_source_event_id(event)
    if not source_event_id or not thread_ts:
        return
    if not _claim_event(f"chatgpt-bridge:{source_event_id}"):
        return
    request = _signed_bridge_request(
        event,
        source_event_id=source_event_id,
        thread_ts=thread_ts,
        action=action,
        prompt=prompt,
        approval_reference=approval_reference,
    )
    payload = request.model_dump(mode="json")
    try:
        body = _bridge_http_post("/v1/chatgpt-bridge", payload, timeout=100)
    except httpx.TransportError as first_exc:
        _log_boundary_failure("ChatGPT bridge transport was ambiguous", first_exc)
        try:
            body = _bridge_http_post("/v1/chatgpt-bridge", payload, timeout=20)
        except Exception as recovery_exc:
            _log_boundary_failure("ChatGPT bridge recovery read failed", recovery_exc)
            _post_bridge_message(
                client,
                channel=event["channel"],
                thread_ts=thread_ts,
                text=(
                    "*Aesop Director — ChatGPT bridge — PENDING*\n"
                    "The bridge result is not yet known. No duplicate provider dispatch "
                    "was requested. Repeat the exact approval command to recover later."
                ),
            )
            return
    except Exception as exc:
        _log_boundary_failure("ChatGPT alias bridge request failed", exc)
        paused = False
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
            # Recognize only the exact, bounded server contract; never display
            # response-controlled text or infer a paid pause from other refusals.
            if len(exc.response.content) <= 256:
                try:
                    paused = exc.response.json() == {"detail": "paid provider execution is disabled"}
                except ValueError:
                    pass
        _post_bridge_message(
            client,
            channel=event["channel"],
            thread_ts=thread_ts,
            text=(
                "*Aesop Director \u2014 ChatGPT bridge \u2014 PAUSED*\n"
                "Paid provider execution is disabled. No provider was dispatched."
                if paused else
                "*Aesop Director — ChatGPT bridge — FAILED*\n"
                "The authenticated bridge request failed closed. No automatic provider "
                "re-dispatch will occur."
            ),
        )
        return

    state = body.get("state")
    if state == "OWNER_DECISION_REQUIRED" and body.get("deliver"):
        instruction = body.get("approval_instruction")
        reference = body.get("request_reference")
        if not isinstance(instruction, str) or not instruction or not isinstance(reference, str):
            raise ValueError("bridge owner-decision response was incomplete")
        _post_bridge_message(
            client,
            channel=event["channel"],
            thread_ts=thread_ts,
            text=(
                "*Aesop Director — ChatGPT bridge — OWNER_DECISION_REQUIRED*\n"
                f"{_escape_slack_text(instruction)}"
            ),
        )
        return
    if state == "DELIVERY_AMBIGUOUS" and body.get("deliver"):
        instruction = body.get("approval_instruction")
        if not isinstance(instruction, str) or not instruction:
            raise ValueError("bridge recovery instruction was incomplete")
        _post_bridge_message(
            client,
            channel=event["channel"],
            thread_ts=thread_ts,
            text=(
                "*Aesop Director — ChatGPT bridge — DELIVERY_AMBIGUOUS*\n"
                f"{_escape_slack_text(instruction)}"
            ),
        )
        return
    if state == "DELIVERY_PENDING_ACK":
        try:
            ambiguity = _signed_delivery_ambiguity(request, body)
            ambiguity_body = _bridge_http_post(
                "/v1/chatgpt-bridge/delivery-ambiguous",
                ambiguity.model_dump(mode="json"),
                timeout=20,
            )
            instruction = ambiguity_body.get("approval_instruction")
            if not isinstance(instruction, str) or not instruction:
                raise ValueError("bridge recovery instruction was incomplete")
        except Exception as exc:
            _log_boundary_failure(
                "ChatGPT bridge pending delivery reconciliation failed", exc
            )
            _post_bridge_message(
                client,
                channel=event["channel"],
                thread_ts=thread_ts,
                text=(
                    "*Aesop Director — ChatGPT bridge — PENDING*\n"
                    "The provider was not called again, but delivery recovery could not "
                    "be armed. Wait for bounded cleanup before sending a new RECOVER "
                    "message."
                ),
            )
            return
        _post_bridge_message(
            client,
            channel=event["channel"],
            thread_ts=thread_ts,
            text=(
                "*Aesop Director — ChatGPT bridge — DELIVERY_AMBIGUOUS*\n"
                f"{_escape_slack_text(instruction)}"
            ),
        )
        return
    if state == "DISPATCHING":
        _post_bridge_message(
            client,
            channel=event["channel"],
            thread_ts=thread_ts,
            text=(
                "*Aesop Director — ChatGPT bridge — PENDING*\n"
                "The approved request is already in progress; no duplicate dispatch occurred."
            ),
        )
        return
    if state != "ANSWER_READY" or not body.get("deliver"):
        return
    answer = body.get("answer")
    delivery_attempt_id = body.get("delivery_client_msg_id")
    if not isinstance(answer, str) or not answer.strip() or not isinstance(
        delivery_attempt_id, str
    ):
        raise ValueError("bridge answer response was incomplete")
    slack_message_ts = _post_bridge_message(
        client,
        channel=event["channel"],
        thread_ts=thread_ts,
        text=f"*Aesop Director — ChatGPT bridge*\n{_escape_slack_text(answer)}",
    )
    if not slack_message_ts:
        try:
            ambiguity = _signed_delivery_ambiguity(request, body)
            _bridge_http_post(
                "/v1/chatgpt-bridge/delivery-ambiguous",
                ambiguity.model_dump(mode="json"),
                timeout=20,
            )
        except Exception as exc:
            _log_boundary_failure(
                "ChatGPT bridge delivery ambiguity recording failed", exc
            )
        return
    try:
        ack = _signed_delivery_ack(request, body, slack_message_ts)
        _bridge_http_post(
            "/v1/chatgpt-bridge/delivery-ack",
            ack.model_dump(mode="json"),
            timeout=20,
        )
    except Exception as exc:
        _log_boundary_failure("ChatGPT bridge delivery acknowledgement failed", exc)
# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]


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


def _primary_approval_payload(response, *, actor_id: str, event_id: str,
                              displayed_binding: dict | None = None,
                              decision: str = "approve") -> dict | None:
    response.raise_for_status()
    projection = response.headers.get("X-Primary-Projection-Digest")
    generation = response.headers.get("X-Primary-Writer-Generation")
    kind = response.headers.get("X-Primary-Approval-Kind")
    request_digest = response.headers.get("X-Primary-Approval-Digest")
    if projection is None:
        return None
    if not generation or not generation.isdigit() or not kind or not request_digest:
        raise ValueError("Director primary task response omitted approval bindings")
    task_id = str(response.json()["task_id"])
    if displayed_binding is not None and displayed_binding != {
        "task_id": task_id, "projection_digest": projection,
        "kind": kind, "request_digest": request_digest,
    }:
        raise ValueError("The displayed primary approval is stale; use the current request")
    path = f"/v1/primary/tasks/{task_id}/approvals/resume"
    placeholder = PrimaryIngressProof(
        issued_at=int(datetime.now(timezone.utc).timestamp()),
        nonce=secrets.token_urlsafe(32),
        signature="0" * 64,
    )
    request = ApprovalResumeRequest(
        task_id=task_id,
        command_id=event_id,
        actor_id=actor_id,
        expected_projection_digest=projection,
        expected_fencing_generation=int(generation),
        kind=kind,
        request_digest=request_digest,
        decision=decision,
        approval_event_id=event_id,
        slack_gateway_proof=placeholder,
    )
    proof = build_primary_ingress_proof(
        settings.slack_gateway_secret,
        settings.shared_secret,
        request,
        path=path,
        proof_field="slack_gateway_proof",
        issued_at=placeholder.issued_at,
        nonce=placeholder.nonce,
        channel_id=settings.slack_channel_id,
    )
    return request.model_copy(
        update={"slack_gateway_proof": proof}
    ).model_dump(mode="json")


def _intake_receipt_task_id(body: object) -> str:
    """Require one unambiguous canonical task ID from supported receipts."""

    if isinstance(body, list):
        if len(body) != 1:
            raise ValueError("Intake receipt must contain exactly one item")
        body = body[0]
    if not isinstance(body, dict):
        raise ValueError("Intake receipt must be an object")

    task_ids = [body["task_id"]] if "task_id" in body else []
    if "data" in body:
        if not isinstance(body["data"], dict):
            raise ValueError("Intake receipt data must be an object")
        if "task_id" in body["data"]:
            task_ids.append(body["data"]["task_id"])
    if not task_ids or any(
        not isinstance(task_id, str)
        or re.fullmatch(r"DIR-(?:LG-)?[A-F0-9]+", task_id) is None
        for task_id in task_ids
    ):
        raise ValueError("Intake receipt has no valid task ID")
    if len(set(task_ids)) != 1:
        raise ValueError("Intake receipt contains conflicting task IDs")
    return task_ids[0]


def _forward(event: dict, client, *, require_prefix: bool) -> None:
    if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_changed", "message_deleted"}:
        return
    if event.get("channel") != settings.slack_channel_id:
        return
    if event.get("user") != settings.slack_owner_user_id:
        return
    text = _clean_text(event.get("text", ""))
    if require_prefix and not _is_work_intake_command(text):
        return
    if not text:
        return

    thread_ts = event.get("thread_ts") or event.get("ts")
    source_event_id = _canonical_slack_source_event_id(event) or (
        event.get("client_msg_id") or event.get("event_ts") or event.get("ts")
    )
    if not source_event_id or not thread_ts or not _claim_event(source_event_id):
        return
    # Routing steering is owner-set deployment configuration, never parsed out of
    # `text`: rule 6 treats the Slack message as untrusted data. The attestation
    # below covers the whole intake, so these fields are bound to the gateway
    # secret exactly like every other field.
    intake = SlackIntake(
        source_event_id=source_event_id,
        channel_id=event["channel"],
        thread_ts=thread_ts,
        user_id=event["user"],
        text=text,
        allocation_profile=settings.intake_allocation_profile,
        primary_required_capabilities=settings.intake_primary_required_capabilities,
        received_at=datetime.now(timezone.utc),
    )
    payload = intake.model_dump(mode="json")
    headers = {
        "Authorization": f"Bearer {settings.shared_secret}",
        **build_slack_intake_attestation(
            settings.slack_gateway_secret,
            settings.shared_secret,
            intake,
        ),
    }
    target = settings.n8n_webhook_url or f"{settings.orchestrator_base_url}/v1/intake"
    try:
        response = httpx.post(
            target,
            headers=headers,
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        if response.status_code != 202:
            raise ValueError("Intake receipt did not return HTTP 202")
        task_id = _intake_receipt_task_id(response.json())
        client.chat_postMessage(channel=event["channel"], thread_ts=thread_ts, text=_tag(f"*{task_id} — REQUEST RECEIVED*\nThe Director returned a valid intake receipt. Execution remains subject to the configured safety gates."))
    except Exception as exc:
        _log_boundary_failure("Director intake acceptance could not be confirmed", exc)
        client.chat_postMessage(
            channel=event["channel"],
            thread_ts=thread_ts,
            text=_tag(
                "*DIRECTOR — BLOCKED*\nAcceptance could not be confirmed. "
                "The request state must be reconciled before retrying."
            ),
        )


def _status_receipt_task_id(body: object) -> str:
    if not isinstance(body, dict):
        raise ValueError("Status receipt response must be an object")
    task_id = body.get("task_id")
    if not isinstance(task_id, str) or re.fullmatch(r"DIR-LG-[A-F0-9]+", task_id) is None:
        raise ValueError("Status receipt has no valid task ID")
    return task_id


def _forward_status_receipt(event: dict, client) -> None:
    if event.get("bot_id") or event.get("subtype") in {
        "bot_message",
        "message_changed",
        "message_deleted",
    }:
        return
    if event.get("channel") != settings.slack_channel_id:
        return
    if event.get("user") != settings.slack_owner_user_id:
        return
    text = _clean_text(event.get("text", ""))
    if not text or _is_work_intake_command(text):
        return

    thread_ts = event.get("thread_ts") or event.get("ts")
    source_event_id = _canonical_slack_source_event_id(event) or (
        event.get("client_msg_id") or event.get("event_ts") or event.get("ts")
    )
    if not source_event_id or not thread_ts or not _claim_event(source_event_id):
        return

    binding_url = (
        f"{settings.orchestrator_base_url}/v1/slack-thread-bindings"
        f"?channel_id={event['channel']}&thread_ts={thread_ts}"
    )
    headers = {"Authorization": f"Bearer {settings.shared_secret}"}
    try:
        binding_response = httpx.get(binding_url, headers=headers, timeout=20)
        if binding_response.status_code == 404:
            _release_event(source_event_id)
            client.chat_postMessage(
                channel=event["channel"],
                thread_ts=thread_ts,
                text=_tag(
                    "*DIRECTOR — STATUS NOT RECORDED*\n"
                    "This thread is not bound to a known task."
                ),
            )
            return
        if binding_response.status_code == 409:
            _release_event(source_event_id)
            client.chat_postMessage(
                channel=event["channel"],
                thread_ts=thread_ts,
                text=_tag(
                    "*DIRECTOR — STATUS NOT RECORDED*\n"
                    "This thread matches more than one task binding."
                ),
            )
            return
        binding_response.raise_for_status()
        task_id = binding_response.json()["task_id"]
        receipt = SlackStatusReceipt(
            source_event_id=source_event_id,
            channel_id=event["channel"],
            thread_ts=thread_ts,
            user_id=event["user"],
            task_id=task_id,
            text_digest=slack_status_text_digest(text),
            received_at=datetime.now(timezone.utc),
        )
        payload = receipt.model_dump(mode="json")
        post_headers = {
            **headers,
            **build_slack_status_receipt_attestation(
                settings.slack_gateway_secret,
                settings.shared_secret,
                receipt,
            ),
        }
        response = httpx.post(
            f"{settings.orchestrator_base_url}/v1/status-receipts",
            headers=post_headers,
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        recorded = response.json()
        task_id = _status_receipt_task_id(recorded)
        if recorded.get("deduplicated"):
            notice = (
                f"*{task_id} — STATUS ALREADY RECORDED*\n"
                "The Director returned the existing deduplicated receipt."
            )
        else:
            notice = (
                f"*{task_id} — STATUS RECORDED*\n"
                "The Director stored a digest-only status receipt with no new work."
            )
        client.chat_postMessage(
            channel=event["channel"],
            thread_ts=thread_ts,
            text=_tag(notice),
        )
    except Exception as exc:
        _release_event(source_event_id)
        _log_boundary_failure("Director status receipt could not be confirmed", exc)
        client.chat_postMessage(
            channel=event["channel"],
            thread_ts=thread_ts,
            text=_tag(
                "*DIRECTOR — BLOCKED*\n"
                "Status receipt acceptance could not be confirmed. "
                "The request state must be reconciled before retrying."
            ),
        )


@app.event("app_mention")
def mention_handler(event, client):
    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
    if _is_chatgpt_alias_message(event):
        _forward_chatgpt_alias(event, client)
        return
    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]
    if not _configured_slack_owner_channel(event):
        return
    text = _clean_text(event.get("text", ""))
    if _is_work_intake_command(text):
        _forward(event, client, require_prefix=True)
        return
    _forward_status_receipt(event, client)


@app.event("message")
def message_handler(event, client):
    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
    if _is_chatgpt_alias_message(event):
        _forward_chatgpt_alias(event, client)
        return
    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]
    if not _configured_slack_owner_channel(event):
        return
    text = _clean_text(event.get("text", ""))
    if _is_work_intake_command(text):
        _forward(event, client, require_prefix=True)
        return
    _forward_status_receipt(event, client)


@app.command("/director")
def command_handler(ack, command, client, respond):
    ack()
    if command.get("channel_id") != settings.slack_channel_id:
        return
    if command.get("user_id") != settings.slack_owner_user_id:
        respond(_tag("Aesop Director only accepts owner commands in the configured private channel."), response_type="ephemeral")
        return
    text = command.get("text", "").strip()
    approval = re.match(r"(?is)^approve\s+(DIR-(?:LG-)?[A-F0-9]+)(?:\s+(.+))?$", text)
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
            primary_payload = _primary_approval_payload(
                current,
                actor_id=command["user_id"],
                event_id=str(command.get("trigger_id") or secrets.token_urlsafe(24)),
            )
            if primary_payload is not None:
                response = httpx.post(
                    f"{settings.orchestrator_base_url}/v1/primary/tasks/{task_id}/approvals/resume",
                    headers={"Authorization": f"Bearer {settings.shared_secret}"},
                    json=primary_payload,
                    timeout=20,
                )
                response.raise_for_status()
                respond(_tag(f"*{task_id} — RESUMING*\nYour approval was recorded."), response_type="ephemeral")
                return
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
            respond(_tag(f"*{task_id} — APPROVAL RESPONSE UNCONFIRMED*\nThe response failed: `{type(exc).__name__}`. The decision may already be recorded; check the current task before trying again."), response_type="ephemeral")
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


@app.action(re.compile(r"^director_primary_(approve|reject)$"))
def primary_approval_button_handler(ack, body, client):
    ack()
    user_id = body.get("user", {}).get("id")
    channel_id = body.get("channel", {}).get("id")
    if user_id != settings.slack_owner_user_id or channel_id != settings.slack_channel_id:
        return
    try:
        action = body.get("actions", [{}])[0]
        binding = json.loads(action.get("value", ""))
        if not isinstance(binding, dict) or set(binding) != {
                "task_id", "projection_digest", "kind", "request_digest"}:
            raise ValueError("Invalid primary approval binding")
        task_id = binding["task_id"]
        if not isinstance(task_id, str) or not re.fullmatch(r"DIR-LG-[A-F0-9]+", task_id):
            raise ValueError("Invalid primary task identity")
        event_id = action.get("action_ts") or body.get("action_ts")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Missing Slack action identity")
        current = httpx.get(
            f"{settings.orchestrator_base_url}/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {settings.shared_secret}"}, timeout=20)
        payload = _primary_approval_payload(current, actor_id=user_id, event_id=event_id,
            displayed_binding=binding,
            decision="deny" if action["action_id"] == "director_primary_reject" else "approve")
        if payload is None:
            raise ValueError("Task is no longer awaiting a primary decision")
        response = httpx.post(
            f"{settings.orchestrator_base_url}/v1/primary/tasks/{task_id}/approvals/resume",
            headers={"Authorization": f"Bearer {settings.shared_secret}"},
            json=payload, timeout=20)
        response.raise_for_status()
        # The primary ledger owns the durable lifecycle reply. An ephemeral
        # acknowledgement cannot duplicate its terminal task result.
        client.chat_postEphemeral(channel=channel_id, user=user_id,
            text=_tag("Your decision was recorded. The Director will reply in the task thread."))
    except Exception as exc:
        _log_boundary_failure("primary_approval", exc)
        client.chat_postEphemeral(channel=channel_id, user=user_id,
            text=_tag("The decision was not confirmed. Check the current task before trying again; the displayed request may be stale."))


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
        primary_payload = _primary_approval_payload(
            current,
            actor_id=user_id,
            event_id=str(body.get("action_ts") or secrets.token_urlsafe(24)),
        )
        if primary_payload is not None:
            response = httpx.post(
                f"{settings.orchestrator_base_url}/v1/primary/tasks/{task_id}/approvals/resume",
                headers={"Authorization": f"Bearer {settings.shared_secret}"},
                json=primary_payload,
                timeout=20,
            )
            response.raise_for_status()
            message = body.get("message", {})
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=message.get("thread_ts") or message.get("ts"),
                text=_tag(f"*{task_id} — RESUMING*\nOwner approval was recorded from Slack."),
            )
            return
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
            text=_tag(f"*{task_id} — APPROVAL RESPONSE UNCONFIRMED*\nThe response failed: `{type(exc).__name__}`. The decision may already be recorded; check the current task before trying again."),
        )


def main() -> None:
    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:05 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:49 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:06 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:53 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 20:28 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 21:33 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 00:19 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 11:40 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:38 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 06:33 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 11:26 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:23 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:43 UTC | END]]

# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-23 | 04:05 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | END]]
