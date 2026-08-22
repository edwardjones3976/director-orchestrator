# [[OpenAI Codex | GPT-5.6 | 2026-08-22 16:14 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-22 16:27 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:42 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:51 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 23:01 UTC | CHANGE]]
"""Bounded evidence derived from real legacy records and deterministic helpers."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from director_orchestrator.approvals import (
    apply_failed_predispatch_retry,
    apply_owner_approval,
    failed_retry_decision_digest,
    reset_for_owner_clarification,
)
from director_orchestrator.config import Settings
from director_orchestrator.langgraph_shadow import (
    ShadowReasonCode,
    checkpoint_config,
    run_shadow_comparison,
    snapshot_from_task_record,
)
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    OwnerApproval,
    ProviderName,
    ProviderContactAttempt,
    ProviderRun,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.policy import execution_policy_errors, preview_plan


def _settings(*, paid: bool = True) -> Settings:
    return replace(
        Settings(),
        allow_paid_runs=paid,
        project_allowlist=("demo",),
        repository_allowlist=("example/demo",),
        allowed_starting_refs=("main",),
        max_agent_hops=6,
        max_revisions=2,
        max_task_budget_usd=10.0,
    )


def _plan(
    index: int,
    *,
    project: str = "demo",
    plan_approval: bool = False,
    assignment_approval: bool = False,
    owner_question: str | None = None,
) -> DirectorPlan:
    return DirectorPlan(
        project=project,
        normalized_outcome=f"bounded legacy outcome {index}",
        risk_level=(RiskLevel.LOW if index % 2 == 0 else RiskLevel.MEDIUM),
        approval_required=plan_approval,
        approval_reasons=([] if not plan_approval else ["bounded owner gate"]),
        max_hops=1 + index % 4,
        max_revisions=index % 2,
        assignments=[
            Assignment(
                sequence=1,
                agent=(ProviderName.CLAUDE if index % 2 == 0 else ProviderName.CURSOR),
                task=f"bounded assignment {index}",
                acceptance=["bounded evidence exists"],
                repository="example/demo",
                starting_ref="main",
                requires_approval=assignment_approval,
            )
        ],
        final_acceptance=["bounded workflow accepted"],
        owner_question=owner_question,
    )


def _record(index: int, *, status: TaskStatus, plan: DirectorPlan | None) -> TaskRecord:
    return TaskRecord(
        task_id=f"DIR-EVIDENCE-{index:03d}",
        status=status,
        intake=SlackIntake(
            source_event_id=f"event-{index}",
            channel_id="C-SHADOW",
            thread_ts=f"{index}.000",
            user_id="U-OWNER",
            text=f"legacy intake fixture {index}",
        ),
        plan=plan,
    )


def _legacy_fixture(index: int) -> tuple[str, TaskRecord, Settings, TaskStatus | None]:
    """Build one legacy state, using real approval/recovery helpers where relevant."""

    scenario = index % 15
    settings = _settings()
    plan = _plan(index)
    previous_status: TaskStatus | None = TaskStatus.QUEUED

    if scenario == 0:
        category, status = "queued", TaskStatus.QUEUED
    elif scenario == 1:
        category, status = "planning", TaskStatus.PLANNING
    elif scenario == 2:
        category, status = "running", TaskStatus.RUNNING
    elif scenario == 3:
        category, status = "reviewing", TaskStatus.REVIEWING
    elif scenario == 4:
        category, status = "done", TaskStatus.DONE
    elif scenario == 5:
        category, status = "failed", TaskStatus.FAILED
    elif scenario == 6:
        category, status = "blocked_terminal", TaskStatus.BLOCKED
    elif scenario == 7:
        category, status = "policy_block", TaskStatus.BLOCKED
        plan = _plan(index, project=f"not-allowlisted-{index}")
    elif scenario == 8:
        category, status = "plan_gate", TaskStatus.OWNER_DECISION_REQUIRED
        plan = _plan(index, plan_approval=True)
    elif scenario == 9:
        category, status = "assignment_gate", TaskStatus.OWNER_DECISION_REQUIRED
        plan = _plan(index, assignment_approval=True)
    elif scenario == 10:
        category, status = "assignment_approved", TaskStatus.OWNER_DECISION_REQUIRED
        plan = _plan(index, assignment_approval=True)
    elif scenario == 11:
        category, status = "owner_clarification", TaskStatus.OWNER_DECISION_REQUIRED
        plan = _plan(index, owner_question="bounded clarification")
    elif scenario == 12:
        category, status = "failed_retry_recovery", TaskStatus.OWNER_DECISION_REQUIRED
        plan = _plan(index, assignment_approval=True)
    elif scenario == 13:
        category, status = "provider_unavailable", TaskStatus.OWNER_DECISION_REQUIRED
    else:
        category, status = "dry_run", TaskStatus.OWNER_DECISION_REQUIRED
        settings = _settings(paid=False)
        intake_text = f"Project: demo\nImplement bounded dry-run fixture {index}"
        plan = preview_plan(intake_text, settings)

    record = _record(index, status=status, plan=plan)
    if category == "done":
        record.next_assignment_index = len(record.plan.assignments)
    elif category == "assignment_approved":
        apply_owner_approval(
            record,
            OwnerApproval(owner_user_id="U-OWNER", assignment_sequence=1),
        )
        record.status = TaskStatus.RUNNING
    elif category == "owner_clarification":
        reset_for_owner_clarification(record)
    elif category == "failed_retry_recovery":
        apply_owner_approval(
            record,
            OwnerApproval(owner_user_id="U-OWNER", assignment_sequence=1),
        )
        record.status = TaskStatus.FAILED
        record.error = "bounded predispatch failure"
        apply_failed_predispatch_retry(
            record,
            OwnerApproval(
                owner_user_id="U-OWNER",
                assignment_sequence=1,
                note="Retry after bounded fixture correction",
                retry_decision_digest=failed_retry_decision_digest(record),
            ),
        )
        previous_status = TaskStatus.FAILED
    elif category == "provider_unavailable":
        record.unavailable_providers = [ProviderName.CURSOR]

    return category, record, settings, previous_status


class LangGraphBoundedEvidenceTests(unittest.TestCase):
    def test_one_hundred_five_legacy_projected_workflows_agree_and_stay_isolated(self):
        """Exercise 15 real legacy categories with unique records and plan shapes."""

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            legacy_path = root / "legacy.sqlite3"
            graph_path = root / "langgraph.sqlite3"

            with sqlite3.connect(legacy_path) as legacy:
                legacy.execute(
                    "CREATE TABLE legacy_sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                legacy.execute("INSERT INTO legacy_sentinel(value) VALUES ('unchanged')")

            graph_connection = sqlite3.connect(graph_path, check_same_thread=False)
            saver = SqliteSaver(graph_connection)
            observed_categories: set[str] = set()

            for index in range(105):
                category, record, settings, previous_status = _legacy_fixture(index)
                observed_categories.add(category)
                snapshot = snapshot_from_task_record(
                    record,
                    settings,
                    previous_status=previous_status,
                )
                report = run_shadow_comparison(
                    snapshot,
                    checkpointer=saver,
                    config=checkpoint_config(
                        snapshot.task_id,
                        namespace="bounded-evidence-v1",
                    ),
                )

                with self.subTest(index=index, category=category):
                    self.assertTrue(report.statuses_agree)
                    self.assertTrue(report.classifications_agree)
                    self.assertTrue(report.approvals_agree)
                    self.assertEqual(report.differences, ())
                    self.assertEqual(report.plan_shape_digest, snapshot.plan_shape_digest)
                    if category == "failed_retry_recovery":
                        self.assertEqual(
                            report.transition_reason_code,
                            ShadowReasonCode.RECOVERY,
                        )

            self.assertEqual(
                observed_categories,
                {
                    "queued",
                    "planning",
                    "running",
                    "reviewing",
                    "done",
                    "failed",
                    "blocked_terminal",
                    "policy_block",
                    "plan_gate",
                    "assignment_gate",
                    "assignment_approved",
                    "owner_clarification",
                    "failed_retry_recovery",
                    "provider_unavailable",
                    "dry_run",
                },
            )

            with sqlite3.connect(legacy_path) as legacy:
                self.assertEqual(
                    legacy.execute("SELECT value FROM legacy_sentinel").fetchone(),
                    ("unchanged",),
                )
                self.assertEqual(
                    {
                        row[0]
                        for row in legacy.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    },
                    {"legacy_sentinel"},
                )

            graph_tables = {
                row[0]
                for row in graph_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("checkpoints", graph_tables)
            self.assertNotIn("tasks", graph_tables)
            self.assertNotIn("legacy_sentinel", graph_tables)
            self.assertGreaterEqual(
                graph_connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0],
                105,
            )
            graph_connection.close()

    def test_owner_question_takes_precedence_over_plan_and_assignment_approval_flags(self):
        plan = _plan(
            400,
            plan_approval=True,
            assignment_approval=True,
            owner_question="bounded clarification",
        )
        record = _record(400, status=TaskStatus.OWNER_DECISION_REQUIRED, plan=plan)

        snapshot = snapshot_from_task_record(record, _settings())
        report = run_shadow_comparison(snapshot)

        self.assertEqual(report.transition_reason_code, ShadowReasonCode.OWNER_CLARIFICATION)
        self.assertFalse(report.plan_approval_required)
        self.assertFalse(report.pending_assignment_requires_approval)
        self.assertFalse(report.legacy_approval_required)
        self.assertFalse(report.shadow_approval_required)
        self.assertTrue(report.approvals_agree)
        self.assertTrue(report.statuses_agree)

    def test_owner_assignment_is_clarification_even_when_marked_requires_approval(self):
        plan = _plan(410)
        plan.assignments = [
            Assignment(
                sequence=1,
                agent=ProviderName.OWNER,
                task="provide bounded clarification",
                acceptance=["clarification recorded"],
                requires_approval=True,
            )
        ]
        record = _record(410, status=TaskStatus.OWNER_DECISION_REQUIRED, plan=plan)
        record.unavailable_providers = [ProviderName.CURSOR]
        record.provider_contact_attempts = [
            ProviderContactAttempt(
                assignment_sequence=1,
                provider=ProviderName.OWNER,
                ordinal=1,
                phase="reconciliation",
                outcome="reconciled_not_created",
                error="bounded recovery fixture",
            )
        ]

        snapshot = snapshot_from_task_record(record, _settings())
        report = run_shadow_comparison(snapshot)

        self.assertEqual(report.transition_reason_code, ShadowReasonCode.OWNER_CLARIFICATION)
        self.assertEqual(report.pending_assignment_sequence, 1)
        self.assertFalse(report.pending_assignment_requires_approval)
        self.assertFalse(report.pending_assignment_approved)
        self.assertFalse(report.legacy_approval_required)
        self.assertFalse(report.shadow_approval_required)
        self.assertTrue(report.approvals_agree)
        self.assertTrue(report.statuses_agree)
        self.assertEqual(report.shadow_status, TaskStatus.OWNER_DECISION_REQUIRED)

    def test_plan_gate_precedes_owner_assignment_then_yields_to_clarification(self):
        plan = _plan(415, plan_approval=True)
        plan.assignments = [
            Assignment(
                sequence=1,
                agent=ProviderName.OWNER,
                task="provide bounded clarification after plan approval",
                acceptance=["clarification recorded"],
                requires_approval=True,
            )
        ]
        record = _record(415, status=TaskStatus.OWNER_DECISION_REQUIRED, plan=plan)

        before = run_shadow_comparison(snapshot_from_task_record(record, _settings()))

        self.assertEqual(before.transition_reason_code, ShadowReasonCode.PLAN_APPROVAL_GATE)
        self.assertTrue(before.plan_gate_pending)
        self.assertFalse(before.owner_clarification_pending)
        self.assertTrue(before.legacy_approval_required)
        self.assertTrue(before.shadow_approval_required)
        self.assertTrue(before.approvals_agree)
        self.assertTrue(before.statuses_agree)

        apply_owner_approval(record, OwnerApproval(owner_user_id="U-OWNER"))
        self.assertTrue(record.owner_approved)
        self.assertEqual(record.approved_assignments, [])

        after = run_shadow_comparison(snapshot_from_task_record(record, _settings()))

        self.assertEqual(after.transition_reason_code, ShadowReasonCode.OWNER_CLARIFICATION)
        self.assertFalse(after.plan_gate_pending)
        self.assertTrue(after.owner_clarification_pending)
        self.assertFalse(after.pending_assignment_requires_approval)
        self.assertFalse(after.legacy_approval_required)
        self.assertFalse(after.shadow_approval_required)
        self.assertTrue(after.approvals_agree)
        self.assertTrue(after.statuses_agree)
        self.assertEqual(after.shadow_status, TaskStatus.OWNER_DECISION_REQUIRED)

    def test_more_than_twenty_policy_errors_are_aggregated_without_losing_block(self):
        assignments = [
            Assignment(
                sequence=sequence,
                agent=ProviderName.OPENAI,
                task=f"adversarial bounded assignment {sequence}",
                acceptance=["bounded evidence"],
                repository=f"example/not-allowed-{sequence}",
                starting_ref=f"feature/{sequence}",
                allow_draft_pr=True,
                requires_approval=False,
            )
            for sequence in range(1, 21)
        ]
        plan = DirectorPlan(
            project="not-allowlisted",
            normalized_outcome="adversarial policy cardinality fixture",
            risk_level=RiskLevel.HIGH,
            approval_required=False,
            max_hops=20,
            max_revisions=10,
            assignments=assignments,
            final_acceptance=["bounded evidence"],
        )
        settings = _settings()
        raw_errors = execution_policy_errors(plan, settings)
        self.assertGreater(len(raw_errors), 20)
        record = _record(420, status=TaskStatus.BLOCKED, plan=plan)

        snapshot = snapshot_from_task_record(record, settings)
        report = run_shadow_comparison(snapshot)

        self.assertTrue(snapshot.policy_blocked)
        self.assertEqual(snapshot.policy_error_count, len(raw_errors))
        self.assertEqual(len(snapshot.policy_errors), 20)
        self.assertIsNotNone(snapshot.policy_error_digest)
        self.assertTrue(report.policy_blocked)
        self.assertEqual(report.policy_error_count, len(raw_errors))
        self.assertEqual(report.policy_error_digest, snapshot.policy_error_digest)
        self.assertTrue(report.statuses_agree)
        self.assertEqual(report.shadow_status, TaskStatus.BLOCKED)

    def test_sqlite_checkpoints_exclude_secret_prompt_and_transcript_markers(self):
        markers = (
            "SECRET_MARKER_SHADOW_MUST_NOT_PERSIST",
            "PROMPT_MARKER_SHADOW_MUST_NOT_PERSIST",
            "TRANSCRIPT_MARKER_SHADOW_MUST_NOT_PERSIST",
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            graph_path = Path(directory) / "langgraph.sqlite3"
            plan = _plan(500, project=markers[0].lower())
            plan.normalized_outcome = markers[1]
            plan.assignments[0].task = markers[1]
            record = _record(500, status=TaskStatus.BLOCKED, plan=plan)
            record.intake.text = " ".join(markers)
            record.message = markers[2]
            record.error = markers[0]
            record.provider_runs = [
                ProviderRun(
                    provider=ProviderName.CURSOR,
                    assignment_sequence=1,
                    run_id="bounded-run",
                    status="FINISHED",
                    result=markers[2],
                    raw={"prompt": markers[1], "secret": markers[0]},
                )
            ]

            snapshot = snapshot_from_task_record(record, _settings())
            self.assertEqual(snapshot.transition_reason_code, ShadowReasonCode.POLICY_BLOCK)
            snapshot_json = snapshot.model_dump_json()
            for marker in markers:
                self.assertNotIn(marker, snapshot_json)

            graph_connection = sqlite3.connect(graph_path, check_same_thread=False)
            saver = SqliteSaver(graph_connection)
            report = run_shadow_comparison(
                snapshot,
                checkpointer=saver,
                config=checkpoint_config(snapshot.task_id, namespace="leakage-proof-v1"),
            )
            self.assertTrue(report.statuses_agree)
            graph_connection.commit()

            serialized_rows = b"".join(
                bytes(value) if isinstance(value, bytes) else str(value).encode("utf-8")
                for row in graph_connection.execute("SELECT checkpoint, metadata FROM checkpoints")
                for value in row
            )
            graph_connection.close()
            serialized_database = graph_path.read_bytes()
            for marker in markers:
                encoded = marker.encode("utf-8")
                self.assertNotIn(encoded, serialized_rows)
                self.assertNotIn(encoded, serialized_database)


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-08-22 16:14 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-22 16:27 UTC | END]]
