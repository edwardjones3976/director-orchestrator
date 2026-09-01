# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:54 UTC | BEGIN]]
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from director_orchestrator.host_provider_bridge import (
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
        object.__setattr__(unsupported, "provider", "windows-copilot")
        with self.assertRaisesRegex(HostProviderBridgeError, "unsupported"):
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

    def test_client_endpoint_is_exact_local_http_and_redirects_are_disabled(self) -> None:
        valid = "http://host.docker.internal:18765/v1/provider/dispatch"
        transport = HostBridgePacketTransport(
            "claude", valid, SECRET, writer_owner_token_digest="sha256:" + "f" * 64
        )
        self.assertEqual(valid, transport.endpoint)
        for endpoint in (
            "https://host.docker.internal:18765/v1/provider/dispatch",
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
                "http://host.docker.internal:18765/v1/provider/dispatch",
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

    def test_executor_refuses_cursor_and_windows_copilot(self) -> None:
        for provider in ("cursor", "windows-copilot"):
            with self.subTest(provider=provider), self.assertRaisesRegex(
                ValueError, "unsupported"
            ):
                WindowsHostProviderExecutor(
                    {provider: ("provider.exe",)}, cwd=self.temporary.name
                )


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:54 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]]
