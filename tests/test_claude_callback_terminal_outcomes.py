# [[Codex | GPT-5.6 | 2026-08-24 | 11:58 UTC | BEGIN]]
from __future__ import annotations

import unittest

from director_orchestrator.callbacks import CallbackConflict, apply_claude_callback
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
    ProviderName,
    ProviderRun,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)


class ClaudeCallbackTerminalOutcomeTests(unittest.TestCase):
    INTERACTION_ID = "interaction-claude-terminal"
    RUN_ID = "claude-terminal-run"

    def _record(self) -> TaskRecord:
        evidence = build_transport_evidence(
            transport=ProviderTransport.DIRECTOR_BACKEND,
            source=TransportEvidenceSource.BACKEND_OBSERVATION,
            idempotency_key=f"{self.INTERACTION_ID}:drafted",
        )
        ledger = [
            start_interaction(
                task_id="DIR-CLAUDE-TERMINAL",
                interaction_id=self.INTERACTION_ID,
                plan_generation=0,
                assignment_sequence=1,
                provider=ProviderName.CLAUDE,
                phase=ProviderInteractionPhase.DISPATCH,
                attempt=1,
                transport_evidence=evidence,
            )
        ]
        for state, source, transport in (
            (
                ProviderInteractionState.ATTEMPTED,
                TransportEvidenceSource.BACKEND_OBSERVATION,
                ProviderTransport.DIRECTOR_BACKEND,
            ),
            (
                ProviderInteractionState.DISPATCHED,
                TransportEvidenceSource.AUTHENTICATED_PROVIDER,
                ProviderTransport.CLAUDE_ROUTINE,
            ),
            (
                ProviderInteractionState.ACKNOWLEDGED,
                TransportEvidenceSource.AUTHENTICATED_PROVIDER,
                ProviderTransport.CLAUDE_ROUTINE,
            ),
        ):
            transition_evidence = build_transport_evidence(
                transport=transport,
                source=source,
                idempotency_key=f"{self.INTERACTION_ID}:{state.value.lower()}",
                provider_run_id=(
                    self.RUN_ID
                    if source == TransportEvidenceSource.AUTHENTICATED_PROVIDER
                    else None
                ),
                native_status=(
                    "WAITING_CALLBACK"
                    if source == TransportEvidenceSource.AUTHENTICATED_PROVIDER
                    else None
                ),
            )
            event = transition_interaction(
                ledger,
                interaction_id=self.INTERACTION_ID,
                next_state=state,
                transport_evidence=transition_evidence,
            )
            ledger = append_interaction_event(ledger, event)
        return TaskRecord(
            task_id="DIR-CLAUDE-TERMINAL",
            status=TaskStatus.OWNER_DECISION_REQUIRED,
            intake=SlackIntake(
                source_event_id="evt-claude-terminal",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: callback terminal state",
            ),
            provider_runs=[
                ProviderRun(
                    provider=ProviderName.CLAUDE,
                    interaction_id=self.INTERACTION_ID,
                    assignment_sequence=1,
                    run_id=self.RUN_ID,
                    status="WAITING_CALLBACK",
                )
            ],
            provider_interaction_ledger=ledger,
        )

    def test_failed_and_cancelled_callbacks_record_failed_terminal_evidence(self) -> None:
        for status in ("failed", "cancelled"):
            with self.subTest(status=status):
                record = self._record()
                callback = ClaudeCallback(
                    run_id=self.RUN_ID,
                    status=status,
                    result=f"Claude reported {status}.",
                )

                self.assertTrue(apply_claude_callback(record, callback))

                run = record.provider_runs[0]
                self.assertEqual(run.status, status)
                self.assertEqual(run.result, callback.result)
                self.assertEqual(run.evidence_source, "provider_authenticated")
                self.assertEqual(record.status, TaskStatus.FAILED)
                self.assertEqual(
                    record.error,
                    f"ProviderError: claude ended with status {status}",
                )
                projection = project_interaction(
                    record.provider_interaction_ledger, self.INTERACTION_ID
                )
                self.assertEqual(projection.state, ProviderInteractionState.FAILED)
                terminal = record.provider_interaction_ledger[-1]
                self.assertEqual(terminal.state, ProviderInteractionState.FAILED)
                self.assertEqual(
                    terminal.transport_evidence.error_code,
                    f"PROVIDER_{status.upper()}",
                )
                self.assertEqual(
                    [event.state for event in record.provider_interaction_ledger],
                    [
                        ProviderInteractionState.DRAFTED,
                        ProviderInteractionState.ATTEMPTED,
                        ProviderInteractionState.DISPATCHED,
                        ProviderInteractionState.ACKNOWLEDGED,
                        ProviderInteractionState.FAILED,
                    ],
                )
                with self.assertRaises(CommunicationEvidenceFailure):
                    require_verified_result(
                        record.provider_interaction_ledger,
                        interaction_id=self.INTERACTION_ID,
                        result=callback.result,
                    )

                self.assertFalse(apply_claude_callback(record, callback))
                with self.assertRaises(CallbackConflict):
                    apply_claude_callback(
                        record,
                        callback.model_copy(update={"result": "Different failure detail"}),
                    )


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-24 | 11:58 UTC | END]]
