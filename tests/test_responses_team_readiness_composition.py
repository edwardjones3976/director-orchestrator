# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:18 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | BEGIN]]
"""Responses and Antigravity team readiness composition: the constructor-
created preflight callback must validate an eligible assignment of either
branch before spend admission, using only the pure signed documents - never
the runtime query factory, worker factory, TeamAuthority.authorize,
admissions, or the admitted-only response-meter `prepare()` reservation.
"""
from __future__ import annotations

import os
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from director_orchestrator.antigravity_team import (
    PrimaryProviderBudgetAuthority,
    ProviderAttemptBudget,
    ProviderBudgetAdmission,
    TeamAuthority,
    TeamAuthorizationError,
    team_ddl,
)
from director_orchestrator.antigravity_team_routing import (
    PrimaryProviderBudgetRouting,
    PrimaryTeamRouting,
)
from director_orchestrator.config import Settings
from director_orchestrator.entitlement_contracts import document_digest
from director_orchestrator.langgraph_postgres import open_postgres_control_plane
from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.models import (
    AntigravityDirective,
    AntigravityFixtureLane,
    ProviderName,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.packet_contracts import validate_packet
from director_orchestrator.policy import preview_plan
from director_orchestrator.primary_default import DefaultPrimaryActivation, DefaultPrimaryAuthority
from director_orchestrator.primary_effect_gateways import (
    ExactEffectGatewayRegistry,
    GITHUB_MARK_READY,
    SLACK_POST_MESSAGE,
)
from director_orchestrator.primary_paid_preflight import check_team_setup
from director_orchestrator.primary_parallel import (
    PaidSetupHold,
    ParallelAssignment,
    ParallelAssignmentStatus,
    ParallelDispatchPolicy,
    ParallelReservation,
    PostgresParallelDispatch,
)

import hashlib
import httpx

from director_orchestrator.packet_contracts import document_digest as packet_document_digest
from director_orchestrator.primary_parallel import provider_attempt_digest, provider_attempt_idempotency
from director_orchestrator.primary_transports import OpenAIResponsesPacketTransport
from director_orchestrator.response_meter import (
    EnrolledResponseMeter,
    ResponseMeterEnrollment,
    ResponseMeterRegistry,
)

from tests.test_antigravity_team import budget, fixtures, grant, signed
from tests.test_primary_default import SHA, digest, packet_router

TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()


def _authority(secret=fixtures.OWNER_SECRET, clock=None, schema="synthetic"):
    return TeamAuthority(
        SimpleNamespace(_owner_authority_secret=secret, _clock=clock or (lambda: datetime.now(timezone.utc))),
        primary_schema=schema, enabled=True,
    )


def _team_rows(g, b):
    """The exact five read-only queries check_team_setup issues, in order."""
    return [
        Mock(fetchall=lambda: [{"grant_digest": g.digest}]),
        Mock(fetchone=lambda: {"document": g.model_dump(mode="json"),
                                "budget_document": b.model_dump(mode="json"), "revoked": False}),
        Mock(fetchone=lambda: None),
        Mock(fetchone=lambda: None),
        Mock(fetchone=lambda: {"task_spend": 0, "daily_spend": 0}),
    ]


class CheckTeamSetupUnitTests(unittest.TestCase):
    def prepared(self, **budget_changes):
        packet = dict(task_id="task-one", assignment_id="assignment-one", project="uppercut")
        g = grant(packet_digest=document_digest(packet))
        b = budget(g, **budget_changes)
        connection = Mock()
        connection.execute.side_effect = _team_rows(g, b)
        args = dict(
            connection=connection,
            team_routing=SimpleNamespace(authority=_authority()),
            settings=SimpleNamespace(max_task_budget_usd=0.0001, max_daily_provider_budget_usd=0.0001),
            packet=packet, attempt_usd=0.00006,
        )
        return g, b, args

    def test_signed_funded_readonly_path_returns_grant_and_budget_without_a_probe(self):
        g, b, args = self.prepared()
        result_grant, result_budget = check_team_setup(**args)
        self.assertEqual(result_grant, g)
        self.assertEqual(result_budget, b)
        self.assertTrue(all(call.args[0].lstrip().startswith("SELECT")
                             for call in args["connection"].execute.call_args_list))

    def test_missing_or_duplicate_registration_holds(self):
        for rows in ([], [{"grant_digest": "a" * 64}, {"grant_digest": "b" * 64}]):
            with self.subTest(rows=rows):
                _, _, args = self.prepared()
                args["connection"].execute.side_effect = [Mock(fetchall=lambda rows=rows: rows)]
                with self.assertRaises(PaidSetupHold):
                    check_team_setup(**args)

    def test_packet_digest_mismatch_holds_before_any_ceiling_check(self):
        g, b, args = self.prepared()
        args["packet"] = dict(args["packet"], project="changed-project")
        with self.assertRaises(PaidSetupHold):
            check_team_setup(**args)

    def test_undersized_signed_reservation_is_rejected(self):
        _, _, args = self.prepared(attempt_reservation_microusd=59)
        with self.assertRaisesRegex(TeamAuthorizationError, "smaller than"):
            check_team_setup(**args)

    def test_expired_grant_fails_the_pure_signature_check(self):
        packet = dict(task_id="task-one", assignment_id="assignment-one", project="uppercut")
        g = grant(packet_digest=document_digest(packet), expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        b = budget(g)
        connection = Mock()
        connection.execute.side_effect = _team_rows(g, b)
        with self.assertRaisesRegex(TeamAuthorizationError, "expired"):
            check_team_setup(connection=connection, team_routing=SimpleNamespace(authority=_authority()),
                settings=SimpleNamespace(max_task_budget_usd=0.0001, max_daily_provider_budget_usd=0.0001),
                packet=packet, attempt_usd=0.00006)

    def test_stale_budget_day_holds_even_with_a_current_grant_and_valid_signature(self):
        """A still-current team grant paired with a correctly *signed* but
        stale (yesterday's) budget must not pass readiness: `TeamAuthority._load`
        verifies the budget's signature and its binding to the grant, never its
        UTC freshness, so without this check readiness would use yesterday's
        spend totals and pass while actual monetary reserve later rejects the
        expired day - defeating the early setup hold.
        """
        _, _, args = self.prepared(day=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat())
        with self.assertRaises(PaidSetupHold):
            check_team_setup(**args)

    def test_combined_wave_exhaustion_is_refused_before_reservation(self):
        wave = {}
        g, b, args = self.prepared()
        check_team_setup(**args, wave_budget_usage=wave)
        g, b, args = self.prepared()
        with self.assertRaises(PaidSetupHold):
            check_team_setup(**args, wave_budget_usage=wave)

    def test_unknown_prior_attempt_holds(self):
        g, b, args = self.prepared()
        args["connection"].execute.side_effect = [
            Mock(fetchall=lambda: [{"grant_digest": g.digest}]),
            Mock(fetchone=lambda: {"document": g.model_dump(mode="json"),
                                    "budget_document": b.model_dump(mode="json"), "revoked": False}),
            Mock(fetchone=lambda: {"1": 1}),
        ]
        with self.assertRaises(PaidSetupHold):
            check_team_setup(**args)


class TeamReadinessWiringTests(unittest.TestCase):
    """Construct the real DefaultPrimaryActivation and invoke its attached
    preflight callback, proving the team branch validates eligibility without
    ever reaching query_factory, worker_factory, authorize or admissions."""

    def build_activation(self):
        registry = ExactEffectGatewayRegistry(
            (SimpleNamespace(system="slack", action=SLACK_POST_MESSAGE,
                mutate=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")),
                readback=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect"))),
             SimpleNamespace(system="github", action=GITHUB_MARK_READY,
                mutate=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")),
                readback=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")))),
            allowed_actions=frozenset({("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}),
        )
        settings = replace(
            Settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_live_provider_dispatch_enabled=True,
            langgraph_primary_antigravity_team_enabled=True,
            allow_paid_runs=True,
            langgraph_primary_owner_token="o" * 32,
            repository_allowlist=("owner/repository",),
            allowed_starting_refs=("main",),
            repository_snapshot_root=str(Path.cwd()),
        )
        handle = SimpleNamespace(
            claim_store=SimpleNamespace(), pool=SimpleNamespace(), close=lambda: None,
            schema="test_team_readiness_v1",
        )
        query_factory = Mock()
        worker_factory = Mock()
        team_routing = PrimaryTeamRouting(
            settings=settings, authority=_authority(schema=handle.schema), pool=handle.pool,
            query_factory=query_factory, worker_factory=worker_factory,
            worker_verifier_secret=b"w" * 32, source_root=Path.cwd(),
            artifact_store=object(), integrator=object(), writer_owner_token="owner-token",
        )
        dependencies = SimpleNamespace(
            team_routing=team_routing,
            budget_routing=PrimaryProviderBudgetRouting(
                authority=PrimaryProviderBudgetAuthority(object(), primary_schema=handle.schema), pool=handle.pool),
            validate=lambda _required: None,
            adapters={provider: PacketBoundaryAdapter(
                    provider, SimpleNamespace(dispatch=lambda *_args, **_kwargs: {}))
                for provider in ("openai", "claude", "cursor")},
            packet_result_resolver=lambda _context: None,
            completion_evidence_resolver=lambda _context: None,
            external_effect_resolver=lambda _context: None,
            evidence_debate_handler=lambda _context: None,
            acceptance_handler=lambda _context: None,
            candidate_sha_resolver=lambda *_args: SHA,
            format_roster=lambda _provider: (("api", "api"),),
            adapter_for_format=lambda *_args: None,
        )
        provider_activation = SimpleNamespace(
            health=lambda: {"combined_live_ready": True, "core_live_ready": True},
            provider_ready=lambda *_args, **_kwargs: True,
        )

        class Coordinator:
            def __init__(self, *_args, **_kwargs):
                pass

        with (
            patch("director_orchestrator.primary_default.resolve_default_primary_authority",
                  return_value=DefaultPrimaryAuthority(SHA, digest("authority"))),
            patch("director_orchestrator.primary_default.open_postgres_control_plane", return_value=handle),
            patch.object(Settings, "primary_provider_activation_bindings", return_value={"openai": object()}),
            patch("director_orchestrator.primary_default.PrimaryProviderActivationService",
                  return_value=provider_activation),
            patch("director_orchestrator.primary_default.PrimaryTaskCoordinator", Coordinator),
        ):
            activation = DefaultPrimaryActivation(settings, live_dependencies=dependencies, effect_gateways=registry)
        return activation, settings, handle, team_routing, query_factory, worker_factory

    def antigravity_reservation(self, activation, settings):
        record = TaskRecord(task_id="DIR-TEAM-READY", status=TaskStatus.QUEUED,
            intake=SlackIntake(source_event_id="team-ready", channel_id="C123",
                                user_id="U123", text="Ship a bounded change", thread_ts="1.0"))
        plan = preview_plan(record.intake.text, settings)
        assignment = plan.assignments[0].model_copy(update={"agent": ProviderName.GEMINI,
            "primary_provider_lane": "antigravity", "estimated_cost_usd": 1.0,
            "antigravity_directive": AntigravityDirective(lane=AntigravityFixtureLane.MODULE_SKELETON)})
        record.plan = plan.model_copy(update={"assignments": [assignment]})
        activation.services._packet_router = packet_router(settings)
        activation.services.register(record)
        # Read back the exact stored record/assignment: this is the source of
        # truth `preflight_parallel_assignment` independently recomputes from.
        stored = activation.services._record(record.task_id)
        stored_assignment = next(
            item for item in stored.plan.assignments if item.sequence == assignment.sequence
        )
        assignment_id = activation.services._packet_router._assignment_id(stored, stored_assignment)
        packet = validate_packet(
            "work", activation.services._packet_router.planned_work_packet(stored, stored_assignment)
        )
        reservation = ParallelReservation(record.task_id, assignment_id, assignment.sequence,
            "antigravity", "test-deployment", digest("reservation"))
        writer = SimpleNamespace(engine_owner="langgraph", owner_token=settings.langgraph_primary_owner_token,
                                  lease_live=True, fencing_generation=1)
        return reservation, writer, packet

    def test_eligible_team_assignment_passes_without_touching_factories_or_authorize(self):
        activation, settings, handle, team_routing, query_factory, worker_factory = self.build_activation()
        with patch.object(team_routing.authority, "authorize",
                           side_effect=AssertionError("preflight must not authorize")):
            reservation, writer, packet = self.antigravity_reservation(activation, settings)
            attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
            g = grant(task_id=reservation.task_id, assignment_id=reservation.assignment_id,
                       project_id=packet["project"], packet_digest=document_digest(packet))
            b = budget(g, provider_id="antigravity", project_id=packet["project"],
                task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
                daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000),
                attempt_reservation_microusd=int(
                    (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
            connection = Mock()
            connection.execute.side_effect = _team_rows(g, b)
            result = activation.application._parallel_dispatcher.preflight(
                reservation, writer=writer, connection=connection, wave_budget_usage={})
        self.assertIsNone(result)
        query_factory.assert_not_called()
        worker_factory.assert_not_called()

    def test_unregistered_team_assignment_holds_without_touching_factories(self):
        activation, settings, handle, team_routing, query_factory, worker_factory = self.build_activation()
        reservation, writer, _packet = self.antigravity_reservation(activation, settings)
        connection = Mock()
        connection.execute.side_effect = [Mock(fetchall=lambda: [])]
        with self.assertRaises(PaidSetupHold):
            activation.application._parallel_dispatcher.preflight(
                reservation, writer=writer, connection=connection, wave_budget_usage={})
        query_factory.assert_not_called()
        worker_factory.assert_not_called()


class RealPostgresReadinessCompositionTests(unittest.TestCase):
    """The actual constructor-created callback through the actual
    `PostgresParallelDispatch.reserve_ready` composition, against a real
    disposable PostgreSQL schema - not a mocked connection. Exercises both
    the Responses and the Antigravity team branch: a valid signed setup
    reaches one real ACTIVE reservation with zero attempt/spend/usage rows;
    an invalid one holds the whole wave PENDING with the real
    `paid_setup_required` diagnostic, also with zero effect rows.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for these tests")

    def setUp(self) -> None:
        self.schema = f"director_readiness_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(
            TEST_DSN, schema=self.schema, allow_insecure_local_transport=True,
        )
        with self.handle.pool.connection() as connection, connection.transaction():
            for statement in team_ddl(self.schema):
                connection.execute(statement)
        self.entitlements = SimpleNamespace(
            _owner_authority_secret=fixtures.OWNER_SECRET,
            _clock=lambda: datetime.now(timezone.utc),
        )
        self.ledger = PostgresParallelDispatch(self.handle.claim_store)
        self.policy = ParallelDispatchPolicy(max_concurrency=2)

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    def _build_activation(self, *, team_enabled: bool):
        registry = ExactEffectGatewayRegistry(
            (SimpleNamespace(system="slack", action=SLACK_POST_MESSAGE,
                mutate=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")),
                readback=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect"))),
             SimpleNamespace(system="github", action=GITHUB_MARK_READY,
                mutate=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")),
                readback=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")))),
            allowed_actions=frozenset({("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}),
        )
        settings = replace(
            Settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_live_provider_dispatch_enabled=True,
            langgraph_primary_antigravity_team_enabled=team_enabled,
            allow_paid_runs=True,
            langgraph_primary_owner_token="o" * 32,
            repository_allowlist=("owner/repository",),
            allowed_starting_refs=("main",),
            repository_snapshot_root=str(Path.cwd()),
        )
        budget_routing = PrimaryProviderBudgetRouting(
            authority=PrimaryProviderBudgetAuthority(self.entitlements, primary_schema=self.schema),
            pool=self.handle.pool,
        )
        team_routing = None
        query_factory = worker_factory = None
        if team_enabled:
            query_factory, worker_factory = Mock(), Mock()
            team_routing = PrimaryTeamRouting(
                settings=settings,
                authority=TeamAuthority(self.entitlements, primary_schema=self.schema, enabled=True),
                pool=self.handle.pool, query_factory=query_factory, worker_factory=worker_factory,
                worker_verifier_secret=b"w" * 32, source_root=Path.cwd(),
                artifact_store=object(), integrator=object(), writer_owner_token="owner-token",
            )
        adapters = {provider: PacketBoundaryAdapter(
                provider, SimpleNamespace(dispatch=lambda *_args, **_kwargs: {}))
            for provider in ("openai", "claude", "cursor")}
        dependencies = SimpleNamespace(
            team_routing=team_routing, budget_routing=budget_routing,
            validate=lambda _required: None,
            adapters=adapters,
            packet_result_resolver=lambda _context: None,
            completion_evidence_resolver=lambda _context: None,
            external_effect_resolver=lambda _context: None,
            evidence_debate_handler=lambda _context: None,
            acceptance_handler=lambda _context: None,
            candidate_sha_resolver=lambda *_args: SHA,
            format_roster=lambda _provider: (("api", "api"),),
            adapter_for_format=lambda provider, _format_transport: adapters[provider],
        )
        provider_activation = SimpleNamespace(
            health=lambda: {"combined_live_ready": True, "core_live_ready": True},
            provider_ready=lambda *_args, **_kwargs: True,
        )

        class Coordinator:
            def __init__(self, *_args, **_kwargs):
                pass

        with (
            patch("director_orchestrator.primary_default.resolve_default_primary_authority",
                  return_value=DefaultPrimaryAuthority(SHA, digest("authority"))),
            patch("director_orchestrator.primary_default.open_postgres_control_plane", return_value=self.handle),
            patch.object(Settings, "primary_provider_activation_bindings", return_value={"openai": object()}),
            patch("director_orchestrator.primary_default.PrimaryProviderActivationService",
                  return_value=provider_activation),
            patch("director_orchestrator.primary_default.PrimaryTaskCoordinator", Coordinator),
        ):
            activation = DefaultPrimaryActivation(settings, live_dependencies=dependencies, effect_gateways=registry)
        return activation, settings, query_factory, worker_factory

    def _register(self, activation, settings, *, provider: str, agent, task_id: str):
        record = TaskRecord(task_id=task_id, status=TaskStatus.QUEUED,
            intake=SlackIntake(source_event_id=task_id, channel_id="C123",
                                user_id="U123", text="Ship a bounded change", thread_ts="1.0"))
        plan = preview_plan(record.intake.text, settings)
        update = {"agent": agent, "primary_provider_lane": provider, "estimated_cost_usd": 1.0}
        if provider == "antigravity":
            update["antigravity_directive"] = AntigravityDirective(lane=AntigravityFixtureLane.MODULE_SKELETON)
        assignment = plan.assignments[0].model_copy(update=update)
        record.plan = plan.model_copy(update={"assignments": [assignment]})
        activation.services._packet_router = packet_router(settings)
        activation.services.register(record)
        stored = activation.services._record(task_id)
        stored_assignment = stored.plan.assignments[0]
        assignment_id = activation.services._packet_router._assignment_id(stored, stored_assignment)
        packet = validate_packet(
            "work", activation.services._packet_router.planned_work_packet(stored, stored_assignment)
        )
        return assignment_id, stored_assignment.sequence, packet

    def _reserve_ready(self, activation, *, task_id, provider, assignment_id, sequence):
        writer = self.handle.claim_store.acquire_task_writer(
            task_id, engine_owner="langgraph", owner_token="synthetic", expected_generation=0
        )
        self.ledger.register_plan(
            task_id=task_id, writer=writer, policy=self.policy, plan_digest=digest(task_id),
            assignments=(ParallelAssignment(assignment_id, sequence, provider, "test-deployment"),),
        )
        return self.ledger.reserve_ready(
            task_id=task_id, writer=writer, policy=self.policy,
            preflight=activation.application._parallel_dispatcher.preflight,
        )

    def _assert_zero_effect_rows(self):
        with self.handle.pool.connection() as connection:
            for table in ("director_primary_provider_attempt", "director_primary_provider_spend",
                          "director_primary_provider_usage"):
                self.assertEqual(0, connection.execute(
                    f"SELECT count(*) AS n FROM {self.schema}.{table}").fetchone()["n"])

    def test_eligible_team_assignment_reaches_one_real_active_reservation(self):
        activation, settings, query_factory, worker_factory = self._build_activation(team_enabled=True)
        task_id = "DIR-PG-TEAM-OK-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, packet = self._register(
            activation, settings, provider="antigravity", agent=ProviderName.GEMINI, task_id=task_id)
        g = grant(task_id=task_id, assignment_id=assignment_id,
                   project_id=packet["project"], packet_digest=document_digest(packet))
        attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
        b = budget(g, provider_id="antigravity", project_id=packet["project"],
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
            daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000),
            attempt_reservation_microusd=int(
                (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_team_grant(grant_digest,document,budget_document) "
                "VALUES(%s,%s::jsonb,%s::jsonb)",
                (g.digest, g.model_dump_json(), b.model_dump_json()),
            )
        reservations = self._reserve_ready(
            activation, task_id=task_id, provider="antigravity", assignment_id=assignment_id, sequence=sequence)
        self.assertEqual(1, len(reservations))
        state = self.ledger.aggregate(task_id)
        self.assertEqual({"ACTIVE"}, set(state.statuses.values()))
        self.assertEqual((), state.setup_hold_reasons)
        self._assert_zero_effect_rows()
        query_factory.assert_not_called()
        worker_factory.assert_not_called()

    def test_unregistered_team_assignment_holds_the_whole_wave_pending(self):
        activation, settings, query_factory, worker_factory = self._build_activation(team_enabled=True)
        task_id = "DIR-PG-TEAM-HOLD-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, _packet = self._register(
            activation, settings, provider="antigravity", agent=ProviderName.GEMINI, task_id=task_id)
        reservations = self._reserve_ready(
            activation, task_id=task_id, provider="antigravity", assignment_id=assignment_id, sequence=sequence)
        self.assertEqual((), reservations)
        state = self.ledger.aggregate(task_id)
        self.assertEqual({"PENDING"}, set(state.statuses.values()))
        self.assertEqual(("paid_setup_required",), state.setup_hold_reasons)
        self._assert_zero_effect_rows()
        query_factory.assert_not_called()
        worker_factory.assert_not_called()

    def test_eligible_responses_assignment_reaches_one_real_active_reservation(self):
        activation, settings, _qf, _wf = self._build_activation(team_enabled=False)
        task_id = "DIR-PG-RESP-OK-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, packet = self._register(
            activation, settings, provider="openai", agent=ProviderName.OPENAI, task_id=task_id)
        day = datetime.now(timezone.utc).date().isoformat()
        attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
        b = budget(grant(task_id=task_id, project_id=packet["project"]),
            provider_id="openai", project_id=packet["project"], day=day,
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
            daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000),
            attempt_reservation_microusd=int(
                (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) "
                "VALUES(%s,%s::jsonb)", (b.grant_digest, b.model_dump_json()),
            )
        reservations = self._reserve_ready(
            activation, task_id=task_id, provider="openai", assignment_id=assignment_id, sequence=sequence)
        self.assertEqual(1, len(reservations))
        state = self.ledger.aggregate(task_id)
        self.assertEqual({"ACTIVE"}, set(state.statuses.values()))
        self.assertEqual((), state.setup_hold_reasons)
        self._assert_zero_effect_rows()

    def test_unregistered_responses_assignment_holds_the_whole_wave_pending(self):
        activation, settings, _qf, _wf = self._build_activation(team_enabled=False)
        task_id = "DIR-PG-RESP-HOLD-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, _packet = self._register(
            activation, settings, provider="openai", agent=ProviderName.OPENAI, task_id=task_id)
        reservations = self._reserve_ready(
            activation, task_id=task_id, provider="openai", assignment_id=assignment_id, sequence=sequence)
        self.assertEqual((), reservations)
        state = self.ledger.aggregate(task_id)
        self.assertEqual({"PENDING"}, set(state.statuses.values()))
        self.assertEqual(("paid_setup_required",), state.setup_hold_reasons)
        self._assert_zero_effect_rows()

    def test_lock_contention_past_the_deadline_cancels_admission_without_commit(self):
        """A real second connection holds the exact row lock atomic admission
        needs; `reserve_provider_attempt`'s carried deadline cancels the wait
        through Postgres's own `lock_timeout`, bounded and with zero
        attempt/spend/workload rows - not a hang, and not a silent commit
        past expiry. Real lock witness, real elapsed bound, real rollback.
        """
        from time import monotonic as _monotonic
        activation, settings, _qf, _wf = self._build_activation(team_enabled=False)
        task_id = "DIR-PG-RESP-LOCK-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, packet = self._register(
            activation, settings, provider="openai", agent=ProviderName.OPENAI, task_id=task_id)
        day = datetime.now(timezone.utc).date().isoformat()
        attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
        b = budget(grant(task_id=task_id, project_id=packet["project"]),
            provider_id="openai", project_id=packet["project"], day=day,
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
            daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000),
            attempt_reservation_microusd=int(
                (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) "
                "VALUES(%s,%s::jsonb)", (b.grant_digest, b.model_dump_json()))
        reservations = self._reserve_ready(
            activation, task_id=task_id, provider="openai", assignment_id=assignment_id, sequence=sequence)
        self.assertEqual(1, len(reservations))
        writer = self.handle.claim_store.current_task_writer(task_id)
        authority = PrimaryProviderBudgetAuthority(self.entitlements, primary_schema=self.schema)
        limits = ProviderAttemptBudget.from_usd(project_id=packet["project"], attempt_usd=attempt_usd,
            task_usd=settings.max_task_budget_usd, daily_usd=settings.max_daily_provider_budget_usd)
        # The real row `reserve_provider_attempt` must `SELECT ... FOR UPDATE`
        # (`director_primary_parallel_assignment`), held open in a second real
        # connection for the whole assertion - a genuine lock witness, not a
        # simulated delay.
        with self.handle.pool.connection() as blocker, blocker.transaction():
            blocker.execute(
                f"SELECT * FROM {self.schema}.director_primary_parallel_assignment "
                "WHERE task_id=%s AND assignment_id=%s FOR UPDATE", (task_id, assignment_id))
            started = _monotonic()
            with self.assertRaises(Exception):
                self.ledger.reserve_provider_attempt(
                    reservation=reservations[0], writer=writer, attempt_ordinal=1,
                    format_roster=(("model-work-packet-v1", "responses-api"),),
                    idempotency_key_digest=digest("idem"),
                    budget_evidence_digest="sha256:" + "0" * 64,
                    budget_admission=ProviderBudgetAdmission(authority, b.grant_digest),
                    budget_limits=limits,
                    deadline=_monotonic() + 0.4,
                )
            elapsed = _monotonic() - started
        self.assertLess(elapsed, 3.0, "the lock wait must be cancelled near the deadline, not hang")
        self.assertGreaterEqual(elapsed, 0.3, "the deadline must be honored, not refused instantly")
        with self.handle.pool.connection() as connection:
            for table in ("director_primary_provider_attempt", "director_primary_provider_spend",
                          "director_primary_workload_assignment"):
                self.assertEqual(0, connection.execute(
                    f"SELECT count(*) AS n FROM {self.schema}.{table}").fetchone()["n"])

    def test_insert_and_deferred_commit_share_one_total_deadline(self):
        """Regression for an independently reproduced late admission.

        Two real server-side delays, each shorter than the supplied budget but
        together longer, one in a BEFORE INSERT trigger and one in a DEFERRABLE
        INITIALLY DEFERRED constraint trigger that runs only during COMMIT.
        Per-statement timeouts alone let this commit successfully after its
        absolute deadline (observed: 0.656s against a 0.5s bound, with attempt,
        spend and workload rows all present). One transaction-level bound must
        refuse it and leave nothing behind.
        """
        from time import monotonic as _monotonic
        activation, settings, _qf, _wf = self._build_activation(team_enabled=False)
        task_id = "DIR-PG-RESP-COMMIT-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, packet = self._register(
            activation, settings, provider="openai", agent=ProviderName.OPENAI, task_id=task_id)
        attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
        b = budget(grant(task_id=task_id, project_id=packet["project"]),
            provider_id="openai", project_id=packet["project"],
            day=datetime.now(timezone.utc).date().isoformat(),
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
            daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000),
            attempt_reservation_microusd=int(
                (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) "
                "VALUES(%s,%s::jsonb)", (b.grant_digest, b.model_dump_json()))
            connection.execute(
                f"""CREATE FUNCTION {self.schema}.deadline_probe_delay() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.3); RETURN NEW; END $$""")
            connection.execute(
                f"""CREATE TRIGGER before_attempt_delay BEFORE INSERT ON
                {self.schema}.director_primary_provider_attempt FOR EACH ROW
                EXECUTE FUNCTION {self.schema}.deadline_probe_delay()""")
            connection.execute(
                f"""CREATE CONSTRAINT TRIGGER commit_attempt_delay AFTER INSERT ON
                {self.schema}.director_primary_provider_attempt DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION {self.schema}.deadline_probe_delay()""")
        reservations = self._reserve_ready(
            activation, task_id=task_id, provider="openai", assignment_id=assignment_id, sequence=sequence)
        self.assertEqual(1, len(reservations))
        writer = self.handle.claim_store.current_task_writer(task_id)
        authority = PrimaryProviderBudgetAuthority(self.entitlements, primary_schema=self.schema)
        limits = ProviderAttemptBudget.from_usd(project_id=packet["project"], attempt_usd=attempt_usd,
            task_usd=settings.max_task_budget_usd, daily_usd=settings.max_daily_provider_budget_usd)
        started = _monotonic()
        with self.assertRaises(Exception):
            self.ledger.reserve_provider_attempt(
                reservation=reservations[0], writer=writer, attempt_ordinal=1,
                format_roster=(("model-work-packet-v1", "responses-api"),),
                idempotency_key_digest=digest("idempotency"),
                budget_evidence_digest="sha256:" + "0" * 64,
                budget_admission=ProviderBudgetAdmission(authority, b.grant_digest),
                budget_limits=limits, deadline=started + 0.5)
        elapsed = _monotonic() - started
        with self.handle.pool.connection() as connection:
            counts = {name: connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}.{name}").fetchone()["n"]
                for name in ("director_primary_provider_attempt",
                             "director_primary_provider_spend",
                             "director_primary_workload_assignment")}
        self.assertEqual({name: 0 for name in counts}, counts,
                          "expired admission must roll back monetary, workload and STARTED together")
        self.assertLess(elapsed, 3.0, "the bound must end the transaction, not merely be recorded")


class RealResponsesTransportCompositionTests(unittest.TestCase):
    """The real `OpenAIResponsesPacketTransport`, a real signed
    `ResponseMeterRegistry`, and the real attempt-admission code (the real
    `PrimaryTaskCoordinator`, delegating `reserve_provider_attempt`/
    `record_provider_attempt` to the real `PostgresParallelDispatch`, not a
    graph/checkpoint stand-in), through a real disposable PostgreSQL schema.
    No placeholder transport and no placeholder admission for the boundary
    under test; only the HTTP transport itself is synthetic
    (`httpx.MockTransport`, no network). Proves - empirically, not by
    inspection - that the attempt identity readiness predicts is the exact
    one real admission commits, and that readiness canonicalizes the packet
    the same way real dispatch does.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for these tests")

    def setUp(self) -> None:
        self.schema = f"director_responses_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(
            TEST_DSN, schema=self.schema, allow_insecure_local_transport=True,
        )
        with self.handle.pool.connection() as connection, connection.transaction():
            for statement in team_ddl(self.schema):
                connection.execute(statement)
        self.entitlements = SimpleNamespace(
            _owner_authority_secret=fixtures.OWNER_SECRET,
            _route_verifier_secret=fixtures.ROUTE_SECRET,
            _clock=lambda: datetime.now(timezone.utc),
            _pool=self.handle.pool,
        )
        self.authority = PrimaryProviderBudgetAuthority(self.entitlements, primary_schema=self.schema)
        self.ledger = PostgresParallelDispatch(self.handle.claim_store)
        self.policy = ParallelDispatchPolicy(max_concurrency=2)

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    def _build_activation(self, transport):
        registry = ExactEffectGatewayRegistry(
            (SimpleNamespace(system="slack", action=SLACK_POST_MESSAGE,
                mutate=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")),
                readback=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect"))),
             SimpleNamespace(system="github", action=GITHUB_MARK_READY,
                mutate=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")),
                readback=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")))),
            allowed_actions=frozenset({("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}),
        )
        settings = replace(
            Settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_live_provider_dispatch_enabled=True,
            langgraph_primary_antigravity_team_enabled=False,
            allow_paid_runs=True,
            langgraph_primary_owner_token="o" * 32,
            repository_allowlist=("owner/repository",),
            allowed_starting_refs=("main",),
            repository_snapshot_root=str(Path.cwd()),
        )
        budget_routing = PrimaryProviderBudgetRouting(authority=self.authority, pool=self.handle.pool)
        adapter = PacketBoundaryAdapter("openai", transport)
        adapters = {"openai": adapter}
        adapters.update({provider: PacketBoundaryAdapter(
                provider, SimpleNamespace(dispatch=lambda *_args, **_kwargs: {}))
            for provider in ("claude", "cursor")})
        dependencies = SimpleNamespace(
            team_routing=None, budget_routing=budget_routing,
            validate=lambda _required: None,
            adapters=adapters,
            packet_result_resolver=lambda _context: None,
            completion_evidence_resolver=lambda _context: None,
            external_effect_resolver=lambda _context: None,
            evidence_debate_handler=lambda _context: None,
            acceptance_handler=lambda _context: None,
            candidate_sha_resolver=lambda *_args: SHA,
            format_roster=lambda _provider: (("model-work-packet-v1", "responses-api"),),
            adapter_for_format=lambda provider, _format_transport: adapters[provider],
        )
        provider_activation = SimpleNamespace(
            health=lambda: {"combined_live_ready": True, "core_live_ready": True},
            provider_ready=lambda *_args, **_kwargs: True,
        )

        # The real `PrimaryTaskCoordinator` (not patched): its
        # `reserve_provider_attempt`/`record_provider_attempt` resolve the
        # current writer through `_task_writer` (engine/owner validation,
        # live-lease renewal, exact-generation reacquisition on an expired
        # row) and delegate to the real `PostgresParallelDispatch` - the
        # actual constructor/coordinator/admission composition, not a
        # substitute that only reads the current writer.
        with (
            patch("director_orchestrator.primary_default.resolve_default_primary_authority",
                  return_value=DefaultPrimaryAuthority(SHA, digest("authority"))),
            patch("director_orchestrator.primary_default.open_postgres_control_plane", return_value=self.handle),
            patch.object(Settings, "primary_provider_activation_bindings", return_value={"openai": object()}),
            patch("director_orchestrator.primary_default.PrimaryProviderActivationService",
                  return_value=provider_activation),
        ):
            activation = DefaultPrimaryActivation(settings, live_dependencies=dependencies, effect_gateways=registry)
        return activation, settings

    def _register(self, activation, settings, *, task_id):
        record = TaskRecord(task_id=task_id, status=TaskStatus.QUEUED,
            intake=SlackIntake(source_event_id=task_id, channel_id="C123", user_id="U123",
                                text="Ship a bounded change", thread_ts="1.0"))
        plan = preview_plan(record.intake.text, settings)
        assignment = plan.assignments[0].model_copy(update={"agent": ProviderName.OPENAI,
            "primary_provider_lane": "openai", "estimated_cost_usd": 1.0})
        record.plan = plan.model_copy(update={"assignments": [assignment]})
        activation.services._packet_router = packet_router(settings)
        activation.services.register(record)
        stored = activation.services._record(task_id)
        stored_assignment = stored.plan.assignments[0]
        assignment_id = activation.services._packet_router._assignment_id(stored, stored_assignment)
        packet = validate_packet(
            "work", activation.services._packet_router.planned_work_packet(stored, stored_assignment)
        )
        return assignment_id, stored_assignment.sequence, packet

    def test_readiness_predicts_the_exact_admitted_attempt_and_dispatch_settles(self):
        task_id = "DIR-PG-RESP-REAL-" + uuid.uuid4().hex[:8].upper()
        api_key, model = "test-secret-key", "configured-model"
        transport = OpenAIResponsesPacketTransport(api_key=api_key, model=model, response_meter=None)
        activation, settings = self._build_activation(transport)
        assignment_id, sequence, packet = self._register(activation, settings, task_id=task_id)

        day = datetime.now(timezone.utc).date().isoformat()
        attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
        signed_budget = budget(grant(task_id=task_id, project_id=packet["project"]),
            provider_id="openai", project_id=packet["project"], day=day, token_limit=1000,
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
            daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000),
            attempt_reservation_microusd=int(
                (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) "
                "VALUES(%s,%s::jsonb)", (signed_budget.grant_digest, signed_budget.model_dump_json()))

        roster = (("model-work-packet-v1", "responses-api"),)
        idem = provider_attempt_idempotency(packet["idempotency_key"], 1, roster)
        # Same formula readiness now uses and real admission's override
        # produces (`"sha256:" + budget_admission.authority_digest`, and
        # `authority_digest` is the exact same signed-budget row's digest):
        # not asserted by inspection, but the actual identity a real signed
        # enrollment is registered under and must survive real admission.
        expected_attempt_digest = provider_attempt_digest(task_id=task_id, assignment_id=assignment_id,
            attempt_ordinal=1, provider_id="openai", format_id=roster[0][0], transport_id=roster[0][1],
            idempotency_key_digest=idem, budget_evidence_digest="sha256:" + signed_budget.grant_digest)

        organization_id, api_project_id = "org-synthetic", "proj-synthetic"
        enrollment = signed(ResponseMeterEnrollment(
            schema_version="responses-meter-enrollment/v1",
            authority_digest=signed_budget.grant_digest,
            attempt_digest=expected_attempt_digest,
            project_id=packet["project"], task_id=task_id, assignment_id=assignment_id,
            work_packet_digest=packet_document_digest(packet),
            credential_digest=hashlib.sha256(api_key.encode()).hexdigest(),
            organization_id=organization_id, api_project_id=api_project_id,
            model=model, service_tier="default", origin="https://api.openai.com",
            tariff_id="tariff-synthetic", input_microusd_per_million=1000,
            cached_input_microusd_per_million=500, output_microusd_per_million=2000,
            valid_from=datetime.now(timezone.utc) - timedelta(minutes=5),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            owner_signature="0" * 64,
        ), fixtures.OWNER_SECRET)
        # Set after construction: the registry can only be built once the
        # exact predicted attempt digest is known, but the transport's own
        # constructor requires no meter to exist yet - this is not a
        # different transport, only a later-configured one.
        transport.response_meter = ResponseMeterRegistry(
            enrollments=(enrollment,), authority=self.authority, pool=self.handle.pool)

        writer = self.handle.claim_store.acquire_task_writer(
            task_id, engine_owner="langgraph", owner_token=settings.langgraph_primary_owner_token,
            expected_generation=0)
        self.ledger.register_plan(
            task_id=task_id, writer=writer, policy=self.policy, plan_digest=digest(task_id),
            assignments=(ParallelAssignment(assignment_id, sequence, "openai", "test-deployment"),),
        )

        count_reply = {"object": "response.input_tokens", "input_tokens": 100}
        main_reply = {"object": "response", "id": "resp-real-openai", "status": "completed",
            "model": model, "service_tier": "default",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "synthetic bounded evidence"}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}}}
        seen = []

        def handle_request(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(200, json=count_reply)
            return httpx.Response(200, json=main_reply, headers={
                "openai-organization": organization_id, "openai-project": api_project_id,
                "x-request-id": "req-synthetic-1"})

        # `reserve_ready` (real readiness) runs first, on its own - proving it
        # makes no HTTP call at all, not merely that the *combined* call count
        # is bounded. Dispatch is then invoked directly rather than through
        # `execute_ready`'s ThreadPoolExecutor, so a synchronous mocked
        # `httpx.AsyncClient` cannot straddle a cross-thread event loop; the
        # dispatcher, admission and transport underneath are unchanged either
        # way - only which thread calls them differs.
        reservations = self.ledger.reserve_ready(
            task_id=task_id, writer=writer, policy=self.policy,
            preflight=activation.application._parallel_dispatcher.preflight,
        )
        self.assertEqual(1, len(reservations))
        self.assertEqual([], seen, "readiness must not have made any HTTP call itself")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
        with patch("director_orchestrator.primary_transports.httpx.AsyncClient", return_value=client), \
             patch("director_orchestrator.primary_transports.httpx.Client",
                   side_effect=AssertionError("no unmocked synchronous I/O")):
            outcome = activation.application._parallel_dispatcher(reservations[0])
        self.ledger.record_result(reservation=reservations[0], writer=writer,
            status=outcome.status, result_digest=outcome.result_digest, reason_code=outcome.reason_code)

        self.assertEqual(2, len(seen))
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, outcome.status,
                          f"dispatch outcome: {outcome.status} / {outcome.reason_code}")
        state = self.ledger.aggregate(task_id)
        self.assertEqual({"SUCCEEDED"}, set(state.statuses.values()))
        with self.handle.pool.connection() as connection:
            # An append-only event log (`event_sequence`), not a mutable row:
            # the STARTED row from admission and the SUCCEEDED row from
            # settlement both exist; the latest event is the current state.
            attempt_row = connection.execute(
                f"SELECT attempt_digest,status FROM {self.schema}.director_primary_provider_attempt "
                "WHERE task_id=%s ORDER BY event_sequence DESC LIMIT 1", (task_id,)).fetchone()
            self.assertEqual("SUCCEEDED", attempt_row["status"])
            self.assertEqual(expected_attempt_digest, attempt_row["attempt_digest"])
            usage_count = connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_usage").fetchone()["n"]
            self.assertEqual(1, usage_count)

    # --- the selection must still hold at admission and before I/O ---

    def _ready_wave(self):
        """Real readiness through real `reserve_ready`, ready to dispatch."""

        transport = OpenAIResponsesPacketTransport(
            api_key="test-secret-key", model="configured-model", response_meter=None)
        activation, settings = self._build_activation(transport)
        task_id = "DIR-PG-SEL-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, packet = self._register(activation, settings, task_id=task_id)
        attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
        signed_budget = budget(grant(task_id=task_id, project_id=packet["project"]),
            provider_id="openai", project_id=packet["project"], token_limit=1000,
            day=datetime.now(timezone.utc).date().isoformat(),
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
            daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000),
            attempt_reservation_microusd=int(
                (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) "
                "VALUES(%s,%s::jsonb)", (signed_budget.grant_digest, signed_budget.model_dump_json()))
        writer = self.handle.claim_store.acquire_task_writer(
            task_id, engine_owner="langgraph", owner_token=settings.langgraph_primary_owner_token,
            expected_generation=0)
        self.ledger.register_plan(task_id=task_id, writer=writer, policy=self.policy,
            plan_digest=digest(task_id),
            assignments=(ParallelAssignment(assignment_id, sequence, "openai", "test-deployment"),))
        reservations = self.ledger.reserve_ready(task_id=task_id, writer=writer, policy=self.policy,
            preflight=activation.application._parallel_dispatcher.preflight)
        self.assertEqual(1, len(reservations))
        return SimpleNamespace(activation=activation, settings=settings, task_id=task_id,
                                assignment_id=assignment_id, packet=packet, writer=writer,
                                reservation=reservations[0], budget=signed_budget)

    def _assert_no_admission_rows(self):
        with self.handle.pool.connection() as connection:
            for table in ("director_primary_provider_attempt", "director_primary_provider_spend",
                          "director_primary_workload_assignment"):
                self.assertEqual(0, connection.execute(
                    f"SELECT count(*) AS n FROM {self.schema}.{table}").fetchone()["n"])

    def test_dependency_enriched_packet_after_readiness_fails_closed(self):
        """The packet readiness checked is the only packet it authorised."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._ready_wave()
        stored = wave.activation.services._record(wave.task_id)
        # A dependency outcome recorded after readiness enriches the planned
        # packet, so its digest is no longer the one that was checked.
        stored.plan.assignments[0].task = stored.plan.assignments[0].task + " (enriched)"
        with self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("readiness_selection_changed_before_admission", raised.exception.reason_code)
        self._assert_no_admission_rows()

    def test_writer_takeover_after_readiness_fails_closed(self):
        """A new fencing generation is a different selection, not a renewal."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._ready_wave()
        taken_over = self.handle.claim_store.acquire_task_writer(
            wave.task_id, engine_owner="langgraph",
            owner_token=wave.settings.langgraph_primary_owner_token,
            expected_generation=wave.writer.fencing_generation)
        self.assertGreater(taken_over.fencing_generation, wave.writer.fencing_generation)
        with self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("readiness_selection_changed_before_admission", raised.exception.reason_code)
        self._assert_no_admission_rows()

    def test_added_signed_budget_after_readiness_fails_closed(self):
        """Registry replacement is refused, not silently re-resolved.

        The signed budget rows are immutable by database guard, so a second
        valid row for the same task/provider/project/day is how the registry
        actually changes. Resolution is no longer unambiguous, and nothing is
        admitted against a guess.
        """
        wave = self._ready_wave()
        added = budget(grant(task_id=wave.task_id, project_id=wave.packet["project"],
                              grant_id="grant-two"),
            provider_id="openai", project_id=wave.packet["project"], token_limit=999,
            day=datetime.now(timezone.utc).date().isoformat(),
            task_limit_microusd=wave.budget.task_limit_microusd,
            daily_limit_microusd=wave.budget.daily_limit_microusd,
            attempt_reservation_microusd=wave.budget.attempt_reservation_microusd)
        self.assertNotEqual(wave.budget.grant_digest, added.grant_digest)
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) "
                "VALUES(%s,%s::jsonb)", (added.grant_digest, added.model_dump_json()))
        with self.assertRaisesRegex(TeamAuthorizationError, "one authoritative daily task budget"):
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self._assert_no_admission_rows()

    def test_admission_refuses_an_authority_other_than_the_checked_one(self):
        """The comparison lives inside the authoritative locked admission.

        Even handed a perfectly valid admission object, the ledger refuses to
        spend against an authority the caller did not check, before any
        monetary, workload or STARTED row is written.
        """
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._ready_wave()
        with self.assertRaises(WorkloadAdmissionHold) as raised:
            self.ledger.reserve_provider_attempt(
                reservation=wave.reservation, writer=wave.writer, attempt_ordinal=1,
                format_roster=(("model-work-packet-v1", "responses-api"),),
                idempotency_key_digest=digest("idem"),
                budget_evidence_digest="sha256:" + wave.budget.grant_digest,
                budget_admission=ProviderBudgetAdmission(self.authority, wave.budget.grant_digest),
                budget_limits=ProviderAttemptBudget.from_usd(
                    project_id=wave.packet["project"],
                    attempt_usd=float(wave.packet["limits"]["budget_usd"]) / 3.0,
                    task_usd=wave.settings.max_task_budget_usd,
                    daily_usd=wave.settings.max_daily_provider_budget_usd),
                expected_authority_digest="b" * 64)
        self.assertEqual("provider_attempt_authority_changed_before_admission",
                          raised.exception.reason_code)
        self._assert_no_admission_rows()

    def test_dispatch_without_this_process_readiness_fails_closed(self):
        """A lost selection fails closed; it is never re-derived."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._ready_wave()
        # Exactly what a restart between reservation and dispatch leaves
        # behind: a committed ACTIVE reservation and no live selection.
        wave.activation.application._parallel_dispatcher.preflight  # attached, but not re-run
        restarted, _settings = self._build_activation(
            OpenAIResponsesPacketTransport(api_key="test-secret-key", model="configured-model"))
        restarted.services._packet_router = wave.activation.services._packet_router
        restarted.services.register(wave.activation.services._record(wave.task_id))
        with self.assertRaises(WorkloadAdmissionHold) as raised:
            restarted.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("readiness_selection_unavailable_before_admission",
                          raised.exception.reason_code)
        self._assert_no_admission_rows()

    def test_readiness_lock_wait_is_bounded_before_any_reservation(self):
        """`reserve_ready`'s own acquisition and locks are inside the bound.

        A second real connection holds the batch row readiness must lock. The
        wave-level deadline the dispatcher publishes is established before any
        acquisition, so the wait ends instead of blocking, and nothing is
        reserved.
        """
        from time import monotonic as _monotonic
        transport = OpenAIResponsesPacketTransport(
            api_key="test-secret-key", model="configured-model", response_meter=None)
        activation, settings = self._build_activation(transport)
        task_id = "DIR-PG-READY-LOCK-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, _packet = self._register(activation, settings, task_id=task_id)
        writer = self.handle.claim_store.acquire_task_writer(
            task_id, engine_owner="langgraph", owner_token=settings.langgraph_primary_owner_token,
            expected_generation=0)
        self.ledger.register_plan(task_id=task_id, writer=writer, policy=self.policy,
            plan_digest=digest(task_id),
            assignments=(ParallelAssignment(assignment_id, sequence, "openai", "test-deployment"),))
        dispatcher = activation.application._parallel_dispatcher
        self.assertTrue(callable(getattr(dispatcher, "readiness_deadline", None)),
                        "the dispatcher must publish the bound readiness works under")
        with self.handle.pool.connection() as blocker, blocker.transaction():
            blocker.execute(
                f"SELECT * FROM {self.schema}.director_primary_parallel_batch "
                "WHERE task_id=%s FOR UPDATE", (task_id,))
            started = _monotonic()
            with self.assertRaises(Exception):
                self.ledger.reserve_ready(task_id=task_id, writer=writer, policy=self.policy,
                                          preflight=dispatcher.preflight,
                                          deadline=_monotonic() + 0.4)
            elapsed = _monotonic() - started
        self.assertLess(elapsed, 5.0, "readiness must not wait past its bound")
        state = self.ledger.aggregate(task_id)
        self.assertEqual({"PENDING"}, set(state.statuses.values()))
        self._assert_no_admission_rows()

    def test_coordinator_writer_renewal_runs_inside_the_admission_bound(self):
        """The real coordinator's own writer work is bounded too.

        `_task_writer` reads, renews or reacquires a lease before the ledger's
        transaction opens. With the writer row held by a second connection, an
        already-bounded admission ends rather than waiting past its deadline.
        """
        from time import monotonic as _monotonic
        wave = self._ready_wave()
        coordinator = wave.activation.coordinator
        with self.handle.pool.connection() as blocker, blocker.transaction():
            blocker.execute(
                f"SELECT * FROM {self.schema}.director_primary_task_writer "
                "WHERE task_id=%s FOR UPDATE", (wave.task_id,))
            started = _monotonic()
            with self.assertRaises(Exception):
                coordinator.reserve_provider_attempt(
                    reservation=wave.reservation, attempt_ordinal=1,
                    format_roster=(("model-work-packet-v1", "responses-api"),),
                    idempotency_key_digest=digest("idem"),
                    budget_evidence_digest="sha256:" + wave.budget.grant_digest,
                    budget_admission=ProviderBudgetAdmission(self.authority, wave.budget.grant_digest),
                    budget_limits=ProviderAttemptBudget.from_usd(
                        project_id=wave.packet["project"],
                        attempt_usd=float(wave.packet["limits"]["budget_usd"]) / 3.0,
                        task_usd=wave.settings.max_task_budget_usd,
                        daily_usd=wave.settings.max_daily_provider_budget_usd),
                    deadline=_monotonic() + 0.4)
            elapsed = _monotonic() - started
        self.assertLess(elapsed, 5.0, "writer renewal must be inside the bound, not ahead of it")
        self._assert_no_admission_rows()

    # --- late accounting: authenticated usage of an admitted, sent request ---

    def _admitted_meter(self, *, expires_in_seconds=3600.0, clock=None):
        """Admit one real attempt and return its meter, response and facts.

        Everything is real except the provider reply: a real signed budget,
        real `reserve_ready`, real `reserve_provider_attempt` (so a real spend
        row exists), a real signed enrollment and the real meter.
        """
        task_id = "DIR-PG-LATE-" + uuid.uuid4().hex[:8].upper()
        api_key, model = "test-secret-key", "configured-model"
        transport = OpenAIResponsesPacketTransport(api_key=api_key, model=model, response_meter=None)
        activation, settings = self._build_activation(transport)
        assignment_id, sequence, packet = self._register(activation, settings, task_id=task_id)
        attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
        signed_budget = budget(grant(task_id=task_id, project_id=packet["project"]),
            provider_id="openai", project_id=packet["project"], token_limit=1000,
            day=datetime.now(timezone.utc).date().isoformat(),
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
            daily_limit_microusd=int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000),
            attempt_reservation_microusd=int(
                (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) "
                "VALUES(%s,%s::jsonb)", (signed_budget.grant_digest, signed_budget.model_dump_json()))
        roster = (("model-work-packet-v1", "responses-api"),)
        idem = provider_attempt_idempotency(packet["idempotency_key"], 1, roster)
        attempt_digest = provider_attempt_digest(task_id=task_id, assignment_id=assignment_id,
            attempt_ordinal=1, provider_id="openai", format_id=roster[0][0], transport_id=roster[0][1],
            idempotency_key_digest=idem, budget_evidence_digest="sha256:" + signed_budget.grant_digest)
        organization_id, api_project_id = "org-synthetic", "proj-synthetic"
        sent_at = datetime.now(timezone.utc)
        enrollment = signed(ResponseMeterEnrollment(
            schema_version="responses-meter-enrollment/v1",
            authority_digest=signed_budget.grant_digest, attempt_digest=attempt_digest,
            project_id=packet["project"], task_id=task_id, assignment_id=assignment_id,
            work_packet_digest=packet_document_digest(packet),
            credential_digest=hashlib.sha256(api_key.encode()).hexdigest(),
            organization_id=organization_id, api_project_id=api_project_id,
            model=model, service_tier="default", origin="https://api.openai.com",
            tariff_id="tariff-synthetic", input_microusd_per_million=1000,
            cached_input_microusd_per_million=500, output_microusd_per_million=2000,
            valid_from=sent_at - timedelta(minutes=5),
            expires_at=sent_at + timedelta(seconds=expires_in_seconds),
            owner_signature="0" * 64,
        ), fixtures.OWNER_SECRET)
        writer = self.handle.claim_store.acquire_task_writer(
            task_id, engine_owner="langgraph", owner_token=settings.langgraph_primary_owner_token,
            expected_generation=0)
        self.ledger.register_plan(task_id=task_id, writer=writer, policy=self.policy,
            plan_digest=digest(task_id),
            assignments=(ParallelAssignment(assignment_id, sequence, "openai", "test-deployment"),))
        transport.response_meter = ResponseMeterRegistry(
            enrollments=(enrollment,), authority=self.authority, pool=self.handle.pool)
        reservations = self.ledger.reserve_ready(task_id=task_id, writer=writer, policy=self.policy,
            preflight=activation.application._parallel_dispatcher.preflight)
        self.assertEqual(1, len(reservations))
        limits = ProviderAttemptBudget.from_usd(project_id=packet["project"], attempt_usd=attempt_usd,
            task_usd=settings.max_task_budget_usd, daily_usd=settings.max_daily_provider_budget_usd)
        self.ledger.reserve_provider_attempt(
            reservation=reservations[0], writer=writer, attempt_ordinal=1, format_roster=roster,
            idempotency_key_digest=idem, budget_evidence_digest="sha256:" + signed_budget.grant_digest,
            budget_admission=ProviderBudgetAdmission(self.authority, signed_budget.grant_digest),
            budget_limits=limits)
        meter = EnrolledResponseMeter(enrollment=enrollment, authority=self.authority,
            pool=self.handle.pool, clock=clock or (lambda: datetime.now(timezone.utc)))
        native = {"object": "response", "id": "resp-late-openai", "status": "completed",
            "model": model, "service_tier": "default",
            "created_at": int(sent_at.timestamp()),
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}}}
        headers = {"openai-organization": organization_id, "openai-project": api_project_id,
                   "x-request-id": "req-synthetic-late"}
        return SimpleNamespace(meter=meter, native=native, headers=headers, sent_at=sent_at,
                                attempt_digest=attempt_digest, enrollment=enrollment,
                                project_id=packet["project"])

    def _usage_rows(self):
        with self.handle.pool.connection() as connection:
            return connection.execute(
                f"SELECT attempt_digest, actual_microusd, observed_tokens "
                f"FROM {self.schema}.director_primary_provider_usage").fetchall()

    def test_expired_enrollment_still_records_the_usage_it_authorised(self):
        """The signed window closed while the provider was answering.

        The request was admitted and sent while the tariff applied, so the
        authenticated usage is recorded through the existing receipt path.
        Eligibility is unaffected: without the `sent_at` anchor the same call
        is refused exactly as before, and `prepare` - the gate that authorises
        *new* execution - still refuses outright.
        """
        expired = datetime.now(timezone.utc) + timedelta(hours=2)
        case = self._admitted_meter(expires_in_seconds=1.0, clock=lambda: expired)
        self.assertEqual([], self._usage_rows())
        receipt = case.meter.settle(case.native, case.headers, sent_at=case.sent_at)
        rows = self._usage_rows()
        self.assertEqual(1, len(rows))
        self.assertEqual(case.attempt_digest, rows[0]["attempt_digest"])
        self.assertEqual(150, rows[0]["observed_tokens"])
        self.assertEqual(receipt.actual_microusd, rows[0]["actual_microusd"])
        self.assertEqual("signed-tariff-metered-not-invoice",
                          receipt.metering_evidence.cost_basis)
        # Same facts, no anchor: the previous current-validity gate is intact.
        with self.assertRaisesRegex(TeamAuthorizationError, "not current"):
            case.meter.settle(case.native, case.headers)
        # And an expired enrollment still authorises no new execution.
        with self.assertRaisesRegex(TeamAuthorizationError, "not current"):
            case.meter.prepare(context=SimpleNamespace(
                task_id="x", assignment_id="x", idempotency_key="x", work_packet_digest="x"),
                packet=b"{}", api_url="https://api.openai.com/v1/responses",
                model=case.enrollment.model, api_key="test-secret-key")

    def test_late_settlement_keeps_replay_account_and_signature_checks(self):
        """A late receipt is not a relaxed one."""
        expired = datetime.now(timezone.utc) + timedelta(hours=2)
        case = self._admitted_meter(expires_in_seconds=1.0, clock=lambda: expired)
        case.meter.settle(case.native, case.headers, sent_at=case.sent_at)
        self.assertEqual(1, len(self._usage_rows()))
        # Exact replay is idempotent: still one row, same amounts.
        case.meter.settle(case.native, case.headers, sent_at=case.sent_at)
        rows = self._usage_rows()
        self.assertEqual(1, len(rows))
        # A conflicting replay of the same attempt is refused, and the
        # original recorded facts are left exactly as they were.
        conflicting = dict(case.native)
        conflicting["usage"] = dict(case.native["usage"],
                                    output_tokens=51, total_tokens=151)
        with self.assertRaisesRegex(TeamAuthorizationError, "conflicting usage receipt replay"):
            case.meter.settle(conflicting, case.headers, sent_at=case.sent_at)
        self.assertEqual(rows, self._usage_rows())
        # An unconfirmed account association is refused before any write.
        other = self._admitted_meter(expires_in_seconds=1.0, clock=lambda: expired)
        with self.assertRaisesRegex(TeamAuthorizationError, "account association"):
            other.meter.settle(other.native,
                                dict(other.headers, **{"openai-organization": "org-other"}),
                                sent_at=other.sent_at)
        self.assertEqual(1, len(self._usage_rows()))

    def test_late_settlement_under_project_lock_contention_retains_reservation(self):
        """Its bounded allowance is a bound, not a licence to wait forever."""
        from time import monotonic as _monotonic
        expired = datetime.now(timezone.utc) + timedelta(hours=2)
        case = self._admitted_meter(expires_in_seconds=1.0, clock=lambda: expired)
        with self.handle.pool.connection() as blocker, blocker.transaction():
            blocker.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            ("primary-provider-budget:" + case.project_id,))
            started = _monotonic()
            with self.assertRaises(RuntimeError):
                case.meter.settle(case.native, case.headers, sent_at=case.sent_at,
                                  remaining_seconds=lambda: 0.3)
            elapsed = _monotonic() - started
        self.assertLess(elapsed, 8.0)
        self.assertEqual([], self._usage_rows())
        with self.handle.pool.connection() as connection:
            held = connection.execute(
                f"SELECT reserved_microusd FROM {self.schema}.director_primary_provider_spend "
                "WHERE attempt_digest=%s", (case.attempt_digest,)).fetchone()
        self.assertIsNotNone(held, "a failed settlement never releases the reservation")
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:18 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]]
