# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:02 UTC | BEGIN]]
from __future__ import annotations

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
    PrimaryCommandBindingError,
    PrimaryIngressProof,
    build_primary_ingress_proof,
    verify_primary_ingress_proof,
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
                "windows-copilot",
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
