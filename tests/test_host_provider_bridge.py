# [[OpenAI Codex | GPT-6 | 2026-09-28 | 02:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:19 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 18:29 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:49 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:07 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 05:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 04:42 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:02 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | BEGIN]]
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
from contextlib import closing
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
from director_orchestrator.packet_contracts import (
    build_replay_run,
    canonical_json_bytes,
    document_digest,
)
from director_orchestrator.packet_routing import (
    antigravity_directive_objective,
    compact_provider_view,
)
from director_orchestrator.primary_transports import (
    _normalize_host_native_result,
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
ROOT = Path(__file__).resolve().parents[1]


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

    def test_an_attestation_refusal_is_a_clean_replayable_no_run(self) -> None:
        """Nothing ran, so the claim settles REFUSED, never AMBIGUOUS, and a
        replay returns the same refusal without executing."""

        from director_orchestrator.host_provider_bridge import (
            HostProviderBridgeAttestationMismatch,
        )

        class _Refusing(_Executor):
            def execute(self, request):
                self.calls += 1
                raise HostProviderBridgeAttestationMismatch("host argv differs")

        executor = _Refusing()
        for nonce in ("nonce-refused-1", "nonce-refused-2"):
            with self.subTest(nonce=nonce), \
                    self.assertRaises(HostProviderBridgeAttestationMismatch):
                dispatch_bridge_request(request(nonce=nonce), secret=SECRET,
                                        store=self.store, executor=executor, now=1_000)
        self.assertEqual(1, executor.calls)
        with closing(self.store._connect()) as connection:
            states = connection.execute("SELECT state FROM bridge_attempts").fetchall()
        self.assertEqual([("REFUSED",)], states)

    def test_a_replay_must_carry_the_attestation_and_limits_it_was_claimed_under(self) -> None:
        """Cached bytes are returned only for the same attested argv digest and
        limits; a changed binding is never replayed, and never as a no-run for
        an attempt that ran."""

        from director_orchestrator.host_provider_bridge import (
            HostProviderBridgeAttestationMismatch,
        )

        pinned = "sha256:" + "1" * 64

        def attested(nonce, digest_value=pinned, budget="0.5", tokens="40000", key="c"):
            candidate = self.attested_request(digest_value, budget=budget, tokens=tokens)
            object.__setattr__(candidate, "nonce", nonce)
            object.__setattr__(candidate, "idempotency_digest", key * 64)
            return candidate

        class _Refusing(_Executor):
            def execute(self, request):
                self.calls += 1
                raise HostProviderBridgeAttestationMismatch("host argv differs")

        changed = {
            "argv_digest": {"digest_value": "sha256:" + "2" * 64},
            "budget": {"budget": "0.9"},
            "tokens": {"tokens": "99999"},
        }
        for state, executor, key in (("SUCCEEDED", _Executor(), "c"),
                                     ("REFUSED", _Refusing(), "d"),
                                     ("AMBIGUOUS", _Executor(ambiguous=True), "a")):
            try:
                self.store.claim(attested(f"{state}-first", key=key))
                try:
                    response = executor.execute(None)
                    self.store.settle(key * 64, response=response)
                except HostProviderBridgeAttestationMismatch:
                    self.store.settle(key * 64, response=None, refused=True)
                except HostProviderBridgeAmbiguous:
                    self.store.settle(key * 64, response=None)
            except HostProviderBridgeError:
                self.fail("first claim failed")
            # The same binding replays as before.
            if state == "SUCCEEDED":
                self.assertEqual(b'{"status":"SUCCEEDED"}',
                                 self.store.claim(attested(f"{state}-same", key=key)))
            for name, overrides in changed.items():
                with self.subTest(state=state, changed=name):
                    expected = {"SUCCEEDED": HostProviderBridgeError,
                                "REFUSED": HostProviderBridgeAttestationMismatch,
                                "AMBIGUOUS": HostProviderBridgeAmbiguous}[state]
                    with self.assertRaises(expected) as raised:
                        self.store.claim(attested(f"{state}-{name}", key=key, **overrides))
                    if state != "REFUSED":
                        # Never the signed no-run refusal for an attempt that
                        # ran or may have run.
                        self.assertNotIsInstance(
                            raised.exception, HostProviderBridgeAttestationMismatch)
            # A replay that drops its attestation entirely is refused too.
            plain = request(nonce=f"{state}-plain")
            object.__setattr__(plain, "idempotency_digest", key * 64)
            with self.subTest(state=state, changed="unattested"), \
                    self.assertRaises(HostProviderBridgeError):
                self.store.claim(plain)

    def test_a_trickled_challenge_body_is_cut_off_at_the_deadline(self) -> None:
        """Over real HTTP: a server sending one byte every 0.2 s never lets the
        inactivity timeout fire; the absolute deadline ends the read."""

        import socket
        import time as time_module
        from director_orchestrator.host_provider_bridge import challenge_host_provider_bridge

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        self.addCleanup(listener.close)

        def trickle():
            connection, _ = listener.accept()
            with connection:
                connection.recv(65536)
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 400\r\n\r\n")
                for _ in range(40):
                    try:
                        connection.sendall(b"x")
                    except OSError:
                        return
                    time_module.sleep(0.2)

        threading.Thread(target=trickle, daemon=True).start()
        started = time_module.monotonic()
        with patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*"}), \
                self.assertRaises(HostProviderBridgeError):
            challenge_host_provider_bridge(
                f"http://127.0.0.1:{port}/v1/provider/dispatch", SECRET, timeout_seconds=1.0)
        self.assertLess(time_module.monotonic() - started, 2.5)

    def _trickling_server(self, head: bytes, trickle: bytes) -> int:
        """A real socket server that sends ``head`` then one byte of
        ``trickle`` every 0.2 s: never idle long enough for a per-read
        timeout to fire."""

        import socket
        import time as time_module

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        def serve():
            connection, _ = listener.accept()
            with connection:
                connection.recv(65536)
                connection.sendall(head)
                for byte in trickle:
                    try:
                        connection.sendall(bytes([byte]))
                    except OSError:
                        return
                    time_module.sleep(0.2)

        threading.Thread(target=serve, daemon=True).start()
        return listener.getsockname()[1]

    def test_trickled_headers_are_cut_off_at_the_deadline(self) -> None:
        """The header phase of open() is bounded too: challenge and dispatch."""

        import time as time_module
        from director_orchestrator.host_provider_bridge import (
            HostBridgePacketTransport, challenge_host_provider_bridge)

        headers = b"X-Director-Padding: " + b"a" * 60 + b"\r\n\r\n"
        port = self._trickling_server(b"HTTP/1.1 200 OK\r\n", headers)
        started = time_module.monotonic()
        with patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*"}),                 self.assertRaises(HostProviderBridgeError):
            challenge_host_provider_bridge(
                f"http://127.0.0.1:{port}/v1/provider/dispatch", SECRET, timeout_seconds=1.0)
        self.assertLess(time_module.monotonic() - started, 2.5)

        port = self._trickling_server(b"HTTP/1.1 200 OK\r\n", headers)
        transport = HostBridgePacketTransport(
            "claude", f"http://127.0.0.1:{port}/v1/provider/dispatch", SECRET,
            writer_owner_token_digest="sha256:" + "f" * 64)
        work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text())
        started = time_module.monotonic()
        with patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*"}),                 self.assertRaisesRegex(RuntimeError, "ambiguous"):
            transport.send(canonical_json_bytes(work),
                           context=SimpleNamespace(timeout_seconds=1, fencing_generation=7))
        self.assertLess(time_module.monotonic() - started, 2.5)

    def test_the_bridge_builds_its_bytes_then_rechecks_then_sends(self) -> None:
        """Nothing slow (a cache-miss rebuild reads files) runs between the
        final authority recheck and the request."""

        from director_orchestrator.host_provider_bridge import HostBridgePacketTransport
        from director_orchestrator.packet_routing import validated_compact_view

        order = []

        def build(packet):
            order.append("build")
            return validated_compact_view(packet)

        transport = HostBridgePacketTransport(
            "claude", "https://host.docker.internal:18765/v1/provider/dispatch", SECRET,
            writer_owner_token_digest="sha256:" + "f" * 64,
            outbound_payload_builder=build, result_normalizer=lambda packet, result: result)
        opener = MagicMock()
        opener.open.side_effect = lambda *args, **kwargs: (
            order.append("send") or (_ for _ in ()).throw(urllib.error.URLError("stop")))
        work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text())
        context = SimpleNamespace(timeout_seconds=30, fencing_generation=7,
                                  authority_recheck=lambda: order.append("recheck"))
        with patch("director_orchestrator.host_provider_bridge.urllib.request.build_opener",
                   return_value=opener), self.assertRaises(RuntimeError):
            transport.send(canonical_json_bytes(work), context=context)
        self.assertEqual(["build", "recheck", "send"], order)

    def test_resolve_and_connect_spend_one_absolute_deadline(self) -> None:
        import socket as socket_module
        import time as time_module
        from director_orchestrator.host_provider_bridge import connect_until_deadline

        def slow_resolve(*args, **kwargs):
            time_module.sleep(3)
            return []

        started = time_module.monotonic()
        with self.assertRaises(TimeoutError):
            connect_until_deadline(("bridge.invalid", 443), 30, deadline=started + 0.3,
                                   resolve=slow_resolve)
        self.assertLess(time_module.monotonic() - started, 1.0)

        timeouts = []

        class Stalling:
            def __init__(self, *args):
                self.timeout = None

            def settimeout(self, value):
                self.timeout = value

            def connect(self, address):
                timeouts.append(self.timeout)
                time_module.sleep(self.timeout)
                raise socket_module.timeout("connect timed out")

            def close(self):
                pass

        addresses = [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("192.0.2.1", 443))] * 4
        started = time_module.monotonic()
        with self.assertRaises((TimeoutError, OSError)):
            connect_until_deadline(("bridge", 443), 30, deadline=started + 0.4,
                                   resolve=lambda *a, **k: addresses, socket_factory=Stalling)
        self.assertLess(time_module.monotonic() - started, 1.0)
        self.assertLessEqual(timeouts[0], 0.4 + 1e-9)
        self.assertLess(len(timeouts), 4)

    def test_socket_connected_after_the_deadline_is_closed_not_returned(self) -> None:
        import socket as socket_module
        import time as time_module
        from director_orchestrator.host_provider_bridge import connect_until_deadline

        closed = []

        class Late:
            def __init__(self, *args):
                pass

            def settimeout(self, value):
                pass

            def connect(self, address):
                time_module.sleep(0.3)

            def close(self):
                closed.append(True)

        addresses = [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]
        with self.assertRaises(TimeoutError):
            connect_until_deadline(("bridge", 443), 30, deadline=time_module.monotonic() + 0.1,
                                   resolve=lambda *a, **k: addresses, socket_factory=Late)
        self.assertEqual([True], closed)

    def test_timed_out_lookups_are_capped_and_fail_closed(self) -> None:
        from director_orchestrator import host_provider_bridge as bridge

        # Control worker completion: a wall-clock timeout before resolver.start()
        # must not be mistaken for an outstanding lookup. Other tests exercise
        # real DNS deadlines; this test owns the capacity and release ordering.
        resolver = MagicMock()
        resolver.is_alive.return_value = True
        slots = threading.BoundedSemaphore(1)
        with patch.object(bridge, "_RESOLVER_SLOTS", slots), \
             patch.object(bridge, "_time_left", return_value=1.0), \
             patch.object(bridge.threading, "Thread", return_value=resolver) as factory:
            try:
                with self.assertRaisesRegex(TimeoutError, "resolution reached its deadline"):
                    bridge.connect_until_deadline(("bridge", 443), 30,
                                                  deadline=1.0, resolve=lambda *a, **k: [])
                factory.assert_called_once()
                with self.assertRaisesRegex(TimeoutError, "capacity"):
                    bridge.connect_until_deadline(("bridge", 443), 30,
                                                  deadline=1.0, resolve=lambda *a, **k: [])
                factory.assert_called_once()  # Refusal starts no second worker.
            finally:
                if factory.called:
                    factory.call_args.kwargs["target"]()  # Drain before restoring slots.

            resolver.is_alive.return_value = False
            resolver.start.side_effect = lambda: factory.call_args.kwargs["target"]()
            with self.assertRaisesRegex(OSError, "no address"):
                bridge.connect_until_deadline(("bridge", 443), 30,
                                              deadline=1.0, resolve=lambda *a, **k: [])
            self.assertEqual(2, factory.call_count)  # Completed lookup released its slot.

    def test_slot_is_released_once_when_start_and_lookup_both_finish(self) -> None:
        from director_orchestrator import host_provider_bridge as bridge

        slots = threading.BoundedSemaphore(1)
        resolver = MagicMock()
        resolver.is_alive.return_value = False

        def start_after_lookup() -> None:
            factory.call_args.kwargs["target"]()
            raise RuntimeError("start observed the worker")

        resolver.start.side_effect = start_after_lookup
        with patch.object(bridge, "_RESOLVER_SLOTS", slots), \
             patch.object(bridge, "_time_left", return_value=1.0), \
             patch.object(bridge.threading, "Thread", return_value=resolver) as factory:
            with self.assertRaisesRegex(RuntimeError, "start observed the worker"):
                bridge.connect_until_deadline(("bridge", 443), 30, deadline=1.0, resolve=lambda *a, **k: [])
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()

    def test_every_bounded_read_spends_one_absolute_deadline(self) -> None:
        import time as time_module
        from director_orchestrator.host_provider_bridge import read_until_deadline

        class Trickle:
            def read1(self, size):
                time_module.sleep(0.1)
                return b"x"

        started = time_module.monotonic()
        with self.assertRaises(TimeoutError):
            read_until_deadline(Trickle(), limit=1000, deadline=started + 0.35)
        self.assertLess(time_module.monotonic() - started, 1.0)
        with self.assertRaises(TimeoutError):
            read_until_deadline(Trickle(), limit=1000, exact=True, deadline=started + 0.35)
        self.assertEqual(b"abc", read_until_deadline(
            io.BytesIO(b"abc"), limit=3, exact=True, deadline=time_module.monotonic() + 1))
        with self.assertRaises(ValueError):
            read_until_deadline(io.BytesIO(b"ab"), limit=3, exact=True,
                                deadline=time_module.monotonic() + 1)
        with self.assertRaises(TimeoutError):
            read_until_deadline(io.BytesIO(b"abc"), limit=3, deadline=time_module.monotonic() - 1)

    def test_a_store_created_before_the_refused_state_is_migrated_in_place(self) -> None:
        import sqlite3

        path = Path(self.temporary.name) / "legacy-bridge.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(
                "CREATE TABLE bridge_attempts (idempotency_digest TEXT PRIMARY KEY, "
                "provider TEXT NOT NULL, task_id TEXT NOT NULL, assignment_id TEXT NOT NULL, "
                "writer_generation INTEGER NOT NULL, packet_digest TEXT NOT NULL, "
                "writer_owner_token_digest TEXT NOT NULL, state TEXT NOT NULL "
                "CHECK(state IN ('INFLIGHT','SUCCEEDED','AMBIGUOUS')), response_json BLOB, "
                "updated_at INTEGER NOT NULL);"
                "INSERT INTO bridge_attempts VALUES "
                "('d', 'claude', 't', 'a', 1, 'p', 'w', 'SUCCEEDED', x'7b7d', 1);")
        store = BridgeClaimStore(path)
        BridgeClaimStore(path)  # idempotent
        with closing(store._connect()) as connection:
            rows = connection.execute(
                "SELECT idempotency_digest, state, response_json FROM bridge_attempts").fetchall()
            connection.execute(
                "INSERT INTO bridge_attempts VALUES "
                "('e', 'claude', 't', 'a', 1, 'p', 'w', 'REFUSED', NULL, 1)")
        self.assertEqual([("d", "SUCCEEDED", b"{}")], rows)

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

    def attested_request(self, digest_value: str, *, argv_digest_in_signature: bool = True,
                         budget: str = "0.5", tokens: str = "40000"):
        base = request(nonce="nonce-attested")
        bindings = {key: getattr(base, key) for key in (
            "timestamp", "nonce", "provider", "task_id", "assignment_id",
            "idempotency_digest", "writer_generation", "packet_digest",
            "writer_owner_token_digest")}
        signature = bridge_signature(
            SECRET, **bindings,
            attested_argv_digest=digest_value if argv_digest_in_signature else "",
            attempt_budget_usd=budget, total_token_limit=tokens)
        return BridgeRequest(**bindings, signature=signature, packet=base.packet,
                             attested_argv_digest=digest_value,
                             attempt_budget_usd=budget, total_token_limit=tokens)

    def test_the_attested_argv_digest_is_signed_and_checked_before_execution(self) -> None:
        """The host refuses, before spawning, an argv other than the attested one."""

        from director_orchestrator.bridge_attestation import argv_digest
        from director_orchestrator.host_provider_bridge import (
            HostProviderBridgeAttestationMismatch,
        )

        # A command that enforces the attempt limits before launch.
        argv = ("python.exe", "C:/bridge/xai-responses-command.py")
        pinned = argv_digest(argv)
        self.attested_request(pinned).validate(SECRET, now=1_000)
        # Stripped from, or forged outside, the signature: authentication fails.
        for label, candidate in {
            "stripped": self.attested_request(pinned),
            "forged": self.attested_request("sha256:" + "9" * 64,
                                            argv_digest_in_signature=False),
        }.items():
            if label == "stripped":
                object.__setattr__(candidate, "attested_argv_digest", "")
            with self.subTest(binding=label), self.assertRaisesRegex(
                    HostProviderBridgeError, "authentication"):
                candidate.validate(SECRET, now=1_000)
        executor = WindowsHostProviderExecutor(
            {"claude": argv}, cwd=self.temporary.name, writer_fence_verifier=_Verifier())
        completed = subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=b'{"status":"SUCCEEDED"}', stderr=b"")
        with patch("subprocess.run", return_value=completed) as run:
            with self.assertRaises(HostProviderBridgeAttestationMismatch):
                executor.execute(self.attested_request("sha256:" + "9" * 64))
            run.assert_not_called()
            self.assertEqual(b'{"status":"SUCCEEDED"}',
                             executor.execute(self.attested_request(pinned)))
            self.assertEqual(1, run.call_count)
            # The signed limits reach the host command as its limit variables.
            environment = run.call_args.kwargs["env"]
            self.assertEqual("0.5", environment["DIRECTOR_PROVIDER_ATTEMPT_BUDGET_USD"])
            self.assertEqual("40000", environment["DIRECTOR_PROVIDER_TOTAL_TOKEN_LIMIT"])

    def test_limits_are_signed_passed_and_recorded_for_a_cli_that_cannot_enforce_them(self) -> None:
        """Owner ruling 2026-09-24, "Accept post-hoc cap": the Grok CLI runs
        with its limits passed and recorded; the cap is checked on the result."""

        from director_orchestrator.bridge_attestation import argv_digest
        from director_orchestrator.host_provider_bridge import (
            HostProviderBridgeAttestationMismatch,
        )

        stripped = self.attested_request("sha256:" + "1" * 64)
        object.__setattr__(stripped, "total_token_limit", "999999")
        with self.assertRaisesRegex(HostProviderBridgeError, "authentication"):
            stripped.validate(SECRET, now=1_000)
        grok_cli = ("grok.exe", "--permission-mode", "plan", "--max-turns", "1")
        executor = WindowsHostProviderExecutor(
            {"claude": grok_cli}, cwd=self.temporary.name, writer_fence_verifier=_Verifier())
        completed = subprocess.CompletedProcess(
            args=grok_cli, returncode=0, stdout=b'{"status":"SUCCEEDED"}', stderr=b"")
        with patch("subprocess.run", return_value=completed) as run:
            executor.execute(self.attested_request(argv_digest(grok_cli)))
        environment = run.call_args.kwargs["env"]
        self.assertEqual("0.5", environment["DIRECTOR_PROVIDER_ATTEMPT_BUDGET_USD"])
        self.assertEqual("40000", environment["DIRECTOR_PROVIDER_TOTAL_TOKEN_LIMIT"])
        # An attested request without its limits never runs.
        with patch("subprocess.run", side_effect=AssertionError("launched")), \
                self.assertRaisesRegex(HostProviderBridgeAttestationMismatch, "attempt limits"):
            executor.execute(self.attested_request(argv_digest(grok_cli), budget="", tokens=""))
        # The claim store records the limits the attempt was admitted under.
        request_with_limits = self.attested_request(argv_digest(grok_cli))
        object.__setattr__(request_with_limits, "idempotency_digest", "e" * 64)
        self.store.claim(request_with_limits)
        with closing(self.store._connect()) as connection:
            row = connection.execute(
                "SELECT attested_argv_digest, attempt_budget_usd, total_token_limit "
                "FROM bridge_attempt_limits WHERE idempotency_digest = ?", ("e" * 64,)).fetchone()
        self.assertEqual((argv_digest(grok_cli), "0.5", "40000"), row)

    def test_a_signed_mismatch_reaches_the_director_as_a_stale_attestation(self) -> None:
        """End to end over HTTP: nothing is spawned, and the refusal is authenticated."""

        from director_orchestrator.host_provider_bridge import (
            HostBridgeAttestationStale,
            HostBridgePacketTransport,
        )
        from director_orchestrator.host_provider_bridge_server import _Handler
        from director_orchestrator.packet_routing import (
            git_blob_sha, prepare_small_task_view, validated_compact_view)

        python = str(Path(sys.executable).resolve(strict=True))
        marker = Path(self.temporary.name) / "spawned"
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.bridge_secret = SECRET
        server.claim_store = BridgeClaimStore(Path(self.temporary.name) / "claims.sqlite3")
        server.executor = WindowsHostProviderExecutor(
            {"grok": (python, "-c", f"open({str(marker)!r}, 'w').close()")},
            cwd=self.temporary.name, writer_fence_verifier=_Verifier())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text())
        text = b"# Agents\n"
        work.update(allowed_tools=["READ_REPOSITORY"], allowed_external_effects=[],
                    objective="Summarize AGENTS.md.",
                    authority_snapshot=[{**work["authority_snapshot"][0],
                                         "blob_sha": git_blob_sha(text)}])
        # Dispatch prepares the one view the transport sends.
        with patch("director_orchestrator.packet_routing._BLOB_READER", lambda sha: text):
            prepare_small_task_view(work)
        transport = HostBridgePacketTransport(
            "grok", f"http://127.0.0.1:{server.server_port}/v1/provider/dispatch", SECRET,
            writer_owner_token_digest="sha256:" + "f" * 64,
            outbound_payload_builder=validated_compact_view,
            result_normalizer=lambda packet, result: result,
            read_only_argv_digest="sha256:" + "9" * 64)
        context = SimpleNamespace(timeout_seconds=30, fencing_generation=7, deadline=None,
                                  budget_usd=0.5, total_token_limit=40000)
        with patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*"}), \
                self.assertRaises(HostBridgeAttestationStale):
            transport.send(canonical_json_bytes(work), context=context)
        self.assertFalse(marker.exists())

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
        # The transport reads in bounded chunks, as a real HTTPResponse does.
        response.read1 = io.BytesIO(body).read1
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

    def test_claude_bridge_posts_compact_view_bound_to_the_full_packet(self) -> None:
        work = json.loads(
            (Path(__file__).resolve().parents[1] / "docs" / "schemas"
             / "model-work-packet-v1.example.json").read_text(encoding="utf-8")
        )
        entry = work["authority_snapshot"][0]
        work["authority_snapshot"] = [
            dict(entry, path=f"src/module_{index:04d}.py") for index in range(1_500)
        ]
        full = canonical_json_bytes(work)
        native = {
            "type": "result", "subtype": "success", "is_error": False,
            "session_id": "claude-bridge-session",
            "result": '{"outcome":"done","status":"SUCCEEDED"}',
        }
        body = json.dumps(native, separators=(",", ":")).encode()
        captured: dict[str, object] = {}

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
            outbound_payload_builder=compact_provider_view,
            result_normalizer=partial(_normalize_host_native_result, "claude"),
        )
        with patch(
            "director_orchestrator.host_provider_bridge.urllib.request.build_opener",
            return_value=opener,
        ):
            result = transport.send(
                full, context=SimpleNamespace(timeout_seconds=30, fencing_generation=7)
            )

        # The host sees exactly the compact view, never the inventory.
        view = compact_provider_view(work)
        self.assertEqual(canonical_json_bytes(view), captured["data"])
        self.assertNotIn(b'"authority_snapshot":', captured["data"])
        headers = captured["headers"]
        self.assertEqual(_digest(captured["data"]), headers["X-director-packet-digest"])
        # The signed origin binding is the full packet's digest, which the view
        # itself also names.
        self.assertEqual(_digest(full), headers["X-director-work-packet-digest"])
        self.assertEqual(
            document_digest(work), "sha256:" + headers["X-director-work-packet-digest"]
        )
        self.assertEqual(document_digest(work), view["work_packet_digest"])

        bindings = dict(
            timestamp=int(headers["X-director-bridge-timestamp"]),
            nonce=headers["X-director-bridge-nonce"],
            provider="claude",
            task_id=headers["X-director-task"],
            assignment_id=headers["X-director-assignment"],
            idempotency_digest=headers["X-director-idempotency-digest"],
            writer_generation=7,
            packet_digest=headers["X-director-packet-digest"],
            writer_owner_token_digest=headers["X-director-writer-owner-token-digest"],
            work_packet_digest=headers["X-director-work-packet-digest"],
        )
        accepted = BridgeRequest(
            **bindings,
            signature=headers["Authorization"].removeprefix("Director-HMAC "),
            packet=captured["data"],
        )
        accepted.validate(SECRET, now=bindings["timestamp"])
        for forged in ("", "a" * 64):
            with self.subTest(work_packet_digest=forged):
                with self.assertRaisesRegex(HostProviderBridgeError, "authentication failed"):
                    replace(accepted, work_packet_digest=forged).validate(
                        SECRET, now=bindings["timestamp"]
                    )

        # The normalized result binds to the full packet, not to the view.
        self.assertEqual("claude", result["provider"])
        self.assertEqual("claude-bridge-session", result["run_reference"])
        self.assertEqual(work["authority_snapshot_digest"], result["authority_snapshot_digest"])
        build_replay_run(work, result)

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

    def test_the_challenge_carries_bound_lane_attestations(self) -> None:
        """Owner ruling 2026-09-24: the bridge attests each lane's exact argv."""

        from director_orchestrator.bridge_attestation import argv_digest, lane_attestations
        from director_orchestrator.native_acceptance_streams import GROK_DENY_TOOLS

        grok = ["grok.exe", "--output-format", "json", "--permission-mode", "plan",
                "--max-turns", "1", "--disable-web-search", "--no-subagents",
                "--disallowed-tools", GROK_DENY_TOOLS, "--deny", "Bash", "--deny", "Edit",
                "--deny", "Write", "--deny", "MCPTool"]
        claude = ["claude.exe", "--print", "--output-format", "json", "--permission-mode", "plan"]
        providers = ["claude", "grok"]
        command_map_digest = "sha256:" + _digest(
            json.dumps(providers, separators=(",", ":")).encode())

        def respond(attestations, *, tamper=None, secret=SECRET, bind=True):
            digest_value = "sha256:" + _digest(canonical_json_bytes(attestations))

            class _Opener:
                def open(inner_self, candidate, timeout):
                    nonce = candidate.headers["X-director-bridge-nonce"]
                    body = {
                        "ok": True, "protocol": "director-host-provider-bridge/v1",
                        "configured_providers": providers,
                        "command_map_digest": command_map_digest,
                        "lane_attestations": tamper or attestations,
                        "lane_attestation_digest": digest_value,
                        "proof": bridge_challenge_signature(
                            secret, timestamp=1_000, nonce=nonce,
                            command_map_digest=command_map_digest,
                            lane_attestation_digest=digest_value if bind else ""),
                    }
                    return _ChallengeResponse(json.dumps(body).encode())

            with patch("urllib.request.build_opener", return_value=_Opener()):
                return challenge_host_provider_bridge(
                    "https://host.docker.internal:18765/v1/provider/dispatch", SECRET, now=1_000)

        attestations = lane_attestations({"claude": claude, "grok": grok})
        # Read-only and admitted, although the Grok CLI cannot enforce a
        # token or dollar cap before launch: owner ruling 2026-09-24,
        # "Accept post-hoc cap". The attestation still reports it.
        self.assertTrue(attestations["grok"]["read_only_attested"])
        self.assertFalse(attestations["grok"]["enforces_attempt_limits"])
        self.assertEqual({"grok": argv_digest(grok)}, respond(attestations).read_only_attested)
        # The host's verdict is required but re-checked: a claimed attestation
        # for an argv that lacks the set is not accepted.
        claimed = {**attestations, "claude": {**attestations["claude"], "read_only_attested": True}}
        self.assertNotIn("claude", respond(claimed).read_only_attested)
        # Web search enabled, shell allowed, or another permission mode.
        for name, argv in {
            "web_search": [item for item in grok if item != "--disable-web-search"],
            "shell": [item for index, item in enumerate(grok)
                      if not (item == "Bash" or (item == "--deny" and grok[index + 1] == "Bash"))],
            "permission_mode": [("auto" if item == "plan" else item) for item in grok],
            "two_turns": [("2" if item == "1" else item) for item in grok],
            "skip_permissions": [*grok, "--dangerously-skip-permissions"],
        }.items():
            with self.subTest(argv=name):
                self.assertEqual({}, respond(lane_attestations({"grok": argv})).read_only_attested)
        # Tampered, unbound or unauthenticated attestations fail the challenge.
        altered = {"grok": {**attestations["grok"], "argv": [*grok, "--add-dir", "C:/"]}}
        for name, kwargs in {
            "tampered": {"tamper": altered},
            "unbound_proof": {"bind": False},
            "bad_hmac": {"secret": "wrong-bridge-secret-" + "y" * 32},
        }.items():
            with self.subTest(failure=name), self.assertRaises(HostProviderBridgeError):
                respond(attestations, **kwargs)
        # A bridge that reports nothing attests nothing.
        self.assertEqual({}, respond({}).read_only_attested)

    def test_a_widening_argument_voids_an_otherwise_complete_restriction_set(self) -> None:
        """Every restriction plus --yolo (or any forbidden argument) attests nothing."""

        from director_orchestrator.bridge_attestation import FORBIDDEN_ARGS, read_only_attested
        from director_orchestrator.native_acceptance_streams import GROK_DENY_TOOLS

        full = {
            "grok": ["grok.exe", "--permission-mode", "plan", "--max-turns", "1",
                     "--disable-web-search", "--no-subagents",
                     "--disallowed-tools", GROK_DENY_TOOLS, "--deny", "Bash", "--deny", "Edit",
                     "--deny", "Write", "--deny", "MCPTool"],
            "claude": ["claude.exe", "--print", "--permission-mode", "plan", "--tools", "",
                       "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--safe-mode"],
        }
        self.assertIn("--yolo", FORBIDDEN_ARGS)
        for lane, argv in full.items():
            self.assertTrue(read_only_attested(lane, argv))
            extras = [[flag] for flag in sorted(FORBIDDEN_ARGS)]
            extras += [[f"{flag}=true"] for flag in sorted(FORBIDDEN_ARGS) if flag.startswith("--")]
            extras += [["--YOLO"], ["--allowedTools", "Bash"], ["--allowedTools=Bash"],
                       ["--permission-mode", "bypassPermissions"],
                       ["--permission-mode=bypassPermissions"],
                       ["--permission-mode", "acceptEdits"]]
            for extra in extras:
                for position in (1, len(argv)):
                    with self.subTest(lane=lane, extra=extra, position=position):
                        self.assertFalse(read_only_attested(
                            lane, [*argv[:position], *extra, *argv[position:]]))

    def test_the_claude_restriction_set_cannot_be_configured_on_the_bridge(self) -> None:
        """Its empty --tools value is an empty argv item, which the executor refuses."""

        from director_orchestrator.bridge_attestation import read_only_attested

        full = ["claude.exe", "--print", "--permission-mode", "plan", "--tools", "",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--safe-mode"]
        self.assertTrue(read_only_attested("claude", full))
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            WindowsHostProviderExecutor({"claude": full}, cwd=directory)
        self.assertFalse(read_only_attested("claude", [item for item in full if item != ""]))
        self.assertFalse(read_only_attested("claude", [*full[:4], "--tools=", *full[6:]]))

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
        (folders["system"] / "cmd.exe").write_bytes(b"")
        self.file = self.root / "native-process-environment.json"
        self.write(self.pinned)
        self.account = self.root / "synthetic-account.json"
        self.account.write_text("synthetic fixture account, not a credential")
        python = str(Path(sys.executable).resolve(strict=True))
        self.executor = WindowsHostProviderExecutor(
            {lane: (python, "-c", "pass") for lane in ("claude", "github-copilot", "grok")}, cwd=self.root)

    def write(self, environment, version="director-native-process-environment/v1") -> None:
        self.file.write_text(json.dumps({"version": version, "environment": environment}))

    def digest_under(self, ambient, *, pinned=True, lane="claude"):
        from director_orchestrator.host_provider_bridge_server import _apply_pinned_process_environment
        from director_orchestrator.provider_validation import production_configuration_digest
        environment = dict(ambient)
        if pinned:
            environment["DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE"] = str(self.file)
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(_apply_pinned_process_environment(), pinned)
            return production_configuration_digest(self.executor, lane, account_files=(str(self.account),))

    def test_pinned_environment_drops_inherited_lane_credentials(self) -> None:
        from director_orchestrator.host_provider_bridge_server import (
            PROFILE_AUTH_NAMES, _apply_pinned_process_environment)
        bare = {"PATH": "ambient-a"}
        inherited = {"ANTHROPIC_API_KEY": "ambient-key", "CLAUDE_CODE_OAUTH_TOKEN": "ambient-oauth",
                     "GH_TOKEN": "ambient-gh", "GITHUB_TOKEN": "ambient-github", "XAI_API_KEY": "ambient-xai"}
        noisy = {"PATH": "ambient-b", **inherited}
        for lane in ("claude", "github-copilot", "grok"):
            with self.subTest(lane=lane):
                self.assertEqual(self.digest_under(bare, lane=lane), self.digest_under(noisy, lane=lane))
                # Control: without the pin an inherited credential changes the digest.
                self.assertNotEqual(self.digest_under(bare, pinned=False, lane=lane),
                                    self.digest_under({**bare, **inherited}, pinned=False, lane=lane))
        # A credential the launcher copied from the profile survives and is bound.
        forwarded = {**noisy, "XAI_API_KEY": "profile-xai", PROFILE_AUTH_NAMES: "XAI_API_KEY"}
        with patch.dict(os.environ, {**forwarded, "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE": str(self.file)},
                        clear=True):
            _apply_pinned_process_environment()
            self.assertEqual(os.environ["XAI_API_KEY"], "profile-xai")
            for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
                self.assertNotIn(name, os.environ)
        self.assertNotEqual(self.digest_under(forwarded, lane="grok"), self.digest_under(bare, lane="grok"))
        self.assertEqual(self.digest_under(forwarded, lane="claude"), self.digest_under(bare, lane="claude"))
        # A name outside the lane credential set refuses without changing the process.
        with patch.dict(os.environ, {**noisy, PROFILE_AUTH_NAMES: "OPENAI_API_KEY",
                                     "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE": str(self.file)}, clear=True):
            before = dict(os.environ)
            with self.assertRaises(RuntimeError):
                _apply_pinned_process_environment()
            self.assertEqual(dict(os.environ), before)

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

    def test_pinned_environment_replaces_inherited_proxy_and_ca_names(self) -> None:
        from director_orchestrator.host_provider_bridge_server import _apply_pinned_process_environment
        ambient = {"HTTPS_PROXY": "http://ambient:1", "https_proxy": "http://ambient:2",
                   "SSL_CERT_FILE": "ambient.pem", "NO_PROXY": "ambient", "UNRELATED": "kept",
                   "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE": str(self.file)}
        with patch.dict(os.environ, ambient, clear=True):
            _apply_pinned_process_environment()
            self.assertEqual(os.environ["UNRELATED"], "kept")
            for name in ("HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE", "NO_PROXY"):
                self.assertNotIn(name, os.environ)
        self.write({**self.pinned, "HTTPS_PROXY": "http://pinned:3"})
        with patch.dict(os.environ, ambient, clear=True):
            _apply_pinned_process_environment()
            self.assertEqual(os.environ["HTTPS_PROXY"], "http://pinned:3")
            if os.name != "nt":  # Windows environment names are case-insensitive
                self.assertNotIn("https_proxy", os.environ)

    def test_codex_child_names_outside_the_pin_are_documented(self) -> None:
        # The openai native Codex child inherits more than the pinned names;
        # the docstring and the operations doc must name exactly that residual.
        from director_orchestrator import host_provider_bridge_server as server_module
        from director_orchestrator.host_provider_bridge import _SAFE_OS_ENV
        from director_orchestrator.primary_transports import _BASE_CHILD_ENVIRONMENT
        residual = _BASE_CHILD_ENVIRONMENT - _SAFE_OS_ENV
        self.assertEqual(residual, {"HOME", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "LANG", "LC_ALL"})
        operations = (Path(__file__).resolve().parents[1] / "docs" / "langgraph-primary-operations.md"
                      ).read_text(encoding="utf-8")
        for name in residual:
            self.assertIn(name, server_module._apply_pinned_process_environment.__doc__)
            self.assertIn(f"`{name}`", operations)

    def test_invalid_pinned_environment_refuses_without_changing_the_process(self) -> None:
        from director_orchestrator.host_provider_bridge_server import _apply_pinned_process_environment
        cases = {
            "missing name": ({k: v for k, v in self.pinned.items() if k != "PATHEXT"}, None, None),
            "extra name": ({**self.pinned, "HOME": self.pinned["TEMP"]}, None, None),
            "newline value": ({**self.pinned, "TEMP": self.pinned["TEMP"] + "\n"}, None, None),
            "missing TEMP directory": ({**self.pinned, "TEMP": str(self.root / "absent")}, None, None),
            "wrong version": (self.pinned, "director-native-process-environment/v0", None),
            "relative path": (self.pinned, None, self.file.name),
            # Each of these exists relative to the cwd set below, so only the
            # absolute/non-empty rule refuses it.
            "relative PATH entry": ({**self.pinned, "PATH": os.pathsep.join((self.pinned["TEMP"], "tools"))},
                                    None, None),
            "relative TEMP directory": ({**self.pinned, "TEMP": "temp"}, None, None),
            "empty PATH entry": ({**self.pinned, "PATH": self.pinned["PATH"] + os.pathsep}, None, None),
            "relative APPDATA": ({**self.pinned, "APPDATA": "roaming"}, None, None),
            "relative LOCALAPPDATA": ({**self.pinned, "LOCALAPPDATA": "local"}, None, None),
            "relative WINDIR": ({**self.pinned, "WINDIR": "system"}, None, None),
            "missing APPDATA directory": ({**self.pinned, "APPDATA": str(self.root / "absent")}, None, None),
            "relative COMSPEC": ({**self.pinned, "COMSPEC": os.path.join("system", "cmd.exe")}, None, None),
            "missing COMSPEC file": ({**self.pinned, "COMSPEC": str(self.root / "system" / "absent.exe")},
                                     None, None),
            "relative SSL_CERT_FILE": ({**self.pinned, "SSL_CERT_FILE": os.path.join("system", "cmd.exe")},
                                       None, None),
            "missing SSL_CERT_FILE": ({**self.pinned, "SSL_CERT_FILE": str(self.root / "absent.pem")}, None, None),
            "relative SSL_CERT_DIR": ({**self.pinned, "SSL_CERT_DIR": "system"}, None, None),
            "pinned SSLKEYLOGFILE": ({**self.pinned, "SSLKEYLOGFILE": str(self.root / "temp" / "keys.log")},
                                     None, None),
            "relative SSLKEYLOGFILE": ({**self.pinned, "SSLKEYLOGFILE": "scripts/launcher.ps1"}, None, None),
        }
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
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
            owner_token=self.OWNER, production=self.executor, account_files={"claude": (str(self.account),)},
            team_route=None, openai_responses_route=None)
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
        from director_orchestrator.bridge_attestation import argv_digest
        self.assertEqual(json.loads(body), {
            "ok": True, "configuration_digests": {"claude": expected},
            # Digests and verdicts only, never the argv or its paths; these
            # fixture commands carry no restriction set.
            "lane_attestations": {
                lane: {"argv_digest": argv_digest(self.executor.commands[lane]),
                       "read_only_attested": False}
                for lane in ("claude", "grok")},
            "pinned_environment": False})
        text = body.decode("utf-8")
        for secret_or_path in (self.OWNER, str(self.account), json.dumps(str(self.account))[1:-1], self.account.name):
            self.assertNotIn(secret_or_path, text)
        # A routed lane reports the digest its route binds.
        server.validation_service = SimpleNamespace(
            owner_token=self.OWNER, production=self.executor, account_files={"claude": (str(self.account),)},
            team_route=SimpleNamespace(configuration_digest=lambda: "t" * 64),
            openai_responses_route=SimpleNamespace(configuration_digest="r" * 64))
        status, body = get("Bearer " + self.OWNER)
        self.assertEqual((status, json.loads(body)["configuration_digests"]),
                         (200, {"antigravity": "t" * 64, "claude": expected, "openai": "r" * 64}))
        server.validation_service = None
        self.assertEqual(get("Bearer " + self.OWNER)[0], 503)

    def test_check_environment_prints_pins_without_serving_or_calling_providers(self) -> None:
        from director_orchestrator import host_provider_bridge_server as server_module
        from director_orchestrator.provider_validation import production_configuration_digest
        marker = self.root / "provider-was-called"
        provider = self.root / "marker_provider.py"
        provider.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        command = (str(Path(sys.executable).resolve(strict=True)), str(provider))
        # antigravity is always team-routed and openai is Responses-routed here;
        # no route is opened, so neither may report a native digest.
        commands = {"claude": list(command), "antigravity": list(command), "openai": [command[0], "fixture-model"]}
        environment = {
            "DIRECTOR_HOST_PROVIDER_COMMANDS_JSON": json.dumps(commands),
            "DIRECTOR_HOST_PROVIDER_WORKSPACE": str(self.root),
            "DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON": json.dumps(
                {lane: [str(self.account)] for lane in commands}),
            "DIRECTOR_PROVIDER_VALIDATION_OPENAI_RESPONSES_CONFIG": str(self.root / "never-opened.json"),
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
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:51 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:02 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 04:42 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 05:23 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:35 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:07 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:49 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:21 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 18:29 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:53 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 02:15 UTC | END]]
