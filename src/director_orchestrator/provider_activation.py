# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 11:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:46 UTC | BEGIN]]
"""Observed, fail-closed provider activation evidence.

This module records readiness facts; it never installs an app, authenticates a
provider, calls a provider, or grants dispatch authority.  App connectivity and
runtime-adapter readiness are independent evidence chains so one cannot be used
as proof for the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, Sequence


ACTIVATION_EVIDENCE_VERSION = "provider-activation-evidence/v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProviderActivationStage(StrEnum):
    INSTALLED = "INSTALLED"
    AUTHENTICATED = "AUTHENTICATED"
    WORKSPACE_BOUND = "WORKSPACE_BOUND"
    DRY_RUN_OK = "DRY_RUN_OK"
    LIVE_OK = "LIVE_OK"


ACTIVATION_STAGES = tuple(ProviderActivationStage)


class ProviderActivationSurface(StrEnum):
    APP = "app"
    RUNTIME_ADAPTER = "runtime_adapter"


@dataclass(frozen=True, slots=True)
class ProviderActivationBinding:
    provider_id: str
    workspace_id: str
    provider_version: str
    app_version: str
    runtime_adapter_version: str
    workspace_version: str
    binding_version: str

    def __post_init__(self) -> None:
        for name in (
            "provider_id",
            "workspace_id",
            "provider_version",
            "app_version",
            "runtime_adapter_version",
            "workspace_version",
            "binding_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be a bounded, non-secret identifier")


@dataclass(frozen=True, slots=True)
class ProviderActivationEvidence:
    schema_version: str
    binding: ProviderActivationBinding
    surface: ProviderActivationSurface
    stage: ProviderActivationStage
    revision: int
    evidence_id: str
    evidence_digest: str
    observed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.binding, ProviderActivationBinding):
            raise ValueError("binding must be a ProviderActivationBinding")
        if not isinstance(self.surface, ProviderActivationSurface):
            raise ValueError("surface must be a supported activation surface")
        if not isinstance(self.stage, ProviderActivationStage):
            raise ValueError("stage must be a supported activation stage")
        if self.schema_version != ACTIVATION_EVIDENCE_VERSION:
            raise ValueError("unsupported provider activation evidence version")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("revision must be positive")
        if not isinstance(self.evidence_id, str) or not _IDENTIFIER.fullmatch(
            self.evidence_id
        ):
            raise ValueError("evidence_id must be a bounded, non-secret identifier")
        if not isinstance(self.evidence_digest, str) or not _SHA256.fullmatch(
            self.evidence_digest
        ):
            raise ValueError("evidence_digest must be a lowercase SHA-256 digest")
        _require_utc(self.observed_at, "observed_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be later than observed_at")


@dataclass(frozen=True, slots=True)
class ProviderActivationAuthorization:
    proof_version: str
    actor_id: str
    nonce_digest: str
    command_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.proof_version, str) or not _IDENTIFIER.fullmatch(
            self.proof_version
        ):
            raise ValueError("proof_version must be a bounded identifier")
        if not isinstance(self.actor_id, str) or not _IDENTIFIER.fullmatch(self.actor_id):
            raise ValueError("actor_id must be a bounded identifier")
        for name in ("nonce_digest", "command_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")


class ProviderActivationStore(Protocol):
    """Persistence boundary; implementations must append atomically by revision."""

    def load(
        self,
        binding: ProviderActivationBinding,
        surface: ProviderActivationSurface,
    ) -> Sequence[ProviderActivationEvidence]: ...

    def append(
        self,
        evidence: ProviderActivationEvidence,
        *,
        expected_revision: int,
    ) -> bool:
        """Append only if the stored latest revision equals expected_revision."""

    def append_authorized(
        self,
        evidence: ProviderActivationEvidence,
        *,
        expected_revision: int,
        authorization: ProviderActivationAuthorization,
    ) -> bool:
        """Atomically claim the signed nonce and append the exact revision."""


class ProviderActivationError(RuntimeError):
    """Evidence cannot safely advance the activation chain."""


@dataclass(frozen=True, slots=True)
class ActivationSurfaceReadiness:
    surface: ProviderActivationSurface
    stage: ProviderActivationStage | None
    dry_run_ready: bool
    live_ready: bool
    reason: str | None
    latest_revision: int


@dataclass(frozen=True, slots=True)
class ProviderActivationReadiness:
    binding: ProviderActivationBinding
    app: ActivationSurfaceReadiness
    runtime_adapter: ActivationSurfaceReadiness

    @property
    def dry_run_ready(self) -> bool:
        return self.app.dry_run_ready and self.runtime_adapter.dry_run_ready

    @property
    def live_ready(self) -> bool:
        return self.app.live_ready and self.runtime_adapter.live_ready


class ProviderActivationRegistry:
    """Validates and persists observed activation evidence for one binding."""

    def __init__(
        self,
        store: ProviderActivationStore,
        binding: ProviderActivationBinding,
    ) -> None:
        self._store = store
        self.binding = binding

    def record(
        self,
        *,
        surface: ProviderActivationSurface,
        stage: ProviderActivationStage,
        evidence_id: str,
        evidence_digest: str,
        observed_at: datetime,
        expires_at: datetime,
        now: datetime,
    ) -> ProviderActivationEvidence:
        _require_utc(now, "now")
        if not isinstance(surface, ProviderActivationSurface):
            raise ValueError("surface must be a supported activation surface")
        if not isinstance(stage, ProviderActivationStage):
            raise ValueError("stage must be a supported activation stage")
        records = self._validated_records(surface)
        if records:
            latest = records[-1]
            if stage == latest.stage:
                candidate = ProviderActivationEvidence(
                    schema_version=ACTIVATION_EVIDENCE_VERSION,
                    binding=self.binding,
                    surface=surface,
                    stage=stage,
                    revision=latest.revision,
                    evidence_id=evidence_id,
                    evidence_digest=evidence_digest,
                    observed_at=observed_at,
                    expires_at=expires_at,
                )
                if candidate == latest:
                    return latest
                raise ProviderActivationError(
                    "a reached stage cannot be replaced within the same binding version"
                )
            expected_stage_index = ACTIVATION_STAGES.index(latest.stage) + 1
        else:
            latest = None
            expected_stage_index = 0

        if expected_stage_index >= len(ACTIVATION_STAGES):
            raise ProviderActivationError("LIVE_OK is terminal for this binding version")
        expected_stage = ACTIVATION_STAGES[expected_stage_index]
        if stage != expected_stage:
            raise ProviderActivationError(
                f"activation evidence must advance exactly to {expected_stage.value}"
            )
        if observed_at > now:
            raise ProviderActivationError("future-dated activation evidence is invalid")
        if expires_at <= now:
            raise ProviderActivationError("expired activation evidence cannot be recorded")
        if latest is not None:
            if latest.expires_at <= now:
                raise ProviderActivationError(
                    "the prior activation stage expired; use a new binding version"
                )
            if observed_at < latest.observed_at:
                raise ProviderActivationError("activation observations must be monotonic")

        expected_revision = latest.revision if latest is not None else 0
        evidence = ProviderActivationEvidence(
            schema_version=ACTIVATION_EVIDENCE_VERSION,
            binding=self.binding,
            surface=surface,
            stage=stage,
            revision=expected_revision + 1,
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            observed_at=observed_at,
            expires_at=expires_at,
        )
        if not self._store.append(evidence, expected_revision=expected_revision):
            concurrent = self._validated_records(surface)
            if concurrent and concurrent[-1] == evidence:
                return concurrent[-1]
            raise ProviderActivationError("activation evidence store revision conflict")
        return evidence

    def record_authorized(
        self,
        *,
        surface: ProviderActivationSurface,
        stage: ProviderActivationStage,
        expected_revision: int,
        evidence_id: str,
        evidence_digest: str,
        observed_at: datetime,
        expires_at: datetime,
        now: datetime,
        authorization: ProviderActivationAuthorization,
    ) -> ProviderActivationEvidence:
        """Advance under a signed command with an atomic durable nonce claim."""

        _require_utc(now, "now")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise ValueError("expected_revision must be a non-negative integer")
        if expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if not isinstance(authorization, ProviderActivationAuthorization):
            raise ValueError("signed provider activation authorization is required")
        if not isinstance(surface, ProviderActivationSurface):
            raise ValueError("surface must be a supported activation surface")
        if not isinstance(stage, ProviderActivationStage):
            raise ValueError("stage must be a supported activation stage")
        if expected_revision >= len(ACTIVATION_STAGES):
            raise ProviderActivationError("LIVE_OK is terminal for this binding version")
        expected_stage = ACTIVATION_STAGES[expected_revision]
        if stage != expected_stage:
            raise ProviderActivationError(
                f"activation evidence must advance exactly to {expected_stage.value}"
            )
        records = self._validated_records(surface)
        if len(records) not in (expected_revision, expected_revision + 1):
            raise ProviderActivationError("activation evidence store revision conflict")
        prior = records[expected_revision - 1] if expected_revision else None
        if observed_at > now:
            raise ProviderActivationError("future-dated activation evidence is invalid")
        if expires_at <= now:
            raise ProviderActivationError("expired activation evidence cannot be recorded")
        if prior is not None:
            if prior.expires_at <= now:
                raise ProviderActivationError(
                    "the prior activation stage expired; use a new binding version"
                )
            if observed_at < prior.observed_at:
                raise ProviderActivationError("activation observations must be monotonic")
        evidence = ProviderActivationEvidence(
            schema_version=ACTIVATION_EVIDENCE_VERSION,
            binding=self.binding,
            surface=surface,
            stage=stage,
            revision=expected_revision + 1,
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            observed_at=observed_at,
            expires_at=expires_at,
        )
        if len(records) == expected_revision + 1 and records[-1] != evidence:
            raise ProviderActivationError(
                "a reached stage cannot be replaced within the same binding version"
            )
        append_authorized = getattr(self._store, "append_authorized", None)
        if not callable(append_authorized):
            raise ProviderActivationError(
                "activation store lacks durable signed replay protection"
            )
        if not append_authorized(
            evidence,
            expected_revision=expected_revision,
            authorization=authorization,
        ):
            concurrent = self._validated_records(surface)
            if (
                len(concurrent) == expected_revision + 1
                and concurrent[-1] == evidence
            ):
                return concurrent[-1]
            raise ProviderActivationError("activation evidence store revision conflict")
        return evidence

    def readiness(self, *, now: datetime) -> ProviderActivationReadiness:
        _require_utc(now, "now")
        return ProviderActivationReadiness(
            binding=self.binding,
            app=self._surface_readiness(ProviderActivationSurface.APP, now),
            runtime_adapter=self._surface_readiness(
                ProviderActivationSurface.RUNTIME_ADAPTER, now
            ),
        )

    def _surface_readiness(
        self, surface: ProviderActivationSurface, now: datetime
    ) -> ActivationSurfaceReadiness:
        try:
            records = self._validated_records(surface)
        except (ProviderActivationError, AttributeError, TypeError, ValueError) as exc:
            return ActivationSurfaceReadiness(surface, None, False, False, str(exc), 0)
        if not records:
            return ActivationSurfaceReadiness(
                surface, None, False, False, "no activation evidence", 0
            )
        for record in records:
            if record.expires_at <= now:
                return ActivationSurfaceReadiness(
                    surface,
                    None,
                    False,
                    False,
                    f"{record.stage.value} evidence expired",
                    records[-1].revision,
                )
        stage = records[-1].stage
        stage_index = ACTIVATION_STAGES.index(stage)
        return ActivationSurfaceReadiness(
            surface=surface,
            stage=stage,
            dry_run_ready=stage_index
            >= ACTIVATION_STAGES.index(ProviderActivationStage.DRY_RUN_OK),
            live_ready=stage is ProviderActivationStage.LIVE_OK,
            reason=None,
            latest_revision=records[-1].revision,
        )

    def _validated_records(
        self, surface: ProviderActivationSurface
    ) -> tuple[ProviderActivationEvidence, ...]:
        records = tuple(self._store.load(self.binding, surface))
        for index, record in enumerate(records):
            if not isinstance(record, ProviderActivationEvidence):
                raise ProviderActivationError("activation store returned invalid evidence")
            expected_revision = index + 1
            expected_stage = ACTIVATION_STAGES[index] if index < len(ACTIVATION_STAGES) else None
            if record.binding != self.binding or record.surface != surface:
                raise ProviderActivationError("activation evidence binding drift")
            if record.schema_version != ACTIVATION_EVIDENCE_VERSION:
                raise ProviderActivationError("activation evidence version drift")
            if record.revision != expected_revision:
                raise ProviderActivationError("activation evidence revision gap")
            if expected_stage is None or record.stage != expected_stage:
                raise ProviderActivationError("activation evidence stage gap or regression")
            if index and record.observed_at < records[index - 1].observed_at:
                raise ProviderActivationError("activation observation order regressed")
        return records


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")


__all__ = [
    "ACTIVATION_EVIDENCE_VERSION",
    "ACTIVATION_STAGES",
    "ActivationSurfaceReadiness",
    "ProviderActivationBinding",
    "ProviderActivationAuthorization",
    "ProviderActivationError",
    "ProviderActivationEvidence",
    "ProviderActivationReadiness",
    "ProviderActivationRegistry",
    "ProviderActivationStage",
    "ProviderActivationStore",
    "ProviderActivationSurface",
]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 11:42 UTC | END]]
