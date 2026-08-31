# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:12 UTC | BEGIN]]
"""Fail-closed activation preflight for primary provider dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping

from .provider_activation import (
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
    ) -> None:
        if not required_providers or len(set(required_providers)) != len(
            required_providers
        ):
            raise ValueError("required providers must be non-empty and unique")
        normalized: dict[str, ProviderActivationRegistry] = {}
        for provider_id in required_providers:
            binding = bindings.get(provider_id)
            if binding is None or binding.provider_id != provider_id:
                raise ValueError("every required provider needs an exact binding")
            normalized[provider_id] = ProviderActivationRegistry(store, binding)
        if set(bindings) != set(required_providers):
            raise ValueError("activation bindings must exactly match required providers")
        self._registries = normalized
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def provider_ready(self, provider_id: str, *, live: bool) -> bool:
        """Require both app and runtime-adapter evidence for one provider."""

        registry = self._registries.get(provider_id)
        if registry is None:
            return False
        try:
            readiness = registry.readiness(now=self._clock())
        except Exception:
            return False
        return readiness.live_ready if live else readiness.dry_run_ready

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
        return registry.record(
            surface=surface,
            stage=stage,
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            observed_at=observed_at,
            expires_at=expires_at,
            now=self._clock(),
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
            except Exception:
                app_dry = app_live = adapter_dry = adapter_live = False
                continue
            app_dry = app_dry and readiness.app.dry_run_ready
            app_live = app_live and readiness.app.live_ready
            adapter_dry = adapter_dry and readiness.runtime_adapter.dry_run_ready
            adapter_live = adapter_live and readiness.runtime_adapter.live_ready
        return {
            "app_dry_run_ready": app_dry,
            "app_live_ready": app_live,
            "runtime_adapter_dry_run_ready": adapter_dry,
            "runtime_adapter_live_ready": adapter_live,
            "combined_dry_run_ready": app_dry and adapter_dry,
            "combined_live_ready": app_live and adapter_live,
        }


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:12 UTC | END]]
