# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:04 UTC | BEGIN]]
"""Closed API command envelopes for the LangGraph-primary coordinator.

The FastAPI layer authenticates callers and loads the current durable projection.
These models then bind the request to the URL task and that exact projection before
constructing the smaller immutable commands consumed by ``PrimaryTaskCoordinator``.
No model in this module performs provider dispatch or an external effect.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .langgraph_primary import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalResume,
    EffectDisposition,
    EffectExecutionResult,
    PrimaryOutcome,
    ProviderDisposition,
    ProviderSubgraphResult,
    WorkflowRunStatus,
)


_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SHA_PATTERN = r"^[0-9a-f]{40}$"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
_ACTOR_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_PROOF_TTL_SECONDS = 300
_PROOF_FUTURE_SKEW_SECONDS = 30
_MIN_SECRET_LENGTH = 32


class PrimaryIngressProof(BaseModel):
    """Short-lived HMAC proof for one exact primary command envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"] = "v1"
    channel_id: str | None = Field(default=None, min_length=1, max_length=64)
    issued_at: int = Field(ge=0)
    nonce: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompletionAuthorityProof(BaseModel):
    """One independently authenticated completion-authority assertion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"] = "v1"
    authority: Literal["independent_review", "ci", "security"]
    issuer_id: str = Field(pattern=_ACTOR_ID_PATTERN)
    principal_id: str = Field(pattern=_ACTOR_ID_PATTERN)
    issued_at: int = Field(ge=0)
    nonce: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class PrimaryCommandBindingError(ValueError):
    """A command is not bound to the current path or durable projection."""


class PrimaryCommandOperation(StrEnum):
    APPROVAL_RESUME = "approval_resume"
    PROVIDER_RESULT = "provider_result"
    EFFECT_RECONCILIATION = "effect_reconciliation"
    COMPLETION_EVIDENCE = "completion_evidence"
    CANCELLATION = "cancellation"
    RECOVERY = "recovery"


class _BoundCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=255)
    command_id: str = Field(min_length=1, max_length=255)
    actor_id: str = Field(pattern=_ACTOR_ID_PATTERN)
    expected_projection_digest: str = Field(pattern=_DIGEST_PATTERN)
    expected_fencing_generation: int = Field(ge=1)

    def model_post_init(self, __context: Any) -> None:
        for field_name in ("task_id", "command_id"):
            if _ID_PATTERN.fullmatch(getattr(self, field_name)) is None:
                raise ValueError(
                    f"{field_name} must start with an alphanumeric character and "
                    "contain only alphanumerics, underscore, period, colon, or hyphen"
                )

    @property
    def command_digest(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def validate_binding(
        self,
        *,
        path_task_id: str,
        current_projection_digest: str,
        current_fencing_generation: int,
    ) -> None:
        """Fail closed on cross-task, stale-projection, or stale-writer input."""

        self.validate_path_and_fence(
            path_task_id=path_task_id,
            current_fencing_generation=current_fencing_generation,
        )
        self.validate_projection(current_projection_digest)

    def validate_path_and_fence(
        self, *, path_task_id: str, current_fencing_generation: int
    ) -> None:
        """Bind a new command to its URL and the current writer authority."""

        self.validate_path(path_task_id)
        if self.expected_fencing_generation != current_fencing_generation:
            raise PrimaryCommandBindingError("command targets a stale writer generation")

    def validate_path(self, path_task_id: str) -> None:
        """Reject cross-task command and receipt lookup before any state access."""

        if not hmac.compare_digest(self.task_id, path_task_id):
            raise PrimaryCommandBindingError("command task does not match the request path")

    def validate_projection(self, current_projection_digest: str) -> None:
        if not hmac.compare_digest(
            self.expected_projection_digest, current_projection_digest
        ):
            raise PrimaryCommandBindingError("command targets a stale task projection")

    def validate_replay(self, previous_command_digest: str) -> None:
        """Accept exact redelivery and reject reuse with changed command content."""

        if re.fullmatch(_DIGEST_PATTERN, previous_command_digest) is None:
            raise PrimaryCommandBindingError("stored command digest is malformed")
        if not hmac.compare_digest(self.command_digest, previous_command_digest):
            raise PrimaryCommandBindingError(
                "command identity was replayed with different content"
            )

    def runtime_replay_is_evidenced(self, projection: Mapping[str, Any]) -> bool:
        """Whether durable graph state proves the command crossed the crash gap."""

        return False


class ApprovalResumeRequest(_BoundCommandRequest):
    schema_version: Literal["langgraph-primary-approval-resume/v1"] = (
        "langgraph-primary-approval-resume/v1"
    )
    kind: ApprovalKind
    request_digest: str = Field(pattern=_DIGEST_PATTERN)
    approval_event_id: str = Field(min_length=1, max_length=255)
    decision: ApprovalDecision = ApprovalDecision.APPROVE
    slack_gateway_proof: PrimaryIngressProof | None = None

    def to_command(self) -> ApprovalResume:
        return ApprovalResume(
            task_id=self.task_id,
            kind=self.kind,
            request_digest=self.request_digest,
            approval_event_id=self.approval_event_id,
            decision=self.decision,
        )

    def runtime_replay_is_evidenced(self, projection: Mapping[str, Any]) -> bool:
        field = (
            "execution_approval_receipt_digest"
            if self.kind == ApprovalKind.EXECUTION
            else "effect_approval_receipt_digest"
        )
        stored = projection.get(field)
        return stored is not None and hmac.compare_digest(
            str(stored), self.to_command().receipt_digest
        )


class ProviderResultRequest(_BoundCommandRequest):
    schema_version: Literal["langgraph-primary-provider-result/v1"] = (
        "langgraph-primary-provider-result/v1"
    )
    provider_attempt_digest: str = Field(pattern=_DIGEST_PATTERN)
    idempotency_key_digest: str = Field(pattern=_DIGEST_PATTERN)
    authority_snapshot_digest: str = Field(pattern=_DIGEST_PATTERN)
    producer_id: str = Field(pattern=_ACTOR_ID_PATTERN)
    assignment_id: str | None = Field(default=None, min_length=1, max_length=255)
    provider_id: str | None = Field(default=None, pattern=_ACTOR_ID_PATTERN)
    provider_run_id: str | None = Field(default=None, min_length=1, max_length=255)
    callback_proof: PrimaryIngressProof | None = None
    disposition: ProviderDisposition
    result_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    candidate_sha: str | None = Field(default=None, pattern=_SHA_PATTERN)
    reason_code: str = Field(default="ok", pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$")

    def to_command(self) -> ProviderSubgraphResult:
        return ProviderSubgraphResult(
            provider_attempt_digest=self.provider_attempt_digest,
            idempotency_key_digest=self.idempotency_key_digest,
            authority_snapshot_digest=self.authority_snapshot_digest,
            producer_id=self.producer_id,
            disposition=self.disposition,
            result_digest=self.result_digest,
            candidate_sha=self.candidate_sha,
            reason_code=self.reason_code,
        )

    def runtime_replay_is_evidenced(self, projection: Mapping[str, Any]) -> bool:
        stored = projection.get("provider_result_receipt_digest")
        return stored is not None and hmac.compare_digest(
            str(stored), _canonical_payload_digest(self.to_command().as_resume_payload())
        )


class EffectReconciliationRequest(_BoundCommandRequest):
    schema_version: Literal["langgraph-primary-effect-reconciliation/v1"] = (
        "langgraph-primary-effect-reconciliation/v1"
    )
    effect_key_digest: str = Field(pattern=_DIGEST_PATTERN)
    effect_attempt_digest: str = Field(pattern=_DIGEST_PATTERN)
    writer_owner_id: str = Field(min_length=1, max_length=255)
    disposition: EffectDisposition
    result_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    readback_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    writer_proof: PrimaryIngressProof | None = None
    reason_code: str = Field(default="ok", pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$")

    def to_command(self) -> EffectExecutionResult:
        return EffectExecutionResult(
            effect_key_digest=self.effect_key_digest,
            effect_attempt_digest=self.effect_attempt_digest,
            writer_owner_id=self.writer_owner_id,
            disposition=self.disposition,
            result_digest=self.result_digest,
            readback_digest=self.readback_digest,
            reason_code=self.reason_code,
        )

    def runtime_replay_is_evidenced(self, projection: Mapping[str, Any]) -> bool:
        stored = projection.get("effect_result_receipt_digest")
        return stored is not None and hmac.compare_digest(
            str(stored), _canonical_payload_digest(self.to_command().as_resume_payload())
        )


class CompletionEvidenceRequest(_BoundCommandRequest):
    """Authenticated immutable CI/security evidence for one exact candidate."""

    schema_version: Literal["langgraph-primary-completion-evidence/v1"] = (
        "langgraph-primary-completion-evidence/v1"
    )
    candidate_sha: str = Field(pattern=_SHA_PATTERN)
    provider_result_digest: str = Field(pattern=_DIGEST_PATTERN)
    ledger: dict[str, Any]
    results: tuple[dict[str, Any], ...] = Field(min_length=1, max_length=64)
    ci_gate_id: str = Field(min_length=1, max_length=255)
    security_gate_id: str = Field(min_length=1, max_length=255)
    independent_review_authority_proof: CompletionAuthorityProof | None = None
    ci_authority_proof: CompletionAuthorityProof | None = None
    security_authority_proof: CompletionAuthorityProof | None = None

    def to_evidence(self):
        # Deferred imports keep the command-envelope module independent of the
        # gate execution implementation. The immutable store revalidates every
        # digest and candidate/result binding before accepting this bundle.
        from .completion_gates import VerifiedGateLedger, VerifiedGateResult
        from .primary_completion import CompletionEvidence

        ledger_digest = str(self.ledger.get("ledger_digest", ""))
        verified_results = tuple(
            VerifiedGateResult(document=dict(item), digest=str(item.get("result_digest", "")))
            for item in self.results
        )
        return CompletionEvidence(
            ledger=VerifiedGateLedger(document=dict(self.ledger), digest=ledger_digest),
            results=verified_results,
            ci_gate_id=self.ci_gate_id,
            security_gate_id=self.security_gate_id,
            authority_proofs={
                "independent_review": self.independent_review_authority_proof.model_dump(mode="json"),
                "ci": self.ci_authority_proof.model_dump(mode="json"),
                "security": self.security_authority_proof.model_dump(mode="json"),
            },
        )


class CancellationRequest(_BoundCommandRequest):
    schema_version: Literal["langgraph-primary-cancellation/v1"] = (
        "langgraph-primary-cancellation/v1"
    )
    reason_code: str = Field(
        default="owner_cancelled", pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$"
    )
    gateway_proof: PrimaryIngressProof | None = None

    def to_task_id(self) -> str:
        return self.task_id

    def runtime_replay_is_evidenced(self, projection: Mapping[str, Any]) -> bool:
        classification = projection.get("terminal_classification")
        reason = projection.get("transition_reason_code") or projection.get("reason_code")
        return classification == "CANCELLED" or reason in {
            "cancelled_after_dispatch",
            "cancelled_after_effect_start",
            "cancel_requested",
        }


class RecoveryRequest(_BoundCommandRequest):
    schema_version: Literal["langgraph-primary-recovery/v1"] = (
        "langgraph-primary-recovery/v1"
    )
    reason_code: str = Field(
        default="operator_recovery", pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$"
    )
    gateway_proof: PrimaryIngressProof | None = None

    def to_task_id(self) -> str:
        return self.task_id

    def runtime_replay_is_evidenced(self, projection: Mapping[str, Any]) -> bool:
        # Recovery is itself the bounded, replay-safe graph reconciliation operation.
        return True


class PrimaryCommandResponse(BaseModel):
    """Sanitized command result suitable for a FastAPI response model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["langgraph-primary-command-response/v1"] = (
        "langgraph-primary-command-response/v1"
    )
    task_id: str = Field(min_length=1, max_length=255)
    command_id: str = Field(min_length=1, max_length=255)
    command_digest: str = Field(pattern=_DIGEST_PATTERN)
    operation: PrimaryCommandOperation
    run_status: str
    phase: str
    reason_code: str
    terminal_classification: str | None = None
    state_digest: str = Field(pattern=_DIGEST_PATTERN)
    projection_version: int | None = Field(default=None, ge=1)
    projection_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    fencing_generation: int | None = Field(default=None, ge=1)

    @classmethod
    def from_outcome(
        cls,
        *,
        request: _BoundCommandRequest,
        operation: PrimaryCommandOperation,
        outcome: PrimaryOutcome,
    ) -> "PrimaryCommandResponse":
        if not hmac.compare_digest(request.task_id, outcome.task_id):
            raise PrimaryCommandBindingError("runtime outcome belongs to another task")
        return cls(
            task_id=outcome.task_id,
            command_id=request.command_id,
            command_digest=request.command_digest,
            operation=operation,
            run_status=outcome.run_status.value,
            phase=outcome.phase,
            reason_code=outcome.reason_code,
            terminal_classification=(
                outcome.terminal_classification.value
                if outcome.terminal_classification is not None
                else None
            ),
            state_digest=outcome.state_digest,
        )

    @classmethod
    def from_projection(
        cls,
        *,
        request: _BoundCommandRequest,
        operation: PrimaryCommandOperation,
        projection: Any,
        fencing_generation: int,
    ) -> "PrimaryCommandResponse":
        """Adapt a ``PrimaryTaskProjection`` without importing its Postgres backend."""

        task_id = str(projection.task_id)
        if not hmac.compare_digest(request.task_id, task_id):
            raise PrimaryCommandBindingError("durable projection belongs to another task")
        payload: Mapping[str, Any] = projection.projection
        state_digest = str(payload.get("state_digest") or projection.projection_digest)
        return cls(
            task_id=task_id,
            command_id=request.command_id,
            command_digest=request.command_digest,
            operation=operation,
            run_status=str(payload.get("run_status") or "UNKNOWN"),
            phase=str(payload.get("phase") or payload.get("current_phase") or "unknown"),
            reason_code=str(
                payload.get("reason_code")
                or payload.get("transition_reason_code")
                or "unknown"
            ),
            terminal_classification=(
                str(payload["terminal_classification"])
                if payload.get("terminal_classification") is not None
                else None
            ),
            state_digest=state_digest,
            projection_version=int(projection.projection_version),
            projection_digest=str(projection.projection_digest),
            fencing_generation=fencing_generation,
        )

    @classmethod
    def from_receipt(
        cls,
        *,
        request: _BoundCommandRequest,
        operation: PrimaryCommandOperation,
        receipt: Any,
    ) -> "PrimaryCommandResponse":
        """Return a durable acknowledgement without consulting advanced state."""

        task_id = str(receipt.task_id)
        if not hmac.compare_digest(request.task_id, task_id):
            raise PrimaryCommandBindingError("durable receipt belongs to another task")
        request.validate_replay(str(receipt.command_digest))
        if request.expected_fencing_generation != int(
            receipt.writer_fencing_generation
        ):
            raise PrimaryCommandBindingError(
                "command receipt belongs to another writer generation"
            )
        return cls(
            task_id=task_id,
            command_id=request.command_id,
            command_digest=request.command_digest,
            operation=operation,
            run_status=WorkflowRunStatus.ALREADY_APPLIED.value,
            phase=operation.value,
            reason_code="command_receipt_replay",
            state_digest=str(receipt.resulting_projection_digest),
            projection_version=int(receipt.resulting_projection_version),
            projection_digest=str(receipt.resulting_projection_digest),
            fencing_generation=int(receipt.writer_fencing_generation),
        )


def _canonical_payload_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


_COMPLETION_AUTHORITY_PROOF_FIELDS = {
    "independent_review": "independent_review_authority_proof",
    "ci": "ci_authority_proof",
    "security": "security_authority_proof",
}


def _completion_authority_result(
    request: CompletionEvidenceRequest, authority: str
) -> Mapping[str, Any]:
    if authority == "independent_review":
        matches = [
            item
            for item in request.results
            if item.get("evidence_role") == "INDEPENDENT_REVIEWER"
        ]
    elif authority == "ci":
        matches = [
            item
            for item in request.results
            if item.get("gate_id") == request.ci_gate_id
            and item.get("evidence_role") == "TRUSTED_EXTERNAL_CI"
        ]
    elif authority == "security":
        matches = [
            item
            for item in request.results
            if item.get("gate_id") == request.security_gate_id
            and item.get("evidence_role") == "CODEX_SECURITY"
        ]
    else:
        raise PrimaryCommandBindingError("completion authority is unsupported")
    if len(matches) != 1:
        raise PrimaryCommandBindingError(
            f"completion {authority} result binding is incomplete"
        )
    return matches[0]


def _completion_authority_payload(
    request: CompletionEvidenceRequest,
    proof: CompletionAuthorityProof,
) -> bytes:
    result = _completion_authority_result(request, proof.authority)
    actor = result.get("evidence_actor")
    principal = (
        actor.get("authenticated_principal") if isinstance(actor, Mapping) else None
    )
    if principal != proof.principal_id:
        raise PrimaryCommandBindingError(
            f"completion {proof.authority} principal binding changed"
        )
    return json.dumps(
        {
            "version": proof.version,
            "authority": proof.authority,
            "issuer_id": proof.issuer_id,
            "principal_id": proof.principal_id,
            "issued_at": proof.issued_at,
            "nonce": proof.nonce,
            "task_id": request.task_id,
            "command_id": request.command_id,
            "candidate_sha": request.candidate_sha,
            "provider_result_digest": request.provider_result_digest,
            "ledger_digest": request.ledger.get("ledger_digest"),
            "gate_id": result.get("gate_id"),
            "result_digest": result.get("result_digest"),
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_completion_authority_proof(
    secret: str,
    shared_secret: str,
    request: CompletionEvidenceRequest,
    *,
    authority: Literal["independent_review", "ci", "security"],
    issuer_id: str,
    principal_id: str,
    issued_at: int | None = None,
    nonce: str | None = None,
) -> CompletionAuthorityProof:
    """Sign only one role's exact result; no bundle credential is accepted."""

    if len(secret) < _MIN_SECRET_LENGTH:
        raise PrimaryCommandBindingError(
            "completion authority signing secret is not configured"
        )
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise PrimaryCommandBindingError("completion authority secret must be distinct")
    proof = CompletionAuthorityProof(
        authority=authority,
        issuer_id=issuer_id,
        principal_id=principal_id,
        issued_at=(
            issued_at
            if issued_at is not None
            else int(datetime.now(timezone.utc).timestamp())
        ),
        nonce=nonce or secrets.token_urlsafe(32),
        signature="0" * 64,
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        _completion_authority_payload(request, proof),
        hashlib.sha256,
    ).hexdigest()
    return proof.model_copy(update={"signature": signature})


def verify_completion_authority_proof(
    secret: str,
    shared_secret: str,
    request: CompletionEvidenceRequest,
    *,
    authority: Literal["independent_review", "ci", "security"],
    now: int | None = None,
) -> CompletionAuthorityProof:
    field = _COMPLETION_AUTHORITY_PROOF_FIELDS[authority]
    proof = getattr(request, field)
    if (
        not isinstance(proof, CompletionAuthorityProof)
        or proof.authority != authority
        or len(secret) < _MIN_SECRET_LENGTH
    ):
        raise PrimaryCommandBindingError(
            f"authenticated completion {authority} proof is required"
        )
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise PrimaryCommandBindingError("completion authority secret must be distinct")
    observed = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    age = observed - proof.issued_at
    if age < -_PROOF_FUTURE_SKEW_SECONDS or age > _PROOF_TTL_SECONDS:
        raise PrimaryCommandBindingError(
            f"completion {authority} proof is expired or not yet valid"
        )
    expected = hmac.new(
        secret.encode("utf-8"),
        _completion_authority_payload(request, proof),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(proof.signature, expected):
        raise PrimaryCommandBindingError(
            f"completion {authority} proof signature is invalid"
        )
    return proof


def _proof_payload(request: _BoundCommandRequest, *, path: str, proof_field: str) -> bytes:
    body = request.model_dump(mode="json")
    proof = body.pop(proof_field, None)
    if not isinstance(proof, Mapping):
        raise PrimaryCommandBindingError("authenticated ingress proof is required")
    return json.dumps(
        {
            "version": proof.get("version"),
            "method": "POST",
            "path": path,
            "issued_at": proof.get("issued_at"),
            "nonce": proof.get("nonce"),
            "channel_id": proof.get("channel_id"),
            "body": body,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_primary_ingress_proof(
    secret: str,
    shared_secret: str,
    request: _BoundCommandRequest,
    *,
    path: str,
    proof_field: str,
    issued_at: int | None = None,
    nonce: str | None = None,
    channel_id: str | None = None,
) -> PrimaryIngressProof:
    if len(secret) < _MIN_SECRET_LENGTH:
        raise PrimaryCommandBindingError("primary ingress signing secret is not configured")
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise PrimaryCommandBindingError("primary ingress secret must be distinct")
    proof = PrimaryIngressProof(
        channel_id=channel_id,
        issued_at=(
            issued_at
            if issued_at is not None
            else int(datetime.now(timezone.utc).timestamp())
        ),
        nonce=nonce or secrets.token_urlsafe(32),
        signature="0" * 64,
    )
    unsigned = request.model_copy(update={proof_field: proof})
    signature = hmac.new(
        secret.encode("utf-8"),
        _proof_payload(unsigned, path=path, proof_field=proof_field),
        hashlib.sha256,
    ).hexdigest()
    return proof.model_copy(update={"signature": signature})


def verify_primary_ingress_proof(
    secret: str,
    shared_secret: str,
    request: _BoundCommandRequest,
    *,
    path: str,
    proof_field: str,
    now: int | None = None,
) -> str:
    proof = getattr(request, proof_field, None)
    if not isinstance(proof, PrimaryIngressProof) or len(secret) < _MIN_SECRET_LENGTH:
        raise PrimaryCommandBindingError("authenticated primary ingress proof is required")
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise PrimaryCommandBindingError("primary ingress secret must be distinct")
    observed = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    age = observed - proof.issued_at
    if age < -_PROOF_FUTURE_SKEW_SECONDS or age > _PROOF_TTL_SECONDS:
        raise PrimaryCommandBindingError("primary ingress proof is expired or not yet valid")
    expected = hmac.new(
        secret.encode("utf-8"),
        _proof_payload(request, path=path, proof_field=proof_field),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(proof.signature, expected):
        raise PrimaryCommandBindingError("primary ingress proof signature is invalid")
    return _canonical_payload_digest(
        {"task_id": request.task_id, "nonce": proof.nonce, "operation": path}
    )


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]
