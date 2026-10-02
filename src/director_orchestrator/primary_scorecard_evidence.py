# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:53 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:49 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:06 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:00 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 15:16 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 13:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 11:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 10:29 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 09:18 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 08:24 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 07:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 06:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 05:07 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 04:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:36 UTC | BEGIN]]
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
import hashlib
import hmac
import json
import os
import re
import uuid
import time
from pathlib import Path
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
               "batches_pending_projection_binding", "pending_owner_decisions", "nonterminal_projections",
               "batches_without_assignments", "effect_children_cross_task", "terminal_evidence_without_reservation",
               "batch_authority_mismatches")

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
    -- A terminal row recorded before any of its claim's reconciliation events does not resolve the later event.
    WHERE t.claim_key IS NULL OR t.recorded_at < r.recorded_at) AS unresolved_reconciliation,
 (SELECT count(*) FROM {s}.director_primary_parallel_assignment WHERE status='AMBIGUOUS') AS ambiguous_assignments,
 (SELECT count(*) FROM p WHERE tc='RECONCILIATION_REQUIRED') AS tasks_requiring_reconciliation,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_assignment x USING (task_id)
    WHERE p.settled AND x.status IN ('PENDING','ACTIVE','AMBIGUOUS','OWNER_DECISION_REQUIRED') AND NOT EXISTS
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
    (SELECT 1 FROM p WHERE p.task_id=b.task_id)) AS batches_without_projection,
 -- primary_parallel.register_plan writes a batch and its validated, non-empty DAG in one transaction: a batch with
 -- no assignment is inconsistent whatever its task's classification.
 (SELECT count(*) FROM {s}.director_primary_parallel_batch b WHERE NOT EXISTS
    (SELECT 1 FROM {s}.director_primary_parallel_assignment x WHERE x.task_id=b.task_id)) AS batches_without_assignments,
 -- An execution, remote binding or terminal row whose reservation (or claim) belongs to another task.
 ((SELECT count(*) FROM {s}.director_primary_effect_execution e JOIN {s}.director_primary_effect_reservation r
     USING (reservation_key) WHERE e.task_id<>r.task_id)
  + (SELECT count(*) FROM {s}.director_primary_remote_binding b JOIN {s}.director_primary_effect_reservation r
     USING (reservation_key) WHERE b.task_id<>r.task_id)
  + (SELECT count(*) FROM {s}.director_primary_terminal_evidence t JOIN {s}.director_primary_effect_reservation r
     ON r.claim_key=t.claim_key WHERE t.task_id<>r.task_id)) AS effect_children_cross_task,
 -- Terminal evidence for a claim no effect reservation holds (the effects section's join would drop it).
 (SELECT count(*) FROM {s}.director_primary_terminal_evidence t WHERE NOT EXISTS
    (SELECT 1 FROM {s}.director_primary_effect_reservation r WHERE r.claim_key=t.claim_key))
    AS terminal_evidence_without_reservation,
 -- register_plan writes the registering writer's token and generation: the task's writer keeps that token and a
 -- generation no older than the batch's.
 (SELECT count(*) FROM {s}.director_primary_parallel_batch b LEFT JOIN {s}.director_primary_task_writer w
    USING (task_id) WHERE w.task_id IS NULL OR b.owner_token <> w.owner_token
    OR b.fencing_generation > w.fencing_generation) AS batch_authority_mismatches"""

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
 a.claimed_at AS authorization_claimed_at, a.proof_expires_at AS authorization_proof_expires_at,
 row_number() OVER (PARTITION BY r.task_id ORDER BY r.recorded_at, r.command_id) AS task_ordinal
FROM {s}.director_primary_command_receipt r LEFT JOIN {s}.director_primary_command_authorization a
 ON a.task_id = r.task_id AND a.operation = r.operation AND a.command_id = r.command_id
WHERE r.task_id = ANY(%(tasks)s) AND r.operation='approval_resume'"""

# One extra row per task proves the cap was hit; the outer query keeps the cap per task, not for the whole read.
_RECEIPT_CAP = 100
_RECEIPTS_BOUNDED = "SELECT * FROM (" + _RECEIPTS + ") q WHERE task_ordinal <= %(cap)s + 1 ORDER BY task_id, task_ordinal"


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


# Each attempt event's field contract, from its producer (primary_parallel):
#   event 1  PrimaryParallel.reserve_provider_attempt  STARTED; no result, outcome evidence or provider run id
#   event 2  PrimaryParallel.record_provider_attempt   SUCCEEDED, FAILED or AMBIGUOUS; outcome evidence always; never
#            a provider run id (the insert carries none); SUCCEEDED with a result digest (the roster passes the
#            authenticated result's), FAILED with or without one (a reported non-success has one, a transport
#            failure none), AMBIGUOUS with none (the outcome is unknown)
#   event 3  PrimaryParallel.record_callback_result    SUCCEEDED, FAILED or AMBIGUOUS; outcome evidence and the
#            provider run id always (the callback requires it); SUCCEEDED with a result digest
_EVENT_CONTRACT = {
    1: {"statuses": {"STARTED"}, "null": ("result_digest", "outcome_evidence_digest", "provider_run_id_digest"),
        "required": ()},
    2: {"statuses": {"SUCCEEDED", "FAILED", "AMBIGUOUS"}, "null": ("provider_run_id_digest",),
        "required": ("outcome_evidence_digest",)},
    3: {"statuses": {"SUCCEEDED", "FAILED", "AMBIGUOUS"}, "null": (),
        "required": ("outcome_evidence_digest", "provider_run_id_digest")}}


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
                or not _is_id(row.get("provider_id")) or not _is_id(row.get("deployment_id"))
                or row["assignment_id"] in by_assignment
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
        contract = _EVENT_CONTRACT[row["event_sequence"]]
        if (row["status"] not in contract["statuses"]
                or any(row.get(field) is not None for field in contract["null"])
                or any(row.get(field) is None for field in contract["required"])
                or (row["event_sequence"] == 2 and row["status"] == "AMBIGUOUS" and row.get("result_digest") is not None)):
            return None, f"event {row['event_sequence']} breaks its producer's field contract"
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
    # Each later ordinal was admitted the way primary_parallel admits it: against the previous attempt's latest event
    # when the new attempt started, which must be a FAILED transient transport failure that authorize_provider_retry
    # (the producer's own rule) accepts for the new transport. _execute_three_attempt_roster stops on authenticated
    # success, so any attempt after a SUCCEEDED one is a conflict.
    from .primary_parallel import (PROVIDER_TRANSIENT_TRANSPORT_FAILURE, FailureKind, ParallelDispatchError,
                                   authorize_provider_retry)
    for (assignment_id, ordinal), events in grouped.items():
        if ordinal == 1:
            continue
        started = min(_when(event["recorded_at"]) for event in events)
        before = [event for event in grouped[(assignment_id, ordinal - 1)] if _when(event["recorded_at"]) <= started]
        if not before:
            return None, "a later attempt started before its predecessor"
        held = max(before, key=lambda event: event["event_sequence"])
        if any(event["status"] == "SUCCEEDED" for event in grouped[(assignment_id, ordinal - 1)]):
            return None, "an attempt follows a SUCCEEDED attempt"
        failure = (FailureKind.TRANSIENT if held["status"] == "FAILED"
                   and held["reason_code"] == PROVIDER_TRANSIENT_TRANSPORT_FAILURE else FailureKind.TERMINAL)
        try:
            authorized, _ = authorize_provider_retry(attempt_number=ordinal, previous_failure=failure,
                                                     transport_id=events[0]["transport_id"],
                                                     previous_transport_id=held["transport_id"])
        except ParallelDispatchError:
            authorized = False
        if not authorized:
            return None, "a later attempt was not authorized as a retry of its predecessor"
    return latest, None


_SLACK_TS = re.compile(r"\d{10,}\.\d{1,6}\Z")
DELIVERY_STATES = ("PENDING", "POSTING", "ACKNOWLEDGED", "DELIVERED", "AMBIGUOUS", "REFUSED", "SUPPRESSED", "SUPERSEDED")


def validate_effects(effects, task_id=None):
    keys = set()
    for row in effects:
        # Children join by reservation (or claim) alone and carry their own task (review 4110798521).
        if task_id is not None and any(row.get(key) not in (None, task_id)
                                       for key in ("execution_task_id", "binding_task_id", "terminal_task_id")):
            return "an effect's execution, binding or terminal row belongs to another task"
        # Review 4111725044: a remote binding carries its claim's owner; a terminal row its claim's (or an authorized
        # successor's) owner and fence, compared in SQL.
        if row.get("remote_identifier") is not None and row.get("binding_owner_matches") is not True:
            return "an effect's remote binding does not carry its claim's owner"
        if row.get("terminal_disposition") is not None and row.get("terminal_authority_matches") is not True:
            return "an effect's terminal evidence carries neither its claim's nor an authorized successor's authority"
        # Review 4111508067: a reservation carries its claim's task, owner and fence (compared in SQL).
        if any(row.get(key) is not True for key in ("reservation_task_matches", "reservation_owner_matches",
                                                     "reservation_fencing_matches")):
            return "an effect reservation does not carry its claim's task, owner or fence"
        # Review 4111174703: an execution marker carries its claim's owner and fence (compared in SQL).
        if row.get("execution_started_at") and (row.get("execution_owner_matches") is not True
                                                or row.get("execution_fencing_matches") is not True):
            return "an effect's execution marker does not carry its claim's owner or fence"
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
        # The effect executor (primary_effects) claims execution before any I/O and binds the remote object after it.
        if row.get("remote_identifier") is not None and not row.get("execution_started_at"):
            return "a remote binding without an execution claim"
        if row.get("remote_identifier") is not None and (not _is_id(row["remote_system"])
                                                          or not isinstance(row["remote_identifier"], str)
                                                          or not row["remote_identifier"].strip()
                                                          or _when(row.get("bound_at")) is None):
            return "malformed remote binding"
        if row.get("terminal_disposition") is not None and (
                row["terminal_disposition"] not in ("COMPLETED", "FAILED")  # langgraph_postgres.FINAL_DISPOSITIONS
                or not _DIGEST.fullmatch(str(row.get("terminal_evidence_digest", "")))
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


def validate_deliveries_and_receipts(deliveries, receipts, *, schema=None, task_id=None, source=None):
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
        # PrimarySlackDelivery.record_owner_decision writes decision, time and command together, on an approval row
        # in whatever state it is (its UPDATE filters no state).
        decision = [row.get(key) is None for key in ("owner_decision", "owner_decision_at", "owner_command_id")]
        if any(decision) and not all(decision):
            return "an owner decision lacks its time or command"
        if row.get("owner_command_id") is not None and not _is_id(row["owner_command_id"]):
            return "malformed owner command identity"
        if (row.get("owner_decision") is not None) and notification_key_family(row["notification_key"]) != "approval":
            return "owner decision recorded on a non-approval notification"
        if row.get("slack_ts") is not None and _SLACK_TS.fullmatch(str(row["slack_ts"])) and not decision_follows_prompt(row):
            return "an owner decision precedes its prompt"
        # PrimarySlackDelivery.enqueue stores the task projection's digest the notification was built from; an
        # approval key names that same digest after its request digest.
        if not _DIGEST.fullmatch(str(row.get("projection_digest", ""))):
            return "malformed delivery projection digest"
        if (notification_key_family(row["notification_key"]) == "approval"
                and ":".join(row["notification_key"].split(":")[3:]) != row["projection_digest"]):
            return "an approval delivery's key names another projection"
        # Its identity is uuid5(URL, "<schema>:<task>:<key>") (enqueue); checked where the schema is known.
        if schema is not None and row.get("client_msg_id") != str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"{schema}:{task_id}:{row['notification_key']}")):
            return "a delivery's client_msg_id is not its producer identity"
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
        error = status_receipt_error(row, task_id, source)
        if error is not None:
            return error
    return None


def status_receipt_error(row, task_id, source):
    """A status receipt against its producer (DefaultPrimaryApplication.create_status_receipt) and its task's source
    (review 4111725051): receipt_id recomputed as "status-" + the first 24 hex of the digest of
    {source_event_id, task_id}; the channel and thread the task's own intake; the user the owner the EVALUATOR is
    configured with (never the candidate's own setting); a sha256 text digest. ``source``: channel_id, thread_ts and
    owner_user_id, each None where the caller cannot know it (the in-image global count has no evaluator owner)."""
    from .primary_default import _digest as producer_digest
    if task_id is None:
        return "a status receipt cannot be bound without its task"
    expected = "status-" + producer_digest({"source_event_id": row.get("source_event_id"),
                                           "task_id": task_id}).removeprefix("sha256:")[:24]
    if row.get("receipt_id") != expected:
        return "a status receipt's id is not its producer's derivation"
    if not _DIGEST.fullmatch(str(row.get("text_digest", ""))) or not _is_id(row.get("user_id")) \
            or not _is_id(row.get("channel_id")) or not _is_id(row.get("thread_ts")):
        return "a status receipt lacks its source binding"
    for key, field in (("channel_id", "channel_id"), ("thread_ts", "thread_ts"), ("owner_user_id", "user_id")):
        if (source or {}).get(key) is not None and row.get(field) != source[key]:
            return "a status receipt is not bound to its task's thread or the configured owner"
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


def decision_follows_prompt(row):
    """The one prompt-before-decision rule (check 08 and every delivery row): an owner decision recorded on a posted
    prompt is recorded no earlier than Slack's ts for that prompt. A row never posted has no ts to order against."""
    if row.get("owner_decision_at") is None or not row.get("slack_ts"):
        return True
    decided = _when(row["owner_decision_at"])
    return decided is not None and Decimal(str(decided.timestamp())) >= Decimal(str(row["slack_ts"]))


def delivery_state(deliveries, projection_digest=None):
    """Each Slack delivery's state, as PrimarySlackDelivery moves it. Returns None or (kind, reason), a conflict
    outranking in flight.
    DELIVERED (its lifecycle checked by lifecycle_error), REFUSED (Slack definitely did not post; never reposted),
    SUPPRESSED and SUPERSEDED are settled. PENDING and POSTING are in flight (a POSTING claim older than 60 s becomes
    AMBIGUOUS). ACKNOWLEDGED and AMBIGUOUS are in flight while readback may still settle them (fewer than two readback
    attempts); after two, nothing ever reads them again, so they are conflicts."""
    found = None
    for row in deliveries:
        state, reads = row.get("state"), row.get("readback_attempts")
        # The terminal notification is enqueued from the terminal projection: one naming another projection than
        # the task record's current one is stale, and no readback ever makes it the record's.
        if (row.get("notification_key") == "terminal" and projection_digest is not None
                and row.get("projection_digest") != projection_digest):
            return ("conflict", "the terminal delivery names another projection than the task record's")
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
    if status == "OWNER_DECISION_REQUIRED":  # settled only from a reservation, completed_at set with it
        return ("reserved_at", "completed_at")
    return ("reserved_at",) if status in ("ACTIVE", "AMBIGUOUS") else ()


def assignment_contract(row):
    """The assignment columns primary_parallel writes per state: reserve sets the reservation digest with reserved_at
    (so exactly the states that require reserved_at carry one; a PENDING row, and one cancelled before dispatch,
    never do) and clears reason_code; PENDING carries none or paid_setup_required; every settlement names one."""
    required = required_moments("assignment", row)
    if ("reserved_at" in required) != (row.get("reservation_digest") is not None):
        return False
    status, reason = row.get("status"), row.get("reason_code")
    if status == "ACTIVE":
        return reason is None
    if status == "PENDING":
        return reason in (None, "paid_setup_required")
    return _is_id(reason)


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


# Each Slack delivery state's field contract, as PrimarySlackDelivery (claim, settle, readback) and the ledger's
# guard (director_primary_slack_delivery_guard) write it: (moments required, fields always unset, readback attempts
# allowed). PENDING rows are suppressed or superseded before any claim; REFUSED, AMBIGUOUS and ACKNOWLEDGED follow a
# claim (POSTING), and only an acknowledgement carries Slack's ts; readback runs only on ACKNOWLEDGED or AMBIGUOUS.
# DELIVERED is delivery_settled's.
_UNCLAIMED = ("claimed_at", "acknowledged_at", "delivered_at", "slack_ts")
_UNACKNOWLEDGED = ("acknowledged_at", "delivered_at", "slack_ts")
DELIVERY_CONTRACT = {
    "PENDING": (("created_at",), _UNCLAIMED, {0}),
    "SUPPRESSED": (("created_at",), _UNCLAIMED, {0}),
    "SUPERSEDED": (("created_at",), _UNCLAIMED, {0}),
    "POSTING": (("created_at", "claimed_at"), _UNACKNOWLEDGED, {0}),
    "REFUSED": (("created_at", "claimed_at"), _UNACKNOWLEDGED, {0}),
    "AMBIGUOUS": (("created_at", "claimed_at"), _UNACKNOWLEDGED, {0, 1, 2}),
    "ACKNOWLEDGED": (("created_at", "claimed_at", "acknowledged_at", "slack_ts"), ("delivered_at",), {0, 1, 2}),
}
# Assignment moments primary_parallel never writes in a state: nothing before its reservation, no completion while
# reserved (settling to AMBIGUOUS clears completed_at; OWNER_DECISION_REQUIRED may or may not carry one).
_ASSIGNMENT_UNSET = {"PENDING": ("reserved_at", "completed_at"), "ACTIVE": ("completed_at",),
                     "AMBIGUOUS": ("completed_at",)}  # OWNER_DECISION_REQUIRED always has both (required_moments)


# last_error exactly as PrimarySlackDelivery writes it (review 4111082596): never before a claim, and cleared by
# the DELIVERED settlement; REFUSED always names the refusal (Slack's ok:false or its 429); AMBIGUOUS always names
# the missing or invalid acknowledgement, and after a readback attempt may instead name that readback's failure;
# ACKNOWLEDGED carries none until a readback attempt fails. A crash between a readback's claim and its error write
# leaves the earlier value.
_RATE_LIMITED = re.compile(r"slack_rate_limited:retry_after=[0-9]{1,9}\Z")
_READBACK_ERRORS = ("readback_unverified",)


def delivery_error_contract(row):
    state, error, reads = row.get("state"), row.get("last_error"), row.get("readback_attempts")
    rate_limited = isinstance(error, str) and bool(_RATE_LIMITED.fullmatch(error))
    read_back = type(reads) is int and reads > 0 and (error in _READBACK_ERRORS or rate_limited)
    if state in ("PENDING", "SUPPRESSED", "SUPERSEDED", "POSTING", "DELIVERED"):
        return error is None
    if state == "REFUSED":
        return error == "slack_refused" or rate_limited
    if state == "AMBIGUOUS":
        return error in ("post_ack_missing", "post_ack_invalid") or read_back
    if state == "ACKNOWLEDGED":
        return error is None or read_back
    return False


def delivery_contract(row):
    """A delivery row carries exactly its state's fields (last_error included), its present moments in order."""
    if not delivery_error_contract(row):
        return False
    if row.get("state") == "DELIVERED":
        return delivery_settled(row)
    contract = DELIVERY_CONTRACT.get(row.get("state"))
    if contract is None:
        return False
    required, unset, reads = contract
    return (all(row.get(key) is None for key in unset) and type(row.get("readback_attempts")) is int
            and row["readback_attempts"] in reads and ordered(row, DELIVERY_FIELDS, required))


def task_moments(sections):
    """Every timestamp of every child row of the task (assignments, attempts, effects, reconciliation events,
    deliveries, status receipts, result and work packets): none may precede the task's creation."""
    rows = [(sections.get("assignments"), ASSIGNMENT_FIELDS), (sections.get("provider_attempts"), ("recorded_at",)),
            (sections.get("effects"), EFFECT_FIELDS), (sections.get("reconciliation_events"), ("recorded_at",)),
            (sections.get("slack_deliveries"), ("created_at", "claimed_at", "acknowledged_at", "delivered_at",
                                                "owner_decision_at")),
            (sections.get("status_receipts"), ("created_at",)), (sections.get("result_artifacts"), ("recorded_at",)),
            (sections.get("work_packets"), ("recorded_at",))]
    return [row.get(key) for section, keys in rows for row in section or [] for key in keys if row.get(key)]


def lifecycle_error(sections):
    """The one ordering rule over a task's rows: every child row's moments at or after the task's creation
    (``task_created_at``, the projection's created_at); each attempt's events in sequence order and chronological;
    every attempt event inside its assignment (primary_parallel reserves, the roster records STARTED then the
    terminal event, primary_parallel settles); every assignment and effect carrying exactly the moments the product
    writes for its state, in order; every delivery its state's field contract (DELIVERY_CONTRACT). None, or the
    first violation."""
    created = _when(sections.get("task_created_at"))
    if created is None or any(_when(moment) is None or _when(moment) < created for moment in task_moments(sections)):
        return "a row of the task is recorded before the task was created, or the task's creation time is unknown"
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
               and all(row.get(key) is None for key in _ASSIGNMENT_UNSET.get(row.get("status"), ()))
               and assignment_contract(row)
               for row in sections.get("assignments") or []):
        return "an assignment lacks a moment its state requires, or its moments are out of order"
    if not all(ordered(row, EFFECT_FIELDS, required_moments("effect", row)) for row in sections.get("effects") or []):
        return "an external effect lacks a moment its state requires, or its moments are out of order"
    if not all(delivery_contract(row) for row in sections.get("slack_deliveries") or []):
        return "a Slack delivery breaks its state's field contract or lifecycle"
    return None


def reconciliation_error(sections):
    """Reconciliation events against their claim's external effect: an event recorded before the effect's
    reservation, or after the claim's terminal evidence (a later event the earlier terminal row would hide), is a
    conflict. None, or the first violation."""
    effects = {row.get("claim_key"): row for row in sections.get("effects") or []}
    for event in sections.get("reconciliation_events") or []:
        recorded = _when(event.get("recorded_at"))
        if recorded is None:
            return "a reconciliation event has no valid time"
        effect = effects.get(event.get("claim_key"))
        if effect is None:
            # Every producer (primary_effects._ambiguous, PrimaryCoordinator.reconcile_effect) records an event for a
            # reservation it holds: an event naming none is a conflict.
            return "a reconciliation event names no external-effect reservation of its task"
        reserved, terminal = _when(effect.get("reserved_at")), _when(effect.get("terminal_recorded_at"))
        if reserved is None or recorded < reserved:
            return "a reconciliation event precedes its effect's reservation"
        if not _DIGEST.fullmatch(str(event.get("evidence_digest", ""))) or event.get("state") not in ("AMBIGUOUS", "RECONCILIATION_REQUIRED"):
            return "a reconciliation event is malformed"  # RECONCILIATION_STATES
        if event.get("authority_matches") is not True:  # review 4111725048
            return "a reconciliation event carries neither its claim's nor an authorized successor's authority"
        if terminal is not None and terminal < recorded:
            return "a reconciliation event was recorded after its claim's terminal evidence"
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


def work_packet_error(sections):
    """Work packets against dispatch and results (primary_default persists the revalidated work packet, through
    record_work_packet_artifact, before any provider attempt of the assignment, and a result packet under the same
    authority snapshot). Every work packet revalidates and names an assignment of the task that left PENDING, once;
    every dispatched assignment (one with a provider attempt) has exactly one, recorded no earlier than the
    assignment's reservation and no later than its first attempt; and
    every result packet has its assignment's work packet under the same authority snapshot digest. None when the
    sections carry no work packets at all (the collector's readback does not serve them), else the first violation."""
    packets = sections.get("work_packets")
    if packets is None:
        return None
    assignments = {row.get("assignment_id"): row for row in sections.get("assignments") or []}
    by_id = {}
    for packet in packets:
        assignment = assignments.get(packet.get("assignment_id"))
        if packet.get("assignment_id") in by_id:
            return "an assignment has more than one work packet"
        if packet.get("verified") is not True:
            return "a stored work packet fails revalidation"
        if assignment is None or assignment.get("status") == "PENDING":
            return "a work packet names no dispatched assignment of its task"
        reserved, recorded = _when(assignment.get("reserved_at")), _when(packet.get("recorded_at"))
        if reserved is None or recorded is None or recorded < reserved:
            return "a work packet was recorded before its assignment was reserved"
        by_id[packet["assignment_id"]] = packet
    for event in sections.get("provider_attempts") or []:
        packet = by_id.get(event.get("assignment_id"))
        if packet is None:
            return "a dispatched assignment has no work packet"
        recorded, attempted = _when(packet.get("recorded_at")), _when(event.get("recorded_at"))
        if recorded is None or attempted is None or recorded > attempted:
            return "a work packet was not recorded before its assignment's attempts"
    for result in sections.get("result_artifacts") or []:
        packet = by_id.get(result.get("assignment_id"))
        if packet is None or packet.get("authority_snapshot_digest") != result.get("authority_snapshot_digest"):
            return "a result packet has no work packet under the same authority snapshot"
    return None


def missing_result_packets(sections, latest):
    """Live SUCCEEDED assignments with no result packet. primary_default persists the authenticated result packet
    (record_result_packet_artifact) before the roster settles an attempt SUCCEEDED (event 2); an assignment settled
    by an authenticated callback (event 3, record_provider_callback) persists none, and offline doubles
    (offline_simulation, paid_off) record none. Returns the assignment IDs, for the global count and check 20."""
    if sections.get("execution_mode") in ("offline_simulation", "paid_off"):
        return []
    packets = {row.get("assignment_id") for row in sections.get("result_artifacts") or []}
    missing = []
    for assignment in sections.get("assignments") or []:
        if assignment.get("status") != "SUCCEEDED" or assignment.get("assignment_id") in packets:
            continue
        own = [event for (assignment_id, _), event in (latest or {}).items()
               if assignment_id == assignment.get("assignment_id")]
        final = max(own, key=lambda event: event.get("attempt_ordinal") or 0) if own else None
        if final is None or final.get("event_sequence") != 3:
            missing.append(assignment.get("assignment_id"))
    return missing


EVIDENCE_CATEGORIES = ("attempts", "usage", "effects", "deliveries", "lifecycle", "settlement", "work_packets")


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
              "effects": (validate_effects(sections.get("effects") or [], sections.get("task_id"))
                          or reconciliation_error(sections)),
              "deliveries": validate_deliveries_and_receipts(sections.get("slack_deliveries") or [],
                                                             sections.get("status_receipts") or [],
                                                             schema=sections.get("schema"),
                                                             task_id=sections.get("task_id"),
                                                             source={key: sections.get("source_" + key) for key in
                                                                     ("channel_id", "thread_ts", "owner_user_id")}),
              "lifecycle": lifecycle_error(sections),
              "settlement": attempts or settlement_error(sections, latest),
              "work_packets": work_packet_error(sections)}
    states = {"usage": None if attempts else usage_state,
              "deliveries": delivery_state(sections.get("slack_deliveries") or [],
                                           sections.get("projection_digest"))}
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


# The writer generation a packet was written under can only be bounded after the fact: the task's writer row keeps
# its current lease, so a packet's generation must be positive and no newer than the writer's.
_PACKET_SQL = """SELECT p.task_id, p.assignment_id, p.authority_snapshot_digest, p.document_digest,
 p.canonical_document, p.writer_fencing_generation, p.recorded_at,
 (p.writer_fencing_generation >= 1 AND p.writer_fencing_generation <= w.fencing_generation) IS TRUE
   AS writer_generation_bounded
 FROM {s}.{table} p LEFT JOIN {s}.director_primary_task_writer w ON w.task_id = p.task_id {where}
 ORDER BY p.task_id, p.assignment_id LIMIT %(limit)s"""


# Each batch with the columns register_plan hashed: its assignments' DAG identity and the task record's plan.
_PLAN_ROWS = """SELECT b.task_id, b.plan_digest AS batch_plan_digest,
 p.projection->>'parallel_plan_digest' AS projection_plan_digest,
 p.projection->'application'->'task_record' AS task_record,
 coalesce(jsonb_agg(jsonb_build_object('assignment_id', x.assignment_id, 'sequence', x.sequence,
   'provider_id', x.provider_id, 'deployment_id', x.deployment_id, 'dependencies', x.dependencies,
   'write_surfaces', x.write_surfaces, 'retry_limit', x.retry_limit, 'allocation_profile', x.allocation_profile))
   FILTER (WHERE x.assignment_id IS NOT NULL), '[]'::jsonb) AS assignments
 FROM {s}.director_primary_parallel_batch b
 LEFT JOIN {s}.director_primary_task_projection p USING (task_id)
 LEFT JOIN {s}.director_primary_parallel_assignment x USING (task_id) {where}
 GROUP BY b.task_id, b.plan_digest, p.projection ORDER BY b.task_id LIMIT %(limit)s"""


def plan_rows(connection, schema, *, tasks=None, limit=None):
    where = "WHERE b.task_id = ANY(%(tasks)s)" if tasks is not None else ""
    return [dict(row) for row in connection.execute(_PLAN_ROWS.format(s=schema, where=where),
            {"tasks": list(tasks or []), "limit": limit or EVIDENCE_ROW_CAP + 1}).fetchall()]


def registered_plan_error(row):
    """Recompute the registered plan digest from durable rows with register_plan's own hashing
    (primary_parallel.registered_plan_digest): the task record's DirectorPlan, digested as
    primary_default._register_parallel_lifecycle digests it, and every assignment row's DAG identity. It must equal
    the batch's plan_digest and, once bound, the projection's parallel_plan_digest. None, or the disagreement."""
    from .models import TaskRecord
    from .primary_default import _digest as plan_document_digest
    from .primary_parallel import ParallelAssignment, registered_plan_digest
    try:
        record = TaskRecord.model_validate(row["task_record"])
        if record.plan is None:
            return "a registered plan's task record carries no plan"
        assignments = [ParallelAssignment(
            assignment_id=item["assignment_id"], sequence=item["sequence"], provider_id=item["provider_id"],
            deployment_id=item["deployment_id"], dependencies=tuple(item["dependencies"] or ()),
            write_surfaces=tuple(item["write_surfaces"] or ()), retry_limit=item["retry_limit"],
            allocation_profile=item["allocation_profile"]) for item in row["assignments"]]
        expected = registered_plan_digest(task_id=row["task_id"],
                                          plan_digest=plan_document_digest(record.plan.model_dump(mode="json")),
                                          assignments=assignments)
    except Exception:  # noqa: BLE001 - rows that cannot be rebuilt into the registered plan prove no plan
        return "the registered plan cannot be rebuilt from its assignment rows and task record"
    if expected != row.get("batch_plan_digest"):
        return "the batch's plan digest is not the plan its assignment rows and task record register"
    if row.get("projection_plan_digest") is not None and row["projection_plan_digest"] != expected:
        return "the projection's parallel plan digest is not the registered plan"
    return None


def packet_rows(connection, schema, kind, *, tasks=None, limit=None):
    """Stored result or work packets (``kind``), each revalidated against its digest and authority binding exactly
    as the Director reloads it (_result_packet_artifact_from_row, _work_packet_artifact_from_row): task and assignment
    IDs, authority_snapshot_digest, document_digest, verified, recorded_at, and for a result packet its provider and
    outcome. ``tasks`` limits the read to those tasks (per-task path); None reads every task (global path)."""
    from .langgraph_postgres import (PostgresControlPlaneError, _result_packet_artifact_from_row,
                                     _work_packet_artifact_from_row)
    table, load = {"result": ("director_primary_result_packet_artifact", _result_packet_artifact_from_row),
                   "work": ("director_primary_work_packet_artifact", _work_packet_artifact_from_row)}[kind]
    where = "WHERE p.task_id = ANY(%(tasks)s)" if tasks is not None else ""
    rows = connection.execute(_PACKET_SQL.format(s=schema, table=table, where=where),
                              {"tasks": list(tasks or []), "limit": limit or EVIDENCE_ROW_CAP + 1}).fetchall()
    out = []
    for row in rows:
        try:
            # Revalidated as the Director reloads it, and written under a writer generation the task has held.
            packet, verified = load(row).packet, row["writer_generation_bounded"] is True
        except (PostgresControlPlaneError, KeyError, TypeError, ValueError):
            packet, verified = {}, False
        item = {"task_id": row["task_id"], "assignment_id": row["assignment_id"],
                "authority_snapshot_digest": row["authority_snapshot_digest"],
                "document_digest": row["document_digest"], "verified": verified,
                "recorded_at": _plain(row["recorded_at"])}
        if kind == "result":
            item.update(provider=packet.get("provider") if verified else None,
                        outcome=packet.get("outcome") if verified else None)
        out.append(item)
    return out


# Every task's evidence, validated by the same per-task rules the collector applies to one task's readback.
EVIDENCE_ROW_CAP = 20000
EVIDENCE_COUNT_KEYS = {"attempts": "provider_attempts_invalid", "usage": "provider_usage_invalid",
                       "effects": "effects_invalid", "deliveries": "slack_deliveries_invalid",
                       "lifecycle": "lifecycle_invalid", "settlement": "settlement_invalid"}
EVIDENCE_COUNT_KEYS["work_packets"] = "work_packets_invalid"
EVIDENCE_IN_FLIGHT_KEYS = {"usage": "provider_usage_in_flight", "deliveries": "slack_deliveries_in_flight"}
_TASK_MODES = """SELECT task_id, projection->'application'->>'execution_mode' AS execution_mode, projection,
 projection_digest, created_at,
 projection->'application'->'task_record'->'intake'->>'channel_id' AS source_channel_id,
 projection->'application'->'task_record'->'intake'->>'thread_ts' AS source_thread_ts
 FROM {s}.director_primary_task_projection ORDER BY task_id LIMIT %(limit)s"""


def _global_task_evidence(connection, schema, usage_tables):
    """Run the per-task validation over every task: task_evidence_errors (attempt histories, usage, effects,
    deliveries and receipts, lifecycle order, settlement) and artifact_assignment_conflict for each result packet,
    on the same sections the readback serves (qualification_section_sql, all tasks), in this snapshot.

    Each projection's canonical digest (langgraph_postgres._projection_payload, the digest the writer stores) is
    recomputed from its content: a mismatch is a conflict (projection_digest_mismatches), and the per-task readback
    refuses such a projection the same way.

    Returns {count key: number of tasks (packets, for the two artifact counts) failing it}; every count is None when
    any read exceeds EVIDENCE_ROW_CAP rows, so a count never covers a subset."""
    from .langgraph_postgres import PostgresControlPlaneError, _projection_payload, qualification_section_sql
    keys = [*EVIDENCE_COUNT_KEYS.values(), *EVIDENCE_IN_FLIGHT_KEYS.values(), "result_artifacts_invalid",
            "result_artifacts_in_flight", "projection_digest_mismatches", "registered_plan_mismatches"]
    limit = {"limit": EVIDENCE_ROW_CAP + 1}
    names = ["assignments", "provider_attempts", "effects", "reconciliation_events", "slack_deliveries",
             "status_receipts"]
    names += ["provider_usage"] if usage_tables else []
    tasks = {}

    def task(task_id):
        return tasks.setdefault(task_id, {"assignments": [], "provider_attempts": [], "effects": [],
                                          "reconciliation_events": [], "task_created_at": None,
                                          "slack_deliveries": [], "status_receipts": [], "provider_usage": [],
                                          "execution_mode": None, "result_artifacts": [], "work_packets": [],
                                          "absent": [] if usage_tables else ["provider_usage"]})

    reads = [(name, qualification_section_sql(schema, name, all_tasks=True)) for name in names]
    reads += [("execution_mode", _TASK_MODES.format(s=schema))]
    projections = []
    for name, query in reads:
        rows = connection.execute(query, limit).fetchall()
        if len(rows) > EVIDENCE_ROW_CAP:
            return {key: None for key in keys}
        for row in rows:
            if name == "execution_mode":
                task(row["task_id"])["execution_mode"] = row["execution_mode"]
                task(row["task_id"])["task_created_at"] = _plain(row["created_at"])
                task(row["task_id"])["projection_digest"] = row["projection_digest"]
                # The task's own source; the evaluator's owner is not known in-image, so the user is checked on the
                # scorecard's per-task path only.
                task(row["task_id"]).update(source_channel_id=row["source_channel_id"] or "",
                                            source_thread_ts=row["source_thread_ts"] or "")
                projections.append(row)
            else:
                task(row["task_id"])[name].append({key: _plain(value) for key, value in row.items() if key != "task_id"})
    for kind, name in (("result", "result_artifacts"), ("work", "work_packets")):
        rows = packet_rows(connection, schema, kind)
        if len(rows) > EVIDENCE_ROW_CAP:
            return {key: None for key in keys}
        for row in rows:
            task(row["task_id"])[name].append(row)
    counts = dict.fromkeys(keys, 0)
    plans = plan_rows(connection, schema)
    if len(plans) > EVIDENCE_ROW_CAP:
        return {key: None for key in keys}
    counts["registered_plan_mismatches"] = sum(registered_plan_error(row) is not None for row in plans)
    for row in projections:
        try:
            canonical = _projection_payload(row["projection"] if isinstance(row["projection"], dict)
                                            else json.loads(row["projection"]))[1]
        except (PostgresControlPlaneError, TypeError, ValueError):
            canonical = None
        counts["projection_digest_mismatches"] += canonical != row["projection_digest"]
    for task_id, sections in tasks.items():
        latest, errors, states = task_evidence_errors({**sections, "task_id": task_id, "schema": schema})
        for category, key in EVIDENCE_COUNT_KEYS.items():
            state = states.get(category)
            counts[key] += errors[category] is not None or (state is not None and state[0] == "conflict")
        for category, key in EVIDENCE_IN_FLIGHT_KEYS.items():
            state = states.get(category)
            counts[key] += errors[category] is None and state is not None and state[0] == "in_flight"
        by_id = {row["assignment_id"]: row for row in sections["assignments"]}
        for row in sections["result_artifacts"]:
            assignment = by_id.get(row["assignment_id"])
            if assignment is None:  # a packet for no durable assignment of its task (no such foreign key exists)
                counts["result_artifacts_invalid"] += 1
                continue
            conflict = artifact_assignment_conflict(row, assignment)
            counts["result_artifacts_in_flight"] += conflict == "in_flight"
            counts["result_artifacts_invalid"] += conflict not in (None, "in_flight")
        # The inverse join (4110553565): a live roster success whose packet is missing.
        counts["result_artifacts_invalid"] += len(missing_result_packets(sections, latest))
    return counts


def _intake_authority(connection, schema, expected_token) -> dict | None:
    """The persisted intake-authority row, read in the caller's snapshot. The lease
    token is compared here with the owner-supplied setting and never returned. The lease's actual
    absolute expiry is retained (it is not a secret) so the collector can revalidate a PASS through
    its own FRESH_FOR/valid_until finalization path instead of trusting a single snapshot-time flag."""
    row = connection.execute(
        f"SELECT intake_owner, owner_token, fencing_generation, legacy_new_intake_disabled, lease_expires_at,"
        f" lease_expires_at > now() AS lease_live FROM {schema}.director_primary_intake_authority"
        " WHERE singleton").fetchone()
    if row is None:
        return None
    token = row["owner_token"]
    return {"intake_owner": row["intake_owner"],
            "legacy_new_intake_disabled": row["legacy_new_intake_disabled"],
            "fencing_generation": _plain(row["fencing_generation"]),
            "lease_live": row["lease_live"],
            "lease_expires_at": _plain(row["lease_expires_at"]),
            "owner_token_matches": bool(isinstance(expected_token, str) and len(expected_token) >= 32
                                        and isinstance(token, str)
                                        and hmac.compare_digest(token.encode(), expected_token.encode()))}


def consistency_report(settings, identity, *, tasks=(), connection=None, environment=None,
                       local_artifact=None, authority_secrets=None) -> dict:
    """One REPEATABLE READ, READ ONLY snapshot of the primary control plane."""
    import psycopg
    from psycopg.rows import dict_row
    from .langgraph_postgres import PostgresControlPlaneError, _require_schema, _result_packet_artifact_from_row

    schema = _require_schema(settings.langgraph_primary_postgres_schema)
    environment = os.environ if environment is None else environment
    artifact = Path if local_artifact is None else local_artifact
    tasks = list(tasks)
    adoption = None
    if settings.langgraph_primary_acceptance_mode == "owner-local-evidence-v1":
        from .primary_activation_evidence import load_pinned_owner_local_adoption
        loaded = load_pinned_owner_local_adoption(artifact(settings.langgraph_primary_local_evidence_adoption_path),
            expected_sha256=settings.langgraph_primary_local_evidence_adoption_sha256,
            owner_token=settings.langgraph_primary_owner_token)
        adoption = {key: loaded[key] for key in _ADOPTION_FIELDS}
    owns_connection = connection is None
    connection = connection or psycopg.connect(settings.langgraph_primary_postgres_dsn, connect_timeout=10, row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=5000")
    try:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.read_only = True
        per_task = {}
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout = '10s'")
            connection.execute("SET LOCAL lock_timeout = '5s'")
            _require_instance(connection, schema, identity)
            counts = connection.execute(_COUNTS.format(s=schema)).fetchone()
            authority = _intake_authority(connection, schema, settings.langgraph_primary_owner_token)
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
                    route, owner = (authority_secrets if authority_secrets is not None else
                                    (_secret(settings, "entitlement_route_verifier_secret_file"),
                                     _secret(settings, "entitlement_owner_authority_secret_file")))
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
                receipts = [_row(r) for r in connection.execute(
                    _RECEIPTS_BOUNDED.format(s=schema), {"tasks": tasks, "cap": _RECEIPT_CAP}).fetchall()]
                truncated_receipts = {r["task_id"] for r in receipts if r["task_ordinal"] > _RECEIPT_CAP}
                receipts = [r for r in receipts if r.pop("task_ordinal") <= _RECEIPT_CAP]
                # The persisted application record (task record, execution mode, effect grant): the scorecard
                # re-renders approval prompts from it rather than from hand-picked fields.
                applications = {r["task_id"]: r["application"] for r in connection.execute(
                    f"SELECT task_id, projection->'application' AS application FROM {schema}.director_primary_task_projection"
                    " WHERE task_id = ANY(%(tasks)s)", {"tasks": tasks}).fetchall()}
                # Each persisted provider result packet, revalidated against its stored digest exactly as the
                # Director reloads it; content that fails revalidation attests nothing and carries no outcome.
                artifacts = packet_rows(connection, schema, "result", tasks=tasks)
                work_packets = packet_rows(connection, schema, "work", tasks=tasks)
                registered = {row["task_id"]: registered_plan_error(row)
                              for row in plan_rows(connection, schema, tasks=tasks)}
                orphan_terminals = {row["task_id"]: int(row["n"]) for row in connection.execute(
                    f"SELECT t.task_id, count(*) AS n FROM {schema}.director_primary_terminal_evidence t"
                    f" WHERE t.task_id = ANY(%(tasks)s) AND NOT EXISTS (SELECT 1 FROM"
                    f" {schema}.director_primary_effect_reservation r WHERE r.claim_key = t.claim_key)"
                    " GROUP BY t.task_id", {"tasks": tasks}).fetchall()}
                # Which effect each external-effect approval authorized (the graph's effect approval binding).
                approval_bindings = [_row(r) for r in connection.execute(
                    f"SELECT task_id, effect_key_digest, approval_request_digest FROM "
                    f"{schema}.director_primary_effect_approval_binding WHERE task_id = ANY(%(tasks)s)",
                    {"tasks": tasks}).fetchall()]
                for task_id in tasks:
                    per_task[task_id] = {"usage_tables_present": usage_tables, "verifier_secrets": verifiers,
                                         "result_artifacts": [{k: v for k, v in a.items() if k != "task_id"}
                                                              for a in artifacts if a["task_id"] == task_id],
                                         "work_packets": [{k: v for k, v in a.items() if k != "task_id"}
                                                          for a in work_packets if a["task_id"] == task_id],
                                         "effect_approval_bindings": [
                                             {k: v for k, v in b.items() if k != "task_id"}
                                             for b in approval_bindings if b["task_id"] == task_id],
                                         "application": applications.get(task_id),
                                         # None when the plan recomputes, else the disagreement; absent: no batch.
                                         "registered_plan": ({"error": registered[task_id]}
                                                             if task_id in registered else None),
                                         # Terminal evidence whose claim no reservation holds (review 4111174699).
                                         "terminal_evidence_without_reservation": orphan_terminals.get(task_id, 0),
                                         "spend": [r for r in spend if r["task_id"] == task_id],
                                         "budgets": [r for r in budgets if r["task_id"] == task_id],
                                         "approval_receipts": [{k: v for k, v in r.items() if k != "task_id"}
                                                               for r in receipts if r["task_id"] == task_id]}
    finally:
        if owns_connection:
            connection.close()
    return {"version": "director-primary-consistency/v1",
            "generation": {"manifest_sha256": environment[_PIN],
                           "acceptance_mode": settings.langgraph_primary_acceptance_mode,
                           "candidate_commit": identity.candidate_commit,
                           "candidate_tree": identity.candidate_tree, "adoption": adoption,
                           # require_generation verified the manifest against this image before any read.
                           "image_id": environment.get("DIRECTOR_RUNTIME_IMAGE_ID")},
            "captured_at": _plain(counts["captured_at"]),
            "intake_fencing_generation": _plain(counts["intake_fencing_generation"]),
            "intake_authority": authority,
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


# The official OpenAI Responses endpoint (primary_transports.OpenAIResponsesPacketTransport's default).
OFFICIAL_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


# Owner ruling 2026-09-26 ("Bind to pinned release"): the Codex, grok and Claude CLIs are held to one standard. The
# model a CLI reports about itself never names its product; the executable that ran does, through an owner-pinned
# release descriptor (A2/R1 work, not yet provisioned). Each CLI lane's one real product:
CLI_PRODUCTS = {"openai": "openai-codex", "grok": "xai-grok-cli", "claude": "anthropic-claude"}
CLI_RELEASE_DESCRIPTOR_VERSION = "director-cli-release-descriptor/v1"
UNATTESTED = "-unattested"


OWNER_HOME_ENV = "DIRECTOR_SCORECARD_OWNER_HOME"
DESCRIPTOR_ENV = "DIRECTOR_SCORECARD_DESCRIPTOR_SHA256"


def owner_home():
    """The account home the owner-installed launcher resolved from the operating system and handed to this
    evaluator, never HOME/USERPROFILE (#287 item 34). None when the launcher did not start this run: such runs are
    unattested and read no owner files."""
    value = os.environ.get(OWNER_HOME_ENV)
    return Path(value) if value and os.environ.get(DESCRIPTOR_ENV) and Path(value).is_absolute() else None


def cli_release_descriptor():
    """The owner-pinned CLI release descriptor, or None while none is provisioned (every CLI capture is then
    unattested). Its exact shape, which cli_release_product enforces:

        {"version": "director-cli-release-descriptor/v1",
         "releases": [{"provider": "claude", "product": "anthropic-claude",
                       "executable_sha256": "<64 lowercase hex: the digest of the executable that ran>"}, ...],
         "ci_workflow_sha256": "<optional: 64 lowercase hex, the owner-pinned .github/workflows/ci.yml content>",
         "security_base_sha": "<optional: 40 lowercase hex, check 18's reviewed security base commit>",
         "workload_journal": "<optional: absolute path of the deployment's workload journal; checks 13 and 14 read only it>",
         "reviewed_release": {"candidate_commit": "<40 hex>", "candidate_tree": "<40 hex>",
                              "director_image_id": "sha256:<64 hex>"},
         "evaluator_sha": "<40 lowercase hex: the scorecard revision that grades; required to grade at all>"}

    ``ci_workflow_sha256`` (check 16, ci_workflow_pin) trusts a ci.yml a candidate changed; without it only the
    current main's ci.yml is trusted.

    It is the owner-installed ``.director-scorecard/release-descriptor.json`` under owner_home() (fixed, no
    override, the stance of the replica signer key), and its exact bytes must hash to the digest the launcher pinned
    for this run (#287 item 33); absent, a link, oversized, malformed, of another version or other bytes, there is
    none. The owner-installed launcher (scripts/scorecard-launcher.py) and scripts/director-scorecard.py read the
    same file with the standard library alone before any repository module is imported.
    """
    home = owner_home()
    if home is None:
        return None
    path = home / ".director-scorecard" / "release-descriptor.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            return None
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != os.environ.get(DESCRIPTOR_ENV):
            return None
        value = json.loads(raw)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("version") == CLI_RELEASE_DESCRIPTOR_VERSION else None


def ci_workflow_pin(descriptor):
    """The owner-pinned ci.yml content digest from the release descriptor, or None (none pinned, or malformed)."""
    value = descriptor.get("ci_workflow_sha256") if isinstance(descriptor, dict) else None
    if (not isinstance(descriptor, dict) or descriptor.get("version") != CLI_RELEASE_DESCRIPTOR_VERSION
            or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
        return None
    return value


def evaluator_pin(descriptor):
    """The owner-pinned evaluator revision (``evaluator_sha``, 40 lowercase hex): the only checkout the scorecard
    grades from, so the collector, rubric requirements and every predicate are never the candidate's own code."""
    value = descriptor.get("evaluator_sha") if isinstance(descriptor, dict) else None
    if (not isinstance(descriptor, dict) or descriptor.get("version") != CLI_RELEASE_DESCRIPTOR_VERSION
            or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value)):
        return None
    return value


def security_base_pin(descriptor):
    """The owner-pinned reviewed base commit for check 18's cumulative security diff (``security_base_sha``, 40
    lowercase hex), or None."""
    value = descriptor.get("security_base_sha") if isinstance(descriptor, dict) else None
    if (not isinstance(descriptor, dict) or descriptor.get("version") != CLI_RELEASE_DESCRIPTOR_VERSION
            or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value)):
        return None
    return value


def workload_journal_pin(descriptor):
    """The owner-pinned location of the deployment's real workload journal (``workload_journal``, an absolute path),
    or None. Checks 13 and 14 read only this journal, so a separately bootstrapped one cannot award them (#287 item 35)."""
    value = descriptor.get("workload_journal") if isinstance(descriptor, dict) else None
    if (not isinstance(descriptor, dict) or descriptor.get("version") != CLI_RELEASE_DESCRIPTOR_VERSION
            or not isinstance(value, str) or not value or len(value) > 1024 or chr(0) in value
            or not os.path.isabs(value)):
        return None
    return Path(value)


_REVIEWED_RELEASE_FIELDS = {"candidate_commit": r"[0-9a-f]{40}", "candidate_tree": r"[0-9a-f]{40}",
                            "director_image_id": r"sha256:[0-9a-f]{64}"}


def reviewed_release_pin(descriptor):
    """The owner-pinned reviewed release (``reviewed_release``): the candidate commit and tree and the Director image
    digest whose behaviour the owner reviewed. Exactly these three fields, each fully matching, or None. Director-written
    evidence is admitted only for a runtime that is exactly this release (owner ruling 2026-10-02, #313)."""
    value = descriptor.get("reviewed_release") if isinstance(descriptor, dict) else None
    if (not isinstance(descriptor, dict) or descriptor.get("version") != CLI_RELEASE_DESCRIPTOR_VERSION
            or not isinstance(value, dict) or set(value) != set(_REVIEWED_RELEASE_FIELDS)
            or any(not isinstance(value[key], str) or not re.fullmatch(pattern, value[key])
                   for key, pattern in _REVIEWED_RELEASE_FIELDS.items())):
        return None
    return {key: value[key] for key in _REVIEWED_RELEASE_FIELDS}


def cli_release_product(provider, executable_sha256, descriptor):
    """The one attestation path for all three CLIs: the lane's real product (CLI_PRODUCTS) when the capture carries
    the executable digest recorded at execution and exactly one descriptor entry maps that digest to this provider
    and its product, with no other keys; None otherwise (no descriptor, no digest, a malformed descriptor or entry,
    a digest pinned twice, or pinned to another provider or product)."""
    product = CLI_PRODUCTS.get(provider)
    if (product is None or not isinstance(executable_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", executable_sha256)
            or not isinstance(descriptor, dict) or descriptor.get("version") != CLI_RELEASE_DESCRIPTOR_VERSION
            or not isinstance(descriptor.get("releases"), list)):
        return None
    matches = [entry for entry in descriptor["releases"]
               if isinstance(entry, dict) and entry.get("executable_sha256") == executable_sha256]
    expected = {"provider": provider, "product": product, "executable_sha256": executable_sha256}
    return product if len(matches) == 1 and matches[0] == expected else None


def _capture_product(provider, output: bytes, *, official_openai: bool = False, executable_sha256=None,
                     descriptor=None):
    """Name the product that produced a verified native capture, or None (a substitution for check 11).

    The capture's dialect must be the lane's, and where it names a model that model must be the vendor's: another
    vendor's model is a substitution. Neither the dialect nor a self-reported model ever names the product, though:
    a Responses envelope is ``openai-responses`` only when ``official_openai`` (the verified capture's route, whose
    signed configuration digest pins its endpoint, targets exactly the official OpenAI endpoint), and a CLI capture
    (the Codex JSONL stream, grok's CLI or Responses-shaped output, Claude's result envelope) is its lane's CLI
    product only through cli_release_product. Anything else is labelled ``-unattested``, which check 11 reads as
    UNKNOWN on its own transport and a substitution on another product's.
    """
    try:
        native = json.loads(output)
    except ValueError:
        native = None  # Codex CLI output is JSONL, not one document.
    envelope = native if isinstance(native, dict) else {}
    model = envelope.get("model")

    def cli() -> str:
        product = cli_release_product(provider, executable_sha256, descriptor)
        if product:
            return product
        # Missing historical attestations remain unknown. An authenticated digest
        # contradicting a well-formed pinned release is positive mismatch evidence.
        releases = descriptor.get("releases", []) if isinstance(descriptor, dict) else []
        if not isinstance(releases, list):
            releases = []
        pinned = [entry for entry in releases if isinstance(entry, dict) and entry.get("provider") == provider
                  and cli_release_product(provider, entry.get("executable_sha256"), descriptor)]
        if executable_sha256 is not None and pinned:
            return CLI_PRODUCTS[provider] + "-digest-mismatch"
        return CLI_PRODUCTS[provider] + UNATTESTED

    if provider == "openai":
        if envelope.get("object") == "response":
            if not (isinstance(model, str) and _OPENAI_MODEL.match(model)):
                return None
            return "openai-responses" if official_openai else "openai-responses" + UNATTESTED
        # The Codex CLI's JSONL names no product and no model (native_codex_transport records "actual model and cost
        # are not reported by this event stream").
        return cli() if _codex_stream(output) else None
    if provider == "grok":
        # A grok validation capture is its pinned native command's output, whether Responses-shaped or the CLI's own
        # dialect (stopReason and an integer num_turns); either must name a Grok model.
        responses = envelope.get("object") == "response" and isinstance(model, str) and model.startswith("grok")
        dialect = ("stopReason" in envelope and type(envelope.get("num_turns")) is int
                   and isinstance(model, str) and "grok" in model.lower())
        return cli() if responses or dialect else None
    if provider == "claude":
        # A result envelope must name its models: a non-empty modelUsage whose keys are all Claude models.
        usage = envelope.get("modelUsage")
        if (envelope.get("type") != "result" or not isinstance(usage, dict) or not usage
                or any(not str(key).startswith("claude") for key in usage)):
            return None
        return cli()
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


def activation_capture_report(settings, identity, manifest, *, verifier=None, environment=None,
                              descriptor=None) -> dict:
    """Latest per-lane capture digests and expiry through a load-only store."""
    from .langgraph_postgres import open_provider_activation_store
    from .primary_activation_evidence import ActivationEvidenceError
    from .primary_provider_activation import PrimaryProviderActivationService
    from .primary_provider_evidence import _BoundedCaptureReader
    from .provider_validation import capture_verifier_from_environment

    explicit = environment is not None
    environment = os.environ if environment is None else environment
    if verifier is None and not explicit:
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
        # The product is named from the exact bytes the verifier read and verified, in the same call.
        # A verified Responses capture passed the route's configuration-digest check (CaptureVerifier: without a
        # bound route it does not normalise at all), and that digest pins the route's api_url.
        route = getattr(verifier, "openai_responses_route", None)
        official = getattr(getattr(route, "transport", None), "api_url", None) == OFFICIAL_OPENAI_RESPONSES_URL
        lanes = service.capture_attestations(
            classify_attested=lambda provider, output, executable_sha256: _capture_product(
                provider, output, official_openai=official,
                executable_sha256=executable_sha256, descriptor=descriptor if explicit else cli_release_descriptor()))
    bindings = settings.primary_provider_activation_bindings()
    for provider, lane in lanes.items():
        # Both activation surfaces must classify as the same attested product (review 4111508065): a lane whose app
        # capture names another product, or none, has no product.
        products = [surface.get("product") if isinstance(surface, dict) and surface.get("capture_verified") is True
                    else None for surface in (lane["surfaces"].get("app"), lane["surfaces"].get("runtime_adapter"))]
        lane["product"] = products[0] if products[0] is not None and products[0] == products[1] else None
        # The persisted insertion time, not the caller-supplied observed_at, says when each surface gated anything;
        # provider_ready needs both surfaces, so both are reported.
        for name, each in lane["surfaces"].items():
            if each is not None and each.get("chain_valid") is True:
                each["recorded_at"] = _activation_recorded_at(settings.langgraph_primary_postgres_dsn,
                                                              identity.postgres_schema, bindings[provider],
                                                              each["evidence_digest"], name, identity=identity)
    return {"version": "director-activation-captures/v1", "generation_manifest_sha256": environment[_PIN],
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
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:36 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 04:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 05:07 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 06:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 07:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 08:24 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 09:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 10:29 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 11:20 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 13:51 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 15:16 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:00 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:06 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:49 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:53 UTC | END]]
