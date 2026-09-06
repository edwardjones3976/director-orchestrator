# [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 18:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:12 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from director_orchestrator.primary_provider_activation import (
    PrimaryProviderActivationService,
)
from director_orchestrator.provider_activation import (
    ACTIVATION_EVIDENCE_VERSION,
    ProviderActivationAuthorization,
    ProviderActivationBinding,
    ProviderActivationRegistry,
    ProviderActivationStage,
    ProviderActivationSurface,
)


class MemoryStore:
    def __init__(self) -> None:
        self.rows = {}
        self.claims = {}

    def load(self, binding, surface):
        return tuple(self.rows.get((binding, surface), ()))

    def append(self, evidence, *, expected_revision):
        key = (evidence.binding, evidence.surface)
        rows = self.rows.setdefault(key, [])
        if len(rows) != expected_revision:
            return False
        rows.append(evidence)
        return True

    def append_authorized(self, evidence, *, expected_revision, authorization):
        claim = (
            authorization.command_digest,
            authorization.proof_version,
            authorization.actor_id,
            evidence,
        )
        existing = self.claims.get(authorization.nonce_digest)
        if existing is not None:
            return existing == claim
        key = (evidence.binding, evidence.surface)
        rows = self.rows.setdefault(key, [])
        if len(rows) != expected_revision:
            return False
        self.claims[authorization.nonce_digest] = claim
        rows.append(evidence)
        return True


class PrimaryProviderActivationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.store = MemoryStore()
        self.bindings = {
            provider: ProviderActivationBinding(
                provider_id=provider,
                workspace_id="default-director",
                provider_version="v1",
                app_version="v1",
                runtime_adapter_version="v1",
                workspace_version="v1",
                binding_version="v1",
            )
            for provider in ("openai", "claude")
        }
        self.service = PrimaryProviderActivationService(
            store=self.store,
            bindings=self.bindings,
            required_providers=("openai", "claude"),
            clock=lambda: self.now,
        )

    def advance(self, provider, surface, through):
        registry = ProviderActivationRegistry(self.store, self.bindings[provider])
        stages = list(ProviderActivationStage)
        for index, stage in enumerate(stages[: stages.index(through) + 1], 1):
            registry.record(
                surface=surface,
                stage=stage,
                evidence_id=f"evidence-{provider}-{surface.value}-{index}",
                evidence_digest=hashlib.sha256(
                    f"{provider}:{surface.value}:{stage.value}".encode()
                ).hexdigest(),
                observed_at=self.now + timedelta(seconds=index),
                expires_at=self.now + timedelta(hours=1),
                now=self.now + timedelta(seconds=index),
            )

    def test_requires_exact_binding_set(self):
        with self.assertRaisesRegex(ValueError, "outside the configured"):
            PrimaryProviderActivationService(
                store=self.store,
                bindings={**self.bindings, "extra": self.bindings["openai"]},
                required_providers=("openai", "claude"),
            )

    def test_combined_readiness_requires_every_provider_and_surface(self):
        for provider in self.bindings:
            self.advance(
                provider,
                ProviderActivationSurface.APP,
                ProviderActivationStage.DRY_RUN_OK,
            )
            self.advance(
                provider,
                ProviderActivationSurface.RUNTIME_ADAPTER,
                ProviderActivationStage.DRY_RUN_OK,
            )

        health = self.service.health()

        self.assertTrue(health["combined_dry_run_ready"])
        self.assertFalse(health["combined_live_ready"])
        self.assertTrue(self.service.provider_ready("openai", live=False))
        self.assertFalse(self.service.provider_ready("openai", live=True))
        self.assertFalse(self.service.provider_ready("unknown", live=False))

    def test_one_missing_surface_fails_global_and_provider_preflight(self):
        self.advance(
            "openai",
            ProviderActivationSurface.APP,
            ProviderActivationStage.LIVE_OK,
        )
        self.advance(
            "openai",
            ProviderActivationSurface.RUNTIME_ADAPTER,
            ProviderActivationStage.LIVE_OK,
        )
        self.advance(
            "claude",
            ProviderActivationSurface.APP,
            ProviderActivationStage.LIVE_OK,
        )

        self.assertTrue(self.service.provider_ready("openai", live=True))
        self.assertFalse(self.service.provider_ready("claude", live=True))
        self.assertFalse(self.service.health()["combined_live_ready"])

    def test_disabled_lane_placeholder_mirrors_live_health_keys(self):
        # While the primary lane is disabled, /health emits a literal
        # placeholder instead of this service. Its key set must equal the live
        # key set so an operator can tell the two apart only by value, never by
        # a missing key, and every placeholder value must be False.
        import re
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "director_orchestrator"
            / "main.py"
        ).read_text(encoding="utf-8")
        block = re.search(
            r'"provider_activation": \{(?P<body>.*?)\n\s*\},', source, re.DOTALL
        )
        self.assertIsNotNone(block)
        placeholder = dict(
            re.findall(r'"([a-z_]+)": (False|True)', block.group("body"))
        )

        self.assertEqual(set(placeholder), set(self.service.health()))
        self.assertEqual(set(placeholder.values()), {"False"})
        self.assertIn("core_live_ready", placeholder)

    def test_record_advances_only_a_required_provider(self):
        evidence = self.service.record(
            provider_id="openai",
            surface=ProviderActivationSurface.APP,
            stage=ProviderActivationStage.INSTALLED,
            evidence_id="openai-app-installed-1",
            evidence_digest=hashlib.sha256(b"installed").hexdigest(),
            observed_at=self.now,
            expires_at=self.now + timedelta(hours=1),
        )
        self.assertEqual(1, evidence.revision)
        with self.assertRaisesRegex(ValueError, "not required"):
            self.service.record(
                provider_id="unknown",
                surface=ProviderActivationSurface.APP,
                stage=ProviderActivationStage.INSTALLED,
                evidence_id="unknown-installed-1",
                evidence_digest=hashlib.sha256(b"unknown").hexdigest(),
                observed_at=self.now,
                expires_at=self.now + timedelta(hours=1),
            )

    def test_authorized_record_binds_version_revision_and_replays_one_nonce(self):
        authorization = ProviderActivationAuthorization(
            proof_version="provider-activation-advance-proof/v1",
            actor_id="UOWNER",
            nonce_digest="a" * 64,
            command_digest="b" * 64,
        )
        arguments = {
            "provider_id": "openai",
            "evidence_schema_version": ACTIVATION_EVIDENCE_VERSION,
            "binding_version": "v1",
            "surface": ProviderActivationSurface.APP,
            "stage": ProviderActivationStage.INSTALLED,
            "expected_revision": 0,
            "evidence_id": "openai-app-installed-signed",
            "evidence_digest": hashlib.sha256(b"signed-installed").hexdigest(),
            "observed_at": self.now,
            "expires_at": self.now + timedelta(hours=1),
            "authorization": authorization,
        }
        first = self.service.record_authorized(**arguments)
        replay = self.service.record_authorized(**arguments)
        self.assertEqual(first, replay)
        self.assertEqual(1, len(self.store.claims))
        self.assertEqual(1, len(self.store.rows[(first.binding, first.surface)]))
        with self.assertRaisesRegex(ValueError, "binding version mismatch"):
            self.service.record_authorized(**{**arguments, "binding_version": "v2"})
        with self.assertRaisesRegex(ValueError, "schema version mismatch"):
            self.service.record_authorized(
                **{**arguments, "evidence_schema_version": "invalid/v1"}
            )


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 18:20 UTC | END]]
