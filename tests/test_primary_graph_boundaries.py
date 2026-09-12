# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:06 UTC | BEGIN]]
"""Actual compiled graph input/output boundaries and v1 checkpoint compatibility."""
import unittest
from unittest.mock import patch

from langgraph.channels import LastValue
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph

from director_orchestrator.langgraph_primary import (
    PrimaryGraphInput,
    PrimaryGraphOutput,
    PrimaryGraphState,
    PrimaryPhase,
    PrimaryRequest,
    build_primary_graph,
    primary_checkpoint_config,
)
from tests.test_langgraph_primary import _digest, _handlers, _request


class PrimaryGraphBoundaryTests(unittest.TestCase):
    def test_schemas_expose_distinct_request_report_and_internal_fields(self):
        inputs = set(PrimaryGraphInput.__annotations__)
        outputs = set(PrimaryGraphOutput.__annotations__)
        internal = set(PrimaryGraphState.__annotations__)
        self.assertEqual(inputs, set(PrimaryRequest.__dataclass_fields__) | {"schema_version", "input_digest"})
        self.assertLess(inputs, internal)
        self.assertLess(outputs, internal)
        self.assertNotIn("execution_approval_receipt_digest", inputs | outputs)
        self.assertNotIn("budget_used_units", inputs)
        self.assertNotIn("scope_digest", outputs)

    def test_compiled_input_filters_forged_internal_authority_and_initializes_defaults(self):
        request = _request("DIR-INPUT-BOUNDARY")
        graph = build_primary_graph(_handlers(), checkpointer=InMemorySaver(),
                                    interrupt_after=[PrimaryPhase.AUTHENTICATE_SCOPE.value])
        config = primary_checkpoint_config(request.task_id)
        payload = request.graph_input()
        payload.update(terminal_classification="SUCCEEDED", budget_used_units=-900,
                       execution_approval_receipt_digest=_digest("forged"),
                       plan_digest=_digest("forged-plan"), candidate_sha="b" * 40,
                       visit_count=200, visited_nodes=["forged"])
        graph.invoke(payload, config)
        state = graph.get_state(config).values
        self.assertIsNone(state["terminal_classification"])
        self.assertEqual(state["budget_used_units"], 0)
        self.assertIsNone(state["execution_approval_receipt_digest"])
        self.assertIsNone(state.get("plan_digest"))
        self.assertEqual(state["candidate_sha"], request.starting_sha)
        self.assertEqual(state["visit_count"], 1)
        self.assertEqual(state["visited_nodes"], [PrimaryPhase.AUTHENTICATE_SCOPE.value])

    def test_output_projection_retains_internal_checkpoint_for_audit(self):
        request = _request("DIR-OUTPUT-BOUNDARY")
        graph = build_primary_graph(_handlers(), checkpointer=InMemorySaver(),
                                    interrupt_after=[PrimaryPhase.CANONICALIZE.value])
        config = primary_checkpoint_config(request.task_id)
        result = graph.invoke(request.graph_input(), config)
        state = graph.get_state(config).values
        self.assertEqual(result["task_id"], request.task_id)
        self.assertEqual(result["visit_count"], 2)
        self.assertLessEqual(set(result) - {"__interrupt__"}, set(PrimaryGraphOutput.__annotations__))
        for key in ("intake_digest", "input_digest", "canonical_digest", "authority_snapshot_digest"):
            self.assertIn(key, state)
            self.assertNotIn(key, result)

    def test_legacy_full_schema_checkpoint_resumes_without_reinitialization(self):
        request = _request("DIR-LEGACY-BOUNDARY")
        saver = InMemorySaver()
        # Reproduce the previous compiled boundary: one full state schema for
        # input/output. The persisted channel names and values stay v1.
        with patch("director_orchestrator.langgraph_primary.StateGraph",
                   side_effect=lambda schema, **kwargs: StateGraph(schema)):
            legacy = build_primary_graph(_handlers(), checkpointer=saver,
                                         interrupt_after=[PrimaryPhase.CANONICALIZE.value])
        config = primary_checkpoint_config(request.task_id)
        legacy.invoke(request.graph_input(), config)
        before = legacy.get_state(config).values
        graph = build_primary_graph(_handlers(), checkpointer=saver)
        graph.invoke(None, config)
        after = graph.get_state(config).values
        self.assertEqual(after["input_digest"], before["input_digest"])
        self.assertEqual(after["canonical_digest"], before["canonical_digest"])
        self.assertGreater(after["visit_count"], before["visit_count"])
        self.assertEqual(after["visited_nodes"][:2], before["visited_nodes"])
        self.assertEqual(graph.get_state(config).next, (PrimaryPhase.PROVIDER_SUBGRAPH.value,))

    def test_sequential_channels_replace_rather_than_accumulate(self):
        request = _request("DIR-LAST-VALUE")
        graph = build_primary_graph(_handlers(), checkpointer=InMemorySaver(),
                                    interrupt_after=[PrimaryPhase.CANONICALIZE.value])
        config = primary_checkpoint_config(request.task_id)
        graph.invoke(request.graph_input(), config)
        for key in PrimaryGraphState.__annotations__:
            self.assertIsInstance(graph.channels[key], LastValue, key)
        update = {"visit_count": 9, "visited_nodes": ["canonicalize"], "budget_used_units": 3}
        graph.update_state(config, update)
        graph.update_state(config, update)
        state = graph.get_state(config).values
        for key, value in update.items():
            self.assertEqual(state[key], value)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:06 UTC | END]]
