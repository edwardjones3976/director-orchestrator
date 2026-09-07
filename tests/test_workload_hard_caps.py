# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:54 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:11 UTC | BEGIN]]
"""Count-window and existing atomic reservation regression coverage."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import tempfile
from pathlib import Path
import unittest

from director_orchestrator.models import Assignment, AssignmentKind, ProviderName
from director_orchestrator.policy import _trusted_v1_assignment
from director_orchestrator.store import TaskStore
from director_orchestrator.workload_policy import (
    AssignmentRequest, DirectProductionProvider as D, DirectProductionRecord,
    EnforcementMode, PRIMARY_PROVIDERS, Provider, TrustedProviderProfile,
    WorkKind, WorkSize, WorkloadPolicyError, WorkloadSelectionHold, evaluate_assignment,
    select_cheapest_capable_provider,
)


def history(count=6, units=1):
    return tuple(DirectProductionRecord(str(i), provider, units)
                 for i, provider in enumerate([D.CURSOR] * (19-count) + [D.CLAUDE] * count))


def request(units=1, model=None):
    return AssignmentRequest("candidate", Provider.CLAUDE, WorkKind.IMPLEMENTATION,
                             WorkSize.LONG, model_label=model, effort_units=units)


class HardCapacityTests(unittest.TestCase):
    def test_exact_ceiling_and_excess(self):
        self.assertTrue(evaluate_assignment(request(), direct_production_history=history()).allowed)
        denied = evaluate_assignment(request(), direct_production_history=history(7))
        self.assertFalse(denied.allowed)
        self.assertIn("direct_production_hard_cap_exceeded:claude:35", denied.violations)

    def test_effort_does_not_change_count_capacity(self):
        self.assertTrue(evaluate_assignment(request(999), direct_production_history=history()).allowed)
        self.assertFalse(evaluate_assignment(request(), direct_production_history=history(7, 999)).allowed)

    def test_rollover_removes_oldest_before_candidate(self):
        prior = (DirectProductionRecord("old", D.CLAUDE),) + history()
        self.assertTrue(evaluate_assignment(request(), direct_production_history=prior).allowed)

    def test_bootstrap_uses_twenty_slots_without_fabricated_credit(self):
        for prior in ((), history()[:18]):
            result = evaluate_assignment(request(), direct_production_history=prior)
            self.assertTrue(result.allowed)
        self.assertFalse(evaluate_assignment(request(), direct_production_history=history(7)[-7:]).allowed)

    def test_size_does_not_require_model_but_explicit_requirement_does(self):
        self.assertTrue(evaluate_assignment(request(), direct_production_history=history()).allowed)
        self.assertFalse(evaluate_assignment(request(model="other"), direct_production_history=history(),
                                            trusted_required_model_label="owner-model").allowed)
        self.assertTrue(evaluate_assignment(request(model="owner-model"), direct_production_history=history(),
                                           trusted_required_model_label="owner-model").allowed)
        for model in (None, "owner-model"):
            assignment = Assignment(sequence=1, agent=ProviderName.CLAUDE,
                                    kind=AssignmentKind.IMPLEMENTATION, task="bounded",
                                    acceptance=["proof"], model_label=model)
            self.assertEqual(_trusted_v1_assignment(assignment).model_label, model)

    def test_selector_cannot_override_capacity_or_bootstrap(self):
        providers = frozenset({Provider.CLAUDE, Provider.CURSOR, Provider.GROK})
        profiles = tuple(TrustedProviderProfile(provider=p, trusted_cost_rank=i,
                         supported_work_kinds=frozenset({WorkKind.IMPLEMENTATION, WorkKind.REVIEW}))
                         for i, p in enumerate((Provider.CLAUDE, Provider.CURSOR, Provider.GROK)))
        arguments = dict(assignment_id="new", kind=WorkKind.IMPLEMENTATION, effort_units=1,
                         trusted_profiles=profiles, ready_providers=providers,
                         in_scope_providers=providers, reviewer_independent_providers=providers)
        self.assertEqual(select_cheapest_capable_provider(**arguments,
                         direct_production_history=history(7)), Provider.GROK)
        with self.assertRaises(WorkloadSelectionHold) as held:
            select_cheapest_capable_provider(**arguments, direct_production_history=history(7),
                                            explicit_required_provider=Provider.CLAUDE)
        self.assertEqual(held.exception.reasons, ("direct_production_hard_cap_exceeded:claude:35",))
        self.assertEqual(select_cheapest_capable_provider(**arguments), Provider.CLAUDE)
        self.assertEqual(select_cheapest_capable_provider(**arguments, direct_production_history=history(7), mode=EnforcementMode.SHADOW), Provider.CLAUDE)
        arguments["ready_providers"] = frozenset({Provider.CLAUDE, Provider.CURSOR})
        with self.assertRaises(WorkloadSelectionHold):
            select_cheapest_capable_provider(**arguments, direct_production_history=history(7))
        arguments["ready_providers"] = frozenset()
        with self.assertRaises(WorkloadPolicyError) as invalid:
            select_cheapest_capable_provider(**arguments, direct_production_history=history(7))
        self.assertNotIsInstance(invalid.exception, WorkloadSelectionHold)

    def test_existing_atomic_store_reserves_only_one_remaining_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite")
            store = TaskStore(path)
            # Isolated accepted-history fixture; production still authenticates
            # credit creation through the unchanged store acceptance path.
            with closing(sqlite3.connect(path)) as connection:
                for record in history():
                    connection.execute("""INSERT INTO direct_production_credits(
                        credit_key, task_id, plan_generation, assignment_sequence,
                        assignment_id, provider, effort_units, provider_event_digest,
                        provider_result_digest, review_event_digest, review_result_digest, recorded_at)
                        VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (record.assignment_id, "fixture", record.assignment_id,
                         record.provider.value, record.effort_units,
                         "a"*64, "b"*64, "c"*64, "d"*64, "2026-09-05T00:00:00Z"))
                connection.commit()
            def reserve(index):
                return store.reserve_workload_assignment(
                    reservation_key=f"reserve-{index}", task_id=f"task-{index}",
                    assignment_id=f"new-{index}", phase="dispatch", provider=Provider.CLAUDE,
                    effort_units=100, kind=WorkKind.IMPLEMENTATION, size=WorkSize.LONG,
                    model_label=None, available_primary_providers=PRIMARY_PROVIDERS,
                    mode=EnforcementMode.ENFORCED, shadow_assignment_limit=5)
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(reserve, (1, 2)))
            self.assertEqual(sum(result.decision.allowed for result in outcomes), 1)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM direct_production_credits").fetchone()[0], 19)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:11 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:54 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | END]]
