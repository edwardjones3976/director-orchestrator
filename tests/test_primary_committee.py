# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:10 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | BEGIN]]
"""Invited primary committee debates: offline only.

Nothing here contacts a provider. The owner's debate command enters through
the real Slack gateway (`slack_bot._forward`), is signed there and verified by
the real `/v1/intake` route; the seats are planned by the real primary
planner, bound by the real packet router and dispatched by the real Default
dispatch closure, the Director's seat through a synthetic stand-in for its
OpenAI route and Z.ai's through the real signed host bridge to a fake GLM
CLI; the persisted results are tallied by the real resolver.
"""
import importlib.util
import json
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import slack_bolt
from pydantic import ValidationError

import director_orchestrator.config as config_module
from director_orchestrator.config import Settings
from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.models import (
    AssignmentKind, DiscussionPhase, IntakeAuthentication, PrimaryCommitteeDebate, SlackIntake,
    TaskRecord, TaskStatus,
)
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.policy import preview_plan
from director_orchestrator.primary_committee import (
    CommitteeContributionInvalid, CommitteeInvitationRejected, committee_contribution,
    committee_invitation_from_command, committee_seats, committee_tally,
)
from director_orchestrator.primary_default import (
    _live_parallel_plan, _terminal_review_assignment, build_primary_committee_resolver,
)
from director_orchestrator.primary_parallel import ParallelAssignmentStatus
from tests.test_primary_default import packet_router
from tests.test_primary_provider_budgets import _LiveDispatchHarness, settings
from tests.test_zai_native import ZaiBridgeFixture, native_envelope

QUESTION = "Should the retry budget move to the gateway?"
OPTIONS = ("gateway", "worker")
BUDGETS = '{"claude":1,"grok":0.55,"openai":1,"zai":0.5}'
COMMAND = f"DIRECTOR debate\nQuestion: {QUESTION}\nOptions: gateway | worker\nInvite: Z.ai"
NOT_SEATED = ("antigravity", "claude", "cursor", "grok")
GATEWAY_SECRET = "synthetic-committee-gateway-secret-32-chars"
BEARER = "synthetic-committee-bearer"


def invitation(**changes):
    fields = dict(question=QUESTION, vote_options=OPTIONS, invited_members=("zai",))
    fields.update(changes)
    return PrimaryCommitteeDebate(**fields)


def contribution(**changes) -> dict:
    document = {
        "position": "Move the budget to the gateway.", "rationale": "One owner of the limit.",
        "evidence": ["README.md"], "contradictions": [], "assumptions": ["The gateway is deployed"],
        "material_risks": [], "unknowns": ["Peak load"], "conditions_to_change": ["A second gateway"],
        "confidence": 0.7, "vote_option": "gateway",
    }
    document.update(changes)
    return {key: value for key, value in document.items() if value is not None}


def intake_with(debate) -> SlackIntake:
    return SlackIntake(source_event_id="committee", channel_id="C123", user_id="U123",
                       text="Review the bounded result in README.md", thread_ts="1.0",
                       primary_committee_debate=debate)


def committee_record(configuration, *, debate=invitation(), intake=None, producer="grok",
                     reviewer="claude", history=(), available=("openai", "grok", "claude", "zai", "cursor")):
    intake = intake if intake is not None else intake_with(debate)
    record = TaskRecord(task_id="DIR-LG-COMMITTEE", status=TaskStatus.QUEUED, intake=intake)
    record.plan = preview_plan(record.intake.text, configuration)
    with patch("director_orchestrator.primary_roster.select_primary_roster",
               return_value=SimpleNamespace(research=None, producer=producer, reviewer=reviewer)):
        _live_parallel_plan(record, configuration, available=available, history=history)
    return record


def result_packet(provider="zai", **changes) -> dict:
    packet = {"status": "SUCCEEDED", "provider": provider, "outcome": json.dumps(contribution())}
    packet.update(changes)
    return packet


class _FakeBoltApp:
    def __init__(self, **kwargs):
        pass

    def event(self, name):
        return lambda function: function

    def command(self, name):
        return lambda function: function

    def action(self, name):
        return lambda function: function


def gateway_settings():
    return SimpleNamespace(
        log_level="INFO", slack_bot_token="xoxb-synthetic-committee", slack_app_token="xapp-synthetic-committee",
        slack_gateway_secret=GATEWAY_SECRET, shared_secret=BEARER, slack_channel_id="CFIXTURE",
        slack_owner_user_id="UFIXTURE", orchestrator_base_url="http://director.invalid", n8n_webhook_url="",
        intake_allocation_profile="engineering", intake_primary_required_capabilities=())


def slack_gateway():
    """The real gateway module, loaded with synthetic owner/channel settings."""

    source = Path(__file__).resolve().parents[1] / "src/director_orchestrator/slack_bot.py"
    spec = importlib.util.spec_from_file_location("director_orchestrator._committee_gateway_test", source)
    module = importlib.util.module_from_spec(spec)
    with patch.object(config_module, "Settings", return_value=gateway_settings()), \
         patch.object(slack_bolt, "App", _FakeBoltApp):
        spec.loader.exec_module(module)
    return module


def forward(gateway, text):
    """Owner message -> gateway; returns the posted (json, headers) or None."""

    event = {"client_msg_id": "committee-" + str(abs(hash(text))), "channel": "CFIXTURE", "user": "UFIXTURE",
             "text": text, "ts": "1788810000.123456", "thread_ts": "1788810000.000001"}
    receipt = httpx.Response(202, json={"task_id": "DIR-LG-A1B2C3D4E5"},
                             request=httpx.Request("POST", "http://director.invalid/v1/intake"))
    client = Mock()
    with patch.object(gateway.httpx, "post", return_value=receipt) as post:
        gateway._forward(event, client, require_prefix=True)
    if not post.call_count:
        return None, client
    return (post.call_args.kwargs["json"], post.call_args.kwargs["headers"]), client


def intake_route(payload, headers):
    """The real /v1/intake route with the primary application at its seam."""

    from fastapi.testclient import TestClient
    from director_orchestrator import main
    received = []

    def create_intake(intake, **kwargs):
        received.append((intake, kwargs))
        return TaskRecord(task_id="DIR-LG-A1B2C3D4E5", status=TaskStatus.QUEUED, intake=intake,
                          intake_authentication=kwargs["intake_authentication"])
    route_settings = replace(Settings(), shared_secret=BEARER, slack_gateway_secret=GATEWAY_SECRET,
                             slack_owner_user_id="UFIXTURE", slack_channel_id="CFIXTURE",
                             allow_paid_runs=True, langgraph_primary_new_intake_enabled=True)
    activation = SimpleNamespace(application=SimpleNamespace(create_intake=create_intake, recover=Mock()))
    with patch.object(main, "settings", route_settings), \
         patch.object(main, "primary_default_activation", activation):
        response = TestClient(main.app).post("/v1/intake", headers=headers, json=payload)
    return response, received


@lru_cache(maxsize=1)
def gateway_intake() -> SlackIntake:
    """The intake the real route handed to the primary application."""

    (payload, headers), _ = forward(slack_gateway(), COMMAND)
    response, received = intake_route(payload, headers)
    if response.status_code != 202:
        raise AssertionError(response.text)
    [(intake, kwargs)] = received
    if kwargs["intake_authentication"] is not IntakeAuthentication.SLACK_GATEWAY_ATTESTED:
        raise AssertionError("the route did not verify the gateway attestation")
    return intake


class InvitationTests(unittest.TestCase):
    def test_invited_members_are_the_seatable_primary_lanes(self):
        self.assertEqual(("claude", "zai"), invitation(invited_members=("claude", "zai")).invited_members)
        for changes in (dict(vote_options=("gateway",)), dict(vote_options=("gateway", " Gateway")),
                        dict(invited_members=("openai",)), dict(invited_members=("antigravity",)),
                        dict(invited_members=()), dict(invited_members=("zai", "zai")),
                        dict(vote_options=("gateway", "x" * 256))):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                invitation(**changes)

    def test_an_intake_without_an_invitation_keeps_its_exact_bytes(self):
        intake = SlackIntake(source_event_id="e", channel_id="C", thread_ts="1", user_id="U", text="t")
        self.assertNotIn("primary_committee_debate", intake.model_dump(mode="json"))
        self.assertNotIn("primary_committee_debate", intake.model_dump_json())
        invited = intake.model_copy(update={"primary_committee_debate": invitation()})
        self.assertEqual(SlackIntake.model_validate_json(invited.model_dump_json()), invited)

    def test_only_the_explicit_debate_command_parses_and_nothing_else_is_guessed(self):
        self.assertEqual(invitation(), committee_invitation_from_command(COMMAND))
        self.assertEqual(invitation(invited_members=("claude", "zai"), material_risk=True),
                         committee_invitation_from_command(
                             "/director debate\nquestion: " + QUESTION + "\noptions: gateway|worker\n"
                             "invite: claude, zai\nmaterial risk: YES"))
        for text in ("DIRECTOR review README.md", "DIRECTOR please debate this later", "debate\nQuestion: x"):
            with self.subTest(text=text):
                self.assertIsNone(committee_invitation_from_command(text))
        for text in (COMMAND.replace("Invite: Z.ai", "Invite: openai"),
                     COMMAND.replace("Invite: Z.ai", "Invite: antigravity"),
                     COMMAND.replace("\nInvite: Z.ai", ""),
                     COMMAND + "\nBudget: 100", COMMAND + "\nQuestion: again",
                     COMMAND.replace("gateway | worker", "only-one"),
                     COMMAND + "\nMaterial risk: maybe", "DIRECTOR debate the merits of X"):
            with self.subTest(text=text), self.assertRaises(CommitteeInvitationRejected):
                committee_invitation_from_command(text)


class SlackGatewayTests(unittest.TestCase):
    """The owner's command reaches the Director through the existing gateway."""

    def setUp(self):
        self.gateway = slack_gateway()

    def test_the_signed_invitation_is_verified_by_the_intake_route(self):
        (payload, headers), _ = forward(self.gateway, COMMAND)
        self.assertEqual(invitation().model_dump(mode="json"), payload["primary_committee_debate"])
        response, [(intake, kwargs)] = intake_route(payload, headers)
        self.assertEqual(202, response.status_code)
        self.assertEqual(invitation(), intake.primary_committee_debate)
        self.assertIs(kwargs["intake_authentication"], IntakeAuthentication.SLACK_GATEWAY_ATTESTED)
        # The signature covers the exact invitation bytes: a widened seat list fails.
        widened = json.loads(json.dumps(payload))
        widened["primary_committee_debate"]["invited_members"] = ["claude", "zai"]
        response, received = intake_route(widened, headers)
        self.assertEqual((403, []), (response.status_code, received))

    def test_a_malformed_command_is_refused_not_forwarded_as_plain_work(self):
        posted, client = forward(self.gateway, COMMAND + "\nBudget: 100")
        self.assertIsNone(posted)
        self.assertIn("DEBATE NOT ACCEPTED", client.chat_postMessage.call_args.kwargs["text"])

    def test_ordinary_work_and_other_users_carry_no_invitation(self):
        (payload, _), _ = forward(self.gateway, "DIRECTOR review README.md")
        self.assertNotIn("primary_committee_debate", payload)
        event = {"client_msg_id": "intruder", "channel": "CFIXTURE", "user": "UOTHER", "text": COMMAND,
                 "ts": "1.0"}
        with patch.object(self.gateway.httpx, "post") as post:
            self.gateway._forward(event, Mock(), require_prefix=True)
        post.assert_not_called()


class PlanSeatTests(unittest.TestCase):
    def setUp(self):
        self.configuration = settings(langgraph_primary_provider_attempt_budgets_usd_json=BUDGETS)

    def test_the_director_and_each_invited_member_hold_one_seat_the_review_waits_for(self):
        record = committee_record(self.configuration, intake=gateway_intake())
        producer, director, zai, review = record.plan.assignments
        self.assertEqual([director, zai], list(committee_seats(record)))
        self.assertEqual([("openai", 2), ("zai", 3)],
                         [(seat.primary_provider_lane, seat.sequence) for seat in (director, zai)])
        for seat in (director, zai):
            self.assertIs(seat.discussion_phase, DiscussionPhase.POSITION)
            self.assertEqual([], seat.depends_on)
        self.assertEqual((3.0, 1.5), (director.estimated_cost_usd, zai.estimated_cost_usd))
        self.assertIs(review.kind, AssignmentKind.INDEPENDENT_REVIEW)
        self.assertEqual([1, 2, 3], review.depends_on)
        self.assertIs(_terminal_review_assignment(record), review)
        wider = committee_record(self.configuration, debate=invitation(invited_members=("claude", "zai")))
        self.assertEqual(["openai", "claude", "zai"],
                         [seat.primary_provider_lane for seat in committee_seats(wider)])

    def test_each_packet_carries_its_own_role_and_the_review_synthesizes(self):
        record = committee_record(self.configuration)
        router = packet_router(self.configuration)
        producer, director, zai, review = record.plan.assignments
        packets = {seat.primary_provider_lane: router.planned_work_packet(record, seat) for seat in (director, zai)}
        for packet in packets.values():
            self.assertIn(QUESTION, packet["objective"])
            self.assertIn(json.dumps(list(OPTIONS)), " ".join(packet["constraints"]))
            self.assertEqual([], packet["allowed_external_effects"])
            self.assertNotIn("WRITE_ISOLATED_BRANCH", packet["allowed_tools"])
        self.assertIn("single full-weight vote", " ".join(packets["openai"]["constraints"]))
        self.assertNotIn("single full-weight vote", " ".join(packets["zai"]["constraints"]))
        # The review packet itself binds accepted dependency outcomes; its
        # router-added synthesis constraint is checked directly.
        from director_orchestrator.primary_committee import committee_position_constraints
        self.assertIn("casts no committee vote", " ".join(committee_position_constraints(record, review)))
        self.assertFalse(any("Committee" in item for item in
                             router.planned_work_packet(record, producer)["constraints"]))

    def test_without_an_invitation_the_plan_is_unchanged(self):
        record = committee_record(self.configuration, debate=None)
        self.assertEqual(2, len(record.plan.assignments))
        self.assertEqual((), committee_seats(record))

    def test_share_above_target_seats_zai_but_real_holds_remain(self):
        from director_orchestrator.primary_roster import PrimaryRosterHold
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        # Allocation share is guidance, never a block (owner 2026-09-27).
        record = committee_record(self.configuration, history=("zai",) * 19)
        self.assertEqual(["openai", "zai"], [seat.primary_provider_lane for seat in committee_seats(record)])
        for available in (("openai", "grok", "claude"), ("grok", "claude", "zai", "cursor")):
            with self.subTest(available=available):
                with self.assertRaisesRegex(PrimaryRosterHold, "committee_member_unavailable"):
                    committee_record(self.configuration, available=available)
        with self.assertRaisesRegex(WorkloadAdmissionHold, "workload_history_invalid"):
            committee_record(self.configuration, history=("forged",))
        # A seat whose lane has no configured budget is never planned.
        with self.assertRaises(RuntimeError):
            committee_record(settings(langgraph_primary_provider_attempt_budgets_usd_json=
                                      '{"claude":1,"grok":0.55,"zai":0.5}'))


class ContributionTests(unittest.TestCase):
    def test_a_complete_position_on_a_configured_option_is_accepted(self):
        self.assertEqual("gateway", committee_contribution(json.dumps(contribution()), invitation()).vote_option)
        abstained = committee_contribution(json.dumps(contribution(vote_option=None, abstain=True)), invitation())
        self.assertTrue(abstained.abstain)

    def test_anything_else_is_never_counted(self):
        cases = {
            "committee_contribution_not_json": ("not json", "[1]", None),
            "committee_contribution_invalid": (json.dumps(contribution(abstain=True)),
                                               json.dumps(contribution(confidence=2)),
                                               json.dumps({**contribution(), "weight": 5})),
            "committee_position_incomplete": (json.dumps(contribution(rationale=None)),
                                              json.dumps(contribution(unknowns=None))),
            "committee_option_unconfigured": (json.dumps(contribution(vote_option="neither")),),
        }
        for code, outcomes in cases.items():
            for outcome in outcomes:
                with self.subTest(code=code, outcome=outcome):
                    with self.assertRaises(CommitteeContributionInvalid) as caught:
                        committee_contribution(outcome, invitation())
                    self.assertEqual(code, caught.exception.reason_code)


class TallyTests(unittest.TestCase):
    def setUp(self):
        self.record = committee_record(settings(langgraph_primary_provider_attempt_budgets_usd_json=BUDGETS))

    def tally(self, director=None, zai=None):
        results = {2: director if director is not None else result_packet("openai"),
                   3: zai if zai is not None else result_packet()}
        return committee_tally(self.record, {key: value for key, value in results.items() if value},
                               producer_lane="grok")

    def test_director_and_zai_carry_2_5_plus_1_5_over_a_fixed_denominator(self):
        record = self.tally()
        self.assertEqual({"gateway": 4.0}, record.tally.option_weights)
        self.assertEqual((10.0, 4.0), (record.tally.configured_weight_total, record.tally.cast_weight_total))
        self.assertEqual((["gateway"], "advisory_recommendation"),
                         (record.tally.winning_options, record.tally.resolution))
        self.assertFalse(record.tally.authorizes_external_action)
        # Seat selection is not an availability observation.
        self.assertEqual((("openai", "zai"), NOT_SEATED), (record.seated, record.not_seated))
        self.assertEqual([], record.tally.unavailable)
        self.assertEqual(["openai", "zai"], [vote.member for vote in record.votes])

    def test_only_an_explicit_director_choice_is_its_abstention(self):
        abstained = self.tally(director=result_packet(
            "openai", outcome=json.dumps(contribution(vote_option=None, abstain=True))))
        self.assertEqual((["openai"], {"gateway": 1.5}, 10.0),
                         (abstained.tally.abstaining, abstained.tally.option_weights,
                          abstained.tally.configured_weight_total))
        for director in ({}, result_packet("openai", status="FAILED"), result_packet("claude")):
            with self.subTest(director=director):
                with self.assertRaisesRegex(CommitteeContributionInvalid,
                                            "committee_director_contribution_missing"):
                    self.tally(director=director)

    def test_a_split_vote_uses_each_configured_weight(self):
        record = self.tally(zai=result_packet(outcome=json.dumps(contribution(vote_option="worker"))))
        self.assertEqual({"gateway": 2.5, "worker": 1.5}, record.tally.option_weights)

    def test_a_missing_failed_or_relabelled_member_seat_is_never_counted(self):
        for zai in ({}, result_packet(status="FAILED"), result_packet(provider="claude")):
            with self.subTest(zai=zai):
                with self.assertRaisesRegex(CommitteeContributionInvalid, "committee_position_unresolved"):
                    self.tally(zai=zai)


class EvidencePhaseTests(unittest.TestCase):
    """The live evidence handler binds the tally, or fails closed with its reason."""

    def handler(self, committee):
        from director_orchestrator.primary_default import build_automatic_primary_live_dependencies
        providers = ("openai", "claude", "cursor")
        review = {"status": "SUCCEEDED", "blockers": [], "requested_decisions": []}
        binding = SimpleNamespace(outcome=SimpleNamespace(result_packet=review),
                                  expected_context_digest="sha256:" + "4" * 64)
        with patch("director_orchestrator.primary_default.discover_local_primary_command_transports",
                   return_value=({}, {p: SimpleNamespace(transport_kind="test") for p in providers})), \
             patch("director_orchestrator.primary_default.build_graph_native_packet_adapters",
                   return_value={p: object() for p in providers}), \
             patch("director_orchestrator.primary_default.build_primary_packet_result_resolver",
                   return_value=lambda _context: binding), \
             patch("director_orchestrator.primary_default.build_primary_committee_resolver",
                   return_value=committee), \
             patch("director_orchestrator.antigravity_team_routing.configured_provider_budget_routing",
                   return_value=object()):
            dependencies = build_automatic_primary_live_dependencies(
                replace(Settings(), langgraph_primary_antigravity_team_enabled=False), None, None)
        return dependencies.evidence_debate_handler(SimpleNamespace(
            task_id="DIR-LG-COMMITTEE", candidate_sha="a" * 40, producer_id="grok",
            provider_result_digest="sha256:" + "4" * 64))

    def test_the_tally_digest_is_bound_into_the_debate_digest(self):
        plain = self.handler(lambda _context: None)
        tallied = self.handler(lambda _context: "sha256:" + "5" * 64)
        self.assertTrue(plain.passed and tallied.passed)
        self.assertEqual(plain.evidence_digest, tallied.evidence_digest)
        self.assertNotEqual(plain.debate_digest, tallied.debate_digest)

    def test_an_uncountable_seat_fails_the_phase_with_its_reason(self):
        for code in ("committee_option_unconfigured", "committee_director_contribution_missing"):
            def refuse(_context, code=code):
                raise CommitteeContributionInvalid(code)
            result = self.handler(refuse)
            self.assertEqual((False, code), (result.passed, result.reason_code))


class LegacyBoundaryTests(unittest.TestCase):
    def test_legacy_intake_refuses_an_invited_debate_instead_of_dropping_zai(self):
        from fastapi.testclient import TestClient
        from director_orchestrator import main
        from director_orchestrator.store import TaskStore
        payload = SlackIntake(source_event_id="legacy-committee", channel_id="C123", user_id="U123",
                              text="Review README.md", thread_ts="1.0",
                              primary_committee_debate=invitation()).model_dump(mode="json")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            test_settings = replace(Settings(), shared_secret="test-secret", allow_paid_runs=False,
                                    slack_gateway_secret="", langgraph_primary_postgres_dsn="")
            run = AsyncMock(return_value=None)
            with patch.object(main, "settings", test_settings), \
                 patch.object(main, "store", TaskStore(str(Path(directory) / "director.sqlite3"))), \
                 patch.object(main, "primary_default_activation", None), \
                 patch.object(main.engine, "run", new=run):
                response = TestClient(main.app).post(
                    "/v1/intake", headers={"Authorization": "Bearer test-secret"}, json=payload)
        self.assertEqual(409, response.status_code)
        self.assertIn("Z.ai is unavailable under legacy rollback", response.json()["detail"])
        run.assert_not_awaited()


class DirectorTransport:
    """A synthetic stand-in for the Director's OpenAI route: no live call.

    It sits behind the real PacketBoundaryAdapter, which validates the work
    packet, the result contract, its linkage and its provider identity.
    """

    def __init__(self):
        self.sent = []
        self.outcome = json.dumps(contribution())

    def send(self, work_packet_json, *, context):
        work = json.loads(work_packet_json)
        self.sent.append(work)
        return {
            "schema_version": "1.0", "task_id": work["task_id"], "assignment_id": work["assignment_id"],
            "idempotency_key": work["idempotency_key"],
            "authority_snapshot_digest": work["authority_snapshot_digest"],
            "correlation": work["correlation"], "provider": "openai", "run_reference": "director-seat",
            "status": "SUCCEEDED", "outcome": self.outcome, "artifacts": [], "checks": [],
            "acceptance": [{"check": str(check), "status": "NOT_RUN", "evidence": "fixture"}
                           for check in work["acceptance_checks"]],
            "risks": [], "blockers": [], "requested_decisions": [], "side_effects": [],
            "revision_recommended": False,
            "usage": {"model": "gpt-fixture", "input_tokens": 1, "output_tokens": 1,
                      "total_tokens": 2, "cost_usd": 0.01},
        }


class CommitteeBridgeDispatchTests(ZaiBridgeFixture, _LiveDispatchHarness, unittest.TestCase):
    """Slack gateway -> /v1/intake -> planner -> router -> Default dispatch
    closure -> Director route and signed Z.ai host bridge -> persisted results
    -> charter tally, with no provider contact."""

    budgets = BUDGETS
    reserved_microusd = (550000, 1000000, 500000, 1000000)
    history = ()

    def setUp(self):
        ZaiBridgeFixture.setUp(self)
        with patch("tests.test_primary_provider_budgets.record_for",
                   side_effect=lambda configuration: committee_record(
                       configuration, intake=gateway_intake(), history=self.history)):
            _LiveDispatchHarness.setUp(self)
        _, _, adapters = self.discover(self.director_settings())
        self.director = DirectorTransport()
        lanes = {"openai": PacketBoundaryAdapter("openai", self.director), "zai": adapters["zai"]}
        # Each lane's one deployment attempt format.
        self.activation.live_dependencies.adapter_for_format = lambda provider, *_args: lanes[provider]
        self.persisted = {}

        def persist(**kw):
            self.persisted[kw["assignment_id"]] = kw["packet"]
            return SimpleNamespace(document_digest=document_digest(kw["packet"]))
        self.handle.claim_store.record_result_packet_artifact = Mock(side_effect=persist)
        recheck = patch("director_orchestrator.primary_default._require_current_dispatch_authority")
        recheck.start()
        self.addCleanup(recheck.stop)
        router = self.activation.services._packet_router
        self.seat_ids = {seat.primary_provider_lane: router._assignment_id(self.record, seat)
                         for seat in committee_seats(self.record)}

    def dispatch_seat(self, lane="zai", during=None):
        reservation, connection = self.request({"openai": 1, "zai": 2}[lane])
        self.dispatch.preflight(reservation, writer=self.writer, connection=connection, wave_budget_usage={})
        with patch("director_orchestrator.primary_default.live_provider_admissions", return_value={}), \
             during or nullcontext():
            return self.dispatch(reservation)

    def resolve(self, **statuses):
        """The live resolver over what dispatch persisted, read back by digest."""

        digests = {assignment_id: document_digest(packet) for assignment_id, packet in self.persisted.items()}
        aggregate = SimpleNamespace(terminal=True, ambiguous_slot_count=0,
            statuses={self.seat_ids[lane]: statuses.get(lane, "SUCCEEDED") for lane in self.seat_ids},
            result_digests=digests)

        def read(*, task_id, assignment_id):
            packet = self.persisted.get(assignment_id)
            return None if packet is None else SimpleNamespace(packet=packet, document_digest=digests[assignment_id])
        resolver = build_primary_committee_resolver(
            handle=SimpleNamespace(claim_store=SimpleNamespace(read_result_packet_artifact=read)),
            services=self.activation.services,
            parallel=SimpleNamespace(aggregate=lambda _task_id: aggregate))
        return resolver(SimpleNamespace(task_id=self.record.task_id))

    def test_director_and_zai_positions_reach_the_tally_and_replay_dispatches_nothing(self):
        self.envelope = native_envelope(result=json.dumps(
            {"outcome": json.dumps(contribution()), "status": "SUCCEEDED"}))
        for lane in ("openai", "zai"):
            self.assertIs(self.dispatch_seat(lane).status, ParallelAssignmentStatus.SUCCEEDED)
        # The Director's own seat and Z.ai's bridged worker were each shown the invitation.
        [director_packet] = self.director.sent
        self.assertIn("single full-weight vote", " ".join(director_packet["constraints"]))
        [call] = self.cli_calls
        self.assertIn(QUESTION, call["input"].decode("utf-8"))
        self.assertEqual((["SUCCEEDED"], 1), (self.claim_states(), self.fence.calls))
        persisted = {lane: self.persisted[self.seat_ids[lane]] for lane in self.seat_ids}
        self.assertEqual(("openai", "zai"), (persisted["openai"]["provider"], persisted["zai"]["provider"]))
        self.assertIsNone(persisted["zai"]["usage"]["cost_usd"])
        # Persisted readback -> charter tally: 2.5 + 1.5 over the fixed 10.0.
        record = committee_tally(self.record, {2: persisted["openai"], 3: persisted["zai"]}, producer_lane="grok")
        self.assertEqual(({"gateway": 4.0}, 10.0, NOT_SEATED),
                         (record.tally.option_weights, record.tally.configured_weight_total, record.not_seated))
        first = self.resolve()
        # A replayed evidence phase reads the same artifacts: same tally, no dispatch.
        self.assertEqual(first, self.resolve())
        self.assertEqual((1, 1), (len(self.director.sent), len(self.cli_calls)))
        # A replayed Z.ai attempt is answered from its bridge claim, not the CLI.
        self.assertIs(self.dispatch_seat("zai").status, ParallelAssignmentStatus.SUCCEEDED)
        self.assertEqual(1, len(self.cli_calls))

    def test_an_explicit_director_abstention_counts_as_one_and_absence_never_does(self):
        self.director.outcome = json.dumps(contribution(vote_option=None, abstain=True))
        self.envelope = native_envelope(result=json.dumps(
            {"outcome": json.dumps(contribution()), "status": "SUCCEEDED"}))
        for lane in ("openai", "zai"):
            self.dispatch_seat(lane)
        record = committee_tally(self.record, {2: self.persisted[self.seat_ids["openai"]],
                                               3: self.persisted[self.seat_ids["zai"]]}, producer_lane="grok")
        self.assertEqual((["openai"], {"gateway": 1.5}), (record.tally.abstaining, record.tally.option_weights))
        self.resolve()
        with self.assertRaisesRegex(CommitteeContributionInvalid, "committee_director_contribution_missing"):
            self.resolve(openai="FAILED")

    def test_a_signed_host_refusal_is_a_no_run_that_never_reaches_the_tally(self):
        from director_orchestrator.primary_default import PROVIDER_BRIDGE_REFUSED_BEFORE_EXECUTION
        from director_orchestrator.primary_workload import ProvenNoRunHold
        self.dispatch_seat("openai")
        # The host's CLI disappeared after readiness: the host settles its claim
        # REFUSED and signs that it launched nothing.
        with self.assertRaises(ProvenNoRunHold) as caught:
            self.dispatch_seat("zai", patch("director_orchestrator.zai_native.ZaiNativePacketTransport",
                                            side_effect=OSError("fixture: the host CLI is missing")))
        self.assertEqual(PROVIDER_BRIDGE_REFUSED_BEFORE_EXECUTION, caught.exception.reason_code)
        recorded = self.activation.coordinator.record_provider_attempt.call_args.kwargs
        self.assertEqual((ParallelAssignmentStatus.FAILED, "provider_refused_before_io"),
                         (recorded["status"], recorded["reason_code"]))
        self.assertEqual(([], ["REFUSED"]), (self.cli_calls, self.claim_states()))
        self.assertNotIn(self.seat_ids["zai"], self.persisted)
        with self.assertRaisesRegex(CommitteeContributionInvalid, "committee_position_unresolved"):
            self.resolve(zai="FAILED")

    def test_an_ambiguous_run_is_never_counted_or_released(self):
        self.envelope = native_envelope(modelUsage={"claude-opus-5": {}})
        outcome = self.dispatch_seat("zai")
        self.assertIs(outcome.status, ParallelAssignmentStatus.AMBIGUOUS)
        self.assertEqual((1, ["AMBIGUOUS"]), (len(self.cli_calls), self.claim_states()))
        self.assertNotIn(self.seat_ids["zai"], self.persisted)


class OverTargetCommitteeDispatchTests(CommitteeBridgeDispatchTests):
    """Every case above again, planned after nineteen Z.ai admissions (95%
    against an 18% target): allocation share never refuses the seat (owner
    2026-09-27), while budget and signature refusals still gate before I/O."""

    history = ("zai",) * 19

    def test_budget_and_signature_refusals_still_gate_before_any_call(self):
        from director_orchestrator.primary_parallel import PaidSetupHold
        for changes in ({"spent": 15_700_001}, {"reserved": 299_999}, {"signature": "0" * 64}):
            with self.subTest(changes=changes):
                reservation, connection = self.request(2, **changes)
                with self.assertRaises(PaidSetupHold):
                    self.dispatch.preflight(reservation, writer=self.writer, connection=connection,
                                            wave_budget_usage={})
        self.activation.coordinator.reserve_provider_attempt.assert_not_called()
        self.assertEqual(([], []), (self.cli_calls, self.claim_states()))
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:10 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:40 UTC | END]]
