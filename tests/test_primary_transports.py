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
import sys
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
    provider_lane_detail,
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
            total_token_limit=1000,
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
        self.assertEqual(json.dumps(work), body["input"])
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

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:04 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 15:13 UTC | END]]
