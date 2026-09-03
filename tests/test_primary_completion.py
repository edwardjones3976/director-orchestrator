# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | BEGIN]]
from __future__ import annotations

import unittest
from types import SimpleNamespace

from director_orchestrator.completion_gates import VerifiedGateLedger, VerifiedGateResult
from director_orchestrator.langgraph_primary import (
    EffectDisposition,
    HandlerContext,
)
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.packet_results import PacketResultProgression
from director_orchestrator.primary_completion import (
    CompletionEvidence,
    ExternalEffectEvidence,
    PacketResultBinding,
    PrimaryCompletionServices,
)


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _context(**overrides: object) -> HandlerContext:
    values: dict[str, object] = {
        "task_id": "task-1",
        "source_event_id": "event-1",
        "correlation_id": "correlation-1",
        "intake_digest": _digest("1"),
        "scope_digest": _digest("2"),
        "authority_snapshot_digest": _digest("3"),
        "work_packet_digest": _digest("4"),
        "idempotency_key_digest": _digest("5"),
        "starting_sha": "1" * 40,
        "candidate_sha": "2" * 40,
        "plan_digest": _digest("6"),
        "scheduled_producer_id": "claude",
        "provider_attempt_digest": _digest("7"),
        "provider_dispatch_status": "COMPLETED",
        "provider_result_digest": _digest("8"),
        "producer_id": "claude",
        "reviewer_id": None,
        "revision_count": 0,
        "budget_limit_units": 2,
        "budget_used_units": 1,
        "effect_key_digest": _digest("9"),
        "effect_attempt_digest": _digest("a"),
        "writer_owner_id": "langgraph-primary",
    }
    values.update(overrides)
    return HandlerContext(**values)  # type: ignore[arg-type]


def _completion_evidence() -> CompletionEvidence:
    ledger_digest = _digest("b")
    ledger = VerifiedGateLedger(
        document={
            "task_id": "task-1",
            "assignment_id": "assignment-1",
            "work_packet_digest": _digest("4"),
            "expected_head_sha": "2" * 40,
            "candidate_result_digest": _digest("8"),
            "ledger_state": "HEAD_BOUND",
            "logical_producer": {"provider_id": "claude"},
            "director_gates": [
                {"gate_id": "review", "requiredness": "REQUIRED"},
                {"gate_id": "ci", "requiredness": "REQUIRED"},
                {"gate_id": "security", "requiredness": "REQUIRED"},
            ],
            "producer_added_gates": [],
        },
        digest=ledger_digest,
    )

    def result(gate_id: str, digest_character: str, *, review: bool = False) -> VerifiedGateResult:
        return VerifiedGateResult(
            document={
                "gate_id": gate_id,
                "ledger_digest": ledger_digest,
                "executed_sha": "2" * 40,
                "status": "PASS",
                "evidence_role": "INDEPENDENT_REVIEWER" if review else "GATE_RUNNER",
                "evidence_actor": {"logical_provider": "cursor" if review else "openai"},
            },
            digest=_digest(digest_character),
        )

    return CompletionEvidence(
        ledger=ledger,
        results=(result("review", "c", review=True), result("ci", "d"), result("security", "e")),
        ci_gate_id="ci",
        security_gate_id="security",
    )


class _ConsumerDouble:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def consume(self, *args: object) -> PacketResultProgression:
        self.calls.append(args)
        return PacketResultProgression("SUCCEEDED", True, True, False, None)


class PrimaryCompletionServicesTests(unittest.TestCase):
    def _services(
        self,
        *,
        completion: CompletionEvidence | None = None,
        effect: ExternalEffectEvidence | None = None,
        packet: PacketResultBinding | None = None,
        consumer: object | None = None,
    ) -> PrimaryCompletionServices:
        return PrimaryCompletionServices(
            packet_result_resolver=lambda _context: packet,
            completion_evidence_resolver=lambda _context: completion,
            external_effect_resolver=lambda _context: effect,
            packet_result_consumer=consumer,  # type: ignore[arg-type]
        )

    def test_consumes_packet_once_through_existing_consumer(self) -> None:
        consumer = _ConsumerDouble()
        record = SimpleNamespace(task_id="task-1")
        outcome = SimpleNamespace(result_packet_digest=_digest("8"))
        binding = PacketResultBinding(
            record=record,  # type: ignore[arg-type]
            assignment=SimpleNamespace(),  # type: ignore[arg-type]
            work_packet={},
            outcome=outcome,  # type: ignore[arg-type]
        )
        service = self._services(packet=binding, consumer=consumer)

        progression = service.consume_provider_result(_context())

        self.assertEqual(progression.packet_status, "SUCCEEDED")
        self.assertEqual(len(consumer.calls), 1)

    def test_packet_digest_mismatch_fails_before_consumer(self) -> None:
        consumer = _ConsumerDouble()
        binding = PacketResultBinding(
            record=SimpleNamespace(task_id="task-1"),  # type: ignore[arg-type]
            assignment=SimpleNamespace(),  # type: ignore[arg-type]
            work_packet={},
            outcome=SimpleNamespace(result_packet_digest=_digest("0")),  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(ValueError, "digest binding mismatch"):
            self._services(packet=binding, consumer=consumer).consume_provider_result(_context())
        self.assertEqual(consumer.calls, [])

    def test_exact_sha_review_uses_independent_verified_result(self) -> None:
        result = self._services(completion=_completion_evidence()).exact_sha_review(_context())

        self.assertTrue(result.accepted)
        self.assertEqual(result.reviewed_sha, "2" * 40)
        self.assertEqual(result.producer_id, "claude")
        self.assertEqual(result.reviewer_id, "cursor")
        self.assertEqual(result.review_digest, _digest("c"))

    def test_review_fails_closed_for_mismatched_packet_binding(self) -> None:
        result = self._services(completion=_completion_evidence()).exact_sha_review(
            _context(work_packet_digest=_digest("f"))
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason_code, "review_evidence_unavailable")

    def test_completion_requires_aggregate_ci_and_security_evidence(self) -> None:
        result = self._services(
            completion=_completion_evidence()
        ).ci_security_completion(_context())

        self.assertTrue(result.passed)
        self.assertEqual(result.ci_digest, _digest("d"))
        self.assertEqual(result.security_digest, _digest("e"))

    def test_completion_fails_closed_when_security_is_unavailable(self) -> None:
        evidence = _completion_evidence()
        evidence = CompletionEvidence(
            evidence.ledger,
            tuple(item for item in evidence.results if item.document["gate_id"] != "security"),
            evidence.ci_gate_id,
            evidence.security_gate_id,
        )

        result = self._services(completion=evidence).ci_security_completion(_context())

        self.assertFalse(result.passed)
        self.assertEqual(result.reason_code, "security_evidence_missing")

    def test_completed_effect_digests_result_and_readback(self) -> None:
        result_document = {"effect": "pull_request", "number": 12}
        readback_document = {"head_sha": "2" * 40, "state": "open"}
        evidence = ExternalEffectEvidence(
            effect_key_digest=_digest("9"),
            effect_attempt_digest=_digest("a"),
            writer_owner_id="langgraph-primary",
            disposition=EffectDisposition.COMPLETED,
            result=result_document,
            readback=readback_document,
        )

        result = self._services(effect=evidence).effect_execution_readback(_context())

        self.assertEqual(result.disposition, EffectDisposition.COMPLETED)
        self.assertEqual(result.result_digest, document_digest(result_document))
        self.assertEqual(result.readback_digest, document_digest(readback_document))

    def test_missing_completed_readback_becomes_ambiguous(self) -> None:
        evidence = ExternalEffectEvidence(
            effect_key_digest=_digest("9"),
            effect_attempt_digest=_digest("a"),
            writer_owner_id="langgraph-primary",
            disposition=EffectDisposition.COMPLETED,
            result={"effect": "slack_message"},
        )

        result = self._services(effect=evidence).effect_execution_readback(_context())

        self.assertEqual(result.disposition, EffectDisposition.AMBIGUOUS)
        self.assertEqual(result.reason_code, "effect_readback_unavailable")

    def test_effect_binding_mismatch_becomes_ambiguous_without_result(self) -> None:
        evidence = ExternalEffectEvidence(
            effect_key_digest=_digest("0"),
            effect_attempt_digest=_digest("a"),
            writer_owner_id="langgraph-primary",
            disposition=EffectDisposition.COMPLETED,
            result={"effect": "github"},
            readback={"observed": True},
        )

        result = self._services(effect=evidence).effect_execution_readback(_context())

        self.assertEqual(result.disposition, EffectDisposition.AMBIGUOUS)
        self.assertIsNone(result.result_digest)
        self.assertIsNone(result.readback_digest)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | END]]
