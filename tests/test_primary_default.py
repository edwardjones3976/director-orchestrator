# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from director_orchestrator.config import Settings
from director_orchestrator.langgraph_postgres import open_postgres_control_plane
from director_orchestrator.langgraph_primary import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalResume,
    HandlerContext,
    ProviderDisposition,
    ProviderSubgraphResult,
)
from director_orchestrator.langgraph_runtime import PacketBoundaryAdapter
from director_orchestrator.models import (
    AssignmentKind,
    AssignmentOutcome,
    IntakeAuthentication,
    ProviderName,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.packet_routing import DirectorPacketTaskRouter
from director_orchestrator.policy import preview_plan
from director_orchestrator.primary_coordinator import PrimaryTaskCoordinator
from director_orchestrator.primary_effect_gateways import (
    ExactEffectGatewayRegistry,
    GITHUB_MARK_READY,
    SLACK_POST_MESSAGE,
)
from director_orchestrator.primary_parallel import (
    ParallelAssignmentStatus,
    ParallelDispatchError,
    ParallelDispatchOutcome,
    PostgresParallelDispatch,
    ProviderAttemptReservation,
)
from director_orchestrator.primary_default import (
    DefaultDryRunServices,
    DefaultOfflineSimulationServices,
    DefaultPrimaryActivation,
    DefaultPrimaryApplication,
    DefaultPrimaryAuthority,
    _assignment_provider_lane,
    _execute_three_attempt_roster,
    _live_parallel_plan,
    _primary_provider_lane,
    _terminal_review_assignment,
    _typed_effect_binding,
    build_automatic_primary_live_dependencies,
    build_primary_packet_result_resolver,
    build_typed_external_effect_grant,
    resolve_default_primary_authority,
    typed_external_effect_grant_kinds,
)


TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
SHA = "b" * 40


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class _FixedAuthorityResolver:
    def resolve(self, record, assignment):
        return [{"path": "README.md", "commit_sha": SHA, "blob_sha": "c" * 40}]


def packet_router(settings: Settings) -> DirectorPacketTaskRouter:
    return DirectorPacketTaskRouter(
        authority_snapshots=_FixedAuthorityResolver(),
        boundary_adapters={},
        writer_owner_id="langgraph-primary-default",
        writer_secret="default-primary-test-writer-secret",
        timeout_seconds=settings.provider_timeout_seconds,
    )


class ProviderAttemptRosterTests(unittest.TestCase):
    def test_first_format_success_still_invokes_every_distinct_adapter(self) -> None:
        roster = (("json", "api"), ("text", "cli"))
        invoked: list[tuple[str, str]] = []
        settled: list[tuple[int, ParallelAssignmentStatus]] = []
        adapters = {
            roster[0]: lambda ordinal: {"status": "SUCCEEDED", "attempt": ordinal},
            roster[1]: lambda ordinal: {"status": "FAILED", "attempt": ordinal},
        }
        self.assertIsNot(adapters[roster[0]], adapters[roster[1]])

        def reserve(ordinal: int) -> ProviderAttemptReservation:
            format_id, transport_id = roster[(ordinal - 1) % len(roster)]
            return ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", format_id,
                transport_id, digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"),
                digest("budget"),
            )

        def record(attempt, status, _result_digest, _reason) -> None:
            settled.append((attempt.attempt_ordinal, status))

        def dispatch(attempt) -> dict[str, object]:
            identity = (attempt.format_id, attempt.transport_id)
            invoked.append(identity)
            return adapters[identity](attempt.attempt_ordinal)

        status, result, _result_digest, reason = _execute_three_attempt_roster(
            format_roster=roster,
            reserve_attempt=reserve,
            record_attempt=record,
            dispatch_attempt=dispatch,
        )

        self.assertEqual([roster[0], roster[1], roster[0]], invoked)
        self.assertEqual(
            [(1, ParallelAssignmentStatus.SUCCEEDED),
             (2, ParallelAssignmentStatus.FAILED),
             (3, ParallelAssignmentStatus.SUCCEEDED)],
            settled,
        )
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, status)
        assert result is not None
        self.assertEqual(1, result["attempt"])
        self.assertEqual("provider_result_authenticated", reason)

    def test_one_format_success_is_still_invoked_exactly_three_times(self) -> None:
        invoked: list[int] = []
        settled: list[int] = []

        def reserve(ordinal: int) -> ProviderAttemptReservation:
            return ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", "json", "api",
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            )

        status, result, _result_digest, reason = _execute_three_attempt_roster(
            format_roster=(("json", "api"),),
            reserve_attempt=reserve,
            record_attempt=lambda attempt, *_args: settled.append(
                attempt.attempt_ordinal
            ),
            dispatch_attempt=lambda attempt: (
                invoked.append(attempt.attempt_ordinal)
                or {"status": "SUCCEEDED", "attempt": attempt.attempt_ordinal}
            ),
        )

        self.assertEqual([1, 2, 3], invoked)
        self.assertEqual([1, 2, 3], settled)
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, status)
        assert result is not None
        self.assertEqual(1, result["attempt"])
        self.assertEqual("provider_result_authenticated", reason)

    def test_ambiguous_attempt_consumes_one_slot_and_never_redispatches(self) -> None:
        invoked: list[int] = []
        settled: list[tuple[int, ParallelAssignmentStatus]] = []

        def reserve(ordinal: int) -> ProviderAttemptReservation:
            return ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", "json", "api",
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            )

        def dispatch(attempt: ProviderAttemptReservation) -> dict[str, object]:
            invoked.append(attempt.attempt_ordinal)
            raise RuntimeError("outcome unknown")

        status, result, result_digest, reason = _execute_three_attempt_roster(
            format_roster=(("json", "api"),),
            reserve_attempt=reserve,
            record_attempt=lambda attempt, attempt_status, *_args: settled.append(
                (attempt.attempt_ordinal, attempt_status)
            ),
            dispatch_attempt=dispatch,
        )

        self.assertEqual([1], invoked)
        self.assertEqual([(1, ParallelAssignmentStatus.AMBIGUOUS)], settled)
        self.assertEqual(ParallelAssignmentStatus.AMBIGUOUS, status)
        self.assertIsNone(result)
        self.assertIsNone(result_digest)
        self.assertEqual("provider_outcome_unknown", reason)

    def test_later_ambiguous_attempt_keeps_the_prior_authenticated_success(self) -> None:
        invoked: list[int] = []
        settled: list[tuple[int, ParallelAssignmentStatus]] = []

        def reserve(ordinal: int) -> ProviderAttemptReservation:
            return ProviderAttemptReservation(
                "task", "assignment", ordinal, "openai", "json", "api",
                digest(f"key-{ordinal}"), digest(f"attempt-{ordinal}"), digest("budget"),
            )

        def dispatch(attempt: ProviderAttemptReservation) -> dict[str, object]:
            invoked.append(attempt.attempt_ordinal)
            if attempt.attempt_ordinal == 1:
                return {"status": "SUCCEEDED", "attempt": 1}
            raise RuntimeError("outcome unknown")

        status, result, result_digest, reason = _execute_three_attempt_roster(
            format_roster=(("json", "api"),),
            reserve_attempt=reserve,
            record_attempt=lambda attempt, attempt_status, *_args: settled.append(
                (attempt.attempt_ordinal, attempt_status)
            ),
            dispatch_attempt=dispatch,
        )

        # The ambiguous slot is durably terminal and consumes its attempt, but
        # the already-authenticated first result remains the assignment result.
        self.assertEqual([1, 2], invoked)
        self.assertEqual(
            [
                (1, ParallelAssignmentStatus.SUCCEEDED),
                (2, ParallelAssignmentStatus.AMBIGUOUS),
            ],
            settled,
        )
        self.assertEqual(ParallelAssignmentStatus.SUCCEEDED, status)
        assert result is not None
        self.assertEqual(1, result["attempt"])
        self.assertEqual(document_digest(result), result_digest)
        self.assertEqual("provider_result_authenticated", reason)


class DefaultPrimaryLiveStartupBoundaryTests(unittest.TestCase):
    def test_live_activation_composes_a_ready_typed_effect_executor(self):
        class Gateway:
            def __init__(self, system, action):
                self.system = system
                self.action = action

            def mutate(self, intent, timeout_seconds):  # pragma: no cover
                raise AssertionError("composition test must not call a gateway")

            def readback(self, intent, remote_identifier, timeout_seconds):  # pragma: no cover
                raise AssertionError("composition test must not call a gateway")

        registry = ExactEffectGatewayRegistry(
            (
                Gateway("slack", SLACK_POST_MESSAGE),
                Gateway("github", GITHUB_MARK_READY),
            ),
            allowed_actions=frozenset(
                {("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}
            ),
        )
        settings = replace(
            Settings(),
            langgraph_primary_enabled=True,
            langgraph_primary_live_provider_dispatch_enabled=True,
            allow_paid_runs=True,
            langgraph_primary_owner_token="o" * 32,
            repository_allowlist=("owner/repository",),
            allowed_starting_refs=("main",),
            repository_snapshot_root=str(Path.cwd()),
        )
        handle = SimpleNamespace(
            claim_store=SimpleNamespace(), pool=SimpleNamespace(), close=lambda: None
        )
        dependencies = SimpleNamespace(
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
        captured = {}

        class Coordinator:
            def __init__(self, *_args, **kwargs):
                captured.update(kwargs)

        with (
            patch(
                "director_orchestrator.primary_default.resolve_default_primary_authority",
                return_value=DefaultPrimaryAuthority(SHA, digest("authority")),
            ),
            patch(
                "director_orchestrator.primary_default.open_postgres_control_plane",
                return_value=handle,
            ),
            patch.object(Settings, "primary_provider_activation_bindings", return_value={"openai": object()}),
            patch(
                "director_orchestrator.primary_default.PrimaryProviderActivationService",
                return_value=provider_activation,
            ),
            patch(
                "director_orchestrator.primary_default.PrimaryTaskCoordinator",
                Coordinator,
            ),
        ):
            activation = DefaultPrimaryActivation(
                settings,
                live_dependencies=dependencies,
                effect_gateways=registry,
            )
            # Separately sufficient pools cannot claim readiness when their
            # intersection lacks a second independent specialist.
            provider_activation.provider_ready = lambda provider, **_kwargs: provider in {"openai", "claude", "grok"}
            with self.assertRaisesRegex(ValueError, "mandatory core"):
                DefaultPrimaryActivation(settings, live_dependencies=dependencies,
                                         effect_gateways=registry)

        self.assertTrue(captured["effect_executor"].ready)
        self.assertIsNotNone(activation.application)

    def test_exact_effect_gateway_action_binding_fails_closed_when_missing(self):
        gateway = SimpleNamespace(system="slack", action=SLACK_POST_MESSAGE)
        with self.assertRaisesRegex(ValueError, "exactly match"):
            ExactEffectGatewayRegistry(
                (gateway,),
                allowed_actions=frozenset(
                    {("slack", SLACK_POST_MESSAGE), ("github", GITHUB_MARK_READY)}
                ),
            )

    def test_antigravity_lane_is_an_explicit_gemini_backend_mapping(self):
        self.assertEqual("antigravity", _primary_provider_lane(ProviderName.GEMINI))
        self.assertEqual("claude", _primary_provider_lane(ProviderName.CLAUDE))

    def test_request_budget_units_are_frozen_from_validated_plan(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
        )
        application = DefaultPrimaryApplication(
            settings,
            coordinator=None,  # type: ignore[arg-type]
            services=None,  # type: ignore[arg-type]
            authority=DefaultPrimaryAuthority(SHA, digest("authority")),
        )
        intake = SlackIntake(
            source_event_id="budget-event",
            channel_id="C123",
            user_id="U123",
            text="Implement the bounded change",
            thread_ts="1.0",
        )
        record = TaskRecord(
            task_id="DIR-LG-BUDGET",
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        record.plan = preview_plan(intake.text, settings)

        request = application._request(record)

        expected_provider_units = sum(
            assignment.effort_points for assignment in record.plan.assignments
        )
        self.assertGreater(expected_provider_units, 0)
        self.assertEqual(expected_provider_units, request.provider_cost_units)
        self.assertEqual(0, request.effect_cost_units)
        self.assertEqual(expected_provider_units, request.budget_limit_units)

    def test_request_derives_external_effect_identity_from_validated_plan(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
        )
        application = DefaultPrimaryApplication(
            settings,
            coordinator=None,  # type: ignore[arg-type]
            services=None,  # type: ignore[arg-type]
            authority=DefaultPrimaryAuthority(SHA, digest("authority")),
        )
        intake = SlackIntake(
            source_event_id="effect-event",
            channel_id="C123",
            user_id="U123",
            text="Implement the bounded change and prepare a draft PR",
            thread_ts="1.1",
        )
        record = TaskRecord(
            task_id="DIR-LG-EFFECT",
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        plan = preview_plan(intake.text, settings)
        assignments = list(plan.assignments)
        assignments[0] = assignments[0].model_copy(update={"allow_draft_pr": True})
        record.plan = plan.model_copy(update={"assignments": assignments})

        first = application._request(record)
        second = application._request(record)

        self.assertTrue(first.external_effect_required)
        self.assertEqual(1, first.effect_cost_units)
        self.assertEqual(
            first.provider_cost_units + first.effect_cost_units,
            first.budget_limit_units,
        )
        self.assertIsNotNone(first.effect_key_digest)
        self.assertEqual(first.effect_key_digest, second.effect_key_digest)
        self.assertNotEqual(first.correlation_id, second.correlation_id)

    def test_paid_execution_requires_the_explicit_live_switch(self):
        settings = replace(
            Settings(),
            langgraph_primary_enabled=True,
            allow_paid_runs=True,
            langgraph_primary_live_provider_dispatch_enabled=False,
        )

        with self.assertRaisesRegex(
            ValueError,
            "ALLOW_PAID_RUNS is enabled.*LIVE_PROVIDER_DISPATCH_ENABLED is disabled",
        ):
            DefaultPrimaryActivation(settings)

    def test_reconciliation_readiness_is_not_sticky_after_failed_pass(self):
        activation = object.__new__(DefaultPrimaryActivation)
        activation.application = SimpleNamespace(
            recover=lambda _task_id: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        activation.services = object()
        projection = SimpleNamespace(
            task_id="task-freshness",
            projection={"terminal_classification": None},
        )
        activation.coordinator = SimpleNamespace(
            list_projections=lambda: (projection,),
            get_task_writer=lambda _task_id: None,
        )
        activation._reconciliation_worker_ready = True

        with self.assertRaisesRegex(RuntimeError, "boom"):
            activation.reconcile_once()
        self.assertFalse(activation._reconciliation_worker_ready)

    def test_automatic_live_discovery_fails_closed_for_missing_lanes(self):
        settings = replace(
            Settings(),
            repository_snapshot_root=str(Path.cwd()),
        )

        with self.assertRaisesRegex(
            ValueError,
            "automatic graph-native live dependencies are unavailable: missing=",
        ):
            build_automatic_primary_live_dependencies(
                settings,
                handle=None,  # type: ignore[arg-type]
                services=None,  # type: ignore[arg-type]
            )

    def test_automatic_live_discovery_reports_typed_microsoft_365_copilot_blocker(self):
        settings = replace(
            Settings(),
            repository_snapshot_root=str(Path.cwd()),
        )

        with patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ), self.assertRaisesRegex(
            ValueError,
            r"external_blockers=microsoft-365-copilot:"
            r"microsoft_365_copilot_work_iq_capability_evidence_missing",
        ):
            build_automatic_primary_live_dependencies(
                settings,
                handle=None,  # type: ignore[arg-type]
                services=None,  # type: ignore[arg-type]
            )


class DefaultPrimaryLiveWaveBoundaryTests(unittest.TestCase):
    """Budget, packet identity and effect-target authority for the live wave."""

    def _record(self, settings: Settings, text: str = "Implement the bounded change"):
        intake = SlackIntake(
            source_event_id="live-wave-event",
            channel_id="C0BAV9CMC80",
            user_id="U123",
            text=text,
            thread_ts="1787450001.123456",
        )
        record = TaskRecord(
            task_id="DIR-LG-LIVEWAVE",
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        record.plan = preview_plan(intake.text, settings)
        return record

    def test_live_wave_binds_a_positive_bounded_per_lane_budget(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
            max_task_budget_usd=2.0,
        )
        record = self._record(settings)
        self.assertTrue(
            all(
                assignment.estimated_cost_usd == 0
                for assignment in record.plan.assignments
            )
        )

        _live_parallel_plan(record, replace(settings, trusted_provider_cost_ranks_json='{"claude":2,"cursor":1,"grok":3}'), available=("openai", "claude", "cursor", "grok"), history=())

        lanes = ("cursor",)
        self.assertEqual(len(lanes) + 1, len(record.plan.assignments))
        expected = settings.max_task_budget_usd / (len(lanes) + 1)
        for assignment in record.plan.assignments:
            self.assertEqual(expected, assignment.estimated_cost_usd)
            self.assertGreater(assignment.estimated_cost_usd, 0)
            self.assertLessEqual(
                assignment.estimated_cost_usd, settings.max_task_budget_usd
            )
        self.assertAlmostEqual(
            settings.max_task_budget_usd,
            sum(item.estimated_cost_usd for item in record.plan.assignments),
        )
        # Every lane, including microsoft-365-copilot, keeps its own slot.
        self.assertEqual(
            list(lanes),
            [
                item.primary_provider_lane
                for item in record.plan.assignments[: len(lanes)]
            ],
        )

    def test_research_is_explicit_public_only_and_precedes_implementation(self):
        settings = replace(Settings(), repository_allowlist=("owner/repository",),
            trusted_provider_cost_ranks_json='{"claude":2,"cursor":1,"grok":3}')
        record = self._record(settings, text="PRIVATE-CONTEXT implementation")
        record.intake = record.intake.model_copy(update={
            "primary_required_capabilities": ("public_research",),
            "primary_public_research_query": "public API specification",
        })
        _live_parallel_plan(record, settings, available=("openai", "claude", "cursor", "perplexity"), history=())
        research, producer, reviewer = record.plan.assignments
        self.assertEqual(research.task, "public API specification")
        self.assertEqual(research.public_research_query, research.task)
        self.assertIsNone(research.repository)
        self.assertEqual(producer.depends_on, [1])
        self.assertNotEqual(producer.primary_provider_lane, reviewer.primary_provider_lane)
        self.assertNotIn("PRIVATE-CONTEXT", research.model_dump_json())
        packet = packet_router(settings).planned_work_packet(record, research)
        self.assertNotIn("PRIVATE-CONTEXT", json.dumps(packet))
        self.assertEqual(packet["objective"], "public API specification")

    def test_zero_configured_task_budget_fails_the_live_wave_closed(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
            max_task_budget_usd=0.0,
        )
        record = self._record(settings)

        with self.assertRaisesRegex(RuntimeError, "positive configured task budget"):
            _live_parallel_plan(record, replace(settings, trusted_provider_cost_ranks_json='{"claude":2,"cursor":1,"grok":3}'), available=("openai", "claude", "cursor", "grok"), history=())

    def test_terminal_review_barrier_is_identified_structurally(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
            max_task_budget_usd=2.0,
        )
        record = self._record(settings)
        _live_parallel_plan(record, replace(settings, trusted_provider_cost_ranks_json='{"claude":2,"cursor":1,"grok":3}'), available=("openai", "claude", "cursor", "grok"), history=())
        assignments = record.plan.assignments
        producer = min(assignments, key=lambda item: item.sequence)
        review = max(assignments, key=lambda item: item.sequence)

        bound = _terminal_review_assignment(record)

        self.assertIs(bound, review)
        self.assertIsNot(bound, producer)
        # The reviewer deliberately reuses a first-wave lane, so lane equality
        # could never have selected the barrier.
        reused = [
            item
            for item in assignments
            if _assignment_provider_lane(item) == _assignment_provider_lane(review)
        ]
        self.assertEqual(len(reused), 1)
        self.assertNotEqual(_assignment_provider_lane(producer), _assignment_provider_lane(review))

        detached = record.model_copy(
            update={
                "plan": record.plan.model_copy(
                    update={
                        "assignments": [
                            *assignments[:-1],
                            review.model_copy(update={"depends_on": []}),
                        ]
                    }
                )
            }
        )
        self.assertIsNone(_terminal_review_assignment(detached))

    def _effect_context(self, record, candidate="a" * 40, starting="a" * 40):
        return SimpleNamespace(
            task_id=record.task_id,
            candidate_sha=candidate,
            starting_sha=starting,
        )

    def _grant_settings(self, pull_number=82):
        return replace(
            Settings(),
            repository_allowlist=("owner/repository",),
            langgraph_primary_external_effect_pull_number=pull_number,
        )

    def test_github_effect_is_pinned_to_configured_director_authority(self):
        settings = self._grant_settings()
        record = self._record(settings)
        grant = build_typed_external_effect_grant(
            settings, DefaultPrimaryAuthority("a" * 40, digest("authority"))
        )
        assert grant is not None

        system, action, target, payload = _typed_effect_binding(
            grant=grant,
            record=record,
            context=self._effect_context(record),  # type: ignore[arg-type]
            granted_kinds=frozenset({"github_mark_ready"}),
            effect={
                "kind": "github_mark_ready",
                "target": "owner/repository#82",
                "status": "NOT_ATTEMPTED",
                "idempotency_key": "proposal-1",
            },
        )

        self.assertEqual(("github", GITHUB_MARK_READY), (system, action))
        self.assertEqual(
            {
                "repository": "owner/repository",
                "pull_number": 82,
                "head_sha": "a" * 40,
                "base_sha": "a" * 40,
            },
            target,
        )
        self.assertEqual({}, payload)
        self.assertEqual("owner/repository#82", grant.proposal_target)
        self.assertEqual(
            ("github_mark_ready",), typed_external_effect_grant_kinds(settings)
        )

    def test_github_effect_fails_closed_without_configured_pull_authority(self):
        record = self._record(self._grant_settings())
        authority = DefaultPrimaryAuthority("a" * 40, digest("authority"))
        unconfigured = self._grant_settings(pull_number=0)
        multi_repository = replace(
            self._grant_settings(),
            repository_allowlist=("owner/repository", "owner/second"),
        )

        for settings in (unconfigured, multi_repository):
            with self.subTest(repositories=settings.repository_allowlist):
                self.assertEqual((), typed_external_effect_grant_kinds(settings))
                self.assertIsNone(
                    build_typed_external_effect_grant(settings, authority)
                )
                with self.assertRaisesRegex(
                    RuntimeError, "configured positive pull request"
                ):
                    _typed_effect_binding(
                        grant=None,
                        record=record,
                        context=self._effect_context(record),  # type: ignore[arg-type]
                        granted_kinds=frozenset({"github_mark_ready"}),
                        effect={
                            "kind": "github_mark_ready",
                            "target": "owner/repository#82",
                            "status": "NOT_ATTEMPTED",
                            "idempotency_key": "missing-authority",
                        },
                    )

    def test_ungranted_effect_kind_never_reaches_a_gateway(self):
        settings = self._grant_settings()
        record = self._record(settings)
        grant = build_typed_external_effect_grant(
            settings, DefaultPrimaryAuthority("a" * 40, digest("authority"))
        )

        # The work packet, not a closed vocabulary, is the only grant authority.
        with self.assertRaisesRegex(RuntimeError, "not granted by the work packet"):
            _typed_effect_binding(
                grant=grant,
                record=record,
                context=self._effect_context(record),  # type: ignore[arg-type]
                granted_kinds=frozenset(),
                effect={
                    "kind": "github_mark_ready",
                    "target": "owner/repository#82",
                    "status": "NOT_ATTEMPTED",
                    "idempotency_key": "ungranted",
                },
            )

    def test_effect_grant_is_digested_into_owner_approval_identity(self):
        def effect_key_digest(pull_number: int) -> str:
            settings = self._grant_settings(pull_number=pull_number)
            application = DefaultPrimaryApplication(
                settings,
                coordinator=None,  # type: ignore[arg-type]
                services=None,  # type: ignore[arg-type]
                authority=DefaultPrimaryAuthority("a" * 40, digest("authority")),
            )
            intake = SlackIntake(
                source_event_id="effect-grant-event",
                channel_id="C0BAV9CMC80",
                user_id="U123",
                text="Implement the bounded change and prepare a draft PR",
                thread_ts="1787450001.123456",
            )
            record = TaskRecord(
                task_id="DIR-LG-GRANT",
                status=TaskStatus.QUEUED,
                intake=intake,
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
            plan = preview_plan(intake.text, settings)
            assignments = list(plan.assignments)
            assignments[0] = assignments[0].model_copy(update={"allow_draft_pr": True})
            record.plan = plan.model_copy(update={"assignments": assignments})
            request = application._request(record)
            self.assertTrue(request.external_effect_required)
            assert request.effect_key_digest is not None
            return request.effect_key_digest

        configured = effect_key_digest(82)
        self.assertEqual(configured, effect_key_digest(82))
        # A different approved pull request is a different approval identity.
        self.assertNotEqual(configured, effect_key_digest(83))
        self.assertNotEqual(configured, effect_key_digest(0))

    def test_redirected_repository_pull_or_revision_fails_closed(self):
        settings = self._grant_settings()
        record = self._record(settings)
        grant = build_typed_external_effect_grant(
            settings, DefaultPrimaryAuthority("a" * 40, digest("authority"))
        )
        assert grant is not None

        def bind(effect, context=None):
            return _typed_effect_binding(
                grant=grant,
                record=record,
                context=context or self._effect_context(record),  # type: ignore[arg-type]
                granted_kinds=frozenset({"github_mark_ready", "slack_post"}),
                effect=effect,
            )

        def proposal(target, key):
            return {
                "kind": "github_mark_ready",
                "target": target,
                "status": "NOT_ATTEMPTED",
                "idempotency_key": key,
            }

        with self.assertRaisesRegex(RuntimeError, "outside the repository allowlist"):
            bind(proposal("attacker/fork#82", "redirected-repository"))
        with self.assertRaisesRegex(RuntimeError, "not the configured pull request"):
            bind(proposal("owner/repository#83", "redirected-pull-request"))
        with self.assertRaisesRegex(
            RuntimeError, "head or base is outside Director authority"
        ):
            bind(
                proposal("owner/repository#82", "redirected-head"),
                context=self._effect_context(record, candidate="c" * 40),
            )
        with self.assertRaisesRegex(RuntimeError, "Slack effect target changed"):
            bind(
                {
                    "kind": "slack_post",
                    "target": "C0000000000",
                    "status": "NOT_ATTEMPTED",
                    "idempotency_key": "redirected-channel",
                }
            )


class DefaultPrimaryPinnedAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "pinned-authority"
        self.root.mkdir()

        self.git("init", "-b", "main")
        self.git("config", "user.email", "pinned-authority@example.test")
        self.git("config", "user.name", "Pinned Authority Test")
        (self.root / "README.md").write_text("pinned authority\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "pinned authority")
        self.git("remote", "add", "origin", "https://github.com/owner/pinned.git")
        self.commit_sha = self.git("rev-parse", "HEAD")
        self.tree_sha = self.git("rev-parse", "HEAD^{tree}")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=self.root,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr)
        return completed.stdout.strip()

    def settings(self, **updates) -> Settings:
        values = {
            "repository_snapshot_root": str(self.root),
            "repository_allowlist": ("owner/pinned",),
            "allowed_starting_refs": ("main",),
            "langgraph_primary_pinned_commit_sha": self.commit_sha,
            "langgraph_primary_pinned_tree_sha": self.tree_sha,
        }
        values.update(updates)
        return replace(Settings(), **values)

    def test_exact_pins_do_not_require_a_remote_tracking_ref(self) -> None:
        authority = resolve_default_primary_authority(self.settings())

        self.assertEqual(self.commit_sha, authority.starting_sha)

        with self.assertRaisesRegex(RuntimeError, "could not be verified"):
            resolve_default_primary_authority(
                self.settings(
                    langgraph_primary_pinned_commit_sha="",
                    langgraph_primary_pinned_tree_sha="",
                )
            )

    def test_real_default_planning_binds_exact_pins_without_tracking_ref(self) -> None:
        settings = self.settings()
        services = DefaultDryRunServices(settings)
        record = TaskRecord(
            task_id="DIR-PINNED-PLAN",
            status=TaskStatus.QUEUED,
            intake=SlackIntake(
                source_event_id="event-pinned-plan",
                channel_id="CPINNED",
                thread_ts="1720000000.000002",
                user_id="UOWNER",
                text="Project: default\nImplement the bounded pinned change.",
            ),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        services.register(record)
        context = HandlerContext(
            task_id=record.task_id,
            source_event_id=record.intake.source_event_id,
            correlation_id="corr-pinned-plan",
            intake_digest=digest("intake"),
            scope_digest=digest("scope"),
            authority_snapshot_digest=digest("authority"),
            work_packet_digest=None,
            idempotency_key_digest=digest("idempotency"),
            starting_sha=self.commit_sha,
            candidate_sha=self.commit_sha,
            plan_digest=None,
            scheduled_producer_id=None,
            provider_attempt_digest=None,
            provider_dispatch_status="NOT_STARTED",
            provider_result_digest=None,
            producer_id=None,
            reviewer_id=None,
            revision_count=0,
            budget_limit_units=10,
            budget_used_units=0,
            effect_key_digest=None,
            effect_attempt_digest=None,
            writer_owner_id="langgraph",
        )

        result = services.plan(context)
        planned = services._record(record.task_id).plan

        self.assertTrue(result.work_packet_digest.startswith("sha256:"))
        assert planned is not None and planned.prestage is not None
        self.assertEqual(self.commit_sha, planned.prestage.base_sha)
        self.assertEqual(self.commit_sha, planned.prestage.branch)
        self.assertEqual(str(self.root.resolve()), planned.prestage.worktree)
        self.assertTrue(
            all(item.starting_ref == self.commit_sha for item in planned.assignments)
        )

    def test_pins_preserve_origin_clean_head_ref_and_tree_checks(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "remote does not match"):
            resolve_default_primary_authority(
                self.settings(repository_allowlist=("other/pinned",))
            )

        self.git(
            "remote",
            "set-url",
            "origin",
            "https://example.invalid/github.com/owner/pinned.git",
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "remote does not match"):
                resolve_default_primary_authority(self.settings())
        finally:
            self.git(
                "remote",
                "set-url",
                "origin",
                "https://github.com/owner/pinned.git",
            )

        (self.root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "must be clean"):
            resolve_default_primary_authority(self.settings())
        (self.root / "untracked.txt").unlink()

        with self.assertRaisesRegex(RuntimeError, "HEAD and starting ref"):
            resolve_default_primary_authority(
                self.settings(langgraph_primary_pinned_commit_sha="f" * 40)
            )

        with self.assertRaisesRegex(RuntimeError, "computed tree"):
            resolve_default_primary_authority(
                self.settings(langgraph_primary_pinned_tree_sha="e" * 40)
            )


class DefaultPrimaryApplicationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests"
            )

    def setUp(self) -> None:
        self.schema = f"director_default_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(
            TEST_DSN,
            schema=self.schema,
            allow_insecure_local_transport=True,
        )
        self.settings = replace(
            Settings(),
            app_env="default-test",
            allow_paid_runs=False,
            project_allowlist=("default",),
            repository_allowlist=("owner/director-orchestrator",),
            allowed_starting_refs=("main",),
        )
        self.services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        self.coordinator = PrimaryTaskCoordinator(
            self.handle,
            self.services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=(
                self.services.application_projection
            ),
        )
        self.coordinator.activate_new_intake(expected_generation=0)
        self.application = DefaultPrimaryApplication(
            self.settings,
            self.coordinator,
            self.services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    @staticmethod
    def intake() -> SlackIntake:
        return SlackIntake(
            source_event_id="event-default-1",
            channel_id="CDEFAULT",
            thread_ts="1720000000.000001",
            user_id="UOWNER",
            text="Project: default\nCreate a bounded implementation plan.",
        )

    def test_paid_off_intake_and_read_are_graph_owned(self):
        record = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )

        self.assertTrue(record.task_id.startswith("DIR-LG-"))
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, record.status)
        self.assertIsNotNone(record.plan)
        self.assertIn("no provider", record.message or "")
        assert record.plan is not None
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        expected_packet = self.services._packet_router.work_packet(record, assignment)
        graph_state = self.coordinator._runtime.compact_state(record.task_id)
        self.assertEqual(
            document_digest(expected_packet), graph_state["work_packet_digest"]
        )
        self.assertNotEqual(
            self.application._request(record).work_packet_digest,
            graph_state["work_packet_digest"],
        )
        restored = self.application.get_task(record.task_id)
        self.assertEqual(record.model_dump(mode="json"), restored.model_dump(mode="json"))
        readiness = self.coordinator.readiness()
        self.assertEqual(1, readiness.graph_task_count)
        self.assertTrue(readiness.writer_lease_ready)
        self.assertTrue(readiness.legacy_new_intake_disabled)

    def test_provider_attempt_ledger_orders_formats_and_restart_blocks_ambiguity(self):
        # Disposable fixture history only; this does not initialize production.
        from tests.test_primary_workload import HISTORY
        from director_orchestrator.primary_workload import POLICY_DIGEST, POLICY_VERSION
        with self.handle.claim_store._pool.connection() as connection:
            for index, provider in enumerate(HISTORY):
                connection.execute(f"""INSERT INTO {self.schema}.director_primary_workload_assignment
                    (task_id, assignment_id, provider_lane, reservation_digest,
                     fencing_generation, policy_version, policy_digest)
                    VALUES (%s,%s,%s,%s,1,%s,%s)""",
                    ("test-workload-fixture", str(index), provider, digest("fixture"),
                     POLICY_VERSION, POLICY_DIGEST))
        record = self.application.create_intake(
            self.intake().model_copy(update={"source_event_id": "attempt-ledger"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        reservation = self.coordinator.reserve_parallel_ready(
            task_id=record.task_id,
            policy=self.application._parallel_policy(),
        )[0]
        first = self.coordinator.reserve_provider_attempt(
            reservation=reservation,
            attempt_ordinal=1,
            format_roster=(("json", "api"), ("text", "cli")),
            idempotency_key_digest=digest("attempt-1-key"),
            budget_evidence_digest=digest("budget"),
        )
        self.assertEqual(("json", "api"), (first.format_id, first.transport_id))
        self.coordinator.record_provider_attempt(
            attempt=first,
            status=ParallelAssignmentStatus.FAILED,
            result_digest=digest("failed-result"),
            reason_code="authenticated_failure",
        )

        restarted = PostgresParallelDispatch(self.handle.claim_store)
        writer = self.handle.claim_store.current_task_writer(record.task_id)
        assert writer is not None
        second = restarted.reserve_provider_attempt(
            reservation=reservation,
            writer=writer,
            attempt_ordinal=2,
            format_roster=(("json", "api"), ("text", "cli")),
            idempotency_key_digest=digest("attempt-2-key"),
            budget_evidence_digest=digest("budget"),
        )
        self.assertEqual(("text", "cli"), (second.format_id, second.transport_id))
        with self.assertRaisesRegex(ParallelDispatchError, "reconciliation required"):
            restarted.reserve_provider_attempt(
                reservation=reservation,
                writer=writer,
                attempt_ordinal=2,
                format_roster=(("json", "api"), ("text", "cli")),
                idempotency_key_digest=digest("attempt-2-key"),
                budget_evidence_digest=digest("budget"),
            )
        restarted.record_provider_attempt(
            attempt=second,
            writer=writer,
            status=ParallelAssignmentStatus.AMBIGUOUS,
            result_digest=None,
            reason_code="provider_outcome_unknown",
        )
        with self.assertRaisesRegex(ParallelDispatchError, "must reconcile"):
            restarted.reserve_provider_attempt(
                reservation=reservation,
                writer=writer,
                attempt_ordinal=3,
                format_roster=(("json", "api"), ("text", "cli")),
                idempotency_key_digest=digest("attempt-3-key"),
                budget_evidence_digest=digest("budget"),
            )
        with self.handle.pool.connection() as connection:
            rows = connection.execute(
                f"SELECT attempt_ordinal, event_sequence, status, format_id, "
                f"transport_id, outcome_evidence_digest "
                f"FROM {self.schema}.director_primary_provider_attempt "
                "WHERE task_id = %s ORDER BY attempt_ordinal, event_sequence",
                (record.task_id,),
            ).fetchall()
        self.assertEqual(4, len(rows))
        self.assertEqual([1, 1, 2, 2], [row["attempt_ordinal"] for row in rows])
        self.assertTrue(rows[1]["outcome_evidence_digest"])
        self.assertTrue(rows[3]["outcome_evidence_digest"])

    def test_repository_free_research_is_approval_bound_and_restart_stable(self):
        intake = self.intake().model_copy(
            update={
                "source_event_id": "event-default-research",
                "text": "Project: default\nResearch current public sources.",
            }
        )

        record = self.application.create_intake(
            intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        duplicate = self.application.create_intake(
            intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        recovered = self.application.recover(record.task_id)
        projection = self.coordinator.get_projection(record.task_id)

        for result in (record, duplicate, recovered):
            self.assertEqual(record.task_id, result.task_id)
            self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, result.status)
        assert record.plan is not None
        research = min(record.plan.assignments, key=lambda item: item.sequence)
        review = max(record.plan.assignments, key=lambda item: item.sequence)
        self.assertEqual("perplexity", research.agent.value)
        self.assertIsNone(research.repository)
        self.assertTrue(research.requires_approval)
        self.assertEqual([research.sequence], review.depends_on)
        assert projection is not None
        self.assertIsNone(projection.projection["terminal_classification"])
        self.assertEqual("execution_approval_required", projection.projection["reason_code"])
        self.assertEqual("NOT_STARTED", projection.projection["provider_dispatch_status"])
        self.assertEqual("NOT_REQUIRED", projection.projection["effect_status"])
        state = self.coordinator._runtime.compact_state(record.task_id)
        self.assertIsNotNone(state["work_packet_digest"])
        self.assertIsNotNone(state["plan_work_packet_binding_digest"])
        pending = projection.projection["pending_approval"]
        self.assertEqual("execution", pending["kind"])

        restarted_services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        restarted_coordinator = PrimaryTaskCoordinator(
            self.handle,
            restarted_services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=restarted_services.application_projection,
        )
        restarted_coordinator.adopt_or_activate_new_intake()
        restarted_application = DefaultPrimaryApplication(
            self.settings,
            restarted_coordinator,
            restarted_services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )

        restarted = restarted_application.recover(record.task_id)
        restarted_projection = restarted_coordinator.get_projection(record.task_id)
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, restarted.status)
        assert restarted_projection is not None
        self.assertEqual(
            "execution_approval_required",
            restarted_projection.projection["reason_code"],
        )

    def parallel_plan(self):
        plan = preview_plan(self.intake().text, self.settings)
        template = plan.assignments[0]
        assignments = [
            template.model_copy(
                update={
                    "sequence": 1,
                    "agent": ProviderName.CLAUDE,
                    "task": "Independent left assignment",
                    "depends_on": [],
                    "retry_limit": 3,
                }
            ),
            template.model_copy(
                update={
                    "sequence": 2,
                    "agent": ProviderName.CURSOR,
                    "task": "Independent right assignment",
                    "depends_on": [],
                    "retry_limit": 3,
                }
            ),
            template.model_copy(
                update={
                    "sequence": 3,
                    "agent": ProviderName.OPENAI,
                    "task": "Dependent aggregation assignment",
                    "depends_on": [1, 2],
                    "retry_limit": 3,
                }
            ),
        ]
        return plan.model_copy(update={"assignments": assignments})

    def approve(self, application, coordinator, task_id):
        projection = coordinator.get_projection(task_id)
        assert projection is not None
        approval = projection.projection["pending_approval"]
        return application.resume_approval(
            ApprovalResume(
                task_id=task_id,
                kind=ApprovalKind(approval["kind"]),
                request_digest=approval["request_digest"],
                approval_event_id=f"approve-{task_id.lower()}",
                decision=ApprovalDecision.APPROVE,
            )
        )

    def use_parallel_graph_completion_services(self, dispatcher):
        settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        services = DefaultOfflineSimulationServices(
            settings, packet_router=packet_router(settings)
        )
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=dispatcher,
        )
        self.settings = settings
        self.services = services
        self.coordinator = coordinator
        self.application = application

    def same_lane_review_plan(self):
        """A plan whose review barrier reuses the first-wave producer's lane."""

        plan = self.parallel_plan()
        assignments = list(plan.assignments)
        assignments[2] = assignments[2].model_copy(
            update={
                # Same provider lane as assignment 1, which is exactly the shape
                # a lane-matching lookup resolves to the wrong plan member.
                "agent": ProviderName.CLAUDE,
                "kind": AssignmentKind.INDEPENDENT_REVIEW,
                "task": "Independent review of the complete first wave",
            }
        )
        return plan.model_copy(update={"assignments": assignments})

    def _record_packet_artifacts(self, record, assignment) -> str:
        """Persist one real work/result artifact pair and return its digest."""

        assignment_id = self.services._packet_router._assignment_id(record, assignment)
        work = self.services._packet_router.planned_work_packet(record, assignment)
        writer = self.handle.claim_store.current_task_writer(record.task_id)
        assert writer is not None
        self.handle.claim_store.record_work_packet_artifact(
            task_id=record.task_id,
            assignment_id=assignment_id,
            packet=work,
            owner_token=writer.owner_token,
            fencing_generation=writer.fencing_generation,
        )
        result = {
            "schema_version": "1.0",
            "task_id": record.task_id,
            "assignment_id": assignment_id,
            "idempotency_key": work["idempotency_key"],
            "authority_snapshot_digest": work["authority_snapshot_digest"],
            "correlation": dict(work["correlation"]),
            "provider": assignment.agent.value,
            "run_reference": f"run-{assignment.sequence}",
            "status": "SUCCEEDED",
            "outcome": f"assignment {assignment.sequence} evidence",
            "artifacts": [],
            "checks": [],
            "acceptance": [
                {"check": str(check), "status": "PASS", "evidence": "recorded"}
                for check in work["acceptance_checks"]
            ],
            "risks": [],
            "blockers": [],
            "requested_decisions": [],
            "side_effects": [],
            "usage": None,
            "revision_recommended": False,
        }
        persisted = self.handle.claim_store.record_result_packet_artifact(
            task_id=record.task_id,
            assignment_id=assignment_id,
            packet=result,
            owner_token=writer.owner_token,
            fencing_generation=writer.fencing_generation,
        )
        return persisted.document_digest

    def test_packet_result_resolves_the_review_not_a_same_lane_producer(self):
        digests: dict[int, str] = {}

        def dispatch(reservation):
            record = self.services._record(reservation.task_id)
            assignment = next(
                item
                for item in record.plan.assignments
                if item.sequence == reservation.sequence
            )
            recorded = self._record_packet_artifacts(record, assignment)
            digests[reservation.sequence] = recorded
            # The dependent review packet can only be built once its upstream
            # outcomes are accepted, exactly as the live dispatcher records them.
            with self.services._lock:
                stored = self.services._records[reservation.task_id]
                stored.assignment_outcomes.append(
                    AssignmentOutcome(
                        assignment_sequence=assignment.sequence,
                        provider=assignment.agent,
                        run_id=f"run-{assignment.sequence}",
                        result=f"assignment {assignment.sequence} evidence",
                        review_summary=f"assignment {assignment.sequence} evidence",
                        provider_result_digest=recorded.removeprefix("sha256:"),
                    )
                )
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                recorded,
                "provider_result_authenticated",
            )

        self.use_parallel_graph_completion_services(dispatch)
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.same_lane_review_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "same-lane-review"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        self.approve(self.application, self.coordinator, pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        state = self.coordinator._runtime.compact_state(pending.task_id)
        record = self.application.get_task(pending.task_id)
        self.services.register(record)
        self.addCleanup(self.services.discard, pending.task_id)

        producer = min(record.plan.assignments, key=lambda item: item.sequence)
        review = max(record.plan.assignments, key=lambda item: item.sequence)
        self.assertEqual(
            _assignment_provider_lane(producer), _assignment_provider_lane(review)
        )
        self.assertNotEqual(digests[producer.sequence], digests[review.sequence])
        # The graph carries the aggregate digest, never an individual one.
        self.assertEqual(aggregate.aggregate_digest, state["provider_result_digest"])
        self.assertNotIn(
            state["provider_result_digest"], set(digests.values())
        )

        resolver = build_primary_packet_result_resolver(
            handle=self.handle, services=self.services
        )
        binding = resolver(
            self.handler_context(
                pending.task_id,
                scheduled_producer_id=str(state["scheduled_producer_id"]),
                provider_result_digest=str(state["provider_result_digest"]),
            )
        )

        assert binding is not None
        review_assignment_id = self.services._packet_router._assignment_id(
            record, review
        )
        self.assertEqual(review.sequence, binding.assignment.sequence)
        self.assertEqual(review_assignment_id, binding.assignment_id)
        self.assertEqual(
            digests[review.sequence], binding.outcome.result_packet_digest
        )
        self.assertEqual(
            aggregate.aggregate_digest, binding.expected_context_digest
        )
        self.assertNotEqual(
            digests[producer.sequence], binding.outcome.result_packet_digest
        )

        # A context carrying anything but the durable aggregate fails closed.
        self.assertIsNone(
            resolver(
                self.handler_context(
                    pending.task_id,
                    scheduled_producer_id=str(state["scheduled_producer_id"]),
                    provider_result_digest=digests[producer.sequence],
                )
            )
        )

    @staticmethod
    def handler_context(
        task_id: str,
        *,
        scheduled_producer_id: str,
        provider_result_digest: str,
    ) -> HandlerContext:
        return HandlerContext(
            task_id=task_id,
            source_event_id="source-same-lane",
            correlation_id="correlation-same-lane",
            intake_digest=digest("intake"),
            scope_digest=digest("scope"),
            authority_snapshot_digest=digest("authority"),
            work_packet_digest=digest("work"),
            idempotency_key_digest=digest("idempotency"),
            starting_sha=SHA,
            candidate_sha=SHA,
            plan_digest=digest("plan"),
            scheduled_producer_id=scheduled_producer_id,
            provider_attempt_digest=digest("attempt"),
            provider_dispatch_status="COMPLETED",
            provider_result_digest=provider_result_digest,
            producer_id=scheduled_producer_id,
            reviewer_id=None,
            revision_count=0,
            budget_limit_units=10,
            budget_used_units=1,
            effect_key_digest=None,
            effect_attempt_digest=None,
            writer_owner_id="langgraph",
        )

    def test_parallel_default_dispatches_all_ready_then_dependency(self):
        barrier = threading.Barrier(2)
        entered: list[int] = []
        completed: list[int] = []
        worker_threads: set[int] = set()
        lock = threading.Lock()

        def dispatch(reservation):
            with lock:
                entered.append(reservation.sequence)
                worker_threads.add(threading.get_ident())
                if reservation.sequence == 3:
                    self.assertEqual({1, 2}, set(completed))
            if reservation.sequence in {1, 2}:
                barrier.wait(timeout=5)
            with lock:
                completed.append(reservation.sequence)
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest(f"parallel-result-{reservation.sequence}"),
                "provider_result_authenticated",
            )

        self.use_parallel_graph_completion_services(dispatch)
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake(),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        finished = self.approve(self.application, self.coordinator, pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        with self.handle.pool.connection() as connection:
            retry_limits = connection.execute(
                f"SELECT retry_limit FROM {self.schema}.director_primary_parallel_assignment "
                "WHERE task_id = %s ORDER BY sequence",
                (pending.task_id,),
            ).fetchall()

        self.assertEqual([1, 2, 3], sorted(entered))
        self.assertEqual([3, 3, 3], [row["retry_limit"] for row in retry_limits])
        self.assertGreaterEqual(len(worker_threads), 2)
        self.assertTrue(aggregate.terminal)
        self.assertTrue(aggregate.succeeded)
        self.assertEqual(TaskStatus.DONE, finished.status)
        self.assertTrue(all(value == "SUCCEEDED" for value in aggregate.statuses.values()))
        projection = self.coordinator.get_projection(pending.task_id)
        assert projection is not None
        state = self.coordinator._runtime.compact_state(pending.task_id)
        self.assertEqual(
            aggregate.aggregate_digest,
            state["provider_result_digest"],
        )
        self.assertIn("exact_sha_review", projection.projection["visited_nodes"])
        self.assertIn(
            "ci_security_completion", projection.projection["visited_nodes"]
        )
        self.assertEqual("SUCCEEDED", projection.projection["terminal_classification"])

    def test_requested_enterprise_capability_persists_owner_hold_before_registration(self):
        settings = replace(self.settings, langgraph_primary_offline_simulation_enabled=True,
            trusted_provider_cost_ranks_json='{"claude":2,"cursor":1,"grok":3}')
        services = DefaultOfflineSimulationServices(settings, packet_router=packet_router(settings), live_composition=True)
        services.available_provider_lanes = ("openai", "claude", "cursor", "grok")
        services.provider_lane_ready = lambda _provider: True
        coordinator = PrimaryTaskCoordinator(self.handle, services.handlers(),
            owner_token="default-primary-test-owner", checkpoint_namespace="capability-hold-v1",
            application_projection_provider=services.application_projection)
        coordinator.adopt_or_activate_new_intake()
        def forbidden(_reservation):
            self.fail("capability hold must precede provider dispatch")
        application = DefaultPrimaryApplication(settings, coordinator, services,
            DefaultPrimaryAuthority(SHA, digest("authority")), parallel_dispatcher=forbidden)
        held = application.create_intake(self.intake().model_copy(update={
            "primary_required_capabilities": ("enterprise_context",),
        }), intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        self.assertEqual(held.status, TaskStatus.OWNER_DECISION_REQUIRED)
        self.assertIn("primary_roster_primary_enterprise_accounting_authority_required", held.workload_selection_hold_reasons)
        with self.handle.pool.connection() as connection:
            count = connection.execute(f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_attempt").fetchone()["n"]
        self.assertEqual(count, 0)
        # A stale policy digest is retained and surfaced as an owner hold.
        from director_orchestrator.primary_workload import POLICY_VERSION
        with self.handle.pool.connection() as connection:
            connection.execute(f"""INSERT INTO {self.schema}.director_primary_workload_assignment
                (task_id, assignment_id, provider_lane, reservation_digest,
                 fencing_generation, policy_version, policy_digest)
                VALUES ('old-history','old-assignment','claude',%s,1,%s,%s)""",
                (digest("old-reservation"), POLICY_VERSION, digest("old-policy")))
        incompatible = application.create_intake(self.intake().model_copy(update={
            "source_event_id": "incompatible-history", "primary_required_capabilities": (),
        }), intake_authentication=IntakeAuthentication.BEARER_DRY_RUN)
        self.assertEqual(incompatible.status, TaskStatus.OWNER_DECISION_REQUIRED)
        self.assertEqual(incompatible.workload_selection_hold_reasons,
                         ["primary_roster_workload_history_invalid"])
        with self.handle.pool.connection() as connection:
            row = connection.execute(f"SELECT policy_digest FROM {self.schema}.director_primary_workload_assignment WHERE task_id='old-history'").fetchone()
        self.assertEqual(row["policy_digest"], digest("old-policy"))

    def test_live_primary_postgres_dispatches_minimal_independent_capability_roster(self):
        settings = replace(
            self.settings,
            langgraph_primary_offline_simulation_enabled=True,
            trusted_provider_cost_ranks_json='{"claude":2,"cursor":1,"grok":3}',
        )
        first_wave_size = 1
        barrier = threading.Barrier(first_wave_size)
        completed_first_wave: set[int] = set()
        entered: list[tuple[int, str]] = []
        worker_threads: set[int] = set()
        lock = threading.Lock()

        def dispatch(reservation):
            with lock:
                entered.append((reservation.sequence, reservation.provider_id))
                worker_threads.add(threading.get_ident())
                if reservation.sequence == first_wave_size + 1:
                    self.assertEqual(
                        set(range(1, first_wave_size + 1)), completed_first_wave
                    )
            if reservation.sequence <= first_wave_size:
                barrier.wait(timeout=10)
                with lock:
                    completed_first_wave.add(reservation.sequence)
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest(f"all-lane:{reservation.assignment_id}"),
                "provider_result_authenticated",
            )

        services = DefaultOfflineSimulationServices(
            settings,
            packet_router=packet_router(settings),
            live_composition=True,
        )
        services.available_provider_lanes = ("openai", "claude", "cursor", "grok")
        services.provider_lane_ready = lambda _provider: True
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-all-lanes-v1",
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=dispatch,
        )

        pending = application.create_intake(
            self.intake().model_copy(update={"source_event_id": "all-lane-live-wave"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        assert pending.plan is not None
        first_wave = pending.plan.assignments[:first_wave_size]
        review = pending.plan.assignments[first_wave_size]
        self.assertEqual(
            {"cursor"},
            {item.primary_provider_lane for item in first_wave},
        )
        self.assertTrue(all(not item.depends_on for item in first_wave))
        self.assertEqual(
            list(range(1, first_wave_size + 1)), review.depends_on
        )

        finished = self.approve(application, coordinator, pending.task_id)
        aggregate = coordinator.parallel_aggregate(pending.task_id)
        with self.handle.pool.connection() as connection:
            rows = connection.execute(
                f"SELECT sequence, provider_id, status FROM "
                f"{self.schema}.director_primary_parallel_assignment "
                "WHERE task_id = %s ORDER BY sequence",
                (pending.task_id,),
            ).fetchall()

        self.assertEqual(first_wave_size + 1, len(rows))
        self.assertEqual(
            {"cursor"},
            {row["provider_id"] for row in rows[:first_wave_size]},
        )
        self.assertGreaterEqual(len(worker_threads), first_wave_size)
        self.assertTrue(all(row["status"] == "SUCCEEDED" for row in rows))
        self.assertTrue(aggregate.succeeded)
        self.assertEqual(TaskStatus.DONE, finished.status)

    def test_parallel_restart_resumes_pending_rows_without_duplicate_dispatch(self):
        self.use_parallel_graph_completion_services(None)
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-restart"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        self.approve(self.application, self.coordinator, pending.task_id)
        self.assertTrue(
            all(
                value == "PENDING"
                for value in self.coordinator.parallel_aggregate(pending.task_id).statuses.values()
            )
        )

        calls: list[str] = []

        def dispatch(reservation):
            calls.append(reservation.assignment_id)
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest("restart:" + reservation.assignment_id),
                "provider_result_authenticated",
            )

        restarted_services = DefaultOfflineSimulationServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        restarted_coordinator = PrimaryTaskCoordinator(
            self.handle,
            restarted_services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=restarted_services.application_projection,
        )
        restarted_coordinator.adopt_or_activate_new_intake()
        restarted = DefaultPrimaryApplication(
            self.settings,
            restarted_coordinator,
            restarted_services,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=dispatch,
        )

        first = restarted.recover(pending.task_id)
        second = restarted.recover(pending.task_id)

        self.assertEqual(3, len(calls))
        self.assertEqual(3, len(set(calls)))
        self.assertEqual(TaskStatus.DONE, first.status)
        self.assertEqual(TaskStatus.DONE, second.status)

    def test_parallel_task_is_not_terminal_until_every_assignment_settles(self):
        def dispatch(reservation):
            if reservation.sequence == 2:
                return ParallelDispatchOutcome(
                    ParallelAssignmentStatus.AMBIGUOUS,
                    None,
                    "provider_outcome_unknown",
                )
            return ParallelDispatchOutcome(
                ParallelAssignmentStatus.SUCCEEDED,
                digest(f"partial-{reservation.sequence}"),
                "provider_result_authenticated",
            )

        self.application._parallel_dispatcher = dispatch
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-partial"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        result = self.approve(self.application, self.coordinator, pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)

        self.assertFalse(aggregate.terminal)
        self.assertFalse(aggregate.succeeded)
        self.assertEqual(1, aggregate.ambiguous_slot_count)
        self.assertNotEqual(TaskStatus.DONE, result.status)
        self.assertIn("PENDING", aggregate.statuses.values())

    def test_parallel_restart_classifies_active_without_redispatch(self):
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-active"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        self.application.settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        self.approve(self.application, self.coordinator, pending.task_id)
        reservations = self.coordinator.reserve_parallel_ready(
            task_id=pending.task_id,
            policy=self.application._parallel_policy(),
        )
        self.assertEqual(2, len(reservations))

        calls: list[str] = []
        restarted_services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        restarted_coordinator = PrimaryTaskCoordinator(
            self.handle,
            restarted_services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=restarted_services.application_projection,
        )
        restarted_coordinator.adopt_or_activate_new_intake()
        restarted = DefaultPrimaryApplication(
            self.settings,
            restarted_coordinator,
            restarted_services,
            DefaultPrimaryAuthority(SHA, digest("authority")),
            parallel_dispatcher=lambda reservation: calls.append(
                reservation.assignment_id
            ),  # type: ignore[arg-type]
        )

        result = restarted.recover(pending.task_id)
        aggregate = restarted_coordinator.parallel_aggregate(pending.task_id)
        projection = restarted_coordinator.get_projection(pending.task_id)

        self.assertEqual([], calls)
        self.assertEqual(2, aggregate.ambiguous_slot_count)
        self.assertFalse(aggregate.terminal)
        self.assertNotEqual(TaskStatus.DONE, result.status)
        assert projection is not None
        self.assertEqual(2, projection.projection["parallel_ambiguous_slot_count"])

    def test_parallel_cancellation_fences_new_work_before_graph_cancel(self):
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-cancel-race"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        active = self.coordinator.reserve_parallel_ready(
            task_id=pending.task_id,
            policy=self.application._parallel_policy(),
        )
        self.assertEqual(2, len(active))

        cancelled = self.application.cancel(pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        self.assertEqual(TaskStatus.FAILED, cancelled.status)
        self.assertEqual(2, aggregate.ambiguous_slot_count)
        self.assertEqual(
            (),
            self.coordinator.reserve_parallel_ready(
                task_id=pending.task_id,
                policy=self.application._parallel_policy(),
            ),
        )
        projection = self.coordinator.get_projection(pending.task_id)
        assert projection is not None
        self.assertEqual("CANCELLED", projection.projection["terminal_classification"])

    def test_recover_repairs_missing_parallel_registration_after_intake_crash(self):
        pending = self.application.create_intake(
            self.intake().model_copy(update={"source_event_id": "intake-registration-repair"}),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        with self.handle.pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    f"DELETE FROM {self.schema}.director_primary_parallel_assignment "
                    "WHERE task_id = %s",
                    (pending.task_id,),
                )
                connection.execute(
                    f"DELETE FROM {self.schema}.director_primary_parallel_batch "
                    "WHERE task_id = %s",
                    (pending.task_id,),
                )

        repaired = self.application.recover(pending.task_id)
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        self.assertEqual(pending.task_id, repaired.task_id)
        self.assertTrue(aggregate.statuses)
        projection = self.coordinator.get_projection(pending.task_id)
        assert projection is not None
        self.assertEqual(
            projection.projection["parallel_plan_digest"],
            self.coordinator._runtime.compact_state(pending.task_id)[
                "parallel_plan_digest"
            ],
        )

    def test_callback_settles_only_exact_assignment_attempt_not_graph_edge(self):
        # Disposable fixture history only; this does not initialize production.
        from tests.test_primary_workload import HISTORY
        from director_orchestrator.primary_workload import POLICY_DIGEST, POLICY_VERSION
        with self.handle.claim_store._pool.connection() as connection:
            for index, provider in enumerate(HISTORY):
                connection.execute(f"""INSERT INTO {self.schema}.director_primary_workload_assignment
                    (task_id, assignment_id, provider_lane, reservation_digest,
                     fencing_generation, policy_version, policy_digest)
                    VALUES (%s,%s,%s,%s,1,%s,%s)""",
                    ("test-workload-fixture", str(index), provider, digest("fixture"),
                     POLICY_VERSION, POLICY_DIGEST))
        with patch(
            "director_orchestrator.primary_default.preview_plan",
            return_value=self.parallel_plan(),
        ):
            pending = self.application.create_intake(
                self.intake().model_copy(update={"source_event_id": "parallel-callback"}),
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        self.application.settings = replace(
            self.settings, langgraph_primary_offline_simulation_enabled=True
        )
        self.approve(self.application, self.coordinator, pending.task_id)
        reservations = self.coordinator.reserve_parallel_ready(
            task_id=pending.task_id,
            policy=self.application._parallel_policy(),
        )
        first, second = reservations
        attempt = self.coordinator.reserve_provider_attempt(
            reservation=first,
            attempt_ordinal=1,
            format_roster=(("json", "callback"),),
            idempotency_key_digest=digest("callback-key"),
            budget_evidence_digest=digest("callback-budget"),
        )
        command = ProviderSubgraphResult(
            provider_attempt_digest=attempt.attempt_digest,
            idempotency_key_digest=attempt.idempotency_key_digest,
            authority_snapshot_digest=digest("callback-authority"),
            producer_id=first.provider_id,
            disposition=ProviderDisposition.COMPLETED,
            result_digest=digest("callback-result"),
            candidate_sha=SHA,
            reason_code="callback_authenticated",
        )
        with self.assertRaisesRegex(ParallelDispatchError, "unknown"):
            self.application.record_provider_callback(
                task_id=pending.task_id,
                assignment_id=second.assignment_id,
                provider_id=second.provider_id,
                provider_run_id="run-wrong-assignment",
                command=command,
            )

        self.application.record_provider_callback(
            task_id=pending.task_id,
            assignment_id=first.assignment_id,
            provider_id=first.provider_id,
            provider_run_id="run-exact-attempt",
            command=command,
        )
        # Exact redelivery is idempotent; the same attempt with another remote
        # run identity is a binding conflict even when every other field agrees.
        self.application.record_provider_callback(
            task_id=pending.task_id,
            assignment_id=first.assignment_id,
            provider_id=first.provider_id,
            provider_run_id="run-exact-attempt",
            command=command,
        )
        with self.assertRaisesRegex(ParallelDispatchError, "run identity conflicts"):
            self.application.record_provider_callback(
                task_id=pending.task_id,
                assignment_id=first.assignment_id,
                provider_id=first.provider_id,
                provider_run_id="run-rebound",
                command=command,
            )
        aggregate = self.coordinator.parallel_aggregate(pending.task_id)
        self.assertEqual("SUCCEEDED", aggregate.statuses[first.assignment_id])
        self.assertEqual("ACTIVE", aggregate.statuses[second.assignment_id])
        state = self.coordinator._runtime.compact_state(pending.task_id)
        self.assertIsNone(state["provider_result_receipt_digest"])
        with self.handle.pool.connection() as connection:
            callback_rows = connection.execute(
                f"SELECT provider_run_id_digest FROM "
                f"{self.schema}.director_primary_provider_attempt "
                "WHERE task_id = %s AND assignment_id = %s "
                "AND event_sequence = 3",
                (pending.task_id, first.assignment_id),
            ).fetchall()
        self.assertEqual(1, len(callback_rows))
        self.assertEqual(
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {"provider_run_id": "run-exact-attempt"},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            callback_rows[0]["provider_run_id_digest"],
        )

    def test_repository_free_research_approval_reaches_dispatch_boundary_without_call(self):
        settings = replace(
            self.settings,
            langgraph_primary_offline_simulation_enabled=True,
        )
        calls = {provider: 0 for provider in settings.langgraph_primary_required_providers}

        def provider_double(provider: str):
            def run(_context):
                calls[provider] += 1
                return digest(f"unexpected:{provider}")

            return run

        services = DefaultOfflineSimulationServices(
            settings,
            packet_router=packet_router(settings),
            provider_doubles={
                provider: provider_double(provider)
                for provider in settings.langgraph_primary_required_providers
            },
        )
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-research-approval-v1",
            auto_execute_provider=False,
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )
        intake = self.intake().model_copy(
            update={
                "source_event_id": "event-default-research-approve",
                "text": "Project: default\nResearch current public sources.",
            }
        )
        pending = application.create_intake(
            intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        projection = coordinator.get_projection(pending.task_id)
        assert projection is not None
        approval = projection.projection["pending_approval"]

        running = application.resume_approval(
            ApprovalResume(
                task_id=pending.task_id,
                kind=ApprovalKind(approval["kind"]),
                request_digest=approval["request_digest"],
                approval_event_id="approve-default-research-boundary",
                decision=ApprovalDecision.APPROVE,
            )
        )
        approved_projection = coordinator.get_projection(pending.task_id)
        assert approved_projection is not None
        approved_state = coordinator._runtime.compact_state(pending.task_id)

        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, running.status)
        self.assertEqual("perplexity", approved_state["scheduled_producer_id"])
        self.assertEqual("provider_subgraph", approved_projection.projection["phase"])
        self.assertEqual("CLAIMED", approved_projection.projection["provider_dispatch_status"])
        self.assertTrue(all(value == 0 for value in calls.values()))

    def test_duplicate_event_returns_original_graph_task(self):
        first = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        replay = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )

        self.assertEqual(first.task_id, replay.task_id)
        self.assertEqual(1, self.coordinator.readiness().graph_task_count)

    def test_denial_and_cancellation_are_graph_owned_and_recoverable(self):
        denied = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        denied_projection = self.coordinator.get_projection(denied.task_id)
        assert denied_projection is not None
        pending = denied_projection.projection["pending_approval"]
        denied = self.application.resume_approval(
            ApprovalResume(
                task_id=denied.task_id,
                kind=ApprovalKind(pending["kind"]),
                request_digest=pending["request_digest"],
                approval_event_id="deny-default-1",
                decision=ApprovalDecision.DENY,
            )
        )
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, denied.status)
        self.assertEqual(
            "execution_approval_denied",
            self.coordinator.get_projection(denied.task_id).projection["reason_code"],
        )
        recovered = self.application.recover(denied.task_id)
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, recovered.status)

        second = self.intake().model_copy(
            update={"source_event_id": "event-default-cancel"}
        )
        cancelled = self.application.create_intake(
            second,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        cancelled = self.application.cancel(cancelled.task_id)
        self.assertEqual(TaskStatus.FAILED, cancelled.status)
        cancelled_projection = self.coordinator.get_projection(cancelled.task_id)
        assert cancelled_projection is not None
        self.assertEqual(
            "CANCELLED", cancelled_projection.projection["terminal_classification"]
        )

    def test_restart_read_does_not_need_in_memory_application_state(self):
        first = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        restarted_services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        restarted = DefaultPrimaryApplication(
            self.settings,
            PrimaryTaskCoordinator(
                self.handle,
                restarted_services.handlers(),
                owner_token="default-primary-test-owner",
                checkpoint_namespace="default-primary-test-v1",
                application_projection_provider=(
                    restarted_services.application_projection
                ),
            ),
            restarted_services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )

        restored = restarted.get_task(first.task_id)
        self.assertIsNotNone(restored)
        self.assertEqual(first.task_id, restored.task_id)

    def test_same_owner_restart_adopts_intake_and_reconciles_from_projection(self):
        first = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        before = self.coordinator.get_projection(first.task_id)
        self.assertIsNotNone(before)

        restarted_services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        restarted_coordinator = PrimaryTaskCoordinator(
            self.handle,
            restarted_services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=(
                restarted_services.application_projection
            ),
        )
        adopted = restarted_coordinator.adopt_or_activate_new_intake()
        self.assertEqual(1, adopted.fencing_generation)

        assert before is not None
        restarted_services.hydrate(before)
        try:
            recovered = restarted_coordinator.recover(first.task_id)
        finally:
            restarted_services.discard(first.task_id)

        self.assertEqual(first.task_id, recovered.task_id)
        self.assertEqual(before.projection_digest, recovered.projection_digest)
        self.assertEqual(1, restarted_coordinator.readiness().graph_task_count)

    def test_offline_simulation_traverses_full_graph_once(self):
        settings = replace(
            self.settings,
            langgraph_primary_offline_simulation_enabled=True,
        )
        barrier = threading.Barrier(len(settings.langgraph_primary_required_providers))
        worker_threads: set[int] = set()
        worker_lock = threading.Lock()

        def provider_double(provider: str):
            def run(context):
                with worker_lock:
                    worker_threads.add(threading.get_ident())
                barrier.wait(timeout=5)
                return digest(f"offline:{provider}:{context.task_id}")

            return run

        services = DefaultOfflineSimulationServices(
            settings,
            packet_router=packet_router(settings),
            provider_doubles={
                provider: provider_double(provider)
                for provider in settings.langgraph_primary_required_providers
            },
        )
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-offline-test-v1",
            auto_execute_provider=True,
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )
        intake = self.intake().model_copy(
            update={"source_event_id": "event-default-offline-full"}
        )
        pending = application.create_intake(
            intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        projection = coordinator.get_projection(pending.task_id)
        assert projection is not None
        approval = projection.projection["pending_approval"]

        completed = application.resume_approval(
            ApprovalResume(
                task_id=pending.task_id,
                kind=ApprovalKind(approval["kind"]),
                request_digest=approval["request_digest"],
                approval_event_id="approve-default-offline-full",
                decision=ApprovalDecision.APPROVE,
            )
        )

        self.assertEqual(
            TaskStatus.DONE,
            completed.status,
            coordinator.get_projection(pending.task_id).projection,
        )
        self.assertEqual(
            set(settings.langgraph_primary_required_providers),
            set(services.provider_call_counts),
        )
        self.assertTrue(
            all(value == 1 for value in services.provider_call_counts.values())
        )
        self.assertEqual(
            len(settings.langgraph_primary_required_providers), len(worker_threads)
        )
        compact = coordinator._runtime.compact_state(pending.task_id)
        self.assertEqual("SUCCEEDED", compact["terminal_classification"])
        self.assertIsNotNone(compact["evidence_digest"])
        self.assertIsNotNone(compact["acceptance_digest"])
        self.assertNotEqual(compact["producer_id"], compact["reviewer_id"])
        self.assertIsNotNone(compact["ci_digest"])
        self.assertIsNotNone(compact["security_digest"])

        replay = application.recover(pending.task_id)
        self.assertEqual(TaskStatus.DONE, replay.status)
        self.assertTrue(
            all(value == 1 for value in services.provider_call_counts.values())
        )


class DefaultPrimaryFastAPIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests"
            )

    def test_real_startup_routes_intake_and_reads_through_langgraph(self):
        schema = f"director_default_api_{uuid.uuid4().hex[:8]}_v1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "authority"
            root.mkdir()

            def git(*arguments: str) -> None:
                completed = subprocess.run(
                    ("git", *arguments),
                    cwd=root,
                    shell=False,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    self.fail(completed.stderr)

            git("init", "-b", "main")
            git("config", "user.email", "default-primary@example.test")
            git("config", "user.name", "Default Primary Test")
            (root / "README.md").write_text("default primary\n", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-m", "authority")
            git("remote", "add", "origin", "https://github.com/owner/director.git")
            git("update-ref", "refs/remotes/origin/main", "HEAD")

            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONPATH": "src",
                    "APP_ENV": "default-primary-api-test",
                    "SQLITE_PATH": str(Path(directory) / "legacy.sqlite3"),
                    "ORCHESTRATOR_SHARED_SECRET": "test-shared-secret",
                    "SLACK_OWNER_USER_ID": "UOWNER",
                    "SLACK_DIRECTOR_CHANNEL_ID": "CDEFAULT",
                    "SLACK_GATEWAY_SECRET": "g" * 40,
                    "ALLOW_PAID_RUNS": "false",
                    "LANGGRAPH_SHADOW_ENABLED": "false",
                    "LANGGRAPH_APPROVAL_RESUME_ENABLED": "false",
                    "LANGGRAPH_PRIMARY_ENABLED": "true",
                    "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": "true",
                    "LANGGRAPH_PRIMARY_POSTGRES_DSN": TEST_DSN,
                    "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": schema,
                    "LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE": (
                        "default-primary-api-test-v1"
                    ),
                    "LANGGRAPH_PRIMARY_OWNER_TOKEN": "p" * 40,
                    "LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES": "true",
                    "REPOSITORY_SNAPSHOT_ROOT": str(root),
                    "REPOSITORY_ALLOWLIST": "owner/director",
                    "ALLOWED_STARTING_REFS": "main",
                    "PROJECT_ALLOWLIST": "default",
                }
            )
            program = r'''
import json
import time
from fastapi.testclient import TestClient
from director_orchestrator.main import app
from director_orchestrator.primary_commands import (
    CancellationRequest, RecoveryRequest, PrimaryIngressProof,
    build_primary_ingress_proof,
)

payload = {
    "source_event_id": "default-api-event-1",
    "channel_id": "CDEFAULT",
    "thread_ts": "1720000000.000001",
    "user_id": "UOWNER",
    "text": "Project: default\nCreate a bounded implementation plan.",
    "received_at": "2026-08-31T23:30:00Z"
}
headers = {"Authorization": "Bearer test-shared-secret"}
with TestClient(app) as client:
    health = client.get("/health").json()["langgraph_primary"]
    first = client.post("/v1/intake", headers=headers, json=payload)
    replay = client.post("/v1/intake", headers=headers, json=payload)
    task_id = first.json()["task_id"]
    restored = client.get(f"/v1/tasks/{task_id}", headers=headers)
    command_binding = {
        "task_id": task_id,
        "actor_id": "UOWNER",
        "expected_projection_digest": restored.headers["X-Primary-Projection-Digest"],
        "expected_fencing_generation": int(
            restored.headers["X-Primary-Writer-Generation"]
        )
    }
    def signed_request(model, path):
        nonce = ("n" if model is CancellationRequest else "r") * 32
        placeholder = PrimaryIngressProof(
            issued_at=int(time.time()), nonce=nonce, signature="0" * 64,
            channel_id="CDEFAULT",
        )
        request = model(**model_payload, gateway_proof=placeholder)
        proof = build_primary_ingress_proof(
            "g" * 40, "test-shared-secret", request, path=path,
            proof_field="gateway_proof", issued_at=placeholder.issued_at,
            nonce=nonce, channel_id="CDEFAULT",
        )
        return request.model_copy(update={"gateway_proof": proof}).model_dump(mode="json")
    model_payload = {**command_binding, "command_id": "cancel-default-api-1"}
    cancel_path = f"/v1/primary/tasks/{task_id}/cancel"
    cancelled = client.post(
        cancel_path,
        headers=headers,
        json=signed_request(CancellationRequest, cancel_path)
    )


    if cancelled.status_code != 200:
        raise RuntimeError(cancelled.text)
    model_payload = {
        **command_binding,
        "command_id": "recover-default-api-1",
        "expected_projection_digest": cancelled.json()["projection_digest"],
    }
    recover_path = f"/v1/primary/tasks/{task_id}/recover"
    recovered = client.post(
        recover_path,
        headers=headers,
        json=signed_request(RecoveryRequest, recover_path)
    )
    print(json.dumps({
        "health": health,
        "first_status": first.status_code,
        "replay_status": replay.status_code,
        "same_task": task_id == replay.json()["task_id"],
        "read_status": restored.status_code,
        "read_owner": restored.headers.get("X-Task-Engine-Owner"),
        "task_status": restored.json()["status"],
        "cancel_status": cancelled.status_code,
        "cancel_classification": cancelled.json()["terminal_classification"],
        "recover_status": recovered.status_code,
        "recover_classification": recovered.json()["terminal_classification"]
    }, sort_keys=True))
'''
            completed = subprocess.run(
                (sys.executable, "-c", program),
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
            )
            try:
                self.assertEqual(0, completed.returncode, completed.stderr)
                result = json.loads(completed.stdout.strip().splitlines()[-1])
                self.assertEqual(202, result["first_status"])
                self.assertEqual(202, result["replay_status"])
                self.assertTrue(result["same_task"])
                self.assertEqual(200, result["read_status"])
                self.assertEqual("langgraph", result["read_owner"])
                self.assertEqual("OWNER_DECISION_REQUIRED", result["task_status"])
                self.assertEqual(200, result["cancel_status"])
                self.assertEqual("CANCELLED", result["cancel_classification"])
                self.assertEqual(200, result["recover_status"])
                self.assertEqual("CANCELLED", result["recover_classification"])
                self.assertTrue(result["health"]["primary_router_wired"])
                self.assertTrue(result["health"]["writer_lease_ready"])
                self.assertEqual("langgraph", result["health"]["new_intake_owner"])
                self.assertTrue(
                    result["health"]["legacy_new_intake_disabled"]
                )
            finally:
                cleanup = open_postgres_control_plane(
                    TEST_DSN,
                    schema=schema,
                    allow_insecure_local_transport=True,
                )
                try:
                    with cleanup.pool.connection() as connection:
                        connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                finally:
                    cleanup.close()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:57 UTC | BEGIN]]
# Request budgets are immutable, non-zero units derived from the validated plan.
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:57 UTC | END]]
if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
