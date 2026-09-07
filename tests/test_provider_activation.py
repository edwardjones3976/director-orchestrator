# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:46 UTC | BEGIN]]
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from director_orchestrator.provider_activation import (
    ProviderActivationBinding,
    ProviderActivationError,
    ProviderActivationEvidence,
    ProviderActivationRegistry,
    ProviderActivationStage,
    ProviderActivationSurface,
)


class InMemoryActivationStore:
    def __init__(self) -> None:
        self.records: dict[tuple[ProviderActivationBinding, ProviderActivationSurface], list[ProviderActivationEvidence]] = {}
        self.force_conflict = False

    def load(self, binding, surface):
        return tuple(self.records.get((binding, surface), ()))

    def append(self, evidence, *, expected_revision):
        records = self.records.setdefault((evidence.binding, evidence.surface), [])
        if self.force_conflict or len(records) != expected_revision:
            return False
        records.append(evidence)
        return True


class ProviderActivationTests(unittest.TestCase):
    now = datetime(2026, 8, 31, 23, 50, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.binding = ProviderActivationBinding(
            provider_id="openai",
            workspace_id="director-default",
            provider_version="codex-v1",
            app_version="chatgpt-app-v1",
            runtime_adapter_version="responses-adapter-v1",
            workspace_version="default-v1",
            binding_version="activation-v1",
        )
        self.store = InMemoryActivationStore()
        self.registry = ProviderActivationRegistry(self.store, self.binding)

    def record(self, surface, stage, *, offset=0, lifetime=60):
        observed = self.now + timedelta(seconds=offset)
        return self.registry.record(
            surface=surface,
            stage=stage,
            evidence_id=f"{surface.value}-{stage.value.lower()}",
            evidence_digest=(f"{len(self.store.records.get((self.binding, surface), [])) + 1:064x}"),
            observed_at=observed,
            expires_at=observed + timedelta(seconds=lifetime),
            now=observed,
        )

    def advance(self, surface, through):
        for index, stage in enumerate(ProviderActivationStage):
            self.record(surface, stage, offset=index)
            if stage is through:
                break

    def test_app_and_runtime_adapter_advance_independently(self):
        self.advance(ProviderActivationSurface.APP, ProviderActivationStage.LIVE_OK)
        self.advance(
            ProviderActivationSurface.RUNTIME_ADAPTER,
            ProviderActivationStage.DRY_RUN_OK,
        )

        readiness = self.registry.readiness(now=self.now + timedelta(seconds=5))

        self.assertTrue(readiness.app.live_ready)
        self.assertTrue(readiness.runtime_adapter.dry_run_ready)
        self.assertFalse(readiness.runtime_adapter.live_ready)
        self.assertTrue(readiness.dry_run_ready)
        self.assertFalse(readiness.live_ready)

    def test_gap_regression_and_different_same_stage_evidence_fail_closed(self):
        with self.assertRaisesRegex(ProviderActivationError, "exactly to INSTALLED"):
            self.record(
                ProviderActivationSurface.APP,
                ProviderActivationStage.AUTHENTICATED,
            )
        installed = self.record(
            ProviderActivationSurface.APP, ProviderActivationStage.INSTALLED
        )
        with self.assertRaisesRegex(ProviderActivationError, "cannot be replaced"):
            self.registry.record(
                surface=ProviderActivationSurface.APP,
                stage=ProviderActivationStage.INSTALLED,
                evidence_id="different-evidence",
                evidence_digest="f" * 64,
                observed_at=installed.observed_at,
                expires_at=installed.expires_at,
                now=self.now,
            )
        with self.assertRaisesRegex(ProviderActivationError, "exactly to AUTHENTICATED"):
            self.record(
                ProviderActivationSurface.APP,
                ProviderActivationStage.WORKSPACE_BOUND,
            )

    def test_exact_replay_is_idempotent(self):
        evidence = self.record(
            ProviderActivationSurface.APP, ProviderActivationStage.INSTALLED
        )
        replay = self.registry.record(
            surface=evidence.surface,
            stage=evidence.stage,
            evidence_id=evidence.evidence_id,
            evidence_digest=evidence.evidence_digest,
            observed_at=evidence.observed_at,
            expires_at=evidence.expires_at,
            now=self.now,
        )
        self.assertIs(replay, evidence)
        self.assertEqual(len(self.store.load(self.binding, evidence.surface)), 1)

    def test_expiry_invalidates_entire_surface_and_blocks_progression(self):
        self.record(
            ProviderActivationSurface.RUNTIME_ADAPTER,
            ProviderActivationStage.INSTALLED,
            lifetime=1,
        )
        later = self.now + timedelta(seconds=2)
        readiness = self.registry.readiness(now=later).runtime_adapter
        self.assertIsNone(readiness.stage)
        self.assertIn("expired", readiness.reason or "")
        with self.assertRaisesRegex(ProviderActivationError, "new binding version"):
            self.registry.record(
                surface=ProviderActivationSurface.RUNTIME_ADAPTER,
                stage=ProviderActivationStage.AUTHENTICATED,
                evidence_id="runtime-authenticated",
                evidence_digest="a" * 64,
                observed_at=later,
                expires_at=later + timedelta(minutes=1),
                now=later,
            )

    def test_binding_version_provider_workspace_and_surface_drift_fail_closed(self):
        evidence = self.record(
            ProviderActivationSurface.APP, ProviderActivationStage.INSTALLED
        )
        drifted = ProviderActivationBinding(
            provider_id="claude",
            workspace_id=self.binding.workspace_id,
            provider_version=self.binding.provider_version,
            app_version=self.binding.app_version,
            runtime_adapter_version=self.binding.runtime_adapter_version,
            workspace_version=self.binding.workspace_version,
            binding_version=self.binding.binding_version,
        )
        self.store.records[(self.binding, ProviderActivationSurface.APP)] = [
            ProviderActivationEvidence(
                schema_version=evidence.schema_version,
                binding=drifted,
                surface=ProviderActivationSurface.RUNTIME_ADAPTER,
                stage=evidence.stage,
                revision=evidence.revision,
                evidence_id=evidence.evidence_id,
                evidence_digest=evidence.evidence_digest,
                observed_at=evidence.observed_at,
                expires_at=evidence.expires_at,
            )
        ]
        readiness = self.registry.readiness(now=self.now).app
        self.assertFalse(readiness.dry_run_ready)
        self.assertIn("binding drift", readiness.reason or "")

    def test_store_revision_conflict_and_future_or_non_utc_evidence_fail_closed(self):
        self.store.force_conflict = True
        with self.assertRaisesRegex(ProviderActivationError, "revision conflict"):
            self.record(
                ProviderActivationSurface.APP, ProviderActivationStage.INSTALLED
            )
        self.store.force_conflict = False
        future = self.now + timedelta(seconds=1)
        with self.assertRaisesRegex(ProviderActivationError, "future-dated"):
            self.registry.record(
                surface=ProviderActivationSurface.APP,
                stage=ProviderActivationStage.INSTALLED,
                evidence_id="future-evidence",
                evidence_digest="b" * 64,
                observed_at=future,
                expires_at=future + timedelta(minutes=1),
                now=self.now,
            )
        with self.assertRaisesRegex(ValueError, "timezone-aware UTC"):
            self.registry.readiness(now=self.now.replace(tzinfo=None))

    def test_invalid_identifiers_and_digest_cannot_carry_unbounded_or_secret_data(self):
        with self.assertRaisesRegex(ValueError, "non-secret identifier"):
            ProviderActivationBinding(
                provider_id="openai token value",
                workspace_id="director-default",
                provider_version="v1",
                app_version="v1",
                runtime_adapter_version="v1",
                workspace_version="v1",
                binding_version="v1",
            )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.registry.record(
                surface=ProviderActivationSurface.APP,
                stage=ProviderActivationStage.INSTALLED,
                evidence_id="evidence-1",
                evidence_digest="not-a-digest",
                observed_at=self.now,
                expires_at=self.now + timedelta(minutes=1),
                now=self.now,
            )
        with self.assertRaisesRegex(ValueError, "supported activation stage"):
            self.registry.record(
                surface=ProviderActivationSurface.APP,
                stage="INSTALLED",  # type: ignore[arg-type]
                evidence_id="evidence-1",
                evidence_digest="c" * 64,
                observed_at=self.now,
                expires_at=self.now + timedelta(minutes=1),
                now=self.now,
            )

    def test_malformed_store_record_is_reported_not_treated_as_ready(self):
        self.store.records[(self.binding, ProviderActivationSurface.APP)] = [
            "untrusted-store-row"  # type: ignore[list-item]
        ]
        readiness = self.registry.readiness(now=self.now).app
        self.assertFalse(readiness.dry_run_ready)
        self.assertIn("invalid evidence", readiness.reason or "")


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:46 UTC | END]]
