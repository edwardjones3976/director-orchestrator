# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 16:22 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
"""The v1 packet contract is the live adapter boundary in both directions."""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from langgraph.checkpoint.sqlite import SqliteSaver

from director_orchestrator.langgraph_runtime import (
    LangGraphPacketRuntime,
    PacketBoundaryAdapter,
    ProviderAdapterContext,
    RuntimeExecutionMode,
    RuntimeExecutionStatus,
    WriterOwnershipToken,
)
from director_orchestrator.packet_contracts import (
    PacketContractError,
    document_digest,
    load_json_document,
)


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"


class RecordingTransport:
    """Stands in for a provider. It sees bytes and returns an unverified payload."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        self.result = copy.deepcopy(dict(result))
        self.received: list[bytes] = []
        self.contexts: list[ProviderAdapterContext] = []
        self.readbacks: list[tuple[bytes, Mapping[str, Any]]] = []

    def send(
        self, work_packet_json: bytes, *, context: ProviderAdapterContext
    ) -> Mapping[str, Any]:
        self.received.append(work_packet_json)
        self.contexts.append(context)
        return copy.deepcopy(self.result)

    def authenticated_readback(
        self, work_packet_json: bytes, provider_run: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.readbacks.append((work_packet_json, provider_run))
        return copy.deepcopy(self.result)


class PacketBoundaryAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        self.result = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")
        self.context = ProviderAdapterContext(
            task_id=self.work["task_id"],
            assignment_id=self.work["assignment_id"],
            idempotency_key=self.work["idempotency_key"],
            work_packet_digest=document_digest(self.work),
            writer_token_digest="sha256:" + "0" * 64,
            timeout_seconds=int(self.work["limits"]["timeout_seconds"]),
            budget_usd=float(self.work["limits"]["budget_usd"]),
            max_hops=int(self.work["limits"]["max_hops"]),
            max_revisions=int(self.work["limits"]["max_revisions"]),
            allowed_tools=tuple(self.work["allowed_tools"]),
            allowed_external_effects=tuple(self.work["allowed_external_effects"]),
        )

    def adapter(self, transport: RecordingTransport) -> PacketBoundaryAdapter:
        return PacketBoundaryAdapter(self.result["provider"], transport)

    def test_the_transport_receives_canonical_validated_packet_bytes(self):
        transport = RecordingTransport(self.result)

        returned = self.adapter(transport).dispatch(self.work, context=self.context)

        self.assertEqual(len(transport.received), 1)
        decoded = json.loads(transport.received[0].decode("utf-8"))
        self.assertEqual(decoded, self.work)
        # Canonical form is what the digest was taken over, so the provider and
        # the ledger agree byte for byte on what was authorized.
        self.assertEqual(document_digest(decoded), self.context.work_packet_digest)
        self.assertEqual(returned["idempotency_key"], self.work["idempotency_key"])

    def test_a_work_packet_that_is_not_the_authorized_one_fails_closed(self):
        transport = RecordingTransport(self.result)
        tampered = copy.deepcopy(self.work)
        tampered["limits"]["budget_usd"] = float(tampered["limits"]["budget_usd"]) + 1

        with self.assertRaises(PacketContractError):
            self.adapter(transport).dispatch(tampered, context=self.context)
        self.assertEqual(transport.received, [])

    def test_an_unvalidatable_provider_reply_never_leaves_the_boundary(self):
        for label, payload in {
            # A decoded list, scalar, or null is not a schema violation the
            # validator can locate, so each must be normalized onto the same
            # fail-closed path instead of escaping as an unclassified error.
            "not_an_object": ["nope"],
            "a_string": "nope",
            "null": None,
            "non_string_member_names": {1: "nope"},
            # Genuine schema failures keep reporting through the validator.
            "missing_fields": {"provider": self.result["provider"]},
            "wrong_schema_version": {**self.result, "schema_version": "0.9"},
        }.items():
            with self.subTest(case=label):
                transport = RecordingTransport({"placeholder": True})
                transport.result = payload
                with self.assertRaises(PacketContractError):
                    self.adapter(transport).dispatch(self.work, context=self.context)

    def test_a_non_object_work_packet_also_fails_the_packet_contract(self):
        transport = RecordingTransport(self.result)
        for payload in (["nope"], "nope", None):
            with self.subTest(payload=type(payload).__name__):
                with self.assertRaises(PacketContractError):
                    self.adapter(transport).dispatch(payload, context=self.context)
        self.assertEqual(transport.received, [])

    def test_a_reply_that_is_not_linked_to_this_work_packet_fails_closed(self):
        mismatched = copy.deepcopy(self.result)
        mismatched["idempotency_key"] = "not-the-authorized-key"
        transport = RecordingTransport(mismatched)

        with self.assertRaises(PacketContractError):
            self.adapter(transport).dispatch(self.work, context=self.context)

    def test_a_reply_claiming_another_provider_fails_closed(self):
        transport = RecordingTransport(self.result)
        adapter = PacketBoundaryAdapter("some-other-provider", transport)

        with self.assertRaises(PacketContractError):
            adapter.dispatch(self.work, context=self.context)

    def test_an_empty_provider_identity_is_refused(self):
        with self.assertRaises(ValueError):
            PacketBoundaryAdapter("  ", RecordingTransport(self.result))

    def test_authenticated_readback_requires_current_packet_correlation(self):
        transport = RecordingTransport(self.result)
        provider_run = {
            "provider": self.result["provider"],
            "interaction_id": "stale-generation-correlation",
            "run_id": self.result["run_reference"],
            "status": "completed",
            "result": self.result["outcome"],
            "evidence_source": "provider_authenticated",
        }

        with self.assertRaisesRegex(PacketContractError, "current work-packet correlation"):
            asyncio.run(
                self.adapter(transport).authenticated_readback(
                    self.work, provider_run
                )
            )

        self.assertEqual([], transport.readbacks)


class BoundaryAdapterInRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.work = load_json_document(SCHEMAS / "model-work-packet-v1.example.json")
        self.result = load_json_document(SCHEMAS / "model-result-packet-v1.example.json")
        from tests.test_engine_provider_spend_settlement import ProviderSpendSettlementTests
        _, store, record, _ = ProviderSpendSettlementTests()._world(str(root))
        legacy = Path(store.path)
        for packet in (self.work, self.result):
            packet["task_id"] = record.task_id
        self.work["idempotency_key"] = (
            f'{record.task_id}:{record.plan_generation}:{self.work["idempotency_key"]}'
        )
        self.result["idempotency_key"] = self.work["idempotency_key"]
        self.connection = sqlite3.connect(
            root / "runtime.sqlite3", check_same_thread=False
        )
        self.addCleanup(self.connection.close)
        self.runtime = LangGraphPacketRuntime(
            SqliteSaver(self.connection),
            ledger_database_path=str(root / "ledger.sqlite3"),
            legacy_database_path=str(legacy),
            checkpoint_namespace="boundary-test-v1",
            runtime_active=True,
            allow_live_provider_dispatch=True,
            require_packet_boundary_adapter=True,
        )

    def test_a_boundary_adapter_completes_and_never_dispatches_twice(self):
        transport = RecordingTransport(self.result)
        adapter = PacketBoundaryAdapter(self.result["provider"], transport)
        token = WriterOwnershipToken.issue(
            task_id=self.work["task_id"], owner_id="langgraph"
        )

        first = self.runtime.run_packet(
            self.work,
            provider=self.result["provider"],
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )
        second = self.runtime.run_packet(
            self.work,
            provider=self.result["provider"],
            execution_mode=RuntimeExecutionMode.LIVE_SAFE,
            adapter=adapter,
            writer_token=token,
        )

        self.assertEqual(first.status, RuntimeExecutionStatus.COMPLETED)
        self.assertEqual(second.status, RuntimeExecutionStatus.REPLAYED)
        self.assertEqual(len(transport.received), 1)


if __name__ == "__main__":
    unittest.main()
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 16:22 UTC | END]]
