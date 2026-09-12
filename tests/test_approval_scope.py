# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:25 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 12:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:51 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:38 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:59 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.approvals import (
    assignment_approval_digest,
    record_task_effect_observation,
)
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.policy import (
    deterministic_approval_reasons,
    enforce_plan,
)
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    IntakeAuthentication,
    ProviderName,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore
from director_orchestrator.policy import enforce_plan


class AcceptingDirector:
    async def review(self, record: TaskRecord, result: str) -> ReviewDecision:
        return ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary="Acceptance evidence is present.",
            evidence=[result],
        )


class RecordingAdapter:
    def __init__(self):
        self.dispatched: list[int] = []

    async def dispatch(self, assignment: Assignment, context: str) -> ProviderRun:
        self.dispatched.append(assignment.sequence)
        return ProviderRun(
            provider=ProviderName.CURSOR,
            run_id=f"run-{assignment.sequence}",
            session_id=f"agent-{assignment.sequence}",
            status="FINISHED",
            result=f"Evidence for assignment {assignment.sequence}",
        )

    async def poll(self, run: ProviderRun) -> ProviderRun:
        return run


def _gated_plan() -> DirectorPlan:
    """Two gated assignments, as enforce_plan produces when any gate fires."""
    return DirectorPlan(
        project="demo",
        normalized_outcome="ship two gated steps",
        risk_level=RiskLevel.HIGH,
        approval_required=True,
        approval_reasons=["merge or protected-branch change"],
        max_hops=4,
        max_revisions=0,
        assignments=[
            Assignment(
                sequence=1,
                agent=ProviderName.CURSOR,
                task="First gated step",
                acceptance=["Evidence exists"],
                repository="example/demo",
                requires_approval=True,
            ),
            Assignment(
                sequence=2,
                agent=ProviderName.CURSOR,
                task="Second gated step",
                acceptance=["Evidence exists"],
                repository="example/demo",
                requires_approval=True,
            ),
        ],
        final_acceptance=["Both assignments accepted"],
    )


class ApprovalScopeTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self, directory: str, record: TaskRecord):
        store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
        settings = replace(
            Settings(),
            allow_paid_runs=True,
            openai_api_key="test-openai",
            cursor_api_key="test-cursor",
            max_agent_hops=4,
            provider_poll_seconds=0,
            provider_timeout_seconds=1,
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
            sqlite_path=str(Path(directory) / "tasks.sqlite3"),
        )
        raw_plan = record.plan
        self.assertIsNotNone(raw_plan)
        record.plan = None
        store.create(record)
        engine = OrchestrationEngine(settings, store)
        persisted = store.get(record.task_id)
        target_generation = persisted.plan_generation + 1
        reservation = engine._reserve_director_effect(
            persisted,
            phase="plan",
            identity={
                "intake": hashlib.sha256(
                    persisted.intake.model_dump_json().encode("utf-8")
                ).hexdigest(),
                "target_generation": target_generation,
            },
            estimated_cost_usd=settings.openai_plan_estimated_cost_usd,
        )
        self.assertIsNotNone(reservation)
        persisted, effect_key = reservation
        plan_interaction_id = engine._begin_director_interaction(
            persisted,
            phase="plan",
            effect_key=effect_key,
            plan_generation=target_generation,
        )
        verified_plan_event = engine._complete_director_interaction(
            persisted,
            interaction_id=plan_interaction_id,
            result=raw_plan,
        )
        persisted.plan = enforce_plan(raw_plan, persisted.intake.text, settings)
        persisted.plan_generation = target_generation
        persisted.plan_interaction_id = plan_interaction_id
        record_task_effect_observation(
            persisted,
            effect_key=effect_key,
            intent_kind="director_intent",
            observation_kind="director_observation",
            phase="plan",
            outcome="completed",
            run_id=engine._plan_materialization_binding(
                persisted,
                verified_event_digest=verified_plan_event.event_digest,
                verified_result_digest=(
                    verified_plan_event.transport_evidence.result_digest or ""
                ),
                effect_key=effect_key,
            ),
        )
        store.save(persisted)
        adapter = RecordingAdapter()
        engine.providers[ProviderName.CURSOR] = adapter
        engine.director = AcceptingDirector()
        return store, engine, adapter

    def _record(self, approved: list[int]) -> TaskRecord:
        record = TaskRecord(
            task_id="DIR-SCOPE",
            status=TaskStatus.QUEUED,
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
            intake=SlackIntake(
                source_event_id="evt-scope",
                channel_id="C123",
                thread_ts="123.45",
                user_id="U123",
                text="DIRECTOR Project: demo Outcome: ship two gated steps",
            ),
            plan=_gated_plan(),
            owner_approved=True,
            approved_assignments=list(approved),
        )
        record.approved_assignment_digests = {
            sequence: assignment_approval_digest(
                next(item for item in record.plan.assignments if item.sequence == sequence)
            )
            for sequence in approved
        }
        return record

    async def test_approving_one_assignment_does_not_clear_the_next(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store, engine, adapter = self._engine(directory, self._record([1]))
            await engine.run("DIR-SCOPE")

            record = store.get("DIR-SCOPE")
            # Assignment 1 was approved and ran; assignment 2 must stop and ask.
            self.assertEqual(adapter.dispatched, [1])
            self.assertEqual(record.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(record.next_assignment_index, 1)
            self.assertIn("2", record.message)

    async def test_approving_each_assignment_completes_the_plan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store, engine, adapter = self._engine(directory, self._record([1, 2]))
            await engine.run("DIR-SCOPE")

            record = store.get("DIR-SCOPE")
            self.assertEqual(adapter.dispatched, [1, 2])
            self.assertEqual(record.status, TaskStatus.DONE)

    async def test_unapproved_first_assignment_never_dispatches(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store, engine, adapter = self._engine(directory, self._record([]))
            await engine.run("DIR-SCOPE")

            record = store.get("DIR-SCOPE")
            self.assertEqual(adapter.dispatched, [])
            self.assertEqual(record.status, TaskStatus.OWNER_DECISION_REQUIRED)


class GeneratedPlanGateTests(unittest.TestCase):
    """The deterministic floor must scan what the planner generated.

    Regression for a P1 raised in independent review of PR #121: enforce_plan
    received only ``record.intake.text``, so an approval-sensitive assignment
    the model introduced -- one the owner never mentioned -- produced no
    deterministic reason and could reach execution without
    OWNER_DECISION_REQUIRED.
    """

    def _plan(self, task, acceptance=None):
        return DirectorPlan(
            project="demo",
            normalized_outcome="add a version field",
            risk_level=RiskLevel.LOW,
            approval_required=False,
            approval_reasons=[],
            max_hops=2,
            max_revisions=0,
            final_acceptance=["Change is reviewed"],
            assignments=[
                Assignment(
                    sequence=1,
                    agent=ProviderName.CURSOR,
                    task=task,
                    acceptance=acceptance or ["Evidence exists"],
                    repository="example/demo",
                )
            ],
        )

    def test_other_action_bearing_fields_cannot_hide_sensitive_actions(self):
        from director_orchestrator.models import DebatePlan
        for field in ("owner_question", "public_research_query", "debate"):
            with self.subTest(field=field):
                plan = self._plan("Update notes")
                sensitive = "Deploy the release to production"
                if field == "debate":
                    plan.debate = DebatePlan(question=sensitive, position_assignments=[1, 2],
                                             rebuttal_assignments=[3, 4], vote_options=["yes", "no"])
                elif field == "owner_question":
                    plan.owner_question = sensitive
                else:
                    setattr(plan.assignments[0], field, sensitive)
                # Inspect the corpus as well: later debate normalization can
                # add independent reasons, which must not mask this omission.
                from director_orchestrator.policy import _generated_plan_gate_strings
                self.assertIn(sensitive, _generated_plan_gate_strings(plan))
                enforced = enforce_plan(plan, "Tidy notes", Settings())
                self.assertIn("production deployment or public publishing", enforced.approval_reasons)

    def test_intake_deterministic_label_survives_generated_reason_union(self):
        plan = self._plan("Deploy the release to production")
        intake = "Delete the archived data"
        original = set(deterministic_approval_reasons(intake))
        self.assertTrue(original)
        enforced = enforce_plan(plan, intake, Settings())
        self.assertTrue(original.issubset(enforced.approval_reasons))

    def test_benign_intake_cannot_hide_a_generated_sensitive_action(self):
        benign = "Add a build number to the status page."
        self.assertEqual([], deterministic_approval_reasons(benign))

        enforced = enforce_plan(
            self._plan("Deploy the release to production"), benign, Settings()
        )

        self.assertTrue(enforced.approval_required)
        self.assertTrue(enforced.approval_reasons, enforced.approval_reasons)

    def test_acceptance_criteria_are_scanned_too(self):
        enforced = enforce_plan(
            self._plan(
                "Update notes",
                acceptance=["Deploy to production once green"],
            ),
            "Tidy the changelog.",
            Settings(),
        )
        self.assertTrue(enforced.approval_required, enforced.approval_reasons)

    def test_a_genuinely_benign_plan_is_not_escalated(self):
        enforced = enforce_plan(
            self._plan("Add a version field"), "Add a version field.", Settings()
        )
        self.assertEqual([], enforced.approval_reasons)
        self.assertFalse(enforced.approval_required)

    def test_final_acceptance_is_scanned(self):
        """Raised P1: a gated action stated only in final_acceptance.

        Those criteria reach the Director on the final assignment, and a
        CHANGES_REQUIRED response can turn an unmet criterion into a revision
        prompt dispatched without another deterministic scan.
        """

        plan = self._plan("Add a version field")
        plan.final_acceptance = ["Deploy to production once checks pass"]

        enforced = enforce_plan(plan, "Add a build number.", Settings())

        self.assertTrue(enforced.approval_required, enforced.approval_reasons)

    def test_repository_identifiers_do_not_trip_the_prose_gates(self):
        """A repository slug is an identifier, not an action.

        Repository access is governed by the allowlist; matching org/release or
        org/admin-tools here escalated benign work on a name alone.
        """

        for repository in ("example/release", "example/identity", "example/admin-tools"):
            with self.subTest(repository=repository):
                plan = self._plan("Add a version field")
                plan.assignments[0].repository = repository

                enforced = enforce_plan(plan, "Add a build number.", Settings())

                self.assertEqual([], enforced.approval_reasons)
                self.assertFalse(enforced.approval_required)

    def test_fields_are_scanned_independently_not_as_one_corpus(self):
        """The external-representation gate spans 50 characters with re.S.

        Joining fields let a verb in one match an audience noun in another,
        escalating a plan that contains no external action.
        """

        plan = self._plan(
            "Send events through the local queue",
            acceptance=["Customer-facing layout remains unchanged"],
        )

        enforced = enforce_plan(plan, "Tidy the queue worker.", Settings())

        self.assertEqual([], enforced.approval_reasons)
        self.assertFalse(enforced.approval_required)

    def test_a_verbose_planner_cannot_crowd_out_a_deterministic_label(self):
        """approval_reasons is capped; the floor must survive the cap.

        Otherwise the store rejects the persisted plan on its next read and an
        approval-required task becomes unreadable.
        """

        from director_orchestrator.policy import _MAX_APPROVAL_REASONS

        plan = self._plan("Deploy the release to production")
        plan.approval_reasons = [
            f"model reason {index}" for index in range(_MAX_APPROVAL_REASONS)
        ]

        enforced = enforce_plan(plan, "Add a build number.", Settings())

        self.assertLessEqual(len(enforced.approval_reasons), _MAX_APPROVAL_REASONS)
        self.assertIn(
            "production deployment or public publishing", enforced.approval_reasons
        )
        # The result must still round-trip through the model that persists it.
        DirectorPlan.model_validate(enforced.model_dump())


# Kept last so a direct `python tests/test_approval_scope.py` run sees every
# class defined above it; running it earlier silently omitted these cases.
if __name__ == "__main__":
    unittest.main()

# [[Codex | GPT-5.6 | 2026-08-24 | 09:59 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:38 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:51 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 12:20 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:25 UTC | END]]
