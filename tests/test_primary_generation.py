# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 16:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:52 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.primary_activation_evidence import ActivationEvidenceError
from director_orchestrator.primary_generation import deployment_digest, require_generation, resolved_deployment_digest, require_validation_generation
from tests.test_primary_activation_evidence import identity


class PrimaryGenerationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI required for configuration-only rendering")
    def test_actual_compose_rendering_binds_content_and_normalizes_checked_modes(self):
        project = Path(__file__).resolve().parents[1]
        image = "sha256:" + "8" * 64
        environment_file = self.root / "render-only.env"
        environment_file.write_text("LANGGRAPH_PRIMARY_POSTGRES_PASSWORD=fixture-only\n")
        environment = dict(os.environ, DIRECTOR_ENV_FILE=str(environment_file),
            DIRECTOR_RUNTIME_IMAGE=image, DIRECTOR_RUNTIME_IMAGE_ID=image,
            DIRECTOR_COMPOSE_PROJECT_NAME="staging-render-test", DIRECTOR_ASGI_APP="director_orchestrator.main:app",
            ALLOW_PAID_RUNS="false", LANGGRAPH_PRIMARY_ENABLED="false",
            LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED="false", LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED="false",
            LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED="false", LANGGRAPH_PARALLEL_KILL_SWITCH="true")
        def render():
            result = subprocess.run(["docker", "compose", "--project-name", "staging-render-test", "--env-file", str(environment_file), "config", "--format", "json"],
                # Hang guard, not a latency budget: docker compose config took
                # over 30s on a loaded windows runner (run 34295743451).
                cwd=project, env=environment, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, "Compose configuration-only rendering failed")
            return resolved_deployment_digest(json.loads(result.stdout), image)
        original = render()
        environment["DIRECTOR_ASGI_APP"] = "director_orchestrator.primary_staging:app"
        self.assertEqual(render(), original)

    def test_resolved_compose_binds_image_mount_network_and_rejects_arbitrary_commands(self):
        import copy
        image = "sha256:" + "8" * 64
        document = {"name": "project", "services": {"orchestrator": {
            "image": image, "command": ["uvicorn", "director_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"],
            "environment": {"DIRECTOR_RUNTIME_IMAGE_ID": image, "DIRECTOR_RUNTIME_IMAGE": image,
                "DIRECTOR_ASGI_APP": "director_orchestrator.main:app",
                "ALLOW_PAID_RUNS": "false", "LANGGRAPH_PRIMARY_ENABLED": "false",
                "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": "false", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED": "false",
                "LANGGRAPH_PARALLEL_KILL_SWITCH": "true", "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256": "unverified"},
            "volumes": [{"type": "bind", "source": "/protected", "target": "/repository", "read_only": True}],
            "networks": {"default": None}}}, "networks": {"default": {"name": "project_default"}}}
        original = resolved_deployment_digest(document, image)
        for field, replacement in (("volumes", [{"type": "bind", "source": "/unapproved", "target": "/repository"}]), ("networks", {"host": None})):
            changed = copy.deepcopy(document)
            changed["services"]["orchestrator"][field] = replacement
            self.assertNotEqual(resolved_deployment_digest(changed, image), original)
        for field, replacement in (("image", "sha256:" + "7" * 64), ("command", ["python", "unapproved.py"]), ("entrypoint", ["sh", "-c"])):
            changed = copy.deepcopy(document)
            changed["services"]["orchestrator"][field] = replacement
            with self.assertRaises(ActivationEvidenceError):
                resolved_deployment_digest(changed, image)
        staged = copy.deepcopy(document)
        service = staged["services"]["orchestrator"]
        service["command"][1] = service["environment"]["DIRECTOR_ASGI_APP"] = "director_orchestrator.primary_staging:app"
        self.assertEqual(resolved_deployment_digest(staged, image), original)
        service["environment"]["ALLOW_PAID_RUNS"] = "true"
        with self.assertRaises(ActivationEvidenceError):
            resolved_deployment_digest(staged, image)

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
        self.manifest = dict(image_id="sha256:" + "8" * 64, resolved_compose_sha256="9" * 64, version="director-primary-generation/v1", activation=asdict(self.identity),
            journal_path=str(self.journal), registry_path=str(self.registry),
            registry_sha256=self.settings.langgraph_primary_acceptance_registry_sha256,
            provider_bindings_sha256=hashlib.sha256(b"{}").hexdigest(),
            deployment_configuration_sha256=deployment_digest(self.settings))
        self.source = self.root / "owner-manifest.json"
        storage_root = patch("director_orchestrator.primary_generation.GENERATION_DATA_ROOT", self.root)
        storage_root.start(); self.addCleanup(storage_root.stop)
        environment = patch.dict(os.environ, {"DIRECTOR_RUNTIME_IMAGE_ID": "sha256:" + "8" * 64, "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256": "9" * 64,
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH": str(self.source),
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

    def test_validation_manifest_cannot_select_another_candidate_or_generation(self):
        validation = self.root / "validation.json"
        document = dict(generation=os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"],
            candidate=self.identity.candidate_commit, tree=self.identity.candidate_tree)
        with patch.dict(os.environ, {"DIRECTOR_PROVIDER_VALIDATION_MANIFEST": str(validation)}):
            validation.write_text(json.dumps(document))
            require_validation_generation(self.identity)
            for field in ("candidate", "tree", "generation"):
                changed = dict(document, **{field: "f" * len(document[field])})
                validation.write_text(json.dumps(changed))
                with self.assertRaises(ActivationEvidenceError):
                    require_validation_generation(self.identity)

    def test_deployment_digest_binds_project_execution_pins(self) -> None:
        baseline = deployment_digest(self.settings)
        changed = deployment_digest(
            replace(
                self.settings,
                langgraph_primary_project_execution_pinned_commit_sha="a" * 40,
                langgraph_primary_project_execution_pinned_tree_sha="b" * 40,
            )
        )
        self.assertNotEqual(baseline, changed)

    def test_malformed_project_execution_pins_fail_before_reserve(self) -> None:
        with self.assertRaises(ActivationEvidenceError):
            require_generation(
                replace(
                    self.settings,
                    langgraph_primary_project_execution_pinned_commit_sha="a" * 40,
                    langgraph_primary_project_execution_pinned_tree_sha="",
                ),
                self.identity,
                reserve=True,
            )
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
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:52 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 16:58 UTC | END]]
