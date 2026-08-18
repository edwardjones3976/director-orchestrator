from __future__ import annotations

import json
import unittest
from dataclasses import replace

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
                supplemental_scope_token="a" * 64,
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
        self.assertEqual(director.captured["current_assignment"]["sequence"], 1)
        self.assertNotIn(
            "supplemental_scope_token", director.captured["current_assignment"]
        )
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


if __name__ == "__main__":
    unittest.main()

