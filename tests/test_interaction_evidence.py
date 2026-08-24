# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:17 UTC | BEGIN]]
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from director_orchestrator.interactions import (
    LEGAL_INTERACTION_TRANSITIONS,
    TERMINAL_INTERACTION_STATES,
    CommunicationEvidenceFailure,
    ProviderInteractionEvent,
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    ProviderTransportEvidence,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    canonical_content_digest,
    find_verified_result,
    project_interaction,
    require_verified_result,
    start_interaction,
    transition_interaction,
)
from director_orchestrator.models import ProviderName, SlackIntake, TaskRecord, TaskStatus
from director_orchestrator.store import TaskStore


NOW = datetime(2026, 8, 24, 9, 17, tzinfo=timezone.utc)
RESULT = "bounded provider result"


class InteractionEvidenceTests(unittest.TestCase):
    def evidence(
        self,
        label: str,
        *,
        state: ProviderInteractionState | None = None,
        source: TransportEvidenceSource | None = None,
        result: str | None = None,
        provider_run_id: str | None = None,
        observed_at: datetime = NOW,
    ) -> ProviderTransportEvidence:
        if source is None:
            source = (
                TransportEvidenceSource.AUTHENTICATED_PROVIDER
                if state
                in {
                    ProviderInteractionState.ACKNOWLEDGED,
                    ProviderInteractionState.RESPONDED,
                    ProviderInteractionState.COMPLETED,
                    ProviderInteractionState.VERIFIED,
                }
                else TransportEvidenceSource.BACKEND_OBSERVATION
            )
        return build_transport_evidence(
            transport=(
                ProviderTransport.CURSOR_AGENTS_API
                if source == TransportEvidenceSource.AUTHENTICATED_PROVIDER
                else ProviderTransport.DIRECTOR_BACKEND
            ),
            source=source,
            idempotency_key=f"interaction-1:{label}",
            observed_at=observed_at,
            request="private request" if state == ProviderInteractionState.ATTEMPTED else None,
            response="private response" if source == TransportEvidenceSource.AUTHENTICATED_PROVIDER else None,
            provider_run_id=(
                provider_run_id
                or (
                    "run-1"
                    if source == TransportEvidenceSource.AUTHENTICATED_PROVIDER
                    else None
                )
            ),
            native_status=state.value.lower() if state else "drafted",
            http_status=200 if source == TransportEvidenceSource.AUTHENTICATED_PROVIDER else None,
            result=result,
            error_code=(
                "transport_failure"
                if state
                in {
                    ProviderInteractionState.FAILED,
                    ProviderInteractionState.UNKNOWN,
                    ProviderInteractionState.UNAVAILABLE,
                }
                else None
            ),
            ambiguity_reason=(
                "provider creation outcome cannot be determined"
                if state == ProviderInteractionState.UNKNOWN
                else None
            ),
            contact_attempt_ordinal=(
                3 if state == ProviderInteractionState.UNAVAILABLE else None
            ),
            sanitized_excerpt="safe excerpt",
        )

    def started(self) -> list[ProviderInteractionEvent]:
        event = start_interaction(
            task_id="DIR-INTERACTION",
            interaction_id="interaction-1",
            plan_generation=2,
            assignment_sequence=1,
            provider=ProviderName.CURSOR,
            phase=ProviderInteractionPhase.DISPATCH,
            attempt=1,
            effect_key="a" * 64,
            transport_evidence=self.evidence("draft"),
            recorded_at=NOW,
        )
        return [event]

    def append_state(
        self,
        events: list[ProviderInteractionEvent],
        state: ProviderInteractionState,
        label: str | None = None,
    ) -> list[ProviderInteractionEvent]:
        result = RESULT if state in {
            ProviderInteractionState.RESPONDED,
            ProviderInteractionState.COMPLETED,
            ProviderInteractionState.VERIFIED,
        } else None
        event = transition_interaction(
            events,
            interaction_id="interaction-1",
            next_state=state,
            transport_evidence=self.evidence(
                label or state.value,
                state=state,
                result=result,
                provider_run_id=(
                    "run-1"
                    if any(
                        item.transport_evidence.provider_run_id_digest is not None
                        for item in events
                    )
                    else None
                ),
            ),
            recorded_at=NOW,
        )
        return append_interaction_event(events, event)

    def chain_to(
        self, state: ProviderInteractionState
    ) -> list[ProviderInteractionEvent]:
        paths = {
            ProviderInteractionState.DRAFTED: [],
            ProviderInteractionState.ATTEMPTED: [ProviderInteractionState.ATTEMPTED],
            ProviderInteractionState.DISPATCHED: [
                ProviderInteractionState.ATTEMPTED,
                ProviderInteractionState.DISPATCHED,
            ],
            ProviderInteractionState.ACKNOWLEDGED: [
                ProviderInteractionState.ATTEMPTED,
                ProviderInteractionState.DISPATCHED,
                ProviderInteractionState.ACKNOWLEDGED,
            ],
            ProviderInteractionState.RESPONDED: [
                ProviderInteractionState.ATTEMPTED,
                ProviderInteractionState.DISPATCHED,
                ProviderInteractionState.ACKNOWLEDGED,
                ProviderInteractionState.RESPONDED,
            ],
            ProviderInteractionState.COMPLETED: [
                ProviderInteractionState.ATTEMPTED,
                ProviderInteractionState.DISPATCHED,
                ProviderInteractionState.ACKNOWLEDGED,
                ProviderInteractionState.RESPONDED,
                ProviderInteractionState.COMPLETED,
            ],
            ProviderInteractionState.VERIFIED: [
                ProviderInteractionState.ATTEMPTED,
                ProviderInteractionState.DISPATCHED,
                ProviderInteractionState.ACKNOWLEDGED,
                ProviderInteractionState.RESPONDED,
                ProviderInteractionState.COMPLETED,
                ProviderInteractionState.VERIFIED,
            ],
            ProviderInteractionState.FAILED: [ProviderInteractionState.FAILED],
            ProviderInteractionState.UNKNOWN: [
                ProviderInteractionState.ATTEMPTED,
                ProviderInteractionState.UNKNOWN,
            ],
            ProviderInteractionState.UNAVAILABLE: [
                ProviderInteractionState.ATTEMPTED,
                ProviderInteractionState.DISPATCHED,
                ProviderInteractionState.ACKNOWLEDGED,
                ProviderInteractionState.UNAVAILABLE,
            ],
        }
        events = self.started()
        for index, item in enumerate(paths[state], 1):
            events = self.append_state(events, item, f"path-{state.value}-{index}")
        return events

    def test_every_declared_legal_transition_is_accepted(self) -> None:
        for source, targets in LEGAL_INTERACTION_TRANSITIONS.items():
            for target in targets:
                with self.subTest(source=source, target=target):
                    events = self.chain_to(source)
                    event = transition_interaction(
                        events,
                        interaction_id="interaction-1",
                        next_state=target,
                        transport_evidence=self.evidence(
                            f"legal-{source.value}-{target.value}",
                            state=target,
                            result=(
                                RESULT
                                if target
                                in {
                                    ProviderInteractionState.RESPONDED,
                                    ProviderInteractionState.COMPLETED,
                                    ProviderInteractionState.VERIFIED,
                                }
                                else None
                            ),
                            provider_run_id=(
                                "run-1"
                                if any(
                                    item.transport_evidence.provider_run_id_digest
                                    is not None
                                    for item in events
                                )
                                else None
                            ),
                        ),
                        recorded_at=NOW,
                    )
                    appended = append_interaction_event(events, event)
                    self.assertEqual(appended[-1].state, target)

    def test_every_undeclared_and_terminal_transition_is_rejected(self) -> None:
        for source in ProviderInteractionState:
            events = self.chain_to(source)
            legal = LEGAL_INTERACTION_TRANSITIONS[source]
            for target in ProviderInteractionState:
                if target in legal:
                    continue
                with self.subTest(source=source, target=target):
                    with self.assertRaises(CommunicationEvidenceFailure):
                        transition_interaction(
                            events,
                            interaction_id="interaction-1",
                            next_state=target,
                            transport_evidence=self.evidence(
                                f"illegal-{source.value}-{target.value}",
                                state=target,
                                result=RESULT,
                            ),
                            recorded_at=NOW,
                        )
        self.assertEqual(
            TERMINAL_INTERACTION_STATES,
            {
                ProviderInteractionState.VERIFIED,
                ProviderInteractionState.FAILED,
                ProviderInteractionState.UNAVAILABLE,
                ProviderInteractionState.UNKNOWN,
            },
        )

    def test_transport_evidence_rejects_lifecycle_authority_from_provider_payload(self) -> None:
        valid = self.evidence("provider-payload", state=ProviderInteractionState.RESPONDED)
        payload = valid.model_dump(mode="json")
        payload["state"] = "VERIFIED"
        with self.assertRaises(ValidationError):
            ProviderTransportEvidence.model_validate(payload)

        provider_payload = {
            "provider": "cursor",
            "status": "VERIFIED",
            "result": "provider says it verified itself",
        }
        with self.assertRaises(ValidationError):
            ProviderInteractionEvent.model_validate(provider_payload)

    def test_evidence_and_event_digest_tampering_are_rejected(self) -> None:
        evidence = self.evidence("tamper", state=ProviderInteractionState.RESPONDED)
        evidence_payload = evidence.model_dump(mode="json")
        evidence_payload["native_status"] = "fabricated"
        with self.assertRaisesRegex(ValidationError, "transport evidence digest mismatch"):
            ProviderTransportEvidence.model_validate(evidence_payload)

        event = self.started()[0]
        event_payload = event.model_dump(mode="json")
        event_payload["task_id"] = "DIR-TAMPERED"
        with self.assertRaisesRegex(
            ValidationError, "provider interaction event digest mismatch"
        ):
            ProviderInteractionEvent.model_validate(event_payload)

    def test_append_is_idempotent_and_rejects_conflicting_same_ordinal(self) -> None:
        events = self.started()
        attempted = transition_interaction(
            events,
            interaction_id="interaction-1",
            next_state=ProviderInteractionState.ATTEMPTED,
            transport_evidence=self.evidence(
                "attempt-one", state=ProviderInteractionState.ATTEMPTED
            ),
            recorded_at=NOW,
        )
        once = append_interaction_event(events, attempted)
        twice = append_interaction_event(once, attempted)
        self.assertEqual(once, twice)

        conflicting = transition_interaction(
            events,
            interaction_id="interaction-1",
            next_state=ProviderInteractionState.ATTEMPTED,
            transport_evidence=self.evidence(
                "attempt-conflict", state=ProviderInteractionState.ATTEMPTED
            ),
            recorded_at=NOW,
        )
        with self.assertRaisesRegex(
            CommunicationEvidenceFailure, "conflicting event"
        ):
            append_interaction_event(once, conflicting)

    def test_provider_run_identity_cannot_change_or_disappear(self) -> None:
        events = self.chain_to(ProviderInteractionState.ACKNOWLEDGED)
        self.assertIsNotNone(events[-1].transport_evidence.provider_run_id_digest)

        for label, provider_run_id in (
            ("different", "run-2"),
            ("missing", None),
        ):
            with self.subTest(provider_run_id=label):
                evidence = build_transport_evidence(
                    transport=ProviderTransport.CURSOR_AGENTS_API,
                    source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
                    idempotency_key=f"interaction-1:{label}-run-id",
                    observed_at=NOW,
                    provider_run_id=provider_run_id,
                    native_status="responded",
                    result=RESULT,
                )
                with self.assertRaises(CommunicationEvidenceFailure):
                    event = transition_interaction(
                        events,
                        interaction_id="interaction-1",
                        next_state=ProviderInteractionState.RESPONDED,
                        transport_evidence=evidence,
                        recorded_at=NOW,
                    )
                    append_interaction_event(events, event)

    def test_verified_result_requires_exact_result_and_evidence_binding(self) -> None:
        events = self.chain_to(ProviderInteractionState.VERIFIED)
        latest = events[-1]
        verified = require_verified_result(
            events,
            interaction_id="interaction-1",
            result=RESULT,
            evidence_digest=latest.transport_evidence.evidence_digest,
        )
        self.assertEqual(verified.event_digest, latest.event_digest)
        self.assertEqual(
            project_interaction(events, "interaction-1").verified_result_digest,
            canonical_content_digest(RESULT),
        )
        self.assertIsNone(
            find_verified_result(
                events, interaction_id="interaction-1", result="different result"
            )
        )
        with self.assertRaisesRegex(
            CommunicationEvidenceFailure, "COMMUNICATION_EVIDENCE_FAILURE"
        ):
            require_verified_result(
                events,
                interaction_id="interaction-1",
                result=RESULT,
                evidence_digest="f" * 64,
            )

    def test_unknown_is_fail_closed_and_cannot_become_verified(self) -> None:
        events = self.chain_to(ProviderInteractionState.UNKNOWN)
        self.assertEqual(
            project_interaction(events, "interaction-1").state,
            ProviderInteractionState.UNKNOWN,
        )
        self.assertIsNone(
            find_verified_result(events, interaction_id="interaction-1", result=RESULT)
        )
        with self.assertRaises(CommunicationEvidenceFailure):
            transition_interaction(
                events,
                interaction_id="interaction-1",
                next_state=ProviderInteractionState.VERIFIED,
                transport_evidence=self.evidence(
                    "unknown-to-verified",
                    state=ProviderInteractionState.VERIFIED,
                    result=RESULT,
                ),
                recorded_at=NOW,
            )

    def test_verified_requires_authenticated_matching_completed_result(self) -> None:
        events = self.chain_to(ProviderInteractionState.COMPLETED)
        with self.assertRaisesRegex(
            CommunicationEvidenceFailure, "authenticated provider"
        ):
            transition_interaction(
                events,
                interaction_id="interaction-1",
                next_state=ProviderInteractionState.VERIFIED,
                transport_evidence=self.evidence(
                    "owner-verified",
                    state=ProviderInteractionState.VERIFIED,
                    source=TransportEvidenceSource.OWNER_ATTESTED,
                    result=RESULT,
                ),
                recorded_at=NOW,
            )
        with self.assertRaisesRegex(
            CommunicationEvidenceFailure, "changed after completion"
        ):
            transition_interaction(
                events,
                interaction_id="interaction-1",
                next_state=ProviderInteractionState.VERIFIED,
                transport_evidence=self.evidence(
                    "mismatched-result",
                    state=ProviderInteractionState.VERIFIED,
                    result="different result",
                ),
                recorded_at=NOW,
            )

    def test_unavailable_requires_three_real_attempts(self) -> None:
        events = self.chain_to(ProviderInteractionState.ACKNOWLEDGED)
        evidence = build_transport_evidence(
            transport=ProviderTransport.CURSOR_AGENTS_API,
            source=TransportEvidenceSource.BACKEND_OBSERVATION,
            idempotency_key="only-two-attempts",
            observed_at=NOW,
            error_code="timeout",
            contact_attempt_ordinal=2,
        )
        with self.assertRaisesRegex(
            CommunicationEvidenceFailure, "third real contact attempt"
        ):
            transition_interaction(
                events,
                interaction_id="interaction-1",
                next_state=ProviderInteractionState.UNAVAILABLE,
                transport_evidence=evidence,
                recorded_at=NOW,
            )

    def test_raw_transport_content_is_not_retained(self) -> None:
        secret = "super-secret-provider-token"
        evidence = build_transport_evidence(
            transport=ProviderTransport.OPENAI_RESPONSES_API,
            source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
            idempotency_key="private-input",
            observed_at=NOW,
            request=f"Authorization: Bearer {secret}",
            response=f"private response {secret}",
            result=f"private result {secret}",
            sanitized_excerpt="safe summary",
        )
        serialized = json.dumps(evidence.model_dump(mode="json"), sort_keys=True)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertEqual(
            evidence.result_digest,
            canonical_content_digest(f"private result {secret}"),
        )

    def test_store_persists_and_rehydrates_append_only_interaction_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "interactions.sqlite3")
            record = TaskRecord(
                task_id="DIR-INTERACTION",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-interaction",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Verify one provider result",
                ),
                provider_interaction_ledger=self.chain_to(
                    ProviderInteractionState.VERIFIED
                ),
            )
            TaskStore(database).create(record)

            restarted = TaskStore(database).get(record.task_id)
            self.assertIsNotNone(restarted)
            self.assertEqual(
                [event.event_digest for event in restarted.provider_interaction_ledger],
                [event.event_digest for event in record.provider_interaction_ledger],
            )
            self.assertEqual(
                project_interaction(
                    restarted.provider_interaction_ledger, "interaction-1"
                ).state,
                ProviderInteractionState.VERIFIED,
            )
            connection = sqlite3.connect(database)
            try:
                stored_json = connection.execute(
                    "SELECT record_json FROM tasks WHERE task_id = ?",
                    (record.task_id,),
                ).fetchone()[0]
                self.assertNotIn('"interaction_id":"interaction-1"', stored_json)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE provider_interaction_events SET state = 'FAILED' "
                        "WHERE task_id = ?",
                        (record.task_id,),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "DELETE FROM provider_interaction_events WHERE task_id = ?",
                        (record.task_id,),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """INSERT OR REPLACE INTO provider_interaction_events(
                            task_id, interaction_id, transition_ordinal, state,
                            event_digest, event_json, recorded_at
                        )
                        SELECT task_id, interaction_id, transition_ordinal, 'FAILED',
                            event_digest, event_json, recorded_at
                        FROM provider_interaction_events
                        WHERE task_id = ? AND interaction_id = ?
                            AND transition_ordinal = ?""",
                        (record.task_id, "interaction-1", 1),
                    )
            finally:
                connection.close()

    def test_simulation_transport_is_explicitly_labeled(self) -> None:
        evidence = build_transport_evidence(
            transport=ProviderTransport.SIMULATION,
            source=TransportEvidenceSource.SIMULATION,
            execution_mode="simulation",
            idempotency_key="simulation-only",
            observed_at=NOW,
            response="synthetic response",
        )
        self.assertEqual(evidence.execution_mode, "simulation")
        self.assertEqual(evidence.source, TransportEvidenceSource.SIMULATION)


if __name__ == "__main__":
    unittest.main()

# [[Codex | GPT-5.6 | 2026-08-24 | 09:17 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | END]]
