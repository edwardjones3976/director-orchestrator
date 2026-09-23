# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:50 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 22:42 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-21 | 22:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:54 UTC | BEGIN]]
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from http.server import ThreadingHTTPServer
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from director_orchestrator.models import AntigravityDirective, AntigravityFixtureLane
from director_orchestrator.packet_routing import antigravity_directive_objective
from director_orchestrator.primary_transports import (
    antigravity_closed_payload,
    normalize_native_provider_envelope,
)
from director_orchestrator.primary_paid_preflight import PlannedProviderAttempt
from director_orchestrator.host_provider_bridge import (
    BRIDGE_PROTOCOL,
    BridgeClaimStore,
    BridgeRequest,
    HostProviderBridgeAmbiguous,
    HostProviderBridgeError,
    HostBridgePacketTransport,
    PostgresHostWriterFenceVerifier,
    WindowsHostProviderExecutor,
    _NoRedirectHandler,
    _digest,
    bridge_signature,
    bridge_response_signature,
    bridge_challenge_signature,
    challenge_host_provider_bridge,
    dispatch_bridge_request,
)


SECRET = "bridge-test-secret-that-is-longer-than-32-characters"


def request(*, nonce: str = "nonce-1", packet_key: str = "effect-key-1") -> BridgeRequest:
    packet = json.dumps(
        {
            "task_id": "task-1",
            "assignment_id": "assignment-1",
            "idempotency_key": packet_key,
        },
        separators=(",", ":"),
    ).encode()
    bindings = {
        "timestamp": 1_000,
        "nonce": nonce,
        "provider": "claude",
        "task_id": "task-1",
        "assignment_id": "assignment-1",
        "idempotency_digest": _digest(packet_key.encode()),
        "writer_generation": 7,
        "packet_digest": _digest(packet),
        "writer_owner_token_digest": "sha256:" + "f" * 64,
    }
    return BridgeRequest(
        **bindings,
        signature=bridge_signature(SECRET, **bindings),
        packet=packet,
    )


class _Executor:
    def __init__(self, *, ambiguous: bool = False) -> None:
        self.calls = 0
        self.ambiguous = ambiguous

    def execute(self, request: BridgeRequest) -> bytes:
        self.calls += 1
        if self.ambiguous:
            raise HostProviderBridgeAmbiguous("ambiguous")
        return b'{"status":"SUCCEEDED"}'


class _Verifier:
    def __init__(self, *, reject: bool = False) -> None:
        self.calls = 0
        self.reject = reject

    def verify(self, request: BridgeRequest) -> None:
        self.calls += 1
        if self.reject:
            raise HostProviderBridgeError("stale writer")


class _PostgresResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _PostgresConnection:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, parameters):
        if "director_primary_task_writer" not in statement:
            raise AssertionError("writer verification queried the wrong authority")
        if parameters != ("task-1",):
            raise AssertionError("writer verification queried the wrong task")
        return _PostgresResult(self.row)


class _ChallengeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


class HostProviderBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = BridgeClaimStore(Path(self.temporary.name) / "bridge.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_writer_digest_requires_lowercase_sha256_at_both_boundaries(self) -> None:
        endpoint = "http://127.0.0.1:18765/v1/provider/dispatch"
        for digest in (
            "sha256:" + "z" * 64,
            "sha256:" + "A" * 64,
            "sha256:" + "a" * 63,
            "sha256:" + "a" * 65,
            "sha256:" + "a" * 63 + "\n",
            "SHA256:" + "a" * 64,
        ):
            with self.subTest(digest=digest):
                candidate = replace(request(), writer_owner_token_digest=digest)
                # Sign the malformed value so rejection proves shape validation,
                # not merely a mismatched authentication signature.
                bindings = {name: getattr(candidate, name) for name in (
                    "timestamp", "nonce", "provider", "task_id", "assignment_id",
                    "idempotency_digest", "writer_generation", "packet_digest",
                    "writer_owner_token_digest",
                )}
                candidate = replace(candidate, signature=bridge_signature(SECRET, **bindings))
                executor = _Executor()
                with self.assertRaisesRegex(HostProviderBridgeError, "writer owner token digest"):
                    dispatch_bridge_request(candidate, secret=SECRET, store=self.store,
                                            executor=executor, now=1_000)
                self.assertEqual(executor.calls, 0)
                with self.assertRaisesRegex(ValueError, "writer owner token digest"):
                    HostBridgePacketTransport("claude", endpoint, SECRET,
                                              writer_owner_token_digest=digest)
        valid = request()
        valid.validate(SECRET, now=1_000)
        transport = HostBridgePacketTransport("claude", endpoint, SECRET,
                                              writer_owner_token_digest=valid.writer_owner_token_digest)
        self.assertEqual(transport.writer_owner_token_digest, valid.writer_owner_token_digest)

    def test_success_is_replayed_without_another_provider_attempt(self) -> None:
        executor = _Executor()
        first = dispatch_bridge_request(
            request(), secret=SECRET, store=self.store, executor=executor, now=1_000
        )
        second = dispatch_bridge_request(
            request(nonce="nonce-2"),
            secret=SECRET,
            store=self.store,
            executor=executor,
            now=1_000,
        )
        self.assertEqual(first, second)
        self.assertEqual(1, executor.calls)

    def test_ambiguous_attempt_is_durable_and_never_retried(self) -> None:
        executor = _Executor(ambiguous=True)
        with self.assertRaises(HostProviderBridgeAmbiguous):
            dispatch_bridge_request(
                request(), secret=SECRET, store=self.store, executor=executor, now=1_000
            )
        with self.assertRaises(HostProviderBridgeAmbiguous):
            dispatch_bridge_request(
                request(nonce="nonce-2"),
                secret=SECRET,
                store=self.store,
                executor=executor,
                now=1_000,
            )
        self.assertEqual(1, executor.calls)

    def test_rejects_nonce_replay_writer_drift_and_unsupported_lanes(self) -> None:
        valid = request()
        valid.validate(SECRET, now=1_000)
        self.store.claim(valid)
        with self.assertRaisesRegex(HostProviderBridgeError, "nonce replay"):
            self.store.claim(valid)
        changed = request(nonce="nonce-3")
        object.__setattr__(changed, "writer_generation", 8)
        with self.assertRaisesRegex(HostProviderBridgeError, "authentication"):
            changed.validate(SECRET, now=1_000)
        unsupported = request(nonce="nonce-4")
        object.__setattr__(unsupported, "provider", "microsoft-365-copilot")
        with self.assertRaisesRegex(HostProviderBridgeError, "unallowlisted"):
            unsupported.validate(SECRET, now=1_000)

    def test_executor_uses_one_shell_false_attempt(self) -> None:
        verifier = _Verifier()
        executor = WindowsHostProviderExecutor(
            {"claude": ("claude.exe", "--print")},
            cwd=self.temporary.name,
            writer_fence_verifier=verifier,
        )
        completed = subprocess.CompletedProcess(
            args=("claude.exe", "--print"),
            returncode=0,
            stdout=b'{"status":"SUCCEEDED"}',
            stderr=b"",
        )
        environment = {
            "SystemRoot": "C:\\Windows",
            "ANTHROPIC_API_KEY": "lane-auth",
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET": "must-not-leak",
            "ORCHESTRATOR_SHARED_SECRET": "must-not-leak",
            "CLAUDE_CALLBACK_SECRET": "must-not-leak",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "subprocess.run", return_value=completed
        ) as run:
            self.assertEqual(b'{"status":"SUCCEEDED"}', executor.execute(request()))
        self.assertEqual(1, verifier.calls)
        self.assertEqual(1, run.call_count)
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertIs(run.call_args.kwargs["check"], False)
        child_environment = {
            key.upper(): value for key, value in run.call_args.kwargs["env"].items()
        }
        self.assertEqual(
            {"SYSTEMROOT": "C:\\Windows", "ANTHROPIC_API_KEY": "lane-auth"},
            child_environment,
        )

    def test_executor_refuses_io_without_current_writer_verification(self) -> None:
        for verifier in (None, _Verifier(reject=True)):
            executor = WindowsHostProviderExecutor(
                {"claude": ("claude.exe",)},
                cwd=self.temporary.name,
                writer_fence_verifier=verifier,
            )
            with patch("subprocess.run") as run, self.assertRaises(
                HostProviderBridgeError
            ):
                executor.execute(request())
            run.assert_not_called()

    ANTIGRAVITY_ENDPOINT = "http://127.0.0.1:18765/v1/provider/dispatch"

    def _antigravity_work(self):
        directive = AntigravityDirective(lane=AntigravityFixtureLane.MODULE_SKELETON)
        return {
            "schema_version": "1.0",
            "task_id": "DIR-LG-BRIDGE",
            "assignment_id": "DIR-LG-BRIDGE-5",
            "idempotency_key": "DIR-LG-BRIDGE:1:5:aa11bb22",
            "project": "confidential-project",
            "repository": "owner/confidential-repository",
            "starting_ref": "c" * 40,
            "authority_base_ref": "release-candidate",
            "objective": antigravity_directive_objective(directive),
            "authority_snapshot": [
                {
                    "path": "docs/confidential-design.md",
                    "commit_sha": "c" * 40,
                    "blob_sha": "d" * 40,
                }
            ],
            "authority_snapshot_digest": "sha256:" + "e" * 64,
            "owned_paths": ["src/confidential_module"],
            "prohibited_paths": ["docs/private-evidence"],
            "constraints": ["Confine every repository write to src/confidential_module."],
            "acceptance_checks": ["Return bounded evidence for the assigned lane"],
            "correlation": {
                "correlation_id": "corr-bridge", "source_event_id": "source",
                "slack_thread_ts": None, "github_issue": None,
                "github_pull_request": None,
            },
            "allowed_external_effects": [],
        }

    def _antigravity_transport(self):
        return HostBridgePacketTransport(
            "antigravity",
            self.ANTIGRAVITY_ENDPOINT,
            SECRET,
            writer_owner_token_digest="sha256:" + "f" * 64,
            outbound_payload_builder=antigravity_closed_payload,
            result_normalizer=partial(
                normalize_native_provider_envelope, "antigravity"
            ),
        )

    @staticmethod
    def _bridge_response(body: bytes, nonce: str, idempotency_digest: str):
        response = MagicMock()
        response.read.return_value = body
        response.headers = {
            "X-Director-Response-Digest": _digest(body),
            "X-Director-Response-Proof": bridge_response_signature(
                SECRET,
                nonce=nonce,
                idempotency_digest=idempotency_digest,
                response_digest=_digest(body),
            ),
        }
        response.__enter__ = lambda self_: self_
        response.__exit__ = lambda *_args: False
        return response

    def _send_antigravity(self, native):
        work = self._antigravity_work()
        transport = self._antigravity_transport()
        captured: dict[str, object] = {}
        body = json.dumps(native, separators=(",", ":")).encode()

        def open_request(request, timeout=None):
            captured["url"] = request.full_url
            captured["data"] = request.data
            captured["headers"] = dict(request.headers)
            nonce = request.headers["X-director-bridge-nonce"]
            idempotency = request.headers["X-director-idempotency-digest"]
            return self._bridge_response(body, nonce, idempotency)

        opener = MagicMock()
        opener.open.side_effect = open_request
        with patch(
            "director_orchestrator.host_provider_bridge.urllib.request.build_opener",
            return_value=opener,
        ):
            result = transport.send(
                json.dumps(work, separators=(",", ":")).encode(),
                context=SimpleNamespace(timeout_seconds=30, fencing_generation=7),
            )
        return work, captured, result

    def test_antigravity_bridge_body_carries_only_the_closed_directive(self) -> None:
        native = {
            "session_id": "antigravity-bridge-session",
            "result": '{"outcome":"generic module skeleton","status":"SUCCEEDED"}',
        }

        work, captured, result = self._send_antigravity(native)

        sent = captured["data"].decode("utf-8")
        for confidential in (
            work["repository"],
            work["project"],
            work["starting_ref"],
            work["authority_base_ref"],
            work["authority_snapshot_digest"],
            work["authority_snapshot"][0]["path"],
            work["authority_snapshot"][0]["blob_sha"],
            work["owned_paths"][0],
            work["prohibited_paths"][0],
            work["constraints"][0],
            work["acceptance_checks"][0],
        ):
            self.assertNotIn(confidential, sent)
        self.assertEqual(
            antigravity_closed_payload(work), json.loads(sent)
        )

        # The signature and the posted digest bind the exact redacted bytes,
        # and the original packet is carried only as an opaque digest.
        headers = captured["headers"]
        self.assertEqual(_digest(captured["data"]), headers["X-director-packet-digest"])
        self.assertEqual(
            _digest(json.dumps(work, separators=(",", ":")).encode()),
            headers["X-director-work-packet-digest"],
        )
        self.assertNotEqual(
            headers["X-director-packet-digest"],
            headers["X-director-work-packet-digest"],
        )
        self.assertEqual("7", headers["X-director-writer-generation"])

        # Normalization still binds the result to the private work packet.
        self.assertEqual("antigravity", result["provider"])
        self.assertEqual(work["task_id"], result["task_id"])
        self.assertEqual(work["assignment_id"], result["assignment_id"])
        self.assertEqual(work["idempotency_key"], result["idempotency_key"])
        self.assertEqual(
            work["authority_snapshot_digest"], result["authority_snapshot_digest"]
        )
        self.assertEqual(work["correlation"], result["correlation"])
        self.assertEqual("antigravity-bridge-session", result["run_reference"])
        self.assertEqual("NOT_RUN", result["acceptance"][0]["status"])

    def test_redacted_bridge_request_authenticates_its_origin_binding(self) -> None:
        native = {
            "session_id": "antigravity-bridge-session",
            "result": '{"outcome":"generic module skeleton","status":"SUCCEEDED"}',
        }
        _work, captured, _result = self._send_antigravity(native)
        headers = captured["headers"]
        bindings = dict(
            timestamp=int(headers["X-director-bridge-timestamp"]),
            nonce=headers["X-director-bridge-nonce"],
            provider="antigravity",
            task_id=headers["X-director-task"],
            assignment_id=headers["X-director-assignment"],
            idempotency_digest=headers["X-director-idempotency-digest"],
            writer_generation=7,
            packet_digest=headers["X-director-packet-digest"],
            writer_owner_token_digest=headers["X-director-writer-owner-token-digest"],
            work_packet_digest=headers["X-director-work-packet-digest"],
        )
        signature = headers["Authorization"].removeprefix("Director-HMAC ")
        accepted = BridgeRequest(
            **bindings, signature=signature, packet=captured["data"]
        )
        accepted.validate(SECRET, now=bindings["timestamp"])

        # Stripping the origin binding invalidates the same signature.
        stripped = replace(accepted, work_packet_digest="")
        with self.assertRaisesRegex(HostProviderBridgeError, "authentication failed"):
            stripped.validate(SECRET, now=bindings["timestamp"])
        forged = replace(accepted, work_packet_digest="a" * 64)
        with self.assertRaisesRegex(HostProviderBridgeError, "authentication failed"):
            forged.validate(SECRET, now=bindings["timestamp"])
        malformed = replace(accepted, work_packet_digest="not-a-digest")
        with self.assertRaisesRegex(HostProviderBridgeError, "origin work-packet digest"):
            malformed.validate(SECRET, now=bindings["timestamp"])

    def test_unredacted_bridge_lanes_are_byte_identical(self) -> None:
        # Other lanes keep the work packet and the pre-existing signature payload.
        work = {
            "task_id": "task-1",
            "assignment_id": "assignment-1",
            "idempotency_key": "effect-key-1",
        }
        encoded = json.dumps(work, separators=(",", ":")).encode()
        captured: dict[str, object] = {}
        body = json.dumps({"schema_version": "1.0", "ok": True}, separators=(",", ":")).encode()

        def open_request(request, timeout=None):
            captured["data"] = request.data
            captured["headers"] = dict(request.headers)
            return self._bridge_response(
                body,
                request.headers["X-director-bridge-nonce"],
                request.headers["X-director-idempotency-digest"],
            )

        opener = MagicMock()
        opener.open.side_effect = open_request
        transport = HostBridgePacketTransport(
            "claude",
            self.ANTIGRAVITY_ENDPOINT,
            SECRET,
            writer_owner_token_digest="sha256:" + "f" * 64,
        )
        with patch(
            "director_orchestrator.host_provider_bridge.urllib.request.build_opener",
            return_value=opener,
        ):
            result = transport.send(
                encoded,
                context=SimpleNamespace(timeout_seconds=30, fencing_generation=7),
            )

        self.assertEqual(encoded, captured["data"])
        self.assertEqual(_digest(encoded), captured["headers"]["X-director-packet-digest"])
        self.assertNotIn("X-director-work-packet-digest", captured["headers"])
        # No normalizer is configured, so the host envelope is returned intact.
        self.assertEqual({"schema_version": "1.0", "ok": True}, result)

    def test_bridge_redaction_requires_its_normalizer(self) -> None:
        for builder, normalizer in (
            (antigravity_closed_payload, None),
            (None, partial(normalize_native_provider_envelope, "antigravity")),
        ):
            with self.subTest(builder=builder is not None):
                with self.assertRaisesRegex(ValueError, "requires both"):
                    HostBridgePacketTransport(
                        "antigravity",
                        self.ANTIGRAVITY_ENDPOINT,
                        SECRET,
                        writer_owner_token_digest="sha256:" + "f" * 64,
                        outbound_payload_builder=builder,
                        result_normalizer=normalizer,
                    )

    def test_preflight_refuses_expired_deadlines_without_bridge_io(self) -> None:
        endpoint = "http://127.0.0.1:18765/v1/provider/dispatch"
        transport = HostBridgePacketTransport(
            "claude",
            endpoint,
            SECRET,
            writer_owner_token_digest="sha256:" + "f" * 64,
        )
        packet = json.dumps(
            {"task_id": "task-1", "assignment_id": "assignment-1", "idempotency_key": "k"},
            separators=(",", ":"),
        ).encode()
        context = PlannedProviderAttempt(
            task_id="task-1",
            assignment_id="assignment-1",
            idempotency_key="attempt",
            work_packet_digest="sha256:" + "a" * 64,
            writer_token_digest="sha256:" + "b" * 64,
            fencing_generation=7,
            timeout_seconds=30,
            budget_usd=1.0,
            total_token_limit=1000,
            max_hops=1,
            max_revisions=1,
            allowed_tools=(),
            allowed_external_effects=(),
            deadline=1.0,
        )
        expired = replace(context, deadline=0.0)
        opener = MagicMock()
        with patch(
            "director_orchestrator.host_provider_bridge.urllib.request.build_opener",
            return_value=opener,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "timeout is exhausted"
            ):
                transport.preflight(packet, context=context)
            with self.assertRaisesRegex(
                RuntimeError, "timeout is exhausted"
            ):
                transport.preflight(packet, context=expired)
        opener.open.assert_not_called()

    def test_preflight_keeps_legacy_no_deadline_context_compatible(self) -> None:
        endpoint = "http://127.0.0.1:18765/v1/provider/dispatch"
        transport = HostBridgePacketTransport(
            "claude",
            endpoint,
            SECRET,
            writer_owner_token_digest="sha256:" + "f" * 64,
        )
        packet = json.dumps(
            {"task_id": "task-1", "assignment_id": "assignment-1", "idempotency_key": "k"},
            separators=(",", ":"),
        ).encode()
        transport.preflight(
            packet,
            context=SimpleNamespace(timeout_seconds=30, fencing_generation=7),
        )

    def test_client_requires_encryption_off_loopback_and_disables_redirects(self) -> None:
        valid = "https://host.docker.internal:18765/v1/provider/dispatch"
        transport = HostBridgePacketTransport(
            "claude", valid, SECRET, writer_owner_token_digest="sha256:" + "f" * 64
        )
        self.assertEqual(valid, transport.endpoint)
        loopback = "http://127.0.0.1:18765/v1/provider/dispatch"
        self.assertEqual(
            loopback,
            HostBridgePacketTransport(
                "claude", loopback, SECRET,
                writer_owner_token_digest="sha256:" + "f" * 64,
            ).endpoint,
        )
        for endpoint in (
            "http://host.docker.internal:18765/v1/provider/dispatch",
            "http://user@host.docker.internal:18765/v1/provider/dispatch",
            "http://host.docker.internal/v1/provider/dispatch",
            "http://host.docker.internal:18765/other",
            "http://host.docker.internal:18765/v1/provider/dispatch?q=1",
            "http://example.com:18765/v1/provider/dispatch",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                HostBridgePacketTransport(
                    "claude", endpoint, SECRET,
                    writer_owner_token_digest="sha256:" + "f" * 64,
                )
        redirect = _NoRedirectHandler()
        redirect_request = urllib.request.Request(valid)
        with self.assertRaisesRegex(urllib.error.HTTPError, "redirect refused"):
            redirect.redirect_request(
                redirect_request, None, 302, "Found", {},
                "http://host.docker.internal:18766/v1/provider/dispatch",
            )

    def test_signed_challenge_authenticates_observed_command_map(self) -> None:
        providers = ["claude", "grok"]
        command_map_digest = "sha256:" + _digest(
            json.dumps(providers, separators=(",", ":")).encode()
        )
        class _Opener:
            def open(inner_self, candidate, timeout):
                nonce = candidate.headers["X-director-bridge-nonce"]
                body = {
                    "ok": True,
                    "protocol": "director-host-provider-bridge/v1",
                    "configured_providers": providers,
                    "command_map_digest": command_map_digest,
                    "proof": bridge_challenge_signature(
                        SECRET, timestamp=1_000, nonce=nonce,
                        command_map_digest=command_map_digest,
                    ),
                }
                return _ChallengeResponse(json.dumps(body).encode())

        with patch("urllib.request.build_opener", return_value=_Opener()):
            evidence = challenge_host_provider_bridge(
                "https://host.docker.internal:18765/v1/provider/dispatch",
                SECRET,
                now=1_000,
            )
        self.assertEqual(frozenset(providers), evidence.configured_providers)
        self.assertEqual(command_map_digest, evidence.command_map_digest)

    def test_postgres_writer_fence_requires_exact_live_authority(self) -> None:
        owner_token = "writer-owner-token"
        token_digest = "sha256:" + __import__("hashlib").sha256(
            owner_token.encode("utf-8")
        ).hexdigest()
        candidate = request()
        object.__setattr__(candidate, "writer_owner_token_digest", token_digest)
        verifier = PostgresHostWriterFenceVerifier(
            "postgresql://local/test", schema="director_control"
        )
        cases = {
            "missing": None,
            "wrong owner": ("legacy", owner_token, 7, True),
            "wrong token": ("langgraph", "other-token", 7, True),
            "wrong generation": ("langgraph", owner_token, 8, True),
            "expired": ("langgraph", owner_token, 7, False),
        }
        for label, row in cases.items():
            fake = SimpleNamespace(
                connect=lambda dsn, connect_timeout: _PostgresConnection(row)
            )
            with self.subTest(label=label), patch.dict(
                sys.modules, {"psycopg": fake}
            ), self.assertRaises(HostProviderBridgeError):
                verifier.verify(candidate)
        fake = SimpleNamespace(
            connect=lambda dsn, connect_timeout: _PostgresConnection(
                ("langgraph", owner_token, 7, True)
            )
        )
        with patch.dict(sys.modules, {"psycopg": fake}):
            verifier.verify(candidate)

    def test_postgres_writer_fence_fails_closed_on_database_error(self) -> None:
        verifier = PostgresHostWriterFenceVerifier(
            "postgresql://local/test", schema="director_control"
        )

        def fail_connect(dsn, connect_timeout):
            raise OSError("database unavailable")

        with patch.dict(
            sys.modules, {"psycopg": SimpleNamespace(connect=fail_connect)}
        ), self.assertRaisesRegex(
            HostProviderBridgeError, "verification failed"
        ):
            verifier.verify(request())

    def test_unauthenticated_health_is_protocol_and_configured_roster_only(self) -> None:
        from director_orchestrator import host_provider_bridge_server

        captured: dict[str, object] = {}
        handler = object.__new__(host_provider_bridge_server._Handler)
        handler.path = "/health"
        handler.server = SimpleNamespace(  # type: ignore[attr-defined]
            executor=SimpleNamespace(commands={"claude": ("claude.exe",)})
        )
        handler._json = lambda status, body: captured.update(  # type: ignore[method-assign]
            {"status": status, "body": body}
        )

        handler.do_GET()

        self.assertEqual(200, captured["status"])
        self.assertEqual(
            {
                "ok": True,
                "protocol": BRIDGE_PROTOCOL,
                "configured_providers": ["claude"],
            },
            captured["body"],
        )
        # Stale per-provider booleans are not part of this public surface, and
        # "windows_copilot" is not the canonical lane name either.
        self.assertNotIn("cursor", captured["body"])
        self.assertNotIn("windows_copilot", captured["body"])
        server_source = Path(
            host_provider_bridge_server.__file__
        ).read_text(encoding="utf-8")
        self.assertNotIn("windows_copilot", server_source)

    def test_server_composes_validation_only_from_enabled_profile(self) -> None:
        from director_orchestrator import host_provider_bridge_server as server_module
        from director_orchestrator.provider_validation import ValidationBridgeService

        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET": SECRET,
                "DIRECTOR_HOST_PROVIDER_WORKSPACE": directory,
                "DIRECTOR_HOST_PROVIDER_COMMANDS_JSON": json.dumps({"claude": [sys.executable]}),
                "DIRECTOR_HOST_PROVIDER_BRIDGE_STORE": "unused",
                "DIRECTOR_HOST_PROVIDER_POSTGRES_DSN": "unused",
                "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": "fixture",
            }
            for enabled in (False, True):
                profile = dict(environment, DIRECTOR_PROVIDER_VALIDATION_ENABLED=str(enabled).lower())
                if enabled:
                    profile.update(
                        DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN="unused-validation",
                        DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY="c" * 40,
                        DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN="o" * 40,
                        DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON='{"claude":[]}',
                    )
                server = SimpleNamespace(serve_forever=MagicMock())
                with patch.dict(os.environ, profile, clear=True), \
                     patch.object(server_module, "ThreadingHTTPServer", return_value=server), \
                     patch.object(server_module, "BridgeClaimStore"), \
                     patch("director_orchestrator.provider_validation.PostgresValidationStore") as store:
                    server_module.main()
                server.serve_forever.assert_called_once_with(poll_interval=0.5)
                self.assertEqual(hasattr(server, "validation_service"), enabled)
                if enabled:
                    self.assertIsInstance(server.validation_service, ValidationBridgeService)
                    self.assertIs(server.validation_service.production, server.executor)
                    store.assert_called_once_with("unused-validation")
                else:
                    store.assert_not_called()

    def test_executor_refuses_direct_provider_lanes(self) -> None:
        for provider in ("microsoft-365-copilot",):
            with self.subTest(provider=provider), self.assertRaisesRegex(
                ValueError, "unallowlisted"
            ):
                WindowsHostProviderExecutor(
                    {provider: ("provider.exe",)}, cwd=self.temporary.name
                )


class PinnedProcessEnvironmentTests(unittest.TestCase):
    OWNER = "validation-owner-fixture-" + "o" * 40

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        folders = {}
        for name in ("bin", "tools", "temp", "system", "local", "roaming", "profile"):
            folders[name] = self.root / name
            folders[name].mkdir()
        self.pinned = {
            "PATH": os.pathsep.join((str(folders["bin"]), str(folders["tools"]))),
            "PATHEXT": ".COM;.EXE", "TEMP": str(folders["temp"]), "TMP": str(folders["temp"]),
            "SYSTEMROOT": str(folders["system"]), "WINDIR": str(folders["system"]),
            "COMSPEC": str(folders["system"] / "cmd.exe"), "LOCALAPPDATA": str(folders["local"]),
            "APPDATA": str(folders["roaming"]), "USERPROFILE": str(folders["profile"]),
        }
        self.file = self.root / "native-process-environment.json"
        self.write(self.pinned)
        self.account = self.root / "synthetic-account.json"
        self.account.write_text("synthetic fixture account, not a credential")
        self.executor = WindowsHostProviderExecutor(
            {"claude": (str(Path(sys.executable).resolve(strict=True)), "-c", "pass")}, cwd=self.root)

    def write(self, environment, version="director-native-process-environment/v1") -> None:
        self.file.write_text(json.dumps({"version": version, "environment": environment}))

    def digest_under(self, ambient, *, pinned=True):
        from director_orchestrator.host_provider_bridge_server import _apply_pinned_process_environment
        from director_orchestrator.provider_validation import production_configuration_digest
        environment = dict(ambient)
        if pinned:
            environment["DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE"] = str(self.file)
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(_apply_pinned_process_environment(), pinned)
            return production_configuration_digest(self.executor, "claude", account_files=(str(self.account),))

    def test_pinned_environment_makes_the_digest_shell_independent(self) -> None:
        first = {"PATH": str(self.root / "one"), "TEMP": "ambient-a", "TMP": "ambient-a", "UNRELATED": "kept"}
        second = {"PATH": str(self.root / "two"), "TEMP": "ambient-b", "TMP": "ambient-b"}
        self.assertEqual(self.digest_under(first), self.digest_under(second))
        # Control: without the pin the same ambient difference changes the digest.
        self.assertNotEqual(self.digest_under(first, pinned=False), self.digest_under(second, pinned=False))
        from director_orchestrator.host_provider_bridge_server import _apply_pinned_process_environment
        with patch.dict(os.environ, {**first, "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE": str(self.file)}, clear=True):
            _apply_pinned_process_environment()
            self.assertEqual(os.environ["UNRELATED"], "kept")
            self.assertEqual(os.environ["TEMP"], self.pinned["TEMP"])

    def test_invalid_pinned_environment_refuses_without_changing_the_process(self) -> None:
        from director_orchestrator.host_provider_bridge_server import _apply_pinned_process_environment
        cases = {
            "missing name": ({k: v for k, v in self.pinned.items() if k != "PATHEXT"}, None, None),
            "extra name": ({**self.pinned, "HOME": self.pinned["TEMP"]}, None, None),
            "newline value": ({**self.pinned, "TEMP": self.pinned["TEMP"] + "\n"}, None, None),
            "missing TEMP directory": ({**self.pinned, "TEMP": str(self.root / "absent")}, None, None),
            "wrong version": (self.pinned, "director-native-process-environment/v0", None),
            "relative path": (self.pinned, None, self.file.name),
        }
        for name, (environment, version, location) in cases.items():
            with self.subTest(case=name):
                self.write(environment, **({"version": version} if version else {}))
                ambient = {"PATH": "ambient-path", "TEMP": "ambient-temp",
                           "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE": location or str(self.file)}
                with patch.dict(os.environ, ambient, clear=True):
                    before = dict(os.environ)
                    with self.assertRaises(RuntimeError) as caught:
                        _apply_pinned_process_environment()
                    self.assertEqual(str(caught.exception),
                                     "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE is invalid")
                    self.assertEqual(dict(os.environ), before)

    def test_configuration_digest_endpoint_requires_the_owner_bearer(self) -> None:
        from director_orchestrator.host_provider_bridge_server import _Handler
        from director_orchestrator.provider_validation import production_configuration_digest
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.executor = self.executor
        server.validation_service = SimpleNamespace(
            owner_token=self.OWNER, production=self.executor, account_files={"claude": (str(self.account),)})
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{server.server_port}/v1/provider/configuration-digest"

        def get(authorization=None):
            request = urllib.request.Request(url)
            if authorization is not None:
                request.add_header("Authorization", authorization)
            try:
                with opener.open(request, timeout=30) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()

        self.assertEqual(get()[0], 401)
        self.assertEqual(get("Bearer wrong")[0], 401)
        self.assertEqual(get(self.OWNER)[0], 401)
        status, body = get("Bearer " + self.OWNER)
        self.assertEqual(status, 200)
        expected = production_configuration_digest(self.executor, "claude", account_files=(str(self.account),))
        self.assertEqual(json.loads(body), {"ok": True, "configuration_digests": {"claude": expected},
                                            "pinned_environment": False})
        text = body.decode("utf-8")
        for secret_or_path in (self.OWNER, str(self.account), json.dumps(str(self.account))[1:-1], self.account.name):
            self.assertNotIn(secret_or_path, text)
        server.validation_service = None
        self.assertEqual(get("Bearer " + self.OWNER)[0], 503)

    def test_check_environment_prints_pins_without_serving_or_calling_providers(self) -> None:
        from director_orchestrator import host_provider_bridge_server as server_module
        from director_orchestrator.provider_validation import production_configuration_digest
        marker = self.root / "provider-was-called"
        provider = self.root / "marker_provider.py"
        provider.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        command = (str(Path(sys.executable).resolve(strict=True)), str(provider))
        environment = {
            "DIRECTOR_HOST_PROVIDER_COMMANDS_JSON": json.dumps({"claude": list(command)}),
            "DIRECTOR_HOST_PROVIDER_WORKSPACE": str(self.root),
            "DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON": json.dumps({"claude": [str(self.account)]}),
            "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE": str(self.file),
            "PATH": "ambient-path",
        }
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(server_module, "ThreadingHTTPServer", side_effect=AssertionError("server started")), \
             redirect_stdout(output):
            server_module.main(["--check-environment"])
            expected = production_configuration_digest(
                WindowsHostProviderExecutor({"claude": command}, cwd=self.root), "claude",
                account_files=(str(self.account),))
        self.assertEqual(json.loads(output.getvalue()), {
            "configuration_digests": {"claude": expected}, "pinned_environment": True, "provider_calls": 0})
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:54 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:18 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-21 | 22:36 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 22:42 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:50 UTC | END]]
