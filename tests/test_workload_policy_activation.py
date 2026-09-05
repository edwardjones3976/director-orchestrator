# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 01:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:54 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:36 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-04 | 11:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 07:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:59 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | BEGIN]]
from __future__ import annotations

import os
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from director_orchestrator.approvals import reserve_provider_effect, current_owner_decision, StaleApproval
from director_orchestrator.config import Settings
from director_orchestrator.engine import OrchestrationEngine
from director_orchestrator.langgraph_runtime import RuntimeExecutionStatus
from director_orchestrator.models import (
    Assignment,
    AssignmentKind,
    IntakeAuthentication,
    OwnerApproval,
    DirectorPlan,
    PlanPrestageRecord,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.policy import enforce_plan, execution_policy_errors, preview_plan
from director_orchestrator.store import TaskStore, WorkloadAttemptOutcome
from director_orchestrator.workload_policy import (
    DirectProductionProvider,
    DirectProductionRecord,
    EnforcementMode,
    PRIMARY_PROVIDERS,
    Provider,
    WorkKind,
    WorkSize,
    WorkloadPolicyError,
    WorkloadSelectionHold,
)


class WorkloadPolicyActivationTests(unittest.TestCase):
    @staticmethod
    def _seed_mature_selector_history(path: Path, *, capped: bool = False) -> None:
        """Persist isolated historical-credit fixtures through the real ledger.

        Authentication of credit creation is covered by workload-ledger tests;
        these fixtures isolate selection/cost/readiness from the startup hold.
        No production seeding or bypass is provided by this test helper.
        """
        providers = (
            [DirectProductionProvider.CLAUDE] * 4
            + [DirectProductionProvider.CURSOR] * 3
            + [DirectProductionProvider.GROK] * 2
            + [DirectProductionProvider.PERPLEXITY]
            + [DirectProductionProvider.EXTERNAL_OPENAI] * 9
        )
        if capped:
            providers = [DirectProductionProvider.CLAUDE] * 6 + [DirectProductionProvider.CURSOR] * 5 + [DirectProductionProvider.GROK] * 3 + [DirectProductionProvider.EXTERNAL_OPENAI] * 5
        with closing(sqlite3.connect(path)) as connection:
            for index, provider in enumerate(providers):
                identity = f"accepted-selector-history-{capped}-{index}"
                connection.execute(
                    """INSERT INTO direct_production_credits(
                        credit_key, task_id, plan_generation, assignment_sequence,
                        assignment_id, provider, effort_units, provider_event_digest,
                        provider_result_digest, review_event_digest, review_result_digest,
                        recorded_at) VALUES (?, ?, 1, 1, ?, ?, 1, ?, ?, ?, ?, ?)""",
                    (identity, "selector-history-fixture", identity, provider.value,
                     "a"*64, "b"*64, "c"*64, "d"*64, "2026-09-05T00:00:00Z"),
                )
            connection.commit()

    def test_runtime_uses_the_same_trusted_work_kind_as_plan_enforcement(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Develop a bounded implementation without relying on its type label",
            acceptance=["Implementation evidence is recorded"],
        )

        self.assertIs(
            OrchestrationEngine._workload_kind(assignment),
            WorkKind.IMPLEMENTATION,
        )

    def test_typed_review_precedes_incidental_implementation_prose(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CURSOR,
            kind=AssignmentKind.EXACT_SHA_REVIEW,
            task="Review the exact implementation SHA without changing it",
            acceptance=["Return independent review evidence"],
        )

        self.assertIs(
            OrchestrationEngine._workload_kind(assignment),
            WorkKind.REVIEW,
        )

    def test_default_configuration_is_enabled_and_enforced(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKLOAD_POLICY_ENABLED": "true",
                "WORKLOAD_POLICY_MODE": "enforced",
                "WORKLOAD_POLICY_WINDOW_ASSIGNMENTS": "20",
                "WORKLOAD_POLICY_SHADOW_ASSIGNMENTS": "5",
            },
            clear=False,
        ):
            settings = Settings()

        settings.validate_workload_policy_configuration()
        self.assertEqual(
            settings.workload_policy_readiness(),
            {
                "enabled": True,
                "enforced": True,
                "shadow": False,
                "mode_valid": True,
                "window_valid": True,
                "shadow_sample_valid": True,
                "selector_mode_valid": True,
                "cost_ranks_valid": False,
                "ready_provider_ranks_complete": False,
            },
        )

    def test_paid_routing_requires_explicit_comparable_cost_ranks(self) -> None:
        missing = replace(
            Settings(),
            allow_paid_runs=True,
            workload_policy_enabled=True,
            primary_workhorse="cheapest_capable",
            trusted_provider_cost_ranks_json="",
            claude_routine_url="https://example.invalid/claude",
            claude_routine_token="token",
        )
        with self.assertRaisesRegex(ValueError, "TRUSTED_PROVIDER_COST_RANKS"):
            missing.validate_workload_policy_configuration()

        malformed = replace(
            missing,
            trusted_provider_cost_ranks_json='{"claude": true}',
        )
        with self.assertRaisesRegex(ValueError, "positive integers"):
            malformed.validate_workload_policy_configuration()

        ambiguous = replace(
            missing,
            trusted_provider_cost_ranks_json='{"claude": 1, "cursor": 1}',
        )
        with self.assertRaisesRegex(ValueError, "unique total order"):
            ambiguous.validate_workload_policy_configuration()

        external_openai = replace(
            missing,
            trusted_provider_cost_ranks_json='{"external_openai": 1}',
        )
        with self.assertRaisesRegex(ValueError, "unsupported provider"):
            external_openai.validate_workload_policy_configuration()

        invalid_payloads = (
            ("not-json", "JSON object"),
            ("[]", "non-empty JSON object"),
            ("{}", "non-empty JSON object"),
            ('{"Claude": 1, " claude ": 2}', "provider names must be unique"),
            ('{"claude": 0}', "positive integers"),
            ('{"claude": -1}', "positive integers"),
            ('{"claude": 1.5}', "positive integers"),
            ('{"claude": "1"}', "positive integers"),
        )
        for payload, reason in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, reason):
                    replace(
                        missing,
                        trusted_provider_cost_ranks_json=payload,
                    ).validate_workload_policy_configuration()

        incomplete = replace(
            missing,
            trusted_provider_cost_ranks_json='{"claude": 1}',
            cursor_api_key="cursor-secret",
        )
        with self.assertRaisesRegex(ValueError, "every configured ready specialist"):
            incomplete.validate_workload_policy_configuration()

    def test_paid_routing_accepts_only_explicit_ready_provider_ranks(self) -> None:
        settings = replace(
            Settings(),
            allow_paid_runs=True,
            workload_policy_enabled=True,
            primary_workhorse="cheapest_capable",
            trusted_provider_cost_ranks_json='{"claude": 2, "cursor": 1}',
            claude_routine_url="https://example.invalid/claude",
            claude_routine_token="token",
            cursor_api_key="cursor-secret",
            gemini_api_key="",
            grok_api_key="",
            perplexity_api_key="",
        )

        settings.validate_workload_policy_configuration()
        self.assertEqual(
            settings.trusted_provider_cost_ranks(), {"claude": 2, "cursor": 1}
        )

    def test_shadow_mode_is_observe_only_but_remains_valid(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKLOAD_POLICY_ENABLED": "true",
                "WORKLOAD_POLICY_MODE": "shadow",
                "WORKLOAD_POLICY_WINDOW_ASSIGNMENTS": "20",
                "WORKLOAD_POLICY_SHADOW_ASSIGNMENTS": "5",
            },
            clear=False,
        ):
            settings = Settings()

        settings.validate_workload_policy_configuration()
        self.assertTrue(settings.workload_policy_readiness()["shadow"])
        self.assertFalse(settings.workload_policy_readiness()["enforced"])

    def test_health_readiness_uses_persisted_shadow_promotion(self) -> None:
        settings = replace(
            Settings(),
            workload_policy_enabled=True,
            workload_policy_mode="shadow",
            workload_policy_shadow_assignments=5,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "director.sqlite3")
            for sequence in range(1, 6):
                reservation = store.reserve_workload_assignment(
                    reservation_key=f"health-shadow-{sequence}",
                    task_id="DIR-HEALTH-SHADOW",
                    assignment_id=f"DIR-HEALTH-SHADOW:{sequence}",
                    phase="dispatch",
                    provider=Provider.CLAUDE,
                    effort_units=1,
                    kind=WorkKind.COORDINATION,
                    size=WorkSize.SIMPLE,
                    model_label=None,
                    available_primary_providers=PRIMARY_PROVIDERS,
                    mode=EnforcementMode.SHADOW,
                    shadow_assignment_limit=5,
                )
                store.finalize_workload_reservation(
                    reservation.reservation_key, WorkloadAttemptOutcome.INVOKED
                )

            effective = store.effective_workload_mode(
                configured_mode=EnforcementMode.SHADOW,
                shadow_assignment_limit=5,
            )
            readiness = settings.workload_policy_readiness(
                effective_mode=effective.value
            )

        self.assertEqual(effective, EnforcementMode.ENFORCED)
        self.assertTrue(readiness["enforced"])
        self.assertFalse(readiness["shadow"])

    def test_disabled_policy_reports_no_active_enforced_or_shadow_mode(self) -> None:
        for mode in ("enforced", "shadow"):
            with self.subTest(mode=mode):
                settings = replace(
                    Settings(),
                    workload_policy_enabled=False,
                    workload_policy_mode=mode,
                )

                readiness = settings.workload_policy_readiness()

                self.assertFalse(readiness["enabled"])
                self.assertFalse(readiness["enforced"])
                self.assertFalse(readiness["shadow"])
                self.assertTrue(readiness["mode_valid"])

    def test_invalid_runtime_mode_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {"WORKLOAD_POLICY_MODE": "advisory"},
            clear=False,
        ):
            settings = Settings()

        with self.assertRaisesRegex(ValueError, "enforced.*shadow"):
            settings.validate_workload_policy_configuration()

    def test_window_and_shadow_sample_are_owner_fixed(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKLOAD_POLICY_WINDOW_ASSIGNMENTS": "19",
                "WORKLOAD_POLICY_SHADOW_ASSIGNMENTS": "4",
            },
            clear=False,
        ):
            settings = Settings()

        with self.assertRaisesRegex(ValueError, "exactly 20"):
            settings.validate_workload_policy_configuration()

    def test_runtime_gate_applies_only_to_new_v1_plans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = OrchestrationEngine(
                replace(
                    Settings(),
                    sqlite_path=str(Path(directory) / "director.sqlite3"),
                ),
                TaskStore(Path(directory) / "director.sqlite3"),
            )
            assignment = Assignment(
                sequence=1,
                agent=ProviderName.CLAUDE,
                task="Perform bounded work",
                acceptance=["Evidence is recorded"],
            )
            plan = DirectorPlan(
                project="demo",
                normalized_outcome="Perform bounded work",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[assignment],
                final_acceptance=["Evidence is recorded"],
            )
            record = TaskRecord(
                task_id="DIR-WORKLOAD-ACTIVATION",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-workload-activation",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Perform bounded work",
                ),
                plan=plan,
            )

            self.assertFalse(engine._workload_policy_applies(record))
            record.plan = plan.model_copy(update={"workload_policy_version": "v1"})
            self.assertTrue(engine._workload_policy_applies(record))

    @staticmethod
    def _selection_plan() -> DirectorPlan:
        plan = DirectorPlan(
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
                    model_label="FABLE 5 ULTRA/MAX",
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
            prestage=PlanPrestageRecord(
                packet_id="trusted-selection-1",
                goal="Implement and independently review the bounded change",
                terminal_condition="Exact candidate accepted",
                repository="example/demo",
                worktree="C:/worktrees/trusted-selection-1",
                branch="main",
                base_sha="a" * 40,
                producer=ProviderName.CLAUDE,
                independent_reviewer=ProviderName.CURSOR,
                owned_paths=("src/demo.py",),
                prohibited_paths=("private/",),
                required_tests=("python -m unittest tests.test_demo",),
                required_security_evidence=("No credential diff",),
                budget_units=7,
                timeout_seconds=3600,
                retry_limit=3,
                merge_eligibility_conditions=("Exact-SHA review accepts",),
                stopping_conditions=("Budget is exhausted",),
            ),
        )
        return plan

    def test_real_plan_install_persists_selection_hold_without_dispatch_or_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "director.sqlite3"
            settings = replace(Settings(), sqlite_path=str(path), allow_paid_runs=True,
                openai_api_key="fixture", project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                trusted_provider_cost_ranks_json='{"cursor":1,"claude":2,"grok":3}',
                claude_routine_url="https://example.invalid/claude", claude_routine_token="fixture",
                cursor_api_key="fixture", grok_enabled=True, grok_api_key="fixture")
            store = TaskStore(path)
            providers = {provider: Mock() for provider in
                         (ProviderName.CLAUDE, ProviderName.CURSOR, ProviderName.GROK)}
            engine = OrchestrationEngine(settings, store, providers=providers)
            from director_orchestrator import main
            plan = self._selection_plan().model_copy(update={
                "owner_question": None, "approval_required": True,
                "approval_reasons": ["Review selected plan"],
            })
            engine.director = Mock(plan=AsyncMock(return_value=plan))
            record = TaskRecord(task_id="DIR-SELECTION-HOLD", status=TaskStatus.QUEUED,
                intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
                intake=SlackIntake(source_event_id="selection-hold", channel_id="C1",
                    thread_ts="1.1", user_id="U1", text="Implement and independently review the bounded change"))
            store.create(record)
            self._seed_mature_selector_history(path, capped=True)
            asyncio.run(engine.run(record.task_id))
            held = store.get(record.task_id)
            self.assertEqual(held.status, TaskStatus.OWNER_DECISION_REQUIRED, held.error)
            self.assertEqual(held.message, WorkloadSelectionHold.code)
            self.assertTrue(all(reason.startswith("direct_production_hard_cap_exceeded:") for reason in held.workload_selection_hold_reasons))
            self.assertEqual(len(store.list_recent_direct_production(limit=50)), 19)
            self.assertEqual(held.provider_runs, [])
            self.assertEqual([event.kind for event in held.task_effect_ledger],
                             ["director_intent", "director_observation"])
            engine._require_verified_plan_materialization(held)
            # Generic approval/resume cannot erase the typed persisted hold or replan.
            held.owner_approved = True
            held.message = "Resume approved"
            store.save(held)
            restarted = OrchestrationEngine(settings, TaskStore(path), providers=providers)
            restarted.director = Mock(plan=AsyncMock(side_effect=AssertionError("must not replan")))
            asyncio.run(restarted.run(record.task_id))
            restarted.director.plan.assert_not_awaited()
            engine.director.plan.assert_awaited_once()
            self.assertFalse(store.get(record.task_id).owner_approved)
            for provider in providers.values():
                self.assertEqual(provider.mock_calls, [])
            held = store.get(record.task_id)
            decision = current_owner_decision(held)
            self.assertEqual(decision.kind, "workload_selection_clarification")
            self.assertTrue(decision.clarification)
            changed = held.model_copy(deep=True)
            changed.plan_generation += 1
            self.assertNotEqual(current_owner_decision(changed).digest, decision.digest)
            changed = held.model_copy(deep=True)
            changed.workload_selection_hold_reasons = ["different allocation hold"]
            self.assertNotEqual(current_owner_decision(changed).digest, decision.digest)
            with patch.object(main, "settings", settings):
                for note, digest in (("Approved", decision.digest),
                                     ("Reconsider available routes", "sha256:" + "0" * 64)):
                    with self.assertRaises(StaleApproval):
                        main._consume_owner_decision(store, record.task_id,
                            OwnerApproval(owner_user_id="U1", note=note, workload_selection_decision_digest=digest))
                main._consume_owner_decision(store, record.task_id,
                    OwnerApproval(owner_user_id="U1", note="Reconsider available routes",
                                  workload_selection_decision_digest=decision.digest))
            self.assertIsNone(store.get(record.task_id).plan)
            asyncio.run(engine.run(record.task_id))
            held = store.get(record.task_id)
            self.assertEqual(held.message, WorkloadSelectionHold.code)
            self.assertEqual(len(store.list_recent_direct_production(limit=50)), 19)
            self.assertEqual(engine.director.plan.await_count, 2)
            # Mature TEST credits permit a separately bound clarification to
            # reach normal selected-plan approval; written input never waives caps.
            self._seed_mature_selector_history(path)
            with patch.object(main, "settings", settings):
                main._consume_owner_decision(store, record.task_id,
                    OwnerApproval(owner_user_id="U1", note="Reassess with the current ledger",
                                  workload_selection_decision_digest=current_owner_decision(held).digest))
            asyncio.run(engine.run(record.task_id))
            installed = store.get(record.task_id)
            self.assertEqual(installed.workload_selection_hold_reasons, [])
            self.assertEqual(current_owner_decision(installed).kind, "plan_approval", installed.error)
            self.assertEqual(installed.plan.assignments[0].agent, ProviderName.CURSOR)
            self.assertEqual(len(store.list_recent_direct_production(limit=50)), 38)
            self.assertEqual(engine.director.plan.await_count, 3)
            for provider in providers.values():
                self.assertEqual(provider.mock_calls, [])

    def test_runtime_selector_uses_explicit_cost_readiness_and_independence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "director.sqlite3"
            settings = replace(
                Settings(),
                sqlite_path=str(path),
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                trusted_provider_cost_ranks_json=(
                    '{"cursor": 1, "claude": 2, "grok": 3}'
                ),
                claude_routine_url="https://example.invalid/claude",
                claude_routine_token="token",
                cursor_api_key="cursor-secret",
                grok_enabled=True,
                grok_api_key="grok-secret",
            )
            store = TaskStore(path)
            engine = OrchestrationEngine(
                settings,
                store,
                providers={
                    ProviderName.CLAUDE: object(),
                    ProviderName.CURSOR: object(),
                    ProviderName.GROK: object(),
                },
            )
            plan = enforce_plan(
                self._selection_plan(),
                "Implement and independently review the bounded change",
                settings,
                activate_workload_policy=True,
            )
            original_digest = plan.prestage.digest
            record = TaskRecord(
                task_id="DIR-TRUSTED-SELECTION",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-trusted-selection",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Implement and independently review the bounded change",
                ),
            )

            before = plan.model_dump(mode="json")
            engine._apply_trusted_provider_selection(record, plan)
            self.assertEqual(plan.model_dump(mode="json"), before)
            self._seed_mature_selector_history(path)
            with patch.object(
                store,
                "list_recent_direct_production",
                wraps=store.list_recent_direct_production,
            ) as direct_history:
                selected = engine._apply_trusted_provider_selection(record, plan)

            self.assertEqual(
                [item.agent for item in selected.assignments],
                [ProviderName.CURSOR, ProviderName.CLAUDE],
            )
            self.assertEqual(selected.prestage.producer, ProviderName.CURSOR)
            self.assertEqual(
                selected.prestage.independent_reviewer, ProviderName.CLAUDE
            )
            self.assertNotEqual(selected.prestage.digest, original_digest)
            self.assertIsNone(selected.assignments[0].model_label)
            self.assertEqual(execution_policy_errors(selected, settings), [])
            direct_history.assert_called_once_with(limit=19)

    def test_runtime_selector_preserves_source_free_antigravity_integration_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "director.sqlite3"
            settings = replace(
                Settings(),
                sqlite_path=str(path),
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                trusted_provider_cost_ranks_json=(
                    '{"gemini": 1, "claude": 2, "cursor": 3}'
                ),
                gemini_api_key="gemini-secret",
                claude_routine_url="https://example.invalid/claude",
                claude_routine_token="token",
                cursor_api_key="cursor-secret",
            )
            store = TaskStore(path)
            engine = OrchestrationEngine(
                settings,
                store,
                providers={
                    ProviderName.GEMINI: object(),
                    ProviderName.CLAUDE: object(),
                    ProviderName.CURSOR: object(),
                },
            )
            source = "Project: demo\nScaffolding for a new bounded package"
            plan = preview_plan(source, settings)
            record = TaskRecord(
                task_id="DIR-TRUSTED-ANTIGRAVITY",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-trusted-antigravity",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text=source,
                ),
            )

            self._seed_mature_selector_history(path)
            selected = engine._apply_trusted_provider_selection(record, plan)

            self.assertEqual(
                [item.agent for item in selected.assignments],
                [ProviderName.GEMINI, ProviderName.CLAUDE, ProviderName.CURSOR],
            )
            self.assertIsNone(selected.assignments[0].repository)
            self.assertIsNotNone(selected.assignments[0].antigravity_directive)
            self.assertEqual(selected.assignments[1].kind, AssignmentKind.IMPLEMENTATION)
            self.assertEqual(selected.assignments[1].depends_on, [1])
            self.assertEqual(
                selected.assignments[2].kind, AssignmentKind.INDEPENDENT_REVIEW
            )
            self.assertEqual(selected.assignments[2].depends_on, [2])
            self.assertEqual(selected.prestage.producer, ProviderName.CLAUDE)
            self.assertEqual(
                selected.prestage.independent_reviewer, ProviderName.CURSOR
            )
            self.assertEqual(execution_policy_errors(selected, settings), [])

    def test_runtime_selector_uses_accepted_direct_production_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "director.sqlite3"
            settings = replace(
                Settings(),
                sqlite_path=str(path),
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                trusted_provider_cost_ranks_json=(
                    '{"cursor": 1, "claude": 2, "grok": 3}'
                ),
                claude_routine_url="https://example.invalid/claude",
                claude_routine_token="token",
                cursor_api_key="cursor-secret",
                grok_enabled=True,
                grok_api_key="grok-secret",
            )
            store = TaskStore(path)
            engine = OrchestrationEngine(
                settings,
                store,
                providers={
                    ProviderName.CLAUDE: object(),
                    ProviderName.CURSOR: object(),
                    ProviderName.GROK: object(),
                },
            )
            plan = enforce_plan(
                self._selection_plan(),
                "Implement and independently review the bounded change",
                settings,
                activate_workload_policy=True,
            )
            record = TaskRecord(
                task_id="DIR-TRUSTED-HISTORY",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-trusted-history",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Implement and independently review the bounded change",
                ),
            )
            cursor_history = [
                DirectProductionRecord(
                    assignment_id=f"accepted-cursor-{index}",
                    provider=DirectProductionProvider.CURSOR,
                    effort_units=5,
                )
                for index in range(19)
            ]

            with patch.object(
                store,
                "list_recent_direct_production",
                return_value=cursor_history,
            ):
                selected = engine._apply_trusted_provider_selection(record, plan)

            self.assertEqual(selected.assignments[0].agent, ProviderName.CLAUDE)
            self.assertEqual(selected.assignments[1].agent, ProviderName.CURSOR)

    def test_runtime_selector_excludes_task_unavailable_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "director.sqlite3"
            settings = replace(
                Settings(),
                sqlite_path=str(path),
                project_allowlist=("demo",),
                repository_allowlist=("example/demo",),
                trusted_provider_cost_ranks_json=(
                    '{"cursor": 1, "claude": 2, "grok": 3}'
                ),
                claude_routine_url="https://example.invalid/claude",
                claude_routine_token="token",
                cursor_api_key="cursor-secret",
                grok_enabled=True,
                grok_api_key="grok-secret",
            )
            store = TaskStore(path)
            engine = OrchestrationEngine(
                settings,
                store,
                providers={
                    ProviderName.CLAUDE: object(),
                    ProviderName.CURSOR: object(),
                    ProviderName.GROK: object(),
                },
            )
            plan = enforce_plan(
                self._selection_plan(),
                "Implement and independently review the bounded change",
                settings,
                activate_workload_policy=True,
            )
            record = TaskRecord(
                task_id="DIR-TRUSTED-UNAVAILABLE",
                status=TaskStatus.QUEUED,
                intake=SlackIntake(
                    source_event_id="evt-trusted-unavailable",
                    channel_id="C1",
                    thread_ts="1.1",
                    user_id="U1",
                    text="Implement and independently review the bounded change",
                ),
                unavailable_providers=[ProviderName.CURSOR],
            )

            self._seed_mature_selector_history(path)
            selected = engine._apply_trusted_provider_selection(record, plan)

            self.assertEqual(
                [item.agent for item in selected.assignments],
                [ProviderName.CLAUDE, ProviderName.GROK],
            )
            self.assertNotIn(
                ProviderName.CURSOR, [item.agent for item in selected.assignments]
            )

    def test_packet_workload_stop_uses_canonical_task_effect_phase(self) -> None:
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Perform bounded work",
            acceptance=["Evidence is recorded"],
        )
        record = TaskRecord(
            task_id="DIR-WORKLOAD-PACKET-STOP",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-workload-packet-stop",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="Perform bounded work",
            ),
        )

        effect_key = reserve_provider_effect(
            record,
            assignment,
            phase="dispatch",
            max_task_budget_usd=3.0,
        )
        OrchestrationEngine._record_workload_pre_submit_stop(
            record,
            assignment,
            effect_key=effect_key,
            phase="parallel_packet_dispatch",
        )

        self.assertEqual(record.task_effect_ledger[-1].phase, "dispatch")
        self.assertEqual(record.task_effect_ledger[-1].outcome, "failed_pre_submit")

    def test_packet_runtime_outcome_counts_unresolved_dispatch_conservatively(
        self,
    ) -> None:
        cases = (
            (
                RuntimeExecutionStatus.COMPLETED,
                WorkloadAttemptOutcome.INVOKED,
            ),
            (
                RuntimeExecutionStatus.REPLAYED,
                WorkloadAttemptOutcome.CACHED_REPLAY,
            ),
            (
                RuntimeExecutionStatus.REJECTED,
                WorkloadAttemptOutcome.FAILED_PRE_SUBMIT,
            ),
            (
                RuntimeExecutionStatus.OWNER_DECISION_REQUIRED,
                WorkloadAttemptOutcome.INVOKED,
            ),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                self.assertIs(
                    OrchestrationEngine._packet_workload_outcome(
                        status,
                        "dispatch state is unresolved",
                    ),
                    expected,
                )


class DirectorAuthorshipBoundaryTests(unittest.TestCase):
    """Director coordination never becomes material authorship of the work."""

    @staticmethod
    def _assignment(
        sequence: int,
        agent: ProviderName,
        depends_on: tuple[int, ...] = (),
    ) -> Assignment:
        return Assignment(
            sequence=sequence,
            agent=agent,
            task=f"Bounded assignment {sequence} within its recorded scope",
            acceptance=["Recorded evidence is complete"],
            depends_on=list(depends_on),
        )

    def test_director_and_owner_dependencies_are_not_material_authors(self) -> None:
        """Delegation and coordination create no authorship the Director inherits.

        The owner work order and the Director's routing are upstream of every
        candidate. Counting them as material authors would attribute provider
        code to the control plane and shrink the independent reviewer pool for
        no evidentiary reason.
        """

        owner = self._assignment(1, ProviderName.OWNER)
        director = self._assignment(2, ProviderName.OPENAI, depends_on=(1,))
        producer = self._assignment(3, ProviderName.CLAUDE, depends_on=(2,))
        reviewer = self._assignment(4, ProviderName.CURSOR, depends_on=(3,))
        by_sequence = {
            item.sequence: item for item in (owner, director, producer, reviewer)
        }

        authors = OrchestrationEngine._trusted_material_authors(reviewer, by_sequence)

        self.assertEqual(authors, frozenset({Provider.CLAUDE}))
        self.assertNotIn(Provider.OPENAI, authors)

    def test_every_producing_provider_in_the_chain_stays_a_material_author(self) -> None:
        """Excluding the control plane must not drop a real upstream producer."""

        scaffold = self._assignment(1, ProviderName.GEMINI)
        integration = self._assignment(2, ProviderName.CLAUDE, depends_on=(1,))
        reviewer = self._assignment(3, ProviderName.GROK, depends_on=(2,))
        by_sequence = {
            item.sequence: item for item in (scaffold, integration, reviewer)
        }

        authors = OrchestrationEngine._trusted_material_authors(reviewer, by_sequence)

        self.assertEqual(authors, frozenset({Provider.GEMINI, Provider.CLAUDE}))


# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 05:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:59 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 07:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:16 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:51 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-04 | 11:56 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:36 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:54 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 01:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
