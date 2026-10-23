# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 03:56 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 19:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:25 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 15:13 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:04 UTC | BEGIN]]
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
import subprocess
import sys
import tempfile
import unittest
import asyncio
import time
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter, ProviderAdapterContext
from director_orchestrator.packet_contracts import (
    build_replay_run,
    canonical_json_bytes,
    document_digest,
)
from director_orchestrator.host_provider_bridge import (
    HostBridgeChallengeEvidence,
    HostBridgePacketTransport,
)
from director_orchestrator.primary_default import build_automatic_primary_live_dependencies
from director_orchestrator.models import AntigravityDirective, AntigravityFixtureLane
from director_orchestrator.packet_routing import (
    antigravity_directive_objective,
    compact_provider_view,
    validated_compact_view,
)
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
    provider_lane_detail,
    discover_local_primary_command_transports,
)


class _Transport:
    def send(self, _packet, *, context):
        raise AssertionError("factory tests never invoke providers")


LAUNCH_SEAM = "director_orchestrator.primary_transports.launch_contained_process"
NATIVE_ENVELOPE = json.dumps({
    "session_id": "authenticated-session",
    "result": '{"outcome":"done","status":"SUCCEEDED"}',
    "stopReason": "end_turn", "num_turns": 1,
}).encode()


class _FakeLaunch:
    """Stands in for `launch_contained_process` at the shared command boundary.

    It records the exact argv and launch options, reads the prepared stdin
    the way a child would - the whole file-backed input through EOF at the
    moment of launch, or `None` for `DEVNULL` - and records the wait every
    launched process is handed, anything the helper tries to resend through
    `communicate(input=...)`, and what cleanup that process and its guard
    receive, which is the real `stop_contained_process` acting on them. It
    can spend synthetic clock time at the spawn, let the wait expire, or run
    a hook before the wait returns. Nothing is executed.
    """

    def __init__(self, *, stdout=b"", stderr=b"", returncode=0, clock=None,
                 spawn_seconds=0.0, expire=False, before_return=None):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.clock, self.spawn_seconds, self.expire = clock, spawn_seconds, expire
        self.before_return = before_return
        self.calls, self.inputs, self.waits, self.resent, self.stopped = [], [], [], [], []

    def __call__(self, argv, **options):
        self.calls.append((tuple(argv), options))
        stdin = options.get("stdin")
        self.inputs.append(None if stdin is subprocess.DEVNULL else stdin.read())
        if self.clock is not None:
            self.clock[0] += self.spawn_seconds
        return _FakeProcess(self), _FakeGuard(self)

    @property
    def argv(self):
        return self.calls[-1][0]

    @property
    def options(self):
        return self.calls[-1][1]


class _FakeProcess:
    pid = 4242

    def __init__(self, launch):
        self.launch = launch
        self.returncode = None
        self.stdin = self.stdout = self.stderr = None

    def communicate(self, input=None, timeout=None):
        self.launch.resent.append(input)
        self.launch.waits.append(timeout)
        if self.launch.expire:
            raise subprocess.TimeoutExpired(cmd="synthetic", timeout=timeout)
        if self.launch.before_return is not None:
            self.launch.before_return()
        self.returncode = self.launch.returncode
        return self.launch.stdout, self.launch.stderr

    def poll(self):
        return self.returncode

    def kill(self):
        self.launch.stopped.append("kill")
        if self.returncode is None:
            self.returncode = -9

    def wait(self, timeout=None):
        self.launch.stopped.append(("wait", timeout))
        return self.returncode


class _FakeGuard:
    def __init__(self, launch):
        self.launch = launch
        self.active = True

    def terminate(self):
        self.launch.stopped.append("terminate")

    def close(self):
        self.launch.stopped.append("close")
        self.active = False


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
            total_token_limit=1000,
        )


    def _work(self, *, objective: str = "Find official public evidence"):
        # A complete v1 work packet: compact views are schema-validated
        # before any transport sends them.
        return {
            **json.loads((SCHEMAS / "model-work-packet-v1.example.json").read_text(encoding="utf-8")),
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

    def test_provider_lane_detail_returns_configured_and_callable(self):
        observed = capabilities(unavailable="github-copilot")
        
        # Make one lane configured but not callable
        observed["claude"] = PrimaryProviderCapability(
            provider_id="claude",
            backend_identity="claude",
            transport_kind="test-observation",
            executable_observed=True,
            authenticated_observed=False,
        )

        detail = provider_lane_detail(observed)

        # Present but not configured (executable_observed False)
        self.assertFalse(detail["github-copilot"]["configured"])
        self.assertFalse(detail["github-copilot"]["live_ready"])

        # Present but not callable
        self.assertTrue(detail["claude"]["configured"])
        self.assertFalse(detail["claude"]["live_ready"])

        # Present and callable but not routed (for example a lane outside
        # LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS): configured, never live-ready.
        self.assertTrue(detail["openai"]["configured"])
        self.assertFalse(detail["openai"]["live_ready"])

        # A lane with a composed adapter (for example team-routed antigravity)
        # is configured and live-ready even without a local executable.
        routed = provider_lane_detail(observed, routed=("github-copilot", "openai"))
        self.assertEqual(routed["github-copilot"], {"configured": True, "live_ready": True})
        self.assertEqual(routed["openai"], {"configured": True, "live_ready": True})
        self.assertEqual(routed["claude"], detail["claude"])
        for lane in routed.values():
            self.assertEqual(set(lane), {"configured", "live_ready"})

        # When the activation service's verdicts are supplied, live_ready also
        # requires the lane to be dispatchable right now.
        gated = provider_lane_detail(observed, routed=("github-copilot", "openai"), dispatchable=("openai",))
        self.assertEqual(gated["openai"], {"configured": True, "live_ready": True})
        self.assertEqual(gated["github-copilot"], {"configured": True, "live_ready": False})
        nothing = provider_lane_detail(observed, routed=("openai",), dispatchable=())
        self.assertEqual(nothing["openai"], {"configured": True, "live_ready": False})

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
            launch = _FakeLaunch(stdout=b'{"provider":"claude"}')
            with patch(LAUNCH_SEAM, launch):
                result = transport.send(b'{"schema_version":"1.0"}', context=context)

        self.assertEqual("claude", result["provider"])
        self.assertEqual(1, len(launch.calls))
        self.assertFalse(launch.options["shell"])
        self.assertEqual([b'{"schema_version":"1.0"}'], launch.inputs,
                         "the packet bytes are read from the file-backed stdin at launch")
        self.assertEqual([None], launch.resent, "nothing is handed to communicate a second time")

    def test_command_transport_rejects_legacy_unfenced_context(self):
        context = self._context(generation=0)
        with TemporaryDirectory() as directory:
            transport = BoundedJsonCommandTransport(
                "claude", ("packet-adapter.exe", "--stdio"), cwd=Path(directory)
            )
            launch = _FakeLaunch()
            with patch(LAUNCH_SEAM, launch):
                with self.assertRaisesRegex(RuntimeError, "writer fencing"):
                    transport.send(b"{}", context=context)
        self.assertEqual([], launch.calls)

    def test_responses_rechecks_before_the_token_count_and_the_generation(self):
        """Both provider requests are preceded by the authority (and grant) recheck."""

        work = self._work()
        calls = []

        def recheck(**_):
            calls.append("recheck")
            if len(calls) == 1:
                raise RuntimeError("grant lapsed before the token count")

        transport = OpenAIResponsesPacketTransport(api_key="sk-test", model="gpt-test")
        context = replace(self._context(), total_token_limit=40000, authority_recheck=recheck)
        requests = []

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                requests.append(args)
                raise AssertionError("a provider request was sent")

        with patch("director_orchestrator.primary_transports.httpx.AsyncClient", Client), \
                self.assertRaisesRegex(RuntimeError, "grant lapsed before the token count"):
            transport.send(json.dumps(work).encode(), context=context)
        self.assertEqual(["recheck"], calls)
        self.assertEqual([], requests)

    def test_responses_builds_its_input_then_rechecks_then_sends(self):
        """Nothing slow (a cache-miss rebuild reads files) runs between the
        final authority recheck and the request."""

        import director_orchestrator.primary_transports as module

        order = []
        real = module.validated_compact_view

        def build(work):
            order.append("build")
            return real(work)

        def recheck(**_):
            order.append("recheck")

        transport = OpenAIResponsesPacketTransport(api_key="sk-test", model="gpt-test")
        context = replace(self._context(), total_token_limit=40000, authority_recheck=recheck)

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                order.append("send")
                raise httpx.ConnectError("stop here")

        with patch.object(module, "validated_compact_view", side_effect=build), \
                patch("director_orchestrator.primary_transports.httpx.AsyncClient", Client), \
                self.assertRaises(RuntimeError):
            transport.send(json.dumps(self._work()).encode(), context=context)
        self.assertEqual(["build", "recheck", "send"], order)

    def test_responses_refuses_a_generation_whose_input_changed_after_the_count(self):
        import director_orchestrator.primary_transports as module

        views = iter([{"schema_version": "a"}, {"schema_version": "b"}])
        requests = []
        with patch.object(module, "validated_compact_view", side_effect=lambda work: next(views)), \
                self.assertRaisesRegex(RuntimeError, "input changed after its token count"):
            self._openai_send([{"object": "response.input_tokens", "input_tokens": 10},
                               self._openai_response()], requests=requests)
        self.assertEqual(["/v1/responses/input_tokens"], [r.url.path for r in requests])

    def test_native_envelope_is_rebound_to_packet_and_exact_lane(self):
        work = {
            **json.loads((SCHEMAS / "model-work-packet-v1.example.json").read_text(encoding="utf-8")),
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
            launch = _FakeLaunch(stdout=json.dumps(native).encode())
            with patch(LAUNCH_SEAM, launch):
                result = transport.send(json.dumps(work).encode(), context=self._context())

        self.assertEqual("claude", result["provider"])
        self.assertEqual("authenticated-session", result["run_reference"])
        self.assertEqual(work["authority_snapshot_digest"], result["authority_snapshot_digest"])
        self.assertEqual("NOT_RUN", result["acceptance"][0]["status"])
        self.assertEqual(1, len(launch.calls))
        self.assertFalse(launch.options["shell"])

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
        launch = _FakeLaunch(stdout=json.dumps(native).encode())
        with TemporaryDirectory() as directory:
            transport = NativeCommandPacketTransport(
                "antigravity",
                ("agy.exe", "--print"),
                cwd=directory,
                prompt_mode="stdin",
                payload_mode="antigravity-closed-directive",
            )
            with patch(LAUNCH_SEAM, launch):
                result = transport.send(
                    json.dumps(work).encode(), context=self._context()
                )

        sent = launch.inputs[0].decode("utf-8")
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
        self.assertFalse(launch.options["shell"])
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

    def _openai_response(self):
        return {
            "id": "resp_authenticated", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "done"}]}],
            "usage": {"input_tokens": 250, "output_tokens": 400, "total_tokens": 650},
        }

    def _openai_send(self, replies, *, context=None, requests=None, clock=None, meter=None):
        seen = requests if requests is not None else []

        def handle(request):
            seen.append(request)
            reply = replies[len(seen) - 1]
            if isinstance(reply, Exception):
                raise reply
            if isinstance(reply, httpx.Response):
                return reply
            return httpx.Response(200, json=reply)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        transport = OpenAIResponsesPacketTransport(api_key="test-secret", model="configured-model", response_meter=meter)
        with patch("director_orchestrator.primary_transports.httpx.AsyncClient", return_value=client), \
             patch("director_orchestrator.primary_transports.httpx.Client", side_effect=AssertionError("no unmocked synchronous I/O")):
            if clock is not None:
                with patch("director_orchestrator.primary_transports.monotonic", side_effect=clock):
                    return transport.send(json.dumps(self._work()).encode(), context=context or self._context())
            return transport.send(json.dumps(self._work()).encode(), context=context or self._context())

    def test_openai_counts_exact_input_then_makes_one_tool_free_bounded_generation(self):
        work = self._work()
        requests = []
        result = self._openai_send([{"object": "response.input_tokens", "input_tokens": 250}, self._openai_response()], requests=requests)
        self.assertEqual("openai", result["provider"])
        self.assertEqual("resp_authenticated", result["run_reference"])
        self.assertEqual(["/v1/responses/input_tokens", "/v1/responses"], [r.url.path for r in requests])
        count, body = [json.loads(r.content) for r in requests]
        self.assertEqual({key: value for key, value in body.items() if key not in {"store", "max_output_tokens"}}, count)
        self.assertEqual({"effort": "medium"}, count["reasoning"])
        self.assertEqual("configured-model", body["model"])
        # The model reads the compact view of the packet, never the inventory.
        self.assertEqual(compact_provider_view(work), json.loads(body["input"]))
        self.assertEqual(750, body["max_output_tokens"])
        self.assertEqual([], body["tools"])
        self.assertFalse(body["store"])
        self.assertTrue(all(r.headers["Authorization"] == "Bearer test-secret" for r in requests))
        self.assertNotIn("test-secret", json.dumps(body))
        self.assertEqual({"model": "configured-model", "input_tokens": 250, "output_tokens": 400,
                          "total_tokens": 650, "cost_usd": None}, result["usage"])

    def test_openai_missing_or_invalid_authority_never_counts_or_generates(self):
        for limit in (None, True, 0, -1, 1.5, "1000"):
            with self.subTest(limit=limit):
                requests = []
                with self.assertRaisesRegex(RuntimeError, "authoritative total token"):
                    self._openai_send([], context=replace(self._context(), total_token_limit=limit), requests=requests)
                self.assertEqual([], requests)

    def test_openai_malformed_or_exhausted_count_never_generates(self):
        counts = [{}, [], {"input_tokens": 250}, {"object": "other", "input_tokens": 250}]
        counts.extend({"object": "response.input_tokens", "input_tokens": value}
                      for value in (True, "250", -1, 250.0, 1000, 1001))
        for count in counts:
            with self.subTest(count=count):
                requests = []
                with self.assertRaisesRegex(RuntimeError, "count is malformed|exhausts"):
                    self._openai_send([count], requests=requests)
                self.assertEqual(1, len(requests))

    def _staged_clock(self, seen, elapsed):
        """A clock driven by request stages, not by implementation calls.

        The shared deadline depends on how much time the provider has taken,
        not on how many times the transport happens to read the clock, so the
        value is a function of the requests made so far. `elapsed[n]` is the
        time consumed once `n` requests have been issued; a finite
        `side_effect` list instead breaks - silently changing what is being
        tested - as soon as a legitimate extra cutoff check is added.
        """

        def clock():
            return 100.0 + elapsed[min(len(seen), len(elapsed) - 1)]

        return clock

    def test_openai_count_and_generation_share_the_remaining_deadline(self):
        requests = []
        # 12 seconds consumed by counting, then a generation that fits.
        self._openai_send([{"object": "response.input_tokens", "input_tokens": 250}, self._openai_response()], requests=requests,
                          clock=self._staged_clock(requests, [0.0, 12.0, 13.0]))
        self.assertEqual(30, requests[0].extensions["timeout"]["read"])
        self.assertEqual(18, requests[1].extensions["timeout"]["read"])
        # The whole window spent by the count: the generation is never sent.
        # Then a window spent by the generation itself: it was sent once, and
        # the exhausted deadline still refuses the attempt afterwards.
        for elapsed, call_count in (([0.0, 30.0], 1), ([0.0, 10.0, 30.0], 2)):
            requests = []
            with self.subTest(elapsed=elapsed), self.assertRaisesRegex(RuntimeError, "timeout is exhausted"):
                self._openai_send([{"object": "response.input_tokens", "input_tokens": 250}, self._openai_response()],
                                  requests=requests, clock=self._staged_clock(requests, elapsed))
            self.assertEqual(call_count, len(requests))

    def test_openai_never_retries_count_or_generation_failure(self):
        for replies in ([httpx.ReadTimeout("count failed")],
                        [{"object": "response.input_tokens", "input_tokens": 250}, httpx.ReadTimeout("generation ambiguous")]):
            requests = []
            with self.subTest(stage=len(replies)), self.assertRaisesRegex(RuntimeError, "outcome is ambiguous"):
                self._openai_send(replies, requests=requests)
            self.assertEqual(len(replies), len(requests))

    def test_openai_deadline_cancels_trickling_count_or_generation_and_closes_response(self):
        class Trickle(httpx.AsyncByteStream):
            chunks = 0
            closed = False

            async def __aiter__(self):
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    self.chunks += 1
                    yield b" "

            async def aclose(self):
                self.closed = True

        for stage in ("count", "generation"):
            stream = Trickle()
            replies = [] if stage == "count" else [{"object": "response.input_tokens", "input_tokens": 250}]
            replies.append(httpx.Response(200, stream=stream))
            requests = []
            started = time.monotonic()
            with self.subTest(stage=stage), self.assertRaisesRegex(RuntimeError, "timeout is exhausted"):
                self._openai_send(replies, context=replace(self._context(), timeout_seconds=1), requests=requests)
            self.assertLess(time.monotonic() - started, 3)
            self.assertLess(stream.chunks, 100)
            self.assertTrue(stream.closed)
            self.assertEqual(len(replies), len(requests))

    def test_openai_count_and_output_response_bodies_are_bounded_without_retry(self):
        count = {"object": "response.input_tokens", "input_tokens": 250}
        for replies in ([{**count, "padding": "x" * 4096}],
                        [count, {**self._openai_response(), "padding": "x" * 1_048_576}]):
            requests = []
            with self.subTest(stage=len(replies)), self.assertRaisesRegex(RuntimeError, "byte bound"):
                self._openai_send(replies, requests=requests)
            self.assertEqual(len(replies), len(requests))

    def test_openai_near_limit_response_is_capturable_or_never_metered(self):
        """Finding 6: the capture bundle bound and the native read bound agree.

        A native response that fits the read bound is always capturable once
        wrapped with its retained headers (the encoded boundary case); one
        that does not fit is refused before the meter settles, exactly like
        an over-bound body, never metered first and found uncapturable after.
        """
        from director_orchestrator import primary_transports as pt
        from director_orchestrator.packet_contracts import canonical_json_bytes
        from director_orchestrator.provider_validation import MAX_CAPTURE_BYTES
        self.assertEqual(MAX_CAPTURE_BYTES, pt._MAX_COMMAND_RESULT_BYTES)
        self.assertLess(pt._MAX_RESPONSES_NATIVE_BYTES, MAX_CAPTURE_BYTES)
        count = {"object": "response.input_tokens", "input_tokens": 250}
        headers = {"openai-organization": "org-" + "o" * 60, "openai-project": "proj-" + "p" * 60,
                   "x-request-id": "req-" + "r" * 60}

        def sized(total_bytes):
            response = self._openai_response()
            response["output"][0]["content"][0]["text"] = "t"
            base = len(json.dumps(response, separators=(",", ":")))
            response["output"][0]["content"][0]["text"] = "t" * max(1, total_bytes - base + 1)
            return response

        settled = []
        sent_instant = datetime(2026, 9, 21, 15, 13, tzinfo=timezone.utc)

        class Meter:
            """Exactly the meter protocol this transport calls, no wider.

            Every parameter is named, so a transport that stopped anchoring
            the send instant, stopped bounding the receipt, or stopped
            re-checking the tariff at the real send fails here instead of
            being absorbed by a catch-all signature.
            """

            enrollments = {}
            enrollment = SimpleNamespace(service_tier="default")

            def __init__(self):
                self.verified = 0

            def prepare(self, *, context, packet, api_url, model, api_key,
                        remaining_seconds=None):
                return {}

            def verify_current(self):
                self.verified += 1

            def clock(self):
                return sent_instant

            def settle(self, native, response_headers, *, sent_at, accounting_deadline):
                settled.append(SimpleNamespace(
                    size=len(canonical_json_bytes(native)), headers=response_headers,
                    sent_at=sent_at, accounting_deadline=accounting_deadline))

        captured = []
        context = replace(self._context(), execution_observer=lambda provider, raw, result: captured.append(raw))
        # Exactly at the native bound: read, metered once, and the observer
        # receives a bundle that the validation store will accept.
        at_bound = sized(pt._MAX_RESPONSES_NATIVE_BYTES)
        encoded = json.dumps(at_bound, separators=(",", ":")).encode()
        self.assertLessEqual(len(encoded), pt._MAX_RESPONSES_NATIVE_BYTES)
        self.assertGreater(len(encoded), pt._MAX_RESPONSES_NATIVE_BYTES - 64)
        meter = Meter()
        self._openai_send([count, httpx.Response(200, content=encoded, headers=headers)], context=context, meter=meter)
        self.assertEqual(1, len(captured))
        self.assertLessEqual(len(captured[0]), MAX_CAPTURE_BYTES)
        self.assertEqual(1, len(settled))
        # The tariff was re-checked at the real send, the receipt is anchored
        # to that instant, and it carries its own absolute accounting cutoff.
        self.assertEqual(1, meter.verified)
        self.assertEqual(sent_instant, settled[0].sent_at)
        self.assertIsInstance(settled[0].accounting_deadline, float)
        self.assertEqual({"openai-organization", "openai-project", "x-request-id"},
                          set(settled[0].headers))
        # One byte over the native bound: refused mid-stream, nothing settled.
        over = json.dumps(sized(pt._MAX_RESPONSES_NATIVE_BYTES + 1), separators=(",", ":")).encode()
        self.assertGreater(len(over), pt._MAX_RESPONSES_NATIVE_BYTES)
        settled.clear(); captured.clear()
        with self.assertRaisesRegex(RuntimeError, "exceeds its byte bound"):
            self._openai_send([count, httpx.Response(200, content=over, headers=headers)], context=context, meter=Meter())
        self.assertEqual([], settled)
        self.assertEqual([], captured)
        # An oversized retained header is refused before settle as well.
        settled.clear()
        big = dict(headers, **{"x-request-id": "r" * (pt._MAX_RESPONSES_HEADER_VALUE_BYTES + 1)})
        with self.assertRaisesRegex(RuntimeError, "header exceeds its byte bound"):
            self._openai_send([count, httpx.Response(200, json=self._openai_response(), headers=big)], context=context, meter=Meter())
        self.assertEqual([], settled)

    def test_openai_rejects_noncompleted_and_malformed_native_results(self):
        valid = self._openai_response()
        invalid = [[], None, {}] + [{**valid, "status": status} for status in
                   (None, "incomplete", "failed", "cancelled", "queued", "in_progress", True)]
        invalid += [{**valid, "error": {"message": "failed"}},
                    {**valid, "incomplete_details": {"reason": "max_output_tokens"}},
                    {**valid, "id": ""}, {**valid, "output": None},
                    {**valid, "output": []}, {**valid, "output": ["done"]}]
        for field, value in (("status", "incomplete"), ("role", "user"),
                             ("type", "function_call"), ("content", None)):
            response = deepcopy(valid)
            response["output"][0][field] = value
            invalid.append(response)
        response = deepcopy(valid)
        response["output"][0]["content"][0]["type"] = "refusal"
        invalid.append(response)
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                self._openai_send([{"object": "response.input_tokens", "input_tokens": 250}, response])

    def test_openai_rejects_unverifiable_or_over_limit_usage(self):
        valid = self._openai_response()
        cases = [None, {}, {**valid["usage"], "input_tokens": 251},
                 {**valid["usage"], "total_tokens": 1},
                 {"input_tokens": 250, "output_tokens": 751, "total_tokens": 1001}]
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            for value in (True, "250", -1, 250.0):
                cases.append({**valid["usage"], key: value})
        for usage in cases:
            with self.subTest(usage=usage), self.assertRaisesRegex(RuntimeError, "response usage"):
                self._openai_send([{"object": "response.input_tokens", "input_tokens": 250}, {**valid, "usage": usage}])

    def test_openai_accepts_completed_reasoning_and_visible_output_at_total_limit(self):
        response = self._openai_response()
        response["output"].insert(0, {"type": "reasoning", "summary": []})
        response["usage"] = {"input_tokens": 250, "output_tokens": 750, "total_tokens": 1000,
                             "output_tokens_details": {"reasoning_tokens": 350}}
        result = self._openai_send([{"object": "response.input_tokens", "input_tokens": 250}, response])
        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual(1000, result["usage"]["total_tokens"])


    def test_public_search_sends_only_approved_query_once_without_shell(self):
        work = self._work(objective="current public documentation")
        work["repository"] = "owner/private-repo"
        native = {"hits": [{"url": "https://example.test", "title": "Evidence"}], "total": 1}
        launch = _FakeLaunch(stdout=json.dumps(native).encode())
        with TemporaryDirectory() as directory:
            transport = PublicSearchCommandTransport(
                "perplexity",
                ("wsl.exe", "--distribution", "Ubuntu", "--exec", "/safe/pplx"),
                cwd=directory,
                maximum_results=3,
            )
            with patch(LAUNCH_SEAM, launch):
                result = transport.send(json.dumps(work).encode(), context=self._context())

        self.assertEqual("perplexity", result["provider"])
        self.assertEqual(1, len(launch.calls))
        self.assertFalse(launch.options["shell"])
        self.assertEqual([None], launch.inputs, "the query travels as argv, never on stdin")
        argv = launch.argv
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
            launch = _FakeLaunch()
            work = self._work()
            work["approval_requirements"] = []
            with patch(LAUNCH_SEAM, launch):
                with self.assertRaisesRegex(RuntimeError, "approval"):
                    transport.send(json.dumps(work).encode(), context=self._context())
            work = self._work()
            work["allowed_external_effects"] = ["WRITE_REPOSITORY"]
            with patch(LAUNCH_SEAM, launch):
                with self.assertRaisesRegex(RuntimeError, "external effects"):
                    transport.send(json.dumps(work).encode(), context=self._context())
            self.assertEqual([], launch.calls)

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
        launch = _FakeLaunch(stdout=b'{"hits":[],"total":0}')
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, secrets, clear=True
        ):
            transport = PublicSearchCommandTransport(
                "firecrawl", ("firecrawl.exe",), cwd=directory
            )
            with patch(LAUNCH_SEAM, launch):
                transport.send(json.dumps(self._work()).encode(), context=self._context())

        child = launch.options["env"]
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
        bounded_launch = _FakeLaunch(stdout=b'{"provider":"claude"}')
        native_launch = _FakeLaunch(stdout=b'{"session_id":"run","result":"done"}')
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, inherited, clear=True
        ):
            bounded = BoundedJsonCommandTransport(
                "claude", ("adapter.exe",), cwd=directory
            )
            native = NativeCommandPacketTransport(
                "claude", ("claude.exe",), cwd=directory
            )
            with patch(LAUNCH_SEAM, bounded_launch):
                bounded.send(b"{}", context=self._context())
            with patch(LAUNCH_SEAM, native_launch):
                native.send(json.dumps(self._work()).encode(), context=self._context())

        for _argv, options in bounded_launch.calls + native_launch.calls:
            child = options["env"]
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
            "director_orchestrator.primary_transports.sys.platform", "linux"
        ), patch(
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

        # Replacing the native Antigravity lane must not reopen the packet: the
        # bridge lane carries the same closed-directive contract. Claude and
        # Grok post the compact provider view; github-copilot stays unredacted.
        antigravity = transports["antigravity"]
        self.assertIs(antigravity.outbound_payload_builder, antigravity_closed_payload)
        self.assertIsNotNone(antigravity.result_normalizer)
        for provider in ("claude", "grok", "github-copilot"):
            with self.subTest(provider=provider):
                if provider in {"claude", "grok"}:
                    # The compact view, validated against its v1 schema.
                    self.assertIs(
                        transports[provider].outbound_payload_builder,
                        validated_compact_view,
                    )
                    self.assertIsNotNone(transports[provider].result_normalizer)
                else:
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


CLOCK_SEAM = "director_orchestrator.primary_transports.monotonic"


class CommandBoundaryDeadlineTests(unittest.TestCase):
    """The shared command boundary under a carried deadline, across the discovered routes.

    The transports are the ones `discover_local_primary_command_transports`
    itself selects for the existing prompt styles - Claude stdin, Cursor
    argument, Grok prompt file, Antigravity closed directive, Copilot
    argument - and both public-search argv routes, plus the generic JSON
    command, which shares the boundary without any demonstrated automatic
    enrollment. The launch is synthetic; the cutoff arithmetic, the
    preparation charged to it, the refusal before launch, the wait handed to
    the child and the real `stop_contained_process` applied afterwards are
    not. One case launches a real local child.
    """

    _context = GraphNativeProviderFactoryTests._context
    _work = GraphNativeProviderFactoryTests._work
    _antigravity_work = GraphNativeProviderFactoryTests._antigravity_work

    def _discovered(self, directory):
        """Every command route discovery would select on a host where each CLI is present."""

        def present(candidates):
            return "cursor-agent" if "cursor-agent" in candidates else candidates[0]

        with patch("director_orchestrator.primary_transports.sys.platform", "linux"), \
             patch("director_orchestrator.primary_transports._which", side_effect=present):
            transports, _observed = discover_local_primary_command_transports(
                cwd=directory, authenticated={provider: True for provider in PRIMARY_PROVIDER_LANES})
        self.assertEqual({"claude", "cursor", "grok", "antigravity", "github-copilot",
                          "perplexity", "firecrawl"}, set(transports))
        self.assertEqual(
            {"claude": "stdin", "cursor": "argument", "grok": "grok-prompt-file",
             "antigravity": "stdin", "github-copilot": "argument"},
            {name: transports[name].prompt_mode
             for name in ("claude", "cursor", "grok", "antigravity", "github-copilot")})
        self.assertEqual("antigravity-closed-directive", transports["antigravity"].payload_mode)
        self.assertIsInstance(transports["perplexity"], PublicSearchCommandTransport)
        self.assertIsInstance(transports["firecrawl"], PublicSearchCommandTransport)
        routes = {}
        for provider, transport in transports.items():
            work = self._antigravity_work() if provider == "antigravity" else self._work()
            stdout = b'{"hits":[],"total":0}' if provider in {"perplexity", "firecrawl"} else NATIVE_ENVELOPE
            routes[provider] = (transport, json.dumps(work).encode(), stdout)
        routes["generic-json"] = (
            BoundedJsonCommandTransport("claude", ("packet-adapter.exe", "--stdio"), cwd=directory),
            b'{"schema_version":"1.0"}', b'{"provider":"claude"}')
        return routes

    STDIN_ROUTES = frozenset({"claude", "antigravity", "generic-json"})

    def test_carried_deadline_shorter_than_the_relative_timeout_bounds_every_route(self):
        """Thirty seconds of relative timeout, a carried bound 0.2 seconds away.

        The spawn itself spends 0.05 of that on the synthetic clock, so the
        wait the child is handed is 0.15: the carried remainder at the moment
        of launch, never the thirty. Stdin routes deliver their prepared
        bytes through the file the child reads at launch and nothing is
        resent; argument, prompt-file and argv routes get `DEVNULL`. After a
        normal exit the tree is ended and the guard released.
        """
        with TemporaryDirectory() as directory:
            for route, (transport, packet, stdout) in self._discovered(directory).items():
                with self.subTest(route=route):
                    clock = [1000.0]
                    launch = _FakeLaunch(stdout=stdout, clock=clock, spawn_seconds=0.05)
                    context = replace(self._context(), deadline=1000.2)
                    with patch(CLOCK_SEAM, lambda: clock[0]), patch(LAUNCH_SEAM, launch):
                        transport.send(packet, context=context)
                    self.assertEqual(1, len(launch.calls))
                    self.assertFalse(launch.options["shell"])
                    self.assertAlmostEqual(0.15, launch.waits[0], places=6)
                    self.assertLess(launch.waits[0], context.timeout_seconds)
                    self.assertEqual([None], launch.resent)
                    if route == "antigravity":
                        # The closed directive, never the private packet.
                        self.assertIn(ANTIGRAVITY_CLOSED_PAYLOAD_SCHEMA.encode(), launch.inputs[0])
                        self.assertNotIn(packet, launch.inputs[0])
                    elif route in self.STDIN_ROUTES:
                        self.assertIsInstance(launch.inputs[0], bytes)
                        # A native lane ends with the exact compact view of
                        # the packet; the generic route forwards the packet.
                        expected = (
                            packet if route == "generic-json"
                            else canonical_json_bytes(compact_provider_view(json.loads(packet)))
                        )
                        self.assertTrue(launch.inputs[0].endswith(expected),
                                        "the prepared bytes end with the exact provider document")
                    else:
                        self.assertEqual([None], launch.inputs)
                    self.assertEqual(["terminate", ("wait", 5.0), "close"], launch.stopped)

    def test_a_launch_that_consumes_the_cutoff_never_waits_and_is_ambiguous(self):
        """The spawn itself outlives the bound: no wait is entered, the tree is ended."""

        with TemporaryDirectory() as directory:
            for route, (transport, packet, stdout) in self._discovered(directory).items():
                with self.subTest(route=route):
                    clock = [1000.0]
                    launch = _FakeLaunch(stdout=stdout, clock=clock, spawn_seconds=0.3)
                    context = replace(self._context(), deadline=1000.2)
                    with patch(CLOCK_SEAM, lambda: clock[0]), patch(LAUNCH_SEAM, launch):
                        with self.assertRaisesRegex(RuntimeError, "launch consumed the cutoff"):
                            transport.send(packet, context=context)
                    self.assertEqual(1, len(launch.calls), "launched once, never again")
                    self.assertEqual([], launch.waits, "no work wait is entered")
                    self.assertEqual(["terminate", "kill", ("wait", 5.0), "close"], launch.stopped)

    def test_a_result_returned_after_the_cutoff_is_not_a_successful_attempt(self):
        """The wait returns a valid envelope, but only after the cutoff has passed."""

        with TemporaryDirectory() as directory:
            for route, (transport, packet, stdout) in self._discovered(directory).items():
                with self.subTest(route=route):
                    clock = [1000.0]
                    launch = _FakeLaunch(stdout=stdout, clock=clock,
                                         before_return=lambda: clock.__setitem__(0, clock[0] + 1.0))
                    context = replace(self._context(), deadline=1000.2)
                    with patch(CLOCK_SEAM, lambda: clock[0]), patch(LAUNCH_SEAM, launch):
                        with self.assertRaisesRegex(RuntimeError, "returned after the cutoff"):
                            transport.send(packet, context=context)
                    self.assertEqual(1, len(launch.calls))
                    self.assertAlmostEqual(0.2, launch.waits[0], places=6)
                    self.assertEqual(["terminate", ("wait", 5.0), "close"], launch.stopped,
                                     "the exited child's tree is still ended and the guard released")

    def test_a_cutoff_that_is_not_a_finite_instant_launches_nothing(self):
        from director_orchestrator.primary_transports import _run_bounded_command
        with TemporaryDirectory() as directory:
            for cutoff in (float("nan"), float("inf"), float("-inf"), True, None):
                with self.subTest(cutoff=cutoff):
                    launch = _FakeLaunch()
                    with patch(LAUNCH_SEAM, launch):
                        with self.assertRaisesRegex(RuntimeError, "cutoff is invalid"):
                            _run_bounded_command(("never.exe",), cwd=Path(directory), environment={},
                                                 input_bytes=b"{}", cutoff=cutoff, what="native transport")
                    self.assertEqual([], launch.calls)

    ECHO_NATIVE = (
        "import hashlib,json,sys; data=sys.stdin.buffer.read(); "
        "print(json.dumps({'session_id':'round-trip','result':json.dumps({"
        "'outcome':'sha256:'+hashlib.sha256(data).hexdigest()+':'+str(len(data)),"
        "'status':'SUCCEEDED'})}))"
    )
    ECHO_JSON = (
        "import hashlib,json,sys; data=sys.stdin.buffer.read(); "
        "print(json.dumps({'provider':'claude','sha256':hashlib.sha256(data).hexdigest(),"
        "'length':len(data)}))"
    )

    def test_small_inputs_round_trip_byte_for_byte_through_the_real_helper(self):
        """Ordinary small valid inputs, real local children, exact bytes and EOF.

        A native stdin route and the generic JSON route each launch a real
        Python child that reads its stdin through EOF and reports the digest
        and length of what it received. The native prompt is compared
        against the exact bytes a recording launch sees prepared for the
        same packet, so the header is never restated here; the generic
        packet and an empty packet are compared directly. No provider binary
        is involved.
        """
        import hashlib
        work = json.dumps(self._work()).encode()
        with TemporaryDirectory() as directory:
            native = NativeCommandPacketTransport(
                "claude", (sys.executable, "-c", self.ECHO_NATIVE), cwd=directory, prompt_mode="stdin")
            recording = _FakeLaunch(stdout=NATIVE_ENVELOPE)
            with patch(LAUNCH_SEAM, recording):
                native.send(work, context=self._context())
            [prepared] = recording.inputs
            self.assertTrue(prepared.endswith(
                canonical_json_bytes(compact_provider_view(json.loads(work)))))
            from jsonschema import Draft202012Validator
            contract = json.loads(prepared.split(b"\n", 2)[1])
            validator = Draft202012Validator(contract)
            validator.validate({"outcome": "EDGE_NATIVE_CANARY_OK", "status": "SUCCEEDED",
                "checks": [{"command": "packet-only", "result": "No tools used.", "exit_code": None}],
                "acceptance": [{"check": "packet-only", "status": "PASS", "evidence": "No tools used."}]})
            for invalid in ({"outcome": "done", "status": "completed"},
                            {"outcome": "done", "status": "CANCELLED"},
                            {"outcome": "done", "status": "REVISION_REQUIRED"},
                            {"outcome": "done", "status": "SUCCEEDED",
                             "checks": [{"name": "packet-only", "passed": True}]}):
                self.assertFalse(validator.is_valid(invalid))
            result = native.send(work, context=self._context())
            self.assertEqual(
                "sha256:" + hashlib.sha256(prepared).hexdigest() + ":" + str(len(prepared)),
                result["outcome"], "the child received exactly the prepared bytes, then EOF")
            self.assertEqual("round-trip", result["run_reference"])

            generic = BoundedJsonCommandTransport(
                "claude", (sys.executable, "-c", self.ECHO_JSON), cwd=directory)
            for packet in (work, b""):
                with self.subTest(length=len(packet)):
                    echoed = generic.send(packet, context=self._context())
                    self.assertEqual(
                        {"provider": "claude", "sha256": hashlib.sha256(packet).hexdigest(),
                         "length": len(packet)}, echoed)

    def test_prompt_file_preparation_is_charged_and_can_exhaust_the_bound(self):
        """The Grok prompt file is real preparation between entry and launch.

        Writing it costs 0.1 on the synthetic clock: the child gets the 0.1
        that remains. When writing it costs 0.25 instead, the bound is spent
        before the launch: nothing is launched, and the prompt file is still
        removed.
        """
        real_named = tempfile.NamedTemporaryFile
        with TemporaryDirectory() as directory:
            transport, packet, stdout = self._discovered(directory)["grok"]
            for cost, launched, remaining in ((0.1, 1, 0.1), (0.25, 0, None)):
                with self.subTest(preparation_seconds=cost):
                    clock, written = [1000.0], []

                    def slow_prompt_file(*args, **kwargs):
                        handle = real_named(*args, **kwargs)
                        written.append(Path(handle.name))
                        clock[0] += cost
                        return handle

                    launch = _FakeLaunch(stdout=stdout, clock=clock)
                    context = replace(self._context(), deadline=1000.2)
                    with patch(CLOCK_SEAM, lambda: clock[0]), patch(LAUNCH_SEAM, launch), \
                         patch("director_orchestrator.primary_transports.tempfile",
                               SimpleNamespace(NamedTemporaryFile=slow_prompt_file)):
                        if launched:
                            transport.send(packet, context=context)
                        else:
                            with self.assertRaisesRegex(RuntimeError, "exhausted before launch"):
                                transport.send(packet, context=context)
                    self.assertEqual(launched, len(launch.calls))
                    if launched:
                        self.assertAlmostEqual(remaining, launch.waits[0], places=6)
                        self.assertIn("--prompt-file", launch.argv)
                    self.assertEqual(1, len(written))
                    self.assertFalse(written[0].exists(), "the prompt file never outlives the call")

    def test_expired_or_invalid_carried_bound_refuses_every_route_before_launch(self):
        for deadline, message in ((995.0, "timeout is exhausted"),
                                  (float("nan"), "carried deadline is invalid"),
                                  (float("inf"), "carried deadline is invalid")):
            with TemporaryDirectory() as directory:
                for route, (transport, packet, _stdout) in self._discovered(directory).items():
                    with self.subTest(route=route, deadline=deadline):
                        launch = _FakeLaunch()
                        context = replace(self._context(), deadline=deadline)
                        with patch(CLOCK_SEAM, lambda: 1000.0), patch(LAUNCH_SEAM, launch):
                            with self.assertRaisesRegex(RuntimeError, message):
                                transport.send(packet, context=context)
                        self.assertEqual([], launch.calls, "nothing is launched")

    def test_a_cutoff_reached_after_launch_is_one_ambiguous_attempt_with_the_tree_ended(self):
        """The child outlives the bound: ambiguous, ended, never relaunched."""

        with TemporaryDirectory() as directory:
            for route, (transport, packet, _stdout) in self._discovered(directory).items():
                with self.subTest(route=route):
                    launch = _FakeLaunch(expire=True)
                    context = replace(self._context(), deadline=1000.2)
                    with patch(CLOCK_SEAM, lambda: 1000.0), patch(LAUNCH_SEAM, launch):
                        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                            transport.send(packet, context=context)
                    self.assertEqual(1, len(launch.calls), "one attempt, not repeated")
                    self.assertEqual(["terminate", "kill", ("wait", 5.0), "close"], launch.stopped,
                                     "the owned tree is ended, the child reaped, the guard released")

    def test_a_real_child_that_outlives_the_bound_is_ended_with_its_descendant(self):
        """One real harmless local process through the shared runner.

        The launched parent spawns a descendant and both keep running past
        the half-second carried bound. The runner reports the attempt
        ambiguous, and afterwards the parent is reaped, its pipes and the
        guard are closed, and the descendant - which would have written its
        marker at 1.5 seconds - is gone with it. On Windows the descendant is
        detached into its own process group, reachable only through the Job;
        on POSIX it stays in the parent's session, which the group signal
        reaches. No provider binary is involved.
        """
        from director_orchestrator import process_isolation
        with TemporaryDirectory() as directory:
            marker = Path(directory) / "descendant.txt"
            descendant = (
                "import pathlib,sys,time; time.sleep(1.5); "
                "pathlib.Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
            )
            detach = (
                "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS"
                if os.name == "nt" else "close_fds=True"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]], " + detach + "); "
                "time.sleep(60)"
            )
            # The prompt is appended as the final argument and ignored by the script.
            transport = NativeCommandPacketTransport(
                "claude", (sys.executable, "-c", parent, str(marker), descendant),
                cwd=directory, prompt_mode="argument")
            launched = []

            def observing(argv, **options):
                process, guard = process_isolation.launch_contained_process(argv, **options)
                launched.append((process, guard))
                return process, guard

            context = replace(self._context(), deadline=time.monotonic() + 0.5)
            started = time.monotonic()
            with patch(LAUNCH_SEAM, observing), self.assertRaisesRegex(RuntimeError, "ambiguous"):
                transport.send(json.dumps(self._work()).encode(), context=context)
            self.assertLess(time.monotonic() - started, 6.0, "cleanup is bounded by its own grace period")
            [(process, guard)] = launched
            self.assertIsNotNone(process.poll(), "the immediate child is reaped")
            self.assertTrue(all(stream is None or stream.closed
                                for stream in (process.stdin, process.stdout, process.stderr)))
            self.assertFalse(guard.active, "the guard is released")
            time.sleep(2.0)
            self.assertFalse(marker.exists(), "the descendant did not outlive the tree")


SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"


def _inventory_work_packet(entries: int = 1_500) -> dict:
    """The schema example packet with a realistic repository inventory."""

    work = json.loads((SCHEMAS / "model-work-packet-v1.example.json").read_text(encoding="utf-8"))
    commit = work["authority_snapshot"][0]["commit_sha"]
    blob = work["authority_snapshot"][0]["blob_sha"]
    work["authority_snapshot"] = [
        {"path": f"src/module_{index:04d}.py", "commit_sha": commit, "blob_sha": blob}
        for index in range(entries)
    ]
    work["authority_snapshot_digest"] = document_digest(
        {"authority_snapshot": work["authority_snapshot"]}
    )
    return work


class CompactProviderViewTransportTests(unittest.TestCase):
    _context = GraphNativeProviderFactoryTests._context

    def test_native_work_packet_mode_sends_only_the_compact_view(self):
        work = _inventory_work_packet()
        sent = []

        def run(argv, **kwargs):
            sent.append(kwargs["input_bytes"])
            return subprocess.CompletedProcess(argv, 0, stdout=NATIVE_ENVELOPE, stderr=b"")

        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._run_bounded_command", side_effect=run
        ):
            transport = NativeCommandPacketTransport("claude", ("claude.exe", "--print"), cwd=directory)
            transport.send(canonical_json_bytes(work), context=self._context())

        [prompt] = sent
        self.assertNotIn(b'"authority_snapshot":', prompt)
        self.assertIn(b'"authority_snapshot_digest":', prompt)
        self.assertTrue(prompt.endswith(canonical_json_bytes(compact_provider_view(work))))
        self.assertLess(len(prompt), len(canonical_json_bytes(work)) // 10)

    def test_compact_native_result_rebinds_to_the_full_packet(self):
        work = _inventory_work_packet()
        context = replace(
            self._context(),
            task_id=work["task_id"],
            assignment_id=work["assignment_id"],
            idempotency_key=work["idempotency_key"],
            work_packet_digest=document_digest(work),
        )
        with TemporaryDirectory() as directory:
            adapter = PacketBoundaryAdapter(
                "claude",
                NativeCommandPacketTransport("claude", ("claude.exe", "--print"), cwd=directory),
            )
            launch = _FakeLaunch(stdout=NATIVE_ENVELOPE)
            with patch(LAUNCH_SEAM, launch):
                result = adapter.dispatch(work, context=context)

        self.assertNotIn(b'"authority_snapshot":', launch.inputs[0])
        replay = build_replay_run(work, result)
        self.assertEqual(document_digest(work), replay["work_packet_digest"])
        self.assertEqual(work["authority_snapshot_digest"], result["authority_snapshot_digest"])
        self.assertEqual(work["task_id"], result["task_id"])
        self.assertEqual("claude", result["provider"])


class AcceptanceTransportTests(unittest.TestCase):
    """Every HTTP request is intercepted by MockTransport; no sockets."""

    def setUp(self):
        from director_orchestrator.primary_transports import (
            AcceptanceRequestPlan, ACCEPTANCE_RESPONSE_SCHEMA)
        from director_orchestrator.langgraph_primary import SqliteAcceptanceJournal, _canonical_digest
        self.directory = TemporaryDirectory(prefix="acceptance-transport-")
        self.addCleanup(self.directory.cleanup)
        self.journal = SqliteAcceptanceJournal(Path(self.directory.name) / "journal.sqlite3")
        self.plan = AcceptanceRequestPlan(model="fixture-model-immutable", authority_digest="sha256:" + "a" * 64,
            tariff_digest="sha256:" + "b" * 64, schema_digest=_canonical_digest(ACCEPTANCE_RESPONSE_SCHEMA),
            valid_until=int(time.time()) + 300, max_input=50000, max_output=8000,
            max_cache_write=50000, input_rate="2", output_rate="10", cache_write_rate="2.5",
            fee_bound="0", scope="fixture-envelope")
        self.context = SimpleNamespace(task_id="task", candidate_sha="a" * 40, revision_count=0)
        self.evidence = {"verified": "evidence"}
        self.key = _canonical_digest({"key": "acceptance"})
        _, self.row = self.journal.acceptance_claim(self.key, {"evidence": self.evidence}, **self.plan.reservation())
        self.requests = []
        self.rechecks = []

    def _completed(self, **updates):
        from director_orchestrator.langgraph_primary import _canonical_digest
        output = {"task_id": "task", "candidate_sha": "a" * 40, "revision": 0,
                  "evidence_digest": _canonical_digest(self.evidence), "decision": "accept"}
        response = {"id": "resp-fixture", "model": self.plan.model, "status": "completed",
            "usage": {"input_tokens": 500, "output_tokens": 200},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}]}
        response.update(updates)
        return response

    def _execute(self, responses=None, *, failure=None, recheck=None, **kwargs):
        from director_orchestrator.primary_transports import OpenAIAcceptanceTransport
        queue = list(responses or [self._completed()])
        def respond(request):
            self.requests.append(request)
            if failure is not None:
                if callable(failure):
                    override = failure(request)
                    if override is not None:
                        return override
                else:
                    raise failure
            if request.url.path.endswith("input_tokens"):
                return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 500},
                    headers={"x-request-id": "fixture-count"})
            return httpx.Response(200, json=queue.pop(0), headers={"x-request-id": "fixture-response"})
        transport = OpenAIAcceptanceTransport("fake-key", client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(respond), trust_env=False, follow_redirects=False))
        return transport.execute(plan=self.plan, evidence=self.evidence, context=self.context,
            journal=self.journal, key=self.key, owner=self.row["owner"],
            recheck=recheck or (lambda: self.rechecks.append(True)), **kwargs)

    def test_client_cleanup_failure_keeps_paid_response_unresolved(self):
        from contextlib import contextmanager
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved
        from director_orchestrator.primary_transports import OpenAIAcceptanceTransport
        error = httpx.CloseError("client cleanup uncertain")
        def respond(request):
            self.requests.append(request)
            if request.url.path.endswith("input_tokens"):
                return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 500},
                                      headers={"x-request-id": "fixture-count"})
            return httpx.Response(200, json=self._completed(), headers={"x-request-id": "fixture-create"})
        @contextmanager
        def client():
            with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as value:
                yield value
            raise error
        transport = OpenAIAcceptanceTransport("fake-key", client_factory=client)
        with self.assertRaises(AcceptanceUnresolved) as raised:
            transport.execute(plan=self.plan, evidence=self.evidence, context=self.context,
                journal=self.journal, key=self.key, owner=self.row["owner"], recheck=lambda: None)
        self.assertIs(raised.exception.__cause__, error)
        held = self.journal.acceptance_read(self.key)
        self.assertEqual((held["response_id"], held["used_requests"], held["reserved_cents"]),
                         ("resp-fixture", 2, 155))
        self.assertEqual(len(self.requests), 2)
        with self.assertRaises(AcceptanceUnresolved):
            self.journal.acceptance_http(self.key, self.row["owner"], "create")

    def test_malformed_create_envelope_is_unresolved_with_cause(self):
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved
        def malformed(request):
            if not request.url.path.endswith("input_tokens"):
                return httpx.Response(200, content=b'{"id":', headers={"x-request-id": "fixture"})
        with self.assertRaises(AcceptanceUnresolved) as raised:
            self._execute(failure=malformed)
        self.assertIsInstance(raised.exception.__cause__, json.JSONDecodeError)
        row = self.journal.acceptance_read(self.key)
        self.assertEqual((row["used_requests"], row["reserved_cents"]), (2, 155))
        with self.assertRaises(AcceptanceUnresolved):
            self.journal.acceptance_http(self.key, self.row["owner"], "create")
        self.assertEqual(len(self.requests), 2)

    def test_malformed_retrieve_keeps_durable_id(self):
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved
        def malformed(request):
            if request.method == "GET":
                return httpx.Response(200, content=b'{', headers={"x-request-id": "fixture"})
        with self.assertRaises(AcceptanceUnresolved) as raised:
            self._execute([{"id": "resp-fixture", "status": "queued"}], failure=malformed)
        self.assertIsInstance(raised.exception.__cause__, json.JSONDecodeError)
        row = self.journal.acceptance_read(self.key)
        self.assertEqual((row["response_id"], row["used_requests"], row["reserved_cents"]),
                         ("resp-fixture", 3, 155))
        self.assertEqual(len(self.requests), 3)

    def test_response_id_database_exit_failure_requires_reconciliation(self):
        import sqlite3
        import psycopg
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved, _canonical_digest
        original = self.journal.acceptance_response_id
        errors = (sqlite3.OperationalError("commit acknowledgement lost"),
                  psycopg.OperationalError("connection lost"),
                  psycopg.errors.TransactionResolutionUnknown("commit unknown"))
        for index, error in enumerate(errors):
            for committed in (False, True):
                with self.subTest(error=type(error), committed=committed):
                    self.key = _canonical_digest({"error": index, "committed": committed})
                    _, self.row = self.journal.acceptance_claim(self.key, {}, **replace(
                        self.plan, scope=f"db-{index}-{committed}").reservation())
                    self.requests = []
                    def uncertain(*args):
                        if committed:
                            original(*args)
                        raise error
                    with patch.object(self.journal, "acceptance_response_id", side_effect=uncertain):
                        with self.assertRaises(AcceptanceUnresolved) as raised:
                            self._execute()
                    self.assertIs(raised.exception.__cause__, error)
                    row = self.journal.acceptance_read(self.key)
                    self.assertEqual(row["response_id"], "resp-fixture" if committed else None)
                    self.assertEqual((row["reserved_cents"], row["used_requests"]), (155, 2))
                    with self.assertRaises(AcceptanceUnresolved):
                        self.journal.acceptance_http(self.key, self.row["owner"], "create")
                    self.assertEqual(len(self.requests), 2)

    def test_count_create_and_bounded_retrieve_record_id_and_no_hidden_retry(self):
        result = self._execute([{"id": "resp-fixture", "status": "queued"}, self._completed()])
        self.assertEqual(result.decision.value, "accept")
        self.assertEqual([request.method for request in self.requests], ["POST", "POST", "GET"])
        row = self.journal.acceptance_read(self.key)
        self.assertEqual((row["state"], row["response_id"], row["used_requests"]),
                         ("RESPONSE_ID_RECORDED", "resp-fixture", 3))
        self.assertEqual(row["reserved_cents"], 155)
        body = json.loads(self.requests[1].content)
        self.assertTrue(body["store"])
        self.assertTrue(body["background"])
        self.assertEqual(body["max_output_tokens"], 8000)
        self.assertGreaterEqual(len(self.rechecks), 4)

    def test_post_charge_checks_cover_every_request_kind(self):
        from unittest.mock import patch
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved, _canonical_digest
        original_http = self.journal.acceptance_http
        for kind in ("input_tokens", "create", "retrieve", "cancel"):
            for failure in ("deadline", "valid_until", "revoked"):
                with self.subTest(kind=kind, failure=failure):
                    self.key = _canonical_digest({"kind": kind, "failure": failure})
                    _, self.row = self.journal.acceptance_claim(self.key, {}, **replace(
                        self.plan, scope=kind + failure).reservation())
                    if kind in {"retrieve", "cancel"}:
                        original_http(self.key, self.row["owner"], "create")
                        self.journal.acceptance_response_id(self.key, self.row["owner"], "resp-fixture")
                    clock = [0.0]
                    wall = [self.plan.valid_until - 100]
                    charged = []
                    def charge(key, owner, request_kind):
                        original_http(key, owner, request_kind)
                        charged.append(request_kind)
                        if request_kind == kind:
                            if failure == "deadline":
                                clock[0] = self.plan.timeout_seconds + 1
                            elif failure == "valid_until":
                                wall[0] = self.plan.valid_until + 1
                    def recheck():
                        if charged and charged[-1] == kind and failure == "revoked":
                            raise ValueError("authority revoked")
                    self.requests = []
                    options = ({"response_id": "resp-fixture", "cancel": kind == "cancel"}
                               if kind in {"retrieve", "cancel"} else {})
                    with patch.object(self.journal, "acceptance_http", side_effect=charge), \
                         patch("director_orchestrator.primary_transports.monotonic", side_effect=lambda: clock[0]), \
                         patch("time.time", side_effect=lambda: wall[0]):
                        expected = AcceptanceUnresolved if failure == "deadline" else ValueError
                        with self.assertRaises(expected):
                            self._execute(recheck=recheck, **options)
                    expected_http = 1 if kind == "create" else 0
                    self.assertEqual(len(self.requests), expected_http)
                    row = self.journal.acceptance_read(self.key)
                    self.assertEqual(row["used_requests"], 2 if kind != "input_tokens" else 1)
                    self.assertEqual(row["reserved_cents"], 155)
                    won, _ = self.journal.acceptance_claim(self.key, {}, **replace(
                        self.plan, scope=kind + failure).reservation())
                    self.assertFalse(won)
                    with self.journal.acceptance_transaction() as sql:
                        creates = sql.execute("SELECT COUNT(*) FROM acceptance_http WHERE operation_key=? AND kind='create'", (self.key,)).fetchone()[0]
                    self.assertEqual(creates, int(kind != "input_tokens"))

    def test_post_charge_timeout_uses_fresh_remaining_budget(self):
        from unittest.mock import patch
        clock = [0.0]
        original_http = self.journal.acceptance_http
        def charge(*args):
            original_http(*args)
            clock[0] += 7
        with patch.object(self.journal, "acceptance_http", side_effect=charge), \
             patch("director_orchestrator.primary_transports.monotonic", side_effect=lambda: clock[0]):
            self._execute()
        self.assertEqual([request.extensions["timeout"]["read"] for request in self.requests], [53, 46])

    def test_disconnect_charges_one_request_and_does_not_retry(self):
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved
        with self.assertRaises(AcceptanceUnresolved):
            self._execute(failure=httpx.ReadTimeout("fixture disconnected"))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.journal.acceptance_read(self.key)["reserved_cents"], 155)

    def test_terminal_failed_cancelled_incomplete_and_missing_id_never_recreate(self):
        # Each status uses a separately admitted fixture; no case reuses the
        # original operation to fake a new authorized create.
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved, _canonical_digest
        for index, status in enumerate(("failed", "cancelled", "incomplete")):
            self.key = _canonical_digest({"separate-fixture": index})
            _, self.row = self.journal.acceptance_claim(self.key, {"status": status}, cents=0, requests=2,
                scope=f"status-fixture-{index}", limit_cents=0, limit_requests=2)
            with self.subTest(status=status), self.assertRaises(AcceptanceUnresolved):
                self._execute([self._completed(status=status)])
            row = self.journal.acceptance_read(self.key)
            self.assertEqual(row["state"], "RESPONSE_ID_RECORDED")
            with self.assertRaises(AcceptanceUnresolved):
                self.journal.acceptance_http(self.key, row["owner"], "create")

    def test_wrong_model_or_output_subject_refuses_after_durable_id(self):
        with self.assertRaisesRegex(ValueError, "model drift"):
            self._execute([self._completed(model="other-model")])
        self.assertEqual(self.journal.acceptance_read(self.key)["response_id"], "resp-fixture")

    def test_known_id_reconciliation_only_and_cancel_keeps_reservation(self):
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved
        self.journal.acceptance_http(self.key, self.row["owner"], "create")
        self.journal.acceptance_response_id(self.key, self.row["owner"], "resp-fixture")
        with self.assertRaises(AcceptanceUnresolved):
            self._execute([self._completed(status="cancelled")], response_id="resp-fixture", cancel=True)
        self.assertEqual([(request.method, request.url.path) for request in self.requests],
            [("POST", "/v1/responses/resp-fixture/cancel")])
        self.assertEqual(self.journal.acceptance_read(self.key)["reserved_cents"], 155)

    def test_unknown_id_reconciliation_refuses_before_http(self):
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved
        with self.assertRaises(AcceptanceUnresolved):
            self._execute(response_id="resp-not-durable")
        self.assertEqual(self.requests, [])

    def test_request_alone_supplies_trusted_subject(self):
        captured = {}
        def respond(request):
            body = json.loads(request.content)
            if request.url.path.endswith("input_tokens"):
                captured.update(body)
                return None
            self.assertEqual(captured, {k: body[k] for k in captured})
            subject = json.loads(body["instructions"].split("\n", 1)[0].removeprefix("TRUSTED SUBJECT: "))
            self.assertIn("BEGIN UNTRUSTED EVIDENCE\n", body["input"])
            self.assertIn("END UNTRUSTED EVIDENCE", body["input"])
            # All response binding values come solely from captured request bytes.
            subject["decision"] = "accept"
            response = {"id": "resp-fixture", "model": body["model"], "status": "completed",
                "usage": {"input_tokens": 500, "output_tokens": 200},
                "output": [{"type": "message", "content": [{"type": "output_text",
                    "text": json.dumps(subject)}]}]}
            return httpx.Response(200, json=response, headers={"x-request-id": "fixture"})
        self.assertEqual(self._execute(failure=respond).decision.value, "accept")

    def test_count_payload_strict_allowlist_matches_create(self):
        captured = {}
        def strict_count(request):
            body = json.loads(request.content)
            if request.url.path.endswith("input_tokens"):
                self.assertEqual(set(body), {"model", "input", "instructions", "tools", "text"})
                captured.update(body)
            else:
                self.assertEqual(captured, {k: body[k] for k in captured})
            return None
        self.assertEqual(self._execute(failure=strict_count).decision.value, "accept")

    def test_altered_digest_and_each_subject_field_refused(self):
        from director_orchestrator.langgraph_primary import _canonical_digest
        for field, value in (("evidence_digest", "sha256:" + "f" * 64),
                             ("task_id", "other"), ("candidate_sha", "b" * 40), ("revision", 1)):
            self.key = _canonical_digest({"altered": field})
            _, self.row = self.journal.acceptance_claim(self.key, {}, cents=0, requests=5,
                scope="altered-" + field, limit_cents=0, limit_requests=5)
            response = self._completed()
            content = response["output"][0]["content"][0]
            output = json.loads(content["text"])
            output[field] = value
            content["text"] = json.dumps(output)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "subject/evidence mismatch"):
                self._execute([response])

    def test_http_failures_preserve_accounting_and_never_retry(self):
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved, _canonical_digest
        class BrokenStream(httpx.SyncByteStream):
            def __init__(self, close_failure=False):
                self.close_failure = close_failure
            def __iter__(self):
                if self.close_failure:
                    yield b'{"id":"resp-fixture","status":"queued"}'
                else:
                    yield b'{"partial":'
                    raise httpx.StreamError("fixture private detail")
            def close(self):
                if self.close_failure:
                    raise httpx.CloseError("fixture private detail")
        cases = ("count", "timeout", "disconnect", "status", "partial", "close", "retrieve", "cancel")
        for case in cases:
            self.key = _canonical_digest({"failure": case})
            _, self.row = self.journal.acceptance_claim(self.key, {}, **replace(
                self.plan, scope="failure-" + case).reservation())
            self.requests = []
            if case == "cancel":
                self.journal.acceptance_http(self.key, self.row["owner"], "create")
                self.journal.acceptance_response_id(self.key, self.row["owner"], "resp-fixture")
            def fail(request):
                if request.url.path.endswith("input_tokens"):
                    if case == "count":
                        raise httpx.ConnectError("fixture private detail")
                    return None
                if case == "retrieve" and request.method == "POST":
                    return httpx.Response(200, json={"id": "resp-fixture", "status": "queued"},
                        headers={"x-request-id": "fixture"})
                if case == "status":
                    return httpx.Response(503, headers={"x-request-id": "fixture"})
                if case in {"partial", "close"}:
                    return httpx.Response(200, stream=BrokenStream(case == "close"),
                        headers={"x-request-id": "fixture"})
                raise (httpx.ReadTimeout("fixture private detail") if case == "timeout"
                       else httpx.RemoteProtocolError("fixture private detail"))
            options = {"response_id": "resp-fixture", "cancel": True} if case == "cancel" else {}
            with self.subTest(case=case), self.assertRaises(AcceptanceUnresolved) as caught:
                self._execute(failure=fail, **options)
            self.assertIsInstance(caught.exception.__cause__, (httpx.HTTPError, httpx.StreamError))
            self.assertNotIn("private detail", str(caught.exception))
            row = self.journal.acceptance_read(self.key)
            expected = 1 if case in {"count", "cancel"} else 3 if case == "retrieve" else 2
            self.assertEqual(len(self.requests), expected)
            self.assertEqual(row["used_requests"], expected + (case == "cancel"))
            self.assertEqual(row["reserved_cents"], 155)
            self.assertEqual(row["state"], "RESPONSE_ID_RECORDED" if case in {"retrieve", "cancel"} else "CLAIMED")
            with self.journal.acceptance_transaction() as sql:
                creates = sql.execute("SELECT COUNT(*) FROM acceptance_http WHERE operation_key=? AND kind='create'", (self.key,)).fetchone()[0]
            self.assertEqual(creates, int(case != "count"))
            self.assertEqual(row["response_id"], "resp-fixture" if case in {"retrieve", "cancel"} else None)

    def test_output_minimum_rejects_instead_of_clamping(self):
        for value in (1, 15, True, 16.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.plan, max_output=value).reservation()
        self.assertEqual(replace(self.plan, max_output=16).reservation()["requests"], 5)

    def test_decimal_rounding_reasoning_cache_fees_and_stale_bounds(self):
        self.assertEqual(self.plan.reservation()["cents"], 155)
        self.assertEqual(replace(self.plan, max_input=1, max_output=16, max_cache_write=1).reservation()["cents"], 5)
        for change in ({"fee_bound": "NaN"}, {"fee_bound": None}, {"input_rate": 2.0},
                       {"max_output": 0}, {"max_cache_write": 0}, {"valid_until": 1},
                       {"fee_bound": "2.01"}, {"request_slots": 6}):
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                replace(self.plan, **change).reservation()


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

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:04 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 15:13 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:30 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-22 | 19:31 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 03:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:17 UTC | END]]
