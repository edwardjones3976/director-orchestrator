# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 02:08 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import multiprocessing
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from director_orchestrator.parallel_dispatch import (
    BatchStatus,
    DailyTaskBudgetExceeded,
    DailyTaskBudgetLedger,
    DispatchCandidate,
    MemberState,
    ParallelDispatchHealth,
    ParallelDispatchError,
    ParallelDispatchIntegrityError,
    ParallelDispatchLedger,
    ParallelDispatchPolicy,
    observe_batch,
    plan_dispatch_batch,
)
from director_orchestrator.models import (
    ProviderName,
    SlackIntake,
    TaskEffectEvidence,
    TaskRecord,
    TaskStatus,
    canonical_task_effect_digest,
)
from director_orchestrator.store import TaskStore


def candidate(
    sequence: int,
    *,
    provider: str = "claude",
    depends_on: tuple[int, ...] = (),
    surfaces: tuple[str, ...] = (),
    dispatch: float = 0.10,
    review: float = 0.05,
) -> DispatchCandidate:
    return DispatchCandidate(
        sequence=sequence,
        provider=provider,
        effect_key=hashlib.sha256(f"effect-{sequence}".encode()).hexdigest(),
        depends_on=depends_on,
        write_surfaces=surfaces,
        dispatch_cost_usd=dispatch,
        review_cost_usd=review,
    )


def enabled_policy(**changes) -> ParallelDispatchPolicy:
    values = {
        "enabled": True,
        "kill_switch_engaged": False,
        "provider_caps": {"claude": 2, "cursor": 2, "gemini": 1},
    }
    values.update(changes)
    return ParallelDispatchPolicy(**values)


def _claim_in_process(
    path: str,
    batch_id: str,
    effect_key: str,
    start,
    results,
) -> None:
    start.wait(10)
    claimed = ParallelDispatchLedger(path).claim(
        batch_id,
        effect_key,
        provider_limit=2,
        deployment_limit=1,
    )
    results.put((effect_key, claimed))


def _effect_event(
    *, effect_key: str, kind: str, outcome: str
) -> TaskEffectEvidence:
    payload = {
        "event_version": "v2",
        "effect_key": effect_key,
        "kind": kind,
        "phase": "dispatch",
        "outcome": outcome,
        "plan_generation": 0,
        "assignment_sequence": 1,
        "assignment_digest": "a" * 64,
        "provider": ProviderName.CURSOR,
        "attempt": 1,
        "estimated_cost_usd": 0.25,
        "hop_units": 1,
        "recorded_at": datetime.now(timezone.utc),
    }
    payload["effect_digest"] = canonical_task_effect_digest(payload)
    return TaskEffectEvidence.model_validate(payload)


class ParallelDispatchPolicyTests(unittest.TestCase):
    def test_batch_identity_and_order_are_deterministic(self):
        inputs = [candidate(2, provider="cursor"), candidate(1)]
        first = plan_dispatch_batch(
            task_id="DIR-PARALLEL",
            ordinal=1,
            candidates=inputs,
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        )
        second = plan_dispatch_batch(
            task_id="DIR-PARALLEL",
            ordinal=1,
            candidates=list(reversed(inputs)),
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        )
        self.assertEqual(first.batch, second.batch)
        self.assertEqual((1, 2), tuple(item.sequence for item in first.batch.members))
        self.assertAlmostEqual(0.30, first.batch.reserved_budget_usd)

    def test_selection_enforces_dependencies_surfaces_provider_limits_cooldowns_and_reviews(self):
        now = datetime(2026, 8, 25, 2, tzinfo=timezone.utc)
        decision = plan_dispatch_batch(
            task_id="DIR-PARALLEL",
            ordinal=1,
            candidates=[
                candidate(1, surfaces=("src/core",), dispatch=0.10, review=0.10),
                candidate(2, surfaces=("src/core/file.py",)),
                candidate(3, provider="cursor", depends_on=(9,)),
                candidate(4, provider="gemini"),
                candidate(5, provider="cursor", dispatch=0.15, review=0.15),
            ],
            completed_sequences=(),
            # Sequence 1 reserves both dispatch and review ($0.20). Sequence 5's
            # full $0.30 envelope cannot fit in the remaining $0.25.
            available_budget_usd=0.45,
            policy=enabled_policy(
                provider_caps={"claude": 2, "cursor": 1, "gemini": 1},
                provider_cooldowns_until={"gemini": now + timedelta(minutes=5)},
            ),
            now=now,
        )
        self.assertEqual((1,), tuple(item.sequence for item in decision.batch.members))
        self.assertEqual("batch_not_independent", decision.blocked[2])
        self.assertEqual("dependency_incomplete", decision.blocked[3])
        self.assertEqual("provider_cooldown", decision.blocked[4])
        self.assertEqual("prospective_budget", decision.blocked[5])

    def test_dynamic_cap_scales_after_ten_clean_batches_and_fails_back_to_one(self):
        policy = enabled_policy()
        candidates = [candidate(1), candidate(2, provider="cursor"), candidate(3, provider="gemini")]
        baseline = plan_dispatch_batch(
            task_id="DIR-PARALLEL", ordinal=1, candidates=candidates,
            completed_sequences=(), available_budget_usd=2.0, policy=policy,
        )
        scaled = plan_dispatch_batch(
            task_id="DIR-PARALLEL", ordinal=2, candidates=candidates,
            completed_sequences=(), available_budget_usd=2.0, policy=policy,
            health=ParallelDispatchHealth(clean_batch_streak=10),
        )
        backpressured = plan_dispatch_batch(
            task_id="DIR-PARALLEL", ordinal=3, candidates=candidates,
            completed_sequences=(), available_budget_usd=2.0, policy=policy,
            health=ParallelDispatchHealth(backpressure=True),
        )
        self.assertEqual(2, len(baseline.batch.members))
        self.assertEqual(3, len(scaled.batch.members))
        self.assertEqual(1, len(backpressured.batch.members))

    def test_disabled_and_kill_switch_states_schedule_nothing(self):
        for policy, reason in (
            (ParallelDispatchPolicy(), "kill_switch"),
            (ParallelDispatchPolicy(enabled=True, kill_switch_engaged=True), "kill_switch"),
            (ParallelDispatchPolicy(enabled=False, kill_switch_engaged=False), "disabled"),
        ):
            with self.subTest(reason=reason):
                decision = plan_dispatch_batch(
                    task_id="DIR-PARALLEL", ordinal=1, candidates=[candidate(1)],
                    completed_sequences=(), available_budget_usd=1.0, policy=policy,
                )
                self.assertIsNone(decision.batch)
                self.assertEqual(reason, decision.blocked[1])


class ParallelDispatchLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ledger = ParallelDispatchLedger(
            str(Path(self.temporary.name) / "parallel.sqlite3")
        )
        decision = plan_dispatch_batch(
            task_id="DIR-PARALLEL", ordinal=1,
            candidates=[candidate(1), candidate(2, provider="cursor")],
            completed_sequences=(), available_budget_usd=1.0,
            policy=enabled_policy(),
        )
        self.batch = decision.batch

    def test_per_member_states_replay_and_deterministic_aggregation(self):
        reserved = self.ledger.reserve(self.batch)
        replayed = self.ledger.reserve(self.batch)
        self.assertEqual(reserved, replayed)
        self.assertEqual(BatchStatus.RESERVED, reserved.status)
        self.assertEqual((1, 2), tuple(item.sequence for item in reserved.members))

        first, second = self.batch.members
        self.assertTrue(self.ledger.claim(self.batch.batch_id, first.effect_key))
        self.assertFalse(self.ledger.claim(self.batch.batch_id, first.effect_key))
        self.ledger.record(
            self.batch.batch_id, first.effect_key, MemberState.SUCCEEDED
        )
        self.assertTrue(self.ledger.claim(self.batch.batch_id, second.effect_key))
        completed = self.ledger.record(
            self.batch.batch_id, second.effect_key, MemberState.SUCCEEDED
        )
        self.assertEqual(BatchStatus.SUCCEEDED, completed.status)
        health = ParallelDispatchHealth(backpressure=True)
        for expected in (1, 2):
            health = observe_batch(health, completed)
            self.assertEqual(expected, health.recovery_clean_streak)
            self.assertTrue(health.backpressure)
        health = observe_batch(health, completed)
        self.assertEqual(0, health.clean_batch_streak)
        self.assertFalse(health.backpressure)

    def test_ambiguous_member_cannot_redispatch_and_requires_reconciliation(self):
        self.ledger.reserve(self.batch)
        first = self.batch.members[0]
        self.assertTrue(self.ledger.claim(self.batch.batch_id, first.effect_key))
        ambiguous = self.ledger.record(
            self.batch.batch_id, first.effect_key, MemberState.AMBIGUOUS
        )
        self.assertEqual(BatchStatus.RECONCILIATION_REQUIRED, ambiguous.status)
        self.assertFalse(self.ledger.claim(self.batch.batch_id, first.effect_key))
        with self.assertRaises(ParallelDispatchIntegrityError):
            self.ledger.record(
                self.batch.batch_id, first.effect_key, MemberState.SUCCEEDED
            )
        reconciled = self.ledger.reconcile(
            self.batch.batch_id, first.effect_key,
            succeeded=True, detail="authenticated provider readback",
        )
        self.assertEqual(BatchStatus.ACTIVE, reconciled.status)
        with self.assertRaises(ParallelDispatchIntegrityError):
            self.ledger.reconcile(
                self.batch.batch_id, first.effect_key,
                succeeded=True, detail="duplicate",
            )

    def test_effect_key_cannot_be_reserved_in_another_batch(self):
        self.ledger.reserve(self.batch)
        repeated = plan_dispatch_batch(
            task_id="DIR-PARALLEL", ordinal=2,
            candidates=[self.batch.members[0]], completed_sequences=(),
            available_budget_usd=1.0, policy=enabled_policy(),
        ).batch
        with self.assertRaisesRegex(ParallelDispatchIntegrityError, "effect key"):
            self.ledger.reserve(repeated)

    def test_kill_switch_cancels_only_unstarted_members(self):
        self.ledger.reserve(self.batch)
        first, second = self.batch.members
        self.assertTrue(self.ledger.claim(self.batch.batch_id, first.effect_key))
        stopped = self.ledger.engage_kill_switch(self.batch.batch_id)
        states = {item.effect_key: item.state for item in stopped.members}
        self.assertEqual(MemberState.DISPATCHING, states[first.effect_key])
        self.assertEqual(MemberState.CANCELLED, states[second.effect_key])
        self.assertFalse(self.ledger.claim(self.batch.batch_id, second.effect_key))

    def test_failure_engages_cap_one_backpressure_until_a_clean_batch(self):
        self.ledger.reserve(self.batch)
        first = self.batch.members[0]
        self.assertTrue(self.ledger.claim(self.batch.batch_id, first.effect_key))
        self.ledger.record(self.batch.batch_id, first.effect_key, MemberState.FAILED)
        failed = self.ledger.cancel_unstarted(
            self.batch.batch_id, reason="stop remaining work after failure"
        )
        self.assertEqual(BatchStatus.FAILED, failed.status)
        health = observe_batch(ParallelDispatchHealth(clean_batch_streak=9), failed)
        self.assertTrue(health.backpressure)
        self.assertEqual(0, health.clean_batch_streak)

        next_decision = plan_dispatch_batch(
            task_id="DIR-PARALLEL-NEXT", ordinal=1,
            candidates=[candidate(11), candidate(12, provider="cursor")],
            completed_sequences=(), available_budget_usd=1.0,
            policy=enabled_policy(), health=health,
        )
        self.assertEqual(1, next_decision.effective_cap)
        self.assertEqual(1, len(next_decision.batch.members))

    def test_daily_budget_and_health_survive_restart(self):
        completed = self.ledger.reserve(self.batch, daily_budget_usd=1.0)
        for member in self.batch.members:
            self.assertTrue(
                self.ledger.claim(self.batch.batch_id, member.effect_key)
            )
            completed = self.ledger.record(
                self.batch.batch_id, member.effect_key, MemberState.SUCCEEDED
            )
        stored = self.ledger.observe_and_persist(self.batch.task_id, completed)
        reopened = ParallelDispatchLedger(self.ledger.path)
        self.assertEqual(stored, reopened.load_health(self.batch.task_id))

        second = plan_dispatch_batch(
            task_id="DIR-PARALLEL-OTHER",
            ordinal=1,
            candidates=[candidate(9, dispatch=0.40, review=0.40)],
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        ).batch
        with self.assertRaisesRegex(ParallelDispatchError, "daily provider budget"):
            reopened.reserve(second, daily_budget_usd=0.70)

    def test_restart_finds_the_existing_batch_instead_of_minting_an_ordinal(self):
        reserved = self.ledger.reserve(self.batch)
        first = self.batch.members[0]
        recovered = ParallelDispatchLedger(self.ledger.path).find_latest_batch(
            self.batch.task_id, [first.effect_key]
        )
        self.assertEqual(reserved, recovered)
        self.assertEqual(2, self.ledger.next_ordinal(self.batch.task_id))

    def test_provider_claim_cap_is_deployment_wide_across_tasks(self):
        first = plan_dispatch_batch(
            task_id="DIR-A",
            ordinal=1,
            candidates=[candidate(11, provider="cursor")],
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        ).batch
        second = plan_dispatch_batch(
            task_id="DIR-B",
            ordinal=1,
            candidates=[candidate(12, provider="cursor")],
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        ).batch
        self.ledger.reserve(first)
        ParallelDispatchLedger(self.ledger.path).reserve(second)
        self.assertTrue(
            self.ledger.claim(
                first.batch_id, first.members[0].effect_key, provider_limit=1
            )
        )
        self.assertFalse(
            ParallelDispatchLedger(self.ledger.path).claim(
                second.batch_id, second.members[0].effect_key, provider_limit=1
            )
        )
        self.ledger.record(
            first.batch_id, first.members[0].effect_key, MemberState.SUCCEEDED
        )
        self.assertTrue(
            ParallelDispatchLedger(self.ledger.path).claim(
                second.batch_id, second.members[0].effect_key, provider_limit=1
            )
        )

    def test_ambiguous_dispatch_holds_provider_slot_until_reconciled(self):
        first = plan_dispatch_batch(
            task_id="DIR-AMBIGUOUS-A",
            ordinal=1,
            candidates=[candidate(21, provider="claude")],
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        ).batch
        second = plan_dispatch_batch(
            task_id="DIR-AMBIGUOUS-B",
            ordinal=1,
            candidates=[candidate(22, provider="claude")],
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        ).batch
        self.ledger.reserve(first)
        self.ledger.reserve(second)
        first_key = first.members[0].effect_key
        second_key = second.members[0].effect_key

        self.assertTrue(self.ledger.claim(first.batch_id, first_key, provider_limit=1))
        self.ledger.record(first.batch_id, first_key, MemberState.AMBIGUOUS)
        self.assertFalse(
            self.ledger.claim(second.batch_id, second_key, provider_limit=1)
        )

        self.ledger.reconcile(
            first.batch_id,
            first_key,
            succeeded=False,
            detail="authenticated terminal readback confirmed failure",
        )
        self.assertTrue(
            self.ledger.claim(second.batch_id, second_key, provider_limit=1)
        )

    def test_global_claim_cap_is_atomic_across_real_processes_and_providers(self):
        first = plan_dispatch_batch(
            task_id="DIR-GLOBAL-A",
            ordinal=1,
            candidates=[candidate(31, provider="cursor")],
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        ).batch
        second = plan_dispatch_batch(
            task_id="DIR-GLOBAL-B",
            ordinal=1,
            candidates=[candidate(32, provider="gemini")],
            completed_sequences=(),
            available_budget_usd=1.0,
            policy=enabled_policy(),
        ).batch
        self.ledger.reserve(first)
        self.ledger.reserve(second)
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_claim_in_process,
                args=(
                    self.ledger.path,
                    batch.batch_id,
                    batch.members[0].effect_key,
                    start,
                    results,
                ),
            )
            for batch in (first, second)
        ]
        for process in processes:
            process.start()
        start.set()
        observed = [results.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(15)
            self.assertEqual(0, process.exitcode)
        self.assertEqual(1, sum(claimed for _, claimed in observed))

        winner_key = next(key for key, claimed in observed if claimed)
        winner_batch = first if winner_key == first.members[0].effect_key else second
        loser_batch = second if winner_batch is first else first
        self.ledger.record(
            winner_batch.batch_id, winner_key, MemberState.SUCCEEDED
        )
        self.assertTrue(
            self.ledger.claim(
                loser_batch.batch_id,
                loser_batch.members[0].effect_key,
                provider_limit=2,
                deployment_limit=1,
            )
        )

    def test_cross_process_claim_blocks_failed_pre_submit_closure_guard(self):
        self.ledger.reserve(self.batch)
        member = self.batch.members[0]
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        process = context.Process(
            target=_claim_in_process,
            args=(
                self.ledger.path,
                self.batch.batch_id,
                member.effect_key,
                start,
                results,
            ),
        )
        process.start()
        start.set()
        self.assertEqual((member.effect_key, True), results.get(timeout=15))
        process.join(15)
        self.assertEqual(0, process.exitcode)
        self.assertFalse(
            self.ledger.reserve_pre_submit_closure(
                runtime_idempotency_key="runtime-cross-process-claim",
                parallel_effect_key=member.effect_key,
            )
        )

    def test_completed_or_ambiguous_evidence_dominates_failed_pre_submit_hydration(self):
        for later_outcome in ("completed", "ambiguous"):
            with self.subTest(later_outcome=later_outcome), tempfile.TemporaryDirectory() as root:
                effect_key = hashlib.sha256(later_outcome.encode()).hexdigest()
                record = TaskRecord(
                    task_id=f"DIR-HYDRATE-{later_outcome.upper()}",
                    status=TaskStatus.RUNNING,
                    intake=SlackIntake(
                        source_event_id=f"evt-{later_outcome}",
                        channel_id="C123",
                        thread_ts="123.45",
                        user_id="U123",
                        text="DIRECTOR preserve durable accounting",
                    ),
                    task_effect_ledger=[
                        _effect_event(
                            effect_key=effect_key,
                            kind="provider_intent",
                            outcome="reserved",
                        ),
                        _effect_event(
                            effect_key=effect_key,
                            kind="provider_observation",
                            outcome="failed_pre_submit",
                        ),
                        _effect_event(
                            effect_key=effect_key,
                            kind="provider_observation",
                            outcome=later_outcome,
                        ),
                    ],
                )
                store = TaskStore(str(Path(root) / "director.sqlite3"))
                stored, _ = store.create(record)
                self.assertAlmostEqual(0.25, stored.cumulative_estimated_cost_usd)
                self.assertEqual(1, stored.cumulative_effect_hops)


class DailyTaskBudgetLedgerTests(unittest.TestCase):
    def test_full_task_allowances_enforce_the_daily_stop_across_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "daily.sqlite3")
            ledger = DailyTaskBudgetLedger(path)
            for ordinal in range(1, 6):
                reserved = ledger.reserve(
                    task_id=f"DIR-{ordinal}",
                    task_budget_usd=1.0,
                    daily_budget_usd=5.0,
                )
                self.assertEqual(float(ordinal), reserved)

            restarted = DailyTaskBudgetLedger(path)
            with self.assertRaisesRegex(DailyTaskBudgetExceeded, "exhausted"):
                restarted.reserve(
                    task_id="DIR-6",
                    task_budget_usd=1.0,
                    daily_budget_usd=5.0,
                )
            self.assertEqual(
                1.0,
                restarted.reserve(
                    task_id="DIR-1",
                    task_budget_usd=1.0,
                    daily_budget_usd=5.0,
                ),
            )

    def test_duplicate_source_event_reserves_one_canonical_task_allowance(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "director.sqlite3")
            store = TaskStore(path)
            intake = SlackIntake(
                source_event_id="evt-daily-dedup",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR deduplicate this intake",
            )
            first, created = store.create(
                TaskRecord(task_id="DIR-FIRST", status=TaskStatus.QUEUED, intake=intake)
            )
            duplicate, duplicate_created = store.create(
                TaskRecord(task_id="DIR-SECOND", status=TaskStatus.QUEUED, intake=intake)
            )
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(first.task_id, duplicate.task_id)

            ledger = DailyTaskBudgetLedger(path)
            self.assertEqual(
                1.0,
                ledger.reserve(
                    task_id=first.task_id,
                    task_budget_usd=1.0,
                    daily_budget_usd=2.0,
                ),
            )
            self.assertEqual(
                1.0,
                ledger.reserve(
                    task_id=duplicate.task_id,
                    task_budget_usd=1.0,
                    daily_budget_usd=2.0,
                ),
            )
            self.assertEqual(
                2.0,
                ledger.reserve(
                    task_id="DIR-THIRD",
                    task_budget_usd=1.0,
                    daily_budget_usd=2.0,
                ),
            )


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 02:08 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:26 UTC | END]]
