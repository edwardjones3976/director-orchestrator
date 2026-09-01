# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:58 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:08 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-31 | 15:05 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-31 | 12:40 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:35 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:28 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:22 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:35 UTC | BEGIN]]
"""Unit and real-Postgres coverage for the bounded control plane.

Everything in the unit suites runs against fakes and never opens a socket.
:class:`PostgresControlPlaneIntegrationTests` is the only suite that talks to a
database. Its explicit ``LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN`` is mandatory;
missing infrastructure is a failure, never silently omitted coverage.
"""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg.conninfo import conninfo_to_dict

from director_orchestrator.langgraph_postgres import (
    CONTROL_PLANE_VERSION,
    DEFAULT_CONTROL_PLANE_SCHEMA,
    FINAL_DISPOSITIONS,
    INITIAL_FENCING_GENERATION,
    MAX_LEASE_SECONDS,
    MAX_POOL_SIZE,
    MIN_LEASE_SECONDS,
    RECONCILIATION_STATES,
    EffectReservation,
    IntakeAuthorityLease,
    PostgresCheckpointBinding,
    PostgresCheckpointConfigurationError,
    PostgresCheckpointHandle,
    PostgresConditionalClaimStore,
    PostgresControlPlaneError,
    PostgresPoolBounds,
    PrimaryCommandReceipt,
    PrimaryResultPacketArtifact,
    PrimaryWorkPacketArtifact,
    TaskWriterLease,
    _build_strict_serializer,
    _configure_connection,
    _control_plane_ddl,
    _parse_connection_target,
    _PostgresDependencies,
    _storage_identity,
    open_postgres_control_plane,
)
from director_orchestrator.langgraph_primary import ClaimOutcome, ClaimRequest
from director_orchestrator.langgraph_runtime import LangGraphPacketRuntime
from director_orchestrator.primary_parallel import (
    ParallelAssignment,
    ParallelAssignmentStatus,
    ParallelDispatchPolicy,
    PostgresParallelDispatch,
)
from director_orchestrator.packet_contracts import (
    canonical_json_bytes,
    document_digest,
    load_json_document,
)
from director_orchestrator.provider_activation import (
    ACTIVATION_EVIDENCE_VERSION,
    ProviderActivationBinding,
    ProviderActivationEvidence,
    ProviderActivationStage,
    ProviderActivationSurface,
)

TEST_DSN_VARIABLE = "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN"

SECURE_DSN = (
    "user=director password=secret host=postgres port=5432 dbname=director_graph "
    "sslmode=verify-full sslrootcert=/etc/ssl/ca.crt"
)
LOCAL_DSN = (
    "user=director password=secret host=127.0.0.1 port=5432 "
    "dbname=director_graph sslmode=disable"
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def _work_packet(
    *, task_id: str = "task-1", assignment_id: str = "assignment-1"
) -> dict[str, object]:
    packet = load_json_document(
        Path(__file__).resolve().parents[1]
        / "docs"
        / "schemas"
        / "model-work-packet-v1.example.json"
    )
    packet["task_id"] = task_id
    packet["assignment_id"] = assignment_id
    packet["idempotency_key"] = f"{task_id}:{assignment_id}:review"
    packet["correlation"]["correlation_id"] = f"corr-{assignment_id}"
    return packet


def _result_packet(
    *, task_id: str = "task-1", assignment_id: str = "assignment-1"
) -> dict[str, object]:
    packet = load_json_document(
        Path(__file__).resolve().parents[1]
        / "docs"
        / "schemas"
        / "model-result-packet-v1.example.json"
    )
    packet["task_id"] = task_id
    packet["assignment_id"] = assignment_id
    packet["idempotency_key"] = f"{task_id}:{assignment_id}:review"
    return packet


def _work_packet_row(packet: dict[str, object], generation: int = 3) -> dict[str, object]:
    return {
        "task_id": packet["task_id"],
        "assignment_id": packet["assignment_id"],
        "authority_snapshot_digest": packet["authority_snapshot_digest"],
        "document_digest": document_digest(packet),
        "canonical_document": canonical_json_bytes(packet).decode("utf-8"),
        "writer_fencing_generation": generation,
    }


def _claim_request(**overrides: object) -> ClaimRequest:
    fields: dict[str, object] = {
        "claim_key": DIGEST_A,
        "task_id": "task-1",
        "field": "effect_status",
        "expected_state": "CLAIMED",
        "target_state": "EXECUTING",
        "attempt_digest": DIGEST_B,
        "owner_token": "owner-1",
    }
    fields.update(overrides)
    return ClaimRequest(**fields)  # type: ignore[arg-type]


class _Serializer:
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _Saver:
    fail_setup = False
    instances: list["_Saver"] = []

    def __init__(self, pool: object, *, serde: object) -> None:
        self.pool = pool
        self.serde = serde
        self.setup_calls = 0
        self.instances.append(self)

    def setup(self) -> None:
        self.setup_calls += 1
        if self.fail_setup:
            raise RuntimeError("setup failed")

    def __repr__(self) -> str:
        return "_Saver(secret-connection)"


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self._connection = connection
        self._row: object = None

    def execute(self, statement: str, parameters: object = None) -> "_Cursor":
        self._connection.executed.append((" ".join(statement.split()), parameters))
        self._row = self._connection.respond(statement, parameters)
        return self

    def fetchone(self) -> object:
        return self._row

    def fetchall(self) -> list[object]:
        if self._row is None:
            return []
        if isinstance(self._row, list):
            return self._row
        return [self._row]

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _Connection:
    """A recording connection whose rows are supplied by the test."""

    def __init__(self, responder: object = None) -> None:
        self.executed: list[tuple[str, object]] = []
        self.transactions = 0
        self.rollbacks = 0
        self._responder = responder

    def respond(self, statement: str, parameters: object) -> object:
        if self._responder is None:
            return None
        return self._responder(statement, parameters)

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        self.transactions += 1
        try:
            yield self
        except Exception:
            self.rollbacks += 1
            raise

    def statements(self) -> list[str]:
        return [statement for statement, _ in self.executed]

    def sql(self) -> str:
        return " ".join(self.statements())

    def __repr__(self) -> str:
        return "_Connection(secret-dsn)"


class _Pool:
    def __init__(self, connection: _Connection | None = None, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.opened: list[dict[str, object]] = []
        self.closed = 0
        self._connection = connection if connection is not None else _Connection()

    def open(self, **kwargs: object) -> None:
        self.opened.append(kwargs)

    @contextmanager
    def connection(self):
        yield self._connection

    def close(self) -> None:
        self.closed += 1

    def __repr__(self) -> str:
        return "_Pool(secret-dsn)"


class _QueuedRows:
    """Return one queued row per statement, in call order."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = list(rows)

    def __call__(self, statement: str, parameters: object) -> object:
        return self._rows.pop(0) if self._rows else None


def _store(rows: list[object]) -> tuple[PostgresConditionalClaimStore, _Connection]:
    from director_orchestrator import langgraph_postgres

    connection = _Connection(_QueuedRows(rows))
    store = PostgresConditionalClaimStore(
        _pool=_Pool(connection),
        _schema=DEFAULT_CONTROL_PLANE_SCHEMA,
        storage_identity=DIGEST_C,
        # The fabrication tests prove this sentinel is unreachable by accident;
        # a behavioural test needs a genuinely provenance-checked store.
        _store_provenance=langgraph_postgres._FACTORY_PROVENANCE,
    )
    return store, connection


def _lock_row() -> dict[str, object]:
    return {"pg_advisory_xact_lock": None}


def _writer_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "task_id": "task-1",
        "engine_owner": "director-engine",
        "owner_token": "owner-1",
        "fencing_generation": 3,
        "lease_expires_at": "2026-08-31T16:00:00Z",
        "lease_live": True,
    }
    row.update(overrides)
    return row


def _intake_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "intake_owner": "legacy",
        "owner_token": "bootstrap-legacy",
        "fencing_generation": 0,
        "lease_expires_at": "-infinity",
        "lease_live": False,
        "legacy_new_intake_disabled": False,
    }
    row.update(overrides)
    return row


def _reservation_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "reservation_key": DIGEST_A,
        "claim_key": DIGEST_B,
        "task_id": "task-1",
        "owner_token": "owner-1",
        "fencing_generation": 3,
        "effect_key_digest": DIGEST_C,
        "effect_attempt_digest": DIGEST_D,
        "writer_owner_id": "langgraph-primary",
        "reserved_units": 1,
    }
    row.update(overrides)
    return row


def _activation_binding(**overrides: str) -> ProviderActivationBinding:
    fields = {
        "provider_id": "claude",
        "workspace_id": "director-default",
        "provider_version": "provider-v1",
        "app_version": "app-v1",
        "runtime_adapter_version": "adapter-v1",
        "workspace_version": "workspace-v1",
        "binding_version": "binding-v1",
    }
    fields.update(overrides)
    return ProviderActivationBinding(**fields)


def _activation_evidence(
    *,
    revision: int = 1,
    stage: ProviderActivationStage = ProviderActivationStage.INSTALLED,
    evidence_id: str = "observed-install-1",
    evidence_digest: str = "a" * 64,
    binding: ProviderActivationBinding | None = None,
    surface: ProviderActivationSurface = ProviderActivationSurface.APP,
) -> ProviderActivationEvidence:
    observed = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)
    return ProviderActivationEvidence(
        schema_version=ACTIVATION_EVIDENCE_VERSION,
        binding=binding or _activation_binding(),
        surface=surface,
        stage=stage,
        revision=revision,
        evidence_id=evidence_id,
        evidence_digest=evidence_digest,
        observed_at=observed + timedelta(minutes=revision),
        expires_at=observed + timedelta(days=1),
    )


def _activation_row(
    evidence: ProviderActivationEvidence,
    **overrides: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        **dataclasses.asdict(evidence.binding),
        "surface": evidence.surface.value,
        "revision": evidence.revision,
        "schema_version": evidence.schema_version,
        "stage": evidence.stage.value,
        "evidence_id": evidence.evidence_id,
        "evidence_digest": evidence.evidence_digest,
        "observed_at": evidence.observed_at,
        "expires_at": evidence.expires_at,
    }
    row.update(overrides)
    return row


def _reservation_arguments(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "reservation_key": DIGEST_A,
        "claim_key": DIGEST_B,
        "task_id": "task-1",
        "owner_token": "owner-1",
        "effect_key_digest": DIGEST_C,
        "effect_attempt_digest": DIGEST_D,
        "writer_owner_id": "langgraph-primary",
        "reserved_units": 1,
    }
    arguments.update(overrides)
    return arguments


# ---------------------------------------------------------------------------
# Task ownership: acquire / renew
# ---------------------------------------------------------------------------


class IntakeAuthorityLeaseTests(unittest.TestCase):
    def test_observed_authority_is_not_inferred_from_configuration(self):
        store, connection = _store([_intake_row()])
        observed = store.current_intake_authority()
        self.assertIsInstance(observed, IntakeAuthorityLease)
        self.assertEqual("legacy", observed.intake_owner)
        self.assertFalse(observed.legacy_new_intake_disabled)
        self.assertFalse(observed.lease_live)
        self.assertIn("lease_expires_at > now()", connection.sql())

    def test_langgraph_acquisition_atomically_disables_legacy_intake(self):
        store, connection = _store(
            [
                _intake_row(),
                _intake_row(
                    intake_owner="langgraph",
                    owner_token="graph-owner",
                    fencing_generation=1,
                    lease_expires_at="2026-08-31T23:00:00Z",
                    lease_live=True,
                    legacy_new_intake_disabled=True,
                ),
            ]
        )
        lease = store.acquire_intake_authority(
            intake_owner="langgraph",
            owner_token="graph-owner",
            expected_generation=0,
        )
        self.assertEqual(1, lease.fencing_generation)
        self.assertTrue(lease.legacy_new_intake_disabled)
        update = connection.statements()[1]
        self.assertIn("fencing_generation = fencing_generation + 1", update)
        self.assertIn("legacy_new_intake_disabled = %(legacy_disabled)s", update)

    def test_stale_or_live_competing_intake_owner_fails_closed(self):
        with self.subTest(refused="stale generation"):
            store, _ = _store([_intake_row(fencing_generation=3)])
            with self.assertRaisesRegex(
                PostgresControlPlaneError, "stale fencing generation"
            ):
                store.acquire_intake_authority(
                    intake_owner="langgraph",
                    owner_token="graph-owner",
                    expected_generation=2,
                )
        with self.subTest(refused="live competing owner"):
            store, _ = _store(
                [
                    _intake_row(
                        owner_token="legacy-owner",
                        fencing_generation=3,
                        lease_live=True,
                    )
                ]
            )
            with self.assertRaisesRegex(PostgresControlPlaneError, "unexpired"):
                store.acquire_intake_authority(
                    intake_owner="langgraph",
                    owner_token="graph-owner",
                    expected_generation=3,
                )

    def test_renewal_preserves_the_fencing_generation(self):
        row = _intake_row(
            intake_owner="langgraph",
            owner_token="graph-owner",
            fencing_generation=4,
            lease_live=True,
            legacy_new_intake_disabled=True,
        )
        store, connection = _store([row])
        renewed = store.renew_intake_authority(
            intake_owner="langgraph",
            owner_token="graph-owner",
            fencing_generation=4,
        )
        self.assertEqual(4, renewed.fencing_generation)
        self.assertNotIn("fencing_generation = fencing_generation + 1", connection.sql())


class TaskWriterLeaseTests(unittest.TestCase):
    def acquire(self, rows: list[object], **overrides: object):
        store, connection = _store(rows)
        arguments: dict[str, object] = {
            "engine_owner": "director-engine",
            "owner_token": "owner-1",
            "expected_generation": INITIAL_FENCING_GENERATION,
        }
        arguments.update(overrides)
        return store.acquire_task_writer("task-1", **arguments), connection

    def test_first_acquisition_creates_generation_one_under_an_advisory_lock(self):
        lease, connection = self.acquire(
            [_lock_row(), None, _writer_row(fencing_generation=1)]
        )
        self.assertIsInstance(lease, TaskWriterLease)
        self.assertEqual(1, lease.fencing_generation)
        self.assertEqual("director-engine", lease.engine_owner)
        self.assertIn("pg_advisory_xact_lock", connection.statements()[0])
        self.assertIn("FOR UPDATE", connection.statements()[1])
        self.assertIn("make_interval(secs => %(lease)s)", connection.statements()[2])

    def test_first_acquisition_with_a_nonzero_expectation_is_refused(self):
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "no writer row exists"
        ):
            self.acquire([_lock_row(), None], expected_generation=1)

    def test_stale_generation_never_takes_over(self):
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "stale fencing generation"
        ):
            self.acquire(
                [_lock_row(), _writer_row(fencing_generation=5)],
                expected_generation=3,
            )

    def test_a_simultaneous_writer_with_a_live_lease_is_refused(self):
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "unexpired lease"
        ):
            self.acquire(
                [_lock_row(), _writer_row(owner_token="owner-other", lease_live=True)],
                owner_token="owner-1",
                expected_generation=3,
            )

    def test_an_expired_lease_may_be_taken_over_at_the_next_generation(self):
        lease, connection = self.acquire(
            [
                _lock_row(),
                _writer_row(owner_token="owner-other", lease_live=False),
                _writer_row(owner_token="owner-1", fencing_generation=4),
            ],
            owner_token="owner-1",
            expected_generation=3,
        )
        self.assertEqual(4, lease.fencing_generation)
        self.assertEqual("owner-1", lease.owner_token)
        self.assertIn(
            "fencing_generation = fencing_generation + 1", connection.statements()[2]
        )
        self.assertIn("fencing_generation = %(expected)s", connection.statements()[2])

    def test_engine_owner_is_immutable(self):
        with self.assertRaisesRegex(PostgresControlPlaneError, "immutable"):
            self.acquire(
                [_lock_row(), _writer_row(engine_owner="other-engine")],
                expected_generation=3,
            )

    def test_a_lost_compare_and_swap_fails_closed(self):
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "lost a concurrent race"
        ):
            self.acquire(
                [_lock_row(), _writer_row(), None], expected_generation=3
            )

    def test_lease_and_generation_inputs_are_validated(self):
        store, _ = _store([])
        for label, overrides in {
            "lease too short": {"lease_seconds": MIN_LEASE_SECONDS - 1},
            "lease too long": {"lease_seconds": MAX_LEASE_SECONDS + 1},
            "lease not a number": {"lease_seconds": "forever"},
            "lease is a boolean": {"lease_seconds": True},
            "negative generation": {"expected_generation": -1},
            "generation is a boolean": {"expected_generation": True},
        }.items():
            with self.subTest(refused=label):
                arguments: dict[str, object] = {
                    "engine_owner": "director-engine",
                    "owner_token": "owner-1",
                    "expected_generation": INITIAL_FENCING_GENERATION,
                }
                arguments.update(overrides)
                with self.assertRaises(PostgresControlPlaneError):
                    store.acquire_task_writer("task-1", **arguments)
        with self.subTest(refused="engine owner is not an actor id"):
            with self.assertRaises(ValueError):
                store.acquire_task_writer(
                    "task-1",
                    engine_owner="Director Engine",
                    owner_token="owner-1",
                    expected_generation=0,
                )

    def test_renew_extends_without_moving_the_generation(self):
        store, connection = _store([_writer_row(fencing_generation=3)])
        lease = store.renew_task_writer(
            "task-1", owner_token="owner-1", fencing_generation=3
        )
        self.assertEqual(3, lease.fencing_generation)
        statement = connection.statements()[0]
        self.assertIn("lease_expires_at > now()", statement)
        self.assertNotIn("fencing_generation = fencing_generation + 1", statement)

    def test_renew_of_a_lost_or_expired_lease_is_refused(self):
        store, _ = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "no longer holds an unexpired lease"
        ):
            store.renew_task_writer(
                "task-1", owner_token="owner-1", fencing_generation=3
            )
        store, _ = _store([])
        with self.assertRaises(PostgresControlPlaneError):
            store.renew_task_writer(
                "task-1", owner_token="owner-1", fencing_generation=0
            )


class PrimaryCommandReceiptTests(unittest.TestCase):
    @staticmethod
    def receipt_row(**overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "task_id": "task-1",
            "operation": "provider_result",
            "command_id": "command-1",
            "command_digest": DIGEST_A,
            "resulting_projection_digest": DIGEST_B,
            "resulting_projection_version": 4,
            "writer_fencing_generation": 3,
        }
        row.update(overrides)
        return row

    @staticmethod
    def arguments(**overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "task_id": "task-1",
            "operation": "provider_result",
            "command_id": "command-1",
            "command_digest": DIGEST_A,
            "resulting_projection_digest": DIGEST_B,
            "resulting_projection_version": 4,
            "owner_token": "owner-1",
            "fencing_generation": 3,
        }
        arguments.update(overrides)
        return arguments

    def test_records_under_exact_projection_and_live_writer(self):
        store, connection = _store([_lock_row(), None, self.receipt_row()])
        receipt = store.record_primary_command_receipt(**self.arguments())
        self.assertIsInstance(receipt, PrimaryCommandReceipt)
        self.assertEqual(4, receipt.resulting_projection_version)
        statements = connection.statements()
        self.assertIn("pg_advisory_xact_lock", statements[0])
        self.assertIn("director_primary_task_projection projected", statements[2])
        self.assertIn("director_primary_task_writer writer", statements[2])
        self.assertIn("writer.lease_expires_at > now()", statements[2])
        self.assertIn("projected.projection_digest", statements[2])

    def test_exact_replay_returns_original_after_projection_advances(self):
        original = self.receipt_row()
        store, connection = _store([_lock_row(), original])
        receipt = store.record_primary_command_receipt(
            **self.arguments(
                resulting_projection_digest=DIGEST_D,
                resulting_projection_version=9,
                fencing_generation=8,
            )
        )
        self.assertEqual(DIGEST_B, receipt.resulting_projection_digest)
        self.assertEqual(4, receipt.resulting_projection_version)
        self.assertEqual(2, len(connection.statements()))
        self.assertNotIn("INSERT INTO", connection.sql())

    def test_command_id_reuse_with_changed_content_fails_closed(self):
        store, connection = _store([_lock_row(), self.receipt_row()])
        with self.assertRaisesRegex(PostgresControlPlaneError, "different content"):
            store.record_primary_command_receipt(
                **self.arguments(command_digest=DIGEST_C)
            )
        self.assertEqual(1, connection.rollbacks)
        self.assertNotIn("INSERT INTO", connection.sql())

    def test_concurrent_identity_is_serialized_before_read_and_insert(self):
        store, connection = _store([_lock_row(), None, None])
        with self.assertRaisesRegex(PostgresControlPlaneError, "projection or writer"):
            store.record_primary_command_receipt(**self.arguments())
        statements = connection.statements()
        self.assertIn("pg_advisory_xact_lock", statements[0])
        self.assertIn("ON CONFLICT (task_id, operation, command_id) DO NOTHING", statements[2])

    def test_stale_writer_or_projection_cannot_record(self):
        for label in ("stale writer", "stale projection"):
            with self.subTest(refused=label):
                store, connection = _store([_lock_row(), None, None])
                with self.assertRaisesRegex(
                    PostgresControlPlaneError, "projection or writer authority is stale"
                ):
                    store.record_primary_command_receipt(**self.arguments())
                self.assertEqual(1, connection.rollbacks)

    def test_read_is_independent_of_current_projection(self):
        store, connection = _store([self.receipt_row()])
        receipt = store.read_primary_command_receipt(
            task_id="task-1", operation="provider_result", command_id="command-1"
        )
        self.assertEqual(DIGEST_A, receipt.command_digest)  # type: ignore[union-attr]
        self.assertNotIn("director_primary_task_projection", connection.sql())


class PrimaryWorkPacketArtifactTests(unittest.TestCase):
    def test_validated_packet_is_inserted_under_the_live_writer_fence(self):
        packet = _work_packet()
        row = _work_packet_row(packet)
        store, connection = _store([_lock_row(), {"task_id": "task-1"}, None, row])
        artifact = store.record_work_packet_artifact(
            task_id="task-1",
            assignment_id="assignment-1",
            packet=packet,
            owner_token="owner-1",
            fencing_generation=3,
        )
        self.assertIsInstance(artifact, PrimaryWorkPacketArtifact)
        self.assertEqual(document_digest(packet), artifact.document_digest)
        self.assertEqual(packet, artifact.packet)
        statements = connection.statements()
        self.assertIn("pg_advisory_xact_lock", statements[0])
        self.assertIn("FOR UPDATE", statements[1])
        self.assertIn("writer.lease_expires_at > now()", statements[3])

    def test_exact_replay_is_idempotent_but_changed_content_conflicts(self):
        packet = _work_packet()
        row = _work_packet_row(packet)
        store, connection = _store([_lock_row(), {"task_id": "task-1"}, row])
        replay = store.record_work_packet_artifact(
            task_id="task-1",
            assignment_id="assignment-1",
            packet=packet,
            owner_token="owner-1",
            fencing_generation=3,
        )
        self.assertEqual(document_digest(packet), replay.document_digest)
        self.assertNotIn("INSERT INTO", connection.sql())

        changed = _work_packet()
        changed["objective"] = "Different authorized work must not replace the artifact."
        store, connection = _store(
            [_lock_row(), {"task_id": "task-1"}, _work_packet_row(packet)]
        )
        with self.assertRaisesRegex(PostgresControlPlaneError, "different content"):
            store.record_work_packet_artifact(
                task_id="task-1",
                assignment_id="assignment-1",
                packet=changed,
                owner_token="owner-1",
                fencing_generation=3,
            )
        self.assertEqual(1, connection.rollbacks)

    def test_stale_writer_and_invalid_or_misbound_packets_fail_closed(self):
        packet = _work_packet()
        store, connection = _store([_lock_row(), None])
        with self.assertRaisesRegex(PostgresControlPlaneError, "writer authority"):
            store.record_work_packet_artifact(
                task_id="task-1",
                assignment_id="assignment-1",
                packet=packet,
                owner_token="owner-1",
                fencing_generation=3,
            )
        self.assertNotIn("INSERT INTO", connection.sql())

        for label, mutated in {
            "task": {**packet, "task_id": "other-task"},
            "assignment": {**packet, "assignment_id": "other-assignment"},
            "schema": {**packet, "schema_version": "2.0"},
        }.items():
            with self.subTest(refused=label):
                store, connection = _store([])
                with self.assertRaises(PostgresControlPlaneError):
                    store.record_work_packet_artifact(
                        task_id="task-1",
                        assignment_id="assignment-1",
                        packet=mutated,
                        owner_token="owner-1",
                        fencing_generation=3,
                    )
                self.assertEqual([], connection.statements())

    def test_load_revalidates_canonical_content_digest_and_bindings(self):
        packet = _work_packet()
        store, _ = _store([_work_packet_row(packet)])
        artifact = store.read_work_packet_artifact(
            task_id="task-1", assignment_id="assignment-1"
        )
        self.assertEqual(packet, artifact.packet)  # type: ignore[union-attr]

        corrupt = _work_packet_row(packet)
        corrupt["document_digest"] = DIGEST_B
        store, _ = _store([corrupt])
        with self.assertRaisesRegex(PostgresControlPlaneError, "digest"):
            store.read_work_packet_artifact(
                task_id="task-1", assignment_id="assignment-1"
            )


# ---------------------------------------------------------------------------
# Provider activation evidence
# ---------------------------------------------------------------------------


class ProviderActivationPostgresStoreTests(unittest.TestCase):
    def test_compare_and_append_then_loads_the_typed_chain(self):
        evidence = _activation_evidence()
        store, connection = _store(
            [
                _lock_row(),
                {"revision_count": 0, "latest_revision": 0},
                {"revision": 1},
            ]
        )
        self.assertTrue(store.append(evidence, expected_revision=0))
        self.assertIn("pg_advisory_xact_lock", connection.statements()[0])
        self.assertIn("ON CONFLICT DO NOTHING", connection.statements()[2])

        store, _ = _store([[_activation_row(evidence)]])
        self.assertEqual(
            (evidence,), store.load(evidence.binding, evidence.surface)
        )

    def test_exact_replay_succeeds_without_an_insert(self):
        evidence = _activation_evidence()
        store, connection = _store(
            [
                _lock_row(),
                {"revision_count": 1, "latest_revision": 1},
                _activation_row(evidence),
            ]
        )
        self.assertTrue(store.append(evidence, expected_revision=0))
        self.assertNotIn("INSERT INTO", connection.sql())

    def test_changed_replay_and_stale_expectation_are_conflicts(self):
        evidence = _activation_evidence()
        changed = _activation_evidence(evidence_id="different-observation")
        store, _ = _store(
            [
                _lock_row(),
                {"revision_count": 1, "latest_revision": 1},
                _activation_row(evidence),
            ]
        )
        self.assertFalse(store.append(changed, expected_revision=0))

        second = _activation_evidence(
            revision=2,
            stage=ProviderActivationStage.AUTHENTICATED,
            evidence_id="observed-auth-2",
            evidence_digest="b" * 64,
        )
        store, connection = _store(
            [_lock_row(), {"revision_count": 0, "latest_revision": 0}]
        )
        self.assertFalse(store.append(second, expected_revision=1))
        self.assertNotIn("INSERT INTO", connection.sql())

    def test_revision_gap_fails_closed_on_read_and_append(self):
        second = _activation_evidence(
            revision=2,
            stage=ProviderActivationStage.AUTHENTICATED,
            evidence_id="observed-auth-2",
            evidence_digest="b" * 64,
        )
        store, _ = _store([[_activation_row(second)]])
        with self.assertRaisesRegex(PostgresControlPlaneError, "revision gap"):
            store.load(second.binding, second.surface)

        store, connection = _store(
            [_lock_row(), {"revision_count": 1, "latest_revision": 2}]
        )
        with self.assertRaisesRegex(PostgresControlPlaneError, "revision gap"):
            store.append(second, expected_revision=1)
        self.assertEqual(1, connection.rollbacks)

    def test_candidate_revision_must_follow_expected_revision(self):
        store, _ = _store([])
        with self.assertRaisesRegex(PostgresControlPlaneError, "must follow"):
            store.append(_activation_evidence(revision=1), expected_revision=1)


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


class ConditionalClaimTests(unittest.TestCase):
    def test_a_grant_selects_from_the_current_unexpired_writer(self):
        store, connection = _store(
            [{"owner_token": "owner-1", "target_state": "EXECUTING", "granted": True}]
        )
        outcome = store.claim(_claim_request())
        self.assertIsInstance(outcome, ClaimOutcome)
        self.assertTrue(outcome.granted)
        self.assertEqual("claim_granted", outcome.reason_code)
        statement = connection.statements()[0]
        self.assertIn("ON CONFLICT (claim_key) DO NOTHING", statement)
        self.assertIn("director_primary_task_writer writer", statement)
        self.assertIn("writer.owner_token = %(owner_token)s", statement)
        self.assertIn("writer.lease_expires_at > now()", statement)
        self.assertIn("writer.fencing_generation", statement)

    def test_claim_never_creates_or_transfers_task_ownership(self):
        for label, rows in {
            "granted": [
                {"owner_token": "owner-1", "target_state": "EXECUTING", "granted": True}
            ],
            "refused": [
                {"owner_token": "owner-2", "target_state": "EXECUTING", "granted": False}
            ],
        }.items():
            with self.subTest(outcome=label):
                store, connection = _store(rows)
                store.claim(_claim_request())
                sql = connection.sql()
                self.assertNotIn("INSERT INTO director_primary_task_writer", sql)
                self.assertNotIn("UPDATE " + DEFAULT_CONTROL_PLANE_SCHEMA, sql)
                self.assertNotIn("ON CONFLICT (task_id)", sql)

    def test_refusal_reports_the_holder_for_reconciliation(self):
        store, _ = _store(
            [{"owner_token": "owner-2", "target_state": "EXECUTING", "granted": False}]
        )
        outcome = store.claim(_claim_request(owner_token="owner-1"))
        self.assertFalse(outcome.granted)
        self.assertEqual("claim_already_held", outcome.reason_code)
        self.assertEqual("owner-2", outcome.owner_token)

    def test_a_caller_without_a_live_lease_is_refused_with_no_side_effect(self):
        store, connection = _store([None, None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "does not hold an unexpired writer lease"
        ):
            store.claim(_claim_request())
        self.assertEqual(1, connection.rollbacks)

    def test_a_concurrent_holder_is_found_on_a_fresh_snapshot(self):
        store, connection = _store(
            [None, {"owner_token": "owner-2", "target_state": "EXECUTING", "granted": False}]
        )
        outcome = store.claim(_claim_request())
        self.assertFalse(outcome.granted)
        self.assertEqual("owner-2", outcome.owner_token)
        self.assertEqual(2, len(connection.statements()))

    def test_claim_requires_a_real_claim_request(self):
        store, _ = _store([])
        for candidate in (None, object(), {"claim_key": DIGEST_A}):
            with self.subTest(refused=type(candidate).__name__):
                with self.assertRaises(TypeError):
                    store.claim(candidate)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Reservation, binding, reconciliation, evidence, audit
# ---------------------------------------------------------------------------


class EffectControlPlaneTests(unittest.TestCase):
    def test_reservation_serializes_on_the_claim_and_rereads_after_the_wait(self):
        store, connection = _store(
            [
                {"claim_key": DIGEST_B, "task_id": "task-1"},
                None,
                None,
                {"budget_reserved_units": 1},
                _reservation_row(),
            ]
        )
        reservation = store.reserve_effect_intent(**_reservation_arguments())
        self.assertIsInstance(reservation, EffectReservation)
        self.assertEqual(3, reservation.fencing_generation)
        statements = connection.statements()
        self.assertIn("FOR UPDATE", statements[0])
        self.assertIn("director_primary_claim", statements[0])
        self.assertIn("SELECT reservation_key", statements[1])
        self.assertIn("director_primary_reconciliation_event", statements[2])
        self.assertIn(
            "budget_reserved_units + %(units)s <= budget_limit_units", statements[3]
        )
        self.assertIn("director_primary_task_writer writer", statements[4])
        self.assertIn(
            "writer.fencing_generation = held.fencing_generation", statements[4]
        )
        self.assertEqual(0, connection.rollbacks)

    def test_identical_concurrent_replay_charges_no_further_budget(self):
        store, connection = _store(
            [{"claim_key": DIGEST_B, "task_id": "task-1"}, _reservation_row()]
        )
        reservation = store.reserve_effect_intent(**_reservation_arguments())
        self.assertEqual(DIGEST_A, reservation.reservation_key)
        self.assertEqual(2, len(connection.statements()))
        self.assertNotIn("budget_reserved_units +", connection.sql())

    def test_a_different_reservation_for_the_claim_is_a_typed_conflict(self):
        for label, overrides in {
            "different key": {"reservation_key": DIGEST_D},
            "different units": {"reserved_units": 9},
            "different effect": {"effect_key_digest": DIGEST_A},
            "different attempt": {"effect_attempt_digest": DIGEST_A},
            "different writer": {"writer_owner_id": "other-writer"},
            "different owner": {"owner_token": "owner-other"},
        }.items():
            with self.subTest(conflict=label):
                store, connection = _store(
                    [
                        {"claim_key": DIGEST_B, "task_id": "task-1"},
                        _reservation_row(**overrides),
                    ]
                )
                with self.assertRaisesRegex(
                    PostgresControlPlaneError, "different effect reservation"
                ):
                    store.reserve_effect_intent(**_reservation_arguments())
                self.assertNotIn("budget_reserved_units +", connection.sql())

    def test_reconciliation_permanently_blocks_a_new_reservation(self):
        store, connection = _store(
            [{"claim_key": DIGEST_B, "task_id": "task-1"}, None, {"blocked": 1}]
        )
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "under reconciliation"
        ):
            store.reserve_effect_intent(**_reservation_arguments())
        self.assertNotIn("budget_reserved_units +", connection.sql())
        self.assertEqual(1, connection.rollbacks)

    def test_reservation_without_a_claim_or_budget_fails_closed(self):
        store, connection = _store([None])
        with self.assertRaisesRegex(PostgresControlPlaneError, "no such claim"):
            store.reserve_effect_intent(**_reservation_arguments())
        store, connection = _store(
            [{"claim_key": DIGEST_B, "task_id": "task-1"}, None, None, None]
        )
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "no I/O was attempted"
        ):
            store.reserve_effect_intent(**_reservation_arguments())
        self.assertEqual(1, connection.rollbacks)

    def test_a_stale_fence_cannot_reserve_and_the_charge_rolls_back(self):
        store, connection = _store(
            [
                {"claim_key": DIGEST_B, "task_id": "task-1"},
                None,
                None,
                {"budget_reserved_units": 1},
                None,
            ]
        )
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "current unexpired writer"
        ):
            store.reserve_effect_intent(**_reservation_arguments())
        self.assertEqual(1, connection.rollbacks)

    # -- remote binding ---------------------------------------------------

    def bind(self, store: PostgresConditionalClaimStore, **overrides: object) -> None:
        arguments: dict[str, object] = {
            "reservation_key": DIGEST_A,
            "owner_token": "owner-1",
            "remote_system": "provider",
            "remote_identifier": "remote-1",
        }
        arguments.update(overrides)
        store.bind_remote_effect(**arguments)  # type: ignore[arg-type]

    def test_binding_locks_the_remote_identifier_and_verifies_authority(self):
        store, connection = _store(
            [_lock_row(), _reservation_row(), None, None, {"reservation_key": DIGEST_A}]
        )
        self.bind(store)
        self.assertIn("pg_advisory_xact_lock", connection.statements()[0])
        self.assertIn("director_primary_task_writer writer", connection.statements()[1])

    def test_a_cross_reservation_remote_collision_is_typed_and_fails_closed(self):
        store, connection = _store(
            [_lock_row(), _reservation_row(), {"reservation_key": DIGEST_D}]
        )
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "already bound to a different reservation"
        ):
            self.bind(store)
        self.assertEqual(1, connection.rollbacks)

    def test_same_payload_binding_replay_still_verifies_the_current_owner(self):
        store, _ = _store(
            [
                _lock_row(),
                _reservation_row(),
                {"reservation_key": DIGEST_A},
                {"remote_system": "provider", "remote_identifier": "remote-1"},
            ]
        )
        self.bind(store)
        # Identical payload, but the caller no longer holds the fence.
        store, _ = _store([_lock_row(), None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "current unexpired writer"
        ):
            self.bind(store)

    def test_binding_a_different_remote_object_is_refused(self):
        store, _ = _store(
            [
                _lock_row(),
                _reservation_row(),
                None,
                {"remote_system": "provider", "remote_identifier": "remote-2"},
            ]
        )
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "different remote object"
        ):
            self.bind(store)

    def test_readback_is_authenticated_and_may_return_nothing(self):
        store, _ = _store(
            [_reservation_row(), {"remote_system": "provider", "remote_identifier": "r"}]
        )
        self.assertEqual(
            {"remote_system": "provider", "remote_identifier": "r"},
            store.read_remote_binding(reservation_key=DIGEST_A, owner_token="owner-1"),
        )
        store, _ = _store([_reservation_row(), None])
        self.assertIsNone(
            store.read_remote_binding(reservation_key=DIGEST_A, owner_token="owner-1")
        )
        store, _ = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "current unexpired writer"
        ):
            store.read_remote_binding(
                reservation_key=DIGEST_A, owner_token="owner-impostor"
            )

    def test_successor_readback_requires_a_strictly_newer_live_generation(self):
        authority = {
            "reservation_key": DIGEST_A,
            "claim_key": DIGEST_B,
            "task_id": "task-1",
            "owner_token": "owner-2",
            "fencing_generation": 4,
        }
        store, connection = _store(
            [authority, {"remote_system": "provider", "remote_identifier": "r-2"}]
        )
        self.assertEqual(
            {"remote_system": "provider", "remote_identifier": "r-2"},
            store.read_remote_binding_for_successor_reconciliation(
                reservation_key=DIGEST_A,
                successor_owner_token="owner-2",
            ),
        )
        authority_sql = connection.statements()[0]
        self.assertIn(
            "successor.fencing_generation > held.fencing_generation", authority_sql
        )
        self.assertIn("successor.lease_expires_at > now()", authority_sql)
        self.assertNotIn(
            "INSERT INTO director_primary_v2.director_primary_remote_binding",
            connection.sql(),
        )

        store, _ = _store([authority, None])
        self.assertIsNone(
            store.read_remote_binding_for_successor_reconciliation(
                reservation_key=DIGEST_A,
                successor_owner_token="owner-2",
            )
        )

        store, connection = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "strictly greater than the historical claim"
        ):
            store.read_remote_binding_for_successor_reconciliation(
                reservation_key=DIGEST_A,
                successor_owner_token="owner-2",
            )
        self.assertEqual(1, len(connection.statements()))

    # -- reconciliation and terminal evidence -----------------------------

    def test_reconciliation_events_are_appended_under_current_authority(self):
        for state in RECONCILIATION_STATES:
            with self.subTest(state=state):
                store, connection = _store([{"event_id": 4}])
                self.assertEqual(
                    4,
                    store.record_reconciliation_event(
                        claim_key=DIGEST_B,
                        owner_token="owner-1",
                        state=state,
                        evidence_digest=DIGEST_C,
                    ),
                )
                self.assertIn(
                    "director_primary_task_writer writer", connection.statements()[0]
                )
        store, _ = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "current unexpired writer"
        ):
            store.record_reconciliation_event(
                claim_key=DIGEST_B,
                owner_token="owner-1",
                state=RECONCILIATION_STATES[0],
                evidence_digest=DIGEST_C,
            )
        store, _ = _store([])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "reconciliation state must be"
        ):
            store.record_reconciliation_event(
                claim_key=DIGEST_B,
                owner_token="owner-1",
                state="COMPLETED",
                evidence_digest=DIGEST_C,
            )

    def test_successor_reconciliation_records_current_token_and_generation(self):
        store, connection = _store([{"event_id": 9}])
        self.assertEqual(
            9,
            store.record_successor_reconciliation_event(
                claim_key=DIGEST_B,
                successor_owner_token="owner-2",
                state=RECONCILIATION_STATES[0],
                evidence_digest=DIGEST_C,
            ),
        )
        statement, parameters = connection.executed[0]
        self.assertIn("successor.owner_token", statement)
        self.assertIn("successor.fencing_generation", statement)
        self.assertIn(
            "successor.fencing_generation > held.fencing_generation", statement
        )
        self.assertEqual("owner-2", parameters["successor_owner_token"])

        store, _ = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "strictly greater than the historical claim"
        ):
            store.record_successor_reconciliation_event(
                claim_key=DIGEST_B,
                successor_owner_token="owner-2",
                state=RECONCILIATION_STATES[0],
                evidence_digest=DIGEST_C,
            )

    def test_ambiguity_is_never_accepted_as_terminal_evidence(self):
        for state in RECONCILIATION_STATES:
            with self.subTest(refused=state):
                store, connection = _store([])
                with self.assertRaisesRegex(
                    PostgresControlPlaneError, "not terminal evidence"
                ):
                    store.record_terminal_evidence(
                        claim_key=DIGEST_B,
                        owner_token="owner-1",
                        disposition=state,
                        evidence_digest=DIGEST_C,
                    )
                self.assertEqual([], connection.statements())
        store, _ = _store([])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "terminal disposition must be"
        ):
            store.record_terminal_evidence(
                claim_key=DIGEST_B,
                owner_token="owner-1",
                disposition="RELEASED",
                evidence_digest=DIGEST_C,
            )

    def test_final_evidence_is_insert_once_and_replay_reverifies_authority(self):
        for disposition in FINAL_DISPOSITIONS:
            with self.subTest(disposition=disposition):
                store, connection = _store(
                    [
                        {
                            "claim_key": DIGEST_B,
                            "task_id": "task-1",
                            "owner_token": "owner-1",
                            "fencing_generation": 3,
                        },
                        None,
                        {"claim_key": DIGEST_B},
                    ]
                )
                store.record_terminal_evidence(
                    claim_key=DIGEST_B,
                    owner_token="owner-1",
                    disposition=disposition,
                    evidence_digest=DIGEST_C,
                )
                self.assertIn("INSERT INTO", connection.statements()[2])
        # Same payload, stale fence: refused before the replay is reported ok.
        store, connection = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "current unexpired writer"
        ):
            store.record_terminal_evidence(
                claim_key=DIGEST_B,
                owner_token="owner-1",
                disposition=FINAL_DISPOSITIONS[0],
                evidence_digest=DIGEST_C,
            )
        self.assertEqual(1, len(connection.statements()))
        # Authorised, but already recorded with a different outcome.
        store, _ = _store(
            [
                {
                    "claim_key": DIGEST_B,
                    "task_id": "task-1",
                    "owner_token": "owner-1",
                    "fencing_generation": 3,
                },
                {"disposition": FINAL_DISPOSITIONS[-1], "evidence_digest": DIGEST_C},
            ]
        )
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "different outcome"
        ):
            store.record_terminal_evidence(
                claim_key=DIGEST_B,
                owner_token="owner-1",
                disposition=FINAL_DISPOSITIONS[0],
                evidence_digest=DIGEST_C,
            )

    def test_successor_terminal_evidence_is_insert_once_and_replay_is_idempotent(self):
        authority = {
            "claim_key": DIGEST_B,
            "task_id": "task-1",
            "owner_token": "owner-2",
            "fencing_generation": 7,
        }
        store, connection = _store([authority, None, {"claim_key": DIGEST_B}])
        arguments = {
            "claim_key": DIGEST_B,
            "successor_owner_token": "owner-2",
            "disposition": FINAL_DISPOSITIONS[0],
            "evidence_digest": DIGEST_C,
        }
        store.record_successor_terminal_evidence(**arguments)
        insert_statement, insert_parameters = connection.executed[2]
        authority_statement = connection.statements()[0]
        self.assertIn("FOR UPDATE OF held, successor", authority_statement)
        self.assertIn("director_primary_terminal_evidence", insert_statement)
        self.assertEqual("owner-2", insert_parameters["successor_owner_token"])
        self.assertEqual(7, insert_parameters["successor_generation"])

        replay, replay_connection = _store(
            [
                authority,
                {
                    "disposition": FINAL_DISPOSITIONS[0],
                    "evidence_digest": DIGEST_C,
                },
            ]
        )
        replay.record_successor_terminal_evidence(**arguments)
        self.assertEqual(2, len(replay_connection.statements()))

        refused, _ = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "strictly greater than the historical claim"
        ):
            refused.record_successor_terminal_evidence(**arguments)

    # -- audit ------------------------------------------------------------

    def test_claim_audit_requires_the_current_fence(self):
        store, connection = _store([{"event_id": 12}])
        self.assertEqual(
            12,
            store.record_audit_event(
                task_id="task-1",
                owner_token="owner-1",
                event_code="effect_claimed",
                payload_digest=DIGEST_C,
                claim_key=DIGEST_B,
            ),
        )
        statement = connection.statements()[0]
        self.assertIn("director_primary_claim held", statement)
        self.assertIn(
            "writer.fencing_generation = held.fencing_generation", statement
        )
        store, _ = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "claim audit event refused"
        ):
            store.record_audit_event(
                task_id="task-1",
                owner_token="owner-1",
                event_code="effect_claimed",
                payload_digest=DIGEST_C,
                claim_key=DIGEST_B,
            )

    def test_task_audit_requires_the_current_task_writer(self):
        store, connection = _store([{"event_id": 3}])
        self.assertEqual(
            3,
            store.record_audit_event(
                task_id="task-1",
                owner_token="owner-1",
                event_code="task_started",
                payload_digest=DIGEST_C,
            ),
        )
        statement = connection.statements()[0]
        self.assertIn("director_primary_task_writer writer", statement)
        self.assertIn("writer.lease_expires_at > now()", statement)
        self.assertNotIn("director_primary_claim", statement)
        store, _ = _store([None])
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "task audit event refused"
        ):
            store.record_audit_event(
                task_id="task-1",
                owner_token="owner-1",
                event_code="task_started",
                payload_digest=DIGEST_C,
            )

    def test_audit_input_is_validated_before_any_statement(self):
        store, connection = _store([])
        for label, overrides in {
            "event code": {"event_code": "Not A Code"},
            "digest": {"payload_digest": "nope"},
            "task": {"task_id": ""},
            "claim key": {"claim_key": "nope"},
        }.items():
            with self.subTest(refused=label):
                arguments: dict[str, object] = {
                    "task_id": "task-1",
                    "owner_token": "owner-1",
                    "event_code": "effect_claimed",
                    "payload_digest": DIGEST_C,
                }
                arguments.update(overrides)
                with self.assertRaises(ValueError):
                    store.record_audit_event(**arguments)  # type: ignore[arg-type]
        self.assertEqual([], connection.statements())


# ---------------------------------------------------------------------------
# Factory, identity, transport, provenance, DDL
# ---------------------------------------------------------------------------


class PostgresControlPlaneUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        _Serializer.calls.clear()
        _Saver.instances.clear()
        _Saver.fail_setup = False
        self.pools: list[_Pool] = []
        self.metadata: dict[str, object] = {}

    def dependencies(self, *, serializer_type: type | None = None):
        state = self.metadata

        def respond(statement: str, parameters: object) -> object:
            squashed = " ".join(statement.split())
            if "INSERT INTO" in squashed and "control_plane" in squashed:
                assert isinstance(parameters, dict)
                # Emulates ON CONFLICT DO NOTHING.
                state.setdefault("version", parameters["version"])
                state.setdefault("identity", parameters["identity"])
                return None
            if squashed.startswith("SELECT control_plane_version"):
                return {
                    "control_plane_version": state.get("version"),
                    "storage_identity": state.get("identity"),
                }
            return None

        connection = _Connection(respond)

        def pool_type(**kwargs: object) -> _Pool:
            pool = _Pool(connection, **kwargs)
            self.pools.append(pool)
            return pool

        return _PostgresDependencies(
            dict_row="DICT_ROW",
            conninfo_to_dict=conninfo_to_dict,
            pool_type=pool_type,
            saver_type=_Saver,
            serializer_type=serializer_type or _Serializer,
        )

    def open_with_fakes(self, dsn: str = SECURE_DSN, **kwargs: object):
        with patch(
            "director_orchestrator.langgraph_postgres._load_dependencies",
            return_value=self.dependencies(),
        ):
            return open_postgres_control_plane(dsn, **kwargs)  # type: ignore[arg-type]

    def parse(self, dsn: str, *, insecure: bool = False):
        return _parse_connection_target(
            dsn,
            conninfo_to_dict=conninfo_to_dict,
            allow_insecure_local_transport=insecure,
        )

    def identity(self, dsn: str, *, schema: str = DEFAULT_CONTROL_PLANE_SCHEMA) -> str:
        return _storage_identity(self.parse(dsn), schema=schema)

    # -- factory ----------------------------------------------------------

    def test_opens_bounded_pool_and_never_surfaces_credentials(self):
        handle = self.open_with_fakes()
        pool = self.pools[0]
        defaults = PostgresPoolBounds()
        self.assertEqual(SECURE_DSN, pool.kwargs["conninfo"])
        self.assertEqual(defaults.max_size, pool.kwargs["max_size"])
        self.assertEqual(defaults.pool_timeout_seconds, pool.kwargs["timeout"])
        self.assertEqual(defaults.max_lifetime_seconds, pool.kwargs["max_lifetime"])
        self.assertEqual(defaults.max_idle_seconds, pool.kwargs["max_idle"])
        self.assertIs(False, pool.kwargs["open"])
        self.assertEqual(
            {
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": "DICT_ROW",
                "connect_timeout": defaults.libpq_connect_timeout,
                "options": f"-c search_path={DEFAULT_CONTROL_PLANE_SCHEMA}",
            },
            pool.kwargs["kwargs"],
        )
        self.assertEqual(
            [{"wait": True, "timeout": defaults.connect_timeout_seconds}], pool.opened
        )
        self.assertEqual(1, _Saver.instances[0].setup_calls)
        self.assertEqual(
            {
                "pickle_fallback": False,
                "allowed_json_modules": (),
                "allowed_msgpack_modules": (),
            },
            _Serializer.calls[0],
        )
        self.assertRegex(handle.storage_identity, r"^postgres:sha256:[0-9a-f]{64}$")
        rendered = repr(handle) + repr(PostgresCheckpointBinding.from_handle(handle))
        for secret in ("secret", "password", SECURE_DSN, "director_graph", "_Saver"):
            self.assertNotIn(secret, rendered)
        handle.close()
        handle.close()
        self.assertEqual(1, pool.closed)
        self.assertTrue(handle.closed)

    def test_setup_failure_closes_the_pool(self):
        _Saver.fail_setup = True
        with self.assertRaisesRegex(RuntimeError, "setup failed"):
            self.open_with_fakes()
        self.assertEqual(1, self.pools[0].closed)

    def test_control_plane_drift_is_refused(self):
        self.metadata["identity"] = "postgres:sha256:" + "0" * 64
        self.metadata["version"] = CONTROL_PLANE_VERSION
        with self.assertRaisesRegex(
            PostgresCheckpointConfigurationError, "different storage target"
        ):
            self.open_with_fakes()
        self.assertEqual(1, self.pools[-1].closed)
        self.metadata = {
            "identity": self.identity(SECURE_DSN),
            "version": "director-postgres-control-plane/v0",
        }
        with self.assertRaisesRegex(
            PostgresCheckpointConfigurationError, "different control-plane version"
        ):
            self.open_with_fakes()

    def test_incompatible_serializer_signature_fails_before_any_pool(self):
        class _LegacySerializer:
            def __init__(self, *, pickle_fallback: bool) -> None:
                self.pickle_fallback = pickle_fallback

        with patch(
            "director_orchestrator.langgraph_postgres._load_dependencies",
            return_value=self.dependencies(serializer_type=_LegacySerializer),
        ):
            with self.assertRaisesRegex(
                PostgresCheckpointConfigurationError,
                "serializer signature is incompatible",
            ):
                open_postgres_control_plane(SECURE_DSN)
        self.assertEqual([], self.pools)

    # -- connection completeness ------------------------------------------

    def test_incomplete_or_externally_resolved_connection_is_refused(self):
        cases = {
            "no dbname": (
                "user=d host=h port=5432 sslmode=verify-ca sslrootcert=ca.crt",
                "dbname",
            ),
            "no port": (
                "user=d host=h dbname=g sslmode=verify-ca sslrootcert=ca.crt",
                "port",
            ),
            "no user": (
                "host=h port=5432 dbname=g sslmode=verify-ca sslrootcert=ca.crt",
                "user",
            ),
            "no sslmode": ("user=d host=h port=5432 dbname=g", "sslmode"),
            "no host or hostaddr": (
                "user=d port=5432 dbname=g sslmode=verify-ca sslrootcert=ca.crt",
                "host or hostaddr",
            ),
            "service file": (
                "service=director user=d host=h port=5432 dbname=g "
                "sslmode=verify-ca sslrootcert=ca.crt",
                "service",
            ),
            "passfile": (
                "passfile=/tmp/pgpass user=d host=h port=5432 dbname=g "
                "sslmode=verify-ca sslrootcert=ca.crt",
                "passfile",
            ),
            "options": (
                "options=-csearch_path=other user=d host=h port=5432 dbname=g "
                "sslmode=verify-ca sslrootcert=ca.crt",
                "must not set options",
            ),
            "port is not a port": (
                "user=d host=h port=99999 dbname=g sslmode=verify-ca sslrootcert=ca.crt",
                "TCP port",
            ),
        }
        for label, (dsn, expected) in cases.items():
            with self.subTest(refused=label):
                with self.assertRaisesRegex(
                    PostgresCheckpointConfigurationError, expected
                ):
                    self.parse(dsn)

    def test_blank_and_broken_dsn_is_refused(self):
        for label, dsn in {
            "empty": "",
            "whitespace": "   ",
            "not a string": None,
            "line break": SECURE_DSN + "\nhost=evil",
            "unparseable": "=nonsense=",
        }.items():
            with self.subTest(refused=label):
                with self.assertRaises(PostgresCheckpointConfigurationError):
                    self.parse(dsn)  # type: ignore[arg-type]

    # -- transport floor --------------------------------------------------

    def test_transport_floor_refuses_unauthenticated_modes(self):
        base = "user=d host=postgres port=5432 dbname=g"
        for label, dsn, expected in (
            ("prefer", f"{base} sslmode=prefer", "verify-ca or"),
            ("require", f"{base} sslmode=require", "verify-ca or"),
            ("disable", f"{base} sslmode=disable", "verify-ca or"),
            ("unknown", f"{base} sslmode=maybe", "not a libpq sslmode"),
            ("no root cert", f"{base} sslmode=verify-full", "sslrootcert"),
        ):
            with self.subTest(refused=label):
                with self.assertRaisesRegex(
                    PostgresCheckpointConfigurationError, expected
                ):
                    self.parse(dsn)
        self.assertEqual("verify-full", self.parse(SECURE_DSN)["sslmode"])

    def test_local_exception_is_narrow_and_still_fails_closed(self):
        self.assertEqual("disable", self.parse(LOCAL_DSN, insecure=True)["sslmode"])
        compose = (
            "user=d host=langgraph-primary-db port=5432 dbname=g sslmode=disable"
        )
        self.assertEqual(
            "langgraph-primary-db", self.parse(compose, insecure=True)["host"]
        )
        for label, dsn, expected in (
            (
                "non-loopback host",
                "user=d host=postgres port=5432 dbname=g sslmode=disable",
                "loopback or the exact",
            ),
            (
                "sslmode still omitted",
                "user=d host=127.0.0.1 port=5432 dbname=g",
                "sslmode",
            ),
            (
                "loopback host with remote hostaddr",
                "user=d host=localhost hostaddr=10.0.0.2 port=5432 dbname=g "
                "sslmode=disable",
                "loopback or the exact",
            ),
            (
                "lookalike compose host",
                "user=d host=langgraph-primary-db.evil port=5432 dbname=g "
                "sslmode=disable",
                "loopback or the exact",
            ),
            (
                "compose host with remote hostaddr",
                "user=d host=langgraph-primary-db hostaddr=10.0.0.2 port=5432 "
                "dbname=g sslmode=disable",
                "loopback or the exact",
            ),
        ):
            with self.subTest(refused=label):
                with self.assertRaisesRegex(
                    PostgresCheckpointConfigurationError, expected
                ):
                    self.parse(dsn, insecure=True)

    # -- identity ---------------------------------------------------------

    def test_transport_tuning_does_not_change_storage_identity(self):
        baseline = self.identity(SECURE_DSN)
        variants = {
            "user_password": (
                "user=other password=other-secret host=postgres port=5432 "
                "dbname=director_graph sslmode=verify-full sslrootcert=/etc/ssl/ca.crt"
            ),
            "application_name": SECURE_DSN + " application_name=director-runtime",
            "connect_timeout": SECURE_DSN + " connect_timeout=9",
            "keepalives": (
                SECURE_DSN + " keepalives=1 keepalives_idle=30 "
                "keepalives_interval=10 keepalives_count=5"
            ),
            "tls_client_material": SECURE_DSN + " sslcert=client.crt sslkey=client.key",
        }
        for label, dsn in variants.items():
            with self.subTest(ignored_tuning=label):
                self.assertEqual(baseline, self.identity(dsn))

    def test_storage_target_and_schema_changes_change_identity(self):
        baseline = self.identity(SECURE_DSN)
        cases = {
            "port": SECURE_DSN.replace("port=5432", "port=5433"),
            "host": SECURE_DSN.replace("host=postgres", "host=other-host"),
            "dbname": SECURE_DSN.replace("dbname=director_graph", "dbname=other_db"),
            "hostaddr": SECURE_DSN + " hostaddr=10.0.0.2",
            "target_session_attrs": SECURE_DSN + " target_session_attrs=read-only",
        }
        for label, dsn in cases.items():
            with self.subTest(changed_target=label):
                self.assertNotEqual(baseline, self.identity(dsn))
        self.assertNotEqual(
            baseline, self.identity(SECURE_DSN, schema="director_primary_v3")
        )

    def test_unversioned_schema_is_refused(self):
        for schema in ("public", "director_primary", "Director_v1", "a b_v1", "x_v1;--", ""):
            with self.subTest(refused=schema):
                with self.assertRaisesRegex(
                    PostgresCheckpointConfigurationError, "explicit versioned name"
                ):
                    _storage_identity(self.parse(SECURE_DSN), schema=schema)

    # -- pool bounds ------------------------------------------------------

    def test_pool_bounds_reject_unbounded_or_absent_limits(self):
        cases = {
            "zero max": {"max_size": 0},
            "over cap": {"max_size": MAX_POOL_SIZE + 1},
            "min above max": {"min_size": 4, "max_size": 2},
            "negative min": {"min_size": -1},
            "boolean max": {"max_size": True},
            "zero connect timeout": {"connect_timeout_seconds": 0},
            "negative pool timeout": {"pool_timeout_seconds": -1.0},
            "absurd lifetime": {"max_lifetime_seconds": 10_000_000.0},
            "non numeric idle": {"max_idle_seconds": "long"},
        }
        for label, overrides in cases.items():
            with self.subTest(refused=label):
                with self.assertRaises(PostgresCheckpointConfigurationError):
                    PostgresPoolBounds(**overrides)  # type: ignore[arg-type]

    def test_custom_bounds_reach_the_pool(self):
        bounds = PostgresPoolBounds(
            min_size=2, max_size=5, connect_timeout_seconds=3.0, pool_timeout_seconds=4.0
        )
        self.open_with_fakes(bounds=bounds)
        self.assertEqual(2, self.pools[0].kwargs["min_size"])
        self.assertEqual(5, self.pools[0].kwargs["max_size"])
        self.assertEqual(4.0, self.pools[0].kwargs["timeout"])
        self.assertEqual(3, self.pools[0].kwargs["kwargs"]["connect_timeout"])
        with self.assertRaisesRegex(
            PostgresCheckpointConfigurationError, "PostgresPoolBounds"
        ):
            self.open_with_fakes(bounds=object())

    # -- search_path pin --------------------------------------------------

    def test_configure_hook_rejects_a_connection_in_the_wrong_schema(self):
        configure = _configure_connection(DEFAULT_CONTROL_PLANE_SCHEMA, "-c ...")
        matching = _Connection(lambda *_: {"search_path": DEFAULT_CONTROL_PLANE_SCHEMA})
        configure(matching)
        self.assertEqual(["SHOW search_path"], matching.statements())
        for label, row in {
            "role default": {"search_path": '"$user", public'},
            "other version": {"search_path": "director_primary_v9"},
            "nothing": None,
        }.items():
            with self.subTest(refused=label):
                with self.assertRaisesRegex(
                    PostgresCheckpointConfigurationError, "search_path"
                ):
                    configure(_Connection(lambda *_, row=row: row))

    # -- fabrication ------------------------------------------------------

    def test_handles_bindings_and_stores_cannot_be_fabricated(self):
        with self.subTest(fabricated="handle"):
            with self.assertRaisesRegex(
                PostgresCheckpointConfigurationError, "factory provenance"
            ):
                PostgresCheckpointHandle(
                    saver=object(),
                    pool=_Pool(),
                    claim_store=object(),  # type: ignore[arg-type]
                    storage_identity=DIGEST_A,
                    schema=DEFAULT_CONTROL_PLANE_SCHEMA,
                )
        with self.subTest(fabricated="claim store"):
            with self.assertRaisesRegex(
                PostgresCheckpointConfigurationError, "factory provenance"
            ):
                PostgresConditionalClaimStore(
                    _pool=_Pool(),
                    _schema=DEFAULT_CONTROL_PLANE_SCHEMA,
                    storage_identity=DIGEST_A,
                )

        class _FakeHandle:
            saver = object()
            storage_identity = "postgres:sha256:" + "f" * 64
            closed = False

        with self.subTest(fabricated="binding around a duck type"):
            with self.assertRaisesRegex(
                PostgresCheckpointConfigurationError, "factory provenance"
            ):
                PostgresCheckpointBinding(_handle=_FakeHandle())  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                PostgresCheckpointConfigurationError, "factory-opened handle"
            ):
                PostgresCheckpointBinding.from_handle(_FakeHandle())  # type: ignore[arg-type]

    def test_control_plane_capability_is_private_factory_sentinel_identity(self):
        store, _ = _store([])
        self.assertTrue(store.is_factory_bound_control_plane())
        self.assertNotEqual(store.storage_identity, "")

        object.__setattr__(store, "_store_provenance", object())
        self.assertFalse(store.is_factory_bound_control_plane())
        # A caller-visible identity cannot restore the private sentinel proof.
        object.__setattr__(store, "storage_identity", DIGEST_A)
        self.assertFalse(store.is_factory_bound_control_plane())

    def test_handle_identity_is_immutable_and_closure_is_observable(self):
        handle = self.open_with_fakes()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            handle.storage_identity = DIGEST_A  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            handle.saver = object()  # type: ignore[misc]
        binding = PostgresCheckpointBinding.from_handle(handle)
        handle.close()
        with self.assertRaisesRegex(PostgresCheckpointConfigurationError, "closed"):
            binding.checkpointer
        with self.assertRaisesRegex(PostgresCheckpointConfigurationError, "closed"):
            PostgresCheckpointBinding.from_handle(handle)

    # -- DDL shape --------------------------------------------------------

    def test_control_plane_ddl_is_schema_scoped_and_guarded(self):
        schema = DEFAULT_CONTROL_PLANE_SCHEMA
        joined = " ".join(" ".join(_control_plane_ddl(schema)).split())
        self.assertIn(f"CREATE SCHEMA IF NOT EXISTS {schema}", joined)
        for table in (
            "director_primary_intake_authority",
            "director_primary_intake_dedup",
            "director_primary_command_receipt",
            "director_provider_activation_evidence",
            "director_primary_task_projection",
            "director_primary_task_writer",
            "director_primary_work_packet_artifact",
            "director_primary_claim",
            "director_primary_budget",
            "director_primary_effect_reservation",
            "director_primary_remote_binding",
            "director_primary_reconciliation_event",
            "director_primary_terminal_evidence",
            "director_primary_audit_event",
        ):
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {schema}.{table}", joined)
                self.assertIn(
                    f"CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON "
                    f"{schema}.{table}",
                    joined,
                )
        # Terminal evidence admits only final dispositions; ambiguity lives in
        # the reconciliation log.
        self.assertIn(
            "disposition text NOT NULL CHECK (disposition IN ("
            + ", ".join(f"'{value}'" for value in FINAL_DISPOSITIONS)
            + "))",
            joined,
        )
        for state in RECONCILIATION_STATES:
            self.assertNotIn(f"disposition IN ('{state}'", joined)
        self.assertIn("engine owner is immutable", joined)
        self.assertIn("writer change requires a new generation", joined)
        self.assertNotIn("public.", joined)
        with self.assertRaises(PostgresCheckpointConfigurationError):
            _control_plane_ddl("public")

    # -- runtime contract -------------------------------------------------

    def test_runtime_accepts_bound_identity_without_dsn_exposure(self):
        handle = self.open_with_fakes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = LangGraphPacketRuntime(
                postgres_checkpoint=PostgresCheckpointBinding.from_handle(handle),
                ledger_database_path=str(root / "ledger.sqlite3"),
                legacy_database_path=str(root / "tasks.sqlite3"),
                checkpoint_storage_identity=handle.storage_identity,
            )
            readiness = repr(runtime.readiness().as_dict())
        self.assertNotIn("secret", readiness)
        self.assertNotIn("director_graph", readiness)
        handle.close()

    def test_runtime_rejects_unbound_postgres_identity_string(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                ValueError, "factory-bound postgres checkpoint"
            ):
                LangGraphPacketRuntime(
                    object(),
                    ledger_database_path=str(root / "ledger.sqlite3"),
                    legacy_database_path=str(root / "tasks.sqlite3"),
                    checkpoint_storage_identity="postgres:sha256:" + "a" * 64,
                )

    def test_runtime_rejects_identity_mismatch_and_mismatched_saver(self):
        handle = self.open_with_fakes()
        binding = PostgresCheckpointBinding.from_handle(handle)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                ValueError, "does not match derived storage target"
            ):
                LangGraphPacketRuntime(
                    postgres_checkpoint=binding,
                    ledger_database_path=str(root / "ledger.sqlite3"),
                    legacy_database_path=str(root / "tasks.sqlite3"),
                    checkpoint_storage_identity="postgres:sha256:" + "b" * 64,
                )
            with self.assertRaisesRegex(
                ValueError, "does not match the supplied checkpointer"
            ):
                LangGraphPacketRuntime(
                    object(),
                    postgres_checkpoint=binding,
                    ledger_database_path=str(root / "ledger.sqlite3"),
                    legacy_database_path=str(root / "tasks.sqlite3"),
                )
        handle.close()

    def test_distinct_sqlite_files_cannot_collide_through_masked_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_path = root / "first-checkpoints.sqlite3"
            second_path = root / "second-checkpoints.sqlite3"
            first_connection = sqlite3.connect(first_path, check_same_thread=False)
            try:
                first = SqliteSaver(first_connection)
                derived_second = "sqlite:" + str(second_path.resolve()).casefold()
                with self.assertRaisesRegex(
                    ValueError, "does not match derived storage target"
                ):
                    LangGraphPacketRuntime(
                        first,
                        ledger_database_path=str(root / "ledger.sqlite3"),
                        legacy_database_path=str(root / "legacy.sqlite3"),
                        checkpoint_storage_identity=derived_second,
                    )
            finally:
                first_connection.close()

    def test_strict_serializer_round_trips_primitives_and_blocks_unsafe_objects(self):
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        serializer = _build_strict_serializer(JsonPlusSerializer)
        payload = {"count": 1, "labels": ["alpha", "beta"], "enabled": True}
        self.assertEqual(payload, serializer.loads_typed(serializer.dumps_typed(payload)))
        with self.assertRaises(Exception):
            serializer.loads_typed(("pickle", b"unsafe"))

        class _UnsafeMarker:
            pass

        with self.assertRaises(Exception):
            serializer.dumps_typed(_UnsafeMarker())


# ---------------------------------------------------------------------------
# Real Postgres
# ---------------------------------------------------------------------------


_TEST_DSN = os.environ.get(TEST_DSN_VARIABLE, "").strip()


class PostgresControlPlaneIntegrationTests(unittest.TestCase):
    """Mandatory real-Postgres races using an explicitly configured test DSN."""

    handle = None

    @classmethod
    def setUpClass(cls) -> None:
        if not _TEST_DSN:
            raise RuntimeError(
                f"{TEST_DSN_VARIABLE} is required for the real-Postgres suite"
            )
        cls.schema = f"director_primary_it_{uuid.uuid4().hex[:8]}_v2"
        cls.handle = open_postgres_control_plane(
            _TEST_DSN,
            schema=cls.schema,
            bounds=PostgresPoolBounds(min_size=1, max_size=6),
            allow_insecure_local_transport=True,
        )
        cls.store = cls.handle.claim_store

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.handle is None:
            return
        try:
            with cls.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
        finally:
            cls.handle.close()

    def setUp(self) -> None:
        self.task_id = f"task-{uuid.uuid4().hex[:10]}"

    def digest(self, marker: str) -> str:
        return "sha256:" + (marker * 64)[:64]

    def own(self, owner_token: str = "owner-a", **overrides: object) -> TaskWriterLease:
        arguments: dict[str, object] = {
            "engine_owner": "director-engine",
            "owner_token": owner_token,
            "expected_generation": INITIAL_FENCING_GENERATION,
        }
        arguments.update(overrides)
        return self.store.acquire_task_writer(self.task_id, **arguments)

    def second_store(self):
        handle = open_postgres_control_plane(
            _TEST_DSN,
            schema=self.schema,
            allow_insecure_local_transport=True,
        )
        self.addCleanup(handle.close)
        return handle.claim_store

    # -- ownership races --------------------------------------------------

    def test_search_path_is_pinned_and_setup_is_idempotent(self):
        with self.handle.pool.connection() as connection:
            row = connection.execute("SHOW search_path").fetchone()
        self.assertEqual(self.schema, str(row["search_path"]).strip('"'))
        self.assertEqual(self.handle.storage_identity, self.second_store().storage_identity)

    def test_work_packet_artifact_survives_restart_and_rejects_mutation(self):
        import psycopg

        lease = self.store.acquire_task_writer(
            self.task_id,
            engine_owner="langgraph",
            owner_token="packet-writer",
            expected_generation=INITIAL_FENCING_GENERATION,
        )
        assignment_id = f"assignment-{uuid.uuid4().hex[:8]}"
        packet = _work_packet(task_id=self.task_id, assignment_id=assignment_id)
        recorded = self.store.record_work_packet_artifact(
            task_id=self.task_id,
            assignment_id=assignment_id,
            packet=packet,
            owner_token="packet-writer",
            fencing_generation=lease.fencing_generation,
        )
        restarted = self.second_store()
        loaded = restarted.read_work_packet_artifact(
            task_id=self.task_id, assignment_id=assignment_id
        )
        self.assertEqual(recorded, loaded)
        self.assertEqual(
            recorded,
            restarted.record_work_packet_artifact(
                task_id=self.task_id,
                assignment_id=assignment_id,
                packet=packet,
                owner_token="packet-writer",
                fencing_generation=lease.fencing_generation,
            ),
        )

        for operation in ("UPDATE", "DELETE"):
            statement = (
                f"{operation} {self.schema}.director_primary_work_packet_artifact "
                + (
                    "SET canonical_document = canonical_document "
                    if operation == "UPDATE"
                    else ""
                )
                + "WHERE task_id = %s AND assignment_id = %s"
            )
            with self.subTest(operation=operation):
                with self.handle.pool.connection() as connection:
                    with self.assertRaises(psycopg.Error):
                        connection.execute(statement, (self.task_id, assignment_id))

    def test_concurrent_work_packet_identity_allows_one_content_only(self):
        lease = self.store.acquire_task_writer(
            self.task_id,
            engine_owner="langgraph",
            owner_token="packet-writer",
            expected_generation=INITIAL_FENCING_GENERATION,
        )
        assignment_id = f"assignment-{uuid.uuid4().hex[:8]}"
        first = _work_packet(task_id=self.task_id, assignment_id=assignment_id)
        second = _work_packet(task_id=self.task_id, assignment_id=assignment_id)
        second["objective"] = "A conflicting packet for the same immutable assignment."
        other = self.second_store()
        barrier = threading.Barrier(2)
        results: list[object] = []

        def attempt(store, packet: dict[str, object]) -> None:
            barrier.wait()
            try:
                results.append(
                    store.record_work_packet_artifact(
                        task_id=self.task_id,
                        assignment_id=assignment_id,
                        packet=packet,
                        owner_token="packet-writer",
                        fencing_generation=lease.fencing_generation,
                    )
                )
            except BaseException as error:  # pragma: no cover - asserted below
                results.append(error)

        threads = [
            threading.Thread(target=attempt, args=(self.store, first)),
            threading.Thread(target=attempt, args=(other, second)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        artifacts = [item for item in results if isinstance(item, PrimaryWorkPacketArtifact)]
        failures = [item for item in results if isinstance(item, PostgresControlPlaneError)]
        self.assertEqual(1, len(artifacts), results)
        self.assertEqual(1, len(failures), results)
        persisted = self.store.read_work_packet_artifact(
            task_id=self.task_id, assignment_id=assignment_id
        )
        self.assertEqual(artifacts[0], persisted)

    def test_result_packet_artifact_survives_restart_and_rejects_mutation(self):
        lease = self.store.acquire_task_writer(
            self.task_id,
            engine_owner="langgraph",
            owner_token="result-writer",
            expected_generation=INITIAL_FENCING_GENERATION,
        )
        assignment_id = f"assignment-{uuid.uuid4().hex[:8]}"
        packet = _result_packet(task_id=self.task_id, assignment_id=assignment_id)
        artifact = self.store.record_result_packet_artifact(
            task_id=self.task_id,
            assignment_id=assignment_id,
            packet=packet,
            owner_token=lease.owner_token,
            fencing_generation=lease.fencing_generation,
        )
        self.assertIsInstance(artifact, PrimaryResultPacketArtifact)
        restarted = self.second_store().read_result_packet_artifact(
            task_id=self.task_id, assignment_id=assignment_id
        )
        self.assertEqual(artifact, restarted)
        mutated = dict(packet)
        mutated["outcome"] = "different authenticated result"
        with self.assertRaisesRegex(PostgresControlPlaneError, "different content"):
            self.store.record_result_packet_artifact(
                task_id=self.task_id,
                assignment_id=assignment_id,
                packet=mutated,
                owner_token=lease.owner_token,
                fencing_generation=lease.fencing_generation,
            )

    def test_parallel_reservation_race_and_crash_ambiguity_hold_slot(self):
        writer = self.store.acquire_task_writer(
            self.task_id,
            engine_owner="langgraph",
            owner_token="parallel-writer",
            expected_generation=INITIAL_FENCING_GENERATION,
        )
        policy = ParallelDispatchPolicy(max_concurrency=1)
        assignments = (
            ParallelAssignment("a", 1, "openai", "codex-app"),
            ParallelAssignment("b", 2, "claude", "claude-app"),
        )
        first = PostgresParallelDispatch(self.store)
        plan_digest = self.digest("a")
        first.register_plan(
            task_id=self.task_id,
            plan_digest=plan_digest,
            assignments=assignments,
            policy=policy,
            writer=writer,
        )
        second = PostgresParallelDispatch(self.second_store())
        barrier = threading.Barrier(2)
        outcomes: list[object] = []

        def reserve(ledger: PostgresParallelDispatch) -> None:
            barrier.wait()
            try:
                outcomes.extend(
                    ledger.reserve_ready(task_id=self.task_id, writer=writer, policy=policy)
                )
            except BaseException as error:  # pragma: no cover - asserted below
                outcomes.append(error)

        threads = [
            threading.Thread(target=reserve, args=(first,)),
            threading.Thread(target=reserve, args=(second,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        failures = [item for item in outcomes if isinstance(item, BaseException)]
        reservations = [item for item in outcomes if not isinstance(item, BaseException)]
        self.assertEqual([], failures)
        self.assertEqual(1, len(reservations), outcomes)
        first.record_result(
            reservation=reservations[0],
            writer=writer,
            status=ParallelAssignmentStatus.AMBIGUOUS,
            result_digest=None,
            reason_code="provider_outcome_unknown",
        )
        restarted = PostgresParallelDispatch(self.second_store())
        self.assertEqual(
            (), restarted.reserve_ready(task_id=self.task_id, writer=writer, policy=policy)
        )
        aggregate = restarted.aggregate(self.task_id)
        self.assertEqual(1, aggregate.ambiguous_slot_count)
        self.assertFalse(aggregate.terminal)

    def test_provider_activation_chain_is_durable_atomic_and_append_only(self):
        import psycopg

        binding = _activation_binding(
            workspace_id=f"workspace-{uuid.uuid4().hex[:8]}"
        )
        first = _activation_evidence(binding=binding)
        self.assertTrue(self.store.append(first, expected_revision=0))
        self.assertTrue(self.second_store().append(first, expected_revision=0))

        candidates = (
            _activation_evidence(
                binding=binding,
                revision=2,
                stage=ProviderActivationStage.AUTHENTICATED,
                evidence_id="auth-observation-a",
                evidence_digest="b" * 64,
            ),
            _activation_evidence(
                binding=binding,
                revision=2,
                stage=ProviderActivationStage.AUTHENTICATED,
                evidence_id="auth-observation-b",
                evidence_digest="c" * 64,
            ),
        )
        other = self.second_store()
        results: list[object] = []
        barrier = threading.Barrier(2)

        def attempt(store, evidence: ProviderActivationEvidence) -> None:
            barrier.wait()
            try:
                results.append(store.append(evidence, expected_revision=1))
            except BaseException as error:  # pragma: no cover - asserted below
                results.append(error)

        threads = [
            threading.Thread(target=attempt, args=(self.store, candidates[0])),
            threading.Thread(target=attempt, args=(other, candidates[1])),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual([True, False], results)

        persisted = self.store.load(binding, ProviderActivationSurface.APP)
        self.assertEqual((1, 2), tuple(item.revision for item in persisted))
        self.assertEqual(1, sum(item in candidates for item in persisted[1:]))
        with self.assertRaises(PostgresControlPlaneError):
            self.store.append(
                _activation_evidence(
                    binding=binding,
                    revision=3,
                    stage=ProviderActivationStage.WORKSPACE_BOUND,
                    evidence_id="workspace-bound",
                    evidence_digest="d" * 64,
                ),
                expected_revision=1,
            )

        with self.handle.pool.connection() as connection:
            with self.assertRaises(psycopg.Error):
                connection.execute(
                    f"DELETE FROM {self.schema}."
                    "director_provider_activation_evidence "
                    "WHERE workspace_id = %s",
                    (binding.workspace_id,),
                )

    def test_two_writers_racing_a_first_acquisition_produce_one_winner(self):
        other = self.second_store()
        results: list[object] = []
        barrier = threading.Barrier(2)

        def attempt(store, token: str) -> None:
            barrier.wait()
            try:
                results.append(store.acquire_task_writer(
                    self.task_id,
                    engine_owner="director-engine",
                    owner_token=token,
                    expected_generation=INITIAL_FENCING_GENERATION,
                ))
            except PostgresControlPlaneError as error:
                results.append(error)

        threads = [
            threading.Thread(target=attempt, args=(self.store, "owner-a")),
            threading.Thread(target=attempt, args=(other, "owner-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        leases = [item for item in results if isinstance(item, TaskWriterLease)]
        failures = [item for item in results if isinstance(item, Exception)]
        self.assertEqual(1, len(leases), results)
        self.assertEqual(1, len(failures), results)
        self.assertEqual(1, leases[0].fencing_generation)
        recorded = self.store.current_task_writer(self.task_id)
        self.assertEqual(leases[0].owner_token, recorded.owner_token)

    def test_two_engines_racing_for_new_intake_produce_one_winner(self):
        other = self.second_store()
        results: list[object] = []
        barrier = threading.Barrier(2)

        def attempt(store, token: str) -> None:
            barrier.wait()
            try:
                results.append(
                    store.acquire_intake_authority(
                        intake_owner="langgraph",
                        owner_token=token,
                        expected_generation=0,
                    )
                )
            except PostgresControlPlaneError as error:
                results.append(error)

        threads = [
            threading.Thread(target=attempt, args=(self.store, "graph-owner-a")),
            threading.Thread(target=attempt, args=(other, "graph-owner-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        leases = [item for item in results if isinstance(item, IntakeAuthorityLease)]
        failures = [item for item in results if isinstance(item, Exception)]
        self.assertEqual(1, len(leases), results)
        self.assertEqual(1, len(failures), results)
        self.assertEqual(1, leases[0].fencing_generation)
        observed = self.store.current_intake_authority()
        self.assertEqual("langgraph", observed.intake_owner)
        self.assertTrue(observed.legacy_new_intake_disabled)
        self.assertEqual(leases[0].owner_token, observed.owner_token)

    def test_stale_generation_and_live_lease_both_refuse_takeover(self):
        lease = self.own()
        with self.subTest(refused="stale generation"):
            with self.assertRaisesRegex(
                PostgresControlPlaneError, "stale fencing generation"
            ):
                self.store.acquire_task_writer(
                    self.task_id,
                    engine_owner="director-engine",
                    owner_token="owner-b",
                    expected_generation=lease.fencing_generation + 5,
                )
        with self.subTest(refused="live lease"):
            with self.assertRaisesRegex(PostgresControlPlaneError, "unexpired lease"):
                self.store.acquire_task_writer(
                    self.task_id,
                    engine_owner="director-engine",
                    owner_token="owner-b",
                    expected_generation=lease.fencing_generation,
                )
        with self.subTest(refused="immutable engine owner"):
            with self.assertRaisesRegex(PostgresControlPlaneError, "immutable"):
                self.store.acquire_task_writer(
                    self.task_id,
                    engine_owner="other-engine",
                    owner_token="owner-a",
                    expected_generation=lease.fencing_generation,
                )
        renewed = self.store.renew_task_writer(
            self.task_id,
            owner_token="owner-a",
            fencing_generation=lease.fencing_generation,
        )
        self.assertEqual(lease.fencing_generation, renewed.fencing_generation)

    def test_a_superseded_generation_loses_all_authority(self):
        lease = self.own()
        key = self.digest("1")
        self.assertTrue(
            self.store.claim(
                _claim_request(claim_key=key, task_id=self.task_id, owner_token="owner-a")
            ).granted
        )
        # A short lease that has already lapsed lets owner-b take over.
        self.store.acquire_task_writer(
            self.task_id,
            engine_owner="director-engine",
            owner_token="owner-a",
            expected_generation=lease.fencing_generation,
            lease_seconds=MIN_LEASE_SECONDS,
        )
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_writer "
                "SET lease_expires_at = now() - interval '1 second', "
                "fencing_generation = fencing_generation + 1 WHERE task_id = %s",
                (self.task_id,),
            )
        self.store.register_task_budget(self.task_id, budget_limit_units=5)
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "current unexpired writer"
        ):
            self.store.reserve_effect_intent(
                reservation_key=self.digest("2"),
                claim_key=key,
                task_id=self.task_id,
                owner_token="owner-a",
                effect_key_digest=self.digest("3"),
                effect_attempt_digest=self.digest("4"),
                writer_owner_id="langgraph-primary",
                reserved_units=1,
            )
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "claim audit event refused"
        ):
            self.store.record_audit_event(
                task_id=self.task_id,
                owner_token="owner-a",
                event_code="effect_claimed",
                payload_digest=self.digest("5"),
                claim_key=key,
            )

    # -- claim races ------------------------------------------------------

    def test_claim_is_granted_once_and_never_moves_ownership(self):
        self.own()
        other = self.second_store()
        key = self.digest("6")
        first = self.store.claim(
            _claim_request(claim_key=key, task_id=self.task_id, owner_token="owner-a")
        )
        self.assertTrue(first.granted)
        self.assertFalse(
            self.store.claim(
                _claim_request(claim_key=key, task_id=self.task_id, owner_token="owner-a")
            ).granted
        )
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "unexpired writer lease"
        ):
            other.claim(
                _claim_request(
                    claim_key=self.digest("7"),
                    task_id=self.task_id,
                    owner_token="owner-b",
                )
            )
        recorded = self.store.current_task_writer(self.task_id)
        self.assertEqual("owner-a", recorded.owner_token)
        self.assertEqual(1, recorded.fencing_generation)

    # -- reservation races ------------------------------------------------

    def prepare_claim(self, marker: str, *, limit: int = 4) -> str:
        self.own()
        key = self.digest(marker)
        self.store.claim(
            _claim_request(
                claim_key=key,
                task_id=self.task_id,
                owner_token="owner-a",
                attempt_digest=self.digest(marker + "0"),
            )
        )
        self.store.register_task_budget(self.task_id, budget_limit_units=limit)
        return key

    def reserve(self, store, key: str, marker: str, units: int = 2):
        return store.reserve_effect_intent(
            reservation_key=self.digest(marker),
            claim_key=key,
            task_id=self.task_id,
            owner_token="owner-a",
            effect_key_digest=self.digest("e"),
            effect_attempt_digest=self.digest("f"),
            writer_owner_id="langgraph-primary",
            reserved_units=units,
        )

    def reserved_units(self) -> int:
        with self.handle.pool.connection() as connection:
            row = connection.execute(
                f"SELECT budget_reserved_units FROM {self.schema}.director_primary_budget "
                "WHERE task_id = %s",
                (self.task_id,),
            ).fetchone()
        return int(row["budget_reserved_units"])

    def test_concurrent_identical_reservations_charge_budget_once(self):
        other = self.second_store()
        key = self.prepare_claim("8")
        results: list[object] = []
        barrier = threading.Barrier(2)

        def attempt(store) -> None:
            barrier.wait()
            try:
                results.append(self.reserve(store, key, "9"))
            except Exception as error:  # noqa: BLE001 - recorded for assertion
                results.append(error)

        threads = [
            threading.Thread(target=attempt, args=(self.store,)),
            threading.Thread(target=attempt, args=(other,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(2, len(results), results)
        for item in results:
            self.assertIsInstance(item, EffectReservation, results)
            self.assertEqual(self.digest("9"), item.reservation_key)
        self.assertEqual(2, self.reserved_units())

    def test_a_different_reservation_for_the_same_claim_is_refused(self):
        key = self.prepare_claim("a")
        self.reserve(self.store, key, "b")
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "different effect reservation"
        ):
            self.reserve(self.store, key, "c")
        self.assertEqual(2, self.reserved_units())

    def test_reconciliation_blocks_new_reservations_but_not_readback_or_evidence(self):
        key = self.prepare_claim("1b")
        reservation = self.reserve(self.store, key, "2b")
        self.store.bind_remote_effect(
            reservation_key=reservation.reservation_key,
            owner_token="owner-a",
            remote_system="provider",
            remote_identifier="remote-1",
        )
        self.store.record_reconciliation_event(
            claim_key=key,
            owner_token="owner-a",
            state="AMBIGUOUS",
            evidence_digest=self.digest("3b"),
        )
        second_key = self.digest("4b")
        self.store.claim(
            _claim_request(
                claim_key=second_key,
                task_id=self.task_id,
                owner_token="owner-a",
                attempt_digest=self.digest("5b"),
            )
        )
        with self.subTest(step="an identical replay of the blocked claim still works"):
            self.assertEqual(
                reservation.reservation_key,
                self.reserve(self.store, key, "2b").reservation_key,
            )
        with self.subTest(step="a new reservation for the blocked claim is refused"):
            # The block is on the reconciled claim, not on the task.
            self.store.record_reconciliation_event(
                claim_key=second_key,
                owner_token="owner-a",
                state="RECONCILIATION_REQUIRED",
                evidence_digest=self.digest("6b"),
            )
            with self.assertRaisesRegex(
                PostgresControlPlaneError, "under reconciliation"
            ):
                self.reserve(self.store, second_key, "7b")
        with self.subTest(step="authenticated readback is still permitted"):
            self.assertEqual(
                {"remote_system": "provider", "remote_identifier": "remote-1"},
                self.store.read_remote_binding(
                    reservation_key=reservation.reservation_key, owner_token="owner-a"
                ),
            )
        with self.subTest(step="one final terminal evidence row is still permitted"):
            self.store.record_terminal_evidence(
                claim_key=key,
                owner_token="owner-a",
                disposition=FINAL_DISPOSITIONS[0],
                evidence_digest=self.digest("8b"),
            )
            with self.assertRaisesRegex(
                PostgresControlPlaneError, "different outcome"
            ):
                self.store.record_terminal_evidence(
                    claim_key=key,
                    owner_token="owner-a",
                    disposition=FINAL_DISPOSITIONS[-1],
                    evidence_digest=self.digest("8b"),
                )
        with self.subTest(step="ambiguity is never terminal evidence"):
            with self.assertRaisesRegex(
                PostgresControlPlaneError, "not terminal evidence"
            ):
                self.store.record_terminal_evidence(
                    claim_key=second_key,
                    owner_token="owner-a",
                    disposition="AMBIGUOUS",
                    evidence_digest=self.digest("9b"),
                )

    def test_expired_claim_is_reconciled_only_by_a_strictly_newer_successor(self):
        key = self.prepare_claim("d1", limit=3)
        reservation = self.reserve(self.store, key, "d2", units=1)
        self.store.bind_remote_effect(
            reservation_key=reservation.reservation_key,
            owner_token="owner-a",
            remote_system="provider",
            remote_identifier="remote-successor",
        )
        historical = self.store.current_task_writer(self.task_id)
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_writer "
                "SET lease_expires_at = now() - interval '1 second' "
                "WHERE task_id = %s",
                (self.task_id,),
            )
        successor = self.store.acquire_task_writer(
            self.task_id,
            engine_owner="director-engine",
            owner_token="owner-b",
            expected_generation=historical.fencing_generation,
        )
        self.assertGreater(
            successor.fencing_generation, historical.fencing_generation
        )

        self.assertEqual(
            {
                "remote_system": "provider",
                "remote_identifier": "remote-successor",
            },
            self.store.read_remote_binding_for_successor_reconciliation(
                reservation_key=reservation.reservation_key,
                successor_owner_token="owner-b",
            ),
        )
        self.store.record_successor_reconciliation_event(
            claim_key=key,
            successor_owner_token="owner-b",
            state="RECONCILIATION_REQUIRED",
            evidence_digest=self.digest("d3"),
        )
        terminal_arguments = {
            "claim_key": key,
            "successor_owner_token": "owner-b",
            "disposition": FINAL_DISPOSITIONS[0],
            "evidence_digest": self.digest("d4"),
        }
        self.store.record_successor_terminal_evidence(**terminal_arguments)
        self.store.record_successor_terminal_evidence(**terminal_arguments)
        with self.handle.pool.connection() as connection:
            event = connection.execute(
                f"SELECT owner_token, fencing_generation FROM {self.schema}."
                "director_primary_reconciliation_event WHERE claim_key = %s",
                (key,),
            ).fetchone()
            terminal = connection.execute(
                f"SELECT owner_token, fencing_generation FROM {self.schema}."
                "director_primary_terminal_evidence WHERE claim_key = %s",
                (key,),
            ).fetchone()
            terminal_count = connection.execute(
                f"SELECT count(*) AS count FROM {self.schema}."
                "director_primary_terminal_evidence WHERE claim_key = %s",
                (key,),
            ).fetchone()
        for recorded in (event, terminal):
            self.assertEqual("owner-b", recorded["owner_token"])
            self.assertEqual(
                successor.fencing_generation, int(recorded["fencing_generation"])
            )
        self.assertEqual(1, int(terminal_count["count"]))

        with self.subTest(refused="successor cannot create a remote binding"):
            with self.assertRaises(PostgresControlPlaneError):
                self.store.bind_remote_effect(
                    reservation_key=reservation.reservation_key,
                    owner_token="owner-b",
                    remote_system="provider",
                    remote_identifier="remote-successor",
                )
        with self.subTest(refused="successor cannot create a new reservation"):
            with self.assertRaises(PostgresControlPlaneError):
                self.store.reserve_effect_intent(
                    reservation_key=self.digest("d5"),
                    claim_key=key,
                    task_id=self.task_id,
                    owner_token="owner-b",
                    effect_key_digest=self.digest("d6"),
                    effect_attempt_digest=self.digest("d7"),
                    writer_owner_id="langgraph-primary",
                    reserved_units=1,
                )
        with self.subTest(refused="historical owner has no audit authority"):
            with self.assertRaises(PostgresControlPlaneError):
                self.store.record_audit_event(
                    task_id=self.task_id,
                    owner_token="owner-a",
                    event_code="effect_execute",
                    payload_digest=self.digest("d8"),
                    claim_key=key,
                )

    def test_successor_terminal_lock_blocks_takeover_until_insert_commits(self):
        key = self.prepare_claim("e1", limit=2)
        self.reserve(self.store, key, "e2", units=1)
        historical = self.store.current_task_writer(self.task_id)
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_writer "
                "SET lease_expires_at = now() - interval '1 second' "
                "WHERE task_id = %s",
                (self.task_id,),
            )
        successor = self.store.acquire_task_writer(
            self.task_id,
            engine_owner="director-engine",
            owner_token="owner-b",
            expected_generation=historical.fencing_generation,
        )

        authority_locked = threading.Event()
        allow_terminal_insert = threading.Event()
        mutation_started = threading.Event()
        mutation_finished = threading.Event()
        errors: list[BaseException] = []
        original_optional_row = PostgresConditionalClaimStore._optional_row

        def gated_optional_row(connection, statement, parameters):
            row = original_optional_row(connection, statement, parameters)
            if (
                "FOR UPDATE OF held, successor" in statement
                and parameters.get("claim_key") == key
            ):
                authority_locked.set()
                if not allow_terminal_insert.wait(5):
                    raise RuntimeError("test did not release terminal insertion")
            return row

        def finalize() -> None:
            try:
                self.store.record_successor_terminal_evidence(
                    claim_key=key,
                    successor_owner_token="owner-b",
                    disposition=FINAL_DISPOSITIONS[0],
                    evidence_digest=self.digest("e3"),
                )
            except BaseException as error:  # pragma: no cover - asserted below
                errors.append(error)

        def expire_successor() -> None:
            mutation_started.set()
            try:
                with self.handle.pool.connection() as connection:
                    connection.execute(
                        f"UPDATE {self.schema}.director_primary_task_writer "
                        "SET lease_expires_at = now() - interval '1 second' "
                        "WHERE task_id = %s",
                        (self.task_id,),
                    )
            except BaseException as error:  # pragma: no cover - asserted below
                errors.append(error)
            finally:
                mutation_finished.set()

        terminal_thread = threading.Thread(target=finalize)
        mutation_thread = threading.Thread(target=expire_successor)
        with patch.object(
            PostgresConditionalClaimStore,
            "_optional_row",
            new=staticmethod(gated_optional_row),
        ):
            terminal_thread.start()
            try:
                self.assertTrue(authority_locked.wait(5))
                mutation_thread.start()
                self.assertTrue(mutation_started.wait(5))
                mutation_was_blocked = not mutation_finished.wait(0.25)
            finally:
                allow_terminal_insert.set()
                terminal_thread.join(5)
                mutation_thread.join(5)

        self.assertTrue(mutation_was_blocked)
        self.assertFalse(terminal_thread.is_alive())
        self.assertFalse(mutation_thread.is_alive())
        self.assertEqual([], errors)
        next_writer = self.store.acquire_task_writer(
            self.task_id,
            engine_owner="director-engine",
            owner_token="owner-c",
            expected_generation=successor.fencing_generation,
        )
        self.assertGreater(
            next_writer.fencing_generation, successor.fencing_generation
        )
        with self.handle.pool.connection() as connection:
            terminal = connection.execute(
                f"SELECT owner_token, fencing_generation FROM {self.schema}."
                "director_primary_terminal_evidence WHERE claim_key = %s",
                (key,),
            ).fetchone()
        self.assertEqual("owner-b", terminal["owner_token"])
        self.assertEqual(
            successor.fencing_generation, int(terminal["fencing_generation"])
        )

    def test_a_non_owner_replay_is_refused_even_with_an_identical_payload(self):
        key = self.prepare_claim("1c")
        reservation = self.reserve(self.store, key, "2c")
        self.store.bind_remote_effect(
            reservation_key=reservation.reservation_key,
            owner_token="owner-a",
            remote_system="provider",
            remote_identifier="remote-9",
        )
        for label, call in {
            "binding": lambda: self.store.bind_remote_effect(
                reservation_key=reservation.reservation_key,
                owner_token="owner-impostor",
                remote_system="provider",
                remote_identifier="remote-9",
            ),
            "readback": lambda: self.store.read_remote_binding(
                reservation_key=reservation.reservation_key,
                owner_token="owner-impostor",
            ),
            "evidence": lambda: self.store.record_terminal_evidence(
                claim_key=key,
                owner_token="owner-impostor",
                disposition=FINAL_DISPOSITIONS[0],
                evidence_digest=self.digest("3c"),
            ),
        }.items():
            with self.subTest(refused=label):
                with self.assertRaises(PostgresControlPlaneError):
                    call()

    def test_a_remote_identifier_cannot_be_reused_across_reservations(self):
        first_key = self.prepare_claim("1d")
        first = self.reserve(self.store, first_key, "2d", units=1)
        self.store.bind_remote_effect(
            reservation_key=first.reservation_key,
            owner_token="owner-a",
            remote_system="provider",
            remote_identifier="shared-remote",
        )
        second_key = self.digest("3d")
        self.store.claim(
            _claim_request(
                claim_key=second_key,
                task_id=self.task_id,
                owner_token="owner-a",
                attempt_digest=self.digest("4d"),
            )
        )
        second = self.reserve(self.store, second_key, "5d", units=1)
        with self.assertRaisesRegex(
            PostgresControlPlaneError, "already bound to a different reservation"
        ):
            self.store.bind_remote_effect(
                reservation_key=second.reservation_key,
                owner_token="owner-a",
                remote_system="provider",
                remote_identifier="shared-remote",
            )

    def test_claims_and_audit_events_are_append_only(self):
        import psycopg

        key = self.prepare_claim("1e")
        self.store.record_audit_event(
            task_id=self.task_id,
            owner_token="owner-a",
            event_code="effect_claimed",
            payload_digest=self.digest("2e"),
            claim_key=key,
        )
        self.store.record_audit_event(
            task_id=self.task_id,
            owner_token="owner-a",
            event_code="task_started",
            payload_digest=self.digest("3e"),
        )
        for statement in (
            f"DELETE FROM {self.schema}.director_primary_claim WHERE claim_key = %s",
            f"UPDATE {self.schema}.director_primary_claim SET owner_token = 'x' "
            "WHERE claim_key = %s",
            f"DELETE FROM {self.schema}.director_primary_audit_event "
            "WHERE claim_key = %s",
        ):
            with self.subTest(statement=" ".join(statement.split()[:2])):
                with self.handle.pool.connection() as connection:
                    with self.assertRaises(psycopg.Error):
                        connection.execute(statement, (key,))


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:35 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:05 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:22 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:28 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-31 | 10:35 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-31 | 12:40 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-31 | 15:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:08 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:35 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:58 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:14 UTC | END]]
