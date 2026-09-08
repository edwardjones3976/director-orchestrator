# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
"""Disposable fixture history is test data, never activation/bootstrap evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import asyncio
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

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
# Last nineteen: eight OpenAI, two Claude, four Cursor, three Antigravity,
# one Grok and one Perplexity. The next OpenAI reaches its 45% hard ceiling.
HISTORY = ["claude"] * 2 + ["cursor"] * 4 + ["antigravity"] * 3 + ["grok", "perplexity"] + ["openai"] * 8

# Immutable test snapshot from e5145abd, before the owner's prospective role swap.
PREVIOUS_ENGINEERING_TARGETS = {
    "openai": 35, "claude": 30, "cursor": 15, "grok": 10, "antigravity": 10,
    "perplexity": 0, "firecrawl": 0, "github-copilot": 0, "microsoft-365-copilot": 0,
}


def previous_engineering_digest():
    from director_orchestrator.allocation_profiles import allocation_profile_digest
    with patch("director_orchestrator.allocation_profiles.ALLOCATION_PROFILES",
               {"engineering": PREVIOUS_ENGINEERING_TARGETS}):
        return allocation_profile_digest("engineering")


class AllocationCompatibilityTests(unittest.TestCase):
    def test_prior_binding_holds_for_owner_without_rewriting_real_history(self):
        from director_orchestrator.allocation_profiles import allocation_profile_digest
        from director_orchestrator.config import Settings
        from director_orchestrator.engine import OrchestrationEngine
        from director_orchestrator.models import Assignment, AssignmentKind, ProviderName, SlackIntake, TaskRecord, TaskStatus
        from director_orchestrator.store import TaskStore, WorkloadAttemptOutcome
        from director_orchestrator.workload_policy import EnforcementMode, PRIMARY_PROVIDERS, Provider, WorkKind, WorkSize

        old_digest = previous_engineering_digest()
        self.assertNotEqual(old_digest, allocation_profile_digest("engineering"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "allocation.sqlite"
            store = TaskStore(path)
            record = TaskRecord(task_id="DIR-OLD-ALLOCATION", plan_generation=1,
                status=TaskStatus.RUNNING, owner_approved=True,
                intake=SlackIntake(source_event_id="old-profile", channel_id="C1",
                    thread_ts="1.1", user_id="U1", text="Bounded fixture work"))
            store.create(record)
            assignment = Assignment(sequence=1, agent=ProviderName.CLAUDE,
                kind=AssignmentKind.IMPLEMENTATION, task="Bounded fixture work",
                acceptance=["Verified result"], workload_size=WorkSize.SIMPLE, effort_points=1)
            with patch("director_orchestrator.allocation_profiles.ALLOCATION_PROFILES",
                       {"engineering": PREVIOUS_ENGINEERING_TARGETS}):
                prior = store.reserve_workload_assignment(
                    reservation_key=f"{record.task_id}:old-attempt", task_id=record.task_id,
                    assignment_id=f"{record.task_id}:1:1", phase="dispatch", provider=Provider.CLAUDE,
                    effort_units=1, kind=WorkKind.IMPLEMENTATION, size=WorkSize.SIMPLE,
                    model_label=None, available_primary_providers=PRIMARY_PROVIDERS,
                    mode=EnforcementMode.ENFORCED)
                self.assertTrue(prior.decision.allowed)
                store.finalize_workload_reservation(prior.reservation_key, WorkloadAttemptOutcome.INVOKED)
            with closing(sqlite3.connect(path)) as connection:
                before_binding = connection.execute("SELECT * FROM workload_profile_bindings").fetchall()
                self.assertEqual(before_binding[0][2], old_digest)
            store = TaskStore(path)
            before_attempts = store.list_recent_workload_attempts()
            engine = OrchestrationEngine(replace(Settings(), sqlite_path=str(path)), store)
            engine._available_workload_primary_providers = Mock(return_value=PRIMARY_PROVIDERS)
            # Isolate the allocation boundary from unrelated publication/effect fixtures.
            engine._record_workload_pre_submit_stop = Mock()
            engine._status_post = AsyncMock()
            for attempt_key in ("old-attempt", "new-attempt"):
                result = asyncio.run(engine._reserve_workload_dispatch(record, assignment,
                    phase="dispatch", effect_key=attempt_key))
                self.assertIsNone(result)
                held = store.get(record.task_id)
                self.assertEqual(held.status, TaskStatus.OWNER_DECISION_REQUIRED)
                self.assertEqual(held.error,
                    "WorkloadReservationConflict: workload allocation profile binding changed")
                self.assertEqual(store.list_recent_workload_attempts(), before_attempts)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT * FROM workload_profile_bindings").fetchall(), before_binding)
            self.assertEqual(len(before_attempts), 1)
            self.assertEqual(store.list_recent_direct_production(), [])


class AdmissionPolicyTests(unittest.TestCase):
    def test_bootstrap_is_explicit_and_aliases_do_not_invent_credit(self):
        self.assertIsNone(admission_reason("openai", []))
        for lane in ("firecrawl", "github-copilot", "microsoft-365-copilot"):
            self.assertEqual(admission_reason(lane, HISTORY),
                             "workload_profile_lane_ineligible")

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
        providers = (["openai"] * 9 + ["claude"] * 6 + ["cursor"] * 4) if capped else HISTORY
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

    def test_reopened_v1_schema_holds_before_any_attempt_and_preserves_history(self):
        for populated in (False, True):
            with self.subTest(populated=populated):
                if not populated:
                    with self.handle.pool.connection() as connection:
                        connection.execute(f"ALTER TABLE {self.schema}.director_primary_workload_assignment DROP CONSTRAINT director_primary_workload_assignment_policy_version_check")
                        connection.execute(f"ALTER TABLE {self.schema}.director_primary_workload_assignment ADD CONSTRAINT director_primary_workload_assignment_policy_version_check CHECK (policy_version = 'primary-assignment-count-v1')")
                else:
                    with self.handle.pool.connection() as connection:
                        connection.execute(f"""INSERT INTO {self.schema}.director_primary_workload_assignment
                            (task_id, assignment_id, provider_lane, reservation_digest,
                             fencing_generation, policy_version, policy_digest)
                            VALUES ('v1-task','v1-assignment','claude',%s,1,'primary-assignment-count-v1',%s)""", (DIGEST, DIGEST))
                self.handle.close()
                self.handle = open_postgres_control_plane(self.dsn, schema=self.schema,
                    allow_insecure_local_transport=True)
                self.parallel = PostgresParallelDispatch(self.handle.claim_store)
                task = self.task()
                from director_orchestrator.primary_workload import read_admission_history
                with self.assertRaisesRegex(WorkloadAdmissionHold, "workload_schema_policy_incompatible"):
                    with self.handle.pool.connection() as connection:
                        read_admission_history(connection, schema=self.schema)
                def dispatcher(reservation):
                    self.attempt(reservation, task[1])
                    self.fail("incompatible schema must never reach provider I/O")
                self.parallel.execute_ready(task_id=task[0], writer=task[1],
                                            policy=self.policy, dispatcher=dispatcher)
                aggregate = self.parallel.aggregate(task[0])
                self.assertEqual(aggregate.workload_hold_reasons,
                                 ("workload_schema_policy_incompatible",))
                self.assertEqual(aggregate.ambiguous_slot_count, 0)
                self.assertEqual(self.count("director_primary_provider_attempt"), 0)
                self.assertEqual(self.count("director_primary_workload_assignment"), int(populated))
                if populated:
                    with self.handle.pool.connection() as connection:
                        row = connection.execute(f"SELECT policy_version, policy_digest FROM {self.schema}.director_primary_workload_assignment").fetchone()
                    self.assertEqual(row, {"policy_version": "primary-assignment-count-v1", "policy_digest": DIGEST})

    def test_profile_switch_preserves_old_v2_history_and_digest(self):
        from director_orchestrator.primary_workload import LEGACY_V2_DIGEST, read_admission_history
        with self.handle.pool.connection() as connection:
            connection.execute(f"""INSERT INTO {self.schema}.director_primary_workload_assignment
                (task_id,assignment_id,provider_lane,reservation_digest,fencing_generation,policy_version,policy_digest)
                VALUES ('old','old','cursor',%s,1,'primary-assignment-count-v2',%s)""", (DIGEST, LEGACY_V2_DIGEST))
        self.handle.close()
        self.handle = open_postgres_control_plane(self.dsn, schema=self.schema, allow_insecure_local_transport=True)
        with self.handle.pool.connection() as connection:
            self.assertEqual(read_admission_history(connection, schema=self.schema), ("cursor",))
            before = connection.execute(f"SELECT * FROM {self.schema}.director_primary_workload_assignment").fetchone()
            with connection.transaction():
                admit_assignment(connection, schema=self.schema, task_id="new", assignment_id="new",
                    provider="perplexity", reservation_digest=DIGEST, fencing_generation=1, allocation_profile="public_research")
            after = connection.execute(f"SELECT * FROM {self.schema}.director_primary_workload_assignment WHERE task_id='old'").fetchone()
            self.assertEqual(before, after)
            self.assertEqual(read_admission_history(connection, schema=self.schema), ("cursor", "perplexity"))
            with self.assertRaisesRegex(WorkloadAdmissionHold, "identity_changed"):
                with connection.transaction():
                    admit_assignment(connection, schema=self.schema, task_id="new", assignment_id="new",
                        provider="perplexity", reservation_digest=DIGEST, fencing_generation=1, allocation_profile="mixed")

    def test_all_four_profiles_admit_into_one_history(self):
        from director_orchestrator.primary_workload import read_admission_history
        sequence = (("engineering", "openai"), ("mixed", "firecrawl"), ("public_research", "perplexity"), ("enterprise", "github-copilot"))
        with self.handle.pool.connection() as connection:
            for profile, lane in sequence:
                with connection.transaction():
                    admit_assignment(connection, schema=self.schema, task_id=profile, assignment_id=profile,
                        provider=lane, reservation_digest=DIGEST, fencing_generation=1, allocation_profile=profile)
            self.assertEqual(read_admission_history(connection, schema=self.schema), tuple(lane for _, lane in sequence))
            self.assertEqual(self.count("director_primary_workload_assignment"), 4)

    def test_previous_engineering_digest_holds_admission_and_preserves_history(self):
        from director_orchestrator.primary_workload import read_admission_history
        old_digest = previous_engineering_digest()
        self.assertNotEqual(old_digest, POLICY_DIGEST)
        with self.handle.pool.connection() as connection:
            connection.execute(f"""INSERT INTO {self.schema}.director_primary_workload_assignment
                (task_id,assignment_id,provider_lane,reservation_digest,fencing_generation,
                 policy_version,policy_digest,allocation_profile)
                VALUES ('old','old','openai',%s,1,%s,%s,'engineering')""",
                (DIGEST, POLICY_VERSION, old_digest))
            before = connection.execute(f"SELECT * FROM {self.schema}.director_primary_workload_assignment").fetchall()
        self.handle.close()
        self.handle = open_postgres_control_plane(self.dsn, schema=self.schema, allow_insecure_local_transport=True)
        self.parallel = PostgresParallelDispatch(self.handle.claim_store)
        with self.handle.pool.connection() as connection:
            with self.assertRaises(WorkloadAdmissionHold) as read_hold:
                read_admission_history(connection, schema=self.schema)
            self.assertEqual(read_hold.exception.reason_code, "workload_history_invalid")
            with self.assertRaises(WorkloadAdmissionHold) as replay_hold:
                with connection.transaction():
                    admit_assignment(connection, schema=self.schema, task_id="old", assignment_id="old",
                        provider="openai", reservation_digest=DIGEST, fencing_generation=1)
            self.assertEqual(replay_hold.exception.reason_code, "workload_admission_identity_changed")
        task = self.task()
        reservation = self.ready(task)
        with self.assertRaises(WorkloadAdmissionHold) as new_hold:
            self.attempt(reservation, task[1])
        self.assertEqual(new_hold.exception.reason_code, "workload_history_invalid")
        self.assertEqual(self.count("director_primary_provider_attempt"), 0)
        with self.handle.pool.connection() as connection:
            self.assertEqual(connection.execute(f"SELECT * FROM {self.schema}.director_primary_workload_assignment").fetchall(), before)

    def test_profile_authority_change_invalidates_registered_plan(self):
        task_id, writer = self.task()
        with patch("director_orchestrator.primary_parallel.allocation_profile_digest", return_value="sha256:" + "b" * 64):
            with self.assertRaises(ParallelDispatchError):
                self.parallel.register_plan(task_id=task_id, writer=writer, plan_digest=DIGEST,
                    assignments=(ParallelAssignment("assignment",1,"openai","test-deployment"),), policy=self.policy)

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
            reason_code="provider_transient_transport_failure")
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
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:32 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:34 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:48 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]]
