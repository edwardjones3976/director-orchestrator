# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 20:48 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:32 UTC | BEGIN]]
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
        codex = (b'{"type":"thread.started","thread_id":"t-1"}\n{"type":"turn.started"}\n'
                 b'{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}\n{"type":"turn.completed"}')
        cases = (("openai", b'{"object":"response","id":"resp_1","model":"gpt-5.6"}', "openai-responses"),
                 ("openai", b'{"object":"response","id":"resp_1","model":"o4-mini"}', "openai-responses"),
                 # Substitutions: another vendor's model, or no model, on the OpenAI Responses route.
                 ("openai", b'{"object":"response","id":"resp_1","model":"grok-4"}', None),
                 ("openai", b'{"object":"response","id":"resp_1"}', None),
                 ("openai", codex, "openai-codex"),
                 ("openai", b'{"type":"thread.started"}\n{"type":"turn.completed"}', "openai-codex"),
                 ("openai", b'{"type":"thread.started"}\n{"type":"message"}\n{"type":"turn.completed"}', None),
                 ("openai", b'{"type":"turn.completed"}', None),
                 ("openai", b'{"type":"message"}', None),
                 ("openai", b'{"type":"result","result":"ok"}', None),
                 ("grok", b'{"object":"response","model":"grok-4"}', "xai-grok-responses"),
                 ("grok", b'{"object":"response","model":"gpt-5"}', None),
                 ("grok", b'{"object":"response"}', None),
                 ("grok", b'{"stopReason":"end_turn","num_turns":1,"sessionId":"s-1"}', None),
                 ("grok", b'{"stopReason":"end_turn","num_turns":1,"model":"xai/grok-4"}', "xai-grok-cli"),
                 ("grok", b'{"stopReason":"end_turn","num_turns":1,"model":"gpt-5"}', None),
                 ("grok", b'{"stopReason":"end_turn","num_turns":"1"}', None),
                 ("grok", b'{"result":"done"}', None),
                 ("grok", b'{"type":"result","subtype":"success"}', None),
                 ("grok", b'[1, 2]', None),
                 ("grok", b'not json', None),
                 ("claude", b'{"type":"result","result":"ok"}', None),
                 ("claude", b'{"type":"result","modelUsage":{"claude-opus-5-5":{},"gpt-5":{}}}', None),
                 ("claude", b'{"type":"result","modelUsage":{"claude-opus-5-5":{}}}', "anthropic-claude"),
                 ("claude", b'{"type":"result","modelUsage":{"gpt-5":{}}}', None),
                 ("claude", b'{"type":"result","modelUsage":{}}', None),
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
            self.assertEqual(surface["observed_at"], (NOW + timedelta(seconds=len(ProviderActivationStage))).isoformat())
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
    def capture_sources(self, store, output=b'{"type":"result","modelUsage":{"claude-opus-5-5":{}}}',
                        verifier_present=True):
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
        with self.capture_sources(store) as writes, \
                patch.object(evidence, "_activation_recorded_at", return_value="2026-09-12T09:30:01+00:00") as recorded:
            report = evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})
        writes.assert_not_called()
        self.assertEqual(report["lanes"]["claude"]["surfaces"]["runtime_adapter"]["recorded_at"],
                         "2026-09-12T09:30:01+00:00")
        self.assertEqual(recorded.call_args.args[1:3], ("director_primary_v1", "claude"))
        self.assertEqual(report["version"], "director-activation-captures/v1")
        self.assertEqual(report["generation_manifest_sha256"], PIN)
        self.assertEqual(report["provider_bindings_sha256"], "d" * 64)
        self.assertEqual(report["lanes"]["claude"]["product"], "anthropic-claude")
        self.assertTrue(report["lanes"]["claude"]["surfaces"]["runtime_adapter"]["capture_verified"])
        with self.capture_sources(store, output=b'{"type":"assistant"}'), \
                patch.object(evidence, "_activation_recorded_at", return_value=None):
            self.assertIsNone(evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})
                              ["lanes"]["claude"]["product"])
        with self.capture_sources(store, verifier_present=False), self.assertRaises(ActivationEvidenceError), \
                patch.object(evidence, "_activation_recorded_at", return_value=None):
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

    def plan(self, task_id, terminal, assignments):
        """A batch bound to the projection, its assignments, and the projection's terminal classification."""
        digest = "sha256:" + "3" * 64
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_parallel_batch"
                "(task_id, plan_digest, owner_token, fencing_generation, max_concurrency)"
                " VALUES (%s, %s, 'default-director-owner', 1, 4)", (task_id, digest))
            for sequence, (assignment_id, status, dependencies) in enumerate(assignments, 1):
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_parallel_assignment(task_id, assignment_id, sequence,"
                    " provider_id, deployment_id, dependencies, write_surfaces, retry_limit, status)"
                    " VALUES (%s, %s, %s, 'claude', 'fixture', %s::jsonb, '[]'::jsonb, 3, %s)",
                    (task_id, assignment_id, sequence, json.dumps(dependencies), status))
            self.classify(connection, task_id, terminal, digest)

    def classify(self, connection, task_id, terminal, digest=None):
        projection = "jsonb_set(projection, '{terminal_classification}', to_jsonb(%s::text))"
        values = [terminal]
        if digest is not None:
            projection = f"jsonb_set({projection}, '{{parallel_plan_digest}}', to_jsonb(%s::text))"
            values.append(digest)
        connection.execute(
            f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
            f" projection = {projection} WHERE task_id = %s", (*values, task_id))

    def test_blocked_dependents_and_owner_holds_are_not_open_terminal_work(self):
        task_id = self.intake()
        # A failed prerequisite leaves its dependency chain PENDING in storage; the aggregator projects it BLOCKED.
        self.plan(task_id, "FAILED", [("a1", "FAILED", []), ("a2", "PENDING", ["a1"]), ("a3", "PENDING", ["a2"]),
                                      ("a4", "PENDING", [])])
        counts = self.report()["counts"]
        # Only a4, which nothing blocks, is open work on a terminal task.
        self.assertEqual((counts["terminal_tasks_open_assignments"], counts["active_assignments"]), (1, 0))
        # An OWNER_DECISION_REQUIRED hold with no pending approval cannot resume: it is settled residue.
        with self.handle.pool.connection() as connection:
            self.classify(connection, task_id, "OWNER_DECISION_REQUIRED")
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = projection - 'pending_approval' WHERE task_id = %s", (task_id,))
        counts = self.report()["counts"]
        self.assertEqual((counts["terminal_tasks_open_assignments"], counts["active_assignments"]), (1, 0))
        # With a pending approval it is a resumable hold: in flight, and its blocked chain is not active work.
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = jsonb_set(projection, '{pending_approval}', %s::jsonb) WHERE task_id = %s",
                (json.dumps({"request_digest": "sha256:" + "4" * 64}), task_id))
        counts = self.report()["counts"]
        self.assertEqual((counts["terminal_tasks_open_assignments"], counts["active_assignments"]), (0, 1))
        with self.handle.pool.connection() as connection:
            connection.execute(f"DELETE FROM {self.schema}.director_primary_parallel_assignment"
                               " WHERE task_id = %s AND assignment_id = 'a4'", (task_id,))
            self.classify(connection, task_id, "DONE")
        self.assertEqual(self.report()["counts"]["terminal_tasks_open_assignments"], 0)

    def test_contradictory_projection_states_are_counted(self):
        task_id = self.intake()
        # SUCCEEDED over a FAILED assignment contradicts the authoritative assignment outcome.
        self.plan(task_id, "SUCCEEDED", [("a1", "SUCCEEDED", []), ("a2", "FAILED", [])])
        self.assertEqual(self.report()["counts"]["succeeded_projections_with_unsucceeded_assignments"], 1)
        other = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        self.coordinator.create_intake(self.coordinator_fixtures.request(other))
        # A projection naming a plan digest whose batch row is absent.
        with self.handle.pool.connection() as connection:
            self.classify(connection, other, "SUCCEEDED", "sha256:" + "5" * 64)
        counts = self.report()["counts"]
        self.assertEqual(counts["projections_missing_batch"], 1)
        self.assertEqual(counts["batches_without_projection"], 0)

    def test_owner_held_assignment_with_pending_approval_is_in_flight(self):
        task_id = self.intake()
        # The held assignment is not PENDING/ACTIVE and its dependent is BLOCKED, so only the hold shows it.
        self.plan(task_id, "OWNER_DECISION_REQUIRED", [("a1", "OWNER_DECISION_REQUIRED", []), ("a2", "PENDING", ["a1"])])
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = jsonb_set(projection, '{pending_approval}', %s::jsonb) WHERE task_id = %s",
                (json.dumps({"request_digest": "sha256:" + "4" * 64}), task_id))
        counts = self.report()["counts"]
        self.assertEqual((counts["active_assignments"], counts["open_effect_reservations"],
                          counts["batches_pending_projection_binding"], counts["pending_owner_decisions"]), (0, 0, 0, 1))
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = projection - 'pending_approval' WHERE task_id = %s", (task_id,))
        self.assertEqual(self.report()["counts"]["pending_owner_decisions"], 0)

    def test_every_residue_count_is_driven_by_real_rows(self):
        task_id = self.intake()
        other = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        self.coordinator.create_intake(self.coordinator_fixtures.request(other))
        s = self.schema
        with self.handle.pool.connection() as connection:
            for claim in ("effect-1", "reconcile-1"):
                connection.execute(
                    f"INSERT INTO {s}.director_primary_claim(claim_key, task_id, field, expected_state, target_state,"
                    " attempt_digest, owner_token, fencing_generation)"
                    " VALUES (%s, %s, 'effect', 'NOT_STARTED', 'COMPLETED', 'fixture', 'default-director-owner', 1)",
                    (claim, task_id))
            connection.execute(
                f"INSERT INTO {s}.director_primary_effect_reservation(reservation_key, claim_key, task_id, owner_token,"
                " fencing_generation, effect_key_digest, effect_attempt_digest, writer_owner_id, reserved_units)"
                " VALUES ('reservation-1', 'effect-1', %s, 'default-director-owner', 1, 'k', 'a', 'w', 0)", (task_id,))
            connection.execute(
                f"INSERT INTO {s}.director_primary_reconciliation_event(claim_key, task_id, owner_token,"
                " fencing_generation, state, evidence_digest)"
                " VALUES ('reconcile-1', %s, 'default-director-owner', 1, 'AMBIGUOUS', 'e')", (task_id,))
            connection.execute(
                f"INSERT INTO {s}.director_primary_task_writer(task_id, engine_owner, owner_token, fencing_generation,"
                " lease_expires_at) VALUES ('DIR-LG-LEASE', 'langgraph', 'default-director-owner', 1,"
                " now() + interval '1 hour'), ('DIR-LG-LAPSED', 'langgraph', 'default-director-owner', 1,"
                " now() - interval '1 hour')")
        live = self.report()["counts"]
        self.assertEqual((live["open_effect_reservations"], live["terminal_tasks_open_effects"],
                          live["unresolved_reconciliation"]), (1, 0, 1))
        self.assertGreaterEqual(live["live_writer_claims"], 1)
        # Settle the task: its unfinished effect and its unblocked assignments become terminal residue.
        # a2 waits on a SUCCEEDED prerequisite, so nothing blocks it.
        self.plan(task_id, "FAILED", [("a1", "SUCCEEDED", []), ("a2", "PENDING", ["a1"]), ("a3", "AMBIGUOUS", [])])
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {s}.director_primary_parallel_batch(task_id, plan_digest, owner_token, fencing_generation,"
                " max_concurrency) VALUES (%s, %s, 'default-director-owner', 1, 1)", (other, "sha256:" + "5" * 64))
            self.classify(connection, other, "RECONCILIATION_REQUIRED")
            # The projection guard forbids this state; lift it in the disposable schema to prove the count sees it.
            connection.execute(f"ALTER TABLE {s}.director_primary_task_projection DISABLE TRIGGER USER")
            connection.execute(f"UPDATE {s}.director_primary_task_projection SET intake_fencing_generation = 99"
                               " WHERE task_id = %s", (other,))
        counts = self.report()["counts"]
        self.assertEqual({key: counts[key] for key in (
            "unresolved_reconciliation", "ambiguous_assignments", "tasks_requiring_reconciliation",
            "terminal_tasks_open_assignments", "terminal_tasks_open_effects", "terminal_batches_unbound",
            "projections_ahead_of_fence", "open_effect_reservations", "batches_pending_projection_binding")},
            {"unresolved_reconciliation": 1, "ambiguous_assignments": 1, "tasks_requiring_reconciliation": 1,
             "terminal_tasks_open_assignments": 2, "terminal_tasks_open_effects": 1, "terminal_batches_unbound": 1,
             "projections_ahead_of_fence": 1, "open_effect_reservations": 0, "batches_pending_projection_binding": 0})
        self.assertEqual(counts["live_writer_claims"], live["live_writer_claims"])

    def test_snapshot_connection_refuses_writes(self):
        import psycopg
        self.intake()
        write = ("INSERT INTO {s}.director_primary_task_writer(task_id, engine_owner, owner_token, fencing_generation,"
                 " lease_expires_at) VALUES ('DIR-LG-W', 'langgraph', 'x', 1, now()) RETURNING now() AS captured_at")
        with patch.object(evidence, "_COUNTS", write), self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            self.report()

    def test_approval_command_receipts_leave_only_a_command_digest(self):
        task_id = self.intake()
        with self.handle.pool.connection() as connection:
            for operation, command in (("approval_resume", "owner-command-1"), ("cancel", "owner-command-2")):
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_command_receipt(task_id, operation, command_id,"
                    " command_digest, resulting_projection_digest, resulting_projection_version,"
                    " writer_fencing_generation) VALUES (%s, %s, %s, %s, %s, 2, 1)",
                    (task_id, operation, command, "sha256:" + "6" * 64, "sha256:" + "7" * 64))
        receipts = self.report(tasks=[task_id])["tasks"][task_id]["approval_receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["command_id_sha256"], hashlib.sha256(b"owner-command-1").hexdigest())
        self.assertEqual((receipts[0]["command_digest"], receipts[0]["resulting_projection_digest"]),
                         ("sha256:" + "6" * 64, "sha256:" + "7" * 64))
        self.assertNotIn("owner-command-1", json.dumps(receipts))
        datetime.fromisoformat(receipts[0]["recorded_at"])
        # No durable command authorization joins it yet: not authorized.
        self.assertEqual((receipts[0]["authorized"], receipts[0]["proof_version"], receipts[0]["actor_id"]),
                         (False, None, None))
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_command_authorization(nonce_digest, task_id, operation,"
                " command_id, command_digest, proof_version, actor_id, writer_fencing_generation, proof_expires_at)"
                " VALUES (%s, %s, 'approval_resume', 'owner-command-1', %s, 'v1', 'UOWNER1', 1, now())",
                ("sha256:" + "9" * 64, task_id, "sha256:" + "6" * 64))
        joined = self.report(tasks=[task_id])["tasks"][task_id]["approval_receipts"][0]
        self.assertEqual({key: joined[key] for key in ("authorized", "authorized_command_digest", "authorized_fence",
                                                        "proof_version", "actor_id")},
                         {"authorized": True, "authorized_command_digest": True, "authorized_fence": True,
                          "proof_version": "v1", "actor_id": "UOWNER1"})
        # The table is append-only: a second command whose authorization names another digest and fence.
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_command_receipt(task_id, operation, command_id,"
                " command_digest, resulting_projection_digest, resulting_projection_version,"
                " writer_fencing_generation) VALUES (%s, 'approval_resume', 'owner-command-3', %s, %s, 3, 1)",
                (task_id, "sha256:" + "6" * 64, "sha256:" + "7" * 64))
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_command_authorization(nonce_digest, task_id, operation,"
                " command_id, command_digest, proof_version, actor_id, writer_fencing_generation, proof_expires_at)"
                " VALUES (%s, %s, 'approval_resume', 'owner-command-3', %s, 'v1', 'UOWNER1', 2, now())",
                ("sha256:" + "8" * 64, task_id, "sha256:" + "0" * 64))
        rows = self.report(tasks=[task_id])["tasks"][task_id]["approval_receipts"]
        other = next(r for r in rows if r["command_id_sha256"] == hashlib.sha256(b"owner-command-3").hexdigest())
        self.assertEqual((other["authorized"], other["authorized_command_digest"], other["authorized_fence"]),
                         (True, False, False))

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
        self.assertEqual(entry["DIR-LG-0"], {"usage_tables_present": True, "spend": [], "budgets": [],
                                             "approval_receipts": [],
                                             "verifier_secrets": {"route_verifier": False, "owner_authority": False}})
        rows = {row["attempt_digest"]: row for row in entry[task_id]["spend"]}
        self.assertEqual(len(rows), 2)
        # Correlated metering text without a verified usage receipt is not actual cost.
        self.assertEqual((rows["sha256:" + "a" * 64]["actual_microusd"], rows["sha256:" + "a" * 64]["request_correlated"],
                          rows["sha256:" + "a" * 64]["usage_verified"]), (300000, False, False))
        self.assertEqual((rows["sha256:" + "b" * 64]["actual_microusd"], rows["sha256:" + "b" * 64]["request_correlated"]),
                         (None, False))
        self.assertEqual(rows["sha256:" + "a" * 64]["budget_day"], "2026-09-12")
        self.assertEqual(entry[task_id]["budgets"], [{"task_id": task_id, "project_id": "fixture-project",
                                                       "day": "2026-09-12", "daily_limit_microusd": 5000000,
                                                       "day_held_microusd": 700000, "provider_id": None,
                                                       "signature_verified": False, "authority_digest": "grant-fixture"}])
        json.dumps(entry, allow_nan=False)

    def test_budget_rows_are_verified_against_the_owner_signed_contract(self):
        import hmac
        import tempfile
        from director_orchestrator.entitlement_contracts import canonical_json_bytes
        task_id = self.intake()
        secret = "fixture-owner-authority-secret-0123456789abcdefghij"
        document = {"schema_version": "primary-provider-budget/v1", "grant_digest": "c" * 64, "provider_id": "openai",
                    "project_id": "fixture-project", "task_id": task_id, "day": "2026-09-12",
                    "task_limit_microusd": 1000000, "daily_limit_microusd": 5000000,
                    "attempt_reservation_microusd": 500000, "token_limit": 1000}
        document["owner_signature"] = hmac.new(secret.encode(), canonical_json_bytes(document), hashlib.sha256).hexdigest()
        forged = {**document, "task_id": task_id, "daily_limit_microusd": 9000000}
        misfiled = {**document, "daily_limit_microusd": 7000000}
        misfiled["owner_signature"] = hmac.new(secret.encode(), canonical_json_bytes(
            {k: v for k, v in misfiled.items() if k != "owner_signature"}), hashlib.sha256).hexdigest()
        with self.handle.pool.connection() as connection:
            # Stored under its own grant digest, under a forged copy's, and (validly signed) under another grant.
            for grant, doc in (("c" * 64, document), ("f" * 64, forged), ("e" * 64, misfiled)):
                connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest, document)"
                                   " VALUES (%s, %s::jsonb)", (grant, json.dumps(doc)))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "owner-authority-secret"
            path.write_text(secret, encoding="utf-8")
            self.settings.entitlement_owner_authority_secret_file = str(path)
            rows = self.report(tasks=[task_id])["tasks"][task_id]["budgets"]
            self.assertEqual(sorted((row["daily_limit_microusd"], row["signature_verified"], row["provider_id"])
                                    for row in rows), [(5000000, True, "openai"), (7000000, False, "openai"),
                                                       (9000000, False, "openai")])
            self.settings.entitlement_owner_authority_secret_file = str(Path(folder) / "missing")
            rows = self.report(tasks=[task_id])["tasks"][task_id]["budgets"]
            self.assertEqual({row["signature_verified"] for row in rows}, {False})

    def test_usage_receipts_are_verified_with_the_route_verifier(self):
        import hmac
        import tempfile
        from director_orchestrator.entitlement_contracts import canonical_json_bytes
        task_id = self.intake()
        secret = "fixture-route-verifier-secret-0123456789abcdefghijk"

        def receipt(attempt, grant, actual, key=secret):
            document = {"schema_version": "primary-provider-usage/v1", "attempt_digest": attempt,
                        "authority_digest": grant, "provider_id": "claude", "actual_microusd": actual,
                        "observed_tokens": 100, "complete": True}
            document["meter_signature"] = hmac.new(key.encode(), canonical_json_bytes(document), hashlib.sha256).hexdigest()
            return document

        attempts = {"sha256:" + "1" * 64: (receipt("sha256:" + "1" * 64, "c" * 64, 300000), 300000),
                    "sha256:" + "2" * 64: (receipt("sha256:" + "2" * 64, "c" * 64, 300000, key=secret[::-1]), 300000),
                    "sha256:" + "3" * 64: (receipt("sha256:" + "3" * 64, "d" * 64, 300000), 300000),
                    "sha256:" + "4" * 64: (receipt("sha256:" + "4" * 64, "c" * 64, 300000), 0)}
        with self.handle.pool.connection() as connection:
            # Spend rows reference their budget grant (foreign key); the budget document itself is not under test here.
            connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest, document)"
                               " VALUES (%s, %s::jsonb)", ("c" * 64, json.dumps({"task_id": task_id})))
            for attempt, (document, column) in attempts.items():
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_provider_spend(attempt_digest, grant_digest, provider_id,"
                    " project_id, task_id, budget_day, reserved_microusd, token_limit)"
                    " VALUES (%s, %s, 'claude', 'fixture-project', %s, '2026-09-12', 500000, 1000)",
                    (attempt, "c" * 64, task_id))
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_provider_usage(attempt_digest, document, actual_microusd,"
                    " observed_tokens) VALUES (%s, %s::jsonb, %s, 100)", (attempt, json.dumps(document), column))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "route-verifier-secret"
            path.write_text(secret, encoding="utf-8")
            self.settings.entitlement_route_verifier_secret_file = str(path)
            entry = self.report(tasks=[task_id])["tasks"][task_id]
        # Signed and bound; wrong key; another grant; cost column differs from the signed receipt.
        self.assertEqual({row["attempt_digest"][-1]: row["usage_verified"] for row in entry["spend"]},
                         {"1": True, "2": False, "3": False, "4": False})
        self.assertEqual(entry["verifier_secrets"], {"route_verifier": True, "owner_authority": False})
        self.assertEqual([row["grant_digest"] for row in entry["spend"]], ["c" * 64] * 4)

    def test_activation_recorded_at_is_the_persisted_row_time(self):
        from director_orchestrator.provider_activation import ACTIVATION_EVIDENCE_VERSION
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_provider_activation_evidence(provider_id, workspace_id,"
                " provider_version, app_version, runtime_adapter_version, workspace_version, binding_version, surface,"
                " revision, schema_version, stage, evidence_id, evidence_digest, observed_at, expires_at, recorded_at)"
                " VALUES ('claude','w','p','a','r','w','b','runtime_adapter',1,%s,'LIVE_OK','e',%s,"
                " '2020-01-01T00:00:00Z','2099-01-01T00:00:00Z','2026-09-12T10:00:10Z')",
                (ACTIVATION_EVIDENCE_VERSION, "a" * 64))
        recorded = evidence._activation_recorded_at(TEST_DSN, self.schema, "claude", "a" * 64)
        self.assertEqual(datetime.fromisoformat(recorded), datetime(2026, 9, 12, 10, 0, 10, tzinfo=timezone.utc))
        self.assertIsNone(evidence._activation_recorded_at(TEST_DSN, self.schema, "claude", "b" * 64))


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:00 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:32 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 20:48 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:58 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | END]]
