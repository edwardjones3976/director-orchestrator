# [[OpenAI Codex | GPT-6 | 2026-09-27 | 15:06 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:04 UTC | BEGIN]]
from __future__ import annotations

import copy
import threading
import unittest
from pathlib import Path
from typing import Any, Mapping

from director_orchestrator.langgraph_primary import (
    HandlerContext,
    ProviderDisposition,
)
from director_orchestrator.langgraph_runtime import (
    PacketBoundaryAdapter,
    ProviderAdapterContext,
)
from director_orchestrator.packet_contracts import document_digest, load_json_document
from director_orchestrator.primary_providers import (
    PrimaryProviderBinding,
    PrimaryProviderBoundary,
    identifier_digest,
)


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"
SHA = "1" * 40


class RecordingTransport:
    def __init__(self, result: Mapping[str, Any], *, fail: bool = False) -> None:
        self.result = copy.deepcopy(dict(result))
        self.fail = fail
        self.calls = 0
        self.contexts: list[ProviderAdapterContext] = []

    def send(
        self, work_packet_json: bytes, *, context: ProviderAdapterContext
    ) -> Mapping[str, Any]:
        self.calls += 1
        self.contexts.append(context)
        if self.fail:
            raise TimeoutError("outcome unknown after submit")
        return copy.deepcopy(self.result)


class PrimaryProviderBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        self.result = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")
        self.attempt = "sha256:" + "2" * 64
        self.transport = RecordingTransport(self.result)
        self.binding = PrimaryProviderBinding(
            provider=str(self.result["provider"]),
            producer_id=str(self.result["provider"]),
            provider_attempt_digest=self.attempt,
            writer_token_digest="sha256:" + "3" * 64,
            fencing_generation=7,
            work_packet=self.work,
        )
        self.context = HandlerContext(
            task_id=str(self.work["task_id"]),
            source_event_id=str(self.work["correlation"]["source_event_id"]),
            correlation_id=str(self.work["correlation"]["correlation_id"]),
            intake_digest="sha256:" + "4" * 64,
            scope_digest="sha256:" + "5" * 64,
            authority_snapshot_digest=str(self.work["authority_snapshot_digest"]),
            work_packet_digest=document_digest(self.work),
            idempotency_key_digest=identifier_digest(str(self.work["idempotency_key"])),
            starting_sha="0" * 40,
            candidate_sha="0" * 40,
            plan_digest="sha256:" + "6" * 64,
            scheduled_producer_id=str(self.result["provider"]),
            provider_attempt_digest=self.attempt,
            provider_dispatch_status="DISPATCHING",
            provider_result_digest=None,
            producer_id=None,
            reviewer_id=None,
            revision_count=0,
            budget_limit_units=1,
            budget_used_units=1,
            effect_key_digest=None,
            effect_attempt_digest=None,
            writer_owner_id="langgraph-primary",
        )

    def service(self, *, transport: RecordingTransport | None = None):
        selected = transport or self.transport
        return PrimaryProviderBoundary(
            adapters={
                str(self.result["provider"]): PacketBoundaryAdapter(
                    str(self.result["provider"]), selected
                )
            },
            binding_resolver=lambda _context: self.binding,
            candidate_sha_resolver=lambda _work, _result: SHA,
            provider_readiness_resolver=lambda _provider: True,
            writer_fence_resolver=lambda _context, binding: binding.fencing_generation,
            result_artifact_writer=lambda _context, _binding, _result, digest: digest,
        )

    def test_success_is_translated_for_primary_handlers(self) -> None:
        outcome = self.service().provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.COMPLETED, outcome.disposition)
        self.assertEqual(SHA, outcome.candidate_sha)
        self.assertEqual(document_digest(self.result), outcome.result_digest)
        self.assertEqual(1, self.transport.calls)
        self.assertEqual(document_digest(self.work), self.transport.contexts[0].work_packet_digest)
        self.assertEqual(self.binding.writer_token_digest, self.transport.contexts[0].writer_token_digest)

    def test_exact_attempt_replay_never_dispatches_twice(self) -> None:
        service = self.service()

        first = service.provider_subgraph(self.context)
        replay = service.provider_subgraph(self.context)

        self.assertEqual(first, replay)
        self.assertEqual(1, self.transport.calls)

    def test_binding_drift_fails_before_transport(self) -> None:
        changed = copy.deepcopy(self.work)
        changed["objective"] = "A different unauthorized objective."
        self.binding = PrimaryProviderBinding(
            provider=self.binding.provider,
            producer_id=self.binding.producer_id,
            provider_attempt_digest=self.binding.provider_attempt_digest,
            writer_token_digest=self.binding.writer_token_digest,
            fencing_generation=self.binding.fencing_generation,
            work_packet=changed,
        )

        outcome = self.service().provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.FAILED, outcome.disposition)
        self.assertEqual("provider_binding_invalid", outcome.reason_code)
        self.assertEqual(0, self.transport.calls)

    def test_unknown_provider_fails_before_transport(self) -> None:
        self.binding = PrimaryProviderBinding(
            provider="unconfigured-provider",
            producer_id=self.binding.producer_id,
            provider_attempt_digest=self.binding.provider_attempt_digest,
            writer_token_digest=self.binding.writer_token_digest,
            fencing_generation=self.binding.fencing_generation,
            work_packet=self.work,
        )

        outcome = self.service().provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.FAILED, outcome.disposition)
        self.assertEqual(0, self.transport.calls)

    def test_transport_ambiguity_is_cached_and_never_retried(self) -> None:
        transport = RecordingTransport(self.result, fail=True)
        service = self.service(transport=transport)

        first = service.provider_subgraph(self.context)
        replay = service.provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.AMBIGUOUS, first.disposition)
        self.assertEqual(first, replay)
        self.assertEqual(1, transport.calls)

    def test_result_rebinding_is_ambiguous_and_never_retried(self) -> None:
        rebound = copy.deepcopy(self.result)
        rebound["idempotency_key"] = "another-attempt"
        transport = RecordingTransport(rebound)
        service = self.service(transport=transport)

        first = service.provider_subgraph(self.context)
        replay = service.provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.AMBIGUOUS, first.disposition)
        self.assertEqual(first, replay)
        self.assertEqual(1, transport.calls)

    def test_candidate_readback_failure_is_ambiguous_and_never_retried(self) -> None:
        def unavailable_candidate(_work, _result):
            raise RuntimeError("exact candidate SHA readback unavailable")

        service = PrimaryProviderBoundary(
            adapters={
                str(self.result["provider"]): PacketBoundaryAdapter(
                    str(self.result["provider"]), self.transport
                )
            },
            binding_resolver=lambda _context: self.binding,
            candidate_sha_resolver=unavailable_candidate,
            provider_readiness_resolver=lambda _provider: True,
            writer_fence_resolver=lambda _context, binding: binding.fencing_generation,
            result_artifact_writer=lambda _context, _binding, _result, digest: digest,
        )

        first = service.provider_subgraph(self.context)
        replay = service.provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.AMBIGUOUS, first.disposition)
        self.assertEqual(first, replay)
        self.assertEqual(1, self.transport.calls)

    def test_authenticated_non_success_is_a_bound_failure(self) -> None:
        failed_result = copy.deepcopy(self.result)
        failed_result["status"] = "FAILED"
        transport = RecordingTransport(failed_result)

        outcome = self.service(transport=transport).provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.FAILED, outcome.disposition)
        self.assertEqual(document_digest(failed_result), outcome.result_digest)
        self.assertIsNone(outcome.candidate_sha)
        self.assertEqual(1, transport.calls)

    def test_unready_provider_fails_before_transport(self) -> None:
        service = PrimaryProviderBoundary(
            adapters={
                str(self.result["provider"]): PacketBoundaryAdapter(
                    str(self.result["provider"]), self.transport
                )
            },
            binding_resolver=lambda _context: self.binding,
            candidate_sha_resolver=lambda _work, _result: SHA,
            provider_readiness_resolver=lambda _provider: False,
            writer_fence_resolver=lambda _context, binding: binding.fencing_generation,
            result_artifact_writer=lambda _context, _binding, _result, digest: digest,
        )

        outcome = service.provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.FAILED, outcome.disposition)
        self.assertEqual("provider_activation_not_ready", outcome.reason_code)
        self.assertEqual(0, self.transport.calls)

    def test_in_flight_reentry_never_enters_transport_twice(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingTransport(RecordingTransport):
            def send(self, work_packet_json: bytes, *, context: ProviderAdapterContext):
                self.calls += 1
                entered.set()
                release.wait(timeout=60)
                return copy.deepcopy(self.result)

        transport = BlockingTransport(self.result)
        service = self.service(transport=transport)
        first: list[Any] = []
        worker = threading.Thread(
            target=lambda: first.append(service.provider_subgraph(self.context))
        )
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=60), "transport did not enter within 60s")
            competing = service.provider_subgraph(self.context)
        finally:
            release.set()
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive(), "provider worker did not finish within 30s")

        self.assertEqual(ProviderDisposition.AMBIGUOUS, competing.disposition)
        self.assertEqual("provider_attempt_in_flight", competing.reason_code)
        self.assertEqual(ProviderDisposition.COMPLETED, first[0].disposition)
        self.assertEqual(1, transport.calls)

    def test_stale_generation_is_rejected_immediately_before_io(self) -> None:
        service = PrimaryProviderBoundary(
            adapters={
                str(self.result["provider"]): PacketBoundaryAdapter(
                    str(self.result["provider"]), self.transport
                )
            },
            binding_resolver=lambda _context: self.binding,
            candidate_sha_resolver=lambda _work, _result: SHA,
            provider_readiness_resolver=lambda _provider: True,
            writer_fence_resolver=lambda _context, binding: (
                binding.fencing_generation + 1
            ),
            result_artifact_writer=lambda _context, _binding, _result, digest: digest,
        )

        outcome = service.provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.AMBIGUOUS, outcome.disposition)
        self.assertEqual(0, self.transport.calls)

    def test_primary_context_carries_positive_generation(self) -> None:
        outcome = self.service().provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.COMPLETED, outcome.disposition)
        self.assertEqual(7, self.transport.contexts[0].fencing_generation)

    def test_result_persistence_ambiguity_is_cached_without_retry(self) -> None:
        service = PrimaryProviderBoundary(
            adapters={
                str(self.result["provider"]): PacketBoundaryAdapter(
                    str(self.result["provider"]), self.transport
                )
            },
            binding_resolver=lambda _context: self.binding,
            candidate_sha_resolver=lambda _work, _result: SHA,
            provider_readiness_resolver=lambda _provider: True,
            writer_fence_resolver=lambda _context, binding: binding.fencing_generation,
            result_artifact_writer=lambda *_args: (_ for _ in ()).throw(
                TimeoutError("result commit outcome unknown")
            ),
        )

        first = service.provider_subgraph(self.context)
        replay = service.provider_subgraph(self.context)

        self.assertEqual(ProviderDisposition.AMBIGUOUS, first.disposition)
        self.assertEqual(first, replay)
        self.assertEqual(1, self.transport.calls)

    def test_legacy_context_default_is_not_a_primary_writer_generation(self) -> None:
        context = ProviderAdapterContext(
            task_id="legacy-task",
            assignment_id="legacy-assignment",
            idempotency_key="legacy-attempt",
            work_packet_digest="sha256:" + "1" * 64,
            writer_token_digest="sha256:" + "2" * 64,
            timeout_seconds=1,
            budget_usd=0.0,
            max_hops=1,
            max_revisions=0,
            allowed_tools=(),
            allowed_external_effects=(),
        )

        self.assertEqual(0, context.fencing_generation)


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:46 UTC | BEGIN]]
# Zero-skip coverage proves current-generation fencing immediately precedes I/O.
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:46 UTC | END]]
if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:16 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 15:06 UTC | END]]
