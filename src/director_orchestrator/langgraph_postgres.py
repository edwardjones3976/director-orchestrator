# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:08 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-31 | 15:05 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-31 | 12:40 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:35 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:28 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:18 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:35 UTC | BEGIN]]
"""Bounded Postgres control plane for the primary LangGraph runtime.

Three separate durable concerns, deliberately not conflated:

1. **Task writer lease** (``director_primary_task_writer``).  One row per task
   carrying an *immutable* ``engine_owner``, the *current* ``owner_token``, a
   strictly monotonic ``fencing_generation``, and a bounded lease that expires
   on the database clock.  Ownership moves only through
   :meth:`PostgresConditionalClaimStore.acquire_task_writer`, a
   compare-and-swap against an expected generation.  ``claim()`` never creates
   or transfers ownership.
2. **Insert-once claims** (``director_primary_claim``).  A claim is granted only
   by ``INSERT … SELECT`` from the current *unexpired* writer row whose
   ``owner_token`` matches the request, and it stores that exact generation.  A
   claim stamped with a superseded generation confers no authority afterwards.
3. **Effects** -- reservation, remote binding, reconciliation, terminal
   evidence, audit.  Every mutation joins the current unexpired writer row and
   requires the claim's ``owner_token`` *and* ``fencing_generation`` to match
   it, so a stale row can never authorize anything.

``AMBIGUOUS`` and ``RECONCILIATION_REQUIRED`` are **not** terminal evidence.
They are append-only reconciliation events that permanently block new
reservations for the claim while still permitting authenticated binding and
readback, and still permitting exactly one final terminal evidence row.
Terminal evidence records only final dispositions.

Connection-identity rules, all fail-closed: the DSN is accepted only at the
process boundary and never stored, logged, or surfaced; ``service``,
``servicefile``, ``passfile`` and ``options`` are refused so no part of the
target is resolved behind the identity's back; ``dbname``, ``port``, ``user``,
``sslmode`` and one of ``host``/``hostaddr`` must be stated inline.  The opaque
identity digests only the fields that select the physical database and the
explicit versioned schema.  ``sslmode`` must be ``verify-ca`` or ``verify-full``
with an explicit ``sslrootcert``; a loopback test may opt out only through
``allow_insecure_local_transport`` and must still state ``sslmode``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .langgraph_primary import (
    ClaimOutcome,
    ClaimRequest,
    EffectDisposition,
    EffectStatus,
    _require_actor_id,
    _require_code,
    _require_digest,
    _require_id,
)


class PostgresCheckpointConfigurationError(ValueError):
    """The production checkpointer configuration is absent or unsafe."""


class PostgresControlPlaneError(RuntimeError):
    """A durable control-plane operation refused, or could not be resolved.

    Never retryable on its own.  Callers in :mod:`.langgraph_primary` treat an
    exception from the claim store as a refusal and fail closed, so this is
    raised in preference to guessing -- and in preference to surfacing a raw
    unique-violation, which a caller might mistake for a transient conflict.
    """


CONTROL_PLANE_VERSION = "director-postgres-control-plane/v2"
STORAGE_IDENTITY_VERSION = "director-postgres-identity/v2"
DEFAULT_CONTROL_PLANE_SCHEMA = "director_primary_v2"

_FACTORY_PROVENANCE = object()

# A schema name is interpolated into DDL, so it is constrained to an allowlist
# that cannot express a quote, a dot, or whitespace, and must carry an explicit
# ``_v<N>`` suffix.  Nothing outside this pattern ever reaches SQL.
_SCHEMA_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,40}_v[0-9]{1,6}$")
_PORT_PATTERN = re.compile(r"^[0-9]{1,5}$")

_STORAGE_TARGET_IDENTITY_FIELDS = (
    "dbname",
    "host",
    "hostaddr",
    "port",
    "target_session_attrs",
)
_REQUIRED_CONNECTION_FIELDS = ("dbname", "port", "user", "sslmode")
_EXTERNALLY_RESOLVED_KEYS = ("service", "servicefile", "passfile")

_SECURE_SSLMODES = frozenset({"verify-ca", "verify-full"})
_KNOWN_SSLMODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Terminal evidence records only outcomes that are actually final.  Ambiguity is
# not an outcome, it is the absence of one, so it lives in the reconciliation
# log instead.
FINAL_DISPOSITIONS = tuple(
    sorted(
        member.value
        for member in EffectDisposition
        if member is not EffectDisposition.AMBIGUOUS
    )
)
RECONCILIATION_STATES = tuple(
    sorted(
        {
            EffectDisposition.AMBIGUOUS.value,
            EffectStatus.RECONCILIATION_REQUIRED.value,
        }
    )
)

DEFAULT_POOL_MIN_SIZE = 1
DEFAULT_POOL_MAX_SIZE = 8
MAX_POOL_SIZE = 64
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_POOL_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_IDLE_SECONDS = 300.0
DEFAULT_MAX_LIFETIME_SECONDS = 1800.0
MAX_TIMEOUT_SECONDS = 3600.0

MIN_LEASE_SECONDS = 5.0
DEFAULT_LEASE_SECONDS = 60.0
MAX_LEASE_SECONDS = 3600.0

INITIAL_FENCING_GENERATION = 0
"""The generation an acquirer states when it believes no writer row exists."""


@dataclass(frozen=True, slots=True)
class PostgresPoolBounds:
    """Explicit bounds for the one pool this process is allowed to open."""

    min_size: int = DEFAULT_POOL_MIN_SIZE
    max_size: int = DEFAULT_POOL_MAX_SIZE
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    pool_timeout_seconds: float = DEFAULT_POOL_TIMEOUT_SECONDS
    max_idle_seconds: float = DEFAULT_MAX_IDLE_SECONDS
    max_lifetime_seconds: float = DEFAULT_MAX_LIFETIME_SECONDS

    def __post_init__(self) -> None:
        for name in ("min_size", "max_size"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise PostgresCheckpointConfigurationError(
                    f"{name} must be a non-negative integer"
                )
        if self.max_size < 1 or self.max_size > MAX_POOL_SIZE:
            raise PostgresCheckpointConfigurationError(
                f"max_size must be between 1 and {MAX_POOL_SIZE}"
            )
        if self.min_size > self.max_size:
            raise PostgresCheckpointConfigurationError("min_size cannot exceed max_size")
        for name in (
            "connect_timeout_seconds",
            "pool_timeout_seconds",
            "max_idle_seconds",
            "max_lifetime_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise PostgresCheckpointConfigurationError(
                    f"{name} must be a positive number of seconds"
                )
            if not 0 < float(value) <= MAX_TIMEOUT_SECONDS:
                raise PostgresCheckpointConfigurationError(
                    f"{name} must be greater than 0 and at most {MAX_TIMEOUT_SECONDS}"
                )

    @property
    def libpq_connect_timeout(self) -> int:
        """Return the integer ``connect_timeout`` libpq accepts (minimum 2s)."""

        return max(2, int(round(self.connect_timeout_seconds)))


@dataclass(frozen=True, slots=True)
class _PostgresDependencies:
    dict_row: Any
    conninfo_to_dict: Callable[[str], Mapping[str, str]]
    pool_type: type[Any]
    saver_type: type[Any]
    serializer_type: type[Any]


def _load_dependencies() -> _PostgresDependencies:
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - exercised by deployment checks
        raise PostgresCheckpointConfigurationError(
            "langgraph-checkpoint-postgres, psycopg and psycopg-pool are required"
        ) from exc
    return _PostgresDependencies(
        dict_row=dict_row,
        conninfo_to_dict=conninfo_to_dict,
        pool_type=ConnectionPool,
        saver_type=PostgresSaver,
        serializer_type=JsonPlusSerializer,
    )


def _require_schema(schema: str) -> str:
    if not isinstance(schema, str) or _SCHEMA_PATTERN.fullmatch(schema) is None:
        raise PostgresCheckpointConfigurationError(
            "control-plane schema must be an explicit versioned name such as "
            f"{DEFAULT_CONTROL_PLANE_SCHEMA!r}"
        )
    return schema


def _require_units(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise PostgresControlPlaneError(f"{name} must be a non-negative integer")
    return value


def _require_generation(value: int, name: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise PostgresControlPlaneError(f"{name} must be an integer >= {minimum}")
    return value


def _require_lease_seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PostgresControlPlaneError("lease_seconds must be a number of seconds")
    seconds = float(value)
    if not MIN_LEASE_SECONDS <= seconds <= MAX_LEASE_SECONDS:
        raise PostgresControlPlaneError(
            f"lease_seconds must be between {MIN_LEASE_SECONDS} and "
            f"{MAX_LEASE_SECONDS}"
        )
    return seconds


def _parse_connection_target(
    dsn: str,
    *,
    conninfo_to_dict: Callable[[str], Mapping[str, str]],
    allow_insecure_local_transport: bool,
) -> dict[str, str]:
    """Parse and fully validate a DSN, or refuse it."""

    if not isinstance(dsn, str) or not dsn.strip():
        raise PostgresCheckpointConfigurationError(
            "LANGGRAPH_RUNTIME_POSTGRES_DSN must be configured"
        )
    if "\n" in dsn or "\r" in dsn:
        raise PostgresCheckpointConfigurationError(
            "LANGGRAPH_RUNTIME_POSTGRES_DSN cannot contain line breaks"
        )
    try:
        raw = dict(conninfo_to_dict(dsn))
    except Exception as exc:
        raise PostgresCheckpointConfigurationError(
            "LANGGRAPH_RUNTIME_POSTGRES_DSN is not valid Postgres connection info"
        ) from exc
    parsed = {
        str(key): str(value)
        for key, value in raw.items()
        if value is not None and str(value).strip()
    }
    for key in _EXTERNALLY_RESOLVED_KEYS:
        if key in parsed:
            raise PostgresCheckpointConfigurationError(
                f"LANGGRAPH_RUNTIME_POSTGRES_DSN must not delegate connection "
                f"identity to {key!r}; state every field inline"
            )
    if "options" in parsed:
        raise PostgresCheckpointConfigurationError(
            "LANGGRAPH_RUNTIME_POSTGRES_DSN must not set options; the control "
            "plane owns search_path"
        )
    for key in _REQUIRED_CONNECTION_FIELDS:
        if key not in parsed:
            raise PostgresCheckpointConfigurationError(
                f"LANGGRAPH_RUNTIME_POSTGRES_DSN must state {key!r} explicitly "
                "rather than inheriting it from the environment"
            )
    if "host" not in parsed and "hostaddr" not in parsed:
        raise PostgresCheckpointConfigurationError(
            "LANGGRAPH_RUNTIME_POSTGRES_DSN must state host or hostaddr "
            "explicitly rather than inheriting it from the environment"
        )
    if _PORT_PATTERN.fullmatch(parsed["port"]) is None or not (
        1 <= int(parsed["port"]) <= 65535
    ):
        raise PostgresCheckpointConfigurationError(
            "LANGGRAPH_RUNTIME_POSTGRES_DSN port must be a TCP port number"
        )
    _require_transport_security(
        parsed, allow_insecure_local_transport=allow_insecure_local_transport
    )
    return parsed


def _require_transport_security(
    parsed: Mapping[str, str], *, allow_insecure_local_transport: bool
) -> None:
    sslmode = parsed["sslmode"].strip().lower()
    if sslmode not in _KNOWN_SSLMODES:
        raise PostgresCheckpointConfigurationError(
            "LANGGRAPH_RUNTIME_POSTGRES_DSN sslmode is not a libpq sslmode"
        )
    if not allow_insecure_local_transport:
        if sslmode not in _SECURE_SSLMODES:
            raise PostgresCheckpointConfigurationError(
                "LANGGRAPH_RUNTIME_POSTGRES_DSN requires sslmode=verify-ca or "
                "verify-full; weaker modes do not authenticate the server"
            )
        if not parsed.get("sslrootcert", "").strip():
            raise PostgresCheckpointConfigurationError(
                "LANGGRAPH_RUNTIME_POSTGRES_DSN requires an explicit sslrootcert "
                "so server verification does not depend on a home directory"
            )
        return
    host = parsed.get("host", "").strip().lower()
    hostaddr = parsed.get("hostaddr", "").strip().lower()
    reachable = {value for value in (host, hostaddr) if value}
    if not reachable or not reachable.issubset(_LOOPBACK_HOSTS):
        raise PostgresCheckpointConfigurationError(
            "allow_insecure_local_transport applies only to a loopback host"
        )


def _storage_identity(parsed: Mapping[str, str], *, schema: str) -> str:
    """Return the opaque, canonical digest of one storage target."""

    target = {
        name: str(parsed[name])
        for name in _STORAGE_TARGET_IDENTITY_FIELDS
        if name in parsed and str(parsed[name]).strip()
    }
    if "dbname" not in target:
        raise PostgresCheckpointConfigurationError(
            "LANGGRAPH_RUNTIME_POSTGRES_DSN must name a dedicated database"
        )
    canonical = json.dumps(
        {
            "identity_version": STORAGE_IDENTITY_VERSION,
            "control_plane_version": CONTROL_PLANE_VERSION,
            "search_path_schema": _require_schema(schema),
            "target": target,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "postgres:sha256:" + hashlib.sha256(canonical).hexdigest()


def _advisory_key(*parts: str) -> int:
    """Return one stable signed 64-bit advisory-lock key."""

    raw = hashlib.sha256(
        "\x1f".join((CONTROL_PLANE_VERSION, *parts)).encode("utf-8")
    ).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


def _build_strict_serializer(serializer_type: type[Any]) -> Any:
    try:
        return serializer_type(
            pickle_fallback=False,
            allowed_json_modules=(),
            allowed_msgpack_modules=(),
        )
    except TypeError as exc:
        raise PostgresCheckpointConfigurationError(
            "checkpoint serializer signature is incompatible"
        ) from exc


def _search_path_option(schema: str) -> str:
    return f"-c search_path={_require_schema(schema)}"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


_APPEND_ONLY_TABLES = (
    "director_primary_claim",
    "director_primary_effect_reservation",
    "director_primary_remote_binding",
    "director_primary_reconciliation_event",
    "director_primary_terminal_evidence",
    "director_primary_audit_event",
)

_RATCHET_TABLES = (
    ("director_primary_task_writer", "director_primary_writer_guard_fn"),
    ("director_primary_budget", "director_primary_budget_ratchet"),
)


def _guard_triggers(schema: str) -> tuple[str, ...]:
    statements: list[str] = []
    guarded = [(table, "director_primary_append_only") for table in _APPEND_ONLY_TABLES]
    guarded.extend(_RATCHET_TABLES)
    for table, function in guarded:
        trigger = f"{table}_guard"
        statements.append(f"DROP TRIGGER IF EXISTS {trigger} ON {schema}.{table}")
        statements.append(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {schema}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {schema}.{function}()"
        )
    return tuple(statements)


def _control_plane_ddl(schema: str) -> tuple[str, ...]:
    """Return the ordered, idempotent DDL for one versioned control plane.

    ``schema`` is interpolated rather than parameterized because an identifier
    cannot be a bind parameter.  It has already passed ``_SCHEMA_PATTERN``,
    which admits only ``[a-z0-9_]`` with a mandatory ``_v<N>`` suffix, so it
    cannot terminate an identifier or introduce a statement.

    Triggers, not application discipline, keep the log honest: claims,
    reservations, bindings, reconciliation events, terminal evidence and audit
    events reject ``UPDATE``/``DELETE`` outright; budgets ratchet upward only;
    and the writer row's ``engine_owner`` is immutable while its generation may
    only increase, with a writer change requiring a new generation.
    """

    schema = _require_schema(schema)
    finals = ", ".join(f"'{value}'" for value in FINAL_DISPOSITIONS)
    states = ", ".join(f"'{value}'" for value in RECONCILIATION_STATES)
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_control_plane (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            control_plane_version text NOT NULL,
            storage_identity text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_task_writer (
            task_id text PRIMARY KEY,
            engine_owner text NOT NULL,
            owner_token text NOT NULL,
            fencing_generation bigint NOT NULL CHECK (fencing_generation > 0),
            lease_expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_claim (
            claim_key text PRIMARY KEY,
            task_id text NOT NULL,
            field text NOT NULL,
            expected_state text NOT NULL,
            target_state text NOT NULL,
            attempt_digest text NOT NULL,
            owner_token text NOT NULL,
            fencing_generation bigint NOT NULL CHECK (fencing_generation > 0),
            claimed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT director_primary_claim_changes_state
                CHECK (expected_state <> target_state)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_budget (
            task_id text PRIMARY KEY,
            budget_limit_units bigint NOT NULL CHECK (budget_limit_units >= 0),
            budget_reserved_units bigint NOT NULL DEFAULT 0
                CHECK (budget_reserved_units >= 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT director_primary_budget_within_limit
                CHECK (budget_reserved_units <= budget_limit_units)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_effect_reservation (
            reservation_key text PRIMARY KEY,
            claim_key text NOT NULL UNIQUE
                REFERENCES {schema}.director_primary_claim (claim_key),
            task_id text NOT NULL,
            owner_token text NOT NULL,
            fencing_generation bigint NOT NULL,
            effect_key_digest text NOT NULL,
            effect_attempt_digest text NOT NULL,
            writer_owner_id text NOT NULL,
            reserved_units bigint NOT NULL CHECK (reserved_units >= 0),
            reserved_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_remote_binding (
            reservation_key text PRIMARY KEY
                REFERENCES {schema}.director_primary_effect_reservation
                    (reservation_key),
            task_id text NOT NULL,
            owner_token text NOT NULL,
            remote_system text NOT NULL,
            remote_identifier text NOT NULL,
            bound_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT director_primary_remote_binding_is_unique
                UNIQUE (remote_system, remote_identifier)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_reconciliation_event (
            event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            claim_key text NOT NULL
                REFERENCES {schema}.director_primary_claim (claim_key),
            task_id text NOT NULL,
            owner_token text NOT NULL,
            fencing_generation bigint NOT NULL,
            state text NOT NULL CHECK (state IN ({states})),
            evidence_digest text NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS director_primary_reconciliation_by_claim
        ON {schema}.director_primary_reconciliation_event (claim_key)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_terminal_evidence (
            claim_key text PRIMARY KEY
                REFERENCES {schema}.director_primary_claim (claim_key),
            task_id text NOT NULL,
            owner_token text NOT NULL,
            fencing_generation bigint NOT NULL,
            disposition text NOT NULL CHECK (disposition IN ({finals})),
            evidence_digest text NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.director_primary_audit_event (
            event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            task_id text NOT NULL,
            owner_token text NOT NULL,
            fencing_generation bigint NOT NULL,
            event_code text NOT NULL,
            claim_key text,
            payload_digest text NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE OR REPLACE FUNCTION {schema}.director_primary_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $fn$
        BEGIN
            RAISE EXCEPTION
                'director primary control-plane table % is append-only',
                TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $fn$
        """,
        f"""
        CREATE OR REPLACE FUNCTION {schema}.director_primary_writer_guard_fn()
        RETURNS trigger LANGUAGE plpgsql AS $fn$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'director primary writer rows are never deleted'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.task_id <> OLD.task_id THEN
                RAISE EXCEPTION 'director primary task identity is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.engine_owner <> OLD.engine_owner THEN
                RAISE EXCEPTION 'director primary engine owner is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.fencing_generation < OLD.fencing_generation THEN
                RAISE EXCEPTION
                    'director primary fencing generation never decreases'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.fencing_generation = OLD.fencing_generation
                AND NEW.owner_token <> OLD.owner_token THEN
                RAISE EXCEPTION
                    'director primary writer change requires a new generation'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $fn$
        """,
        f"""
        CREATE OR REPLACE FUNCTION {schema}.director_primary_budget_ratchet()
        RETURNS trigger LANGUAGE plpgsql AS $fn$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'director primary budget rows are never deleted'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.task_id <> OLD.task_id
                OR NEW.budget_limit_units <> OLD.budget_limit_units THEN
                RAISE EXCEPTION
                    'director primary budget identity and limit are immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.budget_reserved_units < OLD.budget_reserved_units THEN
                RAISE EXCEPTION
                    'director primary budget reservations never decrease'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $fn$
        """,
        *_guard_triggers(schema),
    )


# ---------------------------------------------------------------------------
# Typed control-plane records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskWriterLease:
    """One task's immutable engine owner and its current bounded writer lease."""

    task_id: str
    engine_owner: str
    owner_token: str
    fencing_generation: int
    lease_expires_at: Any


@dataclass(frozen=True, slots=True)
class EffectReservation:
    """One durable, budget-backed intent recorded strictly before any I/O."""

    reservation_key: str
    claim_key: str
    task_id: str
    owner_token: str
    fencing_generation: int
    effect_key_digest: str
    effect_attempt_digest: str
    writer_owner_id: str
    reserved_units: int


def _lease_from_row(row: Mapping[str, Any]) -> TaskWriterLease:
    return TaskWriterLease(
        task_id=str(row["task_id"]),
        engine_owner=str(row["engine_owner"]),
        owner_token=str(row["owner_token"]),
        fencing_generation=int(row["fencing_generation"]),
        lease_expires_at=row["lease_expires_at"],
    )


def _reservation_from_row(row: Mapping[str, Any]) -> EffectReservation:
    return EffectReservation(
        reservation_key=str(row["reservation_key"]),
        claim_key=str(row["claim_key"]),
        task_id=str(row["task_id"]),
        owner_token=str(row["owner_token"]),
        fencing_generation=int(row["fencing_generation"]),
        effect_key_digest=str(row["effect_key_digest"]),
        effect_attempt_digest=str(row["effect_attempt_digest"]),
        writer_owner_id=str(row["writer_owner_id"]),
        reserved_units=int(row["reserved_units"]),
    )


@dataclass(frozen=True, slots=True)
class PostgresConditionalClaimStore:
    """Cross-process ``ConditionalClaimStore`` plus its control-plane records.

    :meth:`claim` satisfies the protocol in :mod:`.langgraph_primary`: for one
    ``claim_key``, over the lifetime of the database, at most one call in any
    thread, process or host is ever granted.  Exclusion comes from the primary
    key on ``director_primary_claim`` and an ``ON CONFLICT DO NOTHING`` insert
    whose ``RETURNING`` row decides the grant -- never from an application read,
    and never from interpreting a unique violation.

    There is no release.  A granted key stays granted, so an owner that dies
    mid-effect leaves a held fence that can only be reconciled.
    """

    _pool: Any = field(repr=False)
    _schema: str = field(repr=False)
    storage_identity: str = ""
    _store_provenance: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if self._store_provenance is not _FACTORY_PROVENANCE:
            raise PostgresCheckpointConfigurationError(
                "postgres claim store lacks factory provenance"
            )
        _require_schema(self._schema)

    def is_factory_bound_control_plane(self) -> bool:
        """Prove this store was created by the guarded Postgres factory.

        The answer is derived only from private sentinel identity.  It does not
        trust the caller-visible storage identity or any duck-typed capability
        string, so the primary runtime can enforce its structural production
        boundary without importing this module and creating an import cycle.
        """

        return self._store_provenance is _FACTORY_PROVENANCE

    # -- task ownership ---------------------------------------------------

    def acquire_task_writer(
        self,
        task_id: str,
        *,
        engine_owner: str,
        owner_token: str,
        expected_generation: int,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> TaskWriterLease:
        """Compare-and-swap the writer lease for one task.

        ``expected_generation`` is ``INITIAL_FENCING_GENERATION`` (0) for a task
        that must not yet have a writer row.  Any other value must equal the
        generation currently recorded, so a stale contender is refused rather
        than silently taking over.  A different ``owner_token`` is refused while
        the incumbent lease is unexpired on the *database* clock, and
        ``engine_owner`` can never change once recorded.
        """

        _require_id(task_id, "task_id")
        _require_actor_id(engine_owner, "engine_owner")
        _require_id(owner_token, "owner_token")
        expected = _require_generation(
            expected_generation, "expected_generation", minimum=0
        )
        lease = _require_lease_seconds(lease_seconds)
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                # Serializes a first acquisition, where there is no row to lock.
                self._optional_row(
                    connection,
                    "SELECT pg_advisory_xact_lock(%(key)s)",
                    {"key": _advisory_key("writer", schema, task_id)},
                )
                current = self._optional_row(
                    connection,
                    f"""
                    SELECT task_id, engine_owner, owner_token, fencing_generation,
                           lease_expires_at, lease_expires_at > now() AS lease_live
                    FROM {schema}.director_primary_task_writer
                    WHERE task_id = %(task_id)s
                    FOR UPDATE
                    """,
                    {"task_id": task_id},
                )
                if current is None:
                    if expected != INITIAL_FENCING_GENERATION:
                        raise PostgresControlPlaneError(
                            "stale fencing generation; no writer row exists for "
                            "this task"
                        )
                    row = self._one_row(
                        connection,
                        f"""
                        INSERT INTO {schema}.director_primary_task_writer (
                            task_id, engine_owner, owner_token,
                            fencing_generation, lease_expires_at
                        )
                        VALUES (%(task_id)s, %(engine_owner)s, %(owner_token)s, 1,
                                now() + make_interval(secs => %(lease)s))
                        RETURNING task_id, engine_owner, owner_token,
                                  fencing_generation, lease_expires_at
                        """,
                        {
                            "task_id": task_id,
                            "engine_owner": engine_owner,
                            "owner_token": owner_token,
                            "lease": lease,
                        },
                        missing="writer lease was not created",
                    )
                    return _lease_from_row(row)
                if str(current["engine_owner"]) != engine_owner:
                    raise PostgresControlPlaneError(
                        "engine_owner is immutable for this task"
                    )
                recorded = int(current["fencing_generation"])
                if expected != recorded:
                    raise PostgresControlPlaneError(
                        "stale fencing generation; the task writer has moved on"
                    )
                if (
                    str(current["owner_token"]) != owner_token
                    and bool(current["lease_live"])
                ):
                    raise PostgresControlPlaneError(
                        "another writer holds an unexpired lease on this task"
                    )
                row = self._one_row(
                    connection,
                    f"""
                    UPDATE {schema}.director_primary_task_writer
                    SET owner_token = %(owner_token)s,
                        fencing_generation = fencing_generation + 1,
                        lease_expires_at = now() + make_interval(secs => %(lease)s),
                        updated_at = now()
                    WHERE task_id = %(task_id)s
                      AND fencing_generation = %(expected)s
                    RETURNING task_id, engine_owner, owner_token,
                              fencing_generation, lease_expires_at
                    """,
                    {
                        "task_id": task_id,
                        "owner_token": owner_token,
                        "expected": expected,
                        "lease": lease,
                    },
                    missing="writer compare-and-swap lost a concurrent race",
                )
                return _lease_from_row(row)

    def renew_task_writer(
        self,
        task_id: str,
        *,
        owner_token: str,
        fencing_generation: int,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> TaskWriterLease:
        """Extend an unexpired lease *without* moving the fencing generation.

        Renewal deliberately keeps the generation, because bumping it would
        invalidate every claim this writer already holds.  Taking over from a
        different writer is :meth:`acquire_task_writer`'s job.
        """

        _require_id(task_id, "task_id")
        _require_id(owner_token, "owner_token")
        generation = _require_generation(
            fencing_generation, "fencing_generation", minimum=1
        )
        lease = _require_lease_seconds(lease_seconds)
        with self._pool.connection() as connection:
            with connection.transaction():
                row = self._optional_row(
                    connection,
                    f"""
                    UPDATE {self._schema}.director_primary_task_writer
                    SET lease_expires_at = now() + make_interval(secs => %(lease)s),
                        updated_at = now()
                    WHERE task_id = %(task_id)s
                      AND owner_token = %(owner_token)s
                      AND fencing_generation = %(generation)s
                      AND lease_expires_at > now()
                    RETURNING task_id, engine_owner, owner_token,
                              fencing_generation, lease_expires_at
                    """,
                    {
                        "task_id": task_id,
                        "owner_token": owner_token,
                        "generation": generation,
                        "lease": lease,
                    },
                )
        if row is None:
            raise PostgresControlPlaneError(
                "lease renewal refused; this writer no longer holds an unexpired "
                "lease at that generation"
            )
        return _lease_from_row(row)

    def current_task_writer(self, task_id: str) -> TaskWriterLease | None:
        """Return the recorded writer row, expired or not, for reconciliation."""

        _require_id(task_id, "task_id")
        with self._pool.connection() as connection:
            row = self._optional_row(
                connection,
                f"""
                SELECT task_id, engine_owner, owner_token, fencing_generation,
                       lease_expires_at
                FROM {self._schema}.director_primary_task_writer
                WHERE task_id = %(task_id)s
                """,
                {"task_id": task_id},
            )
        return None if row is None else _lease_from_row(row)

    # -- protocol ---------------------------------------------------------

    def claim(self, request: ClaimRequest) -> ClaimOutcome:
        """Grant ``request`` its fence, or report which owner already holds it.

        Never creates or transfers task ownership.  The insert selects from the
        current *unexpired* writer row whose ``owner_token`` matches the
        request, and stamps the claim with that exact generation, so a caller
        that is not the current writer is refused with no side effect at all.
        """

        if not isinstance(request, ClaimRequest):
            raise TypeError("claim requires a ClaimRequest")
        schema = self._schema
        parameters = {
            "claim_key": request.claim_key,
            "task_id": request.task_id,
            "field": request.field,
            "expected_state": request.expected_state,
            "target_state": request.target_state,
            "attempt_digest": request.attempt_digest,
            "owner_token": request.owner_token,
        }
        with self._pool.connection() as connection:
            with connection.transaction():
                row = self._optional_row(
                    connection,
                    f"""
                    WITH inserted AS (
                        INSERT INTO {schema}.director_primary_claim (
                            claim_key, task_id, field, expected_state,
                            target_state, attempt_digest, owner_token,
                            fencing_generation
                        )
                        SELECT %(claim_key)s, writer.task_id, %(field)s,
                               %(expected_state)s, %(target_state)s,
                               %(attempt_digest)s, writer.owner_token,
                               writer.fencing_generation
                        FROM {schema}.director_primary_task_writer writer
                        WHERE writer.task_id = %(task_id)s
                          AND writer.owner_token = %(owner_token)s
                          AND writer.lease_expires_at > now()
                        ON CONFLICT (claim_key) DO NOTHING
                        RETURNING owner_token, target_state
                    )
                    SELECT owner_token, target_state, true AS granted
                    FROM inserted
                    UNION ALL
                    SELECT held.owner_token, held.target_state, false AS granted
                    FROM {schema}.director_primary_claim held
                    WHERE held.claim_key = %(claim_key)s
                      AND NOT EXISTS (SELECT 1 FROM inserted)
                    """,
                    parameters,
                )
                if row is None:
                    # Either the insert found no authorizing writer row, or the
                    # conflicting row was committed during this statement and so
                    # sat outside its snapshot.  A fresh statement distinguishes
                    # the two; neither is ever guessed.
                    row = self._optional_row(
                        connection,
                        f"""
                        SELECT held.owner_token, held.target_state,
                               false AS granted
                        FROM {schema}.director_primary_claim held
                        WHERE held.claim_key = %(claim_key)s
                        """,
                        {"claim_key": request.claim_key},
                    )
                if row is None:
                    raise PostgresControlPlaneError(
                        "claim refused; the caller does not hold an unexpired "
                        "writer lease on this task"
                    )
        granted = bool(row["granted"])
        return ClaimOutcome(
            claim_key=request.claim_key,
            granted=granted,
            owner_token=str(row["owner_token"]),
            state=str(row["target_state"]),
            reason_code="claim_granted" if granted else "claim_already_held",
        )

    # -- budget -----------------------------------------------------------

    def register_task_budget(self, task_id: str, *, budget_limit_units: int) -> int:
        """Record one task's immutable spend ceiling, exactly once."""

        _require_id(task_id, "task_id")
        limit = _require_units(budget_limit_units, "budget_limit_units")
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                row = self._one_row(
                    connection,
                    f"""
                    WITH inserted AS (
                        INSERT INTO {schema}.director_primary_budget
                            (task_id, budget_limit_units)
                        VALUES (%(task_id)s, %(limit)s)
                        ON CONFLICT (task_id) DO NOTHING
                        RETURNING budget_limit_units
                    )
                    SELECT budget_limit_units FROM inserted
                    UNION ALL
                    SELECT existing.budget_limit_units
                    FROM {schema}.director_primary_budget existing
                    WHERE existing.task_id = %(task_id)s
                      AND NOT EXISTS (SELECT 1 FROM inserted)
                    """,
                    {"task_id": task_id, "limit": limit},
                    fallback=(
                        f"""
                        SELECT existing.budget_limit_units
                        FROM {schema}.director_primary_budget existing
                        WHERE existing.task_id = %(task_id)s
                        """,
                        {"task_id": task_id},
                    ),
                    missing="task budget did not resolve",
                )
        recorded = int(row["budget_limit_units"])
        if recorded != limit:
            raise PostgresControlPlaneError(
                "task budget limit is already recorded with a different value"
            )
        return recorded

    # -- effect intent ----------------------------------------------------

    def reserve_effect_intent(
        self,
        *,
        reservation_key: str,
        claim_key: str,
        task_id: str,
        owner_token: str,
        effect_key_digest: str,
        effect_attempt_digest: str,
        writer_owner_id: str,
        reserved_units: int,
    ) -> EffectReservation:
        """Charge budget and persist intent in one transaction, before any I/O.

        Creation is serialized on the claim row, so a concurrent duplicate waits
        rather than racing.  After the wait the reservation is re-read: an
        identical replay is returned as-is with no further budget charged, and a
        *different* reservation for the same claim is a typed conflict.  No
        unique violation is ever caught and retried.

        A claim that has a reconciliation event can never take a new
        reservation, and authority is re-derived from the current unexpired
        writer row -- a claim stamped with a superseded generation confers none.
        """

        _require_digest(reservation_key, "reservation_key")
        _require_digest(claim_key, "claim_key")
        _require_id(task_id, "task_id")
        _require_id(owner_token, "owner_token")
        _require_digest(effect_key_digest, "effect_key_digest")
        _require_digest(effect_attempt_digest, "effect_attempt_digest")
        _require_id(writer_owner_id, "writer_owner_id")
        units = _require_units(reserved_units, "reserved_units")
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                locked = self._optional_row(
                    connection,
                    f"""
                    SELECT claim_key, task_id
                    FROM {schema}.director_primary_claim
                    WHERE claim_key = %(claim_key)s
                    FOR UPDATE
                    """,
                    {"claim_key": claim_key},
                )
                if locked is None:
                    raise PostgresControlPlaneError(
                        "effect reservation refused; no such claim"
                    )
                existing = self._optional_row(
                    connection,
                    f"""
                    SELECT reservation_key, claim_key, task_id, owner_token,
                           fencing_generation, effect_key_digest,
                           effect_attempt_digest, writer_owner_id, reserved_units
                    FROM {schema}.director_primary_effect_reservation
                    WHERE claim_key = %(claim_key)s
                    """,
                    {"claim_key": claim_key},
                )
                if existing is not None:
                    recorded = _reservation_from_row(existing)
                    if (
                        recorded.reservation_key != reservation_key
                        or recorded.task_id != task_id
                        or recorded.owner_token != owner_token
                        or recorded.effect_key_digest != effect_key_digest
                        or recorded.effect_attempt_digest != effect_attempt_digest
                        or recorded.writer_owner_id != writer_owner_id
                        or recorded.reserved_units != units
                    ):
                        raise PostgresControlPlaneError(
                            "a different effect reservation already exists for "
                            "this claim"
                        )
                    return recorded
                blocked = self._optional_row(
                    connection,
                    f"""
                    SELECT 1 AS blocked
                    FROM {schema}.director_primary_reconciliation_event
                    WHERE claim_key = %(claim_key)s
                    LIMIT 1
                    """,
                    {"claim_key": claim_key},
                )
                if blocked is not None:
                    raise PostgresControlPlaneError(
                        "effect reservation refused; this claim is under "
                        "reconciliation and can never take a new reservation"
                    )
                charged = self._optional_row(
                    connection,
                    f"""
                    UPDATE {schema}.director_primary_budget
                    SET budget_reserved_units = budget_reserved_units + %(units)s,
                        updated_at = now()
                    WHERE task_id = %(task_id)s
                      AND budget_reserved_units + %(units)s <= budget_limit_units
                    RETURNING budget_reserved_units
                    """,
                    {"task_id": task_id, "units": units},
                )
                if charged is None:
                    raise PostgresControlPlaneError(
                        "effect budget reservation refused; no I/O was attempted"
                    )
                inserted = self._optional_row(
                    connection,
                    f"""
                    INSERT INTO {schema}.director_primary_effect_reservation (
                        reservation_key, claim_key, task_id, owner_token,
                        fencing_generation, effect_key_digest,
                        effect_attempt_digest, writer_owner_id, reserved_units
                    )
                    SELECT %(reservation_key)s, held.claim_key, held.task_id,
                           held.owner_token, held.fencing_generation,
                           %(effect_key_digest)s, %(effect_attempt_digest)s,
                           %(writer_owner_id)s, %(units)s
                    {_AUTHORITY_JOIN.format(schema=schema)}
                      AND held.task_id = %(task_id)s
                    RETURNING reservation_key, claim_key, task_id, owner_token,
                              fencing_generation, effect_key_digest,
                              effect_attempt_digest, writer_owner_id,
                              reserved_units
                    """,
                    {
                        "reservation_key": reservation_key,
                        "claim_key": claim_key,
                        "task_id": task_id,
                        "owner_token": owner_token,
                        "effect_key_digest": effect_key_digest,
                        "effect_attempt_digest": effect_attempt_digest,
                        "writer_owner_id": writer_owner_id,
                        "units": units,
                    },
                )
                if inserted is None:
                    # Rolls the budget charge back with the transaction.
                    raise PostgresControlPlaneError(
                        "effect reservation refused; the claim is not backed by "
                        "the current unexpired writer at its fencing generation"
                    )
                return _reservation_from_row(inserted)

    # -- remote binding ---------------------------------------------------

    def bind_remote_effect(
        self,
        *,
        reservation_key: str,
        owner_token: str,
        remote_system: str,
        remote_identifier: str,
    ) -> None:
        """Bind one reservation to the remote object it created, exactly once.

        A same-payload replay still re-verifies the current writer and fence
        before reporting success.  A remote identifier already bound to a
        *different* reservation is a cross-reservation collision and fails
        closed with :class:`PostgresControlPlaneError` -- serialized on an
        advisory lock so it is detected by a read, never by a unique violation.
        """

        _require_digest(reservation_key, "reservation_key")
        _require_id(owner_token, "owner_token")
        _require_code(remote_system, "remote_system")
        _require_id(remote_identifier, "remote_identifier")
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                self._optional_row(
                    connection,
                    "SELECT pg_advisory_xact_lock(%(key)s)",
                    {
                        "key": _advisory_key(
                            "remote", schema, remote_system, remote_identifier
                        )
                    },
                )
                authority = self._reservation_authority(
                    connection, reservation_key, owner_token
                )
                if authority is None:
                    raise PostgresControlPlaneError(
                        "remote binding refused; the reservation is not backed "
                        "by the current unexpired writer at its fencing generation"
                    )
                collision = self._optional_row(
                    connection,
                    f"""
                    SELECT reservation_key
                    FROM {schema}.director_primary_remote_binding
                    WHERE remote_system = %(remote_system)s
                      AND remote_identifier = %(remote_identifier)s
                    """,
                    {
                        "remote_system": remote_system,
                        "remote_identifier": remote_identifier,
                    },
                )
                if (
                    collision is not None
                    and str(collision["reservation_key"]) != reservation_key
                ):
                    raise PostgresControlPlaneError(
                        "remote identifier is already bound to a different "
                        "reservation"
                    )
                held = self._optional_row(
                    connection,
                    f"""
                    SELECT remote_system, remote_identifier
                    FROM {schema}.director_primary_remote_binding
                    WHERE reservation_key = %(reservation_key)s
                    """,
                    {"reservation_key": reservation_key},
                )
                if held is not None:
                    if (
                        str(held["remote_system"]) != remote_system
                        or str(held["remote_identifier"]) != remote_identifier
                    ):
                        raise PostgresControlPlaneError(
                            "remote binding already names a different remote object"
                        )
                    return
                self._one_row(
                    connection,
                    f"""
                    INSERT INTO {schema}.director_primary_remote_binding (
                        reservation_key, task_id, owner_token, remote_system,
                        remote_identifier
                    )
                    VALUES (%(reservation_key)s, %(task_id)s, %(owner_token)s,
                            %(remote_system)s, %(remote_identifier)s)
                    RETURNING reservation_key
                    """,
                    {
                        "reservation_key": reservation_key,
                        "task_id": str(authority["task_id"]),
                        "owner_token": str(authority["owner_token"]),
                        "remote_system": remote_system,
                        "remote_identifier": remote_identifier,
                    },
                    missing="remote binding was not recorded",
                )

    def read_remote_binding(
        self, *, reservation_key: str, owner_token: str
    ) -> Mapping[str, str] | None:
        """Authenticated readback of one binding, permitted under reconciliation."""

        _require_digest(reservation_key, "reservation_key")
        _require_id(owner_token, "owner_token")
        with self._pool.connection() as connection:
            with connection.transaction():
                authority = self._reservation_authority(
                    connection, reservation_key, owner_token
                )
                if authority is None:
                    raise PostgresControlPlaneError(
                        "remote readback refused; the reservation is not backed "
                        "by the current unexpired writer at its fencing generation"
                    )
                row = self._optional_row(
                    connection,
                    f"""
                    SELECT remote_system, remote_identifier
                    FROM {self._schema}.director_primary_remote_binding
                    WHERE reservation_key = %(reservation_key)s
                    """,
                    {"reservation_key": reservation_key},
                )
        if row is None:
            return None
        return {
            "remote_system": str(row["remote_system"]),
            "remote_identifier": str(row["remote_identifier"]),
        }

    def read_remote_binding_for_successor_reconciliation(
        self, *, reservation_key: str, successor_owner_token: str
    ) -> Mapping[str, str] | None:
        """Read an existing binding under narrowly fenced successor authority.

        The caller must be the current unexpired writer for the reservation's
        task at a generation strictly greater than the historical claim.  This
        path never creates a reservation or binding and therefore cannot cause
        remote I/O; a missing binding is returned conservatively as ``None``.
        """

        _require_digest(reservation_key, "reservation_key")
        _require_id(successor_owner_token, "successor_owner_token")
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                authority = self._successor_reservation_authority(
                    connection, reservation_key, successor_owner_token
                )
                if authority is None:
                    raise PostgresControlPlaneError(
                        "successor reconciliation readback refused; the caller "
                        "is not the current unexpired writer at a generation "
                        "strictly greater than the historical claim"
                    )
                row = self._optional_row(
                    connection,
                    f"""
                    SELECT remote_system, remote_identifier
                    FROM {schema}.director_primary_remote_binding
                    WHERE reservation_key = %(reservation_key)s
                    """,
                    {"reservation_key": reservation_key},
                )
        if row is None:
            return None
        return {
            "remote_system": str(row["remote_system"]),
            "remote_identifier": str(row["remote_identifier"]),
        }

    # -- reconciliation and terminal evidence -----------------------------

    def record_reconciliation_event(
        self,
        *,
        claim_key: str,
        owner_token: str,
        state: str,
        evidence_digest: str,
    ) -> int:
        """Append one ambiguity/reconciliation event for a claim.

        This is *not* terminal evidence.  It permanently blocks any new
        reservation for the claim while leaving authenticated binding, readback
        and exactly one final terminal evidence row available.
        """

        _require_digest(claim_key, "claim_key")
        _require_id(owner_token, "owner_token")
        _require_digest(evidence_digest, "evidence_digest")
        if state not in RECONCILIATION_STATES:
            raise PostgresControlPlaneError(
                "reconciliation state must be one of "
                + ", ".join(RECONCILIATION_STATES)
            )
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                row = self._optional_row(
                    connection,
                    f"""
                    INSERT INTO {schema}.director_primary_reconciliation_event (
                        claim_key, task_id, owner_token, fencing_generation,
                        state, evidence_digest
                    )
                    SELECT held.claim_key, held.task_id, held.owner_token,
                           held.fencing_generation, %(state)s, %(evidence_digest)s
                    {_AUTHORITY_JOIN.format(schema=schema)}
                    RETURNING event_id
                    """,
                    {
                        "claim_key": claim_key,
                        "owner_token": owner_token,
                        "state": state,
                        "evidence_digest": evidence_digest,
                    },
                )
        if row is None:
            raise PostgresControlPlaneError(
                "reconciliation event refused; the claim is not backed by the "
                "current unexpired writer at its fencing generation"
            )
        return int(row["event_id"])

    def record_successor_reconciliation_event(
        self,
        *,
        claim_key: str,
        successor_owner_token: str,
        state: str,
        evidence_digest: str,
    ) -> int:
        """Append reconciliation evidence for a historical claim after takeover.

        The event retains the historical ``claim_key`` but records the current
        successor token and generation that authorized the observation.
        """

        _require_digest(claim_key, "claim_key")
        _require_id(successor_owner_token, "successor_owner_token")
        _require_digest(evidence_digest, "evidence_digest")
        if state not in RECONCILIATION_STATES:
            raise PostgresControlPlaneError(
                "reconciliation state must be one of "
                + ", ".join(RECONCILIATION_STATES)
            )
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                row = self._optional_row(
                    connection,
                    f"""
                    INSERT INTO {schema}.director_primary_reconciliation_event (
                        claim_key, task_id, owner_token, fencing_generation,
                        state, evidence_digest
                    )
                    SELECT held.claim_key, held.task_id, successor.owner_token,
                           successor.fencing_generation, %(state)s,
                           %(evidence_digest)s
                    {_SUCCESSOR_RECONCILIATION_JOIN.format(schema=schema)}
                    RETURNING event_id
                    """,
                    {
                        "claim_key": claim_key,
                        "successor_owner_token": successor_owner_token,
                        "state": state,
                        "evidence_digest": evidence_digest,
                    },
                )
        if row is None:
            raise PostgresControlPlaneError(
                "successor reconciliation event refused; the caller is not the "
                "current unexpired writer at a generation strictly greater than "
                "the historical claim"
            )
        return int(row["event_id"])

    def record_terminal_evidence(
        self,
        *,
        claim_key: str,
        owner_token: str,
        disposition: str,
        evidence_digest: str,
    ) -> None:
        """Record the one *final* outcome for a fence, exactly once.

        Only final dispositions belong here.  ``AMBIGUOUS`` and
        ``RECONCILIATION_REQUIRED`` are refused and directed to
        :meth:`record_reconciliation_event`.  A same-payload replay still
        re-verifies the current writer and fence before reporting success.
        """

        _require_digest(claim_key, "claim_key")
        _require_id(owner_token, "owner_token")
        _require_digest(evidence_digest, "evidence_digest")
        if disposition in RECONCILIATION_STATES:
            raise PostgresControlPlaneError(
                f"{disposition} is not terminal evidence; record it as a "
                "reconciliation event instead"
            )
        if disposition not in FINAL_DISPOSITIONS:
            raise PostgresControlPlaneError(
                "terminal disposition must be one of " + ", ".join(FINAL_DISPOSITIONS)
            )
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                authority = self._claim_authority(connection, claim_key, owner_token)
                if authority is None:
                    raise PostgresControlPlaneError(
                        "terminal evidence refused; the claim is not backed by "
                        "the current unexpired writer at its fencing generation"
                    )
                held = self._optional_row(
                    connection,
                    f"""
                    SELECT disposition, evidence_digest
                    FROM {schema}.director_primary_terminal_evidence
                    WHERE claim_key = %(claim_key)s
                    """,
                    {"claim_key": claim_key},
                )
                if held is not None:
                    if (
                        str(held["disposition"]) != disposition
                        or str(held["evidence_digest"]) != evidence_digest
                    ):
                        raise PostgresControlPlaneError(
                            "terminal evidence is already recorded with a "
                            "different outcome"
                        )
                    return
                self._one_row(
                    connection,
                    f"""
                    INSERT INTO {schema}.director_primary_terminal_evidence (
                        claim_key, task_id, owner_token, fencing_generation,
                        disposition, evidence_digest
                    )
                    VALUES (%(claim_key)s, %(task_id)s, %(owner_token)s,
                            %(generation)s, %(disposition)s, %(evidence_digest)s)
                    RETURNING claim_key
                    """,
                    {
                        "claim_key": claim_key,
                        "task_id": str(authority["task_id"]),
                        "owner_token": str(authority["owner_token"]),
                        "generation": int(authority["fencing_generation"]),
                        "disposition": disposition,
                        "evidence_digest": evidence_digest,
                    },
                    missing="terminal evidence was not recorded",
                )

    def record_successor_terminal_evidence(
        self,
        *,
        claim_key: str,
        successor_owner_token: str,
        disposition: str,
        evidence_digest: str,
    ) -> None:
        """Record or idempotently replay one terminal outcome after takeover.

        Only the current unexpired successor at a strictly newer generation may
        use this path.  The terminal row keeps the historical claim identity and
        records the successor token/generation that finalized it.
        """

        _require_digest(claim_key, "claim_key")
        _require_id(successor_owner_token, "successor_owner_token")
        _require_digest(evidence_digest, "evidence_digest")
        if disposition in RECONCILIATION_STATES:
            raise PostgresControlPlaneError(
                f"{disposition} is not terminal evidence; record it as a "
                "reconciliation event instead"
            )
        if disposition not in FINAL_DISPOSITIONS:
            raise PostgresControlPlaneError(
                "terminal disposition must be one of " + ", ".join(FINAL_DISPOSITIONS)
            )
        schema = self._schema
        with self._pool.connection() as connection:
            with connection.transaction():
                authority = self._successor_claim_authority(
                    connection, claim_key, successor_owner_token
                )
                if authority is None:
                    raise PostgresControlPlaneError(
                        "successor terminal evidence refused; the caller is not "
                        "the current unexpired writer at a generation strictly "
                        "greater than the historical claim"
                    )
                held = self._optional_row(
                    connection,
                    f"""
                    SELECT disposition, evidence_digest
                    FROM {schema}.director_primary_terminal_evidence
                    WHERE claim_key = %(claim_key)s
                    """,
                    {"claim_key": claim_key},
                )
                if held is not None:
                    if (
                        str(held["disposition"]) != disposition
                        or str(held["evidence_digest"]) != evidence_digest
                    ):
                        raise PostgresControlPlaneError(
                            "terminal evidence is already recorded with a "
                            "different outcome"
                        )
                    return
                self._one_row(
                    connection,
                    f"""
                    INSERT INTO {schema}.director_primary_terminal_evidence (
                        claim_key, task_id, owner_token, fencing_generation,
                        disposition, evidence_digest
                    )
                    VALUES (%(claim_key)s, %(task_id)s,
                            %(successor_owner_token)s, %(successor_generation)s,
                            %(disposition)s, %(evidence_digest)s)
                    RETURNING claim_key
                    """,
                    {
                        "claim_key": claim_key,
                        "task_id": str(authority["task_id"]),
                        "successor_owner_token": str(authority["owner_token"]),
                        "successor_generation": int(
                            authority["fencing_generation"]
                        ),
                        "disposition": disposition,
                        "evidence_digest": evidence_digest,
                    },
                    missing="successor terminal evidence was not recorded",
                )

    # -- audit ------------------------------------------------------------

    def record_audit_event(
        self,
        *,
        task_id: str,
        owner_token: str,
        event_code: str,
        payload_digest: str,
        claim_key: str | None = None,
    ) -> int:
        """Append one immutable audit event and return its monotonic event id.

        A claim-scoped event requires the claim to be backed by the current
        unexpired writer at its fencing generation.  A task-level event requires
        the caller to be that current unexpired task writer.
        """

        _require_id(task_id, "task_id")
        _require_id(owner_token, "owner_token")
        _require_code(event_code, "event_code")
        _require_digest(payload_digest, "payload_digest")
        if claim_key is not None:
            _require_digest(claim_key, "claim_key")
        schema = self._schema
        if claim_key is None:
            statement = f"""
                INSERT INTO {schema}.director_primary_audit_event (
                    task_id, owner_token, fencing_generation, event_code,
                    claim_key, payload_digest
                )
                SELECT writer.task_id, writer.owner_token,
                       writer.fencing_generation, %(event_code)s, NULL,
                       %(payload_digest)s
                FROM {schema}.director_primary_task_writer writer
                WHERE writer.task_id = %(task_id)s
                  AND writer.owner_token = %(owner_token)s
                  AND writer.lease_expires_at > now()
                RETURNING event_id
                """
            parameters: dict[str, Any] = {
                "task_id": task_id,
                "owner_token": owner_token,
                "event_code": event_code,
                "payload_digest": payload_digest,
            }
            refusal = (
                "task audit event refused; the caller is not the current "
                "unexpired task writer"
            )
        else:
            statement = f"""
                INSERT INTO {schema}.director_primary_audit_event (
                    task_id, owner_token, fencing_generation, event_code,
                    claim_key, payload_digest
                )
                SELECT held.task_id, held.owner_token, held.fencing_generation,
                       %(event_code)s, held.claim_key, %(payload_digest)s
                {_AUTHORITY_JOIN.format(schema=schema)}
                  AND held.task_id = %(task_id)s
                RETURNING event_id
                """
            parameters = {
                "task_id": task_id,
                "claim_key": claim_key,
                "owner_token": owner_token,
                "event_code": event_code,
                "payload_digest": payload_digest,
            }
            refusal = (
                "claim audit event refused; the claim is not backed by the "
                "current unexpired writer at its fencing generation"
            )
        with self._pool.connection() as connection:
            with connection.transaction():
                row = self._optional_row(connection, statement, parameters)
        if row is None:
            raise PostgresControlPlaneError(refusal)
        return int(row["event_id"])

    # -- internals --------------------------------------------------------

    def _claim_authority(
        self, connection: Any, claim_key: str, owner_token: str
    ) -> Mapping[str, Any] | None:
        return self._optional_row(
            connection,
            f"""
            SELECT held.claim_key, held.task_id, held.owner_token,
                   held.fencing_generation
            {_AUTHORITY_JOIN.format(schema=self._schema)}
            """,
            {"claim_key": claim_key, "owner_token": owner_token},
        )

    def _reservation_authority(
        self, connection: Any, reservation_key: str, owner_token: str
    ) -> Mapping[str, Any] | None:
        schema = self._schema
        return self._optional_row(
            connection,
            f"""
            SELECT reserved.reservation_key, reserved.task_id,
                   reserved.owner_token, reserved.fencing_generation
            FROM {schema}.director_primary_effect_reservation reserved
            JOIN {schema}.director_primary_claim held
              ON held.claim_key = reserved.claim_key
            JOIN {schema}.director_primary_task_writer writer
              ON writer.task_id = held.task_id
             AND writer.owner_token = held.owner_token
             AND writer.fencing_generation = held.fencing_generation
             AND writer.lease_expires_at > now()
            WHERE reserved.reservation_key = %(reservation_key)s
              AND reserved.owner_token = %(owner_token)s
            """,
            {"reservation_key": reservation_key, "owner_token": owner_token},
        )

    def _successor_claim_authority(
        self, connection: Any, claim_key: str, successor_owner_token: str
    ) -> Mapping[str, Any] | None:
        return self._optional_row(
            connection,
            f"""
            SELECT held.claim_key, held.task_id, successor.owner_token,
                   successor.fencing_generation
            {_SUCCESSOR_RECONCILIATION_JOIN.format(schema=self._schema)}
            FOR UPDATE OF held, successor
            """,
            {
                "claim_key": claim_key,
                "successor_owner_token": successor_owner_token,
            },
        )

    def _successor_reservation_authority(
        self, connection: Any, reservation_key: str, successor_owner_token: str
    ) -> Mapping[str, Any] | None:
        schema = self._schema
        return self._optional_row(
            connection,
            f"""
            SELECT reserved.reservation_key, held.claim_key, held.task_id,
                   successor.owner_token, successor.fencing_generation
            FROM {schema}.director_primary_effect_reservation reserved
            JOIN {schema}.director_primary_claim held
              ON held.claim_key = reserved.claim_key
            JOIN {schema}.director_primary_task_writer successor
              ON successor.task_id = held.task_id
             AND successor.owner_token = %(successor_owner_token)s
             AND successor.fencing_generation > held.fencing_generation
             AND successor.lease_expires_at > now()
            WHERE reserved.reservation_key = %(reservation_key)s
            """,
            {
                "reservation_key": reservation_key,
                "successor_owner_token": successor_owner_token,
            },
        )

    @staticmethod
    def _optional_row(
        connection: Any, statement: str, parameters: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchone()

    def _one_row(
        self,
        connection: Any,
        statement: str,
        parameters: Mapping[str, Any],
        *,
        missing: str,
        fallback: tuple[str, Mapping[str, Any]] | None = None,
    ) -> Mapping[str, Any]:
        row = self._optional_row(connection, statement, parameters)
        if row is None and fallback is not None:
            row = self._optional_row(connection, fallback[0], fallback[1])
        if row is None:
            raise PostgresControlPlaneError(missing)
        return row


# The single authority predicate every claim-scoped mutation joins through: the
# claim must name the caller, and the *current* writer row must still match that
# claim's owner token and fencing generation with an unexpired lease.  A claim
# whose generation has been superseded therefore confers nothing.
_AUTHORITY_JOIN = """
            FROM {schema}.director_primary_claim held
            JOIN {schema}.director_primary_task_writer writer
              ON writer.task_id = held.task_id
             AND writer.owner_token = held.owner_token
             AND writer.fencing_generation = held.fencing_generation
             AND writer.lease_expires_at > now()
            WHERE held.claim_key = %(claim_key)s
              AND held.owner_token = %(owner_token)s
"""


# Reconciliation is the sole exception to exact claim-generation authority.
# It never grants claim, reservation, binding-creation, audit, or I/O authority:
# it only recognizes the current live writer for the same task when that
# writer's generation is strictly newer than the immutable historical claim.
_SUCCESSOR_RECONCILIATION_JOIN = """
            FROM {schema}.director_primary_claim held
            JOIN {schema}.director_primary_task_writer successor
              ON successor.task_id = held.task_id
             AND successor.owner_token = %(successor_owner_token)s
             AND successor.fencing_generation > held.fencing_generation
             AND successor.lease_expires_at > now()
            WHERE held.claim_key = %(claim_key)s
"""


@dataclass(slots=True)
class _HandleState:
    """The one mutable bit of a frozen handle: whether it has been closed."""

    closed: bool = False


@dataclass(frozen=True, slots=True)
class PostgresCheckpointHandle:
    """Own one initialized control plane: pool, saver, and claim store."""

    saver: Any = field(repr=False)
    pool: Any = field(repr=False)
    claim_store: PostgresConditionalClaimStore = field(repr=False)
    storage_identity: str = ""
    schema: str = ""
    _state: _HandleState = field(
        repr=False, compare=False, default_factory=_HandleState
    )
    _factory_provenance: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if self._factory_provenance is not _FACTORY_PROVENANCE:
            raise PostgresCheckpointConfigurationError(
                "postgres checkpoint handle lacks factory provenance"
            )

    @property
    def closed(self) -> bool:
        return self._state.closed

    def close(self) -> None:
        if self._state.closed:
            return
        self._state.closed = True
        self.pool.close()

    def __enter__(self) -> "PostgresCheckpointHandle":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class PostgresCheckpointBinding:
    """Bind one factory-opened saver to its opaque storage identity."""

    _handle: PostgresCheckpointHandle = field(repr=False)
    _binding_provenance: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if self._binding_provenance is not _FACTORY_PROVENANCE:
            raise PostgresCheckpointConfigurationError(
                "postgres checkpoint binding lacks factory provenance"
            )
        _assert_open_handle(self._handle)

    @classmethod
    def from_handle(cls, handle: PostgresCheckpointHandle) -> "PostgresCheckpointBinding":
        _assert_open_handle(handle)
        return cls(_handle=handle, _binding_provenance=_FACTORY_PROVENANCE)

    @property
    def checkpointer(self) -> Any:
        _assert_open_handle(self._handle)
        return self._handle.saver

    @property
    def claim_store(self) -> PostgresConditionalClaimStore:
        _assert_open_handle(self._handle)
        return self._handle.claim_store

    @property
    def storage_identity(self) -> str:
        return self._handle.storage_identity


def _assert_open_handle(handle: Any) -> PostgresCheckpointHandle:
    if not isinstance(handle, PostgresCheckpointHandle):
        raise PostgresCheckpointConfigurationError(
            "a postgres checkpoint binding requires a factory-opened handle"
        )
    if handle._factory_provenance is not _FACTORY_PROVENANCE:
        raise PostgresCheckpointConfigurationError(
            "postgres checkpoint handle lacks factory provenance"
        )
    if handle.closed:
        raise PostgresCheckpointConfigurationError(
            "postgres checkpoint handle is closed"
        )
    return handle


def _configure_connection(schema: str, expected_option: str) -> Callable[[Any], None]:
    """Return a pool ``configure`` hook that proves the schema pin took effect."""

    def configure(connection: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SHOW search_path")
            row = cursor.fetchone()
        observed = "" if row is None else str(_first_value(row)).strip().strip('"')
        if observed != schema:
            raise PostgresCheckpointConfigurationError(
                f"connection search_path is {observed!r}, not the pinned "
                f"control-plane schema {schema!r} ({expected_option!r})"
            )

    return configure


def _first_value(row: Any) -> Any:
    if isinstance(row, Mapping):
        return next(iter(row.values()), "")
    return row[0]


def _setup_control_plane(pool: Any, *, schema: str, storage_identity: str) -> None:
    """Create or verify the versioned control plane under one advisory lock."""

    lock_key = _advisory_key("ddl", schema)
    with pool.connection() as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
                for statement in _control_plane_ddl(schema):
                    cursor.execute(statement)
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.director_primary_control_plane
                        (singleton, control_plane_version, storage_identity)
                    VALUES (true, %(version)s, %(identity)s)
                    ON CONFLICT (singleton) DO NOTHING
                    """,
                    {"version": CONTROL_PLANE_VERSION, "identity": storage_identity},
                )
                cursor.execute(
                    f"""
                    SELECT control_plane_version, storage_identity
                    FROM {schema}.director_primary_control_plane
                    WHERE singleton
                    """
                )
                row = cursor.fetchone()
    if row is None:
        raise PostgresCheckpointConfigurationError(
            "control-plane metadata could not be established"
        )
    if str(row["control_plane_version"]) != CONTROL_PLANE_VERSION:
        raise PostgresCheckpointConfigurationError(
            "control-plane schema was created by a different control-plane version"
        )
    if str(row["storage_identity"]) != storage_identity:
        raise PostgresCheckpointConfigurationError(
            "control-plane schema belongs to a different storage target"
        )


def open_postgres_control_plane(
    dsn: str,
    *,
    schema: str = DEFAULT_CONTROL_PLANE_SCHEMA,
    bounds: PostgresPoolBounds | None = None,
    allow_insecure_local_transport: bool = False,
) -> PostgresCheckpointHandle:
    """Open, bound, and initialize the strict production Postgres control plane.

    Ordering is deliberate and every earlier step fails closed before the next:
    validate the DSN completely, derive the identity, build the strict
    serializer, open a bounded pool with an explicit startup deadline, create or
    verify the versioned control plane, then initialize the saver.  Nothing
    reaches the network until the configuration has already been accepted.
    """

    schema = _require_schema(schema)
    limits = bounds if bounds is not None else PostgresPoolBounds()
    if not isinstance(limits, PostgresPoolBounds):
        raise PostgresCheckpointConfigurationError(
            "pool bounds must be a PostgresPoolBounds"
        )
    dependencies = _load_dependencies()
    parsed = _parse_connection_target(
        dsn,
        conninfo_to_dict=dependencies.conninfo_to_dict,
        allow_insecure_local_transport=allow_insecure_local_transport,
    )
    identity = _storage_identity(parsed, schema=schema)
    serializer = _build_strict_serializer(dependencies.serializer_type)
    search_path_option = _search_path_option(schema)
    pool = dependencies.pool_type(
        conninfo=dsn,
        min_size=limits.min_size,
        max_size=limits.max_size,
        timeout=limits.pool_timeout_seconds,
        max_idle=limits.max_idle_seconds,
        max_lifetime=limits.max_lifetime_seconds,
        open=False,
        configure=_configure_connection(schema, search_path_option),
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dependencies.dict_row,
            "connect_timeout": limits.libpq_connect_timeout,
            "options": search_path_option,
        },
    )
    try:
        pool.open(wait=True, timeout=limits.connect_timeout_seconds)
        _setup_control_plane(pool, schema=schema, storage_identity=identity)
        saver = dependencies.saver_type(pool, serde=serializer)
        saver.setup()
        claim_store = PostgresConditionalClaimStore(
            _pool=pool,
            _schema=schema,
            storage_identity=identity,
            _store_provenance=_FACTORY_PROVENANCE,
        )
    except Exception:
        pool.close()
        raise
    return PostgresCheckpointHandle(
        saver=saver,
        pool=pool,
        claim_store=claim_store,
        storage_identity=identity,
        schema=schema,
        _factory_provenance=_FACTORY_PROVENANCE,
    )


# The pre-correction public name, preserved so existing callers keep working.
open_postgres_checkpointer = open_postgres_control_plane


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:35 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:05 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:18 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:28 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:35 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-31 | 12:40 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-31 | 15:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:08 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:35 UTC | END]]
