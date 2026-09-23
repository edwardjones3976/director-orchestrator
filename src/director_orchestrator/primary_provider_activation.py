# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:12 UTC | BEGIN]]
"""Fail-closed activation preflight for primary provider dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping

from .provider_activation import (
    ACTIVATION_EVIDENCE_VERSION,
    ProviderActivationAuthorization,
    ProviderActivationBinding,
    ProviderActivationEvidence,
    ProviderActivationRegistry,
    ProviderActivationStage,
    ProviderActivationStore,
    ProviderActivationSurface,
)


class PrimaryProviderActivationService:
    """Resolve current observed readiness without exposing evidence identities."""

    def __init__(
        self,
        *,
        store: ProviderActivationStore,
        bindings: Mapping[str, ProviderActivationBinding],
        required_providers: tuple[str, ...],
        clock: Callable[[], datetime] | None = None,
        capture_verifier=None,
    ) -> None:
        if not required_providers or len(set(required_providers)) != len(
            required_providers
        ):
            raise ValueError("required providers must be non-empty and unique")
        if not bindings:
            raise ValueError("activation bindings must not be empty")
        normalized: dict[str, ProviderActivationRegistry] = {}
        from .primary_roster import KNOWN_LANES, core_inventory_ready
        if not set(bindings).issubset(set(required_providers) & KNOWN_LANES):
            raise ValueError("activation binding is outside the configured provider pool")
        for provider_id in bindings:
            binding = bindings.get(provider_id)
            if binding is None or binding.provider_id != provider_id:
                raise ValueError("every required provider needs an exact binding")
            normalized[provider_id] = ProviderActivationRegistry(store, binding)
        self._registries = normalized
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._store = store
        self._capture_verifier = capture_verifier

    def _live_captures_valid(self, registry) -> bool:
        if self._capture_verifier is None:
            return False
        for surface in ProviderActivationSurface:
            records = self._store.load(registry.binding,surface)
            if not records or records[-1].stage != ProviderActivationStage.LIVE_OK:
                return False
            evidence = records[-1]
            if not self._capture_verifier.verify(registry.binding,evidence.evidence_digest,
                                                 expires_at=evidence.expires_at):
                return False
        return True

    def provider_ready(self, provider_id: str, *, live: bool) -> bool:
        """Require both app and runtime-adapter evidence for one provider."""

        registry = self._registries.get(provider_id)
        if registry is None:
            return False
        try:
            readiness = registry.readiness(now=self._clock())
        except Exception:
            return False
        if live:
            try:
                return readiness.live_ready and self._live_captures_valid(registry)
            except Exception:
                return False
        return readiness.dry_run_ready

    def record(
        self,
        *,
        provider_id: str,
        surface: ProviderActivationSurface,
        stage: ProviderActivationStage,
        evidence_id: str,
        evidence_digest: str,
        observed_at: datetime,
        expires_at: datetime,
    ) -> ProviderActivationEvidence:
        """Advance one configured evidence chain under its exact binding."""

        registry = self._registries.get(provider_id)
        if registry is None:
            raise ValueError("provider is not required by this activation profile")
        if stage == ProviderActivationStage.LIVE_OK and (
            self._capture_verifier is None or not self._capture_verifier.verify(
                registry.binding,evidence_digest,expires_at=expires_at)
        ):
            raise ValueError("LIVE_OK requires authenticated native execution capture")
        return registry.record(
            surface=surface,
            stage=stage,
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            observed_at=observed_at,
            expires_at=expires_at,
            now=self._clock(),
        )

    def record_authorized(
        self,
        *,
        provider_id: str,
        evidence_schema_version: str,
        binding_version: str,
        surface: ProviderActivationSurface,
        stage: ProviderActivationStage,
        expected_revision: int,
        evidence_id: str,
        evidence_digest: str,
        observed_at: datetime,
        expires_at: datetime,
        authorization: ProviderActivationAuthorization,
    ) -> ProviderActivationEvidence:
        """Advance only under exact binding/version and durable signed authority."""

        registry = self._registries.get(provider_id)
        if registry is None:
            raise ValueError("provider is not required by this activation profile")
        if evidence_schema_version != ACTIVATION_EVIDENCE_VERSION:
            raise ValueError("provider activation evidence schema version mismatch")
        if binding_version != registry.binding.binding_version:
            raise ValueError("provider activation binding version mismatch")
        if stage == ProviderActivationStage.LIVE_OK and (
            self._capture_verifier is None or not self._capture_verifier.verify(
                registry.binding,evidence_digest,expires_at=expires_at)
        ):
            raise ValueError("LIVE_OK requires authenticated native execution capture")
        return registry.record_authorized(
            surface=surface,
            stage=stage,
            expected_revision=expected_revision,
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            observed_at=observed_at,
            expires_at=expires_at,
            now=self._clock(),
            authorization=authorization,
        )

    def health(self) -> dict[str, bool]:
        """Return aggregate booleans only; never bindings, versions, or digests."""

        now = self._clock()
        app_dry = True
        app_live = True
        adapter_dry = True
        adapter_live = True
        for registry in self._registries.values():
            try:
                readiness = registry.readiness(now=now)
                captures_valid = self._live_captures_valid(registry)
            except Exception:
                app_dry = app_live = adapter_dry = adapter_live = False
                continue
            app_dry = app_dry and readiness.app.dry_run_ready
            app_live = app_live and readiness.app.live_ready and captures_valid
            adapter_dry = adapter_dry and readiness.runtime_adapter.dry_run_ready
            adapter_live = adapter_live and readiness.runtime_adapter.live_ready and captures_valid
        from .primary_roster import core_inventory_ready
        return {
            "core_live_ready": core_inventory_ready(tuple(p for p in self._registries if self.provider_ready(p, live=True))),
            "app_dry_run_ready": app_dry,
            "app_live_ready": app_live,
            "runtime_adapter_dry_run_ready": adapter_dry,
            "runtime_adapter_live_ready": adapter_live,
            "combined_dry_run_ready": app_dry and adapter_dry,
            "combined_live_ready": app_live and adapter_live,
        }

    def capture_attestations(self) -> dict:
        """Per-lane latest capture facts for the read-only operator command; digests and expiry only, never binding contents or secrets; not part of /health."""

        from .provider_validation import binding_digest

        lanes = {}
        for provider_id, registry in self._registries.items():
            surfaces = {}
            for surface in ProviderActivationSurface:
                records = self._store.load(registry.binding, surface)
                if not records:
                    surfaces[surface.value] = None
                    continue
                latest = records[-1]
                surfaces[surface.value] = {
                    "stage": latest.stage.value,
                    "evidence_digest": latest.evidence_digest,
                    "expires_at": latest.expires_at.isoformat(),
                    "capture_verified": latest.stage is ProviderActivationStage.LIVE_OK
                    and self._capture_verifier is not None
                    and bool(self._capture_verifier.verify(
                        registry.binding, latest.evidence_digest, expires_at=latest.expires_at)),
                }
            lanes[provider_id] = {"binding_digest": binding_digest(registry.binding), "surfaces": surfaces}
        return lanes


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:46 UTC | END]]
