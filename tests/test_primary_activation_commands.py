# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 01:02 UTC | BEGIN]]
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from director_orchestrator.primary_activation_commands import (
    ProviderActivationAdvanceRequest,
)


class ProviderActivationAdvanceRequestTests(unittest.TestCase):
    def test_command_is_closed_and_digest_only(self):
        command = ProviderActivationAdvanceRequest(
            actor_id="UOWNER",
            surface="app",
            stage="INSTALLED",
            evidence_id="cursor-app-install-1",
            evidence_digest="a" * 64,
            observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            expires_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        self.assertEqual("app", command.surface.value)
        with self.assertRaises(ValidationError):
            ProviderActivationAdvanceRequest(
                **command.model_dump(), token="must-never-enter-evidence"
            )

    def test_malformed_digest_and_unbounded_identity_fail(self):
        with self.assertRaises(ValidationError):
            ProviderActivationAdvanceRequest(
                actor_id="UOWNER",
                surface="runtime_adapter",
                stage="AUTHENTICATED",
                evidence_id="x" * 129,
                evidence_digest="not-a-digest",
                observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 01:02 UTC | END]]
