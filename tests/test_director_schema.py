from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.director import OpenAIDirector, _strict_schema
from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
    DirectorPlan,
    ProviderName,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.team_charter import (
    AdvisoryVote,
    TEAM_CHARTER_MAX_BYTES,
    canonical_team_charter_json,
    canonical_team_charter_manifest_json,
    tally_advisory_votes,
    team_operating_charter,
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
        self.assertEqual(director.captured["plan_context"]["estimated_total_cost_usd"], 0)
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
            {"antigravity": 1.0, "cursor": 1.5, "openai": 2.0, "claude": 2.5},
        )
        self.assertEqual(charter["committee"]["voting"]["mode"], "advisory")
        self.assertIn("medium", charter["committee"]["debate"]["recommended_for_complexity"])
        self.assertIn("committee votes", charter["perplexity_contract"]["prohibited"])
        browser = charter["browser_communication_policy"]
        self.assertEqual(browser["primary"], "chrome")
        self.assertEqual(browser["fallback"], "microsoft_edge")
        self.assertEqual(browser["eligible_providers"], ["claude", "cursor", "gemini"])
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
        self.assertEqual(cursor["capability_parity_target"], 0.9)
        self.assertEqual(cursor["comparison_peers"], ["claude", "openai_codex"])
        self.assertEqual(cursor["eligible_complexity"], ["low", "medium", "high", "critical"])
        self.assertEqual(
            cursor["eligible_responsibilities"],
            [area["name"] for area in charter["responsibility_areas"]],
        )
        self.assertTrue(any("own work" in rule for rule in cursor["non_substitution_rules"]))
        self.assertTrue(
            any(
                "deterministic policy" in rule.lower()
                for rule in charter["committee"]["voting"]["rules"]
            )
        )

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

    def test_machine_and_markdown_charter_manifest_are_exact(self):
        document = (
            Path(__file__).resolve().parents[1] / "docs" / "team-operating-charter.md"
        ).read_text(encoding="utf-8")
        marker = document.split("<!-- TEAM_CHARTER_MANIFEST_BEGIN -->", 1)[1]
        fenced = marker.split("<!-- TEAM_CHARTER_MANIFEST_END -->", 1)[0]
        documented = fenced.split("```json", 1)[1].split("```", 1)[0].strip()

        self.assertEqual(documented, canonical_team_charter_manifest_json())

    def test_provider_cannot_mutate_canonical_roles_or_supply_weights(self):
        mutable = team_operating_charter()
        mutable["committee"]["members"]["cursor"]["voting_power"] = 99
        self.assertEqual(
            team_operating_charter()["committee"]["members"]["cursor"]["voting_power"],
            1.5,
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
                    member="antigravity",
                    disposition="unavailable",
                    rationale="Three safe attempts received no response.",
                ),
            ]
        )

        self.assertEqual(tally.option_weights, {"A": 2.5})
        self.assertEqual(tally.cast_weight_total, 2.5)
        self.assertEqual(tally.configured_weight_total, 7.0)
        self.assertFalse(tally.authorizes_external_action)

    def test_ties_route_to_openai_or_owner_without_authorizing_action(self):
        votes = [
            AdvisoryVote(
                member="claude",
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
        self.assertEqual(ordinary.resolution, "openai_director_judgment")
        self.assertEqual(material.resolution, "owner_decision_required")
        self.assertFalse(ordinary.authorizes_external_action)
        self.assertFalse(material.authorizes_external_action)


if __name__ == "__main__":
    unittest.main()

