# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.primary_activation_evidence import ActivationEvidenceError
from director_orchestrator.primary_generation import deployment_digest, require_generation
from tests.test_primary_activation_evidence import identity


class PrimaryGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = self.root / "registry.json"
        self.registry.write_bytes(b"owner-registry-bytes")
        self.journal = self.root / "fresh.sqlite3"
        self.settings = replace(Settings(), langgraph_primary_acceptance_mode="provider-v2",
            langgraph_primary_acceptance_registry_path=str(self.registry),
            langgraph_primary_acceptance_registry_sha256=hashlib.sha256(self.registry.read_bytes()).hexdigest(),
            langgraph_primary_acceptance_journal_path=str(self.journal))
        self.identity = identity()
        self.manifest = dict(version="director-primary-generation/v1", activation=asdict(self.identity),
            journal_path=str(self.journal), registry_path=str(self.registry),
            registry_sha256=self.settings.langgraph_primary_acceptance_registry_sha256,
            provider_bindings_sha256=hashlib.sha256(b"{}").hexdigest(),
            deployment_configuration_sha256=deployment_digest(self.settings))
        self.source = self.root / "owner-manifest.json"
        storage_root = patch("director_orchestrator.primary_generation.GENERATION_DATA_ROOT", self.root)
        storage_root.start(); self.addCleanup(storage_root.stop)
        environment = patch.dict(os.environ, {"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH": str(self.source),
            "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON": "{}"})
        environment.start(); self.addCleanup(environment.stop)
        self.select_bytes()

    def select_bytes(self):
        self.source.write_text(json.dumps(self.manifest))
        os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_reserve_is_exclusive_and_persists_exact_manifest_without_overwriting_journal(self):
        require_generation(self.settings, self.identity, reserve=True)
        pin = os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"]
        self.assertEqual((self.root / ("generation-" + pin + ".json")).read_bytes(), self.source.read_bytes())
        original = self.journal.read_bytes()
        require_generation(self.settings, self.identity)
        with self.assertRaises(ActivationEvidenceError):
            require_generation(self.settings, self.identity, reserve=True)
        self.assertEqual(self.journal.read_bytes(), original)

    def test_missing_selection_and_mixed_inputs_fail_before_creation(self):
        variants = [replace(self.settings, provider_timeout_seconds=self.settings.provider_timeout_seconds + 1),
            replace(self.settings, langgraph_primary_acceptance_registry_sha256="f" * 64),
            replace(self.settings, langgraph_primary_acceptance_journal_path=str(self.root / "other.sqlite3")),
            replace(self.settings, langgraph_primary_acceptance_mode="github-v1")]
        for settings in variants:
            with self.assertRaises(ActivationEvidenceError):
                require_generation(settings, self.identity, reserve=True)
            self.assertFalse(self.journal.exists())
        with self.assertRaises(ActivationEvidenceError):
            require_generation(self.settings, identity(candidate_commit="f" * 40), reserve=True)
        with patch.dict(os.environ, {"LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON": '{"changed":true}'}):
            with self.assertRaises(ActivationEvidenceError):
                require_generation(self.settings, self.identity, reserve=True)
        self.assertFalse(self.journal.exists())

    def test_unknown_fields_duplicate_keys_and_registry_substitution_fail_closed(self):
        self.manifest["unrestricted_extension"] = True
        self.select_bytes()
        with self.assertRaises(ActivationEvidenceError):
            require_generation(self.settings, self.identity, reserve=True)
        del self.manifest["unrestricted_extension"]
        self.select_bytes()
        self.registry.write_bytes(b"substituted registry")
        with self.assertRaises(ActivationEvidenceError):
            require_generation(self.settings, self.identity, reserve=True)
        self.assertFalse(self.journal.exists())
        self.source.write_text('{"version":"a","version":"b"}')
        os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with self.assertRaises(ActivationEvidenceError):
            require_generation(self.settings, self.identity, reserve=True)

    def test_only_explicit_operational_switches_can_vary(self):
        require_generation(self.settings, self.identity, reserve=True)
        require_generation(replace(self.settings, allow_paid_runs=not self.settings.allow_paid_runs,
            langgraph_primary_enabled=not self.settings.langgraph_primary_enabled), self.identity)
        with self.assertRaises(ActivationEvidenceError):
            require_generation(replace(self.settings, langgraph_primary_owner_token="different-authority"), self.identity)

    def test_missing_journal_observation_does_not_create_it(self):
        with self.assertRaises(ActivationEvidenceError):
            require_generation(self.settings, self.identity)
        self.assertFalse(self.journal.exists())

    def test_substituted_journal_generation_marker_is_rejected_without_repair(self):
        require_generation(self.settings, self.identity, reserve=True)
        connection = sqlite3.connect(self.journal)
        try:
            with connection:
                connection.execute("UPDATE activation_generation SET manifest_sha256=?", ("f" * 64,))
        finally:
            connection.close()
        before = self.journal.read_bytes()
        with self.assertRaises(ActivationEvidenceError):
            require_generation(self.settings, self.identity)
        self.assertEqual(before, self.journal.read_bytes())
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
