# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:16 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:43 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 09:15 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 01:59 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 00:13 UTC | BEGIN]]
"""Provider-neutral LangGraph runtime with fail-closed dispatch ownership.

This coordinator is intentionally narrower than the legacy Director engine. It
executes one already-authorized v1 work packet through one injected provider
adapter.  It does not plan, approve, merge, deploy, or infer authority.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import inspect
import json
import queue
import secrets
import sqlite3
import threading
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver

from .langgraph_postgres import PostgresCheckpointBinding, PostgresCheckpointHandle
from .packet_contracts import (
    PacketContractError,
    build_replay_run,
    canonical_json_bytes,
    document_digest,
    validate_packet,
)
from .provider_subgraphs import (
    PROVIDER_SUBGRAPH_SCHEMA_VERSION,
    ProviderSubgraphPhase,
    build_provider_subgraph,
)


DEFAULT_RUNTIME_CHECKPOINT_NAMESPACE = "director-runtime-v1"
PACKET_TRANSPORT_VERSION = "director-model-packet/v1"


class RuntimeExecutionMode(StrEnum):
    OFFLINE_REPLAY = "offline_replay"
    LIVE_SAFE = "live_safe"


class RuntimeExecutionStatus(StrEnum):
    COMPLETED = "completed"
    REPLAYED = "replayed"
    REJECTED = "rejected"
    OWNER_DECISION_REQUIRED = "owner_decision_required"


class RuntimeContractError(ValueError):
    """The coordinator rejected an invalid or unauthorized execution request."""


class RuntimeDispatchTimeout(TimeoutError):
    """A live provider dispatch exceeded its authorized wall-clock ceiling."""


@dataclass(frozen=True, slots=True)
class WriterOwnershipToken:
    task_id: str
    owner_id: str
    value: str = field(repr=False)

    @classmethod
    def issue(cls, *, task_id: str, owner_id: str) -> "WriterOwnershipToken":
        if not task_id.strip() or not owner_id.strip():
            raise ValueError("task_id and owner_id are required")
        return cls(task_id=task_id, owner_id=owner_id, value=secrets.token_urlsafe(32))

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderAdapterContext:
    task_id: str
    assignment_id: str
    idempotency_key: str
    work_packet_digest: str
    writer_token_digest: str
    timeout_seconds: int
    budget_usd: float
    max_hops: int
    max_revisions: int
    allowed_tools: tuple[str, ...]
    allowed_external_effects: tuple[str, ...]


class ProviderPacketAdapter(Protocol):
    """Live provider boundary; implementations receive no merge/write authority."""

    provider: str

    def dispatch(
        self,
        work_packet: Mapping[str, Any],
        *,
        context: ProviderAdapterContext,
    ) -> Mapping[str, Any]: ...


class RawProviderTransport(Protocol):
    """The untrusted side of the boundary: bytes in, an unverified payload out."""

    def send(
        self, work_packet_json: bytes, *, context: ProviderAdapterContext
    ) -> Mapping[str, Any]: ...


class PacketBoundaryAdapter:
    """The v1 packet contract *is* the live adapter boundary.

    Every real provider integration must be wrapped in this adapter rather than
    implementing `ProviderPacketAdapter` directly, so the contract is enforced on
    both sides of one seam:

    * outbound, the transport receives canonical RFC 8785 bytes of a packet that
      has just been revalidated against `model-work-packet-v1`, so a provider can
      never be handed an unvalidated or locally-mutated document;
    * inbound, the raw payload is validated against `model-result-packet-v1` and
      linked back to the work packet before it can reach the dispatch ledger, so
      an unverifiable provider reply fails closed instead of being persisted.

    The adapter holds no merge, write, approval, or budget authority. It cannot
    widen limits: `ProviderAdapterContext` is built by the coordinator from the
    validated packet and is passed through unchanged.
    """

    def __init__(self, provider: str, transport: RawProviderTransport) -> None:
        if not provider.strip():
            raise ValueError("provider is required")
        self.provider = provider
        self._transport = transport

    def dispatch(
        self,
        work_packet: Mapping[str, Any],
        *,
        context: ProviderAdapterContext,
    ) -> Mapping[str, Any]:
        return self._dispatch_with_submission_signal(
            work_packet,
            context=context,
            on_submit=lambda: None,
        )

    def _dispatch_with_submission_signal(
        self,
        work_packet: Mapping[str, Any],
        *,
        context: ProviderAdapterContext,
        on_submit: Callable[[], None],
    ) -> Mapping[str, Any]:
        """Signal only after outbound validation and immediately before submit."""

        work = validate_packet("work", work_packet)
        if not _secure_equal(document_digest(work), context.work_packet_digest):
            raise PacketContractError(
                "work packet digest does not match the authorized dispatch context"
            )
        on_submit()
        raw = self._transport.send(canonical_json_bytes(work), context=context)
        result = validate_packet("result", raw)
        # Linkage is verified here as well as in the coordinator so an invalid
        # provider reply is rejected at the seam it entered through.
        build_replay_run(work, result)
        if result["provider"] != self.provider:
            raise PacketContractError(
                "provider result does not identify this adapter's provider"
            )
        return result

    async def authenticated_readback(
        self,
        work_packet: Mapping[str, Any],
        provider_run: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Normalize one separately authenticated provider readback.

        This path never contacts the provider. It exists solely to bind a late
        callback/readback to an already ambiguous durable dispatch.
        """

        readback = getattr(self._transport, "authenticated_readback", None)
        if not callable(readback):
            raise RuntimeContractError(
                "packet transport cannot normalize authenticated readback evidence"
            )
        work = validate_packet("work", work_packet)
        expected_correlation_id = str(work["correlation"]["correlation_id"])
        observed_correlation_id = provider_run.get("interaction_id")
        if (
            not isinstance(observed_correlation_id, str)
            or not hmac.compare_digest(
                observed_correlation_id, expected_correlation_id
            )
        ):
            raise PacketContractError(
                "authenticated provider readback does not match the current "
                "work-packet correlation"
            )
        packet_bytes = canonical_json_bytes(work)
        timeout_seconds = int(work["limits"]["timeout_seconds"])
        async_callable = inspect.iscoroutinefunction(readback) or inspect.iscoroutinefunction(
            getattr(readback, "__call__", None)
        )

        async def invoke() -> Any:
            if async_callable:
                return await readback(packet_bytes, provider_run)
            raw = await asyncio.to_thread(readback, packet_bytes, provider_run)
            if inspect.isawaitable(raw):
                return await raw
            return raw

        raw = await asyncio.wait_for(invoke(), timeout=timeout_seconds)
        result = validate_packet("result", raw)
        build_replay_run(work, result)
        if result["provider"] != self.provider:
            raise PacketContractError(
                "readback result does not identify this adapter's provider"
            )
        return result


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    runtime_active: bool
    single_writer: bool
    packet_transport: str
    live_provider_dispatch: bool
    checkpoint_namespace: str

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "runtime_active": self.runtime_active,
            "single_writer": self.single_writer,
            "packet_transport": self.packet_transport,
            "live_provider_dispatch": self.live_provider_dispatch,
            "checkpoint_namespace": self.checkpoint_namespace,
        }


@dataclass(frozen=True, slots=True)
class RuntimeExecutionOutcome:
    status: RuntimeExecutionStatus
    reason: str
    result_packet: dict[str, Any] | None = None
    result_packet_digest: str | None = None


def _secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _opaque_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sqlite_checkpointer_storage_identity(checkpointer: BaseCheckpointSaver) -> str:
    connection = getattr(checkpointer, "conn", None)
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError(
            "non-SQLite checkpointers require a factory-bound postgres checkpoint"
        )
    rows = connection.execute("PRAGMA database_list").fetchall()
    database = next((str(row[2]) for row in rows if row[1] == "main"), "")
    if not database or database == ":memory:":
        raise ValueError("active LangGraph runtime requires a file-backed checkpointer")
    return "sqlite:" + str(Path(database).resolve()).casefold()


def _resolve_postgres_checkpoint_binding(
    postgres_checkpoint: PostgresCheckpointBinding | PostgresCheckpointHandle,
) -> PostgresCheckpointBinding:
    if isinstance(postgres_checkpoint, PostgresCheckpointBinding):
        return postgres_checkpoint
    return PostgresCheckpointBinding.from_handle(postgres_checkpoint)


def _assert_checkpoint_storage_identity(
    derived_identity: str,
    checkpoint_storage_identity: str | None,
) -> str:
    normalized = derived_identity.strip().casefold()
    if not normalized or not normalized.startswith(("sqlite:", "postgres:sha256:")):
        raise ValueError("checkpoint storage identity is invalid")
    if checkpoint_storage_identity is not None:
        asserted = checkpoint_storage_identity.strip().casefold()
        if asserted != normalized:
            raise ValueError(
                "checkpoint storage identity does not match derived storage target"
            )
    return normalized


class LangGraphPacketRuntime:
    """Stable coordinator API for one idempotent provider execution lane."""

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
        *,
        ledger_database_path: str,
        legacy_database_path: str,
        checkpoint_namespace: str = DEFAULT_RUNTIME_CHECKPOINT_NAMESPACE,
        runtime_active: bool = False,
        allow_live_provider_dispatch: bool = False,
        require_packet_boundary_adapter: bool = False,
        checkpoint_storage_identity: str | None = None,
        postgres_checkpoint: PostgresCheckpointBinding | PostgresCheckpointHandle | None = None,
    ) -> None:
        if not checkpoint_namespace.strip():
            raise ValueError("checkpoint_namespace is required")
        if postgres_checkpoint is not None and (
            runtime_active or allow_live_provider_dispatch
        ):
            raise ValueError(
                "postgres checkpoint is supported only for inactive offline replay"
            )
        if postgres_checkpoint is not None and checkpointer is not None:
            binding = _resolve_postgres_checkpoint_binding(postgres_checkpoint)
            if checkpointer is not binding.checkpointer:
                raise ValueError(
                    "postgres checkpoint binding does not match the supplied checkpointer"
                )
        if postgres_checkpoint is None and checkpointer is None:
            raise ValueError("a checkpointer or postgres checkpoint binding is required")
        self.checkpoint_namespace = checkpoint_namespace
        self.runtime_active = bool(runtime_active)
        self.allow_live_provider_dispatch = bool(allow_live_provider_dispatch)
        self._uses_postgres_checkpoint = postgres_checkpoint is not None
        # An activated deployment requires the enforced v1 seam. In-process test
        # doubles may still implement the protocol directly.
        self.require_packet_boundary_adapter = bool(require_packet_boundary_adapter)
        if postgres_checkpoint is not None:
            binding = _resolve_postgres_checkpoint_binding(postgres_checkpoint)
            self._checkpointer = binding.checkpointer
            derived_identity = binding.storage_identity
        else:
            assert checkpointer is not None
            if checkpoint_storage_identity is not None and checkpoint_storage_identity.strip().casefold().startswith(
                "postgres:sha256:"
            ):
                raise ValueError(
                    "postgres storage identity requires a factory-bound postgres checkpoint"
                )
            self._checkpointer = checkpointer
            derived_identity = _sqlite_checkpointer_storage_identity(checkpointer)
        self._checkpoint_storage_identity = _assert_checkpoint_storage_identity(
            derived_identity,
            checkpoint_storage_identity,
        )
        self._ledger_database = Path(ledger_database_path).resolve()
        self._legacy_database = Path(legacy_database_path).resolve()
        self._validate_storage_isolation()
        self._setup_ledger()

    def readiness(self) -> RuntimeReadiness:
        """Return honest readiness without claiming provider availability."""

        return RuntimeReadiness(
            runtime_active=self.runtime_active,
            single_writer=not self._uses_postgres_checkpoint,
            packet_transport=PACKET_TRANSPORT_VERSION,
            live_provider_dispatch=(
                self.runtime_active and self.allow_live_provider_dispatch
            ),
            checkpoint_namespace=self.checkpoint_namespace,
        )

    def _validate_storage_isolation(self) -> None:
        identities = (
            self._checkpoint_storage_identity,
            "sqlite:" + str(self._ledger_database).casefold(),
            "sqlite:" + str(self._legacy_database).casefold(),
        )
        if len(set(identities)) != len(identities):
            raise ValueError(
                "runtime checkpoint, dispatch ledger, and legacy databases must be isolated"
            )

    def _setup_ledger(self) -> None:
        self._ledger_database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._ledger_database, timeout=30)) as connection:
            self._validate_storage_isolation()
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_writer_ownership (
                    task_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    token_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state = 'ACTIVE')
                );
                CREATE TABLE IF NOT EXISTS runtime_dispatch_ledger (
                    idempotency_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    assignment_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    work_packet_digest TEXT NOT NULL,
                    authority_snapshot_digest TEXT NOT NULL,
                    writer_token_digest TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'CLAIMED', 'DISPATCHING', 'COMPLETED', 'REJECTED',
                            'RECONCILIATION_REQUIRED'
                        )
                    ),
                    result_packet_json TEXT,
                    result_packet_digest TEXT,
                    error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS runtime_pre_submit_closures (
                    idempotency_key TEXT PRIMARY KEY,
                    parallel_effect_key TEXT,
                    closed_at TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(runtime_dispatch_ledger)"
                ).fetchall()
            }
            if "work_packet_json" not in columns:
                connection.execute(
                    "ALTER TABLE runtime_dispatch_ledger ADD COLUMN work_packet_json TEXT"
                )

    def bound_work_packet(self, idempotency_key: str) -> dict[str, Any] | None:
        """Return the first accepted canonical packet for restart-safe replay."""

        with closing(sqlite3.connect(self._ledger_database, timeout=30)) as connection:
            row = connection.execute(
                "SELECT work_packet_json, work_packet_digest "
                "FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            packet = validate_packet("work", json.loads(str(row[0])))
        except (json.JSONDecodeError, PacketContractError, TypeError, ValueError) as exc:
            raise RuntimeContractError("persisted work packet is invalid") from exc
        if not _secure_equal(document_digest(packet), str(row[1])):
            raise RuntimeContractError("persisted work packet digest is invalid")
        if packet["idempotency_key"] != idempotency_key:
            raise RuntimeContractError("persisted work packet key is invalid")
        return packet

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(
            self._ledger_database, timeout=30, isolation_level=None
        )
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def bind_writer(self, token: WriterOwnershipToken) -> None:
        """Idempotently bind exactly one writer token to a task."""

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT owner_id, token_digest FROM runtime_writer_ownership WHERE task_id=?",
                (token.task_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO runtime_writer_ownership(task_id, owner_id, token_digest, state) "
                    "VALUES (?, ?, ?, 'ACTIVE')",
                    (token.task_id, token.owner_id, token.digest),
                )
                return
            if row[0] != token.owner_id or not _secure_equal(str(row[1]), token.digest):
                raise RuntimeContractError(
                    "task is already bound to a different side-effect writer"
                )

    def _assert_writer(
        self, connection: sqlite3.Connection, token: WriterOwnershipToken
    ) -> None:
        row = connection.execute(
            "SELECT owner_id, token_digest FROM runtime_writer_ownership WHERE task_id=?",
            (token.task_id,),
        ).fetchone()
        if (
            row is None
            or row[0] != token.owner_id
            or not _secure_equal(str(row[1]), token.digest)
        ):
            raise RuntimeContractError("writer ownership is absent or stale")

    def _validate_existing_row(
        self,
        row: sqlite3.Row | tuple[Any, ...],
        *,
        work: Mapping[str, Any],
        provider: str,
        execution_mode: RuntimeExecutionMode,
        writer_token_digest: str | None,
    ) -> None:
        expected = (
            work["task_id"],
            work["assignment_id"],
            provider,
            execution_mode.value,
            document_digest(work),
            work["authority_snapshot_digest"],
            writer_token_digest,
        )
        actual = tuple(row[:7])
        if actual != expected:
            raise RuntimeContractError(
                "idempotency key is already bound to a different execution"
            )

    @staticmethod
    def _validate_work_semantics(work: Mapping[str, Any]) -> None:
        checks = list(work["acceptance_checks"])
        if len(checks) != len(set(checks)):
            raise RuntimeContractError("work packet acceptance checks must be unique")
        snapshot_paths = [item["path"] for item in work["authority_snapshot"]]
        if len(snapshot_paths) != len(set(snapshot_paths)):
            raise RuntimeContractError("work packet authority paths must be unique")
        dependency_ids = [item["assignment_id"] for item in work["dependencies"]]
        if len(dependency_ids) != len(set(dependency_ids)):
            raise RuntimeContractError("work packet dependencies must be unique")
        if len(work["allowed_external_effects"]) != len(
            set(work["allowed_external_effects"])
        ):
            raise RuntimeContractError("allowed external effects must be unique")

    def _completed_outcome(
        self,
        row: tuple[Any, ...],
        *,
        replayed: bool,
        work: Mapping[str, Any],
        provider: str,
    ) -> RuntimeExecutionOutcome:
        try:
            result = json.loads(str(row[7]))
            validated = validate_packet("result", result)
        except Exception as exc:
            raise RuntimeContractError("persisted provider result is invalid") from exc
        actual_digest = document_digest(validated)
        if not _secure_equal(actual_digest, str(row[8])):
            raise RuntimeContractError("persisted provider result digest is invalid")
        for field, expected in (
            ("task_id", work["task_id"]),
            ("assignment_id", work["assignment_id"]),
            ("idempotency_key", work["idempotency_key"]),
            ("authority_snapshot_digest", work["authority_snapshot_digest"]),
            ("correlation", work["correlation"]),
            ("provider", provider),
        ):
            if validated[field] != expected:
                raise RuntimeContractError(
                    f"persisted provider result {field} binding is invalid"
                )
        return RuntimeExecutionOutcome(
            status=(
                RuntimeExecutionStatus.REPLAYED
                if replayed
                else RuntimeExecutionStatus.COMPLETED
            ),
            reason=(
                "validated result replayed without another provider dispatch"
                if replayed
                else "validated provider result completed"
            ),
            result_packet=validated,
            result_packet_digest=actual_digest,
        )

    def _ledger_row(self, idempotency_key: str) -> tuple[Any, ...] | None:
        with closing(sqlite3.connect(self._ledger_database, timeout=30)) as connection:
            return connection.execute(
                "SELECT task_id, assignment_id, provider, execution_mode, "
                "work_packet_digest, authority_snapshot_digest, writer_token_digest, "
                "result_packet_json, result_packet_digest, status, error_code "
                "FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()

    def _reserve(
        self,
        work: Mapping[str, Any],
        *,
        provider: str,
        execution_mode: RuntimeExecutionMode,
        writer_token: WriterOwnershipToken | None,
    ) -> RuntimeExecutionOutcome | None:
        writer_digest = writer_token.digest if writer_token is not None else None
        with self._transaction() as connection:
            if writer_token is not None:
                self._assert_writer(connection, writer_token)
            pre_submit_closed = connection.execute(
                """SELECT 1 FROM runtime_pre_submit_closures
                WHERE idempotency_key = ?""",
                (work["idempotency_key"],),
            ).fetchone()
            if pre_submit_closed is not None:
                return RuntimeExecutionOutcome(
                    RuntimeExecutionStatus.REJECTED,
                    "the exact provider effect was durably closed before submit",
                )
            row = connection.execute(
                "SELECT task_id, assignment_id, provider, execution_mode, "
                "work_packet_digest, authority_snapshot_digest, writer_token_digest, "
                "result_packet_json, result_packet_digest, status, error_code "
                "FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (work["idempotency_key"],),
            ).fetchone()
            if row is not None:
                self._validate_existing_row(
                    row,
                    work=work,
                    provider=provider,
                    execution_mode=execution_mode,
                    writer_token_digest=writer_digest,
                )
                if row[9] == "COMPLETED":
                    return self._completed_outcome(
                        row, replayed=True, work=work, provider=provider
                    )
                if row[9] == "REJECTED":
                    return RuntimeExecutionOutcome(
                        RuntimeExecutionStatus.REJECTED,
                        "the exact execution was previously rejected and will not be resent",
                    )
                if (
                    row[9] == "CLAIMED"
                    and execution_mode == RuntimeExecutionMode.OFFLINE_REPLAY
                ):
                    # A CLAIMED offline row means the process stopped before the
                    # local replay finished. No provider was contacted and no
                    # external effect is possible in this mode, so resuming the
                    # same validated packet is deterministic rather than a
                    # duplicate dispatch.
                    return None
                return RuntimeExecutionOutcome(
                    RuntimeExecutionStatus.OWNER_DECISION_REQUIRED,
                    "dispatch state is unresolved; open reconciliation "
                    "(open_reconciliation for a legacy CLAIMED row, "
                    "recover_stranded_dispatch for a hard-killed DISPATCHING row) and "
                    "bind a provider readback instead of sending a duplicate",
                )
            # Live dispatch claims the key directly as DISPATCHING in this one
            # transaction. A separate CLAIMED -> DISPATCHING step would leave a
            # window where a crash strands the key in a state no retry and no
            # reconciliation could resolve. A hard kill after this commit can still
            # strand DISPATCHING before `run_packet` reaches its exception handler;
            # `recover_stranded_dispatch` is the explicit, attested edge for that.
            # Offline replay keeps CLAIMED because it contacts nothing and is safe
            # to resume.
            initial_status = (
                "DISPATCHING"
                if execution_mode == RuntimeExecutionMode.LIVE_SAFE
                else "CLAIMED"
            )
            connection.execute(
                "INSERT INTO runtime_dispatch_ledger("
                "idempotency_key, task_id, assignment_id, provider, execution_mode, "
                "work_packet_digest, authority_snapshot_digest, writer_token_digest, status, "
                "work_packet_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    work["idempotency_key"],
                    work["task_id"],
                    work["assignment_id"],
                    provider,
                    execution_mode.value,
                    document_digest(work),
                    work["authority_snapshot_digest"],
                    writer_digest,
                    initial_status,
                    canonical_json_bytes(work).decode("utf-8"),
                ),
            )
        return None

    def _mark_status(self, idempotency_key: str, status: str, error_code: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE runtime_dispatch_ledger SET status=?, error_code=? "
                "WHERE idempotency_key=? AND status IN ('CLAIMED', 'DISPATCHING')",
                (status, error_code[:128], idempotency_key),
            )

    def _persist_result(
        self,
        work: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        provider: str,
        writer_token: WriterOwnershipToken | None,
        allowed_statuses: tuple[str, ...] = ("CLAIMED", "DISPATCHING"),
    ) -> dict[str, str]:
        validated_result = validate_packet("result", result)
        build_replay_run(work, validated_result)
        if validated_result["provider"] != provider:
            raise PacketContractError("result provider does not match the selected adapter")
        expected_checks = list(work["acceptance_checks"])
        observed_checks = [item["check"] for item in validated_result["acceptance"]]
        if len(observed_checks) != len(set(observed_checks)):
            raise PacketContractError("result contains duplicate acceptance dispositions")
        if set(observed_checks) != set(expected_checks):
            raise PacketContractError("result does not dispose every exact acceptance check")
        allowed_effects = set(work["allowed_external_effects"])
        effect_keys: set[str] = set()
        for effect in validated_result["side_effects"]:
            if effect["idempotency_key"] in effect_keys:
                raise PacketContractError("result contains duplicate side-effect idempotency keys")
            effect_keys.add(effect["idempotency_key"])
            if effect["status"] != "NOT_ATTEMPTED" and effect["kind"] not in allowed_effects:
                raise PacketContractError("result reports an unauthorized external side effect")
        usage = validated_result.get("usage")
        if usage is not None and usage.get("cost_usd") is not None:
            if float(usage["cost_usd"]) > float(work["limits"]["budget_usd"]):
                raise PacketContractError("result-reported cost exceeds the work-packet budget")
        result_digest = document_digest(validated_result)
        encoded_result = canonical_json_bytes(validated_result).decode("utf-8")
        placeholders = ",".join("?" for _ in allowed_statuses)
        with self._transaction() as connection:
            if writer_token is not None:
                self._assert_writer(connection, writer_token)
            cursor = connection.execute(
                "UPDATE runtime_dispatch_ledger SET status='COMPLETED', "
                "result_packet_json=?, result_packet_digest=?, error_code=NULL "
                f"WHERE idempotency_key=? AND status IN ({placeholders})",
                (
                    encoded_result,
                    result_digest,
                    work["idempotency_key"],
                    *allowed_statuses,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeContractError("dispatch result lost its durable reservation")
        return {
            "result_packet_digest": result_digest,
            "result_status": str(validated_result["status"]),
            "run_reference_digest": _opaque_digest(
                str(validated_result["run_reference"])
            ),
        }

    def _checkpoint_config(self, idempotency_key: str) -> dict[str, dict[str, str]]:
        return {
            "configurable": {
                "thread_id": (
                    f"{self.checkpoint_namespace}:{_opaque_digest(idempotency_key)}"
                )
            }
        }

    @staticmethod
    def _dispatch_with_timeout(
        adapter: ProviderPacketAdapter,
        work: Mapping[str, Any],
        *,
        context: ProviderAdapterContext,
        submission_started: threading.Event,
    ) -> Mapping[str, Any]:
        """Bound a synchronous adapter without waiting for a non-cooperative call.

        Python cannot safely stop an arbitrary in-process thread. The daemon owns
        only the provider call: after timeout it cannot persist a result or change
        the dispatch ledger. The already-reserved effect remains ambiguous and is
        moved to reconciliation by ``run_packet``; it is never sent again.
        """

        completed: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                if isinstance(adapter, PacketBoundaryAdapter):
                    value: object = adapter._dispatch_with_submission_signal(
                        copy.deepcopy(work),
                        context=context,
                        on_submit=submission_started.set,
                    )
                else:
                    # Direct adapters exist only for isolated compatibility tests.
                    # They have no separately visible transport seam, so invoking
                    # them is the earliest safe point to classify submission.
                    submission_started.set()
                    value = adapter.dispatch(
                        copy.deepcopy(work),
                        context=context,
                    )
                completed.put_nowait((True, value))
            except Exception as exc:
                completed.put_nowait((False, exc))

        worker = threading.Thread(
            target=invoke,
            name=f"director-provider-dispatch-{context.assignment_id}",
            daemon=True,
        )
        worker.start()
        try:
            succeeded, value = completed.get(timeout=context.timeout_seconds)
        except queue.Empty as exc:
            raise RuntimeDispatchTimeout(
                "live provider dispatch exceeded its authorized timeout"
            ) from exc
        if not succeeded:
            assert isinstance(value, Exception)
            raise value
        if not isinstance(value, Mapping):
            raise PacketContractError("provider result must be an object")
        return value

    def run_packet(
        self,
        work_packet: Mapping[str, Any],
        *,
        provider: str,
        execution_mode: RuntimeExecutionMode,
        adapter: ProviderPacketAdapter | None = None,
        offline_result_packet: Mapping[str, Any] | None = None,
        writer_token: WriterOwnershipToken | None = None,
    ) -> RuntimeExecutionOutcome:
        """Execute or replay one validated packet without duplicate dispatch."""

        work = validate_packet("work", work_packet)
        self._validate_work_semantics(work)
        if not provider.strip():
            raise RuntimeContractError("provider is required")
        if execution_mode == RuntimeExecutionMode.LIVE_SAFE:
            if not self.runtime_active or not self.allow_live_provider_dispatch:
                raise RuntimeContractError("live provider dispatch is not active")
            if adapter is None or offline_result_packet is not None:
                raise RuntimeContractError("live execution requires exactly one adapter")
            if adapter.provider != provider:
                raise RuntimeContractError("adapter identity does not match provider")
            if self.require_packet_boundary_adapter and not isinstance(
                adapter, PacketBoundaryAdapter
            ):
                raise RuntimeContractError(
                    "live dispatch requires the v1 packet boundary adapter"
                )
            if writer_token is None or writer_token.task_id != work["task_id"]:
                raise RuntimeContractError("live execution requires the task writer token")
            self.bind_writer(writer_token)
        else:
            if adapter is not None or offline_result_packet is None or writer_token is not None:
                raise RuntimeContractError(
                    "offline replay requires one result packet and no adapter or writer token"
                )

        prior = self._reserve(
            work,
            provider=provider,
            execution_mode=execution_mode,
            writer_token=writer_token,
        )
        if prior is not None:
            return prior

        submission_started = threading.Event()

        def execute_packet() -> dict[str, str]:
            if execution_mode == RuntimeExecutionMode.OFFLINE_REPLAY:
                assert offline_result_packet is not None
                result = offline_result_packet
            else:
                assert adapter is not None and writer_token is not None
                limits = work["limits"]
                context = ProviderAdapterContext(
                    task_id=work["task_id"],
                    assignment_id=work["assignment_id"],
                    idempotency_key=work["idempotency_key"],
                    work_packet_digest=document_digest(work),
                    writer_token_digest=writer_token.digest,
                    timeout_seconds=int(limits["timeout_seconds"]),
                    budget_usd=float(limits["budget_usd"]),
                    max_hops=int(limits["max_hops"]),
                    max_revisions=int(limits["max_revisions"]),
                    allowed_tools=tuple(work["allowed_tools"]),
                    allowed_external_effects=tuple(work["allowed_external_effects"]),
                )
                result = self._dispatch_with_timeout(
                    adapter,
                    work,
                    context=context,
                    submission_started=submission_started,
                )
            return self._persist_result(
                work,
                result,
                provider=provider,
                writer_token=writer_token,
            )

        graph = build_provider_subgraph(
            execute_packet,
            checkpointer=self._checkpointer,
            name=f"provider-neutral-{provider}",
        )
        graph_input = {
            "schema_version": PROVIDER_SUBGRAPH_SCHEMA_VERSION,
            "task_id": work["task_id"],
            "assignment_id": work["assignment_id"],
            "idempotency_key_digest": _opaque_digest(work["idempotency_key"]),
            "work_packet_digest": document_digest(work),
            "authority_snapshot_digest": work["authority_snapshot_digest"],
            "provider": provider,
            "execution_mode": execution_mode.value,
            "writer_token_digest": writer_token.digest if writer_token else None,
            "phase": ProviderSubgraphPhase.VALIDATING.value,
            "visited_nodes": [],
        }
        try:
            graph.invoke(
                graph_input,
                config=self._checkpoint_config(work["idempotency_key"]),
            )
        except Exception as exc:
            row = self._ledger_row(work["idempotency_key"])
            if row is not None and row[9] == "COMPLETED":
                return self._completed_outcome(
                    row, replayed=False, work=work, provider=provider
                )
            post_submit_live_failure = (
                execution_mode == RuntimeExecutionMode.LIVE_SAFE
                and submission_started.is_set()
            )
            self._mark_status(
                work["idempotency_key"],
                (
                    "RECONCILIATION_REQUIRED"
                    if post_submit_live_failure
                    else "REJECTED"
                ),
                type(exc).__name__,
            )
            return RuntimeExecutionOutcome(
                (
                    RuntimeExecutionStatus.OWNER_DECISION_REQUIRED
                    if post_submit_live_failure
                    else RuntimeExecutionStatus.REJECTED
                ),
                (
                    "provider dispatch is ambiguous; reconcile it without resending"
                    if post_submit_live_failure
                    else "provider result or runtime evidence failed closed validation"
                ),
            )

        row = self._ledger_row(work["idempotency_key"])
        if row is None or row[9] != "COMPLETED":
            return RuntimeExecutionOutcome(
                RuntimeExecutionStatus.OWNER_DECISION_REQUIRED,
                "provider subgraph ended without durable completion evidence",
            )
        return self._completed_outcome(
            row, replayed=False, work=work, provider=provider
        )

    def open_reconciliation(
        self,
        work_packet: Mapping[str, Any],
        *,
        provider: str,
        writer_token: WriterOwnershipToken,
    ) -> RuntimeExecutionOutcome:
        """Move one stranded live claim into reconciliation. Never redispatches.

        Live dispatch now claims its key directly as `DISPATCHING`, so a `CLAIMED`
        live row can only come from a ledger written before that change. Such a row
        would otherwise be permanently unresolvable: `run_packet` refuses to resend
        it and `reconcile_result` only accepts `RECONCILIATION_REQUIRED`.

        This is the bounded recovery edge. It is explicit and performs exactly one
        transition — `CLAIMED` to `RECONCILIATION_REQUIRED` — so the owner can then
        bind a provider readback through `reconcile_result`. Existing dispatches
        require the task writer token. It contacts no provider and cannot resurrect
        a rejected or completed dispatch.
        """

        work = validate_packet("work", work_packet)
        if writer_token.task_id != work["task_id"]:
            raise RuntimeContractError("recovery requires the task writer token")
        with self._transaction() as connection:
            # [[Codex | GPT-5.6 | 2026-08-25 | 00:44 UTC | BEGIN]]
            row = connection.execute(
                "SELECT task_id, assignment_id, provider, execution_mode, "
                "work_packet_digest, authority_snapshot_digest, writer_token_digest, "
                "result_packet_json, result_packet_digest, status, error_code "
                "FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (work["idempotency_key"],),
            ).fetchone()
            if row is None:
                raise RuntimeContractError("no dispatch exists to recover")
            self._assert_writer(connection, writer_token)
            # [[Codex | GPT-5.6 | 2026-08-25 | 00:44 UTC | END]]
            self._validate_existing_row(
                row,
                work=work,
                provider=provider,
                execution_mode=RuntimeExecutionMode.LIVE_SAFE,
                writer_token_digest=writer_token.digest,
            )
            if row[9] == "COMPLETED":
                return self._completed_outcome(
                    row, replayed=True, work=work, provider=provider
                )
            if row[9] == "RECONCILIATION_REQUIRED":
                return RuntimeExecutionOutcome(
                    RuntimeExecutionStatus.OWNER_DECISION_REQUIRED,
                    "dispatch is already awaiting reconciliation",
                )
            if row[9] != "CLAIMED":
                # REJECTED is terminal. DISPATCHING is indistinguishable from a
                # dispatch still in flight in another worker, so it is recoverable
                # only through `recover_stranded_dispatch`, which requires the
                # caller to attest that the dispatching process has stopped.
                raise RuntimeContractError("dispatch is not a recoverable legacy claim")
            connection.execute(
                "UPDATE runtime_dispatch_ledger SET status='RECONCILIATION_REQUIRED', "
                "error_code=? WHERE idempotency_key=? AND status='CLAIMED'",
                ("StrandedClaimRecovered", work["idempotency_key"]),
            )
        return RuntimeExecutionOutcome(
            RuntimeExecutionStatus.OWNER_DECISION_REQUIRED,
            "stranded claim moved to reconciliation; bind a provider readback without resending",
        )

    # [[Claude | Claude Opus 5 | 2026-08-25 | 03:26 UTC | BEGIN]]
    def recover_stranded_dispatch(
        self,
        work_packet: Mapping[str, Any],
        *,
        provider: str,
        writer_token: WriterOwnershipToken,
        dispatch_process_confirmed_stopped: bool = False,
    ) -> RuntimeExecutionOutcome:
        """Move one hard-killed `DISPATCHING` row into reconciliation. Never redispatches.

        `run_packet` marks an ambiguous live dispatch `RECONCILIATION_REQUIRED` from
        its exception handler, but a hard process kill — SIGKILL, power loss,
        container eviction — can land between the atomic `DISPATCHING` reservation in
        `_reserve` and that handler ever running. The surviving row is then
        unresolvable by every other edge: `_reserve` refuses to resend it,
        `open_reconciliation` only accepts a legacy `CLAIMED` row, and
        `reconcile_result` only accepts `RECONCILIATION_REQUIRED`.

        This is the bounded recovery edge for exactly that case. It performs one
        transition — `DISPATCHING` to `RECONCILIATION_REQUIRED` — and nothing else.
        It contacts no provider, resends no packet, writes no result, and infers
        nothing about whether the provider ran: the owner must still bind
        authenticated readback evidence through `reconcile_result`, which is the only
        path that can record a result.

        A `DISPATCHING` row is indistinguishable from a dispatch still in flight in
        another worker, and that distinction is not derivable from the ledger, so the
        caller must attest to it explicitly. A wrong attestation still fails closed
        rather than racing: the live worker's `_persist_result` update is guarded on
        `CLAIMED`/`DISPATCHING`, so it loses its reservation, its result is refused,
        and the row stays in reconciliation awaiting authenticated evidence.
        """

        if not dispatch_process_confirmed_stopped:
            raise RuntimeContractError(
                "stranded-dispatch recovery requires an explicit attestation that the "
                "dispatching process has stopped"
            )
        work = validate_packet("work", work_packet)
        if writer_token.task_id != work["task_id"]:
            raise RuntimeContractError("recovery requires the task writer token")
        with self._transaction() as connection:
            # BEGIN IMMEDIATE, so the read and the guarded update below are one
            # serialized writer; a concurrent recovery either precedes this whole
            # block or observes RECONCILIATION_REQUIRED and transitions nothing.
            self._assert_writer(connection, writer_token)
            row = connection.execute(
                "SELECT task_id, assignment_id, provider, execution_mode, "
                "work_packet_digest, authority_snapshot_digest, writer_token_digest, "
                "result_packet_json, result_packet_digest, status, error_code "
                "FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (work["idempotency_key"],),
            ).fetchone()
            if row is None:
                raise RuntimeContractError("no dispatch exists to recover")
            self._validate_existing_row(
                row,
                work=work,
                provider=provider,
                execution_mode=RuntimeExecutionMode.LIVE_SAFE,
                writer_token_digest=writer_token.digest,
            )
            if row[9] == "COMPLETED":
                return self._completed_outcome(
                    row, replayed=True, work=work, provider=provider
                )
            if row[9] == "RECONCILIATION_REQUIRED":
                return RuntimeExecutionOutcome(
                    RuntimeExecutionStatus.OWNER_DECISION_REQUIRED,
                    "dispatch is already awaiting reconciliation",
                )
            if row[9] != "DISPATCHING":
                # CLAIMED belongs to `open_reconciliation`; REJECTED is terminal.
                raise RuntimeContractError("dispatch is not a recoverable live dispatch")
            cursor = connection.execute(
                "UPDATE runtime_dispatch_ledger SET status='RECONCILIATION_REQUIRED', "
                "error_code=? WHERE idempotency_key=? AND status='DISPATCHING'",
                ("StrandedDispatchRecovered", work["idempotency_key"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeContractError(
                    "stranded dispatch lost its durable reservation"
                )
        return RuntimeExecutionOutcome(
            RuntimeExecutionStatus.OWNER_DECISION_REQUIRED,
            "stranded dispatch moved to reconciliation; bind a provider readback "
            "without resending",
        )
    # [[Claude | Claude Opus 5 | 2026-08-25 | 03:26 UTC | END]]

    def reconcile_result(
        self,
        work_packet: Mapping[str, Any],
        result_packet: Mapping[str, Any],
        *,
        provider: str,
        writer_token: WriterOwnershipToken,
    ) -> RuntimeExecutionOutcome:
        """Bind a provider-readback result to one ambiguous dispatch; never resend."""

        work = validate_packet("work", work_packet)
        if writer_token.task_id != work["task_id"]:
            raise RuntimeContractError("reconciliation requires the task writer token")
        with self._transaction() as connection:
            self._assert_writer(connection, writer_token)
            row = connection.execute(
                "SELECT task_id, assignment_id, provider, execution_mode, "
                "work_packet_digest, authority_snapshot_digest, writer_token_digest, "
                "result_packet_json, result_packet_digest, status, error_code "
                "FROM runtime_dispatch_ledger WHERE idempotency_key=?",
                (work["idempotency_key"],),
            ).fetchone()
            if row is None:
                raise RuntimeContractError("no ambiguous dispatch exists to reconcile")
            self._validate_existing_row(
                row,
                work=work,
                provider=provider,
                execution_mode=RuntimeExecutionMode.LIVE_SAFE,
                writer_token_digest=writer_token.digest,
            )
            if row[9] == "COMPLETED":
                return self._completed_outcome(
                    row, replayed=True, work=work, provider=provider
                )
            if row[9] != "RECONCILIATION_REQUIRED":
                raise RuntimeContractError("dispatch is not awaiting reconciliation")
        evidence = self._persist_result(
            work,
            result_packet,
            provider=provider,
            writer_token=writer_token,
            allowed_statuses=("RECONCILIATION_REQUIRED",),
        )
        row = self._ledger_row(work["idempotency_key"])
        if row is None:
            raise RuntimeContractError("reconciled result was not persisted")
        outcome = self._completed_outcome(
            row, replayed=False, work=work, provider=provider
        )
        if outcome.result_packet_digest != evidence["result_packet_digest"]:
            raise RuntimeContractError("reconciled result evidence mismatch")
        return outcome


# [[Codex | GPT-5.6 | 2026-08-25 | 00:13 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 01:59 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 09:15 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-25 | 10:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:43 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:26 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:35 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:16 UTC | END]]
