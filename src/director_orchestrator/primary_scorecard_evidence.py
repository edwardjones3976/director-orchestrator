# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 18:59 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:16 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 05:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:16 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:46 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:37 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:28 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:46 UTC | BEGIN]]
"""Read-only, generation-bound evidence reports for the completion scorecard.

Invoked only through ``python -m director_orchestrator.primary_generation
consistency|activation-captures`` inside the running image, after the
generation manifest and bindings are verified. Every read happens in a read-only
transaction or through a load-only store; nothing is written, dispatched or
signed, and the output carries counts, digests and expiry, never secrets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import re
import time
from types import SimpleNamespace

_PIN = "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"
_ADOPTION_FIELDS = ("repository", "pull_request_number", "pull_request_author", "independent_reviewer",
                    "security_reviewer", "reviewed_source_commit", "reviewed_source_tree", "adoption_digest")
_COUNT_KEYS = ("graph_task_count", "unresolved_reconciliation", "ambiguous_assignments",
               "succeeded_projections_with_unsucceeded_assignments", "succeeded_projections_without_plan",
               "projections_missing_batch",
               "batches_without_projection",
               "tasks_requiring_reconciliation", "terminal_tasks_open_assignments", "terminal_tasks_open_effects",
               "terminal_batches_unbound", "projection_batch_digest_conflicts", "projections_ahead_of_fence",
               "open_effect_reservations", "active_assignments", "live_writer_claims",
               "batches_pending_projection_binding", "pending_owner_decisions", "nonterminal_projections")

# A task is settled when terminal, unless it is an OWNER_DECISION_REQUIRED hold
# with a pending approval (the predicate Slack delivery enqueues approvals by):
# that hold is resumable and counts as in flight. A PENDING assignment behind a
# FAILED or owner-held prerequisite is the aggregator's BLOCKED projection
# (primary_parallel), stored as PENDING; it is neither open nor active. The
# resumable hold itself counts as pending_owner_decisions, so a held task with
# no open assignment, effect or lease is still in flight.
_COUNTS = """WITH RECURSIVE a AS (SELECT fencing_generation FROM {s}.director_primary_intake_authority WHERE singleton),
 p AS (SELECT task_id, intake_fencing_generation, projection->>'terminal_classification' tc,
              (projection->>'terminal_classification' IS NOT NULL
               AND (projection->>'terminal_classification' <> 'OWNER_DECISION_REQUIRED'
                    OR projection->'pending_approval'->>'request_digest' IS NULL)) settled,
              projection->>'parallel_plan_digest' pd FROM {s}.director_primary_task_projection),
 blocked(task_id, assignment_id) AS (
   SELECT x.task_id, x.assignment_id FROM {s}.director_primary_parallel_assignment x
     JOIN {s}.director_primary_parallel_assignment d ON d.task_id=x.task_id AND x.dependencies ? d.assignment_id
     WHERE x.status='PENDING' AND d.status IN ('FAILED','OWNER_DECISION_REQUIRED')
   UNION
   SELECT x.task_id, x.assignment_id FROM {s}.director_primary_parallel_assignment x
     JOIN blocked b ON b.task_id=x.task_id AND x.dependencies ? b.assignment_id
     WHERE x.status='PENDING')
SELECT now() AS captured_at, (SELECT fencing_generation FROM a) AS intake_fencing_generation,
 (SELECT count(*) FROM p) AS graph_task_count,
 (SELECT count(DISTINCT r.claim_key) FROM {s}.director_primary_reconciliation_event r
    LEFT JOIN {s}.director_primary_terminal_evidence t ON t.claim_key=r.claim_key AND t.task_id=r.task_id
    WHERE t.claim_key IS NULL) AS unresolved_reconciliation,
 (SELECT count(*) FROM {s}.director_primary_parallel_assignment WHERE status='AMBIGUOUS') AS ambiguous_assignments,
 (SELECT count(*) FROM p WHERE tc='RECONCILIATION_REQUIRED') AS tasks_requiring_reconciliation,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_assignment x USING (task_id)
    WHERE p.settled AND x.status IN ('PENDING','ACTIVE','AMBIGUOUS') AND NOT EXISTS
      (SELECT 1 FROM blocked b WHERE b.task_id=x.task_id AND b.assignment_id=x.assignment_id)) AS terminal_tasks_open_assignments,
 (SELECT count(*) FROM {s}.director_primary_effect_reservation e JOIN p USING (task_id)
    LEFT JOIN {s}.director_primary_terminal_evidence t ON t.claim_key=e.claim_key AND t.task_id=e.task_id
    WHERE t.claim_key IS NULL AND p.settled) AS terminal_tasks_open_effects,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_batch b USING (task_id)
    WHERE p.pd IS NULL AND p.settled) AS terminal_batches_unbound,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_batch b USING (task_id)
    WHERE p.pd IS NOT NULL AND p.pd<>b.plan_digest) AS projection_batch_digest_conflicts,
 (SELECT count(*) FROM p WHERE p.intake_fencing_generation>(SELECT fencing_generation FROM a)) AS projections_ahead_of_fence,
 (SELECT count(*) FROM {s}.director_primary_effect_reservation e JOIN p USING (task_id)
    LEFT JOIN {s}.director_primary_terminal_evidence t ON t.claim_key=e.claim_key AND t.task_id=e.task_id
    WHERE t.claim_key IS NULL AND NOT p.settled) AS open_effect_reservations,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_assignment x USING (task_id)
    WHERE NOT p.settled AND x.status IN ('PENDING','ACTIVE') AND NOT EXISTS
      (SELECT 1 FROM blocked b WHERE b.task_id=x.task_id AND b.assignment_id=x.assignment_id)) AS active_assignments,
 (SELECT count(*) FROM {s}.director_primary_task_writer WHERE lease_expires_at>now()) AS live_writer_claims,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_batch b USING (task_id)
    WHERE p.pd IS NULL AND NOT p.settled) AS batches_pending_projection_binding,
 (SELECT count(*) FROM p WHERE NOT p.settled AND p.tc='OWNER_DECISION_REQUIRED') AS pending_owner_decisions,
 -- Every projection not yet in a settled terminal classification is in flight in itself, with or without
 -- subordinate rows (admitted before its batch exists, or assignments settled before its terminal projection).
 (SELECT count(*) FROM p WHERE NOT p.settled) AS nonterminal_projections,
 (SELECT count(*) FROM p WHERE p.tc='SUCCEEDED' AND EXISTS (SELECT 1 FROM {s}.director_primary_parallel_assignment x
    WHERE x.task_id=p.task_id AND x.status<>'SUCCEEDED')) AS succeeded_projections_with_unsucceeded_assignments,
 -- Every valid DirectorPlan has at least one assignment: a SUCCEEDED projection without a batch bound to its
 -- plan digest, or without a SUCCEEDED assignment, is an impossible success.
 (SELECT count(*) FROM p WHERE p.tc='SUCCEEDED' AND (p.pd IS NULL
    OR NOT EXISTS (SELECT 1 FROM {s}.director_primary_parallel_batch b WHERE b.task_id=p.task_id AND b.plan_digest=p.pd)
    OR NOT EXISTS (SELECT 1 FROM {s}.director_primary_parallel_assignment x
                   WHERE x.task_id=p.task_id AND x.status='SUCCEEDED'))) AS succeeded_projections_without_plan,
 (SELECT count(*) FROM p WHERE p.pd IS NOT NULL AND NOT EXISTS
    (SELECT 1 FROM {s}.director_primary_parallel_batch b WHERE b.task_id=p.task_id)) AS projections_missing_batch,
 (SELECT count(*) FROM {s}.director_primary_parallel_batch b WHERE NOT EXISTS
    (SELECT 1 FROM p WHERE p.task_id=b.task_id)) AS batches_without_projection"""

_SPEND = """SELECT s.task_id,s.attempt_digest,s.grant_digest,s.provider_id,s.project_id,s.budget_day::text AS budget_day,
 s.reserved_microusd,u.actual_microusd,u.observed_tokens,u.document AS usage_document,
 coalesce(length(u.document->'metering_evidence'->>'request_id'),0)>0
 AND coalesce(length(u.document->'metering_evidence'->>'response_id'),0)>0
 AND u.document->'metering_evidence'->>'cost_basis'='signed-tariff-metered-not-invoice' AS request_correlated
FROM {s}.director_primary_provider_spend s LEFT JOIN {s}.director_primary_provider_usage u USING (attempt_digest)
WHERE s.task_id = ANY(%(tasks)s) ORDER BY s.task_id,s.attempt_digest LIMIT 600"""

_BUDGETS = """SELECT b.document AS document, b.authority_digest, b.document->>'task_id' AS task_id, b.document->>'project_id' AS project_id,
 b.document->>'day' AS day, (b.document->>'daily_limit_microusd')::bigint AS daily_limit_microusd,
 (SELECT coalesce(sum(GREATEST(s2.reserved_microusd,coalesce(u2.actual_microusd,0))),0)
    FROM {s}.director_primary_provider_spend s2 LEFT JOIN {s}.director_primary_provider_usage u2 USING (attempt_digest)
    WHERE s2.project_id=b.document->>'project_id' AND s2.budget_day::text=b.document->>'day') AS day_held_microusd
FROM {s}.director_primary_provider_budget b WHERE b.document->>'task_id' = ANY(%(tasks)s) LIMIT 100"""

# Check 08 re-reads the approval command receipts; the command ID leaves only as a digest.
# Each receipt is joined to the durable command authorization the webhook-verification path admitted:
# its command digest, proof version, actor and writer fence (the actor is compared by the scorecard).
_RECEIPTS = """SELECT r.task_id, encode(sha256(convert_to(r.command_id,'UTF8')),'hex') AS command_id_sha256,
 r.command_digest, r.resulting_projection_digest, r.recorded_at,
 a.nonce_digest IS NOT NULL AS authorized, a.command_digest = r.command_digest AS authorized_command_digest,
 a.writer_fencing_generation = r.writer_fencing_generation AS authorized_fence, a.proof_version, a.actor_id,
 a.claimed_at AS authorization_claimed_at, a.proof_expires_at AS authorization_proof_expires_at
FROM {s}.director_primary_command_receipt r LEFT JOIN {s}.director_primary_command_authorization a
 ON a.task_id = r.task_id AND a.operation = r.operation AND a.command_id = r.command_id
WHERE r.task_id = ANY(%(tasks)s) AND r.operation='approval_resume' ORDER BY r.task_id, r.recorded_at LIMIT 100"""


_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
ATTEMPT_STATUSES = ("STARTED", "SUCCEEDED", "FAILED", "AMBIGUOUS")


def _is_id(value):
    return isinstance(value, str) and bool(_IDENTITY.fullmatch(value))


def _is_count(value):
    return type(value) is int and value >= 0


def _when(value):
    """Timezone-aware datetime from an ISO string, or None when unusable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# The fields each producer digests into a settling event's outcome_evidence_digest (primary_parallel): event 2 by
# PrimaryParallel.record_provider_attempt, event 3 (an authenticated callback settlement) by its callback settle.
_OUTCOME_FIELDS = {
    2: ("attempt_digest", "assignment_id", "attempt_ordinal", "provider_id", "format_id", "transport_id",
        "idempotency_key_digest", "budget_evidence_digest", "status", "result_digest", "reason_code"),
    3: ("assignment_id", "attempt_ordinal", "provider_id", "attempt_digest", "provider_run_id_digest", "status",
        "result_digest", "reason_code")}


def outcome_evidence_digest(task_id, event):
    """The digest the producer stores for a settling event, recomputed from the event's own columns (the same
    fields, the same canonical JSON, primary_parallel._digest); None for an event no producer digests."""
    from .primary_parallel import _digest
    fields = _OUTCOME_FIELDS.get(event.get("event_sequence"))
    return None if fields is None else _digest({key: event.get(key) for key in fields} | {"task_id": task_id})


def validate_attempt_history(assignments, attempts, *, task_id=None):
    """Bind every attempt event to its assignment/provider and its immutable attempt.

    Returns (latest_valid_event_per_attempt, error). Events of one attempt
    (assignment, ordinal) must share digest, idempotency/budget identity,
    provider, format and transport; event sequences are unique and within the
    producer's 1..3 contract; a digest never names two attempts. The latest
    valid event settles the attempt, so an authenticated later SUCCEEDED
    callback (event 3) supersedes an earlier AMBIGUOUS event 2. Each
    assignment's attempt ordinals run 1..n without a gap (the roster numbers
    them from 1), and event times never decrease within an attempt.

    The one rule for both readers: the collector applies it to a selected
    task's typed readback, and the in-image consistency command to every task's
    rows (``provider_attempts_invalid``). Times are ISO strings.
    """
    by_assignment = {}
    for row in assignments:
        if (not _is_id(row.get("assignment_id")) or not _is_count(row.get("sequence")) or row["sequence"] < 1
                or not _is_id(row.get("provider_id")) or row["assignment_id"] in by_assignment
                or row.get("status") not in ("PENDING", "ACTIVE", "AMBIGUOUS", "SUCCEEDED", "FAILED",
                                             "OWNER_DECISION_REQUIRED")):
            return None, "malformed or duplicate assignment row"
        for field in ("reservation_digest", "result_digest"):
            if row.get(field) is not None and not _DIGEST.fullmatch(str(row[field])):
                return None, "malformed assignment digest"
        by_assignment[row["assignment_id"]] = row
    grouped = {}
    for row in attempts:
        key = (row.get("assignment_id"), row.get("attempt_ordinal"))
        assignment = by_assignment.get(row.get("assignment_id"))
        if assignment is None:
            return None, "attempt does not join a listed assignment"
        if (not _is_count(row.get("attempt_ordinal")) or not 1 <= row["attempt_ordinal"] <= 3
                or not _is_count(row.get("event_sequence")) or not 1 <= row["event_sequence"] <= 3
                or not _is_count(row.get("assignment_sequence"))
                or row.get("status") not in ATTEMPT_STATUSES
                or not _DIGEST.fullmatch(str(row.get("attempt_digest", "")))
                or not _DIGEST.fullmatch(str(row.get("idempotency_key_digest", "")))
                or not _DIGEST.fullmatch(str(row.get("budget_evidence_digest", "")))
                or not _is_id(row.get("provider_id")) or not _is_id(row.get("format_id"))
                or not _is_id(row.get("transport_id")) or not _is_id(row.get("reason_code"))
                or _when(row.get("recorded_at")) is None):
            return None, "malformed attempt event"
        if row["provider_id"] != assignment["provider_id"] or row["assignment_sequence"] != assignment["sequence"]:
            return None, "attempt provider/sequence disagrees with its assignment"
        for field in ("result_digest", "outcome_evidence_digest", "provider_run_id_digest"):
            if row.get(field) is not None and not _DIGEST.fullmatch(str(row[field])):
                return None, "malformed attempt digest"
        # Event positions carry meaning: event 1 is the STARTED record; later
        # events are settlements or ambiguity, and a settlement carries its
        # outcome evidence (and, when SUCCEEDED, a result digest).
        if row["event_sequence"] == 1 and row["status"] != "STARTED":
            return None, "event 1 is not the STARTED record"
        if row["event_sequence"] > 1 and row["status"] == "STARTED":
            return None, "a later event repeats STARTED"
        if row["status"] in ("SUCCEEDED", "FAILED") and row.get("outcome_evidence_digest") is None:
            return None, "terminal event lacks outcome evidence"
        if row["status"] == "SUCCEEDED" and row.get("result_digest") is None:
            return None, "SUCCEEDED event lacks a result digest"
        grouped.setdefault(key, []).append(row)
    digests = {}
    latest = {}
    for key, events in grouped.items():
        identity = {(e["attempt_digest"], e["idempotency_key_digest"], e["budget_evidence_digest"],
                     e["provider_id"], e["format_id"], e["transport_id"]) for e in events}
        sequences = [e["event_sequence"] for e in events]
        if len(identity) != 1 or len(set(sequences)) != len(sequences):
            return None, "attempt identity changed across its events or duplicate events"
        digest_value = events[0]["attempt_digest"]
        if digests.setdefault(digest_value, key) != key:
            return None, "attempt digest reused by another attempt"
        ordered = sorted(events, key=lambda e: e["event_sequence"])
        if ordered[0]["event_sequence"] != 1:
            return None, "attempt history lacks its STARTED event 1"
        # Event 3 (authenticated callback settlement) may legitimately follow
        # event 1 directly; event 2 is not fabricated to satisfy this check.
        if any(_when(a["recorded_at"]) > _when(b["recorded_at"]) for a, b in zip(ordered, ordered[1:])):
            return None, "attempt events are not recorded in sequence order"
        latest[key] = ordered[-1]
    # A settling event's outcome evidence digest covers the event itself: recomputed, never trusted (after the
    # structural checks, so a malformed history reports its own defect first).
    if task_id is not None and any(
            event["status"] != "STARTED" and event.get("outcome_evidence_digest") is not None
            and event["outcome_evidence_digest"] != outcome_evidence_digest(task_id, event)
            for events in grouped.values() for event in events):
        return None, "an attempt event's outcome evidence digest does not match the event"
    ordinals = {}
    for assignment_id, ordinal in grouped:
        ordinals.setdefault(assignment_id, set()).add(ordinal)
    if any(values != set(range(1, len(values) + 1)) for values in ordinals.values()):
        return None, "an assignment's attempt ordinals do not run 1..n"
    return latest, None


_SLACK_TS = re.compile(r"\d{10,}\.\d{1,6}\Z")
DELIVERY_STATES = ("PENDING", "POSTING", "ACKNOWLEDGED", "DELIVERED", "AMBIGUOUS", "REFUSED", "SUPPRESSED", "SUPERSEDED")


def validate_effects(effects):
    keys = set()
    for row in effects:
        if (not _DIGEST.fullmatch(str(row.get("reservation_key", ""))) or not _DIGEST.fullmatch(str(row.get("claim_key", "")))
                or not _DIGEST.fullmatch(str(row.get("effect_key_digest", "")))
                or not _DIGEST.fullmatch(str(row.get("effect_attempt_digest", "")))
                or not _is_id(row.get("writer_owner_id")) or not _is_count(row.get("fencing_generation"))
                or not _is_count(row.get("reserved_units")) or _when(row.get("reserved_at")) is None):
            return "malformed external-effect reservation identity"
        if row["reservation_key"] in keys:
            return "duplicate external-effect reservation"
        keys.add(row["reservation_key"])
        if (row.get("remote_identifier") is None) != (row.get("remote_system") is None):
            return "remote binding lacks its system or identifier"
        if row.get("remote_identifier") is not None and (not _is_id(row["remote_system"])
                                                          or not isinstance(row["remote_identifier"], str)
                                                          or not row["remote_identifier"].strip()
                                                          or _when(row.get("bound_at")) is None):
            return "malformed remote binding"
        if row.get("terminal_disposition") is not None and (
                not _is_id(row["terminal_disposition"]) or not _DIGEST.fullmatch(str(row.get("terminal_evidence_digest", "")))
                or _when(row.get("terminal_recorded_at")) is None):
            return "terminal evidence lacks its digest or time"
        for field in ("execution_started_at", "bound_at", "terminal_recorded_at"):
            if row.get(field) is not None and _when(row[field]) is None:
                return "malformed external-effect timestamp"
    return None


# Notification-key families exactly as the delivery producer builds them
# (primary_slack_delivery.enqueue): the fixed terminal key, hold:<state digest>
# and approval:<request digest>:<projection digest>, each digest being one
# sha256-prefixed value. Generic identity bounds are not widened for these.
NOTIFICATION_KEY_FAMILIES = (
    ("terminal", re.compile(r"terminal\Z")),
    ("hold", re.compile(r"hold:sha256:[a-f0-9]{64}\Z")),
    ("approval", re.compile(r"approval:sha256:[a-f0-9]{64}:sha256:[a-f0-9]{64}\Z")),
)


def notification_key_family(value):
    """Return the producer family of a notification key, or None when malformed."""
    if not isinstance(value, str):
        return None
    for family, pattern in NOTIFICATION_KEY_FAMILIES:
        if pattern.fullmatch(value):
            return family
    return None


def validate_deliveries_and_receipts(deliveries, receipts):
    if len({r.get("client_msg_id") for r in deliveries}) != len(deliveries):
        return "duplicate Slack delivery identity"
    if len({r.get("notification_key") for r in deliveries}) != len(deliveries):
        return "duplicate Slack notification key"
    for row in deliveries:
        if (not _is_id(row.get("client_msg_id")) or notification_key_family(row.get("notification_key")) is None
                or row.get("state") not in DELIVERY_STATES or not _is_count(row.get("readback_attempts"))
                or _when(row.get("created_at")) is None):
            return "malformed Slack delivery row"
        if row.get("owner_decision") not in (None, "approve", "reject"):
            return "malformed Slack delivery row"
        if (row.get("owner_decision") is not None) and notification_key_family(row["notification_key"]) != "approval":
            return "owner decision recorded on a non-approval notification"
        for field in ("claimed_at", "acknowledged_at", "delivered_at", "owner_decision_at"):
            if row.get(field) is not None and _when(row[field]) is None:
                return "malformed Slack delivery timestamp"
        if row.get("slack_ts") is not None and not _SLACK_TS.fullmatch(str(row["slack_ts"])):
            return "malformed Slack delivery timestamp"
    if len({r.get("receipt_id") for r in receipts}) != len(receipts):
        return "duplicate status receipt identity"
    for row in receipts:
        if not _is_id(row.get("receipt_id")) or not isinstance(row.get("source_event_id"), str) \
                or not row["source_event_id"] or _when(row.get("created_at")) is None:
            return "malformed status receipt row"
    return None


def validate_usage(usage_rows, latest, *, absent=False):
    """Spend and usage against the attempts, the way PrimaryProviderBudgetAuthority.reserve holds the ledger.

    ``usage_rows`` are the task's spend reservations, each outer-joined to its usage row (actual_microusd and
    observed_tokens are None until usage is recorded). Returns (error, state): error is malformed or inconsistent
    evidence (a duplicated, malformed or provider-mismatched row, or an attempt with no spend reservation at all,
    reserve's own refusal "prior provider attempt lacks authoritative monetary accounting"); state is (kind, reason)
    for well-formed rows that are not settled: a conflict when observed tokens exceed the reservation's token limit
    (reserve's other refusal) or usage is recorded for no recorded attempt (an orphan), in flight when a reservation's
    attempt was never recorded (reserve ran, the attempt did not start) or a settled attempt's usage is not recorded
    yet. Usage can never exist without its spend row (director_primary_provider_usage references it). ``absent``: the spend and usage tables do not exist here, so there is no ledger to hold."""
    if absent:
        return None, None
    seen, state = set(), None
    for row in usage_rows:
        digest_value = row.get("attempt_digest")
        owner = next((e for e in latest.values() if e["attempt_digest"] == digest_value), None)
        measured = (row.get("actual_microusd"), row.get("observed_tokens"))
        if (digest_value in seen or not all(_is_count(row.get(k)) for k in ("reserved_microusd", "token_limit"))
                or measured != (None, None) and not all(_is_count(value) for value in measured)
                or owner is not None and row.get("provider_id") != owner["provider_id"]):
            return "a spend or usage row is duplicated, malformed or provider-mismatched", None
        seen.add(digest_value)
        if measured[1] is not None and measured[1] > row["token_limit"]:
            state = ("conflict", "observed provider tokens exceed the reserved token limit")
        elif owner is None and measured != (None, None):
            # Usage is recorded only for an attempt that ran: usage whose attempt was never recorded is an orphan.
            state = ("conflict", "usage is recorded for no recorded attempt")
        elif state is None and owner is None:
            state = ("in_flight", "a spend reservation has no recorded attempt")
        elif state is None and owner.get("status") in ("SUCCEEDED", "FAILED") and measured == (None, None):
            state = ("in_flight", "a settled attempt's usage is not recorded")
    if any(event["attempt_digest"] not in seen for event in latest.values()):
        return "a provider attempt has no spend reservation", None
    return None, state


def delivery_state(deliveries):
    """Each Slack delivery's state, as PrimarySlackDelivery moves it. Returns None or (kind, reason), a conflict
    outranking in flight.
    DELIVERED (its lifecycle checked by lifecycle_error), REFUSED (Slack definitely did not post; never reposted),
    SUPPRESSED and SUPERSEDED are settled. PENDING and POSTING are in flight (a POSTING claim older than 60 s becomes
    AMBIGUOUS). ACKNOWLEDGED and AMBIGUOUS are in flight while readback may still settle them (fewer than two readback
    attempts); after two, nothing ever reads them again, so they are conflicts."""
    found = None
    for row in deliveries:
        state, reads = row.get("state"), row.get("readback_attempts")
        if state in ("DELIVERED", "REFUSED", "SUPPRESSED", "SUPERSEDED"):
            continue
        if state in ("PENDING", "POSTING") or (state in ("ACKNOWLEDGED", "AMBIGUOUS") and _is_count(reads) and reads < 2):
            found = found or ("in_flight", f"a Slack delivery is still {state}")
        else:
            return ("conflict", f"a Slack delivery is {state} and will never be read back again")
    return found


def ordered(row, fields, required):
    """The one lifecycle rule for every row type: each required moment (one the product writes for this row's type
    and state) is present, and every present moment in `fields` is parseable and chronological. Only moments the
    product may legitimately leave unset (acknowledged_at on the ambiguous path, bound_at) are optional."""
    if any(not row.get(key) for key in required):
        return False
    times = [_when(row[key]) for key in fields if row.get(key)]
    if None in times:
        return False
    return all(earlier <= later for earlier, later in zip(times, times[1:]))


def chain(*moments):
    """Every moment present and chronological (attempt events, an assignment around its attempts)."""
    keys = [str(index) for index in range(len(moments))]
    return ordered(dict(zip(keys, moments)), keys, keys)


DELIVERY_FIELDS = ("created_at", "claimed_at", "posted_at", "acknowledged_at", "delivered_at")
EFFECT_FIELDS = ("reserved_at", "execution_started_at", "bound_at", "terminal_recorded_at")
ASSIGNMENT_FIELDS = ("reserved_at", "completed_at")


def required_moments(kind, row):
    """The moments the product always writes for a row of this type in this state."""
    if kind == "delivery":  # DELIVERED, the only state credited (PrimarySlackDelivery claim, post, readback)
        return ("created_at", "claimed_at", "posted_at", "delivered_at")
    if kind == "effect":  # reservation, then execution claim before any I/O, then terminal evidence
        if row.get("terminal_disposition"):
            return ("reserved_at", "execution_started_at", "terminal_recorded_at")
        return ("reserved_at", "execution_started_at") if row.get("execution_started_at") else ("reserved_at",)
    status = row.get("status")  # assignment: primary_parallel reserve (ACTIVE) and settlement writes
    if status in ("SUCCEEDED", "FAILED"):
        # A PENDING assignment cancelled before dispatch is FAILED with completed_at but never reserved.
        cancelled = status == "FAILED" and row.get("reason_code") == "task_cancelled_before_dispatch"
        return ("completed_at",) if cancelled else ("reserved_at", "completed_at")
    return ("reserved_at",) if status in ("ACTIVE", "AMBIGUOUS", "OWNER_DECISION_REQUIRED") else ()


def delivery_settled(row):
    """A DELIVERED row as PrimarySlackDelivery produces one: created (PENDING), claimed (POSTING), acknowledged when
    Slack answered the post (null on the AMBIGUOUS recovery path), then DELIVERED only by readback, which counts one
    or two attempts first; every present moment in that order."""
    attempts = row.get("readback_attempts")
    posted = row.get("slack_ts")
    if not (isinstance(posted, str) and _SLACK_TS.fullmatch(posted)):
        return False
    # Slack stamps the message between the claim (claimed_at=now() before chat.postMessage) and the
    # acknowledgement (settled with the returned ts), or, on the ambiguous path, before the readback finds it.
    posted_at = datetime.fromtimestamp(float(Decimal(posted)), timezone.utc).isoformat()
    lifecycle = {**row, "posted_at": posted_at}
    return (row.get("state") == "DELIVERED" and type(attempts) is int and 1 <= attempts <= 2
            and ordered(lifecycle, DELIVERY_FIELDS, required_moments("delivery", lifecycle)))


def lifecycle_error(sections):
    """The one ordering rule over a task's rows: each attempt's events in sequence order and chronological; every
    attempt event inside its assignment (primary_parallel reserves, the roster records STARTED then the terminal
    event, primary_parallel settles); every assignment, effect and DELIVERED delivery carrying the moments the
    product writes for its state, in order. None, or the first violation."""
    events = sections.get("provider_attempts") or []
    chains = {}
    for event in sorted(events, key=lambda event: event.get("event_sequence") or 0):
        chains.setdefault((event.get("assignment_id"), event.get("attempt_ordinal")), []).append(event.get("recorded_at"))
    for assignment in sections.get("assignments") or []:
        own = sorted((event for event in events if event.get("assignment_id") == assignment.get("assignment_id")),
                     key=lambda event: (event.get("attempt_ordinal") or 0, event.get("event_sequence") or 0))
        if own:
            chains[("assignment", assignment.get("assignment_id"))] = [
                assignment.get("reserved_at"), *(event.get("recorded_at") for event in own)] + (
                [assignment.get("completed_at")] if assignment.get("status") in ("SUCCEEDED", "FAILED") else [])
    if not all(chain(*moments) for moments in chains.values()):
        return "attempt events are out of order or outside their assignment's reservation and completion"
    if not all(ordered(row, ASSIGNMENT_FIELDS, required_moments("assignment", row))
               for row in sections.get("assignments") or []):
        return "an assignment lacks a moment its state requires, or its moments are out of order"
    if not all(ordered(row, EFFECT_FIELDS, required_moments("effect", row)) for row in sections.get("effects") or []):
        return "an external effect lacks a moment its state requires, or its moments are out of order"
    if not all(delivery_settled(row) for row in sections.get("slack_deliveries") or [] if row.get("state") == "DELIVERED"):
        return "a DELIVERED Slack delivery is not a settled delivery lifecycle"
    return None


# The settlement matrix, from the producers: primary_parallel reserves (PENDING -> ACTIVE) before any attempt and
# settles from the roster's outcome (primary_default._execute_three_attempt_roster), and records a hold or a
# dispatcher exception without one. Per assignment status: the final event its last attempt may have ("none": no
# attempt at all), and its result digest rule.
#   PENDING                  none                               no digest (nothing dispatched)
#   ACTIVE                   none, STARTED, SUCCEEDED, FAILED,  no digest (still settling; a later attempt may
#                            AMBIGUOUS                          follow a transient FAILED one)
#   AMBIGUOUS                none or any                        no digest (an unknown outcome carries none)
#   OWNER_DECISION_REQUIRED  none, STARTED, FAILED              no digest (a workload hold raised before or
#                                                               inside an attempt leaves its STARTED row)
#   FAILED                   none, FAILED                       none, or a digest one of its FAILED attempts ended
#                                                               with (the roster returns the last result document's
#                                                               digest, which a later transient failure need not carry)
#   SUCCEEDED                SUCCEEDED (none only offline)      the last attempt's result digest (offline: present)
_SETTLEMENT_FINALS = {
    "PENDING": {None}, "ACTIVE": {None, "STARTED", "SUCCEEDED", "FAILED", "AMBIGUOUS"},
    "AMBIGUOUS": {None, "STARTED", "SUCCEEDED", "FAILED", "AMBIGUOUS"},
    "OWNER_DECISION_REQUIRED": {None, "STARTED", "FAILED"}, "FAILED": {None, "FAILED"}, "SUCCEEDED": {"SUCCEEDED"}}


def settlement_error(sections, latest):
    """Each assignment's status, its last attempt's final event (``latest``, from validate_attempt_history) and its
    result digest fit the settlement matrix above. A SUCCEEDED assignment with no attempt at all is an offline
    double's (execution mode offline_simulation or paid_off), which records no provider attempt but a result digest.
    None, or the first disagreement."""
    offline = sections.get("execution_mode") in ("offline_simulation", "paid_off")
    for assignment in sections.get("assignments") or []:
        own = [event for (assignment_id, _), event in latest.items() if assignment_id == assignment.get("assignment_id")]
        final = max(own, key=lambda event: event.get("attempt_ordinal") or 0) if own else None
        status, digest = assignment.get("status"), assignment.get("result_digest")
        allowed = _SETTLEMENT_FINALS.get(status)
        if status == "SUCCEEDED" and final is None and offline:
            if not digest:
                return "an offline SUCCEEDED assignment has no result digest"
            continue
        if allowed is None or (final.get("status") if final else None) not in allowed:
            return f"a {status} assignment's last attempt ended {final.get('status') if final else 'without one'}"
        if status == "SUCCEEDED":
            if not digest or final.get("result_digest") != digest:
                return "a SUCCEEDED assignment's result digest is not its last attempt's"
        elif status == "FAILED":
            if digest is not None and digest not in {event.get("result_digest") for event in own
                                                     if event.get("status") == "FAILED"}:
                return "a FAILED assignment names a result digest none of its failed attempts ended with"
        elif digest is not None:
            return f"a {status} assignment carries a result digest"
    return None


EVIDENCE_CATEGORIES = ("attempts", "usage", "effects", "deliveries", "lifecycle", "settlement")


def task_evidence_errors(sections):
    """The one per-task evidence validation, for the offline collector (a selected task's typed readback) and the
    in-image consistency command (every task's rows, the same columns and joins through qualification_section_sql).

    ``sections``: assignments, provider_attempts, effects, slack_deliveries, status_receipts, provider_usage (spend
    outer-joined to usage), times as ISO strings, execution_mode and ``absent`` (the readback's absent sections).
    Returns (latest valid event per attempt, errors, states). errors: {category: the first malformed or inconsistent
    evidence, or None}; the collector refuses a readback with any. states: {category: None or (kind, reason)} for
    well-formed rows that have not settled, kind "conflict" (a delivery no readback will ever settle, tokens over
    their limit) or "in_flight" (a delivery still posting or being read back, usage not yet recorded); the checks
    grade those themselves, and the global count reports them. Every row settles, is in flight, conflicts, or is an
    error: none is ignored. Usage and settlement need a valid attempt history, so they read as its error."""
    latest, attempts = validate_attempt_history(sections.get("assignments") or [], sections.get("provider_attempts") or [],
                                                task_id=sections.get("task_id"))
    latest = latest or {}
    usage, usage_state = validate_usage(sections.get("provider_usage") or [], latest,
                                        absent="provider_usage" in (sections.get("absent") or []))
    errors = {"attempts": attempts,
              "usage": attempts or usage,
              "effects": validate_effects(sections.get("effects") or []),
              "deliveries": validate_deliveries_and_receipts(sections.get("slack_deliveries") or [],
                                                             sections.get("status_receipts") or []),
              "lifecycle": lifecycle_error(sections),
              "settlement": attempts or settlement_error(sections, latest)}
    states = {"usage": None if attempts else usage_state,
              "deliveries": delivery_state(sections.get("slack_deliveries") or [])}
    return latest, errors, states


def _secret(settings, name):
    """An entitlement secret file's contents, or None: without it nothing it signs verifies."""
    try:
        from .entitlement_control_plane import _read_secret_file
        return _read_secret_file(getattr(settings, name, "") or "")
    except Exception:  # noqa: BLE001 - any failure leaves every such document unverified
        return None


def _signed_budget(document, authority_digest, secret) -> bool:
    """ProviderBudget.verify, stored under its own grant (as PrimaryProviderBudgetAuthority.reserve requires)."""
    if secret is None:
        return False
    try:
        from .antigravity_team import ProviderBudget
        budget = ProviderBudget.model_validate(document)
        budget.verify(secret)
    except Exception:  # noqa: BLE001 - malformed, unsigned or wrongly signed: not evidence
        return False
    return budget.grant_digest == authority_digest


def _signed_usage(document, row, observed_tokens, secret) -> bool:
    """The ProviderUsageReceipt that record_usage accepts: route-verifier HMAC, and bound to this
    attempt's reservation (grant, provider) and to the usage row's cost and tokens."""
    if secret is None or document is None:
        return False
    try:
        import hashlib
        import hmac
        from .antigravity_team import ProviderUsageReceipt
        from .entitlement_contracts import canonical_json_bytes
        receipt = ProviderUsageReceipt.model_validate(document)
        expected = hmac.new(secret, canonical_json_bytes(receipt.signing_document()), hashlib.sha256).hexdigest()
    except Exception:  # noqa: BLE001 - malformed or unsigned: no actual cost
        return False
    return (hmac.compare_digest(expected, receipt.meter_signature)
            and (receipt.attempt_digest, receipt.authority_digest, receipt.provider_id)
            == (row["attempt_digest"], row["grant_digest"], row["provider_id"])
            and receipt.actual_microusd == row["actual_microusd"] and receipt.observed_tokens == observed_tokens)


def _plain(value):
    """JSON-safe scalar: integral numerics stay ints, times become ISO text."""
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("non-integral monetary amount")
        return int(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row(row):
    return {key: _plain(value) for key, value in row.items()}


def _require_instance(connection, schema, identity):
    """Re-verify, inside the read's own transaction, that this connection reached the accepted control plane
    (storage identity and database instance) that database_binding resolved; anything else refuses the read."""
    from .langgraph_postgres import _require_accepted_database
    with connection.cursor() as cursor:
        _require_accepted_database(cursor, schema=schema, storage_identity=identity.database_identity,
                                   instance_id=identity.database_instance_id)


def artifact_assignment_conflict(artifact, assignment):
    """The one packet-to-assignment rule, for the global count here and check 20's per-task join.

    ``artifact``: verified (revalidated as the Director reloads it), provider, document_digest, recorded_at;
    ``assignment``: provider_id, status, result_digest, reserved_at, completed_at (times as ISO strings).
    primary_default persists a packet only after the attempt succeeded, while the assignment is reserved
    (ACTIVE), and primary_parallel then settles it SUCCEEDED with that result digest; a crash or an ambiguous
    settlement in between leaves ACTIVE or AMBIGUOUS. Returns None for a packet its SUCCEEDED assignment
    recorded, "in_flight" for that bounded window (ACTIVE or AMBIGUOUS: in flight, not a conflict), or the conflict:
    failed revalidation, another provider than the assignment's agent, recorded outside the reservation and
    completion, or any other status or result digest (a FAILED, PENDING or owner-held assignment never has one).
    """
    from .primary_default import _PRIMARY_LANE_AGENT
    if artifact.get("verified") is not True:
        return "the stored packet fails revalidation"
    agent = _PRIMARY_LANE_AGENT.get(str(assignment.get("provider_id")))
    if agent is None or artifact.get("provider") != agent.value:
        return "the packet's provider is not its assignment's agent"
    reserved, recorded = _when(assignment.get("reserved_at")), _when(artifact.get("recorded_at"))
    if reserved is None or recorded is None or recorded < reserved:
        return "the packet was not recorded after its assignment's reservation"
    if assignment.get("status") in ("ACTIVE", "AMBIGUOUS"):
        return "in_flight"
    completed = _when(assignment.get("completed_at"))
    if (assignment.get("status") != "SUCCEEDED" or completed is None or recorded > completed
            or not isinstance(artifact.get("document_digest"), str)
            or assignment.get("result_digest") != artifact["document_digest"]):
        return "the packet is not its SUCCEEDED assignment's result"
    return None


# Every task's evidence, validated by the same per-task rules the collector applies to one task's readback.
EVIDENCE_ROW_CAP = 20000
EVIDENCE_COUNT_KEYS = {"attempts": "provider_attempts_invalid", "usage": "provider_usage_invalid",
                       "effects": "effects_invalid", "deliveries": "slack_deliveries_invalid",
                       "lifecycle": "lifecycle_invalid", "settlement": "settlement_invalid"}
EVIDENCE_IN_FLIGHT_KEYS = {"usage": "provider_usage_in_flight", "deliveries": "slack_deliveries_in_flight"}
_ARTIFACT_ROWS = """SELECT task_id, assignment_id, authority_snapshot_digest, document_digest, canonical_document,
 writer_fencing_generation, recorded_at FROM {s}.director_primary_result_packet_artifact
ORDER BY task_id, assignment_id LIMIT %(limit)s"""
_TASK_MODES = """SELECT task_id, projection->'application'->>'execution_mode' AS execution_mode, projection,
 projection_digest FROM {s}.director_primary_task_projection ORDER BY task_id LIMIT %(limit)s"""


def _global_task_evidence(connection, schema, usage_tables):
    """Run the per-task validation over every task: task_evidence_errors (attempt histories, usage, effects,
    deliveries and receipts, lifecycle order, settlement) and artifact_assignment_conflict for each result packet,
    on the same sections the readback serves (qualification_section_sql, all tasks), in this snapshot.

    Each projection's canonical digest (langgraph_postgres._projection_payload, the digest the writer stores) is
    recomputed from its content: a mismatch is a conflict (projection_digest_mismatches), and the per-task readback
    refuses such a projection the same way.

    Returns {count key: number of tasks (packets, for the two artifact counts) failing it}; every count is None when
    any read exceeds EVIDENCE_ROW_CAP rows, so a count never covers a subset."""
    from .langgraph_postgres import (PostgresControlPlaneError, _projection_payload, _result_packet_artifact_from_row,
                                     qualification_section_sql)
    keys = [*EVIDENCE_COUNT_KEYS.values(), *EVIDENCE_IN_FLIGHT_KEYS.values(), "result_artifacts_invalid",
            "result_artifacts_in_flight", "projection_digest_mismatches"]
    limit = {"limit": EVIDENCE_ROW_CAP + 1}
    names = ["assignments", "provider_attempts", "effects", "slack_deliveries", "status_receipts"]
    names += ["provider_usage"] if usage_tables else []
    tasks = {}

    def task(task_id):
        return tasks.setdefault(task_id, {"assignments": [], "provider_attempts": [], "effects": [],
                                          "slack_deliveries": [], "status_receipts": [], "provider_usage": [],
                                          "execution_mode": None, "artifacts": [],
                                          "absent": [] if usage_tables else ["provider_usage"]})

    reads = [(name, qualification_section_sql(schema, name, all_tasks=True)) for name in names]
    reads += [("execution_mode", _TASK_MODES.format(s=schema)), ("artifacts", _ARTIFACT_ROWS.format(s=schema))]
    projections = []
    for name, query in reads:
        rows = connection.execute(query, limit).fetchall()
        if len(rows) > EVIDENCE_ROW_CAP:
            return {key: None for key in keys}
        for row in rows:
            if name == "execution_mode":
                task(row["task_id"])["execution_mode"] = row["execution_mode"]
                projections.append(row)
            elif name == "artifacts":
                task(row["task_id"])["artifacts"].append(row)
            else:
                task(row["task_id"])[name].append({key: _plain(value) for key, value in row.items() if key != "task_id"})
    counts = dict.fromkeys(keys, 0)
    for row in projections:
        try:
            canonical = _projection_payload(row["projection"] if isinstance(row["projection"], dict)
                                            else json.loads(row["projection"]))[1]
        except (PostgresControlPlaneError, TypeError, ValueError):
            canonical = None
        counts["projection_digest_mismatches"] += canonical != row["projection_digest"]
    for task_id, sections in tasks.items():
        _, errors, states = task_evidence_errors({**sections, "task_id": task_id})
        for category, key in EVIDENCE_COUNT_KEYS.items():
            state = states.get(category)
            counts[key] += errors[category] is not None or (state is not None and state[0] == "conflict")
        for category, key in EVIDENCE_IN_FLIGHT_KEYS.items():
            state = states.get(category)
            counts[key] += errors[category] is None and state is not None and state[0] == "in_flight"
        by_id = {row["assignment_id"]: row for row in sections["assignments"]}
        for row in sections["artifacts"]:
            assignment = by_id.get(row["assignment_id"])
            if assignment is None:  # a packet for no durable assignment of its task (no such foreign key exists)
                counts["result_artifacts_invalid"] += 1
                continue
            try:
                provider, verified = _result_packet_artifact_from_row(row).packet.get("provider"), True
            except (PostgresControlPlaneError, KeyError, TypeError, ValueError):
                provider, verified = None, False
            conflict = artifact_assignment_conflict(
                {"verified": verified, "provider": provider, "document_digest": row["document_digest"],
                 "recorded_at": _plain(row["recorded_at"])}, assignment)
            counts["result_artifacts_in_flight"] += conflict == "in_flight"
            counts["result_artifacts_invalid"] += conflict not in (None, "in_flight")
    return counts


def consistency_report(settings, identity, *, tasks=()) -> dict:
    """One REPEATABLE READ, READ ONLY snapshot of the primary control plane."""
    import psycopg
    from psycopg.rows import dict_row
    from .langgraph_postgres import PostgresControlPlaneError, _require_schema, _result_packet_artifact_from_row

    schema = _require_schema(settings.langgraph_primary_postgres_schema)
    tasks = list(tasks)
    adoption = None
    if settings.langgraph_primary_acceptance_mode == "owner-local-evidence-v1":
        from .primary_activation_evidence import load_pinned_owner_local_adoption
        loaded = load_pinned_owner_local_adoption(settings.langgraph_primary_local_evidence_adoption_path,
            expected_sha256=settings.langgraph_primary_local_evidence_adoption_sha256,
            owner_token=settings.langgraph_primary_owner_token)
        adoption = {key: loaded[key] for key in _ADOPTION_FIELDS}
    connection = psycopg.connect(settings.langgraph_primary_postgres_dsn, connect_timeout=10, row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=5000")
    try:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.read_only = True
        per_task = {}
        with connection.transaction():
            _require_instance(connection, schema, identity)
            counts = connection.execute(_COUNTS.format(s=schema)).fetchone()
            present = connection.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS spend, to_regclass(%s) IS NOT NULL AS usage",
                [f"{schema}.director_primary_provider_{name}" for name in ("spend", "usage")]).fetchone()
            evidence_counts = _global_task_evidence(connection, schema, all(present.values()))
            if tasks:
                present = connection.execute(
                    "SELECT to_regclass(%s) IS NOT NULL AS spend, to_regclass(%s) IS NOT NULL AS usage,"
                    " to_regclass(%s) IS NOT NULL AS budget",
                    [f"{schema}.director_primary_provider_{name}" for name in ("spend", "usage", "budget")]).fetchone()
                usage_tables = all(present.values())
                spend, budgets, verifiers = [], [], None
                if usage_tables:
                    route, owner = (_secret(settings, "entitlement_route_verifier_secret_file"),
                                    _secret(settings, "entitlement_owner_authority_secret_file"))
                    verifiers = {"route_verifier": route is not None, "owner_authority": owner is not None}
                    for r in connection.execute(_SPEND.format(s=schema), {"tasks": tasks}).fetchall():
                        row = _row(r)
                        document, tokens = row.pop("usage_document"), row.pop("observed_tokens")
                        row["usage_verified"] = _signed_usage(document, row, tokens, route)
                        # Actual cost counts only from a receipt that verified.
                        row["request_correlated"] = row["request_correlated"] is True and row["usage_verified"]
                        spend.append(row)
                    for r in connection.execute(_BUDGETS.format(s=schema), {"tasks": tasks}).fetchall():
                        row = _row(r)
                        document = row.pop("document")
                        row["provider_id"] = document.get("provider_id") if isinstance(document, dict) else None
                        row["signature_verified"] = _signed_budget(document, row["authority_digest"], owner)
                        budgets.append(row)
                receipts = [_row(r) for r in connection.execute(_RECEIPTS.format(s=schema), {"tasks": tasks}).fetchall()]
                # The persisted application record (task record, execution mode, effect grant): the scorecard
                # re-renders approval prompts from it rather than from hand-picked fields.
                applications = {r["task_id"]: r["application"] for r in connection.execute(
                    f"SELECT task_id, projection->'application' AS application FROM {schema}.director_primary_task_projection"
                    " WHERE task_id = ANY(%(tasks)s)", {"tasks": tasks}).fetchall()}
                # Each persisted provider result packet, revalidated against its stored digest exactly as the
                # Director reloads it; content that fails revalidation attests nothing and carries no outcome.
                artifacts = []
                for r in connection.execute(
                        "SELECT task_id, assignment_id, authority_snapshot_digest, document_digest, canonical_document,"
                        f" writer_fencing_generation, recorded_at FROM {schema}.director_primary_result_packet_artifact"
                        " WHERE task_id = ANY(%(tasks)s) ORDER BY task_id, assignment_id", {"tasks": tasks}).fetchall():
                    try:
                        packet = _result_packet_artifact_from_row(r).packet
                        outcome, provider, verified = packet.get("outcome"), packet.get("provider"), True
                    except (PostgresControlPlaneError, KeyError, TypeError, ValueError):
                        outcome, provider, verified = None, None, False
                    artifacts.append({"task_id": r["task_id"], "assignment_id": r["assignment_id"],
                                      "document_digest": r["document_digest"], "outcome": outcome,
                                      "provider": provider, "verified": verified,
                                      "recorded_at": _plain(r["recorded_at"])})
                for task_id in tasks:
                    per_task[task_id] = {"usage_tables_present": usage_tables, "verifier_secrets": verifiers,
                                         "result_artifacts": [{k: v for k, v in a.items() if k != "task_id"}
                                                              for a in artifacts if a["task_id"] == task_id],
                                         "application": applications.get(task_id),
                                         "spend": [r for r in spend if r["task_id"] == task_id],
                                         "budgets": [r for r in budgets if r["task_id"] == task_id],
                                         "approval_receipts": [{k: v for k, v in r.items() if k != "task_id"}
                                                               for r in receipts if r["task_id"] == task_id]}
    finally:
        connection.close()
    return {"version": "director-primary-consistency/v1",
            "generation": {"manifest_sha256": os.environ[_PIN],
                           "acceptance_mode": settings.langgraph_primary_acceptance_mode,
                           "candidate_commit": identity.candidate_commit,
                           "candidate_tree": identity.candidate_tree, "adoption": adoption,
                           # require_generation verified the manifest against this image before any read.
                           "image_id": os.environ.get("DIRECTOR_RUNTIME_IMAGE_ID")},
            "captured_at": _plain(counts["captured_at"]),
            "intake_fencing_generation": _plain(counts["intake_fencing_generation"]),
            # None (over the row cap) is not a count: check 07 reads the snapshot as malformed, never as zero.
            "counts": {**{key: int(counts[key]) for key in _COUNT_KEYS},
                       **evidence_counts},
            "tasks": per_task}


_OPENAI_MODEL = re.compile(r"(?:gpt-|o[0-9]|codex|chatgpt-)")
_CODEX_EVENTS = frozenset({"thread.started", "turn.started", "item.started", "item.updated", "item.completed",
                           "turn.completed"})


def _codex_stream(output: bytes) -> bool:
    """A native Codex CLI JSONL turn: only its event types, from thread.started to turn.completed."""
    try:
        events = [json.loads(line) for line in output.splitlines() if line.strip()]
    except ValueError:
        return False
    return (bool(events) and all(isinstance(event, dict) and event.get("type") in _CODEX_EVENTS for event in events)
            and events[0]["type"] == "thread.started" and events[-1]["type"] == "turn.completed")


def _capture_product(provider, output: bytes):
    """Name the real product that produced a verified native capture, or None.

    The product comes from the capture's own dialect and model, never from the
    lane name: a Responses envelope naming another vendor's model is a
    substitution, and None is what check 11 counts as one. Every named product
    rests on an identity the output itself carries: the Responses and grok CLI
    model, Claude's modelUsage keys. A dialect that carries none is labelled
    ``-unattested``, which check 11 reads as unknown, not as the product.
    """
    try:
        native = json.loads(output)
    except ValueError:
        native = None  # Codex CLI output is JSONL, not one document.
    envelope = native if isinstance(native, dict) else {}
    model = envelope.get("model")
    if provider == "openai":
        if envelope.get("object") == "response":
            return "openai-responses" if isinstance(model, str) and _OPENAI_MODEL.match(model) else None
        # The Codex CLI's JSONL names no product and no model (native_codex_transport records "actual model and cost
        # are not reported by this event stream"), and no release descriptor pins the Codex executable's digest, so
        # a well-formed stream attests only its dialect: labelled unattested (check 11 UNKNOWN), never openai-codex.
        return "openai-codex-unattested" if _codex_stream(output) else None
    if provider == "grok":
        if envelope.get("object") == "response":
            return "xai-grok-responses" if isinstance(model, str) and model.startswith("grok") else None
        # The grok CLI dialect grok_terminal_success accepts: stopReason and an integer num_turns,
        # and it must name a Grok model: the lane name alone attests nothing.
        cli = "stopReason" in envelope and type(envelope.get("num_turns")) is int
        return "xai-grok-cli" if cli and isinstance(model, str) and "grok" in model.lower() else None
    if provider == "claude":
        # A result envelope must name its models: a non-empty modelUsage whose keys are all Claude models.
        usage = envelope.get("modelUsage")
        if (envelope.get("type") != "result" or not isinstance(usage, dict) or not usage
                or any(not str(key).startswith("claude") for key in usage)):
            return None
        return "anthropic-claude"
    return None


def _activation_recorded_at(dsn, schema, binding, digest, surface="runtime_adapter", *, identity):
    """The insertion time of the current binding's latest row for that surface, when that row is LIVE_OK
    and carries digest; otherwise None. Every binding field scopes the lookup (the same digest may exist
    under another workspace or version), and only the latest revision is read, as the store's load does."""
    if surface not in ("app", "runtime_adapter"):
        raise ValueError("unknown activation surface")
    import psycopg
    from psycopg.rows import dict_row
    from .langgraph_postgres import _require_schema

    schema = _require_schema(schema)
    with psycopg.connect(dsn, connect_timeout=10, row_factory=dict_row,
                         options="-c default_transaction_read_only=on -c statement_timeout=10000") as connection, \
            connection.transaction():
        _require_instance(connection, schema, identity)
        row = connection.execute(
            f"SELECT recorded_at, stage, evidence_digest FROM {schema}.director_provider_activation_evidence"
            " WHERE provider_id=%s AND workspace_id=%s AND provider_version=%s AND app_version=%s"
            " AND runtime_adapter_version=%s AND workspace_version=%s AND binding_version=%s AND surface=%s"
            " ORDER BY revision DESC LIMIT 1",
            (binding.provider_id, binding.workspace_id, binding.provider_version, binding.app_version,
             binding.runtime_adapter_version, binding.workspace_version, binding.binding_version, surface)).fetchone()
    if not row or row["stage"] != "LIVE_OK" or row["evidence_digest"] != digest:
        return None
    return _plain(row["recorded_at"])


def activation_capture_report(settings, identity, manifest) -> dict:
    """Latest per-lane capture digests and expiry through a load-only store."""
    from .langgraph_postgres import open_provider_activation_store
    from .primary_activation_evidence import ActivationEvidenceError
    from .primary_provider_activation import PrimaryProviderActivationService
    from .primary_provider_evidence import _BoundedCaptureReader
    from .provider_validation import capture_verifier_from_environment

    verifier = capture_verifier_from_environment(candidate=identity.candidate_commit,
        tree=identity.candidate_tree, require_enabled=False)
    if verifier is None:
        raise ActivationEvidenceError("authenticated capture verification is unavailable")
    verifier.store = _BoundedCaptureReader(verifier.store.dsn)
    with open_provider_activation_store(settings.langgraph_primary_postgres_dsn,
            schema=identity.postgres_schema, accepted_storage_identity=identity.database_identity,
            accepted_instance_id=identity.database_instance_id,
            allow_insecure_local_transport=settings.langgraph_primary_allow_insecure_local_postgres,
            deadline=time.monotonic() + 60) as store:
        # Only load is exposed, so this service cannot append by construction.
        service = PrimaryProviderActivationService(store=SimpleNamespace(load=store.load),
            bindings=settings.primary_provider_activation_bindings(),
            required_providers=settings.langgraph_primary_required_providers, capture_verifier=verifier)
        lanes = service.capture_attestations()
    bindings = settings.primary_provider_activation_bindings()
    for provider, lane in lanes.items():
        surface = lane["surfaces"].get("runtime_adapter")
        product = None
        if surface is not None and surface["capture_verified"] is True:
            row = verifier.store.load_capture(surface["evidence_digest"])
            product = _capture_product(provider, bytes(row[2])) if row is not None else None
        lane["product"] = product
        # The persisted insertion time, not the caller-supplied observed_at, says when each surface gated anything;
        # provider_ready needs both surfaces, so both are reported.
        for name, each in lane["surfaces"].items():
            if each is not None and each.get("chain_valid") is True:
                each["recorded_at"] = _activation_recorded_at(settings.langgraph_primary_postgres_dsn,
                                                              identity.postgres_schema, bindings[provider],
                                                              each["evidence_digest"], name, identity=identity)
    return {"version": "director-activation-captures/v1", "generation_manifest_sha256": os.environ[_PIN],
            "provider_bindings_sha256": manifest["provider_bindings_sha256"],
            "observed_at": datetime.now(timezone.utc).isoformat(), "lanes": lanes,
            # The generation's required providers: the scorecard requires a lane for each, and no other.
            "required_providers": sorted(settings.langgraph_primary_required_providers)}
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:46 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:28 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:21 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:58 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:37 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:46 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:16 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:38 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 05:01 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:57 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:16 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:32 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 18:59 UTC | END]]
