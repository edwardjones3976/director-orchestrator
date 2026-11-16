"""Real Postgres acceptance cases; collection is safe without a DSN."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import threading
import unittest
import uuid
from unittest.mock import Mock, patch

from director_orchestrator import langgraph_postgres as pg
from director_orchestrator.models import NeutralIntake
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.primary_coordinator import PrimaryTaskCoordinator


def digest(value):
    return document_digest({"value": value})


class NeutralClaimContractUnitTests(unittest.TestCase):
    def test_coordinator_keeps_legacy_call_and_forwards_neutral_fields(self):
        coordinator = object.__new__(PrimaryTaskCoordinator)
        coordinator._store = Mock()
        coordinator._require_active_generation = Mock()
        old = dict(nonce_digest=digest("nonce"), idempotency_key_digest=digest("source"),
                   payload_digest=digest("body"), task_id="task")
        coordinator.claim_authenticated_intake(**old)
        coordinator._store.claim_authenticated_primary_intake.assert_called_once_with(**old)
        coordinator._store.reset_mock()
        neutral = dict(old, authentication_class="neutral_gateway_attested",
                       source_provider="native", source_event_id="event")
        coordinator.claim_authenticated_intake(**neutral)
        coordinator._store.claim_authenticated_primary_intake.assert_called_once_with(**neutral)
        self.assertEqual(2, coordinator._require_active_generation.call_count)
        coordinator._require_active_generation.side_effect = RuntimeError("fenced")
        coordinator._store.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "fenced"):
            coordinator.claim_authenticated_intake(**neutral)
        coordinator._store.claim_authenticated_primary_intake.assert_not_called()

    def test_invalid_claims_do_not_open_connection(self):
        pool = Mock()
        store = pg.PostgresConditionalClaimStore(_pool=pool, _schema="claims_v2",
                                               storage_identity="test",
                                               _store_provenance=pg._FACTORY_PROVENANCE)
        base = dict(nonce_digest=digest("nonce"), payload_digest=digest("body"),
                    task_id="task", authentication_class="neutral_gateway_attested",
                    source_provider="native", source_event_id="event")
        for change in ({"authentication_class": "bearer_dry_run"},
                       {"source_provider": None}, {"source_event_id": ""},
                       {"nonce_digest": "bad"}, {"payload_digest": "bad"},
                       {"idempotency_key_digest": digest("wrong-source")}):
            with self.subTest(change=change), self.assertRaises((ValueError, pg.PostgresControlPlaneError)):
                store.claim_authenticated_primary_intake(**dict(base, **change))
        pool.connection.assert_not_called()


class PrimaryNeutralClaimsPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
        if not cls.dsn:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for this zero-skip suite")

    def setUp(self):
        self.schema = "primary_neutral_" + uuid.uuid4().hex[:12] + "_v2"
        self.handle = None
        self.addCleanup(self.cleanup)
        self.open()

    def open(self):
        self.handle = pg.open_postgres_control_plane(
            self.dsn, schema=self.schema, allow_insecure_local_transport=True)
        self.store = self.handle.claim_store

    def reopen(self):
        self.handle.close()
        self.handle = None
        self.open()

    def cleanup(self):
        if self.handle is not None:
            self.handle.close()
        import psycopg
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def body(self, **changes):
        received = datetime(2026, 11, 16, tzinfo=timezone.utc)
        values = dict(provider="native", source_event_id="event", actor_id="owner",
                      recipient_id="director-orchestrator", conversation_id="conversation",
                      text="A bounded source task", received_at=received,
                      deadline_at=received + timedelta(hours=1))
        values.update(changes)
        return NeutralIntake(**values).model_dump(mode="json")

    def claim(self, *, nonce="nonce", task="task", body=None):
        body = self.body() if body is None else body
        return self.store.claim_authenticated_primary_intake(
            nonce_digest=digest(nonce), payload_digest=document_digest(body), task_id=task,
            authentication_class="neutral_gateway_attested",
            source_provider=body["provider"], source_event_id=body["source_event_id"])

    def rows(self):
        with self.handle.pool.connection() as connection:
            return connection.execute(f"SELECT * FROM {self.schema}.director_primary_intake_auth_claim "
                                      "ORDER BY created_at, nonce_digest").fetchall()

    def test_clean_install_exact_replay_alias_and_committed_response_loss(self):
        # The caller may lose the response after this commit; no graph/projection exists.
        self.claim()
        first = dict(self.store.read_authenticated_primary_intake("task"))
        self.assertEqual("neutral_gateway_attested", first["authentication_class"])
        self.assertEqual(document_digest(self.body()), first["payload_digest"])
        self.assertIsNotNone(first["receiver_received_at"].tzinfo)
        self.assertIsNone(self.store.read_primary_task_projection("task"))
        self.reopen()
        self.assertEqual(("task", False), self.claim(task="lost-response-replay"))
        self.assertEqual(("task", False), self.claim(nonce="fresh", task="fresh-replay"))
        self.assertEqual(("task", False), self.claim(nonce="fresh", task="alias-replay"))
        self.reopen()
        self.assertEqual(first, dict(self.store.read_authenticated_primary_intake("task")))
        rows = self.rows()
        self.assertEqual(2, len(rows))
        canonical = next(row for row in rows if row["original_nonce_digest"] is None)
        alias = next(row for row in rows if row["original_nonce_digest"] is not None)
        self.assertEqual(canonical["nonce_digest"], alias["original_nonce_digest"])
        self.assertGreaterEqual(alias["created_at"], canonical["created_at"])
        self.assertEqual({"task"}, {row["task_id"] for row in rows})
        self.assertIsNone(self.store.read_authenticated_primary_intake("missing"))
        import psycopg
        with self.handle.pool.connection() as connection:
            for statement in (
                f"UPDATE {self.schema}.director_primary_intake_auth_claim SET task_id='changed'",
                f"DELETE FROM {self.schema}.director_primary_intake_auth_claim",
            ):
                with self.assertRaises(psycopg.errors.RaiseException), connection.transaction():
                    connection.execute(statement)
        self.assertEqual(rows, self.rows())

    def test_changed_signed_body_conflicts_for_original_and_fresh_nonce(self):
        self.claim()
        first = self.rows()
        for change in ({"text": "changed"}, {"actor_id": "other-owner"},
                       {"conversation_id": "elsewhere"},
                       {"received_at": datetime(2026, 11, 16, 0, 1, tzinfo=timezone.utc)},
                       {"deadline_at": datetime(2026, 11, 17, tzinfo=timezone.utc)}):
            for nonce in ("nonce", "fresh"):
                with self.subTest(change=change, nonce=nonce), self.assertRaisesRegex(
                        pg.PostgresControlPlaneError, "conflicts"):
                    self.claim(nonce=nonce, body=self.body(**change))
        self.assertEqual(first, self.rows())

    def test_source_nonce_and_authentication_domains(self):
        self.assertEqual(("task", True), self.claim())
        self.assertEqual(("other-provider", True), self.claim(
            task="other-provider", body=self.body(provider="another")))
        with self.assertRaisesRegex(pg.PostgresControlPlaneError, "conflicts"):
            self.claim(task="changed-event", body=self.body(source_event_id="other"))
        self.assertEqual(("other-event", True), self.claim(
            nonce="fresh", task="other-event", body=self.body(source_event_id="other")))
        self.assertEqual(("slack", True), self.store.claim_authenticated_primary_intake(
            nonce_digest=digest("nonce"), idempotency_key_digest=digest("event"),
            payload_digest=digest("slack-body"), task_id="slack"))
        self.assertEqual(4, len(self.rows()))
        self.assertEqual(4, len({row["nonce_digest"] for row in self.rows()}))
        with self.assertRaisesRegex(pg.PostgresControlPlaneError, "conflicts"):
            self.claim(nonce="third", body=self.body(source_event_id="third"))

    def race(self, calls):
        barrier = threading.Barrier(len(calls))
        def run(call):
            barrier.wait(timeout=10)
            try:
                return self.claim(**call)
            except pg.PostgresControlPlaneError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            return list(executor.map(run, calls))

    def test_concurrent_fresh_nonces_create_one_logical_task(self):
        results = self.race([dict(nonce=f"nonce-{i}", task=f"task-{i}") for i in range(4)])
        self.assertEqual(1, sum(created for _, created in results))
        self.assertEqual(1, len({task for task, _ in results}))
        self.assertEqual(4, len(self.rows()))
        self.assertEqual(1, sum(row["original_nonce_digest"] is None for row in self.rows()))

    def test_concurrent_changed_payload_and_nonce_source_conflicts(self):
        results = self.race([dict(nonce="one", task="first"),
                             dict(nonce="two", task="second", body=self.body(text="changed"))])
        self.assertEqual(1, results.count("conflict"))
        self.assertEqual(1, len(self.rows()))
        winner = next(row for row in self.rows())
        # Two sources racing on the same fresh nonce: exactly one may bind it.
        results = self.race([dict(nonce="shared", task="third", body=self.body(source_event_id="third")),
                             dict(nonce="shared", task="fourth", body=self.body(source_event_id="fourth"))])
        self.assertEqual(1, results.count("conflict"))
        self.assertEqual(2, len(self.rows()))
        self.assertIn(winner, self.rows())

    def test_alias_nonce_cannot_be_reused_for_changed_event_or_body(self):
        self.claim()
        self.claim(nonce="alias")
        for body in (self.body(source_event_id="other"), self.body(actor_id="other")):
            with self.assertRaisesRegex(pg.PostgresControlPlaneError, "conflicts"):
                self.claim(nonce="alias", task="other", body=body)
        self.assertEqual(2, len(self.rows()))

    def install_v1_claim_table(self):
        # Replace only the empty fixture table with the exact pre-upgrade DDL.
        with self.handle.pool.connection() as connection:
            with connection.transaction():
                connection.execute(f"DROP TABLE {self.schema}.director_primary_intake_auth_claim")
                connection.execute(f"""CREATE TABLE {self.schema}.director_primary_intake_auth_claim (
                    nonce_digest text PRIMARY KEY, idempotency_key_digest text NOT NULL UNIQUE,
                    payload_digest text NOT NULL, task_id text NOT NULL UNIQUE,
                    authentication_class text NOT NULL CHECK (authentication_class = 'slack_gateway_attested'),
                    created_at timestamptz NOT NULL DEFAULT now())""")
                connection.execute(f"""INSERT INTO {self.schema}.director_primary_intake_auth_claim
                    (nonce_digest, idempotency_key_digest, payload_digest, task_id, authentication_class)
                    VALUES (%s, %s, %s, 'legacy', 'slack_gateway_attested')""",
                    (digest("old-nonce"), digest("old-source"), digest("old-body")))
                for ddl in pg._guard_triggers(self.schema):
                    if "director_primary_intake_auth_claim" in ddl:
                        connection.execute(ddl)
        return self.rows()

    def test_installed_v1_upgrade_preserves_history_and_slack_semantics(self):
        old = self.install_v1_claim_table()[0]
        self.reopen()
        self.reopen()
        upgraded = self.rows()[0]
        self.assertEqual(old, {key: upgraded[key] for key in old})
        self.assertIsNone(upgraded["original_nonce_digest"])
        args = dict(nonce_digest=digest("old-nonce"), idempotency_key_digest=digest("old-source"),
                    payload_digest=digest("old-body"), task_id="replay")
        self.assertEqual(("legacy", False), self.store.claim_authenticated_primary_intake(**args))
        for change in ({"nonce_digest": digest("new-nonce")},
                       {"payload_digest": digest("changed-body")},
                       {"idempotency_key_digest": digest("changed-source")}):
            with self.assertRaisesRegex(pg.PostgresControlPlaneError, "conflicts"):
                self.store.claim_authenticated_primary_intake(**dict(args, **change))
        self.assertEqual(("task", True), self.claim())
        self.assertEqual(("task", False), self.claim(nonce="alias"))
        self.reopen()
        self.assertEqual(3, len(self.rows()))
        self.assertEqual(old, {key: self.store.read_authenticated_primary_intake("legacy")[
            "receiver_received_at" if key == "created_at" else key] for key in old})

    def test_failed_partial_upgrade_rolls_back_then_restarts_idempotently(self):
        old = self.install_v1_claim_table()
        original = pg._control_plane_ddl
        def failing_ddl(schema):
            for statement in original(schema):
                yield statement
                if "END $migration$" in statement and "original_nonce_digest" in statement:
                    yield "SELECT director_intentional_claim_upgrade_failure()"
        self.handle.close()
        self.handle = None
        import psycopg
        with patch.object(pg, "_control_plane_ddl", side_effect=failing_ddl):
            with self.assertRaises(psycopg.errors.UndefinedFunction):
                self.open()
        with psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row) as connection:
            rows = connection.execute(f"SELECT * FROM {self.schema}.director_primary_intake_auth_claim").fetchall()
            self.assertEqual(old, rows)
            self.assertNotIn("original_nonce_digest", rows[0])
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(f"""INSERT INTO {self.schema}.director_primary_intake_auth_claim
                    VALUES (%s, %s, %s, 'refused', 'neutral_gateway_attested', now())""",
                    (digest("n"), digest("s"), digest("p")))
        self.open()
        self.reopen()
        self.assertEqual(("task", True), self.claim())
        self.assertEqual(2, len(self.rows()))


if __name__ == "__main__":
    unittest.main()
