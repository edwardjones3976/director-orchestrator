# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | BEGIN]]
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from director_orchestrator.primary_activation_evidence import (
    ActivationEvidenceError,
    ActivationEvidenceStore,
    ActivationIdentity,
    ActivationStage,
)


def identity(**changes: str) -> ActivationIdentity:
    values = {
        "candidate_commit": "a" * 40,
        "candidate_tree": "b" * 40,
        "profile_id": "long-table",
        "compose_project": "long-table-director",
        "database_identity": "postgres:sha256:" + "c" * 64,
        "postgres_schema": "director_primary_v1",
        "checkpoint_namespace": "long-table-primary-v1",
    }
    values.update(changes)
    return ActivationIdentity(**values)


class PrimaryActivationEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ActivationEvidenceStore(Path(self.temporary.name) / "activation.json")
        self.identity = identity()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record(self, stage: ActivationStage, facts: dict | None = None) -> None:
        self.store.record(self.identity, stage, evidence_digest="d" * 64, facts=facts or {})

    def test_requires_exact_order_and_same_identity(self) -> None:
        with self.assertRaisesRegex(ActivationEvidenceError, "start at PREFLIGHT"):
            self.record(ActivationStage.MIGRATED)
        self.record(ActivationStage.PREFLIGHT)
        with self.assertRaisesRegex(ActivationEvidenceError, "out of order"):
            self.record(ActivationStage.BACKED_UP, {"backup_digest": "x"})
        with self.assertRaisesRegex(ActivationEvidenceError, "another candidate"):
            self.store.record(
                identity(candidate_commit="e" * 40),
                ActivationStage.MIGRATED,
                evidence_digest="d" * 64,
                facts={},
            )

    def test_activation_requires_migration_backup_and_nonzero_restore_proof(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        self.record(ActivationStage.MIGRATED)
        self.record(ActivationStage.BACKED_UP, {"backup_digest": "sha256:backup"})
        with self.assertRaisesRegex(ActivationEvidenceError, "nonzero schema"):
            self.record(
                ActivationStage.RESTORE_PROVED,
                {"required_control_plane_tables": 1, "checkpoint_tables": 0, "schema_evidence_rows": 1},
            )
        with self.assertRaisesRegex(ActivationEvidenceError, "prerequisites"):
            self.store.require_ready(self.identity)
        self.record(
            ActivationStage.RESTORE_PROVED,
            {"required_control_plane_tables": 20, "checkpoint_tables": 4, "schema_evidence_rows": 1},
        )
        self.store.require_ready(self.identity)
        self.record(ActivationStage.ACTIVATED)

    def test_backup_requires_artifact_digest_and_corruption_fails_closed(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        self.record(ActivationStage.MIGRATED)
        with self.assertRaisesRegex(ActivationEvidenceError, "backup evidence"):
            self.record(ActivationStage.BACKED_UP)
        self.store.path.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(ActivationEvidenceError, "unreadable"):
            self.store.load()


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | END]]
