# [[OpenAI Codex | GPT-6 | 2026-09-10 | 17:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 16:46 UTC | BEGIN]]
"""Owner-authenticated cancellation against the disposable PostgreSQL lane."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from director_orchestrator.langgraph_postgres import open_postgres_control_plane, PostgresControlPlaneError
from director_orchestrator.primary_commands import (
    CancellationRequest, RecoveryRequest, PrimaryIngressProof, PrimaryCommandResponse,
    PrimaryCommandOperation, PrimaryCommandBindingError,
    build_primary_ingress_proof, verify_primary_ingress_proof,
)
from director_orchestrator.primary_coordinator import PrimaryTaskCoordinator
from director_orchestrator.primary_parallel import ParallelAssignment, ParallelDispatchPolicy
from tests import test_primary_coordinator as fixtures


class AuthenticatedCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "")
        if not cls.dsn:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required")
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"SQLITE_PATH": str(Path(cls.temp.name) / "legacy.sqlite3"),
                                    "LANGGRAPH_PRIMARY_ENABLED": "false"}):
            cls.main = importlib.import_module("director_orchestrator.main")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.schema = "cancel_" + uuid.uuid4().hex[:12] + "_v1"
        self.handle = open_postgres_control_plane(self.dsn, schema=self.schema,
                                                  allow_insecure_local_transport=True)
        self.addCleanup(self.close_database)
        self.provider = Mock(side_effect=AssertionError("provider must not execute"))
        self.effect = Mock(side_effect=AssertionError("effect must not execute"))
        self.coordinator = PrimaryTaskCoordinator(self.handle,
            replace(fixtures.handlers(), provider_subgraph=self.provider, effect_execution_readback=self.effect),
            owner_token="cancel-owner", checkpoint_namespace="cancel-tests",
            auto_execute_provider=False)
        self.coordinator.activate_new_intake(expected_generation=0)
        self.task = "cancel-" + uuid.uuid4().hex[:12]
        self.coordinator.create_intake(fixtures.request(self.task, execution_approval_required=False))
        self.coordinator.register_parallel_plan(task_id=self.task,
            plan_digest=fixtures.digest("parallel"),
            assignments=(ParallelAssignment("one", 1, "openai", "openai-test"),
                         ParallelAssignment("two", 2, "claude", "claude-test")),
            policy=ParallelDispatchPolicy())
        self.application = SimpleNamespace(cancel=self.coordinator.cancel,
                                           recover=Mock(side_effect=AssertionError("recovery must not run")))
        settings = SimpleNamespace(shared_secret="test-bearer", slack_channel_id="CTEST",
                                   slack_owner_user_id="UOWNER", slack_gateway_secret="g" * 40)
        for attribute, value in (("settings", settings), ("primary_default_activation",
                SimpleNamespace(coordinator=self.coordinator, application=self.application))):
            context = patch.object(self.main, attribute, value)
            context.start()
            self.addCleanup(context.stop)
        api = FastAPI()
        api.add_api_route("/v1/primary/tasks/{task_id}/cancel", self.main.primary_cancel,
            methods=["POST"], response_model=PrimaryCommandResponse,
            dependencies=[Depends(self.main.require_auth)])
        api.add_api_route("/v1/primary/tasks/{task_id}/recover", self.main.primary_recover,
            methods=["POST"], response_model=PrimaryCommandResponse,
            dependencies=[Depends(self.main.require_auth)])
        self.client = TestClient(api)
        self.addCleanup(self.client.close)
        self.path = f"/v1/primary/tasks/{self.task}/cancel"
        self.headers = {"Authorization": "Bearer test-bearer"}

    def close_database(self):
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA {self.schema} CASCADE")
        finally:
            self.handle.close()

    def expire(self, task=None):
        with self.handle.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_task_writer "
                "SET lease_expires_at=now()-interval '1 day' WHERE task_id=%s", (task or self.task,))

    def signed(self, **changes):
        writer = self.coordinator.get_task_writer(self.task)
        projected = self.coordinator.get_projection(self.task)
        values = dict(task_id=self.task, command_id="cancel-command", actor_id="UOWNER",
            expected_projection_digest=projected.projection_digest,
            expected_fencing_generation=writer.fencing_generation,
            gateway_proof=PrimaryIngressProof(issued_at=int(time.time()), nonce="n" * 32,
                                            channel_id="CTEST", signature="0" * 64))
        values.update(changes)
        request = CancellationRequest(**values)
        proof = build_primary_ingress_proof("g" * 40, "test-bearer", request,
            path=self.path, proof_field="gateway_proof", issued_at=request.gateway_proof.issued_at,
            nonce=request.gateway_proof.nonce, channel_id=request.gateway_proof.channel_id)
        return request.model_copy(update={"gateway_proof": proof})

    def send(self, request):
        return self.client.post(self.path, json=request.model_dump(mode="json"), headers=self.headers)

    def admission(self, request):
        nonce = verify_primary_ingress_proof("g" * 40, "test-bearer", request,
            path=self.path, proof_field="gateway_proof")
        return dict(task_id=self.task, command_id=request.command_id,
            command_digest=request.command_digest, nonce_digest=nonce,
            actor_id=request.actor_id, proof_version=request.gateway_proof.version,
            expected_projection_digest=request.expected_projection_digest,
            expected_generation=request.expected_fencing_generation,
            proof_expires_at=request.gateway_proof.issued_at + 300)

    def snapshot(self, task=None):
        task = task or self.task
        with self.handle.pool.connection() as connection:
            writer = connection.execute(f"SELECT * FROM {self.schema}.director_primary_task_writer "
                                        "WHERE task_id=%s", (task,)).fetchone()
            auth = connection.execute(f"SELECT * FROM {self.schema}.director_primary_command_authorization "
                                      "WHERE task_id=%s", (task,)).fetchall()
            batch = connection.execute(f"SELECT * FROM {self.schema}.director_primary_parallel_batch "
                                       "WHERE task_id=%s", (task,)).fetchone()
        return writer, auth, batch

    def test_expired_writer_cancels_once_and_never_dispatches(self):
        self.expire()
        request = self.signed()
        before = self.snapshot()
        response = self.send(request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("CANCELLED", response.json()["terminal_classification"])
        after = self.snapshot()
        self.assertEqual(before[0]["fencing_generation"] + 1, after[0]["fencing_generation"])
        self.assertEqual(1, len(after[1]))
        self.assertTrue(after[2]["kill_switch"])
        self.assertEqual(after[0]["fencing_generation"], after[1][0]["writer_fencing_generation"])
        replay = self.send(request)
        self.assertEqual(200, replay.status_code, replay.text)
        self.assertEqual(after, self.snapshot())
        self.assertEqual({"PENDING"}, set(self.coordinator.parallel_aggregate(self.task).statuses.values()))
        self.provider.assert_not_called()
        self.effect.assert_not_called()

    def test_live_same_owner_keeps_generation_and_never_dispatches(self):
        request = self.signed()
        response = self.send(request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(request.expected_fencing_generation, response.json()["fencing_generation"])
        self.assertEqual("CANCELLED", response.json()["terminal_classification"])
        self.provider.assert_not_called()
        self.effect.assert_not_called()

    def test_live_expiring_writer_is_renewed_before_cancellation_and_replay(self):
        request = self.signed()
        with self.handle.pool.connection() as connection:
            original_expiry = connection.execute(
                f"UPDATE {self.schema}.director_primary_task_writer "
                "SET lease_expires_at=clock_timestamp()+interval '2 seconds' "
                "WHERE task_id=%s RETURNING lease_expires_at", (self.task,)
            ).fetchone()["lease_expires_at"]
        admitted_writers = []
        cancel = self.application.cancel

        def cancel_after_original_expiry(*args, **kwargs):
            admitted = self.snapshot()
            admitted_writers.append(admitted[0])
            self.coordinator.authorize_cancellation(**self.admission(request))
            self.assertEqual(admitted, self.snapshot())
            # This delay reproduces the admission-to-cancel lease race.
            time.sleep(max(0, original_expiry.timestamp() - time.time()) + 0.1)
            return cancel(*args, **kwargs)

        with patch.object(self.application, "cancel", side_effect=cancel_after_original_expiry):
            response = self.send(request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("CANCELLED", response.json()["terminal_classification"])
        self.assertEqual(request.expected_fencing_generation, response.json()["fencing_generation"])
        self.assertGreater(admitted_writers[0]["lease_expires_at"], original_expiry)
        after = self.snapshot()
        self.assertEqual(1, len(after[1]))
        self.assertTrue(after[2]["kill_switch"])
        self.assertEqual(200, self.send(request).status_code)
        self.assertEqual(after, self.snapshot())
        self.provider.assert_not_called()
        self.effect.assert_not_called()

    def test_receipt_replay_requires_original_digest_and_only_cancellation_transition(self):
        self.expire()
        request = self.signed()
        self.assertEqual(200, self.send(request).status_code)
        before = self.snapshot()
        for changes in ({"reason_code": "changed"},
                        {"gateway_proof": request.gateway_proof.model_copy(update={"nonce": "x" * 32})}):
            changed = request.model_copy(update=changes)
            proof = build_primary_ingress_proof("g" * 40, "test-bearer", changed,
                path=self.path, proof_field="gateway_proof", issued_at=changed.gateway_proof.issued_at,
                nonce=changed.gateway_proof.nonce, channel_id="CTEST")
            self.assertEqual(409, self.send(changed.model_copy(update={"gateway_proof": proof})).status_code)
            self.assertEqual(before, self.snapshot())
        receipt = self.coordinator.read_command_receipt(task_id=self.task,
            operation="cancellation", command_id=request.command_id)
        for generation in (request.expected_fencing_generation - 1,
                           request.expected_fencing_generation + 2):
            with self.assertRaisesRegex(PrimaryCommandBindingError, "writer generation"):
                PrimaryCommandResponse.from_receipt(request=request,
                    operation=PrimaryCommandOperation.CANCELLATION,
                    receipt=replace(receipt, writer_fencing_generation=generation))
        recovery = RecoveryRequest(**{**request.model_dump(mode="json"),
                                     "schema_version": "langgraph-primary-recovery/v1"})
        with self.assertRaisesRegex(PrimaryCommandBindingError, "writer generation"):
            PrimaryCommandResponse.from_receipt(request=recovery,
                operation=PrimaryCommandOperation.RECOVERY,
                receipt=replace(receipt, command_digest=recovery.command_digest))

    def test_expired_recovery_cannot_use_cancellation_takeover(self):
        self.expire()
        request = self.signed()
        recovery = RecoveryRequest(**{**request.model_dump(mode="json"),
                                     "schema_version": "langgraph-primary-recovery/v1"})
        path = f"/v1/primary/tasks/{self.task}/recover"
        proof = build_primary_ingress_proof("g" * 40, "test-bearer", recovery,
            path=path, proof_field="gateway_proof", issued_at=request.gateway_proof.issued_at,
            nonce="r" * 32, channel_id="CTEST")
        before = self.snapshot()
        response = self.client.post(path,
            json=recovery.model_copy(update={"gateway_proof": proof}).model_dump(mode="json"),
            headers=self.headers)
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual(before, self.snapshot())
        self.application.recover.assert_not_called()

    def test_bad_proof_channel_actor_and_bearer_leave_no_mutations(self):
        self.expire()
        request = self.signed()
        cases = [request.model_copy(update={"gateway_proof": request.gateway_proof.model_copy(update={"signature": "0" * 64})}),
                 self.signed(actor_id="UNOTOWNER"),
                 self.signed(gateway_proof=request.gateway_proof.model_copy(update={"channel_id": "COTHER"}))]
        before = self.snapshot()
        for rejected in cases:
            self.assertEqual(403, self.send(rejected).status_code)
            self.assertEqual(before, self.snapshot())
        response = self.client.post(self.path, json=request.model_dump(mode="json"))
        self.assertEqual(401, response.status_code)
        self.assertEqual(before, self.snapshot())

    def test_stale_projection_or_generation_and_live_foreign_writer_refuse(self):
        self.expire()
        before = self.snapshot()
        for request in (self.signed(expected_projection_digest=fixtures.digest("stale")),
                        self.signed(expected_fencing_generation=999)):
            self.assertEqual(409, self.send(request).status_code)
            self.assertEqual(before, self.snapshot())
        self.handle.claim_store.acquire_task_writer(self.task, engine_owner="langgraph",
            owner_token="other-owner", expected_generation=before[0]["fencing_generation"])
        request = self.signed()
        before = self.snapshot()
        self.assertEqual(409, self.send(request).status_code)
        self.assertEqual(before, self.snapshot())

    def test_crash_after_admission_retries_original_old_generation(self):
        self.expire()
        request = self.signed()
        with patch.object(self.application, "cancel", side_effect=RuntimeError("crash after admission")):
            self.assertEqual(409, self.send(request).status_code)
        admitted = self.snapshot()
        self.assertEqual(request.expected_fencing_generation + 1, admitted[0]["fencing_generation"])
        self.assertTrue(admitted[2]["kill_switch"])
        response = self.send(request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("CANCELLED", response.json()["terminal_classification"])
        self.assertEqual(admitted[0]["fencing_generation"], self.snapshot()[0]["fencing_generation"])

    def test_crash_after_terminal_projection_retries_original_projection(self):
        self.expire()
        request = self.signed()
        with patch.object(self.coordinator, "record_command_receipt", side_effect=RuntimeError("crash before receipt")):
            self.assertEqual(409, self.send(request).status_code)
        self.assertEqual("CANCELLED", self.coordinator.get_projection(self.task).projection["terminal_classification"])
        self.assertNotEqual(request.expected_projection_digest, self.coordinator.get_projection(self.task).projection_digest)
        response = self.send(request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(request.expected_fencing_generation + 1, response.json()["fencing_generation"])

    def test_changed_nonce_digest_or_actor_cannot_rebind_admitted_command(self):
        self.expire()
        request = self.signed()
        arguments = self.admission(request)
        self.coordinator.authorize_cancellation(**arguments)
        before = self.snapshot()
        for changes in ({"nonce_digest": fixtures.digest("other nonce")},
                        {"command_digest": fixtures.digest("other command")}, {"actor_id": "UOTHER"}):
            with self.assertRaises(PostgresControlPlaneError):
                self.coordinator.authorize_cancellation(**{**arguments, **changes})
            self.assertEqual(before, self.snapshot())

    def test_exact_concurrent_admission_has_one_transition(self):
        self.expire()
        request = self.signed()
        arguments = self.admission(request)
        barrier = threading.Barrier(2)
        def admit():
            barrier.wait(timeout=5)
            return self.coordinator.authorize_cancellation(**arguments).fencing_generation
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(admit) for _ in range(2)]
            generations = [f.result(timeout=15) for f in futures]
        self.assertEqual([request.expected_fencing_generation + 1] * 2, generations)
        self.assertEqual(1, len(self.snapshot()[1]))

    def test_projection_advance_before_admission_refuses_without_takeover(self):
        request = self.signed()
        current = self.coordinator.get_projection(self.task)
        self.handle.claim_store.update_primary_task_projection(task_id=self.task,
            owner_token="cancel-owner", fencing_generation=request.expected_fencing_generation,
            projection={**current.projection, "audit_note": "concurrent projection advance"})
        self.expire()
        before = self.snapshot()
        self.assertEqual(409, self.send(request).status_code)
        self.assertEqual(before, self.snapshot())

    def test_superseded_admission_fence_cannot_cancel_or_record_receipt(self):
        self.expire()
        request = self.signed()
        admitted = self.coordinator.authorize_cancellation(**self.admission(request))
        self.expire()
        self.handle.claim_store.acquire_task_writer(self.task, engine_owner="langgraph",
            owner_token="cancel-owner", expected_generation=admitted.fencing_generation)
        before = self.snapshot()
        self.assertEqual(409, self.send(request).status_code)
        with self.assertRaises(PostgresControlPlaneError):
            self.coordinator.cancel(self.task, expected_fencing_generation=admitted.fencing_generation)
        with self.assertRaises(PostgresControlPlaneError):
            self.coordinator.record_command_receipt(task_id=self.task, operation="cancellation",
                command_id=request.command_id, command_digest=request.command_digest,
                projection=self.coordinator.get_projection(self.task),
                expected_fencing_generation=admitted.fencing_generation)
        self.assertEqual(before, self.snapshot())

    def test_expired_admitted_writer_refuses_retry_without_reacquiring(self):
        self.expire()
        request = self.signed()
        self.coordinator.authorize_cancellation(**self.admission(request))
        self.expire()
        before = self.snapshot()
        self.assertEqual(409, self.send(request).status_code)
        self.assertEqual(before, self.snapshot())

    def test_nonce_collision_rolls_back_losing_writer_and_batch(self):
        other = "cancel-other-" + uuid.uuid4().hex[:8]
        self.coordinator.create_intake(fixtures.request(other, execution_approval_required=False))
        self.coordinator.register_parallel_plan(task_id=other, plan_digest=fixtures.digest("other-plan"),
            assignments=(ParallelAssignment("other", 1, "openai", "openai-test"),),
            policy=ParallelDispatchPolicy())
        self.expire()
        self.expire(other)
        arguments = self.admission(self.signed())
        other_args = {**arguments, "task_id": other, "command_id": "other-command",
            "command_digest": fixtures.digest("other-command"),
            "expected_projection_digest": self.coordinator.get_projection(other).projection_digest,
            "expected_generation": self.coordinator.get_task_writer(other).fencing_generation}
        before = {task: self.snapshot(task) for task in (self.task, other)}
        barrier = threading.Barrier(2)
        store = self.handle.claim_store
        original = store._one_row
        def synchronized(connection, statement, parameters, **kwargs):
            result = original(connection, statement, parameters, **kwargs)
            if "SELECT task_id FROM" in statement and "FOR UPDATE" in statement:
                barrier.wait(timeout=5)  # Both absence checks precede either insert.
            return result
        def admit(values):
            try:
                self.coordinator.authorize_cancellation(**values)
                return values["task_id"], True
            except PostgresControlPlaneError:
                return values["task_id"], False
        with patch.object(type(store), "_one_row", side_effect=synchronized), ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(admit, values) for values in (arguments, other_args)]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual([False, True], sorted(success for _, success in outcomes))
        for task, success in outcomes:
            after = self.snapshot(task)
            if success:
                self.assertEqual(before[task][0]["fencing_generation"] + 1, after[0]["fencing_generation"])
                self.assertEqual(1, len(after[1]))
                self.assertTrue(after[2]["kill_switch"])
            else:
                self.assertEqual(before[task], after)

    def test_proof_expiring_while_projection_locked_cannot_admit(self):
        self.expire()
        arguments = {**self.admission(self.signed()), "proof_expires_at": int(time.time()) + 2}
        before = self.snapshot()
        entered = threading.Event()
        original = self.handle.claim_store._one_row
        def observe(connection, statement, parameters, **kwargs):
            if "SELECT projection_digest FROM" in statement and "FOR UPDATE" in statement:
                entered.set()
            return original(connection, statement, parameters, **kwargs)
        with patch.object(type(self.handle.claim_store), "_one_row", side_effect=observe), ThreadPoolExecutor(max_workers=1) as executor:
            with self.handle.pool.connection() as connection, connection.transaction():
                connection.execute(f"SELECT task_id FROM {self.schema}.director_primary_task_projection "
                                   "WHERE task_id=%s FOR UPDATE", (self.task,))
                future = executor.submit(self.coordinator.authorize_cancellation, **arguments)
                self.assertTrue(entered.wait(timeout=5))
                # This sleep is test-controlled lock contention, not a live retry.
                time.sleep(max(0, arguments["proof_expires_at"] - time.time()) + 0.1)
            with self.assertRaisesRegex(PostgresControlPlaneError, "expired during admission"):
                future.result(timeout=5)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 16:46 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 17:47 UTC | END]]
