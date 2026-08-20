from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import TaskRecord, TaskStatus, UsageCheckRecord


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
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
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

    def create(self, record: TaskRecord) -> tuple[TaskRecord, bool]:
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT record_json FROM tasks WHERE source_event_id = ?",
                (record.intake.source_event_id,),
            ).fetchone()
            if existing:
                return TaskRecord.model_validate_json(existing["record_json"]), False
            connection.execute(
                "INSERT INTO tasks(task_id, source_event_id, status, record_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.task_id,
                    record.intake.source_event_id,
                    record.status.value,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            return record, True

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            return TaskRecord.model_validate_json(row["record_json"]) if row else None

    def list_recoverable(self) -> list[TaskRecord]:
        recoverable = {
            TaskStatus.QUEUED.value,
            TaskStatus.PLANNING.value,
            TaskStatus.RUNNING.value,
            TaskStatus.REVIEWING.value,
        }
        placeholders = ",".join("?" for _ in recoverable)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT record_json FROM tasks WHERE status IN ({placeholders})",
                tuple(sorted(recoverable)),
            ).fetchall()
        return [TaskRecord.model_validate_json(row["record_json"]) for row in rows]

    def save(self, record: TaskRecord) -> TaskRecord:
        record.updated_at = datetime.now(timezone.utc)
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, record_json = ?, updated_at = ? WHERE task_id = ?",
                (
                    record.status.value,
                    record.model_dump_json(),
                    record.updated_at.isoformat(),
                    record.task_id,
                ),
            )
        return record

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
