# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:30 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 22:15 UTC | BEGIN]]
"""Postgres-backed entitlement control plane tests (zero-skip when DSN is set)."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from director_orchestrator.entitlement_contracts import (
    NOTICE_SUSPENSION_SECONDS,
    EntitlementActionBoundaryQuery,
    EntitlementContractError,
    EntitlementLifecycleStatus,
    InstallAttestationV1,
    OwnerNoticeDeliveryState,
    ProjectRegistrationV1,
    RepositoryScope,
    SignedEntitlementTemplateV1,
    SubjectKind,
    composed_template_digest,
    compute_signature,
    document_digest,
    signing_bytes,
)
from director_orchestrator.entitlement_control_plane import (
    EntitlementControlPlaneError,
    entitlement_store_from_handle,
    open_entitlement_control_plane,
)
from director_orchestrator.config import Settings

TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
OWNER_POLICY = "a" * 64
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
COMMIT_A = "a" * 40
TREE_A = "b" * 40
VERIFIER_PRINCIPAL = "trusted-verifier"
OWNER_PRINCIPAL = "owner-authority"
PROFILE_TEMPLATE = "antigravity-exact-sha/v1"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_secret(value: str) -> str:
    handle = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    handle.write(value)
    handle.close()
    return handle.name


def _owner_signature(document: dict) -> str:
    return compute_signature(
        principal=OWNER_PRINCIPAL,
        signing_payload=signing_bytes(document),
        secret=OWNER_SECRET,
    )


def _verifier_signature(document: dict) -> str:
    return compute_signature(
        principal=VERIFIER_PRINCIPAL,
        signing_payload=signing_bytes(document),
        secret=VERIFIER_SECRET,
    )


def _owner_action_signature(operation: str, request_digest: str) -> str:
    return _owner_signature(
        {
            "schema_version": "EntitlementOwnerAction/v1",
            "operation": operation,
            "actor_id": OWNER_PRINCIPAL,
            "request_digest": request_digest,
        }
    )


def _owner_install_action_signature(operation: str, installation_id: str) -> str:
    return _owner_action_signature(
        operation,
        document_digest({"operation": operation, "installation_id": installation_id}),
    )


def _signed_template() -> SignedEntitlementTemplateV1:
    signing_doc = {
        "schema_version": "SignedEntitlementTemplate/v1",
        "shared_template_id": "shared-integrations/v1",
        "profile_template_id": PROFILE_TEMPLATE,
        "template_digest": composed_template_digest(PROFILE_TEMPLATE),
        "owner_policy_digest": OWNER_POLICY,
        "owner_signature_principal": OWNER_PRINCIPAL,
    }
    return SignedEntitlementTemplateV1(
        profile_template_id=PROFILE_TEMPLATE,
        owner_policy_digest=OWNER_POLICY,
        owner_signature_principal=OWNER_PRINCIPAL,
        owner_signature_digest=_owner_signature(signing_doc),
    )


def _registration(
    generation: int = 1,
    *,
    slack_destination_id: str = "slack-owner-dm",
) -> ProjectRegistrationV1:
    base = {
        "schema_version": "ProjectRegistration/v1",
        "project_profile_id": "uppercut",
        "entitlement_template_id": PROFILE_TEMPLATE,
        "repository_scopes": [
            {
                "repository": "edwardjones3976/director-orchestrator",
                "refs": ["main"],
                "paths": ["src/**"],
            }
        ],
        "slack_destination_id": slack_destination_id,
        "notion_destination_id": "notion-root",
        "n8n_workflow_ids": ["workflow-intake"],
        "budget_task_usd": 1.0,
        "budget_daily_usd": 5.0,
        "state_namespace": "uppercut",
        "managed_private_source": False,
        "owner_policy_digest": OWNER_POLICY,
        "registration_generation": generation,
        "owner_signature_principal": OWNER_PRINCIPAL,
    }
    return ProjectRegistrationV1(
        project_profile_id="uppercut",
        entitlement_template_id=PROFILE_TEMPLATE,
        repository_scopes=(
            RepositoryScope(
                repository="edwardjones3976/director-orchestrator",
                refs=("main",),
                paths=("src/**",),
            ),
        ),
        slack_destination_id=slack_destination_id,
        notion_destination_id="notion-root",
        n8n_workflow_ids=("workflow-intake",),
        budget_task_usd=1.0,
        budget_daily_usd=5.0,
        state_namespace="uppercut",
        managed_private_source=False,
        owner_policy_digest=OWNER_POLICY,
        registration_generation=generation,
        owner_signature_principal=OWNER_PRINCIPAL,
        owner_signature_digest=_owner_signature(base),
    )


def _boundary_query(
    *,
    installation_id: str,
    path: str = "src/director_orchestrator/config.py",
    budget_task_usd: float = 0.5,
    now: datetime | None = None,
    **overrides: object,
) -> EntitlementActionBoundaryQuery:
    payload = {
        "project_profile_id": "uppercut",
        "repository": "edwardjones3976/director-orchestrator",
        "ref": "main",
        "path": path,
        "provider_id": "antigravity",
        "subject_id": "antigravity",
        "installation_id": installation_id,
        "required_capability": "review.exact_sha",
        "current_build_digest": DIGEST_A,
        "action": "review",
        "budget_task_usd": budget_task_usd,
        "now": now or _utc_now(),
    }
    payload.update(overrides)
    return EntitlementActionBoundaryQuery(**payload)


def _attestation(
    *,
    installation_id: str,
    artifact_digest: str = DIGEST_A,
    nonce_digest: str | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> InstallAttestationV1:
    issued = issued_at or _utc_now()
    expires = expires_at or (issued + timedelta(hours=2))
    signing_doc = {
        "schema_version": "InstallAttestation/v1",
        "subject_kind": "provider",
        "subject_id": "antigravity",
        "provider_id": "antigravity",
        "project_profile_id": "uppercut",
        "installation_id": installation_id,
        "source_commit_sha": COMMIT_A,
        "source_tree_sha": TREE_A,
        "manifest_digest": DIGEST_A,
        "artifact_digest": artifact_digest,
        "registration_generation": 1,
        "template_digest": composed_template_digest(PROFILE_TEMPLATE),
        "owner_policy_digest": OWNER_POLICY,
        "verifier_principal": VERIFIER_PRINCIPAL,
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
        "nonce_digest": nonce_digest or document_digest({"nonce": uuid.uuid4().hex}),
        "attestation_id": f"attest-{uuid.uuid4().hex}",
    }
    return InstallAttestationV1(
        subject_kind=SubjectKind.PROVIDER,
        subject_id="antigravity",
        provider_id="antigravity",
        project_profile_id="uppercut",
        installation_id=installation_id,
        source_commit_sha=COMMIT_A,
        source_tree_sha=TREE_A,
        manifest_digest=DIGEST_A,
        artifact_digest=artifact_digest,
        registration_generation=1,
        template_digest=composed_template_digest(PROFILE_TEMPLATE),
        owner_policy_digest=OWNER_POLICY,
        verifier_principal=VERIFIER_PRINCIPAL,
        verifier_signature_digest=_verifier_signature(signing_doc),
        issued_at=issued,
        expires_at=expires,
        nonce_digest=signing_doc["nonce_digest"],
        attestation_id=signing_doc["attestation_id"],
    )


class EntitlementContractUnitTests(unittest.TestCase):
    def test_self_referential_attestation_signature_is_rejected(self) -> None:
        issued = _utc_now()
        expires = issued + timedelta(hours=1)
        signing_doc = {
            "schema_version": "InstallAttestation/v1",
            "subject_kind": "provider",
            "subject_id": "antigravity",
            "provider_id": "antigravity",
            "project_profile_id": "uppercut",
            "installation_id": "install-x",
            "source_commit_sha": COMMIT_A,
            "source_tree_sha": TREE_A,
            "manifest_digest": DIGEST_A,
            "artifact_digest": DIGEST_A,
            "registration_generation": 1,
            "template_digest": composed_template_digest(PROFILE_TEMPLATE),
            "owner_policy_digest": OWNER_POLICY,
            "verifier_principal": VERIFIER_PRINCIPAL,
            "issued_at": issued.isoformat(),
            "expires_at": expires.isoformat(),
            "nonce_digest": DIGEST_B,
            "attestation_id": "attest-self",
            "verifier_signature_digest": DIGEST_A,
        }
        bad = InstallAttestationV1(
            subject_kind=SubjectKind.PROVIDER,
            subject_id="antigravity",
            provider_id="antigravity",
            project_profile_id="uppercut",
            installation_id="install-x",
            source_commit_sha=COMMIT_A,
            source_tree_sha=TREE_A,
            manifest_digest=DIGEST_A,
            artifact_digest=DIGEST_A,
            registration_generation=1,
            template_digest=composed_template_digest(PROFILE_TEMPLATE),
            owner_policy_digest=OWNER_POLICY,
            verifier_principal=VERIFIER_PRINCIPAL,
            verifier_signature_digest=document_digest(signing_doc),
            issued_at=issued,
            expires_at=expires,
            nonce_digest=DIGEST_B,
            attestation_id="attest-self",
        )
        with self.assertRaises(EntitlementContractError):
            bad.verify_signature(trusted_verifier_secret=b"unit-test-verifier-secret")

    def test_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(EntitlementContractError):
            RepositoryScope(
                repository="edwardjones3976/director-orchestrator",
                refs=("main",),
                paths=("../secrets/**",),
            )


class EntitlementControlPlanePostgresTests(unittest.TestCase):
    handle = None
    store = None

    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for this zero-skip suite"
            )
        global VERIFIER_SECRET_FILE, OWNER_SECRET_FILE
        VERIFIER_SECRET_FILE = _write_secret(VERIFIER_SECRET.decode("utf-8"))
        OWNER_SECRET_FILE = _write_secret(OWNER_SECRET.decode("utf-8"))
    def setUp(self) -> None:
        self.schema = f"entitlement_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_entitlement_control_plane(
            TEST_DSN,
            schema=self.schema,
            trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
            owner_authority_secret_file=OWNER_SECRET_FILE,
            allow_insecure_local_transport=True,
        )
        self.store = entitlement_store_from_handle(self.handle)
        self.store.register_signed_template(_signed_template())
        self.store.register_verified_slack_destination(
            destination_id="slack-owner-dm",
            owner_signature_principal=OWNER_PRINCIPAL,
            owner_signature_digest=_owner_signature(
                {
                    "schema_version": "OwnerSlackDestination/v1",
                    "destination_id": "slack-owner-dm",
                    "owner_signature_principal": OWNER_PRINCIPAL,
                }
            ),
        )
        self.store.register_project(_registration())

    def tearDown(self) -> None:
        if self.handle is None:
            return
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    @classmethod
    def tearDownClass(cls) -> None:
        for path in (VERIFIER_SECRET_FILE, OWNER_SECRET_FILE):
            Path(path).unlink(missing_ok=True)

    def test_grant_requires_owner_template_and_slack_destination(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        attestation = _attestation(installation_id=installation_id)
        outcome = self.store.grant_from_attestation(
            attestation,
            idempotency_key_digest=document_digest({"grant": attestation.attestation_id}),
        )
        self.assertIn("review.exact_sha", outcome.granted_capabilities)
        self.assertIn("integration.slack", outcome.granted_capabilities)
        self.assertEqual(outcome.notice.delivery_state, OwnerNoticeDeliveryState.PENDING)

    def test_stale_and_future_attestations_are_refused(self) -> None:
        now = _utc_now()
        stale = _attestation(
            installation_id=f"install-{uuid.uuid4().hex[:8]}",
            issued_at=now - timedelta(hours=3),
            expires_at=now - timedelta(hours=1),
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "stale"):
            self.store.grant_from_attestation(
                stale,
                idempotency_key_digest=document_digest({"stale": 1}),
            )
        future = _attestation(
            installation_id=f"install-{uuid.uuid4().hex[:8]}",
            issued_at=now + timedelta(hours=1),
            expires_at=now + timedelta(hours=2),
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "not yet valid"):
            self.store.grant_from_attestation(
                future,
                idempotency_key_digest=document_digest({"future": 1}),
            )

    def test_preemptive_revocation_blocks_future_grant(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        self.store.revoke(
            installation_id,
            actor_id=OWNER_PRINCIPAL,
            idempotency_key_digest=document_digest({"preemptive": installation_id}),
            owner_signature_digest=_owner_install_action_signature(
                "revoke", installation_id
            ),
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "preemptively revoked"):
            self.store.grant_from_attestation(
                _attestation(installation_id=installation_id),
                idempotency_key_digest=document_digest({"grant": 1}),
            )

    def test_owner_action_signature_is_required_for_revocation(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        with self.assertRaisesRegex(
            EntitlementControlPlaneError, "owner action signature invalid"
        ):
            self.store.revoke(
                installation_id,
                actor_id=OWNER_PRINCIPAL,
                idempotency_key_digest=document_digest({"revoke": installation_id}),
                owner_signature_digest="0" * 64,
            )
        with self.handle.pool.connection() as connection:
            event_count = connection.execute(
                f"""
                SELECT count(*) AS count
                FROM {self.schema}.entitlement_event
                WHERE installation_id = %s
                """,
                (installation_id,),
            ).fetchone()["count"]
        self.assertEqual(event_count, 0)

    def test_idempotent_grant_replay_returns_stored_outcome(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        attestation = _attestation(installation_id=installation_id)
        key = document_digest({"grant": attestation.attestation_id})
        first = self.store.grant_from_attestation(attestation, idempotency_key_digest=key)
        second = self.store.grant_from_attestation(attestation, idempotency_key_digest=key)
        self.assertFalse(first.idempotent_replay)
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.notice.notice_id, second.notice.notice_id)

    def test_terminal_notice_failure_suspends_without_scheduler(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_failed(
            outcome.notice.notice_id,
            idempotency_key_digest=document_digest({"fail": outcome.notice.notice_id}),
        )
        self.assertEqual(
            self.store.authoritative_capabilities(
                installation_id, current_build_digest=DIGEST_A
            ),
            frozenset(),
        )

    def test_action_boundary_fails_closed_on_deadline_without_scheduler(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        issued = _utc_now()
        outcome = self.store.grant_from_attestation(
            _attestation(
                installation_id=installation_id,
                issued_at=issued,
                expires_at=issued + timedelta(hours=2),
            ),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        denied = self.store.query_action_boundary(
            EntitlementActionBoundaryQuery(
                project_profile_id="uppercut",
                repository="edwardjones3976/director-orchestrator",
                ref="main",
                path="src/director_orchestrator/config.py",
                provider_id="antigravity",
                subject_id="antigravity",
                installation_id=installation_id,
                required_capability="review.exact_sha",
                current_build_digest=DIGEST_A,
                action="review",
                budget_task_usd=0.5,
                now=issued + timedelta(seconds=NOTICE_SUSPENSION_SECONDS + 1),
            )
        )
        self.assertFalse(denied.permitted)
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            delivery_proof_digest=document_digest({"delivered": 1}),
            idempotency_key_digest=document_digest({"deliver": outcome.notice.notice_id}),
        )
        allowed = self.store.query_action_boundary(
            EntitlementActionBoundaryQuery(
                project_profile_id="uppercut",
                repository="edwardjones3976/director-orchestrator",
                ref="main",
                path="src/director_orchestrator/config.py",
                provider_id="antigravity",
                subject_id="antigravity",
                installation_id=installation_id,
                required_capability="review.exact_sha",
                current_build_digest=DIGEST_A,
                action="review",
                budget_task_usd=0.5,
                now=issued + timedelta(seconds=NOTICE_SUSPENSION_SECONDS + 1),
            )
        )
        self.assertTrue(allowed.permitted)

    def test_schema_reopen_is_migration_safe(self) -> None:
        second = open_entitlement_control_plane(
            TEST_DSN,
            schema=self.schema,
            trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
            owner_authority_secret_file=OWNER_SECRET_FILE,
            allow_insecure_local_transport=True,
        )
        second.close()

    def test_foreign_schema_manifest_is_refused(self) -> None:
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"""
                UPDATE {self.schema}.entitlement_schema_manifest
                SET storage_identity = %s
                """,
                ("corrupt-identity",),
            )
        try:
            with self.assertRaises(EntitlementControlPlaneError):
                open_entitlement_control_plane(
                    TEST_DSN,
                    schema=self.schema,
                    trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
                    owner_authority_secret_file=OWNER_SECRET_FILE,
                    allow_insecure_local_transport=True,
                )
        finally:
            with self.handle.pool.connection() as connection:
                connection.execute(
                    f"""
                    UPDATE {self.schema}.entitlement_schema_manifest
                    SET storage_identity = %s
                    """,
                    (self.handle.storage_identity,),
                )

    def test_insecure_remote_dsn_is_refused(self) -> None:
        with self.assertRaisesRegex(
            EntitlementControlPlaneError, "only to loopback"
        ):
            open_entitlement_control_plane(
                "postgresql://director_ci@203.0.113.10:5432/director_test?sslmode=disable",
                schema=f"entitlement_remote_{uuid.uuid4().hex[:8]}_v1",
                trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
                owner_authority_secret_file=OWNER_SECRET_FILE,
                allow_insecure_local_transport=True,
            )

    def test_restore_is_one_transaction_with_new_notice(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        first = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.revoke(
            installation_id,
            actor_id=OWNER_PRINCIPAL,
            idempotency_key_digest=document_digest({"revoke": installation_id}),
            owner_signature_digest=_owner_install_action_signature(
                "revoke", installation_id
            ),
        )
        restore_attestation = _attestation(
            installation_id=installation_id,
            artifact_digest=DIGEST_B,
            nonce_digest=document_digest({"restore-nonce": 1}),
        )
        restored = self.store.restore(
            restore_attestation,
            owner_actor_id=OWNER_PRINCIPAL,
            idempotency_key_digest=document_digest({"restore": installation_id}),
            owner_signature_digest=_owner_action_signature(
                "restore",
                document_digest(
                    {
                        "operation": "restore",
                        "attestation_digest": restore_attestation.digest,
                    }
                ),
            ),
        )
        self.assertGreater(restored.entitlement_generation, first.entitlement_generation)
        self.assertEqual(restored.notice.delivery_state, OwnerNoticeDeliveryState.PENDING)

    def test_concurrent_idempotent_grant_returns_same_outcome(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        attestation = _attestation(installation_id=installation_id)
        key = document_digest({"grant": attestation.attestation_id})
        results: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            outcome = self.store.grant_from_attestation(
                attestation, idempotency_key_digest=key
            )
            with lock:
                results.append(outcome.event_id)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(results)), 1)

    def test_tampered_attestation_signature_is_refused_at_grant(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        attestation = _attestation(installation_id=installation_id)
        tampered = InstallAttestationV1(
            subject_kind=attestation.subject_kind,
            subject_id=attestation.subject_id,
            provider_id=attestation.provider_id,
            project_profile_id=attestation.project_profile_id,
            installation_id=attestation.installation_id,
            source_commit_sha=attestation.source_commit_sha,
            source_tree_sha=attestation.source_tree_sha,
            manifest_digest=attestation.manifest_digest,
            artifact_digest=attestation.artifact_digest,
            registration_generation=attestation.registration_generation,
            template_digest=attestation.template_digest,
            owner_policy_digest=attestation.owner_policy_digest,
            verifier_principal=attestation.verifier_principal,
            verifier_signature_digest="c" * 64,
            issued_at=attestation.issued_at,
            expires_at=attestation.expires_at,
            nonce_digest=attestation.nonce_digest,
            attestation_id=attestation.attestation_id,
        )
        with self.assertRaisesRegex(
            EntitlementControlPlaneError, "install attestation signature mismatch"
        ):
            self.store.grant_from_attestation(
                tampered,
                idempotency_key_digest=document_digest({"tampered": 1}),
            )

    def test_subject_declared_extra_rights_are_rejected(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        with self.assertRaisesRegex(EntitlementControlPlaneError, "subject-declared"):
            self.store.grant_from_attestation(
                _attestation(installation_id=installation_id),
                idempotency_key_digest=document_digest({"escalation": 1}),
                subject_declared_extra_rights=frozenset({"deploy"}),
            )

    def test_unregistered_template_blocks_project_registration(self) -> None:
        schema = f"entitlement_iso_{uuid.uuid4().hex[:8]}_v1"
        handle = open_entitlement_control_plane(
            TEST_DSN,
            schema=schema,
            trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
            owner_authority_secret_file=OWNER_SECRET_FILE,
            allow_insecure_local_transport=True,
        )
        store = entitlement_store_from_handle(handle)
        try:
            store.register_verified_slack_destination(
                destination_id="slack-owner-dm",
                owner_signature_principal=OWNER_PRINCIPAL,
                owner_signature_digest=_owner_signature(
                    {
                        "schema_version": "OwnerSlackDestination/v1",
                        "destination_id": "slack-owner-dm",
                        "owner_signature_principal": OWNER_PRINCIPAL,
                    }
                ),
            )
            with self.assertRaisesRegex(EntitlementControlPlaneError, "signed template"):
                store.register_project(_registration())
        finally:
            with handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            handle.close()

    def test_unverified_slack_destination_blocks_project_registration(self) -> None:
        schema = f"entitlement_iso_{uuid.uuid4().hex[:8]}_v1"
        handle = open_entitlement_control_plane(
            TEST_DSN,
            schema=schema,
            trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
            owner_authority_secret_file=OWNER_SECRET_FILE,
            allow_insecure_local_transport=True,
        )
        store = entitlement_store_from_handle(handle)
        try:
            store.register_signed_template(_signed_template())
            bad_registration = _registration(slack_destination_id="missing-slack")
            with self.assertRaisesRegex(EntitlementControlPlaneError, "slack destination"):
                store.register_project(bad_registration)
        finally:
            with handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            handle.close()

    def test_supersession_invalidates_old_registration_authority(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            self._latest_notice_id(installation_id),
            delivery_proof_digest=document_digest({"delivered": installation_id}),
            idempotency_key_digest=document_digest({"deliver": installation_id}),
        )
        reg2 = _registration(generation=2)
        self.store.supersede_registration(
            reg2,
            actor_id=OWNER_PRINCIPAL,
            idempotency_key_digest=document_digest({"supersede": 2}),
            prior_generation=1,
        )
        denied = self.store.query_action_boundary(
            _boundary_query(installation_id=installation_id)
        )
        self.assertFalse(denied.permitted)
        self.assertEqual(denied.denial_reason, "registration superseded")

    def test_uninstall_removes_authority(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            delivery_proof_digest=document_digest({"delivered": 1}),
            idempotency_key_digest=document_digest({"deliver": 1}),
        )
        self.store.uninstall(
            installation_id,
            actor_id=OWNER_PRINCIPAL,
            idempotency_key_digest=document_digest({"uninstall": installation_id}),
            expected_generation=outcome.entitlement_generation,
            owner_signature_digest=_owner_install_action_signature(
                "uninstall", installation_id
            ),
        )
        self.assertEqual(
            self.store.authoritative_capabilities(
                installation_id, current_build_digest=DIGEST_A
            ),
            frozenset(),
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "uninstalled"):
            self.store.grant_from_attestation(
                _attestation(
                    installation_id=installation_id,
                    nonce_digest=document_digest({"retry": 1}),
                ),
                idempotency_key_digest=document_digest({"grant-retry": 1}),
            )

    def test_installation_isolation(self) -> None:
        install_a = f"install-{uuid.uuid4().hex[:8]}"
        install_b = f"install-{uuid.uuid4().hex[:8]}"
        self.store.grant_from_attestation(
            _attestation(installation_id=install_a),
            idempotency_key_digest=document_digest({"grant": install_a}),
        )
        self.store.grant_from_attestation(
            _attestation(installation_id=install_b),
            idempotency_key_digest=document_digest({"grant": install_b}),
        )
        self.store.revoke(
            install_a,
            actor_id=OWNER_PRINCIPAL,
            idempotency_key_digest=document_digest({"revoke": install_a}),
            owner_signature_digest=_owner_install_action_signature("revoke", install_a),
        )
        self.assertEqual(
            self.store.authoritative_capabilities(install_a, current_build_digest=DIGEST_A),
            frozenset(),
        )
        self.assertIn(
            "review.exact_sha",
            self.store.authoritative_capabilities(install_b, current_build_digest=DIGEST_A),
        )

    def test_budget_boundary_denies_over_limit(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        issued = _utc_now()
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id, issued_at=issued),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            delivery_proof_digest=document_digest({"delivered": 1}),
            idempotency_key_digest=document_digest({"deliver": 1}),
        )
        denied = self.store.query_action_boundary(
            EntitlementActionBoundaryQuery(
                project_profile_id="uppercut",
                repository="edwardjones3976/director-orchestrator",
                ref="main",
                path="src/director_orchestrator/config.py",
                provider_id="antigravity",
                subject_id="antigravity",
                installation_id=installation_id,
                required_capability="review.exact_sha",
                current_build_digest=DIGEST_A,
                action="review",
                budget_task_usd=2.0,
                now=issued,
            )
        )
        self.assertFalse(denied.permitted)
        self.assertEqual(denied.denial_reason, "task budget exceeded")

    def test_path_scope_and_traversal_denials(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        issued = _utc_now()
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id, issued_at=issued),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            delivery_proof_digest=document_digest({"delivered": 1}),
            idempotency_key_digest=document_digest({"deliver": 1}),
        )
        allowed = self.store.query_action_boundary(
            EntitlementActionBoundaryQuery(
                project_profile_id="uppercut",
                repository="edwardjones3976/director-orchestrator",
                ref="main",
                path="src/director_orchestrator/config.py",
                provider_id="antigravity",
                subject_id="antigravity",
                installation_id=installation_id,
                required_capability="review.exact_sha",
                current_build_digest=DIGEST_A,
                action="review",
                budget_task_usd=0.5,
                now=issued,
            )
        )
        self.assertTrue(allowed.permitted)
        outside = self.store.query_action_boundary(
            EntitlementActionBoundaryQuery(
                project_profile_id="uppercut",
                repository="edwardjones3976/director-orchestrator",
                ref="main",
                path="docs/readme.md",
                provider_id="antigravity",
                subject_id="antigravity",
                installation_id=installation_id,
                required_capability="review.exact_sha",
                current_build_digest=DIGEST_A,
                action="review",
                budget_task_usd=0.5,
                now=issued,
            )
        )
        self.assertFalse(outside.permitted)
        sibling_prefix = self.store.query_action_boundary(
            _boundary_query(
                installation_id=installation_id,
                path="src_evil/steal.py",
                now=issued,
            )
        )
        self.assertFalse(sibling_prefix.permitted)
        with self.assertRaisesRegex(EntitlementContractError, "traversal"):
            EntitlementActionBoundaryQuery(
                project_profile_id="uppercut",
                repository="edwardjones3976/director-orchestrator",
                ref="main",
                path="src/../secrets/key",
                provider_id="antigravity",
                subject_id="antigravity",
                installation_id=installation_id,
                required_capability="review.exact_sha",
                current_build_digest=DIGEST_A,
                action="review",
                budget_task_usd=0.5,
                now=issued,
            )

    def test_notice_callback_idempotency(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        fail_key = document_digest({"fail": outcome.notice.notice_id})
        first = self.store.mark_notice_failed(
            outcome.notice.notice_id, idempotency_key_digest=fail_key
        )
        second = self.store.mark_notice_failed(
            outcome.notice.notice_id, idempotency_key_digest=fail_key
        )
        self.assertEqual(first, second)

    def test_evaluate_notice_suspensions_is_race_safe(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        issued = _utc_now()
        self.store.grant_from_attestation(
            _attestation(installation_id=installation_id, issued_at=issued),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        deadline = issued + timedelta(seconds=NOTICE_SUSPENSION_SECONDS + 5)
        suspended = self.store.evaluate_notice_suspensions(now=deadline)
        self.assertIn(installation_id, suspended)
        again = self.store.evaluate_notice_suspensions(now=deadline)
        self.assertNotIn(installation_id, again)
        with self.handle.pool.connection() as connection:
            count = connection.execute(
                f"""
                SELECT count(*) AS c
                FROM {self.schema}.entitlement_event
                WHERE installation_id = %s AND event_type = %s
                """,
                (installation_id, "SUSPEND"),
            ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_concurrent_revoke_idempotency(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        key = document_digest({"revoke": installation_id})
        results: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            event_id = self.store.revoke(
                installation_id,
                actor_id=OWNER_PRINCIPAL,
                idempotency_key_digest=key,
                owner_signature_digest=_owner_install_action_signature(
                    "revoke", installation_id
                ),
            )
            with lock:
                results.append(event_id)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(results)), 1)

    def test_idempotency_key_cannot_be_rebound_to_another_operation(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        attestation = _attestation(installation_id=installation_id)
        key = document_digest({"shared-key": installation_id})
        self.store.grant_from_attestation(attestation, idempotency_key_digest=key)
        with self.assertRaisesRegex(
            EntitlementControlPlaneError, "different operation or request"
        ):
            self.store.revoke(
                installation_id,
                actor_id=OWNER_PRINCIPAL,
                idempotency_key_digest=key,
                owner_signature_digest=_owner_install_action_signature(
                    "revoke", installation_id
                ),
            )

    def test_grant_rolls_back_when_pending_notice_cannot_be_written(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        attestation = _attestation(installation_id=installation_id)
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"""
                CREATE FUNCTION {self.schema}.reject_notice_insert()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'injected notice failure'; END; $$
                """
            )
            connection.execute(
                f"""
                CREATE TRIGGER reject_notice_insert
                BEFORE INSERT ON {self.schema}.entitlement_owner_notice
                FOR EACH ROW EXECUTE FUNCTION {self.schema}.reject_notice_insert()
                """
            )
        with self.assertRaisesRegex(
            EntitlementControlPlaneError, "grant transaction failed"
        ):
            self.store.grant_from_attestation(
                attestation,
                idempotency_key_digest=document_digest({"grant": installation_id}),
            )
        with self.handle.pool.connection() as connection:
            counts = connection.execute(
                f"""
                SELECT
                  (SELECT count(*) FROM {self.schema}.entitlement_event
                   WHERE installation_id = %s) AS events,
                  (SELECT count(*) FROM {self.schema}.entitlement_install_attestation
                   WHERE installation_id = %s) AS attestations,
                  (SELECT count(*) FROM {self.schema}.entitlement_current_generation
                   WHERE installation_id = %s) AS current_rows
                """,
                (installation_id, installation_id, installation_id),
            ).fetchone()
        self.assertEqual((counts["events"], counts["attestations"], counts["current_rows"]), (0, 0, 0))

    def test_delivery_after_revocation_never_reactivates_authority(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.revoke(
            installation_id,
            actor_id=OWNER_PRINCIPAL,
            idempotency_key_digest=document_digest({"revoke": installation_id}),
            owner_signature_digest=_owner_install_action_signature(
                "revoke", installation_id
            ),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            delivery_proof_digest=document_digest({"delivered": installation_id}),
            idempotency_key_digest=document_digest({"delivery": installation_id}),
        )
        self.assertEqual(
            self.store.authoritative_capabilities(
                installation_id, current_build_digest=DIGEST_A
            ),
            frozenset(),
        )
        with self.handle.pool.connection() as connection:
            lifecycle = connection.execute(
                f"""
                SELECT lifecycle_status
                FROM {self.schema}.entitlement_current_generation
                WHERE installation_id = %s
                """,
                (installation_id,),
            ).fetchone()["lifecycle_status"]
        self.assertEqual(lifecycle, EntitlementLifecycleStatus.REVOKED.value)

    def test_distinct_concurrent_grants_cannot_share_a_generation(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        attestations = [
            _attestation(
                installation_id=installation_id,
                nonce_digest=document_digest({"nonce": index, "install": installation_id}),
            )
            for index in range(2)
        ]
        successes: list[int] = []
        failures: list[str] = []
        lock = threading.Lock()

        def worker(index: int) -> None:
            try:
                outcome = self.store.grant_from_attestation(
                    attestations[index],
                    idempotency_key_digest=document_digest(
                        {"grant": installation_id, "attempt": index}
                    ),
                )
                with lock:
                    successes.append(outcome.entitlement_generation)
            except EntitlementControlPlaneError as exc:
                with lock:
                    failures.append(str(exc))

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(successes, [1])
        self.assertEqual(len(failures), 1)
        self.assertIn("unchanged build", failures[0])

    def test_schema_manifest_does_not_hide_missing_control_plane_objects(self) -> None:
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"DROP TABLE {self.schema}.entitlement_idempotency_outcome"
            )
        with self.assertRaisesRegex(
            EntitlementControlPlaneError, "object inventory"
        ):
            open_entitlement_control_plane(
                TEST_DSN,
                schema=self.schema,
                trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
                owner_authority_secret_file=OWNER_SECRET_FILE,
                allow_insecure_local_transport=True,
            )

    def test_config_uses_secret_file_references_not_raw_slots(self) -> None:
        settings = Settings()
        self.assertTrue(hasattr(settings, "entitlement_postgres_dsn_file"))
        self.assertTrue(hasattr(settings, "entitlement_trusted_verifier_secret_file"))
        self.assertTrue(hasattr(settings, "entitlement_owner_authority_secret_file"))
        self.assertFalse(hasattr(settings, "entitlement_postgres_dsn"))
        self.assertFalse(hasattr(settings, "entitlement_trusted_verifier_secret"))

    def _latest_notice_id(self, installation_id: str) -> str:
        with self.handle.pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT notice_id
                FROM {self.schema}.entitlement_current_generation
                WHERE installation_id = %s
                """,
                (installation_id,),
            ).fetchone()
        assert row is not None
        return str(row["notice_id"])


class EntitlementConfigRegressionTests(unittest.TestCase):
    def test_no_raw_entitlement_secret_env_in_settings(self) -> None:
        field_names = {name for name in Settings.__dataclass_fields__}
        self.assertIn("entitlement_postgres_dsn_file", field_names)
        self.assertNotIn("entitlement_postgres_dsn", field_names)
        self.assertNotIn("entitlement_trusted_verifier_secret", field_names)


VERIFIER_SECRET = b"entitlement-test-verifier-secret"
OWNER_SECRET = b"entitlement-test-owner-secret"
VERIFIER_SECRET_FILE = ""
OWNER_SECRET_FILE = ""


if __name__ == "__main__":
    unittest.main()
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 22:15 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:30 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | END]]
