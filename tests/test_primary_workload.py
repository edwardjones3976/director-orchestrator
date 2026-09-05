# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
"""Disposable fixture history is test data, never activation/bootstrap evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
import unittest
import uuid
from unittest.mock import Mock, patch

from director_orchestrator.langgraph_postgres import (
    PostgresCheckpointConfigurationError, open_postgres_control_plane,
)
from director_orchestrator.primary_parallel import (
    ParallelAssignment, ParallelAssignmentStatus, ParallelDispatchError,
    ParallelDispatchPolicy, PostgresParallelDispatch,
)
from director_orchestrator.primary_workload import (
    POLICY_DIGEST, POLICY_VERSION, WorkloadAdmissionHold, admission_reason, admit_assignment,
)
from director_orchestrator.langgraph_primary import PrimaryWorkflowRuntime

DIGEST = "sha256:" + "a" * 64
# Last nineteen: five OpenAI, five Claude, four Cursor, three Gemini, one Grok,
# one Perplexity. The next OpenAI fits exactly at its 30% ceiling.
HISTORY = ["claude"] * 5 + ["cursor"] * 4 + ["antigravity"] * 3 + ["grok", "perplexity"] + ["openai"] * 5


class AdmissionPolicyTests(unittest.TestCase):
    def test_bootstrap_is_explicit_and_aliases_do_not_invent_credit(self):
        self.assertIsNone(admission_reason("openai", []))
        for lane in ("firecrawl", "github-copilot", "microsoft-365-copilot"):
            self.assertEqual(admission_reason(lane, HISTORY),
                             "workload_unsupported_allocation_lane")

    def test_admission_rejects_noncanonical_schema_before_database_access(self):
        for schema in ("public", "director", "director_v", "director_v1234567",
                       "a" * 42 + "_v1", "director_v1;DROP TABLE x", None):
            with self.subTest(schema=schema):
                connection = Mock()
                with self.assertRaises(PostgresCheckpointConfigurationError):
                    admit_assignment(connection, schema=schema, task_id="task",
                        assignment_id="assignment", provider="openai",
                        reservation_digest=DIGEST, fencing_generation=1)
                connection.cursor.assert_not_called()

    def test_exact_ceiling_and_rollover(self):
        self.assertIsNone(admission_reason("openai", HISTORY))
        self.assertEqual(admission_reason("openai", HISTORY + ["openai"]),
                         "workload_assignment_hard_limit")
        self.assertIsNone(admission_reason("grok", HISTORY + ["openai"]))
        self.assertIsNone(admission_reason("cursor", ["claude"] * 19))

    def test_graph_policy_hold_has_no_synthetic_provider_result(self):
        runtime = object.__new__(PrimaryWorkflowRuntime)
        runtime._graph = Mock()
        runtime._config = Mock(return_value={})
        runtime._outcome = Mock(return_value="owner-hold")
        runtime._snapshot = Mock(return_value=Mock(values={
            "parallel_aggregate_digest": DIGEST, "parallel_terminal": True,
            "parallel_succeeded": False, "parallel_ambiguous_slot_count": 0,
        }))
        result = runtime.record_parallel_workload_hold("task", aggregate_digest=DIGEST,
            reason_code="workload_bootstrap_authority_required")
        self.assertEqual(result, "owner-hold")
        self.assertEqual(runtime._graph.update_state.call_args.args[1], {
            "terminal_classification": "OWNER_DECISION_REQUIRED",
            "transition_reason_code": "workload_bootstrap_authority_required",
        })
        runtime._snapshot.return_value.values["parallel_ambiguous_slot_count"] = 1
        with self.assertRaises(ValueError):
            runtime.record_parallel_workload_hold("task", aggregate_digest=DIGEST,
                reason_code="workload_bootstrap_authority_required")


class PostgresAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "")
        if not cls.dsn:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required")

    def setUp(self):
        self.schema = "director_workload_it_" + uuid.uuid4().hex[:8] + "_v1"
        self.handle = open_postgres_control_plane(
            self.dsn, schema=self.schema, allow_insecure_local_transport=True)
        self.parallel = PostgresParallelDispatch(self.handle.claim_store)
        self.policy = ParallelDispatchPolicy()

    def tearDown(self):
        with self.handle.claim_store._pool.connection() as connection:
            connection.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.handle.close()

    def seed_test_history(self, target=None, *, capped=False):
        target = target or self
        providers = (["openai"] * 6 + ["claude"] * 6 + ["cursor"] * 5 + ["grok"] * 2) if capped else HISTORY
        with target.handle.claim_store._pool.connection() as connection:
            with connection.transaction():
                for index, provider in enumerate(providers):
                    connection.execute(f"""INSERT INTO {target.schema}.director_primary_workload_assignment
                        (task_id, assignment_id, provider_lane, reservation_digest,
                         fencing_generation, policy_version, policy_digest) VALUES (%s,%s,%s,%s,1,%s,%s)""",
                        ("test-fixture", str(index), provider, DIGEST, POLICY_VERSION, POLICY_DIGEST))

    def task(self, provider="openai"):
        task_id = uuid.uuid4().hex
        writer = self.handle.claim_store.acquire_task_writer(
            task_id, engine_owner="langgraph", owner_token="test-owner",
            expected_generation=0)
        self.parallel.register_plan(task_id=task_id, writer=writer,
            plan_digest=DIGEST, assignments=(ParallelAssignment(
                "assignment", 1, provider, "test-deployment"),), policy=self.policy)
        return task_id, writer

    def ready(self, task):
        task_id, writer = task
        return self.parallel.reserve_ready(task_id=task_id, writer=writer,
                                           policy=self.policy)[0]

    def attempt(self, reservation, writer, ordinal=1):
        return self.parallel.reserve_provider_attempt(reservation=reservation,
            writer=writer, attempt_ordinal=ordinal, format_roster=(("json", "api"),),
            idempotency_key_digest="sha256:" + str(ordinal) * 64,
            budget_evidence_digest=DIGEST)

    def count(self, table):
        with self.handle.claim_store._pool.connection() as connection:
            return connection.execute(f"SELECT count(*) AS n FROM {self.schema}.{table}").fetchone()["n"]

    def test_startup_admits_one_real_assignment_without_synthetic_history(self):
        task = self.task()
        reservation = self.ready(task)
        self.attempt(reservation, task[1])
        self.assertEqual(self.count("director_primary_provider_attempt"), 1)
        self.assertEqual(self.count("director_primary_workload_assignment"), 1)

    def test_owner_hold_reaches_real_graph_without_success_evidence(self):
        from tests.test_primary_default import DefaultPrimaryApplicationIntegrationTests
        from director_orchestrator.primary_default import IntakeAuthentication
        fixture = DefaultPrimaryApplicationIntegrationTests(methodName="runTest")
        fixture.setUp()
        try:
            self.seed_test_history(fixture, capped=True)
            record = fixture.application.create_intake(fixture.intake(),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
            def dispatcher(reservation):
                fixture.coordinator.reserve_provider_attempt(
                    reservation=reservation, attempt_ordinal=1,
                    format_roster=(("json", "api"),),
                    idempotency_key_digest=DIGEST, budget_evidence_digest=DIGEST)
                self.fail("provider must not be invoked")
            fixture.application._parallel_dispatcher = dispatcher
            aggregate = fixture.application._drive_parallel_lifecycle(record)
            self.assertTrue(aggregate.workload_hold_reasons)
            projection = fixture.coordinator.get_projection(record.task_id)
            self.assertEqual(projection.projection["terminal_classification"],
                             "OWNER_DECISION_REQUIRED")
            self.assertIsNone(projection.projection.get("provider_result_digest"))
        finally:
            fixture.tearDown()

    def test_two_tasks_compete_atomically_for_last_slot(self):
        self.seed_test_history()
        tasks = [self.task(), self.task()]
        reservations = [self.ready(task) for task in tasks]
        def reserve(index):
            try:
                self.attempt(reservations[index], tasks[index][1])
                return "admitted"
            except WorkloadAdmissionHold as hold:
                return hold.reason_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(reserve, range(2)))
        self.assertCountEqual(outcomes, ["admitted", "workload_assignment_hard_limit"])
        self.assertEqual(self.count("director_primary_provider_attempt"), 1)
        self.assertEqual(self.count("director_primary_workload_assignment"), 20)

    def test_reverse_sequence_hold_propagates_to_real_graph(self):
        from tests.test_primary_default import DefaultPrimaryApplicationIntegrationTests
        from director_orchestrator.primary_default import IntakeAuthentication
        fixture = DefaultPrimaryApplicationIntegrationTests(methodName="runTest")
        fixture.setUp()
        try:
            self.seed_test_history(fixture, capped=True)
            assignments = (
                ParallelAssignment("leaf", 1, "cursor", "test", ("middle",)),
                ParallelAssignment("middle", 2, "claude", "test", ("root",)),
                ParallelAssignment("root", 3, "openai", "test"),
            )
            with patch.object(fixture.application, "_parallel_assignments",
                              return_value=assignments), patch.object(
                    fixture.application, "_parallel_policy", return_value=self.policy):
                record = fixture.application.create_intake(fixture.intake(),
                    intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
                attempted = []
                def dispatcher(reservation):
                    attempted.append(reservation.assignment_id)
                    fixture.coordinator.reserve_provider_attempt(
                        reservation=reservation, attempt_ordinal=1,
                        format_roster=(("json", "api"),),
                        idempotency_key_digest=DIGEST, budget_evidence_digest=DIGEST)
                    self.fail("provider must not be invoked")
                fixture.application._parallel_dispatcher = dispatcher
                aggregate = fixture.application._drive_parallel_lifecycle(record)
                self.assertEqual(attempted, ["root"])
                self.assertEqual(aggregate.statuses, {
                    "leaf": "BLOCKED", "middle": "BLOCKED",
                    "root": "OWNER_DECISION_REQUIRED",
                })
                self.assertTrue(aggregate.terminal)
                self.assertFalse(aggregate.succeeded)
                self.assertEqual(aggregate.ambiguous_slot_count, 0)
                projection = fixture.coordinator.get_projection(record.task_id)
                self.assertEqual(projection.projection["terminal_classification"],
                                 "OWNER_DECISION_REQUIRED")
                self.assertIsNone(projection.projection.get("provider_result_digest"))
                with fixture.handle.claim_store._pool.connection() as connection:
                    row = connection.execute(f"SELECT count(*) AS n FROM {fixture.schema}.director_primary_provider_attempt").fetchone()
                    self.assertEqual(row["n"], 0)
        finally:
            fixture.tearDown()

    def test_retry_restart_keeps_single_assignment_and_ambiguity_blocks(self):
        self.seed_test_history()
        task = self.task()
        reservation = self.ready(task)
        first = self.attempt(reservation, task[1])
        self.parallel.record_provider_attempt(attempt=first, writer=task[1],
            status=ParallelAssignmentStatus.FAILED, result_digest=DIGEST,
            reason_code="authenticated_failure")
        self.parallel = PostgresParallelDispatch(self.handle.claim_store)
        second = self.attempt(reservation, task[1], 2)
        self.assertEqual(self.count("director_primary_workload_assignment"), 20)
        self.parallel.record_provider_attempt(attempt=second, writer=task[1],
            status=ParallelAssignmentStatus.AMBIGUOUS, result_digest=None,
            reason_code="provider_outcome_unknown")
        with self.assertRaisesRegex(ParallelDispatchError, "reconcile"):
            self.attempt(reservation, task[1], 3)

    def test_stale_fence_and_binding_cannot_admit(self):
        self.seed_test_history()
        task = self.task()
        reservation = self.ready(task)
        with self.assertRaises(ParallelDispatchError):
            self.attempt(reservation, replace(task[1], fencing_generation=999))
        with self.assertRaisesRegex(ParallelDispatchError, "binding"):
            self.attempt(replace(reservation, provider_id="claude"), task[1])
        self.assertEqual(self.count("director_primary_workload_assignment"), 19)

    def test_attempt_insert_failure_rolls_back_admission(self):
        self.seed_test_history()
        task = self.task()
        reservation = self.ready(task)
        # A database-level failure after admission proves both writes share a transaction.
        with self.handle.claim_store._pool.connection() as connection:
            connection.execute(f"""ALTER TABLE {self.schema}.director_primary_provider_attempt
                ADD CONSTRAINT test_fail_insert CHECK (attempt_ordinal < 0)""")
        with self.assertRaises(Exception):
            self.attempt(reservation, task[1])
        self.assertEqual(self.count("director_primary_workload_assignment"), 19)
        self.assertEqual(self.count("director_primary_provider_attempt"), 0)

    def test_history_is_append_only(self):
        self.seed_test_history()
        with self.assertRaises(Exception):
            with self.handle.claim_store._pool.connection() as connection:
                connection.execute(f"DELETE FROM {self.schema}.director_primary_workload_assignment")
        self.assertEqual(self.count("director_primary_workload_assignment"), 19)
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:18 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
