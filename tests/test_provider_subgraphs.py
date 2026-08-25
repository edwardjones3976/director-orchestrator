# [[Codex | GPT-5.6 | 2026-08-25 | 00:17 UTC | BEGIN]]
from __future__ import annotations

import unittest

from langgraph.checkpoint.memory import InMemorySaver

from director_orchestrator.provider_subgraphs import (
    PROVIDER_SUBGRAPH_SCHEMA_VERSION,
    ProviderSubgraphPhase,
    build_provider_subgraph,
)


DIGEST_A = "sha256:" + ("a" * 64)
DIGEST_B = "sha256:" + ("b" * 64)
DIGEST_C = "sha256:" + ("c" * 64)


class ProviderSubgraphTests(unittest.TestCase):
    def _state(self) -> dict[str, object]:
        return {
            "schema_version": PROVIDER_SUBGRAPH_SCHEMA_VERSION,
            "task_id": "DIR-RUNTIME",
            "assignment_id": "DIR-RUNTIME-1",
            "idempotency_key_digest": DIGEST_A,
            "work_packet_digest": DIGEST_B,
            "authority_snapshot_digest": DIGEST_C,
            "provider": "claude",
            "execution_mode": "live_safe",
            "writer_token_digest": DIGEST_A,
            "phase": ProviderSubgraphPhase.VALIDATING.value,
            "visited_nodes": [],
        }

    def test_graph_is_bounded_and_checkpoints_only_compact_evidence(self):
        calls = 0

        def execute():
            nonlocal calls
            calls += 1
            return {
                "result_packet_digest": DIGEST_A,
                "result_status": "SUCCEEDED",
                "run_reference_digest": DIGEST_B,
            }

        saver = InMemorySaver()
        graph = build_provider_subgraph(execute, checkpointer=saver)
        config = {"configurable": {"thread_id": "private:test"}}
        result = graph.invoke(self._state(), config=config)

        self.assertEqual(calls, 1)
        self.assertEqual(result["phase"], ProviderSubgraphPhase.COMPLETED.value)
        self.assertEqual(
            result["visited_nodes"],
            [
                "validate_packet_binding",
                "execute_provider_packet",
                "complete_provider_packet",
            ],
        )
        checkpoint = saver.get_tuple(config).checkpoint["channel_values"]
        serialized = repr(checkpoint)
        self.assertNotIn("prompt", serialized.casefold())
        self.assertNotIn("objective", serialized.casefold())
        self.assertNotIn("outcome", serialized.casefold())

    def test_live_state_without_writer_digest_fails_before_hook(self):
        calls = 0

        def execute():
            nonlocal calls
            calls += 1
            return {}

        state = self._state()
        state["writer_token_digest"] = None
        with self.assertRaisesRegex(ValueError, "writer ownership"):
            build_provider_subgraph(execute).invoke(state)
        self.assertEqual(calls, 0)

    def test_malformed_digest_fails_before_hook(self):
        calls = 0

        def execute():
            nonlocal calls
            calls += 1
            return {}

        state = self._state()
        state["work_packet_digest"] = "sha256:not-a-digest"
        with self.assertRaisesRegex(ValueError, "work packet"):
            build_provider_subgraph(execute).invoke(state)
        self.assertEqual(calls, 0)

    def test_incomplete_execution_evidence_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "incomplete compact evidence"):
            build_provider_subgraph(lambda: {"result_status": "SUCCEEDED"}).invoke(
                self._state()
            )


if __name__ == "__main__":
    unittest.main()

# [[Codex | GPT-5.6 | 2026-08-25 | 00:17 UTC | END]]
