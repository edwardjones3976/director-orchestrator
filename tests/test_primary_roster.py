# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
import unittest
from director_orchestrator.primary_roster import PRIMARY_TEAM, PrimaryRosterHold, core_inventory_ready, select_primary_roster
from director_orchestrator.primary_workload import admission_reason

class PrimaryRosterTests(unittest.TestCase):
    def select(self, **kw):
        args = dict(available=("openai", "claude", "cursor", "grok"), history=(),
                    trusted_cost_ranks={"openai": 0, "claude": 2, "cursor": 1, "grok": 3, "antigravity": 4, "perplexity": 1, "firecrawl": 2})
        args.update(kw)
        return select_primary_roster(**args)

    def test_director_capability_is_explicit_and_legacy_default_is_preserved(self):
        claude_pool = ("claude", "cursor", "grok")
        self.assertFalse(core_inventory_ready(claude_pool))
        self.assertTrue(core_inventory_ready(claude_pool, director_provider="claude"))
        self.assertTrue(core_inventory_ready(("openai", "cursor", "grok")))
        self.assertFalse(core_inventory_ready(("openai", "cursor", "grok"), director_provider="claude"))
        self.assertFalse(core_inventory_ready(claude_pool, director_provider="cursor"))
        with self.assertRaisesRegex(PrimaryRosterHold, "core_capability"):
            self.select(available=claude_pool)
        roster = self.select(available=claude_pool, director_provider="claude")
        self.assertIn(roster.producer, claude_pool)
        self.assertNotEqual(roster.producer, roster.reviewer)
        with self.assertRaisesRegex(PrimaryRosterHold, "core_capability"):
            self.select(director_provider="unregistered-provider")

    def test_core_is_minimal_independent_and_cost_ranked(self):
        roster = self.select()
        self.assertEqual((roster.producer, roster.reviewer), ("openai", "cursor"))
        self.assertIsNone(roster.research)

    def test_startup_capacity_uses_only_real_assignments(self):
        self.assertIsNone(admission_reason("claude", []))
        self.assertIsNone(admission_reason("claude", ["claude"] * 5))
        self.assertEqual(admission_reason("claude", ["claude"] * 8), "workload_assignment_hard_limit")

    def test_openai_production_has_distinct_reviewer(self):
        selected = self.select()
        self.assertEqual(selected.producer, "openai")
        self.assertNotEqual(selected.reviewer, selected.producer)

    def test_optional_capabilities_are_explicit_and_fail_closed(self):
        with self.assertRaisesRegex(PrimaryRosterHold, "enterprise_scoped_adapter"):
            self.select(required_capabilities=("enterprise_context",))
        with self.assertRaisesRegex(PrimaryRosterHold, "scope_required"):
            self.select(required_capabilities=("public_research",))
        roster = self.select(available=("openai", "claude", "cursor", "perplexity"),
            allocation_profile="public_research", required_capabilities=("public_research",), public_research_query="public topic")
        self.assertEqual(roster.research, "perplexity")

    def test_missing_core_or_cost_authority_cannot_select(self):
        with self.assertRaises(PrimaryRosterHold):
            self.select(available=("claude", "cursor"))
        with self.assertRaisesRegex(PrimaryRosterHold, "cost_rank"):
            self.select(trusted_cost_ranks={})

    def test_shared_history_profile_switch_does_not_reset(self):
        prior = ["cursor"] * 4
        self.assertEqual(admission_reason("cursor", prior, allocation_profile="engineering"), "workload_assignment_hard_limit")
        self.assertEqual(admission_reason("cursor", prior, allocation_profile="public_research"), "workload_assignment_hard_limit")
        self.assertEqual(prior, ["cursor"] * 4)

    def test_research_is_projected_before_production_and_review(self):
        selected = self.select(available=("openai", "claude", "cursor", "grok", "perplexity"),
            allocation_profile="public_research", required_capabilities=("public_research",), public_research_query="public API")
        projected = []
        for provider in (selected.research, selected.producer, selected.reviewer):
            self.assertIsNone(admission_reason(provider, projected, allocation_profile="public_research"))
            projected.append(provider)

    def test_engineering_sustains_real_work_without_filler(self):
        history = []
        for _ in range(30):
            selected = self.select(history=history)
            self.assertNotEqual(selected.producer, selected.reviewer)
            history.extend((selected.producer, selected.reviewer))
        self.assertEqual(len(history), 60)

    def test_zero_target_is_ineligible_at_startup(self):
        self.assertEqual(admission_reason("perplexity", [], allocation_profile="engineering"), "workload_profile_lane_ineligible")

    def test_antigravity_requires_primary_verifier(self):
        roster = self.select(available=("openai", "claude", "grok", "antigravity"),
            required_capabilities=("bounded_scaffolding",),
            trusted_cost_ranks={"openai": 3, "claude": 2, "grok": 4, "antigravity": 0})
        self.assertEqual(roster.producer, "antigravity")
        self.assertIn(roster.reviewer, PRIMARY_TEAM)
        self.assertNotEqual(roster.reviewer, "antigravity")

    def test_antigravity_reviews_outside_team_mode_but_never_its_own_work(self):
        """Owner decision, 2026-09-08 UTC: review authority is not team-mode gated."""
        roster = self.select(available=("openai", "claude", "grok", "antigravity"),
            required_capabilities=("bounded_scaffolding",),
            trusted_cost_ranks={"openai": 0, "claude": 3, "grok": 4, "antigravity": 1})
        self.assertEqual((roster.producer, roster.reviewer), ("openai", "antigravity"))

    def test_explicit_team_mode_plans_engineering_without_scaffolding_permission(self):
        roster=self.select(available=("openai","claude","grok","antigravity"),team_mode=True,
            trusted_cost_ranks={"openai":3,"claude":2,"grok":4,"antigravity":0})
        self.assertEqual(roster.producer,"antigravity")
        self.assertNotEqual(roster.reviewer,"antigravity")

    def test_explicit_team_mode_can_plan_independent_antigravity_review(self):
        roster=self.select(available=("openai","claude","grok","antigravity"),team_mode=True,
            trusted_cost_ranks={"openai":0,"claude":3,"grok":4,"antigravity":1})
        self.assertEqual(roster.producer,"openai")
        self.assertEqual(roster.reviewer,"antigravity")

    def test_firecrawl_real_research_lane_and_no_implicit_scaffolding(self):
        roster = self.select(available=("openai", "claude", "cursor", "firecrawl", "antigravity"),
            allocation_profile="public_research", required_capabilities=("public_research",), public_research_query="public docs")
        self.assertEqual(roster.research, "firecrawl")
        self.assertNotEqual(roster.producer, "antigravity")

    def test_github_copilot_only_explicit_repository_assistance(self):
        args = dict(available=("openai", "claude", "cursor", "github-copilot"), allocation_profile="enterprise",
            trusted_cost_ranks={"openai": 4, "claude": 3, "cursor": 2, "github-copilot": 0})
        self.assertNotEqual(self.select(**args).producer, "github-copilot")
        args["required_capabilities"] = ("repository_assistance",)
        roster = self.select(**args)
        self.assertEqual(roster.producer, "github-copilot")
        self.assertIn(roster.reviewer, {"openai", "claude", "cursor", "grok"})
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]]
