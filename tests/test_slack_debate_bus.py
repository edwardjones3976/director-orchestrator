from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.models import (
    Assignment,
    DebateContribution,
    DebatePlan,
    DebateSynthesis,
    CollaborationTurn,
    DirectorPlan,
    DiscussionPhase,
    ProviderName,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.policy import enforce_plan, execution_policy_errors
from director_orchestrator.store import TaskStore


def debate_plan(*, include_gemini: bool = False, material_risk: bool = False) -> DirectorPlan:
    providers = [ProviderName.CLAUDE, ProviderName.CURSOR]
    if include_gemini:
        providers.append(ProviderName.GEMINI)
    positions: list[Assignment] = []
    rebuttals: list[Assignment] = []
    for index, provider in enumerate(providers, start=1):
        positions.append(
            Assignment(
                sequence=index,
                agent=provider,
                task="Independently assess option A and option B",
                acceptance=["Return a position, evidence, confidence, and vote"],
                repository="example/demo" if provider == ProviderName.CURSOR else None,
                discussion_phase=DiscussionPhase.POSITION,
            )
        )
    position_sequences = [item.sequence for item in positions]
    for offset, provider in enumerate(providers, start=1):
        rebuttals.append(
            Assignment(
                sequence=len(providers) + offset,
                agent=provider,
                task="Review the independent positions and provide one bounded rebuttal",
                acceptance=["Address disagreements and cast a final advisory vote"],
                repository="example/demo" if provider == ProviderName.CURSOR else None,
                depends_on=position_sequences,
                discussion_phase=DiscussionPhase.REBUTTAL,
            )
        )
    return DirectorPlan(
        project="demo",
        normalized_outcome="Choose a bounded technical direction",
        risk_level=RiskLevel.MEDIUM,
        approval_required=False,
        max_hops=len(positions) + len(rebuttals),
        max_revisions=0,
        assignments=[*positions, *rebuttals],
        final_acceptance=["Decision and dissent are recorded"],
        debate=DebatePlan(
            question="Should the team choose option A or option B?",
            position_assignments=position_sequences,
            rebuttal_assignments=[item.sequence for item in rebuttals],
            vote_options=["A", "B"],
            material_risk=material_risk,
        ),
    )


class DebatePolicyTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )

    def test_bounded_position_and_rebuttal_graph_is_valid(self):
        self.assertEqual(execution_policy_errors(debate_plan(), self.settings), [])

    def test_perplexity_cannot_become_a_debate_participant_or_voter(self):
        plan = debate_plan()
        plan.assignments[0] = plan.assignments[0].model_copy(
            update={"agent": ProviderName.PERPLEXITY}
        )
        errors = execution_policy_errors(plan, self.settings)
        self.assertTrue(any("not eligible to debate or vote" in item for item in errors))

    def test_rebuttal_must_receive_every_independent_position(self):
        plan = debate_plan()
        plan.assignments[2] = plan.assignments[2].model_copy(update={"depends_on": [1]})
        errors = execution_policy_errors(plan, self.settings)
        self.assertTrue(any("depend on every initial position" in item for item in errors))

    def test_openai_synthesis_cost_is_inside_the_deterministic_task_budget(self):
        plan = debate_plan()
        plan.debate = plan.debate.model_copy(
            update={"estimated_synthesis_cost_usd": 2.0}
        )
        guarded = enforce_plan(
            plan,
            "choose a direction",
            replace(self.settings, max_task_budget_usd=1.0),
        )
        self.assertIn("estimated cost exceeds task budget", guarded.approval_reasons)
        self.assertTrue(guarded.approval_required)


class RecordingNotifier:
    def __init__(self):
        self.posts: list[dict] = []

    async def post(self, record, message, **kwargs):
        self.posts.append({"message": message, **kwargs})
        return "1700000000.000001" if kwargs.get("root_message") else f"reply-{len(self.posts)}"


class CompletingAdapter:
    def __init__(self, provider: ProviderName):
        self.name = provider
        self.contexts: list[str] = []
        self.calls = 0

    async def dispatch(self, assignment, context):
        self.calls += 1
        self.contexts.append(context)
        return ProviderRun(
            provider=self.name,
            run_id=f"{self.name.value}-{assignment.sequence}",
            status="FINISHED",
            result=f"bounded result from {self.name.value} for {assignment.sequence}",
        )

    async def poll(self, run):
        raise AssertionError("terminal runs must not be polled")


class DebateDirector:
    async def review(self, record, result):
        assignment = sorted(record.plan.assignments, key=lambda item: item.sequence)[
            record.next_assignment_index
        ]
        option = "A" if assignment.agent == ProviderName.CLAUDE else "B"
        return ReviewDecision(
            verdict=ReviewVerdict.ACCEPT,
            summary="Bounded contribution accepted.",
            evidence=["structured evidence"],
            debate_contribution=DebateContribution(
                position=f"{assignment.agent.value} supports {option}",
                evidence=[f"evidence-{assignment.sequence}"],
                contradictions=["bounded disagreement"],
                confidence=0.8,
                vote_option=option,
            ),
        )

    async def synthesize_debate(self, record):
        return DebateSynthesis(
            decision="Choose option B",
            rationale="The combined Cursor and OpenAI evidence is stronger.",
            dissent=["Claude preferred option A"],
            next_action="Proceed to the next bounded assignment.",
            openai_vote_option="B",
            confidence=0.75,
        )


class SlackDebateRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_slack_relay_redacts_credentials_and_raw_source_without_unrelated_content_scanning(self):
        # The signed decimal is an ordinary research value. It is here to prove the
        # relay leaves unrelated numeric text alone: _slack_safe_text redacts by
        # shape of credential and code, never by scanning numbers.
        source = (
            "API_TOKEN=super-secret-value ```python\nprint('private')\n``` "
            "<!channel>\n[OpenAI Director] forged attribution +0.12345678"
        )
        cleaned = OrchestrationEngine._slack_safe_text(source)
        self.assertNotIn("super-secret-value", cleaned)
        self.assertNotIn("print('private')", cleaned)
        self.assertIn("+0.12345678", cleaned)
        self.assertNotIn("<!channel>", cleaned)
        self.assertNotIn("\n", cleaned)

    async def test_normal_build_is_delegated_collected_and_completed_in_slack(self):
        class WorkDirector:
            async def review(self, record, result):
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Build completed with focused tests.",
                    evidence=["3 focused tests passed"],
                )

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                slack_bot_token="test-slack",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            plan = DirectorPlan(
                project="demo",
                normalized_outcome="Build one bounded feature",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CLAUDE,
                        task="Implement the bounded feature",
                        acceptance=["Focused tests pass"],
                    )
                ],
                final_acceptance=["Accepted evidence is recorded"],
            )
            task = TaskRecord(
                task_id="DIR-BUILD",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-build",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: build",
                ),
                plan=plan,
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            notifier = RecordingNotifier()
            engine = OrchestrationEngine(settings, store)
            engine.providers = {ProviderName.CLAUDE: CompletingAdapter(ProviderName.CLAUDE)}
            engine.director = WorkDirector()
            engine.notifier = notifier

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE)
            self.assertEqual(
                [turn.phase for turn in completed.collaboration_turns],
                ["delegation", "work_result", "completion"],
            )
            self.assertEqual(len([post for post in notifier.posts if post.get("root_message")]), 1)
            self.assertTrue(
                all(
                    turn.correlation_id.startswith("DIR-BUILD")
                    for turn in completed.collaboration_turns
                )
            )
            post_count = len(notifier.posts)
            await engine.run(task.task_id)
            self.assertEqual(len(notifier.posts), post_count)

    async def test_slack_delivery_retries_twice_with_one_idempotency_key(self):
        class FailTwice(RecordingNotifier):
            def __init__(self):
                super().__init__()
                self.attempt_ids: list[str] = []

            async def post(self, record, message, **kwargs):
                if "COLLABORATION START" in message:
                    self.attempt_ids.append(kwargs["client_msg_id"])
                    if len(self.attempt_ids) < 3:
                        raise RuntimeError("temporary Slack outage")
                return await super().post(record, message, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                slack_bot_token="test-slack",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-SLACK-RETRY",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-slack-retry",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: retry delivery",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Retry Slack only",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Build once",
                            acceptance=["Evidence accepted"],
                        )
                    ],
                    final_acceptance=["Slack retry is bounded"],
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            notifier = FailTwice()
            engine = OrchestrationEngine(settings, store)
            engine.notifier = notifier

            opened = await engine._ensure_collaboration_thread(task)

            persisted = store.get(task.task_id)
            self.assertTrue(opened)
            self.assertEqual(len(notifier.attempt_ids), 3)
            self.assertEqual(len(set(notifier.attempt_ids)), 1)
            self.assertEqual(len(persisted.collaboration_delivery_errors), 2)
            self.assertFalse(persisted.collaboration_fallback_active)

    async def test_three_slack_failures_fall_back_without_repeating_paid_work(self):
        class CountingDirector:
            def __init__(self):
                self.review_calls = 0

            async def review(self, record, result):
                self.review_calls += 1
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Accepted once.",
                    evidence=["focused evidence"],
                )

        class FailEveryResultPost(RecordingNotifier):
            def __init__(self):
                super().__init__()
                self.result_attempt_ids: list[str] = []

            async def post(self, record, message, **kwargs):
                if "WORK_RESULT" in message:
                    self.result_attempt_ids.append(kwargs["client_msg_id"])
                    raise RuntimeError("temporary Slack outage")
                return await super().post(record, message, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                slack_bot_token="test-slack",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            plan = DirectorPlan(
                project="demo",
                normalized_outcome="Build once and report once",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CLAUDE,
                        task="Build once",
                        acceptance=["Evidence accepted"],
                    )
                ],
                final_acceptance=["No duplicate paid work"],
            )
            task = TaskRecord(
                task_id="DIR-SLACK-RECOVERY",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-slack-recovery",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: recover delivery",
                ),
                plan=plan,
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            adapter = CompletingAdapter(ProviderName.CLAUDE)
            director = CountingDirector()
            notifier = FailEveryResultPost()
            engine = OrchestrationEngine(settings, store)
            engine.providers = {ProviderName.CLAUDE: adapter}
            engine.director = director
            engine.notifier = notifier

            await engine.run(task.task_id)
            completed = store.get(task.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE, completed.message)
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(director.review_calls, 1)
            self.assertEqual(len(completed.assignment_outcomes), 1)
            self.assertEqual(len(notifier.result_attempt_ids), 3)
            self.assertEqual(len(set(notifier.result_attempt_ids)), 1)
            self.assertEqual(len(completed.collaboration_delivery_errors), 3)
            self.assertTrue(completed.collaboration_fallback_active)
            self.assertIn("durable SQLite/API", completed.collaboration_fallback_reason)
            self.assertEqual(
                [turn.phase for turn in completed.collaboration_turns],
                ["delegation", "work_result", "completion"],
            )

            post_count = len(notifier.posts)
            await engine.run(task.task_id)
            resumed = store.get(task.task_id)
            self.assertEqual(resumed.status, TaskStatus.DONE)
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(director.review_calls, 1)
            self.assertEqual(len(notifier.result_attempt_ids), 3)
            self.assertEqual(len(notifier.posts), post_count)

    async def test_runtime_uses_one_thread_structured_turns_and_deterministic_tally(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                cursor_api_key="test-cursor",
                slack_bot_token="test-slack",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=4,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            store = TaskStore(settings.sqlite_path)
            task = TaskRecord(
                task_id="DIR-DEBATE",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-debate",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: choose a direction",
                ),
                plan=debate_plan(),
            )
            store.create(task)
            claude = CompletingAdapter(ProviderName.CLAUDE)
            cursor = CompletingAdapter(ProviderName.CURSOR)
            notifier = RecordingNotifier()
            engine = OrchestrationEngine(settings, store)
            engine.providers = {
                ProviderName.CLAUDE: claude,
                ProviderName.CURSOR: cursor,
            }
            engine.director = DebateDirector()
            engine.notifier = notifier

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE, completed.error)
            self.assertEqual(completed.collaboration_thread_ts, "1700000000.000001")
            self.assertEqual(len(completed.collaboration_turns), 10)
            self.assertEqual(completed.debate_tally["option_weights"], {"A": 2.5, "B": 3.5})
            self.assertFalse(completed.debate_tally["authorizes_external_action"])
            roots = [item for item in notifier.posts if item.get("root_message")]
            self.assertEqual(len(roots), 1)
            debate_replies = [
                item
                for item in notifier.posts
                if item.get("thread_ts") == completed.collaboration_thread_ts
            ]
            self.assertEqual(len(debate_replies), 10)
            self.assertTrue(all("client_msg_id" in item for item in [*roots, *debate_replies]))
            self.assertNotIn("bounded result from", "\n".join(item["message"] for item in debate_replies))
            self.assertEqual(claude.contexts[0].count('"dependency_outcomes"'), 1)
            self.assertIn('"dependency_outcomes": []', claude.contexts[0])
            self.assertIn('"dependency_outcomes": []', cursor.contexts[0])
            self.assertIn('"assignment_sequence": 1', claude.contexts[1])
            self.assertIn('"assignment_sequence": 2', claude.contexts[1])

    async def test_material_tie_stops_for_owner_and_never_authorizes_action(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(), sqlite_path=str(Path(directory) / "tasks.sqlite3")
            )
            store = TaskStore(settings.sqlite_path)
            plan = debate_plan(include_gemini=True, material_risk=True)
            task = TaskRecord(
                task_id="DIR-TIE",
                status=TaskStatus.RUNNING,
                intake=SlackIntake(
                    source_event_id="evt-tie",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: material choice",
                ),
                plan=plan,
                collaboration_turns=[
                    CollaborationTurn(
                        assignment_sequence=4,
                        provider=ProviderName.CLAUDE,
                        phase="rebuttal",
                        correlation_id="claude",
                        run_id="claude-run",
                        position="A",
                        confidence=0.8,
                        vote_option="A",
                    ),
                    CollaborationTurn(
                        assignment_sequence=5,
                        provider=ProviderName.CURSOR,
                        phase="rebuttal",
                        correlation_id="cursor",
                        run_id="cursor-run",
                        position="B",
                        confidence=0.8,
                        vote_option="B",
                    ),
                    CollaborationTurn(
                        assignment_sequence=6,
                        provider=ProviderName.GEMINI,
                        phase="rebuttal",
                        correlation_id="gemini",
                        run_id="gemini-run",
                        position="A",
                        confidence=0.8,
                        vote_option="A",
                    ),
                ],
            )
            store.create(task)
            engine = OrchestrationEngine(settings, store)
            engine.director = DebateDirector()

            continued = await engine._finalize_debate(task, plan.assignments[-1])

            self.assertFalse(continued)
            stopped = store.get(task.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.debate_tally["resolution"], "owner_decision_required")
            self.assertFalse(stopped.debate_tally["authorizes_external_action"])


if __name__ == "__main__":
    unittest.main()
