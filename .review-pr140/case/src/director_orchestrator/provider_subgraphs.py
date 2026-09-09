# [[Codex | GPT-5.6 | 2026-08-25 | 00:09 UTC | BEGIN]]
"""Private provider-neutral LangGraph execution subgraphs.

The caller owns packet validation, durable idempotency, and provider adapter
selection.  This module checkpoints only compact identifiers and digests; work
objectives, provider prose, credentials, and result payloads remain outside the
graph state.
"""

from __future__ import annotations

from enum import StrEnum
import re
from typing import Callable, Mapping, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


PROVIDER_SUBGRAPH_SCHEMA_VERSION = "director-provider-subgraph/v1"
MAX_PROVIDER_SUBGRAPH_VISITS = 4


class ProviderSubgraphPhase(StrEnum):
    VALIDATING = "validating"
    EXECUTING = "executing"
    COMPLETED = "completed"


class ProviderSubgraphState(TypedDict, total=False):
    schema_version: str
    task_id: str
    assignment_id: str
    idempotency_key_digest: str
    work_packet_digest: str
    authority_snapshot_digest: str
    provider: str
    execution_mode: str
    writer_token_digest: str | None
    phase: str
    result_packet_digest: str | None
    result_status: str | None
    run_reference_digest: str | None
    visited_nodes: list[str]


CompactExecutionEvidence = Mapping[str, str | None]
ExecutePacketHook = Callable[[], CompactExecutionEvidence]


def _visit(state: ProviderSubgraphState, node_name: str) -> list[str]:
    visits = [*state.get("visited_nodes", []), node_name]
    if len(visits) > MAX_PROVIDER_SUBGRAPH_VISITS:
        raise ValueError("provider subgraph exceeded its bounded node count")
    return visits


def _validate_compact_state(state: ProviderSubgraphState) -> ProviderSubgraphState:
    required_text = (
        "task_id",
        "assignment_id",
        "idempotency_key_digest",
        "work_packet_digest",
        "authority_snapshot_digest",
        "provider",
        "execution_mode",
    )
    if state.get("schema_version") != PROVIDER_SUBGRAPH_SCHEMA_VERSION:
        raise ValueError("unsupported provider subgraph schema version")
    for field in required_text:
        value = state.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"provider subgraph {field} must be non-empty text")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", str(state["idempotency_key_digest"])) is None:
        raise ValueError("provider subgraph idempotency key must be represented by a digest")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", str(state["work_packet_digest"])) is None:
        raise ValueError("provider subgraph work packet must be represented by a digest")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", str(state["authority_snapshot_digest"])) is None:
        raise ValueError("provider subgraph authority snapshot must be represented by a digest")
    if state.get("execution_mode") == "live_safe" and not state.get(
        "writer_token_digest"
    ):
        raise ValueError("live provider execution requires a writer ownership digest")
    return {
        "phase": ProviderSubgraphPhase.VALIDATING.value,
        "visited_nodes": _visit(state, "validate_packet_binding"),
    }


def _execute_node(execute_packet: ExecutePacketHook):
    def execute(state: ProviderSubgraphState) -> ProviderSubgraphState:
        evidence = execute_packet()
        required = ("result_packet_digest", "result_status", "run_reference_digest")
        if any(not isinstance(evidence.get(field), str) for field in required):
            raise ValueError("provider execution hook returned incomplete compact evidence")
        return {
            "phase": ProviderSubgraphPhase.EXECUTING.value,
            "result_packet_digest": str(evidence["result_packet_digest"]),
            "result_status": str(evidence["result_status"]),
            "run_reference_digest": str(evidence["run_reference_digest"]),
            "visited_nodes": _visit(state, "execute_provider_packet"),
        }

    return execute


def _complete(state: ProviderSubgraphState) -> ProviderSubgraphState:
    if not state.get("result_packet_digest") or not state.get("result_status"):
        raise ValueError("provider subgraph cannot complete without result evidence")
    return {
        "phase": ProviderSubgraphPhase.COMPLETED.value,
        "visited_nodes": _visit(state, "complete_provider_packet"),
    }


def build_provider_subgraph(
    execute_packet: ExecutePacketHook,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    name: str = "provider-neutral-private-subgraph",
) -> CompiledStateGraph:
    """Compile one bounded private subgraph around a caller-owned adapter hook."""

    graph = StateGraph(ProviderSubgraphState)
    graph.add_node("validate_packet_binding", _validate_compact_state)
    graph.add_node("execute_provider_packet", _execute_node(execute_packet))
    graph.add_node("complete_provider_packet", _complete)
    graph.add_edge(START, "validate_packet_binding")
    graph.add_edge("validate_packet_binding", "execute_provider_packet")
    graph.add_edge("execute_provider_packet", "complete_provider_packet")
    graph.add_edge("complete_provider_packet", END)
    return graph.compile(checkpointer=checkpointer, name=name)


# [[Codex | GPT-5.6 | 2026-08-25 | 00:09 UTC | END]]
