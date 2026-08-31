# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import os
import threading
import unittest
import uuid
from unittest.mock import Mock

from director_orchestrator.langgraph_postgres import (
    PostgresControlPlaneError,
    PrimaryCommandReceipt,
    PrimaryTaskProjection,
    TaskWriterLease,
    open_postgres_control_plane,
)
from director_orchestrator.langgraph_primary import (
    AcceptanceDecision,
    AcceptanceResult,
    CompletionGateResult,
    EffectDisposition,
    EffectExecutionResult,
    EvidenceDebateResult,
    ExactShaReviewResult,
    PhaseResult,
    PlanResult,
    PrimaryHandlers,
    PrimaryRequest,
    ProviderDisposition,
    ProviderSubgraphResult,
    ScheduleResult,
)
from director_orchestrator.primary_coordinator import (
    PrimaryCoordinatorError,
    PrimaryTaskCoordinator,
)


TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
SHA = "a" * 40


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def handlers() -> PrimaryHandlers:
    return PrimaryHandlers(
        authenticate_scope=lambda c: PhaseResult(digest("auth:" + c.task_id)),
        canonicalize=lambda c: PhaseResult(digest("canonical:" + c.task_id)),
        plan=lambda c: PlanResult(
            digest("plan:" + c.task_id), digest("packet:" + c.task_id)
        ),
        policy_budget=lambda c: PhaseResult(digest("policy:" + c.task_id)),
        schedule=lambda c: ScheduleResult(digest("schedule:" + c.task_id), "claude"),
        provider_subgraph=lambda c: ProviderSubgraphResult(
            provider_attempt_digest=c.provider_attempt_digest,
            idempotency_key_digest=c.idempotency_key_digest,
            authority_snapshot_digest=c.authority_snapshot_digest,
            producer_id=c.scheduled_producer_id,
            disposition=ProviderDisposition.COMPLETED,
            result_digest=digest("provider:" + c.task_id),
            candidate_sha=SHA,
        ),
        evidence_debate=lambda c: EvidenceDebateResult(
            digest("evidence:" + c.task_id),
            digest("debate:" + c.task_id),
            c.candidate_sha,
        ),
        openai_acceptance_revision=lambda c: AcceptanceResult(
            digest("accept:" + c.task_id), c.candidate_sha, AcceptanceDecision.ACCEPT
        ),
        exact_sha_review=lambda c: ExactShaReviewResult(
            digest("review:" + c.task_id),
            c.candidate_sha,
            c.producer_id,
            "cursor",
        ),
        ci_security_completion=lambda c: CompletionGateResult(
            digest("ci:" + c.task_id),
            digest("security:" + c.task_id),
            digest("completion:" + c.task_id),
            c.candidate_sha,
        ),
        effect_execution_readback=lambda c: EffectExecutionResult(
            c.effect_key_digest or digest("effect-key"),
            c.effect_attempt_digest or digest("effect-attempt"),
            c.writer_owner_id,
            EffectDisposition.COMPLETED,
            result_digest=digest("effect-result:" + c.task_id),
            readback_digest=digest("effect-readback:" + c.task_id),
        ),
    )


def request(task_id: str, **overrides: object) -> PrimaryRequest:
    values: dict[str, object] = {
        "task_id": task_id,
        "source_event_id": "event-" + task_id,
        "correlation_id": "correlation-" + task_id,
        "intake_digest": digest("intake:" + task_id),
        "scope_digest": digest("scope:" + task_id),
        "authority_snapshot_digest": digest("authority:" + task_id),
        "work_packet_digest": digest("packet:" + task_id),
        "idempotency_key_digest": digest("idempotency:" + task_id),
        "starting_sha": SHA,
        "writer_owner_id": "langgraph",
        "budget_limit_units": 4,
        "provider_cost_units": 1,
        "execution_approval_required": True,
        "graph_writer_active": True,
        "legacy_writer_active": False,
    }
    values.update(overrides)
    return PrimaryRequest(**values)  # type: ignore[arg-type]


class PrimaryTaskCoordinatorCommandReceiptUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = TaskWriterLease(
            task_id="task-1",
            engine_owner="langgraph",
            owner_token="owner-1",
            fencing_generation=3,
            lease_expires_at="later",
        )
        self.projection = PrimaryTaskProjection(
            task_id="task-1",
            engine_owner="langgraph",
            input_digest=digest("input"),
            intake_fencing_generation=1,
            projection_version=4,
            projection_digest=digest("projection"),
            projection={"run_status": "PAUSED"},
        )
        self.receipt = PrimaryCommandReceipt(
            task_id="task-1",
            operation="provider_result",
            command_id="command-1",
            command_digest=digest("command"),
            resulting_projection_digest=self.projection.projection_digest,
            resulting_projection_version=4,
            writer_fencing_generation=3,
        )
        self.store = Mock()
        self.store.current_task_writer.return_value = self.writer
        self.store.renew_task_writer.return_value = self.writer
        self.store.record_primary_command_receipt.return_value = self.receipt
        self.store.read_primary_command_receipt.return_value = self.receipt
        self.coordinator = object.__new__(PrimaryTaskCoordinator)
        self.coordinator._store = self.store
        self.coordinator._owner_token = "owner-1"

    def test_read_and_record_expose_only_sanitized_receipt_identity(self) -> None:
        recorded = self.coordinator.record_command_receipt(
            task_id="task-1",
            operation="provider_result",
            command_id="command-1",
            command_digest=digest("command"),
            projection=self.projection,
        )
        self.assertEqual(self.receipt, recorded)
        self.store.record_primary_command_receipt.assert_called_once_with(
            task_id="task-1",
            operation="provider_result",
            command_id="command-1",
            command_digest=digest("command"),
            resulting_projection_digest=self.projection.projection_digest,
            resulting_projection_version=4,
            owner_token="owner-1",
            fencing_generation=3,
        )
        self.assertEqual(
            self.receipt,
            self.coordinator.read_command_receipt(
                task_id="task-1",
                operation="provider_result",
                command_id="command-1",
            ),
        )

    def test_cross_task_projection_is_refused_before_store_write(self) -> None:
        other = PrimaryTaskProjection(
            task_id="task-2",
            engine_owner=self.projection.engine_owner,
            input_digest=self.projection.input_digest,
            intake_fencing_generation=self.projection.intake_fencing_generation,
            projection_version=self.projection.projection_version,
            projection_digest=self.projection.projection_digest,
            projection=self.projection.projection,
        )
        with self.assertRaisesRegex(PrimaryCoordinatorError, "another task"):
            self.coordinator.record_command_receipt(
                task_id="task-1",
                operation="provider_result",
                command_id="command-1",
                command_digest=digest("command"),
                projection=other,
            )
        self.store.record_primary_command_receipt.assert_not_called()


class PrimaryTaskCoordinatorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests"
            )

    def setUp(self) -> None:
        self.schema = f"director_coordinator_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(
            TEST_DSN,
            schema=self.schema,
            allow_insecure_local_transport=True,
        )
        self.coordinator = PrimaryTaskCoordinator(
            self.handle,
            handlers(),
            owner_token="default-director-owner",
            checkpoint_namespace="default-director-primary-v1",
        )

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    def test_fenced_intake_starts_graph_and_replay_is_idempotent(self):
        lease = self.coordinator.activate_new_intake(expected_generation=0)
        self.assertEqual(1, lease.fencing_generation)
        task_id = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        first = self.coordinator.create_intake(request(task_id))
        self.assertEqual("langgraph", first.engine_owner)
        self.assertEqual("PAUSED", first.projection["run_status"])
        self.assertIsNotNone(first.projection["pending_approval"])
        replay = self.coordinator.create_intake(request(task_id))
        self.assertEqual(first.projection_digest, replay.projection_digest)
        self.assertEqual(first.projection_version, replay.projection_version)
        readiness = self.coordinator.readiness().as_dict()
        self.assertTrue(readiness["writer_lease_ready"])
        self.assertEqual("langgraph", readiness["new_intake_owner"])
        self.assertTrue(readiness["legacy_new_intake_disabled"])

    def test_wrong_writer_flags_and_competing_intake_fail_closed(self):
        self.coordinator.activate_new_intake(expected_generation=0)
        with self.assertRaisesRegex(PrimaryCoordinatorError, "legacy writer inactive"):
            self.coordinator.create_intake(
                request("DIR-LG-BADFLAGS", legacy_writer_active=True)
            )
        competing = PrimaryTaskCoordinator(
            self.handle,
            handlers(),
            owner_token="competing-owner",
            checkpoint_namespace="default-director-primary-v1",
        )
        with self.assertRaises(PostgresControlPlaneError):
            competing.activate_new_intake(expected_generation=0)

    def test_duplicate_idempotency_key_returns_one_original_task(self):
        self.coordinator.activate_new_intake(expected_generation=0)
        original_id = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        duplicate_id = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        original_command = request(original_id)
        first = self.coordinator.create_intake(original_command)
        duplicate = self.coordinator.create_intake(
            request(
                duplicate_id,
                source_event_id=original_command.source_event_id,
                correlation_id=original_command.correlation_id,
                intake_digest=original_command.intake_digest,
                scope_digest=original_command.scope_digest,
                authority_snapshot_digest=original_command.authority_snapshot_digest,
                work_packet_digest=original_command.work_packet_digest,
                idempotency_key_digest=original_command.idempotency_key_digest,
            )
        )

        self.assertEqual(original_id, duplicate.task_id)
        self.assertEqual(first.projection_digest, duplicate.projection_digest)
        self.assertIsNone(self.coordinator.get_projection(duplicate_id))
        self.assertEqual(1, self.coordinator.readiness().graph_task_count)

    def test_command_receipt_survives_projection_advance_and_rejects_conflict(self):
        self.coordinator.activate_new_intake(expected_generation=0)
        task_id = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        original_projection = self.coordinator.create_intake(request(task_id))
        command_id = "provider-result-" + uuid.uuid4().hex[:8]
        command_digest = digest("command:" + task_id)
        first = self.coordinator.record_command_receipt(
            task_id=task_id,
            operation="provider_result",
            command_id=command_id,
            command_digest=command_digest,
            projection=original_projection,
        )
        advanced = self.coordinator.cancel(task_id)
        self.assertGreater(advanced.projection_version, first.resulting_projection_version)

        replay = self.coordinator.record_command_receipt(
            task_id=task_id,
            operation="provider_result",
            command_id=command_id,
            command_digest=command_digest,
            projection=advanced,
        )
        self.assertEqual(first, replay)
        self.assertEqual(
            first,
            self.coordinator.read_command_receipt(
                task_id=task_id,
                operation="provider_result",
                command_id=command_id,
            ),
        )
        with self.assertRaisesRegex(PostgresControlPlaneError, "different content"):
            self.coordinator.record_command_receipt(
                task_id=task_id,
                operation="provider_result",
                command_id=command_id,
                command_digest=digest("changed:" + task_id),
                projection=advanced,
            )

    def test_concurrent_exact_command_receipts_collapse_to_one_row(self):
        self.coordinator.activate_new_intake(expected_generation=0)
        task_id = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        projection = self.coordinator.create_intake(request(task_id))
        command_id = "recovery-" + uuid.uuid4().hex[:8]
        command_digest = digest("recovery-command:" + task_id)
        barrier = threading.Barrier(3)
        receipts: list[object] = []
        errors: list[BaseException] = []

        def record() -> None:
            try:
                barrier.wait()
                receipts.append(
                    self.coordinator.record_command_receipt(
                        task_id=task_id,
                        operation="recovery",
                        command_id=command_id,
                        command_digest=command_digest,
                        projection=projection,
                    )
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        workers = [threading.Thread(target=record) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(2, len(receipts))
        self.assertEqual(receipts[0], receipts[1])


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | END]]
