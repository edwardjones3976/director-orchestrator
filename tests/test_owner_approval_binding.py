from __future__ import annotations

import importlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import director_orchestrator.main as main
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.approvals import (
    StaleApproval,
    approval_button_value,
    approval_decision_digest,
    apply_ambiguous_dispatch_resolution,
    apply_failed_predispatch_retry,
    apply_oracle_recovery,
    apply_owner_approval,
    apply_owner_clarification,
    assignment_approval_digest,
    assignment_is_approved,
    clarification_required,
    clarification_source,
    current_owner_decision,
    failed_retry_decision_digest,
    oracle_recovery_matches,
    parse_approval_button_value,
    plan_gate_pending,
    record_oracle_recovery,
    record_provider_effect_observation,
    reserve_provider_effect,
    reserve_task_effect,
    reset_for_owner_clarification,
    task_effect_key,
)
from director_orchestrator.models import (
    AmbiguousDispatchResolution,
    Assignment,
    AssignmentOutcome,
    DirectorPlan,
    DebateSynthesis,
    OracleRecoveryApproval,
    OwnerApproval,
    OwnerApprovalSource,
    ProviderName,
    ProviderContactAttempt,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskEffectEvidence,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore


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


def _button_approval(
    record: TaskRecord, assignment_sequence: int | None = None
) -> OwnerApproval:
    return OwnerApproval(
        owner_user_id="U0B9UJ86D27",
        note="Approved from Slack",
        source=OwnerApprovalSource.SLACK_BUTTON,
        decision_digest=approval_decision_digest(record, assignment_sequence),
        assignment_sequence=assignment_sequence,
    )


def _retry_approval(
    record: TaskRecord,
    *,
    note: str = "Retry after correcting the request contract",
) -> OwnerApproval:
    assignment = sorted(record.plan.assignments, key=lambda item: item.sequence)[
        record.next_assignment_index
    ]
    return OwnerApproval(
        owner_user_id="U0B9UJ86D27",
        assignment_sequence=assignment.sequence,
        retry_decision_digest=failed_retry_decision_digest(record),
        note=note,
    )


class _FakeBoltApp:
    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def _decorator(*args, **kwargs):
        return lambda function: function

    event = _decorator
    command = _decorator
    action = _decorator


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
        record = _record()
        approval = _button_approval(record)

        apply_owner_approval(record, approval)
        self.assertTrue(record.owner_approved)

        clarification = record.model_copy(deep=True)
        clarification.plan.assignments[0].agent = ProviderName.OWNER
        before_clarification = clarification.model_dump(mode="json")
        with self.assertRaisesRegex(StaleApproval, "cannot provide written clarification"):
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

    def test_fresh_slack_command_remains_valid_for_plan_and_assignment_gates(self):
        record = _record()

        apply_owner_approval(
            record,
            OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                note="Approved",
                source=OwnerApprovalSource.SLACK_COMMAND,
            ),
        )
        self.assertTrue(record.owner_approved)
        self.assertEqual(record.approved_assignments, [])

        apply_owner_approval(
            record,
            OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                note="Approved",
                source=OwnerApprovalSource.SLACK_COMMAND,
            ),
        )
        self.assertTrue(assignment_is_approved(record, record.plan.assignments[0]))


class OwnerApprovalParsingTests(unittest.TestCase):
    def test_unknown_binding_field_is_rejected_by_model_and_http_parser(self):
        payload = {
            "owner_user_id": "U0B9UJ86D27",
            "note": "Approved",
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

    def test_button_binding_round_trips_and_legacy_values_fail_closed(self):
        record = _record()
        value = approval_button_value(record)

        task_id, sequence, digest = parse_approval_button_value(value)

        self.assertEqual(task_id, record.task_id)
        self.assertIsNone(sequence)
        self.assertEqual(digest, approval_decision_digest(record))
        for legacy in (record.task_id, f"{record.task_id}|1", "v1|DIR-X|1|bad"):
            with self.subTest(legacy=legacy):
                with self.assertRaises(StaleApproval):
                    parse_approval_button_value(legacy)

    def test_button_source_requires_complete_binding_and_legacy_note_is_rejected(self):
        with self.assertRaises(ValidationError):
            OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                note="Approved from Slack",
                source=OwnerApprovalSource.SLACK_BUTTON,
            )
        with self.assertRaises(ValidationError):
            OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                note="Approved from Slack",
            )


class AtomicOwnerDecisionTests(unittest.TestCase):
    def test_concurrent_endpoint_requests_return_one_success_and_one_409(self):
        with tempfile.TemporaryDirectory() as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = _record()
            task_store.create(record)
            approval = _button_approval(record)
            test_settings = replace(
                main.settings,
                slack_owner_user_id="U0B9UJ86D27",
            )
            barrier = threading.Barrier(2)
            original_get = task_store.get_versioned

            def synchronized_get(task_id: str):
                versioned = original_get(task_id)
                barrier.wait(timeout=5)
                return versioned

            def attempt() -> tuple[int, int]:
                background = BackgroundTasks()
                try:
                    main.approve(record.task_id, approval, background)
                except HTTPException as exc:
                    return exc.status_code, len(background.tasks)
                return 200, len(background.tasks)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(
                    task_store, "get_versioned", side_effect=synchronized_get
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                outcomes = list(executor.map(lambda _: attempt(), range(2)))

            self.assertEqual(sorted(outcomes), [(200, 1), (409, 0)])
            stored, version = task_store.get_versioned(record.task_id)
            self.assertEqual(stored.status, TaskStatus.QUEUED)
            self.assertEqual(version, 1)

    def test_two_store_instances_consume_one_button_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            first_store = TaskStore(database)
            second_store = TaskStore(database)
            record = _record()
            record.plan_generation = 1
            first_store.create(record)
            approval = _button_approval(record)
            barrier = threading.Barrier(2)

            def attempt(task_store: TaskStore) -> tuple[str, int]:
                background = BackgroundTasks()
                versioned = task_store.get_versioned(record.task_id)
                self.assertIsNotNone(versioned)
                _, expected_version = versioned
                barrier.wait(timeout=5)
                stored = task_store.mutate_if_version(
                    record.task_id,
                    expected_version,
                    lambda current: main._apply_owner_decision(current, approval),
                )
                if stored is None:
                    return "conflict", len(background.tasks)
                background.add_task(main.engine.run, record.task_id)
                return "accepted", len(background.tasks)

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(
                    executor.map(attempt, (first_store, second_store))
                )

            self.assertEqual(sorted(outcomes), [("accepted", 1), ("conflict", 0)])
            stored, version = first_store.get_versioned(record.task_id)
            self.assertEqual(stored.status, TaskStatus.QUEUED)
            self.assertTrue(stored.owner_approved)
            self.assertEqual(version, 1)

    def test_sequential_button_replay_returns_409_without_background_task(self):
        with tempfile.TemporaryDirectory() as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = _record()
            task_store.create(record)
            approval = _button_approval(record)
            test_settings = replace(
                main.settings,
                slack_owner_user_id="U0B9UJ86D27",
            )
            first_background = BackgroundTasks()
            replay_background = BackgroundTasks()

            with patch.object(main, "settings", test_settings), patch.object(
                main, "store", task_store
            ):
                accepted = main.approve(
                    record.task_id, approval, first_background
                )
                with self.assertRaises(HTTPException) as raised:
                    main.approve(record.task_id, approval, replay_background)

            self.assertEqual(accepted.status, TaskStatus.QUEUED)
            self.assertEqual(len(first_background.tasks), 1)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(len(replay_background.tasks), 0)
            _, version = task_store.get_versioned(record.task_id)
            self.assertEqual(version, 1)

    def test_existing_database_is_migrated_to_versioned_writes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "legacy.sqlite3")
            record = _record()
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TABLE tasks (
                        task_id TEXT PRIMARY KEY,
                        source_event_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        record_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO tasks(
                        task_id, source_event_id, status, record_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.task_id,
                        record.intake.source_event_id,
                        record.status.value,
                        record.model_dump_json(),
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )

            task_store = TaskStore(database)
            loaded, version = task_store.get_versioned(record.task_id)
            self.assertEqual(version, 0)
            task_store.save(loaded)
            _, version = task_store.get_versioned(record.task_id)
            self.assertEqual(version, 1)


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

    def test_fresh_exact_assignment_button_is_accepted(self):
        record = _record(owner_approved=True)
        approval = _button_approval(record, 1)

        apply_owner_approval(record, approval)

        self.assertTrue(assignment_is_approved(record, record.plan.assignments[0]))

    def test_reused_sequence_after_rebuild_rejects_old_button_without_mutation(self):
        original = _record(owner_approved=True)
        original.plan_generation = 1
        stale = _button_approval(original, 1)

        rebuilt = _record(owner_approved=True)
        rebuilt.plan_generation = 2
        rebuilt.plan.assignments[0].task = "Different rebuilt assignment scope"
        before = rebuilt.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "no longer matches"):
            apply_owner_approval(rebuilt, stale)

        self.assertEqual(rebuilt.model_dump(mode="json"), before)

    def test_identical_regenerated_plan_rejects_old_plan_button_by_generation(self):
        original = _record()
        original.plan_generation = 1
        stale = _button_approval(original)
        regenerated = _record()
        regenerated.plan_generation = 2
        before = regenerated.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "no longer matches"):
            apply_owner_approval(regenerated, stale)

        self.assertEqual(regenerated.model_dump(mode="json"), before)


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
                    note="Answer from an obsolete assignment prompt",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_synthetic_slack_note_cannot_answer_plan_question(self):
        record = self._answered_record()
        before = record.model_dump(mode="json")
        legacy = OwnerApproval.model_construct(
            owner_user_id="U0B9UJ86D27",
            assignment_sequence=None,
            note="Approved from Slack",
            source=OwnerApprovalSource.API,
            decision_digest=None,
        )

        with self.assertRaisesRegex(StaleApproval, "written clarification"):
            apply_owner_clarification(record, legacy)

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
        self.assertEqual([item.run_id for item in record.provider_run_history], ["discarded-run"])
        self.assertEqual(
            [item.run_id for item in record.assignment_outcome_history],
            ["discarded-run"],
        )
        self.assertEqual(len(record.task_effect_ledger), 1)
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
                    note="Answer from an obsolete assignment prompt",
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

    def test_review_owner_stop_routes_written_answer_even_without_owner_question(self):
        for question in ("Should the implementation change direction?", None):
            with self.subTest(question=question):
                record = _record(owner_approved=True, approved=[1])
                record.review = ReviewDecision(
                    verdict=ReviewVerdict.OWNER,
                    summary="Owner judgment is required",
                    owner_question=question,
                )

                self.assertEqual(clarification_source(record), "review_clarification")
                apply_owner_clarification(
                    record,
                    OwnerApproval(
                        owner_user_id="U0B9UJ86D27",
                        note="Use the conservative implementation path",
                    ),
                )

                self.assertIsNone(record.plan)
                self.assertIn("Use the conservative implementation path", record.intake.text)

    def test_debate_owner_stop_routes_written_answer_with_or_without_question(self):
        for question in ("Choose A or B?", None):
            with self.subTest(question=question):
                record = _record(owner_approved=True, approved=[1])
                record.review = ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Implementation evidence accepted",
                )
                record.debate_tally = {
                    "configured_weight_total": 6.0,
                    "cast_weight_total": 6.0,
                    "option_weights": {"A": 3.0, "B": 3.0},
                    "abstaining": [],
                    "unavailable": [],
                    "winning_options": ["A", "B"],
                    "resolution": "owner_decision_required",
                    "authorizes_external_action": False,
                }
                record.debate_synthesis = DebateSynthesis(
                    decision="The weighted evidence is tied",
                    rationale="Both bounded options have equal support",
                    dissent=[],
                    next_action="Ask the owner",
                    openai_vote_option="A",
                    confidence=0.5,
                    owner_question=question,
                )

                self.assertEqual(clarification_source(record), "debate_clarification")
                apply_owner_clarification(
                    record,
                    OwnerApproval(
                        owner_user_id="U0B9UJ86D27",
                        note="Choose option A",
                    ),
                )

                self.assertIsNone(record.plan)
                self.assertIn("Choose option A", record.intake.text)

    def test_malformed_debate_and_manual_completion_stop_are_not_clarifications(self):
        record = _record(owner_approved=True, approved=[1])
        record.debate_tally = {"resolution": "owner_decision_required"}
        record.message = "Untrusted malformed debate output"
        before = record.model_dump(mode="json")

        self.assertIsNone(current_owner_decision(record))
        with self.assertRaisesRegex(StaleApproval, "not awaiting"):
            apply_owner_approval(
                record,
                OwnerApproval(owner_user_id="U0B9UJ86D27", note="Approved"),
            )
        self.assertEqual(record.model_dump(mode="json"), before)

        manual = _record(owner_approved=True, approved=[1])
        manual.message = "cursor requires manual completion verification"
        before_manual = manual.model_dump(mode="json")
        self.assertIsNone(current_owner_decision(manual))
        with self.assertRaisesRegex(StaleApproval, "not awaiting"):
            apply_owner_approval(
                manual,
                OwnerApproval(owner_user_id="U0B9UJ86D27", note="Approved"),
            )
        self.assertEqual(manual.model_dump(mode="json"), before_manual)


class ImmutableEffectLedgerTests(unittest.TestCase):
    def test_director_reservation_counts_toward_specialist_budget_and_hops(self):
        record = _record(owner_approved=True, approved=[1])
        assignment = record.plan.assignments[0]
        assignment.estimated_cost_usd = 0.5
        director_key = task_effect_key(
            record,
            phase="plan",
            identity={"target_generation": record.plan_generation + 1},
        )
        reserve_task_effect(
            record,
            effect_key=director_key,
            kind="director_intent",
            phase="plan",
            provider=ProviderName.OPENAI,
            estimated_cost_usd=0.75,
            hop_units=1,
            max_task_budget_usd=1.0,
        )
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "Cumulative task budget"):
            reserve_provider_effect(
                record,
                assignment,
                phase="dispatch",
                max_task_budget_usd=1.0,
            )

        self.assertEqual(record.model_dump(mode="json"), before)
        self.assertEqual(record.cumulative_estimated_cost_usd, 0.75)
        self.assertEqual(record.cumulative_effect_hops, 1)

    def test_two_workers_reserve_one_provider_effect_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            first_store = TaskStore(database)
            second_store = TaskStore(database)
            record = _record(owner_approved=True, approved=[1])
            record.status = TaskStatus.QUEUED
            record.plan_generation = 1
            record.plan.assignments[0].estimated_cost_usd = 0.5
            record.approved_assignment_digests[1] = assignment_approval_digest(
                record.plan.assignments[0]
            )
            first_store.create(record)
            test_settings = replace(
                main.settings,
                sqlite_path=database,
                max_task_budget_usd=2.0,
            )
            engines = (
                OrchestrationEngine(test_settings, first_store),
                OrchestrationEngine(test_settings, second_store),
            )
            barrier = threading.Barrier(2)

            def attempt(engine: OrchestrationEngine):
                current = engine.store.get(record.task_id)
                original_get = engine.store.get_versioned

                def synchronized_get(task_id: str):
                    versioned = original_get(task_id)
                    barrier.wait(timeout=5)
                    return versioned

                with patch.object(
                    engine.store, "get_versioned", side_effect=synchronized_get
                ):
                    return engine._reserve_effect_intent(
                        current,
                        current.plan.assignments[0],
                        phase="dispatch",
                    )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(attempt, engines))

            self.assertEqual(sum(result is not None for result in results), 1)
            stored, version = first_store.get_versioned(record.task_id)
            self.assertEqual(version, 1)
            self.assertEqual(stored.status, TaskStatus.RUNNING)
            self.assertEqual(
                [item.kind for item in stored.task_effect_ledger],
                ["provider_intent"],
            )
            self.assertEqual(stored.cumulative_estimated_cost_usd, 0.5)

    def test_restart_and_replan_preserve_intent_and_block_duplicate_effect(self):
        record = _record(owner_approved=True, approved=[1])
        record.plan_generation = 1
        assignment = record.plan.assignments[0]
        assignment.estimated_cost_usd = 0.5
        effect_key = reserve_provider_effect(
            record, assignment, phase="dispatch", max_task_budget_usd=2.0
        )
        record_provider_effect_observation(
            record,
            assignment,
            effect_key=effect_key,
            phase="dispatch",
            outcome="submitted",
            run_id="run-paid-1",
        )
        restarted = TaskRecord.model_validate_json(record.model_dump_json())
        old_plan = restarted.plan.model_copy(deep=True)

        reset_for_owner_clarification(restarted)
        restarted.plan = old_plan
        restarted.plan_generation += 1
        before = restarted.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "repeats a prior"):
            reserve_provider_effect(
                restarted,
                restarted.plan.assignments[0],
                phase="dispatch",
                max_task_budget_usd=2.0,
            )

        self.assertEqual(restarted.model_dump(mode="json"), before)
        self.assertEqual(restarted.cumulative_estimated_cost_usd, 0.5)

    def test_proven_pre_submit_failure_releases_budget_for_one_new_intent(self):
        record = _record(owner_approved=True, approved=[1])
        assignment = record.plan.assignments[0]
        assignment.estimated_cost_usd = 0.75
        first = reserve_provider_effect(
            record, assignment, phase="dispatch", max_task_budget_usd=1.0
        )
        record_provider_effect_observation(
            record,
            assignment,
            effect_key=first,
            phase="dispatch",
            outcome="failed_pre_submit",
        )

        second = reserve_provider_effect(
            record, assignment, phase="dispatch", max_task_budget_usd=1.0
        )

        self.assertNotEqual(first, second)
        self.assertEqual(record.cumulative_estimated_cost_usd, 0.75)

    def test_completed_revision_allows_next_revision_but_not_duplicate_attempt(self):
        record = _record(owner_approved=True, approved=[1])
        assignment = record.plan.assignments[0]
        assignment.estimated_cost_usd = 0.25
        first = reserve_provider_effect(
            record, assignment, phase="revision", max_task_budget_usd=1.0
        )
        record_provider_effect_observation(
            record,
            assignment,
            effect_key=first,
            phase="revision",
            outcome="submitted",
            run_id="revision-1",
        )
        before = record.model_dump(mode="json")
        with self.assertRaisesRegex(StaleApproval, "must not be duplicated"):
            reserve_provider_effect(
                record, assignment, phase="revision", max_task_budget_usd=1.0
            )
        self.assertEqual(record.model_dump(mode="json"), before)

        record.revision_count = 1
        second = reserve_provider_effect(
            record, assignment, phase="revision", max_task_budget_usd=1.0
        )

        self.assertNotEqual(first, second)
        self.assertEqual(
            [item.attempt for item in record.task_effect_ledger if item.kind == "provider_intent"],
            [1, 2],
        )

    def test_effect_ledger_capacity_fails_closed_before_external_intent(self):
        record = _record(owner_approved=True, approved=[1])
        assignment = record.plan.assignments[0]
        assignment.estimated_cost_usd = 0.01
        for index in range(199):
            effect_key = f"{index:064x}"
            record.task_effect_ledger.append(
                TaskEffectEvidence(
                    effect_digest=f"{index + 1:064x}",
                    effect_key=effect_key,
                    kind="provider_observation",
                    phase="dispatch",
                    outcome="failed_pre_submit",
                    plan_generation=0,
                    assignment_sequence=1,
                    assignment_digest=assignment_approval_digest(assignment),
                    provider=ProviderName.CURSOR,
                    attempt=1,
                    estimated_cost_usd=0,
                )
            )
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "lacks room"):
            reserve_provider_effect(
                record, assignment, phase="dispatch", max_task_budget_usd=1.0
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_replacement_plan_cannot_exceed_preserved_cumulative_budget(self):
        record = _record(owner_approved=True, approved=[1])
        record.plan_generation = 1
        original = record.plan.assignments[0]
        original.estimated_cost_usd = 0.75
        first = reserve_provider_effect(
            record, original, phase="dispatch", max_task_budget_usd=1.0
        )
        record_provider_effect_observation(
            record,
            original,
            effect_key=first,
            phase="dispatch",
            outcome="submitted",
            run_id="run-paid-1",
        )

        replacement = record.plan.model_copy(deep=True)
        replacement.assignments[0].task = "Different bounded replacement work"
        replacement.assignments[0].estimated_cost_usd = 0.5
        reset_for_owner_clarification(record)
        record.plan = replacement
        record.plan_generation += 1
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "Cumulative task budget"):
            reserve_provider_effect(
                record,
                record.plan.assignments[0],
                phase="dispatch",
                max_task_budget_usd=1.0,
            )

        self.assertEqual(record.model_dump(mode="json"), before)
        self.assertEqual(record.cumulative_estimated_cost_usd, 0.75)


class OracleRecoveryBindingTests(unittest.TestCase):
    def _stopped_record(self, *, retryable: bool = True) -> TaskRecord:
        record = _record(owner_approved=True, approved=[1])
        record.plan_generation = 3
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=1,
            run_id="oracle-review-run",
            status="FINISHED",
            result='{"artifact_type":"edge_oracle_exact_sha_review/v1"}',
            evidence_source="provider_authenticated",
        )
        record.provider_runs = [run]
        record.review = ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary="Exact candidate accepted",
        )
        record_oracle_recovery(
            record,
            run,
            kind=(
                "automerge_runtime_failed"
                if retryable
                else "merge_policy_stopped"
            ),
            evidence={"status": "recoverable" if retryable else "rejected"},
            artifact_id="artifact-1",
            retryable=retryable,
        )
        return record

    def test_generic_approval_and_stale_recovery_cannot_bypass_oracle_stop(self):
        record = self._stopped_record()
        before = record.model_dump(mode="json")
        self.assertEqual(current_owner_decision(record).kind, "oracle_recovery")

        with self.assertRaisesRegex(StaleApproval, "not awaiting an approvable"):
            apply_owner_approval(
                record, OwnerApproval(owner_user_id="U0B9UJ86D27", note="Approved")
            )
        with self.assertRaisesRegex(StaleApproval, "no longer matches"):
            apply_oracle_recovery(
                record,
                OracleRecoveryApproval(
                    owner_user_id="U0B9UJ86D27",
                    decision_digest="sha256:" + "b" * 64,
                    action="retry_recoverable",
                    note="Retry after restoring GitHub availability",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_exact_recovery_survives_restart_and_reuses_same_run(self):
        record = self._stopped_record()
        approval = OracleRecoveryApproval(
            owner_user_id="U0B9UJ86D27",
            decision_digest=record.oracle_recovery.decision_digest,
            action="retry_recoverable",
            note="Retry the unchanged exact-SHA artifact",
        )

        apply_oracle_recovery(
            record, approval, oracle_attempt_status="RECOVERABLE"
        )
        restarted = TaskRecord.model_validate_json(record.model_dump_json())

        self.assertEqual(restarted.status, TaskStatus.QUEUED)
        self.assertTrue(
            oracle_recovery_matches(
                restarted,
                restarted.plan.assignments[0],
                restarted.provider_runs[0],
            )
        )

    def test_retry_rechecks_oracle_store_recoverability_without_mutation(self):
        record = self._stopped_record()
        approval = OracleRecoveryApproval(
            owner_user_id="U0B9UJ86D27",
            decision_digest=record.oracle_recovery.decision_digest,
            action="retry_recoverable",
            note="Retry only if the artifact remains recoverable",
        )
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "no longer recoverable"):
            apply_oracle_recovery(
                record, approval, oracle_attempt_status="REJECTED"
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_retry_honors_persisted_oracle_not_before_deadline(self):
        record = self._stopped_record()
        deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
        record.oracle_recovery.retry_not_before = deadline
        approval = OracleRecoveryApproval(
            owner_user_id="U0B9UJ86D27",
            decision_digest=record.oracle_recovery.decision_digest,
            action="retry_recoverable",
            note="Retry only after the persisted rate-limit deadline",
        )
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "persisted deadline"):
            apply_oracle_recovery(
                record,
                approval,
                oracle_attempt_status="RECOVERABLE",
                oracle_retry_not_before=deadline,
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_terminal_policy_stop_only_allows_bound_replan_or_cancel(self):
        record = self._stopped_record(retryable=False)
        digest = record.oracle_recovery.decision_digest
        before = record.model_dump(mode="json")
        with self.assertRaisesRegex(StaleApproval, "terminal"):
            apply_oracle_recovery(
                record,
                OracleRecoveryApproval(
                    owner_user_id="U0B9UJ86D27",
                    decision_digest=digest,
                    action="retry_recoverable",
                    note="Do not override policy",
                ),
            )
        self.assertEqual(record.model_dump(mode="json"), before)

        apply_oracle_recovery(
            record,
            OracleRecoveryApproval(
                owner_user_id="U0B9UJ86D27",
                decision_digest=digest,
                action="replan_with_remediation",
                note="Revise the plan to remediate the policy finding",
            ),
        )
        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertIsNone(record.plan)
        self.assertEqual(
            [item.run_id for item in record.provider_run_history],
            ["oracle-review-run"],
        )
        self.assertGreaterEqual(len(record.task_effect_ledger), 2)

    def test_replan_rejects_combined_intake_overflow_without_persisting_bad_row(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            store = TaskStore(database)
            record = self._stopped_record(retryable=False)
            record.intake = record.intake.model_copy(update={"text": "x" * 19_990})
            store.create(record)
            before = record.model_dump(mode="json")

            with self.assertRaisesRegex(StaleApproval, "bounded intake size"):
                apply_oracle_recovery(
                    record,
                    OracleRecoveryApproval(
                        owner_user_id="U0B9UJ86D27",
                        decision_digest=record.oracle_recovery.decision_digest,
                        action="replan_with_remediation",
                        note="a remediation note that cannot fit",
                    ),
                )

            self.assertEqual(record.model_dump(mode="json"), before)
            restarted = TaskStore(database)
            self.assertIsNotNone(restarted.get(record.task_id))
            self.assertEqual(len(restarted.list_recoverable()), 0)

    def test_http_endpoint_consumes_exact_recovery_once_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            first_store = TaskStore(database)
            record = self._stopped_record()
            first_store.create(record)
            restarted_store = TaskStore(database)
            approval = OracleRecoveryApproval(
                owner_user_id="U0B9UJ86D27",
                decision_digest=record.oracle_recovery.decision_digest,
                action="retry_recoverable",
                note="Retry the unchanged exact-SHA artifact after restart",
            )
            test_settings = replace(
                main.settings,
                shared_secret="test-secret",
                slack_owner_user_id="U0B9UJ86D27",
            )
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", restarted_store),
                patch.object(
                    main.oracle_merge_store,
                    "attempt_recovery_state",
                    return_value=("RECOVERABLE", None),
                ),
                patch.object(main.engine, "run", new=run),
                patch.object(main.usage_monitor, "run_forever", new=AsyncMock(return_value=None)),
                TestClient(main.app) as client,
            ):
                accepted = client.post(
                    f"/v1/tasks/{record.task_id}/oracle-recovery",
                    headers={"Authorization": "Bearer test-secret"},
                    json=approval.model_dump(mode="json"),
                )
                replay = client.post(
                    f"/v1/tasks/{record.task_id}/oracle-recovery",
                    headers={"Authorization": "Bearer test-secret"},
                    json=approval.model_dump(mode="json"),
                )

            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()["status"], TaskStatus.QUEUED.value)
            self.assertEqual(replay.status_code, 409)
            run.assert_awaited_once_with(record.task_id)
            stored = restarted_store.get(record.task_id)
            self.assertEqual(stored.status, TaskStatus.QUEUED)
            self.assertTrue(stored.oracle_recovery.approved)


class FailedPredispatchRetryTests(unittest.TestCase):
    def test_retry_resumes_exact_approved_assignment_without_a_run(self):
        record = _record(owner_approved=True, index=1, approved=[1, 2])
        record.status = TaskStatus.FAILED
        record.error = "HTTP 400"

        apply_failed_predispatch_retry(
            record,
            _retry_approval(record),
        )

        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertIsNone(record.error)

    def test_request_claiming_slack_command_cannot_gain_unbound_retry_authority(self):
        record = _record(owner_approved=True, approved=[1])
        record.status = TaskStatus.FAILED
        record.error = "Provider request failed before dispatch"
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "exact pending assignment"):
            apply_failed_predispatch_retry(
                record,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    note="Retry after restoring the provider configuration",
                    source=OwnerApprovalSource.SLACK_COMMAND,
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_direct_api_claiming_slack_command_cannot_retry_without_exact_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = _record(owner_approved=True, approved=[1])
            record.status = TaskStatus.FAILED
            record.error = "Provider request failed before dispatch"
            task_store.create(record)
            before = task_store.get(record.task_id).model_dump(mode="json")
            test_settings = replace(
                main.settings,
                shared_secret="test-secret",
                slack_owner_user_id="U0B9UJ86D27",
            )
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main.engine, "run", new=run),
                patch.object(main.usage_monitor, "run_forever", new=AsyncMock(return_value=None)),
                TestClient(main.app) as client,
            ):
                response = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer test-secret"},
                    json={
                        "owner_user_id": "U0B9UJ86D27",
                        "note": "Retry after restoring the provider configuration",
                        "source": "slack_command",
                    },
                )

            self.assertEqual(response.status_code, 409)
            run.assert_not_awaited()
            self.assertEqual(
                task_store.get(record.task_id).model_dump(mode="json"), before
            )

    def test_unbound_api_retry_remains_rejected_without_mutation(self):
        record = _record(owner_approved=True, approved=[1])
        record.status = TaskStatus.FAILED
        record.error = "Provider request failed before dispatch"
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "exact pending assignment"):
            apply_failed_predispatch_retry(
                record,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    note="Retry after restoring the provider configuration",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_slack_command_handler_reaches_failed_retry_endpoint(self):
        import director_orchestrator.config as config_module
        import slack_bolt

        with tempfile.TemporaryDirectory() as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = _record(owner_approved=True, approved=[1])
            record.task_id = "DIR-ABCD"
            record.status = TaskStatus.FAILED
            record.error = "Provider request failed before dispatch"
            task_store.create(record)
            test_settings = replace(
                main.settings,
                shared_secret="test-secret",
                slack_owner_user_id="U0B9UJ86D27",
                slack_channel_id="C123",
                slack_bot_token="xoxb-test",
                slack_app_token="xapp-test",
                orchestrator_base_url="http://testserver",
            )
            module_name = "director_orchestrator.slack_bot"
            sys.modules.pop(module_name, None)
            with (
                patch.object(config_module, "Settings", return_value=test_settings),
                patch.object(slack_bolt, "App", _FakeBoltApp),
            ):
                slack_bot = importlib.import_module(module_name)

            responses: list[str] = []
            run = AsyncMock(return_value=None)
            try:
                with (
                    patch.object(main, "settings", test_settings),
                    patch.object(main, "store", task_store),
                    patch.object(main.engine, "run", new=run),
                    patch.object(
                        main.usage_monitor,
                        "run_forever",
                        new=AsyncMock(return_value=None),
                    ),
                    TestClient(main.app) as client,
                ):

                    def route_to_endpoint(url, *, headers, timeout, json=None):
                        path = url.removeprefix(test_settings.orchestrator_base_url)
                        if json is None:
                            return client.get(path, headers=headers)
                        return client.post(path, headers=headers, json=json)

                    with (
                        patch.object(
                            slack_bot.httpx, "get", side_effect=route_to_endpoint
                        ),
                        patch.object(
                            slack_bot.httpx, "post", side_effect=route_to_endpoint
                        ),
                    ):
                        slack_bot.command_handler(
                            lambda: None,
                            {
                                "channel_id": "C123",
                                "user_id": "U0B9UJ86D27",
                                "text": (
                                    "approve DIR-ABCD Retry after restoring "
                                    "the provider configuration"
                                ),
                            },
                            None,
                            lambda text, **_: responses.append(text),
                        )
            finally:
                sys.modules.pop(module_name, None)

            stored = task_store.get(record.task_id)
            self.assertEqual(stored.status, TaskStatus.QUEUED)
            self.assertIsNone(stored.error)
            self.assertEqual(
                stored.message, "Retry after restoring the provider configuration"
            )
            self.assertTrue(any("RESUMING" in item for item in responses))
            run.assert_awaited_once_with(record.task_id)

    def test_synthetic_slack_note_cannot_authorize_failed_retry(self):
        record = _record(owner_approved=True, index=1, approved=[1, 2])
        record.status = TaskStatus.FAILED
        record.error = "HTTP 400"
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(StaleApproval, "explicit owner note"):
            apply_failed_predispatch_retry(
                record,
                _retry_approval(record, note="Approved"),
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
                _retry_approval(record, note="Retry after provider verification"),
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
        record.plan.assignments[0].estimated_cost_usd = 0.75
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
        record.collaboration_thread_ts = "123.45"
        record.collaboration_thread_opened = True
        record.collaboration_delivery_errors = ["preserved delivery audit"]

        reset_for_owner_clarification(record)

        self.assertIsNone(record.plan)
        self.assertEqual(record.next_assignment_index, 0)
        self.assertEqual(record.provider_runs, [])
        self.assertEqual(record.assignment_outcomes, [])
        self.assertEqual([item.run_id for item in record.provider_run_history], ["run-1"])
        self.assertEqual(
            [item.run_id for item in record.assignment_outcome_history], ["run-1"]
        )
        self.assertEqual(len(record.provider_contact_attempt_history), 1)
        self.assertEqual(len(record.task_effect_ledger), 1)
        self.assertEqual(record.cumulative_estimated_cost_usd, 0.75)
        self.assertEqual(record.review_handoff_digests, {})
        self.assertEqual(record.provider_contact_attempts, [])
        self.assertEqual(record.unavailable_providers, [])
        self.assertEqual(record.deferred_assignments, [])
        self.assertEqual(record.availability_impacts, [])
        self.assertEqual(record.collaboration_thread_ts, "123.45")
        self.assertTrue(record.collaboration_thread_opened)
        self.assertEqual(
            record.collaboration_delivery_errors, ["preserved delivery audit"]
        )
        self.assertIsNone(record.review)
        self.assertEqual(record.revision_count, 0)
        self.assertFalse(record.owner_approved)
        self.assertEqual(record.approved_assignments, [])
        self.assertEqual(record.approved_assignment_digests, {})
        self.assertIsNone(record.error)


if __name__ == "__main__":
    unittest.main()
