# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:49 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 15:16 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 13:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 11:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 10:29 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 09:18 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 07:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 06:39 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 05:07 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 04:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:36 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:16 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 05:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:16 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:46 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:37 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:18 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 20:48 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:00 UTC | BEGIN]]
"""In-image read-only scorecard evidence: capture attestations and consistency."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from director_orchestrator import primary_scorecard_evidence as evidence
from director_orchestrator.primary_activation_evidence import ActivationEvidenceError
from director_orchestrator.primary_provider_activation import PrimaryProviderActivationService
from director_orchestrator.provider_activation import (
    ProviderActivationBinding, ProviderActivationRegistry, ProviderActivationStage, ProviderActivationSurface,
)
from director_orchestrator.provider_validation import binding_digest

TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
PIN = "e" * 64
NOW = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)


def _load(name):
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"scorecard_evidence_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MemoryStore = _load("test_primary_provider_activation").MemoryStore


def binding(provider):
    return ProviderActivationBinding(provider_id=provider, workspace_id="default-director", provider_version="v1",
                                     app_version="v1", runtime_adapter_version="v1", workspace_version="v1",
                                     binding_version="v1")


def advance(store, provider_binding, surface, through=ProviderActivationStage.LIVE_OK):
    registry = ProviderActivationRegistry(store, provider_binding)
    stages = list(ProviderActivationStage)
    for index, stage in enumerate(stages[: stages.index(through) + 1], 1):
        registry.record(surface=surface, stage=stage, evidence_id=f"{provider_binding.provider_id}-{surface.value}-{index}",
                        evidence_digest=hashlib.sha256(f"{provider_binding.provider_id}:{surface.value}:{stage.value}".encode()).hexdigest(),
                        observed_at=NOW + timedelta(seconds=index), expires_at=NOW + timedelta(hours=1),
                        now=NOW + timedelta(seconds=index))


class ScorecardEvidenceUnitTests(unittest.TestCase):
    def test_explicit_capture_verifier_rejects_a_real_capture_with_invalid_signature(self):
        from dataclasses import asdict
        import time
        from director_orchestrator.packet_contracts import canonical_json_bytes
        from director_orchestrator.provider_validation import CaptureVerifier, make_capture, digest
        fixture = _load("test_provider_validation")
        expires = int(time.time()) + 3600
        admission = dict(reservation="e" * 64, nonce="n" * 64, fence=1,
                         generation=PIN, provider="claude", candidate=fixture.CANDIDATE, tree=fixture.TREE,
                         expires_at=expires, binding=asdict(fixture.binding()),
                         configuration_digest="d" * 64, packet=canonical_json_bytes(fixture.work_packet()))
        output = fixture.raw_native()
        capture = make_capture(admission, output, capture_key=fixture.KEY)
        document = asdict(capture)
        capture_digest = digest(canonical_json_bytes(document))
        reader = SimpleNamespace(dsn="fixture", load_capture=lambda _: (document, admission["packet"], output,
                                                          False, expires, "succeeded"))
        verifier = CaptureVerifier(reader, capture_key=fixture.KEY, candidate=fixture.CANDIDATE,
                                   tree=fixture.TREE, generation=PIN)
        self.assertTrue(verifier.verify(fixture.binding(), capture_digest))
        store = MemoryStore()
        observed = datetime.now(timezone.utc) - timedelta(minutes=1)
        registry = ProviderActivationRegistry(store, fixture.binding())
        for surface in ProviderActivationSurface:
            for index, stage in enumerate(ProviderActivationStage):
                registry.record(surface=surface, stage=stage, evidence_id=f"{surface.value}-{index}",
                    evidence_digest=capture_digest, observed_at=observed + timedelta(seconds=index),
                    expires_at=datetime.fromtimestamp(expires, timezone.utc),
                    now=observed + timedelta(seconds=index))
        settings = SimpleNamespace(langgraph_primary_postgres_dsn="fixture",
            langgraph_primary_allow_insecure_local_postgres=False,
            primary_provider_activation_bindings=lambda: {"claude": fixture.binding()},
            langgraph_primary_required_providers=("claude",))
        identity = SimpleNamespace(candidate_commit=fixture.CANDIDATE, candidate_tree=fixture.TREE,
            postgres_schema="director_primary_v1", database_identity="fixture", database_instance_id="fixture")

        @contextmanager
        def open_store(*args, **kwargs):
            yield SimpleNamespace(load=store.load)

        with patch("director_orchestrator.langgraph_postgres.open_provider_activation_store", open_store), \
                patch("director_orchestrator.primary_provider_evidence._BoundedCaptureReader", return_value=reader), \
                patch.object(evidence, "_activation_recorded_at", return_value=observed.isoformat()), \
                patch("director_orchestrator.provider_validation.capture_verifier_from_environment",
                      side_effect=AssertionError("ambient verifier reached")):
            report = evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64},
                verifier=verifier, environment={"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": PIN})
        self.assertTrue(all(s["capture_verified"] for s in report["lanes"]["claude"]["surfaces"].values()))
        document["signature"] = "0" * 64
        forged_digest = digest(canonical_json_bytes(document))
        self.assertFalse(verifier.verify(fixture.binding(), forged_digest))
        # Explicit collection cannot silently fall back to a candidate/environment verifier.
        with patch("director_orchestrator.provider_validation.capture_verifier_from_environment",
                   side_effect=AssertionError("ambient verifier reached")), self.assertRaises(ActivationEvidenceError):
            evidence.activation_capture_report(SimpleNamespace(), SimpleNamespace(), {}, environment={})

    def test_capture_product_names_only_real_products(self):
        codex = (b'{"type":"thread.started","thread_id":"t-1"}\n{"type":"turn.started"}\n'
                 b'{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}\n{"type":"turn.completed"}')
        cases = (("openai", b'{"object":"response","id":"resp_1","model":"gpt-5.6"}', "openai-responses"),
                 ("openai", b'{"object":"response","id":"resp_1","model":"o4-mini"}', "openai-responses"),
                 # Substitutions: another vendor's model, or no model, on the OpenAI Responses route.
                 ("openai", b'{"object":"response","id":"resp_1","model":"grok-4"}', None),
                 ("openai", b'{"object":"response","id":"resp_1"}', None),
                 # Review 4102705154: the Codex CLI stream carries no product or model identity (as
                 # native_codex_transport notes), so even a full turn is only labelled unattested.
                 ("openai", codex, "openai-codex-unattested"),
                 ("openai", b'{"type":"thread.started"}\n{"type":"turn.completed"}', "openai-codex-unattested"),
                 ("openai", b'{"type":"thread.started"}\n{"type":"message"}\n{"type":"turn.completed"}', None),
                 ("openai", b'{"type":"turn.completed"}', None),
                 ("openai", b'{"type":"message"}', None),
                 ("openai", b'{"type":"result","result":"ok"}', None),
                 # 4109777108 sweep: nothing binds a grok Responses envelope to xAI's endpoint.
                 ("grok", b'{"object":"response","model":"grok-4"}', "xai-grok-cli-unattested"),
                 ("grok", b'{"object":"response","model":"gpt-5"}', None),
                 ("grok", b'{"object":"response"}', None),
                 ("grok", b'{"stopReason":"end_turn","num_turns":1,"sessionId":"s-1"}', None),
                 ("grok", b'{"stopReason":"end_turn","num_turns":1,"model":"xai/grok-4"}', "xai-grok-cli-unattested"),
                 ("grok", b'{"stopReason":"end_turn","num_turns":1,"model":"gpt-5"}', None),
                 ("grok", b'{"stopReason":"end_turn","num_turns":"1"}', None),
                 ("grok", b'{"result":"done"}', None),
                 ("grok", b'{"type":"result","subtype":"success"}', None),
                 ("grok", b'[1, 2]', None),
                 ("grok", b'not json', None),
                 ("claude", b'{"type":"result","result":"ok"}', None),
                 ("claude", b'{"type":"result","modelUsage":{"claude-opus-5-5":{},"gpt-5":{}}}', None),
                 ("claude", b'{"type":"result","modelUsage":{"claude-opus-5-5":{}}}', "anthropic-claude-unattested"),
                 ("claude", b'{"type":"result","modelUsage":{"gpt-5":{}}}', None),
                 ("claude", b'{"type":"result","modelUsage":{}}', None),
                 ("claude", b'{"type":"assistant"}', None),
                 ("claude", b'{"type":"result"}\n{"type":"result"}', None),
                 ("cursor", b'{"type":"result"}', None))
        for provider, output, expected in cases:
            with self.subTest(provider=provider, output=output):
                self.assertEqual(expected, evidence._capture_product(provider, output, official_openai=True))
        # 4109777108: the envelope shape alone never names the product; without a route bound to the official
        # OpenAI endpoint a Responses capture is unattested (check 11 UNKNOWN), and a foreign model stays a substitution.
        self.assertEqual(evidence._capture_product("openai", cases[0][1]), "openai-responses-unattested")
        self.assertIsNone(evidence._capture_product("openai", cases[2][1]))

    def test_cli_captures_name_their_product_only_through_a_pinned_release(self):
        """Owner ruling 2026-09-26 ("Bind to pinned release"): one attestation path for the Codex, grok and Claude
        CLIs. A self-reported model never suffices; the executable digest recorded at execution must match exactly one
        owner-pinned descriptor entry for this provider and product. No descriptor is pinned yet: fail closed."""
        self.assertIsNone(evidence.cli_release_descriptor())
        digest = "e" * 64
        outputs = {"openai": b'{"type":"thread.started"}\n{"type":"turn.completed"}',
                   "grok": b'{"stopReason":"end_turn","num_turns":1,"model":"xai/grok-4"}',
                   "claude": b'{"type":"result","modelUsage":{"claude-opus-5-5":{}}}'}
        for provider, product in evidence.CLI_PRODUCTS.items():
            entry = {"provider": provider, "product": product, "executable_sha256": digest}
            descriptor = {"version": evidence.CLI_RELEASE_DESCRIPTOR_VERSION, "releases": [entry]}
            with self.subTest(provider=provider):
                self.assertEqual(evidence.cli_release_product(provider, digest, descriptor), product)
                self.assertEqual(evidence._capture_product(provider, outputs[provider], executable_sha256=digest,
                                                           descriptor=descriptor), product)
                for label, args in (
                        ("no descriptor", (digest, None)),
                        ("no executable digest", (None, descriptor)),
                        ("another executable", ("f" * 64, descriptor)),
                        ("uppercase digest", (digest.upper(), descriptor)),
                        ("another version", (digest, {**descriptor, "version": "v0"}))
                        , ("pinned twice", (digest, {**descriptor, "releases": [entry, dict(entry)]})),
                        ("another provider's entry", (digest, {**descriptor, "releases": [
                            {**entry, "provider": "cursor"}]})),
                        ("another product", (digest, {**descriptor, "releases": [{**entry, "product": "x"}]})),
                        ("an extra key", (digest, {**descriptor, "releases": [{**entry, "note": "x"}]})),
                        ("releases not a list", (digest, {**descriptor, "releases": entry}))):
                    self.assertIsNone(evidence.cli_release_product(provider, *args), label)
                    self.assertEqual(evidence._capture_product(provider, outputs[provider], executable_sha256=args[0],
                                                               descriptor=args[1]), product + "-unattested", label)
        # The attested product never rescues a capture whose dialect or model is another vendor's.
        entry = {"provider": "grok", "product": "xai-grok-cli", "executable_sha256": digest}
        self.assertIsNone(evidence._capture_product(
            "grok", b'{"stopReason":"end_turn","num_turns":1,"model":"gpt-5"}', executable_sha256=digest,
            descriptor={"version": evidence.CLI_RELEASE_DESCRIPTOR_VERSION, "releases": [entry]}))

    def test_capture_attestations_report_digests_and_expiry_but_health_is_unchanged(self):
        store = MemoryStore()
        bindings = {provider: binding(provider) for provider in ("openai", "claude")}
        for surface in ProviderActivationSurface:
            advance(store, bindings["openai"], surface)
        advance(store, bindings["claude"], ProviderActivationSurface.APP)
        verified = {row.evidence_digest for surface in ProviderActivationSurface
                    for row in store.load(bindings["openai"], surface)}
        verifier = Mock()
        # CaptureVerifier.verified_output: the verified output bytes, or None when the capture does not verify.
        verifier.verified_output.side_effect = (
            lambda _binding, digest, expires_at: b'{"object":"response","model":"gpt-5.6"}' if digest in verified else None)
        service = PrimaryProviderActivationService(store=store, bindings=bindings, required_providers=("openai", "claude"),
                                                   clock=lambda: NOW, capture_verifier=verifier)
        lanes = service.capture_attestations()
        self.assertEqual(set(lanes), {"openai", "claude"})
        self.assertEqual(lanes["openai"]["binding_digest"], binding_digest(bindings["openai"]))
        for name in ("app", "runtime_adapter"):
            surface = lanes["openai"]["surfaces"][name]
            self.assertEqual(surface["stage"], "LIVE_OK")
            self.assertTrue(surface["capture_verified"])
            self.assertEqual(surface["expires_at"], (NOW + timedelta(hours=1)).isoformat())
            self.assertEqual(surface["observed_at"], (NOW + timedelta(seconds=len(ProviderActivationStage))).isoformat())
            self.assertEqual(len(surface["evidence_digest"]), 64)
        self.assertFalse(lanes["claude"]["surfaces"]["app"]["capture_verified"])
        self.assertIsNone(lanes["claude"]["surfaces"]["runtime_adapter"])
        for name in ("app", "runtime_adapter"):
            surface = lanes["openai"]["surfaces"][name]
            self.assertTrue(surface["chain_valid"])
            self.assertEqual(surface["stages"], [stage.value for stage in ProviderActivationStage])
            self.assertEqual(surface["chain_expires_at"], (NOW + timedelta(hours=1)).isoformat())
        # /health stays booleans only; attestations are a separate operator read.
        self.assertEqual(set(service.health()), {"core_live_ready", "app_dry_run_ready", "app_live_ready",
                                                 "runtime_adapter_dry_run_ready", "runtime_adapter_live_ready",
                                                 "combined_dry_run_ready", "combined_live_ready"})
        self.assertTrue(all(type(value) is bool for value in service.health().values()))
        unverified = PrimaryProviderActivationService(store=store, bindings=bindings, required_providers=("openai", "claude"),
                                                      clock=lambda: NOW)
        self.assertFalse(unverified.capture_attestations()["openai"]["surfaces"]["app"]["capture_verified"])

    def test_capture_attestations_read_the_registrys_validated_chain(self):
        """PR #264 review 4100588387: a surface is read through ProviderActivationRegistry._validated_records (every
        stage once, in revision and observation order), never its store's last row; and it lapses with its
        earliest-expiring stage."""
        from director_orchestrator.provider_activation import ACTIVATION_EVIDENCE_VERSION, ProviderActivationEvidence
        store = MemoryStore()
        openai = binding("openai")
        verifier = Mock()
        verifier.verify.return_value = True
        service = PrimaryProviderActivationService(store=store, bindings={"openai": openai},
                                                   required_providers=("openai",), clock=lambda: NOW,
                                                   capture_verifier=verifier)
        # A lone LIVE_OK row (no earlier stage): the store's last row looks live, the chain does not validate.
        store.rows[(openai, ProviderActivationSurface.APP)] = [ProviderActivationEvidence(
            schema_version=ACTIVATION_EVIDENCE_VERSION, binding=openai, surface=ProviderActivationSurface.APP,
            stage=ProviderActivationStage.LIVE_OK, revision=1, evidence_id="lone", evidence_digest="f" * 64,
            observed_at=NOW, expires_at=NOW + timedelta(hours=1))]
        # A valid chain whose first stage expires first: the surface lapses with it.
        registry = ProviderActivationRegistry(store, openai)
        for index, stage in enumerate(ProviderActivationStage, 1):
            registry.record(surface=ProviderActivationSurface.RUNTIME_ADAPTER, stage=stage, evidence_id=f"r-{index}",
                            evidence_digest=hashlib.sha256(stage.value.encode()).hexdigest(),
                            observed_at=NOW + timedelta(seconds=index),
                            expires_at=NOW + (timedelta(minutes=10) if index == 1 else timedelta(hours=1)),
                            now=NOW + timedelta(seconds=index))
        surfaces = service.capture_attestations()["openai"]["surfaces"]
        self.assertEqual(surfaces["app"], {"chain_valid": False, "capture_verified": False})
        self.assertTrue(surfaces["runtime_adapter"]["chain_valid"])
        self.assertEqual(surfaces["runtime_adapter"]["expires_at"], (NOW + timedelta(hours=1)).isoformat())
        self.assertEqual(surfaces["runtime_adapter"]["chain_expires_at"], (NOW + timedelta(minutes=10)).isoformat())

    @contextmanager
    def capture_sources(self, store, output=b'{"type":"result","modelUsage":{"claude-opus-5-5":{}}}',
                        verifier_present=True, route=None):
        writes = Mock(side_effect=AssertionError("activation store append reached"))

        @contextmanager
        def open_store(*args, **kwargs):
            yield SimpleNamespace(load=store.load, append_authorized=writes)

        class Reader:
            def __init__(self, dsn):
                self.dsn = dsn

            def load_capture(self, digest):
                raise AssertionError("the capture was re-read after verification")

        # Review 4107814598: the product is named from the bytes verified_output returns,
        # the verifier's own single read; the reader below fails if anything reads the store a second time.
        reads = []

        def verified_output(*_args, **_kwargs):
            reads.append(1)
            return output

        verifier = SimpleNamespace(store=SimpleNamespace(dsn="postgresql://fixture"),
                                   verify=lambda *_args, **_kwargs: True,
                                   verified_output=verified_output,
                                   openai_responses_route=route) if verifier_present else None
        with patch("director_orchestrator.provider_validation.capture_verifier_from_environment", return_value=verifier), \
                patch("director_orchestrator.primary_provider_evidence._BoundedCaptureReader", Reader), \
                patch("director_orchestrator.langgraph_postgres.open_provider_activation_store", open_store), \
                patch.dict(os.environ, {"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": PIN}):
            yield writes

    def test_activation_capture_report_is_load_only_and_names_products(self):
        store = MemoryStore()
        bindings = {"claude": binding("claude")}
        for surface in ProviderActivationSurface:
            advance(store, bindings["claude"], surface)
        settings = SimpleNamespace(langgraph_primary_postgres_dsn="postgresql://fixture",
                                   langgraph_primary_allow_insecure_local_postgres=True,
                                   primary_provider_activation_bindings=lambda: bindings,
                                   langgraph_primary_required_providers=("claude",))
        identity = SimpleNamespace(candidate_commit="a" * 40, candidate_tree="b" * 40, postgres_schema="director_primary_v1",
                                   database_identity="postgres:sha256:" + "c" * 64, database_instance_id=str(uuid.uuid4()))
        with self.capture_sources(store) as writes, \
                patch.object(evidence, "_activation_recorded_at", return_value="2026-09-12T09:30:01+00:00") as recorded:
            report = evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})
        writes.assert_not_called()
        self.assertEqual(report["lanes"]["claude"]["surfaces"]["runtime_adapter"]["recorded_at"],
                         "2026-09-12T09:30:01+00:00")
        self.assertEqual(report["lanes"]["claude"]["surfaces"]["app"]["recorded_at"], "2026-09-12T09:30:01+00:00")
        self.assertEqual(sorted(call.args[4] for call in recorded.call_args_list), ["app", "runtime_adapter"])
        self.assertEqual(recorded.call_args.args[1], "director_primary_v1")
        self.assertEqual(recorded.call_args.args[2], bindings["claude"])  # the full current binding
        self.assertEqual(report["version"], "director-activation-captures/v1")
        self.assertEqual(report["generation_manifest_sha256"], PIN)
        self.assertEqual(report["provider_bindings_sha256"], "d" * 64)
        self.assertEqual(report["required_providers"], ["claude"])
        # Owner ruling 2026-09-26: no release descriptor is pinned yet, so a Claude CLI capture is unattested.
        self.assertEqual(report["lanes"]["claude"]["product"], "anthropic-claude-unattested")
        self.assertTrue(report["lanes"]["claude"]["surfaces"]["runtime_adapter"]["capture_verified"])
        # Review 4111508065: both activation surfaces must classify as the same attested product.
        with self.capture_sources(store), \
                patch.object(evidence, "_capture_product", side_effect=["anthropic-claude", "anthropic-claude-unattested"]), \
                patch.object(evidence, "_activation_recorded_at", return_value=None):
            self.assertIsNone(evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})
                              ["lanes"]["claude"]["product"])
        with self.capture_sources(store, output=b'{"type":"assistant"}'), \
                patch.object(evidence, "_activation_recorded_at", return_value=None):
            self.assertIsNone(evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})
                              ["lanes"]["claude"]["product"])
        with self.capture_sources(store, verifier_present=False), self.assertRaises(ActivationEvidenceError), \
                patch.object(evidence, "_activation_recorded_at", return_value=None):
            evidence.activation_capture_report(settings, identity, {"provider_bindings_sha256": "d" * 64})
        # 4109777108: an OpenAI Responses capture names the product only when the verifier's route (whose signed
        # configuration digest pins it) targets exactly the official endpoint.
        openai = {"openai": binding("openai")}
        for surface in ProviderActivationSurface:
            advance(store, openai["openai"], surface)
        settings.primary_provider_activation_bindings = lambda: openai
        settings.langgraph_primary_required_providers = ("openai",)
        responses = b'{"object":"response","id":"resp_1","model":"gpt-5.6"}'
        for url, product in ((evidence.OFFICIAL_OPENAI_RESPONSES_URL, "openai-responses"),
                             ("https://proxy.example.test/v1/responses", "openai-responses-unattested"),
                             ("https://api.openai.com.example.test/v1/responses", "openai-responses-unattested"),
                             (None, "openai-responses-unattested")):
            route = None if url is None else SimpleNamespace(transport=SimpleNamespace(api_url=url))
            with self.subTest(url=url), self.capture_sources(store, output=responses, route=route), \
                    patch.object(evidence, "_activation_recorded_at", return_value=None):
                self.assertEqual(evidence.activation_capture_report(
                    settings, identity, {"provider_bindings_sha256": "d" * 64})["lanes"]["openai"]["product"], product)

    def run_main(self, *argv):
        settings = SimpleNamespace(langgraph_primary_postgres_dsn="postgresql://fixture",
                                   langgraph_primary_postgres_schema="director_primary_v1",
                                   langgraph_primary_allow_insecure_local_postgres=True,
                                   langgraph_primary_pinned_commit_sha="a" * 40, langgraph_primary_pinned_tree_sha="b" * 40,
                                   app_env="local", langgraph_primary_checkpoint_namespace="default-director-primary-v1")
        manifest = {"provider_bindings_sha256": "d" * 64}
        printed = io.StringIO()
        with patch.object(sys, "argv", ["primary_generation", *argv]), \
                patch("director_orchestrator.config.Settings", return_value=settings), \
                patch("director_orchestrator.langgraph_postgres.database_binding",
                      return_value=("postgres:sha256:" + "c" * 64, str(uuid.uuid4()))), \
                patch("director_orchestrator.primary_generation.require_generation", return_value=manifest) as generation, \
                patch.object(evidence, "consistency_report", return_value={"version": "consistency", "n": 1}) as consistency, \
                patch.object(evidence, "activation_capture_report", return_value={"version": "captures"}) as captures, \
                patch.dict(os.environ, {"DIRECTOR_COMPOSE_PROJECT_NAME": "edge-engine-director"}), redirect_stdout(printed):
            from director_orchestrator import primary_generation
            primary_generation.main()
        return printed.getvalue(), generation, consistency, captures, manifest

    def test_main_dispatches_read_only_commands_without_reserving(self):
        output, generation, consistency, captures, _ = self.run_main("consistency", "--task", "DIR-LG-AB12", "--task", "DIR-LG-CD34")
        self.assertEqual(output.count("\n"), 1)
        self.assertEqual(json.loads(output), {"version": "consistency", "n": 1})
        self.assertEqual(consistency.call_args.kwargs["tasks"], ("DIR-LG-AB12", "DIR-LG-CD34"))
        self.assertIs(generation.call_args.kwargs["reserve"], False)
        captures.assert_not_called()
        output, generation, consistency, captures, manifest = self.run_main("activation-captures")
        self.assertEqual(json.loads(output), {"version": "captures"})
        self.assertIs(captures.call_args.args[2], manifest)
        self.assertIs(generation.call_args.kwargs["reserve"], False)
        consistency.assert_not_called()

    def test_main_refuses_task_selection_outside_consistency(self):
        for argv in (("verify", "--task", "DIR-LG-AB12"), ("activation-captures", "--task", "DIR-LG-AB12"),
                     ("consistency", "--task", "DIR-LG-ab12"), ("consistency", "--task", "task-1"),
                     ("consistency", *[part for index in range(13) for part in ("--task", f"DIR-LG-{index:X}")])):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                self.run_main(*argv)
            self.assertEqual(raised.exception.code, 2)


class ScorecardEvidencePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests")
        cls.coordinator_fixtures = _load("test_primary_coordinator")

    def setUp(self) -> None:
        from director_orchestrator.langgraph_postgres import open_postgres_control_plane
        from director_orchestrator.primary_coordinator import PrimaryTaskCoordinator
        self.schema = f"director_scorecard_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(TEST_DSN, schema=self.schema, allow_insecure_local_transport=True)
        self.coordinator = PrimaryTaskCoordinator(self.handle, self.coordinator_fixtures.handlers(),
                                                  owner_token="default-director-owner",
                                                  checkpoint_namespace="default-director-primary-v1")
        self.settings = SimpleNamespace(langgraph_primary_postgres_dsn=TEST_DSN,
                                        langgraph_primary_postgres_schema=self.schema,
                                        langgraph_primary_acceptance_mode="provider-v2")
        from director_orchestrator.langgraph_postgres import database_binding
        storage, instance = database_binding(TEST_DSN, schema=self.schema, allow_insecure_local_transport=True)
        self.identity = SimpleNamespace(candidate_commit="a" * 40, candidate_tree="b" * 40,
                                        database_identity=storage, database_instance_id=instance)

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    def report(self, tasks=()):
        return evidence.consistency_report(self.settings, self.identity, tasks=tasks,
            environment={"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": PIN,
                         "DIRECTOR_RUNTIME_IMAGE_ID": "sha256:" + "c" * 64})

    def test_explicit_connection_checks_instance_and_stays_open(self):
        import psycopg
        from psycopg.rows import dict_row
        from director_orchestrator.langgraph_postgres import PostgresCheckpointConfigurationError
        self.intake()
        with psycopg.connect(TEST_DSN, row_factory=dict_row) as connection:
            environment = {"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": PIN,
                           "DIRECTOR_RUNTIME_IMAGE_ID": "sha256:" + "c" * 64}
            report = evidence.consistency_report(self.settings, self.identity, connection=connection,
                                                   environment=environment)
            self.assertEqual(report["counts"]["graph_task_count"], 1)
            self.assertFalse(connection.closed)
            self.assertTrue(connection.read_only)
            wrong = SimpleNamespace(**vars(self.identity))
            wrong.database_instance_id = str(uuid.uuid4())
            with self.assertRaises(PostgresCheckpointConfigurationError):
                evidence.consistency_report(self.settings, wrong, connection=connection, environment=environment)

    def intake(self):
        self.coordinator.activate_new_intake(expected_generation=0)
        task_id = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        self.coordinator.create_intake(self.coordinator_fixtures.request(task_id))
        return task_id

    def test_counts_zero_then_digest_conflict(self):
        task_id = self.intake()
        report = self.report()
        self.assertEqual(report["version"], "director-primary-consistency/v1")
        self.assertEqual(report["generation"], {"manifest_sha256": PIN, "acceptance_mode": "provider-v2",
                                                "candidate_commit": "a" * 40, "candidate_tree": "b" * 40, "adoption": None,
                                                "image_id": "sha256:" + "c" * 64})
        self.assertEqual(report["intake_fencing_generation"], 1)
        self.assertEqual(report["counts"]["graph_task_count"], 1)
        for key in ("unresolved_reconciliation", "ambiguous_assignments", "tasks_requiring_reconciliation",
                    "terminal_tasks_open_assignments", "terminal_tasks_open_effects",
                    "projection_batch_digest_conflicts", "projections_ahead_of_fence"):
            self.assertEqual(report["counts"][key], 0, key)
        self.assertTrue(all(type(value) is int for value in report["counts"].values()))
        json.dumps(report, allow_nan=False)
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_parallel_batch"
                "(task_id, plan_digest, owner_token, fencing_generation, max_concurrency)"
                " VALUES (%s, %s, 'default-director-owner', 1, 1)", (task_id, "sha256:" + "1" * 64))
            self.assertEqual(self.report()["counts"]["batches_pending_projection_binding"], 1)
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = jsonb_set(projection, '{parallel_plan_digest}', to_jsonb(%s::text)) WHERE task_id = %s",
                ("sha256:" + "2" * 64, task_id))
        conflicted = self.report()["counts"]
        self.assertEqual(conflicted["projection_batch_digest_conflicts"], 1)
        self.assertEqual(conflicted["batches_pending_projection_binding"], 0)

    def plan(self, task_id, terminal, assignments):
        """A batch bound to the projection, its assignments, and the projection's terminal classification."""
        digest = "sha256:" + "3" * 64
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_parallel_batch"
                "(task_id, plan_digest, owner_token, fencing_generation, max_concurrency)"
                " VALUES (%s, %s, 'default-director-owner', 1, 4)", (task_id, digest))
            for sequence, (assignment_id, status, dependencies) in enumerate(assignments, 1):
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_parallel_assignment(task_id, assignment_id, sequence,"
                    " provider_id, deployment_id, dependencies, write_surfaces, retry_limit, status)"
                    " VALUES (%s, %s, %s, 'claude', 'fixture', %s::jsonb, '[]'::jsonb, 3, %s)",
                    (task_id, assignment_id, sequence, json.dumps(dependencies), status))
            self.classify(connection, task_id, terminal, digest)

    def classify(self, connection, task_id, terminal, digest=None):
        projection = "jsonb_set(projection, '{terminal_classification}', to_jsonb(%s::text))"
        values = [terminal]
        if digest is not None:
            projection = f"jsonb_set({projection}, '{{parallel_plan_digest}}', to_jsonb(%s::text))"
            values.append(digest)
        connection.execute(
            f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
            f" projection = {projection} WHERE task_id = %s", (*values, task_id))

    def test_succeeded_projection_needs_its_plan(self):
        task_id = self.intake()
        # SUCCEEDED with no plan digest, batch or assignment: an impossible success.
        with self.handle.pool.connection() as connection:
            self.classify(connection, task_id, "SUCCEEDED")
        self.assertEqual(self.report()["counts"]["succeeded_projections_without_plan"], 1)
        # A bound batch with only a FAILED assignment is still not a plan that succeeded.
        other = self.intake_another()
        self.plan(other, "SUCCEEDED", [("a1", "FAILED", [])])
        self.assertEqual(self.report()["counts"]["succeeded_projections_without_plan"], 2)
        # A bound batch with a SUCCEEDED assignment is consistent.
        third = self.intake_another()
        self.plan(third, "SUCCEEDED", [("a1", "SUCCEEDED", [])])
        self.assertEqual(self.report()["counts"]["succeeded_projections_without_plan"], 2)

    def intake_another(self):
        task_id = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        self.coordinator.create_intake(self.coordinator_fixtures.request(task_id))
        return task_id

    def test_every_nonterminal_projection_is_in_flight(self):
        task_id = self.intake()
        # Admitted before its batch exists: no subordinate row, still in flight.
        counts = self.report()["counts"]
        self.assertEqual((counts["nonterminal_projections"], counts["active_assignments"]), (1, 0))
        # Every assignment settled, but no terminal projection written: still in flight.
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_parallel_batch"
                "(task_id, plan_digest, owner_token, fencing_generation, max_concurrency)"
                " VALUES (%s, %s, 'default-director-owner', 1, 1)", (task_id, "sha256:" + "3" * 64))
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_parallel_assignment(task_id, assignment_id, sequence,"
                " provider_id, deployment_id, dependencies, write_surfaces, retry_limit, status)"
                " VALUES (%s, 'a1', 1, 'claude', 'fixture', '[]'::jsonb, '[]'::jsonb, 3, 'SUCCEEDED')", (task_id,))
        counts = self.report()["counts"]
        self.assertEqual((counts["nonterminal_projections"], counts["active_assignments"]), (1, 0))
        with self.handle.pool.connection() as connection:
            self.classify(connection, task_id, "SUCCEEDED", "sha256:" + "3" * 64)
        self.assertEqual(self.report()["counts"]["nonterminal_projections"], 0)

    def test_blocked_dependents_and_owner_holds_are_not_open_terminal_work(self):
        task_id = self.intake()
        # A failed prerequisite leaves its dependency chain PENDING in storage; the aggregator projects it BLOCKED.
        self.plan(task_id, "FAILED", [("a1", "FAILED", []), ("a2", "PENDING", ["a1"]), ("a3", "PENDING", ["a2"]),
                                      ("a4", "PENDING", [])])
        counts = self.report()["counts"]
        # Only a4, which nothing blocks, is open work on a terminal task.
        self.assertEqual((counts["terminal_tasks_open_assignments"], counts["active_assignments"]), (1, 0))
        # An OWNER_DECISION_REQUIRED hold with no pending approval cannot resume: it is settled residue.
        with self.handle.pool.connection() as connection:
            self.classify(connection, task_id, "OWNER_DECISION_REQUIRED")
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = projection - 'pending_approval' WHERE task_id = %s", (task_id,))
        counts = self.report()["counts"]
        self.assertEqual((counts["terminal_tasks_open_assignments"], counts["active_assignments"]), (1, 0))
        # With a pending approval it is a resumable hold: in flight, and its blocked chain is not active work.
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = jsonb_set(projection, '{pending_approval}', %s::jsonb) WHERE task_id = %s",
                (json.dumps({"request_digest": "sha256:" + "4" * 64}), task_id))
        counts = self.report()["counts"]
        self.assertEqual((counts["terminal_tasks_open_assignments"], counts["active_assignments"]), (0, 1))
        with self.handle.pool.connection() as connection:
            connection.execute(f"DELETE FROM {self.schema}.director_primary_parallel_assignment"
                               " WHERE task_id = %s AND assignment_id = 'a4'", (task_id,))
            self.classify(connection, task_id, "DONE")
        self.assertEqual(self.report()["counts"]["terminal_tasks_open_assignments"], 0)

    def test_contradictory_projection_states_are_counted(self):
        task_id = self.intake()
        # SUCCEEDED over a FAILED assignment contradicts the authoritative assignment outcome.
        self.plan(task_id, "SUCCEEDED", [("a1", "SUCCEEDED", []), ("a2", "FAILED", [])])
        self.assertEqual(self.report()["counts"]["succeeded_projections_with_unsucceeded_assignments"], 1)
        other = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        self.coordinator.create_intake(self.coordinator_fixtures.request(other))
        # A projection naming a plan digest whose batch row is absent.
        with self.handle.pool.connection() as connection:
            self.classify(connection, other, "SUCCEEDED", "sha256:" + "5" * 64)
        counts = self.report()["counts"]
        self.assertEqual(counts["projections_missing_batch"], 1)
        self.assertEqual(counts["batches_without_projection"], 0)

    def test_owner_held_assignment_with_pending_approval_is_in_flight(self):
        task_id = self.intake()
        # The held assignment is not PENDING/ACTIVE and its dependent is BLOCKED, so only the hold shows it.
        self.plan(task_id, "OWNER_DECISION_REQUIRED", [("a1", "OWNER_DECISION_REQUIRED", []), ("a2", "PENDING", ["a1"])])
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = jsonb_set(projection, '{pending_approval}', %s::jsonb) WHERE task_id = %s",
                (json.dumps({"request_digest": "sha256:" + "4" * 64}), task_id))
        counts = self.report()["counts"]
        self.assertEqual((counts["active_assignments"], counts["open_effect_reservations"],
                          counts["batches_pending_projection_binding"], counts["pending_owner_decisions"]), (0, 0, 0, 1))
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = projection - 'pending_approval' WHERE task_id = %s", (task_id,))
        self.assertEqual(self.report()["counts"]["pending_owner_decisions"], 0)
        self.assertEqual(self.report()["counts"]["terminal_tasks_open_assignments"], 1)

    def test_every_residue_count_is_driven_by_real_rows(self):
        task_id = self.intake()
        other = "DIR-LG-" + uuid.uuid4().hex[:10].upper()
        self.coordinator.create_intake(self.coordinator_fixtures.request(other))
        s = self.schema
        with self.handle.pool.connection() as connection:
            for claim in ("effect-1", "reconcile-1"):
                connection.execute(
                    f"INSERT INTO {s}.director_primary_claim(claim_key, task_id, field, expected_state, target_state,"
                    " attempt_digest, owner_token, fencing_generation)"
                    " VALUES (%s, %s, 'effect', 'NOT_STARTED', 'COMPLETED', 'fixture', 'default-director-owner', 1)",
                    (claim, task_id))
            connection.execute(
                f"INSERT INTO {s}.director_primary_effect_reservation(reservation_key, claim_key, task_id, owner_token,"
                " fencing_generation, effect_key_digest, effect_attempt_digest, writer_owner_id, reserved_units)"
                " VALUES ('reservation-1', 'effect-1', %s, 'default-director-owner', 1, 'k', 'a', 'w', 0)", (task_id,))
            connection.execute(
                f"INSERT INTO {s}.director_primary_reconciliation_event(claim_key, task_id, owner_token,"
                " fencing_generation, state, evidence_digest)"
                " VALUES ('reconcile-1', %s, 'default-director-owner', 1, 'AMBIGUOUS', 'e')", (task_id,))
            connection.execute(
                f"INSERT INTO {s}.director_primary_task_writer(task_id, engine_owner, owner_token, fencing_generation,"
                " lease_expires_at) VALUES ('DIR-LG-LEASE', 'langgraph', 'default-director-owner', 1,"
                " now() + interval '1 hour'), ('DIR-LG-LAPSED', 'langgraph', 'default-director-owner', 1,"
                " now() - interval '1 hour')")
        live = self.report()["counts"]
        self.assertEqual((live["open_effect_reservations"], live["terminal_tasks_open_effects"],
                          live["unresolved_reconciliation"]), (1, 0, 1))
        self.assertGreaterEqual(live["live_writer_claims"], 1)
        # Settle the task: its unfinished effect and its unblocked assignments become terminal residue.
        # a2 waits on a SUCCEEDED prerequisite, so nothing blocks it.
        self.plan(task_id, "FAILED", [("a1", "SUCCEEDED", []), ("a2", "PENDING", ["a1"]), ("a3", "AMBIGUOUS", [])])
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {s}.director_primary_parallel_batch(task_id, plan_digest, owner_token, fencing_generation,"
                " max_concurrency) VALUES (%s, %s, 'default-director-owner', 1, 1)", (other, "sha256:" + "5" * 64))
            self.classify(connection, other, "RECONCILIATION_REQUIRED")
            # The projection guard forbids this state; lift it in the disposable schema to prove the count sees it.
            connection.execute(f"ALTER TABLE {s}.director_primary_task_projection DISABLE TRIGGER USER")
            connection.execute(f"UPDATE {s}.director_primary_task_projection SET intake_fencing_generation = 99"
                               " WHERE task_id = %s", (other,))
        counts = self.report()["counts"]
        self.assertEqual({key: counts[key] for key in (
            "unresolved_reconciliation", "ambiguous_assignments", "tasks_requiring_reconciliation",
            "terminal_tasks_open_assignments", "terminal_tasks_open_effects", "terminal_batches_unbound",
            "projections_ahead_of_fence", "open_effect_reservations", "batches_pending_projection_binding")},
            {"unresolved_reconciliation": 1, "ambiguous_assignments": 1, "tasks_requiring_reconciliation": 1,
             "terminal_tasks_open_assignments": 2, "terminal_tasks_open_effects": 1, "terminal_batches_unbound": 1,
             "projections_ahead_of_fence": 1, "open_effect_reservations": 0, "batches_pending_projection_binding": 0})
        self.assertEqual(counts["live_writer_claims"], live["live_writer_claims"])
        # Review 4110553572: register_plan writes a batch with its non-empty DAG; the batch inserted for `other`
        # above has no assignment, whatever its task's classification.
        self.assertEqual(counts["batches_without_assignments"], 1)
        # Review 4110798521: an execution row naming reservation-1 but carrying another task is counted.
        self.assertEqual(counts["effect_children_cross_task"], 0)
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {s}.director_primary_effect_execution(reservation_key, task_id, owner_token,"
                " fencing_generation) VALUES ('reservation-1', %s, 'default-director-owner', 1)", (other,))
        self.assertEqual(self.report()["counts"]["effect_children_cross_task"], 1)
        # Review 4110241982: terminal evidence recorded before a claim's reconciliation event does not resolve that
        # later event; terminal evidence at or after every event does.
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {s}.director_primary_claim(claim_key, task_id, field, expected_state, target_state,"
                " attempt_digest, owner_token, fencing_generation)"
                " VALUES ('reconcile-2', %s, 'effect', 'NOT_STARTED', 'COMPLETED', 'fixture', 'default-director-owner', 1)",
                (task_id,))
            connection.execute(
                f"INSERT INTO {s}.director_primary_reconciliation_event(claim_key, task_id, owner_token,"
                " fencing_generation, state, evidence_digest)"
                " VALUES ('reconcile-2', %s, 'default-director-owner', 1, 'AMBIGUOUS', 'e')", (task_id,))
            for claim, offset in (("reconcile-1", "- interval '1 second'"), ("reconcile-2", "+ interval '1 second'")):
                connection.execute(
                    f"INSERT INTO {s}.director_primary_terminal_evidence(claim_key, task_id, owner_token,"
                    " fencing_generation, disposition, evidence_digest, recorded_at)"
                    f" SELECT %s, %s, 'default-director-owner', 1, 'COMPLETED', 't', max(recorded_at) {offset}"
                    f" FROM {s}.director_primary_reconciliation_event WHERE claim_key = %s", (claim, task_id, claim))
        self.assertEqual(self.report()["counts"]["unresolved_reconciliation"], 1)
        # Review 4111174699: terminal evidence for a claim no effect reservation holds (reconcile-1 and reconcile-2),
        # counted globally and in the task's entry, never dropped by the effects join.
        self.assertEqual(self.report()["counts"]["terminal_evidence_without_reservation"], 2)
        # A batch whose registering authority is not its task writer's (another token) is counted.
        before = self.report()["counts"]["batch_authority_mismatches"]
        third = self.intake_another()
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {s}.director_primary_parallel_batch(task_id, plan_digest, owner_token, fencing_generation,"
                " max_concurrency) VALUES (%s, %s, 'another-owner', 1, 1)", (third, "sha256:" + "6" * 64))
        self.assertEqual(self.report()["counts"]["batch_authority_mismatches"], before + 1)
        self.assertEqual(self.report(tasks=[task_id])["tasks"][task_id]["terminal_evidence_without_reservation"], 2)

    def test_snapshot_connection_refuses_writes(self):
        import psycopg
        self.intake()
        write = ("INSERT INTO {s}.director_primary_task_writer(task_id, engine_owner, owner_token, fencing_generation,"
                 " lease_expires_at) VALUES ('DIR-LG-W', 'langgraph', 'x', 1, now()) RETURNING now() AS captured_at")
        with patch.object(evidence, "_COUNTS", write), self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            self.report()

    def test_approval_command_receipts_leave_only_a_command_digest(self):
        task_id = self.intake()
        with self.handle.pool.connection() as connection:
            for operation, command in (("approval_resume", "owner-command-1"), ("cancel", "owner-command-2")):
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_command_receipt(task_id, operation, command_id,"
                    " command_digest, resulting_projection_digest, resulting_projection_version,"
                    " writer_fencing_generation) VALUES (%s, %s, %s, %s, %s, 2, 1)",
                    (task_id, operation, command, "sha256:" + "6" * 64, "sha256:" + "7" * 64))
        receipts = self.report(tasks=[task_id])["tasks"][task_id]["approval_receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["command_id_sha256"], hashlib.sha256(b"owner-command-1").hexdigest())
        self.assertEqual((receipts[0]["command_digest"], receipts[0]["resulting_projection_digest"]),
                         ("sha256:" + "6" * 64, "sha256:" + "7" * 64))
        self.assertNotIn("owner-command-1", json.dumps(receipts))
        datetime.fromisoformat(receipts[0]["recorded_at"])
        # No durable command authorization joins it yet: not authorized.
        self.assertEqual((receipts[0]["authorized"], receipts[0]["proof_version"], receipts[0]["actor_id"]),
                         (False, None, None))
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_command_authorization(nonce_digest, task_id, operation,"
                " command_id, command_digest, proof_version, actor_id, writer_fencing_generation, proof_expires_at)"
                " VALUES (%s, %s, 'approval_resume', 'owner-command-1', %s, 'v1', 'UOWNER1', 1, now())",
                ("sha256:" + "9" * 64, task_id, "sha256:" + "6" * 64))
        joined = self.report(tasks=[task_id])["tasks"][task_id]["approval_receipts"][0]
        self.assertEqual({key: joined[key] for key in ("authorized", "authorized_command_digest", "authorized_fence",
                                                        "proof_version", "actor_id")},
                         {"authorized": True, "authorized_command_digest": True, "authorized_fence": True,
                          "proof_version": "v1", "actor_id": "UOWNER1"})
        # The authorization's claim time and proof expiry come back for the scorecard's ordering checks.
        claimed = datetime.fromisoformat(joined["authorization_claimed_at"])
        self.assertGreater(claimed, datetime.fromisoformat(joined["recorded_at"]))  # inserted after the receipt here
        datetime.fromisoformat(joined["authorization_proof_expires_at"])
        # The table is append-only: a second command whose authorization names another digest and fence.
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_command_receipt(task_id, operation, command_id,"
                " command_digest, resulting_projection_digest, resulting_projection_version,"
                " writer_fencing_generation) VALUES (%s, 'approval_resume', 'owner-command-3', %s, %s, 3, 1)",
                (task_id, "sha256:" + "6" * 64, "sha256:" + "7" * 64))
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_command_authorization(nonce_digest, task_id, operation,"
                " command_id, command_digest, proof_version, actor_id, writer_fencing_generation, proof_expires_at)"
                " VALUES (%s, %s, 'approval_resume', 'owner-command-3', %s, 'v1', 'UOWNER1', 2, now())",
                ("sha256:" + "8" * 64, task_id, "sha256:" + "0" * 64))
        rows = self.report(tasks=[task_id])["tasks"][task_id]["approval_receipts"]
        other = next(r for r in rows if r["command_id_sha256"] == hashlib.sha256(b"owner-command-3").hexdigest())
        self.assertEqual((other["authorized"], other["authorized_command_digest"], other["authorized_fence"]),
                         (True, False, False))

    def test_task_settlement_rows(self):
        task_id = self.intake()
        budget = {"task_id": task_id, "project_id": "fixture-project", "day": "2026-09-12",
                  "daily_limit_microusd": 5000000}
        metering = {"request_id": "req_fixture", "response_id": "resp_fixture",
                    "cost_basis": "signed-tariff-metered-not-invoice"}
        with self.handle.pool.connection() as connection:
            connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest, document)"
                               " VALUES ('grant-fixture', %s::jsonb)", (json.dumps(budget),))
            for attempt, reserved in (("sha256:" + "a" * 64, 500000), ("sha256:" + "b" * 64, 200000)):
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_provider_spend(attempt_digest, grant_digest, provider_id,"
                    " project_id, task_id, budget_day, reserved_microusd, token_limit)"
                    " VALUES (%s, 'grant-fixture', 'openai', 'fixture-project', %s, '2026-09-12', %s, 1000)",
                    (attempt, task_id, reserved))
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_usage(attempt_digest, document, actual_microusd,"
                " observed_tokens) VALUES (%s, %s::jsonb, 300000, 100)",
                ("sha256:" + "a" * 64, json.dumps({"provider_id": "openai", "metering_evidence": metering})))
        entry = self.report(tasks=[task_id, "DIR-LG-0"])["tasks"]
        self.assertEqual(entry["DIR-LG-0"], {"usage_tables_present": True, "spend": [], "budgets": [],
                                             "approval_receipts": [], "application": None, "result_artifacts": [],
                                             "work_packets": [], "effect_approval_bindings": [],
                                             "registered_plan": None,  # no batch: nothing registered to recompute
                                             "terminal_evidence_without_reservation": 0,
                                             "verifier_secrets": {"route_verifier": False, "owner_authority": False}})
        # The persisted application record comes back for the scorecard's re-rendering.
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = jsonb_set(projection, '{application}', %s::jsonb) WHERE task_id = %s",
                (json.dumps({"execution_mode": "live", "effect_grant": {"action": "fixture"}}), task_id))
        self.assertEqual(self.report(tasks=[task_id])["tasks"][task_id]["application"],
                         {"execution_mode": "live", "effect_grant": {"action": "fixture"}})
        rows = {row["attempt_digest"]: row for row in entry[task_id]["spend"]}
        self.assertEqual(len(rows), 2)
        # Correlated metering text without a verified usage receipt is not actual cost.
        self.assertEqual((rows["sha256:" + "a" * 64]["actual_microusd"], rows["sha256:" + "a" * 64]["request_correlated"],
                          rows["sha256:" + "a" * 64]["usage_verified"]), (300000, False, False))
        self.assertEqual((rows["sha256:" + "b" * 64]["actual_microusd"], rows["sha256:" + "b" * 64]["request_correlated"]),
                         (None, False))
        self.assertEqual(rows["sha256:" + "a" * 64]["budget_day"], "2026-09-12")
        self.assertEqual(entry[task_id]["budgets"], [{"task_id": task_id, "project_id": "fixture-project",
                                                       "day": "2026-09-12", "daily_limit_microusd": 5000000,
                                                       "day_held_microusd": 700000, "provider_id": None,
                                                       "signature_verified": False, "authority_digest": "grant-fixture"}])
        json.dumps(entry, allow_nan=False)

    def test_result_artifacts_are_revalidated_against_their_digest(self):
        from director_orchestrator.langgraph_postgres import canonical_json_bytes, document_digest
        task_id = self.intake()
        packet = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/model-result-packet-v1.example.json")
                            .read_text(encoding="utf-8"))
        rows = []
        for assignment, tampered in (("A1", None), ("A2", "a different stored result")):
            document = {**packet, "task_id": task_id, "assignment_id": assignment,
                        "idempotency_key": f"{task_id}:{assignment}:review"}
            digest = document_digest(document)
            if tampered:
                document["outcome"] = tampered  # stored content no longer matches its recorded digest
            rows.append((task_id, assignment, document["authority_snapshot_digest"], digest,
                         canonical_json_bytes(document).decode("utf-8")))
        with self.handle.pool.connection() as connection:
            for values in rows:
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_result_packet_artifact(task_id, assignment_id,"
                    " authority_snapshot_digest, document_digest, canonical_document, writer_fencing_generation)"
                    " VALUES (%s, %s, %s, %s, %s, 1)", values)
        report = self.report(tasks=[task_id])
        # Each packet's own persisted insertion time comes back for check 20's lifecycle bound (review 4100588392).
        recorded = [artifact.pop("recorded_at") for artifact in report["tasks"][task_id]["result_artifacts"]]
        self.assertTrue(all(datetime.fromisoformat(value).tzinfo is not None for value in recorded))
        self.assertEqual(report["tasks"][task_id]["result_artifacts"], [
            {"assignment_id": "A1", "authority_snapshot_digest": rows[0][2], "document_digest": rows[0][3],
             "outcome": packet["outcome"], "provider": packet["provider"], "verified": True},
            {"assignment_id": "A2", "authority_snapshot_digest": rows[1][2], "document_digest": rows[1][3],
             "outcome": None, "provider": None, "verified": False}])
        # Neither packet names a durable assignment of its task (the table has no such foreign key): the shared
        # per-task packet rule counts both as conflicts until the assignments exist (PR #264 review 4100381678).
        self.assertEqual(report["counts"]["result_artifacts_invalid"], 2)
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_parallel_batch"
                "(task_id, plan_digest, owner_token, fencing_generation, max_concurrency)"
                " VALUES (%s, %s, 'default-director-owner', 1, 2)", (task_id, "sha256:" + "4" * 64))
            for sequence, assignment in enumerate(("A1", "A2"), 1):
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_parallel_assignment(task_id, assignment_id, sequence,"
                    " provider_id, deployment_id, dependencies, write_surfaces, retry_limit, status)"
                    " VALUES (%s, %s, %s, 'claude', 'fixture', '[]'::jsonb, '[]'::jsonb, 3, 'PENDING')",
                    (task_id, assignment, sequence))
        attached = self.report()["counts"]
        # Review 4102375061: a packet on a PENDING assignment is never one it settled (both conflict: A2's stored
        # document also no longer matches its digest).
        self.assertEqual((attached["result_artifacts_invalid"], attached["result_artifacts_in_flight"]), (2, 0))

        def assignment(sql, *values):
            with self.handle.pool.connection() as connection:
                connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment SET {sql}"
                                   " WHERE task_id=%s AND assignment_id='A1'", (*values, task_id))

        recorded_at = (f"(SELECT recorded_at FROM {self.schema}.director_primary_result_packet_artifact"
                       " WHERE task_id=x.task_id AND assignment_id=x.assignment_id)")
        # primary_default persists the packet while the assignment is reserved (ACTIVE), before primary_parallel
        # settles it: that window is in flight, not a conflict.
        with self.handle.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment x SET status='ACTIVE',"
                               f" reserved_at={recorded_at} - interval '1 second', reservation_digest=%s"
                               " WHERE task_id=%s AND assignment_id='A1'", ("sha256:" + "5" * 64, task_id))
        counts = self.report()["counts"]
        self.assertEqual((counts["result_artifacts_invalid"], counts["result_artifacts_in_flight"]), (1, 1))
        # Settled SUCCEEDED with the packet's digest, after it was recorded: the packet is its result.
        with self.handle.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment x SET status='SUCCEEDED',"
                               f" result_digest=%s, reason_code='completed', completed_at={recorded_at} + interval '1 second'"
                               " WHERE task_id=%s AND assignment_id='A1'", (rows[0][3], task_id))
        counts = self.report()["counts"]
        self.assertEqual((counts["result_artifacts_invalid"], counts["result_artifacts_in_flight"]), (1, 0))
        for change, values in (("result_digest=%s", ("sha256:" + "6" * 64,)),  # another result
                               ("status='FAILED'", ()),  # a FAILED assignment never has a packet
                               ("provider_id='openai'", ())):  # another provider's lane
            with self.subTest(change=change):
                assignment(change, *values)
                self.assertEqual(self.report()["counts"]["result_artifacts_invalid"], 2)
                assignment("status='SUCCEEDED', result_digest=%s, provider_id='claude'", rows[0][3])
        # Past the row cap the count is unknown (None), never a partial number.
        with patch.object(evidence, "EVIDENCE_ROW_CAP", 1):
            self.assertIsNone(self.report()["counts"]["result_artifacts_invalid"])
        # Review 4107814584: every result packet needs its assignment's work packet under the same authority
        # snapshot (work_packet_error). None stored yet: a conflict for this task.
        self.assertEqual(self.report()["counts"]["work_packets_invalid"], 1)
        work = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/model-work-packet-v1.example.json")
                          .read_text(encoding="utf-8"))
        with self.handle.pool.connection() as connection:
            for _, assignment, authority, _, _ in rows:
                document = {**work, "task_id": task_id, "assignment_id": assignment, "authority_snapshot_digest": authority}
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_work_packet_artifact(task_id, assignment_id,"
                    " authority_snapshot_digest, document_digest, canonical_document, writer_fencing_generation)"
                    " VALUES (%s, %s, %s, %s, %s, 1)",
                    (task_id, assignment, authority, document_digest(document),
                     canonical_json_bytes(document).decode("utf-8")))
        report = self.report(tasks=[task_id])
        self.assertEqual([item["verified"] for item in report["tasks"][task_id]["work_packets"]], [True, True])
        # A2 is still PENDING: a work packet for an assignment that was never dispatched is a conflict too.
        self.assertEqual(report["counts"]["work_packets_invalid"], 1)
        with self.handle.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment SET status='ACTIVE',"
                               " reservation_digest=%s, reserved_at=now() WHERE task_id=%s AND assignment_id='A2'",
                               ("sha256:" + "6" * 64, task_id))
        # Review 4109777111: reserved after its work packet was recorded, the packet predates its dispatch.
        self.assertEqual(self.report()["counts"]["work_packets_invalid"], 1)
        with self.handle.pool.connection() as connection:
            connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment x SET reserved_at="
                               f"(SELECT recorded_at FROM {self.schema}.director_primary_work_packet_artifact"
                               " WHERE task_id=x.task_id AND assignment_id=x.assignment_id) - interval '1 second'"
                               " WHERE task_id=%s AND assignment_id='A2'", (task_id,))
        self.assertEqual(self.report()["counts"]["work_packets_invalid"], 0)
        # A packet written under a writer generation the task never held (newer than its writer's) is not verified.
        other = self.intake_another()
        document = {**work, "task_id": other, "assignment_id": "Z1", "authority_snapshot_digest": rows[0][2]}
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_work_packet_artifact(task_id, assignment_id,"
                " authority_snapshot_digest, document_digest, canonical_document, writer_fencing_generation)"
                " VALUES (%s, 'Z1', %s, %s, %s, 99)",
                (other, rows[0][2], document_digest(document), canonical_json_bytes(document).decode("utf-8")))
            self.assertEqual([item["verified"] for item in evidence.packet_rows(connection, self.schema, "work",
                                                                                 tasks=[other])], [False])

    def test_every_tasks_attempt_history_is_validated_by_the_collectors_rule(self):
        """Review 4102375074: provider_attempts_invalid applies validate_attempt_history (the collector's own rule)
        to every task's attempts: first event STARTED, one identity per attempt, contiguous ordinals, times in
        order; past the row cap the count is None."""
        task_id = self.intake()
        self.plan(task_id, "SUCCEEDED", [("a1", "ACTIVE", [])])
        self.assertEqual(self.report()["counts"]["provider_attempts_invalid"], 0)

        def event(ordinal, sequence, status, seconds):
            row = {"assignment_id": "a1", "attempt_ordinal": ordinal, "event_sequence": sequence,
                   "provider_id": "claude", "format_id": "v1", "transport_id": "native-command",
                   "idempotency_key_digest": "sha256:" + "0" * 64, "attempt_digest": "sha256:" + str(ordinal) * 64,
                   "budget_evidence_digest": "sha256:" + "9" * 64, "status": status,
                   "result_digest": "sha256:" + "2" * 64 if status == "SUCCEEDED" else None,
                   "reason_code": "attempt_" + status.lower()}
            # record_provider_attempt's own outcome digest over the event (STARTED rows carry none).
            outcome = None if status == "STARTED" else evidence.outcome_evidence_digest(task_id, row)
            with self.handle.pool.connection() as connection:
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_provider_attempt(task_id, assignment_id,"
                    " attempt_ordinal, event_sequence, provider_id, format_id, transport_id, idempotency_key_digest,"
                    " attempt_digest, status, result_digest, outcome_evidence_digest, reason_code,"
                    " budget_evidence_digest, recorded_at)"
                    " VALUES (%s, 'a1', %s, %s, 'claude', 'v1', 'native-command', %s, %s, %s, %s, %s, %s, %s,"
                    " now() + make_interval(secs => %s))",
                    (task_id, ordinal, sequence, "sha256:" + "0" * 64, "sha256:" + str(ordinal) * 64, status,
                     "sha256:" + "2" * 64 if status == "SUCCEEDED" else None,
                     outcome, "attempt_" + status.lower(),
                     "sha256:" + "9" * 64, seconds))

        event(1, 1, "STARTED", 0)
        event(1, 2, "SUCCEEDED", 1)
        self.assertEqual(self.report()["counts"]["provider_attempts_invalid"], 0)

        def assignment(sql, *values):
            with self.handle.pool.connection() as connection:
                connection.execute(f"UPDATE {self.schema}.director_primary_parallel_assignment x SET {sql}"
                                   " WHERE task_id=%s AND assignment_id='a1'", (*values, task_id))

        # Reviews 4102705142/48/62: the global counts run the same per-task rules. Settled around its attempts with
        # the last attempt's digest: consistent.
        assignment("status='SUCCEEDED', result_digest=%s, reason_code='completed', reservation_digest=%s,"
                   # Reserved when the task was created (4110241997: never before), completed after its attempts.
                   f" reserved_at=(SELECT created_at FROM {self.schema}.director_primary_task_projection"
                   " WHERE task_id=x.task_id), completed_at=now() + interval '1 hour'",
                   "sha256:" + "2" * 64, "sha256:" + "5" * 64)
        counts = self.report()["counts"]
        self.assertEqual((counts["settlement_invalid"], counts["lifecycle_invalid"]), (0, 0))
        # Settled with another digest than its last attempt's final event: a settlement conflict.
        assignment("result_digest=%s", "sha256:" + "8" * 64)
        self.assertEqual(self.report()["counts"]["settlement_invalid"], 1)
        assignment("result_digest=%s", "sha256:" + "2" * 64)
        # Completed before its attempts ran: the events lie outside the assignment's lifecycle.
        assignment("completed_at=now() - interval '30 minutes'")
        self.assertEqual(self.report()["counts"]["lifecycle_invalid"], 1)
        assignment("completed_at=now() + interval '1 hour'")
        # Review 4106903235: spend is outer-joined to usage. Attempt 1 settled with no spend reservation at all is
        # the ledger inconsistency PrimaryProviderBudgetAuthority.reserve refuses; with a reservation but no usage
        # yet it is unresolved (in flight); with its usage recorded it settles.
        self.assertEqual(self.report()["counts"]["provider_usage_invalid"], 1)
        with self.handle.pool.connection() as connection:
            connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest, document)"
                               " VALUES ('grant-usage', '{}'::jsonb)")
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_spend(attempt_digest, grant_digest, provider_id,"
                " project_id, task_id, budget_day, reserved_microusd, token_limit)"
                " VALUES (%s, 'grant-usage', 'claude', 'fixture-project', %s, CURRENT_DATE, 500, 1000)",
                ("sha256:" + "1" * 64, task_id))
        counts = self.report()["counts"]
        self.assertEqual((counts["provider_usage_invalid"], counts["provider_usage_in_flight"]), (0, 1))
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_usage(attempt_digest, document, actual_microusd,"
                " observed_tokens) VALUES (%s, '{}'::jsonb, 300, 100)", ("sha256:" + "1" * 64,))
        counts = self.report()["counts"]
        self.assertEqual((counts["provider_usage_invalid"], counts["provider_usage_in_flight"]), (0, 0))
        # The attempt table is append-only, so the gap comes last: attempt 3 with no attempt 2.
        event(3, 1, "STARTED", 2)  # attempt 3 with no attempt 2: the ordinals have a gap
        self.assertEqual(self.report()["counts"]["provider_attempts_invalid"], 1)
        with patch.object(evidence, "EVIDENCE_ROW_CAP", 1):
            self.assertIsNone(self.report()["counts"]["provider_attempts_invalid"])

    def test_every_delivery_state_and_projection_digest_is_counted(self):
        """Reviews 4106903220 and the readback-digest summary: a delivery still posting or being read back is in
        flight, one no readback will ever settle is a conflict, and a projection whose content no longer matches
        its stored digest is a conflict."""
        task_id = self.intake()
        counts = self.report()["counts"]
        self.assertEqual((counts["slack_deliveries_in_flight"], counts["slack_deliveries_invalid"],
                          counts["projection_digest_mismatches"]), (0, 0, 0))
        # Producer-shaped rows (PrimarySlackDelivery.enqueue): the identity is uuid5 over schema, task and key, and
        # the projection digest is the task projection's (review 4110399590).
        def identity(key):
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.schema}:{task_id}:{key}"))

        current = (f"(SELECT projection_digest FROM {self.schema}.director_primary_task_projection"
                   " WHERE task_id = %s)")
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_slack_delivery(task_id, notification_key,"
                f" projection_digest, payload, client_msg_id, state) VALUES (%s, 'terminal', {current}, '{{}}'::jsonb,"
                " %s, 'PENDING')", (task_id, task_id, identity("terminal")))
        self.assertEqual(self.report()["counts"]["slack_deliveries_in_flight"], 1)
        with self.handle.pool.connection() as connection:
            # A second delivery no readback will ever settle again (two readbacks spent); the ledger refuses rewinding
            # a row, so it is its own row.
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_slack_delivery(task_id, notification_key,"
                f" projection_digest, payload, client_msg_id, state, readback_attempts, claimed_at, last_error)"
                f" VALUES (%s, %s, {current}, '{{}}'::jsonb, %s, 'AMBIGUOUS', 2, now(), 'readback_unverified')",
                (task_id, "hold:sha256:" + "6" * 64, task_id, identity("hold:sha256:" + "6" * 64)))
        counts = self.report()["counts"]
        self.assertEqual((counts["slack_deliveries_in_flight"], counts["slack_deliveries_invalid"]), (0, 1))
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.director_primary_task_projection SET projection_version = projection_version + 1,"
                " projection = jsonb_set(projection, '{run_status}', '\"TAMPERED\"') WHERE task_id = %s", (task_id,))
        self.assertEqual(self.report()["counts"]["projection_digest_mismatches"], 1)
        # The tampered projection's content changed, not its stored digest: the terminal row still names it. A
        # terminal row naming a projection other than the task's is a conflict (4110399590).
        other = self.intake_another()
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_slack_delivery(task_id, notification_key,"
                " projection_digest, payload, client_msg_id, state) VALUES (%s, 'terminal', %s, '{}'::jsonb, %s,"
                " 'PENDING')", (other, "sha256:" + "4" * 64,
                                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.schema}:{other}:terminal"))))
        self.assertEqual(self.report()["counts"]["slack_deliveries_invalid"], 2)

    def test_budget_rows_are_verified_against_the_owner_signed_contract(self):
        import hmac
        import tempfile
        from director_orchestrator.entitlement_contracts import canonical_json_bytes
        task_id = self.intake()
        secret = "fixture-owner-authority-secret-0123456789abcdefghij"
        document = {"schema_version": "primary-provider-budget/v1", "grant_digest": "c" * 64, "provider_id": "openai",
                    "project_id": "fixture-project", "task_id": task_id, "day": "2026-09-12",
                    "task_limit_microusd": 1000000, "daily_limit_microusd": 5000000,
                    "attempt_reservation_microusd": 500000, "token_limit": 1000}
        document["owner_signature"] = hmac.new(secret.encode(), canonical_json_bytes(document), hashlib.sha256).hexdigest()
        forged = {**document, "task_id": task_id, "daily_limit_microusd": 9000000}
        misfiled = {**document, "daily_limit_microusd": 7000000}
        misfiled["owner_signature"] = hmac.new(secret.encode(), canonical_json_bytes(
            {k: v for k, v in misfiled.items() if k != "owner_signature"}), hashlib.sha256).hexdigest()
        with self.handle.pool.connection() as connection:
            # Stored under its own grant digest, under a forged copy's, and (validly signed) under another grant.
            for grant, doc in (("c" * 64, document), ("f" * 64, forged), ("e" * 64, misfiled)):
                connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest, document)"
                                   " VALUES (%s, %s::jsonb)", (grant, json.dumps(doc)))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "owner-authority-secret"
            path.write_text(secret, encoding="utf-8")
            self.settings.entitlement_owner_authority_secret_file = str(path)
            rows = self.report(tasks=[task_id])["tasks"][task_id]["budgets"]
            self.assertEqual(sorted((row["daily_limit_microusd"], row["signature_verified"], row["provider_id"])
                                    for row in rows), [(5000000, True, "openai"), (7000000, False, "openai"),
                                                       (9000000, False, "openai")])
            self.settings.entitlement_owner_authority_secret_file = str(Path(folder) / "missing")
            rows = self.report(tasks=[task_id])["tasks"][task_id]["budgets"]
            self.assertEqual({row["signature_verified"] for row in rows}, {False})

    def test_usage_receipts_are_verified_with_the_route_verifier(self):
        import hmac
        import tempfile
        from director_orchestrator.entitlement_contracts import canonical_json_bytes
        task_id = self.intake()
        secret = "fixture-route-verifier-secret-0123456789abcdefghijk"

        def receipt(attempt, grant, actual, key=secret):
            document = {"schema_version": "primary-provider-usage/v1", "attempt_digest": attempt,
                        "authority_digest": grant, "provider_id": "claude", "actual_microusd": actual,
                        "observed_tokens": 100, "complete": True}
            document["meter_signature"] = hmac.new(key.encode(), canonical_json_bytes(document), hashlib.sha256).hexdigest()
            return document

        attempts = {"sha256:" + "1" * 64: (receipt("sha256:" + "1" * 64, "c" * 64, 300000), 300000),
                    "sha256:" + "2" * 64: (receipt("sha256:" + "2" * 64, "c" * 64, 300000, key=secret[::-1]), 300000),
                    "sha256:" + "3" * 64: (receipt("sha256:" + "3" * 64, "d" * 64, 300000), 300000),
                    "sha256:" + "4" * 64: (receipt("sha256:" + "4" * 64, "c" * 64, 300000), 0)}
        with self.handle.pool.connection() as connection:
            # Spend rows reference their budget grant (foreign key); the budget document itself is not under test here.
            connection.execute(f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest, document)"
                               " VALUES (%s, %s::jsonb)", ("c" * 64, json.dumps({"task_id": task_id})))
            for attempt, (document, column) in attempts.items():
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_provider_spend(attempt_digest, grant_digest, provider_id,"
                    " project_id, task_id, budget_day, reserved_microusd, token_limit)"
                    " VALUES (%s, %s, 'claude', 'fixture-project', %s, '2026-09-12', 500000, 1000)",
                    (attempt, "c" * 64, task_id))
                connection.execute(
                    f"INSERT INTO {self.schema}.director_primary_provider_usage(attempt_digest, document, actual_microusd,"
                    " observed_tokens) VALUES (%s, %s::jsonb, %s, 100)", (attempt, json.dumps(document), column))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "route-verifier-secret"
            path.write_text(secret, encoding="utf-8")
            self.settings.entitlement_route_verifier_secret_file = str(path)
            entry = self.report(tasks=[task_id])["tasks"][task_id]
        # Signed and bound; wrong key; another grant; cost column differs from the signed receipt.
        self.assertEqual({row["attempt_digest"][-1]: row["usage_verified"] for row in entry["spend"]},
                         {"1": True, "2": False, "3": False, "4": False})
        self.assertEqual(entry["verifier_secrets"], {"route_verifier": True, "owner_authority": False})
        self.assertEqual([row["grant_digest"] for row in entry["spend"]], ["c" * 64] * 4)

    def test_activation_recorded_at_is_the_persisted_row_time(self):
        from director_orchestrator.provider_activation import ACTIVATION_EVIDENCE_VERSION
        with self.handle.pool.connection() as connection:
            connection.execute(
                f"INSERT INTO {self.schema}.director_provider_activation_evidence(provider_id, workspace_id,"
                " provider_version, app_version, runtime_adapter_version, workspace_version, binding_version, surface,"
                " revision, schema_version, stage, evidence_id, evidence_digest, observed_at, expires_at, recorded_at)"
                " VALUES ('claude','w','p','a','r','w','b','runtime_adapter',1,%s,'LIVE_OK','e',%s,"
                " '2020-01-01T00:00:00Z','2099-01-01T00:00:00Z','2026-09-12T10:00:10Z')",
                (ACTIVATION_EVIDENCE_VERSION, "a" * 64))
            # The same digest under an older workspace binding, at a higher revision and an earlier time.
            connection.execute(
                f"INSERT INTO {self.schema}.director_provider_activation_evidence(provider_id, workspace_id,"
                " provider_version, app_version, runtime_adapter_version, workspace_version, binding_version, surface,"
                " revision, schema_version, stage, evidence_id, evidence_digest, observed_at, expires_at, recorded_at)"
                " VALUES ('claude','old','p','a','r','w','b','runtime_adapter',7,%s,'LIVE_OK','e',%s,"
                " '2020-01-01T00:00:00Z','2099-01-01T00:00:00Z','2026-09-12T08:00:00Z')",
                (ACTIVATION_EVIDENCE_VERSION, "a" * 64))
        current = SimpleNamespace(provider_id="claude", workspace_id="w", provider_version="p", app_version="a",
                                  runtime_adapter_version="r", workspace_version="w", binding_version="b")
        bound = {"identity": self.identity}
        recorded = evidence._activation_recorded_at(TEST_DSN, self.schema, current, "a" * 64, **bound)
        self.assertEqual(datetime.fromisoformat(recorded), datetime(2026, 9, 12, 10, 0, 10, tzinfo=timezone.utc))
        self.assertIsNone(evidence._activation_recorded_at(TEST_DSN, self.schema, current, "b" * 64, **bound))
        # The same digest on the other surface is another row.
        self.assertIsNone(evidence._activation_recorded_at(TEST_DSN, self.schema, current, "a" * 64, "app", **bound))
        # Another database instance behind the same DSN refuses the read.
        from director_orchestrator.langgraph_postgres import PostgresCheckpointConfigurationError
        other = SimpleNamespace(**{**vars(self.identity), "database_instance_id": str(uuid.uuid4())})
        with self.assertRaises(PostgresCheckpointConfigurationError):
            evidence._activation_recorded_at(TEST_DSN, self.schema, current, "a" * 64, identity=other)

    def test_consistency_read_refuses_another_database_instance(self):
        from director_orchestrator.langgraph_postgres import PostgresCheckpointConfigurationError
        self.intake()
        accepted = self.identity
        for field, value in (("database_instance_id", str(uuid.uuid4())), ("database_identity", "postgres:sha256:" + "e" * 64)):
            self.identity = SimpleNamespace(**{**vars(accepted), field: value})
            with self.subTest(field=field), self.assertRaises(PostgresCheckpointConfigurationError):
                self.report()
        self.identity = accepted
        self.assertEqual(self.report()["counts"]["graph_task_count"], 1)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:00 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:32 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 20:48 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:58 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:58 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:37 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:46 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:16 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:38 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 05:01 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:57 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:16 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:32 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:36 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 03:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 04:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 05:07 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 06:39 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 07:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 09:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 10:29 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 11:20 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 13:51 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 15:16 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:49 UTC | END]]
