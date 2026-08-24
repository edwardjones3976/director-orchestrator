# [[Codex | GPT-5.6 | 2026-08-24 | 10:37 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:44 UTC | BEGIN]]
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.interactions import (
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    canonical_content_digest,
    project_interaction,
    require_verified_result,
    start_interaction,
    transition_interaction,
)
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


def interaction_result_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def seed_verified_interaction(
    record: TaskRecord,
    *,
    interaction_id: str,
    provider: ProviderName,
    phase: ProviderInteractionPhase,
    result: object,
    assignment_sequence: int | None = None,
):
    """Seed the same digest-bound VERIFIED lifecycle expected from live transports."""

    transport = {
        ProviderName.OPENAI: ProviderTransport.OPENAI_RESPONSES_API,
        ProviderName.CLAUDE: ProviderTransport.CLAUDE_ROUTINE,
        ProviderName.CURSOR: ProviderTransport.CURSOR_AGENTS_API,
        ProviderName.GEMINI: ProviderTransport.GEMINI_INTERACTIONS_API,
        ProviderName.PERPLEXITY: ProviderTransport.PERPLEXITY_API,
    }[provider]
    result_text = interaction_result_text(result)
    if not any(
        event.interaction_id == interaction_id
        for event in record.provider_interaction_ledger
    ):
        drafted_evidence = build_transport_evidence(
            transport=transport,
            source=TransportEvidenceSource.BACKEND_OBSERVATION,
            idempotency_key=f"{interaction_id}:drafted",
        )
        drafted = start_interaction(
            task_id=record.task_id,
            interaction_id=interaction_id,
            plan_generation=record.plan_generation,
            assignment_sequence=assignment_sequence,
            provider=provider.value,
            phase=phase,
            attempt=1,
            transport_evidence=drafted_evidence,
        )
        attempted = transition_interaction(
            [drafted],
            interaction_id=interaction_id,
            next_state=ProviderInteractionState.ATTEMPTED,
            transport_evidence=drafted_evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, drafted
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, attempted
        )

    projection = project_interaction(record.provider_interaction_ledger, interaction_id)
    path = [
        ProviderInteractionState.DRAFTED,
        ProviderInteractionState.ATTEMPTED,
        ProviderInteractionState.DISPATCHED,
        ProviderInteractionState.ACKNOWLEDGED,
        ProviderInteractionState.RESPONDED,
        ProviderInteractionState.COMPLETED,
        ProviderInteractionState.VERIFIED,
    ]
    for state in path[path.index(projection.state) + 1 :]:
        evidence = build_transport_evidence(
            transport=transport,
            source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
            idempotency_key=f"{interaction_id}:{state.value.lower()}",
            native_status=state.value.lower(),
            result=result_text,
        )
        event = transition_interaction(
            record.provider_interaction_ledger,
            interaction_id=interaction_id,
            next_state=state,
            transport_evidence=evidence,
        )
        record.provider_interaction_ledger = append_interaction_event(
            record.provider_interaction_ledger, event
        )
    return require_verified_result(
        record.provider_interaction_ledger,
        interaction_id=interaction_id,
        result=result_text,
    )


def seed_verified_plan(record: TaskRecord) -> None:
    interaction_id = f"{record.task_id}:openai-plan"
    seed_verified_interaction(
        record,
        interaction_id=interaction_id,
        provider=ProviderName.OPENAI,
        phase=ProviderInteractionPhase.PLAN,
        result=record.plan,
    )
    record.plan_interaction_id = interaction_id


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
            seed_verified_plan(task)
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
            self.assertEqual(
                [
                    item.kind
                    for item in completed.task_effect_ledger
                    if item.phase == "acceptance_review"
                ],
                ["director_intent", "director_observation"],
            )
            self.assertEqual(
                project_interaction(
                    completed.provider_interaction_ledger,
                    completed.plan_interaction_id,
                ).state,
                ProviderInteractionState.VERIFIED,
            )
            outcome = completed.assignment_outcomes[0]
            verified_result = require_verified_result(
                completed.provider_interaction_ledger,
                interaction_id=outcome.interaction_id,
                result=outcome.result,
            )
            self.assertEqual(
                outcome.verified_interaction_event_digest,
                verified_result.event_digest,
            )
            self.assertEqual(
                outcome.provider_result_digest,
                canonical_content_digest(outcome.result),
            )
            self.assertEqual(
                len(
                    {
                        event.interaction_id
                        for event in completed.provider_interaction_ledger
                        if event.phase == ProviderInteractionPhase.ACCEPTANCE_REVIEW
                        and event.state == ProviderInteractionState.VERIFIED
                    }
                ),
                1,
            )
            self.assertGreaterEqual(
                len(
                    [
                        item
                        for item in completed.task_effect_ledger
                        if item.phase == "slack_delivery"
                        and item.outcome == "delivered"
                    ]
                ),
                1,
            )
            post_count = len(notifier.posts)
            await engine.run(task.task_id)
            self.assertEqual(len(notifier.posts), post_count)

    async def test_missing_verified_plan_interaction_fails_closed(self):
        class AcceptingDirector:
            async def review(self, record, result):
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Provider result accepted.",
                    evidence=["bounded evidence"],
                )

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-MISSING-PLAN-EVIDENCE",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-missing-plan-evidence",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: verify the completion gate",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Verify the completion gate",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Return bounded evidence",
                            acceptance=["Evidence accepted"],
                        )
                    ],
                    final_acceptance=["Missing plan authority stops completion"],
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            adapter = CompletingAdapter(ProviderName.CLAUDE)
            engine = OrchestrationEngine(settings, store)
            engine.providers = {ProviderName.CLAUDE: adapter}
            engine.director = AcceptingDirector()

            await engine.run(task.task_id)

            stopped = store.get(task.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")
            self.assertIn("live plan lacks its Director interaction binding", stopped.error)
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(len(stopped.assignment_outcomes), 1)

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
            self.assertEqual(
                [
                    item.kind
                    for item in persisted.task_effect_ledger
                    if item.phase == "slack_delivery"
                ],
                ["slack_intent", "slack_observation"],
            )

    async def test_missing_slack_token_uses_truthful_non_delivery_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                slack_bot_token="",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-SLACK-NOT-CONFIGURED",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-slack-not-configured",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Record truthful Slack non-delivery",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Record truthful Slack non-delivery",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Do not dispatch in this delivery-only test",
                            acceptance=["Fallback is explicit"],
                        )
                    ],
                    final_acceptance=["No Slack delivery is claimed"],
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            notifier = RecordingNotifier()
            engine = OrchestrationEngine(settings, store)
            engine.notifier = notifier

            opened = await engine._ensure_collaboration_thread(task)

            persisted = store.get(task.task_id)
            self.assertTrue(opened)
            self.assertEqual(notifier.posts, [])
            self.assertTrue(persisted.collaboration_fallback_active)
            self.assertIn(
                "Slack transport is not configured; communication was not delivered",
                persisted.collaboration_fallback_reason,
            )
            self.assertFalse(persisted.collaboration_thread_opened)
            self.assertIsNone(persisted.collaboration_thread_ts)
            self.assertEqual(
                [
                    event
                    for event in persisted.task_effect_ledger
                    if event.phase == "slack_delivery"
                ],
                [],
            )

    async def test_delivered_slack_effect_replays_from_ledger_without_second_post(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                slack_bot_token="test-slack",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-SLACK-LEDGER-REPLAY",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-slack-ledger-replay",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Record one delivery",
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            notifier = RecordingNotifier()
            engine = OrchestrationEngine(settings, store)
            engine.notifier = notifier
            client_id = engine._slack_client_id(task.task_id, "stable-status")

            first = await engine._deliver_collaboration_message(
                task, "one safe status", client_msg_id=client_id
            )
            restarted_record = TaskStore(settings.sqlite_path).get(task.task_id)
            second_engine = OrchestrationEngine(settings, TaskStore(settings.sqlite_path))
            second_engine.notifier = notifier
            second = await second_engine._deliver_collaboration_message(
                restarted_record, "one safe status", client_msg_id=client_id
            )

            self.assertEqual(first[0], second[0])
            self.assertIsNone(second[1])
            self.assertEqual(len(notifier.posts), 1)
            self.assertEqual(
                [item.outcome for item in restarted_record.task_effect_ledger],
                ["reserved", "delivered"],
            )

    async def test_restart_preserves_atomic_thread_routing_and_delivers_later_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                slack_bot_token="test-slack",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CLAUDE,
                task="Publish one bounded delegation",
                acceptance=["Restart behavior is explicit"],
            )
            task = TaskRecord(
                task_id="DIR-SLACK-SPLIT-STATE",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-slack-split-state",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Recover a split Slack root delivery",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Recover Slack routing or stop visibly",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[assignment],
                    final_acceptance=["Later collaboration messages are not silently skipped"],
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            notifier = RecordingNotifier()
            first_engine = OrchestrationEngine(settings, store)
            first_engine.notifier = notifier
            root_client_id = first_engine._slack_client_id(
                task.task_id, "collaboration-root"
            )

            delivered, timestamp = await first_engine._deliver_collaboration_message(
                task,
                "simulated collaboration root",
                root_message=True,
                client_msg_id=root_client_id,
            )
            self.assertTrue(delivered)
            self.assertEqual(timestamp, "1700000000.000001")
            persisted = TaskStore(settings.sqlite_path).get(task.task_id)
            self.assertEqual(
                persisted.collaboration_thread_ts, "1700000000.000001"
            )
            self.assertTrue(persisted.collaboration_thread_opened)
            self.assertEqual(
                [
                    item.outcome
                    for item in persisted.task_effect_ledger
                    if item.phase == "slack_delivery"
                ],
                ["reserved", "delivered"],
            )

            restarted_store = TaskStore(settings.sqlite_path)
            restarted_record = restarted_store.get(task.task_id)
            restarted_engine = OrchestrationEngine(settings, restarted_store)
            restarted_engine.notifier = notifier
            self.assertTrue(
                await restarted_engine._ensure_collaboration_thread(restarted_record)
            )
            self.assertTrue(
                await restarted_engine._publish_delegation(
                    restarted_record, assignment
                )
            )

            recovered = restarted_store.get(task.task_id)
            self.assertEqual(
                recovered.collaboration_thread_ts, "1700000000.000001"
            )
            self.assertTrue(recovered.collaboration_thread_opened)
            self.assertFalse(recovered.collaboration_fallback_active)
            delegation = next(
                item
                for item in recovered.collaboration_turns
                if item.phase == "delegation"
            )
            self.assertEqual(delegation.slack_message_ts, "reply-2")
            self.assertEqual(len(notifier.posts), 2)
            self.assertTrue(notifier.posts[0]["root_message"])
            self.assertEqual(
                notifier.posts[1]["thread_ts"], "1700000000.000001"
            )

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
            seed_verified_plan(task)
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
            self.assertEqual(
                [
                    error.split(" ", 2)[1]
                    for error in completed.collaboration_delivery_errors
                ],
                ["attempt=1/3", "attempt=2/3", "attempt=3/3"],
            )
            self.assertTrue(completed.collaboration_fallback_active)
            self.assertIn(
                "Slack was unavailable after three bounded idempotent attempts",
                completed.collaboration_fallback_reason,
            )
            self.assertEqual(
                len(
                    [
                        item
                        for item in completed.task_effect_ledger
                        if item.phase == "slack_delivery"
                        and item.kind == "slack_observation"
                        and item.outcome == "ambiguous"
                    ]
                ),
                1,
            )
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
            seed_verified_plan(task)
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
            self.assertEqual(
                [
                    (item.kind, item.outcome)
                    for item in completed.task_effect_ledger
                    if item.phase == "debate_synthesis"
                ],
                [
                    ("director_intent", "reserved"),
                    ("director_observation", "completed"),
                ],
            )
            self.assertEqual(
                project_interaction(
                    completed.provider_interaction_ledger,
                    completed.debate_synthesis_interaction_id,
                ).state,
                ProviderInteractionState.VERIFIED,
            )
            synthesis_turn = next(
                turn for turn in completed.collaboration_turns if turn.phase == "synthesis"
            )
            synthesis_verified = require_verified_result(
                completed.provider_interaction_ledger,
                interaction_id=synthesis_turn.interaction_id,
                result=interaction_result_text(completed.debate_synthesis),
            )
            self.assertEqual(
                synthesis_turn.verified_interaction_event_digest,
                synthesis_verified.event_digest,
            )
            self.assertEqual(
                synthesis_turn.provider_result_digest,
                canonical_content_digest(interaction_result_text(completed.debate_synthesis)),
            )
            self.assertTrue(
                all(
                    outcome.interaction_id
                    and outcome.verified_interaction_event_digest
                    and outcome.provider_result_digest
                    for outcome in completed.assignment_outcomes
                )
            )
            self.assertGreaterEqual(completed.cumulative_effect_hops, 9)

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
            for turn in task.collaboration_turns:
                provider_result = f"verified {turn.provider.value} rebuttal"
                interaction_id = f"{task.task_id}:{turn.provider.value}:rebuttal"
                verified = seed_verified_interaction(
                    task,
                    interaction_id=interaction_id,
                    provider=turn.provider,
                    phase=ProviderInteractionPhase.DISPATCH,
                    result=provider_result,
                    assignment_sequence=turn.assignment_sequence,
                )
                turn.interaction_id = interaction_id
                turn.verified_interaction_event_digest = verified.event_digest
                turn.provider_result_digest = canonical_content_digest(provider_result)
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

# [[Codex | GPT-5.6 | 2026-08-24 | 09:44 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:37 UTC | END]]
