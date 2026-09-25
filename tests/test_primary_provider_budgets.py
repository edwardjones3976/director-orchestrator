# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:03 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:49 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:07 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:50 UTC | BEGIN]]
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
    extra_settings: dict = {}
    delegation_ledger = None

    def setUp(self):
        self.settings = settings(langgraph_primary_enabled=True,
            langgraph_primary_live_provider_dispatch_enabled=True, allow_paid_runs=True,
            langgraph_primary_owner_token="o" * 32, allowed_starting_refs=("main",),
            repository_snapshot_root=str(Path.cwd()),
            langgraph_primary_provider_attempt_budgets_usd_json=self.budgets,
            **self.extra_settings)
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
                effect_gateways=gateways, delegation_ledger=self.delegation_ledger)
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

    def selected(self, index):
        """A reservation whose readiness selection (and deadline) exists."""

        reservation, connection = self.request(index)
        self.dispatch.preflight(reservation, writer=self.writer, connection=connection,
                                wave_budget_usage={})
        return reservation, connection

    def run_assignment(self, index):
        reservation, _ = self.selected(index)
        with patch("director_orchestrator.primary_default.live_provider_admissions",
                   return_value={}):
            return self.dispatch(reservation)

    def test_an_oversized_reviewer_fails_before_budget_admission_or_artifacts(self):
        self.record.assignment_outcomes[0].result = "x" * 20_000
        reservation, _ = self.selected(1)
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

    def test_a_class_task_whose_named_files_cannot_be_shown_is_held(self):
        """A read failure before any reservation is an owner hold that ran
        nothing, so the task is a proven no-run rather than a failure."""

        from director_orchestrator.primary_workload import (
            ProvenNoRunHold, WorkloadAdmissionHold)

        reservation, _ = self.selected(0)
        with patch("director_orchestrator.packet_routing._BLOB_READER", None), \
                patch("director_orchestrator.primary_default.live_provider_admissions") as admission, \
                self.assertRaisesRegex(WorkloadAdmissionHold,
                                       "small_task_named_contents_unavailable") as held:
            self.dispatch(reservation)
        # No attempt was reserved, so none needs settling.
        self.assertNotIsInstance(held.exception, ProvenNoRunHold)
        admission.assert_not_called()
        self.activation.coordinator.reserve_provider_attempt.assert_not_called()
        self.activation.coordinator.record_provider_attempt.assert_not_called()
        self.assertEqual([], self.dispatch_contexts)

    def test_the_initial_grant_check_spends_the_selection_deadline(self):
        """Looked up before any packet or ledger work; no selection holds."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold

        self.record.assignment_outcomes = []
        deadlines = []

        def refusal(*args, **kwargs):
            deadlines.append(kwargs.get("deadline"))
            return "small_task_pre_authorization_lapsed"

        with patch.object(self.activation.services, "execution_approval_free", return_value=True), \
                patch("director_orchestrator.primary_default.small_pre_authorization_refusal",
                      side_effect=refusal):
            # Without a selection there is no bound to spend: held before
            # the packet, the ledger or any blob is read.
            reservation, _ = self.request(0)
            with patch.object(self.activation.services._packet_router, "planned_work_packet",
                              side_effect=AssertionError("packet built")), \
                    self.assertRaisesRegex(WorkloadAdmissionHold,
                                           "readiness_selection_unavailable_before_admission"):
                self.dispatch(reservation)
            self.assertEqual([], deadlines)
            reservation, _ = self.selected(0)
            outcome = self.dispatch(reservation)
            self.assertEqual("small_task_pre_authorization_lapsed", outcome.reason_code)
            self.assertEqual(1, len(deadlines))
            self.assertIsNotNone(deadlines[0])

    def test_grant_state_refusals_before_anything_ran_are_owner_holds(self):
        """Only the grant's own state, and only while no predecessor ran;
        class-condition refusals keep FAILED."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold

        for code in ("small_task_pre_authorization_revoked",
                     "small_task_pre_authorization_unaudited",
                     "small_task_pre_authorization_commit_changed"):
            with self.subTest(code=code, stage="producer"):
                self.record.assignment_outcomes = []
                reservation, _ = self.selected(0)
                with patch.object(self.activation.services, "execution_approval_free",
                                  return_value=True), \
                        patch("director_orchestrator.primary_default.small_pre_authorization_refusal",
                              return_value=code), \
                        self.assertRaisesRegex(WorkloadAdmissionHold, code):
                    self.dispatch(reservation)
        self.activation.coordinator.reserve_provider_attempt.assert_not_called()
        # A predecessor ran: the task is incomplete, so FAILED (and a trip).
        self.record.assignment_outcomes = [AssignmentOutcome(
            assignment_sequence=1, provider=ProviderName.GROK, run_id="ran",
            result="The bounded producer result.", review_summary="ran")]
        for code in ("small_task_pre_authorization_revoked",
                     "small_task_pre_authorization_lapsed", "small_task_pre_authorization_tripped"):
            with self.subTest(code=code, stage="reviewer"):
                reservation, _ = self.selected(1)
                with patch.object(self.activation.services, "execution_approval_free",
                                  return_value=True), \
                        patch("director_orchestrator.primary_default.small_pre_authorization_refusal",
                              return_value=code):
                    outcome = self.dispatch(reservation)
                self.assertEqual((ParallelAssignmentStatus.FAILED, code),
                                 (outcome.status, outcome.reason_code))
        # Class conditions stay FAILED even for the first assignment.
        self.record.assignment_outcomes = []
        reservation, _ = self.selected(0)
        with patch.object(self.activation.services, "execution_approval_free", return_value=True), \
                patch("director_orchestrator.primary_default.small_pre_authorization_refusal",
                      return_value="small_task_pre_authorization_lapsed"):
            outcome = self.dispatch(reservation)
        self.assertEqual(ParallelAssignmentStatus.FAILED, outcome.status)

    def test_a_view_the_transport_cannot_rebuild_is_a_no_send_hold(self):
        """A read failure after the dispatch check refuses before provider I/O
        as a hold, never a failed attempt that would trip the grant."""

        from director_orchestrator.packet_routing import _PREPARED_VIEWS, validated_compact_view
        from director_orchestrator.primary_workload import WorkloadAdmissionHold

        sent = []

        def transport(packet, *, context):
            # What every compact-view transport does before its request.
            _PREPARED_VIEWS.clear()
            with patch("director_orchestrator.packet_routing._BLOB_READER", None):
                validated_compact_view(packet)
            sent.append(context)

        self.adapter.dispatch = transport
        self.record.assignment_outcomes = []
        with self.assertRaisesRegex(WorkloadAdmissionHold, "small_task_named_contents_unavailable"):
            self.run_assignment(0)
        self.assertEqual([], sent)

    def test_the_review_cap_measures_the_prepared_bytes_never_a_view_without_contents(self):
        """A read that fails after the producer ran is not measured as a
        smaller view without the files: it is refused."""

        from director_orchestrator.packet_routing import _PREPARED_VIEWS
        from director_orchestrator.primary_default import small_producer_result_refusal

        router = self.activation.services._packet_router
        producer = self.record.plan.assignments[0]
        result = {"status": "SUCCEEDED", "outcome": "short", "run_reference": "run"}
        lanes = frozenset({"grok", "claude"})
        self.record.assignment_outcomes = []
        self.assertIsNone(small_producer_result_refusal(
            self.record, self.settings, producer, result, "0" * 64, router,
            compact_view_lanes=lanes))
        _PREPARED_VIEWS.clear()
        with patch("director_orchestrator.packet_routing._BLOB_READER", None):
            self.assertEqual("small_task_named_contents_unavailable", small_producer_result_refusal(
                self.record, self.settings, producer, result, "0" * 64, router,
                compact_view_lanes=lanes))

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
        # A proven no-run: the admitted attempt is settled FAILED as refused
        # before I/O, not left STARTED, so it authorizes no retry.
        [call] = self.activation.coordinator.record_provider_attempt.call_args_list
        self.assertEqual((ParallelAssignmentStatus.FAILED, None, "provider_refused_before_io"),
                         (call.kwargs["status"], call.kwargs["result_digest"],
                          call.kwargs["reason_code"]))

    def test_holds_before_the_send_settle_the_attempt_and_holds_inside_it_do_not(self):
        """Raised before adapter.dispatch: nothing was sent. Raised by the
        transport's own recheck: it may already have talked to the provider,
        so the attempt stays STARTED and the task is not a proven no-run."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold

        self.record.assignment_outcomes = []
        checks = []

        def refusal(*args, **kwargs):
            # Admission passes; the grant lapses at the pre-send check.
            checks.append(True)
            return None if len(checks) == 1 else "small_task_pre_authorization_revoked"

        with patch.object(self.activation.services, "execution_approval_free", return_value=True), \
                patch("director_orchestrator.primary_default.small_pre_authorization_refusal",
                      side_effect=refusal), \
                self.assertRaisesRegex(WorkloadAdmissionHold,
                                       "small_task_pre_authorization_revoked"):
            self.run_assignment(0)
        [call] = self.activation.coordinator.record_provider_attempt.call_args_list
        self.assertEqual("provider_refused_before_io", call.kwargs["reason_code"])

        self.activation.coordinator.record_provider_attempt.reset_mock()

        def recheck_then_hold(_packet, *, context):
            raise WorkloadAdmissionHold("parallel_wave_cancelled_before_dispatch")

        self.adapter.dispatch = recheck_then_hold
        with self.assertRaisesRegex(WorkloadAdmissionHold, "parallel_wave_cancelled"):
            self.run_assignment(0)
        self.activation.coordinator.record_provider_attempt.assert_not_called()

    def test_a_pre_authorized_task_is_re_checked_before_every_send(self):
        """A grant that lapses between admission and the send sends nothing."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold

        self.record.assignment_outcomes = []
        checks = []

        def refusal(*args, **kwargs):
            checks.append(kwargs.get("packet") is not None)
            return None if len(checks) == 1 else "small_task_pre_authorization_tripped"

        with patch.object(self.activation.services, "execution_approval_free", return_value=True), \
                patch("director_orchestrator.primary_default.small_pre_authorization_refusal",
                      side_effect=refusal), \
                self.assertRaisesRegex(WorkloadAdmissionHold,
                                       "small_task_pre_authorization_tripped"):
            self.run_assignment(0)
        self.assertEqual([True, True], checks)
        self.assertEqual([], self.dispatch_contexts)

    def test_every_provider_request_inside_a_transport_rechecks_the_grant(self):
        """The recheck a transport calls before each of its provider requests
        carries the standing-grant check for a pre-authorized task."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold

        self.record.assignment_outcomes = []
        checks = []

        def refusal(*args, **kwargs):
            checks.append(True)
            # Admission and the pre-send check pass; the transport's own
            # recheck before its (second) provider request sees the lapse.
            return None if len(checks) < 4 else "small_task_pre_authorization_revoked"

        def two_requests(_packet, *, context):
            context.authority_recheck()      # e.g. a token-count request
            self.dispatch_contexts.append(context)
            context.authority_recheck()      # the generation request
            raise AssertionError("second provider request was sent")

        self.adapter.dispatch = two_requests
        with patch.object(self.activation.services, "execution_approval_free", return_value=True), \
                patch("director_orchestrator.primary_default.small_pre_authorization_refusal",
                      side_effect=refusal), \
                patch("director_orchestrator.primary_default._require_current_dispatch_authority"), \
                self.assertRaisesRegex(WorkloadAdmissionHold,
                                       "small_task_pre_authorization_revoked"):
            self.run_assignment(0)
        self.assertEqual(4, len(checks))
        self.assertEqual(1, len(self.dispatch_contexts))

    def test_a_class_packet_with_any_other_tool_is_refused_before_admission(self):
        reservation, _ = self.selected(1)
        self.activation.services._packet_router = packet_router(self.settings)
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



class SmallTaskHappyPathTests(_LiveDispatchHarness, unittest.TestCase):
    """One real small task, end to end through every guard it meets.

    The plan comes from the real live builder (_live_parallel_plan via
    record_for), the grant pre-authorizes it through the real exercise and
    audit ledger, and both assignments run through the real dispatch closure
    with the production read-only router: producer, then reviewer, then the
    acceptance predicate over the reviewer's result and its own packet.
    """

    budgets = '{"claude":0.15,"grok":0.1}'
    reserved_microusd = (100000, 150000)
    USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "cost_usd": 0.001}

    def setUp(self):
        import json
        import tempfile
        import uuid
        from director_orchestrator.delegation import GRANT_SCHEMA_VERSION
        from director_orchestrator.store import TaskStore
        from tests.test_primary_default import _MemoryAuditAnchor

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.delegation_ledger = TaskStore(
            str(Path(directory.name) / f"audit-{uuid.uuid4().hex}.sqlite3"),
            delegation_audit_anchor=_MemoryAuditAnchor())
        self.extra_settings = {
            "delegation_enabled": True, "delegation_suspended": False,
            "slack_owner_user_id": "U123", "slack_channel_id": "C123",
            "delegation_grant_json": json.dumps({
                "schema_version": GRANT_SCHEMA_VERSION, "grant_id": "grant-happy-path",
                "repository": "owner/repository", "branch_pattern": "main",
                "classes": ["execute_small_read_only_task"], "providers": ["grok", "claude"],
                "expires_at": "2099-01-01T00:00:00+00:00", "max_exercises": 5,
                "revoked": False}),
        }
        lanes = frozenset({"grok", "claude"})
        with patch("director_orchestrator.primary_default._compact_view_lanes",
                   return_value=lanes):
            super().setUp()
        use_fixture_blobs(self)
        self.adapter = self.activation.live_dependencies.adapter_for_format()
        self.activation.services._packet_router = packet_router(
            self.settings, read_only_tools=lambda record: small_read_only_eligible(
                record, self.settings, compact_view_lanes=lanes))
        self.results = {}
        self.handle.claim_store.record_result_packet_artifact = Mock(side_effect=lambda **kw:
            self.results.__setitem__(kw["assignment_id"], kw["packet"])
            or SimpleNamespace(document_digest=document_digest(kw["packet"])))
        # This task is running and nothing else was pre-authorized before it.
        self.activation.coordinator.get_projection.return_value = SimpleNamespace(
            projection={"terminal_classification": None, "parallel_plan_digest": None})

    def test_a_live_small_task_is_produced_reviewed_and_accepted_under_a_grant(self):
        from director_orchestrator.models import IntakeAuthentication
        from director_orchestrator.primary_default import (
            result_acceptance_passes, small_pre_authorization_refusal)

        record = self.record
        record.assignment_outcomes = []
        record.intake_authentication = IntakeAuthentication.SLACK_GATEWAY_ATTESTED
        producer, reviewer = sorted(record.plan.assignments, key=lambda item: item.sequence)
        self.assertEqual(("grok", "claude"),
                         (producer.primary_provider_lane, reviewer.primary_provider_lane))
        lanes = frozenset({"grok", "claude"})
        self.assertTrue(small_read_only_eligible(record, self.settings, compact_view_lanes=lanes))

        # Pre-authorization: the real exercise writes PRE_AUTHORIZED.
        application = self.activation.application
        self.assertTrue(application._exercise_standing_grant(
            record, attested=application._attested_owner_intake(record)))
        [entry] = self.delegation_ledger.delegation_audit_records(limit=10)
        self.assertEqual("PRE_AUTHORIZED", entry.outcome.value)
        self.activation.services.register(record, approval_free=True)
        record = self.record = self.activation.services._record(record.task_id)
        router = self.activation.services._packet_router

        def refusal():
            return small_pre_authorization_refusal(
                record, self.settings, self.delegation_ledger, approval_free=True,
                compact_view_lanes=lanes, coordinator=self.activation.coordinator)

        self.assertIsNone(refusal())

        # Producer.
        self.adapter.dispatch = lambda _packet, *, context: (
            self.dispatch_contexts.append(context) or {
                "status": "SUCCEEDED", "run_reference": "run-producer",
                "outcome": "README.md describes a plain fixture file.", "usage": self.USAGE})
        outcome = self.run_assignment(0)
        self.assertEqual(("provider_result_authenticated", ParallelAssignmentStatus.SUCCEEDED),
                         (outcome.reason_code, outcome.status))

        # Reviewer: its packet names the producer's files and carries them.
        review_packet = router.planned_work_packet(record, reviewer)
        view = validated_compact_view(review_packet)
        self.assertEqual(["README.md"], [item["path"] for item in view["contents"]])
        checks = review_packet["acceptance_checks"]
        self.adapter.dispatch = lambda _packet, *, context: (
            self.dispatch_contexts.append(context) or {
                "status": "SUCCEEDED", "run_reference": "run-reviewer",
                "outcome": "The producer's summary matches README.md.",
                "acceptance": [{"check": check, "status": "PASS", "evidence": "Read README.md."}
                               for check in checks],
                "risks": [], "blockers": [], "requested_decisions": [],
                "usage": self.USAGE})
        outcome = self.run_assignment(1)
        self.assertEqual(("provider_result_authenticated", ParallelAssignmentStatus.SUCCEEDED),
                         (outcome.reason_code, outcome.status))
        self.assertEqual(2, len(self.dispatch_contexts))
        # The reviewer ran under the narrowed small-task token cap.
        self.assertEqual(self.settings.small_task_review_max_tokens,
                         self.dispatch_contexts[-1].total_token_limit)

        # Verdict: the reviewer's recorded result against its own packet.
        review_id = router._assignment_id(record, reviewer)
        self.assertTrue(result_acceptance_passes(self.results[review_id], checks))
        # Still pre-authorized and untripped after both sends.
        self.assertIsNone(refusal())
        self.assertEqual(1, len(self.delegation_ledger.delegation_audit_records(limit=10)))

    def run_assignment(self, index):
        reservation, connection = self.request(index)
        self.dispatch.preflight(reservation, writer=self.writer, connection=connection,
                                wave_budget_usage={})
        with patch("director_orchestrator.primary_default.live_provider_admissions",
                   return_value={}), \
                patch("director_orchestrator.primary_default._require_current_dispatch_authority"):
            return self.dispatch(reservation)

if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 00:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:30 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 09:00 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:50 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:07 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:52 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:49 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 12:03 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | END]]
