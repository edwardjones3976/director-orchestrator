# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 04:18 UTC | BEGIN]]
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from director_orchestrator.langgraph_runtime import (
    PacketBoundaryAdapter,
    RuntimeExecutionOutcome,
    RuntimeExecutionStatus,
)
from director_orchestrator.interactions import require_verified_result
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.packet_contracts import document_digest, load_json_document
from director_orchestrator.packet_results import (
    DirectorPacketResultConsumer,
    PacketResultConsumptionError,
)
from director_orchestrator.packet_routing import (
    DirectorPacketTaskRouter,
    PacketRoutingError,
)


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"
COMMIT_SHA = "a" * 40
BLOB_SHA = "b" * 40


class StaticAuthoritySnapshots:
    def resolve(self, record, assignment):
        return [{"path": "AGENTS.md", "commit_sha": COMMIT_SHA, "blob_sha": BLOB_SHA}]


class NoopTransport:
    def send(self, work_packet_json, *, context):  # pragma: no cover - not dispatched
        raise AssertionError("routing tests do not dispatch")


class PacketTaskRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Implement one bounded change",
            acceptance=["Focused tests pass"],
            repository="example/demo",
            allow_draft_pr=True,
            estimated_cost_usd=0.75,
        )
        self.record = TaskRecord(
            task_id="DIR-ROUTER",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-router",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR route this assignment",
            ),
            plan_generation=2,
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="Implement one bounded change",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=3,
                max_revisions=2,
                assignments=[self.assignment],
                final_acceptance=["Focused tests pass"],
            ),
        )
        self.adapter = PacketBoundaryAdapter("claude", NoopTransport())
        self.router = DirectorPacketTaskRouter(
            authority_snapshots=StaticAuthoritySnapshots(),
            boundary_adapters={"claude": self.adapter},
            writer_owner_id="langgraph-packet-runtime",
            writer_secret="local-test-secret",
            timeout_seconds=120,
        )

    def test_router_builds_a_valid_authority_bound_packet(self) -> None:
        packet = self.router.work_packet(self.record, self.assignment)

        self.assertEqual(packet["assignment_id"], "DIR-ROUTER-1")
        self.assertEqual(packet["starting_ref"], COMMIT_SHA)
        self.assertEqual(packet["authority_base_ref"], "main")
        self.assertEqual(packet["authority_snapshot"][0]["commit_sha"], COMMIT_SHA)
        self.assertEqual(packet["limits"]["max_revisions"], 2)
        self.assertEqual(packet["allowed_external_effects"], [
            "WRITE_ISOLATED_BRANCH",
            "OPEN_DRAFT_PULL_REQUEST",
        ])
        self.assertIn("MERGE_PULL_REQUEST", packet["prohibited_actions"])
        self.assertIn(":2:1:", packet["idempotency_key"])

    def test_writer_token_is_stable_for_restart_and_task_scoped(self) -> None:
        first = self.router.writer_token(self.record)
        restarted = DirectorPacketTaskRouter(
            authority_snapshots=StaticAuthoritySnapshots(),
            boundary_adapters={"claude": self.adapter},
            writer_owner_id="langgraph-packet-runtime",
            writer_secret="local-test-secret",
            timeout_seconds=120,
        ).writer_token(self.record)
        other = self.record.model_copy(update={"task_id": "DIR-OTHER"})

        self.assertEqual(first.digest, restarted.digest)
        self.assertNotEqual(first.digest, self.router.writer_token(other).digest)

    def test_router_normalizes_and_revalidates_owner_approved_base_ref(self) -> None:
        assignment = self.assignment.model_copy(
            update={"starting_ref": "refs/heads/main"}
        )
        self.record.plan.assignments[0] = assignment
        packet = self.router.work_packet(self.record, assignment)

        self.assertEqual(packet["authority_base_ref"], "main")
        self.router.validate_bound_packet(self.record, assignment, packet)

        stale = dict(packet)
        stale["authority_base_ref"] = "release"
        with self.assertRaisesRegex(PacketRoutingError, "authority_base_ref changed"):
            self.router.validate_bound_packet(self.record, assignment, stale)

    def test_router_refuses_unaccepted_dependency_and_unconfigured_adapter(self) -> None:
        dependent = self.assignment.model_copy(update={"depends_on": [2]})
        self.record.plan.assignments[0] = dependent
        with self.assertRaisesRegex(PacketRoutingError, "lacks an accepted outcome"):
            self.router.work_packet(self.record, dependent)
        with self.assertRaisesRegex(PacketRoutingError, "no v1 packet boundary"):
            self.router.boundary_adapter("cursor")

    def test_router_refuses_absent_or_stale_assignment_approval(self) -> None:
        gated = self.assignment.model_copy(update={"requires_approval": True})
        self.record.plan.assignments[0] = gated
        with self.assertRaisesRegex(PacketRoutingError, "approval is absent or stale"):
            self.router.work_packet(self.record, gated)


class PacketResultConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.routing = PacketTaskRoutingTests()
        self.routing.setUp()
        self.record = self.routing.record
        self.assignment = self.routing.assignment
        self.work = dict(self.routing.router.work_packet(self.record, self.assignment))
        example = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")
        self.result = copy.deepcopy(example)
        for field in (
            "task_id",
            "assignment_id",
            "idempotency_key",
            "authority_snapshot_digest",
            "correlation",
        ):
            self.result[field] = copy.deepcopy(self.work[field])
        self.result["provider"] = "claude"
        self.result["run_reference"] = "run-router-1"
        self.result["side_effects"] = []
        self.consumer = DirectorPacketResultConsumer()

    def _runtime_outcome(self, result=None):
        packet = self.result if result is None else result
        return RuntimeExecutionOutcome(
            status=RuntimeExecutionStatus.COMPLETED,
            reason="validated provider result completed",
            result_packet=packet,
            result_packet_digest=document_digest(packet),
        )

    def test_success_records_provider_evidence_without_accepting_assignment(self) -> None:
        progression = self.consumer.consume(
            self.record, self.assignment, self.work, self._runtime_outcome()
        )

        self.assertTrue(progression.provider_run_recorded)
        self.assertTrue(progression.requires_director_review)
        self.assertEqual(self.record.status, TaskStatus.RUNNING)
        self.assertEqual(len(self.record.provider_runs), 1)
        self.assertEqual(self.record.provider_runs[0].status, "completed")
        verified = require_verified_result(
            self.record.provider_interaction_ledger,
            interaction_id=self.record.provider_runs[0].interaction_id or "",
            result=self.record.provider_runs[0].result or "",
        )
        self.assertEqual(verified.assignment_sequence, self.assignment.sequence)
        self.assertEqual(self.record.assignment_outcomes, [])
        self.assertEqual(self.record.next_assignment_index, 0)

        replay = self.consumer.consume(
            self.record,
            self.assignment,
            self.work,
            RuntimeExecutionOutcome(
                status=RuntimeExecutionStatus.REPLAYED,
                reason="replayed",
                result_packet=self.result,
                result_packet_digest=document_digest(self.result),
            ),
        )
        self.assertFalse(replay.provider_run_recorded)
        self.assertEqual(len(self.record.provider_runs), 1)

    def test_failed_result_blocks_without_creating_accepted_outcome(self) -> None:
        self.result["status"] = "FAILED"
        progression = self.consumer.consume(
            self.record, self.assignment, self.work, self._runtime_outcome()
        )

        self.assertEqual(progression.terminal_task_status, TaskStatus.BLOCKED)
        self.assertEqual(self.record.status, TaskStatus.BLOCKED)
        self.assertEqual(self.record.assignment_outcomes, [])

    def test_unapproved_or_prohibited_external_effect_fails_closed(self) -> None:
        self.result["side_effects"] = [
            {
                "kind": "DEPLOY",
                "target": "production",
                "status": "COMPLETED",
                "idempotency_key": "bad-effect",
            }
        ]
        with self.assertRaisesRegex(
            PacketResultConsumptionError, "outside packet authority"
        ):
            self.consumer.consume(
                self.record, self.assignment, self.work, self._runtime_outcome()
            )
        self.assertEqual(self.record.provider_runs, [])

    def test_digest_or_provider_identity_drift_fails_closed(self) -> None:
        outcome = self._runtime_outcome()
        bad_digest = RuntimeExecutionOutcome(
            status=outcome.status,
            reason=outcome.reason,
            result_packet=outcome.result_packet,
            result_packet_digest="sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(PacketResultConsumptionError, "digest"):
            self.consumer.consume(self.record, self.assignment, self.work, bad_digest)

        self.result["provider"] = "cursor"
        with self.assertRaisesRegex(PacketResultConsumptionError, "provider"):
            self.consumer.consume(
                self.record, self.assignment, self.work, self._runtime_outcome()
            )


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 04:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | END]]
