# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 03:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]]
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from director_orchestrator.native_codex_transport import (
    NativeCodexPacketTransport, codex_environment, normalize_codex_jsonl,
)
from director_orchestrator.primary_transports import discover_local_primary_command_transports
from director_orchestrator.host_provider_bridge import WindowsHostProviderExecutor
from tests.test_host_provider_bridge import execution_authority

ROOT = Path(__file__).resolve().parents[1]


def work():
    return json.loads((ROOT / 'docs/schemas/model-work-packet-v1.example.json').read_text())


def events(*, final=True):
    rows = [
        {'type': 'thread.started', 'thread_id': '0199a213-81c0-7800-8aa1-bbab2a035a53'},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'id': 'item_1', 'type': 'agent_message',
          'text': json.dumps({'outcome': 'Packet evidence is insufficient to run repository checks.', 'status': 'SUCCEEDED'})}},
    ]
    if final:
        rows.append({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 2, 'output_tokens': 6}})
    return rows


def encode(rows):
    return b'\n'.join(json.dumps(row).encode() for row in rows)


class NativeCodexTests(unittest.TestCase):
    def test_auth_and_execution_share_deadline_and_overflow_never_captures(self):
        native = NativeCodexPacketTransport(str(Path(sys.executable).resolve()), 'gpt-5.6')
        observer = unittest.mock.Mock()
        context = SimpleNamespace(fencing_generation=1,max_hops=1,timeout_seconds=5,
            allowed_external_effects=(),execution_observer=observer,
            execution_authority=execution_authority("openai",execution_task_id=work()["task_id"]))
        with patch('director_orchestrator.bounded_process.run_bounded_process', side_effect=[
                (0,False,False,b'Logged in using ChatGPT',b''),
                (0,False,True,encode(events()),b'overflow')]) as bounded:
            with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
                native.send(json.dumps(work()).encode(),context=context)
            self.assertEqual(bounded.call_args_list[0].kwargs['deadline'], bounded.call_args_list[1].kwargs['deadline'])
            observer.assert_not_called()
        with patch('director_orchestrator.bounded_process.run_bounded_process') as bounded:
            context.execution_authority = None
            with self.assertRaisesRegex(ValueError, 'authority is missing'):
                native.send(json.dumps(work()).encode(),context=context)
            bounded.assert_not_called()

    def test_documented_jsonl_rebinds_session_and_usage_without_billing_claim(self):
        result = normalize_codex_jsonl(work(), encode(events()), requested_model='gpt-5.6')
        self.assertEqual('openai', result['provider'])
        self.assertTrue(result['run_reference'].startswith('codex:0199'))
        self.assertEqual(work()['authority_snapshot_digest'], result['authority_snapshot_digest'])
        self.assertEqual({'model': None, 'cost_usd': None, 'input_tokens': 12, 'output_tokens': 6, 'total_tokens': 18}, result['usage'])
        self.assertTrue(all(check['status'] == 'NOT_RUN' for check in result['acceptance']))
        self.assertFalse(result['checks'])

    def test_missing_failed_duplicate_terminal_or_tool_stream_fails_closed(self):
        variants = [events(final=False), events() + [{'type': 'turn.completed'}],
                    events(final=False) + [{'type': 'turn.failed'}],
                    events(final=False) + [{'type': 'error'}],
                    [{'type': 'turn.started'}]]
        tool = events()
        tool.insert(2, {'type': 'item.started', 'item': {'type': 'command_execution'}})
        variants.append(tool)
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                normalize_codex_jsonl(work(), encode(rows), requested_model='gpt-5.6')

    def test_api_credentials_never_enter_native_environment(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'CODEX_API_KEY': 'secret',
                                    'CODEX_HOME': 'secret-path', 'LANGGRAPH_PRIMARY_OWNER_TOKEN': 'secret'}):
            environment = codex_environment()
        self.assertFalse(set(environment) & {'OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_HOME', 'LANGGRAPH_PRIMARY_OWNER_TOKEN'})

    def test_auth_requires_exact_chatgpt_status_no_api_auth(self):
        native = NativeCodexPacketTransport(str(Path(sys.executable).resolve()), 'gpt-5.6')
        with patch('director_orchestrator.native_codex_transport.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=b'Logged in using ChatGPT\n')):
            self.assertTrue(native.authenticated())
        with patch('director_orchestrator.native_codex_transport.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=b'Logged in using an API key')):
            self.assertFalse(native.authenticated())

    def test_isolated_prompt_only_flags_and_no_retry(self):
        native = NativeCodexPacketTransport(str(Path(sys.executable).resolve()), 'gpt-5.6')
        context = SimpleNamespace(fencing_generation=1, max_hops=1, timeout_seconds=5, allowed_external_effects=(),
            execution_authority=execution_authority("openai", execution_task_id=work()["task_id"]))
        with patch.object(native, 'authenticated', return_value=True), patch.object(native, '_run', return_value=encode(events())) as run:
            native.send(json.dumps(work()).encode(), context=context)
        argv, prompt, cwd, timeout = run.call_args.args
        self.assertIn('--ignore-user-config', argv)
        self.assertIn('forced_login_method="chatgpt"', argv)
        self.assertIn('features.shell_tool=false', argv)
        self.assertNotEqual(str(ROOT), cwd)
        self.assertFalse(Path(cwd).exists())
        self.assertEqual(5, timeout)
        run.assert_called_once()
        with patch.object(native, 'authenticated', return_value=False), patch.object(native, '_run') as run:
            with self.assertRaises(RuntimeError):
                native.send(json.dumps(work()).encode(), context=context)
            run.assert_not_called()

    def test_native_selection_never_exposes_api_fallback(self):
        with patch('director_orchestrator.primary_transports._which', return_value=None):
            transports, capabilities = discover_local_primary_command_transports(
                cwd=ROOT, authenticated={'openai': True}, openai_api_key='not-used',
                openai_transport='native-codex-host')
        self.assertNotIn('openai', transports)
        self.assertFalse(capabilities['openai'].callable)

    def test_host_command_shape_and_auth_roster(self):
        with self.assertRaises(ValueError):
            WindowsHostProviderExecutor({'openai': [str(Path(sys.executable).resolve()), 'gpt-5.6', '--dangerous']}, cwd=ROOT)
        executor = WindowsHostProviderExecutor({'openai': [str(Path(sys.executable).resolve()), 'gpt-5.6']}, cwd=ROOT)
        with patch.object(executor.native_codex, 'authenticated', return_value=False):
            self.assertEqual([], executor.available_providers())
        with patch.object(executor.native_codex, 'authenticated', return_value=True):
            self.assertEqual(['openai'], executor.available_providers())

    def test_native_host_execution_checks_writer_and_returns_bound_packet(self):
        verifier = SimpleNamespace(verify=lambda request: observed.append(request))
        observed = []
        executor = WindowsHostProviderExecutor({'openai': [str(Path(sys.executable).resolve()), 'gpt-5.6']}, cwd=ROOT, writer_fence_verifier=verifier)
        request = SimpleNamespace(provider='openai', writer_generation=7, packet=json.dumps(work()).encode(),
            bound_execution_authority=lambda: execution_authority("openai", execution_task_id=work()["task_id"]))
        with patch.object(executor.native_codex, 'send', return_value={'provider': 'openai'}) as send:
            self.assertEqual({'provider': 'openai'}, json.loads(executor.execute(request)))
        self.assertEqual([request], observed)
        self.assertEqual(7, send.call_args.kwargs['context'].fencing_generation)

    def test_process_output_and_timeout_bounds(self):
        # Actual local child processes; never invokes Codex or a provider.
        with self.assertRaises(RuntimeError):
            NativeCodexPacketTransport._run([str(Path(sys.executable).resolve()), '-c', 'import time; time.sleep(10)'], b'', str(ROOT), 1)
        with self.assertRaises(RuntimeError):
            NativeCodexPacketTransport._run([str(Path(sys.executable).resolve()), '-c', 'import sys; sys.stdout.write("x"*2000000)'], b'', str(ROOT), 5)

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:32 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-08 | 03:47 UTC | END]]
