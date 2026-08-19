from __future__ import annotations

import unittest

from director_orchestrator.approvals import (
    StaleApproval,
    apply_ambiguous_dispatch_resolution,
    apply_failed_predispatch_retry,
    apply_owner_approval,
    assignment_approval_digest,
    assignment_is_approved,
    plan_gate_pending,
)
from director_orchestrator.models import (
    AmbiguousDispatchResolution,
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


def _record(*, owner_approved: bool = False, index: int = 0, approved: list[int] | None = None) -> TaskRecord:
    record = TaskRecord(
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
    record.approved_assignment_digests = {
        sequence: assignment_approval_digest(
            next(item for item in record.plan.assignments if item.sequence == sequence)
        )
        for sequence in record.approved_assignments
    }
    return record


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
        self.assertTrue(assignment_is_approved(record, record.plan.assignments[0]))

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


class FailedPredispatchRetryTests(unittest.TestCase):
    def test_retry_resumes_exact_approved_assignment_without_a_run(self):
        record = _record(owner_approved=True, index=1, approved=[1, 2])
        record.status = TaskStatus.FAILED
        record.error = "HTTP 400"

        apply_failed_predispatch_retry(
            record,
            OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                assignment_sequence=2,
                note="Retry after correcting the request contract",
            ),
        )

        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertIsNone(record.error)

    def test_retry_refuses_when_a_provider_run_already_exists(self):
        record = _record(owner_approved=True, index=1, approved=[1, 2])
        record.status = TaskStatus.FAILED
        record.provider_runs = [
            ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=2,
                run_id="remote-run",
                status="RUNNING",
            )
        ]

        with self.assertRaisesRegex(StaleApproval, "duplicate dispatch"):
            apply_failed_predispatch_retry(
                record,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    assignment_sequence=2,
                    note="Retry",
                ),
            )


class AmbiguousDispatchReconciliationTests(unittest.TestCase):
    def ambiguous_record(self) -> TaskRecord:
        record = _record(owner_approved=True, index=0, approved=[1])
        record.provider_contact_attempts.append(
            ProviderContactAttempt(
                assignment_sequence=1,
                provider=ProviderName.CURSOR,
                ordinal=1,
                phase="dispatch",
                outcome="ambiguous_dispatch",
                error="ReadTimeout: outcome unknown",
            )
        )
        return record

    def test_normal_approval_cannot_redispatch_after_ambiguous_result(self):
        record = self.ambiguous_record()

        with self.assertRaisesRegex(StaleApproval, "must be reconciled"):
            apply_owner_approval(
                record,
                OwnerApproval(owner_user_id="U0B9UJ86D27", assignment_sequence=1),
            )

    def test_owner_can_confirm_no_run_was_created_before_one_new_dispatch(self):
        record = self.ambiguous_record()

        apply_ambiguous_dispatch_resolution(
            record,
            AmbiguousDispatchResolution(
                owner_user_id="U0B9UJ86D27",
                assignment_sequence=1,
                resolution="not_created",
                note="Provider dashboard confirms no run exists",
            ),
        )

        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertEqual(record.provider_contact_attempts[-1].outcome, "reconciled_not_created")
        self.assertEqual(record.provider_runs, [])

    def test_owner_can_adopt_the_existing_provider_run_without_redispatch(self):
        record = self.ambiguous_record()

        apply_ambiguous_dispatch_resolution(
            record,
            AmbiguousDispatchResolution(
                owner_user_id="U0B9UJ86D27",
                assignment_sequence=1,
                resolution="created",
                note="Provider dashboard shows the run",
                run_id="provider-run-123",
                run_status="RUNNING",
                url="https://provider.invalid/runs/123",
            ),
        )

        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertEqual(record.provider_runs[0].run_id, "provider-run-123")
        self.assertEqual(record.provider_contact_attempts[-1].outcome, "reconciled_created")


if __name__ == "__main__":
    unittest.main()
