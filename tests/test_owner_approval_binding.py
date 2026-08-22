from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import director_orchestrator.main as main
from director_orchestrator.approvals import (
    StaleApproval,
    apply_ambiguous_dispatch_resolution,
    apply_failed_predispatch_retry,
    apply_owner_approval,
    apply_owner_clarification,
    assignment_approval_digest,
    assignment_is_approved,
    clarification_required,
    plan_gate_pending,
    reset_for_owner_clarification,
)
from director_orchestrator.models import (
    AmbiguousDispatchResolution,
    Assignment,
    AssignmentOutcome,
    DirectorPlan,
    OwnerApproval,
    ProviderName,
    ProviderContactAttempt,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
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

    def test_plan_approval_button_cannot_be_replayed_as_clarification_or_retry(self):
        approval = OwnerApproval(
            owner_user_id="U0B9UJ86D27",
            note="Approved from Slack",
        )
        record = _record()

        apply_owner_approval(record, approval)
        self.assertTrue(record.owner_approved)

        clarification = record.model_copy(deep=True)
        clarification.plan.assignments[0].agent = ProviderName.OWNER
        before_clarification = clarification.model_dump(mode="json")
        with self.assertRaisesRegex(StaleApproval, "written clarification"):
            apply_owner_clarification(clarification, approval)
        self.assertEqual(
            clarification.model_dump(mode="json"), before_clarification
        )

        retry = record.model_copy(deep=True)
        retry.status = TaskStatus.FAILED
        retry.error = "HTTP 400"
        before_retry = retry.model_dump(mode="json")
        with self.assertRaisesRegex(StaleApproval, "exact pending assignment"):
            apply_failed_predispatch_retry(retry, approval)
        self.assertEqual(retry.model_dump(mode="json"), before_retry)


class OwnerApprovalParsingTests(unittest.TestCase):
    def test_unknown_binding_field_is_rejected_by_model_and_http_parser(self):
        payload = {
            "owner_user_id": "U0B9UJ86D27",
            "note": "Approved from Slack",
            "assignment_seq": 1,
        }
        with self.assertRaises(ValidationError):
            OwnerApproval.model_validate(payload)

        test_settings = replace(
            main.settings,
            shared_secret="test-secret",
        )
        with (
            patch.object(main, "settings", test_settings),
            patch.object(main.usage_monitor, "run_forever", new=AsyncMock(return_value=None)),
            patch.object(main.engine, "run", new=AsyncMock(return_value=None)),
            patch.object(main.store, "list_recoverable", return_value=[]),
            patch.object(main.store, "get") as get_record,
        ):
            with TestClient(main.app) as client:
                response = client.post(
                    "/v1/tasks/DIR-BIND/approve",
                    headers={"Authorization": "Bearer test-secret"},
                    json=payload,
                )

        self.assertEqual(response.status_code, 422)
        get_record.assert_not_called()


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


class OwnerClarificationTests(unittest.TestCase):
    """A clarification rebuilds the plan, so discarded-plan state must not survive."""

    def _answered_record(self) -> TaskRecord:
        record = _record(owner_approved=True, index=1, approved=[1, 2])
        record.plan.owner_question = "Which repository should the second pass target?"
        record.provider_runs = [
            ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=1,
                run_id="discarded-run",
                status="FINISHED",
                result="discarded evidence",
            )
        ]
        record.assignment_outcomes = [
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.CURSOR,
                run_id="discarded-run",
                result="discarded evidence",
                review_summary="Accepted under the plan the owner then replaced",
            )
        ]
        record.review = ReviewDecision(
            verdict=ReviewVerdict.OWNER,
            summary="Needs an owner answer",
        )
        record.revision_count = 1
        return record

    def test_owner_question_requires_a_written_answer(self):
        record = self._answered_record()
        self.assertTrue(clarification_required(record))

        for note in ("", "   ", "Approved"):
            with self.subTest(note=note):
                with self.assertRaisesRegex(StaleApproval, "written clarification"):
                    apply_owner_clarification(
                        record, OwnerApproval(owner_user_id="U0B9UJ86D27", note=note)
                    )

        self.assertIsNotNone(record.plan)

    def test_stale_assignment_button_cannot_answer_a_later_owner_question(self):
        record = self._answered_record()
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "cannot be bound"):
            apply_owner_clarification(
                record,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    assignment_sequence=1,
                    note="Approved from Slack",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_synthetic_slack_note_cannot_answer_plan_question(self):
        record = self._answered_record()
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "written clarification"):
            apply_owner_clarification(
                record,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    note="Approved from Slack",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_clarification_discards_every_answered_plan_artifact(self):
        # assignment_outcomes are keyed by the discarded plan's sequence numbers.
        # Surviving outcomes could satisfy a rebuilt assignment's depends_on check,
        # or reach it as handoff evidence, from work the new plan never authorized.
        record = self._answered_record()

        apply_owner_clarification(
            record,
            OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                note="Target example/demo on main",
            ),
        )

        self.assertIn("Owner clarification: Target example/demo on main", record.intake.text)
        self.assertIsNone(record.plan)
        self.assertEqual(record.next_assignment_index, 0)
        self.assertEqual(record.provider_runs, [])
        self.assertEqual(record.assignment_outcomes, [])
        self.assertIsNone(record.review)
        self.assertEqual(record.revision_count, 0)
        self.assertFalse(record.owner_approved)
        self.assertEqual(record.approved_assignments, [])

    def test_owner_assignment_also_requires_a_clarification(self):
        record = _record(owner_approved=True)
        record.plan.assignments[0].agent = ProviderName.OWNER

        self.assertTrue(clarification_required(record))

    def test_stale_assignment_button_cannot_answer_pending_owner_assignment(self):
        record = _record(owner_approved=True)
        record.plan.assignments[0].agent = ProviderName.OWNER
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "cannot be bound"):
            apply_owner_clarification(
                record,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    assignment_sequence=2,
                    note="Approved from Slack",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_plan_gate_precedes_owner_assignment_clarification(self):
        record = _record()
        record.plan.assignments[0].agent = ProviderName.OWNER

        self.assertTrue(plan_gate_pending(record))
        self.assertFalse(clarification_required(record))

        apply_owner_approval(record, OwnerApproval(owner_user_id="U0B9UJ86D27"))

        self.assertTrue(record.owner_approved)
        self.assertIsNotNone(record.plan)
        self.assertTrue(clarification_required(record))

    def test_plain_gated_assignment_is_not_a_clarification(self):
        # A gated specialist assignment takes the ordinary approval path, so a bare
        # "Approved" from the Slack button must keep working.
        record = _record(owner_approved=True)

        self.assertFalse(clarification_required(record))

    def test_completed_plan_is_not_a_clarification(self):
        record = _record(owner_approved=True, index=2, approved=[1, 2])

        self.assertFalse(clarification_required(record))


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

    def test_synthetic_slack_note_cannot_authorize_failed_retry(self):
        record = _record(owner_approved=True, index=1, approved=[1, 2])
        record.status = TaskStatus.FAILED
        record.error = "HTTP 400"
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "explicit owner note"):
            apply_failed_predispatch_retry(
                record,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    assignment_sequence=2,
                    note="Approved from Slack",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

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
                session_id="provider-session-123",
            ),
        )

        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertEqual(record.provider_runs[0].run_id, "provider-run-123")
        self.assertEqual(record.provider_runs[0].status, "RECONCILED_PENDING")
        self.assertIsNone(record.provider_runs[0].result)
        self.assertEqual(record.provider_runs[0].evidence_source, "unverified")
        self.assertEqual(record.provider_contact_attempts[-1].outcome, "reconciled_created")

    def test_created_reconciliation_cannot_supply_status_result_or_url(self):
        with self.assertRaises(ValidationError):
            AmbiguousDispatchResolution.model_validate(
                {
                    "owner_user_id": "U0B9UJ86D27",
                    "assignment_sequence": 1,
                    "resolution": "created",
                    "note": "Provider dashboard shows the run",
                    "run_id": "provider-run-123",
                    "session_id": "provider-session-123",
                    "run_status": "FINISHED",
                    "result": "fabricated provider result",
                    "url": "https://provider.invalid/runs/123",
                }
            )

    def test_created_perplexity_reconciliation_fails_closed(self):
        record = self.ambiguous_record()
        record.plan.assignments[0].agent = ProviderName.PERPLEXITY
        record.provider_contact_attempts[0].provider = ProviderName.PERPLEXITY

        with self.assertRaisesRegex(StaleApproval, "cannot be recovered"):
            apply_ambiguous_dispatch_resolution(
                record,
                AmbiguousDispatchResolution(
                    owner_user_id="U0B9UJ86D27",
                    assignment_sequence=1,
                    resolution="created",
                    note="Provider dashboard shows the run",
                    run_id="perplexity-run-123",
                ),
            )

    def test_created_revision_reconciliation_counts_only_confirmed_revision(self):
        record = self.ambiguous_record()
        record.provider_contact_attempts[0].phase = "revision"

        apply_ambiguous_dispatch_resolution(
            record,
            AmbiguousDispatchResolution(
                owner_user_id="U0B9UJ86D27",
                assignment_sequence=1,
                resolution="created",
                note="Provider dashboard shows the revision",
                run_id="provider-revision-123",
                session_id="provider-session-123",
            ),
        )

        self.assertEqual(record.revision_count, 1)


class OwnerClarificationResetTests(unittest.TestCase):
    def test_discarded_plan_clears_all_sequence_scoped_execution_state(self):
        record = _record(owner_approved=True, approved=[1])
        record.provider_runs = [
            ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=1,
                run_id="run-1",
                status="FINISHED",
                result="old result",
                evidence_source="provider_authenticated",
            )
        ]
        record.assignment_outcomes = [
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.CURSOR,
                run_id="run-1",
                result="old result",
                review_summary="old acceptance",
            )
        ]
        record.review_handoff_digests = {2: "a" * 64}
        record.provider_contact_attempts = [
            ProviderContactAttempt(
                assignment_sequence=1,
                provider=ProviderName.CURSOR,
                ordinal=1,
                phase="poll",
                outcome="no_response",
                run_id="run-1",
                error="timeout",
            )
        ]
        record.unavailable_providers = [ProviderName.CURSOR]
        record.deferred_assignments = [1]
        record.availability_impacts = ["old confidence impact"]
        record.review = ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary="old review",
        )
        record.revision_count = 1
        record.error = "old error"

        reset_for_owner_clarification(record)

        self.assertIsNone(record.plan)
        self.assertEqual(record.next_assignment_index, 0)
        self.assertEqual(record.provider_runs, [])
        self.assertEqual(record.assignment_outcomes, [])
        self.assertEqual(record.review_handoff_digests, {})
        self.assertEqual(record.provider_contact_attempts, [])
        self.assertEqual(record.unavailable_providers, [])
        self.assertEqual(record.deferred_assignments, [])
        self.assertEqual(record.availability_impacts, [])
        self.assertIsNone(record.review)
        self.assertEqual(record.revision_count, 0)
        self.assertFalse(record.owner_approved)
        self.assertEqual(record.approved_assignments, [])
        self.assertEqual(record.approved_assignment_digests, {})
        self.assertIsNone(record.error)


if __name__ == "__main__":
    unittest.main()
