from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from .models import (
    AmbiguousDispatchResolution,
    Assignment,
    OwnerApproval,
    OwnerApprovalSource,
    ProviderContactAttempt,
    ProviderName,
    ProviderRun,
    ReviewVerdict,
    TaskRecord,
    TaskStatus,
)
from .team_charter import AdvisoryVoteTally


class StaleApproval(ValueError):
    pass


_SYNTHETIC_APPROVAL_NOTES = frozenset({"Approved", "Approved from Slack"})
_BUTTON_VALUE_VERSION = "v1"
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def owner_identity_matches(provided: str, configured: str) -> bool:
    """Constant-time owner check that fails closed when no owner is configured.

    hmac.compare_digest("", "") is True, so an unset SLACK_OWNER_USER_ID would
    otherwise let any caller approve gated assignments by sending an empty
    owner_user_id. require_auth fails closed the same way on a missing secret.
    """
    if not configured or not provided:
        return False
    return hmac.compare_digest(provided, configured)


def pending_assignment(record: TaskRecord) -> Assignment | None:
    """The assignment the engine is currently stopped on, if any."""
    if record.plan is None:
        return None
    assignments = sorted(record.plan.assignments, key=lambda item: item.sequence)
    if record.next_assignment_index >= len(assignments):
        return None
    return assignments[record.next_assignment_index]


def assignment_approval_digest(assignment: Assignment) -> str:
    return _canonical_digest(assignment.model_dump(mode="json"))


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def assignment_is_approved(record: TaskRecord, assignment: Assignment) -> bool:
    return (
        assignment.sequence in record.approved_assignments
        and record.approved_assignment_digests.get(assignment.sequence)
        == assignment_approval_digest(assignment)
    )


def unresolved_ambiguous_dispatch(
    record: TaskRecord, assignment: Assignment
) -> ProviderContactAttempt | None:
    attempts = [
        item
        for item in record.provider_contact_attempts
        if item.assignment_sequence == assignment.sequence
        and item.provider == assignment.agent
    ]
    unresolved: ProviderContactAttempt | None = None
    for item in attempts:
        if item.outcome == "ambiguous_dispatch":
            unresolved = item
        elif item.outcome in {"reconciled_not_created", "reconciled_created"}:
            unresolved = None
    return unresolved


def plan_gate_pending(record: TaskRecord) -> bool:
    """Whether the engine is stopped at the plan-level gate rather than an assignment.

    Mirrors the first gate in OrchestrationEngine._run_locked. That gate shows
    only the plan's approval reasons, never an assignment's task.
    """
    return bool(record.plan and record.plan.approval_required and not record.owner_approved)


def plan_approval_digest(record: TaskRecord) -> str:
    if record.plan is None:
        raise StaleApproval("Task has no plan to approve")
    return _canonical_digest(record.plan.model_dump(mode="json"))


@dataclass(frozen=True)
class CurrentOwnerDecision:
    kind: str
    digest: str
    assignment_sequence: int | None
    clarification: bool


def current_owner_decision(record: TaskRecord) -> CurrentOwnerDecision | None:
    """Classify and bind the exact deterministic owner stop currently pending."""

    if record.status != TaskStatus.OWNER_DECISION_REQUIRED or record.plan is None:
        return None
    plan_digest = plan_approval_digest(record)
    kind: str
    assignment: Assignment | None = None
    source_digest: str | None = None
    clarification = False

    if record.plan.owner_question:
        kind = "plan_clarification"
        clarification = True
    elif plan_gate_pending(record):
        kind = "plan_approval"
    else:
        pending = pending_assignment(record)
        if pending is not None and pending.agent == ProviderName.OWNER:
            kind = "assignment_clarification"
            assignment = pending
            clarification = True
        elif (
            pending is not None
            and pending.requires_approval
            and not assignment_is_approved(record, pending)
        ):
            kind = "assignment_approval"
            assignment = pending
        elif record.debate_tally:
            try:
                tally = AdvisoryVoteTally.model_validate(record.debate_tally)
            except ValueError:
                tally = None
            if tally is not None and tally.resolution == "owner_decision_required":
                kind = "debate_clarification"
                source_digest = _canonical_digest(
                    {
                        "tally": tally.model_dump(mode="json"),
                        "synthesis": (
                            record.debate_synthesis.model_dump(mode="json")
                            if record.debate_synthesis is not None
                            else None
                        ),
                    }
                )
                clarification = True
            elif record.review is not None and record.review.verdict == ReviewVerdict.OWNER:
                kind = "review_clarification"
                source_digest = _canonical_digest(record.review.model_dump(mode="json"))
                clarification = True
            else:
                return None
        elif record.review is not None and record.review.verdict == ReviewVerdict.OWNER:
            kind = "review_clarification"
            source_digest = _canonical_digest(record.review.model_dump(mode="json"))
            clarification = True
        else:
            return None

    payload: dict[str, object] = {
        "task_id": record.task_id,
        "kind": kind,
        "plan_generation": record.plan_generation,
        "plan_digest": plan_digest,
    }
    if assignment is not None:
        payload["assignment_sequence"] = assignment.sequence
        payload["assignment_digest"] = assignment_approval_digest(assignment)
    if source_digest is not None:
        payload["source_digest"] = source_digest
    return CurrentOwnerDecision(
        kind=kind,
        digest="sha256:" + _canonical_digest(payload),
        assignment_sequence=assignment.sequence if assignment is not None else None,
        clarification=clarification,
    )


def approval_decision_digest(
    record: TaskRecord, assignment_sequence: int | None = None
) -> str:
    """Digest the exact current plan or assignment decision shown to the owner."""

    decision = current_owner_decision(record)
    if decision is None or decision.kind not in {"plan_approval", "assignment_approval"}:
        raise StaleApproval("Task is not awaiting an approvable decision")
    if decision.assignment_sequence != assignment_sequence:
        raise StaleApproval("Slack approval button targets a different decision kind or scope")
    return decision.digest


def approval_button_value(
    record: TaskRecord, assignment_sequence: int | None = None
) -> str:
    sequence = "" if assignment_sequence is None else str(assignment_sequence)
    digest = approval_decision_digest(record, assignment_sequence)
    return f"{_BUTTON_VALUE_VERSION}|{record.task_id}|{sequence}|{digest}"


def parse_approval_button_value(value: str) -> tuple[str, int | None, str]:
    parts = value.split("|")
    if len(parts) != 4 or parts[0] != _BUTTON_VALUE_VERSION:
        raise StaleApproval("Malformed or legacy Slack approval button")
    _, task_id, raw_sequence, digest = parts
    if not task_id or not _SHA256_PATTERN.fullmatch(digest):
        raise StaleApproval("Malformed Slack approval button binding")
    sequence: int | None = None
    if raw_sequence:
        if not raw_sequence.isdigit() or not 1 <= int(raw_sequence) <= 20:
            raise StaleApproval("Malformed Slack approval assignment sequence")
        sequence = int(raw_sequence)
    return task_id, sequence, digest


def apply_owner_approval(record: TaskRecord, approval: OwnerApproval) -> None:
    """Record an owner approval, scoped to the assignment it was shown for.

    Raises StaleApproval if the approval names an assignment that is not the one
    now pending -- a Slack approval button persists in the channel, so an old
    button must not authorize work the owner was never shown.
    """
    pending = pending_assignment(record)
    if pending is not None and unresolved_ambiguous_dispatch(record, pending):
        raise StaleApproval(
            "Ambiguous provider dispatch must be reconciled before the task can resume"
        )
    decision = current_owner_decision(record)
    if decision is None or decision.kind not in {"plan_approval", "assignment_approval"}:
        raise StaleApproval("Task is not awaiting an approvable owner decision")
    if (
        approval.assignment_sequence is not None
        and approval.assignment_sequence != decision.assignment_sequence
    ):
        raise StaleApproval("This approval refers to a different pending decision")
    if approval.source == OwnerApprovalSource.SLACK_BUTTON:
        if approval.assignment_sequence != decision.assignment_sequence:
            raise StaleApproval("Slack approval button targets a different decision kind or scope")
        if approval.decision_digest is None or not hmac.compare_digest(
            approval.decision_digest, decision.digest
        ):
            raise StaleApproval("Slack approval button no longer matches the pending decision")
    elif approval.note.strip() == "Approved from Slack":
        # Defense in depth for model_construct or persisted legacy payloads.
        raise StaleApproval("Unbound legacy Slack button approval is not accepted")

    if approval.assignment_sequence is not None and (
        pending is None or approval.assignment_sequence != pending.sequence
    ):
        raise StaleApproval("This approval refers to an assignment that is no longer pending")

    # Clearing the plan gate must not also clear the first assignment: the plan
    # prompt lists why approval is needed, but not what the assignment will do.
    at_plan_gate = decision.kind == "plan_approval"
    record.owner_approved = True
    if at_plan_gate or pending is None:
        return
    if pending.sequence not in record.approved_assignments:
        record.approved_assignments.append(pending.sequence)
    record.approved_assignment_digests[pending.sequence] = assignment_approval_digest(pending)


def apply_ambiguous_dispatch_resolution(
    record: TaskRecord, resolution: AmbiguousDispatchResolution
) -> None:
    if record.status != TaskStatus.OWNER_DECISION_REQUIRED:
        raise StaleApproval("Task is not awaiting dispatch reconciliation")
    pending = pending_assignment(record)
    if pending is None or pending.sequence != resolution.assignment_sequence:
        raise StaleApproval("Dispatch reconciliation does not match the pending assignment")
    ambiguous = unresolved_ambiguous_dispatch(record, pending)
    if ambiguous is None:
        raise StaleApproval("The pending assignment has no unresolved ambiguous dispatch")

    run: ProviderRun | None = None
    if resolution.resolution == "created":
        if pending.agent == ProviderName.PERPLEXITY:
            raise StaleApproval(
                "Perplexity search responses cannot be recovered by run ID; "
                "confirm not-created or start a newly approved assignment"
            )
        if pending.agent == ProviderName.CURSOR and not resolution.session_id:
            raise StaleApproval("Cursor created reconciliation requires session_id")
        if pending.agent not in {
            ProviderName.CURSOR,
            ProviderName.CLAUDE,
            ProviderName.GEMINI,
        }:
            raise StaleApproval("This provider does not support created-run reconciliation")
        status = (
            "WAITING_CALLBACK"
            if pending.agent == ProviderName.CLAUDE
            else "RECONCILED_PENDING"
        )
        run = ProviderRun(
            provider=pending.agent,
            assignment_sequence=pending.sequence,
            run_id=resolution.run_id or "",
            status=status,
            session_id=(
                resolution.session_id
                or (resolution.run_id if pending.agent == ProviderName.CLAUDE else None)
            ),
            evidence_source="unverified",
            raw={"reconciled_identifier_only": True},
        )
        record.provider_runs.append(run)
        if ambiguous.phase == "revision":
            record.revision_count += 1

    record.provider_contact_attempts.append(
        ProviderContactAttempt(
            assignment_sequence=pending.sequence,
            provider=pending.agent,
            ordinal=ambiguous.ordinal,
            phase="reconciliation",
            outcome=(
                "reconciled_created"
                if resolution.resolution == "created"
                else "reconciled_not_created"
            ),
            run_id=run.run_id if run else None,
            error=("Owner reconciliation: " + resolution.note.strip())[:2_000],
        )
    )
    record.status = TaskStatus.QUEUED
    record.error = None
    record.message = (
        "Owner reconciled the ambiguous dispatch as "
        + ("an existing provider run." if run else "not created; one new dispatch is permitted.")
    )


def reset_for_owner_clarification(record: TaskRecord) -> None:
    """Discard every execution artifact derived from a superseded plan."""

    record.plan = None
    record.next_assignment_index = 0
    record.provider_runs = []
    record.assignment_outcomes = []
    record.review_handoff_digests = {}
    record.provider_contact_attempts = []
    record.unavailable_providers = []
    record.deferred_assignments = []
    record.availability_impacts = []
    record.collaboration_thread_ts = None
    record.collaboration_thread_opened = False
    record.collaboration_turns = []
    record.collaboration_delivery_errors = []
    record.collaboration_fallback_active = False
    record.collaboration_fallback_reason = None
    record.debate_tally = None
    record.debate_synthesis = None
    record.review = None
    record.revision_count = 0
    record.owner_approved = False
    record.approved_assignments = []
    record.approved_assignment_digests = {}
    record.error = None


def clarification_source(record: TaskRecord) -> str | None:
    decision = current_owner_decision(record)
    if decision is None or not decision.clarification:
        return None
    return decision.kind


def clarification_required(record: TaskRecord) -> bool:
    return clarification_source(record) is not None


def apply_owner_clarification(record: TaskRecord, approval: OwnerApproval) -> None:
    """Fold a written clarification into the intake and discard the answered plan.

    The plan is rebuilt from the clarified text, so every piece of state keyed to
    the discarded plan's sequence numbers must be discarded with it. Accepted
    outcomes are part of that state: left behind, a rebuilt assignment could
    satisfy its depends_on check, or inherit handoff evidence, from work the new
    plan never authorized.
    """
    if clarification_source(record) is None:
        raise StaleApproval("Task is not awaiting a written owner clarification")
    if approval.source == OwnerApprovalSource.SLACK_BUTTON:
        raise StaleApproval("Slack approval buttons cannot provide written clarification")
    if approval.assignment_sequence is not None:
        raise StaleApproval(
            "A written clarification cannot be bound to an assignment approval"
        )
    note = approval.note.strip()
    if not note or note in _SYNTHETIC_APPROVAL_NOTES:
        raise StaleApproval("This task needs a written clarification, not a bare approval")
    record.intake.text += f"\n\nOwner clarification: {note}"
    reset_for_owner_clarification(record)


def apply_failed_predispatch_retry(record: TaskRecord, approval: OwnerApproval) -> None:
    """Retry a failed assignment only when no provider run was recorded."""
    if record.status != TaskStatus.FAILED:
        raise StaleApproval("Task is not failed")
    pending = pending_assignment(record)
    if pending is None or approval.assignment_sequence != pending.sequence:
        raise StaleApproval("A failed retry must name the exact pending assignment")
    if pending.requires_approval and not assignment_is_approved(record, pending):
        raise StaleApproval("The pending assignment has not been approved")
    if unresolved_ambiguous_dispatch(record, pending) is not None:
        raise StaleApproval("Ambiguous provider dispatch requires explicit reconciliation")
    if any(run.assignment_sequence == pending.sequence for run in record.provider_runs):
        raise StaleApproval("A provider run already exists; retry could duplicate dispatch")
    note = approval.note.strip()
    if not note or note in _SYNTHETIC_APPROVAL_NOTES:
        raise StaleApproval("A failed retry requires an explicit owner note")
    record.status = TaskStatus.QUEUED
    record.error = None
    record.message = note
