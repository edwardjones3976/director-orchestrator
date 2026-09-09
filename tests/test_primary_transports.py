# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 01:59 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:08 UTC | BEGIN]]
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.host_provider_bridge import (
    HostBridgeChallengeEvidence,
    HostBridgePacketTransport,
)
from director_orchestrator.primary_default import build_automatic_primary_live_dependencies
from director_orchestrator.models import AntigravityDirective, AntigravityFixtureLane
from director_orchestrator.packet_routing import antigravity_directive_objective
from director_orchestrator.primary_transports import (
    ANTIGRAVITY_CLOSED_PAYLOAD_SCHEMA,
    BoundedJsonCommandTransport,
    antigravity_closed_payload,
    NativeCommandPacketTransport,
    OpenAIResponsesPacketTransport,
    PRIMARY_PROVIDER_LANES,
    PublicSearchCommandTransport,
    PrimaryProviderCapability,
    build_graph_native_packet_adapters,
    provider_lane_readiness,
    discover_local_primary_command_transports,
)


class _Transport:
    def send(self, _packet, *, context):
        raise AssertionError("factory tests never invoke providers")


def capabilities(*, unavailable: str | None = None):
    result = {}
    for provider_id in PRIMARY_PROVIDER_LANES:
        result[provider_id] = PrimaryProviderCapability(
            provider_id=provider_id,
            backend_identity="gemini" if provider_id == "antigravity" else provider_id,
            transport_kind="test-observation",
            executable_observed=provider_id != unavailable,
            authenticated_observed=provider_id != unavailable,
        )
    return result


class GraphNativeProviderFactoryTests(unittest.TestCase):
    def _context(self, *, generation: int = 4) -> ProviderAdapterContext:
        return ProviderAdapterContext(
            task_id="DIR-EXAMPLE",
            assignment_id="DIR-EXAMPLE-1",
            idempotency_key="DIR-EXAMPLE:1:review",
            work_packet_digest="sha256:" + "1" * 64,
            writer_token_digest="sha256:" + "2" * 64,
            timeout_seconds=30,
            budget_usd=1.0,
            max_hops=1,
            max_revisions=0,
            allowed_tools=(),
            allowed_external_effects=(),
            fencing_generation=generation,
        )


    def _work(self, *, objective: str = "Find official public evidence"):
        return {
            "task_id": "DIR-EXAMPLE",
            "assignment_id": "DIR-EXAMPLE-1",
            "idempotency_key": "DIR-EXAMPLE:1:review",
            "authority_snapshot_digest": "sha256:" + "a" * 64,
            "correlation": {
                "correlation_id": "corr", "source_event_id": "source",
                "slack_thread_ts": None, "github_issue": None,
                "github_pull_request": None,
            },
            "objective": objective,
            "approval_requirements": ["owner approved exact public query"],
            "allowed_external_effects": [],
            "acceptance_checks": ["source evidence is relevant"],
        }
    def test_exact_eight_callable_lanes_build_without_legacy_store(self):
        transports = {provider: _Transport() for provider in PRIMARY_PROVIDER_LANES}

        adapters = build_graph_native_packet_adapters(
            transports=transports,
            capabilities=capabilities(),
        )

        self.assertEqual(set(PRIMARY_PROVIDER_LANES), set(adapters))
        self.assertEqual(
            set(PRIMARY_PROVIDER_LANES),
            {adapter.provider for adapter in adapters.values()},
        )

    def test_unavailable_lane_is_omitted_and_reports_false(self):
        observed = capabilities(unavailable="github-copilot")
        transports = {
            provider: _Transport()
            for provider in PRIMARY_PROVIDER_LANES
            if provider != "github-copilot"
        }

        adapters = build_graph_native_packet_adapters(
            transports=transports,
            capabilities=observed,
        )

        self.assertNotIn("github-copilot", adapters)
        self.assertFalse(provider_lane_readiness(observed)["github-copilot"])

    def test_unavailable_lane_cannot_smuggle_a_transport(self):
        with self.assertRaisesRegex(ValueError, "unavailable provider lane"):
            build_graph_native_packet_adapters(
                transports={provider: _Transport() for provider in PRIMARY_PROVIDER_LANES},
                capabilities=capabilities(unavailable="github-copilot"),
            )

    def test_antigravity_requires_explicit_gemini_backend(self):
        observed = capabilities()
        observed["antigravity"] = PrimaryProviderCapability(
            provider_id="antigravity",
            backend_identity="antigravity",
            transport_kind="test-observation",
            executable_observed=True,
            authenticated_observed=True,
        )

        with self.assertRaisesRegex(ValueError, "Gemini backend"):
            build_graph_native_packet_adapters(
                transports={provider: _Transport() for provider in PRIMARY_PROVIDER_LANES},
                capabilities=observed,
            )

    def test_command_transport_is_shell_free_single_attempt_and_fenced(self):
        context = self._context()
        with TemporaryDirectory() as directory:
            transport = BoundedJsonCommandTransport(
                "claude", ("packet-adapter.exe", "--stdio"), cwd=Path(directory)
            )
            completed = type(
                "Completed",
                (),
                {"returncode": 0, "stdout": b'{"provider":"claude"}', "stderr": b""},
            )()
            with patch("subprocess.run", return_value=completed) as run:
                result = transport.send(b'{"schema_version":"1.0"}', context=context)

        self.assertEqual("claude", result["provider"])
        self.assertEqual(1, run.call_count)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(b'{"schema_version":"1.0"}', run.call_args.kwargs["input"])

    def test_command_transport_rejects_legacy_unfenced_context(self):
        context = self._context(generation=0)
        with TemporaryDirectory() as directory:
            transport = BoundedJsonCommandTransport(
                "claude", ("packet-adapter.exe", "--stdio"), cwd=Path(directory)
            )
            with patch("subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, "writer fencing"):
                    transport.send(b"{}", context=context)
        run.assert_not_called()

    def test_native_envelope_is_rebound_to_packet_and_exact_lane(self):
        work = {
            "task_id": "DIR-EXAMPLE",
            "assignment_id": "DIR-EXAMPLE-1",
            "idempotency_key": "DIR-EXAMPLE:1:review",
            "authority_snapshot_digest": "sha256:" + "a" * 64,
            "correlation": {
                "correlation_id": "corr",
                "source_event_id": "source",
                "slack_thread_ts": None,
                "github_issue": None,
                "github_pull_request": None,
            },
            "allowed_external_effects": [],
            "acceptance_checks": ["check"],
        }
        native = {
            "session_id": "authenticated-session",
            "result": '{"outcome":"done","status":"SUCCEEDED"}',
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }
        with TemporaryDirectory() as directory:
            transport = NativeCommandPacketTransport(
                "claude", ("claude.exe", "--print"), cwd=directory
            )
            completed = type(
                "Completed", (),
                {"returncode": 0, "stdout": json.dumps(native).encode(), "stderr": b""},
            )()
            with patch("subprocess.run", return_value=completed) as run:
                result = transport.send(json.dumps(work).encode(), context=self._context())

        self.assertEqual("claude", result["provider"])
        self.assertEqual("authenticated-session", result["run_reference"])
        self.assertEqual(work["authority_snapshot_digest"], result["authority_snapshot_digest"])
        self.assertEqual("NOT_RUN", result["acceptance"][0]["status"])
        self.assertEqual(1, run.call_count)
        self.assertFalse(run.call_args.kwargs["shell"])

    def _antigravity_work(self):
        directive = AntigravityDirective(lane=AntigravityFixtureLane.MODULE_SKELETON)
        return {
            "schema_version": "1.0",
            "task_id": "DIR-LG-ANTIGRAVITY",
            "assignment_id": "DIR-LG-ANTIGRAVITY-5",
            "idempotency_key": "DIR-LG-ANTIGRAVITY:1:5:aa11bb22",
            "project": "confidential-project",
            "repository": "owner/confidential-repository",
            "starting_ref": "c" * 40,
            "authority_base_ref": "main",
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
                "correlation_id": "corr-antigravity", "source_event_id": "source",
                "slack_thread_ts": None, "github_issue": None,
                "github_pull_request": None,
            },
            "allowed_external_effects": [],
        }

    def test_antigravity_outbound_payload_leaks_no_project_authority(self):
        work = self._antigravity_work()
        native = {
            "session_id": "antigravity-session",
            "result": '{"outcome":"generic module skeleton","status":"SUCCEEDED"}',
        }
        completed = type(
            "Completed", (),
            {"returncode": 0, "stdout": json.dumps(native).encode(), "stderr": b""},
        )()
        with TemporaryDirectory() as directory:
            transport = NativeCommandPacketTransport(
                "antigravity",
                ("agy.exe", "--print"),
                cwd=directory,
                prompt_mode="stdin",
                payload_mode="antigravity-closed-directive",
            )
            with patch("subprocess.run", return_value=completed) as run:
                result = transport.send(
                    json.dumps(work).encode(), context=self._context()
                )

        sent = run.call_args.kwargs["input"].decode("utf-8")
        # Nothing that identifies the project may cross the boundary.
        for confidential in (
            work["repository"],
            work["project"],
            work["starting_ref"],
            work["authority_snapshot_digest"],
            work["authority_snapshot"][0]["path"],
            work["authority_snapshot"][0]["blob_sha"],
            work["owned_paths"][0],
            work["prohibited_paths"][0],
            work["constraints"][0],
            work["acceptance_checks"][0],
            work["authority_base_ref"],
        ):
            self.assertNotIn(confidential, sent)
        for allowed in (
            work["objective"],
            work["task_id"],
            work["assignment_id"],
            work["idempotency_key"],
            work["correlation"]["correlation_id"],
        ):
            self.assertIn(allowed, sent)
        self.assertIn(ANTIGRAVITY_CLOSED_PAYLOAD_SCHEMA, sent)
        self.assertFalse(run.call_args.kwargs["shell"])
        # The Director still rebinds the result to the full private packet.
        self.assertEqual("antigravity", result["provider"])
        self.assertEqual(work["authority_snapshot_digest"], result["authority_snapshot_digest"])
        self.assertEqual("NOT_RUN", result["acceptance"][0]["status"])

    def test_antigravity_payload_requires_a_bound_closed_directive(self):
        work = self._antigravity_work()
        work["objective"] = "Implement the owner's confidential change"

        with self.assertRaisesRegex(RuntimeError, "bound closed directive"):
            antigravity_closed_payload(work)

        effectful = self._antigravity_work()
        effectful["allowed_external_effects"] = ["OPEN_DRAFT_PULL_REQUEST"]
        with self.assertRaisesRegex(RuntimeError, "cannot receive external effects"):
            antigravity_closed_payload(effectful)

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "bound to the Antigravity lane"):
                NativeCommandPacketTransport(
                    "claude",
                    ("claude.exe",),
                    cwd=directory,
                    payload_mode="antigravity-closed-directive",
                )
            with self.assertRaisesRegex(ValueError, "bound to the Antigravity lane"):
                NativeCommandPacketTransport(
                    "antigravity", ("agy.exe",), cwd=directory
                )

    def test_openai_is_one_tool_free_bound_responses_call(self):
        work = self._work()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"id": "resp_authenticated", "output_text": "done"}
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.post.return_value = response
        transport = OpenAIResponsesPacketTransport(
            api_key="test-secret", model="gpt-test"
        )
        with patch("director_orchestrator.primary_transports.httpx.Client", return_value=client):
            result = transport.send(json.dumps(work).encode(), context=self._context())

        self.assertEqual("openai", result["provider"])
        self.assertEqual("resp_authenticated", result["run_reference"])
        self.assertEqual(1, client.post.call_count)
        body = client.post.call_args.kwargs["json"]
        self.assertEqual([], body["tools"])
        self.assertFalse(body["store"])
        self.assertNotIn("test-secret", json.dumps(body))

    def test_public_search_sends_only_approved_query_once_without_shell(self):
        work = self._work(objective="current public documentation")
        work["repository"] = "owner/private-repo"
        native = {"hits": [{"url": "https://example.test", "title": "Evidence"}], "total": 1}
        completed = type(
            "Completed", (),
            {"returncode": 0, "stdout": json.dumps(native).encode(), "stderr": b""},
        )()
        with TemporaryDirectory() as directory:
            transport = PublicSearchCommandTransport(
                "perplexity",
                ("wsl.exe", "--distribution", "Ubuntu", "--exec", "/safe/pplx"),
                cwd=directory,
                maximum_results=3,
            )
            with patch("subprocess.run", return_value=completed) as run:
                result = transport.send(json.dumps(work).encode(), context=self._context())

        self.assertEqual("perplexity", result["provider"])
        self.assertEqual(1, run.call_count)
        self.assertFalse(run.call_args.kwargs["shell"])
        argv = run.call_args.args[0]
        self.assertIn("current public documentation", argv)
        self.assertIn("--limit", argv)
        self.assertEqual("3", argv[argv.index("--limit") + 1])
        self.assertNotIn(work["task_id"], argv)
        self.assertNotIn(work["authority_snapshot_digest"], argv)
        self.assertNotIn(work["repository"], argv)

    def test_public_search_requires_approval_and_forbids_effect_authority(self):
        with TemporaryDirectory() as directory:
            transport = PublicSearchCommandTransport(
                "firecrawl", ("firecrawl.cmd",), cwd=directory
            )
            work = self._work()
            work["approval_requirements"] = []
            with patch("subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, "approval"):
                    transport.send(json.dumps(work).encode(), context=self._context())
            run.assert_not_called()
            work = self._work()
            work["allowed_external_effects"] = ["WRITE_REPOSITORY"]
            with patch("subprocess.run") as run:
                with self.assertRaisesRegex(RuntimeError, "external effects"):
                    transport.send(json.dumps(work).encode(), context=self._context())
            run.assert_not_called()

    def test_provider_children_receive_only_lane_auth_and_minimal_os_environment(self):
        secrets = {
            "SYSTEMROOT": r"C:\Windows",
            "PATH": r"C:\Windows\System32",
            "FIRECRAWL_API_KEY": "lane-key",
            "OPENAI_API_KEY": "unrelated-openai",
            "GROK_API_KEY": "unrelated-grok",
            "ORCHESTRATOR_SHARED_SECRET": "director-secret",
            "SLACK_BOT_TOKEN": "slack-secret",
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET": "bridge-secret",
            "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET": "callback-secret",
            "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET": "effect-secret",
        }
        completed = type(
            "Completed", (),
            {"returncode": 0, "stdout": b'{"hits":[],"total":0}', "stderr": b""},
        )()
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, secrets, clear=True
        ):
            transport = PublicSearchCommandTransport(
                "firecrawl", ("firecrawl.exe",), cwd=directory
            )
            with patch("subprocess.run", return_value=completed) as run:
                transport.send(json.dumps(self._work()).encode(), context=self._context())

        child = run.call_args.kwargs["env"]
        self.assertEqual("lane-key", child["FIRECRAWL_API_KEY"])
        self.assertEqual(r"C:\Windows", child["SYSTEMROOT"])
        for secret_name in (
            "OPENAI_API_KEY", "GROK_API_KEY", "ORCHESTRATOR_SHARED_SECRET",
            "SLACK_BOT_TOKEN", "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
            "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
            "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET",
        ):
            self.assertNotIn(secret_name, child)

    def test_provider_environment_rejects_supplied_cross_lane_credentials(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "non-lane credential"):
                BoundedJsonCommandTransport(
                    "claude",
                    ("adapter.exe",),
                    cwd=directory,
                    environment={"OPENAI_API_KEY": "wrong-lane"},
                )

    def test_bounded_and_native_children_do_not_inherit_director_secrets(self):
        inherited = {
            "SYSTEMROOT": r"C:\Windows",
            "ANTHROPIC_API_KEY": "claude-only",
            "OPENAI_API_KEY": "unrelated",
            "ORCHESTRATOR_SHARED_SECRET": "director",
            "SLACK_SIGNING_SECRET": "slack",
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET": "bridge",
        }
        bounded_result = type(
            "Completed", (),
            {"returncode": 0, "stdout": b'{"provider":"claude"}', "stderr": b""},
        )()
        native_result = type(
            "Completed", (),
            {
                "returncode": 0,
                "stdout": b'{"session_id":"run","result":"done"}',
                "stderr": b"",
            },
        )()
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, inherited, clear=True
        ):
            bounded = BoundedJsonCommandTransport(
                "claude", ("adapter.exe",), cwd=directory
            )
            native = NativeCommandPacketTransport(
                "claude", ("claude.exe",), cwd=directory
            )
            with patch("subprocess.run", side_effect=[bounded_result, native_result]) as run:
                bounded.send(b"{}", context=self._context())
                native.send(json.dumps(self._work()).encode(), context=self._context())

        for call in run.call_args_list:
            child = call.kwargs["env"]
            self.assertEqual("claude-only", child["ANTHROPIC_API_KEY"])
            self.assertNotIn("OPENAI_API_KEY", child)
            self.assertNotIn("ORCHESTRATOR_SHARED_SECRET", child)
            self.assertNotIn("SLACK_SIGNING_SECRET", child)
            self.assertNotIn("LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET", child)

    def test_public_query_metacharacters_are_literal_argv_not_commands(self):
        query = r"evidence & echo bad | whoami < input > output %PATH% ^ caret"
        script = (
            "import json,sys; "
            "print(json.dumps({'hits': [], 'total': 0, 'argv': sys.argv[1:]}))"
        )
        work = self._work(objective=query)
        with TemporaryDirectory() as directory:
            marker = Path(directory) / "output"
            transport = PublicSearchCommandTransport(
                "perplexity", (sys.executable, "-c", script), cwd=directory
            )
            result = transport.send(
                json.dumps(work).encode(), context=self._context()
            )

        native = json.loads(result["outcome"])
        self.assertIn(query, native["argv"])
        self.assertFalse(marker.exists())

    def test_cursor_discovery_uses_documented_status_and_read_only_contract(self):
        status = type(
            "Completed", (),
            {"returncode": 0, "stdout": b'{"isAuthenticated":true}', "stderr": b""},
        )()
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which",
            side_effect=lambda names: "cursor-agent.exe" if "cursor-agent.exe" in names else None,
        ), patch("subprocess.run", return_value=status) as run:
            transports, observed = discover_local_primary_command_transports(
                cwd=directory, authenticated={}
            )

        self.assertTrue(observed["cursor"].callable)
        cursor = transports["cursor"]
        self.assertIsInstance(cursor, NativeCommandPacketTransport)
        self.assertIn("--mode", cursor.command)
        self.assertIn("plan", cursor.command)
        self.assertIn("--sandbox", cursor.command)
        self.assertIn("enabled", cursor.command)
        self.assertEqual(1, run.call_count)
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_configured_host_bridge_replaces_only_supported_native_lanes(self):
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which",
            return_value=None,
        ):
            transports, observed = discover_local_primary_command_transports(
                cwd=directory,
                authenticated={},
                host_bridge_endpoint="https://host.docker.internal:18765/v1/provider/dispatch",
                host_bridge_secret="b" * 32,
                host_bridge_writer_owner_token_digest="sha256:" + "c" * 64,
                host_bridge_challenge=HostBridgeChallengeEvidence(
                    frozenset({"claude", "grok", "antigravity", "github-copilot"}),
                    "sha256:" + "d" * 64,
                    1,
                ),
            )

        for provider in ("claude", "grok", "antigravity", "github-copilot"):
            with self.subTest(provider=provider):
                self.assertIsInstance(transports[provider], HostBridgePacketTransport)
                self.assertTrue(observed[provider].callable)
                self.assertEqual(
                    "authenticated-windows-host-bridge",
                    observed[provider].transport_kind,
                )
        for provider in ("openai", "cursor", "perplexity", "firecrawl"):
            self.assertNotIn(provider, transports)
        self.assertNotIn("microsoft-365-copilot", transports)

        # Replacing the native Antigravity lane must not reopen the packet: the
        # bridge lane carries the same closed-directive contract, and no other
        # bridge lane is redacted.
        antigravity = transports["antigravity"]
        self.assertIs(antigravity.outbound_payload_builder, antigravity_closed_payload)
        self.assertIsNotNone(antigravity.result_normalizer)
        for provider in ("claude", "grok", "github-copilot"):
            with self.subTest(provider=provider):
                self.assertIsNone(transports[provider].outbound_payload_builder)
                self.assertIsNone(transports[provider].result_normalizer)

    def test_bridge_configuration_without_signed_challenge_is_not_callable(self):
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ):
            transports, observed = discover_local_primary_command_transports(
                cwd=directory,
                authenticated={},
                host_bridge_endpoint="http://host.docker.internal:18765/v1/provider/dispatch",
                host_bridge_secret="b" * 32,
                host_bridge_writer_owner_token_digest="sha256:" + "c" * 64,
            )
        for provider in ("claude", "grok", "antigravity", "github-copilot"):
            self.assertNotIn(provider, transports)
            self.assertFalse(observed[provider].callable)

    def test_host_bridge_configuration_is_atomic(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "configured together"):
                discover_local_primary_command_transports(
                    cwd=directory,
                    authenticated={},
                    host_bridge_endpoint="http://host.docker.internal:18765/v1/provider/dispatch",
                )
            with self.assertRaisesRegex(ValueError, "configured together"):
                discover_local_primary_command_transports(
                    cwd=directory,
                    authenticated={},
                    host_bridge_secret="b" * 32,
                )

    def test_host_bridge_secret_must_be_distinct_before_composition(self):
        repeated = "r" * 32
        settings = SimpleNamespace(
            langgraph_primary_host_bridge_secret=repeated,
            shared_secret=repeated,
            slack_gateway_secret="s" * 32,
            langgraph_primary_adapter_callback_secret="a" * 32,
            langgraph_primary_effect_reconciliation_secret="e" * 32,
        )
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            build_automatic_primary_live_dependencies(settings, None, None)


    def test_copilot_lanes_cannot_claim_production_authority(self):
        observed = capabilities()
        observed["github-copilot"] = PrimaryProviderCapability(
            provider_id="github-copilot",
            backend_identity="github-copilot",
            transport_kind="test-observation",
            executable_observed=True,
            authenticated_observed=True,
            production_authority=True,
        )

        with self.assertRaisesRegex(ValueError, "cannot receive production authority"):
            build_graph_native_packet_adapters(
                transports={provider: _Transport() for provider in PRIMARY_PROVIDER_LANES},
                capabilities=observed,
            )


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:08 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:31 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 01:59 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
