# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:54 UTC | BEGIN]]
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.host_provider_bridge import (
    BridgeClaimStore,
    BridgeRequest,
    HostProviderBridgeAmbiguous,
    HostProviderBridgeError,
    WindowsHostProviderExecutor,
    _digest,
    bridge_signature,
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
        executor = WindowsHostProviderExecutor(
            {"claude": ("claude.exe", "--print")}, cwd=self.temporary.name
        )
        completed = subprocess.CompletedProcess(
            args=("claude.exe", "--print"),
            returncode=0,
            stdout=b'{"status":"SUCCEEDED"}',
            stderr=b"",
        )
        with patch("subprocess.run", return_value=completed) as run:
            self.assertEqual(b'{"status":"SUCCEEDED"}', executor.execute(request()))
        self.assertEqual(1, run.call_count)
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertIs(run.call_args.kwargs["check"], False)

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
