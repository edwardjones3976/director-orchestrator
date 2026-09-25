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
    LEFT JOIN {s}.director_primary_terminal_evidence t USING (claim_key) WHERE t.claim_key IS NULL) AS unresolved_reconciliation,
 (SELECT count(*) FROM {s}.director_primary_parallel_assignment WHERE status='AMBIGUOUS') AS ambiguous_assignments,
 (SELECT count(*) FROM p WHERE tc='RECONCILIATION_REQUIRED') AS tasks_requiring_reconciliation,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_assignment x USING (task_id)
    WHERE p.settled AND x.status IN ('PENDING','ACTIVE','AMBIGUOUS') AND NOT EXISTS
      (SELECT 1 FROM blocked b WHERE b.task_id=x.task_id AND b.assignment_id=x.assignment_id)) AS terminal_tasks_open_assignments,
 (SELECT count(*) FROM {s}.director_primary_effect_reservation e JOIN p USING (task_id)
    LEFT JOIN {s}.director_primary_terminal_evidence t ON t.claim_key=e.claim_key
    WHERE t.claim_key IS NULL AND p.settled) AS terminal_tasks_open_effects,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_batch b USING (task_id)
    WHERE p.pd IS NULL AND p.settled) AS terminal_batches_unbound,
 (SELECT count(*) FROM p JOIN {s}.director_primary_parallel_batch b USING (task_id)
    WHERE p.pd IS NOT NULL AND p.pd<>b.plan_digest) AS projection_batch_digest_conflicts,
 (SELECT count(*) FROM p WHERE p.intake_fencing_generation>(SELECT fencing_generation FROM a)) AS projections_ahead_of_fence,
 (SELECT count(*) FROM {s}.director_primary_effect_reservation e JOIN p USING (task_id)
    LEFT JOIN {s}.director_primary_terminal_evidence t ON t.claim_key=e.claim_key
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
                        f" writer_fencing_generation FROM {schema}.director_primary_result_packet_artifact"
                        " WHERE task_id = ANY(%(tasks)s) ORDER BY task_id, assignment_id", {"tasks": tasks}).fetchall():
                    try:
                        outcome, verified = _result_packet_artifact_from_row(r).packet.get("outcome"), True
                    except (PostgresControlPlaneError, KeyError, TypeError, ValueError):
                        outcome, verified = None, False
                    artifacts.append({"task_id": r["task_id"], "assignment_id": r["assignment_id"],
                                      "document_digest": r["document_digest"], "outcome": outcome, "verified": verified})
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
            "counts": {key: int(counts[key]) for key in _COUNT_KEYS},
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
    substitution, and None is what check 11 counts as one.
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
        return "openai-codex" if _codex_stream(output) else None
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
            if each is not None:
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
