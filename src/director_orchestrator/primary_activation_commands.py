# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 01:02 UTC | BEGIN]]
"""Closed owner command for observed provider-activation evidence."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .provider_activation import ProviderActivationStage, ProviderActivationSurface


class ProviderActivationAdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    surface: ProviderActivationSurface
    stage: ProviderActivationStage
    evidence_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    expires_at: datetime


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 01:02 UTC | END]]
