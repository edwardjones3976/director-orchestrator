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
from pathlib import Path
from typing import Callable, Iterator

from .interactions import (
    ProviderInteractionEvent,
    append_interaction_event,
)
from .models import (
    IntakeAuthentication,
    TaskEffectEvidence,
    TaskRecord,
    TaskStatus,
    UsageCheckRecord,
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
            self._hydrate_effects(connection, record)
            self._hydrate_provider_interactions(connection, record)
            record.updated_at = datetime.now(timezone.utc)
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
        record.cumulative_estimated_cost_usd = round(
            sum(
                effect.estimated_cost_usd
                for key, effect in intents.items()
                if "failed_pre_submit" not in observations.get(key, set())
            )
            + sum(effect.estimated_cost_usd for effect in legacy.values()),
            8,
        )
        record.cumulative_effect_hops = sum(
            effect.hop_units
            for key, effect in intents.items()
            if "failed_pre_submit" not in observations.get(key, set())
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
