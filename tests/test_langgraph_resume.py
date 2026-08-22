# [[Codex | GPT-5.6 | 2026-08-22 16:07 UTC | BEGIN]]
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from director_orchestrator.approvals import apply_owner_approval
from director_orchestrator.callbacks import apply_claude_callback
from director_orchestrator.langgraph_resume import (
    LangGraphResumeRuntime,
    ResumeHandle,
    ResumeKind,
    ResumeStatus,
)
from director_orchestrator.models import (
    Assignment,
    ClaudeCallback,
    DirectorPlan,
    OwnerApproval,
    ProviderName,
    ProviderRun,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)

AUTHORITY_A = "sha256:" + "a" * 64
AUTHORITY_B = "sha256:" + "b" * 64


class LangGraphResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "resume.sqlite3"
        self.connections: list[sqlite3.Connection] = []

    def tearDown(self) -> None:
        for connection in self.connections:
            try:
                connection.close()
            except sqlite3.ProgrammingError:
                pass
        self.tempdir.cleanup()

    def runtime(
        self,
        *,
        graph_version: str = "director-shadow-resume.v1",
    ) -> tuple[LangGraphResumeRuntime, sqlite3.Connection]:
        connection = sqlite3.connect(self.database, check_same_thread=False)
        self.connections.append(connection)
        return (
            LangGraphResumeRuntime(
                SqliteSaver(connection),
                checkpoint_namespace="resume-tests",
                graph_version=graph_version,
            ),
            connection,
        )

    @staticmethod
    def record(*, two_assignments: bool = False) -> TaskRecord:
        assignments = [
            Assignment(
                sequence=1,
                agent=ProviderName.CLAUDE,
                task="Implement the bounded fixture.",
                acceptance=["The fixture remains local."],
                repository="edwardjones3976/director-orchestrator",
                requires_approval=True,
            )
        ]
        if two_assignments:
            assignments.append(
                Assignment(
                    sequence=2,
                    agent=ProviderName.CURSOR,
                    task="Review the exact candidate.",
                    acceptance=["Return an exact-SHA verdict."],
                    repository="edwardjones3976/director-orchestrator",
                    requires_approval=True,
                )
            )
        return TaskRecord(
            task_id="DIR-LANGGRAPH-RESUME",
            status=TaskStatus.OWNER_DECISION_REQUIRED,
            intake=SlackIntake(
                source_event_id="evt-langgraph-resume",
                channel_id="C0123456789",
                thread_ts="1787400000.000001",
                user_id="U0123456789",
                text="Run a bounded recovery proof.",
            ),
            plan=DirectorPlan(
                project="director-orchestrator",
                normalized_outcome="Prove authority-neutral checkpoint recovery.",
                risk_level=RiskLevel.MEDIUM,
                approval_required=False,
                max_hops=4,
                max_revisions=1,
                assignments=assignments,
                final_acceptance=["No legacy mutation or external effect."],
            ),
        )

    @staticmethod
    def approve(record: TaskRecord, sequence: int = 1) -> None:
        record.next_assignment_index = sequence - 1
        apply_owner_approval(
            record,
            OwnerApproval(owner_user_id="UOWNER", assignment_sequence=sequence),
        )

    @staticmethod
    def add_waiting_callback(record: TaskRecord, run_id: str = "claude-run-1") -> None:
        record.provider_runs.append(
            ProviderRun(
                provider=ProviderName.CLAUDE,
                assignment_sequence=1,
                run_id=run_id,
                status="WAITING_CALLBACK",
                evidence_source="provider_authenticated",
            )
        )

    def test_real_interrupt_persists_without_mutating_legacy_record(self) -> None:
        record = self.record()
        before = record.model_dump_json()
        runtime, _ = self.runtime()

        paused = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )

        self.assertEqual(paused.status, ResumeStatus.PAUSED)
        self.assertIsNotNone(paused.handle)
        self.assertEqual(record.model_dump_json(), before)
        snapshot = runtime._graph.get_state(runtime._config(paused.handle))
        self.assertEqual(snapshot.next, ("pause",))
        self.assertEqual(snapshot.values["phase"], ResumeStatus.PAUSED.value)
        self.assertNotIn("Implement the bounded fixture", str(snapshot.values))
        self.assertNotIn("Run a bounded recovery proof", str(snapshot.values))

    def test_unapproved_and_stale_assignment_resumes_fail_closed(self) -> None:
        record = self.record()
        runtime, _ = self.runtime()
        paused = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None

        unapproved = runtime.resume(record, paused.handle, AUTHORITY_A)
        self.assertEqual(unapproved.status, ResumeStatus.REJECTED)
        self.assertIn("assignment_is_approved", unapproved.reason)

        self.approve(record)
        record.plan.assignments[0].task = "A different assignment after approval."
        stale = runtime.resume(record, paused.handle, AUTHORITY_A)
        self.assertEqual(stale.status, ResumeStatus.REJECTED)
        self.assertIn("stale", stale.reason)

    def test_malformed_external_authority_digest_is_rejected_before_checkpoint(self) -> None:
        record = self.record()
        runtime, connection = self.runtime()

        result = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest="A" * 64,
        )

        self.assertEqual(result.status, ResumeStatus.REJECTED)
        self.assertIn("sha256:<64 lowercase hex>", result.reason)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertNotIn("checkpoints", tables)

    def test_other_external_authority_snapshot_cannot_touch_paused_checkpoint(self) -> None:
        record = self.record()
        runtime, _ = self.runtime()
        paused = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)

        result = runtime.resume(record, paused.handle, AUTHORITY_B)

        self.assertEqual(result.status, ResumeStatus.REJECTED)
        self.assertIn("authority snapshot digest is stale", result.reason)
        snapshot = runtime._graph.get_state(runtime._config(paused.handle))
        self.assertEqual(snapshot.next, ("pause",))
        self.assertEqual(snapshot.values["phase"], ResumeStatus.PAUSED.value)

    def test_assignment_mismatch_cannot_borrow_another_approval(self) -> None:
        record = self.record(two_assignments=True)
        runtime, _ = self.runtime()
        first = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert first.handle is not None
        self.approve(record, sequence=2)

        result = runtime.resume(record, first.handle, AUTHORITY_A)

        self.assertEqual(result.status, ResumeStatus.REJECTED)
        self.assertIn("assignment_is_approved", result.reason)

    def test_duplicate_approval_resume_is_idempotent(self) -> None:
        record = self.record()
        runtime, _ = self.runtime()
        paused = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)
        before = record.model_dump_json()

        first = runtime.resume(record, paused.handle, AUTHORITY_A)
        second = runtime.resume(record, paused.handle, AUTHORITY_A)

        self.assertEqual(first.status, ResumeStatus.RESUMED)
        self.assertEqual(second.status, ResumeStatus.ALREADY_RESUMED)
        self.assertEqual(first.resume_digest, second.resume_digest)
        self.assertEqual(record.model_dump_json(), before)

    def test_callback_requires_existing_authenticated_apply_result_and_replays(self) -> None:
        record = self.record()
        self.add_waiting_callback(record)
        runtime, _ = self.runtime()
        paused = runtime.begin(
            record,
            kind=ResumeKind.PROVIDER_CALLBACK,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
            callback_run_id="claude-run-1",
        )
        assert paused.handle is not None

        premature = runtime.resume(record, paused.handle, AUTHORITY_A)
        self.assertEqual(premature.status, ResumeStatus.REJECTED)
        self.assertIn("apply_claude_callback", premature.reason)

        callback = ClaudeCallback(
            run_id="claude-run-1",
            status="completed",
            result="Authenticated completion evidence.",
        )
        self.assertTrue(apply_claude_callback(record, callback))
        after_callback = record.model_dump_json()
        first = runtime.resume(record, paused.handle, AUTHORITY_A)
        self.assertFalse(apply_claude_callback(record, callback))
        replay = runtime.resume(record, paused.handle, AUTHORITY_A)

        self.assertEqual(first.status, ResumeStatus.RESUMED)
        self.assertEqual(replay.status, ResumeStatus.ALREADY_RESUMED)
        self.assertEqual(record.model_dump_json(), after_callback)

    def test_callback_run_mismatch_fails_before_checkpoint_resume(self) -> None:
        record = self.record()
        self.add_waiting_callback(record)
        runtime, _ = self.runtime()
        paused = runtime.begin(
            record,
            kind=ResumeKind.PROVIDER_CALLBACK,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
            callback_run_id="claude-run-1",
        )
        assert paused.handle is not None
        self.assertTrue(
            apply_claude_callback(
                record,
                ClaudeCallback(
                    run_id="claude-run-1",
                    status="completed",
                    result="Bound result.",
                ),
            )
        )
        tampered = ResumeHandle(
            task_id=paused.handle.task_id,
            kind=paused.handle.kind,
            assignment_sequence=paused.handle.assignment_sequence,
            callback_run_id="different-run",
            authority_snapshot_digest=paused.handle.authority_snapshot_digest,
            record_binding_digest=paused.handle.record_binding_digest,
            graph_version=paused.handle.graph_version,
            checkpoint_namespace=paused.handle.checkpoint_namespace,
            binding_digest=paused.handle.binding_digest,
            thread_id=paused.handle.thread_id,
        )

        result = runtime.resume(record, tampered, AUTHORITY_A)

        self.assertEqual(result.status, ResumeStatus.REJECTED)
        self.assertIn("callback run", result.reason)

    def test_process_restart_resumes_same_sqlite_checkpoint(self) -> None:
        record = self.record()
        first_runtime, first_connection = self.runtime()
        paused = first_runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)
        first_connection.close()

        restarted_runtime, _ = self.runtime()
        result = restarted_runtime.resume(record, paused.handle, AUTHORITY_A)

        self.assertEqual(result.status, ResumeStatus.RESUMED)
        snapshot = restarted_runtime._graph.get_state(
            restarted_runtime._config(paused.handle)
        )
        self.assertEqual(snapshot.next, ())
        self.assertEqual(snapshot.values["phase"], ResumeStatus.RESUMED.value)

    def test_crash_after_validation_before_command_is_restart_safe(self) -> None:
        record = self.record()
        first_runtime, first_connection = self.runtime()
        paused = first_runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)
        prepared = first_runtime.prepare_resume(record, paused.handle, AUTHORITY_A)
        first_connection.close()  # process exits after validation, before Command(resume)

        restarted_runtime, _ = self.runtime()
        result = restarted_runtime.commit_resume(record, prepared, AUTHORITY_A)

        self.assertEqual(result.status, ResumeStatus.RESUMED)
        self.assertEqual(result.resume_digest, prepared.resume_digest)

    def test_authority_change_after_prepare_is_rejected_at_commit_boundary(self) -> None:
        record = self.record()
        runtime, _ = self.runtime()
        paused = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)
        prepared = runtime.prepare_resume(record, paused.handle, AUTHORITY_A)

        result = runtime.commit_resume(record, prepared, AUTHORITY_B)

        self.assertEqual(result.status, ResumeStatus.REJECTED)
        self.assertIn("authority snapshot digest is stale", result.reason)
        snapshot = runtime._graph.get_state(runtime._config(paused.handle))
        self.assertEqual(snapshot.next, ("pause",))

    def test_crash_after_command_replays_completed_checkpoint_without_second_resume(self) -> None:
        record = self.record()
        first_runtime, first_connection = self.runtime()
        paused = first_runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)
        prepared = first_runtime.prepare_resume(record, paused.handle, AUTHORITY_A)
        committed = first_runtime.commit_resume(record, prepared, AUTHORITY_A)
        self.assertEqual(committed.status, ResumeStatus.RESUMED)
        first_connection.close()  # caller loses the successful response

        restarted_runtime, _ = self.runtime()
        replay = restarted_runtime.commit_resume(record, prepared, AUTHORITY_A)

        self.assertEqual(replay.status, ResumeStatus.ALREADY_RESUMED)
        self.assertEqual(replay.resume_digest, prepared.resume_digest)

    def test_missing_checkpoint_returns_control_to_legacy(self) -> None:
        record = self.record()
        runtime, connection = self.runtime()
        paused = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            if table in existing_tables:
                connection.execute(f"DELETE FROM {table}")
        connection.commit()

        result = runtime.resume(record, paused.handle, AUTHORITY_A)

        self.assertEqual(result.status, ResumeStatus.LEGACY_FALLBACK)
        self.assertIn("missing", result.reason)

    def test_corrupt_checkpoint_returns_control_to_legacy(self) -> None:
        record = self.record()
        runtime, connection = self.runtime()
        paused = runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)
        connection.execute("UPDATE checkpoints SET checkpoint = ?", (b"not-json",))
        connection.commit()

        result = runtime.resume(record, paused.handle, AUTHORITY_A)

        self.assertEqual(result.status, ResumeStatus.LEGACY_FALLBACK)
        self.assertIn("checkpoint", result.reason.lower())

    def test_graph_version_change_cannot_resume_old_checkpoint(self) -> None:
        record = self.record()
        first_runtime, first_connection = self.runtime(graph_version="resume.v1")
        paused = first_runtime.begin(
            record,
            kind=ResumeKind.ASSIGNMENT_APPROVAL,
            assignment_sequence=1,
            authority_snapshot_digest=AUTHORITY_A,
        )
        assert paused.handle is not None
        self.approve(record)
        first_connection.close()
        newer_runtime, _ = self.runtime(graph_version="resume.v2")

        result = newer_runtime.resume(record, paused.handle, AUTHORITY_A)

        self.assertEqual(result.status, ResumeStatus.REJECTED)
        self.assertIn("version", result.reason)


# [[Codex | GPT-5.6 | 2026-08-22 16:07 UTC | END]]
