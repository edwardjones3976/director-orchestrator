# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.antigravity import antigravity_wire_input
from director_orchestrator.config import Settings
from director_orchestrator.models import (
    AntigravityDirective,
    AntigravityFixtureLane,
    Assignment,
    AssignmentKind,
    DirectorPlan,
    DiscussionPhase,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.policy import (
    ANTIGRAVITY_CONSUMER_ACCEPTANCE,
    ANTIGRAVITY_CONSUMER_TASK,
    antigravity_assignment_errors,
    deterministic_approval_reasons,
    enforce_plan,
    execution_policy_errors,
    preview_plan,
)
from director_orchestrator.store import TaskStore


class PolicyTests(unittest.TestCase):
    def test_openai_runtime_cannot_be_an_assignment_target(self):
        settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        plan = DirectorPlan(
            project="demo",
            normalized_outcome="Plan then accept",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=1,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.OPENAI,
                    task="Duplicate the Director planning phase",
                    acceptance=["Plan exists"],
                    repository="example/demo",
                )
            ],
            final_acceptance=["Accepted"],
        )

        self.assertIn(
            "assignment 1: OpenAI is the Director runtime and cannot be an assignment target",
            execution_policy_errors(plan, settings),
        )

    def setUp(self):
        self.settings = replace(Settings(), max_agent_hops=4, max_revisions=1, max_task_budget_usd=1.0)

    def test_merge_and_deploy_require_approval(self):
        reasons = deterministic_approval_reasons("Deploy to production and merge directly to main")
        self.assertIn("production deployment or public publishing", reasons)
        self.assertIn("merge or protected-branch change", reasons)

    def test_dry_run_routes_research_and_clamps_limits(self):
        plan = preview_plan("Project: RootWitness\nResearch current retention requirements with sources", self.settings)
        self.assertEqual(plan.project, "RootWitness")
        self.assertEqual(plan.assignments[0].agent.value, "perplexity")
        self.assertIsNone(plan.assignments[0].public_research_query)
        self.assertLessEqual(plan.max_hops, 4)
        self.assertLessEqual(plan.max_revisions, 1)

    def test_dry_run_research_plan_does_not_copy_intake_into_the_search_query(self):
        text = (
            "Project: RootWitness\n"
            "Research current sources for github.com/acme/private-repo and attached patch"
        )
        plan = preview_plan(text, self.settings)
        self.assertEqual(plan.assignments[0].agent.value, "perplexity")
        self.assertEqual(plan.assignments[0].task, text.strip())
        self.assertIsNone(plan.assignments[0].public_research_query)

    def test_dry_run_uses_claude_as_default_workhorse(self):
        plan = preview_plan("Project: demo\nImplement and test a bounded API change", self.settings)
        self.assertEqual(plan.assignments[0].agent.value, "claude")

    def test_dry_run_can_request_cursor_second_pass_explicitly(self):
        plan = preview_plan("Project: demo\nRun an independent coding pass in Cursor", self.settings)
        self.assertEqual(plan.assignments[0].agent.value, "cursor")

    def test_dry_run_routes_antigravity_only_for_the_subordinate_lane(self):
        for text, kind in (
            ("Project: demo\nGenerate boilerplate for the adapter module", AssignmentKind.BOILERPLATE),
            ("Project: demo\nScaffolding for a new bounded package", AssignmentKind.SCAFFOLDING),
            ("Project: demo\nDo the low-level mechanical rename pass", AssignmentKind.LOW_LEVEL_IMPLEMENTATION),
            ("Project: demo\nOffer alternate ideas for the retry shape", AssignmentKind.ABSTRACT_IDEATION),
        ):
            with self.subTest(text=text):
                plan = preview_plan(text, self.settings)
                self.assertEqual(plan.assignments[0].agent, ProviderName.GEMINI)
                self.assertEqual(plan.assignments[0].kind, kind)
                self.assertEqual(plan.assignments[1].agent, ProviderName.CLAUDE)
                self.assertEqual(plan.assignments[1].task, ANTIGRAVITY_CONSUMER_TASK)
                self.assertEqual(
                    plan.assignments[1].acceptance, ANTIGRAVITY_CONSUMER_ACCEPTANCE
                )
                self.assertEqual(plan.assignments[1].depends_on, [1])

    def test_dry_run_keeps_generic_keyword_work_away_from_antigravity(self):
        for text in (
            "Project: demo\nBuild a prototype of the new screen",
            "Project: demo\nCheck the browser and visual rendering",
            "Project: demo\nDefine the core methodology and provenance rules",
            "Project: demo\nEvaluate temporal eligibility and acceptance evidence",
        ):
            with self.subTest(text=text):
                plan = preview_plan(text, self.settings)
                self.assertEqual(plan.assignments[0].agent, ProviderName.CLAUDE)
                self.assertEqual(len(plan.assignments), 1)

    def antigravity_work(self, **overrides) -> Assignment:
        fields = {
            "sequence": 1,
            "agent": ProviderName.GEMINI,
            "kind": AssignmentKind.BOILERPLATE,
            "task": "Generate boilerplate fixtures for the adapter module",
            "acceptance": ["Report the generated fixtures"],
            "antigravity_directive": AntigravityDirective(
                lane=AntigravityFixtureLane.UNIT_TEST_FIXTURE_SET
            ),
        }
        fields.update(overrides)
        return Assignment(**fields)

    def test_antigravity_lane_admits_only_typed_subordinate_work(self):
        base = self.antigravity_work()
        self.assertEqual(antigravity_assignment_errors(base), [])

        for overrides, expected in (
            (
                {"kind": AssignmentKind.INDEPENDENT_REVIEW},
                "requires kind boilerplate, scaffolding, low_level_implementation, "
                "or abstract_ideation",
            ),
            (
                {"task": "Build the new feature end to end"},
                "requires explicit subordinate boilerplate, scaffolding, low-level, "
                "mechanical, or alternate-ideation scope",
            ),
            (
                {"allow_draft_pr": True, "requires_approval": True},
                "cannot create a draft pull request",
            ),
            ({"repository": "example/demo"}, "cannot receive repository context"),
            ({"starting_ref": "release-2026"}, "cannot select a starting ref"),
            (
                {"public_research_query": "public retry backoff practice"},
                "cannot receive a public research query",
            ),
            (
                {"antigravity_directive": None},
                "requires a typed antigravity_directive selecting a fixture lane, "
                "because no assignment prose is transmitted",
            ),
            (
                {
                    "antigravity_directive": AntigravityDirective(
                        lane=AntigravityFixtureLane.NAMING_ALTERNATIVES
                    )
                },
                "fixture lane does not match the typed capability",
            ),
        ):
            with self.subTest(overrides=sorted(overrides)):
                self.assertIn(
                    expected,
                    antigravity_assignment_errors(base.model_copy(update=overrides)),
                )

    def test_operational_prose_is_no_longer_classified_because_none_is_sent(self):
        """The paraphrase matrix is now moot, and its absence is the point.

        A denylist could never enumerate "land the change on the default branch",
        "give it the thumbs up", or a confidential sentence with no keyword at all.
        The lane is admissible with any of them in its task, because the task is not
        transmitted -- only the typed lane is. Exhaustive wire-composition proof
        lives in tests/test_antigravity_contract.py.
        """
        for prose in (
            "Generate boilerplate fixtures, then land the change on the default branch",
            "Generate boilerplate fixtures, then give it the thumbs up",
            "Generate boilerplate fixtures. Blue Cedar acquisition valuation is 42 "
            "million dollars",
        ):
            with self.subTest(prose=prose[:48]):
                assignment = self.antigravity_work(task=prose)
                self.assertEqual(antigravity_assignment_errors(assignment), [])
                wire = antigravity_wire_input(assignment)
                self.assertNotIn("Blue Cedar", wire)
                self.assertNotIn("thumbs up", wire)
                self.assertNotIn("default branch", wire)

    def antigravity_plan(self, **consumer_overrides) -> DirectorPlan:
        consumer = {
            "sequence": 2,
            "agent": ProviderName.CLAUDE,
            "kind": AssignmentKind.IMPLEMENTATION,
            "task": ANTIGRAVITY_CONSUMER_TASK,
            "acceptance": list(ANTIGRAVITY_CONSUMER_ACCEPTANCE),
            "depends_on": [1],
        }
        consumer.update(consumer_overrides)
        return DirectorPlan(
            project="demo",
            normalized_outcome="Bounded subordinate scaffolding",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=2,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.GEMINI,
                    kind=AssignmentKind.SCAFFOLDING,
                    task="Produce scaffolding for the bounded adapter module",
                    acceptance=["Report the scaffolding evidence"],
                    antigravity_directive=AntigravityDirective(
                        lane=AntigravityFixtureLane.MODULE_SKELETON
                    ),
                ),
                Assignment(**consumer),
            ],
            final_acceptance=["Verified work only"],
        )

    def test_antigravity_output_requires_a_primary_integration_assignment(self):
        settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        self.assertEqual(execution_policy_errors(self.antigravity_plan(), settings), [])

        orphaned = self.antigravity_plan()
        orphaned.assignments = orphaned.assignments[:1]
        self.assertIn(
            "assignment 1: Antigravity output requires a later Claude or Cursor "
            "integration assignment with a direct dependency",
            execution_policy_errors(orphaned, settings),
        )

        unverified = self.antigravity_plan(task="Use the Antigravity artifact as-is")
        self.assertIn(
            "assignment 1: Antigravity output requires a later Claude or Cursor "
            "implementation assignment that explicitly independently verifies and "
            "integrates or rejects the artifact",
            execution_policy_errors(unverified, settings),
        )

    def test_enforce_plan_binds_the_integration_wording_deterministically(self):
        settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        drifted = self.antigravity_plan(
            task="Ship whatever Antigravity produced",
            acceptance=["Antigravity says it works"],
            kind=AssignmentKind.GENERAL,
        )
        guarded = enforce_plan(drifted, "bounded scaffolding", settings)

        self.assertEqual(guarded.assignments[1].task, ANTIGRAVITY_CONSUMER_TASK)
        self.assertEqual(
            guarded.assignments[1].acceptance, ANTIGRAVITY_CONSUMER_ACCEPTANCE
        )
        self.assertEqual(guarded.assignments[1].kind, AssignmentKind.IMPLEMENTATION)
        self.assertEqual(execution_policy_errors(guarded, settings), [])

    def test_antigravity_cannot_seat_itself_in_a_debate(self):
        """Antigravity debates only inside a persisted, bounded Director plan.

        A lone discussion assignment is not an invitation: without a debate plan
        there is no question, no options, and no committee, so the plan is refused
        rather than producing a one-sided advisory turn.
        """
        settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        plan = self.antigravity_plan()
        plan.assignments[0] = plan.assignments[0].model_copy(
            update={
                "kind": AssignmentKind.GENERAL,
                "task": "Independently assess option A and option B",
                "discussion_phase": DiscussionPhase.POSITION,
                "antigravity_directive": None,
            }
        )
        plan.assignments[1] = plan.assignments[1].model_copy(
            update={"depends_on": []}
        )
        errors = execution_policy_errors(plan, settings)
        self.assertIn("discussion assignments require a bounded debate plan", errors)

    def test_execution_policy_pins_the_primary_team(self):
        settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
            primary_team=("openai", "claude", "gemini"),
        )
        self.assertIn(
            "primary team must be exactly openai, claude, cursor",
            execution_policy_errors(self.antigravity_plan(), settings),
        )

    def test_primary_team_is_openai_claude_cursor(self):
        self.assertEqual(self.settings.primary_team, ("openai", "claude", "cursor"))
        strategy = self.settings.provider_strategy()
        self.assertTrue(all(strategy[name]["team"] == "primary" for name in self.settings.primary_team))
        self.assertEqual(strategy["gemini"]["team"], "supplemental")
        self.assertEqual(strategy["perplexity"]["team"], "supplemental")
        self.assertEqual(strategy["cursor"]["capability_parity_target"], 0.9)
        self.assertEqual(strategy["cursor"]["eligible_complexity"], ["low", "medium", "high", "critical"])
        self.assertEqual(strategy["cursor"]["priority"], "primary_full_spectrum_repository_partner")
        self.assertIn("technical leadership and coordination", strategy["cursor"]["strengths"])

    def test_perplexity_readiness_requires_the_complete_research_only_profile(self):
        key_only = replace(self.settings, perplexity_api_key="test-perplexity")
        ready = replace(key_only, perplexity_enabled=True)
        unsafe = replace(ready, perplexity_allow_review_verdict=True)

        self.assertFalse(key_only.readiness()["perplexity"])
        self.assertTrue(ready.readiness()["perplexity"])
        self.assertFalse(unsafe.readiness()["perplexity"])
        self.assertFalse(
            unsafe.perplexity_research_readiness()["review_verdict_disabled"]
        )

    def test_perplexity_assignments_cannot_receive_repository_or_non_research_authority(self):
        settings = replace(
            self.settings,
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        unsafe = DirectorPlan(
            project="demo",
            normalized_outcome="Ask Perplexity to write and approve a repository change",
            risk_level=RiskLevel.LOW,
            approval_required=True,
            max_hops=1,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.PERPLEXITY,
                    task="Write code in the repository, issue an approval verdict, and cast a vote",
                    acceptance=["Create a draft PR and post the result to Slack"],
                    repository="example/demo",
                    allow_draft_pr=True,
                    requires_approval=True,
                )
            ],
            final_acceptance=["Perplexity approved its own change"],
        )

        errors = execution_policy_errors(unsafe, settings)

        self.assertIn("assignment 1: Perplexity research cannot receive repository context", errors)
        self.assertIn("assignment 1: Perplexity research cannot create a draft pull request", errors)
        self.assertIn("assignment 1: Perplexity research cannot receive code or repository write authority", errors)
        self.assertIn("assignment 1: Perplexity research cannot receive pull-request or merge action authority", errors)
        self.assertIn("assignment 1: Perplexity research cannot receive approval or review verdict authority", errors)
        self.assertIn("assignment 1: Perplexity research cannot receive committee vote authority", errors)
        self.assertIn("assignment 1: Perplexity research cannot receive external message authority", errors)

    def test_perplexity_methodological_research_remains_allowed(self):
        settings = replace(
            self.settings,
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        safe = DirectorPlan(
            project="demo",
            normalized_outcome="Research calibration methodology",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=1,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.PERPLEXITY,
                    task="Find authoritative sources about calibration methodology",
                    acceptance=["Report URLs, provenance, contradictions, and unknowns"],
                public_research_query="authoritative calibration methodology sources",
                requires_approval=True,
                )
            ],
            final_acceptance=["A primary-team member evaluates the evidence"],
        )

        self.assertEqual(execution_policy_errors(safe, settings), [])

    def test_execution_targets_and_dependencies_are_strictly_allowlisted(self):
        settings = replace(
            self.settings,
            project_allowlist=("project uppercut",),
            repository_allowlist=("edwardjones3976/project-uppercut",),
            allowed_starting_refs=("main", "release/first-bell"),
        )
        plan = DirectorPlan(
            project="Project Uppercut",
            normalized_outcome="Implement and independently review a bounded change",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=2,
            max_revisions=1,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CLAUDE,
                    task="Implement",
                    acceptance=["Tests pass"],
                    repository="https://github.com/edwardjones3976/Project-Uppercut.git",
                    starting_ref="main",
                ),
                Assignment(
                    sequence=2,
                    agent=ProviderName.CURSOR,
                    task="Review",
                    acceptance=["Evidence reviewed"],
                    repository="edwardjones3976/Project-Uppercut",
                    starting_ref="release/first-bell",
                    depends_on=[1],
                ),
            ],
            final_acceptance=["Both assignments accepted"],
        )

        self.assertEqual(execution_policy_errors(plan, settings), [])

        unsafe = plan.model_copy(
            update={
                "project": "Unlisted",
                "assignments": [
                    plan.assignments[0].model_copy(
                        update={"repository": "other/private-repo", "starting_ref": "develop"}
                    ),
                    plan.assignments[1].model_copy(update={"depends_on": [3]}),
                ],
            }
        )
        errors = execution_policy_errors(unsafe, settings)
        self.assertTrue(any("project is not allowlisted" in item for item in errors))
        self.assertTrue(any("repository is not allowlisted" in item for item in errors))
        self.assertTrue(any("starting ref is not allowlisted" in item for item in errors))
        self.assertTrue(any("dependency 3 does not exist" in item for item in errors))

    def test_cursor_cannot_fall_back_to_an_implicit_repository(self):
        settings = replace(self.settings, project_allowlist=("demo",))
        plan = preview_plan("Project: demo\nRun an independent coding pass in Cursor", settings)

        self.assertIn(
            "assignment 1: Cursor requires an explicit repository",
            execution_policy_errors(plan, settings),
        )

    def test_cursor_working_branch_starting_ref_is_rejected_unless_allowlisted(self):
        settings = replace(
            self.settings,
            project_allowlist=("edge-oracle",),
            repository_allowlist=("edwardjones3976/edge_engine",),
            allowed_starting_refs=("main",),
        )
        plan = DirectorPlan(
            project="edge-oracle",
            normalized_outcome="Continue research from the prior Cursor branch",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=1,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CURSOR,
                    task="Continue research",
                    acceptance=["Evidence exists"],
                    repository="edwardjones3976/edge_engine",
                    starting_ref="cursor/director-task-1-f90c",
                )
            ],
            final_acceptance=["Accepted"],
        )

        errors = execution_policy_errors(plan, settings)
        self.assertTrue(
            any("Cursor working branch that is deleted after merge" in item for item in errors)
        )

        allowlisted = replace(
            settings,
            allowed_starting_refs=("main", "cursor/director-task-1-f90c"),
        )
        self.assertEqual(execution_policy_errors(plan, allowlisted), [])

    def test_draft_pr_capability_requires_assignment_approval(self):
        settings = replace(
            self.settings,
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Create a draft PR",
            acceptance=["Draft PR exists"],
            repository="example/demo",
            allow_draft_pr=True,
        )
        plan = DirectorPlan(
            project="demo",
            normalized_outcome="Create a draft PR",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=1,
            max_revisions=0,
            assignments=[assignment],
            final_acceptance=["Draft only"],
        )

        self.assertIn(
            "assignment 1: draft PR creation requires assignment approval",
            execution_policy_errors(plan, settings),
        )
        approved = plan.model_copy(
            update={
                "max_hops": 2,
                "assignments": [
                    assignment.model_copy(update={"requires_approval": True}),
                    Assignment(
                        sequence=2,
                        agent=ProviderName.CLAUDE,
                        task="Independently review the exact candidate produced by assignment 1",
                        acceptance=["Return an exact-candidate verdict"],
                        repository="example/demo",
                        depends_on=[1],
                    ),
                ],
            }
        )
        self.assertEqual(execution_policy_errors(approved, settings), [])


class StoreTests(unittest.TestCase):
    def test_source_event_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            intake = SlackIntake(
                source_event_id="evt-1",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: test",
            )
            first = TaskRecord(task_id="DIR-ONE", status=TaskStatus.QUEUED, intake=intake)
            second = TaskRecord(task_id="DIR-TWO", status=TaskStatus.QUEUED, intake=intake)
            stored_one, created_one = store.create(first)
            stored_two, created_two = store.create(second)
            self.assertTrue(created_one)
            self.assertFalse(created_two)
            self.assertEqual(stored_one.task_id, stored_two.task_id)

    def test_recoverable_tasks_exclude_owner_waits_and_terminal_states(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            for index, status in enumerate(
                (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.OWNER_DECISION_REQUIRED, TaskStatus.DONE)
            ):
                intake = SlackIntake(
                    source_event_id=f"evt-{index}",
                    channel_id="C123",
                    thread_ts=f"123.{index}",
                    user_id="U123",
                    text="DIRECTOR Project: demo Outcome: test",
                )
                store.create(TaskRecord(task_id=f"DIR-{index}", status=status, intake=intake))
            self.assertEqual(
                {record.task_id for record in store.list_recoverable()},
                {"DIR-0", "DIR-1"},
            )


if __name__ == "__main__":
    unittest.main()
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
