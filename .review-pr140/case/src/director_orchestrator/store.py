# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 08:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 08:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:21 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 06:56 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-29 | 06:42 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5 | 2026-08-29 | 06:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 07:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:08 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | BEGIN]]
from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass, replace
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator

from .interactions import (
    CommunicationEvidenceFailure,
    InteractionProvider,
    ProviderInteractionEvent,
    ProviderInteractionPhase,
    ProviderInteractionState,
    TransportEvidenceSource,
    acceptance_review_candidate_digest,
    append_interaction_event,
    canonical_content_digest,
    canonical_identifier_digest,
    director_evidence_matches,
    project_interaction,
)
from .models import (
    IntakeAuthentication,
    LeanExecutionLimits,
    TaskEffectEvidence,
    TaskRecord,
    TaskStatus,
    UsageCheckRecord,
)
from .allocation_profiles import DEFAULT_ALLOCATION_PROFILE, allocation_profile_digest, allocation_targets, canonical_allocation_lane
from .policy import trusted_work_kind
from .productive_work import (
    ExternalOpenAIProductionAuthorization,
    ExternalOpenAIProductionEvidence,
)
from .workload_policy import (
    DEFAULT_POLICY,
    WorkloadPolicy,
    AssignmentRequest,
    DecisionOutcome,
    DecisionResult,
    DirectProductionRecord,
    DirectProductionProvider,
    EnforcementMode,
    Provider,
    WorkKind,
    WorkSize,
    WorkloadRecord,
    direct_production_provider,
    evaluate_assignment,
    is_accepted_productive_kind,
)


_INTENT_KINDS = frozenset(
    {
        "provider_intent",
        "director_intent",
        "slack_intent",
        "oracle_merge_intent",
    }
)
_MAX_TASK_EFFECTS = 200
_MAX_PROVIDER_INTERACTION_EVENTS = 500


class StaleTaskWrite(RuntimeError):
    """An in-memory task snapshot no longer matches SQLite authority."""


class PrivacyCleanupPending(RuntimeError):
    """Private task projections remain pinned in SQLite's WAL."""


class SlackIntakeReplayConflict(RuntimeError):
    """A signed Slack nonce or source event was reused with different authority."""


class WorkloadReservationConflict(RuntimeError):
    """A workload idempotency key was reused with different assignment identity."""


class WorkloadAttemptOutcome(str, Enum):
    """Append-only terminal classification for a workload reservation."""

    INVOKED = "invoked"
    FAILED_PRE_SUBMIT = "failed_pre_submit"
    CACHED_REPLAY = "cached_replay"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WorkloadLedgerReservation:
    reservation_key: str
    task_id: str
    assignment_id: str
    phase: str
    provider: Provider
    effort_units: int
    kind: WorkKind
    size: WorkSize
    model_label: str | None
    available_primary_providers: frozenset[Provider]
    configured_mode: EnforcementMode
    shadow_assignment_limit: int
    decision: DecisionResult
    created: bool
    final_outcome: WorkloadAttemptOutcome | None
    recorded_at: datetime


@dataclass(frozen=True)
class PrivacyCleanupResume:
    task_id: str
    effect_kind: str
    effect_key: str
    phase: str
    lease_token: str


class TaskStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        # Legacy task JSON may contain provider prose that migration replaces
        # with opaque digests. SQLite's default leaves deleted cell content in
        # free pages, so every writer enables physical page scrubbing before
        # any migration or update.
        connection.execute("PRAGMA secure_delete = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            # Serialize schema migration across concurrently starting workers.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "record_version" not in columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN record_version INTEGER NOT NULL DEFAULT 0"
                )
            if "effect_migration_status" not in columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN effect_migration_status TEXT NOT NULL DEFAULT 'pending'"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_cleanup_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    pending_generation INTEGER NOT NULL DEFAULT 0,
                    completed_generation INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """INSERT OR IGNORE INTO privacy_cleanup_state(
                    singleton, pending_generation, completed_generation, updated_at
                ) VALUES (1, 0, 0, ?)""",
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_cleanup_waits (
                    task_id TEXT PRIMARY KEY,
                    effect_kind TEXT NOT NULL,
                    effect_key TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    lease_token TEXT,
                    lease_until TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            wait_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(privacy_cleanup_waits)"
                ).fetchall()
            }
            # Development builds briefly shipped a task/requested_at-only
            # wait table. Preserve those durable waits during upgrade and make
            # them fail closed until a fresh effect reservation replaces the
            # placeholder identity.
            wait_migrations = {
                "effect_kind": "TEXT NOT NULL DEFAULT 'provider_intent'",
                "effect_key": f"TEXT NOT NULL DEFAULT '{'0' * 64}'",
                "phase": "TEXT NOT NULL DEFAULT 'dispatch'",
                "lease_token": "TEXT",
                "lease_until": "TEXT",
            }
            for name, declaration in wait_migrations.items():
                if name not in wait_columns:
                    connection.execute(
                        f"ALTER TABLE privacy_cleanup_waits ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_cleanup_effect_resolutions (
                    task_id TEXT NOT NULL,
                    original_effect_key TEXT NOT NULL,
                    recovery_effect_key TEXT NOT NULL,
                    recovery_event_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, original_effect_key),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_effect_events (
                    task_id TEXT NOT NULL,
                    event_digest TEXT NOT NULL,
                    effect_key TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, event_digest),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_task_effect_unique_intent
                ON task_effect_events(task_id, effect_key)
                WHERE event_kind IN (
                    'provider_intent', 'director_intent',
                    'slack_intent', 'oracle_merge_intent'
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS task_effect_events_no_replace
                BEFORE INSERT ON task_effect_events
                WHEN EXISTS (
                    SELECT 1 FROM task_effect_events existing
                    WHERE existing.task_id = NEW.task_id
                    AND (
                        existing.event_digest = NEW.event_digest
                        OR (
                            NEW.event_kind IN (
                                'provider_intent', 'director_intent',
                                'slack_intent', 'oracle_merge_intent'
                            )
                            AND existing.effect_key = NEW.effect_key
                            AND existing.event_kind IN (
                                'provider_intent', 'director_intent',
                                'slack_intent', 'oracle_merge_intent'
                            )
                        )
                    )
                )
                BEGIN SELECT RAISE(ABORT, 'task effect events are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS task_effect_events_no_update
                BEFORE UPDATE ON task_effect_events
                BEGIN SELECT RAISE(ABORT, 'task effect events are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS task_effect_events_no_delete
                BEFORE DELETE ON task_effect_events
                BEGIN SELECT RAISE(ABORT, 'task effect events are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_interaction_events (
                    task_id TEXT NOT NULL,
                    interaction_id TEXT NOT NULL,
                    transition_ordinal INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    event_digest TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, interaction_id, transition_ordinal),
                    UNIQUE(task_id, event_digest),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS provider_interaction_events_no_replace
                BEFORE INSERT ON provider_interaction_events
                WHEN EXISTS (
                    SELECT 1 FROM provider_interaction_events existing
                    WHERE existing.task_id = NEW.task_id
                    AND (
                        (
                            existing.interaction_id = NEW.interaction_id
                            AND existing.transition_ordinal = NEW.transition_ordinal
                        )
                        OR existing.event_digest = NEW.event_digest
                    )
                )
                BEGIN SELECT RAISE(ABORT, 'provider interaction events are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS provider_interaction_events_no_update
                BEFORE UPDATE ON provider_interaction_events
                BEGIN SELECT RAISE(ABORT, 'provider interaction events are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS provider_interaction_events_no_delete
                BEFORE DELETE ON provider_interaction_events
                BEGIN SELECT RAISE(ABORT, 'provider interaction events are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workload_assignment_reservations (
                    ledger_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_key TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    assignment_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    effort_units INTEGER NOT NULL CHECK(effort_units > 0),
                    work_kind TEXT NOT NULL,
                    work_size TEXT NOT NULL,
                    model_label TEXT,
                    available_primary_json TEXT NOT NULL,
                    configured_mode TEXT NOT NULL,
                    shadow_assignment_limit INTEGER NOT NULL
                        CHECK(shadow_assignment_limit > 0),
                    mode TEXT NOT NULL,
                    decision_outcome TEXT NOT NULL,
                    allowed INTEGER NOT NULL CHECK(allowed IN (0, 1)),
                    violations_json TEXT NOT NULL,
                    advisories_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )
            reservation_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(workload_assignment_reservations)"
                ).fetchall()
            }
            if "configured_mode" not in reservation_columns:
                connection.execute(
                    "DROP TRIGGER IF EXISTS workload_reservations_no_update"
                )
                connection.execute(
                    "ALTER TABLE workload_assignment_reservations "
                    "ADD COLUMN configured_mode TEXT NOT NULL DEFAULT 'enforced'"
                )
                connection.execute(
                    "UPDATE workload_assignment_reservations SET configured_mode = mode"
                )
            if "shadow_assignment_limit" not in reservation_columns:
                connection.execute(
                    "DROP TRIGGER IF EXISTS workload_reservations_no_update"
                )
                connection.execute(
                    "ALTER TABLE workload_assignment_reservations "
                    "ADD COLUMN shadow_assignment_limit INTEGER NOT NULL DEFAULT 5"
                )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_workload_reservations_order
                ON workload_assignment_reservations(ledger_id DESC)"""
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS workload_reservations_no_update
                BEFORE UPDATE ON workload_assignment_reservations
                BEGIN SELECT RAISE(ABORT, 'workload reservations are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS workload_reservations_no_delete
                BEFORE DELETE ON workload_assignment_reservations
                BEGIN SELECT RAISE(ABORT, 'workload reservations are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workload_assignment_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_key TEXT NOT NULL UNIQUE,
                    outcome TEXT NOT NULL CHECK(outcome IN (
                        'invoked', 'failed_pre_submit', 'cached_replay', 'blocked'
                    )),
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(reservation_key)
                        REFERENCES workload_assignment_reservations(reservation_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS workload_events_no_update
                BEFORE UPDATE ON workload_assignment_events
                BEGIN SELECT RAISE(ABORT, 'workload events are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS workload_events_no_delete
                BEFORE DELETE ON workload_assignment_events
                BEGIN SELECT RAISE(ABORT, 'workload events are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS direct_production_credits (
                    credit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    credit_key TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    plan_generation INTEGER NOT NULL CHECK(plan_generation >= 0),
                    assignment_sequence INTEGER NOT NULL
                        CHECK(assignment_sequence BETWEEN 1 AND 20),
                    assignment_id TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    effort_units INTEGER NOT NULL CHECK(effort_units > 0),
                    provider_event_digest TEXT NOT NULL,
                    provider_result_digest TEXT NOT NULL,
                    review_event_digest TEXT NOT NULL,
                    review_result_digest TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_direct_production_order
                ON direct_production_credits(credit_id DESC)"""
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS direct_production_no_credit (
                    disposition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    assignment_id TEXT NOT NULL UNIQUE,
                    reason TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS direct_production_integration_evidence (
                    credit_key TEXT PRIMARY KEY,
                    integration_credit_key TEXT NOT NULL,
                    integration_assignment_id TEXT NOT NULL,
                    integration_provider TEXT NOT NULL CHECK(
                        integration_provider IN ('claude', 'cursor', 'grok')
                    ),
                    integration_provider_event_digest TEXT NOT NULL,
                    integration_provider_result_digest TEXT NOT NULL,
                    integration_review_event_digest TEXT NOT NULL,
                    integration_review_result_digest TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(credit_key)
                        REFERENCES direct_production_credits(credit_key),
                    FOREIGN KEY(integration_credit_key)
                        REFERENCES direct_production_credits(credit_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS direct_production_integration_consumption (
                    credit_key TEXT PRIMARY KEY,
                    integration_credit_key TEXT NOT NULL,
                    integration_provider_event_digest TEXT NOT NULL,
                    integration_request_payload_digest TEXT NOT NULL,
                    consumed_artifact_digest TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(credit_key)
                        REFERENCES direct_production_credits(credit_key),
                    FOREIGN KEY(integration_credit_key)
                        REFERENCES direct_production_credits(credit_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS external_openai_production_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    owner_authorization_digest TEXT NOT NULL UNIQUE,
                    owner_user_id_digest TEXT NOT NULL,
                    authority_source TEXT NOT NULL CHECK(
                        authority_source IN ('api', 'slack_command', 'slack_button')
                    ),
                    productive_unit_key TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    plan_generation INTEGER NOT NULL CHECK(plan_generation >= 0),
                    assignment_sequence INTEGER NOT NULL
                        CHECK(assignment_sequence BETWEEN 1 AND 20),
                    repository TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    candidate_head_sha TEXT,
                    candidate_digest TEXT,
                    producer TEXT NOT NULL CHECK(producer = 'external_openai'),
                    effort_units INTEGER NOT NULL CHECK(effort_units > 0),
                    reviewer_provider TEXT NOT NULL CHECK(
                        reviewer_provider IN ('claude', 'cursor', 'grok')
                    ),
                    recorded_at TEXT NOT NULL,
                    CHECK(
                        (candidate_head_sha IS NULL)
                        <> (candidate_digest IS NULL)
                    ),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS external_openai_production_evidence (
                    replay_id TEXT PRIMARY KEY,
                    authorization_id TEXT NOT NULL UNIQUE,
                    credit_key TEXT NOT NULL UNIQUE,
                    productive_unit_key TEXT NOT NULL UNIQUE,
                    owner_authorization_digest TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_generation INTEGER NOT NULL CHECK(plan_generation >= 0),
                    assignment_sequence INTEGER NOT NULL
                        CHECK(assignment_sequence BETWEEN 1 AND 20),
                    repository TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    candidate_head_sha TEXT,
                    candidate_digest TEXT,
                    producer TEXT NOT NULL CHECK(producer = 'external_openai'),
                    effort_units INTEGER NOT NULL CHECK(effort_units > 0),
                    reviewer_provider TEXT NOT NULL CHECK(
                        reviewer_provider IN ('claude', 'cursor', 'grok')
                    ),
                    producer_event_digest TEXT NOT NULL,
                    producer_result_digest TEXT NOT NULL,
                    acceptance_event_digest TEXT NOT NULL,
                    acceptance_result_digest TEXT NOT NULL,
                    ci_evidence_digest TEXT NOT NULL,
                    security_evidence_digest TEXT NOT NULL,
                    review_verdict TEXT NOT NULL CHECK(review_verdict = 'ACCEPT'),
                    recorded_at TEXT NOT NULL,
                    CHECK(
                        (candidate_head_sha IS NULL)
                        <> (candidate_digest IS NULL)
                    ),
                    FOREIGN KEY(authorization_id)
                        REFERENCES external_openai_production_authorizations(authorization_id),
                    FOREIGN KEY(credit_key)
                        REFERENCES direct_production_credits(credit_key),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS direct_production_no_credit_no_update
                BEFORE UPDATE ON direct_production_no_credit
                BEGIN SELECT RAISE(ABORT, 'direct-production dispositions are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS direct_production_no_credit_no_delete
                BEFORE DELETE ON direct_production_no_credit
                BEGIN SELECT RAISE(ABORT, 'direct-production dispositions are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS direct_production_credits_no_update
                BEFORE UPDATE ON direct_production_credits
                BEGIN SELECT RAISE(ABORT, 'direct-production credits are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS direct_production_credits_no_delete
                BEFORE DELETE ON direct_production_credits
                BEGIN SELECT RAISE(ABORT, 'direct-production credits are append-only'); END
                """
            )
            for table, label in (
                (
                    "direct_production_integration_evidence",
                    "direct-production integration evidence",
                ),
                (
                    "direct_production_integration_consumption",
                    "direct-production integration consumption evidence",
                ),
                (
                    "external_openai_production_authorizations",
                    "external OpenAI production authorizations",
                ),
                (
                    "external_openai_production_evidence",
                    "external OpenAI production evidence",
                ),
            ):
                connection.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, '{label} is append-only'); END
                    """
                )
                connection.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, '{label} is append-only'); END
                    """
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_gateway_nonces (
                    nonce_digest TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    consumed_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_slack_gateway_nonce_expiry
                ON slack_gateway_nonces(expires_at)"""
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_intake_claims (
                    nonce_digest TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
                """
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_slack_intake_claim_source
                ON slack_intake_claims(source_event_id)"""
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS slack_intake_claims_no_update
                BEFORE UPDATE ON slack_intake_claims
                BEGIN SELECT RAISE(ABORT, 'Slack intake claims are append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS slack_intake_claims_no_delete
                BEFORE DELETE ON slack_intake_claims
                BEGIN SELECT RAISE(ABORT, 'Slack intake claims are append-only'); END
                """
            )
            self._migrate_effect_ledgers(connection)
            if self._journal_mode(connection) == "wal":
                # A pre-upgrade WAL may still contain raw task JSON even when
                # every logical row already uses the compact schema.
                self._mark_privacy_cleanup_pending(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    checked_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_checks_provider_time ON usage_checks(provider, checked_at DESC)"
            )
        self._attempt_privacy_cleanup()

    @staticmethod
    def _journal_mode(connection: sqlite3.Connection) -> str:
        return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    @staticmethod
    def _mark_privacy_cleanup_pending(connection: sqlite3.Connection) -> None:
        connection.execute(
            """UPDATE privacy_cleanup_state
            SET pending_generation = pending_generation + 1, updated_at = ?
            WHERE singleton = 1""",
            (datetime.now(timezone.utc).isoformat(),),
        )

    @staticmethod
    def _privacy_cleanup_pending_in(
        connection: sqlite3.Connection,
    ) -> bool:
        row = connection.execute(
            """SELECT pending_generation, completed_generation
            FROM privacy_cleanup_state WHERE singleton = 1"""
        ).fetchone()
        return bool(row and int(row[0]) > int(row[1]))

    def _attempt_privacy_cleanup(self) -> bool:
        """Boundedly checkpoint private WAL frames without blocking callers.

        The generation comparison prevents a checkpoint from marking cleanup
        complete when a concurrent writer requested another cleanup. A pinned
        reader leaves the durable state pending; ordinary reads remain
        available, while new external-effect intents fail closed.
        """

        connection = sqlite3.connect(self.path, timeout=0.2)
        try:
            connection.execute("PRAGMA busy_timeout = 200")
            connection.execute("PRAGMA secure_delete = ON")
            row = connection.execute(
                """SELECT pending_generation, completed_generation
                FROM privacy_cleanup_state WHERE singleton = 1"""
            ).fetchone()
            if row is None or int(row[0]) <= int(row[1]):
                return True
            target_generation = int(row[0])
            if self._journal_mode(connection) != "wal":
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """UPDATE privacy_cleanup_state
                    SET completed_generation = pending_generation, updated_at = ?
                    WHERE singleton = 1""",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                connection.commit()
                return True
            busy, _, _ = connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            if int(busy) != 0:
                return False
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT pending_generation FROM privacy_cleanup_state
                WHERE singleton = 1"""
            ).fetchone()
            if current is None or int(current[0]) != target_generation:
                connection.rollback()
                return False
            connection.execute(
                """UPDATE privacy_cleanup_state
                SET completed_generation = ?, updated_at = ?
                WHERE singleton = 1""",
                (
                    target_generation,
                    datetime.now(timezone.utc).isoformat(),
                )
            )
            connection.commit()
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            connection.close()

    def privacy_cleanup_pending(self) -> bool:
        self._attempt_privacy_cleanup()
        connection = sqlite3.connect(self.path, timeout=0.2)
        try:
            connection.execute("PRAGMA busy_timeout = 200")
            return self._privacy_cleanup_pending_in(connection)
        except sqlite3.OperationalError:
            return True
        finally:
            connection.close()

    def claim_privacy_cleanup_resumes(
        self,
        *,
        limit: int = 20,
        lease_seconds: int = 7_200,
    ) -> list[PrivacyCleanupResume]:
        """Lease a bounded batch once physical cleanup has completed.

        Waits are retained until explicit acknowledgement. A process crash
        leaves the row reclaimable after the bounded lease, including for task
        statuses outside the ordinary startup recovery scan.
        """

        if limit < 1 or limit > 100:
            raise ValueError("Privacy cleanup resume limit must be between 1 and 100")
        if lease_seconds < 1 or lease_seconds > 86_400:
            raise ValueError("Privacy cleanup lease must be between 1 and 86400 seconds")
        if not self._attempt_privacy_cleanup():
            return []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._privacy_cleanup_pending_in(connection):
                return []
            now = datetime.now(timezone.utc)
            rows = connection.execute(
                """SELECT task_id, effect_kind, effect_key, phase
                FROM privacy_cleanup_waits
                WHERE lease_until IS NULL OR lease_until <= ?
                ORDER BY requested_at, task_id LIMIT ?""",
                (now.isoformat(), limit),
            ).fetchall()
            resumes: list[PrivacyCleanupResume] = []
            for row in rows:
                token = secrets.token_hex(16)
                connection.execute(
                    """UPDATE privacy_cleanup_waits
                    SET lease_token = ?, lease_until = ?
                    WHERE task_id = ? AND (lease_until IS NULL OR lease_until <= ?)""",
                    (
                        token,
                        (now + timedelta(seconds=lease_seconds)).isoformat(),
                        row["task_id"],
                        now.isoformat(),
                    ),
                )
                resumes.append(
                    PrivacyCleanupResume(
                        task_id=str(row["task_id"]),
                        effect_kind=str(row["effect_kind"]),
                        effect_key=str(row["effect_key"]),
                        phase=str(row["phase"]),
                        lease_token=token,
                    )
                )
            return resumes

    def acknowledge_privacy_cleanup_resume(
        self,
        resume: PrivacyCleanupResume,
    ) -> bool:
        with self._lock, self._connect() as connection:
            deleted = connection.execute(
                """DELETE FROM privacy_cleanup_waits
                WHERE task_id = ? AND lease_token = ?""",
                (resume.task_id, resume.lease_token),
            )
            return deleted.rowcount == 1

    def prepare_privacy_slack_recovery(
        self,
        resume: PrivacyCleanupResume,
        recovery_effect: TaskEffectEvidence,
    ) -> str:
        """Atomically choose the original or recovery Slack identity.

        SQLite serializes this decision with normal effect reservations. If
        the original intent already won, its wait is acknowledged without a
        second post. If recovery wins, a durable resolution prevents a stale
        writer from reserving the original identity afterward.
        """

        if resume.effect_kind != "slack_intent":
            raise ValueError("Only Slack cleanup waits use Slack recovery")
        if recovery_effect.kind != "slack_intent":
            raise ValueError("Slack recovery requires a Slack intent")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            wait = connection.execute(
                """SELECT 1 FROM privacy_cleanup_waits
                WHERE task_id = ? AND lease_token = ?""",
                (resume.task_id, resume.lease_token),
            ).fetchone()
            if wait is None:
                return "stale_lease"
            # A private rewrite may begin after this wait was leased. Recheck
            # under the same write transaction that would reserve recovery;
            # never let the caller's earlier clean observation authorize I/O.
            if self._privacy_cleanup_pending_in(connection):
                return "pending"
            original = connection.execute(
                """SELECT 1 FROM task_effect_events
                WHERE task_id = ? AND effect_key = ? AND event_kind = 'slack_intent'""",
                (resume.task_id, resume.effect_key),
            ).fetchone()
            if original is not None:
                connection.execute(
                    """DELETE FROM privacy_cleanup_waits
                    WHERE task_id = ? AND lease_token = ?""",
                    (resume.task_id, resume.lease_token),
                )
                return "original"
            resolution = connection.execute(
                """SELECT recovery_effect_key, recovery_event_digest
                FROM privacy_cleanup_effect_resolutions
                WHERE task_id = ? AND original_effect_key = ?""",
                (resume.task_id, resume.effect_key),
            ).fetchone()
            if resolution is None:
                connection.execute(
                    """INSERT INTO privacy_cleanup_effect_resolutions(
                        task_id, original_effect_key, recovery_effect_key,
                        recovery_event_digest, created_at
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        resume.task_id,
                        resume.effect_key,
                        recovery_effect.effect_key,
                        recovery_effect.effect_digest,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                self._append_effects(
                    connection,
                    resume.task_id,
                    [recovery_effect],
                    allow_privacy_pending=True,
                )
            elif (
                resolution["recovery_effect_key"] != recovery_effect.effect_key
                or resolution["recovery_event_digest"] != recovery_effect.effect_digest
            ):
                raise StaleTaskWrite("Privacy recovery identity changed")
            completed = connection.execute(
                """SELECT 1 FROM task_effect_events
                WHERE task_id = ? AND effect_key = ?
                AND event_kind = 'slack_observation'
                AND json_extract(event_json, '$.outcome') IN ('delivered', 'ambiguous')""",
                (resume.task_id, recovery_effect.effect_key),
            ).fetchone()
            if completed is not None:
                connection.execute(
                    """DELETE FROM privacy_cleanup_waits
                    WHERE task_id = ? AND lease_token = ?""",
                    (resume.task_id, resume.lease_token),
                )
                return "completed"
            return "recovery"

    @staticmethod
    def _defer_for_privacy_cleanup(
        connection: sqlite3.Connection,
        task_id: str,
        effect: TaskEffectEvidence,
    ) -> None:
        # Discard every mutation made in the caller's transaction, then commit
        # only the durable recovery request before raising. This preserves the
        # pre-effect reservation boundary without losing the task on restart.
        connection.rollback()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT OR IGNORE INTO privacy_cleanup_waits(
                task_id, effect_kind, effect_key, phase, requested_at,
                lease_token, lease_until
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL)""",
            (
                task_id,
                effect.kind,
                effect.effect_key,
                effect.phase,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()

    @staticmethod
    def _private_runtime_projection(record_json: str) -> dict[str, object] | None:
        try:
            payload = json.loads(record_json)
        except (TypeError, ValueError):
            return None
        return {
            field: payload.get(field)
            for field in (
                "plan",
                "provider_runs",
                "assignment_outcomes",
                "provider_contact_attempts",
                "availability_impacts",
                "collaboration_turns",
                "collaboration_delivery_errors",
                "collaboration_fallback_reason",
                "debate_tally",
                "debate_synthesis",
                "review",
                "oracle_recovery",
                "error",
            )
            if payload.get(field)
        }

    def _mark_cleanup_for_rewrite(
        self,
        connection: sqlite3.Connection,
        previous_json: str,
        replacement_json: str,
    ) -> None:
        previous_projection = self._private_runtime_projection(previous_json)
        replacement_projection = self._private_runtime_projection(replacement_json)
        if previous_projection is None or replacement_projection is None:
            changed_private_projection = previous_json != replacement_json
        else:
            changed_private_projection = previous_projection != replacement_projection
        had_private_projection = previous_projection is None or bool(previous_projection)
        if (
            changed_private_projection
            and had_private_projection
            and self._journal_mode(connection) == "wal"
        ):
            self._mark_privacy_cleanup_pending(connection)

    def create(self, record: TaskRecord) -> tuple[TaskRecord, bool]:
        self._attempt_privacy_cleanup()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM tasks WHERE source_event_id = ?",
                (record.intake.source_event_id,),
            ).fetchone()
            if existing:
                return self._record_from_row(connection, existing), False
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO tasks(
                    task_id, source_event_id, status, record_json,
                    created_at, updated_at, effect_migration_status
                ) VALUES (?, ?, ?, ?, ?, ?, 'complete')""",
                (
                    record.task_id,
                    record.intake.source_event_id,
                    record.status.value,
                    self._record_json_without_effect_projection(record),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            self._append_effects(connection, record.task_id, record.task_effect_ledger)
            self._append_provider_interactions(
                connection, record.task_id, record.provider_interaction_ledger
            )
            self._append_direct_production_credits(connection, record)
            self._hydrate_effects(connection, record)
            self._hydrate_provider_interactions(connection, record)
            connection.execute(
                "UPDATE tasks SET record_json = ? WHERE task_id = ?",
                (
                    self._record_json_without_effect_projection(record),
                    record.task_id,
                ),
            )
            record._store_version = 0
        self._attempt_privacy_cleanup()
        return record, True

    def create_signed_slack_intake(
        self,
        record: TaskRecord,
        *,
        nonce_digest: str,
        payload_digest: str,
    ) -> tuple[TaskRecord, bool]:
        """Atomically bind one paid Slack intake to its durable replay identity."""

        if (
            len(nonce_digest) != 64
            or len(payload_digest) != 64
            or any(character not in "0123456789abcdef" for character in nonce_digest)
            or any(character not in "0123456789abcdef" for character in payload_digest)
        ):
            raise SlackIntakeReplayConflict("Signed Slack intake digest is malformed")
        self._attempt_privacy_cleanup()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            nonce_claim = connection.execute(
                "SELECT * FROM slack_intake_claims WHERE nonce_digest = ?",
                (nonce_digest,),
            ).fetchone()
            source_claim = connection.execute(
                """SELECT * FROM slack_intake_claims
                WHERE source_event_id = ? ORDER BY claimed_at, nonce_digest LIMIT 1""",
                (record.intake.source_event_id,),
            ).fetchone()

            for claim in (nonce_claim, source_claim):
                if claim is None:
                    continue
                if (
                    claim["source_event_id"] != record.intake.source_event_id
                    or claim["payload_digest"] != payload_digest
                ):
                    raise SlackIntakeReplayConflict(
                        "Signed Slack intake replay identity conflicts with durable authority"
                    )

            authoritative_claim = nonce_claim or source_claim
            if authoritative_claim is not None:
                task = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (authoritative_claim["task_id"],),
                ).fetchone()
                if task is None:
                    raise SlackIntakeReplayConflict(
                        "Signed Slack intake claim has no durable task"
                    )
                if nonce_claim is None:
                    connection.execute(
                        """INSERT INTO slack_intake_claims(
                            nonce_digest, source_event_id, payload_digest,
                            task_id, claimed_at
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            nonce_digest,
                            record.intake.source_event_id,
                            payload_digest,
                            authoritative_claim["task_id"],
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                return self._record_from_row(connection, task), False

            legacy_task = connection.execute(
                "SELECT * FROM tasks WHERE source_event_id = ?",
                (record.intake.source_event_id,),
            ).fetchone()
            if legacy_task is not None:
                stored = self._record_from_row(connection, legacy_task)
                if stored.intake.model_dump(mode="json") != record.intake.model_dump(
                    mode="json"
                ):
                    raise SlackIntakeReplayConflict(
                        "Signed Slack source event conflicts with an existing task"
                    )
                connection.execute(
                    """INSERT INTO slack_intake_claims(
                        nonce_digest, source_event_id, payload_digest,
                        task_id, claimed_at
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        nonce_digest,
                        record.intake.source_event_id,
                        payload_digest,
                        stored.task_id,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                return stored, False

            connection.execute(
                """INSERT INTO tasks(
                    task_id, source_event_id, status, record_json,
                    created_at, updated_at, effect_migration_status
                ) VALUES (?, ?, ?, ?, ?, ?, 'complete')""",
                (
                    record.task_id,
                    record.intake.source_event_id,
                    record.status.value,
                    self._record_json_without_effect_projection(record),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            self._append_effects(connection, record.task_id, record.task_effect_ledger)
            self._append_provider_interactions(
                connection, record.task_id, record.provider_interaction_ledger
            )
            self._hydrate_effects(connection, record)
            self._hydrate_provider_interactions(connection, record)
            connection.execute(
                "UPDATE tasks SET record_json = ? WHERE task_id = ?",
                (self._record_json_without_effect_projection(record), record.task_id),
            )
            connection.execute(
                """INSERT INTO slack_intake_claims(
                    nonce_digest, source_event_id, payload_digest,
                    task_id, claimed_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    nonce_digest,
                    record.intake.source_event_id,
                    payload_digest,
                    record.task_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            record._store_version = 0
        self._attempt_privacy_cleanup()
        return record, True

    def get(self, task_id: str) -> TaskRecord | None:
        versioned = self.get_versioned(task_id)
        return versioned[0] if versioned is not None else None

    def get_versioned(self, task_id: str) -> tuple[TaskRecord, int] | None:
        self._attempt_privacy_cleanup()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            return (
                self._record_from_row(connection, row),
                int(row["record_version"]),
            )

    def list_recoverable(self) -> list[TaskRecord]:
        self._attempt_privacy_cleanup()
        recoverable = {
            TaskStatus.QUEUED.value,
            TaskStatus.PLANNING.value,
            TaskStatus.RUNNING.value,
            TaskStatus.REVIEWING.value,
        }
        placeholders = ",".join("?" for _ in recoverable)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders})",
                tuple(sorted(recoverable)),
            ).fetchall()
            return [self._record_from_row(connection, row) for row in rows]

    @staticmethod
    def _workload_unit_exists(
        connection: sqlite3.Connection, assignment_id: str
    ) -> bool:
        """Return whether any reservation was ever taken for this unit identity."""

        return (
            connection.execute(
                """SELECT 1 FROM workload_assignment_reservations
                WHERE assignment_id = ? LIMIT 1""",
                (assignment_id,),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _stored_direct_production_provider(value: str) -> DirectProductionProvider:
        """Parse ledger identities without mapping runtime OpenAI authority."""

        try:
            return DirectProductionProvider(value)
        except ValueError as exc:
            raise WorkloadReservationConflict(
                "direct-production ledger contains an unknown provider"
            ) from exc

    @staticmethod
    def _is_creditable_accepted_work(provider: Provider, kind: WorkKind) -> bool:
        """Recognize implementation plus Perplexity's bounded research product."""

        return is_accepted_productive_kind(provider, kind)

    @staticmethod
    def _antigravity_integration_identity(
        connection: sqlite3.Connection,
        record: TaskRecord,
        assignments: dict[int, Any],
        persisted_events: list[ProviderInteractionEvent],
        antigravity_sequence: int,
        antigravity_artifact_digest: str,
        *,
        preferred_credit_key: str | None = None,
    ) -> tuple[object, ...] | None:
        """Return durable downstream integration evidence, or defer the credit.

        Antigravity may produce a useful artifact, but the artifact earns no
        accepted-production credit until a later Claude, Cursor, or Grok
        implementation with a direct dependency has itself earned an
        authenticated accepted-production credit.
        """

        eligible_providers = {
            Provider.CLAUDE,
            Provider.CURSOR,
            Provider.GROK,
        }
        candidates: list[tuple[Any, Provider]] = []
        for assignment in assignments.values():
            if (
                assignment.sequence <= antigravity_sequence
                or antigravity_sequence not in assignment.depends_on
                or trusted_work_kind(assignment) is not WorkKind.IMPLEMENTATION
            ):
                continue
            try:
                provider = Provider(assignment.agent.value)
            except ValueError:
                continue
            if provider in eligible_providers:
                candidates.append((assignment, provider))
        for assignment, provider in sorted(
            candidates, key=lambda item: item[0].sequence
        ):
            integration_assignment_id = (
                f"{record.task_id}:{record.plan_generation}:{assignment.sequence}"
            )
            row = connection.execute(
                """SELECT credit_key, task_id, plan_generation,
                          assignment_sequence, assignment_id, provider,
                          effort_units, provider_event_digest,
                          provider_result_digest, review_event_digest,
                          review_result_digest
                FROM direct_production_credits
                WHERE assignment_id = ?""",
                (integration_assignment_id,),
            ).fetchone()
            if row is None:
                continue
            if (
                preferred_credit_key is not None
                and str(row["credit_key"]) != preferred_credit_key
            ):
                continue
            expected = (
                record.task_id,
                record.plan_generation,
                assignment.sequence,
                integration_assignment_id,
                provider.value,
                assignment.effort_points,
            )
            persisted = (
                str(row["task_id"]),
                int(row["plan_generation"]),
                int(row["assignment_sequence"]),
                str(row["assignment_id"]),
                str(row["provider"]),
                int(row["effort_units"]),
            )
            if persisted != expected:
                raise WorkloadReservationConflict(
                    "Antigravity integration credit identity changed"
                )
            integration_event = next(
                (
                    event
                    for event in persisted_events
                    if event.event_digest == str(row["provider_event_digest"])
                    and event.task_id == record.task_id
                    and event.plan_generation == record.plan_generation
                    and event.assignment_sequence == assignment.sequence
                    and event.provider.value == provider.value
                    and event.state is ProviderInteractionState.VERIFIED
                    and event.phase
                    in {
                        ProviderInteractionPhase.DISPATCH,
                        ProviderInteractionPhase.REVISION,
                    }
                ),
                None,
            )
            if (
                integration_event is None
                or integration_event.request_payload_digest is None
                or antigravity_artifact_digest
                not in integration_event.consumed_artifact_digests
            ):
                continue
            return (
                str(row["credit_key"]),
                integration_assignment_id,
                provider.value,
                str(row["provider_event_digest"]),
                str(row["provider_result_digest"]),
                str(row["review_event_digest"]),
                str(row["review_result_digest"]),
                integration_event.request_payload_digest,
                antigravity_artifact_digest,
            )
        return None

    @staticmethod
    def _record_no_credit_disposition(
        connection: sqlite3.Connection,
        assignment_id: str,
        *,
        reason: str,
        recorded_at: datetime,
    ) -> bool:
        """Append one terminal "this unit will never be credited" record.

        An already-credited unit is never retroactively zeroed: the append-only
        credit ledger stays authoritative and the attempt fails closed.
        """

        credited = connection.execute(
            "SELECT 1 FROM direct_production_credits WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()
        if credited is not None:
            raise WorkloadReservationConflict(
                "a credited direct-production unit cannot be given a no-credit disposition"
            )
        existing = connection.execute(
            "SELECT 1 FROM direct_production_no_credit WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()
        if existing is not None:
            return False
        connection.execute(
            """INSERT INTO direct_production_no_credit(
                assignment_id, reason, recorded_at
            ) VALUES (?, ?, ?)""",
            (assignment_id, reason[:255], recorded_at.isoformat()),
        )
        return True

    def record_direct_production_no_credit(
        self, assignment_id: str, *, reason: str
    ) -> bool:
        """Close a rejected or terminated implementation's production accounting.

        An invoked implementation that is never accepted would otherwise stay
        "unresolved" forever: the rollover projection treats every uncredited
        invoked implementation as a unit that might still be credited, so a
        single terminally rejected unit can block later production for good.
        This records the terminal fact that no credit will follow.
        """

        if not isinstance(assignment_id, str) or not assignment_id.strip():
            raise ValueError("workload assignment id must not be blank")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("no-credit disposition reason must not be blank")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._record_no_credit_disposition(
                connection,
                assignment_id,
                reason=reason,
                recorded_at=datetime.now(timezone.utc),
            )

    @staticmethod
    def _require_current_task_generation(
        connection: sqlite3.Connection, task_id: str, plan_generation: int
    ) -> None:
        row = connection.execute(
            "SELECT record_json FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise WorkloadReservationConflict(
                "external OpenAI production authority names an unknown task"
            )
        record = TaskStore._task_record_from_persisted_json(str(row["record_json"]))
        if record.plan_generation != plan_generation:
            raise WorkloadReservationConflict(
                "external OpenAI production authority names a stale plan generation"
            )

    def record_external_openai_production_authorization(
        self, authorization: ExternalOpenAIProductionAuthorization
    ) -> bool:
        """Persist one exact authorization after trusted owner authentication.

        This is intentionally a store-only primitive.  It must be called by an
        already-authenticated owner command path; no generic HTTP ingestion
        route is created here.  ``True`` means appended and ``False`` means an
        exact idempotent replay.
        """

        if not isinstance(authorization, ExternalOpenAIProductionAuthorization):
            raise TypeError(
                "external OpenAI authorization must use its typed evidence contract"
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT authorization_id, owner_authorization_digest,
                          owner_user_id_digest, authority_source,
                          productive_unit_key, task_id, plan_generation,
                          assignment_sequence, repository, base_sha,
                          candidate_head_sha, candidate_digest, producer,
                          effort_units, reviewer_provider
                FROM external_openai_production_authorizations
                WHERE authorization_id = ?
                   OR owner_authorization_digest = ?""",
                (
                    authorization.authorization_id,
                    authorization.owner_authorization_digest,
                ),
            ).fetchone()
            if existing is not None:
                persisted = (
                    str(existing["authorization_id"]),
                    str(existing["owner_authorization_digest"]),
                    str(existing["owner_user_id_digest"]),
                    str(existing["authority_source"]),
                    str(existing["productive_unit_key"]),
                    str(existing["task_id"]),
                    int(existing["plan_generation"]),
                    int(existing["assignment_sequence"]),
                    str(existing["repository"]),
                    str(existing["base_sha"]),
                    existing["candidate_head_sha"],
                    existing["candidate_digest"],
                    str(existing["producer"]),
                    int(existing["effort_units"]),
                    str(existing["reviewer_provider"]),
                )
                if persisted != authorization.binding_identity():
                    raise WorkloadReservationConflict(
                        "external OpenAI owner authorization replay identity changed"
                    )
                return False
            self._require_current_task_generation(
                connection,
                authorization.task_id,
                authorization.plan_generation,
            )
            existing_unit = connection.execute(
                """SELECT 1
                FROM external_openai_production_authorizations
                WHERE productive_unit_key = ?""",
                (authorization.productive_unit_key,),
            ).fetchone()
            if existing_unit is not None:
                raise WorkloadReservationConflict(
                    "external OpenAI owner authorization replay identity changed"
                )
            connection.execute(
                """INSERT INTO external_openai_production_authorizations(
                    authorization_id, owner_authorization_digest,
                    owner_user_id_digest, authority_source,
                    productive_unit_key, task_id, plan_generation,
                    assignment_sequence, repository, base_sha,
                    candidate_head_sha, candidate_digest, producer,
                    effort_units, reviewer_provider, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*authorization.binding_identity(), datetime.now(timezone.utc).isoformat()),
            )
            return True

    @staticmethod
    def _observe_external_openai_direct_production_share(
        connection: sqlite3.Connection,
        evidence: ExternalOpenAIProductionEvidence,
    ) -> None:
        """Validate immutable share-accounting identity without blocking by share.

        The caller holds the same ``BEGIN IMMEDIATE`` transaction used to append
        the evidence and credit.  The accepted owner authorization supplies the
        immutable, pre-staged effort points; no provider-authored estimate is
        consulted here.
        """

        rows = connection.execute(
            """SELECT credit.assignment_id, credit.provider,
                      credit.effort_units
            FROM direct_production_credits AS credit
            WHERE credit.provider <> 'gemini'
               OR EXISTS (
                   SELECT 1
                   FROM direct_production_integration_evidence AS integration
                   WHERE integration.credit_key = credit.credit_key
                     AND EXISTS (
                         SELECT 1
                         FROM direct_production_integration_consumption AS consumption
                         WHERE consumption.credit_key = credit.credit_key
                     )
               )
            ORDER BY credit.credit_id DESC
            LIMIT ?""",
            (DEFAULT_POLICY.rolling_window_size - 1,),
        ).fetchall()
        history = [
            DirectProductionRecord(
                assignment_id=str(row["assignment_id"]),
                provider=TaskStore._stored_direct_production_provider(
                    str(row["provider"])
                ),
                effort_units=int(row["effort_units"]),
            )
            for row in reversed(rows)
        ]
        if any(
            record.assignment_id == evidence.productive_unit_key
            for record in history
        ):
            raise WorkloadReservationConflict(
                "external OpenAI productive unit already exists in direct history"
            )
        candidate = DirectProductionRecord(
            assignment_id=evidence.productive_unit_key,
            provider=DirectProductionProvider.EXTERNAL_OPENAI,
            effort_units=evidence.effort_units,
        )
        window = history + [candidate]
        if len(window) < DEFAULT_POLICY.rolling_window_size:
            return
        total_units = sum(record.effort_units for record in window)
        provider_units = sum(
            record.effort_units
            for record in window
            if record.provider is DirectProductionProvider.EXTERNAL_OPENAI
        )
        _ = (provider_units, total_units)

    def record_external_openai_production_credit(
        self, evidence: ExternalOpenAIProductionEvidence
    ) -> bool:
        """Append one accepted external OpenAI credit under exact prior authority.

        The accepted candidate, effort, repository, reviewer, owner evidence,
        trusted CI, and security evidence are immutable.  ``True`` means a new
        credit; ``False`` is an exact replay.  Any partial or changed replay
        fails closed.
        """

        if not isinstance(evidence, ExternalOpenAIProductionEvidence):
            raise TypeError(
                "external OpenAI credit must use its typed evidence contract"
            )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            authorization = connection.execute(
                """SELECT authorization_id, owner_authorization_digest,
                          productive_unit_key, task_id, plan_generation,
                          assignment_sequence, repository, base_sha,
                          candidate_head_sha, candidate_digest, producer,
                          effort_units, reviewer_provider
                FROM external_openai_production_authorizations
                WHERE authorization_id = ?""",
                (evidence.authorization_id,),
            ).fetchone()
            if authorization is None:
                raise WorkloadReservationConflict(
                    "external OpenAI production lacks exact stored owner authorization"
                )
            if not hmac.compare_digest(
                str(authorization["owner_authorization_digest"]),
                evidence.owner_authorization_digest,
            ):
                raise WorkloadReservationConflict(
                    "external OpenAI owner authorization digest changed"
                )
            authorized_binding = (
                str(authorization["authorization_id"]),
                str(authorization["owner_authorization_digest"]),
                str(authorization["productive_unit_key"]),
                str(authorization["task_id"]),
                int(authorization["plan_generation"]),
                int(authorization["assignment_sequence"]),
                str(authorization["repository"]),
                str(authorization["base_sha"]),
                authorization["candidate_head_sha"],
                authorization["candidate_digest"],
                str(authorization["producer"]),
                int(authorization["effort_units"]),
                str(authorization["reviewer_provider"]),
            )
            if authorized_binding != evidence.authorization_binding():
                raise WorkloadReservationConflict(
                    "external OpenAI accepted candidate exceeds exact owner authorization"
                )
            existing = connection.execute(
                """SELECT replay_id, authorization_id, credit_key,
                          owner_authorization_digest, productive_unit_key,
                          task_id, plan_generation, assignment_sequence,
                          repository, base_sha, candidate_head_sha,
                          candidate_digest, producer, effort_units,
                          reviewer_provider,
                          producer_event_digest, producer_result_digest,
                          acceptance_event_digest, acceptance_result_digest,
                          ci_evidence_digest, security_evidence_digest,
                          review_verdict
                FROM external_openai_production_evidence
                WHERE replay_id = ? OR authorization_id = ? OR credit_key = ?""",
                (
                    evidence.replay_id,
                    evidence.authorization_id,
                    evidence.credit_key,
                ),
            ).fetchone()
            if existing is not None:
                persisted = tuple(
                    existing[name]
                    for name in (
                        "replay_id",
                        "authorization_id",
                        "credit_key",
                        "owner_authorization_digest",
                        "productive_unit_key",
                        "task_id",
                        "plan_generation",
                        "assignment_sequence",
                        "repository",
                        "base_sha",
                        "candidate_head_sha",
                        "candidate_digest",
                        "producer",
                        "effort_units",
                        "reviewer_provider",
                        "producer_event_digest",
                        "producer_result_digest",
                        "acceptance_event_digest",
                        "acceptance_result_digest",
                        "ci_evidence_digest",
                        "security_evidence_digest",
                        "review_verdict",
                    )
                )
                persisted = tuple(
                    int(value) if index in {6, 7, 13} else value
                    for index, value in enumerate(persisted)
                )
                if persisted != evidence.evidence_identity():
                    raise WorkloadReservationConflict(
                        "external OpenAI production replay identity changed"
                    )
                direct = connection.execute(
                    """SELECT task_id, plan_generation, assignment_sequence,
                              assignment_id, provider, effort_units,
                              provider_event_digest, provider_result_digest,
                              review_event_digest, review_result_digest
                    FROM direct_production_credits WHERE credit_key = ?""",
                    (evidence.credit_key,),
                ).fetchone()
                direct_identity = (
                    evidence.task_id,
                    evidence.plan_generation,
                    evidence.assignment_sequence,
                    evidence.credit_key,
                    DirectProductionProvider.EXTERNAL_OPENAI.value,
                    evidence.effort_units,
                    evidence.producer_event_digest,
                    evidence.producer_result_digest,
                    evidence.acceptance_event_digest,
                    evidence.acceptance_result_digest,
                )
                persisted_direct = (
                    None
                    if direct is None
                    else (
                        str(direct["task_id"]),
                        int(direct["plan_generation"]),
                        int(direct["assignment_sequence"]),
                        str(direct["assignment_id"]),
                        str(direct["provider"]),
                        int(direct["effort_units"]),
                        str(direct["provider_event_digest"]),
                        str(direct["provider_result_digest"]),
                        str(direct["review_event_digest"]),
                        str(direct["review_result_digest"]),
                    )
                )
                if persisted_direct != direct_identity:
                    raise WorkloadReservationConflict(
                        "external OpenAI direct-production credit binding changed"
                    )
                return False
            self._require_current_task_generation(
                connection, evidence.task_id, evidence.plan_generation
            )
            conflicting_credit = connection.execute(
                """SELECT 1 FROM direct_production_credits
                WHERE credit_key = ? OR assignment_id = ?""",
                (evidence.credit_key, evidence.credit_key),
            ).fetchone()
            if conflicting_credit is not None:
                raise WorkloadReservationConflict(
                    "external OpenAI credit exists without matching replay evidence"
                )
            self._observe_external_openai_direct_production_share(
                connection, evidence
            )
            recorded_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO direct_production_credits(
                    credit_key, task_id, plan_generation, assignment_sequence,
                    assignment_id, provider, effort_units,
                    provider_event_digest, provider_result_digest,
                    review_event_digest, review_result_digest, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence.credit_key,
                    evidence.task_id,
                    evidence.plan_generation,
                    evidence.assignment_sequence,
                    evidence.credit_key,
                    DirectProductionProvider.EXTERNAL_OPENAI.value,
                    evidence.effort_units,
                    evidence.producer_event_digest,
                    evidence.producer_result_digest,
                    evidence.acceptance_event_digest,
                    evidence.acceptance_result_digest,
                    recorded_at,
                ),
            )
            connection.execute(
                """INSERT INTO external_openai_production_evidence(
                    replay_id, authorization_id, credit_key,
                    owner_authorization_digest, productive_unit_key, task_id,
                    plan_generation, assignment_sequence, repository, base_sha,
                    candidate_head_sha, candidate_digest, producer,
                    effort_units, reviewer_provider,
                    producer_event_digest, producer_result_digest,
                    acceptance_event_digest, acceptance_result_digest,
                    ci_evidence_digest, security_evidence_digest,
                    review_verdict, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*evidence.evidence_identity(), recorded_at),
            )
            return True

    @staticmethod
    def _append_direct_production_credits(
        connection: sqlite3.Connection, record: TaskRecord
    ) -> None:
        """Project authenticated accepted outcomes into an append-only ledger."""

        plan = record.plan
        if plan is None or plan.workload_policy_version != "v1":
            return
        assignments = {item.sequence: item for item in plan.assignments}
        persisted_events = TaskStore._load_provider_interactions(
            connection, record.task_id
        )
        # Downstream primary-team integrations are projected before their
        # Antigravity inputs so the latter can bind to durable accepted evidence
        # in the same transaction.
        ordered_outcomes = [
            outcome
            for outcome in record.assignment_outcomes
            if outcome.provider.value != Provider.GEMINI.value
        ] + [
            outcome
            for outcome in record.assignment_outcomes
            if outcome.provider.value == Provider.GEMINI.value
        ]
        for outcome in ordered_outcomes:
            assignment = assignments.get(outcome.assignment_sequence)
            if assignment is not None and assignment.agent != outcome.provider:
                raise WorkloadReservationConflict(
                    "accepted direct-production outcome is not bound to its assignment"
                )
            try:
                provider = Provider(outcome.provider.value)
            except ValueError as exc:
                raise WorkloadReservationConflict(
                    "accepted outcome provider is outside workload accounting"
                ) from exc
            direct_provider = direct_production_provider(provider)
            if direct_provider is None:
                # Ordinary OpenAI is the control plane. The separately
                # owner-authorized external OpenAI lane is never inferred here.
                continue
            assignment_id = (
                f"{record.task_id}:{record.plan_generation}:"
                f"{outcome.assignment_sequence}"
            )
            if assignment is None:
                if TaskStore._workload_unit_exists(connection, assignment_id):
                    raise WorkloadReservationConflict(
                        "accepted direct-production outcome is not bound to its assignment"
                    )
                # The identity this outcome would claim belongs to no reservation
                # in the current plan generation: the plan was regenerated (a
                # dry-run replan, for instance) and the accepted outcome now
                # names a superseded unit. Raising here wedged every later
                # `save` for the task forever. Record the terminal no-credit
                # disposition instead. It grants no authority — the outcome
                # simply earns nothing — and it keeps the unresolved-rollover
                # projection from holding the stale identity open.
                TaskStore._record_no_credit_disposition(
                    connection,
                    assignment_id,
                    reason="stale_assignment_identity",
                    recorded_at=record.updated_at,
                )
                continue
            trusted_kind = trusted_work_kind(assignment)
            if not TaskStore._is_creditable_accepted_work(provider, trusted_kind):
                # Accepted review, debate, and coordination outcomes are real
                # provider work, but they are not accepted productive-work
                # credits. Perplexity's authenticated accepted public research
                # is the deliberately narrow non-code exception.
                continue
            if not TaskStore._workload_unit_exists(connection, assignment_id):
                raise WorkloadReservationConflict(
                    "accepted direct-production outcome lacks invoked workload authority"
                )
            workload_authority = connection.execute(
                """SELECT reservation.task_id, reservation.provider,
                          reservation.effort_units, reservation.work_kind
                FROM workload_assignment_reservations AS reservation
                JOIN workload_assignment_events AS event
                  ON event.reservation_key = reservation.reservation_key
                WHERE reservation.assignment_id = ?
                  AND event.outcome = 'invoked'
                ORDER BY reservation.ledger_id ASC
                LIMIT 1""",
                (assignment_id,),
            ).fetchone()
            if workload_authority is None:
                raise WorkloadReservationConflict(
                    "accepted direct-production outcome lacks invoked workload authority"
                )
            try:
                authoritative_kind = WorkKind(str(workload_authority["work_kind"]))
            except ValueError as exc:
                raise WorkloadReservationConflict(
                    "accepted direct-production workload authority is malformed"
                ) from exc
            if (
                str(workload_authority["task_id"]) != record.task_id
                or str(workload_authority["provider"]) != provider.value
                or int(workload_authority["effort_units"]) != assignment.effort_points
            ):
                raise WorkloadReservationConflict(
                    "accepted direct-production workload authority binding changed"
                )
            if not TaskStore._is_creditable_accepted_work(
                provider, authoritative_kind
            ):
                continue
            if not outcome.interaction_id or not outcome.director_review_interaction_id:
                raise WorkloadReservationConflict(
                    "accepted direct-production outcome lacks authenticated review evidence"
                )
            expected_provider_result_digest = canonical_content_digest(outcome.result)
            expected_run_id_digest = canonical_identifier_digest(outcome.run_id)
            try:
                provider_projection = project_interaction(
                    persisted_events, outcome.interaction_id
                )
                review_projection = project_interaction(
                    persisted_events, outcome.director_review_interaction_id
                )
            except CommunicationEvidenceFailure as exc:
                raise WorkloadReservationConflict(
                    "accepted direct-production outcome interaction evidence is invalid"
                ) from exc
            provider_event = next(
                (
                    item
                    for item in persisted_events
                    if item.interaction_id == outcome.interaction_id
                    and item.event_digest
                    == outcome.verified_interaction_event_digest
                ),
                None,
            )
            review_event = next(
                (
                    item
                    for item in persisted_events
                    if item.interaction_id
                    == outcome.director_review_interaction_id
                    and item.event_digest
                    == outcome.verified_director_review_event_digest
                ),
                None,
            )
            provider_binding_valid = (
                provider_event is not None
                and provider_projection.task_id == record.task_id
                and provider_projection.provider.value == outcome.provider.value
                and provider_projection.assignment_sequence == assignment.sequence
                and provider_projection.state is ProviderInteractionState.VERIFIED
                and provider_projection.latest_event_digest
                == outcome.verified_interaction_event_digest
                and provider_projection.verified_result_digest
                == expected_provider_result_digest
                and outcome.provider_result_digest
                == expected_provider_result_digest
                and provider_event.plan_generation == record.plan_generation
                and provider_event.phase
                in {
                    ProviderInteractionPhase.DISPATCH,
                    ProviderInteractionPhase.REVISION,
                }
                and provider_event.transport_evidence.source
                is TransportEvidenceSource.AUTHENTICATED_PROVIDER
                and provider_event.transport_evidence.provider_run_id_digest
                == expected_run_id_digest
            )
            expected_review_candidate_digest = acceptance_review_candidate_digest(
                task_id=record.task_id,
                plan_generation=record.plan_generation,
                assignment_sequence=assignment.sequence,
                provider=outcome.provider.value,
                provider_run_id=outcome.run_id,
                provider_event_digest=(
                    provider_event.event_digest if provider_event is not None else "0" * 64
                ),
                provider_result_digest=expected_provider_result_digest,
            )
            review_effect_valid = (
                review_event is not None
                and review_event.effect_key is not None
                and any(
                    effect.effect_key == review_event.effect_key
                    and effect.kind == "director_intent"
                    and effect.phase == "acceptance_review"
                    and effect.plan_generation == record.plan_generation
                    and effect.outcome == "reserved"
                    for effect in record.task_effect_ledger
                )
                and any(
                    effect.effect_key == review_event.effect_key
                    and effect.kind == "director_observation"
                    and effect.phase == "acceptance_review"
                    and effect.plan_generation == record.plan_generation
                    and effect.outcome == "completed"
                    for effect in record.task_effect_ledger
                )
            )
            review_binding_valid = (
                review_event is not None
                and review_projection.task_id == record.task_id
                and director_evidence_matches(
                    review_event, record.director_generations.get(record.plan_generation)
                )
                and (review_event.director_identity is None
                     or review_event.provider.value != outcome.provider.value)
                and review_projection.assignment_sequence is None
                and review_projection.state is ProviderInteractionState.VERIFIED
                and review_projection.latest_event_digest
                == outcome.verified_director_review_event_digest
                and review_projection.verified_result_digest
                == outcome.director_review_result_digest
                and review_event.plan_generation == record.plan_generation
                and review_event.phase
                is ProviderInteractionPhase.ACCEPTANCE_REVIEW
                and review_event.transport_evidence.source
                is TransportEvidenceSource.AUTHENTICATED_PROVIDER
                and review_event.candidate_binding_digest
                == expected_review_candidate_digest
                and review_effect_valid
            )
            if not provider_binding_valid or not review_binding_valid:
                raise WorkloadReservationConflict(
                    "accepted direct-production outcome evidence binding changed"
                )
            identity = (
                record.task_id,
                record.plan_generation,
                assignment.sequence,
                assignment_id,
                direct_provider.value,
                assignment.effort_points,
                provider_event.event_digest,
                expected_provider_result_digest,
                review_event.event_digest,
                outcome.director_review_result_digest,
            )
            existing = connection.execute(
                """SELECT task_id, plan_generation, assignment_sequence,
                          assignment_id, provider, effort_units,
                          provider_event_digest, provider_result_digest,
                          review_event_digest, review_result_digest
                FROM direct_production_credits WHERE credit_key = ?""",
                (assignment_id,),
            ).fetchone()
            integration_identity: tuple[object, ...] | None = None
            append_integration_for_existing = False
            append_consumption_for_existing = False
            if provider is Provider.GEMINI:
                if existing is not None:
                    stored_integration = connection.execute(
                        """SELECT integration_credit_key,
                                  integration_assignment_id,
                                  integration_provider,
                                  integration_provider_event_digest,
                                  integration_provider_result_digest,
                                  integration_review_event_digest,
                                  integration_review_result_digest
                        FROM direct_production_integration_evidence
                        WHERE credit_key = ?""",
                        (assignment_id,),
                    ).fetchone()
                    stored_consumption = connection.execute(
                        """SELECT integration_credit_key,
                                  integration_provider_event_digest,
                                  integration_request_payload_digest,
                                  consumed_artifact_digest
                        FROM direct_production_integration_consumption
                        WHERE credit_key = ?""",
                        (assignment_id,),
                    ).fetchone()
                    integration_identity = TaskStore._antigravity_integration_identity(
                        connection,
                        record,
                        assignments,
                        persisted_events,
                        assignment.sequence,
                        expected_provider_result_digest,
                        preferred_credit_key=(
                            str(stored_integration["integration_credit_key"])
                            if stored_integration is not None
                            else None
                        ),
                    )
                    if integration_identity is None:
                        # Legacy dependency-only evidence remains present but is
                        # never sufficient to make the Antigravity credit visible.
                        continue
                    if (
                        stored_integration is not None
                        and tuple(stored_integration) != integration_identity[:7]
                    ):
                        raise WorkloadReservationConflict(
                            "Antigravity downstream integration evidence changed"
                        )
                    consumption_identity = (
                        integration_identity[0],
                        integration_identity[3],
                        integration_identity[7],
                        integration_identity[8],
                    )
                    if (
                        stored_consumption is not None
                        and tuple(stored_consumption) != consumption_identity
                    ):
                        raise WorkloadReservationConflict(
                            "Antigravity request-consumption evidence changed"
                        )
                    append_integration_for_existing = stored_integration is None
                    append_consumption_for_existing = stored_consumption is None
                else:
                    integration_identity = TaskStore._antigravity_integration_identity(
                        connection,
                        record,
                        assignments,
                        persisted_events,
                        assignment.sequence,
                        expected_provider_result_digest,
                    )
                    if integration_identity is None:
                        # The artifact remains uncredited until an eligible
                        # downstream integration is independently accepted.
                        continue
            if existing is not None:
                persisted = (
                    str(existing["task_id"]),
                    int(existing["plan_generation"]),
                    int(existing["assignment_sequence"]),
                    str(existing["assignment_id"]),
                    str(existing["provider"]),
                    int(existing["effort_units"]),
                    str(existing["provider_event_digest"]),
                    str(existing["provider_result_digest"]),
                    str(existing["review_event_digest"]),
                    str(existing["review_result_digest"]),
                )
                if persisted != identity:
                    raise WorkloadReservationConflict(
                        "accepted direct-production credit identity changed"
                    )
                if append_integration_for_existing and integration_identity is not None:
                    connection.execute(
                        """INSERT INTO direct_production_integration_evidence(
                            credit_key, integration_credit_key,
                            integration_assignment_id, integration_provider,
                            integration_provider_event_digest,
                            integration_provider_result_digest,
                            integration_review_event_digest,
                            integration_review_result_digest, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            assignment_id,
                            *integration_identity[:7],
                            record.updated_at.isoformat(),
                        ),
                    )
                if append_consumption_for_existing and integration_identity is not None:
                    connection.execute(
                        """INSERT INTO direct_production_integration_consumption(
                            credit_key, integration_credit_key,
                            integration_provider_event_digest,
                            integration_request_payload_digest,
                            consumed_artifact_digest, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            assignment_id,
                            integration_identity[0],
                            integration_identity[3],
                            integration_identity[7],
                            integration_identity[8],
                            record.updated_at.isoformat(),
                        ),
                    )
                continue
            connection.execute(
                """INSERT INTO direct_production_credits(
                    credit_key, task_id, plan_generation, assignment_sequence,
                    assignment_id, provider, effort_units,
                    provider_event_digest, provider_result_digest,
                    review_event_digest, review_result_digest, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (assignment_id, *identity, record.updated_at.isoformat()),
            )
            if integration_identity is not None:
                connection.execute(
                    """INSERT INTO direct_production_integration_evidence(
                        credit_key, integration_credit_key,
                        integration_assignment_id, integration_provider,
                        integration_provider_event_digest,
                        integration_provider_result_digest,
                        integration_review_event_digest,
                        integration_review_result_digest, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        assignment_id,
                        *integration_identity[:7],
                        record.updated_at.isoformat(),
                    ),
                )
                connection.execute(
                    """INSERT INTO direct_production_integration_consumption(
                        credit_key, integration_credit_key,
                        integration_provider_event_digest,
                        integration_request_payload_digest,
                        consumed_artifact_digest, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        assignment_id,
                        integration_identity[0],
                        integration_identity[3],
                        integration_identity[7],
                        integration_identity[8],
                        record.updated_at.isoformat(),
                    ),
                )

    def save(self, record: TaskRecord) -> TaskRecord:
        self._attempt_privacy_cleanup()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT effect_migration_status, record_version, record_json
                FROM tasks WHERE task_id = ?""",
                (record.task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(record.task_id)
            self._require_effect_migration(row)
            current_version = int(row["record_version"])
            if record._store_version is None or record._store_version != current_version:
                raise StaleTaskWrite(
                    "Task changed after this snapshot was loaded; stale write rejected"
                )
            self._validate_director_generation_write(row["record_json"], record)
            record.updated_at = datetime.now(timezone.utc)
            replacement_json = self._record_json_without_effect_projection(record)
            self._mark_cleanup_for_rewrite(
                connection, row["record_json"], replacement_json
            )
            self._append_effects(connection, record.task_id, record.task_effect_ledger)
            self._append_provider_interactions(
                connection, record.task_id, record.provider_interaction_ledger
            )
            self._append_direct_production_credits(connection, record)
            self._hydrate_effects(connection, record)
            self._hydrate_provider_interactions(connection, record)
            replacement_json = self._record_json_without_effect_projection(record)
            updated = connection.execute(
                """
                UPDATE tasks
                SET status = ?, record_json = ?, updated_at = ?,
                    record_version = record_version + 1
                WHERE task_id = ? AND record_version = ?
                """,
                (
                    record.status.value,
                    replacement_json,
                    record.updated_at.isoformat(),
                    record.task_id,
                    current_version,
                ),
            )
            if updated.rowcount != 1:
                raise StaleTaskWrite(
                    "Task changed while the write was being committed"
                )
            record._store_version = current_version + 1
        self._attempt_privacy_cleanup()
        return record

    def mutate_if_version(
        self,
        task_id: str,
        expected_version: int,
        mutator: Callable[[TaskRecord], None],
        *,
        gateway_nonce_digest: str | None = None,
        gateway_nonce_expires_at: int | None = None,
    ) -> TaskRecord | None:
        """Atomically validate and consume one persisted task decision.

        The mutator runs against the current persisted record while an immediate
        SQLite write transaction is held. Every task save advances
        ``record_version``, so a second process holding the same decision can
        neither overwrite the winner nor schedule duplicate follow-up work.
        ``None`` means that another writer consumed or changed the task first.
        """

        self._attempt_privacy_cleanup()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None or int(row["record_version"]) != expected_version:
                return None

            self._require_effect_migration(row)
            record = self._record_from_row(connection, row)
            mutator(record)
            self._validate_director_generation_write(row["record_json"], record)
            if gateway_nonce_digest is not None:
                if gateway_nonce_expires_at is None:
                    raise ValueError("Gateway nonce expiry is required")
                try:
                    connection.execute(
                        """INSERT INTO slack_gateway_nonces(
                            nonce_digest, task_id, consumed_at, expires_at
                        ) VALUES (?, ?, ?, ?)""",
                        (
                            gateway_nonce_digest,
                            task_id,
                            int(datetime.now(timezone.utc).timestamp()),
                            gateway_nonce_expires_at,
                        ),
                    )
                except sqlite3.IntegrityError:
                    return None
            replacement_json = self._record_json_without_effect_projection(record)
            self._mark_cleanup_for_rewrite(
                connection, row["record_json"], replacement_json
            )
            self._append_effects(connection, task_id, record.task_effect_ledger)
            self._append_provider_interactions(
                connection, task_id, record.provider_interaction_ledger
            )
            record.updated_at = datetime.now(timezone.utc)
            self._append_direct_production_credits(connection, record)
            self._hydrate_effects(connection, record)
            self._hydrate_provider_interactions(connection, record)
            replacement_json = self._record_json_without_effect_projection(record)
            updated = connection.execute(
                """
                UPDATE tasks
                SET status = ?, record_json = ?, updated_at = ?,
                    record_version = record_version + 1
                WHERE task_id = ? AND record_version = ?
                """,
                (
                    record.status.value,
                    replacement_json,
                    record.updated_at.isoformat(),
                    task_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                return None
            record._store_version = expected_version + 1
        self._attempt_privacy_cleanup()
        return record

    @staticmethod
    def _validate_director_generation_write(previous_json: str, record: TaskRecord) -> None:
        previous = TaskStore._task_record_from_persisted_json(previous_json)
        for generation, identity in previous.director_generations.items():
            if record.director_generations.get(generation) != identity:
                raise CommunicationEvidenceFailure("Persisted Director generation identity is immutable")
        for generation in record.director_generations.keys() - previous.director_generations.keys():
            if generation != previous.plan_generation + 1:
                raise CommunicationEvidenceFailure("Director identity cannot backfill historical generations")
        for event in record.provider_interaction_ledger:
            if event.director_identity is not None and not director_evidence_matches(
                event, record.director_generations.get(event.plan_generation)
            ):
                raise CommunicationEvidenceFailure("Director event differs from its persisted generation identity")

    def list_task_effects(self, task_id: str) -> list[TaskEffectEvidence]:
        with self._lock, self._connect() as connection:
            return self._load_effects(connection, task_id)

    def list_provider_interactions(
        self, task_id: str
    ) -> list[ProviderInteractionEvent]:
        with self._lock, self._connect() as connection:
            return self._load_provider_interactions(connection, task_id)

    @staticmethod
    def _workload_row(
        connection: sqlite3.Connection, reservation_key: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT reservation.*, event.outcome AS final_outcome
            FROM workload_assignment_reservations AS reservation
            LEFT JOIN workload_assignment_events AS event
              ON event.reservation_key = reservation.reservation_key
            WHERE reservation.reservation_key = ?""",
            (reservation_key,),
        ).fetchone()

    @staticmethod
    def _workload_reservation_from_row(
        row: sqlite3.Row, *, created: bool
    ) -> WorkloadLedgerReservation:
        try:
            available_payload = json.loads(str(row["available_primary_json"]))
            violations_payload = json.loads(str(row["violations_json"]))
            advisories_payload = json.loads(str(row["advisories_json"]))
            available = frozenset(Provider(str(item)) for item in available_payload)
            configured_mode = EnforcementMode(str(row["configured_mode"]))
            shadow_assignment_limit = int(row["shadow_assignment_limit"])
            if shadow_assignment_limit <= 0:
                raise ValueError("persisted shadow assignment limit is invalid")
            decision = DecisionResult(
                mode=EnforcementMode(str(row["mode"])),
                outcome=DecisionOutcome(str(row["decision_outcome"])),
                allowed=bool(row["allowed"]),
                violations=tuple(str(item) for item in violations_payload),
                advisories=tuple(str(item) for item in advisories_payload),
            )
            final_outcome = (
                WorkloadAttemptOutcome(str(row["final_outcome"]))
                if row["final_outcome"] is not None
                else None
            )
            recorded_at = datetime.fromisoformat(str(row["recorded_at"]))
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise WorkloadReservationConflict(
                "persisted workload reservation is malformed"
            ) from exc
        if recorded_at.tzinfo is None:
            raise WorkloadReservationConflict(
                "persisted workload reservation timestamp is not timezone-aware"
            )
        return WorkloadLedgerReservation(
            reservation_key=str(row["reservation_key"]),
            task_id=str(row["task_id"]),
            assignment_id=str(row["assignment_id"]),
            phase=str(row["phase"]),
            provider=Provider(str(row["provider"])),
            effort_units=int(row["effort_units"]),
            kind=WorkKind(str(row["work_kind"])),
            size=WorkSize(str(row["work_size"])),
            model_label=(
                str(row["model_label"]) if row["model_label"] is not None else None
            ),
            available_primary_providers=available,
            configured_mode=configured_mode,
            shadow_assignment_limit=shadow_assignment_limit,
            decision=decision,
            created=created,
            final_outcome=final_outcome,
            recorded_at=recorded_at,
        )

    def reserve_workload_assignment(
        self,
        *,
        reservation_key: str,
        task_id: str,
        assignment_id: str,
        phase: str,
        provider: Provider,
        effort_units: int,
        kind: WorkKind,
        size: WorkSize,
        model_label: str | None,
        available_primary_providers: frozenset[Provider],
        mode: EnforcementMode,
        shadow_assignment_limit: int = 5,
        allocation_profile: str = DEFAULT_ALLOCATION_PROFILE,
    ) -> WorkloadLedgerReservation:
        """Atomically evaluate and reserve one prospective provider invocation.

        ``BEGIN IMMEDIATE`` serializes this read-check-append sequence across
        processes.  The evaluator receives the latest nineteen effective
        attempts: actual ``invoked`` rows plus unresolved reservations.  Pending
        rows are conservatively included only while provider-call outcome is
        unknown, preventing simultaneous workers from both crossing a cap. They
        disappear from the effective window when append-only evidence classifies
        them as pre-submit failure, cached replay, or enforced block.

        Reusing the exact reservation key returns its original decision and never
        appends a second workload row. Reusing it with a different assignment
        identity fails closed.  The one exception is a reservation already
        finalized as ``failed_pre_submit``: that attempt reached no provider, so
        returning its stale decision would hand the caller a live allow whose
        every outcome collides with append-only terminal evidence.  Such a key
        advances to the next attempt-scoped key and is evaluated afresh.

        The exponential unresolved-rollover projection runs *outside* the
        exclusive write transaction, against a fingerprinted ledger snapshot.
        The commit transaction re-reads that fingerprint and only proceeds when
        it is unchanged, so the compare-and-set guarantee is preserved without
        holding the cross-process write lock for the projection.
        """

        for value, label in (
            (reservation_key, "reservation key"),
            (task_id, "task id"),
            (assignment_id, "assignment id"),
            (phase, "phase"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"workload {label} must not be blank")
        if len(reservation_key) > 2_000:
            raise ValueError("workload reservation key is too long")
        if not isinstance(mode, EnforcementMode):
            raise ValueError("workload mode must be enforced or shadow")
        if (
            isinstance(shadow_assignment_limit, bool)
            or not isinstance(shadow_assignment_limit, int)
            or shadow_assignment_limit <= 0
        ):
            raise ValueError("workload shadow assignment limit must be positive")
        policy = WorkloadPolicy.for_profile(allocation_profile)
        profile_digest = allocation_profile_digest(allocation_profile)
        available = frozenset(available_primary_providers)
        # Built up front so a structurally invalid request is rejected before any
        # database work, exactly as before. The committed request below carries
        # the attempt-scoped key this call actually owns.
        request = AssignmentRequest(
            assignment_id=reservation_key,
            provider=provider,
            kind=kind,
            size=size,
            model_label=model_label,
            effort_units=effort_units,
        )
        available_json = json.dumps(
            sorted(item.value for item in available),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        requested_identity = (
            task_id,
            assignment_id,
            phase,
            provider.value,
            effort_units,
            kind.value,
            size.value,
            model_label,
        )
        direct_provider = direct_production_provider(provider)
        direct_relevant = direct_provider is not None and self._is_creditable_accepted_work(
            provider, kind
        )

        with self._lock:
            for _ in range(self._MAX_ROLLOVER_SNAPSHOT_ATTEMPTS):
                # Snapshot pass: no write transaction is held, so the exponential
                # projection below cannot block another process's reservation.
                with self._connect() as connection:
                    fingerprint = self._workload_ledger_fingerprint(connection)
                    effective_key, persisted = self._resolve_workload_reservation_key(
                        connection,
                        reservation_key=reservation_key,
                        requested_identity=requested_identity,
                    )
                    if persisted is None:
                        effective_mode = self._effective_assignment_mode(
                            connection,
                            assignment_id=assignment_id,
                            mode=mode,
                            shadow_assignment_limit=shadow_assignment_limit,
                        )
                        overall_rows = self._overall_rollover_rows(connection)
                        direct_inputs = (
                            self._direct_rollover_inputs(connection, assignment_id)
                            if direct_relevant
                            else None
                        )

                if persisted is None:
                    committed_request = (
                        request
                        if effective_key == reservation_key
                        else replace(request, assignment_id=effective_key)
                    )
                    # [[Claude Code | Claude Fable 5 | 2026-08-29 | 05:52 UTC | BEGIN]]
                    # Unresolved-rollover projection.  Only pending rows that can
                    # still enter the rolling window are relevant: a pending row
                    # older than the nineteen most recent invoked rows can never
                    # displace them, so it must not act as a lifetime global
                    # block.  Each relevant pending row is projected both ways
                    # (invoked / not invoked); a sibling is safe only when every
                    # projection agrees it clears the caps.
                    # Total effort controls only prospective usage. Project every
                    # pending sibling both ways so concurrent reservations cannot
                    # move beyond a provider's soft share band, while accepted-production credit
                    # remains exclusively in the separate direct ledger.
                    extra_violations: list[str] = []
                    extra_violations.extend(
                        self._overall_rollover_violations(
                            overall_rows,
                            request=committed_request,
                            available=available,
                            mode=effective_mode,
                            policy=policy,
                        )
                    )
                    if direct_inputs is not None:
                        extra_violations.extend(
                            self._direct_rollover_violations(
                                direct_inputs,
                                request=committed_request,
                                assignment_id=assignment_id,
                                available=available,
                                mode=effective_mode,
                            policy=policy,
                            )
                        )
                    # [[Claude Code | Claude Fable 5 | 2026-08-29 | 05:52 UTC | END]]

                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("""CREATE TABLE IF NOT EXISTS workload_profile_bindings (
                        assignment_id TEXT PRIMARY KEY,
                        allocation_profile TEXT NOT NULL,
                        profile_digest TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        work_kind TEXT NOT NULL,
                        admitted INTEGER NOT NULL CHECK(admitted IN (0, 1))
                    )""")
                    for mutation in ("UPDATE", "DELETE"):
                        connection.execute(
                            f"CREATE TRIGGER IF NOT EXISTS workload_profile_bindings_no_{mutation.lower()} "
                            f"BEFORE {mutation} ON workload_profile_bindings "
                            "BEGIN SELECT RAISE(ABORT, 'workload profile bindings are immutable'); END"
                        )
                    if kind is not WorkKind.COORDINATION and connection.execute(
                        "SELECT 1 FROM workload_assignment_reservations AS r LEFT JOIN workload_profile_bindings AS b ON b.assignment_id = r.assignment_id WHERE b.assignment_id IS NULL LIMIT 1"
                    ).fetchone() is not None:
                        raise WorkloadReservationConflict("legacy workload history requires explicit profile admission reconciliation")
                    bound = connection.execute(
                        "SELECT allocation_profile, profile_digest, provider, task_id, work_kind, admitted FROM workload_profile_bindings WHERE assignment_id = ?",
                        (assignment_id,),
                    ).fetchone()
                    if bound is not None and (
                        str(bound["allocation_profile"]) != allocation_profile
                        or str(bound["profile_digest"]) != profile_digest
                        or str(bound["provider"]) != provider.value
                        or str(bound["task_id"]) != task_id
                        or str(bound["work_kind"]) != kind.value
                    ):
                        raise WorkloadReservationConflict("workload allocation profile binding changed")
                    if bound is not None and not bool(bound["admitted"]) and kind is not WorkKind.COORDINATION and persisted is None:
                        raise WorkloadReservationConflict("non-admitted allocation binding requires a new approved assignment")
                    if bound is None:
                        if connection.execute(
                            "SELECT 1 FROM workload_assignment_reservations WHERE assignment_id = ? LIMIT 1",
                            (assignment_id,),
                        ).fetchone() is not None:
                            raise WorkloadReservationConflict("legacy workload reservation lacks frozen allocation profile")

                    if self._workload_ledger_fingerprint(connection) != fingerprint:
                        # The ledger moved while the projection ran. Nothing was
                        # written, so discard the snapshot and take it again.
                        continue
                    # The fingerprint is unchanged, so this resolves identically;
                    # re-running it keeps the committing transaction self-contained.
                    effective_key, persisted = self._resolve_workload_reservation_key(
                        connection,
                        reservation_key=reservation_key,
                        requested_identity=requested_identity,
                    )
                    if persisted is not None:
                        if persisted.final_outcome is not None:
                            return persisted
                        if (
                            persisted.configured_mode is not mode
                            or persisted.shadow_assignment_limit
                            != shadow_assignment_limit
                            or persisted.available_primary_providers != available
                        ):
                            raise WorkloadReservationConflict(
                                "pending workload reservation authority changed before dispatch"
                            )
                        return persisted

                    effective_rows = connection.execute(
                        """SELECT reservation.reservation_key, reservation.provider,
                                  reservation.effort_units
                        FROM workload_assignment_reservations AS reservation
                        LEFT JOIN workload_assignment_events AS event
                          ON event.reservation_key = reservation.reservation_key
                        WHERE event.outcome = 'invoked'
                           OR (event.outcome IS NULL AND reservation.provider = ?)
                        ORDER BY reservation.ledger_id DESC
                        LIMIT ?""",
                        (provider.value, DEFAULT_POLICY.rolling_window_size - 1),
                    ).fetchall()
                    history = [
                        WorkloadRecord(
                            assignment_id=str(row["reservation_key"]),
                            provider=Provider(str(row["provider"])),
                            effort_units=int(row["effort_units"]),
                        )
                        for row in reversed(effective_rows)
                    ]
                    direct_history: list[DirectProductionRecord] = []
                    if direct_relevant:
                        # Pending and invoked-but-uncredited implementations are
                        # handled symmetrically by _direct_rollover_violations.
                        # The base history therefore remains the canonical
                        # append-only credit window instead of mixing unrelated
                        # table clocks and ordinals.
                        direct_rows = connection.execute(
                            """SELECT credit.assignment_id, credit.provider,
                                      credit.effort_units
                            FROM direct_production_credits AS credit
                            WHERE credit.provider <> 'gemini'
                                OR EXISTS (
                                    SELECT 1
                                    FROM direct_production_integration_evidence AS integration
                                    WHERE integration.credit_key = credit.credit_key
                                      AND EXISTS (
                                          SELECT 1
                                          FROM direct_production_integration_consumption AS consumption
                                          WHERE consumption.credit_key = credit.credit_key
                                      )
                                )
                            ORDER BY credit.credit_id DESC
                            LIMIT ?""",
                            (DEFAULT_POLICY.rolling_window_size - 1,),
                        ).fetchall()
                        for row in reversed(direct_rows):
                            mapped_provider = self._stored_direct_production_provider(
                                str(row["provider"])
                            )
                            direct_history.append(
                                DirectProductionRecord(
                                    assignment_id=str(row["assignment_id"]),
                                    provider=mapped_provider,
                                    effort_units=int(row["effort_units"]),
                                )
                            )
                    decision = evaluate_assignment(
                        committed_request,
                        history=history,
                        direct_production_history=direct_history,
                        direct_production_assignment_id=assignment_id,
                        available_primary_providers=available,
                        policy=policy,
                        mode=effective_mode,
                    )
                    # Profile admission retains real reservations after failure or
                    # ambiguity; accepted-output accounting stays a separate ledger.
                    if kind is not WorkKind.COORDINATION and (bound is None or not bool(bound["admitted"])):
                        lane = canonical_allocation_lane(provider.value)
                        target = allocation_targets(allocation_profile)[lane]
                        recent = connection.execute(
                            "SELECT provider FROM workload_profile_bindings WHERE admitted = 1 ORDER BY rowid DESC LIMIT 19"
                        ).fetchall()
                        count = 1 + sum(canonical_allocation_lane(str(item["provider"])) == lane for item in recent)
                        if target == 0:
                            extra_violations.append(f"allocation_profile_lane_ineligible:{lane}")
                        elif count * 5 > target + 5:
                            extra_violations.append(f"allocation_admission_hard_cap_exceeded:{lane}:{target + 5}")
                    decision = self._decision_with_violations(
                        decision, extra_violations
                    )
                    if bound is None:
                        connection.execute(
                            "INSERT INTO workload_profile_bindings VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (assignment_id, allocation_profile, profile_digest, provider.value, task_id, kind.value,
                             int(decision.allowed and kind is not WorkKind.COORDINATION)),
                        )
                    recorded_at = datetime.now(timezone.utc)
                    connection.execute(
                        """INSERT INTO workload_assignment_reservations(
                            reservation_key, task_id, assignment_id, phase, provider,
                            effort_units, work_kind, work_size, model_label,
                            available_primary_json, configured_mode,
                            shadow_assignment_limit, mode, decision_outcome, allowed,
                            violations_json, advisories_json, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            effective_key,
                            task_id,
                            assignment_id,
                            phase,
                            provider.value,
                            effort_units,
                            kind.value,
                            size.value,
                            model_label,
                            available_json,
                            mode.value,
                            shadow_assignment_limit,
                            effective_mode.value,
                            decision.outcome.value,
                            int(decision.allowed),
                            json.dumps(
                                list(decision.violations),
                                ensure_ascii=True,
                                separators=(",", ":"),
                            ),
                            json.dumps(
                                list(decision.advisories),
                                ensure_ascii=True,
                                separators=(",", ":"),
                            ),
                            recorded_at.isoformat(),
                        ),
                    )
                    if not decision.allowed:
                        connection.execute(
                            """INSERT INTO workload_assignment_events(
                                reservation_key, outcome, recorded_at
                            ) VALUES (?, 'blocked', ?)""",
                            (effective_key, recorded_at.isoformat()),
                        )
                    committed = self._workload_row(connection, effective_key)
                    if committed is None:  # Defensive: insert and read share a transaction.
                        raise RuntimeError(
                            "workload reservation disappeared during commit"
                        )
                    return self._workload_reservation_from_row(
                        committed, created=True
                    )

        raise WorkloadReservationConflict(
            "the workload ledger changed on every rollover projection attempt; "
            "no reservation was recorded"
        )

    # A pre-submit failure reached no provider, so the caller may try again. Each
    # retry takes its own append-only reservation row rather than reusing the
    # finalized one. The ladder is bounded so an unbounded key search can never
    # stand in for a fail-closed stop.
    _MAX_PRE_SUBMIT_RETRY_RESERVATIONS = 20

    # The snapshot the projection ran against must still be the ledger this
    # reservation commits into. A few retries absorb ordinary cross-process
    # interleaving; past that the reservation fails closed.
    _MAX_ROLLOVER_SNAPSHOT_ATTEMPTS = 5

    @staticmethod
    def _workload_ledger_fingerprint(
        connection: sqlite3.Connection,
    ) -> tuple[int, ...]:
        """Return a value that changes whenever any rollover input changes.

        Each table below is append-only under database triggers, so a row count
        paired with the highest identifier ever assigned determines its contents
        exactly.  Comparing this is what lets the exponential projection run
        outside the exclusive write transaction without losing compare-and-set
        correctness: a mismatch means the snapshot is stale and is discarded.
        """

        row = connection.execute(
            """SELECT
                (SELECT COUNT(*) FROM workload_assignment_reservations),
                (SELECT COALESCE(MAX(ledger_id), 0)
                   FROM workload_assignment_reservations),
                (SELECT COUNT(*) FROM workload_assignment_events),
                (SELECT COALESCE(MAX(event_id), 0)
                   FROM workload_assignment_events),
                (SELECT COUNT(*) FROM direct_production_credits),
                (SELECT COALESCE(MAX(credit_id), 0)
                   FROM direct_production_credits),
                (SELECT COUNT(*) FROM direct_production_integration_evidence),
                (SELECT COUNT(*) FROM direct_production_integration_consumption),
                (SELECT COUNT(*) FROM direct_production_no_credit),
                (SELECT COALESCE(MAX(disposition_id), 0)
                   FROM direct_production_no_credit)"""
        ).fetchone()
        return tuple(int(value) for value in row)

    @classmethod
    def _resolve_workload_reservation_key(
        cls,
        connection: sqlite3.Connection,
        *,
        reservation_key: str,
        requested_identity: tuple[Any, ...],
    ) -> tuple[str, WorkloadLedgerReservation | None]:
        """Return the key this attempt owns and any reservation already at it.

        A reservation finalized as ``failed_pre_submit`` never reached a
        provider.  Returning it would hand the caller a stale allow decision
        whose every terminal outcome conflicts with the append-only evidence
        already recorded, so the search advances to the next attempt-scoped key.
        Every other terminal outcome, and any pending row, is genuinely this
        attempt's and is returned unchanged.
        """

        key = reservation_key
        for attempt in range(cls._MAX_PRE_SUBMIT_RETRY_RESERVATIONS + 1):
            existing = cls._workload_row(connection, key)
            if existing is None:
                return key, None
            identity = (
                str(existing["task_id"]),
                str(existing["assignment_id"]),
                str(existing["phase"]),
                str(existing["provider"]),
                int(existing["effort_units"]),
                str(existing["work_kind"]),
                str(existing["work_size"]),
                (
                    str(existing["model_label"])
                    if existing["model_label"] is not None
                    else None
                ),
            )
            if identity != requested_identity:
                raise WorkloadReservationConflict(
                    "workload reservation key was reused with different assignment identity"
                )
            persisted = cls._workload_reservation_from_row(existing, created=False)
            if persisted.final_outcome is not WorkloadAttemptOutcome.FAILED_PRE_SUBMIT:
                return key, persisted
            key = f"{reservation_key}#pre-submit-retry-{attempt + 1}"
        raise WorkloadReservationConflict(
            "workload reservation exhausted its bounded pre-submit retry ladder"
        )

    @classmethod
    def _effective_assignment_mode(
        cls,
        connection: sqlite3.Connection,
        *,
        assignment_id: str,
        mode: EnforcementMode,
        shadow_assignment_limit: int,
    ) -> EnforcementMode:
        """Return the mode this assignment unit is already bound to, if any."""

        unit_authority = connection.execute(
            """SELECT configured_mode, shadow_assignment_limit, mode
            FROM workload_assignment_reservations
            WHERE assignment_id = ?
            ORDER BY ledger_id ASC
            LIMIT 1""",
            (assignment_id,),
        ).fetchone()
        if unit_authority is not None:
            if (
                EnforcementMode(str(unit_authority["configured_mode"])) is not mode
                or int(unit_authority["shadow_assignment_limit"])
                != shadow_assignment_limit
            ):
                raise WorkloadReservationConflict(
                    "workload assignment unit authority changed between phases"
                )
            return EnforcementMode(str(unit_authority["mode"]))
        if mode is EnforcementMode.SHADOW:
            return cls._effective_workload_mode(
                connection,
                configured_mode=mode,
                shadow_assignment_limit=shadow_assignment_limit,
            )
        return mode

    @staticmethod
    def _decision_with_violations(
        decision: DecisionResult, violations: list[str]
    ) -> DecisionResult:
        merged = tuple(dict.fromkeys((*decision.violations, *violations)))
        if merged == decision.violations:
            return decision
        if decision.mode is EnforcementMode.SHADOW:
            return DecisionResult(
                mode=decision.mode,
                outcome=DecisionOutcome.SHADOW_WOULD_BLOCK,
                allowed=True,
                violations=merged,
                advisories=decision.advisories,
            )
        return DecisionResult(
            mode=decision.mode,
            outcome=DecisionOutcome.BLOCK,
            allowed=False,
            violations=merged,
            advisories=decision.advisories,
        )

    # [[Claude Code | Claude Fable 5 | 2026-08-29 | 05:52 UTC | BEGIN]]
    # Exact two-way projection is exhaustive over in-window pending rows; above
    # this many siblings the bounded conservative rule applies instead.
    _ROLLOVER_PROJECTION_LIMIT = 10

    @staticmethod
    def _projection_disagreement(
        decisions: list[DecisionResult], tag: str
    ) -> list[str]:
        """Merge projected violations; tag the case where resolution decides."""

        blocking = [decision for decision in decisions if decision.violations]
        if not blocking:
            return []
        merged: list[str] = []
        for decision in blocking:
            merged.extend(decision.violations)
        if len(blocking) != len(decisions):
            merged.append(tag)
        return list(dict.fromkeys(merged))

    @staticmethod
    def _overall_rollover_rows(
        connection: sqlite3.Connection,
    ) -> list[dict[str, Any]]:
        """Read the in-window invoked and pending rows the projection needs."""

        window = DEFAULT_POLICY.rolling_window_size
        boundary = connection.execute(
            """SELECT reservation.ledger_id
            FROM workload_assignment_reservations AS reservation
            JOIN workload_assignment_events AS event
              ON event.reservation_key = reservation.reservation_key
            WHERE event.outcome = 'invoked'
            ORDER BY reservation.ledger_id DESC
            LIMIT 1 OFFSET ?""",
            (window - 2,),
        ).fetchone()
        rows = connection.execute(
            """SELECT reservation.ledger_id, reservation.reservation_key,
                      reservation.provider, reservation.effort_units,
                      event.outcome AS final_outcome
            FROM workload_assignment_reservations AS reservation
            LEFT JOIN workload_assignment_events AS event
              ON event.reservation_key = reservation.reservation_key
            WHERE (event.outcome = 'invoked' OR event.outcome IS NULL)
              AND reservation.ledger_id >= ?
            ORDER BY reservation.ledger_id ASC""",
            (int(boundary["ledger_id"]) if boundary is not None else 0,),
        ).fetchall()
        return [dict(row) for row in rows]

    @classmethod
    def _overall_rollover_violations(
        cls,
        rows: list[dict[str, Any]],
        *,
        request: AssignmentRequest,
        available: frozenset[Provider],
        mode: EnforcementMode,
        policy: WorkloadPolicy = DEFAULT_POLICY,
    ) -> list[str]:
        """Project every in-window pending row as invoked and as not invoked.

        This is a pure function of the snapshot rows so it can run outside the
        exclusive write transaction.
        """

        window = DEFAULT_POLICY.rolling_window_size
        pending = [row for row in rows if row["final_outcome"] is None]
        if not pending:
            return []
        invoked = [row for row in rows if row["final_outcome"] is not None]
        if len(pending) > cls._ROLLOVER_PROJECTION_LIMIT:
            if len(invoked) + len(pending) >= window - 1:
                return ["workload_rollover_pending_unresolved"]
            return []
        decisions: list[DecisionResult] = []
        for mask in range(1 << len(pending)):
            chosen = [
                row for index, row in enumerate(pending) if mask & (1 << index)
            ]
            projected = sorted(
                invoked + chosen, key=lambda row: int(row["ledger_id"])
            )[-(window - 1) :]
            history = [
                WorkloadRecord(
                    assignment_id=str(row["reservation_key"]),
                    provider=Provider(str(row["provider"])),
                    effort_units=int(row["effort_units"]),
                )
                for row in projected
            ]
            decisions.append(
                evaluate_assignment(
                    request,
                    history=history,
                    available_primary_providers=available,
                    policy=policy,
                    mode=mode,
                )
            )
        return cls._projection_disagreement(
            decisions, "workload_rollover_pending_unresolved"
        )

    @staticmethod
    def _direct_rollover_inputs(
        connection: sqlite3.Connection, assignment_id: str
    ) -> tuple[list[DirectProductionRecord], list[DirectProductionRecord], int]:
        """Read the uncredited in-flight units, recent credits, and credit total.

        A unit carrying a terminal no-credit disposition is excluded: it was
        rejected or otherwise terminated and will never be credited, so leaving
        it "unresolved" would block later production for the ledger's lifetime.
        """

        window = DEFAULT_POLICY.rolling_window_size
        unresolved_rows = connection.execute(
            """SELECT reservation.assignment_id, reservation.provider,
                      reservation.effort_units
            FROM workload_assignment_reservations AS reservation
            LEFT JOIN workload_assignment_events AS event
              ON event.reservation_key = reservation.reservation_key
            WHERE (
                  reservation.work_kind = 'implementation'
                  OR (
                      reservation.provider = 'perplexity'
                      AND reservation.work_kind = 'research'
                  )
              )
              AND reservation.provider <> 'openai'
              AND reservation.assignment_id <> ?
              AND NOT EXISTS (
                  SELECT 1 FROM direct_production_credits AS credit
                  WHERE credit.assignment_id = reservation.assignment_id
                    AND (
                        credit.provider <> 'gemini'
                        OR EXISTS (
                            SELECT 1
                            FROM direct_production_integration_evidence AS integration
                            WHERE integration.credit_key = credit.credit_key
                              AND EXISTS (
                                  SELECT 1
                                  FROM direct_production_integration_consumption AS consumption
                                  WHERE consumption.credit_key = credit.credit_key
                              )
                        )
                    )
              )
              AND NOT EXISTS (
                  SELECT 1 FROM direct_production_no_credit AS disposition
                  WHERE disposition.assignment_id = reservation.assignment_id
              )
              AND (
                  event.outcome IS NULL
                  OR event.outcome = 'invoked'
              )
            ORDER BY reservation.ledger_id ASC""",
            (assignment_id,),
        ).fetchall()
        unresolved: dict[str, DirectProductionRecord] = {}
        for row in unresolved_rows:
            mapped = TaskStore._stored_direct_production_provider(
                str(row["provider"])
            )
            unresolved[str(row["assignment_id"])] = DirectProductionRecord(
                assignment_id=str(row["assignment_id"]),
                provider=mapped,
                effort_units=int(row["effort_units"]),
            )
        credit_rows = connection.execute(
            """SELECT credit.assignment_id, credit.provider,
                      credit.effort_units
            FROM direct_production_credits AS credit
            WHERE credit.provider <> 'gemini'
               OR EXISTS (
                   SELECT 1
                   FROM direct_production_integration_evidence AS integration
                   WHERE integration.credit_key = credit.credit_key
                     AND EXISTS (
                         SELECT 1
                         FROM direct_production_integration_consumption AS consumption
                         WHERE consumption.credit_key = credit.credit_key
                     )
               )
            ORDER BY credit.credit_id DESC
            LIMIT ?""",
            (window - 1,),
        ).fetchall()
        credits: list[DirectProductionRecord] = []
        for row in reversed(credit_rows):
            mapped = TaskStore._stored_direct_production_provider(
                str(row["provider"])
            )
            credits.append(
                DirectProductionRecord(
                    assignment_id=str(row["assignment_id"]),
                    provider=mapped,
                    effort_units=int(row["effort_units"]),
                )
            )
        credit_total = connection.execute(
            """SELECT COUNT(*) AS count
            FROM direct_production_credits AS credit
            WHERE credit.provider <> 'gemini'
               OR EXISTS (
                   SELECT 1
                   FROM direct_production_integration_evidence AS integration
                   WHERE integration.credit_key = credit.credit_key
                     AND EXISTS (
                         SELECT 1
                         FROM direct_production_integration_consumption AS consumption
                         WHERE consumption.credit_key = credit.credit_key
                     )
               )"""
        ).fetchone()
        return list(unresolved.values()), credits, int(credit_total["count"])

    @classmethod
    def _direct_rollover_violations(
        cls,
        inputs: tuple[list[DirectProductionRecord], list[DirectProductionRecord], int],
        *,
        request: AssignmentRequest,
        assignment_id: str,
        available: frozenset[Provider],
        mode: EnforcementMode,
        policy: WorkloadPolicy = DEFAULT_POLICY,
    ) -> list[str]:
        """Project every uncredited in-flight implementation unit both ways.

        This is a pure function of the snapshot inputs so it can run outside the
        exclusive write transaction.
        """

        window = DEFAULT_POLICY.rolling_window_size
        units, credits, credit_total = inputs
        if not units:
            return []
        if len(units) > cls._ROLLOVER_PROJECTION_LIMIT:
            if credit_total + len(units) >= window - 1:
                return ["direct_production_rollover_pending_unresolved"]
            return []
        decisions: list[DecisionResult] = []
        for mask in range(1 << len(units)):
            chosen = [
                unit for index, unit in enumerate(units) if mask & (1 << index)
            ]
            # A unit credited later is appended as the newest direct entry.
            projected = (credits + chosen)[-(window - 1) :]
            decisions.append(
                evaluate_assignment(
                    request,
                    direct_production_history=projected,
                    direct_production_assignment_id=assignment_id,
                    available_primary_providers=available,
                    policy=policy,
                    mode=mode,
                )
            )
        return cls._projection_disagreement(
            decisions, "direct_production_rollover_pending_unresolved"
        )
    # [[Claude Code | Claude Fable 5 | 2026-08-29 | 05:52 UTC | END]]

    @staticmethod
    def _effective_workload_mode(
        connection: sqlite3.Connection,
        *,
        configured_mode: EnforcementMode,
        shadow_assignment_limit: int,
    ) -> EnforcementMode:
        if configured_mode is EnforcementMode.ENFORCED:
            return configured_mode
        completed_shadow = connection.execute(
            """SELECT COUNT(DISTINCT reservation.assignment_id) AS count
            FROM workload_assignment_reservations AS reservation
            JOIN workload_assignment_events AS event
              ON event.reservation_key = reservation.reservation_key
            WHERE reservation.mode = 'shadow'"""
        ).fetchone()
        if int(completed_shadow["count"]) >= shadow_assignment_limit:
            return EnforcementMode.ENFORCED
        return EnforcementMode.SHADOW

    def effective_workload_mode(
        self,
        *,
        configured_mode: EnforcementMode,
        shadow_assignment_limit: int = 5,
    ) -> EnforcementMode:
        """Return the persisted mode that the next assignment will enforce."""

        if not isinstance(configured_mode, EnforcementMode):
            raise ValueError("workload mode must be enforced or shadow")
        if (
            isinstance(shadow_assignment_limit, bool)
            or not isinstance(shadow_assignment_limit, int)
            or shadow_assignment_limit <= 0
        ):
            raise ValueError("workload shadow assignment limit must be positive")
        with self._lock, self._connect() as connection:
            return self._effective_workload_mode(
                connection,
                configured_mode=configured_mode,
                shadow_assignment_limit=shadow_assignment_limit,
            )

    def finalize_workload_reservation(
        self,
        reservation_key: str,
        outcome: WorkloadAttemptOutcome,
    ) -> bool:
        """Append one terminal classification; return False for exact replay."""

        if not isinstance(outcome, WorkloadAttemptOutcome):
            raise ValueError("workload outcome is invalid")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                """SELECT 1 FROM workload_assignment_reservations
                WHERE reservation_key = ?""",
                (reservation_key,),
            ).fetchone()
            if reservation is None:
                raise KeyError(reservation_key)
            existing = connection.execute(
                """SELECT outcome FROM workload_assignment_events
                WHERE reservation_key = ?""",
                (reservation_key,),
            ).fetchone()
            if existing is not None:
                if str(existing["outcome"]) == outcome.value:
                    return False
                raise WorkloadReservationConflict(
                    "workload reservation already has a different terminal outcome"
                )
            connection.execute(
                """INSERT INTO workload_assignment_events(
                    reservation_key, outcome, recorded_at
                ) VALUES (?, ?, ?)""",
                (
                    reservation_key,
                    outcome.value,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return True

    def get_workload_reservation(
        self, reservation_key: str
    ) -> WorkloadLedgerReservation | None:
        with self._lock, self._connect() as connection:
            row = self._workload_row(connection, reservation_key)
            return (
                self._workload_reservation_from_row(row, created=False)
                if row is not None
                else None
            )

    def list_recent_workload_attempts(
        self, *, limit: int = 19
    ) -> list[WorkloadRecord]:
        """Return only actual provider invocation attempts, oldest to newest."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("workload attempt limit must be between 1 and 100")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT reservation.reservation_key, reservation.provider,
                          reservation.effort_units
                FROM workload_assignment_reservations AS reservation
                JOIN workload_assignment_events AS event
                  ON event.reservation_key = reservation.reservation_key
                WHERE event.outcome = 'invoked'
                ORDER BY reservation.ledger_id DESC
                LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            WorkloadRecord(
                assignment_id=str(row["reservation_key"]),
                provider=Provider(str(row["provider"])),
                effort_units=int(row["effort_units"]),
            )
            for row in reversed(rows)
        ]

    def list_recent_direct_production(
        self, *, limit: int = 19
    ) -> list[DirectProductionRecord]:
        """Return authenticated accepted-production credits, oldest to newest."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError(
                "direct-production credit limit must be between 1 and 100"
            )
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT credit.assignment_id, credit.provider,
                          credit.effort_units
                FROM direct_production_credits AS credit
                WHERE credit.provider <> 'gemini'
                   OR EXISTS (
                       SELECT 1
                       FROM direct_production_integration_evidence AS integration
                       WHERE integration.credit_key = credit.credit_key
                         AND EXISTS (
                             SELECT 1
                             FROM direct_production_integration_consumption AS consumption
                             WHERE consumption.credit_key = credit.credit_key
                         )
                   )
                ORDER BY credit.credit_id DESC
                LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            DirectProductionRecord(
                assignment_id=str(row["assignment_id"]),
                provider=self._stored_direct_production_provider(
                    str(row["provider"])
                ),
                effort_units=int(row["effort_units"]),
            )
            for row in reversed(rows)
        ]

    def workload_reservation_count(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM workload_assignment_reservations"
            ).fetchone()
        return int(row["count"])

    @staticmethod
    def _require_effect_migration(row: sqlite3.Row) -> None:
        if row["effect_migration_status"] != "complete":
            raise RuntimeError(
                "Task effect migration is incomplete; external work remains blocked"
            )

    @staticmethod
    def _record_json_without_effect_projection(record: TaskRecord) -> str:
        record.compact_history_projections()
        persisted = record.model_copy(deep=True)
        persisted.task_effect_ledger = []
        persisted.provider_interaction_ledger = []
        return persisted.model_dump_json()

    @staticmethod
    def _task_record_from_persisted_json(record_json: str) -> TaskRecord:
        payload = json.loads(record_json)
        if not isinstance(payload, dict):
            raise ValueError("Persisted task record must be an object")
        payload.setdefault(
            "intake_authentication",
            IntakeAuthentication.BEARER_DRY_RUN.value,
        )
        TaskStore._validate_and_normalize_persisted_execution_limits(payload)
        return TaskRecord.model_validate(payload)

    @staticmethod
    def _validate_and_normalize_persisted_execution_limits(
        payload: dict[str, object],
    ) -> None:
        """Validate current or translate one exact historical limit contract.

        Raw persisted authority must be complete before Pydantic sees it. Stored
        JSON remains untouched; no omitted field can acquire a model default.
        """

        plan = payload.get("plan")
        if plan is None:
            return
        if not isinstance(plan, dict):
            raise ValueError("Persisted task plan must be an object")
        limits = plan.get("execution_limits")
        if not isinstance(limits, dict):
            raise ValueError("Persisted execution limits must be a complete object")

        current_contract = LeanExecutionLimits().model_dump(mode="json")
        current_authority_fields = {
            "retry_limit_source",
            "additional_attempt_requires_classified_failure",
            "additional_attempt_requires_positive_information_gain",
            "additional_attempt_requires_changed_basis",
            "token_cost_timeout_and_cancellation_budgets_enforced",
        }
        legacy_authority = {
            "primary_attempt_limit": 1,
            "transient_recovery_attempt_limit": 1,
            "total_route_attempt_limit": 3,
            "third_attempt_requires_new_information_or_transport": True,
        }
        legacy_contract = {
            field: value
            for field, value in current_contract.items()
            if field not in current_authority_fields
        }
        legacy_contract.update(legacy_authority)

        if limits.keys() == current_contract.keys():
            selected_contract = current_contract
            is_legacy = False
        elif limits.keys() == legacy_contract.keys():
            selected_contract = legacy_contract
            is_legacy = True
        else:
            raise ValueError("Persisted execution limits have incomplete or mixed authority")
        for field, expected in selected_contract.items():
            value = limits[field]
            if type(value) is not type(expected) or value != expected:
                raise ValueError(
                    f"Persisted execution limit {field} is not authoritative"
                )

        assignments = plan.get("assignments")
        if not isinstance(assignments, list):
            raise ValueError("Persisted plan assignments must be a list")
        for assignment in assignments:
            if not isinstance(assignment, dict):
                raise ValueError("Persisted assignment must be an object")
            has_retry_limit = "retry_limit" in assignment
            if is_legacy and has_retry_limit:
                raise ValueError("Persisted legacy assignment mixes retry authority")
            if not is_legacy and not has_retry_limit:
                raise ValueError("Persisted current assignment omits retry authority")
            if has_retry_limit:
                retry_limit = assignment["retry_limit"]
                if type(retry_limit) is not int or retry_limit != 3:
                    raise ValueError("Persisted assignment retry limit is not authoritative")
            else:
                assignment["retry_limit"] = legacy_authority["total_route_attempt_limit"]

        if not is_legacy:
            return
        normalized = dict(limits)
        for field in legacy_authority:
            normalized.pop(field)
        normalized.update(
            {field: current_contract[field] for field in current_authority_fields}
        )
        plan["execution_limits"] = normalized

    def _record_from_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> TaskRecord:
        self._require_effect_migration(row)
        record = self._task_record_from_persisted_json(row["record_json"])
        record._store_version = int(row["record_version"])
        self._hydrate_effects(connection, record)
        self._hydrate_provider_interactions(connection, record)
        return record

    def _append_provider_interactions(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        events: list[ProviderInteractionEvent],
    ) -> None:
        existing = self._load_provider_interactions(connection, task_id)
        existing_digests = {item.event_digest for item in existing}
        new_events = [item for item in events if item.event_digest not in existing_digests]
        if len(existing) + len(new_events) > _MAX_PROVIDER_INTERACTION_EVENTS:
            raise ValueError("Immutable provider interaction ledger is full")
        validated = list(existing)
        for event in new_events:
            if event.task_id != task_id:
                raise ValueError("Provider interaction event task binding changed")
            validated = append_interaction_event(validated, event)
            connection.execute(
                """INSERT INTO provider_interaction_events(
                    task_id, interaction_id, transition_ordinal, state,
                    event_digest, event_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    event.interaction_id,
                    event.transition_ordinal,
                    event.state.value,
                    event.event_digest,
                    event.model_dump_json(),
                    event.recorded_at.isoformat(),
                ),
            )

    @staticmethod
    def _load_provider_interactions(
        connection: sqlite3.Connection, task_id: str
    ) -> list[ProviderInteractionEvent]:
        rows = connection.execute(
            """SELECT event_json FROM provider_interaction_events
            WHERE task_id = ? ORDER BY recorded_at, rowid""",
            (task_id,),
        ).fetchall()
        return [
            ProviderInteractionEvent.model_validate_json(row["event_json"])
            for row in rows
        ]

    def _hydrate_provider_interactions(
        self, connection: sqlite3.Connection, record: TaskRecord
    ) -> None:
        record.provider_interaction_ledger = self._load_provider_interactions(
            connection, record.task_id
        )

    def _append_effects(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        effects: list[TaskEffectEvidence],
        *,
        allow_privacy_pending: bool = False,
    ) -> None:
        existing_count = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM task_effect_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()["count"]
        )
        existing_digests = {
            row["event_digest"]
            for row in connection.execute(
                "SELECT event_digest FROM task_effect_events WHERE task_id = ?",
                (task_id,),
            ).fetchall()
        }
        new_effects: list[TaskEffectEvidence] = []
        seen = set(existing_digests)
        for effect in effects:
            if effect.effect_digest not in seen:
                new_effects.append(effect)
                seen.add(effect.effect_digest)
        if existing_count + len(new_effects) > _MAX_TASK_EFFECTS:
            raise ValueError("Immutable task effect ledger is full")
        if (
            not allow_privacy_pending
            and any(effect.kind in _INTENT_KINDS for effect in new_effects)
            and self._privacy_cleanup_pending_in(connection)
        ):
            self._defer_for_privacy_cleanup(connection, task_id, new_effects[0])
            raise PrivacyCleanupPending(
                "Private SQLite WAL cleanup is pending; external work remains blocked"
            )
        for effect in new_effects:
            if effect.kind in _INTENT_KINDS:
                superseded = connection.execute(
                    """SELECT 1 FROM privacy_cleanup_effect_resolutions
                    WHERE task_id = ? AND original_effect_key = ?""",
                    (task_id, effect.effect_key),
                ).fetchone()
                if superseded is not None:
                    raise StaleTaskWrite(
                        "Original external-effect intent was superseded by durable recovery"
                    )
                prior = connection.execute(
                    """SELECT event_digest FROM task_effect_events
                    WHERE task_id = ? AND effect_key = ? AND event_kind IN (
                        'provider_intent', 'director_intent',
                        'slack_intent', 'oracle_merge_intent'
                    )""",
                    (task_id, effect.effect_key),
                ).fetchone()
                if prior is not None and prior["event_digest"] != effect.effect_digest:
                    raise ValueError(
                        "Conflicting external-effect reservation already exists"
                    )
            connection.execute(
                """INSERT INTO task_effect_events(
                    task_id, event_digest, effect_key, event_kind,
                    event_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    effect.effect_digest,
                    effect.effect_key,
                    effect.kind,
                    effect.model_dump_json(),
                    effect.recorded_at.isoformat(),
                ),
            )

    @staticmethod
    def _load_effects(
        connection: sqlite3.Connection, task_id: str
    ) -> list[TaskEffectEvidence]:
        rows = connection.execute(
            """SELECT event_json FROM task_effect_events
            WHERE task_id = ? ORDER BY recorded_at, rowid""",
            (task_id,),
        ).fetchall()
        return [TaskEffectEvidence.model_validate_json(row["event_json"]) for row in rows]

    def _hydrate_effects(
        self, connection: sqlite3.Connection, record: TaskRecord
    ) -> None:
        effects = self._load_effects(connection, record.task_id)
        record.task_effect_ledger = effects
        observations: dict[str, set[str]] = {}
        intents: dict[str, TaskEffectEvidence] = {}
        legacy: dict[str, TaskEffectEvidence] = {}
        for effect in effects:
            if effect.kind in {"provider_intent", "director_intent"}:
                intents[effect.effect_key] = effect
            elif effect.kind in {"provider_observation", "director_observation"}:
                observations.setdefault(effect.effect_key, set()).add(effect.outcome)
                if effect.effect_key not in intents:
                    legacy[effect.effect_key] = effect
        def counts_as_consumed_effect(key: str) -> bool:
            outcomes = observations.get(key, set())
            return "failed_pre_submit" not in outcomes or bool(
                outcomes - {"failed_pre_submit"}
            )

        record.cumulative_estimated_cost_usd = round(
            sum(
                effect.estimated_cost_usd
                for key, effect in intents.items()
                if counts_as_consumed_effect(key)
            )
            + sum(effect.estimated_cost_usd for effect in legacy.values()),
            8,
        )
        record.cumulative_effect_hops = sum(
            effect.hop_units
            for key, effect in intents.items()
            if counts_as_consumed_effect(key)
        )

    def _migrate_effect_ledgers(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT * FROM tasks WHERE effect_migration_status != 'complete'"
        ).fetchall()
        for row in rows:
            try:
                record = self._task_record_from_persisted_json(row["record_json"])
                self._append_effects(
                    connection,
                    record.task_id,
                    record.task_effect_ledger,
                    allow_privacy_pending=True,
                )
                self._hydrate_effects(connection, record)
                self._append_provider_interactions(
                    connection, record.task_id, record.provider_interaction_ledger
                )
                self._hydrate_provider_interactions(connection, record)
                connection.execute(
                    """UPDATE tasks SET record_json = ?, effect_migration_status = 'complete',
                        record_version = record_version + 1
                    WHERE task_id = ?""",
                    (self._record_json_without_effect_projection(record), record.task_id),
                )
                if self._journal_mode(connection) == "wal":
                    self._mark_privacy_cleanup_pending(connection)
            except Exception:
                connection.execute(
                    "UPDATE tasks SET effect_migration_status = 'failed' WHERE task_id = ?",
                    (row["task_id"],),
                )

    def set_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        message: str | None = None,
        error: str | None = None,
    ) -> TaskRecord:
        record = self.get(task_id)
        if record is None:
            raise KeyError(task_id)
        record.status = status
        if message is not None:
            record.message = message
        if error is not None:
            record.error = error
        return self.save(record)

    def add_usage_check(self, record: UsageCheckRecord) -> UsageCheckRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO usage_checks(cycle_id, provider, status, record_json, checked_at) VALUES (?, ?, ?, ?, ?)",
                (
                    record.cycle_id,
                    record.provider,
                    record.status.value,
                    record.model_dump_json(),
                    record.checked_at.isoformat(),
                ),
            )
        return record

    def latest_usage_check(self, provider: str) -> UsageCheckRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM usage_checks WHERE provider = ? ORDER BY checked_at DESC, id DESC LIMIT 1",
                (provider,),
            ).fetchone()
        return UsageCheckRecord.model_validate_json(row["record_json"]) if row else None

    def latest_usage_checks(self) -> list[UsageCheckRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record_json
                FROM usage_checks AS current
                WHERE current.id = (
                    SELECT latest.id
                    FROM usage_checks AS latest
                    WHERE latest.provider = current.provider
                    ORDER BY latest.checked_at DESC, latest.id DESC
                    LIMIT 1
                )
                ORDER BY provider
                """
            ).fetchall()
        return [UsageCheckRecord.model_validate_json(row["record_json"]) for row in rows]

    def usage_checks_since(
        self, provider: str, since: datetime
    ) -> list[UsageCheckRecord]:
        """Return provider checks at or after an aware UTC boundary.

        Filtering parsed timestamps in Python avoids relying on lexical ordering
        across legacy records that used different but equivalent UTC offsets.
        """
        if since.tzinfo is None:
            raise ValueError("usage-check boundary must be timezone-aware")
        boundary = since.astimezone(timezone.utc)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT record_json FROM usage_checks WHERE provider = ? ORDER BY id",
                (provider,),
            ).fetchall()
        records = [
            UsageCheckRecord.model_validate_json(row["record_json"]) for row in rows
        ]
        return [
            record
            for record in records
            if record.checked_at.astimezone(timezone.utc) >= boundary
        ]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:26 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:08 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:26 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 07:10 UTC | END]]
# [[Claude Code | Claude Fable 5 | 2026-08-29 | 06:14 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-29 | 06:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 06:56 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:21 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:50 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 08:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 08:10 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
