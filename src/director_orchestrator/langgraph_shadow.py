# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-22 16:10 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:12 UTC | CHANGE]]
"""Provider-neutral LangGraph shadow of the legacy task-status lifecycle.

This module deliberately has no provider adapter, store, Slack, GitHub, or other
side-effect dependency.  It observes a legacy status, walks an explicit graph,
and emits comparison evidence.  The legacy engine remains the sole authority.
"""

from __future__ import annotations

import operator
import re
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Mapping, Sequence, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from director_orchestrator.models import TaskStatus


SHADOW_SCHEMA_VERSION = "director-shadow/v1"
PLAN_SHAPE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
POLICY_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
PolicyErrorCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$",
    ),
]


class ShadowTerminalClassification(StrEnum):
    """How the legacy engine treats an observed task status at its boundary."""

    NON_TERMINAL = "non_terminal"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    OWNER_DECISION_REQUIRED = "owner_decision_required"


STATUS_NODE_NAMES: Mapping[TaskStatus, str] = MappingProxyType(
    {
        TaskStatus.QUEUED: "status_queued",
        TaskStatus.PLANNING: "status_planning",
        TaskStatus.RUNNING: "status_running",
        TaskStatus.REVIEWING: "status_reviewing",
        TaskStatus.DONE: "status_done",
        TaskStatus.BLOCKED: "status_blocked",
        TaskStatus.FAILED: "status_failed",
        TaskStatus.OWNER_DECISION_REQUIRED: "status_owner_decision_required",
    }
)


STATUS_CLASSIFICATIONS: Mapping[TaskStatus, ShadowTerminalClassification] = MappingProxyType(
    {
        TaskStatus.QUEUED: ShadowTerminalClassification.NON_TERMINAL,
        TaskStatus.PLANNING: ShadowTerminalClassification.NON_TERMINAL,
        TaskStatus.RUNNING: ShadowTerminalClassification.NON_TERMINAL,
        TaskStatus.REVIEWING: ShadowTerminalClassification.NON_TERMINAL,
        TaskStatus.DONE: ShadowTerminalClassification.SUCCEEDED,
        TaskStatus.BLOCKED: ShadowTerminalClassification.BLOCKED,
        TaskStatus.FAILED: ShadowTerminalClassification.FAILED,
        TaskStatus.OWNER_DECISION_REQUIRED: ShadowTerminalClassification.OWNER_DECISION_REQUIRED,
    }
)


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.DONE,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
        TaskStatus.OWNER_DECISION_REQUIRED,
    }
)


class ShadowGraphState(TypedDict, total=False):
    """Small, checkpoint-safe state containing no prompts or provider payloads."""

    schema_version: str
    task_id: str
    legacy_status: str
    previous_status: str | None
    transition_reason: str
    plan_shape_digest: str | None
    policy_errors: list[str]
    plan_approval_required: bool
    owner_approved: bool
    pending_assignment_sequence: int | None
    pending_assignment_requires_approval: bool
    pending_assignment_approved: bool
    policy_blocked: bool
    plan_gate_pending: bool
    assignment_gate_pending: bool
    legacy_approval_required: bool
    shadow_approval_required: bool
    approvals_agree: bool
    shadow_status: str
    legacy_classification: str
    shadow_classification: str
    legacy_is_terminal: bool
    shadow_is_terminal: bool
    statuses_agree: bool
    classifications_agree: bool
    differences: tuple[str, ...]
    visited_nodes: Annotated[list[str], operator.add]


class ShadowTaskSnapshot(BaseModel):
    """Validated legacy observation used as graph input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=255)
    status: TaskStatus
    previous_status: TaskStatus | None = None
    transition_reason: str = Field(default="legacy status observation", min_length=1, max_length=512)
    plan_shape_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy_errors: tuple[PolicyErrorCode, ...] = Field(default_factory=tuple, max_length=20)
    plan_approval_required: bool = False
    owner_approved: bool = False
    pending_assignment_sequence: int | None = Field(default=None, ge=1, le=20)
    pending_assignment_requires_approval: bool = False
    pending_assignment_approved: bool = False

    @model_validator(mode="after")
    def validate_compact_projection(self) -> "ShadowTaskSnapshot":
        if len(set(self.policy_errors)) != len(self.policy_errors):
            raise ValueError("policy_errors must not contain duplicate codes")
        if self.pending_assignment_sequence is None and (
            self.pending_assignment_requires_approval or self.pending_assignment_approved
        ):
            raise ValueError("assignment approval fields require pending_assignment_sequence")
        if self.pending_assignment_approved and not self.pending_assignment_requires_approval:
            raise ValueError("pending_assignment_approved requires an assignment approval gate")
        return self

    def graph_input(self) -> ShadowGraphState:
        return {
            "schema_version": SHADOW_SCHEMA_VERSION,
            "task_id": self.task_id,
            "legacy_status": self.status.value,
            "previous_status": self.previous_status.value if self.previous_status is not None else None,
            "transition_reason": self.transition_reason,
            "plan_shape_digest": self.plan_shape_digest,
            "policy_errors": list(self.policy_errors),
            "plan_approval_required": self.plan_approval_required,
            "owner_approved": self.owner_approved,
            "pending_assignment_sequence": self.pending_assignment_sequence,
            "pending_assignment_requires_approval": self.pending_assignment_requires_approval,
            "pending_assignment_approved": self.pending_assignment_approved,
            "visited_nodes": [],
        }


class ShadowComparisonReport(BaseModel):
    """Read-only evidence comparing the graph observation with legacy state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    task_id: str
    legacy_status: TaskStatus
    shadow_status: TaskStatus
    previous_status: TaskStatus | None
    transition_reason: str
    plan_shape_digest: str | None
    policy_errors: tuple[str, ...]
    plan_approval_required: bool
    owner_approved: bool
    pending_assignment_sequence: int | None
    pending_assignment_requires_approval: bool
    pending_assignment_approved: bool
    policy_blocked: bool
    plan_gate_pending: bool
    assignment_gate_pending: bool
    legacy_approval_required: bool
    shadow_approval_required: bool
    approvals_agree: bool
    legacy_classification: ShadowTerminalClassification
    shadow_classification: ShadowTerminalClassification
    legacy_is_terminal: bool
    shadow_is_terminal: bool
    statuses_agree: bool
    classifications_agree: bool
    differences: tuple[str, ...]
    visited_nodes: tuple[str, ...]


def classify_status(status: TaskStatus) -> ShadowTerminalClassification:
    """Return the explicit legacy-compatible classification for every status."""

    return STATUS_CLASSIFICATIONS[status]


def _validate_observation(state: ShadowGraphState) -> ShadowGraphState:
    if state.get("schema_version") != SHADOW_SCHEMA_VERSION:
        raise ValueError(f"unsupported shadow schema version: {state.get('schema_version')!r}")

    task_id = state.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 255:
        raise ValueError("task_id must be a non-empty string of at most 255 characters")

    try:
        TaskStatus(state.get("legacy_status"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown legacy TaskStatus: {state.get('legacy_status')!r}") from exc

    previous_status = state.get("previous_status")
    if previous_status is not None:
        try:
            TaskStatus(previous_status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown previous TaskStatus: {previous_status!r}") from exc

    reason = state.get("transition_reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
        raise ValueError("transition_reason must be a non-empty string of at most 512 characters")

    plan_shape_digest = state.get("plan_shape_digest")
    if plan_shape_digest is not None and (
        not isinstance(plan_shape_digest, str) or PLAN_SHAPE_DIGEST_PATTERN.fullmatch(plan_shape_digest) is None
    ):
        raise ValueError("plan_shape_digest must be a lowercase 64-character SHA-256 digest")

    policy_errors = state.get("policy_errors")
    if not isinstance(policy_errors, (list, tuple)) or len(policy_errors) > 20:
        raise ValueError("policy_errors must be a sequence of at most 20 compact codes")
    if any(
        not isinstance(code, str) or POLICY_ERROR_CODE_PATTERN.fullmatch(code) is None
        for code in policy_errors
    ):
        raise ValueError("policy_errors must contain compact lowercase codes")
    if len(set(policy_errors)) != len(policy_errors):
        raise ValueError("policy_errors must not contain duplicate codes")

    boolean_fields = (
        "plan_approval_required",
        "owner_approved",
        "pending_assignment_requires_approval",
        "pending_assignment_approved",
    )
    if any(type(state.get(field)) is not bool for field in boolean_fields):
        raise ValueError("approval projection fields must be booleans")

    sequence = state.get("pending_assignment_sequence")
    if sequence is not None and (type(sequence) is not int or not 1 <= sequence <= 20):
        raise ValueError("pending_assignment_sequence must be null or an integer from 1 through 20")
    if sequence is None and (
        state["pending_assignment_requires_approval"] or state["pending_assignment_approved"]
    ):
        raise ValueError("assignment approval fields require pending_assignment_sequence")
    if state["pending_assignment_approved"] and not state["pending_assignment_requires_approval"]:
        raise ValueError("pending_assignment_approved requires an assignment approval gate")

    return {"visited_nodes": ["observe_legacy_status"]}


def _project_policy(state: ShadowGraphState) -> ShadowGraphState:
    policy_blocked = bool(state["policy_errors"])
    projected: ShadowGraphState = {
        "policy_blocked": policy_blocked,
        "visited_nodes": ["policy_projection"],
    }
    if policy_blocked:
        projected["shadow_status"] = TaskStatus.BLOCKED.value
    return projected


def _project_approval(state: ShadowGraphState) -> ShadowGraphState:
    plan_gate_pending = state["plan_approval_required"] and not state["owner_approved"]
    assignment_gate_pending = (
        state["pending_assignment_sequence"] is not None
        and state["pending_assignment_requires_approval"]
        and not state["pending_assignment_approved"]
    )
    shadow_approval_required = not state["policy_blocked"] and (
        plan_gate_pending or assignment_gate_pending
    )
    if state["policy_blocked"]:
        shadow_status = TaskStatus.BLOCKED
    elif shadow_approval_required:
        shadow_status = TaskStatus.OWNER_DECISION_REQUIRED
    else:
        shadow_status = TaskStatus(state["legacy_status"])

    return {
        "plan_gate_pending": plan_gate_pending,
        "assignment_gate_pending": assignment_gate_pending,
        "shadow_approval_required": shadow_approval_required,
        "shadow_status": shadow_status.value,
        "visited_nodes": ["approval_projection"],
    }


def _route_observed_status(state: ShadowGraphState) -> str:
    return STATUS_NODE_NAMES[TaskStatus(state["shadow_status"])]


def _status_node(status: TaskStatus):
    node_name = STATUS_NODE_NAMES[status]

    def observe_status(_state: ShadowGraphState) -> ShadowGraphState:
        return {
            "shadow_status": status.value,
            "shadow_classification": classify_status(status).value,
            "shadow_is_terminal": status in TERMINAL_STATUSES,
            "visited_nodes": [node_name],
        }

    observe_status.__name__ = node_name
    return observe_status


def _compare_with_legacy(state: ShadowGraphState) -> ShadowGraphState:
    legacy_status = TaskStatus(state["legacy_status"])
    shadow_status = TaskStatus(state["shadow_status"])
    legacy_classification = classify_status(legacy_status)
    shadow_classification = classify_status(shadow_status)

    differences: list[str] = []
    if legacy_status != shadow_status:
        differences.append(
            f"status mismatch: legacy={legacy_status.value}, shadow={shadow_status.value}"
        )
    if legacy_classification != shadow_classification:
        differences.append(
            "classification mismatch: "
            f"legacy={legacy_classification.value}, shadow={shadow_classification.value}"
        )

    legacy_is_terminal = legacy_status in TERMINAL_STATUSES
    shadow_is_terminal = shadow_status in TERMINAL_STATUSES
    if legacy_is_terminal != shadow_is_terminal:
        differences.append(
            f"terminal mismatch: legacy={legacy_is_terminal}, shadow={shadow_is_terminal}"
        )

    # OWNER_DECISION_REQUIRED is a general stop classification.  Clarification,
    # provider unavailability, and other owner decisions are not approvals.  The
    # approval comparison therefore derives only from explicit legacy gate facts,
    # after the same policy-block precedence used by the shadow projection.
    legacy_approval_required = not state["policy_blocked"] and (
        state["plan_gate_pending"] or state["assignment_gate_pending"]
    )
    shadow_approval_required = state["shadow_approval_required"]
    if legacy_approval_required != shadow_approval_required:
        differences.append(
            "approval mismatch: "
            f"legacy={legacy_approval_required}, shadow={shadow_approval_required}"
        )

    return {
        "legacy_classification": legacy_classification.value,
        "shadow_classification": shadow_classification.value,
        "legacy_is_terminal": legacy_is_terminal,
        "shadow_is_terminal": shadow_is_terminal,
        "legacy_approval_required": legacy_approval_required,
        "approvals_agree": legacy_approval_required == shadow_approval_required,
        "statuses_agree": legacy_status == shadow_status,
        "classifications_agree": legacy_classification == shadow_classification,
        "differences": tuple(differences),
        "visited_nodes": ["compare_with_legacy"],
    }


def build_shadow_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    interrupt_after: Sequence[str] | None = None,
) -> CompiledStateGraph:
    """Build the side-effect-free graph with a caller-owned checkpoint saver."""

    graph = StateGraph(ShadowGraphState)
    graph.add_node("observe_legacy_status", _validate_observation)
    graph.add_node("policy_projection", _project_policy)
    graph.add_node("approval_projection", _project_approval)
    for status, node_name in STATUS_NODE_NAMES.items():
        graph.add_node(node_name, _status_node(status))
    graph.add_node("compare_with_legacy", _compare_with_legacy)

    graph.add_edge(START, "observe_legacy_status")
    graph.add_edge("observe_legacy_status", "policy_projection")
    graph.add_edge("policy_projection", "approval_projection")
    graph.add_conditional_edges(
        "approval_projection",
        _route_observed_status,
        {node_name: node_name for node_name in STATUS_NODE_NAMES.values()},
    )
    for node_name in STATUS_NODE_NAMES.values():
        graph.add_edge(node_name, "compare_with_legacy")
    graph.add_edge("compare_with_legacy", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_after=list(interrupt_after) if interrupt_after is not None else None,
        name="director_langgraph_shadow_v1",
    )


def checkpoint_config(task_id: str, *, namespace: str = "director-shadow-v1") -> dict[str, Any]:
    """Return an isolated LangGraph thread configuration for one legacy task."""

    if not task_id.strip():
        raise ValueError("task_id must not be empty")
    if not namespace.strip():
        raise ValueError("namespace must not be empty")
    # LangGraph reserves ``checkpoint_ns`` for subgraph internals and normalizes
    # it at the root graph.  Prefixing the caller-visible thread ID creates the
    # stable, separate shadow namespace without relying on that internal field.
    return {"configurable": {"thread_id": f"{namespace}:{task_id}"}}


def comparison_report(state: Mapping[str, Any]) -> ShadowComparisonReport:
    """Validate and materialize a report from a completed graph state."""

    required = {
        "schema_version",
        "task_id",
        "legacy_status",
        "shadow_status",
        "transition_reason",
        "plan_shape_digest",
        "policy_errors",
        "plan_approval_required",
        "owner_approved",
        "pending_assignment_sequence",
        "pending_assignment_requires_approval",
        "pending_assignment_approved",
        "policy_blocked",
        "plan_gate_pending",
        "assignment_gate_pending",
        "legacy_approval_required",
        "shadow_approval_required",
        "approvals_agree",
        "legacy_classification",
        "shadow_classification",
        "legacy_is_terminal",
        "shadow_is_terminal",
        "statuses_agree",
        "classifications_agree",
        "differences",
        "visited_nodes",
    }
    missing = sorted(required.difference(state))
    if missing:
        raise ValueError(f"shadow comparison is incomplete; missing: {', '.join(missing)}")

    return ShadowComparisonReport(
        schema_version=state["schema_version"],
        task_id=state["task_id"],
        legacy_status=state["legacy_status"],
        shadow_status=state["shadow_status"],
        previous_status=state.get("previous_status"),
        transition_reason=state["transition_reason"],
        plan_shape_digest=state["plan_shape_digest"],
        policy_errors=tuple(state["policy_errors"]),
        plan_approval_required=state["plan_approval_required"],
        owner_approved=state["owner_approved"],
        pending_assignment_sequence=state["pending_assignment_sequence"],
        pending_assignment_requires_approval=state["pending_assignment_requires_approval"],
        pending_assignment_approved=state["pending_assignment_approved"],
        policy_blocked=state["policy_blocked"],
        plan_gate_pending=state["plan_gate_pending"],
        assignment_gate_pending=state["assignment_gate_pending"],
        legacy_approval_required=state["legacy_approval_required"],
        shadow_approval_required=state["shadow_approval_required"],
        approvals_agree=state["approvals_agree"],
        legacy_classification=state["legacy_classification"],
        shadow_classification=state["shadow_classification"],
        legacy_is_terminal=state["legacy_is_terminal"],
        shadow_is_terminal=state["shadow_is_terminal"],
        statuses_agree=state["statuses_agree"],
        classifications_agree=state["classifications_agree"],
        differences=tuple(state["differences"]),
        visited_nodes=tuple(state["visited_nodes"]),
    )


def run_shadow_comparison(
    snapshot: ShadowTaskSnapshot,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    config: dict[str, Any] | None = None,
) -> ShadowComparisonReport:
    """Run one comparison without mutating legacy state or calling a provider."""

    graph = build_shadow_graph(checkpointer=checkpointer)
    effective_config = config
    if checkpointer is not None and effective_config is None:
        effective_config = checkpoint_config(snapshot.task_id)
    state = graph.invoke(snapshot.graph_input(), config=effective_config)
    return comparison_report(state)


# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]
