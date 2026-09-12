# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
import json
import unittest
from dataclasses import replace

from director_orchestrator.allocation_profiles import ALLOCATION_PROFILES, allocation_targets
from director_orchestrator.config import Settings
from director_orchestrator.primary_roster import (
    IMPLEMENTERS,
    PRIMARY_TEAM,
    KNOWN_LANES,
    RESEARCH_LANES,
    PrimaryRosterHold,
    core_inventory_ready,
    lanes_requiring_cost_rank,
    select_primary_roster,
)
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

    def test_explicit_team_mode_plans_engineering_without_scaffolding_permission(self):
        roster=self.select(available=("openai","claude","grok","antigravity"),team_mode=True,
            trusted_cost_ranks={"openai":3,"claude":2,"grok":4,"antigravity":0})
        self.assertEqual(roster.producer,"antigravity")
        self.assertNotEqual(roster.reviewer,"antigravity")

    def test_legacy_mode_does_not_select_antigravity_as_reviewer(self):
        roster = self.select(available=("openai", "claude", "grok", "antigravity"), team_mode=False,
            trusted_cost_ranks={"openai": 0, "claude": 3, "grok": 4, "antigravity": 1})
        self.assertEqual(roster.producer, "openai")
        self.assertNotEqual(roster.reviewer, "antigravity")

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


_ACTIVATION_BINDING = {
    "workspace_id": "default-director",
    "provider_version": "v1",
    "app_version": "v1",
    "runtime_adapter_version": "v1",
    "workspace_version": "v1",
    "binding_version": "v1",
}


class SelectableLaneCostRankTests(unittest.TestCase):
    """A selectable lane without a cost rank must fail at configuration time.

    ``eligible_for`` already refuses, but it holds the *entire* roster rather
    than degrading the one lane, and it does so only once a task reaches
    dispatch. These cover the earlier refusal without weakening the later one.
    """

    def settings(self, *, bound, ranks):
        return replace(
            Settings(),
            allow_paid_runs=True,
            workload_policy_enabled=True,
            workload_policy_mode="enforced",
            primary_workhorse="cheapest_capable",
            trusted_provider_cost_ranks_json=json.dumps(ranks),
            langgraph_primary_provider_activation_bindings_json=json.dumps(
                {lane: _ACTIVATION_BINDING for lane in bound}
            ),
            # Keep exactly one credential-ready specialist so the pre-existing
            # ready-specialist check is satisfied and cannot mask this one.
            claude_routine_url="https://example.invalid/claude",
            claude_routine_token="token",
            cursor_api_key="",
            gemini_api_key="",
            grok_api_key="",
            perplexity_api_key="",
        )

    def test_startup_is_refused_and_names_the_unranked_selectable_lane(self):
        settings = self.settings(
            bound=("openai", "claude", "cursor", "grok"),
            ranks={"openai": 4, "claude": 1, "cursor": 2},
        )

        self.assertEqual(("grok",), settings.unranked_selectable_primary_lanes())
        with self.assertRaises(ValueError) as caught:
            settings.validate_workload_policy_configuration()
        self.assertIn("TRUSTED_PROVIDER_COST_RANKS", str(caught.exception))
        self.assertIn("grok", str(caught.exception))

    def test_startup_succeeds_once_every_selectable_lane_is_ranked(self):
        settings = self.settings(
            bound=("openai", "claude", "cursor", "grok"),
            ranks={"openai": 4, "claude": 1, "cursor": 2, "grok": 3},
        )

        self.assertEqual((), settings.unranked_selectable_primary_lanes())
        settings.validate_workload_policy_configuration()


    def test_an_unbound_lane_needs_no_rank(self):
        # provider_ready is False without a binding, and the roster's available
        # pool is filtered through it, so an unbound lane never becomes a
        # candidate. Requiring its rank would refuse a working deployment.
        settings = replace(
            self.settings(
                bound=("openai", "claude", "cursor"),
                ranks={"openai": 4, "claude": 1, "cursor": 2},
            ),
            langgraph_primary_required_providers=(
                "openai", "claude", "cursor", "grok", "antigravity",
            ),
        )

        self.assertEqual((), settings.unranked_selectable_primary_lanes())
        settings.validate_workload_policy_configuration()

    def test_capability_gated_lanes_still_require_a_rank(self):
        # antigravity and github-copilot are reachable behind a capability gate,
        # but the capability arrives with the intake rather than the
        # configuration, so a bound gated lane is always required to be ranked.
        settings = self.settings(
            bound=("openai", "claude", "cursor", "antigravity", "github-copilot"),
            ranks={"openai": 4, "claude": 1, "cursor": 2},
        )

        self.assertEqual(
            ("antigravity", "github-copilot"),
            settings.unranked_selectable_primary_lanes(),
        )

    def test_a_gemini_rank_satisfies_the_antigravity_lane(self):
        # TRUSTED_PROVIDER_COST_RANKS normalizes antigravity to gemini, and
        # canonical_allocation_lane maps it back, so no separate rank exists.
        settings = self.settings(
            bound=("openai", "claude", "cursor", "antigravity"),
            ranks={"openai": 4, "claude": 1, "cursor": 2, "gemini": 3},
        )

        self.assertIn("gemini", settings.trusted_provider_cost_ranks())
        self.assertNotIn("antigravity", settings.trusted_provider_cost_ranks())
        self.assertEqual((), settings.unranked_selectable_primary_lanes())
        settings.validate_workload_policy_configuration()

        aliased = self.settings(
            bound=("openai", "claude", "cursor", "antigravity"),
            ranks={"openai": 4, "claude": 1, "cursor": 2, "antigravity": 3},
        )
        self.assertIn("gemini", aliased.trusted_provider_cost_ranks())
        self.assertEqual((), aliased.unranked_selectable_primary_lanes())

    def test_a_lane_with_a_zero_target_under_one_profile_still_needs_a_rank(self):
        # The allocation profile arrives with the intake, so a zero target under
        # engineering does not make a lane unselectable; another profile admits
        # it. Only a lane no profile can admit is exempt.
        self.assertEqual(0, allocation_targets("engineering")["github-copilot"])
        self.assertEqual(
            "workload_profile_lane_ineligible",
            admission_reason("github-copilot", [], allocation_profile="engineering"),
        )
        self.assertIn("github-copilot", lanes_requiring_cost_rank(KNOWN_LANES))

        self.assertEqual(
            [],
            [lane for lane in KNOWN_LANES
             if not any(allocation_targets(profile).get(lane, 0) > 0
                        for profile in ALLOCATION_PROFILES)],
            "no known lane is currently zero under every profile; the exemption "
            "exists so a lane that becomes so is dropped without editing this",
        )

    def test_derivation_tracks_the_roster_tuples_rather_than_a_copy(self):
        selectable = frozenset(IMPLEMENTERS) | frozenset(RESEARCH_LANES)
        self.assertEqual(selectable & KNOWN_LANES,
                         lanes_requiring_cost_rank(KNOWN_LANES))
        self.assertEqual(frozenset(), lanes_requiring_cost_rank(()))
        # An identity outside the target table can never be admitted.
        self.assertEqual(frozenset({"openai"}),
                         lanes_requiring_cost_rank({"openai", "not-a-lane"}))

    def test_the_dispatch_time_roster_hold_still_fires(self):
        # Proof this is an additional gate, not a replacement: the same partial
        # rank map that configuration now refuses still holds the roster.
        with self.assertRaisesRegex(PrimaryRosterHold, "primary_trusted_cost_rank_required"):
            select_primary_roster(
                available=("openai", "claude", "cursor", "grok"),
                history=(),
                trusted_cost_ranks={"openai": 4, "claude": 1, "cursor": 2},
            )
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | END]]
