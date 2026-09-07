# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 04:18 UTC | BEGIN]]
from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.approvals import assignment_approval_digest
from director_orchestrator.langgraph_runtime import (
    PacketBoundaryAdapter,
    RuntimeExecutionOutcome,
    RuntimeExecutionStatus,
)
from director_orchestrator.interactions import require_verified_result
from director_orchestrator.models import (
    AntigravityDirective,
    AntigravityFixtureLane,
    Assignment,
    AssignmentKind,
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
    antigravity_directive_objective,
)
from director_orchestrator.policy import preview_plan


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"
COMMIT_SHA = "a" * 40
BLOB_SHA = "b" * 40


class StaticAuthoritySnapshots:
    def resolve(self, record, assignment):
        return [{"path": "AGENTS.md", "commit_sha": COMMIT_SHA, "blob_sha": BLOB_SHA}]


class MissingAuthoritySnapshots:
    def resolve(self, record, assignment):
        return []


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
        self.assertEqual(packet["limits"]["retry_limit"], 3)
        self.assertEqual(packet["allowed_external_effects"], [
            "WRITE_ISOLATED_BRANCH",
            "OPEN_DRAFT_PULL_REQUEST",
        ])
        self.assertIn("MERGE_PULL_REQUEST", packet["prohibited_actions"])
        self.assertIn(":2:1:", packet["idempotency_key"])

    def test_coordinator_typed_grant_is_the_only_extra_granted_kind(self) -> None:
        granting = DirectorPacketTaskRouter(
            authority_snapshots=StaticAuthoritySnapshots(),
            boundary_adapters={"claude": self.adapter},
            writer_owner_id="langgraph-packet-runtime",
            writer_secret="local-test-secret",
            timeout_seconds=120,
            typed_effect_grant_kinds=("github_mark_ready",),
        )

        granted = granting.work_packet(self.record, self.assignment)
        ungranted = self.router.work_packet(self.record, self.assignment)

        self.assertEqual(
            ["WRITE_ISOLATED_BRANCH", "OPEN_DRAFT_PULL_REQUEST", "github_mark_ready"],
            granted["allowed_external_effects"],
        )
        self.assertNotIn("github_mark_ready", ungranted["allowed_external_effects"])
        # The typed grant never widens the provider's own tool authority.
        self.assertEqual(granted["allowed_tools"], ungranted["allowed_tools"])

        effect_free = self.assignment.model_copy(update={"allow_draft_pr": False})
        record = self.record.model_copy(
            update={
                "plan": self.record.plan.model_copy(
                    update={"assignments": [effect_free]}
                )
            }
        )
        self.assertEqual(
            [], granting.work_packet(record, effect_free)["allowed_external_effects"]
        )

    def test_router_binds_assignment_specific_retry_limit(self) -> None:
        assignment = self.assignment.model_copy(update={"retry_limit": 1})
        record = self.record.model_copy(
            update={"plan": self.record.plan.model_copy(update={"assignments": [assignment]})}
        )

        packet = self.router.work_packet(record, assignment)

        self.assertEqual(packet["limits"]["retry_limit"], 1)
        self.assertEqual(packet["limits"]["max_revisions"], 0)

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

    def test_repository_free_research_binds_internal_authority_for_approval(self) -> None:
        settings = replace(
            Settings(), repository_allowlist=("example/demo",)
        )
        plan = preview_plan(
            "Project: demo\nResearch current public sources.", settings
        )
        record = self.record.model_copy(update={"plan": plan, "plan_generation": 3})
        research = min(plan.assignments, key=lambda item: item.sequence)
        review = max(plan.assignments, key=lambda item: item.sequence)

        packet = self.router.planned_work_packet(record, research)

        self.assertIsNone(research.repository)
        self.assertTrue(research.requires_approval)
        self.assertEqual([research.sequence], review.depends_on)
        self.assertEqual("example/demo", packet["repository"])
        self.assertEqual(research.public_research_query, packet["objective"])
        self.assertEqual(["PUBLIC_WEB_SEARCH"], packet["allowed_tools"])
        self.assertEqual([], packet["allowed_external_effects"])
        self.assertEqual(1, len(packet["approval_requirements"]))
        with self.assertRaisesRegex(PacketRoutingError, "approval is absent or stale"):
            self.router.work_packet(record, research)

        record.approved_assignments = [research.sequence]
        record.approved_assignment_digests = {
            research.sequence: assignment_approval_digest(research)
        }
        self.router.validate_bound_packet(record, research, packet)
        mutated = dict(packet)
        mutated["repository"] = "other/repository"
        with self.assertRaisesRegex(PacketRoutingError, "repository changed"):
            self.router.validate_bound_packet(record, research, mutated)

    def test_repository_free_research_missing_or_mutated_authority_fails_closed(self) -> None:
        settings = replace(
            Settings(), repository_allowlist=("example/demo",)
        )
        plan = preview_plan(
            "Project: demo\nResearch current public sources.", settings
        )
        record = self.record.model_copy(update={"plan": plan})
        research = min(plan.assignments, key=lambda item: item.sequence)
        assert plan.prestage is not None
        mutated_plan = plan.model_copy(
            update={
                "prestage": plan.prestage.model_copy(update={"branch": "release"})
            }
        )
        mutated = record.model_copy(update={"plan": mutated_plan})

        with self.assertRaisesRegex(PacketRoutingError, "pre-stage authority"):
            self.router.planned_work_packet(mutated, research)

        missing_router = DirectorPacketTaskRouter(
            authority_snapshots=MissingAuthoritySnapshots(),
            boundary_adapters={"claude": self.adapter},
            writer_owner_id="langgraph-packet-runtime",
            writer_secret="local-test-secret",
            timeout_seconds=120,
        )
        with self.assertRaisesRegex(PacketRoutingError, "authority snapshot"):
            missing_router.planned_work_packet(record, research)

    def test_closed_antigravity_directive_binds_internal_repository_authority(self) -> None:
        settings = replace(Settings(), repository_allowlist=("example/demo",))
        plan = preview_plan(
            "Project: demo\nProduce a bounded generic module skeleton.", settings
        )
        assert plan.prestage is not None
        antigravity = Assignment(
            sequence=1,
            agent=ProviderName.GEMINI,
            kind=AssignmentKind.SCAFFOLDING,
            task="Produce a bounded generic module skeleton.",
            acceptance=["Return bounded evidence for the assigned lane"],
            repository=None,
            starting_ref=plan.prestage.branch,
            antigravity_directive=AntigravityDirective(
                lane=AntigravityFixtureLane.MODULE_SKELETON
            ),
            requires_approval=False,
            allow_draft_pr=False,
        )
        bound_plan = plan.model_copy(update={"assignments": [antigravity]})
        record = self.record.model_copy(update={"plan": bound_plan})

        packet = self.router.planned_work_packet(record, antigravity)

        # The assignment stays repository-free; only the Director-internal
        # packet authority is reconstructed from the pre-stage boundary.
        self.assertIsNone(antigravity.repository)
        self.assertEqual("example/demo", packet["repository"])
        self.assertEqual([], packet["allowed_external_effects"])
        # The closed directive travels in the objective because it is the only
        # packet field the Antigravity transport is allowed to forward.
        self.assertEqual(
            antigravity_directive_objective(antigravity.antigravity_directive),
            packet["objective"],
        )
        self.assertIn("lane=module_skeleton", packet["objective"])

        undirected = antigravity.model_copy(update={"antigravity_directive": None})
        undirected_record = record.model_copy(
            update={"plan": bound_plan.model_copy(update={"assignments": [undirected]})}
        )
        with self.assertRaisesRegex(PacketRoutingError, "pre-stage authority"):
            self.router.planned_work_packet(undirected_record, undirected)


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

    def test_grok_success_uses_the_authenticated_terminal_status(self) -> None:
        assignment = self.assignment.model_copy(update={"agent": ProviderName.GROK})
        self.record.plan.assignments[0] = assignment
        router = DirectorPacketTaskRouter(
            authority_snapshots=StaticAuthoritySnapshots(),
            boundary_adapters={"grok": PacketBoundaryAdapter("grok", NoopTransport())},
            writer_owner_id="langgraph-packet-runtime",
            writer_secret="local-test-secret",
            timeout_seconds=120,
        )
        work = dict(router.work_packet(self.record, assignment))
        result = copy.deepcopy(self.result)
        for field in (
            "task_id",
            "assignment_id",
            "idempotency_key",
            "authority_snapshot_digest",
            "correlation",
        ):
            result[field] = copy.deepcopy(work[field])
        result["provider"] = "grok"
        result["run_reference"] = "grok-router-run"

        progression = self.consumer.consume(
            self.record, assignment, work, self._runtime_outcome(result)
        )

        self.assertTrue(progression.provider_run_recorded)
        self.assertEqual(self.record.provider_runs[0].provider, ProviderName.GROK)
        self.assertEqual(self.record.provider_runs[0].status, "completed")

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

    def test_not_attempted_proposals_have_no_grant_bypass(self) -> None:
        # A NOT_ATTEMPTED proposal is what drives the typed effect gateway, so
        # it needs the same packet grant as a performed effect.
        for kind in ("github_mark_ready", "DEPLOY", "arbitrary_injected_effect"):
            with self.subTest(kind=kind):
                self.result["side_effects"] = [
                    {
                        "kind": kind,
                        "target": "other/repository#7",
                        "status": "NOT_ATTEMPTED",
                        "idempotency_key": f"proposal-{kind}",
                    }
                ]
                with self.assertRaisesRegex(
                    PacketResultConsumptionError, "outside packet authority"
                ):
                    self.consumer.consume(
                        self.record,
                        self.assignment,
                        self.work,
                        self._runtime_outcome(),
                    )
        self.assertEqual(self.record.provider_runs, [])

    def test_coordinator_grant_makes_one_typed_kind_proposable(self) -> None:
        granted = dict(self.work)
        granted["allowed_external_effects"] = [
            *self.work["allowed_external_effects"],
            "github_mark_ready",
        ]
        self.result["side_effects"] = [
            {
                "kind": "github_mark_ready",
                "target": "example/demo#82",
                "status": "NOT_ATTEMPTED",
                "idempotency_key": "granted-proposal",
            }
        ]

        progression = self.consumer.consume(
            self.record, self.assignment, granted, self._runtime_outcome()
        )

        self.assertTrue(progression.requires_director_review)

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
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
