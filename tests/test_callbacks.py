from __future__ import annotations

import unittest

from pydantic import ValidationError

from director_orchestrator.callbacks import (
    CallbackConflict,
    apply_claude_callback,
    bearer_matches,
)
from director_orchestrator.models import (
    ClaudeCallback,
    ProviderName,
    ProviderRun,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)


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


if __name__ == "__main__":
    unittest.main()
