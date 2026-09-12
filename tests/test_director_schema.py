# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:21 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 21:59 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 21:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:11 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 10:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:58 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:25 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 01:31 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 01:27 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 01:13 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 22:28 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 22:20 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 22:10 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 22:30 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 22:05 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 21:35 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:02 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.allocation_profiles import ALLOCATION_PROFILES
from director_orchestrator.config import Settings
from director_orchestrator.director import OpenAIDirector, _strict_schema
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import ProviderTransport
from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
    DebatePlan,
    DirectorPlan,
    IntakeAuthentication,
    ProviderName,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore
from director_orchestrator.antigravity import (
    DENIED_AUTHORITIES,
    WITHHELD_CONTEXT,
)
from director_orchestrator.team_charter import (
    AdvisoryVote,
    AdvisoryVoteTally,
    TEAM_CHARTER_MAX_BYTES,
    canonical_director_operating_directive_json,
    canonical_team_charter_json,
    canonical_team_charter_manifest_json,
    director_operating_directive_sha256,
    tally_advisory_votes,
    team_charter_manifest,
    team_operating_charter,
)
from tests.test_slack_debate_bus import (
    CompletingAdapter,
    RecordingNotifier,
    debate_plan,
)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


class StrictSchemaTests(unittest.TestCase):
    def test_every_object_requires_all_properties_and_forbids_extras(self):
        for model, name in ((DirectorPlan, "plan"), (ReviewDecision, "review")):
            schema = _strict_schema(model, name)["schema"]
            for node in _walk(schema):
                self.assertNotIn("default", node)
                if isinstance(node.get("properties"), dict):
                    self.assertFalse(node["additionalProperties"])
                    self.assertEqual(set(node["required"]), set(node["properties"]))


class DirectorReviewPhaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_budget_estimate_includes_both_debate_control_calls(self):
        class CapturingDirector(OpenAIDirector):
            async def _structured(self, prompt, model, schema_name):
                self.captured = json.loads(prompt)
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Accepted.",
                    evidence=[],
                )

        record = TaskRecord(
            task_id="DIR-REVIEW-DEBATE-BUDGET",
            status=TaskStatus.REVIEWING,
            intake=SlackIntake(
                source_event_id="evt-review-debate-budget",
                channel_id="C123",
                thread_ts="123.47",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: verify debate budget",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="verify debate budget",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=2,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CLAUDE,
                        task="Assess A",
                        acceptance=["Evidence recorded"],
                        estimated_cost_usd=0.10,
                    ),
                    Assignment(
                        sequence=2,
                        agent=ProviderName.CURSOR,
                        task="Assess B",
                        acceptance=["Evidence recorded"],
                        estimated_cost_usd=0.20,
                    ),
                ],
                final_acceptance=["Budget estimate is complete"],
                debate=DebatePlan(
                    question="A or B?",
                    position_assignments=[1, 2],
                    rebuttal_assignments=[1, 2],
                    vote_options=["A", "B"],
                    estimated_counter_bias_cost_usd=0.30,
                    estimated_synthesis_cost_usd=0.40,
                ),
            ),
        )
        director = CapturingDirector(replace(Settings(), openai_api_key="test-openai"))

        await director.review(record, "Claude supplied bounded evidence.")

        # [[Codex | GPT-5.6 | 2026-08-25 | 00:44 UTC | BEGIN]]
        # $1.00 of assignments/debate plus one $0.01 acceptance review for
        # each of the two assignments.
        self.assertAlmostEqual(
            director.captured["plan_context"]["estimated_total_cost_usd"], 1.02
        )
        # [[Codex | GPT-5.6 | 2026-08-25 | 00:44 UTC | END]]

    async def test_intermediate_review_excludes_future_final_acceptance(self):
        class CapturingDirector(OpenAIDirector):
            async def _structured(self, prompt, model, schema_name):
                self.captured = json.loads(prompt)
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Accepted.",
                    evidence=[],
                )

        assignments = [
            Assignment(
                sequence=1,
                agent=ProviderName.CLAUDE,
                task="Analyze",
                acceptance=["Claude evidence is complete"],
                repository="example/demo",
            ),
            Assignment(
                sequence=2,
                agent=ProviderName.CURSOR,
                task="Review independently",
                acceptance=["Cursor evidence is complete"],
                repository="example/demo",
                depends_on=[1],
            ),
        ]
        record = TaskRecord(
            task_id="DIR-REVIEW-PHASE",
            status=TaskStatus.REVIEWING,
            intake=SlackIntake(
                source_event_id="evt-review-phase",
                channel_id="C123",
                thread_ts="123.48",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: analyze then review",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="analyze then review",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=2,
                max_revisions=1,
                assignments=assignments,
                final_acceptance=["Both specialists reconciled"],
            ),
        )
        director = CapturingDirector(replace(Settings(), openai_api_key="test-openai"))

        await director.review(record, "Claude found NOT_READY with complete evidence.")

        self.assertFalse(director.captured["is_final_specialist_assignment"])
        self.assertNotIn("team_operating_charter", director.captured)
        self.assertEqual(director.captured["current_assignment"]["sequence"], 1)
        self.assertEqual(director.captured["final_acceptance"], [])
        self.assertIn("Do not require future assignments", director.captured["instruction"])
        self.assertIn("do not reject or escalate", director.captured["instruction"])
        # [[Codex | GPT-5.6 | 2026-08-25 | 00:44 UTC | BEGIN]]
        # Two assignments with one possible revision reserve four review calls.
        self.assertEqual(
            director.captured["plan_context"]["estimated_total_cost_usd"], 0.04
        )
        # [[Codex | GPT-5.6 | 2026-08-25 | 00:44 UTC | END]]
        self.assertEqual(
            director.captured["plan_context"]["configured_max_task_budget_usd"],
            director.settings.max_task_budget_usd,
        )

        record.next_assignment_index = 1
        record.assignment_outcomes = [
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.CLAUDE,
                run_id="claude-1",
                result="NOT_READY with evidence",
                review_summary="Accepted as complete.",
                evidence=["Exact SHA"],
            )
        ]
        await director.review(record, "Cursor independently confirms NOT_READY.")

        self.assertTrue(director.captured["is_final_specialist_assignment"])
        self.assertEqual(director.captured["current_assignment"]["sequence"], 2)
        self.assertEqual(director.captured["final_acceptance"], ["Both specialists reconciled"])
        self.assertEqual(director.captured["prior_accepted_outcomes"][0]["run_id"], "claude-1")


class DirectorTeamCharterTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_receives_the_owner_authorized_team_charter(self):
        class CapturingDirector(OpenAIDirector):
            async def _structured(self, prompt, model, schema_name):
                self.captured = json.loads(prompt)
                return DirectorPlan(
                    project="demo",
                    normalized_outcome="Assess alternatives",
                    risk_level=RiskLevel.MEDIUM,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.OWNER,
                            task="Select an option",
                            acceptance=["Decision recorded"],
                        )
                    ],
                    final_acceptance=["Decision recorded"],
                )

        record = TaskRecord(
            task_id="DIR-TEAM-CHARTER",
            status=TaskStatus.PLANNING,
            intake=SlackIntake(
                source_event_id="evt-team-charter",
                channel_id="C123",
                thread_ts="123.49",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: assess medium-complexity alternatives",
            ),
        )
        director = CapturingDirector(replace(Settings(), openai_api_key="test-openai"))

        await director.plan(record)

        for profile, targets in ALLOCATION_PROFILES.items():
            record.intake.allocation_profile = profile
            await director.plan(record)
            self.assertEqual(director.captured["allocation_profile"], profile)
            self.assertEqual(director.captured["allocation_targets"], dict(targets))
            rules = " ".join(director.captured["routing_rules"])
            for lane, weight in targets.items():
                self.assertIn(f"{lane} {weight}%", rules)
            self.assertNotIn("25/25/20/15/11/4", rules)
            self.assertLess(rules.index("total-effort soft-preference rank"), rules.index("finally trusted cheapest cost rank"))
            self.assertIn("allocation_profile and allocation_targets", director.instructions)
            self.assertNotIn("Engineering is OpenAI 40%", director.instructions)

        charter = director.captured["team_operating_charter"]
        self.assertEqual(charter["schema_version"], "director-team-charter/v1")
        self.assertEqual(
            [area["name"] for area in charter["responsibility_areas"]],
            [
                "research",
                "evidence_verification",
                "programming",
                "deep_reasoning",
                "innovation",
                "development",
                "quality_assurance",
                "leadership",
            ],
        )
        self.assertEqual(
            {
                name: member["voting_power"]
                for name, member in charter["committee"]["members"].items()
            },
            {"antigravity": 1.0, "cursor": 1.5, "grok": 1.5, "openai": 2.5, "claude": 2.0},
        )
        self.assertEqual(charter["committee"]["voting"]["mode"], "advisory")
        self.assertIn("medium", charter["committee"]["debate"]["recommended_for_complexity"])
        self.assertIn("committee votes", charter["perplexity_contract"]["prohibited"])
        directive = charter["director_operating_directive"]
        self.assertEqual(directive["default_allocation_profile"], "engineering")
        self.assertEqual(directive["allocation_profiles"]["weights"]["engineering"], [40, 25, 15, 10, 10, 0, 0, 0])
        self.assertEqual(len(directive["allocation_profiles"]["lane_order"]), 8)
        self.assertEqual(
            directive["workload_accounting"]["ledgers"]["overall_effort"],
            "prospective_observed_effort_guard",
        )
        self.assertEqual(
            directive["coding_routing"]["selection_policy"],
            "cheap_capable_model_first",
        )
        self.assertFalse(
            directive["coding_routing"][
                "unconditional_claude_medium_or_long_requirement"
            ]
        )
        self.assertFalse(
            directive["workload_enforcement"]["overall_effort_can_block_or_route"]
        )
        self.assertIn("estimated_roi", directive["roi_and_waste"])
        self.assertFalse(directive["copilot_lite_hygiene_review"]["gating"])
        self.assertIsNone(
            directive["copilot_lite_hygiene_review"]["workload_percentage"]
        )
        self.assertEqual(
            directive["coding_routing"]["selection_order"][2],
            "direct_allocation_compliance_and_under_target_preference",
        )
        self.assertNotIn(
            "github_copilot_cli_lite",
            directive["allocation_profiles"]["lane_order"],
        )
        self.assertEqual(
            directive["copilot_lite_hygiene_review"]["attempts"],
            "conditional_maximum_three",
        )
        self.assertTrue(
            directive["pre_staging"]["required_before_provider_dispatch"]
        )
        self.assertEqual(
            directive["pre_staging"]["revision_mode"],
            "immutable_canonical_packet_revisions",
        )
        self.assertIn(
            "user_reviews_and_real_world_experience",
            directive["research_policy"]["triggers"],
        )
        self.assertIn(
            "competitor_solutions_to_similar_issues",
            directive["research_policy"]["triggers"],
        )
        browser = charter["browser_communication_policy"]
        self.assertEqual(browser["primary"], "chrome")
        self.assertEqual(browser["fallback"], "microsoft_edge")
        self.assertEqual(
            browser["priority"],
            "lower_than_authenticated_cli_api_callback_or_event_transport",
        )
        self.assertIsNone(browser["usage_reduction_target"])
        self.assertEqual(browser["eligible_providers"], ["claude", "cursor", "gemini", "grok"])
        self.assertEqual(
            browser["owner_authorizable_actions"],
            [
                "slack_post",
                "eligible_merge",
                "approved_deployment",
                "scoped_repository_access_change",
            ],
        )
        self.assertTrue(any("untrusted coordination evidence" in rule for rule in browser["rules"]))
        self.assertTrue(
            any("applicable deterministic gate" in rule for rule in browser["rules"])
        )
        cursor = charter["role_profiles"]["cursor"]
        self.assertEqual(cursor["capability_parity_target"], 1.0)
        self.assertEqual(cursor["comparison_peers"], ["claude", "openai_codex"])
        self.assertEqual(cursor["eligible_complexity"], ["low", "medium", "high", "critical"])
        self.assertEqual(
            cursor["eligible_responsibilities"],
            [area["name"] for area in charter["responsibility_areas"]],
        )
        self.assertTrue(any("own work" in rule for rule in cursor["non_substitution_rules"]))
        grok = charter["role_profiles"]["grok"]
        self.assertEqual(grok["family"], "cursor")
        self.assertEqual(grok["capability_parity_target"], 1.0)
        self.assertEqual(grok["eligible_responsibilities"], cursor["eligible_responsibilities"])
        self.assertTrue(any("own work" in rule for rule in grok["non_substitution_rules"]))
        self.assertTrue(
            any(
                "deterministic policy" in rule.lower()
                for rule in charter["committee"]["voting"]["rules"]
            )
        )
        efficiency = charter["efficiency_policy"]
        self.assertTrue(efficiency["concise_evidence_first"])
        self.assertTrue(efficiency["repeat_requires_new_information"])
        self.assertTrue(efficiency["event_driven_status"])
        self.assertTrue(efficiency["browser_enabled_lower_priority"])
        self.assertIsNone(efficiency["browser_usage_reduction_target"])
        self.assertTrue(any("information gain" in rule for rule in efficiency["rules"]))
        self.assertTrue(any("safety evidence" in rule for rule in efficiency["rules"]))
        self.assertTrue(any("dependencies" in rule for rule in efficiency["rules"]))
        self.assertEqual(efficiency["minimal_fix_attempt_limit"], 3)
        self.assertTrue(efficiency["minimal_fix_invalidates_evidence"])

    async def test_repeated_plans_inject_one_bounded_immutable_charter_each(self):
        class CapturingDirector(OpenAIDirector):
            def __init__(self, settings):
                super().__init__(settings)
                self.captured = []

            async def _structured(self, prompt, model, schema_name):
                payload = json.loads(prompt)
                self.captured.append(payload)
                return DirectorPlan(
                    project="demo",
                    normalized_outcome="Assess alternatives",
                    risk_level=RiskLevel.MEDIUM,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.OWNER,
                            task="Select an option",
                            acceptance=["Decision recorded"],
                        )
                    ],
                    final_acceptance=["Decision recorded"],
                )

        record = TaskRecord(
            task_id="DIR-CHARTER-REPEAT",
            status=TaskStatus.PLANNING,
            intake=SlackIntake(
                source_event_id="evt-charter-repeat",
                channel_id="C123",
                thread_ts="123.50",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: compare options",
            ),
        )
        director = CapturingDirector(Settings())

        await director.plan(record)
        await director.plan(record)

        self.assertEqual(len(director.captured), 2)
        self.assertTrue(all(list(item).count("team_operating_charter") == 1 for item in director.captured))
        self.assertTrue(
            all(
                item["team_operating_charter"] == director.captured[0]["team_operating_charter"]
                for item in director.captured
            )
        )
        self.assertLessEqual(len(canonical_team_charter_json().encode("utf-8")), TEAM_CHARTER_MAX_BYTES)

    def test_acting_leadership_does_not_relabel_the_runtime_or_capacity(self):
        directive = team_operating_charter()["director_operating_directive"]
        self.assertEqual(directive["director"], "claude")
        self.assertEqual(directive["runtime_director"]["default_provider"], "openai")
        self.assertFalse(directive["runtime_director"]["activation_from_role_change"])
        self.assertEqual(directive["capacity_context"], "owner_Pro20x_Max5x_not_API_entitlements")
        self.assertTrue(directive["duties_excluded_from_direct_production_share"])
        self.assertEqual(directive["workload_lanes"]["openai"], "primary_full_spectrum")
        self.assertEqual(directive["workload_lanes"]["claude"], "control_plane_and_authorized_production")

    def test_historical_openai_tally_round_trips_without_relabeling(self):
        historical = dict(configured_weight_total=8.5, cast_weight_total=4.0,
            option_weights={"A": 2.0, "B": 2.0}, abstaining=[], unavailable=[],
            winning_options=["A", "B"], resolution="openai_director_judgment",
            authorizes_external_action=False)
        self.assertEqual(AdvisoryVoteTally.model_validate(historical).model_dump(), historical)

    def test_machine_and_markdown_charter_manifest_are_exact(self):
        document = (
            Path(__file__).resolve().parents[1] / "docs" / "team-operating-charter.md"
        ).read_text(encoding="utf-8")
        marker = document.split("<!-- TEAM_CHARTER_MANIFEST_BEGIN -->", 1)[1]
        fenced = marker.split("<!-- TEAM_CHARTER_MANIFEST_END -->", 1)[0]
        documented = fenced.split("```json", 1)[1].split("```", 1)[0].strip()

        self.assertEqual(documented, canonical_team_charter_manifest_json())

    def test_manifest_binds_the_complete_director_operating_directive(self):
        manifest = team_charter_manifest()
        directive = team_operating_charter()["director_operating_directive"]

        self.assertEqual(manifest["director_operating_directive"], directive)
        self.assertEqual(
            manifest["director_operating_directive_sha256"],
            director_operating_directive_sha256(),
        )
        self.assertEqual(
            director_operating_directive_sha256(),
            hashlib.sha256(
                canonical_director_operating_directive_json().encode("utf-8")
            ).hexdigest(),
        )

    def test_full_directive_fingerprint_is_synchronized_across_surfaces(self):
        root = Path(__file__).resolve().parents[1]
        expected = (
            "DIRECTOR_OPERATING_DIRECTIVE_SHA256="
            f"{director_operating_directive_sha256()}"
        )
        surfaces = [
            root / "docs" / "team-operating-charter.md",
            root / "prompts" / "director-system.md",
            root / "README.md",
            root
            / "docs"
            / "templates"
            / "project-bootstrap"
            / "project-state-report.md",
        ]

        for surface in surfaces:
            content = surface.read_text(encoding="utf-8")
            matching_lines = [
                line
                for line in content.splitlines()
                if "DIRECTOR_OPERATING_DIRECTIVE_SHA256=" in line
            ]
            self.assertEqual(matching_lines, [f"<!-- {expected} -->"], surface)

    def test_openai_external_production_boundary_is_synchronized_across_surfaces(self):
        root = Path(__file__).resolve().parents[1]
        charter = (root / "docs" / "team-operating-charter.md").read_text(
            encoding="utf-8"
        )
        prompt = (root / "prompts" / "director-system.md").read_text(
            encoding="utf-8"
        )
        readme = (root / "README.md").read_text(encoding="utf-8")
        report = (
            root
            / "docs"
            / "templates"
            / "project-bootstrap"
            / "project-state-report.md"
        ).read_text(encoding="utf-8")

        self.assertIn("OpenAI production may be separately assigned", " ".join(charter.split()))
        self.assertIn("OpenAI production may be separately assigned", prompt)
        self.assertIn("OpenAI production uses a separately assigned", readme)
        self.assertIn("different-provider exact-candidate review", charter)
        self.assertIn("different-provider exact-candidate review", prompt)
        self.assertIn("Different-provider exact-candidate review", readme)
        self.assertIn("non-author review/merge", report)

    def test_balanced_allocation_selection_and_copilot_are_synchronized(self):
        root = Path(__file__).resolve().parents[1]
        surfaces = {
            "charter": root / "docs" / "team-operating-charter.md",
            "prompt": root / "prompts" / "director-system.md",
            "readme": root / "README.md",
            "setup": root / "docs" / "provider-setup.md",
            "protocol": root / "docs" / "project-bootstrap-protocol.md",
            "report": root
            / "docs"
            / "templates"
            / "project-bootstrap"
            / "project-state-report.md",
            "roles": root
            / "docs"
            / "templates"
            / "project-bootstrap"
            / "provider-roles-contract.yaml",
        }
        content = {
            name: path.read_text(encoding="utf-8") for name, path in surfaces.items()
        }

        for name, text in content.items():
            with self.subTest(surface=name):
                normalized = " ".join(text.split()).lower()
                self.assertIn("allocation", normalized)
                self.assertIn("profile", normalized)
                self.assertIn("copilot", normalized)
                self.assertIn("waste", normalized)

        for name in ("charter", "prompt", "readme", "setup", "protocol", "roles"):
            with self.subTest(cheap_capable_surface=name):
                normalized_policy = (
                    content[name].lower().replace("_", " ").replace("-", " ")
                )
                self.assertTrue(
                    "cheap capable" in normalized_policy
                    or "cheapest capable" in normalized_policy,
                    name,
                )

        directive = team_operating_charter()["director_operating_directive"]
        self.assertEqual(set(directive["allocation_profiles"]["weights"]), {"engineering", "mixed", "public_research", "enterprise"})
        for weights in directive["allocation_profiles"]["weights"].values():
            self.assertEqual(sum(weights), 100)
        self.assertEqual(
            directive["workload_accounting"]["unit_basis"],
            "real_reserved_assignment_once",
        )
        self.assertEqual(
            directive["overall_effort_policy"],
            "telemetry_only_no_separate_allocation",
        )

    def test_antigravity_work_and_debate_boundaries_are_synchronized(self):
        root = Path(__file__).resolve().parents[1]
        charter = (root / "docs" / "team-operating-charter.md").read_text(
            encoding="utf-8"
        )
        setup = (root / "docs" / "provider-setup.md").read_text(encoding="utf-8")
        prompt = (root / "prompts" / "director-system.md").read_text(
            encoding="utf-8"
        )
        protocol = (root / "docs" / "project-bootstrap-protocol.md").read_text(
            encoding="utf-8"
        )
        profile = (
            root
            / "docs"
            / "templates"
            / "project-bootstrap"
            / "project-profile.yaml"
        ).read_text(encoding="utf-8")
        workstream = (
            root
            / "docs"
            / "templates"
            / "project-bootstrap"
            / "workstream-plan.md"
        ).read_text(encoding="utf-8")

        for surface in (charter, setup, prompt, protocol, workstream):
            self.assertIn("cannot initiate", surface)

        self.assertIn("Ordinary Antigravity *work*", charter)
        self.assertIn("Ordinary Antigravity work dispatch", setup)
        self.assertIn("Ordinary Antigravity work", prompt)
        self.assertIn("separate typed contract", charter)
        self.assertIn("typed debate contract", setup)
        self.assertIn("separate typed debate contract", prompt)
        self.assertNotIn("Antigravity cannot debate", prompt)
        self.assertNotIn("Antigravity cannot participate", protocol)
        self.assertNotIn("never POSITION/REBUTTAL", profile)
        self.assertIn("matching persisted", profile)
        self.assertIn("may not initiate", profile)
        self.assertNotIn("Claude and Cursor only", workstream)
        self.assertNotIn("Antigravity cannot be seated", workstream)
        self.assertNotIn("never cast", workstream)
        self.assertIn("matching persisted", workstream)
        self.assertNotIn(
            "sends only the assignment's own bounded task and acceptance checks",
            setup,
        )

    def test_minimal_fix_policy_is_present_across_authoritative_surfaces(self):
        root = Path(__file__).resolve().parents[1]
        surfaces = [
            root / "docs" / "team-operating-charter.md",
            root / "docs" / "project-bootstrap-protocol.md",
            root / "docs" / "templates" / "project-bootstrap" / "workstream-plan.md",
            root / "docs" / "templates" / "project-bootstrap" / "minimal-fix.md",
            root / "prompts" / "director-system.md",
        ]
        for surface in surfaces:
            self.assertIn("MINIMAL_FIX", surface.read_text(encoding="utf-8"), surface)

        profile = (
            root / "docs" / "templates" / "project-bootstrap" / "project-profile.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("minimal_fix:", profile)
        self.assertIn("attempt_limit: 3", profile)
        self.assertIn("changed_candidate_invalidates_evidence: true", profile)

    def test_pplx_cli_skill_is_installed_and_research_only_across_surfaces(self):
        root = Path(__file__).resolve().parents[1]
        skill = root / ".agents" / "skills" / "pplx-cli" / "SKILL.md"
        skill_content = skill.read_text(encoding="utf-8")

        self.assertIn("name: pplx-cli", skill_content)
        self.assertIn("pplx search web", skill_content)
        surfaces = [
            root / "docs" / "team-operating-charter.md",
            root / "docs" / "project-bootstrap-protocol.md",
            root / "docs" / "templates" / "project-bootstrap" / "README.md",
            root / "docs" / "templates" / "project-bootstrap" / "workstream-plan.md",
            root / "prompts" / "director-system.md",
        ]
        for surface in surfaces:
            content = surface.read_text(encoding="utf-8")
            matching_blocks = [
                block
                for block in content.split("\n\n")
                if "`pplx-cli`" in block and "research-only" in block
            ]
            self.assertTrue(matching_blocks, surface)
        protocol = (root / "docs" / "project-bootstrap-protocol.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Linux-only `pplx-cli`", protocol)
        self.assertIn("project WSL", protocol)

    def test_provider_cannot_mutate_canonical_roles_or_supply_weights(self):
        mutable = team_operating_charter()
        mutable["committee"]["members"]["cursor"]["voting_power"] = 99
        mutable["efficiency_policy"]["repeat_requires_new_information"] = False
        mutable["efficiency_policy"]["minimal_fix_attempt_limit"] = 99
        self.assertEqual(
            team_operating_charter()["committee"]["members"]["cursor"]["voting_power"],
            1.5,
        )
        self.assertTrue(
            team_operating_charter()["efficiency_policy"][
                "repeat_requires_new_information"
            ]
        )
        self.assertEqual(
            team_operating_charter()["efficiency_policy"]["minimal_fix_attempt_limit"],
            3,
        )
        with self.assertRaises(ValueError):
            AdvisoryVote.model_validate(
                {
                    "member": "cursor",
                    "disposition": "vote",
                    "option": "A",
                    "rationale": "Evidence supports A.",
                    "weight": 99,
                }
            )

    def test_producer_vote_counts_fully_while_abstention_cannot_change_denominator(self):
        tally = tally_advisory_votes(
            [
                AdvisoryVote(
                    member="claude",
                    disposition="vote",
                    option="A",
                    rationale="Producer supports A.",
                    producer=True,
                ),
                AdvisoryVote(
                    member="cursor",
                    disposition="abstain",
                    rationale="Conflict prevents a vote.",
                ),
                AdvisoryVote(
                    member="grok",
                    disposition="unavailable",
                    rationale="Three safe attempts received no response.",
                ),
                AdvisoryVote(
                    member="antigravity",
                    disposition="unavailable",
                    rationale="Three safe attempts received no response.",
                ),
            ]
        )

        self.assertEqual(tally.option_weights, {"A": 2.0})
        self.assertEqual(tally.cast_weight_total, 2.0)
        self.assertEqual(tally.configured_weight_total, 8.5)
        self.assertFalse(tally.authorizes_external_action)

    def test_ties_route_to_claude_or_owner_without_authorizing_action(self):
        votes = [
            AdvisoryVote(
                member="openai",
                disposition="vote",
                option="A",
                rationale="Evidence supports A.",
            ),
            AdvisoryVote(
                member="cursor",
                disposition="vote",
                option="B",
                rationale="Evidence supports B.",
            ),
            AdvisoryVote(
                member="antigravity",
                disposition="vote",
                option="B",
                rationale="Prototype supports B.",
            ),
        ]

        ordinary = tally_advisory_votes(votes)
        material = tally_advisory_votes(votes, material_risk=True)

        self.assertEqual(ordinary.option_weights, {"A": 2.5, "B": 2.5})
        self.assertEqual(ordinary.resolution, "claude_director_judgment")
        self.assertEqual(material.resolution, "owner_decision_required")
        self.assertFalse(ordinary.authorizes_external_action)
        self.assertFalse(material.authorizes_external_action)


class ModeratorVoteDocumentationTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    @staticmethod
    def unconditional_claims(text):
        """Match KNOWN LEXICAL PATTERNS.

        This guard is NOT exhaustive over natural-language paraphrase.
        A passing run means "no known unconditional phrasing", never
        "no unconditional claim exists". A semantic reintroduction can pass.
        A human reviewing documentation wording must read it and must not treat
        this test as a substitute for reading it.

        Matching is clause-local and some spans are length-bounded. It does not
        resolve cross-sentence pronouns or infer obligations from arbitrary
        synonyms (for example, an implicit requirement to submit a "ballot").

        Known, accepted gaps (documented examples, not rejection patterns):
        - "Claude's participation entails a vote each time."
        - "No debate concludes without the Participant vote."
        - "Withholding the Claude vote is not an option."
        - "Claude will have voted in all debates."
        """
        # Inspect separate bullets, YAML fields, table cells, and sentences so
        # an abstention elsewhere cannot excuse an unconditional local claim.
        blocks = re.split(r"\n\s*\n|\n(?=\s*(?:[-*] |[\w-]+:|\|))", text)
        problems = []
        for block in blocks:
            normalized = " ".join(block.split()).replace("\u2019", "'")
            for clause in re.split(r"[.!?](?=\s|$)|[;|]", normalized):
                universal_or_mandatory = re.search(
                    r"\b(?:votes|voting|cast(?:s|ing)? [^.!?;|]{0,160})\s+"
                    r"(?:in|at|during) (?:every|each|all) debates?\b"
                    r"|\b(?:must|shall|always|has to|is required to) "
                    r"(?:casts?\b[^.!?;|]{0,80}\bvote|votes?)\b"
                    r"|\b(?:voting|(?:claude|participant|director)(?:'s)? (?:advisory )?vote) "
                    r"(?:is )?(?:mandatory|required|compulsory|unconditional|automatic|guaranteed|invariable)\b"
                    r"|\bvotes? (?:unconditionally|automatically|without exception|in all cases)\b"
                    r"|\b(?:claude|participant|director)(?: seat)? "
                    r"(?:must not|cannot|may not|shall not|does not|(?:can )?(?:never|no longer)) abstains?\b"
                    r"|\b(?:claude|participant|director)(?: seat)? "
                    r"(?:votes?|casts? (?:its |an? )?(?:advisory )?vote) without abstaining\b"
                    r"|\babstention (?:is )?(?:(?:not|no longer) (?:permitted|allowed)|prohibited|forbidden) "
                    r"for (?:the )?(?:claude|participant|director)\b"
                    r"|\b(?:claude|participant|director)(?: seat)? "
                    r"(?:is not|is no longer) (?:permitted|allowed) to abstain\b"
                    r"|\bno abstention (?:is (?:permitted|allowed) )?"
                    r"for (?:the )?(?:claude|participant|director)\b"
                    r"|\b(?:every|each|all) debates?"
                    r"(?: (?:that |which )?(?:claude|participant|director) (?:joins|moderates|participates in))? "
                    r"(?:includes?|carries|carry|records?) "
                    r"(?:the )?(?:claude|participant|director)(?:'s)? (?:advisory )?vote\b"
                    r"|\b(?:claude|participant|director)(?: seat)? always "
                    r"(?:carries|holds|includes) (?:its |an? |the )?(?:advisory )?vote\b",
                    clause, re.I,
                )
                cast_claim = re.search(
                    r"\b(?:casts?|casting) (?=its |the |one |single )(?:its |the )?(?:one |single )?"
                    r"(?:existing )?(?:Claude )?(?:advisory |weighted )?vote\b"
                    r"|\b(?:is cast|vote cast)\b",
                    clause, re.I,
                )
                relevant = re.search(r"\b(?:claude|participant|director|advisory)\b", clause, re.I)
                optional = re.search(
                    r"\b(?:abstain\w*|abstention|if cast|when cast|may cast|only for an invited turn)\b",
                    clause, re.I,
                )
                if universal_or_mandatory or (cast_claim and relevant and not optional):
                    problems.append(clause.strip())
        return problems

    def test_all_documentation_surfaces_reject_known_unconditional_claims(self):
        """Scan known lexical forms; a pass cannot establish semantic absence.

        Cross-sentence references ("Claude participates in every debate. It
        never abstains.") and implicit obligations using unlisted synonyms
        ("Claude leaves no debate without a ballot on record.") remain outside
        this guard and require human review.
        """
        # Discover new documentation, including templates and both profile
        # sources and rendered sheets; do not freeze this to the known defects.
        paths = set(self.ROOT.glob("*.md"))
        for directory in ("docs", "prompts", ".agents", ".claude", ".github"):
            paths.update(
                path for path in (self.ROOT / directory).rglob("*")
                if path.suffix in {".md", ".yaml", ".yml", ".rst", ".txt"}
            )
        for path in sorted(paths):
            with self.subTest(path=path.relative_to(self.ROOT).as_posix()):
                self.assertEqual(self.unconditional_claims(path.read_text(encoding="utf-8")), [])

    def test_guard_rejects_historical_and_mandatory_variants(self):
        for claim in (
            "Claude votes in every debate it takes part in.",
            "Claude votes in every\ndebate it moderates.",
            "Participant is the Claude vote, cast at full weight in every debate Claude joins.",
            "Claude casts its one existing advisory vote at full weight.",
            "Claude's advisory vote is booked under Participant and is cast in full whether or not Claude moderates.",
            "cast the single existing Claude advisory vote during synthesis",
            "The single Participant vote cast later in synthesis.",
            "Claude must vote.", "Participant shall cast its vote.",
            "Claude always votes.", "Voting is mandatory.",
            "Claude votes unconditionally.", "Claude cannot abstain.",
            "Claude never abstains.",
            "Abstention is not permitted for Claude.",
            "Claude votes without exception.",
            "Every debate includes Claude's advisory vote.",
            "Claude's vote is automatic.",
            "The Participant seat always carries a vote.",
            "Claude does not abstain.",
            "Claude no longer abstains.",
            "Claude votes without abstaining.",
            "No abstention is allowed for Claude.",
            "Claude is not permitted to abstain.",
            "Abstention is no longer allowed for Claude.",
            "Claude can never abstain.",
            "Claude can no longer abstain.",
            "Abstention is forbidden for the Director.",
            "Each debate records Claude's advisory vote.",
            "Every debate Claude joins includes Claude's advisory vote.",
            "Each debate that Claude moderates records Claude's advisory vote.",
            "All debates carry the Participant vote.",
            "The Director's advisory vote is guaranteed.",
            "Claude's vote is invariable.",
            "Claude\u2019s vote is guaranteed.",
            "Claude's advisory vote is unconditional.",
            "Claude votes in all cases.",
            "The Participant seat always holds its advisory vote.",
            "The Director always includes a vote.",
        ):
            with self.subTest(claim=claim):
                # A separate general permission must not mask the bad claim.
                self.assertTrue(self.unconditional_claims(claim + "\n\nVoting permits explicit abstention."))

    def test_guard_preserves_entitlement_and_optional_exercise(self):
        for claim in (
            "Claude retains its right to vote in every debate it moderates.",
            "Moderation never removes Claude's vote.",
            "Claude casts its one advisory vote or records an explicit abstention.",
            "Voting is encouraged, not mandatory.",
            "Participant may cast its single advisory vote or abstain.",
            "A cast vote is recorded under Participant.",
            "Advisory weight is cast only for an invited turn.",
            "Claude's vote is not automatic.",
            "Claude's right to vote is unconditional.",
            "Each debate includes Claude's right to vote.",
            "Every debate Claude moderates includes Claude's right to vote.",
            "The Participant seat always carries a right to vote.",
            "Abstention is permitted for Claude.",
            "Claude is not required to vote.",
            "Abstention is not prohibited for Claude.",
        ):
            with self.subTest(claim=claim):
                self.assertEqual(self.unconditional_claims(claim), [])


class ModeratorVoteRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_claude_vote_and_abstention_survive_moderated_debate(self):
        # Exercise the real prompts, typed review admission, ledger, and tally.
        # Only provider responses and Slack delivery are offline fixtures.
        class FixtureDirector(OpenAIDirector):
            async def _structured(self, prompt, model, schema_name):
                request = json.loads(prompt)
                self.requests[schema_name] = request
                if schema_name == "director_plan":
                    payload = self.fixture_plan.model_dump(mode="json")
                elif schema_name == "review_decision":
                    is_claude = request["current_assignment"]["agent"] == "claude"
                    abstain = is_claude and self.claude_abstains
                    payload = {
                        "verdict": "accept", "summary": "Fixture evidence accepted.",
                        "debate_contribution": {
                            "position": "Abstain pending evidence." if abstain else "Support the evidence-backed option.",
                            "rationale": "The bounded fixture supports this position.",
                            "evidence": ["Offline fixture evidence"], "contradictions": [],
                            "assumptions": ["The fixture is representative"], "material_risks": [],
                            "unknowns": ["Live behavior is unverified"],
                            "conditions_to_change": ["Contrary evidence is verified"],
                            "confidence": 0.7, "abstain": abstain,
                            "vote_option": None if abstain else ("A" if is_claude else "B"),
                        },
                    }
                elif schema_name == "debate_counter_bias":
                    payload = {
                        "strongest_counterargument": "The fixture may be unrepresentative.",
                        "challenged_assumptions": ["The fixture is representative"],
                        "disconfirming_evidence": [], "alternatives_reconsidered": ["A", "B"],
                        "independence_assessment": "Independent positions precede this pass.",
                        "recommendation_changed": False, "confidence": 0.7,
                    }
                elif schema_name == "debate_synthesis":
                    is_claude = self.provider == ProviderName.CLAUDE
                    abstain = is_claude and self.claude_abstains
                    payload = {
                        "decision": "Record the advisory result.",
                        "rationale": "Preserve the bounded evidence and dissent.",
                        "next_action": "Return the result for review.", "confidence": 0.7,
                        "openai_abstains": abstain,
                        "openai_vote_option": None if abstain else ("A" if is_claude else "B"),
                    }
                else:
                    raise AssertionError(f"Unexpected provider call: {schema_name}")
                # Validate serialized responses, including the generic
                # DebateContribution vote-or-abstain contract inside reviews.
                return model.model_validate_json(json.dumps(payload))

        for moderator in (ProviderName.OPENAI, ProviderName.CLAUDE):
            for abstain in (False, True):
                with self.subTest(moderator=moderator, abstain=abstain), tempfile.TemporaryDirectory() as directory:
                    settings = replace(
                        Settings(), workload_policy_enabled=False, allow_paid_runs=True,
                        openai_api_key="test-openai", claude_routine_url="https://claude.invalid/fire",
                        claude_routine_token="test-claude", cursor_api_key="test-cursor",
                        grok_enabled=True, grok_api_key="test-grok", project_allowlist=("demo",),
                        slack_bot_token="test-slack",
                        repository_allowlist=("example/demo",), allowed_starting_refs=("main",),
                        max_agent_hops=4, sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                    )
                    FixtureDirector.provider = moderator
                    FixtureDirector.transport = (
                        ProviderTransport.CLAUDE_NATIVE_HOST if moderator == ProviderName.CLAUDE
                        else ProviderTransport.OPENAI_RESPONSES_API
                    )
                    director = FixtureDirector(settings)
                    director.requests = {}
                    director.claude_abstains = abstain
                    director.fixture_plan = debate_plan()
                    if moderator == ProviderName.CLAUDE:
                        # The executing Director votes or abstains at synthesis; specialists
                        # occupy separate seats, so Claude cannot vote twice.
                        for assignment in director.fixture_plan.assignments:
                            if assignment.agent == ProviderName.CLAUDE:
                                assignment.agent = ProviderName.GROK
                    store = TaskStore(settings.sqlite_path)
                    task = TaskRecord(
                        task_id="DIR-MODERATOR-VOTE", status=TaskStatus.QUEUED,
                        intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                        intake=SlackIntake(
                            source_event_id="evt-moderator-vote", channel_id="C-TEST",
                            thread_ts="fixture-thread", user_id="U-TEST",
                            text="DIRECTOR Project: demo Outcome: compare bounded options",
                        ),
                    )
                    store.create(task)
                    with patch("director_orchestrator.engine.build_director", return_value=director):
                        engine = OrchestrationEngine(settings, store, providers={
                            provider: CompletingAdapter(provider)
                            for provider in {item.agent for item in director.fixture_plan.assignments}
                        })
                    engine.notifier = RecordingNotifier()

                    await engine.run(task.task_id)

                    completed = store.get(task.task_id)
                    self.assertEqual(completed.status, TaskStatus.DONE, completed.error or completed.message)
                    self.assertIsNotNone(completed.debate_counter_bias)
                    self.assertTrue(any(
                        turn.phase == "moderator" and turn.provider == moderator
                        for turn in completed.collaboration_turns
                    ))
                    tally = completed.debate_tally
                    self.assertEqual(tally["option_weights"].get("A", 0.0), 0.0 if abstain else 2.0)
                    self.assertEqual("claude" in tally["abstaining"], abstain)
                    self.assertEqual(tally["configured_weight_total"], 8.5)
                    self.assertFalse(tally["authorizes_external_action"])
                    messages = "\n".join(post["message"] for post in engine.notifier.posts)
                    self.assertIn("casts its single advisory vote or records an explicit abstention", messages)
                    self.assertIn(
                        f"One {'Claude' if moderator == ProviderName.CLAUDE else 'OpenAI'} advisory vote at its configured weight "
                        "or an explicit abstention", messages,
                    )
                    if moderator == ProviderName.CLAUDE:
                        self.assertIn(
                            "Claude Participant abstained from the advisory vote." if abstain
                            else "Claude Participant cast its one advisory vote for A.", messages,
                        )
                    if moderator == ProviderName.OPENAI:
                        contributions = [
                            turn.verified_debate_contribution for turn in completed.collaboration_turns
                            if turn.provider == ProviderName.CLAUDE and turn.phase in {"position", "rebuttal"}
                        ]
                        self.assertEqual(len(contributions), 2)
                        for contribution in contributions:
                            self.assertEqual(contribution.abstain, abstain)
                            self.assertEqual(contribution.vote_option, None if abstain else "A")
                    self.assertIn("or records an explicit abstention", director.instructions)
                    self.assertIn(
                        "at synthesis unless you explicitly abstain",
                        director.requests["debate_counter_bias"]["instruction"],
                    )


class ProjectProviderRolesContractTests(unittest.TestCase):
    """Drift tests for the project-local provider roles contract template."""

    BOOTSTRAP = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "templates"
        / "project-bootstrap"
    )

    def contract(self) -> str:
        return (self.BOOTSTRAP / "provider-roles-contract.yaml").read_text(
            encoding="utf-8"
        )

    def test_contract_mirrors_the_charter_responsibility_areas(self):
        contract = self.contract()
        areas = team_operating_charter()["responsibility_areas"]

        self.assertEqual(len(areas), 8)
        for area in areas:
            with self.subTest(area=area["name"]):
                self.assertIn(f'- name: "{area["name"]}"', contract)
                self.assertIn(f'expectation: "{area["expectation"]}"', contract)

    def test_contract_mirrors_prospective_total_effort_controls(self):
        contract = self.contract()
        for field in (
            'default_profile: "engineering"',
            'engineering: [40, 25, 15, 10, 10, 0, 0, 0]',
            'mixed: [27, 33, 10, 10, 5, 5, 5, 5]',
            'public_research: [20, 25, 5, 5, 5, 25, 15, 0]',
            'enterprise: [32, 38, 5, 5, 5, 0, 5, 10]',
            'total_effort_status: "separate_telemetry_not_production_credit"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, contract)

        self.assertNotIn("historical_overall_effort_percent:", contract)
        self.assertNotIn("historical_overall_effort_status:", contract)

    def test_contract_encodes_the_cursor_boundary(self):
        contract = self.contract()
        cursor = team_operating_charter()["role_profiles"]["cursor"]

        self.assertEqual(len(cursor["eligible_responsibilities"]), 8)
        for area in cursor["eligible_responsibilities"]:
            with self.subTest(area=area):
                self.assertIn(f'      - "{area}"', contract)
        self.assertIn(
            f'capability_parity_target: {cursor["capability_parity_target"]}', contract
        )
        for peer in cursor["comparison_peers"]:
            self.assertIn(peer, contract)
        self.assertIn('may_handle_critical_work: true', contract)
        self.assertIn('"critical"', contract)
        for denial in (
            "a benchmark, score, ranking, or measured performance claim",
            "permission, access, or credential expansion",
            "sole acceptance authority",
            "authority to review or approve its own work",
            "merge, deployment, or publication authority",
            "safety authority",
        ):
            with self.subTest(denial=denial):
                self.assertIn(f'- "{denial}"', contract)
        for rule in cursor["non_substitution_rules"]:
            with self.subTest(rule=rule):
                self.assertIn(f'- "{rule}"', contract)

    def test_contract_encodes_the_antigravity_boundary(self):
        contract = self.contract()

        self.assertIn('lane: "subordinate_supplemental_specialist"', contract)
        for category in (
            "explicit boilerplate",
            "scaffolding",
            "low-level mechanical implementation",
            "alternate/abstract ideation",
            "the bounded sandbox execution those need",
        ):
            with self.subTest(category=category):
                self.assertIn(f'- "{category}"', contract)
        self.assertNotIn("sandboxed prototypes", contract)
        for permitted in (
            "repository_owned_fixed_strings",
            "closed_enum_values",
            "bounded_integers",
        ):
            with self.subTest(permitted=permitted):
                self.assertIn(f'- "{permitted}"', contract)
        for prose in (
            "model_authored_prose",
            "owner_authored_prose",
            "caller_authored_prose",
        ):
            with self.subTest(prose=prose):
                self.assertIn(f'- "{prose}"', contract)
        # Antigravity may hold an invited debate seat, but it can never create
        # one and the seat carries no authority.
        self.assertIn("seatable: true", contract)
        self.assertIn("may_initiate: false", contract)

    def test_contract_mirrors_the_runtime_antigravity_denied_authorities(self):
        contract = self.contract()

        # Exact equality with the runtime constant, not a paraphrase: a broad
        # phrase like "operational authority" does not preserve the typed
        # boundary the runtime actually sends.
        block = contract.split("denied_authorities:", 1)[1].split("withheld_context:", 1)[0]
        mirrored = tuple(
            line.strip()[3:-1]
            for line in block.splitlines()
            if line.strip().startswith('- "')
        )
        self.assertEqual(mirrored, DENIED_AUTHORITIES)
        self.assertEqual(len(mirrored), 11)

        tail = contract.split("withheld_context:", 1)[1].split("\n    debate:", 1)[0]
        withheld = tuple(
            line.strip()[3:-1]
            for line in tail.splitlines()
            if line.strip().startswith('- "')
        )
        self.assertEqual(withheld, WITHHELD_CONTEXT)

    def test_contract_requires_downstream_verification_of_antigravity_work(self):
        contract = self.contract()

        self.assertIn('artifact_status: "non_authoritative_evidence"', contract)
        self.assertIn("mandatory_downstream_verification:", contract)
        self.assertIn("required: true", contract)
        self.assertIn('assignee: "claude_cursor_or_grok"', contract)
        self.assertIn(
            "must feed a later Claude, Cursor, or Grok assignment that independently "
            "verifies and integrates or rejects it",
            contract,
        )
        for denied in (
            "exact-SHA review",
            "final acceptance",
            "deterministic merge",
            "defining research-contract or methodology truth",
        ):
            with self.subTest(denied=denied):
                self.assertIn(f'- "{denied}"', contract)

    def test_contract_encodes_the_perplexity_boundary(self):
        contract = self.contract()
        charter = team_operating_charter()["perplexity_contract"]

        self.assertIn(f'role: "{charter["role"]}"', contract)
        # Every canonical prohibition must appear verbatim. Omitting or
        # rewording any one of them fails here rather than silently narrowing
        # what the template appears to permit.
        self.assertEqual(len(charter["prohibited"]), 6)
        for prohibited in charter["prohibited"]:
            with self.subTest(prohibited=prohibited):
                self.assertIn(f'- "{prohibited}"', contract)
        self.assertIn('excluded_responsibility_areas:', contract)
        self.assertIn('      - "programming"', contract)
        for field in (
            "query",
            "result_url",
            "page_title",
            "publication_date",
            "retrieval_date",
            "supporting_excerpt_or_extracted_content",
            "source_type",
            "confidence",
            "contradictions",
            "unavailable_evidence",
        ):
            with self.subTest(field=field):
                self.assertIn(f'- "{field}"', contract)
        self.assertIn(f'review_requirement: "{charter["review_requirement"]}"', contract)

    def test_contract_keeps_the_three_acting_director_functions_distinct(self):
        contract = self.contract()
        current = contract.split("\n  claude:\n", 1)[1].split("\n  openai:\n", 1)[0]
        self.assertIn("Claude as acting Director keeps", current)
        self.assertIn("functions:", current)

        for function_id in ("moderator", "participant", "director"):
            with self.subTest(function_id=function_id):
                self.assertIn(f'- id: "{function_id}"', contract)
        self.assertIn("function_separation_rule:", contract)
        self.assertIn("never collapsed or renamed into duty buckets", contract)

        # The voting boundary is what the canonical identities carry: the
        # Participant holds the one authenticated position and may vote or abstain;
        # neither Moderator nor Director votes.
        moderator = contract.split('- id: "moderator"', 1)[1].split("- id:", 1)[0]
        participant = contract.split('- id: "participant"', 1)[1].split("- id:", 1)[0]
        director = contract.split('- id: "director"', 1)[1].split("\n    participation_record_rule", 1)[0]
        self.assertIn("votes: false", moderator)
        self.assertIn("votes: true", participant)
        self.assertIn("vote_count: 1", participant)
        self.assertIn("vote_weight: 2.0", participant)
        self.assertIn("votes: false", director)
        # Moderating relocates nothing about the vote: it stays one vote, booked
        # under Participant, and the Moderator function cannot strip it.
        self.assertIn("Moderating never removes, reduces, or defers", moderator)
        self.assertIn("no additional vote", participant)
        self.assertIn("bars a second Claude vote and never the first", participant)
        self.assertIn("does not create a vote", director)

        # Coordination, planning, and acceptance survive only as nested duties.
        for duty in ("plan", "decompose", "route", "coordinate", "synthesize", "acceptance_review"):
            with self.subTest(duty=duty):
                self.assertIn(f'- "{duty}"', director)
        self.assertIn("participation_record_rule:", contract)
        for boundary in (
            "producer_exclusion:",
            "material_tie_escalation:",
            "deterministic_gates:",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, contract)

    def test_contract_grants_no_authority_and_changes_no_runtime_surface(self):
        contract = self.contract()

        self.assertIn('authority_granted: "none"', contract)
        for unchanged in (
            "committee voting weights or the immutable denominator",
            "runtime permissions or allowlists",
            "provider credentials",
            "deployments or service state",
            "Slack behaviour",
            "deterministic policy in src/director_orchestrator/policy.py",
        ):
            with self.subTest(unchanged=unchanged):
                self.assertIn(f'- "{unchanged}"', contract)

    def test_intake_names_the_canonical_acting_director_functions(self):
        intake = (self.BOOTSTRAP / "intake.md").read_text(encoding="utf-8")

        for name in ("Moderator", "Participant", "Director"):
            with self.subTest(name=name):
                self.assertIn(name, intake)
        self.assertIn("Only Participant votes", intake)
        self.assertIn("Moderator and Director add no second vote", intake)
        self.assertIn("including every\ndebate it moderates", intake)
        # The duty-bucket phrasing must not reappear as a function partition.
        self.assertNotIn(
            "coordination, planning, and acceptance-review functions distinct", intake
        )
        self.assertIn(
            "acceptance review are Director duties, not separate functions", intake
        )

    def test_readme_requires_the_contract_whenever_the_profile_is_used(self):
        readme = (self.BOOTSTRAP / "README.md").read_text(encoding="utf-8")
        profile = (self.BOOTSTRAP / "project-profile.yaml").read_text(encoding="utf-8")

        # The profile sets the reference unconditionally, so the kit must not
        # let a single-provider project skip the companion file.
        self.assertIn('provider_roles_contract: "provider-roles-contract.yaml"', profile)
        self.assertNotIn("whenever the project staffs more than one provider", readme)
        self.assertIn("whenever you", readme)
        self.assertIn("including single-provider", readme)
        self.assertIn("leaves that reference dangling", readme)

    def test_bootstrap_surfaces_reference_the_roles_contract(self):
        for surface in (
            "project-profile.yaml",
            "workstream-plan.md",
            "intake.md",
            "project-state-report.md",
        ):
            with self.subTest(surface=surface):
                self.assertIn(
                    "provider-roles-contract.yaml",
                    (self.BOOTSTRAP / surface).read_text(encoding="utf-8"),
                )


class ProjectRolloutPacketContractTests(unittest.TestCase):
    """Drift tests for project rollout and concurrent Slack isolation."""

    ROOT = Path(__file__).resolve().parents[1]
    BOOTSTRAP = ROOT / "docs" / "templates" / "project-bootstrap"

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_every_presented_project_gets_a_rollout_packet(self):
        packet = self.BOOTSTRAP / "rollout-packet.md"
        self.assertTrue(packet.is_file())
        for surface in (
            self.BOOTSTRAP / "README.md",
            self.ROOT / "docs" / "project-bootstrap-protocol.md",
            self.ROOT / "prompts" / "director-system.md",
        ):
            with self.subTest(surface=surface):
                content = self.read(surface)
                self.assertIn("rollout-packet.md", content)
                self.assertIn("every project presented", content.lower())

        readme = self.read(self.BOOTSTRAP / "README.md")
        self.assertIn("rollout packet is mandatory", readme.lower())
        self.assertNotIn("may need only the intake", readme.lower())

    def test_profile_requires_concurrent_deployment_isolation(self):
        profile = self.read(self.BOOTSTRAP / "project-profile.yaml")
        for field in (
            'rollout_packet: "rollout-packet.md"',
            "dedicated_app_per_active_deployment: true",
            "dedicated_channel_per_active_deployment: true",
            "distinct_profile_and_state_namespace: true",
            'concurrent_deployment_collision_check: "pending"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, profile)

    def test_packet_blocks_slack_activation_without_borrowing(self):
        packet = self.read(self.BOOTSTRAP / "rollout-packet.md")
        normalized = " ".join(packet.split()).lower()
        for requirement in (
            "distinct slack app",
            "distinct project channel",
            "distinct deployment profile/state namespace",
            "blocks slack gateway activation",
            "assignment-specific `retry_limit`",
            "recovery requires temporary failure",
            "never retry an ambiguous slack side effect",
            "do not borrow another deployment's slack app",
            "activation evidence",
            "rollback",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, normalized)

    def test_packet_records_references_not_token_values(self):
        packet = self.read(self.BOOTSTRAP / "rollout-packet.md")
        normalized = " ".join(packet.split())
        self.assertIn("secret reference", packet)
        self.assertIn("Never copy token values", packet)
        self.assertIn("Slack owner-user allowlist reference", packet)
        self.assertIn("non-reversible verification digest", packet)
        self.assertIn("exact IDs stay in the ignored deployment profile", normalized)
        self.assertNotIn("exact owner user ID(s)", packet)
        self.assertNotIn("xapp-", packet)
        self.assertNotIn("xoxb-", packet)

    def test_slack_retry_policy_is_synchronized(self):
        profile = self.read(self.BOOTSTRAP / "project-profile.yaml")
        for field in (
            "retry_limit: 3",
            'additional_attempt_requires: "second_temporary_failure_third_new_information_or_different_transport"',
            "resource_budget_required: true",
            'ambiguous_slack_side_effect: "do_not_retry_reconcile_first"',
            'exhausted_route: "same_project_durable_transport_never_borrow_another_deployment_surface"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, profile)

        for surface in (
            self.BOOTSTRAP / "rollout-packet.md",
            self.ROOT / "docs" / "project-bootstrap-protocol.md",
            self.ROOT / "prompts" / "director-system.md",
        ):
            normalized = " ".join(self.read(surface).split()).lower()
            for phrase in (
                "assignment-specific `retry_limit`",
                "recovery requires temporary failure",
                "third attempt requires materially new information",
                "different transport",
                "never retry an ambiguous slack side effect",
                "same project's prior durable",
                "another deployment",
            ):
                with self.subTest(surface=surface, phrase=phrase):
                    self.assertIn(phrase, normalized)

    def test_packet_records_slack_identities_and_negative_activation_checks(self):
        profile = self.read(self.BOOTSTRAP / "project-profile.yaml")
        packet = self.read(self.BOOTSTRAP / "rollout-packet.md")
        for field in (
            "owner_user_allowlist_reference:",
            "runtime_channel_allowlist:",
            "bot_user_id:",
            "manifest_sha256:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, profile)
        self.assertNotIn("<exact Slack owner user ID", profile)
        for evidence in (
            "Slack owner-user allowlist",
            "Runtime channel allowlist",
            "Slack bot user ID",
            "Slack app manifest SHA-256",
            "Off-channel event rejection",
            "Non-owner event rejection",
            "Replayed event handling",
            "proof no other active deployment consumed or persisted the event",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, packet)

    def test_rollout_status_is_synchronized_across_bootstrap_surfaces(self):
        for name in ("intake.md", "workstream-plan.md", "project-state-report.md"):
            with self.subTest(name=name):
                content = self.read(self.BOOTSTRAP / name)
                self.assertIn("rollout", content.lower())
                self.assertIn("Slack", content)

    def test_firecrawl_routing_fails_closed_without_authenticated_zdr(self):
        protocol = self.read(self.ROOT / "docs" / "project-bootstrap-protocol.md")
        normalized = " ".join(protocol.split())
        for requirement in (
            "zeroDataRetention: true",
            "lockdown: true",
            "cache-only ZDR route",
            "first-party public retrieval route outside Firecrawl",
            "Never send private, repository, account, credential, or project material",
            "Do not retry a rejected ZDR request unchanged",
            "authenticated ZDR is proven",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, normalized)


if __name__ == "__main__":
    unittest.main()

# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:02 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 21:35 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 22:05 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 22:30 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 22:10 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 22:20 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 22:28 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-25 | 01:13 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-25 | 01:27 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-25 | 01:31 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:58 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:37 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:38 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | END]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 10:40 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:11 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:35 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:45 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 21:50 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 21:59 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:21 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | END]]
