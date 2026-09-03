# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:24 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from types import SimpleNamespace

from director_orchestrator.langgraph_postgres import open_postgres_control_plane
from director_orchestrator.langgraph_primary import ClaimRequest, EffectDisposition
from director_orchestrator.primary_effects import (
    DeterministicExternalEffectExecutor,
    PrimaryEffectExecutorError,
    RemoteEffectReference,
)


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


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:24 UTC | END]]
