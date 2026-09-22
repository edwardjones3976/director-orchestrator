# [[Codex Director | GPT-6 Astra | 2026-09-22 | 08:26 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 08:15 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 07:03 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:40 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-22 | 06:10 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:52 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:35 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from unittest.mock import patch
import os
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.primary_activation_evidence import (
    ACCEPTANCE_ARTIFACT_OWNER_LOCAL_VERSION,
    ActivationEvidenceError,
    ActivationEvidenceStore,
    ActivationIdentity,
    ActivationStage,
    HOSTED_CI_NOT_EXECUTED,
    OWNER_LOCAL_ADOPTION_DECISION,
    OWNER_LOCAL_ADOPTION_VERSION,
    OWNER_LOCAL_CI_ISSUER,
    _owner_local_adoption_signature,
    canonical_facts_digest,
    load_pinned_owner_local_adoption,
    owner_local_acceptance_facts,
)


def identity(**changes: str) -> ActivationIdentity:
    values = {
        "candidate_commit": "f2a637c3ee04df386771e341fc079ff7148ae99b",
        "candidate_tree": "af345d97ba83eb5094bd0b2a367282a2dbee263f",
        "profile_id": "long-table",
        "compose_project": "long-table-director",
        "database_identity": "postgres:sha256:" + "c" * 64,
        "database_instance_id": "11111111-1111-4111-8111-111111111111",
        "postgres_schema": "director_primary_v1",
        "checkpoint_namespace": "long-table-primary-v1",
    }
    values.update(changes)
    return ActivationIdentity(**values)


class LocalActivationEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.owner_token = "owner-token-that-is-long-enough-00001"
        self.secrets = {"owner_adoption": self.owner_token}
        pins = []
        digests = {}
        for role in ("local_qa", "security", "independent_review"):
            raw = ("independently retained " + role + " receipt").encode()
            name = role + ".txt"
            (Path(self.temporary.name) / name).write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            pins.append({"path": name, "sha256": digest})
            digests[role + "_evidence_digest"] = digest
        self.adoption_document = {
            "version": OWNER_LOCAL_ADOPTION_VERSION,
            "decision": OWNER_LOCAL_ADOPTION_DECISION,
            "hosted_ci_failure_cause": "payments/spending unavailable",
            "reviewed_source_commit": "3130a50298bd1723134a7e27afd3965a2ccdce56",
            "reviewed_source_tree": "af345d97ba83eb5094bd0b2a367282a2dbee263f",
            "repository": "owner/repository",
            "pull_request_number": 249,
            "pinned_evidence": pins,
            **digests,
            "security_reviewer": "security-bot",
            "independent_reviewer": "grok-reviewer",
            "pull_request_author": "implementation-producer",
        }

        path, pin = self.write_adoption()
        self.environment = patch.dict(os.environ, {
            "LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": "owner-local-evidence-v1",
            "LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH": str(Path(self.temporary.name) / "selected.sqlite3"),
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": "7" * 64,
            "LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_PATH": str(path),
            "LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_SHA256": pin,
            "LANGGRAPH_PRIMARY_OWNER_TOKEN": self.owner_token,
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_adoption(self, *, document: dict | None = None, owner_token: str | None = None) -> tuple[Path, str]:
        payload = dict(document or self.adoption_document)
        signature = _owner_local_adoption_signature(owner_token or self.owner_token, payload)
        envelope = {"document": payload, "signature": signature}
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path = Path(self.temporary.name) / "adoption.json"
        path.write_bytes(raw)
        return path, hashlib.sha256(raw).hexdigest()

    def loaded_adoption(self, path: Path, pin: str) -> dict:
        return load_pinned_owner_local_adoption(
            path,
            expected_sha256=pin,
            owner_token=self.owner_token,
        )

    def local_artifact(self, ident: ActivationIdentity, adoption: dict) -> dict:
        facts = owner_local_acceptance_facts(
            ident,
            adoption,
            repository=adoption["repository"],
            pull_request_number=adoption["pull_request_number"],
        )
        unsigned = {
            "facts": facts,
            "facts_digest": canonical_facts_digest(facts),
            "pull_request_number": adoption["pull_request_number"],
            "repository": adoption["repository"],
            "version": ACCEPTANCE_ARTIFACT_OWNER_LOCAL_VERSION,
        }
        payload = json.dumps(
            unsigned,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        from director_orchestrator.primary_activation_evidence import _acceptance_signature

        attestations = {
            "owner_adoption": {
                "issuer": OWNER_LOCAL_CI_ISSUER,
                "signature": _acceptance_signature(
                    "owner_adoption", OWNER_LOCAL_CI_ISSUER, self.owner_token, payload
                ),
            },
        }
        return {**unsigned, "attestations": attestations}

    def test_owner_local_acceptance_records_and_replays(self) -> None:
        ident = identity()
        path, pin = self.write_adoption()
        adoption = self.loaded_adoption(path, pin)
        artifact = self.local_artifact(ident, adoption)
        artifact_path = Path(self.temporary.name) / "artifact.json"
        artifact_path.write_text(json.dumps(artifact, sort_keys=True), encoding="utf-8")
        store = ActivationEvidenceStore(
            Path(self.temporary.name) / "activation.json",
            acceptance_secrets=self.secrets,
        )
        store.record(
            ident,
            ActivationStage.PREFLIGHT,
            evidence_digest=canonical_facts_digest({"compose_config_valid": True}),
            facts={"compose_config_valid": True},
        )
        store.record_acceptance_artifact(ident, artifact_path)
        replay = ActivationEvidenceStore(
            Path(self.temporary.name) / "activation.json",
            acceptance_secrets=self.secrets,
            read_only=True,
        )
        replay.require_accepted(ident)
        self.assertEqual(replay.path, Path(self.temporary.name) / "selected.evidence.json")
        (Path(self.temporary.name) / "security.txt").write_bytes(b"changed after store construction")
        with self.assertRaises(ActivationEvidenceError):
            replay.require_accepted(ident)

    def test_missing_adoption_pin_fails_closed(self) -> None:
        with patch.dict(os.environ, {"LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_SHA256": ""}):
            with self.assertRaises(ActivationEvidenceError):
                ActivationEvidenceStore(Path(self.temporary.name) / "activation.json",
                    acceptance_secrets=self.secrets)

    def test_github_v1_still_rejects_failed_ci(self) -> None:
        ident = identity(candidate_commit="a" * 40, candidate_tree="b" * 40)
        facts = {
            "candidate_commit": ident.candidate_commit,
            "candidate_tree": ident.candidate_tree,
            "remote_head_commit": ident.candidate_commit,
            "security_reviewed_commit": ident.candidate_commit,
            "independent_reviewed_commit": ident.candidate_commit,
            "ci_conclusion": HOSTED_CI_NOT_EXECUTED,
            "ci_required_check_count": 6,
            "ci_run_ids": [],
            "ci_evidence_digest": "1" * 64,
            "security_verdict": "ACCEPT",
            "security_reviewer": "security-bot",
            "security_evidence_digest": "2" * 64,
            "independent_review_verdict": "ACCEPT",
            "independent_reviewer": "grok-reviewer",
            "independent_review_evidence_digest": "3" * 64,
            "pull_request_author": "implementation-producer",
        }
        with self.assertRaises(ActivationEvidenceError):
            ActivationEvidenceStore._validate_acceptance(ident, facts)

    def test_adoption_pin_mismatch_rejected(self) -> None:
        path, pin = self.write_adoption()
        with self.assertRaises(ActivationEvidenceError):
            load_pinned_owner_local_adoption(
                path,
                expected_sha256="f" * 64,
                owner_token=self.owner_token,
            )

    def test_config_requires_owner_local_pins(self) -> None:
        settings = replace(
            Settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_postgres_dsn="postgresql://director@example.test/director",
            langgraph_primary_postgres_schema="director_primary_v1",
            langgraph_primary_checkpoint_namespace="director-primary-v1",
            langgraph_primary_owner_token="x" * 32,
            repository_snapshot_root="/tmp/snapshots",
            repository_allowlist=("owner/repo",),
            allowed_starting_refs=("main",),
            langgraph_primary_pinned_commit_sha="a" * 40,
            langgraph_primary_pinned_tree_sha="b" * 40,
            langgraph_primary_required_providers=("openai", "claude", "cursor", "grok"),
            langgraph_primary_acceptance_mode="owner-local-evidence-v1",
            langgraph_primary_local_evidence_adoption_path="",
            langgraph_primary_local_evidence_adoption_sha256="",
        )
        with self.assertRaisesRegex(ValueError, "LOCAL_EVIDENCE_ADOPTION"):
            settings.validate_langgraph_primary_configuration()

    def test_receipt_bytes_and_required_digest_pins_are_verified(self) -> None:
        path, pin = self.write_adoption()
        receipt = Path(self.temporary.name) / "security.txt"
        original = receipt.read_bytes()
        receipt.write_bytes(b"tampered")
        with self.assertRaises(ActivationEvidenceError):
            self.loaded_adoption(path, pin)
        receipt.write_bytes(original)
        for change in (
            {"local_qa_evidence_digest": "e" * 64},
            {"pinned_evidence": [{"path": "../security.txt", "sha256": hashlib.sha256(original).hexdigest()}]},
            {"independent_reviewer": "implementation-producer"},
        ):
            with self.subTest(change=change):
                path, pin = self.write_adoption(document={**self.adoption_document, **change})
                with self.assertRaises(ActivationEvidenceError):
                    self.loaded_adoption(path, pin)

    def test_changed_source_tree_cannot_adopt_prior_review(self) -> None:
        path, pin = self.write_adoption()
        adoption = self.loaded_adoption(path, pin)
        ident = identity(candidate_commit="a" * 40, candidate_tree="b" * 40)
        store = ActivationEvidenceStore(Path(self.temporary.name) / "changed.json",
            acceptance_secrets=self.secrets)
        with self.assertRaises(ActivationEvidenceError):
            store._validate_owner_local_acceptance(ident, owner_local_acceptance_facts(
                ident, adoption, repository=adoption["repository"],
                pull_request_number=adoption["pull_request_number"]))

    def test_local_generation_retains_bindings_and_exclusive_journal(self) -> None:
        from director_orchestrator.primary_generation import deployment_digest, require_generation
        root = Path(self.temporary.name)
        adoption_path, adoption_pin = self.write_adoption()
        journal = root / "selected.sqlite3"
        ident = identity()
        settings = replace(Settings(), langgraph_primary_acceptance_mode="owner-local-evidence-v1",
            langgraph_primary_owner_token=self.owner_token,
            langgraph_primary_local_evidence_adoption_path=str(adoption_path),
            langgraph_primary_local_evidence_adoption_sha256=adoption_pin,
            langgraph_primary_acceptance_journal_path=str(journal),
            repository_allowlist=("owner/repository",))
        manifest = dict(version="director-primary-generation/owner-local-evidence-v1",
            activation=asdict(ident), journal_path=str(journal),
            owner_adoption_path=str(adoption_path), owner_adoption_sha256=adoption_pin,
            provider_bindings_sha256=hashlib.sha256(b"{}").hexdigest(),
            deployment_configuration_sha256=deployment_digest(settings),
            image_id="sha256:" + "8" * 64, resolved_compose_sha256="9" * 64)
        source = root / "generation.json"
        source.write_text(json.dumps(manifest))
        env = {"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH": str(source),
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON": "{}",
            "DIRECTOR_RUNTIME_IMAGE_ID": manifest["image_id"],
            "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256": manifest["resolved_compose_sha256"]}
        with patch("director_orchestrator.primary_generation.GENERATION_DATA_ROOT", root), patch.dict(os.environ, env):
            for field in ("DIRECTOR_RUNTIME_IMAGE_ID", "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256",
                          "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON",
                          "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256",
                          "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH"):
                with self.subTest(field=field), patch.dict(os.environ, {field: "missing-or-mixed"}):
                    with self.assertRaises(ActivationEvidenceError):
                        require_generation(settings, ident, reserve=True)
                    self.assertFalse(journal.exists())
            for changed in (replace(settings, provider_timeout_seconds=settings.provider_timeout_seconds + 1),
                            replace(settings, langgraph_primary_acceptance_mode="github-v1"),
                            replace(settings, langgraph_primary_acceptance_journal_path=str(root / "other.sqlite3"))):
                with self.assertRaises(ActivationEvidenceError):
                    require_generation(changed, ident, reserve=True)
                self.assertFalse(journal.exists())
            with self.assertRaises(ActivationEvidenceError):
                require_generation(settings, identity(database_instance_id="22222222-2222-4222-8222-222222222222"), reserve=True)
            require_generation(settings, ident, reserve=True)
            before = journal.read_bytes()
            self.assertEqual(require_generation(settings, ident), manifest)
            with self.assertRaises(ActivationEvidenceError):
                require_generation(settings, ident, reserve=True)
            self.assertEqual(journal.read_bytes(), before)

            artifact = root / "acceptance.json"
            artifact.write_text(json.dumps(self.local_artifact(ident, self.loaded_adoption(adoption_path, adoption_pin))))
            store = ActivationEvidenceStore(root / "ignored.json", acceptance_secrets=self.secrets)
            def record(stage, facts):
                store.record(ident, stage, evidence_digest=canonical_facts_digest(facts), facts=facts)
            record(ActivationStage.PREFLIGHT, {"compose_config_valid": True})
            store.record_acceptance_artifact(ident, artifact)
            record(ActivationStage.MIGRATED, {"schema_current": True})
            backup = "sha256:" + "0" * 64
            record(ActivationStage.BACKED_UP, {"backup_digest": backup})
            record(ActivationStage.RESTORE_PROVED, {"backup_digest": backup,
                "required_control_plane_tables": 17, "checkpoint_tables": 4, "schema_evidence_rows": 1})
            store.require_staged(ident)
            retained = store.path.read_bytes()

            # Reproduce a new image generation with the old sidecar retained.
            journal.unlink()
            manifest["image_id"] = "sha256:" + "a" * 64
            source.write_text(json.dumps(manifest))
            new_pin = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch.dict(os.environ, {"DIRECTOR_RUNTIME_IMAGE_ID": manifest["image_id"],
                    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": new_pin}):
                require_generation(settings, ident, reserve=True)
                self.assertEqual(require_generation(settings, ident), manifest)
                for replay in (store, ActivationEvidenceStore(root / "ignored.json",
                        acceptance_secrets=self.secrets, read_only=True)):
                    with self.assertRaisesRegex(ActivationEvidenceError, "generation"):
                        replay.require_staged(ident)
                for invalid in ("", "not-a-digest"):
                    with patch.dict(os.environ, {"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": invalid}):
                        with self.assertRaisesRegex(ActivationEvidenceError, "generation"):
                            store.load()
            self.assertEqual(store.path.read_bytes(), retained)

    def test_require_generation_rejects_repository_allowlist_mismatch(self) -> None:
        from director_orchestrator.primary_generation import deployment_digest, require_generation

        root = Path(self.temporary.name)
        adoption_path, adoption_pin = self.write_adoption()
        journal = root / "selected.sqlite3"
        ident = identity()
        base = dict(
            langgraph_primary_acceptance_mode="owner-local-evidence-v1",
            langgraph_primary_owner_token=self.owner_token,
            langgraph_primary_local_evidence_adoption_path=str(adoption_path),
            langgraph_primary_local_evidence_adoption_sha256=adoption_pin,
            langgraph_primary_acceptance_journal_path=str(journal),
        )
        manifest = dict(
            version="director-primary-generation/owner-local-evidence-v1",
            activation=asdict(ident),
            journal_path=str(journal),
            owner_adoption_path=str(adoption_path),
            owner_adoption_sha256=adoption_pin,
            provider_bindings_sha256=hashlib.sha256(b"{}").hexdigest(),
            deployment_configuration_sha256=deployment_digest(
                replace(Settings(), **base, repository_allowlist=("owner/repository",))
            ),
            image_id="sha256:" + "8" * 64,
            resolved_compose_sha256="9" * 64,
        )
        source = root / "generation.json"
        source.write_text(json.dumps(manifest))
        env = {
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH": str(source),
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": hashlib.sha256(
                source.read_bytes()
            ).hexdigest(),
            "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON": "{}",
            "DIRECTOR_RUNTIME_IMAGE_ID": manifest["image_id"],
            "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256": manifest["resolved_compose_sha256"],
        }
        with patch("director_orchestrator.primary_generation.GENERATION_DATA_ROOT", root), patch.dict(
            os.environ, env
        ):
            for settings, label in (
                (replace(Settings(), **base, repository_allowlist=()), "absent"),
                (
                    replace(Settings(), **base, repository_allowlist=("owner/a", "owner/b")),
                    "multiple",
                ),
                (
                    replace(Settings(), **base, repository_allowlist=("other/repository",)),
                    "mismatched",
                ),
            ):
                with self.subTest(label=label):
                    case_journal = root / f"{label}.sqlite3"
                    settings = replace(settings, langgraph_primary_acceptance_journal_path=str(case_journal))
                    manifest["journal_path"] = str(case_journal)
                    manifest["deployment_configuration_sha256"] = deployment_digest(settings)
                    source.write_text(json.dumps(manifest))
                    os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                    with self.assertRaises(ActivationEvidenceError):
                        require_generation(settings, ident, reserve=True)
                    self.assertFalse(case_journal.exists())

    def test_hosted_ci_failure_cause_rejects_surrounding_whitespace(self) -> None:
        document = {
            **self.adoption_document,
            "hosted_ci_failure_cause": "  payments/spending unavailable  ",
        }
        path, pin = self.write_adoption(document=document)
        with self.assertRaisesRegex(ActivationEvidenceError, "noncanonical"):
            self.loaded_adoption(path, pin)

    def test_pinned_evidence_rejects_duplicate_paths_before_hashing(self) -> None:
        pins = list(self.adoption_document["pinned_evidence"])
        duplicate = dict(pins[0])
        duplicate["path"] = pins[0]["path"].swapcase()
        document = {**self.adoption_document, "pinned_evidence": pins + [duplicate]}
        path, pin = self.write_adoption(document=document)
        with self.assertRaisesRegex(ActivationEvidenceError, "duplicate paths"):
            self.loaded_adoption(path, pin)

    def test_pinned_evidence_rejects_excessive_pin_count_before_reading(self) -> None:
        pins = [{"path": f"receipt{i}.txt", "sha256": "a" * 64} for i in range(33)]
        document = {**self.adoption_document, "pinned_evidence": pins}
        path, pin = self.write_adoption(document=document)
        with self.assertRaisesRegex(ActivationEvidenceError, "allowed count"):
            self.loaded_adoption(path, pin)

    def test_adoption_and_receipt_reads_require_regular_files(self) -> None:
        adoption_dir = Path(self.temporary.name) / "adoption-directory"
        adoption_dir.mkdir()
        with self.assertRaisesRegex(ActivationEvidenceError, "regular file"):
            load_pinned_owner_local_adoption(
                adoption_dir,
                expected_sha256="a" * 64,
                owner_token=self.owner_token,
            )
        path, pin = self.write_adoption()
        receipt_dir = Path(self.temporary.name) / "security.txt"
        if receipt_dir.exists():
            receipt_dir.unlink()
        receipt_dir.mkdir()
        with self.assertRaisesRegex(ActivationEvidenceError, "unavailable"):
            self.loaded_adoption(path, pin)

    def test_owner_local_mode_rejects_valid_legacy_github_artifact(self) -> None:
        import tests.test_primary_activation_evidence as github_tests
        with patch.dict(os.environ, {
            "LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": "github-v1",
            "LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH": "",
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": "",
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH": "",
        }):
            fixture = github_tests.PrimaryActivationEvidenceTests()
            fixture.setUp()
            self.addCleanup(fixture.tearDown)
            fixture.identity = identity()
            artifact_path = fixture.acceptance_artifact()
            fixture.record(ActivationStage.PREFLIGHT)
            fixture.store.record_acceptance_artifact(fixture.identity, artifact_path)
            self.assertEqual(len(fixture.store.load()["stages"]), 2)
            legacy_history = fixture.store.path.read_bytes()
        store = ActivationEvidenceStore(
            Path(self.temporary.name) / "activation.json",
            acceptance_secrets={**self.secrets, **fixture.secrets},
        )
        facts = {"compose_config_valid": True}
        store.record(fixture.identity, ActivationStage.PREFLIGHT,
            evidence_digest=canonical_facts_digest(facts), facts=facts)
        with self.assertRaisesRegex(ActivationEvidenceError, "selected mode"):
            store.record_acceptance_artifact(fixture.identity, artifact_path)
        store.path.write_bytes(legacy_history)
        with self.assertRaisesRegex(ActivationEvidenceError, "selected mode"):
            store.load()
        self.assertEqual(store.path.read_bytes(), legacy_history)

    def test_aggregate_limit_precedes_any_receipt_read(self) -> None:
        import director_orchestrator.primary_activation_evidence as evidence_module
        pins = list(self.adoption_document["pinned_evidence"])
        for index in range(9):
            name = f"large-{index}.bin"
            with (Path(self.temporary.name) / name).open("wb") as stream:
                stream.truncate(8 * 1024 * 1024)
            pins.append({"path": name, "sha256": "a" * 64})
        path, pin = self.write_adoption(document={**self.adoption_document, "pinned_evidence": pins})
        with patch.object(evidence_module, "_read_regular_file_bounded",
                wraps=evidence_module._read_regular_file_bounded) as reader:
            with self.assertRaisesRegex(ActivationEvidenceError, "aggregate"):
                self.loaded_adoption(path, pin)
        self.assertEqual(reader.call_count, 1)  # Only the bounded adoption document.

    def test_receipt_growth_cannot_exceed_preflight_aggregate(self) -> None:
        import director_orchestrator.primary_activation_evidence as evidence_module
        path, pin = self.write_adoption()
        read_bounded = evidence_module._read_regular_file_bounded
        receipt = Path(self.temporary.name) / "local_qa.txt"
        def grow_before_read(source, **kwargs):
            if source == receipt:
                source.write_bytes(source.read_bytes() + b"growth")
            return read_bounded(source, **kwargs)
        with patch.object(evidence_module, "_read_regular_file_bounded", side_effect=grow_before_read):
            with self.assertRaisesRegex(ActivationEvidenceError, "oversized"):
                self.loaded_adoption(path, pin)

    def test_oversized_adoption_is_rejected_before_reading(self) -> None:
        path = Path(self.temporary.name) / "adoption.json"
        with path.open("wb") as stream:
            stream.truncate(256 * 1024 + 1)
        with patch("os.fdopen") as opened:
            # fstat must reject size before a stream read; use the actual open fd.
            import contextlib
            real_fdopen = __import__("io").open
            streams = []
            @contextlib.contextmanager
            def capture(fd, mode):
                with real_fdopen(fd, mode) as stream:
                    proxy = unittest.mock.Mock(wraps=stream)
                    streams.append(proxy)
                    yield proxy
            opened.side_effect = capture
            with self.assertRaisesRegex(ActivationEvidenceError, "oversized"):
                load_pinned_owner_local_adoption(path, expected_sha256="a" * 64, owner_token=self.owner_token)
            self.assertTrue(streams)
            streams[0].read.assert_not_called()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO behavior is exercised in the Linux lane")
    def test_fifo_adoption_rejects_without_blocking(self) -> None:
        import subprocess
        import sys
        fifo = Path(self.temporary.name) / "fifo.json"
        os.mkfifo(fifo)
        code = """import sys
from director_orchestrator.primary_activation_evidence import ActivationEvidenceError, load_pinned_owner_local_adoption
try:
    load_pinned_owner_local_adoption(sys.argv[1], expected_sha256='a'*64, owner_token='fixture-owner-token-00000000000000000')
except ActivationEvidenceError:
    sys.exit(0)
sys.exit(1)
"""
        result = subprocess.run([sys.executable, "-c", code, str(fifo)],
            capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_runtime_rejects_missing_acceptance_before_database_open(self) -> None:
        from director_orchestrator.primary_default import DefaultPrimaryActivation
        settings = replace(Settings(), langgraph_primary_enabled=True, allow_paid_runs=False,
            langgraph_primary_acceptance_mode="owner-local-evidence-v1")
        with patch("director_orchestrator.primary_default.resolve_default_primary_authority"), \
             patch("director_orchestrator.primary_default.build_typed_external_effect_grant"), \
             patch("director_orchestrator.primary_default.open_postgres_control_plane") as open_database:
            with self.assertRaises(ActivationEvidenceError):
                DefaultPrimaryActivation(settings)
            open_database.assert_not_called()

    def test_qualification_callers_verify_the_actual_staged_history(self) -> None:
        from director_orchestrator import primary_generation, primary_provider_evidence
        ident = identity()
        path, pin = self.write_adoption()
        artifact_path = Path(self.temporary.name) / "acceptance.json"
        artifact_path.write_text(json.dumps(self.local_artifact(ident, self.loaded_adoption(path, pin))), encoding="utf-8")
        store = ActivationEvidenceStore(Path(self.temporary.name) / "ignored.json", acceptance_secrets=self.secrets)
        def record(stage, facts):
            store.record(ident, stage, evidence_digest=canonical_facts_digest(facts), facts=facts)
        record(ActivationStage.PREFLIGHT, {"compose_config_valid": True})
        store.record_acceptance_artifact(ident, artifact_path)
        record(ActivationStage.MIGRATED, {"schema_current": True})
        backup = "sha256:" + "0" * 64
        record(ActivationStage.BACKED_UP, {"backup_digest": backup})
        record(ActivationStage.RESTORE_PROVED, {"backup_digest": backup,
            "required_control_plane_tables": 17, "checkpoint_tables": 4, "schema_evidence_rows": 1})
        settings = replace(Settings(), app_env=ident.profile_id,
            langgraph_primary_pinned_commit_sha=ident.candidate_commit,
            langgraph_primary_pinned_tree_sha=ident.candidate_tree,
            langgraph_primary_postgres_schema=ident.postgres_schema,
            langgraph_primary_checkpoint_namespace=ident.checkpoint_namespace,
            allow_paid_runs=False, langgraph_primary_enabled=False,
            langgraph_primary_new_intake_enabled=False,
            langgraph_primary_live_provider_dispatch_enabled=False,
            langgraph_primary_offline_simulation_enabled=False,
            langgraph_parallel_kill_switch=True)
        with patch.dict(os.environ, {"DIRECTOR_COMPOSE_PROJECT_NAME": ident.compose_project,
                "DIRECTOR_ASGI_APP": "director_orchestrator.primary_staging:app"}), \
             patch("director_orchestrator.config.Settings", return_value=settings), \
             patch("director_orchestrator.langgraph_postgres.database_binding",
                return_value=(ident.database_identity, ident.database_instance_id)), \
             patch.object(primary_generation, "require_generation", return_value={}), \
             patch.object(primary_generation, "require_validation_generation",
                side_effect=RuntimeError("next validation gate")) as next_gate, \
             patch("director_orchestrator.langgraph_postgres.open_provider_activation_store") as open_store, \
             patch("sys.argv", ["primary_generation", "validate-providers"]):
            for caller in (primary_generation.main,
                    lambda: primary_provider_evidence.publish_file("unused.json", settings=settings, identity=ident)):
                with self.subTest(caller=caller):
                    next_gate.reset_mock()
                    with self.assertRaisesRegex(RuntimeError, "next validation gate"):
                        caller()
                    next_gate.assert_called_once_with(ident)
                    next_gate.reset_mock()
                    with patch.dict(os.environ, {"LANGGRAPH_PRIMARY_OWNER_TOKEN": "wrong-owner-token-" * 3}):
                        with self.assertRaises(ActivationEvidenceError):
                            caller()
                    next_gate.assert_not_called()
            open_store.assert_not_called()

    def test_later_stage_sequencing_preserved(self) -> None:
        ident = identity()
        path, pin = self.write_adoption()
        adoption = self.loaded_adoption(path, pin)
        artifact = self.local_artifact(ident, adoption)
        artifact_path = Path(self.temporary.name) / "artifact.json"
        artifact_path.write_text(json.dumps(artifact, sort_keys=True), encoding="utf-8")
        store = ActivationEvidenceStore(
            Path(self.temporary.name) / "activation.json",
            acceptance_secrets=self.secrets,
        )
        store.record(
            ident,
            ActivationStage.PREFLIGHT,
            evidence_digest=canonical_facts_digest({"compose_config_valid": True}),
            facts={"compose_config_valid": True},
        )
        store.record_acceptance_artifact(ident, artifact_path)
        store.record(
            ident,
            ActivationStage.MIGRATED,
            evidence_digest=canonical_facts_digest({"schema_current": True}),
            facts={"schema_current": True},
        )
        with self.assertRaises(ActivationEvidenceError):
            store.require_staged(ident)


if __name__ == "__main__":
    unittest.main()
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:35 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:52 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-22 | 06:10 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:17 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:40 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 07:03 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-22 | 08:15 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-22 | 08:26 UTC | END]]
