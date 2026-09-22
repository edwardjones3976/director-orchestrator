# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:10 UTC | BEGIN]]
"""Connected regression for cross-repository Edge activation configuration."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.primary_activation_evidence import ActivationEvidenceError
from director_orchestrator.primary_generation import (
    deployment_digest,
    require_generation,
    require_validation_generation,
    resolved_deployment_digest,
)
from director_orchestrator.provider_validation import (
    ValidationError,
    capture_verifier_from_environment,
    resolved_provider_validation_generation,
)
from tests.test_local_activation_evidence import LocalActivationEvidenceTests, identity


class CrossRepositoryGenerationTests(LocalActivationEvidenceTests):
    def _edge_project(self, root: Path) -> tuple[str, str, str]:
        project_root = root / "edge_engine"
        project_root.mkdir()
        subprocess.run(("git", "init", "-b", "main"), cwd=project_root, check=True, capture_output=True)
        subprocess.run(("git", "config", "user.email", "edge@example.test"), cwd=project_root, check=True)
        subprocess.run(("git", "config", "user.name", "Edge"), cwd=project_root, check=True)
        (project_root / "README.md").write_text("edge\n", encoding="utf-8")
        subprocess.run(("git", "add", "README.md"), cwd=project_root, check=True)
        subprocess.run(("git", "commit", "-m", "edge"), cwd=project_root, check=True)
        subprocess.run(
            ("git", "remote", "add", "origin", "https://github.com/edwardjones3976/edge_engine.git"),
            cwd=project_root,
            check=True,
        )
        commit = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=project_root, text=True).strip()
        tree = subprocess.check_output(("git", "rev-parse", "HEAD^{tree}"), cwd=project_root, text=True).strip()
        return str(project_root), commit, tree

    def _generation_fixture(self, root: Path, settings: Settings, ident):
        journal = root / "generation.sqlite3"
        manifest = dict(
            version="director-primary-generation/owner-local-evidence-v1",
            activation=asdict(ident),
            journal_path=str(journal),
            owner_adoption_path=settings.langgraph_primary_local_evidence_adoption_path,
            owner_adoption_sha256=settings.langgraph_primary_local_evidence_adoption_sha256,
            provider_bindings_sha256=hashlib.sha256(b"{}").hexdigest(),
            deployment_configuration_sha256=deployment_digest(settings),
            image_id="sha256:" + "8" * 64,
            resolved_compose_sha256="9" * 64,
        )
        source = root / "generation.json"
        source.write_text(json.dumps(manifest))
        env = {
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH": str(source),
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON": "{}",
            "DIRECTOR_RUNTIME_IMAGE_ID": manifest["image_id"],
            "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256": manifest["resolved_compose_sha256"],
        }
        return journal, manifest, source, env

    def test_director_adoption_with_edge_execution_repository(self) -> None:
        root = Path(self.temporary.name)
        project_root, project_commit, project_tree = self._edge_project(root)
        ident = identity(
            candidate_commit=self.adoption_document["reviewed_source_commit"],
            candidate_tree=self.adoption_document["reviewed_source_tree"],
        )
        adoption_path, adoption_pin = self.write_adoption(
            document={
                **self.adoption_document,
                "repository": "edwardjones3976/director-orchestrator",
                "pull_request_number": 251,
            }
        )
        settings = replace(
            Settings(),
            langgraph_primary_acceptance_mode="owner-local-evidence-v1",
            langgraph_primary_owner_token=self.owner_token,
            langgraph_primary_local_evidence_adoption_path=str(adoption_path),
            langgraph_primary_local_evidence_adoption_sha256=adoption_pin,
            langgraph_primary_acceptance_journal_path=str(root / "journal.sqlite3"),
            repository_snapshot_root=project_root,
            repository_allowlist=("edwardjones3976/edge_engine",),
            langgraph_primary_service_repository="edwardjones3976/director-orchestrator",
            allowed_starting_refs=("main",),
            langgraph_primary_pinned_commit_sha=ident.candidate_commit,
            langgraph_primary_pinned_tree_sha=ident.candidate_tree,
            langgraph_primary_project_execution_pinned_commit_sha=project_commit,
            langgraph_primary_project_execution_pinned_tree_sha=project_tree,
        )
        journal, manifest, source, env = self._generation_fixture(root, settings, ident)
        with patch("director_orchestrator.primary_generation.GENERATION_DATA_ROOT", root), patch.dict(
            os.environ, env
        ):
            require_generation(settings, ident, reserve=True)
            require_generation(settings, ident)
            with self.assertRaises(ActivationEvidenceError):
                require_generation(
                    replace(settings, langgraph_primary_service_repository="other/service"),
                    ident,
                )
            with self.assertRaises(ActivationEvidenceError):
                require_generation(replace(settings, repository_allowlist=()), ident, reserve=True)
            with self.assertRaises(ActivationEvidenceError):
                require_generation(
                    replace(settings, repository_allowlist=("owner/a", "owner/b")),
                    ident,
                    reserve=True,
                )
            with self.assertRaises(ActivationEvidenceError):
                require_generation(
                    replace(settings, langgraph_primary_service_repository="not-a-repo"),
                    ident,
                    reserve=True,
                )
            with self.assertRaises(ActivationEvidenceError):
                require_generation(
                    replace(settings, langgraph_primary_project_execution_pinned_commit_sha="f" * 40),
                    ident,
                )
            tampered = root / "tampered.sqlite3"
            tampered_settings = replace(settings, langgraph_primary_acceptance_journal_path=str(tampered))
            manifest["journal_path"] = str(tampered)
            manifest["deployment_configuration_sha256"] = deployment_digest(tampered_settings)
            source.write_text(json.dumps(manifest))
            os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = hashlib.sha256(
                source.read_bytes()
            ).hexdigest()
            with self.assertRaises(ActivationEvidenceError):
                require_generation(tampered_settings, ident, reserve=True)
            self.assertFalse(tampered.exists())

    def test_same_repository_fallback_without_service_repository(self) -> None:
        root = Path(self.temporary.name)
        adoption_path, adoption_pin = self.write_adoption()
        ident = identity()
        settings = replace(
            Settings(),
            langgraph_primary_acceptance_mode="owner-local-evidence-v1",
            langgraph_primary_owner_token=self.owner_token,
            langgraph_primary_local_evidence_adoption_path=str(adoption_path),
            langgraph_primary_local_evidence_adoption_sha256=adoption_pin,
            langgraph_primary_acceptance_journal_path=str(root / "fallback.sqlite3"),
            repository_allowlist=("owner/repository",),
        )
        _, _, _, env = self._generation_fixture(root, settings, ident)
        with patch("director_orchestrator.primary_generation.GENERATION_DATA_ROOT", root), patch.dict(
            os.environ, env
        ):
            require_generation(settings, ident, reserve=True)
            with self.assertRaises(ActivationEvidenceError):
                require_generation(
                    replace(settings, repository_allowlist=("other/repository",)),
                    ident,
                )


class ValidationGenerationDigestTests(unittest.TestCase):
    def test_compose_digest_and_validation_generation_stay_consistent(self) -> None:
        image = "sha256:" + "8" * 64
        generation = "a" * 64
        document = {
            "name": "project",
            "services": {
                "orchestrator": {
                    "image": image,
                    "command": ["uvicorn", "director_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"],
                    "environment": {
                        "DIRECTOR_RUNTIME_IMAGE_ID": image,
                        "DIRECTOR_RUNTIME_IMAGE": image,
                        "DIRECTOR_ASGI_APP": "director_orchestrator.main:app",
                        "ALLOW_PAID_RUNS": "false",
                        "LANGGRAPH_PRIMARY_ENABLED": "false",
                        "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": "false",
                        "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED": "false",
                        "LANGGRAPH_PARALLEL_KILL_SWITCH": "true",
                        "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": generation,
                        "DIRECTOR_PROVIDER_VALIDATION_MANIFEST": "/data/provider-validation-manifest.json",
                    },
                    "volumes": [],
                    "networks": {"default": None},
                }
            },
            "networks": {"default": {"name": "project_default"}},
        }
        digest = resolved_deployment_digest(document, image)
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary) / "validation.json"
            ident = identity()
            manifest = dict(
                generation=generation,
                candidate=ident.candidate_commit,
                tree=ident.candidate_tree,
                expires_at=9_999_999_999,
                budget_units=1,
                lanes=[],
            )
            validation.write_text(json.dumps(manifest))
            env = {
                "DIRECTOR_PROVIDER_VALIDATION_MANIFEST": str(validation),
                "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": generation,
                "DIRECTOR_PROVIDER_VALIDATION_CANDIDATE": ident.candidate_commit,
                "DIRECTOR_PROVIDER_VALIDATION_TREE": ident.candidate_tree,
                "DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN": "postgresql://fixture.invalid/validation",
                "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY": "fixture-capture-key-" * 3,
            }
            with patch.dict(os.environ, env, clear=False):
                require_validation_generation(ident)
                self.assertEqual(generation, resolved_provider_validation_generation())
                verifier = capture_verifier_from_environment(
                    candidate=ident.candidate_commit,
                    tree=ident.candidate_tree,
                    require_enabled=False,
                )
                self.assertEqual(generation, verifier.generation)
            manifest_env = copy.deepcopy(document)
            manifest_env["services"]["orchestrator"]["environment"]["DIRECTOR_RESOLVED_DEPLOYMENT_SHA256"] = digest
            self.assertEqual(digest, resolved_deployment_digest(manifest_env, image))
            with patch.dict(os.environ, {**env, "DIRECTOR_PROVIDER_VALIDATION_GENERATION": "f" * 64}, clear=False):
                with self.assertRaises(ValidationError):
                    resolved_provider_validation_generation()


if __name__ == "__main__":
    unittest.main()
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:10 UTC | END]]
