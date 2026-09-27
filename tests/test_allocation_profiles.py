# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
import unittest
from director_orchestrator.allocation_profiles import (
    ALLOCATION_PROFILES, PRE_ZAI_ALLOCATION_PROFILES, ZAI_TARGET_PERCENT, allocation_targets,
    allocation_profile_digest, hard_ceiling_slots, redistribute_for_enrollment,
)
from director_orchestrator.workload_policy import WorkloadPolicy, DirectProductionProvider, direct_assignment_capacity_violation

class AllocationProfilesTests(unittest.TestCase):
    def test_all_nine_lanes_and_approved_targets(self):
        # Owner-confirmed Z.ai enrollment: zai 18 everywhere, the other nonzero
        # lanes reduced proportionally to 82 (largest remainder), zeros kept.
        expected = {"engineering": (33,21,12,8,8,0,0,0,18), "mixed":(22,27,9,8,4,4,4,4,18), "public_research":(16,21,4,4,4,21,12,0,18), "enterprise":(27,31,4,4,4,0,4,8,18)}
        for profile, weights in expected.items():
            self.assertEqual(tuple(allocation_targets(profile).values()), weights)
            self.assertEqual(sum(weights), 100)
            policy = WorkloadPolicy.for_profile(profile)
            self.assertEqual(sum(t.percent for t in policy.direct_production_targets),100)
        self.assertEqual(len({allocation_profile_digest(p) for p in expected}),4)

    def test_active_weights_are_the_documented_proportional_redistribution(self):
        for profile, previous in PRE_ZAI_ALLOCATION_PROFILES.items():
            derived = redistribute_for_enrollment(previous, "zai", ZAI_TARGET_PERCENT)
            self.assertEqual(derived, dict(ALLOCATION_PROFILES[profile]))
            self.assertEqual(sum(derived.values()), 100)
            for lane, weight in previous.items():
                if weight == 0:
                    self.assertEqual(derived[lane], 0, (profile, lane))
                else:
                    self.assertLessEqual(derived[lane], weight)
                    self.assertLess(abs(derived[lane] - weight * 0.82), 1, (profile, lane))
        with self.assertRaises(ValueError):
            redistribute_for_enrollment(PRE_ZAI_ALLOCATION_PROFILES["engineering"], "claude", 18)
        with self.assertRaises(ValueError):
            redistribute_for_enrollment(ALLOCATION_PROFILES["engineering"], "new", 100)

    def test_zai_hard_ceiling_is_target_plus_five_in_a_rolling_twenty(self):
        from director_orchestrator.primary_workload import admission_reason
        for profile in ALLOCATION_PROFILES:
            self.assertEqual(hard_ceiling_slots("zai", profile), 4)  # (18 + 5)% of 20 slots
            self.assertIsNone(hard_ceiling_slots("claude", profile))
            self.assertIsNone(admission_reason("zai", ["zai"] * 3, allocation_profile=profile))
            self.assertEqual(admission_reason("zai", ["zai"] * 4, allocation_profile=profile),
                             "workload_lane_hard_ceiling_reached")
        # Only the last nineteen admissions count toward the rolling twenty.
        self.assertIsNone(admission_reason("zai", ["zai"] * 4 + ["openai"] * 16))
        # The retired ceiling stays retired for the other lanes.
        self.assertIsNone(admission_reason("claude", ["claude"] * 19))
        policy = WorkloadPolicy.for_profile("engineering")
        from director_orchestrator.workload_policy import DirectProductionRecord
        history = [DirectProductionRecord(f"A{i}", DirectProductionProvider.ZAI) for i in range(4)]
        self.assertEqual(direct_assignment_capacity_violation(DirectProductionProvider.ZAI, history, policy=policy),
                         "allocation_lane_hard_ceiling_reached:zai")
        self.assertIsNone(direct_assignment_capacity_violation(DirectProductionProvider.ZAI, history[:3], policy=policy))

    def test_engineering_allocation_is_prospective_and_keeps_real_provider_history(self):
        from director_orchestrator.primary_workload import admission_reason
        openai_history = ["openai"] * 7
        claude_history = ["claude"] * 7
        self.assertIsNone(admission_reason("openai", openai_history))
        self.assertIsNone(admission_reason("claude", claude_history))
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
        for provider in (Provider.PERPLEXITY, Provider.FIRECRAWL):
            self.assertTrue(is_accepted_productive_kind(provider, WorkKind.RESEARCH))
            self.assertFalse(is_accepted_productive_kind(provider, WorkKind.COORDINATION))

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | END]]
