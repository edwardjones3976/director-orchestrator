# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | BEGIN]]
import base64
from contextlib import closing
from dataclasses import asdict, replace
import hashlib
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator.primary_activation_evidence import ActivationEvidenceStore, ActivationStage, ActivationEvidenceError, canonical_facts_digest
from director_orchestrator.acceptance_journal import AcceptanceJournal, require_runtime_acceptance
from director_orchestrator.config import Settings
from tests.test_provider_acceptance_v2 import ProviderAcceptanceV2Tests


def _accept_process(path, artifact, identity, queue):
    try:
        ActivationEvidenceStore(path).record_acceptance_artifact(identity, artifact)
        queue.put("accepted")
    except Exception as exc:
        queue.put(type(exc).__name__)


class AcceptanceJournalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ProviderAcceptanceV2Tests()
        self.fixture.setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "old-v1.json"
        self.registry = self.root / "registry.json"
        self.db = self.root / "journal.sqlite3"
        self.identity = self.fixture.context.activation
        self.policy = dict(version="director-acceptance-registry/v1", activation=asdict(self.identity),
            repository="owner/repo", pull_request_number=100, packet_digest="d"*64,
            material_author_providers=["openai"], authorities={}, revoked_deployments=[], ci_oidc_policy=None)
        for role, authority in self.fixture.authorities.items():
            fields = asdict(authority)
            fields["public_key"] = base64.b64encode(fields["public_key"]).decode()
            fields["required_ci_checks"] = sorted(fields["required_ci_checks"])
            self.policy["authorities"][role] = fields
        self.registry.write_text(json.dumps(self.policy))
        self.env = patch.dict("os.environ", {
            "LANGGRAPH_PRIMARY_ACCEPTANCE_MODE":"provider-v2",
            "LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH":str(self.registry),
            "LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256":hashlib.sha256(self.registry.read_bytes()).hexdigest(),
            "LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH":str(self.db),
            "DIRECTOR_COMPOSE_PROJECT_NAME":"compose"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.store = ActivationEvidenceStore(self.path)
        facts = {"preflight":True}
        self.store.record(self.identity, ActivationStage.PREFLIGHT, facts=facts, evidence_digest=canonical_facts_digest(facts))
        self.context = self.store._journal.challenge(self.identity)
        self.fixture.package["context"] = self.context
        self.fixture.sign()
        self.artifact = self.root / "acceptance.json"
        self.artifact.write_text(json.dumps(self.fixture.package))

    def accept(self):
        return self.store.record_acceptance_artifact(self.identity, self.artifact)

    def test_real_startup_rejects_missing_acceptance_before_database_or_writer(self):
        from director_orchestrator.primary_default import DefaultPrimaryActivation
        settings = replace(Settings(), langgraph_primary_enabled=True, allow_paid_runs=False,
            langgraph_primary_acceptance_mode="provider-v2",
            langgraph_primary_acceptance_registry_path=str(self.registry),
            langgraph_primary_acceptance_registry_sha256=hashlib.sha256(self.registry.read_bytes()).hexdigest(),
            langgraph_primary_acceptance_journal_path=str(self.db))
        with patch("director_orchestrator.primary_default.resolve_default_primary_authority"), patch("director_orchestrator.primary_default.build_typed_external_effect_grant"), patch("director_orchestrator.primary_default.open_postgres_control_plane") as database:
            with self.assertRaises(ActivationEvidenceError): DefaultPrimaryActivation(settings)
            database.assert_not_called()

    def test_transactional_acceptance_survives_restart_and_rejects_replay(self):
        self.accept()
        self.assertFalse(self.path.exists())
        loaded = ActivationEvidenceStore(self.path).load()
        self.assertEqual("ACCEPTED", loaded["stages"][-1]["stage"])
        with self.assertRaises(ActivationEvidenceError): self.accept()

    def test_post_consumption_failure_rolls_back_both_rows(self):
        with patch.object(AcceptanceJournal, "_write", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError): self.accept()
        with closing(sqlite3.connect(self.db)) as db:
            self.assertIsNone(db.execute("SELECT accepted_at FROM acceptance_challenge").fetchone()[0])
        self.assertEqual(1, len(ActivationEvidenceStore(self.path).load()["stages"]))
        self.accept()

    def test_crossprocess_exact_replay_has_one_winner(self):
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        processes = [ctx.Process(target=_accept_process, args=(str(self.path), str(self.artifact), self.identity, queue)) for _ in range(2)]
        for process in processes: process.start()
        outcomes = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(30)
            self.assertEqual(0, process.exitcode)
        self.assertEqual(1, outcomes.count("accepted"))
        self.assertEqual(2, len(ActivationEvidenceStore(self.path).load()["stages"]))

    def test_projection_tamper_or_revoked_current_policy_fails_readback(self):
        self.accept()
        with closing(sqlite3.connect(self.db)) as db:
            raw = json.loads(db.execute("SELECT document FROM activation_state").fetchone()[0])
            raw["stages"][-1]["source_artifact"]["receipts"]["security"]["session_id"] = "spoofed"
            db.execute("UPDATE activation_state SET document=?", (json.dumps(raw),))
            db.commit()
        with self.assertRaises(ActivationEvidenceError): ActivationEvidenceStore(self.path).load()
        self.policy["revoked_deployments"] = ["security-isolated-service"]
        self.registry.write_text(json.dumps(self.policy))
        with self.assertRaises(ActivationEvidenceError): self.store.load()

    def test_state_compare_and_swap_rejects_stale_writer(self):
        other = ActivationEvidenceStore(self.path)
        stale = other.load()
        self.accept()
        with self.assertRaises(ActivationEvidenceError): other._atomic_write(stale)

    def test_runtime_requires_restore_and_validates_activated_restart(self):
        settings = replace(Settings(), app_env="profile", langgraph_primary_acceptance_mode="provider-v2",
            langgraph_primary_acceptance_registry_path=str(self.registry),
            langgraph_primary_acceptance_registry_sha256=hashlib.sha256(self.registry.read_bytes()).hexdigest(),
            langgraph_primary_acceptance_journal_path=str(self.db),
            langgraph_primary_pinned_commit_sha=self.identity.candidate_commit,
            langgraph_primary_pinned_tree_sha=self.identity.candidate_tree,
            langgraph_primary_postgres_schema="primary", langgraph_primary_checkpoint_namespace="checkpoint")
        with self.assertRaises(ActivationEvidenceError): require_runtime_acceptance(settings)
        self.accept()
        for stage, facts in ((ActivationStage.MIGRATED,{"schema_current":True}),
                             (ActivationStage.BACKED_UP,{"backup_digest":"sha256:"+"1"*64}),
                             (ActivationStage.RESTORE_PROVED,{"backup_digest":"sha256:"+"1"*64,"required_control_plane_tables":17,"checkpoint_tables":4,"schema_evidence_rows":1})):
            self.store.record(self.identity, stage, facts=facts, evidence_digest=canonical_facts_digest(facts))
        self.store.bind_legacy_inventory(self.identity, inventory_digest="2"*64, task_count=0)
        require_runtime_acceptance(settings)
        self.store.record(self.identity, ActivationStage.ACTIVATED, facts={}, evidence_digest=canonical_facts_digest({}))
        require_runtime_acceptance(settings)
        with self.assertRaises(ActivationEvidenceError):
            require_runtime_acceptance(replace(settings, langgraph_primary_pinned_commit_sha="3"*40))


if __name__ == "__main__": unittest.main()

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | END]]
