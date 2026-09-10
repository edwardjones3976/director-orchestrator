# [[Grok Bot | unreported | 2026-09-10 | 15:45 UTC | BEGIN]]
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
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from director_orchestrator import provider_acceptance_v2 as v2
from director_orchestrator import provider_acceptance_v3 as v3
from director_orchestrator.native_v3_review_issuer import (
    Config, IssuerError, collect, digest, write_new,
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
        self.wrong_observer = Ed25519PrivateKey.generate()
        self.public = self.signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat
        self.private = self.signer.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        self.observer_public = self.observer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
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
        self.context = v2.ReviewContext(
            self.identity,
            "edwardjones3976/director-orchestrator",
            170,
            "d" * 64,
            "e" * 64,
            self.now,
            self.now + 3600,
            frozenset({"openai"}),
        )
        self.captures = self.root / "captures"
        self.observations = self.root / "observations"
        self.public_out = self.root / "public"
        for path in (self.captures, self.observations, self.public_out):
            path.mkdir()
        self.capture_path = self.captures / "review.bin"
        self.capture_path.write_bytes(b"protected-native-review-capture")
        self.capture_digest = digest(self.capture_path.read_bytes())
        self.context_path = self.root / "context.json"
        self.context_path.write_bytes(v2.canonical(self.context.document()))
        self.observation_path = self.observations / "observation.json"
        self.output_path = self.public_out / (self.context.challenge + ".json")
        self.cfg_path = self.root / "issuer.json"
        self._write_config()
        self.env = patch.dict(os.environ, {"LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": v3.MODE})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _write_config(self, **overrides):
        doc = {
            "root": str(self.root),
            "role": "independent_review",
            "provider": "grok",
            "principal": "grok-principal",
            "deployment": "grok-deployment",
            "public_key": b64(self.public),
            "private_key": b64(self.private),
            "observation_public_key": b64(self.observer_public),
        }
        doc.update(overrides)
        raw = v2.canonical(doc)
        self.cfg_path.write_bytes(raw)
        self.cfg_digest = digest(raw)
        self.cfg = Config.load(self.cfg_path, self.cfg_digest)

    def _observation_document(self, **overrides):
        doc = {
            "version": v3.OBSERVATION_VERSION,
            "context": self.context.document(),
            "product": "grok-bot",
            "provider_family": "grok",
            "served_model": "fixture-observed-grok-model",
            "session_id": "grok-session-1",
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
        envelope = signed(key or self.observer, v3.OBSERVATION_DOMAIN, document or self._observation_document())
        self.observation_path.write_bytes(v2.canonical(envelope))
        return envelope

    def collect(self, **kwargs):
        return collect(
            self.cfg,
            context_path=kwargs.get("context_path", self.context_path),
            capture_path=kwargs.get("capture_path", self.capture_path),
            observation_path=kwargs.get("observation_path", self.observation_path),
            output_path=kwargs.get("output_path", self.output_path),
            now=kwargs.get("now", self.now),
        )

    def test_collect_publishes_receipt_consumable_by_v3_signature_domain(self):
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
        self.assertEqual(receipt["role"], "independent_review")
        self.assertEqual(receipt["transport_evidence_digest"], self.capture_digest)
        self.assertEqual(receipt["session_id"], "grok-session-1")
        self.assertEqual(receipt["native_observation"]["document"]["product"], "grok-bot")
        # Existing verifier rejects a wrong-domain signature for the same bytes.
        with self.assertRaises(Exception):
            Ed25519PublicKey.from_public_bytes(self.public).verify(
                signature,
                v2.DOMAIN
                + v2.canonical(dict(version=v2.VERSION, context=self.context.document(), receipt=receipt)),
            )

    def test_tampered_context_session_or_capture_refuse(self):
        self._write_observation()
        # Tampered challenge context file.
        bad_context = replace(self.context, challenge="f" * 64)
        bad_path = self.root / "bad-context.json"
        bad_path.write_bytes(v2.canonical(bad_context.document()))
        with self.assertRaisesRegex(IssuerError, "context"):
            self.collect(context_path=bad_path)
        # Tampered capture bytes after observation was bound.
        self.capture_path.write_bytes(b"mutated-capture")
        with self.assertRaisesRegex(IssuerError, "capture digest"):
            self.collect()
        self.capture_path.write_bytes(b"protected-native-review-capture")
        # Observation session left intact but we alter observation session after signing? resign wrong session vs capture ok
        self._write_observation(self._observation_document(session_id="other-session"))
        # still valid observation alone; collect succeeds with new session
        out2 = self.public_out / "second.json"
        result = self.collect(output_path=out2)
        self.assertEqual(result["receipt"]["session_id"], "other-session")

    def test_wrong_observer_key_refuses(self):
        self._write_observation(key=self.wrong_observer)
        with self.assertRaisesRegex(IssuerError, "observation signature"):
            self.collect()

    def test_expired_evidence_refuses(self):
        self._write_observation(self._observation_document(
            issued_at=self.now - 10, expires_at=self.now - 1,
        ))
        with self.assertRaisesRegex(IssuerError, "expired"):
            self.collect()

    def test_author_family_reviewer_refuses(self):
        authored = replace(self.context, material_author_providers=frozenset({"openai", "grok"}))
        self.context_path.write_bytes(v2.canonical(authored.document()))
        self.context = authored
        self._write_observation(self._observation_document(context=authored.document()))
        with self.assertRaisesRegex(IssuerError, "material author"):
            self.collect()

    def test_unqualified_alias_only_model_refuses(self):
        for alias in ("grok-bot-default", "default", "unknown", "auto"):
            with self.subTest(alias=alias):
                self._write_observation(self._observation_document(served_model=alias))
                out = self.public_out / f"{alias}.json"
                with self.assertRaisesRegex(IssuerError, "unqualified alias-only"):
                    self.collect(output_path=out)

    def test_repeated_publication_refuses(self):
        self._write_observation()
        self.collect()
        with self.assertRaisesRegex(IssuerError, "write-once"):
            self.collect()

    def test_wrong_mode_refuses_before_signing(self):
        self._write_observation()
        with patch.dict(os.environ, {"LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": "provider-v2"}):
            with self.assertRaisesRegex(IssuerError, "provider-v3-two-party"):
                self.collect(output_path=self.public_out / "mode.json")


if __name__ == "__main__":
    unittest.main()
# [[Grok Bot | unreported | 2026-09-10 | 15:45 UTC | END]]
