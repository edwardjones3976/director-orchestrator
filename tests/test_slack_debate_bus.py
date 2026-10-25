# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 04:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:43 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:19 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | BEGIN]]
# [[Cursor | Cursor Grok 4.6 | 2026-08-24 | 19:32 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:40 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:25 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:58 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:30 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:02 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:37 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:44 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.approvals import (
    StaleApproval,
    record_task_effect_observation,
    reserve_task_effect,
    reset_for_owner_clarification,
    task_effect_key,
)
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
    DebateActorRole,
    DebateCounterBias,
    DebateContribution,
    DebateInitialPosition,
    DebatePlan,
    DebateSynthesis,
    DebateTieJudgment,
    CollaborationTurn,
    DirectorPlan,
    DiscussionPhase,
    IntakeAuthentication,
    MaterialDecisionDimension,
    ProviderName,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
    TieJudgmentRubric,
    TieRubricApplication,
    TieRubricCriterion,
)
from director_orchestrator.policy import (
    MINIMUM_OPENAI_REVIEW_COST_USD,
    debate_tie_is_material,
    debate_tie_materiality_reasons,
    enforce_plan,
    estimated_plan_cost_usd,
    execution_policy_errors,
)
from director_orchestrator.store import TaskStore
from director_orchestrator.team_charter import AdvisoryVoteTally


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
    effect_key: str | None = None,
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
            effect_key=effect_key,
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
    if record.plan is None:
        raise AssertionError("a plan is required before seeding its authority")
    if record.plan_generation == 0:
        record.plan_generation = 1
    target_generation = record.plan_generation
    record.plan_generation = target_generation - 1
    effect_key = task_effect_key(
        record,
        phase="plan",
        identity={
            "intake": hashlib.sha256(
                record.intake.model_dump_json().encode("utf-8")
            ).hexdigest(),
            "target_generation": target_generation,
        },
    )
    reserve_task_effect(
        record,
        effect_key=effect_key,
        kind="director_intent",
        phase="plan",
        provider=ProviderName.OPENAI,
    )
    record.plan_generation = target_generation
    interaction_id = (
        f"{record.task_id}:generation-{record.plan_generation}:openai-plan"
    )
    verified_plan_event = seed_verified_interaction(
        record,
        interaction_id=interaction_id,
        provider=ProviderName.OPENAI,
        phase=ProviderInteractionPhase.PLAN,
        result=record.plan,
        effect_key=effect_key,
    )
    record.plan_interaction_id = interaction_id
    binding = OrchestrationEngine._plan_materialization_binding(
        record,
        verified_event_digest=verified_plan_event.event_digest,
        verified_result_digest=(
            verified_plan_event.transport_evidence.result_digest or ""
        ),
        effect_key=effect_key,
    )
    record_task_effect_observation(
        record,
        effect_key=effect_key,
        intent_kind="director_intent",
        observation_kind="director_observation",
        phase="plan",
        outcome="completed",
        run_id=binding,
    )


def bind_reviewed_contribution(record: TaskRecord, turn: CollaborationTurn) -> None:
    contribution = DebateContribution(
        position=turn.position,
        evidence=turn.evidence,
        contradictions=turn.contradictions,
        confidence=turn.confidence,
        vote_option=turn.vote_option,
        abstain=turn.abstain,
    )
    decision = ReviewDecision(
        verdict=ReviewVerdict.ACCEPT,
        summary="Exact debate contribution accepted.",
        evidence=["exact structured contribution"],
        debate_contribution=contribution,
    )
    interaction_id = f"{record.task_id}:openai-review:{turn.assignment_sequence}"
    verified = seed_verified_interaction(
        record,
        interaction_id=interaction_id,
        provider=ProviderName.OPENAI,
        phase=ProviderInteractionPhase.ACCEPTANCE_REVIEW,
        result=decision,
    )
    turn.verified_debate_contribution = contribution
    turn.director_review = decision
    turn.director_review_interaction_id = interaction_id
    turn.verified_director_review_event_digest = verified.event_digest
    turn.director_review_result_digest = canonical_content_digest(
        interaction_result_text(decision)
    )


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
            neutral_framing=(
                "Compare the evidence for A and B without presuming either is correct; "
                "retain a bounded option to gather more evidence."
            ),
            alternative_option="Gather more evidence",
            openai_initial_position=DebateInitialPosition(
                position="OpenAI preliminarily favors B while preserving uncertainty.",
                rationale="The limited plan-time evidence gives B a provisional edge.",
                evidence=["Plan-time evidence only"],
                contradictions=["Specialist evidence has not been collected"],
                assumptions=["The bounded fixture represents the expected decision"],
                material_risks=[],
                unknowns=["Independent specialist results are not available yet"],
                conditions_to_change=["Verified specialist evidence favors another option"],
                confidence=0.55,
                provisional_option="B",
            ),
            tie_judgment_rubric=TieJudgmentRubric(
                criteria=[
                    TieRubricCriterion(
                        criterion="evidence_quality",
                        evaluation_question="Which tied option has stronger verified evidence?",
                    ),
                    TieRubricCriterion(
                        criterion="reversibility",
                        evaluation_question="Which tied option is easier to reverse safely?",
                    ),
                ]
            ),
            material_dimensions=(
                [MaterialDecisionDimension.PROJECT_DIRECTION] if material_risk else []
            ),
            position_assignments=position_sequences,
            rebuttal_assignments=[item.sequence for item in rebuttals],
            vote_options=["A", "B", "Gather more evidence"],
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

    def test_trusted_debate_cost_floor_is_installed_before_budget_admission(self):
        plan = debate_plan()
        plan.debate = plan.debate.model_copy(
            update={"estimated_synthesis_cost_usd": 0.35}
        )
        guarded = enforce_plan(
            plan,
            "choose a direction",
            replace(
                self.settings,
                max_task_budget_usd=0.74,
                openai_debate_turn_estimated_cost_usd=0.20,
            ),
        )

        self.assertEqual(guarded.debate.estimated_counter_bias_cost_usd, 0.20)
        self.assertEqual(guarded.debate.estimated_synthesis_cost_usd, 0.35)
        self.assertEqual(guarded.debate.estimated_tie_judgment_cost_usd, 0.20)
        # 0.20 counter-bias + 0.35 synthesis + 0.20 tie judgment, plus the review
        # floor for each of the four debate assignments (4 x 0.01). Reserving zero
        # for those reviews would under-reserve the envelope by exactly four calls.
        self.assertAlmostEqual(
            estimated_plan_cost_usd(guarded),
            0.75 + 4 * MINIMUM_OPENAI_REVIEW_COST_USD,
        )
        self.assertIn("estimated cost exceeds task budget", guarded.approval_reasons)

    def test_runtime_reservation_applies_floor_to_legacy_zero_estimates(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                self.settings,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                max_task_budget_usd=1.0,
                openai_debate_turn_estimated_cost_usd=0.20,
            )
            store = TaskStore(settings.sqlite_path)
            task = TaskRecord(
                    task_id="DIR-DEBATE-COST-FLOOR",
                    status=TaskStatus.REVIEWING,
                    intake=SlackIntake(
                        source_event_id="evt-debate-cost-floor",
                        channel_id="C-ORACLE",
                        thread_ts="owner-thread",
                        user_id="U-OWNER",
                        text="DIRECTOR Project: demo Outcome: debate safely",
                    ),
                    plan=debate_plan(),
                    plan_generation=1,
                )
            seed_verified_plan(task)
            store.create(task)
            engine = OrchestrationEngine(settings, store)
            current = store.get("DIR-DEBATE-COST-FLOOR")

            reserved = engine._reserve_director_effect(
                current,
                phase="debate_counter_bias",
                identity={"phase": "counter-bias"},
                estimated_cost_usd=0,
            )
            self.assertIsNotNone(reserved)
            current, _ = reserved
            reserved = engine._reserve_director_effect(
                current,
                phase="debate_synthesis",
                identity={"phase": "synthesis"},
                estimated_cost_usd=0.35,
            )
            self.assertIsNotNone(reserved)
            current, _ = reserved

            debate_intents = [
                effect
                for effect in current.task_effect_ledger
                if effect.kind == "director_intent"
                and effect.phase in {"debate_counter_bias", "debate_synthesis"}
            ]
            self.assertEqual(
                [effect.estimated_cost_usd for effect in debate_intents],
                [0.20, 0.35],
            )
            self.assertAlmostEqual(current.cumulative_estimated_cost_usd, 0.55)

    def test_runtime_floor_cannot_reserve_beyond_task_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                self.settings,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
                max_task_budget_usd=0.19,
                openai_debate_turn_estimated_cost_usd=0.20,
            )
            store = TaskStore(settings.sqlite_path)
            task = TaskRecord(
                    task_id="DIR-DEBATE-COST-FLOOR-BLOCKED",
                    status=TaskStatus.REVIEWING,
                    intake=SlackIntake(
                        source_event_id="evt-debate-cost-floor-blocked",
                        channel_id="C-ORACLE",
                        thread_ts="owner-thread",
                        user_id="U-OWNER",
                        text="DIRECTOR Project: demo Outcome: debate safely",
                    ),
                    plan=debate_plan(),
                    plan_generation=1,
                )
            seed_verified_plan(task)
            store.create(task)
            engine = OrchestrationEngine(settings, store)

            with self.assertRaisesRegex(StaleApproval, "Cumulative task budget"):
                engine._reserve_director_effect(
                    store.get("DIR-DEBATE-COST-FLOOR-BLOCKED"),
                    phase="debate_tie_judgment",
                    identity={"phase": "tie-judgment"},
                    estimated_cost_usd=0,
                )
            stored = store.get("DIR-DEBATE-COST-FLOOR-BLOCKED")
            self.assertFalse(
                any(
                    effect.phase == "debate_tie_judgment"
                    for effect in stored.task_effect_ledger
                )
            )
            self.assertEqual(stored.cumulative_estimated_cost_usd, 0)

    def test_neutral_alternative_and_preliminary_option_must_be_configured(self):
        plan = debate_plan()
        plan.debate = plan.debate.model_copy(update={"alternative_option": None})
        self.assertEqual(execution_policy_errors(plan, self.settings), [])

        plan = debate_plan()
        plan.debate = plan.debate.model_copy(update={"alternative_option": "Hidden D"})
        errors = execution_policy_errors(plan, self.settings)
        self.assertIn(
            "debate alternative option must be one configured vote option", errors
        )

        plan = debate_plan()
        plan.debate = plan.debate.model_copy(
            update={
                "openai_initial_position": plan.debate.openai_initial_position.model_copy(
                    update={"provisional_option": "Hidden D"}
                )
            }
        )
        errors = execution_policy_errors(plan, self.settings)
        self.assertIn(
            "OpenAI preliminary position selected an unconfigured debate option",
            errors,
        )

    def test_legacy_debate_records_remain_readable_but_cannot_execute(self):
        payload = debate_plan().model_dump(mode="json")
        for field in (
            "neutral_framing",
            "alternative_option",
            "openai_initial_position",
            "tie_judgment_rubric",
            "material_dimensions",
        ):
            payload["debate"].pop(field)

        legacy = DirectorPlan.model_validate(payload)

        self.assertIsNone(legacy.debate.neutral_framing)
        self.assertIn(
            "debate must record a neutral framing",
            execution_policy_errors(legacy, self.settings),
        )
        self.assertIn(
            "debate must record OpenAI's independent initial position",
            execution_policy_errors(legacy, self.settings),
        )
        self.assertIn(
            "debate must predeclare an ordinary-tie judgment rubric",
            execution_policy_errors(legacy, self.settings),
        )
        self.assertIn(
            "debate must declare its material decision dimensions",
            execution_policy_errors(legacy, self.settings),
        )

        legacy_synthesis = DebateSynthesis.model_validate(
            {
                "decision": "Legacy decision",
                "rationale": "Legacy rationale",
                "dissent": [],
                "next_action": "Stop for migration",
                "counter_bias_analysis": "Old post-hoc field",
                "counter_bias_changed_decision": False,
                "openai_vote_option": "A",
                "openai_abstains": False,
                "confidence": 0.5,
            }
        )
        self.assertNotIn(
            "counter_bias_analysis", legacy_synthesis.model_dump(mode="json")
        )


class DebateTieMaterialityTests(unittest.TestCase):
    def test_ordinary_low_signal_tie_remains_non_material(self):
        plan = debate_plan()

        self.assertFalse(debate_tie_is_material(plan))
        self.assertEqual(debate_tie_materiality_reasons(plan), [])

    def test_model_may_escalate_an_otherwise_ordinary_tie(self):
        risk_flag = debate_plan(material_risk=True)
        declared = debate_plan()
        declared.debate = declared.debate.model_copy(
            update={"material_dimensions": [MaterialDecisionDimension.PRIVACY]}
        )

        self.assertTrue(debate_tie_is_material(risk_flag))
        self.assertIn("model escalated material_risk", debate_tie_materiality_reasons(risk_flag))
        self.assertTrue(debate_tie_is_material(declared))
        self.assertIn(
            "model declared material decision dimensions",
            debate_tie_materiality_reasons(declared),
        )

    def test_false_empty_model_flags_cannot_deescalate_trusted_signals(self):
        cases = {
            "high_risk": debate_plan().model_copy(update={"risk_level": RiskLevel.HIGH}),
            "approval_required": debate_plan().model_copy(
                update={"approval_required": True}
            ),
            "approval_reasons": debate_plan().model_copy(
                update={"approval_reasons": ["sensitive data"]}
            ),
            "assignment_approval": debate_plan().model_copy(
                update={
                    "assignments": [
                        debate_plan().assignments[0].model_copy(
                            update={"requires_approval": True}
                        ),
                        *debate_plan().assignments[1:],
                    ]
                }
            ),
            "draft_pr": debate_plan().model_copy(
                update={
                    "assignments": [
                        debate_plan().assignments[0].model_copy(
                            update={"allow_draft_pr": True}
                        ),
                        *debate_plan().assignments[1:],
                    ]
                }
            ),
        }
        for name, plan in cases.items():
            with self.subTest(name=name):
                self.assertFalse(plan.debate.material_risk)
                self.assertEqual(plan.debate.material_dimensions, [])
                self.assertTrue(debate_tie_is_material(plan))

    def test_omitted_malformed_or_contradictory_model_materiality_fails_closed(self):
        omitted = debate_plan()
        omitted.debate = omitted.debate.model_copy(update={"material_dimensions": None})
        malformed = debate_plan()
        malformed.debate = malformed.debate.model_construct(
            **{
                **malformed.debate.model_dump(),
                "material_dimensions": ["not-a-dimension"],
            }
        )
        contradictory = debate_plan().model_copy(update={"risk_level": RiskLevel.CRITICAL})
        contradictory.debate = contradictory.debate.model_copy(
            update={
                "material_risk": False,
                "material_dimensions": [],
            }
        )

        self.assertTrue(debate_tie_is_material(omitted))
        self.assertIn(
            "debate material dimensions were omitted",
            debate_tie_materiality_reasons(omitted),
        )
        self.assertTrue(debate_tie_is_material(malformed))
        self.assertIn(
            "debate material dimensions are malformed",
            debate_tie_materiality_reasons(malformed),
        )
        self.assertTrue(debate_tie_is_material(contradictory))
        self.assertIn("critical-risk plan", debate_tie_materiality_reasons(contradictory))

    def test_model_false_cannot_hide_safety_privacy_or_repository_scope(self):
        deploy = debate_plan()
        deploy.debate = deploy.debate.model_copy(
            update={"question": "Should the team deploy option A or option B to production?"}
        )
        privacy = debate_plan()
        two_repos = debate_plan()
        two_repos.assignments[0] = two_repos.assignments[0].model_copy(
            update={"repository": "example/alpha"}
        )
        two_repos.assignments[1] = two_repos.assignments[1].model_copy(
            update={"repository": "example/beta"}
        )

        self.assertTrue(debate_tie_is_material(deploy))
        self.assertTrue(
            any("production" in reason for reason in debate_tie_materiality_reasons(deploy))
        )
        self.assertTrue(
            debate_tie_is_material(
                privacy,
                source_text="DIRECTOR Project: demo Outcome: choose how to store medical records",
            )
        )
        self.assertTrue(debate_tie_is_material(two_repos))
        self.assertIn(
            "assignments span multiple repositories",
            debate_tie_materiality_reasons(two_repos),
        )


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
    def __init__(self, *, omit_position_fields: bool = False):
        self.omit_position_fields = omit_position_fields

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
                rationale=(
                    None
                    if self.omit_position_fields
                    and assignment.discussion_phase == DiscussionPhase.POSITION
                    else f"{assignment.agent.value} evaluated the bounded evidence."
                ),
                evidence=[f"evidence-{assignment.sequence}"],
                contradictions=["bounded disagreement"],
                assumptions=(
                    None
                    if self.omit_position_fields
                    and assignment.discussion_phase == DiscussionPhase.POSITION
                    else ["The bounded fixture is representative"]
                ),
                material_risks=(
                    None
                    if self.omit_position_fields
                    and assignment.discussion_phase == DiscussionPhase.POSITION
                    else []
                ),
                unknowns=(
                    None
                    if self.omit_position_fields
                    and assignment.discussion_phase == DiscussionPhase.POSITION
                    else ["No live environment evidence was supplied"]
                ),
                conditions_to_change=(
                    None
                    if self.omit_position_fields
                    and assignment.discussion_phase == DiscussionPhase.POSITION
                    else ["Contrary exact evidence is verified"]
                ),
                confidence=0.8,
                vote_option=option,
            ),
        )

    async def counter_bias_debate(self, record):
        return DebateCounterBias(
            strongest_counterargument="Claude's evidence provides the strongest case for A.",
            challenged_assumptions=["The preliminary OpenAI preference for B may anchor review"],
            disconfirming_evidence=["Claude produced contrary verified evidence"],
            alternatives_reconsidered=["A", "Gather more evidence"],
            independence_assessment=(
                "Specialist positions were authenticated before this moderator pass."
            ),
            recommendation_changed=False,
            provisional_recommendation="B",
            confidence=0.7,
        )

    async def synthesize_debate(
        self,
        record,
        counter_bias,
        *,
        counter_bias_interaction_id,
        counter_bias_event_digest,
    ):
        self.counter_bias_received = counter_bias
        self.counter_bias_interaction_id_received = counter_bias_interaction_id
        self.counter_bias_event_digest_received = counter_bias_event_digest
        return DebateSynthesis(
            decision="Choose option B",
            rationale="The combined Cursor and OpenAI evidence is stronger.",
            dissent=["Claude preferred option A"],
            next_action="Proceed to the next bounded assignment.",
            openai_vote_option="B",
            confidence=0.75,
        )

    async def judge_debate_tie(self, record, **kwargs):
        self.tie_judgment_calls = getattr(self, "tie_judgment_calls", 0) + 1
        return DebateTieJudgment(
            chosen_option="B",
            rubric_applications=[
                TieRubricApplication(
                    criterion="evidence_quality",
                    finding="B has the stronger exact verified evidence.",
                    favors_option="B",
                ),
                TieRubricApplication(
                    criterion="reversibility",
                    finding="Both are reversible; B has the narrower change.",
                    favors_option="B",
                ),
            ],
            rationale="The predeclared rubric favors B among the tied winners.",
            confidence=0.8,
        )


class SlackDebateRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_debate_plan_binds_target_generation_before_dispatch(self):
        class PlanningDebateDirector(DebateDirector):
            def __init__(self):
                super().__init__()
                self.plan_calls = 0

            async def plan(self, record):
                self.plan_calls += 1
                return debate_plan()

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                workload_policy_enabled=False,
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                cursor_api_key="test-cursor",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=4,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-GENERATED-DEBATE-BINDING",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-generated-debate-binding",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: choose a direction",
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            claude = CompletingAdapter(ProviderName.CLAUDE)
            cursor = CompletingAdapter(ProviderName.CURSOR)
            director = PlanningDebateDirector()
            engine = OrchestrationEngine(settings, store)
            engine.providers = {
                ProviderName.CLAUDE: claude,
                ProviderName.CURSOR: cursor,
            }
            engine.director = director
            engine.notifier = RecordingNotifier()

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE, completed.error)
            self.assertEqual(completed.plan_generation, 1)
            self.assertEqual(director.plan_calls, 1)
            self.assertEqual(claude.calls, 2)
            self.assertEqual(cursor.calls, 2)
            plan_projection = project_interaction(
                completed.provider_interaction_ledger,
                completed.plan_interaction_id,
            )
            plan_event = next(
                event
                for event in completed.provider_interaction_ledger
                if event.event_digest == plan_projection.latest_event_digest
            )
            self.assertEqual(plan_event.plan_generation, completed.plan_generation)
            self.assertEqual(
                [
                    (effect.kind, effect.plan_generation)
                    for effect in completed.task_effect_ledger
                    if effect.phase == "plan"
                ],
                [("director_intent", 0), ("director_observation", 1)],
            )

    async def test_generated_plan_mutation_fails_before_provider_dispatch(self):
        class PlanningDebateDirector(DebateDirector):
            async def plan(self, record):
                return debate_plan()

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                workload_policy_enabled=False,
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                cursor_api_key="test-cursor",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=4,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-MUTATED-DEBATE-BINDING",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-mutated-debate-binding",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: choose a direction",
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            claude = CompletingAdapter(ProviderName.CLAUDE)
            cursor = CompletingAdapter(ProviderName.CURSOR)
            engine = OrchestrationEngine(settings, store)
            engine.providers = {
                ProviderName.CLAUDE: claude,
                ProviderName.CURSOR: cursor,
            }
            engine.director = PlanningDebateDirector()
            engine.notifier = RecordingNotifier()
            publish_delegation = engine._publish_delegation

            async def mutate_before_delegation(record, assignment):
                record.plan.debate = record.plan.debate.model_copy(
                    update={"neutral_framing": "mutated after PLAN verification"}
                )
                return await publish_delegation(record, assignment)

            engine._publish_delegation = mutate_before_delegation

            await engine.run(task.task_id)

            stopped = store.get(task.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")
            self.assertIn("materialized plan changed", stopped.error)
            self.assertEqual(claude.calls, 0)
            self.assertEqual(cursor.calls, 0)
            self.assertEqual(stopped.assignment_outcomes, [])

    async def test_non_debate_plan_mutation_fails_before_slack_or_provider_dispatch(self):
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
                task_id="DIR-MUTATED-WORK-BEFORE-SLACK",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-mutated-work-before-slack",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: build one bounded feature",
                ),
                plan=DirectorPlan(
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
                            task="Implement the authenticated task",
                            acceptance=["Focused evidence passes"],
                        )
                    ],
                    final_acceptance=["Late mutation is rejected"],
                ),
            )
            seed_verified_plan(task)
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            adapter = CompletingAdapter(ProviderName.CLAUDE)
            notifier = RecordingNotifier()
            engine = OrchestrationEngine(settings, store)
            engine.providers = {ProviderName.CLAUDE: adapter}
            engine.notifier = notifier
            publish_delegation = engine._publish_delegation

            async def mutate_before_delegation(record, assignment):
                record.plan.assignments[0] = record.plan.assignments[0].model_copy(
                    update={"task": "Mutated after authenticated plan admission"}
                )
                return await publish_delegation(record, assignment)

            engine._publish_delegation = mutate_before_delegation

            await engine.run(task.task_id)

            stopped = store.get(task.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")
            self.assertIn("materialized plan changed", stopped.error)
            self.assertEqual(adapter.calls, 0)
            self.assertFalse(
                any("DELEGATION" in post["message"] for post in notifier.posts)
            )

    async def test_cas_reload_revalidates_plan_before_provider_dispatch(self):
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
                task_id="DIR-CAS-PLAN-REBIND",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-cas-plan-rebind",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: verify CAS authority",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Verify CAS authority",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Execute only the authenticated assignment",
                            acceptance=["Provider dispatch remains bound"],
                        )
                    ],
                    final_acceptance=["CAS reload rejects changed authority"],
                ),
            )
            seed_verified_plan(task)
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            adapter = CompletingAdapter(ProviderName.CLAUDE)
            engine = OrchestrationEngine(settings, store)
            engine.providers = {ProviderName.CLAUDE: adapter}
            engine.notifier = RecordingNotifier()
            mutate_if_version = store.mutate_if_version
            injected = False

            def mutate_current_before_reservation(task_id, expected_version, mutator, **kwargs):
                nonlocal injected

                if injected:
                    return mutate_if_version(
                        task_id, expected_version, mutator, **kwargs
                    )
                injected = True

                def mutate_after_reload(current):
                    current.plan.assignments[0] = current.plan.assignments[0].model_copy(
                        update={"task": "Changed inside the provider reservation CAS"}
                    )
                    mutator(current)

                return mutate_if_version(
                    task_id, expected_version, mutate_after_reload, **kwargs
                )

            store.mutate_if_version = mutate_current_before_reservation

            await engine.run(task.task_id)

            stopped = store.get(task.task_id)
            self.assertTrue(injected)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")
            self.assertIn("materialized plan changed", stopped.error)
            self.assertEqual(adapter.calls, 0)
            self.assertFalse(
                any(effect.kind == "provider_intent" for effect in stopped.task_effect_ledger)
            )

    async def test_reloaded_second_assignment_revalidates_plan_materialization(self):
        class AcceptingDirector:
            async def review(self, record, result):
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="First assignment accepted.",
                    evidence=["exact provider evidence"],
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
                task_id="DIR-RELOAD-SECOND-ASSIGNMENT",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-reload-second-assignment",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: run two bounded assignments",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Run two bounded assignments",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=2,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Complete stage one",
                            acceptance=["Stage one accepted"],
                        ),
                        Assignment(
                            sequence=2,
                            agent=ProviderName.CLAUDE,
                            task="Complete authenticated stage two",
                            acceptance=["Stage two accepted"],
                            depends_on=[1],
                        ),
                    ],
                    final_acceptance=["Each reloaded assignment remains bound"],
                ),
            )
            seed_verified_plan(task)
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            adapter = CompletingAdapter(ProviderName.CLAUDE)
            engine = OrchestrationEngine(settings, store)
            engine.providers = {ProviderName.CLAUDE: adapter}
            engine.director = AcceptingDirector()
            engine.notifier = RecordingNotifier()
            execute_assignment = engine._execute_assignment

            async def mutate_reloaded_second_assignment(record, assignment):
                if assignment.sequence == 2:
                    record.plan.assignments[1] = record.plan.assignments[1].model_copy(
                        update={"task": "Changed after the first assignment reload"}
                    )
                return await execute_assignment(record, assignment)

            engine._execute_assignment = mutate_reloaded_second_assignment

            await engine.run(task.task_id)

            stopped = store.get(task.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")
            self.assertIn("materialized plan changed", stopped.error)
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(
                [outcome.assignment_sequence for outcome in stopped.assignment_outcomes],
                [1],
            )

    async def test_review_reservation_revalidates_plan_after_provider_result(self):
        class CountingDirector:
            def __init__(self):
                self.review_calls = 0

            async def review(self, record, result):
                self.review_calls += 1
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Result accepted.",
                    evidence=["exact provider evidence"],
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
                task_id="DIR-REVIEW-PLAN-REBIND",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-review-plan-rebind",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: review a bounded result",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Review a bounded result",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Return one bounded result",
                            acceptance=["Review remains plan-bound"],
                        )
                    ],
                    final_acceptance=["Review cannot use changed plan authority"],
                ),
            )
            seed_verified_plan(task)
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            adapter = CompletingAdapter(ProviderName.CLAUDE)
            director = CountingDirector()
            engine = OrchestrationEngine(settings, store)
            engine.providers = {ProviderName.CLAUDE: adapter}
            engine.director = director
            engine.notifier = RecordingNotifier()
            reserve_director_effect = engine._reserve_director_effect
            injected = False

            def mutate_before_review_reservation(record, **kwargs):
                nonlocal injected

                if kwargs["phase"] == "acceptance_review" and not injected:
                    injected = True
                    record.plan.normalized_outcome = (
                        "Changed after provider result but before Director review"
                    )
                    store.save(record)
                return reserve_director_effect(record, **kwargs)

            engine._reserve_director_effect = mutate_before_review_reservation

            await engine.run(task.task_id)

            stopped = store.get(task.task_id)
            self.assertTrue(injected)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")
            self.assertIn("materialized plan changed", stopped.error)
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(director.review_calls, 0)
            self.assertFalse(
                any(
                    effect.phase == "acceptance_review"
                    for effect in stopped.task_effect_ledger
                )
            )

    async def test_new_plan_budget_reserves_all_debate_control_costs(self):
        class PlanningDirector:
            async def plan(self, record):
                plan = debate_plan()
                plan.debate = plan.debate.model_copy(
                    update={
                        "estimated_counter_bias_cost_usd": 0.30,
                        "estimated_synthesis_cost_usd": 0.20,
                    }
                )
                return plan

        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                workload_policy_enabled=False,
                allow_paid_runs=True,
                openai_api_key="test-openai",
                openai_plan_estimated_cost_usd=0.10,
                max_task_budget_usd=0.55,
                max_agent_hops=4,
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-DEBATE-PROSPECTIVE-BUDGET",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-debate-prospective-budget",
                    channel_id="C-ORACLE",
                    thread_ts="owner-thread",
                    user_id="U-OWNER",
                    text="DIRECTOR Project: demo Outcome: choose a direction",
                ),
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            engine = OrchestrationEngine(settings, store)
            engine.director = PlanningDirector()
            engine.notifier = RecordingNotifier()

            await engine.run(task.task_id)

            blocked = store.get(task.task_id)
            self.assertEqual(blocked.status, TaskStatus.BLOCKED)
            self.assertIn("Cumulative task budget blocks", blocked.message)
            # [[Codex | GPT-5.6 | 2026-08-25 | 00:44 UTC | BEGIN]]
            # The positive review floor reserves four acceptance passes here:
            # two assignments times the initial pass plus one possible revision.
            self.assertIn("new plan $0.59", blocked.message)
            # [[Codex | GPT-5.6 | 2026-08-25 | 00:44 UTC | END]]
            self.assertAlmostEqual(blocked.cumulative_estimated_cost_usd, 0.10)

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
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
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
            root_post = next(post for post in notifier.posts if post.get("root_message"))
            result_post = next(post for post in notifier.posts if "WORK_RESULT" in post["message"])
            with self.subTest(surface="collaboration roster"):
                self.assertIn("claude (Claude)=assignment-1", root_post["message"])
                self.assertNotIn("(None)", root_post["message"])
            with self.subTest(surface="provider result attribution"):
                self.assertEqual(result_post["contributor"], "claude (Claude)")
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
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
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
            self.assertEqual(adapter.calls, 0)
            self.assertEqual(len(stopped.assignment_outcomes), 0)

    async def test_dry_run_preview_recovers_after_missing_key_then_authenticates_live_replan(self):
        class FreshPlanningDirector:
            def __init__(self):
                self.plan_calls = 0

            async def plan(self, record):
                self.plan_calls += 1
                return DirectorPlan(
                    project="demo",
                    normalized_outcome="Build one bounded feature after owner confirmation",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    owner_question="Confirm the newly authenticated live plan.",
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Build only after the new plan is confirmed",
                            acceptance=["Authenticated plan is preserved"],
                        )
                    ],
                    final_acceptance=["Preview authority was not reused"],
                )

            async def review(self, record, result):
                raise AssertionError("owner-question replan must stop before review")

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            dry_settings = replace(
                Settings(),
                workload_policy_enabled=False,
                allow_paid_runs=False,
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=database,
            )
            task = TaskRecord(
                task_id="DIR-DRY-RUN-CANNOT-GO-LIVE",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-dry-run-cannot-go-live",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: build one bounded feature",
                ),
            )
            store = TaskStore(database)
            store.create(task)
            dry_engine = OrchestrationEngine(dry_settings, store)
            dry_engine.notifier = RecordingNotifier()
            await dry_engine.run(task.task_id)
            preview = store.get(task.task_id)
            self.assertIsNotNone(preview.plan)
            self.assertIsNone(preview.plan_interaction_id)

            missing_key_engine = OrchestrationEngine(
                replace(
                    dry_settings,
                    allow_paid_runs=True,
                    openai_api_key="",
                ),
                store,
            )
            missing_key_engine.notifier = RecordingNotifier()
            await missing_key_engine.run(task.task_id)
            blocked = store.get(task.task_id)
            self.assertEqual(blocked.status, TaskStatus.BLOCKED)
            self.assertEqual(blocked.message, "OPENAI_API_KEY is not configured")
            self.assertEqual(blocked.plan_generation, preview.plan_generation)
            self.assertIsNone(blocked.plan_interaction_id)

            live_settings = replace(
                dry_settings,
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
            )
            adapter = CompletingAdapter(ProviderName.CLAUDE)
            director = FreshPlanningDirector()
            live_engine = OrchestrationEngine(live_settings, store)
            live_engine.providers = {ProviderName.CLAUDE: adapter}
            live_engine.director = director
            live_engine.notifier = RecordingNotifier()

            await live_engine.run(task.task_id)

            stopped = store.get(task.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.message, "Confirm the newly authenticated live plan.")
            self.assertIsNone(stopped.error)
            self.assertEqual(director.plan_calls, 1)
            self.assertIsNotNone(stopped.plan_interaction_id)
            self.assertEqual(stopped.plan_generation, preview.plan_generation + 1)
            plan_projection = project_interaction(
                stopped.provider_interaction_ledger, stopped.plan_interaction_id
            )
            self.assertEqual(plan_projection.state, ProviderInteractionState.VERIFIED)
            self.assertEqual(adapter.calls, 0)
            self.assertEqual(stopped.assignment_outcomes, [])

    def test_reset_and_replan_namespaces_debate_correlations_by_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(), sqlite_path=str(Path(directory) / "tasks.sqlite3")
            )
            task = TaskRecord(
                task_id="DIR-DEBATE-CORRELATION-RESET",
                status=TaskStatus.RUNNING,
                intake=SlackIntake(
                    source_event_id="evt-debate-correlation-reset",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: compare two options",
                ),
                plan=debate_plan(),
                plan_generation=1,
            )
            seed_verified_plan(task)
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            engine = OrchestrationEngine(settings, store)
            first_turn_ids = {
                turn.correlation_id
                for turn in engine._ensure_openai_debate_preamble_records(task)
            }
            first_role_ids = {
                engine._generation_correlation_id(task, role)
                for role in (
                    "debate-moderator",
                    "openai-initial-position",
                    "provider-rebuttal",
                    "debate-counter-bias",
                    "openai-final-vote",
                    "debate-synthesis",
                    "debate-tie-judgment",
                )
            }

            reset_for_owner_clarification(task)
            task.plan = debate_plan()
            task.plan_generation += 1
            task.status = TaskStatus.RUNNING
            seed_verified_plan(task)
            second_turn_ids = {
                turn.correlation_id
                for turn in engine._ensure_openai_debate_preamble_records(task)
            }
            second_role_ids = {
                engine._generation_correlation_id(task, role)
                for role in (
                    "debate-moderator",
                    "openai-initial-position",
                    "provider-rebuttal",
                    "debate-counter-bias",
                    "openai-final-vote",
                    "debate-synthesis",
                    "debate-tie-judgment",
                )
            }

            self.assertTrue(first_turn_ids.isdisjoint(second_turn_ids))
            self.assertTrue(first_role_ids.isdisjoint(second_role_ids))
            self.assertTrue(all(":generation-1:" in item for item in first_role_ids))
            self.assertTrue(all(":generation-2:" in item for item in second_role_ids))

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
            seed_verified_plan(task)
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
            seed_verified_plan(task)
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

    async def test_default_slack_client_id_is_distinct_across_plan_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                slack_bot_token="test-slack",
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-SLACK-GENERATION-ID",
                status=TaskStatus.OWNER_DECISION_REQUIRED,
                intake=SlackIntake(
                    source_event_id="evt-slack-generation-id",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: generation-safe status",
                ),
                plan_generation=1,
            )
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            notifier = RecordingNotifier()
            engine = OrchestrationEngine(settings, store)
            engine.notifier = notifier

            await engine._deliver_collaboration_message(task, "same owner status")
            next_generation = store.get(task.task_id)
            next_generation.plan_generation = 2
            store.save(next_generation)
            await engine._deliver_collaboration_message(
                next_generation, "same owner status"
            )

            client_ids = [post["client_msg_id"] for post in notifier.posts]
            persisted = store.get(task.task_id)
            slack_intents = [
                effect
                for effect in persisted.task_effect_ledger
                if effect.kind == "slack_intent"
            ]
            self.assertEqual(len(client_ids), 2)
            self.assertNotEqual(client_ids[0], client_ids[1])
            self.assertEqual(
                [effect.plan_generation for effect in slack_intents], [1, 2]
            )
            self.assertEqual(len({effect.effect_key for effect in slack_intents}), 2)

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
            seed_verified_plan(task)
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            notifier = RecordingNotifier()
            first_engine = OrchestrationEngine(settings, store)
            first_engine.notifier = notifier
            root_client_id = first_engine._slack_client_id(
                first_engine._generation_correlation_id(task, "collaboration-root")
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

    async def test_five_slack_failures_fall_back_without_repeating_paid_work(self):
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
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
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
            self.assertEqual(len(notifier.result_attempt_ids), 5)
            self.assertEqual(len(set(notifier.result_attempt_ids)), 1)
            self.assertEqual(len(completed.collaboration_delivery_errors), 5)
            self.assertEqual(
                [
                    error.split(" ", 2)[1]
                    for error in completed.collaboration_delivery_errors
                ],
                [f"attempt={ordinal}/5" for ordinal in range(1, 6)],
            )
            self.assertTrue(completed.collaboration_fallback_active)
            self.assertIn(
                "Slack was unavailable after five bounded idempotent attempts",
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
            self.assertEqual(len(notifier.result_attempt_ids), 5)
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
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
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
            director = DebateDirector()
            engine.director = director
            engine.notifier = notifier

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(completed.status, TaskStatus.DONE, completed.error)
            self.assertEqual(completed.collaboration_thread_ts, "1700000000.000001")
            self.assertEqual(len(completed.collaboration_turns), 14)
            self.assertEqual(completed.debate_tally["option_weights"], {"A": 2.0, "B": 4.0})
            self.assertFalse(completed.debate_tally["authorizes_external_action"])
            self.assertIsNotNone(completed.debate_counter_bias)
            self.assertNotEqual(
                completed.debate_counter_bias_interaction_id,
                completed.debate_synthesis_interaction_id,
            )
            counter_projection = project_interaction(
                completed.provider_interaction_ledger,
                completed.debate_counter_bias_interaction_id,
            )
            synthesis_projection = project_interaction(
                completed.provider_interaction_ledger,
                completed.debate_synthesis_interaction_id,
            )
            self.assertEqual(counter_projection.state, ProviderInteractionState.VERIFIED)
            self.assertEqual(synthesis_projection.state, ProviderInteractionState.VERIFIED)
            self.assertEqual(director.counter_bias_received, completed.debate_counter_bias)
            self.assertEqual(
                director.counter_bias_interaction_id_received,
                completed.debate_counter_bias_interaction_id,
            )
            self.assertEqual(
                director.counter_bias_event_digest_received,
                counter_projection.latest_event_digest,
            )
            counter_event = next(
                event
                for event in completed.provider_interaction_ledger
                if event.event_digest == counter_projection.latest_event_digest
            )
            synthesis_event = next(
                event
                for event in completed.provider_interaction_ledger
                if event.event_digest == synthesis_projection.latest_event_digest
            )
            self.assertEqual(
                counter_event.phase, ProviderInteractionPhase.DEBATE_COUNTER_BIAS
            )
            self.assertEqual(
                synthesis_event.phase, ProviderInteractionPhase.DEBATE_SYNTHESIS
            )
            self.assertLess(counter_event.recorded_at, synthesis_event.recorded_at)
            openai_turns = [
                turn
                for turn in completed.collaboration_turns
                if turn.debate_role is not None
            ]
            self.assertEqual(
                [turn.debate_role for turn in openai_turns],
                [
                    DebateActorRole.OPENAI_MODERATOR,
                    DebateActorRole.OPENAI_PARTICIPANT,
                    DebateActorRole.OPENAI_MODERATOR,
                    DebateActorRole.OPENAI_PARTICIPANT,
                    DebateActorRole.OPENAI_DIRECTOR,
                ],
            )
            self.assertEqual(
                len(
                    [
                        turn
                        for turn in openai_turns
                        if turn.vote_option is not None and not turn.abstain
                    ]
                ),
                1,
            )
            director_turn = next(
                turn
                for turn in openai_turns
                if turn.debate_role == DebateActorRole.OPENAI_DIRECTOR
            )
            self.assertTrue(director_turn.abstain)
            self.assertIsNone(director_turn.vote_option)
            plan_projection = project_interaction(
                completed.provider_interaction_ledger,
                completed.plan_interaction_id,
            )
            self.assertTrue(
                all(
                    turn.interaction_id == completed.plan_interaction_id
                    and turn.verified_interaction_event_digest
                    == plan_projection.latest_event_digest
                    and turn.provider_result_digest
                    == plan_projection.verified_result_digest
                    for turn in openai_turns[:2]
                )
            )
            self.assertTrue(
                all(
                    turn.rationale
                    and turn.assumptions is not None
                    and turn.material_risks is not None
                    and turn.unknowns is not None
                    and turn.conditions_to_change is not None
                    for turn in completed.collaboration_turns
                    if turn.phase == "position"
                )
            )
            counter_turn = next(
                turn
                for turn in openai_turns
                if turn.phase == "counter_bias"
            )
            self.assertEqual(
                counter_turn.interaction_id,
                completed.debate_counter_bias_interaction_id,
            )
            self.assertEqual(
                counter_turn.verified_interaction_event_digest,
                counter_projection.latest_event_digest,
            )
            roots = [item for item in notifier.posts if item.get("root_message")]
            self.assertEqual(len(roots), 1)
            debate_replies = [
                item
                for item in notifier.posts
                if item.get("thread_ts") == completed.collaboration_thread_ts
            ]
            self.assertEqual(len(debate_replies), 14)
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
            self.assertEqual(
                [
                    (item.kind, item.outcome)
                    for item in completed.task_effect_ledger
                    if item.phase == "debate_counter_bias"
                ],
                [
                    ("director_intent", "reserved"),
                    ("director_observation", "completed"),
                ],
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

            tampered = next(
                turn
                for turn in completed.collaboration_turns
                if turn.provider == ProviderName.CURSOR and turn.phase == "rebuttal"
            )
            tampered.vote_option = "A"
            store.save(completed)
            continued = await engine._finalize_debate(
                completed, completed.plan.assignments[-1]
            )
            self.assertFalse(continued)
            rejected = store.get(completed.task_id)
            self.assertEqual(rejected.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(rejected.message, "COMMUNICATION_EVIDENCE_FAILURE")
            self.assertIn("persisted debate fields changed", rejected.error)

    async def test_position_without_full_initial_evidence_fields_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                claude_routine_url="https://claude.invalid/fire",
                claude_routine_token="test-claude",
                cursor_api_key="test-cursor",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                max_agent_hops=4,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            task = TaskRecord(
                task_id="DIR-MISSING-POSITION-FIELDS",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-missing-position-fields",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: verify initial evidence",
                ),
                plan=debate_plan(),
            )
            seed_verified_plan(task)
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            engine = OrchestrationEngine(settings, store)
            engine.providers = {
                ProviderName.CLAUDE: CompletingAdapter(ProviderName.CLAUDE),
                ProviderName.CURSOR: CompletingAdapter(ProviderName.CURSOR),
            }
            engine.director = DebateDirector(omit_position_fields=True)

            await engine.run(task.task_id)

            stopped = store.get(task.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")
            self.assertIn("lacks rationale, assumptions", stopped.error)
            self.assertEqual(stopped.next_assignment_index, 0)
            self.assertIsNone(stopped.debate_counter_bias)

    async def test_material_tie_stops_for_owner_and_never_authorizes_action(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(), sqlite_path=str(Path(directory) / "tasks.sqlite3")
            )
            store = TaskStore(settings.sqlite_path)
            plan = debate_plan(include_gemini=True, material_risk=False).model_copy(
                update={"risk_level": RiskLevel.HIGH}
            )
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
            seed_verified_plan(task)
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
                bind_reviewed_contribution(task, turn)
            store.create(task)
            engine = OrchestrationEngine(settings, store)
            director = DebateDirector()
            engine.director = director

            captured_votes = []

            def material_tie(votes, *, material_risk=False):
                captured_votes.extend(votes)
                self.assertTrue(material_risk)
                return AdvisoryVoteTally(
                    configured_weight_total=8.5,
                    cast_weight_total=6.0,
                    option_weights={"A": 3.0, "B": 3.0},
                    abstaining=[],
                    unavailable=[],
                    winning_options=["A", "B"],
                    resolution="owner_decision_required",
                    authorizes_external_action=False,
                )

            with patch(
                "director_orchestrator.engine.tally_advisory_votes",
                side_effect=material_tie,
            ):
                continued = await engine._finalize_debate(
                    task, plan.assignments[-1]
                )

            self.assertFalse(continued)
            stopped = store.get(task.task_id)
            self.assertEqual(stopped.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(stopped.debate_tally["resolution"], "owner_decision_required")
            self.assertFalse(stopped.debate_tally["authorizes_external_action"])
            self.assertEqual(
                [vote.member for vote in captured_votes],
                ["claude", "cursor", "antigravity", "openai"],
            )
            self.assertIsNone(stopped.debate_tie_judgment)
            self.assertEqual(getattr(director, "tie_judgment_calls", 0), 0)
            self.assertNotEqual(stopped.message, "COMMUNICATION_EVIDENCE_FAILURE")

    async def test_ordinary_tie_requires_documented_director_judgment(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(), sqlite_path=str(Path(directory) / "tasks.sqlite3")
            )
            plan = debate_plan()
            task = TaskRecord(
                task_id="DIR-ORDINARY-TIE",
                status=TaskStatus.RUNNING,
                intake=SlackIntake(
                    source_event_id="evt-ordinary-tie",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: ordinary technical choice",
                ),
                plan=plan,
                collaboration_turns=[
                    CollaborationTurn(
                        assignment_sequence=3,
                        provider=ProviderName.CLAUDE,
                        phase="rebuttal",
                        correlation_id="ordinary-claude",
                        run_id="claude-run",
                        position="A",
                        confidence=0.8,
                        vote_option="A",
                    ),
                    CollaborationTurn(
                        assignment_sequence=4,
                        provider=ProviderName.CURSOR,
                        phase="rebuttal",
                        correlation_id="ordinary-cursor",
                        run_id="cursor-run",
                        position="B",
                        confidence=0.8,
                        vote_option="B",
                    ),
                    # Unassigned text cannot manufacture participation or a vote.
                    CollaborationTurn(
                        assignment_sequence=4,
                        provider=ProviderName.GEMINI,
                        phase="rebuttal",
                        correlation_id="invented-gemini",
                        run_id="gemini-run",
                        position="A",
                        confidence=0.99,
                        vote_option="A",
                    ),
                ],
            )
            seed_verified_plan(task)
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
                if turn.provider in {ProviderName.CLAUDE, ProviderName.CURSOR}:
                    bind_reviewed_contribution(task, turn)
            store = TaskStore(settings.sqlite_path)
            store.create(task)
            engine = OrchestrationEngine(settings, store)
            director = DebateDirector()
            engine.director = director
            captured_votes = []

            def ordinary_tie(votes, *, material_risk=False):
                captured_votes.extend(votes)
                self.assertFalse(material_risk)
                return AdvisoryVoteTally(
                    configured_weight_total=8.5,
                    cast_weight_total=6.0,
                    option_weights={"A": 3.0, "B": 3.0},
                    abstaining=[],
                    unavailable=[],
                    winning_options=["A", "B"],
                    resolution="openai_director_judgment",
                    authorizes_external_action=False,
                )

            with patch(
                "director_orchestrator.engine.tally_advisory_votes",
                side_effect=ordinary_tie,
            ):
                continued = await engine._finalize_debate(
                    task, plan.assignments[-1]
                )

            self.assertTrue(continued)
            persisted = store.get(task.task_id)
            self.assertEqual(
                persisted.debate_tally,
                {
                    "configured_weight_total": 8.5,
                    "cast_weight_total": 6.0,
                    "option_weights": {"A": 3.0, "B": 3.0},
                    "abstaining": [],
                    "unavailable": [],
                    "winning_options": ["A", "B"],
                    "resolution": "openai_director_judgment",
                    "authorizes_external_action": False,
                },
            )
            self.assertEqual(
                [vote.member for vote in captured_votes],
                ["claude", "cursor", "openai"],
            )
            synthesis_turn = next(
                turn
                for turn in persisted.collaboration_turns
                if turn.phase == "synthesis"
            )
            tie_turn = next(
                turn
                for turn in persisted.collaboration_turns
                if turn.phase == "tie_judgment"
            )
            self.assertIn("Choose option B", synthesis_turn.position)
            self.assertTrue(synthesis_turn.abstain)
            self.assertIsNone(synthesis_turn.vote_option)
            self.assertTrue(tie_turn.abstain)
            self.assertIsNone(tie_turn.vote_option)
            self.assertEqual(persisted.debate_tie_judgment.chosen_option, "B")
            self.assertEqual(director.tie_judgment_calls, 1)
            synthesis_projection = project_interaction(
                persisted.provider_interaction_ledger,
                persisted.debate_synthesis_interaction_id,
            )
            judgment_projection = project_interaction(
                persisted.provider_interaction_ledger,
                persisted.debate_tie_judgment_interaction_id,
            )
            self.assertNotEqual(
                persisted.debate_synthesis_interaction_id,
                persisted.debate_tie_judgment_interaction_id,
            )
            self.assertEqual(
                tie_turn.verified_interaction_event_digest,
                judgment_projection.latest_event_digest,
            )
            synthesis_event = next(
                event
                for event in persisted.provider_interaction_ledger
                if event.event_digest == synthesis_projection.latest_event_digest
            )
            judgment_event = next(
                event
                for event in persisted.provider_interaction_ledger
                if event.event_digest == judgment_projection.latest_event_digest
            )
            self.assertLess(synthesis_event.recorded_at, judgment_event.recorded_at)


if __name__ == "__main__":
    unittest.main()

# [[Codex | GPT-5.6 | 2026-08-24 | 09:44 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:37 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:02 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:30 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:01 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:47 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:58 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:25 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:40 UTC | END]]
# [[Cursor | Cursor Grok 4.6 | 2026-08-24 | 19:32 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:19 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:43 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:16 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 04:13 UTC | END]]
