# [[Claude Code | Claude Opus 5 | 2026-09-09 | 03:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:33 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from fastapi.testclient import TestClient
from director_orchestrator.primary_activation_evidence import ActivationEvidenceStore, ActivationEvidenceError, ActivationStage
from director_orchestrator.primary_staging import create_app, verify_staged_identity


class StagingBoundaryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell required")
    def test_actual_observation_and_bootstrap_blocks_never_start_or_build_services(self):
        script = Path(__file__).resolve().parents[1] / "scripts/windows-langgraph-primary-control.ps1"
        for action in ("Health", "Preflight", "BootstrapDatabaseIdentity"):
            program = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parser failure'}
$Action=$args[1]
$block=$ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if ($Action -eq "' + $Action + '")')},$true) | Select-Object -First 1
function Invoke-Compose { throw 'Forbidden service/build helper reached' }
function Invoke-Evidence { 'evidence-record-only' }
function Wait-Health { @{langgraph_primary=@{staged_ready=$true}} }
function Get-DatabaseBinding { param([switch]$Bootstrap) if(-not $Bootstrap){throw 'not explicit bootstrap'}; @('identity','instance') }
& ([scriptblock]::Create($block.Extent.Text))
'''
            with tempfile.TemporaryDirectory() as directory:
                harness = Path(directory) / "probe.ps1"
                harness.write_text(program)
                result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(harness), str(script), action], capture_output=True, text=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell required")
    def test_actual_stop_proof_rejects_a_running_mutating_surface(self):
        script = Path(__file__).resolve().parents[1] / "scripts/windows-langgraph-primary-control.ps1"
        program = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors)
$node=$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-AllIntakeStopped'},$true) | Select-Object -First 1
. ([scriptblock]::Create($node.Extent.Text))
function Invoke-Compose { 'still-running' }
try { Assert-AllIntakeStopped } catch { 'rejected'; exit 0 }
throw 'running intake incorrectly accepted'
'''
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "probe.ps1"
            harness.write_text(program)
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(harness), str(script)], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rejected", result.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell required")
    def test_atomic_selection_overrides_inherited_compose_entrypoint(self):
        script = Path(__file__).resolve().parents[1] / "scripts/windows-langgraph-primary-control.ps1"
        program = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors)
foreach($name in @('Set-DotEnvValue','Write-Switches')) {
  $node=$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true) | Select-Object -First 1
  . ([scriptblock]::Create($node.Extent.Text))
}
$script:environmentPath=$args[1]; $script:values=@{}
$env:DIRECTOR_ASGI_APP='director_orchestrator.main:app'
Write-Switches @{DIRECTOR_ASGI_APP='director_orchestrator.primary_staging:app'; LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH='/data/fresh.sqlite3'}
if($env:DIRECTOR_ASGI_APP -cne 'director_orchestrator.primary_staging:app'){throw 'Inherited entrypoint survived'}
if($env:LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH -cne '/data/fresh.sqlite3'){throw 'Journal pointer missing'}
'''
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "probe.ps1"
            harness.write_text(program)
            environment = Path(directory) / "configuration.env"
            environment.write_text("DIRECTOR_ASGI_APP=director_orchestrator.main:app\nUNCHANGED=value\n")
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(harness), str(script), str(environment)], capture_output=True, text=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr)
            selected = environment.read_text()
            self.assertIn("UNCHANGED=value", selected)
            self.assertIn("LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH=/data/fresh.sqlite3", selected)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_staged_routes_cannot_mutate_existing_tasks_or_run_background_work(self):
        verifier = Mock()
        with tempfile.TemporaryDirectory() as directory:
            task_file = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(task_file)) as db:
                db.execute("CREATE TABLE tasks (id text, status text)")
                db.execute("INSERT INTO tasks VALUES ('existing', 'RUNNING')")
                db.commit()
            before = task_file.read_bytes()
            with patch.dict(os.environ, {"SQLITE_PATH": str(task_file)}):
                with TestClient(create_app(verifier)) as client:
                    for path in ("/v1/intake", "/v1/tasks/existing/approve", "/v1/tasks/existing/resume", "/v1/primary/effects/reconcile"):
                        self.assertEqual(client.post(path, json={}).status_code, 404)
                    status = client.get("/health").json()["langgraph_primary"]
                    self.assertTrue(status.pop("staged_ready"))
                    self.assertTrue(status.pop("evidence_only"))
                    self.assertTrue(all(value is False for value in status.values()))
                    self.assertEqual(client.get("/v1/staging/status").status_code, 200)
            self.assertEqual(before, task_file.read_bytes())
        verifier.assert_called_once()

    def test_failed_acceptance_prevents_service_start_and_sanitizes_error(self):
        with self.assertRaisesRegex(RuntimeError, "verification failed") as caught:
            with TestClient(create_app(Mock(side_effect=ValueError("private-detail")))):
                self.fail("unaccepted service started")
        self.assertNotIn("private-detail", str(caught.exception))

    def test_execution_switch_rejected_before_database(self):
        for name in ("allow_paid_runs", "langgraph_primary_enabled", "langgraph_primary_new_intake_enabled", "langgraph_primary_live_provider_dispatch_enabled"):
            values = dict.fromkeys(("allow_paid_runs", "langgraph_primary_enabled", "langgraph_primary_new_intake_enabled", "langgraph_primary_live_provider_dispatch_enabled"), False)
            values[name] = True
            with patch("director_orchestrator.primary_staging.Settings", return_value=Mock(**values)), patch("director_orchestrator.primary_staging.database_binding") as database:
                with self.assertRaisesRegex(RuntimeError, "switches disabled"):
                    verify_staged_identity()
                database.assert_not_called()

    def test_read_only_file_store_does_not_create_missing_parent(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": "github-v1"}):
            path = Path(directory) / "absent" / "evidence.json"
            store = ActivationEvidenceStore(path, read_only=True)
            self.assertIsNone(store.load())
            self.assertFalse(path.parent.exists())
            with self.assertRaisesRegex(ActivationEvidenceError, "read-only"):
                store._atomic_write({})

    def test_signed_restore_allows_staging_without_final_inventory_and_is_immutable(self):
        from tests.test_acceptance_journal import AcceptanceJournalTests
        fixture = AcceptanceJournalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.accept()
        # Merely accepted is sufficient for migration, never for staged startup.
        fixture.store.require_accepted(fixture.identity)
        with self.assertRaises(ActivationEvidenceError):
            fixture.store.require_staged(fixture.identity)
        from director_orchestrator.primary_activation_evidence import canonical_facts_digest
        for stage, facts in ((ActivationStage.MIGRATED, {}), (ActivationStage.BACKED_UP, {"backup_digest": "sha256:" + "1" * 64}), (ActivationStage.RESTORE_PROVED, {"backup_digest": "sha256:" + "1" * 64, "required_control_plane_tables": 17, "checkpoint_tables": 4, "schema_evidence_rows": 1})):
            fixture.store.record(fixture.identity, stage, evidence_digest=canonical_facts_digest(facts), facts=facts)
        before = fixture.db.read_bytes()
        store = ActivationEvidenceStore(fixture.path, read_only=True)
        store.require_staged(fixture.identity)
        with self.assertRaisesRegex(ActivationEvidenceError, "inventory"):
            store.require_ready(fixture.identity)
        with self.assertRaises(ActivationEvidenceError):
            store.require_accepted(fixture.identity)
        self.assertEqual(before, fixture.db.read_bytes())
        # Exercise the real staged startup verifier through both independently
        # checked boundaries; only the already-tested Postgres read is substituted.
        from dataclasses import asdict, replace
        import hashlib
        import json
        from director_orchestrator.config import Settings
        from director_orchestrator.primary_generation import deployment_digest
        settings = replace(Settings(), allow_paid_runs=False, langgraph_primary_enabled=False,
            langgraph_primary_new_intake_enabled=False, langgraph_primary_live_provider_dispatch_enabled=False,
            app_env=fixture.identity.profile_id,
            langgraph_primary_pinned_commit_sha=fixture.identity.candidate_commit,
            langgraph_primary_pinned_tree_sha=fixture.identity.candidate_tree,
            langgraph_primary_postgres_schema=fixture.identity.postgres_schema,
            langgraph_primary_checkpoint_namespace=fixture.identity.checkpoint_namespace)
        manifest = dict(image_id="sha256:" + "8" * 64, resolved_compose_sha256="9" * 64, version="director-primary-generation/v1", activation=asdict(fixture.identity),
            journal_path=str(fixture.db), registry_path=str(fixture.registry),
            registry_sha256=settings.langgraph_primary_acceptance_registry_sha256,
            provider_bindings_sha256=hashlib.sha256(os.environ.get("LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON", "").encode()).hexdigest(),
            deployment_configuration_sha256=deployment_digest(settings))
        source = fixture.root / "generation.json"
        source.write_text(json.dumps(manifest))
        with closing(sqlite3.connect(fixture.db)) as db:
            db.execute("CREATE TABLE activation_generation (singleton INTEGER PRIMARY KEY, manifest_sha256 TEXT)")
            db.execute("INSERT INTO activation_generation VALUES (1,?)", (hashlib.sha256(source.read_bytes()).hexdigest(),))
            db.commit()
        before = fixture.db.read_bytes()
        with patch.dict(os.environ, {"DIRECTOR_RUNTIME_IMAGE_ID": "sha256:" + "8" * 64, "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256": "9" * 64,
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH": str(source),
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": hashlib.sha256(source.read_bytes()).hexdigest()}), patch(
            "director_orchestrator.primary_generation.GENERATION_DATA_ROOT", fixture.root), patch(
            "director_orchestrator.primary_staging.Settings", return_value=settings), patch(
            "director_orchestrator.primary_staging.database_binding", return_value=(fixture.identity.database_identity, fixture.identity.database_instance_id)):
            verify_staged_identity()
            source.write_text("substituted manifest")
            with self.assertRaises(ActivationEvidenceError):
                verify_staged_identity()
        with self.assertRaises(sqlite3.OperationalError):
            with store._journal.connect() as db:
                db.execute("DELETE FROM activation_state")
        self.assertEqual(before, fixture.db.read_bytes())
        # Revocation must still be checked through the read-only journal path.
        fixture.policy["revoked_deployments"] = [fixture.policy["authorities"]["security"]["deployment"]]
        import json
        fixture.registry.write_text(json.dumps(fixture.policy))
        with self.assertRaises(ActivationEvidenceError):
            store.require_staged(fixture.identity)


class DatabaseIdentityBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN"):
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required; use the sanctioned disposable Postgres lane")

    def setUp(self):
        import psycopg
        self.psycopg = psycopg
        self.dsn = os.environ["LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN"]
        self.schema = "stage_identity_" + uuid.uuid4().hex[:12] + "_v2"
        self.addCleanup(self.cleanup_schema)

    def cleanup_schema(self):
        with self.psycopg.connect(self.dsn, autocommit=True) as db:
            db.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def tables(self):
        with self.psycopg.connect(self.dsn) as db:
            return [row[0] for row in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s ORDER BY table_name", (self.schema,))]

    def test_absent_observation_no_ddl_bootstrap_only_identity_migration_preserves_uuid(self):
        from director_orchestrator.langgraph_postgres import database_binding, open_postgres_control_plane
        with self.assertRaises(self.psycopg.errors.UndefinedTable):
            database_binding(self.dsn, schema=self.schema, allow_insecure_local_transport=True)
        self.assertEqual(self.tables(), [])
        original = database_binding(self.dsn, schema=self.schema, allow_insecure_local_transport=True, bootstrap=True)
        self.assertEqual(self.tables(), ["director_primary_control_plane"])
        self.assertEqual(database_binding(self.dsn, schema=self.schema, allow_insecure_local_transport=True), original)
        self.assertEqual(database_binding(self.dsn, schema=self.schema, allow_insecure_local_transport=True, bootstrap=True), original)
        self.assertEqual(self.tables(), ["director_primary_control_plane"])
        # Identity enrollment is deliberately not runtime schema provisioning.
        # An accepted restart must reject missing inventory without repairing it.
        from director_orchestrator.antigravity_team import TeamAuthorizationError
        with self.assertRaisesRegex(TeamAuthorizationError,"inventory changed"):
            open_postgres_control_plane(self.dsn, schema=self.schema, allow_insecure_local_transport=True,
                accepted_storage_identity=original[0], accepted_instance_id=original[1])
        self.assertEqual(self.tables(), ["director_primary_control_plane"])
        from unittest.mock import patch
        from director_orchestrator import langgraph_postgres
        original_ddl=langgraph_postgres._control_plane_ddl
        def failing_ddl(schema):
            yield from original_ddl(schema)
            yield "SELECT director_intentional_migration_failure()"
        with patch.object(langgraph_postgres,"_control_plane_ddl",side_effect=failing_ddl):
            with self.assertRaises(self.psycopg.errors.UndefinedFunction):
                open_postgres_control_plane(self.dsn,schema=self.schema,allow_insecure_local_transport=True,
                    accepted_storage_identity=original[0],accepted_instance_id=original[1],initial_accepted_migration=True)
        self.assertEqual(self.tables(), ["director_primary_control_plane"])
        with self.psycopg.connect(self.dsn) as db:
            self.assertTrue(db.execute(f"SELECT initial_schema_pending FROM {self.schema}.director_primary_control_plane").fetchone()[0])
        handle = open_postgres_control_plane(self.dsn, schema=self.schema, allow_insecure_local_transport=True,
            accepted_storage_identity=original[0], accepted_instance_id=original[1], initial_accepted_migration=True)
        try:
            self.assertEqual((handle.storage_identity, handle.database_instance_id), original)
            self.assertIn("checkpoints", self.tables())
        finally:
            handle.close()
        with self.psycopg.connect(self.dsn) as db:
            self.assertFalse(db.execute(f"SELECT initial_schema_pending FROM {self.schema}.director_primary_control_plane").fetchone()[0])
            db.execute(f"ALTER TABLE {self.schema}.director_primary_team_grant DISABLE TRIGGER ALL")
        # The explicit migration action cannot be replayed to repair drift after
        # the durable first-migration marker was consumed.
        for initial in (False,True):
            with self.assertRaises(TeamAuthorizationError):
                open_postgres_control_plane(self.dsn,schema=self.schema,allow_insecure_local_transport=True,
                    accepted_storage_identity=original[0],accepted_instance_id=original[1],initial_accepted_migration=initial)

    def test_old_identity_without_initial_phase_cannot_bypass_inventory(self):
        from director_orchestrator.langgraph_postgres import database_binding,open_postgres_control_plane
        from director_orchestrator.antigravity_team import TeamAuthorizationError
        identity,instance=database_binding(self.dsn,schema=self.schema,allow_insecure_local_transport=True,bootstrap=True)
        with self.psycopg.connect(self.dsn) as db:
            db.execute(f"ALTER TABLE {self.schema}.director_primary_control_plane DROP COLUMN initial_schema_pending")
        # Repeating identity bootstrap must not manufacture an initial phase on
        # a preexisting identity singleton with unknown migration history.
        self.assertEqual(database_binding(self.dsn,schema=self.schema,allow_insecure_local_transport=True,bootstrap=True),(identity,instance))
        with self.assertRaises(TeamAuthorizationError):
            open_postgres_control_plane(self.dsn,schema=self.schema,allow_insecure_local_transport=True,
                accepted_storage_identity=identity,accepted_instance_id=instance,initial_accepted_migration=True)
        self.assertEqual(self.tables(),["director_primary_control_plane"])

    def test_wrong_existing_identity_is_rejected_without_repair(self):
        from director_orchestrator.langgraph_postgres import database_binding, PostgresCheckpointConfigurationError
        database_binding(self.dsn, schema=self.schema, allow_insecure_local_transport=True, bootstrap=True)
        with self.psycopg.connect(self.dsn) as db:
            db.execute(f"UPDATE {self.schema}.director_primary_control_plane SET storage_identity='wrong'")
        for bootstrap in (False, True):
            with self.assertRaises(PostgresCheckpointConfigurationError):
                database_binding(self.dsn, schema=self.schema, allow_insecure_local_transport=True, bootstrap=bootstrap)
        self.assertEqual(self.tables(), ["director_primary_control_plane"])
        with self.psycopg.connect(self.dsn) as db:
            self.assertEqual(db.execute(f"SELECT storage_identity FROM {self.schema}.director_primary_control_plane").fetchone()[0], "wrong")
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:33 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 03:13 UTC | END]]
