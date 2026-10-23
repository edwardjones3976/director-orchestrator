# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:38 UTC | BEGIN]]
from __future__ import annotations

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from director_orchestrator.langgraph_primary import (
    AcceptanceDecision,
    AcceptanceResult,
    EvidenceDebateResult,
    HandlerContext,
    PhaseResult,
    PlanResult,
    PrimaryHandlers,
    ProviderDisposition,
    ScheduleResult,
)
from director_orchestrator.langgraph_runtime import (
    PacketBoundaryAdapter,
    ProviderAdapterContext,
)
from director_orchestrator.packet_contracts import document_digest, load_json_document
from director_orchestrator.packet_results import PacketResultProgression
from director_orchestrator.primary_live import DefaultLiveServices
from director_orchestrator.primary_providers import (
    PrimaryProviderBinding,
    identifier_digest,
)


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"


def _digest(character: str) -> str:
    return "sha256:" + character * 64


class _Transport:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls = 0

    def send(
        self, _payload: bytes, *, context: ProviderAdapterContext
    ) -> dict[str, object]:
        self.calls += 1
        return copy.deepcopy(self.result)


class _Planning:
    def __init__(self, work: dict[str, object]) -> None:
        self.work = work
        self.plan_calls = 0
        self.assignment = SimpleNamespace(sequence=1)
        self.record = SimpleNamespace(
            plan=SimpleNamespace(assignments=[self.assignment])
        )
        self._packet_router = SimpleNamespace(
            work_packet=lambda _record, _assignment: copy.deepcopy(self.work),
            planned_work_packet=(
                lambda _record, _assignment: copy.deepcopy(self.work)
            ),
        )
        self.authenticate_scope = Mock(return_value=PhaseResult(_digest("1")))
        self.canonicalize = Mock(return_value=PhaseResult(_digest("2")))
        self.policy_budget = Mock(return_value=PhaseResult(_digest("3")))
        self.schedule = Mock(return_value=ScheduleResult(_digest("4"), "claude"))

    def _record(self, _task_id: str):
        return self.record

    def plan(self, _context: HandlerContext) -> PlanResult:
        self.plan_calls += 1
        return PlanResult(_digest("5"), document_digest(self.work))

    def handlers(self) -> PrimaryHandlers:
        disabled = lambda _context: None
        return PrimaryHandlers(
            authenticate_scope=self.authenticate_scope,
            canonicalize=self.canonicalize,
            plan=self.plan,
            policy_budget=self.policy_budget,
            schedule=self.schedule,
            provider_subgraph=disabled,  # type: ignore[arg-type]
            evidence_debate=disabled,  # type: ignore[arg-type]
            openai_acceptance_revision=disabled,  # type: ignore[arg-type]
            exact_sha_review=disabled,  # type: ignore[arg-type]
            ci_security_completion=disabled,  # type: ignore[arg-type]
            effect_execution_readback=disabled,  # type: ignore[arg-type]
        )


class DefaultLiveServicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        self.result = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")
        self.provider = str(self.result["provider"])
        self.attempt = _digest("6")
        self.transport = _Transport(self.result)
        self.planning = _Planning(self.work)
        self.persisted: list[tuple[str, str]] = []
        self.activation_calls: list[str] = []
        self.evidence = EvidenceDebateResult(
            _digest("7"), _digest("8"), "b" * 40
        )
        self.acceptance = AcceptanceResult(
            _digest("9"), "b" * 40, AcceptanceDecision.ACCEPT
        )
        self.context = HandlerContext(
            task_id=str(self.work["task_id"]),
            source_event_id=str(self.work["correlation"]["source_event_id"]),
            correlation_id=str(self.work["correlation"]["correlation_id"]),
            intake_digest=_digest("a"),
            scope_digest=_digest("b"),
            authority_snapshot_digest=str(self.work["authority_snapshot_digest"]),
            work_packet_digest=document_digest(self.work),
            idempotency_key_digest=identifier_digest(str(self.work["idempotency_key"])),
            starting_sha="a" * 40,
            candidate_sha="b" * 40,
            plan_digest=_digest("5"),
            scheduled_producer_id=self.provider,
            provider_attempt_digest=self.attempt,
            provider_dispatch_status="DISPATCHING",
            provider_result_digest=document_digest(self.result),
            producer_id=self.provider,
            reviewer_id="cursor" if self.provider != "cursor" else "claude",
            revision_count=0,
            budget_limit_units=2,
            budget_used_units=1,
            effect_key_digest=_digest("c"),
            effect_attempt_digest=_digest("d"),
            writer_owner_id="langgraph-primary",
        )

    def _services(self, *, ready: bool = True, writer=None) -> DefaultLiveServices:
        binding = PrimaryProviderBinding(
            provider=self.provider,
            producer_id=self.provider,
            provider_attempt_digest=self.attempt,
            writer_token_digest=_digest("e"),
            fencing_generation=3,
            work_packet=self.work,
        )

        def activation(provider: str) -> bool:
            self.activation_calls.append(provider)
            return ready

        def persist(context, _packet, digest):
            self.persisted.append((context.task_id, digest))
            return digest

        return DefaultLiveServices(
            planning_services=self.planning,  # type: ignore[arg-type]
            adapters={
                self.provider: PacketBoundaryAdapter(self.provider, self.transport)
            },
            work_packet_artifact_writer=writer or persist,
            work_packet_binding_resolver=lambda _context: binding,
            candidate_sha_resolver=lambda _work, _result: "b" * 40,
            live_provider_activation=activation,
            writer_fence_resolver=lambda _context, selected: selected.fencing_generation,
            result_artifact_writer=lambda _context, _binding, _result, digest: digest,
            packet_result_resolver=lambda _context: None,
            completion_evidence_resolver=lambda _context: None,
            external_effect_resolver=lambda _context: None,
            evidence_debate_handler=lambda _context: self.evidence,
            acceptance_handler=lambda _context: self.acceptance,
        )

    def test_live_activation_callback_is_mandatory(self) -> None:
        with self.assertRaisesRegex(ValueError, "live_provider_activation"):
            DefaultLiveServices(
                planning_services=self.planning,  # type: ignore[arg-type]
                adapters={self.provider: PacketBoundaryAdapter(self.provider, self.transport)},
                work_packet_artifact_writer=lambda _c, _p, digest: digest,
                work_packet_binding_resolver=lambda _context: None,  # type: ignore[arg-type]
                candidate_sha_resolver=lambda _work, _result: "b" * 40,
                live_provider_activation=None,  # type: ignore[arg-type]
                writer_fence_resolver=lambda _context, binding: binding.fencing_generation,
                result_artifact_writer=lambda _context, _binding, _result, digest: digest,
                packet_result_resolver=lambda _context: None,
                completion_evidence_resolver=lambda _context: None,
                external_effect_resolver=lambda _context: None,
                evidence_debate_handler=lambda _context: self.evidence,
                acceptance_handler=lambda _context: self.acceptance,
            )

    def test_plan_fails_before_return_when_durable_persistence_is_not_acknowledged(self) -> None:
        service = self._services(
            writer=lambda _context, _packet, _packet_digest: _digest("f")
        )

        with self.assertRaisesRegex(RuntimeError, "persistence was not acknowledged"):
            service.plan(self.context)

        self.assertEqual(1, self.planning.plan_calls)

    def test_unready_live_provider_never_enters_transport(self) -> None:
        result = self._services(ready=False).handlers().provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.FAILED, result.disposition)
        self.assertEqual("provider_activation_not_ready", result.reason_code)
        self.assertEqual([self.provider], self.activation_calls)
        self.assertEqual(0, self.transport.calls)

    def test_full_handler_bundle_uses_primary_boundaries_only(self) -> None:
        service = self._services()
        service._completion.consume_provider_result = Mock(
            return_value=PacketResultProgression("SUCCEEDED", True, True, False, None)
        )
        exact = Mock(return_value="exact")
        completion = Mock(return_value="completion")
        effect = Mock(return_value="effect")
        service._completion.exact_sha_review = exact  # type: ignore[method-assign]
        service._completion.ci_security_completion = completion  # type: ignore[method-assign]
        service._completion.effect_execution_readback = effect  # type: ignore[method-assign]
        handlers = service.handlers()

        plan = handlers.plan(self.context)
        provider = handlers.provider_subgraph(self.context)
        evidence = handlers.evidence_debate(self.context)
        acceptance = handlers.openai_acceptance_revision(self.context)

        self.assertEqual(document_digest(self.work), plan.work_packet_digest)
        self.assertEqual([(self.context.task_id, document_digest(self.work))], self.persisted)
        self.assertEqual(ProviderDisposition.COMPLETED, provider.disposition)
        self.assertEqual(1, self.transport.calls)
        self.assertEqual(self.evidence, evidence)
        self.assertEqual(self.acceptance, acceptance)
        self.assertEqual("exact", handlers.exact_sha_review(self.context))
        self.assertEqual("completion", handlers.ci_security_completion(self.context))
        self.assertEqual("effect", handlers.effect_execution_readback(self.context))
        service._completion.consume_provider_result.assert_called_once_with(self.context)
        exact.assert_called_once_with(self.context)
        completion.assert_called_once_with(self.context)
        effect.assert_called_once_with(self.context)

    def test_acceptance_forwarding_preserves_durable_handler_contract(self):
        """Do not hide prepare/execute behind a lambda that loses admission."""
        service = self._services()
        class DurableHandler:
            def __call__(handler, context):
                return self.acceptance
            def prepare_acceptance(handler, context):
                raise AssertionError("forwarding itself must not admit or dispatch")
            def execute_acceptance(handler, *args):
                raise AssertionError("forwarding itself must not execute transport")
        handler = DurableHandler()
        service._acceptance_handler = handler
        self.assertIs(service.handlers().openai_acceptance_revision, handler)


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:46 UTC | BEGIN]]
# Live services require current writer fencing and durable result persistence.
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:46 UTC | END]]
if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
