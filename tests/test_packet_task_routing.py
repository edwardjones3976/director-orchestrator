# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:19 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:03 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:07 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:07 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:44 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:45 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | BEGIN]]
# [[xAI Grok; Codex QA fixture correction | grok-4.6; GPT-6 Astra | 2026-09-22 | 22:42 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 04:18 UTC | BEGIN]]
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import patch
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
    AssignmentOutcome,
    DirectorPlan,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.packet_contracts import (
    canonical_json_bytes,
    document_digest,
    load_json_document,
)
from director_orchestrator.packet_results import (
    DirectorPacketResultConsumer,
    PacketResultConsumptionError,
)
from director_orchestrator.packet_routing import (
    COMPACT_PROVIDER_VIEW_SCHEMA,
    DirectorPacketTaskRouter,
    PacketRoutingError,
    antigravity_directive_objective,
    compact_provider_view,
)
from director_orchestrator.policy import preview_plan


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"
COMMIT_SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]
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

    def test_dependent_reviewer_packet_includes_accepted_producer_outcome(self) -> None:
        producer = self.assignment.model_copy(update={"agent": ProviderName.GROK})
        reviewer = self.assignment.model_copy(
            update={
                "sequence": 2,
                "task": "Independently verify the accepted producer answer.",
                "depends_on": [1],
                "allow_draft_pr": False,
            }
        )
        answer = "PRODUCER-ANSWER-EXACT-71c1: paper picks require operator review."
        self.record.plan.assignments = [producer, reviewer]
        self.record.assignment_outcomes = [
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.GROK,
                run_id="synthetic-run",
                result=answer,
                review_summary=answer,
                provider_result_digest="a" * 64,
            )
        ]

        packet = self.router.work_packet(self.record, reviewer)
        encoded = json.dumps(packet)
        dependency = packet["dependencies"][0]

        self.assertEqual(packet["objective"], reviewer.task)
        self.assertEqual(len(packet["dependencies"]), 1)
        self.assertEqual(dependency["assignment_id"], "DIR-ROUTER-1")
        self.assertEqual(set(dependency), {"assignment_id", "accepted_outcome_digest"})
        self.assertTrue(dependency["accepted_outcome_digest"].startswith("sha256:"))
        self.assertIn(answer, encoded)
        self.assertIn("sha256:" + "a" * 64, encoded)
        self.assertEqual(packet["allowed_tools"], ["READ_REPOSITORY", "RUN_TESTS"])
        self.assertEqual(packet["allowed_external_effects"], [])
        self.router.validate_bound_packet(self.record, reviewer, packet)

        independent = self.router.work_packet(
            self.record.model_copy(
                update={
                    "plan": self.record.plan.model_copy(
                        update={"assignments": [self.assignment]}
                    ),
                    "assignment_outcomes": [],
                }
            ),
            self.assignment,
        )
        self.assertEqual(independent["dependencies"], [])
        self.assertNotIn(answer, json.dumps(independent))
        self.router.validate_bound_packet(
            self.record.model_copy(
                update={
                    "plan": self.record.plan.model_copy(
                        update={"assignments": [self.assignment]}
                    ),
                    "assignment_outcomes": [],
                }
            ),
            self.assignment,
            independent,
        )

    def test_validate_bound_packet_rejects_bad_predecessor_context(self) -> None:
        producer = self.assignment.model_copy(update={"agent": ProviderName.GROK})
        reviewer = self.assignment.model_copy(
            update={
                "sequence": 2,
                "task": "Independently verify the accepted producer answer.",
                "depends_on": [1],
                "allow_draft_pr": False,
            }
        )
        answer = "PRODUCER-ANSWER-EXACT-71c1: paper picks require operator review."
        outcome = AssignmentOutcome(
            assignment_sequence=1,
            provider=ProviderName.GROK,
            run_id="synthetic-run",
            result=answer,
            review_summary=answer,
            provider_result_digest="a" * 64,
        )
        self.record.plan.assignments = [producer, reviewer]
        self.record.assignment_outcomes = [outcome]
        packet = self.router.work_packet(self.record, reviewer)
        prefix = DirectorPacketTaskRouter._PREDECESSOR_CONSTRAINT_PREFIX

        altered = dict(packet)
        altered["constraints"] = [
            item.replace(answer, "substituted-producer-answer") if answer in item else item
            for item in packet["constraints"]
        ]
        with self.assertRaisesRegex(PacketRoutingError, "predecessor context changed"):
            self.router.validate_bound_packet(self.record, reviewer, altered)

        missing = dict(packet)
        missing["constraints"] = [
            item for item in packet["constraints"] if not item.startswith(prefix)
        ]
        with self.assertRaisesRegex(PacketRoutingError, "predecessor context changed"):
            self.router.validate_bound_packet(self.record, reviewer, missing)

        duplicated_lines = dict(packet)
        duplicated_lines["constraints"] = [
            *packet["constraints"],
            next(item for item in packet["constraints"] if item.startswith(prefix)),
        ]
        with self.assertRaisesRegex(PacketRoutingError, "predecessor context changed"):
            self.router.validate_bound_packet(self.record, reviewer, duplicated_lines)

        stale = self.record.model_copy(
            update={
                "assignment_outcomes": [
                    outcome.model_copy(
                        update={
                            "result": "stale-producer-answer",
                            "review_summary": "stale-producer-answer",
                            "provider_result_digest": "b" * 64,
                        }
                    )
                ]
            }
        )
        with self.assertRaisesRegex(PacketRoutingError, "dependencies changed"):
            self.router.validate_bound_packet(stale, reviewer, packet)

        duplicated_outcomes = self.record.model_copy(
            update={
                "assignment_outcomes": [
                    outcome,
                    outcome.model_copy(update={"run_id": "synthetic-run-2"}),
                ]
            }
        )
        with self.assertRaisesRegex(PacketRoutingError, "duplicate accepted outcomes"):
            self.router.work_packet(duplicated_outcomes, reviewer)
        with self.assertRaisesRegex(PacketRoutingError, "duplicate accepted outcomes"):
            self.router.validate_bound_packet(duplicated_outcomes, reviewer, packet)

    def test_public_and_closed_lanes_omit_private_predecessor_output(self) -> None:
        settings = replace(Settings(), repository_allowlist=("example/demo",))
        answer = "PRODUCER-ANSWER-EXACT-71c1: paper picks require operator review."
        outcome = AssignmentOutcome(
            assignment_sequence=1,
            provider=ProviderName.GROK,
            run_id="synthetic-run",
            result=answer,
            review_summary=answer,
            provider_result_digest="a" * 64,
        )

        research_plan = preview_plan(
            "Project: demo\nResearch current public sources.", settings
        )
        assert research_plan.prestage is not None
        producer = self.assignment.model_copy(
            update={"starting_ref": research_plan.prestage.branch}
        )
        research = min(research_plan.assignments, key=lambda item: item.sequence).model_copy(
            update={"sequence": 2, "depends_on": [1]}
        )
        research_record = self.record.model_copy(
            update={
                "plan": research_plan.model_copy(
                    update={"assignments": [producer, research]}
                ),
                "assignment_outcomes": [outcome],
            }
        )
        research_packet = self.router.planned_work_packet(research_record, research)
        self.assertNotIn(answer, json.dumps(research_packet))
        self.assertEqual(research.public_research_query, research_packet["objective"])
        self.assertEqual(["PUBLIC_WEB_SEARCH"], research_packet["allowed_tools"])
        self.assertEqual(
            set(research_packet["dependencies"][0]),
            {"assignment_id", "accepted_outcome_digest"},
        )

        closed_plan = preview_plan(
            "Project: demo\nProduce a bounded generic module skeleton.", settings
        )
        assert closed_plan.prestage is not None
        antigravity = Assignment(
            sequence=2,
            agent=ProviderName.GEMINI,
            kind=AssignmentKind.SCAFFOLDING,
            task="Produce a bounded generic module skeleton.",
            acceptance=["Return bounded evidence for the assigned lane"],
            repository=None,
            starting_ref=closed_plan.prestage.branch,
            depends_on=[1],
            antigravity_directive=AntigravityDirective(
                lane=AntigravityFixtureLane.MODULE_SKELETON
            ),
            requires_approval=False,
            allow_draft_pr=False,
        )
        closed_producer = self.assignment.model_copy(
            update={"starting_ref": closed_plan.prestage.branch}
        )
        closed_record = self.record.model_copy(
            update={
                "plan": closed_plan.model_copy(
                    update={"assignments": [closed_producer, antigravity]}
                ),
                "assignment_outcomes": [outcome],
            }
        )
        closed_packet = self.router.planned_work_packet(closed_record, antigravity)
        self.assertNotIn(answer, json.dumps(closed_packet))
        self.assertEqual(
            antigravity_directive_objective(antigravity.antigravity_directive),
            closed_packet["objective"],
        )
        self.assertEqual(
            set(closed_packet["dependencies"][0]),
            {"assignment_id", "accepted_outcome_digest"},
        )


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


def _entry(path: str) -> dict[str, str]:
    return {"path": path, "commit_sha": COMMIT_SHA, "blob_sha": BLOB_SHA}


class RepositoryInventorySnapshots:
    """The shape of the one real run: 1,505 blob entries for a README task."""

    def resolve(self, record, assignment):
        return [_entry("README.md")] + [
            _entry(f"src/director_orchestrator/module_{index:04d}.py")
            for index in range(1_504)
        ]


class CompactProviderViewTests(unittest.TestCase):
    TEXT = "Project: demo\nSummarize README.md for the owner."

    def setUp(self) -> None:
        settings = replace(Settings(), repository_allowlist=("example/demo",))
        self.record = TaskRecord(
            task_id="DIR-COMPACT",
            status=TaskStatus.QUEUED,
            intake=SlackIntake(
                source_event_id="evt-compact",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text=self.TEXT,
            ),
        )
        self.record.plan = preview_plan(self.TEXT, settings)
        router = DirectorPacketTaskRouter(
            authority_snapshots=RepositoryInventorySnapshots(),
            boundary_adapters={},
            writer_owner_id="langgraph-packet-runtime",
            writer_secret="local-test-secret",
            timeout_seconds=120,
        )
        producer = min(self.record.plan.assignments, key=lambda item: item.sequence)
        self.packet = router.planned_work_packet(self.record, producer)
        self.view = compact_provider_view(self.packet)

    def test_named_contents_are_bounded_blob_bound_and_screened(self) -> None:
        """A small task's models are shown its named files' text, or it keeps the button."""

        from director_orchestrator.packet_routing import (
            git_blob_sha,
            small_task_named_contents,
        )

        text = b"# Readme\n\nPlain text.\n"
        files = {"README.md": text, "docs/guide.md": b"Guide.\n"}

        def work(objective, blobs=None, **changes):
            blobs = blobs if blobs is not None else files
            return {
                "objective": objective,
                "allowed_tools": ["READ_REPOSITORY"],
                "allowed_external_effects": [],
                "prohibited_paths": ["docs/private"],
                "authority_snapshot": [
                    {"path": path, "commit_sha": COMMIT_SHA, "blob_sha": git_blob_sha(data)}
                    for path, data in blobs.items()],
                **changes,
            }

        def read_from(blobs):
            by_sha = {git_blob_sha(data): data for data in blobs.values()}
            return lambda sha: by_sha.get(sha)

        with patch("director_orchestrator.packet_routing._BLOB_READER", read_from(files)):
            contents = small_task_named_contents(work("Compare README.md and docs/guide.md."))
            self.assertEqual(["README.md", "docs/guide.md"], [item["path"] for item in contents])
            self.assertEqual(text.decode(), contents[0]["text"])
            self.assertEqual(git_blob_sha(text), contents[0]["blob_sha"])
            view = compact_provider_view(work("Summarize README.md."))
            self.assertEqual(contents[:1], view["contents"])
            # Only a read-only packet carries contents.
            self.assertNotIn("contents", compact_provider_view(
                work("Summarize README.md.", allowed_tools=["READ_REPOSITORY", "RUN_TESTS"])))
            for name, objective in {
                "no_named_path": "Summarize the repository.",
                "missing": "Summarize docs/missing.md.",
                "too_many": " ".join(f"f{index}.md" for index in range(9)),
            }.items():
                with self.subTest(case=name):
                    self.assertIsNone(small_task_named_contents(work(objective)))
        cases = {
            "binary": {"README.md": b"\0binary"},
            "not_utf8": {"README.md": b"\xff\xfe"},
            "over_file_cap": {"README.md": b"x" * 4097},
            "over_total_cap": {"README.md": b"x" * 4096, "a.md": b"y" * 4096, "b.md": b"z"},
            "secret": {"README.md": b"token: ghp_" + b"a" * 30 + b"\n"},
            "credential_gate": {"README.md": b"Rotate the api key here.\n"},
            "personal_gate": {"README.md": b"Their date of birth is listed.\n"},
            # Built at runtime: no committed file holds a credential shape.
            "private_key": {"README.md": b"-" * 5 + b"BEGIN RSA " + b"PRIVATE KEY" + b"-" * 5 + b"\n"},
            "customer_record": {"README.md": b"Customer: Alice Smith\nAddress: 123 Main St\n"},
            "employee_list": {"README.md": b"Employees on the rota this week.\n"},
            "user_email": {"README.md": b"Contact user_email for access.\n"},
            "patient_note": {"README.md": b"patientName was recorded.\n"},
            "phone_field": {"README.md": b"phoneNumber: listed\n"},
            "tabular_file": {"README.md": b"Load export.csv into the sheet.\n"},
            "genealogy": {"README.md": b"The ancestry chart goes back four generations.\n"},
        }
        for name, blobs in cases.items():
            with self.subTest(case=name), patch(
                    "director_orchestrator.packet_routing._BLOB_READER", read_from(blobs)):
                self.assertIsNone(small_task_named_contents(
                    work(" ".join(["Summarize", *blobs]) + ".", blobs)))
        # A prohibited path, a reader that returns other bytes than the hash
        # names, and no reader at all.
        blobs = {"docs/private/x.md": b"x\n"}
        with patch("director_orchestrator.packet_routing._BLOB_READER", read_from(blobs)):
            self.assertIsNone(small_task_named_contents(work("Summarize docs/private/x.md.", blobs)))
        with patch("director_orchestrator.packet_routing._BLOB_READER", lambda sha: b"other"):
            self.assertIsNone(small_task_named_contents(work("Summarize README.md.")))
        with patch("director_orchestrator.packet_routing._BLOB_READER", None):
            self.assertIsNone(small_task_named_contents(work("Summarize README.md.")))

    def test_blob_reads_check_the_size_first_and_stream_a_bounded_read(self) -> None:
        """A large blob is refused on its size, never read into memory."""

        import subprocess as subprocess_module
        import tempfile as tempfile_module
        from director_orchestrator.packet_routing import git_blob_reader

        with tempfile_module.TemporaryDirectory() as root:
            def git(*args, data=None):
                return subprocess_module.run(
                    ("git", "-c", f"safe.directory={root}", *args), cwd=root, check=True,
                    input=data, capture_output=True).stdout.strip().decode()

            git("init", "-q")
            small = git("hash-object", "-w", "--stdin", data=b"small file\n")
            large = git("hash-object", "-w", "--stdin", data=b"x" * 5_000_000)
            reader = git_blob_reader(root)
            self.assertEqual(b"small file\n", reader(small))
            real_popen = subprocess_module.Popen

            def refuse_blob_reads(args, *rest, **kwargs):
                # subprocess.run uses Popen too: only the blob read is refused.
                if "blob" in args:
                    raise AssertionError("large blob was read")
                return real_popen(args, *rest, **kwargs)

            with patch("director_orchestrator.packet_routing.subprocess.Popen",
                       side_effect=refuse_blob_reads):
                self.assertIsNone(reader(large))
            # A size that lies still cannot fill memory: the stream stops at
            # the cap plus one byte and the blob is refused.
            reads, processes = [], []

            class Recording:
                def __init__(self, *args, **kwargs):
                    self._process = real_popen(*args, **kwargs)
                    processes.append(self)
                    original = self._process.stdout.read
                    self._process.stdout.read = lambda size=-1: (
                        reads.append(size) or original(size))
                    self.stdout = self._process.stdout
                    self.stderr = self._process.stderr

                def poll(self):
                    return self._process.poll()

                def kill(self):
                    self._process.kill()

                def wait(self, timeout=None):
                    return self._process.wait(timeout)

                @property
                def returncode(self):
                    return self._process.returncode

            lying = subprocess_module.CompletedProcess((), 0, stdout=b"10\n", stderr=b"")
            with patch("director_orchestrator.packet_routing.subprocess.run",
                       return_value=lying), \
                    patch("director_orchestrator.packet_routing.subprocess.Popen", Recording):
                self.assertIsNone(reader(large))
            self.assertEqual([4097], reads)
            self.assertEqual(1, len(processes))
            self.assertIsNotNone(processes[0].returncode)
            self.assertTrue(processes[0].stdout.closed)

    def test_screens_ignore_case_where_the_format_does(self) -> None:
        """Lower, upper and mixed case of the same identifier or credential."""

        from director_orchestrator.policy import (
            _read_only_request, holds_credential, holds_identifier_format,
            small_task_content_screened)

        dash, colon = "-", ":"
        identifiers = (
            "meet at 123 main st today", "meet at 123 MAIN ST today",
            "meet at 123 Main Street today", "meet at 12 elm ave today",
            "BORN on 04/12/1987", "Dob" + colon + " 1987" + dash + "04" + dash + "12",
            "PASSPORT ab1234567", "passport AB1234567", "id x12345678", "id X12345678",
            "pay gb82 west 1234 5698 7654 32 now", "pay GB82WEST12345698765432 now",
        )
        for text in identifiers:
            with self.subTest(identifier=text):
                self.assertTrue(holds_identifier_format(text))
                self.assertTrue(small_task_content_screened(f"# Notes\n\n{text}.\n"))
        pem = dash * 5 + "begin rsa " + "private key" + dash * 5
        credentials = (
            "BEARER " + "abcDEF123" * 3, "bearer " + "abcDEF123" * 3,
            "AUTHORIZATION" + colon + " Basic YWRtaW46aHVudGVyMg==",
            "authorization" + colon + " Basic YWRtaW46aHVudGVyMg==",
            "PASSWORD=" + "s3cr3t", "Pwd=" + "s3cr3t", "PassWd = " + "s3cr3t", pem,
        )
        for text in credentials:
            with self.subTest(credential=text):
                self.assertTrue(holds_credential(text))
                self.assertTrue(small_task_content_screened(f"# Config\n\n{text}\n"))
                self.assertFalse(_read_only_request(f"Summarize README.md with {text}."))

    def test_credential_bearing_formats_keep_the_button(self) -> None:
        """Built at runtime: no committed file holds a credential shape."""

        from director_orchestrator.policy import (
            _read_only_request, holds_credential, small_task_content_screened)

        dot, colon, at = ".", ":", "@"
        values = {
            "uri_userinfo": "postgres" + colon + "//admin" + colon + "hunter2" + at + "db.internal/app",
            "authorization": "Authorization" + colon + " Basic YWRtaW46aHVudGVyMg==",
            "bearer": "curl with Bearer " + "abcDEF123" * 3,
            "jwt": dot.join(("eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjMifQ", "c2lnbmF0dXJlLXZhbHVl")),
            "aws_key": "key " + "AKIA" + "IOSFODNN7EXAMPLE",
            "aws_session": "key " + "ASIA" + "IOSFODNN7EXAMPLE",
            "connection_string": "Server=db;User Id=app;Pwd=" + "s3cr3t" + ";",
            "env_line": "API_THING=" + "q8Zr2mXv7Lp4Tn9Wk3Hs6Fd1",
            "yaml_line": "token_value: " + "Zk3pQ9vL2mN8xR4tW7yB5cJ1",
        }
        for name, text in values.items():
            with self.subTest(value=name):
                self.assertTrue(holds_credential(text))
                self.assertTrue(small_task_content_screened(f"# Config\n\n{text}\n"))
                self.assertFalse(_read_only_request(f"Summarize README.md with {text}."))
        for text in ("Version 1.2.3 of the package.", "timeout_seconds = 120",
                     "Commit 0b3cdbba19bc650c01687da9d402f01dfb06ce1e was merged."):
            with self.subTest(ordinary=text):
                self.assertFalse(holds_credential(text))

    def test_every_token_format_is_screened_in_files_and_research_queries(self) -> None:
        """Bare tokens, built at runtime: the file-content screen and the public
        research block share one list, so neither misses a prefix."""

        from director_orchestrator.policy import (
            _PUBLIC_RESEARCH_QUERY_BLOCKS, holds_credential, small_task_content_screened)

        body = "Zk3pQ9vL2mN8xR4tW7yB5cJ1aB"
        underscore, dash = "_", "-"
        tokens = {
            "github_fine_grained": "github" + underscore + "pat" + underscore
                                   + "11ABCDEFG0" + underscore + body,
            "github_classic": "gh" + "p" + underscore + body,
            "github_oauth": "gh" + "o" + underscore + body,
            "github_server": "gh" + "s" + underscore + body,
            "openai": "sk" + dash + body,
            "publishable": "pk" + dash + body,
            "restricted": "rk" + dash + body,
            "slack_bot": "xox" + "b" + dash + "1234567890" + dash + body,
            "aws": "AKIA" + "IOSFODNN7EXAMPLE",
            "private_key": dash * 5 + "BEGIN EC " + "PRIVATE KEY" + dash * 5,
        }
        research = dict(_PUBLIC_RESEARCH_QUERY_BLOCKS)["secret-like value"]
        for name, token in tokens.items():
            with self.subTest(token=name):
                self.assertTrue(holds_credential(token))
                self.assertTrue(small_task_content_screened(f"# Notes\n\nuse {token} here\n"))
                self.assertIsNotNone(research.search(f"what is {token} used for"))

    def test_credential_bearing_urls_are_screened_as_a_class(self) -> None:
        """Webhook URLs whose path is the secret, and any URL whose query
        carries a credential parameter, in any case. Built at runtime."""

        import secrets
        from director_orchestrator.policy import holds_credential, small_task_content_screened

        scheme, slash = "https" + ":" + "//", "/"
        secret = secrets.token_urlsafe(18)
        urls = {
            "slack": scheme + "hooks.slack.com" + slash + "services" + slash + "T0" + "AAAA"
                     + slash + "B0" + "BBBB" + slash + secret,
            "discord": scheme + "discord.com" + slash + "api" + slash + "webhooks" + slash
                       + "123" + slash + secret,
            "office": scheme + "contoso.webhook.office.com" + slash + "webhookb2" + slash + secret,
        }
        for name in ("token", "key", "sig", "signature", "access_token", "api_key", "secret",
                     "TOKEN", "Api_Key"):
            urls["query_" + name] = (scheme + "example.org" + slash + "hook?x=1&" + name
                                     + "=" + secret)
        for name, url in urls.items():
            with self.subTest(url=name):
                self.assertTrue(holds_credential(url))
                self.assertTrue(small_task_content_screened(f"# Notes\n\nSee {url} here.\n"))
        for ordinary in (scheme + "example.org" + slash + "docs?page=2",
                         scheme + "hooks.slack.com" + slash + "docs"):
            with self.subTest(ordinary=ordinary):
                self.assertFalse(holds_credential(ordinary))

    def test_an_opaque_high_entropy_token_is_refused_in_file_text(self) -> None:
        """The backstop for credential formats no pattern names."""

        import base64
        import secrets
        import string
        import uuid
        from director_orchestrator.policy import holds_opaque_token, small_task_content_screened

        alphabet = string.ascii_letters + string.digits
        refused = 0
        samples = 0
        for _ in range(200):
            for token in (base64.b64encode(secrets.token_bytes(32)).decode(),
                          base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("="),
                          "".join(secrets.choice(alphabet) for _ in range(40))):
                samples += 1
                refused += holds_opaque_token(f"value: {token}")
        # Random 24-, 32- and 40-character tokens: nearly all are caught.
        self.assertGreaterEqual(refused / samples, 0.97)
        token = "".join(secrets.choice(alphabet) for _ in range(40))
        while not holds_opaque_token(token):
            token = "".join(secrets.choice(alphabet) for _ in range(40))
        self.assertTrue(small_task_content_screened(f"# Notes\n\nuse {token} here\n"))
        # Lower-case hex digests, UUIDs, identifiers and prose never match.
        ordinary = [
            secrets.token_hex(20), secrets.token_hex(32), str(uuid.uuid4()),
            "sha256:" + secrets.token_hex(32), "test_a_blob_read_spends_the_shared_deadline",
            "EntitlementOwnerActionV1", "backup-stageA-20260907T062414Z",
            "com/example/project/blob/" + secrets.token_hex(20) + "/README",
            "DIRECTOR_OPERATING_DIRECTIVE_SHA256=" + secrets.token_hex(32),
        ]
        for text in ordinary:
            with self.subTest(ordinary=text[:40]):
                self.assertFalse(holds_opaque_token(text))

    def test_the_opaque_token_backstop_passes_every_tracked_doc_and_source_file(self) -> None:
        """The negative corpus: this repository's own docs, prompts and source."""

        import subprocess
        from director_orchestrator.policy import holds_opaque_token

        listed = subprocess.run(
            ("git", "ls-files", "docs", "src", "prompts", "README.md", "AGENTS.md"),
            cwd=ROOT, capture_output=True, text=True, check=False)
        if listed.returncode != 0:
            self.skipTest("git is unavailable")
        checked = []
        for name in listed.stdout.split():
            try:
                text = (ROOT / name).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            checked.append(name)
            with self.subTest(file=name):
                self.assertFalse(holds_opaque_token(text))
        self.assertGreater(len(checked), 200)

    def test_ip_addresses_are_found_by_parsing_not_by_shape(self) -> None:
        """Any token the standard library parses as an address, compressed
        IPv6 forms and zones included."""

        from director_orchestrator.policy import holds_identifier_format

        colon, dot = ":", "."
        addresses = {
            "ipv6_compressed_many_groups": colon.join(("2001", "db8", "85a3", "", "8a2e", "370", "7334")),
            "ipv6_full": colon.join(["fd00"] + ["1"] * 7),
            "ipv6_zone": colon.join(("fe80", "", "1")) + "%eth0",
            "ipv6_leading": colon.join(("", "", "ffff", "10.0.0.1")),
            "ipv4": dot.join(("10", "20", "30", "40")),
        }
        for name, value in addresses.items():
            with self.subTest(address=name):
                self.assertTrue(holds_identifier_format(f"reach it at {value} today"))
        for ordinary in ("items[::2]", "std::io::Result", "at 12:30:45 UTC", "x = a[::-1]",
                         "version 1.2.3", "a ratio of 3:4"):
            with self.subTest(ordinary=ordinary):
                self.assertFalse(holds_identifier_format(ordinary))

    def test_phone_numbers_are_a_generic_digit_rule_not_country_formats(self) -> None:
        """9 to 15 digits in single-separated groups, any country; only
        shapes that cannot be phones are exempt."""

        import secrets
        import uuid
        from director_orchestrator.policy import holds_identifier_format

        space, dash = " ", "-"
        phones = {
            "uk_mobile_local": "07700" + space + "900123",
            "uk_international": "+44" + space + "7700" + space + "900123",
            "double_zero_prefix": "0044" + space + "20" + space + "7946" + space + "0958",
            "us_bracketed": "(415)" + space + "555" + dash + "2671",
            "dotted": "030.1234.5678",
            "bare_run": "4915112345678",
        }
        for name, value in phones.items():
            with self.subTest(phone=name):
                self.assertTrue(holds_identifier_format(f"call {value} tonight"))
        ordinary = {
            "iso_datetime": "2026-09-25 17:48:03",
            "iso_date_time_zone": "2026-09-25T17:48:03+00:00",
            "version": "release 10.24.117",
            "uuid": str(uuid.uuid4()),
            "sha": secrets.token_hex(20),
            "short_number": "port 47263 and 8080",
            "test_summary": "Ran 3524 tests in 1484.041s",
            "thread_ts": "1720000000.000001",
        }
        for name, value in ordinary.items():
            with self.subTest(ordinary=name):
                self.assertFalse(holds_identifier_format(value))

    def test_exemptions_only_discard_hits_they_wholly_contain(self) -> None:
        """Every detector reads the original text; an exempt token never
        swallows an overlapping protected format."""

        import uuid
        from director_orchestrator.policy import holds_identifier_format

        for value in ("a12345678", "ab1234567", "a052377", "01.23.45.67.89",
                      "1234.5678.90", "(415) 555-2671"):
            with self.subTest(hit=value):
                self.assertTrue(holds_identifier_format(f"see {value} now"))
        for value in ("1.2.3", "10.24.117", "(2026-09-17 01:02)", "2026-09-25T17:48:03Z",
                      str(uuid.uuid4()), "Ran 3524 tests in 1484.041s"):
            with self.subTest(exempt=value):
                self.assertFalse(holds_identifier_format(value))

    def test_paths_are_validated_as_written_before_canonicalizing(self) -> None:
        from director_orchestrator.packet_routing import path_key, small_task_named_contents

        for bad in ("secret.txt ", " secret.txt", "docs /a.md", "docs/ a.md", "docs./a.md",
                    "a.md.", "a\\b."):
            with self.subTest(path=bad):
                self.assertIsNone(path_key(bad))
        self.assertEqual(("docs", "a.md"), path_key("./docs\\a.md"))
        self.assertEqual(("docs",), path_key("docs/"))
        # A tracked "secret.txt " never answers for "secret.txt".
        work = {"objective": "Summarize secret.txt.", "allowed_tools": ["READ_REPOSITORY"],
                "allowed_external_effects": [],
                "authority_snapshot": [{"path": "secret.txt ", "commit_sha": "a" * 40,
                                        "blob_sha": "b" * 40}]}
        with patch("director_orchestrator.packet_routing._BLOB_READER", lambda sha: b"x"):
            self.assertIsNone(small_task_named_contents(work))

    def test_record_identifiers_never_read_as_personal_identifiers(self) -> None:
        """UUIDs and lower-case hex digests: a group such as "c0395302" is not
        a passport number and "de54b14240fa" is not an IBAN."""

        import secrets
        import uuid
        from director_orchestrator.policy import holds_sensitive_value

        for _ in range(5_000):
            value = str(uuid.uuid4())
            self.assertFalse(holds_sensitive_value(value), value)
        for size in (20, 32):
            for _ in range(2_000):
                value = secrets.token_hex(size)
                self.assertFalse(holds_sensitive_value(value), value)
        # A short hex value in the passport shape is a hit (owner ruling:
        # fail closed); one with three or more letters is not that shape.
        self.assertTrue(holds_sensitive_value("commit a052377"))
        self.assertFalse(holds_sensitive_value("commit 5ab3cdf"))

    def test_request_text_and_file_text_share_one_value_screen(self) -> None:
        """An unlabeled mixed-case token, an address or a phone in the request
        keeps it out of the class, exactly as in a file."""

        import secrets
        import string
        from director_orchestrator.policy import (
            _read_only_request, holds_sensitive_value, small_task_content_screened)

        alphabet = string.ascii_letters + string.digits
        token = "".join(secrets.choice(alphabet) for _ in range(40))
        from director_orchestrator.policy import holds_opaque_token
        while not holds_opaque_token(token):
            token = "".join(secrets.choice(alphabet) for _ in range(40))
        colon = ":"
        for name, value in {
            "opaque_token": token,
            "ipv6": colon.join(("2001", "db8", "", "7334")),
            "phone": "07700 900123",
        }.items():
            with self.subTest(value=name):
                self.assertTrue(holds_sensitive_value(value))
                self.assertTrue(small_task_content_screened(f"# Notes\n\n{value}\n"))
                self.assertFalse(_read_only_request(f"Summarize README.md for {value}."))
        self.assertTrue(_read_only_request("Summarize README.md for the owner."))

    def test_every_vendor_prefix_needs_only_eight_characters(self) -> None:
        """One table; any known prefix plus 8 or more token characters is a
        credential in file text, request text and a research query."""

        import secrets
        import string
        from director_orchestrator.policy import (
            _PUBLIC_RESEARCH_QUERY_BLOCKS, VENDOR_TOKEN_PREFIXES, holds_credential)

        research = dict(_PUBLIC_RESEARCH_QUERY_BLOCKS)["secret-like value"]
        for prefix in ("glpat-", "glptt-", "gldt-", "glrt-", "npm_", "pypi-", "hf_", "xapp-",
                       "xoxe-", "shpat_", "shpss_", "dop_v1_", "doo_v1_", "SG.", "sk_live_",
                       "sk_test_", "rk_live_", "whsec_", "pk_live_", "AIza", "ya29.", "ghr_",
                       "github_pat_", "sq0atp-", "EAAC", "xai-", "sk-ant-", "sk-proj-",
                       "lin_api_", "ntn_", "secret_", "dapi", "ATTA"):
            self.assertIn(prefix, VENDOR_TOKEN_PREFIXES)
        for prefix in VENDOR_TOKEN_PREFIXES:
            for suffix in ("".join(secrets.choice(string.ascii_lowercase) for _ in range(20)),
                           secrets.token_hex(4),
                           "".join(secrets.choice(string.ascii_letters + string.digits)
                                   for _ in range(8))):
                token = prefix + suffix
                with self.subTest(prefix=prefix, suffix=len(suffix)):
                    self.assertTrue(holds_credential(f"use {token} here"))
                    self.assertIsNotNone(research.search(f"what is {token}"))
        # A known prefix before lower-case words is an identifier, not a token.
        for identifier in ("secret_scan_passed", "xai-responses-command.py", "sk-learn-pipeline",
                           "hf_hub_download"):
            with self.subTest(identifier=identifier):
                self.assertFalse(holds_credential(identifier))

    def test_a_generic_prefixed_token_is_a_credential(self) -> None:
        import secrets
        import string
        from director_orchestrator.policy import holds_credential

        alphabet = string.ascii_letters + string.digits

        def mixed(length):
            while True:
                value = "".join(secrets.choice(alphabet) for _ in range(length))
                if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                        and any(c.isdigit() for c in value)):
                    return value

        for token in ("mypat-" + "q" * 16, "deploykey_" + mixed(18), "apitoken-" + "z" * 20,
                      "sk_" + "a" * 16, "acme-" + mixed(20), "vendor_" + mixed(14) + "_x"):
            with self.subTest(token=token[:12]):
                self.assertTrue(holds_credential(f"value {token} end"))
        for identifier in ("task_cancelled_before_dispatch", "token_budget_remaining",
                           "owner_Pro20x_Max5x_not_API_entitlements",
                           "backup-stageA-20260907T062414Z", "gate-ledger-DIR-COMPLETION-EXAMPLE-1-r2",
                           "exact-40-character-PR-head", "task_api_gateway_attested"):
            with self.subTest(identifier=identifier):
                self.assertFalse(holds_credential(identifier))

    def test_the_prefixed_token_rules_pass_every_tracked_doc_and_source_file(self) -> None:
        """The negative corpus. policy.py itself holds the prefix table."""

        import subprocess
        from director_orchestrator.policy import _holds_prefixed_token

        listed = subprocess.run(
            ("git", "ls-files", "docs", "src", "prompts", "README.md", "AGENTS.md"),
            cwd=ROOT, capture_output=True, text=True, check=False)
        if listed.returncode != 0:
            self.skipTest("git is unavailable")
        for name in listed.stdout.split():
            if name == "src/director_orchestrator/policy.py":
                continue
            try:
                text = (ROOT / name).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            with self.subTest(file=name):
                self.assertFalse(_holds_prefixed_token(text))

    def test_a_slow_blob_read_is_ended_at_its_deadline(self) -> None:
        import subprocess as subprocess_module
        import time as time_module
        from director_orchestrator.packet_routing import git_blob_reader

        killed = []

        class Slow:
            def __init__(self, *args, **kwargs):
                self.returncode = None
                self.stderr = None
                self.stdout = SimpleNamespace(
                    read=lambda size=-1: time_module.sleep(3) or b"late", close=lambda: None)

            def poll(self):
                return self.returncode

            def kill(self):
                killed.append(True)
                self.returncode = -9

            def wait(self, timeout=None):
                return self.returncode

        sized = subprocess_module.CompletedProcess((), 0, stdout=b"10\n", stderr=b"")
        reader = git_blob_reader(".", timeout_seconds=0.3)
        started = time_module.monotonic()
        with patch("director_orchestrator.packet_routing.subprocess.run", return_value=sized), \
                patch("director_orchestrator.packet_routing.subprocess.Popen", Slow):
            self.assertIsNone(reader("a" * 40))
        self.assertLess(time_module.monotonic() - started, 2.5)
        self.assertEqual([True], killed)

    def test_a_blob_read_spends_the_shared_deadline_and_reaps_before_returning(self) -> None:
        """Read work shares the deadline; cleanup gets a bounded synchronous wait."""

        import subprocess as subprocess_module
        import threading as threading_module
        import time as time_module
        from director_orchestrator.packet_routing import blob_read_deadline, git_blob_reader

        waits, processes, closed = [], [], []
        ended = threading_module.Event()

        class Stuck:
            def __init__(self, *args, **kwargs):
                self.returncode = None
                self.stdout = SimpleNamespace(
                    read=lambda size=-1: ended.wait(3) and b"late",
                    close=lambda: closed.append("stdout"))
                self.stderr = SimpleNamespace(close=lambda: closed.append("stderr"))
                processes.append(self)

            def poll(self):
                return self.returncode

            def kill(self):
                ended.set()

            def wait(self, timeout=None):
                waits.append(timeout)
                if not ended.is_set():
                    raise AssertionError("child was not killed before wait")
                self.returncode = -9
                return self.returncode

        sized = subprocess_module.CompletedProcess((), 0, stdout=b"10\n", stderr=b"")
        runs = []

        def run(*args, **kwargs):
            runs.append(kwargs["timeout"])
            return sized

        # A 10 s reader inside a 0.3 s caller deadline.
        reader = git_blob_reader(".", timeout_seconds=10)
        started = time_module.monotonic()
        with patch("director_orchestrator.packet_routing.subprocess.run", side_effect=run), \
                patch("director_orchestrator.packet_routing.subprocess.Popen", Stuck), \
                blob_read_deadline(time_module.monotonic() + 0.3):
            self.assertIsNone(reader("a" * 40))
        self.assertLess(time_module.monotonic() - started, 1.5)
        self.assertLessEqual(runs[0], 0.3)
        self.assertEqual([1.0], waits)
        self.assertIsNotNone(processes[0].returncode)
        self.assertEqual(["stdout", "stderr"], closed)
        # A deadline already spent reads nothing at all.
        with patch("director_orchestrator.packet_routing.subprocess.run",
                   side_effect=AssertionError("spawned")), \
                blob_read_deadline(time_module.monotonic() - 1):
            self.assertIsNone(reader("a" * 40))

    def test_a_small_task_view_is_prepared_once_and_sent_as_prepared(self) -> None:
        """The transport sends the prepared bytes; without them it rebuilds with
        contents required and refuses rather than ship hashes only."""

        from director_orchestrator.packet_contracts import document_digest
        from director_orchestrator.packet_routing import (
            SmallTaskViewUnavailable, _PREPARED_VIEWS, git_blob_sha,
            prepare_small_task_view, validated_compact_view)

        text = b"# Notes\n"
        work = {"objective": "Summarize NOTES.md.", "allowed_tools": ["READ_REPOSITORY"],
                "allowed_external_effects": [], "starting_ref": "a" * 40,
                "authority_snapshot_digest": "sha256:" + "b" * 64,
                "authority_snapshot": [{"path": "NOTES.md", "commit_sha": "a" * 40,
                                        "blob_sha": git_blob_sha(text)}]}
        with patch("director_orchestrator.packet_routing.validate_compact_view",
                   side_effect=lambda view: view):
            with patch("director_orchestrator.packet_routing._BLOB_READER", None), \
                    self.assertRaises(SmallTaskViewUnavailable):
                validated_compact_view(work)
            with patch("director_orchestrator.packet_routing._BLOB_READER", lambda sha: text):
                prepared = prepare_small_task_view(work)
            self.assertEqual("# Notes\n", prepared["contents"][0]["text"])
            # A read that now fails still sends the prepared view.
            with patch("director_orchestrator.packet_routing._BLOB_READER",
                       side_effect=AssertionError("rebuilt")):
                self.assertEqual(prepared, validated_compact_view(work))
            _PREPARED_VIEWS.pop(document_digest(work))

    def test_every_small_class_path_match_uses_one_case_insensitive_normalizer(self) -> None:
        """Prohibited, owned and named paths compare by path_key: case,
        separators, ./ and trailing slashes do not matter; ambiguity refuses."""

        from director_orchestrator.packet_routing import (
            compact_provider_view, git_blob_sha, path_key, small_task_named_contents)

        self.assertEqual(("vendor", "license.md"), path_key("Vendor\\License.md"))
        self.assertEqual(path_key("./docs/"), path_key("DOCS"))
        for ambiguous in ("../x", "/etc", "C:/x", "a/*", "", "a/b.", "a /b"):
            with self.subTest(ambiguous=ambiguous):
                self.assertIsNone(path_key(ambiguous))

        text = b"# Vendored\n"
        blob = git_blob_sha(text)

        def work(objective, prohibited, owned=(), inventory=("vendor/license.md",)):
            return {"objective": objective, "allowed_tools": ["READ_REPOSITORY"],
                    "allowed_external_effects": [], "starting_ref": "a" * 40,
                    "authority_snapshot_digest": "sha256:" + "b" * 64,
                    "owned_paths": list(owned), "prohibited_paths": list(prohibited),
                    "authority_snapshot": [{"path": path, "commit_sha": "a" * 40,
                                            "blob_sha": blob} for path in inventory]}

        with patch("director_orchestrator.packet_routing._BLOB_READER", lambda sha: text):
            # Shown when nothing prohibits it.
            self.assertIsNotNone(small_task_named_contents(
                work("Summarize vendor/license.md.", ["docs"])))
            for spelling in ("Vendor", "VENDOR/", "./vendor", ".\\Vendor\\",
                             "vendor/LICENSE.md", "../elsewhere", "C:/abs", "*"):
                with self.subTest(prohibited=spelling):
                    packet = work("Summarize vendor/license.md.", [spelling])
                    self.assertIsNone(small_task_named_contents(packet))
                    self.assertEqual([], compact_provider_view(packet)["authority"]["entries"])
            # A name spelled in another case resolves to the one entry.
            [shown] = small_task_named_contents(work("Summarize Vendor/License.md.", []))
            self.assertEqual("vendor/license.md", shown["path"])
            # Two entries differing only in case: ambiguous, refused.
            self.assertIsNone(small_task_named_contents(work(
                "Summarize vendor/license.md.", [],
                inventory=("vendor/license.md", "Vendor/License.md"))))
            # An owned root matches in any case; an ambiguous one owns nothing.
            owned = work("Summarize the notes.", [], owned=["VENDOR"])
            self.assertEqual(["vendor/license.md"],
                             [e["path"] for e in compact_provider_view(owned)["authority"]["entries"]])

    def test_named_paths_resolve_against_the_snapshot_inventory(self) -> None:
        """Extensionless files count: conventional names and any exact snapshot path."""

        from director_orchestrator.packet_routing import git_blob_sha, small_task_named_contents
        from director_orchestrator.policy import _read_only_request

        files = {"Makefile": b"all:\n\techo ok\n", "Dockerfile": b"FROM scratch\n",
                 "VERSION": b"1.2\n"}
        by_sha = {git_blob_sha(data): data for data in files.values()}

        def work(objective, blobs=files):
            return {"objective": objective, "allowed_tools": ["READ_REPOSITORY"],
                    "allowed_external_effects": [],
                    "authority_snapshot": [
                        {"path": path, "commit_sha": COMMIT_SHA, "blob_sha": git_blob_sha(data)}
                        for path, data in blobs.items()]}

        with patch("director_orchestrator.packet_routing._BLOB_READER", by_sha.get):
            contents = small_task_named_contents(work("Explain the Makefile and VERSION."))
            self.assertEqual(["Makefile", "VERSION"], [item["path"] for item in contents])
            self.assertEqual(["Dockerfile"], [item["path"] for item in small_task_named_contents(
                work("Describe the Dockerfile targets."))])
            # A conventional name the snapshot lacks is a missing file.
            self.assertIsNone(small_task_named_contents(
                work("Explain the Makefile.", {"Dockerfile": files["Dockerfile"]})))
        # The request text names a conventional extensionless file.
        self.assertTrue(_read_only_request("Explain the Makefile."))
        self.assertTrue(_read_only_request("Describe the Dockerfile targets."))

    def test_unlabeled_identifier_formats_in_a_file_keep_the_button(self) -> None:
        """Raw values with no label, in otherwise innocuous files."""

        from director_orchestrator.policy import holds_identifier_format, small_task_content_screened

        # Built at runtime, like any credential- or identity-shaped fixture.
        dash = "-"
        values = {
            "email": "reach " + "jane.doe" + "@" + "example.org" + " for notes",
            "ssn": "ref " + dash.join(("123", "45", "6789")),
            "us_phone": "call (555) 867" + dash + "5309 after lunch",
            "intl_phone": "dial +44 20 7946 0958 today",
            "card_spaced": "code 4111 1111 1111 1111 on file",
            "card_plain": "entry 5555555555554444 recorded",
            "iban": "pay GB82 WEST 1234 5698 7654 32 next",
            "routing": "bank 021000021 listed",
            "ipv4": "host 192.168.10.44 responded",
            "ipv6": "host 2001:0db8:85a3:0000:0000:8a2e:0370:7334 responded",
            "ipv6_short": "host fe80::1 responded",
            "street": "meet at 221 Baker Street at noon",
            "dob": "born on 04/12/1987 in spring",
            "passport": "passport AB1234567 stamped",
            "passport_bare": "id X12345678 stamped",
        }
        for name, text in values.items():
            with self.subTest(value=name):
                self.assertTrue(holds_identifier_format(text))
                self.assertTrue(small_task_content_screened(f"# Notes\n\n{text}.\n"))
        # Ordinary code and docs: versions, commit SHAs, dates, counts, PR
        # numbers and durations do not trip it.
        for text in (
            "Version 1.2.3 of the package.",
            "Commit 0b3cdbba19bc650c01687da9d402f01dfb06ce1e was merged on 2026-09-24.",
            "Ran 3457 tests in 1113.536s.",
            "See pull request 263 and issue 48.",
            "timeout_seconds = 120; retries = 3",
            "Python 3.12, port 8080.",
        ):
            with self.subTest(ordinary=text):
                self.assertFalse(holds_identifier_format(text))

    def test_the_compact_view_has_a_v1_schema_every_view_is_validated_against(self) -> None:
        from director_orchestrator.packet_contracts import (
            PacketContractError,
            validate_compact_view,
        )
        from director_orchestrator.packet_routing import validated_compact_view

        example = json.loads((ROOT / "docs/schemas/director-compact-work-view-v1.example.json")
                             .read_text(encoding="utf-8"))
        validate_compact_view(example)
        self.assertEqual(validate_compact_view(self.view), validated_compact_view(self.packet))
        for name, change in {
            "extra_field": {"unexpected": True},
            "wrong_schema": {"schema_version": "director-compact-work-view/v2"},
            "entries_over_cap": {"authority": {**example["authority"],
                                               "entries": example["authority"]["entries"] * 65}},
            "bad_blob": {"contents": [{**example["contents"][0], "blob_sha": "x"}]},
            "text_over_cap": {"contents": [{**example["contents"][0], "text": "x" * 4097}]},
            "unbound": {"work_packet_digest": "sha256:short"},
        }.items():
            with self.subTest(invalid=name), self.assertRaises(PacketContractError):
                validate_compact_view({**example, **change})
        for field in ("objective", "task_id", "authority", "work_packet_digest"):
            with self.subTest(missing=field), self.assertRaises(PacketContractError):
                validate_compact_view({key: value for key, value in example.items() if key != field})

    def test_small_task_view_is_bounded_and_omits_the_inventory(self) -> None:
        self.assertGreater(len(canonical_json_bytes(self.packet)), 200_000)
        self.assertLess(len(canonical_json_bytes(self.view)), 20_000)
        self.assertNotIn("authority_snapshot", self.view)
        self.assertEqual(
            {
                "commit": self.packet["starting_ref"],
                "digest": self.packet["authority_snapshot_digest"],
                "file_count": 1_505,
                "entries": [_entry("README.md")],
            },
            self.view["authority"],
        )

    def test_view_is_bound_to_the_full_packet_by_digest_and_identity(self) -> None:
        self.assertEqual(COMPACT_PROVIDER_VIEW_SCHEMA, self.view["schema_version"])
        self.assertEqual(document_digest(self.packet), self.view["work_packet_digest"])
        for field in (
            "task_id",
            "assignment_id",
            "idempotency_key",
            "starting_ref",
            "authority_snapshot_digest",
            "correlation",
            "limits",
            "owned_paths",
            "prohibited_paths",
        ):
            with self.subTest(field=field):
                self.assertEqual(self.packet[field], self.view[field])

    def test_entries_follow_owned_paths_and_never_prohibited_paths(self) -> None:
        view = compact_provider_view(
            {
                "objective": "Review the package.",
                "owned_paths": ["src/pkg"],
                "prohibited_paths": ["src/pkg/secret.py"],
                "authority_snapshot": [
                    _entry("src/pkg/a.py"),
                    _entry("src/pkg/secret.py"),
                    _entry("src/pkgx/b.py"),
                ],
            }
        )
        self.assertEqual([_entry("src/pkg/a.py")], view["authority"]["entries"])
        self.assertEqual(3, view["authority"]["file_count"])

    def test_an_attended_repository_wide_view_keeps_a_bounded_inventory(self) -> None:
        """Outside the small class a view that names no path is not left empty."""

        snapshot = [_entry(f"src/m{index}.py") for index in range(100)] + [
            _entry("docs/private/secret.md")]
        view = compact_provider_view({
            "objective": "List repository files.",
            "allowed_tools": ["READ_REPOSITORY", "RUN_TESTS"],
            "allowed_external_effects": [],
            "prohibited_paths": ["docs/private"],
            "authority_snapshot": snapshot,
        })
        self.assertEqual(64, len(view["authority"]["entries"]))
        self.assertEqual(snapshot[:64], view["authority"]["entries"])
        self.assertNotIn(_entry("docs/private/secret.md"), view["authority"]["entries"])
        # A small read-only packet stays on its named paths only.
        small = compact_provider_view({
            "objective": "List repository files.",
            "allowed_tools": ["READ_REPOSITORY"],
            "allowed_external_effects": [],
            "authority_snapshot": snapshot,
        })
        self.assertEqual([], small["authority"]["entries"])

    def test_entries_are_capped(self) -> None:
        view = compact_provider_view(
            {
                "owned_paths": ["src/"],
                "authority_snapshot": [_entry(f"src/m{index}.py") for index in range(100)],
            }
        )
        self.assertEqual(64, len(view["authority"]["entries"]))
        self.assertEqual(100, view["authority"]["file_count"])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 04:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[xAI Grok; Codex QA fixture correction | grok-4.6; GPT-6 Astra | 2026-09-22 | 22:42 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:45 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:44 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:07 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:50 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:07 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:03 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:21 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:17 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:19 UTC | END]]
