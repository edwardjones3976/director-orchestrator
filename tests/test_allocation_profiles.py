# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
import unittest
from director_orchestrator.allocation_profiles import ALLOCATION_PROFILES, allocation_targets, allocation_profile_digest
from director_orchestrator.workload_policy import WorkloadPolicy, DirectProductionProvider, direct_assignment_capacity_violation

class AllocationProfilesTests(unittest.TestCase):
    def test_all_nine_lanes_and_approved_targets(self):
        expected = {"engineering": (40,25,15,10,10,0,0,0,0), "mixed":(25,30,10,10,5,5,5,5,5), "public_research":(20,25,5,5,5,25,15,0,0), "enterprise":(25,30,5,5,5,0,5,10,15)}
        for profile, weights in expected.items():
            self.assertEqual(tuple(allocation_targets(profile).values()), weights)
            self.assertEqual(sum(weights), 100)
            policy = WorkloadPolicy.for_profile(profile)
            self.assertEqual(sum(t.percent for t in policy.direct_production_targets),100)
        self.assertEqual(len({allocation_profile_digest(p) for p in expected}),4)

    def test_engineering_allocation_is_prospective_and_keeps_real_provider_history(self):
        from director_orchestrator.primary_workload import admission_reason
        openai_history = ["openai"] * 7
        claude_history = ["claude"] * 7
        self.assertIsNone(admission_reason("openai", openai_history))
        self.assertEqual(admission_reason("claude", claude_history), "workload_assignment_hard_limit")
        self.assertEqual(openai_history, ["openai"] * 7)
        self.assertEqual(claude_history, ["claude"] * 7)

    def test_zero_lane_has_no_five_point_spill(self):
        self.assertIsNotNone(direct_assignment_capacity_violation(DirectProductionProvider.PERPLEXITY, [], policy=WorkloadPolicy.for_profile("engineering")))
        self.assertIsNone(direct_assignment_capacity_violation(DirectProductionProvider.PERPLEXITY, [], policy=WorkloadPolicy.for_profile("public_research")))

    def test_targets_are_immutable_and_unknown_profile_fails(self):
        with self.assertRaises(TypeError):
            ALLOCATION_PROFILES["engineering"]["openai"] = 100
        with self.assertRaises(ValueError):
            allocation_targets("automatic-renormalization")
    def test_research_credit_preserves_specialist_identity(self):
        from director_orchestrator.workload_policy import Provider, WorkKind, is_accepted_productive_kind
        for provider in (Provider.PERPLEXITY, Provider.FIRECRAWL, Provider.MICROSOFT_365_COPILOT):
            self.assertTrue(is_accepted_productive_kind(provider, WorkKind.RESEARCH))
            self.assertFalse(is_accepted_productive_kind(provider, WorkKind.COORDINATION))

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]]
