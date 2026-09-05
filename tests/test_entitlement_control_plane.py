# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:30 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 22:15 UTC | BEGIN]]
"""Postgres-backed entitlement control plane tests (zero-skip when DSN is set)."""
from __future__ import annotations

import os
import json
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path

from director_orchestrator.entitlement_contracts import (
    NOTICE_SUSPENSION_SECONDS,
    EntitlementActionBoundaryQuery,
    EntitlementActionType,
    EntitlementContractError,
    EntitlementLifecycleStatus,
    EntitlementOwnerActionV1,
    InstallAttestationV1,
    ManifestEntryV1,
    NoticeRouteProofV1,
    OwnerNoticeV1,
    OwnerNoticeDeliveryState,
    ProjectRegistrationV1,
    RepositoryScope,
    RuntimeEvidenceProofV1,
    SignedEntitlementTemplateV1,
    SlackDestinationHealthProofV1,
    SubjectKind,
    composed_template_digest,
    compute_signature,
    document_digest,
    manifest_digest,
    parse_install_attestation_v1,
    parse_project_registration_v1,
    parse_signed_entitlement_template_v1,
    parse_entitlement_action_boundary_query_v1,
    parse_owner_notice_v1,
    signing_bytes,
)
from director_orchestrator.entitlement_control_plane import (
    EntitlementControlPlaneStore,
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


def _route_signature(document: dict) -> str:
    return compute_signature(
        principal=VERIFIER_PRINCIPAL,
        signing_payload=signing_bytes(document),
        secret=ROUTE_SECRET,
    )


def _owner_action(
    operation: str,
    installation_id: str,
    expected_generation: int | None,
    idempotency_key_digest: str,
    request_digest: str,
    *,
    issued_at: datetime | None = None,
    nonce_digest: str | None = None,
) -> EntitlementOwnerActionV1:
    issued = issued_at or _utc_now()
    signing_doc = {
        "schema_version": "EntitlementOwnerAction/v1",
        "operation": operation,
        "actor_id": OWNER_PRINCIPAL,
        "installation_id": installation_id,
        "expected_generation": expected_generation,
        "idempotency_key_digest": idempotency_key_digest,
        "request_digest": request_digest,
        "nonce_digest": nonce_digest or document_digest({"owner-action": uuid.uuid4().hex}),
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(minutes=5)).isoformat(),
    }
    return EntitlementOwnerActionV1(
        operation=operation,
        actor_id=OWNER_PRINCIPAL,
        installation_id=installation_id,
        expected_generation=expected_generation,
        idempotency_key_digest=idempotency_key_digest,
        request_digest=request_digest,
        nonce_digest=signing_doc["nonce_digest"],
        issued_at=issued,
        expires_at=issued + timedelta(minutes=5),
        owner_signature_digest=_owner_signature(signing_doc),
    )


def _install_action(operation: str, installation_id: str, generation: int | None) -> EntitlementOwnerActionV1:
    key = document_digest({"key": operation, "installation": installation_id, "nonce": uuid.uuid4().hex})
    request = document_digest({"operation": operation, "installation_id": installation_id, "expected_generation": generation})
    return _owner_action(operation, installation_id, generation, key, request)


def _restore_action(
    attestation: InstallAttestationV1,
    expected_generation: int,
    *,
    key: str | None = None,
) -> EntitlementOwnerActionV1:
    idempotency = key or document_digest({"restore-key": uuid.uuid4().hex})
    request = document_digest(
        {
            "operation": "restore",
            "attestation": attestation.to_document(),
            "expected_generation": expected_generation,
        }
    )
    return _owner_action(
        "restore",
        attestation.installation_id,
        expected_generation,
        idempotency,
        request,
    )


def _slack_health_proof(destination_id: str = "slack-owner-dm") -> SlackDestinationHealthProofV1:
    issued = _utc_now()
    signing_doc = {
        "schema_version": "SlackDestinationHealthProof/v1",
        "destination_id": destination_id,
        "workspace_id": "workspace-primary",
        "channel_id": "owner-dm",
        "account_id": "director-bot",
        "authenticated": True,
        "delivery_probe_digest": DIGEST_B,
        "verifier_principal": VERIFIER_PRINCIPAL,
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(hours=1)).isoformat(),
        "nonce_digest": document_digest({"slack-proof": uuid.uuid4().hex}),
    }
    return SlackDestinationHealthProofV1(
        destination_id=destination_id,
        workspace_id="workspace-primary",
        channel_id="owner-dm",
        account_id="director-bot",
        authenticated=True,
        delivery_probe_digest=DIGEST_B,
        verifier_principal=VERIFIER_PRINCIPAL,
        issued_at=issued,
        expires_at=issued + timedelta(hours=1),
        nonce_digest=signing_doc["nonce_digest"],
        verifier_signature_digest=_route_signature(signing_doc),
    )


def _route_proof(notice, outcome: str) -> NoticeRouteProofV1:
    issued = _utc_now()
    signing_doc = {
        "schema_version": "NoticeRouteProof/v1",
        "notice_id": notice.notice_id,
        "installation_id": notice.installation_id,
        "entitlement_generation": notice.entitlement_generation,
        "destination_id": "slack-owner-dm",
        "outcome": outcome,
        "evidence_digest": DIGEST_B,
        "verifier_principal": VERIFIER_PRINCIPAL,
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(minutes=5)).isoformat(),
        "nonce_digest": document_digest({"route-proof": uuid.uuid4().hex}),
    }
    return NoticeRouteProofV1(
        notice_id=notice.notice_id,
        installation_id=notice.installation_id,
        entitlement_generation=notice.entitlement_generation,
        destination_id="slack-owner-dm",
        outcome=outcome,
        evidence_digest=DIGEST_B,
        verifier_principal=VERIFIER_PRINCIPAL,
        issued_at=issued,
        expires_at=issued + timedelta(minutes=5),
        nonce_digest=signing_doc["nonce_digest"],
        verifier_signature_digest=_route_signature(signing_doc),
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
    notion_destination_id: str = "notion-root",
) -> ProjectRegistrationV1:
    base = {
        "schema_version": "ProjectRegistration/v1",
        "project_profile_id": "uppercut",
        "entitlement_template_id": PROFILE_TEMPLATE,
        "repository_scopes": [
            {
                "repository": "edwardjones3976/director-orchestrator",
                "refs": [COMMIT_A],
                "paths": ["src/**"],
            }
        ],
        "slack_destination_id": slack_destination_id,
        "notion_destination_id": notion_destination_id,
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
                refs=(COMMIT_A,),
                paths=("src/**",),
            ),
        ),
        slack_destination_id=slack_destination_id,
        notion_destination_id=notion_destination_id,
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
    now: datetime | None = None,
    **overrides: object,
) -> EntitlementActionBoundaryQuery:
    payload = {
        "project_profile_id": "uppercut",
        "repository": "edwardjones3976/director-orchestrator",
        "ref": COMMIT_A,
        "path": path,
        "provider_id": "antigravity",
        "subject_id": "antigravity",
        "installation_id": installation_id,
        "current_build_digest": DIGEST_A,
        "action_type": EntitlementActionType.REVIEW_EXACT_SHA,
        "destination_id": None,
        "effect_digest": document_digest({"review": installation_id}),
        "reserved_spend_usd": 0.0,
        "now": now or _utc_now(),
    }
    payload.update(overrides)
    if "runtime_proof" not in payload:
        issued = payload["now"]
        signing_doc = {
            "schema_version": "RuntimeEvidenceProof/v1",
            "installation_id": payload["installation_id"],
            "build_digest": payload["current_build_digest"],
            "source_commit_sha": COMMIT_A,
            "source_tree_sha": TREE_A,
            "action_type": payload["action_type"].value,
            "destination_id": payload["destination_id"],
            "effect_digest": payload["effect_digest"],
            "verifier_principal": VERIFIER_PRINCIPAL,
            "issued_at": issued.isoformat(),
            "expires_at": (issued + timedelta(hours=1)).isoformat(),
            "nonce_digest": document_digest({"runtime": uuid.uuid4().hex}),
        }
        payload["runtime_proof"] = RuntimeEvidenceProofV1(
            installation_id=payload["installation_id"],
            build_digest=payload["current_build_digest"],
            source_commit_sha=COMMIT_A,
            source_tree_sha=TREE_A,
            action_type=payload["action_type"],
            destination_id=payload["destination_id"],
            effect_digest=payload["effect_digest"],
            verifier_principal=VERIFIER_PRINCIPAL,
            issued_at=issued,
            expires_at=issued + timedelta(hours=1),
            nonce_digest=signing_doc["nonce_digest"],
            verifier_signature_digest=_verifier_signature(signing_doc),
        )
    return EntitlementActionBoundaryQuery(**payload)


def _attestation(
    *,
    installation_id: str,
    artifact_digest: str = DIGEST_A,
    nonce_digest: str | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    subject_id: str = "antigravity",
    provider_id: str = "antigravity",
    project_profile_id: str = "uppercut",
    registration_generation: int = 1,
) -> InstallAttestationV1:
    issued = issued_at or _utc_now()
    expires = expires_at or (issued + timedelta(hours=2))
    entries = (
        ManifestEntryV1(
            path="src/director_orchestrator/worker.py",
            artifact_digest=artifact_digest,
            size_bytes=128,
        ),
    )
    canonical_manifest_digest = manifest_digest(entries)
    signing_doc = {
        "schema_version": "InstallAttestation/v1",
        "subject_kind": "provider",
        "subject_id": subject_id,
        "provider_id": provider_id,
        "project_profile_id": project_profile_id,
        "installation_id": installation_id,
        "source_commit_sha": COMMIT_A,
        "source_tree_sha": TREE_A,
        "manifest_entries": [entry.to_document() for entry in entries],
        "manifest_digest": canonical_manifest_digest,
        "artifact_digest": artifact_digest,
        "build_evidence_digest": document_digest({"build": artifact_digest}),
        "test_evidence_digest": document_digest({"tests": artifact_digest}),
        "registration_generation": registration_generation,
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
        subject_id=subject_id,
        provider_id=provider_id,
        project_profile_id=project_profile_id,
        installation_id=installation_id,
        source_commit_sha=COMMIT_A,
        source_tree_sha=TREE_A,
        manifest_entries=entries,
        manifest_digest=canonical_manifest_digest,
        artifact_digest=artifact_digest,
        build_evidence_digest=signing_doc["build_evidence_digest"],
        test_evidence_digest=signing_doc["test_evidence_digest"],
        registration_generation=registration_generation,
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
    def test_public_contract_parsers_are_strict_and_exact(self) -> None:
        with self.assertRaisesRegex(EntitlementContractError, "valid JSON"):
            parse_project_registration_v1(
                b'{"schema_version":"ProjectRegistration/v1","schema_version":"ProjectRegistration/v1"}'
            )
        registration = _registration()
        document = registration.to_document()
        document["capabilities"] = ["subject.chosen"]
        with self.assertRaisesRegex(EntitlementContractError, "unknown fields"):
            parse_project_registration_v1(json.dumps(document).encode())
        document = registration.to_document()
        document["registration_generation"] = True
        with self.assertRaisesRegex(EntitlementContractError, "wrong type"):
            parse_project_registration_v1(json.dumps(document).encode())
        attestation = _attestation(installation_id="parser-install")
        attestation_document = attestation.to_document()
        attestation_document["capabilities"] = ["deploy"]
        with self.assertRaisesRegex(EntitlementContractError, "unknown fields"):
            parse_install_attestation_v1(json.dumps(attestation_document).encode())
        self.assertEqual(
            parse_install_attestation_v1(
                json.dumps(attestation.to_document()).encode()
            ).digest,
            attestation.digest,
        )
        self.assertEqual(
            parse_signed_entitlement_template_v1(
                json.dumps(_signed_template().to_document()).encode()
            ).profile_template_id,
            PROFILE_TEMPLATE,
        )
        query = _boundary_query(installation_id="parser-install")
        self.assertEqual(
            parse_entitlement_action_boundary_query_v1(
                json.dumps(query.to_document()).encode()
            ).action_type,
            EntitlementActionType.REVIEW_EXACT_SHA,
        )
        deep = b'{"schema_version":"ProjectRegistration/v1","x":' + b'[' * 5000 + b'0' + b']' * 5000 + b'}'
        # Parser recursion thresholds vary by platform. Either a translated
        # recursion failure or the later exact-key rejection is acceptable;
        # a raw RecursionError must never escape this public contract.
        with self.assertRaises(EntitlementContractError):
            parse_project_registration_v1(deep)

    def test_owner_notice_acknowledgement_contract_round_trips(self) -> None:
        notice = OwnerNoticeV1(
            notice_id="notice-round-trip",
            installation_id="install-round-trip",
            entitlement_generation=1,
            delivery_state=OwnerNoticeDeliveryState.ACKNOWLEDGED,
            project_profile_id="uppercut",
            subject_kind=SubjectKind.PROVIDER,
            subject_id="antigravity",
            template_id=PROFILE_TEMPLATE,
            authority_bearing_capabilities=("review.exact_sha",),
            build_digest=DIGEST_A,
            pending_since=_utc_now(),
            ack_proof_digest=DIGEST_B,
        )
        parsed = parse_owner_notice_v1(json.dumps(notice.to_document()).encode())
        self.assertEqual(parsed.ack_proof_digest, DIGEST_B)

    def test_self_referential_attestation_signature_is_rejected(self) -> None:
        issued = _utc_now()
        expires = issued + timedelta(hours=1)
        entries = (ManifestEntryV1(path="src/app.py", artifact_digest=DIGEST_A, size_bytes=1),)
        signing_doc = {
            "schema_version": "InstallAttestation/v1",
            "subject_kind": "provider",
            "subject_id": "antigravity",
            "provider_id": "antigravity",
            "project_profile_id": "uppercut",
            "installation_id": "install-x",
            "source_commit_sha": COMMIT_A,
            "source_tree_sha": TREE_A,
            "manifest_entries": [entry.to_document() for entry in entries],
            "manifest_digest": manifest_digest(entries),
            "artifact_digest": DIGEST_A,
            "build_evidence_digest": DIGEST_A,
            "test_evidence_digest": DIGEST_B,
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
            manifest_entries=entries,
            manifest_digest=manifest_digest(entries),
            artifact_digest=DIGEST_A,
            build_evidence_digest=DIGEST_A,
            test_evidence_digest=DIGEST_B,
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
        global VERIFIER_SECRET_FILE, ROUTE_SECRET_FILE, OWNER_SECRET_FILE
        VERIFIER_SECRET_FILE = _write_secret(VERIFIER_SECRET.decode("utf-8"))
        ROUTE_SECRET_FILE = _write_secret(ROUTE_SECRET.decode("utf-8"))
        OWNER_SECRET_FILE = _write_secret(OWNER_SECRET.decode("utf-8"))
    def setUp(self) -> None:
        self.schema = f"entitlement_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_entitlement_control_plane(
            TEST_DSN,
            schema=self.schema,
            trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
            route_verifier_secret_file=ROUTE_SECRET_FILE,
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
            health_proof=_slack_health_proof(),
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
        for path in (VERIFIER_SECRET_FILE, ROUTE_SECRET_FILE, OWNER_SECRET_FILE):
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

    def test_install_verifier_cannot_forge_route_health_proof(self) -> None:
        proof = _slack_health_proof("slack-separated")
        forged = replace(
            proof,
            verifier_signature_digest=compute_signature(
                principal=proof.verifier_principal,
                signing_payload=signing_bytes(proof.signing_document()),
                secret=VERIFIER_SECRET,
            ),
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "signature invalid"):
            self.store.register_verified_slack_destination(
                destination_id="slack-separated",
                owner_signature_principal=OWNER_PRINCIPAL,
                owner_signature_digest=_owner_signature(
                    {
                        "schema_version": "OwnerSlackDestination/v1",
                        "destination_id": "slack-separated",
                        "owner_signature_principal": OWNER_PRINCIPAL,
                    }
                ),
                health_proof=forged,
            )

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
            owner_action=_install_action("revoke", installation_id, None),
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
                owner_action=replace(
                    _install_action("revoke", installation_id, None),
                    owner_signature_digest="0" * 64,
                ),
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
            route_proof=_route_proof(outcome.notice, "FAILED"),
        )
        self.assertEqual(
            self.store.authoritative_capabilities(
                installation_id,
                runtime_proof=_boundary_query(installation_id=installation_id).runtime_proof,
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
        deadline = issued + timedelta(seconds=NOTICE_SUSPENSION_SECONDS + 1)
        deadline_store = EntitlementControlPlaneStore(
            self.handle.pool,
            schema=self.schema,
            storage_identity=self.handle.storage_identity,
            trusted_verifier_secret=VERIFIER_SECRET,
            route_verifier_secret=ROUTE_SECRET,
            owner_authority_secret=OWNER_SECRET,
            clock=lambda: deadline,
        )
        denied = deadline_store.query_action_boundary(
            _boundary_query(installation_id=installation_id, now=issued)
        )
        self.assertFalse(denied.permitted)
        self.store.evaluate_notice_suspensions(now=deadline)
        with self.assertRaisesRegex(EntitlementControlPlaneError, "cannot reactivate"):
            self.store.mark_notice_delivered(
                outcome.notice.notice_id,
                idempotency_key_digest=document_digest({"deliver": outcome.notice.notice_id}),
                route_proof=_route_proof(outcome.notice, "DELIVERED"),
            )
        self.assertFalse(
            deadline_store.query_action_boundary(
                _boundary_query(installation_id=installation_id, now=issued)
            ).permitted
        )

    def test_schema_reopen_is_migration_safe(self) -> None:
        second = open_entitlement_control_plane(
            TEST_DSN,
            schema=self.schema,
            trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
            route_verifier_secret_file=ROUTE_SECRET_FILE,
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
                    route_verifier_secret_file=ROUTE_SECRET_FILE,
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
                route_verifier_secret_file=ROUTE_SECRET_FILE,
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
            owner_action=_install_action(
                "revoke", installation_id, first.entitlement_generation
            ),
        )
        restore_attestation = _attestation(
            installation_id=installation_id,
            artifact_digest=DIGEST_B,
            nonce_digest=document_digest({"restore-nonce": 1}),
        )
        restored = self.store.restore(
            restore_attestation,
            owner_action=_restore_action(
                restore_attestation, first.entitlement_generation
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
            manifest_entries=attestation.manifest_entries,
            manifest_digest=attestation.manifest_digest,
            artifact_digest=attestation.artifact_digest,
            build_evidence_digest=attestation.build_evidence_digest,
            test_evidence_digest=attestation.test_evidence_digest,
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
            route_verifier_secret_file=ROUTE_SECRET_FILE,
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
                    health_proof=_slack_health_proof(),
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
            route_verifier_secret_file=ROUTE_SECRET_FILE,
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
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            idempotency_key_digest=document_digest({"deliver": installation_id}),
            route_proof=_route_proof(outcome.notice, "DELIVERED"),
        )
        reg2 = _registration(generation=2)
        supersede_key = document_digest({"supersede": 2})
        supersede_request = document_digest(
            {
                "operation": "supersede",
                "project_profile_id": reg2.project_profile_id,
                "prior_generation": 1,
                "replacement_registration": reg2.to_document(),
                "replacement_generation": 2,
                "actor_id": OWNER_PRINCIPAL,
                "idempotency_key_digest": supersede_key,
            }
        )
        self.store.supersede_registration(
            reg2,
            prior_generation=1,
            owner_action=_owner_action(
                "supersede_registration",
                reg2.project_profile_id,
                1,
                supersede_key,
                supersede_request,
            ),
        )
        denied = self.store.query_action_boundary(
            _boundary_query(installation_id=installation_id)
        )
        self.assertFalse(denied.permitted)
        self.assertEqual(denied.denial_reason, "registration superseded")

    def test_rotating_grant_and_supersede_use_one_deadlock_free_lock_order(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        rotating = _attestation(
            installation_id=installation_id,
            artifact_digest=DIGEST_B,
            nonce_digest=document_digest({"rotate": installation_id}),
        )
        replacement = _registration(generation=2)
        supersede_key = document_digest({"supersede-race": installation_id})
        supersede_request = document_digest(
            {
                "operation": "supersede",
                "project_profile_id": replacement.project_profile_id,
                "prior_generation": 1,
                "replacement_registration": replacement.to_document(),
                "replacement_generation": 2,
                "actor_id": OWNER_PRINCIPAL,
                "idempotency_key_digest": supersede_key,
            }
        )
        supersede_action = _owner_action(
            "supersede_registration",
            replacement.project_profile_id,
            1,
            supersede_key,
            supersede_request,
        )
        barrier = threading.Barrier(2)
        original_lock_project = self.store._lock_project
        outcomes: list[str] = []
        failures: list[str] = []
        result_lock = threading.Lock()

        def gated_lock_project(connection, project_profile_id: str) -> None:
            barrier.wait(timeout=5)
            original_lock_project(connection, project_profile_id)

        def rotate_worker() -> None:
            try:
                self.store.grant_from_attestation(
                    rotating,
                    idempotency_key_digest=document_digest(
                        {"rotate-grant": installation_id}
                    ),
                )
                with result_lock:
                    outcomes.append("grant")
            except Exception as exc:  # captured for bounded cross-thread assertion
                with result_lock:
                    failures.append(f"grant:{type(exc).__name__}:{exc}")

        def supersede_worker() -> None:
            try:
                self.store.supersede_registration(
                    replacement,
                    prior_generation=1,
                    owner_action=supersede_action,
                )
                with result_lock:
                    outcomes.append("supersede")
            except Exception as exc:  # captured for bounded cross-thread assertion
                with result_lock:
                    failures.append(f"supersede:{type(exc).__name__}:{exc}")

        self.store._lock_project = gated_lock_project
        threads = [
            threading.Thread(target=rotate_worker),
            threading.Thread(target=supersede_worker),
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        finally:
            self.store._lock_project = original_lock_project
        self.assertFalse(any(thread.is_alive() for thread in threads), failures)
        self.assertIn("supersede", outcomes, failures)
        self.assertFalse(
            any("deadlock" in failure.lower() or "serialization" in failure.lower()
                for failure in failures),
            failures,
        )
        if failures:
            # Supersede won the project lock. Either exact rejection preserves
            # the same fail-closed outcome; the lifecycle check may run first.
            self.assertEqual(len(failures), 1, failures)
            self.assertIn(failures[0], (
                "grant:EntitlementControlPlaneError:project registration is not active",
                "grant:EntitlementControlPlaneError:suspended, revoked, superseded, "
                "or uninstalled installation cannot be granted",
            ))
            self.assertEqual(outcomes, ["supersede"])
            expected_generation = 1
        else:
            # Rotation won the lock, then supersede revoked its new generation.
            self.assertCountEqual(outcomes, ["grant", "supersede"])
            expected_generation = 2
        with self.handle.pool.connection() as connection:
            current = connection.execute(
                f"SELECT lifecycle_status, entitlement_generation "
                f"FROM {self.schema}.entitlement_current_generation "
                "WHERE installation_id = %s",
                (installation_id,),
            ).fetchone()
            active_registration = connection.execute(
                f"SELECT registration_generation FROM {self.schema}.entitlement_project_registration "
                "WHERE project_profile_id = 'uppercut' AND superseded_at IS NULL"
            ).fetchone()
        self.assertEqual(
            current["lifecycle_status"], EntitlementLifecycleStatus.SUPERSEDED.value
        )
        self.assertEqual(int(current["entitlement_generation"]), expected_generation)
        self.assertEqual(int(active_registration["registration_generation"]), 2)

    def test_uninstall_removes_authority(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            idempotency_key_digest=document_digest({"deliver": 1}),
            route_proof=_route_proof(outcome.notice, "DELIVERED"),
        )
        self.store.uninstall(
            installation_id,
            owner_action=_install_action(
                "uninstall", installation_id, outcome.entitlement_generation
            ),
        )
        self.assertEqual(
            self.store.authoritative_capabilities(
                installation_id,
                runtime_proof=_boundary_query(installation_id=installation_id).runtime_proof,
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
            owner_action=_install_action("revoke", install_a, 1),
        )
        self.assertEqual(
            self.store.authoritative_capabilities(
                install_a,
                runtime_proof=_boundary_query(installation_id=install_a).runtime_proof,
            ),
            frozenset(),
        )
        self.assertIn(
            "review.exact_sha",
            self.store.authoritative_capabilities(
                install_b,
                runtime_proof=_boundary_query(installation_id=install_b).runtime_proof,
            ),
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
            idempotency_key_digest=document_digest({"deliver": 1}),
            route_proof=_route_proof(outcome.notice, "DELIVERED"),
        )
        denied = self.store.query_action_boundary(
            _boundary_query(
                installation_id=installation_id,
                action_type=EntitlementActionType.PROVIDER_DISPATCH,
                destination_id="uppercut",
                reserved_spend_usd=2.0,
                now=issued,
            )
        )
        self.assertFalse(denied.permitted)
        self.assertEqual(denied.denial_reason, "shared budget ledger unavailable")

    def test_path_scope_and_traversal_denials(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        issued = _utc_now()
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id, issued_at=issued),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            idempotency_key_digest=document_digest({"deliver": 1}),
            route_proof=_route_proof(outcome.notice, "DELIVERED"),
        )
        allowed_query = _boundary_query(installation_id=installation_id, now=issued)
        allowed = self.store.query_action_boundary(allowed_query)
        self.assertTrue(allowed.permitted)
        self.assertIsNotNone(allowed.authorization_binding_digest)
        self.assertNotEqual(
            allowed.authorization_binding_digest, allowed_query.runtime_proof.digest
        )
        with self.handle.pool.connection() as connection:
            claim = connection.execute(
                f"SELECT authorization_document, authorization_binding_digest "
                f"FROM {self.schema}.entitlement_runtime_authorization_claim "
                "WHERE nonce_digest = %s",
                (allowed_query.runtime_proof.nonce_digest,),
            ).fetchone()
        self.assertEqual(
            document_digest(dict(claim["authorization_document"])),
            allowed.authorization_binding_digest,
        )
        self.assertEqual(
            claim["authorization_document"]["registration_document_digest"],
            _registration().digest,
        )
        # A movable branch name can never satisfy exact-SHA review authority.
        movable = self.store.query_action_boundary(
            _boundary_query(installation_id=installation_id, ref="main", now=issued)
        )
        self.assertFalse(movable.permitted)
        self.assertEqual(movable.denial_reason, "review ref is not the attested exact commit")
        outside = self.store.query_action_boundary(
            _boundary_query(
                installation_id=installation_id,
                path="docs/readme.md",
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
            proof = _boundary_query(installation_id=installation_id, now=issued).runtime_proof
            EntitlementActionBoundaryQuery(
                project_profile_id="uppercut",
                repository="edwardjones3976/director-orchestrator",
                ref=COMMIT_A,
                path="src/../secrets/key",
                provider_id="antigravity",
                subject_id="antigravity",
                installation_id=installation_id,
                current_build_digest=DIGEST_A,
                action_type=EntitlementActionType.REVIEW_EXACT_SHA,
                destination_id=None,
                effect_digest=DIGEST_B,
                reserved_spend_usd=0.0,
                runtime_proof=proof,
                now=issued,
            )

    def test_notice_callback_idempotency(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        fail_key = document_digest({"fail": outcome.notice.notice_id})
        proof = _route_proof(outcome.notice, "FAILED")
        first = self.store.mark_notice_failed(
            outcome.notice.notice_id,
            idempotency_key_digest=fail_key,
            route_proof=proof,
        )
        second = self.store.mark_notice_failed(
            outcome.notice.notice_id,
            idempotency_key_digest=fail_key,
            route_proof=proof,
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
        request = document_digest(
            {"operation": "revoke", "installation_id": installation_id, "expected_generation": 1}
        )
        action = _owner_action("revoke", installation_id, 1, key, request)
        results: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            event_id = self.store.revoke(
                installation_id,
                owner_action=action,
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
                owner_action=_owner_action(
                    "revoke",
                    installation_id,
                    1,
                    key,
                    document_digest(
                        {
                            "operation": "revoke",
                            "installation_id": installation_id,
                            "expected_generation": 1,
                        }
                    ),
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
            owner_action=_install_action(
                "revoke", installation_id, outcome.entitlement_generation
            ),
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "cannot reactivate"):
            self.store.mark_notice_delivered(
                outcome.notice.notice_id,
                idempotency_key_digest=document_digest({"delivery": installation_id}),
                route_proof=_route_proof(outcome.notice, "DELIVERED"),
            )
        self.assertEqual(
            self.store.authoritative_capabilities(
                installation_id,
                runtime_proof=_boundary_query(installation_id=installation_id).runtime_proof,
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

    def test_grant_bound_capabilities_fail_closed_on_template_drift(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            idempotency_key_digest=document_digest({"deliver": installation_id}),
            route_proof=_route_proof(outcome.notice, "DELIVERED"),
        )
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.entitlement_signed_template "
                "SET capabilities = %s::jsonb WHERE profile_template_id = %s",
                (json.dumps(["review.exact_sha"]), PROFILE_TEMPLATE),
            )
        denied = self.store.query_action_boundary(
            _boundary_query(installation_id=installation_id)
        )
        self.assertFalse(denied.permitted)
        self.assertEqual(denied.denial_reason, "grant authority drift detected")
        self.assertEqual(
            self.store.authoritative_capabilities(
                installation_id,
                runtime_proof=_boundary_query(installation_id=installation_id).runtime_proof,
            ),
            frozenset(),
        )

    def test_suspended_generation_cannot_be_regranted(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        issued = _utc_now()
        self.store.grant_from_attestation(
            _attestation(installation_id=installation_id, issued_at=issued),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.evaluate_notice_suspensions(
            now=issued + timedelta(seconds=NOTICE_SUSPENSION_SECONDS + 1)
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "suspended"):
            self.store.grant_from_attestation(
                _attestation(installation_id=installation_id, artifact_digest=DIGEST_B),
                idempotency_key_digest=document_digest({"regrant": installation_id}),
            )

    def test_restore_nonce_evidence_and_stale_replay_are_fail_closed(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        first = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.revoke(
            installation_id,
            owner_action=_install_action("revoke", installation_id, 1),
        )
        attestation = _attestation(
            installation_id=installation_id,
            artifact_digest=DIGEST_B,
            nonce_digest=document_digest({"restore-nonce": installation_id}),
        )
        action = _restore_action(attestation, first.entitlement_generation)
        restored = self.store.restore(attestation, owner_action=action)
        replay = self.store.restore(attestation, owner_action=action)
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(replay.event_id, restored.event_id)
        with self.handle.pool.connection() as connection:
            row = connection.execute(
                f"SELECT document, build_evidence_digest, test_evidence_digest "
                f"FROM {self.schema}.entitlement_install_attestation "
                "WHERE attestation_id = %s",
                (attestation.attestation_id,),
            ).fetchone()
        self.assertEqual(row["document"]["manifest_digest"], attestation.manifest_digest)
        self.assertEqual(row["build_evidence_digest"], attestation.build_evidence_digest)
        self.assertEqual(row["test_evidence_digest"], attestation.test_evidence_digest)
        self.store.revoke(
            installation_id,
            owner_action=_install_action(
                "revoke", installation_id, restored.entitlement_generation
            ),
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "current lineage"):
            self.store.restore(attestation, owner_action=action)

    def test_restore_exactly_binds_subject_and_registration_lineage(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.revoke(
            installation_id,
            owner_action=_install_action("revoke", installation_id, 1),
        )
        wrong = _attestation(
            installation_id=installation_id,
            artifact_digest=DIGEST_B,
            provider_id="grok",
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "lineage"):
            self.store.restore(wrong, owner_action=_restore_action(wrong, 1))

    def test_typed_actions_bind_signed_destinations_and_effects(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            idempotency_key_digest=document_digest({"deliver": installation_id}),
            route_proof=_route_proof(outcome.notice, "DELIVERED"),
        )
        authorized = self.store.query_action_boundary(
                _boundary_query(
                    installation_id=installation_id,
                    action_type=EntitlementActionType.SLACK_WRITE,
                    destination_id="slack-owner-dm",
                )
        )
        self.assertTrue(authorized.permitted)
        self.assertIsNotNone(authorized.authorization_binding_digest)
        denied = self.store.query_action_boundary(
            _boundary_query(
                installation_id=installation_id,
                action_type=EntitlementActionType.SLACK_WRITE,
                destination_id="slack-other",
            )
        )
        self.assertFalse(denied.permitted)
        self.assertEqual(denied.denial_reason, "signed destination mismatch")
        after_health_expiry = _utc_now() + timedelta(hours=1, seconds=1)
        future_store = EntitlementControlPlaneStore(
            self.handle.pool,
            schema=self.schema,
            storage_identity=self.handle.storage_identity,
            trusted_verifier_secret=VERIFIER_SECRET,
            route_verifier_secret=ROUTE_SECRET,
            owner_authority_secret=OWNER_SECRET,
            clock=lambda: after_health_expiry,
        )
        stale_health = future_store.query_action_boundary(
            _boundary_query(
                installation_id=installation_id,
                action_type=EntitlementActionType.SLACK_WRITE,
                destination_id="slack-owner-dm",
                now=after_health_expiry,
            )
        )
        self.assertFalse(stale_health.permitted)
        self.assertEqual(
            stale_health.denial_reason, "Slack destination health proof is stale"
        )

    def test_runtime_authorization_proof_is_atomically_single_use(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            idempotency_key_digest=document_digest({"deliver": installation_id}),
            route_proof=_route_proof(outcome.notice, "DELIVERED"),
        )
        query = _boundary_query(installation_id=installation_id)
        decisions = []
        failures: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                decision = self.store.query_action_boundary(query)
                with lock:
                    decisions.append(decision)
            except Exception as exc:  # captured for bounded cross-thread assertion
                with lock:
                    failures.append(f"{type(exc).__name__}:{exc}")

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads), failures)
        self.assertEqual(failures, [])
        self.assertEqual(sum(decision.permitted for decision in decisions), 1)
        bindings = {decision.authorization_binding_digest for decision in decisions}
        self.assertEqual(len(bindings), 1)
        self.assertNotIn(None, bindings)
        self.assertNotIn(query.runtime_proof.digest, bindings)
        self.assertEqual(
            sum(decision.denial_reason == "runtime authorization already consumed"
                for decision in decisions),
            5,
        )

        changed = _boundary_query(
            installation_id=installation_id,
            effect_digest=DIGEST_B,
        )
        changed_proof = replace(
            changed.runtime_proof,
            nonce_digest=query.runtime_proof.nonce_digest,
        )
        changed_proof = replace(
            changed_proof,
            verifier_signature_digest=_verifier_signature(
                changed_proof.signing_document()
            ),
        )
        mismatched = self.store.query_action_boundary(
            replace(changed, runtime_proof=changed_proof)
        )
        self.assertFalse(mismatched.permitted)
        self.assertEqual(
            mismatched.denial_reason, "runtime authorization nonce binding mismatch"
        )

    def test_action_claim_cannot_commit_from_pre_revocation_eligibility(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        outcome = self.store.grant_from_attestation(
            _attestation(installation_id=installation_id),
            idempotency_key_digest=document_digest({"grant": installation_id}),
        )
        self.store.mark_notice_delivered(
            outcome.notice.notice_id,
            idempotency_key_digest=document_digest({"deliver": installation_id}),
            route_proof=_route_proof(outcome.notice, "DELIVERED"),
        )
        revoke_action = _install_action(
            "revoke", installation_id, outcome.entitlement_generation
        )
        query = _boundary_query(installation_id=installation_id)
        revoke_done: list[str] = []
        query_decisions = []
        failures: list[str] = []
        query_started = threading.Event()

        def revoke_worker() -> None:
            try:
                revoke_done.append(
                    self.store.revoke(installation_id, owner_action=revoke_action)
                )
            except Exception as exc:
                failures.append(f"revoke:{type(exc).__name__}:{exc}")

        def query_worker() -> None:
            query_started.set()
            try:
                query_decisions.append(self.store.query_action_boundary(query))
            except Exception as exc:
                failures.append(f"query:{type(exc).__name__}:{exc}")

        with self.handle.pool.connection() as blocker:
            blocker.execute("BEGIN")
            blocker.execute(
                f"SELECT 1 FROM {self.schema}.entitlement_current_generation "
                "WHERE installation_id = %s FOR UPDATE",
                (installation_id,),
            )
            revoke_thread = threading.Thread(target=revoke_worker)
            revoke_thread.start()
            lock_key = f"{self.schema}:{installation_id}"
            revoke_holds_installation_lock = False
            for _ in range(100):
                with self.handle.pool.connection() as probe:
                    acquired = probe.execute(
                        "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                        (lock_key,),
                    ).fetchone()["acquired"]
                    if acquired:
                        probe.execute(
                            "SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,)
                        )
                    else:
                        revoke_holds_installation_lock = True
                        break
                time.sleep(0.01)
            self.assertTrue(revoke_holds_installation_lock)
            query_thread = threading.Thread(target=query_worker)
            query_thread.start()
            self.assertTrue(query_started.wait(timeout=2))
            time.sleep(0.05)
            self.assertTrue(query_thread.is_alive())
            blocker.execute("COMMIT")
        revoke_thread.join(timeout=10)
        query_thread.join(timeout=10)
        self.assertFalse(revoke_thread.is_alive())
        self.assertFalse(query_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(revoke_done), 1)
        self.assertEqual(len(query_decisions), 1)
        self.assertFalse(query_decisions[0].permitted)
        self.assertEqual(
            query_decisions[0].lifecycle_status, EntitlementLifecycleStatus.REVOKED
        )
        with self.handle.pool.connection() as connection:
            claim_count = connection.execute(
                f"SELECT count(*) AS count "
                f"FROM {self.schema}.entitlement_runtime_authorization_claim "
                "WHERE nonce_digest = %s",
                (query.runtime_proof.nonce_digest,),
            ).fetchone()["count"]
        self.assertEqual(claim_count, 0)

    def test_owner_action_expiry_and_nonce_replay_are_rejected(self) -> None:
        installation_id = f"install-{uuid.uuid4().hex[:8]}"
        stale_time = _utc_now() - timedelta(hours=1)
        stale_key = document_digest({"stale": installation_id})
        stale_request = document_digest(
            {"operation": "revoke", "installation_id": installation_id, "expected_generation": None}
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "not currently valid"):
            self.store.revoke(
                installation_id,
                owner_action=_owner_action(
                    "revoke", installation_id, None, stale_key, stale_request,
                    issued_at=stale_time,
                ),
            )
        shared_nonce = document_digest({"shared-owner-nonce": uuid.uuid4().hex})
        first = _install_action("revoke", installation_id, None)
        self.store.revoke(
            installation_id,
            owner_action=_owner_action(
                "revoke", installation_id, None, first.idempotency_key_digest,
                first.request_digest, nonce_digest=shared_nonce,
            ),
        )
        second_installation = f"install-{uuid.uuid4().hex[:8]}"
        second_key = document_digest({"second": second_installation})
        second_request = document_digest(
            {"operation": "revoke", "installation_id": second_installation, "expected_generation": None}
        )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "nonce replayed"):
            self.store.revoke(
                second_installation,
                owner_action=_owner_action(
                    "revoke", second_installation, None, second_key, second_request,
                    nonce_digest=shared_nonce,
                ),
            )

    def test_supersede_action_binds_complete_replacement_registration(self) -> None:
        original = _registration(generation=2)
        key = document_digest({"supersede-binding": 2})
        request = document_digest(
            {
                "operation": "supersede",
                "project_profile_id": original.project_profile_id,
                "prior_generation": 1,
                "replacement_registration": original.to_document(),
                "replacement_generation": 2,
                "actor_id": OWNER_PRINCIPAL,
                "idempotency_key_digest": key,
            }
        )
        changed = _registration(generation=2, notion_destination_id="notion-other")
        with self.assertRaisesRegex(EntitlementControlPlaneError, "exact request"):
            self.store.supersede_registration(
                changed,
                prior_generation=1,
                owner_action=_owner_action(
                    "supersede_registration", "uppercut", 1, key, request
                ),
            )

    def test_slack_health_proof_and_active_registration_are_fail_closed(self) -> None:
        with self.assertRaisesRegex(EntitlementControlPlaneError, "not fresh"):
            self.store.register_verified_slack_destination(
                destination_id="slack-stale",
                owner_signature_principal=OWNER_PRINCIPAL,
                owner_signature_digest=_owner_signature(
                    {
                        "schema_version": "OwnerSlackDestination/v1",
                        "destination_id": "slack-stale",
                        "owner_signature_principal": OWNER_PRINCIPAL,
                    }
                ),
                health_proof=replace(
                    _slack_health_proof("slack-stale"),
                    issued_at=_utc_now() - timedelta(hours=2),
                    expires_at=_utc_now() - timedelta(hours=1),
                ),
            )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "superseded atomically"):
            self.store.register_project(_registration(generation=2))

    def test_schema_trigger_and_secret_separation_are_enforced(self) -> None:
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"DROP TRIGGER entitlement_install_attestation_immutable "
                f"ON {self.schema}.entitlement_install_attestation"
            )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "trigger contract"):
            open_entitlement_control_plane(
                TEST_DSN,
                schema=self.schema,
                trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
                route_verifier_secret_file=ROUTE_SECRET_FILE,
                owner_authority_secret_file=OWNER_SECRET_FILE,
                allow_insecure_local_transport=True,
            )
        same_secret = _write_secret(OWNER_SECRET.decode())
        try:
            with self.assertRaisesRegex(EntitlementControlPlaneError, "separated"):
                open_entitlement_control_plane(
                    TEST_DSN,
                    schema=f"entitlement_secret_{uuid.uuid4().hex[:8]}_v1",
                    trusted_verifier_secret_file=same_secret,
                    route_verifier_secret_file=ROUTE_SECRET_FILE,
                    owner_authority_secret_file=same_secret,
                    allow_insecure_local_transport=True,
                )
        finally:
            Path(same_secret).unlink(missing_ok=True)

        with self.assertRaisesRegex(EntitlementControlPlaneError, "entropy"):
            EntitlementControlPlaneStore(
                object(),
                schema="entitlement_direct_v1",
                storage_identity=DIGEST_A,
                trusted_verifier_secret=b"weak",
                route_verifier_secret=ROUTE_SECRET,
                owner_authority_secret=OWNER_SECRET,
            )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "separated"):
            EntitlementControlPlaneStore(
                object(),
                schema="entitlement_direct_v1",
                storage_identity=DIGEST_A,
                trusted_verifier_secret=VERIFIER_SECRET,
                route_verifier_secret=VERIFIER_SECRET,
                owner_authority_secret=OWNER_SECRET,
            )

    def test_schema_manifest_detects_dropped_critical_constraint(self) -> None:
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"ALTER TABLE {self.schema}.entitlement_install_attestation "
                "DROP CONSTRAINT entitlement_install_attestation_nonce_digest_key"
            )
        with self.assertRaisesRegex(EntitlementControlPlaneError, "key constraint"):
            open_entitlement_control_plane(
                TEST_DSN,
                schema=self.schema,
                trusted_verifier_secret_file=VERIFIER_SECRET_FILE,
                route_verifier_secret_file=ROUTE_SECRET_FILE,
                owner_authority_secret_file=OWNER_SECRET_FILE,
                allow_insecure_local_transport=True,
            )

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
                route_verifier_secret_file=ROUTE_SECRET_FILE,
                owner_authority_secret_file=OWNER_SECRET_FILE,
                allow_insecure_local_transport=True,
            )

    def test_config_uses_secret_file_references_not_raw_slots(self) -> None:
        settings = Settings()
        self.assertTrue(hasattr(settings, "entitlement_postgres_dsn_file"))
        self.assertTrue(hasattr(settings, "entitlement_trusted_verifier_secret_file"))
        self.assertTrue(hasattr(settings, "entitlement_route_verifier_secret_file"))
        self.assertTrue(hasattr(settings, "entitlement_owner_authority_secret_file"))
        self.assertFalse(hasattr(settings, "entitlement_postgres_dsn"))
        self.assertFalse(hasattr(settings, "entitlement_trusted_verifier_secret"))
        self.assertFalse(hasattr(settings, "entitlement_route_verifier_secret"))

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
        self.assertNotIn("entitlement_route_verifier_secret", field_names)


VERIFIER_SECRET = b"0123456789abcdefABCDEF!@#$%^&*()_+-=verifier-key"
ROUTE_SECRET = b"route-9876543210-ZYXWVUT!@#$%^&*()_+-=health-key"
OWNER_SECRET = b"fedcba9876543210ZYXWVU!@#$%^&*()_+-=owner-key"
VERIFIER_SECRET_FILE = ""
ROUTE_SECRET_FILE = ""
OWNER_SECRET_FILE = ""


if __name__ == "__main__":
    unittest.main()
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 22:15 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:30 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:50 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:34 UTC | END]]
