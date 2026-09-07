# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:04 UTC | BEGIN]]
from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

from pydantic import ValidationError

from director_orchestrator.langgraph_primary import (
    ApprovalDecision,
    ApprovalKind,
    EffectDisposition,
    PrimaryOutcome,
    ProviderDispatchStatus,
    ProviderDisposition,
    TerminalClassification,
    WorkflowRunStatus,
    EffectStatus,
)
from director_orchestrator.primary_commands import (
    ApprovalResumeRequest,
    CancellationRequest,
    EffectReconciliationRequest,
    PrimaryCommandBindingError,
    PrimaryCommandOperation,
    PrimaryCommandResponse,
    ProviderResultRequest,
    RecoveryRequest,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _binding() -> dict[str, object]:
    return {
        "task_id": "task-1",
        "command_id": "command-1",
        "actor_id": "director.owner",
        "expected_projection_digest": _digest("a"),
        "expected_fencing_generation": 7,
    }


class PrimaryCommandRequestTests(unittest.TestCase):
    def test_approval_request_is_closed_frozen_and_translates_exactly(self) -> None:
        request = ApprovalResumeRequest(
            **_binding(),
            kind=ApprovalKind.EXECUTION,
            request_digest=_digest("b"),
            approval_event_id="approval-1",
            decision=ApprovalDecision.APPROVE,
        )

        command = request.to_command()
        self.assertEqual(command.task_id, "task-1")
        self.assertEqual(command.request_digest, _digest("b"))
        self.assertEqual(command.approval_event_id, "approval-1")
        self.assertEqual(request.command_digest, request.model_copy().command_digest)
        with self.assertRaises(ValidationError):
            ApprovalResumeRequest.model_validate(
                {**request.model_dump(mode="json"), "unexpected": True}
            )
        with self.assertRaises(ValidationError):
            request.task_id = "other"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            command.task_id = "other"  # type: ignore[misc]

    def test_projection_binding_rejects_cross_task_and_stale_inputs(self) -> None:
        request = CancellationRequest(**_binding())
        request.validate_binding(
            path_task_id="task-1",
            current_projection_digest=_digest("a"),
            current_fencing_generation=7,
        )
        with self.assertRaisesRegex(PrimaryCommandBindingError, "request path"):
            request.validate_binding(
                path_task_id="task-2",
                current_projection_digest=_digest("a"),
                current_fencing_generation=7,
            )
        with self.assertRaisesRegex(PrimaryCommandBindingError, "stale task projection"):
            request.validate_binding(
                path_task_id="task-1",
                current_projection_digest=_digest("f"),
                current_fencing_generation=7,
            )
        with self.assertRaisesRegex(PrimaryCommandBindingError, "stale writer generation"):
            request.validate_binding(
                path_task_id="task-1",
                current_projection_digest=_digest("a"),
                current_fencing_generation=8,
            )

    def test_provider_result_preserves_idempotency_and_authority_bindings(self) -> None:
        request = ProviderResultRequest(
            **_binding(),
            provider_attempt_digest=_digest("b"),
            idempotency_key_digest=_digest("c"),
            authority_snapshot_digest=_digest("d"),
            producer_id="external_openai.worker",
            disposition=ProviderDisposition.COMPLETED,
            result_digest=_digest("e"),
            candidate_sha="1" * 40,
        )
        command = request.to_command()
        replay = request.to_command()
        self.assertEqual(command, replay)
        self.assertEqual(command.idempotency_key_digest, _digest("c"))
        self.assertEqual(command.authority_snapshot_digest, _digest("d"))
        self.assertEqual(command.producer_id, "external_openai.worker")
        request.validate_replay(request.command_digest)

        conflicting = request.model_copy(update={"result_digest": _digest("f")})
        with self.assertRaisesRegex(PrimaryCommandBindingError, "different content"):
            conflicting.validate_replay(request.command_digest)

    def test_completed_provider_result_requires_result_and_candidate(self) -> None:
        request = ProviderResultRequest(
            **_binding(),
            provider_attempt_digest=_digest("b"),
            idempotency_key_digest=_digest("c"),
            authority_snapshot_digest=_digest("d"),
            producer_id="claude.worker",
            disposition=ProviderDisposition.COMPLETED,
        )
        with self.assertRaisesRegex(ValueError, "require result_digest and candidate_sha"):
            request.to_command()

    def test_effect_reconciliation_preserves_attempt_and_writer_binding(self) -> None:
        request = EffectReconciliationRequest(
            **_binding(),
            effect_key_digest=_digest("b"),
            effect_attempt_digest=_digest("c"),
            writer_owner_id="director-writer-1",
            disposition=EffectDisposition.COMPLETED,
            result_digest=_digest("d"),
            readback_digest=_digest("e"),
        )
        command = request.to_command()
        self.assertEqual(command.effect_key_digest, _digest("b"))
        self.assertEqual(command.effect_attempt_digest, _digest("c"))
        self.assertEqual(command.writer_owner_id, "director-writer-1")

    def test_noncompleted_effect_cannot_claim_readback(self) -> None:
        request = EffectReconciliationRequest(
            **_binding(),
            effect_key_digest=_digest("b"),
            effect_attempt_digest=_digest("c"),
            writer_owner_id="director-writer-1",
            disposition=EffectDisposition.AMBIGUOUS,
            readback_digest=_digest("e"),
        )
        with self.assertRaisesRegex(ValueError, "only completed effects"):
            request.to_command()

    def test_cancel_and_recover_are_stable_task_commands(self) -> None:
        cancel = CancellationRequest(**_binding())
        recovery_binding = {**_binding(), "command_id": "recovery-1"}
        recover = RecoveryRequest(**recovery_binding)
        self.assertEqual(cancel.to_task_id(), "task-1")
        self.assertEqual(recover.to_task_id(), "task-1")
        self.assertEqual(cancel.command_digest, cancel.model_copy().command_digest)
        self.assertEqual(recover.command_digest, recover.model_copy().command_digest)

    def test_runtime_receipts_bound_crash_gap_replay(self) -> None:
        approval = ApprovalResumeRequest(
            **_binding(),
            kind=ApprovalKind.EXECUTION,
            request_digest=_digest("b"),
            approval_event_id="approval-1",
            decision=ApprovalDecision.DENY,
        )
        self.assertTrue(
            approval.runtime_replay_is_evidenced(
                {
                    "execution_approval_receipt_digest": (
                        approval.to_command().receipt_digest
                    )
                }
            )
        )
        self.assertFalse(approval.runtime_replay_is_evidenced({}))

        provider = ProviderResultRequest(
            **_binding(),
            provider_attempt_digest=_digest("b"),
            idempotency_key_digest=_digest("c"),
            authority_snapshot_digest=_digest("d"),
            producer_id="external_openai.worker",
            disposition=ProviderDisposition.COMPLETED,
            result_digest=_digest("e"),
            candidate_sha="1" * 40,
        )
        provider_receipt = provider.runtime_replay_is_evidenced
        self.assertFalse(provider_receipt({"provider_result_receipt_digest": _digest("f")}))

        self.assertTrue(
            CancellationRequest(**_binding()).runtime_replay_is_evidenced(
                {"terminal_classification": "CANCELLED"}
            )
        )
        self.assertTrue(RecoveryRequest(**_binding()).runtime_replay_is_evidenced({}))


class PrimaryCommandResponseTests(unittest.TestCase):
    def test_outcome_adapter_is_closed_and_task_bound(self) -> None:
        request = RecoveryRequest(**_binding())
        outcome = PrimaryOutcome(
            task_id="task-1",
            run_status=WorkflowRunStatus.TERMINAL,
            phase="terminal_classification",
            terminal_classification=TerminalClassification.SUCCEEDED,
            reason_code="completed",
            pending_approval=None,
            provider_dispatch_status=ProviderDispatchStatus.COMPLETED,
            effect_status=EffectStatus.NOT_REQUIRED,
            state_digest=_digest("f"),
            visit_count=5,
            visited_nodes=("authenticate_scope",),
            revision_count=0,
            budget_used_units=1,
        )
        response = PrimaryCommandResponse.from_outcome(
            request=request,
            operation=PrimaryCommandOperation.RECOVERY,
            outcome=outcome,
        )
        self.assertEqual(response.task_id, "task-1")
        self.assertEqual(response.run_status, "TERMINAL")
        self.assertEqual(response.terminal_classification, "SUCCEEDED")
        self.assertEqual(response.command_digest, request.command_digest)

        stale = replace(outcome, task_id="task-2")
        with self.assertRaisesRegex(PrimaryCommandBindingError, "another task"):
            PrimaryCommandResponse.from_outcome(
                request=request,
                operation=PrimaryCommandOperation.RECOVERY,
                outcome=stale,
            )

    def test_projection_adapter_exposes_only_bounded_status(self) -> None:
        request = CancellationRequest(**_binding())
        projection = SimpleNamespace(
            task_id="task-1",
            projection_version=4,
            projection_digest=_digest("b"),
            intake_fencing_generation=7,
            projection={
                "run_status": "TERMINAL",
                "current_phase": "terminal_classification",
                "transition_reason_code": "cancelled",
                "terminal_classification": "CANCELLED",
                "secret": "must-not-be-projected",
            },
        )
        response = PrimaryCommandResponse.from_projection(
            request=request,
            operation=PrimaryCommandOperation.CANCELLATION,
            projection=projection,
            fencing_generation=7,
        )
        self.assertEqual(response.projection_version, 4)
        self.assertEqual(response.projection_digest, _digest("b"))
        self.assertEqual(response.fencing_generation, 7)
        self.assertEqual(response.terminal_classification, "CANCELLED")
        self.assertNotIn("secret", response.model_dump())

    def test_receipt_adapter_returns_saved_identity_after_projection_advance(self) -> None:
        request = CancellationRequest(**_binding())
        receipt = SimpleNamespace(
            task_id="task-1",
            command_digest=request.command_digest,
            resulting_projection_version=4,
            resulting_projection_digest=_digest("b"),
            writer_fencing_generation=7,
        )
        response = PrimaryCommandResponse.from_receipt(
            request=request,
            operation=PrimaryCommandOperation.CANCELLATION,
            receipt=receipt,
        )
        self.assertEqual(response.run_status, "ALREADY_APPLIED")
        self.assertEqual(response.projection_version, 4)
        self.assertEqual(response.projection_digest, _digest("b"))
        self.assertEqual(response.fencing_generation, 7)

        changed = request.model_copy(update={"reason_code": "changed"})
        with self.assertRaisesRegex(PrimaryCommandBindingError, "different content"):
            PrimaryCommandResponse.from_receipt(
                request=changed,
                operation=PrimaryCommandOperation.CANCELLATION,
                receipt=receipt,
            )


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:23 UTC | END]]
