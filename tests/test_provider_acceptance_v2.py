# [[OpenAI Codex | GPT-6 | 2026-09-06 | 08:24 UTC | BEGIN]]
"""Synthetic keys are test-only; no test evidence is usable for activation."""
import base64
import copy
from dataclasses import replace
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from director_orchestrator.primary_activation_evidence import ActivationIdentity, ActivationEvidenceStore, ActivationEvidenceError
from director_orchestrator.provider_acceptance_v2 import (
    DOMAIN, VERSION, IssuerAuthority, ReviewContext, ProviderAcceptanceError,
    canonical, verify_provider_acceptance,
)


class ProviderAcceptanceV2Tests(unittest.TestCase):
    def setUp(self):
        identity = ActivationIdentity("a"*40, "b"*40, "profile", "compose",
            "postgres:sha256:" + "c"*64, "12345678-1234-1234-1234-123456789abc", "primary", "checkpoint")
        self.context = ReviewContext(identity, "owner/repo", 100, "d"*64, "e"*64,
                                     1000, 1300, frozenset({"openai"}))
        self.keys = {role: Ed25519PrivateKey.generate() for role in ("security", "independent_review", "ci")}
        self.authorities = {role: IssuerAuthority(role, provider, role+"-principal", role+"-isolated-service",
            self.keys[role].public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            frozenset({"unit", "security"}) if role == "ci" else frozenset(),
            "owner/repo/.github/workflows/issuer.yml@refs/heads/main" if role == "ci" else "",
            "9"*40 if role == "ci" else "")
            for role, provider in (("security", "claude"), ("independent_review", "cursor"), ("ci", "github-actions"))}
        self.package = dict(version=VERSION, context=self.context.document(), receipts={})
        for role, authority in self.authorities.items():
            self.package["receipts"][role] = dict(role=role, provider=authority.provider,
                principal=authority.principal, deployment=authority.deployment, session_id=role+"-session",
                transport_evidence_digest="f"*64, ci_workflow_ref=authority.ci_workflow_ref,
                ci_workflow_commit=authority.ci_workflow_commit, verdict="SUCCESS" if role == "ci" else "ACCEPT",
                ci_checks=[dict(name=name, run_id=1, status="COMPLETED", conclusion="SUCCESS")
                           for name in sorted(authority.required_ci_checks)])
        self.sign()

    def sign(self):
        for role, receipt in self.package["receipts"].items():
            unsigned = {k:v for k,v in receipt.items() if k != "signature"}
            payload = canonical(dict(version=VERSION, context=self.package["context"], receipt=unsigned))
            receipt["signature"] = base64.b64encode(self.keys[role].sign(DOMAIN + payload)).decode()

    def verify(self, **kwargs):
        return verify_provider_acceptance(canonical(self.package), expected=kwargs.get("expected", self.context),
            authorities=kwargs.get("authorities", self.authorities), now=kwargs.get("now", 1100))

    def test_verified_output_is_immutable_and_does_not_claim_durable_acceptance(self):
        verified = self.verify()
        original = verified.canonical_package
        self.package["receipts"]["security"]["verdict"] = "FAILED"
        self.assertEqual(original, verified.canonical_package)
        self.assertEqual(self.context.challenge, verified.challenge)
        with self.assertRaises(ActivationEvidenceError):
            ActivationEvidenceStore._validate_acceptance_artifact_contract(json.loads(original))

    def test_bound_context_rejects_other_packet_revision_challenge_or_deployment(self):
        for context in (replace(self.context, packet_digest="1"*64),
                        replace(self.context, challenge="2"*64),
                        replace(self.context, activation=replace(self.context.activation, candidate_commit="3"*40)),
                        replace(self.context, activation=replace(self.context.activation, candidate_tree="4"*40)),
                        replace(self.context, activation=replace(self.context.activation, profile_id="other")),
                        replace(self.context, activation=replace(self.context.activation, database_instance_id="22345678-1234-1234-1234-123456789abc"))):
            with self.subTest(context=context), self.assertRaises(ProviderAcceptanceError):
                self.verify(expected=context)

    def test_expired_or_future_challenge_is_rejected(self):
        for now in (999, 1300, 2000):
            with self.subTest(now=now), self.assertRaises(ProviderAcceptanceError):
                self.verify(now=now)

    def test_tampering_cannot_relabel_principal_role_session_or_transport(self):
        saved = copy.deepcopy(self.package)
        for field, value in (("principal", "fake"), ("role", "ci"), ("provider", "openai"),
                             ("deployment", "same-user"), ("session_id", "fake-session"),
                             ("transport_evidence_digest", "1"*64)):
            self.package = copy.deepcopy(saved)
            self.package["receipts"]["security"][field] = value
            with self.subTest(field=field), self.assertRaises(ProviderAcceptanceError):
                self.verify()

    def test_registry_rejects_shared_authorities_and_material_authors(self):
        for field in ("public_key", "principal", "deployment"):
            authorities = dict(self.authorities)
            authorities["security"] = replace(authorities["security"], **{field: getattr(authorities["ci"], field)})
            with self.subTest(field=field), self.assertRaises(ProviderAcceptanceError):
                self.verify(authorities=authorities)
        for provider in ("openai", "cursor"):
            authorities = dict(self.authorities)
            authorities["security"] = replace(authorities["security"], provider=provider)
            with self.subTest(provider=provider), self.assertRaises(ProviderAcceptanceError):
                self.verify(authorities=authorities)

    def test_even_valid_signature_cannot_approve_failed_or_incomplete_ci(self):
        saved = copy.deepcopy(self.package)
        for field, value in (("status", "IN_PROGRESS"), ("conclusion", "FAILURE"), ("run_id", True), ("name", "untrusted")):
            self.package = copy.deepcopy(saved)
            self.package["receipts"]["ci"]["ci_checks"][0][field] = value
            self.sign()
            with self.subTest(field=field), self.assertRaises(ProviderAcceptanceError):
                self.verify()
        self.package = copy.deepcopy(saved)
        self.package["receipts"]["ci"]["ci_checks"].pop()
        self.sign()
        with self.assertRaises(ProviderAcceptanceError): self.verify()

    def test_prose_incomplete_and_unknown_evidence_fail_closed(self):
        for role in self.authorities:
            saved = self.package["receipts"].pop(role)
            with self.assertRaises(ProviderAcceptanceError): self.verify()
            self.package["receipts"][role] = saved
        self.package["operator_approved"] = True
        with self.assertRaises(ProviderAcceptanceError): self.verify()
        with self.assertRaises(ProviderAcceptanceError):
            verify_provider_acceptance(b'"ACCEPT"', expected=self.context, authorities=self.authorities, now=1100)

    def test_duplicate_fields_and_wrong_key_fail_closed(self):
        raw = canonical(self.package)
        with self.assertRaises(ProviderAcceptanceError):
            verify_provider_acceptance(b'{"version":"bad",'+raw[1:], expected=self.context, authorities=self.authorities, now=1100)
        authorities = dict(self.authorities)
        authorities["security"] = replace(authorities["security"], public_key=Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        with self.assertRaises(ProviderAcceptanceError): self.verify(authorities=authorities)

    def test_valid_signature_cannot_substitute_untrusted_workflow(self):
        self.package["receipts"]["ci"]["ci_workflow_commit"] = "8"*40
        self.sign()
        with self.assertRaises(ProviderAcceptanceError): self.verify()

    def test_nonfinite_and_malformed_receipts_fail_closed(self):
        saved = copy.deepcopy(self.package)
        for value in (None, [], 1, {"unexpected": True}):
            self.package = copy.deepcopy(saved)
            self.package["receipts"]["security"]["signature"] = value
            with self.subTest(value=value), self.assertRaises(ProviderAcceptanceError): self.verify()
        self.package = copy.deepcopy(saved)
        self.package["context"]["issued_at"] = float("nan")
        with self.assertRaises(ProviderAcceptanceError):
            verify_provider_acceptance(json.dumps(self.package).encode(), expected=self.context,
                                       authorities=self.authorities, now=1100)
        self.package = copy.deepcopy(saved)
        self.package["receipts"]["security"]["operator_approved"] = True
        with self.assertRaises(ProviderAcceptanceError): self.verify()

    def test_pure_verifier_does_not_consume_challenges(self):
        # This intentionally is not replay protection. A durable caller must reject
        # a second insertion, atomically with acceptance; readiness can reverify.
        self.assertEqual(self.verify(), self.verify())


if __name__ == "__main__": unittest.main()

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 08:24 UTC | END]]
