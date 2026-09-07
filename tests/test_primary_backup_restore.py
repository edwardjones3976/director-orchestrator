# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:06 UTC | BEGIN]]
"""Disposable logical backup/restore proof for the primary Postgres schema.

This test is intentionally opt-in.  It creates and destroys one uniquely named
schema in the database selected by ``LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN`` and
runs the locally installed ``pg_dump`` and ``pg_restore`` executables.  The DSN
must therefore identify a disposable database whose user may create and drop
schemas.  No connection string or subprocess output is emitted.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint
from psycopg.conninfo import conninfo_to_dict

from director_orchestrator.langgraph_postgres import (
    INITIAL_FENCING_GENERATION,
    PostgresPoolBounds,
    PostgresControlPlaneError,
    open_postgres_control_plane,
)
from director_orchestrator.langgraph_primary import ClaimRequest


TEST_DSN_VARIABLE = "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN"
_TEST_DSN = os.environ.get(TEST_DSN_VARIABLE, "").strip()
_PG_DUMP = shutil.which("pg_dump")
_PG_RESTORE = shutil.which("pg_restore")
_TEST_CONTAINER = os.environ.get(
    "LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER", ""
).strip()
_DOCKER = shutil.which("docker")
_CONTAINER_TOOLS = bool(
    _TEST_CONTAINER
    and _DOCKER
    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", _TEST_CONTAINER)
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _postgres_environment(dsn: str) -> dict[str, str]:
    """Translate libpq connection fields to child environment variables."""

    fields = conninfo_to_dict(dsn)
    mapping = {
        "host": "PGHOST",
        "hostaddr": "PGHOSTADDR",
        "port": "PGPORT",
        "dbname": "PGDATABASE",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "passfile": "PGPASSFILE",
        "service": "PGSERVICE",
        "servicefile": "PGSERVICEFILE",
        "connect_timeout": "PGCONNECT_TIMEOUT",
        "sslmode": "PGSSLMODE",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcrl": "PGSSLCRL",
        "sslcrldir": "PGSSLCRLDIR",
        "sslsni": "PGSSLSNI",
        "ssl_min_protocol_version": "PGSSLMINPROTOCOLVERSION",
        "ssl_max_protocol_version": "PGSSLMAXPROTOCOLVERSION",
        "target_session_attrs": "PGTARGETSESSIONATTRS",
        "options": "PGOPTIONS",
    }
    unsupported = sorted(set(fields).difference(mapping))
    if unsupported:
        raise AssertionError(
            "disposable Postgres DSN contains connection fields not supported by "
            "the credential-safe backup harness"
        )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != TEST_DSN_VARIABLE and not key.upper().startswith("PG")
    }
    for field, value in fields.items():
        if value is not None:
            environment[mapping[field]] = str(value)
    return environment


def _run_backup_tool(argv: list[str], *, environment: dict[str, str]) -> None:
    """Run one fixed backup command without a shell or credential-bearing argv."""

    try:
        completed = subprocess.run(
            argv,
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        tool = Path(argv[0]).name
        raise AssertionError(f"{tool} timed out; output suppressed") from error
    if completed.returncode != 0:
        tool = Path(argv[0]).name
        raise AssertionError(
            f"{tool} failed with exit code {completed.returncode}; output suppressed"
        )


class PrimaryBackupRestoreTests(unittest.TestCase):
    """Prove identity-preserving restore of checkpoints and the control plane."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _TEST_DSN:
            raise RuntimeError(f"{TEST_DSN_VARIABLE} is required for backup proof")
        if not ((_PG_DUMP and _PG_RESTORE) or _CONTAINER_TOOLS):
            raise RuntimeError(
                "pg_dump/pg_restore or the configured disposable container is required"
            )

    def test_logical_backup_restore_preserves_primary_identities(self) -> None:
        schema = f"director_primary_backup_{uuid.uuid4().hex[:12]}_v2"
        task_id = f"backup-task-{uuid.uuid4().hex}"
        intake_token = f"intake-{uuid.uuid4().hex}"
        writer_token = f"writer-{uuid.uuid4().hex}"
        approval_id = f"approval-{uuid.uuid4().hex}"
        claim_key = _digest(f"claim:{task_id}")
        attempt_digest = _digest(f"attempt:{task_id}")
        reservation_key = _digest(f"reservation:{task_id}")
        effect_key_digest = _digest(f"effect:{task_id}")
        effect_attempt_digest = _digest(f"effect-attempt:{task_id}")
        input_digest = _digest(f"input:{task_id}")
        idempotency_digest = _digest(f"idempotency:{task_id}")
        payload_digest = _digest(f"payload:{task_id}")
        environment = _postgres_environment(_TEST_DSN)
        handle = None

        try:
            handle = open_postgres_control_plane(
                _TEST_DSN,
                schema=schema,
                bounds=PostgresPoolBounds(min_size=1, max_size=2),
                allow_insecure_local_transport=True,
            )
            store = handle.claim_store
            intake = store.acquire_intake_authority(
                intake_owner="langgraph",
                owner_token=intake_token,
                expected_generation=INITIAL_FENCING_GENERATION,
                lease_seconds=3600,
            )
            projection_value = {
                "task_id": task_id,
                "phase": "WAITING_APPROVAL",
                "approval_id": approval_id,
            }
            projection, created = store.create_primary_task_projection(
                task_id=task_id,
                input_digest=input_digest,
                idempotency_key_digest=idempotency_digest,
                dedup_payload_digest=payload_digest,
                owner_token=intake_token,
                intake_fencing_generation=intake.fencing_generation,
                projection=projection_value,
            )
            self.assertTrue(created)
            writer = store.acquire_task_writer(
                task_id,
                engine_owner="langgraph",
                owner_token=writer_token,
                expected_generation=INITIAL_FENCING_GENERATION,
                lease_seconds=3600,
            )
            store.register_task_budget(task_id, budget_limit_units=5)
            claim_request = ClaimRequest(
                claim_key=claim_key,
                task_id=task_id,
                field="effect_status",
                expected_state="CLAIMED",
                target_state="EXECUTING",
                attempt_digest=attempt_digest,
                owner_token=writer_token,
            )
            self.assertTrue(store.claim(claim_request).granted)
            reservation = store.reserve_effect_intent(
                reservation_key=reservation_key,
                claim_key=claim_key,
                task_id=task_id,
                owner_token=writer_token,
                effect_key_digest=effect_key_digest,
                effect_attempt_digest=effect_attempt_digest,
                writer_owner_id="langgraph-primary",
                reserved_units=2,
            )
            remote_identity = {
                "remote_system": "provider-double",
                "remote_identifier": f"remote-{uuid.uuid4().hex}",
            }
            store.bind_remote_effect(
                reservation_key=reservation_key,
                owner_token=writer_token,
                **remote_identity,
            )
            store.record_audit_event(
                task_id=task_id,
                owner_token=writer_token,
                event_code="backup_fixture_created",
                payload_digest=_digest(f"audit-before:{task_id}"),
                claim_key=claim_key,
            )

            approval = {
                "approval_id": approval_id,
                "status": "APPROVED",
                "scope_digest": _digest(f"approval:{task_id}"),
            }
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {
                "task_id": task_id,
                "approval": approval,
            }
            checkpoint["channel_versions"] = {"task_id": "1", "approval": "1"}
            checkpoint_config = {
                "configurable": {
                    "thread_id": f"director-primary-v1:{task_id}",
                    "checkpoint_ns": "primary-backup-restore-v1",
                }
            }
            saved_config = handle.saver.put(
                checkpoint_config,
                checkpoint,
                {
                    "source": "update",
                    "step": 1,
                    "writes": {"approval": approval},
                },
                {"task_id": "1", "approval": "1"},
            )

            handle.close()
            handle = None
            import psycopg

            if _CONTAINER_TOOLS:
                connection_fields = conninfo_to_dict(_TEST_DSN)
                container_user = connection_fields.get("user")
                container_database = connection_fields.get("dbname")
                if not container_user or not container_database:
                    raise AssertionError(
                        "disposable Postgres DSN must identify its user and database"
                    )
                dump_name = f"/tmp/{schema}.dump"
                try:
                    _run_backup_tool(
                        [
                            str(_DOCKER),
                            "exec",
                            _TEST_CONTAINER,
                            "pg_dump",
                            "-U",
                            str(container_user),
                            "-d",
                            str(container_database),
                            "--format=custom",
                            "--no-owner",
                            "--no-privileges",
                            f"--schema={schema}",
                            f"--file={dump_name}",
                        ],
                        environment=environment,
                    )
                    with psycopg.connect(_TEST_DSN, autocommit=True) as connection:
                        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
                    _run_backup_tool(
                        [
                            str(_DOCKER),
                            "exec",
                            _TEST_CONTAINER,
                            "pg_restore",
                            "-U",
                            str(container_user),
                            "-d",
                            str(container_database),
                            "--no-owner",
                            "--no-privileges",
                            dump_name,
                        ],
                        environment=environment,
                    )
                finally:
                    subprocess.run(
                        [
                            str(_DOCKER),
                            "exec",
                            _TEST_CONTAINER,
                            "rm",
                            "--force",
                            dump_name,
                        ],
                        shell=False,
                        check=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=30,
                    )
            else:
                with tempfile.TemporaryDirectory() as temporary:
                    dump_path = Path(temporary) / "primary-control-plane.dump"
                    _run_backup_tool(
                        [
                            str(_PG_DUMP),
                            "--format=custom",
                            "--no-owner",
                            "--no-privileges",
                            f"--schema={schema}",
                            f"--file={dump_path}",
                        ],
                        environment=environment,
                    )
                    self.assertGreater(dump_path.stat().st_size, 0)
                    with psycopg.connect(_TEST_DSN, autocommit=True) as connection:
                        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
                    _run_backup_tool(
                        [
                            str(_PG_RESTORE),
                            "--dbname",
                            environment["PGDATABASE"],
                            "--exit-on-error",
                            "--no-owner",
                            "--no-privileges",
                            str(dump_path),
                        ],
                        environment=environment,
                    )

            handle = open_postgres_control_plane(
                _TEST_DSN,
                schema=schema,
                bounds=PostgresPoolBounds(min_size=1, max_size=2),
                allow_insecure_local_transport=True,
            )
            restored = handle.claim_store

            restored_checkpoint = handle.saver.get_tuple(saved_config)
            self.assertIsNotNone(restored_checkpoint)
            self.assertEqual(
                approval,
                restored_checkpoint.checkpoint["channel_values"]["approval"],
            )
            self.assertEqual(
                task_id,
                restored_checkpoint.checkpoint["channel_values"]["task_id"],
            )

            restored_projection = restored.read_primary_task_projection(task_id=task_id)
            self.assertIsNotNone(restored_projection)
            self.assertEqual(projection.task_id, restored_projection.task_id)
            self.assertEqual(projection.input_digest, restored_projection.input_digest)
            self.assertEqual(
                projection.intake_fencing_generation,
                restored_projection.intake_fencing_generation,
            )
            self.assertEqual(projection.projection_digest, restored_projection.projection_digest)
            self.assertEqual(projection_value, restored_projection.projection)

            restored_intake = restored.current_intake_authority()
            self.assertEqual("langgraph", restored_intake.intake_owner)
            self.assertEqual(intake_token, restored_intake.owner_token)
            self.assertEqual(intake.fencing_generation, restored_intake.fencing_generation)
            self.assertTrue(restored_intake.legacy_new_intake_disabled)

            restored_writer = restored.current_task_writer(task_id)
            self.assertIsNotNone(restored_writer)
            self.assertEqual(writer_token, restored_writer.owner_token)
            self.assertEqual(writer.fencing_generation, restored_writer.fencing_generation)
            replay = restored.claim(claim_request)
            self.assertFalse(replay.granted)
            self.assertEqual(claim_key, replay.claim_key)
            self.assertEqual(writer_token, replay.owner_token)
            with self.assertRaises(PostgresControlPlaneError):
                restored.claim(
                    ClaimRequest(
                        claim_key=_digest(f"impostor-claim:{task_id}"),
                        task_id=task_id,
                        field="effect_status",
                        expected_state="CLAIMED",
                        target_state="EXECUTING",
                        attempt_digest=attempt_digest,
                        owner_token="stale-or-impostor-owner",
                    )
                )

            replayed_reservation = restored.reserve_effect_intent(
                reservation_key=reservation_key,
                claim_key=claim_key,
                task_id=task_id,
                owner_token=writer_token,
                effect_key_digest=effect_key_digest,
                effect_attempt_digest=effect_attempt_digest,
                writer_owner_id="langgraph-primary",
                reserved_units=2,
            )
            self.assertEqual(reservation, replayed_reservation)
            restored.bind_remote_effect(
                reservation_key=reservation_key,
                owner_token=writer_token,
                **remote_identity,
            )
            self.assertEqual(
                remote_identity,
                restored.read_remote_binding(
                    reservation_key=reservation_key,
                    owner_token=writer_token,
                ),
            )
            restored.record_audit_event(
                task_id=task_id,
                owner_token=writer_token,
                event_code="backup_fixture_restored",
                payload_digest=_digest(f"audit-after:{task_id}"),
                claim_key=claim_key,
            )
            with handle.pool.connection() as connection:
                counts = connection.execute(
                    f"""
                    SELECT
                        (SELECT count(*) FROM {schema}.director_primary_claim
                         WHERE claim_key = %s) AS claims,
                        (SELECT count(*) FROM {schema}.director_primary_effect_reservation
                         WHERE reservation_key = %s) AS reservations,
                        (SELECT count(*) FROM {schema}.director_primary_remote_binding
                         WHERE reservation_key = %s) AS bindings
                    """,
                    (claim_key, reservation_key, reservation_key),
                ).fetchone()
                audit_ids = connection.execute(
                    f"SELECT event_id FROM {schema}.director_primary_audit_event "
                    "WHERE task_id = %s ORDER BY event_id",
                    (task_id,),
                ).fetchall()
            self.assertEqual(1, int(counts["claims"]))
            self.assertEqual(1, int(counts["reservations"]))
            self.assertEqual(1, int(counts["bindings"]))
            self.assertEqual(2, len(audit_ids))
            self.assertLess(int(audit_ids[0]["event_id"]), int(audit_ids[1]["event_id"]))
        finally:
            if handle is not None:
                try:
                    with handle.pool.connection() as connection:
                        connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                finally:
                    handle.close()
            else:
                # The first handle was deliberately closed before restore.  If
                # backup/restore failed, use a short-lived direct connection to
                # remove any remaining disposable schema without exposing DSN.
                try:
                    import psycopg

                    with psycopg.connect(_TEST_DSN, autocommit=True) as connection:
                        connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:06 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:00 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 02:23 UTC | END]]
