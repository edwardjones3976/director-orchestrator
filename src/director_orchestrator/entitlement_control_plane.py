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
    EntitlementActionBoundaryQuery,
    EntitlementActionBoundaryResult,
    EntitlementContractError,
    EntitlementEventType,
    EntitlementEventV1,
    EntitlementLifecycleStatus,
    InstallAttestationV1,
    OwnerNoticeDeliveryState,
    OwnerNoticeV1,
    ProjectRegistrationV1,
    RepositoryScope,
    SignedEntitlementTemplateV1,
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
    owner_authority_secret: bytes

    def close(self) -> None:
        self.pool.close()


def _require_schema(schema: str) -> str:
    if not _SCHEMA_PATTERN.fullmatch(schema):
        raise EntitlementControlPlaneError(
            "entitlement schema must match [a-z0-9_]+_v<N>"
        )
    return schema


def _read_secret_file(path: str) -> bytes:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise EntitlementControlPlaneError("secret file is missing")
    value = resolved.read_text(encoding="utf-8").strip()
    if not value:
        raise EntitlementControlPlaneError("secret file is empty")
    return value.encode("utf-8")


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
            build_digest text NOT NULL,
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


class EntitlementControlPlaneStore:
    def __init__(
        self,
        pool: Any,
        *,
        schema: str,
        storage_identity: str,
        trusted_verifier_secret: bytes,
        owner_authority_secret: bytes,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pool = pool
        self._schema = _require_schema(schema)
        self._storage_identity = storage_identity
        self._trusted_verifier_secret = trusted_verifier_secret
        self._owner_authority_secret = owner_authority_secret
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

    def _verify_owner_action(
        self,
        *,
        operation: str,
        actor_id: str,
        request_digest: str,
        owner_signature_digest: str,
    ) -> None:
        signing_document = {
            "schema_version": "EntitlementOwnerAction/v1",
            "operation": operation,
            "actor_id": actor_id,
            "request_digest": request_digest,
        }
        try:
            verify_signature(
                principal=actor_id,
                signature_digest=owner_signature_digest,
                signing_payload=signing_bytes(signing_document),
                secret=self._owner_authority_secret,
            )
        except EntitlementContractError as exc:
            raise EntitlementControlPlaneError(
                f"owner action signature invalid: {exc}"
            ) from exc

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
    ) -> None:
        signing_doc = {
            "schema_version": "OwnerSlackDestination/v1",
            "destination_id": destination_id,
            "owner_signature_principal": owner_signature_principal,
        }
        from .entitlement_contracts import verify_signature

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
        with self._pool.connection() as connection:
            connection.execute(
                f"""
                INSERT INTO {self._schema}.entitlement_slack_destination (
                    destination_id, verified, owner_signature_principal,
                    owner_signature_digest
                ) VALUES (%s, true, %s, %s)
                ON CONFLICT (destination_id) DO UPDATE SET
                    verified = EXCLUDED.verified,
                    owner_signature_principal = EXCLUDED.owner_signature_principal,
                    owner_signature_digest = EXCLUDED.owner_signature_digest,
                    verified_at = now()
                """,
                (destination_id, owner_signature_principal, owner_signature_digest),
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
                slack = connection.execute(
                    f"""
                    SELECT verified FROM {self._schema}.entitlement_slack_destination
                    WHERE destination_id = %s
                    """,
                    (registration.slack_destination_id,),
                ).fetchone()
                if slack is None or not slack["verified"]:
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
                           registration_generation
                    FROM {self._schema}.entitlement_current_generation
                    WHERE installation_id = %s
                    FOR UPDATE
                    """,
                    (attestation.installation_id,),
                ).fetchone()
                if current is not None and current["lifecycle_status"] in (
                    EntitlementLifecycleStatus.REVOKED.value,
                    EntitlementLifecycleStatus.SUPERSEDED.value,
                    EntitlementLifecycleStatus.UNINSTALLED.value,
                ):
                    raise EntitlementControlPlaneError(
                        "revoked, superseded, or uninstalled installation cannot be granted"
                    )
                registration = connection.execute(
                    f"""
                    SELECT document, profile_template_id, owner_policy_digest,
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
                expected_digest = composed_template_digest(profile_template_id)
                if attestation.template_digest != expected_digest:
                    raise EntitlementControlPlaneError("template digest mismatch")
                if attestation.owner_policy_digest != str(registration["owner_policy_digest"]):
                    raise EntitlementControlPlaneError("owner policy digest mismatch")
                slack = connection.execute(
                    f"""
                    SELECT verified FROM {self._schema}.entitlement_slack_destination
                    WHERE destination_id = %s
                    """,
                    (registration["slack_destination_id"],),
                ).fetchone()
                if slack is None or not slack["verified"]:
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
                capabilities = effective_capabilities(profile_template_id)
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
                        installation_id, nonce_digest
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        attestation.attestation_id,
                        INSTALL_ATTESTATION_VERSION,
                        attestation.digest,
                        attestation.installation_id,
                        attestation.nonce_digest,
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
                            lifecycle_status, notice_id, notice_delivery_state,
                            notice_pending_since, granted_at, last_event_id
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
        actor_id: str,
        idempotency_key_digest: str,
        owner_signature_digest: str,
    ) -> str:
        now = self._clock()
        request_digest = document_digest({"operation": "revoke", "installation_id": installation_id})
        self._verify_owner_action(
            operation="revoke",
            actor_id=actor_id,
            request_digest=request_digest,
            owner_signature_digest=owner_signature_digest,
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_installation(connection, installation_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    idempotency_key_digest,
                    operation="revoke",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
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
                if current is not None and current["lifecycle_status"] == EntitlementLifecycleStatus.REVOKED.value:
                    raise EntitlementControlPlaneError("installation already revoked")
                event_id = f"evt-{uuid.uuid4().hex}"
                event = EntitlementEventV1(
                    event_type=EntitlementEventType.REVOKE,
                    installation_id=installation_id,
                    entitlement_generation=generation,
                    expected_generation=generation if current else None,
                    actor_id=actor_id,
                    idempotency_key_digest=idempotency_key_digest,
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
                    idempotency_key_digest=idempotency_key_digest,
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
        owner_actor_id: str,
        idempotency_key_digest: str,
        owner_signature_digest: str,
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
            {"operation": "restore", "attestation_digest": attestation.digest}
        )
        self._verify_owner_action(
            operation="restore",
            actor_id=owner_actor_id,
            request_digest=request_digest,
            owner_signature_digest=owner_signature_digest,
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_installation(connection, attestation.installation_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    idempotency_key_digest,
                    operation="restore",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return self._outcome_from_document(cached, idempotent_replay=True)
                current = connection.execute(
                    f"""
                    SELECT entitlement_generation, lifecycle_status, profile_template_id
                    FROM {self._schema}.entitlement_current_generation
                    WHERE installation_id = %s
                    FOR UPDATE
                    """,
                    (attestation.installation_id,),
                ).fetchone()
                if current is None or current["lifecycle_status"] != EntitlementLifecycleStatus.REVOKED.value:
                    raise EntitlementControlPlaneError("restore requires a revoked installation")
                registration = connection.execute(
                    f"""
                    SELECT profile_template_id, owner_policy_digest, slack_destination_id,
                           superseded_at
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
                if attestation.template_digest != composed_template_digest(profile_template_id):
                    raise EntitlementControlPlaneError("template digest mismatch")
                next_generation = int(current["entitlement_generation"]) + 1
                capabilities = effective_capabilities(profile_template_id)
                restore_event_id = f"evt-{uuid.uuid4().hex}"
                restore_event = EntitlementEventV1(
                    event_type=EntitlementEventType.RESTORE,
                    installation_id=attestation.installation_id,
                    entitlement_generation=next_generation,
                    expected_generation=int(current["entitlement_generation"]),
                    actor_id=owner_actor_id,
                    idempotency_key_digest=document_digest(
                        {"restore-lineage": idempotency_key_digest}
                    ),
                    scope_digest=document_digest({"owner_actor": owner_actor_id}),
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
                    actor_id=owner_actor_id,
                    idempotency_key_digest=document_digest(
                        {"restore-grant": idempotency_key_digest}
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
                    idempotency_key_digest=idempotency_key_digest,
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
        actor_id: str,
        idempotency_key_digest: str,
        expected_generation: int,
        owner_signature_digest: str,
    ) -> str:
        request_digest = document_digest(
            {"operation": "uninstall", "installation_id": installation_id}
        )
        self._verify_owner_action(
            operation="uninstall",
            actor_id=actor_id,
            request_digest=request_digest,
            owner_signature_digest=owner_signature_digest,
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_installation(connection, installation_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    idempotency_key_digest,
                    operation="uninstall",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
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
                if int(current["entitlement_generation"]) != expected_generation:
                    raise EntitlementControlPlaneError("generation CAS failed on uninstall")
                event_id = f"evt-{uuid.uuid4().hex}"
                event = EntitlementEventV1(
                    event_type=EntitlementEventType.UNINSTALL,
                    installation_id=installation_id,
                    entitlement_generation=expected_generation,
                    expected_generation=expected_generation,
                    actor_id=actor_id,
                    idempotency_key_digest=idempotency_key_digest,
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
                        expected_generation,
                    ),
                )
                if updated.rowcount != 1:
                    raise EntitlementControlPlaneError("generation CAS failed on uninstall")
                self._store_idempotent_outcome(
                    connection,
                    operation="uninstall",
                    idempotency_key_digest=idempotency_key_digest,
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
        actor_id: str,
        idempotency_key_digest: str,
        prior_generation: int,
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
            }
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                self._lock_project(connection, registration.project_profile_id)
                cached = self._load_idempotent_outcome(
                    connection,
                    idempotency_key_digest,
                    operation="supersede_registration",
                    request_digest=request_digest,
                )
                if cached is not None:
                    connection.execute("COMMIT")
                    return str(cached["event_id"])
                if registration.registration_generation <= prior_generation:
                    raise EntitlementControlPlaneError(
                        "registration generation must strictly increase on supersede"
                    )
                slack = connection.execute(
                    f"""
                    SELECT verified FROM {self._schema}.entitlement_slack_destination
                    WHERE destination_id = %s
                    """,
                    (registration.slack_destination_id,),
                ).fetchone()
                if slack is None or not slack["verified"]:
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
                    actor_id=actor_id,
                    idempotency_key_digest=idempotency_key_digest,
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
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    outcome_document={"event_id": event.event_id},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return event_id

    def mark_notice_failed(self, notice_id: str, *, idempotency_key_digest: str) -> str:
        now = self._clock()
        request_digest = document_digest(
            {"operation": "mark_notice_failed", "notice_id": notice_id}
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
                    SELECT lifecycle_status
                    FROM {self._schema}.entitlement_current_generation
                    WHERE installation_id = %s
                      AND entitlement_generation = %s
                      AND notice_id = %s
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
        delivery_proof_digest: str,
        idempotency_key_digest: str,
    ) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", delivery_proof_digest):
            raise EntitlementControlPlaneError(
                "delivery proof digest must be lowercase sha256"
            )
        now = self._clock()
        request_digest = document_digest(
            {
                "operation": "mark_notice_delivered",
                "notice_id": notice_id,
                "delivery_proof_digest": delivery_proof_digest,
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
                    SELECT installation_id, entitlement_generation, delivery_state, document
                    FROM {self._schema}.entitlement_owner_notice
                    WHERE notice_id = %s
                    FOR UPDATE
                    """,
                    (notice_id,),
                ).fetchone()
                if row is None:
                    raise EntitlementControlPlaneError("notice not found")
                if row["delivery_state"] not in (
                    OwnerNoticeDeliveryState.PENDING.value,
                    OwnerNoticeDeliveryState.FAILED.value,
                ):
                    raise EntitlementControlPlaneError("notice is not deliverable")
                current = connection.execute(
                    f"""
                    SELECT lifecycle_status, notice_delivery_state, last_event_id
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
                document["delivery_state"] = OwnerNoticeDeliveryState.DELIVERED.value
                document["delivery_proof_digest"] = delivery_proof_digest
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
                        delivery_proof_digest,
                        now,
                        json.dumps(document),
                        notice_id,
                    ),
                )
                if current["lifecycle_status"] not in (
                    EntitlementLifecycleStatus.REVOKED.value,
                    EntitlementLifecycleStatus.UNINSTALLED.value,
                    EntitlementLifecycleStatus.SUPERSEDED.value,
                ):
                    lifecycle = (
                        EntitlementLifecycleStatus.ACTIVE.value
                        if current["lifecycle_status"]
                        == EntitlementLifecycleStatus.SUSPENDED.value
                        else current["lifecycle_status"]
                    )
                    updated = connection.execute(
                        f"""
                        UPDATE {self._schema}.entitlement_current_generation
                        SET lifecycle_status = %s,
                            notice_delivery_state = %s,
                            updated_at = now()
                        WHERE installation_id = %s
                          AND entitlement_generation = %s
                          AND notice_id = %s
                          AND lifecycle_status = %s
                        """,
                        (
                            lifecycle,
                            OwnerNoticeDeliveryState.DELIVERED.value,
                            row["installation_id"],
                            row["entitlement_generation"],
                            notice_id,
                            current["lifecycle_status"],
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
        idempotency_key_digest: str,
        actor_id: str,
        owner_signature_digest: str,
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
            }
        )
        self._verify_owner_action(
            operation="acknowledge_notice",
            actor_id=actor_id,
            request_digest=request_digest,
            owner_signature_digest=owner_signature_digest,
        )
        with self._pool.connection() as connection:
            connection.execute("BEGIN")
            try:
                cached = self._load_idempotent_outcome(
                    connection,
                    idempotency_key_digest,
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
                    idempotency_key_digest=idempotency_key_digest,
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
        with self._pool.connection() as connection:
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
                SELECT cg.*, pr.document AS registration_document, pr.superseded_at
                FROM {self._schema}.entitlement_current_generation cg
                JOIN {self._schema}.entitlement_project_registration pr
                  ON pr.project_profile_id = cg.project_profile_id
                 AND pr.registration_generation = cg.registration_generation
                WHERE cg.installation_id = %s
                  AND cg.project_profile_id = %s
                  AND cg.provider_id = %s
                  AND cg.subject_id = %s
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
                and query.now >= deadline
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
            if row["build_digest"] != query.current_build_digest:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="build digest mismatch",
                )
            capabilities = effective_capabilities(str(row["profile_template_id"]))
            if query.required_capability not in capabilities:
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="capability not entitled",
                )
            registration = row["registration_document"]
            if query.budget_task_usd > float(registration["budget_task_usd"]):
                return EntitlementActionBoundaryResult(
                    permitted=False,
                    entitlement_generation=int(row["entitlement_generation"]),
                    lifecycle_status=lifecycle,
                    notice_delivery_state=notice_state,
                    audit_event_ids=audit_ids,
                    denial_reason="task budget exceeded",
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
            return EntitlementActionBoundaryResult(
                permitted=True,
                entitlement_generation=int(row["entitlement_generation"]),
                lifecycle_status=lifecycle,
                notice_delivery_state=notice_state,
                audit_event_ids=audit_ids,
                denial_reason=None,
            )

    def authoritative_capabilities(
        self, installation_id: str, *, current_build_digest: str, now: datetime | None = None
    ) -> frozenset[str]:
        moment = now or self._clock()
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT cg.lifecycle_status, cg.build_digest, cg.profile_template_id,
                       cg.notice_delivery_state, cg.notice_pending_since,
                       pr.superseded_at
                FROM {self._schema}.entitlement_current_generation cg
                JOIN {self._schema}.entitlement_project_registration pr
                  ON pr.project_profile_id = cg.project_profile_id
                 AND pr.registration_generation = cg.registration_generation
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
            if row["build_digest"] != current_build_digest:
                return frozenset()
            return effective_capabilities(str(row["profile_template_id"]))

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
    owner_authority_secret_file: str,
    allow_insecure_local_transport: bool = False,
) -> EntitlementStoreHandle:
    schema = _require_schema(schema)
    trusted_verifier_secret = _read_secret_file(trusted_verifier_secret_file)
    owner_authority_secret = _read_secret_file(owner_authority_secret_file)
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
        owner_authority_secret=owner_authority_secret,
    )


def entitlement_store_from_handle(handle: EntitlementStoreHandle) -> EntitlementControlPlaneStore:
    return EntitlementControlPlaneStore(
        handle.pool,
        schema=handle.schema,
        storage_identity=handle.storage_identity,
        trusted_verifier_secret=handle.trusted_verifier_secret,
        owner_authority_secret=handle.owner_authority_secret,
    )
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:25 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 20:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | END]]
