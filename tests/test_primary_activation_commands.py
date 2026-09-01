# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 01:02 UTC | BEGIN]]
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from director_orchestrator.primary_activation_commands import (
    ProviderActivationAdvanceProof,
    ProviderActivationAdvanceProofError,
    ProviderActivationAdvanceRequest,
    build_provider_activation_advance_proof,
    verify_provider_activation_advance_proof,
)


class ProviderActivationAdvanceRequestTests(unittest.TestCase):
    secret = "gateway-proof-secret-that-is-distinct-32"

    def command(self, **changes):
        values = {
            "actor_id": "UOWNER",
            "binding_version": "activation-v1",
            "surface": "app",
            "stage": "INSTALLED",
            "expected_revision": 0,
            "evidence_id": "cursor-app-install-1",
            "evidence_digest": "a" * 64,
            "observed_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "expires_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
            "gateway_proof": ProviderActivationAdvanceProof(
                issued_at=1_788_220_800,
                nonce="N" * 32,
                signature="0" * 64,
            ),
        }
        values.update(changes)
        return ProviderActivationAdvanceRequest(**values)

    def signed(self, **changes):
        command = self.command(**changes)
        proof = build_provider_activation_advance_proof(
            self.secret,
            "public-bearer-secret",
            "openai",
            command,
            issued_at=1_788_220_800,
            nonce="R" * 32,
        )
        return command.model_copy(update={"gateway_proof": proof})

    def test_command_is_closed_and_digest_only(self):
        command = self.command()
        self.assertEqual("app", command.surface.value)
        with self.assertRaises(ValidationError):
            ProviderActivationAdvanceRequest(
                **command.model_dump(), token="must-never-enter-evidence"
            )

    def test_malformed_digest_and_unbounded_identity_fail(self):
        with self.assertRaises(ValidationError):
            self.command(
                surface="runtime_adapter",
                stage="AUTHENTICATED",
                evidence_id="x" * 129,
                evidence_digest="not-a-digest",
            )

    def test_signed_proof_binds_provider_state_revision_evidence_version_and_actor(self):
        command = self.signed()
        authority = verify_provider_activation_advance_proof(
            self.secret,
            "public-bearer-secret",
            "openai",
            command,
            now=1_788_220_800,
        )
        self.assertEqual("UOWNER", authority.actor_id)
        self.assertRegex(authority.nonce_digest, r"^[0-9a-f]{64}$")
        for label, changed_provider, changed in (
            ("provider", "claude", {}),
            (
                "surface",
                "openai",
                {"surface": command.surface.__class__.RUNTIME_ADAPTER},
            ),
            ("state", "openai", {"stage": command.stage.__class__.AUTHENTICATED}),
            ("revision", "openai", {"expected_revision": 1}),
            ("evidence", "openai", {"evidence_digest": "b" * 64}),
            ("version", "openai", {"binding_version": "activation-v2"}),
            ("actor", "openai", {"actor_id": "UOTHER"}),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                ProviderActivationAdvanceProofError, "signature"
            ):
                verify_provider_activation_advance_proof(
                    self.secret,
                    "public-bearer-secret",
                    changed_provider,
                    command.model_copy(update=changed),
                    now=1_788_220_800,
                )

    def test_proof_rejects_stale_and_shared_gateway_secret(self):
        command = self.signed()
        with self.assertRaisesRegex(ProviderActivationAdvanceProofError, "expired"):
            verify_provider_activation_advance_proof(
                self.secret,
                "public-bearer-secret",
                "openai",
                command,
                now=1_788_221_101,
            )
        with self.assertRaisesRegex(ProviderActivationAdvanceProofError, "distinct"):
            verify_provider_activation_advance_proof(
                self.secret, self.secret, "openai", command, now=1_788_220_800
            )


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:36 UTC | END]]
