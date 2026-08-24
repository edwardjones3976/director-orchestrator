# [[Codex | GPT-5.6 | 2026-08-24 | 12:12 UTC | BEGIN]]
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import CommunicationEvidenceFailure
from director_orchestrator.models import (
    Assignment,
    ProviderName,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore


class FailTwiceNotifier:
    def __init__(self) -> None:
        self.attempt_ids: list[str] = []
        self.messages: list[str] = []
        self.delivered = 0

    async def post(self, record: TaskRecord, message: str, **kwargs) -> str:
        self.attempt_ids.append(str(kwargs["client_msg_id"]))
        self.messages.append(message)
        if len(self.attempt_ids) < 3:
            raise RuntimeError("temporary Slack outage")
        self.delivered += 1
        return "notice-1"


class CollaborationEvidenceStopNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_provider_evidence_posts_one_deduplicated_owner_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                slack_bot_token="test-slack",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="Return bounded evidence",
                acceptance=["Evidence is authenticated"],
            )
            record = TaskRecord(
                task_id="DIR-COLLAB-EVIDENCE-STOP",
                status=TaskStatus.RUNNING,
                intake=SlackIntake(
                    source_event_id="evt-collab-evidence-stop",
                    channel_id="C123",
                    thread_ts="123.45",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: verify evidence stop notice",
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(record)
            notifier = FailTwiceNotifier()
            engine = OrchestrationEngine(settings, store)
            engine.notifier = notifier
            run = ProviderRun(
                provider=ProviderName.CURSOR,
                assignment_sequence=1,
                run_id="cursor-run-without-interaction-binding",
                status="FINISHED",
                result="unbound provider result",
                evidence_source="provider_authenticated",
            )
            decision = ReviewDecision(
                verdict=ReviewVerdict.ACCEPT,
                summary="Result otherwise appears acceptable.",
            )

            first = await engine._record_and_publish_collaboration_turn(
                record, assignment, run, decision
            )
            recovered = store.get(record.task_id)
            second = await engine._record_and_publish_collaboration_turn(
                recovered, assignment, run, decision
            )

            persisted = store.get(record.task_id)
            self.assertFalse(first)
            self.assertFalse(second)
            self.assertEqual(
                persisted.status, TaskStatus.OWNER_DECISION_REQUIRED
            )
            self.assertEqual(
                persisted.message, CommunicationEvidenceFailure.code
            )
            self.assertIn("interaction evidence is missing", persisted.error)
            self.assertEqual(len(notifier.attempt_ids), 3)
            self.assertEqual(len(set(notifier.attempt_ids)), 1)
            self.assertEqual(notifier.delivered, 1)
            self.assertTrue(
                all("OWNER DECISION REQUIRED" in item for item in notifier.messages)
            )
            self.assertEqual(len(persisted.collaboration_delivery_errors), 2)
            self.assertEqual(
                [
                    item.kind
                    for item in persisted.task_effect_ledger
                    if item.phase == "slack_delivery"
                ],
                ["slack_intent", "slack_observation"],
            )


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 12:12 UTC | END]]
