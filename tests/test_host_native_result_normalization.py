# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:44 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:45 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 04:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 04:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 03:56 UTC | BEGIN]]
"""Factory-built bridge adapters consume authenticated native reply shapes."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from director_orchestrator.host_provider_bridge import (
    HostProviderBridgeError,
    HostBridgeChallengeEvidence,
    bridge_response_signature,
)
from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.models import AntigravityDirective, AntigravityFixtureLane
from director_orchestrator.packet_contracts import (
    PacketContractError,
    canonical_json_bytes,
    document_digest,
)
from director_orchestrator.packet_routing import (
    antigravity_directive_objective,
    compact_provider_view,
)
from director_orchestrator.primary_transports import (
    NativeCommandPacketTransport,
    antigravity_closed_payload,
    build_graph_native_packet_adapters,
    discover_local_primary_command_transports,
    normalize_native_provider_envelope,
    sends_compact_provider_view,
)
from director_orchestrator.provider_validation import normalize_capture
from tests.test_grok_responses_envelope import response as grok_response
from tests.test_provider_validation import work_packet


SECRET = "fixture-bridge-secret-" + "x" * 32
# Pinned argv digests for attested bridge lanes (values are opaque here).
ATTESTED_PINS = {"claude": "sha256:" + "1" * 64, "grok": "sha256:" + "2" * 64}
ROOT = Path(__file__).resolve().parents[1]


def claude_response():
    return {
        "type": "result", "subtype": "success", "is_error": False,
        "session_id": "claude-fixture-session",
        "result": json.dumps({"status": "SUCCEEDED", "outcome": "Evidence reviewed"}),
        "usage": {"input_tokens": 100, "output_tokens": 40},
        "total_cost_usd": 0.001,
    }


def resumed_claude_response():
    """Retained-session shape from the 2026-09-23 continuation; no private text."""
    fields = ("status", "outcome", "artifacts", "checks", "acceptance", "risks",
              "blockers", "requested_decisions", "side_effects", "revision_recommended", "usage")
    example = json.loads((ROOT / "docs/schemas/model-result-packet-v1.example.json").read_text())
    output = {key: example[key] for key in fields}
    output.update(risks=["Fixture review finding"], requested_decisions=["Confirm the fixture finding"],
                  revision_recommended=True, usage=None)
    native = claude_response()
    native.update(
        result="```json\n" + json.dumps(output) + "\n```",
        usage={"input_tokens": 2, "cache_creation_input_tokens": 5688,
               "cache_read_input_tokens": 164887, "output_tokens": 4012,
               "output_tokens_details": {"thinking_tokens": 835}},
        modelUsage={"claude-opus-5-5": {
            "inputTokens": 4, "outputTokens": 4352, "thinkingTokens": 1072,
            "cacheReadInputTokens": 176196, "cacheCreationInputTokens": 159266,
            "costUSD": 1.3964232,
        }},
        total_cost_usd=1.3964232,
    )
    return native, output


class HostNativeResultNormalizationTests(unittest.TestCase):
    def dispatch(self, provider, envelope, *, work=None, tokens=400000, budget=4):
        work = work_packet() if work is None else work
        context = ProviderAdapterContext(
            task_id=work["task_id"], assignment_id=work["assignment_id"],
            idempotency_key=work["idempotency_key"], work_packet_digest=document_digest(work),
            writer_token_digest="sha256:" + "b" * 64,
            timeout_seconds=30, budget_usd=budget, max_hops=1, max_revisions=0,
            allowed_tools=(), allowed_external_effects=(), fencing_generation=7,
            total_token_limit=tokens,
        )
        body = canonical_json_bytes(envelope)
        captured = {}

        def open_request(request, timeout=None):
            captured.update(data=request.data, headers=request.headers)
            digest = hashlib.sha256(body).hexdigest()
            response = MagicMock()
            response.read.return_value = body
            response.headers = {
                "X-Director-Response-Digest": digest,
                "X-Director-Response-Proof": bridge_response_signature(
                    SECRET, nonce=request.headers["X-director-bridge-nonce"],
                    idempotency_digest=request.headers["X-director-idempotency-digest"],
                    response_digest=digest,
                ),
            }
            response.__enter__.return_value = response
            return response

        opener = MagicMock()
        opener.open.side_effect = open_request
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ), patch(
            "director_orchestrator.host_provider_bridge.urllib.request.build_opener",
            return_value=opener,
        ):
            transports, capabilities = discover_local_primary_command_transports(
                cwd=directory, authenticated={},
                host_bridge_endpoint="https://host.docker.internal:18765/v1/provider/dispatch",
                host_bridge_secret=SECRET,
                host_bridge_writer_owner_token_digest=context.writer_token_digest,
                host_bridge_challenge=HostBridgeChallengeEvidence(
                    frozenset({provider}), "sha256:" + "c" * 64, 1
                ),
            )
            adapters = build_graph_native_packet_adapters(
                transports=transports, capabilities=capabilities
            )
            result = adapters[provider].dispatch(work, context=context)
        self.assertEqual(1, opener.open.call_count)
        return result, captured

    def test_real_shaped_native_envelopes_cross_factory_packet_validation(self):
        for provider, envelope, run, outcome in (
            ("grok", grok_response(), "resp-xai-fixture", "Canary analyzed"),
            ("claude", claude_response(), "claude-fixture-session", "Evidence reviewed"),
        ):
            with self.subTest(provider=provider):
                original = deepcopy(envelope)
                result, sent = self.dispatch(provider, envelope)
                self.assertEqual(original, envelope)
                self.assertEqual("SUCCEEDED", result["status"])
                self.assertEqual(provider, result["provider"])
                self.assertEqual(run, result["run_reference"])
                self.assertEqual(outcome, result["outcome"])
                self.assertEqual(work_packet()["task_id"], result["task_id"])
                self.assertEqual(work_packet()["authority_snapshot_digest"],
                                 result["authority_snapshot_digest"])
                self.assertEqual(40, result["usage"]["output_tokens"])
                if provider == "grok":
                    # The 40 output tokens already include 25 reasoning tokens.
                    self.assertEqual(140, result["usage"]["total_tokens"])
                    self.assertEqual(0.0005, result["usage"]["cost_usd"])
                # The bridge posts the compact view, signed with the full
                # packet's digest; the result above still binds to the packet.
                self.assertEqual(
                    canonical_json_bytes(compact_provider_view(work_packet())), sent["data"])
                self.assertEqual(
                    hashlib.sha256(canonical_json_bytes(work_packet())).hexdigest(),
                    sent["headers"]["X-director-work-packet-digest"])

    def test_existing_canonical_packets_are_preserved_without_rebinding(self):
        work = work_packet()
        for provider in ("grok", "claude"):
            with self.subTest(provider=provider):
                packet = json.loads((ROOT / "docs/schemas/model-result-packet-v1.example.json").read_text())
                packet.update({key: work[key] for key in (
                    "task_id", "assignment_id", "idempotency_key",
                    "authority_snapshot_digest", "correlation",
                )})
                packet["provider"] = provider
                packet["status"] = "FAILED"
                self.assertEqual(packet, self.dispatch(provider, packet)[0])
                for changes in ({"task_id": "wrong-task"}, {"schema_version": "2.0"}):
                    with self.subTest(changes=changes), self.assertRaises(PacketContractError):
                        self.dispatch(provider, {**packet, **changes})

    def test_outbound_bytes_per_bridge_lane_match_the_small_class_predicate(self):
        """What each bridge lane actually posts, and what eligibility believes."""

        work = work_packet()
        example = json.loads((ROOT / "docs/schemas/model-result-packet-v1.example.json").read_text())
        example.update({key: work[key] for key in (
            "task_id", "assignment_id", "idempotency_key",
            "authority_snapshot_digest", "correlation",
        )})
        lanes = ("claude", "grok", "cursor", "github-copilot")
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ):
            transports, _ = discover_local_primary_command_transports(
                cwd=directory, authenticated={},
                host_bridge_endpoint="https://host.docker.internal:18765/v1/provider/dispatch",
                host_bridge_secret=SECRET,
                host_bridge_writer_owner_token_digest="sha256:" + "b" * 64,
                # Both lanes attested and pinned: the owner's 2026-09-24 ruling.
                host_bridge_challenge=HostBridgeChallengeEvidence(
                    frozenset(lanes), "sha256:" + "c" * 64, 1,
                    read_only_attested=ATTESTED_PINS),
                host_bridge_attested_argv=ATTESTED_PINS,
            )
        for provider in lanes:
            with self.subTest(provider=provider):
                _, sent = self.dispatch(provider, {**example, "provider": provider})
                compact = provider in {"claude", "grok"}
                self.assertEqual(
                    canonical_json_bytes(compact_provider_view(work) if compact else work),
                    sent["data"])
                # A lane that posts the full packet can never join the small
                # class, so its reviewer is never measured on a view it was not sent.
                self.assertIs(compact, sends_compact_provider_view(transports[provider]))

    def test_a_bridge_lane_needs_a_fresh_attestation_matching_its_pin(self):
        """Owner ruling 2026-09-24, "Bridge with attested sandbox"."""

        from director_orchestrator.primary_default import _independent_lane_pair
        from director_orchestrator.primary_transports import OpenAIResponsesPacketTransport

        def discover(attested, pins):
            with TemporaryDirectory() as directory, patch(
                "director_orchestrator.primary_transports._which", return_value=None
            ):
                transports, _ = discover_local_primary_command_transports(
                    cwd=directory, authenticated={},
                    host_bridge_endpoint="https://host.docker.internal:18765/v1/provider/dispatch",
                    host_bridge_secret=SECRET,
                    host_bridge_writer_owner_token_digest="sha256:" + "b" * 64,
                    host_bridge_challenge=HostBridgeChallengeEvidence(
                        frozenset({"claude", "grok"}), "sha256:" + "c" * 64, 1,
                        read_only_attested=attested),
                    host_bridge_attested_argv=pins,
                )
            return transports

        grok_pin = {"grok": ATTESTED_PINS["grok"]}
        cases = {
            "attested_and_pinned": (grok_pin, grok_pin, True),
            "no_attestation": (None, grok_pin, False),
            "not_attested": ({}, grok_pin, False),
            "no_pin": (grok_pin, {}, False),
            "pin_mismatch": (grok_pin, {"grok": "sha256:" + "3" * 64}, False),
        }
        for name, (attested, pins, admitted) in cases.items():
            with self.subTest(case=name):
                transports = discover(attested, pins)
                self.assertIs(admitted, sends_compact_provider_view(transports["grok"]))
                # Readiness: OpenAI plus an attested Grok bridge is an
                # independent pair; without the attestation there is none.
                lanes = frozenset(
                    lane for lane, transport in {
                        **transports,
                        "openai": OpenAIResponsesPacketTransport(api_key="sk-test", model="gpt-test"),
                    }.items() if sends_compact_provider_view(transport))
                self.assertIs(admitted, _independent_lane_pair(lanes))

        # Each small-task attempt re-challenges: a lost, changed or
        # unauthenticated attestation stops it.
        transport = discover(grok_pin, grok_pin)["grok"]
        evidence = lambda attested: HostBridgeChallengeEvidence(
            frozenset({"grok"}), "sha256:" + "c" * 64, 2, read_only_attested=attested)
        for name, outcome, current in (
            ("unchanged", evidence(grok_pin), True),
            ("argv_changed", evidence({"grok": "sha256:" + "3" * 64}), False),
            ("attestation_lost", evidence({}), False),
            ("bad_hmac", HostProviderBridgeError("host bridge challenge authentication failed"), False),
        ):
            with self.subTest(recheck=name), patch(
                "director_orchestrator.host_provider_bridge.challenge_host_provider_bridge",
                side_effect=[outcome]):
                self.assertIs(current, transport.read_only_attestation_current())

    def test_bridge_challenges_spend_one_shared_deadline(self):
        from time import monotonic
        from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
        from director_orchestrator.primary_default import _bridge_attestations_current

        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ):
            transports, _ = discover_local_primary_command_transports(
                cwd=directory, authenticated={},
                host_bridge_endpoint="https://host.docker.internal:18765/v1/provider/dispatch",
                host_bridge_secret=SECRET,
                host_bridge_writer_owner_token_digest="sha256:" + "b" * 64,
                host_bridge_challenge=HostBridgeChallengeEvidence(
                    frozenset({"grok"}), "sha256:" + "c" * 64, 1,
                    read_only_attested={"grok": ATTESTED_PINS["grok"]}),
                host_bridge_attested_argv={"grok": ATTESTED_PINS["grok"]},
            )
        bridge = transports["grok"]
        evidence = HostBridgeChallengeEvidence(
            frozenset({"grok"}), "sha256:" + "c" * 64, 2,
            read_only_attested={"grok": ATTESTED_PINS["grok"]})
        target = "director_orchestrator.host_provider_bridge.challenge_host_provider_bridge"
        # Nothing left: refused without a request.
        with patch(target, side_effect=AssertionError("challenged")):
            self.assertFalse(bridge.read_only_attestation_current(deadline=monotonic() - 1))
        # The HTTP timeout is the remainder, never a fresh five seconds.
        with patch(target, return_value=evidence) as challenge:
            self.assertTrue(bridge.read_only_attestation_current(deadline=monotonic() + 1.5))
        self.assertLessEqual(challenge.call_args.kwargs["timeout_seconds"], 1.5)
        # Every transport of a lane shares one window.
        seen = []
        attempt = PacketBoundaryAdapter("grok", bridge)
        dependencies = SimpleNamespace(
            adapters={"grok": PacketBoundaryAdapter("grok", bridge)},
            attempt_adapters={"grok": {("json", "a"): attempt, ("text", "b"): attempt}})
        with patch.object(type(bridge), "read_only_attestation_current",
                          lambda self, *, deadline=None: seen.append(deadline) or True):
            self.assertTrue(_bridge_attestations_current(dependencies, "grok"))
        self.assertEqual(3, len(seen))
        self.assertEqual(1, len(set(seen)))

    def test_the_one_viable_pair_openai_producer_and_attested_grok_reviewer(self):
        """End to end on the parts that decide it: eligibility, readiness,
        usage, and the acceptance evidence the review must carry."""

        from dataclasses import replace
        from director_orchestrator.config import Settings
        from director_orchestrator.models import (
            Assignment, AssignmentKind, ProviderName, SlackIntake, TaskRecord, TaskStatus)
        from director_orchestrator.policy import preview_plan, small_read_only_eligible
        from director_orchestrator.primary_default import (
            _independent_lane_pair, result_acceptance_passes, small_task_usage_refusal)
        from director_orchestrator.policy import SMALL_TASK_PRODUCER_ONLY_LANES
        from director_orchestrator.primary_transports import _normalize_host_native_result
        from director_orchestrator.primary_transports import _bound_research_result

        settings = replace(Settings(), repository_allowlist=("example/demo",))
        text = "Project: demo\nSummarize README.md for the owner."
        record = TaskRecord(task_id="DIR-PAIR", status=TaskStatus.QUEUED, intake=SlackIntake(
            source_event_id="evt-pair", channel_id="C1", thread_ts="1.1", user_id="U1", text=text))
        plan = preview_plan(text, settings)
        producer, reviewer = sorted(plan.assignments, key=lambda item: item.sequence)

        def with_agents(first, second):
            return plan.model_copy(update={"assignments": [
                producer.model_copy(update={"agent": first}),
                reviewer.model_copy(update={"agent": second})]})

        lanes = frozenset({"openai", "grok"})
        record.plan = with_agents(ProviderName.OPENAI, ProviderName.GROK)
        self.assertTrue(small_read_only_eligible(record, settings, compact_view_lanes=lanes))
        # Responses can produce, never review.
        record.plan = with_agents(ProviderName.GROK, ProviderName.OPENAI)
        self.assertFalse(small_read_only_eligible(record, settings, compact_view_lanes=lanes))
        self.assertIn("openai", SMALL_TASK_PRODUCER_ONLY_LANES)
        # Readiness: that pair arms it; Responses alone, or with only another
        # producer-only reviewer, does not.
        self.assertTrue(_independent_lane_pair(lanes, reviewer_excluded={"openai"}))
        self.assertFalse(_independent_lane_pair({"openai"}, reviewer_excluded={"openai"}))
        self.assertFalse(_independent_lane_pair({"openai", "grok"},
                                                reviewer_excluded={"openai", "grok"}))
        # The Grok bridge reviewer can return acceptance evidence the review
        # accepts, with complete usage; without it, the review rejects.
        work = work_packet()
        passing = grok_response()
        passing["output"][1]["content"][0]["text"] = json.dumps({
            "status": "SUCCEEDED", "outcome": "The summary is accurate.",
            "acceptance": [{"check": check, "status": "PASS", "evidence": "Checked against README.md."}
                           for check in work["acceptance_checks"]]})
        result = _normalize_host_native_result("grok", work, passing)
        self.assertTrue(result_acceptance_passes(result, work["acceptance_checks"]))
        self.assertIsNone(small_task_usage_refusal(result, "grok", small_class=True))
        self.assertFalse(result_acceptance_passes(
            _normalize_host_native_result("grok", work, grok_response()), work["acceptance_checks"]))
        # Responses results always report NOT_RUN: never an accepted review.
        self.assertFalse(result_acceptance_passes(
            _bound_research_result("openai", work, run_reference="resp-1", outcome="done"),
            work["acceptance_checks"]))

    def test_an_accepted_review_disposes_of_exactly_the_packet_checks(self):
        """Every required check exactly once, no extra, no duplicate, all PASS."""

        from director_orchestrator.primary_default import result_acceptance_passes

        checks = ["First check", "Second check"]

        def review(*names, status="PASS"):
            return {"status": "SUCCEEDED", "risks": [], "blockers": [],
                    "requested_decisions": [],
                    "acceptance": [{"check": name, "status": status} for name in names]}

        self.assertTrue(result_acceptance_passes(review(*checks), checks))
        self.assertTrue(result_acceptance_passes(review(*reversed(checks)), checks))
        for name, (result, expected) in {
            "missing": (review("First check"), checks),
            "extra": (review(*checks, "Invented check"), checks),
            "duplicate": (review("First check", "First check", "Second check"), checks),
            "duplicate_hides_missing": (review("First check", "First check"), checks),
            "renamed": (review("First check", "Second check."), checks),
            "unrelated": (review("Anything"), checks),
            "not_pass": (review(*checks, status="NOT_RUN"), checks),
            "no_expected": (review(*checks), []),
            "unknown_expected": (review(*checks), None),
            "duplicate_expected": (review(*checks), [*checks, "First check"]),
            "not_a_mapping": ({**review(), "acceptance": ["First check", "Second check"]},
                              checks),
        }.items():
            with self.subTest(case=name):
                self.assertFalse(result_acceptance_passes(result, expected))

    def test_the_grok_reviewer_cap_is_enforced_on_the_result(self):
        """Owner ruling 2026-09-24, "Accept post-hoc cap": over-cap reported
        usage fails the attempt; within the cap it is accepted; missing
        usage is unmeasured."""

        from director_orchestrator.primary_default import (
            result_acceptance_passes, small_task_usage_refusal)

        work = work_packet()
        review = grok_response()
        review["output"][1]["content"][0]["text"] = json.dumps({
            "status": "SUCCEEDED", "outcome": "The summary is accurate.",
            "acceptance": [{"check": check, "status": "PASS", "evidence": "Checked."}
                           for check in work["acceptance_checks"]]})
        # 140 tokens reported (input 100 + output 40) and $0.0005.
        within, _ = self.dispatch("grok", review, tokens=140, budget=Decimal("0.0005"))
        self.assertEqual("SUCCEEDED", within["status"])
        self.assertTrue(result_acceptance_passes(within, work["acceptance_checks"]))
        self.assertIsNone(small_task_usage_refusal(within, "grok", small_class=True))
        for limits in ({"tokens": 139}, {"budget": Decimal("0.00049")}):
            with self.subTest(limits=limits):
                over, _ = self.dispatch("grok", review, **limits)
                # A failed result: the roster reports the attempt FAILED, the
                # task's terminal is not SUCCEEDED, and its grant trips.
                self.assertEqual("FAILED", over["status"])
                self.assertIn("admitted attempt limit", over["blockers"][-1])
                self.assertFalse(result_acceptance_passes(over, work["acceptance_checks"]))
        # A Responses-shaped Grok reply without usage is refused outright; a
        # result that reaches dispatch without usage is unmeasured.
        unmeasured = dict(review)
        unmeasured.pop("usage")
        with self.assertRaisesRegex(RuntimeError, "usage is missing"):
            self.dispatch("grok", unmeasured)
        self.assertEqual("small_task_usage_unmeasured", small_task_usage_refusal(
            {**within, "usage": None}, "grok", small_class=True))

    def test_no_native_lane_joins_the_small_class(self):
        """Owner ruling 2026-09-24: native lanes send the compact view but are
        never small-class capable; readiness and eligibility never count them."""

        from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
        from director_orchestrator.policy import preview_plan, small_read_only_eligible
        from director_orchestrator.primary_default import _compact_view_lanes, _independent_lane_pair
        from director_orchestrator.primary_transports import OpenAIResponsesPacketTransport
        from director_orchestrator.config import Settings
        from director_orchestrator.models import SlackIntake, TaskRecord, TaskStatus
        from dataclasses import replace

        with TemporaryDirectory() as directory:
            natives = {
                provider: NativeCommandPacketTransport(
                    provider, (f"{provider}.exe", "--print"), cwd=directory)
                for provider in ("claude", "grok", "cursor", "github-copilot")
            }
        for provider, transport in natives.items():
            with self.subTest(provider=provider):
                self.assertEqual("work-packet", transport.payload_mode)
                self.assertFalse(sends_compact_provider_view(transport))
        adapters = {lane: PacketBoundaryAdapter(lane, transport)
                    for lane, transport in natives.items()}
        adapters["openai"] = PacketBoundaryAdapter(
            "openai", OpenAIResponsesPacketTransport(api_key="sk-test", model="gpt-test"))
        lanes = _compact_view_lanes(SimpleNamespace(adapters=adapters, attempt_adapters={}))
        self.assertEqual(frozenset({"openai"}), lanes)
        # Readiness needs an independent pair among these: native lanes add none.
        self.assertFalse(_independent_lane_pair(lanes))
        # A native-Claude producer or a native-Cursor reviewer leaves the task
        # outside the class under this deployment's lanes.
        settings = replace(Settings(), repository_allowlist=("example/demo",))
        text = "Project: demo\nSummarize README.md for the owner."
        record = TaskRecord(task_id="DIR-NATIVE", status=TaskStatus.QUEUED, intake=SlackIntake(
            source_event_id="evt-native", channel_id="C1", thread_ts="1.1", user_id="U1", text=text))
        record.plan = preview_plan(text, settings)
        self.assertEqual({"claude", "cursor"}, {item.agent.value for item in record.plan.assignments})
        self.assertTrue(small_read_only_eligible(
            record, settings, compact_view_lanes=frozenset({"claude", "cursor"})))
        self.assertFalse(small_read_only_eligible(record, settings, compact_view_lanes=lanes))

    def test_compact_view_lanes_admits_only_lanes_whose_every_adapter_is_compact(self):
        """The production composition used at intake and at dispatch alike."""

        from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
        from director_orchestrator.primary_default import _compact_view_lanes
        from director_orchestrator.primary_transports import OpenAIResponsesPacketTransport

        lanes = ("claude", "grok", "cursor", "github-copilot")
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ):
            transports, _ = discover_local_primary_command_transports(
                cwd=directory, authenticated={},
                host_bridge_endpoint="https://host.docker.internal:18765/v1/provider/dispatch",
                host_bridge_secret=SECRET,
                host_bridge_writer_owner_token_digest="sha256:" + "b" * 64,
                # Both lanes attested and pinned: the owner's 2026-09-24 ruling.
                host_bridge_challenge=HostBridgeChallengeEvidence(
                    frozenset(lanes), "sha256:" + "c" * 64, 1,
                    read_only_attested=ATTESTED_PINS),
                host_bridge_attested_argv=ATTESTED_PINS,
            )
        adapters = {lane: PacketBoundaryAdapter(lane, transports[lane]) for lane in lanes}
        adapters["openai"] = PacketBoundaryAdapter(
            "openai", OpenAIResponsesPacketTransport(api_key="sk-test", model="gpt-test"))
        self.assertEqual(
            frozenset({"claude", "grok", "openai"}),
            _compact_view_lanes(SimpleNamespace(adapters=adapters, attempt_adapters={})))
        # These are the host-bridge Claude and Grok lanes; native ones never count.
        # One attempt adapter that posts the full packet takes its lane out.
        attempts = {
            "claude": {("json", "full"): PacketBoundaryAdapter("claude", transports["cursor"])},
            "grok": {("json", "compact"): PacketBoundaryAdapter("grok", transports["grok"])},
        }
        self.assertEqual(
            frozenset({"grok", "openai"}),
            _compact_view_lanes(SimpleNamespace(adapters=adapters, attempt_adapters=attempts)))

    def test_native_failure_is_not_promoted_to_success(self):
        native = grok_response()
        native.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        self.assertEqual("FAILED", self.dispatch("grok", native)[0]["status"])
        for changes in ({"is_error": True}, {"subtype": "error_max_turns"}, {"type": "assistant"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                RuntimeError, "not a successful terminal result"
            ):
                self.dispatch("claude", {**claude_response(), **changes})

    def test_attempt_caps_preserve_usage_and_bindings_for_native_and_canonical_replies(self):
        for provider, native in (("grok", grok_response()), ("claude", claude_response())):
            canonical, _ = self.dispatch(provider, native)
            for envelope in (native, canonical):
                original = deepcopy(envelope)
                for limits in ({"tokens": 139}, {"budget": Decimal("0.00049")}):
                    with self.subTest(provider=provider, canonical=envelope is canonical, limits=limits):
                        result, _ = self.dispatch(provider, envelope, **limits)
                        self.assertEqual("FAILED", result["status"])
                        self.assertIn("admitted attempt limit", result["blockers"][-1])
                        self.assertEqual(canonical["usage"], result["usage"])
                        for key in ("task_id", "assignment_id", "idempotency_key",
                                    "authority_snapshot_digest", "correlation", "run_reference"):
                            self.assertEqual(canonical[key], result[key])
                # Exact admitted values remain valid, including Decimal dollars.
                result, _ = self.dispatch(
                    provider, envelope, tokens=140,
                    budget=Decimal(str(canonical["usage"]["cost_usd"])),
                )
                self.assertEqual("SUCCEEDED", result["status"])
                self.assertEqual(original, envelope)

    def test_factory_antigravity_keeps_its_closed_outbound_boundary(self):
        work = work_packet()
        work["objective"] = antigravity_directive_objective(
            AntigravityDirective(lane=AntigravityFixtureLane.MODULE_SKELETON)
        )
        result, sent = self.dispatch("antigravity", {
            "session_id": "antigravity-fixture",
            "result": '{"status":"SUCCEEDED","outcome":"Generic skeleton"}',
        }, work=work)
        self.assertEqual(canonical_json_bytes(antigravity_closed_payload(work)), sent["data"])
        self.assertEqual(hashlib.sha256(canonical_json_bytes(work)).hexdigest(),
                         sent["headers"]["X-director-work-packet-digest"])
        self.assertNotIn(work["repository"].encode(), sent["data"])
        self.assertEqual("antigravity", result["provider"])
        self.assertEqual(work["authority_snapshot_digest"], result["authority_snapshot_digest"])

    def test_claude_cached_input_is_included_once_in_the_admitted_total(self):
        native = claude_response()
        native["usage"] = {
            "input_tokens": 2, "cache_creation_input_tokens": 153578,
            "cache_read_input_tokens": 11309, "output_tokens": 340,
        }
        native["total_cost_usd"] = 1.2376938
        original = deepcopy(native)
        result, _ = self.dispatch("claude", native, tokens=165229)
        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual({"input_tokens": 2, "output_tokens": 340,
                          "total_tokens": 165229, "cost_usd": 1.2376938}, result["usage"])
        for envelope in (native, result):
            with self.subTest(canonical=envelope is result):
                failed, _ = self.dispatch("claude", envelope, tokens=165228)
                self.assertEqual("FAILED", failed["status"])
                self.assertEqual(result["usage"], failed["usage"])
        # The Windows command path uses the same ceiling and gives the observer
        # the untouched raw reply alongside the truthful failed result.
        raw = canonical_json_bytes(native)
        observed = []
        with TemporaryDirectory() as directory, patch(
            "director_orchestrator.primary_transports._run_bounded_command",
            return_value=subprocess.CompletedProcess("fixture", 0, stdout=raw, stderr=b""),
        ) as run:
            transport = NativeCommandPacketTransport("claude", ["fixture"], cwd=directory)
            failed = transport.send(canonical_json_bytes(work_packet()), context=SimpleNamespace(
                fencing_generation=1, timeout_seconds=30, total_token_limit=165228,
                budget_usd=4, execution_observer=lambda *args: observed.append(args),
            ))
            run.assert_called_once()
        self.assertEqual("FAILED", failed["status"])
        self.assertEqual(result["usage"], failed["usage"])
        self.assertEqual([("claude", raw, failed)], observed)
        self.assertEqual(original, native)
        for changes in ({"cache_read_input_tokens": True},
                        {"cache_creation_input_tokens": -1}, {"input_tokens": None},
                        {"total_tokens": 342}):
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, "token usage"):
                self.dispatch("claude", {**native, "usage": {**native["usage"], **changes}})

    def test_resumed_fenced_claude_retains_findings_and_uses_cumulative_session_usage(self):
        native, output = resumed_claude_response()
        original = deepcopy(native)
        result, _ = self.dispatch("claude", native, tokens=339818)
        for key, value in output.items():
            if key != "usage":
                self.assertEqual(value, result[key])
        self.assertEqual({"input_tokens": 4, "output_tokens": 4352,
                          "total_tokens": 339818, "cost_usd": 1.3964232}, result["usage"])
        self.assertEqual(result, normalize_capture("claude", work_packet(), canonical_json_bytes(native)))
        for limits in ({"tokens": 339817}, {"budget": Decimal("1.3964231")}):
            failed, _ = self.dispatch("claude", native, **limits)
            self.assertEqual("FAILED", failed["status"])
            self.assertEqual(result["usage"], failed["usage"])
            self.assertEqual(output["risks"], failed["risks"])
        self.assertEqual(original, native)

    def test_fenced_output_must_be_one_complete_json_object(self):
        native, _ = resumed_claude_response()
        for value in (
            "prefix\n" + native["result"], native["result"] + "\nsuffix",
            native["result"] + "\n" + native["result"],
            "```json\n{}", "```python\n{}\n```", "```json\n[]\n```",
            "```json\n{broken}\n```", '```json\n{"outcome":"x","outcome":"y"}\n```',
            '```json\n{"outcome":NaN}\n```',
        ):
            with self.subTest(value=value[:24]), self.assertRaises((RuntimeError, PacketContractError)):
                self.dispatch("claude", {**native, "result": value})

    def test_cumulative_model_usage_rejects_partial_or_inconsistent_records(self):
        native, _ = resumed_claude_response()
        good = native["modelUsage"]["claude-opus-5-5"]
        bad_records = [None, {}, [], {"claude-opus-5-5": None}, {"": good}]
        for changes in ({"inputTokens": None}, {"inputTokens": True}, {"outputTokens": -1},
                        {"cacheReadInputTokens": 164886}, {"costUSD": None},
                        {"costUSD": -1}, {"costUSD": 0.1}):
            bad_records.append({"claude-opus-5-5": {**good, **changes}})
        bad_records.append({"claude-opus-5-5": {k: v for k, v in good.items() if k != "cacheReadInputTokens"}})
        for models in bad_records:
            with self.subTest(models=models), self.assertRaisesRegex(RuntimeError, "cumulative"):
                self.dispatch("claude", {**native, "modelUsage": models})
        for changes in ({"usage": None}, {"total_cost_usd": None}, {"total_cost_usd": 0.1}):
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, "cumulative"):
                self.dispatch("claude", {**native, **changes})
        # Non-finite values cannot be encoded by the authenticated fixture's
        # canonical JSON helper; exercise their normalization rejection directly.
        with self.assertRaisesRegex(RuntimeError, "cumulative"):
            normalize_native_provider_envelope("claude", work_packet(), {
                **native, "modelUsage": {"claude-opus-5-5": {**good, "costUSD": float("nan")}},
            })

    def test_all_cumulative_models_are_counted_once(self):
        native, _ = resumed_claude_response()
        native["modelUsage"]["claude-opus-5-5"].update(inputTokens=3, costUSD=1.3)
        native["modelUsage"]["fixture-auxiliary-model"] = {
            "inputTokens": 1, "outputTokens": 0,
            "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "costUSD": 0.0964232,
        }
        result, _ = self.dispatch("claude", native, tokens=339818)
        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual(339818, result["usage"]["total_tokens"])
        self.assertEqual(1.3964232, result["usage"]["cost_usd"])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 03:56 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 04:03 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 04:26 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:43 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:01 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:45 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:44 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:50 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | END]]
