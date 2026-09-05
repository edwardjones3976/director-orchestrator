# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
import unittest
from director_orchestrator.primary_roster import PrimaryRosterHold, select_primary_roster
from director_orchestrator.primary_workload import admission_reason

class PrimaryRosterTests(unittest.TestCase):
    def select(self, **kw):
        args = dict(available=("openai", "claude", "cursor", "grok"), history=(),
                    trusted_cost_ranks={"claude": 2, "cursor": 1, "grok": 3})
        args.update(kw)
        return select_primary_roster(**args)

    def test_core_is_minimal_independent_and_cost_ranked(self):
        roster = self.select()
        self.assertEqual((roster.producer, roster.reviewer), ("cursor", "claude"))
        self.assertIsNone(roster.research)

    def test_startup_capacity_uses_only_real_assignments(self):
        self.assertIsNone(admission_reason("claude", []))
        self.assertIsNone(admission_reason("claude", ["claude"] * 5))
        self.assertEqual(admission_reason("claude", ["claude"] * 6), "workload_assignment_hard_limit")

    def test_orchestration_never_spills_into_production(self):
        with self.assertRaises(PrimaryRosterHold):
            self.select(history=["claude"] * 6 + ["cursor"] * 5)

    def test_optional_capabilities_are_explicit_and_fail_closed(self):
        with self.assertRaisesRegex(PrimaryRosterHold, "enterprise_accounting"):
            self.select(required_capabilities=("enterprise_context",))
        with self.assertRaisesRegex(PrimaryRosterHold, "scope_required"):
            self.select(required_capabilities=("public_research",))
        roster = self.select(available=("openai", "claude", "cursor", "perplexity"),
            required_capabilities=("public_research",), public_research_query="public topic")
        self.assertEqual(roster.research, "perplexity")

    def test_missing_core_or_cost_authority_cannot_select(self):
        with self.assertRaises(PrimaryRosterHold):
            self.select(available=("claude", "cursor"))
        with self.assertRaisesRegex(PrimaryRosterHold, "cost_rank"):
            self.select(trusted_cost_ranks={})

    def test_fixed_original_targets_eventually_hold_core_only_work(self):
        history = []
        for _ in range(10):
            try:
                selected = self.select(history=history)
            except PrimaryRosterHold as hold:
                self.assertEqual(str(hold), "primary_independent_review_allocation_unavailable")
                break
            history.extend((selected.producer, selected.reviewer))
        else:
            self.fail("the fixed original targets cannot sustain core-only rollover")
        self.assertLessEqual(len(history), 14)
        self.assertLess(len(history), 20)
        self.assertLessEqual(history.count("claude"), 6)
        self.assertLessEqual(history.count("cursor"), 5)
        self.assertLessEqual(history.count("grok"), 3)
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
