# [[Claude Code | Claude Opus 5 | 2026-09-08 | 13:05 UTC | BEGIN]]
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
import hashlib
import os
import sys
import time
import unittest
import jwt
import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

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
    Microsoft365CopilotCapabilityEvidence,
    Microsoft365CopilotReadinessVerifier,
    Microsoft365CopilotWorkIQTransport,
    MicrosoftOIDCDelegatedTokenVerifier,
    NativeCommandPacketTransport,
    OpenAIResponsesPacketTransport,
    PRIMARY_PROVIDER_LANES,
    PublicSearchCommandTransport,
    ProviderExternalBlocker,
    PrimaryProviderCapability,
    MICROSOFT_365_COPILOT_EXTERNAL_BLOCKER,
    build_graph_native_packet_adapters,
    provider_lane_external_blockers,
    provider_lane_readiness,
    read_protected_delegated_token_file,
    discover_local_primary_command_transports,
)


class _Transport:
    def send(self, _packet, *, context):
        raise AssertionError("factory tests never invoke providers")


def work_iq_evidence(**overrides):
    current = int(time.time())
    values = {
        "conversation_id": "0d110e7e-2b7e-4270-a899-fd2af6fde333",
        "endpoint": "https://workiq.svc.cloud.microsoft/rest",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "issuer": "https://login.microsoftonline.com/11111111-1111-4111-8111-111111111111/v2.0",
        "audience": "api://workiq.svc.cloud.microsoft",
        "delegated_scope": "api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask",
        "token_subject_digest": "sha256:" + "a" * 64,
        "challenge_id": "challenge-1",
        "response_request_id": "request-1",
        "response_digest": "sha256:" + "b" * 64,
        "observed_at": current,
        "expires_at": current + 300,
    }
    values.update(overrides)
    return Microsoft365CopilotCapabilityEvidence(**values)


class _FakeReadinessVerifier:
    def __init__(self, evidence=None, error=None):
        self.evidence = evidence
        self.error = error
        self.calls = []

    def verify(
        self, token, *, tenant_id, expected_subject, expected_object_id, now=None
    ):
        self.calls.append(
            (token, tenant_id, expected_subject, expected_object_id, now)
        )
        if self.error is not None:
            raise self.error
        return self.evidence


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

    def test_delegated_token_file_checks_acl_before_same_handle_read(self):
        with TemporaryDirectory() as directory:
            token_file = Path(directory) / "work-iq.token"
            token_file.write_text("delegated-token\n", encoding="utf-8")
            if os.name != "nt":
                token_file.chmod(0o600)
            with patch(
                "director_orchestrator.primary_transports._validate_windows_token_file_acl"
            ) as acl:
                self.assertEqual(
                    "delegated-token",
                    read_protected_delegated_token_file(str(token_file)),
                )
            if os.name == "nt":
                acl.assert_called_once_with(token_file.resolve())
                with patch(
                    "director_orchestrator.primary_transports._validate_windows_token_file_acl",
                    side_effect=ValueError("broad ACL"),
                ), self.assertRaisesRegex(ValueError, "broad ACL"):
                    read_protected_delegated_token_file(str(token_file))
            else:
                token_file.chmod(0o644)
                with self.assertRaisesRegex(ValueError, "permissions are too broad"):
                    read_protected_delegated_token_file(str(token_file))

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
    def test_exact_nine_callable_lanes_build_without_legacy_store(self):
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
        observed = capabilities(unavailable="microsoft-365-copilot")
        transports = {
            provider: _Transport()
            for provider in PRIMARY_PROVIDER_LANES
            if provider != "microsoft-365-copilot"
        }

        adapters = build_graph_native_packet_adapters(
            transports=transports,
            capabilities=observed,
        )

        self.assertNotIn("microsoft-365-copilot", adapters)
        self.assertFalse(provider_lane_readiness(observed)["microsoft-365-copilot"])

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
        # The lane pins --sandbox enabled, which the Cursor CLI supports only on
        # macOS and Linux, so discovery withholds it elsewhere.  Pin a supported
        # platform here: this case covers the command contract, and the
        # platform behaviour has its own test.  Without this the result depends
        # on whichever host runs the suite, and CI runs both.
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports.sys.platform", "linux"
        ), patch(
            # A POSIX launcher, matching the platform pinned above.  A Windows
            # .exe here would be the WSL interop case, which discovery now
            # rejects because that process still refuses --sandbox enabled.
            "director_orchestrator.primary_transports._which",
            side_effect=lambda names: "cursor-agent" if "cursor-agent" in names else None,
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
        self.assertFalse(observed["microsoft-365-copilot"].callable)

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

    def test_discovery_never_promotes_microsoft_365_copilot_without_evidence(self):
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which",
            side_effect=lambda names: "observed.exe" if "claude.exe" in names else None,
        ):
            transports, observed = discover_local_primary_command_transports(
                cwd=directory,
                authenticated={"claude": True, "microsoft-365-copilot": True},
            )

        self.assertIn("claude", transports)
        self.assertNotIn("microsoft-365-copilot", transports)
        self.assertFalse(observed["microsoft-365-copilot"].callable)
        self.assertEqual("external-blocker", observed["microsoft-365-copilot"].transport_kind)
        self.assertEqual(
            {"microsoft-365-copilot": MICROSOFT_365_COPILOT_EXTERNAL_BLOCKER},
            provider_lane_external_blockers(observed),
        )

    def test_microsoft_365_copilot_blocker_cannot_coexist_with_callable_claim(self):
        observed = capabilities()
        observed["microsoft-365-copilot"] = PrimaryProviderCapability(
            provider_id="microsoft-365-copilot",
            backend_identity="microsoft-365-copilot",
            transport_kind="test-observation",
            executable_observed=True,
            authenticated_observed=True,
            external_blocker=ProviderExternalBlocker(
                MICROSOFT_365_COPILOT_EXTERNAL_BLOCKER,
                "production-supported API and delegated capability evidence are required",
            ),
        )

        with self.assertRaisesRegex(ValueError, "cannot retain an external blocker"):
            build_graph_native_packet_adapters(
                transports={provider: _Transport() for provider in PRIMARY_PROVIDER_LANES},
                capabilities=observed,
            )

    def test_microsoft_365_copilot_requires_exact_work_iq_capability_evidence(self):
        evidence = work_iq_evidence()
        verifier = _FakeReadinessVerifier(evidence)
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ):
            transports, observed = discover_local_primary_command_transports(
                cwd=directory,
                authenticated={},
                microsoft_365_copilot_access_token="delegated-token",
                microsoft_365_copilot_tenant_id=evidence.tenant_id,
                microsoft_365_copilot_expected_subject="user-1",
                microsoft_365_copilot_expected_object_id=(
                    "22222222-2222-4222-8222-222222222222"
                ),
                microsoft_365_copilot_readiness_verifier=verifier,
            )
        self.assertIsInstance(
            transports["microsoft-365-copilot"], Microsoft365CopilotWorkIQTransport
        )
        self.assertTrue(observed["microsoft-365-copilot"].callable)
        self.assertEqual(
            "work-iq-rest-production",
            observed["microsoft-365-copilot"].transport_kind,
        )
        self.assertEqual(
            [
                (
                    "delegated-token", evidence.tenant_id, "user-1",
                    "22222222-2222-4222-8222-222222222222", None,
                )
            ], verifier.calls
        )

        invalid = _FakeReadinessVerifier(error=ValueError("invalid token"))
        with TemporaryDirectory() as directory:
            failed_transports, failed_observed = discover_local_primary_command_transports(
                cwd=directory,
                authenticated={},
                microsoft_365_copilot_access_token="delegated-token",
                microsoft_365_copilot_tenant_id=evidence.tenant_id,
                microsoft_365_copilot_expected_subject="user-1",
                microsoft_365_copilot_expected_object_id=(
                    "22222222-2222-4222-8222-222222222222"
                ),
                microsoft_365_copilot_readiness_verifier=invalid,
            )
        self.assertNotIn("microsoft-365-copilot", failed_transports)
        self.assertFalse(failed_observed["microsoft-365-copilot"].callable)

    def test_microsoft_oidc_verifier_checks_signature_tenant_audience_scope_and_cache(self):
        tenant = "11111111-1111-4111-8111-111111111111"
        issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update({"kid": "key-1", "use": "sig", "alg": "RS256"})
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": "api://workiq.svc.cloud.microsoft",
            "exp": now + 600,
            "nbf": now - 1,
            "tid": tenant,
            "sub": "user-1",
            "oid": "22222222-2222-4222-8222-222222222222",
            "scp": "api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask",
        }
        token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "key-1"})
        metadata = Mock()
        metadata.json.return_value = {
            "issuer": issuer,
            "jwks_uri": f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        }
        keys = Mock()
        keys.json.return_value = {"keys": [jwk]}
        client = Mock()
        client.get.side_effect = [metadata, keys]
        factory = MagicMock()
        factory.return_value.__enter__.return_value = client
        verifier = MicrosoftOIDCDelegatedTokenVerifier(client_factory=factory)

        verify_args = {
            "tenant_id": tenant,
            "expected_subject": "user-1",
            "expected_object_id": "22222222-2222-4222-8222-222222222222",
        }
        self.assertEqual("user-1", verifier.verify(token, **verify_args)["sub"])
        self.assertEqual("user-1", verifier.verify(token, **verify_args)["sub"])
        self.assertEqual(2, client.get.call_count)

        wrong_scope = jwt.encode(
            {**claims, "scp": claims["scp"] + " User.Read"},
            key,
            algorithm="RS256",
            headers={"kid": "key-1"},
        )
        with self.assertRaisesRegex(ValueError, "lacks exact delegated scope"):
            verifier.verify(wrong_scope, **verify_args)

        wrong_audience = jwt.encode(
            {**claims, "aud": "api://other"},
            key,
            algorithm="RS256",
            headers={"kid": "key-1"},
        )
        with self.assertRaises(jwt.InvalidAudienceError):
            verifier.verify(wrong_audience, **verify_args)

        wrong_tenant = jwt.encode(
            {**claims, "tid": "22222222-2222-4222-8222-222222222222"},
            key,
            algorithm="RS256",
            headers={"kid": "key-1"},
        )
        with self.assertRaisesRegex(ValueError, "tenant is not exact"):
            verifier.verify(wrong_tenant, **verify_args)

        not_yet_valid = jwt.encode(
            {**claims, "nbf": now + 600},
            key,
            algorithm="RS256",
            headers={"kid": "key-1"},
        )
        with self.assertRaises(jwt.ImmatureSignatureError):
            verifier.verify(not_yet_valid, **verify_args)

        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(
            claims, attacker, algorithm="RS256", headers={"kid": "key-1"}
        )
        with self.assertRaises(jwt.InvalidSignatureError):
            verifier.verify(forged, **verify_args)

        with self.assertRaisesRegex(ValueError, "subject is not authorized"):
            verifier.verify(token, **{**verify_args, "expected_subject": "user-2"})
        with self.assertRaisesRegex(ValueError, "object id is not authorized"):
            verifier.verify(
                token,
                **{
                    **verify_args,
                    "expected_object_id": "33333333-3333-4333-8333-333333333333",
                },
            )

    def test_work_iq_readiness_is_bound_to_authenticated_prompt_free_response(self):
        evidence = work_iq_evidence()
        token_verifier = Mock()
        token_verifier.verify.return_value = {
            "iss": evidence.issuer,
            "aud": evidence.audience,
            "exp": int(time.time()) + 600,
            "nbf": int(time.time()) - 1,
            "tid": evidence.tenant_id,
            "sub": "user-1",
            "oid": "22222222-2222-4222-8222-222222222222",
            "scp": evidence.delegated_scope,
        }
        response = Mock()
        response.status_code = 201
        response.content = json.dumps(
            {"id": evidence.conversation_id, "status": "active", "turnCount": 0}
        ).encode()
        response.json.return_value = {
            "id": evidence.conversation_id,
            "status": "active",
            "turnCount": 0,
        }
        response.headers = {
            "request-id": "server-request-1",
            "client-request-id": "22222222-2222-4222-8222-222222222222",
        }
        client = Mock()
        client.post.return_value = response
        factory = MagicMock()
        factory.return_value.__enter__.return_value = client
        verifier = Microsoft365CopilotReadinessVerifier(
            token_verifier=token_verifier,
            client_factory=factory,
            challenge_factory=lambda: "22222222-2222-4222-8222-222222222222",
        )

        observed = verifier.verify(
            "signed-token", tenant_id=evidence.tenant_id,
            expected_subject="user-1",
            expected_object_id="22222222-2222-4222-8222-222222222222",
        )

        client.post.assert_called_once_with(
            "https://workiq.svc.cloud.microsoft/rest/conversations",
            headers={
                "Authorization": "Bearer signed-token",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "client-request-id": "22222222-2222-4222-8222-222222222222",
                "return-client-request-id": "true",
            },
            json={},
        )
        self.assertEqual("server-request-1", observed.response_request_id)
        self.assertEqual(evidence.conversation_id, observed.conversation_id)

    def test_work_iq_readiness_rejects_unproven_entitlement_response(self):
        evidence = work_iq_evidence()
        token_verifier = Mock()
        token_verifier.verify.return_value = {
            "iss": evidence.issuer,
            "aud": evidence.audience,
            "exp": int(time.time()) + 600,
            "nbf": int(time.time()) - 1,
            "tid": evidence.tenant_id,
            "sub": "user-1",
            "oid": "22222222-2222-4222-8222-222222222222",
            "scp": evidence.delegated_scope,
        }
        response = Mock()
        response.content = b'{"error":"license required"}'
        response.status_code = 403
        response.json.return_value = {"error": "license required"}
        response.headers = {}
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "forbidden", request=Mock(), response=Mock()
        )
        client = Mock()
        client.post.return_value = response
        factory = MagicMock()
        factory.return_value.__enter__.return_value = client
        verifier = Microsoft365CopilotReadinessVerifier(
            token_verifier=token_verifier,
            client_factory=factory,
            challenge_factory=lambda: "22222222-2222-4222-8222-222222222222",
        )
        with self.assertRaises(httpx.HTTPStatusError):
            verifier.verify(
                "signed-token", tenant_id=evidence.tenant_id,
                expected_subject="user-1",
                expected_object_id="22222222-2222-4222-8222-222222222222",
            )

    def test_microsoft_365_copilot_uses_one_exact_production_work_iq_turn(self):
        evidence = work_iq_evidence()
        response = Mock()
        response.content = b'{"id":"bound"}'
        response.json.return_value = {
            "id": evidence.conversation_id,
            "turnCount": 1,
            "messages": [{"text": "review evidence"}],
        }
        client = Mock()
        client.post.return_value = response
        with patch("director_orchestrator.primary_transports.httpx.Client") as factory:
            factory.return_value.__enter__.return_value = client
            result = Microsoft365CopilotWorkIQTransport(
                "delegated-token", evidence
            ).send(json.dumps(self._work()).encode(), context=self._context())

        factory.assert_called_once_with(timeout=30, follow_redirects=False)
        client.post.assert_called_once()
        self.assertEqual(
            "https://workiq.svc.cloud.microsoft/rest/conversations/"
            + evidence.conversation_id
            + "/chat",
            client.post.call_args.args[0],
        )
        self.assertFalse(
            client.post.call_args.kwargs["json"]["contextualResources"]
            ["webContext"]["isWebEnabled"]
        )
        self.assertEqual("microsoft-365-copilot", result["provider"])

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
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 13:05 UTC | END]]
