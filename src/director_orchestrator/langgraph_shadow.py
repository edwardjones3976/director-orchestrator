# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-22 16:10 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:12 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:27 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:42 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 16:51 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 23:12 UTC | CHANGE]]
# [[Codex | GPT-5.6 | 2026-08-22 23:19 UTC | CHANGE]]
"""Provider-neutral LangGraph shadow of the legacy task-status lifecycle.

This module deliberately has no provider adapter, store, Slack, GitHub, or other
side-effect dependency.  It observes a legacy status, walks an explicit graph,
and emits comparison evidence.  The legacy engine remains the sole authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Mapping, Sequence, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from director_orchestrator.approvals import (
    assignment_is_approved,
    current_owner_decision,
    pending_assignment,
    plan_gate_pending,
)
from director_orchestrator.config import Settings
from director_orchestrator.models import DirectorPlan, ProviderName, TaskRecord, TaskStatus
from director_orchestrator.policy import execution_policy_errors


SHADOW_SCHEMA_VERSION = "director-shadow/v1"
PLAN_SHAPE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
POLICY_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
MAX_VISITED_NODES = 8
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


class ShadowReasonCode(StrEnum):
    """Bounded transition evidence; arbitrary messages never enter checkpoints."""

    LEGACY_OBSERVATION = "legacy_observation"
    DRY_RUN = "dry_run"
    POLICY_BLOCK = "policy_block"
    PLAN_APPROVAL_GATE = "plan_approval_gate"
    ASSIGNMENT_APPROVAL_GATE = "assignment_approval_gate"
    OWNER_CLARIFICATION = "owner_clarification"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TERMINAL_STATUS = "terminal_status"
    RECOVERY = "recovery"


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
    transition_reason_code: str
    plan_shape_digest: str | None
    policy_errors: list[str]
    policy_error_count: int
    policy_error_digest: str | None
    plan_approval_required: bool
    owner_approved: bool
    pending_assignment_sequence: int | None
    pending_assignment_requires_approval: bool
    pending_assignment_approved: bool
    owner_clarification_pending: bool
    observed_legacy_approval_required: bool
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
    visited_nodes: list[str]


class ShadowTaskSnapshot(BaseModel):
    """Validated legacy observation used as graph input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=255)
    status: TaskStatus
    previous_status: TaskStatus | None = None
    transition_reason_code: ShadowReasonCode = ShadowReasonCode.LEGACY_OBSERVATION
    plan_shape_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy_errors: tuple[PolicyErrorCode, ...] = Field(default_factory=tuple, max_length=20)
    policy_blocked: bool = False
    policy_error_count: int = Field(default=0, ge=0, le=10_000)
    policy_error_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plan_approval_required: bool = False
    owner_approved: bool = False
    pending_assignment_sequence: int | None = Field(default=None, ge=1, le=20)
    pending_assignment_requires_approval: bool = False
    pending_assignment_approved: bool = False
    owner_clarification_pending: bool = False
    observed_legacy_approval_required: bool = False

    @model_validator(mode="after")
    def validate_compact_projection(self) -> "ShadowTaskSnapshot":
        if len(set(self.policy_errors)) != len(self.policy_errors):
            raise ValueError("policy_errors must not contain duplicate codes")
        if self.policy_error_count < len(self.policy_errors):
            raise ValueError("policy_error_count cannot be smaller than retained policy codes")
        if self.policy_blocked != (self.policy_error_count > 0):
            raise ValueError("policy_blocked must match policy_error_count")
        if (self.policy_error_count > 0) != (self.policy_error_digest is not None):
            raise ValueError("policy_error_digest is required exactly when policy errors exist")
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
            "transition_reason_code": self.transition_reason_code.value,
            "plan_shape_digest": self.plan_shape_digest,
            "policy_errors": list(self.policy_errors),
            "policy_blocked": self.policy_blocked,
            "policy_error_count": self.policy_error_count,
            "policy_error_digest": self.policy_error_digest,
            "plan_approval_required": self.plan_approval_required,
            "owner_approved": self.owner_approved,
            "pending_assignment_sequence": self.pending_assignment_sequence,
            "pending_assignment_requires_approval": self.pending_assignment_requires_approval,
            "pending_assignment_approved": self.pending_assignment_approved,
            "owner_clarification_pending": self.owner_clarification_pending,
            "observed_legacy_approval_required": self.observed_legacy_approval_required,
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
    transition_reason_code: ShadowReasonCode
    plan_shape_digest: str | None
    policy_errors: tuple[str, ...]
    policy_error_count: int
    policy_error_digest: str | None
    plan_approval_required: bool
    owner_approved: bool
    pending_assignment_sequence: int | None
    pending_assignment_requires_approval: bool
    pending_assignment_approved: bool
    owner_clarification_pending: bool
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


def plan_shape_digest(plan: DirectorPlan | None) -> str | None:
    """Digest only structural plan facts, excluding all model-authored prose."""

    if plan is None:
        return None
    debate = plan.debate
    shape = {
        "risk_level": plan.risk_level.value,
        "approval_required": plan.approval_required,
        "approval_reason_count": len(plan.approval_reasons),
        "max_hops": plan.max_hops,
        "max_revisions": plan.max_revisions,
        "assignments": [
            {
                "sequence": assignment.sequence,
                "agent": assignment.agent.value,
                "kind": assignment.kind.value,
                "depends_on": sorted(assignment.depends_on),
                "requires_approval": assignment.requires_approval,
                "allow_draft_pr": assignment.allow_draft_pr,
                "discussion_phase": assignment.discussion_phase.value,
                "has_repository": assignment.repository is not None,
                "has_public_research_query": assignment.public_research_query is not None,
                "has_antigravity_directive": assignment.antigravity_directive is not None,
                "acceptance_count": len(assignment.acceptance),
            }
            for assignment in sorted(plan.assignments, key=lambda item: item.sequence)
        ],
        "final_acceptance_count": len(plan.final_acceptance),
        "owner_question_present": plan.owner_question is not None,
        "debate": (
            None
            if debate is None
            else {
                "position_assignments": sorted(debate.position_assignments),
                "rebuttal_assignments": sorted(debate.rebuttal_assignments),
                "vote_option_count": len(debate.vote_options),
                "deadline_minutes": debate.deadline_minutes,
                "max_rebuttal_rounds": debate.max_rebuttal_rounds,
                "material_risk": debate.material_risk,
            }
        ),
    }
    canonical = json.dumps(shape, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _policy_error_code(error: str) -> str:
    """Replace potentially identifying policy prose with a stable opaque code."""

    return "policy:" + hashlib.sha256(error.encode("utf-8")).hexdigest()[:32]


def _policy_error_digest(codes: Sequence[str]) -> str | None:
    if not codes:
        return None
    canonical = json.dumps(sorted(codes), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reason_code_from_record(
    record: TaskRecord,
    *,
    previous_status: TaskStatus | None,
    dry_run: bool,
    policy_blocked: bool,
    plan_gate_is_pending: bool,
    owner_clarification_pending: bool,
    assignment_gate_is_pending: bool,
) -> ShadowReasonCode:
    if dry_run:
        return ShadowReasonCode.DRY_RUN
    if policy_blocked:
        return ShadowReasonCode.POLICY_BLOCK
    if plan_gate_is_pending:
        return ShadowReasonCode.PLAN_APPROVAL_GATE
    if owner_clarification_pending:
        return ShadowReasonCode.OWNER_CLARIFICATION
    if assignment_gate_is_pending:
        return ShadowReasonCode.ASSIGNMENT_APPROVAL_GATE
    if record.unavailable_providers:
        return ShadowReasonCode.PROVIDER_UNAVAILABLE
    if any(
        attempt.outcome in {"reconciled_not_created", "reconciled_created"}
        for attempt in record.provider_contact_attempts
    ):
        return ShadowReasonCode.RECOVERY
    if previous_status in {TaskStatus.FAILED, TaskStatus.BLOCKED} and record.status == TaskStatus.QUEUED:
        return ShadowReasonCode.RECOVERY
    if record.status in {TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.FAILED}:
        return ShadowReasonCode.TERMINAL_STATUS
    return ShadowReasonCode.LEGACY_OBSERVATION


def snapshot_from_task_record(
    record: TaskRecord,
    settings: Settings,
    *,
    previous_status: TaskStatus | None = None,
) -> ShadowTaskSnapshot:
    """Project actual deterministic legacy structures into compact shadow input.

    No intake text, plan prose, provider result, message, error, or transcript is
    copied.  Policy messages are represented only by opaque deterministic codes.
    """

    plan = record.plan
    dry_run = not settings.allow_paid_runs and plan is not None
    raw_policy_errors = (
        execution_policy_errors(plan, settings)
        if plan is not None and not dry_run
        else []
    )
    all_policy_error_codes = tuple(sorted(_policy_error_code(error) for error in raw_policy_errors))
    compact_policy_errors = all_policy_error_codes[:20]
    pending = pending_assignment(record)
    owner_question_pending = bool(plan is not None and plan.owner_question is not None)
    pending_owner_assignment = bool(pending is not None and pending.agent == ProviderName.OWNER)
    planless_owner_stop = record.status == TaskStatus.OWNER_DECISION_REQUIRED and plan is None
    effective_plan_approval_required = bool(
        not dry_run
        and not raw_policy_errors
        and plan
        and plan.approval_required
        and not owner_question_pending
    )
    plan_gate_is_pending = effective_plan_approval_required and plan_gate_pending(record)
    raw_owner_clarification = bool(
        owner_question_pending or pending_owner_assignment or planless_owner_stop
    )
    owner_clarification_pending = bool(
        not dry_run
        and not raw_policy_errors
        and not plan_gate_is_pending
        and raw_owner_clarification
    )
    effective_assignment_requires_approval = bool(
        not dry_run
        and not raw_policy_errors
        and not owner_clarification_pending
        and not plan_gate_is_pending
        and pending
        and pending.requires_approval
    )
    assignment_gate_is_pending = bool(
        effective_assignment_requires_approval
        and not assignment_is_approved(record, pending)
    )
    legacy_decision = current_owner_decision(record)
    observed_legacy_approval_required = bool(
        not dry_run
        and not raw_policy_errors
        and legacy_decision is not None
        and legacy_decision.kind in {"plan_approval", "assignment_approval"}
    )
    return ShadowTaskSnapshot(
        task_id=record.task_id,
        status=record.status,
        previous_status=previous_status,
        transition_reason_code=_reason_code_from_record(
            record,
            previous_status=previous_status,
            dry_run=dry_run,
            policy_blocked=bool(raw_policy_errors),
            plan_gate_is_pending=plan_gate_is_pending,
            owner_clarification_pending=owner_clarification_pending,
            assignment_gate_is_pending=assignment_gate_is_pending,
        ),
        plan_shape_digest=plan_shape_digest(plan),
        policy_errors=compact_policy_errors,
        policy_blocked=bool(raw_policy_errors),
        policy_error_count=len(raw_policy_errors),
        policy_error_digest=_policy_error_digest(all_policy_error_codes),
        plan_approval_required=effective_plan_approval_required,
        owner_approved=record.owner_approved,
        pending_assignment_sequence=pending.sequence if pending is not None else None,
        pending_assignment_requires_approval=effective_assignment_requires_approval,
        pending_assignment_approved=bool(
            effective_assignment_requires_approval
            and assignment_is_approved(record, pending)
        ),
        owner_clarification_pending=owner_clarification_pending,
        observed_legacy_approval_required=observed_legacy_approval_required,
    )


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

    try:
        ShadowReasonCode(state.get("transition_reason_code"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"unknown transition reason code: {state.get('transition_reason_code')!r}"
        ) from exc

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
    policy_blocked = state.get("policy_blocked")
    if type(policy_blocked) is not bool:
        raise ValueError("policy_blocked must be a boolean")
    policy_error_count = state.get("policy_error_count")
    if (
        type(policy_error_count) is not int
        or not 0 <= policy_error_count <= 10_000
        or policy_error_count < len(policy_errors)
    ):
        raise ValueError("policy_error_count must cover all retained policy codes")
    policy_error_digest = state.get("policy_error_digest")
    if policy_error_digest is not None and (
        not isinstance(policy_error_digest, str)
        or PLAN_SHAPE_DIGEST_PATTERN.fullmatch(policy_error_digest) is None
    ):
        raise ValueError("policy_error_digest must be a lowercase SHA-256 digest")
    if policy_blocked != (policy_error_count > 0):
        raise ValueError("policy_blocked must match policy_error_count")
    if (policy_error_count > 0) != (policy_error_digest is not None):
        raise ValueError("policy_error_digest is required exactly when policy errors exist")

    boolean_fields = (
        "plan_approval_required",
        "owner_approved",
        "pending_assignment_requires_approval",
        "pending_assignment_approved",
        "owner_clarification_pending",
        "observed_legacy_approval_required",
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


def _visit(state: ShadowGraphState, node_name: str) -> list[str]:
    """Append one compact trace step without accumulating across new invocations."""

    prior = state.get("visited_nodes", [])
    if not isinstance(prior, list) or any(not isinstance(item, str) for item in prior):
        raise ValueError("visited_nodes must contain compact node names")
    return [*prior, node_name][-MAX_VISITED_NODES:]


def _project_policy(state: ShadowGraphState) -> ShadowGraphState:
    policy_blocked = state["policy_blocked"]
    projected: ShadowGraphState = {
        "policy_blocked": policy_blocked,
        "visited_nodes": _visit(state, "policy_projection"),
    }
    if policy_blocked:
        projected["shadow_status"] = TaskStatus.BLOCKED.value
    return projected


def _project_approval(state: ShadowGraphState) -> ShadowGraphState:
    plan_gate_pending = (
        not state["policy_blocked"]
        and state["plan_approval_required"]
        and not state["owner_approved"]
    )
    assignment_gate_pending = (
        not state["policy_blocked"]
        and not plan_gate_pending
        and not state["owner_clarification_pending"]
        and state["pending_assignment_sequence"] is not None
        and state["pending_assignment_requires_approval"]
        and not state["pending_assignment_approved"]
    )
    shadow_approval_required = plan_gate_pending or assignment_gate_pending
    if state["policy_blocked"]:
        shadow_status = TaskStatus.BLOCKED
    elif shadow_approval_required or state["owner_clarification_pending"]:
        shadow_status = TaskStatus.OWNER_DECISION_REQUIRED
    else:
        shadow_status = TaskStatus(state["legacy_status"])

    return {
        "plan_gate_pending": plan_gate_pending,
        "assignment_gate_pending": assignment_gate_pending,
        "shadow_approval_required": shadow_approval_required,
        "shadow_status": shadow_status.value,
        "visited_nodes": _visit(state, "approval_projection"),
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
            "visited_nodes": _visit(_state, node_name),
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

    # This fact is projected independently by current_owner_decision from the
    # legacy TaskRecord.  It must not be reconstructed from the shadow graph's
    # own plan/assignment gate outputs or approval agreement becomes tautological.
    legacy_approval_required = state["observed_legacy_approval_required"]
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
        "visited_nodes": _visit(state, "compare_with_legacy"),
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
        "transition_reason_code",
        "plan_shape_digest",
        "policy_errors",
        "policy_error_count",
        "policy_error_digest",
        "plan_approval_required",
        "owner_approved",
        "pending_assignment_sequence",
        "pending_assignment_requires_approval",
        "pending_assignment_approved",
        "owner_clarification_pending",
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
        transition_reason_code=state["transition_reason_code"],
        plan_shape_digest=state["plan_shape_digest"],
        policy_errors=tuple(state["policy_errors"]),
        policy_error_count=state["policy_error_count"],
        policy_error_digest=state["policy_error_digest"],
        plan_approval_required=state["plan_approval_required"],
        owner_approved=state["owner_approved"],
        pending_assignment_sequence=state["pending_assignment_sequence"],
        pending_assignment_requires_approval=state["pending_assignment_requires_approval"],
        pending_assignment_approved=state["pending_assignment_approved"],
        owner_clarification_pending=state["owner_clarification_pending"],
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
