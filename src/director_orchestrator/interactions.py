# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:30 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:17 UTC | BEGIN]]
"""Backend-owned, digest-bound provider interaction evidence.

Provider payloads are data inputs to :class:`ProviderTransportEvidence`; they do
not carry an authoritative lifecycle state.  Only the transition helpers in this
module create :class:`ProviderInteractionEvent` values.  Persistence is left to
the caller so a store can append the returned event in the same transaction as
the task projection it authorizes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .workload_policy import MAX_PROVIDER_ATTEMPTS, SUPPORTED_ATTEMPT_LIMITS

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


class CommunicationEvidenceFailure(RuntimeError):
    """Fail-closed signal for absent, invalid, or mismatched communication evidence."""

    code = "COMMUNICATION_EVIDENCE_FAILURE"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class ProviderInteractionState(StrEnum):
    DRAFTED = "DRAFTED"
    ATTEMPTED = "ATTEMPTED"
    DISPATCHED = "DISPATCHED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESPONDED = "RESPONDED"
    COMPLETED = "COMPLETED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class InteractionProvider(StrEnum):
    """Closed provider identity independent of task-model imports.

    Keeping this enum local prevents a models.py -> interactions.py -> models.py
    cycle when TaskRecord adopts the event projection.  String-valued provider
    enums from existing callers normalize through ``InteractionProvider(value)``.
    """

    OPENAI = "openai"
    CLAUDE = "claude"
    CURSOR = "cursor"
    GROK = "grok"
    GEMINI = "gemini"
    PERPLEXITY = "perplexity"
    # Enrolled 2026-09-27; GLM served through the Claude Code CLI is never CLAUDE.
    ZAI = "zai"


class ProviderInteractionPhase(StrEnum):
    PLAN = "plan"
    DISPATCH = "dispatch"
    POLL = "poll"
    CALLBACK = "callback"
    REVISION = "revision"
    ACCEPTANCE_REVIEW = "acceptance_review"
    DEBATE_COUNTER_BIAS = "debate_counter_bias"
    DEBATE_SYNTHESIS = "debate_synthesis"
    DEBATE_TIE_JUDGMENT = "debate_tie_judgment"
    USAGE_CHECK = "usage_check"
    SLACK_DELIVERY = "slack_delivery"


class ProviderTransport(StrEnum):
    DIRECTOR_BACKEND = "director_backend"
    OPENAI_RESPONSES_API = "openai_responses_api"
    CLAUDE_ROUTINE = "claude_routine"
    CLAUDE_NATIVE_HOST = "claude_native_host"
    CLAUDE_CALLBACK = "claude_callback"
    CURSOR_AGENTS_API = "cursor_agents_api"
    GEMINI_INTERACTIONS_API = "gemini_interactions_api"
    XAI_RESPONSES_API = "xai_responses_api"
    GROK_CLI = "grok_cli"
    PERPLEXITY_API = "perplexity_api"
    # The Claude Code CLI on Z.ai's Coding Plan endpoint, run natively or by the
    # Windows host bridge; its own transport, never a Claude route.
    ZAI_CODING_CLI = "zai_coding_cli"
    SLACK = "slack"
    MANUAL_OWNER = "manual_owner"
    SIMULATION = "simulation"


class TransportEvidenceSource(StrEnum):
    BACKEND_OBSERVATION = "backend_observation"
    AUTHENTICATED_PROVIDER = "authenticated_provider"
    OWNER_ATTESTED = "owner_attested"
    UNVERIFIED = "unverified"
    SIMULATION = "simulation"


class DirectorIdentity(BaseModel):
    """Prospective generation identity; a leadership role grants no execution rights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Historical v3 records used Claude; do not reinterpret an omitted legacy field.
    # New generations explicitly bind the current project Director in the engine.
    acting_project_director: Literal["claude", "openai"] = "claude"
    provider: Literal["openai", "claude"]
    transport: ProviderTransport
    model_name: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_transport(self) -> DirectorIdentity:
        expected = {
            "openai": ProviderTransport.OPENAI_RESPONSES_API,
            "claude": ProviderTransport.CLAUDE_NATIVE_HOST,
        }[self.provider]
        if self.transport != expected:
            raise ValueError("Director provider and transport identity disagree")
        return self


TERMINAL_INTERACTION_STATES = frozenset(
    {
        ProviderInteractionState.VERIFIED,
        ProviderInteractionState.FAILED,
        ProviderInteractionState.UNAVAILABLE,
        ProviderInteractionState.UNKNOWN,
    }
)


LEGAL_INTERACTION_TRANSITIONS: dict[
    ProviderInteractionState, frozenset[ProviderInteractionState]
] = {
    ProviderInteractionState.DRAFTED: frozenset(
        {ProviderInteractionState.ATTEMPTED, ProviderInteractionState.FAILED}
    ),
    ProviderInteractionState.ATTEMPTED: frozenset(
        {
            ProviderInteractionState.DISPATCHED,
            ProviderInteractionState.FAILED,
            ProviderInteractionState.UNKNOWN,
        }
    ),
    ProviderInteractionState.DISPATCHED: frozenset(
        {
            ProviderInteractionState.ACKNOWLEDGED,
            ProviderInteractionState.RESPONDED,
            ProviderInteractionState.FAILED,
            ProviderInteractionState.UNKNOWN,
        }
    ),
    ProviderInteractionState.ACKNOWLEDGED: frozenset(
        {
            ProviderInteractionState.RESPONDED,
            ProviderInteractionState.FAILED,
            ProviderInteractionState.UNAVAILABLE,
            ProviderInteractionState.UNKNOWN,
        }
    ),
    ProviderInteractionState.RESPONDED: frozenset(
        {
            ProviderInteractionState.COMPLETED,
            ProviderInteractionState.FAILED,
            ProviderInteractionState.UNKNOWN,
        }
    ),
    ProviderInteractionState.COMPLETED: frozenset(
        {ProviderInteractionState.VERIFIED, ProviderInteractionState.FAILED}
    ),
    ProviderInteractionState.VERIFIED: frozenset(),
    ProviderInteractionState.FAILED: frozenset(),
    ProviderInteractionState.UNAVAILABLE: frozenset(),
    ProviderInteractionState.UNKNOWN: frozenset(),
}


def canonical_content_digest(value: str | bytes) -> str:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(encoded).hexdigest()


def canonical_identifier_digest(value: str) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def acceptance_review_candidate_digest(
    *,
    task_id: str,
    plan_generation: int,
    assignment_sequence: int,
    provider: InteractionProvider | str,
    provider_run_id: str,
    provider_event_digest: str,
    provider_result_digest: str,
) -> str:
    """Bind one acceptance review to the exact provider candidate it reviewed."""

    return _canonical_digest(
        {
            "binding_version": "v1",
            "task_id": task_id,
            "plan_generation": plan_generation,
            "assignment_sequence": assignment_sequence,
            "provider": InteractionProvider(provider).value,
            "provider_run_id_digest": canonical_identifier_digest(provider_run_id),
            "provider_event_digest": provider_event_digest,
            "provider_result_digest": provider_result_digest,
        }
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("interaction evidence timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class ProviderTransportEvidence(BaseModel):
    """Sanitized transport facts; deliberately contains no lifecycle authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_version: str = Field(default="v1", pattern=r"^v1$")
    transport: ProviderTransport
    source: TransportEvidenceSource
    execution_mode: Literal["live", "dry_run", "simulation", "shadow"] = "live"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key_digest: str = Field(pattern=_DIGEST_PATTERN)
    request_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    response_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    request_id_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    response_id_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    provider_run_id_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    provider_session_id_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    correlation_id_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    native_status: str | None = Field(default=None, min_length=1, max_length=100)
    http_status: int | None = Field(default=None, ge=100, le=599)
    result_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    error_code: str | None = Field(default=None, min_length=1, max_length=100)
    ambiguity_reason: str | None = Field(default=None, min_length=1, max_length=500)
    contact_attempt_ordinal: int | None = Field(default=None, ge=1, le=MAX_PROVIDER_ATTEMPTS)
    sanitized_excerpt_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    evidence_digest: str = Field(pattern=_DIGEST_PATTERN)

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        return _utc(value)

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"evidence_digest"})

    @model_validator(mode="after")
    def validate_evidence_digest(self) -> ProviderTransportEvidence:
        expected = _canonical_digest(self.canonical_payload())
        if not hmac.compare_digest(self.evidence_digest, expected):
            raise ValueError("transport evidence digest mismatch")
        return self


def build_transport_evidence(
    *,
    transport: ProviderTransport,
    source: TransportEvidenceSource,
    idempotency_key: str,
    execution_mode: Literal["live", "dry_run", "simulation", "shadow"] = "live",
    observed_at: datetime | None = None,
    request: str | bytes | None = None,
    response: str | bytes | None = None,
    request_id: str | None = None,
    response_id: str | None = None,
    provider_run_id: str | None = None,
    provider_session_id: str | None = None,
    correlation_id: str | None = None,
    native_status: str | None = None,
    http_status: int | None = None,
    result: str | bytes | None = None,
    error_code: str | None = None,
    ambiguity_reason: str | None = None,
    contact_attempt_ordinal: int | None = None,
    sanitized_excerpt: str | bytes | None = None,
) -> ProviderTransportEvidence:
    """Build evidence from backend-observed values without retaining raw content."""

    payload: dict[str, Any] = {
        "evidence_version": "v1",
        "transport": transport,
        "source": source,
        "execution_mode": execution_mode,
        "observed_at": _utc(observed_at or datetime.now(timezone.utc)),
        "idempotency_key_digest": canonical_identifier_digest(idempotency_key),
        "request_digest": canonical_content_digest(request) if request is not None else None,
        "response_digest": canonical_content_digest(response) if response is not None else None,
        "request_id_digest": canonical_identifier_digest(request_id) if request_id else None,
        "response_id_digest": canonical_identifier_digest(response_id) if response_id else None,
        "provider_run_id_digest": (
            canonical_identifier_digest(provider_run_id) if provider_run_id else None
        ),
        "provider_session_id_digest": (
            canonical_identifier_digest(provider_session_id)
            if provider_session_id
            else None
        ),
        "correlation_id_digest": (
            canonical_identifier_digest(correlation_id) if correlation_id else None
        ),
        "native_status": native_status,
        "http_status": http_status,
        "result_digest": canonical_content_digest(result) if result is not None else None,
        "error_code": error_code,
        "ambiguity_reason": ambiguity_reason,
        "contact_attempt_ordinal": contact_attempt_ordinal,
        "sanitized_excerpt_digest": (
            canonical_content_digest(sanitized_excerpt)
            if sanitized_excerpt is not None
            else None
        ),
    }
    serializable = ProviderTransportEvidence.model_construct(
        **payload, evidence_digest="0" * 64
    ).canonical_payload()
    payload["evidence_digest"] = _canonical_digest(serializable)
    return ProviderTransportEvidence.model_validate(payload)


class ProviderInteractionEvent(BaseModel):
    """One immutable backend transition bound to exact sanitized transport evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_version: Literal["v1", "v2", "v3"] = "v2"
    task_id: str = Field(min_length=1, max_length=255)
    interaction_id: str = Field(min_length=1, max_length=255)
    plan_generation: int = Field(ge=0)
    assignment_sequence: int | None = Field(default=None, ge=1, le=20)
    provider: InteractionProvider
    phase: ProviderInteractionPhase
    attempt: int = Field(default=1, ge=1, le=10)
    state: ProviderInteractionState
    transition_ordinal: int = Field(ge=1)
    effect_key: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    previous_event_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    candidate_binding_digest: str | None = Field(
        default=None, pattern=_DIGEST_PATTERN
    )
    request_payload_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    consumed_artifact_digests: tuple[str, ...] = Field(
        default_factory=tuple, max_length=20
    )
    transport_evidence: ProviderTransportEvidence
    director_identity: DirectorIdentity | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_digest: str = Field(pattern=_DIGEST_PATTERN)

    @field_validator("recorded_at")
    @classmethod
    def normalize_recorded_at(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_validator("consumed_artifact_digests", mode="before")
    @classmethod
    def normalize_consumed_artifact_digests(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        normalized = tuple(sorted(set(str(item) for item in value)))
        if any(
            len(item) != 64
            or item.lower() != item
            or any(character not in "0123456789abcdef" for character in item)
            for item in normalized
        ):
            raise ValueError("consumed artifact digests must be canonical SHA-256 values")
        return normalized

    @model_validator(mode="after")
    def validate_candidate_and_request_bindings(self) -> ProviderInteractionEvent:
        if self.event_version == "v3":
            if self.director_identity is None or (
                self.director_identity.provider != self.provider.value
                or self.director_identity.transport != self.transport_evidence.transport
                or self.assignment_sequence is not None
            ):
                raise ValueError("v3 Director event requires exact executing role identity")
        elif self.director_identity is not None:
            raise ValueError("historical interaction versions cannot acquire Director identity")
        if self.event_version == "v1" and (
            self.candidate_binding_digest is not None
            or self.request_payload_digest is not None
            or self.consumed_artifact_digests
        ):
            raise ValueError("v1 interaction events cannot carry v2 request bindings")
        if (
            self.candidate_binding_digest is not None
            and self.phase is not ProviderInteractionPhase.ACCEPTANCE_REVIEW
        ):
            raise ValueError("candidate binding is limited to acceptance-review interactions")
        if self.consumed_artifact_digests:
            if self.phase not in {
                ProviderInteractionPhase.DISPATCH,
                ProviderInteractionPhase.REVISION,
            }:
                raise ValueError("artifact consumption is limited to work requests")
            if self.request_payload_digest is None:
                raise ValueError("artifact consumption requires an exact request payload digest")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        excluded = {"event_digest"}
        if self.event_version != "v3":
            excluded.add("director_identity")
        if self.event_version == "v1":
            excluded.update(
                {
                    "candidate_binding_digest",
                    "request_payload_digest",
                    "consumed_artifact_digests",
                }
            )
        return self.model_dump(mode="json", exclude=excluded)

    @model_validator(mode="after")
    def validate_event_digest(self) -> ProviderInteractionEvent:
        expected = _canonical_digest(self.canonical_payload())
        if not hmac.compare_digest(self.event_digest, expected):
            raise ValueError("provider interaction event digest mismatch")
        return self


class ProviderInteractionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    interaction_id: str
    provider: InteractionProvider
    assignment_sequence: int | None
    state: ProviderInteractionState
    event_count: int = Field(ge=1)
    latest_event_digest: str = Field(pattern=_DIGEST_PATTERN)
    latest_evidence_digest: str = Field(pattern=_DIGEST_PATTERN)
    verified_result_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)


def _event_payload(
    *,
    task_id: str,
    interaction_id: str,
    plan_generation: int,
    assignment_sequence: int | None,
    provider: InteractionProvider,
    phase: ProviderInteractionPhase,
    attempt: int,
    state: ProviderInteractionState,
    transition_ordinal: int,
    effect_key: str | None,
    previous_event_digest: str | None,
    candidate_binding_digest: str | None,
    request_payload_digest: str | None,
    consumed_artifact_digests: tuple[str, ...],
    transport_evidence: ProviderTransportEvidence,
    recorded_at: datetime,
    director_identity: DirectorIdentity | None = None,
) -> dict[str, Any]:
    return {
        "event_version": "v3" if director_identity is not None else "v2",
        "director_identity": director_identity,
        "task_id": task_id,
        "interaction_id": interaction_id,
        "plan_generation": plan_generation,
        "assignment_sequence": assignment_sequence,
        "provider": provider,
        "phase": phase,
        "attempt": attempt,
        "state": state,
        "transition_ordinal": transition_ordinal,
        "effect_key": effect_key,
        "previous_event_digest": previous_event_digest,
        "candidate_binding_digest": candidate_binding_digest,
        "request_payload_digest": request_payload_digest,
        "consumed_artifact_digests": consumed_artifact_digests,
        "transport_evidence": transport_evidence,
        "recorded_at": _utc(recorded_at),
    }


def _build_event(**payload: Any) -> ProviderInteractionEvent:
    provisional = ProviderInteractionEvent.model_construct(
        **payload, event_digest="0" * 64
    )
    return ProviderInteractionEvent.model_validate(
        {**payload, "event_digest": _canonical_digest(provisional.canonical_payload())}
    )


def start_interaction(
    *,
    task_id: str,
    interaction_id: str,
    plan_generation: int,
    assignment_sequence: int | None,
    provider: InteractionProvider | str,
    phase: ProviderInteractionPhase,
    attempt: int,
    transport_evidence: ProviderTransportEvidence,
    effect_key: str | None = None,
    candidate_binding_digest: str | None = None,
    request_payload: str | bytes | None = None,
    consumed_artifact_digests: Iterable[str] = (),
    recorded_at: datetime | None = None,
    director_identity: DirectorIdentity | None = None,
) -> ProviderInteractionEvent:
    if transport_evidence.source != TransportEvidenceSource.BACKEND_OBSERVATION:
        raise CommunicationEvidenceFailure(
            "DRAFTED requires backend-observed evidence"
        )
    consumed = tuple(sorted(set(consumed_artifact_digests)))
    encoded_request = (
        request_payload.encode("utf-8")
        if isinstance(request_payload, str)
        else request_payload
    )
    if consumed and encoded_request is None:
        raise CommunicationEvidenceFailure(
            "artifact consumption requires the exact outbound request payload"
        )
    if encoded_request is not None and any(
        digest.encode("ascii") not in encoded_request for digest in consumed
    ):
        raise CommunicationEvidenceFailure(
            "outbound request does not contain every consumed artifact digest"
        )
    return _build_event(
        **_event_payload(
            task_id=task_id,
            director_identity=director_identity,
            interaction_id=interaction_id,
            plan_generation=plan_generation,
            assignment_sequence=assignment_sequence,
            provider=InteractionProvider(provider),
            phase=phase,
            attempt=attempt,
            state=ProviderInteractionState.DRAFTED,
            transition_ordinal=1,
            effect_key=effect_key,
            previous_event_digest=None,
            candidate_binding_digest=candidate_binding_digest,
            request_payload_digest=(
                canonical_content_digest(encoded_request)
                if encoded_request is not None
                else None
            ),
            consumed_artifact_digests=consumed,
            transport_evidence=transport_evidence,
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )
    )


def _validated_events(
    events: Iterable[ProviderInteractionEvent], interaction_id: str
) -> list[ProviderInteractionEvent]:
    selected = sorted(
        (item for item in events if item.interaction_id == interaction_id),
        key=lambda item: item.transition_ordinal,
    )
    if not selected:
        raise CommunicationEvidenceFailure("interaction evidence is missing")
    for index, event in enumerate(selected):
        try:
            event = ProviderInteractionEvent.model_validate(event.model_dump(mode="json"))
        except ValueError as exc:
            raise CommunicationEvidenceFailure("interaction event digest is invalid") from exc
        selected[index] = event
        if event.transition_ordinal != index + 1:
            raise CommunicationEvidenceFailure("interaction transition ordinal is not contiguous")
        if index == 0:
            if event.state != ProviderInteractionState.DRAFTED:
                raise CommunicationEvidenceFailure("interaction must begin in DRAFTED")
            if event.previous_event_digest is not None:
                raise CommunicationEvidenceFailure("first interaction event has a predecessor")
            continue
        previous = selected[index - 1]
        if event.previous_event_digest != previous.event_digest:
            raise CommunicationEvidenceFailure("interaction event chain is broken")
        for field in (
            "task_id",
            "interaction_id",
            "plan_generation",
            "assignment_sequence",
            "provider",
            "phase",
            "attempt",
            "effect_key",
            "candidate_binding_digest",
            "request_payload_digest",
            "consumed_artifact_digests",
            "director_identity",
        ):
            if getattr(event, field) != getattr(previous, field):
                raise CommunicationEvidenceFailure(
                    f"interaction identity changed at {field}"
                )
        for field in (
            "provider_run_id_digest",
            "provider_session_id_digest",
            "correlation_id_digest",
        ):
            prior = getattr(previous.transport_evidence, field)
            current = getattr(event.transport_evidence, field)
            if prior is not None and current != prior:
                raise CommunicationEvidenceFailure(
                    f"interaction transport identity changed at {field}"
                )
        if event.state not in LEGAL_INTERACTION_TRANSITIONS[previous.state]:
            raise CommunicationEvidenceFailure(
                f"illegal interaction transition {previous.state.value}->{event.state.value}"
            )
    return selected


def transition_interaction(
    events: Iterable[ProviderInteractionEvent],
    *,
    interaction_id: str,
    next_state: ProviderInteractionState,
    transport_evidence: ProviderTransportEvidence,
    recorded_at: datetime | None = None,
) -> ProviderInteractionEvent:
    selected = _validated_events(events, interaction_id)
    previous = selected[-1]
    if next_state not in LEGAL_INTERACTION_TRANSITIONS[previous.state]:
        raise CommunicationEvidenceFailure(
            f"illegal interaction transition {previous.state.value}->{next_state.value}"
        )
    if next_state in {
        ProviderInteractionState.ACKNOWLEDGED,
        ProviderInteractionState.RESPONDED,
        ProviderInteractionState.COMPLETED,
        ProviderInteractionState.VERIFIED,
    } and transport_evidence.source != TransportEvidenceSource.AUTHENTICATED_PROVIDER:
        raise CommunicationEvidenceFailure(
            f"{next_state.value} requires authenticated provider transport evidence"
        )
    if next_state == ProviderInteractionState.VERIFIED:
        if transport_evidence.result_digest is None:
            raise CommunicationEvidenceFailure("VERIFIED requires an exact result digest")
        if previous.transport_evidence.result_digest is None:
            raise CommunicationEvidenceFailure("COMPLETED result binding is missing")
        if not hmac.compare_digest(
            transport_evidence.result_digest,
            previous.transport_evidence.result_digest,
        ):
            raise CommunicationEvidenceFailure("VERIFIED result digest changed after completion")
    if next_state == ProviderInteractionState.FAILED and not transport_evidence.error_code:
        raise CommunicationEvidenceFailure("FAILED requires a backend-recorded error code")
    if next_state == ProviderInteractionState.UNKNOWN and not (
        transport_evidence.ambiguity_reason or transport_evidence.error_code
    ):
        raise CommunicationEvidenceFailure("UNKNOWN requires an ambiguity reason or error code")
    if next_state == ProviderInteractionState.UNAVAILABLE:
        if transport_evidence.contact_attempt_ordinal not in SUPPORTED_ATTEMPT_LIMITS:
            raise CommunicationEvidenceFailure(
                "UNAVAILABLE requires the final real contact attempt (legacy 3 or current 5)"
            )
        if not transport_evidence.error_code:
            raise CommunicationEvidenceFailure("UNAVAILABLE requires a failure code")
    return _build_event(
        **_event_payload(
            task_id=previous.task_id,
            director_identity=previous.director_identity,
            interaction_id=previous.interaction_id,
            plan_generation=previous.plan_generation,
            assignment_sequence=previous.assignment_sequence,
            provider=previous.provider,
            phase=previous.phase,
            attempt=previous.attempt,
            state=next_state,
            transition_ordinal=previous.transition_ordinal + 1,
            effect_key=previous.effect_key,
            previous_event_digest=previous.event_digest,
            candidate_binding_digest=previous.candidate_binding_digest,
            request_payload_digest=previous.request_payload_digest,
            consumed_artifact_digests=previous.consumed_artifact_digests,
            transport_evidence=transport_evidence,
            recorded_at=recorded_at or datetime.now(timezone.utc),
        )
    )


def append_interaction_event(
    events: Iterable[ProviderInteractionEvent], event: ProviderInteractionEvent
) -> list[ProviderInteractionEvent]:
    """Return an idempotently appended, fully validated event projection."""

    current = list(events)
    for existing in current:
        if hmac.compare_digest(existing.event_digest, event.event_digest):
            return current
        if (
            existing.interaction_id == event.interaction_id
            and existing.transition_ordinal == event.transition_ordinal
        ):
            raise CommunicationEvidenceFailure(
                "conflicting event occupies the interaction transition ordinal"
            )
    candidate = [*current, event]
    _validated_events(candidate, event.interaction_id)
    return candidate


def project_interaction(
    events: Iterable[ProviderInteractionEvent], interaction_id: str
) -> ProviderInteractionProjection:
    selected = _validated_events(events, interaction_id)
    latest = selected[-1]
    return ProviderInteractionProjection(
        task_id=latest.task_id,
        interaction_id=latest.interaction_id,
        provider=latest.provider,
        assignment_sequence=latest.assignment_sequence,
        state=latest.state,
        event_count=len(selected),
        latest_event_digest=latest.event_digest,
        latest_evidence_digest=latest.transport_evidence.evidence_digest,
        verified_result_digest=(
            latest.transport_evidence.result_digest
            if latest.state == ProviderInteractionState.VERIFIED
            else None
        ),
    )


def find_verified_result(
    events: Iterable[ProviderInteractionEvent],
    *,
    interaction_id: str,
    result: str | bytes,
    evidence_digest: str | None = None,
) -> ProviderInteractionEvent | None:
    try:
        selected = _validated_events(events, interaction_id)
    except CommunicationEvidenceFailure:
        return None
    latest = selected[-1]
    if latest.state != ProviderInteractionState.VERIFIED:
        return None
    actual = canonical_content_digest(result)
    expected = latest.transport_evidence.result_digest
    if expected is None or not hmac.compare_digest(actual, expected):
        return None
    if evidence_digest is not None and not hmac.compare_digest(
        latest.transport_evidence.evidence_digest, evidence_digest
    ):
        return None
    return latest


def require_verified_result(
    events: Iterable[ProviderInteractionEvent],
    *,
    interaction_id: str,
    result: str | bytes,
    evidence_digest: str | None = None,
) -> ProviderInteractionEvent:
    verified = find_verified_result(
        events,
        interaction_id=interaction_id,
        result=result,
        evidence_digest=evidence_digest,
    )
    if verified is None:
        raise CommunicationEvidenceFailure(
            "exact result is not bound to the latest VERIFIED transport event"
        )
    return verified


def director_evidence_matches(
    event: ProviderInteractionEvent, identity: DirectorIdentity | None
) -> bool:
    """Never reinterpret historical evidence using the current provider setting."""
    if identity is None:
        return event.provider == InteractionProvider.OPENAI and event.director_identity is None
    return (
        event.director_identity == identity
        and event.provider.value == identity.provider
        and event.transport_evidence.transport == identity.transport
    )


# [[Codex | GPT-5.6 | 2026-08-24 | 09:17 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:30 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:12 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | END]]
