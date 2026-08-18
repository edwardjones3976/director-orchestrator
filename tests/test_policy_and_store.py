from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.models import (
    Assignment,
    AssignmentKind,
    DirectorPlan,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.policy import (
    antigravity_assignment_errors,
    deterministic_approval_reasons,
    execution_policy_errors,
    perplexity_assignment_errors,
    preview_plan,
)
from director_orchestrator.store import TaskStore


class PolicyTests(unittest.TestCase):
    def test_antigravity_scope_allows_non_authority_words_in_low_level_code(self):
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.GEMINI,
            kind=AssignmentKind.LOW_LEVEL_IMPLEMENTATION,
            task=(
                "Create non-authoritative low-level boilerplate for a merge utility "
                "and approve workflow; a later Claude assignment verifies the result"
            ),
            acceptance=["Produce mechanical fixtures only"],
            repository="example/demo",
        )

        self.assertEqual(antigravity_assignment_errors(assignment), [])

    def test_antigravity_rejects_authority_in_acceptance_fields(self):
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.GEMINI,
            kind=AssignmentKind.LOW_LEVEL_IMPLEMENTATION,
            task="Generate low-level mechanical parser boilerplate",
            acceptance=["Approve the pull request and issue the final verdict"],
            repository="example/demo",
        )

        self.assertTrue(antigravity_assignment_errors(assignment))

    def test_perplexity_requires_unbound_research_without_authority(self):
        valid = Assignment(
            sequence=1,
            agent=ProviderName.PERPLEXITY,
            kind=AssignmentKind.RESEARCH,
            task="Find current primary sources about calibration metrics",
            acceptance=["Report sources and contradictions"],
        )
        self.assertEqual(perplexity_assignment_errors(valid), [])

        for invalid in (
            valid.model_copy(update={"kind": AssignmentKind.EXACT_SHA_REVIEW}),
            valid.model_copy(update={"repository": "example/private"}),
            valid.model_copy(update={"task": "Audit this patch and return PASS or FAIL"}),
        ):
            with self.subTest(assignment=invalid):
                self.assertTrue(perplexity_assignment_errors(invalid))

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
        self.assertLessEqual(plan.max_hops, 4)
        self.assertLessEqual(plan.max_revisions, 1)

    def test_dry_run_uses_claude_as_default_workhorse(self):
        plan = preview_plan("Project: demo\nImplement and test a bounded API change", self.settings)
        self.assertEqual(plan.assignments[0].agent.value, "claude")

    def test_dry_run_can_request_cursor_second_pass_explicitly(self):
        plan = preview_plan("Project: demo\nRun an independent coding pass in Cursor", self.settings)
        self.assertEqual(plan.assignments[0].agent.value, "cursor")

    def test_primary_team_is_openai_claude_cursor(self):
        self.assertEqual(self.settings.primary_team, ("openai", "claude", "cursor"))
        strategy = self.settings.provider_strategy()
        self.assertTrue(all(strategy[name]["team"] == "primary" for name in self.settings.primary_team))
        self.assertEqual(strategy["gemini"]["team"], "supplemental")
        self.assertEqual(strategy["perplexity"]["team"], "supplemental")

    def test_primary_team_override_fails_closed(self):
        settings = replace(
            self.settings,
            primary_team=("openai", "claude", "cursor", "gemini"),
        )
        plan = preview_plan("Project: demo\nImplement a bounded change", settings)

        self.assertIn(
            "primary team must be exactly openai, claude, cursor",
            execution_policy_errors(plan, settings),
        )

    def test_antigravity_readiness_requires_subordinate_profile(self):
        ready = replace(self.settings, gemini_api_key="test-gemini")
        unsafe = replace(ready, gemini_allow_primary_authority=True)

        self.assertTrue(ready.readiness()["gemini"])
        self.assertFalse(unsafe.readiness()["gemini"])
        self.assertFalse(
            unsafe.antigravity_subordinate_readiness()["primary_authority_disabled"]
        )

    def test_antigravity_requires_subordinate_kind_and_primary_consumer(self):
        settings = replace(
            self.settings,
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        antigravity = Assignment(
            sequence=1,
            agent=ProviderName.GEMINI,
            kind=AssignmentKind.LOW_LEVEL_IMPLEMENTATION,
            task="Generate low-level mechanical parser boilerplate",
            acceptance=["Return code and local test output"],
            repository="example/demo",
        )
        valid = DirectorPlan(
            project="demo",
            normalized_outcome="Use subordinate boilerplate only after primary integration",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=2,
            max_revisions=1,
            assignments=[
                antigravity,
                Assignment(
                    sequence=2,
                    agent=ProviderName.CLAUDE,
                    kind=AssignmentKind.IMPLEMENTATION,
                    task="Independently verify and integrate or reject the artifact",
                    acceptance=["Verified result is reported"],
                    repository="example/demo",
                    depends_on=[1],
                ),
            ],
            final_acceptance=["Primary integration is verified"],
        )

        self.assertEqual(execution_policy_errors(valid, settings), [])

        invalid = valid.model_copy(
            update={"assignments": [antigravity.model_copy(update={"kind": AssignmentKind.GENERAL})]}
        )
        errors = execution_policy_errors(invalid, settings)
        self.assertTrue(any("requires kind" in error for error in errors))
        self.assertTrue(any("requires a later Claude or Cursor" in error for error in errors))

        vacuous_consumer = valid.model_copy(
            update={
                "assignments": [
                    antigravity,
                    valid.assignments[1].model_copy(update={"task": "Adopt the result"}),
                ]
            }
        )
        self.assertTrue(
            any(
                "explicitly independently verifies and integrates or rejects" in error
                for error in execution_policy_errors(vacuous_consumer, settings)
            )
        )

    def test_antigravity_dry_run_is_never_the_final_assignment(self):
        plan = preview_plan(
            "Project: demo\nGenerate low-level mechanical boilerplate",
            self.settings,
        )

        self.assertEqual(
            [item.agent for item in plan.assignments],
            [ProviderName.GEMINI, ProviderName.CLAUDE],
        )
        self.assertEqual(plan.assignments[0].kind, AssignmentKind.BOILERPLATE)
        self.assertEqual(plan.assignments[1].depends_on, [1])

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
            update={"assignments": [assignment.model_copy(update={"requires_approval": True})]}
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
