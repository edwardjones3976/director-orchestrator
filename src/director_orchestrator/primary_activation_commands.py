# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 01:02 UTC | BEGIN]]
"""Closed owner command for observed provider-activation evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .provider_activation import (
    ACTIVATION_EVIDENCE_VERSION,
    ProviderActivationAuthorization,
    ProviderActivationStage,
    ProviderActivationSurface,
)


ACTIVATION_ADVANCE_PROOF_VERSION = "provider-activation-advance-proof/v1"
_MIN_SECRET_LENGTH = 32
_PROOF_TTL_SECONDS = 300
_PROOF_FUTURE_SKEW_SECONDS = 30


class ProviderActivationAdvanceProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["provider-activation-advance-proof/v1"] = (
        ACTIVATION_ADVANCE_PROOF_VERSION
    )
    issued_at: int = Field(ge=0)
    nonce: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProviderActivationAdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    evidence_schema_version: Literal["provider-activation-evidence/v1"] = (
        ACTIVATION_EVIDENCE_VERSION
    )
    binding_version: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    surface: ProviderActivationSurface
    stage: ProviderActivationStage
    expected_revision: int = Field(ge=0)
    evidence_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    expires_at: datetime
    gateway_proof: ProviderActivationAdvanceProof


class ProviderActivationAdvanceProofError(ValueError):
    """The activation command lacks exact authenticated gateway authority."""


def _proof_payload(
    provider_id: str, request: ProviderActivationAdvanceRequest
) -> bytes:
    body = request.model_dump(mode="json")
    proof = body.pop("gateway_proof")
    return json.dumps(
        {
            "version": proof["version"],
            "method": "POST",
            "path": f"/v1/primary/provider-activations/{provider_id}/advance",
            "issued_at": proof["issued_at"],
            "nonce": proof["nonce"],
            "body": body,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_provider_activation_advance_proof(
    secret: str,
    shared_secret: str,
    provider_id: str,
    request: ProviderActivationAdvanceRequest,
    *,
    issued_at: int | None = None,
    nonce: str | None = None,
) -> ProviderActivationAdvanceProof:
    if len(secret) < _MIN_SECRET_LENGTH:
        raise ProviderActivationAdvanceProofError(
            "provider activation gateway signing secret is not configured"
        )
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise ProviderActivationAdvanceProofError(
            "provider activation gateway secret must be distinct"
        )
    proof = ProviderActivationAdvanceProof(
        issued_at=(
            issued_at
            if issued_at is not None
            else int(datetime.now().astimezone().timestamp())
        ),
        nonce=nonce or secrets.token_urlsafe(32),
        signature="0" * 64,
    )
    unsigned = request.model_copy(update={"gateway_proof": proof})
    signature = hmac.new(
        secret.encode("utf-8"), _proof_payload(provider_id, unsigned), hashlib.sha256
    ).hexdigest()
    return proof.model_copy(update={"signature": signature})


def verify_provider_activation_advance_proof(
    secret: str,
    shared_secret: str,
    provider_id: str,
    request: ProviderActivationAdvanceRequest,
    *,
    now: int | None = None,
) -> ProviderActivationAuthorization:
    proof = request.gateway_proof
    if len(secret) < _MIN_SECRET_LENGTH:
        raise ProviderActivationAdvanceProofError(
            "authenticated provider activation gateway proof is required"
        )
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise ProviderActivationAdvanceProofError(
            "provider activation gateway secret must be distinct"
        )
    observed = now if now is not None else int(datetime.now().astimezone().timestamp())
    age = observed - proof.issued_at
    if age < -_PROOF_FUTURE_SKEW_SECONDS or age > _PROOF_TTL_SECONDS:
        raise ProviderActivationAdvanceProofError(
            "provider activation gateway proof is expired or not yet valid"
        )
    payload = _proof_payload(provider_id, request)
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(proof.signature, expected):
        raise ProviderActivationAdvanceProofError(
            "provider activation gateway proof signature is invalid"
        )
    return ProviderActivationAuthorization(
        proof_version=proof.version,
        actor_id=request.actor_id,
        nonce_digest=hashlib.sha256(
            json.dumps(
                {
                    "version": proof.version,
                    "provider_id": provider_id,
                    "nonce": proof.nonce,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        command_digest=hashlib.sha256(payload).hexdigest(),
    )


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | END]]
