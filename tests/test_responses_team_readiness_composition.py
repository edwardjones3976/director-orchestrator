# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:25 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:07 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | BEGIN]]
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

import asyncio
import os
import unittest
import uuid
from dataclasses import replace
from tempfile import TemporaryDirectory
from time import monotonic as real_monotonic
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

from director_orchestrator.packet_contracts import canonical_json_bytes
from director_orchestrator.packet_contracts import document_digest as packet_document_digest
from director_orchestrator.primary_parallel import provider_attempt_digest, provider_attempt_idempotency
from director_orchestrator.primary_transports import (
    NativeCommandPacketTransport,
    OpenAIResponsesPacketTransport,
)
from director_orchestrator.response_meter import (
    EnrolledResponseMeter,
    ResponseMeterEnrollment,
    ResponseMeterRegistry,
)

from tests.test_antigravity_team import budget, fixtures, grant, signed
from tests.test_primary_default import SHA, digest, packet_router
from tests.test_primary_transports import LAUNCH_SEAM, NATIVE_ENVELOPE, _FakeLaunch

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

    def _build_activation(self, transport, *, provider_timeout_seconds=None):
        registry = ExactEffectGatewayRegistry(
            (SimpleNamespace(system="slack", action=SLACK_POST_MESSAGE,
                mutate=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")),
                readback=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect"))),
             SimpleNamespace(system="github", action=GITHUB_MARK_READY,
                mutate=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")),
                readback=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no effect")))),
            allowed_actions=frozenset({("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}),
        )
        bound = ({} if provider_timeout_seconds is None
                 else {"provider_timeout_seconds": provider_timeout_seconds})
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
            **bound,
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

    def _ready_wave(self, *, transport=None, provider_timeout_seconds=None):
        """Real readiness through real `reserve_ready`, ready to dispatch.

        `transport` defaults to the real Responses transport. A command
        transport can be placed under the same constructor-created adapter
        instead: the dispatcher, coordinator, admission and the context they
        build are then identical, and only the raw transport at the end of
        that path differs.
        """

        transport = transport or OpenAIResponsesPacketTransport(
            api_key="test-secret-key", model="configured-model", response_meter=None)
        activation, settings = self._build_activation(
            transport, provider_timeout_seconds=provider_timeout_seconds)
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

    def _enrolled_wave(self, *, daily_limit_ratio=None, provider_timeout_seconds=None):
        """`_ready_wave` plus the real signed enrollment for its first attempt.

        The registry is attached before readiness runs, so the selection
        records the exact enrollment document, and the same one must still
        resolve at admission and immediately before the provider call.
        """
        api_key, model = "test-secret-key", "configured-model"
        transport = OpenAIResponsesPacketTransport(api_key=api_key, model=model, response_meter=None)
        activation, settings = self._build_activation(
            transport, provider_timeout_seconds=provider_timeout_seconds)
        task_id = "DIR-PG-ENROL-" + uuid.uuid4().hex[:8].upper()
        assignment_id, sequence, packet = self._register(activation, settings, task_id=task_id)
        attempt_usd = float(packet["limits"]["budget_usd"]) / 3.0
        reservation_microusd = int(
            (Decimal(str(attempt_usd)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
        daily_limit_microusd = (
            int(reservation_microusd * daily_limit_ratio) if daily_limit_ratio is not None
            else int(Decimal(str(settings.max_daily_provider_budget_usd)) * 1_000_000))
        signed_budget = budget(grant(task_id=task_id, project_id=packet["project"]),
            provider_id="openai", project_id=packet["project"], token_limit=1000,
            day=datetime.now(timezone.utc).date().isoformat(),
            task_limit_microusd=int(Decimal(str(settings.max_task_budget_usd)) * 1_000_000),
            daily_limit_microusd=daily_limit_microusd,
            attempt_reservation_microusd=reservation_microusd)
        with self.handle.pool.connection() as connection, connection.transaction():
            connection.execute(
                f"INSERT INTO {self.schema}.director_primary_provider_budget(authority_digest,document) "
                "VALUES(%s,%s::jsonb)", (signed_budget.grant_digest, signed_budget.model_dump_json()))
        roster = (("model-work-packet-v1", "responses-api"),)
        idem = provider_attempt_idempotency(packet["idempotency_key"], 1, roster)
        attempt_digest = provider_attempt_digest(task_id=task_id, assignment_id=assignment_id,
            attempt_ordinal=1, provider_id="openai", format_id=roster[0][0], transport_id=roster[0][1],
            idempotency_key_digest=idem, budget_evidence_digest="sha256:" + signed_budget.grant_digest)
        sent_at = datetime.now(timezone.utc)
        enrollment = self._enrollment(attempt_digest=attempt_digest,
            authority_digest=signed_budget.grant_digest, packet=packet, task_id=task_id,
            assignment_id=assignment_id, sent_at=sent_at, api_key=api_key, model=model)
        transport.response_meter = ResponseMeterRegistry(
            enrollments=(enrollment,), authority=self.authority, pool=self.handle.pool)
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
                                reservation=reservations[0], budget=signed_budget,
                                transport=transport, enrollment=enrollment, sent_at=sent_at,
                                attempt_digest=attempt_digest, attempt_usd=attempt_usd,
                                api_key=api_key, model=model,
                                reservation_microusd=reservation_microusd,
                                daily_limit_microusd=daily_limit_microusd)

    def _replaced_enrollment(self, wave, **changes):
        """A second, individually valid enrollment for the same attempt."""

        replacement = self._enrollment(attempt_digest=wave.attempt_digest,
            authority_digest=wave.budget.grant_digest, packet=wave.packet,
            task_id=wave.task_id, assignment_id=wave.assignment_id, sent_at=wave.sent_at,
            api_key=wave.api_key, model=wave.model, **changes)
        self.assertNotEqual(document_digest(wave.enrollment.model_dump(mode="json")),
                            document_digest(replacement.model_dump(mode="json")))
        return ResponseMeterRegistry(enrollments=(replacement,), authority=self.authority,
                                     pool=self.handle.pool)

    def _no_provider_call(self):
        """Any HTTP at all from the real transport fails the test outright."""

        return patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                     side_effect=AssertionError("no provider call may be made"))

    # --- cancellation authority at admission and immediately before I/O ---

    def test_cancelled_wave_admits_nothing_and_calls_no_provider(self):
        """An engaged kill switch stops admission itself, not just dispatch.

        The switch is read inside the admission transaction, under the same
        row lock, so a cancellation racing an in-flight admission cannot leave
        an ACTIVE slot with a real STARTED attempt behind.
        """
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._ready_wave()
        self.ledger.engage_kill_switch(task_id=wave.task_id, writer=wave.writer)
        with self._no_provider_call(), self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("parallel_wave_cancelled_before_admission", raised.exception.reason_code)
        self._assert_no_admission_rows()

    def test_cancellation_after_admission_stops_the_send_and_keeps_the_evidence(self):
        """Cancelled between admission and I/O: no call, but nothing is erased.

        The switch is engaged by the real `engage_kill_switch` immediately
        after the real admission commits - "block new reservations without
        cancelling or freeing in-flight slots". The provider is never called,
        and the admitted monetary, workload and STARTED rows all remain for
        reconciliation rather than being silently rolled back.
        """
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._ready_wave()
        coordinator = wave.activation.coordinator
        admit = coordinator.reserve_provider_attempt

        def admit_then_cancel(**kwargs):
            attempt = admit(**kwargs)
            self.ledger.engage_kill_switch(task_id=wave.task_id, writer=wave.writer)
            return attempt

        with patch.object(coordinator, "reserve_provider_attempt", side_effect=admit_then_cancel), \
             self._no_provider_call(), \
             self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("wave_cancelled_before_dispatch", raised.exception.reason_code)
        with self.handle.pool.connection() as connection:
            counts = {name: connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}.{name}").fetchone()["n"]
                for name in ("director_primary_provider_attempt",
                             "director_primary_provider_spend",
                             "director_primary_workload_assignment")}
            usage = connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_usage"
            ).fetchone()["n"]
        self.assertEqual({name: 1 for name in counts}, counts,
                          "an admitted attempt keeps its rows, slot and evidence")
        self.assertEqual(0, usage, "nothing was sent, so nothing was metered")

    # --- authority at the real generation boundary, after token counting ---

    def _counting_provider(self, during_count):
        """A synthetic Responses endpoint that acts while counting input tokens.

        The count request is the one the provider can make the caller wait on
        before anything billable is sent. `during_count` runs at exactly that
        point; a generation request reaching this endpoint fails the test.
        """
        seen = []

        def handle_request(request):
            seen.append(str(request.url))
            if str(request.url).endswith("/input_tokens"):
                during_count()
                return httpx.Response(200, json={"object": "response.input_tokens",
                                                  "input_tokens": 100})
            raise AssertionError("a generation request must never be made")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
        self.addCleanup(lambda: asyncio.run(client.aclose()))
        return seen, patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                           return_value=client)

    def _assert_admitted_attempt_survives_unsent(self, wave, seen):
        """One count request, no generation, and the admitted facts intact."""

        self.assertEqual(1, len(seen), seen)
        with self.handle.pool.connection() as connection:
            statuses = [row["status"] for row in connection.execute(
                f"SELECT status FROM {self.schema}.director_primary_provider_attempt "
                "WHERE task_id=%s ORDER BY event_sequence", (wave.task_id,)).fetchall()]
            counts = tuple(connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}.{table}").fetchone()["n"]
                for table in ("director_primary_provider_spend",
                              "director_primary_workload_assignment",
                              "director_primary_provider_usage"))
        self.assertEqual(["STARTED"], statuses,
                          "the admitted attempt is preserved, not retried or overwritten")
        self.assertEqual((1, 1, 0), counts,
                          "its reservation and slot remain; nothing was generated or metered")

    def test_cancellation_during_token_counting_never_reaches_generation(self):
        """A real cancellation ordered while the count request is in flight.

        `coordinator.cancel` engages this same kill switch. Every earlier check
        ran before the transport started waiting on the provider, so the
        request would otherwise be sent against a cancelled wave.
        """
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._enrolled_wave()
        seen, mocked = self._counting_provider(
            lambda: self.ledger.engage_kill_switch(task_id=wave.task_id, writer=wave.writer))
        with mocked, self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("wave_cancelled_before_generation", raised.exception.reason_code)
        self._assert_admitted_attempt_survives_unsent(wave, seen)

    def test_writer_takeover_during_token_counting_never_reaches_generation(self):
        """A legitimate successor takes the writer while counting waits."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._enrolled_wave()

        def take_over():
            taken = self.handle.claim_store.acquire_task_writer(
                wave.task_id, engine_owner="langgraph",
                owner_token=wave.settings.langgraph_primary_owner_token,
                expected_generation=wave.writer.fencing_generation)
            self.assertGreater(taken.fencing_generation, wave.writer.fencing_generation)

        seen, mocked = self._counting_provider(take_over)
        with mocked, self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("writer_authority_changed_before_generation",
                          raised.exception.reason_code)
        self._assert_admitted_attempt_survives_unsent(wave, seen)

    def test_expired_writer_lease_during_token_counting_never_reaches_generation(self):
        """The lease simply runs out during the wait; no successor required."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._enrolled_wave()

        def expire():
            with self.handle.pool.connection() as connection, connection.transaction():
                connection.execute(
                    f"UPDATE {self.schema}.director_primary_task_writer "
                    "SET lease_expires_at = now() - interval '1 second' WHERE task_id=%s",
                    (wave.task_id,))

        seen, mocked = self._counting_provider(expire)
        with mocked, self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("writer_authority_changed_before_generation",
                          raised.exception.reason_code)
        self._assert_admitted_attempt_survives_unsent(wave, seen)

    def test_attempt_outcome_write_is_bounded_and_keeps_the_usage_it_recorded(self):
        """The attempt's own outcome write spends the completion allowance.

        The provider has answered and its usage is already metered when the
        writer row is held by a second connection. Recording the outcome
        therefore waits - inside the real coordinator's `_task_writer` and the
        ledger's own transaction - and ends at the completion cutoff rather
        than blocking the wave indefinitely. Nothing already recorded is lost.
        """
        from contextlib import ExitStack
        from time import monotonic as _monotonic
        from director_orchestrator.primary_parallel import COMPLETION_ALLOWANCE_SECONDS

        wave = self._enrolled_wave(provider_timeout_seconds=3)
        held = ExitStack()
        seen = []

        def handle_request(request):
            seen.append(str(request.url))
            if str(request.url).endswith("/input_tokens"):
                return httpx.Response(200, json={"object": "response.input_tokens",
                                                  "input_tokens": 100})
            # Taken once the generation request is answered: admission and the
            # provider call are done, so only the completion path can wait.
            connection = held.enter_context(self.handle.pool.connection())
            held.enter_context(connection.transaction())
            connection.execute(
                f"SELECT 1 FROM {self.schema}.director_primary_task_writer "
                "WHERE task_id=%s FOR UPDATE", (wave.task_id,))
            return httpx.Response(200, json={
                "object": "response", "id": "resp-completion-bound", "status": "completed",
                "model": wave.model, "service_tier": "default",
                "created_at": int(datetime.now(timezone.utc).timestamp()),
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "synthetic evidence"}]}],
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}}},
                headers={"openai-organization": "org-synthetic",
                         "openai-project": "proj-synthetic",
                         "x-request-id": "req-synthetic-completion"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
        self.addCleanup(lambda: asyncio.run(client.aclose()))
        started = _monotonic()
        try:
            with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                       return_value=client):
                with self.assertRaises(Exception):
                    wave.activation.application._parallel_dispatcher(wave.reservation)
            elapsed = _monotonic() - started
        finally:
            held.close()
        self.assertEqual(2, len(seen))
        self.assertLess(elapsed, 3 + COMPLETION_ALLOWANCE_SECONDS + 6.0,
                        "the allowance bounds the outcome write; it does not remove it")
        with self.handle.pool.connection() as connection:
            statuses = [row["status"] for row in connection.execute(
                f"SELECT status FROM {self.schema}.director_primary_provider_attempt "
                "WHERE task_id=%s ORDER BY event_sequence", (wave.task_id,)).fetchall()]
            usage = connection.execute(
                f"SELECT observed_tokens FROM {self.schema}.director_primary_provider_usage "
                "WHERE attempt_digest=%s", (wave.attempt_digest,)).fetchone()
        self.assertEqual(["STARTED"], statuses,
                          "the admitted attempt stays open for reconciliation")
        self.assertIsNotNone(usage, "authenticated usage already recorded is retained")
        self.assertEqual(150, usage["observed_tokens"])

    # --- the enrollment the selection checked is the only one that is used ---

    def test_enrollment_replaced_during_the_admission_wait_rolls_back(self):
        """Proved again after the locks, where rolling back is still possible.

        A second connection holds the exact batch row admission must lock, so
        the registry is replaced while admission is genuinely waiting - after
        every check `reserve_attempt` made before the transaction opened. The
        post-lock re-check refuses, and the monetary, workload and STARTED
        writes roll back together.
        """
        from concurrent.futures import ThreadPoolExecutor
        from time import sleep
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._enrolled_wave()
        dispatcher = wave.activation.application._parallel_dispatcher
        with ThreadPoolExecutor(max_workers=1) as pool, self._no_provider_call():
            with self.handle.pool.connection() as blocker, blocker.transaction():
                blocker.execute(
                    f"SELECT * FROM {self.schema}.director_primary_parallel_batch "
                    "WHERE task_id=%s FOR UPDATE", (wave.task_id,))
                admitting = pool.submit(dispatcher, wave.reservation)
                sleep(0.75)
                self.assertFalse(admitting.done(),
                                  "admission must still be waiting on the locked batch row")
                wave.transport.response_meter = self._replaced_enrollment(
                    wave, tariff_id="tariff-replaced")
            with self.assertRaises(WorkloadAdmissionHold) as raised:
                admitting.result(timeout=30)
        self.assertEqual("readiness_enrollment_changed_during_admission",
                          raised.exception.reason_code)
        self._assert_no_admission_rows()

    def test_competing_authority_registered_during_the_wait_rolls_back(self):
        """The authority is re-selected after the waits, not just compared.

        A second, individually valid signed budget for the same task, provider,
        project and day is registered through the real table while admission is
        genuinely blocked on the batch row. Resolution is no longer unambiguous,
        so the selection readiness made no longer holds and every write of this
        admission rolls back together.
        """
        from concurrent.futures import ThreadPoolExecutor
        from time import sleep
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._ready_wave()
        competing = budget(grant(task_id=wave.task_id, project_id=wave.packet["project"],
                                  grant_id="grant-competing"),
            provider_id="openai", project_id=wave.packet["project"], token_limit=999,
            day=datetime.now(timezone.utc).date().isoformat(),
            task_limit_microusd=wave.budget.task_limit_microusd,
            daily_limit_microusd=wave.budget.daily_limit_microusd,
            attempt_reservation_microusd=wave.budget.attempt_reservation_microusd)
        self.assertNotEqual(wave.budget.grant_digest, competing.grant_digest)
        dispatcher = wave.activation.application._parallel_dispatcher
        with ThreadPoolExecutor(max_workers=1) as pool, self._no_provider_call():
            with self.handle.pool.connection() as blocker, blocker.transaction():
                blocker.execute(
                    f"SELECT * FROM {self.schema}.director_primary_parallel_batch "
                    "WHERE task_id=%s FOR UPDATE", (wave.task_id,))
                admitting = pool.submit(dispatcher, wave.reservation)
                sleep(0.75)
                self.assertFalse(admitting.done(),
                                  "admission must still be waiting on the locked batch row")
                with self.handle.pool.connection() as registrar, registrar.transaction():
                    registrar.execute(
                        f"INSERT INTO {self.schema}.director_primary_provider_budget"
                        "(authority_digest,document) VALUES(%s,%s::jsonb)",
                        (competing.grant_digest, competing.model_dump_json()))
            with self.assertRaises(WorkloadAdmissionHold) as raised:
                admitting.result(timeout=30)
        self.assertEqual("readiness_authority_changed_during_admission",
                          raised.exception.reason_code)
        self._assert_no_admission_rows()

    def test_writer_lease_expiring_during_the_admission_wait_rolls_back(self):
        """The lease is proved against the real clock at the final fence.

        The lease is set to run out one second from now, and the real
        admission is then blocked on the batch row for longer than that. Its
        first writer check passed while the lease was live, and holding that
        row stops a takeover but not the clock; `now()` is this transaction's
        start time, so a transaction-start comparison would still call the
        lease current. The execution deadline is far later, so nothing else
        would stop it. Admission is entered directly here because the live
        coordinator renews its own lease immediately beforehand, which is
        exactly the behaviour that would mask the defect.
        """
        from concurrent.futures import ThreadPoolExecutor
        from time import sleep
        from director_orchestrator.primary_parallel import ParallelDispatchError
        wave = self._ready_wave()
        limits = ProviderAttemptBudget.from_usd(
            project_id=wave.packet["project"],
            attempt_usd=float(wave.packet["limits"]["budget_usd"]) / 3.0,
            task_usd=wave.settings.max_task_budget_usd,
            daily_usd=wave.settings.max_daily_provider_budget_usd)

        def admit():
            return self.ledger.reserve_provider_attempt(
                reservation=wave.reservation, writer=wave.writer, attempt_ordinal=1,
                format_roster=(("model-work-packet-v1", "responses-api"),),
                idempotency_key_digest=digest("idem-lease"),
                budget_evidence_digest="sha256:" + wave.budget.grant_digest,
                budget_admission=ProviderBudgetAdmission(self.authority,
                                                          wave.budget.grant_digest),
                budget_limits=limits,
                expected_authority_digest=wave.budget.grant_digest)

        with ThreadPoolExecutor(max_workers=1) as pool, self._no_provider_call():
            with self.handle.pool.connection() as blocker, blocker.transaction():
                blocker.execute(
                    f"SELECT * FROM {self.schema}.director_primary_parallel_batch "
                    "WHERE task_id=%s FOR UPDATE", (wave.task_id,))
                with self.handle.pool.connection() as expiring, expiring.transaction():
                    expiring.execute(
                        f"UPDATE {self.schema}.director_primary_task_writer "
                        "SET lease_expires_at = clock_timestamp() + interval '1 second' "
                        "WHERE task_id=%s", (wave.task_id,))
                admitting = pool.submit(admit)
                sleep(1.6)
                self.assertFalse(admitting.done(),
                                  "admission must still be waiting on the locked batch row")
            with self.assertRaises(ParallelDispatchError) as raised:
                admitting.result(timeout=30)
        self.assertIn("writer authority", str(raised.exception))
        self._assert_no_admission_rows()

    def test_enrollment_replaced_after_admission_stops_the_send(self):
        """Between a committed admission and the provider call, it still holds."""

        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._enrolled_wave()
        coordinator = wave.activation.coordinator
        admit = coordinator.reserve_provider_attempt

        def admit_then_replace(**kwargs):
            attempt = admit(**kwargs)
            wave.transport.response_meter = self._replaced_enrollment(
                wave, tariff_id="tariff-replaced")
            return attempt

        with patch.object(coordinator, "reserve_provider_attempt", side_effect=admit_then_replace), \
             self._no_provider_call(), \
             self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("readiness_enrollment_changed_before_dispatch",
                          raised.exception.reason_code)
        with self.handle.pool.connection() as connection:
            self.assertEqual(0, connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_usage"
            ).fetchone()["n"])

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

    def test_writer_reacquired_for_admission_cannot_admit_the_stale_selection(self):
        """The writer admission actually runs under, not the one dispatch read first.

        The independent witness (writer-selection-after14): this task's
        writer lease expires after the real `live_provider_admissions` lookup
        returns and before the real coordinator resolves the writer for
        admission, so the coordinator reacquires it - generation 1 becomes 2.
        Readiness was checked under generation 1, and so was the writer
        dispatch captured when it began, so the comparison between those two
        passed and admission committed under generation 2: two attempt rows
        including a STARTED one, a spend row, a workload row and one HTTP
        client construction were observed at the frozen C14 head. The
        generation readiness selected is now bound to the writer the
        coordinator resolves, before the admission transaction: nothing is
        written, and no client is constructed.
        """
        from director_orchestrator import primary_default
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._enrolled_wave()
        original = primary_default.live_provider_admissions
        observed = {"admissions": 0, "http": 0}

        def admissions(**kwargs):
            result = original(**kwargs)
            observed["admissions"] += 1
            # Only the synthetic task's lease, after the real authority lookup
            # and before the coordinator resolves the writer for admission.
            with self.handle.pool.connection() as connection, connection.transaction():
                connection.execute(
                    f"UPDATE {self.schema}.director_primary_task_writer "
                    "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE task_id=%s",
                    (wave.task_id,))
            return result

        def no_http(*_args, **_kwargs):
            observed["http"] += 1
            raise AssertionError("HTTP constructor reached; network prevented")

        with patch.object(primary_default, "live_provider_admissions", side_effect=admissions), \
             patch("director_orchestrator.primary_transports.httpx.AsyncClient", side_effect=no_http), \
             self.assertRaises(WorkloadAdmissionHold) as raised:
            wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("readiness_writer_changed_before_admission", raised.exception.reason_code)
        self.assertEqual({"admissions": 1, "http": 0}, observed,
                         "the witness reached the real admission lookup once and no provider client")
        with self.handle.pool.connection() as connection:
            generation = connection.execute(
                f"SELECT fencing_generation FROM {self.schema}.director_primary_task_writer "
                "WHERE task_id=%s", (wave.task_id,)).fetchone()["fencing_generation"]
        self.assertEqual(wave.writer.fencing_generation + 1, generation,
                         "the coordinator reacquired the expired lease, and that writer was refused")
        self._assert_no_admission_rows()

    # --- the ordinary command boundary, under the constructor-created dispatcher ---

    def _attempt_history(self, task_id):
        with self.handle.pool.connection() as connection:
            rows = connection.execute(
                f"SELECT attempt_digest, status FROM {self.schema}.director_primary_provider_attempt "
                "WHERE task_id=%s ORDER BY event_sequence", (task_id,)).fetchall()
        return [row["status"] for row in rows], {row["attempt_digest"] for row in rows}

    def test_command_child_receives_only_the_remaining_carried_bound_through_the_constructor(self):
        """The real `NativeCommandPacketTransport` at the end of the real dispatch path.

        Readiness fixes this assignment's bound six seconds out, from a packet
        whose own timeout is six, so each attempt's relative timeout is two.
        Dispatch is then delayed: only the transport module's clock is
        advanced four seconds, so every dispatcher-side check has passed on
        the real clock and the transport is the one to notice that under two
        seconds of the carried bound remain - the case readiness's own
        refusal cannot cover. The child must be given that remainder, less
        than the two-second relative timeout, and the attempt still settles
        normally. The launch is synthetic; readiness, admission, the
        coordinator, the context, the transport and its cutoff arithmetic
        are real.
        """
        with TemporaryDirectory() as directory:
            transport = NativeCommandPacketTransport(
                "openai", ("native-cli.exe", "--print"), cwd=directory, prompt_mode="stdin")
            wave = self._ready_wave(transport=transport, provider_timeout_seconds=6)
            launch = _FakeLaunch(stdout=NATIVE_ENVELOPE)
            contexts = []
            original_send = transport.send

            def observed_send(packet, *, context):
                contexts.append(context)
                return original_send(packet, context=context)

            transport.send = observed_send
            with patch(LAUNCH_SEAM, launch), \
                 patch("director_orchestrator.primary_transports.monotonic",
                       lambda: real_monotonic() + 4.0):
                outcome = wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual(1, len(launch.calls))
        [context] = contexts
        self.assertIsNotNone(context.deadline, "the constructor carried the readiness bound")
        self.assertEqual(2, context.timeout_seconds, "the packet's six seconds, one third per attempt")
        self.assertGreater(launch.waits[0], 1.0, "the carried remainder after the delay")
        self.assertLess(launch.waits[0], context.timeout_seconds,
                        "never the relative timeout the delay would otherwise restart")
        self.assertEqual(["terminate", ("wait", 5.0), "close"], launch.stopped)
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, outcome.status,
                         f"dispatch outcome: {outcome.status} / {outcome.reason_code}")
        statuses, digests = self._attempt_history(wave.task_id)
        self.assertEqual(["STARTED", "SUCCEEDED"], statuses)
        self.assertEqual(1, len(digests))

    def test_command_transport_refuses_a_spent_carried_bound_before_launch_through_the_constructor(self):
        """The bound runs out between the dispatcher's last check and the transport's entry.

        Modelled by advancing only the transport module's clock ten seconds:
        every dispatcher-side check has passed, the adapter has validated the
        packet, and the transport is the first to see the spent bound. It
        launches nothing. The refusal is classified the way every
        non-transient dispatch exception is - one AMBIGUOUS attempt that
        keeps its identity and slot, never a second attempt.
        """
        with TemporaryDirectory() as directory:
            transport = NativeCommandPacketTransport(
                "openai", ("native-cli.exe", "--print"), cwd=directory, prompt_mode="stdin")
            wave = self._ready_wave(transport=transport, provider_timeout_seconds=3)
            launch = _FakeLaunch(stdout=NATIVE_ENVELOPE)
            with patch(LAUNCH_SEAM, launch), \
                 patch("director_orchestrator.primary_transports.monotonic",
                       lambda: real_monotonic() + 10.0):
                outcome = wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual([], launch.calls, "a spent bound launches nothing")
        self.assertEqual(ParallelAssignmentStatus.AMBIGUOUS, outcome.status)
        self.assertEqual("provider_outcome_unknown", outcome.reason_code)
        statuses, digests = self._attempt_history(wave.task_id)
        self.assertEqual(["STARTED", "AMBIGUOUS"], statuses, "one attempt, retained, not repeated")
        self.assertEqual(1, len(digests))

    def test_responses_requests_receive_only_the_remaining_carried_bound_through_the_constructor(self):
        """The narrow Responses counterpart: both requests are bounded by the carried remainder.

        The same six-second bound and four-second delayed dispatch. The count
        and generation requests each carry an HTTPX timeout of what is left
        of the carried bound - under the two-second relative timeout - and
        the attempt then settles through the real meter. With the transport's
        clock advanced past the bound instead, no request is made at all.
        """
        wave = self._enrolled_wave(provider_timeout_seconds=6)
        timeouts, contexts = [], []
        original_send = wave.transport.send

        def observed_send(packet, *, context):
            contexts.append(context)
            return original_send(packet, context=context)

        wave.transport.send = observed_send
        main_reply = {"object": "response", "id": "resp-bounded", "status": "completed",
            "model": wave.model, "service_tier": "default",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "bounded evidence"}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}}}

        def handle_request(request):
            timeouts.append(request.extensions["timeout"]["read"])
            if str(request.url).endswith("/input_tokens"):
                return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
            return httpx.Response(200, json=main_reply, headers={
                "openai-organization": "org-synthetic", "openai-project": "proj-synthetic",
                "x-request-id": "req-bounded"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
        self.addCleanup(lambda: asyncio.run(client.aclose()))
        with patch("director_orchestrator.primary_transports.httpx.AsyncClient", return_value=client), \
             patch("director_orchestrator.primary_transports.monotonic",
                   lambda: real_monotonic() + 4.0):
            outcome = wave.activation.application._parallel_dispatcher(wave.reservation)
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, outcome.status,
                         f"dispatch outcome: {outcome.status} / {outcome.reason_code}")
        [context] = contexts
        self.assertEqual(2, context.timeout_seconds)
        self.assertEqual(2, len(timeouts), "one count and one generation request")
        for observed in timeouts:
            self.assertGreater(observed, 0.0)
            self.assertLess(observed, context.timeout_seconds, "the carried remainder, not the relative timeout")
        self.assertGreater(timeouts[0], 1.0, "the count request saw the remainder after the delay")

        expired = self._enrolled_wave(provider_timeout_seconds=6)
        requests = []
        never = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(500)))
        self.addCleanup(lambda: asyncio.run(never.aclose()))
        with patch("director_orchestrator.primary_transports.httpx.AsyncClient", return_value=never), \
             patch("director_orchestrator.primary_transports.monotonic",
                   lambda: real_monotonic() + 10.0):
            outcome = expired.activation.application._parallel_dispatcher(expired.reservation)
        self.assertEqual([], requests, "a spent bound sends nothing")
        self.assertEqual(ParallelAssignmentStatus.AMBIGUOUS, outcome.status)
        statuses, digests = self._attempt_history(expired.task_id)
        self.assertEqual(["STARTED", "AMBIGUOUS"], statuses)
        self.assertEqual(1, len(digests))

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

    def test_lost_readiness_is_refused_before_any_database_call(self):
        """The refusal happens ahead of the writer read and the artifact write.

        A known refusal must not first wait on locks and mutate durable state:
        with no live selection there is no bound to spend, so `None` would
        also disable the SQL deadline on both of those calls. The sentinels
        are installed only after setup, so they witness the dispatch itself.
        """
        from director_orchestrator.primary_workload import WorkloadAdmissionHold
        wave = self._ready_wave()
        restarted, _settings = self._build_activation(
            OpenAIResponsesPacketTransport(api_key="test-secret-key", model="configured-model"))
        restarted.services._packet_router = wave.activation.services._packet_router
        restarted.services.register(wave.activation.services._record(wave.task_id))

        def artifact_rows():
            with self.handle.pool.connection() as connection:
                return connection.execute(
                    f"SELECT count(*) AS n FROM {self.schema}"
                    ".director_primary_work_packet_artifact").fetchone()["n"]

        before = artifact_rows()
        reached = []
        # Patched on the store class, and only now that setup is finished, so
        # these sentinels witness the dispatch under test and nothing else.
        for entry in ("current_task_writer", "record_work_packet_artifact"):
            patcher = patch.object(
                type(self.handle.claim_store), entry, autospec=True,
                side_effect=lambda *_a, _entry=entry, **_k: reached.append(_entry))
            patcher.start()
            self.addCleanup(patcher.stop)
        with self._no_provider_call():
            with self.assertRaises(WorkloadAdmissionHold) as raised:
                restarted.application._parallel_dispatcher(wave.reservation)
        self.assertEqual("readiness_selection_unavailable_before_admission",
                          raised.exception.reason_code)
        self.assertEqual([], reached,
                          "neither database entry point may be called")
        self.assertEqual(before, artifact_rows(),
                          "no packet artifact is persisted by a refused dispatch")
        self._assert_no_admission_rows()
        with self.handle.pool.connection() as connection:
            self.assertEqual(0, connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}"
                ".director_primary_provider_usage").fetchone()["n"])

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

    def _enrollment(self, *, attempt_digest, authority_digest, packet, task_id, assignment_id,
                    sent_at, api_key="test-secret-key", model="configured-model",
                    organization_id="org-synthetic", api_project_id="proj-synthetic",
                    expires_in_seconds=3600.0, tariff_id="tariff-synthetic"):
        """One owner-signed enrollment for exactly one admitted attempt."""

        return signed(ResponseMeterEnrollment(
            schema_version="responses-meter-enrollment/v1",
            authority_digest=authority_digest, attempt_digest=attempt_digest,
            project_id=packet["project"], task_id=task_id, assignment_id=assignment_id,
            work_packet_digest=packet_document_digest(packet),
            credential_digest=hashlib.sha256(api_key.encode()).hexdigest(),
            organization_id=organization_id, api_project_id=api_project_id,
            model=model, service_tier="default", origin="https://api.openai.com",
            tariff_id=tariff_id, input_microusd_per_million=1000,
            cached_input_microusd_per_million=500, output_microusd_per_million=2000,
            valid_from=sent_at - timedelta(minutes=5),
            expires_at=sent_at + timedelta(seconds=expires_in_seconds),
            owner_signature="0" * 64,
        ), fixtures.OWNER_SECRET)

    def _admitted_meter(self, *, expires_in_seconds=3600.0, clock=None,
                        api_key="test-secret-key", organization_id="org-synthetic",
                        api_project_id="proj-synthetic"):
        """Admit one real attempt and return its meter, response and facts.

        Everything is real except the provider reply: a real signed budget,
        real `reserve_ready`, real `reserve_provider_attempt` (so a real spend
        row exists), a real signed enrollment and the real meter.
        """
        task_id = "DIR-PG-LATE-" + uuid.uuid4().hex[:8].upper()
        model = "configured-model"
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
        sent_at = datetime.now(timezone.utc)
        enrollment = self._enrollment(
            attempt_digest=attempt_digest, authority_digest=signed_budget.grant_digest,
            packet=packet, task_id=task_id, assignment_id=assignment_id, sent_at=sent_at,
            api_key=api_key, model=model, organization_id=organization_id,
            api_project_id=api_project_id, expires_in_seconds=expires_in_seconds)
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
                                project_id=packet["project"], transport=transport,
                                packet=packet, task_id=task_id, assignment_id=assignment_id,
                                api_key=api_key, model=model,
                                organization_id=organization_id, api_project_id=api_project_id,
                                attempt_usd=attempt_usd)

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
        from time import monotonic as _monotonic
        expired = datetime.now(timezone.utc) + timedelta(hours=2)
        case = self._admitted_meter(expires_in_seconds=1.0, clock=lambda: expired)
        self.assertEqual([], self._usage_rows())
        # The production shape: the execution callback raises, exactly as the
        # transport's does once its budget is spent, and only the absolute
        # accounting cutoff keeps the receipt reachable.
        def spent_execution_budget():
            raise RuntimeError("OpenAI transport timeout is exhausted")
        receipt = case.meter.settle(
            case.native, case.headers, sent_at=case.sent_at,
            remaining_seconds=spent_execution_budget,
            accounting_deadline=_monotonic() + 5.0)
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
        """A late receipt is not a relaxed one.

        Every call uses the production shape - a raising execution callback
        plus one absolute accounting cutoff - and that cutoff is the same
        value on every call, so replay cannot extend it.
        """
        from time import monotonic as _monotonic
        expired = datetime.now(timezone.utc) + timedelta(hours=2)
        case = self._admitted_meter(expires_in_seconds=1.0, clock=lambda: expired)

        def spent():
            raise RuntimeError("OpenAI transport timeout is exhausted")

        cutoff = _monotonic() + 5.0
        late = dict(remaining_seconds=spent, accounting_deadline=cutoff)
        case.meter.settle(case.native, case.headers, sent_at=case.sent_at, **late)
        self.assertEqual(1, len(self._usage_rows()))
        # Exact replay is idempotent: still one row, same amounts.
        case.meter.settle(case.native, case.headers, sent_at=case.sent_at, **late)
        rows = self._usage_rows()
        self.assertEqual(1, len(rows))
        # A conflicting replay of the same attempt is refused, and the
        # original recorded facts are left exactly as they were.
        conflicting = dict(case.native)
        conflicting["usage"] = dict(case.native["usage"],
                                    output_tokens=51, total_tokens=151)
        with self.assertRaisesRegex(TeamAuthorizationError, "conflicting usage receipt replay"):
            case.meter.settle(conflicting, case.headers, sent_at=case.sent_at, **late)
        self.assertEqual(rows, self._usage_rows())
        # An unconfirmed account association is refused before any write.
        other = self._admitted_meter(expires_in_seconds=1.0, clock=lambda: expired)
        with self.assertRaisesRegex(TeamAuthorizationError, "account association"):
            other.meter.settle(other.native,
                                dict(other.headers, **{"openai-organization": "org-other"}),
                                sent_at=other.sent_at, **late)
        # A tampered signature is refused even with a valid anchor and cutoff.
        forged = EnrolledResponseMeter(
            enrollment=other.enrollment.model_copy(update={"owner_signature": "f" * 64}),
            authority=self.authority, pool=self.handle.pool, clock=lambda: expired)
        with self.assertRaisesRegex(TeamAuthorizationError, "signature invalid"):
            forged.settle(other.native, other.headers, sent_at=other.sent_at, **late)
        # An accounting cutoff already spent records nothing and keeps the
        # reservation: the allowance is a bound, not an unconditional write.
        with self.assertRaisesRegex(RuntimeError, "deadline exhausted"):
            other.meter.settle(other.native, other.headers, sent_at=other.sent_at,
                                remaining_seconds=spent,
                                accounting_deadline=_monotonic() - 1.0)
        self.assertEqual(1, len(self._usage_rows()))

    def test_late_settlement_under_project_lock_contention_retains_reservation(self):
        """Its bounded allowance is a bound, not a licence to wait forever.

        The production shape again: the execution callback is already spent,
        so only the absolute cutoff governs, and it ends the accounting lock
        wait rather than holding a pooled connection open indefinitely.
        """
        from time import monotonic as _monotonic
        expired = datetime.now(timezone.utc) + timedelta(hours=2)
        case = self._admitted_meter(expires_in_seconds=1.0, clock=lambda: expired)

        def spent():
            raise RuntimeError("OpenAI transport timeout is exhausted")

        with self.handle.pool.connection() as blocker, blocker.transaction():
            blocker.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            ("primary-provider-budget:" + case.project_id,))
            started = _monotonic()
            with self.assertRaises(RuntimeError):
                case.meter.settle(case.native, case.headers, sent_at=case.sent_at,
                                  remaining_seconds=spent,
                                  accounting_deadline=_monotonic() + 0.4)
            elapsed = _monotonic() - started
        self.assertLess(elapsed, 8.0)
        self.assertEqual([], self._usage_rows())
        with self.handle.pool.connection() as connection:
            held = connection.execute(
                f"SELECT reserved_microusd FROM {self.schema}.director_primary_provider_spend "
                "WHERE attempt_digest=%s", (case.attempt_digest,)).fetchone()
        self.assertIsNotNone(held, "a failed settlement never releases the reservation")

    # --- the real transport reaching the real meter ---

    def _adapter_context(self, case, **changes):
        """The context real dispatch builds for this exact admitted attempt."""

        from director_orchestrator.langgraph_runtime import ProviderAdapterContext
        fields = dict(
            task_id=case.task_id, assignment_id=case.assignment_id,
            idempotency_key=case.attempt_digest,
            work_packet_digest=packet_document_digest(case.packet),
            writer_token_digest=digest("writer"), timeout_seconds=2,
            budget_usd=case.attempt_usd, max_hops=1, max_revisions=1,
            allowed_tools=(), allowed_external_effects=(), fencing_generation=1,
            total_token_limit=1000,
            enrollment_digest=document_digest(case.enrollment.model_dump(mode="json")))
        fields.update(changes)
        return ProviderAdapterContext(**fields)

    def _reenrolled(self, case, *, expires_in_seconds):
        """Re-sign this admitted attempt's enrollment from *now*.

        The same owner signature, attempt, packet, credential and account; only
        the signed validity window moves, so the window's edge is a known
        distance from the call under test instead of from admission.
        """
        replacement = self._enrollment(
            attempt_digest=case.attempt_digest,
            authority_digest=case.enrollment.authority_digest, packet=case.packet,
            task_id=case.task_id, assignment_id=case.assignment_id,
            sent_at=datetime.now(timezone.utc), api_key=case.api_key, model=case.model,
            organization_id=case.organization_id, api_project_id=case.api_project_id,
            expires_in_seconds=expires_in_seconds)
        case.transport.response_meter = ResponseMeterRegistry(
            enrollments=(replacement,), authority=self.authority, pool=self.handle.pool)
        return document_digest(replacement.model_dump(mode="json"))

    def _synthetic_openai(self, case, *, count_delay=0.0):
        """A synthetic Responses endpoint. No network and no credentials."""

        from time import sleep
        seen = []
        created_at = int(datetime.now(timezone.utc).timestamp())

        def handle_request(request):
            seen.append(str(request.url))
            if str(request.url).endswith("/input_tokens"):
                if count_delay:
                    sleep(count_delay)
                return httpx.Response(200, json={"object": "response.input_tokens",
                                                  "input_tokens": 100})
            return httpx.Response(200, json={
                "object": "response", "id": "resp-transport-late", "status": "completed",
                "model": case.model, "service_tier": "default", "created_at": created_at,
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "synthetic evidence"}]}],
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}}},
                headers={"openai-organization": case.organization_id,
                         "openai-project": case.api_project_id,
                         "x-request-id": "req-synthetic-transport"})

        return seen, httpx.MockTransport(handle_request)

    def test_transport_records_a_completed_reply_whose_unwinding_timed_out(self):
        """The real transport, the real meter, one complete reply, no time left.

        The provider answered inside the bound; only the async unwinding ran
        past it, and the signed window closed meanwhile. The authenticated
        usage - anchored at the instant the generation request was actually
        sent, not at token counting - is still recorded through the existing
        receipt path, and the attempt is still refused.
        """
        import asyncio
        from time import monotonic as _monotonic

        class _TimeoutOnExit:
            """A real client whose async-context exit outlives the bound."""

            def __init__(self, client):
                self._client = client

            async def __aenter__(self):
                return self._client

            async def __aexit__(self, *_exception):
                await asyncio.sleep(30)

        case = self._admitted_meter()
        # Re-signed here so the window closes a known two seconds from this
        # instant, rather than from whenever admission happened.
        closing = self._reenrolled(case, expires_in_seconds=2.0)
        seen, mock = self._synthetic_openai(case)
        client = httpx.AsyncClient(transport=mock)
        packet_bytes = canonical_json_bytes(case.packet)
        try:
            with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                       return_value=_TimeoutOnExit(client)):
                with self.assertRaisesRegex(
                        RuntimeError, "exhausted after a complete provider response"):
                    case.transport.send(packet_bytes, context=self._adapter_context(
                        case, deadline=_monotonic() + 2, enrollment_digest=closing))
        finally:
            asyncio.run(client.aclose())
        self.assertEqual(2, len(seen))
        rows = self._usage_rows()
        self.assertEqual(1, len(rows), "a complete reply that cost real tokens is still recorded")
        self.assertEqual(case.attempt_digest, rows[0]["attempt_digest"])
        self.assertEqual(150, rows[0]["observed_tokens"])

    def _closing_stream_case(self, failure):
        """One complete generation reply whose cleanup then fails.

        A real `httpx.AsyncByteStream` through `MockTransport`, so the body is
        genuinely exhausted and closed rather than handed over as a cached
        `Response`. `failure` runs inside `aclose`, which HTTPX awaits while
        ending `aiter_bytes` - after the whole body has been delivered.
        """
        case = self._admitted_meter()
        seen, mock = self._synthetic_openai(case)
        evidence = {"body_exhausted": False, "close_entered": False,
                    "stream_cleanup_finished": False}
        responses = []

        class ClosingBody(httpx.AsyncByteStream):
            def __init__(self, body):
                self.body = body

            async def __aiter__(self):
                yield self.body
                evidence["body_exhausted"] = True

            async def aclose(self):
                evidence["close_entered"] = True
                try:
                    await failure()
                finally:
                    # This fixture owns no socket; the flag witnesses that its
                    # own cleanup path completed, even under cancellation.
                    evidence["stream_cleanup_finished"] = True

        async def respond(request):
            original = await mock.handle_async_request(request)
            if str(request.url).endswith("/input_tokens"):
                return original
            response = httpx.Response(original.status_code, headers=original.headers,
                                      stream=ClosingBody(original.content), request=request)
            responses.append(response)
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        return SimpleNamespace(case=case, seen=seen, evidence=evidence,
                                responses=responses, client=client)

    def _assert_complete_usage_survived(self, fixture, started, *, requests=2):
        from time import monotonic as _monotonic

        self.assertLess(_monotonic() - started, 9.0)
        self.assertEqual(requests, len(fixture.seen))
        self.assertTrue(all(fixture.evidence.values()), fixture.evidence)
        self.assertEqual(1, len(fixture.responses))
        self.assertTrue(fixture.responses[0].is_closed)
        self.assertTrue(fixture.client.is_closed)
        rows = self._usage_rows()
        self.assertEqual(1, len(rows),
                          "a complete reply that was already delivered is still charged")
        self.assertEqual(fixture.case.attempt_digest, rows[0]["attempt_digest"])
        self.assertEqual(150, rows[0]["observed_tokens"])
        with self.handle.pool.connection() as connection:
            self.assertIsNotNone(connection.execute(
                f"SELECT 1 FROM {self.schema}.director_primary_provider_spend "
                "WHERE attempt_digest=%s", (fixture.case.attempt_digest,)).fetchone())

    def test_inner_response_close_timeout_keeps_the_usage_it_delivered(self):
        """The body arrived in full; only the response's own close hung."""

        from time import monotonic as _monotonic

        async def hang():
            await asyncio.sleep(30)

        fixture = self._closing_stream_case(hang)
        started = _monotonic()
        try:
            with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                       return_value=fixture.client):
                with self.assertRaisesRegex(
                        RuntimeError, "exhausted after a complete provider response"):
                    fixture.case.transport.send(
                        canonical_json_bytes(fixture.case.packet),
                        context=self._adapter_context(fixture.case, deadline=started + 1.0))
        finally:
            asyncio.run(fixture.client.aclose())
        self._assert_complete_usage_survived(fixture, started)

    def test_inner_response_close_error_keeps_the_usage_it_delivered(self):
        """The same boundary when cleanup raises instead of hanging."""

        from time import monotonic as _monotonic

        async def fail():
            raise httpx.ReadError("synthetic response-stream close failure")

        fixture = self._closing_stream_case(fail)
        started = _monotonic()
        try:
            with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                       return_value=fixture.client):
                with self.assertRaisesRegex(
                        RuntimeError, "cleanup failed after a complete provider response"):
                    fixture.case.transport.send(
                        canonical_json_bytes(fixture.case.packet),
                        context=self._adapter_context(fixture.case, deadline=started + 20.0,
                                                       timeout_seconds=20))
        finally:
            asyncio.run(fixture.client.aclose())
        self._assert_complete_usage_survived(fixture, started)

    def test_outer_client_close_error_keeps_the_usage_it_delivered(self):
        """The client's own cleanup fails after a normally closed response."""

        from time import monotonic as _monotonic
        case = self._admitted_meter()
        seen, mock = self._synthetic_openai(case)
        real = httpx.AsyncClient(transport=mock)

        class FailingExit:
            """The real client, whose context exit raises after the reply."""

            async def __aenter__(self):
                return real

            async def __aexit__(self, *_exception):
                await real.aclose()
                raise httpx.CloseError("synthetic client close failure")

        started = _monotonic()
        with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                   return_value=FailingExit()):
            with self.assertRaisesRegex(
                    RuntimeError, "cleanup failed after a complete provider response"):
                case.transport.send(canonical_json_bytes(case.packet),
                                    context=self._adapter_context(
                                        case, deadline=started + 20.0, timeout_seconds=20))
        self.assertEqual(2, len(seen))
        self.assertTrue(real.is_closed)
        rows = self._usage_rows()
        self.assertEqual(1, len(rows))
        self.assertEqual(case.attempt_digest, rows[0]["attempt_digest"])
        self.assertEqual(150, rows[0]["observed_tokens"])

    def test_truncated_body_before_a_cleanup_failure_stays_ambiguous(self):
        """Recovery proves completeness; it never assumes it."""

        from time import monotonic as _monotonic

        async def fail():
            raise httpx.ReadError("synthetic response-stream close failure")

        fixture = self._closing_stream_case(fail)
        # The same delivered-then-failed shape, with a body that cannot be a
        # complete reply: nothing is metered and the outcome stays unknown.
        truncated = fixture.responses
        original_respond = fixture.client._transport.handler

        async def respond(request):
            response = await original_respond(request)
            if str(request.url).endswith("/input_tokens"):
                return response
            body = response.stream.body[:-5]
            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=type(response.stream)(body), request=request)

        fixture.client._transport.handler = respond
        started = _monotonic()
        try:
            with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                       return_value=fixture.client):
                with self.assertRaisesRegex(RuntimeError, "outcome is ambiguous"):
                    fixture.case.transport.send(
                        canonical_json_bytes(fixture.case.packet),
                        context=self._adapter_context(fixture.case, deadline=started + 20.0,
                                                       timeout_seconds=20))
        finally:
            asyncio.run(fixture.client.aclose())
        self.assertEqual(2, len(fixture.seen))
        self.assertEqual(1, len(truncated))
        self.assertEqual([], self._usage_rows(),
                          "an incomplete body is never metered")

    def test_enrollment_expiring_between_counting_and_sending_stops_the_send(self):
        """`sent_at` is sampled at the generation request, not before counting.

        Input-token counting outlives the signed window here. Because the
        tariff is re-checked at the actual send instant, the generation
        request is never made and nothing is metered - the opposite outcome
        from a `sent_at` sampled before the counting call. The attempt ends
        refused and unretried, with its reservation still held.
        """
        from time import monotonic as _monotonic
        case = self._admitted_meter()
        closing = self._reenrolled(case, expires_in_seconds=1.0)
        seen, mock = self._synthetic_openai(case, count_delay=1.5)
        client = httpx.AsyncClient(transport=mock)
        packet_bytes = canonical_json_bytes(case.packet)
        try:
            with patch("director_orchestrator.primary_transports.httpx.AsyncClient",
                       return_value=client):
                with self.assertRaises(RuntimeError) as raised:
                    case.transport.send(packet_bytes, context=self._adapter_context(
                        case, timeout_seconds=20, deadline=_monotonic() + 20,
                        enrollment_digest=closing))
        finally:
            asyncio.run(client.aclose())
        # The refusal is the meter's own current-validity gate at the send
        # instant; the transport reports the attempt conservatively rather
        # than deciding an authorization failure was a provider outcome.
        self.assertIsInstance(raised.exception.__cause__, TeamAuthorizationError)
        self.assertIn("not current", str(raised.exception.__cause__))
        self.assertEqual(1, len(seen), "the generation request must never be made")
        self.assertEqual([], self._usage_rows())
        with self.handle.pool.connection() as connection:
            self.assertIsNotNone(connection.execute(
                f"SELECT 1 FROM {self.schema}.director_primary_provider_spend "
                "WHERE attempt_digest=%s", (case.attempt_digest,)).fetchone())

    def test_transport_refuses_a_meter_other_than_the_one_selected(self):
        """The transport binds the meter it actually resolves, before any I/O.

        A registry replaced after the caller's own comparison, and a transport
        that has lost its registry entirely, are both refused at the transport
        boundary itself - so the check does not depend on the caller having
        looked at the right object.
        """
        from time import monotonic as _monotonic
        case = self._admitted_meter()
        packet_bytes = canonical_json_bytes(case.packet)
        replaced = self._enrollment(
            attempt_digest=case.attempt_digest,
            authority_digest=case.enrollment.authority_digest, packet=case.packet,
            task_id=case.task_id, assignment_id=case.assignment_id, sent_at=case.sent_at,
            api_key=case.api_key, model=case.model, tariff_id="tariff-replaced")
        case.transport.response_meter = ResponseMeterRegistry(
            enrollments=(replaced,), authority=self.authority, pool=self.handle.pool)
        with self._no_provider_call():
            with self.assertRaisesRegex(RuntimeError, "different response meter enrollment"):
                case.transport.send(packet_bytes, context=self._adapter_context(
                    case, deadline=_monotonic() + 20, timeout_seconds=20))
            case.transport.response_meter = None
            with self.assertRaisesRegex(RuntimeError, "lost the response meter"):
                case.transport.send(packet_bytes, context=self._adapter_context(
                    case, deadline=_monotonic() + 20, timeout_seconds=20))
        self.assertEqual([], self._usage_rows())

    # --- concurrency: two requests share nothing they were not granted ---

    def test_parallel_requests_cannot_exchange_enrollment_credentials_or_account(self):
        """Two admitted attempts, one registry, no crossing.

        Each request resolves strictly by its own admitted attempt digest, and
        neither the other request's credential nor its account association can
        be used to settle this one.
        """
        first = self._admitted_meter()
        second = self._admitted_meter(api_key="other-secret-key",
                                      organization_id="org-other", api_project_id="proj-other")
        self.assertNotEqual(first.attempt_digest, second.attempt_digest)
        shared = ResponseMeterRegistry(enrollments=(first.enrollment, second.enrollment),
                                       authority=self.authority, pool=self.handle.pool)
        for case in (first, second):
            resolved = shared.for_context(SimpleNamespace(idempotency_key=case.attempt_digest))
            self.assertEqual(case.attempt_digest, resolved.enrollment.attempt_digest)
            self.assertEqual(case.organization_id, resolved.enrollment.organization_id)
            self.assertEqual(hashlib.sha256(case.api_key.encode()).hexdigest(),
                              resolved.enrollment.credential_digest)
        # The other request's credential cannot authorise this one.
        binding = dict(api_url="https://api.openai.com/v1/responses", model=first.model,
                       packet=canonical_json_bytes(first.packet))
        context = SimpleNamespace(task_id=first.task_id, assignment_id=first.assignment_id,
            idempotency_key=first.attempt_digest,
            work_packet_digest=packet_document_digest(first.packet))
        with self.assertRaisesRegex(TeamAuthorizationError, "request binding mismatch"):
            first.meter.prepare(context=context, api_key=second.api_key, **binding)
        # Nor can the other request's context reach this enrollment.
        with self.assertRaisesRegex(TeamAuthorizationError, "request binding mismatch"):
            second.meter.prepare(context=context, api_key=second.api_key,
                                  **dict(binding, model=second.model))
        # Nor can the other request's account association settle it.
        with self.assertRaisesRegex(TeamAuthorizationError, "account association"):
            first.meter.settle(first.native,
                                dict(first.headers,
                                     **{"openai-organization": second.organization_id}),
                                sent_at=first.sent_at)
        # Nor can one provider response be claimed by two attempts.
        first.meter.settle(first.native, first.headers, sent_at=first.sent_at)
        with self.assertRaisesRegex(TeamAuthorizationError,
                                     "already metered for another attempt"):
            second.meter.settle(second.native, second.headers, sent_at=second.sent_at)
        # Each settles only its own facts, under its own attempt digest.
        second.native["id"] = "resp-late-openai-second"
        second.meter.settle(second.native, second.headers, sent_at=second.sent_at)
        self.assertEqual({first.attempt_digest, second.attempt_digest},
                          {row["attempt_digest"] for row in self._usage_rows()})

    def test_competing_waves_cannot_overspend_one_signed_daily_budget(self):
        """Two waves admit at once; the ledger, not readiness, decides.

        Both pass their own conservative readiness reads - neither promises
        capacity against the other - and both then admit simultaneously
        against the same project and day. Exactly one reservation exists
        afterwards, and the total never exceeds the signed daily limit.
        """
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        # Each wave's own signed budget allows half an attempt more than one
        # reservation: enough for either alone, never for both, and never
        # discoverable by readiness's own conservative read.
        first = self._enrolled_wave(daily_limit_ratio=1.5)
        second = self._enrolled_wave(daily_limit_ratio=1.5)
        self.assertEqual(first.packet["project"], second.packet["project"],
                          "the shared project day is what the ceiling covers")
        ceiling = first.daily_limit_microusd
        self.assertEqual(ceiling, second.daily_limit_microusd)
        self.assertLess(ceiling, 2 * first.reservation_microusd)
        barrier = Barrier(2)

        def admit(wave):
            barrier.wait(timeout=30)
            try:
                return wave.activation.coordinator.reserve_provider_attempt(
                    reservation=wave.reservation, attempt_ordinal=1,
                    format_roster=(("model-work-packet-v1", "responses-api"),),
                    idempotency_key_digest=digest("idem-" + wave.task_id),
                    budget_evidence_digest="sha256:" + wave.budget.grant_digest,
                    budget_admission=ProviderBudgetAdmission(self.authority,
                                                              wave.budget.grant_digest),
                    budget_limits=ProviderAttemptBudget.from_usd(
                        project_id=wave.packet["project"], attempt_usd=wave.attempt_usd,
                        task_usd=wave.settings.max_task_budget_usd,
                        daily_usd=wave.settings.max_daily_provider_budget_usd),
                    expected_authority_digest=wave.budget.grant_digest)
            except Exception as exc:  # returned, never swallowed
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.result(timeout=60) for future in
                        [pool.submit(admit, first), pool.submit(admit, second)]]
        admitted = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
        refused = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        self.assertEqual(1, len(admitted), f"exactly one admission: {outcomes}")
        self.assertEqual(1, len(refused))
        self.assertRegex(str(refused[0]), "budget exhausted")
        with self.handle.pool.connection() as connection:
            totals = connection.execute(
                f"SELECT count(*) AS n, COALESCE(sum(reserved_microusd),0) AS spent "
                f"FROM {self.schema}.director_primary_provider_spend").fetchone()
            attempts = connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_attempt"
            ).fetchone()["n"]
        self.assertEqual(1, totals["n"])
        self.assertEqual(1, attempts,
                          "the refused wave leaves no STARTED attempt without monetary accounting")
        self.assertLessEqual(totals["spent"], ceiling,
                              "concurrent waves must not overspend the signed daily limit")
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 22:29 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 09:43 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:18 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:07 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 14:50 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 16:04 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:25 UTC | END]]
