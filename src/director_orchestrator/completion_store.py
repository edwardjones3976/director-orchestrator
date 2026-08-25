# [[Codex | GPT-5.6 | 2026-08-24 | 23:44 UTC | BEGIN]]
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from .completion_gates import (
    AuthenticatedGateContext,
    CompletionGateError,
    RunnerRegistry,
    VerifiedGateLedger,
    VerifiedGateResult,
    aggregate_completion_eligibility,
    canonical_json_bytes,
    validate_gate_ledger,
    validate_gate_result,
)


class CompletionGateStore:
    """Append-only, Director-controlled Completion Gate evidence store."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect(immediate=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS completion_gate_ledgers (
                    ledger_digest TEXT PRIMARY KEY,
                    ledger_series_id TEXT NOT NULL,
                    ledger_id TEXT NOT NULL UNIQUE,
                    ledger_revision INTEGER NOT NULL,
                    ledger_state TEXT NOT NULL,
                    expected_head_sha TEXT,
                    document_json TEXT NOT NULL,
                    UNIQUE(ledger_series_id, ledger_revision)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS completion_gate_results (
                    result_digest TEXT PRIMARY KEY,
                    result_id TEXT NOT NULL UNIQUE,
                    attempt_id TEXT NOT NULL UNIQUE,
                    ledger_digest TEXT NOT NULL,
                    gate_id TEXT NOT NULL,
                    executed_sha TEXT NOT NULL,
                    status TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    FOREIGN KEY(ledger_digest) REFERENCES completion_gate_ledgers(ledger_digest)
                )
                """
            )
            for table in ("completion_gate_ledgers", "completion_gate_results"):
                connection.execute(
                    f"""CREATE TRIGGER IF NOT EXISTS {table}_no_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
                )
                connection.execute(
                    f"""CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
                )

    @staticmethod
    def _encode(document: Mapping[str, object]) -> str:
        return canonical_json_bytes(document).decode("utf-8")

    @staticmethod
    def _decode(raw: str) -> dict:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise CompletionGateError("stored completion evidence is not an object")
        return value

    def _load_latest_ledger(
        self, connection: sqlite3.Connection, series_id: str
    ) -> dict | None:
        row = connection.execute(
            """SELECT document_json FROM completion_gate_ledgers
            WHERE ledger_series_id = ? ORDER BY ledger_revision DESC LIMIT 1""",
            (series_id,),
        ).fetchone()
        return None if row is None else self._decode(row["document_json"])

    def append_ledger(
        self,
        document: Mapping[str, object],
        *,
        context: AuthenticatedGateContext,
        runner_registry: RunnerRegistry | None = None,
    ) -> VerifiedGateLedger:
        with self._lock, self._connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT document_json FROM completion_gate_ledgers WHERE ledger_id = ?",
                (document.get("ledger_id"),),
            ).fetchone()
            if existing is not None:
                stored = self._decode(existing["document_json"])
                if stored != dict(document):
                    raise CompletionGateError("ledger identity already records different evidence")
                return validate_gate_ledger(
                    stored, context=context,
                    predecessor=self._predecessor(connection, stored),
                    runner_registry=runner_registry,
                )
            predecessor = self._load_latest_ledger(connection, str(document.get("ledger_series_id")))
            verified = validate_gate_ledger(
                document, context=context, predecessor=predecessor,
                runner_registry=runner_registry,
            )
            connection.execute(
                """INSERT INTO completion_gate_ledgers(
                    ledger_digest, ledger_series_id, ledger_id, ledger_revision,
                    ledger_state, expected_head_sha, document_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    verified.digest, verified.document["ledger_series_id"],
                    verified.document["ledger_id"], verified.document["ledger_revision"],
                    verified.document["ledger_state"], verified.document["expected_head_sha"],
                    self._encode(verified.document),
                ),
            )
            return verified

    def _predecessor(self, connection: sqlite3.Connection, ledger: Mapping[str, object]) -> dict | None:
        revision = int(ledger["ledger_revision"])
        if revision == 1:
            return None
        row = connection.execute(
            """SELECT document_json FROM completion_gate_ledgers
            WHERE ledger_series_id = ? AND ledger_revision = ?""",
            (ledger["ledger_series_id"], revision - 1),
        ).fetchone()
        return None if row is None else self._decode(row["document_json"])

    def load_ledger(self, ledger_digest: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_json FROM completion_gate_ledgers WHERE ledger_digest = ?",
                (ledger_digest,),
            ).fetchone()
        if row is None:
            raise KeyError(ledger_digest)
        return self._decode(row["document_json"])

    def append_result(
        self,
        document: Mapping[str, object],
        *,
        verified_ledger: VerifiedGateLedger,
        context: AuthenticatedGateContext,
    ) -> VerifiedGateResult:
        with self._lock, self._connect(immediate=True) as connection:
            stored_ledger = connection.execute(
                "SELECT document_json FROM completion_gate_ledgers WHERE ledger_digest = ?",
                (verified_ledger.digest,),
            ).fetchone()
            if stored_ledger is None or self._decode(stored_ledger["document_json"]) != verified_ledger.document:
                raise CompletionGateError("result ledger is not authoritative in this store")
            existing = connection.execute(
                "SELECT document_json FROM completion_gate_results WHERE result_id = ?",
                (document.get("result_id"),),
            ).fetchone()
            dependencies = self._all_trusted_results(
                connection, verified_ledger, context
            )
            if existing is not None:
                stored = self._decode(existing["document_json"])
                if stored != dict(document):
                    raise CompletionGateError("result identity already records different evidence")
                return validate_gate_result(
                    verified_ledger, stored, context=context, dependency_results=dependencies,
                )
            verified = validate_gate_result(
                verified_ledger, document, context=context, dependency_results=dependencies,
            )
            connection.execute(
                """INSERT INTO completion_gate_results(
                    result_digest, result_id, attempt_id, ledger_digest, gate_id,
                    executed_sha, status, document_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    verified.digest, verified.document["result_id"], verified.document["attempt_id"],
                    verified_ledger.digest, verified.document["gate_id"],
                    verified.document["executed_sha"], verified.document["status"],
                    self._encode(verified.document),
                ),
            )
            return verified

    def _all_trusted_results(
        self,
        connection: sqlite3.Connection,
        ledger: VerifiedGateLedger,
        context: AuthenticatedGateContext,
    ) -> dict[str, VerifiedGateResult]:
        trusted: dict[str, VerifiedGateResult] = {}
        rows = connection.execute(
            """SELECT document_json FROM completion_gate_results
            WHERE ledger_digest = ? AND status = 'PASS' ORDER BY rowid""",
            (ledger.digest,),
        ).fetchall()
        pending = [self._decode(row["document_json"]) for row in rows]
        for _ in range(len(pending) + 1):
            changed = False
            for candidate in list(pending):
                try:
                    verified = validate_gate_result(
                        ledger, candidate, context=context, dependency_results=trusted,
                    )
                except CompletionGateError:
                    continue
                trusted[verified.document["gate_id"]] = verified
                pending.remove(candidate)
                changed = True
            if not changed:
                break
        return trusted

    def completion_status(
        self,
        verified_ledger: VerifiedGateLedger,
        *,
        context: AuthenticatedGateContext,
    ) -> dict:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_json FROM completion_gate_results WHERE ledger_digest = ?",
                (verified_ledger.digest,),
            ).fetchall()
        verified: list[VerifiedGateResult] = []
        by_gate: dict[str, VerifiedGateResult] = {}
        pending = [self._decode(row["document_json"]) for row in rows]
        for _ in range(len(pending) + 1):
            changed = False
            for candidate in list(pending):
                try:
                    item = validate_gate_result(
                        verified_ledger, candidate, context=context, dependency_results=by_gate,
                    )
                except CompletionGateError:
                    continue
                verified.append(item)
                if item.document["status"] == "PASS":
                    by_gate[item.document["gate_id"]] = item
                pending.remove(candidate)
                changed = True
            if not changed:
                break
        return aggregate_completion_eligibility(verified_ledger, verified)
# [[Codex | GPT-5.6 | 2026-08-24 | 23:44 UTC | END]]
