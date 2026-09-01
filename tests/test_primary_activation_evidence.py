# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | BEGIN]]
from __future__ import annotations

import hashlib
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
        "database_instance_id": "11111111-1111-4111-8111-111111111111",
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

    def acceptance_facts(self, **changes: object) -> dict:
        facts = {
            "candidate_commit": self.identity.candidate_commit,
            "candidate_tree": self.identity.candidate_tree,
            "remote_head_commit": self.identity.candidate_commit,
            "ci_conclusion": "SUCCESS",
            "ci_required_check_count": 6,
            "ci_run_ids": [1234],
            "ci_evidence_digest": "1" * 64,
            "security_verdict": "ACCEPT",
            "security_reviewer": "security-bot",
            "security_reviewed_commit": self.identity.candidate_commit,
            "security_evidence_digest": "2" * 64,
            "independent_review_verdict": "ACCEPT",
            "independent_reviewer": "cursor-reviewer",
            "independent_reviewed_commit": self.identity.candidate_commit,
            "independent_review_evidence_digest": "3" * 64,
        }
        facts.update(changes)
        return facts

    def accept(self) -> None:
        self.record(ActivationStage.ACCEPTED, self.acceptance_facts())

    def test_requires_exact_order_and_same_identity(self) -> None:
        with self.assertRaisesRegex(ActivationEvidenceError, "start at PREFLIGHT"):
            self.record(ActivationStage.MIGRATED)
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
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
        backup_digest = "sha256:" + "a" * 64
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
        self.record(ActivationStage.MIGRATED)
        self.record(ActivationStage.BACKED_UP, {"backup_digest": backup_digest})
        with self.assertRaisesRegex(ActivationEvidenceError, "nonzero schema"):
            self.record(
                ActivationStage.RESTORE_PROVED,
                {
                    "backup_digest": backup_digest,
                    "required_control_plane_tables": 1,
                    "checkpoint_tables": 0,
                    "schema_evidence_rows": 1,
                },
            )
        with self.assertRaisesRegex(ActivationEvidenceError, "prerequisites"):
            self.store.require_ready(self.identity)
        self.record(
            ActivationStage.RESTORE_PROVED,
            {
                "backup_digest": backup_digest,
                "required_control_plane_tables": 20,
                "checkpoint_tables": 4,
                "schema_evidence_rows": 1,
            },
        )
        self.store.bind_legacy_inventory(
            self.identity, inventory_digest="e" * 64, task_count=3
        )
        self.store.require_ready(self.identity)
        self.record(ActivationStage.ACTIVATED)

    def test_backup_requires_artifact_digest_and_corruption_fails_closed(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
        self.record(ActivationStage.MIGRATED)
        with self.assertRaisesRegex(ActivationEvidenceError, "backup evidence"):
            self.record(ActivationStage.BACKED_UP)
        self.store.path.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(ActivationEvidenceError, "unreadable"):
            self.store.load()

    def test_restore_dump_continuity_rejects_substitution_and_mutation_then_accepts_exact_replay(self) -> None:
        original = Path(self.temporary.name) / "original.dump"
        substitute = Path(self.temporary.name) / "substitute.dump"
        original.write_bytes(b"valid-custom-dump-a")
        substitute.write_bytes(b"valid-custom-dump-b")
        digest = "sha256:" + hashlib.sha256(
            original.read_bytes()
        ).hexdigest()
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
        self.record(ActivationStage.MIGRATED)
        self.record(ActivationStage.BACKED_UP, {"backup_digest": digest})

        with self.assertRaisesRegex(ActivationEvidenceError, "does not match"):
            self.store.require_backup_file(self.identity, substitute)

        original.write_bytes(original.read_bytes() + b"-mutated")
        with self.assertRaisesRegex(ActivationEvidenceError, "does not match"):
            self.store.require_backup_file(self.identity, original)

        original.write_bytes(b"valid-custom-dump-a")
        self.assertEqual(digest, self.store.require_backup_file(self.identity, original))
        self.record(
            ActivationStage.RESTORE_PROVED,
            {
                "backup_digest": digest,
                "required_control_plane_tables": 12,
                "checkpoint_tables": 4,
                "schema_evidence_rows": 1,
            },
        )
        self.store.bind_legacy_inventory(
            self.identity, inventory_digest="e" * 64, task_count=2
        )
        recorded = self.store.load()
        self.assertEqual(
            digest,
            recorded["stages"][-1]["facts"]["backup_digest"],
        )

    def test_legacy_inventory_is_required_and_immutable_before_activation(self) -> None:
        backup_digest = "sha256:" + "a" * 64
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
        self.record(ActivationStage.MIGRATED)
        self.record(ActivationStage.BACKED_UP, {"backup_digest": backup_digest})
        self.record(
            ActivationStage.RESTORE_PROVED,
            {
                "backup_digest": backup_digest,
                "required_control_plane_tables": 12,
                "checkpoint_tables": 4,
                "schema_evidence_rows": 1,
            },
        )
        with self.assertRaisesRegex(ActivationEvidenceError, "inventory"):
            self.store.require_ready(self.identity)
        self.store.bind_legacy_inventory(
            self.identity, inventory_digest="f" * 64, task_count=4
        )
        self.store.require_ready(self.identity)
        with self.assertRaisesRegex(ActivationEvidenceError, "immutable"):
            self.store.bind_legacy_inventory(
                self.identity, inventory_digest="e" * 64, task_count=5
            )

    def test_restore_dump_continuity_rejects_missing_file(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
        self.record(ActivationStage.MIGRATED)
        self.record(
            ActivationStage.BACKED_UP,
            {"backup_digest": "sha256:" + "a" * 64},
        )
        with self.assertRaisesRegex(ActivationEvidenceError, "missing or unreadable"):
            self.store.require_backup_file(
                self.identity, Path(self.temporary.name) / "missing.dump"
            )

    def test_acceptance_requires_exact_head_ci_and_distinct_reviews(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        with self.assertRaisesRegex(ActivationEvidenceError, "exact candidate"):
            self.record(
                ActivationStage.ACCEPTED,
                self.acceptance_facts(remote_head_commit="f" * 40),
            )
        with self.assertRaisesRegex(ActivationEvidenceError, "CI has not succeeded"):
            self.record(
                ActivationStage.ACCEPTED,
                self.acceptance_facts(ci_conclusion="FAILURE"),
            )
        with self.assertRaisesRegex(ActivationEvidenceError, "distinct attributable"):
            self.record(
                ActivationStage.ACCEPTED,
                self.acceptance_facts(independent_reviewer="security-bot"),
            )
        self.accept()
        self.assertEqual(
            ActivationStage.ACCEPTED.value,
            self.store.load()["stages"][-1]["stage"],
        )


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | END]]
