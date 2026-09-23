# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:00 UTC | BEGIN]]
"""In-image read-only scorecard evidence: capture attestations and consistency."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from director_orchestrator import primary_scorecard_evidence as evidence
from director_orchestrator.primary_activation_evidence import ActivationEvidenceError
from director_orchestrator.primary_provider_activation import PrimaryProviderActivationService
from director_orchestrator.provider_activation import (
    ProviderActivationBinding, ProviderActivationRegistry, ProviderActivationStage, ProviderActivationSurface,
)
from director_orchestrator.provider_validation import binding_digest

TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
PIN = "e" * 64
NOW = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)


def _load(name):
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"scorecard_evidence_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MemoryStore = _load("test_primary_provider_activation").MemoryStore


def binding(provider):
    return ProviderActivationBinding(provider_id=provider, workspace_id="default-director", provider_version="v1",
                                     app_version="v1", runtime_adapter_version="v1", workspace_version="v1",
                                     binding_version="v1")


def advance(store, provider_binding, surface, through=ProviderActivationStage.LIVE_OK):
    registry = ProviderActivationRegistry(store, provider_binding)
    stages = list(ProviderActivationStage)
    for index, stage in enumerate(stages[: stages.index(through) + 1], 1):
        registry.record(surface=surface, stage=stage, evidence_id=f"{provider_binding.provider_id}-{surface.value}-{index}",
                        evidence_digest=hashlib.sha256(f"{provider_binding.provider_id}:{surface.value}:{stage.value}".encode()).hexdigest(),
                        observed_at=NOW + timedelta(seconds=index), expires_at=NOW + timedelta(hours=1),
                        now=NOW + timedelta(seconds=index))


class ScorecardEvidenceUnitTests(unittest.TestCase):
    def test_capture_product_names_only_real_products(self):
        cases = (("openai", b'{"object":"response","id":"resp_1"}', "openai-responses"),
                 ("openai", b'{"type":"thread.started"}\n{"type":"turn.completed"}', "openai-codex"),
                 ("openai", b'{"type":"message"}', "openai-codex"),
                 ("grok", b'{"object":"response","model":"grok-4"}', "xai-grok-responses"),
                 ("grok", b'{"object":"response","model":"gpt-5"}', "xai-grok-cli"),
                 ("grok", b'{"result":"done"}', "xai-grok-cli"),
                 ("grok", b'[1, 2]', None),
                 ("grok", b'not json', None),
                 ("claude", b'{"type":"result","result":"ok"}', "anthropic-claude"),
                 ("claude", b'{"type":"assistant"}', None),
                 ("claude", b'{"type":"result"}\n{"type":"result"}', None),
                 ("cursor", b'{"type":"result"}', None))
        for provider, output, expected in cases:
            with self.subTest(provider=provider, output=output):
                self.assertEqual(expected, evidence._capture_product(provider, output))

    def test_capture_attestations_report_digests_and_expiry_but_health_is_unchanged(self):
        store = MemoryStore()
        bindings = {provider: binding(provider) for provider in ("openai", "claude")}
        for surface in ProviderActivationSurface:
            advance(store, bindings["openai"], surface)
        advance(store, bindings["claude"], ProviderActivationSurface.APP)
        verified = {row.evidence_digest for surface in ProviderActivationSurface
                    for row in store.load(bindings["openai"], surface)}
        verifier = Mock()
        verifier.verify.side_effect = lambda _binding, digest, expires_at: digest in verified
        service = PrimaryProviderActivationService(store=store, bindings=bindings, required_providers=("openai", "claude"),
                                                   clock=lambda: NOW, capture_verifier=verifier)
        lanes = service.capture_attestations()
        self.assertEqual(set(lanes), {"openai", "claude"})
        self.assertEqual(lanes["openai"]["binding_digest"], binding_digest(bindings["openai"]))
        for name in ("app", "runtime_adapter"):
            surface = lanes["openai"]["surfaces"][name]
            self.assertEqual(surface["stage"], "LIVE_OK")
            self.assertTrue(surface["capture_verified"])
            self.assertEqual(surface["expires_at"], (NOW + timedelta(hours=1)).isoformat())
            self.assertEqual(len(surface["evidence_digest"]), 64)
        self.assertFalse(lanes["claude"]["surfaces"]["app"]["capture_verified"])
        self.assertIsNone(lanes["claude"]["surfaces"]["runtime_adapter"])
        # /health stays booleans only; attestations are a separate operator read.
        self.assertEqual(set(service.health()), {"core_live_ready", "app_dry_run_ready", "app_live_ready",
                                                 "runtime_adapter_dry_run_ready", "runtime_adapter_live_ready",
                                                 "combined_dry_run_ready", "combined_live_ready"})
        self.assertTrue(all(type(value) is bool for value in service.health().values()))
        unverified = PrimaryProviderActivationService(store=store, bindings=bindings, required_providers=("openai", "claude"),
                                                      clock=lambda: NOW)
        self.assertFalse(unverified.capture_attestations()["openai"]["surfaces"]["app"]["capture_verified"])

    @contextmanager
    def capture_sources(self, store, output=b'{"type":"result","result":"ok"}', verifier_present=True):
        writes = Mock(side_effect=AssertionError("activation store append reached"))

        @contextmanager
        def open_store(*args, **kwargs):
            yield SimpleNamespace(load=store.load, append_authorized=writes)

        class Reader:
            def __init__(self, dsn):
                self.dsn = dsn

            def load_capture(self, digest):
                return ({}, b"{}", output, False, 0, "succeeded")

        verifier = SimpleNamespace(store=SimpleNamespace(dsn="postgresql://fixture"),
                                   verify=lambda *_args, **_kwargs: True) if verifier_present else None
        with patch("director_orchestrator.provider_validation.capture_verifier_from_environment", return_value=verifier), \
                patch("director_orchestrator.primary_provider_evidence._BoundedCaptureReader", Reader), \
                patch("director_orchestrator.langgraph_postgres.open_provider_activation_store", open_store), \
                patch.dict(os.environ, {"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": PIN}):
            yield writes

    def test_activation_capture_report_is_load_only_and_names_products(self):
        store = MemoryStore()
        bindings = {"claude": binding("claude")}
        for surface in ProviderActivationSurface:
            advance(store, bindings["claude"], surface)
        settings = SimpleNamespace(langgraph_primary_postgres_dsn="postgresql://fixture",
                                   langgraph_primary_allow_insecure_local_postgres=True,
                                   primary_provider_activation_bindings=lambda: bindings,
                                   langgraph_primary_required_providers=("claude",))
        identity = SimpleNamespace(candidate_commit="a" * 40, candidate_tree="b" * 40, postgres_schema="director_primary_v1",
                                   database_identity="postgres:sha256:" + "c" * 64, database_instance_id=str(uuid.uuid4()))
        with self.capture_sources(store) as writes:
            report = evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})
        writes.assert_not_called()
        self.assertEqual(report["version"], "director-activation-captures/v1")
        self.assertEqual(report["generation_manifest_sha256"], PIN)
        self.assertEqual(report["provider_bindings_sha256"], "d" * 64)
        self.assertEqual(report["lanes"]["claude"]["product"], "anthropic-claude")
        self.assertTrue(report["lanes"]["claude"]["surfaces"]["runtime_adapter"]["capture_verified"])
        with self.capture_sources(store, output=b'{"type":"assistant"}'):
            self.assertIsNone(evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})
                              ["lanes"]["claude"]["product"])
        with self.capture_sources(store, verifier_present=False), self.assertRaises(ActivationEvidenceError):
            evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})

    def run_main(self, *argv):
        settings = SimpleNamespace(langgraph_primary_postgres_dsn="postgresql://fixture",
                                   langgraph_primary_postgres_schema="director_primary_v1",
                                   langgraph_primary_allow_insecure_local_postgres=True,
                                   langgraph_primary_pinned_commit_sha="a" * 40, langgraph_primary_pinned_tree_sha="b" * 40,
                                   app_env="local", langgraph_primary_checkpoint_namespace="default-director-primary-v1")
        manifest = {"provider_bindings_sha256": "d" * 64}
        printed = io.StringIO()
        with patch.object(sys, "argv", ["primary_generation", *argv]), \
                patch("director_orchestrator.config.Settings", return_value=settings), \
                patch("director_orchestrator.langgraph_postgres.database_binding",
                      return_value=("postgres:sha256:" + "c" * 64, str(uuid.uuid4()))), \
                patch("director_orchestrator.primary_generation.require_generation", return_value=manifest) as generation, \
                patch.object(evidence, "consistency_report", return_value={"version": "consistency", "n": 1}) as consistency, \
                patch.object(evidence, "activation_capture_report", return_value={"version": "captures"}) as captures, \
                patch.dict(os.environ, {"DIRECTOR_COMPOSE_PROJECT_NAME": "edge-engine-director"}), redirect_stdout(printed):
            from director_orchestrator import primary_generation
            primary_generation.main()
        return printed.getvalue(), generation, consistency, captures, manifest

    def test_main_dispatches_read_only_commands_without_reserving(self):
        output, generation, consistency, captures, _ = self.run_main("consistency", "--task", "DIR-LG-AB12", "--task", "DIR-LG-CD34")
        self.assertEqual(output.count("\n"), 1)
        self.assertEqual(json.loads(output), {"version": "consistency", "n": 1})
        self.assertEqual(consistency.call_args.kwargs["tasks"], ("DIR-LG-AB12", "DIR-LG-CD34"))
        self.assertIs(generation.call_args.kwargs["reserve"], False)
        captures.assert_not_called()
        output, generation, consistency, captures, manifest = self.run_main("activation-captures")
        self.assertEqual(json.loads(output), {"version": "captures"})
        self.assertIs(captures.call_args.args[2], manifest)
        self.assertIs(generation.call_args.kwargs["reserve"], False)
        consistency.assert_not_called()

    def test_main_refuses_task_selection_outside_consistency(self):
        for argv in (("verify", "--task", "DIR-LG-AB12"), ("activation-captures", "--task", "DIR-LG-AB12"),
                     ("consistency", "--task", "DIR-LG-ab12"), ("consistency", "--task", "task-1"),
                     ("consistency", *[part for index in range(13) for part in ("--task", f"DIR-LG-{index:X}")])):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                self.run_main(*argv)
            self.assertEqual(raised.exception.code, 2)


class ScorecardEvidencePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests")
        cls.coordinator_fixtures = _load("test_primary_coordinator")

    def setUp(self) -> None:
        from director_orchestrator.langgraph_postgres import open_postgres_control_plane
        from director_orchestrator.primary_coordinator import PrimaryTaskCoordinator
        self.schema = f"director_scorecard_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(TEST_DSN, schema=self.schema, allow_insecure_local_transport=True)
        self.coordinator = PrimaryTaskCoordinator(self.handle, self.coordinator_fixtures.handlers(),
                                                  owner_token="default-director-owner",
                                                  checkpoint_namespace="default-director-primary-v1")
        self.settings = SimpleNamespace(langgraph_primary_postgres_dsn=TEST_DSN,
                                        langgraph_primary_postgres_schema=self.schema,
                                        langgraph_primary_acceptance_mode="provider-v2")
        self.identity = SimpleNamespace(candidate_commit="a" * 40, candidate_tree="b" * 40)

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    def report(self, tasks=()):
        with patch.dict(os.environ, {"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": PIN}):
            return evidence.consistency_report(self.settings, self.identity, tasks=tasks)

    def intake(self):
        self.coordinator.activate_new_intake(expected_generation=0)
        task_id = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        self.coordinator.create_intake(self.coordinator_fixtures.request(task_id))
        return task_id

    def test_counts_zero_then_digest_conflict(self):
        task_id = self.intake()
        report = self.report()
        self.assertEqual(report["version"], "director-primary-consistency/v1")
        self.assertEqual(report["generation"], {"manifest_sha256": PIN, "acceptance_mode": "provider-v2",
                                                "candidate_commit": "a" * 40, "candidate_tree": "b" * 40, "adoption": None})
        self.assertEqual(report["intake_fencing_generation"], 1)
        self.assertEqual(report["counts"]["graph_task_count"], 1)
        for key in ("unresolved_reconciliation", "ambiguous_assignments", "tasks_requiring_reconciliation",
                    "terminal_tasks_open_assignments", "terminal_tasks_open_effects",
                    "projection_batch_digest_conflicts", "projections_ahead_of_fence"):
            self.assertEqual(report["counts"][key], 0, key)
        self.assertTrue(all(type(value) is int for value in report["counts"].values()))
        json.dumps(report, allow_nan=False)
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_parallel_batch"
                "(task_id, plan_digest, owner_token, fencing_generation, max_concurrency)"
                " VALUES (%s, %s, 'default-director-owner', 1, 1)", (task_id, "sha256:" + "1" * 64))
            self.assertEqual(self.report()["counts"]["batches_pending_projection_binding"], 1)
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = jsonb_set(projection, '{parallel_plan_digest}', to_jsonb(%s::text)) WHERE task_id = %s",
                ("sha256:" + "2" * 64, task_id))
        conflicted = self.report()["counts"]
        self.assertEqual(conflicted["projection_batch_digest_conflicts"], 1)
        self.assertEqual(conflicted["batches_pending_projection_binding"], 0)

    def test_task_settlement_rows(self):
        task_id = self.intake()
        budget = {"task_id": task_id, "project_id": "fixture-project", "day": "2026-09-12",
                  "daily_limit_microusd": 5000000}
        metering = {"request_id": "req_fixture", "response_id": "resp_fixture",
                    "cost_basis": "signed-tariff-metered-not-invoice"}
        with self.handle.pool.connection() as connection:
            connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest, document)"
                               " VALUES ('grant-fixture', %s::jsonb)", (json.dumps(budget),))
            for attempt, reserved in (("sha256:" + "a" * 64, 500000), ("sha256:" + "b" * 64, 200000)):
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_provider_spend(attempt_digest, grant_digest, provider_id,"
                    " project_id, task_id, budget_day, reserved_microusd, token_limit)"
                    " VALUES (%s, 'grant-fixture', 'openai', 'fixture-project', %s, '2026-09-12', %s, 1000)",
                    (attempt, task_id, reserved))
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_usage(attempt_digest, document, actual_microusd,"
                " observed_tokens) VALUES (%s, %s::jsonb, 300000, 100)",
                ("sha256:" + "a" * 64, json.dumps({"provider_id": "openai", "metering_evidence": metering})))
        entry = self.report(tasks=[task_id, "DIR-LG-0"])["tasks"]
        self.assertEqual(entry["DIR-LG-0"], {"usage_tables_present": True, "spend": [], "budgets": []})
        rows = {row["attempt_digest"]: row for row in entry[task_id]["spend"]}
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows["sha256:" + "a" * 64]["actual_microusd"], rows["sha256:" + "a" * 64]["request_correlated"]),
                         (300000, True))
        self.assertEqual((rows["sha256:" + "b" * 64]["actual_microusd"], rows["sha256:" + "b" * 64]["request_correlated"]),
                         (None, False))
        self.assertEqual(rows["sha256:" + "a" * 64]["budget_day"], "2026-09-12")
        self.assertEqual(entry[task_id]["budgets"], [{"task_id": task_id, "project_id": "fixture-project",
                                                       "day": "2026-09-12", "daily_limit_microusd": 5000000,
                                                       "day_held_microusd": 700000}])
        json.dumps(entry, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:00 UTC | END]]
