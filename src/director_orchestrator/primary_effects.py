# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 06:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 19:18 UTC | BEGIN]]
"""Deterministic, fenced execution of one LangGraph external-effect intent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .langgraph_postgres import EffectReservation, PostgresConditionalClaimStore
from .langgraph_primary import (
    EffectDisposition,
    EffectExecutionResult,
    HandlerContext,
)
from .packet_contracts import document_digest
from .primary_effect_gateways import (
    ExactEffectGatewayRegistry,
    PostgresTypedEffectIntentStore,
    TypedEffectIntent,
)


class PrimaryEffectExecutorError(RuntimeError):
    """The deterministic effect boundary was configured or invoked incorrectly."""


@dataclass(frozen=True, slots=True)
class RemoteEffectReference:
    """The minimal remote identity returned by one mutating call."""

    remote_system: str
    remote_identifier: str

    def __post_init__(self) -> None:
        if not self.remote_system.strip():
            raise ValueError("remote_system is required")
        if not self.remote_identifier.strip():
            raise ValueError("remote_identifier is required")

    def as_dict(self) -> dict[str, str]:
        return {
            "remote_system": self.remote_system,
            "remote_identifier": self.remote_identifier,
        }


EffectMutation = Callable[
    [HandlerContext, EffectReservation, float], RemoteEffectReference
]
EffectReadback = Callable[
    [HandlerContext, EffectReservation, RemoteEffectReference, float],
    Mapping[str, Any],
]
EffectIntentFactory = Callable[[HandlerContext, EffectReservation], TypedEffectIntent]


class DeterministicExternalEffectExecutor:
    """Execute at most one mutation for a durable Postgres reservation.

    The transport receives the stable effect key through ``reservation`` and a
    fixed timeout.  It must use both as its remote idempotency/deadline inputs.
    A restart with a durable remote binding performs readback only.  A restart
    without that binding is ambiguous and never repeats the mutation.
    """

    def __init__(
        self,
        *,
        store: PostgresConditionalClaimStore,
        owner_token: str,
        timeout_seconds: float,
        mutation: EffectMutation | None = None,
        readback: EffectReadback | None = None,
        intent_factory: EffectIntentFactory | None = None,
        intent_store: PostgresTypedEffectIntentStore | None = None,
        gateways: ExactEffectGatewayRegistry | None = None,
    ) -> None:
        if not owner_token.strip():
            raise ValueError("owner_token is required")
        timeout = float(timeout_seconds)
        if timeout <= 0 or timeout > 60:
            raise ValueError("timeout_seconds must be in (0, 60]")
        if (mutation is None) != (readback is None):
            raise ValueError("legacy effect mutation and readback must be configured together")
        self._store = store
        self._owner_token = owner_token
        self._mutation = mutation
        self._readback = readback
        self._timeout_seconds = timeout
        typed_values = (intent_factory, intent_store, gateways)
        if any(item is not None for item in typed_values) and not all(
            item is not None for item in typed_values
        ):
            raise ValueError(
                "typed effect intent factory, store, and gateways must be configured together"
            )
        if mutation is None and not all(item is not None for item in typed_values):
            raise ValueError("an exact typed gateway or legacy effect transport is required")
        self._intent_factory = intent_factory
        self._intent_store = intent_store
        self._gateways = gateways

    @property
    def ready(self) -> bool:
        return bool(
            self._intent_factory is not None
            and self._intent_store is not None
            and self._gateways is not None
            and self._gateways.ready
        )

    def _typed_intent(
        self, context: HandlerContext, reservation: EffectReservation
    ) -> TypedEffectIntent | None:
        if self._intent_factory is None:
            return None
        assert self._intent_store is not None
        assert self._gateways is not None
        intent = self._intent_factory(context, reservation)
        if not isinstance(intent, TypedEffectIntent):
            raise PrimaryEffectExecutorError(
                "effect intent factory returned the wrong result type"
            )
        if (
            intent.writer_owner_id != reservation.writer_owner_id
            or intent.writer_fencing_generation != reservation.fencing_generation
            or intent.budget_units != reservation.reserved_units
        ):
            raise PrimaryEffectExecutorError("typed effect intent reservation binding changed")
        self._gateways.require(intent)
        self._intent_store.persist(
            reservation=reservation,
            owner_token=self._owner_token,
            intent=intent,
        )
        return intent

    def _reservation(self, context: HandlerContext) -> EffectReservation:
        if context.effect_attempt_digest is None:
            raise PrimaryEffectExecutorError("effect attempt is not bound")
        reservation = self._store.read_effect_reservation(
            task_id=context.task_id,
            effect_attempt_digest=context.effect_attempt_digest,
            owner_token=self._owner_token,
        )
        if reservation is None:
            raise PrimaryEffectExecutorError(
                "effect execution lacks a current-writer Postgres reservation"
            )
        if (
            context.effect_key_digest is None
            or reservation.effect_key_digest != context.effect_key_digest
            or reservation.effect_attempt_digest != context.effect_attempt_digest
            or reservation.writer_owner_id != context.writer_owner_id
        ):
            raise PrimaryEffectExecutorError("effect reservation binding changed")
        return reservation

    def _ambiguous(
        self,
        reservation: EffectReservation,
        *,
        reason_code: str,
    ) -> EffectExecutionResult:
        result = EffectExecutionResult(
            effect_key_digest=reservation.effect_key_digest,
            effect_attempt_digest=reservation.effect_attempt_digest,
            writer_owner_id=reservation.writer_owner_id,
            disposition=EffectDisposition.AMBIGUOUS,
            reason_code=reason_code,
        )
        self._store.record_reconciliation_event(
            claim_key=reservation.claim_key,
            owner_token=self._owner_token,
            state="AMBIGUOUS",
            evidence_digest=document_digest(result.as_resume_payload()),
        )
        return result

    def __call__(self, context: HandlerContext) -> EffectExecutionResult:
        reservation = self._reservation(context)
        try:
            intent = self._typed_intent(context, reservation)
        except Exception as exc:
            raise PrimaryEffectExecutorError("typed effect intent was refused") from exc
        binding = self._store.read_remote_binding(
            reservation_key=reservation.reservation_key,
            owner_token=self._owner_token,
        )
        if binding is None:
            if not self._store.claim_effect_execution(
                reservation_key=reservation.reservation_key,
                owner_token=self._owner_token,
            ):
                return self._ambiguous(
                    reservation,
                    reason_code="effect_started_without_remote_binding",
                )
            try:
                if intent is None:
                    assert self._mutation is not None
                    reference = self._mutation(
                        context, reservation, self._timeout_seconds
                    )
                else:
                    assert self._gateways is not None
                    gateway = self._gateways.require(intent)
                    reference = RemoteEffectReference(
                        intent.system,
                        gateway.mutate(intent, self._timeout_seconds),
                    )
                if not isinstance(reference, RemoteEffectReference):
                    raise PrimaryEffectExecutorError(
                        "effect mutation returned the wrong result type"
                    )
                self._store.bind_remote_effect(
                    reservation_key=reservation.reservation_key,
                    owner_token=self._owner_token,
                    remote_system=reference.remote_system,
                    remote_identifier=reference.remote_identifier,
                )
            except Exception:
                return self._ambiguous(
                    reservation, reason_code="effect_mutation_outcome_unknown"
                )
            binding = self._store.read_remote_binding(
                reservation_key=reservation.reservation_key,
                owner_token=self._owner_token,
            )
            if binding is None:
                return self._ambiguous(
                    reservation, reason_code="effect_remote_binding_missing"
                )
        reference = RemoteEffectReference(
            remote_system=str(binding["remote_system"]),
            remote_identifier=str(binding["remote_identifier"]),
        )
        try:
            if intent is None:
                assert self._readback is not None
                readback = dict(
                    self._readback(
                        context, reservation, reference, self._timeout_seconds
                    )
                )
            else:
                assert self._gateways is not None
                gateway = self._gateways.require(intent)
                readback = dict(
                    gateway.readback(
                        intent,
                        reference.remote_identifier,
                        self._timeout_seconds,
                    )
                )
        except Exception:
            return self._ambiguous(
                reservation, reason_code="effect_terminal_readback_unknown"
            )
        expected = reference.as_dict()
        if any(str(readback.get(key, "")) != value for key, value in expected.items()):
            return self._ambiguous(
                reservation, reason_code="effect_terminal_readback_mismatch"
            )
        if readback.get("terminal_status") != "COMPLETED":
            return self._ambiguous(
                reservation, reason_code="effect_terminal_status_not_completed"
            )
        result = EffectExecutionResult(
            effect_key_digest=reservation.effect_key_digest,
            effect_attempt_digest=reservation.effect_attempt_digest,
            writer_owner_id=reservation.writer_owner_id,
            disposition=EffectDisposition.COMPLETED,
            result_digest=document_digest(
                {
                    "effect_key_digest": reservation.effect_key_digest,
                    "remote": expected,
                }
            ),
            readback_digest=document_digest(readback),
            reason_code="effect_terminal_readback_completed",
        )
        self._store.record_terminal_evidence(
            claim_key=reservation.claim_key,
            owner_token=self._owner_token,
            disposition=EffectDisposition.COMPLETED.value,
            evidence_digest=document_digest(result.as_resume_payload()),
        )
        return result


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 19:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 06:03 UTC | END]]
