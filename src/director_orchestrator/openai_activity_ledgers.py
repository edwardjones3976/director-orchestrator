# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:26 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:10 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:00 UTC | BEGIN]]
"""OpenAI dual activity ledgers.

Owner directive #207, "Required implementation" items 1 and 2.
Implements the two OpenAI activity ledgers required for the conditional profile
`dual_activity_ledgers_operational`.
The Director/control-plane ledger records planning and coordination.
The Direct-production ledger records bounded production assignments.
They must not be conflated, and production credit cannot be derived from Director activity.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

UNIT = "authenticated_openai_activity_event"
OBSERVATION_WINDOW_DAYS = 7
MINIMUM_EVENTS = 20
FLOOR_PERCENT = 75

DIRECTOR_KINDS = frozenset([
    "intake", "planning", "decomposition", "packet_preparation",
    "capability_based_routing", "team_communication", "synthesis",
    "acceptance_supervision", "completion_reporting", "owner_escalation"
])

PRODUCTION_KINDS = frozenset([
    "ordinary_authorship", "review", "ci", "merge_gates"
])

class DirectorUtilizationHold(Exception):
    """Raised when utilization is below the required floor."""
    pass

class LedgerError(Exception):
    """Ledger error carrying a code but no path."""
    pass

def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())

def _paths_are_isolated(path1: str, path2: str) -> bool:
    if path1 == path2:
        return False
    p1 = Path(path1).resolve()
    p2 = Path(path2).resolve()
    if p1 == p2:
        return False
    try:
        if p1.stat().st_ino == p2.stat().st_ino and p1.stat().st_dev == p2.stat().st_dev:
            return False
    except FileNotFoundError:
        pass
    return True

def _build_expected_schema(statements: tuple[str, ...]) -> frozenset[tuple[str, str, str]]:
    memory = sqlite3.connect(":memory:")
    try:
        for statement in statements:
            memory.execute(statement)
        return frozenset(
            (row[0], row[1], _normalize_sql(row[2]))
            for row in memory.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        )
    finally:
        memory.close()

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _hash_dict(d: dict[str, str]) -> str:
    items = sorted(d.items())
    s = "".join(f"{k}:{v}" for k, v in items)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

class _BaseLedger:
    _KIND = ""
    _TABLE_NAME = ""
    _STATEMENTS: tuple[str, ...] = ()
    
    def __init__(self, path: str):
        if not path:
            raise ValueError("path is required")
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._expected = _build_expected_schema(self._STATEMENTS)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                raise
            except sqlite3.DatabaseError as exc:
                raise LedgerError("not_a_valid_ledger") from exc
            with connection:
                self._verify_schema(connection)
                yield connection
        finally:
            connection.close()

    def _verify_schema(self, connection: sqlite3.Connection) -> None:
        """Verify the database schema matches this ledger's expected schema.
        
        If a metadata table is present, its ledger_kind is checked first to yield
        a specific 'wrong_ledger_kind' error before falling back to a general
        'schema_mismatch' error.
        """
        try:
            actual = frozenset(
                (row["type"], row["name"], _normalize_sql(row["sql"]))
                for row in connection.execute(
                    "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"
                )
            )
        except sqlite3.OperationalError:
            raise
        except sqlite3.DatabaseError as exc:
            raise LedgerError("not_a_valid_ledger") from exc
        
        if not actual:
            return

        has_metadata = any(row[0] == "table" and row[1] == "metadata" for row in actual)
        if has_metadata:
            metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
            if metadata.get("ledger_kind") != self._KIND:
                raise LedgerError("wrong_ledger_kind")

        if actual != self._expected:
            raise LedgerError("schema_mismatch")
        
        if not has_metadata:
            metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())

        if metadata.get("unit") != UNIT:
            raise LedgerError("wrong_unit")
        if metadata.get("observation_window_days") != str(OBSERVATION_WINDOW_DAYS):
            raise LedgerError("wrong_window")

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            for statement in self._STATEMENTS:
                connection.execute(statement)
            connection.execute("INSERT OR IGNORE INTO metadata (key, value) VALUES ('ledger_kind', ?)", (self._KIND,))
            connection.execute("INSERT OR IGNORE INTO metadata (key, value) VALUES ('unit', ?)", (UNIT,))
            connection.execute("INSERT OR IGNORE INTO metadata (key, value) VALUES ('observation_window_days', ?)", (str(OBSERVATION_WINDOW_DAYS),))

    def _record_event(self, idempotency_key: str, kind: str, task_id: str, purpose_digest: str, assignment_id: Optional[str]) -> None:
        exc_to_raise = None
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    f"INSERT INTO {self._TABLE_NAME} (idempotency_key, kind, started_at, task_id, assignment_id, purpose_digest) VALUES (?, ?, ?, ?, ?, ?)",
                    (idempotency_key, kind, _now(), task_id, assignment_id, purpose_digest)
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed" in str(exc):
                    connection.execute("INSERT INTO duplicates (idempotency_key, attempted_at) VALUES (?, ?)", (idempotency_key, _now()))
                    exc_to_raise = LedgerError("duplicate_idempotency_key")
                    exc_to_raise.__cause__ = exc
                else:
                    exc_to_raise = LedgerError("integrity_error")
                    exc_to_raise.__cause__ = exc
        if exc_to_raise:
            raise exc_to_raise

    def get_metadata(self) -> dict[str, str]:
        with self._lock, self._connect() as connection:
            return dict(connection.execute("SELECT key, value FROM metadata").fetchall())


class DirectorActivityLedger(_BaseLedger):
    """Ledger for Director/control-plane activity."""
    _KIND = "openai_director_activity"
    _TABLE_NAME = "openai_director_activity_events"
    _STATEMENTS = (
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS duplicates (idempotency_key TEXT, attempted_at TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS openai_director_activity_events (
            idempotency_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            started_at TEXT NOT NULL,
            task_id TEXT NOT NULL,
            assignment_id TEXT,
            purpose_digest TEXT NOT NULL,
            production_credit INTEGER NOT NULL DEFAULT 0 CHECK (production_credit = 0)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_dir_started ON openai_director_activity_events(started_at)",
        "CREATE TRIGGER IF NOT EXISTS openai_director_activity_events_no_update BEFORE UPDATE ON openai_director_activity_events BEGIN SELECT RAISE(ABORT, 'openai_director_activity_events is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS openai_director_activity_events_no_delete BEFORE DELETE ON openai_director_activity_events BEGIN SELECT RAISE(ABORT, 'openai_director_activity_events is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS duplicates_no_update BEFORE UPDATE ON duplicates BEGIN SELECT RAISE(ABORT, 'duplicates is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS duplicates_no_delete BEFORE DELETE ON duplicates BEGIN SELECT RAISE(ABORT, 'duplicates is append-only'); END",
    )

    def record_activity(self, idempotency_key: str, kind: str, task_id: str, purpose_digest: str, assignment_id: Optional[str] = None) -> None:
        if not purpose_digest.strip():
            raise LedgerError("purpose_digest_required")
        actual_kind = kind if kind in DIRECTOR_KINDS else "unknown"
        self._record_event(idempotency_key, actual_kind, task_id, purpose_digest, assignment_id)


class DirectProductionLedger(_BaseLedger):
    """Ledger for direct-production activity."""
    _KIND = "openai_direct_production"
    _TABLE_NAME = "openai_direct_production_events"
    _STATEMENTS = (
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS duplicates (idempotency_key TEXT, attempted_at TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS openai_direct_production_events (
            idempotency_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            started_at TEXT NOT NULL,
            task_id TEXT NOT NULL,
            assignment_id TEXT,
            purpose_digest TEXT NOT NULL,
            director_activity INTEGER NOT NULL DEFAULT 0 CHECK (director_activity = 0)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_prod_started ON openai_direct_production_events(started_at)",
        "CREATE TRIGGER IF NOT EXISTS openai_direct_production_events_no_update BEFORE UPDATE ON openai_direct_production_events BEGIN SELECT RAISE(ABORT, 'openai_direct_production_events is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS openai_direct_production_events_no_delete BEFORE DELETE ON openai_direct_production_events BEGIN SELECT RAISE(ABORT, 'openai_direct_production_events is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS duplicates_no_update BEFORE UPDATE ON duplicates BEGIN SELECT RAISE(ABORT, 'duplicates is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS duplicates_no_delete BEFORE DELETE ON duplicates BEGIN SELECT RAISE(ABORT, 'duplicates is append-only'); END",
    )

    def record_production(self, idempotency_key: str, kind: str, task_id: str, purpose_digest: str, assignment_id: Optional[str] = None) -> None:
        if not purpose_digest.strip():
            raise LedgerError("purpose_digest_required")
        if kind not in PRODUCTION_KINDS:
            raise LedgerError("invalid_production_kind")
        self._record_event(idempotency_key, kind, task_id, purpose_digest, assignment_id)


class OpenAIActivityLedgers:
    """Manages the two isolated OpenAI ledgers and calculates utilization."""
    
    def __init__(self, director_path: str, production_path: str):
        if not _paths_are_isolated(director_path, production_path):
            raise LedgerError("director and production ledgers must not be the same file")
        self.director = DirectorActivityLedger(director_path)
        self.production = DirectProductionLedger(production_path)

    def utilization(self, moment: Optional[datetime] = None) -> dict[str, Any]:
        """Calculate utilization over a rolling 7-day window.
        
        The window looks back from the given moment (or current time).
        A minimum of 20 valid events must be recorded within that window
        for history to be considered sufficient.
        """
        moment = moment or datetime.now(timezone.utc)
        window_start = moment - timedelta(days=OBSERVATION_WINDOW_DAYS)
        window_start_str = window_start.isoformat(timespec="seconds")
        
        with self.director._lock, self.director._connect() as d_conn:
            d_events = d_conn.execute("SELECT kind FROM openai_director_activity_events WHERE started_at >= ?", (window_start_str,)).fetchall()
            d_dups = d_conn.execute("SELECT COUNT(*) FROM duplicates WHERE attempted_at >= ?", (window_start_str,)).fetchone()[0]
        
        with self.production._lock, self.production._connect() as p_conn:
            p_events = p_conn.execute("SELECT kind FROM openai_direct_production_events WHERE started_at >= ?", (window_start_str,)).fetchall()
            p_dups = p_conn.execute("SELECT COUNT(*) FROM duplicates WHERE attempted_at >= ?", (window_start_str,)).fetchone()[0]
                
        duplicate_attempts = d_dups + p_dups
        director_events = sum(1 for row in d_events if row[0] in DIRECTOR_KINDS)
        unknown_events = sum(1 for row in d_events if row[0] == "unknown")
        production_events = len(p_events)
        
        total_valid = director_events + production_events
        
        if total_valid < MINIMUM_EVENTS:
            verdict = "insufficient_history"
        else:
            ratio = (director_events / total_valid) * 100
            if ratio >= FLOOR_PERCENT:
                verdict = "meets_floor"
            else:
                verdict = "below_floor"
                
        ratio_val = (director_events / total_valid) if total_valid > 0 else None
        
        d_meta = self.director.get_metadata()
        p_meta = self.production.get_metadata()
        
        return {
            "director_events": director_events,
            "production_events": production_events,
            "unknown_events": unknown_events,
            "duplicate_attempts": duplicate_attempts,
            "floor_percent": FLOOR_PERCENT,
            "ratio": ratio_val,
            "verdict": verdict,
            "window_bounds": [window_start_str, moment.isoformat(timespec="seconds")],
            "unit": UNIT,
            "ledger_kinds": [d_meta["ledger_kind"], p_meta["ledger_kind"]],
            "director_metadata_digest": _hash_dict(d_meta),
            "production_metadata_digest": _hash_dict(p_meta),
        }

    def require_floor(self, moment: Optional[datetime] = None) -> None:
        stats = self.utilization(moment=moment)
        if stats["verdict"] == "below_floor":
            raise DirectorUtilizationHold("OpenAI Director utilization is below the required floor")
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:00 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:10 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:26 UTC | END]]
