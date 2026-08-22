# [[Codex | GPT-5.6 | 2026-08-22 16:07 UTC | BEGIN]]
"""Authority-neutral LangGraph pause and resume proof.

The graph in this module is deliberately incapable of authorizing work.  It
only persists an interrupt position and a digest of evidence that the legacy
Director has already validated.  Callers must present the current
``TaskRecord`` on every resume so the legacy approval/callback state is checked
again immediately before LangGraph receives ``Command(resume=...)``.

No legacy task fields are mutated here and no provider or external-write
adapter is imported.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .approvals import assignment_approval_digest, assignment_is_approved
from .models import Assignment, ProviderName, ProviderRun, TaskRecord


GRAPH_VERSION = "director-shadow-resume.v1"
DEFAULT_CHECKPOINT_NAMESPACE = "director-shadow-resume"


class ResumeKind(StrEnum):
    ASSIGNMENT_APPROVAL = "assignment_approval"
    PROVIDER_CALLBACK = "provider_callback"


class ResumeStatus(StrEnum):
    PAUSED = "paused"
    RESUMED = "resumed"
    ALREADY_RESUMED = "already_resumed"
    REJECTED = "rejected"
    LEGACY_FALLBACK = "legacy_fallback"


class _ResumeState(TypedDict, total=False):
    handle: dict[str, Any]
    phase: str
    resume_digest: str


class ResumeRejected(ValueError):
    """The current legacy authority does not authorize this exact resume."""


class CheckpointUnavailable(RuntimeError):
    """The graph checkpoint cannot be trusted; legacy execution must retain control."""


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ResumeHandle:
    task_id: str
    kind: ResumeKind
    assignment_sequence: int
    callback_run_id: str | None
    authority_snapshot_digest: str
    record_binding_digest: str
    graph_version: str
    checkpoint_namespace: str
    binding_digest: str
    thread_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "assignment_sequence": self.assignment_sequence,
            "callback_run_id": self.callback_run_id,
            "authority_snapshot_digest": self.authority_snapshot_digest,
            "record_binding_digest": self.record_binding_digest,
            "graph_version": self.graph_version,
            "checkpoint_namespace": self.checkpoint_namespace,
            "binding_digest": self.binding_digest,
            "thread_id": self.thread_id,
        }


@dataclass(frozen=True, slots=True)
class PreparedResume:
    handle: ResumeHandle
    authority_evidence_digest: str
    resume_digest: str


@dataclass(frozen=True, slots=True)
class ResumeOutcome:
    status: ResumeStatus
    reason: str
    handle: ResumeHandle | None = None
    resume_digest: str | None = None


def _find_assignment(record: TaskRecord, sequence: int) -> Assignment:
    if record.plan is None:
        raise ResumeRejected("task has no validated plan")
    matches = [
        assignment
        for assignment in record.plan.assignments
        if assignment.sequence == sequence
    ]
    if len(matches) != 1:
        raise ResumeRejected("resume does not identify exactly one current assignment")
    return matches[0]


def _find_callback_run(
    record: TaskRecord,
    *,
    assignment_sequence: int,
    callback_run_id: str,
) -> ProviderRun:
    matches = [
        run
        for run in record.provider_runs
        if run.provider == ProviderName.CLAUDE
        and run.assignment_sequence == assignment_sequence
        and _secure_equal(run.run_id, callback_run_id)
    ]
    if len(matches) != 1:
        raise ResumeRejected("resume does not identify exactly one current callback run")
    return matches[0]


def _validate_authority_snapshot_digest(value: str) -> str:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ResumeRejected(
            "authority snapshot digest must use strict sha256:<64 lowercase hex> format"
        )
    return value


def _record_binding_digest(
    record: TaskRecord,
    *,
    kind: ResumeKind,
    assignment: Assignment,
    callback_run_id: str | None,
) -> str:
    """Bind current legacy records without confusing this with external authority."""

    payload: dict[str, Any] = {
        "task_id": record.task_id,
        "source_event_id": record.intake.source_event_id,
        "kind": kind.value,
        "assignment_sequence": assignment.sequence,
        "assignment_digest": assignment_approval_digest(assignment),
        # The plan digest makes a changed plan stale while avoiding persistence of
        # the plan text itself in the graph checkpoint.
        "plan_digest": _digest(record.plan.model_dump(mode="json")),
    }
    if kind == ResumeKind.PROVIDER_CALLBACK:
        if not callback_run_id:
            raise ResumeRejected("callback pause requires an exact run ID")
        run = _find_callback_run(
            record,
            assignment_sequence=assignment.sequence,
            callback_run_id=callback_run_id,
        )
        payload["callback"] = {
            "provider": run.provider.value,
            "assignment_sequence": run.assignment_sequence,
            "run_id": run.run_id,
        }
    elif callback_run_id is not None:
        raise ResumeRejected("approval pause cannot be bound to a callback run")
    return _digest(payload)


def _new_handle(
    record: TaskRecord,
    *,
    kind: ResumeKind,
    assignment_sequence: int,
    callback_run_id: str | None,
    authority_snapshot_digest: str,
    graph_version: str,
    checkpoint_namespace: str,
) -> ResumeHandle:
    authority_snapshot_digest = _validate_authority_snapshot_digest(
        authority_snapshot_digest
    )
    assignment = _find_assignment(record, assignment_sequence)
    record_digest = _record_binding_digest(
        record,
        kind=kind,
        assignment=assignment,
        callback_run_id=callback_run_id,
    )
    binding_payload = {
        "task_id": record.task_id,
        "kind": kind.value,
        "assignment_sequence": assignment_sequence,
        "callback_run_id": callback_run_id,
        "authority_snapshot_digest": authority_snapshot_digest,
        "record_binding_digest": record_digest,
        "graph_version": graph_version,
        "checkpoint_namespace": checkpoint_namespace,
    }
    binding_digest = _digest(binding_payload)
    return ResumeHandle(
        task_id=record.task_id,
        kind=kind,
        assignment_sequence=assignment_sequence,
        callback_run_id=callback_run_id,
        authority_snapshot_digest=authority_snapshot_digest,
        record_binding_digest=record_digest,
        graph_version=graph_version,
        checkpoint_namespace=checkpoint_namespace,
        binding_digest=binding_digest,
        thread_id=f"resume-{binding_digest}",
    )


def _pause_node(state: _ResumeState) -> dict[str, str]:
    """Persist a position; deliberately make no authority decision."""

    resume_payload = interrupt(
        {
            "schema_version": "1",
            "type": "legacy_authority_required",
            "handle": state["handle"],
        }
    )
    # The runtime supplies this digest only after validating legacy state.  It
    # is evidence for audit/recovery, not a capability interpreted by the graph.
    return {
        "phase": ResumeStatus.RESUMED.value,
        "resume_digest": str(resume_payload["resume_digest"]),
    }


def _build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(_ResumeState)
    builder.add_node("pause", _pause_node)
    builder.add_edge(START, "pause")
    builder.add_edge("pause", END)
    return builder.compile(checkpointer=checkpointer, name="director-shadow-resume")


class LangGraphResumeRuntime:
    """Bounded, authority-neutral facade around a real LangGraph interrupt."""

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver,
        *,
        checkpoint_namespace: str = DEFAULT_CHECKPOINT_NAMESPACE,
        graph_version: str = GRAPH_VERSION,
    ) -> None:
        if not checkpoint_namespace.strip() or not graph_version.strip():
            raise ValueError("checkpoint namespace and graph version are required")
        self.checkpoint_namespace = checkpoint_namespace
        self.graph_version = graph_version
        self._graph = _build_graph(checkpointer)

    def _config(self, handle: ResumeHandle) -> dict[str, dict[str, str]]:
        return {
            "configurable": {
                "thread_id": handle.thread_id,
            }
        }

    def _verify_handle(
        self,
        record: TaskRecord,
        handle: ResumeHandle,
        authority_snapshot_digest: str,
    ) -> None:
        current_authority_digest = _validate_authority_snapshot_digest(
            authority_snapshot_digest
        )
        if handle.task_id != record.task_id:
            raise ResumeRejected("resume task does not match the current TaskRecord")
        if handle.graph_version != self.graph_version:
            raise ResumeRejected("resume graph version is stale")
        if handle.checkpoint_namespace != self.checkpoint_namespace:
            raise ResumeRejected("resume checkpoint namespace is stale")
        if not _secure_equal(
            handle.authority_snapshot_digest, current_authority_digest
        ):
            raise ResumeRejected("resume authority snapshot digest is stale")
        expected = _new_handle(
            record,
            kind=handle.kind,
            assignment_sequence=handle.assignment_sequence,
            callback_run_id=handle.callback_run_id,
            authority_snapshot_digest=current_authority_digest,
            graph_version=self.graph_version,
            checkpoint_namespace=self.checkpoint_namespace,
        )
        if not _secure_equal(handle.binding_digest, expected.binding_digest):
            raise ResumeRejected("resume authority snapshot or assignment binding is stale")
        if not _secure_equal(handle.thread_id, expected.thread_id):
            raise ResumeRejected("resume checkpoint thread binding is invalid")

    def _state(self, handle: ResumeHandle):
        try:
            return self._graph.get_state(self._config(handle))
        except Exception as exc:  # corrupt/unreadable checkpoints must fail closed
            raise CheckpointUnavailable(
                f"LangGraph checkpoint is unavailable: {type(exc).__name__}"
            ) from exc

    @staticmethod
    def _state_handle(values: Mapping[str, Any]) -> Mapping[str, Any] | None:
        candidate = values.get("handle")
        return candidate if isinstance(candidate, Mapping) else None

    def _verify_checkpoint_binding(
        self, handle: ResumeHandle, values: Mapping[str, Any]
    ) -> None:
        stored = self._state_handle(values)
        if stored is None:
            raise CheckpointUnavailable("LangGraph checkpoint has no resume binding")
        if stored != handle.as_dict():
            raise ResumeRejected("checkpoint binding does not match the requested resume")

    def begin(
        self,
        record: TaskRecord,
        *,
        kind: ResumeKind,
        assignment_sequence: int,
        authority_snapshot_digest: str,
        callback_run_id: str | None = None,
    ) -> ResumeOutcome:
        """Create or replay an interrupt without changing the legacy TaskRecord."""

        try:
            handle = _new_handle(
                record,
                kind=kind,
                assignment_sequence=assignment_sequence,
                callback_run_id=callback_run_id,
                authority_snapshot_digest=authority_snapshot_digest,
                graph_version=self.graph_version,
                checkpoint_namespace=self.checkpoint_namespace,
            )
            snapshot = self._state(handle)
            if snapshot.values:
                self._verify_checkpoint_binding(handle, snapshot.values)
                status = (
                    ResumeStatus.ALREADY_RESUMED
                    if snapshot.values.get("phase") == ResumeStatus.RESUMED.value
                    and not snapshot.next
                    else ResumeStatus.PAUSED
                )
                return ResumeOutcome(
                    status=status,
                    reason="existing bound checkpoint reused",
                    handle=handle,
                    resume_digest=snapshot.values.get("resume_digest"),
                )
            result = self._graph.invoke(
                {"handle": handle.as_dict(), "phase": ResumeStatus.PAUSED.value},
                self._config(handle),
            )
            if not result.get("__interrupt__"):
                raise CheckpointUnavailable("LangGraph failed to persist its interrupt")
            return ResumeOutcome(
                status=ResumeStatus.PAUSED,
                reason="LangGraph interrupt persisted; legacy authority remains required",
                handle=handle,
            )
        except ResumeRejected as exc:
            return ResumeOutcome(ResumeStatus.REJECTED, str(exc))
        except CheckpointUnavailable as exc:
            return ResumeOutcome(ResumeStatus.LEGACY_FALLBACK, str(exc))
        except Exception as exc:
            return ResumeOutcome(
                ResumeStatus.LEGACY_FALLBACK,
                f"LangGraph pause failed closed: {type(exc).__name__}",
            )

    def _legacy_authority_evidence(
        self, record: TaskRecord, handle: ResumeHandle
    ) -> str:
        assignment = _find_assignment(record, handle.assignment_sequence)
        if handle.kind == ResumeKind.ASSIGNMENT_APPROVAL:
            if not assignment_is_approved(record, assignment):
                raise ResumeRejected(
                    "legacy assignment_is_approved rejected this exact assignment"
                )
            return _digest(
                {
                    "kind": handle.kind.value,
                    "assignment_sequence": assignment.sequence,
                    "assignment_digest": assignment_approval_digest(assignment),
                    "legacy_approved": True,
                }
            )

        if not handle.callback_run_id:
            raise ResumeRejected("callback resume lacks its exact run binding")
        run = _find_callback_run(
            record,
            assignment_sequence=assignment.sequence,
            callback_run_id=handle.callback_run_id,
        )
        if (
            run.status not in {"completed", "failed", "cancelled"}
            or not run.result
            or run.evidence_source != "provider_authenticated"
        ):
            raise ResumeRejected(
                "authenticated apply_claude_callback result is not present on the exact run"
            )
        return _digest(
            {
                "kind": handle.kind.value,
                "assignment_sequence": assignment.sequence,
                "callback_run_id": run.run_id,
                "callback_status": run.status,
                "callback_result_digest": hashlib.sha256(
                    run.result.encode("utf-8")
                ).hexdigest(),
                "evidence_source": run.evidence_source,
            }
        )

    def _expected_resume_digest(
        self, handle: ResumeHandle, authority_evidence_digest: str
    ) -> str:
        return _digest(
            {
                "binding_digest": handle.binding_digest,
                "authority_evidence_digest": authority_evidence_digest,
                "graph_version": self.graph_version,
            }
        )

    def prepare_resume(
        self,
        record: TaskRecord,
        handle: ResumeHandle,
        authority_snapshot_digest: str,
    ) -> PreparedResume:
        """Validate legacy authority and return a restart-safe deterministic command."""

        self._verify_handle(record, handle, authority_snapshot_digest)
        evidence_digest = self._legacy_authority_evidence(record, handle)
        snapshot = self._state(handle)
        if not snapshot.values:
            raise CheckpointUnavailable("LangGraph checkpoint is missing; use legacy recovery")
        self._verify_checkpoint_binding(handle, snapshot.values)
        expected_resume_digest = self._expected_resume_digest(handle, evidence_digest)
        if snapshot.values.get("phase") == ResumeStatus.RESUMED.value and not snapshot.next:
            stored_digest = str(snapshot.values.get("resume_digest") or "")
            if not _secure_equal(stored_digest, expected_resume_digest):
                raise ResumeRejected(
                    "completed checkpoint does not match current legacy authority evidence"
                )
            return PreparedResume(handle, evidence_digest, expected_resume_digest)
        if not snapshot.next:
            raise CheckpointUnavailable(
                "LangGraph checkpoint is not at a resumable interrupt"
            )
        return PreparedResume(handle, evidence_digest, expected_resume_digest)

    def commit_resume(
        self,
        record: TaskRecord,
        prepared: PreparedResume,
        authority_snapshot_digest: str,
    ) -> ResumeOutcome:
        """Revalidate at the crash boundary, then issue the sole Command(resume)."""

        try:
            self._verify_handle(
                record, prepared.handle, authority_snapshot_digest
            )
            current_evidence = self._legacy_authority_evidence(record, prepared.handle)
            if not _secure_equal(
                current_evidence, prepared.authority_evidence_digest
            ):
                raise ResumeRejected("legacy authority changed after resume preparation")
            expected_resume_digest = self._expected_resume_digest(
                prepared.handle, current_evidence
            )
            if not _secure_equal(
                prepared.resume_digest, expected_resume_digest
            ):
                raise ResumeRejected(
                    "prepared resume digest does not match current legacy authority evidence"
                )
            snapshot = self._state(prepared.handle)
            if not snapshot.values:
                raise CheckpointUnavailable(
                    "LangGraph checkpoint is missing; use legacy recovery"
                )
            self._verify_checkpoint_binding(prepared.handle, snapshot.values)
            if (
                snapshot.values.get("phase") == ResumeStatus.RESUMED.value
                and not snapshot.next
            ):
                stored_digest = str(snapshot.values.get("resume_digest") or "")
                if not _secure_equal(stored_digest, expected_resume_digest):
                    raise ResumeRejected(
                        "completed checkpoint does not match current legacy authority evidence"
                    )
                return ResumeOutcome(
                    ResumeStatus.ALREADY_RESUMED,
                    "exact resume was already committed",
                    prepared.handle,
                    stored_digest,
                )
            if not snapshot.next:
                raise CheckpointUnavailable(
                    "LangGraph checkpoint is not at a resumable interrupt"
                )
            result = self._graph.invoke(
                Command(resume={"resume_digest": expected_resume_digest}),
                self._config(prepared.handle),
            )
            if result.get("phase") != ResumeStatus.RESUMED.value:
                raise CheckpointUnavailable("LangGraph did not reach its terminal resume state")
            stored_digest = str(result.get("resume_digest") or "")
            if not _secure_equal(stored_digest, expected_resume_digest):
                raise ResumeRejected("LangGraph stored different resume evidence")
            return ResumeOutcome(
                ResumeStatus.RESUMED,
                "legacy-authorized resume committed to the graph checkpoint",
                prepared.handle,
                stored_digest,
            )
        except ResumeRejected as exc:
            return ResumeOutcome(ResumeStatus.REJECTED, str(exc), prepared.handle)
        except CheckpointUnavailable as exc:
            return ResumeOutcome(
                ResumeStatus.LEGACY_FALLBACK, str(exc), prepared.handle
            )
        except Exception as exc:
            # A caller may safely retry after inspecting the checkpoint.  The
            # command is deterministic and a completed checkpoint is replayed as
            # ALREADY_RESUMED rather than issuing a second resume.
            return ResumeOutcome(
                ResumeStatus.LEGACY_FALLBACK,
                f"LangGraph resume failed closed: {type(exc).__name__}",
                prepared.handle,
            )

    def resume(
        self,
        record: TaskRecord,
        handle: ResumeHandle,
        authority_snapshot_digest: str,
    ) -> ResumeOutcome:
        """Validate and resume, safely returning control to legacy on checkpoint faults."""

        try:
            prepared = self.prepare_resume(
                record, handle, authority_snapshot_digest
            )
        except ResumeRejected as exc:
            return ResumeOutcome(ResumeStatus.REJECTED, str(exc), handle)
        except CheckpointUnavailable as exc:
            return ResumeOutcome(ResumeStatus.LEGACY_FALLBACK, str(exc), handle)
        except Exception as exc:
            return ResumeOutcome(
                ResumeStatus.LEGACY_FALLBACK,
                f"LangGraph resume preparation failed closed: {type(exc).__name__}",
                handle,
            )
        return self.commit_resume(record, prepared, authority_snapshot_digest)


# [[Codex | GPT-5.6 | 2026-08-22 16:07 UTC | END]]
