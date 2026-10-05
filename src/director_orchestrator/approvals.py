# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:35 UTC | BEGIN]]
# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | BEGIN]]
# Administrative attribution only; material implementation remains Cursor-authored.
# [[Cursor | Composer 2.5 | 2026-09-21 | 20:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 01:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:54 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 16:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:47 UTC | BEGIN]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:06 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:21 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import (
    AmbiguousDispatchResolution,
    Assignment,
    AssignmentOutcome,
    IntakeAuthentication,
    MAX_SLACK_INTAKE_TEXT,
    OracleRecoveryApproval,
    OracleRecoveryState,
    OpaqueEvidenceDigest,
    OwnerApproval,
    OwnerApprovalSource,
    ProviderContactAttempt,
    ProviderName,
    ProviderRun,
    ReviewVerdict,
    SlackGatewayProof,
    SlackIntake,
    SlackStatusReceipt,
    TaskEffectEvidence,
    TaskRecord,
    TaskStatus,
    canonical_opaque_identifier_digest,
    canonical_task_effect_digest,
)
from .team_charter import AdvisoryVoteTally


class StaleApproval(ValueError):
    pass


class EffectAlreadyClaimed(StaleApproval):
    """Another worker owns the same current external-effect attempt."""


_SYNTHETIC_APPROVAL_NOTES = frozenset({"Approved", "Approved from Slack"})
_BUTTON_VALUE_VERSION = "v1"
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_TASK_EFFECTS = 200
_SLACK_GATEWAY_PROOF_TTL_SECONDS = 300
_SLACK_GATEWAY_FUTURE_SKEW_SECONDS = 30
PAID_REARM_MESSAGE = (
    "Paid execution requires a fresh authenticated Slack gateway proof."
)
_SLACK_GATEWAY_MIN_SECRET_LENGTH = 32
_SLACK_INTAKE_ATTESTATION_VERSION = "v1"
_SLACK_STATUS_RECEIPT_ATTESTATION_VERSION = "v1"
SLACK_INTAKE_ISSUED_AT_HEADER = "X-Director-Intake-Issued-At"
SLACK_INTAKE_NONCE_HEADER = "X-Director-Intake-Nonce"
SLACK_INTAKE_SIGNATURE_HEADER = "X-Director-Intake-Signature"
SLACK_STATUS_RECEIPT_ISSUED_AT_HEADER = "X-Director-Status-Receipt-Issued-At"
SLACK_STATUS_RECEIPT_NONCE_HEADER = "X-Director-Status-Receipt-Nonce"
SLACK_STATUS_RECEIPT_SIGNATURE_HEADER = "X-Director-Status-Receipt-Signature"
_SLACK_INTAKE_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_SLACK_INTAKE_SIGNATURE_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def owner_identity_matches(provided: str, configured: str) -> bool:
    """Constant-time owner check that fails closed when no owner is configured.

    hmac.compare_digest("", "") is True, so an unset SLACK_OWNER_USER_ID would
    otherwise let any caller approve gated assignments by sending an empty
    owner_user_id. require_auth fails closed the same way on a missing secret.
    """
    if not configured or not provided:
        return False
    return hmac.compare_digest(provided, configured)


def _slack_intake_attestation_payload(
    intake: SlackIntake, *, issued_at: int, nonce: str
) -> bytes:
    return json.dumps(
        {
            "version": _SLACK_INTAKE_ATTESTATION_VERSION,
            "method": "POST",
            "path": "/v1/intake",
            "issued_at": issued_at,
            "nonce": nonce,
            "intake": intake.model_dump(mode="json"),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_slack_intake_attestation(
    secret: str,
    shared_secret: str,
    intake: SlackIntake,
    *,
    issued_at: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Bind one short-lived Slack intake to its exact canonical payload."""

    if len(secret) < _SLACK_GATEWAY_MIN_SECRET_LENGTH:
        raise StaleApproval("Slack gateway signing secret is not securely configured")
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise StaleApproval("Slack gateway signing secret must be distinct")
    observed_at = (
        issued_at
        if issued_at is not None
        else int(datetime.now(timezone.utc).timestamp())
    )
    observed_nonce = nonce or secrets.token_urlsafe(32)
    if not _SLACK_INTAKE_NONCE_PATTERN.fullmatch(observed_nonce):
        raise StaleApproval("Slack intake attestation nonce is malformed")
    signature = hmac.new(
        secret.encode("utf-8"),
        _slack_intake_attestation_payload(
            intake, issued_at=observed_at, nonce=observed_nonce
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        SLACK_INTAKE_ISSUED_AT_HEADER: str(observed_at),
        SLACK_INTAKE_NONCE_HEADER: observed_nonce,
        SLACK_INTAKE_SIGNATURE_HEADER: signature,
    }


def verify_slack_intake_attestation(
    secret: str,
    shared_secret: str,
    intake: SlackIntake,
    *,
    expected_owner_user_id: str,
    expected_channel_id: str,
    issued_at: str | None,
    nonce: str | None,
    signature: str | None,
    now: int | None = None,
) -> tuple[str, str]:
    """Fail closed unless the Slack gateway attested this owner intake exactly."""

    if len(secret) < _SLACK_GATEWAY_MIN_SECRET_LENGTH:
        raise StaleApproval("Authenticated Slack intake attestation is required")
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise StaleApproval("Slack gateway signing secret must be distinct")
    if not owner_identity_matches(intake.user_id, expected_owner_user_id):
        raise StaleApproval("Slack intake owner identity mismatch")
    if (
        not intake.channel_id
        or not expected_channel_id
        or not hmac.compare_digest(intake.channel_id, expected_channel_id)
    ):
        raise StaleApproval("Slack intake channel mismatch")
    if (
        issued_at is None
        or nonce is None
        or signature is None
        or not issued_at.isdigit()
        or not _SLACK_INTAKE_NONCE_PATTERN.fullmatch(nonce)
        or not _SLACK_INTAKE_SIGNATURE_PATTERN.fullmatch(signature)
    ):
        raise StaleApproval("Slack intake attestation is missing or malformed")
    issued_timestamp = int(issued_at)
    current_time = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    age = current_time - issued_timestamp
    if age < -_SLACK_GATEWAY_FUTURE_SKEW_SECONDS or age > _SLACK_GATEWAY_PROOF_TTL_SECONDS:
        raise StaleApproval("Slack intake attestation is expired or not yet valid")
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        _slack_intake_attestation_payload(
            intake, issued_at=issued_timestamp, nonce=nonce
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise StaleApproval("Slack intake attestation signature is invalid")
    return (
        _canonical_digest({"nonce": nonce}),
        _canonical_digest(intake.model_dump(mode="json")),
    )


def _slack_status_receipt_attestation_payload(
    receipt: SlackStatusReceipt, *, issued_at: int, nonce: str
) -> bytes:
    return json.dumps(
        {
            "version": _SLACK_STATUS_RECEIPT_ATTESTATION_VERSION,
            "method": "POST",
            "path": "/v1/status-receipts",
            "issued_at": issued_at,
            "nonce": nonce,
            "receipt": receipt.model_dump(mode="json"),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_slack_status_receipt_attestation(
    secret: str,
    shared_secret: str,
    receipt: SlackStatusReceipt,
    *,
    issued_at: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Bind one short-lived Slack status receipt to its canonical payload."""

    if len(secret) < _SLACK_GATEWAY_MIN_SECRET_LENGTH:
        raise StaleApproval("Slack gateway signing secret is not securely configured")
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise StaleApproval("Slack gateway signing secret must be distinct")
    observed_at = (
        issued_at
        if issued_at is not None
        else int(datetime.now(timezone.utc).timestamp())
    )
    observed_nonce = nonce or secrets.token_urlsafe(32)
    if not _SLACK_INTAKE_NONCE_PATTERN.fullmatch(observed_nonce):
        raise StaleApproval("Slack status receipt attestation nonce is malformed")
    signature = hmac.new(
        secret.encode("utf-8"),
        _slack_status_receipt_attestation_payload(
            receipt, issued_at=observed_at, nonce=observed_nonce
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        SLACK_STATUS_RECEIPT_ISSUED_AT_HEADER: str(observed_at),
        SLACK_STATUS_RECEIPT_NONCE_HEADER: observed_nonce,
        SLACK_STATUS_RECEIPT_SIGNATURE_HEADER: signature,
    }


def verify_slack_status_receipt_attestation(
    secret: str,
    shared_secret: str,
    receipt: SlackStatusReceipt,
    *,
    expected_owner_user_id: str,
    expected_channel_id: str,
    issued_at: str | None,
    nonce: str | None,
    signature: str | None,
    now: int | None = None,
) -> tuple[str, str]:
    """Fail closed unless the Slack gateway attested this owner status receipt."""

    if len(secret) < _SLACK_GATEWAY_MIN_SECRET_LENGTH:
        raise StaleApproval("Authenticated Slack status receipt attestation is required")
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise StaleApproval("Slack gateway signing secret must be distinct")
    if not owner_identity_matches(receipt.user_id, expected_owner_user_id):
        raise StaleApproval("Slack status receipt owner identity mismatch")
    if (
        not receipt.channel_id
        or not expected_channel_id
        or not hmac.compare_digest(receipt.channel_id, expected_channel_id)
    ):
        raise StaleApproval("Slack status receipt channel mismatch")
    if (
        issued_at is None
        or nonce is None
        or signature is None
        or not issued_at.isdigit()
        or not _SLACK_INTAKE_NONCE_PATTERN.fullmatch(nonce)
        or not _SLACK_INTAKE_SIGNATURE_PATTERN.fullmatch(signature)
    ):
        raise StaleApproval("Slack status receipt attestation is missing or malformed")
    issued_timestamp = int(issued_at)
    current_time = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    age = current_time - issued_timestamp
    if age < -_SLACK_GATEWAY_FUTURE_SKEW_SECONDS or age > _SLACK_GATEWAY_PROOF_TTL_SECONDS:
        raise StaleApproval("Slack status receipt attestation is expired or not yet valid")
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        _slack_status_receipt_attestation_payload(
            receipt, issued_at=issued_timestamp, nonce=nonce
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise StaleApproval("Slack status receipt attestation signature is invalid")
    return (
        _canonical_digest({"nonce": nonce}),
        _canonical_digest(receipt.model_dump(mode="json")),
    )


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


def provider_run_digest(run: ProviderRun) -> str:
    payload = run.model_dump(mode="json")
    if run.plan_generation is None:
        payload.pop("plan_generation")  # Preserve digests written before this field existed.
    return _canonical_digest(payload)


def opaque_identifier_digest(value: str) -> str:
    return canonical_opaque_identifier_digest(value)


def _refresh_cumulative_cost(record: TaskRecord) -> None:
    observations: dict[str, set[str]] = {}
    intents: dict[str, TaskEffectEvidence] = {}
    legacy: dict[str, TaskEffectEvidence] = {}
    for effect in record.task_effect_ledger:
        if effect.kind in {"provider_intent", "director_intent"}:
            intents[effect.effect_key] = effect
        elif effect.kind in {"provider_observation", "director_observation"}:
            observations.setdefault(effect.effect_key, set()).add(effect.outcome)
            if effect.effect_key not in intents:
                legacy[effect.effect_key] = effect
    reserved = [
        effect.estimated_cost_usd
        for key, effect in intents.items()
        if observations.get(key, set()) != {"failed_pre_submit"}
    ]
    reserved.extend(effect.estimated_cost_usd for effect in legacy.values())
    record.cumulative_estimated_cost_usd = round(sum(reserved), 8)
    record.cumulative_effect_hops = sum(
        effect.hop_units
        for key, effect in intents.items()
        if observations.get(key, set()) != {"failed_pre_submit"}
    )


def task_effect_key(
    record: TaskRecord,
    *,
    phase: str,
    identity: object,
) -> str:
    """Return a stable opaque identity without persisting input prose."""

    return _canonical_digest(
        {
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "phase": phase,
            "identity_digest": _canonical_digest(identity),
        }
    )


def _append_generic_task_effect(
    record: TaskRecord,
    *,
    effect_key: str,
    kind: str,
    phase: str,
    outcome: str,
    assignment: Assignment | None = None,
    provider: ProviderName | None = None,
    attempt: int = 1,
    run_id: str | None = None,
    estimated_cost_usd: float = 0,
    hop_units: int = 0,
) -> TaskEffectEvidence:
    assignment_digest = (
        assignment_approval_digest(assignment) if assignment is not None else None
    )
    provider = assignment.agent if assignment is not None else provider
    payload = {
        "event_version": "v2",
        "effect_key": effect_key,
        "kind": kind,
        "phase": phase,
        "outcome": outcome,
        "plan_generation": record.plan_generation,
        "assignment_sequence": assignment.sequence if assignment else None,
        "assignment_digest": assignment_digest,
        "provider": provider.value if provider else None,
        "attempt": attempt,
        "run_id_digest": opaque_identifier_digest(run_id) if run_id else None,
        "estimated_cost_usd": estimated_cost_usd,
        "hop_units": hop_units,
    }
    effect = TaskEffectEvidence(
        effect_digest=canonical_task_effect_digest(payload),
        effect_key=effect_key,
        kind=kind,
        phase=phase,
        outcome=outcome,
        plan_generation=record.plan_generation,
        assignment_sequence=assignment.sequence if assignment else None,
        assignment_digest=assignment_digest,
        provider=provider,
        attempt=attempt,
        run_id_digest=opaque_identifier_digest(run_id) if run_id else None,
        estimated_cost_usd=estimated_cost_usd,
        hop_units=hop_units,
    )
    is_new = all(
        item.effect_digest != effect.effect_digest for item in record.task_effect_ledger
    )
    if is_new and len(record.task_effect_ledger) >= _MAX_TASK_EFFECTS:
        raise StaleApproval(
            "Immutable task effect ledger is full; external work remains blocked"
        )
    if is_new:
        record.task_effect_ledger.append(effect)
    _refresh_cumulative_cost(record)
    return effect


def reserve_task_effect(
    record: TaskRecord,
    *,
    effect_key: str,
    kind: str,
    phase: str,
    provider: ProviderName | None = None,
    assignment: Assignment | None = None,
    estimated_cost_usd: float = 0,
    hop_units: int = 0,
    max_task_budget_usd: float | None = None,
    replay_safe: bool = False,
) -> TaskEffectEvidence:
    """Reserve an externally effective operation before I/O.

    `replay_safe` is limited to transports or services with their own stable
    idempotency identity (Slack client_msg_id and Oracle artifact_id). Paid
    model calls never opt in: an unresolved reservation fails closed.
    """

    existing = next(
        (
            item
            for item in record.task_effect_ledger
            if item.effect_key == effect_key and item.kind == kind
        ),
        None,
    )
    if existing is not None:
        if replay_safe:
            return existing
        raise StaleApproval(
            "An external-effect intent already exists and must not be duplicated"
        )
    if len(record.task_effect_ledger) > _MAX_TASK_EFFECTS - 2:
        raise StaleApproval(
            "Immutable task effect ledger lacks room for a complete external attempt"
        )
    _refresh_cumulative_cost(record)
    if (
        max_task_budget_usd is not None
        and record.cumulative_estimated_cost_usd + estimated_cost_usd
        > max_task_budget_usd
    ):
        raise StaleApproval(
            "Cumulative task budget would be exceeded by this external call"
        )
    return _append_generic_task_effect(
        record,
        effect_key=effect_key,
        kind=kind,
        phase=phase,
        outcome="reserved",
        assignment=assignment,
        provider=provider,
        estimated_cost_usd=estimated_cost_usd,
        hop_units=hop_units,
    )


def record_task_effect_observation(
    record: TaskRecord,
    *,
    effect_key: str,
    intent_kind: str,
    observation_kind: str,
    phase: str,
    outcome: str,
    run_id: str | None = None,
) -> TaskEffectEvidence:
    intent = next(
        (
            item
            for item in record.task_effect_ledger
            if item.effect_key == effect_key and item.kind == intent_kind
        ),
        None,
    )
    if intent is None:
        raise StaleApproval("External-effect observation has no persisted intent")
    return _append_generic_task_effect(
        record,
        effect_key=effect_key,
        kind=observation_kind,
        phase=phase,
        outcome=outcome,
        provider=intent.provider,
        attempt=intent.attempt,
        run_id=run_id,
        estimated_cost_usd=intent.estimated_cost_usd,
        hop_units=intent.hop_units,
    )


def _append_task_effect(
    record: TaskRecord,
    *,
    effect_key: str,
    kind: str,
    phase: str,
    outcome: str,
    assignment: Assignment,
    attempt: int,
    run_id: str | None = None,
) -> TaskEffectEvidence:
    counts_as_provider_call = kind in {"provider_intent", "provider_observation"}
    return _append_generic_task_effect(
        record,
        effect_key=effect_key,
        kind=kind,
        phase=phase,
        outcome=outcome,
        assignment=assignment,
        attempt=attempt,
        run_id=run_id,
        estimated_cost_usd=(assignment.estimated_cost_usd if counts_as_provider_call else 0),
        hop_units=(1 if counts_as_provider_call else 0),
    )


def reserve_provider_effect(
    record: TaskRecord,
    assignment: Assignment,
    *,
    phase: str,
    max_task_budget_usd: float,
) -> str:
    """Persist a unique paid-effect intent before an external provider call."""

    # One intent plus one terminal/ambiguity observation are required for every
    # external call. Refuse before I/O if both immutable slots are unavailable.
    if len(record.task_effect_ledger) > _MAX_TASK_EFFECTS - 2:
        raise StaleApproval(
            "Immutable task effect ledger lacks room for a complete provider attempt"
        )

    assignment_digest = assignment_approval_digest(assignment)
    relevant = [
        item
        for item in record.task_effect_ledger
        if item.assignment_digest == assignment_digest
        and item.phase == phase
        and item.kind in {"provider_intent", "provider_observation"}
    ]
    observations: dict[str, set[str]] = {}
    for item in relevant:
        if item.kind == "provider_observation":
            observations.setdefault(item.effect_key, set()).add(item.outcome)
    for intent in (item for item in relevant if item.kind == "provider_intent"):
        outcomes = observations.get(intent.effect_key, set())
        if intent.plan_generation != record.plan_generation and "failed_pre_submit" not in outcomes:
            raise StaleApproval(
                "The rebuilt plan repeats a prior paid or repository-effect assignment"
            )
        unresolved_current_attempt = (
            phase == "dispatch" or intent.attempt > record.revision_count
        )
        if (
            intent.plan_generation == record.plan_generation
            and "failed_pre_submit" not in outcomes
            and unresolved_current_attempt
        ):
            raise EffectAlreadyClaimed(
                "A provider dispatch intent already exists and must not be duplicated"
            )
    # Migrated observations have no preceding intent but still prove an effect.
    if any(item.kind == "provider_observation" for item in relevant) and not any(
        item.kind == "provider_intent" for item in relevant
    ):
        raise StaleApproval(
            "The rebuilt plan repeats preserved provider-run evidence"
        )

    attempt = 1 + sum(
        item.kind == "provider_intent"
        and item.plan_generation == record.plan_generation
        and item.assignment_digest == assignment_digest
        and item.phase == phase
        for item in record.task_effect_ledger
    )
    _refresh_cumulative_cost(record)
    if record.cumulative_estimated_cost_usd + assignment.estimated_cost_usd > max_task_budget_usd:
        raise StaleApproval("Cumulative task budget would be exceeded by this provider dispatch")
    effect_key = _canonical_digest(
        {
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "assignment_digest": assignment_digest,
            "phase": phase,
            "attempt": attempt,
        }
    )
    _append_task_effect(
        record,
        effect_key=effect_key,
        kind="provider_intent",
        phase=phase,
        outcome="reserved",
        assignment=assignment,
        attempt=attempt,
    )
    return effect_key


def retire_terminal_revision_authority(record: TaskRecord) -> None:
    """Retire only terminal recovery bindings; consumed retry digests stay spent."""
    latest = {}
    for item in record.task_effect_ledger:
        if item.kind == "provider_observation" and item.phase == "revision":
            latest[item.effect_key] = item.outcome
    for key, outcome in latest.items():
        if outcome in {"completed", "failed_pre_submit"}:
            record.revision_authority_by_effect.pop(key, None)


def record_provider_effect_observation(
    record: TaskRecord,
    assignment: Assignment,
    *,
    effect_key: str,
    phase: str,
    outcome: str,
    run_id: str | None = None,
) -> None:
    intent = next(
        (
            item
            for item in record.task_effect_ledger
            if item.effect_key == effect_key and item.kind == "provider_intent"
        ),
        None,
    )
    if intent is None:
        raise StaleApproval("Provider effect observation has no persisted intent")
    _append_task_effect(
        record,
        effect_key=effect_key,
        kind="provider_observation",
        phase=phase,
        outcome=outcome,
        assignment=assignment,
        attempt=intent.attempt,
        run_id=run_id,
    )
    retire_terminal_revision_authority(record)


def assignment_is_approved(record: TaskRecord, assignment: Assignment) -> bool:
    return (
        assignment.sequence in record.approved_assignments
        and record.approved_assignment_digests.get(assignment.sequence)
        == assignment_approval_digest(assignment)
    )


def orphaned_provider_intent(record: TaskRecord, assignment: Assignment) -> TaskEffectEvidence | None:
    """Find one exact, unobserved intent; lease/owner checks belong to the engine."""
    digest = assignment_approval_digest(assignment)
    observed = {item.effect_key for item in record.task_effect_ledger if item.kind == "provider_observation"}
    intents = [item for item in record.task_effect_ledger
               if item.kind == "provider_intent" and item.effect_key not in observed
               and item.plan_generation == record.plan_generation
               and item.assignment_sequence == assignment.sequence
               and item.assignment_digest == digest and item.provider == assignment.agent
               and item.phase in {"dispatch", "revision"}]
    if len(intents) != 1:
        return None
    intent = intents[0]
    expected = _canonical_digest({
        "task_id": record.task_id, "plan_generation": record.plan_generation,
        "assignment_digest": digest, "phase": intent.phase, "attempt": intent.attempt,
    })
    return intent if hmac.compare_digest(intent.effect_key, expected) else None


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

    if record.status != TaskStatus.OWNER_DECISION_REQUIRED:
        return None
    if (
        record.intake_authentication != IntakeAuthentication.SLACK_GATEWAY_ATTESTED
        and hmac.compare_digest(record.message or "", PAID_REARM_MESSAGE)
    ):
        return CurrentOwnerDecision(
            kind="paid_rearm",
            digest="sha256:"
            + _canonical_digest(
                {
                    "task_id": record.task_id,
                    "kind": "paid_rearm",
                    "plan_generation": record.plan_generation,
                    "intake_digest": _canonical_digest(
                        record.intake.model_dump(mode="json")
                    ),
                    "plan_digest": (
                        plan_approval_digest(record)
                        if record.plan is not None
                        else None
                    ),
                }
            ),
            assignment_sequence=None,
            clarification=False,
        )
    if record.plan is None:
        return None
    plan_digest = plan_approval_digest(record)
    kind: str
    assignment: Assignment | None = None
    source_digest: str | None = None
    clarification = False

    if record.workload_selection_hold_reasons:
        kind = "workload_selection_clarification"
        source_digest = _canonical_digest(record.workload_selection_hold_reasons)
        clarification = True
    elif record.plan.owner_question:
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
        elif record.oracle_recovery is not None:
            return CurrentOwnerDecision(
                kind="oracle_recovery",
                digest=record.oracle_recovery.decision_digest,
                assignment_sequence=record.oracle_recovery.assignment_sequence,
                clarification=False,
            )
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


def failed_retry_decision_digest(record: TaskRecord) -> str:
    if record.status != TaskStatus.FAILED:
        raise StaleApproval("Task is not failed")
    assignment = pending_assignment(record)
    if assignment is None:
        raise StaleApproval("Failed task has no pending assignment")
    frontier = [
        item.model_dump(mode="json") for item in record.task_effect_ledger[-20:]
    ]
    return "sha256:" + _canonical_digest(
        {
            "task_id": record.task_id,
            "kind": "failed_predispatch_retry",
            "plan_generation": record.plan_generation,
            "assignment_sequence": assignment.sequence,
            "assignment_digest": assignment_approval_digest(assignment),
            "approved_assignment_digest": record.approved_assignment_digests.get(
                assignment.sequence
            ),
            "error_digest": _canonical_digest(record.error or ""),
            "effect_frontier_digest": _canonical_digest(frontier),
        }
    )


def _slack_gateway_decision(record: TaskRecord) -> tuple[str, str]:
    if record.status == TaskStatus.FAILED:
        return "failed_predispatch_retry", failed_retry_decision_digest(record)
    decision = current_owner_decision(record)
    if decision is None:
        raise StaleApproval("Task is not awaiting an owner decision")
    return decision.kind, decision.digest


def _slack_gateway_payload(
    record: TaskRecord,
    approval: OwnerApproval,
    proof: SlackGatewayProof,
    *,
    path: str,
) -> bytes:
    decision_kind, decision_digest = _slack_gateway_decision(record)
    body_digest = _canonical_digest(
        {
            "owner_user_id": approval.owner_user_id,
            "note": approval.note,
            "source": approval.source.value,
            "decision_digest": approval.decision_digest,
            "retry_decision_digest": approval.retry_decision_digest,
            "assignment_sequence": approval.assignment_sequence,
        }
    )
    payload = {
        "version": proof.version,
        "method": "POST",
        "path": path,
        "task_id": record.task_id,
        "task_version": proof.task_version,
        "plan_generation": record.plan_generation,
        "decision_kind": decision_kind,
        "decision_digest": decision_digest,
        "body_digest": body_digest,
        "owner_user_id": approval.owner_user_id,
        "channel_id": proof.channel_id,
        "assignment_sequence": approval.assignment_sequence,
        "issued_at": proof.issued_at,
        "nonce": proof.nonce,
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_slack_gateway_proof(
    secret: str,
    shared_secret: str,
    record: TaskRecord,
    task_version: int,
    approval: OwnerApproval,
    *,
    channel_id: str,
    path: str,
    issued_at: int | None = None,
    nonce: str | None = None,
) -> SlackGatewayProof:
    """Create a short-lived exact-decision proof inside the Slack gateway."""

    if len(secret) < _SLACK_GATEWAY_MIN_SECRET_LENGTH:
        raise StaleApproval("Slack gateway signing secret is not securely configured")
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise StaleApproval("Slack gateway signing secret must be distinct")
    if approval.source not in {
        OwnerApprovalSource.SLACK_BUTTON,
        OwnerApprovalSource.SLACK_COMMAND,
    }:
        raise StaleApproval("Only the Slack gateway may request a Slack proof")
    unsigned = SlackGatewayProof(
        task_version=task_version,
        channel_id=channel_id,
        issued_at=(
            issued_at
            if issued_at is not None
            else int(datetime.now(timezone.utc).timestamp())
        ),
        nonce=nonce or secrets.token_urlsafe(32),
        signature="0" * 64,
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        _slack_gateway_payload(record, approval, unsigned, path=path),
        hashlib.sha256,
    ).hexdigest()
    return unsigned.model_copy(update={"signature": signature})


def verify_slack_gateway_proof(
    secret: str,
    shared_secret: str,
    record: TaskRecord,
    expected_version: int,
    approval: OwnerApproval,
    *,
    channel_id: str,
    path: str,
    allow_paid_runs: bool,
    now: int | None = None,
) -> tuple[str, str, int]:
    """Verify transport provenance and return the bound decision and nonce claim."""

    proof = approval.slack_gateway_proof
    if proof is None or len(secret) < _SLACK_GATEWAY_MIN_SECRET_LENGTH:
        raise StaleApproval("Authenticated Slack gateway proof is required")
    if shared_secret and hmac.compare_digest(secret, shared_secret):
        raise StaleApproval("Slack gateway signing secret must be distinct")
    if approval.source not in {
        OwnerApprovalSource.SLACK_BUTTON,
        OwnerApprovalSource.SLACK_COMMAND,
    }:
        raise StaleApproval("Slack gateway proof cannot authorize an API request")
    if proof.task_version != expected_version:
        raise StaleApproval("Slack gateway proof targets a stale task version")
    if not channel_id or not hmac.compare_digest(proof.channel_id, channel_id):
        raise StaleApproval("Slack gateway proof targets a different channel")
    current_time = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    age = current_time - proof.issued_at
    if age < -_SLACK_GATEWAY_FUTURE_SKEW_SECONDS or age > _SLACK_GATEWAY_PROOF_TTL_SECONDS:
        raise StaleApproval("Slack gateway proof is expired or not yet valid")
    decision_kind, _ = _slack_gateway_decision(record)
    if decision_kind == "paid_rearm" and not allow_paid_runs:
        raise StaleApproval("Paid rearm is disabled while paid execution is off")
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        _slack_gateway_payload(record, approval, proof, path=path),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(proof.signature, expected_signature):
        raise StaleApproval("Slack gateway proof signature is invalid")
    nonce_digest = _canonical_digest(
        {"task_id": record.task_id, "nonce": proof.nonce}
    )
    return (
        decision_kind,
        nonce_digest,
        proof.issued_at + _SLACK_GATEWAY_PROOF_TTL_SECONDS,
    )


def record_oracle_recovery(
    record: TaskRecord,
    run: ProviderRun,
    *,
    kind: str,
    evidence: object,
    artifact_id: str | None = None,
    retryable: bool = False,
    retry_not_before: datetime | None = None,
) -> OracleRecoveryState:
    assignment = pending_assignment(record)
    if assignment is None or run.assignment_sequence != assignment.sequence:
        raise StaleApproval("Oracle recovery does not match the pending assignment")
    prior = record.oracle_recovery
    ordinal = (
        min(3, prior.ordinal + 1)
        if prior is not None and prior.provider_run_id == run.run_id
        else 1
    )
    payload = {
        "task_id": record.task_id,
        "kind": kind,
        "plan_generation": record.plan_generation,
        "assignment_sequence": assignment.sequence,
        "assignment_digest": assignment_approval_digest(assignment),
        "provider_run_id": run.run_id,
        "provider_run_digest": provider_run_digest(run),
        "evidence_digest": _canonical_digest(evidence),
        "artifact_id": artifact_id,
        "retry_not_before": (
            retry_not_before.astimezone(timezone.utc).isoformat()
            if retry_not_before is not None
            else None
        ),
        "ordinal": ordinal,
    }
    state = OracleRecoveryState(
        kind=kind,
        plan_generation=record.plan_generation,
        assignment_sequence=assignment.sequence,
        provider_run_id=run.run_id,
        provider_run_digest=payload["provider_run_digest"],
        evidence_digest=payload["evidence_digest"],
        artifact_id=artifact_id,
        retry_not_before=retry_not_before,
        ordinal=ordinal,
        decision_digest="sha256:" + _canonical_digest(payload),
        retryable=bool(retryable and ordinal < 3),
    )
    record.oracle_recovery = state
    effect_key = _canonical_digest(
        {
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "assignment_digest": assignment_approval_digest(assignment),
            "phase": "oracle_merge",
            "run_id": run.run_id,
            "ordinal": ordinal,
        }
    )
    _append_task_effect(
        record,
        effect_key=effect_key,
        kind="oracle_merge_observation",
        phase="oracle_merge",
        outcome="stopped",
        assignment=assignment,
        attempt=ordinal,
        run_id=run.run_id,
    )
    return state


def oracle_recovery_matches(
    record: TaskRecord, assignment: Assignment, run: ProviderRun
) -> bool:
    state = record.oracle_recovery
    return bool(
        state is not None
        and state.approved
        and state.plan_generation == record.plan_generation
        and state.assignment_sequence == assignment.sequence
        and state.provider_run_id == run.run_id
        and hmac.compare_digest(state.provider_run_digest, provider_run_digest(run))
        and (
            not assignment.requires_approval
            or hmac.compare_digest(
                assignment_approval_digest(assignment),
                record.approved_assignment_digests.get(assignment.sequence, ""),
            )
        )
    )


def record_oracle_merge_success(
    record: TaskRecord,
    run: ProviderRun,
    *,
    artifact_id: str,
    merge_commit_sha: str,
) -> None:
    assignment = pending_assignment(record)
    if assignment is None or run.assignment_sequence != assignment.sequence:
        raise StaleApproval("Oracle merge result does not match the pending assignment")
    effect_key = _canonical_digest(
        {
            "task_id": record.task_id,
            "plan_generation": record.plan_generation,
            "assignment_digest": assignment_approval_digest(assignment),
            "phase": "oracle_merge",
            "run_id": run.run_id,
            "artifact_id": artifact_id,
        }
    )
    _append_task_effect(
        record,
        effect_key=effect_key,
        kind="oracle_merge_observation",
        phase="oracle_merge",
        outcome="merged",
        assignment=assignment,
        attempt=1,
        run_id=merge_commit_sha,
    )
    record.oracle_recovery = None


def apply_oracle_recovery(
    record: TaskRecord,
    approval: OracleRecoveryApproval,
    *,
    oracle_attempt_status: str | None = None,
    oracle_retry_not_before: datetime | None = None,
) -> None:
    state = record.oracle_recovery
    if record.status != TaskStatus.OWNER_DECISION_REQUIRED or state is None:
        raise StaleApproval("Task is not awaiting an Oracle recovery decision")
    if state.approved or not hmac.compare_digest(
        approval.decision_digest, state.decision_digest
    ):
        raise StaleApproval("Oracle recovery binding no longer matches the stopped attempt")
    note = approval.note.strip()
    if not note or note in _SYNTHETIC_APPROVAL_NOTES:
        raise StaleApproval("Oracle recovery requires an explicit owner note")
    if approval.action == "retry_recoverable":
        if not state.retryable:
            raise StaleApproval("This Oracle stop is terminal and cannot be retried")
        if state.artifact_id is not None and oracle_attempt_status != "RECOVERABLE":
            raise StaleApproval(
                "The exact Oracle artifact is no longer recoverable"
            )
        deadline = oracle_retry_not_before or state.retry_not_before
        if deadline is not None and datetime.now(timezone.utc) < deadline:
            raise StaleApproval(
                "The exact Oracle artifact is not eligible for retry before its persisted deadline"
            )
        state.approved = True
        record.status = TaskStatus.QUEUED
        record.error = None
        record.message = note
        return
    if approval.action == "replan_with_remediation":
        replacement_text = record.intake.text + f"\n\nOwner Oracle remediation: {note}"
        if len(replacement_text) > 20_000:
            raise StaleApproval(
                "Oracle remediation would exceed the bounded intake size"
            )
        record.intake = record.intake.model_copy(update={"text": replacement_text})
        reset_for_owner_clarification(record)
        record.status = TaskStatus.QUEUED
        record.message = note
        # Assignment validation is not enabled on nested Pydantic models. Make
        # the persistence boundary explicit so recovery can never store a row
        # that later prevents restart deserialization.
        TaskRecord.model_validate(record.model_dump(mode="python"))
        return
    state.approved = True
    record.status = TaskStatus.FAILED
    record.error = "Owner cancelled the stopped Oracle merge attempt"
    record.message = note


def apply_owner_approval(record: TaskRecord, approval: OwnerApproval) -> None:
    """Record an owner approval, scoped to the assignment it was shown for.

    Raises StaleApproval if the approval names an assignment that is not the one
    now pending -- a Slack approval button persists in the channel, so an old
    button must not authorize work the owner was never shown.
    """
    if approval.retry_decision_digest is not None:
        raise StaleApproval("A failed-retry binding cannot authorize an owner decision")
    if approval.workload_selection_decision_digest is not None:
        raise StaleApproval("A workload clarification binding cannot authorize ordinary approval")
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
            plan_generation=record.plan_generation,
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
    """Clear plan-local projections while retaining immutable task evidence."""

    assignments = {
        item.sequence: item for item in (record.plan.assignments if record.plan else [])
    }
    known_runs = {item.digest for item in record.provider_run_history}
    for run in record.provider_runs:
        history = OpaqueEvidenceDigest.from_value(run)
        if history.digest not in known_runs:
            record.provider_run_history.append(history)
            known_runs.add(history.digest)
        assignment = assignments.get(run.assignment_sequence or -1)
        if assignment is not None and not any(
            item.kind == "provider_observation"
            and item.run_id_digest == opaque_identifier_digest(run.run_id)
            and item.assignment_digest == assignment_approval_digest(assignment)
            for item in record.task_effect_ledger
        ):
            effect_key = _canonical_digest(
                {
                    "task_id": record.task_id,
                    "plan_generation": record.plan_generation,
                    "assignment_digest": assignment_approval_digest(assignment),
                    "phase": "dispatch",
                    "legacy_run_id": run.run_id,
                }
            )
            _append_task_effect(
                record,
                effect_key=effect_key,
                kind="provider_observation",
                phase="dispatch",
                outcome="submitted",
                assignment=assignment,
                attempt=max(1, run.attempt_count),
                run_id=run.run_id,
            )
    known_outcomes = {item.digest for item in record.assignment_outcome_history}
    for outcome in record.assignment_outcomes:
        history = OpaqueEvidenceDigest.from_value(outcome)
        if history.digest not in known_outcomes:
            record.assignment_outcome_history.append(history)
            known_outcomes.add(history.digest)
    known_attempts = {item.digest for item in record.provider_contact_attempt_history}
    for attempt in record.provider_contact_attempts:
        history = OpaqueEvidenceDigest.from_value(attempt)
        if history.digest not in known_attempts:
            record.provider_contact_attempt_history.append(history)
            known_attempts.add(history.digest)

    record.compact_history_projections()

    record.plan = None
    record.next_assignment_index = 0
    record.provider_runs = []
    record.assignment_outcomes = []
    record.review_handoff_digests = {}
    record.consumed_packet_results = {}
    record.revision_authority_by_effect = {}
    record.provider_contact_attempts = []
    record.unavailable_providers = []
    record.deferred_assignments = []
    record.availability_impacts = []
    # The thread identifier is routing metadata, but turn content and delivery
    # errors may contain provider or private prose. Durable Slack effects remain
    # represented by opaque append-only event digests, so raw turn projections
    # are discarded with the answered plan.
    record.collaboration_turns = []
    record.collaboration_delivery_errors = []
    record.collaboration_fallback_active = False
    record.collaboration_fallback_reason = None
    record.debate_tally = None
    record.debate_counter_bias = None
    record.debate_counter_bias_interaction_id = None
    record.debate_synthesis = None
    record.debate_synthesis_interaction_id = None
    record.debate_tie_judgment = None
    record.debate_tie_judgment_interaction_id = None
    record.plan_interaction_id = None
    record.workload_selection_hold_reasons = []
    record.review_interaction_id = None
    record.review = None
    record.oracle_recovery = None
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
    if approval.retry_decision_digest is not None:
        raise StaleApproval("A failed-retry binding cannot answer a clarification")
    if clarification_source(record) is None:
        raise StaleApproval("Task is not awaiting a written owner clarification")
    if approval.source == OwnerApprovalSource.SLACK_BUTTON:
        raise StaleApproval("Slack approval buttons cannot provide written clarification")
    if approval.assignment_sequence is not None:
        raise StaleApproval(
            "A written clarification cannot be bound to an assignment approval"
        )
    decision = current_owner_decision(record)
    if decision is not None and decision.kind == "workload_selection_clarification":
        # Slack commands bind this decision through their verified gateway proof;
        # direct API clarification must supply the exact decision digest.
        if (approval.source == OwnerApprovalSource.API or approval.workload_selection_decision_digest is not None) and (
            approval.workload_selection_decision_digest is None
            or not hmac.compare_digest(approval.workload_selection_decision_digest, decision.digest)
        ):
            raise StaleApproval("Workload clarification no longer matches the exact pending hold")
    elif approval.workload_selection_decision_digest is not None:
        raise StaleApproval("A workload binding cannot answer a different clarification")
    note = approval.note.strip()
    if not note or note in _SYNTHETIC_APPROVAL_NOTES:
        raise StaleApproval("This task needs a written clarification, not a bare approval")
    clarified_text = record.intake.text + f"\n\nOwner clarification: {note}"
    if len(clarified_text) > MAX_SLACK_INTAKE_TEXT:
        raise StaleApproval(
            f"Owner clarification would exceed the {MAX_SLACK_INTAKE_TEXT}-character "
            "task intake limit"
        )
    record.intake.text = clarified_text
    reset_for_owner_clarification(record)


def apply_failed_predispatch_retry(record: TaskRecord, approval: OwnerApproval) -> None:
    """Retry a failed assignment only when no provider run was recorded."""
    if record.status != TaskStatus.FAILED:
        raise StaleApproval("Task is not failed")
    pending = pending_assignment(record)
    if pending is None or approval.assignment_sequence != pending.sequence:
        raise StaleApproval("A failed retry must name the exact pending assignment")
    if approval.retry_decision_digest is None or not hmac.compare_digest(
        approval.retry_decision_digest, failed_retry_decision_digest(record)
    ):
        raise StaleApproval("Failed retry binding no longer matches the persisted task state")
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
# [[Codex | GPT-5.6 | 2026-08-24 | 15:21 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:06 UTC | END]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:47 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:24 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 16:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:28 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:54 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 01:00 UTC | END]]

# [[Cursor | Composer 2.5 | 2026-09-21 | 20:19 UTC | END]]

# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:35 UTC | END]]
