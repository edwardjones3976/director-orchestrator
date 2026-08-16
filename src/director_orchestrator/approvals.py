from __future__ import annotations

import hmac

from .models import Assignment, OwnerApproval, TaskRecord, TaskStatus


class StaleApproval(ValueError):
    pass


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


def plan_gate_pending(record: TaskRecord) -> bool:
    """Whether the engine is stopped at the plan-level gate rather than an assignment.

    Mirrors the first gate in OrchestrationEngine._run_locked. That gate shows
    only the plan's approval reasons, never an assignment's task.
    """
    return bool(record.plan and record.plan.approval_required and not record.owner_approved)


def apply_owner_approval(record: TaskRecord, approval: OwnerApproval) -> None:
    """Record an owner approval, scoped to the assignment it was shown for.

    Raises StaleApproval if the approval names an assignment that is not the one
    now pending -- a Slack approval button persists in the channel, so an old
    button must not authorize work the owner was never shown.
    """
    pending = pending_assignment(record)
    if approval.assignment_sequence is not None and (
        pending is None or approval.assignment_sequence != pending.sequence
    ):
        raise StaleApproval("This approval refers to an assignment that is no longer pending")

    # Clearing the plan gate must not also clear the first assignment: the plan
    # prompt lists why approval is needed, but not what the assignment will do.
    at_plan_gate = plan_gate_pending(record)
    record.owner_approved = True
    if at_plan_gate or pending is None:
        return
    if pending.sequence not in record.approved_assignments:
        record.approved_assignments.append(pending.sequence)


def apply_failed_predispatch_retry(record: TaskRecord, approval: OwnerApproval) -> None:
    """Retry a failed assignment only when no provider run was recorded."""
    if record.status != TaskStatus.FAILED:
        raise StaleApproval("Task is not failed")
    pending = pending_assignment(record)
    if pending is None or approval.assignment_sequence != pending.sequence:
        raise StaleApproval("A failed retry must name the exact pending assignment")
    if pending.requires_approval and pending.sequence not in record.approved_assignments:
        raise StaleApproval("The pending assignment has not been approved")
    if any(run.assignment_sequence == pending.sequence for run in record.provider_runs):
        raise StaleApproval("A provider run already exists; retry could duplicate dispatch")
    if not approval.note.strip() or approval.note == "Approved":
        raise StaleApproval("A failed retry requires an explicit owner note")
    record.status = TaskStatus.QUEUED
    record.error = None
    record.message = approval.note.strip()
