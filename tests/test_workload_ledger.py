# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 06:53 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-08-29 | 06:42 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5 | 2026-08-29 | 06:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:49 UTC | BEGIN]]
from __future__ import annotations

import multiprocessing
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from director_orchestrator.interactions import (
    InteractionProvider,
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    canonical_identifier_digest,
    project_interaction,
    start_interaction,
    transition_interaction,
)
from director_orchestrator.models import (
    Assignment,
    AssignmentKind,
    AssignmentOutcome,
    DirectorPlan,
    ProviderName,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import (
    TaskStore,
    WorkloadAttemptOutcome,
    WorkloadReservationConflict,
)
from director_orchestrator.workload_policy import (
    EnforcementMode,
    PRIMARY_PROVIDERS,
    Provider,
    WorkKind,
    WorkSize,
)


def _provider_transport(provider: ProviderName) -> ProviderTransport:
    return {
        ProviderName.OPENAI: ProviderTransport.OPENAI_RESPONSES_API,
        ProviderName.CLAUDE: ProviderTransport.CLAUDE_ROUTINE,
        ProviderName.CURSOR: ProviderTransport.CURSOR_AGENTS_API,
        ProviderName.GROK: ProviderTransport.XAI_RESPONSES_API,
        ProviderName.GEMINI: ProviderTransport.GEMINI_INTERACTIONS_API,
        ProviderName.PERPLEXITY: ProviderTransport.PERPLEXITY_API,
    }[provider]


def _install_verified_interaction(
    record: TaskRecord,
    *,
    provider: ProviderName,
    phase: ProviderInteractionPhase,
    interaction_id: str,
    run_id: str,
    result: str,
    assignment_sequence: int | None,
) -> tuple[str, str]:
    effect_key = canonical_identifier_digest(interaction_id)
    drafted_evidence = build_transport_evidence(
        transport=_provider_transport(provider),
        source=TransportEvidenceSource.BACKEND_OBSERVATION,
        execution_mode="simulation",
        idempotency_key=effect_key,
    )
    event = start_interaction(
        task_id=record.task_id,
        interaction_id=interaction_id,
        plan_generation=record.plan_generation,
        assignment_sequence=assignment_sequence,
        provider=InteractionProvider(provider.value),
        phase=phase,
        attempt=1,
        transport_evidence=drafted_evidence,
        effect_key=effect_key,
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, event
    )
    event = transition_interaction(
        record.provider_interaction_ledger,
        interaction_id=interaction_id,
        next_state=ProviderInteractionState.ATTEMPTED,
        transport_evidence=drafted_evidence,
    )
    record.provider_interaction_ledger = append_interaction_event(
        record.provider_interaction_ledger, event
    )
    authenticated_evidence = build_transport_evidence(
        transport=_provider_transport(provider),
        source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
        execution_mode="simulation",
        idempotency_key=interaction_id,
        provider_run_id=run_id,
        result=result,
        native_status="completed",
    )
    for state in (
        ProviderInteractionState.DISPATCHED,
        ProviderInteractionState.ACKNOWLEDGED,
        ProviderInteractionState.RESPONDED,
        ProviderInteractionState.COMPLETED,
        ProviderInteractionState.VERIFIED,
    ):
        event = transition_interaction(
            record.provider_interaction_ledger,
            interaction_id=interaction_id,
            next_state=state,
            transport_evidence=authenticated_evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )
    projection = project_interaction(record.provider_interaction_ledger, interaction_id)
    if projection.state is not ProviderInteractionState.VERIFIED:
        raise AssertionError("test interaction did not reach VERIFIED")
    if event.transport_evidence.provider_run_id_digest != canonical_identifier_digest(
        run_id
    ):
        raise AssertionError("test interaction is not bound to the provider run id")
    if projection.verified_result_digest is None:
        raise AssertionError("test interaction lacks a verified result digest")
    return projection.latest_event_digest, projection.verified_result_digest


def _concurrent_reservation_worker(
    path: str,
    barrier: multiprocessing.synchronize.Barrier,
    queue: multiprocessing.queues.Queue,
) -> None:
    store = TaskStore(path)
    barrier.wait(timeout=20)
    try:
        reservation = store.reserve_workload_assignment(
            reservation_key="shared-reservation",
            task_id="DIR-CONCURRENT",
            assignment_id="DIR-CONCURRENT:1",
            phase="dispatch",
            provider=Provider.CLAUDE,
            effort_units=1,
            kind=WorkKind.COORDINATION,
            size=WorkSize.SIMPLE,
            model_label=None,
            available_primary_providers=PRIMARY_PROVIDERS,
            mode=EnforcementMode.ENFORCED,
        )
        queue.put(("ok", reservation.created))
    except Exception as exc:  # pragma: no cover - surfaced to the parent process.
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


class WorkloadLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "director.sqlite3")
        self.store = TaskStore(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def reserve(
        self,
        key: str,
        provider: Provider,
        *,
        mode: EnforcementMode = EnforcementMode.ENFORCED,
        effort_units: int = 1,
        assignment_id: str | None = None,
        phase: str = "dispatch",
        kind: WorkKind = WorkKind.COORDINATION,
        size: WorkSize = WorkSize.SIMPLE,
        model_label: str | None = None,
        available_primary_providers: frozenset[Provider] = PRIMARY_PROVIDERS,
        shadow_assignment_limit: int = 5,
    ):
        return self.store.reserve_workload_assignment(
            reservation_key=key,
            task_id=f"DIR-{key}",
            assignment_id=assignment_id or f"DIR-{key}:1",
            phase=phase,
            provider=provider,
            effort_units=effort_units,
            kind=kind,
            size=size,
            model_label=model_label,
            available_primary_providers=available_primary_providers,
            mode=mode,
            shadow_assignment_limit=shadow_assignment_limit,
        )

    @staticmethod
    def accepted_record(
        task_id: str,
        provider: ProviderName,
        *,
        effort_points: int = 2,
    ) -> TaskRecord:
        assignment = Assignment(
            sequence=1,
            agent=provider,
            kind=AssignmentKind.IMPLEMENTATION,
            workload_size=WorkSize.SHORT,
            effort_points=effort_points,
            task="Produce one bounded implementation artifact.",
            acceptance=["Director acceptance review is authenticated."],
        )
        outcome = AssignmentOutcome(
            assignment_sequence=1,
            provider=provider,
            run_id=f"run-{task_id}",
            result="Bounded implementation result.",
            review_summary="Accepted by the Director.",
            evidence=["The bounded result satisfies the assignment."],
        )
        record = TaskRecord(
            task_id=task_id,
            status=TaskStatus.DONE,
            intake=SlackIntake(
                source_event_id=f"evt-{task_id}",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="Produce one bounded implementation artifact.",
            ),
            plan=DirectorPlan(
                project="ledger-regression",
                normalized_outcome="Produce one bounded implementation artifact.",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[assignment],
                final_acceptance=["Authenticated accepted result is recorded."],
                workload_policy_version="v1",
            ),
            plan_generation=1,
            assignment_outcomes=[outcome],
        )
        provider_interaction_id = f"test:{task_id}:provider:{outcome.run_id}"
        provider_event_digest, provider_result_digest = _install_verified_interaction(
            record,
            provider=provider,
            phase=ProviderInteractionPhase.DISPATCH,
            interaction_id=provider_interaction_id,
            run_id=outcome.run_id,
            result=outcome.result,
            assignment_sequence=outcome.assignment_sequence,
        )
        outcome.interaction_id = provider_interaction_id
        outcome.verified_interaction_event_digest = provider_event_digest
        outcome.provider_result_digest = provider_result_digest

        review_result = ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary=outcome.review_summary,
            evidence=outcome.evidence,
        ).model_dump_json()
        review_interaction_id = f"test:{task_id}:openai:acceptance-review"
        review_event_digest, review_result_digest = _install_verified_interaction(
            record,
            provider=ProviderName.OPENAI,
            phase=ProviderInteractionPhase.ACCEPTANCE_REVIEW,
            interaction_id=review_interaction_id,
            run_id=f"review-{task_id}",
            result=review_result,
            assignment_sequence=None,
        )
        outcome.director_review_interaction_id = review_interaction_id
        outcome.verified_director_review_event_digest = review_event_digest
        outcome.director_review_result_digest = review_result_digest
        return record

    def authorize_accepted_record(
        self,
        record: TaskRecord,
        *,
        kind: WorkKind = WorkKind.IMPLEMENTATION,
    ) -> None:
        if record.plan is None:
            raise AssertionError("accepted-record fixture requires a plan")
        assignment = record.plan.assignments[0]
        reservation = self.store.reserve_workload_assignment(
            reservation_key=f"{record.task_id}:authorized-dispatch",
            task_id=record.task_id,
            assignment_id=(
                f"{record.task_id}:{record.plan_generation}:{assignment.sequence}"
            ),
            phase="dispatch",
            provider=Provider(assignment.agent.value),
            effort_units=assignment.effort_points,
            kind=kind,
            size=assignment.workload_size,
            model_label=assignment.model_label,
            available_primary_providers=PRIMARY_PROVIDERS,
            mode=EnforcementMode.ENFORCED,
        )
        self.assertTrue(reservation.decision.allowed)
        self.store.finalize_workload_reservation(
            reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
        )

    def test_exact_replay_is_idempotent_and_does_not_double_count(self) -> None:
        first = self.reserve("one", Provider.CLAUDE)
        self.assertTrue(first.created)
        self.assertTrue(first.decision.allowed)
        self.assertTrue(
            self.store.finalize_workload_reservation(
                first.reservation_key, WorkloadAttemptOutcome.INVOKED
            )
        )

        replay = self.reserve("one", Provider.CLAUDE)
        self.assertFalse(replay.created)
        self.assertEqual(replay.final_outcome, WorkloadAttemptOutcome.INVOKED)
        self.assertEqual(self.store.workload_reservation_count(), 1)
        self.assertEqual(len(self.store.list_recent_workload_attempts()), 1)

    def test_key_reuse_with_changed_identity_fails_closed(self) -> None:
        self.reserve("identity", Provider.CLAUDE)
        with self.assertRaises(WorkloadReservationConflict):
            self.reserve("identity", Provider.CURSOR)

    def test_pre_submit_failure_and_cached_replay_do_not_enter_actual_window(self) -> None:
        failed = self.reserve("failed", Provider.CLAUDE)
        cached = self.reserve("cached", Provider.CURSOR)
        self.store.finalize_workload_reservation(
            failed.reservation_key, WorkloadAttemptOutcome.FAILED_PRE_SUBMIT
        )
        self.store.finalize_workload_reservation(
            cached.reservation_key, WorkloadAttemptOutcome.CACHED_REPLAY
        )
        self.assertEqual(self.store.list_recent_workload_attempts(), [])

    def test_rolling_twenty_blocks_hard_overage_and_excludes_blocked_candidate(self) -> None:
        providers = [Provider.CURSOR] * 5 + [Provider.CLAUDE] * 7 + [Provider.GROK] * 7
        for index, provider in enumerate(providers):
            reservation = self.reserve(f"seed-{index}", provider)
            self.assertTrue(reservation.decision.allowed)
            self.store.finalize_workload_reservation(
                reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        blocked = self.reserve("twentieth", Provider.CURSOR)
        self.assertFalse(blocked.decision.allowed)
        self.assertIn(
            "overall_hard_threshold_exceeded:cursor:25",
            blocked.decision.violations,
        )
        self.assertEqual(blocked.final_outcome, WorkloadAttemptOutcome.BLOCKED)
        self.assertEqual(len(self.store.list_recent_workload_attempts(limit=20)), 19)

    def test_shadow_would_block_but_real_attempt_is_still_accounted_once(self) -> None:
        providers = [Provider.CURSOR] * 5 + [Provider.CLAUDE] * 7 + [Provider.GROK] * 7
        for index, provider in enumerate(providers):
            reservation = self.reserve(f"shadow-seed-{index}", provider)
            self.store.finalize_workload_reservation(
                reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        shadow = self.reserve(
            "shadow-twentieth", Provider.CURSOR, mode=EnforcementMode.SHADOW
        )
        self.assertTrue(shadow.decision.allowed)
        self.assertTrue(shadow.decision.would_block)
        self.assertEqual(shadow.decision.outcome.value, "shadow_would_block")
        self.store.finalize_workload_reservation(
            shadow.reservation_key, WorkloadAttemptOutcome.INVOKED
        )
        self.assertEqual(len(self.store.list_recent_workload_attempts(limit=20)), 20)

    def test_cross_provider_pending_does_not_evict_invoked_history(self) -> None:
        providers = (
            [Provider.CURSOR] * 5
            + [Provider.CLAUDE] * 6
            + [Provider.GROK] * 4
            + [Provider.OPENAI]
            + [Provider.GEMINI] * 2
            + [Provider.PERPLEXITY]
        )
        for index, provider in enumerate(providers):
            reservation = self.reserve(f"cross-seed-{index}", provider)
            self.store.finalize_workload_reservation(
                reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        cross_provider_pending = self.reserve("cross-pending", Provider.CLAUDE)
        self.assertTrue(cross_provider_pending.decision.allowed)
        self.assertIsNone(cross_provider_pending.final_outcome)

        cursor = self.reserve("cross-candidate", Provider.CURSOR)
        self.assertFalse(cursor.decision.allowed)
        self.assertIn(
            "overall_hard_threshold_exceeded:cursor:25",
            cursor.decision.violations,
        )

    def test_pending_outcome_cannot_change_a_mature_weighted_window(self) -> None:
        seeds = (
            [(Provider.OPENAI, 8)]
            + [(Provider.CLAUDE, 4)] * 7
            + [(Provider.GROK, 4)] * 3
            + [(Provider.GROK, 2)]
            + [(Provider.CURSOR, 5)] * 2
            + [(Provider.CURSOR, 4)]
            + [(Provider.GEMINI, 3)] * 2
            + [(Provider.PERPLEXITY, 1)] * 2
        )
        self.assertEqual(len(seeds), 19)
        for index, (provider, effort_units) in enumerate(seeds):
            reservation = self.reserve(
                f"weighted-seed-{index}",
                provider,
                effort_units=effort_units,
            )
            self.store.finalize_workload_reservation(
                reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        pending = self.reserve(
            "weighted-pending", Provider.GROK, effort_units=1
        )
        self.assertTrue(pending.decision.allowed)
        candidate = self.reserve(
            "weighted-candidate", Provider.CLAUDE, effort_units=8
        )

        self.assertFalse(candidate.decision.allowed)
        self.assertIn(
            "workload_rollover_pending_unresolved",
            candidate.decision.violations,
        )

    def test_same_provider_pending_remains_conservative_pressure(self) -> None:
        providers = (
            [Provider.CLAUDE]
            + [Provider.CURSOR] * 4
            + [Provider.CLAUDE] * 5
            + [Provider.GROK] * 5
            + [Provider.OPENAI]
            + [Provider.GEMINI] * 2
            + [Provider.PERPLEXITY]
        )
        for index, provider in enumerate(providers):
            reservation = self.reserve(f"same-seed-{index}", provider)
            self.store.finalize_workload_reservation(
                reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        first_pending = self.reserve("same-pending", Provider.CURSOR)
        self.assertTrue(first_pending.decision.allowed)
        self.assertIsNone(first_pending.final_outcome)

        second_pending = self.reserve("same-candidate", Provider.CURSOR)
        self.assertFalse(second_pending.decision.allowed)
        self.assertIn(
            "overall_hard_threshold_exceeded:cursor:25",
            second_pending.decision.violations,
        )

    def test_pending_replay_rejects_changed_authority_but_terminal_replay_is_stable(
        self,
    ) -> None:
        initial = self.reserve("authority", Provider.CLAUDE)
        self.assertIsNone(initial.final_outcome)

        with self.assertRaisesRegex(
            WorkloadReservationConflict,
            "pending workload reservation authority changed before dispatch",
        ):
            self.reserve(
                "authority",
                Provider.CLAUDE,
                mode=EnforcementMode.SHADOW,
            )
        with self.assertRaisesRegex(
            WorkloadReservationConflict,
            "pending workload reservation authority changed before dispatch",
        ):
            self.reserve(
                "authority",
                Provider.CLAUDE,
                available_primary_providers=frozenset(
                    {Provider.CLAUDE, Provider.CURSOR}
                ),
            )

        self.store.finalize_workload_reservation(
            initial.reservation_key, WorkloadAttemptOutcome.INVOKED
        )
        terminal_replay = self.reserve(
            "authority",
            Provider.CLAUDE,
            mode=EnforcementMode.SHADOW,
            available_primary_providers=frozenset(
                {Provider.CLAUDE, Provider.CURSOR}
            ),
        )
        self.assertFalse(terminal_replay.created)
        self.assertEqual(terminal_replay.final_outcome, WorkloadAttemptOutcome.INVOKED)
        self.assertEqual(terminal_replay.decision.mode, EnforcementMode.ENFORCED)
        self.assertEqual(
            terminal_replay.available_primary_providers,
            PRIMARY_PROVIDERS,
        )

    def test_five_distinct_terminal_shadow_units_activate_enforcement(self) -> None:
        first = self.reserve(
            "shadow-unit-1-dispatch",
            Provider.CLAUDE,
            mode=EnforcementMode.SHADOW,
            assignment_id="DIR-SHADOW:1",
        )
        self.assertEqual(first.decision.mode, EnforcementMode.SHADOW)
        self.store.finalize_workload_reservation(
            first.reservation_key, WorkloadAttemptOutcome.INVOKED
        )

        retry = self.reserve(
            "shadow-unit-1-retry",
            Provider.CLAUDE,
            mode=EnforcementMode.SHADOW,
            assignment_id="DIR-SHADOW:1",
            phase="revision",
        )
        self.assertEqual(retry.decision.mode, EnforcementMode.SHADOW)
        self.store.finalize_workload_reservation(
            retry.reservation_key, WorkloadAttemptOutcome.FAILED_PRE_SUBMIT
        )

        for sequence in range(2, 6):
            reservation = self.reserve(
                f"shadow-unit-{sequence}",
                Provider.CLAUDE,
                mode=EnforcementMode.SHADOW,
                assignment_id=f"DIR-SHADOW:{sequence}",
            )
            self.assertEqual(reservation.decision.mode, EnforcementMode.SHADOW)
            self.store.finalize_workload_reservation(
                reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        sixth = self.reserve(
            "shadow-unit-6",
            Provider.CLAUDE,
            mode=EnforcementMode.SHADOW,
            assignment_id="DIR-SHADOW:6",
        )
        self.assertEqual(sixth.decision.mode, EnforcementMode.ENFORCED)

    def test_accepted_v1_outcomes_append_one_immutable_direct_credit(self) -> None:
        record = self.accepted_record("DIR-DIRECT-CLAUDE", ProviderName.CLAUDE)
        self.authorize_accepted_record(record)
        self.store.create(record)
        self.assertEqual(len(self.store.list_recent_direct_production()), 1)

        self.store.save(record)
        self.store.save(record)
        credits = self.store.list_recent_direct_production()
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].assignment_id, "DIR-DIRECT-CLAUDE:1:1")
        self.assertEqual(credits[0].provider.value, Provider.CLAUDE.value)
        self.assertEqual(credits[0].effort_units, 2)

        record.assignment_outcomes[0].provider_result_digest = "d" * 64
        with self.assertRaises(WorkloadReservationConflict):
            self.store.save(record)
        self.assertEqual(len(self.store.list_recent_direct_production()), 1)

        for statement in (
            "UPDATE direct_production_credits SET effort_units = 3",
            "DELETE FROM direct_production_credits",
        ):
            with self.subTest(statement=statement), closing(
                sqlite3.connect(self.path)
            ) as connection:
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "direct-production credits are append-only",
                ):
                    connection.execute(statement)

    def test_direct_credit_rejects_missing_or_tampered_interaction_bindings(self) -> None:
        mutations = (
            ("missing-provider-interaction", "interaction_id", None),
            (
                "tampered-provider-event",
                "verified_interaction_event_digest",
                "d" * 64,
            ),
            ("missing-provider-result", "provider_result_digest", None),
            ("missing-director-interaction", "director_review_interaction_id", None),
            (
                "tampered-director-event",
                "verified_director_review_event_digest",
                "e" * 64,
            ),
            (
                "tampered-director-result",
                "director_review_result_digest",
                "f" * 64,
            ),
        )
        for index, (name, field, value) in enumerate(mutations, start=1):
            with self.subTest(name=name):
                record = self.accepted_record(
                    f"DIR-DIRECT-BINDING-{index}", ProviderName.CLAUDE
                )
                self.authorize_accepted_record(record)
                setattr(record.assignment_outcomes[0], field, value)
                with self.assertRaises(WorkloadReservationConflict):
                    self.store.create(record)
                self.assertEqual(self.store.list_recent_direct_production(), [])

    def test_ordinary_openai_outcome_does_not_create_external_openai_credit(self) -> None:
        record = self.accepted_record("DIR-DIRECT-OPENAI", ProviderName.OPENAI)
        self.store.create(record)
        self.store.save(record)
        self.assertEqual(self.store.list_recent_direct_production(), [])

    def test_direct_credit_requires_invoked_implementation_authority(self) -> None:
        unreserved = self.accepted_record(
            "DIR-DIRECT-UNRESERVED", ProviderName.CLAUDE
        )
        with self.assertRaisesRegex(
            WorkloadReservationConflict, "lacks invoked workload authority"
        ):
            self.store.create(unreserved)

        review = self.accepted_record("DIR-DIRECT-REVIEW", ProviderName.CLAUDE)
        self.authorize_accepted_record(review, kind=WorkKind.REVIEW)
        self.store.create(review)
        self.assertEqual(self.store.list_recent_direct_production(), [])

    def test_direct_window_blocks_while_accepted_credit_is_unresolved(self) -> None:
        direct_seeds = (
            [(Provider.CLAUDE, 6)] * 5
            + [(Provider.CURSOR, 6)] * 4
            + [(Provider.GROK, 6), (Provider.GROK, 5), (Provider.GROK, 5)]
            + [(Provider.GEMINI, 5)] * 4
            + [(Provider.PERPLEXITY, 3)] * 3
        )
        self.assertEqual(len(direct_seeds), 19)
        for index, (provider, effort_units) in enumerate(direct_seeds):
            record = self.accepted_record(
                f"DIR-DIRECT-SEED-{index}",
                ProviderName(provider.value),
                effort_points=effort_units,
            )
            self.authorize_accepted_record(record)
            self.store.create(record)

        # Keep the overall-effort ledger safely below Claude's cap so this
        # regression isolates the accepted-production rollover boundary.
        overall_controls = (
            [(Provider.CLAUDE, 6)]
            + [(Provider.CLAUDE, 5)] * 6
            + [(Provider.CURSOR, 5)] * 4
            + [(Provider.GROK, 5)] * 4
            + [(Provider.OPENAI, 8)]
            + [(Provider.GEMINI, 6), (Provider.GEMINI, 5)]
            + [(Provider.PERPLEXITY, 5)]
        )
        self.assertEqual(len(overall_controls), 19)
        for index, (provider, effort_units) in enumerate(overall_controls):
            control = self.reserve(
                f"direct-overall-control-{index}",
                provider,
                mode=EnforcementMode.SHADOW,
                shadow_assignment_limit=100,
                effort_units=effort_units,
            )
            self.store.finalize_workload_reservation(
                control.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        pending = self.reserve(
            "direct-pending",
            Provider.CURSOR,
            effort_units=1,
            kind=WorkKind.IMPLEMENTATION,
        )
        self.assertTrue(pending.decision.allowed)
        candidate = self.reserve(
            "direct-candidate",
            Provider.CLAUDE,
            effort_units=6,
            kind=WorkKind.IMPLEMENTATION,
        )

        self.assertFalse(candidate.decision.allowed)
        self.assertIn(
            "direct_production_rollover_pending_unresolved",
            candidate.decision.violations,
        )

    # [[Claude Code | Claude Fable 5 | 2026-08-29 | 05:52 UTC | BEGIN]]
    _SIBLING_SEEDS = (
        [Provider.CURSOR] * 5
        + [Provider.CLAUDE] * 6
        + [Provider.GROK] * 4
        + [Provider.OPENAI]
        + [Provider.GEMINI] * 2
        + [Provider.PERPLEXITY]
    )

    def test_safe_sibling_reservation_is_not_blocked_by_in_window_pending(
        self,
    ) -> None:
        for index, provider in enumerate(self._SIBLING_SEEDS):
            reservation = self.reserve(f"sibling-seed-{index}", provider)
            self.assertTrue(reservation.decision.allowed)
            self.store.finalize_workload_reservation(
                reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        pending = self.reserve("sibling-pending", Provider.CLAUDE)
        self.assertTrue(pending.decision.allowed)
        self.assertIsNone(pending.final_outcome)

        # Safe under both projections of the pending row, so it must dispatch.
        sibling = self.reserve("sibling-candidate", Provider.GEMINI)
        self.assertTrue(sibling.decision.allowed, sibling.decision.violations)
        self.assertNotIn(
            "workload_rollover_pending_unresolved", sibling.decision.violations
        )
        self.assertIsNone(sibling.final_outcome)

        # In-flight rows remain conservative pressure for an unsafe sibling.
        unsafe = self.reserve("sibling-unsafe", Provider.CURSOR)
        self.assertFalse(unsafe.decision.allowed)
        self.assertIn(
            "overall_hard_threshold_exceeded:cursor:25", unsafe.decision.violations
        )

    def test_stale_pending_row_outside_the_window_is_not_a_lifetime_block(
        self,
    ) -> None:
        stale = self.reserve("stale-pending", Provider.CLAUDE)
        self.assertIsNone(stale.final_outcome)
        for index, provider in enumerate(self._SIBLING_SEEDS):
            reservation = self.reserve(f"stale-seed-{index}", provider)
            self.assertTrue(reservation.decision.allowed, reservation.decision.violations)
            self.store.finalize_workload_reservation(
                reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        candidate = self.reserve("stale-candidate", Provider.GEMINI)
        self.assertTrue(candidate.decision.allowed, candidate.decision.violations)
        self.assertNotIn(
            "workload_rollover_pending_unresolved", candidate.decision.violations
        )

    def test_direct_window_permits_safe_sibling_while_credit_is_unresolved(
        self,
    ) -> None:
        direct_seeds = (
            [(Provider.GEMINI, 1)]
            + [(Provider.CLAUDE, 6)] * 5
            + [(Provider.CURSOR, 5)] * 5
            + [(Provider.GROK, 6), (Provider.GROK, 5), (Provider.GROK, 5)]
            + [(Provider.GEMINI, 6)] * 3
            + [(Provider.PERPLEXITY, 5), (Provider.PERPLEXITY, 4)]
        )
        self.assertEqual(len(direct_seeds), 19)
        for index, (provider, effort_units) in enumerate(direct_seeds):
            record = self.accepted_record(
                f"DIR-DIRECT-SIBLING-{index}",
                ProviderName(provider.value),
                effort_points=effort_units,
            )
            self.authorize_accepted_record(record)
            self.store.create(record)
        overall_controls = (
            [(Provider.CLAUDE, 6)]
            + [(Provider.CLAUDE, 5)] * 6
            + [(Provider.CURSOR, 5)] * 4
            + [(Provider.GROK, 5)] * 4
            + [(Provider.OPENAI, 8)]
            + [(Provider.GEMINI, 6), (Provider.GEMINI, 5)]
            + [(Provider.PERPLEXITY, 5)]
        )
        for index, (provider, effort_units) in enumerate(overall_controls):
            control = self.reserve(
                f"direct-sibling-control-{index}",
                provider,
                mode=EnforcementMode.SHADOW,
                shadow_assignment_limit=100,
                effort_units=effort_units,
            )
            self.store.finalize_workload_reservation(
                control.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        pending = self.reserve(
            "direct-sibling-pending",
            Provider.GEMINI,
            effort_units=1,
            kind=WorkKind.IMPLEMENTATION,
        )
        self.assertTrue(pending.decision.allowed, pending.decision.violations)
        sibling = self.reserve(
            "direct-sibling-candidate",
            Provider.GEMINI,
            effort_units=1,
            kind=WorkKind.IMPLEMENTATION,
        )
        self.assertTrue(sibling.decision.allowed, sibling.decision.violations)
        self.assertNotIn(
            "direct_production_rollover_pending_unresolved",
            sibling.decision.violations,
        )
    # [[Claude Code | Claude Fable 5 | 2026-08-29 | 05:52 UTC | END]]

    @staticmethod
    def _minimal_done_task(task_id: str) -> TaskRecord:
        return TaskRecord(
            task_id=task_id,
            status=TaskStatus.DONE,
            intake=SlackIntake(
                source_event_id=f"evt-{task_id}",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="Bounded implementation artifact.",
            ),
            plan=DirectorPlan(
                project="ledger-regression",
                normalized_outcome="Bounded implementation artifact.",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CURSOR,
                        kind=AssignmentKind.IMPLEMENTATION,
                        workload_size=WorkSize.SIMPLE,
                        effort_points=1,
                        task="Produce one bounded implementation artifact.",
                        acceptance=["Authenticated accepted result is recorded."],
                    )
                ],
                final_acceptance=["Authenticated accepted result is recorded."],
                workload_policy_version="v1",
            ),
            plan_generation=1,
        )

    def _insert_raw_direct_credit(
        self,
        *,
        task_id: str,
        assignment_id: str,
        provider: Provider,
        effort_units: int,
        recorded_at: str,
    ) -> None:
        digest = "a" * 64
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """INSERT INTO direct_production_credits(
                    credit_key, task_id, plan_generation, assignment_sequence,
                    assignment_id, provider, effort_units,
                    provider_event_digest, provider_result_digest,
                    review_event_digest, review_result_digest, recorded_at
                ) VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assignment_id,
                    task_id,
                    assignment_id,
                    provider.value,
                    effort_units,
                    digest,
                    digest,
                    digest,
                    digest,
                    recorded_at,
                ),
            )
            connection.commit()

    def test_direct_window_projects_invoked_terminal_task_awaiting_credit(self) -> None:
        direct_seeds = (
            [(Provider.CLAUDE, 6)] * 5
            + [(Provider.CURSOR, 6)] * 4
            + [(Provider.GROK, 6), (Provider.GROK, 5), (Provider.GROK, 5)]
            + [(Provider.GEMINI, 5)] * 4
            + [(Provider.PERPLEXITY, 3)] * 3
        )
        self.assertEqual(len(direct_seeds), 19)
        for index, (provider, effort_units) in enumerate(direct_seeds):
            record = self.accepted_record(
                f"DIR-DIRECT-SEED-{index}",
                ProviderName(provider.value),
                effort_points=effort_units,
            )
            self.authorize_accepted_record(record)
            self.store.create(record)

        overall_controls = (
            [(Provider.CLAUDE, 6)]
            + [(Provider.CLAUDE, 5)] * 6
            + [(Provider.CURSOR, 5)] * 4
            + [(Provider.GROK, 5)] * 4
            + [(Provider.OPENAI, 8)]
            + [(Provider.GEMINI, 6), (Provider.GEMINI, 5)]
            + [(Provider.PERPLEXITY, 5)]
        )
        for index, (provider, effort_units) in enumerate(overall_controls):
            control = self.reserve(
                f"direct-overall-control-{index}",
                provider,
                mode=EnforcementMode.SHADOW,
                shadow_assignment_limit=100,
                effort_units=effort_units,
            )
            self.store.finalize_workload_reservation(
                control.reservation_key, WorkloadAttemptOutcome.INVOKED
            )

        delayed = self._minimal_done_task("DIR-DIRECT-DELAYED")
        self.store.create(delayed)
        invoked = self.store.reserve_workload_assignment(
            reservation_key="delayed-dispatch",
            task_id=delayed.task_id,
            assignment_id=f"{delayed.task_id}:1:1",
            phase="dispatch",
            provider=Provider.CURSOR,
            effort_units=1,
            kind=WorkKind.IMPLEMENTATION,
            size=WorkSize.SIMPLE,
            model_label=None,
            available_primary_providers=PRIMARY_PROVIDERS,
            mode=EnforcementMode.ENFORCED,
        )
        self.assertTrue(invoked.decision.allowed)
        self.store.finalize_workload_reservation(
            invoked.reservation_key, WorkloadAttemptOutcome.INVOKED
        )

        candidate = self.reserve(
            "direct-candidate",
            Provider.CLAUDE,
            effort_units=6,
            kind=WorkKind.IMPLEMENTATION,
        )

        self.assertFalse(candidate.decision.allowed)
        self.assertIn(
            "direct_production_rollover_pending_unresolved",
            candidate.decision.violations,
        )

    def test_direct_rollover_window_uses_credit_id_not_recorded_at(self) -> None:
        old_recorded_at = "2026-08-01T00:00:00+00:00"
        new_recorded_at = "2026-08-29T12:00:00+00:00"

        pending_task = self._minimal_done_task("DIR-CREDIT-PENDING")
        self.store.create(pending_task)
        pending = self.store.reserve_workload_assignment(
            reservation_key="credit-order-pending",
            task_id=pending_task.task_id,
            assignment_id=f"{pending_task.task_id}:1:1",
            phase="dispatch",
            provider=Provider.CURSOR,
            effort_units=1,
            kind=WorkKind.IMPLEMENTATION,
            size=WorkSize.SIMPLE,
            model_label=None,
            available_primary_providers=PRIMARY_PROVIDERS,
            mode=EnforcementMode.ENFORCED,
        )
        self.assertTrue(pending.decision.allowed)
        self.store.finalize_workload_reservation(
            pending.reservation_key, WorkloadAttemptOutcome.INVOKED
        )

        canonical_tail = (
            [(Provider.CLAUDE, 6)] * 4
            + [(Provider.CLAUDE, 5)]
            + [(Provider.CURSOR, 6)] * 4
            + [(Provider.GROK, 8)] * 2
            + [(Provider.GEMINI, 5)] * 4
            + [(Provider.PERPLEXITY, 3)] * 3
        )
        self.assertEqual(len(canonical_tail), 18)
        for index in range(20):
            task_id = f"DIR-CREDIT-ORDER-{index}"
            self.store.create(self._minimal_done_task(task_id))
            if index == 0:
                provider = Provider.CLAUDE
                effort_units = 8
                recorded_at = new_recorded_at
            elif index == 1:
                # Match the unresolved Cursor unit so either projection keeps
                # the exact 30/25/16/20/9 direct-production boundary.
                provider = Provider.CURSOR
                effort_units = 1
                recorded_at = old_recorded_at
            else:
                provider, effort_units = canonical_tail[index - 2]
                recorded_at = old_recorded_at
            self._insert_raw_direct_credit(
                task_id=task_id,
                assignment_id=f"{task_id}:1:1",
                provider=provider,
                effort_units=effort_units,
                recorded_at=recorded_at,
            )

        candidate = self.reserve(
            "credit-order-candidate",
            Provider.CLAUDE,
            effort_units=1,
            kind=WorkKind.IMPLEMENTATION,
        )

        self.assertTrue(candidate.decision.allowed, candidate.decision.violations)
        self.assertNotIn(
            "direct_production_hard_threshold_exceeded:claude:",
            candidate.decision.violations,
        )

    def test_sqlite_cas_allows_one_cross_process_creator(self) -> None:
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(4)
        queue = context.Queue()
        processes = [
            context.Process(
                target=_concurrent_reservation_worker,
                args=(self.path, barrier, queue),
            )
            for _ in range(4)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
            self.assertFalse(process.is_alive(), "workload reservation process hung")
            self.assertEqual(process.exitcode, 0)

        results = [queue.get(timeout=5) for _ in processes]
        self.assertEqual([status for status, _ in results], ["ok"] * 4)
        self.assertEqual(sum(bool(created) for _, created in results), 1)
        self.assertEqual(self.store.workload_reservation_count(), 1)


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:49 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | END]]
# [[Claude Code | Claude Fable 5 | 2026-08-29 | 06:14 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-08-29 | 06:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 06:53 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | END]]
