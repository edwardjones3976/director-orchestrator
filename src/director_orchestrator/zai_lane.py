# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | BEGIN]]
"""Z.ai trial-only contact lane: typed identity, separate trial ledger, bounded dispatch.

Owner directive #207 stages Z.ai (GLM Coding Plan) as a trial-only, fail-closed
contact until the owner's separate activation decision on or after 2026-09-29.
This module wires the existing `zai_trial` transport into the program without
making Z.ai a production lane. The identifier `zai` is deliberately absent from
`ProviderName`, the workload `Provider` enums, `LANES`, `PRIMARY_PROVIDER_LANES`,
`KNOWN_LANES` and the Postgres lane constraint; that absence, plus the CHECK
constraints below, is what makes "no production credit, no review authority, no
merge authority" structural rather than a runtime promise.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .langgraph_runtime import ProviderAdapterContext
from .native_acceptance_issuer import IssuerError
from .packet_contracts import document_digest
from .zai_trial import ENDPOINT, TRIAL_END, build_zai_trial_adapter

LEDGER_VERSION = "zai-trial-ledger/v1"
# Welcome packet rule: after three recorded failures the route is unavailable.
MAX_RECORDED_FAILURES = 3
ATTEMPT_STATUSES = ("SUCCEEDED", "FAILED", "AMBIGUOUS")
CORRECTNESS = ("correct", "incorrect", "unverified")
PRIVACY_FIT = ("fit", "unfit", "unassessed")
USEFULNESS = ("accepted", "partially_useful", "unusable", "unassessed")
_SELF_NAMES = frozenset({"zai", "z.ai", "glm"})


@dataclass(frozen=True)
class ZaiContactLane:
    """Typed contact-lane identity. The product (Claude Code CLI) is not the family (Z.ai)."""

    lane_id: str = "zai"
    provider_family: str = "zai"
    product: str = "claude-code-cli"
    backend_identity: str = "glm"
    endpoint: str = ENDPOINT
    trial_end: str = TRIAL_END.isoformat()
    production_lane: bool = False
    production_credit: bool = False
    review_authority: bool = False
    merge_authority: bool = False
    activation: str = "owner_decision_required_on_or_after_2026-09-29"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


ZAI_CONTACT_LANE = ZaiContactLane()


class ZaiTrialLedgerError(RuntimeError):
    """A trial-ledger rule refused the operation; nothing was recorded."""


class ZaiTrialLaneUnavailable(RuntimeError):
    """The lane is not ready or the trial window is closed; nothing was dispatched."""


@dataclass(frozen=True)
class ZaiTrialDispatch:
    attempt_id: str
    latency_ms: int
    result: Mapping[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def attempt_id_for(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


class ZaiTrialLedger:
    """Append-only SQLite record of bounded Lite assignments and their evaluation.

    Separate from every production ledger by construction: its own file, rows
    that carry `production_credit = 0` under CHECK constraints, and no writes to
    TaskStore, the Postgres admission ledger or the engineering workload ledger.
    Rows are immutable; an outcome or evaluation is a new row, never an UPDATE.
    """

    def __init__(self, path: str):
        if not path:
            raise ValueError("a trial ledger path is required")
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS zai_trial_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO zai_trial_metadata (key, value) VALUES ('version', ?)",
                (LEDGER_VERSION,),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS zai_trial_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    assignment_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    work_packet_digest TEXT NOT NULL,
                    model TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    recovery INTEGER NOT NULL CHECK (recovery IN (0, 1)),
                    recovery_basis TEXT,
                    started_at TEXT NOT NULL,
                    production_credit INTEGER NOT NULL DEFAULT 0 CHECK (production_credit = 0),
                    review_authority INTEGER NOT NULL DEFAULT 0 CHECK (review_authority = 0),
                    merge_authority INTEGER NOT NULL DEFAULT 0 CHECK (merge_authority = 0),
                    CHECK ((recovery = 1) = (recovery_basis IS NOT NULL))
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS zai_trial_attempts_assignment"
                " ON zai_trial_attempts(assignment_id, started_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS zai_trial_outcomes (
                    attempt_id TEXT PRIMARY KEY REFERENCES zai_trial_attempts(attempt_id),
                    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED', 'AMBIGUOUS')),
                    finished_at TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
                    result_packet_digest TEXT,
                    cost_usd REAL CHECK (cost_usd IS NULL OR cost_usd > 0),
                    error_type TEXT,
                    CHECK (status <> 'SUCCEEDED' OR result_packet_digest IS NOT NULL)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS zai_trial_evaluations (
                    attempt_id TEXT PRIMARY KEY REFERENCES zai_trial_outcomes(attempt_id),
                    evaluated_at TEXT NOT NULL,
                    evaluator TEXT NOT NULL CHECK (
                        evaluator <> '' AND lower(evaluator) NOT IN ('zai', 'z.ai', 'glm')
                    ),
                    correctness TEXT NOT NULL CHECK (correctness IN ('correct', 'incorrect', 'unverified')),
                    first_pass INTEGER NOT NULL CHECK (first_pass IN (0, 1)),
                    correction_count INTEGER NOT NULL CHECK (correction_count >= 0),
                    privacy_fit TEXT NOT NULL CHECK (privacy_fit IN ('fit', 'unfit', 'unassessed')),
                    usefulness TEXT NOT NULL CHECK (
                        usefulness IN ('accepted', 'partially_useful', 'unusable', 'unassessed')
                    ),
                    notes_digest TEXT,
                    CHECK (first_pass = 0 OR correction_count = 0)
                )
                """
            )
            for table in ("zai_trial_attempts", "zai_trial_outcomes", "zai_trial_evaluations"):
                for verb in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()}
                        BEFORE {verb} ON {table}
                        BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
                    )

    @staticmethod
    def _recorded_failures(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COUNT(*) FROM zai_trial_outcomes WHERE status IN ('FAILED', 'AMBIGUOUS')"
        ).fetchone()
        return int(row[0])

    def route_available(self) -> bool:
        with self._lock, self._connect() as connection:
            return self._recorded_failures(connection) < MAX_RECORDED_FAILURES

    def reserve(
        self,
        *,
        assignment_id: str,
        task_id: str,
        work_packet_digest: str,
        idempotency_key: str,
        model: str,
        purpose: str,
        recovery_basis: str | None = None,
    ) -> str:
        """Reserve one attempt before any provider I/O.

        One primary attempt per assignment. One recovery only after a recorded
        FAILED outcome and only with an explicit basis; a SUCCEEDED attempt is
        never repeated and an AMBIGUOUS one is never redispatched.
        """

        if not purpose.strip():
            raise ZaiTrialLedgerError("zai_attempt_requires_purpose")
        attempt_id = attempt_id_for(idempotency_key)
        with self._lock, self._connect() as connection:
            if self._recorded_failures(connection) >= MAX_RECORDED_FAILURES:
                raise ZaiTrialLedgerError("zai_route_unavailable_after_three_failures")
            if connection.execute(
                "SELECT 1 FROM zai_trial_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone():
                raise ZaiTrialLedgerError("zai_attempt_already_reserved_for_idempotency_key")
            prior = connection.execute(
                """SELECT a.recovery, o.status FROM zai_trial_attempts a
                LEFT JOIN zai_trial_outcomes o ON o.attempt_id = a.attempt_id
                WHERE a.assignment_id = ? ORDER BY a.started_at, a.attempt_id""",
                (assignment_id,),
            ).fetchall()
            if recovery_basis is None:
                if prior:
                    raise ZaiTrialLedgerError("zai_primary_attempt_already_recorded")
            else:
                if not recovery_basis.strip():
                    raise ZaiTrialLedgerError("zai_recovery_requires_explicit_basis")
                if not prior:
                    raise ZaiTrialLedgerError("zai_recovery_requires_a_prior_attempt")
                if any(row["recovery"] for row in prior):
                    raise ZaiTrialLedgerError("zai_recovery_already_used")
                statuses = [row["status"] for row in prior]
                if None in statuses:
                    raise ZaiTrialLedgerError("zai_prior_attempt_unresolved_no_redispatch")
                if "SUCCEEDED" in statuses:
                    raise ZaiTrialLedgerError("zai_successful_attempt_must_not_be_repeated")
                if "AMBIGUOUS" in statuses:
                    raise ZaiTrialLedgerError("zai_ambiguous_attempt_never_redispatched")
            connection.execute(
                """INSERT INTO zai_trial_attempts (attempt_id, assignment_id, task_id,
                work_packet_digest, model, purpose, recovery, recovery_basis, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt_id,
                    assignment_id,
                    task_id,
                    work_packet_digest,
                    model,
                    purpose.strip(),
                    int(recovery_basis is not None),
                    recovery_basis.strip() if recovery_basis is not None else None,
                    _now(),
                ),
            )
        return attempt_id

    def record_outcome(
        self,
        attempt_id: str,
        *,
        status: str,
        latency_ms: int,
        result_packet_digest: str | None = None,
        cost_usd: float | None = None,
        error_type: str | None = None,
    ) -> None:
        if status not in ATTEMPT_STATUSES:
            raise ZaiTrialLedgerError("zai_outcome_status_unknown")
        if cost_usd is not None and not cost_usd > 0:
            raise ZaiTrialLedgerError("zai_cost_unknown_is_null_never_zero")
        if status == "SUCCEEDED" and not result_packet_digest:
            raise ZaiTrialLedgerError("zai_success_requires_result_digest")
        with self._lock, self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM zai_trial_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone():
                raise ZaiTrialLedgerError("zai_outcome_requires_reserved_attempt")
            if connection.execute(
                "SELECT 1 FROM zai_trial_outcomes WHERE attempt_id = ?", (attempt_id,)
            ).fetchone():
                raise ZaiTrialLedgerError("zai_outcome_already_recorded")
            connection.execute(
                """INSERT INTO zai_trial_outcomes (attempt_id, status, finished_at, latency_ms,
                result_packet_digest, cost_usd, error_type) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (attempt_id, status, _now(), int(latency_ms), result_packet_digest, cost_usd, error_type),
            )

    def record_evaluation(
        self,
        attempt_id: str,
        *,
        evaluator: str,
        correctness: str,
        first_pass: bool,
        correction_count: int,
        privacy_fit: str,
        usefulness: str,
        notes: str | None = None,
    ) -> None:
        """Record the non-Z.ai evaluator's verdict; Z.ai never assesses its own work."""

        if not evaluator.strip() or evaluator.strip().lower() in _SELF_NAMES:
            raise ZaiTrialLedgerError("zai_cannot_evaluate_its_own_work")
        if correctness not in CORRECTNESS:
            raise ZaiTrialLedgerError("zai_evaluation_correctness_unknown")
        if privacy_fit not in PRIVACY_FIT:
            raise ZaiTrialLedgerError("zai_evaluation_privacy_fit_unknown")
        if usefulness not in USEFULNESS:
            raise ZaiTrialLedgerError("zai_evaluation_usefulness_unknown")
        if correction_count < 0 or (first_pass and correction_count):
            raise ZaiTrialLedgerError("zai_first_pass_means_zero_corrections")
        notes_digest = (
            "sha256:" + hashlib.sha256(notes.encode("utf-8")).hexdigest() if notes else None
        )
        with self._lock, self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM zai_trial_outcomes WHERE attempt_id = ?", (attempt_id,)
            ).fetchone():
                raise ZaiTrialLedgerError("zai_evaluation_requires_recorded_outcome")
            if connection.execute(
                "SELECT 1 FROM zai_trial_evaluations WHERE attempt_id = ?", (attempt_id,)
            ).fetchone():
                raise ZaiTrialLedgerError("zai_evaluation_already_recorded")
            connection.execute(
                """INSERT INTO zai_trial_evaluations (attempt_id, evaluated_at, evaluator,
                correctness, first_pass, correction_count, privacy_fit, usefulness, notes_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt_id,
                    _now(),
                    evaluator.strip(),
                    correctness,
                    int(bool(first_pass)),
                    int(correction_count),
                    privacy_fit,
                    usefulness,
                    notes_digest,
                ),
            )

    def attempts(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT a.attempt_id, a.assignment_id, a.task_id, a.work_packet_digest, a.model,
                a.purpose, a.recovery, a.started_at, o.status, o.finished_at, o.latency_ms,
                o.result_packet_digest, o.cost_usd, o.error_type, e.evaluator, e.correctness,
                e.first_pass, e.correction_count, e.privacy_fit, e.usefulness
                FROM zai_trial_attempts a
                LEFT JOIN zai_trial_outcomes o ON o.attempt_id = a.attempt_id
                LEFT JOIN zai_trial_evaluations e ON e.attempt_id = a.attempt_id
                ORDER BY a.started_at, a.attempt_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def status(self) -> dict[str, Any]:
        """Trial metrics for the 2026-09-29 owner decision; grants nothing."""

        rows = self.attempts()
        outcomes = [row for row in rows if row["status"]]
        evaluated = [row for row in rows if row["correctness"]]
        failures = sum(1 for row in outcomes if row["status"] in ("FAILED", "AMBIGUOUS"))
        first_pass_correct = sum(
            1 for row in evaluated if row["correctness"] == "correct" and row["first_pass"]
        )
        return {
            "version": LEDGER_VERSION,
            "lane": ZAI_CONTACT_LANE.as_dict(),
            "attempts": len(rows),
            "succeeded": sum(1 for row in outcomes if row["status"] == "SUCCEEDED"),
            "failed": sum(1 for row in outcomes if row["status"] == "FAILED"),
            "ambiguous": sum(1 for row in outcomes if row["status"] == "AMBIGUOUS"),
            "unresolved": len(rows) - len(outcomes),
            "evaluated": len(evaluated),
            "first_pass_yield": (
                round(first_pass_correct / len(evaluated), 4) if evaluated else None
            ),
            "correction_rate": (
                round(sum(row["correction_count"] for row in evaluated) / len(evaluated), 4)
                if evaluated
                else None
            ),
            "mean_latency_ms": (
                round(sum(row["latency_ms"] for row in outcomes) / len(outcomes))
                if outcomes
                else None
            ),
            "cost_known": sum(1 for row in outcomes if row["cost_usd"] is not None),
            "recorded_failures": failures,
            "route_available": failures < MAX_RECORDED_FAILURES,
            "trial_window_open": datetime.now(timezone.utc) < TRIAL_END,
            "dispatch_authorized": False,
            "production_credit_issued": False,
            "review_authority": False,
            "merge_authority": False,
        }


class ZaiTrialLane:
    """Bounded, tools-off, single-turn Lite dispatch through the existing trial adapter.

    The lane never touches TaskStore, the interaction ledger or the packet result
    consumer: a trial result is evidence in the trial ledger, not a ProviderRun.
    """

    def __init__(self, *, adapter, ledger: ZaiTrialLedger, model: str):
        if getattr(adapter, "provider", None) != ZAI_CONTACT_LANE.lane_id:
            raise ValueError("the trial lane only drives the zai packet boundary adapter")
        self.adapter = adapter
        self.ledger = ledger
        self.model = model

    @classmethod
    def from_settings(cls, settings, *, moment: datetime | None = None) -> "ZaiTrialLane":
        readiness = settings.zai_trial_readiness(moment=moment)
        closed = sorted(name for name, ready in readiness.items() if not ready)
        if closed:
            raise ZaiTrialLaneUnavailable("zai trial lane is not ready: " + ", ".join(closed))
        adapter = build_zai_trial_adapter(
            executable=Path(settings.zai_cli_executable).expanduser(),
            api_key=settings.zai_api_key,
            journal=Path(settings.zai_trial_journal_dir).expanduser(),
            model=settings.zai_model,
            lite_verified=settings.zai_lite_entitlement_verified,
        )
        return cls(
            adapter=adapter,
            ledger=ZaiTrialLedger(settings.zai_trial_ledger_path),
            model=settings.zai_model,
        )

    def dispatch(
        self,
        work_packet: Mapping[str, Any],
        *,
        context: ProviderAdapterContext,
        purpose: str,
        recovery_basis: str | None = None,
    ) -> ZaiTrialDispatch:
        if datetime.now(timezone.utc) >= TRIAL_END:
            raise ZaiTrialLaneUnavailable("Lite trial window closed; separate owner decision required")
        if context.allowed_tools or context.allowed_external_effects or context.budget_usd != 0:
            raise ZaiTrialLaneUnavailable("trial dispatch requires no tools, external effects or budget")
        attempt_id = self.ledger.reserve(
            assignment_id=context.assignment_id,
            task_id=context.task_id,
            work_packet_digest=context.work_packet_digest,
            idempotency_key=context.idempotency_key,
            model=self.model,
            purpose=purpose,
            recovery_basis=recovery_basis,
        )
        started = time.monotonic()
        try:
            result = self.adapter.dispatch(work_packet, context=context)
        except Exception as exc:
            # Timeouts, output overflow and a pre-existing journal reservation
            # leave the provider state unknown: never redispatched.
            ambiguous = isinstance(exc, (IssuerError, FileExistsError))
            self.ledger.record_outcome(
                attempt_id,
                status="AMBIGUOUS" if ambiguous else "FAILED",
                latency_ms=int((time.monotonic() - started) * 1000),
                error_type=type(exc).__name__,
            )
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        succeeded = result.get("status") == "SUCCEEDED"
        self.ledger.record_outcome(
            attempt_id,
            status="SUCCEEDED" if succeeded else "FAILED",
            latency_ms=latency_ms,
            result_packet_digest=document_digest(result),
            # The host CLI prices with Anthropic tariffs, so cost stays unknown.
            cost_usd=None,
            error_type=None if succeeded else str(result.get("status")),
        )
        return ZaiTrialDispatch(attempt_id=attempt_id, latency_ms=latency_ms, result=result)
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | END]]
