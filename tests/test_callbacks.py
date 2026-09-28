# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:40 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from pydantic import ValidationError

import director_orchestrator.main as main

from director_orchestrator.callbacks import (
    CallbackConflict,
    apply_claude_callback,
    apply_manual_run_completion,
    bearer_matches,
)
from director_orchestrator.interactions import (
    CommunicationEvidenceFailure,
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    project_interaction,
    require_verified_result,
    start_interaction,
    transition_interaction,
)
from director_orchestrator.models import (
    ClaudeCallback,
    ClaudeCallbackSideEffectEvidence,
    ManualRunCompletion,
    ProviderName,
    ProviderRun,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.approvals import reset_for_owner_clarification
from director_orchestrator.store import TaskStore


class ClaudeCallbackTests(unittest.TestCase):
    @staticmethod
    def _interaction_ledger(
        provider: ProviderName,
        run_id: str,
        interaction_id: str,
        *,
        task_id: str = "DIR-CALLBACK",
        assignment_sequence: int = 1,
    ) -> list:
        drafted = start_interaction(
            task_id=task_id,
            interaction_id=interaction_id,
            plan_generation=0,
            assignment_sequence=assignment_sequence,
            provider=provider,
            phase=ProviderInteractionPhase.DISPATCH,
            attempt=1,
            transport_evidence=build_transport_evidence(
                transport=ProviderTransport.DIRECTOR_BACKEND,
                source=TransportEvidenceSource.BACKEND_OBSERVATION,
                idempotency_key=f"{interaction_id}:drafted",
            ),
        )
        events = [drafted]
        for state, source, transport in (
            (
                ProviderInteractionState.ATTEMPTED,
                TransportEvidenceSource.BACKEND_OBSERVATION,
                ProviderTransport.DIRECTOR_BACKEND,
            ),
            (
                ProviderInteractionState.DISPATCHED,
                TransportEvidenceSource.BACKEND_OBSERVATION,
                ProviderTransport.DIRECTOR_BACKEND,
            ),
            (
                ProviderInteractionState.ACKNOWLEDGED,
                TransportEvidenceSource.AUTHENTICATED_PROVIDER,
                (
                    ProviderTransport.CLAUDE_ROUTINE
                    if provider == ProviderName.CLAUDE
                    else ProviderTransport.CURSOR_AGENTS_API
                ),
            ),
        ):
            event = transition_interaction(
                events,
                interaction_id=interaction_id,
                next_state=state,
                transport_evidence=build_transport_evidence(
                    transport=transport,
                    source=source,
                    idempotency_key=f"{interaction_id}:{state.value.lower()}",
                    provider_run_id=(
                        run_id
                        if source == TransportEvidenceSource.AUTHENTICATED_PROVIDER
                        else None
                    ),
                    native_status=(
                        "WAITING_CALLBACK"
                        if provider == ProviderName.CLAUDE
                        else "CREATING"
                    ),
                ),
            )
            events = append_interaction_event(events, event)
        return events

    def _record(self) -> TaskRecord:
        interaction_id = "interaction-claude-session-1"
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
                    interaction_id=interaction_id,
                    assignment_sequence=1,
                    run_id="claude-session-1",
                    status="WAITING_CALLBACK",
                )
            ],
            provider_interaction_ledger=self._interaction_ledger(
                ProviderName.CLAUDE,
                "claude-session-1",
                interaction_id,
            ),
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
        verified = require_verified_result(
            record.provider_interaction_ledger,
            interaction_id=record.provider_runs[0].interaction_id,
            result=callback.result,
        )
        self.assertEqual(verified.state, ProviderInteractionState.VERIFIED)
        self.assertFalse(apply_claude_callback(record, callback))

        conflicting = callback.model_copy(update={"result": "Different result"})
        with self.assertRaises(CallbackConflict):
            apply_claude_callback(record, conflicting)

    def test_legacy_terminal_callback_replays_only_without_effect_evidence(self):
        record = self._record()
        run = record.provider_runs[0]
        run.status = "completed"
        run.result = "Legacy terminal result."
        run.raw = {}
        callback = ClaudeCallback(
            run_id=run.run_id,
            status="completed",
            result=run.result,
        )

        self.assertFalse(apply_claude_callback(record, callback))
        changed = callback.model_copy(update={"result": "Different terminal result."})
        with self.assertRaisesRegex(CallbackConflict, "different terminal result"):
            apply_claude_callback(record, changed)
        effectful = callback.model_copy(
            update={
                "side_effect_evidence": ClaudeCallbackSideEffectEvidence(
                    schema_version="1.0",
                    task_id=record.task_id,
                    assignment_id="DIR-CALLBACK-1",
                    packet_idempotency_key="legacy-packet-key",
                    authority_snapshot_digest="sha256:" + "a" * 64,
                    correlation_id=str(run.interaction_id),
                    repository="example/demo",
                    starting_ref="main",
                    provider="claude",
                    provider_run_id=run.run_id,
                    result_digest=hashlib.sha256(run.result.encode("utf-8")).hexdigest(),
                    effects=[],
                )
            }
        )
        with self.assertRaisesRegex(CallbackConflict, "different terminal result"):
            apply_claude_callback(record, effectful)

    def test_callback_rejects_duplicate_pending_run_id_without_mutation(self):
        record = self._record()
        duplicate_interaction_id = "interaction-claude-session-duplicate"
        record.provider_runs.append(
            ProviderRun(
                provider=ProviderName.CLAUDE,
                interaction_id=duplicate_interaction_id,
                assignment_sequence=2,
                run_id="claude-session-1",
                status="WAITING_CALLBACK",
            )
        )
        record.provider_interaction_ledger.extend(
            self._interaction_ledger(
                ProviderName.CLAUDE,
                "claude-session-1",
                duplicate_interaction_id,
                assignment_sequence=2,
            )
        )
        runs_before = [run.model_dump(mode="json") for run in record.provider_runs]
        ledger_before = [
            event.model_dump(mode="json")
            for event in record.provider_interaction_ledger
        ]

        callback = ClaudeCallback(
            run_id="claude-session-1",
            status="completed",
            result="Ambiguous callback evidence must not be accepted.",
        )
        with self.assertRaises(CallbackConflict):
            apply_claude_callback(record, callback)

        self.assertEqual(
            [run.model_dump(mode="json") for run in record.provider_runs],
            runs_before,
        )
        self.assertEqual(
            [
                event.model_dump(mode="json")
                for event in record.provider_interaction_ledger
            ],
            ledger_before,
        )

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
            update={
                "provider": ProviderName.CURSOR,
                "interaction_id": "interaction-cursor-run",
                "run_id": "cursor-run",
                "status": "CREATING",
            }
        )
        record.provider_interaction_ledger = self._interaction_ledger(
            ProviderName.CURSOR,
            "cursor-run",
            "interaction-cursor-run",
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
        self.assertEqual(
            project_interaction(
                record.provider_interaction_ledger, "interaction-cursor-run"
            ).state,
            ProviderInteractionState.UNKNOWN,
        )
        with self.assertRaises(CommunicationEvidenceFailure):
            require_verified_result(
                record.provider_interaction_ledger,
                interaction_id="interaction-cursor-run",
                result="Independent review completed.",
            )
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
                    "interaction_id": "interaction-cursor-manual-run",
                    "run_id": "cursor-manual-run",
                    "status": "CREATING",
                }
            )
            manual_record.provider_interaction_ledger = self._interaction_ledger(
                ProviderName.CURSOR,
                "cursor-manual-run",
                "interaction-cursor-manual-run",
                task_id=manual_record.task_id,
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

    def test_authenticated_exact_callback_replay_is_an_http_no_op(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = self._record()
            task_store.create(record)
            settings = replace(
                main.settings,
                claude_callback_secret="callback-test-secret",
            )
            run = AsyncMock(return_value=None)
            payload = {
                "run_id": "claude-session-1",
                "status": "completed",
                "result": "Authenticated callback evidence",
            }
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
                first = client.post(
                    f"/v1/callbacks/claude/{record.task_id}",
                    headers={"Authorization": "Bearer callback-test-secret"},
                    json=payload,
                )
                first_version = task_store.get(record.task_id)._store_version
                replay = client.post(
                    f"/v1/callbacks/claude/{record.task_id}",
                    headers={"Authorization": "Bearer callback-test-secret"},
                    json=payload,
                )
                conflict = client.post(
                    f"/v1/callbacks/claude/{record.task_id}",
                    headers={"Authorization": "Bearer callback-test-secret"},
                    json={**payload, "result": "Conflicting callback evidence"},
                )

            self.assertEqual(first.status_code, 200)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(first_version, 1)
            self.assertEqual(task_store.get(record.task_id)._store_version, first_version)
            self.assertEqual(run.await_count, 1)

    def test_concurrent_exact_callbacks_have_one_cas_winner_and_one_no_op(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = self._record()
            task_store.create(record)
            callback = ClaudeCallback(
                run_id="claude-session-1",
                status="completed",
                result="Concurrent authenticated evidence",
            )
            barrier = threading.Barrier(2)
            calls_lock = threading.Lock()
            initial_reads = 0
            real_get_versioned = task_store.get_versioned

            def synchronized_get_versioned(task_id: str, **kwargs):
                nonlocal initial_reads
                result = real_get_versioned(task_id, **kwargs)
                with calls_lock:
                    initial_reads += 1
                    should_wait = initial_reads <= 2
                if should_wait:
                    barrier.wait(timeout=5)
                return result

            def submit_callback() -> tuple[TaskRecord, int]:
                background = BackgroundTasks()
                result = main.claude_callback(record.task_id, callback, background)
                return result, len(background.tasks)

            with (
                patch.object(main, "store", task_store),
                patch.object(task_store, "get_versioned", side_effect=synchronized_get_versioned),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                results = list(pool.map(lambda _: submit_callback(), range(2)))

            self.assertEqual(
                [result.status for result, _ in results],
                [TaskStatus.REVIEWING] * 2,
            )
            self.assertEqual(sum(task_count for _, task_count in results), 1)
            self.assertEqual(task_store.get(record.task_id)._store_version, 1)

    def test_stale_exact_replay_cannot_return_pre_clarification_private_state(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            task_store = TaskStore(str(Path(directory) / "director.sqlite3"))
            record = self._record()
            task_store.create(record)
            callback = ClaudeCallback(
                run_id="claude-session-1",
                status="completed",
                result="PRIVATE-STALE-CALLBACK-MARKER",
            )
            accepted = task_store.get(record.task_id)
            apply_claude_callback(accepted, callback)
            task_store.save(accepted)
            stale, stale_version = task_store.get_versioned(record.task_id)
            current = task_store.get(record.task_id)
            reset_for_owner_clarification(current)
            current.status = TaskStatus.PLANNING
            task_store.save(current)
            current_version = task_store.get(record.task_id)._store_version
            real_get_versioned = task_store.get_versioned
            calls = 0

            def stale_then_current(task_id: str, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return stale.model_copy(deep=True), stale_version
                return real_get_versioned(task_id, **kwargs)

            settings = replace(
                main.settings,
                claude_callback_secret="callback-test-secret",
            )
            run = AsyncMock(return_value=None)
            with (
                patch.object(main, "settings", settings),
                patch.object(main, "store", task_store),
                patch.object(task_store, "get_versioned", side_effect=stale_then_current),
                patch.object(main.engine, "run", new=run),
                patch.object(
                    main.usage_monitor,
                    "run_forever",
                    new=AsyncMock(return_value=None),
                ),
                TestClient(main.app) as client,
            ):
                run.reset_mock()
                response = client.post(
                    f"/v1/callbacks/claude/{record.task_id}",
                    headers={"Authorization": "Bearer callback-test-secret"},
                    json=callback.model_dump(mode="json"),
                )

            self.assertEqual(response.status_code, 409)
            self.assertNotIn("PRIVATE-STALE-CALLBACK-MARKER", response.text)
            self.assertEqual(task_store.get(record.task_id)._store_version, current_version)
            run.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

# [[Codex | GPT-5.6 | 2026-08-24 | 09:40 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:07 UTC | END]]
