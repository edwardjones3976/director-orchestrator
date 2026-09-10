# [[Grok Bot | unreported | 2026-09-10 | 16:00 UTC | BEGIN]]
"""Functional and refusal tests for the collect-only v3 independent-review issuer."""
from __future__ import annotations

import base64
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from director_orchestrator import provider_acceptance_v2 as v2
from director_orchestrator import provider_acceptance_v3 as v3
from director_orchestrator.native_v3_review_issuer import (
    PROTECTED_CAPTURE_VERSION,
    Config,
    IssuerError,
    collect,
    digest,
    expected_product,
)
from director_orchestrator.primary_activation_evidence import ActivationIdentity


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def signed(key: Ed25519PrivateKey, domain: bytes, document: dict) -> dict:
    return {
        "document": document,
        "signature": b64(key.sign(domain + v2.canonical(document))),
    }


class NativeV3ReviewIssuerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = int(time.time())
        self.signer = Ed25519PrivateKey.generate()
        self.observer = Ed25519PrivateKey.generate()
        self.owner = Ed25519PrivateKey.generate()
        self.wrong_observer = Ed25519PrivateKey.generate()
        self.public = self.signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.private = self.signer.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        self.observer_public = self.observer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.owner_public = self.owner.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.identity = ActivationIdentity(
            candidate_commit="a" * 40,
            candidate_tree="b" * 40,
            profile_id="profile",
            compose_project="compose",
            database_identity="postgres:sha256:" + "c" * 64,
            database_instance_id="12345678-1234-1234-1234-123456789abc",
            postgres_schema="primary",
            checkpoint_namespace="checkpoint",
        )
        self.challenge = "e" * 64
        self.packet = "d" * 64
        self.context = v2.ReviewContext(
            self.identity,
            "edwardjones3976/director-orchestrator",
            170,
            self.packet,
            self.challenge,
            self.now,
            self.now + 3600,
            frozenset({"claude"}),
        )
        for name in ("captures", "observations", "public", "authorizations", "broker"):
            (self.root / name).mkdir()
        (self.root / "broker" / "private.key").write_bytes(self.private)
        # claims db created on first claim()
        self.fixture_impl = "9" * 64
        self.impl_patch = patch(
            "director_orchestrator.provider_acceptance_v3.implementation_digest",
            return_value=self.fixture_impl,
        )
        self.impl_patch.start()
        self.addCleanup(self.impl_patch.stop)
        self.provider = "grok"
        self.product = "grok-bot"
        self.session = "native-session-1"
        self._write_registry()
        self._write_authorization()
        self._write_capture()
        self.cfg_path = self.root / "issuer.json"
        self._write_config()
        self.env = patch.dict(os.environ, {"LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": v3.MODE})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _authority(self, role, provider, key, **extra):
        pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        fields = dict(
            role=role,
            provider=provider,
            principal=f"{role}-principal",
            deployment=f"{role}-deployment",
            public_key=b64(pub),
            required_ci_checks=[],
            ci_workflow_ref="",
            ci_workflow_commit="",
        )
        fields.update(extra)
        return fields

    def _write_registry(self):
        # Minimal enrolled registry; CI authority present for validate_adoption isolation checks.
        ci_key = Ed25519PrivateKey.generate()
        sec_key = Ed25519PrivateKey.generate()
        authorities = {
            "security": self._authority("security", "openai", sec_key),
            "independent_review": self._authority(
                "independent_review", self.provider, self.signer,
                principal="review-principal", deployment="review-deployment",
            ),
            "ci": self._authority(
                "ci", "github-actions", ci_key,
                required_ci_checks=["fixture-check"],
                ci_workflow_ref="owner/repo/.github/workflows/ci.yml@refs/heads/main",
                ci_workflow_commit="1" * 40,
            ),
        }
        policy = dict(
            version=v3.REGISTRY_VERSION,
            activation=asdict(self.identity),
            repository=self.context.repository,
            pull_request_number=self.context.pull_request_number,
            packet_digest=self.packet,
            material_author_providers=sorted(self.context.material_author_providers),
            authorities=authorities,
            revoked_deployments=[],
            ci_oidc_policy={"required_ci_checks": ["fixture-check"], "audience_prefix": "fixture"},
            owner_authority=dict(
                principal="owner-principal",
                deployment="owner-deployment",
                public_key=b64(self.owner_public),
            ),
            observation_authority=dict(
                principal="observer-principal",
                deployment="observer-deployment",
                public_key=b64(self.observer_public),
            ),
        )
        registry_digest = hashlib.sha256(
            v2.canonical({k: v for k, v in policy.items() if k not in {"owner_adoption", "revoked_deployments"}})
        ).hexdigest()
        adoption = dict(
            version=v3.ADOPTION_VERSION,
            decision="ADOPT_TWO_PARTY_V3",
            policy_commit="4" * 40,
            policy_tree="5" * 40,
            implementation_digest=self.fixture_impl,
            registry_digest=registry_digest,
            activation=asdict(self.identity),
            repository=self.context.repository,
            pull_request_number=self.context.pull_request_number,
            packet_digest=self.packet,
        )
        policy["owner_adoption"] = signed(self.owner, v3.ADOPTION_DOMAIN, adoption)
        self.registry_path = self.root / "registry.json"
        self.registry_path.write_bytes(v2.canonical(policy))
        self.registry_sha256 = digest(self.registry_path.read_bytes())
        self.policy = policy
        self.ci_key = ci_key
        self.sec_key = sec_key

    def _write_authorization(self):
        path = self.root / "authorizations" / f"{self.challenge}.json"
        path.write_bytes(v2.canonical({"base": "0" * 40, "context": self.context.document()}))

    def _capture_document(self, **overrides):
        doc = {
            "version": PROTECTED_CAPTURE_VERSION,
            "verdict": "ACCEPT",
            "challenge": self.challenge,
            "packet_digest": self.packet,
            "candidate_commit": self.identity.candidate_commit,
            "candidate_tree": self.identity.candidate_tree,
            "session_id": self.session,
            "provider_family": self.provider,
            "product": self.product,
        }
        doc.update(overrides)
        return doc

    def _write_capture(self, document=None):
        path = self.root / "captures" / f"{self.challenge}.json"
        raw = v2.canonical(document or self._capture_document())
        path.write_bytes(raw)
        self.capture_digest = digest(raw)
        return raw

    def _observation_document(self, **overrides):
        doc = {
            "version": v3.OBSERVATION_VERSION,
            "context": self.context.document(),
            "product": self.product,
            "provider_family": self.provider,
            "served_model": "fixture-observed-model",
            "session_id": self.session,
            "transport": "native-product-capture",
            "authentication": "native-account-observed",
            "capture_digest": self.capture_digest,
            "installation_digest": "1" * 64,
            "issued_at": self.now,
            "expires_at": self.now + 3600,
        }
        doc.update(overrides)
        return doc

    def _write_observation(self, document=None, key=None):
        path = self.root / "observations" / f"{self.challenge}.json"
        envelope = signed(key or self.observer, v3.OBSERVATION_DOMAIN, document or self._observation_document())
        path.write_bytes(v2.canonical(envelope))
        return envelope

    def _write_config(self, **overrides):
        doc = {
            "root": str(self.root),
            "role": "independent_review",
            "provider": self.provider,
            "principal": "review-principal",
            "deployment": "review-deployment",
            "public_key": b64(self.public),
            "observation_public_key": b64(self.observer_public),
            "registry_path": str(self.registry_path),
            "registry_sha256": self.registry_sha256,
        }
        doc.update(overrides)
        raw = v2.canonical(doc)
        self.cfg_path.write_bytes(raw)
        self.cfg_digest = digest(raw)
        self.cfg = Config.load(self.cfg_path, self.cfg_digest)

    def collect(self, **kwargs):
        return collect(self.cfg, challenge=kwargs.get("challenge", self.challenge), now=kwargs.get("now", self.now))

    def test_collect_publishes_receipt_accepted_by_complete_v3_verifier(self):
        self._write_observation()
        result = self.collect()
        self.assertEqual(result["version"], v3.VERSION)
        receipt = dict(result["receipt"])
        signature = base64.b64decode(receipt.pop("signature"))
        Ed25519PublicKey.from_public_bytes(self.public).verify(
            signature,
            v3.DOMAIN
            + v2.canonical(dict(version=v3.VERSION, context=self.context.document(), receipt=receipt)),
        )
        self.assertEqual(receipt["verdict"], "ACCEPT")
        self.assertEqual(receipt["session_id"], self.session)
        self.assertEqual(receipt["transport_evidence_digest"], self.capture_digest)
        # Complete existing verifier with synthetic security + CI around this receipt.
        authorities = {
            role: v2.IssuerAuthority(
                role=fields["role"],
                provider=fields["provider"],
                principal=fields["principal"],
                deployment=fields["deployment"],
                public_key=base64.b64decode(fields["public_key"]),
                required_ci_checks=frozenset(fields.get("required_ci_checks") or ()),
                ci_workflow_ref=fields.get("ci_workflow_ref") or "",
                ci_workflow_commit=fields.get("ci_workflow_commit") or "",
            )
            for role, fields in self.policy["authorities"].items()
        }
        # Expand CI required checks to production set expected by verifier.
        from director_orchestrator.acceptance_journal import REQUIRED_ACTIVATION_CHECKS
        authorities["ci"] = replace(
            authorities["ci"],
            required_ci_checks=frozenset(REQUIRED_ACTIVATION_CHECKS),
        )
        self.policy["authorities"]["ci"]["required_ci_checks"] = sorted(REQUIRED_ACTIVATION_CHECKS)
        self.policy["ci_oidc_policy"]["required_ci_checks"] = sorted(REQUIRED_ACTIVATION_CHECKS)
        # Re-adopt after CI policy field change.
        registry_digest = hashlib.sha256(
            v2.canonical({k: v for k, v in self.policy.items() if k not in {"owner_adoption", "revoked_deployments"}})
        ).hexdigest()
        adoption = self.policy["owner_adoption"]["document"]
        adoption = dict(adoption, registry_digest=registry_digest)
        self.policy["owner_adoption"] = signed(self.owner, v3.ADOPTION_DOMAIN, adoption)
        package = dict(version=v3.VERSION, context=self.context.document(), receipts={})
        package["receipts"]["independent_review"] = result["receipt"]
        # security
        sec_auth = authorities["security"]
        sec_obs = dict(
            version=v3.OBSERVATION_VERSION,
            context=self.context.document(),
            product="codex-security",
            provider_family="openai",
            served_model="fixture-security-model",
            session_id="security-session",
            transport="codex-security-product",
            authentication="native-account-observed",
            capture_digest="f" * 64,
            installation_digest="1" * 64,
            issued_at=self.now,
            expires_at=self.now + 3600,
        )
        sec_receipt = dict(
            role="security",
            provider=sec_auth.provider,
            principal=sec_auth.principal,
            deployment=sec_auth.deployment,
            session_id="security-session",
            transport_evidence_digest="f" * 64,
            verdict="ANALYZED",
            ci_checks=[],
            ci_workflow_ref="",
            ci_workflow_commit="",
            native_observation=signed(self.observer, v3.OBSERVATION_DOMAIN, sec_obs),
            analysis=dict(
                scan_id="fixture-scan",
                manifest_digest="2" * 64,
                report_digest="3" * 64,
                coverage_complete=True,
                unresolved_findings=0,
                author_family_analysis=False,
            ),
        )
        package["receipts"]["security"] = sec_receipt
        ci_auth = authorities["ci"]
        ci_receipt = dict(
            role="ci",
            provider=ci_auth.provider,
            principal=ci_auth.principal,
            deployment=ci_auth.deployment,
            session_id="github-actions:1:1",
            transport_evidence_digest="a" * 64,
            verdict="SUCCESS",
            ci_checks=[
                dict(name=name, run_id=10, status="COMPLETED", conclusion="SUCCESS")
                for name in sorted(ci_auth.required_ci_checks)
            ],
            ci_workflow_ref=ci_auth.ci_workflow_ref,
            ci_workflow_commit=ci_auth.ci_workflow_commit,
        )
        package["receipts"]["ci"] = ci_receipt
        for role, key in (
            ("security", self.sec_key),
            ("independent_review", self.signer),
            ("ci", self.ci_key),
        ):
            receipt = package["receipts"][role]
            unsigned = {k: v for k, v in receipt.items() if k != "signature"}
            receipt["signature"] = b64(
                key.sign(v3.DOMAIN + v2.canonical(dict(version=v3.VERSION, context=package["context"], receipt=unsigned)))
            )
        verified = v3.verify_two_party_acceptance(
            v2.canonical(package),
            expected=self.context,
            authorities=authorities,
            now=self.now,
            policy=self.policy,
        )
        self.assertEqual(verified.challenge, self.challenge)

    def test_rejected_or_malformed_terminal_capture_refuses(self):
        self._write_observation()
        for verdict in ("CHANGES_REQUIRED", "UNAVAILABLE", "REJECT"):
            with self.subTest(verdict=verdict):
                # Reset claim db by using fresh root files; claim may already exist from prior subtests
                claims = self.root / "broker" / "claims.sqlite3"
                if claims.exists():
                    claims.unlink()
                public = self.root / "public" / f"{self.challenge}.json"
                if public.exists():
                    public.unlink()
                self._write_capture(self._capture_document(verdict=verdict))
                self._write_observation()
                with self.assertRaisesRegex(IssuerError, "successful ACCEPT|terminal review"):
                    self.collect()
        claims = self.root / "broker" / "claims.sqlite3"
        if claims.exists():
            claims.unlink()
        public = self.root / "public" / f"{self.challenge}.json"
        if public.exists():
            public.unlink()
        self._write_capture(self._capture_document())
        # Missing required capture field
        bad = self._capture_document()
        del bad["verdict"]
        (self.root / "captures" / f"{self.challenge}.json").write_bytes(v2.canonical(bad))
        self.capture_digest = digest((self.root / "captures" / f"{self.challenge}.json").read_bytes())
        self._write_observation()
        with self.assertRaisesRegex(IssuerError, "protected capture"):
            self.collect()

    def test_mismatched_session_context_or_candidate_refuses(self):
        # Observation session differs from protected capture session.
        self._write_capture()
        self._write_observation(self._observation_document(session_id="other-session"))
        with self.assertRaisesRegex(IssuerError, "session"):
            self.collect()
        # Capture candidate mismatch.
        claims = self.root / "broker" / "claims.sqlite3"
        if claims.exists():
            claims.unlink()
        self._write_capture(self._capture_document(candidate_commit="f" * 40))
        self._write_observation()
        with self.assertRaisesRegex(IssuerError, "candidate commit"):
            self.collect()
        if claims.exists():
            claims.unlink()
        self._write_capture()
        # Observation context challenge drift.
        bad_context = replace(self.context, challenge="f" * 64)
        self._write_observation(self._observation_document(context=bad_context.document()))
        with self.assertRaisesRegex(IssuerError, "context"):
            self.collect()

    def test_opaque_capture_cannot_force_accept_without_terminal_contract(self):
        claims = self.root / "broker" / "claims.sqlite3"
        if claims.exists():
            claims.unlink()
        # Synthetic opaque capture that previously fooled the collector.
        opaque = v2.canonical({"verdict": "CHANGES_REQUIRED", "session_id": "actual-capture-session"})
        path = self.root / "captures" / f"{self.challenge}.json"
        path.write_bytes(opaque)
        self.capture_digest = digest(opaque)
        self._write_observation(self._observation_document(session_id="observed-different-session"))
        with self.assertRaises(IssuerError):
            self.collect()

    def test_unprotected_custody_and_invalid_adoption_refuse(self):
        with patch.dict(os.environ, {"LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": "provider-v2"}):
            with self.assertRaisesRegex(IssuerError, "provider-v3-two-party"):
                Config.load(self.cfg_path, self.cfg_digest)
        # Wrong registry pin.
        with self.assertRaisesRegex(IssuerError, "registry digest"):
            bad = json.loads(self.cfg_path.read_text())
            bad["registry_sha256"] = "0" * 64
            raw = v2.canonical(bad)
            self.cfg_path.write_bytes(raw)
            Config.load(self.cfg_path, digest(raw))
        self._write_config()
        # Caller JSON carrying private_key refused.
        with self.assertRaisesRegex(IssuerError, "private key"):
            doc = json.loads(self.cfg_path.read_bytes())
            # reload canonical dict
            doc = {
                "root": str(self.root),
                "role": "independent_review",
                "provider": self.provider,
                "principal": "review-principal",
                "deployment": "review-deployment",
                "public_key": b64(self.public),
                "private_key": b64(self.private),
                "observation_public_key": b64(self.observer_public),
                "registry_path": str(self.registry_path),
                "registry_sha256": self.registry_sha256,
            }
            raw = v2.canonical(doc)
            path = self.root / "bad-issuer.json"
            path.write_bytes(raw)
            Config.load(path, digest(raw))

    def test_wrong_observer_key_refuses(self):
        self._write_observation(key=self.wrong_observer)
        with self.assertRaisesRegex(IssuerError, "observation signature"):
            self.collect()

    def test_expired_evidence_refuses(self):
        self._write_observation(self._observation_document(issued_at=self.now - 10, expires_at=self.now - 1))
        with self.assertRaisesRegex(IssuerError, "expired"):
            self.collect()

    def test_author_family_reviewer_refuses(self):
        authored = replace(self.context, material_author_providers=frozenset({"claude", "grok"}))
        self.context = authored
        self._write_authorization()
        self._write_capture()
        self._write_observation(self._observation_document(context=authored.document()))
        with self.assertRaisesRegex(IssuerError, "material author"):
            self.collect()

    def test_unqualified_alias_only_model_refuses(self):
        for alias in ("grok-bot-default", "default", "unknown", "auto"):
            with self.subTest(alias=alias):
                claims = self.root / "broker" / "claims.sqlite3"
                if claims.exists():
                    claims.unlink()
                public = self.root / "public" / f"{self.challenge}.json"
                if public.exists():
                    public.unlink()
                self._write_capture()
                self._write_observation(self._observation_document(served_model=alias))
                with self.assertRaisesRegex(IssuerError, "unqualified alias-only"):
                    self.collect()

    def test_repeated_publication_and_alternate_filename_refuse(self):
        self._write_observation()
        self.collect()
        with self.assertRaisesRegex(IssuerError, "claimed|write-once|replay"):
            self.collect()
        # Even if public file were removed, durable claim still refuses.
        public = self.root / "public" / f"{self.challenge}.json"
        public.unlink()
        with self.assertRaisesRegex(IssuerError, "claimed|write-once|replay"):
            self.collect()

    def test_wrong_mode_refuses_before_secret_access(self):
        self._write_observation()
        with patch.dict(os.environ, {"LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": "provider-v2"}):
            with self.assertRaisesRegex(IssuerError, "provider-v3-two-party"):
                self.collect()

    def test_codex_family_product_path(self):
        # Enrolled openai/codex reviewer of non-openai authored work.
        self.provider = "openai"
        self.product = "codex"
        self.signer = Ed25519PrivateKey.generate()
        self.public = self.signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.private = self.signer.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        (self.root / "broker" / "private.key").write_bytes(self.private)
        claims = self.root / "broker" / "claims.sqlite3"
        if claims.exists():
            claims.unlink()
        self.context = replace(self.context, material_author_providers=frozenset({"grok"}))
        self._write_registry()
        self._write_authorization()
        self._write_capture()
        self._write_observation()
        self._write_config()
        result = self.collect()
        self.assertEqual(result["receipt"]["provider"], "openai")
        self.assertEqual(result["receipt"]["native_observation"]["document"]["product"], "codex")
        self.assertEqual(expected_product("openai"), "codex")


if __name__ == "__main__":
    unittest.main()
# [[Grok Bot | unreported | 2026-09-10 | 16:00 UTC | END]]
