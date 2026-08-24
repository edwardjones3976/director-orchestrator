# [[Codex | GPT-5.6 | 2026-08-24 | 12:24 UTC | BEGIN]]
from __future__ import annotations

import unittest

from director_orchestrator.callbacks import CallbackConflict, apply_manual_run_completion
from director_orchestrator.interactions import (
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    project_interaction,
    start_interaction,
    transition_interaction,
)
from director_orchestrator.models import (
    ManualRunCompletion,
    ProviderName,
    ProviderRun,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)


class ManualTerminalRecoveryIntegrityTests(unittest.TestCase):
    run_id = "cursor-terminal-run"
    interaction_id = "interaction-cursor-terminal-run"

    def _ledger(
        self,
        terminal_state: ProviderInteractionState,
        *,
        bound_run_id: str | None = run_id,
    ) -> list:
        drafted = start_interaction(
            task_id="DIR-MANUAL-TERMINAL",
            interaction_id=self.interaction_id,
            plan_generation=0,
            assignment_sequence=1,
            provider=ProviderName.CURSOR,
            phase=ProviderInteractionPhase.DISPATCH,
            attempt=1,
            transport_evidence=build_transport_evidence(
                transport=ProviderTransport.DIRECTOR_BACKEND,
                source=TransportEvidenceSource.BACKEND_OBSERVATION,
                idempotency_key=f"{self.interaction_id}:drafted",
            ),
        )
        events = [drafted]
        for state in (
            ProviderInteractionState.ATTEMPTED,
            ProviderInteractionState.DISPATCHED,
            ProviderInteractionState.ACKNOWLEDGED,
        ):
            authenticated = state == ProviderInteractionState.ACKNOWLEDGED
            event = transition_interaction(
                events,
                interaction_id=self.interaction_id,
                next_state=state,
                transport_evidence=build_transport_evidence(
                    transport=(
                        ProviderTransport.CURSOR_AGENTS_API
                        if authenticated
                        else ProviderTransport.DIRECTOR_BACKEND
                    ),
                    source=(
                        TransportEvidenceSource.AUTHENTICATED_PROVIDER
                        if authenticated
                        else TransportEvidenceSource.BACKEND_OBSERVATION
                    ),
                    idempotency_key=f"{self.interaction_id}:{state.value.lower()}",
                    provider_run_id=bound_run_id if authenticated else None,
                    native_status="RUNNING" if authenticated else state.value,
                ),
            )
            events = append_interaction_event(events, event)

        terminal = transition_interaction(
            events,
            interaction_id=self.interaction_id,
            next_state=terminal_state,
            transport_evidence=build_transport_evidence(
                transport=ProviderTransport.DIRECTOR_BACKEND,
                source=TransportEvidenceSource.BACKEND_OBSERVATION,
                idempotency_key=f"{self.interaction_id}:{terminal_state.value.lower()}",
                provider_run_id=bound_run_id,
                native_status=terminal_state.value,
                error_code=f"PROVIDER_{terminal_state.value}",
                contact_attempt_ordinal=(
                    3 if terminal_state == ProviderInteractionState.UNAVAILABLE else None
                ),
            ),
        )
        return append_interaction_event(events, terminal)

    def _record(self, ledger: list) -> TaskRecord:
        return TaskRecord(
            task_id="DIR-MANUAL-TERMINAL",
            status=TaskStatus.FAILED,
            intake=SlackIntake(
                source_event_id="evt-manual-terminal",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR recover the exact terminal run",
            ),
            provider_runs=[
                ProviderRun(
                    provider=ProviderName.CURSOR,
                    interaction_id=self.interaction_id,
                    assignment_sequence=1,
                    run_id=self.run_id,
                    status="FAILED",
                )
            ],
            provider_interaction_ledger=ledger,
            error="Provider transport ended without an accepted result",
        )

    def test_owner_recovery_accepts_terminal_failed_without_rewriting_ledger(self):
        self._assert_terminal_recovery_preserves_ledger(ProviderInteractionState.FAILED)

    def test_owner_recovery_accepts_terminal_unavailable_without_rewriting_ledger(self):
        self._assert_terminal_recovery_preserves_ledger(
            ProviderInteractionState.UNAVAILABLE
        )

    def _assert_terminal_recovery_preserves_ledger(
        self, terminal_state: ProviderInteractionState
    ) -> None:
        record = self._record(self._ledger(terminal_state))
        original_events = list(record.provider_interaction_ledger)
        original_digests = [event.event_digest for event in original_events]

        apply_manual_run_completion(
            record,
            self.run_id,
            ManualRunCompletion(
                owner_user_id="U123",
                result="Owner-attested exact-run recovery result",
            ),
        )

        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertIsNone(record.error)
        self.assertEqual(record.provider_runs[0].status, "FINISHED")
        self.assertEqual(record.provider_runs[0].evidence_source, "owner_recovery")
        self.assertEqual(
            record.provider_runs[0].result,
            "Owner-attested exact-run recovery result",
        )
        self.assertEqual(record.provider_interaction_ledger, original_events)
        self.assertEqual(
            [event.event_digest for event in record.provider_interaction_ledger],
            original_digests,
        )
        self.assertEqual(
            project_interaction(
                record.provider_interaction_ledger, self.interaction_id
            ).state,
            terminal_state,
        )

        with self.assertRaisesRegex(
            CallbackConflict, "Task is not awaiting manual run recovery"
        ):
            apply_manual_run_completion(
                record,
                self.run_id,
                ManualRunCompletion(
                    owner_user_id="U123",
                    result="Owner-attested exact-run recovery result",
                ),
            )

    def test_terminal_recovery_rejects_mismatched_run_binding_without_mutation(self):
        record = self._record(
            self._ledger(
                ProviderInteractionState.FAILED,
                bound_run_id="different-provider-run",
            )
        )
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(
            CallbackConflict,
            "Provider interaction is bound to a different provider run",
        ):
            apply_manual_run_completion(
                record,
                self.run_id,
                ManualRunCompletion(
                    owner_user_id="U123",
                    result="Must not be accepted",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)

    def test_terminal_recovery_rejects_ambiguous_run_id_without_mutation(self):
        record = self._record(self._ledger(ProviderInteractionState.UNAVAILABLE))
        record.provider_runs.append(record.provider_runs[0].model_copy(deep=True))
        before = record.model_dump(mode="json")

        with self.assertRaisesRegex(
            CallbackConflict,
            "Provider run ID is ambiguous across task assignments",
        ):
            apply_manual_run_completion(
                record,
                self.run_id,
                ManualRunCompletion(
                    owner_user_id="U123",
                    result="Must not be accepted",
                ),
            )

        self.assertEqual(record.model_dump(mode="json"), before)


# [[Codex | GPT-5.6 | 2026-08-24 | 12:24 UTC | END]]
