# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:24 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from types import SimpleNamespace

from director_orchestrator.langgraph_postgres import open_postgres_control_plane
from director_orchestrator.langgraph_primary import (
    ApprovalKind,
    ClaimRequest,
    EffectDisposition,
    EffectStatus,
    PrimaryWorkflowRuntime,
    TerminalClassification,
    WorkflowRunStatus,
    _canonical_digest,
    primary_checkpoint_config,
)
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.primary_effects import (
    DeterministicExternalEffectExecutor,
    PrimaryEffectExecutorError,
    RemoteEffectReference,
)
from tests import test_langgraph_primary as graph_fixtures


TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class DeterministicExternalEffectExecutorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests"
            )

    def setUp(self) -> None:
        self.schema = f"director_effect_executor_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(
            TEST_DSN,
            schema=self.schema,
            allow_insecure_local_transport=True,
        )
        self.store = self.handle.claim_store
        self.owner = "effect-executor-owner"
        self.task_id = "DIR-EFFECT-" + uuid.uuid4().hex[:10].upper()
        self.store.acquire_task_writer(
            self.task_id,
            engine_owner="langgraph",
            owner_token=self.owner,
            expected_generation=0,
        )
        self.claim_key = digest("claim:" + self.task_id)
        self.effect_key = digest("effect-key:" + self.task_id)
        self.effect_attempt = digest("effect-attempt:" + self.task_id)
        outcome = self.store.claim(
            ClaimRequest(
                claim_key=self.claim_key,
                task_id=self.task_id,
                field="effect_status",
                expected_state="CLAIMED",
                target_state="EXECUTING",
                attempt_digest=self.effect_attempt,
                owner_token=self.owner,
            )
        )
        self.assertTrue(outcome.granted)
        self.store.register_task_budget(self.task_id, budget_limit_units=2)
        self.reservation = self.store.reserve_effect_intent(
            reservation_key=digest("reservation:" + self.task_id),
            claim_key=self.claim_key,
            task_id=self.task_id,
            owner_token=self.owner,
            effect_key_digest=self.effect_key,
            effect_attempt_digest=self.effect_attempt,
            writer_owner_id="langgraph",
            reserved_units=1,
        )
        self.context = SimpleNamespace(
            task_id=self.task_id,
            effect_key_digest=self.effect_key,
            effect_attempt_digest=self.effect_attempt,
            writer_owner_id="langgraph",
        )

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    def row_count(self, table: str) -> int:
        with self.handle.pool.connection() as connection:
            row = connection.execute(
                f"SELECT count(*) AS count FROM {self.schema}.{table}"
            ).fetchone()
        return int(row["count"])

    def test_reserves_then_calls_once_binds_and_records_terminal_readback(self) -> None:
        mutations: list[tuple[str, float]] = []
        readbacks: list[str] = []

        def mutate(context, reservation, timeout_seconds):
            self.assertEqual(1, self.row_count("director_primary_effect_reservation"))
            self.assertEqual(1, self.row_count("director_primary_effect_execution"))
            self.assertEqual(self.effect_key, reservation.effect_key_digest)
            mutations.append((context.task_id, timeout_seconds))
            return RemoteEffectReference("github", "remote-123")

        def readback(context, reservation, reference, timeout_seconds):
            self.assertEqual(1, self.row_count("director_primary_remote_binding"))
            readbacks.append(reference.remote_identifier)
            return {
                **reference.as_dict(),
                "terminal_status": "COMPLETED",
            }

        executor = DeterministicExternalEffectExecutor(
            store=self.store,
            owner_token=self.owner,
            mutation=mutate,
            readback=readback,
            timeout_seconds=7,
        )
        first = executor(self.context)
        replay = executor(self.context)

        self.assertEqual(EffectDisposition.COMPLETED, first.disposition)
        self.assertEqual(first, replay)
        self.assertEqual([(self.task_id, 7.0)], mutations)
        self.assertEqual(["remote-123", "remote-123"], readbacks)
        self.assertEqual(1, self.row_count("director_primary_effect_execution"))
        self.assertEqual(1, self.row_count("director_primary_remote_binding"))
        self.assertEqual(1, self.row_count("director_primary_terminal_evidence"))

    def test_crash_between_remote_call_and_binding_never_redispatches(self) -> None:
        mutations = 0

        def mutate(context, reservation, timeout_seconds):
            nonlocal mutations
            mutations += 1
            # The remote write may have completed, but its response/binding was
            # lost. This is the exact crash-ambiguity window.
            raise TimeoutError("remote outcome unknown")

        executor = DeterministicExternalEffectExecutor(
            store=self.store,
            owner_token=self.owner,
            mutation=mutate,
            readback=lambda *args: {},
            timeout_seconds=3,
        )
        first = executor(self.context)
        restarted = DeterministicExternalEffectExecutor(
            store=self.store,
            owner_token=self.owner,
            mutation=mutate,
            readback=lambda *args: {},
            timeout_seconds=3,
        )
        second = restarted(self.context)

        self.assertEqual(EffectDisposition.AMBIGUOUS, first.disposition)
        self.assertEqual(EffectDisposition.AMBIGUOUS, second.disposition)
        self.assertEqual(1, mutations)
        self.assertEqual(1, self.row_count("director_primary_effect_execution"))
        self.assertEqual(0, self.row_count("director_primary_remote_binding"))
        self.assertGreaterEqual(
            self.row_count("director_primary_reconciliation_event"), 1
        )

    def test_stale_writer_fence_prevents_any_external_call(self) -> None:
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_writer "
                "SET lease_expires_at = now() - interval '1 second' "
                "WHERE task_id = %s",
                (self.task_id,),
            )
        mutations = 0

        def mutate(*args):
            nonlocal mutations
            mutations += 1
            return RemoteEffectReference("github", "forbidden")

        executor = DeterministicExternalEffectExecutor(
            store=self.store,
            owner_token=self.owner,
            mutation=mutate,
            readback=lambda *args: {},
            timeout_seconds=3,
        )
        with self.assertRaises(PrimaryEffectExecutorError):
            executor(self.context)
        self.assertEqual(0, mutations)

    # ------------------------------------------------------------------
    # Paired crash orderings at the real PostgreSQL effect-receipt writes.
    # The graph checkpoint is written after the executor returns
    # (langgraph_primary applies the effect result separately), so a
    # restart re-runs the effect node with the same context. Both orderings
    # reopen a fresh store handle and must never mutate remotely again.
    # ------------------------------------------------------------------

    class _SimulatedProcessLoss(BaseException):
        """Escapes the executor's Exception handling like a real process loss."""

    def reopened_store(self):
        handle = open_postgres_control_plane(
            TEST_DSN, schema=self.schema, allow_insecure_local_transport=True
        )
        self.addCleanup(handle.close)
        return handle.claim_store

    def never_mutate(self, *args):
        raise AssertionError("a restarted executor must never call the remote mutation again")

    def test_crash_after_remote_binding_before_receipt_restarts_with_readback_only(self) -> None:
        # Ordering 1: the remote write and its binding are durable, but the
        # process is lost before the terminal effect receipt is written.
        mutations = 0

        def mutate(context, reservation, timeout_seconds):
            nonlocal mutations
            mutations += 1
            return RemoteEffectReference("slack", "1787450002.123456")

        def crash_before_receipt(*args):
            raise self._SimulatedProcessLoss()

        crashed = DeterministicExternalEffectExecutor(
            store=self.store, owner_token=self.owner, mutation=mutate,
            readback=crash_before_receipt, timeout_seconds=3,
        )
        with self.assertRaises(self._SimulatedProcessLoss):
            crashed(self.context)
        self.assertEqual(1, mutations)
        self.assertEqual(1, self.row_count("director_primary_effect_execution"))
        self.assertEqual(1, self.row_count("director_primary_remote_binding"))
        self.assertEqual(0, self.row_count("director_primary_terminal_evidence"))
        self.assertEqual(0, self.row_count("director_primary_reconciliation_event"))

        readbacks: list[str] = []

        def readback(context, reservation, reference, timeout_seconds):
            readbacks.append(reference.remote_identifier)
            return {**reference.as_dict(), "terminal_status": "COMPLETED"}

        restarted = DeterministicExternalEffectExecutor(
            store=self.reopened_store(), owner_token=self.owner,
            mutation=self.never_mutate, readback=readback, timeout_seconds=3,
        )
        result = restarted(self.context)
        self.assertEqual(EffectDisposition.COMPLETED, result.disposition, result.reason_code)
        self.assertEqual(["1787450002.123456"], readbacks)
        self.assertEqual(1, mutations)
        self.assertEqual(1, self.row_count("director_primary_effect_execution"))
        self.assertEqual(1, self.row_count("director_primary_remote_binding"))
        self.assertEqual(1, self.row_count("director_primary_terminal_evidence"))
        self.assertEqual(0, self.row_count("director_primary_reconciliation_event"))

    def test_receipt_persisted_but_checkpoint_lost_replays_without_second_effect(self) -> None:
        # Ordering 2: the terminal receipt is durable, but the process is lost
        # before the graph checkpoint records it, so the node re-runs.
        mutations = 0
        readbacks = 0

        def mutate(context, reservation, timeout_seconds):
            nonlocal mutations
            mutations += 1
            return RemoteEffectReference("github", "github-pr:example.director:7")

        def readback(context, reservation, reference, timeout_seconds):
            nonlocal readbacks
            readbacks += 1
            return {**reference.as_dict(), "terminal_status": "COMPLETED"}

        first = DeterministicExternalEffectExecutor(
            store=self.store, owner_token=self.owner, mutation=mutate,
            readback=readback, timeout_seconds=3,
        )(self.context)
        self.assertEqual(EffectDisposition.COMPLETED, first.disposition, first.reason_code)
        self.assertEqual(1, self.row_count("director_primary_terminal_evidence"))

        replay = DeterministicExternalEffectExecutor(
            store=self.reopened_store(), owner_token=self.owner,
            mutation=self.never_mutate, readback=readback, timeout_seconds=3,
        )(self.context)
        self.assertEqual(first, replay)
        self.assertEqual(first.result_digest, replay.result_digest)
        self.assertEqual(1, mutations)
        self.assertEqual(2, readbacks)
        self.assertEqual(1, self.row_count("director_primary_effect_execution"))
        self.assertEqual(1, self.row_count("director_primary_remote_binding"))
        self.assertEqual(1, self.row_count("director_primary_terminal_evidence"))
        self.assertEqual(0, self.row_count("director_primary_reconciliation_event"))


class _SimulatedProcessLoss(BaseException):
    """Escapes every Exception handler in the runtime, like a real process loss."""


class PrimaryGraphCheckpointReceiptOrderingTests(unittest.TestCase):
    """Paired crash orderings at the real PostgresSaver checkpoint and the
    PostgreSQL terminal effect-receipt writes, driven only through the real
    PrimaryWorkflowRuntime graph API over a factory-opened control plane.

    The effect handler is the production DeterministicExternalEffectExecutor
    with synthetic remote mutation/readback closures. Every observation is made
    from a separately opened handle (its own pool and connections), and every
    recovery closes the crashed handle and reopens the runtime and stores.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests"
            )

    def setUp(self) -> None:
        self.schema = f"director_graph_effect_{uuid.uuid4().hex[:8]}_v1"
        self.owner = "graph-effect-owner"
        self.task_id = "DIR-GRAPH-" + uuid.uuid4().hex[:10].upper()
        self.effect_key = digest("effect-key:" + self.task_id)
        self.remote = RemoteEffectReference("slack", "1787450009." + self.task_id[-6:].lower())
        self.config = primary_checkpoint_config(self.task_id)
        self.handles = []
        self.handle = self.open()
        self.handle.claim_store.acquire_task_writer(
            self.task_id, engine_owner="langgraph-primary",
            owner_token=self.owner, expected_generation=0,
        )
        self.mutations = 0
        self.readbacks = 0
        self.results = []
        self.executing_checkpoint_id = None

    def tearDown(self) -> None:
        witness = None
        try:
            witness = open_postgres_control_plane(
                TEST_DSN, schema=self.schema, allow_insecure_local_transport=True
            )
            with witness.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            if witness is not None:
                witness.close()
            for handle in self.handles:
                handle.close()

    def open(self):
        handle = open_postgres_control_plane(
            TEST_DSN, schema=self.schema, allow_insecure_local_transport=True
        )
        self.handles.append(handle)
        return handle

    def count(self, handle, table: str) -> int:
        with handle.pool.connection() as connection:
            row = connection.execute(
                f"SELECT count(*) AS count FROM {self.schema}.{table}"
            ).fetchone()
        return int(row["count"])

    def request(self):
        return graph_fixtures._request(
            self.task_id, external_effect_required=True, effect_key_digest=self.effect_key,
            effect_cost_units=1, graph_writer_active=True, legacy_writer_active=False,
        )

    def executor(self, handle, *, mutation=None, readback=None):
        def mutate(context, reservation, timeout_seconds):
            self.mutations += 1
            return self.remote

        def read_back(context, reservation, reference, timeout_seconds):
            self.readbacks += 1
            return {**reference.as_dict(), "terminal_status": "COMPLETED"}

        return DeterministicExternalEffectExecutor(
            store=handle.claim_store, owner_token=self.owner, timeout_seconds=3,
            mutation=mutation or mutate, readback=readback or read_back,
        )

    def never_mutate(self, *args):
        raise AssertionError("a recovered runtime must never call the remote mutation again")

    def runtime(self, handle, handler, **options):
        return PrimaryWorkflowRuntime(
            handle.saver, graph_fixtures._handlers(effect_execution_readback=handler),
            claim_store=handle.claim_store, owner_token=self.owner, **options,
        )

    def handler(self, handle, executor, *, after=None):
        """The production executor, observing the durable EXECUTING checkpoint first."""

        def effect_execution_readback(context):
            durable = handle.saver.get_tuple(self.config)
            self.assertEqual(EffectStatus.EXECUTING.value,
                             durable.checkpoint["channel_values"]["effect_status"])
            self.executing_checkpoint_id = durable.config["configurable"]["checkpoint_id"]
            result = executor(context)
            self.results.append(result)
            if after is not None:
                after()
            return result

        return effect_execution_readback

    def approve_effect(self, runtime):
        outcome = runtime.start(self.request())
        self.assertEqual(WorkflowRunStatus.PAUSED, outcome.run_status)
        self.assertEqual(ApprovalKind.EXTERNAL_EFFECT, outcome.pending_approval.kind)
        return runtime.resume_approval(graph_fixtures._approval(outcome, "effect-approval-event"))

    def durable(self, handle):
        return handle.saver.get_tuple(self.config)

    # -- ordering 1: EXECUTING checkpoint persisted, terminal receipt absent ---

    def crash_before_receipt(self):
        """Run the real graph until the remote effect is bound, then lose the process."""

        def crash(context, reservation, reference, timeout_seconds):
            raise _SimulatedProcessLoss()

        runtime = self.runtime(self.handle, self.handler(self.handle, self.executor(self.handle, readback=crash)))
        with self.assertRaises(_SimulatedProcessLoss):
            self.approve_effect(runtime)
        witness = self.open()
        durable = self.durable(witness)
        self.assertEqual(self.executing_checkpoint_id, durable.config["configurable"]["checkpoint_id"])
        self.assertEqual(EffectStatus.EXECUTING.value, durable.checkpoint["channel_values"]["effect_status"])
        self.assertIsNone(durable.checkpoint["channel_values"].get("effect_result_receipt_digest"))
        self.assertEqual(1, self.mutations)
        self.assertEqual(1, self.count(witness, "director_primary_effect_execution"))
        self.assertEqual(1, self.count(witness, "director_primary_remote_binding"))
        self.assertEqual(0, self.count(witness, "director_primary_terminal_evidence"))
        self.assertEqual(0, self.count(witness, "director_primary_reconciliation_event"))
        self.handle.close()
        return witness

    def test_graph_crash_after_executing_checkpoint_before_receipt_is_fail_closed_without_repeat(self):
        witness = self.crash_before_receipt()
        reopened = self.open()
        recovered = self.runtime(reopened, self.handler(reopened, self.executor(reopened, mutation=self.never_mutate)))
        outcome = recovered.recover(self.task_id)
        # Default runtime: an EXECUTING checkpoint without a receipt is ambiguous;
        # the handler is never re-run and nothing is resent.
        self.assertEqual(WorkflowRunStatus.TERMINAL, outcome.run_status)
        state = recovered.compact_state(self.task_id)
        self.assertEqual(TerminalClassification.RECONCILIATION_REQUIRED.value, state["terminal_classification"])
        self.assertEqual(EffectStatus.RECONCILIATION_REQUIRED.value, state["effect_status"])
        self.assertEqual("effect_outcome_unknown", state["transition_reason_code"])
        self.assertEqual([], self.results)
        self.assertEqual(1, self.mutations)
        self.assertEqual(0, self.readbacks)
        self.assertNotEqual(self.executing_checkpoint_id, self.durable(witness).config["configurable"]["checkpoint_id"])
        self.assertEqual(1, self.count(witness, "director_primary_remote_binding"))
        self.assertEqual(0, self.count(witness, "director_primary_terminal_evidence"))

    def test_graph_crash_after_executing_checkpoint_before_receipt_reconciles_by_readback_only(self):
        witness = self.crash_before_receipt()
        reopened = self.open()
        recovered = self.runtime(reopened, self.handler(reopened, self.executor(reopened, mutation=self.never_mutate)),
                                 reconcile_executing_effect=True)
        outcome = recovered.recover(self.task_id)
        self.assertEqual(WorkflowRunStatus.TERMINAL, outcome.run_status)
        state = recovered.compact_state(self.task_id)
        self.assertEqual(TerminalClassification.SUCCEEDED.value, state["terminal_classification"])
        self.assertEqual(EffectStatus.COMPLETED.value, state["effect_status"])
        self.assertEqual(1, len(self.results))
        result = self.results[0]
        self.assertEqual(EffectDisposition.COMPLETED, result.disposition)
        self.assertEqual(_canonical_digest(result.as_resume_payload()), state["effect_result_receipt_digest"])
        self.assertEqual(result.result_digest, state["effect_result_digest"])
        self.assertEqual(1, self.mutations)
        self.assertEqual(1, self.readbacks)
        with witness.pool.connection() as connection:
            receipt = connection.execute(
                f"SELECT task_id, disposition, evidence_digest FROM {self.schema}.director_primary_terminal_evidence"
            ).fetchall()
            binding = connection.execute(
                f"SELECT remote_system, remote_identifier FROM {self.schema}.director_primary_remote_binding"
            ).fetchall()
        self.assertEqual([{"task_id": self.task_id, "disposition": "COMPLETED",
                           "evidence_digest": document_digest(result.as_resume_payload())}], receipt)
        self.assertEqual([self.remote.as_dict()], binding)
        self.assertEqual(0, self.count(witness, "director_primary_reconciliation_event"))

    # -- ordering 2: terminal receipt persisted, result checkpoint absent ------

    def test_graph_crash_after_receipt_before_checkpoint_recovers_without_second_effect(self):
        armed = {"crash": False}
        saver = self.handle.saver
        durable_put = saver.put

        def crashing_put(*args, **kwargs):
            if armed["crash"]:
                raise _SimulatedProcessLoss()
            return durable_put(*args, **kwargs)

        saver.put = crashing_put  # the real PostgresSaver; only the post-receipt checkpoint write is lost
        runtime = self.runtime(self.handle, self.handler(self.handle, self.executor(self.handle),
                                                          after=lambda: armed.update(crash=True)))
        with self.assertRaises(_SimulatedProcessLoss):
            self.approve_effect(runtime)
        result = self.results[0]
        self.assertEqual(EffectDisposition.COMPLETED, result.disposition)
        witness = self.open()
        durable = self.durable(witness)
        # Receipt persisted; the checkpoint that would record it never was.
        self.assertEqual(1, self.count(witness, "director_primary_terminal_evidence"))
        self.assertEqual(self.executing_checkpoint_id, durable.config["configurable"]["checkpoint_id"])
        self.assertEqual(EffectStatus.EXECUTING.value, durable.checkpoint["channel_values"]["effect_status"])
        self.assertIsNone(durable.checkpoint["channel_values"].get("effect_result_receipt_digest"))
        self.handle.close()
        reopened = self.open()
        recovered = self.runtime(reopened, self.handler(reopened, self.executor(reopened, mutation=self.never_mutate)),
                                 reconcile_executing_effect=True)
        outcome = recovered.recover(self.task_id)
        self.assertEqual(WorkflowRunStatus.TERMINAL, outcome.run_status)
        state = recovered.compact_state(self.task_id)
        self.assertEqual(TerminalClassification.SUCCEEDED.value, state["terminal_classification"])
        self.assertEqual(EffectStatus.COMPLETED.value, state["effect_status"])
        self.assertEqual(_canonical_digest(result.as_resume_payload()), state["effect_result_receipt_digest"])
        self.assertEqual(result.result_digest, state["effect_result_digest"])
        self.assertEqual(1, self.mutations)
        # Either LangGraph's durable task writes settle the node without a handler
        # call, or the reconciling handler reads back once; never a second mutation.
        self.assertIn(len(self.results), (1, 2))
        self.assertEqual(len(self.results), self.readbacks)
        self.assertTrue(all(item == result for item in self.results))
        with witness.pool.connection() as connection:
            receipt = connection.execute(
                f"SELECT task_id, disposition, evidence_digest FROM {self.schema}.director_primary_terminal_evidence"
            ).fetchall()
        self.assertEqual([{"task_id": self.task_id, "disposition": "COMPLETED",
                           "evidence_digest": document_digest(result.as_resume_payload())}], receipt)
        self.assertEqual(1, self.count(witness, "director_primary_remote_binding"))
        self.assertEqual(0, self.count(witness, "director_primary_reconciliation_event"))


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:24 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | END]]
