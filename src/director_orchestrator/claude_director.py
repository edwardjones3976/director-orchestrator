# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:00 UTC | BEGIN]]
"""Typed Claude Director over an explicitly enrolled native v1 packet boundary.

The binder and authenticator are trusted application dependencies, never model
payloads. This module neither discovers a CLI nor enrolls/authenticates a host.
An integration must verify current authority and native execution provenance,
including process timeout/output/tool limits, before supplying these dependencies.
"""
from __future__ import annotations

import asyncio
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from .config import Settings
from .director import OpenAIDirector, _strict_schema
from .interactions import ProviderTransport
from .langgraph_runtime import PacketBoundaryAdapter, ProviderAdapterContext, RawProviderTransport
from .models import ProviderName
from .packet_contracts import PacketContractError, canonical_json_bytes, document_digest, validate_packet


@dataclass(frozen=True, slots=True)
class ClaudeDirectorBinding:
    work_packet: Mapping[str, Any]
    context: ProviderAdapterContext


class ClaudeDirectorAuthenticator(Protocol):
    def __call__(
        self,
        binding: ClaudeDirectorBinding,
        *,
        model_name: str,
        result: Mapping[str, Any] | None,
    ) -> None:
        """Raise unless the exact call is authorized or its result authenticated.

        With result=None verify the enrolled Claude native transport, authority,
        writer generation, process bounds, budget admission and idempotency. With
        a result verify authenticated native provenance for this exact binding and
        expected model. A provider/model string in a payload is not proof.
        """
        ...


class ClaudeDirectorPacketTransport:
    """One bounded call; no retry, discovery, fallback, or authority synthesis."""

    maximum_timeout_seconds = 180
    maximum_output_bytes = 65_536

    def __init__(
        self,
        native_transport: RawProviderTransport,
        *,
        model_name: str,
        bind_request: Callable[[str], ClaudeDirectorBinding],
        authenticate: ClaudeDirectorAuthenticator,
    ) -> None:
        if not isinstance(model_name, str) or not model_name.strip() or len(model_name) > 200:
            raise ValueError("an exact enrolled Claude model name is required")
        if not callable(bind_request) or not callable(authenticate) or not callable(getattr(native_transport, "send", None)):
            raise ValueError("native transport, trusted binder and authenticator are required")
        self.model_name = model_name
        self._bind_request = bind_request
        self._authenticate = authenticate
        self._boundary = PacketBoundaryAdapter("claude", native_transport)

    def structured(self, instructions: str, input_text: str, model: type[BaseModel], name: str):
        request = canonical_json_bytes({
            "executing_provider": "claude",
            "model": self.model_name,
            "instructions": instructions,
            "input": input_text,
            "response_format": _strict_schema(model, name),
            "result_instruction": "Return the typed response as a JSON object encoded in the v1 result outcome string. Use no tools or external effects.",
        }).decode("utf-8")
        if len(request.encode("utf-8")) > 524_288:
            raise PacketContractError("Claude Director request exceeds the bounded input limit")
        supplied = self._bind_request(request)
        if not isinstance(supplied, ClaudeDirectorBinding):
            raise PacketContractError("Claude Director requires an explicit work/context binding")
        work = validate_packet("work", supplied.work_packet)
        context = supplied.context
        if not isinstance(context, ProviderAdapterContext):
            raise PacketContractError("Claude Director requires a dispatch context")
        if work["objective"] != request or not hmac.compare_digest(document_digest(work), context.work_packet_digest):
            raise PacketContractError("Claude Director request does not match its authorized packet")
        for field in ("task_id", "assignment_id", "idempotency_key"):
            if work[field] != getattr(context, field):
                raise PacketContractError("Claude Director context identity does not match its packet")
        if context.fencing_generation < 1 or not context.writer_token_digest:
            raise PacketContractError("Claude Director requires a fenced writer binding")
        limits = work["limits"]
        for field in ("timeout_seconds", "budget_usd", "max_hops", "max_revisions"):
            if limits[field] != getattr(context, field):
                raise PacketContractError("Claude Director context limits do not match its packet")
        if not 0 < context.timeout_seconds <= self.maximum_timeout_seconds:
            raise PacketContractError("Claude Director timeout exceeds the bounded call limit")
        if work["allowed_tools"] or context.allowed_tools or work["allowed_external_effects"] or context.allowed_external_effects:
            raise PacketContractError("Claude Director calls may not use tools or external effects")
        binding = ClaudeDirectorBinding(work, context)
        # Trusted application enrollment must fail before any native invocation.
        self._authenticate(binding, model_name=self.model_name, result=None)
        result = self._boundary.dispatch(work, context=context)
        if result["status"] != "SUCCEEDED" or result["side_effects"]:
            raise PacketContractError("Claude Director did not return a successful effect-free result")
        if result["usage"].get("model") != self.model_name:
            raise PacketContractError("Claude Director result does not identify the enrolled model")
        outcome = result["outcome"]
        if len(outcome.encode("utf-8")) > self.maximum_output_bytes:
            raise PacketContractError("Claude Director outcome exceeds the bounded output limit")
        self._authenticate(binding, model_name=self.model_name, result=result)
        return model.model_validate_json(outcome)


class ClaudeDirector(OpenAIDirector):
    """Reuse provider-neutral plan/review shapes; execute only native Claude."""

    provider = ProviderName.CLAUDE
    transport = ProviderTransport.CLAUDE_NATIVE_HOST

    @property
    def model_name(self) -> str:
        return self._packet_transport.model_name

    def __init__(self, settings: Settings, transport: ClaudeDirectorPacketTransport):
        if not isinstance(transport, ClaudeDirectorPacketTransport):
            raise RuntimeError("Claude Director requires an explicitly enrolled native packet transport")
        self._packet_transport = transport
        super().__init__(settings)

    async def _structured(self, input_text: str, model: type[BaseModel], name: str):
        # The qualified native transport enforces the context deadline. Cancelling
        # a Python await alone is not a process kill or proof a dispatch stopped.
        return await asyncio.to_thread(
            self._packet_transport.structured, self.instructions, input_text, model, name,
        )
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
