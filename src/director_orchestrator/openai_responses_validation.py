# [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | BEGIN]]
"""Explicit OpenAI Responses validation/capture route for LIVE_OK evidence.

Native Codex host validation remains the default Windows bridge path and still
refuses before launch when signed total_token_limit cannot be enforced. This
module is the separate Responses API route that reuses OpenAIResponsesPacketTransport,
ValidationCapture/CaptureVerifier, and enrolled response metering.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

from .packet_contracts import canonical_json_bytes, validate_packet
from .provider_validation import ValidationError, digest, MAX_CAPTURE_BYTES


RESPONSES_VALIDATION_TRANSPORT = "responses-api"


@dataclass(frozen=True, slots=True)
class OpenAIResponsesValidationRoute:
    """Operator-injected Responses validation binding; never discovered from env."""

    transport: object
    configuration_digest: str
    total_token_limit: int
    expected_model: str
    meter_enrollment_digest: str

    def __post_init__(self) -> None:
        if type(self.total_token_limit) is not int or self.total_token_limit < 1:
            raise ValueError("Responses validation requires a positive signed total_token_limit")
        if not self.expected_model or not self.configuration_digest or not self.meter_enrollment_digest:
            raise ValueError("Responses validation route bindings are incomplete")
        meter = getattr(self.transport, "response_meter", None)
        if meter is None:
            raise ValueError("Responses validation requires an enrolled response meter")
        if getattr(self.transport, "model", None) != self.expected_model:
            raise ValueError("Responses validation model binding mismatch")


def responses_validation_configuration_digest(
    *,
    model: str,
    api_url: str,
    credential_digest: str,
    organization_id: str,
    api_project_id: str,
    meter_enrollment_digest: str,
    total_token_limit: int,
) -> str:
    """Pin Responses validation identity without inventing account proof.

    Organization and project ids here are the enrollment routing intention that
    settlement later matches against provider-returned headers. Missing provider
    headers still refuse qualification.
    """
    if (not model or not api_url or not credential_digest or not organization_id
            or not api_project_id or not meter_enrollment_digest
            or type(total_token_limit) is not int or total_token_limit < 1):
        raise ValidationError("Responses validation configuration pin is incomplete")
    return digest(canonical_json_bytes({
        "transport": RESPONSES_VALIDATION_TRANSPORT,
        "model": model,
        "api_url": api_url,
        "credential_digest": credential_digest,
        "organization_id": organization_id,
        "api_project_id": api_project_id,
        "meter_enrollment_digest": meter_enrollment_digest,
        "total_token_limit": total_token_limit,
    }))


def normalize_openai_responses_capture(work: Mapping, raw: bytes, *, expected_model: str) -> dict:
    """Qualify one Responses API terminal envelope; never accept arbitrary success JSON."""
    if not raw or len(raw) > MAX_CAPTURE_BYTES:
        raise ValidationError("native capture size invalid")
    try:
        native = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("OpenAI Responses capture is not valid JSON") from exc
    if not isinstance(native, dict) or native.get("object") != "response":
        raise ValidationError("OpenAI Responses capture object binding is required")
    if (native.get("status") != "completed" or native.get("error") is not None
            or native.get("incomplete_details") is not None):
        raise ValidationError("OpenAI Responses capture is not a completed terminal result")
    if native.get("model") != expected_model:
        raise ValidationError("OpenAI Responses capture model differs from route binding")
    run_reference = native.get("id")
    if not isinstance(run_reference, str) or not run_reference.strip():
        raise ValidationError("OpenAI Responses capture lacks authenticated response id")
    output = native.get("output")
    if not isinstance(output, list):
        raise ValidationError("OpenAI Responses output is malformed")
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            raise ValidationError("OpenAI Responses output is malformed")
        if item.get("type") == "reasoning" and item.get("status") in {None, "completed"}:
            continue
        if (item.get("type") != "message" or item.get("role") != "assistant"
                or item.get("status") != "completed"
                or not isinstance(item.get("content"), list)):
            raise ValidationError("OpenAI Responses contains non-completed or unexpected output")
        for content in item["content"]:
            if (not isinstance(content, dict) or content.get("type") != "output_text"
                    or not isinstance(content.get("text"), str)):
                raise ValidationError("OpenAI Responses text is malformed or refused")
            chunks.append(content["text"])
    outcome = "".join(chunks)
    if not outcome.strip():
        raise ValidationError("OpenAI Responses lacks output text")
    usage = native.get("usage")
    if (not isinstance(usage, dict)
            or any(type(usage.get(key)) is not int or usage[key] < 0
                   for key in ("input_tokens", "output_tokens", "total_tokens"))
            or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]):
        raise ValidationError("OpenAI Responses usage is missing or inconsistent")
    from .primary_transports import _bound_research_result
    result = _bound_research_result(
        "openai", work, run_reference=run_reference, outcome=outcome,
    )
    result["usage"] = {
        "model": expected_model,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "cost_usd": None,
    }
    return validate_packet("result", result)
# [[Grok Bot | Managed model unknown | 2026-09-13 | 17:50 UTC | END]]
