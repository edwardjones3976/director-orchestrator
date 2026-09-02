# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from director_orchestrator.primary_activation_evidence import (
    ActivationEvidenceError,
    ActivationEvidenceStore,
    ActivationIdentity,
    ActivationStage,
    canonical_facts_digest,
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
        self.identity = identity()
        self.secrets = {
            "ci": "ci-authority-secret-that-is-distinct-0001",
            "independent_review": "review-authority-secret-that-is-distinct-0002",
            "security": "security-authority-secret-that-is-distinct-0003",
        }
        self.store = ActivationEvidenceStore(
            Path(self.temporary.name) / "activation.json",
            acceptance_secrets=self.secrets,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record(self, stage: ActivationStage, facts: dict | None = None) -> None:
        observed = facts or {}
        self.store.record(
            self.identity,
            stage,
            evidence_digest=canonical_facts_digest(observed),
            facts=observed,
        )

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
            "pull_request_author": "implementation-producer",
        }
        facts.update(changes)
        return facts

    def accept(self) -> None:
        artifact = self.acceptance_artifact()
        self.store.record_acceptance_artifact(self.identity, artifact)

    def acceptance_artifact(self, **changes: object) -> Path:
        facts = self.acceptance_facts(**changes)
        ActivationEvidenceStore._validate_acceptance(self.identity, facts)
        unsigned = {
            "facts": facts,
            "facts_digest": canonical_facts_digest(facts),
            "pull_request_number": 77,
            "repository": "owner/repository",
            "version": "director-primary-acceptance-artifact/v1",
        }
        payload = json.dumps(
            unsigned,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        issuers = {
            "ci": "github-actions",
            "independent_review": facts["independent_reviewer"],
            "security": facts["security_reviewer"],
        }
        artifact = {
            **unsigned,
            "attestations": {
                kind: {
                    "issuer": issuer,
                    "signature": hmac.new(
                        self.secrets[kind].encode("utf-8"),
                        f"director-primary-acceptance/{kind}/{issuer}/v1\0".encode("ascii")
                        + payload,
                        hashlib.sha256,
                    ).hexdigest(),
                }
                for kind, issuer in issuers.items()
            },
        }
        path = Path(self.temporary.name) / f"acceptance-{len(list(Path(self.temporary.name).glob('acceptance-*')))}.json"
        path.write_text(
            json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return path

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
                evidence_digest=canonical_facts_digest({}),
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
            self.acceptance_artifact(remote_head_commit="f" * 40)
        with self.assertRaisesRegex(ActivationEvidenceError, "CI has not succeeded"):
            self.acceptance_artifact(ci_conclusion="FAILURE")
        with self.assertRaisesRegex(ActivationEvidenceError, "distinct attributable"):
            self.acceptance_artifact(independent_reviewer="security-bot")
        self.accept()
        self.assertEqual(
            ActivationStage.ACCEPTED.value,
            self.store.load()["stages"][-1]["stage"],
        )

    def test_acceptance_refuses_self_asserted_facts_and_caller_digest(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        with self.assertRaisesRegex(ActivationEvidenceError, "canonical acceptance artifact"):
            self.store.record(
                self.identity,
                ActivationStage.ACCEPTED,
                evidence_digest=canonical_facts_digest(self.acceptance_facts()),
                facts=self.acceptance_facts(),
            )

    def test_acceptance_artifact_mutation_substitution_and_digest_mismatch_fail(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        artifact_path = self.acceptance_artifact()
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["facts"]["ci_run_ids"] = [9999]
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        with self.assertRaisesRegex(ActivationEvidenceError, "facts digest"):
            self.store.record_acceptance_artifact(
                self.identity, artifact_path
            )

        artifact_path = self.acceptance_artifact()
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["facts_digest"] = "f" * 64
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        with self.assertRaisesRegex(ActivationEvidenceError, "facts digest"):
            self.store.record_acceptance_artifact(
                self.identity, artifact_path
            )

        artifact_path = self.acceptance_artifact()
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["attestations"]["security"]["signature"] = "0" * 64
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        with self.assertRaisesRegex(ActivationEvidenceError, "security attestation"):
            self.store.record_acceptance_artifact(
                self.identity, artifact_path
            )

    def test_recorded_stage_fact_mutation_is_detected_on_offline_load(self) -> None:
        self.record(ActivationStage.PREFLIGHT, {"snapshot_exact": True})
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        document["stages"][0]["facts"]["snapshot_exact"] = False
        self.store.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ActivationEvidenceError, "canonical facts"):
            self.store.load()

    def test_recorded_acceptance_artifact_substitution_is_detected_offline(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
        self.assertEqual("security-bot", self.store.load()["stages"][1]["facts"]["security_reviewer"])
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        document["stages"][1]["source_artifact"]["attestations"]["security"]["signature"] = "0" * 64
        self.store.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ActivationEvidenceError, "mutated or substituted"):
            self.store.load()

    def test_public_rewrite_with_consistent_hashes_and_stale_hmacs_fails_replay(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        accepted = document["stages"][1]
        accepted["facts"]["ci_run_ids"] = [9999]
        accepted["source_artifact"]["facts"]["ci_run_ids"] = [9999]
        rewritten_facts_digest = canonical_facts_digest(accepted["facts"])
        accepted["evidence_digest"] = rewritten_facts_digest
        accepted["source_artifact"]["facts_digest"] = rewritten_facts_digest
        accepted["source_artifact_digest"] = hashlib.sha256(
            json.dumps(
                accepted["source_artifact"],
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.store.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ActivationEvidenceError, "attestation is invalid"):
            self.store.require_ready(self.identity)

    def test_accepted_replay_requires_injected_verifier_secrets(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        self.accept()
        untrusted_reader = ActivationEvidenceStore(self.store.path)
        with self.assertRaisesRegex(ActivationEvidenceError, "verifier secrets"):
            untrusted_reader.require_ready(self.identity)

    def test_cli_rejects_arbitrary_acceptance_flags_and_accepts_exact_artifact_path(self) -> None:
        self.record(ActivationStage.PREFLIGHT)
        common = [
            sys.executable,
            "-m",
            "director_orchestrator.primary_activation_evidence",
            "record",
            "--artifact",
            str(self.store.path),
            "--candidate-commit",
            self.identity.candidate_commit,
            "--candidate-tree",
            self.identity.candidate_tree,
            "--profile-id",
            self.identity.profile_id,
            "--compose-project",
            self.identity.compose_project,
            "--database-identity",
            self.identity.database_identity,
            "--database-instance-id",
            self.identity.database_instance_id,
            "--postgres-schema",
            self.identity.postgres_schema,
            "--checkpoint-namespace",
            self.identity.checkpoint_namespace,
            "--stage",
            "ACCEPTED",
        ]
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET": self.secrets["ci"],
                "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET": self.secrets["independent_review"],
                "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET": self.secrets["security"],
            }
        )
        rejected = subprocess.run(
            common
            + [
                "--facts-json",
                json.dumps(self.acceptance_facts()),
                "--evidence-digest",
                canonical_facts_digest(self.acceptance_facts()),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, rejected.returncode, rejected.stderr)
        self.assertIn("refuses --facts-json and --evidence-digest", rejected.stderr)

        accepted = subprocess.run(
            common + ["--acceptance-artifact", str(self.acceptance_artifact())],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        self.assertIn("activation_stage_recorded=true", accepted.stdout)


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:48 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | END]]
