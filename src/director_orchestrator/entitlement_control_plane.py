# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 20:05 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:25 UTC | BEGIN]]
"""Default-off PostgreSQL entitlement control plane."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .entitlement_contracts import (
    ENTITLEMENT_EVENT_VERSION,
    INSTALL_ATTESTATION_VERSION,
    NOTICE_SUSPENSION_SECONDS,
    OWNER_NOTICE_VERSION,
    PROJECT_REGISTRATION_VERSION,
    SIGNED_TEMPLATE_VERSION,
    ACTION_CAPABILITY,
    SPEND_BEARING_ACTIONS,
    EntitlementActionType,
    EntitlementActionBoundaryQuery,
    EntitlementActionBoundaryResult,
    EntitlementContractError,
    EntitlementEventType,
    EntitlementEventV1,
    EntitlementLifecycleStatus,
    EntitlementOwnerActionV1,
    InstallAttestationV1,
    OwnerNoticeDeliveryState,
    OwnerNoticeV1,
    NoticeRouteProofV1,
    ProjectRegistrationV1,
    RepositoryScope,
    RuntimeEvidenceProofV1,
    SignedEntitlementTemplateV1,
    SlackDestinationHealthProofV1,
    SubjectKind,
    authority_bearing_capabilities,
    composed_template_digest,
    document_digest,
    effective_capabilities,
    signing_bytes,
    verify_signature,
)
from .langgraph_postgres import (
    PostgresCheckpointConfigurationError,
    _parse_connection_target,
)

ENTITLEMENT_MANIFEST_VERSION = "entitlement-control-plane-manifest/v1"
DEFAULT_ENTITLEMENT_SCHEMA = "entitlement_control_plane_v1"
_SCHEMA_PATTERN = re.compile(r"^[a-z0-9_]+_v[0-9]+$")


class EntitlementControlPlaneError(RuntimeError):
    """An entitlement operation was refused. Nothing is partially applied."""


@dataclass(frozen=True, slots=True)
class EntitlementGrantOutcome:
    granted_capabilities: tuple[str, ...]
    entitlement_generation: int
    notice: OwnerNoticeV1
    event_id: str
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class EntitlementStoreHandle:
    pool: Any
    schema: str
    storage_identity: str
    trusted_verifier_secret: bytes
    route_verifier_secret: bytes
    owner_authority_secret: bytes

    def close(self) -> None:
        self.pool.close()


def _require_schema(schema: str) -> str:
    if not _SCHEMA_PATTERN.fullmatch(schema):
        raise EntitlementControlPlaneError(
            "entitlement schema must match [a-z0-9_]+_v<N>"
        )
    return schema


def _validate_secret_material(value: bytes, *, label: str) -> bytes:
    if len(value) < 32 or len(set(value)) < 16:
        raise EntitlementControlPlaneError(
            f"{label} must contain at least 32 bytes of nontrivial entropy"
        )
    return value


def _read_secret_file(path: str) -> bytes:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise EntitlementControlPlaneError("secret file is missing")
    value = resolved.read_text(encoding="utf-8").strip()
    if not value:
        raise EntitlementControlPlaneError("secret file is empty")
    encoded = value.encode("utf-8")
    return _validate_secret_material(encoded, label="secret")


def _storage_identity(parsed: Mapping[str, str], *, schema: str) -> str:
    target = {
        "host": parsed.get("host", ""),
        "hostaddr": parsed.get("hostaddr", ""),
        "port": parsed.get("port", ""),
        "dbname": parsed.get("dbname", ""),
        "user": parsed.get("user", ""),
        "schema": schema,
    }
    return document_digest(target)


def _entitlement_ddl(schema: str) -> tuple[str, ...]:
    schema = _require_schema(schema)
    reject = (
        f"CREATE OR REPLACE FUNCTION {schema}.entitlement_reject_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'immutable entitlement row'; END; $$"
    )
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_schema_manifest (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            manifest_version text NOT NULL,
            schema_version text NOT NULL,
            storage_identity text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_signed_template (
            profile_template_id text PRIMARY KEY,
            template_digest text NOT NULL CHECK (template_digest ~ '^[0-9a-f]{{64}}$'),
            capabilities jsonb NOT NULL,
            owner_policy_digest text NOT NULL,
            owner_signature_principal text NOT NULL,
            owner_signature_digest text NOT NULL,
            registered_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_slack_destination (
            destination_id text PRIMARY KEY,
            verified boolean NOT NULL,
            owner_signature_principal text NOT NULL,
            owner_signature_digest text NOT NULL,
            health_proof_document jsonb NOT NULL,
            health_proof_digest text NOT NULL,
            health_proof_nonce_digest text NOT NULL UNIQUE,
            health_proof_expires_at timestamptz NOT NULL,
            verified_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_project_registration (
            project_profile_id text NOT NULL,
            registration_generation bigint NOT NULL CHECK (registration_generation >= 1),
            schema_version text NOT NULL,
            document jsonb NOT NULL,
            document_digest text NOT NULL,
            profile_template_id text NOT NULL,
            owner_policy_digest text NOT NULL,
            slack_destination_id text NOT NULL,
            registered_at timestamptz NOT NULL DEFAULT now(),
            superseded_at timestamptz,
            PRIMARY KEY (project_profile_id, registration_generation)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_install_attestation (
            attestation_id text PRIMARY KEY,
            schema_version text NOT NULL,
            document_digest text NOT NULL,
            installation_id text NOT NULL,
            nonce_digest text NOT NULL UNIQUE,
            document jsonb NOT NULL,
            build_evidence_digest text NOT NULL,
            test_evidence_digest text NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_event (
            event_id text PRIMARY KEY,
            schema_version text NOT NULL,
            event_type text NOT NULL,
            installation_id text NOT NULL,
            entitlement_generation bigint NOT NULL,
            expected_generation bigint,
            actor_id text NOT NULL,
            idempotency_key_digest text NOT NULL UNIQUE,
            scope_digest text NOT NULL,
            evidence_binding_digest text NOT NULL,
            document jsonb NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_current_generation (
            installation_id text PRIMARY KEY,
            project_profile_id text NOT NULL,
            subject_kind text NOT NULL,
            subject_id text NOT NULL,
            provider_id text NOT NULL,
            registration_generation bigint NOT NULL,
            entitlement_generation bigint NOT NULL CHECK (entitlement_generation >= 1),
            profile_template_id text NOT NULL,
            template_digest text NOT NULL,
            registration_document_digest text NOT NULL,
            granted_capabilities jsonb NOT NULL,
            granted_capabilities_digest text NOT NULL,
            build_digest text NOT NULL,
            source_commit_sha text NOT NULL,
            source_tree_sha text NOT NULL,
            lifecycle_status text NOT NULL,
            notice_id text NOT NULL,
            notice_delivery_state text NOT NULL,
            notice_pending_since timestamptz NOT NULL,
            granted_at timestamptz NOT NULL,
            last_event_id text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_owner_notice (
            notice_id text PRIMARY KEY,
            schema_version text NOT NULL,
            installation_id text NOT NULL,
            entitlement_generation bigint NOT NULL,
            delivery_state text NOT NULL,
            document jsonb NOT NULL,
            delivery_proof_digest text,
            pending_since timestamptz NOT NULL,
            delivered_at timestamptz,
            failed_at timestamptz,
            acknowledged_at timestamptz,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_idempotency_outcome (
            idempotency_key_digest text PRIMARY KEY,
            operation text NOT NULL,
            request_digest text NOT NULL,
            outcome_document jsonb NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_preemptive_revocation (
            installation_id text PRIMARY KEY,
            revoked_event_id text NOT NULL,
            revoked_at timestamptz NOT NULL
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS entitlement_one_active_registration
        ON {schema}.entitlement_project_registration (project_profile_id)
        WHERE superseded_at IS NULL
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_owner_action_nonce (
            nonce_digest text PRIMARY KEY,
            operation text NOT NULL,
            installation_id text NOT NULL,
            expected_generation bigint,
            request_digest text NOT NULL,
            idempotency_key_digest text NOT NULL,
            actor_id text NOT NULL,
            issued_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            signature_digest text NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_route_proof_nonce (
            nonce_digest text PRIMARY KEY,
            proof_document jsonb NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.entitlement_runtime_authorization_claim (
            nonce_digest text PRIMARY KEY,
            installation_id text NOT NULL,
            action_type text NOT NULL,
            destination_id text,
            effect_digest text NOT NULL,
            build_digest text NOT NULL,
            source_commit_sha text NOT NULL,
            source_tree_sha text NOT NULL,
            authorization_document jsonb NOT NULL,
            authorization_binding_digest text NOT NULL,
            claimed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        reject,
        f"""
        CREATE TRIGGER entitlement_event_immutable
        BEFORE UPDATE OR DELETE ON {schema}.entitlement_event
        FOR EACH ROW EXECUTE FUNCTION {schema}.entitlement_reject_mutation()
        """,
        f"""
        CREATE TRIGGER entitlement_preemptive_revocation_immutable
        BEFORE UPDATE OR DELETE ON {schema}.entitlement_preemptive_revocation
        FOR EACH ROW EXECUTE FUNCTION {schema}.entitlement_reject_mutation()
        """,
        f"""
        CREATE TRIGGER entitlement_install_attestation_immutable
        BEFORE UPDATE OR DELETE ON {schema}.entitlement_install_attestation
        FOR EACH ROW EXECUTE FUNCTION {schema}.entitlement_reject_mutation()
        """,
        f"""
        CREATE TRIGGER entitlement_owner_action_nonce_immutable
        BEFORE UPDATE OR DELETE ON {schema}.entitlement_owner_action_nonce
        FOR EACH ROW EXECUTE FUNCTION {schema}.entitlement_reject_mutation()
        """,
        f"""
        CREATE TRIGGER entitlement_route_proof_nonce_immutable
        BEFORE UPDATE OR DELETE ON {schema}.entitlement_route_proof_nonce
        FOR EACH ROW EXECUTE FUNCTION {schema}.entitlement_reject_mutation()
        """,
        f"""
        CREATE TRIGGER entitlement_runtime_authorization_claim_immutable
        BEFORE UPDATE OR DELETE ON {schema}.entitlement_runtime_authorization_claim
        FOR EACH ROW EXECUTE FUNCTION {schema}.entitlement_reject_mutation()
        """,
    )


def _bootstrap_schema(connection: Any, *, schema: str, storage_identity: str) -> None:
    manifest_table = connection.execute(
        "SELECT to_regclass(%s) AS manifest_table",
        (f"{schema}.entitlement_schema_manifest",),
    ).fetchone()["manifest_table"]
    if manifest_table is None:
        foreign_objects = connection.execute(
            """
            SELECT (
                EXISTS (
                    SELECT 1
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = %s
                )
                OR EXISTS (
                    SELECT 1
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = %s
                )
            ) AS present
            """,
            (schema, schema),
        ).fetchone()["present"]
        if foreign_objects:
            raise EntitlementControlPlaneError(
                "refusing to initialize a nonempty schema without the entitlement manifest"
            )
        for statement in _entitlement_ddl(schema):
            connection.execute(statement)
        connection.execute(
            f"""
            INSERT INTO {schema}.entitlement_schema_manifest (
                singleton, manifest_version, schema_version, storage_identity
            ) VALUES (true, %s, %s, %s)
            """,
            (ENTITLEMENT_MANIFEST_VERSION, schema, storage_identity),
        )
        return
    manifest = connection.execute(
        f"""
        SELECT manifest_version, schema_version, storage_identity
        FROM {schema}.entitlement_schema_manifest
        WHERE singleton = true
        """
    ).fetchone()
    if manifest is None:
        raise EntitlementControlPlaneError("entitlement schema manifest row is missing")
    if (
        manifest["manifest_version"] != ENTITLEMENT_MANIFEST_VERSION
        or manifest["schema_version"] != schema
        or manifest["storage_identity"] != storage_identity
    ):
        raise EntitlementControlPlaneError(
            "entitlement schema belongs to a different storage target or manifest"
        )
    expected_tables = {
        "entitlement_schema_manifest",
        "entitlement_signed_template",
        "entitlement_slack_destination",
        "entitlement_project_registration",
        "entitlement_install_attestation",
        "entitlement_event",
        "entitlement_current_generation",
        "entitlement_owner_notice",
        "entitlement_idempotency_outcome",
        "entitlement_preemptive_revocation",
        "entitlement_owner_action_nonce",
        "entitlement_route_proof_nonce",
        "entitlement_runtime_authorization_claim",
    }
    actual_tables = {
        str(row["table_name"])
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            """,
            (schema,),
        ).fetchall()
    }
    if actual_tables != expected_tables:
        raise EntitlementControlPlaneError(
            "entitlement schema object inventory does not match its manifest"
        )
    expected_columns = {
        "entitlement_install_attestation": {
            "attestation_id", "schema_version", "document_digest", "installation_id",
            "nonce_digest", "document", "build_evidence_digest", "test_evidence_digest",
            "recorded_at",
        },
        "entitlement_current_generation": {
            "installation_id", "project_profile_id", "subject_kind", "subject_id",
            "provider_id", "registration_generation", "entitlement_generation",
            "profile_template_id", "template_digest", "registration_document_digest",
            "granted_capabilities", "granted_capabilities_digest", "build_digest",
            "source_commit_sha", "source_tree_sha",
            "lifecycle_status", "notice_id", "notice_delivery_state",
            "notice_pending_since", "granted_at", "last_event_id", "updated_at",
        },
        "entitlement_slack_destination": {
            "destination_id", "verified", "owner_signature_principal",
            "owner_signature_digest", "health_proof_document", "health_proof_digest",
            "health_proof_nonce_digest", "health_proof_expires_at", "verified_at",
        },
        "entitlement_runtime_authorization_claim": {
            "nonce_digest", "installation_id", "action_type", "effect_digest",
            "build_digest", "source_commit_sha", "source_tree_sha",
            "authorization_document", "authorization_binding_digest", "claimed_at",
        },
    }
    for table_name, required_columns in expected_columns.items():
        actual_columns = {
            str(row["column_name"])
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s AND is_nullable = 'NO'
                """,
                (schema, table_name),
            ).fetchall()
        }
        if actual_columns != required_columns:
            raise EntitlementControlPlaneError(
                f"entitlement schema column contract mismatch for {table_name}"
            )
    expected_triggers = {
        ("entitlement_event_immutable", "entitlement_event", 27, "entitlement_reject_mutation"),
        ("entitlement_preemptive_revocation_immutable", "entitlement_preemptive_revocation", 27, "entitlement_reject_mutation"),
        ("entitlement_install_attestation_immutable", "entitlement_install_attestation", 27, "entitlement_reject_mutation"),
        ("entitlement_owner_action_nonce_immutable", "entitlement_owner_action_nonce", 27, "entitlement_reject_mutation"),
        ("entitlement_route_proof_nonce_immutable", "entitlement_route_proof_nonce", 27, "entitlement_reject_mutation"),
        ("entitlement_runtime_authorization_claim_immutable", "entitlement_runtime_authorization_claim", 27, "entitlement_reject_mutation"),
    }
    actual_triggers = {
        (str(row["trigger_name"]), str(row["table_name"]), int(row["trigger_type"]), str(row["function_name"]))
        for row in connection.execute(
            """
            SELECT trigger.tgname AS trigger_name, relation.relname AS table_name,
                   trigger.tgtype AS trigger_type, procedure.proname AS function_name
            FROM pg_trigger AS trigger
            JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            JOIN pg_proc AS procedure ON procedure.oid = trigger.tgfoid
            WHERE namespace.nspname = %s AND NOT trigger.tgisinternal
            """,
            (schema,),
        ).fetchall()
    }
    if not expected_triggers.issubset(actual_triggers):
        raise EntitlementControlPlaneError("entitlement immutable trigger contract mismatch")
    active_index = connection.execute(
        """
        SELECT 1 FROM pg_indexes
        WHERE schemaname = %s AND indexname = 'entitlement_one_active_registration'
          AND indexdef ILIKE '%%UNIQUE INDEX%%'
          AND indexdef ILIKE '%%WHERE (superseded_at IS NULL)%%'
        """,
        (schema,),
    ).fetchone()
    if active_index is None:
        raise EntitlementControlPlaneError("active registration uniqueness constraint is missing")
    expected_key_constraints = {
        ("entitlement_schema_manifest", "p", ("singleton",)),
        ("entitlement_signed_template", "p", ("profile_template_id",)),
        ("entitlement_slack_destination", "p", ("destination_id",)),
        ("entitlement_slack_destination", "u", ("health_proof_nonce_digest",)),
        ("entitlement_project_registration", "p", ("project_profile_id", "registration_generation")),
        ("entitlement_install_attestation", "p", ("attestation_id",)),
        ("entitlement_install_attestation", "u", ("nonce_digest",)),
        ("entitlement_event", "p", ("event_id",)),
        ("entitlement_event", "u", ("idempotency_key_digest",)),
        ("entitlement_current_generation", "p", ("installation_id",)),
        ("entitlement_owner_notice", "p", ("notice_id",)),
        ("entitlement_idempotency_outcome", "p", ("idempotency_key_digest",)),
        ("entitlement_preemptive_revocation", "p", ("installation_id",)),
        ("entitlement_owner_action_nonce", "p", ("nonce_digest",)),
        ("entitlement_route_proof_nonce", "p", ("nonce_digest",)),
        ("entitlement_runtime_authorization_claim", "p", ("nonce_digest",)),
    }
    actual_key_constraints = {
        (str(row["table_name"]), str(row["constraint_type"]), tuple(row["columns"]))
        for row in connection.execute(
            """
            SELECT relation.relname AS table_name,
                   con.contype::text AS constraint_type,
                   array_agg(attribute.attname ORDER BY key_column.ordinality) AS columns
            FROM pg_constraint AS con
            JOIN pg_class AS relation ON relation.oid = con.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            JOIN unnest(con.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
              ON true
            JOIN pg_attribute AS attribute
              ON attribute.attrelid = relation.oid AND attribute.attnum = key_column.attnum
            WHERE namespace.nspname = %s AND con.contype IN ('p', 'u')
            GROUP BY relation.relname, con.contype, con.oid
            """,
            (schema,),
        ).fetchall()
    }
    if not expected_key_constraints.issubset(actual_key_constraints):
        raise EntitlementControlPlaneError("entitlement schema key constraint contract mismatch")
    ownership = connection.execute(
        """
        SELECT
          (SELECT pg_get_userbyid(namespace.nspowner)
             FROM pg_namespace AS namespace WHERE namespace.nspname = %s) AS schema_owner,
          current_user AS current_owner,
          bool_and(pg_get_userbyid(relation.relowner) = current_user) AS tables_owned,
          (SELECT pg_get_userbyid(procedure.proowner) = current_user
             FROM pg_proc AS procedure
             JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = %s
              AND procedure.proname = 'entitlement_reject_mutation') AS function_owned,
          (SELECT lower(procedure.prosrc) LIKE '%%raise exception ''immutable entitlement row''%%'
             FROM pg_proc AS procedure
             JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = %s
              AND procedure.proname = 'entitlement_reject_mutation') AS function_body_valid
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s AND relation.relkind = 'r'
        """,
        (schema, schema, schema, schema),
    ).fetchone()
    if (
        ownership is None
        or ownership["schema_owner"] != ownership["current_owner"]
        or not ownership["tables_owned"]
        or not ownership["function_owned"]
        or not ownership["function_body_valid"]
    ):
        raise EntitlementControlPlaneError("entitlement schema ownership contract mismatch")


class EntitlementControlPlaneStore:
    def __init__(
        self,
        pool: Any,
        *,
        schema: str,
        storage_identity: str,
        trusted_verifier_secret: bytes,
        route_verifier_secret: bytes,
        owner_authority_secret: bytes,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pool = pool
        self._schema = _require_schema(schema)
        self._storage_identity = storage_identity
        self._trusted_verifier_secret = _validate_secret_material(
            trusted_verifier_secret, label="install verifier secret"
        )
        self._route_verifier_secret = _validate_secret_material(
            route_verifier_secret, label="route verifier secret"
        )
        self._owner_authority_secret = _validate_secret_material(
            owner_authority_secret, label="owner authority secret"
        )
        if len({self._trusted_verifier_secret, self._route_verifier_secret,
                self._owner_authority_secret}) != 3:
            raise EntitlementControlPlaneError(
                "owner, install-verifier, and route-verifier secrets must be cryptographically separated"
            )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def schema(self) -> str:
        return self._schema

    def _lock_installation(self, connection: Any, installation_id: str) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"{self._schema}:{installation_id}",),
        )

    def _lock_project(self, connection: Any, project_profile_id: str) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"{self._schema}:project:{project_profile_id}",),
        )

    def _validate_owner_action(
        self,
        *,
        action: EntitlementOwnerActionV1,
        operation: str,
        installation_id: str,
        expected_generation: int | None,
        idempotency_key_digest: str,
        request_digest: str,
        require_fresh: bool = True,
    ) -> None:
        now = self._clock()
        if require_fresh and (action.issued_at > now or action.expires_at <= now):
            raise EntitlementControlPlaneError("owner action is not currently valid")
        if (
            action.operation != operation
            or action.installation_id != installation_id
            or action.expected_generation != expected_generation
            or action.idempotency_key_digest != idempotency_key_digest
            or action.request_digest != request_digest
        ):
            raise EntitlementControlPlaneError("owner action does not bind the exact request")
        try:
            action.verify_signature(
                owner_authority_secret=self._owner_authority_secret
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"owner action signature invalid: {exc}"
            ) from exc

    def _claim_owner_action(
        self,
        connection: Any,
        *,
        action: EntitlementOwnerActionV1,
        operation: str,
        installation_id: str,
        expected_generation: int | None,
        idempotency_key_digest: str,
        request_digest: str,
    ) -> None:
        self._validate_owner_action(
            action=action,
            operation=operation,
            installation_id=installation_id,
            expected_generation=expected_generation,
            idempotency_key_digest=idempotency_key_digest,
            request_digest=request_digest,
        )
        inserted = connection.execute(
            f"""
            INSERT INTO {self._schema}.entitlement_owner_action_nonce (
                nonce_digest, operation, installation_id, expected_generation,
                request_digest, idempotency_key_digest, actor_id, issued_at,
                expires_at, signature_digest
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (nonce_digest) DO NOTHING
            RETURNING nonce_digest
            """,
            (
                action.nonce_digest,
                action.operation,
                action.installation_id,
                action.expected_generation,
                action.request_digest,
                action.idempotency_key_digest,
                action.actor_id,
                action.issued_at,
                action.expires_at,
                action.owner_signature_digest,
            ),
        ).fetchone()
        if inserted is None:
            raise EntitlementControlPlaneError("owner action nonce replayed")

    def _load_idempotent_outcome(
        self,
        connection: Any,
        idempotency_key_digest: str,
        *,
        operation: str,
        request_digest: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            f"""
            SELECT operation, request_digest, outcome_document
            FROM {self._schema}.entitlement_idempotency_outcome
            WHERE idempotency_key_digest = %s
            """,
            (idempotency_key_digest,),
        ).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["request_digest"] != request_digest:
            raise EntitlementControlPlaneError(
                "idempotency key reused with different operation or request"
            )
        return dict(row["outcome_document"])

    def _claim_notice_route_proof(
        self,
        connection: Any,
        *,
        proof: NoticeRouteProofV1,
        notice_id: str,
        installation_id: str,
        entitlement_generation: int,
        destination_id: str,
        outcome: str,
    ) -> None:
        now = self._clock()
        if proof.issued_at > now or proof.expires_at <= now:
            raise EntitlementControlPlaneError("notice route proof is not fresh")
        if (
            proof.notice_id != notice_id
            or proof.installation_id != installation_id
            or proof.entitlement_generation != entitlement_generation
            or proof.destination_id != destination_id
            or proof.outcome != outcome
        ):
            raise EntitlementControlPlaneError("notice route proof binding mismatch")
        try:
            proof.verify_signature(trusted_verifier_secret=self._route_verifier_secret)
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"notice route proof signature invalid: {exc}"
            ) from exc
        inserted = connection.execute(
            f"""
            INSERT INTO {self._schema}.entitlement_route_proof_nonce (
                nonce_digest, proof_document
            ) VALUES (%s, %s::jsonb)
            ON CONFLICT (nonce_digest) DO NOTHING
            RETURNING nonce_digest
            """,
            (proof.nonce_digest, json.dumps(proof.to_document())),
        ).fetchone()
        if inserted is None:
            raise EntitlementControlPlaneError("notice route proof nonce replayed")

    def _store_idempotent_outcome(
        self,
        connection: Any,
        *,
        operation: str,
        idempotency_key_digest: str,
        request_digest: str,
        outcome_document: Mapping[str, Any],
    ) -> None:
        inserted = connection.execute(
            f"""
            INSERT INTO {self._schema}.entitlement_idempotency_outcome (
                idempotency_key_digest, operation, request_digest, outcome_document
            ) VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (idempotency_key_digest) DO NOTHING
            RETURNING idempotency_key_digest
            """,
            (
                idempotency_key_digest,
                operation,
                request_digest,
                json.dumps(outcome_document),
            ),
        ).fetchone()
        if inserted is None:
            existing = connection.execute(
                f"""
                SELECT request_digest, outcome_document
                FROM {self._schema}.entitlement_idempotency_outcome
                WHERE idempotency_key_digest = %s
                """,
                (idempotency_key_digest,),
            ).fetchone()
            if existing is None:
                raise EntitlementControlPlaneError("idempotency outcome lost race")
            if existing["request_digest"] != request_digest:
                raise EntitlementControlPlaneError(
                    "idempotency key reused with different request"
                )

    def _insert_event(
        self, connection: Any, event: EntitlementEventV1
    ) -> str:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.entitlement_event (
                event_id, schema_version, event_type, installation_id,
                entitlement_generation, expected_generation, actor_id,
                idempotency_key_digest, scope_digest, evidence_binding_digest,
                document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                event.event_id,
                ENTITLEMENT_EVENT_VERSION,
                event.event_type.value,
                event.installation_id,
                event.entitlement_generation,
                event.expected_generation,
                event.actor_id,
                event.idempotency_key_digest,
                event.scope_digest,
                event.evidence_binding_digest,
                json.dumps(event.signing_document()),
            ),
        )
        return event.event_id

    def register_signed_template(self, template: SignedEntitlementTemplateV1) -> str:
        try:
            template.verify_owner_signature(
                owner_authority_secret=self._owner_authority_secret
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"signed template signature invalid: {exc}"
            ) from exc
        digest = composed_template_digest(template.profile_template_id)
        capabilities = sorted(effective_capabilities(template.profile_template_id))
        with self._pool.connection() as connection:
            connection.execute(
                f"""
                INSERT INTO {self._schema}.entitlement_signed_template (
                    profile_template_id, template_digest, capabilities,
                    owner_policy_digest, owner_signature_principal,
                    owner_signature_digest
                ) VALUES (%s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (profile_template_id) DO UPDATE SET
                    template_digest = EXCLUDED.template_digest,
                    capabilities = EXCLUDED.capabilities,
                    owner_policy_digest = EXCLUDED.owner_policy_digest,
                    owner_signature_principal = EXCLUDED.owner_signature_principal,
                    owner_signature_digest = EXCLUDED.owner_signature_digest
                """,
                (
                    template.profile_template_id,
                    digest,
                    json.dumps(capabilities),
                    template.owner_policy_digest,
                    template.owner_signature_principal,
                    template.owner_signature_digest,
                ),
            )
        return digest

    def register_verified_slack_destination(
        self,
        *,
        destination_id: str,
        owner_signature_principal: str,
        owner_signature_digest: str,
        health_proof: SlackDestinationHealthProofV1,
    ) -> None:
        signing_doc = {
            "schema_version": "OwnerSlackDestination/v1",
            "destination_id": destination_id,
            "owner_signature_principal": owner_signature_principal,
        }
        try:
            verify_signature(
                principal=owner_signature_principal,
                signature_digest=owner_signature_digest,
                signing_payload=signing_bytes(signing_doc),
                secret=self._owner_authority_secret,
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"owner slack destination signature invalid: {exc}"
            ) from exc
        if health_proof.destination_id != destination_id:
            raise EntitlementControlPlaneError("Slack health proof destination mismatch")
        now = self._clock()
        if health_proof.issued_at > now or health_proof.expires_at <= now:
            raise EntitlementControlPlaneError("Slack health proof is not fresh")
        try:
            health_proof.verify_signature(
                trusted_verifier_secret=self._route_verifier_secret
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"Slack health proof signature invalid: {exc}"
            ) from exc
        with self._pool.connection() as connection:
            connection.execute(
                f"""
                INSERT INTO {self._schema}.entitlement_slack_destination (
                    destination_id, verified, owner_signature_principal,
                    owner_signature_digest, health_proof_document,
                    health_proof_digest, health_proof_nonce_digest,
                    health_proof_expires_at
                ) VALUES (%s, true, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (destination_id) DO UPDATE SET
                    verified = EXCLUDED.verified,
                    owner_signature_principal = EXCLUDED.owner_signature_principal,
                    owner_signature_digest = EXCLUDED.owner_signature_digest,
                    health_proof_document = EXCLUDED.health_proof_document,
                    health_proof_digest = EXCLUDED.health_proof_digest,
                    health_proof_nonce_digest = EXCLUDED.health_proof_nonce_digest,
                    health_proof_expires_at = EXCLUDED.health_proof_expires_at,
                    verified_at = now()
                """,
                (
                    destination_id,
                    owner_signature_principal,
                    owner_signature_digest,
                    json.dumps(health_proof.to_document()),
                    health_proof.digest,
                    health_proof.nonce_digest,
                    health_proof.expires_at,
                ),
            )

    def register_project(self, registration: ProjectRegistrationV1) -> str:
        try:
            registration.verify_owner_signature(
                owner_authority_secret=self._owner_authority_secret
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"project registration signature invalid: {exc}"
            ) from exc
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_project(connection, registration.project_profile_id)
                prior = connection.execute(
                    f"""
                    SELECT registration_generation
                    FROM {self._schema}.entitlement_project_registration
                    WHERE project_profile_id = %s
                    ORDER BY registration_generation DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (registration.project_profile_id,),
                ).fetchone()
                if prior is not None and registration.registration_generation <= prior[
                    "registration_generation"
                ]:
                    raise EntitlementControlPlaneError(
                        "registration generation must strictly increase"
                    )
                if prior is not None:
                    raise EntitlementControlPlaneError(
                        "an active registration must be superseded atomically"
                    )
                slack = connection.execute(
                    f"""
                    SELECT verified, health_proof_expires_at
                    FROM {self._schema}.entitlement_slack_destination
                    WHERE destination_id = %s
                    """,
                    (registration.slack_destination_id,),
                ).fetchone()
                if (
                    slack is None
                    or not slack["verified"]
                    or slack["health_proof_expires_at"] <= self._clock()
                ):
                    raise EntitlementControlPlaneError(
                        "verified owner slack destination is required"
                    )
                template = connection.execute(
                    f"""
                    SELECT profile_template_id FROM {self._schema}.entitlement_signed_template
                    WHERE profile_template_id = %s
                    """,
                    (registration.entitlement_template_id,),
                ).fetchone()
                if template is None:
                    raise EntitlementControlPlaneError(
                        "owner-approved signed template is not registered"
                    )
                connection.execute(
                    f"""
                    INSERT INTO {self._schema}.entitlement_project_registration (
                        project_profile_id, registration_generation, schema_version,
                        document, document_digest, profile_template_id,
                        owner_policy_digest, slack_destination_id
                    ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    """,
                    (
                        registration.project_profile_id,
                        registration.registration_generation,
                        PROJECT_REGISTRATION_VERSION,
                        json.dumps(registration.to_document()),
                        registration.digest,
                        registration.entitlement_template_id,
                        registration.owner_policy_digest,
                        registration.slack_destination_id,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return registration.digest

    def grant_from_attestation(
        self,
        attestation: InstallAttestationV1,
        *,
        idempotency_key_digest: str,
        actor_id: str = "trusted-installer",
        subject_declared_extra_rights: frozenset[str] | None = None,
    ) -> EntitlementGrantOutcome:
        if subject_declared_extra_rights:
            raise EntitlementControlPlaneError(
                "subject-declared extra rights are rejected"
            )
        now = self._clock()
        if attestation.expires_at <= now:
            raise EntitlementControlPlaneError("install attestation is stale")
        if attestation.issued_at > now:
            raise EntitlementControlPlaneError("install attestation is not yet valid")
        try:
            attestation.verify_signature(
                trusted_verifier_secret=self._trusted_verifier_secret
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"install attestation signature mismatch: {exc}"
            ) from exc
        request_digest = document_digest(
            {
                "operation": "grant_from_attestation",
                "attestation_digest": attestation.digest,
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_project(connection, attestation.project_profile_id)
                self._lock_installation(connection, attestation.installation_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    idempotency_key_digest,
                    operation="grant_from_attestation",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return self._outcome_from_document(cached, idempotent_replay=True)
                preemptive = connection.execute(
                    f"""
                    SELECT revoked_event_id
                    FROM {self._schema}.entitlement_preemptive_revocation
                    WHERE installation_id = %s
                    """,
                    (attestation.installation_id,),
                ).fetchone()
                if preemptive is not None:
                    raise EntitlementControlPlaneError("installation is preemptively revoked")
                current = connection.execute(
                    f"""
                    SELECT entitlement_generation, lifecycle_status, build_digest,
                           registration_generation, project_profile_id, subject_kind,
                           subject_id, provider_id
                    FROM {self._schema}.entitlement_current_generation
                    WHERE installation_id = %s
                    FOR UPDATE
                    """,
                    (attestation.installation_id,),
                ).fetchone()
                if current is not None and current["lifecycle_status"] in (
                    EntitlementLifecycleStatus.REVOKED.value,
                    EntitlementLifecycleStatus.SUSPENDED.value,
                    EntitlementLifecycleStatus.SUPERSEDED.value,
                    EntitlementLifecycleStatus.UNINSTALLED.value,
                ):
                    raise EntitlementControlPlaneError(
                        "suspended, revoked, superseded, or uninstalled installation cannot be granted"
                    )
                if current is not None and (
                    current["project_profile_id"] != attestation.project_profile_id
                    or current["subject_kind"] != attestation.subject_kind.value
                    or current["subject_id"] != attestation.subject_id
                    or current["provider_id"] != attestation.provider_id
                ):
                    raise EntitlementControlPlaneError("installation identity cannot change")
                registration = connection.execute(
                    f"""
                    SELECT document, document_digest, profile_template_id, owner_policy_digest,
                           slack_destination_id, superseded_at
                    FROM {self._schema}.entitlement_project_registration
                    WHERE project_profile_id = %s
                      AND registration_generation = %s
                    """,
                    (
                        attestation.project_profile_id,
                        attestation.registration_generation,
                    ),
                ).fetchone()
                if registration is None or registration["superseded_at"] is not None:
                    raise EntitlementControlPlaneError("project registration is not active")
                profile_template_id = str(registration["profile_template_id"])
                template = connection.execute(
                    f"""
                    SELECT template_digest, capabilities, owner_policy_digest
                    FROM {self._schema}.entitlement_signed_template
                    WHERE profile_template_id = %s
                    """,
                    (profile_template_id,),
                ).fetchone()
                expected_digest = composed_template_digest(profile_template_id)
                expected_capabilities = tuple(sorted(effective_capabilities(profile_template_id)))
                if (
                    template is None
                    or str(template["template_digest"]) != expected_digest
                    or tuple(template["capabilities"]) != expected_capabilities
                    or str(template["owner_policy_digest"])
                    != str(registration["owner_policy_digest"])
                ):
                    raise EntitlementControlPlaneError("signed template drift detected")
                if attestation.template_digest != expected_digest:
                    raise EntitlementControlPlaneError("template digest mismatch")
                if attestation.owner_policy_digest != str(registration["owner_policy_digest"]):
                    raise EntitlementControlPlaneError("owner policy digest mismatch")
                slack = connection.execute(
                    f"""
                    SELECT verified, health_proof_expires_at
                    FROM {self._schema}.entitlement_slack_destination
                    WHERE destination_id = %s
                    """,
                    (registration["slack_destination_id"],),
                ).fetchone()
                if (
                    slack is None
                    or not slack["verified"]
                    or slack["health_proof_expires_at"] <= now
                ):
                    raise EntitlementControlPlaneError(
                        "verified owner slack destination is required"
                    )
                if connection.execute(
                    f"""
                    SELECT attestation_id FROM {self._schema}.entitlement_install_attestation
                    WHERE nonce_digest = %s
                    """,
                    (attestation.nonce_digest,),
                ).fetchone():
                    raise EntitlementControlPlaneError("attestation nonce replayed")
                next_generation = 1
                expected_generation = None
                if current is not None:
                    expected_generation = int(current["entitlement_generation"])
                    if (
                        current["build_digest"] == attestation.artifact_digest
                        and current["lifecycle_status"] == EntitlementLifecycleStatus.ACTIVE.value
                    ):
                        raise EntitlementControlPlaneError(
                            "unchanged build already holds an active generation"
                        )
                    next_generation = expected_generation + 1
                capabilities = frozenset(expected_capabilities)
                capabilities_digest = document_digest(
                    {"capabilities": list(expected_capabilities)}
                )
                event_id = f"evt-{uuid.uuid4().hex}"
                grant_event = EntitlementEventV1(
                    event_type=EntitlementEventType.GRANT,
                    installation_id=attestation.installation_id,
                    entitlement_generation=next_generation,
                    expected_generation=expected_generation,
                    actor_id=actor_id,
                    idempotency_key_digest=idempotency_key_digest,
                    scope_digest=document_digest(
                        {"registration_generation": attestation.registration_generation}
                    ),
                    evidence_binding_digest=attestation.digest,
                    event_id=event_id,
                )
                self._insert_event(connection, grant_event)
                connection.execute(
                    f"""
                    INSERT INTO {self._schema}.entitlement_install_attestation (
                        attestation_id, schema_version, document_digest,
                        installation_id, nonce_digest, document,
                        build_evidence_digest, test_evidence_digest
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        attestation.attestation_id,
                        INSTALL_ATTESTATION_VERSION,
                        attestation.digest,
                        attestation.installation_id,
                        attestation.nonce_digest,
                        json.dumps(attestation.to_document()),
                        attestation.build_evidence_digest,
                        attestation.test_evidence_digest,
                    ),
                )
                notice_id = f"notice-{uuid.uuid4().hex}"
                authority = authority_bearing_capabilities(capabilities)
                notice = OwnerNoticeV1(
                    notice_id=notice_id,
                    installation_id=attestation.installation_id,
                    entitlement_generation=next_generation,
                    delivery_state=OwnerNoticeDeliveryState.PENDING,
                    project_profile_id=attestation.project_profile_id,
                    subject_kind=attestation.subject_kind,
                    subject_id=attestation.subject_id,
                    template_id=profile_template_id,
                    authority_bearing_capabilities=authority,
                    build_digest=attestation.artifact_digest,
                    pending_since=now,
                )
                connection.execute(
                    f"""
                    INSERT INTO {self._schema}.entitlement_owner_notice (
                        notice_id, schema_version, installation_id,
                        entitlement_generation, delivery_state, document, pending_since
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    (
                        notice.notice_id,
                        OWNER_NOTICE_VERSION,
                        notice.installation_id,
                        notice.entitlement_generation,
                        notice.delivery_state.value,
                        json.dumps(notice.to_document()),
                        notice.pending_since,
                    ),
                )
                if current is None:
                    connection.execute(
                        f"""
                        INSERT INTO {self._schema}.entitlement_current_generation (
                            installation_id, project_profile_id, subject_kind, subject_id,
                            provider_id, registration_generation, entitlement_generation,
                            profile_template_id, template_digest, build_digest,
                            registration_document_digest, granted_capabilities,
                            granted_capabilities_digest,
                            source_commit_sha, source_tree_sha,
                            lifecycle_status, notice_id, notice_delivery_state,
                            notice_pending_since, granted_at, last_event_id
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            attestation.installation_id,
                            attestation.project_profile_id,
                            attestation.subject_kind.value,
                            attestation.subject_id,
                            attestation.provider_id,
                            attestation.registration_generation,
                            next_generation,
                            profile_template_id,
                            attestation.template_digest,
                            attestation.artifact_digest,
                            registration["document_digest"],
                            json.dumps(expected_capabilities),
                            capabilities_digest,
                            attestation.source_commit_sha,
                            attestation.source_tree_sha,
                            EntitlementLifecycleStatus.ACTIVE.value,
                            notice.notice_id,
                            OwnerNoticeDeliveryState.PENDING.value,
                            notice.pending_since,
                            now,
                            grant_event.event_id,
                        ),
                    )
                else:
                    updated = connection.execute(
                        f"""
                        UPDATE {self._schema}.entitlement_current_generation
                        SET project_profile_id = %s,
                            subject_kind = %s,
                            subject_id = %s,
                            provider_id = %s,
                            registration_generation = %s,
                            entitlement_generation = %s,
                            profile_template_id = %s,
                            template_digest = %s,
                            build_digest = %s,
                            registration_document_digest = %s,
                            granted_capabilities = %s::jsonb,
                            granted_capabilities_digest = %s,
                            source_commit_sha = %s,
                            source_tree_sha = %s,
                            lifecycle_status = %s,
                            notice_id = %s,
                            notice_delivery_state = %s,
                            notice_pending_since = %s,
                            granted_at = %s,
                            last_event_id = %s,
                            updated_at = now()
                        WHERE installation_id = %s
                          AND entitlement_generation = %s
                        """,
                        (
                            attestation.project_profile_id,
                            attestation.subject_kind.value,
                            attestation.subject_id,
                            attestation.provider_id,
                            attestation.registration_generation,
                            next_generation,
                            profile_template_id,
                            attestation.template_digest,
                            attestation.artifact_digest,
                            registration["document_digest"],
                            json.dumps(expected_capabilities),
                            capabilities_digest,
                            attestation.source_commit_sha,
                            attestation.source_tree_sha,
                            EntitlementLifecycleStatus.ACTIVE.value,
                            notice.notice_id,
                            OwnerNoticeDeliveryState.PENDING.value,
                            notice.pending_since,
                            now,
                            grant_event.event_id,
                            attestation.installation_id,
                            expected_generation,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise EntitlementControlPlaneError("generation CAS failed on grant")
                outcome = EntitlementGrantOutcome(
                    granted_capabilities=tuple(sorted(capabilities)),
                    entitlement_generation=next_generation,
                    notice=notice,
                    event_id=grant_event.event_id,
                    idempotent_replay=False,
                )
                self._store_idempotent_outcome(
                    connection,
                    operation="grant_from_attestation",
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document=self._outcome_to_document(outcome),
                )
                connection.execute("COMMIT")
            except Exception as exc:
                connection.execute("ROLLBACK")
                if isinstance(exc, EntitlementControlPlaneError):
                    raise
                raise EntitlementControlPlaneError(
                    "grant transaction failed"
                ) from exc
        return outcome

    def _outcome_to_document(self, outcome: EntitlementGrantOutcome) -> dict[str, Any]:
        return {
            "granted_capabilities": list(outcome.granted_capabilities),
            "entitlement_generation": outcome.entitlement_generation,
            "notice": outcome.notice.to_document(),
            "event_id": outcome.event_id,
            "idempotent_replay": outcome.idempotent_replay,
        }

    def _outcome_from_document(
        self, document: Mapping[str, Any], *, idempotent_replay: bool
    ) -> EntitlementGrantOutcome:
        notice_doc = document["notice"]
        notice = OwnerNoticeV1(
            notice_id=notice_doc["notice_id"],
            installation_id=notice_doc["installation_id"],
            entitlement_generation=notice_doc["entitlement_generation"],
            delivery_state=OwnerNoticeDeliveryState(notice_doc["delivery_state"]),
            project_profile_id=notice_doc["project_profile_id"],
            subject_kind=SubjectKind(notice_doc["subject_kind"]),
            subject_id=notice_doc["subject_id"],
            template_id=notice_doc["template_id"],
            authority_bearing_capabilities=tuple(
                notice_doc["authority_bearing_capabilities"]
            ),
            build_digest=notice_doc["build_digest"],
            pending_since=datetime.fromisoformat(notice_doc["pending_since"]),
            delivery_proof_digest=notice_doc.get("delivery_proof_digest"),
            ack_proof_digest=notice_doc.get("ack_proof_digest"),
        )
        return EntitlementGrantOutcome(
            granted_capabilities=tuple(document["granted_capabilities"]),
            entitlement_generation=int(document["entitlement_generation"]),
            notice=notice,
            event_id=str(document["event_id"]),
            idempotent_replay=idempotent_replay,
        )

    def revoke(
        self,
        installation_id: str,
        *,
        owner_action: EntitlementOwnerActionV1,
    ) -> str:
        now = self._clock()
        request_digest = document_digest(
            {
                "operation": "revoke",
                "installation_id": installation_id,
                "expected_generation": owner_action.expected_generation,
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_installation(connection, installation_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    owner_action.idempotency_key_digest,
                    operation="revoke",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
                self._claim_owner_action(
                    connection,
                    action=owner_action,
                    operation="revoke",
                    installation_id=installation_id,
                    expected_generation=owner_action.expected_generation,
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                )
                current = connection.execute(
                    f"""
                    SELECT entitlement_generation, lifecycle_status, profile_template_id
                    FROM {self._schema}.entitlement_current_generation
                    WHERE installation_id = %s
                    FOR UPDATE
                    """,
                    (installation_id,),
                ).fetchone()
                generation = 0 if current is None else int(current["entitlement_generation"])
                if owner_action.expected_generation != (generation if current else None):
                    raise EntitlementControlPlaneError("generation CAS failed on revoke")
                if current is not None and current["lifecycle_status"] == EntitlementLifecycleStatus.REVOKED.value:
                    raise EntitlementControlPlaneError("installation already revoked")
                event_id = f"evt-{uuid.uuid4().hex}"
                event = EntitlementEventV1(
                    event_type=EntitlementEventType.REVOKE,
                    installation_id=installation_id,
                    entitlement_generation=generation,
                    expected_generation=generation if current else None,
                    actor_id=owner_action.actor_id,
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    scope_digest=document_digest({"installation_id": installation_id}),
                    evidence_binding_digest=document_digest({"revoked_at": now.isoformat()}),
                    event_id=event_id,
                )
                self._insert_event(connection, event)
                if current is None:
                    connection.execute(
                        f"""
                        INSERT INTO {self._schema}.entitlement_preemptive_revocation (
                            installation_id, revoked_event_id, revoked_at
                        ) VALUES (%s, %s, %s)
                        ON CONFLICT (installation_id) DO NOTHING
                        """,
                        (installation_id, event.event_id, now),
                    )
                else:
                    updated = connection.execute(
                        f"""
                        UPDATE {self._schema}.entitlement_current_generation
                        SET lifecycle_status = %s, last_event_id = %s, updated_at = now()
                        WHERE installation_id = %s AND entitlement_generation = %s
                        """,
                        (
                            EntitlementLifecycleStatus.REVOKED.value,
                            event.event_id,
                            installation_id,
                            generation,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise EntitlementControlPlaneError("generation CAS failed on revoke")
                self._store_idempotent_outcome(
                    connection,
                    operation="revoke",
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document={"event_id": event.event_id},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return event_id

    def restore(
        self,
        attestation: InstallAttestationV1,
        *,
        owner_action: EntitlementOwnerActionV1,
    ) -> EntitlementGrantOutcome:
        now = self._clock()
        if attestation.expires_at <= now or attestation.issued_at > now:
            raise EntitlementControlPlaneError("install attestation is not currently valid")
        try:
            attestation.verify_signature(
                trusted_verifier_secret=self._trusted_verifier_secret
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"restore attestation signature mismatch: {exc}"
            ) from exc
        request_digest = document_digest(
            {
                "operation": "restore",
                "attestation": attestation.to_document(),
                "expected_generation": owner_action.expected_generation,
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_project(connection, attestation.project_profile_id)
                self._lock_installation(connection, attestation.installation_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    owner_action.idempotency_key_digest,
                    operation="restore",
                    request_digest=request_digest,
                )
                if cached is not None:
                    self._validate_owner_action(
                        action=owner_action,
                        operation="restore",
                        installation_id=attestation.installation_id,
                        expected_generation=owner_action.expected_generation,
                        idempotency_key_digest=owner_action.idempotency_key_digest,
                        request_digest=request_digest,
                        require_fresh=False,
                    )
                    live_lineage = connection.execute(
                        f"""
                        SELECT entitlement_generation, last_event_id
                        FROM {self._schema}.entitlement_current_generation
                        WHERE installation_id = %s
                        FOR UPDATE
                        """,
                        (attestation.installation_id,),
                    ).fetchone()
                    if (
                        live_lineage is None
                        or int(live_lineage["entitlement_generation"])
                        != int(cached["entitlement_generation"])
                        or str(live_lineage["last_event_id"]) != str(cached["event_id"])
                    ):
                        raise EntitlementControlPlaneError(
                            "stored restore outcome is no longer the current lineage"
                        )
                    connection.execute("COMMIT")
                    return self._outcome_from_document(cached, idempotent_replay=True)
                current = connection.execute(
                    f"""
                    SELECT entitlement_generation, lifecycle_status, profile_template_id,
                           project_profile_id, subject_kind, subject_id, provider_id,
                           registration_generation
                    FROM {self._schema}.entitlement_current_generation
                    WHERE installation_id = %s
                    FOR UPDATE
                    """,
                    (attestation.installation_id,),
                ).fetchone()
                if current is None or current["lifecycle_status"] != EntitlementLifecycleStatus.REVOKED.value:
                    raise EntitlementControlPlaneError("restore requires a revoked installation")
                if owner_action.expected_generation != int(current["entitlement_generation"]):
                    raise EntitlementControlPlaneError("generation CAS failed on restore")
                if (
                    current["project_profile_id"] != attestation.project_profile_id
                    or current["subject_kind"] != attestation.subject_kind.value
                    or current["subject_id"] != attestation.subject_id
                    or current["provider_id"] != attestation.provider_id
                    or int(current["registration_generation"])
                    != attestation.registration_generation
                ):
                    raise EntitlementControlPlaneError(
                        "restore attestation does not match installation lineage"
                    )
                self._claim_owner_action(
                    connection,
                    action=owner_action,
                    operation="restore",
                    installation_id=attestation.installation_id,
                    expected_generation=int(current["entitlement_generation"]),
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                )
                registration = connection.execute(
                    f"""
                    SELECT document, document_digest, profile_template_id, owner_policy_digest,
                           slack_destination_id, superseded_at
                    FROM {self._schema}.entitlement_project_registration
                    WHERE project_profile_id = %s AND registration_generation = %s
                    """,
                    (
                        attestation.project_profile_id,
                        attestation.registration_generation,
                    ),
                ).fetchone()
                if registration is None or registration["superseded_at"] is not None:
                    raise EntitlementControlPlaneError("project registration is not active")
                profile_template_id = str(registration["profile_template_id"])
                if attestation.owner_policy_digest != str(registration["owner_policy_digest"]):
                    raise EntitlementControlPlaneError("owner policy digest mismatch")
                template = connection.execute(
                    f"""
                    SELECT template_digest, capabilities, owner_policy_digest
                    FROM {self._schema}.entitlement_signed_template
                    WHERE profile_template_id = %s
                    """,
                    (profile_template_id,),
                ).fetchone()
                expected_digest = composed_template_digest(profile_template_id)
                expected_capabilities = tuple(sorted(effective_capabilities(profile_template_id)))
                if (
                    template is None
                    or str(template["template_digest"]) != expected_digest
                    or tuple(template["capabilities"]) != expected_capabilities
                    or str(template["owner_policy_digest"])
                    != str(registration["owner_policy_digest"])
                ):
                    raise EntitlementControlPlaneError("signed template drift detected")
                if attestation.template_digest != expected_digest:
                    raise EntitlementControlPlaneError("template digest mismatch")
                slack = connection.execute(
                    f"""
                    SELECT verified, health_proof_expires_at
                    FROM {self._schema}.entitlement_slack_destination
                    WHERE destination_id = %s
                    """,
                    (registration["slack_destination_id"],),
                ).fetchone()
                if (
                    slack is None
                    or not slack["verified"]
                    or slack["health_proof_expires_at"] <= now
                ):
                    raise EntitlementControlPlaneError(
                        "verified owner slack destination is required"
                    )
                if connection.execute(
                    f"""
                    SELECT 1 FROM {self._schema}.entitlement_install_attestation
                    WHERE nonce_digest = %s
                    """,
                    (attestation.nonce_digest,),
                ).fetchone():
                    raise EntitlementControlPlaneError("attestation nonce replayed")
                next_generation = int(current["entitlement_generation"]) + 1
                capabilities = frozenset(expected_capabilities)
                capabilities_digest = document_digest(
                    {"capabilities": list(expected_capabilities)}
                )
                connection.execute(
                    f"""
                    INSERT INTO {self._schema}.entitlement_install_attestation (
                        attestation_id, schema_version, document_digest,
                        installation_id, nonce_digest, document,
                        build_evidence_digest, test_evidence_digest
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        attestation.attestation_id,
                        INSTALL_ATTESTATION_VERSION,
                        attestation.digest,
                        attestation.installation_id,
                        attestation.nonce_digest,
                        json.dumps(attestation.to_document()),
                        attestation.build_evidence_digest,
                        attestation.test_evidence_digest,
                    ),
                )
                restore_event_id = f"evt-{uuid.uuid4().hex}"
                restore_event = EntitlementEventV1(
                    event_type=EntitlementEventType.RESTORE,
                    installation_id=attestation.installation_id,
                    entitlement_generation=next_generation,
                    expected_generation=int(current["entitlement_generation"]),
                    actor_id=owner_action.actor_id,
                    idempotency_key_digest=document_digest(
                        {"restore-lineage": owner_action.idempotency_key_digest}
                    ),
                    scope_digest=document_digest({"owner_actor": owner_action.actor_id}),
                    evidence_binding_digest=attestation.digest,
                    event_id=restore_event_id,
                )
                self._insert_event(connection, restore_event)
                grant_event_id = f"evt-{uuid.uuid4().hex}"
                grant_event = EntitlementEventV1(
                    event_type=EntitlementEventType.GRANT,
                    installation_id=attestation.installation_id,
                    entitlement_generation=next_generation,
                    expected_generation=int(current["entitlement_generation"]),
                    actor_id=owner_action.actor_id,
                    idempotency_key_digest=document_digest(
                        {"restore-grant": owner_action.idempotency_key_digest}
                    ),
                    scope_digest=document_digest(
                        {"registration_generation": attestation.registration_generation}
                    ),
                    evidence_binding_digest=attestation.digest,
                    event_id=grant_event_id,
                )
                self._insert_event(connection, grant_event)
                notice_id = f"notice-{uuid.uuid4().hex}"
                notice = OwnerNoticeV1(
                    notice_id=notice_id,
                    installation_id=attestation.installation_id,
                    entitlement_generation=next_generation,
                    delivery_state=OwnerNoticeDeliveryState.PENDING,
                    project_profile_id=attestation.project_profile_id,
                    subject_kind=attestation.subject_kind,
                    subject_id=attestation.subject_id,
                    template_id=profile_template_id,
                    authority_bearing_capabilities=authority_bearing_capabilities(capabilities),
                    build_digest=attestation.artifact_digest,
                    pending_since=now,
                )
                connection.execute(
                    f"""
                    INSERT INTO {self._schema}.entitlement_owner_notice (
                        notice_id, schema_version, installation_id,
                        entitlement_generation, delivery_state, document, pending_since
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    (
                        notice.notice_id,
                        OWNER_NOTICE_VERSION,
                        notice.installation_id,
                        notice.entitlement_generation,
                        notice.delivery_state.value,
                        json.dumps(notice.to_document()),
                        notice.pending_since,
                    ),
                )
                updated = connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_current_generation
                    SET registration_generation = %s,
                        entitlement_generation = %s,
                        profile_template_id = %s,
                        template_digest = %s,
                        build_digest = %s,
                        source_commit_sha = %s,
                        source_tree_sha = %s,
                        registration_document_digest = %s,
                        granted_capabilities = %s::jsonb,
                        granted_capabilities_digest = %s,
                        lifecycle_status = %s,
                        notice_id = %s,
                        notice_delivery_state = %s,
                        notice_pending_since = %s,
                        granted_at = %s,
                        last_event_id = %s,
                        updated_at = now()
                    WHERE installation_id = %s
                      AND entitlement_generation = %s
                      AND lifecycle_status = %s
                    """,
                    (
                        attestation.registration_generation,
                        next_generation,
                        profile_template_id,
                        attestation.template_digest,
                        attestation.artifact_digest,
                        attestation.source_commit_sha,
                        attestation.source_tree_sha,
                        registration["document_digest"],
                        json.dumps(expected_capabilities),
                        capabilities_digest,
                        EntitlementLifecycleStatus.ACTIVE.value,
                        notice.notice_id,
                        OwnerNoticeDeliveryState.PENDING.value,
                        notice.pending_since,
                        now,
                        grant_event.event_id,
                        attestation.installation_id,
                        int(current["entitlement_generation"]),
                        EntitlementLifecycleStatus.REVOKED.value,
                    ),
                )
                if updated.rowcount != 1:
                    raise EntitlementControlPlaneError("generation CAS failed on restore")
                outcome = EntitlementGrantOutcome(
                    granted_capabilities=tuple(sorted(capabilities)),
                    entitlement_generation=next_generation,
                    notice=notice,
                    event_id=grant_event.event_id,
                    idempotent_replay=False,
                )
                self._store_idempotent_outcome(
                    connection,
                    operation="restore",
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document=self._outcome_to_document(outcome),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return outcome

    def uninstall(
        self,
        installation_id: str,
        *,
        owner_action: EntitlementOwnerActionV1,
    ) -> str:
        request_digest = document_digest(
            {
                "operation": "uninstall",
                "installation_id": installation_id,
                "expected_generation": owner_action.expected_generation,
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_installation(connection, installation_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    owner_action.idempotency_key_digest,
                    operation="uninstall",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
                self._claim_owner_action(
                    connection,
                    action=owner_action,
                    operation="uninstall",
                    installation_id=installation_id,
                    expected_generation=owner_action.expected_generation,
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                )
                current = connection.execute(
                    f"""
                    SELECT entitlement_generation
                    FROM {self._schema}.entitlement_current_generation
                    WHERE installation_id = %s
                    FOR UPDATE
                    """,
                    (installation_id,),
                ).fetchone()
                if current is None:
                    raise EntitlementControlPlaneError("installation not found")
                if int(current["entitlement_generation"]) != owner_action.expected_generation:
                    raise EntitlementControlPlaneError("generation CAS failed on uninstall")
                event_id = f"evt-{uuid.uuid4().hex}"
                event = EntitlementEventV1(
                    event_type=EntitlementEventType.UNINSTALL,
                    installation_id=installation_id,
                    entitlement_generation=int(owner_action.expected_generation),
                    expected_generation=int(owner_action.expected_generation),
                    actor_id=owner_action.actor_id,
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    scope_digest=document_digest({"installation_id": installation_id}),
                    evidence_binding_digest=document_digest({"uninstalled": True}),
                    event_id=event_id,
                )
                self._insert_event(connection, event)
                updated = connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_current_generation
                    SET lifecycle_status = %s, last_event_id = %s, updated_at = now()
                    WHERE installation_id = %s AND entitlement_generation = %s
                    """,
                    (
                        EntitlementLifecycleStatus.UNINSTALLED.value,
                        event.event_id,
                        installation_id,
                            owner_action.expected_generation,
                    ),
                )
                if updated.rowcount != 1:
                    raise EntitlementControlPlaneError("generation CAS failed on uninstall")
                self._store_idempotent_outcome(
                    connection,
                    operation="uninstall",
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document={"event_id": event.event_id},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return event_id

    def supersede_registration(
        self,
        registration: ProjectRegistrationV1,
        *,
        prior_generation: int,
        owner_action: EntitlementOwnerActionV1,
    ) -> str:
        try:
            registration.verify_owner_signature(
                owner_authority_secret=self._owner_authority_secret
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"project registration signature invalid: {exc}"
            ) from exc
        request_digest = document_digest(
            {
                "operation": "supersede",
                "project_profile_id": registration.project_profile_id,
                "prior_generation": prior_generation,
                "replacement_registration": registration.to_document(),
                "replacement_generation": registration.registration_generation,
                "actor_id": owner_action.actor_id,
                "idempotency_key_digest": owner_action.idempotency_key_digest,
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_project(connection, registration.project_profile_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    owner_action.idempotency_key_digest,
                    operation="supersede_registration",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
                self._claim_owner_action(
                    connection,
                    action=owner_action,
                    operation="supersede_registration",
                    installation_id=registration.project_profile_id,
                    expected_generation=prior_generation,
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                )
                if registration.registration_generation <= prior_generation:
                    raise EntitlementControlPlaneError(
                        "registration generation must strictly increase on supersede"
                    )
                slack = connection.execute(
                    f"""
                    SELECT verified, health_proof_expires_at
                    FROM {self._schema}.entitlement_slack_destination
                    WHERE destination_id = %s
                    """,
                    (registration.slack_destination_id,),
                ).fetchone()
                if (
                    slack is None
                    or not slack["verified"]
                    or slack["health_proof_expires_at"] <= self._clock()
                ):
                    raise EntitlementControlPlaneError(
                        "verified owner slack destination is required"
                    )
                template = connection.execute(
                    f"""
                    SELECT profile_template_id FROM {self._schema}.entitlement_signed_template
                    WHERE profile_template_id = %s
                    """,
                    (registration.entitlement_template_id,),
                ).fetchone()
                if template is None:
                    raise EntitlementControlPlaneError(
                        "owner-approved signed template is not registered"
                    )
                # The project lock freezes the membership set against grant/restore.
                # Take installation advisory locks in a stable order before any
                # current-generation row lock/update, matching the global order
                # project -> installation -> row used by grant and restore.
                affected_installations = connection.execute(
                    f"""
                    SELECT installation_id
                    FROM {self._schema}.entitlement_current_generation
                    WHERE project_profile_id = %s
                      AND registration_generation = %s
                    ORDER BY installation_id
                    """,
                    (registration.project_profile_id, prior_generation),
                ).fetchall()
                for affected in affected_installations:
                    self._lock_installation(connection, str(affected["installation_id"]))
                superseded = connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_project_registration
                    SET superseded_at = now()
                    WHERE project_profile_id = %s
                      AND registration_generation = %s
                      AND superseded_at IS NULL
                    """,
                    (registration.project_profile_id, prior_generation),
                )
                if superseded.rowcount != 1:
                    raise EntitlementControlPlaneError(
                        "registration generation CAS failed on supersede"
                    )
                connection.execute(
                    f"""
                    INSERT INTO {self._schema}.entitlement_project_registration (
                        project_profile_id, registration_generation, schema_version,
                        document, document_digest, profile_template_id,
                        owner_policy_digest, slack_destination_id
                    ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    """,
                    (
                        registration.project_profile_id,
                        registration.registration_generation,
                        PROJECT_REGISTRATION_VERSION,
                        json.dumps(registration.to_document()),
                        registration.digest,
                        registration.entitlement_template_id,
                        registration.owner_policy_digest,
                        registration.slack_destination_id,
                    ),
                )
                connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_current_generation
                    SET lifecycle_status = %s, updated_at = now()
                    WHERE project_profile_id = %s
                      AND registration_generation = %s
                    """,
                    (
                        EntitlementLifecycleStatus.SUPERSEDED.value,
                        registration.project_profile_id,
                        prior_generation,
                    ),
                )
                event_id = f"evt-{uuid.uuid4().hex}"
                event = EntitlementEventV1(
                    event_type=EntitlementEventType.SUPERSEDE,
                    installation_id=registration.project_profile_id,
                    entitlement_generation=registration.registration_generation,
                    expected_generation=prior_generation,
                    actor_id=owner_action.actor_id,
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    scope_digest=registration.digest,
                    evidence_binding_digest=registration.owner_policy_digest,
                    event_id=event_id,
                )
                self._insert_event(connection, event)
                connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_current_generation
                    SET last_event_id = %s, updated_at = now()
                    WHERE project_profile_id = %s
                      AND registration_generation = %s
                      AND lifecycle_status = %s
                    """,
                    (
                        event.event_id,
                        registration.project_profile_id,
                        prior_generation,
                        EntitlementLifecycleStatus.SUPERSEDED.value,
                    ),
                )
                self._store_idempotent_outcome(
                    connection,
                    operation="supersede_registration",
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document={"event_id": event.event_id},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return event_id

    def mark_notice_failed(
        self,
        notice_id: str,
        *,
        idempotency_key_digest: str,
        route_proof: NoticeRouteProofV1,
    ) -> str:
        now = self._clock()
        request_digest = document_digest(
            {
                "operation": "mark_notice_failed",
                "notice_id": notice_id,
                "route_proof": route_proof.to_document(),
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                cached = self._load_idempotent_outcome(
                    connection,
                    idempotency_key_digest,
                    operation="mark_notice_failed",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
                row = connection.execute(
                    f"""
                    SELECT installation_id, entitlement_generation, delivery_state, document
                    FROM {self._schema}.entitlement_owner_notice
                    WHERE notice_id = %s
                    FOR UPDATE
                    """,
                    (notice_id,),
                ).fetchone()
                if row is None:
                    raise EntitlementControlPlaneError("notice not found")
                if row["delivery_state"] != OwnerNoticeDeliveryState.PENDING.value:
                    raise EntitlementControlPlaneError("notice is not pending")
                current = connection.execute(
                    f"""
                    SELECT cg.lifecycle_status, pr.slack_destination_id
                    FROM {self._schema}.entitlement_current_generation cg
                    JOIN {self._schema}.entitlement_project_registration pr
                      ON pr.project_profile_id = cg.project_profile_id
                     AND pr.registration_generation = cg.registration_generation
                    WHERE cg.installation_id = %s
                      AND cg.entitlement_generation = %s
                      AND cg.notice_id = %s
                    FOR UPDATE
                    """,
                    (row["installation_id"], row["entitlement_generation"], notice_id),
                ).fetchone()
                if current is None or current["lifecycle_status"] not in (
                    EntitlementLifecycleStatus.ACTIVE.value,
                    EntitlementLifecycleStatus.SUSPENDED.value,
                ):
                    raise EntitlementControlPlaneError(
                        "notice generation is no longer eligible for suspension"
                    )
                self._claim_notice_route_proof(
                    connection,
                    proof=route_proof,
                    notice_id=notice_id,
                    installation_id=str(row["installation_id"]),
                    entitlement_generation=int(row["entitlement_generation"]),
                    destination_id=str(current["slack_destination_id"]),
                    outcome="FAILED",
                )
                event_id = f"evt-{uuid.uuid4().hex}"
                event = EntitlementEventV1(
                    event_type=EntitlementEventType.SUSPEND,
                    installation_id=row["installation_id"],
                    entitlement_generation=int(row["entitlement_generation"]),
                    expected_generation=int(row["entitlement_generation"]),
                    actor_id="notice-failure",
                    idempotency_key_digest=idempotency_key_digest,
                    scope_digest=document_digest({"notice_id": notice_id, "terminal": True}),
                    evidence_binding_digest=document_digest({"failed_at": now.isoformat()}),
                    event_id=event_id,
                )
                self._insert_event(connection, event)
                document = dict(row["document"])
                document["delivery_state"] = OwnerNoticeDeliveryState.FAILED.value
                connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_owner_notice
                    SET delivery_state = %s, failed_at = %s, document = %s::jsonb
                    WHERE notice_id = %s
                    """,
                    (
                        OwnerNoticeDeliveryState.FAILED.value,
                        now,
                        json.dumps(document),
                        notice_id,
                    ),
                )
                updated = connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_current_generation
                    SET lifecycle_status = %s,
                        notice_delivery_state = %s,
                        last_event_id = %s,
                        updated_at = now()
                    WHERE installation_id = %s
                      AND entitlement_generation = %s
                      AND notice_id = %s
                      AND lifecycle_status IN (%s, %s)
                    """,
                    (
                        EntitlementLifecycleStatus.SUSPENDED.value,
                        OwnerNoticeDeliveryState.FAILED.value,
                        event.event_id,
                        row["installation_id"],
                        row["entitlement_generation"],
                        notice_id,
                        EntitlementLifecycleStatus.ACTIVE.value,
                        EntitlementLifecycleStatus.SUSPENDED.value,
                    ),
                )
                if updated.rowcount != 1:
                    raise EntitlementControlPlaneError("notice failure CAS failed")
                self._store_idempotent_outcome(
                    connection,
                    operation="mark_notice_failed",
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document={"event_id": event.event_id},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return event_id

    def mark_notice_delivered(
        self,
        notice_id: str,
        *,
        idempotency_key_digest: str,
        route_proof: NoticeRouteProofV1,
    ) -> str:
        now = self._clock()
        request_digest = document_digest(
            {
                "operation": "mark_notice_delivered",
                "notice_id": notice_id,
                "route_proof": route_proof.to_document(),
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                cached = self._load_idempotent_outcome(
                    connection,
                    idempotency_key_digest,
                    operation="mark_notice_delivered",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
                row = connection.execute(
                    f"""
                    SELECT installation_id, entitlement_generation, delivery_state,
                           document, pending_since
                    FROM {self._schema}.entitlement_owner_notice
                    WHERE notice_id = %s
                    FOR UPDATE
                    """,
                    (notice_id,),
                ).fetchone()
                if row is None:
                    raise EntitlementControlPlaneError("notice not found")
                if row["delivery_state"] != OwnerNoticeDeliveryState.PENDING.value:
                    raise EntitlementControlPlaneError("notice is not deliverable")
                if now >= row["pending_since"] + timedelta(seconds=NOTICE_SUSPENSION_SECONDS):
                    raise EntitlementControlPlaneError("late notice delivery cannot reactivate authority")
                current = connection.execute(
                    f"""
                    SELECT cg.lifecycle_status, cg.notice_delivery_state,
                           cg.last_event_id, pr.slack_destination_id
                    FROM {self._schema}.entitlement_current_generation cg
                    JOIN {self._schema}.entitlement_project_registration pr
                      ON pr.project_profile_id = cg.project_profile_id
                     AND pr.registration_generation = cg.registration_generation
                    WHERE cg.installation_id = %s
                      AND cg.entitlement_generation = %s
                      AND cg.notice_id = %s
                    FOR UPDATE
                    """,
                    (row["installation_id"], row["entitlement_generation"], notice_id),
                ).fetchone()
                if current is None:
                    raise EntitlementControlPlaneError("notice generation mismatch")
                if current["lifecycle_status"] != EntitlementLifecycleStatus.ACTIVE.value:
                    raise EntitlementControlPlaneError(
                        "late or terminal notice delivery cannot reactivate authority"
                    )
                self._claim_notice_route_proof(
                    connection,
                    proof=route_proof,
                    notice_id=notice_id,
                    installation_id=str(row["installation_id"]),
                    entitlement_generation=int(row["entitlement_generation"]),
                    destination_id=str(current["slack_destination_id"]),
                    outcome="DELIVERED",
                )
                event_id = str(current["last_event_id"])
                document = dict(row["document"])
                document["delivery_state"] = OwnerNoticeDeliveryState.DELIVERED.value
                document["delivery_proof_digest"] = route_proof.evidence_digest
                connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_owner_notice
                    SET delivery_state = %s,
                        delivery_proof_digest = %s,
                        delivered_at = %s,
                        document = %s::jsonb
                    WHERE notice_id = %s
                    """,
                    (
                        OwnerNoticeDeliveryState.DELIVERED.value,
                        route_proof.evidence_digest,
                        now,
                        json.dumps(document),
                        notice_id,
                    ),
                )
                updated = connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_current_generation
                    SET notice_delivery_state = %s, updated_at = now()
                    WHERE installation_id = %s
                      AND entitlement_generation = %s
                      AND notice_id = %s
                      AND lifecycle_status = %s
                      AND notice_delivery_state = %s
                    """,
                    (
                        OwnerNoticeDeliveryState.DELIVERED.value,
                        row["installation_id"],
                        row["entitlement_generation"],
                        notice_id,
                        EntitlementLifecycleStatus.ACTIVE.value,
                        OwnerNoticeDeliveryState.PENDING.value,
                    ),
                )
                if updated.rowcount != 1:
                    raise EntitlementControlPlaneError(
                        "notice delivery generation CAS failed"
                    )
                self._store_idempotent_outcome(
                    connection,
                    operation="mark_notice_delivered",
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document={"event_id": event_id},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return event_id

    def acknowledge_notice(
        self,
        notice_id: str,
        *,
        ack_proof_digest: str,
        owner_action: EntitlementOwnerActionV1,
    ) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", ack_proof_digest):
            raise EntitlementControlPlaneError(
                "acknowledgement proof digest must be lowercase sha256"
            )
        now = self._clock()
        request_digest = document_digest(
            {
                "operation": "acknowledge_notice",
                "notice_id": notice_id,
                "ack_proof_digest": ack_proof_digest,
                "installation_id": owner_action.installation_id,
                "expected_generation": owner_action.expected_generation,
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                cached = self._load_idempotent_outcome(
                    connection,
                    owner_action.idempotency_key_digest,
                    operation="acknowledge_notice",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
                row = connection.execute(
                    f"""
                    SELECT installation_id, entitlement_generation, delivery_state, document
                    FROM {self._schema}.entitlement_owner_notice
                    WHERE notice_id = %s
                    FOR UPDATE
                    """,
                    (notice_id,),
                ).fetchone()
                if row is None:
                    raise EntitlementControlPlaneError("notice not found")
                if (
                    row["installation_id"] != owner_action.installation_id
                    or int(row["entitlement_generation"])
                    != owner_action.expected_generation
                ):
                    raise EntitlementControlPlaneError(
                        "owner action does not bind the notice generation"
                    )
                self._claim_owner_action(
                    connection,
                    action=owner_action,
                    operation="acknowledge_notice",
                    installation_id=str(row["installation_id"]),
                    expected_generation=int(row["entitlement_generation"]),
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                )
                if row["delivery_state"] != OwnerNoticeDeliveryState.DELIVERED.value:
                    raise EntitlementControlPlaneError("notice must be delivered before ack")
                current = connection.execute(
                    f"""
                    SELECT notice_id, last_event_id
                    FROM {self._schema}.entitlement_current_generation
                    WHERE installation_id = %s
                      AND entitlement_generation = %s
                      AND notice_id = %s
                    FOR UPDATE
                    """,
                    (row["installation_id"], row["entitlement_generation"], notice_id),
                ).fetchone()
                if current is None:
                    raise EntitlementControlPlaneError("notice generation mismatch")
                event_id = str(current["last_event_id"])
                document = dict(row["document"])
                document["delivery_state"] = OwnerNoticeDeliveryState.ACKNOWLEDGED.value
                document["ack_proof_digest"] = ack_proof_digest
                connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_owner_notice
                    SET delivery_state = %s,
                        acknowledged_at = %s,
                        document = %s::jsonb
                    WHERE notice_id = %s
                    """,
                    (
                        OwnerNoticeDeliveryState.ACKNOWLEDGED.value,
                        now,
                        json.dumps(document),
                        notice_id,
                    ),
                )
                updated = connection.execute(
                    f"""
                    UPDATE {self._schema}.entitlement_current_generation
                    SET notice_delivery_state = %s,
                        updated_at = now()
                    WHERE installation_id = %s
                      AND entitlement_generation = %s
                      AND notice_id = %s
                    """,
                    (
                        OwnerNoticeDeliveryState.ACKNOWLEDGED.value,
                        row["installation_id"],
                        row["entitlement_generation"],
                        notice_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise EntitlementControlPlaneError(
                        "notice acknowledgement generation CAS failed"
                    )
                self._store_idempotent_outcome(
                    connection,
                    operation="acknowledge_notice",
                    idempotency_key_digest=owner_action.idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document={"event_id": event_id},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return event_id

    def query_action_boundary(
        self, query: EntitlementActionBoundaryQuery
    ) -> EntitlementActionBoundaryResult:
        trusted_now = self._clock()
        with self._pool.connection() as connection, connection.transaction():
            # Eligibility and the immutable effect claim are one serializable
            # critical section relative to every authority mutation.
            self._lock_project(connection, query.project_profile_id)
            self._lock_installation(connection, query.installation_id)
            if connection.execute(
                f"""
                SELECT 1 FROM {self._schema}.entitlement_preemptive_revocation
                WHERE installation_id = %s
                """,
                (query.installation_id,),
            ).fetchone():
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=None,
                    lifecycle_status=EntitlementLifecycleStatus.REVOKED,
                    notice_delivery_state=None,
                    audit_event_ids=(),
                    denial_reason="preemptive revocation",
                )
            row = connection.execute(
                f"""
                SELECT cg.*, pr.document AS registration_document,
                       pr.document_digest AS live_registration_document_digest,
                       pr.owner_policy_digest AS registration_owner_policy_digest,
                       pr.superseded_at, st.template_digest AS live_template_digest,
                       st.capabilities AS live_template_capabilities,
                       st.owner_policy_digest AS template_owner_policy_digest,
                       sd.verified AS slack_destination_verified,
                       sd.health_proof_expires_at AS slack_health_expires_at
                FROM {self._schema}.entitlement_current_generation cg
                JOIN {self._schema}.entitlement_project_registration pr
                  ON pr.project_profile_id = cg.project_profile_id
                 AND pr.registration_generation = cg.registration_generation
                JOIN {self._schema}.entitlement_signed_template st
                  ON st.profile_template_id = cg.profile_template_id
                LEFT JOIN {self._schema}.entitlement_slack_destination sd
                  ON sd.destination_id = pr.slack_destination_id
                WHERE cg.installation_id = %s
                  AND cg.project_profile_id = %s
                  AND cg.provider_id = %s
                  AND cg.subject_id = %s
                FOR UPDATE OF cg, pr, st
                """,
                (
                    query.installation_id,
                    query.project_profile_id,
                    query.provider_id,
                    query.subject_id,
                ),
            ).fetchone()
            if row is None:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=None,
                    lifecycle_status=None,
                    notice_delivery_state=None,
                    audit_event_ids=(),
                    denial_reason="no live entitlement",
                )
            if row["superseded_at"] is not None:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=EntitlementLifecycleStatus.SUPERSEDED,
                    notice_delivery_state=OwnerNoticeDeliveryState(
                        row["notice_delivery_state"]
                    ),
                    audit_event_ids=(str(row["last_event_id"]),),
                    denial_reason="registration superseded",
                )
            lifecycle = EntitlementLifecycleStatus(row["lifecycle_status"])
            notice_state = OwnerNoticeDeliveryState(row["notice_delivery_state"])
            audit_ids = (str(row["last_event_id"]),)
            if lifecycle in (
                EntitlementLifecycleStatus.REVOKED,
                EntitlementLifecycleStatus.UNINSTALLED,
                EntitlementLifecycleStatus.SUPERSEDED,
            ):
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason=f"lifecycle {lifecycle.value}",
                )
            if notice_state == OwnerNoticeDeliveryState.FAILED:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="terminal notice failure",
                )
            deadline = row["notice_pending_since"] + timedelta(
                seconds=NOTICE_SUSPENSION_SECONDS
            )
            if (
                notice_state == OwnerNoticeDeliveryState.PENDING
                and trusted_now >= deadline
            ):
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=EntitlementLifecycleStatus.SUSPENDED,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="notice deadline expired",
                )
            if lifecycle == EntitlementLifecycleStatus.SUSPENDED:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="notice suspension active",
                )
            proof = query.runtime_proof
            try:
                proof.verify_signature(
                    trusted_verifier_secret=self._trusted_verifier_secret
                )
            except EntitlementContractError:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="runtime evidence signature invalid",
                )
            if proof.issued_at > trusted_now or proof.expires_at <= trusted_now:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="runtime evidence is not fresh",
                )
            if (
                proof.installation_id != query.installation_id
                or proof.build_digest != query.current_build_digest
                or proof.action_type != query.action_type
                or proof.destination_id != query.destination_id
                or proof.effect_digest != query.effect_digest
                or proof.source_commit_sha != row["source_commit_sha"]
                or proof.source_tree_sha != row["source_tree_sha"]
            ):
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="runtime evidence binding mismatch",
                )
            if (
                query.action_type == EntitlementActionType.REVIEW_EXACT_SHA
                and query.ref != proof.source_commit_sha
            ):
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="review ref is not the attested exact commit",
                )
            if row["build_digest"] != query.current_build_digest:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="build digest mismatch",
                )
            granted_capabilities = tuple(row["granted_capabilities"])
            granted_capabilities_digest = document_digest(
                {"capabilities": list(granted_capabilities)}
            )
            code_capabilities = tuple(
                sorted(effective_capabilities(str(row["profile_template_id"])))
            )
            if (
                granted_capabilities_digest != row["granted_capabilities_digest"]
                or row["template_digest"] != composed_template_digest(
                    str(row["profile_template_id"])
                )
                or row["live_template_digest"] != row["template_digest"]
                or tuple(row["live_template_capabilities"]) != code_capabilities
                or granted_capabilities != code_capabilities
                or row["registration_document_digest"]
                != row["live_registration_document_digest"]
                or row["template_owner_policy_digest"]
                != row["registration_owner_policy_digest"]
            ):
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="grant authority drift detected",
                )
            required_capability = ACTION_CAPABILITY[query.action_type]
            if required_capability not in granted_capabilities:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="capability not entitled",
                )
            registration = row["registration_document"]
            expected_destination: str | None = None
            if query.action_type == EntitlementActionType.SLACK_WRITE:
                expected_destination = str(registration["slack_destination_id"])
                slack_authority = connection.execute(
                    f"""
                    SELECT verified, health_proof_expires_at
                    FROM {self._schema}.entitlement_slack_destination
                    WHERE destination_id = %s
                    FOR SHARE
                    """,
                    (expected_destination,),
                ).fetchone()
                if (
                    slack_authority is None
                    or not slack_authority["verified"]
                    or slack_authority["health_proof_expires_at"] <= trusted_now
                ):
                    return EntitlementActionBoundaryResult(
                        permitted=False,
                        entitlement_generation=int(row["entitlement_generation"]),
                        lifecycle_status=lifecycle,
                        notice_delivery_state=notice_state,
                        audit_event_ids=audit_ids,
                        denial_reason="Slack destination health proof is stale",
                    )
            elif query.action_type == EntitlementActionType.NOTION_WRITE:
                expected_destination = str(registration["notion_destination_id"])
            elif query.action_type == EntitlementActionType.N8N_WRITE:
                if query.destination_id not in registration["n8n_workflow_ids"]:
                    expected_destination = "__denied__"
                else:
                    expected_destination = query.destination_id
            elif query.action_type == EntitlementActionType.PROVIDER_DISPATCH:
                expected_destination = str(registration["state_namespace"])
            if expected_destination is not None and query.destination_id != expected_destination:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="signed destination mismatch",
                )
            if query.action_type in SPEND_BEARING_ACTIONS:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="shared budget ledger unavailable",
                )
            scopes = [
                RepositoryScope(
                    repository=scope["repository"],
                    refs=tuple(scope["refs"]),
                    paths=tuple(scope["paths"]),
                )
                for scope in registration["repository_scopes"]
            ]
            if not any(
                scope.allows(
                    repository=query.repository, ref=query.ref, path=query.path
                )
                for scope in scopes
            ):
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="repository scope mismatch",
                )
            authorization_document = {
                "schema_version": "EntitlementAuthorizationBinding/v1",
                "query": query.to_document(),
                "runtime_proof_digest": proof.digest,
                "project_profile_id": query.project_profile_id,
                "provider_id": query.provider_id,
                "subject_id": query.subject_id,
                "installation_id": query.installation_id,
                "repository": query.repository,
                "ref": query.ref,
                "path": query.path,
                "action_type": query.action_type.value,
                "destination_id": query.destination_id,
                "effect_digest": query.effect_digest,
                "build_digest": query.current_build_digest,
                "source_commit_sha": proof.source_commit_sha,
                "source_tree_sha": proof.source_tree_sha,
                "registration_generation": int(row["registration_generation"]),
                "registration_document_digest": str(
                    row["registration_document_digest"]
                ),
                "entitlement_generation": int(row["entitlement_generation"]),
                "lifecycle_status": lifecycle.value,
                "notice_id": str(row["notice_id"]),
                "notice_delivery_state": notice_state.value,
                "notice_deadline": deadline.isoformat(),
                "profile_template_id": str(row["profile_template_id"]),
                "template_digest": str(row["template_digest"]),
            }
            binding_digest = document_digest(authorization_document)
            claimed = connection.execute(
                f"""
                INSERT INTO {self._schema}.entitlement_runtime_authorization_claim (
                    nonce_digest, installation_id, action_type, destination_id,
                    effect_digest, build_digest, source_commit_sha, source_tree_sha,
                    authorization_document, authorization_binding_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (nonce_digest) DO NOTHING
                RETURNING authorization_binding_digest
                """,
                (
                    proof.nonce_digest,
                    query.installation_id,
                    query.action_type.value,
                    query.destination_id,
                    query.effect_digest,
                    query.current_build_digest,
                    proof.source_commit_sha,
                    proof.source_tree_sha,
                    json.dumps(authorization_document),
                    binding_digest,
                ),
            ).fetchone()
            if claimed is None:
                prior_claim = connection.execute(
                    f"""
                    SELECT installation_id, action_type, destination_id, effect_digest,
                           build_digest, source_commit_sha, source_tree_sha,
                           authorization_document, authorization_binding_digest
                    FROM {self._schema}.entitlement_runtime_authorization_claim
                    WHERE nonce_digest = %s
                    """,
                    (proof.nonce_digest,),
                ).fetchone()
                expected_claim = (
                    query.installation_id,
                    query.action_type.value,
                    query.destination_id,
                    query.effect_digest,
                    query.current_build_digest,
                    proof.source_commit_sha,
                    proof.source_tree_sha,
                    binding_digest,
                )
                actual_claim = None if prior_claim is None else (
                    str(prior_claim["installation_id"]),
                    str(prior_claim["action_type"]),
                    prior_claim["destination_id"],
                    str(prior_claim["effect_digest"]),
                    str(prior_claim["build_digest"]),
                    str(prior_claim["source_commit_sha"]),
                    str(prior_claim["source_tree_sha"]),
                    dict(prior_claim["authorization_document"]),
                    str(prior_claim["authorization_binding_digest"]),
                )
                expected_claim = expected_claim[:-1] + (
                    authorization_document,
                    expected_claim[-1],
                )
                if actual_claim != expected_claim:
                    return EntitlementActionBoundaryResult(
                        permitted=False,
                        entitlement_generation=int(row["entitlement_generation"]),
                        lifecycle_status=lifecycle,
                        notice_delivery_state=notice_state,
                        audit_event_ids=audit_ids,
                        denial_reason="runtime authorization nonce binding mismatch",
                    )
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="runtime authorization already consumed",
                    authorization_binding_digest=str(
                        prior_claim["authorization_binding_digest"]
                    ),
                )
            return EntitlementActionBoundaryResult(
                permitted=True,
                entitlement_generation=int(row["entitlement_generation"]),
                lifecycle_status=lifecycle,
                notice_delivery_state=notice_state,
                audit_event_ids=audit_ids,
                denial_reason=None,
                authorization_binding_digest=binding_digest,
            )

    def authoritative_capabilities(
        self, installation_id: str, *, runtime_proof: RuntimeEvidenceProofV1
    ) -> frozenset[str]:
        moment = self._clock()
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT cg.lifecycle_status, cg.build_digest, cg.source_commit_sha,
                       cg.source_tree_sha, cg.profile_template_id,
                       cg.template_digest, cg.registration_document_digest,
                       cg.granted_capabilities, cg.granted_capabilities_digest,
                       cg.notice_delivery_state, cg.notice_pending_since,
                       pr.superseded_at, pr.document_digest AS live_registration_document_digest,
                       pr.owner_policy_digest AS registration_owner_policy_digest,
                       st.template_digest AS live_template_digest,
                       st.capabilities AS live_template_capabilities,
                       st.owner_policy_digest AS template_owner_policy_digest
                FROM {self._schema}.entitlement_current_generation cg
                JOIN {self._schema}.entitlement_project_registration pr
                  ON pr.project_profile_id = cg.project_profile_id
                 AND pr.registration_generation = cg.registration_generation
                JOIN {self._schema}.entitlement_signed_template st
                  ON st.profile_template_id = cg.profile_template_id
                WHERE cg.installation_id = %s
                """,
                (installation_id,),
            ).fetchone()
            if row is None or row["superseded_at"] is not None:
                return frozenset()
            if row["lifecycle_status"] != EntitlementLifecycleStatus.ACTIVE.value:
                return frozenset()
            if row["notice_delivery_state"] == OwnerNoticeDeliveryState.FAILED.value:
                return frozenset()
            deadline = row["notice_pending_since"] + timedelta(
                seconds=NOTICE_SUSPENSION_SECONDS
            )
            if (
                row["notice_delivery_state"] == OwnerNoticeDeliveryState.PENDING.value
                and moment >= deadline
            ):
                return frozenset()
            try:
                runtime_proof.verify_signature(
                    trusted_verifier_secret=self._trusted_verifier_secret
                )
            except EntitlementContractError:
                return frozenset()
            if (
                runtime_proof.installation_id != installation_id
                or runtime_proof.build_digest != row["build_digest"]
                or runtime_proof.source_commit_sha != row["source_commit_sha"]
                or runtime_proof.source_tree_sha != row["source_tree_sha"]
                or runtime_proof.issued_at > moment
                or runtime_proof.expires_at <= moment
            ):
                return frozenset()
            granted = tuple(row["granted_capabilities"])
            code_capabilities = tuple(
                sorted(effective_capabilities(str(row["profile_template_id"])))
            )
            if (
                document_digest({"capabilities": list(granted)})
                != row["granted_capabilities_digest"]
                or row["template_digest"]
                != composed_template_digest(str(row["profile_template_id"]))
                or row["live_template_digest"] != row["template_digest"]
                or tuple(row["live_template_capabilities"]) != code_capabilities
                or granted != code_capabilities
                or row["registration_document_digest"]
                != row["live_registration_document_digest"]
                or row["template_owner_policy_digest"]
                != row["registration_owner_policy_digest"]
            ):
                return frozenset()
            return frozenset(granted)

    def evaluate_notice_suspensions(self, *, now: datetime | None = None) -> tuple[str, ...]:
        moment = now or self._clock()
        deadline = moment - timedelta(seconds=NOTICE_SUSPENSION_SECONDS)
        suspended: list[str] = []
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                rows = connection.execute(
                    f"""
                    SELECT cg.installation_id, cg.entitlement_generation, cg.notice_id
                    FROM {self._schema}.entitlement_current_generation cg
                    JOIN {self._schema}.entitlement_owner_notice n
                      ON n.notice_id = cg.notice_id
                    WHERE cg.lifecycle_status = %s
                      AND n.delivery_state = %s
                      AND cg.notice_pending_since <= %s
                    FOR UPDATE
                    """,
                    (
                        EntitlementLifecycleStatus.ACTIVE.value,
                        OwnerNoticeDeliveryState.PENDING.value,
                        deadline,
                    ),
                ).fetchall()
                for row in rows:
                    idempotency = document_digest(
                        {
                            "suspend": row["notice_id"],
                            "generation": row["entitlement_generation"],
                        }
                    )
                    event_id = f"evt-{uuid.uuid4().hex}"
                    event = EntitlementEventV1(
                        event_type=EntitlementEventType.SUSPEND,
                        installation_id=row["installation_id"],
                        entitlement_generation=int(row["entitlement_generation"]),
                        expected_generation=int(row["entitlement_generation"]),
                        actor_id="notice-deadline",
                        idempotency_key_digest=idempotency,
                        scope_digest=document_digest({"reason": "notice_deadline"}),
                        evidence_binding_digest=document_digest(
                            {"deadline_seconds": NOTICE_SUSPENSION_SECONDS}
                        ),
                        event_id=event_id,
                    )
                    inserted = connection.execute(
                        f"""
                        INSERT INTO {self._schema}.entitlement_event (
                            event_id, schema_version, event_type, installation_id,
                            entitlement_generation, expected_generation, actor_id,
                            idempotency_key_digest, scope_digest, evidence_binding_digest,
                            document
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (idempotency_key_digest) DO NOTHING
                        RETURNING event_id
                        """,
                        (
                            event.event_id,
                            ENTITLEMENT_EVENT_VERSION,
                            event.event_type.value,
                            event.installation_id,
                            event.entitlement_generation,
                            event.expected_generation,
                            event.actor_id,
                            event.idempotency_key_digest,
                            event.scope_digest,
                            event.evidence_binding_digest,
                            json.dumps(event.signing_document()),
                        ),
                    ).fetchone()
                    if inserted is None:
                        continue
                    updated = connection.execute(
                        f"""
                        UPDATE {self._schema}.entitlement_current_generation
                        SET lifecycle_status = %s, last_event_id = %s, updated_at = now()
                        WHERE installation_id = %s
                          AND entitlement_generation = %s
                          AND lifecycle_status = %s
                          AND notice_id = %s
                        """,
                        (
                            EntitlementLifecycleStatus.SUSPENDED.value,
                            inserted["event_id"],
                            row["installation_id"],
                            row["entitlement_generation"],
                            EntitlementLifecycleStatus.ACTIVE.value,
                            row["notice_id"],
                        ),
                    )
                    if updated.rowcount == 1:
                        suspended.append(row["installation_id"])
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return tuple(suspended)


def _load_pool_dependencies() -> tuple[Any, Any]:
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        raise EntitlementControlPlaneError(
            "psycopg and psycopg-pool are required for the entitlement store"
        ) from exc
    return ConnectionPool, dict_row


def open_entitlement_control_plane(
    dsn: str,
    *,
    schema: str = DEFAULT_ENTITLEMENT_SCHEMA,
    trusted_verifier_secret_file: str,
    route_verifier_secret_file: str,
    owner_authority_secret_file: str,
    allow_insecure_local_transport: bool = False,
) -> EntitlementStoreHandle:
    schema = _require_schema(schema)
    trusted_verifier_secret = _read_secret_file(trusted_verifier_secret_file)
    route_verifier_secret = _read_secret_file(route_verifier_secret_file)
    owner_authority_secret = _read_secret_file(owner_authority_secret_file)
    if len({trusted_verifier_secret, route_verifier_secret, owner_authority_secret}) != 3:
        raise EntitlementControlPlaneError(
            "owner, install-verifier, and route-verifier secrets must be cryptographically separated"
        )
    pool_type, dict_row = _load_pool_dependencies()
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError as exc:
        raise EntitlementControlPlaneError("psycopg is required") from exc
    try:
        parsed = _parse_connection_target(
            dsn,
            conninfo_to_dict=conninfo_to_dict,
            allow_insecure_local_transport=allow_insecure_local_transport,
        )
    except PostgresCheckpointConfigurationError as exc:
        raise EntitlementControlPlaneError(str(exc)) from exc
    storage_identity = _storage_identity(parsed, schema=schema)
    pool = pool_type(
        conninfo=dsn,
        min_size=1,
        max_size=6,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=False,
    )
    pool.open(wait=True)
    try:
        with pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"entitlement-bootstrap:{schema}",),
                )
                connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                _bootstrap_schema(
                    connection, schema=schema, storage_identity=storage_identity
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
    except Exception:
        pool.close()
        raise
    return EntitlementStoreHandle(
        pool=pool,
        schema=schema,
        storage_identity=storage_identity,
        trusted_verifier_secret=trusted_verifier_secret,
        route_verifier_secret=route_verifier_secret,
        owner_authority_secret=owner_authority_secret,
    )


def entitlement_store_from_handle(handle: EntitlementStoreHandle) -> EntitlementControlPlaneStore:
    return EntitlementControlPlaneStore(
        handle.pool,
        schema=handle.schema,
        storage_identity=handle.storage_identity,
        trusted_verifier_secret=handle.trusted_verifier_secret,
        route_verifier_secret=handle.route_verifier_secret,
        owner_authority_secret=handle.owner_authority_secret,
    )
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:25 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 20:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:40 UTC | END]]
