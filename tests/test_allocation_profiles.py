# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
import unittest
from director_orchestrator.allocation_profiles import ALLOCATION_PROFILES, allocation_targets, allocation_profile_digest
from director_orchestrator.workload_policy import WorkloadPolicy, DirectProductionProvider, direct_assignment_capacity_violation

class AllocationProfilesTests(unittest.TestCase):
    def test_all_nine_lanes_and_approved_targets(self):
        expected = {"engineering": (35,30,15,10,10,0,0,0,0), "mixed":(30,25,10,10,5,5,5,5,5), "public_research":(25,20,5,5,5,25,15,0,0), "enterprise":(30,25,5,5,5,0,5,10,15)}
        for profile, weights in expected.items():
            self.assertEqual(tuple(allocation_targets(profile).values()), weights)
            self.assertEqual(sum(weights), 100)
            policy = WorkloadPolicy.for_profile(profile)
            self.assertEqual(sum(t.percent for t in policy.direct_production_targets),100)
        self.assertEqual(len({allocation_profile_digest(p) for p in expected}),4)

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
