# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
"""Transactional assignment admission for the primary control plane.

Admissions count assigned work, not accepted production or attempts. They are
never removed after provider failure. No synthetic bootstrap history is created.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any, Sequence

from .langgraph_postgres import _require_schema
from .workload_policy import DEFAULT_POLICY, DirectProductionProvider

POLICY_VERSION = "primary-assignment-count-v2"
TARGETS = {
    lane: DEFAULT_POLICY.direct_production_target(provider)
    for lane, provider in {
        "openai": DirectProductionProvider.EXTERNAL_OPENAI,
        "claude": DirectProductionProvider.CLAUDE,
        "cursor": DirectProductionProvider.CURSOR,
        "antigravity": DirectProductionProvider.GEMINI,
        "grok": DirectProductionProvider.GROK,
        "perplexity": DirectProductionProvider.PERPLEXITY,
    }.items()
}
POLICY_DIGEST = "sha256:" + hashlib.sha256(json.dumps({
    "version": POLICY_VERSION, "targets": TARGETS, "window": 20,
    "hard_tolerance": 5, "unit": "admitted_assignment",
    "bootstrap": "fixed_twenty_slot_denominator",
}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkloadAdmissionHold(RuntimeError):
    """A proven pre-I/O policy hold, never an ambiguous provider outcome."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def admission_reason(provider: str, history: Sequence[str]) -> str | None:
    if provider not in TARGETS:
        return "workload_unsupported_allocation_lane"
    if any(item not in TARGETS for item in history):
        return "workload_history_invalid"
    counts = Counter((*history[-19:], provider))
    if 100 * counts[provider] > (TARGETS[provider] + 5) * 20:
        return "workload_assignment_hard_limit"
    return None


def _require_compatible_admission_schema(cursor: Any, *, schema: str) -> None:
    # Keep the table definition stable through the caller's transaction. Merely
    # reopening an old empty v1 table does not upgrade its CREATE-IF-ABSENT CHECK.
    cursor.execute(f"LOCK TABLE {schema}.director_primary_workload_assignment IN ROW SHARE MODE")
    cursor.execute("""SELECT pg_get_constraintdef(c.oid) AS definition, c.convalidated
        FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = %s AND t.relname = 'director_primary_workload_assignment'
          AND c.conname = 'director_primary_workload_assignment_policy_version_check'
          AND c.contype = 'c'""", (schema,))
    constraint = cursor.fetchone()
    expected = f"CHECK ((policy_version = '{POLICY_VERSION}'::text))"
    if constraint is None or not constraint["convalidated"] or constraint["definition"] != expected:
        raise WorkloadAdmissionHold("workload_schema_policy_incompatible")


def read_admission_history(connection: Any, *, schema: str) -> tuple[str, ...]:
    schema = _require_schema(schema)
    with connection.transaction():
        with connection.cursor() as cursor:
            _require_compatible_admission_schema(cursor, schema=schema)
            cursor.execute(f"SELECT provider_lane, policy_digest FROM {schema}.director_primary_workload_assignment ORDER BY ledger_id DESC LIMIT 19")
            rows = cursor.fetchall()
    if any(row["policy_digest"] != POLICY_DIGEST for row in rows):
        raise WorkloadAdmissionHold("workload_history_invalid")
    return tuple(row["provider_lane"] for row in reversed(rows))


def admit_assignment(connection: Any, *, schema: str, task_id: str,
                     assignment_id: str, provider: str,
                     reservation_digest: str, fencing_generation: int) -> None:
    """Caller owns the writer-fenced transaction; do not commit independently.

One database-wide lock serializes every task. The configured control-plane
schema is trusted and validated by its owner, never supplied by a work packet.
    """
    schema = _require_schema(schema)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                       ("director-primary-workload-admission",))
        _require_compatible_admission_schema(cursor, schema=schema)
        cursor.execute(f"""SELECT provider_lane, reservation_digest, policy_version, policy_digest
            FROM {schema}.director_primary_workload_assignment
            WHERE task_id = %s AND assignment_id = %s""", (task_id, assignment_id))
        held = cursor.fetchone()
        if held is not None:
            if (held["provider_lane"] != provider
                    or held["reservation_digest"] != reservation_digest
                    or held["policy_version"] != POLICY_VERSION
                    or held["policy_digest"] != POLICY_DIGEST):
                raise WorkloadAdmissionHold("workload_admission_identity_changed")
            return
        cursor.execute(f"""SELECT provider_lane, policy_digest
            FROM {schema}.director_primary_workload_assignment
            ORDER BY ledger_id DESC LIMIT 19""")
        rows = cursor.fetchall()
        if any(row["policy_digest"] != POLICY_DIGEST for row in rows):
            raise WorkloadAdmissionHold("workload_history_invalid")
        history = [row["provider_lane"] for row in reversed(rows)]
        reason = admission_reason(provider, history)
        if reason:
            raise WorkloadAdmissionHold(reason)
        cursor.execute(f"""INSERT INTO {schema}.director_primary_workload_assignment
            (task_id, assignment_id, provider_lane, reservation_digest,
             fencing_generation, policy_version, policy_digest)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (task_id, assignment_id, provider, reservation_digest,
             fencing_generation, POLICY_VERSION, POLICY_DIGEST))
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
