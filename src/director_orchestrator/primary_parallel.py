# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:42 UTC | BEGIN]]
"""Postgres-native dependency-ready dispatch for the primary coordinator.

The ledger is intentionally separate from the legacy SQLite parallel ledger.
Every reservation is serialized in Postgres under the current task-writer fence.
An ``AMBIGUOUS`` reservation continues to consume its slot and can never be
reserved again; authenticated reconciliation must settle it explicitly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence

from .langgraph_postgres import (
    PostgresConditionalClaimStore,
    PostgresControlPlaneError,
    TaskWriterLease,
)


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SAFE_SURFACE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ParallelDispatchError(RuntimeError):
    """The plan or durable dispatch state failed closed."""


class ParallelAssignmentStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    AMBIGUOUS = "AMBIGUOUS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_digest(value: str, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ParallelDispatchError(f"{field} must be a sha256 digest")
    return value


def _require_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ParallelDispatchError(f"{field} is invalid")
    return value


def canonical_write_surface(value: str) -> str:
    """Return one Windows-stable repository-relative write surface."""

    if not isinstance(value, str) or value != value.strip() or not value:
        raise ParallelDispatchError("write surfaces must be clean relative paths")
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or _WINDOWS_DRIVE.match(normalized)
        or not _SAFE_SURFACE.fullmatch(normalized)
    ):
        raise ParallelDispatchError("write surfaces must be safe repository-relative paths")
    components = normalized.split("/")
    if any(component in {".", ".."} for component in components):
        raise ParallelDispatchError("write surfaces must not contain dot segments")
    return normalized.casefold()


def write_surfaces_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    """Detect exact aliases and parent/child subtree overlap."""

    for raw_left in left:
        left_surface = canonical_write_surface(raw_left)
        for raw_right in right:
            right_surface = canonical_write_surface(raw_right)
            if (
                left_surface == right_surface
                or left_surface.startswith(right_surface + "/")
                or right_surface.startswith(left_surface + "/")
            ):
                return True
    return False


@dataclass(frozen=True, slots=True)
class ParallelAssignment:
    assignment_id: str
    sequence: int
    provider_id: str
    deployment_id: str
    dependencies: tuple[str, ...] = ()
    write_surfaces: tuple[str, ...] = ()
    retry_limit: int = 1

    def __post_init__(self) -> None:
        _require_id(self.assignment_id, "assignment_id")
        _require_id(self.provider_id, "provider_id")
        _require_id(self.deployment_id, "deployment_id")
        if type(self.sequence) is not int or not 1 <= self.sequence <= 20:
            raise ParallelDispatchError("sequence must be between 1 and 20")
        if type(self.retry_limit) is not int or not 1 <= self.retry_limit <= 3:
            raise ParallelDispatchError("retry_limit must be between 1 and 3")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ParallelDispatchError("dependencies must be unique")
        canonical_surfaces = tuple(
            canonical_write_surface(value) for value in self.write_surfaces
        )
        if len(set(canonical_surfaces)) != len(canonical_surfaces):
            raise ParallelDispatchError("write surfaces must be unique after normalization")
        for index, surface in enumerate(canonical_surfaces):
            if write_surfaces_overlap((surface,), canonical_surfaces[index + 1 :]):
                raise ParallelDispatchError("write surfaces must not overlap")
        object.__setattr__(self, "write_surfaces", canonical_surfaces)
        if any(not value.strip() for value in self.dependencies):
            raise ParallelDispatchError("dependencies must be non-empty IDs")
        if any(not value.strip() for value in self.write_surfaces):
            raise ParallelDispatchError("write surfaces must be non-empty")


@dataclass(frozen=True, slots=True)
class ParallelDispatchPolicy:
    max_concurrency: int = 2
    provider_caps: Mapping[str, int] | None = None
    deployment_caps: Mapping[str, int] | None = None
    scale_policy_evidence_digest: str | None = None
    scale_policy_accepted: bool = False

    def __post_init__(self) -> None:
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 3:
            raise ParallelDispatchError("max concurrency must be between 1 and 3")
        if self.max_concurrency == 3:
            if not self.scale_policy_accepted or self.scale_policy_evidence_digest is None:
                raise ParallelDispatchError(
                    "scaling from two to three requires accepted policy evidence"
                )
            _require_digest(
                self.scale_policy_evidence_digest, "scale_policy_evidence_digest"
            )
        elif self.scale_policy_evidence_digest is not None:
            _require_digest(
                self.scale_policy_evidence_digest, "scale_policy_evidence_digest"
            )
        for name, caps in (
            ("provider_caps", self.provider_caps or {}),
            ("deployment_caps", self.deployment_caps or {}),
        ):
            if any(type(value) is not int or value < 1 for value in caps.values()):
                raise ParallelDispatchError(f"{name} values must be positive integers")


@dataclass(frozen=True, slots=True)
class ParallelReservation:
    task_id: str
    assignment_id: str
    sequence: int
    provider_id: str
    deployment_id: str
    reservation_digest: str


@dataclass(frozen=True, slots=True)
class ParallelAggregate:
    task_id: str
    statuses: Mapping[str, str]
    result_digests: Mapping[str, str]
    aggregate_digest: str
    terminal: bool
    succeeded: bool
    ambiguous_slot_count: int


@dataclass(frozen=True, slots=True)
class ParallelDispatchOutcome:
    status: ParallelAssignmentStatus
    result_digest: str | None
    reason_code: str


def validate_assignment_dag(
    assignments: Sequence[ParallelAssignment],
) -> tuple[ParallelAssignment, ...]:
    """Return a deterministic topological order or reject the plan."""

    if not assignments or len(assignments) > 20:
        raise ParallelDispatchError("parallel plans require 1 through 20 assignments")
    by_id = {item.assignment_id: item for item in assignments}
    if len(by_id) != len(assignments):
        raise ParallelDispatchError("assignment IDs must be unique")
    if len({item.sequence for item in assignments}) != len(assignments):
        raise ParallelDispatchError("assignment sequences must be unique")
    for item in assignments:
        if item.assignment_id in item.dependencies:
            raise ParallelDispatchError("an assignment cannot depend on itself")
        missing = set(item.dependencies) - set(by_id)
        if missing:
            raise ParallelDispatchError(
                f"assignment {item.assignment_id} has unknown dependencies"
            )
    pending = set(by_id)
    ordered: list[ParallelAssignment] = []
    emitted: set[str] = set()
    while pending:
        ready = sorted(
            (by_id[item_id] for item_id in pending if set(by_id[item_id].dependencies) <= emitted),
            key=lambda item: (item.sequence, item.assignment_id),
        )
        if not ready:
            raise ParallelDispatchError("assignment dependencies contain a cycle")
        for item in ready:
            pending.remove(item.assignment_id)
            emitted.add(item.assignment_id)
            ordered.append(item)
    return tuple(ordered)


class PostgresParallelDispatch:
    """Durable dependency scheduler bound to one factory-created control plane."""

    def __init__(self, store: PostgresConditionalClaimStore) -> None:
        if not store.is_factory_bound_control_plane():
            raise ParallelDispatchError("parallel dispatch requires the Postgres control plane")
        self._store = store
        self._pool = store._pool  # same-package factory-bound capability
        self._schema = store._schema

    @staticmethod
    def _row(connection: Any, statement: str, parameters: Mapping[str, Any]) -> Any:
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchone()

    def _writer(
        self,
        connection: Any,
        *,
        task_id: str,
        owner_token: str,
        fencing_generation: int,
    ) -> Mapping[str, Any]:
        row = self._row(
            connection,
            f"""
            SELECT task_id, owner_token, fencing_generation
            FROM {self._schema}.director_primary_task_writer
            WHERE task_id = %(task_id)s
              AND engine_owner = 'langgraph'
              AND owner_token = %(owner_token)s
              AND fencing_generation = %(generation)s
              AND lease_expires_at > now()
            FOR UPDATE
            """,
            {
                "task_id": task_id,
                "owner_token": owner_token,
                "generation": fencing_generation,
            },
        )
        if row is None:
            raise ParallelDispatchError("parallel mutation lacks current writer authority")
        return row

    def register_plan(
        self,
        *,
        task_id: str,
        plan_digest: str,
        assignments: Sequence[ParallelAssignment],
        policy: ParallelDispatchPolicy,
        writer: TaskWriterLease,
    ) -> str:
        """Persist one immutable validated DAG before any reservation."""

        _require_id(task_id, "task_id")
        _require_digest(plan_digest, "plan_digest")
        if writer.task_id != task_id or writer.engine_owner != "langgraph" or not writer.lease_live:
            raise ParallelDispatchError("writer does not authorize this parallel plan")
        ordered = validate_assignment_dag(assignments)
        plan = _digest(
            {
                "task_id": task_id,
                "plan_digest": plan_digest,
                "assignments": [
                    {
                        "assignment_id": item.assignment_id,
                        "sequence": item.sequence,
                        "provider_id": item.provider_id,
                        "deployment_id": item.deployment_id,
                        "dependencies": list(item.dependencies),
                        "write_surfaces": list(item.write_surfaces),
                        "retry_limit": item.retry_limit,
                    }
                    for item in ordered
                ],
            }
        )
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                self._writer(
                    connection,
                    task_id=task_id,
                    owner_token=writer.owner_token,
                    fencing_generation=writer.fencing_generation,
                )
                held = self._row(
                    connection,
                    f"SELECT plan_digest FROM {schema}.director_primary_parallel_batch "
                    "WHERE task_id = %(task_id)s",
                    {"task_id": task_id},
                )
                if held is not None:
                    if str(held["plan_digest"]) != plan:
                        raise ParallelDispatchError("parallel plan identity is immutable")
                    return plan
                self._row(
                    connection,
                    f"""
                    INSERT INTO {schema}.director_primary_parallel_batch (
                        task_id, plan_digest, owner_token, fencing_generation,
                        max_concurrency, scale_policy_evidence_digest
                    ) VALUES (
                        %(task_id)s, %(plan)s, %(owner_token)s, %(generation)s,
                        %(max_concurrency)s, %(scale_evidence)s
                    ) RETURNING task_id
                    """,
                    {
                        "task_id": task_id,
                        "plan": plan,
                        "owner_token": writer.owner_token,
                        "generation": writer.fencing_generation,
                        "max_concurrency": policy.max_concurrency,
                        "scale_evidence": policy.scale_policy_evidence_digest,
                    },
                )
                for item in ordered:
                    self._row(
                        connection,
                        f"""
                        INSERT INTO {schema}.director_primary_parallel_assignment (
                            task_id, assignment_id, sequence, provider_id,
                            deployment_id, dependencies, write_surfaces,
                            retry_limit, status
                        ) VALUES (
                            %(task_id)s, %(assignment_id)s, %(sequence)s,
                            %(provider_id)s, %(deployment_id)s,
                            %(dependencies)s::jsonb, %(write_surfaces)s::jsonb,
                            %(retry_limit)s, 'PENDING'
                        ) RETURNING assignment_id
                        """,
                        {
                            "task_id": task_id,
                            "assignment_id": item.assignment_id,
                            "sequence": item.sequence,
                            "provider_id": item.provider_id,
                            "deployment_id": item.deployment_id,
                            "dependencies": json.dumps(list(item.dependencies)),
                            "write_surfaces": json.dumps(list(item.write_surfaces)),
                            "retry_limit": item.retry_limit,
                        },
                    )
        return plan

    def engage_kill_switch(self, *, task_id: str, writer: TaskWriterLease) -> None:
        """Block new reservations without cancelling or freeing in-flight slots."""

        with self._pool.connection() as connection:
            with connection.transaction():
                self._writer(
                    connection,
                    task_id=task_id,
                    owner_token=writer.owner_token,
                    fencing_generation=writer.fencing_generation,
                )
                self._row(
                    connection,
                    f"""
                    UPDATE {self._schema}.director_primary_parallel_batch
                    SET kill_switch = true, updated_at = now()
                    WHERE task_id = %(task_id)s
                    RETURNING task_id
                    """,
                    {"task_id": task_id},
                )

    def reserve_ready(
        self,
        *,
        task_id: str,
        writer: TaskWriterLease,
        policy: ParallelDispatchPolicy,
    ) -> tuple[ParallelReservation, ...]:
        """Atomically reserve every dependency-ready, cap-safe assignment."""

        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                self._writer(
                    connection,
                    task_id=task_id,
                    owner_token=writer.owner_token,
                    fencing_generation=writer.fencing_generation,
                )
                self._row(
                    connection,
                    "SELECT pg_advisory_xact_lock(hashtextextended(%(key)s, 0))",
                    {"key": f"director-primary-parallel:{schema}:{task_id}"},
                )
                batch = self._row(
                    connection,
                    f"""
                    SELECT max_concurrency, scale_policy_evidence_digest, kill_switch
                    FROM {schema}.director_primary_parallel_batch
                    WHERE task_id = %(task_id)s FOR UPDATE
                    """,
                    {"task_id": task_id},
                )
                if batch is None:
                    raise ParallelDispatchError("parallel plan is not registered")
                if bool(batch["kill_switch"]):
                    return ()
                if int(batch["max_concurrency"]) != policy.max_concurrency:
                    raise ParallelDispatchError("parallel concurrency policy changed")
                if (batch["scale_policy_evidence_digest"] or None) != (
                    policy.scale_policy_evidence_digest or None
                ):
                    raise ParallelDispatchError("parallel scale evidence changed")
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT assignment_id, sequence, provider_id, deployment_id,
                               dependencies, write_surfaces, status
                        FROM {schema}.director_primary_parallel_assignment
                        WHERE task_id = %(task_id)s
                        ORDER BY sequence, assignment_id
                        FOR UPDATE
                        """,
                        {"task_id": task_id},
                    )
                    rows = list(cursor.fetchall())
                status = {str(row["assignment_id"]): str(row["status"]) for row in rows}
                occupied = [
                    row for row in rows if str(row["status"]) in {"ACTIVE", "AMBIGUOUS"}
                ]
                provider_use: dict[str, int] = {}
                deployment_use: dict[str, int] = {}
                active_surfaces: list[str] = []
                for row in occupied:
                    provider = str(row["provider_id"])
                    deployment = str(row["deployment_id"])
                    provider_use[provider] = provider_use.get(provider, 0) + 1
                    deployment_use[deployment] = deployment_use.get(deployment, 0) + 1
                    active_surfaces.extend(
                        canonical_write_surface(item) for item in row["write_surfaces"]
                    )
                available = policy.max_concurrency - len(occupied)
                reserved: list[ParallelReservation] = []
                for row in rows:
                    if available <= 0 or str(row["status"]) != "PENDING":
                        continue
                    dependencies = tuple(row["dependencies"])
                    if any(status.get(item) == "FAILED" for item in dependencies):
                        continue
                    if not all(status.get(item) == "SUCCEEDED" for item in dependencies):
                        continue
                    provider = str(row["provider_id"])
                    deployment = str(row["deployment_id"])
                    if provider_use.get(provider, 0) >= (policy.provider_caps or {}).get(
                        provider, policy.max_concurrency
                    ):
                        continue
                    if deployment_use.get(deployment, 0) >= (
                        policy.deployment_caps or {}
                    ).get(deployment, policy.max_concurrency):
                        continue
                    surfaces = tuple(
                        canonical_write_surface(item) for item in row["write_surfaces"]
                    )
                    if write_surfaces_overlap(surfaces, active_surfaces):
                        continue
                    reservation = _digest(
                        {
                            "task_id": task_id,
                            "assignment_id": str(row["assignment_id"]),
                            "plan_generation": writer.fencing_generation,
                        }
                    )
                    updated = self._row(
                        connection,
                        f"""
                        UPDATE {schema}.director_primary_parallel_assignment
                        SET status = 'ACTIVE', reservation_digest = %(reservation)s,
                            reserved_at = now()
                        WHERE task_id = %(task_id)s
                          AND assignment_id = %(assignment_id)s
                          AND status = 'PENDING'
                        RETURNING assignment_id
                        """,
                        {
                            "task_id": task_id,
                            "assignment_id": str(row["assignment_id"]),
                            "reservation": reservation,
                        },
                    )
                    if updated is None:
                        raise ParallelDispatchError("parallel reservation lost its row lock")
                    reserved.append(
                        ParallelReservation(
                            task_id=task_id,
                            assignment_id=str(row["assignment_id"]),
                            sequence=int(row["sequence"]),
                            provider_id=provider,
                            deployment_id=deployment,
                            reservation_digest=reservation,
                        )
                    )
                    status[str(row["assignment_id"])] = "ACTIVE"
                    provider_use[provider] = provider_use.get(provider, 0) + 1
                    deployment_use[deployment] = deployment_use.get(deployment, 0) + 1
                    active_surfaces.extend(surfaces)
                    available -= 1
        return tuple(reserved)

    def record_result(
        self,
        *,
        reservation: ParallelReservation,
        writer: TaskWriterLease,
        status: ParallelAssignmentStatus,
        result_digest: str | None,
        reason_code: str,
    ) -> None:
        """Settle any reservation order; ambiguous outcomes keep their slot."""

        if status not in {
            ParallelAssignmentStatus.AMBIGUOUS,
            ParallelAssignmentStatus.SUCCEEDED,
            ParallelAssignmentStatus.FAILED,
        }:
            raise ParallelDispatchError("a reservation requires a terminal or ambiguous result")
        if status == ParallelAssignmentStatus.SUCCEEDED:
            _require_digest(result_digest or "", "result_digest")
        elif result_digest is not None:
            _require_digest(result_digest, "result_digest")
        if _CODE.fullmatch(reason_code) is None:
            raise ParallelDispatchError("reason_code is invalid")
        with self._pool.connection() as connection:
            with connection.transaction():
                self._writer(
                    connection,
                    task_id=reservation.task_id,
                    owner_token=writer.owner_token,
                    fencing_generation=writer.fencing_generation,
                )
                held = self._row(
                    connection,
                    f"""
                    SELECT status, reservation_digest, result_digest, reason_code
                    FROM {self._schema}.director_primary_parallel_assignment
                    WHERE task_id = %(task_id)s
                      AND assignment_id = %(assignment_id)s
                    FOR UPDATE
                    """,
                    {
                        "task_id": reservation.task_id,
                        "assignment_id": reservation.assignment_id,
                    },
                )
                if held is None or not hmac.compare_digest(
                    str(held["reservation_digest"] or ""), reservation.reservation_digest
                ):
                    raise ParallelDispatchError("parallel result binding mismatch")
                current = str(held["status"])
                if current in {"SUCCEEDED", "FAILED"}:
                    if (
                        current != status.value
                        or (held["result_digest"] or None) != result_digest
                        or str(held["reason_code"]) != reason_code
                    ):
                        raise ParallelDispatchError("parallel result conflicts with settlement")
                    return
                if current not in {"ACTIVE", "AMBIGUOUS"}:
                    raise ParallelDispatchError("parallel assignment is not reserved")
                if current == "AMBIGUOUS" and status == ParallelAssignmentStatus.AMBIGUOUS:
                    return
                self._row(
                    connection,
                    f"""
                    UPDATE {self._schema}.director_primary_parallel_assignment
                    SET status = %(status)s, result_digest = %(result_digest)s,
                        reason_code = %(reason_code)s,
                        completed_at = CASE WHEN %(status)s IN ('SUCCEEDED', 'FAILED')
                                            THEN now() ELSE NULL END
                    WHERE task_id = %(task_id)s
                      AND assignment_id = %(assignment_id)s
                    RETURNING assignment_id
                    """,
                    {
                        "status": status.value,
                        "result_digest": result_digest,
                        "reason_code": reason_code,
                        "task_id": reservation.task_id,
                        "assignment_id": reservation.assignment_id,
                    },
                )

    def execute_ready(
        self,
        *,
        task_id: str,
        writer: TaskWriterLease,
        policy: ParallelDispatchPolicy,
        dispatcher: Any,
    ) -> tuple[ParallelReservation, ...]:
        """Execute the reserved ready set concurrently and durably settle each.

        Reservations are committed before threads start. An exception after that
        boundary is classified as ambiguous and permanently retains its slot.
        """

        if not callable(dispatcher):
            raise ParallelDispatchError("dispatcher must be callable")
        reservations = self.reserve_ready(task_id=task_id, writer=writer, policy=policy)
        if not reservations:
            return ()

        def invoke(reservation: ParallelReservation) -> None:
            try:
                outcome = dispatcher(reservation)
                if not isinstance(outcome, ParallelDispatchOutcome):
                    raise TypeError("dispatcher returned the wrong result type")
            except BaseException:
                outcome = ParallelDispatchOutcome(
                    status=ParallelAssignmentStatus.AMBIGUOUS,
                    result_digest=None,
                    reason_code="provider_outcome_unknown",
                )
            self.record_result(
                reservation=reservation,
                writer=writer,
                status=outcome.status,
                result_digest=outcome.result_digest,
                reason_code=outcome.reason_code,
            )

        with ThreadPoolExecutor(
            max_workers=len(reservations),
            thread_name_prefix="director-primary-parallel",
        ) as executor:
            tuple(executor.map(invoke, reservations))
        return reservations

    def aggregate(self, task_id: str) -> ParallelAggregate:
        """Return a stable aggregation independent of completion arrival order."""

        with self._pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT assignment_id, dependencies, status, result_digest
                    FROM {self._schema}.director_primary_parallel_assignment
                    WHERE task_id = %(task_id)s
                    ORDER BY sequence, assignment_id
                    """,
                    {"task_id": task_id},
                )
                rows = list(cursor.fetchall())
        if not rows:
            raise ParallelDispatchError("parallel plan is unavailable")
        statuses = {str(row["assignment_id"]): str(row["status"]) for row in rows}
        projected = dict(statuses)
        for row in rows:
            if projected[str(row["assignment_id"])] == "PENDING" and any(
                statuses.get(item) == "FAILED" for item in row["dependencies"]
            ):
                projected[str(row["assignment_id"])] = "BLOCKED"
        results = {
            str(row["assignment_id"]): str(row["result_digest"])
            for row in rows
            if row["result_digest"] is not None
        }
        aggregate_digest = _digest(
            {
                "task_id": task_id,
                "statuses": sorted(projected.items()),
                "results": sorted(results.items()),
            }
        )
        terminal = all(value in {"SUCCEEDED", "FAILED", "BLOCKED"} for value in projected.values())
        return ParallelAggregate(
            task_id=task_id,
            statuses=projected,
            result_digests=results,
            aggregate_digest=aggregate_digest,
            terminal=terminal,
            succeeded=terminal and all(value == "SUCCEEDED" for value in projected.values()),
            ambiguous_slot_count=sum(value == "AMBIGUOUS" for value in projected.values()),
        )


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | END]]
