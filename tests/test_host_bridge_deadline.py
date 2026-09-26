# [[Codex Director | GPT-6 Astra | 2026-09-22 | 22:38 UTC | BEGIN]]
"""Real local HTTP admission and host execution cutoff; zero provider calls."""
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from director_orchestrator.host_provider_bridge import (
    BridgeClaimStore, HostBridgePacketTransport, HostProviderBridgeError,
    WindowsHostProviderExecutor,
)
from director_orchestrator.host_provider_bridge_server import _Handler
from tests.test_host_provider_bridge import SECRET, request, _Verifier


class HostBridgeDeadlineTests(unittest.TestCase):
    def test_actual_http_preflight_and_dispatch_preserve_deadline_and_authority(self):
        executed = []

        class Executor:
            commands = {'grok': ['synthetic']}

            def available_providers(self):
                return ['grok']

            def execute(self, req):
                self_outer.assertEqual(req.host_deadline.split(':')[0], _Handler._clock_id)
                self_outer.assertLessEqual(float.fromhex(req.host_deadline.split(':')[1]), cutoff)
                self_outer.assertGreater(float.fromhex(req.host_deadline.split(':')[1]), time.monotonic())
                executed.append(req)
                return b'{"outcome":"verified local response"}'

        self_outer = self
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
            server.bridge_secret = SECRET
            server.claim_store = BridgeClaimStore(Path(root) / 'claims.sqlite')
            server.executor = Executor()
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                transport = HostBridgePacketTransport('grok',
                    f'http://127.0.0.1:{server.server_port}/v1/provider/dispatch', SECRET,
                    writer_owner_token_digest='sha256:' + 'f' * 64)
                packet = json.dumps(dict(task_id='task-1', assignment_id='assignment-1',
                                         idempotency_key='one-dispatch')).encode()
                cutoff = time.monotonic() + 20
                rechecked = []
                context = SimpleNamespace(deadline=cutoff, timeout_seconds=30,
                    fencing_generation=7, authority_recheck=lambda: rechecked.append(True))
                transport.preflight(packet, context=context)
                self.assertEqual(executed, [])
                result = transport.send(packet, context=context)
                self.assertEqual(result['outcome'], 'verified local response')
                self.assertEqual(len(executed), 1)
                original_clock = _Handler._clock_id
                context.authority_recheck = lambda: setattr(_Handler, '_clock_id', '0' * 32)
                try:
                    with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
                        transport.send(packet, context=context)
                    self.assertEqual(len(executed), 1)
                finally:
                    _Handler._clock_id = original_clock
                self.assertEqual(rechecked, [True])
                with self.assertRaisesRegex(HostProviderBridgeError, 'authentication'):
                    replace(executed[0], host_deadline=_Handler._clock_id + ':' +
                            float(cutoff + 30).hex()).validate(SECRET)
                with self.assertRaisesRegex(RuntimeError, 'exhausted'):
                    transport.preflight(packet, context=SimpleNamespace(
                        deadline=time.monotonic() - 1, timeout_seconds=30))
                self.assertEqual(len(executed), 1)
            finally:
                server.shutdown()
                worker.join(5)
                server.server_close()

    def test_host_executor_uses_existing_tree_owned_runner_and_rejects_late_fence(self):
        with tempfile.TemporaryDirectory() as root:
            executor = WindowsHostProviderExecutor({'claude': [sys.executable, '-c', 'pass']},
                cwd=root, timeout_seconds=900, writer_fence_verifier=_Verifier())
            cutoff = time.monotonic() + 5
            candidate = replace(request(), host_deadline=_Handler._clock_id + ':' + cutoff.hex())
            with patch('director_orchestrator.primary_transports._run_bounded_command',
                       return_value=subprocess.CompletedProcess([], 0, b'{}', b'')) as run:
                self.assertEqual(executor.execute(candidate), b'{}')
                self.assertEqual(run.call_args.kwargs['cutoff'], cutoff)
                self.assertEqual(run.call_args.kwargs['input_bytes'], candidate.packet)
            class SlowFence:
                def verify(self, req):
                    pass
            executor.writer_fence_verifier = SlowFence()
            with patch('director_orchestrator.host_provider_bridge.time.monotonic',
                       side_effect=[cutoff - 1, cutoff + 1]), patch(
                           'director_orchestrator.primary_transports._run_bounded_command') as run:
                with self.assertRaisesRegex(HostProviderBridgeError, 'before execution'):
                    executor.execute(candidate)
                run.assert_not_called()

    def test_preflight_refuses_legacy_host_without_clock_contract(self):
        from director_orchestrator.host_provider_bridge import HostBridgeChallengeEvidence
        transport = HostBridgePacketTransport('claude',
            'http://127.0.0.1:18765/v1/provider/dispatch', SECRET,
            writer_owner_token_digest='sha256:' + 'f' * 64)
        with patch('director_orchestrator.host_provider_bridge.challenge_host_provider_bridge',
                   return_value=HostBridgeChallengeEvidence(frozenset({'claude'}), 'digest', 0)):
            with self.assertRaisesRegex(RuntimeError, 'support is unavailable'):
                transport.preflight(b'{}', context=SimpleNamespace(
                    deadline=time.monotonic() + 10, timeout_seconds=30))


if __name__ == '__main__':
    unittest.main()
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 22:38 UTC | END]]
