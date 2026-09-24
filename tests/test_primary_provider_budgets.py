# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 09:00 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 00:55 UTC | BEGIN]]
"""Owner-configured ceilings at the real Default planning and dispatch seams."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from director_orchestrator.antigravity_team import (
    PrimaryProviderBudgetAuthority, ProviderAttemptBudget,
)
from director_orchestrator.antigravity_team_routing import PrimaryProviderBudgetRouting
from director_orchestrator.config import Settings
from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.models import AssignmentOutcome, ProviderName, SlackIntake, TaskRecord, TaskStatus
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.policy import preview_plan, small_read_only_eligible
from director_orchestrator.primary_default import (
    DefaultPrimaryActivation, DefaultPrimaryAuthority,
    _live_assignment_attempt_budget, _live_parallel_plan,
)
from director_orchestrator.primary_effect_gateways import (
    ExactEffectGatewayRegistry, GITHUB_MARK_READY, SLACK_POST_MESSAGE,
)
from director_orchestrator.primary_generation import deployment_digest
from director_orchestrator.primary_parallel import (
    PaidSetupHold, ParallelAssignmentStatus, ParallelReservation, ProviderAttemptReservation,
    ProviderTransientDispatchError,
)
from tests.test_antigravity_team import budget, fixtures, grant
from tests.test_primary_default import (
    README_BLOB,
    README_TEXT,
    SHA,
    digest,
    packet_router,
    use_fixture_blobs,
)
from director_orchestrator.packet_routing import validated_compact_view


def settings(**updates):
    return replace(Settings(), **{
        "repository_allowlist": ("owner/repository",),
        "trusted_provider_cost_ranks_json": '{"openai":3,"claude":2,"grok":1}',
        "max_task_budget_usd": 16.0, "max_daily_provider_budget_usd": 16.0,
        "langgraph_primary_provider_attempt_budgets_usd_json": '{"claude":4,"grok":0.55}',
        **updates})


def record_for(configuration):
    record = TaskRecord(task_id="DIR-LG-BUDGET", status=TaskStatus.QUEUED,
        intake=SlackIntake(source_event_id="budget", channel_id="C123", user_id="U123",
            text="Review the bounded result in README.md", thread_ts="1.0"))
    record.plan = preview_plan(record.intake.text, configuration)
    with patch("director_orchestrator.primary_roster.select_primary_roster",
               return_value=SimpleNamespace(research=None, producer="grok", reviewer="claude")):
        _live_parallel_plan(record, configuration, available=("openai", "grok", "claude"), history=())
    # Synthetic accepted producer evidence lets the real router bind the review packet.
    record.assignment_outcomes = [AssignmentOutcome(assignment_sequence=1, provider=ProviderName.GROK,
        run_id="synthetic-producer", result="The bounded producer result.", review_summary="Accepted fixture")]
    return record


class PrimaryProviderBudgetTests(unittest.TestCase):
    def test_explicit_budgets_are_bound_before_approval_without_float_overshoot(self):
        configuration = settings()
        record = record_for(configuration)
        record = TaskRecord.model_validate_json(record.model_dump_json())
        self.assertEqual([1.65, 12.0], [item.estimated_cost_usd for item in record.plan.assignments])
        self.assertEqual(Decimal("13.65"), sum(Decimal(str(item.estimated_cost_usd))
                         for item in record.plan.assignments))
        router = packet_router(configuration)
        for assignment, expected in zip(record.plan.assignments, (550000, 4000000)):
            packet = router.planned_work_packet(record, assignment)
            amount = _live_assignment_attempt_budget(record, configuration, assignment, packet)
            limits = ProviderAttemptBudget.from_usd(project_id=packet["project"], attempt_usd=amount,
                task_usd=16, daily_usd=16)
            self.assertEqual(expected, limits.attempt_microusd)
            self.assertEqual(3, packet["limits"]["retry_limit"])

    def test_defaults_keep_equal_allocation_and_configuration_is_generation_bound(self):
        configured = settings()
        default = replace(configured, max_task_budget_usd=2,
            langgraph_primary_provider_attempt_budgets_usd_json="{}")
        record = record_for(default)
        self.assertEqual([1, 1], [item.estimated_cost_usd for item in record.plan.assignments])
        assignment = record.plan.assignments[0]
        packet = packet_router(default).planned_work_packet(record, assignment)
        self.assertEqual(1 / 3, _live_assignment_attempt_budget(record, default, assignment, packet))
        self.assertNotEqual(deployment_digest(configured), deployment_digest(replace(configured,
            langgraph_primary_provider_attempt_budgets_usd_json="{}")))

    def test_bad_configuration_missing_lanes_and_over_task_totals_are_rejected(self):
        invalid = ['[]', '', '{', '{"unknown":1}', '{"claude":true}', '{"claude":"4"}',
            '{"claude":0}', '{"claude":-1}', '{"claude":NaN}', '{"claude":Infinity}',
            '{"claude":0.0000001}', '{"claude":0.5499999999999999999999999999999}',
            '{"claude":1e100}', '{"claude":1,"claude":4}', '{"Claude":4}']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                replace(settings(), langgraph_primary_provider_attempt_budgets_usd_json=raw
                    ).primary_provider_attempt_budgets_usd()
        for raw in ('{"claude":4}', '{"claude":5,"grok":0.55}'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                record_for(replace(settings(), langgraph_primary_provider_attempt_budgets_usd_json=raw))

    def test_intake_cannot_supply_unsigned_budget_configuration(self):
        raw = record_for(settings()).intake.model_dump()
        raw["langgraph_primary_provider_attempt_budgets_usd_json"] = '{"claude":100}'
        with self.assertRaises(ValidationError):
            SlackIntake.model_validate(raw)

    def test_plan_and_packet_budget_retry_mismatches_are_rejected(self):
        for mutation in ("plan", "packet", "retry", "other_assignment", "new_settings"):
            with self.subTest(mutation=mutation):
                configuration = settings()
                record = record_for(configuration)
                assignment = record.plan.assignments[0]
                packet = packet_router(configuration).planned_work_packet(record, assignment)
                if mutation == "plan":
                    assignment.estimated_cost_usd = 2
                elif mutation == "packet":
                    packet["limits"]["budget_usd"] = 2
                elif mutation == "retry":
                    packet["limits"]["retry_limit"] = 4
                elif mutation == "other_assignment":
                    record.plan.assignments[-1].estimated_cost_usd = 15
                else:
                    configuration = replace(configuration,
                        langgraph_primary_provider_attempt_budgets_usd_json='{"claude":3,"grok":0.55}')
                with self.assertRaises(ValueError):
                    _live_assignment_attempt_budget(record, configuration, assignment, packet)


class _LiveDispatchHarness:
    """The real Default activation and dispatch closure over synthetic I/O."""

    budgets = '{"claude":4,"grok":0.55}'
    reserved_microusd = (550000, 4000000)

    def setUp(self):
        self.settings = settings(langgraph_primary_enabled=True,
            langgraph_primary_live_provider_dispatch_enabled=True, allow_paid_runs=True,
            langgraph_primary_owner_token="o" * 32, allowed_starting_refs=("main",),
            repository_snapshot_root=str(Path.cwd()),
            langgraph_primary_provider_attempt_budgets_usd_json=self.budgets)
        self.writer = SimpleNamespace(engine_owner="langgraph", owner_token="o" * 32,
            lease_live=True, fencing_generation=4)

        class Connection:
            info = SimpleNamespace(server_version=170000)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @contextmanager
            def transaction(self):
                yield self

            def execute(self, sql, *_args, **_kwargs):
                return SimpleNamespace(fetchone=lambda: {"bound": None}
                    if "director.transaction_deadline" in sql else {"kill_switch": False})

        self.handle = SimpleNamespace(pool=SimpleNamespace(connection=lambda **_: Connection()),
            schema="synthetic_budget", close=lambda: None,
            claim_store=SimpleNamespace(current_task_writer=Mock(return_value=self.writer),
                record_work_packet_artifact=Mock(side_effect=lambda **kw:
                    SimpleNamespace(document_digest=document_digest(kw["packet"])))))
        authority = PrimaryProviderBudgetAuthority(SimpleNamespace(
            _clock=lambda: datetime.now(timezone.utc), _owner_authority_secret=fixtures.OWNER_SECRET),
            primary_schema=self.handle.schema)
        self.preflight_contexts, self.dispatch_contexts = [], []
        adapter = SimpleNamespace(_transport=SimpleNamespace(
            preflight=lambda _packet, *, context: self.preflight_contexts.append(context)),
            dispatch=lambda _packet, *, context:
                (self.dispatch_contexts.append(context) or {"status": "FAILED"}))
        dependencies = SimpleNamespace(team_routing=None,
            budget_routing=PrimaryProviderBudgetRouting(authority=authority, pool=self.handle.pool),
            validate=lambda _required: None,
            adapters={provider: PacketBoundaryAdapter(provider, adapter)
                      for provider in ("openai", "grok", "claude")},
            packet_result_resolver=lambda _context: None, completion_evidence_resolver=lambda _context: None,
            external_effect_resolver=lambda _context: None, evidence_debate_handler=lambda _context: None,
            acceptance_handler=lambda _context: None, candidate_sha_resolver=lambda *_args: SHA,
            format_roster=lambda _provider: (("api", "api"),), adapter_for_format=lambda *_args: adapter)
        gateways = ExactEffectGatewayRegistry(tuple(SimpleNamespace(system=system, action=action,
            mutate=Mock(), readback=Mock()) for system, action in
            (("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY))),
            allowed_actions=frozenset({("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}))
        coordinator = Mock()
        with patch("director_orchestrator.primary_default.resolve_default_primary_authority",
                   return_value=DefaultPrimaryAuthority(SHA, digest("authority"))), \
             patch("director_orchestrator.primary_default.open_postgres_control_plane", return_value=self.handle), \
             patch.object(Settings, "primary_provider_activation_bindings", return_value={"openai": object()}), \
             patch("director_orchestrator.primary_default.PrimaryProviderActivationService", return_value=SimpleNamespace(
                   health=lambda: {"combined_live_ready": True, "core_live_ready": True},
                   provider_ready=lambda *_args, **_kwargs: True)), \
             patch("director_orchestrator.primary_default.PrimaryTaskCoordinator", return_value=coordinator):
            self.activation = DefaultPrimaryActivation(self.settings, live_dependencies=dependencies,
                effect_gateways=gateways)
        self.record = record_for(self.settings)
        self.activation.services._packet_router = packet_router(self.settings)
        self.activation.services.register(self.record)
        self.record = self.activation.services._record(self.record.task_id)
        self.dispatch = self.activation.application._parallel_dispatcher

    def request(self, index, *, reserved=None, spent=0, signature=None):
        assignment = self.record.plan.assignments[index]
        provider = assignment.primary_provider_lane
        router = self.activation.services._packet_router
        packet = router.planned_work_packet(self.record, assignment)
        assignment_id = router._assignment_id(self.record, assignment)
        reservation = ParallelReservation(self.record.task_id, assignment_id, assignment.sequence,
            provider, "test-deployment", digest("reservation"))
        signed = budget(grant(), provider_id=provider, project_id=packet["project"], task_id=self.record.task_id,
            task_limit_microusd=16_000_000, daily_limit_microusd=16_000_000,
            attempt_reservation_microusd=(
                reserved if reserved is not None else self.reserved_microusd[index]))
        document = signed.model_dump(mode="json")
        if signature is not None:
            document["owner_signature"] = signature
        connection = Mock()
        connection.execute.side_effect = [Mock(fetchall=lambda: [dict(
            authority_digest=signed.grant_digest, document=document)]), Mock(fetchone=lambda: None),
            Mock(fetchone=lambda: None), Mock(fetchone=lambda: dict(task_spend=spent, daily_spend=spent))]
        self.activation.coordinator.reserve_provider_attempt.return_value = ProviderAttemptReservation(
            self.record.task_id, assignment_id, 1, provider, "api", "api",
            digest("key"), digest("attempt"), digest("budget"), total_token_limit=400000)
        return reservation, connection


class LiveBudgetBoundaryTests(_LiveDispatchHarness, unittest.TestCase):
    def test_real_readiness_and_dispatch_use_identical_heterogeneous_limits(self):
        for index, expected in enumerate((0.55, 4.0)):
            reservation, connection = self.request(index)
            self.dispatch.preflight(reservation, writer=self.writer, connection=connection, wave_budget_usage={})
            with patch("director_orchestrator.primary_default.live_provider_admissions", return_value={}) as admission:
                self.dispatch(reservation)
            self.assertEqual(expected, self.preflight_contexts[-1].budget_usd)
            self.assertEqual(expected, self.dispatch_contexts[-1].budget_usd)
            self.assertEqual(expected, admission.call_args.kwargs["attempt_usd"])

    def test_bad_plan_is_rejected_by_both_boundaries_before_reservation_or_transport(self):
        reservation, connection = self.request(1)
        self.record.plan.assignments[1].estimated_cost_usd = 13.0
        with self.assertRaises(PaidSetupHold):
            self.dispatch.preflight(reservation, writer=self.writer, connection=connection, wave_budget_usage={})
        outcome = self.dispatch(reservation)
        self.assertEqual("live_lane_budget_unauthorized", outcome.reason_code)
        self.activation.coordinator.reserve_provider_attempt.assert_not_called()
        self.assertEqual([], self.preflight_contexts)
        self.assertEqual([], self.dispatch_contexts)

    def test_signed_reservation_signature_and_cumulative_caps_still_gate_before_calls(self):
        for changes in ({"reserved":3999999}, {"signature":"0" * 64}, {"spent":12_000_001}):
            with self.subTest(changes=changes):
                reservation, connection = self.request(1, **changes)
                with self.assertRaises(PaidSetupHold):
                    self.dispatch.preflight(reservation, writer=self.writer, connection=connection, wave_budget_usage={})
                self.activation.coordinator.reserve_provider_attempt.assert_not_called()
                self.assertEqual([], self.preflight_contexts)
                self.assertEqual([], self.dispatch_contexts)


class SmallTaskDispatchWiringTests(_LiveDispatchHarness, unittest.TestCase):
    """The small-task bounds reach the real dispatch closure, not only helpers."""

    # grok produces and claude reviews within the small class: 0.3 + 0.45 <= $1.
    budgets = '{"claude":0.15,"grok":0.1}'
    reserved_microusd = (100000, 150000)

    def setUp(self):
        # Both lanes' transports send the compact view in this deployment.
        with patch("director_orchestrator.primary_default._compact_view_lanes",
                   return_value=frozenset({"grok", "claude"})):
            super().setUp()
        self.adapter = self.activation.live_dependencies.adapter_for_format()
        # The production router grants the class only READ_REPOSITORY.
        self.activation.services._packet_router = packet_router(
            self.settings, read_only_tools=lambda record: small_read_only_eligible(
                record, self.settings, compact_view_lanes=frozenset({"grok", "claude"})))
        self.handle.claim_store.record_result_packet_artifact = Mock(side_effect=lambda **kw:
            SimpleNamespace(document_digest=document_digest(kw["packet"])))
        use_fixture_blobs(self)

    def run_assignment(self, index):
        reservation, connection = self.request(index)
        self.dispatch.preflight(reservation, writer=self.writer, connection=connection,
                                wave_budget_usage={})
        with patch("director_orchestrator.primary_default.live_provider_admissions",
                   return_value={}):
            return self.dispatch(reservation)

    def test_an_oversized_reviewer_fails_before_budget_admission_or_artifacts(self):
        self.record.assignment_outcomes[0].result = "x" * 20_000
        reservation, _ = self.request(1)
        with patch("director_orchestrator.primary_default.live_provider_admissions") as admission:
            outcome = self.dispatch(reservation)
        self.assertIs(ParallelAssignmentStatus.FAILED, outcome.status)
        self.assertEqual("small_task_review_packet_over_cap", outcome.reason_code)
        admission.assert_not_called()
        self.activation.coordinator.reserve_provider_attempt.assert_not_called()
        self.handle.claim_store.record_work_packet_artifact.assert_not_called()
        self.assertEqual([], self.dispatch_contexts)

    def test_the_reviewer_context_carries_the_narrowed_token_cap(self):
        self.run_assignment(1)
        # The reservation allowed 400,000 tokens; the small-task cap only narrows.
        self.assertEqual(self.settings.small_task_review_max_tokens,
                         self.dispatch_contexts[-1].total_token_limit)
        self.assertLess(self.dispatch_contexts[-1].total_token_limit, 400000)

    USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "cost_usd": 0.001}

    def test_a_class_task_whose_named_files_cannot_be_shown_is_refused(self):
        reservation, _ = self.request(0)
        with patch("director_orchestrator.packet_routing._BLOB_READER", None), \
                patch("director_orchestrator.primary_default.live_provider_admissions") as admission:
            outcome = self.dispatch(reservation)
        self.assertIs(ParallelAssignmentStatus.FAILED, outcome.status)
        self.assertEqual("small_task_named_contents_unavailable", outcome.reason_code)
        admission.assert_not_called()
        self.assertEqual([], self.dispatch_contexts)

    def test_both_models_see_the_same_named_file_contents(self):
        router = self.activation.services._packet_router
        views = [validated_compact_view(router.planned_work_packet(self.record, item))
                 for item in self.record.plan.assignments]
        self.assertEqual(views[0]["contents"], views[1]["contents"])
        self.assertEqual([{"path": "README.md", "blob_sha": README_BLOB,
                           "text": README_TEXT.decode()}], views[0]["contents"])
        self.assertIn("Review scope: README.md.", views[1]["objective"])

    def test_every_bridge_attempt_is_re_attested_immediately_before_its_send(self):
        """A producer retry re-challenges too; a lost attestation sends nothing."""

        from director_orchestrator.host_provider_bridge import HostBridgePacketTransport
        from director_orchestrator.primary_workload import WorkloadAdmissionHold

        bridge = HostBridgePacketTransport(
            "grok", "https://host.docker.internal:18765/v1/provider/dispatch", "s" * 40,
            writer_owner_token_digest="sha256:" + "b" * 64,
            outbound_payload_builder=validated_compact_view,
            result_normalizer=lambda packet, result: result,
            read_only_argv_digest="sha256:" + "2" * 64)
        sends = []

        def fail_transiently(_packet, *, context):
            sends.append(context)
            raise ProviderTransientDispatchError("temporary transport outage")

        self.adapter.dispatch = fail_transiently
        self.record.assignment_outcomes = []
        reservation, connection = self.request(0)
        self.dispatch.preflight(reservation, writer=self.writer, connection=connection,
                                wave_budget_usage={})
        # The attempt's transport is the bridge from here on.
        self.adapter._transport = bridge
        with patch.object(bridge, "read_only_attestation_current",
                          side_effect=[True, False]) as current, \
                patch("director_orchestrator.primary_default.live_provider_admissions",
                      return_value={}), \
                self.assertRaisesRegex(WorkloadAdmissionHold, "small_task_bridge_attestation_stale"):
            self.dispatch(reservation)
        # Checked before the first send and again before the retry, which
        # never went out.
        self.assertEqual(2, current.call_count)
        self.assertEqual(1, len(sends))

    def test_an_authenticated_host_refusal_is_a_stale_attestation(self):
        from director_orchestrator.host_provider_bridge import HostBridgeAttestationStale
        from director_orchestrator.primary_workload import WorkloadAdmissionHold

        def refuse(_packet, *, context):
            self.dispatch_contexts.append(context)
            raise HostBridgeAttestationStale("the bridge refused an unattested argv")

        self.adapter.dispatch = refuse
        self.record.assignment_outcomes = []
        with self.assertRaisesRegex(WorkloadAdmissionHold, "small_task_bridge_attestation_stale"):
            self.run_assignment(0)
        self.assertEqual(1, len(self.dispatch_contexts))

    def test_a_class_packet_with_any_other_tool_is_refused_before_admission(self):
        self.activation.services._packet_router = packet_router(self.settings)
        reservation, _ = self.request(1)
        with patch("director_orchestrator.primary_default.live_provider_admissions") as admission:
            outcome = self.dispatch(reservation)
        self.assertIs(ParallelAssignmentStatus.FAILED, outcome.status)
        self.assertEqual("small_task_packet_not_read_only", outcome.reason_code)
        admission.assert_not_called()
        self.activation.coordinator.reserve_provider_attempt.assert_not_called()
        self.assertEqual([], self.dispatch_contexts)

    def test_a_reviewer_transport_failure_is_not_retried(self):
        """Its usage is unknown, so it spent the reviewer's whole token allowance."""

        def fail(_packet, *, context):
            self.dispatch_contexts.append(context)
            raise ProviderTransientDispatchError("temporary transport outage")

        self.adapter.dispatch = fail
        outcome = self.run_assignment(1)
        self.assertIs(ParallelAssignmentStatus.FAILED, outcome.status)
        self.assertEqual("small_task_review_tokens_exhausted", outcome.reason_code)
        self.assertEqual(1, self.activation.coordinator.reserve_provider_attempt.call_count)
        self.assertEqual(1, len(self.dispatch_contexts))

    def test_a_small_task_result_without_complete_usage_fails(self):
        self.record.assignment_outcomes = []
        for usage in (None, {"total_tokens": 15}, {"cost_usd": 0.001}):
            with self.subTest(usage=usage):
                self.adapter.dispatch = lambda _packet, *, context: {
                    "status": "SUCCEEDED", "run_reference": "run-producer",
                    "outcome": "short", "usage": usage}
                outcome = self.run_assignment(0)
                self.assertIs(ParallelAssignmentStatus.FAILED, outcome.status)
                self.assertEqual("small_task_usage_unmeasured", outcome.reason_code)
                self.handle.claim_store.record_result_packet_artifact.assert_not_called()
                self.assertEqual([], self.record.assignment_outcomes)

    def test_a_producer_result_over_the_review_cap_is_refused_before_recording(self):
        self.record.assignment_outcomes = []
        self.adapter.dispatch = lambda _packet, *, context: {
            "status": "SUCCEEDED", "run_reference": "run-producer", "outcome": "x" * 20_000,
            "usage": self.USAGE}
        outcome = self.run_assignment(0)
        self.assertIs(ParallelAssignmentStatus.FAILED, outcome.status)
        self.assertEqual("small_task_producer_result_over_cap", outcome.reason_code)
        self.handle.claim_store.record_result_packet_artifact.assert_not_called()
        self.assertEqual([], self.record.assignment_outcomes)

        # A short result is recorded and becomes the reviewer's predecessor.
        self.adapter.dispatch = lambda _packet, *, context: {
            "status": "SUCCEEDED", "run_reference": "run-producer", "outcome": "short",
            "usage": self.USAGE}
        self.record.assignment_outcomes = []
        outcome = self.run_assignment(0)
        self.assertIs(ParallelAssignmentStatus.SUCCEEDED, outcome.status)
        self.assertEqual(["short"], [item.result for item in self.record.assignment_outcomes])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 00:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 09:00 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | END]]
