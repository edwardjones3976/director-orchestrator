# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:55 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 17:18 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 17:11 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:30 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:50 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:30 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:04 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:20 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from director_orchestrator.config import Settings
from director_orchestrator.approvals import (
    assignment_approval_digest,
    parse_approval_button_value,
    record_provider_effect_observation,
    record_task_effect_observation,
    reserve_provider_effect,
    reserve_task_effect,
)
from director_orchestrator.engine import (
    OrchestrationEngine,
    ProviderUnavailableError,
    SlackNotifier,
)
from director_orchestrator.interactions import (
    CommunicationEvidenceFailure,
    ProviderInteractionPhase,
    ProviderInteractionState,
    ProviderTransport,
    TransportEvidenceSource,
    append_interaction_event,
    build_transport_evidence,
    start_interaction,
    transition_interaction,
)
from director_orchestrator.providers import PostSubmitAmbiguityError, ProviderError
from director_orchestrator.models import (
    Assignment,
    AssignmentOutcome,
    DirectorPlan,
    IntakeAuthentication,
    ProviderName,
    ProviderContactAttempt,
    ProviderRun,
    ReviewDecision,
    ReviewVerdict,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.policy import enforce_plan, execution_policy_errors
from director_orchestrator.oracle_merge import authenticate_provider_review_artifact
from director_orchestrator.review_handoff import (
    bind_review_handoff,
    reconstruct_review_handoff,
    transitive_dependency_sequences,
)
from director_orchestrator.store import TaskStore
from director_orchestrator.team_charter import (
    TEAM_CHARTER_MAX_BYTES,
    canonical_team_charter_json,
    team_operating_charter,
)


def review_candidate_payload(*, producer: str = "cursor", material_authors=None) -> dict:
    payload = {
        "artifact_type": "director_review_candidate/v1",
        "assignment_id": "DIR-HANDOFF:1",
        "logical_producer": producer,
        "material_authors": material_authors or [producer],
        "repository": "example/demo",
        "pull_number": 17,
        "base_ref": "main",
        "base_sha": "a" * 40,
        "head_ref": "codex/change",
        "head_sha": "b" * 40,
        "implementation_actor": "cursor-agent",
        "changed_files": ["src/example.py", "tests/test_example.py"],
        "tests": ["python -m unittest: 2 passed"],
        "known_limitations": ["No deployment performed"],
        "acceptance_checks": ["Behavior is covered"],
        "security_context": ["No credential or permission changes"],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    payload["artifact_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return payload


def bind_verified_interaction(
    record: TaskRecord,
    run: ProviderRun,
    *,
    label: str,
    account_effect: bool = False,
) -> None:
    """Attach one backend-built VERIFIED transport chain to an exact test result."""

    interaction_id = (
        f"{record.task_id}:{run.assignment_sequence}:{run.provider.value}:{label}"
    )
    transport = {
        ProviderName.CURSOR: ProviderTransport.CURSOR_AGENTS_API,
        ProviderName.CLAUDE: ProviderTransport.CLAUDE_CALLBACK,
    }[run.provider]
    idempotency_key = f"{interaction_id}:idempotency"
    effect_key = None
    assignment = None
    if account_effect:
        assignment = next(
            item
            for item in record.plan.assignments
            if item.sequence == run.assignment_sequence
        )
        effect_key = reserve_provider_effect(
            record,
            assignment,
            phase="dispatch",
            max_task_budget_usd=1_000,
        )

    def evidence(
        state: ProviderInteractionState,
        *,
        authenticated: bool,
    ):
        return build_transport_evidence(
            transport=transport,
            source=(
                TransportEvidenceSource.AUTHENTICATED_PROVIDER
                if authenticated
                else TransportEvidenceSource.BACKEND_OBSERVATION
            ),
            idempotency_key=idempotency_key,
            request="bounded review fixture",
            response=("authenticated response" if authenticated else None),
            provider_run_id=(run.run_id if authenticated else None),
            native_status=run.status,
            http_status=(200 if authenticated else None),
            result=(
                run.result
                if state
                in {
                    ProviderInteractionState.RESPONDED,
                    ProviderInteractionState.COMPLETED,
                    ProviderInteractionState.VERIFIED,
                }
                else None
            ),
        )

    events = list(record.provider_interaction_ledger)
    drafted = start_interaction(
        task_id=record.task_id,
        interaction_id=interaction_id,
        plan_generation=record.plan_generation,
        assignment_sequence=run.assignment_sequence,
        provider=run.provider,
        phase=ProviderInteractionPhase.DISPATCH,
        attempt=1,
        transport_evidence=evidence(
            ProviderInteractionState.DRAFTED, authenticated=False
        ),
        effect_key=effect_key,
    )
    events = append_interaction_event(events, drafted)
    for state in (
        ProviderInteractionState.ATTEMPTED,
        ProviderInteractionState.DISPATCHED,
        ProviderInteractionState.ACKNOWLEDGED,
        ProviderInteractionState.RESPONDED,
        ProviderInteractionState.COMPLETED,
        ProviderInteractionState.VERIFIED,
    ):
        event = transition_interaction(
            events,
            interaction_id=interaction_id,
            next_state=state,
            transport_evidence=evidence(
                state,
                authenticated=state
                in {
                    ProviderInteractionState.ACKNOWLEDGED,
                    ProviderInteractionState.RESPONDED,
                    ProviderInteractionState.COMPLETED,
                    ProviderInteractionState.VERIFIED,
                },
            ),
        )
        events = append_interaction_event(events, event)
    record.provider_interaction_ledger = events
    run.interaction_id = interaction_id
    if effect_key is not None and assignment is not None:
        record_provider_effect_observation(
            record,
            assignment,
            effect_key=effect_key,
            phase="dispatch",
            outcome="submitted",
            run_id=run.run_id,
        )


def accepted_provider_outcome(
    record: TaskRecord, run: ProviderRun
) -> AssignmentOutcome:
    verified = next(
        item
        for item in reversed(record.provider_interaction_ledger)
        if item.interaction_id == run.interaction_id
        and item.state == ProviderInteractionState.VERIFIED
    )
    return AssignmentOutcome(
        assignment_sequence=run.assignment_sequence or 1,
        provider=run.provider,
        run_id=run.run_id,
        result=run.result or "candidate",
        review_summary="accepted",
        interaction_id=run.interaction_id,
        verified_interaction_event_digest=verified.event_digest,
        provider_result_digest=hashlib.sha256(
            (run.result or "").encode("utf-8")
        ).hexdigest(),
    )


def bind_authenticated_director_acceptance_review(
    record: TaskRecord,
    outcome: AssignmentOutcome,
) -> None:
    """Attach exact authenticated OpenAI acceptance evidence to one outcome."""

    interaction_id = (
        f"{record.task_id}:director-acceptance:{outcome.assignment_sequence}"
    )
    effect_key = hashlib.sha256(interaction_id.encode("utf-8")).hexdigest()
    review_result = "ACCEPT: exact producer outcome satisfies the bounded checks"
    reserve_task_effect(
        record,
        effect_key=effect_key,
        kind="director_intent",
        phase="acceptance_review",
        provider=ProviderName.OPENAI,
        estimated_cost_usd=0.1,
        hop_units=1,
        max_task_budget_usd=1_000,
    )
    backend_evidence = build_transport_evidence(
        transport=ProviderTransport.OPENAI_RESPONSES_API,
        source=TransportEvidenceSource.BACKEND_OBSERVATION,
        idempotency_key=effect_key,
    )
    authenticated_evidence = build_transport_evidence(
        transport=ProviderTransport.OPENAI_RESPONSES_API,
        source=TransportEvidenceSource.AUTHENTICATED_PROVIDER,
        idempotency_key=effect_key,
        result=review_result,
        native_status="completed",
    )
    drafted = start_interaction(
        task_id=record.task_id,
        interaction_id=interaction_id,
        plan_generation=record.plan_generation,
        assignment_sequence=None,
        provider=ProviderName.OPENAI,
        phase=ProviderInteractionPhase.ACCEPTANCE_REVIEW,
        attempt=1,
        transport_evidence=backend_evidence,
        effect_key=effect_key,
    )
    events = append_interaction_event(record.provider_interaction_ledger, drafted)
    attempted = transition_interaction(
        events,
        interaction_id=interaction_id,
        next_state=ProviderInteractionState.ATTEMPTED,
        transport_evidence=backend_evidence,
    )
    events = append_interaction_event(events, attempted)
    for state in (
        ProviderInteractionState.DISPATCHED,
        ProviderInteractionState.ACKNOWLEDGED,
        ProviderInteractionState.RESPONDED,
        ProviderInteractionState.COMPLETED,
        ProviderInteractionState.VERIFIED,
    ):
        event = transition_interaction(
            events,
            interaction_id=interaction_id,
            next_state=state,
            transport_evidence=authenticated_evidence,
        )
        events = append_interaction_event(events, event)
    record.provider_interaction_ledger = events
    outcome.director_review_interaction_id = interaction_id
    outcome.verified_director_review_event_digest = event.event_digest
    outcome.director_review_result_digest = (
        event.transport_evidence.result_digest
    )
    record_task_effect_observation(
        record,
        effect_key=effect_key,
        intent_kind="director_intent",
        observation_kind="director_observation",
        phase="acceptance_review",
        outcome="completed",
        run_id=f"sha256:{outcome.director_review_result_digest}",
    )


def seed_authenticated_normalized_plan(
    store: TaskStore,
    engine: OrchestrationEngine,
    record: TaskRecord,
) -> None:
    """Persist a fixture through the same authenticated PLAN path as production."""

    raw_plan = record.plan
    if raw_plan is None:
        raise AssertionError("live test fixture must provide a raw Director plan")
    record.plan = None
    store.create(record)
    persisted = store.get(record.task_id)
    if persisted is None:
        raise AssertionError("live test fixture was not persisted")

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
        estimated_cost_usd=engine.settings.openai_plan_estimated_cost_usd,
    )
    if reservation is None:
        raise AssertionError("live test fixture could not reserve Director planning")
    persisted, effect_key = reservation
    interaction_id = engine._begin_director_interaction(
        persisted,
        phase="plan",
        effect_key=effect_key,
        plan_generation=target_generation,
    )
    verified_plan_event = engine._complete_director_interaction(
        persisted,
        interaction_id=interaction_id,
        result=raw_plan,
    )
    persisted.plan = enforce_plan(
        raw_plan,
        persisted.intake.text,
        engine.settings,
    )
    persisted.plan_generation = target_generation
    persisted.plan_interaction_id = interaction_id
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


def bind_legacy_unaccounted_plan(
    engine: OrchestrationEngine,
    record: TaskRecord,
) -> None:
    """Bind exact authenticated PLAN evidence without production effect accounting."""

    if record.plan is None:
        raise AssertionError("legacy test fixture must provide a Director plan")
    interaction_id = f"{record.task_id}:legacy-plan"
    backend_evidence = build_transport_evidence(
        transport=ProviderTransport.OPENAI_RESPONSES_API,
        source=TransportEvidenceSource.BACKEND_OBSERVATION,
        idempotency_key=interaction_id,
    )
    drafted = start_interaction(
        task_id=record.task_id,
        interaction_id=interaction_id,
        plan_generation=record.plan_generation,
        assignment_sequence=None,
        provider=ProviderName.OPENAI,
        phase=ProviderInteractionPhase.PLAN,
        attempt=1,
        transport_evidence=backend_evidence,
    )
    attempted = transition_interaction(
        [drafted],
        interaction_id=interaction_id,
        next_state=ProviderInteractionState.ATTEMPTED,
        transport_evidence=backend_evidence,
    )
    record.provider_interaction_ledger = append_interaction_event(
        append_interaction_event([], drafted), attempted
    )
    engine._complete_director_interaction(
        record,
        interaction_id=interaction_id,
        result=record.plan,
    )
    record.plan_interaction_id = interaction_id


class PlanMaterializationAccountingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _task(task_id: str) -> TaskRecord:
        return TaskRecord(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
            intake=SlackIntake(
                source_event_id=f"evt-{task_id}",
                channel_id="C1",
                thread_ts="1.0",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: bounded work",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="Bounded work",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.CLAUDE,
                        task="Perform bounded work",
                        acceptance=["Return evidence"],
                    )
                ],
                final_acceptance=["Evidence accepted"],
            ),
        )

    async def test_exact_verified_plan_without_effect_accounting_cannot_dispatch(self):
        class RecordingAdapter:
            name = ProviderName.CLAUDE

            def __init__(self):
                self.dispatch_calls = 0

            async def dispatch(self, assignment, context):
                self.dispatch_calls += 1
                raise AssertionError("unaccounted legacy plan reached provider dispatch")

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
            store = TaskStore(settings.sqlite_path)
            task = self._task("DIR-UNACCOUNTED-PLAN")
            engine = OrchestrationEngine(settings, store)
            adapter = RecordingAdapter()
            engine.providers = {ProviderName.CLAUDE: adapter}
            bind_legacy_unaccounted_plan(engine, task)
            store.create(task)

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(adapter.dispatch_calls, 0)
            self.assertEqual(completed.status, TaskStatus.OWNER_DECISION_REQUIRED)
            self.assertEqual(completed.message, CommunicationEvidenceFailure.code)
            self.assertIn(
                "effect and materialization accounting",
                completed.error or "",
            )

    async def test_fully_accounted_authenticated_plan_remains_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                Settings(),
                allow_paid_runs=True,
                openai_api_key="test-openai",
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                allowed_starting_refs=("main",),
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            store = TaskStore(settings.sqlite_path)
            engine = OrchestrationEngine(settings, store)
            seed_authenticated_normalized_plan(
                store,
                engine,
                self._task("DIR-ACCOUNTED-PLAN"),
            )

            persisted = store.get("DIR-ACCOUNTED-PLAN")
            projection, event = engine._require_verified_plan_materialization(
                persisted
            )

            self.assertEqual(projection.state, ProviderInteractionState.VERIFIED)
            self.assertIsNotNone(event.effect_key)


class DirectorOperatingDirectiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.charter = team_operating_charter()
        self.directive = self.charter["director_operating_directive"]

    def test_director_duties_are_separate_from_direct_production_share(self):
        self.assertEqual(self.directive["director"], "openai")
        self.assertTrue(
            self.directive["duties_excluded_from_openai_direct_production_share"]
        )
        self.assertEqual(self.directive["duties"], [
            "plan",
            "decompose",
            "route",
            "coordinate",
            "synthesize",
            "acceptance_review",
            "review_handoff",
            "milestone_reporting",
        ])
        targets = self.directive["direct_workload_percent"]
        self.assertEqual(
            targets,
            {
                "claude": 60,
                "cursor": 28,
                "gemini": 7,
                "perplexity": 4,
                "openai": 1,
            },
        )
        self.assertEqual(sum(targets.values()), 100)
        self.assertEqual(targets["openai"], 1)
        self.assertEqual(
            self.directive["workload_lanes"]["perplexity"],
            "public_research_evidence_only",
        )
        self.assertEqual(
            self.directive["workload_lanes"]["openai"],
            "external_owner_authorized_only",
        )
        self.assertEqual(
            self.directive["openai_direct_production_boundary"],
            {
                "runtime_assignment_target_allowed": False,
                "surface": "owner_authorized_external_codex_openai",
                "counts": "authenticated_production_only",
                "review": "different_provider_exact_candidate",
                "candidate_change": "invalidate_evidence",
                "authority": "owner_scope_and_current_gates",
            },
        )

    def test_coordination_capacity_and_commit_reduction_are_narrowly_bounded(self):
        capacity = self.directive["coordination_percent"]
        self.assertEqual(capacity, {"productive": 95, "reserve": 5})
        self.assertEqual(sum(capacity.values()), 100)
        self.assertEqual(
            self.directive["reserve_use"],
            [
                "checks",
                "drift_correction",
                "contingencies",
                "evidence_reconciliation",
                "recovery",
                "process_improvement",
                "infrastructure_failure",
            ],
        )
        self.assertFalse(self.directive["reserve_is_planned_idle"])

        reduction = self.directive["avoidable_commit_reduction"]
        self.assertEqual(reduction["target_percent"], 33)
        self.assertEqual(reduction["scope"], "avoidable_micro_fixup_only")
        self.assertEqual(
            reduction["excludes"],
            ["security_safety", "evidence_restoration", "milestones", "recovery"],
        )
        self.assertEqual(
            reduction["baseline"],
            {
                "rolling": True,
                "comparable": True,
                "reset_when_unfavorable": False,
                "measurement_fields": [
                    "baseline_window",
                    "baseline_sample",
                    "baseline_count_or_rate",
                    "current_count_or_rate",
                    "absolute_delta",
                    "percentage_delta",
                    "exclusions",
                    "comparability_changes",
                ],
            },
        )

    def test_oversight_and_communication_labels_are_exactly_bounded(self):
        self.assertEqual(
            self.directive["repository_oversight"],
            {
                "mode": "read_only",
                "scope": [
                    "named_repository",
                    "refs",
                    "worktrees",
                    "diffs",
                    "artifacts",
                    "checks",
                    "reviews",
                    "security_evidence",
                    "deployment_state",
                ],
                "writes_require": (
                    "separate_exact_owner_authorization_and_current_gates"
                ),
            },
        )
        self.assertEqual(
            self.directive["communication_labels"],
            {
                "draft": "DRAFT — NOT SENT",
                "simulation": "SIMULATION — NO PROVIDER INTERACTION",
                "predicted": "PREDICTED POSITION — PROVIDER HAS NOT RESPONDED",
            },
        )

    def test_reporting_and_participation_require_current_evidence(self):
        reporting = self.directive["reporting"]
        self.assertEqual(reporting["mode"], "evidence_first")
        self.assertEqual(
            reporting["requires"],
            [
                "project_id_and_assignment_ids",
                "objective_bounded_scope_and_categorized_status",
                "starting_and_current_refs_shas_and_artifact_digests",
                "authenticated_provider_participation_unavailable_abstaining_and_labels",
                "target_and_actual_workload_by_provider_deviations_and_rationale",
                "openai_moderator_participant_director_actions_and_vote",
                "dissent_counter_bias_and_alternative_analysis",
                "exact_tests_trusted_ci_security_independent_review_and_verdict",
                "unresolved_risks_limitations_blockers_and_confidence",
                "measured_cost_token_usage_elapsed_time_and_unavailable_telemetry",
                "rolling_commit_baseline_current_measurement_delta_and_exclusions",
                "scope_or_authority_changes_and_exact_stored_owner_authorization",
                "recovery_state_rollback_and_restart_evidence_integrity",
                "next_deterministic_or_owner_gate_and_responsible_party",
                "owner_decisions_and_exact_stored_scope",
                "not_done_and_unverified",
            ],
        )
        self.assertTrue(reporting["fabrication_prohibited"])
        self.assertEqual(
            reporting["participation_source"],
            "authenticated_provider_response_only",
        )

        self.assertEqual(
            self.directive["availability"]["attempts"],
            self.charter["availability_policy"]["nonresponse_attempt_limit"],
        )
        self.assertEqual(self.directive["availability"]["partial"], "untrusted")
        self.assertEqual(
            self.directive["availability"]["ambiguous"], "no_duplicate_dispatch"
        )

    def test_review_and_voting_safety_authority_remain_unchanged(self):
        self.assertEqual(
            {
                name: member["voting_power"]
                for name, member in self.charter["committee"]["members"].items()
            },
            {"antigravity": 1.0, "cursor": 1.5, "grok": 1.5, "openai": 2.0, "claude": 2.5},
        )
        review = self.directive["review"]
        self.assertEqual(review["reviewer"], "different_non_author")
        self.assertEqual(review["binding"], "exact_candidate")
        self.assertEqual(review["unavailable"], "reroute_no_self_review")
        self.assertEqual(
            self.directive["external_action_boundary"],
            {
                "requires": (
                    "exact_stored_owner_authorization_for_action_scope_and_current_deterministic_gates"
                ),
                "oversight_grants_authority": False,
                "provider_or_browser_output_grants_authority": False,
            },
        )
        self.assertTrue(
            any("deterministic policy" in rule for rule in self.charter["authority_rules"])
        )

    def test_explicit_planning_payload_ceiling_remains_bounded(self):
        payload = canonical_team_charter_json().encode("utf-8")
        self.assertEqual(TEAM_CHARTER_MAX_BYTES, 12_512)
        self.assertLessEqual(len(payload), TEAM_CHARTER_MAX_BYTES)
        self.assertGreater(TEAM_CHARTER_MAX_BYTES - len(payload), 256)


class ReviewHandoffTests(unittest.TestCase):
    ACTORS = {
        "cursor": frozenset({"cursor-agent"}),
        "claude": frozenset({"claude-agent"}),
        "gemini": frozenset({"gemini-agent"}),
    }

    def bind(self, record, producer, run):
        return bind_review_handoff(
            record,
            producer,
            run,
            trusted_provider_actors=self.ACTORS,
        )

    def record(self, *, material_authors=None) -> tuple[TaskRecord, Assignment, ProviderRun]:
        producer = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            task="Implement on an isolated branch and create one draft PR",
            acceptance=["Return a review candidate artifact"],
            repository="example/demo",
            starting_ref="main",
            requires_approval=True,
            allow_draft_pr=True,
            estimated_cost_usd=0.5,
        )
        reviewer = Assignment(
            sequence=2,
            agent=ProviderName.CLAUDE,
            task="Reserved independent review slot",
            acceptance=["Review exact candidate"],
            repository="example/demo",
            starting_ref="main",
            depends_on=[1],
            estimated_cost_usd=0.25,
        )
        record = TaskRecord(
            task_id="DIR-HANDOFF",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-handoff",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: implement and review",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="Implement and independently review",
                risk_level=RiskLevel.MEDIUM,
                approval_required=True,
                max_hops=2,
                max_revisions=1,
                assignments=[producer, reviewer],
                final_acceptance=["Exact review, security, and CI remain current"],
            ),
            approved_assignments=[1],
            approved_assignment_digests={1: assignment_approval_digest(producer)},
        )
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=1,
            run_id="cursor-run-1",
            status="FINISHED",
            evidence_source="provider_authenticated",
            result=json.dumps(
                review_candidate_payload(material_authors=material_authors),
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        bind_verified_interaction(
            record,
            run,
            label="producer",
            account_effect=True,
        )
        return record, producer, run

    def test_exact_candidate_binds_the_prebudgeted_different_provider(self):
        record, producer, run = self.record()

        sequence, reviewer = self.bind(record, producer, run) or (None, None)

        self.assertEqual(sequence, 2)
        self.assertIsNotNone(reviewer)
        handoff = json.loads(reviewer.task)
        self.assertEqual(handoff["logical_producer"], "cursor")
        self.assertEqual(handoff["reviewer_provider"], "claude")
        self.assertEqual(handoff["head_sha"], "b" * 40)
        self.assertEqual(reviewer.estimated_cost_usd, 0.25)
        self.assertFalse(reviewer.allow_draft_pr)

    def test_exact_candidate_reconstructs_without_mutating_authenticated_plan(self):
        record, producer, run = self.record()
        original_plan = record.plan.model_dump_json()
        sequence, expected = self.bind(record, producer, run) or (None, None)
        self.assertEqual(sequence, 2)
        handoff = json.loads(expected.task)
        record.review_handoff_digests[2] = handoff["artifact_digest"]
        outcome = accepted_provider_outcome(record, run)
        bind_authenticated_director_acceptance_review(record, outcome)
        record.assignment_outcomes.append(outcome)
        record.provider_runs.append(run)

        restarted = TaskRecord.model_validate_json(record.model_dump_json())
        planned = next(
            item for item in restarted.plan.assignments if item.sequence == 2
        )
        reconstructed = reconstruct_review_handoff(
            restarted,
            planned,
            trusted_provider_actors=self.ACTORS,
        )

        self.assertEqual(reconstructed, expected)
        self.assertEqual(record.plan.model_dump_json(), original_plan)
        self.assertEqual(restarted.plan.model_dump_json(), original_plan)

    def test_reconstruction_rejects_changed_candidate_marker(self):
        record, producer, run = self.record()
        sequence, expected = self.bind(record, producer, run) or (None, None)
        self.assertEqual(sequence, 2)
        record.review_handoff_digests[2] = "f" * 64
        outcome = accepted_provider_outcome(record, run)
        bind_authenticated_director_acceptance_review(record, outcome)
        record.assignment_outcomes.append(outcome)
        record.provider_runs.append(run)
        planned = next(item for item in record.plan.assignments if item.sequence == 2)

        with self.assertRaisesRegex(ValueError, "digest is missing or changed"):
            reconstruct_review_handoff(
                record,
                planned,
                trusted_provider_actors=self.ACTORS,
            )

    def test_reconstruction_requires_exact_accepted_verified_event(self):
        record, producer, run = self.record()
        sequence, expected = self.bind(record, producer, run) or (None, None)
        self.assertEqual(sequence, 2)
        handoff = json.loads(expected.task)
        record.review_handoff_digests[2] = handoff["artifact_digest"]
        record.assignment_outcomes.append(accepted_provider_outcome(record, run))
        record.provider_runs.append(run)
        planned = next(item for item in record.plan.assignments if item.sequence == 2)

        tampered = record.model_copy(deep=True)
        tampered.assignment_outcomes[0].verified_interaction_event_digest = "f" * 64
        with self.assertRaisesRegex(ValueError, "changed from VERIFIED evidence"):
            reconstruct_review_handoff(
                tampered,
                planned,
                trusted_provider_actors=self.ACTORS,
            )

        missing_interaction = record.model_copy(deep=True)
        missing_interaction.provider_interaction_ledger = []
        with self.assertRaisesRegex(ValueError, "COMMUNICATION_EVIDENCE_FAILURE"):
            reconstruct_review_handoff(
                missing_interaction,
                planned,
                trusted_provider_actors=self.ACTORS,
            )

    def test_prebudgeted_reviewer_cannot_require_second_approval(self):
        record, producer, run = self.record()
        record.plan.assignments[1].requires_approval = True

        with self.assertRaisesRegex(ValueError, "second owner approval"):
            self.bind(record, producer, run)

    def test_material_author_cannot_receive_the_review_handoff(self):
        record, producer, run = self.record(material_authors=["cursor", "claude"])

        with self.assertRaisesRegex(ValueError, "material author"):
            self.bind(record, producer, run)

    def test_producer_cannot_omit_recorded_material_author(self):
        record, producer, run = self.record()
        upstream = Assignment(
            sequence=3,
            agent=ProviderName.CLAUDE,
            task="Provide material implementation input",
            acceptance=["Return evidence"],
        )
        producer.depends_on = [3]
        record.plan.assignments.append(upstream)
        record.approved_assignment_digests[1] = assignment_approval_digest(producer)
        record.assignment_outcomes.append(
            AssignmentOutcome(
                assignment_sequence=3,
                provider=ProviderName.CLAUDE,
                run_id="claude-upstream",
                result="material implementation input",
                review_summary="accepted",
            )
        )

        with self.assertRaisesRegex(ValueError, "recorded orchestration provenance"):
            self.bind(record, producer, run)

    def test_review_artifact_identity_is_bound_to_the_stored_handoff(self):
        record, producer, run = self.record()
        record.assignment_outcomes.append(
            AssignmentOutcome(
                assignment_sequence=1,
                provider=ProviderName.CURSOR,
                run_id="cursor-run-1",
                result=run.result or "candidate",
                review_summary="accepted",
            )
        )
        sequence, reviewer = self.bind(record, producer, run) or (None, None)
        self.assertEqual(sequence, 2)
        handoff = json.loads(reviewer.task)
        original_plan = record.plan.model_dump_json()
        record.review_handoff_digests[2] = handoff["artifact_digest"]
        payload = {
            "artifact_type": "edge_oracle_exact_sha_review/v1",
            "artifact_id": handoff["required_review_artifact_id"],
            "assignment_id": handoff["assignment_id"],
            "project": "demo",
            "repository": handoff["repository"],
            "pull_number": handoff["pull_number"],
            "base_ref": handoff["base_ref"],
            "base_sha": handoff["base_sha"],
            "head_ref": handoff["head_ref"],
            "head_sha": handoff["head_sha"],
            "implementation_provider": handoff["logical_producer"],
            "implementation_actor": handoff["implementation_actor"],
            "reviewer_provider": handoff["reviewer_provider"],
            "review_run_id": "claude-review-run",
            "verdict": "ACCEPT",
            "summary": "Accepted exact candidate.",
            "evidence": ["Reviewed the bound candidate."],
            "acceptance_checks": ["test"],
            "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
        }
        review_run = ProviderRun(
            provider=ProviderName.CLAUDE,
            assignment_sequence=2,
            run_id="claude-review-run",
            status="completed",
            evidence_source="provider_authenticated",
            result=json.dumps(payload),
        )
        bind_verified_interaction(record, review_run, label="review-original")
        record.provider_runs.append(review_run)
        authenticated = authenticate_provider_review_artifact(
            record,
            review_run,
            "secret",
            review_assignment=reviewer,
        )
        self.assertIsNotNone(authenticated)
        self.assertEqual(record.plan.model_dump_json(), original_plan)

        review_run.evidence_source = "owner_recovery"
        record.provider_runs[-1] = review_run
        with self.assertRaisesRegex(ValueError, "authenticated provider evidence"):
            authenticate_provider_review_artifact(
                record,
                review_run,
                "secret",
                review_assignment=reviewer,
            )
        review_run.evidence_source = "provider_authenticated"
        record.provider_runs[-1] = review_run

        for field, changed in (("pull_number", 18), ("implementation_actor", "other-actor")):
            with self.subTest(field=field):
                tampered = {**payload, field: changed}
                review_run.result = json.dumps(tampered)
                bind_verified_interaction(
                    record, review_run, label=f"review-tampered-{field}"
                )
                record.provider_runs[-1] = review_run
                with self.assertRaisesRegex(ValueError, "stored handoff"):
                    authenticate_provider_review_artifact(
                        record,
                        review_run,
                        "secret",
                        review_assignment=reviewer,
                    )

    def test_tampering_with_candidate_metadata_invalidates_its_digest(self):
        record, producer, run = self.record()
        payload = json.loads(run.result or "{}")
        payload["head_sha"] = "c" * 40
        run.result = json.dumps(payload)
        bind_verified_interaction(record, run, label="tampered-candidate")

        with self.assertRaisesRegex(ValidationError, "digest"):
            self.bind(record, producer, run)

    def test_review_overlay_cannot_change_planned_authority_or_budget(self):
        record, producer, run = self.record()
        record.assignment_outcomes.append(accepted_provider_outcome(record, run))
        sequence, reviewer = self.bind(record, producer, run) or (None, None)
        self.assertEqual(sequence, 2)
        handoff = json.loads(reviewer.task)
        record.review_handoff_digests[2] = handoff["artifact_digest"]
        payload = {
            "artifact_type": "edge_oracle_exact_sha_review/v1",
            "artifact_id": handoff["required_review_artifact_id"],
            "assignment_id": handoff["assignment_id"],
            "project": "demo",
            "repository": handoff["repository"],
            "pull_number": handoff["pull_number"],
            "base_ref": handoff["base_ref"],
            "base_sha": handoff["base_sha"],
            "head_ref": handoff["head_ref"],
            "head_sha": handoff["head_sha"],
            "implementation_provider": handoff["logical_producer"],
            "implementation_actor": handoff["implementation_actor"],
            "reviewer_provider": handoff["reviewer_provider"],
            "review_run_id": "claude-review-authority",
            "verdict": "ACCEPT",
            "summary": "Accepted exact candidate.",
            "evidence": ["Reviewed the bound candidate."],
            "acceptance_checks": ["test"],
            "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
        }
        review_run = ProviderRun(
            provider=ProviderName.CLAUDE,
            assignment_sequence=2,
            run_id="claude-review-authority",
            status="completed",
            evidence_source="provider_authenticated",
            result=json.dumps(payload),
        )
        bind_verified_interaction(record, review_run, label="review-authority")
        record.provider_runs.extend([run, review_run])

        for changed in (
            reviewer.model_copy(update={"estimated_cost_usd": 99}),
            reviewer.model_copy(update={"depends_on": []}),
            reviewer.model_copy(update={"requires_approval": True}),
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ValueError, "authority or budget"):
                    authenticate_provider_review_artifact(
                        record,
                        review_run,
                        "secret",
                        review_assignment=changed,
                    )

    def test_mutable_evidence_source_alone_cannot_authenticate_review(self):
        record, producer, run = self.record()
        record.provider_interaction_ledger = []
        run.interaction_id = "forged-mutable-label-only"

        with self.assertRaisesRegex(ValueError, "COMMUNICATION_EVIDENCE_FAILURE"):
            self.bind(record, producer, run)

    def test_owner_recovery_result_cannot_become_provider_review_authority(self):
        record, producer, run = self.record()
        run.evidence_source = "owner_recovery"

        with self.assertRaisesRegex(ValueError, "provider-authenticated"):
            self.bind(record, producer, run)

    def test_nonwrite_and_unapproved_producers_cannot_arm_review(self):
        record, producer, run = self.record()
        nonwrite = producer.model_copy(update={"allow_draft_pr": False})
        record.plan.assignments[0] = nonwrite
        record.approved_assignment_digests[1] = assignment_approval_digest(nonwrite)
        with self.assertRaisesRegex(ValueError, "lacks draft-PR authority"):
            self.bind(record, nonwrite, run)

        record, producer, run = self.record()
        record.approved_assignment_digests = {}
        with self.assertRaisesRegex(ValueError, "exact-scope owner approval"):
            self.bind(record, producer, run)

    def test_candidate_actor_must_belong_to_producer_and_not_reviewer(self):
        record, producer, run = self.record()
        payload = json.loads(run.result or "{}")
        payload["implementation_actor"] = "claude-agent"
        payload.pop("artifact_digest")
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        payload["artifact_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        run.result = json.dumps(payload)
        bind_verified_interaction(record, run, label="wrong-implementation-actor")

        with self.assertRaisesRegex(ValueError, "not bound to the logical producer"):
            self.bind(record, producer, run)

        actor_record, actor_producer, actor_run = self.record()
        with self.assertRaisesRegex(ValueError, "no configured trusted GitHub actor"):
            bind_review_handoff(
                actor_record,
                actor_producer,
                actor_run,
                trusted_provider_actors={},
            )

    def test_transitive_material_author_cannot_review_final_candidate(self):
        upstream = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Create material design input",
            acceptance=["Return design evidence"],
        )
        middle = Assignment(
            sequence=2,
            agent=ProviderName.GEMINI,
            task="Turn the design into a tested prototype",
            acceptance=["Return prototype evidence"],
            depends_on=[1],
        )
        producer = Assignment(
            sequence=3,
            agent=ProviderName.CURSOR,
            task="Integrate and create a draft PR",
            acceptance=["Return exact candidate"],
            repository="example/demo",
            starting_ref="main",
            depends_on=[2],
            requires_approval=True,
            allow_draft_pr=True,
        )
        reviewer = Assignment(
            sequence=4,
            agent=ProviderName.CLAUDE,
            task="Reserved review",
            acceptance=["Review exact candidate"],
            repository="example/demo",
            depends_on=[3],
        )
        record = TaskRecord(
            task_id="DIR-HANDOFF",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-transitive",
                channel_id="C1",
                thread_ts="1.5",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: staged implementation",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="Staged implementation",
                risk_level=RiskLevel.MEDIUM,
                approval_required=True,
                max_hops=4,
                max_revisions=0,
                assignments=[upstream, middle, producer, reviewer],
                final_acceptance=["Independent exact-SHA review"],
            ),
            assignment_outcomes=[
                AssignmentOutcome(
                    assignment_sequence=1,
                    provider=ProviderName.CLAUDE,
                    run_id="claude-upstream",
                    result="design",
                    review_summary="accepted",
                ),
                AssignmentOutcome(
                    assignment_sequence=2,
                    provider=ProviderName.GEMINI,
                    run_id="gemini-middle",
                    result="prototype",
                    review_summary="accepted",
                ),
            ],
            approved_assignments=[3],
            approved_assignment_digests={3: assignment_approval_digest(producer)},
        )
        payload = review_candidate_payload(
            material_authors=["claude", "gemini", "cursor"]
        )
        payload["assignment_id"] = "DIR-HANDOFF:3"
        payload.pop("artifact_digest")
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        payload["artifact_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        run = ProviderRun(
            provider=ProviderName.CURSOR,
            assignment_sequence=3,
            run_id="cursor-final",
            status="FINISHED",
            evidence_source="provider_authenticated",
            result=json.dumps(payload),
        )
        bind_verified_interaction(record, run, label="transitive-producer")

        with self.assertRaisesRegex(ValueError, "review provider is listed as a material author"):
            self.bind(record, producer, run)

    def test_dependency_closure_excludes_unrelated_prior_assignments(self):
        unrelated = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Unrelated analysis",
            acceptance=["Evidence"],
        )
        upstream = Assignment(
            sequence=2,
            agent=ProviderName.GEMINI,
            task="Declared upstream input",
            acceptance=["Evidence"],
        )
        middle = Assignment(
            sequence=3,
            agent=ProviderName.CLAUDE,
            task="Declared middle input",
            acceptance=["Evidence"],
            depends_on=[2],
        )
        producer = Assignment(
            sequence=4,
            agent=ProviderName.CURSOR,
            task="Produce candidate",
            acceptance=["Evidence"],
            depends_on=[3],
        )

        self.assertEqual(
            transitive_dependency_sequences(
                [unrelated, upstream, middle, producer], producer
            ),
            {2, 3},
        )


class NonresponseContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_attempt_limit_survives_process_restart(self):
        class MustNotPoll:
            async def poll(self, run):
                raise AssertionError("persisted attempt cap must stop before another poll")

        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="bounded work",
            acceptance=["evidence"],
        )
        run = ProviderRun(
            provider=ProviderName.CLAUDE,
            assignment_sequence=1,
            run_id="persisted-run",
            status="RUNNING",
            result="partial",
        )
        record = TaskRecord(
            task_id="DIR-PERSISTED-ATTEMPTS",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-persisted",
                channel_id="C1",
                thread_ts="1.4",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: bounded work",
            ),
            provider_contact_attempts=[
                ProviderContactAttempt(
                    assignment_sequence=1,
                    provider=ProviderName.CLAUDE,
                    ordinal=ordinal,
                    phase="poll",
                    outcome="no_response",
                    run_id="persisted-run",
                    partial_output="partial",
                    error="ReadTimeout: unavailable",
                )
                for ordinal in (1, 2, 3)
            ],
        )
        engine = OrchestrationEngine(replace(Settings(), provider_poll_seconds=0), None)

        with self.assertRaises(ProviderUnavailableError):
            await engine._wait(MustNotPoll(), run, record, assignment)
        self.assertEqual(len(record.provider_contact_attempts), 3)

    async def test_three_safe_poll_failures_defer_lane_and_continue_independent_work(self):
        class NonresponsiveAdapter:
            name = ProviderName.CLAUDE

            def __init__(self):
                self.dispatch_calls = 0
                self.poll_calls = 0

            async def dispatch(self, assignment, context):
                self.dispatch_calls += 1
                return ProviderRun(
                    provider=self.name,
                    run_id="claude-persisted-run",
                    status="RUNNING",
                    result="partial untrusted output",
                )

            async def poll(self, run):
                self.poll_calls += 1
                raise httpx.ReadTimeout("no response")

        class CompletingAdapter:
            name = ProviderName.CURSOR

            def __init__(self):
                self.dispatch_calls = 0

            async def dispatch(self, assignment, context):
                self.dispatch_calls += 1
                return ProviderRun(
                    provider=self.name,
                    run_id="cursor-independent-run",
                    status="FINISHED",
                    result="independent safe evidence",
                )

            async def poll(self, run):
                raise AssertionError("terminal run must not be polled")

        class AcceptingDirector:
            async def review(self, record, result):
                return ReviewDecision(
                    verdict=ReviewVerdict.ACCEPT,
                    summary="Independent evidence accepted.",
                    evidence=["bounded test evidence"],
                )

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
                max_agent_hops=3,
                provider_poll_seconds=0,
                sqlite_path=str(Path(directory) / "tasks.sqlite3"),
            )
            store = TaskStore(settings.sqlite_path)
            task = TaskRecord(
                task_id="DIR-NONRESPONSE",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-nonresponse",
                    channel_id="C1",
                    thread_ts="1.2",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: run independent lanes",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Run independent lanes",
                    risk_level=RiskLevel.MEDIUM,
                    approval_required=False,
                    max_hops=3,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Run unavailable lane",
                            acceptance=["Return evidence"],
                        ),
                        Assignment(
                            sequence=2,
                            agent=ProviderName.CURSOR,
                            task="Run independent safe lane",
                            acceptance=["Return independent evidence"],
                            repository="example/demo",
                        ),
                    ],
                    final_acceptance=["Report completed and deferred lanes"],
                ),
            )
            claude = NonresponsiveAdapter()
            cursor = CompletingAdapter()
            engine = OrchestrationEngine(settings, store)
            seed_authenticated_normalized_plan(store, engine, task)
            engine.providers = {
                ProviderName.CLAUDE: claude,
                ProviderName.CURSOR: cursor,
            }
            engine.director = AcceptingDirector()

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(
                completed.status,
                TaskStatus.OWNER_DECISION_REQUIRED,
                completed.error or completed.message,
            )
            self.assertEqual(claude.dispatch_calls, 1)
            self.assertEqual(claude.poll_calls, 3)
            self.assertEqual(cursor.dispatch_calls, 1)
            self.assertEqual(
                [attempt.ordinal for attempt in completed.provider_contact_attempts],
                [1, 2, 3],
            )
            self.assertTrue(
                all(attempt.partial_output == "partial untrusted output" for attempt in completed.provider_contact_attempts)
            )
            self.assertEqual(completed.unavailable_providers, [ProviderName.CLAUDE])
            self.assertEqual(completed.deferred_assignments, [1])
            self.assertEqual(completed.assignment_outcomes[0].provider, ProviderName.CURSOR)
            self.assertIn("no duplicate dispatch", completed.availability_impacts[0])

    async def test_post_submit_failures_are_recorded_once_and_never_retried(self):
        class AmbiguousAdapter:
            name = ProviderName.CLAUDE

            def __init__(self, error):
                self.dispatch_calls = 0
                self.error = error

            async def dispatch(self, assignment, context):
                self.dispatch_calls += 1
                raise self.error

        request = httpx.Request("POST", "https://claude.invalid/fire")
        errors = {
            "timeout": httpx.ReadTimeout("request outcome unknown", request=request),
            "server_status": httpx.HTTPStatusError(
                "provider returned 503",
                request=request,
                response=httpx.Response(503, request=request),
            ),
            "malformed_json": ValueError("provider returned malformed JSON"),
            "missing_identifier": KeyError("run_id"),
            "provider_post_submit": PostSubmitAmbiguityError(
                "provider accepted create request without an identifier"
            ),
        }
        for label, error in errors.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
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
                store = TaskStore(settings.sqlite_path)
                task = TaskRecord(
                    task_id=f"DIR-AMBIGUOUS-{label.upper()}",
                    status=TaskStatus.QUEUED,
                    intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                    intake=SlackIntake(
                        source_event_id=f"evt-ambiguous-{label}",
                        channel_id="C1",
                        thread_ts="1.3",
                        user_id="U1",
                        text="DIRECTOR Project: demo Outcome: bounded work",
                    ),
                    plan=DirectorPlan(
                        project="demo",
                        normalized_outcome="Bounded work",
                        risk_level=RiskLevel.LOW,
                        approval_required=False,
                        max_hops=1,
                        max_revisions=0,
                        assignments=[
                            Assignment(
                                sequence=1,
                                agent=ProviderName.CLAUDE,
                                task="Perform bounded work",
                                acceptance=["Return evidence"],
                            )
                        ],
                        final_acceptance=["Evidence accepted"],
                    ),
                )
                adapter = AmbiguousAdapter(error)
                engine = OrchestrationEngine(settings, store)
                seed_authenticated_normalized_plan(store, engine, task)
                engine.providers = {ProviderName.CLAUDE: adapter}

                await engine.run(task.task_id)

                completed = store.get(task.task_id)
                self.assertEqual(completed.status, TaskStatus.OWNER_DECISION_REQUIRED)
                self.assertEqual(adapter.dispatch_calls, 1)
                self.assertEqual(len(completed.provider_contact_attempts), 1)
                self.assertEqual(
                    completed.provider_contact_attempts[0].outcome,
                    "ambiguous_dispatch",
                )
                completed.status = TaskStatus.QUEUED
                store.save(completed)
                await engine.run(task.task_id)
                self.assertEqual(adapter.dispatch_calls, 1)
                self.assertIn(
                    "remains ambiguous", (store.get(task.task_id).message or "")
                )

    async def test_plain_provider_error_is_not_misclassified_as_post_submit(self):
        class PredispatchAdapter:
            name = ProviderName.CLAUDE

            async def dispatch(self, assignment, context):
                raise ProviderError("bounded public research query is required")

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
            store = TaskStore(settings.sqlite_path)
            task = TaskRecord(
                task_id="DIR-PREDISPATCH-ERROR",
                status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(
                    source_event_id="evt-predispatch-error",
                    channel_id="C1",
                    thread_ts="1.4",
                    user_id="U1",
                    text="DIRECTOR Project: demo Outcome: bounded research",
                ),
                plan=DirectorPlan(
                    project="demo",
                    normalized_outcome="Bounded research",
                    risk_level=RiskLevel.LOW,
                    approval_required=False,
                    max_hops=1,
                    max_revisions=0,
                    assignments=[
                        Assignment(
                            sequence=1,
                            agent=ProviderName.CLAUDE,
                            task="Perform bounded work",
                            acceptance=["Return evidence"],
                        )
                    ],
                    final_acceptance=["Evidence accepted"],
                ),
            )
            engine = OrchestrationEngine(settings, store)
            seed_authenticated_normalized_plan(store, engine, task)
            engine.providers = {ProviderName.CLAUDE: PredispatchAdapter()}

            await engine.run(task.task_id)

            completed = store.get(task.task_id)
            self.assertEqual(completed.status, TaskStatus.FAILED)
            self.assertEqual(completed.provider_contact_attempts, [])

    async def test_perplexity_approval_block_displays_exact_full_query(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        class RecordingClient:
            body = None

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, *, headers, json):
                RecordingClient.body = json
                return FakeResponse()

        query = "public calibration evidence " + ("q" * 1_970)
        record = TaskRecord(
            task_id="DIR-QUERY-DISPLAY",
            status=TaskStatus.OWNER_DECISION_REQUIRED,
            intake=SlackIntake(
                source_event_id="evt-query-display",
                channel_id="C1",
                thread_ts="1.4",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: research",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="research",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                approval_reasons=[],
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.PERPLEXITY,
                        task="Collect public calibration evidence",
                        acceptance=["Return cited public evidence"],
                        public_research_query=query,
                        requires_approval=True,
                    )
                ],
                final_acceptance=["Evidence is cited"],
            ),
        )
        message = (
            "*DIR-QUERY-DISPLAY — OWNER DECISION REQUIRED*\n"
            + ("task " * 300)
            + f"\nPublic research query: `{query}`"
        )
        notifier = SlackNotifier(replace(Settings(), slack_bot_token="test-slack"))
        with patch(
            "director_orchestrator.engine.httpx.AsyncClient", RecordingClient
        ):
            await notifier.post(
                record,
                message,
                approvable=True,
                approval_sequence=1,
                approval_query=query,
            )

        blocks = RecordingClient.body["blocks"]
        self.assertEqual(
            blocks[1]["text"]["text"],
            "Exact outbound Perplexity query (approved byte-for-byte):\n" + query,
        )
        self.assertEqual(blocks[1]["text"]["type"], "plain_text")
        self.assertEqual(blocks[-1]["type"], "actions")
        value = blocks[-1]["elements"][0]["value"]
        task_id, sequence, digest = parse_approval_button_value(value)
        self.assertEqual((task_id, sequence), (record.task_id, 1))
        self.assertTrue(digest.startswith("sha256:"))


class AssignmentBoundaryTests(unittest.TestCase):
    def test_renamed_nested_and_malformed_assignment_fields_fail_closed(self):
        valid = {
            "sequence": 1,
            "agent": "claude",
            "task": "Analyze",
            "acceptance": ["Report evidence"],
        }
        cases = [
            {**valid, "provider": valid["agent"]},
            {**valid, "task": {"nested": "Analyze"}},
            {**valid, "acceptance": "Report evidence"},
            {**valid, "public_research_query": ["not", "a", "string"]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    Assignment.model_validate(payload)

    def test_perplexity_indirect_verdicts_and_private_queries_fail_closed(self):
        settings = replace(
            Settings(),
            project_allowlist=("demo",),
            repository_allowlist=("example/demo",),
            allowed_starting_refs=("main",),
        )
        cases = [
            (
                "Assess whether the attached patch should be accepted",
                "public evidence about calibration",
                "operational decision",
            ),
            (
                "Find public methodology",
                r"review C:\\Users\\owner\\private\\src\\policy.py",
                "repository path",
            ),
            (
                "Find public methodology",
                "analyze commit " + "a" * 40,
                "commit SHA",
            ),
            (
                "Find public methodology",
                "use secret sk-examplecredential123456",
                "secret-like value",
            ),
            (
                "Find public methodology",
                "Research records about jane.doe@example.com",
                "email address",
            ),
            (
                "Find public methodology",
                "Research records for 018f0f5e-7b3a-7c9d-8f12-123456789abc",
                "UUID",
            ),
            (
                "Find public methodology",
                "Research records for Slack user u0b9uj86d27",
                "Slack identifier",
            ),
        ]
        for index, (task, query, reason) in enumerate(cases, start=1):
            plan = DirectorPlan(
                project="demo",
                normalized_outcome="Bounded public research",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.PERPLEXITY,
                        task=task,
                        acceptance=["Return public sources"],
                        public_research_query=query,
                    )
                ],
                final_acceptance=["Primary team evaluates evidence"],
            )
            with self.subTest(index=index):
                errors = execution_policy_errors(plan, settings)
                self.assertTrue(any(reason in error for error in errors), errors)
# [[Codex | GPT-5.6 | 2026-08-24 | 09:20 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:04 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:30 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 15:50 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:30 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 17:11 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 17:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:36 UTC | END]]
