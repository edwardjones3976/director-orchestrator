from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import director_orchestrator.main as main

from director_orchestrator.callbacks import (
    CallbackConflict,
    apply_claude_callback,
    apply_manual_run_completion,
    bearer_matches,
)
from director_orchestrator.models import (
    ClaudeCallback,
    ManualRunCompletion,
    ProviderName,
    ProviderRun,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore


class ClaudeCallbackTests(unittest.TestCase):
    def _record(self) -> TaskRecord:
        return TaskRecord(
            task_id="DIR-CALLBACK",
            status=TaskStatus.OWNER_DECISION_REQUIRED,
            intake=SlackIntake(
                source_event_id="evt-callback",
                channel_id="C123",
                thread_ts="123.47",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: callback",
            ),
            provider_runs=[
                ProviderRun(
                    provider=ProviderName.CLAUDE,
                    assignment_sequence=1,
                    run_id="claude-session-1",
                    status="WAITING_CALLBACK",
                )
            ],
        )

    def test_callback_uses_a_dedicated_constant_time_bearer_check(self):
        self.assertTrue(bearer_matches("callback-secret", "Bearer callback-secret"))
        self.assertFalse(bearer_matches("callback-secret", "Bearer wrong"))
        self.assertFalse(bearer_matches("", "Bearer callback-secret"))

    def test_callback_must_match_the_pending_run_and_is_idempotent(self):
        record = self._record()
        callback = ClaudeCallback(
            run_id="claude-session-1",
            status="completed",
            result="Tests passed with evidence.",
        )

        self.assertTrue(apply_claude_callback(record, callback))
        self.assertEqual(record.status, TaskStatus.REVIEWING)
        self.assertEqual(record.provider_runs[0].result, callback.result)
        self.assertEqual(
            record.provider_runs[0].evidence_source, "provider_authenticated"
        )
        self.assertFalse(apply_claude_callback(record, callback))

        conflicting = callback.model_copy(update={"result": "Different result"})
        with self.assertRaises(CallbackConflict):
            apply_claude_callback(record, conflicting)

    def test_callback_rejects_a_run_from_another_task(self):
        record = self._record()
        callback = ClaudeCallback(
            run_id="other-session",
            status="completed",
            result="Unexpected",
        )

        with self.assertRaises(CallbackConflict):
            apply_claude_callback(record, callback)

    def test_callback_schema_rejects_unexpected_fields(self):
        with self.assertRaises(ValidationError):
            ClaudeCallback.model_validate(
                {
                    "run_id": "claude-session-1",
                    "status": "completed",
                    "result": "Done",
                    "unexpected": "ignored payload must not cross the boundary",
                }
            )

    def test_owner_can_recover_exact_failed_run_with_manual_result(self):
        record = self._record()
        record.status = TaskStatus.FAILED
        record.provider_runs[0] = record.provider_runs[0].model_copy(
            update={"provider": ProviderName.CURSOR, "run_id": "cursor-run", "status": "CREATING"}
        )

        apply_manual_run_completion(
            record,
            "cursor-run",
            ManualRunCompletion(owner_user_id="U123", result="Independent review completed."),
        )

        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertEqual(record.provider_runs[0].status, "FINISHED")
        self.assertEqual(record.provider_runs[0].result, "Independent review completed.")
        self.assertEqual(record.provider_runs[0].evidence_source, "owner_recovery")
        with self.assertRaises(CallbackConflict):
            apply_manual_run_completion(
                record,
                "cursor-run",
                ManualRunCompletion(owner_user_id="U123", result="Different result"),
            )

    def test_authenticated_callback_and_manual_completion_endpoints_are_versioned(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            callback_record = self._record()
            task_store.create(callback_record)
            manual_record = self._record().model_copy(
                update={"task_id": "DIR-MANUAL-CALLBACK"}, deep=True
            )
            manual_record.intake.source_event_id = "evt-manual-callback"
            manual_record.status = TaskStatus.FAILED
            manual_record.provider_runs[0] = manual_record.provider_runs[0].model_copy(
                update={
                    "provider": ProviderName.CURSOR,
                    "run_id": "cursor-manual-run",
                    "status": "CREATING",
                }
            )
            task_store.create(manual_record)
            settings = replace(
                main.settings,
                shared_secret="public-test-secret",
                claude_callback_secret="callback-test-secret",
                slack_owner_user_id="U123",
            )
            run = AsyncMock(return_value=None)
            with (
                patch.object(main, "settings", settings),
                patch.object(main, "store", task_store),
                patch.object(main.engine, "run", new=run),
                patch.object(
                    main.usage_monitor,
                    "run_forever",
                    new=AsyncMock(return_value=None),
                ),
                TestClient(main.app) as client,
            ):
                callback = client.post(
                    f"/v1/callbacks/claude/{callback_record.task_id}",
                    headers={"Authorization": "Bearer callback-test-secret"},
                    json={
                        "run_id": "claude-session-1",
                        "status": "completed",
                        "result": "Authenticated callback evidence",
                    },
                )
                completion = client.post(
                    f"/v1/tasks/{manual_record.task_id}/runs/cursor-manual-run/complete",
                    headers={"Authorization": "Bearer public-test-secret"},
                    json={
                        "owner_user_id": "U123",
                        "result": "Owner supplied exact-run evidence",
                    },
                )

            self.assertEqual(callback.status_code, 200)
            self.assertEqual(callback.json()["status"], "REVIEWING")
            self.assertEqual(completion.status_code, 200)
            self.assertEqual(completion.json()["status"], "QUEUED")
            self.assertEqual(task_store.get(callback_record.task_id)._store_version, 1)
            self.assertEqual(task_store.get(manual_record.task_id)._store_version, 1)


if __name__ == "__main__":
    unittest.main()
