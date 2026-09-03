# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:06 UTC | BEGIN]]
from __future__ import annotations

import os
import unittest
import uuid

from director_orchestrator.langgraph_postgres import (
    PostgresControlPlaneError,
    open_postgres_control_plane,
)


class PrimaryAuthenticatedIngressPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
        if not dsn:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for this zero-skip suite"
            )
        cls.schema = f"director_primary_auth_{uuid.uuid4().hex[:8]}_v1"
        cls.handle = open_postgres_control_plane(
            dsn,
            schema=cls.schema,
            allow_insecure_local_transport=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            with cls.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
        finally:
            cls.handle.close()

    def test_nonce_and_source_identity_are_durable_and_conflict_safe(self) -> None:
        store = self.handle.claim_store
        nonce = "sha256:" + "a" * 64
        source = "sha256:" + "b" * 64
        payload = "sha256:" + "c" * 64
        self.assertEqual(
            ("task-1", True),
            store.claim_authenticated_primary_intake(
                nonce_digest=nonce,
                idempotency_key_digest=source,
                payload_digest=payload,
                task_id="task-1",
            ),
        )
        self.assertEqual(
            ("task-1", False),
            store.claim_authenticated_primary_intake(
                nonce_digest=nonce,
                idempotency_key_digest=source,
                payload_digest=payload,
                task_id="task-replay",
            ),
        )
        with self.assertRaisesRegex(PostgresControlPlaneError, "conflicts"):
            store.claim_authenticated_primary_intake(
                nonce_digest=nonce,
                idempotency_key_digest="sha256:" + "d" * 64,
                payload_digest=payload,
                task_id="task-conflict",
            )


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:06 UTC | END]]
