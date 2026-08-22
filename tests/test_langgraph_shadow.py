# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-22 16:10 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:12 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:27 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:42 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:51 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 23:12 UTC | CHANGE]]
from __future__ import annotations

import json
import unittest

from langgraph.checkpoint.memory import InMemorySaver

from director_orchestrator.langgraph_shadow import (
    SHADOW_SCHEMA_VERSION,
    STATUS_CLASSIFICATIONS,
    STATUS_NODE_NAMES,
    TERMINAL_STATUSES,
    ShadowReasonCode,
    ShadowTaskSnapshot,
    ShadowTerminalClassification,
    build_shadow_graph,
    checkpoint_config,
    comparison_report,
    run_shadow_comparison,
)
from director_orchestrator.models import TaskStatus


class LangGraphShadowTests(unittest.TestCase):
    def test_every_task_status_has_an_explicit_node_and_classification(self):
        self.assertEqual(set(TaskStatus), set(STATUS_NODE_NAMES))
        self.assertEqual(set(TaskStatus), set(STATUS_CLASSIFICATIONS))

        expected = {
            TaskStatus.QUEUED: (ShadowTerminalClassification.NON_TERMINAL, False),
            TaskStatus.PLANNING: (ShadowTerminalClassification.NON_TERMINAL, False),
            TaskStatus.RUNNING: (ShadowTerminalClassification.NON_TERMINAL, False),
            TaskStatus.REVIEWING: (ShadowTerminalClassification.NON_TERMINAL, False),
            TaskStatus.DONE: (ShadowTerminalClassification.SUCCEEDED, True),
            TaskStatus.BLOCKED: (ShadowTerminalClassification.BLOCKED, True),
            TaskStatus.FAILED: (ShadowTerminalClassification.FAILED, True),
            TaskStatus.OWNER_DECISION_REQUIRED: (
                ShadowTerminalClassification.OWNER_DECISION_REQUIRED,
                True,
            ),
        }

        for status, (classification, terminal) in expected.items():
            with self.subTest(status=status):
                report = run_shadow_comparison(
                    ShadowTaskSnapshot(
                        task_id=f"DIR-SHADOW-{status.value}",
                        status=status,
                        previous_status=TaskStatus.QUEUED,
                        transition_reason_code=ShadowReasonCode.LEGACY_OBSERVATION,
                        plan_approval_required=status == TaskStatus.OWNER_DECISION_REQUIRED,
                        observed_legacy_approval_required=(
                            status == TaskStatus.OWNER_DECISION_REQUIRED
                        ),
                    )
                )
                self.assertEqual(report.schema_version, SHADOW_SCHEMA_VERSION)
                self.assertEqual(report.legacy_status, status)
                self.assertEqual(report.shadow_status, status)
                self.assertEqual(report.legacy_classification, classification)
                self.assertEqual(report.shadow_classification, classification)
                self.assertEqual(report.legacy_is_terminal, terminal)
                self.assertEqual(report.shadow_is_terminal, terminal)
                self.assertTrue(report.statuses_agree)
                self.assertTrue(report.classifications_agree)
                self.assertEqual(report.differences, ())
                self.assertEqual(
                    report.visited_nodes,
                    (
                        "observe_legacy_status",
                        "policy_projection",
                        "approval_projection",
                        STATUS_NODE_NAMES[status],
                        "compare_with_legacy",
                    ),
                )

        self.assertEqual(TERMINAL_STATUSES, {status for status, (_, terminal) in expected.items() if terminal})

    def test_compiled_graph_exposes_named_nodes_and_edges(self):
        drawable = build_shadow_graph().get_graph()
        node_names = set(drawable.nodes)
        edge_pairs = {(edge.source, edge.target) for edge in drawable.edges}

        self.assertIn("observe_legacy_status", node_names)
        self.assertIn("policy_projection", node_names)
        self.assertIn("approval_projection", node_names)
        self.assertIn("compare_with_legacy", node_names)
        self.assertTrue(set(STATUS_NODE_NAMES.values()).issubset(node_names))
        self.assertIn(("observe_legacy_status", "policy_projection"), edge_pairs)
        self.assertIn(("policy_projection", "approval_projection"), edge_pairs)
        for node_name in STATUS_NODE_NAMES.values():
            self.assertIn(("approval_projection", node_name), edge_pairs)
            self.assertIn((node_name, "compare_with_legacy"), edge_pairs)

    def test_same_snapshot_produces_the_same_compact_report(self):
        snapshot = ShadowTaskSnapshot(
            task_id="DIR-DETERMINISTIC",
            status=TaskStatus.REVIEWING,
            previous_status=TaskStatus.RUNNING,
            transition_reason_code=ShadowReasonCode.LEGACY_OBSERVATION,
        )

        first = run_shadow_comparison(snapshot)
        second = run_shadow_comparison(snapshot)

        self.assertEqual(first, second)
        self.assertNotIn("provider", first.model_dump())
        self.assertNotIn("prompt", first.model_dump())
        self.assertNotIn("artifact", first.model_dump())

    def test_invalid_graph_inputs_fail_before_status_routing(self):
        valid = ShadowTaskSnapshot(task_id="DIR-VALID", status=TaskStatus.QUEUED).graph_input()
        invalid_states = (
            ({**valid, "schema_version": "director-shadow/v2"}, "unsupported shadow schema"),
            ({**valid, "task_id": ""}, "task_id must be"),
            ({**valid, "legacy_status": "UNKNOWN"}, "unknown legacy TaskStatus"),
            ({**valid, "previous_status": "UNKNOWN"}, "unknown previous TaskStatus"),
            ({**valid, "transition_reason_code": "secret prompt prose"}, "unknown transition reason code"),
            ({**valid, "plan_shape_digest": "bad"}, "plan_shape_digest must be"),
            ({**valid, "policy_errors": ["duplicate", "duplicate"]}, "duplicate codes"),
            ({**valid, "policy_errors": ["This is policy prose"]}, "compact lowercase codes"),
            ({**valid, "policy_blocked": True}, "must match policy_error_count"),
            ({**valid, "plan_approval_required": 1}, "must be booleans"),
            (
                {**valid, "pending_assignment_requires_approval": True},
                "require pending_assignment_sequence",
            ),
        )
        graph = build_shadow_graph()

        for state, message in invalid_states:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    graph.invoke(state)

    def test_comparison_report_rejects_an_interrupted_state(self):
        graph = build_shadow_graph(interrupt_after=["status_running"])
        state = graph.invoke(
            ShadowTaskSnapshot(task_id="DIR-INCOMPLETE", status=TaskStatus.RUNNING).graph_input()
        )

        self.assertEqual(
            state["visited_nodes"],
            ["observe_legacy_status", "policy_projection", "approval_projection", "status_running"],
        )
        with self.assertRaisesRegex(ValueError, "shadow comparison is incomplete"):
            comparison_report(state)

    def test_caller_injected_saver_resumes_to_the_same_comparison(self):
        snapshot = ShadowTaskSnapshot(
            task_id="DIR-RESTART",
            status=TaskStatus.OWNER_DECISION_REQUIRED,
            previous_status=TaskStatus.RUNNING,
            transition_reason_code=ShadowReasonCode.PLAN_APPROVAL_GATE,
        )
        config = checkpoint_config(snapshot.task_id, namespace="test-shadow")
        self.assertEqual(config["configurable"]["thread_id"], "test-shadow:DIR-RESTART")
        saver = InMemorySaver()

        first_process = build_shadow_graph(
            checkpointer=saver,
            interrupt_after=["status_owner_decision_required"],
        )
        interrupted = first_process.invoke(snapshot.graph_input(), config=config)
        self.assertEqual(
            interrupted["visited_nodes"],
            [
                "observe_legacy_status",
                "policy_projection",
                "approval_projection",
                "status_owner_decision_required",
            ],
        )

        restarted_process = build_shadow_graph(checkpointer=saver)
        resumed = restarted_process.invoke(None, config=config)
        resumed_report = comparison_report(resumed)
        uninterrupted_report = run_shadow_comparison(snapshot)

        self.assertEqual(resumed_report, uninterrupted_report)
        self.assertEqual(saver.get_tuple(config).checkpoint["channel_values"]["task_id"], snapshot.task_id)

    def test_comparison_report_preserves_a_visible_mismatch(self):
        completed = build_shadow_graph().invoke(
            ShadowTaskSnapshot(task_id="DIR-MISMATCH", status=TaskStatus.RUNNING).graph_input()
        )
        completed["shadow_status"] = TaskStatus.FAILED.value
        completed["shadow_classification"] = ShadowTerminalClassification.FAILED.value
        completed["shadow_is_terminal"] = True
        completed["statuses_agree"] = False
        completed["classifications_agree"] = False
        completed["differences"] = (
            "status mismatch: legacy=RUNNING, shadow=FAILED",
            "classification mismatch: legacy=non_terminal, shadow=failed",
            "terminal mismatch: legacy=False, shadow=True",
        )

        report = comparison_report(completed)

        self.assertFalse(report.statuses_agree)
        self.assertFalse(report.classifications_agree)
        self.assertEqual(len(report.differences), 3)

    def test_dry_run_plan_approval_gate_agrees_with_legacy_stop(self):
        digest = "a" * 64
        report = run_shadow_comparison(
            ShadowTaskSnapshot(
                task_id="DIR-PLAN-GATE",
                status=TaskStatus.OWNER_DECISION_REQUIRED,
                previous_status=TaskStatus.PLANNING,
                plan_shape_digest=digest,
                plan_approval_required=True,
                owner_approved=False,
                observed_legacy_approval_required=True,
            )
        )

        self.assertEqual(report.plan_shape_digest, digest)
        self.assertTrue(report.plan_gate_pending)
        self.assertTrue(report.legacy_approval_required)
        self.assertTrue(report.shadow_approval_required)
        self.assertTrue(report.approvals_agree)
        self.assertTrue(report.statuses_agree)

    def test_pending_assignment_approval_gate_agrees_with_legacy_stop(self):
        report = run_shadow_comparison(
            ShadowTaskSnapshot(
                task_id="DIR-ASSIGNMENT-GATE",
                status=TaskStatus.OWNER_DECISION_REQUIRED,
                previous_status=TaskStatus.RUNNING,
                pending_assignment_sequence=2,
                pending_assignment_requires_approval=True,
                pending_assignment_approved=False,
                observed_legacy_approval_required=True,
            )
        )

        self.assertFalse(report.plan_gate_pending)
        self.assertTrue(report.assignment_gate_pending)
        self.assertTrue(report.approvals_agree)
        self.assertTrue(report.statuses_agree)

    def test_approval_comparison_exposes_independent_legacy_mismatch(self):
        report = run_shadow_comparison(
            ShadowTaskSnapshot(
                task_id="DIR-APPROVAL-MISMATCH",
                status=TaskStatus.OWNER_DECISION_REQUIRED,
                plan_approval_required=True,
                owner_approved=False,
                observed_legacy_approval_required=False,
            )
        )

        self.assertFalse(report.legacy_approval_required)
        self.assertTrue(report.shadow_approval_required)
        self.assertFalse(report.approvals_agree)
        self.assertIn(
            "approval mismatch: legacy=False, shadow=True",
            report.differences,
        )

    def test_owner_clarification_stop_is_not_misclassified_as_approval(self):
        report = run_shadow_comparison(
            ShadowTaskSnapshot(
                task_id="DIR-CLARIFICATION",
                status=TaskStatus.OWNER_DECISION_REQUIRED,
                previous_status=TaskStatus.PLANNING,
                transition_reason_code=ShadowReasonCode.OWNER_CLARIFICATION,
            )
        )

        self.assertFalse(report.plan_gate_pending)
        self.assertFalse(report.assignment_gate_pending)
        self.assertFalse(report.legacy_approval_required)
        self.assertFalse(report.shadow_approval_required)
        self.assertTrue(report.approvals_agree)
        self.assertTrue(report.statuses_agree)
        self.assertEqual(report.shadow_status, TaskStatus.OWNER_DECISION_REQUIRED)

    def test_policy_errors_project_blocked_and_expose_divergence(self):
        report = run_shadow_comparison(
            ShadowTaskSnapshot(
                task_id="DIR-POLICY-BLOCK",
                status=TaskStatus.RUNNING,
                policy_errors=("repository_not_allowlisted",),
                policy_blocked=True,
                policy_error_count=1,
                policy_error_digest="d" * 64,
            )
        )

        self.assertTrue(report.policy_blocked)
        self.assertEqual(report.policy_errors, ("repository_not_allowlisted",))
        self.assertEqual(report.policy_error_count, 1)
        self.assertEqual(report.shadow_status, TaskStatus.BLOCKED)
        self.assertFalse(report.statuses_agree)
        self.assertTrue(report.approvals_agree)
        self.assertIn("status mismatch: legacy=RUNNING, shadow=BLOCKED", report.differences)

    def test_approved_plan_and_assignment_gates_allow_progression(self):
        report = run_shadow_comparison(
            ShadowTaskSnapshot(
                task_id="DIR-APPROVED",
                status=TaskStatus.RUNNING,
                plan_shape_digest="b" * 64,
                plan_approval_required=True,
                owner_approved=True,
                pending_assignment_sequence=3,
                pending_assignment_requires_approval=True,
                pending_assignment_approved=True,
            )
        )

        self.assertFalse(report.plan_gate_pending)
        self.assertFalse(report.assignment_gate_pending)
        self.assertFalse(report.shadow_approval_required)
        self.assertTrue(report.approvals_agree)
        self.assertEqual(report.shadow_status, TaskStatus.RUNNING)
        self.assertTrue(report.statuses_agree)

    def test_checkpoint_state_is_compact_and_contains_no_plan_or_provider_content(self):
        snapshot = ShadowTaskSnapshot(
            task_id="DIR-COMPACT",
            status=TaskStatus.OWNER_DECISION_REQUIRED,
            plan_shape_digest="c" * 64,
            plan_approval_required=True,
        )
        saver = InMemorySaver()
        config = checkpoint_config(snapshot.task_id, namespace="compact-shadow")
        graph = build_shadow_graph(checkpointer=saver)

        graph.invoke(snapshot.graph_input(), config=config)
        values = graph.get_state(config).values
        serialized = json.dumps(values, sort_keys=True)

        self.assertEqual(values["plan_shape_digest"], "c" * 64)
        self.assertLess(len(serialized), 2_048)
        for forbidden in ("prompt", "objective", "assignment_task", "provider_payload", "full_plan"):
            self.assertNotIn(forbidden, values)


if __name__ == "__main__":
    unittest.main()


# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]
