# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:41 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:10 UTC | BEGIN]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 09:57 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:53 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:21 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:02 UTC | BEGIN]]
from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import director_orchestrator.main as main
from director_orchestrator.callbacks import apply_claude_callback
from director_orchestrator.config import Settings
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
    build_slack_intake_attestation,
    build_slack_gateway_proof,
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
    SLACK_INTAKE_ISSUED_AT_HEADER,
    SLACK_INTAKE_NONCE_HEADER,
    SLACK_INTAKE_SIGNATURE_HEADER,
    task_effect_key,
    verify_slack_intake_attestation,
)
from director_orchestrator.models import (
    AmbiguousDispatchResolution,
    Assignment,
    AssignmentOutcome,
    CollaborationTurn,
    ClaudeCallback,
    DebateCounterBias,
    DirectorPlan,
    DebateSynthesis,
    DebateTieJudgment,
    IntakeAuthentication,
    OracleRecoveryApproval,
    OpaqueEvidenceDigest,
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
    TieRubricApplication,
    canonical_task_effect_digest,
    canonical_opaque_identifier_digest,
)
from director_orchestrator.parallel_dispatch import DailyTaskBudgetLedger
from director_orchestrator.store import (
    PrivacyCleanupPending,
    StaleTaskWrite,
    TaskStore,
)
from tests.plan_binding_helpers import seed_production_plan_materialization


def _record(*, owner_approved: bool = False, index: int = 0, approved: list[int] | None = None) -> TaskRecord:
    record = TaskRecord(
        task_id="DIR-BIND",
        status=TaskStatus.OWNER_DECISION_REQUIRED,
        intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
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


def _signed_slack_approval(
    record: TaskRecord,
    version: int,
    approval: OwnerApproval,
    *,
    secret: str = "gateway-test-secret-32-characters!!",
    shared_secret: str = "public-test-secret",
    channel_id: str = "C123",
    nonce: str | None = None,
    issued_at: int | None = None,
) -> OwnerApproval:
    proof = build_slack_gateway_proof(
        secret,
        shared_secret,
        record,
        version,
        approval,
        channel_id=channel_id,
        path=f"/v1/tasks/{record.task_id}/approve",
        nonce=nonce,
        issued_at=issued_at,
    )
    return approval.model_copy(update={"slack_gateway_proof": proof})


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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = _record()
            task_store.create(record)
            test_settings = replace(
                main.settings,
                slack_owner_user_id="U0B9UJ86D27",
                slack_channel_id="C123",
                slack_gateway_secret="gateway-test-secret-32-characters!!",
            )
            current, version = task_store.get_versioned(record.task_id)
            approval = _signed_slack_approval(
                current, version, _button_approval(current)
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
            with sqlite3.connect(task_store.path) as connection:
                nonce_count = connection.execute(
                    "SELECT COUNT(*) FROM slack_gateway_nonces"
                ).fetchone()[0]
            self.assertEqual(nonce_count, 1)

    def test_two_store_instances_consume_one_button_once(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = _record()
            task_store.create(record)
            test_settings = replace(
                main.settings,
                slack_owner_user_id="U0B9UJ86D27",
                slack_channel_id="C123",
                slack_gateway_secret="gateway-test-secret-32-characters!!",
            )
            current, version = task_store.get_versioned(record.task_id)
            approval = _signed_slack_approval(
                current, version, _button_approval(current)
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
            self.assertEqual(version, 1)
            task_store.save(loaded)
            _, version = task_store.get_versioned(record.task_id)
            self.assertEqual(version, 2)


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
    def test_clarification_enforces_intake_limit_before_mutation_and_restarts(self):
        note = "A"
        suffix = f"\n\nOwner clarification: {note}"
        record = _record()
        record.plan.approval_required = False
        record.plan.approval_reasons = []
        record.plan.owner_question = "Which option?"
        record.intake.text = "x" * (20_000 - len(suffix))
        apply_owner_clarification(
            record,
            OwnerApproval(owner_user_id="U0B9UJ86D27", note=note),
        )
        self.assertEqual(len(record.intake.text), 20_000)

        with tempfile.TemporaryDirectory() as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            task_store.create(record)
            restarted = TaskStore(task_store.path).get(record.task_id)
            self.assertEqual(restarted.intake.text, record.intake.text)

        overflow = _record()
        overflow.plan.approval_required = False
        overflow.plan.approval_reasons = []
        overflow.plan.owner_question = "Which option?"
        overflow.intake.text = "x" * (20_001 - len(suffix))
        before = overflow.model_dump(mode="json")
        with self.assertRaisesRegex(StaleApproval, "20000-character"):
            apply_owner_clarification(
                overflow,
                OwnerApproval(owner_user_id="U0B9UJ86D27", note=note),
            )
        self.assertEqual(overflow.model_dump(mode="json"), before)

        with tempfile.TemporaryDirectory() as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            task_store.create(overflow)
            persisted_before, version_before = task_store.get_versioned(
                overflow.task_id
            )
            with self.assertRaisesRegex(StaleApproval, "20000-character"):
                main._consume_owner_decision(
                    task_store,
                    overflow.task_id,
                    OwnerApproval(owner_user_id="U0B9UJ86D27", note=note),
                )
            persisted_after, version_after = task_store.get_versioned(
                overflow.task_id
            )
            self.assertEqual(version_after, version_before)
            self.assertEqual(
                persisted_after.model_dump(mode="json"),
                persisted_before.model_dump(mode="json"),
            )

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
        self.assertEqual(len(record.provider_run_history), 1)
        self.assertEqual(len(record.assignment_outcome_history), 1)
        self.assertNotIn("discarded-run", record.model_dump_json())
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
            seed_production_plan_materialization(record)
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
                original_mutate = engine.store.mutate_if_version

                def synchronized_mutate(task_id, expected_version, mutator, **kwargs):
                    barrier.wait(timeout=5)
                    return original_mutate(
                        task_id, expected_version, mutator, **kwargs
                    )

                with patch.object(
                    engine.store, "mutate_if_version", side_effect=synchronized_mutate
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
                ["director_intent", "director_observation", "provider_intent"],
            )
            self.assertEqual(stored.cumulative_estimated_cost_usd, 0.5)

    def test_late_provider_worker_cannot_overwrite_effect_owner_result(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            owner_store = TaskStore(database)
            observer_store = TaskStore(database)
            record = _record(owner_approved=True, approved=[1])
            record.status = TaskStatus.QUEUED
            record.plan_generation = 1
            assignment = record.plan.assignments[0]
            assignment.estimated_cost_usd = 0.5
            record.approved_assignment_digests[1] = assignment_approval_digest(
                assignment
            )
            seed_production_plan_materialization(record)
            owner_store.create(record)
            settings = replace(
                main.settings,
                sqlite_path=database,
                max_task_budget_usd=2.0,
            )
            owner_engine = OrchestrationEngine(settings, owner_store)
            observer_engine = OrchestrationEngine(settings, observer_store)
            owner_snapshot = owner_store.get(record.task_id)
            start = threading.Barrier(2)
            owner_reserved = threading.Event()
            observer_checked = threading.Event()
            owner_saved = threading.Event()

            def own_and_finish() -> None:
                start.wait(timeout=5)
                reservation = owner_engine._reserve_effect_intent(
                    owner_snapshot, assignment, phase="dispatch"
                )
                self.assertIsNotNone(reservation)
                owned, effect_key = reservation
                owner_reserved.set()
                self.assertTrue(observer_checked.wait(timeout=5))
                record_provider_effect_observation(
                    owned,
                    assignment,
                    effect_key=effect_key,
                    phase="dispatch",
                    outcome="submitted",
                    run_id="winner-run",
                )
                owned.provider_runs.append(
                    ProviderRun(
                        provider=assignment.agent,
                        assignment_sequence=assignment.sequence,
                        run_id="winner-run",
                        status="FINISHED",
                        result="winner-result",
                    )
                )
                owned.status = TaskStatus.REVIEWING
                owner_store.save(owned)
                owner_saved.set()

            def observe_late() -> object:
                start.wait(timeout=5)
                self.assertTrue(owner_reserved.wait(timeout=5))
                observer_snapshot, version_before = observer_store.get_versioned(
                    record.task_id
                )
                try:
                    result = observer_engine._reserve_effect_intent(
                        observer_snapshot, assignment, phase="dispatch"
                    )
                    _, version_after = observer_store.get_versioned(record.task_id)
                    self.assertEqual(version_after, version_before)
                    return result
                finally:
                    observer_checked.set()

            with ThreadPoolExecutor(max_workers=2) as executor:
                owner_future = executor.submit(own_and_finish)
                observer_future = executor.submit(observe_late)
                owner_future.result(timeout=10)
                self.assertIsNone(observer_future.result(timeout=10))

            stored, version = TaskStore(database).get_versioned(record.task_id)
            self.assertEqual(version, 2)
            self.assertEqual(stored.status, TaskStatus.REVIEWING)
            self.assertEqual(stored.provider_runs[0].run_id, "winner-run")
            self.assertEqual(stored.provider_runs[0].result, "winner-result")
            self.assertEqual(
                [item.kind for item in stored.task_effect_ledger],
                [
                    "director_intent",
                    "director_observation",
                    "provider_intent",
                    "provider_observation",
                ],
            )

    def test_late_revision_worker_cannot_overwrite_effect_owner_result(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "director.sqlite3")
            owner_store = TaskStore(database)
            observer_store = TaskStore(database)
            record = _record(owner_approved=True, approved=[1])
            record.status = TaskStatus.REVIEWING
            record.plan_generation = 1
            record.plan.max_revisions = 1
            assignment = record.plan.assignments[0]
            assignment.estimated_cost_usd = 0.25
            record.approved_assignment_digests[1] = assignment_approval_digest(
                assignment
            )
            record.provider_runs = [
                ProviderRun(
                    provider=assignment.agent,
                    assignment_sequence=assignment.sequence,
                    run_id="initial-run",
                    status="FINISHED",
                    result="initial-result",
                )
            ]
            seed_production_plan_materialization(record)
            owner_store.create(record)
            settings = replace(
                main.settings,
                sqlite_path=database,
                max_task_budget_usd=2.0,
            )
            owner_engine = OrchestrationEngine(settings, owner_store)
            observer_engine = OrchestrationEngine(settings, observer_store)
            owner_snapshot = owner_store.get(record.task_id)
            start = threading.Barrier(2)
            owner_reserved = threading.Event()
            observer_checked = threading.Event()
            owner_saved = threading.Event()

            def own_and_finish() -> None:
                start.wait(timeout=5)
                reservation = owner_engine._reserve_effect_intent(
                    owner_snapshot, assignment, phase="revision"
                )
                self.assertIsNotNone(reservation)
                owned, effect_key = reservation
                owner_reserved.set()
                self.assertTrue(observer_checked.wait(timeout=5))
                record_provider_effect_observation(
                    owned,
                    assignment,
                    effect_key=effect_key,
                    phase="revision",
                    outcome="submitted",
                    run_id="revised-run",
                )
                owned.provider_runs[-1] = ProviderRun(
                    provider=assignment.agent,
                    assignment_sequence=assignment.sequence,
                    run_id="revised-run",
                    status="FINISHED",
                    result="revised-result",
                )
                owned.status = TaskStatus.REVIEWING
                owner_store.save(owned)
                owner_saved.set()

            def observe_late() -> object:
                start.wait(timeout=5)
                self.assertTrue(owner_reserved.wait(timeout=5))
                observer_snapshot, version_before = observer_store.get_versioned(
                    record.task_id
                )
                try:
                    result = observer_engine._reserve_effect_intent(
                        observer_snapshot, assignment, phase="revision"
                    )
                    _, version_after = observer_store.get_versioned(record.task_id)
                    self.assertEqual(version_after, version_before)
                    return result
                finally:
                    observer_checked.set()

            with ThreadPoolExecutor(max_workers=2) as executor:
                owner_future = executor.submit(own_and_finish)
                observer_future = executor.submit(observe_late)
                owner_future.result(timeout=10)
                self.assertIsNone(observer_future.result(timeout=10))

            stored = TaskStore(database).get(record.task_id)
            self.assertEqual(stored.status, TaskStatus.REVIEWING)
            self.assertEqual(stored.provider_runs[-1].run_id, "revised-run")
            self.assertEqual(stored.provider_runs[-1].result, "revised-result")
            self.assertEqual(
                [item.phase for item in stored.task_effect_ledger],
                ["plan", "plan", "revision", "revision"],
            )

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

    def test_late_failed_pre_submit_cannot_erase_completed_effect_accounting(self):
        record = _record(owner_approved=True, approved=[1])
        assignment = record.plan.assignments[0]
        assignment.estimated_cost_usd = 0.75
        effect_key = reserve_provider_effect(
            record, assignment, phase="dispatch", max_task_budget_usd=1.0
        )
        record_provider_effect_observation(
            record,
            assignment,
            effect_key=effect_key,
            phase="dispatch",
            outcome="completed",
            run_id="completed-run",
        )
        record_provider_effect_observation(
            record,
            assignment,
            effect_key=effect_key,
            phase="dispatch",
            outcome="failed_pre_submit",
        )

        self.assertEqual(0.75, record.cumulative_estimated_cost_usd)
        self.assertEqual(1, record.cumulative_effect_hops)

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
            effect_data = {
                "event_version": "v2",
                "effect_key": effect_key,
                "kind": "provider_observation",
                "phase": "dispatch",
                "outcome": "failed_pre_submit",
                "plan_generation": 0,
                "assignment_sequence": 1,
                "assignment_digest": assignment_approval_digest(assignment),
                "provider": ProviderName.CURSOR,
                "attempt": 1,
                "estimated_cost_usd": 0,
                "hop_units": 0,
                "run_id_digest": None,
            }
            record.task_effect_ledger.append(
                TaskEffectEvidence(
                    effect_digest=canonical_task_effect_digest(effect_data),
                    **effect_data,
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
        self.assertEqual(len(record.provider_run_history), 1)
        self.assertNotIn("oracle-review-run", record.model_dump_json())
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
                slack_gateway_secret="gateway-test-secret-32-characters!!",
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

    def test_slack_validation_failures_never_log_malformed_owner_content(self):
        import director_orchestrator.config as config_module
        import slack_bolt

        marker = "PRIVATE-MALFORMED-SLACK-NOTE-7d31"
        test_settings = replace(
            main.settings,
            shared_secret="test-secret",
            slack_owner_user_id="U0B9UJ86D27",
            slack_channel_id="C123",
            slack_bot_token="xoxb-test",
            slack_app_token="xapp-test",
            orchestrator_base_url="http://testserver",
            slack_gateway_secret="gateway-test-secret-32-characters!!",
        )
        module_name = "director_orchestrator.slack_bot"
        sys.modules.pop(module_name, None)
        with (
            patch.object(config_module, "Settings", return_value=test_settings),
            patch.object(slack_bolt, "App", _FakeBoltApp),
        ):
            slack_bot = importlib.import_module(module_name)

        record = _record()
        task_response = MagicMock()
        task_response.headers = {"X-Task-Record-Version": "0"}
        task_response.json.return_value = record.model_dump(mode="json")
        task_response.raise_for_status.return_value = None
        responses: list[str] = []
        client = MagicMock()
        try:
            with (
                patch.object(slack_bot.httpx, "get", return_value=task_response),
                patch.object(slack_bot.httpx, "post") as post,
                self.assertLogs("director.slack", level="WARNING") as captured,
            ):
                slack_bot.command_handler(
                    lambda: None,
                    {
                        "channel_id": "C123",
                        "user_id": "U0B9UJ86D27",
                        "text": f"approve DIR-ABCD {marker}" + ("X" * 2_100),
                    },
                    client,
                    lambda text, **_: responses.append(text),
                )
                slack_bot.approval_button_handler(
                    lambda: None,
                    {
                        "user": {"id": "U0B9UJ86D27"},
                        "channel": {"id": "C123"},
                        "actions": [{"value": marker}],
                        "message": {"ts": "123.45"},
                    },
                    client,
                )
        finally:
            sys.modules.pop(module_name, None)

        logs = "\n".join(captured.output)
        self.assertNotIn(marker, logs)
        self.assertNotIn("Traceback", logs)
        self.assertIn("ValidationError", logs)
        self.assertIn("StaleApproval", logs)
        self.assertTrue(any("BLOCKED" in item for item in responses))
        client.chat_postMessage.assert_called_once()
        post.assert_not_called()

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
    def test_reset_discards_generation_bound_debate_state_and_allows_clean_replan(self):
        record = _record(owner_approved=True, approved=[1])
        old_plan = record.plan.model_copy(deep=True)
        record.plan_generation = 4
        record.plan_interaction_id = "plan-old"
        record.review_interaction_id = "review-old"
        record.debate_tally = {"winner": "A", "participating_weight": 4.5}
        record.debate_counter_bias = DebateCounterBias(
            strongest_counterargument="Old counterargument",
            challenged_assumptions=["Old assumption"],
            alternatives_reconsidered=["B"],
            independence_assessment="Old-generation assessment",
            recommendation_changed=False,
            provisional_recommendation="A",
            confidence=0.75,
        )
        record.debate_counter_bias_interaction_id = "counter-old"
        record.debate_synthesis = DebateSynthesis(
            decision="Old decision",
            rationale="Old rationale",
            next_action="Old next action",
            openai_vote_option="A",
            confidence=0.8,
        )
        record.debate_synthesis_interaction_id = "synthesis-old"
        record.debate_tie_judgment = DebateTieJudgment(
            chosen_option="A",
            rubric_applications=[
                TieRubricApplication(
                    criterion="Evidence quality",
                    finding="Old evidence favored A",
                    favors_option="A",
                ),
                TieRubricApplication(
                    criterion="Reversibility",
                    finding="Old assessment favored B",
                    favors_option="B",
                ),
            ],
            rationale="Old tie judgment",
            confidence=0.7,
        )
        record.debate_tie_judgment_interaction_id = "tie-old"
        reserve_task_effect(
            record,
            effect_key="a" * 64,
            kind="director_intent",
            phase="debate_counter_bias",
            provider=ProviderName.OPENAI,
            estimated_cost_usd=0.1,
            hop_units=1,
        )
        durable_effects = list(record.task_effect_ledger)

        reset_for_owner_clarification(record)

        self.assertIsNone(record.debate_tally)
        self.assertIsNone(record.debate_counter_bias)
        self.assertIsNone(record.debate_counter_bias_interaction_id)
        self.assertIsNone(record.debate_synthesis)
        self.assertIsNone(record.debate_synthesis_interaction_id)
        self.assertIsNone(record.debate_tie_judgment)
        self.assertIsNone(record.debate_tie_judgment_interaction_id)
        self.assertIsNone(record.plan_interaction_id)
        self.assertIsNone(record.review_interaction_id)
        self.assertEqual(record.task_effect_ledger, durable_effects)
        self.assertEqual(record.cumulative_estimated_cost_usd, 0.1)
        for stale_id in (
            "plan-old",
            "review-old",
            "counter-old",
            "synthesis-old",
            "tie-old",
        ):
            self.assertNotIn(stale_id, record.model_dump_json())

        record.plan = old_plan
        record.plan_generation += 1
        record.plan_interaction_id = "plan-new"
        new_intent = reserve_task_effect(
            record,
            effect_key="b" * 64,
            kind="director_intent",
            phase="plan",
            provider=ProviderName.OPENAI,
            estimated_cost_usd=0.1,
            hop_units=1,
        )

        self.assertEqual(new_intent.plan_generation, 5)
        self.assertEqual(record.plan_interaction_id, "plan-new")
        self.assertEqual(
            [item.plan_generation for item in record.task_effect_ledger],
            [4, 5],
        )

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
        self.assertEqual(len(record.provider_run_history), 1)
        self.assertEqual(len(record.assignment_outcome_history), 1)
        self.assertEqual(len(record.provider_contact_attempt_history), 1)
        serialized = record.model_dump_json()
        for marker in ("run-1", "old result", "old acceptance", "timeout"):
            self.assertNotIn(marker, serialized)
        self.assertEqual(len(record.task_effect_ledger), 1)
        self.assertEqual(record.cumulative_estimated_cost_usd, 0.75)
        self.assertEqual(record.review_handoff_digests, {})
        self.assertEqual(record.provider_contact_attempts, [])
        self.assertEqual(record.unavailable_providers, [])
        self.assertEqual(record.deferred_assignments, [])
        self.assertEqual(record.availability_impacts, [])
        self.assertEqual(record.collaboration_thread_ts, "123.45")
        self.assertTrue(record.collaboration_thread_opened)
        self.assertEqual(record.collaboration_turns, [])
        self.assertEqual(record.collaboration_delivery_errors, [])
        self.assertIsNone(record.review)
        self.assertEqual(record.revision_count, 0)
        self.assertFalse(record.owner_approved)
        self.assertEqual(record.approved_assignments, [])
        self.assertEqual(record.approved_assignment_digests, {})
        self.assertIsNone(record.error)

    def test_repeated_resets_keep_digest_history_reloadable_at_200(self):
        record = _record()
        record.plan = None
        for index in range(201):
            record.provider_runs = [
                ProviderRun(
                    provider=ProviderName.CLAUDE,
                    assignment_sequence=1,
                    run_id=f"run-{index}",
                    status="FINISHED",
                    result=f"result-{index}",
                )
            ]
            reset_for_owner_clarification(record)

        self.assertEqual(len(record.provider_run_history), 200)
        reloaded = TaskRecord.model_validate_json(record.model_dump_json())
        self.assertEqual(reloaded.provider_run_history, record.provider_run_history)
        self.assertNotEqual(
            reloaded.provider_run_history[0],
            OpaqueEvidenceDigest.from_value(
                ProviderRun(
                    provider=ProviderName.CLAUDE,
                    assignment_sequence=1,
                    run_id="run-0",
                    status="FINISHED",
                    result="result-0",
                )
            ),
        )


class DurableEffectStoreTests(unittest.TestCase):
    @staticmethod
    def _sqlite_bytes(database: str) -> bytes:
        content = bytearray()
        for suffix in ("", "-wal", "-shm"):
            path = Path(database + suffix)
            if path.exists():
                content.extend(path.read_bytes())
        return bytes(content)

    def test_pinned_wal_cleanup_survives_restart_and_blocks_new_effects(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            marker_id = "PINNED-WAL-PRIVATE-MARKER-2f97"
            marker = marker_id + ("X" * 4_096)
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode=WAL").fetchone()[0],
                    "wal",
                )
            store = TaskStore(database)
            record = _record(owner_approved=True, approved=[1])
            store.create(record)

            reader = sqlite3.connect(database)
            reader.execute("BEGIN")
            clean_pinned_json = reader.execute(
                "SELECT record_json FROM tasks WHERE task_id = ?",
                (record.task_id,),
            ).fetchone()[0]
            self.assertNotIn(marker, clean_pinned_json)
            try:
                private = store.get(record.task_id)
                private.provider_runs = [
                    ProviderRun(
                        provider=ProviderName.CURSOR,
                        assignment_sequence=1,
                        run_id="private-run",
                        status="FINISHED",
                        result=marker,
                        raw={"transcript": marker},
                    )
                ]
                store.save(private)
                self.assertIn(marker_id.encode(), self._sqlite_bytes(database))

                compacted = store.get(record.task_id)
                reset_for_owner_clarification(compacted)
                store.save(compacted)

                self.assertTrue(store.privacy_cleanup_pending())
                self.assertNotIn(marker, store.get(record.task_id).model_dump_json())
                self.assertIn(marker_id.encode(), self._sqlite_bytes(database))

                blocked = store.get(record.task_id)
                reserve_task_effect(
                    blocked,
                    effect_key="e" * 64,
                    kind="director_intent",
                    phase="plan",
                    provider=ProviderName.OPENAI,
                )
                with self.assertRaises(PrivacyCleanupPending):
                    store.save(blocked)

                restarted_while_pinned = TaskStore(database)
                self.assertTrue(restarted_while_pinned.privacy_cleanup_pending())
                self.assertIsNotNone(restarted_while_pinned.get(record.task_id))
                with sqlite3.connect(database) as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM privacy_cleanup_waits"
                        ).fetchone()[0],
                        1,
                    )
                pinned_run = AsyncMock(return_value=None)
                with (
                    patch.object(main, "store", restarted_while_pinned),
                    patch.object(main.engine, "run", new=pinned_run),
                ):
                    asyncio.run(main._resume_privacy_cleanup_waits_once())
                pinned_run.assert_not_awaited()
            finally:
                reader.rollback()
                reader.close()

            recovered = TaskStore(database)
            self.assertFalse(recovered.privacy_cleanup_pending())
            first_claim = recovered.claim_privacy_cleanup_resumes(
                lease_seconds=60
            )
            self.assertEqual([item.task_id for item in first_claim], [record.task_id])
            restarted_after_claim = TaskStore(database)
            self.assertEqual(restarted_after_claim.list_recoverable(), [])
            self.assertEqual(
                restarted_after_claim.claim_privacy_cleanup_resumes(
                    lease_seconds=60
                ),
                [],
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE privacy_cleanup_waits SET lease_until = ?",
                    ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
                )
            reclaimed = restarted_after_claim.claim_privacy_cleanup_resumes(
                lease_seconds=60
            )
            self.assertEqual([item.task_id for item in reclaimed], [record.task_id])
            self.assertNotEqual(
                reclaimed[0].lease_token,
                first_claim[0].lease_token,
            )
            self.assertFalse(
                recovered.acknowledge_privacy_cleanup_resume(first_claim[0])
            )

            async def record_recovered_intent(task_id: str) -> None:
                resumed = restarted_after_claim.get(task_id)
                reserve_task_effect(
                    resumed,
                    effect_key="e" * 64,
                    kind="director_intent",
                    phase="plan",
                    provider=ProviderName.OPENAI,
                )
                restarted_after_claim.save(resumed)

            recovered_run = AsyncMock(side_effect=record_recovered_intent)
            with (
                patch.object(main, "store", restarted_after_claim),
                patch.object(main.engine, "run", new=recovered_run),
            ):
                asyncio.run(main._resume_privacy_cleanup_wait(reclaimed[0]))
                asyncio.run(main._resume_privacy_cleanup_waits_once())
            recovered_run.assert_awaited_once_with(record.task_id)
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM privacy_cleanup_waits"
                    ).fetchone()[0],
                    0,
                )
            self.assertNotIn(
                marker,
                restarted_after_claim.get(record.task_id).model_dump_json(),
            )
            self.assertNotIn(marker_id.encode(), self._sqlite_bytes(database))

    def test_slack_cleanup_recovery_and_original_reservation_have_one_winner(self):
        for ordinal in range(8):
            with self.subTest(ordinal=ordinal), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as directory:
                database = str(Path(directory) / "director.sqlite3")
                original_key = f"{ordinal:064x}"
                first_store = TaskStore(database)
                first_store.create(_record())
                with sqlite3.connect(database) as connection:
                    connection.execute(
                        """INSERT INTO privacy_cleanup_waits(
                            task_id, effect_kind, effect_key, phase, requested_at
                        ) VALUES (?, 'slack_intent', ?, 'slack_delivery', ?)""",
                        (
                            "DIR-BIND",
                            original_key,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                resume = first_store.claim_privacy_cleanup_resumes(
                    lease_seconds=60
                )[0]
                second_store = TaskStore(database)

                original_record = first_store.get("DIR-BIND")
                reserve_task_effect(
                    original_record,
                    effect_key=original_key,
                    kind="slack_intent",
                    phase="slack_delivery",
                    replay_safe=True,
                )
                recovery_record = second_store.get("DIR-BIND")
                client_msg_id = main.engine._slack_client_id(
                    "DIR-BIND", "privacy-recovery", original_key
                )
                recovery_key = task_effect_key(
                    recovery_record,
                    phase="slack_delivery",
                    identity={"client_msg_id": client_msg_id},
                )
                recovery_effect = reserve_task_effect(
                    recovery_record,
                    effect_key=recovery_key,
                    kind="slack_intent",
                    phase="slack_delivery",
                    replay_safe=True,
                )
                barrier = threading.Barrier(2)

                def save_original() -> str:
                    barrier.wait()
                    try:
                        first_store.save(original_record)
                        return "original"
                    except StaleTaskWrite:
                        return "superseded"

                def reserve_recovery() -> str:
                    barrier.wait()
                    return second_store.prepare_privacy_slack_recovery(
                        resume, recovery_effect
                    )

                with ThreadPoolExecutor(max_workers=2) as pool:
                    original_result = pool.submit(save_original)
                    recovery_result = pool.submit(reserve_recovery)
                    original_outcome = original_result.result(timeout=10)
                    recovery_outcome = recovery_result.result(timeout=10)

                intents = [
                    effect
                    for effect in first_store.list_task_effects("DIR-BIND")
                    if effect.kind == "slack_intent"
                ]
                self.assertEqual(len(intents), 1)
                if recovery_outcome == "original":
                    self.assertEqual(original_outcome, "original")
                    self.assertEqual(intents[0].effect_key, original_key)
                else:
                    self.assertEqual(recovery_outcome, "recovery")
                    self.assertEqual(original_outcome, "superseded")
                    self.assertEqual(intents[0].effect_key, recovery_key)

                recovery_post = AsyncMock(return_value=None)
                with (
                    patch.object(main, "store", second_store),
                    patch.object(main.engine, "_status_post", new=recovery_post),
                ):
                    asyncio.run(main._resume_privacy_cleanup_wait(resume))
                if recovery_outcome == "original":
                    recovery_post.assert_not_awaited()
                else:
                    recovery_post.assert_awaited_once()
                    self.assertEqual(
                        recovery_post.await_args.kwargs["client_msg_id"],
                        client_msg_id,
                    )

    def test_slack_recovery_rechecks_privacy_gate_after_lease_claim(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            task_store.create(_record())
            original_key = "a" * 64
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """INSERT INTO privacy_cleanup_waits(
                        task_id, effect_kind, effect_key, phase, requested_at
                    ) VALUES (?, 'slack_intent', ?, 'slack_delivery', ?)""",
                    (
                        "DIR-BIND",
                        original_key,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            first_lease = task_store.claim_privacy_cleanup_resumes(
                lease_seconds=60
            )[0]
            record = task_store.get("DIR-BIND")
            client_msg_id = main.engine._slack_client_id(
                "DIR-BIND", "privacy-recovery", original_key
            )
            recovery_key = task_effect_key(
                record,
                phase="slack_delivery",
                identity={"client_msg_id": client_msg_id},
            )
            recovery_effect = reserve_task_effect(
                record,
                effect_key=recovery_key,
                kind="slack_intent",
                phase="slack_delivery",
                replay_safe=True,
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """UPDATE privacy_cleanup_state
                    SET pending_generation = completed_generation + 1
                    WHERE singleton = 1"""
                )

            self.assertEqual(
                task_store.prepare_privacy_slack_recovery(
                    first_lease, recovery_effect
                ),
                "pending",
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM privacy_cleanup_effect_resolutions"
                    ).fetchone()[0],
                    0,
                )
            self.assertEqual(task_store.list_task_effects("DIR-BIND"), [])
            recovery_post = AsyncMock(return_value=None)
            with (
                patch.object(main, "store", task_store),
                patch.object(task_store, "_attempt_privacy_cleanup", return_value=False),
                patch.object(main.engine, "_status_post", new=recovery_post),
            ):
                asyncio.run(main._resume_privacy_cleanup_wait(first_lease))
            recovery_post.assert_not_awaited()

            with sqlite3.connect(database) as connection:
                connection.execute(
                    """UPDATE privacy_cleanup_state
                    SET completed_generation = pending_generation WHERE singleton = 1"""
                )
                connection.execute(
                    "UPDATE privacy_cleanup_waits SET lease_until = ?",
                    ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
                )
            reclaimed = task_store.claim_privacy_cleanup_resumes(
                lease_seconds=60
            )[0]
            with (
                patch.object(main, "store", task_store),
                patch.object(main.engine, "_status_post", new=recovery_post),
            ):
                asyncio.run(main._resume_privacy_cleanup_wait(reclaimed))
            recovery_post.assert_awaited_once()
            self.assertEqual(
                [
                    effect.effect_key
                    for effect in task_store.list_task_effects("DIR-BIND")
                    if effect.kind == "slack_intent"
                ],
                [recovery_key],
            )

    def test_legacy_private_history_is_physically_scrubbed_during_migration(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "legacy-private.sqlite3")
            marker = "LEGACY-PRIVATE-PROVIDER-MARKER-7f10"
            private_blob = marker + ("X" * 4_096)
            record = _record(owner_approved=True, approved=[1])
            legacy_payload = record.model_dump(mode="json")
            legacy_payload["provider_run_history"] = [
                {
                    "provider": "claude",
                    "assignment_sequence": 1,
                    "run_id": "legacy-private-run",
                    "status": "FINISHED",
                    "result": private_blob,
                    "raw": {"transcript": private_blob, "extra": private_blob},
                }
            ]
            legacy_payload["assignment_outcome_history"] = [
                {
                    "assignment_sequence": 1,
                    "provider": "claude",
                    "run_id": "legacy-private-run",
                    "result": private_blob,
                    "review_summary": private_blob,
                    "evidence": [private_blob],
                }
            ]
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """CREATE TABLE tasks (
                        task_id TEXT PRIMARY KEY,
                        source_event_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        record_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.task_id,
                        record.intake.source_event_id,
                        record.status.value,
                        json.dumps(legacy_payload),
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )
            self.assertIn(marker.encode(), Path(database).read_bytes())

            migrated = TaskStore(database)
            loaded = migrated.get(record.task_id)
            self.assertEqual(len(loaded.provider_run_history), 1)
            self.assertEqual(len(loaded.assignment_outcome_history), 1)
            self.assertNotIn(marker, loaded.model_dump_json())
            self.assertNotIn(marker.encode(), Path(database).read_bytes())

    def test_sqlite_effect_rows_resist_update_delete_replace_and_upsert(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            store = TaskStore(database)
            record = _record(owner_approved=True, approved=[1])
            store.create(record)
            loaded = store.get(record.task_id)
            reserve_provider_effect(
                loaded,
                loaded.plan.assignments[0],
                phase="dispatch",
                max_task_budget_usd=2.0,
            )
            store.save(loaded)

            with sqlite3.connect(database) as connection:
                before = connection.execute(
                    "SELECT * FROM task_effect_events WHERE task_id = ?",
                    (record.task_id,),
                ).fetchone()
                statements = (
                    "UPDATE task_effect_events SET event_json='{}' WHERE task_id=?",
                    "DELETE FROM task_effect_events WHERE task_id=?",
                    """INSERT OR REPLACE INTO task_effect_events
                    SELECT task_id,event_digest,effect_key,event_kind,'{}',recorded_at
                    FROM task_effect_events WHERE task_id=?""",
                    """INSERT INTO task_effect_events(
                        task_id,event_digest,effect_key,event_kind,event_json,recorded_at
                    ) SELECT task_id,event_digest,effect_key,event_kind,'{}',recorded_at
                    FROM task_effect_events WHERE task_id=?
                    ON CONFLICT(task_id,event_digest) DO UPDATE SET event_json='{}'""",
                )
                for statement in statements:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, (record.task_id,))
                    connection.rollback()
                after = connection.execute(
                    "SELECT * FROM task_effect_events WHERE task_id = ?",
                    (record.task_id,),
                ).fetchone()
            self.assertEqual(before, after)

    def test_stale_save_cannot_erase_append_only_effect_or_accounting(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            first = TaskStore(database)
            second = TaskStore(database)
            record = _record(owner_approved=True, approved=[1])
            record.plan.assignments[0].estimated_cost_usd = 0.75
            first.create(record)

            stale = first.get(record.task_id)
            fresh = second.get(record.task_id)
            reserve_provider_effect(
                fresh,
                fresh.plan.assignments[0],
                phase="dispatch",
                max_task_budget_usd=2.0,
            )
            second.save(fresh)

            stale.message = "late callback projection"
            with self.assertRaises(StaleTaskWrite):
                first.save(stale)

            restarted = TaskStore(database).get(record.task_id)
            self.assertNotEqual(restarted.message, "late callback projection")
            self.assertEqual(len(restarted.task_effect_ledger), 1)
            self.assertEqual(restarted.cumulative_estimated_cost_usd, 0.75)
            with sqlite3.connect(database) as connection:
                raw = connection.execute(
                    "SELECT record_json FROM tasks WHERE task_id = ?",
                    (record.task_id,),
                ).fetchone()[0]
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM task_effect_events WHERE task_id = ?",
                    (record.task_id,),
                ).fetchone()[0]
            self.assertEqual(event_count, 1)
            self.assertEqual(TaskRecord.model_validate_json(raw).task_effect_ledger, [])

    def test_stale_callback_snapshot_cannot_restore_an_older_plan_generation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            owner_store = TaskStore(database)
            callback_store = TaskStore(database)
            record = _record(owner_approved=True, approved=[1])
            record.provider_runs = [
                ProviderRun(
                    provider=ProviderName.CLAUDE,
                    assignment_sequence=1,
                    run_id="callback-race-run",
                    status="WAITING_CALLBACK",
                )
            ]
            interaction_engine = OrchestrationEngine(
                replace(Settings(), sqlite_path=database), owner_store
            )
            callback_assignment = record.plan.assignments[0].model_copy(
                update={"agent": ProviderName.CLAUDE}
            )
            interaction_id = interaction_engine._begin_provider_interaction(
                record,
                callback_assignment,
                phase="dispatch",
                attempt=1,
                effect_key="b" * 64,
            )
            record.provider_runs[0].interaction_id = interaction_id
            interaction_engine._record_dispatch_acknowledgement(
                record, record.provider_runs[0]
            )
            owner_store.create(record)
            stale_callback = callback_store.get(record.task_id)
            current = owner_store.get(record.task_id)
            current.plan_generation += 1
            current.plan.owner_question = "Current replacement-plan question"
            current.message = "new generation remains authoritative"
            owner_store.save(current)

            apply_claude_callback(
                stale_callback,
                ClaudeCallback(
                    run_id="callback-race-run",
                    status="completed",
                    result="late old-generation result",
                ),
            )
            with self.assertRaises(StaleTaskWrite):
                callback_store.save(stale_callback)

            persisted = TaskStore(database).get(record.task_id)
            self.assertEqual(persisted.plan_generation, 1)
            self.assertEqual(persisted.plan.owner_question, "Current replacement-plan question")
            self.assertEqual(persisted.message, "new generation remains authoritative")
            self.assertEqual(persisted.provider_runs[0].status, "WAITING_CALLBACK")

    def test_legacy_effect_projection_is_backfilled_then_removed_from_task_json(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "legacy.sqlite3")
            record = _record(owner_approved=True, approved=[1])
            effect_key = reserve_provider_effect(
                record,
                record.plan.assignments[0],
                phase="dispatch",
                max_task_budget_usd=2.0,
            )
            record_provider_effect_observation(
                record,
                record.plan.assignments[0],
                effect_key=effect_key,
                phase="dispatch",
                outcome="submitted",
                run_id="legacy-run-identifier",
            )
            legacy_payload = record.model_dump(mode="json")
            legacy_observation = legacy_payload["task_effect_ledger"][1]
            legacy_observation.pop("event_version")
            legacy_observation.pop("run_id_digest")
            legacy_observation["run_id"] = "legacy-run-identifier"
            legacy_observation["effect_digest"] = "0" * 64
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """CREATE TABLE tasks (
                        task_id TEXT PRIMARY KEY,
                        source_event_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        record_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.task_id,
                        record.intake.source_event_id,
                        record.status.value,
                        json.dumps(legacy_payload),
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )

            migrated = TaskStore(database)
            loaded = migrated.get(record.task_id)
            self.assertEqual(len(loaded.task_effect_ledger), 2)
            self.assertEqual(
                loaded.task_effect_ledger[1].run_id_digest,
                canonical_opaque_identifier_digest("legacy-run-identifier"),
            )
            with sqlite3.connect(database) as connection:
                raw, status = connection.execute(
                    "SELECT record_json, effect_migration_status FROM tasks"
                ).fetchone()
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM task_effect_events"
                ).fetchone()[0]
            self.assertEqual(status, "complete")
            self.assertEqual(event_count, 2)
            self.assertEqual(TaskRecord.model_validate_json(raw).task_effect_ledger, [])

    def test_reset_and_storage_never_retain_provider_text_or_raw_identifiers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            marker = "PRIVATE-PROVIDER-MARKER-7f10"
            record = _record(owner_approved=True, approved=[1])
            record.provider_runs = [
                ProviderRun(
                    provider=ProviderName.CURSOR,
                    assignment_sequence=1,
                    run_id=marker,
                    status="FINISHED",
                    result=marker,
                    raw={"extra": marker, "transcript": marker},
                )
            ]
            record.assignment_outcomes = [
                AssignmentOutcome(
                    assignment_sequence=1,
                    provider=ProviderName.CURSOR,
                    run_id=marker,
                    result=marker,
                    review_summary=marker,
                    evidence=[marker],
                )
            ]
            record.collaboration_turns = [
                CollaborationTurn(
                    assignment_sequence=1,
                    provider=ProviderName.CURSOR,
                    phase="work_result",
                    correlation_id="marker-turn",
                    run_id=marker,
                    position=marker,
                    evidence=[marker],
                    contradictions=[marker],
                    confidence=0.5,
                )
            ]
            record.collaboration_delivery_errors = [marker]
            reset_for_owner_clarification(record)
            store = TaskStore(database)
            store.create(record)
            first = store.get(record.task_id)
            restarted = TaskStore(database).get(record.task_id)
            self.assertEqual(first.provider_run_history, restarted.provider_run_history)
            self.assertEqual(
                first.assignment_outcome_history,
                restarted.assignment_outcome_history,
            )
            self.assertNotIn(marker, restarted.model_dump_json())
            self.assertNotIn(marker.encode(), Path(database).read_bytes())


class IntakeBudgetAdmissionTests(unittest.TestCase):
    @staticmethod
    def _payload(source_event_id: str) -> dict[str, str]:
        return {
            "source_event_id": source_event_id,
            "channel_id": "C123",
            "thread_ts": "123.45",
            "user_id": "U-OWNER",
            "text": "DIRECTOR queue this task without spending paid capacity",
            "received_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _settings():
        return replace(
            main.settings,
            shared_secret="test-secret",
            slack_gateway_secret="gateway-test-secret-at-least-32-characters",
            slack_owner_user_id="U-OWNER",
            slack_channel_id="C123",
            allow_paid_runs=True,
            max_task_budget_usd=1.0,
            max_daily_provider_budget_usd=2.0,
        )

    @staticmethod
    def _signed_headers(
        payload: dict[str, str], test_settings, *, issued_at: int | None = None
    ) -> dict[str, str]:
        intake = SlackIntake.model_validate(payload)
        return {
            "Authorization": "Bearer test-secret",
            **build_slack_intake_attestation(
                test_settings.slack_gateway_secret,
                test_settings.shared_secret,
                intake,
                issued_at=issued_at,
                nonce="N" * 32,
            ),
        }

    @staticmethod
    def _budget_rows(database: str) -> list[tuple[str, float]]:
        with sqlite3.connect(database) as connection:
            return connection.execute(
                "SELECT task_id,reserved_budget_usd FROM paid_task_daily_budget "
                "ORDER BY recorded_at"
            ).fetchall()

    def test_forged_owner_id_with_bearer_only_cannot_schedule_or_reserve(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            ledger = DailyTaskBudgetLedger(database)
            test_settings = self._settings()
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main, "daily_task_budget", ledger),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                response = client.post(
                    "/v1/intake",
                    headers={"Authorization": "Bearer test-secret"},
                    json=self._payload("evt-forged-owner"),
                )

            self.assertEqual(403, response.status_code)
            self.assertEqual([], self._budget_rows(database))
            run.assert_not_awaited()
            with sqlite3.connect(database) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_dry_run_intake_remains_bearer_only_and_cannot_reserve(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            test_settings = replace(
                self._settings(), allow_paid_runs=False, slack_gateway_secret=""
            )
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main, "daily_task_budget", None),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                response = client.post(
                    "/v1/intake",
                    headers={"Authorization": "Bearer test-secret"},
                    json=self._payload("evt-dry-run-bearer-only"),
                )

            self.assertEqual(202, response.status_code)
            self.assertEqual(
                IntakeAuthentication.BEARER_DRY_RUN,
                response.json()["intake_authentication"],
            )
            run.assert_awaited_once_with(response.json()["task_id"])
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT COUNT(*) FROM sqlite_master "
                        "WHERE type='table' AND name='paid_task_daily_budget'"
                    ).fetchone()[0],
                )

    def test_signed_owner_channel_intake_schedules_without_early_reservation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            ledger = DailyTaskBudgetLedger(database)
            test_settings = self._settings()
            run = AsyncMock(return_value=None)
            payload = self._payload("evt-signed-owner")
            headers = self._signed_headers(payload, test_settings)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main, "daily_task_budget", ledger),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                response = client.post("/v1/intake", headers=headers, json=payload)

            self.assertEqual(202, response.status_code)
            self.assertEqual(
                IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                response.json()["intake_authentication"],
            )
            self.assertEqual([], self._budget_rows(database))
            run.assert_awaited_once_with(response.json()["task_id"])

    def test_paid_intake_attestation_rejects_tamper_stale_malformed_and_wrong_signature(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            ledger = DailyTaskBudgetLedger(database)
            test_settings = self._settings()
            run = AsyncMock(return_value=None)
            payload = self._payload("evt-invalid-attestation")
            valid_headers = self._signed_headers(payload, test_settings)
            stale_headers = self._signed_headers(
                payload,
                test_settings,
                issued_at=int(datetime.now(timezone.utc).timestamp()) - 301,
            )
            cases = {
                "tampered-body": (
                    valid_headers,
                    {**payload, "text": payload["text"] + " altered"},
                ),
                "stale": (stale_headers, payload),
                "malformed": (
                    {
                        **valid_headers,
                        SLACK_INTAKE_NONCE_HEADER: "short",
                    },
                    payload,
                ),
                "malformed-issued-at": (
                    {
                        **valid_headers,
                        SLACK_INTAKE_ISSUED_AT_HEADER: "not-a-timestamp",
                    },
                    payload,
                ),
                "wrong-signature": (
                    {
                        **valid_headers,
                        SLACK_INTAKE_SIGNATURE_HEADER: "0" * 64,
                    },
                    payload,
                ),
            }

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main, "daily_task_budget", ledger),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                for label, (headers, body) in cases.items():
                    with self.subTest(label=label):
                        response = client.post(
                            "/v1/intake", headers=headers, json=body
                        )
                        self.assertEqual(403, response.status_code)

            self.assertEqual([], self._budget_rows(database))
            run.assert_not_awaited()
            with sqlite3.connect(database) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_duplicate_signed_event_replays_idempotently(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            ledger = DailyTaskBudgetLedger(database)
            test_settings = self._settings()
            run = AsyncMock(return_value=None)
            payload = self._payload("evt-signed-duplicate")
            headers = self._signed_headers(payload, test_settings)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main, "daily_task_budget", ledger),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                first = client.post("/v1/intake", headers=headers, json=payload)
                replay = client.post("/v1/intake", headers=headers, json=payload)

            self.assertEqual([202, 202], [first.status_code, replay.status_code])
            self.assertEqual(first.json()["task_id"], replay.json()["task_id"])
            self.assertEqual([], self._budget_rows(database))
            run.assert_awaited_once_with(first.json()["task_id"])

    def test_signed_event_replay_with_fresh_nonce_reuses_original_task_after_restart(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            test_settings = self._settings()
            payload = self._payload("evt-signed-restart")
            first_headers = self._signed_headers(payload, test_settings)
            second_headers = {
                "Authorization": "Bearer test-secret",
                **build_slack_intake_attestation(
                    test_settings.slack_gateway_secret,
                    test_settings.shared_secret,
                    SlackIntake.model_validate(payload),
                    nonce="R" * 32,
                ),
            }
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", TaskStore(database)),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                first = client.post(
                    "/v1/intake", headers=first_headers, json=payload
                )

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", TaskStore(database)),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                replay = client.post(
                    "/v1/intake", headers=second_headers, json=payload
                )

            self.assertEqual([202, 202], [first.status_code, replay.status_code])
            self.assertEqual(first.json()["task_id"], replay.json()["task_id"])
            run.assert_awaited_once_with(first.json()["task_id"])
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    1, connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                )
                self.assertEqual(
                    2,
                    connection.execute(
                        "SELECT COUNT(*) FROM slack_intake_claims"
                    ).fetchone()[0],
                )

    def test_signed_event_or_nonce_conflicting_reuse_fails_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            test_settings = self._settings()
            run = AsyncMock(return_value=None)
            original = self._payload("evt-signed-conflict")
            original_headers = self._signed_headers(original, test_settings)
            changed = {**original, "text": original["text"] + " changed"}
            changed_headers = self._signed_headers(changed, test_settings)
            reused_nonce = self._payload("evt-other-source")
            reused_nonce_headers = self._signed_headers(reused_nonce, test_settings)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                first = client.post(
                    "/v1/intake", headers=original_headers, json=original
                )
                source_conflict = client.post(
                    "/v1/intake", headers=changed_headers, json=changed
                )
                nonce_conflict = client.post(
                    "/v1/intake", headers=reused_nonce_headers, json=reused_nonce
                )

            self.assertEqual(202, first.status_code)
            self.assertEqual(409, source_conflict.status_code)
            self.assertEqual(409, nonce_conflict.status_code)
            run.assert_awaited_once_with(first.json()["task_id"])
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    1, connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                )

    def test_concurrent_signed_claims_create_exactly_one_task(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            TaskStore(database)
            payload = SlackIntake.model_validate(
                self._payload("evt-signed-concurrent")
            )
            test_settings = self._settings()
            headers = build_slack_intake_attestation(
                test_settings.slack_gateway_secret,
                test_settings.shared_secret,
                payload,
                nonce="C" * 32,
            )
            nonce_digest, payload_digest = verify_slack_intake_attestation(
                test_settings.slack_gateway_secret,
                test_settings.shared_secret,
                payload,
                expected_owner_user_id=test_settings.slack_owner_user_id,
                expected_channel_id=test_settings.slack_channel_id,
                issued_at=headers[SLACK_INTAKE_ISSUED_AT_HEADER],
                nonce=headers[SLACK_INTAKE_NONCE_HEADER],
                signature=headers[SLACK_INTAKE_SIGNATURE_HEADER],
            )
            start = threading.Barrier(2)

            def claim(task_id: str):
                task_store = TaskStore(database)
                candidate = TaskRecord(
                    task_id=task_id,
                    status=TaskStatus.QUEUED,
                    intake=payload.model_copy(deep=True),
                    intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                )
                start.wait(timeout=5)
                return task_store.create_signed_slack_intake(
                    candidate,
                    nonce_digest=nonce_digest,
                    payload_digest=payload_digest,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(
                    executor.map(claim, ("DIR-RACE-ONE", "DIR-RACE-TWO"))
                )

            self.assertEqual(1, sum(created for _, created in outcomes))
            self.assertEqual(1, len({stored.task_id for stored, _ in outcomes}))
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    1, connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                )
                self.assertEqual(
                    1,
                    connection.execute(
                        "SELECT COUNT(*) FROM slack_intake_claims"
                    ).fetchone()[0],
                )

    def test_slack_forward_signs_exact_payload_before_direct_or_n8n_delivery(self):
        import director_orchestrator.config as config_module
        import slack_bolt

        test_settings = replace(
            self._settings(),
            slack_bot_token="xoxb-test",
            slack_app_token="xapp-test",
            n8n_webhook_url="http://n8n.test/webhook/director-intake",
        )
        module_name = "director_orchestrator.slack_bot"
        sys.modules.pop(module_name, None)
        with (
            patch.object(config_module, "Settings", return_value=test_settings),
            patch.object(slack_bolt, "App", _FakeBoltApp),
        ):
            slack_bot = importlib.import_module(module_name)

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"task_id": "DIR-SIGNED"}
        client = MagicMock()
        try:
            with patch.object(slack_bot.httpx, "post", return_value=response) as post:
                slack_bot._forward(
                    {
                        "channel": "C123",
                        "user": "U-OWNER",
                        "ts": "1787640000.123456",
                        "event_ts": "1787640000.123456",
                        "text": "DIRECTOR verify signed forwarding",
                    },
                    client,
                    require_prefix=False,
                )
        finally:
            sys.modules.pop(module_name, None)

        post.assert_called_once()
        target = post.call_args.args[0]
        headers = post.call_args.kwargs["headers"]
        intake = SlackIntake.model_validate(post.call_args.kwargs["json"])
        self.assertEqual(test_settings.n8n_webhook_url, target)
        self.assertEqual("Bearer test-secret", headers["Authorization"])
        verify_slack_intake_attestation(
            test_settings.slack_gateway_secret,
            test_settings.shared_secret,
            intake,
            expected_owner_user_id=test_settings.slack_owner_user_id,
            expected_channel_id=test_settings.slack_channel_id,
            issued_at=headers[SLACK_INTAKE_ISSUED_AT_HEADER],
            nonce=headers[SLACK_INTAKE_NONCE_HEADER],
            signature=headers[SLACK_INTAKE_SIGNATURE_HEADER],
        )

    def test_owner_authorized_engine_admission_reserves_once_across_replay(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            record = TaskRecord(
                task_id="DIR-AUTHORIZED-BUDGET",
                status=TaskStatus.QUEUED,
                intake=SlackIntake.model_validate(self._payload("evt-authorized-budget")),
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                owner_approved=True,
            )
            task_store.create(record)
            test_settings = replace(
                main.settings,
                allow_paid_runs=True,
                openai_api_key="",
                slack_bot_token="",
                max_task_budget_usd=1.0,
                max_daily_provider_budget_usd=2.0,
                langgraph_runtime_enabled=False,
                langgraph_runtime_live_dispatch_enabled=False,
                langgraph_runtime_task_writer_enabled=False,
            )
            authorized_engine = OrchestrationEngine(test_settings, task_store)

            self.assertEqual([], self._budget_rows(database))
            asyncio.run(authorized_engine.run(record.task_id))
            first_rows = self._budget_rows(database)
            asyncio.run(authorized_engine.run(record.task_id))
            replay_rows = self._budget_rows(database)

            self.assertEqual([(record.task_id, 1.0)], first_rows)
            self.assertEqual(first_rows, replay_rows)

    def test_bearer_dry_run_cannot_be_rearmed_through_api_after_paid_mode_enabled(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            record = _record()
            record.task_id = "DIR-DRY-API-REARM"
            record.intake_authentication = IntakeAuthentication.BEARER_DRY_RUN
            task_store.create(record)
            ledger = DailyTaskBudgetLedger(database)
            test_settings = replace(
                self._settings(),
                slack_owner_user_id="U0B9UJ86D27",
            )
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main, "daily_task_budget", ledger),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                response = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer test-secret"},
                    json={
                        "owner_user_id": "U0B9UJ86D27",
                        "source": "api",
                        "note": "Attempt paid rearm with the shared bearer only",
                    },
                )

            self.assertEqual(409, response.status_code)
            self.assertIn("authenticated Slack gateway proof", response.json()["detail"])
            stored = task_store.get(record.task_id)
            self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, stored.status)
            self.assertFalse(stored.owner_approved)
            self.assertEqual(
                IntakeAuthentication.BEARER_DRY_RUN,
                stored.intake_authentication,
            )
            self.assertEqual([], self._budget_rows(database))
            run.assert_not_awaited()

    def test_signed_slack_approval_durably_rearms_a_dry_run_record(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            record = _record()
            record.task_id = "DIR-DRY-SLACK-REARM"
            record.intake_authentication = IntakeAuthentication.BEARER_DRY_RUN
            record.message = (
                "Paid execution requires a fresh authenticated Slack gateway proof."
            )
            task_store.create(record)
            current, version = task_store.get_versioned(record.task_id)
            test_settings = replace(
                self._settings(),
                slack_owner_user_id="U0B9UJ86D27",
            )
            approval = _signed_slack_approval(
                current,
                version,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    source=OwnerApprovalSource.SLACK_COMMAND,
                    note="Fresh authenticated paid rearm",
                ),
                secret=test_settings.slack_gateway_secret,
                shared_secret=test_settings.shared_secret,
                channel_id=test_settings.slack_channel_id,
            )
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                response = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer test-secret"},
                    json=approval.model_dump(mode="json"),
                )

            self.assertEqual(200, response.status_code)
            self.assertEqual(
                IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                response.json()["intake_authentication"],
            )
            restarted = TaskStore(database).get(record.task_id)
            self.assertEqual(
                IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                restarted.intake_authentication,
            )
            self.assertEqual(TaskStatus.QUEUED, restarted.status)
            run.assert_awaited_once_with(record.task_id)

    def test_nonpaid_slack_decision_remains_the_current_plan_approval(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            record = _record()
            record.task_id = "DIR-DRY-SLACK-PLAN-APPROVAL"
            record.intake_authentication = IntakeAuthentication.BEARER_DRY_RUN
            task_store.create(record)
            current, version = task_store.get_versioned(record.task_id)
            test_settings = replace(
                self._settings(),
                allow_paid_runs=False,
                slack_owner_user_id="U0B9UJ86D27",
            )
            approval = OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                source=OwnerApprovalSource.SLACK_COMMAND,
                note="Approve only the current dry-run plan decision",
            )
            approval = _signed_slack_approval(
                current,
                version,
                approval,
                secret=test_settings.slack_gateway_secret,
                shared_secret=test_settings.shared_secret,
                channel_id=test_settings.slack_channel_id,
            )
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main.engine, "run", new=run),
            ):
                response = TestClient(main.app).post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer test-secret"},
                    json=approval.model_dump(mode="json"),
                )

            self.assertEqual(200, response.status_code, response.text)
            stored = task_store.get(record.task_id)
            self.assertTrue(stored.owner_approved)
            self.assertEqual(
                IntakeAuthentication.BEARER_DRY_RUN,
                stored.intake_authentication,
            )
            run.assert_awaited_once_with(record.task_id)

    def test_failed_retry_proof_cannot_be_consumed_as_paid_rearm(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            record = _record(owner_approved=True, approved=[1])
            record.task_id = "DIR-FAILED-DRY-RETRY"
            record.status = TaskStatus.FAILED
            record.error = "Provider failed before dispatch"
            record.intake_authentication = IntakeAuthentication.BEARER_DRY_RUN
            original_plan = record.plan.model_dump(mode="json")
            task_store.create(record)
            current, version = task_store.get_versioned(record.task_id)
            test_settings = replace(
                self._settings(),
                slack_owner_user_id="U0B9UJ86D27",
            )
            retry_approval = _retry_approval(
                current,
                note="Retry the exact failed pre-dispatch assignment",
            ).model_copy(update={"source": OwnerApprovalSource.SLACK_COMMAND})
            approval = _signed_slack_approval(
                current,
                version,
                retry_approval,
                secret=test_settings.slack_gateway_secret,
                shared_secret=test_settings.shared_secret,
                channel_id=test_settings.slack_channel_id,
            )
            run = AsyncMock(return_value=None)

            with (
                patch.object(main, "settings", test_settings),
                patch.object(main, "store", task_store),
                patch.object(main.engine, "run", new=run),
            ):
                client = TestClient(main.app)
                response = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer test-secret"},
                    json=approval.model_dump(mode="json"),
                )

            self.assertEqual(200, response.status_code)
            stored = task_store.get(record.task_id)
            self.assertEqual(TaskStatus.QUEUED, stored.status)
            self.assertEqual(
                IntakeAuthentication.BEARER_DRY_RUN,
                stored.intake_authentication,
            )
            self.assertEqual(original_plan, stored.plan.model_dump(mode="json"))
            self.assertIsNone(stored.error)
            run.assert_awaited_once_with(record.task_id)

    def test_many_bearer_dry_run_records_cannot_exhaust_paid_task_budget(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            records: list[TaskRecord] = []
            for index in range(5):
                record = TaskRecord(
                    task_id=f"DIR-DRY-EXHAUST-{index}",
                    status=TaskStatus.QUEUED,
                    intake=SlackIntake.model_validate(
                        self._payload(f"evt-dry-exhaust-{index}")
                    ),
                    intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
                    owner_approved=True,
                )
                task_store.create(record)
                records.append(record)
            test_settings = replace(
                main.settings,
                allow_paid_runs=True,
                openai_api_key="",
                slack_bot_token="",
                max_task_budget_usd=1.0,
                max_daily_provider_budget_usd=2.0,
                langgraph_runtime_enabled=False,
                langgraph_runtime_live_dispatch_enabled=False,
                langgraph_runtime_task_writer_enabled=False,
            )
            paid_engine = OrchestrationEngine(test_settings, task_store)

            for record in records:
                asyncio.run(paid_engine.run(record.task_id))

            self.assertEqual([], self._budget_rows(database))
            for record in records:
                stored = task_store.get(record.task_id)
                self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, stored.status)
                self.assertFalse(stored.owner_approved)
                self.assertEqual(
                    IntakeAuthentication.BEARER_DRY_RUN,
                    stored.intake_authentication,
                )

    def test_real_dry_preview_requires_signed_rearm_then_builds_one_fresh_live_plan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            dry_settings = replace(
                self._settings(),
                allow_paid_runs=False,
                sqlite_path=database,
                openai_api_key="",
                slack_bot_token="",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                langgraph_runtime_enabled=False,
                langgraph_runtime_live_dispatch_enabled=False,
                langgraph_runtime_task_writer_enabled=False,
            )
            dry_engine = OrchestrationEngine(dry_settings, task_store)

            with (
                patch.object(main, "settings", dry_settings),
                patch.object(main, "store", task_store),
                patch.object(main.engine, "run", new=dry_engine.run),
            ):
                client = TestClient(main.app)
                intake_response = client.post(
                    "/v1/intake",
                    headers={"Authorization": "Bearer test-secret"},
                    json=self._payload("evt-real-dry-rearm"),
                )

            self.assertEqual(202, intake_response.status_code)
            task_id = intake_response.json()["task_id"]
            preview, preview_version = task_store.get_versioned(task_id)
            self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, preview.status)
            self.assertEqual(
                IntakeAuthentication.BEARER_DRY_RUN,
                preview.intake_authentication,
            )
            self.assertIsNotNone(preview.plan)
            preview_plan_dump = preview.plan.model_dump(mode="json")

            paid_settings = replace(
                dry_settings,
                allow_paid_runs=True,
                openai_api_key="test-openai-key",
            )
            fresh_plan = preview.plan.model_copy(
                update={
                    "project": "demo",
                    "approval_required": True,
                    "approval_reasons": ["Focused live-plan acceptance gate"],
                    "assignments": [
                        assignment.model_copy(
                            update={"repository": "example/demo", "starting_ref": "main"}
                        )
                        for assignment in preview.plan.assignments
                    ],
                },
                deep=True,
            )
            live_engine = OrchestrationEngine(paid_settings, task_store)
            live_engine.director.plan = AsyncMock(return_value=fresh_plan)
            asyncio.run(live_engine.run(task_id))
            preview, preview_version = task_store.get_versioned(task_id)
            approval = _signed_slack_approval(
                preview,
                preview_version,
                OwnerApproval(
                    owner_user_id="U-OWNER",
                    source=OwnerApprovalSource.SLACK_COMMAND,
                    note="Authenticate and rebuild the live plan",
                ),
                secret=paid_settings.slack_gateway_secret,
                shared_secret=paid_settings.shared_secret,
                channel_id=paid_settings.slack_channel_id,
            )

            with (
                patch.object(main, "settings", paid_settings),
                patch.object(main, "store", task_store),
                patch.object(main.engine, "run", new=live_engine.run),
            ):
                client = TestClient(main.app)
                accepted = client.post(
                    f"/v1/tasks/{task_id}/approve",
                    headers={"Authorization": "Bearer test-secret"},
                    json=approval.model_dump(mode="json"),
                )
                replay = client.post(
                    f"/v1/tasks/{task_id}/approve",
                    headers={"Authorization": "Bearer test-secret"},
                    json=approval.model_dump(mode="json"),
                )

            self.assertEqual(200, accepted.status_code)
            self.assertEqual(409, replay.status_code)
            final = TaskStore(database).get(task_id)
            self.assertEqual(
                IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                final.intake_authentication,
            )
            self.assertEqual(
                TaskStatus.OWNER_DECISION_REQUIRED,
                final.status,
                final.model_dump_json(),
            )
            self.assertIsNotNone(final.plan_interaction_id)
            self.assertGreater(final.plan_generation, preview.plan_generation)
            self.assertNotEqual(preview_plan_dump, final.plan.model_dump(mode="json"))
            self.assertEqual([(task_id, 1.0)], self._budget_rows(database))
            live_engine.director.plan.assert_awaited_once()


    def test_persisted_legacy_record_without_authentication_fails_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            task_store = TaskStore(database)
            record = _record()
            task_store.create(record)
            with sqlite3.connect(database) as connection:
                stored = json.loads(
                    connection.execute(
                        "SELECT record_json FROM tasks WHERE task_id = ?",
                        (record.task_id,),
                    ).fetchone()[0]
                )
                stored.pop("intake_authentication")
                connection.execute(
                    "UPDATE tasks SET record_json = ? WHERE task_id = ?",
                    (json.dumps(stored), record.task_id),
                )

            loaded = TaskStore(database).get(record.task_id)

            self.assertEqual(
                IntakeAuthentication.BEARER_DRY_RUN,
                loaded.intake_authentication,
            )


class SlackGatewayProofTests(unittest.TestCase):
    def _settings(self):
        return replace(
            main.settings,
            shared_secret="public-test-secret",
            slack_owner_user_id="U0B9UJ86D27",
            slack_channel_id="C123",
            slack_gateway_secret="distinct-gateway-secret-32-characters",
        )

    def _client_context(self, task_store: TaskStore):
        return (
            patch.object(main, "settings", self._settings()),
            patch.object(main, "store", task_store),
            patch.object(main.engine, "run", new=AsyncMock(return_value=None)),
            patch.object(
                main.usage_monitor,
                "run_forever",
                new=AsyncMock(return_value=None),
            ),
        )

    def test_slack_readiness_requires_gateway_secret_without_exposing_it(self):
        incomplete = replace(
            main.settings,
            slack_app_token="xapp-test",
            slack_bot_token="xoxb-test",
            slack_gateway_secret="",
        )
        ready = replace(
            incomplete,
            slack_gateway_secret="distinct-secret-value-32-characters",
        )
        self.assertFalse(incomplete.readiness()["slack"])
        self.assertTrue(ready.readiness()["slack"])
        self.assertNotIn(
            "distinct-secret-value-32-characters", json.dumps(ready.readiness())
        )

    def test_gateway_secret_equal_to_public_bearer_is_rejected(self):
        reused = "same-secret-value-at-least-32-characters"
        unsafe = replace(
            main.settings,
            shared_secret=reused,
            slack_app_token="xapp-test",
            slack_bot_token="xoxb-test",
            slack_gateway_secret=reused,
        )
        self.assertFalse(unsafe.readiness()["slack"])
        record = _record()
        approval = OwnerApproval(
            owner_user_id="U0B9UJ86D27",
            note="Approve the exact current plan",
            source=OwnerApprovalSource.SLACK_COMMAND,
        )
        with self.assertRaisesRegex(StaleApproval, "must be distinct"):
            build_slack_gateway_proof(
                reused,
                reused,
                record,
                0,
                approval,
                channel_id="C123",
                path=f"/v1/tasks/{record.task_id}/approve",
            )

    def test_tampered_note_and_expired_proof_fail_without_mutation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = _record()
            task_store.create(record)
            current, version = task_store.get_versioned(record.task_id)
            approval = OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                note="Approve the exact current plan",
                source=OwnerApprovalSource.SLACK_COMMAND,
            )
            signed = _signed_slack_approval(
                current,
                version,
                approval,
                secret="distinct-gateway-secret-32-characters",
                issued_at=int(datetime.now(timezone.utc).timestamp()),
            )
            before = current.model_dump(mode="json")
            contexts = self._client_context(task_store)
            with contexts[0], contexts[1], contexts[2], contexts[3], TestClient(
                main.app
            ) as client:
                tampered = signed.model_dump(mode="json")
                tampered["note"] = "Altered after signing"
                bad = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer public-test-secret"},
                    json=tampered,
                )
                expired_approval = _signed_slack_approval(
                    current,
                    version,
                    approval,
                    secret="distinct-gateway-secret-32-characters",
                    issued_at=int(datetime.now(timezone.utc).timestamp()) - 301,
                )
                expired = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer public-test-secret"},
                    json=expired_approval.model_dump(mode="json"),
                )
            self.assertEqual(bad.status_code, 409)
            self.assertEqual(expired.status_code, 409)
            self.assertEqual(task_store.get(record.task_id).model_dump(mode="json"), before)

    def test_valid_proof_is_single_use_across_restart_and_secret_is_not_stored(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database = str(Path(directory) / "director.sqlite3")
            first = TaskStore(database)
            record = _record()
            first.create(record)
            current, version = first.get_versioned(record.task_id)
            approval = _signed_slack_approval(
                current,
                version,
                OwnerApproval(
                    owner_user_id="U0B9UJ86D27",
                    note="Approve the exact current plan",
                    source=OwnerApprovalSource.SLACK_COMMAND,
                ),
                secret="distinct-gateway-secret-32-characters",
                nonce="N" * 32,
            )
            contexts = self._client_context(first)
            with contexts[0], contexts[1], contexts[2], contexts[3], TestClient(
                main.app
            ) as client:
                accepted = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer public-test-secret"},
                    json=approval.model_dump(mode="json"),
                )
            restarted = TaskStore(database)
            contexts = self._client_context(restarted)
            with contexts[0], contexts[1], contexts[2], contexts[3], TestClient(
                main.app
            ) as client:
                replay = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer public-test-secret"},
                    json=approval.model_dump(mode="json"),
                )
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(replay.status_code, 409)
            with sqlite3.connect(database) as connection:
                nonce_count = connection.execute(
                    "SELECT COUNT(*) FROM slack_gateway_nonces"
                ).fetchone()[0]
            self.assertEqual(nonce_count, 1)
            database_bytes = Path(database).read_bytes()
            self.assertNotIn(b"distinct-gateway-secret-32-characters", database_bytes)
            self.assertNotIn(("N" * 32).encode(), database_bytes)

    def test_public_bearer_cannot_forge_slack_transport_or_copy_a_proof_to_api(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = _record()
            task_store.create(record)
            current, version = task_store.get_versioned(record.task_id)
            slack = OwnerApproval(
                owner_user_id="U0B9UJ86D27",
                note="Approve the exact current plan",
                source=OwnerApprovalSource.SLACK_COMMAND,
            )
            signed = _signed_slack_approval(
                current,
                version,
                slack,
                secret="distinct-gateway-secret-32-characters",
            )
            before = current.model_dump(mode="json")
            contexts = self._client_context(task_store)
            with contexts[0], contexts[1], contexts[2], contexts[3], TestClient(
                main.app
            ) as client:
                missing = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer public-test-secret"},
                    json=slack.model_dump(mode="json"),
                )
                copied = signed.model_copy(
                    update={"source": OwnerApprovalSource.API}
                )
                forged = client.post(
                    f"/v1/tasks/{record.task_id}/approve",
                    headers={"Authorization": "Bearer public-test-secret"},
                    json=copied.model_dump(mode="json"),
                )
            self.assertEqual(missing.status_code, 409)
            self.assertEqual(forged.status_code, 409)
            self.assertEqual(task_store.get(record.task_id).model_dump(mode="json"), before)


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 10:02 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:21 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:53 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 09:57 UTC | END]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:47 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:19 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:41 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:28 UTC | END]]
