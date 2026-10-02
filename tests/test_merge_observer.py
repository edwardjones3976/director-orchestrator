# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
"""The merge observer signs challenges; nothing else can.

Covers the phase 3 contract: the observer process answers the boundary's
fresh challenge with an Ed25519-signed observation; the boundary accepts a
genuine one and refuses a forged signature, a wrong key, a replayed or stale
observation; the observer binds loopback only; the construction sites fail
closed without configuration; and the private key never reaches the merging
process. All keys are throwaway and generated in memory.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from director_orchestrator.config import Settings
from director_orchestrator.hotl_execution import (
    MergeExecutionBoundary,
    MergeObservation,
    Outcome,
    ProposedAction,
)
from director_orchestrator.merge_eligibility import MergeEligibilityPolicy
from director_orchestrator.merge_observer import (
    MergeObserverClient,
    MergeObserverService,
    build_standing_merge_boundary,
    load_observer_private_key,
    observation_document,
    observation_from_document,
    build_observer_handler,
    MAX_REQUEST_BYTES,
    serve_merge_observer,
)
from director_orchestrator.oracle_merge import (
    GitHubOracleGateway,
    OracleChangedFile,
    OracleCheckRun,
    OraclePullRequestSnapshot,
)

HEAD = "a" * 40
BASE = "d" * 40


class FakeObserverGateway:
    """Live-read double: one independent ACCEPT, one security APPROVED."""

    def __init__(self, *, base_protected: bool = True):
        self.base_protected = base_protected
        self.snapshot_calls = 0
        self.review_calls = 0

    def snapshot(self, repository: str, pull_number: int) -> OraclePullRequestSnapshot:
        self.snapshot_calls += 1
        return OraclePullRequestSnapshot(
            repository=repository,
            pull_number=pull_number,
            title="Bounded change",
            body="",
            state="open",
            draft=False,
            mergeable=True,
            base_ref="main",
            base_sha=BASE,
            head_ref="candidate/feature",
            head_sha=HEAD,
            head_repository=repository,
            author_login="author-1",
            changed_files=[
                OracleChangedFile(
                    filename="src/module.py",
                    status="added",
                    additions=1,
                    patch="@@ -0,0 +1 @@\n+x = 1",
                )
            ],
            checks=[
                OracleCheckRun(
                    run_id=1, name="ci", status="completed",
                    conclusion="success", head_sha=HEAD, app_slug="github-actions",
                ),
                OracleCheckRun(
                    run_id=2, name="security", status="completed",
                    conclusion="success", head_sha=HEAD, app_slug="github-actions",
                ),
            ],
            base_protected=self.base_protected,
        )

    def list_reviews(self, repository: str, pull_number: int) -> list[dict]:
        self.review_calls += 1
        return [
            {
                "id": 1, "state": "APPROVED", "commit_id": HEAD,
                "user": {"login": "reviewer-1"},
            },
            {
                "id": 2, "state": "APPROVED", "commit_id": HEAD,
                "user": {"login": "security-1"},
            },
        ]


class MergeObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 9, 27, 6, 39, tzinfo=timezone.utc)
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.gateway = FakeObserverGateway()
        self.service = MergeObserverService(
            gateway=self.gateway,
            private_key=self.private_key,
            repository="example/demo",
            environment="test",
            assignment_id="ASSIGN-1",
            producer_principal="author-1",
            security_reviewer_principals=frozenset({"security-1"}),
            clock=lambda: self.now,
        )
        self.action = ProposedAction(
            "eligible_merge_protected_branch", "example/demo", "test", HEAD,
            "reviewer-1",
        )
        self.merge_calls = []

    def merge(self, **kwargs):
        self.merge_calls.append(kwargs)
        return "e" * 40

    def boundary(self, *, observe, public_key=None, audit="standing.sqlite3"):
        return MergeExecutionBoundary(
            repository="example/demo",
            environment="test",
            assignment_id="ASSIGN-1",
            policy=MergeEligibilityPolicy(
                allowed_base_refs=("main",),
                allowed_path_globs=("src/*",),
                required_check_names=("ci", "security"),
                required_branch_prefix="candidate/",
                kill_switch_engaged=False,
            ),
            prohibited_paths=("src/private/*",),
            observer_public_key=public_key or self.public_key,
            observe=observe,
            merge=self.merge,
            audit_path=str(Path(self.temp.name) / audit),
            clock=lambda: self.now,
        )

    def test_the_observer_answers_the_fresh_challenge_and_signs(self):
        observation, signature = self.service.signed_observe(
            self.action, 1, "challenge-material"
        )
        self.assertEqual(observation.challenge, "challenge-material")
        self.assertEqual(observation.merging_principal, "reviewer-1")
        self.assertEqual(observation.bundle.repository, "example/demo")
        # The signature verifies under the public key over the exact payload.
        self.private_key.public_key().verify(
            bytes.fromhex(signature), observation.payload()
        )

    def test_the_boundary_accepts_a_genuine_observation_and_merges(self):
        boundary = self.boundary(observe=self.service.signed_observe)
        decision = boundary.execute(self.action, 1)
        self.assertIs(decision.outcome, Outcome.EXECUTE, decision.blockers)
        self.assertEqual(self.merge_calls, [{
            "repository": "example/demo", "pull_number": 1,
            "expected_head_sha": HEAD, "expected_base_sha": BASE,
            "acting_identity": "reviewer-1", "allow_bypass": False,
        }])

    def test_a_forged_signature_is_refused(self):
        observation, signature = self.service.signed_observe(self.action, 1, "c")
        forged = "0" * 128
        self.assertNotEqual(signature, forged)
        boundary = self.boundary(
            observe=lambda *args: (observation, forged), audit="forged.sqlite3"
        )
        decision = boundary.execute(self.action, 1)
        self.assertIs(decision.outcome, Outcome.HOLD)
        # The boundary never echoes the signature check's detail, only that
        # the read was refused; the hold is what matters.
        self.assertIn("refused or result ambiguous", decision.blockers[0])
        self.assertEqual(self.merge_calls, [])

    def test_a_tampered_payload_is_refused(self):
        # A signature over different bytes: flip the head SHA after signing.
        observation, signature = self.service.signed_observe(self.action, 1, "c")
        from dataclasses import replace as _replace

        tampered = _replace(
            observation,
            bundle=_replace(observation.bundle, candidate_head_sha="f" * 40),
        )
        boundary = self.boundary(
            observe=lambda *args: (tampered, signature), audit="tampered.sqlite3"
        )
        self.assertIs(boundary.execute(self.action, 1).outcome, Outcome.HOLD)

    def test_an_observation_from_the_wrong_key_is_refused(self):
        other_service = MergeObserverService(
            gateway=self.gateway,
            private_key=Ed25519PrivateKey.generate(),
            repository="example/demo",
            environment="test",
            assignment_id="ASSIGN-1",
            producer_principal="author-1",
            security_reviewer_principals=frozenset({"security-1"}),
            clock=lambda: self.now,
        )
        boundary = self.boundary(
            observe=other_service.signed_observe, audit="wrong-key.sqlite3"
        )
        decision = boundary.execute(self.action, 1)
        self.assertIs(decision.outcome, Outcome.HOLD)
        self.assertIn("refused or result ambiguous", decision.blockers[0])

    def test_a_replayed_observation_is_refused(self):
        stale_pair = self.service.signed_observe(self.action, 1, "old-challenge")
        boundary = self.boundary(
            observe=lambda *args: stale_pair, audit="replay.sqlite3"
        )
        decision = boundary.execute(self.action, 1)
        self.assertIs(decision.outcome, Outcome.HOLD)
        # The challenge mismatch is refused before any signature-only path
        # could matter; the hold names the refusal, never a detail echo.
        self.assertIn("refused or result ambiguous", decision.blockers[0])
        self.assertEqual(self.merge_calls, [])

    def test_a_stale_observation_is_refused(self):
        stale_service = MergeObserverService(
            gateway=self.gateway,
            private_key=self.private_key,
            repository="example/demo",
            environment="test",
            assignment_id="ASSIGN-1",
            producer_principal="author-1",
            security_reviewer_principals=frozenset({"security-1"}),
            clock=lambda: self.now - timedelta(seconds=60),
        )
        boundary = self.boundary(
            observe=stale_service.signed_observe, audit="stale.sqlite3"
        )
        decision = boundary.execute(self.action, 1)
        self.assertIs(decision.outcome, Outcome.HOLD)
        self.assertTrue(any("stale" in b for b in decision.blockers))

    def test_the_signed_channel_round_trips_over_loopback_http(self):
        server = serve_merge_observer(self.service, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = MergeObserverClient(
            f"http://127.0.0.1:{server.server_address[1]}"
        )
        self.addCleanup(client.close)
        boundary = self.boundary(observe=client.observe, audit="http.sqlite3")
        decision = boundary.execute(self.action, 1)
        self.assertIs(decision.outcome, Outcome.EXECUTE, decision.blockers)
        self.assertEqual(self.gateway.snapshot_calls, 2)
        self.assertEqual(self.gateway.review_calls, 2)

    def test_the_observer_refuses_a_non_loopback_bind(self):
        for host in ("0.0.0.0", "192.168.1.4", "example.invalid"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    serve_merge_observer(self.service, host=host, port=0)

    def test_the_client_refuses_a_non_loopback_endpoint(self):
        for endpoint in (
            "http://0.0.0.0:8123",
            "http://192.168.1.4:8123",
            "https://127.0.0.1:8123",
            "http://127.0.0.1",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    MergeObserverClient(endpoint)

    def test_the_server_refuses_malformed_requests_without_detail(self):
        import httpx

        server = serve_merge_observer(self.service, host="127.0.0.1", port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with httpx.Client() as client:
            def delayed_body():
                time.sleep(0.06)
                yield b"{}"

            wrong_path = client.post(
                f"{base}/other", content=delayed_body(),
                headers={"Content-Length": "2"},
            )
            self.assertEqual(wrong_path.status_code, 400)
            self.assertNotIn(b"Traceback", wrong_path.content)
            malformed = client.post(
                f"{base}/observe", content=b"not-json"
            )
            self.assertEqual(malformed.status_code, 400)

    def test_wrong_path_drain_has_a_total_deadline_and_byte_bound(self):
        from unittest.mock import Mock, patch

        handler_type = build_observer_handler(self.service)
        for length, ticks, reads, waits in (
            ("10000", [0.0, 0.25, 0.75, 1.25], 2, [0.75, 0.25]),
            (str(MAX_REQUEST_BYTES + 1), [0.0], 0, []),
            ("invalid", [0.0], 0, []),
        ):
            with self.subTest(length=length):
                handler = object.__new__(handler_type)
                handler.path = "/other"
                handler.headers = {"Content-Length": length}
                handler.connection = Mock()
                handler.connection.gettimeout.return_value = None
                handler.rfile = Mock()
                handler.rfile.read1.return_value = b"x"
                handler._refuse = Mock()
                with patch("director_orchestrator.merge_observer.time.monotonic", side_effect=ticks):
                    handler.do_POST()
                self.assertEqual(handler.rfile.read1.call_count, reads)
                self.assertEqual(
                    [call.args[0] for call in handler.connection.settimeout.call_args_list], waits + [None]
                )
                for call in handler.rfile.read1.call_args_list:
                    self.assertLessEqual(call.args[0], 8192)
                handler._refuse.assert_called_once_with("merge observer serves only /observe")
                self.assertEqual(self.gateway.snapshot_calls, 0)

    def test_observation_documents_round_trip_exactly(self):
        observation, _ = self.service.signed_observe(self.action, 1, "round-trip")
        rebuilt = observation_from_document(observation_document(observation))
        self.assertEqual(rebuilt.payload(), observation.payload())

    def test_malformed_documents_are_refused(self):
        for document in (None, [], {}, {"bundle": {}}):
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    observation_from_document(document)

    def test_the_private_key_never_reaches_the_merging_process(self):
        """Only the observer module may hold or load the private key.

        The boundary and the merge transport verify with the public key; the
        boundary builder reads only the public-key path. These are structural
        guarantees, so the test pins the source itself.
        """

        import inspect

        from director_orchestrator import (
            hotl_execution,
            merge_observer,
            oracle_merge,
        )

        boundary_source = inspect.getsource(hotl_execution)
        transport_source = inspect.getsource(oracle_merge)
        self.assertNotIn("Ed25519PrivateKey", boundary_source)
        self.assertNotIn("Ed25519PrivateKey", transport_source)
        self.assertNotIn("from_private_bytes", boundary_source)
        builder_source = inspect.getsource(
            merge_observer.build_standing_merge_boundary
        )
        self.assertNotIn("oracle_observer_private_key_path", builder_source)
        # And the only loader of the private-key setting is this module.
        module_source = inspect.getsource(merge_observer)
        self.assertEqual(module_source.count("oracle_observer_private_key_path"), 0)
        self.assertIn(
            "Ed25519PrivateKey",
            inspect.getsource(load_observer_private_key),
        )

    def test_private_key_loading_names_the_file_never_the_material(self):
        missing = str(Path(self.temp.name) / "absent.key")
        with self.assertRaisesRegex(ValueError, "unreadable"):
            load_observer_private_key(missing)
        wrong_size = Path(self.temp.name) / "wrong-size.key"
        wrong_size.write_bytes(b"\x01\x02\x03")  # not key material, a length probe
        with self.assertRaisesRegex(ValueError, "32-byte"):
            load_observer_private_key(str(wrong_size))

    def a_settings(self, **overrides) -> Settings:
        public_key_file = Path(self.temp.name) / "observer.pub"
        public_key_file.write_bytes(self.public_key)
        values = dict(
            sqlite_path=str(Path(self.temp.name) / "oracle.sqlite3"),
            oracle_automerge_repository="example/demo",
            oracle_automerge_base_ref="main",
            oracle_automerge_branch_prefix="candidate/",
            oracle_automerge_allowed_paths=("src/**",),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_observer_endpoint="http://127.0.0.1:8901",
            oracle_observer_public_key_path=str(public_key_file),
            oracle_standing_merge_audit_path=str(
                Path(self.temp.name) / "standing-audit.sqlite3"
            ),
            oracle_standing_merge_environment="test",
            oracle_standing_merge_assignment_id="ASSIGN-1",
        )
        values.update(overrides)
        return Settings(**values)

    def github(self) -> GitHubOracleGateway:
        return GitHubOracleGateway(token="test-only-token")

    def test_the_construction_site_builds_a_boundary_from_settings(self):
        settings = self.a_settings()
        boundary = build_standing_merge_boundary(settings, github=self.github())
        self.assertEqual(boundary.repository, "example/demo")
        self.assertEqual(boundary.assignment_id, "ASSIGN-1")
        self.assertEqual(boundary.environment, "test")
        self.assertIsInstance(boundary, MergeExecutionBoundary)

    def test_the_construction_site_fails_closed_on_each_missing_value(self):
        full = {
            "oracle_observer_endpoint": "http://127.0.0.1:8901",
            "oracle_observer_public_key_path": "set",
            "oracle_standing_merge_audit_path": "set",
            "oracle_standing_merge_environment": "test",
            "oracle_standing_merge_assignment_id": "ASSIGN-1",
        }
        for field in full:
            with self.subTest(missing=field):
                overrides = {field: ""}
                settings = self.a_settings(**overrides)
                with self.assertRaisesRegex(RuntimeError, "not configured"):
                    build_standing_merge_boundary(settings, github=self.github())

    def test_the_construction_site_fails_closed_on_an_invalid_public_key(self):
        bad = Path(self.temp.name) / "bad.pub"
        bad.write_bytes(b"\x00" * 31)  # length probe, not key material
        settings = self.a_settings(oracle_observer_public_key_path=str(bad))
        with self.assertRaisesRegex(RuntimeError, "32-byte Ed25519"):
            build_standing_merge_boundary(settings, github=self.github())

        settings = self.a_settings(
            oracle_observer_public_key_path=str(Path(self.temp.name) / "absent.pub")
        )
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            build_standing_merge_boundary(settings, github=self.github())

    def test_the_construction_site_fails_closed_without_scope_or_gateway(self):
        settings = self.a_settings(oracle_automerge_base_ref="")
        with self.assertRaisesRegex(RuntimeError, "ORACLE_AUTOMERGE_BASE_REF"):
            build_standing_merge_boundary(settings, github=self.github())
        with self.assertRaisesRegex(RuntimeError, "GitHub gateway"):
            build_standing_merge_boundary(self.a_settings(), github=None)

    def test_the_construction_site_refuses_a_remote_observer_endpoint(self):
        settings = self.a_settings(
            oracle_observer_endpoint="http://192.168.1.4:8901"
        )
        # The builder normalizes every refusal to one fail-closed error type
        # for the construction sites, naming the loopback requirement.
        with self.assertRaisesRegex(RuntimeError, "loopback"):
            build_standing_merge_boundary(settings, github=self.github())


if __name__ == "__main__":
    unittest.main()
# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]
