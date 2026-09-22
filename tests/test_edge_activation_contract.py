# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:23 UTC | BEGIN]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:10 UTC | BEGIN]]
"""Connected activation regression; PostgreSQL uses only the sanctioned test DSN."""
from __future__ import annotations

from contextlib import nullcontext
import copy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.host_provider_bridge import WindowsHostProviderExecutor
from director_orchestrator.primary_activation_evidence import (
    ActivationEvidenceError, ActivationEvidenceStore, ActivationStage, canonical_facts_digest,
)
from director_orchestrator.primary_default import resolve_default_primary_authority
from director_orchestrator.primary_generation import (
    deployment_digest, require_generation, require_validation_generation, resolved_deployment_digest,
)
from director_orchestrator.primary_provider_evidence import publish_file
from director_orchestrator.provider_activation import ProviderActivationStage, ProviderActivationSurface
from director_orchestrator.provider_validation import (
    PostgresValidationStore, SCHEMA, ValidationBridgeService, ValidationError, ValidationRequest,
    capture_verifier_from_environment, production_configuration_digest,
)
import tests.test_local_activation_evidence as adoption_fixtures
import tests.test_primary_provider_activation as activation_fixtures
import tests.test_primary_provider_evidence as publication_fixtures
import tests.test_provider_validation as validation_fixtures


class EdgeActivationContractTests(unittest.TestCase):
    def setUp(self):
        # Keep OS execution support, not an ambient Director route or credential.
        self.dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "")
        clean = {key: value for key, value in os.environ.items()
                 if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}}
        self.enterContext(patch.dict(os.environ, clean, clear=True))
        self.fixture = adoption_fixtures.LocalActivationEvidenceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.addCleanup(self.fixture.doCleanups)
        self.root = Path(self.fixture.temporary.name)
        self.identity = adoption_fixtures.identity(
            candidate_commit=self.fixture.adoption_document["reviewed_source_commit"],
            candidate_tree=self.fixture.adoption_document["reviewed_source_tree"],
            profile_id="edge-engine", compose_project="edge-engine-director",
            checkpoint_namespace="edge-engine-primary-v1",
        )
        self.adoption_path, self.adoption_pin = self.fixture.write_adoption(document={
            **self.fixture.adoption_document,
            "repository": "edwardjones3976/director-orchestrator", "pull_request_number": 251,
        })
        self.project = self.root / "edge_engine"
        self.project.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "fixture@example.test")
        self.git("config", "user.name", "Synthetic Edge fixture")
        (self.project / "README.md").write_text("Synthetic Edge project.\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "Synthetic project fixture")
        self.git("remote", "add", "origin", "https://github.com/edwardjones3976/edge_engine.git")
        self.project_commit, self.project_tree = self.git("rev-parse", "HEAD"), self.git("rev-parse", "HEAD^{tree}")
        self.binding = validation_fixtures.binding()
        binding = asdict(self.binding)
        binding.pop("provider_id")
        self.bindings = json.dumps({"claude": binding}, sort_keys=True)
        self.journal = self.root / "selected.sqlite3"
        self.settings = replace(
            Settings(), langgraph_primary_acceptance_mode="owner-local-evidence-v1",
            langgraph_primary_owner_token=self.fixture.owner_token,
            langgraph_primary_local_evidence_adoption_path=str(self.adoption_path),
            langgraph_primary_local_evidence_adoption_sha256=self.adoption_pin,
            langgraph_primary_acceptance_journal_path=str(self.journal),
            repository_snapshot_root=str(self.project), repository_allowlist=("edwardjones3976/edge_engine",),
            langgraph_primary_service_repository="edwardjones3976/director-orchestrator",
            allowed_starting_refs=("main",), langgraph_primary_pinned_commit_sha=self.identity.candidate_commit,
            langgraph_primary_pinned_tree_sha=self.identity.candidate_tree,
            langgraph_primary_project_execution_pinned_commit_sha=self.project_commit,
            langgraph_primary_project_execution_pinned_tree_sha=self.project_tree,
            langgraph_primary_provider_activation_bindings_json=self.bindings,
            langgraph_primary_required_providers=("claude",), langgraph_primary_postgres_dsn=self.dsn,
            slack_owner_user_id="UOWNER", slack_gateway_secret="gateway-fixture-" + "s" * 32,
            shared_secret="shared-fixture-" + "h" * 32, allow_paid_runs=False,
            langgraph_primary_enabled=False, langgraph_primary_new_intake_enabled=False,
            langgraph_primary_live_provider_dispatch_enabled=False,
            langgraph_primary_offline_simulation_enabled=False, langgraph_parallel_kill_switch=True,
        )
        self.image = "sha256:" + "8" * 64
        self.validation_path = self.root / "validation.json"
        self.manifest_path = self.root / "generation.json"
        self.compose = {
            "name": "edge-engine-director",
            "services": {"orchestrator": {
                "image": self.image,
                "command": ["uvicorn", "director_orchestrator.primary_staging:app", "--host", "0.0.0.0", "--port", "8080"],
                "environment": {
                    "DIRECTOR_RUNTIME_IMAGE_ID": self.image, "DIRECTOR_RUNTIME_IMAGE": self.image,
                    "DIRECTOR_ASGI_APP": "director_orchestrator.primary_staging:app",
                    "ALLOW_PAID_RUNS": "false", "LANGGRAPH_PRIMARY_ENABLED": "false",
                    "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": "false",
                    "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED": "false",
                    "LANGGRAPH_PARALLEL_KILL_SWITCH": "true",
                    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH": str(self.manifest_path),
                    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": "0" * 64,
                    "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256": "0" * 64,
                    "DIRECTOR_PROVIDER_VALIDATION_MANIFEST": str(self.validation_path),
                    "LANGGRAPH_PRIMARY_SERVICE_REPOSITORY": self.settings.langgraph_primary_service_repository,
                    "REPOSITORY_ALLOWLIST": self.settings.repository_allowlist[0],
                    "LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON": self.bindings,
                },
                "volumes": [{"type": "bind", "source": str(self.root), "target": str(self.root), "read_only": True}],
                "networks": {"default": None},
            }},
            "networks": {"default": {"name": "edge-engine-director_default"}},
        }
        self.enterContext(patch("director_orchestrator.primary_generation.GENERATION_DATA_ROOT", self.root))
        self.select_configuration()

    def git(self, *args):
        return subprocess.check_output(("git", *args), cwd=self.project, text=True, stderr=subprocess.PIPE).strip()

    def select_configuration(self):
        compose_digest = resolved_deployment_digest(self.compose, self.image)
        self.manifest = dict(
            version="director-primary-generation/owner-local-evidence-v1", activation=asdict(self.identity),
            journal_path=str(self.journal), owner_adoption_path=str(self.adoption_path),
            owner_adoption_sha256=self.adoption_pin,
            provider_bindings_sha256=hashlib.sha256(self.bindings.encode()).hexdigest(),
            deployment_configuration_sha256=deployment_digest(self.settings),
            image_id=self.image, resolved_compose_sha256=compose_digest,
        )
        self.manifest_path.write_text(json.dumps(self.manifest, sort_keys=True), encoding="utf-8")
        self.generation = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()
        environment = self.compose["services"]["orchestrator"]["environment"]
        environment.update(LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256=self.generation,
                           DIRECTOR_RESOLVED_DEPLOYMENT_SHA256=compose_digest)
        self.assertEqual(compose_digest, resolved_deployment_digest(self.compose, self.image))
        os.environ.update(environment)
        os.environ.update(
            LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH=str(self.journal),
            LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_PATH=str(self.adoption_path),
            LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_SHA256=self.adoption_pin,
        )

    def test_cross_repository_selection_replay_and_drift_refusal(self):
        for changes in (
            {"langgraph_primary_service_repository": "other/service"},
            {"langgraph_primary_service_repository": "invalid"},
            {"repository_allowlist": ()}, {"repository_allowlist": ("owner/a", "owner/b")},
            {"langgraph_primary_project_execution_pinned_tree_sha": ""},
            {"langgraph_primary_project_execution_pinned_commit_sha": "", "langgraph_primary_project_execution_pinned_tree_sha": ""},
            {"langgraph_primary_project_execution_pinned_commit_sha": "f" * 40},
            {"langgraph_primary_owner_token": "wrong-owner-" + "x" * 32},
            {"langgraph_primary_provider_activation_bindings_json": "{}"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ActivationEvidenceError):
                require_generation(replace(self.settings, **changes), self.identity, reserve=True)
            self.assertFalse(self.journal.exists())
        for changes in ({"candidate_commit": "c" * 40}, {"candidate_tree": "d" * 40}):
            with self.subTest(changes=changes), self.assertRaises(ActivationEvidenceError):
                require_generation(self.settings, replace(self.identity, **changes), reserve=True)
            self.assertFalse(self.journal.exists())
        adoption = self.adoption_path.read_bytes()
        envelope = json.loads(adoption)
        envelope["signature"] = "0" * 64
        self.adoption_path.write_text(json.dumps(envelope))
        with self.assertRaises(ActivationEvidenceError):
            require_generation(self.settings, self.identity, reserve=True)
        self.assertFalse(self.journal.exists())
        self.adoption_path.write_bytes(adoption)
        require_generation(self.settings, self.identity, reserve=True)
        journal = self.journal.read_bytes()
        self.assertEqual(self.manifest, require_generation(self.settings, self.identity))
        self.assertEqual(journal, self.journal.read_bytes())
        self.assertEqual(self.project_commit, resolve_default_primary_authority(self.settings).starting_sha)
        self.assertNotEqual(self.project_commit, self.identity.candidate_commit)
        with self.assertRaises(RuntimeError):
            resolve_default_primary_authority(replace(self.settings, langgraph_primary_project_execution_pinned_tree_sha="e" * 40))
        changed = copy.deepcopy(self.compose)
        changed_env = changed["services"]["orchestrator"]["environment"]
        changed_env["DIRECTOR_PROVIDER_VALIDATION_GENERATION"] = self.generation
        self.assertEqual(self.manifest["resolved_compose_sha256"], resolved_deployment_digest(changed, self.image))
        changed_env["DIRECTOR_PROVIDER_VALIDATION_GENERATION"] = "e" * 64
        with self.assertRaises(ActivationEvidenceError):
            resolved_deployment_digest(changed, self.image)
        changed = copy.deepcopy(self.compose)
        changed["services"]["orchestrator"]["environment"]["REPOSITORY_ALLOWLIST"] = "other/project"
        self.assertNotEqual(self.manifest["resolved_compose_sha256"], resolved_deployment_digest(changed, self.image))

    def test_same_repository_fallback_is_retained(self):
        self.settings = replace(self.settings, langgraph_primary_service_repository="",
                                repository_allowlist=("edwardjones3976/director-orchestrator",),
                                langgraph_primary_project_execution_pinned_commit_sha="",
                                langgraph_primary_project_execution_pinned_tree_sha="")
        self.compose["services"]["orchestrator"]["environment"].update(
            LANGGRAPH_PRIMARY_SERVICE_REPOSITORY="", REPOSITORY_ALLOWLIST=self.settings.repository_allowlist[0])
        self.select_configuration()
        require_generation(self.settings, self.identity, reserve=True)
        self.assertEqual(self.manifest, require_generation(self.settings, self.identity))

    def stage(self, *, restore=True):
        store = ActivationEvidenceStore(self.root / "ignored.json", acceptance_secrets=self.fixture.secrets)
        def record(stage, facts):
            store.record(self.identity, stage, evidence_digest=canonical_facts_digest(facts), facts=facts)
        record(ActivationStage.PREFLIGHT, {"compose_config_valid": True})
        artifact = self.root / "acceptance.json"
        adoption = self.fixture.loaded_adoption(self.adoption_path, self.adoption_pin)
        artifact.write_text(json.dumps(self.fixture.local_artifact(self.identity, adoption)), encoding="utf-8")
        store.record_acceptance_artifact(self.identity, artifact)
        record(ActivationStage.MIGRATED, {"schema_current": True})
        self.backup_digest = "sha256:" + hashlib.sha256(b"synthetic backup fixture").hexdigest()
        record(ActivationStage.BACKED_UP, {"backup_digest": self.backup_digest})
        if restore:
            self.restore(store)
        return store

    def restore(self, store):
        facts = {"backup_digest": self.backup_digest, "required_control_plane_tables": 17,
                 "checkpoint_tables": 4, "schema_evidence_rows": 1}
        store.record(self.identity, ActivationStage.RESTORE_PROVED,
                     evidence_digest=canonical_facts_digest(facts), facts=facts)
        store.require_staged(self.identity)

    def validation(self):
        if not self.dsn:
            self.fail("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required; use the sanctioned PostgreSQL lane")
        store = PostgresValidationStore(self.dsn)
        store.bootstrap()
        self.account = self.root / "synthetic-account.txt"
        self.account.write_text("synthetic account bytes", encoding="utf-8")
        self.marker = self.root / "native-invocations.txt"
        native = self.root / "native-fixture.py"
        native.write_text("import sys\nfrom pathlib import Path\nsys.stdin.buffer.read()\n"
                          f"with Path({str(self.marker)!r}).open('a') as stream: stream.write('called\\n')\n"
                          f"print({validation_fixtures.raw_native().decode()!r})\n", encoding="utf-8")
        self.executor = WindowsHostProviderExecutor({"claude": (str(Path(sys.executable).resolve()), str(native))},
                                                   cwd=str(self.project))
        config = production_configuration_digest(self.executor, "claude", account_files=(str(self.account),))
        work = validation_fixtures.work_packet()
        work.update(project="Edge Engine", starting_ref=self.identity.candidate_commit,
                    repository=self.settings.repository_allowlist[0])
        manifest = dict(generation=self.generation, candidate=self.identity.candidate_commit,
                        tree=self.identity.candidate_tree, expires_at=int(time.time()) + 600, budget_units=1,
                        lanes={"claude": {"binding": asdict(self.binding), "configuration_digest": config,
                                          "cost_units": 1, "work_packet": work}})
        self.validation_path.write_text(json.dumps(manifest), encoding="utf-8")
        require_validation_generation(self.identity)
        store.create_generation(**manifest, owner_token=validation_fixtures.OWNER)
        os.environ.update(DIRECTOR_PROVIDER_VALIDATION_ENABLED="true",
                          DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN=self.dsn,
                          DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY=validation_fixtures.KEY,
                          DIRECTOR_PROVIDER_VALIDATION_CANDIDATE=self.identity.candidate_commit,
                          DIRECTOR_PROVIDER_VALIDATION_TREE=self.identity.candidate_tree)
        service = ValidationBridgeService(store=store, production_executor=self.executor,
            capture_key=validation_fixtures.KEY, owner_token=validation_fixtures.OWNER,
            account_files={"claude": (str(self.account),)})
        return store, service

    def test_staged_generation_to_native_capture_and_signed_publication(self):
        require_generation(self.settings, self.identity, reserve=True)
        staged = self.stage(restore=False)
        store, service = self.validation()
        request = ValidationRequest(generation=self.generation, provider="claude")
        with self.assertRaises(ValidationError):
            service.run(request, "wrong-owner-" + "x" * 32)
        self.assertFalse(self.marker.exists())
        capture = service.run(request, validation_fixtures.OWNER)
        verifier = capture_verifier_from_environment(candidate=self.identity.candidate_commit,
                                                      tree=self.identity.candidate_tree)
        self.assertEqual(self.generation, verifier.generation)
        self.assertTrue(verifier.verify(self.binding, capture["evidence_digest"]))
        self.assertFalse(verifier.verify(replace(self.binding, binding_version="changed"), capture["evidence_digest"]))
        publication = publication_fixtures.PublicationTests()
        publication.setUp()
        publication.identity, publication.settings = self.identity, self.settings
        publication.generation, publication.binding = self.generation, self.binding
        document = publication.make_document(capture["evidence_digest"], fresh=True)
        observations = self.root / "observations.json"
        activation_store = activation_fixtures.MemoryStore()
        def publish(document=document, *, identity=None):
            observations.write_text(json.dumps(document), encoding="utf-8")
            return publish_file(observations, settings=self.settings, identity=identity or self.identity)
        with patch("director_orchestrator.langgraph_postgres.open_provider_activation_store",
                   side_effect=lambda *args, **kwargs: nullcontext(activation_store)) as opened:
            with self.assertRaisesRegex(ActivationEvidenceError, "incomplete"):
                publish()
            opened.assert_not_called()
            self.restore(staged)
            for mutate in (
                lambda d: d.update(generation="f" * 64),
                lambda d: d["requests"][0]["request"].update(binding_version="changed"),
                lambda d: d["requests"][0]["request"].update(actor_id="OTHER"),
                lambda d: d["requests"][0]["request"]["gateway_proof"].update(signature="0" * 64),
            ):
                changed = copy.deepcopy(document)
                mutate(changed)
                with self.assertRaises(ActivationEvidenceError):
                    publish(changed)
                self.assertEqual({}, activation_store.rows)
            for field, value in (
                ("DIRECTOR_PROVIDER_VALIDATION_GENERATION", "f" * 64),
                ("DIRECTOR_PROVIDER_VALIDATION_TREE", "d" * 40),
                ("LANGGRAPH_PRIMARY_OWNER_TOKEN", "wrong-owner-" + "x" * 32),
                ("DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY", "wrong-capture-" + "x" * 32),
            ):
                with self.subTest(field=field), patch.dict(os.environ, {field: value}):
                    with self.assertRaises((ActivationEvidenceError, ValidationError)):
                        publish()
                self.assertEqual({}, activation_store.rows)
            manifest_bytes = self.validation_path.read_bytes()
            for field in ("generation", "candidate", "tree"):
                changed = json.loads(manifest_bytes)
                changed[field] = "f" * (64 if field == "generation" else 40)
                self.validation_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.subTest(validation_field=field), self.assertRaises(ActivationEvidenceError):
                    publish()
                self.assertEqual({}, activation_store.rows)
            self.validation_path.write_bytes(manifest_bytes)
            for changes in ({"candidate_commit": "c" * 40}, {"candidate_tree": "d" * 40}):
                with self.assertRaises(ActivationEvidenceError):
                    publish(identity=replace(self.identity, **changes))
            result = publish()
            self.assertTrue(result["selected_provider_live_ready"])
            self.assertEqual(result, publish())
        for surface in ProviderActivationSurface:
            self.assertEqual(list(ProviderActivationStage), [row.stage for row in activation_store.load(self.binding, surface)])
        self.assertEqual(10, len(activation_store.claims))
        self.assertEqual(["called"], self.marker.read_text().splitlines())
        with store.connect() as db:
            states = db.execute(f"SELECT c.state FROM {SCHEMA}.checkpoints c JOIN {SCHEMA}.attempts a "
                                "USING(reservation) WHERE a.generation=%s ORDER BY c.sequence", (self.generation,)).fetchall()
        self.assertEqual([("admitted",), ("running",), ("succeeded",)], states)

    def test_account_drift_stops_native_execution_before_capture(self):
        require_generation(self.settings, self.identity, reserve=True)
        _, service = self.validation()
        self.account.write_text("changed synthetic account", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "binding drift"):
            service.run(ValidationRequest(generation=self.generation, provider="claude"), validation_fixtures.OWNER)
        self.assertFalse(self.marker.exists())

    def test_command_drift_stops_native_execution_before_capture(self):
        require_generation(self.settings, self.identity, reserve=True)
        _, service = self.validation()
        self.executor.commands["claude"] += ("changed-argument",)
        with self.assertRaisesRegex(ValidationError, "binding drift"):
            service.run(ValidationRequest(generation=self.generation, provider="claude"), validation_fixtures.OWNER)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:10 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:23 UTC | END]]
