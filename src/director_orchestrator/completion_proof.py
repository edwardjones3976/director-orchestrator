# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 03:05 UTC | BEGIN]]
# [[Auto (Cursor Agent) | Composer | 2026-09-02 | 02:41 UTC | BEGIN]]
"""Cryptographic verification for durable completion-authority proofs.

Role-specific HMAC secrets, issuers, and principals are checked against the
exact task, command, candidate, provider-result, ledger, and gate bindings.
Secrets are injectable, never persisted, and never logged from this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from pydantic import ValidationError

from .primary_commands import (
    CompletionAuthorityProof,
    CompletionEvidenceRequest,
    PrimaryCommandBindingError,
)

if False:  # pragma: no cover - import cycle guard for type checkers
    from .primary_completion import CompletionEvidence

_PROOF_TTL_SECONDS = 300
_PROOF_FUTURE_SKEW_SECONDS = 30
_MIN_SECRET_LENGTH = 32

_COMPLETION_AUTHORITY_PROOF_FIELDS = {
    "independent_review": "independent_review_authority_proof",
    "ci": "ci_authority_proof",
    "security": "security_authority_proof",
}

CompletionAuthorityRole = Literal["independent_review", "ci", "security"]


@dataclass(frozen=True, slots=True)
class CompletionAuthorityBinding:
    """Exact durable bindings covered by one completion-authority HMAC."""

    task_id: str
    command_id: str
    candidate_sha: str
    provider_result_digest: str
    ledger_digest: str
    results: tuple[Mapping[str, Any], ...]
    ci_gate_id: str
    security_gate_id: str

    @classmethod
    def from_request(cls, request: CompletionEvidenceRequest) -> CompletionAuthorityBinding:
        ledger_digest = str(request.ledger.get("ledger_digest", ""))
        if not ledger_digest:
            raise PrimaryCommandBindingError("completion ledger digest is required")
        return cls(
            task_id=request.task_id,
            command_id=request.command_id,
            candidate_sha=request.candidate_sha,
            provider_result_digest=request.provider_result_digest,
            ledger_digest=ledger_digest,
            results=tuple(dict(item) for item in request.results),
            ci_gate_id=request.ci_gate_id,
            security_gate_id=request.security_gate_id,
        )

    @classmethod
    def from_evidence(
        cls,
        evidence: CompletionEvidence,
        *,
        task_id: str,
        candidate_sha: str,
        provider_result_digest: str,
    ) -> CompletionAuthorityBinding:
        if not evidence.command_id:
            raise PrimaryCommandBindingError(
                "completion evidence command binding is required"
            )
        return cls(
            task_id=task_id,
            command_id=evidence.command_id,
            candidate_sha=candidate_sha,
            provider_result_digest=provider_result_digest,
            ledger_digest=evidence.ledger.digest,
            results=tuple(dict(item.document) for item in evidence.results),
            ci_gate_id=evidence.ci_gate_id,
            security_gate_id=evidence.security_gate_id,
        )


def _completion_authority_result(
    binding: CompletionAuthorityBinding, authority: str
) -> Mapping[str, Any]:
    if authority == "independent_review":
        matches = [
            item
            for item in binding.results
            if item.get("evidence_role") == "INDEPENDENT_REVIEWER"
        ]
    elif authority == "ci":
        matches = [
            item
            for item in binding.results
            if item.get("gate_id") == binding.ci_gate_id
            and item.get("evidence_role") == "TRUSTED_EXTERNAL_CI"
        ]
    elif authority == "security":
        matches = [
            item
            for item in binding.results
            if item.get("gate_id") == binding.security_gate_id
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
    binding: CompletionAuthorityBinding,
    proof: CompletionAuthorityProof,
) -> bytes:
    result = _completion_authority_result(binding, proof.authority)
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
            "task_id": binding.task_id,
            "command_id": binding.command_id,
            "candidate_sha": binding.candidate_sha,
            "provider_result_digest": binding.provider_result_digest,
            "ledger_digest": binding.ledger_digest,
            "gate_id": result.get("gate_id"),
            "result_digest": result.get("result_digest"),
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_distinct_secrets(
    *,
    independent_review_secret: str,
    ci_secret: str,
    security_secret: str,
    shared_secret: str,
) -> None:
    secrets_by_role = (
        independent_review_secret,
        ci_secret,
        security_secret,
    )
    if not all(len(value) >= _MIN_SECRET_LENGTH for value in secrets_by_role):
        raise PrimaryCommandBindingError(
            "completion authority signing secret is not configured"
        )
    if len(set(secrets_by_role)) != 3:
        raise PrimaryCommandBindingError(
            "completion authority secrets must be distinct"
        )
    if shared_secret:
        for value in secrets_by_role:
            if hmac.compare_digest(value, shared_secret):
                raise PrimaryCommandBindingError(
                    "completion authority secret must be distinct"
                )


@dataclass(frozen=True, slots=True)
class CompletionAuthorityVerifier:
    """Injectable verifier for role-specific completion-authority HMAC proofs."""

    independent_review_secret: str = field(repr=False)
    ci_secret: str = field(repr=False)
    security_secret: str = field(repr=False)
    shared_secret: str = field(repr=False, default="")

    def __post_init__(self) -> None:
        _validate_distinct_secrets(
            independent_review_secret=self.independent_review_secret,
            ci_secret=self.ci_secret,
            security_secret=self.security_secret,
            shared_secret=self.shared_secret,
        )

    def secret_for(self, authority: CompletionAuthorityRole) -> str:
        if authority == "independent_review":
            return self.independent_review_secret
        if authority == "ci":
            return self.ci_secret
        return self.security_secret

    def verify_proof(
        self,
        binding: CompletionAuthorityBinding,
        proof: CompletionAuthorityProof,
        *,
        now: int | None = None,
        enforce_freshness: bool = True,
    ) -> CompletionAuthorityProof:
        if proof.authority not in _COMPLETION_AUTHORITY_PROOF_FIELDS:
            raise PrimaryCommandBindingError("completion authority is unsupported")
        secret = self.secret_for(proof.authority)
        if enforce_freshness:
            observed = now if now is not None else int(datetime.now(timezone.utc).timestamp())
            age = observed - proof.issued_at
            if age < -_PROOF_FUTURE_SKEW_SECONDS or age > _PROOF_TTL_SECONDS:
                raise PrimaryCommandBindingError(
                    f"completion {proof.authority} proof is expired or not yet valid"
                )
        expected = hmac.new(
            secret.encode("utf-8"),
            _completion_authority_payload(binding, proof),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(proof.signature, expected):
            raise PrimaryCommandBindingError(
                f"completion {proof.authority} proof signature is invalid"
            )
        return proof

    def verify_binding(
        self,
        binding: CompletionAuthorityBinding,
        authority_proofs: Mapping[str, Mapping[str, object]],
        *,
        now: int | None = None,
        enforce_freshness: bool = True,
    ) -> dict[str, CompletionAuthorityProof]:
        required = set(_COMPLETION_AUTHORITY_PROOF_FIELDS)
        if set(authority_proofs) != required:
            raise PrimaryCommandBindingError(
                "completion evidence requires three authority proofs"
            )
        verified: dict[str, CompletionAuthorityProof] = {}
        issuers: set[str] = set()
        principals: set[str] = set()
        for authority in sorted(required):
            try:
                proof = CompletionAuthorityProof.model_validate(
                    authority_proofs[authority]
                )
            except ValidationError as exc:
                raise PrimaryCommandBindingError(
                    f"completion {authority} authority proof is invalid"
                ) from exc
            if proof.authority != authority:
                raise PrimaryCommandBindingError(
                    "completion authority proof role binding is invalid"
                )
            verified[authority] = self.verify_proof(
                binding, proof, now=now, enforce_freshness=enforce_freshness
            )
            issuers.add(proof.issuer_id)
            principals.add(proof.principal_id)
        if len(issuers) != 3 or len(principals) != 3:
            raise PrimaryCommandBindingError(
                "completion evidence authorities are not distinct"
            )
        return verified

    def verify_request(
        self,
        request: CompletionEvidenceRequest,
        *,
        authority: CompletionAuthorityRole,
        now: int | None = None,
        enforce_freshness: bool = True,
    ) -> CompletionAuthorityProof:
        field_name = _COMPLETION_AUTHORITY_PROOF_FIELDS[authority]
        proof = getattr(request, field_name)
        if not isinstance(proof, CompletionAuthorityProof) or proof.authority != authority:
            raise PrimaryCommandBindingError(
                f"authenticated completion {authority} proof is required"
            )
        return self.verify_proof(
            CompletionAuthorityBinding.from_request(request),
            proof,
            now=now,
            enforce_freshness=enforce_freshness,
        )

    def verify_evidence(
        self,
        evidence: CompletionEvidence,
        *,
        task_id: str,
        candidate_sha: str,
        provider_result_digest: str,
        now: int | None = None,
        enforce_freshness: bool = True,
    ) -> dict[str, CompletionAuthorityProof]:
        binding = CompletionAuthorityBinding.from_evidence(
            evidence,
            task_id=task_id,
            candidate_sha=candidate_sha,
            provider_result_digest=provider_result_digest,
        )
        return self.verify_binding(
            binding,
            evidence.authority_proofs,
            now=now,
            enforce_freshness=enforce_freshness,
        )


def build_completion_authority_proof(
    secret: str,
    shared_secret: str,
    request: CompletionEvidenceRequest,
    *,
    authority: CompletionAuthorityRole,
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
    binding = CompletionAuthorityBinding.from_request(request)
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
        _completion_authority_payload(binding, proof),
        hashlib.sha256,
    ).hexdigest()
    return proof.model_copy(update={"signature": signature})


def verify_completion_authority_proof(
    secret: str,
    shared_secret: str,
    request: CompletionEvidenceRequest,
    *,
    authority: CompletionAuthorityRole,
    now: int | None = None,
) -> CompletionAuthorityProof:
    if len(secret) < _MIN_SECRET_LENGTH:
        raise PrimaryCommandBindingError(
            f"authenticated completion {authority} proof is required"
        )
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise PrimaryCommandBindingError("completion authority secret must be distinct")
    binding = CompletionAuthorityBinding.from_request(request)
    field_name = _COMPLETION_AUTHORITY_PROOF_FIELDS[authority]
    proof = getattr(request, field_name)
    if not isinstance(proof, CompletionAuthorityProof) or proof.authority != authority:
        raise PrimaryCommandBindingError(
            f"authenticated completion {authority} proof is required"
        )
    observed = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    age = observed - proof.issued_at
    if age < -_PROOF_FUTURE_SKEW_SECONDS or age > _PROOF_TTL_SECONDS:
        raise PrimaryCommandBindingError(
            f"completion {authority} proof is expired or not yet valid"
        )
    expected = hmac.new(
        secret.encode("utf-8"),
        _completion_authority_payload(binding, proof),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(proof.signature, expected):
        raise PrimaryCommandBindingError(
            f"completion {authority} proof signature is invalid"
        )
    return proof

# [[Auto (Cursor Agent) | Composer | 2026-09-02 | 02:41 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 03:05 UTC | END]]
