# [[OpenAI Codex | GPT-6 | 2026-09-08 | 03:33 UTC | BEGIN]]
"""Existing entitlement enrollment checks on a caller-owned primary pool."""
from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import ReadOnlySqlTransaction

from director_orchestrator import entitlement_control_plane as entitlement
from director_orchestrator.langgraph_postgres import open_postgres_control_plane


TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()


class BorrowedEntitlementEnrollmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required")

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.primary_schema = f"borrowed_primary_{suffix}_v1"
        self.schema = f"borrowed_entitlement_{suffix}_v1"
        self.handle = open_postgres_control_plane(
            TEST_DSN, schema=self.primary_schema,
            allow_insecure_local_transport=True,
        )
        self.addCleanup(self.handle.close)
        self.addCleanup(self._drop_schemas)
        self.identity = entitlement._storage_identity(
            conninfo_to_dict(TEST_DSN), schema=self.schema
        )

    def _drop_schemas(self) -> None:
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            connection.execute(f"DROP SCHEMA IF EXISTS {self.primary_schema} CASCADE")

    def _enroll(self) -> None:
        # Explicit fixture enrollment, never part of the borrowed validator.
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(f"CREATE SCHEMA {self.schema}")
            entitlement._bootstrap_schema(
                connection, schema=self.schema, storage_identity=self.identity
            )

    def _validate(self) -> None:
        entitlement.validate_entitlement_enrollment(
            self.handle.pool, schema=self.schema, storage_identity=self.identity
        )

    def _assert_pool_usable(self) -> None:
        self.assertFalse(self.handle.pool.closed)
        with self.handle.pool.connection() as connection, connection.transaction():
            self.assertEqual("off", connection.execute(
                "SHOW transaction_read_only"
            ).fetchone()["transaction_read_only"])
            connection.execute(
                "CREATE TEMP TABLE borrowed_pool_probe (value integer) ON COMMIT DROP"
            )
            connection.execute("INSERT INTO borrowed_pool_probe VALUES (7)")
            self.assertEqual(7, connection.execute(
                "SELECT value FROM borrowed_pool_probe"
            ).fetchone()["value"])

    def test_missing_schema_refuses_without_creating_it_or_closing_pool(self) -> None:
        with self.assertRaisesRegex(entitlement.EntitlementControlPlaneError, "not enrolled"):
            self._validate()
        with self.handle.pool.connection() as connection:
            self.assertIsNone(connection.execute(
                "SELECT to_regnamespace(%s) AS schema", (self.schema,)
            ).fetchone()["schema"])
        self._assert_pool_usable()

    def test_foreign_schema_refuses_without_adopting_or_changing_it(self) -> None:
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(f"CREATE SCHEMA {self.schema}")
            connection.execute(f"CREATE TABLE {self.schema}.foreign_marker (value integer)")
            connection.execute(f"INSERT INTO {self.schema}.foreign_marker VALUES (13)")
        with self.assertRaisesRegex(entitlement.EntitlementControlPlaneError, "not enrolled"):
            self._validate()
        with self.handle.pool.connection() as connection:
            self.assertEqual(13, connection.execute(
                f"SELECT value FROM {self.schema}.foreign_marker"
            ).fetchone()["value"])
            self.assertIsNone(connection.execute(
                "SELECT to_regclass(%s) AS manifest",
                (f"{self.schema}.entitlement_schema_manifest",),
            ).fetchone()["manifest"])
        self._assert_pool_usable()

    def test_valid_distinct_enrollment_and_existing_bootstrap_reopen_succeed(self) -> None:
        self._enroll()
        with self.handle.pool.connection() as connection, connection.transaction():
            entitlement._bootstrap_schema(
                connection, schema=self.schema, storage_identity=self.identity
            )
        self._validate()
        self._assert_pool_usable()

    def test_foreign_manifest_identity_refuses_without_rewriting_it(self) -> None:
        self._enroll()
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.entitlement_schema_manifest SET storage_identity = %s",
                ("foreign-enrollment",),
            )
        with self.assertRaisesRegex(entitlement.EntitlementControlPlaneError, "different storage target"):
            self._validate()
        with self.handle.pool.connection() as connection:
            self.assertEqual("foreign-enrollment", connection.execute(
                f"SELECT storage_identity FROM {self.schema}.entitlement_schema_manifest"
            ).fetchone()["storage_identity"])
        self._assert_pool_usable()

    def test_changed_object_inventory_refuses(self) -> None:
        self._enroll()
        with self.handle.pool.connection() as connection:
            connection.execute(f"CREATE TABLE {self.schema}.foreign_table (value integer)")
        with self.assertRaisesRegex(entitlement.EntitlementControlPlaneError, "object inventory"):
            self._validate()
        self._assert_pool_usable()

    def test_missing_immutable_trigger_refuses(self) -> None:
        self._enroll()
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"DROP TRIGGER entitlement_event_immutable ON {self.schema}.entitlement_event"
            )
        with self.assertRaisesRegex(entitlement.EntitlementControlPlaneError, "immutable trigger"):
            self._validate()
        self._assert_pool_usable()

    def test_foreign_schema_owner_refuses(self) -> None:
        self._enroll()
        with self.handle.pool.connection() as connection:
            connection.execute(f"ALTER SCHEMA {self.schema} OWNER TO pg_database_owner")
        with self.assertRaisesRegex(entitlement.EntitlementControlPlaneError, "ownership contract"):
            self._validate()
        self._assert_pool_usable()

    def test_database_enforces_read_only_and_pool_recovers_after_sql_failure(self) -> None:
        self._enroll()

        def attempted_write(connection, **_kwargs):
            connection.execute(
                f"UPDATE {self.schema}.entitlement_schema_manifest SET storage_identity = 'changed'"
            )

        with patch.object(entitlement, "_validate_enrolled_schema", side_effect=attempted_write):
            with self.assertRaises(ReadOnlySqlTransaction):
                self._validate()
        self._assert_pool_usable()
        self._validate()


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 03:33 UTC | END]]
