# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:28 UTC | BEGIN]]
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

from .primary_workload import WorkloadAdmissionHold, admit_assignment
from .allocation_profiles import allocation_targets, allocation_profile_digest

from .langgraph_postgres import (
    PostgresConditionalClaimStore,
    PostgresControlPlaneError,
    TaskWriterLease,
)


from .workload_policy import FailureKind, RetryRequest, Transport, evaluate_retry


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SAFE_SURFACE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ParallelDispatchError(RuntimeError):
    """The plan or durable dispatch state failed closed."""


class ProviderTransientDispatchError(RuntimeError):
    """Trusted temporary rejection known to occur before any paid dispatch.

    Never use for timeouts or errors with uncertain provider acceptance.
    """


PROVIDER_TRANSIENT_TRANSPORT_FAILURE = "provider_transient_transport_failure"
PROVIDER_RETRY_REQUIRES_TRANSIENT_FAILURE = "provider_retry_requires_transient_failure"
PROVIDER_RETRY_EVIDENCE_INSUFFICIENT = "provider_retry_evidence_insufficient"
PROVIDER_ATTEMPT_BUDGET_EXHAUSTED = "provider_attempt_budget_exhausted"

_TRANSPORT_BY_ID = {
    "api": Transport.PROVIDER_API,
    "responses-api": Transport.PROVIDER_API,
    "cli": Transport.LOCAL_CLI,
    "native-cli": Transport.LOCAL_CLI,
    "public-search-cli": Transport.LOCAL_CLI,
    # The authenticated bridge executes the same configured Windows command.
    # Its HTTP envelope is not a different underlying provider execution mode.
    "authenticated-windows-host-bridge": Transport.LOCAL_CLI,
    "browser": Transport.BROWSER,
    "packet-boundary-adapter": Transport.DIRECTOR_ADAPTER,
}


def provider_transport_kind(transport_id: str) -> Transport:
    """Map a roster transport id to the shared workload transport vocabulary."""

    try:
        return _TRANSPORT_BY_ID[transport_id]
    except KeyError as exc:
        raise ParallelDispatchError("provider_retry_transport_unknown") from exc


def classify_provider_dispatch_exception(exc: BaseException) -> FailureKind:
    """Classify dispatch exceptions without treating provider prose as authority."""

    if isinstance(exc, ProviderTransientDispatchError):
        return FailureKind.TRANSIENT
    return FailureKind.AMBIGUOUS_PAID_DISPATCH


def classify_provider_result_failure(result: Mapping[str, Any]) -> FailureKind:
    """Classify authenticated packet results; non-success is terminal."""

    if result.get("status") == "SUCCEEDED":
        return FailureKind.NONE
    return FailureKind.TERMINAL


def authorize_provider_retry(
    *,
    attempt_number: int,
    previous_failure: FailureKind,
    transport_id: str,
    previous_transport_id: str | None,
) -> tuple[bool, str]:
    """Fail closed unless typed evidence authorizes the next roster slot."""

    if attempt_number <= 1:
        return False, "primary_attempt_has_no_recovery_predecessor"
    if previous_failure is FailureKind.AMBIGUOUS_PAID_DISPATCH:
        return False, "ambiguous_paid_dispatch_must_not_be_repeated"
    if previous_failure is not FailureKind.TRANSIENT:
        return False, PROVIDER_RETRY_REQUIRES_TRANSIENT_FAILURE
    try:
        transport = provider_transport_kind(transport_id)
        previous_transport = (
            provider_transport_kind(previous_transport_id)
            if previous_transport_id is not None
            else None
        )
    except ParallelDispatchError:
        return False, "provider_retry_transport_unknown"
    transport_changed = (
        previous_transport is not None and transport != previous_transport
    )
    decision = evaluate_retry(
        RetryRequest(
            attempt_number=attempt_number,
            retry_limit=3,
            previous_failure=previous_failure,
            transport=transport,
            previous_transport=previous_transport,
            materially_new_information=False,
            corrected_approach=False,
            positive_expected_information_gain=transport_changed,
        )
    )
    if not decision.allowed:
        code = (
            decision.violations[0]
            if decision.violations
            else "provider_retry_not_authorized"
        )
        return False, code
    # Browser priority ranks transport preference; it does not invalidate
    # otherwise sufficient retry evidence. Every other advisory stays closed.
    if any(advisory != "browser_transport_enabled_at_lowest_priority"
           for advisory in decision.advisories):
        return False, PROVIDER_RETRY_EVIDENCE_INSUFFICIENT
    return True, "provider_retry_authorized"


class ParallelAssignmentStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    AMBIGUOUS = "AMBIGUOUS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OWNER_DECISION_REQUIRED = "OWNER_DECISION_REQUIRED"


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
    retry_limit: int = 3
    allocation_profile: str = "engineering"

    def __post_init__(self) -> None:
        allocation_targets(self.allocation_profile)
        _require_id(self.assignment_id, "assignment_id")
        _require_id(self.provider_id, "provider_id")
        _require_id(self.deployment_id, "deployment_id")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ParallelDispatchError("sequence must be a positive integer")
        if type(self.retry_limit) is not int or self.retry_limit != 3:
            raise ParallelDispatchError("retry_limit must be exactly 3")
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
    simultaneous_provider_roster: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 20:
            raise ParallelDispatchError("max concurrency must be between 1 and 20")
        roster = self.simultaneous_provider_roster
        if self.max_concurrency > 3:
            if (
                len(roster) != self.max_concurrency
                or len(set(roster)) != len(roster)
                or any(not provider.strip() for provider in roster)
                or any((self.provider_caps or {}).get(provider) != 1 for provider in roster)
            ):
                raise ParallelDispatchError(
                    "concurrency above three requires an exact one-slot provider roster"
                )
            if self.scale_policy_evidence_digest is not None:
                raise ParallelDispatchError(
                    "all-provider first-wave concurrency cannot use adaptive scale evidence"
                )
        elif roster:
            raise ParallelDispatchError(
                "simultaneous provider roster is reserved for concurrency above three"
            )
        elif self.max_concurrency == 3:
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
    workload_hold_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParallelDispatchOutcome:
    status: ParallelAssignmentStatus
    result_digest: str | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class ProviderAttemptReservation:
    """One immutable, writer-fenced child attempt under an assignment."""

    task_id: str
    assignment_id: str
    attempt_ordinal: int
    provider_id: str
    format_id: str
    transport_id: str
    idempotency_key_digest: str
    attempt_digest: str
    budget_evidence_digest: str


def deterministic_format_roster(formats: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Canonicalize configured format/transport pairs and fail closed above three."""

    roster = tuple(sorted(set(formats)))
    if not roster:
        raise ParallelDispatchError("provider format roster is empty")
    if len(roster) > 3:
        raise ParallelDispatchError("provider format roster exceeds three-attempt budget")
    for format_id, transport_id in roster:
        if _CODE.fullmatch(format_id) is None or _CODE.fullmatch(transport_id) is None:
            raise ParallelDispatchError("provider format roster contains an invalid identity")
    return roster


def format_for_attempt(
    roster: Sequence[tuple[str, str]], attempt_ordinal: int
) -> tuple[str, str]:
    """Use every configured format once before deterministic reuse."""

    canonical = deterministic_format_roster(roster)
    if type(attempt_ordinal) is not int or not 1 <= attempt_ordinal <= 3:
        raise ParallelDispatchError("attempt ordinal must be between one and three")
    return canonical[(attempt_ordinal - 1) % len(canonical)]


def validate_assignment_dag(
    assignments: Sequence[ParallelAssignment],
) -> tuple[ParallelAssignment, ...]:
    """Return a deterministic topological order or reject the plan."""

    if not assignments:
        raise ParallelDispatchError("parallel plans require at least one assignment")
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
                        "allocation_profile": item.allocation_profile,
                        "allocation_profile_digest": allocation_profile_digest(item.allocation_profile),
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
                            retry_limit, allocation_profile, status
                        ) VALUES (
                            %(task_id)s, %(assignment_id)s, %(sequence)s,
                            %(provider_id)s, %(deployment_id)s,
                            %(dependencies)s::jsonb, %(write_surfaces)s::jsonb,
                            %(retry_limit)s, %(allocation_profile)s, 'PENDING'
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
                        "allocation_profile": item.allocation_profile,
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

    def reserve_provider_attempt(
        self,
        *,
        reservation: ParallelReservation,
        writer: TaskWriterLease,
        attempt_ordinal: int,
        format_roster: Sequence[tuple[str, str]],
        idempotency_key_digest: str,
        budget_evidence_digest: str,
        team_admission=None,
        budget_admission=None,
        budget_limits=None,
    ) -> ProviderAttemptReservation:
        """Append one STARTED child event before I/O; never reuse an open attempt."""

        format_id, transport_id = format_for_attempt(format_roster, attempt_ordinal)
        if budget_limits is not None:
            from .antigravity_team import ProviderAttemptBudget
            if not isinstance(budget_limits, ProviderAttemptBudget):
                raise ParallelDispatchError("invalid provider attempt budget ceilings")
            if team_admission is None and budget_admission is None:
                raise ParallelDispatchError("paid provider attempt requires authoritative monetary admission")
        if team_admission is not None and budget_admission is not None:
            raise ParallelDispatchError("team admission already includes monetary authority")
        if team_admission is not None:
            budget_evidence_digest = "sha256:" + team_admission.grant_digest
        elif budget_admission is not None:
            budget_evidence_digest = "sha256:" + budget_admission.authority_digest
        _require_digest(idempotency_key_digest, "idempotency_key_digest")
        _require_digest(budget_evidence_digest, "budget_evidence_digest")
        attempt_digest = _digest(
            {
                "task_id": reservation.task_id,
                "assignment_id": reservation.assignment_id,
                "attempt_ordinal": attempt_ordinal,
                "provider_id": reservation.provider_id,
                "format_id": format_id,
                "transport_id": transport_id,
                "idempotency_key_digest": idempotency_key_digest,
                "budget_evidence_digest": budget_evidence_digest,
            }
        )
        schema = self._schema
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
                    SELECT provider_id, status, attempt_digest, reason_code, transport_id
                    FROM {schema}.director_primary_provider_attempt
                    WHERE task_id = %(task_id)s
                      AND assignment_id = %(assignment_id)s
                    ORDER BY attempt_ordinal DESC, event_sequence DESC
                    LIMIT 1 FOR UPDATE
                    """,
                    {
                        "task_id": reservation.task_id,
                        "assignment_id": reservation.assignment_id,
                    },
                )
                if held is not None:
                    if str(held["provider_id"]) != reservation.provider_id:
                        raise ParallelDispatchError("provider attempt identity changed")
                    if str(held["status"]) == "SUCCEEDED":
                        raise ParallelDispatchError("successful provider attempt must not be repeated")
                    if str(held["status"]) == "STARTED":
                        raise ParallelDispatchError(
                            "provider attempt reconciliation required before redispatch"
                        )
                    if str(held["status"]) == "AMBIGUOUS":
                        raise ParallelDispatchError(
                            "ambiguous provider attempt must reconcile before redispatch"
                        )
                    with connection.cursor() as cursor:
                        cursor.execute(
                            f"""
                            SELECT max(attempt_ordinal) AS ordinal
                            FROM {schema}.director_primary_provider_attempt
                            WHERE task_id = %(task_id)s
                              AND assignment_id = %(assignment_id)s
                            """,
                            {
                                "task_id": reservation.task_id,
                                "assignment_id": reservation.assignment_id,
                            },
                        )
                        maximum = int(cursor.fetchone()["ordinal"])
                    if attempt_ordinal != maximum + 1:
                        raise ParallelDispatchError("provider attempt ordinal is not next")
                    previous_failure = (
                        FailureKind.TRANSIENT
                        if str(held["status"]) == "FAILED"
                        and str(held["reason_code"]) == PROVIDER_TRANSIENT_TRANSPORT_FAILURE
                        else FailureKind.TERMINAL
                    )
                    authorized, reason = authorize_provider_retry(
                        attempt_number=attempt_ordinal,
                        previous_failure=previous_failure,
                        transport_id=transport_id,
                        previous_transport_id=str(held["transport_id"]),
                    )
                    if not authorized:
                        raise ParallelDispatchError(reason)
                elif attempt_ordinal != 1:
                    raise ParallelDispatchError("first provider attempt ordinal must be one")
                binding = self._row(
                    connection,
                    f"""SELECT provider_id, reservation_digest, status, allocation_profile
                    FROM {schema}.director_primary_parallel_assignment
                    WHERE task_id = %(task_id)s AND assignment_id = %(assignment_id)s
                    FOR UPDATE""",
                    {"task_id": reservation.task_id,
                     "assignment_id": reservation.assignment_id},
                )
                if (binding is None or binding["status"] != "ACTIVE"
                        or binding["provider_id"] != reservation.provider_id
                        or binding["reservation_digest"] != reservation.reservation_digest):
                    raise ParallelDispatchError("workload parallel reservation binding changed")
                monetary_clock = None
                admitted_grant = None
                if team_admission is not None:
                    from .antigravity_team import TeamAttemptAdmission
                    if not isinstance(team_admission, TeamAttemptAdmission):
                        raise ParallelDispatchError("invalid team admission authority")
                    monetary_clock = team_admission.authority.entitlements._clock
                    from datetime import timezone
                    admission_day = monetary_clock().astimezone(timezone.utc).date()
                    admitted_grant = team_admission.admit(connection, task_id=reservation.task_id,
                        assignment_id=reservation.assignment_id, provider=reservation.provider_id,
                        attempt_digest=attempt_digest, limits=budget_limits)
                elif format_id == "antigravity-team-engineering-v1":
                    raise ParallelDispatchError("team contract requires atomic entitlement admission")
                if budget_admission is not None:
                    from .antigravity_team import ProviderBudgetAdmission
                    if not isinstance(budget_admission,ProviderBudgetAdmission):
                        raise ParallelDispatchError("invalid provider budget authority")
                    monetary_clock = budget_admission.authority.entitlements._clock
                    from datetime import timezone
                    admission_day = monetary_clock().astimezone(timezone.utc).date()
                    budget_admission.admit(connection,task_id=reservation.task_id,
                        provider=reservation.provider_id,attempt_digest=attempt_digest,limits=budget_limits)
                admit_assignment(
                    connection, schema=schema, task_id=reservation.task_id,
                    assignment_id=reservation.assignment_id,
                    provider=reservation.provider_id,
                    reservation_digest=reservation.reservation_digest,
                    fencing_generation=writer.fencing_generation,
                    allocation_profile=binding["allocation_profile"],
                )
                # Monetary and workload locks can each wait across UTC midnight.
                # Expired authority must roll back even if its spend insert ran
                # before a later lock wait. Never leave a STARTED attempt behind.
                if monetary_clock is not None:
                    from .antigravity_team import TeamAuthorizationError
                    admitted_at = monetary_clock()
                    if admitted_at.astimezone(timezone.utc).date() != admission_day:
                        raise TeamAuthorizationError("provider budget expired during atomic admission")
                    if admitted_grant is not None:
                        admitted_grant.verify(team_admission.authority.entitlements._owner_authority_secret, admitted_at)
                row = self._row(
                    connection,
                    f"""
                    INSERT INTO {schema}.director_primary_provider_attempt (
                        task_id, assignment_id, attempt_ordinal, event_sequence,
                        provider_id, format_id, transport_id,
                        idempotency_key_digest, attempt_digest, status,
                        reason_code, budget_evidence_digest
                    ) VALUES (
                        %(task_id)s, %(assignment_id)s, %(ordinal)s, 1,
                        %(provider_id)s, %(format_id)s, %(transport_id)s,
                        %(idempotency)s, %(attempt)s, 'STARTED',
                        'provider_attempt_started', %(budget)s
                    ) ON CONFLICT DO NOTHING RETURNING attempt_digest
                    """,
                    {
                        "task_id": reservation.task_id,
                        "assignment_id": reservation.assignment_id,
                        "ordinal": attempt_ordinal,
                        "provider_id": reservation.provider_id,
                        "format_id": format_id,
                        "transport_id": transport_id,
                        "idempotency": idempotency_key_digest,
                        "attempt": attempt_digest,
                        "budget": budget_evidence_digest,
                    },
                )
                if row is None:
                    raise ParallelDispatchError("provider attempt identity already exists")
        return ProviderAttemptReservation(
            reservation.task_id,
            reservation.assignment_id,
            attempt_ordinal,
            reservation.provider_id,
            format_id,
            transport_id,
            idempotency_key_digest,
            attempt_digest,
            budget_evidence_digest,
        )

    def record_provider_attempt(
        self,
        *,
        attempt: ProviderAttemptReservation,
        writer: TaskWriterLease,
        status: ParallelAssignmentStatus,
        result_digest: str | None,
        reason_code: str,
    ) -> None:
        """Append the immutable outcome for one exact child attempt."""

        if status not in {
            ParallelAssignmentStatus.SUCCEEDED,
            ParallelAssignmentStatus.FAILED,
            ParallelAssignmentStatus.AMBIGUOUS,
        }:
            raise ParallelDispatchError("provider attempt outcome is invalid")
        if result_digest is not None:
            _require_digest(result_digest, "result_digest")
        if _CODE.fullmatch(reason_code) is None:
            raise ParallelDispatchError("reason_code is invalid")
        outcome_evidence_digest = _digest(
            {
                "attempt_digest": attempt.attempt_digest,
                "task_id": attempt.task_id,
                "assignment_id": attempt.assignment_id,
                "attempt_ordinal": attempt.attempt_ordinal,
                "provider_id": attempt.provider_id,
                "format_id": attempt.format_id,
                "transport_id": attempt.transport_id,
                "idempotency_key_digest": attempt.idempotency_key_digest,
                "budget_evidence_digest": attempt.budget_evidence_digest,
                "status": status.value,
                "result_digest": result_digest,
                "reason_code": reason_code,
            }
        )
        with self._pool.connection() as connection:
            with connection.transaction():
                self._writer(
                    connection,
                    task_id=attempt.task_id,
                    owner_token=writer.owner_token,
                    fencing_generation=writer.fencing_generation,
                )
                row = self._row(
                    connection,
                    f"""
                    INSERT INTO {self._schema}.director_primary_provider_attempt (
                        task_id, assignment_id, attempt_ordinal, event_sequence,
                        provider_id, format_id, transport_id,
                        idempotency_key_digest, attempt_digest, status,
                        result_digest, outcome_evidence_digest, reason_code,
                        budget_evidence_digest
                    ) VALUES (
                        %(task_id)s, %(assignment_id)s, %(ordinal)s, 2,
                        %(provider_id)s, %(format_id)s, %(transport_id)s,
                        %(idempotency)s, %(attempt)s, %(status)s,
                        %(result)s, %(outcome_evidence)s, %(reason)s, %(budget)s
                    ) ON CONFLICT DO NOTHING RETURNING attempt_digest
                    """,
                    {
                        "task_id": attempt.task_id,
                        "assignment_id": attempt.assignment_id,
                        "ordinal": attempt.attempt_ordinal,
                        "provider_id": attempt.provider_id,
                        "format_id": attempt.format_id,
                        "transport_id": attempt.transport_id,
                        "idempotency": attempt.idempotency_key_digest,
                        "attempt": attempt.attempt_digest,
                        "status": status.value,
                        "result": result_digest,
                        "outcome_evidence": outcome_evidence_digest,
                        "reason": reason_code,
                        "budget": attempt.budget_evidence_digest,
                    },
                )
                if row is None:
                    raise ParallelDispatchError("provider attempt outcome already recorded")

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
            ParallelAssignmentStatus.OWNER_DECISION_REQUIRED,
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
                if current in {"SUCCEEDED", "FAILED", "OWNER_DECISION_REQUIRED"}:
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
                        completed_at = CASE WHEN %(status)s IN ('SUCCEEDED', 'FAILED', 'OWNER_DECISION_REQUIRED')
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

    def reservation_for_callback(
        self,
        *,
        task_id: str,
        assignment_id: str,
        provider_id: str,
        attempt_digest: str,
        writer: TaskWriterLease,
    ) -> ParallelReservation:
        """Resolve an authenticated callback only to its exact ledger slot."""

        _require_digest(attempt_digest, "attempt_digest")
        with self._pool.connection() as connection:
            with connection.transaction():
                self._writer(
                    connection,
                    task_id=task_id,
                    owner_token=writer.owner_token,
                    fencing_generation=writer.fencing_generation,
                )
                row = self._row(
                    connection,
                    f"""
                    SELECT a.sequence, a.provider_id, a.deployment_id,
                           a.reservation_digest, a.status
                    FROM {self._schema}.director_primary_parallel_assignment a
                    JOIN {self._schema}.director_primary_provider_attempt p
                      ON p.task_id = a.task_id
                     AND p.assignment_id = a.assignment_id
                    WHERE a.task_id = %(task_id)s
                      AND a.assignment_id = %(assignment_id)s
                      AND p.attempt_digest = %(attempt_digest)s
                    ORDER BY p.event_sequence DESC
                    LIMIT 1 FOR UPDATE OF a
                    """,
                    {
                        "task_id": task_id,
                        "assignment_id": assignment_id,
                        "attempt_digest": attempt_digest,
                    },
                )
                if row is None:
                    raise ParallelDispatchError("callback attempt binding is unknown")
                if str(row["provider_id"]) != provider_id:
                    raise ParallelDispatchError("callback provider binding changed")
                if str(row["status"]) not in {"ACTIVE", "AMBIGUOUS"}:
                    raise ParallelDispatchError("callback assignment is already settled")
                reservation_digest = str(row["reservation_digest"] or "")
                _require_digest(reservation_digest, "reservation_digest")
                return ParallelReservation(
                    task_id=task_id,
                    assignment_id=assignment_id,
                    sequence=int(row["sequence"]),
                    provider_id=provider_id,
                    deployment_id=str(row["deployment_id"]),
                    reservation_digest=reservation_digest,
                )

    def record_callback_result(
        self,
        *,
        task_id: str,
        assignment_id: str,
        provider_id: str,
        attempt_digest: str,
        provider_run_id_digest: str,
        writer: TaskWriterLease,
        status: ParallelAssignmentStatus,
        result_digest: str | None,
        reason_code: str,
    ) -> ParallelReservation:
        """Atomically append an exact run-bound callback and settle its slot."""

        _require_digest(attempt_digest, "attempt_digest")
        _require_digest(provider_run_id_digest, "provider_run_id_digest")
        if status not in {
            ParallelAssignmentStatus.SUCCEEDED,
            ParallelAssignmentStatus.FAILED,
            ParallelAssignmentStatus.AMBIGUOUS,
        }:
            raise ParallelDispatchError("callback outcome is invalid")
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
                    task_id=task_id,
                    owner_token=writer.owner_token,
                    fencing_generation=writer.fencing_generation,
                )
                row = self._row(
                    connection,
                    f"""
                    SELECT a.sequence, a.provider_id, a.deployment_id,
                           a.reservation_digest, a.status AS assignment_status,
                           p.attempt_ordinal, p.format_id, p.transport_id,
                           p.idempotency_key_digest, p.budget_evidence_digest
                    FROM {self._schema}.director_primary_parallel_assignment a
                    JOIN {self._schema}.director_primary_provider_attempt p
                      ON p.task_id = a.task_id
                     AND p.assignment_id = a.assignment_id
                     AND p.event_sequence = 1
                    WHERE a.task_id = %(task_id)s
                      AND a.assignment_id = %(assignment_id)s
                      AND p.attempt_digest = %(attempt_digest)s
                    FOR UPDATE OF a
                    """,
                    {
                        "task_id": task_id,
                        "assignment_id": assignment_id,
                        "attempt_digest": attempt_digest,
                    },
                )
                if row is None:
                    raise ParallelDispatchError("callback attempt binding is unknown")
                if str(row["provider_id"]) != provider_id:
                    raise ParallelDispatchError("callback provider binding changed")
                existing = self._row(
                    connection,
                    f"""
                    SELECT provider_run_id_digest, status, result_digest, reason_code
                    FROM {self._schema}.director_primary_provider_attempt
                    WHERE task_id = %(task_id)s
                      AND assignment_id = %(assignment_id)s
                      AND attempt_ordinal = %(attempt_ordinal)s
                      AND event_sequence = 3
                    """,
                    {
                        "task_id": task_id,
                        "assignment_id": assignment_id,
                        "attempt_ordinal": int(row["attempt_ordinal"]),
                    },
                )
                if existing is not None:
                    if not (
                        hmac.compare_digest(
                            str(existing["provider_run_id_digest"] or ""),
                            provider_run_id_digest,
                        )
                        and str(existing["status"]) == status.value
                        and (existing["result_digest"] or None) == result_digest
                        and str(existing["reason_code"]) == reason_code
                    ):
                        raise ParallelDispatchError("callback run identity conflicts")
                else:
                    if str(row["assignment_status"]) not in {"ACTIVE", "AMBIGUOUS"}:
                        raise ParallelDispatchError("callback assignment is already settled")
                    outcome_evidence_digest = _digest(
                        {
                            "task_id": task_id,
                            "assignment_id": assignment_id,
                            "attempt_ordinal": int(row["attempt_ordinal"]),
                            "provider_id": provider_id,
                            "attempt_digest": attempt_digest,
                            "provider_run_id_digest": provider_run_id_digest,
                            "status": status.value,
                            "result_digest": result_digest,
                            "reason_code": reason_code,
                        }
                    )
                    self._row(
                        connection,
                        f"""
                        INSERT INTO {self._schema}.director_primary_provider_attempt (
                            task_id, assignment_id, attempt_ordinal, event_sequence,
                            provider_id, format_id, transport_id,
                            idempotency_key_digest, attempt_digest,
                            provider_run_id_digest, status, result_digest,
                            outcome_evidence_digest, reason_code,
                            budget_evidence_digest
                        ) VALUES (
                            %(task_id)s, %(assignment_id)s, %(attempt_ordinal)s, 3,
                            %(provider_id)s, %(format_id)s, %(transport_id)s,
                            %(idempotency)s, %(attempt_digest)s,
                            %(provider_run_id_digest)s, %(status)s, %(result_digest)s,
                            %(outcome_evidence_digest)s, %(reason_code)s,
                            %(budget_evidence_digest)s
                        ) RETURNING attempt_digest
                        """,
                        {
                            "task_id": task_id,
                            "assignment_id": assignment_id,
                            "attempt_ordinal": int(row["attempt_ordinal"]),
                            "provider_id": provider_id,
                            "format_id": str(row["format_id"]),
                            "transport_id": str(row["transport_id"]),
                            "idempotency": str(row["idempotency_key_digest"]),
                            "attempt_digest": attempt_digest,
                            "provider_run_id_digest": provider_run_id_digest,
                            "status": status.value,
                            "result_digest": result_digest,
                            "outcome_evidence_digest": outcome_evidence_digest,
                            "reason_code": reason_code,
                            "budget_evidence_digest": str(row["budget_evidence_digest"]),
                        },
                    )
                    self._row(
                        connection,
                        f"""
                        UPDATE {self._schema}.director_primary_parallel_assignment
                        SET status = %(status)s, result_digest = %(result_digest)s,
                            reason_code = %(reason_code)s,
                            completed_at = CASE
                                WHEN %(status)s IN ('SUCCEEDED', 'FAILED') THEN now()
                                ELSE NULL END
                        WHERE task_id = %(task_id)s
                          AND assignment_id = %(assignment_id)s
                        RETURNING assignment_id
                        """,
                        {
                            "status": status.value,
                            "result_digest": result_digest,
                            "reason_code": reason_code,
                            "task_id": task_id,
                            "assignment_id": assignment_id,
                        },
                    )
                reservation_digest = str(row["reservation_digest"] or "")
                _require_digest(reservation_digest, "reservation_digest")
                return ParallelReservation(
                    task_id=task_id,
                    assignment_id=assignment_id,
                    sequence=int(row["sequence"]),
                    provider_id=provider_id,
                    deployment_id=str(row["deployment_id"]),
                    reservation_digest=reservation_digest,
                )

    def reconcile_stranded_active(
        self, *, task_id: str, writer: TaskWriterLease
    ) -> int:
        """Classify restart-visible ACTIVE rows as ambiguous without dispatch."""

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
                    {"key": f"director-primary-parallel:{self._schema}:{task_id}"},
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        UPDATE {self._schema}.director_primary_parallel_assignment
                        SET status = 'AMBIGUOUS',
                            reason_code = 'provider_outcome_unknown'
                        WHERE task_id = %(task_id)s AND status = 'ACTIVE'
                        RETURNING assignment_id
                        """,
                        {"task_id": task_id},
                    )
                    return len(cursor.fetchall())

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
            except WorkloadAdmissionHold as hold:
                outcome = ParallelDispatchOutcome(
                    status=ParallelAssignmentStatus.OWNER_DECISION_REQUIRED,
                    result_digest=None,
                    reason_code=hold.reason_code,
                )
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
                    SELECT assignment_id, dependencies, status, result_digest, reason_code
                    FROM {self._schema}.director_primary_parallel_assignment
                    WHERE task_id = %(task_id)s
                    ORDER BY sequence, assignment_id
                    """,
                    {"task_id": task_id},
                )
                rows = list(cursor.fetchall())
                cursor.execute(
                    f"""
                    SELECT assignment_id, attempt_ordinal, event_sequence,
                           provider_id, format_id, transport_id,
                           idempotency_key_digest, attempt_digest,
                           provider_run_id_digest, status,
                           result_digest, outcome_evidence_digest,
                           reason_code, budget_evidence_digest
                    FROM {self._schema}.director_primary_provider_attempt
                    WHERE task_id = %(task_id)s
                    ORDER BY assignment_id, attempt_ordinal, event_sequence
                    """,
                    {"task_id": task_id},
                )
                attempt_rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            raise ParallelDispatchError("parallel plan is unavailable")
        statuses = {str(row["assignment_id"]): str(row["status"]) for row in rows}
        projected = dict(statuses)
        # Sequence is presentation order, not dependency order. The registered
        # DAG has at most len(rows)-1 edges on any path, so this fixed point is
        # bounded even when descendants precede their prerequisites in SQL.
        for _ in range(len(rows)):
            changed = False
            for row in rows:
                assignment_id = str(row["assignment_id"])
                if projected[assignment_id] == "PENDING" and any(
                    projected.get(item) in {"FAILED", "OWNER_DECISION_REQUIRED", "BLOCKED"}
                    for item in row["dependencies"]
                ):
                    projected[assignment_id] = "BLOCKED"
                    changed = True
            if not changed:
                break
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
                "attempt_events": attempt_rows,
                **({"workload_holds": sorted(
                    (str(row["assignment_id"]), str(row["reason_code"]))
                    for row in rows if row["status"] == "OWNER_DECISION_REQUIRED"
                )} if any(row["status"] == "OWNER_DECISION_REQUIRED" for row in rows) else {}),
            }
        )
        terminal = all(value in {"SUCCEEDED", "FAILED", "BLOCKED", "OWNER_DECISION_REQUIRED"} for value in projected.values())
        return ParallelAggregate(
            task_id=task_id,
            statuses=projected,
            result_digests=results,
            aggregate_digest=aggregate_digest,
            terminal=terminal,
            succeeded=terminal and all(value == "SUCCEEDED" for value in projected.values()),
            ambiguous_slot_count=sum(value == "AMBIGUOUS" for value in projected.values()),
            workload_hold_reasons=tuple(sorted({
                str(row["reason_code"]) for row in rows
                if row["status"] == "OWNER_DECISION_REQUIRED"
            })),
        )


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:18 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | END]]
