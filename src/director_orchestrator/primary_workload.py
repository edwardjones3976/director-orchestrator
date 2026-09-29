# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:10 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:52 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:07 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:06 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
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

import hashlib
import json
from typing import Any, Sequence
from types import MappingProxyType

from .langgraph_postgres import _require_schema
from .allocation_profiles import (
    allocation_targets, allocation_profile_digest, DEFAULT_ALLOCATION_PROFILE,
    PRE_RETIREMENT_V3 as _PRE_RETIREMENT_V3, PRE_ZAI_LANES,
    pre_zai_allocation_profile_digest,
)

POLICY_VERSION = "primary-assignment-count-v3"
TARGETS = allocation_targets()
POLICY_DIGEST = allocation_profile_digest()
LEGACY_V2_DIGEST = "sha256:" + hashlib.sha256(json.dumps({
    "version": "primary-assignment-count-v2", "targets": {"openai":25,"claude":25,"cursor":20,"antigravity":15,"grok":11,"perplexity":4},
    "window":20,"hard_tolerance":5,"unit":"admitted_assignment","bootstrap":"fixed_twenty_slot_denominator",
},sort_keys=True,separators=(",",":")).encode()).hexdigest()

# _PRE_RETIREMENT_V3 is the shared allocation_profiles.PRE_RETIREMENT_V3 registry,
# re-exported here for existing callers of this module.
_CURRENT_AT_BASE_V3 = MappingProxyType({
    "engineering": "sha256:1928779a3d24e9172a8f74911b601bc01905dc2dbfd49c306f0dcff73e442a68",
    "mixed": "sha256:f228e056553673abdffce8f84af45801deb73e0b2bd612930e07fcc36980e976",
    "public_research": "sha256:81687a465f4fcbae938733dc83cddc2ad293e519983f4f9c79394f41a9d9474c",
    "enterprise": "sha256:db08376ea3c632828f326d6e08f4ead2a865bd8fbb7faa1396969275c5a8815a",
})

_HISTORY_LANES = frozenset(TARGETS) | {"microsoft-365-copilot"}

def _valid_history_row(row):
    if row["policy_version"] == "primary-assignment-count-v2":
        return (row["policy_digest"] == LEGACY_V2_DIGEST and
                row["provider_lane"] in {"openai", "claude", "cursor", "antigravity", "grok", "perplexity"})
    try:
        if row["policy_version"] != POLICY_VERSION:
            return False
        profile = row["allocation_profile"]
        if row["policy_digest"] == allocation_profile_digest(profile):
            return row["provider_lane"] in TARGETS
        # Identities recorded before Z.ai enrollment name only the eight earlier lanes.
        if row["policy_digest"] in (_CURRENT_AT_BASE_V3.get(profile), pre_zai_allocation_profile_digest(profile)):
            return row["provider_lane"] in PRE_ZAI_LANES
        old_digest, old_lanes = _PRE_RETIREMENT_V3[profile]
        return row["policy_digest"] == old_digest and row["provider_lane"] in old_lanes
    except ValueError:
        return False



class WorkloadAdmissionHold(RuntimeError):
    """A proven pre-I/O policy hold, never an ambiguous provider outcome."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class ProvenNoRunHold(WorkloadAdmissionHold):
    """A hold raised after an attempt was admitted but before any provider
    request left this process, or refused by the host with authenticated
    proof that nothing ran. Its admitted attempt is settled as refused
    before I/O rather than left STARTED."""


def admission_reason(provider: str, history: Sequence[str], *, allocation_profile: str = DEFAULT_ALLOCATION_PROFILE) -> str | None:
    targets = allocation_targets(allocation_profile)
    if provider not in targets:
        return "workload_unsupported_allocation_lane"
    # Durable readers authenticate each row's profile digest before exposing its
    # lane. Retired work still occupies its original position in the last 19.
    if any(item not in _HISTORY_LANES for item in history):
        return "workload_history_invalid"
    if targets[provider] == 0:
        return "workload_profile_lane_ineligible"
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
    expected = "CHECK ((policy_version = ANY (ARRAY['primary-assignment-count-v2'::text, 'primary-assignment-count-v3'::text])))"
    if constraint is None or not constraint["convalidated"] or constraint["definition"] != expected:
        raise WorkloadAdmissionHold("workload_schema_policy_incompatible")


def read_admission_history(connection: Any, *, schema: str) -> tuple[str, ...]:
    schema = _require_schema(schema)
    with connection.transaction():
        with connection.cursor() as cursor:
            _require_compatible_admission_schema(cursor, schema=schema)
            cursor.execute(f"SELECT provider_lane, policy_digest, policy_version, allocation_profile FROM {schema}.director_primary_workload_assignment ORDER BY ledger_id DESC LIMIT 19")
            rows = cursor.fetchall()
    if any(not _valid_history_row(row) for row in rows):
        raise WorkloadAdmissionHold("workload_history_invalid")
    return tuple(row["provider_lane"] for row in reversed(rows))


def admit_assignment(connection: Any, *, schema: str, task_id: str,
                     assignment_id: str, provider: str,
                     reservation_digest: str, fencing_generation: int,
                     allocation_profile: str = DEFAULT_ALLOCATION_PROFILE) -> None:
    """Caller owns the writer-fenced transaction; do not commit independently.

One database-wide lock serializes every task. The configured control-plane
schema is trusted and validated by its owner, never supplied by a work packet.
    """
    policy_digest = allocation_profile_digest(allocation_profile)
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
                    or held["policy_digest"] != policy_digest):
                raise WorkloadAdmissionHold("workload_admission_identity_changed")
            return
        cursor.execute(f"""SELECT provider_lane, policy_digest, policy_version, allocation_profile
            FROM {schema}.director_primary_workload_assignment
            ORDER BY ledger_id DESC LIMIT 19""")
        rows = cursor.fetchall()
        if any(not _valid_history_row(row) for row in rows):
            raise WorkloadAdmissionHold("workload_history_invalid")
        history = [row["provider_lane"] for row in reversed(rows)]
        reason = admission_reason(provider, history, allocation_profile=allocation_profile)
        if reason:
            raise WorkloadAdmissionHold(reason)
        cursor.execute(f"""INSERT INTO {schema}.director_primary_workload_assignment
            (task_id, assignment_id, provider_lane, reservation_digest,
             fencing_generation, policy_version, policy_digest, allocation_profile)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (task_id, assignment_id, provider, reservation_digest,
             fencing_generation, POLICY_VERSION, policy_digest, allocation_profile))
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:06 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:07 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:52 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:10 UTC | END]]
