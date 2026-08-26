# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:31 UTC | BEGIN]]
from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

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
    ):
        return self.store.reserve_workload_assignment(
            reservation_key=key,
            task_id=f"DIR-{key}",
            assignment_id=f"DIR-{key}:1",
            phase="dispatch",
            provider=provider,
            effort_units=effort_units,
            kind=WorkKind.COORDINATION,
            size=WorkSize.SIMPLE,
            model_label=None,
            available_primary_providers=PRIMARY_PROVIDERS,
            mode=mode,
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


# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:31 UTC | END]]
