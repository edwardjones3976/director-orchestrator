# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:10 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | BEGIN]]
"""Invited Z.ai committee seats in the LangGraph primary: offline only.

Nothing here contacts Z.ai. The seat is planned by the real primary planner,
bound by the real packet router, dispatched by the real Default dispatch
closure through the real signed host bridge to a fake GLM CLI, and its
persisted result is tallied by the real resolver with the charter's weights.
"""
import json
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from director_orchestrator.config import Settings
from director_orchestrator.models import (
    AssignmentKind, DiscussionPhase, PrimaryCommitteeDebate, SlackIntake, TaskRecord, TaskStatus,
)
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.policy import preview_plan
from director_orchestrator.primary_committee import (
    CommitteeContributionInvalid, committee_contribution, committee_seats, committee_tally,
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
BUDGETS = '{"claude":1,"grok":0.55,"zai":0.5}'
OTHER_MEMBERS = ["antigravity", "claude", "cursor", "grok", "openai"]


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


def committee_record(configuration, *, debate=invitation(), producer="grok", reviewer="claude",
                     history=(), available=("openai", "grok", "claude", "zai")):
    record = TaskRecord(task_id="DIR-LG-COMMITTEE", status=TaskStatus.QUEUED,
        intake=SlackIntake(source_event_id="committee", channel_id="C123", user_id="U123",
            text="Review the bounded result in README.md", thread_ts="1.0",
            primary_committee_debate=debate))
    record.plan = preview_plan(record.intake.text, configuration)
    with patch("director_orchestrator.primary_roster.select_primary_roster",
               return_value=SimpleNamespace(research=None, producer=producer, reviewer=reviewer)):
        _live_parallel_plan(record, configuration, available=available, history=history)
    return record


def result_packet(**changes) -> dict:
    packet = {"status": "SUCCEEDED", "provider": "zai", "outcome": json.dumps(contribution())}
    packet.update(changes)
    return packet


class InvitationTests(unittest.TestCase):
    def test_only_a_bounded_zai_invitation_is_accepted(self):
        for changes in (dict(vote_options=("gateway",)), dict(vote_options=("gateway", " Gateway")),
                        dict(invited_members=("claude",)), dict(invited_members=()),
                        dict(vote_options=("gateway", "x" * 256))):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                invitation(**changes)

    def test_an_intake_without_an_invitation_keeps_its_exact_bytes(self):
        intake = SlackIntake(source_event_id="e", channel_id="C", thread_ts="1", user_id="U", text="t")
        self.assertNotIn("primary_committee_debate", intake.model_dump(mode="json"))
        self.assertNotIn("primary_committee_debate", intake.model_dump_json())
        invited = intake.model_copy(update={"primary_committee_debate": invitation()})
        self.assertEqual(SlackIntake.model_validate_json(invited.model_dump_json()), invited)


class PlanSeatTests(unittest.TestCase):
    def setUp(self):
        self.configuration = settings(langgraph_primary_provider_attempt_budgets_usd_json=BUDGETS)

    def test_invited_zai_holds_one_position_seat_the_review_waits_for(self):
        record = committee_record(self.configuration)
        producer, seat, review = record.plan.assignments
        self.assertEqual([seat], list(committee_seats(record)))
        self.assertEqual((seat.sequence, seat.primary_provider_lane, seat.agent.value), (2, "zai", "zai"))
        self.assertIs(seat.discussion_phase, DiscussionPhase.POSITION)
        self.assertEqual([], seat.depends_on)
        self.assertEqual(1.5, seat.estimated_cost_usd)
        self.assertIs(review.kind, AssignmentKind.INDEPENDENT_REVIEW)
        self.assertEqual([1, 2], review.depends_on)
        self.assertIs(_terminal_review_assignment(record), review)

    def test_the_seat_packet_carries_the_question_and_options_only_to_the_seat(self):
        record = committee_record(self.configuration)
        router = packet_router(self.configuration)
        producer, seat, review = record.plan.assignments
        packet = router.planned_work_packet(record, seat)
        self.assertIn(QUESTION, packet["objective"])
        self.assertIn(json.dumps(list(OPTIONS)), " ".join(packet["constraints"]))
        self.assertEqual([], packet["allowed_external_effects"])
        self.assertNotIn("WRITE_ISOLATED_BRANCH", packet["allowed_tools"])
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
        self.assertEqual(["zai"], [seat.primary_provider_lane for seat in committee_seats(record)])
        with self.assertRaisesRegex(PrimaryRosterHold, "committee_member_unavailable"):
            committee_record(self.configuration, available=("openai", "grok", "claude"))
        with self.assertRaisesRegex(WorkloadAdmissionHold, "workload_history_invalid"):
            committee_record(self.configuration, history=("forged",))
        # A seat whose lane has no configured budget is never planned.
        with self.assertRaises(RuntimeError):
            committee_record(settings(langgraph_primary_provider_attempt_budgets_usd_json='{"claude":1,"grok":0.55}'))


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

    def test_the_zai_vote_carries_its_owner_approved_weight_over_a_fixed_denominator(self):
        votes, tally = committee_tally(self.record, {2: result_packet()}, producer_lane="grok")
        self.assertEqual({"gateway": 1.5}, tally.option_weights)
        self.assertEqual(10.0, tally.configured_weight_total)
        self.assertEqual(1.5, tally.cast_weight_total)
        self.assertEqual(OTHER_MEMBERS, tally.unavailable)
        self.assertEqual((["gateway"], "advisory_recommendation"), (tally.winning_options, tally.resolution))
        self.assertFalse(tally.authorizes_external_action)
        [zai] = [vote for vote in votes if vote.member == "zai"]
        self.assertFalse(zai.producer)

    def test_an_abstention_casts_nothing_and_keeps_the_denominator(self):
        _, tally = committee_tally(self.record, {2: result_packet(
            outcome=json.dumps(contribution(vote_option=None, abstain=True)))}, producer_lane="grok")
        self.assertEqual((["zai"], "no_votes", 10.0), (tally.abstaining, tally.resolution, tally.configured_weight_total))

    def test_a_missing_failed_or_relabelled_seat_is_never_counted(self):
        for results in ({}, {2: result_packet(status="FAILED")}, {2: result_packet(provider="claude")}):
            with self.subTest(results=results):
                with self.assertRaisesRegex(CommitteeContributionInvalid, "committee_position_unresolved"):
                    committee_tally(self.record, results, producer_lane="grok")


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
        def refuse(_context):
            raise CommitteeContributionInvalid("committee_option_unconfigured")
        result = self.handler(refuse)
        self.assertEqual((False, "committee_option_unconfigured"), (result.passed, result.reason_code))


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


class CommitteeBridgeDispatchTests(ZaiBridgeFixture, _LiveDispatchHarness, unittest.TestCase):
    """Planner -> router -> Default dispatch closure -> signed host bridge ->
    fake GLM CLI -> persisted result -> charter tally, with no Z.ai contact."""

    budgets = BUDGETS
    reserved_microusd = (550000, 500000, 1000000)
    history = ()

    def setUp(self):
        ZaiBridgeFixture.setUp(self)
        with patch("tests.test_primary_provider_budgets.record_for",
                   side_effect=lambda configuration: committee_record(configuration, history=self.history)):
            _LiveDispatchHarness.setUp(self)
        _, _, adapters = self.discover(self.director_settings())
        # The deployment's one attempt format for the lane: the bridge adapter.
        self.activation.live_dependencies.adapter_for_format = lambda *_args: adapters["zai"]
        self.persisted = {}

        def persist(**kw):
            self.persisted[kw["assignment_id"]] = kw["packet"]
            return SimpleNamespace(document_digest=document_digest(kw["packet"]))
        self.handle.claim_store.record_result_packet_artifact = Mock(side_effect=persist)
        recheck = patch("director_orchestrator.primary_default._require_current_dispatch_authority")
        recheck.start()
        self.addCleanup(recheck.stop)
        self.seat = self.record.plan.assignments[1]
        self.seat_id = self.activation.services._packet_router._assignment_id(self.record, self.seat)

    def dispatch_seat(self, during=None):
        reservation, connection = self.request(1)
        self.dispatch.preflight(reservation, writer=self.writer, connection=connection, wave_budget_usage={})
        with patch("director_orchestrator.primary_default.live_provider_admissions", return_value={}), \
             during or nullcontext():
            return self.dispatch(reservation)

    def resolve(self, status="SUCCEEDED"):
        packet = self.persisted.get(self.seat_id)
        digest = document_digest(packet) if packet is not None else None
        aggregate = SimpleNamespace(terminal=True, ambiguous_slot_count=0,
            statuses={self.seat_id: status}, result_digests={self.seat_id: digest} if digest else {})
        store = SimpleNamespace(read_result_packet_artifact=lambda **_kw: None if packet is None
                                else SimpleNamespace(packet=packet, document_digest=digest))
        resolver = build_primary_committee_resolver(
            handle=SimpleNamespace(claim_store=store), services=self.activation.services,
            parallel=SimpleNamespace(aggregate=lambda _task_id: aggregate))
        return resolver(SimpleNamespace(task_id=self.record.task_id))

    def test_the_invited_packet_reaches_the_bridge_and_its_position_reaches_the_tally(self):
        self.envelope = native_envelope(result=json.dumps(
            {"outcome": json.dumps(contribution()), "status": "SUCCEEDED"}))
        outcome = self.dispatch_seat()
        self.assertIs(outcome.status, ParallelAssignmentStatus.SUCCEEDED)
        # One fenced, claimed attempt; the GLM worker was shown the invitation.
        [call] = self.cli_calls
        shown = call["input"].decode("utf-8")
        self.assertIn(QUESTION, shown)
        self.assertIn("Committee POSITION turn", shown)
        self.assertEqual(["SUCCEEDED"], self.claim_states())
        self.assertEqual(1, self.fence.calls)
        persisted = self.persisted[self.seat_id]
        self.assertEqual(("zai", None), (persisted["provider"], persisted["usage"]["cost_usd"]))
        # The persisted, validated position reaches the charter tally.
        _, tally = committee_tally(self.record, {2: persisted}, producer_lane="grok")
        self.assertEqual(({"gateway": 1.5}, 10.0), (tally.option_weights, tally.configured_weight_total))
        first = self.resolve()
        # A replayed evidence phase reads the same artifacts: same tally, no model call.
        self.assertEqual(first, self.resolve())
        self.assertEqual(1, len(self.cli_calls))
        # A replayed dispatch of the same attempt is answered from the bridge claim.
        self.assertIs(self.dispatch_seat().status, ParallelAssignmentStatus.SUCCEEDED)
        self.assertEqual(1, len(self.cli_calls))

    def test_a_signed_host_refusal_is_a_no_run_that_never_reaches_the_tally(self):
        from director_orchestrator.primary_default import PROVIDER_BRIDGE_REFUSED_BEFORE_EXECUTION
        from director_orchestrator.primary_workload import ProvenNoRunHold
        # The host's CLI disappeared after readiness: the host settles its claim
        # REFUSED and signs that it launched nothing.
        with self.assertRaises(ProvenNoRunHold) as caught:
            self.dispatch_seat(patch("director_orchestrator.zai_native.ZaiNativePacketTransport",
                                     side_effect=OSError("fixture: the host CLI is missing")))
        self.assertEqual(PROVIDER_BRIDGE_REFUSED_BEFORE_EXECUTION, caught.exception.reason_code)
        recorded = self.activation.coordinator.record_provider_attempt.call_args.kwargs
        self.assertEqual((ParallelAssignmentStatus.FAILED, "provider_refused_before_io"),
                         (recorded["status"], recorded["reason_code"]))
        self.assertEqual(([], ["REFUSED"], {}), (self.cli_calls, self.claim_states(), self.persisted))
        with self.assertRaisesRegex(CommitteeContributionInvalid, "committee_position_unresolved"):
            self.resolve(status="FAILED")

    def test_an_ambiguous_run_is_never_counted_or_released(self):
        self.envelope = native_envelope(modelUsage={"claude-opus-5": {}})
        outcome = self.dispatch_seat()
        self.assertIs(outcome.status, ParallelAssignmentStatus.AMBIGUOUS)
        self.assertEqual((1, ["AMBIGUOUS"], {}), (len(self.cli_calls), self.claim_states(), self.persisted))
        with self.assertRaisesRegex(CommitteeContributionInvalid, "committee_position_unresolved"):
            self.resolve(status="AMBIGUOUS")


class OverTargetCommitteeDispatchTests(CommitteeBridgeDispatchTests):
    """Every case above again, planned after nineteen Z.ai admissions (95%
    against an 18% target): allocation share never refuses the seat (owner
    2026-09-27), while budget and signature refusals still gate before I/O."""

    history = ("zai",) * 19

    def test_budget_and_signature_refusals_still_gate_before_any_call(self):
        from director_orchestrator.primary_parallel import PaidSetupHold
        for changes in ({"spent": 15_500_001}, {"reserved": 499_999}, {"signature": "0" * 64}):
            with self.subTest(changes=changes):
                reservation, connection = self.request(1, **changes)
                with self.assertRaises(PaidSetupHold):
                    self.dispatch.preflight(reservation, writer=self.writer, connection=connection,
                                            wave_budget_usage={})
        self.activation.coordinator.reserve_provider_attempt.assert_not_called()
        self.assertEqual(([], []), (self.cli_calls, self.claim_states()))
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 06:10 UTC | END]]
