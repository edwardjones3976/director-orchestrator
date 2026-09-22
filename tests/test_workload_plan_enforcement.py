# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 03:40 UTC | BEGIN]]
# [[GitHub Copilot CLI | Claude Sonnet 5 | 2026-09-20 | 03:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:32 UTC | BEGIN]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:38 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:05 UTC | BEGIN]]
from __future__ import annotations

import unittest
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator import contact_families
from director_orchestrator import policy as policy_module
from director_orchestrator.config import Settings
from director_orchestrator.models import (
    Assignment,
    AssignmentKind,
    DirectorPlan,
    PlanPrestageRecord,
    ProviderName,
    ResearchEvidenceType,
    RiskLevel,
)
from director_orchestrator.policy import enforce_plan, execution_policy_errors
from director_orchestrator.policy import preview_plan
from director_orchestrator.store import TaskStore
from director_orchestrator.workload_policy import (
    FABLE_MODEL_FLOOR, WorkKind, WorkSize, Provider, PRIMARY_PROVIDERS, EnforcementMode,
)


class WorkloadPlanEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
            workload_policy_enabled=True,
            workload_policy_mode="enforced",
            workload_policy_window_assignments=20,
            workload_policy_shadow_assignments=5,
        )

    @staticmethod
    def _prestage(
        *,
        producer: ProviderName = ProviderName.CLAUDE,
        reviewer: ProviderName = ProviderName.CURSOR,
        budget_units: int = 7,
    ) -> PlanPrestageRecord:
        return PlanPrestageRecord(
            packet_id="demo-plan-1",
            goal="Implement and independently review the bounded change",
            terminal_condition="Exact candidate accepted",
            repository="example/demo",
            worktree="C:/worktrees/demo-plan-1",
            branch="main",
            base_sha="a" * 40,
            candidate_sha=None,
            producer=producer,
            independent_reviewer=reviewer,
            owned_paths=("src/demo.py", "tests/test_demo.py"),
            prohibited_paths=("private/",),
            required_tests=("python -m unittest tests.test_demo",),
            required_security_evidence=("No secret or credential diff",),
            budget_units=budget_units,
            timeout_seconds=3_600,
            retry_limit=3,
            merge_eligibility_conditions=("Exact-SHA review accepts", "CI is green"),
            stopping_conditions=("Owner decision is required", "Budget is exhausted"),
        )

    @classmethod
    def _valid_plan(cls) -> DirectorPlan:
        return DirectorPlan(
            project="demo",
            normalized_outcome="Implement and independently review the bounded change",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=2,
            max_revisions=1,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CLAUDE,
                    kind=AssignmentKind.IMPLEMENTATION,
                    workload_size=WorkSize.MEDIUM,
                    effort_points=5,
                    model_label=FABLE_MODEL_FLOOR,
                    task="Implement the bounded change",
                    acceptance=["Focused tests pass"],
                    repository="example/demo",
                ),
                Assignment(
                    sequence=2,
                    agent=ProviderName.CURSOR,
                    kind=AssignmentKind.INDEPENDENT_REVIEW,
                    workload_size=WorkSize.SHORT,
                    effort_points=2,
                    task="Independently review the exact candidate",
                    acceptance=["Return findings tied to the candidate"],
                    repository="example/demo",
                    depends_on=[1],
                ),
            ],
            final_acceptance=["Exact candidate accepted"],
            workload_policy_version="v1",
            prestage=cls._prestage(),
        )

    def test_legacy_persisted_plan_remains_readable(self) -> None:
        legacy = DirectorPlan.model_validate(
            {
                "project": "demo",
                "normalized_outcome": "Read an older persisted plan",
                "risk_level": "low",
                "approval_required": False,
                "max_hops": 1,
                "max_revisions": 0,
                "assignments": [
                    {
                        "sequence": 1,
                        "agent": "claude",
                        "task": "Summarize the bounded record",
                        "acceptance": ["Summary exists"],
                        "repository": "example/demo",
                    }
                ],
                "final_acceptance": ["Summary accepted"],
            }
        )

        self.assertIsNone(legacy.workload_policy_version)
        self.assertEqual(legacy.assignments[0].workload_size, WorkSize.SIMPLE)
        self.assertEqual(legacy.assignments[0].effort_points, 1)
        self.assertEqual(execution_policy_errors(legacy, self.settings), [])

    def test_fresh_plan_omission_cannot_bypass_default_on_enforcement(self) -> None:
        fresh = DirectorPlan(
            project="demo",
            normalized_outcome="New plan omitted workload metadata",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=1,
            max_revisions=0,
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CURSOR,
                    kind=AssignmentKind.IMPLEMENTATION,
                    task="Implement a medium-sized change",
                    acceptance=["Tests pass"],
                    repository="example/demo",
                )
            ],
            final_acceptance=["Accepted"],
        )

        compatibility = enforce_plan(
            fresh, "Implement a medium-sized change", self.settings
        )
        self.assertIsNone(compatibility.workload_policy_version)
        self.assertIsNone(compatibility.workload_enforcement)
        self.assertEqual(execution_policy_errors(compatibility, self.settings), [])

        guarded = enforce_plan(
            fresh,
            "Implement a medium-sized change",
            self.settings,
            activate_workload_policy=True,
        )

        self.assertEqual(guarded.workload_policy_version, "v1")
        self.assertEqual(guarded.assignments[0].workload_size, WorkSize.MEDIUM)
        self.assertEqual(guarded.workload_enforcement.outcome, "block")
        errors = execution_policy_errors(guarded, self.settings)
        self.assertIn(
            "v1 workload plans require an immutable pre-stage packet", errors
        )
        self.assertFalse(
            any("medium_long_implementation_requires_claude" in error for error in errors)
        )

    def test_dry_run_preview_has_team_and_digest_but_no_dispatch_authority(self) -> None:
        preview = preview_plan(
            "Project: demo\nImplement and test a bounded API change", self.settings
        )

        self.assertEqual(preview.workload_policy_version, "v1")
        self.assertTrue(preview.prestage.digest.startswith("sha256:"))
        self.assertEqual(
            {assignment.agent for assignment in preview.assignments},
            {ProviderName.CLAUDE, ProviderName.CURSOR},
        )
        self.assertEqual(execution_policy_errors(preview, self.settings), [])

        paid = replace(self.settings, allow_paid_runs=True)
        self.assertIn(
            "dry-run preview packets cannot authorize paid provider dispatch",
            execution_policy_errors(preview, paid),
        )

    def test_research_label_cannot_hide_implementation_scope(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            kind=AssignmentKind.RESEARCH,
            task="Research and implement the bounded adapter fix",
            acceptance=["The implementation is tested"],
            repository="example/demo",
        )

        self.assertEqual(
            policy_module.trusted_work_kind(assignment),
            WorkKind.IMPLEMENTATION,
        )

    def test_dry_run_preview_uses_first_allowlisted_repository(self) -> None:
        settings = replace(
            self.settings,
            repository_allowlist=("example/primary", "example/secondary"),
        )

        preview = preview_plan(
            "Project: demo\nImplement and test a bounded API change",
            settings,
        )

        self.assertEqual(preview.assignments[0].repository, "example/primary")
        self.assertEqual(preview.assignments[1].repository, "example/primary")
        self.assertEqual(preview.prestage.repository, "example/primary")
        self.assertEqual(execution_policy_errors(preview, settings), [])

    def test_enforce_plan_attaches_canonical_immutable_prestage_digest(self) -> None:
        plan = self._valid_plan()
        self.assertIsNone(plan.prestage.digest)

        guarded = enforce_plan(plan, "bounded implementation", self.settings)

        self.assertTrue(guarded.prestage.digest.startswith("sha256:"))
        self.assertEqual(guarded.prestage.goal, guarded.normalized_outcome)
        self.assertEqual(guarded.workload_enforcement.outcome, "allow")
        self.assertEqual(execution_policy_errors(guarded, self.settings), [])
        self.assertEqual(len(guarded.assignments), len(plan.assignments))

        tampered = guarded.prestage.model_copy(
            update={"owned_paths": ("src/other.py",)}
        )
        changed = guarded.model_copy(update={"prestage": tampered})
        self.assertIn(
            "pre-stage packet digest does not match its canonical contents",
            execution_policy_errors(changed, self.settings),
        )

    def test_underclassified_workload_is_raised_to_trusted_size_and_effort(self) -> None:
        plan = self._valid_plan()
        plan.assignments[0] = plan.assignments[0].model_copy(
            update={
                "workload_size": WorkSize.SHORT,
                "effort_points": 1,
                "model_label": "explicit-model-choice",
            }
        )
        plan.assignments[1] = plan.assignments[1].model_copy(
            update={"workload_size": WorkSize.SIMPLE, "effort_points": 100}
        )

        guarded = enforce_plan(plan, "bounded implementation", self.settings)

        self.assertEqual(guarded.assignments[0].workload_size, WorkSize.MEDIUM)
        self.assertEqual(guarded.assignments[0].effort_points, 5)
        self.assertEqual(guarded.assignments[0].model_label, "explicit-model-choice")
        self.assertEqual(guarded.assignments[1].workload_size, WorkSize.SHORT)
        self.assertEqual(guarded.assignments[1].effort_points, 2)
        self.assertEqual(execution_policy_errors(guarded, self.settings), [])

    def test_valid_plan_reaches_atomic_admission_without_fabricating_history(self) -> None:
        guarded = enforce_plan(self._valid_plan(), "bounded implementation", self.settings)
        self.assertEqual(execution_policy_errors(guarded, self.settings), [])
        assignment = guarded.assignments[0]
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "ledger.sqlite")
            store = TaskStore(path)
            def reserve(key):
                return store.reserve_workload_assignment(
                    reservation_key=key, task_id=key, assignment_id=f"{key}:1",
                    phase="dispatch", provider=Provider(assignment.agent.value),
                    effort_units=assignment.effort_points, kind=WorkKind.IMPLEMENTATION,
                    size=assignment.workload_size, model_label=assignment.model_label,
                    available_primary_providers=PRIMARY_PROVIDERS,
                    mode=EnforcementMode.ENFORCED, shadow_assignment_limit=5)
            empty = reserve("empty")
            self.assertTrue(empty.decision.allowed, empty.decision.violations)
            # Isolated historical accepted-credit fixture, never a live seeding path.
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM direct_production_credits").fetchone()[0], 0)
                for index, provider in enumerate(["cursor"]*14 + ["claude"]*5):
                    connection.execute("""INSERT INTO direct_production_credits(
                        credit_key, task_id, plan_generation, assignment_sequence, assignment_id,
                        provider, effort_units, provider_event_digest, provider_result_digest,
                        review_event_digest, review_result_digest, recorded_at)
                        VALUES (?, ?, 1, 1, ?, ?, 1, ?, ?, ?, ?, ?)""",
                        (str(index), "history", str(index), provider, "a"*64, "b"*64,
                         "c"*64, "d"*64, "2026-09-05T00:00:00Z"))
                connection.commit()
            mature = reserve("mature")
            # The real startup reservation still occupies capacity alongside the
            # five accepted Claude fixtures; a seventh Claude is now permitted.
            self.assertTrue(mature.decision.allowed)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM direct_production_credits").fetchone()[0], 19)

    def test_explicit_larger_size_remains_and_derives_effort_without_model_rewrite(self) -> None:
        plan = self._valid_plan()
        plan.assignments[0] = plan.assignments[0].model_copy(
            update={
                "workload_size": WorkSize.LONG,
                "effort_points": 1,
                "model_label": None,
            }
        )
        plan.prestage = self._prestage(budget_units=10)

        guarded = enforce_plan(plan, "bounded implementation", self.settings)

        self.assertEqual(guarded.assignments[0].workload_size, WorkSize.LONG)
        self.assertEqual(guarded.assignments[0].effort_points, 8)
        self.assertIsNone(guarded.assignments[0].model_label)
        self.assertEqual(execution_policy_errors(guarded, self.settings), [])

    def test_underclassified_nonclaude_implementation_keeps_provider(self) -> None:
        plan = self._valid_plan()
        plan.assignments[0] = plan.assignments[0].model_copy(
            update={
                "agent": ProviderName.GROK,
                "workload_size": WorkSize.SHORT,
                "effort_points": 1,
            }
        )
        plan.prestage = self._prestage(producer=ProviderName.GROK)
        guarded = enforce_plan(plan, "bounded implementation", self.settings)
        self.assertEqual(guarded.assignments[0].workload_size, WorkSize.MEDIUM)
        self.assertEqual(guarded.assignments[0].effort_points, 5)
        self.assertEqual(guarded.assignments[0].agent, ProviderName.GROK)
        self.assertFalse(
            any(
                "medium_long_implementation_requires_claude" in error
                for error in execution_policy_errors(guarded, self.settings)
            )
        )

    def test_only_antigravity_implementation_may_remain_short(self) -> None:
        direct = self._valid_plan().assignments[0].model_copy(
            update={
                "agent": ProviderName.CURSOR,
                "kind": AssignmentKind.LOW_LEVEL_IMPLEMENTATION,
                "workload_size": WorkSize.SIMPLE,
                "effort_points": 1,
                "task": "Replace the complete authentication subsystem",
            }
        )
        trusted_direct = policy_module._trusted_v1_assignment(direct)
        self.assertEqual(trusted_direct.workload_size, WorkSize.MEDIUM)
        self.assertEqual(trusted_direct.effort_points, 5)

        antigravity = direct.model_copy(
            update={
                "agent": ProviderName.GEMINI,
                "kind": AssignmentKind.IMPLEMENTATION,
                "task": "Generate bounded scaffolding",
            }
        )
        trusted_antigravity = policy_module._trusted_v1_assignment(antigravity)
        self.assertEqual(trusted_antigravity.workload_size, WorkSize.SHORT)
        self.assertEqual(trusted_antigravity.effort_points, 2)

    def test_perplexity_cannot_relabel_implementation_or_review_as_research(self) -> None:
        implementation = self._valid_plan().assignments[0].model_copy(
            update={
                "agent": ProviderName.PERPLEXITY,
                "kind": AssignmentKind.IMPLEMENTATION,
                "workload_size": WorkSize.SIMPLE,
                "model_label": None,
                "task": "Produce a complete standalone Python module",
                "repository": None,
                "public_research_query": "public module design references",
                "requires_approval": True,
            }
        )
        self.assertEqual(
            policy_module._work_kind(implementation),
            policy_module.WorkKind.IMPLEMENTATION,
        )
        trusted = policy_module._trusted_v1_assignment(implementation)
        self.assertEqual(trusted.workload_size, WorkSize.MEDIUM)

        review = self._valid_plan().assignments[1].model_copy(
            update={
                "agent": ProviderName.PERPLEXITY,
                "repository": None,
                "public_research_query": "public review methods",
                "requires_approval": True,
            }
        )
        plan = self._valid_plan()
        plan.assignments[1] = review
        plan.prestage = self._prestage(reviewer=ProviderName.PERPLEXITY)
        guarded = enforce_plan(plan, "bounded implementation", self.settings)
        errors = execution_policy_errors(guarded, self.settings)
        self.assertTrue(
            any("Perplexity is research-only" in error for error in errors),
            errors,
        )

    def test_prestage_scope_is_canonical_and_digest_bound(self) -> None:
        plan = self._valid_plan()
        plan.prestage = plan.prestage.model_copy(
            update={"repository": "https://github.com/Example/Demo.git"}
        )

        guarded = enforce_plan(plan, "bounded implementation", self.settings)

        self.assertEqual(guarded.prestage.repository, "example/demo")
        self.assertTrue(guarded.prestage.digest.startswith("sha256:"))
        self.assertEqual(execution_policy_errors(guarded, self.settings), [])

    def test_prestage_rejects_forged_repository_branch_and_candidate_sha(self) -> None:
        cases = (
            (
                {"repository": "attacker/forged-repository"},
                "pre-stage repository must match the single canonical assignment repository",
            ),
            (
                {"branch": "feature/unbound"},
                "pre-stage branch must match the single assignment starting ref",
            ),
            (
                {"candidate_sha": "b" * 40},
                "initial pre-stage candidate SHA must be absent before production",
            ),
        )
        for updates, expected in cases:
            with self.subTest(updates=updates):
                plan = self._valid_plan()
                plan.prestage = plan.prestage.model_copy(update=updates)
                guarded = enforce_plan(plan, "bounded implementation", self.settings)
                self.assertIn(expected, execution_policy_errors(guarded, self.settings))

    def test_prestage_rejects_unbound_or_multiple_assignment_scope(self) -> None:
        unbound = self._valid_plan()
        unbound.assignments = [
            assignment.model_copy(update={"repository": None})
            for assignment in unbound.assignments
        ]
        guarded = enforce_plan(unbound, "bounded implementation", self.settings)
        self.assertIn(
            "v1 pre-stage repository scope is unbound",
            execution_policy_errors(guarded, self.settings),
        )

        repositories = self._valid_plan()
        repositories.assignments[1] = repositories.assignments[1].model_copy(
            update={"repository": "example/other"}
        )
        guarded = enforce_plan(repositories, "bounded implementation", self.settings)
        self.assertIn(
            "v1 pre-stage repository scope spans multiple canonical assignment repositories",
            execution_policy_errors(guarded, self.settings),
        )

        refs = self._valid_plan()
        refs.assignments[1] = refs.assignments[1].model_copy(
            update={"starting_ref": "release-2026"}
        )
        guarded = enforce_plan(refs, "bounded implementation", self.settings)
        self.assertIn(
            "v1 pre-stage starting-ref scope spans multiple assignment starting refs",
            execution_policy_errors(guarded, self.settings),
        )

    def test_repository_free_supplemental_plan_does_not_claim_write_scope(self) -> None:
        plan = self._valid_plan()
        plan.assignments = [
            plan.assignments[1].model_copy(
                update={
                    "agent": ProviderName.PERPLEXITY,
                    "kind": AssignmentKind.RESEARCH,
                    "repository": None,
                    "allow_draft_pr": False,
                    "public_research_query": "public evidence only",
                }
            )
        ]
        plan.prestage = self._prestage(
            producer=ProviderName.PERPLEXITY,
            reviewer=ProviderName.OPENAI,
            budget_units=2,
        )

        guarded = enforce_plan(plan, "public research only", self.settings)

        errors = execution_policy_errors(guarded, self.settings)
        self.assertNotIn("v1 pre-stage repository scope is unbound", errors)

    def test_v1_provider_plan_is_team_first(self) -> None:
        plan = self._valid_plan()
        plan.assignments = plan.assignments[:1]
        plan.max_hops = 1
        plan.prestage = self._prestage(budget_units=5)

        guarded = enforce_plan(plan, "bounded implementation", self.settings)
        self.assertTrue(
            any(
                "require at least two distinct team providers" in error
                for error in execution_policy_errors(guarded, self.settings)
            )
        )

    def test_declared_external_user_and_competitor_evidence_requires_research_lane(self) -> None:
        declared = self._valid_plan()
        declared.research_evidence = (
            ResearchEvidenceType.EXTERNAL_SOURCES,
            ResearchEvidenceType.USER_REVIEWS,
            ResearchEvidenceType.COMPETITOR_SOLUTIONS,
        )
        guarded = enforce_plan(declared, "bounded implementation", self.settings)
        self.assertTrue(
            any(
                "requires a bounded Perplexity research assignment" in error
                for error in execution_policy_errors(guarded, self.settings)
            )
        )

        researched = self._valid_plan()
        researched.research_evidence = declared.research_evidence
        researched.assignments.append(
            Assignment(
                sequence=3,
                agent=ProviderName.PERPLEXITY,
                kind=AssignmentKind.RESEARCH,
                workload_size=WorkSize.SHORT,
                effort_points=2,
                task="Find current public evidence and comparable approaches",
                acceptance=["Return URLs, provenance, contradictions, and unknowns"],
                public_research_query=(
                    "current user reviews and competitor solutions for comparable issues"
                ),
                requires_approval=True,
            )
        )
        researched.max_hops = 3
        researched.assignments = [item.model_copy(update={"allocation_profile": "public_research"}) for item in researched.assignments]
        researched.prestage = self._prestage(budget_units=9).model_copy(update={"allocation_profile": "public_research"})
        guarded = enforce_plan(researched, "bounded implementation", self.settings)
        self.assertEqual(execution_policy_errors(guarded, self.settings), [])

    def test_shadow_mode_records_would_block_without_blocking(self) -> None:
        shadow_settings = replace(self.settings, workload_policy_mode="shadow")
        plan = self._valid_plan()
        plan.assignments = [
            Assignment(
                sequence=sequence,
                agent=ProviderName.GROK,
                kind=AssignmentKind.IMPLEMENTATION,
                workload_size=WorkSize.MEDIUM,
                effort_points=5,
                task=f"Implement bounded productive unit {sequence}",
                acceptance=["Evidence recorded"],
                repository="example/demo",
            )
            for sequence in range(1, 21)
        ]
        plan.max_hops = 20
        plan.prestage = self._prestage(
            producer=ProviderName.GROK,
            budget_units=100,
        )

        guarded = enforce_plan(plan, "bounded implementation", shadow_settings)

        self.assertEqual(guarded.workload_enforcement.mode, "shadow")
        self.assertEqual(
            guarded.workload_enforcement.outcome, "shadow_would_block"
        )
        self.assertTrue(guarded.workload_enforcement.allowed)
        self.assertTrue(guarded.workload_enforcement.would_block)
        self.assertFalse(
            any(
                "overall" in violation or "share" in violation
                for violation in guarded.workload_enforcement.violations
            )
        )
        self.assertEqual(execution_policy_errors(guarded, shadow_settings), [])

    def test_disabled_policy_preserves_legacy_execution_behavior(self) -> None:
        disabled = replace(self.settings, workload_policy_enabled=False)
        plan = self._valid_plan()
        plan.assignments = plan.assignments[:1]
        plan.max_hops = 1
        plan.prestage = None

        guarded = enforce_plan(plan, "bounded implementation", disabled)

        self.assertEqual(guarded.workload_enforcement.mode, "disabled")
        self.assertEqual(guarded.workload_enforcement.outcome, "disabled")
        self.assertEqual(execution_policy_errors(guarded, disabled), [])

    def test_complete_plan_overall_window_keeps_share_pressure_soft(self) -> None:
        assignments: list[Assignment] = []
        for sequence in range(1, 21):
            provider = (
                ProviderName.CLAUDE
                if sequence <= 9 or sequence == 20
                else ProviderName.CURSOR
            )
            kind = (
                AssignmentKind.INDEPENDENT_REVIEW
                if sequence == 19
                else AssignmentKind.GENERAL
            )
            assignments.append(
                Assignment(
                    sequence=sequence,
                    agent=provider,
                    kind=kind,
                    workload_size=WorkSize.SHORT,
                    effort_points=1,
                    task=f"Bounded work unit {sequence}",
                    acceptance=["Evidence recorded"],
                    repository="example/demo",
                )
            )
        plan = DirectorPlan(
            project="demo",
            normalized_outcome="Implement and independently review the bounded change",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=20,
            max_revisions=0,
            assignments=assignments,
            final_acceptance=["Exact candidate accepted"],
            workload_policy_version="v1",
            workload_target_variance_reason=(
                "This bounded plan follows dependency ownership; targets are rolling goals"
            ),
            prestage=self._prestage(budget_units=40),
        )

        guarded = enforce_plan(plan, "bounded coordinated work", self.settings)
        errors = execution_policy_errors(guarded, self.settings)
        self.assertFalse(
            any("overall" in error or "share" in error for error in errors)
        )

    def test_exact_retry_browser_and_action_limits_are_plan_metadata(self) -> None:
        limits = self._valid_plan().execution_limits
        self.assertEqual(limits.retry_limit_source, "immutable_assignment_prestage")
        self.assertTrue(limits.additional_attempt_requires_classified_failure)
        self.assertTrue(limits.additional_attempt_requires_positive_information_gain)
        self.assertTrue(limits.additional_attempt_requires_changed_basis)
        self.assertTrue(limits.token_cost_timeout_and_cancellation_budgets_enforced)
        self.assertFalse(limits.ambiguous_paid_dispatch_retry_allowed)
        self.assertEqual(limits.browser_transport_priority, "lower_priority_fallback")
        self.assertEqual(limits.browser_usage_reduction_target_percent, 0)
        self.assertEqual(limits.major_action_limit, 5)
        self.assertEqual(limits.status_check_reduction_target_percent, 80)
        self.assertEqual(limits.duplicate_validation_reduction_target_percent, 70)
        self.assertEqual(limits.total_action_reduction_target_percent, 60)

    def test_assignment_retry_limit_is_canonicalized_to_exactly_three(self) -> None:
        plan = self._valid_plan()
        plan.prestage = plan.prestage.model_copy(
            update={"retry_limit": 1, "digest": None}
        )

        guarded = enforce_plan(plan, "bounded implementation", self.settings)

        self.assertIn(
            "pre-stage packet is invalid: prestage attempt budget must be exactly 3",
            execution_policy_errors(guarded, self.settings),
        )

    @staticmethod
    def _implementation_and_review_plan(
        *, reviewer_agent: ProviderName
    ) -> DirectorPlan:
        """A minimal plan with one implementation and one dependent review, for
        direct whitebox tests of ``_independent_review_binding_errors``."""

        implementation = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            kind=AssignmentKind.IMPLEMENTATION,
            task="Implement the bounded change",
            acceptance=["Tests pass"],
            repository="example/demo",
        )
        review = Assignment(
            sequence=2,
            agent=reviewer_agent,
            kind=AssignmentKind.INDEPENDENT_REVIEW,
            task="Independently review the exact candidate",
            acceptance=["Return findings tied to the candidate"],
            repository="example/demo",
            depends_on=[1],
        )
        return DirectorPlan(
            project="demo",
            normalized_outcome="Implement and independently review",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            max_hops=1,
            max_revisions=0,
            assignments=[implementation, review],
            final_acceptance=["Accepted"],
        )

    def test_independent_review_family_unknown_fails_closed(self) -> None:
        """Directive #207: a reviewer identity absent from the contact-family
        table is never assumed independent of the producer it would review.
        ``owner`` is removed from the table for this test only."""

        plan = self._implementation_and_review_plan(reviewer_agent=ProviderName.OWNER)

        with patch.dict(contact_families.CONTACT_LANES_BY_ID):
            del contact_families.CONTACT_LANES_BY_ID["owner"]
            errors = policy_module._independent_review_binding_errors(plan)

        self.assertIn("assignment 2: reviewer_family_unknown", errors)

    def test_independent_review_rejects_same_provider_family_reviewer(self) -> None:
        """Directive #207, "Contact-family safety": a reviewer sharing the
        producer's provider family is rejected even under a distinct contact
        identity. No two ``ProviderName`` members share a family today, so
        this test binds one family label to prove the check itself fires."""

        plan = self._implementation_and_review_plan(reviewer_agent=ProviderName.CURSOR)

        with patch.dict(
            contact_families.CONTACT_LANES_BY_ID,
            {
                "cursor": contact_families.ContactLane(
                    lane_id="cursor", product="Cursor Agent", provider_family="Anthropic"
                )
            },
        ):
            errors = policy_module._independent_review_binding_errors(plan)

        self.assertIn("assignment 2: reviewer_shares_provider_family", errors)


if __name__ == "__main__":
    unittest.main()
# [[GitHub Copilot CLI | Claude Sonnet 5 | 2026-09-20 | 03:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:29 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:39 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:20 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[Codex | GPT-6 | 2026-09-05 | 23:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:32 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:34 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 03:40 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | END]]
