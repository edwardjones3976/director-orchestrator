# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 03:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:02 UTC | BEGIN]]
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import BackgroundTasks, HTTPException

from director_orchestrator import main
from director_orchestrator.approvals import build_slack_intake_attestation
from director_orchestrator.config import Settings
from director_orchestrator.langgraph_primary import ApprovalKind
from director_orchestrator.models import IntakeAuthentication, SlackIntake
from director_orchestrator.primary_commands import (
    ApprovalResumeRequest,
    CancellationRequest,
    CompletionEvidenceRequest,
    CompletionAuthorityProof,
    PrimaryCommandBindingError,
    PrimaryIngressProof,
    build_completion_authority_proof,
    build_primary_ingress_proof,
    verify_primary_ingress_proof,
)
from director_orchestrator.primary_activation_commands import (
    ProviderActivationAdvanceProof,
    ProviderActivationAdvanceRequest,
    build_provider_activation_advance_proof,
)


SECRET = "s" * 32
SHARED = "h" * 32
DIGEST = "sha256:" + "a" * 64


def _approval() -> ApprovalResumeRequest:
    placeholder = PrimaryIngressProof(
        issued_at=2_000_000_000,
        nonce="n" * 32,
        signature="0" * 64,
    )
    request = ApprovalResumeRequest(
        task_id="task-1",
        command_id="approval-1",
        actor_id="owner-1",
        expected_projection_digest=DIGEST,
        expected_fencing_generation=7,
        kind=ApprovalKind.EXECUTION,
        request_digest=DIGEST,
        approval_event_id="event-1",
        slack_gateway_proof=placeholder,
    )
    proof = build_primary_ingress_proof(
        SECRET,
        SHARED,
        request,
        path="/v1/primary/tasks/task-1/approvals/resume",
        proof_field="slack_gateway_proof",
        issued_at=placeholder.issued_at,
        nonce=placeholder.nonce,
    )
    return request.model_copy(update={"slack_gateway_proof": proof})


class PrimaryAuthenticatedIngressTests(unittest.TestCase):
    def _activation_advance(self, settings: Settings) -> ProviderActivationAdvanceRequest:
        now = datetime.now(timezone.utc)
        placeholder = ProviderActivationAdvanceProof(
            issued_at=int(now.timestamp()), nonce="A" * 32, signature="0" * 64
        )
        request = ProviderActivationAdvanceRequest(
            actor_id="owner-1",
            binding_version="activation-v1",
            surface="app",
            stage="INSTALLED",
            expected_revision=0,
            evidence_id="observed-install-1",
            evidence_digest="a" * 64,
            observed_at=now,
            expires_at=now.replace(year=now.year + 1),
            gateway_proof=placeholder,
        )
        proof = build_provider_activation_advance_proof(
            settings.slack_gateway_secret,
            settings.shared_secret,
            "openai",
            request,
            issued_at=placeholder.issued_at,
            nonce=placeholder.nonce,
        )
        return request.model_copy(update={"gateway_proof": proof})

    def test_default_live_roster_uses_all_nine_exact_lane_ids(self) -> None:
        self.assertEqual(
            (
                "openai",
                "claude",
                "cursor",
                "grok",
                "antigravity",
                "perplexity",
                "firecrawl",
                "github-copilot",
                "microsoft-365-copilot",
            ),
            Settings().langgraph_primary_required_providers,
        )

    def test_slack_proof_binds_projection_writer_and_exact_body(self) -> None:
        request = _approval()
        verify_primary_ingress_proof(
            SECRET,
            SHARED,
            request,
            path="/v1/primary/tasks/task-1/approvals/resume",
            proof_field="slack_gateway_proof",
            now=2_000_000_001,
        )
        changed = request.model_copy(update={"expected_fencing_generation": 8})
        with self.assertRaisesRegex(
            PrimaryCommandBindingError, "signature is invalid"
        ):
            verify_primary_ingress_proof(
                SECRET,
                SHARED,
                changed,
                path="/v1/primary/tasks/task-1/approvals/resume",
                proof_field="slack_gateway_proof",
                now=2_000_000_001,
            )

    def test_paid_primary_intake_verifies_and_persists_slack_classification(self) -> None:
        settings = replace(
            main.settings,
            allow_paid_runs=True,
            shared_secret=SHARED,
            slack_gateway_secret=SECRET,
            slack_owner_user_id="owner-1",
            slack_channel_id="channel-1",
            langgraph_primary_new_intake_enabled=True,
        )
        intake = SlackIntake(
            source_event_id="event-1",
            channel_id="channel-1",
            thread_ts="1.0",
            user_id="owner-1",
            text="bounded work",
            received_at=datetime.now(timezone.utc),
        )
        attestation = build_slack_intake_attestation(
            SECRET, SHARED, intake, issued_at=2_000_000_000, nonce="n" * 32
        )
        application = MagicMock()
        application.create_intake.return_value = MagicMock()
        activation = SimpleNamespace(application=application)
        with patch.object(main, "settings", settings), patch.object(
            main, "primary_default_activation", activation
        ), patch("director_orchestrator.approvals.datetime") as clock:
            clock.now.return_value = datetime.fromtimestamp(2_000_000_001, timezone.utc)
            main.intake(
                intake,
                BackgroundTasks(),
                intake_issued_at=attestation["X-Director-Intake-Issued-At"],
                intake_nonce=attestation["X-Director-Intake-Nonce"],
                intake_signature=attestation["X-Director-Intake-Signature"],
            )
        self.assertEqual(
            application.create_intake.call_args.kwargs["intake_authentication"],
            IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
        )
        self.assertIsNotNone(
            application.create_intake.call_args.kwargs[
                "authenticated_replay_identity"
            ]
        )

    def test_effect_ingress_requires_result_and_authenticated_readback(self) -> None:
        request = MagicMock(
            actor_id="writer-1", writer_owner_id="writer-1", result_digest=None
        )
        with self.assertRaises(HTTPException) as caught:
            main.primary_reconcile_effect("task-1", request)
        self.assertEqual(caught.exception.status_code, 409)

    def test_cancel_requires_signed_gateway_proof_and_durable_nonce_claim(self) -> None:
        settings = replace(
            main.settings,
            shared_secret=SHARED,
            slack_gateway_secret=SECRET,
            slack_owner_user_id="owner-1",
            slack_channel_id="channel-1",
        )
        placeholder = PrimaryIngressProof(
            issued_at=2_000_000_000,
            nonce="c" * 32,
            signature="0" * 64,
            channel_id="channel-1",
        )
        request = CancellationRequest(
            task_id="task-1",
            command_id="cancel-1",
            actor_id="owner-1",
            expected_projection_digest=DIGEST,
            expected_fencing_generation=7,
            gateway_proof=placeholder,
        )
        proof = build_primary_ingress_proof(
            SECRET,
            SHARED,
            request,
            path="/v1/primary/tasks/task-1/cancel",
            proof_field="gateway_proof",
            issued_at=placeholder.issued_at,
            nonce=placeholder.nonce,
            channel_id="channel-1",
        )
        request = request.model_copy(update={"gateway_proof": proof})
        coordinator = MagicMock()
        projection = SimpleNamespace(
            task_id="task-1", projection_digest=DIGEST, projection={}
        )
        writer = SimpleNamespace(fencing_generation=7)
        with patch.object(main, "settings", settings), patch.object(
            main, "_primary_command_projection",
            return_value=(coordinator, projection, writer, None),
        ), patch("director_orchestrator.primary_commands.datetime") as clock:
            clock.now.return_value = datetime.fromtimestamp(2_000_000_001, timezone.utc)
            main._verify_primary_owner_command_proof(
                "task-1", request, main.PrimaryCommandOperation.CANCELLATION
            )
        coordinator.claim_command_authorization.assert_called_once()

        with patch.object(main, "settings", settings):
            with self.assertRaises(HTTPException) as caught:
                main._verify_primary_owner_command_proof(
                    "task-1",
                    request.model_copy(update={"gateway_proof": None}),
                    main.PrimaryCommandOperation.CANCELLATION,
                )
        self.assertEqual(403, caught.exception.status_code)

    def test_completion_evidence_requires_authenticated_actor_and_writer_path(self) -> None:
        placeholder = CompletionAuthorityProof(
            authority="independent_review",
            issuer_id="review-issuer",
            principal_id="reviewer-1",
            issued_at=2_000_000_000,
            nonce="e" * 32,
            signature="0" * 64,
        )
        request = CompletionEvidenceRequest(
            task_id="task-1",
            command_id="evidence-1",
            actor_id="evidence-aggregator",
            expected_projection_digest=DIGEST,
            expected_fencing_generation=7,
            candidate_sha="1" * 40,
            provider_result_digest=DIGEST,
            ledger={"ledger_digest": DIGEST},
            results=(
                {
                    "result_digest": DIGEST,
                    "evidence_role": "INDEPENDENT_REVIEWER",
                    "evidence_actor": {"authenticated_principal": "reviewer-1"},
                    "gate_id": "review",
                },
                {
                    "result_digest": "sha256:" + "b" * 64,
                    "evidence_role": "TRUSTED_EXTERNAL_CI",
                    "evidence_actor": {"authenticated_principal": "ci-1"},
                    "gate_id": "ci",
                },
                {
                    "result_digest": "sha256:" + "c" * 64,
                    "evidence_role": "CODEX_SECURITY",
                    "evidence_actor": {"authenticated_principal": "security-1"},
                    "gate_id": "security",
                },
            ),
            ci_gate_id="ci",
            security_gate_id="security",
            independent_review_authority_proof=placeholder,
        )
        secrets_by_role = {
            "independent_review": "r" * 32,
            "ci": "c" * 32,
            "security": "x" * 32,
        }
        proof_fields = {
            "independent_review": "independent_review_authority_proof",
            "ci": "ci_authority_proof",
            "security": "security_authority_proof",
        }
        actors = {
            "independent_review": ("review-issuer", "reviewer-1"),
            "ci": ("ci-issuer", "ci-1"),
            "security": ("security-issuer", "security-1"),
        }
        proofs = {
            proof_fields[authority]: build_completion_authority_proof(
                secrets_by_role[authority],
                SHARED,
                request,
                authority=authority,
                issuer_id=actors[authority][0],
                principal_id=actors[authority][1],
                issued_at=placeholder.issued_at,
                nonce=(authority[0] * 32),
            )
            for authority in actors
        }
        request = request.model_copy(update=proofs)
        coordinator = MagicMock()
        projection = SimpleNamespace(
            task_id="task-1", projection_digest=DIGEST, projection={}
        )
        settings = replace(
            main.settings,
            shared_secret=SHARED,
            langgraph_primary_adapter_callback_secret=SECRET,
            langgraph_primary_independent_review_evidence_secret=("r" * 32),
            langgraph_primary_ci_evidence_secret=("c" * 32),
            langgraph_primary_security_evidence_secret=("x" * 32),
            langgraph_primary_completion_evidence_actor_allowlist_json=json.dumps(
                {
                    "independent_review": {
                        "issuers": ["review-issuer"],
                        "principals": ["reviewer-1"],
                    },
                    "ci": {
                        "issuers": ["ci-issuer"],
                        "principals": ["ci-1"],
                    },
                    "security": {
                        "issuers": ["security-issuer"],
                        "principals": ["security-1"],
                    },
                }
            ),
        )
        overlapping = replace(
            settings,
            langgraph_primary_completion_evidence_actor_allowlist_json=json.dumps(
                {
                    "independent_review": {
                        "issuers": ["shared-issuer"],
                        "principals": ["reviewer-1"],
                    },
                    "ci": {
                        "issuers": ["shared-issuer"],
                        "principals": ["ci-1"],
                    },
                    "security": {
                        "issuers": ["security-issuer"],
                        "principals": ["security-1"],
                    },
                }
            ),
        )
        with self.assertRaisesRegex(ValueError, "issuers must be disjoint"):
            overlapping.completion_evidence_actor_allowlist()
        with patch.object(main, "settings", settings), patch.object(
            main, "_primary_command_projection",
            return_value=(coordinator, projection, SimpleNamespace(), None),
        ), patch.object(main, "_record_primary_command", return_value=MagicMock()), patch(
            "director_orchestrator.completion_proof.datetime"
        ) as clock:
            clock.now.return_value = datetime.fromtimestamp(2_000_000_001, timezone.utc)
            main.primary_completion_evidence("task-1", request)
            adapter_proof = build_completion_authority_proof(
                SECRET,
                SHARED,
                request,
                authority="security",
                issuer_id="security-issuer",
                principal_id="security-1",
                issued_at=placeholder.issued_at,
                nonce="f" * 32,
            )
            with self.assertRaises(HTTPException) as cross_role:
                main.primary_completion_evidence(
                    "task-1",
                    request.model_copy(
                        update={"security_authority_proof": adapter_proof}
                    ),
                )
            tampered = list(request.results)
            tampered[2] = {
                **tampered[2],
                "evidence_actor": {"authenticated_principal": "provider-adapter-1"},
            }
            tampered_request = request.model_copy(
                update={"results": tuple(tampered)}
            )
            with self.assertRaises(HTTPException) as wrong_principal:
                main.primary_completion_evidence("task-1", tampered_request)
            same_gate = request.model_copy(
                update={"security_gate_id": "ci"}
            )
            with self.assertRaises(HTTPException) as duplicate_gate:
                main.primary_completion_evidence("task-1", same_gate)
            swapped_results = list(request.results)
            swapped_results[1] = {
                **swapped_results[1], "evidence_role": "CODEX_SECURITY"
            }
            swapped_results[2] = {
                **swapped_results[2], "evidence_role": "TRUSTED_EXTERNAL_CI"
            }
            swapped = request.model_copy(
                update={"results": tuple(swapped_results)}
            )
            with self.assertRaises(HTTPException) as swapped_kinds:
                main.primary_completion_evidence("task-1", swapped)
        coordinator.record_completion_evidence.assert_called_once()
        self.assertEqual(403, cross_role.exception.status_code)
        self.assertEqual(403, wrong_principal.exception.status_code)
        self.assertEqual(403, duplicate_gate.exception.status_code)
        self.assertEqual(403, swapped_kinds.exception.status_code)

    def test_provider_activation_route_requires_exact_signed_owner_gateway_proof(self) -> None:
        settings = replace(
            main.settings,
            shared_secret=SHARED,
            slack_gateway_secret=SECRET,
            slack_owner_user_id="owner-1",
        )
        request = self._activation_advance(settings)
        provider_activation = MagicMock()
        provider_activation.health.return_value = {"combined_live_ready": False}
        activation = SimpleNamespace(provider_activation=provider_activation)
        with patch.object(main, "settings", settings), patch.object(
            main, "primary_default_activation", activation
        ):
            response = main.primary_advance_provider_activation("openai", request)
            with self.assertRaises(HTTPException) as caught:
                main.primary_advance_provider_activation(
                    "openai", request.model_copy(update={"actor_id": "owner-2"})
                )
        self.assertFalse(response["combined_live_ready"])
        provider_activation.record_authorized.assert_called_once()
        self.assertEqual(
            "owner-1",
            provider_activation.record_authorized.call_args.kwargs[
                "authorization"
            ].actor_id,
        )
        self.assertEqual(403, caught.exception.status_code)

    def test_legacy_replica_consults_shared_cutover_fence_before_sqlite(self) -> None:
        settings = replace(
            main.settings,
            allow_paid_runs=False,
            langgraph_primary_postgres_dsn="postgresql://test.invalid/db?sslmode=require",
        )
        intake = SlackIntake(
            source_event_id="event-fenced",
            channel_id="channel-1",
            thread_ts="2.0",
            user_id="owner-1",
            text="must not enter legacy sqlite",
            received_at=datetime.now(timezone.utc),
        )
        handle = MagicMock()
        handle.claim_store.current_intake_authority.return_value = SimpleNamespace(
            legacy_new_intake_disabled=True
        )
        with patch.object(main, "settings", settings), patch.object(
            main, "primary_default_activation", None
        ), patch(
            "director_orchestrator.langgraph_postgres.open_postgres_control_plane",
            return_value=handle,
        ), patch.object(main.store, "create") as create:
            with self.assertRaises(HTTPException) as caught:
                main.intake(intake, BackgroundTasks())
        self.assertEqual(caught.exception.status_code, 409)
        create.assert_not_called()
        handle.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:02 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 03:05 UTC | END]]
