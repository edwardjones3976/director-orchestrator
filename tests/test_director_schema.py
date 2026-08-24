# [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 21:35 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:02 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.director import OpenAIDirector, _strict_schema
from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
    DebatePlan,
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
    canonical_director_operating_directive_json,
    canonical_team_charter_json,
    canonical_team_charter_manifest_json,
    director_operating_directive_sha256,
    tally_advisory_votes,
    team_charter_manifest,
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

        self.assertAlmostEqual(
            director.captured["plan_context"]["estimated_total_cost_usd"], 1.0
        )

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
        efficiency = charter["efficiency_policy"]
        self.assertTrue(efficiency["concise_evidence_first"])
        self.assertTrue(efficiency["repeat_requires_new_information"])
        self.assertTrue(any("information gain" in rule for rule in efficiency["rules"]))
        self.assertTrue(any("safety evidence" in rule for rule in efficiency["rules"]))
        self.assertTrue(any("dependencies" in rule for rule in efficiency["rules"]))
        self.assertEqual(efficiency["minimal_fix_attempt_limit"], 1)
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

        self.assertIn("must never emit OpenAI as an assignment target", charter)
        self.assertIn("Never emit OpenAI as a specialist assignment target", prompt)
        self.assertIn("never emits OpenAI as a", readme)
        self.assertIn("different-provider exact-candidate review", charter)
        self.assertIn("different-provider exact-candidate review", prompt)
        self.assertIn("different-provider exact-candidate review", readme)
        self.assertIn("different-provider exact-candidate review", report)

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
        self.assertIn("attempt_limit: 1", profile)
        self.assertIn("changed_candidate_invalidates_evidence: true", profile)

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
            1,
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

        self.assertIn('lane: "sandbox_prototype_behavioral_verification_only"', contract)
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
        for prohibited in (
            "secrets, credentials, or tokens",
            "private repository context",
            "Slack posting or other external posts",
            "merge",
            "deployment",
            "operational authority",
        ):
            with self.subTest(prohibited=prohibited):
                self.assertIn(f'- "{prohibited}"', contract)
        self.assertIn("seatable: false", contract)

    def test_contract_encodes_the_perplexity_boundary(self):
        contract = self.contract()
        charter = team_operating_charter()["perplexity_contract"]

        self.assertIn(f'role: "{charter["role"]}"', contract)
        for excluded in (
            "programming",
            "committee voting",
            "repository review",
            "repository writes",
            "approval, acceptance, or merge verdicts",
            "operational decisions",
        ):
            with self.subTest(excluded=excluded):
                self.assertIn(f'- "{excluded}"', contract)
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

    def test_contract_keeps_the_three_openai_functions_distinct(self):
        contract = self.contract()

        for function_id in (
            "director_coordination_and_routing",
            "planning_and_decomposition",
            "evidence_synthesis_and_independent_acceptance_review",
        ):
            with self.subTest(function_id=function_id):
                self.assertIn(f'- id: "{function_id}"', contract)
        self.assertIn("function_separation_rule:", contract)
        self.assertIn("never collapsed into a single role", contract)
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


if __name__ == "__main__":
    unittest.main()

# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:02 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 21:35 UTC | END]]
