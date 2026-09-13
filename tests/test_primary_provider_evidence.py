# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 11:33 UTC | BEGIN]]
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import copy
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from director_orchestrator.config import Settings
from director_orchestrator.primary_activation_commands import (
    ProviderActivationAdvanceRequest, build_provider_activation_advance_proof,
)
from director_orchestrator.primary_activation_evidence import ActivationEvidenceError
from director_orchestrator.primary_provider_evidence import (
    MAX_BYTES, VERSION, evidence_id, publish_file, publish_requests, read_requests, require_runtime_off,
)
from director_orchestrator.provider_activation import (
    ProviderActivationBinding, ProviderActivationRegistry, ProviderActivationStage, ProviderActivationSurface,
)
from tests.test_primary_activation_evidence import identity
from tests.test_primary_provider_activation import MemoryStore


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.identity = identity()
        self.generation = "d" * 64
        self.binding = ProviderActivationBinding("claude", "workspace", "provider-v1", "app-v1", "adapter-v1", "workspace-v1", "binding-v1")
        raw_binding = asdict(self.binding)
        raw_binding.pop("provider_id")
        self.settings = replace(Settings(), slack_owner_user_id="UOWNER", slack_gateway_secret="s" * 40,
            shared_secret="h" * 40, langgraph_primary_required_providers=("claude",),
            langgraph_primary_provider_activation_bindings_json=json.dumps({"claude": raw_binding}),
            allow_paid_runs=False, langgraph_primary_enabled=False,
            langgraph_primary_new_intake_enabled=False, langgraph_primary_live_provider_dispatch_enabled=False,
            langgraph_primary_offline_simulation_enabled=False, langgraph_parallel_kill_switch=True)
        self.store = MemoryStore()
        registry = ProviderActivationRegistry(self.store, self.binding)
        for surface in ProviderActivationSurface:
            for stage in list(ProviderActivationStage)[:4]:
                registry.record(surface=surface, stage=stage, evidence_id="fixture-prior-" + stage.value,
                    evidence_digest="a" * 64, observed_at=self.now - timedelta(seconds=5),
                    expires_at=self.now + timedelta(minutes=5), now=self.now)
        self.verifier = Mock()
        self.verifier.verify.return_value = True
        self.document = self.make_document()

    def make_document(self, capture_digest="b" * 64, *, fresh=False):
        requests = []
        for surface in ProviderActivationSurface:
            for stage in (list(ProviderActivationStage) if fresh else [ProviderActivationStage.LIVE_OK]):
                observation = f"fixture-only operator observation: {surface.value} {stage.value}".encode()
                observed_digest = capture_digest if stage is ProviderActivationStage.LIVE_OK else hashlib.sha256(observation).hexdigest()
                request = ProviderActivationAdvanceRequest(actor_id="UOWNER", binding_version=self.binding.binding_version,
                    surface=surface, stage=stage, expected_revision=list(ProviderActivationStage).index(stage),
                    evidence_id=evidence_id(self.identity, self.generation, "claude", surface, observed_digest, stage=stage),
                    evidence_digest=observed_digest, observed_at=self.now, expires_at=self.now + timedelta(minutes=3),
                    gateway_proof=dict(issued_at=int(self.now.timestamp()), nonce="n" * 32, signature="0" * 64))
                request = request.model_copy(update={"gateway_proof": build_provider_activation_advance_proof(
                    self.settings.slack_gateway_secret, self.settings.shared_secret, "claude", request,
                    issued_at=int(self.now.timestamp()), nonce=surface.value + stage.value + "n" * 32)})
                item = dict(provider_id="claude", request=request.model_dump(mode="json"))
                if stage is not ProviderActivationStage.LIVE_OK:
                    item["observation_base64"] = base64.b64encode(observation).decode()
                requests.append(item)
        return dict(version=VERSION, generation=self.generation, requests=requests)

    def read(self, document=None, **changes):
        return read_requests(io.BytesIO(json.dumps(document or self.document).encode()),
            settings=self.settings, identity=changes.get("identity", self.identity),
            generation=changes.get("generation", self.generation), now=changes.get("now", self.now))

    def publish(self, **changes):
        return publish_requests(changes.get("store", self.store), changes.get("verifier", self.verifier),
            settings=self.settings, requests=changes.get("requests", self.read()), now=self.now)

    def test_two_surfaces_publish_with_exact_durable_replay_and_no_extra_rows(self):
        first = self.publish()
        self.assertTrue(first["selected_provider_live_ready"])
        self.assertEqual(first, self.publish())
        self.assertEqual(len(self.store.claims), 2)
        for surface in ProviderActivationSurface:
            self.assertEqual(len(self.store.load(self.binding, surface)), 5)

    def test_changed_outer_generation_or_candidate_cannot_rebind_valid_signature(self):
        changed = copy.deepcopy(self.document)
        changed["generation"] = "e" * 64
        with self.assertRaises(ActivationEvidenceError):
            self.read(changed, generation="e" * 64)
        with self.assertRaises(ActivationEvidenceError):
            self.read(identity=replace(self.identity, candidate_commit="f" * 40))
        changed["requests"][0]["request"]["evidence_id"] = evidence_id(self.identity, "e" * 64,
            "claude", ProviderActivationSurface.APP, "b" * 64)
        with self.assertRaises(ActivationEvidenceError):
            self.read(changed, generation="e" * 64)
        self.assertEqual(self.store.claims, {})

    def test_closed_bounded_input_rejects_duplicates_unknowns_missing_surface_and_unsigned_changes(self):
        for raw in (b'{"version":"a","version":"b"}', b' ' * (MAX_BYTES + 1)):
            with self.assertRaises(ActivationEvidenceError):
                read_requests(io.BytesIO(raw), settings=self.settings, identity=self.identity,
                    generation=self.generation, now=self.now)
        mutations = [lambda d: d.update(extra=True),
            lambda d: d["requests"].append(d["requests"][0]),
            lambda d: d["requests"][0]["request"].update(actor_id="UOTHER"),
            lambda d: d["requests"][0]["request"].update(evidence_digest="f" * 64),
            lambda d: d["requests"][0]["request"].update(expected_revision="4"),
            lambda d: d["requests"][0]["request"].update(expected_revision=3),
            lambda d: d["requests"][0]["request"].update(stage="DRY_RUN_OK"),
            lambda d: d["requests"][0]["request"].update(binding_version="changed")]
        for mutate in mutations:
            changed = copy.deepcopy(self.document)
            mutate(changed)
            with self.assertRaises(ActivationEvidenceError): self.read(changed)
        with self.assertRaises(ActivationEvidenceError):
            self.read(now=self.now + timedelta(seconds=301))

    def test_missing_prior_or_bad_second_capture_cannot_partially_write(self):
        saved = self.store.rows.pop((self.binding, ProviderActivationSurface.RUNTIME_ADAPTER))
        with self.assertRaises(ActivationEvidenceError): self.publish()
        self.assertEqual(self.store.claims, {})
        self.store.rows[(self.binding, ProviderActivationSurface.RUNTIME_ADAPTER)] = saved
        self.verifier.verify.side_effect = [True, False]
        with self.assertRaises(ActivationEvidenceError): self.publish()
        self.assertEqual(self.store.claims, {})

    def test_fresh_signed_observations_publish_and_replay_full_indexed_histories(self):
        self.store = MemoryStore()
        self.document = self.make_document(fresh=True)
        self.assertTrue(self.publish()["selected_provider_live_ready"])
        self.assertTrue(self.publish()["selected_provider_live_ready"])
        self.assertEqual(len(self.store.claims), 10)
        self.assertEqual([len(rows) for rows in self.store.rows.values()], [5, 5])

    def test_fresh_batch_rejects_missing_reordered_and_tampered_observations_before_writes(self):
        self.store = MemoryStore()
        self.document = self.make_document(fresh=True)
        mutations = [lambda d: d["requests"].pop(1),
            lambda d: d["requests"].__setitem__(slice(0, 2), list(reversed(d["requests"][:2]))),
            lambda d: d["requests"][0].update(observation_base64=base64.b64encode(b"changed").decode()),
            lambda d: d["requests"][0].pop("observation_base64"),
            lambda d: d["requests"].pop(),
            lambda d: d["requests"][0]["request"].update(observed_at=(self.now + timedelta(seconds=1)).isoformat())]
        for mutate in mutations:
            changed = copy.deepcopy(self.document)
            mutate(changed)
            with self.assertRaises((ActivationEvidenceError, ValueError)):
                self.publish(requests=self.read(changed))
            self.assertEqual(self.store.claims, {})

    def test_recovery_can_supply_only_remaining_surface_without_repeating_native_execution(self):
        self.store = MemoryStore()
        self.document = self.make_document(fresh=True)
        original = self.store.append_authorized
        def fail_second_surface(evidence, **kwargs):
            if evidence.surface is ProviderActivationSurface.RUNTIME_ADAPTER:
                raise RuntimeError("fixture interruption")
            return original(evidence, **kwargs)
        with patch.object(self.store, "append_authorized", side_effect=fail_second_surface):
            with self.assertRaises(RuntimeError): self.publish()
        remaining = copy.deepcopy(self.document)
        remaining["requests"] = remaining["requests"][5:]
        self.assertTrue(self.publish(requests=self.read(remaining))["selected_provider_live_ready"])
        self.assertEqual(len(self.store.claims), 10)

    def test_deadline_and_altered_indexed_history_fail_closed(self):
        with self.assertRaises(ActivationEvidenceError):
            publish_requests(self.store, self.verifier, settings=self.settings, requests=self.read(), deadline=0)
        self.assertEqual(self.store.claims, {})
        self.store = MemoryStore()
        self.document = self.make_document(fresh=True)
        self.publish()
        self.store.rows[(self.binding, ProviderActivationSurface.APP)][0] = replace(
            self.store.rows[(self.binding, ProviderActivationSurface.APP)][0], evidence_id="altered-past")
        with self.assertRaises(ActivationEvidenceError): self.publish()
        self.assertEqual(len(self.store.claims), 10)

    def test_authenticated_future_or_expired_observation_fails_before_writes(self):
        self.store = MemoryStore()
        self.document = self.make_document(fresh=True)
        for field, value in (("observed_at", self.now + timedelta(seconds=1)),
            ("expires_at", self.now - timedelta(seconds=1))):
            changed = copy.deepcopy(self.document)
            request = ProviderActivationAdvanceRequest.model_validate(changed["requests"][0]["request"])
            request = request.model_copy(update={field: value})
            request = request.model_copy(update={"gateway_proof": build_provider_activation_advance_proof(
                self.settings.slack_gateway_secret, self.settings.shared_secret, "claude", request,
                issued_at=int(self.now.timestamp()), nonce="fresh-but-invalid-observation-" + "n" * 32)})
            changed["requests"][0]["request"] = request.model_dump(mode="json")
            with self.assertRaises((ActivationEvidenceError, ValueError)):
                self.publish(requests=self.read(changed))
            self.assertEqual(self.store.claims, {})

    def test_partial_database_failure_resumes_exact_input_without_redispatch(self):
        original = self.store.append_authorized
        def fail_second(evidence, **kwargs):
            if evidence.surface is ProviderActivationSurface.RUNTIME_ADAPTER:
                raise RuntimeError("fixture database interruption")
            return original(evidence, **kwargs)
        with patch.object(self.store, "append_authorized", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "interruption"): self.publish()
        self.assertEqual(len(self.store.claims), 1)
        self.assertTrue(self.publish()["selected_provider_live_ready"])
        self.assertEqual(len(self.store.claims), 2)

    def test_conflicting_prior_publication_or_expired_chain_is_refused(self):
        self.publish()
        changed = self.make_document("c" * 64)
        with self.assertRaises(ActivationEvidenceError): self.publish(requests=self.read(changed))
        self.store.rows[(self.binding, ProviderActivationSurface.APP)][0] = replace(
            self.store.rows[(self.binding, ProviderActivationSurface.APP)][0],
            expires_at=self.now - timedelta(seconds=1))
        with self.assertRaises(ActivationEvidenceError): self.publish()

    def test_readback_revocation_cannot_report_success(self):
        original = self.store.append_authorized
        def revoke_after_write(evidence, **kwargs):
            result = original(evidence, **kwargs)
            if evidence.surface is ProviderActivationSurface.RUNTIME_ADAPTER:
                self.verifier.verify.return_value = False
            return result
        with patch.object(self.store, "append_authorized", side_effect=revoke_after_write):
            with self.assertRaises(ActivationEvidenceError): self.publish()

    def test_runtime_switches_fail_before_any_store_open(self):
        with patch.dict(os.environ, {"DIRECTOR_ASGI_APP": "director_orchestrator.primary_staging:app"}):
            require_runtime_off(self.settings)
            for field in ("allow_paid_runs", "langgraph_primary_enabled", "langgraph_primary_new_intake_enabled",
                "langgraph_primary_live_provider_dispatch_enabled", "langgraph_primary_offline_simulation_enabled"):
                with self.assertRaises(ActivationEvidenceError): require_runtime_off(replace(self.settings, **{field: True}))
            with self.assertRaises(ActivationEvidenceError):
                require_runtime_off(replace(self.settings, langgraph_parallel_kill_switch=False))
        with patch.dict(os.environ, {"DIRECTOR_ASGI_APP": "director_orchestrator.main:app"}):
            with self.assertRaises(ActivationEvidenceError): require_runtime_off(self.settings)

    def test_unselected_providers_are_not_read_and_mid_read_deadline_stops_work(self):
        configured = json.loads(self.settings.langgraph_primary_provider_activation_bindings_json)
        configured["openai"] = dict(configured["claude"])
        self.settings = replace(self.settings, langgraph_primary_required_providers=("claude", "openai"),
            langgraph_primary_provider_activation_bindings_json=json.dumps(configured))
        original = self.store.load
        def selected_only(binding, surface):
            self.assertEqual(binding.provider_id, "claude")
            return original(binding, surface)
        with patch.object(self.store, "load", side_effect=selected_only):
            result = self.publish()
        self.assertEqual(result["published_providers"], ["claude"])
        self.assertNotIn("combined_live_ready", result)
        tick = [0]
        def overrun(binding, surface):
            value = original(binding, surface)
            tick[0] = 121
            return value
        with patch("director_orchestrator.primary_provider_evidence.time.monotonic", side_effect=lambda: tick[0]), \
            patch.object(self.store, "load", side_effect=overrun) as read:
            with self.assertRaises(ActivationEvidenceError):
                publish_requests(self.store, self.verifier, settings=self.settings,
                    requests=self.read(), deadline=120)
            self.assertEqual(read.call_count, 1)

    def test_cli_refuses_runtime_before_database_identity_read(self):
        from director_orchestrator.primary_generation import main
        with patch("sys.argv", ["primary_generation", "publish-provider-evidence", "--evidence-file", "unused.json"]), \
            patch.dict(os.environ, {"DIRECTOR_ASGI_APP": "director_orchestrator.primary_staging:app"}), \
            patch("director_orchestrator.config.Settings", return_value=replace(self.settings, allow_paid_runs=True)), \
            patch("director_orchestrator.langgraph_postgres.database_binding") as database:
            with self.assertRaises(ActivationEvidenceError): main()
            database.assert_not_called()

    def test_generation_restore_validation_and_input_fail_before_write_store(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "publication.json"
            path.write_text(json.dumps(self.document), encoding="utf-8")
            with patch.dict(os.environ, {"DIRECTOR_ASGI_APP": "director_orchestrator.primary_staging:app",
                "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": self.generation,
                "DIRECTOR_PROVIDER_VALIDATION_GENERATION": self.generation}), \
                patch("director_orchestrator.primary_generation.require_generation") as generation, \
                patch("director_orchestrator.primary_generation.require_validation_generation") as validation, \
                patch("director_orchestrator.primary_activation_evidence.ActivationEvidenceStore") as journal, \
                patch("director_orchestrator.provider_validation.capture_verifier_from_environment", return_value=self.verifier), \
                patch("director_orchestrator.langgraph_postgres.open_provider_activation_store") as opener:
                for boundary in (generation, journal.return_value.require_staged, validation):
                    boundary.side_effect = ActivationEvidenceError("fixture preflight refusal")
                    with self.assertRaises(ActivationEvidenceError):
                        publish_file(path, settings=self.settings, identity=self.identity)
                    boundary.side_effect = None
                    opener.assert_not_called()
                with patch.dict(os.environ, {"DIRECTOR_PROVIDER_VALIDATION_GENERATION": "f" * 64}):
                    with self.assertRaises(ActivationEvidenceError):
                        publish_file(path, settings=self.settings, identity=self.identity)
                opener.assert_not_called()
                path.write_text('{"unknown":true}')
                with self.assertRaises(ActivationEvidenceError):
                    publish_file(path, settings=self.settings, identity=self.identity)
                opener.assert_not_called()


class SignedValidationRegressionTests(unittest.TestCase):
    def test_validate_providers_reads_real_signed_restore_without_legacy_hmac_secrets(self):
        from director_orchestrator.primary_activation_evidence import ActivationStage, canonical_facts_digest
        from director_orchestrator.primary_generation import main
        from tests.test_acceptance_journal import AcceptanceJournalTests
        fixture = AcceptanceJournalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.accept()
        for stage, facts in ((ActivationStage.MIGRATED, {}),
            (ActivationStage.BACKED_UP, {"backup_digest": "sha256:" + "1" * 64}),
            (ActivationStage.RESTORE_PROVED, {"backup_digest": "sha256:" + "1" * 64,
                "required_control_plane_tables": 17, "checkpoint_tables": 4, "schema_evidence_rows": 1})):
            fixture.store.record(fixture.identity, stage, evidence_digest=canonical_facts_digest(facts), facts=facts)
        settings = replace(Settings(), langgraph_primary_pinned_commit_sha=fixture.identity.candidate_commit,
            langgraph_primary_pinned_tree_sha=fixture.identity.candidate_tree,
            app_env=fixture.identity.profile_id, langgraph_primary_postgres_schema=fixture.identity.postgres_schema,
            langgraph_primary_checkpoint_namespace=fixture.identity.checkpoint_namespace)
        before = fixture.db.read_bytes()
        with patch("sys.argv", ["primary_generation", "validate-providers"]), \
            patch("director_orchestrator.config.Settings", return_value=settings), \
            patch("director_orchestrator.langgraph_postgres.database_binding",
                return_value=(fixture.identity.database_identity, fixture.identity.database_instance_id)), \
            patch("director_orchestrator.primary_generation.require_generation"), \
            patch("director_orchestrator.primary_generation.require_validation_generation"), \
            patch("director_orchestrator.provider_validation_cli.main") as validate:
            main()
            validate.assert_called_once()
        self.assertEqual(fixture.db.read_bytes(), before)


class PublicationScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell required")
    def test_actual_action_uses_only_staged_checks_and_readonly_bounded_file(self):
        script = Path(__file__).resolve().parents[1] / "scripts/windows-langgraph-primary-control.ps1"
        program = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parser failure'}
$block=$ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($Action -eq "PublishProviderEvidence")')},$true) | Select-Object -First 1
$Action='PublishProviderEvidence'; $ProviderEvidencePath=$args[1]; $script:seen=0
function Assert-RunningCandidate { param($AsgiApp) if($AsgiApp -cne 'director_orchestrator.primary_staging:app'){throw 'wrong app'} }
function Wait-Health { @{langgraph_primary=@{staged_ready=$true;evidence_only=$true;primary_runtime_active=$false}} }
function Write-Switches { throw 'forbidden switch mutation' }
function Stop-And-Prove-AllIntake { throw 'forbidden service mutation' }
function Invoke-Compose {
    if(($args -join ' ') -notmatch 'run --rm --no-deps --pull never -T -v .+:/run/director-provider-evidence.json:ro --entrypoint python orchestrator -m director_orchestrator.primary_generation publish-provider-evidence --evidence-file /run/director-provider-evidence.json'){throw 'unexpected command'}
    $script:seen++
}
& ([scriptblock]::Create($block.Extent.Text))
if($script:seen -ne 1){throw 'expected exactly one publisher'}
'''
        with tempfile.TemporaryDirectory() as temp:
            harness, source = Path(temp) / "probe.ps1", Path(temp) / "evidence.json"
            harness.write_text(program)
            source.write_text("{}")
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(harness), str(script), str(source)],
                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class PublicationFactoryTests(unittest.TestCase):
    def test_bounded_factory_checks_existing_tables_and_closes_without_setup(self):
        from director_orchestrator.langgraph_postgres import (
            _parse_connection_target, _storage_identity, open_provider_activation_store,
        )
        from psycopg.conninfo import conninfo_to_dict
        dsn = "postgresql://fixture:fixture@127.0.0.1:5432/fixture?sslmode=disable"
        schema = "publication_fixture_v1"
        storage = _storage_identity(_parse_connection_target(dsn, conninfo_to_dict=conninfo_to_dict,
            allow_insecure_local_transport=True), schema=schema)
        with patch("psycopg_pool.ConnectionPool") as pool_type, \
            patch("director_orchestrator.langgraph_postgres._setup_control_plane") as setup:
            pool = pool_type.return_value
            with open_provider_activation_store(dsn, schema=schema, accepted_storage_identity=storage,
                accepted_instance_id="11111111-1111-4111-8111-111111111111",
                allow_insecure_local_transport=True) as store:
                self.assertFalse(hasattr(store, "claim"))
            self.assertEqual(pool_type.call_args.kwargs["max_size"], 1)
            pool.close.assert_called_once()
            setup.assert_not_called()
            commands = [c.args[0] for c in pool.connection.return_value.__enter__.return_value.execute.call_args_list]
            self.assertEqual(commands, ["SET TRANSACTION READ ONLY",
                f"SELECT 1 FROM {schema}.director_provider_activation_evidence LIMIT 0",
                f"SELECT 1 FROM {schema}.director_provider_activation_authorization LIMIT 0"])
            pool.close.reset_mock()
            pool.open.side_effect = RuntimeError("fixture unavailable")
            with self.assertRaises(RuntimeError):
                with open_provider_activation_store(dsn, schema=schema, accepted_storage_identity=storage,
                    accepted_instance_id="11111111-1111-4111-8111-111111111111",
                    allow_insecure_local_transport=True):
                    self.fail("unavailable pool opened")
            pool.close.assert_called_once()
            setup.assert_not_called()


class PublicationPostgresTests(unittest.TestCase):
    """Run only in the sanctioned disposable PostgreSQL lane, never production."""
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN")
        if not cls.dsn:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN required; use sanctioned disposable lane")

    def test_real_capture_publication_replay_preserves_all_runtime_tables(self):
        import uuid
        import psycopg
        from director_orchestrator.langgraph_postgres import (
            database_binding, open_postgres_control_plane, open_provider_activation_store,
        )
        from tests.test_provider_validation import PostgresValidationTests, binding
        from director_orchestrator.provider_validation import PostgresValidationStore
        fixture = PublicationTests()
        fixture.setUp()
        native = PostgresValidationTests()
        native.store = PostgresValidationStore(self.dsn)
        native.store.bootstrap()
        admission, capture, capture_digest, verifier = native.completed()
        from director_orchestrator.primary_provider_evidence import _BoundedCaptureReader
        verifier.store = _BoundedCaptureReader(verifier.store.dsn)
        with verifier.store.connect() as db:
            self.assertEqual(db.execute("SHOW transaction_read_only").fetchone()[0], "on")
            self.assertEqual(db.execute("SHOW statement_timeout").fetchone()[0], "10s")
            self.assertEqual(db.execute("SHOW lock_timeout").fetchone()[0], "10s")
        schema = "publication_" + uuid.uuid4().hex[:20] + "_v1"
        storage, instance = database_binding(self.dsn, schema=schema,
            allow_insecure_local_transport=True, bootstrap=True)
        def cleanup():
            with psycopg.connect(self.dsn, autocommit=True) as db:
                db.execute(f"DROP SCHEMA {schema} CASCADE")
        self.addCleanup(cleanup)
        fixture.identity = replace(fixture.identity, database_identity=storage,
            database_instance_id=instance, postgres_schema=schema)
        fixture.generation = admission["generation"]
        fixture.binding = binding()
        binding_fields = asdict(fixture.binding)
        binding_fields.pop("provider_id")
        fixture.settings = replace(fixture.settings,
            langgraph_primary_provider_activation_bindings_json=json.dumps({"claude": binding_fields}))
        fixture.document = fixture.make_document(capture_digest, fresh=True)
        with open_postgres_control_plane(self.dsn, schema=schema, allow_insecure_local_transport=True,
            accepted_storage_identity=storage, accepted_instance_id=instance, initial_accepted_migration=True) as handle:
            for surface in ProviderActivationSurface:
                self.assertEqual(handle.claim_store.load(fixture.binding, surface), ())
        def table_counts():
            with psycopg.connect(self.dsn) as db:
                db.execute("SET TRANSACTION READ ONLY")
                names = db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema,)).fetchall()
                return {name: db.execute(f'SELECT count(*) FROM {schema}."{name}"').fetchone()[0] for name, in names}
        before = table_counts()
        with open_provider_activation_store(self.dsn, schema=schema,
            accepted_storage_identity=storage, accepted_instance_id=instance,
            allow_insecure_local_transport=True) as store:
            for forbidden in ("claim", "append", "acquire_writer", "is_factory_bound_control_plane"):
                self.assertFalse(hasattr(store, forbidden))
            self.assertTrue(fixture.publish(store=store, verifier=verifier)["selected_provider_live_ready"])
            self.assertTrue(fixture.publish(store=store, verifier=verifier)["selected_provider_live_ready"])
        after = table_counts()
        self.assertEqual(after.pop("director_provider_activation_evidence"), 10)
        self.assertEqual(before.pop("director_provider_activation_evidence"), 0)
        self.assertEqual(after.pop("director_provider_activation_authorization"), 10)
        self.assertEqual(before.pop("director_provider_activation_authorization"), 0)
        self.assertEqual(after, before)
        with native.store.connect() as db:
            count = db.execute("SELECT count(*) FROM director_provider_validation_v1.attempts WHERE generation=%s", (fixture.generation,)).fetchone()[0]
            self.assertEqual(count, 1)
        # A second factory selection cannot retarget the accepted instance.
        with self.assertRaises(Exception):
            with open_provider_activation_store(self.dsn, schema=schema,
                accepted_storage_identity="postgres:sha256:" + "f" * 64,
                accepted_instance_id=instance, allow_insecure_local_transport=True):
                self.fail("wrong storage identity accepted")
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 11:33 UTC | END]]
