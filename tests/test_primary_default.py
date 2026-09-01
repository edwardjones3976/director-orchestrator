# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from dataclasses import replace
from pathlib import Path

from director_orchestrator.config import Settings
from director_orchestrator.langgraph_postgres import open_postgres_control_plane
from director_orchestrator.langgraph_primary import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalResume,
    HandlerContext,
)
from director_orchestrator.models import (
    IntakeAuthentication,
    ProviderName,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.packet_routing import DirectorPacketTaskRouter
from director_orchestrator.policy import preview_plan
from director_orchestrator.primary_coordinator import PrimaryTaskCoordinator
from director_orchestrator.primary_default import (
    DefaultDryRunServices,
    DefaultOfflineSimulationServices,
    DefaultPrimaryActivation,
    DefaultPrimaryApplication,
    DefaultPrimaryAuthority,
    _primary_provider_lane,
    build_automatic_primary_live_dependencies,
    resolve_default_primary_authority,
)


TEST_DSN = os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN", "").strip()
SHA = "b" * 40


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class _FixedAuthorityResolver:
    def resolve(self, record, assignment):
        return [{"path": "README.md", "commit_sha": SHA, "blob_sha": "c" * 40}]


def packet_router(settings: Settings) -> DirectorPacketTaskRouter:
    return DirectorPacketTaskRouter(
        authority_snapshots=_FixedAuthorityResolver(),
        boundary_adapters={},
        writer_owner_id="langgraph-primary-default",
        writer_secret="default-primary-test-writer-secret",
        timeout_seconds=settings.provider_timeout_seconds,
    )


class DefaultPrimaryLiveStartupBoundaryTests(unittest.TestCase):
    def test_antigravity_lane_is_an_explicit_gemini_backend_mapping(self):
        self.assertEqual("antigravity", _primary_provider_lane(ProviderName.GEMINI))
        self.assertEqual("claude", _primary_provider_lane(ProviderName.CLAUDE))

    def test_request_budget_units_are_frozen_from_validated_plan(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
        )
        application = DefaultPrimaryApplication(
            settings,
            coordinator=None,  # type: ignore[arg-type]
            services=None,  # type: ignore[arg-type]
            authority=DefaultPrimaryAuthority(SHA, digest("authority")),
        )
        intake = SlackIntake(
            source_event_id="budget-event",
            channel_id="C123",
            user_id="U123",
            text="Implement the bounded change",
            thread_ts="1.0",
        )
        record = TaskRecord(
            task_id="DIR-LG-BUDGET",
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        record.plan = preview_plan(intake.text, settings)

        request = application._request(record)

        expected_provider_units = sum(
            assignment.effort_points for assignment in record.plan.assignments
        )
        self.assertGreater(expected_provider_units, 0)
        self.assertEqual(expected_provider_units, request.provider_cost_units)
        self.assertEqual(0, request.effect_cost_units)
        self.assertEqual(expected_provider_units, request.budget_limit_units)

    def test_request_derives_external_effect_identity_from_validated_plan(self):
        settings = replace(
            Settings(),
            repository_allowlist=("owner/repository",),
        )
        application = DefaultPrimaryApplication(
            settings,
            coordinator=None,  # type: ignore[arg-type]
            services=None,  # type: ignore[arg-type]
            authority=DefaultPrimaryAuthority(SHA, digest("authority")),
        )
        intake = SlackIntake(
            source_event_id="effect-event",
            channel_id="C123",
            user_id="U123",
            text="Implement the bounded change and prepare a draft PR",
            thread_ts="1.1",
        )
        record = TaskRecord(
            task_id="DIR-LG-EFFECT",
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        plan = preview_plan(intake.text, settings)
        assignments = list(plan.assignments)
        assignments[0] = assignments[0].model_copy(update={"allow_draft_pr": True})
        record.plan = plan.model_copy(update={"assignments": assignments})

        first = application._request(record)
        second = application._request(record)

        self.assertTrue(first.external_effect_required)
        self.assertEqual(1, first.effect_cost_units)
        self.assertEqual(
            first.provider_cost_units + first.effect_cost_units,
            first.budget_limit_units,
        )
        self.assertIsNotNone(first.effect_key_digest)
        self.assertEqual(first.effect_key_digest, second.effect_key_digest)
        self.assertNotEqual(first.correlation_id, second.correlation_id)

    def test_paid_execution_requires_the_explicit_live_switch(self):
        settings = replace(
            Settings(),
            langgraph_primary_enabled=True,
            allow_paid_runs=True,
            langgraph_primary_live_provider_dispatch_enabled=False,
        )

        with self.assertRaisesRegex(
            ValueError,
            "ALLOW_PAID_RUNS is enabled.*LIVE_PROVIDER_DISPATCH_ENABLED is disabled",
        ):
            DefaultPrimaryActivation(settings)

    def test_automatic_live_discovery_fails_closed_for_missing_lanes(self):
        settings = replace(
            Settings(),
            repository_snapshot_root=str(Path.cwd()),
        )

        with self.assertRaisesRegex(
            ValueError,
            "automatic graph-native live dependencies are unavailable: missing=",
        ):
            build_automatic_primary_live_dependencies(
                settings,
                handle=None,  # type: ignore[arg-type]
                services=None,  # type: ignore[arg-type]
            )

    def test_automatic_live_discovery_reports_typed_microsoft_365_copilot_blocker(self):
        settings = replace(
            Settings(),
            repository_snapshot_root=str(Path.cwd()),
        )

        with patch(
            "director_orchestrator.primary_transports._which", return_value=None
        ), self.assertRaisesRegex(
            ValueError,
            r"external_blockers=microsoft-365-copilot:"
            r"microsoft_365_copilot_work_iq_capability_evidence_missing",
        ):
            build_automatic_primary_live_dependencies(
                settings,
                handle=None,  # type: ignore[arg-type]
                services=None,  # type: ignore[arg-type]
            )


class DefaultPrimaryPinnedAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "pinned-authority"
        self.root.mkdir()

        self.git("init", "-b", "main")
        self.git("config", "user.email", "pinned-authority@example.test")
        self.git("config", "user.name", "Pinned Authority Test")
        (self.root / "README.md").write_text("pinned authority\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "pinned authority")
        self.git("remote", "add", "origin", "https://github.com/owner/pinned.git")
        self.commit_sha = self.git("rev-parse", "HEAD")
        self.tree_sha = self.git("rev-parse", "HEAD^{tree}")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=self.root,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr)
        return completed.stdout.strip()

    def settings(self, **updates) -> Settings:
        values = {
            "repository_snapshot_root": str(self.root),
            "repository_allowlist": ("owner/pinned",),
            "allowed_starting_refs": ("main",),
            "langgraph_primary_pinned_commit_sha": self.commit_sha,
            "langgraph_primary_pinned_tree_sha": self.tree_sha,
        }
        values.update(updates)
        return replace(Settings(), **values)

    def test_exact_pins_do_not_require_a_remote_tracking_ref(self) -> None:
        authority = resolve_default_primary_authority(self.settings())

        self.assertEqual(self.commit_sha, authority.starting_sha)

        with self.assertRaisesRegex(RuntimeError, "could not be verified"):
            resolve_default_primary_authority(
                self.settings(
                    langgraph_primary_pinned_commit_sha="",
                    langgraph_primary_pinned_tree_sha="",
                )
            )

    def test_real_default_planning_binds_exact_pins_without_tracking_ref(self) -> None:
        settings = self.settings()
        services = DefaultDryRunServices(settings)
        record = TaskRecord(
            task_id="DIR-PINNED-PLAN",
            status=TaskStatus.QUEUED,
            intake=SlackIntake(
                source_event_id="event-pinned-plan",
                channel_id="CPINNED",
                thread_ts="1720000000.000002",
                user_id="UOWNER",
                text="Project: default\nImplement the bounded pinned change.",
            ),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        services.register(record)
        context = HandlerContext(
            task_id=record.task_id,
            source_event_id=record.intake.source_event_id,
            correlation_id="corr-pinned-plan",
            intake_digest=digest("intake"),
            scope_digest=digest("scope"),
            authority_snapshot_digest=digest("authority"),
            work_packet_digest=None,
            idempotency_key_digest=digest("idempotency"),
            starting_sha=self.commit_sha,
            candidate_sha=self.commit_sha,
            plan_digest=None,
            scheduled_producer_id=None,
            provider_attempt_digest=None,
            provider_dispatch_status="NOT_STARTED",
            provider_result_digest=None,
            producer_id=None,
            reviewer_id=None,
            revision_count=0,
            budget_limit_units=10,
            budget_used_units=0,
            effect_key_digest=None,
            effect_attempt_digest=None,
            writer_owner_id="langgraph",
        )

        result = services.plan(context)
        planned = services._record(record.task_id).plan

        self.assertTrue(result.work_packet_digest.startswith("sha256:"))
        assert planned is not None and planned.prestage is not None
        self.assertEqual(self.commit_sha, planned.prestage.base_sha)
        self.assertEqual(self.commit_sha, planned.prestage.branch)
        self.assertEqual(str(self.root.resolve()), planned.prestage.worktree)
        self.assertTrue(
            all(item.starting_ref == self.commit_sha for item in planned.assignments)
        )

    def test_pins_preserve_origin_clean_head_ref_and_tree_checks(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "remote does not match"):
            resolve_default_primary_authority(
                self.settings(repository_allowlist=("other/pinned",))
            )

        (self.root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "must be clean"):
            resolve_default_primary_authority(self.settings())
        (self.root / "untracked.txt").unlink()

        with self.assertRaisesRegex(RuntimeError, "HEAD and starting ref"):
            resolve_default_primary_authority(
                self.settings(langgraph_primary_pinned_commit_sha="f" * 40)
            )

        with self.assertRaisesRegex(RuntimeError, "computed tree"):
            resolve_default_primary_authority(
                self.settings(langgraph_primary_pinned_tree_sha="e" * 40)
            )


class DefaultPrimaryApplicationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests"
            )

    def setUp(self) -> None:
        self.schema = f"director_default_it_{uuid.uuid4().hex[:8]}_v1"
        self.handle = open_postgres_control_plane(
            TEST_DSN,
            schema=self.schema,
            allow_insecure_local_transport=True,
        )
        self.settings = replace(
            Settings(),
            app_env="default-test",
            allow_paid_runs=False,
            project_allowlist=("default",),
            repository_allowlist=("owner/director-orchestrator",),
            allowed_starting_refs=("main",),
        )
        self.services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        self.coordinator = PrimaryTaskCoordinator(
            self.handle,
            self.services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=(
                self.services.application_projection
            ),
        )
        self.coordinator.activate_new_intake(expected_generation=0)
        self.application = DefaultPrimaryApplication(
            self.settings,
            self.coordinator,
            self.services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )

    def tearDown(self) -> None:
        try:
            with self.handle.pool.connection() as connection:
                connection.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            self.handle.close()

    @staticmethod
    def intake() -> SlackIntake:
        return SlackIntake(
            source_event_id="event-default-1",
            channel_id="CDEFAULT",
            thread_ts="1720000000.000001",
            user_id="UOWNER",
            text="Project: default\nCreate a bounded implementation plan.",
        )

    def test_paid_off_intake_and_read_are_graph_owned(self):
        record = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )

        self.assertTrue(record.task_id.startswith("DIR-LG-"))
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, record.status)
        self.assertIsNotNone(record.plan)
        self.assertIn("no provider", record.message or "")
        assert record.plan is not None
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        expected_packet = self.services._packet_router.work_packet(record, assignment)
        graph_state = self.coordinator._runtime.compact_state(record.task_id)
        self.assertEqual(
            document_digest(expected_packet), graph_state["work_packet_digest"]
        )
        self.assertNotEqual(
            self.application._request(record).work_packet_digest,
            graph_state["work_packet_digest"],
        )
        restored = self.application.get_task(record.task_id)
        self.assertEqual(record.model_dump(mode="json"), restored.model_dump(mode="json"))
        readiness = self.coordinator.readiness()
        self.assertEqual(1, readiness.graph_task_count)
        self.assertTrue(readiness.writer_lease_ready)
        self.assertTrue(readiness.legacy_new_intake_disabled)

    def test_repository_free_research_is_approval_bound_and_restart_stable(self):
        intake = self.intake().model_copy(
            update={
                "source_event_id": "event-default-research",
                "text": "Project: default\nResearch current public sources.",
            }
        )

        record = self.application.create_intake(
            intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        duplicate = self.application.create_intake(
            intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        recovered = self.application.recover(record.task_id)
        projection = self.coordinator.get_projection(record.task_id)

        for result in (record, duplicate, recovered):
            self.assertEqual(record.task_id, result.task_id)
            self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, result.status)
        assert record.plan is not None
        research = min(record.plan.assignments, key=lambda item: item.sequence)
        review = max(record.plan.assignments, key=lambda item: item.sequence)
        self.assertEqual("perplexity", research.agent.value)
        self.assertIsNone(research.repository)
        self.assertTrue(research.requires_approval)
        self.assertEqual([research.sequence], review.depends_on)
        assert projection is not None
        self.assertIsNone(projection.projection["terminal_classification"])
        self.assertEqual("execution_approval_required", projection.projection["reason_code"])
        self.assertEqual("NOT_STARTED", projection.projection["provider_dispatch_status"])
        self.assertEqual("NOT_REQUIRED", projection.projection["effect_status"])
        state = self.coordinator._runtime.compact_state(record.task_id)
        self.assertIsNotNone(state["work_packet_digest"])
        self.assertIsNotNone(state["plan_work_packet_binding_digest"])
        pending = projection.projection["pending_approval"]
        self.assertEqual("execution", pending["kind"])

        restarted_services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        restarted_coordinator = PrimaryTaskCoordinator(
            self.handle,
            restarted_services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=restarted_services.application_projection,
        )
        restarted_coordinator.adopt_or_activate_new_intake()
        restarted_application = DefaultPrimaryApplication(
            self.settings,
            restarted_coordinator,
            restarted_services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )

        restarted = restarted_application.recover(record.task_id)
        restarted_projection = restarted_coordinator.get_projection(record.task_id)
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, restarted.status)
        assert restarted_projection is not None
        self.assertEqual(
            "execution_approval_required",
            restarted_projection.projection["reason_code"],
        )

    def test_repository_free_research_approval_reaches_dispatch_boundary_without_call(self):
        settings = replace(
            self.settings,
            langgraph_primary_offline_simulation_enabled=True,
        )
        calls = {provider: 0 for provider in settings.langgraph_primary_required_providers}

        def provider_double(provider: str):
            def run(_context):
                calls[provider] += 1
                return digest(f"unexpected:{provider}")

            return run

        services = DefaultOfflineSimulationServices(
            settings,
            packet_router=packet_router(settings),
            provider_doubles={
                provider: provider_double(provider)
                for provider in settings.langgraph_primary_required_providers
            },
        )
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-research-approval-v1",
            auto_execute_provider=False,
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )
        intake = self.intake().model_copy(
            update={
                "source_event_id": "event-default-research-approve",
                "text": "Project: default\nResearch current public sources.",
            }
        )
        pending = application.create_intake(
            intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        projection = coordinator.get_projection(pending.task_id)
        assert projection is not None
        approval = projection.projection["pending_approval"]

        running = application.resume_approval(
            ApprovalResume(
                task_id=pending.task_id,
                kind=ApprovalKind(approval["kind"]),
                request_digest=approval["request_digest"],
                approval_event_id="approve-default-research-boundary",
                decision=ApprovalDecision.APPROVE,
            )
        )
        approved_projection = coordinator.get_projection(pending.task_id)
        assert approved_projection is not None
        approved_state = coordinator._runtime.compact_state(pending.task_id)

        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, running.status)
        self.assertEqual("perplexity", approved_state["scheduled_producer_id"])
        self.assertEqual("provider_subgraph", approved_projection.projection["phase"])
        self.assertEqual("CLAIMED", approved_projection.projection["provider_dispatch_status"])
        self.assertTrue(all(value == 0 for value in calls.values()))

    def test_duplicate_event_returns_original_graph_task(self):
        first = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        replay = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )

        self.assertEqual(first.task_id, replay.task_id)
        self.assertEqual(1, self.coordinator.readiness().graph_task_count)

    def test_denial_and_cancellation_are_graph_owned_and_recoverable(self):
        denied = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        denied_projection = self.coordinator.get_projection(denied.task_id)
        assert denied_projection is not None
        pending = denied_projection.projection["pending_approval"]
        denied = self.application.resume_approval(
            ApprovalResume(
                task_id=denied.task_id,
                kind=ApprovalKind(pending["kind"]),
                request_digest=pending["request_digest"],
                approval_event_id="deny-default-1",
                decision=ApprovalDecision.DENY,
            )
        )
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, denied.status)
        self.assertEqual(
            "execution_approval_denied",
            self.coordinator.get_projection(denied.task_id).projection["reason_code"],
        )
        recovered = self.application.recover(denied.task_id)
        self.assertEqual(TaskStatus.OWNER_DECISION_REQUIRED, recovered.status)

        second = self.intake().model_copy(
            update={"source_event_id": "event-default-cancel"}
        )
        cancelled = self.application.create_intake(
            second,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        cancelled = self.application.cancel(cancelled.task_id)
        self.assertEqual(TaskStatus.FAILED, cancelled.status)
        cancelled_projection = self.coordinator.get_projection(cancelled.task_id)
        assert cancelled_projection is not None
        self.assertEqual(
            "CANCELLED", cancelled_projection.projection["terminal_classification"]
        )

    def test_restart_read_does_not_need_in_memory_application_state(self):
        first = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        restarted_services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        restarted = DefaultPrimaryApplication(
            self.settings,
            PrimaryTaskCoordinator(
                self.handle,
                restarted_services.handlers(),
                owner_token="default-primary-test-owner",
                checkpoint_namespace="default-primary-test-v1",
                application_projection_provider=(
                    restarted_services.application_projection
                ),
            ),
            restarted_services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )

        restored = restarted.get_task(first.task_id)
        self.assertIsNotNone(restored)
        self.assertEqual(first.task_id, restored.task_id)

    def test_same_owner_restart_adopts_intake_and_reconciles_from_projection(self):
        first = self.application.create_intake(
            self.intake(),
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        before = self.coordinator.get_projection(first.task_id)
        self.assertIsNotNone(before)

        restarted_services = DefaultDryRunServices(
            self.settings, packet_router=packet_router(self.settings)
        )
        restarted_coordinator = PrimaryTaskCoordinator(
            self.handle,
            restarted_services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-test-v1",
            application_projection_provider=(
                restarted_services.application_projection
            ),
        )
        adopted = restarted_coordinator.adopt_or_activate_new_intake()
        self.assertEqual(1, adopted.fencing_generation)

        assert before is not None
        restarted_services.hydrate(before)
        try:
            recovered = restarted_coordinator.recover(first.task_id)
        finally:
            restarted_services.discard(first.task_id)

        self.assertEqual(first.task_id, recovered.task_id)
        self.assertEqual(before.projection_digest, recovered.projection_digest)
        self.assertEqual(1, restarted_coordinator.readiness().graph_task_count)

    def test_offline_simulation_traverses_full_graph_once(self):
        settings = replace(
            self.settings,
            langgraph_primary_offline_simulation_enabled=True,
        )
        barrier = threading.Barrier(len(settings.langgraph_primary_required_providers))
        worker_threads: set[int] = set()
        worker_lock = threading.Lock()

        def provider_double(provider: str):
            def run(context):
                with worker_lock:
                    worker_threads.add(threading.get_ident())
                barrier.wait(timeout=5)
                return digest(f"offline:{provider}:{context.task_id}")

            return run

        services = DefaultOfflineSimulationServices(
            settings,
            packet_router=packet_router(settings),
            provider_doubles={
                provider: provider_double(provider)
                for provider in settings.langgraph_primary_required_providers
            },
        )
        coordinator = PrimaryTaskCoordinator(
            self.handle,
            services.handlers(),
            owner_token="default-primary-test-owner",
            checkpoint_namespace="default-primary-offline-test-v1",
            auto_execute_provider=True,
            application_projection_provider=services.application_projection,
        )
        coordinator.adopt_or_activate_new_intake()
        application = DefaultPrimaryApplication(
            settings,
            coordinator,
            services,
            DefaultPrimaryAuthority(
                starting_sha=SHA,
                authority_snapshot_digest=digest("authority"),
            ),
        )
        intake = self.intake().model_copy(
            update={"source_event_id": "event-default-offline-full"}
        )
        pending = application.create_intake(
            intake,
            intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
        )
        projection = coordinator.get_projection(pending.task_id)
        assert projection is not None
        approval = projection.projection["pending_approval"]

        completed = application.resume_approval(
            ApprovalResume(
                task_id=pending.task_id,
                kind=ApprovalKind(approval["kind"]),
                request_digest=approval["request_digest"],
                approval_event_id="approve-default-offline-full",
                decision=ApprovalDecision.APPROVE,
            )
        )

        self.assertEqual(
            TaskStatus.DONE,
            completed.status,
            coordinator.get_projection(pending.task_id).projection,
        )
        self.assertEqual(
            set(settings.langgraph_primary_required_providers),
            set(services.provider_call_counts),
        )
        self.assertTrue(
            all(value == 1 for value in services.provider_call_counts.values())
        )
        self.assertEqual(
            len(settings.langgraph_primary_required_providers), len(worker_threads)
        )
        compact = coordinator._runtime.compact_state(pending.task_id)
        self.assertEqual("SUCCEEDED", compact["terminal_classification"])
        self.assertIsNotNone(compact["evidence_digest"])
        self.assertIsNotNone(compact["acceptance_digest"])
        self.assertNotEqual(compact["producer_id"], compact["reviewer_id"])
        self.assertIsNotNone(compact["ci_digest"])
        self.assertIsNotNone(compact["security_digest"])

        replay = application.recover(pending.task_id)
        self.assertEqual(TaskStatus.DONE, replay.status)
        self.assertTrue(
            all(value == 1 for value in services.provider_call_counts.values())
        )


class DefaultPrimaryFastAPIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TEST_DSN:
            raise RuntimeError(
                "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required for integration tests"
            )

    def test_real_startup_routes_intake_and_reads_through_langgraph(self):
        schema = f"director_default_api_{uuid.uuid4().hex[:8]}_v1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "authority"
            root.mkdir()

            def git(*arguments: str) -> None:
                completed = subprocess.run(
                    ("git", *arguments),
                    cwd=root,
                    shell=False,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    self.fail(completed.stderr)

            git("init", "-b", "main")
            git("config", "user.email", "default-primary@example.test")
            git("config", "user.name", "Default Primary Test")
            (root / "README.md").write_text("default primary\n", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-m", "authority")
            git("remote", "add", "origin", "https://github.com/owner/director.git")
            git("update-ref", "refs/remotes/origin/main", "HEAD")

            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONPATH": "src",
                    "APP_ENV": "default-primary-api-test",
                    "SQLITE_PATH": str(Path(directory) / "legacy.sqlite3"),
                    "ORCHESTRATOR_SHARED_SECRET": "test-shared-secret",
                    "SLACK_OWNER_USER_ID": "UOWNER",
                    "ALLOW_PAID_RUNS": "false",
                    "LANGGRAPH_SHADOW_ENABLED": "false",
                    "LANGGRAPH_APPROVAL_RESUME_ENABLED": "false",
                    "LANGGRAPH_PRIMARY_ENABLED": "true",
                    "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED": "true",
                    "LANGGRAPH_PRIMARY_POSTGRES_DSN": TEST_DSN,
                    "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": schema,
                    "LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE": (
                        "default-primary-api-test-v1"
                    ),
                    "LANGGRAPH_PRIMARY_OWNER_TOKEN": "p" * 40,
                    "LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES": "true",
                    "REPOSITORY_SNAPSHOT_ROOT": str(root),
                    "REPOSITORY_ALLOWLIST": "owner/director",
                    "ALLOWED_STARTING_REFS": "main",
                    "PROJECT_ALLOWLIST": "default",
                }
            )
            program = r'''
import json
from fastapi.testclient import TestClient
from director_orchestrator.main import app

payload = {
    "source_event_id": "default-api-event-1",
    "channel_id": "CDEFAULT",
    "thread_ts": "1720000000.000001",
    "user_id": "UOWNER",
    "text": "Project: default\nCreate a bounded implementation plan.",
    "received_at": "2026-08-31T23:30:00Z"
}
headers = {"Authorization": "Bearer test-shared-secret"}
with TestClient(app) as client:
    health = client.get("/health").json()["langgraph_primary"]
    first = client.post("/v1/intake", headers=headers, json=payload)
    replay = client.post("/v1/intake", headers=headers, json=payload)
    task_id = first.json()["task_id"]
    restored = client.get(f"/v1/tasks/{task_id}", headers=headers)
    command_binding = {
        "task_id": task_id,
        "actor_id": "UOWNER",
        "expected_projection_digest": restored.headers["X-Primary-Projection-Digest"],
        "expected_fencing_generation": int(
            restored.headers["X-Primary-Writer-Generation"]
        )
    }
    cancelled = client.post(
        f"/v1/primary/tasks/{task_id}/cancel",
        headers=headers,
        json={**command_binding, "command_id": "cancel-default-api-1"}
    )
    recovered = client.post(
        f"/v1/primary/tasks/{task_id}/recover",
        headers=headers,
        json={
            **command_binding,
            "command_id": "recover-default-api-1",
            "expected_projection_digest": cancelled.json()["projection_digest"]
        }
    )
    print(json.dumps({
        "health": health,
        "first_status": first.status_code,
        "replay_status": replay.status_code,
        "same_task": task_id == replay.json()["task_id"],
        "read_status": restored.status_code,
        "read_owner": restored.headers.get("X-Task-Engine-Owner"),
        "task_status": restored.json()["status"],
        "cancel_status": cancelled.status_code,
        "cancel_classification": cancelled.json()["terminal_classification"],
        "recover_status": recovered.status_code,
        "recover_classification": recovered.json()["terminal_classification"]
    }, sort_keys=True))
'''
            completed = subprocess.run(
                (sys.executable, "-c", program),
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
            )
            try:
                self.assertEqual(0, completed.returncode, completed.stderr)
                result = json.loads(completed.stdout.strip().splitlines()[-1])
                self.assertEqual(202, result["first_status"])
                self.assertEqual(202, result["replay_status"])
                self.assertTrue(result["same_task"])
                self.assertEqual(200, result["read_status"])
                self.assertEqual("langgraph", result["read_owner"])
                self.assertEqual("OWNER_DECISION_REQUIRED", result["task_status"])
                self.assertEqual(200, result["cancel_status"])
                self.assertEqual("CANCELLED", result["cancel_classification"])
                self.assertEqual(200, result["recover_status"])
                self.assertEqual("CANCELLED", result["recover_classification"])
                self.assertTrue(result["health"]["primary_router_wired"])
                self.assertTrue(result["health"]["writer_lease_ready"])
                self.assertEqual("langgraph", result["health"]["new_intake_owner"])
                self.assertTrue(
                    result["health"]["legacy_new_intake_disabled"]
                )
            finally:
                cleanup = open_postgres_control_plane(
                    TEST_DSN,
                    schema=schema,
                    allow_insecure_local_transport=True,
                )
                try:
                    with cleanup.pool.connection() as connection:
                        connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                finally:
                    cleanup.close()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:57 UTC | BEGIN]]
# Request budgets are immutable, non-zero units derived from the validated plan.
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:57 UTC | END]]
if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
