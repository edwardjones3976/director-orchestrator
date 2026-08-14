from __future__ import annotations

import unittest

from director_orchestrator.approvals import (
    StaleApproval,
    apply_owner_approval,
    plan_gate_pending,
)
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    OwnerApproval,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)


def _record(*, owner_approved: bool = False, index: int = 0, approved: list[int] | None = None) -> TaskRecord:
    return TaskRecord(
        task_id="DIR-BIND",
        status=TaskStatus.OWNER_DECISION_REQUIRED,
        intake=SlackIntake(
            source_event_id="evt-bind",
            channel_id="C123",
            thread_ts="123.45",
            user_id="U123",
            text="DIRECTOR Project: demo Outcome: two gated steps",
        ),
        plan=DirectorPlan(
            project="demo",
            normalized_outcome="two gated steps",
            risk_level=RiskLevel.HIGH,
            approval_required=True,
            approval_reasons=["merge or protected-branch change"],
            max_hops=4,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CURSOR,
                    task="First gated step",
                    acceptance=["Evidence exists"],
                    repository="example/demo",
                    requires_approval=True,
                ),
                Assignment(
                    sequence=2,
                    agent=ProviderName.CURSOR,
                    task="Second gated step",
                    acceptance=["Evidence exists"],
                    repository="example/demo",
                    requires_approval=True,
                ),
            ],
            final_acceptance=["Both assignments accepted"],
        ),
        next_assignment_index=index,
        owner_approved=owner_approved,
        approved_assignments=list(approved or []),
    )


class PlanGateSeparationTests(unittest.TestCase):
    def test_plan_gate_approval_does_not_clear_the_first_assignment(self):
        # The plan prompt lists only approval_reasons -- it never shows what
        # assignment 1 will do -- so clearing it must not authorize assignment 1.
        record = _record()
        self.assertTrue(plan_gate_pending(record))

        apply_owner_approval(record, OwnerApproval(owner_user_id="U0B9UJ86D27"))

        self.assertTrue(record.owner_approved)
        self.assertEqual(record.approved_assignments, [])

    def test_assignment_approval_after_plan_gate_binds_that_assignment(self):
        record = _record(owner_approved=True)
        self.assertFalse(plan_gate_pending(record))

        apply_owner_approval(
            record, OwnerApproval(owner_user_id="U0B9UJ86D27", assignment_sequence=1)
        )

        self.assertEqual(record.approved_assignments, [1])

    def test_ungated_plan_still_binds_the_pending_assignment(self):
        # approval_required False with a gated assignment: the engine stops at
        # the assignment gate directly, so the approval must bind it or the task
        # would loop asking forever.
        record = _record()
        record.plan.approval_required = False
        self.assertFalse(plan_gate_pending(record))

        apply_owner_approval(record, OwnerApproval(owner_user_id="U0B9UJ86D27"))

        self.assertEqual(record.approved_assignments, [1])


class StaleButtonTests(unittest.TestCase):
    def test_button_for_an_earlier_assignment_is_rejected(self):
        # Engine has advanced to assignment 2; the owner clicks the assignment-1
        # button still sitting in the Slack channel.
        record = _record(owner_approved=True, index=1, approved=[1])

        with self.assertRaises(StaleApproval):
            apply_owner_approval(
                record, OwnerApproval(owner_user_id="U0B9UJ86D27", assignment_sequence=1)
            )

        self.assertEqual(record.approved_assignments, [1])

    def test_button_matching_the_pending_assignment_is_accepted(self):
        record = _record(owner_approved=True, index=1, approved=[1])

        apply_owner_approval(
            record, OwnerApproval(owner_user_id="U0B9UJ86D27", assignment_sequence=2)
        )

        self.assertEqual(record.approved_assignments, [1, 2])

    def test_button_is_rejected_when_no_assignment_is_pending(self):
        record = _record(owner_approved=True, index=2, approved=[1, 2])

        with self.assertRaises(StaleApproval):
            apply_owner_approval(
                record, OwnerApproval(owner_user_id="U0B9UJ86D27", assignment_sequence=2)
            )


if __name__ == "__main__":
    unittest.main()
