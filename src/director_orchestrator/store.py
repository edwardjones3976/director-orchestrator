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

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
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
    append_interaction_event,
    canonical_content_digest,
    canonical_identifier_digest,
    project_interaction,
)
from .models import (
    IntakeAuthentication,
    TaskEffectEvidence,
    TaskRecord,
    TaskStatus,
    UsageCheckRecord,
)
from .policy import trusted_work_kind
from .workload_policy import (
    DEFAULT_POLICY,
    AssignmentRequest,
    DecisionOutcome,
    DecisionResult,
    DirectProductionRecord,
    EnforcementMode,
    Provider,
    WorkKind,
    WorkSize,
    WorkloadRecord,
    direct_production_provider,
    evaluate_assignment,
    is_direct_production_kind,
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
        for outcome in record.assignment_outcomes:
            assignment = assignments.get(outcome.assignment_sequence)
            if assignment is None or assignment.agent != outcome.provider:
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
            if not is_direct_production_kind(trusted_work_kind(assignment)):
                # Accepted research, review, debate, and coordination outcomes
                # are real provider work, but they are not production credits.
                continue
            assignment_id = (
                f"{record.task_id}:{record.plan_generation}:"
                f"{assignment.sequence}"
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
            if not is_direct_production_kind(authoritative_kind):
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
            review_binding_valid = (
                review_event is not None
                and review_projection.task_id == record.task_id
                and review_projection.provider is InteractionProvider.OPENAI
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
        identity fails closed.
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
        available = frozenset(available_primary_providers)
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

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._workload_row(connection, reservation_key)
            if existing is not None:
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
                if identity != requested_identity:
                    raise WorkloadReservationConflict(
                        "workload reservation key was reused with different assignment identity"
                    )
                persisted = self._workload_reservation_from_row(
                    existing, created=False
                )
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

            effective_mode = mode
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
                    EnforcementMode(str(unit_authority["configured_mode"]))
                    is not mode
                    or int(unit_authority["shadow_assignment_limit"])
                    != shadow_assignment_limit
                ):
                    raise WorkloadReservationConflict(
                        "workload assignment unit authority changed between phases"
                    )
                effective_mode = EnforcementMode(str(unit_authority["mode"]))
            elif mode is EnforcementMode.SHADOW:
                effective_mode = self._effective_workload_mode(
                    connection,
                    configured_mode=mode,
                    shadow_assignment_limit=shadow_assignment_limit,
                )

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
            direct_provider = direct_production_provider(provider)
            if (
                direct_provider is not None
                and is_direct_production_kind(kind)
            ):
                # Pending and invoked-but-uncredited implementations are handled
                # symmetrically by _direct_rollover_violations below.  The base
                # history therefore remains the canonical append-only credit
                # window instead of mixing unrelated table clocks and ordinals.
                direct_rows = connection.execute(
                    """SELECT assignment_id, provider, effort_units
                    FROM direct_production_credits
                    ORDER BY credit_id DESC
                    LIMIT ?""",
                    (DEFAULT_POLICY.rolling_window_size - 1,),
                ).fetchall()
                for row in reversed(direct_rows):
                    mapped_provider = direct_production_provider(
                        Provider(str(row["provider"]))
                    )
                    if mapped_provider is None:
                        raise WorkloadReservationConflict(
                            "ordinary OpenAI cannot enter direct-production history"
                        )
                    direct_history.append(
                        DirectProductionRecord(
                            assignment_id=str(row["assignment_id"]),
                            provider=mapped_provider,
                            effort_units=int(row["effort_units"]),
                        )
                    )
            decision = evaluate_assignment(
                request,
                history=history,
                direct_production_history=direct_history,
                direct_production_assignment_id=assignment_id,
                available_primary_providers=available,
                policy=DEFAULT_POLICY,
                mode=effective_mode,
            )
            # [[Claude Code | Claude Fable 5 | 2026-08-29 | 05:52 UTC | BEGIN]]
            # Unresolved-rollover projection.  Only pending rows that can still
            # enter the rolling window are relevant: a pending row older than the
            # nineteen most recent invoked rows can never displace them, so it
            # must not act as a lifetime global block.  Each relevant pending row
            # is projected both ways (invoked / not invoked); a sibling is safe
            # only when every projection agrees it clears the caps.
            extra_violations: list[str] = []
            extra_violations.extend(
                self._overall_rollover_violations(
                    connection,
                    request=request,
                    available=available,
                    mode=effective_mode,
                )
            )
            if direct_provider is not None and is_direct_production_kind(kind):
                extra_violations.extend(
                    self._direct_rollover_violations(
                        connection,
                        request=request,
                        assignment_id=assignment_id,
                        available=available,
                        mode=effective_mode,
                    )
                )
            # [[Claude Code | Claude Fable 5 | 2026-08-29 | 05:52 UTC | END]]
            decision = self._decision_with_violations(
                decision, extra_violations
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
                    reservation_key,
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
                    (reservation_key, recorded_at.isoformat()),
                )
            persisted = self._workload_row(connection, reservation_key)
            if persisted is None:  # Defensive: insertion and read share a transaction.
                raise RuntimeError("workload reservation disappeared during commit")
            return self._workload_reservation_from_row(persisted, created=True)

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

    @classmethod
    def _overall_rollover_violations(
        cls,
        connection: sqlite3.Connection,
        *,
        request: AssignmentRequest,
        available: frozenset[Provider],
        mode: EnforcementMode,
    ) -> list[str]:
        """Project every in-window pending row as invoked and as not invoked."""

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
                    policy=DEFAULT_POLICY,
                    mode=mode,
                )
            )
        return cls._projection_disagreement(
            decisions, "workload_rollover_pending_unresolved"
        )

    @classmethod
    def _direct_rollover_violations(
        cls,
        connection: sqlite3.Connection,
        *,
        request: AssignmentRequest,
        assignment_id: str,
        available: frozenset[Provider],
        mode: EnforcementMode,
    ) -> list[str]:
        """Project every uncredited in-flight implementation unit both ways."""

        window = DEFAULT_POLICY.rolling_window_size
        unresolved_rows = connection.execute(
            """SELECT reservation.assignment_id, reservation.provider,
                      reservation.effort_units
            FROM workload_assignment_reservations AS reservation
            LEFT JOIN workload_assignment_events AS event
              ON event.reservation_key = reservation.reservation_key
            WHERE reservation.work_kind = 'implementation'
              AND reservation.provider <> 'openai'
              AND reservation.assignment_id <> ?
              AND NOT EXISTS (
                  SELECT 1 FROM direct_production_credits AS credit
                  WHERE credit.assignment_id = reservation.assignment_id
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
            mapped = direct_production_provider(Provider(str(row["provider"])))
            if mapped is None:
                raise WorkloadReservationConflict(
                    "ordinary OpenAI cannot enter direct-production history"
                )
            unresolved[str(row["assignment_id"])] = DirectProductionRecord(
                assignment_id=str(row["assignment_id"]),
                provider=mapped,
                effort_units=int(row["effort_units"]),
            )
        if not unresolved:
            return []
        units = list(unresolved.values())
        if len(units) > cls._ROLLOVER_PROJECTION_LIMIT:
            credit_total = connection.execute(
                "SELECT COUNT(*) AS count FROM direct_production_credits"
            ).fetchone()
            if int(credit_total["count"]) + len(units) >= window - 1:
                return ["direct_production_rollover_pending_unresolved"]
            return []
        credit_rows = connection.execute(
            """SELECT assignment_id, provider, effort_units
            FROM direct_production_credits
            ORDER BY credit_id DESC
            LIMIT ?""",
            (window - 1,),
        ).fetchall()
        credits: list[DirectProductionRecord] = []
        for row in reversed(credit_rows):
            mapped = direct_production_provider(Provider(str(row["provider"])))
            if mapped is None:
                raise WorkloadReservationConflict(
                    "ordinary OpenAI cannot enter direct-production history"
                )
            credits.append(
                DirectProductionRecord(
                    assignment_id=str(row["assignment_id"]),
                    provider=mapped,
                    effort_units=int(row["effort_units"]),
                )
            )
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
                    policy=DEFAULT_POLICY,
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
                """SELECT assignment_id, provider, effort_units
                FROM direct_production_credits
                ORDER BY credit_id DESC
                LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            DirectProductionRecord(
                assignment_id=str(row["assignment_id"]),
                provider=direct_production_provider(
                    Provider(str(row["provider"]))
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
        return TaskRecord.model_validate(payload)

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
