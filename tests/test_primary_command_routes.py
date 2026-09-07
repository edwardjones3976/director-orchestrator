# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:23 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from director_orchestrator import main
from director_orchestrator.langgraph_primary import (
    ProviderDisposition,
)
from director_orchestrator.primary_commands import ProviderResultRequest


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _runtime_digest(request: ProviderResultRequest) -> str:
    encoded = json.dumps(
        request.to_command().as_resume_payload(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _request(**updates: object) -> ProviderResultRequest:
    payload: dict[str, object] = {
        "task_id": "task-1",
        "command_id": "provider-result-1",
        "actor_id": "external_openai.worker",
        "expected_projection_digest": _digest("a"),
        "expected_fencing_generation": 7,
        "provider_attempt_digest": _digest("b"),
        "idempotency_key_digest": _digest("c"),
        "authority_snapshot_digest": _digest("d"),
        "producer_id": "external_openai.worker",
        "assignment_id": "assignment-1",
        "provider_id": "external_openai.worker",
        "provider_run_id": "provider-run-1",
        "disposition": ProviderDisposition.COMPLETED,
        "result_digest": _digest("e"),
        "candidate_sha": "1" * 40,
    }
    payload.update(updates)
    return ProviderResultRequest.model_validate(payload)


def _projection(*, digest: str = _digest("a"), payload: dict | None = None):
    return SimpleNamespace(
        task_id="task-1",
        projection_version=3,
        projection_digest=digest,
        projection={
            "run_status": "TERMINAL",
            "current_phase": "terminal_classification",
            "transition_reason_code": "completed",
            "terminal_classification": "SUCCEEDED",
            **(payload or {}),
        },
    )


class PrimaryCommandRouteReceiptTests(unittest.TestCase):
    def _activation(self, *, projection, receipt=None, generation: int = 7):
        coordinator = MagicMock()
        coordinator.get_projection.return_value = projection
        coordinator.get_task_writer.return_value = SimpleNamespace(
            fencing_generation=generation
        )
        coordinator.read_command_receipt.return_value = receipt
        application = MagicMock()
        return SimpleNamespace(
            coordinator=coordinator,
            application=application,
        )

    def test_exact_receipt_replay_precedes_advanced_projection_and_writer(self) -> None:
        request = _request()
        receipt = SimpleNamespace(
            task_id="task-1",
            command_digest=request.command_digest,
            resulting_projection_digest=_digest("f"),
            resulting_projection_version=4,
            writer_fencing_generation=7,
        )
        activation = self._activation(
            projection=_projection(digest=_digest("9")),
            receipt=receipt,
            generation=9,
        )
        activation.coordinator.get_projection.return_value = None
        activation.coordinator.get_task_writer.return_value = None
        with patch.object(main, "primary_default_activation", activation):
            response = main.primary_provider_result("task-1", request)

        self.assertEqual(response.run_status, "ALREADY_APPLIED")
        self.assertEqual(response.projection_digest, _digest("f"))
        activation.application.record_provider_callback.assert_not_called()
        activation.coordinator.record_command_receipt.assert_not_called()

    def test_changed_content_under_existing_command_identity_is_conflict(self) -> None:
        original = _request()
        changed = _request(result_digest=_digest("f"))
        receipt = SimpleNamespace(
            task_id="task-1",
            command_digest=original.command_digest,
            resulting_projection_digest=_digest("9"),
            resulting_projection_version=4,
            writer_fencing_generation=7,
        )
        activation = self._activation(projection=_projection(), receipt=receipt)
        with patch.object(main, "primary_default_activation", activation):
            with self.assertRaises(HTTPException) as caught:
                main.primary_provider_result("task-1", changed)

        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("different content", caught.exception.detail)
        activation.application.record_provider_callback.assert_not_called()

    def test_success_records_receipt_under_resulting_projection(self) -> None:
        request = _request()
        resulting = _projection(digest=_digest("f"))
        activation = self._activation(projection=_projection())
        activation.coordinator.get_projection.side_effect = [
            _projection(),
            resulting,
            resulting,
        ]
        with patch.object(main, "primary_default_activation", activation):
            response = main.primary_provider_result("task-1", request)

        activation.application.record_provider_callback.assert_called_once_with(
            task_id="task-1",
            assignment_id="assignment-1",
            provider_id="external_openai.worker",
            provider_run_id="provider-run-1",
            command=request.to_command(),
        )
        activation.coordinator.record_command_receipt.assert_called_once_with(
            task_id="task-1",
            operation="provider_result",
            command_id="provider-result-1",
            command_digest=request.command_digest,
            projection=resulting,
        )
        self.assertEqual(response.projection_digest, _digest("f"))

    def test_receipt_write_crash_gap_converges_only_with_runtime_evidence(self) -> None:
        request = _request()
        advanced = _projection(
            digest=_digest("f"),
            payload={"provider_result_receipt_digest": _runtime_digest(request)},
        )
        activation = self._activation(projection=advanced)
        with patch.object(main, "primary_default_activation", activation):
            response = main.primary_provider_result("task-1", request)

        activation.application.record_provider_callback.assert_called_once_with(
            task_id="task-1",
            assignment_id="assignment-1",
            provider_id="external_openai.worker",
            provider_run_id="provider-run-1",
            command=request.to_command(),
        )
        activation.coordinator.record_command_receipt.assert_called_once()
        self.assertEqual(response.projection_digest, _digest("f"))

        unevidenced = self._activation(
            projection=_projection(digest=_digest("9"))
        )
        with patch.object(main, "primary_default_activation", unevidenced):
            with self.assertRaises(HTTPException) as caught:
                main.primary_provider_result("task-1", request)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("stale task projection", caught.exception.detail)
        unevidenced.application.record_provider_callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:00 UTC | END]]
