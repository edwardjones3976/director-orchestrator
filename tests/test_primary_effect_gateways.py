# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 06:11 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from types import SimpleNamespace

from director_orchestrator.langgraph_postgres import open_postgres_control_plane
from director_orchestrator.langgraph_primary import ClaimRequest, EffectDisposition
from director_orchestrator.primary_effect_gateways import (
    ExactEffectGatewayRegistry,
    GITHUB_MARK_READY,
    PostgresTypedEffectIntentStore,
    SLACK_POST_MESSAGE,
    TypedEffectIntent,
)
from director_orchestrator.primary_effects import (
    DeterministicExternalEffectExecutor,
    PrimaryEffectExecutorError,
)


TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class RecordingGateway:
    def __init__(self, system: str, action: str, *, fail_mutation: bool = False) -> None:
        self.system = system
        self.action = action
        self.fail_mutation = fail_mutation
        self.mutations = 0
        self.readbacks = 0

    def mutate(self, intent, timeout_seconds):
        self.mutations += 1
        if self.fail_mutation:
            raise TimeoutError("remote result was lost")
        if self.system == "slack":
            return "1787450002.123456"
        repository = str(intent.target["repository"]).replace("/", ".")
        return f"github-pr:{repository}:{intent.target['pull_number']}"

    def readback(self, intent, remote_identifier, timeout_seconds):
        self.readbacks += 1
        return {
            "remote_system": self.system,
            "remote_identifier": remote_identifier,
            "terminal_status": "COMPLETED",
            "intent_digest": intent.intent_digest,
        }


class TypedEffectGatewayPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests"
            )

    def setUp(self) -> None:
        self.schema = f"director_typed_effect_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(
            TEST_DSN, schema=self.schema, allow_insecure_local_transport=True
        )
        self.store = self.handle.claim_store
        self.owner = "typed-effect-owner"
        self.task_id = "DIR-TYPED-" + uuid.uuid4().hex[:10].upper()
        writer = self.store.acquire_task_writer(
            self.task_id,
            engine_owner="langgraph",
            owner_token=self.owner,
            expected_generation=0,
        )
        self.generation = writer.fencing_generation
        self.claim_key = digest("claim:" + self.task_id)
        self.effect_key = digest("effect:" + self.task_id)
        self.attempt = digest("attempt:" + self.task_id)
        claimed = self.store.claim(
            ClaimRequest(
                claim_key=self.claim_key,
                task_id=self.task_id,
                field="effect_status",
                expected_state="CLAIMED",
                target_state="EXECUTING",
                attempt_digest=self.attempt,
                owner_token=self.owner,
            )
        )
        self.assertTrue(claimed.granted)
        self.store.register_task_budget(self.task_id, budget_limit_units=2)
        self.reservation = self.store.reserve_effect_intent(
            reservation_key=digest("reservation:" + self.task_id),
            claim_key=self.claim_key,
            task_id=self.task_id,
            owner_token=self.owner,
            effect_key_digest=self.effect_key,
            effect_attempt_digest=self.attempt,
            writer_owner_id="langgraph",
            reserved_units=1,
        )
        self.intent_store = PostgresTypedEffectIntentStore(
            pool=self.handle.pool, schema=self.schema
        )
        self.approval_request_digest = digest("approval-request:" + self.task_id)
        self.approval_receipt_digest = digest("approval-receipt:" + self.task_id)
        self.command_digest = digest("approval-command:" + self.task_id)
        self.authorization_digest = digest("approval-authority:" + self.task_id)
        self.command_id = "approval-" + self.task_id
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"""
                INSERT INTO {self.schema}.director_primary_task_projection (
                    task_id, engine_owner, input_digest,
                    intake_fencing_generation, projection_digest, projection
                ) VALUES (%s, 'langgraph', %s, 1, %s, %s::jsonb)
                """,
                (self.task_id, digest("input:" + self.task_id),
                 digest("projection:" + self.task_id), '{"effect_status":"CLAIMED"}'),
            )
            connection.execute(
                f"""
                INSERT INTO {self.schema}.director_primary_command_authorization (
                    nonce_digest, task_id, operation, command_id, command_digest,
                    proof_version, actor_id, writer_fencing_generation,
                    proof_expires_at
                ) VALUES (%s, %s, 'approval_resume', %s, %s, 'v1', 'owner', %s,
                          now() + interval '5 minutes')
                """,
                (self.authorization_digest, self.task_id, self.command_id,
                 self.command_digest, self.generation),
            )
            connection.execute(
                f"""
                INSERT INTO {self.schema}.director_primary_command_receipt (
                    task_id, operation, command_id, command_digest,
                    resulting_projection_digest, resulting_projection_version,
                    writer_fencing_generation
                ) VALUES (%s, 'approval_resume', %s, %s, %s, 1, %s)
                """,
                (self.task_id, self.command_id, self.command_digest,
                 digest("approval-projection:" + self.task_id), self.generation),
            )
        self.intent_store.record_approval_binding(
            task_id=self.task_id,
            effect_key_digest=self.effect_key,
            approval_request_digest=self.approval_request_digest,
            approval_receipt_digest=self.approval_receipt_digest,
            command_id=self.command_id,
            command_digest=self.command_digest,
            authorization_nonce_digest=self.authorization_digest,
            actor_id="owner",
            writer_owner_id="langgraph",
            writer_fencing_generation=self.generation,
        )
        self.context = SimpleNamespace(
            task_id=self.task_id,
            effect_key_digest=self.effect_key,
            effect_attempt_digest=self.attempt,
            writer_owner_id="langgraph",
        )

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    def count(self, table: str) -> int:
        with self.handle.pool.connection() as connection:
            row = connection.execute(
                f"SELECT count(*) AS count FROM {self.schema}.{table}"
            ).fetchone()
        return int(row["count"])

    def intent(self, system: str) -> TypedEffectIntent:
        common = dict(
            idempotency_key="director:" + self.reservation.reservation_key,
            authority_binding_digest=self.authorization_digest,
            approval_binding_digest=self.approval_receipt_digest,
            writer_owner_id="langgraph",
            writer_fencing_generation=self.generation,
            budget_units=1,
        )
        if system == "slack":
            return TypedEffectIntent(
                system="slack",
                action=SLACK_POST_MESSAGE,
                target={"channel": "C0BAV9CMC80", "thread_ts": "1787450001.123456"},
                payload={"text": "Director terminal result"},
                **common,
            )
        return TypedEffectIntent(
            system="github",
            action=GITHUB_MARK_READY,
            target={
                "repository": "example/director",
                "pull_number": int(self.task_id[-6:], 16) % 100000 + 1,
                "head_sha": "a" * 40,
                "base_sha": "b" * 40,
            },
            payload={},
            **common,
        )

    def executor(self, intent: TypedEffectIntent, gateway: RecordingGateway):
        registry = ExactEffectGatewayRegistry(
            (gateway,), allowed_actions=frozenset({(gateway.system, gateway.action)})
        )
        return DeterministicExternalEffectExecutor(
            store=self.store,
            owner_token=self.owner,
            timeout_seconds=5,
            intent_factory=lambda _context, _reservation: intent,
            intent_store=self.intent_store,
            gateways=registry,
        )

    def test_slack_success_persists_before_call_and_restarts_with_readback_only(self):
        intent = self.intent("slack")
        gateway = RecordingGateway("slack", SLACK_POST_MESSAGE)
        executor = self.executor(intent, gateway)

        first = executor(self.context)
        restarted = self.executor(intent, gateway)
        second = restarted(self.context)

        self.assertTrue(executor.ready)
        self.assertEqual(EffectDisposition.COMPLETED, first.disposition, first.reason_code)
        self.assertEqual(first, second)
        self.assertEqual(1, gateway.mutations)
        self.assertEqual(2, gateway.readbacks)
        self.assertEqual(1, self.count("director_primary_typed_effect_intent"))
        self.assertEqual(intent.as_dict(), self.intent_store.read(self.reservation.reservation_key))

    def test_github_success_is_writer_fenced_and_zero_duplicate(self):
        intent = self.intent("github")
        gateway = RecordingGateway("github", GITHUB_MARK_READY)
        executor = self.executor(intent, gateway)

        first = executor(self.context)
        second = executor(self.context)

        self.assertEqual(EffectDisposition.COMPLETED, first.disposition, first.reason_code)
        self.assertEqual(first, second)
        self.assertEqual(1, gateway.mutations)
        self.assertEqual(1, self.count("director_primary_effect_execution"))
        self.assertEqual(1, self.count("director_primary_remote_binding"))

    def test_unsupported_action_transport_fails_before_execution_claim(self):
        github_intent = self.intent("github")
        slack_gateway = RecordingGateway("slack", SLACK_POST_MESSAGE)
        executor = self.executor(github_intent, slack_gateway)

        with self.assertRaises(PrimaryEffectExecutorError):
            executor(self.context)
        self.assertEqual(0, slack_gateway.mutations)
        self.assertEqual(0, self.count("director_primary_effect_execution"))
        self.assertEqual(0, self.count("director_primary_typed_effect_intent"))

    def test_crash_ambiguity_never_redispatches_typed_effect(self):
        intent = self.intent("slack")
        gateway = RecordingGateway("slack", SLACK_POST_MESSAGE, fail_mutation=True)
        first = self.executor(intent, gateway)(self.context)
        second = self.executor(intent, gateway)(self.context)

        self.assertEqual(EffectDisposition.AMBIGUOUS, first.disposition)
        self.assertEqual(EffectDisposition.AMBIGUOUS, second.disposition)
        self.assertEqual(1, gateway.mutations)
        self.assertEqual(1, self.count("director_primary_effect_execution"))
        self.assertEqual(0, self.count("director_primary_remote_binding"))

    def test_stale_fence_refuses_intent_and_external_call(self):
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_writer "
                "SET lease_expires_at = now() - interval '1 second' WHERE task_id = %s",
                (self.task_id,),
            )
        intent = self.intent("github")
        gateway = RecordingGateway("github", GITHUB_MARK_READY)

        with self.assertRaises(PrimaryEffectExecutorError):
            self.executor(intent, gateway)(self.context)
        self.assertEqual(0, gateway.mutations)
        self.assertEqual(0, self.count("director_primary_typed_effect_intent"))

    def test_payload_secret_fields_and_values_are_rejected(self):
        with self.assertRaises(ValueError):
            TypedEffectIntent(
                system="slack",
                action=SLACK_POST_MESSAGE,
                target={"channel": "C0BAV9CMC80"},
                payload={"token": "forbidden"},
                idempotency_key="director:forbidden",
                authority_binding_digest=digest("authority"),
                approval_binding_digest=digest("approval"),
                writer_owner_id="langgraph",
                writer_fencing_generation=1,
                budget_units=1,
            )

    def test_forged_approval_or_authority_digest_is_rejected_before_execution(self):
        valid = self.intent("slack")
        forged = TypedEffectIntent(
            system=valid.system,
            action=valid.action,
            target=valid.target,
            payload=valid.payload,
            idempotency_key=valid.idempotency_key,
            authority_binding_digest=digest("forged-owner-authority"),
            approval_binding_digest=valid.approval_binding_digest,
            writer_owner_id=valid.writer_owner_id,
            writer_fencing_generation=valid.writer_fencing_generation,
            budget_units=valid.budget_units,
        )
        gateway = RecordingGateway("slack", SLACK_POST_MESSAGE)
        with self.assertRaises(PrimaryEffectExecutorError):
            self.executor(forged, gateway)(self.context)
        self.assertEqual(0, gateway.mutations)
        self.assertEqual(0, self.count("director_primary_effect_execution"))


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 06:11 UTC | END]]
