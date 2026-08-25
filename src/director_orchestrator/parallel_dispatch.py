# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 02:08 UTC | BEGIN]]
"""Deterministic policy and durable ledger primitives for bounded dispatch batches.

This module performs no provider call.  A runtime may use the pure planner to
reserve one exact batch, then claim each member exactly once through the ledger.
Ambiguous members require explicit reconciliation and are never redispatched.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterator, Mapping, Sequence


class ParallelDispatchError(RuntimeError):
    """The proposed parallel operation is not safe to perform."""


class ParallelDispatchIntegrityError(ParallelDispatchError):
    """Durable batch or effect identity conflicts with recorded state."""


class DailyTaskBudgetExceeded(ParallelDispatchError):
    """The deployment-wide conservative paid-task allowance is exhausted."""


class MemberState(StrEnum):
    RESERVED = "RESERVED"
    DISPATCHING = "DISPATCHING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"
    CANCELLED = "CANCELLED"
    RECONCILED_SUCCEEDED = "RECONCILED_SUCCEEDED"
    RECONCILED_FAILED = "RECONCILED_FAILED"


class BatchStatus(StrEnum):
    RESERVED = "RESERVED"
    ACTIVE = "ACTIVE"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_AFTER_RECONCILIATION = "SUCCEEDED_AFTER_RECONCILIATION"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True)
class DispatchCandidate:
    sequence: int
    provider: str
    effect_key: str
    depends_on: tuple[int, ...] = ()
    write_surfaces: tuple[str, ...] = ()
    dispatch_cost_usd: float = 0.0
    review_cost_usd: float = 0.0

    @property
    def prospective_cost_usd(self) -> float:
        return self.dispatch_cost_usd + self.review_cost_usd


@dataclass(frozen=True)
class ParallelDispatchHealth:
    clean_batch_streak: int = 0
    backpressure: bool = False
    recovery_clean_streak: int = 0


@dataclass(frozen=True)
class ParallelDispatchPolicy:
    enabled: bool = False
    kill_switch_engaged: bool = True
    initial_cap: int = 2
    scaled_cap: int = 3
    clean_batches_to_scale: int = 10
    provider_caps: Mapping[str, int] | None = None
    provider_cooldowns_until: Mapping[str, datetime] | None = None

    def effective_cap(self, health: ParallelDispatchHealth) -> int:
        if not self.enabled or self.kill_switch_engaged:
            return 0
        if health.backpressure:
            return 1
        return (
            self.scaled_cap
            if health.clean_batch_streak >= self.clean_batches_to_scale
            else self.initial_cap
        )


@dataclass(frozen=True)
class DispatchBatch:
    batch_id: str
    task_id: str
    ordinal: int
    members: tuple[DispatchCandidate, ...]
    reserved_budget_usd: float


@dataclass(frozen=True)
class BatchDecision:
    batch: DispatchBatch | None
    effective_cap: int
    blocked: Mapping[int, str]


@dataclass(frozen=True)
class MemberSnapshot:
    sequence: int
    provider: str
    effect_key: str
    state: MemberState
    prospective_cost_usd: float


@dataclass(frozen=True)
class BatchAggregate:
    batch_id: str
    status: BatchStatus
    members: tuple[MemberSnapshot, ...]
    reserved_budget_usd: float


_EFFECT_KEY = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SURFACE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")


def _surface(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    if not normalized or not _SAFE_SURFACE.fullmatch(normalized):
        raise ParallelDispatchError("write surfaces must be safe repository-relative paths")
    return normalized.casefold()


def _surfaces_conflict(left: Sequence[str], right: Sequence[str]) -> bool:
    for raw_left in left:
        left_surface = _surface(raw_left)
        for raw_right in right:
            right_surface = _surface(raw_right)
            if (
                left_surface == right_surface
                or left_surface.startswith(right_surface + "/")
                or right_surface.startswith(left_surface + "/")
            ):
                return True
    return False


def _candidate_payload(candidate: DispatchCandidate) -> dict[str, object]:
    return {
        "sequence": candidate.sequence,
        "provider": candidate.provider,
        "effect_key": candidate.effect_key,
        "depends_on": list(candidate.depends_on),
        "write_surfaces": sorted(_surface(item) for item in candidate.write_surfaces),
        "dispatch_cost_usd": format(candidate.dispatch_cost_usd, ".12g"),
        "review_cost_usd": format(candidate.review_cost_usd, ".12g"),
    }


def _batch_identity(
    task_id: str, ordinal: int, members: Sequence[DispatchCandidate]
) -> str:
    payload = {
        "task_id": task_id,
        "ordinal": ordinal,
        "members": [_candidate_payload(item) for item in members],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(b"director-dispatch-batch/v1\0" + encoded).hexdigest()


def plan_dispatch_batch(
    *,
    task_id: str,
    ordinal: int,
    candidates: Sequence[DispatchCandidate],
    completed_sequences: Sequence[int],
    available_budget_usd: float,
    policy: ParallelDispatchPolicy,
    health: ParallelDispatchHealth = ParallelDispatchHealth(),
    now: datetime | None = None,
) -> BatchDecision:
    """Select one deterministic, dependency- and write-independent batch."""

    if not task_id or ordinal < 1 or available_budget_usd < 0:
        raise ParallelDispatchError("batch identity and available budget are required")
    cap = policy.effective_cap(health)
    if cap == 0:
        reason = "kill_switch" if policy.kill_switch_engaged else "disabled"
        return BatchDecision(None, 0, {item.sequence: reason for item in candidates})
    if policy.initial_cap != 2 or policy.scaled_cap != 3:
        raise ParallelDispatchError("bounded parallel caps must start at two and scale to three")
    if policy.clean_batches_to_scale != 10:
        raise ParallelDispatchError("parallel scaling requires ten clean batches")

    ordered = sorted(candidates, key=lambda item: (item.sequence, item.effect_key))
    sequences = [item.sequence for item in ordered]
    effects = [item.effect_key for item in ordered]
    if len(set(sequences)) != len(sequences) or len(set(effects)) != len(effects):
        raise ParallelDispatchError("candidate sequences and effect keys must be unique")
    completed = {int(item) for item in completed_sequences}
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    provider_caps = {
        key.casefold(): value for key, value in (policy.provider_caps or {}).items()
    }
    cooldowns = {
        key.casefold(): value
        for key, value in (policy.provider_cooldowns_until or {}).items()
    }
    selected: list[DispatchCandidate] = []
    blocked: dict[int, str] = {}
    provider_counts: dict[str, int] = {}
    reserved = 0.0

    for candidate in ordered:
        provider = candidate.provider.strip().casefold()
        if candidate.sequence < 1 or not provider or not _EFFECT_KEY.fullmatch(candidate.effect_key):
            raise ParallelDispatchError("candidates require canonical sequence, provider, and effect key")
        if candidate.dispatch_cost_usd < 0 or candidate.review_cost_usd < 0:
            raise ParallelDispatchError("dispatch and independent-review costs cannot be negative")
        if len(set(candidate.depends_on)) != len(candidate.depends_on):
            raise ParallelDispatchError("candidate dependencies must be unique")
        if not set(candidate.depends_on).issubset(completed):
            blocked[candidate.sequence] = "dependency_incomplete"
            continue
        cooldown = cooldowns.get(provider)
        if cooldown is not None:
            if cooldown.tzinfo is None:
                raise ParallelDispatchError("provider cooldowns must be timezone-aware")
            if cooldown.astimezone(timezone.utc) > observed_at:
                blocked[candidate.sequence] = "provider_cooldown"
                continue
        provider_cap = provider_caps.get(provider, 1)
        if provider_cap < 1:
            raise ParallelDispatchError("provider caps must be positive")
        if provider_counts.get(provider, 0) >= provider_cap:
            blocked[candidate.sequence] = "provider_cap"
            continue
        if any(
            candidate.sequence in member.depends_on
            or member.sequence in candidate.depends_on
            or _surfaces_conflict(candidate.write_surfaces, member.write_surfaces)
            for member in selected
        ):
            blocked[candidate.sequence] = "batch_not_independent"
            continue
        if reserved + candidate.prospective_cost_usd > available_budget_usd + 1e-9:
            blocked[candidate.sequence] = "prospective_budget"
            continue
        if len(selected) >= cap:
            blocked[candidate.sequence] = "effective_cap"
            continue
        selected.append(candidate)
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        reserved += candidate.prospective_cost_usd

    if not selected:
        return BatchDecision(None, cap, blocked)
    members = tuple(selected)
    batch = DispatchBatch(
        batch_id=_batch_identity(task_id, ordinal, members),
        task_id=task_id,
        ordinal=ordinal,
        members=members,
        reserved_budget_usd=reserved,
    )
    return BatchDecision(batch, cap, blocked)


def observe_batch(
    health: ParallelDispatchHealth,
    aggregate: BatchAggregate,
    *,
    recovery_clean_batches: int = 3,
) -> ParallelDispatchHealth:
    """Apply deterministic scaling/backpressure state after a terminal batch."""

    if recovery_clean_batches != 3:
        raise ParallelDispatchError("backpressure recovery requires three clean batches")
    if aggregate.status == BatchStatus.SUCCEEDED:
        if health.backpressure:
            recovered = health.recovery_clean_streak + 1
            if recovered < recovery_clean_batches:
                return ParallelDispatchHealth(
                    clean_batch_streak=0,
                    backpressure=True,
                    recovery_clean_streak=recovered,
                )
            return ParallelDispatchHealth()
        return ParallelDispatchHealth(
            clean_batch_streak=health.clean_batch_streak + 1,
            backpressure=False,
            recovery_clean_streak=0,
        )
    if aggregate.status in {
        BatchStatus.FAILED,
        BatchStatus.PARTIAL,
        BatchStatus.RECONCILIATION_REQUIRED,
        BatchStatus.SUCCEEDED_AFTER_RECONCILIATION,
    }:
        return ParallelDispatchHealth(
            clean_batch_streak=0, backpressure=True, recovery_clean_streak=0
        )
    return health


class ParallelDispatchLedger:
    """SQLite-backed exact-once reservation and member transition ledger."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
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
                """CREATE TABLE IF NOT EXISTS parallel_dispatch_batches(
                    batch_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    reserved_budget_usd REAL NOT NULL,
                    utc_day TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, ordinal)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS parallel_dispatch_members(
                    batch_id TEXT NOT NULL,
                    effect_key TEXT NOT NULL UNIQUE,
                    sequence INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    prospective_cost_usd REAL NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT,
                    PRIMARY KEY(batch_id, effect_key),
                    FOREIGN KEY(batch_id) REFERENCES parallel_dispatch_batches(batch_id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS parallel_dispatch_health(
                    task_id TEXT PRIMARY KEY,
                    clean_batch_streak INTEGER NOT NULL,
                    backpressure INTEGER NOT NULL,
                    recovery_clean_streak INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runtime_pre_submit_closures(
                    idempotency_key TEXT PRIMARY KEY,
                    parallel_effect_key TEXT,
                    closed_at TEXT NOT NULL
                )"""
            )

    @staticmethod
    def _identity_json(batch: DispatchBatch) -> str:
        return json.dumps(
            {
                "batch_id": batch.batch_id,
                "task_id": batch.task_id,
                "ordinal": batch.ordinal,
                "members": [_candidate_payload(item) for item in batch.members],
                "reserved_budget_usd": format(batch.reserved_budget_usd, ".12g"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def reserve(
        self, batch: DispatchBatch, *, daily_budget_usd: float | None = None
    ) -> BatchAggregate:
        expected_id = _batch_identity(batch.task_id, batch.ordinal, batch.members)
        expected_budget = sum(item.prospective_cost_usd for item in batch.members)
        if batch.batch_id != expected_id or abs(batch.reserved_budget_usd - expected_budget) > 1e-9:
            raise ParallelDispatchIntegrityError("batch identity or reserved budget is invalid")
        identity = self._identity_json(batch)
        with self._lock, self._connect(immediate=True) as connection:
            existing = connection.execute(
                """SELECT batch_id, identity_json FROM parallel_dispatch_batches
                WHERE task_id = ? AND ordinal = ?""",
                (batch.task_id, batch.ordinal),
            ).fetchone()
            if existing is not None:
                if existing["batch_id"] != batch.batch_id or existing["identity_json"] != identity:
                    raise ParallelDispatchIntegrityError("batch ordinal already has different identity")
                return self._aggregate(connection, batch.batch_id)
            utc_day = datetime.now(timezone.utc).date().isoformat()
            if daily_budget_usd is not None:
                if daily_budget_usd <= 0:
                    raise ParallelDispatchError("daily provider budget must be positive")
                reserved_today = float(
                    connection.execute(
                        """SELECT COALESCE(SUM(reserved_budget_usd), 0)
                        FROM parallel_dispatch_batches WHERE utc_day = ?""",
                        (utc_day,),
                    ).fetchone()[0]
                )
                if reserved_today + batch.reserved_budget_usd > daily_budget_usd + 1e-9:
                    raise ParallelDispatchError("daily provider budget is exhausted")
            try:
                connection.execute(
                    """INSERT INTO parallel_dispatch_batches(
                        batch_id,task_id,ordinal,reserved_budget_usd,utc_day,identity_json,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        batch.batch_id,
                        batch.task_id,
                        batch.ordinal,
                        batch.reserved_budget_usd,
                        utc_day,
                        identity,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                for member in batch.members:
                    connection.execute(
                        """INSERT INTO parallel_dispatch_members(
                            batch_id,effect_key,sequence,provider,prospective_cost_usd,state
                        ) VALUES(?,?,?,?,?,?)""",
                        (
                            batch.batch_id,
                            member.effect_key,
                            member.sequence,
                            member.provider.casefold(),
                            member.prospective_cost_usd,
                            MemberState.RESERVED.value,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ParallelDispatchIntegrityError(
                    "effect key already belongs to another durable batch"
                ) from exc
            return self._aggregate(connection, batch.batch_id)

    def next_ordinal(self, task_id: str) -> int:
        if not task_id.strip():
            raise ParallelDispatchError("task identity is required")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) FROM parallel_dispatch_batches WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row[0]) + 1

    def find_latest_batch(
        self, task_id: str, effect_keys: Sequence[str]
    ) -> BatchAggregate | None:
        """Return the newest batch containing an exact current candidate effect.

        This is the restart edge: RESERVED work may be claimed, DISPATCHING work
        is replayed through the runtime ledger (never blindly resent), and a
        SUCCEEDED member can have its already-durable result consumed after a
        process crash.
        """

        keys = tuple(dict.fromkeys(effect_keys))
        if not task_id.strip() or not keys:
            return None
        if any(not _EFFECT_KEY.fullmatch(item) for item in keys):
            raise ParallelDispatchError("recovery effect keys must be canonical")
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT b.batch_id FROM parallel_dispatch_batches b
                JOIN parallel_dispatch_members m ON m.batch_id = b.batch_id
                WHERE b.task_id = ? AND m.effect_key IN ({placeholders})
                ORDER BY b.ordinal DESC LIMIT 1""",
                (task_id, *keys),
            ).fetchone()
            if row is None:
                return None
            return self._aggregate(connection, str(row[0]))

    def load_health(self, task_id: str) -> ParallelDispatchHealth:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT clean_batch_streak,backpressure,recovery_clean_streak
                FROM parallel_dispatch_health WHERE task_id = ?""",
                (task_id,),
            ).fetchone()
        if row is None:
            return ParallelDispatchHealth()
        return ParallelDispatchHealth(
            clean_batch_streak=int(row["clean_batch_streak"]),
            backpressure=bool(row["backpressure"]),
            recovery_clean_streak=int(row["recovery_clean_streak"]),
        )

    def observe_and_persist(
        self, task_id: str, aggregate: BatchAggregate
    ) -> ParallelDispatchHealth:
        with self._lock, self._connect(immediate=True) as connection:
            row = connection.execute(
                """SELECT clean_batch_streak,backpressure,recovery_clean_streak
                FROM parallel_dispatch_health WHERE task_id = ?""",
                (task_id,),
            ).fetchone()
            current = (
                ParallelDispatchHealth()
                if row is None
                else ParallelDispatchHealth(
                    clean_batch_streak=int(row["clean_batch_streak"]),
                    backpressure=bool(row["backpressure"]),
                    recovery_clean_streak=int(row["recovery_clean_streak"]),
                )
            )
            updated = observe_batch(current, aggregate)
            connection.execute(
                """INSERT INTO parallel_dispatch_health(
                    task_id,clean_batch_streak,backpressure,recovery_clean_streak,updated_at
                ) VALUES(?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
                    clean_batch_streak=excluded.clean_batch_streak,
                    backpressure=excluded.backpressure,
                    recovery_clean_streak=excluded.recovery_clean_streak,
                    updated_at=excluded.updated_at""",
                (
                    task_id,
                    updated.clean_batch_streak,
                    int(updated.backpressure),
                    updated.recovery_clean_streak,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return updated

    def claim(
        self,
        batch_id: str,
        effect_key: str,
        *,
        provider_limit: int = 1,
        deployment_limit: int = 2,
    ) -> bool:
        """Claim one member under provider and deployment-wide durable caps."""

        if provider_limit < 1:
            raise ParallelDispatchError("provider concurrency limit must be positive")
        if deployment_limit < 1:
            raise ParallelDispatchError("deployment concurrency limit must be positive")
        with self._lock, self._connect(immediate=True) as connection:
            member = connection.execute(
                """SELECT provider,state FROM parallel_dispatch_members
                WHERE batch_id = ? AND effect_key = ?""",
                (batch_id, effect_key),
            ).fetchone()
            if member is None or member["state"] != MemberState.RESERVED.value:
                return False
            provider_active = int(
                connection.execute(
                    """SELECT COUNT(*) FROM parallel_dispatch_members
                    WHERE provider = ? AND state IN (?, ?)""",
                    (
                        member["provider"],
                        MemberState.DISPATCHING.value,
                        MemberState.AMBIGUOUS.value,
                    ),
                ).fetchone()[0]
            )
            deployment_active = int(
                connection.execute(
                    """SELECT COUNT(*) FROM parallel_dispatch_members
                    WHERE state IN (?, ?)""",
                    (
                        MemberState.DISPATCHING.value,
                        MemberState.AMBIGUOUS.value,
                    ),
                ).fetchone()[0]
            )
            if (
                provider_active >= provider_limit
                or deployment_active >= deployment_limit
            ):
                return False
            changed = connection.execute(
                """UPDATE parallel_dispatch_members SET state = ?
                WHERE batch_id = ? AND effect_key = ? AND state = ?""",
                (
                    MemberState.DISPATCHING.value,
                    batch_id,
                    effect_key,
                    MemberState.RESERVED.value,
                ),
            ).rowcount
            return changed == 1

    def reserve_pre_submit_closure(
        self,
        *,
        runtime_idempotency_key: str,
        parallel_effect_key: str | None = None,
    ) -> bool:
        """Persist an exact-key tombstone before task-level pre-submit closure."""

        if not runtime_idempotency_key.strip():
            raise ParallelDispatchError("runtime idempotency key is required")
        if parallel_effect_key is not None and not _EFFECT_KEY.fullmatch(
            parallel_effect_key
        ):
            raise ParallelDispatchError("parallel effect key must be canonical")
        with self._lock, self._connect(immediate=True) as connection:
            existing_closure = connection.execute(
                """SELECT parallel_effect_key FROM runtime_pre_submit_closures
                WHERE idempotency_key = ?""",
                (runtime_idempotency_key,),
            ).fetchone()
            if existing_closure is not None:
                if existing_closure["parallel_effect_key"] != parallel_effect_key:
                    raise ParallelDispatchIntegrityError(
                        "runtime pre-submit closure identity conflicts"
                    )
                return True
            runtime_table = connection.execute(
                """SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'runtime_dispatch_ledger'"""
            ).fetchone()
            runtime_owned = runtime_table is not None and connection.execute(
                    """SELECT 1 FROM runtime_dispatch_ledger
                    WHERE idempotency_key = ?""",
                    (runtime_idempotency_key,),
                ).fetchone() is not None
            parallel_owned = False
            if parallel_effect_key is not None:
                row = connection.execute(
                    """SELECT state FROM parallel_dispatch_members
                    WHERE effect_key = ?""",
                    (parallel_effect_key,),
                ).fetchone()
                parallel_owned = row is not None and row["state"] not in {
                    MemberState.RESERVED.value,
                    MemberState.CANCELLED.value,
                }
            if runtime_owned or parallel_owned:
                return False
            connection.execute(
                """INSERT INTO runtime_pre_submit_closures(
                    idempotency_key, parallel_effect_key, closed_at
                ) VALUES (?, ?, ?)""",
                (
                    runtime_idempotency_key,
                    parallel_effect_key,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return True

    def record(
        self, batch_id: str, effect_key: str, state: MemberState, *, detail: str = ""
    ) -> BatchAggregate:
        if state not in {MemberState.SUCCEEDED, MemberState.FAILED, MemberState.AMBIGUOUS}:
            raise ParallelDispatchError("dispatch completion state is invalid")
        return self._transition(
            batch_id, effect_key, MemberState.DISPATCHING, state, detail=detail
        )

    def reconcile(
        self, batch_id: str, effect_key: str, *, succeeded: bool, detail: str
    ) -> BatchAggregate:
        target = (
            MemberState.RECONCILED_SUCCEEDED
            if succeeded
            else MemberState.RECONCILED_FAILED
        )
        return self._transition(
            batch_id, effect_key, MemberState.AMBIGUOUS, target, detail=detail
        )

    def engage_kill_switch(self, batch_id: str) -> BatchAggregate:
        """Cancel unstarted work; in-flight work remains visible for reconciliation."""

        return self.cancel_unstarted(batch_id, reason="kill_switch")

    def cancel_unstarted(self, batch_id: str, *, reason: str) -> BatchAggregate:
        """Cancel only members with no possible external side effect."""

        if not reason.strip():
            raise ParallelDispatchError("cancellation requires a recorded reason")
        with self._lock, self._connect(immediate=True) as connection:
            connection.execute(
                """UPDATE parallel_dispatch_members SET state = ?, detail = ?
                WHERE batch_id = ? AND state = ?""",
                (
                    MemberState.CANCELLED.value,
                    reason.strip(),
                    batch_id,
                    MemberState.RESERVED.value,
                ),
            )
            return self._aggregate(connection, batch_id)

    def aggregate(self, batch_id: str) -> BatchAggregate:
        with self._connect() as connection:
            return self._aggregate(connection, batch_id)

    def _transition(
        self,
        batch_id: str,
        effect_key: str,
        expected: MemberState,
        target: MemberState,
        *,
        detail: str,
    ) -> BatchAggregate:
        with self._lock, self._connect(immediate=True) as connection:
            changed = connection.execute(
                """UPDATE parallel_dispatch_members SET state = ?, detail = ?
                WHERE batch_id = ? AND effect_key = ? AND state = ?""",
                (target.value, detail, batch_id, effect_key, expected.value),
            ).rowcount
            if changed != 1:
                raise ParallelDispatchIntegrityError(
                    f"member transition requires durable {expected.value} state"
                )
            return self._aggregate(connection, batch_id)

    @staticmethod
    def _aggregate(
        connection: sqlite3.Connection, batch_id: str
    ) -> BatchAggregate:
        batch = connection.execute(
            "SELECT reserved_budget_usd FROM parallel_dispatch_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if batch is None:
            raise KeyError(batch_id)
        rows = connection.execute(
            """SELECT sequence,provider,effect_key,state,prospective_cost_usd
            FROM parallel_dispatch_members WHERE batch_id = ?
            ORDER BY sequence,effect_key""",
            (batch_id,),
        ).fetchall()
        members = tuple(
            MemberSnapshot(
                sequence=int(row["sequence"]),
                provider=str(row["provider"]),
                effect_key=str(row["effect_key"]),
                state=MemberState(row["state"]),
                prospective_cost_usd=float(row["prospective_cost_usd"]),
            )
            for row in rows
        )
        states = {item.state for item in members}
        if MemberState.AMBIGUOUS in states:
            status = BatchStatus.RECONCILIATION_REQUIRED
        elif states == {MemberState.RESERVED}:
            status = BatchStatus.RESERVED
        elif MemberState.DISPATCHING in states or MemberState.RESERVED in states:
            status = BatchStatus.ACTIVE
        elif states <= {MemberState.SUCCEEDED}:
            status = BatchStatus.SUCCEEDED
        elif states <= {MemberState.SUCCEEDED, MemberState.RECONCILED_SUCCEEDED}:
            status = BatchStatus.SUCCEEDED_AFTER_RECONCILIATION
        elif states == {MemberState.CANCELLED}:
            status = BatchStatus.CANCELLED
        elif states & {MemberState.FAILED, MemberState.RECONCILED_FAILED}:
            status = BatchStatus.FAILED
        else:
            status = BatchStatus.PARTIAL
        return BatchAggregate(
            batch_id=batch_id,
            status=status,
            members=members,
            reserved_budget_usd=float(batch["reserved_budget_usd"]),
        )


class DailyTaskBudgetLedger:
    """Conservatively reserve one full task allowance before paid planning.

    Reserving the deployment's complete per-task ceiling at intake makes the
    daily stop independent of provider-reported usage and prevents concurrent
    tasks from jointly exceeding the operator's configured daily allowance.
    Reservations are intentionally never released: an interrupted provider call
    can still incur cost, so over-counting is the fail-safe outcome.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect(immediate=True) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS paid_task_daily_budget(
                    task_id TEXT PRIMARY KEY,
                    utc_day TEXT NOT NULL,
                    reserved_budget_usd REAL NOT NULL CHECK(reserved_budget_usd > 0),
                    recorded_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_paid_task_daily_budget_day
                ON paid_task_daily_budget(utc_day)"""
            )

    @contextmanager
    def _connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection
        finally:
            connection.close()

    def reserve(
        self,
        *,
        task_id: str,
        task_budget_usd: float,
        daily_budget_usd: float,
        now: datetime | None = None,
    ) -> float:
        if not task_id.strip() or task_budget_usd <= 0 or daily_budget_usd <= 0:
            raise ParallelDispatchError(
                "paid task and positive task/daily budgets are required"
            )
        if task_budget_usd > daily_budget_usd + 1e-9:
            raise DailyTaskBudgetExceeded(
                "the per-task paid ceiling exceeds the deployment daily ceiling"
            )
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        utc_day = observed_at.date().isoformat()
        with self._connect(immediate=True) as connection:
            existing = connection.execute(
                """SELECT utc_day,reserved_budget_usd FROM paid_task_daily_budget
                WHERE task_id = ?""",
                (task_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing[0]) != utc_day
                    or abs(float(existing[1]) - task_budget_usd) > 1e-9
                ):
                    raise ParallelDispatchIntegrityError(
                        "paid task identity was reused with different accounting"
                    )
                return float(existing[1])
            reserved = float(
                connection.execute(
                    """SELECT COALESCE(SUM(reserved_budget_usd), 0)
                    FROM paid_task_daily_budget WHERE utc_day = ?""",
                    (utc_day,),
                ).fetchone()[0]
            )
            if reserved + task_budget_usd > daily_budget_usd + 1e-9:
                raise DailyTaskBudgetExceeded(
                    "the deployment daily paid-task budget is exhausted"
                )
            connection.execute(
                """INSERT INTO paid_task_daily_budget(
                    task_id,utc_day,reserved_budget_usd,recorded_at
                ) VALUES(?,?,?,?)""",
                (
                    task_id,
                    utc_day,
                    task_budget_usd,
                    observed_at.isoformat(),
                ),
            )
            return reserved + task_budget_usd
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 02:08 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:26 UTC | END]]
